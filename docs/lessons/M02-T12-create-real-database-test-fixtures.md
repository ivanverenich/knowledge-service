# M02-T12 — Create real-database test fixtures

Source task: [M02-T12](../curriculum/milestones/M02-domain-and-persistence/M02-T12-create-real-database-test-fixtures.md)

## What you will build

This lesson includes coding. It gives the integration tests one shared PostgreSQL fixture instead of nine copies of the same setup, and it makes migration drift fail loudly instead of silently.

Three pieces:

- `tests/database_fixtures.py`, the helpers that create, migrate, and drop a database on a real server.
- `tests/conftest.py`, which gives every worker its own database and empties it before each test.
- A drift check that compares the migrated schema against the tables the code declares.

| Property | What it means |
|---|---|
| One database per worker | Two pytest workers never delete each other's rows. |
| No mocks | Every helper talks to a real server; nothing replaces SQL with a fake. |
| Drift fails visibly | A revision that no longer matches the declared tables fails the suite. |

## Before you begin

- Nine integration files each resolve `KNOWLEDGE_SERVICE_TEST_DATABASE_URL`, and six of them define the same `migrated_database` fixture. This lesson removes that duplication.
- `migrations/env.py` runs Alembic through `asyncio.run()`, so a migration cannot be applied from inside a running event loop. The fixture that migrates must be synchronous.
- `persistence.py` owns the declared table shapes. The migrations and those tables have been kept in step by hand since M02-T04.
- `tests/fakes.py` shows how a shared test module is imported: the tests directory is on the path, so `from database_fixtures import ...` works.

## Walkthrough

### Step 1 — No coding in this step: write down the rule you are preserving

**Purpose:** Fix what the fixtures must guarantee before writing them.

**Decision:** Keep notes in `docs/evidence/M02-T12-real-database-test-fixtures.md`.

**Action:** Read the M02-T11 evidence file under `docs/evidence/`. Create `docs/evidence/M02-T12-real-database-test-fixtures.md`:

```markdown
# M02-T12 — Real-database test fixtures evidence

Completed: pending

## Invariant

Write down how two workers stay apart, what state a test starts from, and what
happens when a migration no longer matches the tables the code declares.

## Prediction

Write what you expect before adding the fixtures.

## Verification

Pending.

## Reflection

Pending.
```

Confirm what exists today:

```bash
grep -rn "def migrated_database" tests/*.py | grep -v __pycache__
grep -rln "KNOWLEDGE_SERVICE_TEST_DATABASE_URL" tests/*.py
```

**Check:** The evidence file holds your invariant and prediction, and the searches show the duplication you are about to remove. Add no code and edit no task or progress files in this step.

### Step 2 — Coding step: give drift something to compare against

**Purpose:** Make the migrated schema and the declared tables comparable, which is what drift detection needs.

**Decision:** Two changes. `persistence.py` must declare every table the migrations create; `sources` and `synchronization_runs` were missing, so `documents.source_id` had no table to point at and the comparison could not even run. Then `migrations/env.py` sets `target_metadata`, so Alembic can compare.

**Action:** In `src/knowledge_service/persistence.py`, add both tables directly after `metadata = sa.MetaData()`:

```python
sources = sa.Table(
    "sources",
    metadata,
    sa.Column("source_id", sa.Uuid(), primary_key=True),
    sa.Column("kind", sa.String(length=32), nullable=False),
    sa.Column("location", sa.Text(), nullable=False),
    sa.Column("credentials_reference", sa.Text(), nullable=True),
    sa.Column("display_name", sa.Text(), nullable=False),
    sa.Column("enabled", sa.Boolean(), nullable=False),
    sa.Column("checkpoint", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("kind", "location", name="uq_sources_kind_location"),
)

synchronization_runs = sa.Table(
    "synchronization_runs",
    metadata,
    sa.Column("run_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "source_id",
        sa.Uuid(),
        sa.ForeignKey("sources.source_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("status", sa.String(length=16), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("detail", sa.Text(), nullable=True),
    sa.CheckConstraint(
        "status in ('running', 'succeeded', 'failed')",
        name="ck_synchronization_runs_status",
    ),
    sa.CheckConstraint(
        "(status = 'running') = (finished_at is null)",
        name="ck_synchronization_runs_finished_at",
    ),
)
```

Then in `migrations/env.py`, import the metadata and point Alembic at it:

