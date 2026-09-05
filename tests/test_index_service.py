from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from steadlith.config import (
    ChunkerConfig,
    EmbeddingConfig,
    IndexConfig,
    SourcesConfig,
    SteadlithConfig,
    StoreConfig,
)
from steadlith.errors import BackendError, ConfigError
from steadlith.index.plan import OperationKind
from steadlith.index.service import (
    apply_prepared,
    compact_index,
    index_status,
    prepare_index,
    query_index,
    verify_index,
)


def _config(tmp_path: Path) -> SteadlithConfig:
    return SteadlithConfig(
        chunker=ChunkerConfig(
            strategy="cdc-rabin",
            window_words=4,
            min_tokens=8,
            max_tokens=24,
            snap_window_words=4,
            primary_mask_bits=3,
            backup_mask_bits=2,
        ),
        embedding=EmbeddingConfig(
            provider="hash",
            model="test-hash-v1",
            dimensions=32,
            batch_size=3,
        ),
        store=StoreConfig(cache=".steadlith/cache.sqlite3"),
        index=IndexConfig(database=".steadlith/index.sqlite3"),
        sources=SourcesConfig(include=("docs/*.md",), exclude=()),
        base_dir=tmp_path,
    ).validate()


def test_plan_apply_edit_delete_verify_and_compact(tmp_path: Path) -> None:
    config = _config(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "guide.md"
    words = [f"term-{index}" for index in range(180)]
    source.write_text(" ".join(words), encoding="utf-8")

    first = prepare_index(config)
    assert first.plan.counts[OperationKind.ADD] == first.plan.new_chunks
    assert not config.resolve(config.index.database).exists()
    assert not config.resolve(config.store.cache).exists()

    applied = apply_prepared(first)
    assert applied.active_chunks == first.plan.new_chunks
    assert applied.embedded_chunks > 0
    assert config.resolve(config.index.database).exists()
    assert config.resolve(config.store.cache).exists()
    assert config.resolve(config.index.database).with_name("index.sqlite3.manifest.json").exists()
    assert verify_index(config) == (True, ())

    generation = index_status(config).generation
    no_op = prepare_index(config)
    assert not no_op.plan.requires_apply
    no_op_result = apply_prepared(no_op)
    assert no_op_result.embedded_chunks == 0
    assert index_status(config).generation == generation

    revised = words[:80] + ["inserted-alpha", "inserted-beta"] + words[80:]
    source.write_text(" ".join(revised), encoding="utf-8")
    second = prepare_index(config)
    assert second.plan.counts[OperationKind.MOVE] > 0
    assert second.plan.cost.chunks_to_embed < second.plan.new_chunks
    edited = apply_prepared(second)
    assert edited.active_chunks == second.plan.new_chunks
    assert verify_index(config) == (True, ())

    source.unlink()
    deletion = prepare_index(config)
    assert deletion.plan.new_chunks == 0
    deleted = apply_prepared(deletion)
    assert deleted.active_chunks == 0
    assert index_status(config).active_chunks == 0
    assert index_status(config).tombstoned_chunks > 0
    assert verify_index(config) == (True, ())

    tombstones_before_restore = index_status(config).tombstoned_chunks
    source.write_text(" ".join(revised), encoding="utf-8")
    restored = apply_prepared(prepare_index(config))
    assert restored.active_chunks > 0
    assert restored.cache_hits > 0
    assert index_status(config).tombstoned_chunks == tombstones_before_restore
    assert verify_index(config) == (True, ())

    source.unlink()
    apply_prepared(prepare_index(config))
    assert compact_index(config) > 0
    assert index_status(config).tombstoned_chunks == 0


def test_explicit_project_scope_never_indexes_steadlith_state(tmp_path: Path) -> None:
    config = replace(
        _config(tmp_path),
        store=StoreConfig(cache="cache.sqlite3"),
        index=IndexConfig(database="index.sqlite3"),
        sources=SourcesConfig(
            include=("**/*.md", "**/*.sql"),
            exclude=(),
        ),
    ).validate()
    (tmp_path / "guide.md").write_text(
        " ".join(f"stable-{index}" for index in range(80)), encoding="utf-8"
    )
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "001-create.sql").write_text(
        "CREATE TABLE indexed_user_migration (id INTEGER PRIMARY KEY);", encoding="utf-8"
    )

    first = prepare_index(config, paths=(".",))
    apply_prepared(first)
    second = prepare_index(config, paths=(".",))

    assert tuple(second.target_manifest.documents) == ("guide.md", "migrations/001-create.sql")
    assert not second.plan.changed
    assert not second.plan.requires_apply


