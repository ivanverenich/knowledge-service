"""Real PostgreSQL tests for transaction ownership while publishing."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from knowledge_service.access import AccessGrant, Subject
from knowledge_service.chunks import Chunk, StaleChunkWrite
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import Document, DocumentProvenance
from knowledge_service.identifiers import (
    AccessGrantId,
    ChunkId,
    DocumentId,
    SourceId,
    UserId,
)
from knowledge_service.persistence import load_chunks, load_document
from knowledge_service.publishing import (
    Publication,
    publish_document,
    publish_documents,
)
from knowledge_service.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
USER_ID = UserId.parse("00000000-0000-0000-0000-000000000030")

INSERT_SOURCE = text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


@pytest.fixture
def migrated_database(monkeypatch: pytest.MonkeyPatch) -> str:
    """Apply migrations synchronously; Alembic's env.py runs its own event loop."""
    database_url = os.environ.get("KNOWLEDGE_SERVICE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set KNOWLEDGE_SERVICE_TEST_DATABASE_URL to a disposable database")

    monkeypatch.setenv("KNOWLEDGE_SERVICE_DATABASE_URL", database_url)
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    return database_url


def registered_document(external_id: str, *, content_fingerprint: str) -> Document:
    return Document.register(
        document_id=DocumentId.new(),
        provenance=DocumentProvenance(
            source_id=SOURCE_ID, external_id=external_id, source_version="1"
        ),
        content_fingerprint=content_fingerprint,
        authorization_fingerprint="acl-a",
        observed_at=OBSERVED_AT,
    )


def publication_for(document: Document, *, text_body: str) -> Publication:
    return Publication(
        document=document,
        chunks=(
            Chunk(
                chunk_id=ChunkId.new(),
                document_id=document.document_id,
                content_version=document.content_version,
                ordinal=0,
                text=text_body,
                heading_path="Setup",
                token_count=4,
                content_fingerprint=document.content_fingerprint,
                source_anchor="#setup",
            ),
        ),
        grants=(
            AccessGrant(
                grant_id=AccessGrantId.new(),
                document_id=document.document_id,
                subject=Subject.user(USER_ID),
                granted_at=OBSERVED_AT,
            ),
        ),
    )


async def start_from_an_empty_database(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(text("DELETE FROM message_feedback"))
        await connection.execute(text("DELETE FROM messages"))
        await connection.execute(text("DELETE FROM conversations"))
        await connection.execute(text("DELETE FROM access_grants"))
        await connection.execute(text("DELETE FROM chunks"))
        await connection.execute(text("DELETE FROM documents"))
        await connection.execute(text("DELETE FROM sources"))
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})


@pytest.mark.integration
async def test_a_failed_publish_leaves_no_half_published_document(
    migrated_database: str,
) -> None:
    runtime = create_database_runtime(Settings())
    try:
        await start_from_an_empty_database(runtime)

        first = registered_document("page-a", content_fingerprint="content-a")
        second = first.reconcile(
            content_fingerprint="content-b",
            authorization_fingerprint="acl-a",
            source_version="2",
            observed_at=OBSERVED_AT + timedelta(minutes=1),
        )
        elsewhere = registered_document("page-b", content_fingerprint="content-b")

        published = await publish_documents(
            runtime,
            publications=[
                publication_for(first, text_body="First version."),
                publication_for(second, text_body="Second version."),
            ],
        )
        assert published == (first.document_id, first.document_id)

        with pytest.raises(StaleChunkWrite):
            await publish_documents(
                runtime,
                publications=[
                    publication_for(first, text_body="Redelivered."),
                    publication_for(elsewhere, text_body="Never published."),
                ],
            )

        async with runtime.transaction() as connection:
            stored = await load_document(
                connection, source_id=SOURCE_ID, external_id="page-a"
            )
            chunks = await load_chunks(connection, document_id=first.document_id)
            absent = await load_document(
                connection, source_id=SOURCE_ID, external_id="page-b"
            )

        assert stored is not None
        assert stored.content_version.number == 2
        assert [chunk.text for chunk in chunks] == ["Second version."]
        assert {chunk.content_version.number for chunk in chunks} == {2}
        assert absent is None
    finally:
        await runtime.dispose()


@pytest.mark.integration
async def test_an_uncommitted_publication_is_invisible_until_it_commits(
    migrated_database: str,
) -> None:
    runtime = create_database_runtime(Settings())
    try:
        await start_from_an_empty_database(runtime)

        first = registered_document("page-a", content_fingerprint="content-a")
        await publish_documents(
            runtime, publications=[publication_for(first, text_body="First version.")]
        )

        upgraded = first.reconcile(
            content_fingerprint="content-b",
            authorization_fingerprint="acl-a",
            source_version="2",
            observed_at=OBSERVED_AT + timedelta(minutes=1),
        )
        async with runtime.transaction() as connection:
            await publish_document(
                connection, publication_for(upgraded, text_body="Second version.")
            )
            async with runtime.transaction() as observer:
                during = await load_document(
                    observer, source_id=SOURCE_ID, external_id="page-a"
                )
                chunks_during = await load_chunks(
                    observer, document_id=first.document_id
                )

            assert during is not None
            assert during.content_version.number == 1
            assert [chunk.text for chunk in chunks_during] == ["First version."]

        async with runtime.transaction() as connection:
            after = await load_document(
                connection, source_id=SOURCE_ID, external_id="page-a"
            )
            chunks_after = await load_chunks(connection, document_id=first.document_id)

        assert after is not None
        assert after.content_version.number == 2
        assert [chunk.text for chunk in chunks_after] == ["Second version."]
    finally:
        await runtime.dispose()
