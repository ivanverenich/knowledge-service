"""The benchmark on real PostgreSQL: plans, recall, privacy, and no trace."""

import re
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from dataclasses import replace
from itertools import count
from typing import Any, cast

import psycopg
import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service import vector_benchmark
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import ChunkId, EmbeddingSpaceId
from knowledge_service.persistence import chunks, sources
from knowledge_service.settings import Settings
from knowledge_service.vector_benchmark import (
    INDEX_NAME,
    SEEDED_TABLES,
    SEEDED_TEXT,
    BenchmarkParameters,
    BenchmarkRefused,
    SearchPlan,
    VectorBenchmarkError,
    render_report,
    run_benchmark,
)

# Big enough that PostgreSQL reads the HNSW index for the approximate plans;
# half the Documents are private and the index hands over only 10 rows.
PARAMETERS = BenchmarkParameters(
    documents=100,
    chunks_per_document=10,
    dimensions=16,
    clusters=10,
    queries=8,
    warmup=2,
    private_share=0.5,
    hnsw_ef_search=10,
)
# psycopg's default: prepare a statement on the server from its fifth run.
DEFAULT_PREPARE_THRESHOLD = 5


def a_runtime(
    database_url: str, *, statement_timeout_ms: int = 10_000
) -> DatabaseRuntime:
    # One pooled connection, so a test can inspect the one the run used.
    return create_database_runtime(
        Settings(
            database_url=SecretStr(database_url),
            database_pool_size=1,
            database_pool_max_overflow=0,
            database_statement_timeout_ms=statement_timeout_ms,
        )
    )


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = a_runtime(migrated_database)
    try:
        yield runtime
    finally:
        await runtime.dispose()


def ticking_clock() -> Iterator[float]:
    """A clock that moves one millisecond each time it is read."""
    return (tick / 1000 for tick in count())


async def seeded_table_bytes(runtime: DatabaseRuntime) -> dict[str, int]:
    async with runtime.transaction() as connection:
        return {
            table: cast(
                int,
                await connection.scalar(
                    sa.select(sa.func.pg_relation_size(sa.text(f"'{table}'::regclass")))
                ),
            )
            for table in SEEDED_TABLES.split(", ")
        }


async def prepare_threshold(runtime: DatabaseRuntime) -> int | None:
    async with runtime.engine.connect() as connection:
        raw = await connection.get_raw_connection()
        return cast(
            psycopg.AsyncConnection[Any], raw.driver_connection
        ).prepare_threshold


@pytest.mark.integration
async def test_a_run_compares_three_plans_and_leaves_nothing_behind(
    runtime: DatabaseRuntime,
) -> None:
    ticks = ticking_clock()

    report = await run_benchmark(runtime, PARAMETERS, clock=lambda: next(ticks))

    exact, hnsw, iterative = report.results
    assert [result.plan for result in report.results] == list(SearchPlan)
    assert (report.chunks, report.eligible_chunks) == (1000, 500)
    assert report.pgvector_version
    assert report.index_bytes > 0
    assert report.index_build_seconds == pytest.approx(0.001)
    assert exact.latency.p50_ms == pytest.approx(1.0)
    assert not exact.uses_index
    assert hnsw.uses_index
    assert iterative.uses_index
    # Rolled back and vacuumed: no rows, no dead rows, no index.
    assert set((await seeded_table_bytes(runtime)).values()) == {0}
    async with runtime.transaction() as connection:
        assert (
            await connection.scalar(sa.select(sa.func.to_regclass(INDEX_NAME))) is None
        )
    assert await prepare_threshold(runtime) == DEFAULT_PREPARE_THRESHOLD


@pytest.mark.integration
async def test_every_plan_warms_up_then_times_each_query_once(
    runtime: DatabaseRuntime,
) -> None:
    searches: list[str] = []

    def record(
        _connection: object,
        _cursor: object,
        statement: str,
        *_: object,
    ) -> None:
        if "<=>" in statement:
            searches.append(statement)

    event.listen(runtime.engine.sync_engine, "before_cursor_execute", record)
    await run_benchmark(runtime, PARAMETERS)

    explains = [search for search in searches if search.startswith("EXPLAIN")]
    # Every plan fetches Chunk IDs only, so no plan pays for fetching text.
    assert not any("chunks.text" in search for search in searches)
    assert len(explains) == len(SearchPlan)
    assert len(searches) - len(explains) == len(SearchPlan) * (
        PARAMETERS.warmup + PARAMETERS.queries
    )


