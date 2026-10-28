"""Store, replace, and load a Document's Chunks."""

from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.chunks import Chunk, StaleChunkWrite, ordered_chunks
from knowledge_service.documents import ContentVersion, Document, DocumentAvailability
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.persistence.schema import chunks, documents


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


def searchable_chunks_statement(source_id: UUID) -> sa.Select[Any]:
    """Select the Chunks retrieval may read: only those of available Documents."""
    return (
        sa.select(chunks)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(
            documents.c.source_id == source_id,
            documents.c.availability == DocumentAvailability.AVAILABLE.value,
        )
        .order_by(documents.c.external_id, chunks.c.ordinal)
    )


async def load_searchable_chunks(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
) -> tuple[Chunk, ...]:
    """Read the Chunks of one Source that retrieval may return."""
    records = (
        (await connection.execute(searchable_chunks_statement(source_id.value)))
        .mappings()
        .all()
    )
    return tuple(_chunk_from(dict(record)) for record in records)


async def delete_chunks(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
) -> int:
    """Delete every stored Chunk of a Document; return how many were deleted."""
    result = await connection.execute(
        sa.delete(chunks).where(chunks.c.document_id == document_id.value)
    )
    return result.rowcount
