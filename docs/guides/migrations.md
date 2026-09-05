# Migrations

A migration changes chunking or embedding identity while keeping configuration and index state synchronized. The workflow supports preview, apply, recovery after interruption, and immediate checked rollback.

## Preview first

Preview a chunk-size change:

```bash
steadlith migrate --min-tokens 220 --max-tokens 720
```

Preview an embedding change:

```bash
steadlith migrate \
  --embedding-provider sentence-transformers \
  --embedding-model YOUR_MODEL_ID \
  --embedding-dimensions 384
```

Preview is the default. `--dry-run` is accepted when an explicit marker is useful in automation. A preview parses the target configuration in memory, rebuilds the target manifest, checks cache identities, and reports work. It does not construct a provider, mutate state, write a receipt, or edit TOML.

Positional source paths are permitted only for exploratory previews. An apply always uses the persisted `[sources]` configuration so the published config immediately reproduces the committed corpus.

Migration editing supports named `[chunker]` and `[embedding]` tables with single-line settings, including quoted names and whitespace inside table headers. It preserves unrelated comments and settings. If a configuration uses inline or dotted tables, or multiline values for settings being changed, rewrite those sections as named tables before migrating. The parsed target must match exactly the requested changes.

## Apply a reviewed migration

Repeat the target arguments with `--apply` and required approvals:

```bash
steadlith migrate \
  --embedding-provider sentence-transformers \
  --embedding-model YOUR_MODEL_ID \
  --embedding-dimensions 384 \
  --apply --allow-network --allow-delete
```

An embedding identity change tombstones the prior vector lifecycle even when every chunk operation is `keep`. This is why a model migration requires deletion approval.

Apply sequence:

1. Validate current config, generation, root, and absence of another pending migration.
2. Write and fsync a recovery journal beside the config.
3. Resolve cache hits and provider work.
4. Publish one new SQLite generation.
5. Atomically write the target TOML.
6. Write a checksummed receipt in the database-namespaced receipt directory.
7. Remove the pending journal.

The receipt is checksummed for corruption detection. It is not an authenticated audit record.

Both the current and target TOML must fit the 1 MiB migration limit. Oversized targets are rejected during preparation, before any provider work or index publication, so recovery can always read the staged configuration.

Journal and receipt publication requires a filesystem that supports hard-link creation within a directory. Configuration publication requires atomic replacement within its directory. Steadlith creates each temporary file beside its destination; filesystems that do not provide these operations are unsupported and cause migration apply or recovery to fail with a storage error. Parent directories are not explicitly fsynced, so sudden-power-loss durability of directory entries depends on the operating system and filesystem.

## Recover an interrupted migration

While a migration journal exists, normal config loads and ordinary commands fail closed. Recovery is always explicit:

```bash
steadlith migrate --recover
```

Recovery checks the journal against config and database state:

- If the target generation committed, it finishes configuration publication and receipt handling.
- If the database remained at the old generation, it clears the uncommitted stage.
- If state is neither recognized version, it refuses to guess.

Do not delete the journal manually. Preserve the database, config, and journal together for diagnosis.

Complete or abort recovery before relocating a project. A pending journal records resolved config, database, and receipt paths, and recovery requires the same resolved config path.

## Roll back the current migration

Preview:

```bash
steadlith migrate --rollback
```

Apply:

```bash
steadlith migrate --rollback --apply --allow-delete
```

Add `--allow-network` if the old embedding identity needs provider work.

Rollback creates a new generation. It does not erase database history or reactivate tombstoned occurrence IDs. It is accepted only when:

- the latest receipt matches current config and generation;
- no later index write or configuration edit occurred;
- current sources reproduce the receipt's pre-migration corpus root.

If source content changed, restore the corresponding source revision before rollback. If old cache entries were pruned, rollback can call the old provider again.

## Adopt a 0.2 project

Steadlith 1.0 preserves the v1 chunk and embedding identity wire format from Cairn 0.2. Adopt configuration explicitly:

```bash
steadlith adopt --from-config cairn.toml --config steadlith.toml
```

The command:

- validates the earlier configuration using 0.2 defaults;
- writes a normalized Steadlith configuration;
- preserves configured cache and index paths;
- preserves the embedding model identity;
- moves no state and computes no embedding;
- refuses cross-directory adoption, output overwrite, or a pending migration.

After adoption:

```bash
steadlith plan
steadlith verify
steadlith query "known phrase"
```

A compatible unchanged project should plan only keeps with zero embeddings. Do not rename `.cairn` state paths merely for appearance. The preserved paths keep the existing cache and index usable.

## Changing several settings

Pass all target settings in one preview and one apply. This avoids paying for an intermediate identity that is immediately replaced.

```bash
steadlith migrate \
  --chunker cdc-rabin \
  --window-words 64 \
  --min-tokens 220 \
  --max-tokens 720 \
  --primary-mask-bits 9 \
  --backup-mask-bits 7 \
  --embedding-provider openai \
  --embedding-model YOUR_MODEL_ID \
  --embedding-dimensions 1536
```

Record the preview JSON with the change review, then repeat exactly with `--apply` and approvals.
