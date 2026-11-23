"""Tests for the Job state machine."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from knowledge_service.identifiers import JobId
from knowledge_service.jobs import (
    IllegalJobTransition,
    InvalidJob,
    Job,
    JobAlreadyClaimed,
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
        error_code="source_unreachable",
        finished_at=QUEUED_AT + timedelta(seconds=2),
        retry_at=QUEUED_AT + timedelta(seconds=3),
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


def test_a_job_is_open_until_it_can_do_no_more_work() -> None:
    job = requested_job(max_attempts=2)
    started = job.start(started_at=QUEUED_AT)
    failed_once = started.fail(
        error_code="boom", finished_at=QUEUED_AT, retry_at=QUEUED_AT
    )
    failed_twice = (
        failed_once.retry(queued_at=QUEUED_AT)
        .start(started_at=QUEUED_AT)
        .fail(error_code="boom", finished_at=QUEUED_AT)
    )

    assert job.is_open
    assert started.is_open
    # A failed Job with a retry scheduled will be queued again.
    assert failed_once.is_open
    # A failure with no retry time is final, even with attempts left.
    assert not started.fail(error_code="boom", finished_at=QUEUED_AT).is_open
    assert not failed_twice.is_open
    assert not started.succeed(finished_at=QUEUED_AT).is_open


def test_a_refused_claim_carries_the_job_as_stored() -> None:
    finished = (
        requested_job().start(started_at=QUEUED_AT).succeed(finished_at=QUEUED_AT)
    )

    refused = JobAlreadyClaimed(finished)

    assert isinstance(refused, InvalidJob)
    assert refused.job is finished
    assert str(refused) == "only a queued job can run, not a succeeded one"


def test_only_a_failed_job_with_attempts_left_carries_a_retry_time() -> None:
    running = requested_job(max_attempts=2).start(started_at=QUEUED_AT)
    later = QUEUED_AT + timedelta(minutes=1)

    with pytest.raises(InvalidJob, match="only a failed job"):
        replace(running, retry_at=later)
    with pytest.raises(InvalidJob, match="earlier than finished_at"):
        running.fail(error_code="boom", finished_at=later, retry_at=QUEUED_AT)
    with pytest.raises(InvalidJob, match="UTC"):
        running.fail(
            error_code="boom",
            finished_at=QUEUED_AT,
            retry_at=later.replace(tzinfo=None),
        )


def test_a_job_with_no_attempts_left_cannot_get_a_retry_time() -> None:
    running = requested_job(max_attempts=1).start(started_at=QUEUED_AT)

    with pytest.raises(InvalidJob, match="no attempts left"):
        running.fail(error_code="boom", finished_at=QUEUED_AT, retry_at=QUEUED_AT)


def test_a_retry_waits_for_its_time_and_never_follows_a_final_failure() -> None:
    running = requested_job(max_attempts=3).start(started_at=QUEUED_AT)
    due = QUEUED_AT + timedelta(minutes=1)
    scheduled = running.fail(error_code="boom", finished_at=QUEUED_AT, retry_at=due)
    final = running.fail(error_code="boom", finished_at=QUEUED_AT)

    with pytest.raises(JobNotRetryable, match="before its retry time"):
        scheduled.retry(queued_at=due - timedelta(seconds=1))
    with pytest.raises(JobNotRetryable, match="permanently"):
        final.retry(queued_at=due)
    retried = scheduled.retry(queued_at=due)
    assert (retried.status, retried.retry_at) == (JobStatus.QUEUED, None)
