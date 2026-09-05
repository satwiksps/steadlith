# Configuration

Steadlith reads TOML from `steadlith.toml` by default. Pass `--config PATH` after a command to select another file. Relative paths and source patterns are resolved from the configuration file's directory, not from the shell's current directory.

Generate a documented starter file:

```bash
steadlith init
```

## Complete example

```toml
[chunker]
strategy = "cdc-rabin"
window_words = 48
min_tokens = 180
max_tokens = 640
snap_window_words = 24
primary_mask_bits = 8
backup_mask_bits = 6

[embedding]
provider = "hash"
model = "steadlith-hash-256-v1"
dimensions = 256
batch_size = 64
price_per_million_tokens = 0.0

[store]
cache = ".steadlith/cache.sqlite3"

[index]
backend = "sqlite"
database = ".steadlith/index.sqlite3"

[sources]
include = ["docs/**/*.md", "README.md"]
exclude = ["**/drafts/**", "**/.git/**", "**/.steadlith/**"]
```

Unknown tables and keys are rejected. This prevents a misspelled safety or identity setting from being silently ignored.

## Chunker fields

| Field | Default | Constraint | Meaning |
| --- | --- | --- | --- |
| `strategy` | `cdc-rabin` | Supported strategy name | Boundary algorithm. |
| `window_words` | `48` | Integer at least 2 in project config | Rolling Rabin window in normalized words. |
| `min_tokens` | `180` | Positive and below `max_tokens` | Do not accept CDC candidates before this size. |
| `max_tokens` | `640` | Greater than `min_tokens` | Use the last backup candidate or a hard cut by this size. |
| `snap_window_words` | `24` | Non-negative integer | Search radius used only by `cdc-rabin+snap`. |
| `primary_mask_bits` | `8` | `backup < primary <= 63` | Strength of the preferred fingerprint match. |
| `backup_mask_bits` | `6` | `1 <= backup < primary` | Strength of the fallback fingerprint match. |

Supported strategies:

| Strategy | Use |
| --- | --- |
| `cdc-rabin` | Default Rabin/TTTD content-defined chunking. |
| `cdc-rabin+snap` | CDC followed by a bounded sentence or paragraph boundary search. Requires project-specific legal review before distribution in a product. |
| `fixed` | Non-overlapping chunks capped by `max_tokens`; useful as a baseline. |
| `recursive` | Separator-priority chunking within the configured size bounds. |
| `semantic` | Lexical sentence-affinity proxy used for comparison; it does not call an embedding model. |

Every identity-bearing chunker change should be previewed with `steadlith migrate`. Do not edit identity settings and immediately query an older index.

## Embedding fields

| Field | Default | Constraint | Meaning |
| --- | --- | --- | --- |
| `provider` | `hash` | `hash`, `openai`, or `sentence-transformers` | Provider adapter. |
| `model` | `steadlith-hash-256-v1` | Non-empty string | Provider model identity. |
| `dimensions` | `256` | Integer from 1 to 65,536 | Required vector length. |
| `batch_size` | `64` | Positive integer | Maximum texts passed to one provider call. |
| `price_per_million_tokens` | Not assumed by the library; starter uses `0.0` | Finite non-negative number or omitted | Reporting input for plan estimates. |
| `api_key_env` | Not set | OpenAI only; must be `OPENAI_API_KEY` | Environment variable read by the OpenAI adapter. |
| `base_url` | Not set | Custom URLs rejected | Reserved configuration key; official OpenAI endpoint only. |

`price_per_million_tokens` does not affect provider billing. It changes only the displayed estimate. Omit it when the price or billing unit is unknown.

Provider and model settings participate in embedding identity. Dimensions are validated against stored vectors and provider output.

## Store fields

| Field | Default | Meaning |
| --- | --- | --- |
| `cache` | `.steadlith/cache.sqlite3` | SQLite content-addressed embedding cache. |

The cache path must stay below the configuration directory and must differ from the index database path. State paths cannot overlap the configuration, SQLite sidecars (`-wal`, `-shm`, `-journal`), index manifest (`.manifest.json`), migration journal, or migration receipt directory (`.migrations`). Validation rejects these collisions before writing state.

## Index fields

| Field | Default | Meaning |
| --- | --- | --- |
| `backend` | `sqlite` | Only supported backend value. |
| `database` | `.steadlith/index.sqlite3` | One logical index and its history. |

Use a different database file for each logical index. Collection and alias keys are rejected because SQLite has no implemented namespace or alias layer.

## Source fields

| Field | Default | Meaning |
| --- | --- | --- |
| `include` | `docs/**/*.md`, `README.md` | Globs used when no positional paths are supplied. |
| `exclude` | drafts, `.git`, `.steadlith` patterns | Patterns removed from discovered sources. |

Source rules:

- Patterns must be relative and cannot contain `..` path components.
- Explicit files, explicit directories, glob matches, and symlink targets must resolve below the configuration directory.
- Files must be valid UTF-8 text without an early NUL byte.
- Source content is checked for mutation while it is read.
- Steadlith-managed config, cache, index, SQLite sidecars, manifest mirrors, journals, and receipt directories are excluded unconditionally.
- Supported suffixes include common Markdown, reStructuredText, source code, web, configuration, tabular, and SQL text formats. Unsupported and binary suffixes are skipped.

## Environment variables

The core does not perform general environment substitution in TOML. The OpenAI adapter reads `OPENAI_API_KEY` when selected. Set it in the process environment, not in the configuration file:

```bash
export OPENAI_API_KEY="..."
steadlith index --allow-network
```

PowerShell:

```powershell
$env:OPENAI_API_KEY = "..."
steadlith index --allow-network
```

Do not commit credentials. Custom API endpoints and custom credential-variable names are deliberately rejected from project-controlled configuration.

## Validate a configuration

Every state command validates before use. For a write-free check that also evaluates source scope and identity:

```bash
steadlith plan --json
```

Configuration errors use exit code 4 and include a direct explanation of the invalid field.
