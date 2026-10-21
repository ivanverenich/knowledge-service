"""Real PostgreSQL tests for reconciling a Document transactionally."""

import asyncio
from collections.abc import AsyncIterator, Coroutine, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from knowledge_service import reconciliation
from knowledge_service.access import AccessGrant, Subject
from knowledge_service.chunks import Chunk
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import (
    Document,
    DocumentAvailability,
    InvalidDocument,
)
from knowledge_service.identifiers import DocumentId, GroupId, SourceId, UserId
from knowledge_service.normalization import NormalizedDocument, normalize
from knowledge_service.persistence import (
    load_access_grants,
    load_chunks,
    load_document,
    replace_chunks,
    upsert_document,
)
from knowledge_service.reconciliation import (
    Change,
    Reconciliation,
    reconcile_document,
)
from knowledge_service.settings import Settings
from knowledge_service.sources import SourceAccess, SourceRecord

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
EXTERNAL_ID = "guides/handbook.md"
OBSERVED_AT = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)
ALICE = Subject.user(UserId.parse("00000000-0000-0000-0000-000000000031"))
FINANCE = Subject.group(GroupId.parse("00000000-0000-0000-0000-000000000041"))
FIRST = "# Team handbook\n\nWelcome to the team.\n\n## Vacation\n\nAsk early.\n"
SECOND = (
    "# Team handbook\n\nWelcome to the team.\n\n## Vacation\n\nAsk a month ahead.\n"
)

INSERT_SOURCE = text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


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


def handbook(markdown: str, version: str) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id=EXTERNAL_ID,
            version=version,
            media_type="text/markdown",
            content=markdown.encode(),
            access=SourceAccess(public=True),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def stored_state(
    runtime: DatabaseRuntime,
) -> tuple[Document, tuple[Chunk, ...], tuple[AccessGrant, ...]]:
    async with runtime.transaction() as connection:
        document = await load_document(
            connection, source_id=SOURCE_ID, external_id=EXTERNAL_ID
        )
        assert document is not None
        chunks = await load_chunks(connection, document_id=document.document_id)
        grants = await load_access_grants(connection, document_id=document.document_id)
    return document, chunks, grants


@pytest.mark.integration
async def test_a_new_document_is_published_with_chunks_and_grants(
    runtime: DatabaseRuntime,
) -> None:
    result = await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )

    document, chunks, grants = await stored_state(runtime)
    assert result.change is Change.CREATED
    assert (document.content_version.number, document.authorization_version.number) == (
        1,
        1,
    )
    assert [chunk.heading_path for chunk in chunks] == ["Team handbook"]
    assert all(
        (chunk.source_anchor or "").startswith(f"{EXTERNAL_ID}#offset=")
        for chunk in chunks
    )
    assert [grant.subject for grant in grants] == [ALICE]


@pytest.mark.integration
async def test_an_unchanged_document_writes_nothing(runtime: DatabaseRuntime) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    before = await stored_state(runtime)

    result = await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=LATER
    )

    assert result.change is Change.UNCHANGED
    assert await stored_state(runtime) == before


