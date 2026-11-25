"""Recovering stuck Jobs, and an operator's decisions, on real PostgreSQL.

The drill: a Job that keeps reporting is left running however long it
takes, while one that went silent is failed with `worker_lost`, and its
attempt keeps what it reported. A message no worker took up counts as a lost
attempt, so one that keeps getting lost ends in an operator's decision, not
a loop. An operator retries or cancels through the API, and each decision is
in the audit log.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from authorization_scenarios import Person
from database_fixtures import migration_config, migration_environment
from job_fixtures import (
    OBSERVED_AT,
    SOURCE_ID,
    database_went_away,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service import job_recovery, job_reporting
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.ingestion.deletions import (
    purge_document_chunks,
    requeue_unfinished_purges,
)
from knowledge_service.job_conditions import JobCondition, condition_of
from knowledge_service.job_decisions import (
    DatabaseJobDecisions,
    cancel_job,
    load_jobs_needing_decision,
    retry_job,
)
from knowledge_service.job_failures import RetryPolicy
from knowledge_service.job_progress import (
    AttemptOutcome,
    JobCounters,
    JobProgress,
    JobStage,
    ProgressWentBackwards,
)
from knowledge_service.job_recovery import RecoveryReport, recover_stuck_jobs
from knowledge_service.job_reporting import report_progress
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.persistence.job_attempts import (
    AttemptNotCurrent,
    begin_attempt,
    load_latest_attempt,
)
from knowledge_service.persistence.jobs import (
    enqueue_job,
    mark_job_published,
    requeue_due_jobs,
    save_job,
)
from knowledge_service.persistence.schema import audit_events, job_attempts, jobs
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings
from knowledge_service.worker.jobs import run_stored_job

pytestmark = pytest.mark.integration

STARTED_AT = OBSERVED_AT + timedelta(minutes=1)
POLICY = RetryPolicy(jitter=lambda: 0.5)
# The first retry waits half of 30 s plus half of the other half.
FIRST_DELAY = timedelta(seconds=22.5)

AVERY = Person("avery", ())
CALLERS = {
    "Bearer avery": Caller(
        reader=AVERY.context(), roles=frozenset({ApplicationRole.ADMINISTRATOR})
    ),
}


class Tokens:
    """Gives each test token the caller its claims would give."""

    async def authenticate(self, headers: list[str]) -> Caller:
        if len(headers) == 1 and headers[0] in CALLERS:
            return CALLERS[headers[0]]
        raise Unauthenticated("test", error=AuthenticationError.INVALID_TOKEN)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    await insert_source(runtime)
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def running_synchronization(runtime: DatabaseRuntime) -> Job:
    """A synchronization Job whose claim and attempt row committed at 09:01."""
    async with runtime.transaction() as connection:
        queued = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=SOURCE_ID.value,
                queued_at=OBSERVED_AT,
            ),
        )
        running = queued.job.start(started_at=STARTED_AT)
        await save_job(connection, job=running)
        await begin_attempt(connection, job=running)
    return running


async def heartbeat(
    runtime: DatabaseRuntime, job: Job, *, minutes: float, processed: int
) -> None:
    await report_progress(
        runtime,
        job_id=job.job_id,
        attempt=job.attempt + 1,
        stage=JobStage.READING,
        counters=JobCounters(processed=processed),
        now=STARTED_AT + timedelta(minutes=minutes),
    )


async def go_silent(runtime: DatabaseRuntime, job: Job, *, minutes: float) -> None:
    """Make the database have last heard from `job`'s attempt `minutes` ago."""
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(job_attempts)
            .where(
                job_attempts.c.job_id == job.job_id.value,
                job_attempts.c.attempt == job.attempt + 1,
            )
            .values(heard_at=sa.func.now() - timedelta(minutes=minutes))
        )


async def database_now(runtime: DatabaseRuntime) -> datetime:
    async with runtime.transaction() as connection:
        now = await connection.scalar(sa.select(sa.func.now()))
    assert now is not None
    return now.astimezone(UTC)


async def recover(runtime: DatabaseRuntime, *, minutes: float) -> RecoveryReport:
    return await recover_stuck_jobs(
        runtime, now=STARTED_AT + timedelta(minutes=minutes), retry_policy=POLICY
    )


