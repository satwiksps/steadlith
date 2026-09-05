"""Crash-recoverable configuration and SQLite index migrations."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from steadlith.config import SteadlithConfig, load_config, loads_config, pending_migration_path
from steadlith.embed import embedding_identity
from steadlith.errors import BackendError, ConfigError
from steadlith.index.adapters import SQLiteIndex
from steadlith.index.service import ApplyResult, PreparedIndex, apply_prepared, prepare_index
from steadlith.migrate.planner import MigrationPlan

_CONFIG_LIMIT = 1_048_576
_JOURNAL_LIMIT = 4_194_304
_JOURNAL_VERSION = 1
_TABLE = re.compile(r"""^\s*\[\s*(['"]?)([A-Za-z0-9_-]+)\1\s*\]\s*(?:#.*)?$""")

ConfigValue = str | int


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_bytes(path: Path, *, limit: int, label: str) -> bytes:
    try:
        size = path.stat().st_size
        if size > limit:
            raise ConfigError(f"{label} exceeds the {limit:,}-byte safety limit: {path}")
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise ConfigError(f"{label} does not exist: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {label.lower()} {path}: {exc}") from exc


def _read_config_text(path: Path) -> str:
    payload = _read_bytes(path, limit=_CONFIG_LIMIT, label="Configuration")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Configuration is not valid UTF-8: {path}") from exc


def _toml_scalar(value: ConfigValue) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if type(value) is int:
        return str(value)
    raise ConfigError(f"Unsupported migration value: {value!r}")


def _set_toml_value(text: str, table: str, key: str, value: ConfigValue) -> str:
    """Replace or add one simple key while preserving the rest of the TOML file."""

    lines = text.splitlines()
    table_start: int | None = None
    table_end = len(lines)
    for index, line in enumerate(lines):
        match = _TABLE.match(line)
        if match is None:
            continue
        if table_start is not None:
            table_end = index
            break
        if match.group(2) == table:
            table_start = index
    assignment = f"{key} = {_toml_scalar(value)}"
    if table_start is None:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend((f"[{table}]", assignment))
    else:
        key_pattern = re.compile(rf"""^\s*(['"]?){re.escape(key)}\1\s*=""")
        for index in range(table_start + 1, table_end):
            if key_pattern.match(lines[index]):
                indent = lines[index][: len(lines[index]) - len(lines[index].lstrip())]
                lines[index] = f"{indent}{assignment}"
                break
        else:
            lines.insert(table_end, assignment)
    return "\n".join(lines) + "\n"


def _patched_config(text: str, overrides: Mapping[str, ConfigValue]) -> str:
    result = text
    for dotted_key in sorted(overrides):
        try:
            table, key = dotted_key.split(".", 1)
        except ValueError as exc:  # pragma: no cover - internal caller contract
            raise ConfigError(f"Invalid migration setting name: {dotted_key}") from exc
        if table not in {"chunker", "embedding"}:
            raise ConfigError(f"Migration cannot update [{table}]")
        result = _set_toml_value(result, table, key, overrides[dotted_key])
    return result


def _config_changes(
    before: SteadlithConfig, after: SteadlithConfig
) -> dict[str, dict[str, object]]:
    changes: dict[str, dict[str, object]] = {}
    for table in ("chunker", "embedding"):
        old_values = dataclasses.asdict(getattr(before, table))
        new_values = dataclasses.asdict(getattr(after, table))
        for key in sorted(old_values):
            if old_values[key] != new_values[key]:
                changes[f"{table}.{key}"] = {
                    "from": old_values[key],
                    "to": new_values[key],
                }
    return changes


@dataclass(frozen=True)
class PreparedMigration:
    config_path: Path
    current_config: SteadlithConfig
    desired_config: SteadlithConfig
    before_config: str
    after_config: str
    prepared_index: PreparedIndex
    summary: MigrationPlan
    changes: Mapping[str, Mapping[str, object]]
    source_scope_overridden: bool = False
    kind: str = "migration"
    rollback_of: str | None = None
    old_model_id: str | None = None
    old_params_hash: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload = self.summary.as_dict()
        payload.update(
            {
                "kind": self.kind,
                "rollback_of": self.rollback_of,
                "configuration_changes": dict(self.changes),
                "expected_generation": self.prepared_index.expected_generation,
                "target_generation": self.prepared_index.expected_generation + 1,
                "source_scope_overridden": self.source_scope_overridden,
            }
        )
        return payload


@dataclass(frozen=True)
class MigrationResult:
    migration_id: str
    receipt: Path
    applied: ApplyResult

    def as_dict(self) -> dict[str, object]:
        return {
            "migration_id": self.migration_id,
            "receipt": str(self.receipt),
            "applied": self.applied.as_dict(),
        }


@dataclass(frozen=True)
class RecoveryResult:
    outcome: str
    migration_id: str | None = None
    receipt: Path | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "migration_id": self.migration_id,
            "receipt": str(self.receipt) if self.receipt is not None else None,
        }


def _make_prepared(
    *,
    config_path: Path,
    current: SteadlithConfig,
    desired: SteadlithConfig,
    before_text: str,
    after_text: str,
    paths: Sequence[str | Path],
    kind: str,
    rollback_of: str | None = None,
) -> PreparedMigration:
    if len(after_text.encode("utf-8")) > _CONFIG_LIMIT:
        raise ConfigError(
            f"Target configuration exceeds the {_CONFIG_LIMIT:,}-byte migration safety limit"
        )
    changes = _config_changes(current, desired)
    if not changes:
        raise ConfigError("The requested migration does not change chunking or embedding config")
    prepared = prepare_index(desired, paths)
    if not prepared.plan.requires_apply:
        raise ConfigError("The requested configuration change produced no durable index change")
    summary = MigrationPlan(
        plan=prepared.plan,
        chunker_changed=current.chunker != desired.chunker,
        embedding_changed=embedding_identity(current.embedding)
        != embedding_identity(desired.embedding),
        from_chunker=current.chunker.strategy,
        to_chunker=desired.chunker.strategy,
        from_model=f"{current.embedding.provider}:{current.embedding.model}",
        to_model=f"{desired.embedding.provider}:{desired.embedding.model}",
    )
    with SQLiteIndex(current.resolve(current.index.database), readonly=True) as index:
        old_status = index.status()
    if old_status.generation != prepared.expected_generation:
        raise BackendError("Index state changed while the migration was being prepared; re-plan")
    return PreparedMigration(
        config_path=config_path,
        current_config=current,
        desired_config=desired,
        before_config=before_text,
        after_config=after_text,
        prepared_index=prepared,
        summary=summary,
        changes=changes,
        source_scope_overridden=bool(paths),
        kind=kind,
        rollback_of=rollback_of,
        old_model_id=old_status.model_id,
        old_params_hash=old_status.params_hash,
    )


def prepare_migration(
    config_path: str | Path,
    paths: Sequence[str | Path] = (),
    *,
    overrides: Mapping[str, ConfigValue],
) -> PreparedMigration:
    """Prepare a write-free config/index migration."""

    destination = Path(config_path).expanduser().resolve()
    current = load_config(destination)
    before_text = _read_config_text(destination)
    after_text = _patched_config(before_text, overrides)
    desired = loads_config(after_text, base_dir=destination.parent, config_path=destination)
    expected = dataclasses.asdict(current)
    for dotted_key, value in overrides.items():
        table, key = dotted_key.split(".", 1)
        expected[table][key] = value
    if dataclasses.asdict(desired) != expected:
        raise ConfigError(
            "Migration could not safely update the requested TOML settings. Use named "
            "[chunker] and [embedding] tables with single-line values, then re-plan."
        )
    return _make_prepared(
        config_path=destination,
        current=current,
        desired=desired,
        before_text=before_text,
        after_text=after_text,
        paths=paths,
        kind="migration",
    )


def _safe_stored_path(raw: object, *, base: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ConfigError(f"Pending migration has an invalid {label} path")
    path = Path(raw).expanduser().resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ConfigError(f"Pending migration {label} path escapes the project: {path}") from exc
    return path


def _read_json_object(path: Path, *, limit: int, label: str) -> dict[str, Any]:
    payload = _read_bytes(path, limit=limit, label=label)
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"{label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"{label} root must be an object: {path}")
    return value


def _history_directory(config: SteadlithConfig) -> Path:
    database = config.resolve(config.index.database)
    return database.with_name(f"{database.name}.migrations")


def _latest_receipt(config: SteadlithConfig, config_path: Path) -> dict[str, Any]:
    directory = _history_directory(config)
    if not directory.exists():
        raise ConfigError("No completed migration is available to roll back")
    receipts: list[dict[str, Any]] = []
    for path in sorted(directory.glob("migration-*.json")):
        receipt = _read_json_object(path, limit=_JOURNAL_LIMIT, label="Migration receipt")
        if receipt.get("version") != _JOURNAL_VERSION:
            raise ConfigError(f"Unsupported migration receipt version in {path}")
        receipt["_path"] = str(path)
        receipts.append(receipt)
    if not receipts:
        raise ConfigError("No completed migration is available to roll back")
    with SQLiteIndex(config.resolve(config.index.database), readonly=True) as index:
        status = index.status()
    current_sha = _sha256(_read_bytes(config_path, limit=_CONFIG_LIMIT, label="Configuration"))
    matching = [
        receipt
        for receipt in receipts
        if receipt.get("target_generation") == status.generation
        and receipt.get("new_root") == status.corpus_root
        and receipt.get("new_model_id") == status.model_id
        and receipt.get("new_params_hash") == status.params_hash
        and receipt.get("after_sha256") == current_sha
    ]
    if not matching:
        raise ConfigError(
            "The latest index/config state no longer matches a migration receipt; refusing "
            "a stale rollback"
        )
    return max(matching, key=lambda item: str(item.get("applied_at", "")))


def prepare_rollback(
    config_path: str | Path, paths: Sequence[str | Path] = ()
) -> PreparedMigration:
    """Prepare a rollback of the immediately current migration as a new generation."""

    destination = Path(config_path).expanduser().resolve()
    current = load_config(destination)
    before_text = _read_config_text(destination)
    receipt = _latest_receipt(current, destination)
    after_value = receipt.get("before_config")
    if not isinstance(after_value, str):
        raise ConfigError("Migration receipt does not contain a rollback configuration")
    if _sha256(after_value.encode("utf-8")) != receipt.get("before_sha256"):
        raise ConfigError("Migration receipt rollback configuration failed its checksum")
    desired = loads_config(after_value, base_dir=destination.parent, config_path=destination)
    prepared = _make_prepared(
        config_path=destination,
        current=current,
        desired=desired,
        before_text=before_text,
        after_text=after_value,
        paths=paths,
        kind="rollback",
        rollback_of=str(receipt.get("migration_id")),
    )
    expected_root = receipt.get("old_root")
    old_model = receipt.get("old_model_id")
    old_params = receipt.get("old_params_hash")
    if (
        prepared.prepared_index.target_manifest.root_hash != expected_root
        or prepared.prepared_index.model_id != old_model
        or prepared.prepared_index.params_hash != old_params
    ):
        raise ConfigError(
            "Current sources no longer reproduce the pre-migration index. Restore that source "
            "snapshot before rolling back, or create a new forward migration."
        )
    return prepared


def _write_exclusive(path: Path, payload: bytes, *, label: str) -> None:
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    except FileExistsError as exc:
        raise ConfigError(f"{label} already exists: {path}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not write {label.lower()} {path}: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _replace_config(config_path: Path, payload: bytes) -> None:
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            dir=str(config_path.parent), prefix=f".{config_path.name}.migration.", suffix=".tmp"
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, config_path)
    except OSError as exc:
        raise ConfigError(f"Could not atomically publish migrated configuration: {exc}") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _receipt_payload(
    migration: PreparedMigration, *, migration_id: str, receipt: Path
) -> dict[str, object]:
    prepared = migration.prepared_index
    return {
        "version": _JOURNAL_VERSION,
        "migration_id": migration_id,
        "kind": migration.kind,
        "rollback_of": migration.rollback_of,
        "applied_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "config_path": str(migration.config_path),
        "receipt_path": str(receipt),
        "expected_generation": prepared.expected_generation,
        "target_generation": prepared.expected_generation + 1,
        "old_root": prepared.plan.old_root,
        "new_root": prepared.plan.new_root,
        "old_model_id": migration.old_model_id,
        "old_params_hash": migration.old_params_hash,
        "new_model_id": prepared.model_id,
        "new_params_hash": prepared.params_hash,
        "before_sha256": _sha256(migration.before_config.encode("utf-8")),
        "after_sha256": _sha256(migration.after_config.encode("utf-8")),
        "before_config": migration.before_config,
        "after_config": migration.after_config,
        "changes": dict(migration.changes),
    }


def _remove_file(path: Path, *, label: str) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise ConfigError(f"Could not remove completed {label} {path}: {exc}") from exc


def _write_receipt(path: Path, receipt: Mapping[str, object]) -> None:
    payload = (json.dumps(receipt, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )
    if len(payload) > _JOURNAL_LIMIT:
        raise ConfigError("Migration receipt exceeds the safety limit")
    if path.exists():
        existing = _read_bytes(path, limit=_JOURNAL_LIMIT, label="Migration receipt")
        if existing != payload:
            raise ConfigError(f"A conflicting migration receipt already exists: {path}")
        return
    _write_exclusive(path, payload, label="Migration receipt")


def recover_pending_migration(config_path: str | Path) -> RecoveryResult:
    """Finish or abort a journaled migration based on the committed SQLite generation."""

    destination = Path(config_path).expanduser().resolve()
    journal_path = pending_migration_path(destination)
    if not journal_path.exists():
        return RecoveryResult("none")
    journal = _read_json_object(
        journal_path, limit=_JOURNAL_LIMIT, label="Pending migration journal"
    )
    if journal.get("version") != _JOURNAL_VERSION:
        raise ConfigError(f"Unsupported pending migration version in {journal_path}")
    if journal.get("config_path") != str(destination):
        raise ConfigError("Pending migration journal belongs to a different config path")
    receipt_value = journal.get("receipt")
    if not isinstance(receipt_value, dict):
        raise ConfigError("Pending migration does not contain a valid receipt")
    if (
        receipt_value.get("version") != _JOURNAL_VERSION
        or receipt_value.get("migration_id") != journal.get("migration_id")
        or receipt_value.get("config_path") != str(destination)
        or receipt_value.get("receipt_path") != journal.get("receipt_path")
    ):
        raise ConfigError("Pending migration journal and receipt metadata do not agree")
    base = destination.parent
    database = _safe_stored_path(journal.get("database_path"), base=base, label="database")
    receipt_path = _safe_stored_path(journal.get("receipt_path"), base=base, label="receipt")
    migration_id = journal.get("migration_id")
    if not isinstance(migration_id, str) or not migration_id:
        raise ConfigError("Pending migration has an invalid identifier")
    current_payload = _read_bytes(destination, limit=_CONFIG_LIMIT, label="Configuration")
    current_sha = _sha256(current_payload)
    old_sha = receipt_value.get("before_sha256")
    new_sha = receipt_value.get("after_sha256")
    if not isinstance(old_sha, str) or not isinstance(new_sha, str):
        raise ConfigError("Pending migration config checksums are invalid")
    with SQLiteIndex(database, readonly=True) as index:
        status = index.status()
    old_state = (
        status.generation == receipt_value.get("expected_generation")
        and status.corpus_root == receipt_value.get("old_root")
        and status.model_id == receipt_value.get("old_model_id")
        and status.params_hash == receipt_value.get("old_params_hash")
    )
    target_state = (
        status.generation == receipt_value.get("target_generation")
        and status.corpus_root == receipt_value.get("new_root")
        and status.model_id == receipt_value.get("new_model_id")
        and status.params_hash == receipt_value.get("new_params_hash")
    )
    if old_state:
        if current_sha != old_sha:
            raise ConfigError(
                "The index did not commit the migration, but steadlith.toml changed while it was "
                "pending; restore the pre-migration config before recovery"
            )
        _remove_file(journal_path, label="migration journal")
        return RecoveryResult("aborted", migration_id)
    if not target_state:
        raise BackendError(
            "Pending migration cannot be recovered automatically because the index matches "
            "neither its old nor target generation. Preserve the journal and inspect the "
            "database before making further changes."
        )
    if current_sha == old_sha:
        after_config = receipt_value.get("after_config")
        if not isinstance(after_config, str):
            raise ConfigError("Pending migration receipt has no target configuration")
        staged_payload = after_config.encode("utf-8")
        if _sha256(staged_payload) != new_sha or len(staged_payload) > _CONFIG_LIMIT:
            raise ConfigError("Pending migration target configuration failed its checksum")
        try:
            _replace_config(destination, staged_payload)
        except ConfigError as exc:
            raise ConfigError(
                f"Index migration committed, but steadlith.toml could not be finalized: {exc}. "
                "Rerun 'steadlith migrate --recover'."
            ) from exc
    elif current_sha != new_sha:
        raise ConfigError(
            "Index migration committed, but steadlith.toml was edited concurrently. Preserve the "
            "journal, reconcile the config, then rerun migration recovery."
        )
    _write_receipt(receipt_path, receipt_value)
    _remove_file(journal_path, label="migration journal")
    return RecoveryResult("committed", migration_id, receipt_path)


def apply_migration(migration: PreparedMigration) -> MigrationResult:
    """Apply an approved migration and durably synchronize its TOML configuration."""

    if migration.source_scope_overridden:
        raise ConfigError(
            "Migration apply must use the persisted [sources] scope; explicit paths are "
            "preview-only"
        )
    if migration.prepared_index.old_manifest is None:
        raise ConfigError(
            "Migration apply requires an existing index. Update the config before the first "
            "'steadlith index' run instead."
        )
    config_path = migration.config_path
    journal_path = pending_migration_path(config_path)
    if journal_path.exists():
        raise ConfigError(
            f"A pending migration already exists: {journal_path}. Recover it before retrying."
        )
    before_payload = migration.before_config.encode("utf-8")
    current_payload = _read_bytes(config_path, limit=_CONFIG_LIMIT, label="Configuration")
    if _sha256(current_payload) != _sha256(before_payload):
        raise ConfigError("steadlith.toml changed after the migration plan was prepared; re-plan")
    migration_id = uuid.uuid4().hex
    history = _history_directory(migration.current_config)
    receipt_path = (
        history
        / f"migration-{migration.prepared_index.expected_generation + 1:08d}-{migration_id}.json"
    )
    receipt = _receipt_payload(migration, migration_id=migration_id, receipt=receipt_path)
    journal: dict[str, object] = {
        "version": _JOURNAL_VERSION,
        "migration_id": migration_id,
        "config_path": str(config_path),
        "database_path": str(
            migration.current_config.resolve(migration.current_config.index.database)
        ),
        "receipt_path": str(receipt_path),
        "receipt": receipt,
    }
    journal_payload = (
        json.dumps(journal, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
    if len(journal_payload) > _JOURNAL_LIMIT:
        raise ConfigError("Pending migration journal exceeds the safety limit")
    _write_exclusive(journal_path, journal_payload, label="Pending migration journal")
    try:
        applied = apply_prepared(migration.prepared_index)
    except BaseException:
        # If SQLite did not commit, this removes the abandoned journal. If it did
        # commit and only post-commit mirror writing failed, recovery finalizes the
        # matching config so callers never continue with a silently stale TOML file.
        recover_pending_migration(config_path)
        raise
    recovery = recover_pending_migration(config_path)
    if recovery.outcome != "committed" or recovery.receipt is None:
        raise BackendError("Migration applied without a committed recovery receipt")
    return MigrationResult(migration_id, recovery.receipt, applied)
