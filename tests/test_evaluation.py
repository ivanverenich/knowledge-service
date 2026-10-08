"""Tests for the Evaluation Run state machine."""

from datetime import UTC, datetime, timedelta

import pytest

from knowledge_service.evaluation import (
    EvaluationRun,
    EvaluationStatus,
    IllegalRunTransition,
    InvalidEvaluationRun,
)
from knowledge_service.identifiers import EvaluationDatasetId, EvaluationRunId

CREATED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
DATASET_ID = EvaluationDatasetId.parse("00000000-0000-0000-0000-000000000060")


def planned_run(*, dataset_version: int = 3) -> EvaluationRun:
    return EvaluationRun.plan(
        run_id=EvaluationRunId.new(),
        dataset_id=DATASET_ID,
        dataset_version=dataset_version,
        configuration_fingerprint="config-a",
        created_at=CREATED_AT,
    )


def test_a_run_starts_and_records_its_artifact() -> None:
    run = planned_run()
    running = run.start(started_at=CREATED_AT + timedelta(seconds=1))
    finished = running.succeed(
        artifact_location="s3://evaluations/run-1",
        finished_at=CREATED_AT + timedelta(minutes=5),
    )

    assert run.status is EvaluationStatus.PENDING
    assert finished.status is EvaluationStatus.SUCCEEDED
    assert finished.artifact_location == "s3://evaluations/run-1"
    assert finished.configuration_fingerprint == "config-a"
    assert finished.dataset_version == 3


def test_a_failed_run_keeps_no_artifact() -> None:
    running = planned_run().start(started_at=CREATED_AT + timedelta(seconds=1))
    failed = running.fail(finished_at=CREATED_AT + timedelta(minutes=1))

    assert failed.status is EvaluationStatus.FAILED
    assert failed.artifact_location is None


def test_a_succeeded_run_cannot_start_again() -> None:
    running = planned_run().start(started_at=CREATED_AT + timedelta(seconds=1))
    finished = running.succeed(
        artifact_location="s3://evaluations/run-1",
        finished_at=CREATED_AT + timedelta(minutes=2),
    )

    with pytest.raises(IllegalRunTransition, match="succeeded"):
        finished.start(started_at=CREATED_AT + timedelta(minutes=3))


def test_a_succeeded_run_needs_an_artifact_location() -> None:
    running = planned_run().start(started_at=CREATED_AT + timedelta(seconds=1))

    with pytest.raises(InvalidEvaluationRun, match="artifact"):
        EvaluationRun(
            run_id=running.run_id,
            dataset_id=running.dataset_id,
            dataset_version=running.dataset_version,
            configuration_fingerprint=running.configuration_fingerprint,
            status=EvaluationStatus.SUCCEEDED,
            artifact_location=None,
            created_at=running.created_at,
            started_at=running.started_at,
            finished_at=CREATED_AT + timedelta(minutes=1),
        )