@pytest.mark.integration
async def test_a_content_change_replaces_chunks_and_keeps_grants(
    runtime: DatabaseRuntime,
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    _, _, grants_before = await stored_state(runtime)

    result = await reconcile_document(
        runtime, handbook(SECOND, "v2"), readers=[ALICE], observed_at=LATER
    )

    document, chunks, grants = await stored_state(runtime)
    assert result.change is Change.CONTENT
    assert (document.content_version.number, document.authorization_version.number) == (
        2,
        1,
    )
    assert "Ask a month ahead." in chunks[0].text
    assert {chunk.content_version.number for chunk in chunks} == {2}
    assert grants == grants_before


@pytest.mark.integration
async def test_an_access_change_keeps_chunks_without_rechunking(
    runtime: DatabaseRuntime,
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    _, chunks_before, _ = await stored_state(runtime)

    result = await reconcile_document(
        runtime,
        handbook(FIRST, "v1"),
        readers=[ALICE, FINANCE],
        observed_at=LATER,
    )

    document, chunks, grants = await stored_state(runtime)
    assert result.change is Change.ACCESS
    assert (document.content_version.number, document.authorization_version.number) == (
        1,
        2,
    )
    assert chunks == chunks_before  # same chunk IDs: nothing was re-chunked
    assert {grant.subject for grant in grants} == {ALICE, FINANCE}


@pytest.mark.integration
async def test_a_failure_mid_publish_leaves_the_old_version_searchable(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    before = await stored_state(runtime)

    async def fail_after_chunks_were_written(*_: object, **__: object) -> None:
        # By now the new Document row and Chunks are written, uncommitted.
        raise RuntimeError("injected failure while replacing grants")

    monkeypatch.setattr(
        reconciliation, "replace_access_grants", fail_after_chunks_were_written
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        await reconcile_document(
            runtime,
            handbook(SECOND, "v2"),
            readers=[ALICE, FINANCE],
            observed_at=LATER,
        )

    assert await stored_state(runtime) == before


@pytest.mark.integration
async def test_readers_see_the_old_version_until_the_new_one_commits(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    _, old_chunks, _ = await stored_state(runtime)
    seen_mid_publish: list[Sequence[Chunk]] = []
    original = reconciliation.replace_access_grants

    async def look_from_another_connection(
        *args: object, **kwargs: object
    ) -> tuple[AccessGrant, ...]:
        _, chunks, _ = await stored_state(runtime)
        seen_mid_publish.append(chunks)
        return await original(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(
        reconciliation, "replace_access_grants", look_from_another_connection
    )
    await reconcile_document(
        runtime,
        handbook(SECOND, "v2"),
        readers=[ALICE, FINANCE],
        observed_at=LATER,
    )

    _, new_chunks, _ = await stored_state(runtime)
    assert seen_mid_publish == [old_chunks]
    assert new_chunks != old_chunks
    assert {chunk.content_version.number for chunk in new_chunks} == {2}


@pytest.mark.integration
async def test_an_older_observation_is_refused_and_changes_nothing(
    runtime: DatabaseRuntime,
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=LATER
    )
    before = await stored_state(runtime)

    with pytest.raises(InvalidDocument, match="observed_at"):
        await reconcile_document(
            runtime, handbook(SECOND, "v2"), readers=[ALICE], observed_at=OBSERVED_AT
        )

    assert await stored_state(runtime) == before


@pytest.mark.integration
async def test_a_document_id_never_changes_across_versions(
    runtime: DatabaseRuntime,
) -> None:
    first = await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    second = await reconcile_document(
        runtime, handbook(SECOND, "v2"), readers=[FINANCE], observed_at=LATER
    )

    document, _, _ = await stored_state(runtime)
    assert isinstance(document.document_id, DocumentId)
    assert first.document.document_id == second.document.document_id
    assert second.change is Change.CONTENT_AND_ACCESS
    assert document.document_id == first.document.document_id


@pytest.mark.integration
async def test_a_new_source_version_alone_updates_only_the_row(
    runtime: DatabaseRuntime,
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    _, chunks_before, grants_before = await stored_state(runtime)

    result = await reconcile_document(
        runtime, handbook(FIRST, "v2"), readers=[ALICE], observed_at=LATER
    )

    document, chunks, grants = await stored_state(runtime)
    assert result.change is Change.SOURCE_VERSION
    assert document.provenance.source_version == "v2"
    assert (chunks, grants) == (chunks_before, grants_before)


@pytest.mark.integration
async def test_a_tombstoned_document_that_returns_gets_its_chunks_back(
    runtime: DatabaseRuntime,
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    document, _, _ = await stored_state(runtime)
    gone = document.tombstone(observed_at=LATER)
    async with runtime.transaction() as connection:  # what M03-T13 will do
        await upsert_document(connection, gone)
        await replace_chunks(connection, document=gone, chunk_run=[])

    result = await reconcile_document(
        runtime,
        handbook(FIRST, "v1"),
        readers=[ALICE],
        observed_at=LATER + timedelta(hours=1),
    )

    restored, chunks, _ = await stored_state(runtime)
    assert result.change is Change.RESTORED
    assert restored.availability is DocumentAvailability.AVAILABLE
    assert [chunk.heading_path for chunk in chunks] == ["Team handbook"]


@pytest.mark.integration
async def test_losing_a_race_to_create_writes_under_the_stored_id(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )

    original = reconciliation.load_document
    lookups = 0

    async def not_found_the_first_time(*args: object, **kwargs: object) -> object:
        # Another worker created the row after this one first looked it up.
        nonlocal lookups
        lookups += 1
        if lookups == 1:
            return None
        return await original(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(reconciliation, "load_document", not_found_the_first_time)
    second = await reconcile_document(
        runtime, handbook(SECOND, "v2"), readers=[FINANCE], observed_at=LATER
    )

    document, chunks, grants = await stored_state(runtime)
    assert second.document.document_id == first.document.document_id
    assert document.document_id == first.document.document_id
    assert "Ask a month ahead." in chunks[0].text
    assert [grant.subject for grant in grants] == [FINANCE]


THIRD = "# Team handbook\n\nWelcome to the team.\n\n## Vacation\n\nAsk your lead.\n"


def pause_first_call(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> tuple[asyncio.Event, asyncio.Event]:
    """Make the first call to `reconciliation.<name>` wait until released.

    The run that makes that call is then paused inside its transaction,
    holding its locks, so a second run can be started against it.
    """
    original = getattr(reconciliation, name)
    paused, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def pausing(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            paused.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(reconciliation, name, pausing)
    return paused, release


async def race(
    paused: asyncio.Event,
    release: asyncio.Event,
    first: Coroutine[object, object, Reconciliation],
    second: Coroutine[object, object, Reconciliation],
) -> tuple[Reconciliation | BaseException, Reconciliation | BaseException]:
    """Start `first`, then `second` while `first` is paused; return both results."""
    first_task = asyncio.create_task(first)
    await paused.wait()
    second_task = asyncio.create_task(second)
    await asyncio.sleep(0.2)  # long enough for the second run to reach the lock
    release.set()
    return await asyncio.gather(first_task, second_task, return_exceptions=True)


@pytest.mark.integration
async def test_an_older_observation_cannot_overwrite_a_newer_concurrent_one(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    paused, release = pause_first_call(monkeypatch, "replace_chunks")

    newer, older = await race(
        paused,
        release,
        reconcile_document(
            runtime,
            handbook(SECOND, "v3"),
            readers=[ALICE],
            observed_at=LATER + timedelta(hours=1),
        ),
        reconcile_document(
            runtime, handbook(THIRD, "v2"), readers=[ALICE], observed_at=LATER
        ),
    )

    document, chunks, _ = await stored_state(runtime)
    assert not isinstance(newer, BaseException)
    assert isinstance(older, InvalidDocument)
    assert document.provenance.source_version == "v3"
    assert "Ask a month ahead." in chunks[0].text


@pytest.mark.integration
async def test_concurrent_content_and_access_changes_never_publish_a_mix(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    paused, release = pause_first_call(monkeypatch, "replace_chunks")

    content_run, access_run = await race(
        paused,
        release,
        reconcile_document(
            runtime, handbook(SECOND, "v2"), readers=[ALICE], observed_at=LATER
        ),
        reconcile_document(
            runtime,
            handbook(FIRST, "v1"),
            readers=[ALICE, FINANCE],
            observed_at=LATER + timedelta(minutes=1),
        ),
    )

    document, chunks, grants = await stored_state(runtime)
    assert not isinstance(content_run, BaseException)
    assert not isinstance(access_run, BaseException)
    assert {chunk.content_version for chunk in chunks} == {document.content_version}
    assert {chunk.content_fingerprint for chunk in chunks} == {
        document.content_fingerprint
    }
    assert {grant.subject for grant in grants} == {ALICE, FINANCE}


@pytest.mark.integration
async def test_two_runs_creating_one_document_leave_one_valid_row(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    paused, release = pause_first_call(monkeypatch, "replace_chunks")

    newer, older = await race(
        paused,
        release,
        reconcile_document(
            runtime, handbook(SECOND, "v2"), readers=[ALICE], observed_at=LATER
        ),
        reconcile_document(
            runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
        ),
    )

    document, chunks, _ = await stored_state(runtime)  # the row is still readable
    assert not isinstance(newer, BaseException)
    assert isinstance(older, InvalidDocument)
    assert document.created_at <= document.updated_at
    assert "Ask a month ahead." in chunks[0].text
