# M04-T11 — Benchmark vector query plans

Source task: [M04-T11](../curriculum/milestones/M04-dense-retrieval/M04-T11-benchmark-vector-query-plans.md)

## What you will do

Search today is **exact**: PostgreSQL compares the question's vector with every stored vector the reader may see, then keeps the nearest k. That always finds the true top-k, but its cost grows with the corpus. pgvector also offers an **HNSW index**, which finds *nearly* the nearest vectors much faster. You will measure what that index would buy and cost, on a synthetic corpus that is the same every run, and decide which plan the service should keep.

| Requirement | How this lesson meets it |
|---|---|
| Reproducible seeded corpus | `seeded_corpus` builds Documents, Chunks, vectors and queries from one seed; equal parameters give an equal corpus. |
| Exact versus approximate plans | Three plans on the same queries: `exact` (today's search statement), `hnsw` (an HNSW index, permission filter after it), `hnsw_iterative` (the same, reading on until enough rows pass the filter). |
| Recall, latency, index build cost | Recall@k against the exact result, p50/p95/max latency, index build time and size. |
| EXPLAIN, data size, warmup, parameters | The report holds `EXPLAIN (ANALYZE, BUFFERS)` per plan, table and index sizes, warmup count, every parameter and the server settings that matter. |
| Justified baseline choice | The report states the baseline and its share of the 750 ms retrieval budget; you write the decision and its reasons in the evidence file. |
| Authorization preserved | The permission filter stays in every plan's SQL, and `ineligible_results` counts any row the reader may not see; it must be 0. |
| No seeded text in telemetry | The report holds counts, timings and plans; vectors in plans are replaced by `'[vector]'`; database errors name only their kind. |
| Typed failure or denial | `InvalidBenchmark` for bad parameters; `BenchmarkRefused` for a database that holds content; `VectorBenchmarkError` for a database error. |

This lesson includes coding: Steps 2–4 write code, and Steps 1, 5 and 6 do not.

Terms used in this lesson:

- **Exact search:** compare the query with every eligible vector. Its result is the true top-k.
- **Approximate search (ANN):** use an index that skips most vectors. Much faster, but it may miss some of the true nearest ones.
- **HNSW:** pgvector's graph index. Each vector links to some near neighbours; a search walks the graph towards the query.
- **`ef_search`:** how many candidates one HNSW search collects. Higher means better results and slower searches.
- **Iterative scan:** a pgvector 0.8 setting. When the filter after the index drops rows, the index is read further until k rows pass.
- **Recall@k:** the share of the exact top-k that a plan also found. 1.0 means it found all of them.
- **Warmup:** queries run and thrown away before timing, so caches are filled and the first slow queries do not count.
- **p50 / p95:** the time half / 95% of the queries stayed under.
- **`EXPLAIN (ANALYZE, BUFFERS)`:** PostgreSQL runs the query and shows the plan it used, the time of each part, and the pages it read.
- **Dead rows:** rows deleted or rolled back stay on disk until `VACUUM` removes them.

## Before you begin

- **What already exists.** `vector_search_statement` (M04-T06, M04-T07, M04-T10) is the search: one SQL statement with the current space, the permission filter, and `ORDER BY distance, chunk_id LIMIT k`. The `embedding` column is a plain `vector` with no fixed length (M04-T04), because several spaces with different lengths share the table. There is no vector index yet.
- **Why today's statement cannot use an HNSW index.** Three things must change for PostgreSQL to read such an index, as M04-T04's notes predicted:
  1. HNSW needs a fixed length, so the index is on `embedding::vector(384)`, and only for one space: `WHERE space_id = '<that space>'`. The query must order by the same cast expression.
  2. The planner only matches that `WHERE` when the space is written into the SQL, not sent as a parameter. The approximate statement uses `literal_execute=True` for it.
  3. An index scan gives rows in distance order only; a second sort key (`chunk_id`, our tie-break) forces a full sort. The approximate statement orders by distance alone.

  So the benchmark keeps the real statement as the `exact` plan and builds a second statement, `approximate_search_statement`, for the HNSW plans. The service's search is not changed in this lesson.
- **Why the filter after the index matters.** The permission filter is in the same SQL, but an index scan hands over `ef_search` rows first, and the filter runs on those. If most of them are private, fewer than k remain. Iterative scan reads on until k pass. The benchmark measures both.
- **Why one rolled-back transaction.** Seeding, the index and every search happen in one transaction that is always rolled back, so nothing is left behind. Rolled-back rows still sit on disk as dead rows, so the seeded tables are vacuumed before and after; otherwise the next run's sizes and full scans would include them. The run refuses any database that holds a Source or an Embedding Space, so it never times or touches real content. Use a dedicated database for it (Step 5).
- **Why prepared statements are switched off for the run.** psycopg prepares a statement on the server after its fifth run, and PostgreSQL may then use one plan for all values. A first prototype showed the timed queries using the index while their `EXPLAIN` showed a sort, so the report contradicted itself. With preparing off during the run, every query gets its own plan, the same one its `EXPLAIN` shows; the setting is put back afterwards.
- **What the adversarial review found.** An independent review attacked the prototype against the real database. Every break is fixed below, with a regression test that fails when the fix is removed:
  - **A time limit stopped the clean-up.** `VACUUM` ran with the service's 10-second statement limit, so after a large run it could fail, leave dead rows, and hide the real error. The clean-up now lifts the limit, and a failed clean-up never replaces the run's own error.
  - **Seeded rows leaked into errors.** SQLAlchemy puts the failing statement's parameters (vectors, Chunk text) into its error message. Database errors are now raised as `VectorBenchmarkError` naming only the error's kind, with the original detached.
  - **Huge parameters were accepted.** One million Chunks of 2000 numbers would need about 60 GiB in Python. The total seeded numbers are now capped at 32 million (about 1 GiB).
  - **Timings included Python work and unequal columns.** Building the statement was inside the timed window (7–10% of each number), and the exact plan fetched 11 columns, including the text, against the approximate plan's 2. Statements are now built before the timer, and every plan fetches Chunk IDs only.
  - **Tests that could not fail.** Removing the warmup, the vacuums, or the restore of the prepare setting passed every test. New tests count the statements, measure the tables' size on disk, and read the pooled connection's setting.
  - **No baseline in the report.** The report now states the baseline and its share of the retrieval budget.

  It confirmed what held: a cancelled or failed run leaves no rows and no index; dropping the permission filter from the approximate statement is caught by three tests; the redaction also catches negative numbers and exponents; the f-string SQL only ever holds checked ints, a UUID and constants; recall was identical over seven runs at the test size.
- **Known limits, not fixed here.**
  - HNSW builds its graph with random choices, so approximate recall moves a little between runs even with the same seed. The corpus and queries do not.
  - The corpus is random points around 50 centres, not real text embeddings.
  - One connection, one query at a time: no load. Latency includes one round trip to the database.
  - Rows are measured before they are committed, which differs slightly from committed data.
  - The emptiness check cannot see another session's uncommitted writes, and the index build blocks writes to `chunk_embeddings` until the rollback: use a dedicated database.
  - `uses_index` reads the plan of the first timed query only.
  - The VACUUM before a run has no test of its own; the one after a run does.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and the expected failing check

Create `docs/evidence/M04-T11-vector-query-plans.md` before writing any code. Start it with this text, then adjust the wording to your own:

```markdown
# M04-T11 — Vector query plans evidence

## Invariant

The benchmark measures without changing anything: it runs only on an
empty database, seeds the same corpus for the same parameters, rolls
every row and the index back, and vacuums the dead rows away. Exact
search is the baseline: its result is the true top-k of the Chunks the
reader may see. No plan returns a Chunk the reader may not see, and
neither the report nor an error carries Chunk text or vectors.

## Expected failing check

Before the change, `tests/test_vector_benchmark.py` and
`tests/test_vector_benchmark_integration.py` fail to import
`knowledge_service.vector_benchmark`.

Predictions, written before the first run:

- With half the Documents private, HNSW is several times faster than
  exact at 20,000 Chunks, with mean recall a little below 1.
- With 90% private and `ef_search` 40, plain HNSW returns fewer than k
  rows for most queries; iterative scan returns all k.
- At 1536 dimensions, exact search stays under the 750 ms retrieval
  budget at 20,000 Chunks.
- The index takes seconds to build at 384 dimensions and much longer at
  1536.
- `ineligible_results` is 0 for every plan.

## Design

## Verification

## Baseline decision

## Reflection
```

Check: the file exists, and the predictions say what you expect before you run anything.

### Step 2 — Coding step: the benchmark module

Create `src/knowledge_service/vector_benchmark.py`.

**In plain words:** This file makes up a pretend library of documents from a fixed seed, so it is the same every time. It then answers the same questions three ways: today's careful search that looks at everything, and two fast searches through an index. It records how fast each was, how many of the right answers each found, and whether any answer was something the reader must not see, then undoes everything it wrote.

| Part | What it does |
|---|---|
| `BenchmarkParameters` | Every setting, checked: corpus size, dimensions, privacy share, queries, warmup, k, and the HNSW settings. Out of range, or more than 32 million seeded numbers, raises `InvalidBenchmark`. |
| `seeded_corpus` | Documents, Chunks with vectors around random centres, the private Documents, and the queries, all from one `random.Random(seed)`. |
| `recall`, `latency_summary` | The share of the exact top-k found, and nearest-rank p50/p95/max. |
| `run_benchmark` | Refuses a non-empty database, vacuums, runs `_measure` in one transaction that is always rolled back, vacuums again. Database errors become `VectorBenchmarkError`. |
| `_measure` | Seeds, `ANALYZE`s, runs `exact`, builds the HNSW index and times it, then runs `hnsw` and `hnsw_iterative`. |
| `_run_plan` | Warmup queries, then each timed query with the statement built before the timer; recall, short and ineligible counts, and one redacted `EXPLAIN`. |
| `approximate_search_statement` | The search an HNSW index can serve (cast, literal space, distance as the only sort key), with the same permission filter. |
| `render_report` | Markdown: data, parameters, server settings, results, baseline, and each plan. |

Why the details:

- The exact plan is `vector_search_statement` itself, narrowed with `.with_only_columns(chunks.c.chunk_id)`. Its FROM, WHERE, ORDER BY and LIMIT stay as the service runs them; only the fetched columns shrink, to match the approximate plan.
- The index DDL, the `VACUUM` and the size lookups are built with f-strings. Only checked ints, a UUID and constant names go in, so nothing can carry SQL; the comment says so.
- `random.Random(seed)` is deliberately not a secure generator, so ruff's `S311` is silenced on that one line with the reason.
- `_Explain` is a small SQLAlchemy construct that puts `EXPLAIN (ANALYZE, BUFFERS)` in front of a statement and keeps its parameters and their types.

````python
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
````

Check: `uv run ruff check src/knowledge_service/vector_benchmark.py` and `uv run pyright src/knowledge_service/vector_benchmark.py` report nothing.

### Step 3 — Coding step: the command that runs it

Create `scripts/benchmark_vector_search.py`.

**In plain words:** This is the button you press. It reads your settings from the command line, runs the benchmark against the database you point it at, saves the full report as a file, and prints the main tables. If a setting is wrong or the database is not empty, it says so and stops.

| Part | What it does |
|---|---|
| Options | One `--option` per `BenchmarkParameters` field, with its default, plus `--output`. |
| Settings | `Settings(database_statement_timeout_ms=600_000)`: building a large index takes longer than the service's 10-second request limit. The database URL comes from `KNOWLEDGE_SERVICE_DATABASE_URL`, as for the service. |
| Output | The report goes to `artifacts/benchmarks/vector-query-plans.md` (Git ignores `artifacts/`), and everything before the plans is printed. |
| Exit codes | 0 on success, 1 for `VectorBenchmarkError` (refused or a database error), 2 for invalid parameters. |

Scripts here write with `sys.stdout.write` and `sys.stderr.write`, as `scripts/validate_docs.py` does, because ruff forbids `print`.

````python
"""Run the vector benchmark and write its report.

Point `KNOWLEDGE_SERVICE_DATABASE_URL` at an empty, migrated database; the
benchmark refuses any other. Every `BenchmarkParameters` field is an
option, such as `--documents 2000` or `--private-share 0.9`. The report goes
to `artifacts/benchmarks/vector-query-plans.md`, which Git ignores, and the
results table is printed.
"""

import argparse
import asyncio
import dataclasses
import sys
from pathlib import Path

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.settings import Settings
from knowledge_service.vector_benchmark import (
    BenchmarkParameters,
    BenchmarkReport,
    InvalidBenchmark,
    VectorBenchmarkError,
    render_report,
    run_benchmark,
)

REPORT = Path("artifacts/benchmarks/vector-query-plans.md")
# Building an index over a large corpus takes longer than a request may.
STATEMENT_TIMEOUT_MS = 600_000


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    defaults = BenchmarkParameters()
    for field in dataclasses.fields(BenchmarkParameters):
        default: int | float = getattr(defaults, field.name)
        parser.add_argument(
            f"--{field.name.replace('_', '-')}", type=type(default), default=default
        )
    parser.add_argument("--output", type=Path, default=REPORT)
    arguments = parser.parse_args()
    try:
        parameters = BenchmarkParameters(
            **{
                field.name: getattr(arguments, field.name)
                for field in dataclasses.fields(BenchmarkParameters)
            }
        )
    except InvalidBenchmark as error:
        sys.stderr.write(f"invalid parameters: {error}\n")
        return 2
    runtime = create_database_runtime(
        Settings(database_statement_timeout_ms=STATEMENT_TIMEOUT_MS)
    )
    try:
        report = asyncio.run(_run(runtime, parameters))
    except VectorBenchmarkError as error:
        sys.stderr.write(f"benchmark failed: {error}\n")
        return 1
    rendered = render_report(report)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered, encoding="utf-8")
    summary = rendered.split("## Plan:", 1)[0].rstrip()
    sys.stdout.write(f"{summary}\n\nFull report: {arguments.output}\n")
    return 0