# One row per attempt: its number, how many it processed, and its outcome.
type AttemptRow = tuple[int, int, str | None]
# One row per decision: the action, who took it, and its metadata.
type DecisionRow = tuple[str, uuid.UUID, dict[str, object]]


async def attempts_of(runtime: DatabaseRuntime, job_id: JobId) -> list[AttemptRow]:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(
                job_attempts.c.attempt,
                job_attempts.c.processed,
                job_attempts.c.outcome,
            )
            .where(job_attempts.c.job_id == job_id.value)
            .order_by(job_attempts.c.attempt)
        )
        return [cast(AttemptRow, tuple(row)) for row in rows]


async def latest(runtime: DatabaseRuntime, job_id: JobId) -> JobProgress:
    async with runtime.transaction() as connection:
        progress = await load_latest_attempt(connection, job_id=job_id)
    assert progress is not None
    return progress


async def published_purge(runtime: DatabaseRuntime, *, at: timedelta) -> Job:
    """A queued purge whose message was sent at OBSERVED_AT + `at`."""
    _document, job_id = await tombstoned_with_purge(runtime)
    async with runtime.transaction() as connection:
        await mark_job_published(
            connection, job_id=job_id, published_at=OBSERVED_AT + at
        )
    return await stored_job(runtime, job_id)


async def failed_for_good(
    runtime: DatabaseRuntime, job: Job, *, error_code: str = "database_rejected"
) -> Job:
    """`job`, claimed at 09:01 and failed at 09:02 with no retry planned."""
    failed = job.start(started_at=STARTED_AT).fail(
        error_code=error_code, finished_at=STARTED_AT + timedelta(minutes=1)
    )
    async with runtime.transaction() as connection:
        await save_job(connection, job=failed)
    return failed


async def decisions_of(runtime: DatabaseRuntime, job_id: JobId) -> list[DecisionRow]:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(
                audit_events.c.action,
                audit_events.c.actor_id,
                audit_events.c.metadata,
            ).where(audit_events.c.target_id == job_id.value)
        )
        return [cast(DecisionRow, tuple(row)) for row in rows]