```python
from knowledge_service.persistence import metadata
```

```python
target_metadata = metadata
```

**Check:** Alembic now finds nothing to change, which means the migrations and the declared tables agree:

```bash
KNOWLEDGE_SERVICE_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic upgrade head
KNOWLEDGE_SERVICE_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic check
```

### Step 3 — Coding step: the database helpers

**Purpose:** Put the server work in one place: naming a worker's database, creating it, migrating it, and dropping it.

**Decision:** `worker_database_name()` keys the database on the pytest worker id, so `gw0` and `gw1` never meet. The worker name is validated against a plain-word pattern before it reaches a `CREATE DATABASE` statement, which is the one place a name cannot be a bound parameter. `migration_environment()` sets the URL variable only while a migration command runs, because leaving it set would change what later tests see — including the deterministic test that asserts a missing URL raises.

**Action:** Create `tests/database_fixtures.py`:

```python
"""Shared PostgreSQL test-database helpers.

One pytest worker owns one database, so two workers running at the same time
never share a row. Every helper talks to a real PostgreSQL server; nothing here
replaces SQL with a fake.
"""

import os
import re
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL_ENV = "KNOWLEDGE_SERVICE_TEST_DATABASE_URL"
MIGRATION_URL_ENV = "KNOWLEDGE_SERVICE_DATABASE_URL"
ADMIN_DATABASE = "postgres"
SYNC_DRIVER = "postgresql+psycopg"

_WORKER_NAME = re.compile(r"[a-z0-9]+")


class InvalidWorkerName(ValueError):
    """A pytest worker name cannot be used in a database name."""


def worker_database_name(base_name: str, worker: str | None) -> str:
    """Return the database one worker owns, so two workers never share rows."""
    if worker is None or worker == "master":
        return base_name
    if _WORKER_NAME.fullmatch(worker) is None:
        raise InvalidWorkerName(f"{worker!r} is not usable in a database name")
    return f"{base_name}_{worker}"


def worker_database_url(base_url: URL, worker: str | None) -> URL:
    """Point a configured URL at the database this worker owns."""
    base_name = base_url.database
    if not base_name:
        raise InvalidWorkerName("the test database URL must name a database")
    return base_url.set(
        database=worker_database_name(base_name, worker),
        drivername=SYNC_DRIVER,
    )


def database_url_string(url: URL) -> str:
    """Render a URL for callers that need the credentials in the string."""
    return url.render_as_string(hide_password=False)


@contextmanager
def migration_environment(url: URL) -> Generator[None]:
    """Point the Alembic environment at one database, then put it back.

    The variable is process-wide, so it is set only while a migration command
    runs. Leaving it set would change what every later test sees.
    """
    previous = os.environ.get(MIGRATION_URL_ENV)
    os.environ[MIGRATION_URL_ENV] = database_url_string(url)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(MIGRATION_URL_ENV, None)
        else:
            os.environ[MIGRATION_URL_ENV] = previous


def migration_config() -> Config:
    """Build an Alembic config for the repository migrations."""
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def _admin_engine(url: URL) -> Engine:
    """Open one autocommit engine on the server's admin database."""
    admin_url = url.set(database=ADMIN_DATABASE, drivername=SYNC_DRIVER)
    return create_engine(admin_url, isolation_level="AUTOCOMMIT")


def create_database(url: URL) -> None:
    """Create the database when the server does not already have it."""
    name = url.database
    if not name:
        raise InvalidWorkerName("the test database URL must name a database")

    engine = _admin_engine(url)
    try:
        with engine.connect() as connection:
            present = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if present is None:
                # The name comes from the operator's URL and a validated worker
                # id, and identifiers cannot be bound as parameters.
                connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        engine.dispose()


def drop_database(url: URL) -> None:
    """Drop the database, ending any connection still using it."""
    name = url.database
    if not name:
        raise InvalidWorkerName("the test database URL must name a database")

    engine = _admin_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                    " WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            # See create_database: a validated identifier cannot be bound.
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    finally:
        engine.dispose()


def migrate(url: URL) -> None:
    """Apply every revision to one database."""
    with migration_environment(url):
        command.upgrade(migration_config(), "head")


def check_migrations(url: URL) -> None:
    """Fail when the migrated schema drifts from the tables the code declares."""
    with migration_environment(url):
        command.check(migration_config())
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright tests/database_fixtures.py
```

