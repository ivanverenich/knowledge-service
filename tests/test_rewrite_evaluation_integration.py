"""Query rewriting on real PostgreSQL: what is sent, what is searched, fallback."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import SecretStr

from fakes import FakeEmbeddingModel
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import EmbeddingLimits
from knowledge_service.pricing import ModelPrice
from knowledge_service.retrieval.evaluation_dataset import (
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    REWRITE_RETRIEVAL_FILE,
    CaseFailed,
    RewriteDataset,
    compare_rewriting,
    load_rewrite_dataset,
)
from knowledge_service.retrieval.query_rewriting import (
    RewriteOutcome,
    RewriterIdentity,
    RewriterUnavailable,
)
from knowledge_service.retrieval.rewrite_cases import (
    REWRITE_CASES_FILE,
    load_rewrite_cases,
)
from knowledge_service.retrieval.rewrite_classes import REWRITABLE
from knowledge_service.retrieval.rewrite_evaluation import (
    Rollout,
    render_rewrite_comparison,
    score_rewriting,
)
from knowledge_service.settings import Settings
from rewriting_fakes import FakeRewriteModel

OBSERVED_AT = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
LIMITS = EmbeddingLimits(max_inputs=64, max_input_tokens=1_000, max_batch_tokens=20_000)
PRICE = ModelPrice(
    price_id="fake-rewriter",
    input_usd_per_million_tokens=Decimal("1"),
    output_usd_per_million_tokens=Decimal("2"),
)


class IdentityChanges(FakeRewriteModel):
    """Names one model when the run starts and another while it rewrites."""

    def __init__(self) -> None:
        super().__init__()
        self._reads = 0

    @property
    def identity(self) -> RewriterIdentity:
        self._reads += 1
        model = "fake-rewriter" if self._reads == 1 else "another-rewriter"
        return RewriterIdentity(provider="fake", model=model)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


@pytest.fixture
def rewrite() -> RewriteDataset:
    return load_rewrite_dataset(
        DATASET_DIRECTORY / REWRITE_RETRIEVAL_FILE,
        load_rewrite_cases(REWRITE_CASES_FILE),
    )


@pytest.mark.integration
async def test_only_rewritable_questions_are_sent_and_rewritten_ones_searched_again(
    runtime: DatabaseRuntime,
    rewrite: RewriteDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    rewriter = FakeRewriteModel()

    comparison = await compare_rewriting(
        runtime,
        rewrite,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        rewriter=rewriter,
        price=PRICE,
    )

    assert [case.case_id for case in comparison.cases] == [
        case.case_id for case in rewrite.labelled
    ]
    rewritable = [c for c in comparison.cases if c.rewrite_class in REWRITABLE]
    assert rewritable
    # Only the conversation was sent: each call is a labelled Question
    # with its own earlier Questions, never a Chunk.
    conversations = {(case.question, case.history) for case in rewrite.labelled}
    assert len(rewriter.calls) == len(rewritable)
    assert all((q, history) in conversations for q, _, history in rewriter.calls)
    assert all(RESTRICTED_MARKER not in repr(call) for call in rewriter.calls)
    for case in comparison.cases:
        if case.rewrite_class not in REWRITABLE:
            assert case.outcome is RewriteOutcome.NOT_REWRITABLE
        if case.outcome is not RewriteOutcome.REWRITTEN:
            assert case.after == case.before
    usages = comparison.usages
    assert len(usages) == len(rewritable)
    tokens_in = sum(u.input_tokens for u in usages)
    tokens_out = sum(u.output_tokens for u in usages)
    assert comparison.cost_usd == (
        Decimal(tokens_in) + 2 * Decimal(tokens_out)
    ) / Decimal(1_000_000)
    gains = score_rewriting(comparison)
    assert {g.rollout for g in gains if g.rewrite_class not in REWRITABLE} == {
        Rollout.NEVER_REWRITTEN
    }
    rendered = render_rewrite_comparison(comparison, gains)
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in rewrite.labelled)


@pytest.mark.integration
async def test_a_failing_rewriter_keeps_every_question_and_the_run_goes_on(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    rewriter = FakeRewriteModel(error=RewriterUnavailable())

    comparison = await compare_rewriting(
        runtime,
        rewrite,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        rewriter=rewriter,
        price=None,
    )

    rewritable = [c for c in comparison.cases if c.rewrite_class in REWRITABLE]
    assert {c.fallback for c in rewritable} == {"RewriterUnavailable"}
    assert all(case.after == case.before for case in comparison.cases)
    assert (comparison.usages, comparison.cost_usd) == ((), None)
    gains = score_rewriting(comparison)
    assert all(g.rollout is not Rollout.ALLOWED for g in gains)


@pytest.mark.integration
async def test_a_rewriter_that_changes_its_identity_is_refused(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    with pytest.raises(CaseFailed, match="changed its identity"):
        await compare_rewriting(
            runtime,
            rewrite,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            rewriter=IdentityChanges(),
            price=PRICE,
        )


@pytest.mark.integration
async def test_a_rewriter_bug_stops_the_run_without_quoting_the_question(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    question = next(c.question for c in rewrite.labelled if c.history)
    rewriter = FakeRewriteModel(error=RuntimeError(question))

    with pytest.raises(CaseFailed, match="rewriting raised RuntimeError") as caught:
        await compare_rewriting(
            runtime,
            rewrite,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            rewriter=rewriter,
            price=PRICE,
        )

    assert question not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
