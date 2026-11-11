"""LLM reranking on real PostgreSQL: the seed run, what is sent, fallback, cost."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import SecretStr

from fakes import FakeEmbeddingModel, FakeRankingModel
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import EmbeddingLimits
from knowledge_service.pricing import ModelPrice
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    CaseFailed,
    EvaluationDataset,
    compare_llm_reranking,
    load_dataset,
    render_llm_rerank_comparison,
)
from knowledge_service.retrieval.llm_reranking import RankingBudget
from knowledge_service.retrieval.reranking import (
    RerankerIdentity,
    RerankerUnavailable,
)
from knowledge_service.settings import Settings

OBSERVED_AT = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
LIMITS = EmbeddingLimits(max_inputs=64, max_input_tokens=1_000, max_batch_tokens=20_000)
BUDGET = RankingBudget(max_candidates=20, max_input_tokens=20_000)
PRICE = ModelPrice(
    price_id="fake-ranker",
    input_usd_per_million_tokens=Decimal("1"),
    output_usd_per_million_tokens=Decimal("2"),
)


class IdentityChanges(FakeRankingModel):
    """Names one model when the run starts and another while it ranks."""

    def __init__(self) -> None:
        super().__init__()
        self._reads = 0

    @property
    def identity(self) -> RerankerIdentity:
        self._reads += 1
        model = "fake-ranker" if self._reads == 1 else "another-ranker"
        return RerankerIdentity(provider="fake", model=model)


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
def dataset() -> EvaluationDataset:
    return load_dataset(DATASET_DIRECTORY / CASES_FILE)


@pytest.mark.integration
async def test_every_case_is_ranked_and_only_eligible_text_is_sent(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    ranker = FakeRankingModel()

    comparison = await compare_llm_reranking(
        runtime,
        dataset,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        ranker=ranker,
        budget=BUDGET,
        price=PRICE,
    )

    before, after = comparison.retrieved, comparison.reranked
    assert (before.reranker, after.reranker) == (None, ranker.identity)
    assert len(ranker.calls) == len(dataset.cases)
    assert comparison.fallbacks == ()
    # The permission check ran before ranking: no restricted text was sent.
    sent = [p.text for _, passages in ranker.calls for p in passages]
    assert sent
    assert all(RESTRICTED_MARKER not in text for text in sent)
    for old, new in zip(before.outcomes, after.outcomes, strict=True):
        assert [r is None for r in new.ranks] == [r is None for r in old.ranks]
    # Every billed answer is costed: 1 USD per million in, 2 per million out.
    usages = comparison.usages
    assert len(usages) == len(dataset.cases)
    tokens_in = sum(u.input_tokens for u in usages)
    tokens_out = sum(u.output_tokens for u in usages)
    assert comparison.cost_usd == (
        Decimal(tokens_in) + 2 * Decimal(tokens_out)
    ) / Decimal(1_000_000)
    rendered = render_llm_rerank_comparison(comparison)
    for section in ("## Ranking delta", "## Fallbacks", "## Latency", "## Cost"):
        assert section in rendered
    assert f"- Input tokens: {tokens_in}; output tokens: {tokens_out}." in rendered
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in dataset.cases)


@pytest.mark.integration
async def test_a_failing_ranker_keeps_retrieval_order_and_the_run_goes_on(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    ranker = FakeRankingModel(error=RerankerUnavailable())

    comparison = await compare_llm_reranking(
        runtime,
        dataset,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        ranker=ranker,
        budget=BUDGET,
        price=None,
    )

    assert len(ranker.calls) == len(dataset.cases)
    assert comparison.fallbacks == tuple(
        (case.case_id, "RerankerUnavailable") for case in dataset.cases
    )
    before, after = comparison.retrieved, comparison.reranked
    assert [o.ranks for o in after.outcomes] == [o.ranks for o in before.outcomes]
    assert (comparison.usages, comparison.cost_usd) == ((), None)
    rendered = render_llm_rerank_comparison(comparison)
    assert "- Billed answers: 0 for 16 cases." in rendered
    assert "- Estimated cost: unknown" in rendered


@pytest.mark.integration
async def test_a_ranker_that_changes_its_identity_is_refused(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    with pytest.raises(CaseFailed, match="changed its identity"):
        await compare_llm_reranking(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            ranker=IdentityChanges(),
            budget=BUDGET,
            price=PRICE,
        )
