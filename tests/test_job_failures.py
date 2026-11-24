"""Tests for classifying Job failures and scheduling their retries."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from knowledge_service.embeddings.port import (
    EmbeddingInputRejected,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
    EmbeddingTimeout,
    RejectionReason,
)
from knowledge_service.identifiers import JobId
from knowledge_service.ingestion.sources import (
    SourceUnauthorized,
    SourceUnavailable,
    SynchronizationSuperseded,
)
from knowledge_service.job_failures import (
    JobFailure,
    RetryPolicy,
    classify_job_failure,
    fail_attempt,
)
from knowledge_service.jobs import InvalidJob, Job, JobKind
from knowledge_service.model import (
    ChatModelMalformedResponse,
    ChatModelRefused,
    ChatModelRequestRefused,
    ChatModelUnavailable,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.jobs import JOB_TIME_LIMIT_MS

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
# Stands in for driver text that could quote a URL or a credential.
MARKER = "leak-marker-5c1e"


class DriverError(Exception):
    """A database driver error with the SQLSTATE the server sent."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(MARKER)
        self.sqlstate = sqlstate


def database_error(
    kind: type[OperationalError | IntegrityError], sqlstate: str
) -> DBAPIError:
    return kind("DELETE FROM chunks", None, DriverError(sqlstate))


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (SourceUnavailable("the wiki is down"), JobFailure.SOURCE_UNAVAILABLE),
        (SourceUnauthorized("token expired"), JobFailure.SOURCE_REFUSED),
        (SynchronizationSuperseded("newer pass"), JobFailure.SUPERSEDED),
        (
            OperationalError("SELECT 1", None, ConnectionError("lost")),
            JobFailure.DATABASE_UNAVAILABLE,
        ),
        # A deadlock or serialization failure may pass on a second try.
        (database_error(OperationalError, "40P01"), JobFailure.DATABASE_UNAVAILABLE),
        # The statement timeout cancelled the statement.
        (database_error(OperationalError, "57014"), JobFailure.DATABASE_UNAVAILABLE),
        (database_error(IntegrityError, "23505"), JobFailure.DATABASE_REJECTED),
        (ChatModelUnavailable(), JobFailure.MODEL_UNAVAILABLE),
        (ChatModelMalformedResponse("no usage"), JobFailure.MODEL_UNAVAILABLE),
        (EmbeddingTimeout(), JobFailure.MODEL_UNAVAILABLE),
        (EmbeddingRateLimited(), JobFailure.MODEL_UNAVAILABLE),
        (ChatModelRequestRefused(), JobFailure.MODEL_REFUSED),
        (ChatModelRefused(), JobFailure.MODEL_REFUSED),
        (EmbeddingRequestRefused(), JobFailure.MODEL_REFUSED),
        (
            EmbeddingInputRejected(RejectionReason.EMPTY_TEXT, index=0),
            JobFailure.MODEL_REFUSED,
        ),
        (ValueError("bad input"), JobFailure.PROGRAMMING_ERROR),
        (KeyError("missing"), JobFailure.PROGRAMMING_ERROR),
        (InvalidJob("bad job"), JobFailure.PROGRAMMING_ERROR),
        # An adapter must wrap network errors; a raw one is a bug.
        (ConnectionError("reset"), JobFailure.PROGRAMMING_ERROR),
    ],
)
def test_every_failure_is_classified(error: Exception, failure: JobFailure) -> None:
    assert classify_job_failure(error) is failure


def test_only_failures_that_may_pass_later_are_transient() -> None:
    # A dependency that may answer later, a clock that may come back in line,
    # or a worker or message lost on the way.
    assert {failure for failure in JobFailure if failure.transient} == {
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.CLOCK_AHEAD,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
        JobFailure.WORKER_LOST,
        JobFailure.DELIVERY_LOST,
    }


def test_the_delay_doubles_until_its_cap_and_never_drops_below_half() -> None:
    lowest = RetryPolicy(jitter=lambda: 0.0)
    highest = RetryPolicy(jitter=lambda: 0.999)

    assert [lowest.delay(n) for n in (1, 2, 3)] == [
        timedelta(seconds=15),
        timedelta(seconds=30),
        timedelta(seconds=60),
    ]
    assert highest.delay(1) < timedelta(seconds=30)
    assert lowest.delay(10) == timedelta(minutes=7, seconds=30)
    assert highest.delay(10) < timedelta(minutes=15)
    # A huge attempt count stays at the cap instead of overflowing.
    assert lowest.delay(10_000) == timedelta(minutes=7, seconds=30)


