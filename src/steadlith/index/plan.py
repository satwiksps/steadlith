"""Pure manifest diffing and embedding-cost planning."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from steadlith.content.manifest import CorpusManifest, DocumentManifest
from steadlith.models import ChunkRecord


class OperationKind(str, Enum):
    ADD = "add"
    KEEP = "keep"
    MOVE = "move"
    DELETE = "delete"


@dataclass(frozen=True)
class PlanOperation:
    kind: OperationKind
    document_id: str
    chunk_hash: str
    old_position: int | None = None
    new_position: int | None = None
    old_chunk: ChunkRecord | None = None
    new_chunk: ChunkRecord | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "document_id": self.document_id,
            "chunk_hash": self.chunk_hash,
            "old_position": self.old_position,
            "new_position": self.new_position,
            "old_offsets": (
                [self.old_chunk.start_offset, self.old_chunk.end_offset]
                if self.old_chunk is not None
                else None
            ),
            "new_offsets": (
                [self.new_chunk.start_offset, self.new_chunk.end_offset]
                if self.new_chunk is not None
                else None
            ),
        }


@dataclass(frozen=True)
class CostEstimate:
    cache_hits: int
    chunks_to_embed: int
    tokens_to_embed: int
    estimated_cost: float | None
    naive_chunks_to_embed: int
    naive_tokens_to_embed: int
    naive_estimated_cost: float | None

    @property
    def avoided_embeddings(self) -> int:
        return max(0, self.naive_chunks_to_embed - self.chunks_to_embed)

    def as_dict(self) -> dict[str, int | float | None]:
        return {
            "cache_hits": self.cache_hits,
            "chunks_to_embed": self.chunks_to_embed,
            "tokens_to_embed": self.tokens_to_embed,
            "estimated_cost": self.estimated_cost,
            "naive_chunks_to_embed": self.naive_chunks_to_embed,
            "naive_tokens_to_embed": self.naive_tokens_to_embed,
            "naive_estimated_cost": self.naive_estimated_cost,
            "avoided_embeddings": self.avoided_embeddings,
        }


@dataclass(frozen=True)
class IndexPlan:
    old_root: str | None
    new_root: str
    operations: tuple[PlanOperation, ...]
    cost: CostEstimate
    old_chunks: int
    new_chunks: int
    requires_apply: bool

    @property
    def changed(self) -> bool:
        return self.requires_apply

    @property
    def counts(self) -> Mapping[OperationKind, int]:
        counter = Counter(operation.kind for operation in self.operations)
        return {kind: counter.get(kind, 0) for kind in OperationKind}

    def as_dict(self, *, include_operations: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "old_root": self.old_root,
            "new_root": self.new_root,
            "changed": self.changed,
            "requires_apply": self.requires_apply,
            "old_chunks": self.old_chunks,
            "new_chunks": self.new_chunks,
            "counts": {kind.value: count for kind, count in self.counts.items()},
            "cost": self.cost.as_dict(),
        }
        if include_operations:
            payload["operations"] = [operation.as_dict() for operation in self.operations]
        return payload


def _record_key(record: ChunkRecord) -> tuple[str, int, int]:
    return record.chunk_hash, record.start_offset, record.end_offset


def _canonical_metadata(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def diff_document(
    old: DocumentManifest | None, new: DocumentManifest | None, *, document_id: str
) -> tuple[PlanOperation, ...]:
    """Diff occurrences, preserving duplicates and distinguishing moves from adds."""

    old_chunks = old.chunks if old is not None else ()
    new_chunks = new.chunks if new is not None else ()
    unmatched_old = set(range(len(old_chunks)))
    unmatched_new = set(range(len(new_chunks)))
    matches: list[PlanOperation] = []
    document_metadata_changed = (
        old is not None
        and new is not None
        and _canonical_metadata(old.metadata) != _canonical_metadata(new.metadata)
    )

    # Prefer exact hash+offset matches so duplicate boilerplate does not turn into
    # arbitrary moves. Deques keep matching deterministic when all fields repeat.
    exact: dict[tuple[str, int, int], deque[int]] = defaultdict(deque)
    for position, record in enumerate(old_chunks):
        exact[_record_key(record)].append(position)
    for new_position, record in enumerate(new_chunks):
        queue = exact.get(_record_key(record))
        if queue:
            old_position = queue.popleft()
            unmatched_old.discard(old_position)
            unmatched_new.discard(new_position)
            previous = old_chunks[old_position]
            record_metadata_changed = (
                previous.token_count != record.token_count
                or _canonical_metadata(previous.metadata) != _canonical_metadata(record.metadata)
            )
            matches.append(
                PlanOperation(
                    kind=(
                        OperationKind.MOVE
                        if document_metadata_changed or record_metadata_changed
                        else OperationKind.KEEP
                    ),
                    document_id=document_id,
                    chunk_hash=record.chunk_hash,
                    old_position=old_position,
                    new_position=new_position,
                    old_chunk=old_chunks[old_position],
                    new_chunk=record,
                )
            )

    # Remaining occurrences with the same content hash are metadata-only moves.
    by_hash: dict[str, deque[int]] = defaultdict(deque)
    for old_position in sorted(unmatched_old):
        by_hash[old_chunks[old_position].chunk_hash].append(old_position)
    for new_position in sorted(tuple(unmatched_new)):
        record = new_chunks[new_position]
        queue = by_hash.get(record.chunk_hash)
        if queue:
            old_position = queue.popleft()
            unmatched_old.discard(old_position)
            unmatched_new.discard(new_position)
            matches.append(
                PlanOperation(
                    kind=OperationKind.MOVE,
                    document_id=document_id,
                    chunk_hash=record.chunk_hash,
                    old_position=old_position,
                    new_position=new_position,
                    old_chunk=old_chunks[old_position],
                    new_chunk=record,
                )
            )

    for new_position in sorted(unmatched_new):
        record = new_chunks[new_position]
        matches.append(
            PlanOperation(
                kind=OperationKind.ADD,
                document_id=document_id,
                chunk_hash=record.chunk_hash,
                new_position=new_position,
                new_chunk=record,
            )
        )
    for old_position in sorted(unmatched_old):
        record = old_chunks[old_position]
        matches.append(
            PlanOperation(
                kind=OperationKind.DELETE,
                document_id=document_id,
                chunk_hash=record.chunk_hash,
                old_position=old_position,
                old_chunk=record,
            )
        )
    order = {
        OperationKind.KEEP: 0,
        OperationKind.MOVE: 1,
        OperationKind.ADD: 2,
        OperationKind.DELETE: 3,
    }
    return tuple(
        sorted(
            matches,
            key=lambda item: (
                order[item.kind],
                item.new_position if item.new_position is not None else 1 << 60,
                item.old_position if item.old_position is not None else 1 << 60,
                item.chunk_hash,
            ),
        )
    )


def _all_records(manifest: CorpusManifest | None) -> Iterable[ChunkRecord]:
    if manifest is None:
        return ()
    return (
        chunk
        for document_id in sorted(manifest.documents)
        for chunk in manifest.documents[document_id].chunks
    )


def create_plan(
    old: CorpusManifest | None,
    new: CorpusManifest,
    *,
    is_cached: Callable[[str], bool] | None = None,
    price_per_million_tokens: float | None = None,
    embed_all: bool = False,
) -> IndexPlan:
    """Create a write-free corpus plan and cache-aware price estimate."""

    if price_per_million_tokens is not None and (
        isinstance(price_per_million_tokens, bool)
        or not isinstance(price_per_million_tokens, (int, float))
        or not math.isfinite(price_per_million_tokens)
        or price_per_million_tokens < 0
    ):
        raise ValueError("price_per_million_tokens must be finite and non-negative")

    operations: list[PlanOperation] = []
    old_documents = old.documents if old is not None else {}
    for document_id in sorted(set(old_documents) | set(new.documents)):
        operations.extend(
            diff_document(
                old_documents.get(document_id),
                new.documents.get(document_id),
                document_id=document_id,
            )
        )

    if embed_all:
        candidates = list(_all_records(new))
    else:
        reusable_hashes = {record.chunk_hash for record in _all_records(old)}
        candidates = [
            operation.new_chunk
            for operation in operations
            if operation.kind is OperationKind.ADD
            and operation.new_chunk is not None
            and operation.chunk_hash not in reusable_hashes
        ]
    # A content-addressed embedding is paid once even if a chunk occurs repeatedly.
    unique_candidates: dict[str, ChunkRecord] = {}
    for record in candidates:
        unique_candidates.setdefault(record.chunk_hash, record)
    cached = is_cached or (lambda _chunk_hash: False)
    cache_state = {chunk_hash: cached(chunk_hash) for chunk_hash in unique_candidates}
    cache_hits = sum(cache_state.values())
    missing = [
        record for chunk_hash, record in unique_candidates.items() if not cache_state[chunk_hash]
    ]
    tokens = sum(record.token_count for record in missing)

    changed_documents = {
        operation.document_id
        for operation in operations
        if operation.kind is not OperationKind.KEEP
    }
    if embed_all:
        naive_records = list(_all_records(new))
    else:
        naive_records = [
            chunk
            for document_id in sorted(changed_documents)
            if document_id in new.documents
            for chunk in new.documents[document_id].chunks
        ]
    naive_tokens = sum(record.token_count for record in naive_records)
    multiplier = (
        price_per_million_tokens / 1_000_000 if price_per_million_tokens is not None else None
    )
    old_count = sum(len(document.chunks) for document in old_documents.values())
    new_count = sum(len(document.chunks) for document in new.documents.values())
    manifests_differ = old is None or old.to_json() != new.to_json()
    return IndexPlan(
        old_root=old.root_hash if old is not None else None,
        new_root=new.root_hash,
        operations=tuple(operations),
        cost=CostEstimate(
            cache_hits=cache_hits,
            chunks_to_embed=len(missing),
            tokens_to_embed=tokens,
            estimated_cost=tokens * multiplier if multiplier is not None else None,
            naive_chunks_to_embed=len(naive_records),
            naive_tokens_to_embed=naive_tokens,
            naive_estimated_cost=naive_tokens * multiplier if multiplier is not None else None,
        ),
        old_chunks=old_count,
        new_chunks=new_count,
        requires_apply=embed_all or manifests_differ,
    )
