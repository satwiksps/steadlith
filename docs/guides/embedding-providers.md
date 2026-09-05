# Embedding providers

An embedding provider has three identity components: provider-selected model ID, parameters hash, and vector dimensions. Steadlith stores the first two with the active index and validates dimensions independently.

## Choose a provider

| Requirement | Recommended starting point |
| --- | --- |
| Offline keyword or identifier lookup | `hash` |
| Local learned semantic embeddings | `sentence-transformers` |
| Hosted semantic embeddings | `openai` |

Model quality, legal terms, data handling, availability, and price depend on the selected model and deployment. Steadlith does not choose a current model on the operator's behalf.

## Hash provider

```toml
[embedding]
provider = "hash"
model = "steadlith-hash-256-v1"
dimensions = 256
batch_size = 64
price_per_million_tokens = 0.0
```

Properties:

- included in the core package;
- deterministic across supported platforms;
- no account, model download, or network request;
- unigram and adjacent-bigram lexical features;
- fixed output dimension selected in configuration;
- not a semantic language model.

Changing dimensions changes embedding parameters and produces a distinct cache identity.

## OpenAI provider

Install:

```bash
python -m pip install "steadlith[openai]"
```

Configure:

```toml
[embedding]
provider = "openai"
model = "YOUR_MODEL_ID"
dimensions = 1536
batch_size = 64
price_per_million_tokens = 0.0
api_key_env = "OPENAI_API_KEY"
```

Set the key in the process environment and approve network use:

```bash
export OPENAI_API_KEY="..."
steadlith migrate --embedding-provider openai \
  --embedding-model YOUR_MODEL_ID \
  --embedding-dimensions 1536
```

After reviewing the preview:

```bash
steadlith migrate --embedding-provider openai \
  --embedding-model YOUR_MODEL_ID \
  --embedding-dimensions 1536 \
  --apply --allow-network --allow-delete
```

Constraints:

- only the official OpenAI endpoint is supported;
- only `OPENAI_API_KEY` may be selected in project configuration;
- requested model and dimensions must be valid for the live service;
- text in missing chunks is sent to the provider;
- current price, quota, rate limits, retention, and regional availability remain operator responsibilities.

Custom base URLs are rejected in project configuration, and `OPENAI_BASE_URL` cannot override the official endpoint. This prevents repository configuration or an inherited endpoint override from forwarding documents and the API key to another endpoint.

## Sentence Transformers provider

Install:

```bash
python -m pip install "steadlith[sentence-transformers]"
```

Configure:

```toml
[embedding]
provider = "sentence-transformers"
model = "YOUR_MODEL_ID"
dimensions = 384
batch_size = 32
```

The adapter loads the configured model through `sentence-transformers` and verifies the actual output dimension. Model loading may download files, so indexing and querying require `--allow-network` even when inference itself is local.

Before deployment, pin and review:

- the exact model revision and artifact source;
- model and dataset licenses;
- whether model code requires remote-code trust;
- hardware memory and inference latency;
- CPU, GPU, and framework versions;
- behavior without network access after artifacts are cached.

## Batching and retries

`batch_size` bounds texts sent to one provider call. The batching layer validates:

- one output vector per input text;
- configured dimensions for every vector;
- numeric, finite, float32-representable values;
- stable provider identity throughout the request sequence.

Explicit transient provider failures use bounded retries with backoff. Invalid responses and permanent provider failures are not treated as safe retries.

## Cost estimates

`price_per_million_tokens` is a reporting value. Plan cost is calculated from Steadlith's deterministic word-based token counts, which can differ from provider billing tokens.

For a decision:

1. Obtain a current price from the provider.
2. Put the value in configuration.
3. Run `steadlith plan --json`.
4. Compare Steadlith's estimated units with provider tokenizer or billing data.
5. Run a small controlled apply and inspect actual usage.

Never interpret a configured `0.0` as a statement that a remote model is free.

## Provider changes

Use `steadlith migrate`, not a direct edit followed by `index`. Migration prices the target identity, persists the configuration only after index publication, records a receipt, and supports immediate rollback while required state remains available.
