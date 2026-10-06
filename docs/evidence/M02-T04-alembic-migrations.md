# M02-T04 — Alembic migrations evidence

Completed: 2026-10-06

## Invariant

Database structure changes only through an explicitly run Alembic revision.
Application startup never calls `metadata.create_all()` and never applies
revisions automatically, so every schema change is a reviewed, ordered,
reversible file. `env.py` obtains its connection from the same validated
`create_database_runtime(Settings())` boundary the application uses, and no
database URL or password is stored in `alembic.ini` or in a migration.

## Prediction

I think this is database version, because no tables so far. I expected Alembic
to record which revision was applied rather than any table contents, and to
create no application tables yet because no SQLAlchemy models are defined.

## Verification

- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_migrations.py -vv`
  — 2 passed in 0.37s. One test proves there is a single root revision with
  `down_revision = None`; the other proves that a missing
  `KNOWLEDGE_SERVICE_DATABASE_URL` raises `DatabaseConfigurationError` without
  needing PostgreSQL.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration tests/test_migrations_integration.py -vv`
  — 1 passed in 0.58s against PostgreSQL 17.11 in a disposable container. It
  proved upgrade to the baseline, downgrade back to base, and re-upgrade to the
  same revision ID, with `alembic_version` as the only table created. The
  connection URL and credentials are not recorded.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make check` — Ruff format and lint
  passed, Pyright clean, 179 passed, 2 skipped, 2 deselected, total coverage
  92%.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` — passed.
- `git diff --check` — passed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pre-commit run --files <changed files>`
  — all hooks passed, including secret scanning, trailing whitespace,
  end-of-file, formatting, lint, type checking, and deterministic tests.

Implementation files: `pyproject.toml`, `uv.lock`, `alembic.ini`,
`migrations/README`, `migrations/env.py`, `migrations/script.py.mako`,
`migrations/versions/20c8d8dcd117_baseline.py`, `tests/test_migrations.py`,
`tests/test_migrations_integration.py`, and
`docs/lessons/M02-T04-establish-alembic-migrations.md`. No credential,
connection string, or private content entered a tracked artifact; the
integration test reads only the dedicated disposable-database variable.

## Reflection

1. The revision ID (a short string like `20c8d8dcd117`), recorded in Alembic's
   own `alembic_version` table.

2. `metadata.create_all()` on startup, manual SQL, or a "sync" tool that diffs
   models against the live database. Without migration files, the database
   structure becomes an undocumented side effect of running code. With them, it
   becomes a reviewed, ordered, reversible artifact — the same way Git turns
   code changes into something you can inspect and undo.

3. We might upgrade successfully but then be unable to downgrade, which is risky
   exactly when we are trying to recover from a bad deploy.
