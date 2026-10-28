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

Completed: 2026-10-28

Changed files:

- New `src/knowledge_service/vector_benchmark.py`: `BenchmarkParameters`
  (checked bounds, at most 1,000,000 Chunks and 32 million seeded numbers),
  `seeded_corpus`, `SearchPlan`, `latency_summary` (nearest-rank), `recall`,
  `PlanResult`, `BenchmarkReport`, `run_benchmark`, `render_report`,
  `approximate_search_statement`, and the errors `VectorBenchmarkError`,
  `InvalidBenchmark`, and `BenchmarkRefused`.
- New `scripts/benchmark_vector_search.py`: one option per parameter and
  `--output`; exit 0 on success, 1 on `VectorBenchmarkError`, 2 on invalid
  parameters.
- New `tests/test_vector_benchmark.py` (32 tests) and
  `tests/test_vector_benchmark_integration.py` (10 tests).

Three plans run on the same queries:

| Plan | Statement | Index |
|---|---|---|
| `exact` | today's `vector_search_statement`, selecting Chunk IDs only | none |
| `hnsw` | `approximate_search_statement`, `hnsw.ef_search` set | partial HNSW index on `embedding::vector(dims)` for the seeded space |
| `hnsw_iterative` | the same, with `hnsw.iterative_scan = strict_order` | the same |

The HNSW plans need their own statement: the index needs a fixed length
(the cast), the planner matches the partial index only when the space ID is
written into the SQL (`literal_execute`), and a second sort key would force
a full sort, so it orders by distance alone. The permission filter stays in
every statement.

`run_benchmark` refuses a database that holds a Source or an Embedding
Space, vacuums the seeded tables, seeds, indexes, and searches in one
transaction that is always rolled back, then vacuums again. psycopg's
prepared statements are off during the run so every query gets the plan its
`EXPLAIN` shows, and the setting is restored afterwards. A database error
becomes `VectorBenchmarkError` naming only its kind. The service's search
is unchanged, so no diagram or decision record became wrong.

## Verification

Commands, run with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set:

| Command | Result |
|---|---|
| `uv run pytest tests/test_vector_benchmark.py -q` | 32 passed |
| `uv run pytest tests/test_vector_benchmark_integration.py -q -m integration` | 10 passed |
| `uv run pytest -m "not integration and not live" -q` | 863 passed, 199 deselected |
| `make test-integration` | 196 passed, 866 deselected |
| `make format-check`, `make lint`, `make typecheck` | 144 files formatted; all checks passed; 0 errors |
| `make check` | 1059 passed, 3 deselected; coverage 95%, `vector_benchmark.py` 100% |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `uv run pre-commit run --all-files` | every hook passed |

Benchmark runs, on a dedicated, migrated `knowledge_service_benchmark`
database (PostgreSQL 17.11, pgvector 0.8.7, seed 7, 20,000 Chunks in 2,000
Documents, 50 clusters, 50 timed queries after 5 warmup queries per plan,
k 10, m 16, `ef_construction` 64). Server settings: `maintenance_work_mem`
64MB, `max_parallel_maintenance_workers` 2, `work_mem` 4MB,
`shared_buffers` 128MB, `hnsw.max_scan_tuples` 20000.

| Configuration | Eligible | Table | Index | Build |
|---|---:|---:|---:|---:|
| 384 dims, 50% private, `ef_search` 40 | 10,000 | 32.6 MiB | 39.1 MiB | 1.98 s |
| 384 dims, 90% private, `ef_search` 40 | 2,000 | 32.7 MiB | 39.1 MiB | 1.95 s |
| 384 dims, 50% private, `ef_search` 200 | 10,000 | 32.7 MiB | 39.1 MiB | 2.12 s |
| 1536 dims, 50% private, `ef_search` 40 | 10,000 | 161.3 MiB | 156.3 MiB | 75.75 s |

| Configuration | Plan | p50 | p95 | max | Mean recall | Lowest recall | Short | Ineligible |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 384, 50% | exact | 14.95 ms | 18.05 ms | 20.95 ms | 1.000 | 1.000 | 0 | 0 |
| | hnsw | 2.04 ms | 2.50 ms | 2.62 ms | 0.970 | 0.000 | 0 | 0 |
| | hnsw_iterative | 2.00 ms | 2.33 ms | 2.39 ms | 0.970 | 0.000 | 0 | 0 |
| 384, 90% | exact | 4.53 ms | 5.92 ms | 6.82 ms | 1.000 | 1.000 | 0 | 0 |
| | hnsw | 2.13 ms | 3.08 ms | 4.85 ms | 0.380 | 0.100 | 49 | 0 |
| | hnsw_iterative | 2.55 ms | 3.23 ms | 3.87 ms | 0.992 | 0.900 | 0 | 0 |
| 384, `ef_search` 200 | exact | 15.55 ms | 17.94 ms | 20.18 ms | 1.000 | 1.000 | 0 | 0 |
| | hnsw | 2.34 ms | 4.56 ms | 6.07 ms | 1.000 | 1.000 | 0 | 0 |
| | hnsw_iterative | 2.23 ms | 2.86 ms | 3.93 ms | 1.000 | 1.000 | 0 | 0 |
| 1536, 50% | exact | 190.79 ms | 198.75 ms | 241.44 ms | 1.000 | 1.000 | 0 | 0 |
| | hnsw | 4.80 ms | 8.81 ms | 10.86 ms | 0.962 | 0.000 | 0 | 0 |
| | hnsw_iterative | 3.56 ms | 5.07 ms | 6.02 ms | 0.962 | 0.000 | 0 | 0 |

