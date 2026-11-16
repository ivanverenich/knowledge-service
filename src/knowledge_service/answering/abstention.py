"""When to answer and when to abstain, and how to measure that choice.

An Answer is only as good as its Evidence. This module turns what the
pipeline already knows into one decision: answer, or abstain with a
reason the user is told in plain words.

What the module promises:

- **Evidence first, before any model call.** `assess_evidence` looks at
  retrieval's result: no eligible Evidence is `EMPTY`; a best reranker
  score below the policy's calibrated threshold is `WEAK`. Either means
  the model need not be called at all.
- **Empty and unauthorized look the same.** Retrieval only ever returns
  Chunks the reader may see, so a question about a Document the reader may
  not see finds nothing, exactly like a question about nothing. A
  withdrawn Answer (see `answering.current_citations`) is told the same
  way. The user is never told that something exists but is hidden.
- **Scores are compared only where they mean something.** A reranker
  score has no fixed scale; the threshold belongs to one `RerankerIdentity`.
  A score from another reranker, or no score where the policy needs one,
  raises `UncalibratedScore` instead of guessing.
- **Conflicts are found in the cited Evidence.** `find_conflicts` reports
  a claim when two cited sentences from different Documents say nearly the
  same thing as each other and as the claim, but disagree on the claim's
  numbers, such as an old and a new policy. It compares words, not
  meaning, so it is a cheap first signal, not a proof: an inflected word
  or a number written two ways ("1,000" and "1000") can fool it.
- **One decision, with one reason.** `decide` checks, in order: no
  Evidence, weak Evidence, a refused, invalid, or withdrawn Answer, the
  model's own abstention, conflicting Evidence, then claim support: a
  missing or failed support report, a report of another text (its SHA-256
  or its claims differ), or too small a share of supported claims among
  all claims, unchecked ones counted as unsupported, is `UNSUPPORTED`.
  Only an Answer that passes all of them is `ANSWER`. The decision names
  the policy version that made it.
- **Measured, not assumed.** `classify` compares a decision with an
  Evaluation Case's answerability label: a correct answer, a correct
  abstention, an over-abstention (abstained on an answerable question),
  or an unsupported answer (answered an unanswerable one).
  `score_abstention` counts them, and `calibrate_threshold` picks the
  reranker threshold that answers fewest unanswerable questions while
  over-abstaining on at most a given share of answerable ones; the share
  is read as the decimal it was written as, so 0.29 of 100 cases is 29.
- **Quiet.** Decisions hold reasons, counts, and versions, never text.
"""

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import cast

from knowledge_service.answering.evidence import EvidenceSet
from knowledge_service.answering.generation import AnswerOutcome, AnswerResult
from knowledge_service.answering.support import (
    MAX_CLAIMS,
    SupportOutcome,
    SupportReport,
    SupportVerdict,
    numbers_in,
    split_claims,
    topic_words,
)
from knowledge_service.language import Language
from knowledge_service.retrieval.reranking import RerankerIdentity

POLICY_VERSION = "1"
# A claim needs this many longer words before two sentences can be said to
# talk about the same thing, and a sentence must hold this share of them.
_CONFLICT_WORDS = 2
_CLAIM_SHARE = 0.6
# Two sentences say nearly the same thing when this share of all their
# longer words is common to both.
_SAME_TOPIC = 0.8


class InvalidAbstentionInput(ValueError):
    """A signal, policy, or case is not what the policy can use."""


class UncalibratedScore(InvalidAbstentionInput):
    """A score cannot be compared with the policy's threshold."""


class Sufficiency(StrEnum):
    """What retrieval's result says before any model is asked."""

    SUFFICIENT = "sufficient"
    EMPTY = "empty"
    WEAK = "weak"


class Decision(StrEnum):
    """What the user gets."""

    ANSWER = "answer"
    ABSTAIN = "abstain"


class AbstentionReason(StrEnum):
    """Why the user gets an abstention; each has one fixed message."""

    NO_EVIDENCE = "no_evidence"
    WEAK_EVIDENCE = "weak_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNSUPPORTED = "unsupported"
    ANSWER_FAILED = "answer_failed"


class CaseOutcome(StrEnum):
    """How one decision compares with an Evaluation Case's label."""

    CORRECT_ANSWER = "correct_answer"
    CORRECT_ABSTENTION = "correct_abstention"
    OVER_ABSTENTION = "over_abstention"
    UNSUPPORTED_ANSWER = "unsupported_answer"