### Step 4 — Coding step: the shared fixtures

**Purpose:** Give every integration test a migrated, empty database of its own without repeating the setup.

**Decision:** Three fixtures. `disposable_database_url` reads the URL once and skips the whole integration suite when it is unset. `migrated_test_database` runs once per session, creating and migrating this worker's database. `migrated_database` runs per test, re-applies any missing revisions and empties every declared table, so a test that downgrades cannot break the next one.

**Action:** Create `tests/conftest.py`:

```python
"""Shared PostgreSQL fixtures for the integration tests.

One pytest worker owns one database. Without the worker split, two workers
running at the same time would delete each other's rows, because every test
starts from an empty schema.
"""

import os

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import URL, make_url

from database_fixtures import (
    DATABASE_URL_ENV,
    SYNC_DRIVER,
    create_database,
    database_url_string,
    migrate,
    worker_database_url,
)
from knowledge_service.persistence import metadata


@pytest.fixture(scope="session")
def disposable_database_url() -> URL:
    """The configured disposable server, or skip every integration test."""
    configured = os.environ.get(DATABASE_URL_ENV)
    if not configured:
        pytest.skip(f"set {DATABASE_URL_ENV} to a disposable PostgreSQL server")
    return make_url(configured)


@pytest.fixture(scope="session")
def migrated_test_database(disposable_database_url: URL) -> str:
    """Create and migrate the database this worker owns, once per session."""
    url = worker_database_url(
        disposable_database_url, os.environ.get("PYTEST_XDIST_WORKER")
    )
    create_database(url)
    migrate(url)
    return database_url_string(url)


def delete_every_row(database_url: str) -> None:
    """Empty every table this repository declares, in dependency order.

    This runs synchronously: Alembic's environment owns the event loop, so a
    migration cannot be applied from inside a running one.
    """
    engine: Engine = create_engine(make_url(database_url).set(drivername=SYNC_DRIVER))
    try:
        with engine.begin() as connection:
            for table in reversed(metadata.sorted_tables):
                connection.execute(sa.delete(table))
    finally:
        engine.dispose()


@pytest.fixture
def migrated_database(migrated_test_database: str) -> str:
    """A migrated, empty database that no other worker shares."""
    migrate(make_url(migrated_test_database))
    delete_every_row(migrated_test_database)
    return migrated_test_database
```

`delete_every_row` walks `reversed(metadata.sorted_tables)`, which is dependency order, so a table is emptied after everything that points at it.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_persistence.py -q
```

### Step 5 — Coding step: move the integration files onto the fixture

**Purpose:** Delete the nine copies of the same setup.

**Decision:** Each integration file keeps its own test body and loses everything around it: the `PROJECT_ROOT` constant, the `migrated_database` fixture, the skip, the `command.upgrade` call, and the row deletes. The fixture name does not change, so no test signature moves.

**Action:** For each integration file, delete the `migrated_database` fixture, the `PROJECT_ROOT` constant, any `os.environ` and `monkeypatch.setenv` handling, and the `DELETE FROM` statements. The shape of one converted file, `tests/test_sources_integration.py`:

```python
"""Real PostgreSQL tests for the Source and Synchronization Run tables."""

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from database_fixtures import SYNC_DRIVER

INSERT_SOURCE = text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, :kind, :location, 'Handbook',"
    " true, now(), now())"
)

INSERT_RUN = text(
    "INSERT INTO synchronization_runs (run_id, source_id, status, started_at,"
    " finished_at) VALUES (:run_id, :source_id, :status, now(), :finished_at)"
)