`EXPLAIN (ANALYZE, BUFFERS)` excerpts of the first timed query:

- `exact` (384, 50%): `Sort Method: top-N heapsort` over `Seq Scan on
  chunk_embeddings (... rows=20000 ...)`; every eligible row is ranked.
- `hnsw` (384, 90%): `Index Scan using ix_benchmark_chunk_embeddings_hnsw
  on chunk_embeddings (... rows=40 ...)`: the index hands over its 40
  candidates, and the permission filter leaves too few.
- `hnsw_iterative` (384, 90%): the same index scan with `rows=133`: the
  index is read on until 10 rows pass the filter.

The full reports stay in `artifacts/benchmarks/`; none contains Chunk text,
and vectors in plans show as `'[vector]'`. After the runs the benchmark
database held no Chunks, no Sources, and no benchmark index.

Predictions against the results:

- HNSW several times faster with mean recall a little below 1: held (about
  7 times at 384 dimensions, 22 times at 1536; mean recall 0.970 and
  0.962). Not predicted: one query had recall 0, with 10 eligible rows none
  of which were in the exact top 10; `ef_search` 200 fixed it.
- 90% private: held. Plain HNSW returned short results for 49 of 50
  queries; iterative scan returned all k with recall 0.992.
- 1536 dimensions under the budget: held, p95 198.75 ms (26.5%).
- Index build seconds versus much longer: held, 1.98 s versus 75.75 s.
- `ineligible_results` 0: held in every run.

Standards and Spec reviews found nothing blocking. Non-blocking notes, left
for later:

- `sa.text` with f-strings (ANALYZE, VACUUM, the partial index, `_size`)
  holds only constants, checked ints, and a UUID today; `_size` takes a
  free string, so a bound `regclass` parameter would be safer.
- If the rollback itself raises, the prepare setting is not restored; only
  `DBAPIError` is suppressed from the clean-up vacuum and converted to
  `VectorBenchmarkError`, so a pool timeout or `OSError` leaves the script
  as a traceback.
- The script picks the summary by splitting on `## Plan:`, writes relative
  to the current directory, lies outside pyright's `include`, and has no
  test of its own.
- `approximate_search_statement` lives in the benchmark module; it belongs
  next to `vector_search_statement` if the service adopts HNSW.
- Approximate plans order by distance only while exact breaks ties by
  Chunk ID, which could understate recall on exact ties (negligible with
  random vectors); the exact plan runs before the index exists, so cache
  state differs between plans despite each plan's warmup.

## Baseline decision

Keep exact search. It is always right (recall 1 by definition), it filters
by permission before ranking so it never comes back short, and it needs no
index per Embedding Space. At 20,000 Chunks its p95 was 18.05 ms at 384
dimensions and 198.75 ms at 1536, at most 26.5% of the 750 ms retrieval
budget.

HNSW was 7 to 22 times faster here, but it would cost a per-space index
built during each model migration (76 s at 1536 dimensions for 20,000
Chunks), a separate statement without the Chunk ID tie-break, iterative
scan so strict permission filters do not starve it, and a higher
`ef_search` to avoid zero-recall queries. Revisit the decision when exact
p95 nears the budget; exact cost grows roughly with the number of eligible
Chunks, so at 1536 dimensions that is several times today's size.

## Risks

- HNSW builds its graph with random choices, so approximate recall moves a
  little between runs even with the same seed.
- The corpus is random points around 50 centres, not real text embeddings.
- One connection, one query at a time: no concurrent load. Latency includes
  one round trip to the database.
- Rows are measured before they are committed.
- The emptiness check cannot see another session's uncommitted writes, and
  the index build blocks writes to `chunk_embeddings` until the rollback:
  use a dedicated database.
- `uses_index` reads the plan of the first timed query only.
- The VACUUM before a run has no test of its own; the one after a run does.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **What `run_benchmark` hides.** The caller never thinks about the dead
   rows a rollback leaves (vacuumed before and after), psycopg's prepared
   statements (off during the run, restored after), the statement time
   limit during VACUUM, the HNSW index (built and rolled back), the
   transaction, the refusal of a database with content, or the driver's
   error message that would repeat seeded rows.
2. **The promise easiest to break by switching to HNSW.** Returning the
   full k results: the permission filter still holds, but with strict
   grants plain HNSW returns fewer rows without any error, as the 90% run
   showed. The tie-break by Chunk ID would also go, since the index orders
   by distance alone. A test that searches as a reader who sees few Chunks
   and checks for k results, plus a recall check against exact search,
   would notice.
3. **Why 4 of 10 rows at 90% private.** The index hands over its 40
   nearest candidates first and the permission filter runs on those; about
   one in ten is readable, so about 4 remain. The filter being in the same
   SQL does not make the index aware of it.
4. **The zero-recall query.** The graph walk reached a neighbouring
   cluster and collected 40 candidates there; the true nearest Chunks were
   never reached. With 200 candidates the walk explores wider and finds the
   right cluster.
5. **What could prove the decision wrong.** A larger corpus (exact grows
   with the eligible Chunks), real embeddings that cluster differently,
   many users searching at once, or a slower database server; a benchmark
   at production size under concurrent load would show exact p95 nearing
   the budget.
