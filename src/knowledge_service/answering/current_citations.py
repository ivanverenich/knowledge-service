"""Confirm an Answer's Citations against the Documents as they are now.

Retrieval checks permissions when it ranks. The model then takes seconds
to write the Answer, and in that time a Document can be deleted, rewritten,
renamed, or taken away from the reader. `confirm_citations` runs after
generation and before anything reaches the client, and asks the database
once more about every cited Chunk.

What `confirm_citations` promises:

- **Checked shape first.** The context, the result, its outcome, and every
  Citation must be of exactly the types generation builds, with UUIDs
  inside the identifiers; otherwise `TypeError`, before any SQL. A result
  that is not `ANSWERED` must hold no Citations.
- **Only Answers are confirmed.** Any other outcome is returned unchanged,
  and the database is not asked.
- **Every Citation is still allowed.** One statement selects the cited
  Chunks under `eligible_chunks_predicate`, the same permission check
  retrieval uses, for the same `AuthorizationContext`. A Citation counts
  only when its Chunk still exists in the same Document, Source, and
  content version, the Document is available, and the reader may see it.
- **All or nothing.** If even one Citation fails, the whole Answer is
  withdrawn: the outcome becomes `WITHDRAWN`, with no text, no reason, and
  no Citations. The text was written from that Evidence, so showing it
  without the Citation would still show what the reader may no longer
  see. The usage and the context report are kept, since the call was paid.
- **Provenance from the stored rows.** A confirmed Citation is rebuilt from
  its row: identifiers, version, title, heading path, source link, and
  excerpt, cleaned for display as Evidence is. Only its Evidence ID comes
  from the result. So a renamed Document is cited under its new title,
  and nothing a caller put into a Citation reaches the client.
- **Same answer to every failure.** A deleted Chunk, a new version, a
  tombstoned Document, and a revoked grant all give `WITHDRAWN`, so the
  client cannot learn which one happened, or that a Document exists.
- **Quiet.** One log record per check holds the request ID and counts;
  never a title, a link, or text.

The caller passes a connection for a short transaction of its own; it
must not hold one open while the model writes.
"""

import logging
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.citations import Citation
from knowledge_service.answering.evidence import (
    EXCERPT_SOURCE_CHARACTERS,
    MAX_EVIDENCE,
    EvidenceId,
)
from knowledge_service.answering.generation import AnswerOutcome, AnswerResult
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.ingestion.provenance import display_excerpt, display_text
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks, documents

logger = logging.getLogger(__name__)


async def confirm_citations(
    connection: AsyncConnection,
    context: AuthorizationContext,
    result: AnswerResult,
    *,
    request_id: UUID,
) -> AnswerResult:
    """Return the Answer with current provenance, or withdraw it.

    Raises `TypeError` when `context` or `result` is not what the request
    and generation produce.
    """
    if type(context) is not AuthorizationContext:
        raise TypeError("context must be an AuthorizationContext")
    if type(result) is not AnswerResult or type(result.outcome) is not AnswerOutcome:
        raise TypeError("result must be an AnswerResult")
    if result.outcome is not AnswerOutcome.ANSWERED:
        if result.citations != ():
            raise TypeError("only an Answer holds Citations")
        return result
    cited = _checked(result.citations)
    statement = current_citations_statement(
        [citation.chunk_id.value for citation in cited],
        eligibility=eligible_chunks_predicate(context),
    )
    records = (await connection.execute(statement)).mappings().all()
    rows = {cast(UUID, record["chunk_id"]): dict(record) for record in records}
    confirmed = [
        _current(citation, rows.get(citation.chunk_id.value)) for citation in cited
    ]
    withdrawn = sum(citation is None for citation in confirmed)
    if withdrawn:
        result = AnswerResult(
            outcome=AnswerOutcome.WITHDRAWN,
            text="",
            reason=None,
            citations=(),
            defect=None,
            usage=result.usage,
            context=result.context,
        )
    else:
        result = AnswerResult(
            outcome=result.outcome,
            text=result.text,
            reason=result.reason,
            citations=tuple(cast(list[Citation], confirmed)),
            defect=result.defect,
            usage=result.usage,
            context=result.context,
        )
    _log(request_id, cited=len(cited), withdrawn=withdrawn, outcome=result.outcome)
    return result


