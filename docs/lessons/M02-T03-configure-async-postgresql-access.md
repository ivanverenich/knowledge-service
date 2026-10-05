# M02-T03 — Configure async PostgreSQL access

Source task: [M02-T03](../curriculum/milestones/M02-domain-and-persistence/M02-T03-configure-async-postgresql-access.md)

## What you will do

This lesson includes coding. You will add SQLAlchemy 2 with Psycopg 3, place
database credentials and bounded connection settings in typed configuration,
and build one reusable asynchronous engine and session factory. A real
PostgreSQL integration test will exercise connection, commit, rollback,
statement timeout, and engine disposal behavior.

This follows M02-T02, which established provider-neutral Document and version
rules without persistence imports. The database module will own connection
resources; domain values will remain independent of SQLAlchemy. Each concurrent
request or job gets its own `AsyncSession`, because a session represents one
mutable transaction and must not be shared across concurrent tasks.

## Walkthrough

### Step 1 — Record the resource ownership rule

**Purpose:** Make the connection lifecycle clear before introducing an engine
that can hold sockets and pool connections.

**Decision:** The process owns one `AsyncEngine` and one session factory. A
request or job creates a fresh session from that factory and closes it through
an async context manager. The process explicitly awaits engine disposal during
shutdown. Tests use a dedicated PostgreSQL database; they never run against
production data.

**Action — No coding in this step:** Create
`docs/evidence/M02-T03-async-postgresql-access.md`. Write the invariant in your
own words, then record this prediction before coding:

```markdown
# M02-T03 — Async PostgreSQL access evidence

Completed: pending

## Invariant

Write who owns the engine, who owns each session, and when a transaction closes.

## Prediction

The first focused test command will fail because the database runtime and its
tests do not exist yet. An unconfigured local Settings object should be safe to
construct, but asking it to create a database runtime should raise a
project-owned configuration error.

## Verification

Pending.

## Reflection

Pending.
```

Read the M02-T02 evidence and confirm the domain module has no SQLAlchemy
imports. Check for existing database code and run the deliberately red test:

```bash
rg --files src/knowledge_service tests | rg 'database|postgres'
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_database.py
```

**Check:** There is no database runtime or test module yet, and pytest reports
that `tests/test_database.py` does not exist. Record the actual command result
briefly in the evidence file.

### Step 2 — Add the database packages and typed settings

**Purpose:** Keep connection secrets and resource limits in one validated
configuration object. Pool waiting, opening a network connection, and running a
SQL statement are different waits, so give each a finite bound.

**Decision:** Add SQLAlchemy 2 and Psycopg 3 as application dependencies. Use
Psycopg's binary extra for the local development runtime. Store the full
database URL as `SecretStr`; keep it optional in local/test configuration, but
require it in production. Set finite, positive defaults for pool acquisition,
connection, and PostgreSQL statement execution; never enable SQL echo.

**Action — Coding step:** Add these bounded dependency ranges from the project
root:

```bash
uv add 'sqlalchemy[asyncio]>=2,<3' 'psycopg[binary]>=3,<4'
```

This updates `pyproject.toml` and `uv.lock`. The Psycopg documentation calls
the package `psycopg` (not `psycopg3`) and documents the `binary` extra for a
self-contained local installation.

In `src/knowledge_service/settings.py`, import `Field`, add these fields to
`Settings`, and extend `require_production_secret()`:

```python
from pydantic import Field, SecretStr, model_validator
```

```python
    database_url: SecretStr | None = None
    database_pool_size: int = Field(default=5, gt=0)
    database_pool_max_overflow: int = Field(default=5, ge=0)
    database_pool_timeout: float = Field(default=10.0, gt=0, allow_inf_nan=False)
    database_connect_timeout_seconds: int = Field(default=5, gt=0)
    database_statement_timeout_ms: int = Field(default=10_000, gt=0)
```

Keep the current model-key check first, then add the database requirement:

