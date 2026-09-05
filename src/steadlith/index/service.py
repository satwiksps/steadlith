"""High-level, testable orchestration around the pure Steadlith core."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from steadlith.chunk.cdc import CDCChunker
from steadlith.chunk.params import CDCParams
from steadlith.chunk.strategies import FixedChunker, RecursiveChunker, SemanticChunker
from steadlith.config import SteadlithConfig, pending_migration_path
from steadlith.content.manifest import CorpusManifest, DocumentManifest, manifest_from_chunks
from steadlith.embed import (
    EmbeddingInput,
    create_provider,
    embed_texts,
    embed_with_cache,
    embedding_identity,
)
from steadlith.errors import BackendError, ConfigError, ProviderError
from steadlith.index.adapters import (
    DocumentState,
    IndexRecord,
    IndexStatus,
    SQLiteIndex,
    VectorMatch,
)
from steadlith.index.apply import resolve_identities
from steadlith.index.plan import IndexPlan, create_plan
from steadlith.index.sources import SourceDocument, discover_sources, load_sources
from steadlith.models import Chunk, is_hard_cut
from steadlith.store import Cache


class Chunker(Protocol):
    def split(self, text: str, metadata: Mapping[str, Any] | None = None) -> Sequence[Chunk]: ...


@dataclass(frozen=True)
class BuiltDocument:
    source: SourceDocument
    chunks: tuple[Chunk, ...]
    manifest: DocumentManifest
    hard_cuts: int


@dataclass(frozen=True)
class PreparedIndex:
    config: SteadlithConfig
    expected_generation: int
    old_manifest: CorpusManifest | None
    target_manifest: CorpusManifest
    documents: Mapping[str, BuiltDocument]
    plan: IndexPlan
    model_id: str
    params_hash: str
    embed_all: bool


@dataclass(frozen=True)
class ApplyResult:
    plan: IndexPlan
    active_chunks: int
    tombstoned_chunks: int
    cache_hits: int
    embedded_chunks: int
    embedded_tokens: int

    def as_dict(self) -> dict[str, object]:
        return {
            "plan": self.plan.as_dict(include_operations=False),
            "active_chunks": self.active_chunks,
            "tombstoned_chunks": self.tombstoned_chunks,
            "cache_hits": self.cache_hits,
            "embedded_chunks": self.embedded_chunks,
            "embedded_tokens": self.embedded_tokens,
        }


def create_chunker(config: SteadlithConfig) -> Chunker:
    config.validate()
    chunker = config.chunker
    if chunker.strategy in {"cdc-rabin", "cdc-rabin+snap"}:
        return CDCChunker(
            CDCParams(
                window_words=chunker.window_words,
                min_tokens=chunker.min_tokens,
                max_tokens=chunker.max_tokens,
                primary_mask_bits=chunker.primary_mask_bits,
                backup_mask_bits=chunker.backup_mask_bits,
                snap_window_words=chunker.snap_window_words,
                snap_to_boundaries=chunker.strategy.endswith("+snap"),
            )
        )
    if chunker.strategy == "fixed":
        return FixedChunker(
            chunk_size_tokens=chunker.max_tokens,
            overlap_tokens=0,
        )
    if chunker.strategy == "recursive":
        return RecursiveChunker(
            min_tokens=chunker.min_tokens,
            max_tokens=chunker.max_tokens,
        )
    if chunker.strategy == "semantic":
        return SemanticChunker(
            min_tokens=chunker.min_tokens,
            max_tokens=chunker.max_tokens,
        )
    raise AssertionError(f"validated strategy is unsupported: {chunker.strategy}")


def _hard_cut_count(chunks: Sequence[Chunk]) -> int:
    return sum(is_hard_cut(chunk.metadata) for chunk in chunks)


def _protected_state(config: SteadlithConfig) -> tuple[set[Path], set[Path]]:
    """Return Steadlith-managed paths that must never become corpus sources."""

    database = config.resolve(config.index.database)
    cache = config.resolve(config.store.cache)
    files = {
        database,
        cache,
        database.with_name(f"{database.name}.manifest.json"),
        *(
            Path(f"{path}{suffix}")
            for path in (database, cache)
            for suffix in ("-wal", "-shm", "-journal")
        ),
    }
    if config.config_path is not None:
        files.add(config.config_path.expanduser().resolve())
        files.add(pending_migration_path(config.config_path))
    return files, {database.with_name(f"{database.name}.migrations")}


def _is_protected_source(path: Path, files: set[Path], directories: set[Path]) -> bool:
    resolved = path.resolve()
    if resolved in files:
        return True
    for directory in directories:
        try:
            resolved.relative_to(directory)
            return True
        except ValueError:
            continue
    return False


def build_target_manifest(
    config: SteadlithConfig, paths: Sequence[str | Path] = ()
) -> tuple[CorpusManifest, Mapping[str, BuiltDocument]]:
    config.validate()
    discovered = discover_sources(
        base_dir=config.base_dir,
        inputs=paths,
        includes=config.sources.include,
        excludes=config.sources.exclude,
    )
    protected_files, protected_directories = _protected_state(config)
    discovered = tuple(
        path
        for path in discovered
        if not _is_protected_source(path, protected_files, protected_directories)
    )
    sources = load_sources(discovered, base_dir=config.base_dir)
    chunker = create_chunker(config)
    built: dict[str, BuiltDocument] = {}
    manifests: dict[str, DocumentManifest] = {}
    for source in sources:
        chunks = tuple(chunker.split(source.text, metadata=source.metadata))
        manifest = manifest_from_chunks(
            source.document_id,
            chunks,
            metadata=source.metadata,
        )
        item = BuiltDocument(
            source=source,
            chunks=chunks,
            manifest=manifest,
            hard_cuts=_hard_cut_count(chunks),
        )
        built[source.document_id] = item
        manifests[source.document_id] = manifest
    target = CorpusManifest(
        documents=manifests,
        metadata={
            "chunker_strategy": config.chunker.strategy,
            "chunker": {
                "window_words": config.chunker.window_words,
                "min_tokens": config.chunker.min_tokens,
                "max_tokens": config.chunker.max_tokens,
                "snap_window_words": config.chunker.snap_window_words,
                "primary_mask_bits": config.chunker.primary_mask_bits,
                "backup_mask_bits": config.chunker.backup_mask_bits,
            },
        },
    )
    return target, built


def _old_manifest(index: SQLiteIndex) -> CorpusManifest | None:
    payload = index.get_manifest_payload()
    if payload is None:
        return None
    try:
        return CorpusManifest.from_dict(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise BackendError(f"Stored manifest is invalid: {exc}") from exc


def prepare_index(
    config: SteadlithConfig,
    paths: Sequence[str | Path] = (),
    *,
    chunker_strategy: str | None = None,
    embedding_model: str | None = None,
    force_embed_all: bool = False,
) -> PreparedIndex:
    """Read sources and durable state, then return a plan without writing anything."""

    effective = config.validate()
    if chunker_strategy is not None:
        effective = effective.with_chunker(strategy=chunker_strategy)
    if embedding_model is not None:
        effective = effective.with_embedding(model=embedding_model)
    target, documents = build_target_manifest(effective, paths)
    model_id, params_hash = embedding_identity(effective.embedding)
    with (
        SQLiteIndex(effective.resolve(effective.index.database), readonly=True) as index,
        index.read_snapshot(),
    ):
        old = _old_manifest(index)
        status = index.status()
    model_changed = status.model_id is not None and (
        status.model_id != model_id or status.params_hash != params_hash
    )
    embed_all = force_embed_all or model_changed
    with Cache(effective.resolve(effective.store.cache), readonly=True) as cache:
        plan = create_plan(
            old,
            target,
            is_cached=lambda chunk_hash: cache.contains(chunk_hash, model_id, params_hash),
            price_per_million_tokens=effective.embedding.price_per_million_tokens,
            embed_all=embed_all,
        )
    return PreparedIndex(
        config=effective,
        expected_generation=status.generation,
        old_manifest=old,
        target_manifest=target,
        documents=documents,
        plan=plan,
        model_id=model_id,
        params_hash=params_hash,
        embed_all=embed_all,
    )


def _write_manifest_snapshot(config: SteadlithConfig) -> None:
    """Mirror the authoritative SQLite manifest as diffable JSON after commit."""

    database = config.resolve(config.index.database)
    destination = database.with_name(f"{database.name}.manifest.json")
    temporary: str | None = None
    try:
        # An older apply may finish after a newer generation has committed. Read
        # the current manifest and prevent commits until its mirror is published.
        with SQLiteIndex(database) as index, index.read_snapshot(block_writers=True):
            payload = index.get_manifest_payload()
            if payload is None:
                raise BackendError("The authoritative SQLite manifest is absent")
            serialized = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            descriptor, temporary = tempfile.mkstemp(
                dir=str(destination.parent), prefix="manifest.", suffix=".tmp"
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
    except (OSError, TypeError, ValueError, BackendError) as exc:
        raise BackendError(
            "The index committed, but its diffable manifest mirror could not be written: "
            f"{exc}. Rerun 'steadlith index' with the same configuration and source scope to "
            "repair the mirror."
        ) from exc
    finally:
        if temporary is not None and os.path.exists(temporary):
            try:
                os.unlink(temporary)
            except OSError:
                pass


def apply_prepared(prepared: PreparedIndex) -> ApplyResult:
    """Embed missing content, then atomically publish the target index snapshot."""

    config = prepared.config.validate()
    with SQLiteIndex(config.resolve(config.index.database)) as index:
        status = index.status()
        if (
            status.generation != prepared.expected_generation
            or status.corpus_root != prepared.plan.old_root
        ):
            raise BackendError(
                "Index state changed after this plan was prepared; prepare a fresh plan and retry"
            )
        if not prepared.plan.requires_apply:
            _write_manifest_snapshot(config)
            return ApplyResult(
                plan=prepared.plan,
                active_chunks=status.active_chunks,
                tombstoned_chunks=0,
                cache_hits=0,
                embedded_chunks=0,
                embedded_tokens=0,
            )
        current = index.active_records()
        same_model = (
            status.model_id == prepared.model_id and status.params_hash == prepared.params_hash
        )
        identities = resolve_identities(
            prepared.plan,
            current if same_model else (),
            identity_scope=(
                f"{prepared.model_id}\0{prepared.params_hash}\0"
                f"generation:{prepared.expected_generation + 1}"
            ),
        )
        vectors: dict[str, tuple[float, ...]] = {}
        if same_model:
            for record in current:
                if len(record.vector) != config.embedding.dimensions:
                    raise BackendError(
                        f"Active vector {record.instance_id!r} has {len(record.vector)} "
                        f"dimensions; configuration requires {config.embedding.dimensions}"
                    )
                vectors.setdefault(record.chunk_hash, tuple(record.vector))

        unique_chunks: dict[str, Chunk] = {}
        for document_id in sorted(prepared.documents):
            for chunk in prepared.documents[document_id].chunks:
                unique_chunks.setdefault(chunk.chunk_hash, chunk)
        missing_inputs = [
            EmbeddingInput(
                chunk_hash=chunk_hash,
                text=chunk.text,
                token_count=chunk.token_count,
            )
            for chunk_hash, chunk in unique_chunks.items()
            if chunk_hash not in vectors
        ]
        cache_hits = 0
        embedded_chunks = 0
        embedded_tokens = 0
        if missing_inputs:
            provider = create_provider(config.embedding)
            if (
                provider.model_id != prepared.model_id
                or provider.params_hash != prepared.params_hash
            ):
                raise RuntimeError("embedding identity changed while applying the prepared plan")
            with Cache(config.resolve(config.store.cache)) as cache:
                batch = embed_with_cache(
                    missing_inputs,
                    cache=cache,
                    provider=provider,
                    batch_size=config.embedding.batch_size,
                )
            vectors.update(batch.vectors)
            cache_hits = batch.cache_hits
            embedded_chunks = batch.embedded
            embedded_tokens = batch.embedded_tokens

        records: list[IndexRecord] = []
        document_states: list[DocumentState] = []
        for document_id in sorted(prepared.documents):
            built = prepared.documents[document_id]
            document_states.append(
                DocumentState(
                    document_id=document_id,
                    root_hash=built.manifest.root_hash,
                    chunk_count=len(built.chunks),
                    hard_cuts=built.hard_cuts,
                    metadata=built.source.metadata,
                )
            )
            for position, chunk in enumerate(built.chunks):
                identity = identities[(document_id, position)]
                records.append(
                    IndexRecord(
                        instance_id=identity.instance_id,
                        document_id=document_id,
                        position=position,
                        chunk_hash=chunk.chunk_hash,
                        text=chunk.text,
                        start_offset=chunk.start_offset,
                        end_offset=chunk.end_offset,
                        token_count=chunk.token_count,
                        metadata={**built.source.metadata, **dict(chunk.metadata)},
                        vector=vectors[chunk.chunk_hash],
                    )
                )
        payload = prepared.target_manifest.to_dict()
        active, tombstoned_now = index.apply_snapshot(
            records=records,
            documents=document_states,
            manifest_payload=payload,
            corpus_root=prepared.target_manifest.root_hash,
            model_id=prepared.model_id,
            params_hash=prepared.params_hash,
            vector_dimensions=config.embedding.dimensions,
            expected_generation=prepared.expected_generation,
            check_root=True,
            expected_root=prepared.plan.old_root,
        )
    _write_manifest_snapshot(config)
    return ApplyResult(
        plan=prepared.plan,
        active_chunks=active,
        tombstoned_chunks=tombstoned_now,
        cache_hits=cache_hits,
        embedded_chunks=embedded_chunks,
        embedded_tokens=embedded_tokens,
    )


def index_status(config: SteadlithConfig) -> IndexStatus:
    config.validate()
    with SQLiteIndex(config.resolve(config.index.database), readonly=True) as index:
        return index.status()


def query_index(
    config: SteadlithConfig,
    text: str,
    *,
    limit: int = 5,
) -> tuple[VectorMatch, ...]:
    """Embed a query with the indexed model and return ranked active chunks."""

    config.validate()
    if not isinstance(text, str) or not text.strip():
        raise ConfigError("Query text must be a non-empty string")
    if type(limit) is not int or limit <= 0:
        raise ConfigError("Query limit must be a positive integer")

    model_id, params_hash = embedding_identity(config.embedding)
    database = config.resolve(config.index.database)
    with SQLiteIndex(database, readonly=True) as index:
        status = index.status()
    if status.corpus_root is None:
        raise BackendError("Index does not exist or has not been built; run 'steadlith index'")
    if status.active_chunks == 0:
        raise BackendError("Index contains no active chunks")
    if status.model_id != model_id or status.params_hash != params_hash:
        raise ConfigError(
            "Configured embedding identity does not match the active index; "
            "run 'steadlith plan' and 'steadlith index' before querying"
        )

    provider = create_provider(config.embedding)
    if provider.model_id != model_id or provider.params_hash != params_hash:
        raise ProviderError("Embedding provider identity differs from the validated configuration")
    query_vector = embed_texts((text,), provider=provider)[0]
    with SQLiteIndex(database, readonly=True) as index:
        return tuple(
            index.query(
                query_vector,
                limit=limit,
                expected_model_id=model_id,
                expected_params_hash=params_hash,
            )
        )


def compact_index(
    config: SteadlithConfig, *, before: str | None = None, dry_run: bool = False
) -> int:
    config.validate()
    database = config.resolve(config.index.database)
    if dry_run and not database.exists():
        with SQLiteIndex(database, readonly=True):
            pass
        return 0
    with SQLiteIndex(database) as index:
        return index.compact(before=before, dry_run=dry_run)


def verify_index(config: SteadlithConfig) -> tuple[bool, tuple[str, ...]]:
    config.validate()
    database = config.resolve(config.index.database)
    with SQLiteIndex(database, readonly=True) as index, index.read_snapshot():
        _, problems = index.verify()
        payload = index.get_manifest_payload()
    if payload is None:
        return not problems, problems

    mirror_path = database.with_name(f"{database.name}.manifest.json")
    mirror_problems = list(problems)
    try:
        mirror = json.loads(mirror_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        mirror_problems.append(f"manifest mirror is missing: {mirror_path}")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        mirror_problems.append(f"manifest mirror is unreadable: {exc}")
    else:
        if mirror != payload:
            mirror_problems.append("manifest mirror differs from the authoritative SQLite manifest")
    return not mirror_problems, tuple(mirror_problems)