@pytest.mark.integration
def test_constraints_reject_ambiguous_identity_and_invalid_run_state(
    migrated_database: str,
) -> None:
    engine = create_engine(make_url(migrated_database).set(drivername=SYNC_DRIVER))
    try:
        source_id = uuid.uuid4()
        with engine.begin() as connection:
            connection.execute(
                INSERT_SOURCE,
                {
                    "source_id": source_id,
                    "kind": "local_directory",
                    "location": "/srv/handbook",
                },
            )

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                INSERT_SOURCE,
                {
                    "source_id": uuid.uuid4(),
                    "kind": "local_directory",
                    "location": "/srv/handbook",
                },
            )

        with engine.begin() as connection:
            connection.execute(
                INSERT_SOURCE,
                {
                    "source_id": uuid.uuid4(),
                    "kind": "confluence",
                    "location": "/srv/handbook",
                },
            )

        with engine.begin() as connection:
            connection.execute(
                INSERT_RUN,
                {
                    "run_id": uuid.uuid4(),
                    "source_id": source_id,
                    "status": "running",
                    "finished_at": None,
                },
            )

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                INSERT_RUN,
                {
                    "run_id": uuid.uuid4(),
                    "source_id": source_id,
                    "status": "succeeded",
                    "finished_at": None,
                },
            )

        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                INSERT_RUN,
                {
                    "run_id": uuid.uuid4(),
                    "source_id": source_id,
                    "status": "abandoned",
                    "finished_at": None,
                },
            )
    finally:
        engine.dispose()
