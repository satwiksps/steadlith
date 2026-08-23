<div align="center">
  <h1 align="center">Steadlith</h1>

  <img src="https://raw.githubusercontent.com/satwiksps/steadlith/main/assets/steadlith-banner.svg" alt="Steadlith" width="100%">

  <p>Steadlith reuses unchanged RAG chunks with content-defined identities, cache-aware planning, and transactional indexing.</p>

  <p>
    <a href="https://steadlith.readthedocs.io/en/latest/">Documentation</a>
    &middot;
    <a href="https://steadlith.readthedocs.io/en/latest/getting-started/quickstart/">Quick start</a>
    &middot;
    <a href="https://steadlith.readthedocs.io/en/latest/cli/">CLI reference</a>
    &middot;
    <a href="https://steadlith.readthedocs.io/en/latest/reference/python-api/">Python API</a>
    &middot;
    <a href="https://steadlith.readthedocs.io/en/latest/architecture/">Architecture</a>
    &middot;
    <a href="CONTRIBUTING.md">Contributing</a>
  </p>

  <p>
    <a href="https://github.com/satwiksps/steadlith/actions/workflows/ci.yml"><img src="https://github.com/satwiksps/steadlith/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="https://steadlith.readthedocs.io/en/latest/"><img src="https://readthedocs.org/projects/steadlith/badge/?version=latest" alt="Documentation"></a>
    <a href="https://codecov.io/gh/satwiksps/steadlith"><img src="https://codecov.io/gh/satwiksps/steadlith/graph/badge.svg" alt="Codecov"></a>
    <a href="https://pypi.org/project/steadlith/"><img src="https://img.shields.io/pypi/v/steadlith?cacheSeconds=300" alt="PyPI"></a>
    <a href="https://pypi.org/project/steadlith/"><img src="https://img.shields.io/pypi/pyversions/steadlith?cacheSeconds=300" alt="Python"></a>
    <a href="LICENSE"><img src="https://img.shields.io/github/license/satwiksps/steadlith" alt="License"></a>
  </p>
</div>

Steadlith is for the engineer responsible for a single-host RAG index over frequently edited documentation or source text. It previews and applies incremental SQLite updates so unchanged chunks can reuse cached embeddings.

The distribution, Python module, and command are all `steadlith`.

The v1 chunk-identity schema is compatibility-stable and protected by golden-vector tests. Built-in five-corpus benchmarks publish churn and retrieval baselines for every bundled chunking strategy. The default offline provider performs lexical retrieval; select a learned provider when queries require semantic similarity.

## At a glance

| Area | Included |
| --- | --- |
| Indexing | Dry-run plans, incremental apply, tombstones, verification, compaction |
| Chunking | Rabin CDC, optional bounded snapping, fixed, recursive, and lexical-semantic comparison strategies |
| Embeddings | Offline feature hashing, OpenAI, and sentence-transformers providers |
| Persistence | Transactional SQLite index and content-addressed SQLite embedding cache |
| Evaluation | Five versioned corpora, 45 deterministic edit cases, and eight retrieval questions |
| Delivery | Typed Python 3.10+ package, Linux/Windows/macOS CI configuration, PyPI packaging workflow, and a standalone CLI |

## Why Steadlith exists

Hashing chunks only avoids work when chunk boundaries stay stable. Offset-based chunkers can shift every downstream boundary after an insertion near the start of a document, which changes hashes even where the underlying text did not change.

Steadlith's default `cdc-rabin` strategy places candidate boundaries from a rolling fingerprint over normalized words. Boundaries far from a local edit should therefore remain stable. A manifest diff then classifies each chunk as `add`, `keep`, `move`, or `delete`. When embedding identity is unchanged, only uncached `add` content needs a new embedding; a model or embedding-parameter migration may re-embed otherwise unchanged occurrences.

Steadlith is deliberately a library and planner, not a RAG framework. The intended integration point is below orchestration libraries and above embedding/vector providers.

## Supported scope

The reference path is implemented end to end:

- deterministic normalized-word chunking with Rabin content-defined boundaries;
- content-addressed chunks and embedding-cache keys;
- manifests, Merkle roots, and explicit change plans;
- a local CLI, SQLite index, and SQLite embedding cache;
- migration with preview, recovery, and rollback;
- versioned churn and retrieval regressions across five built-in corpora;
- cross-platform determinism, adapter, crash-recovery, deletion, and query tests.

Sentence/paragraph snapping remains opt-in because it needs project-specific legal review. The default unsnapped strategy does not depend on it.

## Installation

Steadlith requires Python 3.10 or newer.

Install the latest published release:

```bash
python -m pip install steadlith
```

