"""Gain per rewrite class, and which classes may be switched on.

`compare_rewriting` (in `retrieval.evaluation_dataset`) searches every
labelled case as typed and, where a rewrite was used, again with the
rewrite. This module turns that into one row per rewrite class and a
rollout decision for each:

- **Gain.** MRR with the Question as typed, MRR with the search text the
  policy would use, and the difference.
- **Harm.** A case is harmed when its best relevant Chunk ranks lower
  after the rewrite, or when any relevant Chunk found as typed is no
  longer found.
- **Rollout.** A class may be switched on only when it is rewritable,
  harms no case, ran without a fallback or a degraded search, has at
  least `MIN_ROLLOUT_CASES` rewritten cases, and raises its MRR. A
  fallback keeps the Question as typed, so it could hide a rewrite that
  harms; such a run measured too little and must be repeated. A tie is
  not a gain: a rewrite that changes nothing still costs a request and
  time.
- **A guard.** `check_rollout` raises `RolloutRefused` when a policy
  switches on a class the measurement did not allow, so a script can stop
  a deployment that would make retrieval worse.
- **Quiet.** Reports name cases by ID and give ranks, counts, and cost;
  never a Question or a rewrite.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from knowledge_service.retrieval.evaluation_dataset import (
    RewriteCaseOutcome,
    RewriteComparison,
)
from knowledge_service.retrieval.query_rewriting import RewriteOutcome, RewritePolicy
from knowledge_service.retrieval.ranking_metrics import mean, reciprocal_rank
from knowledge_service.retrieval.rewrite_classes import REWRITABLE, RewriteClass

MIN_ROLLOUT_CASES = 2


class Rollout(StrEnum):
    """Whether a class may be switched on, and if not, why."""

    ALLOWED = "allowed"
    NEVER_REWRITTEN = "never_rewritten"
    HARMS = "harms"
    INCOMPLETE = "incomplete"
    TOO_FEW_CASES = "too_few_cases"
    NO_GAIN = "no_gain"


class RolloutRefused(Exception):
    """A policy switches on a class that the measurement did not allow."""


@dataclass(frozen=True, slots=True)
class ClassGain:
    """What rewriting did to the cases of one class.

    `mrr_before` and `mrr_after` are None when the class has no case.
    `improved` names the cases whose best rank rose; `harmed` those whose
    best rank fell or that lost a relevant Chunk. `degraded` counts cases
    whose search, as typed or rewritten, ran without one of its branches.
    """

    rewrite_class: RewriteClass
    cases: int
    rewritten: int
    fallbacks: int
    degraded: int
    mrr_before: Fraction | None
    mrr_after: Fraction | None
    improved: tuple[str, ...]
    harmed: tuple[str, ...]
    rollout: Rollout

    @property
    def gain(self) -> Fraction | None:
        """MRR after minus MRR before, or None when the class has no case."""
        if self.mrr_before is None or self.mrr_after is None:
            return None
        return self.mrr_after - self.mrr_before


def score_rewriting(comparison: RewriteComparison) -> tuple[ClassGain, ...]:
    """One `ClassGain` per rewrite class, in the order of `RewriteClass`."""
    return tuple(
        _gain(
            rewrite_class,
            [case for case in comparison.cases if case.rewrite_class is rewrite_class],
        )
        for rewrite_class in RewriteClass
    )


def allowed_classes(gains: Sequence[ClassGain]) -> frozenset[RewriteClass]:
    """The classes whose rollout the measurement allows."""
    return frozenset(
        gain.rewrite_class for gain in gains if gain.rollout is Rollout.ALLOWED
    )


def check_rollout(policy: RewritePolicy, gains: Sequence[ClassGain]) -> None:
    """Raise `RolloutRefused` if `policy` switches on a class not allowed."""
    if type(policy) is not RewritePolicy:
        raise TypeError("policy must be a RewritePolicy")
    measured = {gain.rewrite_class: gain.rollout for gain in gains}
    refused = [
        f"{name} ({measured.get(name, 'not measured')})"
        for name in RewriteClass
        if name in policy.classes and measured.get(name) is not Rollout.ALLOWED
    ]
    if refused:
        raise RolloutRefused(
            "the policy switches on classes the measurement did not allow: "
            + ", ".join(refused)
        )


def render_rewrite_comparison(
    comparison: RewriteComparison, gains: Sequence[ClassGain]
) -> str:
    """Return the gain per class, every case, cost, and the rollout as Markdown."""
    rewriter = comparison.rewriter
    embedding = comparison.embedding
    lines = [
        f"# Query rewriting: {comparison.name} v{comparison.version}",
        "",
        f"- Rewriter: {rewriter.provider} {rewriter.model}",
        f"- Retrieval: dense + lexical, fused; embedding {embedding.provider}"
        f" {embedding.model}; limit {comparison.limit}",
        "- Every rewritable class was switched on for this run.",
        "",
        "## Gain per class",
        "",
        "| Class | Cases | Rewritten | Fallbacks | Degraded | MRR as typed"
        " | MRR rewritten | Gain | Harmed | Rollout |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for gain in gains:
        harmed = ", ".join(gain.harmed) or "none"
        lines.append(
            f"| {gain.rewrite_class} | {gain.cases} | {gain.rewritten}"
            f" | {gain.fallbacks} | {gain.degraded} | {_number(gain.mrr_before)}"
            f" | {_number(gain.mrr_after)} | {_signed(gain.gain)} | {harmed}"
            f" | {gain.rollout} |"
        )
    lines += [
        "",
        "## Cases",
        "",
        "| Case | Class | Outcome | Ranks as typed | Ranks rewritten |",
        "|---|---|---|---|---|",
    ]
    for case in comparison.cases:
        outcome = str(case.outcome)
        if case.fallback is not None:
            outcome = f"{outcome}: {case.fallback}"
        lines.append(
            f"| {case.case_id} | {case.rewrite_class} | {outcome}"
            f" | {_ranks(case.before.ranks)} | {_ranks(case.after.ranks)} |"
        )
    usages = comparison.usages
    cost = "unknown" if comparison.cost_usd is None else f"{comparison.cost_usd:.6f}"
    lines += [
        "",
        "## Cost",
        "",
        f"- Requests billed: {len(usages)}",
        f"- Input tokens: {sum(usage.input_tokens for usage in usages)}",
        f"- Output tokens: {sum(usage.output_tokens for usage in usages)}",
        f"- Estimated cost: {cost} USD",
        "",
        "## Rollout",
        "",
    ]
    allowed = [name for name in RewriteClass if name in allowed_classes(gains)]
    flag = "[" + ", ".join(f'"{name}"' for name in allowed) + "]"
    lines.append(f"Allowed classes: {', '.join(allowed) or 'none'}.")
    lines.append(f"Largest safe policy: `QUERY_REWRITE_CLASSES='{flag}'`.")
    return "\n".join(lines) + "\n"


def _gain(
    rewrite_class: RewriteClass, cases: Sequence[RewriteCaseOutcome]
) -> ClassGain:
    before = [reciprocal_rank(case.before.ranks) for case in cases]
    after = [reciprocal_rank(case.after.ranks) for case in cases]
    improved: list[str] = []
    harmed: list[str] = []
    for case, old, new in zip(cases, before, after, strict=True):
        if new > old:
            improved.append(case.case_id)
        if new < old or _lost(case):
            harmed.append(case.case_id)
    rewritten = sum(case.outcome is RewriteOutcome.REWRITTEN for case in cases)
    fallbacks = sum(case.outcome is RewriteOutcome.FALLBACK for case in cases)
    degraded = sum(case.before.degraded or case.after.degraded for case in cases)
    mrr_before = mean(before) if cases else None
    mrr_after = mean(after) if cases else None
    if rewrite_class not in REWRITABLE:
        rollout = Rollout.NEVER_REWRITTEN
    elif harmed:
        rollout = Rollout.HARMS
    elif fallbacks or degraded:
        rollout = Rollout.INCOMPLETE
    elif rewritten < MIN_ROLLOUT_CASES:
        rollout = Rollout.TOO_FEW_CASES
    elif mrr_before is None or mrr_after is None or mrr_after <= mrr_before:
        rollout = Rollout.NO_GAIN
    else:
        rollout = Rollout.ALLOWED
    return ClassGain(
        rewrite_class=rewrite_class,
        cases=len(cases),
        rewritten=rewritten,
        fallbacks=fallbacks,
        degraded=degraded,
        mrr_before=mrr_before,
        mrr_after=mrr_after,
        improved=tuple(improved),
        harmed=tuple(harmed),
        rollout=rollout,
    )


def _lost(case: RewriteCaseOutcome) -> bool:
    """True when a relevant Chunk found as typed is missed after the rewrite."""
    return any(
        old is not None and new is None
        for old, new in zip(case.before.ranks, case.after.ranks, strict=True)
    )


def _ranks(ranks: tuple[int | None, ...]) -> str:
    return ", ".join("missed" if rank is None else str(rank) for rank in ranks)


def _number(value: Fraction | None) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def _signed(value: Fraction | None) -> str:
    return "n/a" if value is None else f"{float(value):+.3f}"
