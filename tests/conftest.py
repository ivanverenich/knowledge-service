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
