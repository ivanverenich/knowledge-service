"""Evidence: the Chunks an Answer may use, each under an opaque ID.

Retrieval returns Candidates the reader may see. `build_evidence` turns
them into an `EvidenceSet` for one request, and the model that writes the
Answer sees only each Evidence's opaque ID and text. Where a Chunk came
from, its Document, title, heading, and link, stays on the server, so the
model can point at Evidence but can never name a source of its own.

What an `EvidenceSet` promises:

- **Opaque IDs.** Every Evidence gets a fresh random `EvidenceId` for this
  set only, such as `E3f9a1c0b`. It says nothing about the Chunk, and a
  Document's text cannot know it in advance, so a passage that writes
  "cite E1" cannot make itself cited. IDs are distinct within a set.
- **Only what retrieval allowed.** Evidence is built from Candidates,
  which passed the permission check inside their search; nothing is
  added, and no Chunk appears twice.
- **Provenance is copied, not trusted.** Each Evidence keeps the Chunk's
  identity and version and the Document's title, heading path, and source
  link as retrieval returned them; a Citation takes them from here, never
  from the model. Every identifier is rebuilt from a checked UUID.
- **Safe to show.** Title, heading path, and source link are cleaned for
  display by the ingestion policy (see `ingestion.provenance`): no
  invisible or direction characters, and no credentials in an address.
  The excerpt is the text's first words, cleaned the same way. Whether a
  link may be opened is the interface's decision, not this module's.
- **Checked whatever builds it.** An `Evidence` checks its own fields, so
  an `EvidenceSet` built by hand keeps the same rules.
- **Bounded.** At most `MAX_EVIDENCE` items; at most `MAX_EVIDENCE + 1`
  Candidates are ever read.
- **Quiet.** Text, excerpt, title, heading, and link are kept out of every
  repr, and errors never quote them.
"""

import itertools
import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Self, cast
from uuid import UUID

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import (
    ChunkId,
    DocumentId,
    SourceId,
    StableIdentifier,
)
from knowledge_service.ingestion.provenance import display_excerpt, display_text
from knowledge_service.retrieval.candidates import Candidate

MAX_EVIDENCE = 20
# "E" and 8 hex digits: 4 billion values, a few tokens each.
_ID_BYTES = 4
_ID = re.compile(r"E[0-9a-f]{8}")
# The excerpt is cut from the text's start; cleaning more would only cost.
_EXCERPT_SOURCE_CHARACTERS = 4_000


class InvalidEvidence(ValueError):
    """Evidence cannot be built from what was given; nothing is quoted."""


@dataclass(frozen=True, slots=True)
class EvidenceId:
    """An opaque name for one Evidence, valid only inside its set."""

    value: str

    def __post_init__(self) -> None:
        if type(self.value) is not str or _ID.fullmatch(self.value) is None:
            raise InvalidEvidence("an Evidence ID is E and 8 lowercase hex digits")

    @classmethod
    def new(cls) -> Self:
        """Return a fresh random ID."""
        return cls("E" + secrets.token_hex(_ID_BYTES))


@dataclass(frozen=True, slots=True)
class Evidence:
    """One Chunk an Answer may use, under its opaque ID, with its provenance.

    `source_link` is the Chunk's `source_anchor`, cleaned for display, or
    None when the Source gives none.
    """

    evidence_id: EvidenceId
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_link: str | None = field(repr=False)
    excerpt: str = field(repr=False)
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        identifiers = (
            (self.chunk_id, ChunkId),
            (self.document_id, DocumentId),
            (self.source_id, SourceId),
        )
        if (
            type(self.evidence_id) is not EvidenceId
            or not all(
                type(value) is kind and type(value.value) is UUID
                for value, kind in identifiers
            )
            or type(self.content_version) is not ContentVersion
            or type(self.ordinal) is not int
            or self.ordinal < 0
            or type(self.title) is not str
            or not (self.heading_path is None or type(self.heading_path) is str)
            or not (self.source_link is None or type(self.source_link) is str)
            or type(self.excerpt) is not str
            or type(self.text) is not str
            or not self.text.strip()
        ):
            raise InvalidEvidence("an Evidence field has the wrong type")


