"""Calibrated abstention: evidence signals, one decision, and its evaluation."""

import math
import time
from dataclasses import replace
from fractions import Fraction
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.abstention import (
    AbstentionDecision,
    AbstentionPolicy,
    AbstentionReason,
    AbstentionScore,
    CaseOutcome,
    Decision,
    EvidenceSignals,
    InvalidAbstentionInput,
    Sufficiency,
    UncalibratedScore,
    abstention_message,
    assess_evidence,
    calibrate_threshold,
    classify,
    decide,
    find_conflicts,
    score_abstention,
)
from knowledge_service.answering.citations import resolve_citations
from knowledge_service.answering.context_budget import ContextBudget, allocate_context
from knowledge_service.answering.evidence import EvidenceSet, build_evidence
from knowledge_service.answering.generation import AnswerOutcome, AnswerResult
from knowledge_service.answering.support import LexicalSupportJudge, check_support
from knowledge_service.contracts import Usage
from knowledge_service.language import Language
from knowledge_service.retrieval.reranking import RerankerIdentity

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000048")
RERANKER = RerankerIdentity(provider="local", model="bge-reranker-v2-m3")
POLICY = AbstentionPolicy(reranker=RERANKER, min_top_score=0.2, min_supported_share=1.0)
USAGE = Usage(
    provider="fake",
    model="fake-1",
    latency_ms=5,
    input_tokens=300,
    output_tokens=30,
    total_tokens=330,
)
LEAVE = "Every employee receives 24 days of annual leave."
OLD_LEAVE = "Every employee receives 20 days of annual leave."
CARRY = "Unused annual leave carries over for 5 days."


def evidence(*texts: str) -> EvidenceSet:
    return build_evidence(
        [candidate(n, text=text) for n, text in enumerate(texts, start=1)]
    )


def answered(text: str, cited: EvidenceSet) -> AnswerResult:
    return AnswerResult(
        outcome=AnswerOutcome.ANSWERED,
        text=text,
        reason=None,
        citations=resolve_citations(
            cited, [item.evidence_id.value for item in cited.items]
        ),
        defect=None,
        usage=USAGE,
        context=allocate_context(
            "Leave?", cited, ContextBudget(32_000, 1_000, 0)
        ).report,
    )


async def decide_answer(text: str, cited: EvidenceSet) -> AbstentionDecision:
    """Check support with the lexical judge, then decide."""
    result = answered(text, cited)
    support = await check_support(
        result,
        cited,
        LexicalSupportJudge(),
        request_id=REQUEST_ID,
        timeout_seconds=5,
    )
    return decide(Sufficiency.SUFFICIENT, result, cited, support, POLICY)


def signals(eligible: int = 3, top_score: float | None = 0.8) -> EvidenceSignals:
    return EvidenceSignals(
        eligible=eligible,
        top_score=top_score,
        reranker=None if top_score is None else RERANKER,
    )


def test_retrieval_is_judged_before_any_model_call() -> None:
    assert assess_evidence(signals(), POLICY) is Sufficiency.SUFFICIENT
    assert assess_evidence(signals(eligible=0), POLICY) is Sufficiency.EMPTY
    assert assess_evidence(signals(top_score=0.05), POLICY) is Sufficiency.WEAK
    # The threshold itself is enough.
    assert assess_evidence(signals(top_score=0.2), POLICY) is Sufficiency.SUFFICIENT


def test_without_a_threshold_any_eligible_evidence_is_sufficient() -> None:
    policy = AbstentionPolicy(reranker=None, min_top_score=None, min_supported_share=1)

    assert assess_evidence(signals(top_score=None), policy) is Sufficiency.SUFFICIENT


@pytest.mark.parametrize(
    "given_signals",
    [
        EvidenceSignals(eligible=3, top_score=0.9, reranker=None),
        EvidenceSignals(
            eligible=3,
            top_score=0.9,
            reranker=RerankerIdentity(provider="openai", model="gpt-5.4-mini"),
        ),
        EvidenceSignals(eligible=3, top_score=None, reranker=RERANKER),
        EvidenceSignals(eligible=3, top_score=float("nan"), reranker=RERANKER),
    ],
    ids=["no-reranker", "other-reranker", "no-score", "nan-score"],
)
def test_a_score_the_threshold_was_not_calibrated_for_is_refused(
    given_signals: EvidenceSignals,
) -> None:
    with pytest.raises(UncalibratedScore):
        assess_evidence(given_signals, POLICY)


def test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing() -> None:
    # Retrieval returns only what the reader may see: a question about a
    # hidden Document finds nothing, exactly like a question about nothing.
    empty = decide(Sufficiency.EMPTY, None, None, None, POLICY)
    cited = evidence(LEAVE)
    withdrawn = decide(
        Sufficiency.SUFFICIENT,
        replace(answered("x.", cited), outcome=AnswerOutcome.WITHDRAWN, citations=()),
        None,
        None,
        POLICY,
    )

    assert empty == withdrawn
    assert empty.reason is AbstentionReason.NO_EVIDENCE


