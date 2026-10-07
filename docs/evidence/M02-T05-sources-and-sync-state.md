# M02-T05 — Sources and synchronization state evidence

Completed: 2026-10-07

## Invariant

Database structure changes only through reviewed Alembic revisions, so the
`sources` and `synchronization_runs` tables are created by a migration and never
by the application at startup. A Source changes through explicit operations that
return a new value, and its checkpoint always moves forward in time, so a stale
observation cannot overwrite a newer one. A row must never store a credential:
it stores only the name of the reference its adapter resolves elsewhere. One
origin is one row, so `(kind, location)` is unique.

## Prediction

I expect I'll implement a migration that will create the tables, and won't do it
directly in code somewhere.

## Verification

- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_sources.py tests/test_migrations.py -q`
  — 8 passed. Covers successful registration, the checkpoint edge case, the
  no-op disable, the single-finish rule, typed failures for a naive timestamp
  and an empty location, and the single-root-revision check.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration tests/test_migrations_integration.py tests/test_sources_integration.py -q`
  — 2 passed in 0.49s against PostgreSQL 17.11, twice in a row. The baseline
  test finds the baseline by revision chain, upgrades to it, downgrades to base,
  and re-upgrades. The new test proves the revision creates both tables, that a
  duplicate `(kind, location)` is rejected while the same location under a
  different kind is accepted, and that PostgreSQL rejects a `succeeded` run with
  no `finished_at` and an unknown status. No connection URL or credentials are
  recorded.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make check` — Ruff format and lint
  passed, Pyright strict clean, 185 passed, 3 skipped, 2 deselected, total
  coverage 90%.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` — passed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pre-commit run --all-files` —
  all hooks passed, including secret scanning, formatting, lint, type checking,
  and deterministic tests.
- `git diff --check` — passed.

Implementation files: `src/knowledge_service/sources.py`,
`migrations/versions/b96066f98718_sources_and_synchronization_runs.py`,
`tests/test_sources.py`, `tests/test_sources_integration.py`,
`tests/test_migrations.py`, and `tests/test_migrations_integration.py`. The
`sources` table stores only `credentials_reference`, a name, so no credential,
connection string, or private content entered a tracked artifact.

## Reflection

1. Source object checks:

   - display_name is not empty
   - checkpoint is not empty if it has been passed
   - created_at and updated_at are UTC-aware timestamps
   - created_at is earlier in the timeline than updated_at

   SynchronizationRun object checks:

   - started_at is a UTC-aware timestamp
   - status is not Running if finished_at is passed
   - finished_at is a UTC-aware timestamp if passed
   - started_at is earlier in the timeline than finished_at

   I think most likely a caller would forget to check that a timestamp should be
   UTC-aware.

2. Database backups and test databases retain secret data for an unpredictable
   amount of time, so this violates the security of the system.

3. We could have a few rows with the same kind and location, which could lead to
   errors in the future.
