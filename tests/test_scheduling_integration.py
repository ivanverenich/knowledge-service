"""Scheduling synchronizations against real PostgreSQL, on a clock the tests set.

Every test passes `now` itself, so "due", "not due yet", and "missed for ten
hours" are exact moments, not waits.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.ingestion import scheduling
from knowledge_service.ingestion.scheduling import (
    ScheduleReport,
    SynchronizationSchedule,
    schedule_due_synchronizations,
)
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
from knowledge_service.persistence.schema import jobs
from knowledge_service.settings import Settings
from knowledge_service.worker.relay import relay_once

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
HOURLY = SynchronizationSchedule(interval=timedelta(hours=1))
FIRST = SourceId.parse("00000000-0000-0000-0000-000000000061")
SECOND = SourceId.parse("00000000-0000-0000-0000-000000000062")
THIRD = SourceId.parse("00000000-0000-0000-0000-000000000063")
# The name of a credential, which a Job must not carry either.
CREDENTIALS_NAME = "vault:confluence/handbook"

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, credentials_reference,"
    " display_name, enabled, created_at, updated_at) VALUES (:source_id,"
    " 'confluence', :location, :credentials, 'Handbook', :enabled, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def add_source(
    runtime: DatabaseRuntime, source_id: SourceId, *, enabled: bool = True
) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            INSERT_SOURCE,
            {
                "source_id": source_id.value,
                "location": f"https://wiki.example.com/spaces/{source_id.value}",
                "credentials": CREDENTIALS_NAME,
                "enabled": enabled,
            },
        )


async def past_job(
    runtime: DatabaseRuntime,
    source_id: SourceId,
    *,
    queued_at: datetime,
    finished_at: datetime | None = None,
    status: JobStatus = JobStatus.SUCCEEDED,
) -> Job:
    """A synchronization Job of `source_id` that reached `status`."""
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=source_id.value,
        queued_at=queued_at,
    )
    async with runtime.transaction() as connection:
        await enqueue_job(connection, job)
        if status is JobStatus.QUEUED:
            return job
        job = job.start(started_at=queued_at)
        if status is JobStatus.SUCCEEDED:
            assert finished_at is not None
            job = job.succeed(finished_at=finished_at)
        elif status is JobStatus.FAILED:
            assert finished_at is not None
            job = job.fail(
                error_code="source_unavailable",
                finished_at=finished_at,
                retry_at=finished_at + timedelta(minutes=5),
            )
        return await save_job(connection, job=job)


async def schedule(runtime: DatabaseRuntime, now: datetime) -> ScheduleReport:
    return await schedule_due_synchronizations(runtime, now=now, schedule=HOURLY)


async def sync_jobs(runtime: DatabaseRuntime, source_id: SourceId) -> list[Job]:
    async with runtime.transaction() as connection:
        ids = await connection.scalars(
            sa.select(jobs.c.job_id)
            .where(
                jobs.c.kind == JobKind.SYNCHRONIZE_SOURCE.value,
                jobs.c.target_id == source_id.value,
            )
            .order_by(jobs.c.queued_at)
        )
        found = [await load_job(connection, job_id=JobId(job_id)) for job_id in ids]
    return [job for job in found if job is not None]


async def test_a_source_that_never_synchronized_is_due_at_once(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)

    report = await schedule(runtime, NOW)

    [job] = await sync_jobs(runtime, FIRST)
    assert report == ScheduleReport(queued=(job.job_id,), already_open=())
    assert (job.status, job.queued_at) == (JobStatus.QUEUED, NOW)


async def test_a_source_is_due_one_interval_after_its_last_run_finished(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)
    await past_job(
        runtime, FIRST, queued_at=NOW - timedelta(minutes=10), finished_at=NOW
    )

    early = await schedule(runtime, NOW + timedelta(minutes=59))
    due = await schedule(runtime, NOW + timedelta(hours=1))

    assert early.queued == ()
    assert len(due.queued) == 1


async def test_a_long_run_is_counted_from_when_it_finished(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)
    # Queued three hours ago, it took two hours and a half.
    await past_job(
        runtime,
        FIRST,
        queued_at=NOW - timedelta(hours=3),
        finished_at=NOW - timedelta(minutes=30),
    )

    assert (await schedule(runtime, NOW)).queued == ()
    assert len((await schedule(runtime, NOW + timedelta(minutes=30))).queued) == 1


async def test_a_disabled_source_is_never_scheduled(runtime: DatabaseRuntime) -> None:
    await add_source(runtime, FIRST, enabled=False)

    report = await schedule(runtime, NOW + timedelta(days=30))

    assert report.queued == ()
    assert await sync_jobs(runtime, FIRST) == []


@pytest.mark.parametrize(
    "status", [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED]
)
async def test_a_source_whose_run_is_still_open_is_not_scheduled_again(
    runtime: DatabaseRuntime, status: JobStatus
) -> None:
    await add_source(runtime, FIRST)
    # Queued, running, or failed with a retry planned: still open.
    open_job = await past_job(
        runtime,
        FIRST,
        queued_at=NOW - timedelta(hours=5),
        finished_at=NOW - timedelta(hours=4),
        status=status,
    )

    report = await schedule(runtime, NOW)

    assert report.queued == ()
    assert [job.job_id for job in await sync_jobs(runtime, FIRST)] == [open_job.job_id]


async def test_a_missed_schedule_queues_one_run_not_one_per_interval(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)
    # The scheduler was down for ten hours: ten intervals were missed.
    await past_job(
        runtime,
        FIRST,
        queued_at=NOW - timedelta(hours=10, minutes=5),
        finished_at=NOW - timedelta(hours=10),
    )

    first = await schedule(runtime, NOW)
    second = await schedule(runtime, NOW)

    assert len(first.queued) == 1
    assert second.queued == ()
    assert len(await sync_jobs(runtime, FIRST)) == 2


async def test_two_passes_at_once_store_one_run_per_source(
    runtime: DatabaseRuntime,
) -> None:
    for source_id in (FIRST, SECOND):
        await add_source(runtime, source_id)

    reports = await asyncio.gather(schedule(runtime, NOW), schedule(runtime, NOW))

    stored = [
        job.job_id for s in (FIRST, SECOND) for job in await sync_jobs(runtime, s)
    ]
    assert len(stored) == 2
    # Each Job was stored by exactly one pass; the other found it or skipped it.
    assert sum(len(r.queued) for r in reports) == 2
    reported = {job_id for r in reports for job_id in r.queued + r.already_open}
    assert reported <= set(stored)


async def test_a_run_names_its_source_by_id_and_carries_no_configuration(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)

    [job_id] = (await schedule(runtime, NOW)).queued

    async with runtime.transaction() as connection:
        row = (
            (
                await connection.execute(
                    sa.select(jobs).where(jobs.c.job_id == job_id.value)
                )
            )
            .mappings()
            .one()
        )
    assert row["kind"] == JobKind.SYNCHRONIZE_SOURCE.value
    assert row["target_id"] == FIRST.value
    stored = " ".join(str(value) for value in row.values())
    assert CREDENTIALS_NAME not in stored
    assert "wiki.example.com" not in stored


async def test_a_pass_takes_sources_in_a_stable_order_up_to_its_limit(
    runtime: DatabaseRuntime,
) -> None:
    for source_id in (THIRD, FIRST, SECOND):
        await add_source(runtime, source_id)

    first = await schedule_due_synchronizations(
        runtime, now=NOW, schedule=HOURLY, limit=2
    )
    second = await schedule_due_synchronizations(
        runtime, now=NOW, schedule=HOURLY, limit=2
    )

    assert [len(first.queued), len(second.queued)] == [2, 1]
    assert [job.target_id for job in await sync_jobs(runtime, THIRD)] == [THIRD.value]
    queued_first = {
        job.target_id
        for source_id in (FIRST, SECOND)
        for job in await sync_jobs(runtime, source_id)
        if job.job_id in first.queued
    }
    assert queued_first == {FIRST.value, SECOND.value}


@pytest.mark.parametrize("limit", [0, 1_001, True, 2.0])
async def test_the_pass_limit_is_bounded(
    runtime: DatabaseRuntime, limit: object
) -> None:
    with pytest.raises(ValueError, match="limit"):
        await schedule_due_synchronizations(
            runtime,
            now=NOW,
            schedule=HOURLY,
            limit=limit,  # pyright: ignore[reportArgumentType]
        )


async def test_the_pass_needs_utc(runtime: DatabaseRuntime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        await schedule(runtime, datetime(2026, 11, 20, 9, 0))


async def test_an_unknown_target_id_is_not_a_source(runtime: DatabaseRuntime) -> None:
    # A synchronization Job whose Source was deleted does not make a Source due.
    await past_job(
        runtime,
        SourceId(uuid.uuid4()),
        queued_at=NOW - timedelta(hours=5),
        finished_at=NOW - timedelta(hours=4),
    )

    assert (await schedule(runtime, NOW)).queued == ()


async def test_the_longest_waiting_sources_go_first_so_none_is_starved(
    runtime: DatabaseRuntime,
) -> None:
    sources = [
        SourceId.parse(f"00000000-0000-0000-0000-0000000000{n}") for n in range(70, 75)
    ]
    for source_id in sources:
        await add_source(runtime, source_id)
    ever_queued: set[SourceId] = set()

    for hours in (0, 2, 4):
        report = await schedule_due_synchronizations(
            runtime, now=NOW + timedelta(hours=hours), schedule=HOURLY, limit=2
        )
        # Each run finishes a minute later, so its Source is due again soon.
        async with runtime.transaction() as connection:
            for job_id in report.queued:
                job = await load_job(connection, job_id=job_id)
                assert job is not None
                ended = job.start(started_at=job.queued_at).succeed(
                    finished_at=job.queued_at + timedelta(minutes=1)
                )
                await save_job(connection, job=ended)
                ever_queued.add(SourceId(job.target_id))

    # Two per pass over three passes reach all five, low IDs or not.
    assert ever_queued == set(sources)


async def test_a_time_from_a_fast_clock_delays_a_source_by_one_interval_at_most(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)
    # A worker whose clock ran a year ahead recorded this finish time.
    await past_job(
        runtime,
        FIRST,
        queued_at=NOW - timedelta(minutes=5),
        finished_at=NOW + timedelta(days=365),
    )

    assert (await schedule(runtime, NOW)).queued == ()
    assert len((await schedule(runtime, NOW + timedelta(hours=1))).queued) == 1


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE sources SET enabled = false WHERE source_id = :source_id",
        "DELETE FROM sources WHERE source_id = :source_id",
    ],
    ids=["disabled", "deleted"],
)
async def test_a_source_disabled_or_deleted_during_a_pass_gets_no_job(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    await add_source(runtime, FIRST)
    load_due = scheduling.load_due_source_ids

    async def then_someone_changes_it(*args: object, **kwargs: object) -> object:
        due = await load_due(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        # Another session commits the change right after this pass read it.
        async with runtime.transaction() as other:
            await other.execute(sa.text(change), {"source_id": FIRST.value})
        return due

    monkeypatch.setattr(scheduling, "load_due_source_ids", then_someone_changes_it)
    report = await schedule(runtime, NOW)

    assert report.queued == ()
    assert await sync_jobs(runtime, FIRST) == []


async def test_a_failed_scheduling_step_does_not_stop_publishing(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    purge = Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_DOCUMENT_CHUNKS,
        target_id=uuid.uuid4(),
        queued_at=NOW,
    )
    async with runtime.transaction() as connection:
        await enqueue_job(connection, purge)
    sent: list[JobId] = []

    class Recording:
        kinds = frozenset({JobKind.PURGE_DOCUMENT_CHUNKS})

        def publish(self, job: Job) -> None:
            sent.append(job.job_id)

    async def broken(*args: object, **kwargs: object) -> ScheduleReport:
        raise RuntimeError("the schedule query timed out")

    monkeypatch.setattr(
        "knowledge_service.worker.relay.schedule_due_synchronizations", broken
    )
    done = await relay_once(runtime, publisher=Recording(), now=NOW, schedule=HOURLY)

    assert done.schedule_failed
    assert sent == [purge.job_id]
    assert done.published.published == 1
