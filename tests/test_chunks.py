"""Tests for Chunk ordering and value invariants."""

import pytest

from knowledge_service.chunks import Chunk, InvalidChunk, ordered_chunks
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId

DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000020")
OTHER_DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000021")
CONTENT_VERSION = ContentVersion(1)


def make_chunk(
    *,
    ordinal: int,
    document_id: DocumentId = DOCUMENT_ID,
    content_version: ContentVersion = CONTENT_VERSION,
    text: str = "Some handbook text.",
) -> Chunk:
    return Chunk(
        chunk_id=ChunkId.new(),
        document_id=document_id,
        content_version=content_version,
        ordinal=ordinal,
        text=text,
        heading_path="Setup > Install",
        token_count=12,
        content_fingerprint="content-a",
        source_anchor="#install",
    )


def test_ordered_chunks_returns_the_run_in_ordinal_order() -> None:
    run = ordered_chunks(
        document_id=DOCUMENT_ID,
        content_version=CONTENT_VERSION,
        chunks=[make_chunk(ordinal=2), make_chunk(ordinal=0), make_chunk(ordinal=1)],
    )

    assert [chunk.ordinal for chunk in run] == [0, 1, 2]


def test_a_gap_in_ordinals_is_rejected() -> None:
    with pytest.raises(InvalidChunk, match="contiguous"):
        ordered_chunks(
            document_id=DOCUMENT_ID,
            content_version=CONTENT_VERSION,
            chunks=[make_chunk(ordinal=0), make_chunk(ordinal=2)],
        )


def test_a_chunk_from_another_document_is_rejected() -> None:
    with pytest.raises(InvalidChunk, match="same document"):
        ordered_chunks(
            document_id=DOCUMENT_ID,
            content_version=CONTENT_VERSION,
            chunks=[
                make_chunk(ordinal=0),
                make_chunk(ordinal=1, document_id=OTHER_DOCUMENT_ID),
            ],
        )


def test_a_chunk_from_another_content_version_is_rejected() -> None:
    with pytest.raises(InvalidChunk, match="content version"):
        ordered_chunks(
            document_id=DOCUMENT_ID,
            content_version=CONTENT_VERSION,
            chunks=[
                make_chunk(ordinal=0),
                make_chunk(ordinal=1, content_version=ContentVersion(2)),
            ],
        )


def test_empty_chunk_text_raises_typed_failure() -> None:
    with pytest.raises(InvalidChunk, match="text"):
        make_chunk(ordinal=0, text="   ")
