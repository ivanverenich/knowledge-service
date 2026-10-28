"""Measure exact and approximate vector search on a seeded corpus.

`run_benchmark` answers one question: what would an HNSW index buy, and
cost, compared with the exact search the service runs today? It seeds a
synthetic corpus from a fixed seed, so every run searches the same vectors
with the same queries, and compares three plans:

- `exact`: `vector_search_statement`, unchanged. PostgreSQL compares the
  query with every vector of the space, so its result is the true top-k
  and the baseline for recall.
- `hnsw`: the same permission filter, ordered by distance alone, so that an
  HNSW index on the space can serve it. The index hands over at most
  `hnsw.ef_search` candidates and the permission filter runs after it, so
  eligible Chunks can go missing.
- `hnsw_iterative`: the same with `hnsw.iterative_scan = strict_order`,
  which keeps reading the index until enough rows pass the filter.

Every plan selects only Chunk IDs, and each statement is built before its
timer starts, so the timings compare the searches alone. Everything happens
in one transaction that is always rolled back, so a run leaves no rows and
no index behind. It refuses a database that holds any
Source or Embedding Space, so it never times or reads real content. The
report holds counts, timings, and plans: never Chunk text or vectors, and
a database error is raised as `VectorBenchmarkError` naming only its kind,
because the driver's own message would repeat the seeded rows.
"""

import contextlib
import math
import random
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

import psycopg
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector as VectorType
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.ext.compiler import compiles

from knowledge_service.access import AuthorizationContext, SubjectKind
from knowledge_service.database import DatabaseRuntime
from knowledge_service.embeddings import Vector, unit_length
from knowledge_service.identifiers import (
    ChunkId,
    DocumentId,
    EmbeddingSpaceId,
    GroupId,
    UserId,
)
from knowledge_service.persistence import (
    access_grants,
    chunk_embeddings,
    chunks,
    documents,
    eligible_chunks_predicate,
    embedding_spaces,
    insert_chunk_embeddings,
    metadata_predicate,
    sources,
)
from knowledge_service.vector_search import MAX_CANDIDATES, vector_search_statement

# pgvector's HNSW index holds vectors of at most 2000 dimensions.
MAX_HNSW_DIMENSIONS = 2000
MAX_CHUNKS = 1_000_000
# Seeded numbers (Chunks, centres, and queries, times dimensions) one run
# may hold in memory: about 1 GiB of Python floats.
MAX_SEEDED_VALUES = 32_000_000
# The retrieval p95 target in docs/operations.md.
RETRIEVAL_P95_BUDGET_MS = 750
# Server settings that shape index builds and searches, shown in the report.
SERVER_SETTINGS = (
    "maintenance_work_mem",
    "max_parallel_maintenance_workers",
    "work_mem",
    "shared_buffers",
    "hnsw.max_scan_tuples",
)
INDEX_NAME = "ix_benchmark_chunk_embeddings_hnsw"
SEEDED_AT = datetime(2026, 1, 1, tzinfo=UTC)
# Every seeded Chunk's text starts with this, so a test can prove that no
# report ever carries Chunk text.
SEEDED_TEXT = "seeded benchmark chunk"
# The reader the benchmark searches as, and the Group private Documents are
# granted to instead.
READER = UserId(UUID(int=1))
READERS_GROUP = GroupId(UUID(int=2))
OTHER_GROUP = GroupId(UUID(int=3))
# How far a Chunk lies from its cluster's centre, per dimension.
CLUSTER_SPREAD = 0.35
SEEDED_TABLES = (
    "sources, embedding_spaces, documents, access_grants, chunks, chunk_embeddings"
)
_VECTOR_LITERAL = re.compile(r"'\[[^\]]*\]'")


class VectorBenchmarkError(Exception):
    """The benchmark could not run."""


class InvalidBenchmark(ValueError):
    """A benchmark parameter is out of range."""


class BenchmarkRefused(VectorBenchmarkError):
    """The database holds content, so the benchmark will not run there."""


class SearchPlan(StrEnum):
    EXACT = "exact"
    HNSW = "hnsw"
    HNSW_ITERATIVE = "hnsw_iterative"


