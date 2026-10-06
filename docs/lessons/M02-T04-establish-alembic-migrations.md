# M02-T04 — Establish Alembic migrations

Source task: [M02-T04](../curriculum/milestones/M02-domain-and-persistence/M02-T04-establish-alembic-migrations.md)

## What you will build

This lesson includes coding. You will add Alembic, the tool this project will
use to keep database schema changes in ordered, reviewable files. You will
create a reversible starting revision and test it against a disposable,
initially empty PostgreSQL database.

M02-T03 gave the application one async PostgreSQL engine and session factory.
This lesson adds a separate, explicit path for changing database structure.
The first revision will intentionally create no application tables because
this project has no database models yet. Alembic will still record that the
baseline revision was applied. Later lessons can add reviewed table changes
as new revisions.

The key rule is: application startup does not create or update tables by
inspecting model definitions. Only an explicitly run Alembic revision changes
the schema. Alembic's async template uses SQLAlchemy's async engine and
`run_sync()` to run Alembic's migration functions; Alembic itself does not
provide a separate async migration API. See the [official async Alembic
example](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic).

## Walkthrough

### Step 1 — No coding in this step: write down the safety rule

**Purpose:** Make the important rule clear before adding migration commands
that can change a database.

**Decision:** Alembic revision files are the only way this project changes
database structure. The application will not call `metadata.create_all()` or
automatically apply revisions when it starts.

**Action:** Read
[`M02-T03 evidence`](../evidence/M02-T03-async-postgresql-access.md), then
create `docs/evidence/M02-T04-alembic-migrations.md` with this starting text.
Write the invariant in your own words and predict what the first Alembic
command will report before adding Alembic.

```markdown
# M02-T04 — Alembic migrations evidence

Completed: pending

## Invariant

Write down how this project is allowed to change database tables, and what
must not happen automatically when the application starts.

## Prediction

Write what you expect before installing or running Alembic.

## Verification

Pending.

## Reflection

Pending.
```

From the repository root, confirm there is not already an Alembic environment
or migration directory:

```bash
rg --files | rg '(^|/)(alembic\.ini|migrations/)'
```

No matches are expected. Do not add code or edit the task/progress files in this
step.

**Check:** The evidence file contains your invariant and prediction, and the
file search confirms migrations have not already been set up.

### Step 2 — Coding step: install Alembic and create its environment

**Purpose:** Add the migration tool and its standard files. A migration
environment is the configuration and revision directory Alembic uses for
commands.

**Decision:** Use Alembic's official `async` template because this project
connects to PostgreSQL through SQLAlchemy's async Psycopg driver. The template
provides the online migration path; the next step will connect it to the
project's existing `DatabaseRuntime`.

**Action:** From the repository root, add Alembic as an application
dependency, then initialize the environment:

```bash
uv add 'alembic>=1.20,<2'
uv run alembic init -t async migrations
```

