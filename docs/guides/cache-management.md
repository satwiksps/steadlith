# Cache management

The embedding cache prevents repeated provider work for identical content under the same embedding identity.

## Cache key

```text
(chunk_hash, model_id, embedding_parameters_hash)
```

Source path and chunk position are absent. Identical content can reuse one vector across documents and positions. A model or parameter change receives a separate entry even when chunk hashes are unchanged.

## Inspect usage

```bash
steadlith cache stats
steadlith cache stats --json
```

The report includes entry count, distinct chunks, distinct models, and encoded vector bytes. The number does not include all SQLite page and index overhead.

## Prune entries

Remove entries not accessed for 90 days:

```bash
steadlith cache prune --max-age-days 90
```

Keep at most 100,000 least-recently-used entries:

```bash
steadlith cache prune --max-entries 100000
```

Apply both policies:

```bash
steadlith cache prune --max-age-days 90 --max-entries 100000
```

Pruning has no dry-run mode. Back up the cache first when re-embedding is expensive. Removing cache entries does not remove vectors already stored in the active index, but later edits, restores, or migrations may need the provider again.

## Export

```bash
steadlith cache export cache-backup.jsonl
```

The command writes deterministic JSON Lines through a temporary file and atomic replacement. It refuses an existing destination unless `--force` is present:

```bash
steadlith cache export --force cache-backup.jsonl
```

It also refuses destinations that resolve to the live cache, its SQLite sidecars, or the configured index database.

## Import

```bash
steadlith cache import --trust-source cache-backup.jsonl
```

`--trust-source` is mandatory because cache exports are unsigned. A malicious artifact can place attacker-selected vectors under valid-looking identities and poison later retrieval. Authenticate the file outside Steadlith before import.

Import validates:

- file and line size limits;
- JSON structure and identity fields;
- vector dimension and value constraints;
- duplicate-key consistency;
- conflicts with existing entries.

Identity fields must be non-empty strings, vectors must be arrays of finite numbers,
and token counts must be non-negative integers. Malformed fields are rejected instead
of being converted. Validation failure leaves the cache unchanged and identifies
the invalid line.

An identical existing entry is retained and touched. A different vector under an existing key fails instead of overwriting trusted state.

## Backup strategy

For a consistent offline backup:

1. Stop writers that use the project.
2. Export the cache or copy the SQLite database with a SQLite-aware backup method.
3. Copy the index database and its manifest mirror.
4. Copy `steadlith.toml` and source revision information.
5. Record the Steadlith version.
6. Restore into a disposable directory and run `steadlith verify`.

Do not rely on copying only the main SQLite file while an active writer can leave committed pages in a WAL file.

## Cache and index are different

The cache is keyed by reusable content and embedding identity. The index stores source occurrences, positions, metadata, validity intervals, and vectors for one committed corpus. They use separate schemas and must use different file paths.

Deleting or compacting an index does not automatically prune the cache. Pruning the cache does not alter active query results already present in the index.