async def _run(
    runtime: DatabaseRuntime, parameters: BenchmarkParameters
) -> BenchmarkReport:
    try:
        return await run_benchmark(runtime, parameters)
    finally:
        await runtime.dispose()


if __name__ == "__main__":
    sys.exit(main())
````

Check: `uv run python scripts/benchmark_vector_search.py --k 0` prints `invalid parameters: k must be an int from 1 to 100` and exits with 2.

### Step 4 — Coding step: tests

**In plain words:** These tests prove the measurements can be trusted. Without a database, they check the settings, that one seed always gives the same corpus, and the recall and timing arithmetic. With a database, they run small benchmarks and check that the index is really used, that filtering after the index loses rows unless the scan iterates, that no private Chunk is ever returned, that nothing is left behind, and that errors and reports carry no text or vectors.

**`tests/test_vector_benchmark.py`** (new, no database):

| Test | Proves |
|---|---|
| `test_the_defaults_and_the_production_length_are_valid` | The defaults and 1536 dimensions are accepted. |
| `test_out_of_range_parameters_are_refused` | Every bad value raises `InvalidBenchmark`, including bools, NaN, and too many seeded numbers. |
| `test_the_same_seed_gives_the_same_corpus`, `test_another_seed_gives_another_corpus` | Reproducibility. |
| `test_the_corpus_has_the_asked_size_and_unit_vectors` | Counts, private share, query count, and length 1. |
| `test_recall_is_the_share_of_the_exact_top_k_found` | Recall examples, including no exact rows. |
| `test_latency_uses_nearest_rank_percentiles` | p50/p95/max on 1–20 ms. |
| `test_the_report_shows_settings_sizes_results_and_plans` | The rendered tables, server settings and baseline line. |