The current Alembic release is 1.20.0; the range keeps this project on the
current major version while `uv.lock` records the exact resolved version. The
official [Alembic tutorial](https://alembic.sqlalchemy.org/en/latest/tutorial.html#the-migration-environment)
describes the generated `alembic.ini`, `env.py`, and revision directory.

The command should create `alembic.ini`, `migrations/env.py`,
`migrations/script.py.mako`, and `migrations/versions/`. Keep these generated
files in the repository. Do not put a real database URL in `alembic.ini`.

**Check:** `uv run alembic --version` prints an Alembic 1.x version, and
`rg --files migrations` lists the generated environment files.

### Step 3 — Coding step: use the application's database settings

**Purpose:** Make command-line migrations use the same validated PostgreSQL
URL and async engine setup as the application, without saving credentials in
the Alembic config file.

**Decision:** Reuse `create_database_runtime(Settings())` from
`src/knowledge_service/database.py`. This keeps driver selection, connection
timeouts, and secret handling in the existing database boundary instead of
copying that setup into migration code.

**Action:** In `alembic.ini`, clear the generated sample URL so it contains no
credentials:

```ini
sqlalchemy.url =
```

In `migrations/env.py`, keep the generated `target_metadata = None`. Add these
imports:

```python
from knowledge_service.database import create_database_runtime
from knowledge_service.settings import Settings
```

In the generated `run_async_migrations()` function, replace the code that
builds an engine from `alembic.ini` with this code:

```python
async def run_async_migrations() -> None:
    runtime = create_database_runtime(Settings())
    try:
        async with runtime.engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await runtime.dispose()
```

Keep the generated `do_run_migrations()` function and the call to
`asyncio.run(run_async_migrations())`. Remove generated imports that are now
unused, such as `async_engine_from_config` and `pool`. The `Settings` object
reads `KNOWLEDGE_SERVICE_DATABASE_URL`; `create_database_runtime` validates
that a URL is configured and chooses the async Psycopg driver. `finally`
ensures migration connections are disposed even if a revision fails.

This setup intentionally uses online migrations (connected to PostgreSQL).
Offline SQL generation is not configured in this lesson; use the online
commands below with the dedicated disposable database.

**Check:** With
`KNOWLEDGE_SERVICE_DATABASE_URL` unset, an Alembic upgrade command should fail
with the project's `DatabaseConfigurationError` and no URL or password in the
error. Ruff should report no unused imports in `migrations/env.py`.

### Step 4 — Coding step: add a reversible baseline

**Purpose:** Give the database a first recorded revision so later changes have
a clear starting point.

**Decision:** Create a root revision with no table operations. There are no
SQLAlchemy models or application tables yet, so this revision records the
baseline without guessing at future schema. Alembic still records the applied
revision in its own `alembic_version` table.

**Action:** Generate a revision without `--autogenerate`:

```bash
uv run alembic revision -m "baseline"
```

Open the generated file under `migrations/versions/`. Keep Alembic's generated
`revision` identifier and `down_revision = None`. Remove unused `op`,
`sqlalchemy`, or typing imports from the generated file, then make both
functions explicit no-ops:

```python
def upgrade() -> None:
    """Record the starting point; later revisions add application tables."""
    pass


def downgrade() -> None:
    """Return to the state before the baseline revision."""
    pass
```

Do not add `metadata.create_all()` to `env.py` or this revision. With
`target_metadata = None`, Alembic has no model metadata to turn into automatic
schema changes. The baseline is reviewed and explicit; future lessons can add
real table operations. Alembic's [autogenerate documentation](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)
also describes generated migrations as candidates that require review.

**Check:** `uv run alembic history` shows one root revision named `baseline`,
and the revision has `down_revision = None` with no schema operations.

### Step 5 — Coding step: test the revision and its failure case

**Purpose:** Catch a missing or incorrectly connected baseline before trying
to change a real database.

**Decision:** Test Alembic's public configuration and revision interfaces.
The missing-URL test calls Alembic directly and expects the existing typed
configuration error, so it does not need PostgreSQL.

**Action:** Create `tests/test_migrations.py`:

```python
"""Tests for the Alembic migration environment."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from knowledge_service.database import DatabaseConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def migration_config() -> Config:
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def test_baseline_is_the_single_root_revision() -> None:
    scripts = ScriptDirectory.from_config(migration_config())

    heads = scripts.get_heads()
    assert len(heads) == 1
    baseline = scripts.get_revision(heads[0])
    assert baseline is not None
    assert baseline.down_revision is None


def test_upgrade_requires_a_database_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("KNOWLEDGE_SERVICE_ENVIRONMENT", "local")
    monkeypatch.delenv("KNOWLEDGE_SERVICE_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL"):
        command.upgrade(migration_config(), "head")
```

`tmp_path` gives the test an empty directory, so a developer's local `.env`
file cannot accidentally supply a real database URL. The test uses an absolute
path to `alembic.ini`, so it still finds the migration environment after
changing directories.

**Check:** Run the focused test:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_migrations.py -vv
```

Both tests should pass without a database server.

### Step 6 — Coding step: verify upgrade, downgrade, and re-upgrade in PostgreSQL

**Purpose:** Prove the Alembic environment can connect through the app's async
runtime and safely apply its baseline in the real database.

**Decision:** Use only `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` for this
integration test. It must point to a dedicated disposable PostgreSQL database,
never production. The test temporarily maps that URL to the app setting while
calling Alembic; it does not read or fall back to a production URL.

**Action:** Create `tests/test_migrations_integration.py`:

```python
"""PostgreSQL integration test for the baseline migration."""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_baseline_can_upgrade_downgrade_and_upgrade_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.getenv("KNOWLEDGE_SERVICE_TEST_DATABASE_URL")
    if database_url is None:
        pytest.skip(
            "set KNOWLEDGE_SERVICE_TEST_DATABASE_URL to an empty disposable "
            "database"
        )

    monkeypatch.setenv("KNOWLEDGE_SERVICE_DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    baseline_id = ScriptDirectory.from_config(config).get_current_head()
    assert baseline_id is not None
    sync_url = make_url(database_url).set(drivername="postgresql+psycopg")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as connection:
            existing_tables = set(inspect(connection).get_table_names())
            assert existing_tables <= {"alembic_version"}
            if "alembic_version" in existing_tables:
                current_id = connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                assert current_id in (None, baseline_id)

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert set(inspect(connection).get_table_names()) == {"alembic_version"}
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == baseline_id

        command.downgrade(config, "base")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) is None

        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == baseline_id
    finally:
        engine.dispose()
```

The only database object this baseline should create is Alembic's own version
table. The test accepts a fresh database or the version table left by a prior
run, but rejects unrelated tables and unknown migration revisions. The
temporary mapping to `KNOWLEDGE_SERVICE_DATABASE_URL` is scoped to this test;
all migrations still go through `create_database_runtime(Settings())`.

Run it with the test URL set only in your shell. Use a fresh, empty disposable
database for the first run; if using Docker, keep the container and credentials
temporary as you did for M02-T03.

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_migrations_integration.py -vv
```

The test should report one pass. It never drops tables or changes an existing
application schema. Do not paste the connection URL into the evidence file or
commit it.

### Step 7 — No coding in this step: run repository checks and record evidence

**Purpose:** Confirm the lesson works as a whole and leave M02-T05 with a
reviewable migration starting point.

**Decision:** Run the focused tests first, then the repository's configured
format, lint, type, test, documentation, and hook checks. The normal check may
skip the opt-in PostgreSQL test when its test URL is not configured; the
explicit integration command above must pass separately.

**Action:** Run these commands from the repository root:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_migrations.py -vv
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration tests/test_migrations_integration.py -vv
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
git diff --check
```

In `docs/evidence/M02-T04-alembic-migrations.md`, record the date, invariant,
files changed, exact observed command results, PostgreSQL version, and the
answers to the reflection questions below. Do not include credentials, the
connection URL, or private data.

**Reflection — answer in your own words:**

1. What does Alembic record after it applies a migration?
2. How could we have changed database tables without migration files, and what
   would become harder as this project grows?
3. If the test for undoing a migration were removed, what production problem
   might we fail to notice until it is too late?

**Check:** The PostgreSQL integration test passes through upgrade, downgrade,
and re-upgrade; every repository gate passes; evidence contains the actual
results and no connection secret.

## Completion checklist

- [ ] Alembic is installed and locked as a project dependency.
- [ ] `env.py` uses the project's validated async PostgreSQL runtime.
- [ ] No database URL or password is stored in `alembic.ini` or a migration.
- [ ] Alembic has one root baseline revision with explicit upgrade and
  downgrade functions and no automatic table creation.
- [ ] Unit tests cover the revision chain and missing database configuration.
- [ ] A disposable PostgreSQL test passes upgrade, downgrade, and re-upgrade.
- [ ] Repository checks pass and the evidence records real results.

When you have completed the first step, share your prediction or any issue
setting up an empty disposable PostgreSQL database. I can help you work
through it without taking over the implementation.

## Official references

- [Alembic async template and `run_sync()` pattern](https://alembic.sqlalchemy.org/en/latest/cookbook.html#using-asyncio-with-alembic)
- [Alembic migration environment](https://alembic.sqlalchemy.org/en/latest/tutorial.html#the-migration-environment)
- [Alembic autogeneration and review](https://alembic.sqlalchemy.org/en/latest/autogenerate.html)
- [Alembic releases](https://pypi.org/project/alembic/)