@dataclass(frozen=True, slots=True)
class AbstentionPolicy:
    """The calibrated knobs of the decision.

    `min_top_score` is a threshold for scores of `reranker` only, or None
    to skip the weak check. `min_supported_share` is the share of judged
    claims that must be supported, from 0 to 1.
    """

    reranker: RerankerIdentity | None
    min_top_score: float | None
    min_supported_share: float
    version: str = POLICY_VERSION

    def __post_init__(self) -> None:
        if (self.reranker is None) != (self.min_top_score is None):
            raise InvalidAbstentionInput(
                "a score threshold needs its reranker, and a reranker its threshold"
            )
        if self.reranker is not None and type(self.reranker) is not RerankerIdentity:
            raise InvalidAbstentionInput("reranker must be a RerankerIdentity")
        if self.min_top_score is not None and not _finite(self.min_top_score):
            raise InvalidAbstentionInput("min_top_score must be a finite number")
        # Zero would answer when no claim at all is supported.
        if not _finite(self.min_supported_share) or not (
            0 < self.min_supported_share <= 1
        ):
            raise InvalidAbstentionInput(
                "min_supported_share must be above 0 and at most 1"
            )
        if type(self.version) is not str or not self.version:
            raise InvalidAbstentionInput("version must be non-empty text")


@dataclass(frozen=True, slots=True)
class EvidenceSignals:
    """What retrieval knows: how many eligible Chunks, and the best score.

    `top_score` and `reranker` are None when nothing was reranked.
    """

    eligible: int
    top_score: float | None
    reranker: RerankerIdentity | None


@dataclass(frozen=True, slots=True)
class AbstentionDecision:
    """The decision, why, and which policy version made it."""

    decision: Decision
    reason: AbstentionReason | None
    policy_version: str


@dataclass(frozen=True, slots=True)
class AbstentionScore:
    """How a set of decisions compares with their labels."""

    correct_answers: int
    correct_abstentions: int
    over_abstentions: int
    unsupported_answers: int

    @property
    def total(self) -> int:
        return (
            self.correct_answers
            + self.correct_abstentions
            + self.over_abstentions
            + self.unsupported_answers
        )


_MESSAGES: dict[AbstentionReason, tuple[str, str]] = {
    AbstentionReason.NO_EVIDENCE: (
        "I could not find documents that answer this question.",
        "Я не знайшов документів, які відповідають на це запитання.",
    ),
    AbstentionReason.WEAK_EVIDENCE: (
        "The documents I found do not answer this question reliably.",
        "Знайдені документи не дають надійної відповіді на це запитання.",
    ),
    AbstentionReason.CONFLICTING_EVIDENCE: (
        "The documents disagree on this, so I cannot give one reliable answer.",
        "Документи суперечать одне одному, тому я не можу дати надійну відповідь.",
    ),
    AbstentionReason.INSUFFICIENT_EVIDENCE: (
        "The documents do not contain enough to answer this.",
        # A Ukrainian sentence starts with the Cyrillic letter U.
        "У документах недостатньо інформації, щоб відповісти.",  # noqa: RUF001
    ),
    AbstentionReason.UNSUPPORTED: (
        "I could not verify an answer against the documents.",
        "Я не зміг перевірити відповідь за документами.",
    ),
    AbstentionReason.ANSWER_FAILED: (
        "I could not produce an answer right now.",
        "Зараз я не можу сформувати відповідь.",
    ),
}


def abstention_message(reason: AbstentionReason, language: Language) -> str:
    """The fixed message for `reason`, in Ukrainian or else in English."""
    if type(reason) is not AbstentionReason:
        raise InvalidAbstentionInput("reason must be an AbstentionReason")
    english, ukrainian = _MESSAGES[reason]
    return ukrainian if language is Language.UKRAINIAN else english


def assess_evidence(signals: EvidenceSignals, policy: AbstentionPolicy) -> Sufficiency:
    """Judge retrieval's result before any model is asked."""
    _check_policy(policy)
    if type(signals) is not EvidenceSignals:
        raise InvalidAbstentionInput("signals must be EvidenceSignals")
    if type(signals.eligible) is not int or signals.eligible < 0:
        raise InvalidAbstentionInput("eligible must be a whole number of zero or more")
    if signals.eligible == 0:
        return Sufficiency.EMPTY
    if policy.min_top_score is None:
        return Sufficiency.SUFFICIENT
    # The type first: a forged identity could claim to equal anything.
    if (
        type(signals.reranker) is not RerankerIdentity
        or signals.reranker != policy.reranker
        or signals.top_score is None
    ):
        raise UncalibratedScore("the score is not from the policy's reranker")
    if not _finite(signals.top_score):
        raise UncalibratedScore("the score must be a finite number")
    if signals.top_score < policy.min_top_score:
        return Sufficiency.WEAK
    return Sufficiency.SUFFICIENT


