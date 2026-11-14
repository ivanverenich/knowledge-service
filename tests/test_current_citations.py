"""Confirming Citations: what is checked before the database is asked."""

from dataclasses import replace
from uuid import UUID

import pytest

from evidence_fakes import MARKER, candidate
from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.citations import resolve_citations
from knowledge_service.answering.context_budget import ContextBudget, allocate_context
from knowledge_service.answering.current_citations import (
    confirm_citations,
    current_citations_statement,
)
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import AnswerOutcome, AnswerResult
from knowledge_service.identifiers import ChunkId, DocumentId, UserId
from knowledge_service.persistence.access import eligible_chunks_predicate

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000046")
CONTEXT = AuthorizationContext(user_id=UserId(UUID(int=7)))
EVIDENCE = build_evidence([candidate(1), candidate(2)])
FIRST, SECOND = (item.evidence_id.value for item in EVIDENCE.items)
REPORT = allocate_context("Leave?", EVIDENCE, ContextBudget(32_000, 1_000, 0)).report


class NoConnection:
    """Fails the test if anything asks the database."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the database was used: {name}")


def result(outcome: AnswerOutcome, citations: tuple[object, ...]) -> AnswerResult:
    return AnswerResult(
        outcome=outcome,
        text="You get 24 days.",
        reason=None,
        citations=citations,  # type: ignore[arg-type]
        defect=None,
        usage=None,
        context=REPORT,
    )


@pytest.mark.parametrize(
    "outcome",
    [o for o in AnswerOutcome if o is not AnswerOutcome.ANSWERED],
)
async def test_an_outcome_that_is_not_an_answer_passes_without_a_query(
    outcome: AnswerOutcome,
) -> None:
    given = result(outcome, ())

    confirmed = await confirm_citations(
        NoConnection(),  # type: ignore[arg-type]
        CONTEXT,
        given,
        request_id=REQUEST_ID,
    )

    assert confirmed is given


CITED = resolve_citations(EVIDENCE, [FIRST, SECOND])


class LyingUUID(UUID):
    """A UUID that claims to equal anything."""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    __hash__ = UUID.__hash__


def answered(*citations: object) -> AnswerResult:
    return result(AnswerOutcome.ANSWERED, citations)


@pytest.mark.parametrize(
    ("context", "given"),
    [
        (None, result(AnswerOutcome.ANSWERED, CITED)),
        (CONTEXT, object()),
        (CONTEXT, result(AnswerOutcome.ANSWERED, ())),
        (CONTEXT, result(AnswerOutcome.ANSWERED, (CITED[0], CITED[0]))),
        (CONTEXT, replace(answered(*CITED), outcome="answered")),
        (CONTEXT, result(AnswerOutcome.ABSTAINED, CITED)),
        (
            CONTEXT,
            answered(replace(CITED[0], document_id=DocumentId(LyingUUID(int=9)))),
        ),
        (CONTEXT, answered(replace(CITED[0], chunk_id=ChunkId(MARKER)))),  # type: ignore[arg-type]
        (CONTEXT, answered(replace(CITED[0], content_version=3))),
        (
            CONTEXT,
            answered(CITED[0], replace(CITED[1], evidence_id=CITED[0].evidence_id)),
        ),
        (CONTEXT, result(AnswerOutcome.ANSWERED, (CITED[0], "E00000001"))),
        (CONTEXT, result(AnswerOutcome.ANSWERED, CITED[:1] * 21)),
        (
            CONTEXT,
            result(
                AnswerOutcome.ANSWERED,
                (CITED[0], replace(CITED[1], chunk_id=CITED[0].chunk_id)),
            ),
        ),
    ],
    ids=[
        "no-context",
        "not-a-result",
        "answer-without-citations",
        "same-citation-twice",
        "outcome-as-text",
        "abstention-with-citations",
        "lying-uuid",
        "text-as-chunk-id",
        "plain-int-version",
        "same-evidence-id-twice",
        "not-a-citation",
        "too-many",
        "same-chunk-twice",
    ],
)
async def test_a_broken_request_is_refused_before_the_database(
    context: object, given: object
) -> None:
    with pytest.raises(TypeError) as caught:
        await confirm_citations(
            NoConnection(),  # type: ignore[arg-type]
            context,  # type: ignore[arg-type]
            given,  # type: ignore[arg-type]
            request_id=REQUEST_ID,
        )

    assert MARKER not in str(caught.value)


def test_the_statement_checks_permission_version_and_ids_in_one_query() -> None:
    statement = current_citations_statement(
        [CITED[0].chunk_id.value], eligibility=eligible_chunks_predicate(CONTEXT)
    )

    sql = str(statement)
    assert sql.count("SELECT") == 2  # the query and the grant check inside it
    assert "chunks.content_version = documents.content_version" in sql
    assert "documents.availability" in sql
    assert "ANY (" in sql
