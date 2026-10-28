"""Real PostgreSQL tests for carrying a Document's title through the pipeline."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from database_fixtures import migration_config, migration_environment
from knowledge_service.access import Subject
from knowledge_service.chunks import Chunk
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import Document
from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import Change, reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.ingestion.synchronization import synchronize_source
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.documents import load_document
from knowledge_service.settings import Settings

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
OBSERVED_AT = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)
HANDBOOK = b"# Team handbook\n\nWelcome to the team.\n"

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


def titled(title: str) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id="pages/42",
            version="v1",
            media_type="text/markdown",
            content=HANDBOOK,
            access=SourceAccess(public=True),
            metadata=SourceMetadata(
                title=title, modified_at=OBSERVED_AT, size_bytes=len(HANDBOOK)
            ),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def stored(
    runtime: DatabaseRuntime, external_id: str
) -> tuple[Document, tuple[Chunk, ...]]:
    async with runtime.transaction() as connection:
        document = await load_document(
            connection, source_id=SOURCE_ID, external_id=external_id
        )
        assert document is not None
        chunks = await load_chunks(connection, document_id=document.document_id)
    return document, chunks


@pytest.mark.integration
async def test_a_synchronized_file_keeps_its_title_and_a_second_pass_writes_nothing(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_bytes(HANDBOOK)
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )
    before = await stored(runtime, "handbook.md")

    second = await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=LATER
    )

    assert before[0].title == "handbook"
    assert second.changes == {Change.UNCHANGED: 1}
    assert await stored(runtime, "handbook.md") == before


@pytest.mark.integration
async def test_a_new_title_updates_only_the_row(runtime: DatabaseRuntime) -> None:
    first = await reconcile_document(
        runtime, titled("Handbook"), readers=[Subject.public()], observed_at=OBSERVED_AT
    )
    _, chunks_before = await stored(runtime, "pages/42")

    renamed = await reconcile_document(
        runtime,
        titled("Team handbook"),
        readers=[Subject.public()],
        observed_at=LATER,
    )

    document, chunks_after = await stored(runtime, "pages/42")
    assert renamed.change is Change.TITLE
    assert document.title == "Team handbook"
    assert document.document_id == first.document.document_id
    assert document.provenance == first.document.provenance
    assert document.content_version == first.document.content_version
    assert document.authorization_version == first.document.authorization_version
    assert [c.chunk_id for c in chunks_after] == [c.chunk_id for c in chunks_before]


INSERT_OLD_DOCUMENT = text(
    "INSERT INTO documents (document_id, source_id, external_id, source_version,"
    " content_version, content_fingerprint, authorization_version,"
    " authorization_fingerprint, availability, created_at, updated_at)"
    " VALUES (gen_random_uuid(), :source_id, :external_id, 'v1', 1, 'c', 1, 'a',"
    " 'available', now(), now())"
)


@pytest.mark.integration
def test_documents_stored_before_titles_get_a_valid_placeholder(
    migrated_database: str,
) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    # Each of these starts with 200 characters that Python's strip() removes.
    external_ids = (
        "guides/handbook.md",
        " " * 200 + "x.md",
        "\u3000" * 200 + "x.md",
        "\x1f" * 200 + "x.md",
    )
    with migration_environment(url):
        command.downgrade(config, "c1d2e3f4a5b6")
    engine = create_engine(migrated_database)
    try:
        with engine.begin() as connection:
            connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
            for external_id in external_ids:
                connection.execute(
                    INSERT_OLD_DOCUMENT,
                    {"source_id": SOURCE_ID.value, "external_id": external_id},
                )
        with migration_environment(url):
            command.upgrade(config, "head")
    finally:
        engine.dispose()

    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )

    async def load_all() -> list[Document | None]:
        async with runtime.transaction() as connection:
            return [
                await load_document(
                    connection, source_id=SOURCE_ID, external_id=external_id
                )
                for external_id in external_ids
            ]

    try:
        loaded = asyncio.run(load_all())
    finally:
        asyncio.run(runtime.dispose())
    assert [d.title if d else None for d in loaded] == ["Untitled"] * 4