@pytest.mark.parametrize("ceiling_us", [1, 3, 7, 1_000_001])
def test_the_delay_stays_in_its_window_for_any_ceiling(ceiling_us: int) -> None:
    ceiling = timedelta(microseconds=ceiling_us)
    lowest = RetryPolicy(base_delay=ceiling, max_delay=ceiling, jitter=lambda: 0.0)
    highest = RetryPolicy(
        base_delay=ceiling, max_delay=ceiling, jitter=lambda: 0.999999
    )

    assert lowest.delay(1) == timedelta(microseconds=ceiling_us // 2)
    assert highest.delay(1) < ceiling


def test_a_long_base_delay_reaches_the_cap_without_overflowing() -> None:
    policy = RetryPolicy(
        base_delay=timedelta(days=1_000),
        max_delay=timedelta(days=2_000),
        jitter=lambda: 0.0,
    )

    assert policy.delay(21) == timedelta(days=1_000)
    assert policy.delay(10_000) == timedelta(days=1_000)


@pytest.mark.parametrize("failed_attempts", [0, -1, True, 1.0])
def test_the_delay_needs_a_positive_attempt_count(failed_attempts: int) -> None:
    with pytest.raises(ValueError, match="failed_attempts"):
        RetryPolicy().delay(failed_attempts)


@pytest.mark.parametrize("share", [-0.1, 1.0, float("nan")])
def test_a_jitter_outside_zero_to_one_is_refused(share: float) -> None:
    with pytest.raises(ValueError, match="jitter"):
        RetryPolicy(jitter=lambda: share).delay(1)


def test_a_policy_needs_a_positive_delay_and_a_cap_above_it() -> None:
    with pytest.raises(ValueError, match="base_delay"):
        RetryPolicy(base_delay=timedelta(0))
    with pytest.raises(ValueError, match="max_delay"):
        RetryPolicy(base_delay=timedelta(minutes=2), max_delay=timedelta(minutes=1))


def running_job(*, attempt: int = 0, max_attempts: int = 3) -> Job:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_DOCUMENT_CHUNKS,
        target_id=JobId.new().value,
        queued_at=NOW,
        max_attempts=max_attempts,
    )
    for _ in range(attempt):
        job = job.start(started_at=NOW).fail(
            error_code="boom", finished_at=NOW, retry_at=NOW
        )
        job = job.retry(queued_at=NOW)
    return job.start(started_at=NOW)


def test_a_transient_failure_with_attempts_left_is_retried_after_the_delay() -> None:
    policy = RetryPolicy(jitter=lambda: 0.5)

    failed = fail_attempt(
        running_job(), SourceUnavailable("down"), now=NOW, policy=policy
    )

    assert (failed.attempt, failed.last_error_code) == (1, "source_unavailable")
    assert failed.retry_at == NOW + policy.delay(1)
    assert failed.is_open


def test_a_transient_failure_stops_at_the_attempt_budget() -> None:
    last = running_job(attempt=2, max_attempts=3)

    failed = fail_attempt(last, SourceUnavailable("down"), now=NOW)

    assert (failed.attempt, failed.retry_at) == (3, None)
    assert not failed.is_open


@pytest.mark.parametrize(
    "error",
    [SourceUnauthorized("revoked"), ChatModelRequestRefused(), ValueError("bug")],
)
def test_a_permanent_failure_is_never_retried(error: Exception) -> None:
    failed = fail_attempt(running_job(), error, now=NOW)

    assert (failed.attempt, failed.retry_at) == (1, None)
    assert not failed.is_open


def test_a_failure_logs_its_code_and_type_but_not_its_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    fail_attempt(running_job(), database_error(OperationalError, "08006"), now=NOW)

    assert "database_unavailable" in caplog.text
    assert "OperationalError" in caplog.text
    assert MARKER not in caplog.text


def test_database_deadlines_end_before_the_hard_job_time_limit() -> None:
    settings = Settings()
    # The worst single statement: waiting for a pooled connection, opening
    # one, and running until the statement timeout cancels it.
    worst_statement_seconds = (
        settings.database_pool_timeout
        + settings.database_connect_timeout_seconds
        + settings.database_statement_timeout_ms / 1000
    )

    assert worst_statement_seconds < JOB_TIME_LIMIT_MS / 1000
