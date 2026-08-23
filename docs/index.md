# Steadlith

**Content-defined chunk identities, cache-aware planning, and transactional indexing for RAG corpora that change.**

Steadlith is a Python CLI and library for maintaining local retrieval indexes when source documents change. It assigns content-defined identities to chunks, previews each change, reuses cached embeddings, and publishes the resulting SQLite index update as one transaction.

Use Steadlith when a document corpus changes a little at a time and rebuilding every embedding would repeat work. It is not a RAG framework or a remote vector database.

```{raw} html
<p>
  <a class="md-button md-button--primary" href="getting-started/installation/">Install Steadlith</a>
  <a class="md-button" href="getting-started/quickstart/">Follow the quick start</a>
</p>
```

```console
python -m pip install steadlith
steadlith init
steadlith plan
```

The core workflow is: source files → stable chunk identities → a read-only plan → cached or new embeddings → one transactional SQLite update. Start with `steadlith plan`; it does not contact an embedding provider or write index state.

## What Steadlith provides

```{raw} html
<div class="doc-card-grid">
  <div class="doc-card"><strong><a href="getting-started/quickstart/">Follow the quick start</a></strong><p>Install Steadlith, index a local corpus, query it, and verify the result.</p></div>
  <div class="doc-card"><strong><a href="getting-started/configuration/">Configure a project</a></strong><p>Choose source globs, chunking, embeddings, cache paths, and index paths.</p></div>
  <div class="doc-card"><strong><a href="guides/indexing/">Operate an index safely</a></strong><p>Plan changes, approve deletions, inspect drift, recover migrations, and compact tombstones.</p></div>
  <div class="doc-card"><strong><a href="reference/python-api/">Use the Python API</a></strong><p>Integrate the stable chunker, parameter, chunk, and cache interfaces.</p></div>
</div>
```

- Deterministic Rabin content-defined chunking with stable v1 chunk identities.
- A read-only plan before every index mutation.
- A content-addressed embedding cache keyed by chunk, model, and provider parameters.
- Transactional SQLite index publication with stale-plan detection.
- Tombstones for immediate logical deletion and explicit physical compaction.
- Offline lexical retrieval, an OpenAI adapter, and a local sentence-transformers adapter.
- Status, verification, migration, cache, churn, and retrieval commands.
- Machine-readable JSON for automation.

Steadlith does not claim that every edit changes only a fixed number of chunks. The current TTTD selector is stateful, and an exact counterexample is kept as a regression test. Use the bundled benchmark tools on your own corpus before making cost or quality decisions.

## Choose a path

**First use**
: Read [Installation](getting-started/installation.md), [Quick start](getting-started/quickstart.md), and [Core model](getting-started/core-model.md).

**Running a local index**
: Use [Indexing](guides/indexing.md), [Querying](guides/querying.md), [Verification and recovery](guides/verification-and-recovery.md), and the [CLI reference](cli.md).

**Selecting embeddings**
: Read [Embedding providers](guides/embedding-providers.md) and [Backends and providers](backends-and-providers.md).

**Changing chunking or models**
: Read [Migrations](guides/migrations.md), [Chunk identity](concepts/chunk-identity.md), and [Compatibility](compatibility.md).

**Integrating with Python**
: Start with the [Python API guide](reference/python-api.md) and the generated [API reference](reference/api/chunking.md).

**Contributing**
: Read [Development setup](development/setup.md), [Testing](development/testing.md), [Documentation](development/documentation.md), and [Adapter conformance](adapter-conformance.md).

## Scope

The supported reference deployment is one logical index per local SQLite database. It is suitable for development, evaluation, command-line workflows, and single-host applications after workload-specific testing. Remote vector databases, replicated serving, multi-tenant namespaces, and high-availability orchestration are outside the current implementation.

```{toctree}
:caption: Getting started
:maxdepth: 2
:hidden:

getting-started/installation
getting-started/quickstart
getting-started/core-model
getting-started/configuration
```

```{toctree}
:caption: User guides
:maxdepth: 2
:hidden:

guides/indexing
guides/querying
guides/embedding-providers
guides/cache-management
guides/migrations
guides/verification-and-recovery
guides/operations
```

```{toctree}
:caption: Concepts
:maxdepth: 2
:hidden:

concepts/chunk-identity
concepts/manifests-and-merkle
concepts/planning-and-transactions
concepts/deletion-lifecycle
algorithm
architecture
compatibility
limitations
```

```{toctree}
:caption: Reference
:maxdepth: 2
:hidden:

cli
reference/configuration
reference/json-output
reference/python-api
reference/api/chunking
reference/api/cache
reference/api/configuration
reference/api/indexing
reference/api/models
backends-and-providers
benchmarks
```

```{toctree}
:caption: Development and project
:maxdepth: 2
:hidden:

development/setup
development/testing
development/documentation
adapter-conformance
development/security
development/release-process
release-checklist
changelog
```
