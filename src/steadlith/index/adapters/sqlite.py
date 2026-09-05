"""Transactional local vector index with tombstone-aware querying."""

from __future__ import annotations

import heapq
import json
import math
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from steadlith._legacy_wire import V1_INDEX_RECORD_DOMAIN
from steadlith._sqlite_values import decode_vector, find_blocking_parent
from steadlith._sqlite_values import encode_vector as _encode_vector
from steadlith._sqlite_values import utc_now as _now
from steadlith.config import MAX_VECTOR_DIMENSIONS
from steadlith.content.hashing import chunk_content_hash, hash_fields
from steadlith.content.manifest import CorpusManifest
from steadlith.errors import BackendError
from steadlith.index.adapters.base import (
    DocumentState,
    IndexRecord,
    IndexStatus,
    VectorMatch,
)
from steadlith.models import is_hard_cut

SCHEMA_VERSION = 2


def _decode_vector(payload: bytes, dimensions: int) -> tuple[float, ...]:
    return decode_vector(
        payload,
        dimensions,
        corrupt_message="The index contains a corrupt vector payload",
    )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return float("-inf")
    left_norm = math.hypot(*left)
    right_norm = math.hypot(*right)
    if not left_norm or not right_norm:
        return 0.0
    return sum((a / left_norm) * (b / right_norm) for a, b in zip(left, right, strict=True))


def _query_match(
    row: sqlite3.Row,
    query_vector: Sequence[float],
    expected_dimensions: int,
) -> VectorMatch:
    candidate = _decode_vector(row["vector"], int(row["dimensions"]))
    if len(candidate) != expected_dimensions or any(
        not math.isfinite(value) for value in candidate
    ):
        raise BackendError("The index contains an invalid candidate vector")
    metadata = json.loads(row["metadata_json"])
    if not isinstance(metadata, Mapping):
        raise ValueError("chunk metadata is not an object")
    score = _cosine(query_vector, candidate)
    if not math.isfinite(score):
        raise BackendError("The index produced a non-finite similarity score")
    return VectorMatch(
        score=score,
        chunk_hash=row["chunk_hash"],
        text=row["text"],
        document_id=row["document_id"],
        start_offset=int(row["start_offset"]),
        end_offset=int(row["end_offset"]),
        metadata=dict(metadata),
    )


def _record_digest(
    *,
    instance_id: str,
    document_id: str,
    position: int,
    chunk_hash: str,
    text: str,
    start_offset: int,
    end_offset: int,
    token_count: int,
    vector: bytes,
    dimensions: int,
    model_id: str,
    params_hash: str,
    metadata_json: str,
) -> str:
    """Commit to every durable active-record field, including vector bytes."""

    return hash_fields(
        V1_INDEX_RECORD_DOMAIN,
        (
            instance_id,
            document_id,
            str(position),
            chunk_hash,
            text,
            str(start_offset),
            str(end_offset),
            str(token_count),
            vector,
            str(dimensions),
            model_id,
            params_hash,
            metadata_json,
        ),
    )


