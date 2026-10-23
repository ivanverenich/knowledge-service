"""Embed a Document's new Chunks, then publish all their vectors at once.

Calling an embedding model takes seconds, so it never runs inside a database
transaction: a transaction held open that long would keep a pooled
connection busy and block every writer waiting for the Document's row. The
work is therefore split in three:

1. **Read.** A short transaction locks the Document's row and records what
   it looks like now: its content version, its Chunks with their text, and
   which of them still lack a vector in the model's Embedding Space. Only an
   available Document is read, so the text of a Document tombstoned before
   the read is never sent to the provider.
2. **Embed.** With no connection held, the Chunks' texts go to the model in
   batches.
3. **Publish.** A second short transaction locks the Document's row and the
   space's row again and compares them with what step 1 saw. If anything
   changed, nothing is written and the caller is told the result is stale;
   otherwise every vector is written in one statement. This is
   compare-and-swap: the write happens only if the state is still the one it
   was computed from.

A Document is therefore never left with vectors for some of its Chunks: when
any batch fails, nothing is written, and the next run embeds them all again.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.chunks import Chunk
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import ContentVersion, DocumentAvailability
from knowledge_service.embedding_batches import (
    BatchStatus,
    embed_in_batches,
    vectors_by_position,
)
from knowledge_service.embeddings import EmbeddingModel, Vector
from knowledge_service.identifiers import ChunkId, DocumentId, EmbeddingSpaceId
from knowledge_service.persistence import (
    insert_chunk_embeddings,
    load_chunks,
    load_document_by_id,
    load_embedded_chunk_ids,
    load_embedding_space_id,
)


class EmbeddingPublicationError(Exception):
    """Base error for publishing a Document's embeddings."""


class UnknownEmbeddingSpace(EmbeddingPublicationError):
    """No Embedding Space matches the model's identity; nothing was sent."""


class IncompleteEmbedding(EmbeddingPublicationError):
    """At least one batch did not succeed, so no vector was written.

    `errors` holds error class names only, never text, so it is safe to log.
    """

    def __init__(self, *, failed: int, deferred: int, errors: Sequence[str]) -> None:
        self.failed = failed
        self.deferred = deferred
        self.errors = tuple(sorted(set(errors)))
        super().__init__(
            f"embedding incomplete: {failed} batches failed, {deferred} deferred"
            f" ({', '.join(self.errors) or 'no error'})"
        )


class EmbeddingPublicationStatus(StrEnum):
    """How one publication attempt ended."""

    PUBLISHED = "published"
    UNCHANGED = "unchanged"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class EmbeddingPublication:
    """What one attempt did; `embedded` counts the vectors written."""

    document_id: DocumentId
    status: EmbeddingPublicationStatus
    embedded: int = 0


@dataclass(frozen=True, slots=True)
class _Snapshot:
    """The state the vectors are computed from, compared again before writing.

    Two snapshots are equal when the version, the Chunks with their text, and
    the embedded Chunks are; `pending` follows from those.
    """

    content_version: ContentVersion
    # The text is part of the key: vectors are only valid for the text they
    # were computed from, even if a Chunk ID were reused. `chunks` and
    # `pending` hold Document text, so both are kept out of repr, and logging
    # a snapshot never shows it.
    chunks: frozenset[tuple[ChunkId, str]] = field(repr=False)
    embedded: frozenset[ChunkId]
    pending: tuple[Chunk, ...] = field(repr=False, compare=False)


async def publish_document_embeddings(
    runtime: DatabaseRuntime,
    model: EmbeddingModel,
    *,
    document_id: DocumentId,
    concurrency: int,
    request_id: UUID,
    timeout_seconds: float,
    now: datetime,
) -> EmbeddingPublication:
    """Embed the Document's Chunks that lack a vector, then publish them together.

    Raises `UnknownEmbeddingSpace` before sending anything when no space holds
    the model's identity, `EmbeddingInputRejected` when a Chunk is too long
    for the model, and `IncompleteEmbedding` when any batch fails; in every
    case nothing is written.
    """
    async with runtime.transaction() as connection:
        space_id = await load_embedding_space_id(connection, model.identity)
        if space_id is None:
            raise UnknownEmbeddingSpace("no embedding space matches the model")
        snapshot = await _snapshot(
            connection,
            document_id=document_id,
            space_id=space_id,
        )
    if snapshot is None:
        return EmbeddingPublication(
            document_id=document_id,
            status=EmbeddingPublicationStatus.UNAVAILABLE,
        )
    if not snapshot.pending:
        return EmbeddingPublication(
            document_id=document_id, status=EmbeddingPublicationStatus.UNCHANGED
        )

    vectors = await _embed(
        model,
        snapshot.pending,
        concurrency=concurrency,
        request_id=request_id,
        timeout_seconds=timeout_seconds,
    )

    # Compare-and-swap: lock and read again, and write only if nothing changed.
    async with runtime.transaction() as connection:
        current_space = await load_embedding_space_id(connection, model.identity)
        current = await _snapshot(
            connection, document_id=document_id, space_id=space_id
        )
        if current_space != space_id or current is None or current != snapshot:
            return EmbeddingPublication(
                document_id=document_id, status=EmbeddingPublicationStatus.STALE
            )
        await insert_chunk_embeddings(
            connection,
            space_id=space_id,
            dimensions=model.identity.dimensions,
            vectors=vectors,
            created_at=now,
        )
    return EmbeddingPublication(
        document_id=document_id,
        status=EmbeddingPublicationStatus.PUBLISHED,
        embedded=len(vectors),
    )


async def _snapshot(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
    space_id: EmbeddingSpaceId,
) -> _Snapshot | None:
    """Read the Document's embedding state, or None if it is not available.

    The row stays locked until the transaction ends. Reconciling, tombstoning,
    and purging lock it too before they touch Chunks, so all three reads see
    one state of the Document.
    """
    document = await load_document_by_id(
        connection,
        document_id=document_id,
        for_update=True,
    )
    if document is None or document.availability is not DocumentAvailability.AVAILABLE:
        return None
    stored = await load_chunks(connection, document_id=document_id)
    embedded = await load_embedded_chunk_ids(
        connection, document_id=document_id, space_id=space_id
    )
    return _Snapshot(
        content_version=document.content_version,
        chunks=frozenset((chunk.chunk_id, chunk.text) for chunk in stored),
        embedded=embedded,
        pending=tuple(chunk for chunk in stored if chunk.chunk_id not in embedded),
    )


async def _embed(
    model: EmbeddingModel,
    pending: Sequence[Chunk],
    *,
    concurrency: int,
    request_id: UUID,
    timeout_seconds: float,
) -> Mapping[ChunkId, Vector]:
    """Return one vector per pending Chunk, or raise if any batch did not succeed."""
    outcomes = await embed_in_batches(
        model,
        [chunk.text for chunk in pending],
        concurrency=concurrency,
        request_id=request_id,
        timeout_seconds=timeout_seconds,
    )
    unfinished = [o for o in outcomes if o.status is not BatchStatus.SUCCEEDED]
    if unfinished:
        raise IncompleteEmbedding(
            failed=sum(o.status is BatchStatus.FAILED for o in unfinished),
            deferred=sum(o.status is BatchStatus.DEFERRED for o in unfinished),
            errors=[o.error for o in unfinished if o.error is not None],
        )
    by_position = vectors_by_position(outcomes)
    return {chunk.chunk_id: by_position[index] for index, chunk in enumerate(pending)}
