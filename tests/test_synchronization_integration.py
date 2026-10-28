"""Real PostgreSQL tests for one complete pass over a local Source."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.normalization import QuarantineReason
from knowledge_service.ingestion.reconciliation import Change
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourceAccess,
    SourcePage,
    SourceRecord,
    SourceUnavailable,
)
from knowledge_service.ingestion.synchronization import synchronize_source
from knowledge_service.persistence.chunks import load_searchable_chunks
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
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT, page_size=1
    )
    (tmp_path / "old-policy.md").unlink()
    second = await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=LATER, page_size=1
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
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )

    with pytest.raises(SourceUnavailable):
        await synchronize_source(
            runtime,
            FailingSecondPage(source),
            source_id=SOURCE_ID,
            observed_at=LATER,
            page_size=1,
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
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )

    assert summary.changes == {Change.CREATED: 1}
    async with runtime.transaction() as connection:
        grants = (
            await connection.execute(text("SELECT count(*) FROM access_grants"))
        ).scalar()
    assert grants == 0


@pytest.mark.integration
async def test_a_file_that_stops_parsing_keeps_its_last_good_version(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )
    (tmp_path / "handbook.md").write_bytes(b"\xff\xfe broken")

    summary = await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=LATER
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
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )

    summary = await synchronize_source(
        runtime, ReportsDeletion(), source_id=SOURCE_ID, observed_at=LATER
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
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
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
            runtime, StuckCheckpoint(), source_id=SOURCE_ID, observed_at=OBSERVED_AT
        )
