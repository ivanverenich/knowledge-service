"""Real PostgreSQL tests for running `synchronize_source` Jobs end to end.

Each test stores a Job, delivers it the way a worker does, through
`run_stored_job`, and checks the Job, its attempt, the Source's progress,
and the Documents. The Source is a fake, so every run is exact: empty,
multi-page, update, delete, retry, cancellation, and incremental.
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from database_fixtures import migration_config, migration_environment
from fakes import FakeSource
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceConfiguration,
    SourceKind,
    SourcePage,
    SourcePort,
    SourceRecord,
    SourceUnavailable,
)
from knowledge_service.ingestion.synchronization_jobs import (
    SourceSettings,
    SynchronizationJobs,
    open_source,
)
from knowledge_service.job_dispatch import requeue_due_retries
from knowledge_service.job_failures import JobFailure, RetryPolicy
from knowledge_service.job_progress import AttemptOutcome, JobProgress, JobWarning
from knowledge_service.job_recovery import recover_stuck_jobs
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.leases import Lease, LeaseStore
from knowledge_service.persistence.job_attempts import load_latest_attempt
from knowledge_service.persistence.jobs import enqueue_job, load_job
from knowledge_service.persistence.schema import (
    documents,
    job_attempts,
    jobs,
    sources,
    synchronization_seen,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.jobs import JobOutcome, run_stored_job

pytestmark = pytest.mark.integration

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-0000000000a0")
# Fast, fixed retries: the wait before attempt 2 is half a second.
RETRIES = RetryPolicy(
    base_delay=timedelta(seconds=1),
    max_delay=timedelta(seconds=1),
    jitter=lambda: 0.0,
)


class Crash(Exception):
    """Stands in for a bug that stops a pass at one exact point."""


@dataclass
class MemoryLeases:
    """A lease store that always grants and renews; it holds no state."""

    acquired: list[str] = field(default_factory=list[str])

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
        self.acquired.append(name)
        return Lease(name=name, token=name, ttl=ttl)

    async def renew(self, lease: Lease) -> bool:
        return True

    async def release(self, lease: Lease) -> bool:
        return True


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


@pytest.fixture
def start() -> datetime:
    """A moment in the recent past, so no time a test uses is in the future."""
    return datetime.now(UTC) - timedelta(hours=2)


async def register(
    runtime: DatabaseRuntime,
    *,
    kind: str = "local_directory",
    location: str = "/srv/handbook",
    enabled: bool = True,
) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.insert(sources).values(
                source_id=SOURCE_ID.value,
                kind=kind,
                location=location,
                display_name="Handbook",
                enabled=enabled,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                default_public=True,
            )
        )


async def queue(runtime: DatabaseRuntime, at: datetime) -> JobId:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=SOURCE_ID.value,
        queued_at=at,
    )
    async with runtime.transaction() as connection:
        return (await enqueue_job(connection, job)).job.job_id


def handler(
    source: SourcePort,
    at: datetime,
    *,
    full_pass_interval: timedelta = timedelta(hours=24),
) -> SynchronizationJobs:
    """The real handler, opening `source` for every stored Source."""
    leases = MemoryLeases()

    @asynccontextmanager
    async def memory() -> AsyncGenerator[LeaseStore]:
        yield leases

    return SynchronizationJobs(
        open_source=lambda _settings: source,
        leases=memory,
        principal_directory=None,
        full_pass_interval=full_pass_interval,
        retry_policy=RETRIES,
        page_size=1,
        clock=lambda: at,
    )


async def deliver(
    runtime: DatabaseRuntime, job_id: JobId, run: SynchronizationJobs, at: datetime
) -> JobOutcome:
    return await run_stored_job(
        runtime, job_id, now=at, handlers={JobKind.SYNCHRONIZE_SOURCE: run}
    )


async def sync(
    runtime: DatabaseRuntime,
    source: SourcePort,
    at: datetime,
    *,
    full_pass_interval: timedelta = timedelta(hours=24),
) -> Job:
    """Queue one Job at `at` and run it to its end."""
    job_id = await queue(runtime, at)
    outcome = await deliver(
        runtime,
        job_id,
        handler(source, at, full_pass_interval=full_pass_interval),
        at,
    )
    return outcome.job


def record(name: str, text: str = "") -> SourceRecord:
    return SourceRecord.present(
        external_id=name,
        version="v1",
        media_type="text/plain",
        content=(text or f"The {name} policy.").encode(),
        access=SourceAccess(public=True),
    )


def gone(name: str) -> SourceRecord:
    return SourceRecord.deleted(external_id=name, version="gone")


async def availability(runtime: DatabaseRuntime) -> dict[str, str]:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(documents.c.external_id, documents.c.availability)
        )
        return {row.external_id: row.availability for row in rows}


async def attempt(runtime: DatabaseRuntime, job_id: JobId) -> JobProgress:
    async with runtime.transaction() as connection:
        progress = await load_latest_attempt(connection, job_id=job_id)
    assert progress is not None
    return progress


type Progress = tuple[str | None, str | None, str | None, datetime | None]


async def source_row(runtime: DatabaseRuntime) -> Progress:
    """The Source's checkpoint, pass mode, change cursor, and last full pass."""
    async with runtime.transaction() as connection:
        row = (
            await connection.execute(
                sa.select(
                    sources.c.checkpoint,
                    sources.c.pass_mode,
                    sources.c.sync_cursor,
                    sources.c.last_full_pass_at,
                ).where(sources.c.source_id == SOURCE_ID.value)
            )
        ).one()
    return (row.checkpoint, row.pass_mode, row.sync_cursor, row.last_full_pass_at)


