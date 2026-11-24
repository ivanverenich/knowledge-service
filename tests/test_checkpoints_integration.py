"""Real PostgreSQL tests for a synchronization pass that resumes from its checkpoint.

Each test stops a pass at one boundary, the way a worker crash would, and
runs the pass again later: nothing is lost, and the replayed work changes
nothing that already landed.
"""

import asyncio
from collections.abc import AsyncIterator, Collection
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection

from database_fixtures import migration_config, migration_environment
from fakes import FakeSource
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import DocumentAvailability
from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion import checkpoints, synchronization
from knowledge_service.ingestion.checkpoints import begin_pass, commit_page
from knowledge_service.ingestion.normalization import NormalizedDocument
from knowledge_service.ingestion.reconciliation import Change, Reconciliation
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourceAccess,
    SourcePage,
    SourceRecord,
    SourceUnavailable,
    SynchronizationSuperseded,
)
from knowledge_service.ingestion.synchronization import (
    SynchronizationSummary,
    synchronize_source,
)
from knowledge_service.persistence.schema import (
    documents,
    sources,
    synchronization_seen,
)
from knowledge_service.settings import Settings

pytestmark = pytest.mark.integration

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000090")
OBSERVED_AT = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
RETRIED_AT = OBSERVED_AT + timedelta(minutes=5)
LATER = OBSERVED_AT + timedelta(hours=2)


class Crash(Exception):
    """Stands in for a worker that dies at one exact point."""


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.insert(sources).values(
                source_id=SOURCE_ID.value,
                kind="local_directory",
                location="/srv/checkpoints",
                display_name="Checkpoints",
                enabled=True,
                created_at=OBSERVED_AT,
                updated_at=OBSERVED_AT,
            )
        )
    try:
        yield runtime
    finally:
        await runtime.dispose()


def record(name: str, text: str = "") -> SourceRecord:
    return SourceRecord.present(
        external_id=name,
        version="v1",
        media_type="text/plain",
        content=(text or f"The {name} policy.").encode(),
        access=SourceAccess(public=True),
    )


async def run(
    runtime: DatabaseRuntime,
    source: FakeSource,
    observed_at: datetime,
    *,
    max_resume_age: timedelta = checkpoints.MAX_RESUME_AGE,
) -> SynchronizationSummary:
    return await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=observed_at,
        principal_directory=None,
        page_size=1,
        max_resume_age=max_resume_age,
    )


async def availability(runtime: DatabaseRuntime) -> dict[str, str]:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(documents.c.external_id, documents.c.availability)
        )
        return {row.external_id: row.availability for row in rows}


async def progress(runtime: DatabaseRuntime) -> tuple[str | None, datetime | None]:
    async with runtime.transaction() as connection:
        row = (
            await connection.execute(
                sa.select(sources.c.checkpoint, sources.c.pass_started_at).where(
                    sources.c.source_id == SOURCE_ID.value
                )
            )
        ).one()
    return row.checkpoint, row.pass_started_at


THREE_PAGES = [[record("a.txt")], [record("b.txt")], [record("c.txt")]]


async def test_a_pass_resumes_after_the_last_committed_page(
    runtime: DatabaseRuntime,
) -> None:
    # The worker dies fetching page three; pages one and two committed.
    crashed = FakeSource(THREE_PAGES, fail_on_call=3)
    with pytest.raises(SourceUnavailable):
        await run(runtime, crashed, OBSERVED_AT)
    assert await progress(runtime) == ("2", OBSERVED_AT)

    retried = FakeSource(THREE_PAGES)
    summary = await run(runtime, retried, RETRIED_AT)

    assert retried.checkpoints == ["2"]
    assert summary.resumed is True
    assert summary.changes == {Change.CREATED: 1}
    assert summary.tombstones == ()
    assert await availability(runtime) == dict.fromkeys(
        ("a.txt", "b.txt", "c.txt"), DocumentAvailability.AVAILABLE.value
    )
    assert await progress(runtime) == (None, None)


