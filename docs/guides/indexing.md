# Indexing

An indexing run has two phases: prepare a complete target snapshot, then apply it. The same planner powers `plan`, `index`, and migration previews.

## Establish the corpus boundary

For repeatable projects, put the full source scope in `steadlith.toml`:

```toml
[sources]
include = ["handbook/**/*.md", "policies/**/*.txt"]
exclude = ["**/drafts/**", "**/.git/**", "**/.steadlith/**"]
```

Then run commands without positional paths:

```bash
steadlith plan
steadlith index
```

Positional paths replace the configured include set for that invocation. They still define a complete desired corpus:

```bash
steadlith plan handbook/ policies/
```

Use this form for controlled one-off indexes, not to add a single file to an established corpus.

## Read the plan

Each occurrence receives one operation:

| Operation | Meaning | New embedding when identity is unchanged? |
| --- | --- | --- |
| `add` | New chunk occurrence. | Only on a cache miss for its chunk hash. |
| `keep` | Same hash and position. | No. |
| `move` | Same chunk content at a different position or with changed source metadata. | No. |
| `delete` | Previously active occurrence absent from the target. | No; the old record is tombstoned. |

A model or provider-parameter migration can re-embed kept and moved content because embedding identity changed. The chunk operation alone does not describe embedding work, so also inspect `embedding_count`, `cache_hits`, `tokens`, and the price estimate.

Unknown provider prices remain unknown. Steadlith never fetches pricing or assumes that its word-based token count matches a provider billing tokenizer.

## Apply approval gates

Steadlith requires explicit approval for three classes of effect.

Network or model download:

```bash
steadlith index --allow-network
```

Any logical deletion:

```bash
steadlith index --allow-delete
```

Replacing a non-empty corpus with an empty one:

```bash
steadlith index --allow-delete --allow-empty
```

Combine flags only after reviewing the plan. These approvals are invocation-scoped and are not saved.

## Apply behavior

An apply performs the following work:

1. Rebuild the target manifest from sources.
2. Compare the current generation and corpus root with the prepared state.
3. Reuse active vectors with the same embedding identity.
4. Read content-addressed cache entries for remaining chunk hashes.
5. Call the configured provider in bounded batches for misses.
6. Write successful batches to the cache.
7. Build all target index records in memory.
8. Publish records, document state, manifest, root, embedding identity, and the next generation in one SQLite transaction.
9. Write the diffable manifest mirror after the database commit.

Queries never observe a partially published generation. If another writer committed after preparation, the apply fails instead of overwriting the newer state.

Plan preparation, status, and database verification each read one consistent SQLite
snapshot. An interrupted apply rolls back its uncommitted index changes; the same
connection can be reused after the interruption.

Provider-side charging cannot be strictly transactional with a local SQLite commit. A process failure after a remote provider accepts a request but before the cache records its response can lead to a repeated charge on retry.

## Idempotent routine

A practical update routine is:

```bash
steadlith plan --json > plan.json
steadlith index
steadlith status --json
steadlith verify --json
```

If `plan` reports deletions, review source scope and rerun `index --allow-delete`. Do not automatically attach `--allow-delete` to every scheduled job. The gate is most useful when it can stop a bad glob, an empty mount, or an accidental partial path.

## JSON automation

Use process exit codes and JSON together:

```bash
steadlith plan --json
```

Consumers should:

- check the process exit code before parsing success output;
- treat paths and source text as untrusted display data;
- ignore unknown JSON fields for forward compatibility;
- compare operation and embedding counts against policy limits;
- require a separate operator approval when deletes are present;
- run `verify` after a successful apply.

See [JSON output](../reference/json-output.md) for envelopes and compatibility rules.

## Hard cuts

A hard cut occurs when no primary or backup Rabin boundary is usable before `max_tokens`. High hard-cut rates can indicate parameters that fit the corpus poorly.

Check `hard_cut_rate` in `status --json` and compare it across representative documents before changing defaults. A hard cut is not a verification failure, but it weakens the practical edit-locality benefit expected from content-defined boundaries.

## Common mistakes

Indexing one changed file by position
: The omitted files are planned for deletion. Use configured globs for the full corpus.

Changing `steadlith.toml` and querying immediately
: The active index still carries the old identity. Run `plan`, apply the migration, then query.

Putting cache and index at the same path
: Validation rejects the configuration because the schemas and lifecycles differ.

Indexing generated state
: Managed state is excluded unconditionally. Add application build outputs and private working directories to `[sources].exclude`.
