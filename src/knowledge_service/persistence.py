"""PostgreSQL persistence for Documents, Chunks, and Access Grants.

Domain values stay free of SQLAlchemy. This module owns the stored table shapes,
the translation between a domain value and a row, and the statements that read
and write them.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import (
    AccessGrant,
    InvalidGrant,
    Subject,
    SubjectKind,
    granted_subjects,
)
from knowledge_service.chunks import Chunk, StaleChunkWrite, ordered_chunks
from knowledge_service.documents import (
    AuthorizationVersion,
    ContentVersion,
    Document,
    DocumentAvailability,
    DocumentProvenance,
    InvalidDocument,
)
from knowledge_service.identifiers import (
    AccessGrantId,
    ChunkId,
    DocumentId,
    GroupId,
    SourceId,
    UserId,
)

metadata = sa.MetaData()

documents = sa.Table(
    "documents",
    metadata,
    sa.Column("document_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "source_id",
        sa.Uuid(),
        sa.ForeignKey("sources.source_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("external_id", sa.Text(), nullable=False),
    sa.Column("source_version", sa.Text(), nullable=True),
    sa.Column("content_version", sa.Integer(), nullable=False),
    sa.Column("content_fingerprint", sa.Text(), nullable=False),
    sa.Column("authorization_version", sa.Integer(), nullable=False),
    sa.Column("authorization_fingerprint", sa.Text(), nullable=False),
    sa.Column("availability", sa.String(length=16), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "source_id", "external_id", name="uq_documents_source_external"
    ),
    sa.CheckConstraint("content_version >= 1", name="ck_documents_content_version"),
    sa.CheckConstraint(
        "authorization_version >= 1", name="ck_documents_authorization_version"
    ),
    sa.CheckConstraint(
        "availability in ('available', 'tombstoned')",
        name="ck_documents_availability",
    ),
)

chunks = sa.Table(
    "chunks",
    metadata,
    sa.Column("chunk_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "document_id",
        sa.Uuid(),
        sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("content_version", sa.Integer(), nullable=False),
    sa.Column("ordinal", sa.Integer(), nullable=False),
    sa.Column("text", sa.Text(), nullable=False),
    sa.Column("heading_path", sa.Text(), nullable=True),
    sa.Column("token_count", sa.Integer(), nullable=False),
    sa.Column("content_fingerprint", sa.Text(), nullable=False),
    sa.Column("source_anchor", sa.Text(), nullable=True),
    sa.UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
    sa.CheckConstraint("content_version >= 1", name="ck_chunks_content_version"),
    sa.CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal"),
    sa.CheckConstraint("token_count >= 1", name="ck_chunks_token_count"),
)

access_grants = sa.Table(
    "access_grants",
    metadata,
    sa.Column("grant_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "document_id",
        sa.Uuid(),
        sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("subject_kind", sa.String(length=8), nullable=False),
    sa.Column("subject_id", sa.Uuid(), nullable=True),
    sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "subject_kind in ('public', 'user', 'group')",
        name="ck_access_grants_subject_kind",
    ),
    sa.CheckConstraint(
        "(subject_kind = 'public') = (subject_id is null)",
        name="ck_access_grants_subject_id",
    ),
    sa.UniqueConstraint(
        "document_id",
        "subject_kind",
        "subject_id",
        name="uq_access_grants_document_subject",
        postgresql_nulls_not_distinct=True,
    ),
)


def as_row(document: Document) -> dict[str, object]:
    """Translate a domain Document into the columns that store it."""
    return {
        "document_id": document.document_id.value,
        "source_id": document.provenance.source_id.value,
        "external_id": document.provenance.external_id,
        "source_version": document.provenance.source_version,
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


def _chunk_row(chunk: Chunk) -> dict[str, object]:
    return {
        "chunk_id": chunk.chunk_id.value,
        "document_id": chunk.document_id.value,
        "content_version": chunk.content_version.number,
        "ordinal": chunk.ordinal,
        "text": chunk.text,
        "heading_path": chunk.heading_path,
        "token_count": chunk.token_count,
        "content_fingerprint": chunk.content_fingerprint,
        "source_anchor": chunk.source_anchor,
    }


def _chunk_from(record: Mapping[str, object]) -> Chunk:
    return Chunk(
        chunk_id=ChunkId(cast(UUID, record["chunk_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        content_version=ContentVersion(cast(int, record["content_version"])),
        ordinal=cast(int, record["ordinal"]),
        text=cast(str, record["text"]),
        heading_path=cast("str | None", record["heading_path"]),
        token_count=cast(int, record["token_count"]),
        content_fingerprint=cast(str, record["content_fingerprint"]),
        source_anchor=cast("str | None", record["source_anchor"]),
    )


def _grant_row(grant: AccessGrant) -> dict[str, object]:
    identifier = grant.subject.identifier
    return {
        "grant_id": grant.grant_id.value,
        "document_id": grant.document_id.value,
        "subject_kind": grant.subject.kind.value,
        "subject_id": identifier.value if identifier is not None else None,
        "granted_at": grant.granted_at,
    }


def _grant_from(record: Mapping[str, object]) -> AccessGrant:
    try:
        kind = SubjectKind(cast(str, record["subject_kind"]))
    except ValueError:
        raise InvalidGrant("stored subject kind is not a known value") from None

    stored_id = cast("UUID | None", record["subject_id"])
    if kind is SubjectKind.PUBLIC:
        subject = Subject.public()
    elif kind is SubjectKind.USER:
        subject = Subject.user(UserId(cast(UUID, stored_id)))
    else:
        subject = Subject.group(GroupId(cast(UUID, stored_id)))

    return AccessGrant(
        grant_id=AccessGrantId(cast(UUID, record["grant_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        subject=subject,
        granted_at=cast(datetime, record["granted_at"]),
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


async def load_document(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    external_id: str,
) -> Document | None:
    """Read the stored Document for one source item, if it exists."""
    statement = sa.select(documents).where(
        documents.c.source_id == source_id.value,
        documents.c.external_id == external_id,
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return as_document(dict(record))


async def replace_chunks(
    connection: AsyncConnection,
    *,
    document: Document,
    chunk_run: Sequence[Chunk],
) -> tuple[Chunk, ...]:
    """Replace every stored Chunk for a Document with one ordered content version."""
    ordered = ordered_chunks(
        document_id=document.document_id,
        content_version=document.content_version,
        chunks=chunk_run,
    )

    stored_version = cast(
        "int | None",
        await connection.scalar(
            sa.select(sa.func.max(chunks.c.content_version)).where(
                chunks.c.document_id == document.document_id.value
            )
        ),
    )
    if stored_version is not None and stored_version > document.content_version.number:
        raise StaleChunkWrite(
            f"stored chunks are at content version {stored_version}; refusing to"
            f" replace them with version {document.content_version.number}"
        )

    await connection.execute(
        sa.delete(chunks).where(chunks.c.document_id == document.document_id.value)
    )
    if ordered:
        await connection.execute(
            sa.insert(chunks), [_chunk_row(chunk) for chunk in ordered]
        )
    return ordered


async def load_chunks(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
) -> tuple[Chunk, ...]:
    """Read the stored Chunks for a Document in ordinal order."""
    statement = (
        sa.select(chunks)
        .where(chunks.c.document_id == document_id.value)
        .order_by(chunks.c.ordinal)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_chunk_from(dict(record)) for record in records)


async def replace_access_grants(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
    grant_run: Sequence[AccessGrant],
) -> tuple[AccessGrant, ...]:
    """Replace every stored Access Grant for a Document with the given set."""
    ordered = granted_subjects(document_id=document_id, grants=grant_run)

    await connection.execute(
        sa.delete(access_grants).where(access_grants.c.document_id == document_id.value)
    )
    if ordered:
        await connection.execute(
            sa.insert(access_grants), [_grant_row(grant) for grant in ordered]
        )
    return ordered


async def load_access_grants(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
) -> tuple[AccessGrant, ...]:
    """Read the stored Access Grants for a Document."""
    statement = (
        sa.select(access_grants)
        .where(access_grants.c.document_id == document_id.value)
        .order_by(access_grants.c.subject_kind, access_grants.c.subject_id)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_grant_from(dict(record)) for record in records)
