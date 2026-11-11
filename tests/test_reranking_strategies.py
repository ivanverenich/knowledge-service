"""The three-way reranking comparison: refused settings and the report."""

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import SecretStr

from fakes import FakeEmbeddingModel, FakeRankingModel, FakeReranker
from knowledge_service.contracts import Usage
from knowledge_service.database import create_database_runtime
from knowledge_service.embeddings.port import EmbeddingModelIdentity
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    CaseOutcome,
    DatasetReport,
    DatasetRunRefused,
    QueryClass,
    StrategyComparison,
    compare_reranking_strategies,
    load_dataset,
    render_strategy_comparison,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.llm_reranking import RankingBudget, RankingModel
from knowledge_service.retrieval.reranking import RerankerIdentity
from knowledge_service.settings import Settings

CROSS = RerankerIdentity(provider="local", model="cross@1")
LLM = RerankerIdentity(provider="openai", model="ranker-1")


class SameNameRanker(FakeRankingModel):
    """A ranking model that names itself like `FakeReranker`."""

    @property
    def identity(self) -> RerankerIdentity:
        return RerankerIdentity(provider="fake", model="fake-reranker")


@pytest.mark.parametrize(
    ("limit", "ranker", "message"),
    [
        (0, FakeRankingModel(), "limit"),
        (21, FakeRankingModel(), "max_candidates"),
        (10, SameNameRanker(), "different identities"),
    ],
)
async def test_a_wrong_strategy_setting_is_refused_before_any_work(
    limit: int, ranker: RankingModel, message: str
) -> None:
    # The runtime connects lazily, so nothing is reached at this address.
    runtime = create_database_runtime(
        Settings(database_url=SecretStr("postgresql+psycopg://u:p@localhost:1/x"))
    )
    dataset = load_dataset(DATASET_DIRECTORY / CASES_FILE)

    with pytest.raises(DatasetRunRefused, match=message):
        await compare_reranking_strategies(
            runtime,
            dataset,
            DATASET_DIRECTORY / CORPUS_DIRECTORY,
            observed_at=datetime(2026, 11, 11, tzinfo=UTC),
            model=FakeEmbeddingModel(),
            reranker=FakeReranker(),
            device="cpu",
            ranker=ranker,
            budget=RankingBudget(max_candidates=20, max_input_tokens=8_000),
            price=None,
            limit=limit,
        )
    await runtime.dispose()


def report(
    ranks: list[tuple[int | None, ...]],
    seconds: list[float],
    reranker: RerankerIdentity | None,
) -> DatasetReport:
    cases = [
        ("a", "en->en", QueryClass.PLAIN),
        ("b", "uk->uk", QueryClass.MORPHOLOGY),
        ("c", "uk->en", QueryClass.IDENTIFIER),
    ]
    return DatasetReport(
        name="bilingual-seed",
        version=1,
        limit=10,
        branches=(RetrievalBranch.DENSE, RetrievalBranch.LEXICAL),
        outcomes=tuple(
            CaseOutcome(case, direction, query_class, rank, False, elapsed)
            for (case, direction, query_class), rank, elapsed in zip(
                cases, ranks, seconds, strict=True
            )
        ),
        embedding=EmbeddingModelIdentity(
            provider="local", model="bge-m3@1", dimensions=1024
        ),
        reranker=reranker,
    )


def usage(tokens_in: int, tokens_out: int) -> Usage:
    return Usage(
        provider="openai",
        model="ranker-1",
        price_id="ranker-1",
        latency_ms=1,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        total_tokens=tokens_in + tokens_out,
    )


COMPARISON = StrategyComparison(
    retrieved=report([(2,), (1,), (None,)], [0.1, 0.2, 0.3], None),
    cross_encoder=report([(1,), (1,), (None,)], [0.5, 0.5, 0.5], CROSS),
    llm=report([(1,), (3,), (None,)], [1.0, 2.0, 3.0], LLM),
    device="mps",
    requests=3,
    fallbacks=(("c", "RerankerTimeout"),),
    usages=(usage(100, 10), usage(200, 20)),
    cost_usd=Decimal("0.000300"),
    partial=(("a", 4, 10),),
)


