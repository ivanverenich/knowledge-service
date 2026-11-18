"""Real PostgreSQL tests for one complete pass over a local Source."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy import text

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import SourceId
from knowledge_service.identity.context import group_id_for, user_id_for
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.grant_normalization import (
    PrincipalDirectory,
    UnresolvedPrincipal,
    UnresolvedReason,
)
from knowledge_service.ingestion.normalization import QuarantineReason
from knowledge_service.ingestion.reconciliation import Change
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourceAccess,
    SourceMetadata,
    SourcePage,
    SourceRecord,
    SourceUnavailable,
)
from knowledge_service.ingestion.synchronization import (
    SynchronizationSummary,
    synchronize_source,
)
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.chunks import load_searchable_chunks
from knowledge_service.persistence.schema import access_grants, chunks, documents
from knowledge_service.settings import Settings

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
OBSERVED_AT = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)

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


async def searchable_text(runtime: DatabaseRuntime) -> str:
    async with runtime.transaction() as connection:
        chunks = await load_searchable_chunks(connection, source_id=SOURCE_ID)
    return "\n".join(chunk.text for chunk in chunks)


@pytest.mark.integration
async def test_a_file_removed_from_the_folder_is_tombstoned(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    (tmp_path / "old-policy.md").write_text("# Old policy\n\nRetired rules.\n")
    (tmp_path / "empty.md").write_text("")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))

    first = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        page_size=1,
        principal_directory=None,
    )
    (tmp_path / "old-policy.md").unlink()
    second = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=LATER,
        page_size=1,
        principal_directory=None,
    )

    assert first.changes == {Change.CREATED: 2}
    assert [q.reason for q in first.quarantined] == [QuarantineReason.EMPTY_CONTENT]
    assert first.tombstones == ()
    assert second.changes == {Change.UNCHANGED: 1}
    assert [t.external_id for t in second.tombstones] == ["old-policy.md"]
    searchable = await searchable_text(runtime)
    assert "Welcome aboard." in searchable
    assert "Retired rules." not in searchable


class FailingSecondPage:
    """A Source whose second page cannot be fetched."""

    def __init__(self, first: DirectorySource) -> None:
        self._first = first

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        if checkpoint is not None:
            raise SourceUnavailable("the source went away")
        return await self._first.fetch_page(checkpoint=None, page_size=page_size)


@pytest.mark.integration
async def test_a_failed_pass_tombstones_nothing(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "a.md").write_text("# A\n\nFirst file.\n")
    (tmp_path / "b.md").write_text("# B\n\nSecond file.\n")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )

    with pytest.raises(SourceUnavailable):
        await synchronize_source(
            runtime,
            FailingSecondPage(source),
            source_id=SOURCE_ID,
            observed_at=LATER,
            page_size=1,
            principal_directory=None,
        )

    searchable = await searchable_text(runtime)
    assert "First file." in searchable
    assert "Second file." in searchable


@pytest.mark.integration
async def test_a_private_document_is_stored_but_readable_by_nobody(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "salaries.md").write_text("# Salaries\n\nConfidential.\n")
    source = DirectorySource(
        tmp_path, access=SourceAccess(public=False, principals=("hr",))
    )

    summary = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )

    assert summary.changes == {Change.CREATED: 1}
    assert [item.principals for item in summary.unresolved] == [
        (UnresolvedPrincipal("hr", UnresolvedReason.MALFORMED),)
    ]
    async with runtime.transaction() as connection:
        grants = (
            await connection.execute(text("SELECT count(*) FROM access_grants"))
        ).scalar()
    assert grants == 0


ISSUER = "http://localhost:8180/realms/knowledge-service"
CAROL = "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a03"
DIRECTORY = PrincipalDirectory(
    issuer=ISSUER,
    users={"carol@example.test": CAROL},
    groups={"engineering": "/engineering"},
)


async def readable_by(runtime: DatabaseRuntime, reader: AuthorizationContext) -> int:
    """How many Chunks retrieval's own eligibility check lets `reader` see."""
    statement = (
        sa.select(sa.func.count())
        .select_from(
            chunks.join(documents, chunks.c.document_id == documents.c.document_id)
        )
        .where(eligible_chunks_predicate(reader))
    )
    async with runtime.transaction() as connection:
        return (await connection.execute(statement)).scalar_one()