def find_conflicts(answer_text: str, cited: EvidenceSet) -> tuple[int, ...]:
    """The indexes of claims whose cited Documents disagree on a number.

    For each of the first `MAX_CLAIMS` claims with a number and at least
    two longer words, the cited sentences that hold at least 60% of those
    words are compared in pairs. Two sentences conflict when they come from
    different Documents, say nearly the same thing (at least 80% of their
    longer words in common), and hold different numbers of the claim, such
    as "24 days of annual leave" and "20 days of annual leave". A number
    only one of them adds, such as a year, is no disagreement, and
    sentences of one Document never conflict.
    """
    if type(answer_text) is not str or type(cited) is not EvidenceSet:
        raise InvalidAbstentionInput("an answer text and its cited EvidenceSet")
    # Each distinct sentence once: repeated text adds no pairs to compare.
    sentences = {
        (
            item.document_id,
            frozenset(topic_words(text)),
            frozenset(numbers_in(text)),
        )
        for item in cited.items
        for c in split_claims(item.text)
        if (text := item.text[c.start : c.end])
    }
    conflicts: list[int] = []
    for claim in split_claims(answer_text)[:MAX_CLAIMS]:
        text = answer_text[claim.start : claim.end]
        words = topic_words(text)
        numbers = numbers_in(text)
        if not numbers or len(words) < _CONFLICT_WORDS:
            continue
        # Each matching sentence by its Document, its words, and which of
        # the claim's numbers it holds.
        about = {
            (document, own, held & numbers)
            for document, own, held in sentences
            if held and len(words & own) >= _CLAIM_SHARE * len(words)
        }
        if len({d for d, _, _ in about}) < 2 or len({h for _, _, h in about}) < 2:
            continue
        if any(
            first[0] != second[0]
            and first[2] != second[2]
            and _similar(first[1], second[1])
            for first in about
            for second in about
        ):
            conflicts.append(claim.index)
    return tuple(conflicts)


def decide(
    sufficiency: Sufficiency,
    result: AnswerResult | None,
    cited: EvidenceSet | None,
    support: SupportReport | None,
    policy: AbstentionPolicy,
) -> AbstentionDecision:
    """Answer, or abstain with one reason.

    `result` is None when retrieval's result was already not sufficient
    and no model was called; `cited` is the Evidence the Answer cites, and
    `support` its support report, or None if it was not checked.
    """
    _check_policy(policy)
    if type(sufficiency) is not Sufficiency:
        raise InvalidAbstentionInput("sufficiency must be a Sufficiency")
    if sufficiency is Sufficiency.EMPTY:
        return _abstain(AbstentionReason.NO_EVIDENCE, policy)
    if sufficiency is Sufficiency.WEAK:
        return _abstain(AbstentionReason.WEAK_EVIDENCE, policy)
    if type(result) is not AnswerResult or type(result.outcome) is not AnswerOutcome:
        raise InvalidAbstentionInput("sufficient Evidence needs an AnswerResult")
    match result.outcome:
        case AnswerOutcome.NO_EVIDENCE | AnswerOutcome.WITHDRAWN:
            return _abstain(AbstentionReason.NO_EVIDENCE, policy)
        case AnswerOutcome.REFUSED | AnswerOutcome.INVALID:
            return _abstain(AbstentionReason.ANSWER_FAILED, policy)
        case AnswerOutcome.ABSTAINED:
            return _abstain(AbstentionReason.INSUFFICIENT_EVIDENCE, policy)
        case AnswerOutcome.ANSWERED:
            pass
    if type(cited) is not EvidenceSet:
        raise InvalidAbstentionInput("an Answer needs its cited EvidenceSet")
    if find_conflicts(result.text, cited):
        return _abstain(AbstentionReason.CONFLICTING_EVIDENCE, policy)
    if not _supported_enough(support, result.text, policy):
        return _abstain(AbstentionReason.UNSUPPORTED, policy)
    return AbstentionDecision(
        decision=Decision.ANSWER, reason=None, policy_version=policy.version
    )


def classify(answerable: bool, decision: AbstentionDecision) -> CaseOutcome:
    """Compare one decision with its Evaluation Case's answerability label."""
    if type(answerable) is not bool or type(decision) is not AbstentionDecision:
        raise InvalidAbstentionInput("a bool label and an AbstentionDecision")
    answered = decision.decision is Decision.ANSWER
    if answerable:
        return CaseOutcome.CORRECT_ANSWER if answered else CaseOutcome.OVER_ABSTENTION
    return (
        CaseOutcome.UNSUPPORTED_ANSWER if answered else CaseOutcome.CORRECT_ABSTENTION
    )


