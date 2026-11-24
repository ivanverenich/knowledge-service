"""The M08 gate on real PostgreSQL: a worker killed mid-page, and a new field.

The first part kills a synchronization Job's worker before each record of a
pass in turn, redelivers its message, lets recovery and the retry finish
the pass, and compares everything stored with a pass nobody interrupted.
The second part sends a Source item's web address, `web_url`, through the
same Job and checks where every Chunk's anchor leads.
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

import knowledge_service.ingestion.synchronization as synchronization
from fakes import FakeSource
from knowledge_service.audit import AuditAction
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourcePort,
    SourceRecord,
)
from knowledge_service.ingestion.synchronization_jobs import SynchronizationJobs
from knowledge_service.job_dispatch import requeue_due_retries
from knowledge_service.job_failures import JobFailure, RetryPolicy
from knowledge_service.job_recovery import recover_stuck_jobs
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.leases import Lease, LeaseStore
from knowledge_service.persistence.jobs import enqueue_job, load_job
from knowledge_service.persistence.schema import (
    access_grants,
    audit_events,
    chunks,
    documents,
    job_attempts,
    jobs,
    sources,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.jobs import JobOutcome, run_stored_job

pytestmark = pytest.mark.integration

CLEAN = SourceId.parse("00000000-0000-0000-0000-0000000016c1")
CRASHED = SourceId.parse("00000000-0000-0000-0000-0000000016c2")
RETRIES = RetryPolicy(
    base_delay=timedelta(seconds=1),
    max_delay=timedelta(seconds=1),
    jitter=lambda: 0.0,
)


class MemoryLeases:
    """A lease store that always grants and renews; it holds no state."""

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
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


async def register(runtime: DatabaseRuntime, source_id: SourceId) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.insert(sources).values(
                source_id=source_id.value,
                kind="local_directory",
                location=f"/srv/{source_id}",
                display_name="Handbook",
                enabled=True,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                default_public=True,
            )
        )


async def queue(runtime: DatabaseRuntime, source_id: SourceId, at: datetime) -> JobId:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=source_id.value,
        queued_at=at,
    )
    async with runtime.transaction() as connection:
        return (await enqueue_job(connection, job)).job.job_id


def handler(source: SourcePort, at: datetime) -> SynchronizationJobs:
    """The real handler, opening `source` for every stored Source."""

    @asynccontextmanager
    async def memory() -> AsyncGenerator[LeaseStore]:
        yield MemoryLeases()

    return SynchronizationJobs(
        open_source=lambda _settings: source,
        leases=memory,
        principal_directory=None,
        full_pass_interval=timedelta(hours=24),
        retry_policy=RETRIES,
        clock=lambda: at,
    )


async def deliver(
    runtime: DatabaseRuntime, job_id: JobId, source: SourcePort, at: datetime
) -> JobOutcome:
    """Deliver the Job's message once, the way a worker does."""
    return await run_stored_job(
        runtime,
        job_id,
        now=at,
        handlers={JobKind.SYNCHRONIZE_SOURCE: handler(source, at)},
    )


async def sync(
    runtime: DatabaseRuntime, source_id: SourceId, source: SourcePort, at: datetime
) -> Job:
    """Queue one Job at `at` and run it to its end."""
    job_id = await queue(runtime, source_id, at)
    return (await deliver(runtime, job_id, source, at)).job


def item(
    name: str,
    text: str = "",
    *,
    web_url: str | None = None,
    principals: tuple[str, ...] = (),
) -> SourceRecord:
    """A text item; public unless it names principals."""
    return SourceRecord.present(
        external_id=name,
        version="v1",
        media_type="text/plain",
        content=(text or f"The {name} policy.").encode(),
        access=SourceAccess(public=not principals, principals=principals),
        metadata=SourceMetadata(
            title=name,
            modified_at=datetime(2026, 1, 1, tzinfo=UTC),
            size_bytes=0,
            web_url=web_url,
        ),
    )


@dataclass(frozen=True)
class Stored:
    """Everything a pass leaves behind for one Document, without its IDs."""

    title: str
    content_fingerprint: str
    content_version: int
    authorization_version: int
    availability: str
    chunks: tuple[tuple[str, str], ...]
    readers: tuple[str, ...]
    events: tuple[str, ...]

    @property
    def anchors(self) -> tuple[str, ...]:
        return tuple(anchor for _, anchor in self.chunks)


