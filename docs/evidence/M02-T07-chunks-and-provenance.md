# M02-T07 — Chunks and provenance evidence

Completed: 2026-10-07

## Invariant

A stored Chunk run answers to three rules. It belongs to exactly one Document
and one content version, and its ordinals run contiguously from zero, so reading
the run back returns the pieces in their original order. Replacing a run deletes
the previous one and inserts the new one inside the caller's transaction, so a
replacement that fails leaves the stored run exactly as it was. A write whose
content version is older than the stored run is refused, because at-least-once
delivery can deliver a job after a newer one has already been applied.

## Prediction

I expect the revision to create one `chunks` table with
`UNIQUE (document_id, ordinal)`, a cascading foreign key to `documents`, and
`CHECK` constraints on `content_version`, `ordinal`, and `token_count`. The write
path should validate the run, compare the stored `max(content_version)` against
the incoming version, and only then delete and re-insert. Three things should
hold when tested against PostgreSQL: an abandoned transaction leaves the previous
Chunks untouched, a committed replacement for a newer version removes the stale
run and keeps ordinals `[0, 1]`, and a late write for the old version raises
`StaleChunkWrite`.

## Verification

- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_chunks.py -q`
  — 5 passed in 0.01s. Covers ordinal ordering, the ordinal gap, a Chunk from
  another Document, a Chunk from another content version, and empty Chunk text.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration -q`
  — 6 passed in 1.07s against PostgreSQL 17.11, covering migrations, Sources,
  Documents, and Chunks. The chunk test passed on two consecutive runs. It
  proves the abandoned replacement left the three version-1 Chunks stored, the
  committed version-2 replacement dropped the stale run and kept ordinals
  `[0, 1]` carrying `content_version` 2, and the late version-1 write raised
  `StaleChunkWrite` without changing what was stored. Reading the table after
  the run shows exactly `0 | First | 2` and `1 | Second and third | 2`. No
  connection URL or credentials are recorded.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make check` — Ruff format and lint
  passed, Pyright strict clean, 199 passed, 2 deselected, total coverage 90%.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` — passed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pre-commit run --all-files` —
  all 12 hooks passed, including secret scanning, formatting, lint, type
  checking, and deterministic tests.
- `git diff --check` — passed.

Implementation files: `src/knowledge_service/chunks.py`,
`migrations/versions/559156a3fbd6_chunks.py`,
`src/knowledge_service/persistence.py`, `tests/test_chunks.py`, and
`tests/test_chunks_integration.py`. The table stores synthetic text and
fingerprints only, so no credential, connection string, or private source
content entered a tracked artifact; the integration test reads only the
dedicated disposable-database variable.

## Reflection

1. If `replace_chunks` opened its own connection and committed by itself, the
   delete would become visible before the insert had a chance to fail. A crash
   between the two would leave the Document with no stored Chunks at all, and
   retrieval would return nothing until the next synchronization run. It would
   also be unable to join the caller's transaction, so a job that failed after
   the replacement could not roll it back.

2. Rebuilding the order from a character offset would break as soon as
   re-chunking split one section in two, because every later offset would shift
   and all following pieces would carry stale positions. There would also be no
   cheap way to express "these two Chunks replaced that one". And a citation for
   a Confluence page needs a page anchor, not a character position, so the
   offset could not be turned back into a location — the anchor column would
   still be needed.

3. Without the rollback assertion, a replacement that failed halfway could
   silently leave a Document with no searchable Chunks while its `documents` row
   still looked healthy. Users would see answers that found no evidence, which
   reads as a retrieval-quality problem rather than data loss, so the cause
   would be hard to find and easy to misdiagnose.
