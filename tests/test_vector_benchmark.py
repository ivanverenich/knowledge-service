"""The benchmark's parameters, corpus, and arithmetic, without a database."""

import math
from dataclasses import replace

import pytest

from knowledge_service.identifiers import ChunkId
from knowledge_service.vector_benchmark import (
    MAX_HNSW_DIMENSIONS,
    BenchmarkParameters,
    BenchmarkReport,
    InvalidBenchmark,
    LatencySummary,
    PlanResult,
    SearchPlan,
    latency_summary,
    recall,
    render_report,
    seeded_corpus,
)

SMALL = BenchmarkParameters(
    documents=20, chunks_per_document=3, dimensions=8, queries=4, warmup=2
)
A, B, C, D = (ChunkId.new() for _ in range(4))


def test_the_defaults_and_the_production_length_are_valid() -> None:
    BenchmarkParameters()
    BenchmarkParameters(dimensions=1536)


@pytest.mark.parametrize(
    "changes",
    [
        {"k": 0},
        {"k": 101},
        {"k": True},
        {"dimensions": MAX_HNSW_DIMENSIONS + 1},
        {"dimensions": 1},
        {"documents": 0},
        {"documents": 1_000_000, "chunks_per_document": 2},
        {"private_share": 1.0},
        {"private_share": -0.1},
        {"private_share": math.nan},
        {"private_share": True},
        {"queries": 0},
        {"warmup": -1},
        {"seed": -1},
        {"hnsw_m": 1},
        {"hnsw_m": 16, "hnsw_ef_construction": 31},
        {"hnsw_ef_search": 0},
        {"clusters": 2.0},
        # 200,000 Chunks of 1,600 numbers would need about 10 GiB of memory.
        {"documents": 20_000, "dimensions": 1_600},
        {"clusters": 10_000, "dimensions": 2_000, "documents": 700},
    ],
)
def test_out_of_range_parameters_are_refused(changes: dict[str, object]) -> None:
    with pytest.raises(InvalidBenchmark):
        BenchmarkParameters(**changes)  # pyright: ignore[reportArgumentType]


def test_the_same_seed_gives_the_same_corpus() -> None:
    assert seeded_corpus(SMALL) == seeded_corpus(SMALL)


def test_another_seed_gives_another_corpus() -> None:
    first = seeded_corpus(SMALL)
    second = seeded_corpus(replace(SMALL, seed=8))

    assert first.chunks[0].vector != second.chunks[0].vector
    assert first.queries != second.queries


def test_the_corpus_has_the_asked_size_and_unit_vectors() -> None:
    corpus = seeded_corpus(replace(SMALL, private_share=0.5))

    assert len(corpus.documents) == 20
    assert len(corpus.chunks) == 60
    assert len(corpus.private) == 10
    assert len(corpus.queries) == SMALL.warmup + SMALL.queries
    assert len(corpus.eligible_chunk_ids()) == 30
    for vector in (*(chunk.vector for chunk in corpus.chunks), *corpus.queries):
        assert len(vector) == SMALL.dimensions
        assert math.isclose(math.hypot(*vector), 1.0, rel_tol=1e-9)


@pytest.mark.parametrize(
    ("exact", "found", "expected"),
    [
        ((A, B), (B, A), 1.0),
        ((A, B), (A, C), 0.5),
        ((A, B), (), 0.0),
        ((A, B, C, D), (D,), 0.25),
        ((), (), 1.0),
    ],
)
def test_recall_is_the_share_of_the_exact_top_k_found(
    exact: tuple[ChunkId, ...], found: tuple[ChunkId, ...], expected: float
) -> None:
    assert recall(exact, found) == expected


def test_latency_uses_nearest_rank_percentiles() -> None:
    seconds = [n / 1000 for n in range(20, 0, -1)]

    summary = latency_summary(seconds)

    assert summary == LatencySummary(p50_ms=10.0, p95_ms=19.0, max_ms=20.0)


def test_no_timed_query_has_no_latency() -> None:
    with pytest.raises(ValueError, match="no query"):
        latency_summary([])


def test_the_report_shows_settings_sizes_results_and_plans() -> None:
    result = PlanResult(
        plan=SearchPlan.HNSW,
        uses_index=True,
        latency=LatencySummary(p50_ms=1.5, p95_ms=2.25, max_ms=3.0),
        mean_recall=0.875,
        lowest_recall=0.5,
        short_results=2,
        ineligible_results=0,
        explain="Limit\n  ->  Index Scan using ix_benchmark_chunk_embeddings_hnsw",
    )
    report = BenchmarkReport(
        parameters=SMALL,
        postgres_version="17.6",
        pgvector_version="0.8.7",
        server_settings=(("maintenance_work_mem", "64MB"), ("work_mem", "4MB")),
        chunks=60,
        eligible_chunks=30,
        table_bytes=3 * 1024 * 1024,
        index_bytes=1024 * 1024,
        index_build_seconds=0.25,
        results=(result,),
    )

    rendered = render_report(report)

    assert "PostgreSQL 17.6, pgvector 0.8.7, seed 7." in rendered
    assert "| 60 | 30 | 8 | 50 | 3.0 MiB | 1.0 MiB | 0.25 s |" in rendered
    assert "| 10 | 4 | 2 | 0.5 | 16 | 64 | 40 |" in rendered
    assert "| maintenance_work_mem | 64MB |\n| work_mem | 4MB |" in rendered
    assert "The baseline is `exact`" in rendered
    assert "Its p95 here is 2.25 ms, 0.3% of the 750 ms retrieval budget." in rendered
    assert (
        "| hnsw | yes | 1.50 ms | 2.25 ms | 3.00 ms | 0.875 | 0.500 | 2 | 0 |"
        in rendered
    )
    assert "## Plan: hnsw\n\n```text\nLimit\n" in rendered
