# M02-T02 — Document and version invariants evidence

Completed: 2026-10-05

## Invariant

Content and authorization versions change independently: a content fingerprint change advances only the content version, while an access fingerprint change advances only the authorization version.

## Prediction

The first focused test command will fail because the Document domain module
and its tests do not exist yet. Equal fingerprints should keep both versions;
changing only the authorization fingerprint should advance only its version.

## Verification

The initial prediction was recorded, but the output from the deliberately red
pre-implementation command was not saved. During the finish review, the focused
test module existed and its observed result was 7 passed.

- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_documents.py -vv` — 7 passed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make check` — Ruff format and lint passed; Pyright reported 0 errors, 0 warnings, 0 informations; 168 passed, 2 deselected; total coverage 92%.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` — passed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks` — all pre-commit hooks passed, including secret scanning, formatting, lint, type checking, and deterministic tests.
- `git diff --check` — passed.

The implementation files are `src/knowledge_service/documents.py` and
`tests/test_documents.py`. The domain module imports standard-library types
and project-owned identifiers only; it has no persistence or provider imports.
The tests use fixed synthetic UUIDs, fingerprints, and timestamps. No
credentials or private source content were used.

## Reflection

1. Separate versions hide the logic of deciding whether content, permissions, or both changed from the persistence caller.

2. Using one version for everything would make indexing and ACL updates harder because callers could not tell which part actually changed.

3. Without those tests, permission changes might not be applied correctly, or deleted source documents might remain available to users.