async def over_http(runtime: DatabaseRuntime, method: str, path: str) -> httpx.Response:
    app = create_app(
        authenticator=Tokens(),
        job_decisions=DatabaseJobDecisions(
            runtime, clock=lambda: STARTED_AT + timedelta(hours=2)
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.request(
            method, path, headers={"Authorization": "Bearer avery"}
        )


# The recovery drill: slow is not dead.


async def test_a_slow_job_that_keeps_reporting_is_left_running(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    # An hour of work, a report every four minutes.
    for minute in range(4, 61, 4):
        await heartbeat(runtime, job, minutes=minute, processed=minute * 10)
    # The last report reached the database four minutes ago.
    await go_silent(runtime, job, minutes=4)

    report = await recover(runtime, minutes=63)

    assert report == RecoveryReport(stale=(), orphaned=(), errors=())
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.RUNNING


async def test_a_silent_job_is_failed_and_its_attempt_keeps_its_progress(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=4, processed=120)
    # Nothing reached the database for six minutes since.
    await go_silent(runtime, job, minutes=6)

    report = await recover(runtime, minutes=10)

    assert report.stale == (job.job_id,)
    stored = await stored_job(runtime, job.job_id)
    recovered_at = STARTED_AT + timedelta(minutes=10)
    assert (stored.status, stored.last_error_code) == (JobStatus.FAILED, "worker_lost")
    assert stored.retry_at == recovered_at + FIRST_DELAY
    attempt = await latest(runtime, job.job_id)
    # What the dead attempt did stays: 120 processed, last heard at 09:05.
    assert attempt.counters.processed == 120
    assert attempt.heartbeat_at == STARTED_AT + timedelta(minutes=4)
    assert (attempt.outcome, attempt.finished_at) == (
        AttemptOutcome.RETRY_SCHEDULED,
        recovered_at,
    )


async def test_the_next_attempt_gets_its_own_row_and_the_dead_one_stays(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=4, processed=120)
    await go_silent(runtime, job, minutes=6)
    await recover(runtime, minutes=10)
    retry_at = STARTED_AT + timedelta(minutes=10) + FIRST_DELAY
    async with runtime.transaction() as connection:
        [queued] = await requeue_due_jobs(connection, now=retry_at, limit=10)
        second = queued.start(started_at=retry_at)
        await save_job(connection, job=second)
        await begin_attempt(connection, job=second)

    assert await attempts_of(runtime, job.job_id) == [
        (1, 120, "retry_scheduled"),
        (2, 0, None),
    ]


async def test_a_worker_presumed_dead_cannot_report_after_recovery(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=1, processed=10)
    await go_silent(runtime, job, minutes=9)
    await recover(runtime, minutes=10)

    # The worker was only paused; it wakes up and reports.
    with pytest.raises(AttemptNotCurrent):
        await heartbeat(runtime, job, minutes=11, processed=500)
    assert (await latest(runtime, job.job_id)).counters.processed == 10


async def test_a_job_that_never_reported_is_judged_by_its_start(
    runtime: DatabaseRuntime,
) -> None:
    now = await database_now(runtime)
    started: dict[int, Job] = {}
    async with runtime.transaction() as connection:
        for minutes in (4, 5):
            queued = await enqueue_job(
                connection,
                Job.request(
                    job_id=JobId.new(),
                    kind=JobKind.PURGE_CONVERSATION,
                    target_id=uuid.uuid4(),
                    queued_at=now - timedelta(minutes=10),
                ),
            )
            # Claimed, but its handler stored no attempt row.
            started[minutes] = queued.job.start(
                started_at=now - timedelta(minutes=minutes)
            )
            await save_job(connection, job=started[minutes])

    report = await recover_stuck_jobs(runtime, now=now, retry_policy=POLICY)

    assert report.stale == (started[5].job_id,)


async def test_a_report_that_lands_before_the_lock_keeps_the_job_alive(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await running_synchronization(runtime)
    await go_silent(runtime, job, minutes=6)
    real = job_recovery.load_silent_job_ids

    async def then_a_report(*args: object, **kwargs: object) -> tuple[JobId, ...]:
        found = await real(*args, **kwargs)  # type: ignore[arg-type]
        # Read as silent, then the worker reports before recovery locks it.
        await heartbeat(runtime, job, minutes=9, processed=30)
        return found

    monkeypatch.setattr(job_recovery, "load_silent_job_ids", then_a_report)

    report = await recover(runtime, minutes=10)

    assert report.stale == ()
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.RUNNING


async def test_a_job_whose_row_a_worker_holds_is_skipped(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await go_silent(runtime, job, minutes=6)
    async with runtime.transaction() as connection:
        # A report in progress holds a share lock on the Job's row.
        await connection.execute(
            sa.select(jobs.c.job_id)
            .where(jobs.c.job_id == job.job_id.value)
            .with_for_update(read=True)
        )
        report = await recover(runtime, minutes=10)

    assert report.stale == ()


# Orphaned: a message no worker took up.


async def test_a_recently_published_job_is_left_waiting(
    runtime: DatabaseRuntime,
) -> None:
    await published_purge(runtime, at=timedelta(0))

    # Published at 09:00; 29 minutes later it may still be in the queue.
    report = await recover(runtime, minutes=28)

    assert report == RecoveryReport(stale=(), orphaned=(), errors=())


async def test_an_undelivered_message_counts_as_a_lost_attempt(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))

    report = await recover(runtime, minutes=29)

    assert report.orphaned == (purge.job_id,)
    stored = await stored_job(runtime, purge.job_id)
    assert (stored.status, stored.attempt, stored.last_error_code) == (
        JobStatus.FAILED,
        1,
        "delivery_lost",
    )
    assert await attempts_of(runtime, purge.job_id) == [(1, 0, "retry_scheduled")]


async def test_the_lost_message_arriving_late_does_nothing(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await recover(runtime, minutes=29)

    late = await run_stored_job(
        runtime, purge.job_id, now=STARTED_AT + timedelta(minutes=30)
    )

    assert late.duplicate
    assert (await stored_job(runtime, purge.job_id)).last_error_code == "delivery_lost"


async def test_a_message_lost_every_time_ends_in_a_decision(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    minutes = 29.0
    for _ in range(3):
        await recover(runtime, minutes=minutes)
        stored = await stored_job(runtime, purge.job_id)
        if stored.retry_at is None:
            break
        async with runtime.transaction() as connection:
            await requeue_due_jobs(connection, now=stored.retry_at, limit=10)
            await mark_job_published(
                connection, job_id=purge.job_id, published_at=stored.retry_at
            )
        minutes += 31

    stored = await stored_job(runtime, purge.job_id)
    assert (stored.attempt, stored.retry_at) == (3, None)
    assert condition_of(stored, now=stored.queued_at) is JobCondition.EXHAUSTED
    assert [job.job_id for job in await load_jobs_needing_decision(runtime)] == [
        purge.job_id
    ]
    assert [row[2] for row in await attempts_of(runtime, purge.job_id)] == [
        "retry_scheduled",
        "retry_scheduled",
        "failed",
    ]


# An operator's decisions.


async def test_an_operator_retry_gives_an_exhausted_job_one_more_attempt(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    exhausted = await failed_for_good(runtime, purge, error_code="database_unavailable")
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(jobs).where(jobs.c.job_id == purge.job_id.value).values(attempt=3)
        )

    response = await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/retry")

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["failed_attempts"], body["max_attempts"]) == (
        "queued",
        3,
        4,
    )
    assert body["decisions"] == ["cancel"]
    async with runtime.transaction() as connection:
        published_at = await connection.scalar(
            sa.select(jobs.c.published_at).where(jobs.c.job_id == purge.job_id.value)
        )
    # Unpublished, so the next relay pass sends a new message.
    assert published_at is None
    [(action, actor, metadata)] = await decisions_of(runtime, exhausted.job_id)
    assert (action, actor) == (
        "job.retried",
        CALLERS["Bearer avery"].reader.user_id.value,
    )
    assert metadata == {
        "kind": "purge_document_chunks",
        "from_status": "failed",
        "failed_attempts": 3,
        "error_code": "database_unavailable",
    }


async def test_a_retry_is_refused_while_newer_work_is_open(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await failed_for_good(runtime, purge, error_code="database_unavailable")
    # The next synchronization pass asks for a new purge of the same Document.
    [newer] = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=STARTED_AT + timedelta(hours=1)
    )

    response = await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/retry")

    assert response.status_code == 409
    assert response.json()["code"] == "job_already_open"
    assert (await stored_job(runtime, purge.job_id)).status is JobStatus.FAILED
    assert (await stored_job(runtime, newer)).status is JobStatus.QUEUED
    assert await decisions_of(runtime, purge.job_id) == []


async def test_two_retries_of_the_same_work_at_once_leave_one_open(
    runtime: DatabaseRuntime,
) -> None:
    first = await published_purge(runtime, at=timedelta(0))
    await failed_for_good(runtime, first)
    async with runtime.transaction() as connection:
        second = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=first.target_id,
                queued_at=STARTED_AT,
            ),
        )
    await failed_for_good(runtime, second.job)
    operator = CALLERS["Bearer avery"].reader.user_id
    now = STARTED_AT + timedelta(hours=1)

    results = await asyncio.gather(
        *(
            retry_job(runtime, job_id=job.job_id, operator=operator, now=now)
            for job in (first, second.job)
        ),
        return_exceptions=True,
    )

    assert sorted(type(result).__name__ for result in results) == [
        "Job",
        "JobIntentAlreadyOpen",
    ]


async def test_cancel_closes_a_queued_job_and_its_message_does_nothing(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))

    response = await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/cancel")

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["decisions"]) == (
        "cancelled",
        [],
    )
    late = await run_stored_job(
        runtime, purge.job_id, now=STARTED_AT + timedelta(hours=3)
    )
    assert late.duplicate
    assert late.job.status is JobStatus.CANCELLED
    [(action, _actor, metadata)] = await decisions_of(runtime, purge.job_id)
    assert (action, metadata["from_status"]) == ("job.cancelled", "queued")


async def test_a_cancelled_purge_is_asked_for_again_while_its_chunks_remain(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/cancel")

    # Cancelling does not keep a deleted Document's Chunks: a pass queues
    # a new purge for them.
    requeued = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=STARTED_AT + timedelta(hours=3)
    )

    assert len(requeued) == 1
    assert requeued[0] != purge.job_id


async def test_cancelling_a_failed_job_keeps_why_it_failed(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await failed_for_good(runtime, purge, error_code="source_refused")

    response = await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/cancel")

    assert response.status_code == 200
    stored = await stored_job(runtime, purge.job_id)
    assert (stored.status, stored.last_error_code) == (
        JobStatus.CANCELLED,
        "source_refused",
    )


async def test_a_running_job_cannot_be_cancelled(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)

    response = await over_http(runtime, "POST", f"/v1/jobs/{job.job_id.value}/cancel")

    assert response.status_code == 409
    assert response.json()["code"] == "job_running"
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.RUNNING


async def test_a_decision_that_does_not_fit_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    retried = await over_http(runtime, "POST", f"/v1/jobs/{job.job_id.value}/retry")
    missing = await over_http(runtime, "POST", f"/v1/jobs/{uuid.uuid4()}/retry")

    assert (retried.status_code, retried.json()["code"]) == (
        409,
        "decision_not_allowed",
    )
    assert (missing.status_code, missing.json()["code"]) == (404, "job_not_found")


async def test_the_decision_list_shows_only_failures_nothing_replaced(
    runtime: DatabaseRuntime,
) -> None:
    replaced = await published_purge(runtime, at=timedelta(0))
    await failed_for_good(runtime, replaced, error_code="database_unavailable")
    [newer] = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=STARTED_AT + timedelta(hours=1)
    )
    async with runtime.transaction() as connection:
        newer_job = await stored_job(runtime, newer)
        newest = newer_job.start(started_at=STARTED_AT + timedelta(hours=1)).fail(
            error_code="database_unavailable",
            finished_at=STARTED_AT + timedelta(hours=1, minutes=1),
        )
        await save_job(connection, job=newest)

    response = await over_http(runtime, "GET", "/v1/job-decisions")

    assert response.status_code == 200
    [listed] = response.json()["items"]
    assert listed["job_id"] == str(newer.value)
    assert (listed["condition"], listed["decisions"]) == (
        "exhausted",
        ["retry", "cancel"],
    )


async def failed_purge_at(runtime: DatabaseRuntime, finished_at: datetime) -> Job:
    """A purge of a Document of its own, failed for good at `finished_at`."""
    async with runtime.transaction() as connection:
        queued = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=uuid.uuid4(),
                queued_at=OBSERVED_AT,
            ),
        )
        failed = queued.job.start(started_at=STARTED_AT).fail(
            error_code="database_rejected", finished_at=finished_at
        )
        await save_job(connection, job=failed)
    return failed