```

Two details are worth keeping in mind. A `with engine.begin()` block whose only statement was a delete becomes empty and must go with it. And `test_publishing_integration.py` built its runtime from `Settings()`, which read the URL from the environment that the fixture no longer leaves set; it now names the database it publishes into:

```python
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
```

**Check:** Nothing but the conftest defines the fixture, and one place reads the URL:

```bash
grep -rn "def migrated_database" tests/*.py
grep -rln "KNOWLEDGE_SERVICE_TEST_DATABASE_URL" tests/*.py
```

### Step 6 — Coding step: test the helpers without a database

**Purpose:** Pin down the naming rule, which is the whole isolation mechanism.

**Decision:** Test the pure part. The successful case is a worker getting its own database. The edge cases are a run without workers and a URL whose credentials and host must survive the change. The typed failure is a worker name that is not a plain word.

**Action:** Create `tests/test_database_fixtures.py`:

```python
"""Tests for the PostgreSQL test-database helpers."""

import pytest
from sqlalchemy.engine import make_url

from database_fixtures import (
    InvalidWorkerName,
    worker_database_name,
    worker_database_url,
)

BASE_URL = "postgresql://test_user:secret@localhost:5433/knowledge_service_test"


def test_a_worker_gets_its_own_database() -> None:
    assert worker_database_name("knowledge_service_test", "gw3") == (
        "knowledge_service_test_gw3"
    )


def test_a_run_without_workers_uses_the_configured_database() -> None:
    assert worker_database_name("knowledge_service_test", None) == (
        "knowledge_service_test"
    )
    assert worker_database_name("knowledge_service_test", "master") == (
        "knowledge_service_test"
    )


def test_only_the_database_name_changes() -> None:
    base = make_url(BASE_URL)

    worker = worker_database_url(base, "gw0")

    assert worker.database == "knowledge_service_test_gw0"
    assert worker.host == base.host
    assert worker.port == base.port
    assert worker.username == base.username
    assert worker.password == base.password


def test_a_worker_name_that_is_not_a_plain_word_is_rejected() -> None:
    with pytest.raises(InvalidWorkerName, match="not usable"):
        worker_database_name("knowledge_service_test", "gw-0; drop database")
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_database_fixtures.py -vv
```

Expected: 4 passed, without a database.

### Step 7 — Coding step: make drift fail visibly

**Purpose:** Prove the check passes on a clean database and fails on a drifted one.

**Decision:** Two tests. The first runs `command.check`, which compares the migrated schema with the declared tables and raises when they differ. The second proves that check can fail: it creates a database of its own, drops a column from a migrated table, and expects the raise. It uses a separate database so a deliberate act of drift cannot leak into the tests that follow.

**Action:** Replace `tests/test_migrations_integration.py`:

```python
"""PostgreSQL integration tests for the migration chain and its drift."""

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import AutogenerateDiffsDetected
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from database_fixtures import (
    check_migrations,
    create_database,
    drop_database,
    migrate,
    migration_config,
    migration_environment,
    worker_database_url,
)


def baseline_revision(config: Config) -> str:
    """Return the root revision identifier, which new revisions may follow."""
    scripts = ScriptDirectory.from_config(config)
    for revision in scripts.walk_revisions():
        if revision.down_revision is None:
            return revision.revision
    raise AssertionError("the migration chain has no baseline revision")


def stored_revision(engine_url: str) -> str | None:
    """Read the revision the database is currently at."""
    engine = create_engine(engine_url)
    try:
        with engine.connect() as connection:
            return connection.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        engine.dispose()


def table_names(engine_url: str) -> set[str]:
    """Read the table names the database currently holds."""
    engine = create_engine(engine_url)
    try:
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())
    finally:
        engine.dispose()


@pytest.mark.integration
def test_baseline_can_upgrade_downgrade_and_upgrade_again(
    migrated_database: str,
) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    baseline_id = baseline_revision(config)

    with migration_environment(url):
        command.downgrade(config, "base")
    assert table_names(migrated_database) == {"alembic_version"}
    assert stored_revision(migrated_database) is None

    with migration_environment(url):
        command.upgrade(config, baseline_id)
    assert table_names(migrated_database) == {"alembic_version"}
    assert stored_revision(migrated_database) == baseline_id

    with migration_environment(url):
        command.downgrade(config, "base")
    assert stored_revision(migrated_database) is None

    with migration_environment(url):
        command.upgrade(config, baseline_id)
    assert stored_revision(migrated_database) == baseline_id


@pytest.mark.integration
def test_the_migrated_schema_matches_the_declared_tables(
    migrated_database: str,
) -> None:
    """Every revision applied leaves behind the schema the code declares."""
    check_migrations(make_url(migrated_database))


@pytest.mark.integration
def test_a_column_missing_from_the_database_is_reported(
    migrated_database: str,
) -> None:
    """Prove the drift check can fail, on a database of its own."""
    drift_url = worker_database_url(make_url(migrated_database), "drift")
    create_database(drift_url)
    try:
        migrate(drift_url)
        engine = create_engine(drift_url)
        try:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE jobs DROP COLUMN last_error_code"))
        finally:
            engine.dispose()

        with pytest.raises(AutogenerateDiffsDetected):
            check_migrations(drift_url)
    finally:
        drop_database(drift_url)
```

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_migrations_integration.py -vv
```

Expected: 3 passed. Do not paste the URL into the evidence file or commit it.

### Step 8 — No coding in this step: run the gates and record the evidence

**Purpose:** Confirm the suite is isolated, that drift is caught, and that nothing else regressed.

**Decision:** Run the deterministic gate without a database URL, then the integration suite, then the same integration suite in parallel workers, which is the property the task asks for.

**Action:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
git diff --check
```

To run the integration suite in parallel without adding a dependency, start two processes with different worker ids and let them overlap:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  PYTEST_XDIST_WORKER=gw0 UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_publishing_integration.py tests/test_chunks_integration.py -q &
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  PYTEST_XDIST_WORKER=gw1 UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_records_integration.py tests/test_sources_integration.py -q &
wait
```

Then confirm each worker left its own database behind:

```bash
docker exec knowledge-service-postgres psql -U test_user -d postgres -tAc \
  "SELECT datname FROM pg_database WHERE datname LIKE 'knowledge_service%' ORDER BY datname"
```

In `docs/evidence/M02-T12-real-database-test-fixtures.md`, replace the placeholders with the date, your invariant, the files you changed, the exact observed results, the PostgreSQL version, and the answers below. Add no credential, connection URL, or private content.

**Reflection — answer in your own words:**

1. The fixture creates a database per worker and empties every table before each test. Name what that takes off the caller's hands, and describe what a test author would otherwise have to remember.
2. The isolation could have been one shared database with a transaction per test rolled back at the end. Describe what gets harder later if it were — think about a test that opens a second connection while the first is still working.
3. The drift test drops a column and expects a failure. If we removed that assertion and kept only the passing check, what production failure might go unnoticed until a migration reached production?

**Check:** The unit tests and every integration test pass, the parallel run passes with one database per worker, all repository gates pass, and the evidence records the real results and no connection secret.

## Completion checklist

- [ ] `src/knowledge_service/persistence.py` declares every table the migrations create, including `sources` and `synchronization_runs`.
- [ ] `migrations/env.py` sets `target_metadata`, so drift can be detected.
- [ ] `tests/database_fixtures.py` names, creates, migrates, and drops one database per pytest worker, and scopes the migration URL variable to the call that needs it.
- [ ] `tests/conftest.py` provides the shared fixtures, and `migrated_database` empties every table before each test.
- [ ] No integration file defines its own fixture or reads the test URL.
- [ ] A drift test fails when the migrated schema and the declared tables disagree.
- [ ] Repository checks pass, the parallel run passes, and the evidence records real results.

Share your implementation or any error you hit and I will review it.