@pytest.mark.integration
async def test_filtering_after_the_index_loses_eligible_chunks_unless_it_iterates(
    runtime: DatabaseRuntime,
) -> None:
    report = await run_benchmark(runtime, PARAMETERS)

    _, hnsw, iterative = report.results
    # Half of the 10 rows the index hands over are private and filtered out.
    assert hnsw.short_results > 0
    assert hnsw.mean_recall < 1
    # Iterating, the index is read until 10 eligible rows are found.
    assert iterative.short_results == 0


@pytest.mark.integration
@pytest.mark.parametrize("private_share", [0.0, 0.5, 0.9])
async def test_no_plan_returns_a_chunk_the_reader_may_not_see(
    runtime: DatabaseRuntime, private_share: float
) -> None:
    report = await run_benchmark(
        runtime, replace(PARAMETERS, private_share=private_share)
    )

    for result in report.results[1:]:
        assert result.ineligible_results == 0
        if not result.uses_index:
            # Without the index the eligible rows are sorted: exact.
            assert result.mean_recall == 1
    assert report.results[0].ineligible_results == 0


@pytest.mark.integration
async def test_the_report_carries_no_chunk_text_and_no_vectors(
    runtime: DatabaseRuntime,
) -> None:
    rendered = render_report(await run_benchmark(runtime, PARAMETERS))

    assert SEEDED_TEXT not in rendered
    assert re.search(r"\[-?[0-9]", rendered) is None
    assert "'[vector]'" in rendered


@pytest.mark.integration
async def test_a_database_error_names_only_its_kind_and_leaves_nothing_behind(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    insert = vector_benchmark.insert_chunk_embeddings

    async def insert_with_the_wrong_length(
        connection: AsyncConnection,
        *,
        space_id: EmbeddingSpaceId,
        dimensions: int,
        vectors: Mapping[ChunkId, Sequence[float]],
        created_at: Any,
    ) -> None:
        # The database refuses these rows, and its message repeats them.
        await insert(
            connection,
            space_id=space_id,
            dimensions=dimensions,
            vectors={chunk_id: [*vector, 0.5] for chunk_id, vector in vectors.items()},
            created_at=created_at,
        )

    monkeypatch.setattr(
        vector_benchmark, "insert_chunk_embeddings", insert_with_the_wrong_length
    )

    with pytest.raises(VectorBenchmarkError) as raised:
        await run_benchmark(runtime, PARAMETERS)

    message = str(raised.value)
    assert message.endswith("CheckViolation")
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__
    assert re.search(r"[0-9]\.[0-9]", message) is None
    assert set((await seeded_table_bytes(runtime)).values()) == {0}
    assert await prepare_threshold(runtime) == DEFAULT_PREPARE_THRESHOLD


@pytest.mark.integration
async def test_cleaning_up_ignores_the_request_time_limit(
    migrated_database: str,
) -> None:
    # A committed delete leaves a dead row behind until VACUUM.
    patient = a_runtime(migrated_database)
    try:
        async with patient.transaction() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO sources (source_id, kind, location, display_name,"
                    " enabled, created_at, updated_at) VALUES (gen_random_uuid(),"
                    " 'local_directory', '/srv/gone', 'Gone', true, now(), now())"
                )
            )
        async with patient.transaction() as connection:
            await connection.execute(sa.delete(sources))
        assert (await seeded_table_bytes(patient))["sources"] > 0
    finally:
        await patient.dispose()
    hurried = a_runtime(migrated_database, statement_timeout_ms=1)

    try:
        # Seeding cannot finish in 1 ms; vacuuming must still happen.
        with pytest.raises(VectorBenchmarkError):
            await run_benchmark(hurried, PARAMETERS)
        assert set((await seeded_table_bytes(hurried)).values()) == {0}
    finally:
        await hurried.dispose()


@pytest.mark.integration
async def test_a_database_with_content_is_refused_and_left_as_it_was(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO sources (source_id, kind, location, display_name,"
                " enabled, created_at, updated_at) VALUES"
                " (gen_random_uuid(), 'local_directory', '/srv/real', 'Real',"
                " true, now(), now())"
            )
        )

    with pytest.raises(BenchmarkRefused):
        await run_benchmark(runtime, PARAMETERS)

    async with runtime.transaction() as connection:
        assert await connection.scalar(sa.select(sources.c.location)) == "/srv/real"
        assert (
            await connection.scalar(sa.select(sa.func.count()).select_from(chunks)) == 0
        )
