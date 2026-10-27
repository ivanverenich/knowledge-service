# M04-T08 — Retrieval metadata filters evidence

## Invariant

A search can be narrowed only by conditions from a fixed list: Source is any
of some Sources, media type is any of some types, language is any of some
languages, or the Source changed the Document on or after a UTC time. Each
condition is turned into a fixed SQL condition with bound parameters and
added to the same WHERE as the permission check, so filters only narrow:
they never show a Chunk the reader may not see, and never change ranks,
counts, or `truncated` through hidden Chunks. Anything outside the list
raises `UnsupportedFilter` before the database is used.

## Expected failing check

Before the change, the new test files fail to import
`knowledge_service.retrieval_filters`, and the migration drift test would
fail once `document_metadata` is declared without its migration.

Predictions once it exists:

- Five Documents across two Sources, two media types, two languages, and
  different modification times: each filter returns exactly the matching
  ones in distance order, and `limit=1` returns the first with `truncated`
  True when more match.
- A Document whose Source gives no modification time: never matches a
  recency filter.
- A hidden Document that matches every filter and is the nearest: not
  returned, and `truncated` stays False.
- `LANGUAGE` with `ON_OR_AFTER`, the string `"like"` as an operator, or two
  `LANGUAGE` conditions: `UnsupportedFilter`.
- A newer reconcile changes only the modification time: `Change.METADATA`;
  an older observation delivered after it: `InvalidDocument`.

## Design

Completed: 2026-10-27

The spec's "content type" is the media type, such as `text/markdown`.

Changed files:

- `src/knowledge_service/documents.py`: `DocumentMetadata` (`media_type`,
  `language`, `modified_at` or None, `observed_at`) and
  `MAX_MEDIA_TYPE_CHARACTERS`.
- New `migrations/versions/f4a5b6c7d8e9_document_metadata.py`: the
  `document_metadata` table, one row per Document, removed with it.
- New `src/knowledge_service/retrieval_filters.py`: `FilterField`,
  `FilterOperator`, `MetadataCondition`, `checked_conditions`, and
  `UnsupportedFilter`.
- `src/knowledge_service/persistence.py`: the table, `metadata_predicate`,
  `upsert_document_metadata`, and `load_document_metadata`.
- `src/knowledge_service/reconciliation.py`: `Change.METADATA` and
  `_write_metadata`, called on every reconcile.
- `src/knowledge_service/vector_search.py`: a `filters` argument, checked
  before the database and passed as `restriction` next to `eligibility`.
- `tests/test_documents.py`, `tests/test_vector_search.py`, new
  `tests/test_retrieval_filters.py` (46 tests) and
  `tests/test_retrieval_filters_integration.py` (22 tests), counting
  parametrized cases.
- `docs/architecture.md`: the new table in the data model and the filters
  in the vector search flow.

The allowlist:

| Field | Operator | Value | SQL |
|---|---|---|---|
| `SOURCE` | `ANY_OF` | 1–100 `SourceId` holding a UUID | `documents.source_id = ANY(:uuid[])` |
| `MEDIA_TYPE` | `ANY_OF` | 1–100 RFC 6838 media types | `EXISTS` on `document_metadata`, `media_type = ANY(:text[])` |
| `LANGUAGE` | `ANY_OF` | 1–100 `Language` | `EXISTS`, `language = ANY(:text[])` |
| `MODIFIED_AT` | `ON_OR_AFTER` | one UTC `datetime`, kept as a plain UTC copy | `EXISTS`, `modified_at >= :since`; NULL never matches |

Any other field, operator, value type, or a second condition on one field
raises `UnsupportedFilter` before the database is used; `checked_conditions`
rebuilds each condition, so one altered after it was made is checked again.
Conditions are added to the same WHERE as the eligibility predicate, so
they only narrow.

Reconciling stores the metadata when it differs from the stored row,
reports `Change.METADATA` when nothing else changed, and raises
`InvalidDocument` when an older observation would change it.

## Verification

Commands, run with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set:

| Command | Result |
|---|---|
| `uv run pytest tests/test_retrieval_filters.py -q` | 46 passed |
| `uv run pytest tests/test_retrieval_filters_integration.py tests/test_vector_search_integration.py tests/test_reconciliation_integration.py tests/test_synchronization_integration.py tests/test_migrations_integration.py -q` | 89 passed |
| `uv run pytest -m "not integration and not live" -q` | 756 passed, 153 deselected |
| `make test-integration` (same `pytest -m "integration and not live"`) | 150 passed, 759 deselected |
| `make format-check lint typecheck` | 131 files formatted; all checks passed; 0 errors |
| `make check` | 906 passed, 3 deselected; coverage 94%, `retrieval_filters.py` and `vector_search.py` fully covered |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `uv run pre-commit run --all-files` | every hook passed |

`tests/test_migrations_integration.py` includes the drift check, so the
migration and the declared table agree.

Deliberate breaks, each undone afterwards:

| Break | Failed |
|---|---|
| `restriction` removed from the `WHERE` | eight rows of `test_each_filter_narrows_the_ranked_result` and more |
| Operator allowlist not checked | none at first: every wrong-operator case also had a wrong value type. Three cases with a valid value and a wrong operator were added, and all three then failed |
| Staleness check removed from `_write_metadata` | `test_a_late_observation_cannot_roll_the_metadata_back` |

Every prediction above held, as each case has its own test.

Adversarial review: an independent review fuzzed the first version against
a plain-Python model of the rules (300 random filter and permission
combinations; order, ranks, and `truncated` matched exactly) and tried
about thirty kinds of tampered input. It found four breaks, each fixed with
a regression test that failed against the old code:

- A late, stale observation rolled metadata back, because a metadata-only
  change did not move `documents.updated_at`. The row now stores
  `observed_at`, and a metadata-only change is `Change.METADATA`.
- A `SourceId` altered to hold text reached PostgreSQL as a `DataError`.
- A NUL in a media type reached PostgreSQL as a `DataError`.
- A time zone object could report UTC while checked and something else
  later, or raise an untyped error.

Finish review: the Standards and Spec reviews found nothing blocking. The
Standards review found docstrings that overstated behaviour; those in
`persistence.py` and `reconciliation.py`, which Claude had written, were
corrected while finishing the lesson. Two in `vector_search.py` (the `search_similar_chunks` "Raises"
list without `UnsupportedFilter`, and `vector_search_statement` still
describing one predicate) are left for the learner; the guide's Step 6
has the corrected text.

Authorization and telemetry: the filters share the `WHERE` with the
eligibility predicate, and
`test_a_filter_never_shows_a_document_the_reader_may_not_see` shows a
hidden nearest Document that matches every filter staying hidden, with
`truncated` False. No new logging; `UnsupportedFilter` messages carry no
caller values; `test_the_reader_and_the_query_are_bound_parameters` shows
media types and languages reach the database only as parameters.

Risks and notes:

- A Document reconciled before this change, or published through
  `publish_document`, has no metadata row and matches only Source filters
  until its next reconcile. That reconcile reports `Change.METADATA`.
- A stale observation older than an unchanged newer one can still roll the
  metadata back, as it can a Document's title; nothing records an unchanged
  observation.
- A late run whose metadata differs now fails with `InvalidDocument`, the
  same as a late Document change.
- The filters have no index; M04-T11 measures query plans.
- Non-blocking suggestions not applied: use `MAX_MEDIA_TYPE_CHARACTERS` and
  `btrim` in the table's check constraints; split
  `test_a_condition_list_must_hold_one_condition_per_field`; type `filters`
  as a tuple or list.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **Why not accept SQL text.** A fragment can do anything SQL can,
   including `OR true`, which would widen results past the permission
   check, and its meaning cannot be checked or tested ahead of time. A
   fixed list of field and operator pairs can only ever narrow, and each
   pair has one tested SQL form.
2. **Filtering in Python after the database.** The database would return
   the 10 nearest Chunks of any language, Python would drop the others, and
   a user asking for a rare language would often get nothing although
   matching Chunks exist further down.
3. **The separate table.** It kept `Document` and its many tests unchanged
   and kept metadata out of the Document's versions. The cost is an
   `EXISTS` lookup on a second table for each metadata condition.
4. **No time means no match.** A user asking for recent documents should
   not get ones of unknown age presented as recent. The opposite choice
   would fill "recent" results with old documents from Sources that report
   no time.
5. **`observed_at`.** It records when the values last changed, so a run
   made before that change and delivered after it is recognised as older
   and refused. A newer run that changed nothing leaves no trace, so a
   later stale run is compared only with the older change and gets through.