async def test_a_supported_answer_is_answered() -> None:
    decision = await decide_answer("You get 24 days of annual leave.", evidence(LEAVE))

    assert decision == AbstentionDecision(
        decision=Decision.ANSWER, reason=None, policy_version="1"
    )


async def test_an_answer_with_an_unsupported_claim_abstains() -> None:
    decision = await decide_answer(
        "You get 24 days of annual leave. Unused leave carries over for 9 days.",
        evidence(LEAVE, CARRY),
    )

    assert decision.reason is AbstentionReason.UNSUPPORTED


async def test_documents_that_disagree_on_the_number_abstain() -> None:
    cited = evidence(LEAVE, OLD_LEAVE)

    decision = await decide_answer("You get 24 days of annual leave.", cited)

    assert find_conflicts("You get 24 days of annual leave.", cited) == (0,)
    assert decision.reason is AbstentionReason.CONFLICTING_EVIDENCE


def test_one_document_never_conflicts_with_itself() -> None:
    cited = build_evidence(
        [
            candidate(1, text=LEAVE),
            candidate(2, text=OLD_LEAVE, document_id=candidate(1).document_id),
        ]
    )

    assert find_conflicts("You get 24 days of annual leave.", cited) == ()


@pytest.mark.parametrize(
    "other",
    ["A leave request takes 3 days to approve.", CARRY],
    ids=["request-time", "carry-over"],
)
def test_a_sentence_about_something_else_is_no_conflict(other: str) -> None:
    # Both share the claim's words "days", "annual", and "leave", but say
    # something else, so their different number is no disagreement.
    cited = evidence(LEAVE, other)

    assert find_conflicts("You get 24 days of annual leave.", cited) == ()


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (AnswerOutcome.ABSTAINED, AbstentionReason.INSUFFICIENT_EVIDENCE),
        (AnswerOutcome.REFUSED, AbstentionReason.ANSWER_FAILED),
        (AnswerOutcome.INVALID, AbstentionReason.ANSWER_FAILED),
        (AnswerOutcome.NO_EVIDENCE, AbstentionReason.NO_EVIDENCE),
        (AnswerOutcome.WITHDRAWN, AbstentionReason.NO_EVIDENCE),
    ],
)
def test_every_outcome_that_is_not_an_answer_abstains_with_its_reason(
    outcome: AnswerOutcome, reason: AbstentionReason
) -> None:
    result = replace(answered("x.", evidence(LEAVE)), outcome=outcome, citations=())

    decision = decide(Sufficiency.SUFFICIENT, result, None, None, POLICY)

    assert (decision.decision, decision.reason) == (Decision.ABSTAIN, reason)


def test_weak_evidence_abstains_even_if_the_model_answered() -> None:
    cited = evidence(LEAVE)
    result = answered("You get 24 days of annual leave.", cited)

    decision = decide(Sufficiency.WEAK, result, cited, None, POLICY)

    assert decision.reason is AbstentionReason.WEAK_EVIDENCE


def test_an_answer_whose_support_was_not_checked_abstains() -> None:
    cited = evidence(LEAVE)

    decision = decide(
        Sufficiency.SUFFICIENT,
        answered("You get 24 days of annual leave.", cited),
        cited,
        None,
        POLICY,
    )

    assert decision.reason is AbstentionReason.UNSUPPORTED


@pytest.mark.parametrize("reason", list(AbstentionReason))
def test_each_reason_has_a_fixed_message_in_both_languages(
    reason: AbstentionReason,
) -> None:
    english = abstention_message(reason, Language.ENGLISH)
    ukrainian = abstention_message(reason, Language.UKRAINIAN)

    assert english != ukrainian
    assert abstention_message(reason, Language.UNKNOWN) == english
    assert MARKER not in english + ukrainian


@pytest.mark.parametrize(
    "build",
    [
        lambda: AbstentionPolicy(
            reranker=RERANKER, min_top_score=None, min_supported_share=1
        ),
        lambda: AbstentionPolicy(
            reranker=None, min_top_score=0.2, min_supported_share=1
        ),
        lambda: AbstentionPolicy(
            reranker=RERANKER, min_top_score=float("inf"), min_supported_share=1
        ),
        lambda: AbstentionPolicy(
            reranker=None, min_top_score=None, min_supported_share=1.5
        ),
        lambda: AbstentionPolicy(
            reranker=None, min_top_score=None, min_supported_share=0
        ),
        lambda: AbstentionPolicy(
            reranker=None, min_top_score=None, min_supported_share=True
        ),
        lambda: AbstentionPolicy(
            reranker=None, min_top_score=None, min_supported_share=1, version=""
        ),
    ],
    ids=[
        "threshold-missing",
        "reranker-missing",
        "infinite",
        "share-above-one",
        "share-zero",
        "bool-share",
        "no-version",
    ],
)
def test_a_policy_that_cannot_work_is_refused(build: Any) -> None:
    with pytest.raises(InvalidAbstentionInput):
        build()


