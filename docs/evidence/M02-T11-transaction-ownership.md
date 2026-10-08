# M02-T11 — Transaction ownership evidence

Completed: 2026-10-08

## Invariant

An application workflow opens the transaction and commits once; a repository
function takes a connection and never commits, so it cannot end a unit of work
it does not own. A Document is published when its row, its Chunks, and its
Access Grants are written in one transaction, so a reader sees either the
previous complete version or the new one, never a mixture. A workflow that fails
publishes nothing, including the writes it had already made inside its own
transaction. External calls — model providers, source APIs, embedding services —
happen outside the transaction, so no transaction is held open across a network
call and no row is locked before one.

## Prediction

I expect `DatabaseRuntime.transaction()` to be the only place a transaction
opens, wrapping `engine.begin()` so a clean exit commits and a raise rolls back.
`publish_documents` should own that boundary while `publish_document` takes the
connection, and a `Publication` should refuse parts that do not describe one
Document version before any transaction opens. A redelivered older version
should be refused inside the transaction, leaving the published version and its
Chunks untouched, and a second Document in the same failed batch should not be
published at all. While a publication is uncommitted, another connection should
still read the previous complete version.

## Verification

- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_publishing.py -vv`
  — 4 passed. Covers a consistent Publication, one with no grants, a Chunk
  carrying a different content version, and a Grant belonging to another
  Document.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration tests/test_publishing_integration.py -vv`
  — 2 passed in 0.68s against PostgreSQL 17.11, repeated on a second run. The
  failure-injection test publishes version 1 and version 2, then attempts a
  batch whose first item is a redelivered version 1; `upsert_document` rewrites
  the Document row before `replace_chunks` raises `StaleChunkWrite`, and the
  assertions show the row is still at content version 2, its Chunks are still
  the version 2 run, and the second Document in that batch was never published.
  The visibility test reads from a second connection while a publication is
  uncommitted and sees the version 1 row and its version 1 Chunks, then sees
  version 2 after the block exits. Reading the tables after the run shows
  `page-a` at version 2 with one Chunk reading `Second version.` at version 2
  and one Access Grant, and no `page-b` row. No connection URL or credentials
  are recorded.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project make check`
  — Ruff format and lint passed, Pyright strict clean, 295 passed, 2 deselected,
  total coverage 88%.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` — passed, including
  the new decision record and its index entry.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pre-commit run --all-files` —
  all 12 hooks passed, including secret scanning, formatting, lint, type
  checking, and deterministic tests.
- `git diff --check` — passed.

Implementation files: `src/knowledge_service/database.py`,
`src/knowledge_service/publishing.py`,
`docs/adr/0012-application-workflows-own-transaction-boundaries.md`,
`docs/adr/README.md`, `tests/test_publishing.py`, and
`tests/test_publishing_integration.py`. The policy records no credential or
connection string, and the tests use synthetic fixture text only; the
integration tests read only the dedicated disposable-database variable.

## Reflection

1. `publish_document` is responsible for the writes and for using the identity
   the database already holds; `publish_documents` is responsible for the
   boundary — it decides when the batch becomes final and what happens when one
   item fails. If every repository function opened its own connection, publishing
   one Document would be three separate commits, so a failure between them would
   leave the row updated while its Chunks still described the previous version,
   and the batch could publish its first Document while refusing its second.

2. Opening the transaction inside each repository function would take the choice
   away from the caller. A publication could no longer be all or nothing, an
   out-of-order redelivery could overwrite a newer published version before the
   Chunk write refused it, and a batch of Documents could commit partially. It
   would also make the boundary invisible: a later workflow could not tell
   whether two calls shared a transaction or each committed on its own.

3. Without that assertion, a publication could be seen half-finished. A reader
   that arrived while the row had already changed but the Chunks had not would
   get an Answer citing Chunks from one content version next to a Document row
   from another, and nothing would raise. The fault would surface as
   occasionally wrong citations rather than as an error, which is the hardest
   kind to trace back to its cause.