async def test_pages_of_decisions_never_repeat_or_skip_a_job(
    runtime: DatabaseRuntime,
) -> None:
    # Three Jobs fail in the same microsecond, so only their IDs order them.
    for minutes in (1, 2, 2, 2, 3):
        await failed_purge_at(runtime, STARTED_AT + timedelta(minutes=minutes))
    expected = [
        str(job.job_id.value) for job in await load_jobs_needing_decision(runtime)
    ]
    seen: list[str] = []
    query = "?limit=2"

    for _ in range(10):
        response = await over_http(runtime, "GET", f"/v1/job-decisions{query}")
        page = response.json()
        if not seen:
            # A Job that fails while the operator reads goes before the first
            # page, so it moves none of the pages after it.
            await failed_purge_at(runtime, STARTED_AT + timedelta(minutes=10))
        seen += [item["job_id"] for item in page["items"]]
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    assert seen == expected
    assert len(set(seen)) == 5


# The relay and the migration.


async def test_the_relay_recovers_before_it_requeues_and_publishes(
    runtime: DatabaseRuntime,
) -> None:
    from knowledge_service.ingestion.scheduling import SynchronizationSchedule
    from knowledge_service.worker.relay import relay_once

    job = await running_synchronization(runtime)
    await go_silent(runtime, job, minutes=6)
    sent: list[JobId] = []

    class Recording:
        kinds = frozenset({JobKind.PURGE_DOCUMENT_CHUNKS})

        def publish(self, job: Job) -> None:
            sent.append(job.job_id)

    done = await relay_once(
        runtime,
        publisher=Recording(),
        now=STARTED_AT + timedelta(minutes=10),
        schedule=SynchronizationSchedule(interval=timedelta(hours=1)),
    )

    assert done.recovered.stale == (job.job_id,)
    assert not done.recovery_failed


