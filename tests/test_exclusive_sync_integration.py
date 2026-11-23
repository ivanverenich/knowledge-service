"""One synchronization pass per Source at a time, against real PostgreSQL and Redis.

The lease saves work; the fence in PostgreSQL keeps the data right. The fence
tests use a lease store that lets every pass in, as an expired lease would,
and show that an older pass still cannot write after a newer one started.
"""

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.audit import AuditAction, AuditOutcome
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import DocumentAvailability
from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.exclusive_sync import (
    SourceBusy,
    SourceCoordinationUnavailable,
    SourceLeaseLost,
    lease_name,
    synchronize_source_exclusively,
)
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourceAccess,
    SourcePage,
    SourcePort,
    SynchronizationSuperseded,
)
from knowledge_service.ingestion.synchronization import synchronize_source
from knowledge_service.leases import (
    NAMESPACE,
    Lease,
    LeaseStore,
    RedisLeaseStore,
    lease_client,
)
from knowledge_service.persistence.audit import load_audit_events
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.persistence.sources import (
    claim_synchronization_fence,
    synchronization_fence_holds,
)
from knowledge_service.settings import Settings

pytestmark = pytest.mark.integration

REDIS_URL_ENV = "KNOWLEDGE_SERVICE_TEST_REDIS_URL"
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000030")
OBSERVED_AT = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)
PUBLIC = SourceAccess(public=True)
SHORT = timedelta(milliseconds=300)

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/exclusive', 'Exclusive', true, now(), now())"
)


@dataclass
class WatchedStore:
    """A real lease store that counts renewals and can act on each one."""

    inner: RedisLeaseStore
    renewals: int = 0
    on_renew: Callable[[Lease], object] | None = None
    released: list[Lease] = field(default_factory=lambda: list[Lease]())

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
        return await self.inner.acquire(name, ttl)

    async def renew(self, lease: Lease) -> bool:
        self.renewals += 1
        if self.on_renew is not None:
            outcome = self.on_renew(lease)
            if asyncio.iscoroutine(outcome):
                await outcome
        return await self.inner.renew(lease)

    async def release(self, lease: Lease) -> bool:
        self.released.append(lease)
        return await self.inner.release(lease)


@pytest.fixture
def redis_url() -> str:
    configured = os.environ.get(REDIS_URL_ENV)
    if not configured:
        pytest.skip(f"set {REDIS_URL_ENV} to a disposable Redis server")
    return configured


@pytest.fixture
async def store(redis_url: str) -> AsyncIterator[RedisLeaseStore]:
    client = lease_client(redis_url)
    leases = RedisLeaseStore(client)
    try:
        yield leases
    finally:
        await client.delete(NAMESPACE + lease_name(SOURCE_ID))
        await client.aclose()


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
    try:
        yield runtime
    finally:
        await runtime.dispose()


def folder(path: Path, *names: str) -> DirectorySource:
    path.mkdir(parents=True, exist_ok=True)
    for name in names:
        (path / name).write_text(f"# {name}\n\nThe text of {name}.\n")
    return DirectorySource(path, access=PUBLIC)


async def availability(runtime: DatabaseRuntime) -> dict[str, str]:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(documents.c.external_id, documents.c.availability).where(
                documents.c.source_id == SOURCE_ID.value
            )
        )
        return {row.external_id: row.availability for row in rows}


