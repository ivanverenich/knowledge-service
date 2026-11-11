"""Three reranking strategies on real PostgreSQL: same Candidates, typed failure."""

import logging
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import SecretStr

from fakes import FakeEmbeddingModel, FakeRankingModel, FakeReranker
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import EmbeddingLimits
from knowledge_service.pricing import ModelPrice
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    CaseFailed,
    EvaluationDataset,
    compare_reranking_strategies,
    load_dataset,
    render_strategy_comparison,
)
from knowledge_service.retrieval.llm_reranking import RankingBudget
from knowledge_service.retrieval.reranking import RerankerUnavailable
from knowledge_service.settings import Settings

OBSERVED_AT = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
LIMITS = EmbeddingLimits(max_inputs=64, max_input_tokens=1_000, max_batch_tokens=20_000)
BUDGET = RankingBudget(max_candidates=20, max_input_tokens=20_000)
# Room for a few Candidates of the seed corpus, not all ten.
TIGHT_TOKENS = 300
PRICE = ModelPrice(
    price_id="fake-ranker",
    input_usd_per_million_tokens=Decimal("1"),
    output_usd_per_million_tokens=Decimal("2"),
)


class FailsOnSecondCase(FakeReranker):
    """Scores the first case, then fails as if its server stopped."""

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        if self.calls:
            raise RerankerUnavailable()
        return await super().score(
            query, passages, request_id=request_id, timeout_seconds=timeout_seconds
        )


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
async def test_both_rerankers_get_the_same_eligible_candidates_of_every_case(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    reranker, ranker = FakeReranker(), FakeRankingModel()

    comparison = await compare_reranking_strategies(
        runtime,
        dataset,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        reranker=reranker,
        device="cpu",
        ranker=ranker,
        budget=BUDGET,
        price=PRICE,
    )

    # One request per case to each reranker, carrying the same texts in
    # retrieval's order: the strategies differ only in how they order them.
    assert comparison.requests == len(reranker.calls) == len(dataset.cases)
    assert len(ranker.calls) == len(dataset.cases)
    for scored, (_, passages) in zip(reranker.calls, ranker.calls, strict=True):
        assert list(scored) == [p.text for p in passages]
    sent = [text for texts in reranker.calls for text in texts]
    assert sent
    assert all(RESTRICTED_MARKER not in text for text in sent)
    # Reordering the same Candidates cannot find or lose a relevant Chunk.
    runs = (comparison.retrieved, comparison.cross_encoder, comparison.llm)
    assert (runs[1].reranker, runs[2].reranker) == (
        reranker.identity,
        ranker.identity,
    )
    for row in zip(*(run.outcomes for run in runs), strict=True):
        assert len({tuple(r is None for r in o.ranks) for o in row}) == 1
    assert (comparison.fallbacks, comparison.partial) == ((), ())
    assert len(comparison.usages) == len(dataset.cases)
    rendered = render_strategy_comparison(comparison)
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in dataset.cases)


@pytest.mark.integration
async def test_a_failing_cross_encoder_stops_before_the_language_model_is_asked(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    ranker = FakeRankingModel()

    with pytest.raises(CaseFailed, match=r"case \S+: reranking raised Reranker"):
        await compare_reranking_strategies(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            reranker=FailsOnSecondCase(),
            device="cpu",
            ranker=ranker,
            budget=BUDGET,
            price=PRICE,
        )

    # The first case was cross-encoded, but nothing was sent to the paid
    # model for a run that cannot finish.
    assert ranker.calls == []


@pytest.mark.integration
async def test_a_failing_language_model_falls_back_and_the_run_goes_on(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    reranker = FakeReranker()

    comparison = await compare_reranking_strategies(
        runtime,
        dataset,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        reranker=reranker,
        device="cpu",
        ranker=FakeRankingModel(error=RerankerUnavailable()),
        budget=BUDGET,
        price=PRICE,
    )

    assert len(reranker.calls) == len(dataset.cases)
    assert comparison.fallbacks == tuple(
        (case.case_id, "RerankerUnavailable") for case in dataset.cases
    )
    before, llm = comparison.retrieved, comparison.llm
    assert [o.ranks for o in llm.outcomes] == [o.ranks for o in before.outcomes]
    assert (comparison.usages, comparison.cost_usd) == ((), Decimal(0))


@pytest.mark.integration
async def test_candidates_beyond_the_token_budget_are_reported_as_partial(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    ranker = FakeRankingModel()

    comparison = await compare_reranking_strategies(
        runtime,
        dataset,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        reranker=FakeReranker(),
        device="cpu",
        ranker=ranker,
        budget=RankingBudget(max_candidates=10, max_input_tokens=TIGHT_TOKENS),
        price=PRICE,
    )

    assert comparison.partial
    for case_id, ranked, given in comparison.partial:
        assert 2 <= ranked < given
        assert f"- {case_id}: {ranked} of {given} ordered;" in (
            render_strategy_comparison(comparison)
        )
