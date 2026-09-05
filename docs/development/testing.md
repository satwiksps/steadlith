# Testing

## Required checks

Run from the repository root:

```bash
python -m ruff check .
python -m ruff format --check .
python -m mypy src/steadlith
python -m pytest --cov=steadlith --cov-branch --cov-report=term-missing
python -m build
```

Test the distribution in a separate environment with only runtime dependencies:

```bash
python -m venv tmp/wheel-check
tmp/wheel-check/bin/python -m pip install dist/steadlith-1.0.0-py3-none-any.whl
tmp/wheel-check/bin/python -m pip check
tmp/wheel-check/bin/python tests/smoke_installed.py
```

Use `tmp/wheel-check/Scripts/python.exe` in PowerShell, and substitute the wheel filename for the version being tested. The smoke script runs outside the checkout and checks both entry points, exact quick-start results, repeat indexing, deletion guards, migration and rollback, cache transfer, and compaction. Pull-request and release CI run the same script against the built wheel.

Build documentation with warnings as errors:

```bash
python -m pip install -r docs/requirements.txt
python -m sphinx -W --keep-going -b dirhtml docs docs/_build/dirhtml
```

Website changes have a separate locked Node environment:

```bash
cd website
npm ci
npm audit --audit-level=high
npm run lint
npm run typecheck
npm run build
```

## Test categories

Unit tests
: Validate normalization, chunking, hashes, manifests, planning, cache, config, providers, and adapters in isolation.

Regression tests
: Preserve exact failures found during review, including Unicode output, source-state exclusion, duplicate occurrence handling, migration restore, and locality counterexamples.

Determinism tests
: Pin parameter hashes, chunk hashes, stream behavior, and serialized state across the CI matrix.

Integration-style tests
: Exercise complete local workflows with real SQLite state while keeping external providers out of default CI.

Benchmark tests
: Reproduce versioned churn and retrieval fixtures. They protect comparative results, not private application quality.

## Identity changes

Treat any change to these areas as protocol work:

- whitespace and Unicode normalization;
- tokenizer identity or count behavior;
- Rabin symbols, rolling hash, masks, or boundary state;
- chunk parameter serialization;
- hash domains and field order;
- Merkle construction;
- cache identity;
- instance IDs;
- manifest and SQLite schemas.

Add or update golden vectors only after deciding whether the change preserves v1 or requires a new identity version. Never modify a golden value only to make a failing test pass.

## Provider tests

Default tests use fakes and malformed responses to verify:

- batch cardinality;
- identity stability;
- vector dimensions and finite values;
- retry classification and limits;
- cache write behavior;
- safe user-facing errors.

Live-provider tests require explicit credentials, cost approval, and synthetic text. They are not part of default pull-request CI.

## Windows behavior

The supported matrix includes Windows. Tests should cover path containment, case-insensitive suffixes, Unicode paths and text, console encoding, file replacement, SQLite sidecars, and cleanup without relying on POSIX-only shell behavior.