@dataclass(frozen=True, slots=True)
class BenchmarkParameters:
    """What to seed and how to search it; every run with equal values is equal.

    `private_share` of the Documents are granted only to a Group the reader
    is not in. `warmup` queries run before each plan is timed and are not
    counted. The `hnsw_*` values are pgvector's index and search settings.
    """

    seed: int = 7
    documents: int = 2_000
    chunks_per_document: int = 10
    dimensions: int = 384
    clusters: int = 50
    private_share: float = 0.5
    queries: int = 50
    warmup: int = 5
    k: int = 10
    hnsw_m: int = 16
    hnsw_ef_construction: int = 64
    hnsw_ef_search: int = 40

    def __post_init__(self) -> None:
        _check_int("seed", self.seed, 0, 2**63 - 1)
        _check_int("documents", self.documents, 1, MAX_CHUNKS)
        _check_int("chunks_per_document", self.chunks_per_document, 1, 100)
        if self.documents * self.chunks_per_document > MAX_CHUNKS:
            raise InvalidBenchmark(f"at most {MAX_CHUNKS} Chunks may be seeded")
        _check_int("dimensions", self.dimensions, 2, MAX_HNSW_DIMENSIONS)
        _check_int("clusters", self.clusters, 1, 10_000)
        if (
            type(self.private_share) not in (int, float)
            or not math.isfinite(self.private_share)
            or not 0 <= self.private_share < 1
        ):
            raise InvalidBenchmark("private_share must be at least 0 and below 1")
        _check_int("queries", self.queries, 1, 1_000)
        _check_int("warmup", self.warmup, 0, 1_000)
        _check_int("k", self.k, 1, MAX_CANDIDATES)
        _check_int("hnsw_m", self.hnsw_m, 2, 100)
        _check_int(
            "hnsw_ef_construction", self.hnsw_ef_construction, 2 * self.hnsw_m, 1_000
        )
        _check_int("hnsw_ef_search", self.hnsw_ef_search, 1, 1_000)
        points = (
            self.documents * self.chunks_per_document
            + self.clusters
            + self.warmup
            + self.queries
        )
        if points * self.dimensions > MAX_SEEDED_VALUES:
            raise InvalidBenchmark(
                f"at most {MAX_SEEDED_VALUES} seeded numbers fit in one run"
            )


def _check_int(name: str, value: int, low: int, high: int) -> None:
    # bool is an int, so `True` would otherwise pass as 1.
    if type(value) is not int or not low <= value <= high:
        raise InvalidBenchmark(f"{name} must be an int from {low} to {high}")


@dataclass(frozen=True, slots=True)
class SeededChunk:
    chunk_id: ChunkId
    document_id: DocumentId
    ordinal: int
    vector: Vector


@dataclass(frozen=True, slots=True)
class SeededCorpus:
    """The Documents, Chunks, and queries one seed gives.

    Vectors and queries have length 1. The first `warmup` queries are for
    warming up only.
    """

    documents: tuple[DocumentId, ...]
    private: frozenset[DocumentId]
    chunks: tuple[SeededChunk, ...]
    queries: tuple[Vector, ...]

    def eligible_chunk_ids(self) -> frozenset[ChunkId]:
        """The Chunks the benchmark's reader may see."""
        return frozenset(
            chunk.chunk_id
            for chunk in self.chunks
            if chunk.document_id not in self.private
        )


def seeded_corpus(parameters: BenchmarkParameters) -> SeededCorpus:
    """Build the corpus for these parameters, the same one every time.

    Chunks lie around `clusters` random centres, as texts on a few topics
    do, and every query lies near one of the centres.
    """
    # Repeatable on purpose: the same seed must give the same corpus.
    rng = random.Random(parameters.seed)  # noqa: S311

    def point_near(centre: Sequence[float]) -> Vector:
        return unit_length([value + rng.gauss(0.0, CLUSTER_SPREAD) for value in centre])

    centres = [
        [rng.gauss(0.0, 1.0) for _ in range(parameters.dimensions)]
        for _ in range(parameters.clusters)
    ]
    document_ids = tuple(
        DocumentId(UUID(int=rng.getrandbits(128))) for _ in range(parameters.documents)
    )
    private = frozenset(
        rng.sample(
            document_ids, math.floor(parameters.documents * parameters.private_share)
        )
    )
    seeded = tuple(
        SeededChunk(
            chunk_id=ChunkId(UUID(int=rng.getrandbits(128))),
            document_id=document_id,
            ordinal=ordinal,
            vector=point_near(rng.choice(centres)),
        )
        for document_id in document_ids
        for ordinal in range(parameters.chunks_per_document)
    )
    queries = tuple(
        point_near(rng.choice(centres))
        for _ in range(parameters.warmup + parameters.queries)
    )
    return SeededCorpus(
        documents=document_ids, private=private, chunks=seeded, queries=queries
    )


