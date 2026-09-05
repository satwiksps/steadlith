from __future__ import annotations

import json
from pathlib import Path

import pytest

from steadlith.cli import main
from steadlith.config import load_config, pending_migration_path
from steadlith.errors import BackendError, ConfigError, ExitCode
from steadlith.index import service as index_service
from steadlith.index.service import index_status, prepare_index, verify_index
from steadlith.migrate import (
    apply_migration,
    prepare_migration,
    prepare_rollback,
    recover_pending_migration,
    workflow,
)


def _project(tmp_path: Path) -> Path:
    config = tmp_path / "steadlith.toml"
    config.write_text(
        """\
[chunker]
strategy = "cdc-rabin"
window_words = 4
min_tokens = 8
max_tokens = 24
snap_window_words = 4
primary_mask_bits = 3
backup_mask_bits = 2

[embedding]
provider = "hash"
model = "test-hash-v1"
dimensions = 32
batch_size = 4

[store]
cache = ".steadlith/cache.sqlite3"

[index]
backend = "sqlite"
database = ".steadlith/index.sqlite3"

[sources]
include = ["docs/*.md"]
exclude = []
""",
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(
        " ".join(f"migration-word-{index}" for index in range(180)), encoding="utf-8"
    )
    assert main(["index", "--config", str(config), "--json"]) == ExitCode.SUCCESS
    return config


def _leave_committed_migration_pending(config_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    migration = prepare_migration(config_path, overrides={"embedding.model": "test-hash-v2"})
    real_recovery = workflow.recover_pending_migration

    def interrupted_recovery(_path: str | Path) -> workflow.RecoveryResult:
        raise ConfigError("simulated process interruption before config publication")

    monkeypatch.setattr(workflow, "recover_pending_migration", interrupted_recovery)
    with pytest.raises(ConfigError, match="simulated process interruption"):
        apply_migration(migration)
    monkeypatch.setattr(workflow, "recover_pending_migration", real_recovery)
    journal = pending_migration_path(config_path)
    assert journal.exists()
    return journal


def test_model_migration_persists_config_history_and_rolls_back(tmp_path: Path) -> None:
    config_path = _project(tmp_path)
    before_text = config_path.read_text(encoding="utf-8")
    before_status = index_status(load_config(config_path))

    assert (
        main(
            [
                "migrate",
                "--embedding-model",
                "test-hash-v2",
                "--config",
                str(config_path),
                "--json",
            ]
        )
        == ExitCode.SUCCESS
    )
    assert config_path.read_text(encoding="utf-8") == before_text
    assert index_status(load_config(config_path)).generation == before_status.generation

    assert (
        main(
            [
                "migrate",
                "--embedding-model",
                "test-hash-v2",
                "--apply",
                "--config",
                str(config_path),
                "--json",
            ]
        )
        == ExitCode.CONFIG_ERROR
    )
    assert (
        main(
            [
                "migrate",
                "--embedding-model",
                "test-hash-v2",
                "--apply",
                "--allow-delete",
                "--config",
                str(config_path),
                "--json",
            ]
        )
        == ExitCode.SUCCESS
    )
    migrated = load_config(config_path)
    migrated_status = index_status(migrated)
    assert migrated.embedding.model == "test-hash-v2"
    assert migrated_status.model_id == "hash:test-hash-v2"
    assert migrated_status.generation == before_status.generation + 1
    assert migrated_status.tombstoned_chunks == before_status.active_chunks
    assert verify_index(migrated) == (True, ())
    receipts = list((tmp_path / ".steadlith/index.sqlite3.migrations").glob("migration-*.json"))
    assert len(receipts) == 1
    assert not pending_migration_path(config_path).exists()

    assert (
        main(
            [
                "migrate",
                "--rollback",
                "--apply",
                "--allow-delete",
                "--config",
                str(config_path),
                "--json",
            ]
        )
        == ExitCode.SUCCESS
    )
    rolled_back = load_config(config_path)
    rollback_status = index_status(rolled_back)
    assert config_path.read_text(encoding="utf-8") == before_text
    assert rolled_back.embedding.model == "test-hash-v1"
    assert rollback_status.model_id == "hash:test-hash-v1"
    assert rollback_status.generation == migrated_status.generation + 1
    assert rollback_status.tombstoned_chunks == before_status.active_chunks * 2
    assert verify_index(rolled_back) == (True, ())
    assert (
        len(list((tmp_path / ".steadlith/index.sqlite3.migrations").glob("migration-*.json"))) == 2
    )


def test_chunker_migration_requires_delete_confirmation_and_persists_parameters(
    tmp_path: Path,
) -> None:
    config_path = _project(tmp_path)
    before = index_status(load_config(config_path))

    arguments = [
        "migrate",
        "--max-tokens",
        "20",
        "--apply",
        "--config",
        str(config_path),
        "--json",
    ]
    assert main(arguments) == ExitCode.CONFIG_ERROR
    assert load_config(config_path).chunker.max_tokens == 24
    assert index_status(load_config(config_path)).generation == before.generation

    arguments.insert(4, "--allow-delete")
    assert main(arguments) == ExitCode.SUCCESS
    migrated = load_config(config_path)
    assert migrated.chunker.max_tokens == 20
    assert index_status(migrated).generation == before.generation + 1
    assert index_status(migrated).tombstoned_chunks > 0
    assert verify_index(migrated) == (True, ())


def test_apply_rejects_preview_only_positional_source_scope(tmp_path: Path) -> None:
    config_path = _project(tmp_path)
    source = tmp_path / "docs/guide.md"
    assert (
        main(
            [
                "migrate",
                str(source),
                "--embedding-model",
                "test-hash-v2",
                "--apply",
                "--config",
                str(config_path),
            ]
        )
        == ExitCode.CONFIG_ERROR
    )
    assert load_config(config_path).embedding.model == "test-hash-v1"


def test_programmatic_apply_rejects_preview_only_source_scope(tmp_path: Path) -> None:
    config_path = _project(tmp_path)
    migration = prepare_migration(
        config_path,
        paths=(tmp_path / "docs/guide.md",),
        overrides={"embedding.model": "test-hash-v2"},
    )

    assert migration.source_scope_overridden
    with pytest.raises(ConfigError, match=r"persisted \[sources\] scope"):
        apply_migration(migration)
    assert load_config(config_path).embedding.model == "test-hash-v1"


def test_migration_excludes_active_config_and_receipts_from_broad_sources(
    tmp_path: Path,
) -> None:
    config_path = _project(tmp_path)
    broad = config_path.read_text(encoding="utf-8").replace(
        'include = ["docs/*.md"]',
        'include = ["**/*.md", "**/*.toml", "**/*.json"]',
    )
    config_path.write_text(broad, encoding="utf-8")

    assert (
        main(
            [
                "migrate",
                "--embedding-model",
                "test-hash-v2",
                "--apply",
                "--allow-delete",
                "--config",
                str(config_path),
                "--json",
            ]
        )
        == ExitCode.SUCCESS
    )
    migrated = load_config(config_path)
    assert not prepare_index(migrated).plan.changed

    assert (
        main(
            [
                "migrate",
                "--rollback",
                "--apply",
                "--allow-delete",
                "--config",
                str(config_path),
                "--json",
            ]
        )
        == ExitCode.SUCCESS
    )
    assert not prepare_index(load_config(config_path)).plan.changed


def test_preview_has_no_logical_writes_or_provider_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _project(tmp_path)
    config = load_config(config_path)
    before_config = config_path.read_bytes()
    before_status = index_status(config)
    before_verify = verify_index(config)

    def fail_if_provider_created(_config: object) -> object:
        raise AssertionError("migration preview constructed an embedding provider")

    monkeypatch.setattr(index_service, "create_provider", fail_if_provider_created)

    assert (
        main(
            [
                "migrate",
                "--embedding-model",
                "test-hash-v2",
                "--dry-run",
                "--config",
                str(config_path),
                "--json",
            ]
        )
        == ExitCode.SUCCESS
    )
    assert config_path.read_bytes() == before_config
    assert index_status(load_config(config_path)) == before_status
    assert verify_index(load_config(config_path)) == before_verify
    assert not pending_migration_path(config_path).exists()
    assert not (tmp_path / ".steadlith/index.sqlite3.migrations").exists()


def test_recovery_finishes_config_after_committed_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _project(tmp_path)
    migration = prepare_migration(config_path, overrides={"embedding.model": "test-hash-v2"})
    real_recovery = workflow.recover_pending_migration

    def interrupted_recovery(_path: str | Path) -> workflow.RecoveryResult:
        raise ConfigError("simulated process interruption before config publication")

    monkeypatch.setattr(workflow, "recover_pending_migration", interrupted_recovery)
    with pytest.raises(ConfigError, match="simulated process interruption"):
        apply_migration(migration)
    assert pending_migration_path(config_path).exists()
    with pytest.raises(ConfigError, match="pending migration journal"):
        load_config(config_path)
    assert main(["init", "--force", "--config", str(config_path)]) == ExitCode.CONFIG_ERROR
    assert pending_migration_path(config_path).exists()
    assert (
        main(
            [
                "migrate",
                "--embedding-model",
                "ignored-until-recovery",
                "--config",
                str(config_path),
            ]
        )
        == ExitCode.CONFIG_ERROR
    )
    assert pending_migration_path(config_path).exists()

    monkeypatch.setattr(workflow, "recover_pending_migration", real_recovery)
    recovery = recover_pending_migration(config_path)
    assert recovery.outcome == "committed"
    assert load_config(config_path).embedding.model == "test-hash-v2"
    assert verify_index(load_config(config_path)) == (True, ())


def test_recovery_rejects_tampered_journal_receipt_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _project(tmp_path)
    journal = _leave_committed_migration_pending(config_path, monkeypatch)
    payload = json.loads(journal.read_text(encoding="utf-8"))
    payload["receipt"]["migration_id"] = "tampered-migration-id"
    journal.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConfigError, match="journal and receipt metadata do not agree"):
        recover_pending_migration(config_path)

    assert journal.exists()
    with pytest.raises(ConfigError, match="pending migration journal"):
        load_config(config_path)


def test_recovery_preserves_journal_after_concurrent_config_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _project(tmp_path)
    indexed_config = load_config(config_path)
    before_generation = index_status(indexed_config).generation
    journal = _leave_committed_migration_pending(config_path, monkeypatch)
    concurrent_text = config_path.read_text(encoding="utf-8") + "\n# concurrent operator edit\n"
    config_path.write_text(concurrent_text, encoding="utf-8")

    with pytest.raises(ConfigError, match="edited concurrently"):
        recover_pending_migration(config_path)

    assert index_status(indexed_config).generation == before_generation + 1
    assert config_path.read_text(encoding="utf-8") == concurrent_text
    assert journal.exists()


def test_recover_rejects_all_planning_options(tmp_path: Path) -> None:
    config_path = _project(tmp_path)
    assert (
        main(
            [
                "migrate",
                "--recover",
                "--embedding-model",
                "must-not-be-ignored",
                "--config",
                str(config_path),
            ]
        )
        == ExitCode.CONFIG_ERROR
    )
    assert load_config(config_path).embedding.model == "test-hash-v1"


def test_failed_apply_clears_journal_and_keeps_original_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _project(tmp_path)
    before_text = config_path.read_text(encoding="utf-8")
    before_status = index_status(load_config(config_path))
    migration = prepare_migration(config_path, overrides={"embedding.model": "test-hash-v2"})

    def fail_before_commit(_prepared: object) -> object:
        raise BackendError("simulated pre-commit failure")

    monkeypatch.setattr(workflow, "apply_prepared", fail_before_commit)
    with pytest.raises(BackendError, match="pre-commit failure"):
        apply_migration(migration)
    assert not pending_migration_path(config_path).exists()
    assert config_path.read_text(encoding="utf-8") == before_text
    assert index_status(load_config(config_path)) == before_status


def test_migration_mirror_failure_is_repaired_by_a_noop_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _project(tmp_path)
    migration = prepare_migration(config_path, overrides={"chunker.max_tokens": 20})
    write_snapshot = index_service._write_manifest_snapshot

    def fail_snapshot(*args: object, **kwargs: object) -> None:
        raise BackendError("simulated mirror publication failure")

    monkeypatch.setattr(index_service, "_write_manifest_snapshot", fail_snapshot)
    with pytest.raises(BackendError, match="simulated mirror publication failure"):
        apply_migration(migration)

    migrated = load_config(config_path)
    assert migrated.chunker.max_tokens == 20
    assert not pending_migration_path(config_path).exists()
    valid, issues = verify_index(migrated)
    assert not valid
    assert any("manifest mirror" in issue.lower() for issue in issues)

    monkeypatch.setattr(index_service, "_write_manifest_snapshot", write_snapshot)
    assert main(["index", "--config", str(config_path), "--json"]) == ExitCode.SUCCESS
    assert verify_index(migrated) == (True, ())


def test_rollback_refuses_sources_that_no_longer_reproduce_old_root(tmp_path: Path) -> None:
    config_path = _project(tmp_path)
    migration = prepare_migration(config_path, overrides={"embedding.model": "test-hash-v2"})
    apply_migration(migration)
    (tmp_path / "docs/guide.md").write_text("source changed after migration", encoding="utf-8")

    with pytest.raises(ConfigError, match="no longer reproduce"):
        prepare_rollback(config_path)


def test_migration_rejects_oversized_target_before_writing_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _project(tmp_path)
    before_config = config_path.read_bytes()
    before_status = index_status(load_config(config_path))
    monkeypatch.setattr(workflow, "_CONFIG_LIMIT", len(before_config) + 32)

    with pytest.raises(ConfigError, match="Target configuration exceeds"):
        migration = prepare_migration(
            config_path, overrides={"embedding.model": "a-long-model-name-" * 20}
        )
        apply_migration(migration)

    assert config_path.read_bytes() == before_config
    assert index_status(load_config(config_path)) == before_status
    assert not pending_migration_path(config_path).exists()
    assert not (tmp_path / ".steadlith/index.sqlite3.migrations").exists()


@pytest.mark.parametrize(
    ("table", "key"),
    [("[ embedding ]", "model"), ('["embedding"]', '"model"'), ("['embedding']", "'model'")],
)
def test_migration_accepts_quoted_and_padded_toml_names(
    tmp_path: Path, table: str, key: str
) -> None:
    config_path = _project(tmp_path)
    original = config_path.read_text(encoding="utf-8").replace("[embedding]", table)
    original = original.replace('model = "test-hash-v1"', f'{key} = "test-hash-v1"')
    original += "\n# Preserve this operator note during migration.\n"
    config_path.write_text(original, encoding="utf-8")
    original_bytes = config_path.read_bytes()

    apply_migration(prepare_migration(config_path, overrides={"embedding.model": "test-hash-v2"}))

    assert load_config(config_path).embedding.model == "test-hash-v2"
    assert "# Preserve this operator note" in config_path.read_text(encoding="utf-8")
    assert verify_index(load_config(config_path)) == (True, ())

    apply_migration(prepare_rollback(config_path))

    assert config_path.read_bytes() == original_bytes
    assert verify_index(load_config(config_path)) == (True, ())


def test_migration_rejects_editing_a_table_name_inside_a_multiline_string(tmp_path: Path) -> None:
    config_path = _project(tmp_path)
    original = config_path.read_text(encoding="utf-8")
    # Place the real chunker table after a table-looking line inside a model label.
    chunker, remaining = original.split("[embedding]", 1)
    original = "[embedding]" + remaining + "\n" + chunker
    original = original.replace(
        'model = "test-hash-v1"', "model = '''model label\n[chunker]\nmax_tokens = 24\n'''"
    )
    config_path.write_text(original, encoding="utf-8")
    before_config = config_path.read_bytes()
    before_status = index_status(load_config(config_path))

    with pytest.raises(ConfigError, match="could not safely update"):
        prepare_migration(config_path, overrides={"chunker.max_tokens": 20})

    assert config_path.read_bytes() == before_config
    assert index_status(load_config(config_path)) == before_status
    assert not pending_migration_path(config_path).exists()
