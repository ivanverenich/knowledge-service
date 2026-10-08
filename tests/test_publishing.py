"""Tests for the unit of work that publishes a Document."""

from datetime import UTC, datetime

import pytest

from knowledge_service.access import AccessGrant, Subject
from knowledge_service.chunks import Chunk
from knowledge_service.documents import ContentVersion, Document, DocumentProvenance
from knowledge_service.identifiers import (
    AccessGrantId,
    ChunkId,
    DocumentId,
    SourceId,
    UserId,
)
from knowledge_service.publishing import InvalidPublication, Publication

OBSERVED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000020")
OTHER_DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000021")
USER_ID = UserId.parse("00000000-0000-0000-0000-000000000030")


def registered_document() -> Document:
    return Document.register(
        document_id=DOCUMENT_ID,
        provenance=DocumentProvenance(
            source_id=SOURCE_ID, external_id="page-1", source_version="1"
        ),
        content_fingerprint="content-a",
        authorization_fingerprint="acl-a",
        observed_at=OBSERVED_AT,
    )


def make_chunk(
    document: Document,
    *,
    ordinal: int = 0,
    content_version: ContentVersion | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=ChunkId.new(),
        document_id=document.document_id,
        content_version=content_version or document.content_version,
        ordinal=ordinal,
        text="Install the service.",
        heading_path="Setup",
        token_count=6,
        content_fingerprint=document.content_fingerprint,
        source_anchor="#setup",
    )


def test_a_publication_carries_one_document_version() -> None:
    document = registered_document()
    publication = Publication(document=document, chunks=(make_chunk(document),))

    assert publication.document is document
    assert len(publication.chunks) == 1


def test_a_publication_without_grants_is_accepted() -> None:
    document = registered_document()

    assert Publication(document=document).grants == ()


def test_a_chunk_from_another_content_version_is_rejected() -> None:
    document = registered_document()
    stale = make_chunk(document, content_version=ContentVersion(2))

    with pytest.raises(InvalidPublication, match="content version"):
        Publication(document=document, chunks=(stale,))


def test_a_grant_for_another_document_is_rejected() -> None:
    document = registered_document()
    elsewhere = AccessGrant(
        grant_id=AccessGrantId.new(),
        document_id=OTHER_DOCUMENT_ID,
        subject=Subject.user(USER_ID),
        granted_at=OBSERVED_AT,
    )

    with pytest.raises(InvalidPublication, match="grant"):
        Publication(document=document, grants=(elsewhere,))
