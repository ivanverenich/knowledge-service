"""Real PostgreSQL tests for Jobs, audit events, and Evaluation Runs."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from knowledge_service.audit import AuditAction, AuditEvent, AuditTargetKind
from knowledge_service.evaluation import EvaluationRun, EvaluationStatus
from knowledge_service.identifiers import (
    AuditEventId,
    EvaluationDatasetId,
    EvaluationRunId,
    JobId,
    UserId,
)
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.persistence import (
    create_evaluation_run,
    enqueue_job,
    load_audit_events,
    load_evaluation_run,
    load_job,
    record_audit_event,
    save_evaluation_run,
    save_job,
)

OBSERVED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
USER_ID = UserId.parse("00000000-0000-0000-0000-000000000030")
DATASET_ID = EvaluationDatasetId.parse("00000000-0000-0000-0000-000000000060")

INSERT_RAW_AUDIT = text(
    "INSERT INTO audit_events (event_id, occurred_at, actor_id, action, target_kind,"
    " target_id, metadata) VALUES (:event_id, now(), NULL, 'job.queued', 'job',"
    " :target_id, CAST(:metadata AS jsonb))"
)

INSERT_RAW_RUN = text(
    "INSERT INTO evaluation_runs (run_id, dataset_id, dataset_version,"
    " configuration_fingerprint, status, artifact_location, created_at, started_at,"
    " finished_at) VALUES (:run_id, :dataset_id, 1, 'config-a', 'succeeded', NULL,"
    " now(), now(), now())"
)


@pytest.mark.integration
async def test_records_survive_their_state_machines_and_refuse_raw_content(
    migrated_database: str,
) -> None:
    engine = create_async_engine(
        make_url(migrated_database).set(drivername="postgresql+psycopg")
    )
    try:
        job = Job.request(
            job_id=JobId.new(),
            kind=JobKind.SYNCHRONIZE_SOURCE,
            target_id=uuid4(),
            queued_at=OBSERVED_AT,
            max_attempts=2,
        )

        async with engine.begin() as connection:
            await enqueue_job(connection, job)
            running = job.start(started_at=OBSERVED_AT + timedelta(seconds=1))
            await save_job(connection, job=running)
            failed = running.fail(
                error_code="source_unreachable",
                finished_at=OBSERVED_AT + timedelta(seconds=2),
            )
            await save_job(connection, job=failed)

        async with engine.begin() as connection:
            stored_job = await load_job(connection, job_id=job.job_id)

        assert stored_job is not None
        assert stored_job.status is JobStatus.FAILED
        assert stored_job.attempt == 1
        assert stored_job.last_error_code == "source_unreachable"

        async with engine.begin() as connection:
            retried = failed.retry(queued_at=OBSERVED_AT + timedelta(seconds=3))
            await save_job(connection, job=retried)
            reloaded_job = await load_job(connection, job_id=job.job_id)

        assert reloaded_job is not None
        assert reloaded_job.status is JobStatus.QUEUED
        assert reloaded_job.attempt == 1
        assert reloaded_job.started_at is None
        assert reloaded_job.last_error_code is None

        target_id = uuid4()
        async with engine.begin() as connection:
            await record_audit_event(
                connection,
                AuditEvent(
                    event_id=AuditEventId.new(),
                    occurred_at=OBSERVED_AT,
                    actor_id=USER_ID,
                    action=AuditAction.JOB_QUEUED,
                    target_kind=AuditTargetKind.JOB,
                    target_id=target_id,
                    metadata={"kind": "synchronize_source"},
                ),
            )
            await record_audit_event(
                connection,
                AuditEvent(
                    event_id=AuditEventId.new(),
                    occurred_at=OBSERVED_AT + timedelta(seconds=1),
                    actor_id=None,
                    action=AuditAction.JOB_FAILED,
                    target_kind=AuditTargetKind.JOB,
                    target_id=target_id,
                    metadata={"attempt": 1},
                ),
            )

        async with engine.begin() as connection:
            trail = await load_audit_events(connection, target_id=target_id, limit=10)

        assert [event.action for event in trail] == [
            AuditAction.JOB_QUEUED,
            AuditAction.JOB_FAILED,
        ]
        assert trail[0].metadata == {"kind": "synchronize_source"}
        assert trail[0].actor_id == USER_ID
        assert trail[1].actor_id is None

        async with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    INSERT_RAW_AUDIT,
                    {
                        "event_id": AuditEventId.new().value,
                        "target_id": uuid4(),
                        "metadata": json.dumps(
                            {"question": "How do I deploy the service?" * 40}
                        ),
                    },
                )

        run = EvaluationRun.plan(
            run_id=EvaluationRunId.new(),
            dataset_id=DATASET_ID,
            dataset_version=3,
            configuration_fingerprint="config-a",
            created_at=OBSERVED_AT,
        )

        async with engine.begin() as connection:
            await create_evaluation_run(connection, run)
            started_run = run.start(started_at=OBSERVED_AT + timedelta(seconds=1))
            await save_evaluation_run(connection, run=started_run)
            finished_run = started_run.succeed(
                artifact_location="s3://evaluations/run-1",
                finished_at=OBSERVED_AT + timedelta(minutes=1),
            )
            await save_evaluation_run(connection, run=finished_run)

        async with engine.begin() as connection:
            stored_run = await load_evaluation_run(connection, run_id=run.run_id)

        assert stored_run is not None
        assert stored_run.status is EvaluationStatus.SUCCEEDED
        assert stored_run.artifact_location == "s3://evaluations/run-1"
        assert stored_run.configuration_fingerprint == "config-a"
        assert stored_run.dataset_version == 3

        async with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    INSERT_RAW_RUN,
                    {
                        "run_id": EvaluationRunId.new().value,
                        "dataset_id": DATASET_ID.value,
                    },
                )
    finally:
        await engine.dispose()