```python
        if self.environment is Environment.PRODUCTION:
            if self.model_api_key is None:
                raise ValueError("MODEL_API_KEY is required in production")
            if self.database_url is None:
                raise ValueError("DATABASE_URL is required in production")
```

Replace the existing validator body with the version above; it preserves the
current check and adds the database URL requirement. `KNOWLEDGE_SERVICE_` is
already the environment prefix, so the new field reads
`KNOWLEDGE_SERVICE_DATABASE_URL` automatically. Add a commented placeholder to
`.env.example`, never a real password:

```dotenv
# Optional locally; required in production. Keep real credentials out of Git.
# KNOWLEDGE_SERVICE_DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/knowledge_service
```

Update `tests/test_settings.py`: provide a synthetic database URL in the
existing production environment override test, and add tests that production
without a database URL fails and that the configured URL is redacted in
`repr(settings)`. In `test_environment_overrides()`, add:

```python
monkeypatch.setenv(
    "KNOWLEDGE_SERVICE_DATABASE_URL",
    "postgresql://test_user:test_password@localhost:5432/test_db",
)
```

Then assert the parsed secret is present and redacted:

```python
assert settings.database_url is not None
assert str(settings.database_url) == "**********"
```

Add these two tests (with `SecretStr` already imported from `pydantic`):

```python
def test_missing_database_url_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_SERVICE_ENVIRONMENT", "production")
    monkeypatch.setenv("KNOWLEDGE_SERVICE_MODEL_API_KEY", "TEST_API_KEY")
    monkeypatch.delenv("KNOWLEDGE_SERVICE_DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="DATABASE_URL is required in production"):
        Settings()


def test_database_url_is_redacted() -> None:
    raw_url = "postgresql://test_user:test_password@localhost:5432/test_db"

    settings = Settings(database_url=SecretStr(raw_url))

    assert settings.database_url is not None
    assert str(settings.database_url) == "**********"
    assert raw_url not in repr(settings)
```

Keep the existing test that proves the local default needs no database URL.

**Check:** Run the settings tests:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_settings.py -vv
```

The local defaults still construct without a database URL. Production requires
both credentials; a synthetic URL does not appear in the settings repr.

### Step 3 — Build the engine and session lifecycle module

**Purpose:** Hide SQLAlchemy setup behind a small application-owned interface.
Callers get the session factory and a clear shutdown method, not duplicated
pool and driver configuration.

**Decision:** Add `src/knowledge_service/database.py`. Normalize accepted
PostgreSQL URL schemes to `postgresql+psycopg`, which selects SQLAlchemy's async
Psycopg implementation when passed to `create_async_engine()`. Configure
`pool_size`, `max_overflow`, `pool_timeout`, and `pool_pre_ping`. Pass Psycopg a
connection timeout and PostgreSQL a per-connection `statement_timeout`.
Translate missing or malformed URL configuration into a project-owned error
without including the URL in its message.

**Action — Coding step:** Create the module with these imports, error, runtime,
and factory:

```python
"""Asynchronous PostgreSQL engine and session lifecycle."""

from dataclasses import dataclass

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from knowledge_service.settings import Settings


class DatabaseConfigurationError(ValueError):
    """Database configuration is absent or cannot create a PostgreSQL runtime."""


@dataclass(frozen=True, slots=True)
class DatabaseRuntime:
    """Process-owned async engine and factory for per-operation sessions."""

    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    async def dispose(self) -> None:
        """Close pooled connections during application shutdown."""
        await self.engine.dispose()


