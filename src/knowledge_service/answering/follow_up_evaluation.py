"""Retrieval with and without follow-up resolution, on stored Conversations.

`compare_resolution` replays each case of the bilingual rewrite dataset
(M05-T15) as a Conversation of the evaluation reader: the case's earlier
Questions are stored as turns, `load_history` reads them back, and
`resolve_question` resolves the case's Question with only `follow_up`
switched on. Then the corpus is ingested and embedded once, and every case
is searched as typed and, when it was resolved, again with its search
text, both by dense and lexical search fused.

The result is a `RewriteComparison`, so `score_rewriting`
(`retrieval.rewrite_evaluation`) gives the gain, the harm, and the rollout
decision per class, and `render_resolution` reports them by case ID, with
no Question or rewrite in them.
The earlier turns are stored with a fixed failed reply, because only their
Questions reach the rewriter.
"""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

from knowledge_service.answering.follow_up import (
    FOLLOW_UP_ONLY,
    ResolvedQuestion,
    resolve_question,
)
from knowledge_service.answering.history import load_history
from knowledge_service.contracts import Usage
from knowledge_service.conversations import ConversationLimits, MessageOutcome
from knowledge_service.database import DatabaseRuntime
from knowledge_service.embeddings.port import EmbeddingModel
from knowledge_service.persistence.conversations import add_turn, start_conversation
from knowledge_service.pricing import ModelPrice, estimate_cost
from knowledge_service.retrieval.evaluation_dataset import (
    DEFAULT_LIMIT,
    READER,
    CaseFailed,
    DatasetRunRefused,
    RewriteCaseOutcome,
    RewriteComparison,
    RewriteDataset,
    compare_branches,
)
from knowledge_service.retrieval.query_rewriting import RewriteModel
from knowledge_service.retrieval.rewrite_cases import RewriteCase
from knowledge_service.retrieval.rewrite_evaluation import (
    render_rewrite_comparison,
    score_rewriting,
)

# The reply stored after each earlier Question; History withholds it.
EARLIER_REPLY = "No answer was kept for this evaluation turn."
HISTORY_TOKENS = 4_000
RESOLVE_TIMEOUT_SECONDS = 30.0
# A dataset case ID is lowercase words and hyphens, so none ends like this.
RESOLVED_SUFFIX = "+resolved"

_SWITCHED_ON = "- Every rewritable class was switched on for this run."
_RESOLVED_ON = (
    "- Only follow_up was switched on; each case was resolved through its"
    " stored History."
)
_REQUEST_IDS = UUID("6f0c2b4e-31d5-4a8e-9c71-5e2d0b7a9f12")


async def compare_resolution(
    runtime: DatabaseRuntime,
    rewrite: RewriteDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: EmbeddingModel,
    rewriter: RewriteModel,
    price: ModelPrice | None,
    limit: int = DEFAULT_LIMIT,
) -> RewriteComparison:
    """Resolve every case through its stored History, then search both ways.

    Raises like `compare_branches`; `DatasetRunRefused` before anything
    is written when `rewrite` did not come from its loader; `CaseFailed`
    when resolving a case raises, naming only error types, or when the
    rewriter changes its identity during the run.
    """
    # Each case is searched with one Question and resolved with the same.
    if type(rewrite) is not RewriteDataset or [
        (case.case_id, case.question, case.question_language)
        for case in rewrite.dataset.cases
    ] != [(case.case_id, case.question, case.language) for case in rewrite.labelled]:
        raise DatasetRunRefused("the rewrite dataset must come from its loader")
    identity = rewriter.identity
    resolved: list[ResolvedQuestion] = []
    for label in rewrite.labelled:
        result = await _resolve(runtime, label, rewriter, observed_at)
        if result.rewriter != identity:
            raise CaseFailed(f"case {label.case_id}: the rewriter changed its identity")
        resolved.append(result)
    typed = rewrite.dataset.cases
    again = tuple(
        replace(
            case,
            case_id=f"{case.case_id}{RESOLVED_SUFFIX}",
            question=result.search_text,
        )
        for case, result in zip(typed, resolved, strict=True)
        if result.resolved
    )
    combined = replace(rewrite.dataset, cases=(*typed, *again))
    reports = await compare_branches(
        runtime, combined, corpus, observed_at=observed_at, model=model, limit=limit
    )
    fused = {outcome.case_id: outcome for outcome in reports[-1].outcomes}
    cases: list[RewriteCaseOutcome] = []
    for case, result in zip(typed, resolved, strict=True):
        before = fused[case.case_id]
        after = before
        if result.resolved:
            after = replace(
                fused[f"{case.case_id}{RESOLVED_SUFFIX}"], case_id=case.case_id
            )
        cases.append(
            RewriteCaseOutcome(
                case_id=case.case_id,
                rewrite_class=result.rewrite_class,
                outcome=result.outcome,
                fallback=result.fallback,
                before=before,
                after=after,
            )
        )
    usages = tuple(result.usage for result in resolved if result.usage is not None)
    return RewriteComparison(
        name=rewrite.dataset.name,
        version=rewrite.dataset.version,
        limit=limit,
        embedding=model.identity,
        rewriter=identity,
        cases=tuple(cases),
        usages=usages,
        cost_usd=_cost(usages, price),
    )


def render_resolution(comparison: RewriteComparison) -> str:
    """The comparison as Markdown: gain per class, every case, cost, rollout."""
    return render_rewrite_comparison(comparison, score_rewriting(comparison)).replace(
        _SWITCHED_ON, _RESOLVED_ON
    )


async def _resolve(
    runtime: DatabaseRuntime,
    label: RewriteCase,
    rewriter: RewriteModel,
    observed_at: datetime,
) -> ResolvedQuestion:
    """Store the case's earlier turns, read its History, resolve its Question."""
    limits = ConversationLimits()
    resolved: ResolvedQuestion | None = None
    failure = "no result"
    try:
        async with runtime.transaction() as connection:
            started = await start_conversation(
                connection, owner_id=READER.user_id, now=observed_at, limits=limits
            )
            for question in label.history:
                await add_turn(
                    connection,
                    owner_id=READER.user_id,
                    conversation_id=started.conversation_id,
                    question=question,
                    reply=EARLIER_REPLY,
                    outcome=MessageOutcome.FAILED,
                    references=(),
                    now=observed_at,
                    limits=limits,
                )
            history = await load_history(
                connection,
                READER,
                conversation_id=started.conversation_id,
                now=observed_at,
                budget_tokens=HISTORY_TOKENS,
            )
        if history is not None:
            resolved = await resolve_question(
                rewriter,
                label.question,
                language=label.language,
                history=history,
                policy=FOLLOW_UP_ONLY,
                request_id=uuid5(_REQUEST_IDS, f"resolve {label.case_id}"),
                timeout_seconds=RESOLVE_TIMEOUT_SECONDS,
            )
    except Exception as error:
        # A bug or a broken request; its message may quote a Question.
        failure = type(error).__name__
    # Raised outside the `except`, so no linked error carries a text.
    if resolved is None:
        raise CaseFailed(f"case {label.case_id}: resolving raised {failure}")
    return resolved


def _cost(usages: tuple[Usage, ...], price: ModelPrice | None) -> Decimal | None:
    """The estimated cost of every billed answer; None if any is unknown."""
    if price is None:
        return None
    total = Decimal(0)
    for usage in usages:
        cost = estimate_cost(usage, {price.price_id: price})
        if cost is None:
            return None
        total += cost
    return total
