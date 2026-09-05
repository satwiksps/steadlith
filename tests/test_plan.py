from __future__ import annotations

import pytest

from steadlith.content.manifest import CorpusManifest, DocumentManifest
from steadlith.index.plan import OperationKind, create_plan, diff_document
from steadlith.models import ChunkRecord


def _record(name: str, start: int, end: int, tokens: int = 5) -> ChunkRecord:
    return ChunkRecord(
        chunk_hash=name,
        start_offset=start,
        end_offset=end,
        token_count=tokens,
    )


def _document(*chunks: ChunkRecord) -> DocumentManifest:
    return DocumentManifest(document_id="doc", chunks=chunks)


def _corpus(document: DocumentManifest) -> CorpusManifest:
    return CorpusManifest(documents={"doc": document})


def test_section_reordering_is_all_moves_and_no_embeddings() -> None:
    old_document = _document(_record("a", 0, 10), _record("b", 10, 20))
    new_document = _document(_record("b", 0, 10), _record("a", 10, 20))
    operations = diff_document(old_document, new_document, document_id="doc")
    assert {operation.kind for operation in operations} == {OperationKind.MOVE}
    plan = create_plan(_corpus(old_document), _corpus(new_document))
    assert plan.cost.chunks_to_embed == 0


def test_duplicate_occurrences_are_matched_as_a_multiset() -> None:
    old_document = _document(_record("same", 0, 10), _record("same", 10, 20))
    new_document = _document(_record("same", 0, 10))
    operations = diff_document(old_document, new_document, document_id="doc")
    assert [operation.kind for operation in operations].count(OperationKind.KEEP) == 1
    assert [operation.kind for operation in operations].count(OperationKind.DELETE) == 1


def test_cost_estimate_counts_unique_uncached_additions() -> None:
    old = _corpus(_document(_record("a", 0, 10)))
    new = _corpus(
        _document(
            _record("a", 0, 10),
            _record("new", 10, 20, tokens=12),
            _record("new", 20, 30, tokens=12),
        )
    )
    uncached = create_plan(old, new, price_per_million_tokens=2.0)
    assert uncached.cost.chunks_to_embed == 1
    assert uncached.cost.tokens_to_embed == 12
    assert uncached.cost.estimated_cost == 0.000024
    cached = create_plan(old, new, is_cached=lambda value: value == "new")
    assert cached.cost.cache_hits == 1
    assert cached.cost.chunks_to_embed == 0


def test_cache_state_is_consulted_once_per_unique_candidate() -> None:
    new = _corpus(_document(_record("one", 0, 10), _record("two", 10, 20)))
    calls: list[str] = []

    plan = create_plan(None, new, is_cached=lambda chunk_hash: not calls.append(chunk_hash))

    assert sorted(calls) == ["one", "two"]
    assert plan.cost.cache_hits == 2


def test_new_occurrences_reuse_active_content_before_consulting_cache() -> None:
    old = _corpus(_document(_record("same", 0, 10)))
    new = _corpus(_document(_record("same", 0, 10), _record("same", 10, 20)))
    cache_queries: list[str] = []

    def cache_miss(chunk_hash: str) -> bool:
        cache_queries.append(chunk_hash)
        return False

    plan = create_plan(old, new, is_cached=cache_miss, price_per_million_tokens=2.0)
    assert plan.counts[OperationKind.ADD] == 1
    assert plan.cost.chunks_to_embed == 0
    assert plan.cost.tokens_to_embed == 0
    assert plan.cost.estimated_cost == 0.0
    assert plan.cost.cache_hits == 0
    assert cache_queries == []

    migration = create_plan(old, new, is_cached=cache_miss, embed_all=True)
    assert migration.cost.chunks_to_embed == 1
    assert cache_queries == ["same"]


@pytest.mark.parametrize("price", [-1.0, float("nan"), float("inf")])
def test_cost_estimate_rejects_invalid_prices(price: float) -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        create_plan(None, _corpus(_document(_record("new", 0, 10))), price_per_million_tokens=price)


def test_cost_estimate_preserves_an_unknown_provider_price() -> None:
    new = _corpus(_document(_record("new", 0, 10, tokens=3)))
    plan = create_plan(None, new)

    assert plan.cost.tokens_to_embed == 3
    assert plan.cost.estimated_cost is None
    assert plan.cost.naive_estimated_cost is None
    assert plan.cost.as_dict()["estimated_cost"] is None


def test_metadata_only_change_requires_apply_even_when_merkle_root_is_unchanged() -> None:
    record = _record("same", 0, 10)
    old = CorpusManifest({"doc": DocumentManifest("doc", (record,), metadata={"acl": "private"})})
    new = CorpusManifest({"doc": DocumentManifest("doc", (record,), metadata={"acl": "public"})})

    plan = create_plan(old, new)

    assert plan.old_root == plan.new_root
    assert plan.changed
    assert plan.requires_apply
    assert plan.counts[OperationKind.MOVE] == 1


def test_identical_manifest_is_a_true_no_op_but_model_migration_is_not() -> None:
    manifest = _corpus(_document(_record("same", 0, 10)))

    assert not create_plan(manifest, manifest).changed
    assert create_plan(manifest, manifest, embed_all=True).changed