def create_database_runtime(settings: Settings) -> DatabaseRuntime:
    """Create a lazy PostgreSQL runtime from validated settings."""
    if settings.database_url is None:
        raise DatabaseConfigurationError("DATABASE_URL is required")

    try:
        parsed_url: URL = make_url(settings.database_url.get_secret_value())
    except ArgumentError:
        raise DatabaseConfigurationError("DATABASE_URL is invalid") from None

    if parsed_url.drivername not in {
        "postgresql",
        "postgresql+psycopg",
        "postgresql+psycopg_async",
    }:
        raise DatabaseConfigurationError("DATABASE_URL must use PostgreSQL")

    async_url = parsed_url.set(drivername="postgresql+psycopg")
    engine = create_async_engine(
        async_url,
        echo=False,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": settings.database_connect_timeout_seconds,
            "options": (
                "-c statement_timeout="
                f"{settings.database_statement_timeout_ms}"
            ),
        },
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    return DatabaseRuntime(engine=engine, sessions=sessions)
```

`pool_timeout` bounds how long a caller waits for a free pooled connection;
`connect_timeout` bounds opening a new connection; PostgreSQL's
`statement_timeout` aborts an overlong statement on the server. These limits
cover separate parts of a database operation. SQLAlchemy's async session is a
mutable transaction object, so create one per operation with
`async with runtime.sessions() as session:`. For a transaction that commits on
success and rolls back on failure, use
`async with runtime.sessions.begin() as session:`.

**Check:** The module imports SQLAlchemy and the Psycopg dialect only at the
database boundary. Creating a runtime does not connect immediately; the first
session query opens a connection. The error messages do not include the secret
URL.

### Step 4 — Test configuration and runtime construction without a database

**Purpose:** Verify the safe local path, driver selection, and typed failure
without requiring PostgreSQL for every test run.

**Decision:** Keep fast unit tests separate from the real database integration
test. Constructing the engine is lazy, so a test can prove the selected dialect
is async and dispose the unused engine without making a network connection.

**Action — Coding step:** Create `tests/test_database.py`:

```python
"""Unit tests for database runtime construction."""

import pytest
from pydantic import SecretStr

from knowledge_service.database import (
    DatabaseConfigurationError,
    create_database_runtime,
)
from knowledge_service.settings import Settings


def test_runtime_requires_database_url() -> None:
    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL"):
        create_database_runtime(Settings())


async def test_runtime_uses_async_psycopg_and_disposes() -> None:
    settings = Settings(
        database_url=SecretStr(
            "postgresql://test_user:test_password@localhost:5432/test_db"
        )
    )

    runtime = create_database_runtime(settings)
    try:
        assert runtime.engine.url.drivername == "postgresql+psycopg"
        assert runtime.engine.dialect.is_async
    finally:
        await runtime.dispose()
```

The factory is deliberately exercised through `runtime.sessions` in the
integration test instead of asserting against SQLAlchemy's internal factory
configuration.

**Check:** Run the new unit tests and settings tests:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_database.py tests/test_settings.py -vv
```

They pass without PostgreSQL. The integration test remains separately marked
so its required real-database run is explicit.

### Step 5 — Prove transactions against a real PostgreSQL database

**Purpose:** A constructed engine does not prove network access, commit,
rollback, server deadlines, or cleanup. This integration test exercises those
behaviors against PostgreSQL itself.

**Decision:** Use only the explicit environment variable
`KNOWLEDGE_SERVICE_TEST_DATABASE_URL` for the integration test. Point it at a
dedicated disposable test database with a role that can connect and create
temporary tables. If that variable is absent, skip the test with a clear
reason; never silently reuse the production `DATABASE_URL`.

**Action — Coding step:** Create `tests/test_database_integration.py`:

```python
"""Real PostgreSQL tests for transaction and engine lifecycle."""

import os

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from knowledge_service.database import create_database_runtime
from knowledge_service.settings import Settings


@pytest.mark.integration
async def test_postgresql_commit_rollback_timeout_and_disposal() -> None:
    database_url = os.getenv("KNOWLEDGE_SERVICE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip("set KNOWLEDGE_SERVICE_TEST_DATABASE_URL to a disposable PostgreSQL database")

    settings = Settings(
        database_url=SecretStr(database_url),
        database_statement_timeout_ms=100,
    )
    runtime = create_database_runtime(settings)
    try:
        async with runtime.sessions() as session:
            await session.execute(
                text(
                    "CREATE TEMPORARY TABLE m02_t03_probe "
                    "(value integer) ON COMMIT PRESERVE ROWS"
                )
            )
            await session.execute(
                text("INSERT INTO m02_t03_probe (value) VALUES (1)")
            )
            await session.commit()

            await session.execute(
                text("INSERT INTO m02_t03_probe (value) VALUES (2)")
            )
            await session.rollback()

            count = await session.scalar(
                text("SELECT count(*) FROM m02_t03_probe")
            )
            assert count == 1

            with pytest.raises(DBAPIError):
                await session.execute(text("SELECT pg_sleep(0.25)"))
            await session.rollback()
    finally:
        await runtime.dispose()
```

