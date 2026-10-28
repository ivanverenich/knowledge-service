"""Store and load Documents and the metadata retrieval filters by."""

from collections.abc import Mapping
from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.documents import (
    AuthorizationVersion,
    ContentVersion,
    Document,
    DocumentAvailability,
    DocumentMetadata,
    DocumentProvenance,
    InvalidDocument,
)
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.jobs import JobKind, JobStatus
from knowledge_service.language import Language
from knowledge_service.persistence.schema import (
    chunks,
    document_metadata,
    documents,
    jobs,
)


def as_row(document: Document) -> dict[str, object]:
    """Translate a domain Document into the columns that store it."""
    return {
        "document_id": document.document_id.value,
        "source_id": document.provenance.source_id.value,
        "external_id": document.provenance.external_id,
        "source_version": document.provenance.source_version,
        "title": document.title,
        "content_version": document.content_version.number,
        "content_fingerprint": document.content_fingerprint,
        "authorization_version": document.authorization_version.number,
        "authorization_fingerprint": document.authorization_fingerprint,
        "availability": document.availability.value,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }


def as_document(record: Mapping[str, object]) -> Document:
    """Rebuild a domain Document from a stored row."""
    try:
        availability = DocumentAvailability(cast(str, record["availability"]))
    except ValueError:
        raise InvalidDocument("stored availability is not a known value") from None

    return Document(
        document_id=DocumentId(cast(UUID, record["document_id"])),
        provenance=DocumentProvenance(
            source_id=SourceId(cast(UUID, record["source_id"])),
            external_id=cast(str, record["external_id"]),
            source_version=cast("str | None", record["source_version"]),
        ),
        title=cast(str, record["title"]),
        content_version=ContentVersion(cast(int, record["content_version"])),
        content_fingerprint=cast(str, record["content_fingerprint"]),
        authorization_version=AuthorizationVersion(
            cast(int, record["authorization_version"])
        ),
        authorization_fingerprint=cast(str, record["authorization_fingerprint"]),
        availability=availability,
        created_at=cast(datetime, record["created_at"]),
        updated_at=cast(datetime, record["updated_at"]),
    )


async def upsert_document(
    connection: AsyncConnection, document: Document
) -> DocumentId:
    """Store a Document, keeping the identity and creation time already stored."""
    statement = insert(documents).values(as_row(document))
    statement = statement.on_conflict_do_update(
        constraint="uq_documents_source_external",
        set_={
            "source_version": statement.excluded.source_version,
            "title": statement.excluded.title,
            "content_version": statement.excluded.content_version,
            "content_fingerprint": statement.excluded.content_fingerprint,
            "authorization_version": statement.excluded.authorization_version,
            "authorization_fingerprint": statement.excluded.authorization_fingerprint,
            "availability": statement.excluded.availability,
            "updated_at": statement.excluded.updated_at,
        },
    ).returning(documents.c.document_id)
    stored_id = cast(UUID, (await connection.execute(statement)).scalar_one())
    return DocumentId(stored_id)


async def insert_document(connection: AsyncConnection, document: Document) -> bool:
    """Store a new Document, or return False if its source item is already stored.

    Unlike `upsert_document`, this never overwrites a row, so a run that lost
    a race to create the Document can decide again against the stored one.
    """
    statement = (
        insert(documents)
        .values(as_row(document))
        .on_conflict_do_nothing(constraint="uq_documents_source_external")
        .returning(documents.c.document_id)
    )
    return (await connection.execute(statement)).scalar_one_or_none() is not None


async def load_document(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    external_id: str,
    for_update: bool = False,
) -> Document | None:
    """Read the stored Document for one source item, if it exists.

    With `for_update`, the row stays locked until the transaction ends, so a
    concurrent writer waits instead of deciding from a stale copy.
    """
    statement = sa.select(documents).where(
        documents.c.source_id == source_id.value,
        documents.c.external_id == external_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return as_document(dict(record))


async def load_document_by_id(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
    for_update: bool = False,
) -> Document | None:
    """Read a stored Document by its ID, optionally locking its row."""
    statement = sa.select(documents).where(documents.c.document_id == document_id.value)
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return as_document(dict(record))


async def load_available_documents(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
) -> tuple[Document, ...]:
    """Read every available Document of one Source."""
    statement = (
        sa.select(documents)
        .where(
            documents.c.source_id == source_id.value,
            documents.c.availability == DocumentAvailability.AVAILABLE.value,
        )
        .order_by(documents.c.external_id)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(as_document(dict(record)) for record in records)


async def load_unpurged_documents(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
) -> tuple[Document, ...]:
    """Read tombstoned Documents that still hold Chunks and no purge can delete.

    A purge Job counts while it is queued, running, or failed with attempts
    left; a Document whose last purge used every attempt is returned.
    """
    open_purge = sa.exists().where(
        jobs.c.kind == JobKind.PURGE_DOCUMENT_CHUNKS.value,
        jobs.c.target_id == documents.c.document_id,
        sa.or_(
            jobs.c.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            sa.and_(
                jobs.c.status == JobStatus.FAILED.value,
                jobs.c.attempt < jobs.c.max_attempts,
            ),
        ),
    )
    statement = (
        sa.select(documents)
        .where(
            documents.c.source_id == source_id.value,
            documents.c.availability == DocumentAvailability.TOMBSTONED.value,
            sa.exists().where(chunks.c.document_id == documents.c.document_id),
            ~open_purge,
        )
        .order_by(documents.c.external_id)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(as_document(dict(record)) for record in records)


async def upsert_document_metadata(
    connection: AsyncConnection, described: DocumentMetadata
) -> None:
    """Store a Document's metadata, replacing what was stored before."""
    values = {
        "document_id": described.document_id.value,
        "media_type": described.media_type,
        "language": described.language.value,
        "modified_at": described.modified_at,
        "observed_at": described.observed_at,
    }
    statement = insert(document_metadata).values(values)
    await connection.execute(
        statement.on_conflict_do_update(
            index_elements=[document_metadata.c.document_id],
            set_={
                "media_type": statement.excluded.media_type,
                "language": statement.excluded.language,
                "modified_at": statement.excluded.modified_at,
                "observed_at": statement.excluded.observed_at,
            },
        )
    )


async def load_document_metadata(
    connection: AsyncConnection, *, document_id: DocumentId
) -> DocumentMetadata | None:
    """Read a Document's stored metadata, or None if it has none yet."""
    record = (
        (
            await connection.execute(
                sa.select(document_metadata).where(
                    document_metadata.c.document_id == document_id.value
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if record is None:
        return None
    try:
        language = Language(cast(str, record["language"]))
    except ValueError:
        raise InvalidDocument("stored language is not a known value") from None
    return DocumentMetadata(
        document_id=document_id,
        media_type=cast(str, record["media_type"]),
        language=language,
        modified_at=cast("datetime | None", record["modified_at"]),
        observed_at=cast(datetime, record["observed_at"]),
    )
