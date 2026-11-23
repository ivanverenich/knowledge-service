"""Telling a slow Job from a dead one, and the decisions an operator may take."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from knowledge_service.identifiers import JobId
from knowledge_service.job_conditions import (
    JobCondition,
    JobDecision,
    RecoveryPolicy,
    allowed_decisions,
    condition_of,
)
from knowledge_service.jobs import (
    IllegalJobTransition,
    InvalidJob,
    Job,
    JobKind,
    JobNotRetryable,
    JobStatus,
    JobStillRunning,
)

QUEUED_AT = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
STARTED_AT = QUEUED_AT + timedelta(minutes=1)
FAILED_AT = STARTED_AT + timedelta(minutes=1)
POLICY = RecoveryPolicy()


def queued() -> Job:
    return Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_DOCUMENT_CHUNKS,
        target_id=uuid4(),
        queued_at=QUEUED_AT,
    )


def running() -> Job:
    return queued().start(started_at=STARTED_AT)


def failed(code: str = "database_unavailable", *, retry: bool = False) -> Job:
    retry_at = FAILED_AT + timedelta(seconds=30) if retry else None
    return running().fail(error_code=code, finished_at=FAILED_AT, retry_at=retry_at)


def exhausted() -> Job:
    job = failed()
    for _ in range(2):
        job = job.reopen(queued_at=FAILED_AT)
        job = job.start(started_at=FAILED_AT).fail(
            error_code="database_unavailable", finished_at=FAILED_AT
        )
    return job


# Slow or dead: a running Job is judged by when it was last heard from.


def test_a_running_job_heard_from_recently_is_running_however_long_it_ran() -> None:
    hours_later = STARTED_AT + timedelta(hours=5)

    condition = condition_of(
        running(),
        now=hours_later,
        last_heard_at=hours_later - timedelta(minutes=4, seconds=59),
    )

    assert condition is JobCondition.RUNNING


def test_a_running_job_silent_for_the_stale_time_is_stale() -> None:
    heard = STARTED_AT + timedelta(minutes=30)

    at_limit = condition_of(
        running(), now=heard + POLICY.stale_after, last_heard_at=heard
    )
    just_before = condition_of(
        running(),
        now=heard + POLICY.stale_after - timedelta(microseconds=1),
        last_heard_at=heard,
    )

    assert (at_limit, just_before) == (JobCondition.STALE, JobCondition.RUNNING)


def test_a_running_job_that_never_reported_is_judged_by_its_start() -> None:
    assert (
        condition_of(running(), now=STARTED_AT + timedelta(minutes=5))
        is JobCondition.STALE
    )


def test_a_clock_behind_the_job_never_calls_it_stale() -> None:
    assert condition_of(running(), now=QUEUED_AT) is JobCondition.RUNNING


# Orphaned: a queued Job whose message was sent long ago.


@pytest.mark.parametrize(
    ("published_ago", "condition"),
    [
        (None, JobCondition.WAITING),
        (timedelta(minutes=29), JobCondition.WAITING),
        (timedelta(minutes=30), JobCondition.ORPHANED),
    ],
)
def test_a_queued_job_is_orphaned_once_its_message_is_old(
    published_ago: timedelta | None, condition: JobCondition
) -> None:
    now = QUEUED_AT + timedelta(hours=1)
    published_at = None if published_ago is None else now - published_ago

    assert condition_of(queued(), now=now, published_at=published_at) is condition


def test_a_job_queued_after_the_clock_is_never_orphaned() -> None:
    earlier = QUEUED_AT - timedelta(hours=1)
    published_at = earlier - timedelta(hours=1)

    condition = condition_of(queued(), now=earlier, published_at=published_at)

    assert condition is JobCondition.WAITING


# Failed: planned retry, exhausted, or permanent.


@pytest.mark.parametrize(
    ("job", "condition"),
    [
        (failed(retry=True), JobCondition.RETRY_PLANNED),
        (failed("database_unavailable"), JobCondition.EXHAUSTED),
        (failed("worker_lost"), JobCondition.EXHAUSTED),
        (failed("source_refused"), JobCondition.FAILED_PERMANENTLY),
        (failed("superseded"), JobCondition.FAILED_PERMANENTLY),
        (failed("an_old_code"), JobCondition.FAILED_PERMANENTLY),
    ],
)
def test_a_failed_job_is_judged_by_its_retry_and_its_code(
    job: Job, condition: JobCondition
) -> None:
    assert condition_of(job, now=FAILED_AT) is condition


def test_only_failures_with_no_retry_need_a_decision() -> None:
    assert {condition for condition in JobCondition if condition.needs_decision} == {
        JobCondition.EXHAUSTED,
        JobCondition.FAILED_PERMANENTLY,
    }


@pytest.mark.parametrize("value", [timedelta(seconds=59), timedelta(days=8), 300, None])
def test_a_recovery_policy_refuses_an_unbounded_time(value: object) -> None:
    with pytest.raises(ValueError, match="stale_after"):
        RecoveryPolicy(stale_after=value)  # type: ignore[arg-type]


# An operator's decisions in the domain.


def test_a_retry_gives_a_job_that_used_every_attempt_one_more() -> None:
    job = exhausted()

    reopened = job.reopen(queued_at=FAILED_AT + timedelta(hours=1))

    assert (reopened.status, reopened.attempt, reopened.max_attempts) == (
        JobStatus.QUEUED,
        3,
        4,
    )
    assert reopened.last_error_code is None
    assert reopened.started_at is None


def test_a_retry_of_a_permanent_failure_keeps_its_attempt_limit() -> None:
    reopened = failed("source_refused").reopen(queued_at=FAILED_AT)

    assert (reopened.attempt, reopened.max_attempts) == (1, 3)


def test_a_retry_is_refused_while_one_is_planned() -> None:
    with pytest.raises(JobNotRetryable):
        failed(retry=True).reopen(queued_at=FAILED_AT)


@pytest.mark.parametrize("job", [queued(), running()])
def test_only_a_failed_job_can_be_retried_by_an_operator(job: Job) -> None:
    with pytest.raises(IllegalJobTransition):
        job.reopen(queued_at=FAILED_AT)


def test_cancelling_a_queued_job_closes_it_without_a_start() -> None:
    cancelled = queued().cancel(finished_at=QUEUED_AT)

    assert (cancelled.status, cancelled.started_at, cancelled.is_open) == (
        JobStatus.CANCELLED,
        None,
        False,
    )


def test_cancelling_a_failed_job_drops_its_retry_and_keeps_its_code() -> None:
    cancelled = failed(retry=True).cancel(finished_at=FAILED_AT)

    assert (cancelled.retry_at, cancelled.last_error_code) == (
        None,
        "database_unavailable",
    )


def test_a_running_job_cannot_be_cancelled() -> None:
    with pytest.raises(JobStillRunning):
        running().cancel(finished_at=FAILED_AT)


@pytest.mark.parametrize(
    "job",
    [
        running().succeed(finished_at=FAILED_AT),
        queued().cancel(finished_at=QUEUED_AT),
    ],
)
def test_a_finished_job_cannot_be_cancelled_or_retried(job: Job) -> None:
    with pytest.raises(IllegalJobTransition):
        job.cancel(finished_at=FAILED_AT)
    with pytest.raises(IllegalJobTransition):
        job.reopen(queued_at=FAILED_AT)


def test_a_cancel_never_ends_before_the_job_was_queued() -> None:
    with pytest.raises(InvalidJob, match="queued_at"):
        queued().cancel(finished_at=QUEUED_AT - timedelta(seconds=1))


@pytest.mark.parametrize(
    ("job", "decisions"),
    [
        (queued(), (JobDecision.CANCEL,)),
        (running(), ()),
        (failed(retry=True), (JobDecision.CANCEL,)),
        (failed(), (JobDecision.RETRY, JobDecision.CANCEL)),
        (running().succeed(finished_at=FAILED_AT), ()),
        (queued().cancel(finished_at=QUEUED_AT), ()),
    ],
)
def test_only_the_decisions_that_fit_are_offered(
    job: Job, decisions: tuple[JobDecision, ...]
) -> None:
    assert allowed_decisions(job) == decisions