def score_abstention(
    cases: Sequence[tuple[bool, AbstentionDecision]],
) -> AbstentionScore:
    """Count each kind of outcome over labelled decisions."""
    counts = dict.fromkeys(CaseOutcome, 0)
    for item in cases:
        if type(item) is not tuple or len(cast(tuple[object, ...], item)) != 2:
            raise InvalidAbstentionInput("a case is an (answerable, decision) pair")
        answerable, decision = item
        counts[classify(answerable, decision)] += 1
    return AbstentionScore(
        correct_answers=counts[CaseOutcome.CORRECT_ANSWER],
        correct_abstentions=counts[CaseOutcome.CORRECT_ABSTENTION],
        over_abstentions=counts[CaseOutcome.OVER_ABSTENTION],
        unsupported_answers=counts[CaseOutcome.UNSUPPORTED_ANSWER],
    )


def calibrate_threshold(
    cases: Sequence[tuple[float, bool]], *, max_over_abstention: float
) -> float:
    """The reranker threshold for one reranker's labelled top scores.

    Each case is a question's best score and whether it is answerable. A
    threshold abstains below it. Of all thresholds that over-abstain on at
    most `max_over_abstention` of the answerable cases, this returns the
    one that answers fewest unanswerable cases; among equals, the lowest,
    so it abstains least.
    """
    if not _finite(max_over_abstention) or not 0 <= max_over_abstention <= 1:
        raise InvalidAbstentionInput("max_over_abstention must be from 0 to 1")
    labelled = [_case(item) for item in cases]
    answerable = sum(1 for _, label in labelled if label)
    if answerable == 0 or answerable == len(labelled):
        raise InvalidAbstentionInput("calibration needs both kinds of case")
    # 0.29 means 29 of 100, not the float just below it.
    allowed = math.floor(Fraction(repr(max_over_abstention)) * answerable)
    labelled.sort(key=lambda case: case[0])
    unanswerable = len(labelled) - answerable
    best: tuple[int, float] | None = None
    over = below_unanswerable = 0
    index = 0
    # One pass, lowest threshold first: a threshold abstains on every case
    # below it, so the counts below it only grow.
    while index < len(labelled):
        threshold = labelled[index][0]
        unsupported = unanswerable - below_unanswerable
        if over <= allowed and (best is None or unsupported < best[0]):
            best = (unsupported, threshold)
        while index < len(labelled) and labelled[index][0] == threshold:
            if labelled[index][1]:
                over += 1
            else:
                below_unanswerable += 1
            index += 1
    # Above every score: abstain on everything, if the budget allows it.
    if over <= allowed and (best is None or best[0] > 0):
        best = (0, math.nextafter(labelled[-1][0], math.inf))
    # The lowest score is always allowed: it abstains on nothing.
    return cast(tuple[int, float], best)[1]


def _similar(first: frozenset[str], second: frozenset[str]) -> bool:
    """True when two sets of words share at least 80% of all their words."""
    return len(first & second) >= _SAME_TOPIC * len(first | second)


def _supported_enough(
    support: SupportReport | None, text: str, policy: AbstentionPolicy
) -> bool:
    """True when the report is about this text and enough claims passed.

    Every claim counts; one that was not checked counts as unsupported.
    """
    if (
        type(support) is not SupportReport
        or support.outcome is not SupportOutcome.CHECKED
        or support.answer_sha256 != hashlib.sha256(text.encode("utf-8")).hexdigest()
    ):
        return False
    claims = split_claims(text)
    if not claims or tuple(f.claim for f in support.findings) != claims:
        return False
    supported = sum(
        1 for f in support.findings if f.verdict is SupportVerdict.SUPPORTED
    )
    return supported >= policy.min_supported_share * len(claims)


def _abstain(reason: AbstentionReason, policy: AbstentionPolicy) -> AbstentionDecision:
    return AbstentionDecision(
        decision=Decision.ABSTAIN, reason=reason, policy_version=policy.version
    )


def _check_policy(policy: object) -> None:
    if type(policy) is not AbstentionPolicy:
        raise InvalidAbstentionInput("policy must be an AbstentionPolicy")


def _case(item: object) -> tuple[float, bool]:
    if type(item) is not tuple or len(cast(tuple[object, ...], item)) != 2:
        raise InvalidAbstentionInput("a case is a (score, answerable) pair")
    score, label = cast(tuple[object, object], item)
    if not _finite(score) or type(label) is not bool:
        raise InvalidAbstentionInput("a case is a finite score and a bool label")
    return float(cast(float, score)), label


def _finite(value: object) -> bool:
    """A finite int or float, not a bool; never raises."""
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(cast(float, value)))
    except OverflowError:
        return False
