"""Real PostgreSQL tests for Chunk replacement."""

import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from knowledge_service.chunks import Chunk, StaleChunkWrite
from knowledge_service.documents import Document, DocumentProvenance
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.persistence import load_chunks, replace_chunks, upsert_document

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000020")

INSERT_SOURCE = text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


class Abandoned(Exception):
    """Raised mid-transaction to prove the replacement was never committed."""


async def replace_then_abandon(
    engine: AsyncEngine,
    *,
    document: Document,
    chunk_run: Sequence[Chunk],
) -> None:
    """Replace a run inside one transaction, then fail before it can commit."""
    async with engine.begin() as connection:
        await replace_chunks(connection, document=document, chunk_run=chunk_run)
        raise Abandoned


@pytest.fixture
def migrated_database(monkeypatch: pytest.MonkeyPatch) -> str:
    """Apply migrations synchronously; Alembic's env.py runs its own event loop."""
    database_url = os.environ.get("KNOWLEDGE_SERVICE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set KNOWLEDGE_SERVICE_TEST_DATABASE_URL to a disposable database")

    monkeypatch.setenv("KNOWLEDGE_SERVICE_DATABASE_URL", database_url)
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    return database_url


def make_document(*, observed_at: datetime = OBSERVED_AT) -> Document:
    return Document.register(
        document_id=DOCUMENT_ID,
        provenance=DocumentProvenance(
            source_id=SOURCE_ID,
            external_id="page-123",
            source_version="17",
        ),
        content_fingerprint="content-a",
        authorization_fingerprint="acl-a",
        observed_at=observed_at,
    )


def make_chunk(*, document: Document, ordinal: int, text: str) -> Chunk:
    return Chunk(
        chunk_id=ChunkId.new(),
        document_id=document.document_id,
        content_version=document.content_version,
        ordinal=ordinal,
        text=text,
        heading_path="Setup",
        token_count=8,
        content_fingerprint=document.content_fingerprint,
        source_anchor=f"#section-{ordinal}",
    )


@pytest.mark.integration
async def test_replacing_a_content_version_removes_stale_chunks_in_order(
    migrated_database: str,
) -> None:
    engine = create_async_engine(
        make_url(migrated_database).set(drivername="postgresql+psycopg")
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM chunks"))
            await connection.execute(text("DELETE FROM documents"))
            await connection.execute(text("DELETE FROM sources"))
            await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})

        document_v1 = make_document()
        async with engine.begin() as connection:
            await upsert_document(connection, document_v1)
            await replace_chunks(
                connection,
                document=document_v1,
                chunk_run=[
                    make_chunk(document=document_v1, ordinal=0, text="First"),
                    make_chunk(document=document_v1, ordinal=1, text="Second"),
                    make_chunk(document=document_v1, ordinal=2, text="Third"),
                ],
            )

        document_v2 = document_v1.reconcile(
            content_fingerprint="content-b",
            authorization_fingerprint="acl-a",
            source_version="18",
            observed_at=OBSERVED_AT + timedelta(minutes=1),
        )
        with pytest.raises(Abandoned):
            await replace_then_abandon(
                engine,
                document=document_v2,
                chunk_run=[make_chunk(document=document_v2, ordinal=0, text="Lost")],
            )

        async with engine.begin() as connection:
            stored = await load_chunks(connection, document_id=DOCUMENT_ID)
        assert [chunk.text for chunk in stored] == ["First", "Second", "Third"]

        async with engine.begin() as connection:
            await upsert_document(connection, document_v2)
            await replace_chunks(
                connection,
                document=document_v2,
                chunk_run=[
                    make_chunk(document=document_v2, ordinal=0, text="First"),
                    make_chunk(
                        document=document_v2, ordinal=1, text="Second and third"
                    ),
                ],
            )
            stored = await load_chunks(connection, document_id=DOCUMENT_ID)
        assert [chunk.ordinal for chunk in stored] == [0, 1]
        assert [chunk.text for chunk in stored] == ["First", "Second and third"]
        assert {chunk.content_version.number for chunk in stored} == {2}

        async with engine.begin() as connection:
            with pytest.raises(StaleChunkWrite):
                await replace_chunks(
                    connection,
                    document=document_v1,
                    chunk_run=[
                        make_chunk(document=document_v1, ordinal=0, text="Stale")
                    ],
                )
            stored = await load_chunks(connection, document_id=DOCUMENT_ID)
        assert [chunk.text for chunk in stored] == ["First", "Second and third"]
    finally:
        await engine.dispose()