async def purge_jobs(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        return (
            await connection.scalar(
                sa.select(sa.func.count())
                .select_from(jobs)
                .where(jobs.c.kind == JobKind.PURGE_DOCUMENT_CHUNKS.value)
            )
        ) or 0


async def fall_silent(runtime: DatabaseRuntime, job_id: JobId) -> None:
    """Make the database's last word from the Job's worker six minutes old."""
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(job_attempts)
            .where(job_attempts.c.job_id == job_id.value)
            .values(heard_at=job_attempts.c.heard_at - timedelta(minutes=6))
        )


THREE_PAGES = [[record("a.txt")], [record("b.txt")], [record("c.txt")]]


# --- Full passes: empty, multi-page, update, delete


async def test_an_empty_source_succeeds_with_nothing_stored(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)

    job = await sync(runtime, FakeSource([]), start)

    assert job.status is JobStatus.SUCCEEDED
    progress = await attempt(runtime, job.job_id)
    assert progress.outcome is AttemptOutcome.SUCCEEDED
    assert (progress.counters.processed, progress.counters.changed) == (0, 0)
    assert await availability(runtime) == {}


async def test_a_multi_page_source_is_stored_and_its_progress_reported(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    seen_mid_run: list[int] = []

    class Watched(FakeSource):
        """Reads the stored progress before it hands out page three."""

        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            if checkpoint == "2":
                seen_mid_run.append((await attempt(runtime, job_id)).counters.processed)
            return await super().fetch_page(checkpoint=checkpoint, page_size=page_size)

    job_id = await queue(runtime, start)
    outcome = await deliver(
        runtime, job_id, handler(Watched(THREE_PAGES), start), start
    )

    assert outcome.job.status is JobStatus.SUCCEEDED
    # Two pages were committed, and reported, before the third was read.
    assert seen_mid_run == [2]
    counters = (await attempt(runtime, job_id)).counters
    assert (counters.processed, counters.changed, counters.skipped) == (3, 3, 0)
    assert await availability(runtime) == dict.fromkeys(
        ("a.txt", "b.txt", "c.txt"), "available"
    )
    assert (await source_row(runtime)) == (None, None, None, start)


async def test_an_update_changes_only_the_record_that_changed(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    await sync(runtime, FakeSource(THREE_PAGES), start)
    edited = [[record("a.txt")], [record("b.txt", "Rewritten.")], [record("c.txt")]]

    job = await sync(runtime, FakeSource(edited), start + timedelta(minutes=5))

    counters = (await attempt(runtime, job.job_id)).counters
    assert (counters.processed, counters.changed) == (3, 1)


async def test_a_file_the_source_no_longer_lists_is_tombstoned(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    await sync(runtime, FakeSource(THREE_PAGES), start)

    job = await sync(
        runtime,
        FakeSource([[record("a.txt")], [record("c.txt")]]),
        start + timedelta(minutes=5),
    )

    assert job.status is JobStatus.SUCCEEDED
    assert (await availability(runtime))["b.txt"] == "tombstoned"
    assert await purge_jobs(runtime) == 1


async def test_a_quarantined_file_finishes_with_a_warning(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    broken = SourceRecord.present(
        external_id="broken.txt",
        version="v1",
        media_type="application/x-unknown",
        content=b"???",
        access=SourceAccess(public=True),
    )

    job = await sync(runtime, FakeSource([[record("a.txt")], [broken]]), start)

    progress = await attempt(runtime, job.job_id)
    assert progress.outcome is AttemptOutcome.SUCCEEDED_WITH_WARNINGS
    assert progress.warnings == frozenset({JobWarning.QUARANTINED_ITEMS})
    assert progress.counters.skipped == 1


# --- Retry and cancellation


async def test_a_failed_attempt_is_retried_and_resumes_from_its_checkpoint(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    job_id = await queue(runtime, start)
    # The Source goes away while page three is fetched.
    first = await deliver(
        runtime, job_id, handler(FakeSource(THREE_PAGES, fail_on_call=3), start), start
    )
    assert first.job.status is JobStatus.FAILED
    assert first.job.last_error_code == JobFailure.SOURCE_UNAVAILABLE.value
    assert first.job.retry_at is not None
    assert (await attempt(runtime, job_id)).outcome is AttemptOutcome.RETRY_SCHEDULED

    later = start + timedelta(seconds=1)
    assert await requeue_due_retries(runtime, now=later) == (job_id,)
    retried = FakeSource(THREE_PAGES)
    second = await deliver(runtime, job_id, handler(retried, later), later)

    assert second.job.status is JobStatus.SUCCEEDED
    assert second.job.attempt == 1
    assert retried.checkpoints == ["2"]
    assert (await attempt(runtime, job_id)).attempt == 2
    assert len(await availability(runtime)) == 3


async def test_a_cancelled_attempt_is_recovered_and_its_retry_resumes(
    runtime: DatabaseRuntime,
) -> None:
    # Real time: recovery compares the silence with the database's clock.
    now = datetime.now(UTC)
    await register(runtime)
    waiting = asyncio.Event()

    class HangsOnPageThree(FakeSource):
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            if checkpoint == "2":
                waiting.set()
                await asyncio.Event().wait()
            return await super().fetch_page(checkpoint=checkpoint, page_size=page_size)

    job_id = await queue(runtime, now)
    task = asyncio.create_task(
        deliver(runtime, job_id, handler(HangsOnPageThree(THREE_PAGES), now), now)
    )
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Nothing was recorded: the Job still runs, as far as anyone can tell.
    async with runtime.transaction() as connection:
        stuck = await load_job(connection, job_id=job_id)
    assert stuck is not None
    assert stuck.status is JobStatus.RUNNING

    # Five silent minutes later, recovery takes the worker for dead.
    await fall_silent(runtime, job_id)
    recovered = await recover_stuck_jobs(
        runtime, now=datetime.now(UTC), retry_policy=RETRIES
    )
    assert recovered.stale == (job_id,)
    later = datetime.now(UTC) + timedelta(seconds=1)
    assert await requeue_due_retries(runtime, now=later) == (job_id,)
    retried = FakeSource(THREE_PAGES)
    outcome = await deliver(runtime, job_id, handler(retried, later), later)

    assert outcome.job.status is JobStatus.SUCCEEDED
    assert retried.checkpoints == ["2"]
    assert len(await availability(runtime)) == 3


async def test_an_attempt_ended_by_recovery_keeps_recoverys_outcome(
    runtime: DatabaseRuntime,
) -> None:
    now = datetime.now(UTC)
    await register(runtime)
    job_id = await queue(runtime, now)

    class RecoveredMidRun(FakeSource):
        """Recovery takes the Job for dead while page two is read."""

        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            if checkpoint == "1":
                await fall_silent(runtime, job_id)
                await recover_stuck_jobs(
                    runtime, now=datetime.now(UTC), retry_policy=RETRIES
                )
            return await super().fetch_page(checkpoint=checkpoint, page_size=page_size)

    outcome = await deliver(
        runtime, job_id, handler(RecoveredMidRun(THREE_PAGES), now), now
    )

    # The late worker's report and finish were refused: recovery's stands.
    assert outcome.duplicate is True
    assert outcome.job.status is JobStatus.FAILED
    assert outcome.job.last_error_code == JobFailure.WORKER_LOST.value


async def test_a_second_message_for_a_finished_job_runs_nothing(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    job_id = await queue(runtime, start)
    await deliver(runtime, job_id, handler(FakeSource(THREE_PAGES), start), start)
    again = FakeSource(THREE_PAGES)

    outcome = await deliver(runtime, job_id, handler(again, start), start)

    assert outcome.duplicate is True
    assert again.checkpoints == []


# --- Sources that cannot be synchronized


@pytest.mark.parametrize("enabled", [False, None], ids=["disabled", "missing"])
async def test_a_disabled_or_missing_source_fails_the_job_for_good(
    runtime: DatabaseRuntime, start: datetime, enabled: bool | None
) -> None:
    if enabled is not None:
        await register(runtime, enabled=enabled)
    untouched = FakeSource(THREE_PAGES)

    job = await sync(runtime, untouched, start)

    assert job.status is JobStatus.FAILED
    assert job.last_error_code == JobFailure.SOURCE_GONE.value
    assert job.retry_at is None
    assert untouched.checkpoints == []


@pytest.mark.parametrize(
    ("kind", "location"),
    [("confluence", "https://wiki.example.test"), ("local_directory", "relative")],
    ids=["no-adapter-yet", "relative-folder"],
)
async def test_a_source_no_adapter_can_open_fails_the_job_for_good(
    runtime: DatabaseRuntime, start: datetime, kind: str, location: str
) -> None:
    await register(runtime, kind=kind, location=location)
    job_id = await queue(runtime, start)
    run = handler(FakeSource([]), start)
    real = SynchronizationJobs(
        open_source=open_source,
        leases=run.leases,
        principal_directory=None,
        clock=run.clock,
    )

    outcome = await deliver(runtime, job_id, real, start)

    assert outcome.job.status is JobStatus.FAILED
    assert outcome.job.last_error_code == JobFailure.SOURCE_MISCONFIGURED.value
    assert outcome.job.retry_at is None


# --- Incremental passes


type Changes = Mapping[str, tuple[Sequence[Sequence[SourceRecord]], str]]


class FeedSource(FakeSource):
    """A fake Source with a change feed.

    A full listing ends with cursor "c1". The changes after a cursor are
    `changes[since]`: pages of changed records, and the cursor that follows.
    `fail_on_change_call` makes that call to `fetch_changes` fail as if the
    Source were unreachable.
    """

    def __init__(
        self,
        pages: Sequence[Sequence[SourceRecord]],
        changes: Changes,
        *,
        fail_on_change_call: int | None = None,
    ) -> None:
        super().__init__(pages)
        self.changes = changes
        self.since: list[str] = []
        self._fail_on = fail_on_change_call

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        page = await super().fetch_page(checkpoint=checkpoint, page_size=page_size)
        if page.next_checkpoint is not None:
            return page
        return SourcePage(records=page.records, next_checkpoint=None, cursor="c1")

    async def fetch_changes(
        self, *, since: str, checkpoint: str | None, page_size: int
    ) -> SourcePage:
        self.since.append(since)
        if self._fail_on is not None and len(self.since) == self._fail_on:
            raise SourceUnavailable("the change feed went away")
        pages, following = self.changes[since]
        index = 0 if checkpoint is None else int(checkpoint)
        last = index + 1 >= len(pages)
        return SourcePage(
            records=tuple(pages[index]) if pages else (),
            next_checkpoint=None if last else str(index + 1),
            cursor=following if last else None,
        )


async def test_an_incremental_pass_reads_only_changes_after_the_cursor(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    changes = {"c1": ([[record("a.txt", "Edited.")], [gone("b.txt")]], "c2")}
    full = await sync(runtime, FeedSource(THREE_PAGES, changes), start)
    assert (await source_row(runtime)) == (None, None, "c1", start)

    feed = FeedSource(THREE_PAGES, changes)
    job = await sync(runtime, feed, start + timedelta(minutes=5))

    assert (full.status, job.status) == (JobStatus.SUCCEEDED, JobStatus.SUCCEEDED)
    # No full listing was read: c.txt was never offered, and stays.
    assert feed.checkpoints == []
    assert feed.since == ["c1", "c1"]
    assert await availability(runtime) == {
        "a.txt": "available",
        "b.txt": "tombstoned",
        "c.txt": "available",
    }
    counters = (await attempt(runtime, job.job_id)).counters
    assert (counters.processed, counters.changed) == (1, 1)
    # The cursor moved on; the last full pass is still the first one.
    assert (await source_row(runtime)) == (None, None, "c2", start)


async def test_a_full_pass_comes_back_after_the_full_pass_interval(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    changes: Changes = {"c1": ([[]], "c2")}
    await sync(runtime, FeedSource(THREE_PAGES, changes), start)
    # The Source dropped c.txt without reporting it; only a full pass notices.
    feed = FeedSource([[record("a.txt")], [record("b.txt")]], changes)

    await sync(runtime, feed, start + timedelta(minutes=5))
    assert (await availability(runtime))["c.txt"] == "available"
    await sync(
        runtime,
        feed,
        start + timedelta(minutes=40),
        full_pass_interval=timedelta(minutes=30),
    )

    assert feed.since == ["c1"]
    assert feed.checkpoints == [None, "1"]
    assert (await availability(runtime))["c.txt"] == "tombstoned"
    assert (await source_row(runtime))[2:] == (
        "c1",
        start + timedelta(minutes=40),
    )


async def test_a_failed_incremental_pass_resumes_as_incremental(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    changes = {"c1": ([[record("a.txt", "Edited.")], [gone("b.txt")]], "c2")}
    await sync(runtime, FeedSource(THREE_PAGES, changes), start)
    job_id = await queue(runtime, start + timedelta(minutes=5))
    first = FeedSource(THREE_PAGES, changes, fail_on_change_call=2)

    failed = await deliver(
        runtime,
        job_id,
        handler(first, start + timedelta(minutes=5)),
        start + timedelta(minutes=5),
    )
    assert failed.job.last_error_code == JobFailure.SOURCE_UNAVAILABLE.value
    assert (await source_row(runtime))[:2] == ("1", "incremental")

    # The retry resumes from its checkpoint, still reading changes after "c1".
    later = start + timedelta(minutes=6)
    assert await requeue_due_retries(runtime, now=later) == (job_id,)
    retried = FeedSource(THREE_PAGES, changes)
    done = await deliver(runtime, job_id, handler(retried, later), later)

    assert done.job.status is JobStatus.SUCCEEDED
    assert retried.since == ["c1"]
    assert (await availability(runtime))["b.txt"] == "tombstoned"


async def test_an_incremental_pass_without_a_new_cursor_fails_and_keeps_its_cursor(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)

    class LosesItsCursor(FeedSource):
        async def fetch_changes(
            self, *, since: str, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            page = await super().fetch_changes(
                since=since, checkpoint=checkpoint, page_size=page_size
            )
            return SourcePage(records=page.records, next_checkpoint=None)

    changes = {"c1": ([[gone("a.txt")]], "c2")}
    await sync(runtime, FeedSource(THREE_PAGES, changes), start)

    job = await sync(
        runtime, LosesItsCursor(THREE_PAGES, changes), start + timedelta(minutes=5)
    )

    assert job.status is JobStatus.FAILED
    assert (await availability(runtime))["a.txt"] == "available"
    assert (await source_row(runtime))[2] == "c1"


async def test_a_source_without_a_change_feed_is_always_read_in_full(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    await sync(runtime, FakeSource(THREE_PAGES), start)
    again = FakeSource(THREE_PAGES)

    await sync(runtime, again, start + timedelta(minutes=5))

    assert again.checkpoints == [None, "1", "2"]
    assert (await source_row(runtime))[2] is None


# --- Review findings


async def test_a_warning_a_later_report_resolves_does_not_fail_the_job(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    unknown_reader = SourceRecord.present(
        external_id="a.txt",
        version="v1",
        media_type="text/plain",
        content=b"The a.txt policy.",
        access=SourceAccess(public=False, principals=("user:bob",)),
    )

    job = await sync(runtime, FakeSource([[unknown_reader], [record("a.txt")]]), start)

    assert job.status is JobStatus.SUCCEEDED
    progress = await attempt(runtime, job.job_id)
    assert progress.warnings == frozenset({JobWarning.UNRESOLVED_READERS})


async def test_a_clock_stepping_back_between_pages_does_not_fail_the_job(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    readings = iter([start + timedelta(seconds=10), start + timedelta(seconds=8)])
    run = handler(FakeSource(THREE_PAGES), start)
    stepping = SynchronizationJobs(
        open_source=run.open_source,
        leases=run.leases,
        principal_directory=None,
        page_size=1,
        clock=lambda: next(readings, start + timedelta(seconds=8)),
    )
    job_id = await queue(runtime, start)

    outcome = await deliver(runtime, job_id, stepping, start)

    assert outcome.job.status is JobStatus.SUCCEEDED


async def test_a_job_queued_by_a_clock_far_ahead_is_retried_not_failed_for_good(
    runtime: DatabaseRuntime,
) -> None:
    now = datetime.now(UTC)
    await register(runtime)
    job_id = await queue(runtime, now + timedelta(minutes=6))

    outcome = await deliver(runtime, job_id, handler(FakeSource([]), now), now)

    assert outcome.job.last_error_code == JobFailure.CLOCK_AHEAD.value
    assert outcome.job.retry_at is not None


async def test_an_incremental_pass_whose_source_lost_its_feed_starts_over_in_full(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    changes: Changes = {"c1": ([[record("a.txt", "Edited.")]], "c2")}
    await sync(runtime, FeedSource(THREE_PAGES, changes), start)
    job_id = await queue(runtime, start + timedelta(minutes=5))
    failing = FeedSource(THREE_PAGES, changes, fail_on_change_call=1)
    at = start + timedelta(minutes=5)
    await deliver(runtime, job_id, handler(failing, at), at)
    assert (await source_row(runtime))[1] == "incremental"

    # The adapter is redeployed without its change feed.
    later = start + timedelta(minutes=6)
    assert await requeue_due_retries(runtime, now=later) == (job_id,)
    plain = FakeSource(THREE_PAGES)
    done = await deliver(runtime, job_id, handler(plain, later), later)

    assert done.job.status is JobStatus.SUCCEEDED
    assert plain.checkpoints == [None, "1", "2"]
    assert (await source_row(runtime))[2] is None


async def test_a_null_default_principal_is_misconfigured_not_a_bug(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(sources).values(
                default_public=False,
                default_principals=sa.text("array[null]::text[]"),
            )
        )

    job = await sync(runtime, FakeSource([]), start)

    assert job.last_error_code == JobFailure.SOURCE_MISCONFIGURED.value


# --- The production opener and the migration


def test_a_local_folder_opens_with_its_stored_readers() -> None:
    settings = SourceSettings(
        source_id=SOURCE_ID,
        configuration=SourceConfiguration(
            kind=SourceKind.LOCAL_DIRECTORY, location="/srv/docs"
        ),
        default_access=SourceAccess(public=False, principals=("group:engineering",)),
    )

    opened = open_source(settings)

    assert isinstance(opened, DirectorySource)


def test_the_migration_goes_down_and_up_with_passes_in_progress(
    migrated_database: str,
) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    in_progress = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
    full_id = SourceId.parse("00000000-0000-0000-0000-0000000000a1")
    try:
        with engine.begin() as connection:
            for source_id, mode in ((SOURCE_ID, "incremental"), (full_id, "full")):
                connection.execute(
                    sa.insert(sources).values(
                        source_id=source_id.value,
                        kind="local_directory",
                        location=f"/srv/{mode}",
                        display_name=mode,
                        enabled=True,
                        created_at=in_progress,
                        updated_at=in_progress,
                        pass_started_at=in_progress,
                        pass_mode=mode,
                        checkpoint="1",
                    )
                )
                connection.execute(
                    sa.insert(synchronization_seen).values(
                        [
                            {
                                "source_id": source_id.value,
                                "external_id": "kept.txt",
                                "deleted": False,
                            },
                            {
                                "source_id": source_id.value,
                                "external_id": "gone.txt",
                                "deleted": True,
                            },
                        ]
                    )
                )
        with migration_environment(url):
            command.downgrade(config, "e1f2a3b4c5d6")
        with engine.connect() as connection:
            columns = {
                column["name"]
                for column in sa.inspect(connection).get_columns("sources")
            }
            passes = connection.execute(
                sa.text("select source_id, checkpoint from sources order by location")
            ).all()
            seen = connection.execute(
                sa.text("select source_id, external_id from synchronization_seen")
            ).all()
        assert not columns & {
            "pass_mode",
            "sync_cursor",
            "last_full_pass_at",
            "default_public",
            "default_principals",
        }
        # The full pass keeps its progress, minus what it saw deleted; the
        # incremental one has no meaning before the revision and is dropped.
        assert [(str(row[0]), row[1]) for row in passes] == [
            (str(full_id.value), "1"),
            (str(SOURCE_ID.value), None),
        ]
        assert [(str(row[0]), row[1]) for row in seen] == [
            (str(full_id.value), "kept.txt")
        ]
    finally:
        with migration_environment(url):
            command.upgrade(config, "head")
        engine.dispose()
