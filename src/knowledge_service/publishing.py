"""Publishing: store a Document's complete state inside one transaction.

A Document is published when its row, its Chunks, and its Access Grants all
describe the same content version. This module builds that unit of work; the
caller owns the transaction it runs in.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AccessGrant
from knowledge_service.chunks import Chunk
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document
from knowledge_service.identifiers import DocumentId
from knowledge_service.persistence import (
    replace_access_grants,
    replace_chunks,
    upsert_document,
)


class InvalidPublication(ValueError):
    """A Publication holds parts that do not describe one Document version."""


@dataclass(frozen=True, slots=True)
class Publication:
    """One Document together with the Chunks and Access Grants to store with it."""

    document: Document
    chunks: tuple[Chunk, ...] = ()
    grants: tuple[AccessGrant, ...] = ()

    def __post_init__(self) -> None:
        for chunk in self.chunks:
            if chunk.document_id != self.document.document_id:
                raise InvalidPublication("every chunk must belong to the document")
            if chunk.content_version != self.document.content_version:
                raise InvalidPublication(
                    "every chunk must carry the document content version"
                )
            if chunk.content_fingerprint != self.document.content_fingerprint:
                raise InvalidPublication(
                    "every chunk must carry the document content fingerprint"
                )
        for grant in self.grants:
            if grant.document_id != self.document.document_id:
                raise InvalidPublication("every grant must belong to the document")


async def publish_document(
    connection: AsyncConnection, publication: Publication
) -> DocumentId:
    """Store a Publication's row, Chunks, and Access Grants on one connection."""
    document = publication.document
    stored_id = await upsert_document(connection, document)
    stored_document = replace(document, document_id=stored_id)
    chunk_run = [replace(chunk, document_id=stored_id) for chunk in publication.chunks]
    await replace_chunks(connection, document=stored_document, chunk_run=chunk_run)
    await replace_access_grants(
        connection, document_id=stored_id, grant_run=publication.grants
    )
    return stored_id


async def publish_documents(
    runtime: DatabaseRuntime,
    *,
    publications: Sequence[Publication],
) -> tuple[DocumentId, ...]:
    """Publish every Publication in one transaction, or publish none of them."""
    async with runtime.transaction() as connection:
        published: list[DocumentId] = []
        for publication in publications:
            published.append(await publish_document(connection, publication))
        return tuple(published)
