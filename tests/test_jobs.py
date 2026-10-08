"""Tests for the Job state machine."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from knowledge_service.identifiers import JobId
from knowledge_service.jobs import (
    IllegalJobTransition,
    InvalidJob,
    Job,
    JobKind,
    JobNotRetryable,
    JobStatus,
)

QUEUED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
TARGET_ID = uuid4()


def requested_job(*, max_attempts: int = 2) -> Job:
    return Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=TARGET_ID,
        queued_at=QUEUED_AT,
        max_attempts=max_attempts,
    )


def test_a_job_runs_and_succeeds() -> None:
    job = requested_job()
    started = job.start(started_at=QUEUED_AT + timedelta(seconds=1))
    finished = started.succeed(finished_at=QUEUED_AT + timedelta(seconds=5))

    assert job.status is JobStatus.QUEUED
    assert started.status is JobStatus.RUNNING
    assert finished.status is JobStatus.SUCCEEDED
    assert finished.attempt == 0
    assert finished.finished_at == QUEUED_AT + timedelta(seconds=5)


def test_a_failed_job_retries_until_its_attempts_run_out() -> None:
    job = requested_job(max_attempts=2)
    first = job.start(started_at=QUEUED_AT + timedelta(seconds=1)).fail(
        error_code="source_unreachable", finished_at=QUEUED_AT + timedelta(seconds=2)
    )
    second = first.retry(queued_at=QUEUED_AT + timedelta(seconds=3))
    second_failure = second.start(started_at=QUEUED_AT + timedelta(seconds=4)).fail(
        error_code="source_unreachable", finished_at=QUEUED_AT + timedelta(seconds=5)
    )

    assert first.attempt == 1
    assert second.status is JobStatus.QUEUED
    assert second.attempt == 1
    assert second.started_at is None
    assert second.last_error_code is None
    assert second_failure.attempt == 2

    with pytest.raises(JobNotRetryable):
        second_failure.retry(queued_at=QUEUED_AT + timedelta(seconds=6))


def test_a_queued_job_cannot_succeed() -> None:
    with pytest.raises(IllegalJobTransition, match="queued"):
        requested_job().succeed(finished_at=QUEUED_AT + timedelta(seconds=1))


def test_a_raw_error_message_is_rejected() -> None:
    running = requested_job().start(started_at=QUEUED_AT + timedelta(seconds=1))

    with pytest.raises(InvalidJob, match="at most"):
        running.fail(error_code="x" * 65, finished_at=QUEUED_AT + timedelta(seconds=2))