The temporary table is scoped to its PostgreSQL connection and disappears when
the engine closes its pool. The first row proves commit; the rolled-back second
row is absent. `pg_sleep` deliberately exceeds the configured 100 ms statement
limit and verifies the server stops the query. The `finally` block disposes the
engine even if an assertion or query fails.

Start or use a local PostgreSQL server and create a dedicated test database.
Set the test URL in your shell without adding it to a tracked file, then run:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL='postgresql://test_user:test_password@localhost:5432/knowledge_service_test'
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration tests/test_database_integration.py -vv
unset KNOWLEDGE_SERVICE_TEST_DATABASE_URL
```

Replace the example values with your local test credentials. Do not paste a
real connection string into evidence, shell transcripts, or source files. The
normal `make check` suite should report a skip when the test URL is unset; the
explicit integration command must report the test as passed against a real
PostgreSQL server before this lesson is complete.

**Check:** The explicit integration command passes. Confirm the temporary
table is gone after engine disposal and the test did not create permanent
objects.

### Step 6 — Run the repository gates and record evidence

**Purpose:** Confirm the runtime follows repository standards and leave the
next persistence task with tested connection/session ownership.

**Decision:** Run the focused unit tests, real PostgreSQL integration test,
repository gates, documentation validation, and hooks. Do not add ORM models or
migrations; those are later tasks.

**Action — No coding in this step:** Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_database.py tests/test_settings.py -vv
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration tests/test_database_integration.py -vv
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
git diff --check
```

Fill `docs/evidence/M02-T03-async-postgresql-access.md` with the exact command
results, changed files, the integration database version (not its URL), and
your reflection in your own words:

1. What connection or transaction complexity does the runtime hide from its
   callers?
2. Which alternative would make M02-T04 migrations or later transaction
   ownership harder, and why?
3. What production failure could escape if the rollback or timeout test were
   removed?

**Check:** The integration test passes against real PostgreSQL; all other
commands pass; the evidence contains no connection string, password, or private
data.

## Completion checklist

- [ ] SQLAlchemy 2 and Psycopg 3 are locked as application dependencies.
- [ ] The database URL is typed and redacted; production requires it.
- [ ] Pool size, overflow, checkout timeout, connect timeout, and statement
  timeout have finite validated settings.
- [ ] One async PostgreSQL engine and session factory are created at the
  database boundary.
- [ ] Sessions are scoped to one operation and engine disposal is explicit.
- [ ] Unit tests prove configuration, driver selection, and typed failure.
- [ ] A real PostgreSQL test proves connection, commit, rollback, timeout, and
  disposal behavior.
- [ ] Focused tests and all repository gates pass; evidence and reflection are
  complete.

When you finish Step 1, share the predicted failure or any environment issue
with PostgreSQL and we can work through it step by step.

## Primary references

- [SQLAlchemy 2.0 asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) — async engines, session factories, and disposal.
- [SQLAlchemy PostgreSQL dialect](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#psycopg) — Psycopg 3's sync/async driver selection.
- [SQLAlchemy session basics](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#is-the-session-thread-safe-is-asyncsession-safe-to-share-in-concurrent-tasks) — one `AsyncSession` per concurrent task.
- [Psycopg installation](https://www.psycopg.org/psycopg3/docs/basic/install.html) — binary development installation and platform requirements.
- [PostgreSQL statement timeout](https://www.postgresql.org/docs/current/runtime-config-client.html#GUC-STATEMENT-TIMEOUT) — server-side statement deadline.