class SQLiteIndex:
    """Reference adapter optimized for correctness and easy local evaluation."""

    supports_metadata_filtering = True

    def __init__(self, path: str | Path, *, readonly: bool = False) -> None:
        requested_path = Path(path).expanduser()
        try:
            self.path = requested_path.resolve()
            blocking_path = find_blocking_parent(self.path)
            if blocking_path is not None:
                raise BackendError(
                    f"Could not prepare SQLite index path {requested_path}: "
                    f"{blocking_path} blocks the path. Rename the blocking file or configure "
                    "index.database to use another path."
                )
            if not readonly:
                self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            blocking_path = exc.filename or requested_path
            raise BackendError(
                f"Could not prepare SQLite index path {requested_path}: "
                f"{blocking_path} blocks the path. Rename the blocking file or configure "
                "index.database to use another path."
            ) from exc
        self.readonly = readonly
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None
        if readonly:
            self._connection = self._open_readonly()
        else:
            try:
                self._connection = sqlite3.connect(
                    str(self.path), timeout=30, check_same_thread=False
                )
                self._connection.row_factory = sqlite3.Row
                self._connection.execute("PRAGMA journal_mode = WAL")
                self._connection.execute("PRAGMA synchronous = FULL")
                self._initialize()
            except BackendError:
                if getattr(self, "_connection", None) is not None:
                    self._connection.close()
                    self._connection = None
                raise
            except sqlite3.Error as exc:
                if getattr(self, "_connection", None) is not None:
                    self._connection.close()
                    self._connection = None
                raise BackendError(f"Could not open SQLite index {self.path}: {exc}") from exc

    def _open_readonly(self) -> sqlite3.Connection | None:
        if not self.path.exists():
            return None
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"{self.path.as_uri()}?mode=ro", uri=True, timeout=30, check_same_thread=False
            )
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT value FROM index_meta WHERE key = 'schema_version'"
            ).fetchone()
            if row is None:
                raise BackendError("Index schema version is absent")
            try:
                schema_version = int(row["value"])
            except (TypeError, ValueError) as exc:
                raise BackendError(f"Invalid index schema version: {row['value']!r}") from exc
            if schema_version != SCHEMA_VERSION:
                raise BackendError(
                    f"Unsupported index schema {row['value']}; expected {SCHEMA_VERSION}"
                )
            return connection
        except BackendError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise BackendError(f"Could not read SQLite index {self.path}: {exc}") from exc

    def _initialize(self) -> None:
        connection = self._require_connection()
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS index_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                root_hash TEXT NOT NULL,
                chunk_count INTEGER NOT NULL,
                hard_cuts INTEGER NOT NULL,
                metadata_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chunks (
                instance_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                chunk_hash TEXT NOT NULL,
                text TEXT NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                token_count INTEGER NOT NULL,
                vector BLOB NOT NULL,
                dimensions INTEGER NOT NULL,
                model_id TEXT NOT NULL,
                params_hash TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                record_digest TEXT NOT NULL,
                valid_from TEXT NOT NULL,
                valid_to TEXT
            );
            CREATE INDEX IF NOT EXISTS chunks_active_document
                ON chunks(document_id, position) WHERE valid_to IS NULL;
            CREATE INDEX IF NOT EXISTS chunks_active_hash
                ON chunks(chunk_hash) WHERE valid_to IS NULL;
            CREATE INDEX IF NOT EXISTS chunks_valid_to ON chunks(valid_to);
            """
        )
        row = connection.execute(
            "SELECT value FROM index_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            connection.execute(
                "INSERT INTO index_meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        else:
            try:
                schema_version = int(row["value"])
            except (TypeError, ValueError) as exc:
                raise BackendError(f"Invalid index schema version: {row['value']!r}") from exc
            if schema_version != SCHEMA_VERSION:
                raise BackendError(
                    f"Unsupported index schema {row['value']}; expected {SCHEMA_VERSION}"
                )
        connection.execute(
            "INSERT OR IGNORE INTO index_meta(key, value) VALUES ('generation', '0')"
        )
        connection.commit()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise BackendError(f"Index does not exist: {self.path}")
        return self._connection

    @staticmethod
    def _read(
        connection: sqlite3.Connection,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> sqlite3.Cursor:
        try:
            return connection.execute(statement, parameters)
        except sqlite3.Error as exc:
            raise BackendError(f"Could not read SQLite index: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def __enter__(self) -> SQLiteIndex:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def read_snapshot(self) -> Iterator[None]:
        """Keep related reads on one committed generation until the context exits."""

        with self._lock:
            connection = self._connection
            owns_transaction = connection is not None and not connection.in_transaction
            try:
                if owns_transaction and connection is not None:
                    connection.execute("BEGIN")
                yield
            except sqlite3.Error as exc:
                raise BackendError(f"Could not read SQLite index: {exc}") from exc
            finally:
                if owns_transaction and connection is not None and connection.in_transaction:
                    connection.rollback()

    def get_manifest_payload(self) -> Mapping[str, Any] | None:
        with self.read_snapshot():
            return self._get_manifest_payload()

    def _get_manifest_payload(self) -> Mapping[str, Any] | None:
        connection = self._connection
        if connection is None:
            return None
        row = self._read(
            connection, "SELECT value FROM index_meta WHERE key = 'manifest_json'"
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["value"])
        except json.JSONDecodeError as exc:
            raise BackendError(f"Stored manifest is not valid JSON: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise BackendError("Stored manifest root is not an object")
        return payload

    def status(self) -> IndexStatus:
        with self.read_snapshot():
            return self._status()

    def _status(self) -> IndexStatus:
        connection = self._connection
        if connection is None:
            return IndexStatus(None, 0, 0, 0, 0, 0, 0, None, None)
        meta = {
            row["key"]: row["value"]
            for row in self._read(
                connection,
                """
                SELECT key, value FROM index_meta
                WHERE key IN ('corpus_root', 'generation', 'model_id', 'params_hash')
                """,
            )
        }
        try:
            generation = int(meta.get("generation", "0"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise BackendError("Stored index generation is not an integer") from exc
        if generation < 0:
            raise BackendError("Stored index generation cannot be negative")
        documents = int(self._read(connection, "SELECT COUNT(*) FROM documents").fetchone()[0])
        active = int(
            self._read(connection, "SELECT COUNT(*) FROM chunks WHERE valid_to IS NULL").fetchone()[
                0
            ]
        )
        tombstones = int(
            self._read(
                connection, "SELECT COUNT(*) FROM chunks WHERE valid_to IS NOT NULL"
            ).fetchone()[0]
        )
        cuts = self._read(
            connection,
            """
            SELECT COALESCE(SUM(hard_cuts), 0),
                   COALESCE(SUM(CASE WHEN chunk_count > 0 THEN chunk_count - 1 ELSE 0 END), 0),
                   COALESCE(SUM(CASE
                       WHEN typeof(hard_cuts) != 'integer' OR hard_cuts < 0
                         OR typeof(chunk_count) != 'integer' OR chunk_count < 0
                       THEN 1 ELSE 0 END), 0)
            FROM documents
            """,
        ).fetchone()
        try:
            if int(cuts[2]):
                raise ValueError("invalid counter storage type or value")
            return IndexStatus(
                corpus_root=meta.get("corpus_root"),
                generation=generation,
                documents=documents,
                active_chunks=active,
                tombstoned_chunks=tombstones,
                hard_cuts=int(cuts[0]),
                total_boundaries=int(cuts[1]),
                model_id=meta.get("model_id"),
                params_hash=meta.get("params_hash"),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise BackendError("Stored index counters are invalid") from exc

    def active_records(self) -> tuple[IndexRecord, ...]:
        with self.read_snapshot():
            return self._active_records()

    def _active_records(self) -> tuple[IndexRecord, ...]:
        connection = self._connection
        if connection is None:
            return ()
        rows = self._read(
            connection,
            """
            SELECT instance_id, document_id, position, chunk_hash, text,
                   start_offset, end_offset, token_count, metadata_json,
                   vector, dimensions
            FROM chunks WHERE valid_to IS NULL
            ORDER BY document_id, position, instance_id
            """,
        )
        try:
            return tuple(
                IndexRecord(
                    instance_id=row["instance_id"],
                    document_id=row["document_id"],
                    position=int(row["position"]),
                    chunk_hash=row["chunk_hash"],
                    text=row["text"],
                    start_offset=int(row["start_offset"]),
                    end_offset=int(row["end_offset"]),
                    token_count=int(row["token_count"]),
                    metadata=json.loads(row["metadata_json"]),
                    vector=_decode_vector(row["vector"], int(row["dimensions"])),
                )
                for row in rows
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BackendError(f"The index contains an invalid active record: {exc}") from exc

    def apply_snapshot(
        self,
        *,
        records: Iterable[IndexRecord],
        documents: Iterable[DocumentState],
        manifest_payload: Mapping[str, Any],
        corpus_root: str,
        model_id: str,
        params_hash: str,
        vector_dimensions: int,
        expected_generation: int,
        check_root: bool = False,
        expected_root: str | None = None,
    ) -> tuple[int, int]:
        """Atomically expose a complete desired snapshot and tombstone removals."""

        if self.readonly:
            raise BackendError("Cannot apply to a read-only index")
        connection = self._require_connection()
        desired = tuple(records)
        states = tuple(documents)
        desired_ids = {record.instance_id for record in desired}
        desired_positions = {(record.document_id, record.position) for record in desired}
        state_ids = {state.document_id for state in states}
        if len(desired_ids) != len(desired):
            raise BackendError("Snapshot contains duplicate occurrence identifiers")
        if len(desired_positions) != len(desired):
            raise BackendError("Snapshot contains duplicate document positions")
        if len(state_ids) != len(states):
            raise BackendError("Snapshot contains duplicate document states")
        timestamp = _now()
        if type(expected_generation) is not int or expected_generation < 0:
            raise BackendError("Expected index generation must be a non-negative integer")
        if type(vector_dimensions) is not int or not 0 < vector_dimensions <= MAX_VECTOR_DIMENSIONS:
            raise BackendError(f"Vector dimensions must be between 1 and {MAX_VECTOR_DIMENSIONS:,}")
        if any(len(record.vector) != vector_dimensions for record in desired):
            raise BackendError("Snapshot contains a vector with unexpected dimensions")
        try:
            if any(
                not math.isfinite(float(value)) for record in desired for value in record.vector
            ):
                raise BackendError("Snapshot vectors must contain only finite values")
        except (TypeError, ValueError, OverflowError) as exc:
            raise BackendError(f"Snapshot contains a non-numeric vector value: {exc}") from exc
        try:
            with self._lock, connection:
                connection.execute("BEGIN IMMEDIATE")
                generation_row = connection.execute(
                    "SELECT value FROM index_meta WHERE key = 'generation'"
                ).fetchone()
                try:
                    current_generation = (
                        int(generation_row["value"]) if generation_row is not None else 0
                    )
                except ValueError as exc:
                    raise BackendError("Stored index generation is not an integer") from exc
                if current_generation < 0:
                    raise BackendError("Stored index generation cannot be negative")
                if current_generation != expected_generation:
                    raise BackendError(
                        "Index state changed after this plan was prepared; "
                        "prepare a fresh plan and retry"
                    )
                if check_root:
                    root_row = connection.execute(
                        "SELECT value FROM index_meta WHERE key = 'corpus_root'"
                    ).fetchone()
                    current_root = str(root_row["value"]) if root_row is not None else None
                    if current_root != expected_root:
                        raise BackendError(
                            "Index state changed after this plan was prepared; "
                            "prepare a fresh plan and retry"
                        )
                active_rows = connection.execute(
                    "SELECT instance_id FROM chunks WHERE valid_to IS NULL"
                ).fetchall()
                removed_ids = [
                    row["instance_id"]
                    for row in active_rows
                    if row["instance_id"] not in desired_ids
                ]
                if removed_ids:
                    connection.executemany(
                        "UPDATE chunks SET valid_to = ? WHERE instance_id = ? AND valid_to IS NULL",
                        ((timestamp, instance_id) for instance_id in removed_ids),
                    )
                for record in desired:
                    metadata_json = json.dumps(
                        dict(record.metadata), sort_keys=True, separators=(",", ":")
                    )
                    encoded_vector = _encode_vector(record.vector)
                    record_digest = _record_digest(
                        instance_id=record.instance_id,
                        document_id=record.document_id,
                        position=record.position,
                        chunk_hash=record.chunk_hash,
                        text=record.text,
                        start_offset=record.start_offset,
                        end_offset=record.end_offset,
                        token_count=record.token_count,
                        vector=encoded_vector,
                        dimensions=len(record.vector),
                        model_id=model_id,
                        params_hash=params_hash,
                        metadata_json=metadata_json,
                    )
                    existing = connection.execute(
                        """
                        SELECT chunk_hash, text, vector, dimensions, model_id, params_hash,
                               valid_to
                        FROM chunks WHERE instance_id = ?
                        """,
                        (record.instance_id,),
                    ).fetchone()
                    if existing is not None and existing["valid_to"] is not None:
                        raise BackendError(
                            f"Tombstoned occurrence {record.instance_id!r} cannot be "
                            "reactivated; mint a new lifecycle identifier"
                        )
                    if existing is not None and (
                        existing["chunk_hash"] != record.chunk_hash
                        or existing["text"] != record.text
                        or bytes(existing["vector"]) != encoded_vector
                        or int(existing["dimensions"]) != len(record.vector)
                        or existing["model_id"] != model_id
                        or existing["params_hash"] != params_hash
                    ):
                        raise BackendError(
                            f"Occurrence identifier {record.instance_id!r} was reused with "
                            "incompatible content or embedding identity"
                        )
                    connection.execute(
                        """
                        INSERT INTO chunks(
                            instance_id, document_id, position, chunk_hash, text,
                            start_offset, end_offset, token_count, vector, dimensions,
                            model_id, params_hash, metadata_json, record_digest,
                            valid_from, valid_to
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                        ON CONFLICT(instance_id) DO UPDATE SET
                            document_id = excluded.document_id,
                            position = excluded.position,
                            start_offset = excluded.start_offset,
                            end_offset = excluded.end_offset,
                            token_count = excluded.token_count,
                            metadata_json = excluded.metadata_json,
                            record_digest = excluded.record_digest,
                            valid_to = NULL
                        """,
                        (
                            record.instance_id,
                            record.document_id,
                            record.position,
                            record.chunk_hash,
                            record.text,
                            record.start_offset,
                            record.end_offset,
                            record.token_count,
                            encoded_vector,
                            len(record.vector),
                            model_id,
                            params_hash,
                            metadata_json,
                            record_digest,
                            timestamp,
                        ),
                    )
                connection.execute("DELETE FROM documents")
                connection.executemany(
                    """
                    INSERT INTO documents(
                        document_id, root_hash, chunk_count, hard_cuts,
                        metadata_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            state.document_id,
                            state.root_hash,
                            state.chunk_count,
                            state.hard_cuts,
                            json.dumps(dict(state.metadata), sort_keys=True, separators=(",", ":")),
                            timestamp,
                        )
                        for state in states
                    ),
                )
                meta = {
                    "corpus_root": corpus_root,
                    "generation": str(current_generation + 1),
                    "manifest_json": json.dumps(
                        manifest_payload, sort_keys=True, separators=(",", ":")
                    ),
                    "model_id": model_id,
                    "params_hash": params_hash,
                    "dimensions": str(vector_dimensions),
                    "updated_at": timestamp,
                }
                connection.executemany(
                    """
                    INSERT INTO index_meta(key, value) VALUES (?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    meta.items(),
                )
        except BackendError:
            raise
        except sqlite3.Error as exc:
            raise BackendError(f"Could not apply index snapshot: {exc}") from exc
        except Exception as exc:
            raise BackendError(f"Could not serialize index snapshot: {exc}") from exc
        return len(desired), len(removed_ids)

    def query(
        self,
        vector: Sequence[float],
        *,
        limit: int = 10,
        expected_model_id: str | None = None,
        expected_params_hash: str | None = None,
    ) -> list[VectorMatch]:
        if limit <= 0:
            return []
        connection = self._connection
        if connection is None:
            return []
        try:
            query_vector = tuple(float(value) for value in vector)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"query vector contains a non-numeric value: {exc}") from exc
        if not query_vector or any(not math.isfinite(value) for value in query_vector):
            raise ValueError("query vector must be non-empty and finite")
        try:
            with self.read_snapshot():
                meta = {
                    str(row["key"]): str(row["value"])
                    for row in connection.execute(
                        """
                        SELECT key, value FROM index_meta
                        WHERE key IN ('dimensions', 'model_id', 'params_hash')
                        """
                    )
                }
                try:
                    expected_dimensions = int(meta.get("dimensions", ""))
                    if not 0 < expected_dimensions <= MAX_VECTOR_DIMENSIONS:
                        raise ValueError
                except ValueError as exc:
                    raise BackendError("Stored index vector dimensions are invalid") from exc
                if expected_model_id is not None and meta.get("model_id") != expected_model_id:
                    raise BackendError(
                        "Active index embedding model changed while the query was prepared"
                    )
                if (
                    expected_params_hash is not None
                    and meta.get("params_hash") != expected_params_hash
                ):
                    raise BackendError(
                        "Active index embedding parameters changed while the query was prepared"
                    )
                if len(query_vector) != expected_dimensions:
                    raise ValueError(
                        f"query vector has {len(query_vector)} dimensions; "
                        f"index requires {expected_dimensions}"
                    )
                rows = connection.execute(
                    """
                    SELECT chunk_hash, text, document_id, start_offset, end_offset,
                           vector, dimensions, metadata_json
                    FROM chunks WHERE valid_to IS NULL
                    """
                )
                try:
                    matches = heapq.nsmallest(
                        limit,
                        (_query_match(row, query_vector, expected_dimensions) for row in rows),
                        key=lambda item: (-item.score, item.document_id, item.start_offset),
                    )
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise BackendError(
                        f"The index contains an invalid query record: {exc}"
                    ) from exc
        except sqlite3.Error as exc:
            raise BackendError(f"Could not query SQLite index: {exc}") from exc
        return matches

    def compact(self, *, before: str | None = None, dry_run: bool = False) -> int:
        if self.readonly:
            raise BackendError("Cannot compact a read-only index")
        connection = self._require_connection()
        if before is None:
            cutoff = _now()
        else:
            try:
                parsed = datetime.fromisoformat(before.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("before must be a valid ISO-8601 timestamp") from exc
            if parsed.tzinfo is None:
                raise ValueError("before must include a timezone offset")
            cutoff = parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")
        try:
            with self._lock:
                if dry_run:
                    row = connection.execute(
                        """
                        SELECT COUNT(*) FROM chunks
                        WHERE valid_to IS NOT NULL AND valid_to <= ?
                        """,
                        (cutoff,),
                    ).fetchone()
                    return int(row[0])
                cursor = connection.execute(
                    "DELETE FROM chunks WHERE valid_to IS NOT NULL AND valid_to <= ?", (cutoff,)
                )
                removed = max(cursor.rowcount, 0)
                connection.commit()
                connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
                return removed
        except sqlite3.Error as exc:
            raise BackendError(f"Could not compact index: {exc}") from exc

    def verify(self) -> tuple[bool, tuple[str, ...]]:
        with self.read_snapshot():
            return self._verify()

    def _verify(self) -> tuple[bool, tuple[str, ...]]:
        connection = self._connection
        if connection is None:
            return False, ("index does not exist",)
        problems: list[str] = []
        try:
            meta = {
                str(row["key"]): str(row["value"])
                for row in connection.execute("SELECT key, value FROM index_meta")
            }
            manifest: CorpusManifest | None = None
            raw_manifest = meta.get("manifest_json")
            if raw_manifest is None:
                problems.append("manifest is absent")
            else:
                try:
                    decoded = json.loads(raw_manifest)
                    if not isinstance(decoded, Mapping):
                        raise ValueError("manifest root is not an object")
                    manifest = CorpusManifest.from_dict(decoded)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    problems.append(f"manifest is invalid: {exc}")

            corpus_root = meta.get("corpus_root")
            if corpus_root is None:
                problems.append("corpus root is absent")
            if manifest is not None and corpus_root != manifest.root_hash:
                problems.append(
                    f"manifest root {manifest.root_hash} differs from index root {corpus_root}"
                )
            model_id = meta.get("model_id")
            params_hash = meta.get("params_hash")
            if not model_id or not params_hash:
                problems.append("embedding model identity is absent")
            try:
                expected_dimensions = int(meta.get("dimensions", ""))
                if expected_dimensions <= 0:
                    raise ValueError
            except ValueError:
                expected_dimensions = 0
                problems.append("index vector dimensions are not a positive integer")
            try:
                generation = int(meta.get("generation", ""))
                if generation < 0:
                    raise ValueError
            except ValueError:
                problems.append("index generation is not a non-negative integer")

            document_rows = {
                str(row["document_id"]): row
                for row in connection.execute(
                    """
                    SELECT document_id, root_hash, chunk_count, hard_cuts, metadata_json
                    FROM documents
                    """
                )
            }
            chunk_rows = list(
                connection.execute(
                    """
                    SELECT instance_id, document_id, position, chunk_hash, text,
                           start_offset, end_offset, token_count, vector, dimensions,
                           model_id, params_hash, metadata_json, record_digest
                    FROM chunks WHERE valid_to IS NULL
                    ORDER BY document_id, position, instance_id
                    """
                )
            )

            if manifest is not None:
                expected_documents = set(manifest.documents)
                actual_documents = set(document_rows)
                for document_id in sorted(expected_documents - actual_documents):
                    problems.append(f"document {document_id!r} is missing from index state")
                for document_id in sorted(actual_documents - expected_documents):
                    problems.append(f"document {document_id!r} is unexpected in index state")
                for document_id in sorted(expected_documents & actual_documents):
                    expected_document = manifest.documents[document_id]
                    actual_document = document_rows[document_id]
                    if actual_document["root_hash"] != expected_document.root_hash:
                        problems.append(f"document {document_id!r} root hash differs")
                    if int(actual_document["chunk_count"]) != len(expected_document.chunks):
                        problems.append(f"document {document_id!r} chunk count differs")
                    expected_hard_cuts = sum(
                        is_hard_cut(chunk.metadata) for chunk in expected_document.chunks
                    )
                    if int(actual_document["hard_cuts"]) != expected_hard_cuts:
                        problems.append(f"document {document_id!r} hard-cut count differs")
                    try:
                        document_metadata = json.loads(actual_document["metadata_json"])
                    except (TypeError, json.JSONDecodeError) as exc:
                        problems.append(f"document {document_id!r} metadata is invalid: {exc}")
                    else:
                        if document_metadata != dict(expected_document.metadata):
                            problems.append(f"document {document_id!r} metadata differs")

                rows_by_position: dict[tuple[str, int], list[sqlite3.Row]] = {}
                for row in chunk_rows:
                    key = (str(row["document_id"]), int(row["position"]))
                    rows_by_position.setdefault(key, []).append(row)
                expected_positions = {
                    (document_id, position)
                    for document_id, document in manifest.documents.items()
                    for position in range(len(document.chunks))
                }
                actual_positions = set(rows_by_position)
                for document_id, position in sorted(expected_positions - actual_positions):
                    problems.append(f"{document_id} position {position} is missing")
                for document_id, position in sorted(actual_positions - expected_positions):
                    problems.append(f"{document_id} position {position} is unexpected")

                for key in sorted(expected_positions & actual_positions):
                    rows = rows_by_position[key]
                    document_id, position = key
                    if len(rows) != 1:
                        problems.append(
                            f"{document_id} position {position} has {len(rows)} active rows"
                        )
                        continue
                    row = rows[0]
                    expected_document = manifest.documents[document_id]
                    expected = expected_document.chunks[position]
                    scalar_fields = {
                        "chunk hash": (str(row["chunk_hash"]), expected.chunk_hash),
                        "start offset": (int(row["start_offset"]), expected.start_offset),
                        "end offset": (int(row["end_offset"]), expected.end_offset),
                        "token count": (int(row["token_count"]), expected.token_count),
                    }
                    for label, (actual_value, expected_value) in scalar_fields.items():
                        if actual_value != expected_value:
                            problems.append(f"{document_id} position {position} {label} differs")
                    if str(row["model_id"]) != model_id or str(row["params_hash"]) != params_hash:
                        problems.append(
                            f"{document_id} position {position} embedding identity differs"
                        )
                    try:
                        metadata = json.loads(row["metadata_json"])
                        if not isinstance(metadata, Mapping):
                            raise ValueError("chunk metadata is not an object")
                    except (TypeError, ValueError) as exc:
                        problems.append(
                            f"{document_id} position {position} metadata is invalid: {exc}"
                        )
                    else:
                        reserved_metadata = {
                            "chunk_hash": expected.chunk_hash,
                            "start_offset": expected.start_offset,
                            "end_offset": expected.end_offset,
                            "token_count": expected.token_count,
                        }
                        for name, expected_value in reserved_metadata.items():
                            if name in metadata and metadata[name] != expected_value:
                                problems.append(
                                    f"{document_id} position {position} metadata {name} differs"
                                )
                        user_metadata = {
                            name: value
                            for name, value in metadata.items()
                            if name not in reserved_metadata
                        }
                        if user_metadata != dict(expected.metadata):
                            problems.append(f"{document_id} position {position} metadata differs")

                    if (
                        expected_document.chunker_id
                        and expected_document.chunker_params_hash
                        and expected_document.normalizer_version
                    ):
                        calculated_hash = chunk_content_hash(
                            str(row["text"]),
                            chunker_id=expected_document.chunker_id,
                            chunker_params_hash=expected_document.chunker_params_hash,
                            normalizer_version=expected_document.normalizer_version,
                        )
                        if calculated_hash != expected.chunk_hash:
                            problems.append(
                                f"{document_id} position {position} text does not match chunk hash"
                            )
                    else:
                        problems.append(f"document {document_id!r} chunk identity is incomplete")

                    try:
                        vector_payload = bytes(row["vector"])
                        dimensions = int(row["dimensions"])
                        vector = _decode_vector(vector_payload, dimensions)
                    except (TypeError, ValueError, BackendError) as exc:
                        problems.append(
                            f"{document_id} position {position} vector is invalid: {exc}"
                        )
                    else:
                        if dimensions <= 0 or any(not math.isfinite(value) for value in vector):
                            problems.append(f"{document_id} position {position} vector is invalid")
                        if dimensions != expected_dimensions:
                            problems.append(
                                f"{document_id} position {position} vector dimensions differ"
                            )
                        calculated_digest = _record_digest(
                            instance_id=str(row["instance_id"]),
                            document_id=document_id,
                            position=position,
                            chunk_hash=str(row["chunk_hash"]),
                            text=str(row["text"]),
                            start_offset=int(row["start_offset"]),
                            end_offset=int(row["end_offset"]),
                            token_count=int(row["token_count"]),
                            vector=vector_payload,
                            dimensions=dimensions,
                            model_id=str(row["model_id"]),
                            params_hash=str(row["params_hash"]),
                            metadata_json=str(row["metadata_json"]),
                        )
                        if calculated_digest != row["record_digest"]:
                            problems.append(
                                f"{document_id} position {position} record digest differs"
                            )
        except sqlite3.Error as exc:
            raise BackendError(f"Could not verify SQLite index: {exc}") from exc
        except (TypeError, ValueError, OverflowError) as exc:
            problems.append(f"index contains invalid scalar data: {exc}")
        return not problems, tuple(problems)