@pytest.mark.integration
async def test_mapped_principals_become_grants_and_the_rest_are_reported(
    runtime: DatabaseRuntime, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "roadmap.md").write_text("# Roadmap\n\nEngineering only.\n")
    principals = (
        "group:contractors",
        "group:engineering",
        "hr",
        "user:carol@example.test",
    )
    source = DirectorySource(
        tmp_path, access=SourceAccess(public=False, principals=principals)
    )

    summary = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )

    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(access_grants.c.subject_kind, access_grants.c.subject_id)
            )
        ).all()
    assert sorted((kind, str(subject)) for kind, subject in rows) == [
        ("group", str(group_id_for(ISSUER, "/engineering").value)),
        ("user", str(user_id_for(ISSUER, CAROL).value)),
    ]
    assert [item.external_id for item in summary.unresolved] == ["roadmap.md"]
    assert [item.principals for item in summary.unresolved] == [
        (
            UnresolvedPrincipal("group:contractors", UnresolvedReason.UNKNOWN),
            UnresolvedPrincipal("hr", UnresolvedReason.MALFORMED),
        )
    ]
    assert "unresolved source principals: 2 in 1 documents" in caplog.messages
    assert "contractors" not in caplog.text


@pytest.mark.integration
async def test_retrieval_lets_only_the_mapped_readers_see_the_document(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "roadmap.md").write_text("# Roadmap\n\nEngineering only.\n")
    source = DirectorySource(
        tmp_path,
        access=SourceAccess(public=False, principals=("group:engineering",)),
    )
    await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )
    alice = AuthorizationContext(
        user_id=user_id_for(ISSUER, "alice"),
        group_ids=frozenset({group_id_for(ISSUER, "/engineering")}),
    )
    bob = AuthorizationContext(
        user_id=user_id_for(ISSUER, "bob"),
        group_ids=frozenset({group_id_for(ISSUER, "/finance")}),
    )

    assert await readable_by(runtime, alice) > 0
    assert await readable_by(runtime, bob) == 0


@pytest.mark.integration
async def test_mapping_a_principal_later_opens_the_document_on_the_next_pass(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "roadmap.md").write_text("# Roadmap\n\nEngineering only.\n")
    source = DirectorySource(
        tmp_path,
        access=SourceAccess(public=False, principals=("group:engineering",)),
    )
    alice = AuthorizationContext(
        user_id=user_id_for(ISSUER, "alice"),
        group_ids=frozenset({group_id_for(ISSUER, "/engineering")}),
    )

    first = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    hidden = await readable_by(runtime, alice)
    second = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=DIRECTORY,
    )

    assert (hidden, len(first.unresolved), len(second.unresolved)) == (0, 1, 0)
    assert await readable_by(runtime, alice) > 0


@pytest.mark.integration
async def test_a_file_that_stops_parsing_keeps_its_last_good_version(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    (tmp_path / "handbook.md").write_bytes(b"\xff\xfe broken")

    summary = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=None,
    )

    assert [q.reason for q in summary.quarantined] == [
        QuarantineReason.UNDECODABLE_TEXT
    ]
    assert summary.tombstones == ()
    assert "Welcome aboard." in await searchable_text(runtime)


class ReportsDeletion:
    """A Source that reports one item deleted instead of leaving it out."""

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        return SourcePage(
            records=(SourceRecord.deleted(external_id="handbook.md", version="v2"),),
            next_checkpoint=None,
        )


@pytest.mark.integration
async def test_an_item_reported_deleted_is_tombstoned(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )

    summary = await synchronize_source(
        runtime,
        ReportsDeletion(),
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=None,
    )

    assert [t.external_id for t in summary.tombstones] == ["handbook.md"]
    assert await searchable_text(runtime) == ""


class OffersThenDeletes:
    """A Source that offers an item on page one and reports it deleted on page two."""

    def __init__(self, first: DirectorySource) -> None:
        self._first = first

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        if checkpoint is None:
            page = await self._first.fetch_page(checkpoint=None, page_size=page_size)
            return SourcePage(records=page.records, next_checkpoint="page-2")
        return SourcePage(
            records=(SourceRecord.deleted(external_id="handbook.md", version="v2"),),
            next_checkpoint=None,
        )


@pytest.mark.integration
async def test_the_last_report_of_an_item_in_a_pass_wins(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    source = OffersThenDeletes(
        DirectorySource(tmp_path, access=SourceAccess(public=True))
    )

    summary = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )

    assert [t.external_id for t in summary.tombstones] == ["handbook.md"]
    assert await searchable_text(runtime) == ""


class StuckCheckpoint:
    """A Source that always says there is one more page after "0"."""

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        return SourcePage(records=(), next_checkpoint="0")


@pytest.mark.integration
async def test_a_repeated_checkpoint_is_refused(runtime: DatabaseRuntime) -> None:
    with pytest.raises(InvalidSource, match="repeated a checkpoint"):
        await synchronize_source(
            runtime,
            StuckCheckpoint(),
            source_id=SOURCE_ID,
            observed_at=OBSERVED_AT,
            principal_directory=None,
        )