async def test_a_page_reconciled_but_not_committed_is_replayed_harmlessly(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def crash(*args: object, **kwargs: object) -> object:
        raise Crash

    with monkeypatch.context() as patched:
        patched.setattr(synchronization, "commit_page", crash)
        with pytest.raises(Crash):
            await run(runtime, FakeSource(THREE_PAGES), OBSERVED_AT)
    # Page one's record landed, but the checkpoint never moved past it.
    assert await availability(runtime) == {"a.txt": "available"}
    assert await progress(runtime) == (None, OBSERVED_AT)

    retried = FakeSource(THREE_PAGES)
    summary = await run(runtime, retried, RETRIED_AT)

    assert retried.checkpoints == [None, "1", "2"]
    assert summary.changes == {Change.UNCHANGED: 1, Change.CREATED: 2}
    assert summary.tombstones == ()


async def test_a_resumed_pass_does_not_tombstone_what_an_earlier_attempt_saw(
    runtime: DatabaseRuntime,
) -> None:
    await run(runtime, FakeSource([*THREE_PAGES, [record("old.txt")]]), OBSERVED_AT)
    # The next pass sees a, b, c, but no longer old.txt; it dies after the
    # last page committed, before the tombstones.
    crashed = FakeSource(THREE_PAGES)

    async def crash() -> None:
        if crashed.checkpoints[-1] == "2":
            raise Crash

    with pytest.raises(Crash):
        await synchronize_source(
            runtime,
            crashed,
            source_id=SOURCE_ID,
            observed_at=RETRIED_AT,
            principal_directory=None,
            page_size=1,
            heartbeat=crash,
        )

    retried = FakeSource(THREE_PAGES)
    summary = await run(runtime, retried, RETRIED_AT + timedelta(minutes=5))

    # Only the last page is read again; a.txt and b.txt are still known seen.
    assert retried.checkpoints == ["2"]
    assert [tombstone.external_id for tombstone in summary.tombstones] == ["old.txt"]
    assert await availability(runtime) == {
        "a.txt": "available",
        "b.txt": "available",
        "c.txt": "available",
        "old.txt": "tombstoned",
    }


async def test_a_page_commit_is_all_or_nothing(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_reports = checkpoints.record_reports

    async def crash_on_b(
        connection: AsyncConnection,
        *,
        source_id: SourceId,
        offered: Collection[str],
        deleted: Collection[str],
    ) -> None:
        if "b.txt" in offered:
            raise Crash
        await record_reports(
            connection, source_id=source_id, offered=offered, deleted=deleted
        )

    # Page two's commit dies after it moved the checkpoint, before it ends.
    with monkeypatch.context() as patched:
        patched.setattr(checkpoints, "record_reports", crash_on_b)
        with pytest.raises(Crash):
            await run(runtime, FakeSource(THREE_PAGES), OBSERVED_AT)
    # The checkpoint move rolled back with the rest of the commit.
    assert await progress(runtime) == ("1", OBSERVED_AT)

    retried = FakeSource(THREE_PAGES)
    await run(runtime, retried, RETRIED_AT)

    assert retried.checkpoints == ["1", "2"]


async def test_the_checkpoint_never_passes_a_record_that_failed_to_reconcile(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    reconcile_document = synchronization.reconcile_document

    async def crash_on_b(
        runtime: DatabaseRuntime, normalized: NormalizedDocument, **kwargs: Any
    ) -> Reconciliation:
        if normalized.provenance.external_id == "b.txt":
            raise Crash
        return await reconcile_document(runtime, normalized, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(synchronization, "reconcile_document", crash_on_b)
        with pytest.raises(Crash):
            await run(runtime, FakeSource(THREE_PAGES), OBSERVED_AT)
    # Page one committed; page two, whose record failed, did not.
    assert await progress(runtime) == ("1", OBSERVED_AT)

    retried = FakeSource(THREE_PAGES)
    await run(runtime, retried, RETRIED_AT)

    assert retried.checkpoints == ["1", "2"]
    assert await availability(runtime) == dict.fromkeys(
        ("a.txt", "b.txt", "c.txt"), DocumentAvailability.AVAILABLE.value
    )


async def test_a_crash_while_narrowing_a_page_replays_it(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    revoke_first = synchronization._revoke_first  # pyright: ignore[reportPrivateUsage]
    calls: list[int] = []

    async def crash_on_page_two(*args: Any, **kwargs: Any) -> set[str]:
        calls.append(1)
        if len(calls) == 2:
            raise Crash
        return await revoke_first(*args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(synchronization, "_revoke_first", crash_on_page_two)
        with pytest.raises(Crash):
            await run(runtime, FakeSource(THREE_PAGES), OBSERVED_AT)
    assert await progress(runtime) == ("1", OBSERVED_AT)

    retried = FakeSource(THREE_PAGES)
    await run(runtime, retried, RETRIED_AT)

    assert retried.checkpoints == ["1", "2"]
    assert await availability(runtime) == dict.fromkeys(
        ("a.txt", "b.txt", "c.txt"), DocumentAvailability.AVAILABLE.value
    )


async def test_a_page_of_two_records_is_replayed_after_one_landed(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    pages = [[record("a.txt"), record("b.txt")], [record("c.txt")]]
    reconcile_document = synchronization.reconcile_document

    async def crash_on_b(
        runtime: DatabaseRuntime, normalized: NormalizedDocument, **kwargs: Any
    ) -> Reconciliation:
        if normalized.provenance.external_id == "b.txt":
            raise Crash
        return await reconcile_document(runtime, normalized, **kwargs)

    async def two_per_page(source: FakeSource, at: datetime) -> SynchronizationSummary:
        return await synchronize_source(
            runtime,
            source,
            source_id=SOURCE_ID,
            observed_at=at,
            principal_directory=None,
            page_size=2,
        )

    with monkeypatch.context() as patched:
        patched.setattr(synchronization, "reconcile_document", crash_on_b)
        with pytest.raises(Crash):
            await two_per_page(FakeSource(pages), OBSERVED_AT)
    # a.txt landed; the page holding it and b.txt was never committed.
    assert await availability(runtime) == {"a.txt": "available"}
    assert await progress(runtime) == (None, OBSERVED_AT)

    retried = FakeSource(pages)
    summary = await two_per_page(retried, RETRIED_AT)

    assert retried.checkpoints == [None, "1"]
    assert summary.changes == {Change.UNCHANGED: 1, Change.CREATED: 2}


async def test_a_cancelled_pass_resumes_like_a_crashed_one(
    runtime: DatabaseRuntime,
) -> None:
    waiting = asyncio.Event()

    class HangsOnPageThree(FakeSource):
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            if checkpoint == "2":
                waiting.set()
                await asyncio.Event().wait()
            return await super().fetch_page(checkpoint=checkpoint, page_size=page_size)

    task = asyncio.create_task(run(runtime, HangsOnPageThree(THREE_PAGES), OBSERVED_AT))
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await progress(runtime) == ("2", OBSERVED_AT)

    retried = FakeSource(THREE_PAGES)
    summary = await run(runtime, retried, RETRIED_AT)

    assert retried.checkpoints == ["2"]
    assert summary.resumed is True


async def test_a_crash_after_the_finish_starts_the_next_pass_over(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    await run(
        runtime, FakeSource([[record("a.txt")], [record("old.txt")]]), OBSERVED_AT
    )

    async def crash(*args: object, **kwargs: object) -> None:
        raise Crash

    # The finish committed; the worker dies before it requeues purges.
    with monkeypatch.context() as patched:
        patched.setattr(synchronization, "requeue_unfinished_purges", crash)
        with pytest.raises(Crash):
            await run(runtime, FakeSource([[record("a.txt")]]), RETRIED_AT)
    assert await availability(runtime) == {
        "a.txt": "available",
        "old.txt": "tombstoned",
    }
    assert await progress(runtime) == (None, None)

    again = FakeSource([[record("a.txt")]])
    summary = await run(runtime, again, RETRIED_AT + timedelta(minutes=5))

    assert again.checkpoints == [None]
    assert summary.resumed is False
    assert summary.tombstones == ()


async def test_a_deletion_reported_before_a_crash_is_still_tombstoned(
    runtime: DatabaseRuntime,
) -> None:
    await run(runtime, FakeSource([[record("a.txt")], [record("b.txt")]]), OBSERVED_AT)
    reports_deletion = [
        [SourceRecord.deleted(external_id="a.txt", version="v2")],
        [record("b.txt")],
    ]
    # Page one reports a.txt deleted; the worker dies fetching page two.
    with pytest.raises(SourceUnavailable):
        await run(runtime, FakeSource(reports_deletion, fail_on_call=2), RETRIED_AT)
    # Nothing is tombstoned before the last page.
    assert await availability(runtime) == {"a.txt": "available", "b.txt": "available"}

    retried = FakeSource(reports_deletion)
    summary = await run(runtime, retried, RETRIED_AT + timedelta(minutes=5))

    # The retry never reads page one again, yet the deletion it stored counts.
    assert retried.checkpoints == ["1"]
    assert [tombstone.external_id for tombstone in summary.tombstones] == ["a.txt"]


async def test_an_item_offered_then_reported_deleted_across_a_crash_is_tombstoned(
    runtime: DatabaseRuntime,
) -> None:
    layout = [
        [record("a.txt")],
        [SourceRecord.deleted(external_id="a.txt", version="v2")],
        [record("b.txt")],
    ]
    # Page one offers a.txt, page two reports it deleted; the worker dies
    # fetching page three.
    with pytest.raises(SourceUnavailable):
        await run(runtime, FakeSource(layout, fail_on_call=3), OBSERVED_AT)

    summary = await run(runtime, FakeSource(layout), RETRIED_AT)

    # The last report of a.txt was "deleted", so it is no longer seen.
    assert [tombstone.external_id for tombstone in summary.tombstones] == ["a.txt"]


async def test_an_item_deleted_then_offered_unparseable_stays_available(
    runtime: DatabaseRuntime,
) -> None:
    await run(runtime, FakeSource([[record("x.txt")]]), OBSERVED_AT)
    unparseable = SourceRecord.present(
        external_id="x.txt",
        version="v3",
        media_type="application/x-unknown",
        content=b"???",
        access=SourceAccess(public=True),
    )

    summary = await run(
        runtime,
        FakeSource(
            [[SourceRecord.deleted(external_id="x.txt", version="v2")], [unparseable]]
        ),
        RETRIED_AT,
    )

    # The last report says present; a file that fails to parse is not gone.
    assert summary.tombstones == ()
    assert await availability(runtime) == {"x.txt": "available"}


async def test_a_failed_finish_tombstones_nothing_and_keeps_the_progress(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    await run(runtime, FakeSource([[record("a.txt")], [record("b.txt")]]), OBSERVED_AT)

    async def crash(*args: object, **kwargs: object) -> None:
        raise Crash

    with monkeypatch.context() as patched:
        patched.setattr(checkpoints, "forget_pass", crash)
        with pytest.raises(Crash):
            await run(runtime, FakeSource([[record("a.txt")]]), RETRIED_AT)
    assert await availability(runtime) == {"a.txt": "available", "b.txt": "available"}
    assert await progress(runtime) == (None, RETRIED_AT)

    summary = await run(
        runtime, FakeSource([[record("a.txt")]]), LATER - timedelta(hours=1)
    )

    assert summary.resumed is True
    assert [tombstone.external_id for tombstone in summary.tombstones] == ["b.txt"]
    assert await progress(runtime) == (None, None)


async def test_a_finished_pass_leaves_no_progress_so_the_next_starts_over(
    runtime: DatabaseRuntime,
) -> None:
    await run(runtime, FakeSource(THREE_PAGES), OBSERVED_AT)

    again = FakeSource(THREE_PAGES)
    summary = await run(runtime, again, RETRIED_AT)

    assert again.checkpoints == [None, "1", "2"]
    assert summary.resumed is False
    assert summary.changes == {Change.UNCHANGED: 3}


async def test_progress_older_than_the_resume_age_is_dropped(
    runtime: DatabaseRuntime,
) -> None:
    with pytest.raises(SourceUnavailable):
        await run(runtime, FakeSource(THREE_PAGES, fail_on_call=3), OBSERVED_AT)

    fresh = FakeSource([[record("a.txt")]])
    summary = await run(runtime, fresh, LATER, max_resume_age=timedelta(hours=1))

    # It starts from the beginning, and the old attempt's seen IDs are gone,
    # so b.txt, which this pass did not see, is tombstoned.
    assert fresh.checkpoints == [None]
    assert summary.resumed is False
    assert [tombstone.external_id for tombstone in summary.tombstones] == ["b.txt"]


async def test_an_older_pass_cannot_move_the_checkpoint(
    runtime: DatabaseRuntime,
) -> None:
    await begin_pass(runtime, source_id=SOURCE_ID, observed_at=OBSERVED_AT)
    await begin_pass(runtime, source_id=SOURCE_ID, observed_at=RETRIED_AT)

    with pytest.raises(SynchronizationSuperseded):
        await commit_page(
            runtime,
            source_id=SOURCE_ID,
            observed_at=OBSERVED_AT,
            offered=["a.txt"],
            deleted=[],
            next_checkpoint="7",
        )
    assert await progress(runtime) == (None, OBSERVED_AT)


async def test_a_repeated_checkpoint_is_never_stored(runtime: DatabaseRuntime) -> None:
    class Stuck(FakeSource):
        """Hands back checkpoint "1" after every page."""

        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            page = await super().fetch_page(checkpoint=checkpoint, page_size=page_size)
            return SourcePage(records=page.records, next_checkpoint="1")

    with pytest.raises(InvalidSource, match="repeated a checkpoint"):
        await run(runtime, Stuck([[record("a.txt")], [record("b.txt")]]), OBSERVED_AT)
    assert await progress(runtime) == ("1", OBSERVED_AT)


class Cycle(FakeSource):
    """Pages None, then "A", then "B", and "B" points back to "A"."""

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        self.checkpoints.append(checkpoint)
        following = {None: "A", "A": "B", "B": "A"}[checkpoint]
        return SourcePage(
            records=(record(f"{checkpoint}.txt"),), next_checkpoint=following
        )


async def test_a_repeated_checkpoint_is_refused_by_a_resumed_attempt_too(
    runtime: DatabaseRuntime,
) -> None:
    with pytest.raises(InvalidSource, match="repeated a checkpoint"):
        await run(runtime, Cycle([]), OBSERVED_AT)
    assert await progress(runtime) == ("B", OBSERVED_AT)

    # The retry resumes from "B"; "A" was stored by the first attempt.
    retried = Cycle([])
    with pytest.raises(InvalidSource, match="repeated a checkpoint"):
        await run(runtime, retried, RETRIED_AT)

    assert retried.checkpoints == ["B"]
    assert await progress(runtime) == ("B", OBSERVED_AT)


async def test_a_page_larger_than_one_statement_can_hold_commits(
    runtime: DatabaseRuntime,
) -> None:
    # Two parameters per ID: 40,000 IDs would be 80,000 in one statement,
    # over PostgreSQL's limit of 65,535.
    offered = [f"{number:05}.txt" for number in range(40_000)]
    await begin_pass(runtime, source_id=SOURCE_ID, observed_at=OBSERVED_AT)

    await commit_page(
        runtime,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        offered=offered,
        deleted=offered[:35_000],
        next_checkpoint=None,
    )

    async with runtime.transaction() as connection:
        stored = await connection.scalar(
            sa.select(sa.func.count()).select_from(synchronization_seen)
        )
    assert stored == 40_000


def test_the_migration_goes_down_and_up(migrated_database: str) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    with migration_environment(url):
        command.downgrade(config, "d0e1f2a3b4c5")
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    try:
        with engine.connect() as connection:
            columns = {
                column["name"]
                for column in sa.inspect(connection).get_columns("sources")
            }
            tables = set(sa.inspect(connection).get_table_names())
        assert "pass_started_at" not in columns
        assert "synchronization_seen" not in tables
        assert "synchronization_checkpoints" not in tables
    finally:
        with migration_environment(url):
            command.upgrade(config, "head")
        engine.dispose()