@dataclass(frozen=True, slots=True)
class LatencySummary:
    """Nearest-rank percentiles of the timed queries, in milliseconds."""

    p50_ms: float
    p95_ms: float
    max_ms: float


def latency_summary(seconds: Sequence[float]) -> LatencySummary:
    """Summarise query times; raises `ValueError` when there are none."""
    if not seconds:
        raise ValueError("no query was timed")
    ordered = sorted(seconds)

    def percentile(share: float) -> float:
        rank = max(1, math.ceil(share * len(ordered)))
        return ordered[rank - 1] * 1000

    return LatencySummary(
        p50_ms=percentile(0.50), p95_ms=percentile(0.95), max_ms=ordered[-1] * 1000
    )


def recall(exact: Sequence[ChunkId], found: Sequence[ChunkId]) -> float:
    """The share of the exact top-k that a plan found; 1 when there is none."""
    if not exact:
        return 1.0
    return len(set(exact) & set(found)) / len(exact)


@dataclass(frozen=True, slots=True)
class PlanResult:
    """How one plan did over every timed query.

    `short_results` counts queries that returned fewer rows than the exact
    plan. `ineligible_results` counts returned rows the reader may not see,
    and must be 0. `explain` is `EXPLAIN (ANALYZE, BUFFERS)` of the first
    timed query, with vectors left out, and `uses_index` says whether that
    plan read the HNSW index: when few Chunks are eligible, PostgreSQL may
    sort them all instead, which is exact.
    """

    plan: SearchPlan
    uses_index: bool
    latency: LatencySummary
    mean_recall: float
    lowest_recall: float
    short_results: int
    ineligible_results: int
    explain: str


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    parameters: BenchmarkParameters
    postgres_version: str
    pgvector_version: str
    server_settings: tuple[tuple[str, str], ...]
    chunks: int
    eligible_chunks: int
    table_bytes: int
    index_bytes: int
    index_build_seconds: float
    results: tuple[PlanResult, ...]


async def run_benchmark(
    runtime: DatabaseRuntime,
    parameters: BenchmarkParameters,
    *,
    clock: Callable[[], float] = time.perf_counter,
) -> BenchmarkReport:
    """Seed, index, and search in one transaction, then roll all of it back.

    The seeded tables are vacuumed before and after, because rolled-back
    rows stay on disk as dead rows until then, and they would inflate the
    sizes and the full scans of the next run. Raises `BenchmarkRefused`
    before writing anything when the database holds a Source or an
    Embedding Space.
    """
    corpus = seeded_corpus(parameters)
    try:
        async with runtime.engine.connect() as connection:
            await _refuse_unless_empty(connection)
            await connection.rollback()
        await _vacuum(runtime)
        try:
            report = await _measure_and_roll_back(runtime, parameters, corpus, clock)
        except BaseException:
            # The run's own error says more than a failed clean-up would.
            with contextlib.suppress(DBAPIError):
                await _vacuum(runtime)
            raise
        await _vacuum(runtime)
    except DBAPIError as error:
        raise VectorBenchmarkError(
            f"the database refused a benchmark step: {type(error.orig).__name__}"
        ) from None
    return report


async def _measure_and_roll_back(
    runtime: DatabaseRuntime,
    parameters: BenchmarkParameters,
    corpus: SeededCorpus,
    clock: Callable[[], float],
) -> BenchmarkReport:
    async with runtime.engine.connect() as connection:
        raw = await connection.get_raw_connection()
        driver = cast(psycopg.AsyncConnection[Any], raw.driver_connection)
        prepare_threshold = driver.prepare_threshold
        # psycopg prepares a statement on the server after a few runs, and
        # PostgreSQL may then plan it once for every query. Off, each query
        # gets its own plan, the same one its EXPLAIN shows.
        driver.prepare_threshold = None
        transaction = await connection.begin()
        try:
            return await _measure(connection, parameters, corpus, clock)
        finally:
            await transaction.rollback()
            driver.prepare_threshold = prepare_threshold