def test_the_migration_turns_cancelled_jobs_into_final_failures_on_the_way_down(
    migrated_database: str,
) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    job_id = uuid.uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "insert into jobs (job_id, kind, target_id, status, attempt,"
                    " max_attempts, queued_at, finished_at) values (:job_id,"
                    " 'purge_document_chunks', :target_id, 'cancelled', 0, 3,"
                    " :at, :at)"
                ),
                {"job_id": job_id, "target_id": uuid.uuid4(), "at": OBSERVED_AT},
            )
        with migration_environment(url):
            command.downgrade(config, "c9d0e1f2a3b4")
        with engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "select status, last_error_code, started_at from jobs"
                    " where job_id = :job_id"
                ),
                {"job_id": job_id},
            ).one()
        assert tuple(row) == ("failed", "cancelled", OBSERVED_AT)
    finally:
        with migration_environment(url):
            command.upgrade(config, "head")
        engine.dispose()


async def test_the_migration_will_not_go_down_past_recorded_decisions(
    migrated_database: str, runtime: DatabaseRuntime
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await cancel_job(
        runtime,
        job_id=purge.job_id,
        operator=CALLERS["Bearer avery"].reader.user_id,
        now=STARTED_AT,
    )
    url = make_url(migrated_database)

    def downgrade() -> None:
        with migration_environment(url):
            command.downgrade(migration_config(), "c9d0e1f2a3b4")

    with pytest.raises(DBAPIError, match="operator decisions are recorded"):
        await asyncio.to_thread(downgrade)
    # Nothing changed: the Job is still cancelled.
    assert (await stored_job(runtime, purge.job_id)).status is JobStatus.CANCELLED


async def test_the_database_refuses_a_cancelled_job_with_a_planned_retry(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))

    with pytest.raises(DBAPIError, match="ck_jobs_retry_at"):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.update(jobs)
                .where(jobs.c.job_id == purge.job_id.value)
                .values(
                    status="cancelled",
                    finished_at=STARTED_AT,
                    retry_at=STARTED_AT,
                )
            )


