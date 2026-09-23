# M00-T13 — Documentation-link validation evidence

Completed: 2026-09-23

## Invariant

Planning documents must remain internally navigable, and curriculum task
metadata must identify valid tasks and dependencies. Validation is local,
deterministic, and provider-independent.

## What changed

| File | Change |
|---|---|
| `src/knowledge_service/documentation.py` | Added typed Markdown-link, front-matter, duplicate-ID, and dependency validation |
| `scripts/validate_docs.py` | Added the repeatable repository validation CLI |
| `Makefile` | Exposed `make docs-check` |
| `tests/test_documentation_validator.py` | Added valid, ignored-link, and repaired broken-link/dependency tests |

The validator reports structured `ValidationIssue` values and exits nonzero
when it finds a broken internal link, malformed task front matter, duplicate
task ID, invalid status, or missing dependency.

## Verification

```text
$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
uv run python scripts/validate_docs.py

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_documentation_validator.py
3 passed

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
14 passed, 1 deselected

$ UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
all hooks passed
```

The focused negative test planted a broken dependency and link, observed
typed validation issues, repaired both, and then observed a clean result.
No secrets, provider payloads, or private documents entered the repository.

## Reflection

- **What complexity did this task hide from its caller?** Contributors get one
  command instead of manually checking links and task metadata.
- **Which alternative would make the next change harder, and why?** A
  third-party documentation parser would add dependency and configuration
  surface for a small deterministic check.
- **What production failure would escape if the negative test were removed?**
  Broken curriculum navigation or dependencies could merge while the command
  still appears superficially successful.