async def _measure(
    connection: AsyncConnection,
    parameters: BenchmarkParameters,
    corpus: SeededCorpus,
    clock: Callable[[], float],
) -> BenchmarkReport:
    # Checked again inside the transaction, in case content arrived since.
    await _refuse_unless_empty(connection)
    space_id = await _seed(connection, parameters, corpus)
    await connection.execute(sa.text(f"ANALYZE {SEEDED_TABLES}"))
    table_bytes = await _size(connection, "chunk_embeddings", total=True)
    context = AuthorizationContext(user_id=READER, group_ids=frozenset({READERS_GROUP}))
    eligible = corpus.eligible_chunk_ids()

    def exact(query: Vector) -> sa.Select[*tuple[Any, ...]]:
        return vector_search_statement(
            space_id=space_id,
            query=query,
            eligibility=eligible_chunks_predicate(context),
            restriction=metadata_predicate(()),
            limit=parameters.k,
        ).with_only_columns(chunks.c.chunk_id)

    def approximate(query: Vector) -> sa.Select[*tuple[Any, ...]]:
        return approximate_search_statement(
            space_id=space_id,
            dimensions=parameters.dimensions,
            query=query,
            eligibility=eligible_chunks_predicate(context),
            limit=parameters.k,
        )

    baseline, exact_result = await _run_plan(
        connection, SearchPlan.EXACT, exact, parameters, corpus, eligible, None, clock
    )
    started = clock()
    await connection.execute(sa.text(_create_index_sql(parameters, space_id)))
    index_build_seconds = clock() - started
    index_bytes = await _size(connection, INDEX_NAME, total=False)
    await _set(connection, "hnsw.ef_search", str(parameters.hnsw_ef_search))
    _, hnsw_result = await _run_plan(
        connection,
        SearchPlan.HNSW,
        approximate,
        parameters,
        corpus,
        eligible,
        baseline,
        clock,
    )
    await _set(connection, "hnsw.iterative_scan", "strict_order")
    _, iterative_result = await _run_plan(
        connection,
        SearchPlan.HNSW_ITERATIVE,
        approximate,
        parameters,
        corpus,
        eligible,
        baseline,
        clock,
    )
    return BenchmarkReport(
        parameters=parameters,
        postgres_version=await _show(connection, "server_version"),
        pgvector_version=cast(
            str,
            await connection.scalar(
                sa.text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            ),
        ),
        server_settings=tuple(
            [(name, await _show(connection, name)) for name in SERVER_SETTINGS]
        ),
        chunks=len(corpus.chunks),
        eligible_chunks=len(eligible),
        table_bytes=table_bytes,
        index_bytes=index_bytes,
        index_build_seconds=index_build_seconds,
        results=(exact_result, hnsw_result, iterative_result),
    )


def render_report(report: BenchmarkReport) -> str:
    """The report as Markdown: settings, sizes, results, and each plan."""
    parameters = report.parameters
    lines = [
        "# Vector query plans",
        "",
        f"PostgreSQL {report.postgres_version}, pgvector {report.pgvector_version},"
        f" seed {parameters.seed}.",
        "",
        "## Data",
        "",
        "| Chunks | Eligible to the reader | Dimensions | Clusters"
        " | Table size | HNSW index size | Index build |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {report.chunks} | {report.eligible_chunks} | {parameters.dimensions}"
        f" | {parameters.clusters} | {_mebibytes(report.table_bytes)}"
        f" | {_mebibytes(report.index_bytes)}"
        f" | {report.index_build_seconds:.2f} s |",
        "",
        "## Parameters",
        "",
        "| k | Timed queries | Warmup queries per plan | Private share"
        " | m | ef_construction | ef_search |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        f"| {parameters.k} | {parameters.queries} | {parameters.warmup}"
        f" | {parameters.private_share} | {parameters.hnsw_m}"
        f" | {parameters.hnsw_ef_construction} | {parameters.hnsw_ef_search} |",
        "",
        "| Server setting | Value |",
        "|---|---|",
        *(f"| {name} | {value} |" for name, value in report.server_settings),
        "",
        "## Results",
        "",
        "Recall is against the exact plan's top-k for the same query.",
        "",
        "| Plan | Uses HNSW index | p50 | p95 | max | Mean recall@k"
        " | Lowest recall@k | Short results | Ineligible results |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    lines += [
        f"| {result.plan} | {'yes' if result.uses_index else 'no'}"
        f" | {result.latency.p50_ms:.2f} ms | {result.latency.p95_ms:.2f} ms"
        f" | {result.latency.max_ms:.2f} ms | {result.mean_recall:.3f}"
        f" | {result.lowest_recall:.3f} | {result.short_results}"
        f" | {result.ineligible_results} |"
        for result in report.results
    ]
    exact = report.results[0]
    lines += [
        "",
        "## Baseline",
        "",
        "The baseline is `exact`, the search the service runs today. It ranks"
        " every eligible Chunk, so its recall is 1 by definition, and the"
        " other plans are measured against it.",
        f"Its p95 here is {exact.latency.p95_ms:.2f} ms,"
        f" {exact.latency.p95_ms / RETRIEVAL_P95_BUDGET_MS:.1%} of the"
        f" {RETRIEVAL_P95_BUDGET_MS} ms retrieval budget.",
    ]
    for result in report.results:
        lines += ["", f"## Plan: {result.plan}", "", "```text", result.explain, "```"]
    return "\n".join(lines) + "\n"


