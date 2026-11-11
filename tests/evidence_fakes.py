"""Candidates as retrieval returns them, for the answering tests."""

from typing import Any
from uuid import UUID

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.retrieval.vector_search import VectorCandidate

MARKER = "PLANTED-MARKER-7719"
SOURCE = SourceId(UUID(int=1))


def candidate(number: int, **fields: Any) -> VectorCandidate:
    """A Candidate as retrieval returns it, numbered for a distinct Chunk."""
    values: dict[str, Any] = {
        "rank": number,
        "chunk_id": ChunkId(UUID(int=1000 + number)),
        "document_id": DocumentId(UUID(int=2000 + number)),
        "source_id": SOURCE,
        "content_version": ContentVersion(3),
        "ordinal": number,
        "distance": 0.1 * number,
        "external_id": f"policy-{number}.md",
        "title": f"Policy {number}",
        "heading_path": f"Policy {number} > Days",
        "source_anchor": f"policy-{number}.md#offset=0-40",
        "text": f"Every employee receives {number} days of leave.",
    }
    values.update(fields)
    return VectorCandidate(**values)
