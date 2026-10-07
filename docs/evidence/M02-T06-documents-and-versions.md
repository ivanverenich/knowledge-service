# M02-T06 — Documents and versions evidence

Completed: 2026-10-07

## Invariant

What is stored is the current state of one source item, never its history. A
stored row keeps its `document_id`, its `created_at`, and its provenance key
`(source_id, external_id)`, so repeated observations of the same item always
update that one row instead of adding another. The two versions move
independently: `content_version` advances only when the content fingerprint
changes, and `authorization_version` only when the authorization fingerprint
changes. An observation that saw no change must leave every stored value
untouched, no version may ever move backwards, and a source item that disappears
becomes `tombstoned` rather than deleted.

## Prediction

I expect the revision to create one `documents` table whose unique constraint is
`(source_id, external_id)`, with `CHECK` constraints keeping both versions at
least 1 and `availability` limited to the two known values. The write path
should be a single `INSERT ... ON CONFLICT DO UPDATE` that returns the stored
`document_id`, so writing the same observation twice leaves one row with
unchanged versions, and a later observation that carries a fresh `document_id`
still returns the identity stored the first time.

## Verification

- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_persistence.py -q`
  — 4 passed. Covers the row round trip, the content-only version change, the
  tombstone round trip, and the typed failure for an unknown stored
  `availability`.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration tests/test_migrations_integration.py tests/test_sources_integration.py tests/test_persistence_integration.py -q`
  — 3 passed in 0.78s against PostgreSQL 17.11, and 3 passed again on a second
  run. The new test proves that writing the same Document twice leaves one row
  with unchanged versions, that re-registering the same source item with a fresh
  `document_id` still returns the stored identity and keeps `created_at`, that a
  content-only change moves `content_version` to 2 while
  `authorization_version` stays at 1, that an authorization-only change moves
  `authorization_version` to 2 while `content_version` stays at 2, and that a
  tombstone is stored as `tombstoned`. No connection URL or credentials are
  recorded.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make check` — Ruff format and lint
  passed, Pyright strict clean, 189 passed, 4 skipped, 2 deselected, total
  coverage 89%.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` — passed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pre-commit run --all-files` —
  all 12 hooks passed, including secret scanning, formatting, lint, type
  checking, and deterministic tests.
- `git diff --check` — passed.

Implementation files:
`migrations/versions/5e8b9875be50_documents.py`,
`src/knowledge_service/persistence.py`, `tests/test_persistence.py`, and
`tests/test_persistence_integration.py`. The persisted columns hold fingerprints
and provenance only, so no credential, connection string, or private source
content entered a tracked artifact; the integration test reads only the
dedicated disposable-database variable.

## Reflection

1. `upsert_document` decides which row a Document belongs to, using the
   `(source_id, external_id)` key rather than the id the caller passed in. It
   refuses to overwrite `document_id` and `created_at`, writes only the columns
   that describe the current state, and returns the identity that is actually
   stored. A caller writing this SQL by hand would most likely list every column
   in the `DO UPDATE SET` clause, which would quietly replace the stored
   identity on the second write instead of keeping it.

2. With one shared version column, nothing could tell whether the text or the
   permissions had changed. Re-indexing would have to re-embed every changed
   Document, including ones where only access rules moved, and a permission
   refresh would look identical to a content change. Keeping the versions
   separate is what lets later work re-embed only changed content and re-check
   permissions without touching embeddings.

3. Without the idempotency test, a second observation carrying a fresh
   `document_id` could silently take over the row, and version numbers could
   climb on every scan even when nothing changed. The system would then look
   permanently in-flux: incremental indexing and authorization refresh would
   never settle, and nobody would notice until the version numbers or the
   storage growth stopped making sense.