async def snapshot(runtime: DatabaseRuntime, source_id: SourceId) -> dict[str, Stored]:
    """What the Source's Documents look like, keyed by their external ID."""
    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(documents).where(documents.c.source_id == source_id.value)
            )
        ).mappings()
        stored: dict[str, Stored] = {}
        for row in rows:
            document_id = row["document_id"]
            texts = await connection.execute(
                sa.select(chunks.c.text, chunks.c.source_anchor)
                .where(chunks.c.document_id == document_id)
                .order_by(chunks.c.ordinal)
            )
            readers = await connection.scalars(
                sa.select(access_grants.c.subject_kind).where(
                    access_grants.c.document_id == document_id
                )
            )
            events = await connection.scalars(
                sa.select(audit_events.c.action).where(
                    audit_events.c.target_id == document_id
                )
            )
            stored[row["external_id"]] = Stored(
                title=row["title"],
                content_fingerprint=row["content_fingerprint"],
                content_version=row["content_version"],
                authorization_version=row["authorization_version"],
                availability=row["availability"],
                chunks=tuple((text, anchor or "") for text, anchor in texts),
                readers=tuple(sorted(readers)),
                events=tuple(sorted(events)),
            )
    return stored


@dataclass(frozen=True)
class SourceState:
    """What a pass leaves on the Source itself."""

    checkpoint: str | None
    pass_mode: str | None
    finished_events: int
    purges: int


async def source_state(runtime: DatabaseRuntime, source_id: SourceId) -> SourceState:
    async with runtime.transaction() as connection:
        row = (
            await connection.execute(
                sa.select(sources.c.checkpoint, sources.c.pass_mode).where(
                    sources.c.source_id == source_id.value
                )
            )
        ).one()
        finished = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(audit_events)
            .where(
                audit_events.c.action == AuditAction.SYNCHRONIZATION_FINISHED.value,
                audit_events.c.target_id == source_id.value,
            )
        )
        purges = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(jobs)
            .where(
                jobs.c.kind == JobKind.PURGE_DOCUMENT_CHUNKS.value,
                jobs.c.target_id.in_(
                    sa.select(documents.c.document_id).where(
                        documents.c.source_id == source_id.value
                    )
                ),
            )
        )
    return SourceState(row.checkpoint, row.pass_mode, finished or 0, purges or 0)


async def attempts(runtime: DatabaseRuntime, job_id: JobId) -> int:
    async with runtime.transaction() as connection:
        return (
            await connection.scalar(
                sa.select(sa.func.count())
                .select_from(job_attempts)
                .where(job_attempts.c.job_id == job_id.value)
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


# --- A worker killed mid-page

FIRST: Sequence[Sequence[SourceRecord]] = [
    [item("a.txt"), item("b.txt")],
    [item("pay.txt"), item("old.txt")],
]
# Pass two edits b.txt, takes pay.txt away from everyone it could resolve,
# adds c and e, and no longer lists old.txt.
SECOND: Sequence[Sequence[SourceRecord]] = [
    [item("a.txt"), item("b.txt", "The b.txt policy, rewritten.")],
    [item("c.txt"), item("pay.txt", principals=("payroll",))],
    [item("e.txt")],
]
# After pass two: old.txt is tombstoned, b.txt has new text, and nobody
# this service knows may read pay.txt any more.
EXPECTED = {
    "a.txt": ("available", 1, ("public",)),
    "b.txt": ("available", 2, ("public",)),
    "c.txt": ("available", 1, ("public",)),
    "e.txt": ("available", 1, ("public",)),
    "old.txt": ("tombstoned", 1, ("public",)),
    "pay.txt": ("available", 1, ()),
}
# Where the worker dies: before the n-th call of one step of the pass.
KILL_POINTS = [
    *(("reconcile_document", n) for n in range(1, 6)),
    *(("commit_page", n) for n in range(1, 4)),
    ("finish_pass", 1),
]


@pytest.mark.parametrize(("step", "call"), KILL_POINTS)
async def test_a_worker_killed_mid_pass_ends_as_if_nothing_happened(
    runtime: DatabaseRuntime,
    monkeypatch: pytest.MonkeyPatch,
    step: str,
    call: int,
) -> None:
    """Kill the worker before call `call` of `step` in the second pass."""
    earlier = datetime.now(UTC) - timedelta(hours=2)
    for source_id in (CLEAN, CRASHED):
        await register(runtime, source_id)
        await sync(runtime, source_id, FakeSource(FIRST), earlier)
    now = datetime.now(UTC)
    clean = await sync(runtime, CLEAN, FakeSource(SECOND), now)
    assert clean.status is JobStatus.SUCCEEDED

    # The injected crash: the worker stops for good at one exact point and
    # is cancelled, so it records nothing more. Unlike a killed process, a
    # cancellation still rolls back and releases; a real kill is a drill.
    real = getattr(synchronization, step)
    reached = asyncio.Event()
    calls = 0

    async def dies_here(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == call:
            reached.set()
            await asyncio.Event().wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(synchronization, step, dies_here)
    job_id = await queue(runtime, CRASHED, now)
    worker = asyncio.create_task(deliver(runtime, job_id, FakeSource(SECOND), now))
    # A kill point the pass never reaches fails here instead of hanging.
    await asyncio.wait_for(reached.wait(), timeout=30)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    monkeypatch.setattr(synchronization, step, real)

    # Delivery: the broker gives the message to another worker. The Job is
    # still running in PostgreSQL, so that delivery runs nothing.
    redelivered = FakeSource(SECOND)
    duplicate = await deliver(runtime, job_id, redelivered, now)
    assert duplicate.duplicate is True
    assert redelivered.checkpoints == []

    # Business state: only recovery, five silent minutes later, may decide
    # the worker is gone and plan the retry.
    await fall_silent(runtime, job_id)
    recovered = await recover_stuck_jobs(
        runtime, now=datetime.now(UTC), retry_policy=RETRIES
    )
    assert recovered.stale == (job_id,)
    async with runtime.transaction() as connection:
        lost = await load_job(connection, job_id=job_id)
    assert lost is not None
    assert lost.last_error_code == JobFailure.WORKER_LOST.value
    resume_from = (await source_state(runtime, CRASHED)).checkpoint

    later = datetime.now(UTC) + timedelta(seconds=1)
    assert await requeue_due_retries(runtime, now=later) == (job_id,)
    retried = FakeSource(SECOND)
    outcome = await deliver(runtime, job_id, retried, later)

    assert outcome.job.status is JobStatus.SUCCEEDED
    assert await attempts(runtime, job_id) == 2
    # The retry starts where the last committed page left off.
    assert retried.checkpoints[0] == resume_from
    stored = await snapshot(runtime, CRASHED)
    assert stored == await snapshot(runtime, CLEAN)
    assert await source_state(runtime, CRASHED) == await source_state(runtime, CLEAN)
    # Both are right, not merely the same.
    assert {
        name: (document.availability, document.content_version, document.readers)
        for name, document in stored.items()
    } == EXPECTED
    assert (await source_state(runtime, CRASHED)).purges == 1


# --- A new Source field: web_url

WIKI = "https://wiki.example.com/spaces/HR/pages/42"


async def anchors(runtime: DatabaseRuntime, name: str) -> tuple[str, ...]:
    return (await snapshot(runtime, CRASHED))[name].anchors


async def test_a_web_url_becomes_where_every_chunk_leads(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)

    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=WIKI)]]),
        datetime.now(UTC),
    )

    assert await anchors(runtime, "leave.txt") == (f"{WIKI}#offset=0-21",)


