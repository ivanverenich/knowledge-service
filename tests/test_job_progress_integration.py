"""Job progress and outcomes stored in PostgreSQL, read through the API.

A running attempt reports its stage, counters, warnings, and heartbeat; each
report commits on its own, so a new worker process, or the API, reads it
after the reporting one is gone. Nothing in a report may go back, and an
attempt the Job is no longer running cannot report. Each attempt keeps its
own row and typed outcome.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from authorization_scenarios import Person
from job_fixtures import (
    OBSERVED_AT,
    SOURCE_ID,
    database_went_away,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service import job_reporting
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import purge_document_chunks
from knowledge_service.job_failures import RetryPolicy
from knowledge_service.job_progress import (
    AttemptOutcome,
    JobCounters,
    JobProgress,
    JobStage,
    JobWarning,
    ProgressWentBackwards,
    ReportFromTheFuture,
)
from knowledge_service.job_reporting import DatabaseJobReports, report_progress
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.persistence.job_attempts import (
    AttemptAlreadyBegun,
    AttemptNotCurrent,
    advance_attempt,
    begin_attempt,
    finish_attempt,
    load_latest_attempt,
)
from knowledge_service.persistence.jobs import (
    enqueue_job,
    load_job,
    requeue_due_jobs,
    save_job,
)
from knowledge_service.persistence.schema import job_attempts, jobs
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings

pytestmark = pytest.mark.integration

STARTED_AT = OBSERVED_AT + timedelta(minutes=1)
LATER = STARTED_AT + timedelta(seconds=30)
POLICY = RetryPolicy(jitter=lambda: 0.5)

CALLERS = {
    "Bearer morgan": Caller(
        reader=Person("morgan", ("/engineering",)).context(),
        roles=frozenset({ApplicationRole.CONTENT_MANAGER}),
    ),
}


class Tokens:
    """Gives each test token the caller its claims would give."""

    async def authenticate(self, headers: list[str]) -> Caller:
        if len(headers) == 1 and headers[0] in CALLERS:
            return CALLERS[headers[0]]
        raise Unauthenticated("test", error=AuthenticationError.INVALID_TOKEN)


def new_runtime(url: str) -> DatabaseRuntime:
    return create_database_runtime(Settings(database_url=SecretStr(url)))


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = new_runtime(migrated_database)
    await insert_source(runtime)
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def running_synchronization(runtime: DatabaseRuntime) -> Job:
    """A synchronization Job whose claim committed, as its handler will do."""
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


async def latest(runtime: DatabaseRuntime, job_id: JobId) -> JobProgress:
    async with runtime.transaction() as connection:
        progress = await load_latest_attempt(connection, job_id=job_id)
    assert progress is not None
    return progress


async def report(
    runtime: DatabaseRuntime,
    job: Job,
    *,
    processed: int,
    at: timedelta = timedelta(seconds=30),
    attempt: int = 1,
    warnings: tuple[JobWarning, ...] = (),
) -> JobProgress:
    return await report_progress(
        runtime,
        job_id=job.job_id,
        attempt=attempt,
        stage=JobStage.READING,
        counters=JobCounters(processed=processed),
        warnings=warnings,
        now=STARTED_AT + at,
    )


async def read_over_http(runtime: DatabaseRuntime, job_id: JobId) -> httpx.Response:
    app = create_app(authenticator=Tokens(), job_reports=DatabaseJobReports(runtime))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.get(
            f"/v1/jobs/{job_id.value}", headers={"Authorization": "Bearer morgan"}
        )


async def test_progress_survives_the_worker_that_reported_it(
    migrated_database: str, runtime: DatabaseRuntime
) -> None:
    job = await running_synchronization(runtime)
    worker = new_runtime(migrated_database)
    await report_progress(
        worker,
        job_id=job.job_id,
        attempt=1,
        stage=JobStage.WRITING,
        counters=JobCounters(processed=120, changed=7, skipped=2),
        warnings=[JobWarning.QUARANTINED_ITEMS],
        now=LATER,
    )
    # The worker process ends; its connections go with it.
    await worker.dispose()

    response = await read_over_http(runtime, job.job_id)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["source_id"] == str(SOURCE_ID.value)
    assert body["latest_attempt"] == {
        "attempt": 1,
        "stage": "writing",
        "processed": 120,
        "changed": 7,
        "skipped": 2,
        "warnings": ["quarantined_items"],
        "started_at": "2026-11-21T09:01:00Z",
        "heartbeat_at": "2026-11-21T09:01:30Z",
        "outcome": None,
        "finished_at": None,
    }


async def test_a_lower_counter_is_refused_and_the_stored_one_stays(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await report(runtime, job, processed=50)

    with pytest.raises(ProgressWentBackwards):
        await report(runtime, job, processed=49, at=timedelta(minutes=1))

    stored = await latest(runtime, job.job_id)
    assert stored.counters.processed == 50
    assert stored.heartbeat_at == STARTED_AT + timedelta(seconds=30)


async def test_an_older_heartbeat_or_a_lost_warning_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await report(runtime, job, processed=1, warnings=(JobWarning.UNRESOLVED_READERS,))

    with pytest.raises(ProgressWentBackwards):
        await report(
            runtime,
            job,
            processed=2,
            at=timedelta(seconds=29),
            warnings=(JobWarning.UNRESOLVED_READERS,),
        )
    with pytest.raises(ProgressWentBackwards):
        await report(runtime, job, processed=2, at=timedelta(minutes=1))

    stored = await latest(runtime, job.job_id)
    assert stored.counters.processed == 1
    assert stored.warnings == {JobWarning.UNRESOLVED_READERS}


async def test_a_replayed_report_changes_nothing(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)
    first = await report(runtime, job, processed=10)

    again = await report(runtime, job, processed=10)

    assert again == first == await latest(runtime, job.job_id)


async def test_reports_racing_each_other_never_lower_what_is_stored(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    # Even totals high to low, then odd ones low to high: out of order.
    totals = [*range(30, 0, -2), *range(1, 31, 2)]

    async def send(total: int) -> int | None:
        try:
            await report(runtime, job, processed=total, at=timedelta(seconds=total))
        except ProgressWentBackwards:
            return None
        return total

    accepted = [t for t in await asyncio.gather(*map(send, totals)) if t]

    # Every report either landed or was refused as going back; the highest won.
    assert 30 in accepted
    assert (await latest(runtime, job.job_id)).counters.processed == 30


async def test_a_report_from_an_attempt_the_job_left_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await report(runtime, job, processed=5)
    retry_at = LATER + timedelta(minutes=1)
    async with runtime.transaction() as connection:
        failed = job.fail(
            error_code="source_unavailable", finished_at=LATER, retry_at=retry_at
        )
        await save_job(connection, job=failed)
        await finish_attempt(connection, job=failed)
        [requeued] = await requeue_due_jobs(connection, now=retry_at, limit=10)
        second = requeued.start(started_at=retry_at)
        await save_job(connection, job=second)
        await begin_attempt(connection, job=second)

    # The first attempt's worker wakes up and reports.
    with pytest.raises(AttemptNotCurrent):
        await report(runtime, job, processed=99, at=timedelta(minutes=5))

    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    job_attempts.c.attempt,
                    job_attempts.c.processed,
                    job_attempts.c.outcome,
                ).order_by(job_attempts.c.attempt)
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        (1, 5, "retry_scheduled"),
        (2, 0, None),
    ]


async def test_a_finished_job_takes_no_more_reports(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)
    async with runtime.transaction() as connection:
        succeeded = job.succeed(finished_at=LATER)
        await save_job(connection, job=succeeded)
        await finish_attempt(connection, job=succeeded)

    with pytest.raises(AttemptNotCurrent):
        await report(runtime, job, processed=1, at=timedelta(minutes=1))

    assert (await latest(runtime, job.job_id)).outcome is AttemptOutcome.SUCCEEDED


async def test_a_report_for_an_unknown_job_is_refused(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)

    with pytest.raises(AttemptNotCurrent):
        await report_progress(
            runtime,
            job_id=JobId.new(),
            attempt=1,
            stage=JobStage.READING,
            counters=JobCounters(),
            now=LATER,
        )
    with pytest.raises(AttemptNotCurrent):
        await report(runtime, job, processed=1, attempt=2)


async def test_the_database_refuses_a_counter_below_zero(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    with pytest.raises(IntegrityError):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.update(job_attempts)
                .where(job_attempts.c.job_id == job.job_id.value)
                .values(processed=-1)
            )


async def test_a_purge_records_its_attempt_and_outcome(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)

    await purge_document_chunks(runtime, job_id=purge_id, now=LATER)

    progress = await latest(runtime, purge_id)
    assert progress.attempt == 1
    assert progress.counters == JobCounters(processed=1, changed=1, skipped=0)
    assert progress.outcome is AttemptOutcome.SUCCEEDED
    assert progress.finished_at == LATER


async def test_each_purge_attempt_keeps_its_own_outcome(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    real_delete = deletions.delete_chunks

    async def lost_connection(*_args: object, **_kwargs: object) -> int:
        raise database_went_away()

    monkeypatch.setattr(deletions, "delete_chunks", lost_connection)
    failed = await purge_document_chunks(
        runtime, job_id=purge_id, now=LATER, retry_policy=POLICY
    )
    assert failed.retry_at is not None
    monkeypatch.setattr(deletions, "delete_chunks", real_delete)
    async with runtime.transaction() as connection:
        await requeue_due_jobs(connection, now=failed.retry_at, limit=10)
    await purge_document_chunks(runtime, job_id=purge_id, now=failed.retry_at)

    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(job_attempts.c.attempt, job_attempts.c.outcome)
                .where(job_attempts.c.job_id == purge_id.value)
                .order_by(job_attempts.c.attempt)
            )
        ).all()
    assert [tuple(row) for row in rows] == [(1, "retry_scheduled"), (2, "succeeded")]
    assert (await stored_job(runtime, purge_id)).status is JobStatus.SUCCEEDED


async def test_the_api_shows_a_queued_job_with_no_attempt_yet(
    runtime: DatabaseRuntime,
) -> None:
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

    response = await read_over_http(runtime, queued.job.job_id)

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["latest_attempt"] is None


async def test_the_api_hides_jobs_that_are_not_synchronizations(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)

    hidden = await read_over_http(runtime, purge_id)
    missing = await read_over_http(runtime, JobId.new())

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["code"] == missing.json()["code"] == "job_not_found"


async def test_deleting_a_job_deletes_its_attempts(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)

    async with runtime.transaction() as connection:
        await connection.execute(
            sa.delete(jobs).where(jobs.c.job_id == job.job_id.value)
        )
        left = await connection.scalar(
            sa.select(sa.func.count()).select_from(job_attempts)
        )
        stored = await load_job(connection, job_id=job.job_id)

    assert left == 0
    assert stored is None


# --- Found by the adversarial review ---------------------------------------


async def test_a_heartbeat_later_than_the_finish_does_not_stop_the_finish(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    # The reporting worker's clock runs ten seconds ahead of the finisher's.
    await report(runtime, job, processed=3, at=timedelta(seconds=40))

    async with runtime.transaction() as connection:
        succeeded = job.succeed(finished_at=STARTED_AT + timedelta(seconds=30))
        await save_job(connection, job=succeeded)
        ended = await finish_attempt(connection, job=succeeded)

    assert ended.outcome is AttemptOutcome.SUCCEEDED
    # The attempt ends when its Job did; the skewed heartbeat is pulled back.
    assert ended.finished_at == ended.heartbeat_at == STARTED_AT + timedelta(seconds=30)
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.SUCCEEDED


async def test_final_counters_below_a_late_report_do_not_stop_the_finish(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await report(runtime, job, processed=10)

    async with runtime.transaction() as connection:
        succeeded = job.succeed(finished_at=LATER + timedelta(seconds=5))
        await save_job(connection, job=succeeded)
        ended = await finish_attempt(
            connection, job=succeeded, counters=JobCounters(processed=9, changed=2)
        )

    assert ended.counters == JobCounters(processed=10, changed=2)
    assert ended.outcome is AttemptOutcome.SUCCEEDED


async def test_a_report_from_a_clock_far_ahead_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    with pytest.raises(ReportFromTheFuture):
        await report(runtime, job, processed=1, at=timedelta(days=3650))

    # The attempt is not poisoned: an honest report still lands.
    assert (await report(runtime, job, processed=1)).counters.processed == 1


async def test_a_refused_report_does_not_hold_back_a_due_retry(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    retry_at = LATER + timedelta(minutes=1)
    async with runtime.transaction() as connection:
        failed = job.fail(
            error_code="source_unavailable", finished_at=LATER, retry_at=retry_at
        )
        await save_job(connection, job=failed)
        await finish_attempt(connection, job=failed)

    async with runtime.transaction() as stale:
        with pytest.raises(AttemptNotCurrent):
            await advance_attempt(
                stale,
                job_id=job.job_id,
                attempt=1,
                stage=JobStage.READING,
                counters=JobCounters(processed=1),
                now=retry_at,
            )
        # The stale report's transaction is still open.
        async with runtime.transaction() as relay:
            requeued = await requeue_due_jobs(relay, now=retry_at, limit=10)

    assert [j.job_id for j in requeued] == [job.job_id]


async def test_the_api_shows_the_job_and_its_attempt_from_one_moment(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await running_synchronization(runtime)
    real_load = job_reporting.load_latest_attempt

    async def finish_meanwhile(connection: object, *, job_id: JobId) -> object:
        # Another session finishes the Job between the API's two reads.
        async with runtime.transaction() as other:
            succeeded = job.succeed(finished_at=LATER)
            await save_job(other, job=succeeded)
            await finish_attempt(other, job=succeeded)
        return await real_load(connection, job_id=job_id)  # type: ignore[arg-type]

    monkeypatch.setattr(job_reporting, "load_latest_attempt", finish_meanwhile)

    body = (await read_over_http(runtime, job.job_id)).json()

    assert body["status"] == "running"
    assert body["latest_attempt"]["outcome"] is None


async def test_beginning_an_attempt_twice_is_a_typed_error(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    with pytest.raises(AttemptAlreadyBegun):
        async with runtime.transaction() as connection:
            await begin_attempt(connection, job=job)


async def test_a_stale_worker_cannot_end_an_attempt_the_job_left(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    retry_at = LATER + timedelta(minutes=1)
    stale = job.fail(
        error_code="source_unavailable", finished_at=LATER, retry_at=retry_at
    )
    async with runtime.transaction() as connection:
        await save_job(connection, job=stale)
        await finish_attempt(connection, job=stale)
        [requeued] = await requeue_due_jobs(connection, now=retry_at, limit=10)
        second = requeued.start(started_at=retry_at)
        await save_job(connection, job=second)
        await begin_attempt(connection, job=second)

    async def store_stale_outcome() -> None:
        # The first attempt's worker, presumed dead, stores its outcome again.
        async with runtime.transaction() as connection:
            await save_job(connection, job=stale)
            await finish_attempt(connection, job=stale)

    with pytest.raises(AttemptNotCurrent):
        await store_stale_outcome()

    stored = await stored_job(runtime, job.job_id)
    assert stored.status is JobStatus.RUNNING
    assert (await latest(runtime, job.job_id)).attempt == 2


async def test_an_attempt_reads_back_in_utc_whatever_the_session_time_zone(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    async with runtime.transaction() as connection:
        await connection.execute(sa.text("SET LOCAL TIME ZONE 'Asia/Tokyo'"))
        progress = await load_latest_attempt(connection, job_id=job.job_id)

    assert progress is not None
    assert progress.started_at == STARTED_AT
