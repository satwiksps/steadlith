# CLI reference

The installed executable is `steadlith`. The default configuration path is `steadlith.toml`; paths inside that file are resolved relative to the file's directory.

```text
steadlith --version
steadlith <command> --help
```

Commands that use project state accept `-c PATH`/`--config PATH`. Reporting commands accept `--json` where shown below. Put these options after the relevant command (or after a `cache` leaf command).

## Corpus scope and deletion semantics

For `plan`, `index`, and migration previews, positional paths are the **complete desired corpus for that run**. They are not appended to the existing index. Migration applies, including rollback apply, reject positional paths: an applied migration always uses the `[sources]` scope that remains in `steadlith.toml`, preventing an immediately drifting index.

- With no positional paths, Steadlith discovers files from `[sources].include` in `steadlith.toml`.
- A positional file is read directly; a positional directory is searched recursively.
- `[sources].exclude` and the supported text-file suffix allowlist apply to discovered inputs.
- Relative paths are resolved from the configuration directory.
- Every explicit path, glob match, and resolved symlink target must remain inside the configuration directory. Absolute paths and `..`-escaping source patterns are rejected.
- Any previously indexed document absent from the resulting source set is planned as `delete`.

Steadlith unconditionally removes its active configuration, configured cache/index files and SQLite sidecars, manifest mirror, pending migration journal, and database-namespaced migration-receipt directory from every discovered corpus. This protection also applies to explicit `.` scopes and state directories outside `.steadlith`; an unrelated project directory named `migrations/` remains ordinary source content.

`plan` does not write index/cache state or call an embedding provider. Inspect it before `index`, especially when changing globs or passing an explicit subset. Applying an empty desired corpus tombstones every currently active document; physical removal happens only through `compact`.

## Project commands

### `init`

```text
steadlith init [-c PATH] [--json] [--force]
```

Writes a commented offline starter configuration. It refuses to replace an existing file unless `--force` is supplied. Replacing the config does not delete the index or cache files it previously referenced.

### `adopt`

```text
steadlith adopt --from-config cairn.toml --config steadlith.toml [--json]
```

Adopts a Cairn 0.2 configuration without recomputing its existing index or embedding cache. It validates and writes a normalized Steadlith configuration while preserving the configured state paths and embedding model identity. It refuses paths outside the project, an existing output file, or a source with a pending migration. Adoption is explicit and never runs through automatic file detection.

### `plan`

```text
steadlith plan [-c PATH] [--json] [PATH ...]
```

Builds the desired manifest, compares it with durable state, consults the cache, and reports `add`, `keep`, `move`, and `delete` operations plus embedding/token/cost estimates. An unknown configured price remains unknown.

### `index`

```text
steadlith index [-c PATH] [--json] [--allow-network]
  [--allow-delete] [--allow-empty] [PATH ...]
```

Prepares the same plan, resolves cache hits, embeds missing content, and applies the target snapshot in one SQLite index transaction. Removed occurrences become inactive tombstones. Completed provider batches are committed to the cache before index publication so a later run can reuse them.

Networked providers require `--allow-network`; this covers OpenAI requests and model downloads by sentence-transformers. Any plan containing deletions requires `--allow-delete`. Making a previously non-empty corpus completely empty additionally requires `--allow-empty`. These confirmations apply only to that invocation and do not change the configuration.

### `status`

```text
steadlith status [-c PATH] [--json]
```

Reports the committed corpus root, model identity, active/tombstoned counts, and hard-cut diagnostics. JSON output also includes the state generation and embedding-parameters hash. The command does not read configured sources. Run `steadlith plan` to compare current sources or embedding configuration with the committed index.

### `query`

```text
steadlith query [-c PATH] [--json] [-k NUMBER] [--allow-network] TEXT
```

Embeds the query with the configured provider and returns the highest-scoring active chunks from the SQLite index, including source identifiers, offsets, text, and scores. The default limit is 5. Steadlith refuses to query an unbuilt or empty index, or an index whose committed embedding identity differs from the current configuration.

Networked providers require `--allow-network`. The built-in hash provider supports offline exact-term and keyword retrieval, but it does not infer synonyms or semantic similarity.

### `verify`

```text
steadlith verify [-c PATH] [--json]
```

Checks the authoritative SQLite manifest and active records for consistency, then requires the JSON mirror to exist, parse, and equal that manifest. A mismatch is reported with exit code 2.

### `compact`

```text
steadlith compact [-c PATH] [--json] [--dry-run] [--before TIMESTAMP]
```

Physically removes tombstoned vector rows whose `valid_to` is at or before the cutoff. Without `--before`, the cutoff is the current time. A supplied timestamp must be ISO 8601 and include a timezone offset, for example `2026-08-15T12:00:00+05:30` or `2026-08-15T06:30:00Z`; Steadlith compares it in UTC.

`--dry-run` reports the eligible row count without deleting rows. Compaction removes index rollback evidence; it does not prune the separate embedding cache.

### `migrate`

```text
steadlith migrate [-c PATH] [--json] [PATH ...] \
  [--chunker STRATEGY] [--window-words N] [--min-tokens N]
  [--max-tokens N] [--snap-window-words N]
  [--primary-mask-bits N] [--backup-mask-bits N]
  [--embedding-provider PROVIDER] [--embedding-model MODEL]
  [--embedding-dimensions N] [--dry-run | --apply]

steadlith migrate [-c PATH] [--json] --rollback [--apply]
steadlith migrate [-c PATH] [--json] --recover
```

