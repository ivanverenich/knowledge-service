"""Persist before enqueue, against real PostgreSQL.

A fake publisher stands in for Redis here, so each test can make the queue
refuse a message or the process "crash" at an exact point.
"""

import asyncio
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from dramatiq.brokers.stub import StubBroker
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from job_fixtures import (
    OBSERVED_AT,
    chunk_count,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.job_dispatch import (
    PublishFailed,
    PublishOutcome,
    RelayReport,
    Submission,
    UnsupportedJobKind,
    publish_job,
    publish_pending_jobs,
    submit_job,
)
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence.jobs import enqueue_job, save_job
from knowledge_service.persistence.schema import jobs
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import QueueName, declare_queues, queue_middleware
from knowledge_service.worker.jobs import (
    HANDLERS,
    DramatiqJobPublisher,
    InvalidJobMessage,
    declare_job_actors,
    run_stored_job,
)

pytestmark = pytest.mark.integration

NOW = OBSERVED_AT + timedelta(minutes=5)
LATER = OBSERVED_AT + timedelta(minutes=10)
PURGE = JobKind.PURGE_DOCUMENT_CHUNKS
RUNNABLE = frozenset({PURGE})


@dataclass
class FakePublisher:
    """Records every message it is asked to send, and can fail on purpose."""

    kinds: frozenset[JobKind] = RUNNABLE
    sent: list[JobId] = field(default_factory=lambda: list[JobId]())
    # Called with each Job before it counts as sent; may raise.
    before_send: Callable[[Job], None] | None = None
    # Called after the message counts as sent; may raise.
    after_send: Callable[[Job], None] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def publish(self, job: Job) -> None:
        if self.before_send is not None:
            self.before_send(job)
        with self.lock:
            self.sent.append(job.job_id)
        if self.after_send is not None:
            self.after_send(job)


def refuse(_job: Job) -> None:
    raise PublishFailed("the queue is down")


def crash(_job: Job) -> None:
    raise RuntimeError("the process stopped before recording the send")


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


def new_job(kind: JobKind = PURGE) -> Job:
    return Job.request(
        job_id=JobId.new(), kind=kind, target_id=uuid.uuid4(), queued_at=OBSERVED_AT
    )


async def published_at(runtime: DatabaseRuntime, job_id: JobId) -> datetime | None:
    async with runtime.transaction() as connection:
        return await connection.scalar(
            sa.select(jobs.c.published_at).where(jobs.c.job_id == job_id.value)
        )


async def job_count(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        result = await connection.execute(sa.select(sa.func.count()).select_from(jobs))
        return result.scalar_one()


async def test_a_job_is_committed_before_its_message_is_sent(
    runtime: DatabaseRuntime, migrated_database: str
) -> None:
    seen_at_send: list[tuple[str, bool]] = []
    other = sa.create_engine(
        make_url(migrated_database).set(drivername="postgresql+psycopg")
    )

    def check_committed(job: Job) -> None:
        # Another connection sees the row only once it is committed.
        with other.connect() as connection:
            row = connection.execute(
                sa.select(jobs.c.status, jobs.c.published_at).where(
                    jobs.c.job_id == job.job_id.value
                )
            ).one()
        seen_at_send.append((row.status, row.published_at is None))

    job = new_job()
    publisher = FakePublisher(before_send=check_committed)

    try:
        submission = await submit_job(runtime, job, publisher=publisher, now=NOW)
    finally:
        other.dispose()

    assert submission == Submission(job_id=job.job_id, published=True)
    assert seen_at_send == [("queued", True)]
    assert publisher.sent == [job.job_id]
    assert await published_at(runtime, job.job_id) == NOW


async def test_a_refused_message_leaves_the_job_stored_for_the_relay(
    runtime: DatabaseRuntime,
) -> None:
    job = new_job()

    submission = await submit_job(
        runtime, job, publisher=FakePublisher(before_send=refuse), now=NOW
    )

    assert submission == Submission(job_id=job.job_id, published=False)
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.QUEUED
    assert await published_at(runtime, job.job_id) is None

    relay = FakePublisher()
    first = await publish_pending_jobs(runtime, publisher=relay, now=LATER)
    second = await publish_pending_jobs(runtime, publisher=relay, now=LATER)

    assert first == RelayReport(published=1, skipped=0, failed=0, untried=0)
    assert second == RelayReport(published=0, skipped=0, failed=0, untried=0)
    assert relay.sent == [job.job_id]
    assert await published_at(runtime, job.job_id) == LATER


async def test_a_crash_after_sending_is_repaired_by_sending_again(
    runtime: DatabaseRuntime,
) -> None:
    job = new_job()
    publisher = FakePublisher(after_send=crash)

    submission = await submit_job(runtime, job, publisher=publisher, now=NOW)

    # The message left, but the send was never recorded.
    assert submission == Submission(job_id=job.job_id, published=False)
    assert publisher.sent == [job.job_id]
    assert await published_at(runtime, job.job_id) is None

    relay = FakePublisher()
    report = await publish_pending_jobs(runtime, publisher=relay, now=LATER)

    assert report.published == 1
    assert relay.sent == [job.job_id]
    assert await published_at(runtime, job.job_id) == LATER


async def test_the_relay_finds_jobs_stored_inside_another_transaction(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    assert await published_at(runtime, purge_id) is None

    relay = FakePublisher()
    report = await publish_pending_jobs(runtime, publisher=relay, now=NOW)

    assert report.published == 1
    assert relay.sent == [purge_id]


async def test_two_relays_at_once_send_each_job_once(
    runtime: DatabaseRuntime,
) -> None:
    stored = [new_job() for _ in range(6)]
    async with runtime.transaction() as connection:
        for job in stored:
            await enqueue_job(connection, job)

    def slow(_job: Job) -> None:
        time.sleep(0.05)

    publisher = FakePublisher(before_send=slow)
    reports = await asyncio.gather(
        publish_pending_jobs(runtime, publisher=publisher, now=NOW),
        publish_pending_jobs(runtime, publisher=publisher, now=NOW),
    )

    assert sorted(publisher.sent, key=str) == sorted(
        (job.job_id for job in stored), key=str
    )
    assert sum(report.published for report in reports) == 6


async def test_the_relay_stops_at_the_first_refused_message(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        for _ in range(3):
            await enqueue_job(connection, new_job())
    attempts: list[Job] = []

    def count_and_refuse(job: Job) -> None:
        attempts.append(job)
        refuse(job)

    report = await publish_pending_jobs(
        runtime, publisher=FakePublisher(before_send=count_and_refuse), now=NOW
    )

    assert report == RelayReport(published=0, skipped=0, failed=1, untried=2)
    assert len(attempts) == 1


async def test_the_relay_publishes_oldest_first_up_to_its_limit(
    runtime: DatabaseRuntime,
) -> None:
    older = Job.request(
        job_id=JobId.new(), kind=PURGE, target_id=uuid.uuid4(), queued_at=OBSERVED_AT
    )
    newer = Job.request(
        job_id=JobId.new(), kind=PURGE, target_id=uuid.uuid4(), queued_at=NOW
    )
    async with runtime.transaction() as connection:
        await enqueue_job(connection, newer)
        await enqueue_job(connection, older)
    relay = FakePublisher()

    report = await publish_pending_jobs(runtime, publisher=relay, now=LATER, limit=1)

    assert report.published == 1
    assert relay.sent == [older.job_id]


async def test_jobs_no_worker_runs_are_neither_submitted_nor_relayed(
    runtime: DatabaseRuntime,
) -> None:
    with pytest.raises(UnsupportedJobKind):
        await submit_job(
            runtime,
            new_job(JobKind.SYNCHRONIZE_SOURCE),
            publisher=FakePublisher(),
            now=NOW,
        )
    assert await job_count(runtime) == 0

    waiting = new_job(JobKind.PURGE_CONVERSATION)
    async with runtime.transaction() as connection:
        await enqueue_job(connection, waiting)
    relay = FakePublisher()

    report = await publish_pending_jobs(runtime, publisher=relay, now=NOW)

    assert report.published == 0
    assert relay.sent == []
    assert await published_at(runtime, waiting.job_id) is None


async def test_a_job_queued_again_needs_a_new_message(
    runtime: DatabaseRuntime,
) -> None:
    job = new_job()
    await submit_job(runtime, job, publisher=FakePublisher(), now=NOW)
    async with runtime.transaction() as connection:
        failed = job.start(started_at=NOW).fail(
            error_code="boom", finished_at=NOW, retry_at=LATER
        )
        await save_job(connection, job=failed)
        await save_job(connection, job=failed.retry(queued_at=LATER))
    assert await published_at(runtime, job.job_id) is None

    relay = FakePublisher()
    await publish_pending_jobs(runtime, publisher=relay, now=LATER)

    assert relay.sent == [job.job_id]


async def test_a_finished_or_running_job_is_never_published(
    runtime: DatabaseRuntime,
) -> None:
    job = new_job()
    async with runtime.transaction() as connection:
        await enqueue_job(connection, job)
        await save_job(connection, job=job.start(started_at=NOW))
    publisher = FakePublisher()

    outcome = await publish_job(
        runtime, job_id=job.job_id, publisher=publisher, now=NOW
    )

    assert outcome is PublishOutcome.SKIPPED
    assert publisher.sent == []


async def test_only_a_new_queued_job_can_be_submitted(runtime: DatabaseRuntime) -> None:
    running = new_job().start(started_at=NOW)

    with pytest.raises(InvalidJob):
        await submit_job(runtime, running, publisher=FakePublisher(), now=NOW)

    assert await job_count(runtime) == 0


@pytest.mark.parametrize("limit", [0, 1_001, True, 2.0])
async def test_the_relay_limit_is_bounded(runtime: DatabaseRuntime, limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        await publish_pending_jobs(
            runtime, publisher=FakePublisher(), now=NOW, limit=limit
        )


async def test_times_must_be_utc(runtime: DatabaseRuntime) -> None:
    local = datetime(2026, 11, 21, 9, 0)
    with pytest.raises(ValueError, match="UTC"):
        await submit_job(runtime, new_job(), publisher=FakePublisher(), now=local)
    assert await job_count(runtime) == 0


async def test_a_duplicate_message_runs_the_job_once(
    runtime: DatabaseRuntime,
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    assert await chunk_count(runtime, document.document_id) > 0

    first = await run_stored_job(runtime, purge_id, now=NOW)
    second = await run_stored_job(runtime, purge_id, now=LATER)

    assert not first.duplicate
    assert first.job.status is JobStatus.SUCCEEDED
    # The duplicate reports the outcome the first message produced.
    assert second.duplicate
    assert second.job == first.job
    assert await chunk_count(runtime, document.document_id) == 0
    stored = await stored_job(runtime, purge_id)
    assert (stored.status, stored.attempt, stored.finished_at) == (
        JobStatus.SUCCEEDED,
        0,
        NOW,
    )


async def test_two_messages_at_once_run_the_job_once(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)

    results = await asyncio.gather(
        run_stored_job(runtime, purge_id, now=NOW),
        run_stored_job(runtime, purge_id, now=NOW),
    )

    assert sorted(result.duplicate for result in results) == [False, True]
    stored = await stored_job(runtime, purge_id)
    assert stored.status is JobStatus.SUCCEEDED
    assert [result.job for result in results] == [stored, stored]


async def test_a_message_for_no_stored_job_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    with pytest.raises(InvalidJobMessage):
        await run_stored_job(runtime, JobId.new(), now=NOW)


async def test_a_message_for_a_kind_without_a_handler_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    waiting = new_job(JobKind.PURGE_CONVERSATION)
    async with runtime.transaction() as connection:
        await enqueue_job(connection, waiting)

    with pytest.raises(InvalidJobMessage):
        await run_stored_job(runtime, waiting.job_id, now=NOW)

    assert (await stored_job(runtime, waiting.job_id)).status is JobStatus.QUEUED


async def test_the_real_publisher_leaves_kinds_without_a_handler_waiting(
    runtime: DatabaseRuntime,
) -> None:
    broker = StubBroker(middleware=queue_middleware())
    declare_queues(broker)
    try:
        publisher = DramatiqJobPublisher(
            declare_job_actors(
                broker, run_job=lambda _job_id: None, kinds=frozenset(HANDLERS)
            )
        )
        conversation_purge = new_job(JobKind.PURGE_CONVERSATION)
        async with runtime.transaction() as connection:
            await enqueue_job(connection, conversation_purge)

        with pytest.raises(UnsupportedJobKind):
            await submit_job(
                runtime,
                new_job(JobKind.PURGE_CONVERSATION),
                publisher=publisher,
                now=NOW,
            )
        report = await publish_pending_jobs(runtime, publisher=publisher, now=NOW)

        assert report == RelayReport(published=0, skipped=0, failed=0, untried=0)
        assert await published_at(runtime, conversation_purge.job_id) is None
        assert broker.queues[QueueName.MAINTENANCE].qsize() == 0
    finally:
        broker.close()