def test_decide_refuses_an_answer_without_its_inputs() -> None:
    with pytest.raises(InvalidAbstentionInput):
        decide(Sufficiency.SUFFICIENT, None, None, None, POLICY)
    with pytest.raises(InvalidAbstentionInput):
        decide("sufficient", None, None, None, POLICY)  # type: ignore[arg-type]
    with pytest.raises(InvalidAbstentionInput):
        decide(
            Sufficiency.SUFFICIENT,
            answered("You get 24 days.", evidence(LEAVE)),
            None,
            None,
            POLICY,
        )


ANSWER = AbstentionDecision(decision=Decision.ANSWER, reason=None, policy_version="1")
ABSTAIN = AbstentionDecision(
    decision=Decision.ABSTAIN,
    reason=AbstentionReason.NO_EVIDENCE,
    policy_version="1",
)


def test_each_decision_is_classified_against_its_label() -> None:
    assert classify(True, ANSWER) is CaseOutcome.CORRECT_ANSWER
    assert classify(False, ABSTAIN) is CaseOutcome.CORRECT_ABSTENTION
    assert classify(True, ABSTAIN) is CaseOutcome.OVER_ABSTENTION
    assert classify(False, ANSWER) is CaseOutcome.UNSUPPORTED_ANSWER


async def test_evaluation_cases_distinguish_the_four_outcomes() -> None:
    # Each case: the answerability label, and what the pipeline produced.
    cases = [
        # A plain answerable question, answered and supported.
        (
            True,
            await decide_answer("You get 24 days of annual leave.", evidence(LEAVE)),
        ),
        # Nothing the reader may see: abstaining is right.
        (False, decide(Sufficiency.EMPTY, None, None, None, POLICY)),
        # Answerable, but the reranker scored it low: an over-abstention.
        (True, decide(Sufficiency.WEAK, None, None, None, POLICY)),
        # Unanswerable, yet the model wrote a number its Evidence holds:
        # the support check passes, so this is an unsupported answer.
        (
            False,
            await decide_answer(
                "Contractors get 24 days of annual leave.", evidence(LEAVE)
            ),
        ),
        # The model invented a number: support stops it.
        (
            False,
            await decide_answer("You get 30 days of annual leave.", evidence(LEAVE)),
        ),
    ]

    score = score_abstention(cases)

    assert score == AbstentionScore(
        correct_answers=1,
        correct_abstentions=2,
        over_abstentions=1,
        unsupported_answers=1,
    )
    assert score.total == len(cases)


def test_the_threshold_answers_fewest_unanswerable_within_its_budget() -> None:
    cases = [
        (0.95, True),
        (0.80, True),
        (0.60, True),
        (0.35, False),
        (0.30, True),
        (0.20, False),
        (0.10, False),
    ]

    strict = calibrate_threshold(cases, max_over_abstention=0.25)
    lenient = calibrate_threshold(cases, max_over_abstention=0.0)

    # Abstaining below 0.6 loses one answerable case of four (25%) and
    # answers no unanswerable one.
    assert strict == 0.60
    # With no over-abstention allowed, only the lowest answerable score works.
    assert lenient == 0.30
    policy = AbstentionPolicy(
        reranker=RERANKER, min_top_score=strict, min_supported_share=1
    )
    over = [s for s, label in cases if label and s < policy.min_top_score]  # type: ignore[operator]
    assert len(over) == 1


@pytest.mark.parametrize(
    ("cases", "budget"),
    [
        ([(0.9, True), (0.1, True)], 0.1),
        ([(0.9, False)], 0.1),
        ([(0.9, True), (float("nan"), False)], 0.1),
        ([(0.9, True), (0.1, "no")], 0.1),
        ([(0.9, True), [0.1, False]], 0.1),
        ([(0.9, True), (0.1, False)], 1.5),
    ],
    ids=[
        "only-answerable",
        "only-unanswerable",
        "nan",
        "text-label",
        "a-list",
        "budget",
    ],
)
def test_calibration_refuses_cases_it_cannot_use(cases: Any, budget: float) -> None:
    with pytest.raises(InvalidAbstentionInput):
        calibrate_threshold(cases, max_over_abstention=budget)