def broken(path: Path) -> None:
    """Make the file one normalization quarantines."""
    path.write_bytes(b"\xff\xfe broken")


ALICE = AuthorizationContext(
    user_id=user_id_for(ISSUER, "alice"),
    group_ids=frozenset({group_id_for(ISSUER, "/engineering")}),
)
BOB = AuthorizationContext(
    user_id=user_id_for(ISSUER, "bob"),
    group_ids=frozenset({group_id_for(ISSUER, "/finance")}),
)
ANYONE = AuthorizationContext(user_id=user_id_for(ISSUER, "anyone"))


async def sync(
    runtime: DatabaseRuntime, root: Path, access: SourceAccess, at: datetime
) -> SynchronizationSummary:
    return await synchronize_source(
        runtime,
        DirectorySource(root, access=access),
        source_id=SOURCE_ID,
        observed_at=at,
        principal_directory=DIRECTORY,
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("before", "after", "loses"),
    [
        (("group:engineering",), ("user:carol@example.test",), ALICE),
        ((), ("group:engineering",), ANYONE),
    ],
    ids=["group-removed", "public-made-private"],
)
async def test_a_reader_removed_while_the_file_is_broken_loses_it_at_once(
    runtime: DatabaseRuntime,
    tmp_path: Path,
    before: tuple[str, ...],
    after: tuple[str, ...],
    loses: AuthorizationContext,
) -> None:
    (tmp_path / "roadmap.md").write_text("# Roadmap\n\nEngineering only.\n")
    access = SourceAccess(public=not before, principals=before)
    await sync(runtime, tmp_path, access, OBSERVED_AT)
    assert await readable_by(runtime, loses) > 0
    broken(tmp_path / "roadmap.md")

    summary = await sync(
        runtime, tmp_path, SourceAccess(public=False, principals=after), LATER
    )

    assert len(summary.quarantined) == 1
    assert summary.changes == {Change.ACCESS: 1}
    assert await readable_by(runtime, loses) == 0


@pytest.mark.integration
async def test_a_reader_added_while_the_file_is_broken_waits_for_a_good_version(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    path = tmp_path / "roadmap.md"
    path.write_text("# Roadmap\n\nEngineering only.\n")
    engineering = SourceAccess(public=False, principals=("group:engineering",))
    await sync(runtime, tmp_path, engineering, OBSERVED_AT)
    broken(path)
    wider = SourceAccess(public=True)

    await sync(runtime, tmp_path, wider, LATER)
    hidden_from_bob = await readable_by(runtime, BOB)
    path.write_text("# Roadmap\n\nEveryone now.\n")
    await sync(runtime, tmp_path, wider, LATER + timedelta(hours=1))

    assert (hidden_from_bob, await readable_by(runtime, ALICE) > 0) == (0, True)
    assert await readable_by(runtime, BOB) > 0


@pytest.mark.integration
async def test_access_narrowed_while_broken_is_restored_by_a_good_version(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    path = tmp_path / "roadmap.md"
    path.write_text("# Roadmap\n\nEngineering only.\n")
    engineering = SourceAccess(public=False, principals=("group:engineering",))
    await sync(runtime, tmp_path, engineering, OBSERVED_AT)
    broken(path)
    await sync(
        runtime,
        tmp_path,
        SourceAccess(public=False, principals=("user:carol@example.test",)),
        LATER,
    )
    path.write_text("# Roadmap\n\nEngineering only.\n")

    summary = await sync(runtime, tmp_path, engineering, LATER + timedelta(hours=1))

    # The stored fingerprint names the narrowed readers, so this is a change.
    assert summary.changes == {Change.ACCESS: 1}
    assert await readable_by(runtime, ALICE) > 0


class OffersTwice:
    """A Source that offers one item twice in a pass, with different access."""

    def __init__(self, first: SourceAccess, last: SourceAccess) -> None:
        self.accesses = (first, last)

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        index = 0 if checkpoint is None else 1
        record = SourceRecord.present(
            external_id="roadmap.md",
            version=f"v{index}",
            media_type="text/markdown",
            content=b"# Roadmap\n\nEngineering only.\n",
            access=self.accesses[index],
            metadata=SourceMetadata(
                title="roadmap", modified_at=OBSERVED_AT, size_bytes=30
            ),
        )
        return SourcePage(
            records=(record,), next_checkpoint="page-2" if index == 0 else None
        )


@pytest.mark.integration
async def test_a_record_offered_twice_is_reported_as_last_seen(
    runtime: DatabaseRuntime,
) -> None:
    source = OffersTwice(
        SourceAccess(public=False, principals=("group:contractors",)),
        SourceAccess(public=False, principals=("group:engineering",)),
    )

    summary = await synchronize_source(
        runtime,
        source,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )

    assert summary.unresolved == ()
