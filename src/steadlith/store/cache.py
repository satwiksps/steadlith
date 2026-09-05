"""A small, durable content-addressed embedding cache backed by SQLite."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import struct
import tempfile
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from steadlith._sqlite_values import decode_vector, find_blocking_parent
from steadlith._sqlite_values import encode_vector as _encode_vector
from steadlith._sqlite_values import utc_now as _now
from steadlith.errors import BackendError

SCHEMA_VERSION = 1
MAX_IMPORT_BYTES = 512 * 1024 * 1024
MAX_IMPORT_LINE_BYTES = 4 * 1024 * 1024
MAX_VECTOR_DIMENSIONS = 65_536


def _decode_vector(payload: bytes, dimensions: int) -> tuple[float, ...]:
    expected = dimensions * 4
    return decode_vector(
        payload,
        dimensions,
        corrupt_message=f"Corrupt cached vector: expected {expected} bytes, found {len(payload)}",
    )


@dataclass(frozen=True)
class CacheStats:
    entries: int
    chunks: int
    models: int
    bytes: int

    def as_dict(self) -> dict[str, int]:
        return {
            "entries": self.entries,
            "chunks": self.chunks,
            "models": self.models,
            "bytes": self.bytes,
        }


class Cache:
    """Cache embeddings by ``(chunk_hash, model_id, params_hash)``.

    The embedding model deliberately belongs here rather than in the chunk hash.
    That separation is what lets one chunking pass serve multiple model migrations.
    """

    def __init__(self, path: str | Path, *, readonly: bool = False) -> None:
        requested_path = Path(path).expanduser()
        try:
            self.path = requested_path.resolve()
            blocking_path = find_blocking_parent(self.path)
            if blocking_path is not None:
                raise BackendError(
                    f"Could not prepare embedding cache path {requested_path}: "
                    f"{blocking_path} blocks the path. Rename the blocking file or configure "
                    "store.cache to use another path."
                )
            if not readonly:
                self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            blocking_path = exc.filename or requested_path
            raise BackendError(
                f"Could not prepare embedding cache path {requested_path}: "
                f"{blocking_path} blocks the path. Rename the blocking file or configure "
                "store.cache to use another path."
            ) from exc
        self.readonly = readonly
        self._lock = threading.RLock()
        if readonly:
            self._connection = self._open_readonly()
        else:
            try:
                self._connection = sqlite3.connect(
                    str(self.path), timeout=30, check_same_thread=False
                )
                self._connection.row_factory = sqlite3.Row
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA synchronous = NORMAL")
                self._initialize()
            except BackendError:
                connection = getattr(self, "_connection", None)
                if connection is not None:
                    connection.close()
                    self._connection = None
                raise
            except sqlite3.Error as exc:
                connection = getattr(self, "_connection", None)
                if connection is not None:
                    connection.close()
                    self._connection = None
                raise BackendError(f"Could not open embedding cache {self.path}: {exc}") from exc

    def _open_readonly(self) -> sqlite3.Connection | None:
        if not self.path.exists():
            return None
        connection: sqlite3.Connection | None = None
        try:
            uri = f"{self.path.as_uri()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT value FROM cache_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                raise BackendError("Embedding cache schema version is absent")
            try:
                schema_version = int(row["value"])
            except (TypeError, ValueError) as exc:
                raise BackendError(f"Invalid cache schema version: {row['value']!r}") from exc
            if schema_version != SCHEMA_VERSION:
                raise BackendError(
                    f"Unsupported cache schema {row['value']}; expected {SCHEMA_VERSION}"
                )
            return connection
        except BackendError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise BackendError(f"Could not read embedding cache {self.path}: {exc}") from exc

    def _initialize(self) -> None:
        connection = self._require_connection()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS cache_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS embeddings (
                chunk_hash TEXT NOT NULL,
                model_id TEXT NOT NULL,
                params_hash TEXT NOT NULL,
                dimensions INTEGER NOT NULL CHECK (dimensions >= 0),
                vector BLOB NOT NULL,
                token_count INTEGER NOT NULL CHECK (token_count >= 0),
                created_at TEXT NOT NULL,
                accessed_at TEXT NOT NULL,
                PRIMARY KEY (chunk_hash, model_id, params_hash)
            );
            CREATE INDEX IF NOT EXISTS embeddings_accessed_at
                ON embeddings(accessed_at);
            """
        )
        row = connection.execute(
            "SELECT value FROM cache_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO cache_meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        else:
            try:
                schema_version = int(row["value"])
            except (TypeError, ValueError) as exc:
                raise BackendError(f"Invalid cache schema version: {row['value']!r}") from exc
            if schema_version != SCHEMA_VERSION:
                raise BackendError(
                    f"Unsupported cache schema {row['value']}; expected {SCHEMA_VERSION}"
                )
        connection.commit()

    @property
    def available(self) -> bool:
        return self._connection is not None

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> Cache:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise BackendError(f"Cache does not exist: {self.path}")
        return self._connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        if self.readonly:
            raise BackendError("Cannot write to a read-only cache")
        connection = self._require_connection()
        with self._lock:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise BackendError(f"Embedding cache transaction failed: {exc}") from exc
            except BaseException:
                connection.rollback()
                raise

    def contains(self, chunk_hash: str, model_id: str, params_hash: str) -> bool:
        connection = self._connection
        if connection is None:
            return False
        try:
            with self._lock:
                row = connection.execute(
                    """
                    SELECT 1 FROM embeddings
                    WHERE chunk_hash = ? AND model_id = ? AND params_hash = ?
                    """,
                    (chunk_hash, model_id, params_hash),
                ).fetchone()
        except sqlite3.Error as exc:
            raise BackendError(f"Could not query embedding cache: {exc}") from exc
        return row is not None

    def get(self, chunk_hash: str, model_id: str, params_hash: str) -> tuple[float, ...] | None:
        connection = self._connection
        if connection is None:
            return None
        try:
            with self._lock:
                row = connection.execute(
                    """
                    SELECT dimensions, vector FROM embeddings
                    WHERE chunk_hash = ? AND model_id = ? AND params_hash = ?
                    """,
                    (chunk_hash, model_id, params_hash),
                ).fetchone()
                if row is None:
                    return None
                if not self.readonly:
                    connection.execute(
                        """
                        UPDATE embeddings SET accessed_at = ?
                        WHERE chunk_hash = ? AND model_id = ? AND params_hash = ?
                        """,
                        (_now(), chunk_hash, model_id, params_hash),
                    )
                    connection.commit()
        except sqlite3.Error as exc:
            raise BackendError(f"Could not read embedding cache: {exc}") from exc
        return _decode_vector(row["vector"], row["dimensions"])

    def get_many(
        self, keys: Iterable[tuple[str, str, str]]
    ) -> dict[tuple[str, str, str], tuple[float, ...]]:
        unique_keys = tuple(dict.fromkeys(keys))
        connection = self._connection
        if connection is None or not unique_keys:
            return {}
        found: dict[tuple[str, str, str], tuple[float, ...]] = {}
        try:
            with self._lock:
                # Three bind parameters per key. Batches of 250 stay below the
                # conservative 999-parameter limit of older SQLite builds.
                for start in range(0, len(unique_keys), 250):
                    batch = unique_keys[start : start + 250]
                    predicates = " OR ".join(
                        "(chunk_hash = ? AND model_id = ? AND params_hash = ?)" for _ in batch
                    )
                    parameters = tuple(value for key in batch for value in key)
                    rows = connection.execute(
                        f"""
                        SELECT chunk_hash, model_id, params_hash, dimensions, vector
                        FROM embeddings WHERE {predicates}
                        """,  # noqa: S608 - placeholders supply every untrusted value
                        parameters,
                    )
                    for row in rows:
                        key = (
                            str(row["chunk_hash"]),
                            str(row["model_id"]),
                            str(row["params_hash"]),
                        )
                        found[key] = _decode_vector(row["vector"], row["dimensions"])
                if found and not self.readonly:
                    timestamp = _now()
                    connection.executemany(
                        """
                        UPDATE embeddings SET accessed_at = ?
                        WHERE chunk_hash = ? AND model_id = ? AND params_hash = ?
                        """,
                        ((timestamp, *key) for key in found),
                    )
                    connection.commit()
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.rollback()
            raise BackendError(f"Could not read embedding cache: {exc}") from exc
        return found

    def put(
        self,
        chunk_hash: str,
        model_id: str,
        params_hash: str,
        vector: Sequence[float],
        *,
        token_count: int = 0,
    ) -> None:
        self.put_many([(chunk_hash, model_id, params_hash, vector, token_count)])

    def put_many(
        self,
        records: Iterable[tuple[str, str, str, Sequence[float], int]],
    ) -> None:
        rows: list[tuple[str, str, str, int, bytes, int, str, str]] = []
        seen: dict[tuple[str, str, str], tuple[bytes, int, int]] = {}
        timestamp = _now()
        for chunk_hash, model_id, params_hash, vector, token_count in records:
            if any(
                not isinstance(value, str) or not value
                for value in (chunk_hash, model_id, params_hash)
            ):
                raise BackendError("Cache identity fields must be non-empty strings")
            if type(token_count) is not int or token_count < 0:
                raise BackendError("Cached token count must be a non-negative integer")
            try:
                values = tuple(float(value) for value in vector)
            except (TypeError, ValueError, OverflowError) as exc:
                raise BackendError(f"Cached vector contains a non-numeric value: {exc}") from exc
            if not values or any(not math.isfinite(value) for value in values):
                raise BackendError("Cached vectors must be non-empty and finite")
            if len(values) > MAX_VECTOR_DIMENSIONS:
                raise BackendError(
                    f"Cached vectors cannot exceed {MAX_VECTOR_DIMENSIONS:,} dimensions"
                )
            try:
                encoded = _encode_vector(values)
            except (OverflowError, struct.error) as exc:
                raise BackendError(f"Cached vector is outside the float32 range: {exc}") from exc
            key = (chunk_hash, model_id, params_hash)
            identity = (encoded, len(values), token_count)
            prior = seen.get(key)
            if prior is not None and prior != identity:
                raise BackendError("One cache batch contains incompatible values for the same key")
            seen[key] = identity
            rows.append(
                (
                    chunk_hash,
                    model_id,
                    params_hash,
                    len(values),
                    encoded,
                    token_count,
                    timestamp,
                    timestamp,
                )
            )
        if not rows:
            return
        with self.transaction() as connection:
            for row in rows:
                existing = connection.execute(
                    """
                    SELECT dimensions, vector, token_count FROM embeddings
                    WHERE chunk_hash = ? AND model_id = ? AND params_hash = ?
                    """,
                    row[:3],
                ).fetchone()
                if existing is not None and (
                    int(existing["dimensions"]) != row[3]
                    or bytes(existing["vector"]) != row[4]
                    or int(existing["token_count"]) != row[5]
                ):
                    raise BackendError("Cache key already contains an incompatible embedding")
                connection.execute(
                    """
                    INSERT INTO embeddings(
                        chunk_hash, model_id, params_hash, dimensions, vector,
                        token_count, created_at, accessed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chunk_hash, model_id, params_hash) DO UPDATE SET
                        accessed_at = excluded.accessed_at
                    """,
                    row,
                )

    def stats(self) -> CacheStats:
        connection = self._connection
        if connection is None:
            return CacheStats(entries=0, chunks=0, models=0, bytes=0)
        try:
            with self._lock:
                row = connection.execute(
                    """
                    SELECT COUNT(*) AS entries,
                           COUNT(DISTINCT chunk_hash) AS chunks,
                           COUNT(DISTINCT model_id || ':' || params_hash) AS models,
                           COALESCE(SUM(LENGTH(vector)), 0) AS bytes
                    FROM embeddings
                    """
                ).fetchone()
        except sqlite3.Error as exc:
            raise BackendError(f"Could not inspect embedding cache: {exc}") from exc
        return CacheStats(
            entries=int(row["entries"]),
            chunks=int(row["chunks"]),
            models=int(row["models"]),
            bytes=int(row["bytes"]),
        )

    def prune(self, *, max_age_days: int | None = None, max_entries: int | None = None) -> int:
        if max_age_days is None and max_entries is None:
            raise ValueError("Specify max_age_days, max_entries, or both")
        if max_age_days is not None and (type(max_age_days) is not int or max_age_days < 0):
            raise ValueError("max_age_days must be a non-negative integer")
        if max_entries is not None and (type(max_entries) is not int or max_entries < 0):
            raise ValueError("max_entries must be a non-negative integer")
        removed = 0
        with self.transaction() as connection:
            if max_age_days is not None:
                cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
                cursor = connection.execute(
                    "DELETE FROM embeddings WHERE accessed_at < ?", (cutoff,)
                )
                removed += max(cursor.rowcount, 0)
            if max_entries is not None:
                count = int(connection.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0])
                overflow = max(0, count - max_entries)
                if overflow:
                    cursor = connection.execute(
                        """
                        DELETE FROM embeddings WHERE rowid IN (
                            SELECT rowid FROM embeddings
                            ORDER BY accessed_at ASC LIMIT ?
                        )
                        """,
                        (overflow,),
                    )
                    removed += max(cursor.rowcount, 0)
        return removed

    def export_jsonl(self, destination: str | Path, *, force: bool = False) -> int:
        output = Path(destination).expanduser().resolve()
        protected = {
            self.path,
            Path(f"{self.path}-wal").resolve(),
            Path(f"{self.path}-shm").resolve(),
        }
        if output in protected:
            raise BackendError("Cache export destination cannot be the live cache or its sidecars")
        if output.exists() and not force:
            raise BackendError(f"Refusing to overwrite existing cache export: {output}")
        temporary: str | None = None
        count = 0
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary = tempfile.mkstemp(
                dir=str(output.parent), prefix=f".{output.name}.", suffix=".tmp"
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                connection = self._connection
                if connection is not None:
                    with self._lock:
                        for row in connection.execute(
                            """
                            SELECT chunk_hash, model_id, params_hash, dimensions, vector,
                                   token_count, created_at, accessed_at
                            FROM embeddings ORDER BY chunk_hash, model_id, params_hash
                            """
                        ):
                            payload: dict[str, Any] = {
                                "chunk_hash": row["chunk_hash"],
                                "model_id": row["model_id"],
                                "params_hash": row["params_hash"],
                                "vector": list(_decode_vector(row["vector"], row["dimensions"])),
                                "token_count": row["token_count"],
                                "created_at": row["created_at"],
                                "accessed_at": row["accessed_at"],
                            }
                            handle.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
                            handle.write("\n")
                            count += 1
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, output)
        except (OSError, sqlite3.Error) as exc:
            raise BackendError(f"Could not write cache export {output}: {exc}") from exc
        finally:
            if temporary is not None and os.path.exists(temporary):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        return count

    def import_jsonl(self, source: str | Path, *, trusted: bool = False) -> int:
        if not trusted:
            raise BackendError(
                "Cache imports contain executable retrieval state; explicitly trust the source"
            )
        rows: list[tuple[str, str, str, Sequence[float], int]] = []
        source_path = Path(source).expanduser().resolve()
        if source_path in {
            self.path,
            Path(f"{self.path}-wal").resolve(),
            Path(f"{self.path}-shm").resolve(),
        }:
            raise BackendError("Cache import source cannot be the live cache or its sidecars")
        try:
            if source_path.stat().st_size > MAX_IMPORT_BYTES:
                raise BackendError(
                    f"Cache import exceeds the {MAX_IMPORT_BYTES:,}-byte safety limit"
                )
            with source_path.open("rb") as handle:
                total_bytes = 0
                number = 0
                while line := handle.readline(MAX_IMPORT_LINE_BYTES + 1):
                    number += 1
                    total_bytes += len(line)
                    if total_bytes > MAX_IMPORT_BYTES:
                        raise BackendError(
                            f"Cache import exceeds the {MAX_IMPORT_BYTES:,}-byte safety limit"
                        )
                    if len(line) > MAX_IMPORT_LINE_BYTES:
                        raise BackendError(f"Cache export line {number} exceeds the safety limit")
                    decoded_line = line.decode("utf-8")
                    if not decoded_line.strip():
                        continue
                    try:
                        item = json.loads(decoded_line)
                        if not isinstance(item, Mapping):
                            raise ValueError("each cache record must be an object")
                        identities = tuple(
                            item[name] for name in ("chunk_hash", "model_id", "params_hash")
                        )
                        if any(not isinstance(value, str) or not value for value in identities):
                            raise ValueError("identity fields must be non-empty strings")
                        raw_vector = item["vector"]
                        if not isinstance(raw_vector, list) or any(
                            type(value) not in (int, float) for value in raw_vector
                        ):
                            raise ValueError("vector must be an array of numbers")
                        vector = tuple(float(value) for value in raw_vector)
                        if not 0 < len(vector) <= MAX_VECTOR_DIMENSIONS:
                            raise ValueError(
                                f"vector dimensions must be between 1 and {MAX_VECTOR_DIMENSIONS}"
                            )
                        if any(not math.isfinite(value) for value in vector):
                            raise ValueError("vector values must be finite")
                        token_count = item.get("token_count", 0)
                        if type(token_count) is not int or not 0 <= token_count <= 2**63 - 1:
                            raise ValueError("token_count must be a non-negative SQLite integer")
                        rows.append(
                            (
                                identities[0],
                                identities[1],
                                identities[2],
                                vector,
                                token_count,
                            )
                        )
                    except (KeyError, TypeError, ValueError, OverflowError) as exc:
                        raise BackendError(
                            f"Invalid cache export at {source_path}:{number}: {exc}"
                        ) from exc
        except (OSError, UnicodeError) as exc:
            raise BackendError(f"Could not read cache export {source_path}: {exc}") from exc
        self.put_many(rows)
        return len(rows)
