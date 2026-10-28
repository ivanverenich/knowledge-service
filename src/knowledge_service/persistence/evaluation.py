"""Store Evaluation Runs."""

from collections.abc import Mapping
from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.evaluation import (
    EvaluationRun,
    EvaluationStatus,
    InvalidEvaluationRun,
)
from knowledge_service.identifiers import EvaluationDatasetId, EvaluationRunId
from knowledge_service.persistence.schema import evaluation_runs


def _evaluation_row(run: EvaluationRun) -> dict[str, object]:
    return {
        "run_id": run.run_id.value,
        "dataset_id": run.dataset_id.value,
        "dataset_version": run.dataset_version,
        "configuration_fingerprint": run.configuration_fingerprint,
        "status": run.status.value,
        "artifact_location": run.artifact_location,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _evaluation_from(record: Mapping[str, object]) -> EvaluationRun:
    try:
        status = EvaluationStatus(cast(str, record["status"]))
    except ValueError:
        raise InvalidEvaluationRun("stored run status is not a known value") from None

    return EvaluationRun(
        run_id=EvaluationRunId(cast(UUID, record["run_id"])),
        dataset_id=EvaluationDatasetId(cast(UUID, record["dataset_id"])),
        dataset_version=cast(int, record["dataset_version"]),
        configuration_fingerprint=cast(str, record["configuration_fingerprint"]),
        status=status,
        artifact_location=cast("str | None", record["artifact_location"]),
        created_at=cast(datetime, record["created_at"]),
        started_at=cast("datetime | None", record["started_at"]),
        finished_at=cast("datetime | None", record["finished_at"]),
    )


async def create_evaluation_run(
    connection: AsyncConnection, run: EvaluationRun
) -> EvaluationRunId:
    """Store a planned Evaluation Run."""
    await connection.execute(sa.insert(evaluation_runs).values(_evaluation_row(run)))
    return run.run_id


async def load_evaluation_run(
    connection: AsyncConnection, *, run_id: EvaluationRunId
) -> EvaluationRun | None:
    """Read a stored Evaluation Run, if it exists."""
    statement = sa.select(evaluation_runs).where(
        evaluation_runs.c.run_id == run_id.value
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _evaluation_from(dict(record))


async def save_evaluation_run(
    connection: AsyncConnection, *, run: EvaluationRun
) -> EvaluationRun:
    """Store the status a run has reached."""
    statement = (
        sa.update(evaluation_runs)
        .where(evaluation_runs.c.run_id == run.run_id.value)
        .values(_evaluation_row(run))
    )
    await connection.execute(statement)
    return run