@dataclass(frozen=True, slots=True)
class EvidenceSet:
    """The Evidence of one request, in retrieval's order."""

    items: tuple[Evidence, ...]

    def __post_init__(self) -> None:
        if type(self.items) is not tuple or not all(
            type(item) is Evidence for item in self.items
        ):
            raise InvalidEvidence("an EvidenceSet holds a tuple of Evidence")
        if len(self.items) > MAX_EVIDENCE:
            raise InvalidEvidence(f"an EvidenceSet holds at most {MAX_EVIDENCE} items")
        if len({item.evidence_id for item in self.items}) != len(self.items):
            raise InvalidEvidence("an Evidence ID appears twice")
        if len({item.chunk_id for item in self.items}) != len(self.items):
            raise InvalidEvidence("a Chunk appears twice")

    def get(self, evidence_id: EvidenceId) -> Evidence | None:
        """Return the Evidence with this ID, or None if it is not in this set."""
        for item in self.items:
            if item.evidence_id == evidence_id:
                return item
        return None


def build_evidence(candidates: Iterable[Candidate]) -> EvidenceSet:
    """Give each Candidate a fresh opaque ID and keep its provenance.

    Candidates keep retrieval's order. Raises `InvalidEvidence` for more
    than `MAX_EVIDENCE` Candidates, the same Chunk twice, or a Candidate
    whose fields are not what retrieval returns.
    """
    # Checked at run time too, because a caller may pass any object.
    if isinstance(candidates, (str, bytes)) or not isinstance(
        cast(object, candidates), Iterable
    ):
        raise InvalidEvidence("candidates must be a sequence")
    read = tuple(itertools.islice(candidates, MAX_EVIDENCE + 1))
    if len(read) > MAX_EVIDENCE:
        raise InvalidEvidence(f"at most {MAX_EVIDENCE} Candidates become Evidence")
    ids: list[EvidenceId] = []
    while len(ids) < len(read):
        new = EvidenceId.new()
        if new not in ids:
            ids.append(new)
    return EvidenceSet(
        tuple(
            _evidence(evidence_id, candidate)
            for evidence_id, candidate in zip(ids, read, strict=True)
        )
    )


def _evidence(evidence_id: EvidenceId, candidate: Candidate) -> Evidence:
    """Copy one Candidate's provenance; any surprise is `InvalidEvidence`."""
    evidence: Evidence | None = None
    try:
        text = _text(candidate.text)
        evidence = Evidence(
            evidence_id=evidence_id,
            chunk_id=ChunkId(_uuid(candidate.chunk_id, ChunkId)),
            document_id=DocumentId(_uuid(candidate.document_id, DocumentId)),
            source_id=SourceId(_uuid(candidate.source_id, SourceId)),
            content_version=candidate.content_version,
            ordinal=candidate.ordinal,
            title=_shown(candidate.title),
            heading_path=_optional(candidate.heading_path),
            source_link=_optional(candidate.source_anchor),
            excerpt=display_excerpt(text[:_EXCERPT_SOURCE_CHARACTERS]),
            text=text,
        )
    except Exception:  # noqa: S110 - a Candidate's code may raise anything
        # A property may raise with Document text in its message; nothing of
        # it is kept, not even as a chained error.
        pass
    if evidence is None:
        raise InvalidEvidence("a Candidate's provenance is not what retrieval returns")
    return evidence


def _uuid(identifier: object, kind: type[StableIdentifier]) -> UUID:
    """The UUID inside an identifier of exactly this kind."""
    value = getattr(identifier, "value", None)
    if type(identifier) is not kind or type(value) is not UUID:
        raise InvalidEvidence("an identifier must be of its kind and hold a UUID")
    return value


def _text(value: object) -> str:
    if type(value) is not str:
        raise InvalidEvidence("text must be plain str")
    return value


def _shown(value: object) -> str:
    return display_text(_text(value))


def _optional(value: object) -> str | None:
    return None if value is None else _shown(value)
