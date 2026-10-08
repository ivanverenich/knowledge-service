"""Real PostgreSQL tests for Document persistence."""

from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from knowledge_service.documents import (
    AuthorizationVersion,
    ContentVersion,
    Document,
    DocumentAvailability,
    DocumentProvenance,
)
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.persistence import documents, load_document, upsert_document

OBSERVED_AT = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
EXTERNAL_ID = "page-123"


def make_document(
    *, document_id: DocumentId, observed_at: datetime = OBSERVED_AT
) -> Document:
    return Document.register(
        document_id=document_id,
        provenance=DocumentProvenance(
            source_id=SOURCE_ID,
            external_id=EXTERNAL_ID,
            source_version="17",
        ),
        content_fingerprint="content-a",
        authorization_fingerprint="acl-a",
        observed_at=observed_at,
    )


@pytest.mark.integration
async def test_upsert_is_idempotent_and_versions_move_independently(
    migrated_database: str,
) -> None:
    engine = create_async_engine(
        make_url(migrated_database).set(drivername="postgresql+psycopg")
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO sources (source_id, kind, location, display_name,"
                    " enabled, created_at, updated_at) VALUES (:source_id,"
                    " 'local_directory', '/srv/handbook', 'Handbook', true, now(),"
                    " now())"
                ),
                {"source_id": SOURCE_ID.value},
            )

        registered = make_document(document_id=DocumentId.new())

        async with engine.begin() as connection:
            assert (
                await upsert_document(connection, registered) == registered.document_id
            )

        async with engine.begin() as connection:
            assert (
                await upsert_document(connection, registered) == registered.document_id
            )
            stored_rows = await connection.scalar(
                sa.select(sa.func.count()).select_from(documents)
            )
            assert stored_rows == 1
            stored = await load_document(
                connection, source_id=SOURCE_ID, external_id=EXTERNAL_ID
            )
            assert stored == registered

        async with engine.begin() as connection:
            re_registered = make_document(
                document_id=DocumentId.new(),
                observed_at=OBSERVED_AT + timedelta(minutes=1),
            )
            assert (
                await upsert_document(connection, re_registered)
                == registered.document_id
            )
            stored = await load_document(
                connection, source_id=SOURCE_ID, external_id=EXTERNAL_ID
            )
            assert stored is not None
            assert stored.document_id == registered.document_id
            assert stored.created_at == registered.created_at

        content_changed = registered.reconcile(
            content_fingerprint="content-b",
            authorization_fingerprint="acl-a",
            source_version="18",
            observed_at=OBSERVED_AT + timedelta(minutes=2),
        )
        async with engine.begin() as connection:
            await upsert_document(connection, content_changed)
            stored = await load_document(
                connection, source_id=SOURCE_ID, external_id=EXTERNAL_ID
            )
        assert stored is not None
        assert stored.content_version == ContentVersion(2)
        assert stored.authorization_version == AuthorizationVersion(1)

        authorization_changed = content_changed.reconcile(
            content_fingerprint="content-b",
            authorization_fingerprint="acl-b",
            source_version="19",
            observed_at=OBSERVED_AT + timedelta(minutes=3),
        )
        async with engine.begin() as connection:
            await upsert_document(connection, authorization_changed)
            stored = await load_document(
                connection, source_id=SOURCE_ID, external_id=EXTERNAL_ID
            )
        assert stored is not None
        assert stored.content_version == ContentVersion(2)
        assert stored.authorization_version == AuthorizationVersion(2)

        tombstoned = authorization_changed.tombstone(
            observed_at=OBSERVED_AT + timedelta(minutes=4)
        )
        async with engine.begin() as connection:
            await upsert_document(connection, tombstoned)
            stored = await load_document(
                connection, source_id=SOURCE_ID, external_id=EXTERNAL_ID
            )
        assert stored is not None
        assert stored.availability is DocumentAvailability.TOMBSTONED
    finally:
        await engine.dispose()