def test_the_report_puts_three_strategies_side_by_side() -> None:
    rendered = render_strategy_comparison(COMPARISON)

    assert rendered.startswith("# Reranking strategies: bilingual-seed v1\n")
    assert "- Cross-encoder: local cross@1." in rendered
    assert "- Language model: openai ranker-1." in rendered
    # Recall@1 and MRR: Recall@10 cannot change when only the order does.
    assert (
        "| Slice | Cases | none R@1 | none MRR | cross-encoder R@1"
        " | cross-encoder MRR | LLM R@1 | LLM MRR |"
    ) in rendered
    assert "| all | 3 | 0.333 | 0.500 | 0.667 | 0.667 | 0.333 | 0.444 |" in rendered
    assert "| b | uk->uk | morphology | 1 | 1 | 3 |" in rendered
    assert "| c | uk->en | identifier | missed | missed | missed |" in rendered
    assert "- cross-encoder against none: up 1, down 0, same 2." in rendered
    assert "- LLM against none: up 1, down 1, same 1." in rendered
    assert "- The rerankers rank differently in 1 of 3 cases: b." in rendered
    assert "| c | RerankerTimeout |" in rendered
    assert "- a: 4 of 10 ordered; the rest kept retrieval's order." in rendered


def test_the_last_section_shows_what_each_step_adds_per_case() -> None:
    rendered = render_strategy_comparison(COMPARISON)
    # Only this section changes from run to run, so it comes last.
    stable, varying = rendered.split("## Latency and cost\n")

    assert "ms" not in stable
    assert "mps" not in stable
    assert "- Cross-encoder device: mps." in varying
    assert "| retrieval | 200.0 | 300.0 | 3 | 0 |" in varying
    assert "| cross-encoder | 500.0 | 500.0 | 3 | 0 |" in varying
    assert "| LLM | 2000.0 | 3000.0 | 2 | 0.000100 |" in varying
    assert "- LLM tokens: 300 in, 30 out; estimated cost: 0.000300 USD" in varying


def test_an_unknown_price_is_reported_as_unknown() -> None:
    unpriced = dataclasses.replace(
        COMPARISON, fallbacks=(), usages=(), cost_usd=None, partial=()
    )

    rendered = render_strategy_comparison(unpriced)

    assert "| LLM | 2000.0 | 3000.0 | 0 | unknown |" in rendered
    assert "estimated cost: unknown" in rendered
    assert "## Fallbacks\n\nCases where the language model's ranking was not" in (
        rendered
    )
    assert "- None." in rendered


def swapped_cases(report: DatasetReport) -> DatasetReport:
    return dataclasses.replace(report, outcomes=report.outcomes[::-1])


@pytest.mark.parametrize(
    "comparison",
    [
        # The cross-encoder's rows in another case order.
        dataclasses.replace(
            COMPARISON, cross_encoder=swapped_cases(COMPARISON.cross_encoder)
        ),
        # One report too short.
        dataclasses.replace(
            COMPARISON,
            llm=dataclasses.replace(
                COMPARISON.llm, outcomes=COMPARISON.llm.outcomes[:2]
            ),
        ),
        # A reranked report in the place of no reranker.
        dataclasses.replace(COMPARISON, retrieved=COMPARISON.cross_encoder),
        # A report from a run with another limit.
        dataclasses.replace(
            COMPARISON, llm=dataclasses.replace(COMPARISON.llm, limit=5)
        ),
    ],
)
def test_reports_that_do_not_line_up_are_refused(
    comparison: StrategyComparison,
) -> None:
    with pytest.raises(ValueError, match=r"three reports|rerankers"):
        render_strategy_comparison(comparison)
