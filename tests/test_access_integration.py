"""Real PostgreSQL tests for Access Grants."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from knowledge_service.access import AccessGrant, Subject
from knowledge_service.documents import Document, DocumentProvenance
from knowledge_service.identifiers import (
    AccessGrantId,
    DocumentId,
    GroupId,
    SourceId,
    UserId,
)
from knowledge_service.persistence import (
    load_access_grants,
    replace_access_grants,
    upsert_document,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
USER_ID = UserId.parse("00000000-0000-0000-0000-000000000030")
GROUP_ID = GroupId.parse("00000000-0000-0000-0000-000000000040")

INSERT_SOURCE = text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)

INSERT_RAW_GRANT = text(
    "INSERT INTO access_grants (grant_id, document_id, subject_kind, subject_id,"
    " granted_at) VALUES (:grant_id, :document_id, :subject_kind, :subject_id,"
    " now())"
)

ACCESS_CLASSIFICATION = text(
    """
    SELECT d.document_id,
           CASE
               WHEN NOT EXISTS (
                   SELECT 1 FROM access_grants g WHERE g.document_id = d.document_id
               ) THEN 'none'
               WHEN EXISTS (
                   SELECT 1 FROM access_grants g
                   WHERE g.document_id = d.document_id
                     AND g.subject_kind = 'public'
               ) THEN 'public'
               WHEN EXISTS (
                   SELECT 1 FROM access_grants g
                   WHERE g.document_id = d.document_id
                     AND g.subject_kind = 'group'
               ) THEN 'group'
               ELSE 'user'
           END AS access_class
    FROM documents d
    """
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


def make_document(external_id: str) -> Document:
    return Document.register(
        document_id=DocumentId.new(),
        provenance=DocumentProvenance(
            source_id=SOURCE_ID,
            external_id=external_id,
            source_version="17",
        ),
        content_fingerprint=f"content-{external_id}",
        authorization_fingerprint=f"acl-{external_id}",
        observed_at=OBSERVED_AT,
    )


def make_grant(document: Document, subject: Subject) -> AccessGrant:
    return AccessGrant(
        grant_id=AccessGrantId.new(),
        document_id=document.document_id,
        subject=subject,
        granted_at=OBSERVED_AT,
    )


async def access_classes(connection: AsyncConnection) -> dict[str, str]:
    rows = (await connection.execute(ACCESS_CLASSIFICATION)).mappings().all()
    return {str(row["document_id"]): str(row["access_class"]) for row in rows}


@pytest.mark.integration
async def test_queries_tell_public_user_group_and_no_access_apart(
    migrated_database: str,
) -> None:
    engine = create_async_engine(
        make_url(migrated_database).set(drivername="postgresql+psycopg")
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM access_grants"))
            await connection.execute(text("DELETE FROM chunks"))
            await connection.execute(text("DELETE FROM documents"))
            await connection.execute(text("DELETE FROM sources"))
            await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})

        public = make_document("public-page")
        user_only = make_document("user-page")
        group_only = make_document("group-page")
        secret = make_document("secret-page")

        async with engine.begin() as connection:
            for document in (public, user_only, group_only, secret):
                await upsert_document(connection, document)
            await replace_access_grants(
                connection,
                document_id=public.document_id,
                grant_run=[make_grant(public, Subject.public())],
            )
            await replace_access_grants(
                connection,
                document_id=user_only.document_id,
                grant_run=[make_grant(user_only, Subject.user(USER_ID))],
            )
            await replace_access_grants(
                connection,
                document_id=group_only.document_id,
                grant_run=[make_grant(group_only, Subject.group(GROUP_ID))],
            )

        async with engine.begin() as connection:
            classes = await access_classes(connection)
            stored = await load_access_grants(
                connection, document_id=group_only.document_id
            )

        assert classes[str(public.document_id.value)] == "public"
        assert classes[str(user_only.document_id.value)] == "user"
        assert classes[str(group_only.document_id.value)] == "group"
        assert classes[str(secret.document_id.value)] == "none"
        assert [grant.subject for grant in stored] == [Subject.group(GROUP_ID)]

        async with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    INSERT_RAW_GRANT,
                    {
                        "grant_id": AccessGrantId.new().value,
                        "document_id": user_only.document_id.value,
                        "subject_kind": "public",
                        "subject_id": USER_ID.value,
                    },
                )

        async with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    INSERT_RAW_GRANT,
                    {
                        "grant_id": AccessGrantId.new().value,
                        "document_id": user_only.document_id.value,
                        "subject_kind": "user",
                        "subject_id": None,
                    },
                )

        async with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    INSERT_RAW_GRANT,
                    {
                        "grant_id": AccessGrantId.new().value,
                        "document_id": public.document_id.value,
                        "subject_kind": "public",
                        "subject_id": None,
                    },
                )

        async with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    INSERT_RAW_GRANT,
                    {
                        "grant_id": AccessGrantId.new().value,
                        "document_id": user_only.document_id.value,
                        "subject_kind": "team",
                        "subject_id": USER_ID.value,
                    },
                )
    finally:
        await engine.dispose()
