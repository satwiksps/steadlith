# Changelog

All notable changes to Steadlith are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases use
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Reject overlapping configuration, cache, index, SQLite sidecar, manifest, and migration paths before writes can corrupt project state.
- Report invalid UTF-8 configuration files as actionable configuration errors, including JSON CLI errors.
- Reject malformed cache import fields without coercing identities, vectors, or token counts, and bound import line reads.
- Prevent forced cache exports from replacing configuration, databases and sidecars, manifest mirrors, migration journals, or receipts.
- Roll back interrupted cache and index transactions so the same connection can be retried safely.
- Keep related SQLite reads on one committed generation and report malformed stored metadata as verification failures.
- Publish manifest mirrors from the latest committed SQLite state under a writer reservation, preventing delayed operations from restoring an older mirror.
- Account for reusable active vectors when estimating embeddings for copied or renamed chunks after cache pruning.
- Pin OpenAI clients to the official API endpoint so inherited `OPENAI_BASE_URL` values cannot redirect requests or disagree with cache identity.
- Validate migration target size and the exact configuration change before publishing state; accept quoted and padded TOML table and key names.

### Changed

- Exercise installed-wheel indexing, querying, migration, cache, deletion, compaction, and fixture retrieval workflows in pull-request and release CI.
- Activate the contributor virtual environment before installing development dependencies.

## [1.0.0] - 2026-08-22

### Added

- Added versioned Sphinx documentation for installation, configuration, operations, migrations, JSON output, and the public Python API.
- Added a production website smoke check for key routes, metadata, headers, and assets.
- Added regression coverage for CLI contracts, provider responses and retries, cache pruning, compaction boundaries, migration recovery, and installed entry points.

### Changed

- **Breaking:** `status` now reads committed index state without scanning source files. The JSON fields `index_drift`, `source_drift`, `embedding_drift`, `pending_operations`, and `pending_embeddings` were removed; use `plan --json` for source and configuration comparison.
- `Chunk` now rejects non-string content and non-mapping metadata. `CDCParams` now rejects invalid numeric and boolean fields, unknown keys, and non-CDC strategies.
- Source path metadata now uses project-relative document IDs instead of absolute paths. The first 1.0 plan over a 0.3 index can report metadata-only `move` operations; applying them does not re-embed unchanged chunks.
- OpenAI requests use Steadlith's retry loop without additional SDK retries.
- The README and website now provide a complete offline quick start and supported deployment and provider scope. The README also provides benchmark commands and support instructions.
- Removed the unused `embed_with_cache(..., on_batch=...)` callback, duplicate vector serialization code, duplicate website artwork, and an unnecessary development dependency.

### Fixed

- State-path collisions now fail with typed errors that name the blocking path and the configuration key to change.
- Configuration and benchmark choice errors now list the accepted values.
- Manifest mirror verification and repair now detect mismatches and preserve a recoverable state after interrupted writes.
- `compact --dry-run` no longer creates a missing index database.
- Network-consent errors now report the active project configuration instead of assuming the default file.
- The release wheel smoke test now runs in an isolated virtual environment instead of altering the documentation environment.

## [0.3.0] - 2026-08-16

### Added

- `steadlith adopt` for validating a 0.2 configuration and reusing its existing index and embedding cache under the new command.
- Codecov reporting with project and patch coverage checks.
- Repository banner and reusable social card artwork.

### Changed

- Renamed the project, distribution, Python module, command, configuration, and default state directory to Steadlith.
- Preserved the v1 wire identities so existing 0.2 indexes and cached embeddings remain reusable.
- Shortened website metadata and removed decorative arrow symbols from public pages.
- Grouped Dependabot updates and ignored incompatible toolchain majors.
- Updated pinned GitHub Actions and made Codecov upload non-blocking until the renamed repository is activated.

## [0.2.0] - 2026-08-16

### Added

- A compatibility policy that freezes the v1 chunk identity, backed by golden-vector tests.
- Published five-corpus churn and retrieval tables with CI-enforced regression thresholds.
- Direct PyPI installation and package links throughout the website and documentation.

### Changed

- The offline hash provider is documented and tested as lexical unigram/bigram retrieval rather than dismissed as test-only infrastructure.
- Project status now reflects the supported local SQLite workflow while retaining explicit semantic, TTTD-locality, snapping, and remote-provider boundaries.
- Unknown chunk-identity schema versions fail closed instead of being accepted as arbitrary hash inputs.

### Fixed

- Release automation now supplies repository context when creating a GitHub Release from an artifact-only job.

## [0.1.0] - 2026-08-16

### Added

- Deterministic fixed, recursive, semantic, Rabin CDC, and bounded-snap chunking strategies.
- Content-addressed chunk identities, manifests, Merkle roots, and add/keep/move/delete plans.
- SQLite embedding cache and transactional SQLite index with tombstones, verification, and compaction.
- Offline hash embeddings plus optional OpenAI and sentence-transformers providers.
- CLI workflows for initialization, planning, indexing, querying, migration, measurement, and cache management.
- Migration preview, apply, recovery, and rollback with durable receipts and stale-state protection.
- Reproducible churn and retrieval measurement fixtures.
- Next.js project website and automated Python/package/website release workflows.

### Security

- Source discovery confines inputs to the configured project root and excludes project-managed state.
- Destructive index operations require explicit deletion and empty-corpus consent.
- Network providers and unsigned cache imports require explicit trust flags.

### Known limitations

- Package APIs were initially published without a compatibility guarantee.
- The current stateful TTTD fallback does not provide a strict fixed-margin locality guarantee.
- The bundled hash embedder is deterministic test infrastructure, not a production retrieval model.
- Post-anchor structural snapping is experimental and requires project-specific legal review before use.

[Unreleased]: https://github.com/satwiksps/steadlith/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/satwiksps/steadlith/compare/v0.3.0...v1.0.0
[0.3.0]: https://github.com/satwiksps/steadlith/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/satwiksps/steadlith/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/satwiksps/steadlith/releases/tag/v0.1.0
