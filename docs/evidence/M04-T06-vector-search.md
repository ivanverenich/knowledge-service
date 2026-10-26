# M04-T06 — Authorizable vector search evidence

## Invariant

Vector search returns a Chunk only if its Document is available and at least
one of its Access Grants names everyone, the reader's User, or one of the
reader's Groups. That check runs in the same SQL statement as the ranking,
before ORDER BY and LIMIT, so an ineligible Chunk is never ranked, never
counts toward the limit, and never leaves the database. Only vectors of the
current Embedding Space are compared, and only with a query from its model.

## Expected failing check

Before the module exists, `tests/test_vector_search.py` and
`tests/test_vector_search_integration.py` fail to import
`knowledge_service.vector_search`, so pytest reports collection errors.

Predictions once it exists:

- A forbidden Chunk that matches the query exactly, plus an allowed one
  farther away, with `limit=1`: the allowed one is returned. A filter
  applied after ranking would return nothing.
- A group grant whose UUID equals the reader's user ID: not visible.
- A Document whose readers become empty, or which is tombstoned: gone from
  the next search, although its vector is still stored.
- A query from a model that is not the current space's: `NoCurrentEmbeddingSpace`.
- A query of the wrong length, NaN, all zeros, or a limit of 0 or 101:
  `InvalidVectorQuery`, before the database is touched.

## Design

Changed files:

- `src/knowledge_service/access.py`: `AuthorizationContext` (one `UserId`,
  a `frozenset` of `GroupId`s) and `InvalidAuthorizationContext`.
- `src/knowledge_service/persistence.py`: `eligible_chunks_predicate` and
  `load_current_embedding_space_id`.
- New `src/knowledge_service/vector_search.py`: `search_similar_chunks`,
  `vector_search_statement`, `VectorCandidate`, `MAX_CANDIDATES`,
  `VectorSearchError`, `InvalidVectorQuery`, and `NoCurrentEmbeddingSpace`.
- New `tests/test_vector_search.py`: 23 tests, counting parametrized cases.
- New `tests/test_vector_search_integration.py`: 23 tests, counting
  parametrized cases.
- `docs/architecture.md`: a "Vector search as built in M04" flow.

How it works:

| Part | Behaviour |
|---|---|
| Check | `_unit_query` refuses a limit outside 1–100, a query that is not a tuple of finite numbers of the model's length, or all zeros, with `InvalidVectorQuery`, before the database is used. It then scales the query to length 1, which keeps cosine distance and keeps every value inside pgvector's 32-bit floats. |
| Space | `load_current_embedding_space_id` returns the space only if it is current and holds the query's model identity; otherwise `NoCurrentEmbeddingSpace`. |
| Eligibility | `eligible_chunks_predicate` requires an available Document and an `EXISTS` over Access Grants: public, or kind `user` with the reader's ID, or kind `group` with an ID in the reader's Groups. Groups are one `uuid[]` parameter compared with `= ANY`. |
| Rank | `vector_search_statement` is one `SELECT` over `chunk_embeddings` → `chunks` → `documents`. The space and the eligibility predicate share its `WHERE`, so PostgreSQL filters before `ORDER BY distance, chunk_id` and `LIMIT`. |
| Result | `VectorCandidate` carries the Chunk, Document and Source IDs, content version, ordinal, cosine distance, external ID, title, heading path, source anchor and text. The text fields are excluded from `repr`. |

The search takes an `AsyncConnection`; the caller owns the transaction
(ADR 0012).

## Verification

Commands, run with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set:

| Command | Result |
|---|---|
| `uv run pytest tests/test_vector_search.py tests/test_vector_search_integration.py -q` | 46 passed |
| `make format-check lint typecheck` | 127 files formatted; all checks passed; 0 errors |
| `uv run pytest -m "not integration and not live" -q` | 683 passed, 111 deselected |
| `make test-integration` | 108 passed, 686 deselected |
| `make check` | 791 passed, 3 deselected; coverage 94%, `vector_search.py` fully covered |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `uv run pre-commit run --all-files` | every hook passed |

The first runs failed for two reasons, both fixed at the learner's request:
the field was named `groups_ids` while the tests use `group_ids`, so the
integration file could not even be collected; and
`load_current_embedding_space_id` selected the comparisons instead of
`space_id`, so the search sent `space_id = true` and PostgreSQL raised
`operator does not exist: uuid = boolean`.

Deliberate breaks, each undone afterwards:

| Break | Failed |
|---|---|
| Eligibility removed from the `WHERE` | 9 tests, including `test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked` and `test_the_reader_and_the_query_are_bound_parameters` |
| `subject_kind` not checked for user and group grants | the two cross-kind cases of `test_only_a_matching_access_grant_makes_a_chunk_eligible` |
| Availability not checked | `test_revoked_and_tombstoned_documents_disappear_at_once` |
| Query sent without scaling | all six cases of `test_distance_depends_on_direction_not_length` |
| `is_current` not checked | `test_a_query_from_a_model_that_is_not_current_is_refused` |

Every prediction above held, as each case has its own test.

Adversarial review: an independent review attacked an earlier version on
the real database. It found three breaks, all in query checking: a value
such as `1e39` overflowed pgvector's 32-bit floats and raised a raw
`DataError` whose message held the bound user and group IDs; tiny or huge
finite queries returned NaN or wrong distances; and `10**400`, strings,
`None` or an iterator raised `OverflowError` or `TypeError`. Scaling to
length 1 and type checks fixed them, and the regression tests failed
against the old code. A fuzz test against a plain-Python model of the
rules (random grants, contexts, tombstones, revocations, ties and limits)
found no authorization break.

Authorization and telemetry: eligibility is decided inside the SQL
statement, so an ineligible Chunk's text never leaves the database. The
module adds no logging; no Document text appears in a `repr`
(`test_a_candidate_repr_shows_no_document_text`) or in an error message,
and no ID or vector value appears as a literal in the SQL.

Risks:

- A stored vector of all zeros gives a NaN distance, and one with values
  near 3e38 gives a wrong distance. `_checked_vector` already refuses
  all-zero provider vectors, and real embedding values are small.
- With an approximate index (HNSW, M04-T11), PostgreSQL may take the
  nearest rows from the index before filtering, and return fewer than
  `limit` eligible Chunks to a reader who may see few Documents.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **What the module hides from callers.** A caller passes a vector, the
   model identity, the reader and a limit. Grants, tombstones, which space
   is current, 32-bit float limits and tie order are all handled inside.
   If each caller handled them, one forgotten check would leak a Document.
2. **The invariant easiest to break while optimizing.** Filtering before
   ranking. Fetching the nearest 100 rows and filtering in Python looks
   faster, but `test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`
   would fail: the forbidden text would leave the database, and an empty
   or short result would hint that something hidden matched.
3. **Why searching right after a revocation is safe.** Eligibility is read
   from `access_grants` and `documents` at query time, not stored with the
   vector, so a revoked or tombstoned Document drops out of the next search
   even while its vectors are still stored.
4. **What could prove this wrong.** Recall per reader once an approximate
   index exists: compare the exact and index results for a reader who may
   see only a few Documents, and count how often fewer than `limit` come
   back (M04-T11).
5. **Why `subject_kind` is checked with `subject_id`.** User and Group IDs
   are both UUIDs. Without the kind, a group grant that happens to carry a
   user's UUID would show that Document to the user; the cross-kind tests
   prove it does not.