````python
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
````

**`tests/test_vector_benchmark_integration.py`** (new, real PostgreSQL). Its corpus is 1,000 Chunks of 16 dimensions with `ef_search` 10: big enough that PostgreSQL chooses the index, small enough to run in about half a second. Each runtime has a single pooled connection, so a test can read the setting of the very connection the run used.

| Test | Proves |
|---|---|
| `test_a_run_compares_three_plans_and_leaves_nothing_behind` | Plan order, sizes, timing plumbing (a clock that ticks 1 ms per read), index use, and afterwards: every seeded table 0 bytes on disk, no index, prepare setting restored. |
| `test_every_plan_warms_up_then_times_each_query_once` | Counts the search statements: warmup plus timed per plan, one `EXPLAIN` each, and no plan fetches `chunks.text`. |
| `test_filtering_after_the_index_loses_eligible_chunks_unless_it_iterates` | The highest-risk edge: plain HNSW returns short results and lower recall; iterative returns all k. |
| `test_no_plan_returns_a_chunk_the_reader_may_not_see` | `ineligible_results` is 0 at 0%, 50% and 90% private; a plan that skipped the index is exact. |
| `test_the_report_carries_no_chunk_text_and_no_vectors` | No seeded text, no number in brackets; vectors appear as `'[vector]'`. |
| `test_a_database_error_names_only_its_kind_and_leaves_nothing_behind` | A refused insert gives `VectorBenchmarkError` ending in `CheckViolation`, no numbers, no attached cause, and a clean database. |
| `test_cleaning_up_ignores_the_request_time_limit` | With a 1 ms statement limit the run fails, yet the clean-up still empties the tables on disk. |
| `test_a_database_with_content_is_refused_and_left_as_it_was` | The typed denial: `BenchmarkRefused`, and the existing Source is untouched. |

