"""One open Job per intent, against real PostgreSQL.

A Job's intent is its kind and target: "purge the Chunks of Document D".
Asking for the same intent again, even at the same moment, returns the Job
that is already open instead of storing a second one.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from database_fixtures import migration_config, migration_environment
from job_fixtures import (
    OBSERVED_AT,
    SOURCE_ID,
    chunk_count,
    insert_source,
    offer_retired,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, JobId
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import (
    PURGE_FAILED,
    purge_document_chunks,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.job_dispatch import PublishFailed, Submission, submit_job
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence import jobs as job_store
from knowledge_service.persistence.jobs import (
    Enqueued,
    JobIntentContended,
    enqueue_job,
    save_job,
)
from knowledge_service.persistence.schema import jobs
from knowledge_service.settings import Settings
from knowledge_service.worker.jobs import run_stored_job

pytestmark = pytest.mark.integration

NOW = OBSERVED_AT + timedelta(minutes=5)
LATER = OBSERVED_AT + timedelta(minutes=10)
PURGE = JobKind.PURGE_DOCUMENT_CHUNKS
TARGET = uuid.UUID("5f1d2c3b-4a59-4e6f-8a7b-9c0d1e2f3a4b")


@dataclass
class RecordingPublisher:
    """Records every message it is asked to send; can refuse them all."""

    refuse: bool = False
    kinds: frozenset[JobKind] = frozenset({PURGE})
    sent: list[JobId] = field(default_factory=lambda: list[JobId]())

    def publish(self, job: Job) -> None:
        if self.refuse:
            raise PublishFailed("the queue is down")
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


def new_job(kind: JobKind = PURGE, target_id: uuid.UUID = TARGET) -> Job:
    return Job.request(
        job_id=JobId.new(), kind=kind, target_id=target_id, queued_at=OBSERVED_AT
    )


async def enqueue(runtime: DatabaseRuntime, job: Job) -> Enqueued:
    async with runtime.transaction() as connection:
        return await enqueue_job(connection, job)


async def broken_delete(connection: AsyncConnection, *, document_id: DocumentId) -> int:
    raise ConnectionError("the database went away")


async def stored_job_ids(runtime: DatabaseRuntime) -> list[JobId]:
    async with runtime.transaction() as connection:
        rows = await connection.scalars(sa.select(jobs.c.job_id))
        return [JobId(job_id) for job_id in rows]


async def test_asking_twice_for_the_same_work_leaves_one_job(
    runtime: DatabaseRuntime,
) -> None:
    first = await enqueue(runtime, new_job())
    second = await enqueue(runtime, new_job())

    assert first.created
    assert not second.created
    assert second.job == first.job
    assert await stored_job_ids(runtime) == [first.job.job_id]


@pytest.mark.parametrize(
    ("kind", "target_id"),
    [
        (JobKind.PURGE_CONVERSATION, TARGET),
        (PURGE, uuid.UUID("0c9b8a7d-6e5f-4a3b-8c1d-2e3f4a5b6c7d")),
    ],
)
async def test_another_kind_or_target_is_other_work(
    runtime: DatabaseRuntime, kind: JobKind, target_id: uuid.UUID
) -> None:
    await enqueue(runtime, new_job())

    other = await enqueue(runtime, new_job(kind, target_id))

    assert other.created
    assert len(await stored_job_ids(runtime)) == 2


async def test_a_job_that_can_do_no_more_work_does_not_block_new_work(
    runtime: DatabaseRuntime,
) -> None:
    done = (await enqueue(runtime, new_job())).job
    async with runtime.transaction() as connection:
        await save_job(
            connection, job=done.start(started_at=NOW).succeed(finished_at=NOW)
        )

    again = await enqueue(runtime, new_job())

    assert again.created
    assert again.job.job_id != done.job_id


async def test_two_requests_at_once_store_one_job(runtime: DatabaseRuntime) -> None:
    async def request_and_hold() -> Enqueued:
        async with runtime.transaction() as connection:
            enqueued = await enqueue_job(connection, new_job())
            # Keep the transaction open, so the other request runs meanwhile.
            await asyncio.sleep(0.2)
            return enqueued

    results = await asyncio.gather(request_and_hold(), request_and_hold())

    assert sorted(result.created for result in results) == [False, True]
    assert results[0].job.job_id == results[1].job.job_id
    assert len(await stored_job_ids(runtime)) == 1


async def test_the_database_refuses_a_second_open_job_for_one_intent(
    runtime: DatabaseRuntime,
) -> None:
    await enqueue(runtime, new_job())
    second = new_job()

    with pytest.raises(IntegrityError, match="uq_jobs_open_intent"):
        async with runtime.transaction() as connection:
            # A plain insert, as code that skipped `enqueue_job` would do.
            await connection.execute(
                sa.insert(jobs).values(
                    job_id=second.job_id.value,
                    kind=second.kind.value,
                    target_id=second.target_id,
                    status=second.status.value,
                    attempt=0,
                    max_attempts=second.max_attempts,
                    queued_at=second.queued_at,
                )
            )


async def test_an_open_job_that_keeps_finishing_is_a_typed_failure(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await enqueue(runtime, new_job())

    async def always_finished(*_args: object, **_kwargs: object) -> None:
        return None

    # Each round finds the conflict but never the open Job behind it.
    monkeypatch.setattr(job_store, "load_open_job", always_finished)
    with pytest.raises(JobIntentContended):
        await enqueue(runtime, new_job())

    assert await stored_job_ids(runtime) == [first.job.job_id]


async def test_a_document_tombstoned_again_reuses_its_open_purge(
    runtime: DatabaseRuntime,
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    # The Source offers the Document again, then stops offering it.
    await offer_retired(runtime, observed_at=NOW)
    [again] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )

    assert again.purge_job_id == purge_id
    assert await stored_job_ids(runtime) == [purge_id]
    assert await chunk_count(runtime, document.document_id) > 0


async def test_two_requeues_at_once_leave_one_open_purge(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    # The purge ended without deleting anything, so the Chunks still need one.
    finished = (await stored_job(runtime, purge_id)).start(started_at=NOW)
    async with runtime.transaction() as connection:
        await save_job(connection, job=finished.succeed(finished_at=NOW))

    results = await asyncio.gather(
        requeue_unfinished_purges(runtime, source_id=SOURCE_ID, now=LATER),
        requeue_unfinished_purges(runtime, source_id=SOURCE_ID, now=LATER),
    )

    requeued = {job_id for result in results for job_id in result}
    assert len(requeued) == 1
    [requeued_id] = requeued
    assert (await stored_job(runtime, requeued_id)).status is JobStatus.QUEUED
    assert len(await stored_job_ids(runtime)) == 2


async def test_submitting_the_same_work_twice_returns_the_open_job(
    runtime: DatabaseRuntime,
) -> None:
    publisher = RecordingPublisher()
    first = await submit_job(runtime, new_job(), publisher=publisher, now=NOW)

    second = await submit_job(runtime, new_job(), publisher=publisher, now=LATER)

    assert first == Submission(job_id=first.job_id, published=True)
    assert second == Submission(job_id=first.job_id, published=False, duplicate=True)
    assert publisher.sent == [first.job_id]


async def test_a_duplicate_submission_sends_an_open_job_no_message_names(
    runtime: DatabaseRuntime,
) -> None:
    first = await submit_job(
        runtime, new_job(), publisher=RecordingPublisher(refuse=True), now=NOW
    )
    publisher = RecordingPublisher()

    second = await submit_job(runtime, new_job(), publisher=publisher, now=LATER)

    assert not first.published
    assert second == Submission(job_id=first.job_id, published=True, duplicate=True)
    assert publisher.sent == [first.job_id]


async def test_only_a_new_queued_job_can_be_enqueued(runtime: DatabaseRuntime) -> None:
    finished = new_job().start(started_at=NOW).succeed(finished_at=NOW)

    with pytest.raises(InvalidJob, match="only a new queued job"):
        await enqueue(runtime, finished)

    assert await stored_job_ids(runtime) == []


async def test_asking_again_after_a_failed_attempt_queues_the_job_again(
    runtime: DatabaseRuntime,
) -> None:
    job = (await enqueue(runtime, new_job())).job
    async with runtime.transaction() as connection:
        await save_job(
            connection,
            job=job.start(started_at=NOW).fail(error_code="boom", finished_at=NOW),
        )

    again = await enqueue(runtime, new_job())

    assert not again.created
    assert (again.job.job_id, again.job.status, again.job.attempt) == (
        job.job_id,
        JobStatus.QUEUED,
        1,
    )
    assert await stored_job(runtime, job.job_id) == again.job


async def test_a_document_tombstoned_again_after_a_failed_purge_is_purged(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(runtime, job_id=purge_id, now=NOW)
    assert failed.status is JobStatus.FAILED
    await offer_retired(runtime, observed_at=NOW)

    [again] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )
    purged = await purge_document_chunks(runtime, job_id=again.purge_job_id, now=LATER)

    assert again.purge_job_id == purge_id
    assert (purged.status, purged.attempt) == (JobStatus.SUCCEEDED, 1)
    assert await chunk_count(runtime, document.document_id) == 0


async def test_two_messages_at_once_for_a_failing_attempt_run_it_once(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    calls: list[DocumentId] = []

    async def slow_broken_delete(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        calls.append(document_id)
        # Long enough for the other messages to queue up behind the claim.
        await asyncio.sleep(0.2)
        raise ConnectionError("the database went away")

    monkeypatch.setattr(deletions, "delete_chunks", slow_broken_delete)
    results = await asyncio.gather(
        *(run_stored_job(runtime, purge_id, now=NOW) for _ in range(3))
    )

    stored = await stored_job(runtime, purge_id)
    assert len(calls) == 1
    assert sorted(result.duplicate for result in results) == [False, True, True]
    assert [result.job for result in results] == [stored] * 3
    assert (stored.status, stored.attempt, stored.last_error_code) == (
        JobStatus.FAILED,
        1,
        PURGE_FAILED,
    )


def test_the_migration_keeps_one_open_job_per_intent(migrated_database: str) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    with migration_environment(url):
        command.downgrade(config, "c2d3e4f5a6b7")
    insert_job = sa.text(
        "insert into jobs (job_id, kind, target_id, status, attempt, max_attempts,"
        " queued_at, started_at, finished_at, last_error_code) values (:job_id,"
        " 'purge_document_chunks', :target_id, :status, :attempt, 3, :queued_at,"
        " :started_at, :finished_at, :error)"
    )
    other_target = uuid.uuid4()
    failed_target = uuid.uuid4()
    rows = {
        "newer_queued": ("queued", LATER, TARGET),
        "running": ("running", LATER, TARGET),
        "older_queued": ("queued", NOW, TARGET),
        "alone": ("queued", NOW, other_target),
        "older_failed": ("failed", NOW, failed_target),
        "newer_queued_after_failure": ("queued", LATER, failed_target),
    }
    ids = {name: uuid.uuid4() for name in rows}
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            for name, (status, queued_at, target_id) in rows.items():
                failed = status == "failed"
                connection.execute(
                    insert_job,
                    {
                        "job_id": ids[name],
                        "target_id": target_id,
                        "status": status,
                        "attempt": 1 if failed else 0,
                        "queued_at": queued_at,
                        "started_at": None if status == "queued" else queued_at,
                        "finished_at": queued_at if failed else None,
                        "error": "chunk_purge_failed" if failed else None,
                    },
                )

        with migration_environment(url):
            command.upgrade(config, "head")

        with engine.connect() as connection:
            stored = {
                row.job_id: (row.status, row.attempt, row.last_error_code)
                for row in connection.execute(
                    sa.select(
                        jobs.c.job_id,
                        jobs.c.status,
                        jobs.c.attempt,
                        jobs.c.last_error_code,
                    )
                )
            }
    finally:
        engine.dispose()

    # A running Job is kept first; the queued duplicates are closed.
    assert stored[ids["running"]] == ("running", 0, None)
    assert stored[ids["older_queued"]] == ("failed", 3, "superseded_duplicate")
    assert stored[ids["newer_queued"]] == ("failed", 3, "superseded_duplicate")
    assert stored[ids["alone"]] == ("queued", 0, None)
    # A queued Job will run, so it is kept over an older failed one.
    assert stored[ids["newer_queued_after_failure"]] == ("queued", 0, None)
    assert stored[ids["older_failed"]] == ("failed", 3, "superseded_duplicate")
