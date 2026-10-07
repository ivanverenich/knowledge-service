"""Chunk values: ordered, citable sections of a Document.

A Chunk is the unit a citation points at, so it carries both the text that may
be retrieved and the location a reader can be sent back to.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId


class InvalidChunk(ValueError):
    """A Chunk value, chunk run, or chunk write violates a domain invariant."""


class StaleChunkWrite(InvalidChunk):
    """A chunk write targets a content version older than the stored one."""


@dataclass(frozen=True, slots=True)
class Chunk:
    """One ordered, provenance-preserving section of a Document."""

    chunk_id: ChunkId
    document_id: DocumentId
    content_version: ContentVersion
    ordinal: int
    text: str
    heading_path: str | None
    token_count: int
    content_fingerprint: str
    source_anchor: str | None

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise InvalidChunk("chunk ordinal must be a non-negative integer")
        if not self.text.strip():
            raise InvalidChunk("chunk text must not be empty")
        if self.heading_path is not None and not self.heading_path.strip():
            raise InvalidChunk("heading path must not be empty when provided")
        if type(self.token_count) is not int or self.token_count < 1:
            raise InvalidChunk("token count must be a positive integer")
        if not self.content_fingerprint.strip():
            raise InvalidChunk("content fingerprint must not be empty")
        if self.source_anchor is not None and not self.source_anchor.strip():
            raise InvalidChunk("source anchor must not be empty when provided")


def ordered_chunks(
    *,
    document_id: DocumentId,
    content_version: ContentVersion,
    chunks: Sequence[Chunk],
) -> tuple[Chunk, ...]:
    """Return one contiguous, ordered run of chunks for a single content version."""
    for chunk in chunks:
        if chunk.document_id != document_id:
            raise InvalidChunk("every chunk must belong to the same document")
        if chunk.content_version != content_version:
            raise InvalidChunk("every chunk must carry the same content version")

    ordered = tuple(sorted(chunks, key=lambda chunk: chunk.ordinal))
    for expected, chunk in enumerate(ordered):
        if chunk.ordinal != expected:
            raise InvalidChunk("chunk ordinals must be contiguous from zero")
    return ordered
