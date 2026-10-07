"""Real PostgreSQL tests for the Source and Synchronization Run tables."""

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

PROJECT_ROOT = Path(__file__).resolve().parents[1]

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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ.get("KNOWLEDGE_SERVICE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set KNOWLEDGE_SERVICE_TEST_DATABASE_URL to a disposable database")

    monkeypatch.setenv("KNOWLEDGE_SERVICE_DATABASE_URL", database_url)
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")

    engine = create_engine(make_url(database_url).set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM synchronization_runs"))
            connection.execute(text("DELETE FROM sources"))

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
