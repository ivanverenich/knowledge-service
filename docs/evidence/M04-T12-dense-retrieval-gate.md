# M04-T12 — Dense-retrieval gate evidence

Completed: 2026-10-28

## Invariant

Search compares a query only with vectors of the same model identity, in
the one current Embedding Space, and only among Chunks the reader may see.
Every filter is part of the same SQL statement and only narrows it: a
filtered result is the unfiltered result with some Chunks removed, in the
same order and at the same distances, and it never reveals a Chunk, or the
existence of a Document, the reader may not see.

## Prediction

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

Wrong vector length:

- The provider answers 1024 numbers for a 1536-dimension model: the
  adapter's response check (`checked_batch`) raises
  `EmbeddingMalformedResponse` before anything is stored.
- The configured dimensions change from 1536 to 1024: the model identity
  changes, so no current Embedding Space holds it, and search raises
  `NoCurrentEmbeddingSpace` before running the query. Publication would
  raise `UnknownEmbeddingSpace` before sending any text.
- Search gets a 1024-number query for the 1536 model: search refuses it
  with `InvalidVectorQuery` before the database is used.
- A 1024-number vector is written into the 1536 space: the database
  refuses the row through its length check on `chunk_embeddings`.

Document filter:

- Naming a Document the reader may not see returns an empty result with
  `truncated` False.
- Naming a Document that does not exist returns exactly the same result,
  so the two cannot be told apart.
- The remaining Chunks keep the order and distances they have without the
  filter, because the filter is only one more condition in the same
  `WHERE`, before ordering and the limit.

## Embedding and index versioning

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **Why dimensions are part of the identity.** A vector is only a list of
   numbers, and distance means something only between two lists made by
   the same model at the same length. `text-embedding-3` can shorten its
   vectors, so one model name gives different, incomparable vectors at 1536
   and at 1024 numbers. `EmbeddingModelIdentity` therefore holds provider,
   model, and dimensions together; `embedding_spaces` is unique on all
   three (`uq_embedding_spaces_identity`), and `checked_batch` refuses any
   provider answer whose length is not the identity's dimensions. If
   dimensions were only a setting beside the identity, a changed setting
   would look like the same model and its vectors would be compared with
   the old ones.

2. **Embedding Space versus index configuration.** An Embedding Space
   (`embedding_spaces`) is where one identity's vectors live, and the
   `is_current` flag (at most one, through the partial unique index
   `uq_embedding_spaces_current`) says which one search reads. Search looks
   only at the space: `load_current_embedding_space_id` matches the query's
   identity against the current space. An `IndexConfiguration` records
   everything that shaped a vector: the model identity, the chunker version
   and token budgets, the normalization version, parser versions, media
   type aliases, the Unicode data version, the distance metric, and the
   vector scaling. Its `fingerprint` is one SHA-256 hash of all of them, so
   two vectors were made the same way exactly when their fingerprints are
   equal. Today a configuration is stored only when a migration starts
   (`target_configuration_id` on `embedding_migrations`); no space or vector
   points at one, so search cannot yet tell vectors of an old chunker from
   a new one in the same space.

3. **Changes that need a new configuration with the same model.** A new
   chunker version or token budget (`CHUNKER_VERSION`, `MAX_TOKENS`,
   `OVERLAP_TOKENS`), a new normalization version or parser library
   version (`NORMALIZATION_VERSION`, `parser_versions`), and a different
   Unicode data version after a Python upgrade. A change of distance metric
   or vector scaling would too. Each changes the text that reaches the
   model, or how its vector is compared, without changing the model.

4. **Why migrate instead of re-embedding in place.** Re-embedding takes
   time and provider calls for every Chunk. Done in place, search would
   meanwhile compare a query against a mix of old and new vectors, or find
   nothing for Chunks whose old vector was deleted; a new length would not
   even fit the old space (`ck_chunk_embeddings_dimensions`). The migration
   (`embedding_migration.py`) fills a new, not-current space while search
   keeps reading the old one, publication writes to both, and one
   transaction switches `is_current` only once the new space has a vector
   for every searchable Chunk. Search moves from all-old to all-new at one
   commit, and the old space is deleted only afterwards.

## Publication and search

Drafted by Claude at the learner's request. What the paper drawing from
memory missed is the learner's to add here.

Both diagrams in `docs/architecture.md` now match the code; every arrow was
traced to `publish_document_embeddings` and `search_similar_chunks`.

