"""Reranking on real PostgreSQL: the seed run, what reaches the reranker, failure."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from fakes import FakeEmbeddingModel, FakeReranker
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import EmbeddingLimits
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    CaseFailed,
    EvaluationDataset,
    compare_reranking,
    load_dataset,
    render_rerank_comparison,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.reranking import (
    RerankerIdentity,
    RerankerUnavailable,
)
from knowledge_service.settings import Settings

OBSERVED_AT = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
LIMITS = EmbeddingLimits(max_inputs=64, max_input_tokens=1_000, max_batch_tokens=20_000)


class IdentityChanges(FakeReranker):
    """Names one model when the run starts and another while it scores."""

    def __init__(self) -> None:
        super().__init__()
        self._reads = 0

    @property
    def identity(self) -> RerankerIdentity:
        self._reads += 1
        model = "fake-reranker" if self._reads == 1 else "another-reranker"
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
async def test_every_case_is_reranked_and_only_eligible_text_reaches_the_reranker(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    reranker = FakeReranker()

    comparison = await compare_reranking(
        runtime,
        dataset,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        reranker=reranker,
        device="cpu",
    )

    before, after = comparison.retrieved, comparison.reranked
    assert before.branches == (RetrievalBranch.DENSE, RetrievalBranch.LEXICAL)
    assert (before.reranker, after.reranker) == (None, reranker.identity)
    assert [o.case_id for o in after.outcomes] == [c.case_id for c in dataset.cases]
    # One request per case: ten Candidates fit the fake's limits at once.
    assert comparison.requests == len(dataset.cases) == len(reranker.calls)
    # The permission check ran before reranking: no restricted text ever
    # left the database for the reranker, however relevant it might be.
    sent = [text for call in reranker.calls for text in call]
    assert sent
    assert all(RESTRICTED_MARKER not in text for text in sent)
    # Reranking reorders; it never finds what retrieval missed.
    for old, new in zip(before.outcomes, after.outcomes, strict=True):
        assert [r is None for r in new.ranks] == [r is None for r in old.ranks]
    rendered = render_rerank_comparison(comparison)
    assert "## Ranking delta" in rendered
    assert "- Reranker device: cpu." in rendered
    assert rendered.index("## Ranking delta") < rendered.index("## Latency")
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in dataset.cases)


@pytest.mark.integration
async def test_a_reranker_that_fails_stops_the_run_quietly(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    reranker = FakeReranker(error=RerankerUnavailable())

    with pytest.raises(CaseFailed) as raised:
        await compare_reranking(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            reranker=reranker,
            device="cpu",
        )

    first = dataset.cases[0]
    assert str(raised.value) == (
        f"case {first.case_id}: reranking raised RerankerUnavailable"
    )
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert len(reranker.calls) == 1


@pytest.mark.integration
async def test_a_reranker_that_changes_its_identity_is_refused(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    with pytest.raises(CaseFailed, match="changed its identity"):
        await compare_reranking(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            reranker=IdentityChanges(),
            device="cpu",
        )