def current_citations_statement(
    chunk_ids: Sequence[UUID], *, eligibility: sa.ColumnElement[bool]
) -> sa.Select[*tuple[Any, ...]]:
    """Select the cited Chunks that are current and meet `eligibility`.

    The IDs are one array parameter, however many there are. A Chunk is
    current when its version is its Document's; replacing a version gives
    its Chunks new IDs, so an old ID finds nothing. Only the start of the
    text is read, as much as an excerpt needs.
    """
    return (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            documents.c.source_id,
            chunks.c.content_version,
            documents.c.title,
            chunks.c.heading_path,
            chunks.c.source_anchor,
            sa.func.left(chunks.c.text, EXCERPT_SOURCE_CHARACTERS).label("opening"),
        )
        .select_from(chunks)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(
            chunks.c.chunk_id
            == sa.any_(
                sa.bindparam(
                    "chunk_ids", list(chunk_ids), type_=ARRAY(sa.Uuid()), unique=True
                )
            ),
            chunks.c.content_version == documents.c.content_version,
            eligibility,
        )
    )


def _checked(citations: object) -> tuple[Citation, ...]:
    """The result's Citations, if they are exactly what generation builds."""
    if type(citations) is not tuple:
        raise TypeError("Citations must be a tuple")
    items = cast(tuple[object, ...], citations)
    if not 1 <= len(items) <= MAX_EVIDENCE:
        raise TypeError(f"an Answer holds 1 to {MAX_EVIDENCE} Citations")
    if not all(_well_formed(item) for item in items):
        raise TypeError("a Citation is not what generation builds")
    cited = cast(tuple[Citation, ...], items)
    if len({c.chunk_id.value for c in cited}) != len(cited) or len(
        {c.evidence_id.value for c in cited}
    ) != len(cited):
        raise TypeError("an Answer cites each Evidence and Chunk once")
    return cited


def _well_formed(item: object) -> bool:
    """A Citation whose identifiers hold exactly a UUID, a str, or an int."""
    if type(item) is not Citation:
        return False
    identifiers = (
        (item.chunk_id, ChunkId),
        (item.document_id, DocumentId),
        (item.source_id, SourceId),
    )
    return (
        type(item.evidence_id) is EvidenceId
        and type(item.evidence_id.value) is str
        and all(
            type(value) is kind and type(value.value) is UUID
            for value, kind in identifiers
        )
        and type(item.content_version) is ContentVersion
        and type(item.content_version.number) is int
    )


def _current(citation: Citation, row: dict[str, object] | None) -> Citation | None:
    """The Citation rebuilt from its stored row, or None if it is not current."""
    if (
        row is None
        or row["document_id"] != citation.document_id.value
        or row["source_id"] != citation.source_id.value
        or row["content_version"] != citation.content_version.number
    ):
        return None
    return Citation(
        evidence_id=citation.evidence_id,
        chunk_id=ChunkId(cast(UUID, row["chunk_id"])),
        document_id=DocumentId(cast(UUID, row["document_id"])),
        source_id=SourceId(cast(UUID, row["source_id"])),
        content_version=ContentVersion(cast(int, row["content_version"])),
        title=display_text(cast(str, row["title"])),
        heading_path=_shown(row["heading_path"]),
        source_link=_shown(row["source_anchor"]),
        excerpt=display_excerpt(cast(str, row["opening"])),
    )


def _shown(value: object) -> str | None:
    return None if value is None else display_text(cast(str, value))


def _log(
    request_id: UUID, *, cited: int, withdrawn: int, outcome: AnswerOutcome
) -> None:
    """Record one check: the request, counts, and the outcome. No text."""
    fields = {
        "request_id": str(request_id),
        "cited": cited,
        "withdrawn": withdrawn,
        "outcome": outcome.value,
    }
    level = logging.WARNING if withdrawn else logging.INFO
    logger.log(level, "citations confirmed", extra={"citation_check": fields})