What the old publication diagram missed:

- The early exits that send nothing to the provider: `UnknownEmbeddingSpace`
  when no space holds the model's identity, `UNAVAILABLE` for a tombstoned
  or missing Document, and `UNCHANGED` when every Chunk already has a
  vector.
- That the first transaction share-locks the space row
  (`load_embedding_space_id`) and locks the Document row (`_snapshot`).
- That every vector's length is checked against the model's dimensions
  before it can be written (`EmbeddingBatch`, `checked_batch`).
- The M04-T10 rule in the compare-and-swap: the write also needs the same
  space for the model and a space that still accepts writes
  (`space_accepts_writes`); a space retired by a migration gives `STALE`.

What the old search diagram missed:

- The `Document` filter added in this lesson.
- That the `SELECT` reads the space only while it is still current (the
  `EXISTS` from M04-T10).
- The recheck after an empty result: if a migration switched spaces
  between the lookup and the query, search raises `NoCurrentEmbeddingSpace`
  instead of returning a silently empty result.

Found while checking the preview: the vector search diagram (since
M04-T06) and the migration state diagram (since M04-T10) never rendered.
Each held a `;` in a label, which Mermaid reads as the end of a statement.
Both labels now use a comma, and no Mermaid block in the docs holds a
semicolon.

## Dimension mismatch diagnosis

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words. Which predictions were wrong is the learner's to add.

The guide's Step 4 script ran against the dedicated, migrated
`knowledge_service_benchmark` database. It created a current
1536-dimension space inside one transaction and rolled it back; afterwards
the database held no Embedding Space.

```text
1. provider answers 1024 numbers for a 1536-dimension model
   checked_batch                   EmbeddingMalformedResponse: vector 0 has 1024 dimensions, expected 1536
2. the setting changed to 1024; search with the new identity
   search_similar_chunks           NoCurrentEmbeddingSpace: the current embedding space is another model
3. the stored identity, but a 1024-number query
   search_similar_chunks           InvalidVectorQuery: query has 1024 dimensions, expected 1536
4. write a 1024-number vector into the 1536 space
   insert_chunk_embeddings         CheckViolation: constraint ck_chunk_embeddings_dimensions
```

1. **Who refused what, and what each layer prevents.**
   - Case 1, the provider adapter: `_checked_vector`
     (`embeddings.py:298`), called by `checked_batch` on every response.
     Without it, the wrong vector would travel through publication and only
     the database would stop it, as a raw driver error after the provider
     was already paid.
   - Case 2, search: `load_current_embedding_space_id` finds no current
     space with this identity, and `search_similar_chunks` raises
     `NoCurrentEmbeddingSpace` (`vector_search.py:145`). Without it, a model
     with the same length but another name would be compared with foreign
     vectors and return a confident, meaningless ranking with no error.
   - Case 3, search: `_unit_query` raises `InvalidVectorQuery`
     (`vector_search.py:242`) before the database is used. Without it,
     pgvector would fail on the mismatched lengths with a raw `DataError`.
   - Case 4, the database: `ck_chunk_embeddings_dimensions`
     (`vector_dims(embedding) = dimensions`, `persistence.py:281`), with the
     composite foreign key that ties the row's `dimensions` to its space.
     The `embedding` column has no fixed length, because spaces of
     different lengths share the table, so without the check one space
     could hold mixed lengths and every later search of it would fail.
2. **Why "another model" when only the dimensions changed.** The identity
   is provider, model, and dimensions together, so 1024 numbers of
   `text-embedding-3-small` are a different identity from 1536 numbers of
   it, and no current space holds that identity. Publication with the same
   setting raises `UnknownEmbeddingSpace` (`embedding_publication.py:135`)
   before any Chunk text is sent.
3. **The right fix.** Change the setting back to restore search at once:
   nothing stored is wrong, only the running configuration. If 1024
   dimensions are really wanted, start an embedding migration to a new
   1024-dimension space, backfill, and switch. Editing stored vectors is
   wrong: the project has no way to turn a 1536-number vector into a valid
   1024-number one, and changing rows in place would mix lengths, which the
   database refuses.
4. **Wrong predictions.** Pending; for the learner.

## Filter plan

Drafted by Claude at the learner's request, after Claude had built and
reviewed the prototype, so it is not a plan made without a recipe. The
learner should write their own plan, and what it missed, before Step 6.

Files and changes, following `FilterField.SOURCE`:

