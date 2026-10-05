# M02-T03 — Async PostgreSQL access evidence

Completed: 2026-10-05

## Invariant

The application process owns one AsyncEngine and one session factory. Each request or job creates its own AsyncSession, which is closed when the work finishes, whether it succeeds or fails.

## Prediction

I expected the first focused test command to fail before the database runtime
and its tests existed. The observed collection failure was more specific:
SQLAlchemy's asyncio module could not import `greenlet`. Adding SQLAlchemy's
`asyncio` dependency extra supplied that runtime dependency. An unconfigured
local Settings object remains safe to construct, while asking it to create a
database runtime raises a project-owned configuration error.

## Verification

- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_database.py tests/test_settings.py -vv` — 14 passed.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<ephemeral disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration tests/test_database_integration.py -vv` — 1 passed against PostgreSQL 17.11. It verified commit, rollback, the 100 ms statement timeout, and that disposal causes the next checkout to receive a different PostgreSQL backend connection. No connection URL or credentials are recorded.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make check` — Ruff format and lint passed; Pyright reported 0 errors, 0 warnings, 0 informations; 177 passed, 1 skipped (the opt-in PostgreSQL test), 2 deselected; total coverage 92%.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks` — all pre-commit hooks passed, including secret scanning, formatting, lint, type checking, and deterministic tests.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` — passed.
- `git diff --check` — passed.

Implementation files: `.env.example`, `pyproject.toml`, `uv.lock`,
`src/knowledge_service/database.py`, `src/knowledge_service/settings.py`,
`tests/test_database.py`, `tests/test_database_integration.py`,
`tests/test_settings.py`, and `docs/lessons/M02-T03-configure-async-postgresql-access.md`.
The integration test reads only the dedicated test-database variable and used
synthetic credentials in a disposable container. No credentials, connection
strings, or private content were added to tracked artifacts.

## Reflection

1. It creates the only engine, sets it up, and that creates sets of connections to be reused by callers. This complexity is hidden behind DatabaseRuntime object.

2. Now each request/job has it's own session, so it can make transactions inside the session. Othersiwe every request/job could create it's own engine, that would lead to resource depletion

3. If the rollback test were removed, broken transaction handling could accidentally persist changes that should have been undone; if the timeout test were removed, long-running queries could hang requests and tie up database connections in production.