async def test_a_moved_page_rewrites_its_chunks_and_nothing_else(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    start = datetime.now(UTC) - timedelta(minutes=10)
    await sync(runtime, CRASHED, FakeSource([[item("leave.txt", web_url=WIKI)]]), start)
    moved = "https://wiki.example.com/spaces/HR/pages/43"

    job = await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=moved)]]),
        start + timedelta(minutes=5),
    )

    stored = (await snapshot(runtime, CRASHED))["leave.txt"]
    assert stored.anchors == (f"{moved}#offset=0-21",)
    # The text is the same, but every Chunk stores the link, so they change.
    assert (stored.content_version, stored.authorization_version) == (2, 1)
    assert job.status is JobStatus.SUCCEEDED


async def test_the_same_web_url_again_changes_nothing(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    start = datetime.now(UTC) - timedelta(minutes=10)
    pages = [[item("leave.txt", web_url=WIKI)]]
    await sync(runtime, CRASHED, FakeSource(pages), start)
    before = await snapshot(runtime, CRASHED)

    await sync(runtime, CRASHED, FakeSource(pages), start + timedelta(minutes=5))

    assert await snapshot(runtime, CRASHED) == before


async def test_a_secret_in_a_web_url_never_reaches_a_chunk(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    leaky = "https://reader:hunter2@wiki.example.com/spaces/HR/pages/42?token=SECRET#x"

    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=leaky)]]),
        datetime.now(UTC),
    )

    assert await anchors(runtime, "leave.txt") == (f"{WIKI}#offset=0-21",)


async def test_a_new_token_in_the_same_link_changes_nothing(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    start = datetime.now(UTC) - timedelta(minutes=10)
    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=f"{WIKI}?token=one")]]),
        start,
    )
    before = await snapshot(runtime, CRASHED)

    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=f"{WIKI}?token=two")]]),
        start + timedelta(minutes=5),
    )

    # Only the safe form counts, so a new session token rewrites nothing.
    assert await snapshot(runtime, CRASHED) == before


async def test_an_unsafe_web_url_falls_back_to_the_external_id(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)

    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url="javascript:alert(1)")]]),
        datetime.now(UTC),
    )

    assert await anchors(runtime, "leave.txt") == ("leave.txt#offset=0-21",)


async def test_a_source_that_starts_sending_links_rewrites_the_chunks_once(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    start = datetime.now(UTC) - timedelta(minutes=10)
    await sync(runtime, CRASHED, FakeSource([[item("leave.txt")]]), start)

    # A Source that later starts sending links rewrites the Chunks once.
    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=WIKI)]]),
        start + timedelta(minutes=5),
    )

    stored = (await snapshot(runtime, CRASHED))["leave.txt"]
    assert stored.anchors == (f"{WIKI}#offset=0-21",)
    assert stored.content_version == 2