# Found by the adversarial review.


async def test_a_worker_whose_clock_runs_behind_is_not_taken_for_dead(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    # The worker reports just now, but its clock runs six minutes behind
    # the relay's: by the worker's stamps it looks silent.
    await heartbeat(runtime, job, minutes=4, processed=40)

    report = await recover(runtime, minutes=10)

    assert report.stale == ()
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.RUNNING


async def test_a_purge_queued_by_a_clock_ahead_of_the_worker_still_runs(
    runtime: DatabaseRuntime,
) -> None:
    _document, job_id = await tombstoned_with_purge(runtime)

    # Queued at 09:00 by a clock ten seconds ahead of this worker's.
    ran = await purge_document_chunks(
        runtime, job_id=job_id, now=OBSERVED_AT - timedelta(seconds=10)
    )

    assert ran.status is JobStatus.SUCCEEDED
    assert ran.started_at == OBSERVED_AT


async def test_a_job_queued_by_a_clock_ahead_does_not_crowd_out_a_real_orphan(
    runtime: DatabaseRuntime,
) -> None:
    orphan = await published_purge(runtime, at=timedelta(0))
    async with runtime.transaction() as connection:
        ahead = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=SOURCE_ID.value,
                queued_at=OBSERVED_AT + timedelta(days=1),
            ),
        )
        # Published earlier than the orphan, so it would be tried first.
        await mark_job_published(
            connection,
            job_id=ahead.job.job_id,
            published_at=OBSERVED_AT - timedelta(hours=1),
        )

    report = await recover_stuck_jobs(
        runtime, now=STARTED_AT + timedelta(minutes=29), retry_policy=POLICY, limit=1
    )

    assert report.orphaned == (orphan.job_id,)


async def test_a_refused_report_still_shows_the_worker_is_alive(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=4, processed=40)
    await go_silent(runtime, job, minutes=6)
    # The worker's clock stepped back three minutes: its report is refused,
    # yet it was sent just now.
    with pytest.raises(ProgressWentBackwards):
        await heartbeat(runtime, job, minutes=1, processed=50)

    report = await recover(runtime, minutes=10)

    assert report.stale == ()
    assert (await latest(runtime, job.job_id)).counters.processed == 40


async def test_a_refusal_stays_typed_when_recording_the_worker_fails(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=4, processed=40)

    async def unreachable(*args: object, **kwargs: object) -> bool:
        raise database_went_away()

    monkeypatch.setattr(job_reporting, "record_heard", unreachable)

    with pytest.raises(ProgressWentBackwards):
        await heartbeat(runtime, job, minutes=1, processed=50)