- `retrieval_filters.py`: a new `FilterField.DOCUMENT` that allows only
  `ANY_OF`, with `DocumentId` as its element type, and `frozenset[DocumentId]`
  added to `FilterValue`. The docstring table gets a row.
- `persistence.py`: one more `case` in `_condition_clause`:
  `documents.document_id = ANY(:document_ids)`, one array parameter of
  UUIDs, built with the existing `_any_of`. No join changes: `documents` is
  already joined in `vector_search_statement`.
- `docs/architecture.md`: the search paragraph lists the new filter.

What the caller passes, and what is refused with `UnsupportedFilter`
before the database is used:

- Accepted: a frozenset of 1 to 100 `DocumentId`s, each holding a UUID.
- Refused: another operator, an empty set, more than 100 values, a list or
  set instead of a frozenset, bare UUIDs or strings, a `SourceId` (also a
  UUID, but another kind of ID), and an ID altered after it was made.

Where the condition sits: in the same `WHERE` as the current space,
eligibility, and the real-distance check, through `metadata_predicate`.
PostgreSQL drops Chunks of other Documents before `ORDER BY distance,
chunk_id` and the `LIMIT`, so the order, the distances, and `truncated`
are those of the unfiltered search with Chunks removed.

Three tests:

- Success: filtering on three of five Documents returns exactly their
  Chunks, in the unfiltered order with the same distances, and `truncated`
  is right for a smaller limit.
- Highest-risk edge: a filter naming a Document the reader may not see,
  which is the exact nearest match, returns an empty result equal to the
  one for an unknown ID; with a visible Document beside it, only the
  visible one is returned.
- Typed failure: an altered `DocumentId` raises `UnsupportedFilter` before
  the search.

What this plan would have missed without the adversarial review: the UUID
inside an ID can be altered too, an ID can be altered after it was checked
while the search waits on the database, and an ID can lose its `value`.
Step 6's `_copied_ids` handles all three.

## Design

Changed files:

- `src/knowledge_service/retrieval_filters.py`: `FilterField.DOCUMENT`,
  allowed only with `ANY_OF` and a frozenset of 1 to 100 `DocumentId`s.
  Both ID fields (`SOURCE`, `DOCUMENT`) now go through `_copied_ids`, which
  checks each ID down to the number inside its UUID (an `int` from 0 to
  2**128 - 1) and stores new IDs rebuilt from those numbers. The old
  Source-only UUID check moved there.
- `src/knowledge_service/persistence.py`: `_condition_clause` maps
  `DOCUMENT` to `documents.document_id = ANY(:document_ids)`, one array
  parameter through the existing `_any_of`.
- `docs/architecture.md`: the publication and search diagrams match the
  code (see above), the filter list names Document, and two Mermaid labels
  lost the `;` that stopped them rendering.
- Tests: `tests/test_retrieval_filters.py`, `tests/test_vector_search.py`,
  `tests/test_retrieval_filters_integration.py`.

The filter reaches PostgreSQL through `search_similar_chunks` →
`checked_conditions` → `metadata_predicate`, as one more condition in the
same `WHERE` as the current space, eligibility, and the real-distance
check. PostgreSQL drops Chunks of other Documents before `ORDER BY
distance, chunk_id` and `LIMIT k + 1`, so a filtered result is the
unfiltered one with Chunks removed, and `truncated` counts only Chunks
that are eligible and match.

Why the IDs are copied: a frozen dataclass, and the UUID inside it, can
still be changed with `object.__setattr__`. The adversarial review showed
three breaks in the first version: a UUID whose number was swapped for
text, `-1`, or `None` reached the database (`DataError`, `OverflowError`,
`TypeError`); an ID changed by another task while the search awaited the
space lookup was sent unchecked; and an ID with its `value` deleted raised
`AttributeError`. `checked_conditions` rebuilds every condition, and each
rebuild makes fresh copies from checked numbers, so the IDs the search
sends are ones no caller passed in. This also hardens the Source filter.

## Verification

Commands, run with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set:

| Command | Result |
|---|---|
| `uv run pytest tests/test_retrieval_filters.py tests/test_vector_search.py -q` | 95 passed |
| `uv run pytest tests/test_retrieval_filters_integration.py -q -m integration` | 26 passed |
| `uv run pytest -m "not integration and not live" -q` | 878 passed, 203 deselected |
| `make test-integration` | 200 passed, 881 deselected |
| `make format-check`, `make lint`, `make typecheck` | 144 files formatted; all checks passed; 0 errors |
| `make check` | 1078 passed, 3 deselected; coverage 95%, `retrieval_filters.py` 100% |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `uv run pre-commit run --all-files` | every hook passed |