async def finished_outcomes(runtime: DatabaseRuntime) -> list[AuditOutcome]:
    async with runtime.transaction() as connection:
        events = await load_audit_events(
            connection, target_id=SOURCE_ID.value, limit=20
        )
    return [
        event.outcome
        for event in events
        if event.action is AuditAction.SYNCHRONIZATION_FINISHED
    ]


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def test_a_pass_renews_its_lease_after_every_page_and_gives_it_up(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    watched = WatchedStore(store)

    summary = await synchronize_source_exclusively(
        runtime,
        folder(tmp_path, "a.md", "b.md", "c.md"),
        leases=watched,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
        page_size=1,
    )

    assert sum(summary.changes.values()) == 3
    assert watched.renewals == 3
    assert len(watched.released) == 1
    # Given up: the next pass can take it at once.
    assert await store.acquire(lease_name(SOURCE_ID), SHORT) is not None


async def test_a_second_pass_does_nothing_while_another_holds_the_source(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    other = await store.acquire(lease_name(SOURCE_ID), timedelta(seconds=30))
    assert other is not None

    with pytest.raises(SourceBusy):
        await synchronize_source_exclusively(
            runtime,
            folder(tmp_path, "a.md"),
            leases=store,
            source_id=SOURCE_ID,
            observed_at=OBSERVED_AT,
            principal_directory=None,
        )

    assert await availability(runtime) == {}
    assert await finished_outcomes(runtime) == []
    # The other holder still has it.
    assert await store.renew(other)


async def test_a_crashed_pass_blocks_its_source_only_until_the_lease_expires(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    # A pass took the lease and died without giving it up.
    assert await store.acquire(lease_name(SOURCE_ID), SHORT) is not None
    source = folder(tmp_path, "a.md")
    with pytest.raises(SourceBusy):
        await synchronize_source_exclusively(
            runtime,
            source,
            leases=store,
            source_id=SOURCE_ID,
            observed_at=OBSERVED_AT,
            principal_directory=None,
        )

    await asyncio.sleep(0.45)
    summary = await synchronize_source_exclusively(
        runtime,
        source,
        leases=store,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )

    assert sum(summary.changes.values()) == 1


async def test_a_pass_that_loses_its_lease_stops_before_tombstoning(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    await synchronize_source(
        runtime,
        folder(tmp_path / "before", "a.md", "b.md", "gone.md"),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    taken_over: list[Lease] = []

    async def expire_and_take_over(lease: Lease) -> None:
        # The lease runs out mid-pass, and another pass takes it.
        if not taken_over:
            await store.release(lease)
            other = await store.acquire(lease.name, timedelta(seconds=30))
            assert other is not None
            taken_over.append(other)

    watched = WatchedStore(store, on_renew=expire_and_take_over)
    with pytest.raises(SourceLeaseLost):
        await synchronize_source_exclusively(
            runtime,
            folder(tmp_path / "after", "a.md", "b.md"),
            leases=watched,
            source_id=SOURCE_ID,
            observed_at=LATER,
            principal_directory=None,
            page_size=1,
        )

    # Stopped after its first page: nothing was tombstoned.
    assert (await availability(runtime))["gone.md"] == DocumentAvailability.AVAILABLE
    assert await finished_outcomes(runtime) == [
        AuditOutcome.SUCCEEDED,
        AuditOutcome.FAILED,
    ]
    # Releasing its own lease did not take the new holder's.
    assert await store.renew(taken_over[0])


async def test_a_cancelled_pass_gives_up_its_lease(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    first_page_done = asyncio.Event()
    never = asyncio.Event()
    inner = folder(tmp_path, "a.md", "b.md")

    class StallsOnSecondPage:
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            if checkpoint is not None:
                first_page_done.set()
                await never.wait()
            return await inner.fetch_page(checkpoint=checkpoint, page_size=page_size)

    source: SourcePort = StallsOnSecondPage()
    task = asyncio.create_task(
        synchronize_source_exclusively(
            runtime,
            source,
            leases=store,
            source_id=SOURCE_ID,
            observed_at=OBSERVED_AT,
            principal_directory=None,
            page_size=1,
        )
    )
    await first_page_done.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await store.acquire(lease_name(SOURCE_ID), SHORT) is not None


async def test_an_unreachable_lease_store_stops_the_pass_before_it_reads(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    client = lease_client(f"redis://127.0.0.1:{closed_port()}/0")
    reads: list[str | None] = []
    inner = folder(tmp_path, "a.md")

    class Recording:
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            reads.append(checkpoint)
            return await inner.fetch_page(checkpoint=checkpoint, page_size=page_size)

    try:
        with pytest.raises(SourceCoordinationUnavailable):
            await synchronize_source_exclusively(
                runtime,
                Recording(),
                leases=RedisLeaseStore(client),
                source_id=SOURCE_ID,
                observed_at=OBSERVED_AT,
                principal_directory=None,
            )
    finally:
        await client.aclose()

    assert reads == []
    assert await availability(runtime) == {}


@dataclass
class EveryoneGetsIn:
    """A lease store whose lease never stops anyone, as if it kept expiring.

    `pause` makes the first renewal of one pass wait, so a test can run a
    newer pass in between.
    """

    pause: asyncio.Event | None = None
    paused: asyncio.Event = field(default_factory=asyncio.Event)

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
        return Lease(name=name, token=os.urandom(16).hex(), ttl=ttl)

    async def renew(self, lease: Lease) -> bool:
        if self.pause is not None and not self.paused.is_set():
            self.paused.set()
            await self.pause.wait()
        return True

    async def release(self, lease: Lease) -> bool:
        return True


async def text_of(runtime: DatabaseRuntime) -> str:
    async with runtime.transaction() as connection:
        rows = await connection.scalars(sa.select(chunks.c.text))
        return " ".join(rows)


async def exclusive(
    runtime: DatabaseRuntime,
    source: SourcePort,
    leases: LeaseStore,
    observed_at: datetime,
) -> object:
    return await synchronize_source_exclusively(
        runtime,
        source,
        leases=leases,
        source_id=SOURCE_ID,
        observed_at=observed_at,
        principal_directory=None,
    )


async def test_an_older_pass_cannot_tombstone_what_a_newer_pass_saw(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    await synchronize_source(
        runtime,
        folder(tmp_path / "start", "x.md", "y.md"),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    # The older pass no longer sees x.md; it stops at its last renewal.
    resume = asyncio.Event()
    older_store = EveryoneGetsIn(pause=resume)
    older = asyncio.create_task(
        exclusive(
            runtime,
            folder(tmp_path / "older", "y.md"),
            older_store,
            OBSERVED_AT + timedelta(minutes=10),
        )
    )
    await older_store.paused.wait()
    # A newer pass sees both files, unchanged, so it writes nothing.
    await exclusive(
        runtime, folder(tmp_path / "newer", "x.md", "y.md"), EveryoneGetsIn(), LATER
    )
    resume.set()

    with pytest.raises(SynchronizationSuperseded):
        await older
    assert (await availability(runtime))["x.md"] == DocumentAvailability.AVAILABLE


async def test_an_older_pass_cannot_write_a_page_it_read_before_a_newer_pass(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    await synchronize_source(
        runtime,
        folder(tmp_path / "start", "x.md"),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    edited = tmp_path / "older"
    edited.mkdir()
    (edited / "x.md").write_text("# x.md\n\nAn edit the Source later undid.\n")
    page_read = asyncio.Event()
    newer_done = asyncio.Event()
    inner = DirectorySource(edited, access=PUBLIC)

    class SlowFirstPage:
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            page = await inner.fetch_page(checkpoint=checkpoint, page_size=page_size)
            page_read.set()
            # The page arrives only after a newer pass has finished.
            await newer_done.wait()
            return page

    older = asyncio.create_task(
        exclusive(
            runtime,
            SlowFirstPage(),
            EveryoneGetsIn(),
            OBSERVED_AT + timedelta(minutes=10),
        )
    )
    await page_read.wait()
    await exclusive(
        runtime, folder(tmp_path / "newer", "x.md"), EveryoneGetsIn(), LATER
    )
    newer_done.set()

    with pytest.raises(SynchronizationSuperseded):
        await older
    text = await text_of(runtime)
    assert "The text of x.md." in text
    assert "An edit the Source later undid." not in text


async def test_a_pass_that_did_not_observe_after_the_fence_reads_nothing(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    await exclusive(
        runtime, folder(tmp_path / "first", "a.md"), EveryoneGetsIn(), LATER
    )
    reads: list[str | None] = []
    inner = folder(tmp_path / "second", "a.md", "b.md")

    class Recording:
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            reads.append(checkpoint)
            return await inner.fetch_page(checkpoint=checkpoint, page_size=page_size)

    # Same time, then earlier: neither is newer than the fence.
    for observed_at in (LATER, OBSERVED_AT):
        with pytest.raises(SynchronizationSuperseded):
            await exclusive(runtime, Recording(), EveryoneGetsIn(), observed_at)

    assert reads == []
    assert set(await availability(runtime)) == {"a.md"}


async def test_a_newer_claim_waits_for_a_write_that_holds_the_fence(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        assert await claim_synchronization_fence(
            connection, source_id=SOURCE_ID, observed_at=OBSERVED_AT
        )
    write_holds = asyncio.Event()
    finish_write = asyncio.Event()

    async def a_write_of_the_older_pass() -> None:
        async with runtime.transaction() as connection:
            assert await synchronization_fence_holds(
                connection, source_id=SOURCE_ID, observed_at=OBSERVED_AT
            )
            write_holds.set()
            await finish_write.wait()

    async def the_newer_claim() -> bool:
        async with runtime.transaction() as connection:
            return await claim_synchronization_fence(
                connection, source_id=SOURCE_ID, observed_at=LATER
            )

    write = asyncio.create_task(a_write_of_the_older_pass())
    await write_holds.wait()
    claim = asyncio.create_task(the_newer_claim())
    await asyncio.sleep(0.3)
    # Still waiting for the write's share lock on the Source's row.
    assert not claim.done()

    finish_write.set()
    await write
    assert await claim
    async with runtime.transaction() as connection:
        assert not await synchronization_fence_holds(
            connection, source_id=SOURCE_ID, observed_at=OBSERVED_AT
        )


async def test_an_observation_from_the_far_future_cannot_take_the_fence(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    far_future = datetime.now(UTC) + timedelta(days=365)

    with pytest.raises(InvalidSource, match="future"):
        await exclusive(
            runtime, folder(tmp_path / "bad", "a.md"), EveryoneGetsIn(), far_future
        )

    # A real pass can still claim the Source.
    await exclusive(runtime, folder(tmp_path / "good", "a.md"), EveryoneGetsIn(), LATER)
    assert set(await availability(runtime)) == {"a.md"}


async def test_an_unleased_newer_pass_also_fences_out_an_older_leased_one(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    await synchronize_source(
        runtime,
        folder(tmp_path / "start", "x.md", "y.md"),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    resume = asyncio.Event()
    older_store = EveryoneGetsIn(pause=resume)
    older = asyncio.create_task(
        exclusive(
            runtime,
            folder(tmp_path / "older", "y.md"),
            older_store,
            OBSERVED_AT + timedelta(minutes=10),
        )
    )
    await older_store.paused.wait()
    # Every pass claims the fence, with or without a lease.
    await synchronize_source(
        runtime,
        folder(tmp_path / "newer", "x.md", "y.md"),
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=None,
    )
    resume.set()

    with pytest.raises(SynchronizationSuperseded):
        await older
    assert (await availability(runtime))["x.md"] == DocumentAvailability.AVAILABLE


async def test_a_retry_must_observe_the_source_again(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    class FailsOnce:
        def __init__(self, inner: DirectorySource) -> None:
            self.inner = inner
            self.failed = False

        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            if not self.failed:
                self.failed = True
                raise SourceCoordinationUnavailable("a blip")
            return await self.inner.fetch_page(
                checkpoint=checkpoint, page_size=page_size
            )

    source = FailsOnce(folder(tmp_path, "a.md"))
    with pytest.raises(SourceCoordinationUnavailable):
        await exclusive(runtime, source, EveryoneGetsIn(), OBSERVED_AT)

    # The same observation time cannot claim the fence again...
    with pytest.raises(SynchronizationSuperseded):
        await exclusive(runtime, source, EveryoneGetsIn(), OBSERVED_AT)
    # ...a new one can.
    await exclusive(runtime, source, EveryoneGetsIn(), LATER)
    assert set(await availability(runtime)) == {"a.md"}
