# M04-T07 — Top-k and distance semantics evidence

## Invariant

A vector search returns at most k Candidates, nearest first, where k is an
int from 1 to 100. Distance is cosine, between 0 and 2, lower is nearer,
and equal distances are ordered by Chunk ID, so the same data and query
always give the same result. `truncated` is True only when more eligible
Chunks exist beyond k; hidden Chunks never change it. No eligible Chunk is
an empty result, not an error.

## Expected failing check

Before the change, the updated test files fail to import `DistanceMetric`,
`InvalidCandidateLimit` and `VectorSearchResult`, and `tests/test_embeddings.py`
fails to import `unit_length`, so pytest reports collection errors.

Predictions once it exists:

- Five Documents at distances 0, 0, 0.29, 1 and 2: k=1 returns the first
  tie with `truncated` True; k=5 returns all five with `truncated` False;
  k=100 returns the same five.
- The two Chunks at distance 0 come back in Chunk ID order, whatever order
  the Documents were stored in.
- k of 0, 101, `True`, 1.0 or `"5"`: `InvalidCandidateLimit`, before the
  database is touched.
- One visible Chunk and three hidden ones nearer, k=1: the visible one,
  with `truncated` False.
- A stored vector of `[3e38, 3e38, 0]`: its true distance, not missing.

## Design

Completed: 2026-10-26

Changed files:

- `src/knowledge_service/embeddings.py`: `unit_length`, which refuses
  non-finite or all-zero values with `InvalidEmbedding` and returns the
  vector scaled to length 1.
- `src/knowledge_service/persistence.py`: `insert_chunk_embeddings` stores
  `unit_length(vector)`.
- `src/knowledge_service/vector_search.py`: the contract in the module
  docstring, `DistanceMetric`, `InvalidCandidateLimit`,
  `VectorCandidate.rank`, `VectorSearchResult`, `_check_limit`, a `limit + 1`
  probe for `truncated`, and a `distance != NaN` condition; `_unit_query`
  now calls `unit_length`.
- `tests/test_embeddings.py`: a Hypothesis property test and a table for
  `unit_length`.
- `tests/test_vector_search.py`: 34 tests, counting parametrized cases.
- `tests/test_vector_search_integration.py`: 43 tests, counting
  parametrized cases; the M04-T06 tests are unchanged apart from the
  `search` helper.
- `docs/architecture.md`: the "Vector search as built in M04" section.

The contract:

| Question | Answer |
|---|---|
| How many? | `limit` is top-k, an int from 1 to `MAX_CANDIDATES` (100); anything else raises `InvalidCandidateLimit`, a subclass of `InvalidVectorQuery`, before the database is used. |
| Which way is near? | `metric` is `DistanceMetric.COSINE`; `distance` is from 0 (same direction) to 2 (opposite), lower is nearer. |
| Equal distances? | Ordered by Chunk ID; `rank` counts from 1. Ties are exact: parallel vectors may differ in the last digits after 32-bit rounding and are then ordered by those digits. |
| Fewer than k? | All eligible Chunks are returned. `truncated` is True only when a `limit + 1`-th eligible row exists, so hidden Chunks never change it. |
| None at all? | An empty result, not an error. A stored vector with no direction (NaN distance) is never a Candidate. |

Every stored vector and every query has length 1, so pgvector's 32-bit
arithmetic gives true distances.

## Verification

Commands, run with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set:

| Command | Result |
|---|---|
| `uv run pytest tests/test_vector_search.py tests/test_embeddings.py -q` | 85 passed |
| `uv run pytest tests/test_vector_search_integration.py tests/test_embedding_publication_integration.py -q` | 59 passed |
| `uv run pytest -m "not integration and not live" -q` | 702 passed, 131 deselected |
| `make test-integration` (same `pytest -m "integration and not live"`) | 128 passed, 705 deselected |
| `make format-check lint typecheck` | 127 files formatted; all checks passed; 0 errors |
| `make check` | 830 passed, 3 deselected; coverage 94%, `vector_search.py` and `embeddings.py` fully covered |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `uv run pre-commit run --all-files` | every hook passed |

Deliberate breaks, each undone afterwards:

| Break | Failed |
|---|---|
| Ask the database for `limit` rows, not `limit + 1` | the four truncated rows of `test_top_k_returns_the_nearest_k_in_order`, `test_a_tiny_sideways_vector_does_not_outrank_a_near_one` |
| Store vectors without `unit_length` | all five cases of `test_a_stored_vector_of_any_size_gets_its_true_distance`, `test_a_tiny_sideways_vector_does_not_outrank_a_near_one` |
| No `distance != NaN` condition | `test_a_chunk_whose_vector_has_no_direction_is_never_a_candidate` |
| `ORDER BY distance` without Chunk ID | `test_top_k_returns_the_nearest_k_in_order[1-1-True]` only; the tie order then depends on scan order, so this break is caught by one case, not reliably by all |
| Limit type not checked | five cases of `test_an_invalid_k_is_refused_before_the_database` and the `True` case of the bad-query table |

Every prediction above held, as each case has its own test.

Adversarial review: an independent review fuzzed the first version against
a plain-Python model of the rules. It found that stored vectors were not
scaled: `[3e38, 3e38, 0]` vanished (NaN), `[2e19, 2e19, 0]` got distance 1
instead of 0, and a tiny sideways `[1e-30, 1e-25, 0]` took rank 1 ahead of
a near Chunk. Storing vectors through `unit_length` fixed it, and the
regression tests failed against the unfixed code. A self-check then found
that `unit_length` let `[1.0, nan]` through, because `max` skips past NaN;
it now checks every value, with a test that failed before. The review
confirmed that hidden Chunks never changed rank, count or `truncated`, and
that distances stayed within 0 to 2.

Finish review: a Standards review and a Spec review found no blocking
issue.

Authorization and telemetry: the eligibility predicate still shares the
`WHERE` with the ranking, and the `limit + 1` probe row passes the same
filter, so `truncated` reveals nothing about hidden Documents
(`test_truncated_counts_only_chunks_the_reader_may_see`). The module adds no
logging; `repr` of a Candidate or a result shows no Document text
(`test_a_candidate_repr_shows_no_document_text`), and error messages carry
no input values.

Risks and notes:

- Vectors stored before this change keep their old length. Real OpenAI
  vectors already have length 1, and the test databases start empty, so
  nothing was migrated.
- Ties are exact, not rounded, so two parallel vectors do not always tie.
- With an approximate index (M04-T11), fewer-than-k and `truncated` may no
  longer mean "no more eligible Chunks exist", and the NaN condition must
  be rechecked in the new query plans.
- `insert_chunk_embeddings` can now raise `InvalidEmbedding`. Publication
  cannot reach it, because `_checked_vector` already refuses non-finite and
  all-zero provider vectors.
- Non-blocking suggestions from the Standards review, not applied: say
  "1 is a right angle" instead of "1 is unrelated"; assert the
  `InvalidCandidateLimit` subclass link in a test; use `pytest.approx` for
  the 0 and 2 ends; and comment why `_unit_query` keeps its own zero check.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **What the result object prevents.** With a plain list, a caller could
   sort the wrong way, read a short list as "nothing else matched" when it
   was only the limit, or pass a bad k and get a database error. The result
   names its metric, its limit and whether more exist, and a bad k fails
   with its own error before any query.
2. **Why `limit + 1` instead of counting.** Counting every matching row
   reads all of them, which grows with the corpus. One extra row costs
   almost nothing, and because it passes the same permission filter, it
   says only "more that you may see exist", never how many hidden ones.
3. **Rounding distances to make ties.** Near-equal distances would then
   order by Chunk ID, which is more stable to read. But a truly nearer
   Chunk could fall behind a farther one with a smaller ID, so ranking would
   get slightly worse.
4. **A model that returns vectors not of length 1.** They are scaled on the
   way in. That is safe for cosine distance, which only looks at direction,
   but straight-line or inner-product distance also depends on length, so
   scaling would change their results.
5. **Which tests catch what.** Going back to `limit` rows fails the
   truncated rows of `test_top_k_returns_the_nearest_k_in_order`. Hidden
   Documents showing through `truncated` fail
   `test_truncated_counts_only_chunks_the_reader_may_see`.