@given(
    cases=st.lists(
        st.tuples(st.floats(-10, 10, allow_nan=False), st.booleans()),
        min_size=2,
        max_size=40,
    ),
    budget=st.floats(0, 1),
)
def test_the_calibrated_threshold_is_optimal_within_its_budget(
    cases: list[tuple[float, bool]], budget: float
) -> None:
    if all(label for _, label in cases) or not any(label for _, label in cases):
        return

    threshold = calibrate_threshold(cases, max_over_abstention=budget)

    def counts(t: float) -> tuple[int, int]:
        over = sum(1 for s, label in cases if label and s < t)
        unsupported = sum(1 for s, label in cases if not label and s >= t)
        return over, unsupported

    answerable = sum(1 for _, label in cases if label)
    allowed = math.floor(Fraction(repr(budget)) * answerable)
    over, unsupported = counts(threshold)
    assert over <= allowed
    for other in {s for s, _ in cases} | {math.inf}:
        other_over, other_unsupported = counts(other)
        if other_over <= allowed:
            assert unsupported <= other_unsupported


async def test_a_support_report_of_another_text_is_no_support() -> None:
    cited = evidence(LEAVE)
    checked = answered("You get 24 days of annual leave.", cited)
    support = await check_support(
        checked, cited, LexicalSupportJudge(), request_id=REQUEST_ID, timeout_seconds=5
    )
    other = answered("You get 99 days of annual leave and a free car.", cited)

    decision = decide(Sufficiency.SUFFICIENT, other, cited, support, POLICY)

    assert decision.reason is AbstentionReason.UNSUPPORTED


async def test_a_support_report_with_missing_claims_is_no_support() -> None:
    cited = evidence(LEAVE)
    result = answered("You get 24 days of annual leave.", cited)
    support = await check_support(
        result, cited, LexicalSupportJudge(), request_id=REQUEST_ID, timeout_seconds=5
    )

    decision = decide(
        Sufficiency.SUFFICIENT, result, cited, replace(support, findings=()), POLICY
    )

    assert decision.reason is AbstentionReason.UNSUPPORTED


async def test_claims_the_judge_never_checked_count_as_unsupported() -> None:
    # 20 claims are judged; the five after them never are.
    text = (
        " ".join(["You get 24 days of annual leave."] * 20)
        + " "
        + " ".join(["Every employee is paid 1000000 euros."] * 5)
    )
    policy = AbstentionPolicy(
        reranker=None, min_top_score=None, min_supported_share=0.9
    )
    cited = evidence(LEAVE)
    result = answered(text, cited)
    support = await check_support(
        result, cited, LexicalSupportJudge(), request_id=REQUEST_ID, timeout_seconds=5
    )

    decision = decide(Sufficiency.SUFFICIENT, result, cited, support, policy)

    assert decision.reason is AbstentionReason.UNSUPPORTED


def test_a_number_only_one_document_adds_is_no_conflict() -> None:
    cited = evidence(
        LEAVE, "Every employee receives 24 days of annual leave since 2024."
    )

    assert find_conflicts("You get 24 days of annual leave.", cited) == ()


def test_a_claim_with_one_extra_word_still_finds_the_conflict() -> None:
    cited = evidence(LEAVE, OLD_LEAVE)

    assert find_conflicts("You get 24 paid days of annual leave.", cited) == (0,)


def test_finding_conflicts_in_long_repeated_evidence_is_fast() -> None:
    # Twenty Documents that all agree: no pair stops the search early.
    cited = evidence(*["Every employee receives 24 days of annual leave. " * 200] * 20)
    started = time.perf_counter()

    conflicts = find_conflicts(
        " ".join(["You get 24 days of annual leave."] * 5), cited
    )

    assert conflicts == ()
    assert time.perf_counter() - started < 1.0


def test_the_budget_is_read_as_the_decimal_it_was_written_as() -> None:
    cases = [(0.0, True)] * 29 + [(1.0, True)] * 71 + [(0.0, False)] * 10

    # 29 of 100 answerable cases is exactly 29%, within the budget.
    assert calibrate_threshold(cases, max_over_abstention=0.29) == 1.0


def test_with_the_whole_budget_the_threshold_may_abstain_on_everything() -> None:
    threshold = calibrate_threshold([(0.5, True), (0.9, False)], max_over_abstention=1)

    assert threshold > 0.9


class LyingIdentity:
    """Claims to equal any reranker."""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    __hash__ = object.__hash__


def test_a_forged_reranker_identity_cannot_borrow_the_threshold() -> None:
    forged = EvidenceSignals(eligible=3, top_score=0.9, reranker=LyingIdentity())  # type: ignore[arg-type]

    with pytest.raises(UncalibratedScore):
        assess_evidence(forged, POLICY)


@pytest.mark.parametrize("item", [(True,), (True, ANSWER, 1), None, [True, ANSWER]])
def test_a_case_that_is_not_a_pair_is_refused(item: object) -> None:
    with pytest.raises(InvalidAbstentionInput):
        score_abstention([item])  # type: ignore[list-item]
