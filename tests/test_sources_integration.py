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