````python
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
````

Check:

```bash
uv run pytest tests/test_vector_benchmark.py -q
make test-integration
```

Expected: 32 passed for the first; the integration suite includes 10 new tests.

### Step 5 — No coding in this step: run the benchmark

Create a dedicated, empty database, migrate it, and run four configurations. The database URL holds the password, so it only ever lives in a shell variable; nothing prints it.

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_benchmark
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_benchmark#')"
uv run alembic upgrade head
uv run python scripts/benchmark_vector_search.py
uv run python scripts/benchmark_vector_search.py --private-share 0.9 --output artifacts/benchmarks/vector-query-plans-private.md
uv run python scripts/benchmark_vector_search.py --hnsw-ef-search 200 --output artifacts/benchmarks/vector-query-plans-ef200.md
uv run python scripts/benchmark_vector_search.py --dimensions 1536 --output artifacts/benchmarks/vector-query-plans-1536.md
unset KNOWLEDGE_SERVICE_DATABASE_URL
```

The first three take about 20 seconds each; the 1536-dimension run takes about two and a half minutes, mostly building the index.

What the reference run gave (your numbers will differ a little, and the HNSW recall moves between runs):

| Configuration | Plan | p50 | p95 | Mean recall | Lowest recall | Short results |
|---|---|---:|---:|---:|---:|---:|
| 384 dims, 50% private | exact | 16.07 ms | 19.37 ms | 1.000 | 1.000 | 0 |
| | hnsw | 1.96 ms | 3.20 ms | 0.972 | 0.000 | 0 |
| | hnsw_iterative | 2.39 ms | 4.90 ms | 0.972 | 0.000 | 0 |
| 384 dims, 90% private | exact | 5.70 ms | 11.91 ms | 1.000 | 1.000 | 0 |
| | hnsw | 2.20 ms | 2.79 ms | 0.382 | 0.100 | 49 |
| | hnsw_iterative | 2.44 ms | 2.96 ms | 0.992 | 0.900 | 0 |
| 384 dims, `ef_search` 200 | exact | 15.65 ms | 17.33 ms | 1.000 | 1.000 | 0 |
| | hnsw | 2.35 ms | 3.53 ms | 1.000 | 1.000 | 0 |
| 1536 dims, 50% private | exact | 189.35 ms | 216.91 ms | 1.000 | 1.000 | 0 |
| | hnsw | 4.27 ms | 6.37 ms | 0.984 | 0.800 | 0 |
| | hnsw_iterative | 3.50 ms | 5.07 ms | 0.984 | 0.800 | 0 |

20,000 Chunks in every run; `ineligible_results` was 0 everywhere. The index took 1.9 s to build at 384 dimensions (39.1 MiB) and 71.7 s at 1536 (156.3 MiB, with `maintenance_work_mem` at 64MB).

How to read it:

- **Exact grows with the data.** 216.91 ms p95 at 1536 dimensions is 28.9% of the 750 ms budget, and it grows roughly in step with the number of Chunks.
- **HNSW is far faster but not always right.** A lowest recall of 0.000 means one query got 10 answers from a neighbouring cluster and none of the true ones; `ef_search` 200 fixed it here.
- **A strict permission filter starves plain HNSW.** With 90% private, the 40 candidates hold about 4 readable rows, so 49 of 50 queries came back short. Iterative scan fixed that.
- **When few Chunks are eligible, PostgreSQL may skip the index.** Exact is faster at 90% private because it starts from the 2,000 readable Chunks.

Open `artifacts/benchmarks/vector-query-plans.md` and read the three `EXPLAIN` blocks: the exact plan reads every eligible row and keeps the nearest with a `Sort` (`top-N heapsort`); the HNSW plans start with `Index Scan using ix_benchmark_chunk_embeddings_hnsw`.

Check: four reports exist under `artifacts/benchmarks/`, every one shows `Ineligible results` 0, and none contains Chunk text.

### Step 6 — No coding in this step: gates, evidence, and progress

Run these in order and keep the outputs:

```bash
uv run pytest tests/test_vector_benchmark.py -q
make test-integration
make format-check
make lint
make typecheck
make check
make docs-check
```

Expected: everything passes. On the reference run, `make check` reported 1059 passed and 3 deselected.

Then complete the evidence file from Step 1:

- **Design:** the new files; the three plans and why the HNSW plans need their own statement; the rollback and vacuum.
- **Verification:** the commands and counts, your Step 5 tables (data size, warmup, parameters, server settings, results), and one `EXPLAIN` excerpt per plan. Do not paste whole reports; they stay in `artifacts/`.
- **Baseline decision:** which plan the service should keep, and when to change it. Base it on your numbers. The reference run supports keeping exact search: it is always right, it filters before ranking, it needs no index per space, and at 20,000 Chunks of 1536 dimensions it uses under a third of the latency budget. Moving to HNSW would need a per-space index built during each model migration, the statement changes above, iterative scan, and a higher `ef_search`; revisit it when exact p95 nears the budget.
- **Risks:** the known limits above.
- **Reflection:** your answers to the questions below.

No diagram or decision record becomes wrong in this lesson: the service's search is unchanged. Finally, add the M04-T11 row to `docs/curriculum/PROGRESS.md`, set the next task to M04-T12, and commit with `m04-t11: benchmark vector query plans`.

## Reflection questions

1. What does `run_benchmark` hide from whoever calls it? List the things a caller never has to think about, such as the dead rows or the prepared statements.
2. Suppose someone later switches the real search to the HNSW index to make it faster. Which promise of today's search would be easiest to break by accident (for example the permission filter, the full k results, or the tie-break by Chunk ID), and how would you notice?
3. Why did plain HNSW return only about 4 of 10 rows when 90% of the Documents were private, even though the permission filter was in the same SQL statement?
4. One query had a recall of 0 with `ef_search` 40. In your own words, what happened in the graph, and why did 200 fix it?
5. What measurement or test case could prove your baseline decision wrong? Think about corpus size, real embeddings instead of random points, or many users searching at once.

## Completion checklist

- [ ] The evidence file has the invariant and predictions, written before the code.
- [ ] `vector_benchmark.py` seeds a reproducible corpus and compares `exact`, `hnsw` and `hnsw_iterative`.
- [ ] Bad parameters raise `InvalidBenchmark`; a database with content raises `BenchmarkRefused`; a database error raises `VectorBenchmarkError` without seeded data.
- [ ] The tests prove: no ineligible Chunk in any plan, short results without iterative scan, nothing left behind, no text or vectors in reports.
- [ ] You ran the four configurations, and the reports show EXPLAIN output, data size, warmup, and parameters.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence file has the design, verification, baseline decision with reasons, risks, and your reflection.
- [ ] The PROGRESS row is added and the commit is made.

When you have written the code, share it or any error output, and I will review it against this checklist.