What the tests prove, through `search_similar_chunks` on the real
database:

- Success: `test_a_document_filter_keeps_the_ranking_of_the_documents_it_names`
  returns the same Chunks, distances, and ranks as the unfiltered result
  for three of five Documents, and `truncated` is right at limit 2;
  `test_a_document_filter_orders_equal_distances_by_chunk_id` keeps the
  tie-break.
- Highest-risk edge: `test_naming_a_document_the_reader_may_not_see_finds_nothing`.
  The hidden Document is the exact nearest match; naming it gives a result
  equal to naming an unknown ID, and with a visible Document beside it
  only the visible one is returned, with `truncated` False.
- Typed failure: `test_a_document_id_altered_to_hold_text_is_refused_before_the_search`
  raises `UnsupportedFilter`; unit tests refuse a `SourceId`, bare UUIDs
  and strings, an empty set, 101 values, a list, a tampered UUID, and a
  deleted `value`, and show a condition keeps its own copies.
- Storage, not Python: `test_the_reader_and_the_query_are_bound_parameters`
  shows the Document ID absent from the SQL text and present as one array
  value.

Deliberate break, undone afterwards: with the `DOCUMENT` clause replaced
by `sa.true()`, three tests failed:
`test_the_reader_and_the_query_are_bound_parameters`,
`test_a_document_filter_keeps_the_ranking_of_the_documents_it_names`, and
`test_naming_a_document_the_reader_may_not_see_finds_nothing`. Each fix
from the adversarial review has a regression test that failed against the
unfixed filter (six tests).

The adversarial review also held the filter's main promises: 150 seeded
random queries, each naming a mix of visible, unknown, and planted hidden
Documents (private to another user, granted to another group, or
tombstoned, each the exact nearest match), always equalled the unfiltered
result restricted to the named Documents, never showed a planted
Document, and equalled the same query with the hidden IDs swapped for
unknown ones.

Telemetry: neither `vector_search.py` nor `retrieval_filters.py` logs, and
`UnsupportedFilter` messages name only the field, never a value.

Standards and Spec reviews found nothing blocking in code or tests. The
Spec review noted that the parts the task wants in the learner's own words
(predictions, the filter plan made without a recipe, the explanations, the
reflection) are drafted by Claude here. Non-blocking notes, left for
later:

- The publication diagram does not show `EmbeddingInputRejected` (a Chunk
  too long for the model, refused before sending), and its single-branch
  `alt` blocks would read better as `opt`, since those branches end the
  flow.
- No committed test names a tombstoned Document in the Document filter;
  only the review's fuzz did.
- A test comment about a past `DataError` now covers both ID fields,
  though only the Source filter ever had it; one `cast` in `_copied_ids`
  could go with a dedicated ID-type mapping.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **What `MetadataCondition` hides from callers.** A caller names a field
   and a value and never writes SQL. It does not have to know that values
   become one bound array parameter, that each field maps to one fixed
   clause in the same `WHERE` as eligibility, that a Document without a
   metadata row never matches a media type or language, that a time zone
   is replaced by plain UTC, or that IDs are copied so a later change to
   the caller's objects has no effect.
2. **The Document filter under an HNSW index.** The index would hand over
   its `ef_search` nearest candidates first and the filter would run on
   those. With only two Documents named, almost none of 40 candidates
   would belong to them, so the search would return few or no rows while
   exact search finds all of them; M04-T11 showed the same with strict
   permissions. A recall check against exact search, or a test that names
   two Documents far from the query and expects k rows, would notice.
   Iterative scan would be needed.
3. **Why a hidden Document must look like a made-up one.** If naming a
   hidden ID gave a different answer, such as an error or a different
   `truncated`, a reader could test IDs one by one and learn which hidden
   Documents exist, even without seeing their text. Equal answers leave
   nothing to learn.
4. **What could prove this wrong.** A filter with 100 Documents on a large
   corpus, to see whether the planner still filters before ranking or
   picks a slower plan; many readers at once, to see that eligibility and
   the filter stay in one consistent snapshot; and a Document tombstoned
   while a search runs, to check that no Chunk of it is returned after the
   tombstone commits. A committed test that names a tombstoned Document
   would close the gap the review noted.
