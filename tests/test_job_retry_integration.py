"""Retries owned by the stored Job, against real PostgreSQL.

A failed attempt is classified. A transient one gets a retry time, and the
retry pass queues the Job again once that time has come, until its attempts
run out. A permanent one is never queued again.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic import command
from dramatiq.middleware.time_limit import TimeLimitExceeded
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from database_fixtures import migration_config, migration_environment
from job_fixtures import (
    OBSERVED_AT,
    SOURCE_ID,
    chunk_count,
    database_went_away,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, JobId
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import (
    purge_document_chunks,
    requeue_unfinished_purges,
)
from knowledge_service.job_dispatch import publish_pending_jobs, requeue_due_retries
from knowledge_service.job_failures import JobFailure, RetryPolicy
from knowledge_service.jobs import Job, JobAlreadyClaimed, JobKind, JobStatus
from knowledge_service.persistence.jobs import enqueue_job, requeue_due_jobs, save_job
from knowledge_service.persistence.schema import chunks, jobs
from knowledge_service.settings import Settings

pytestmark = pytest.mark.integration

NOW = OBSERVED_AT + timedelta(minutes=5)
FAR_FUTURE = OBSERVED_AT + timedelta(days=30)
# The middle of every delay window, so retry times are exact in assertions.
POLICY = RetryPolicy(jitter=lambda: 0.5)


class RecordingPublisher:
    """Records every Job it is asked to send."""

    kinds = frozenset({JobKind.PURGE_DOCUMENT_CHUNKS})

    def __init__(self) -> None:
        self.sent: list[JobId] = []

    def publish(self, job: Job) -> None:
        self.sent.append(job.job_id)


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


async def job_ids(runtime: DatabaseRuntime) -> list[JobId]:
    async with runtime.transaction() as connection:
        return [
            JobId(job_id)
            for job_id in await connection.scalars(sa.select(jobs.c.job_id))
        ]


async def drop_my_connection(
    connection: AsyncConnection, *, document_id: DocumentId
) -> int:
    # The server ends this session, as a restart or a failover would.
    await connection.execute(sa.text("select pg_terminate_backend(pg_backend_pid())"))
    return 0


async def lost_connection(
    connection: AsyncConnection, *, document_id: DocumentId
) -> int:
    raise database_went_away()


async def bug(connection: AsyncConnection, *, document_id: DocumentId) -> int:
    raise KeyError("a programming error")


async def purge(runtime: DatabaseRuntime, job_id: JobId, now: datetime) -> Job:
    return await purge_document_chunks(
        runtime, job_id=job_id, now=now, retry_policy=POLICY
    )


async def test_a_transient_failure_is_queued_again_only_when_due(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", lost_connection)

    failed = await purge(runtime, purge_id, NOW)

    assert (failed.status, failed.attempt, failed.last_error_code) == (
        JobStatus.FAILED,
        1,
        JobFailure.DATABASE_UNAVAILABLE,
    )
    assert failed.retry_at == NOW + POLICY.delay(1)
    due = failed.retry_at
    assert due is not None
    assert await requeue_due_retries(runtime, now=due - timedelta(seconds=1)) == ()
    assert await requeue_due_retries(runtime, now=due) == (purge_id,)

    requeued = await stored_job(runtime, purge_id)
    assert (requeued.status, requeued.attempt, requeued.retry_at) == (
        JobStatus.QUEUED,
        1,
        None,
    )
    # Queued again means unpublished again: the relay sends a new message.
    publisher = RecordingPublisher()
    await publish_pending_jobs(runtime, publisher=publisher, now=due)
    assert publisher.sent == [purge_id]

    monkeypatch.undo()
    succeeded = await purge(runtime, purge_id, due)
    assert (succeeded.status, succeeded.attempt) == (JobStatus.SUCCEEDED, 1)
    assert await chunk_count(runtime, document.document_id) == 0


async def test_a_transient_failure_stops_at_the_attempt_budget(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", lost_connection)
    now = NOW
    attempts: list[Job] = []

    for _ in range(10):
        attempts.append(await purge(runtime, purge_id, now))
        if attempts[-1].retry_at is None:
            break
        now = attempts[-1].retry_at
        assert await requeue_due_retries(runtime, now=now) == (purge_id,)

    last = attempts[-1]
    assert len(attempts) == last.max_attempts == 3
    assert (last.status, last.attempt, last.retry_at) == (JobStatus.FAILED, 3, None)
    assert [job.retry_at for job in attempts[:-1]] == [
        NOW + POLICY.delay(1),
        NOW + POLICY.delay(1) + POLICY.delay(2),
    ]
    assert await requeue_due_retries(runtime, now=FAR_FUTURE) == ()


async def test_a_permanent_failure_is_never_queued_again(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", bug)

    failed = await purge(runtime, purge_id, NOW)

    assert (failed.attempt, failed.last_error_code, failed.retry_at) == (
        1,
        JobFailure.PROGRAMMING_ERROR,
        None,
    )
    assert await requeue_due_retries(runtime, now=FAR_FUTURE) == ()
    assert (await stored_job(runtime, purge_id)).status is JobStatus.FAILED


async def test_a_permanent_purge_failure_is_not_asked_for_again_by_later_passes(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", bug)
    await purge(runtime, purge_id, NOW)

    # Each synchronization pass asks for unfinished purges; none comes back.
    for _ in range(3):
        assert (
            await requeue_unfinished_purges(
                runtime, source_id=SOURCE_ID, now=FAR_FUTURE
            )
            == ()
        )

    assert await job_ids(runtime) == [purge_id]
    assert await chunk_count(runtime, document.document_id) > 0


async def test_a_permanent_failure_stops_the_loop_even_after_a_late_retry(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", lost_connection)
    now = NOW
    while (failed := await purge(runtime, purge_id, now)).retry_at is not None:
        now = failed.retry_at
        await requeue_due_retries(runtime, now=now)
    # A synchronization pass that started before that last retry asks for a
    # new purge, which then fails permanently.
    monkeypatch.setattr(deletions, "delete_chunks", bug)
    [second] = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=NOW + timedelta(seconds=1)
    )
    await purge(runtime, second, now + timedelta(hours=1))

    for hours in (2, 3, 4):
        assert (
            await requeue_unfinished_purges(
                runtime, source_id=SOURCE_ID, now=NOW + timedelta(seconds=hours)
            )
            == ()
        )
    assert len(await job_ids(runtime)) == 2
    assert await chunk_count(runtime, document.document_id) > 0


async def test_a_lost_attempt_is_not_recorded_against_a_later_one(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    load_job = deletions.load_job
    loads: list[JobId] = []

    async def meanwhile_another_delivery(
        connection: AsyncConnection, *, job_id: JobId, for_update: bool = False
    ) -> Job | None:
        loads.append(job_id)
        # The second load is this delivery's own, after its connection died.
        # Before it, another delivery fails the Job and it is queued again.
        if len(loads) == 2:
            monkeypatch.setattr(deletions, "delete_chunks", lost_connection)
            other = await purge(runtime, purge_id, NOW)
            assert other.retry_at is not None
            await requeue_due_retries(runtime, now=other.retry_at)
        return await load_job(connection, job_id=job_id, for_update=for_update)

    monkeypatch.setattr(deletions, "load_job", meanwhile_another_delivery)
    monkeypatch.setattr(deletions, "delete_chunks", drop_my_connection)

    # This delivery's clock is an hour ahead of the relay's.
    with pytest.raises(JobAlreadyClaimed, match="another delivery") as refused:
        await purge(runtime, purge_id, NOW + timedelta(hours=1))

    stored = await stored_job(runtime, purge_id)
    assert (stored.status, stored.attempt) == (JobStatus.QUEUED, 1)
    assert refused.value.job == stored


async def test_a_lost_connection_is_recorded_as_a_retried_failure(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)

    monkeypatch.setattr(deletions, "delete_chunks", drop_my_connection)
    failed = await purge(runtime, purge_id, NOW)

    assert (failed.status, failed.attempt, failed.last_error_code) == (
        JobStatus.FAILED,
        1,
        JobFailure.DATABASE_UNAVAILABLE,
    )
    assert failed.retry_at == NOW + POLICY.delay(1)
    assert await stored_job(runtime, purge_id) == failed
    assert await chunk_count(runtime, document.document_id) > 0


@pytest.mark.parametrize(
    "interrupt", [asyncio.CancelledError, TimeLimitExceeded], ids=["cancel", "limit"]
)
async def test_a_cancelled_attempt_leaves_the_job_queued_and_nothing_deleted(
    runtime: DatabaseRuntime,
    monkeypatch: pytest.MonkeyPatch,
    interrupt: type[BaseException],
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    chunks_before = await chunk_count(runtime, document.document_id)

    async def interrupted(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        await connection.execute(
            sa.delete(chunks).where(chunks.c.document_id == document_id.value)
        )
        raise interrupt()

    monkeypatch.setattr(deletions, "delete_chunks", interrupted)
    with pytest.raises(interrupt):
        await purge(runtime, purge_id, NOW)

    # Rolled back whole: the stuck-work sweep (M08-T08) owns this Job now.
    stored = await stored_job(runtime, purge_id)
    assert (stored.status, stored.attempt) == (JobStatus.QUEUED, 0)
    assert await chunk_count(runtime, document.document_id) == chunks_before
    assert await requeue_due_retries(runtime, now=FAR_FUTURE) == ()


async def test_two_retry_passes_at_once_queue_each_job_once(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        for _ in range(4):
            job = Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=uuid.uuid4(),
                queued_at=OBSERVED_AT,
            )
            await enqueue_job(connection, job)
            failed = job.start(started_at=NOW).fail(
                error_code="boom", finished_at=NOW, retry_at=NOW
            )
            await save_job(connection, job=failed)

    async def pass_and_hold() -> tuple[Job, ...]:
        async with runtime.transaction() as connection:
            requeued = await requeue_due_jobs(connection, now=NOW, limit=10)
            # Keep the rows locked, so the other pass runs meanwhile.
            await asyncio.sleep(0.2)
            return requeued

    first, second = await asyncio.gather(pass_and_hold(), pass_and_hold())

    ids = [job.job_id for job in first + second]
    assert len(ids) == len(set(ids)) == 4


async def test_the_retry_pass_takes_the_oldest_retry_first_up_to_its_limit(
    runtime: DatabaseRuntime,
) -> None:
    stored: list[Job] = []
    async with runtime.transaction() as connection:
        for minutes in (2, 1):
            job = Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=uuid.uuid4(),
                queued_at=OBSERVED_AT,
            )
            await enqueue_job(connection, job)
            retry_at = NOW + timedelta(minutes=minutes)
            failed = job.start(started_at=NOW).fail(
                error_code="boom", finished_at=NOW, retry_at=retry_at
            )
            stored.append(await save_job(connection, job=failed))

    requeued = await requeue_due_retries(runtime, now=FAR_FUTURE, limit=1)

    assert requeued == (stored[1].job_id,)


@pytest.mark.parametrize("limit", [0, 1_001, True, 2.0])
async def test_the_retry_pass_limit_is_bounded(
    runtime: DatabaseRuntime, limit: object
) -> None:
    with pytest.raises(ValueError, match="limit"):
        await requeue_due_retries(runtime, now=NOW, limit=limit)  # pyright: ignore[reportArgumentType]


async def test_the_retry_pass_needs_utc(runtime: DatabaseRuntime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        await requeue_due_retries(runtime, now=datetime(2026, 11, 23, 9, 0))


async def test_the_database_refuses_a_retry_time_on_a_job_that_did_not_fail(
    runtime: DatabaseRuntime,
) -> None:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_DOCUMENT_CHUNKS,
        target_id=uuid.uuid4(),
        queued_at=OBSERVED_AT,
    )
    async with runtime.transaction() as connection:
        await enqueue_job(connection, job)

    with pytest.raises(IntegrityError, match="ck_jobs_retry_at"):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.update(jobs)
                .where(jobs.c.job_id == job.job_id.value)
                .values(retry_at=NOW)
            )


def test_the_migration_schedules_open_failures_and_keeps_final_ones_closed(
    migrated_database: str,
) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    with migration_environment(url):
        command.downgrade(config, "e5f6a7b8c9d0")
    insert_failed = sa.text(
        "insert into jobs (job_id, kind, target_id, status, attempt, max_attempts,"
        " queued_at, started_at, finished_at, last_error_code) values (:job_id,"
        " 'purge_document_chunks', :target_id, 'failed', :attempt, 3, :at, :at,"
        " :at, 'chunk_purge_failed')"
    )
    with_attempts_left, used_up = uuid.uuid4(), uuid.uuid4()
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            for job_id, attempt in ((with_attempts_left, 1), (used_up, 3)):
                connection.execute(
                    insert_failed,
                    {
                        "job_id": job_id,
                        "target_id": uuid.uuid4(),
                        "attempt": attempt,
                        "at": NOW,
                    },
                )

        with migration_environment(url):
            command.upgrade(config, "head")
        with engine.connect() as connection:
            retry_at = dict(
                connection.execute(sa.select(jobs.c.job_id, jobs.c.retry_at)).all()
            )

        # Open before, so still open: due at once. Used up stays closed.
        assert retry_at == {with_attempts_left: NOW, used_up: None}

        # A permanent failure with attempts left must stay closed on the way down.
        with engine.begin() as connection:
            connection.execute(
                sa.update(jobs)
                .where(jobs.c.job_id == with_attempts_left)
                .values(retry_at=None)
            )
        with migration_environment(url):
            command.downgrade(config, "e5f6a7b8c9d0")
        with engine.connect() as connection:
            attempt = connection.scalar(
                sa.text("select attempt from jobs where job_id = :job_id"),
                {"job_id": with_attempts_left},
            )
        assert attempt == 3
        with migration_environment(url):
            command.upgrade(config, "head")
    finally:
        engine.dispose()