@pytest.mark.parametrize("move", [False, True], ids=["copy", "rename"])
def test_plan_matches_active_vector_reuse_after_cache_pruning(tmp_path: Path, move: bool) -> None:
    from steadlith.store import Cache

    config = _config(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "original.md"
    source.write_text("existing reusable content", encoding="utf-8")
    apply_prepared(prepare_index(config))
    with Cache(config.resolve(config.store.cache)) as cache:
        assert cache.prune(max_entries=0) == 1
    if move:
        source.rename(docs / "new.md")
    else:
        (docs / "new.md").write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    prepared = prepare_index(config)
    assert prepared.plan.counts[OperationKind.ADD] == 1
    assert prepared.plan.cost.chunks_to_embed == 0
    assert prepared.plan.cost.tokens_to_embed == 0
    applied = apply_prepared(prepared)
    assert applied.embedded_chunks == 0
    assert applied.cache_hits == 0
    assert verify_index(config) == (True, ())
    assert query_index(config, "reusable")[0].document_id in {"docs/new.md", "docs/original.md"}


def test_stale_prepared_plan_cannot_overwrite_newer_state(tmp_path: Path) -> None:
    config = _config(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(" ".join(f"word-{i}" for i in range(80)), encoding="utf-8")
    first = prepare_index(config)
    stale = prepare_index(config)
    apply_prepared(first)
    with pytest.raises(BackendError, match="changed after this plan"):
        apply_prepared(stale)


def test_preparation_keeps_manifest_and_generation_in_one_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from steadlith.index.adapters import SQLiteIndex

    config = _config(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("stable source content", encoding="utf-8")
    apply_prepared(prepare_index(config))
    migration = prepare_index(config.with_embedding(model="changed-model"))
    get_manifest = SQLiteIndex.get_manifest_payload
    published = False

    def publish_after_manifest(index: SQLiteIndex) -> object:
        nonlocal published
        payload = get_manifest(index)
        if not published:
            published = True
            apply_prepared(migration)
        return payload

    monkeypatch.setattr(SQLiteIndex, "get_manifest_payload", publish_after_manifest)
    prepared = prepare_index(config)
    assert published
    assert prepared.expected_generation == 1
    assert index_status(config).generation == 2
    with pytest.raises(BackendError, match="fresh plan"):
        apply_prepared(prepared)


def test_query_embeds_against_active_index_identity(tmp_path: Path) -> None:
    config = _config(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "guide.md"
    source.write_text(
        " ".join([*("ordinary" for _ in range(80)), "quartz", "needle", "quartz"]),
        encoding="utf-8",
    )
    apply_prepared(prepare_index(config))

    matches = query_index(config, "quartz needle", limit=2)
    assert matches
    assert all(match.document_id == "docs/guide.md" for match in matches)
    assert "quartz needle" in matches[0].text

    changed = config.with_embedding(model="different-hash-model")
    with pytest.raises(ConfigError, match="does not match the active index"):
        query_index(changed, "quartz needle")


def test_query_rejects_missing_empty_or_invalid_input(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with pytest.raises(BackendError, match="has not been built"):
        query_index(config, "hello")
    with pytest.raises(ConfigError, match="non-empty"):
        query_index(config, "  ")
    with pytest.raises(ConfigError, match="positive"):
        query_index(config, "hello", limit=0)


def test_compact_dry_run_does_not_create_an_unbuilt_index(tmp_path: Path) -> None:
    config = _config(tmp_path)
    database = config.resolve(config.index.database)

    assert not database.exists()
    assert compact_index(config, dry_run=True) == 0
    assert not database.exists()


def test_service_validates_programmatic_configuration(tmp_path: Path) -> None:
    invalid = SteadlithConfig(
        store=StoreConfig(cache=".steadlith/shared.sqlite3"),
        index=IndexConfig(database=".steadlith/shared.sqlite3"),
        base_dir=tmp_path,
    )
    with pytest.raises(ConfigError, match="must use different files"):
        prepare_index(invalid)


def test_stale_same_root_model_migration_cannot_overwrite_newer_state(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(" ".join(f"word-{i}" for i in range(80)), encoding="utf-8")
    apply_prepared(prepare_index(config))

    model_two = prepare_index(config, embedding_model="test-hash-v2", force_embed_all=True)
    stale_model_three = prepare_index(config, embedding_model="test-hash-v3", force_embed_all=True)
    assert model_two.plan.old_root == model_two.plan.new_root
    assert stale_model_three.plan.old_root == stale_model_three.plan.new_root
    assert model_two.expected_generation == stale_model_three.expected_generation

    apply_prepared(model_two)
    with pytest.raises(BackendError, match="changed after this plan"):
        apply_prepared(stale_model_three)

    status = index_status(config)
    assert status.model_id == "hash:test-hash-v2"
    assert status.generation == model_two.expected_generation + 1
    assert status.tombstoned_chunks == status.active_chunks
    assert verify_index(config) == (True, ())


def test_manifest_failure_is_detected_and_noop_retry_repairs_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "guide.md"
    source.write_text(" ".join(f"word-{i}" for i in range(80)), encoding="utf-8")
    apply_prepared(prepare_index(config))

    source.write_text(" ".join(f"changed-{i}" for i in range(80)), encoding="utf-8")
    update = prepare_index(config)
    from steadlith.index import service

    write_snapshot = service._write_manifest_snapshot

    def fail_snapshot(*args: object, **kwargs: object) -> None:
        raise BackendError("simulated mirror publication failure")

    monkeypatch.setattr(service, "_write_manifest_snapshot", fail_snapshot)
    with pytest.raises(BackendError, match="simulated mirror publication failure"):
        apply_prepared(update)
    assert index_status(config).generation == update.expected_generation + 1

    monkeypatch.setattr(service, "_write_manifest_snapshot", write_snapshot)
    valid, issues = verify_index(config)
    assert not valid
    assert any("manifest mirror" in issue.lower() for issue in issues)

    retry = prepare_index(config)
    assert not retry.plan.requires_apply
    apply_prepared(retry)
    assert verify_index(config) == (True, ())