For an isolated command-line installation, use `pipx`:

```bash
pipx install steadlith
```

For development, install from a source checkout:

```bash
git clone https://github.com/satwiksps/steadlith.git
cd steadlith
python -m venv .venv
```

Activate the environment with `source .venv/bin/activate` on Linux or macOS, or
`.venv\Scripts\Activate.ps1` in Windows PowerShell. Then install:

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Releases are built and published by the tag-triggered workflow described in the [release checklist](https://github.com/satwiksps/steadlith/blob/main/docs/release-checklist.md).

Provider SDKs are optional and do not load with the core package. From a source checkout:

```bash
python -m pip install -e ".[openai]"
python -m pip install -e ".[sentence-transformers]"
```

To uninstall the package:

```bash
python -m pip uninstall steadlith
```

Use `pipx uninstall steadlith` for a pipx installation. Uninstalling does not remove project data. With the default configuration, `steadlith.toml`, `steadlith.toml.migration.json`, and `.steadlith/` can contain configuration, resolved paths, index data, cached embeddings, the manifest mirror, and migration records. If the migration journal exists, run `steadlith migrate --recover` before uninstalling or deleting data. Retain any required backup, then remove only the default files or the custom state paths named by the configuration.

## Quick start

The following example creates a new disposable project, previews its index operations, builds the index, and runs an offline query. It makes no network requests:

```bash
mkdir steadlith-demo
cd steadlith-demo
steadlith init
python -c "from pathlib import Path; Path('docs').mkdir(exist_ok=True); Path('docs/example.md').write_text('# Notes\n\nSteadlith reuses embeddings for unchanged RAG chunks.\n', encoding='utf-8')"
steadlith plan
steadlith index
steadlith status
steadlith query "unchanged RAG chunks"
steadlith verify
```

Every command should exit successfully. `plan` reports one added chunk, one required embedding, and nine tokens. `index` reports one active and one embedded chunk. `status` reports one document and one active chunk. The query's first result is `docs/example.md` with score `0.5774`, and the final line is `Verified: active index and manifest agree.`

With no explicit paths, `steadlith plan` and `index` use the configured `[sources]` globs. `plan` is the safe starting point: it reports proposed adds, keeps, moves, and deletes without writing index state.

> [!CAUTION]
> Paths passed to `plan` or `index` are the complete desired corpus for that run. Previously indexed documents omitted from that scope are planned as deletions. Prefer the committed `[sources]` globs and inspect `steadlith plan` before applying changes. `index` requires `--allow-delete` for a deleting plan and also requires `--allow-empty` before emptying a previously populated corpus.

The generated starter configuration uses deterministic unigram/bigram feature hashing. It works offline for exact-term and keyword retrieval but does not infer synonyms or semantic similarity. Use the OpenAI or sentence-transformers provider when semantic matching is required.

After editing `docs/example.md`, run `steadlith plan` again to see which chunks would be added, kept, moved, or deleted before changing durable state.

See the [CLI reference](https://steadlith.readthedocs.io/en/latest/cli/) before automating a workflow; in particular, positional paths describe a complete desired corpus rather than additions to the existing index.

Programmatic use keeps chunking separate from provider and backend concerns:

```python
from steadlith import CDCChunker
from steadlith.config import load_config

chunker = CDCChunker.from_config(load_config("steadlith.toml"))
chunks = chunker.split("A document that changes a little at a time.")

for chunk in chunks:
    print(chunk.text)
```

`load_config` performs file I/O at the application edge; the chunker receives the parsed object and remains independent of files, providers, and backends.

The documented top-level API and JSON outputs follow the [compatibility policy](https://steadlith.readthedocs.io/en/latest/compatibility/). Versioned chunk identities do not change silently across package releases.

## Configuration

The complete sample is in [`examples/steadlith.toml`](https://github.com/satwiksps/steadlith/blob/main/examples/steadlith.toml). The default strategy is unsnapped Rabin CDC:

```toml
[chunker]
strategy = "cdc-rabin"
window_words = 48
min_tokens = 180
max_tokens = 640
snap_window_words = 24
```

Snapping is enabled only by selecting `strategy = "cdc-rabin+snap"`; it is never enabled by the plain default strategy.

Changing normalization or chunking parameters changes chunk identity. Always run `steadlith plan` before applying a configuration change.

## Providers and deployment scope

| Component | Option | Intended use |
| --- | --- | --- |
| Embeddings | `hash` | Offline exact-term and keyword retrieval with no account or network access |
| Embeddings | `openai` | Hosted semantic embeddings through the official OpenAI API |
| Embeddings | `sentence-transformers` | Local learned embeddings using a compatible sentence-transformers model |
| Index | `sqlite` | Local development, evaluation, and single-host applications |

The SQLite adapter is the supported reference backend. Steadlith does not currently claim remote vector-database support, replication, high availability, or zero-downtime alias swaps. See [backends and providers](https://steadlith.readthedocs.io/en/latest/backends-and-providers/) before selecting a production deployment.

## Reproducible results

Across 45 bundled corpus/edit cases, default `cdc-rabin` reduced the weighted re-embed fraction from **53.3%** with fixed chunks to **32.7%**, while preserving **1.000 recall@5** on eight exact-evidence questions. These are regression results for the bundled fixtures, not a quality or cost forecast for an unrelated corpus.

```bash
steadlith measure churn --json
steadlith measure retrieval --scoring lexical --json
steadlith measure retrieval --scoring hash-embedding --json
```

See [benchmarks](https://steadlith.readthedocs.io/en/latest/benchmarks/) for the complete tables, metrics, fixtures, and interpretation.

## Design constraints

- `chunk` and `content` stay deterministic and free of I/O, network access, and configuration lookups.
- The embedding model is not part of the chunk hash. Embedding cache keys add model and model-parameter identities separately.
- Snapping may inspect only a bounded local window and is never enabled implicitly.
- Deletes become tombstones before compaction so removed content cannot silently remain active.
- Benchmark reporting must publish churn and retrieval-quality results together.

## Documentation

Documentation sources for the current code are in [`docs/`](https://github.com/satwiksps/steadlith/tree/main/docs); the project is configured to publish them at [steadlith.readthedocs.io](https://steadlith.readthedocs.io/).

- [Installation](https://steadlith.readthedocs.io/en/latest/getting-started/installation/): supported Python versions, PyPI, optional providers, source installs, and upgrades.
- [Quick start](https://steadlith.readthedocs.io/en/latest/getting-started/quickstart/): complete offline indexing and query workflow.
- [Configuration](https://steadlith.readthedocs.io/en/latest/getting-started/configuration/): every TOML field, default, constraint, and security boundary.
- [Operations](https://steadlith.readthedocs.io/en/latest/guides/operations/): process model, backups, capacity, compaction, and upgrade procedure.
- [CLI reference](https://steadlith.readthedocs.io/en/latest/cli/): commands, output modes, corpus scope, exit codes, and approvals.
- [Python API](https://steadlith.readthedocs.io/en/latest/reference/python-api/): stable exports and advanced indexing integration.
- [Architecture](https://steadlith.readthedocs.io/en/latest/architecture/): boundaries, identities, manifests, planning, and deletion.
- [Benchmarks](https://steadlith.readthedocs.io/en/latest/benchmarks/): reproducible five-corpus churn and retrieval results.

## Upgrading from Cairn 0.2

Steadlith 0.3 is the same project under a new package, module, command, configuration, and default state name. It preserves the 0.2 wire identities so existing indexes and cached embeddings can be adopted without recomputation.

From the directory that contains the existing configuration, run:

```bash
python -m pip uninstall cairn-rag
python -m pip install steadlith
steadlith adopt --from-config cairn.toml --config steadlith.toml
steadlith plan
```

`adopt` writes a validated Steadlith configuration while preserving the existing state paths and configured embedding identity. It does not overwrite files, follow paths outside the project, or proceed while a migration is pending. Update Python imports from `cairn_rag` to `steadlith` and use the `steadlith` command afterward. New releases are published only as `steadlith`.

## When Steadlith is a fit

Steadlith is aimed at large documents or corpora where edits are small relative to the indexed content. It may offer little advantage for short documents that are normally replaced wholesale, and content-defined boundaries may retrieve differently from semantically selected boundaries. Measure both churn and retrieval quality on your own corpus.

## Contributing and security

Contributions are welcome. Start with [`CONTRIBUTING.md`](https://github.com/satwiksps/steadlith/blob/main/CONTRIBUTING.md), follow the [`CODE_OF_CONDUCT.md`](https://github.com/satwiksps/steadlith/blob/main/CODE_OF_CONDUCT.md), and add tests for behavior changes. Report vulnerabilities privately as described in [`SECURITY.md`](https://github.com/satwiksps/steadlith/blob/main/SECURITY.md).

For usage questions and reproducible bugs, search the [issue tracker](https://github.com/satwiksps/steadlith/issues) before opening a report. Include the Steadlith version, Python version, operating system, configuration with secrets removed, command used, and complete error message. Security reports must remain private.

## License

Steadlith is available under the [Apache License 2.0](https://github.com/satwiksps/steadlith/blob/main/LICENSE).