def _mebibytes(size: int) -> str:
    return f"{size / 1024 / 1024:.1f} MiB"


async def _refuse_unless_empty(connection: AsyncConnection) -> None:
    for table in (sources, embedding_spaces):
        if await connection.scalar(sa.select(sa.exists().select_from(table))):
            raise BenchmarkRefused("the benchmark runs only on an empty database")


async def _vacuum(runtime: DatabaseRuntime) -> None:
    # VACUUM cannot run inside a transaction block, and after a large run it
    # can take longer than a request may, so the time limit is lifted.
    async with runtime.engine.connect() as connection:
        autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
        await autocommit.execute(sa.text("SET statement_timeout = 0"))
        try:
            await autocommit.execute(sa.text(f"VACUUM {SEEDED_TABLES}"))
        finally:
            await autocommit.execute(sa.text("RESET statement_timeout"))


def approximate_search_statement(
    *,
    space_id: EmbeddingSpaceId,
    dimensions: int,
    query: Vector,
    eligibility: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    """The search an HNSW index on one space can serve.

    It differs from `vector_search_statement` in what the index needs: the
    vector cast to the space's length, the space written into the SQL so
    the planner can match the index's `WHERE`, and distance as the only
    sort key, so ties are no longer broken by Chunk ID.
    """
    distance = sa.cast(chunk_embeddings.c.embedding, VectorType(dimensions))
    distance = distance.cosine_distance(list(query))
    return (
        sa.select(chunks.c.chunk_id)
        .select_from(chunk_embeddings)
        .join(chunks, chunks.c.chunk_id == chunk_embeddings.c.chunk_id)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(
            chunk_embeddings.c.space_id
            == sa.bindparam("space_id", space_id.value, literal_execute=True),
            sa.exists().where(
                embedding_spaces.c.space_id == space_id.value,
                embedding_spaces.c.is_current,
            ),
            eligibility,
        )
        .order_by(distance)
        .limit(limit)
    )


def _create_index_sql(
    parameters: BenchmarkParameters, space_id: EmbeddingSpaceId
) -> str:
    # Every value is a checked int or a UUID, so none can carry SQL.
    return (
        f"CREATE INDEX {INDEX_NAME} ON chunk_embeddings USING hnsw"
        f" ((embedding::vector({parameters.dimensions})) vector_cosine_ops)"
        f" WITH (m = {parameters.hnsw_m},"
        f" ef_construction = {parameters.hnsw_ef_construction})"
        f" WHERE space_id = '{space_id.value}'"
    )


async def _run_plan(
    connection: AsyncConnection,
    plan: SearchPlan,
    statement: Callable[[Vector], sa.Select[*tuple[Any, ...]]],
    parameters: BenchmarkParameters,
    corpus: SeededCorpus,
    eligible: frozenset[ChunkId],
    baseline: Sequence[Sequence[ChunkId]] | None,
    clock: Callable[[], float],
) -> tuple[list[list[ChunkId]], PlanResult]:
    for query in corpus.queries[: parameters.warmup]:
        await connection.execute(statement(query))
    timed = corpus.queries[parameters.warmup :]
    found: list[list[ChunkId]] = []
    seconds: list[float] = []
    for query in timed:
        built = statement(query)
        started = clock()
        rows = (await connection.execute(built)).mappings().all()
        seconds.append(clock() - started)
        found.append([ChunkId(cast(UUID, row["chunk_id"])) for row in rows])
    expected = found if baseline is None else baseline
    recalls = [recall(e, f) for e, f in zip(expected, found, strict=True)]
    explained = await connection.execute(_Explain(statement(timed[0])))
    explain = "\n".join(cast(str, row[0]) for row in explained)
    return found, PlanResult(
        plan=plan,
        uses_index=INDEX_NAME in explain,
        latency=latency_summary(seconds),
        mean_recall=sum(recalls) / len(recalls),
        lowest_recall=min(recalls),
        short_results=sum(
            len(f) < len(e) for e, f in zip(expected, found, strict=True)
        ),
        ineligible_results=sum(
            chunk_id not in eligible for result in found for chunk_id in result
        ),
        explain=_VECTOR_LITERAL.sub("'[vector]'", explain),
    )


async def _seed(
    connection: AsyncConnection, parameters: BenchmarkParameters, corpus: SeededCorpus
) -> EmbeddingSpaceId:
    source_id = UUID(int=4)
    space_id = EmbeddingSpaceId(UUID(int=5))
    await connection.execute(
        sa.insert(sources).values(
            source_id=source_id,
            kind="local_directory",
            location="/benchmark",
            display_name="Benchmark",
            enabled=True,
            created_at=SEEDED_AT,
            updated_at=SEEDED_AT,
        )
    )
    await connection.execute(
        sa.insert(embedding_spaces).values(
            space_id=space_id.value,
            provider="benchmark",
            model="seeded",
            dimensions=parameters.dimensions,
            is_current=True,
            created_at=SEEDED_AT,
        )
    )
    await connection.execute(
        sa.insert(documents),
        [
            {
                "document_id": document_id.value,
                "source_id": source_id,
                "external_id": f"benchmark-{number}",
                "source_version": None,
                "title": f"Benchmark {number}",
                "content_version": 1,
                "content_fingerprint": "benchmark",
                "authorization_version": 1,
                "authorization_fingerprint": "benchmark",
                "availability": "available",
                "created_at": SEEDED_AT,
                "updated_at": SEEDED_AT,
            }
            for number, document_id in enumerate(corpus.documents)
        ],
    )
    await connection.execute(
        sa.insert(access_grants),
        [
            _grant(document_id, document_id in corpus.private)
            for document_id in corpus.documents
        ],
    )
    await connection.execute(
        sa.insert(chunks),
        [
            {
                "chunk_id": chunk.chunk_id.value,
                "document_id": chunk.document_id.value,
                "content_version": 1,
                "ordinal": chunk.ordinal,
                "text": f"{SEEDED_TEXT} {number}",
                "heading_path": None,
                "token_count": 1,
                "content_fingerprint": "benchmark",
                "source_anchor": None,
            }
            for number, chunk in enumerate(corpus.chunks)
        ],
    )
    await insert_chunk_embeddings(
        connection,
        space_id=space_id,
        dimensions=parameters.dimensions,
        vectors={chunk.chunk_id: chunk.vector for chunk in corpus.chunks},
        created_at=SEEDED_AT,
    )
    return space_id


def _grant(document_id: DocumentId, private: bool) -> Mapping[str, object]:
    return {
        "grant_id": UUID(int=document_id.value.int ^ 1),
        "document_id": document_id.value,
        "subject_kind": (SubjectKind.GROUP if private else SubjectKind.PUBLIC).value,
        "subject_id": OTHER_GROUP.value if private else None,
        "granted_at": SEEDED_AT,
    }


async def _set(connection: AsyncConnection, name: str, value: str) -> None:
    # `true`: the setting ends with the transaction.
    await connection.execute(sa.select(sa.func.set_config(name, value, True)))


async def _show(connection: AsyncConnection, name: str) -> str:
    # `true`: an unknown setting reads as NULL instead of failing.
    value = await connection.scalar(sa.select(sa.func.current_setting(name, True)))
    return "unset" if value is None else cast(str, value)


async def _size(connection: AsyncConnection, relation: str, *, total: bool) -> int:
    function = sa.func.pg_total_relation_size if total else sa.func.pg_relation_size
    return cast(
        int,
        await connection.scalar(
            sa.select(function(sa.text(f"'{relation}'::regclass")))
        ),
    )


class _Explain(sa.Executable, sa.ClauseElement):
    """`EXPLAIN (ANALYZE, BUFFERS)` of a statement, keeping its parameters."""

    inherit_cache = False

    def __init__(self, statement: sa.Select[*tuple[Any, ...]]) -> None:
        self.statement = statement


@compiles(_Explain)
def _compile_explain(element: _Explain, compiler: Any, **kw: Any) -> str:
    return "EXPLAIN (ANALYZE, BUFFERS) " + compiler.process(element.statement, **kw)
