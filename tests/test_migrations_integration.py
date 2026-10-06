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
    database_url = os.environ.get("KNOWLEDGE_SERVICE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip(
            "set KNOWLEDGE_SERVICE_TEST_DATABASE_URL to an empty disposable database"
        )

    monkeypatch.setenv("KNOWLEDGE_SERVICE_DATABASE_URL", database_url)
    config = Config(PROJECT_ROOT / "alembic.ini")
    baseline_id = ScriptDirectory.from_config(config).get_current_head()
    assert baseline_id is not None, "Baseline revision should exist"
    sync_url = make_url(database_url).set(drivername="postgresql+psycopg")
    engine = create_engine(sync_url)
    try:
        with engine.connect() as connection:
            existing_tables = set(inspect(connection).get_table_names())
            assert existing_tables <= {"alembic_version"}, (
                "Database should be empty before migration"
            )
            if "alembic_version" in existing_tables:
                current_id = connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
                assert current_id in (None, baseline_id), (
                    "Database should be at baseline before migration"
                )

            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert set(inspect(connection).get_table_names()) == {"alembic_version"}
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == baseline_id
                )

            command.downgrade(config, "base")
            with engine.connect() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    is None
                )

            command.upgrade(config, "head")
            with engine.connect() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM alembic_version"))
                    == baseline_id
                )
    finally:
        engine.dispose()
