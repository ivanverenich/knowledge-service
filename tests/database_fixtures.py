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
