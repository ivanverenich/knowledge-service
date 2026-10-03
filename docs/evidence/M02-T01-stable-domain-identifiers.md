# M02-T01 — Stable domain identifiers evidence

Completed: 2026-10-03

## Invariant

Application code uses project-owned identifiers, while external provider identifiers are translated at the boundary and do not leak into domain contracts.

## Prediction

The first narrow test command will fail because the identifiers module and its
tests do not exist yet. After implementation, each identifier kind should keep
its type, round-trip through its canonical UUID string, and reject malformed
input with InvalidIdentifier.

## Verification

The static type probe was rejected as expected: Pyright reported that an
argument of type `DocumentId` cannot be assigned to a parameter of type
`SourceId`. After removing the probe, Pyright reported zero errors, warnings,
or information messages.

The guide's canonical `tests/test_identifiers.py` path did not exist because
the implementation file was spelled `tests/test_indentifiers.py`. The test
module was run directly at that existing path: 72 passed. The guide now points
to the actual filename.

- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_indentifiers.py -vv` — 72 passed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make check` — Ruff format and lint passed; Pyright reported 0 errors, 0 warnings, 0 informations; 161 passed, 2 deselected; total coverage 93%.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` — passed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks` — all pre-commit hooks passed, including secret scanning, formatting, lint, type checking, and deterministic tests.
- `git diff --check` — passed.

The deliverable is `src/knowledge_service/identifiers.py`. It imports only
standard-library modules, documents where internal and external identifiers
enter, and suppresses the underlying parse error context. Tests use fixed
synthetic UUIDs; no secrets, provider calls, or private content were used.

## Reflection

1. Identifiers don't allow caller to accidently pass incorrect id to incorrect place, for example pass UserId instead of DocumentId.

2. Alternatively it is possible use raw UUIDs, but propability of error is much highter

3. Missing handlers for raising exceptions related to invalid identifier or incorrect identifier type
