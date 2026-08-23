# Documentation development

The documentation is Sphinx with MyST Markdown and the Sphinx-Immaterial theme. Read the Docs builds it from `.readthedocs.yaml` using Python 3.12 and exact documentation dependency pins.

## Local build

```bash
python -m pip install -r docs/requirements.txt
python -m pip install -e .
python -m sphinx -W --keep-going -b dirhtml docs docs/_build/dirhtml
```

Open `docs/_build/dirhtml/index.html` in a browser.

The `-W` flag turns warnings into failures. `--keep-going` reports all warnings in one run.

## Structure

- `getting-started/` contains first-use material and configuration guidance.
- `guides/` contains goal-based operational procedures.
- `concepts/` explains identity, state, and transactions.
- `reference/` documents exact fields, commands, JSON, and APIs.
- `development/` covers contribution, testing, security, releases, and documentation work.
- Existing algorithm, architecture, benchmark, compatibility, limitation, and adapter documents remain focused technical references.

## Writing rules

- Lead with observable behavior or the task outcome.
- Use exact command names, flags, defaults, and error boundaries from the implementation.
- Distinguish implemented support from design constraints and unsupported scope.
- Keep warnings actionable. State what can go wrong and what the reader should do.
- Avoid promises about retrieval quality, provider cost, or locality that are not backed by reproduced evidence.
- Use short examples that can be copied without hidden setup.
- Prefer tables for schemas and matrices, prose for reasoning, and numbered steps for stateful procedures.
- Avoid decorative punctuation, marketing filler, and future-version roadmaps.

## Links and cross-references

Use relative Markdown links for project pages:

```markdown
[Migrations](../guides/migrations.md)
```

Use fully qualified symbols in autodoc directives:

````markdown
```{autoclass} steadlith.CDCChunker
:members:
```
````

Build with warnings as errors after renaming any page or heading. MyST generates anchors for headings through level four.

## Add a page

1. Put it in the task-appropriate directory.
2. Add it to the corresponding hidden `toctree` in `docs/index.md`.
3. Link it from at least one nearby guide when useful.
4. Build locally with warnings as errors.
5. Inspect desktop and narrow viewport rendering.
6. Update the changelog when documentation describes a behavior change.

## Read the Docs builds

Read the Docs installs `docs/requirements.txt`, installs the repository package, and runs the `dirhtml` builder. The canonical URL is supplied through `READTHEDOCS_CANONICAL_URL` by the platform. Pull-request builds produce HTML previews when enabled in the Read the Docs project settings.

Do not set a hard-coded canonical host in `conf.py`. This keeps local builds and alternate project slugs from publishing false metadata.
