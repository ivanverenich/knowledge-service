"""Citations: an Answer's references to Evidence, checked on the server.

A model that writes an Answer names the Evidence it used by opaque ID (see
`answering.evidence`). Nothing it writes is trusted: `resolve_citations`
accepts only IDs of the request's own `EvidenceSet`, and every other part
of a Citation, the Chunk, the Document, its title, heading, link, and
excerpt, is copied from that Evidence. So a model can leave a source out,
but it can never invent one or relabel one.

What the module promises:

- **Strict references.** `resolve_citations` takes the model's list of
  IDs as it arrived. It refuses, with `InvalidCitation`, anything that is
  not a list or tuple of plain `str`, an ID that is malformed, an ID not
  in the set, and an ID listed twice. One bad reference rejects the whole
  list; nothing is guessed or dropped.
- **Provenance from the server.** A `Citation` is built from its Evidence
  only.
- **Checked when read back.** `CitationPayload` is the JSON form of a
  Citation, for a client or for storage. `read_citations` reads a list of
  payloads against the `EvidenceSet` they claim to come from, with the
  same checks, and also refuses a payload whose Document, title, heading,
  link, or excerpt differs from its Evidence, or that has a field too many.
- **Quiet.** Errors name the position and the defect, never a value, and
  keep no chained error that could; the reprs of a Citation and of a
  `CitationPayload` hide the Document's title, heading, link, and excerpt.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knowledge_service.answering.evidence import (
    Evidence,
    EvidenceId,
    EvidenceSet,
    InvalidEvidence,
)
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId


class CitationDefect(StrEnum):
    """Why a list of references was refused; the values are never repeated."""

    NOT_A_LIST = "not_a_list"
    MALFORMED = "malformed"
    UNKNOWN = "unknown"
    DUPLICATE = "duplicate"
    MISMATCHED = "mismatched"


class InvalidCitation(ValueError):
    """A reference or payload cannot be a Citation of this EvidenceSet."""

    def __init__(self, defect: CitationDefect, index: int | None = None) -> None:
        self.defect = defect
        self.index = index
        where = "citations" if index is None else f"citations.{index}"
        super().__init__(f"{where}: {defect.value}")


@dataclass(frozen=True, slots=True)
class Citation:
    """A checked reference from an Answer to one Evidence and its Document."""

    evidence_id: EvidenceId
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_link: str | None = field(repr=False)
    excerpt: str = field(repr=False)


class CitationPayload(BaseModel):
    """The JSON form of a Citation: what a client sees, and nothing else.

    The Chunk ID and the text stay on the server; the Document ID lets a
    client group Citations of one Document.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    evidence_id: str
    document_id: str
    title: str = Field(repr=False)
    heading_path: str | None = Field(repr=False)
    source_link: str | None = Field(repr=False)
    excerpt: str = Field(repr=False)


def resolve_citations(
    evidence: EvidenceSet, references: object
) -> tuple[Citation, ...]:
    """Turn the model's Evidence IDs into Citations, or raise `InvalidCitation`."""
    if type(evidence) is not EvidenceSet:
        raise TypeError("evidence must be an EvidenceSet")
    # A list subclass could run its own code while being read; not this one.
    if type(references) not in (list, tuple):
        raise InvalidCitation(CitationDefect.NOT_A_LIST)
    citations: list[Citation] = []
    seen: set[EvidenceId] = set()
    for index, reference in enumerate(cast(Sequence[object], references)):
        item = _known(evidence, reference, index, seen)
        citations.append(_citation(item))
    return tuple(citations)


def citation_payload(citation: Citation) -> CitationPayload:
    """Return the JSON form of a Citation."""
    return CitationPayload(
        evidence_id=citation.evidence_id.value,
        document_id=citation.document_id.serialize(),
        title=citation.title,
        heading_path=citation.heading_path,
        source_link=citation.source_link,
        excerpt=citation.excerpt,
    )


def read_citations(evidence: EvidenceSet, payloads: object) -> tuple[Citation, ...]:
    """Read JSON Citations back against their EvidenceSet, or raise.

    `payloads` is a decoded JSON list of objects: each must be a plain
    `dict` that is a valid `CitationPayload`, whose Evidence ID is in
    `evidence`, listed once, and whose every other field equals that
    Evidence's provenance.
    """
    if type(evidence) is not EvidenceSet:
        raise TypeError("evidence must be an EvidenceSet")
    if type(payloads) not in (list, tuple):
        raise InvalidCitation(CitationDefect.NOT_A_LIST)
    citations: list[Citation] = []
    seen: set[EvidenceId] = set()
    for index, raw in enumerate(cast(Sequence[object], payloads)):
        payload = _payload(raw)
        # Raised outside any `except`: a validation error quotes the input.
        if payload is None:
            raise InvalidCitation(CitationDefect.MALFORMED, index)
        item = _known(evidence, payload.evidence_id, index, seen)
        citation = _citation(item)
        if citation_payload(citation) != payload:
            raise InvalidCitation(CitationDefect.MISMATCHED, index)
        citations.append(citation)
    return tuple(citations)


def _payload(raw: object) -> CitationPayload | None:
    """A payload read from a plain dict, or None if it is not a valid one."""
    # A model instance or a dict subclass could run its own code; JSON never
    # produces either.
    if type(raw) is not dict:
        return None
    try:
        return CitationPayload.model_validate(raw)
    except ValidationError:
        return None


def _known(
    evidence: EvidenceSet, reference: object, index: int, seen: set[EvidenceId]
) -> Evidence:
    """The Evidence a reference names, if it is well formed, known, and new."""
    try:
        evidence_id = EvidenceId(cast(str, reference))
    except InvalidEvidence:
        raise InvalidCitation(CitationDefect.MALFORMED, index) from None
    item = evidence.get(evidence_id)
    if item is None:
        raise InvalidCitation(CitationDefect.UNKNOWN, index)
    if evidence_id in seen:
        raise InvalidCitation(CitationDefect.DUPLICATE, index)
    seen.add(evidence_id)
    return item


def _citation(item: Evidence) -> Citation:
    return Citation(
        evidence_id=item.evidence_id,
        chunk_id=item.chunk_id,
        document_id=item.document_id,
        source_id=item.source_id,
        content_version=item.content_version,
        title=item.title,
        heading_path=item.heading_path,
        source_link=item.source_link,
        excerpt=item.excerpt,
    )