Supported chunker values are `cdc-rabin`, `cdc-rabin+snap`, `fixed`, `recursive`, and `semantic`; embedding providers are `hash`, `openai`, and `sentence-transformers`. The command reports both the configuration delta and the normal add/keep/move/delete and embedding-cost plan. With neither `--apply` nor `--dry-run`, it defaults to a preview that does not change configuration, cache entries, index rows/generation, or receipts and does not construct an embedding provider.

`--apply` is the explicit approval boundary. A networked provider also needs `--allow-network`; plans with tombstones need `--allow-delete`; an empty target additionally needs `--allow-empty`. Changing or rolling back an embedding identity tombstones the prior active vector lifecycle even when every manifest operation is `keep`, so it also requires `--allow-delete`. Apply requires an existing index and uses only the persisted `[sources]` scope.

An approved migration embeds through the normal resumable cache and publishes one generation through the SQLite transaction and generation check. It then atomically replaces `steadlith.toml`, preserving unrelated comments and settings, and writes a checksummed receipt to the database-namespaced `<database>.migrations/` folder. Old active rows become tombstones rather than being overwritten.

An fsynced recovery journal beside `steadlith.toml` closes the SQLite/config process-crash window. While a journal exists, ordinary CLI commands and library config loads fail closed without changing it; run `migrate --recover` explicitly. Recovery finalizes config when the target generation committed or clears the stage when SQLite remained at the old generation. It refuses to guess if the database, journal, or config was edited into a third state.

`--rollback` previews the inverse of the immediately current migration receipt. With `--apply`, rollback creates a new generation and tombstones the migrated rows; it does not rewrite database history. Rollback is refused after another index generation or config edit, and the current sources must reproduce the receipt's pre-migration corpus root. Restore the corresponding source revision first when they do not. Provider calls may be needed if the old embedding cache was pruned.

## Measurements

The measurement commands use versioned deterministic fixtures and do not read project configuration. The published results are reproducible regression evidence for those fixtures, not a forecast for every private corpus; see [benchmarks](benchmarks.md).

### `measure churn`

```text
steadlith measure churn [--json] [--strategy NAME] [--corpus NAME]
  [--edit NAME] [--price USD_PER_MILLION_TOKENS] [--price-label LABEL]
```

Repeat `--strategy`, `--corpus`, or `--edit` to select several values; omitting a selector runs all built-in choices. Strategy names are `fixed`, `recursive`, `semantic-lexical-proxy`, `cdc-rabin`, and `cdc-rabin+snap`. Corpus names and edit operations are enumerated by `--help`.

The CLI default price (`0.02`) is explicitly illustrative and may not match any current provider. Pass a verified, current price and a traceable `--price-label` for meaningful estimates.

### `measure retrieval`

```text
steadlith measure retrieval [--json] [--strategy NAME] [--corpus NAME]
  [--scoring lexical|hash-embedding] [-k NUMBER]
```

Reports recall and nDCG over the versioned fixture gold questions. The default scorer is lexical and the default `k` is 5. `hash-embedding` exercises the same offline lexical feature-hashing provider used by the starter configuration; neither scorer measures synonym or semantic understanding.

## Embedding cache commands

The cache key is `(chunk_hash, model_id, embedding_parameters_hash)`. These commands operate on the cache configured by `[store].cache`.

### `cache stats`

```text
steadlith cache stats [-c PATH] [--json]
```

Reports entry, distinct chunk/model, and stored vector-byte counts. A missing cache reports zero counts.

### `cache prune`

```text
steadlith cache prune [-c PATH] [--json]
  [--max-age-days DAYS] [--max-entries COUNT]
```

At least one limit is required and both must be non-negative. Age pruning removes entries older than the UTC cutoff by last-access time; the entry cap then removes the least-recently accessed overflow. This command has no dry-run mode.

### `cache export`

```text
steadlith cache export [-c PATH] [--json] [--force] DESTINATION
```

Writes all cache entries as deterministic JSON Lines through an atomic temporary file. It refuses to overwrite an existing destination unless `--force` is supplied. Managed project state is always protected: the configuration, cache and index databases, their SQLite sidecars, the manifest mirror, migration journal, and files in the migration-receipt directory cannot be export destinations, even with `--force`. A missing cache produces an empty file.

### `cache import`

```text
steadlith cache import [-c PATH] [--json] --trust-source SOURCE
```

Imports a Steadlith JSONL cache export after the operator explicitly supplies `--trust-source`. Cache exports are unsigned retrieval state: an attacker who controls one can supply vectors under otherwise valid cache identities and poison later results. Import only artifacts you created or authenticated out of band. Steadlith enforces file, line, and vector-size limits; an existing identical key/value is retained and touched, while an incompatible value for an existing key fails instead of silently overwriting it.

## Exit codes

| Code | Meaning |
| ---: | --- |
| 0 | Success |
| 1 | Plan/apply failure or an intentionally gated operation |
| 2 | Verification mismatch |
| 3 | Backend or embedding-provider error |
| 4 | Configuration error |
| 130 | Interrupted with Ctrl+C |

Documented JSON fields and top-level Python exports follow the [compatibility policy](compatibility.md). Consumers must ignore additional JSON fields so compatible releases can add diagnostics.
