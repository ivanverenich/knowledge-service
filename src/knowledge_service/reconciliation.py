"""Reconcile one normalized Document with the stored, searchable state.

Each Synchronization Run sees most documents again unchanged. Reconciling
compares the new content and authorization fingerprints with the stored ones
and writes only what changed, all in one transaction:

- nothing changed: nothing is written;
- the content changed: the Chunks are rebuilt and replaced;
- only who may read it changed: the Access Grants are replaced, and the Chunks
  are kept, because re-chunking would change nothing.

In every case the Document's metadata (media type, language, and when the
Source last changed it) is stored if it differs from the stored row, so a
Document reconciled before that row existed gets one on its next run. If
only the metadata changed, the change is `METADATA`. An observation older
than the stored metadata that would change it raises `InvalidDocument`
instead of rolling it back.

A reader sees either the previous complete version or the new one, never a
mixture, because the Document row, its Chunks, its Access Grants, and its
metadata are written together and committed once.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AccessGrant, Subject
from knowledge_service.chunking import chunk_by_structure
from knowledge_service.chunks import Chunk
from knowledge_service.database import DatabaseRuntime
from knowledge_service.deletions import availability_event
from knowledge_service.documents import (
    Document,
    DocumentAvailability,
    DocumentMetadata,
    InvalidDocument,
)
from knowledge_service.fingerprints import (
    authorization_fingerprint,
    content_fingerprint,
)
from knowledge_service.identifiers import AccessGrantId, ChunkId, DocumentId
from knowledge_service.normalization import NormalizedDocument
from knowledge_service.persistence import (
    insert_document,
    load_document,
    load_document_metadata,
    record_audit_event,
    replace_access_grants,
    replace_chunks,
    upsert_document,
    upsert_document_metadata,
)
from knowledge_service.provenance import chunk_sources


class Change(StrEnum):
    """What one reconciliation did to the stored state."""

    CREATED = "created"
    UNCHANGED = "unchanged"
    CONTENT = "content"
    ACCESS = "access"
    CONTENT_AND_ACCESS = "content_and_access"
    RESTORED = "restored"
    SOURCE_VERSION = "source_version"
    TITLE = "title"
    METADATA = "metadata"


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """The Document as stored after reconciling, and what changed."""

    document: Document
    change: Change


async def reconcile_document(
    runtime: DatabaseRuntime,
    normalized: NormalizedDocument,
    *,
    readers: Sequence[Subject],
    observed_at: datetime,
) -> Reconciliation:
    """Publish one observation of a document, writing only what changed.

    `readers` are the normalized subjects who may read it; an empty sequence
    means nobody may. An observation older than the stored version raises
    `InvalidDocument`, and nothing is written.
    """
    provenance = normalized.provenance
    content = content_fingerprint(normalized.parsed)
    # Only the readers count in this fingerprint, so a provisional ID will do.
    provisional = DocumentId.new()
    access = authorization_fingerprint(
        document_id=provisional, grants=_grants(provisional, readers, observed_at)
    )
    async with runtime.transaction() as connection:
        # Lock the stored row: a concurrent run of the same document waits
        # here until this one commits, then decides from what it committed.
        stored = await load_document(
            connection,
            source_id=provenance.source_id,
            external_id=provenance.external_id,
            for_update=True,
        )
        if stored is None:
            created = Document.register(
                document_id=DocumentId.new(),
                provenance=provenance,
                title=normalized.title,
                content_fingerprint=content,
                authorization_fingerprint=access,
                observed_at=observed_at,
            )
            if await insert_document(connection, created):
                await _write_chunks(connection, normalized, created)
                await _write_grants(connection, created, readers, observed_at)
                await _write_metadata(connection, normalized, created, observed_at)
                return Reconciliation(document=created, change=Change.CREATED)
            # Another run created it after the lookup; decide against its row.
            stored = await load_document(
                connection,
                source_id=provenance.source_id,
                external_id=provenance.external_id,
                for_update=True,
            )
            if stored is None:
                raise InvalidDocument("the document vanished while reconciling")

        document = stored.reconcile(
            title=normalized.title,
            content_fingerprint=content,
            authorization_fingerprint=access,
            source_version=provenance.source_version,
            observed_at=observed_at,
        )
        metadata_changed = await _write_metadata(
            connection, normalized, stored, observed_at
        )
        if document is stored:
            return Reconciliation(
                document=stored,
                change=Change.METADATA if metadata_changed else Change.UNCHANGED,
            )
        content_changed = document.content_version != stored.content_version
        access_changed = document.authorization_version != stored.authorization_version
        restored = stored.availability is DocumentAvailability.TOMBSTONED
        source_version_changed = document.provenance != stored.provenance
        await upsert_document(connection, document)
        if restored:
            await record_audit_event(connection, availability_event(document))
        # A restored Document gets its Chunks back even if its text is unchanged.
        if content_changed or restored:
            await _write_chunks(connection, normalized, document)
        if access_changed:
            await _write_grants(connection, document, readers, observed_at)
    return Reconciliation(
        document=document,
        change=_change(
            content=content_changed,
            access=access_changed,
            restored=restored,
            source_version=source_version_changed,
        ),
    )


def build_chunks(
    normalized: NormalizedDocument,
    document: Document,
) -> tuple[Chunk, ...]:
    """Chunk a document by its structure and give each chunk its source anchor."""
    drafts = chunk_by_structure(normalized.parsed)
    sources = chunk_sources(
        normalized.parsed,
        drafts,
        external_id=normalized.provenance.external_id,
    )
    return tuple(
        Chunk(
            chunk_id=ChunkId.new(),
            document_id=document.document_id,
            content_version=document.content_version,
            ordinal=draft.ordinal,
            text=draft.text,
            heading_path=draft.heading_path,
            token_count=draft.token_count,
            content_fingerprint=document.content_fingerprint,
            source_anchor=source.anchor,
        )
        for draft, source in zip(drafts, sources, strict=True)
    )


def _grants(
    document_id: DocumentId,
    readers: Sequence[Subject],
    observed_at: datetime,
) -> list[AccessGrant]:
    return [
        AccessGrant(
            grant_id=AccessGrantId.new(),
            document_id=document_id,
            subject=subject,
            granted_at=observed_at,
        )
        for subject in readers
    ]


async def _write_chunks(
    connection: AsyncConnection,
    normalized: NormalizedDocument,
    document: Document,
) -> None:
    await replace_chunks(
        connection, document=document, chunk_run=build_chunks(normalized, document)
    )


async def _write_metadata(
    connection: AsyncConnection,
    normalized: NormalizedDocument,
    document: Document,
    observed_at: datetime,
) -> bool:
    """Store the observed metadata if it differs; return whether it did.

    Like the Document's own versions, the metadata only moves forward: an
    observation older than the one that last changed it, with different
    values, raises `InvalidDocument`, so a late run cannot roll it back. An
    older observation with the same values changes nothing and passes.
    """
    observed = DocumentMetadata(
        document_id=document.document_id,
        media_type=normalized.media_type,
        language=normalized.language.language,
        modified_at=normalized.modified_at,
        observed_at=observed_at,
    )
    stored = await load_document_metadata(connection, document_id=document.document_id)
    if stored is not None:
        if replace(stored, observed_at=observed_at) == observed:
            return False
        if observed_at < stored.observed_at:
            raise InvalidDocument(
                "observed_at must not be earlier than the stored metadata"
            )
    await upsert_document_metadata(connection, observed)
    return True


async def _write_grants(
    connection: AsyncConnection,
    document: Document,
    readers: Sequence[Subject],
    observed_at: datetime,
) -> None:
    await replace_access_grants(
        connection,
        document_id=document.document_id,
        grant_run=_grants(document.document_id, readers, observed_at),
    )


def _change(
    *, content: bool, access: bool, restored: bool, source_version: bool
) -> Change:
    if content and access:
        return Change.CONTENT_AND_ACCESS
    if content:
        return Change.CONTENT
    if access:
        return Change.ACCESS
    if restored:
        return Change.RESTORED
    if source_version:
        return Change.SOURCE_VERSION
    return Change.TITLE
