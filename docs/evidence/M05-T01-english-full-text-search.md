# M05-T01 — English full-text search evidence

Completed: 2026-10-28

## Invariant

Every Chunk's `search_en` holds the English search words of its Document's
current title (weight A), its headings (B), and its text (C), built with the
named `english` configuration, whatever wrote the row. English search returns
only Chunks the reader may see, best match first, with ties broken by Chunk
ID, and no typed text raises anything but `InvalidLexicalQuery`.

## Expected failing check

Before the change, `tests/test_lexical_search.py` and
`tests/test_lexical_search_integration.py` fail to import
`knowledge_service.retrieval.lexical_search`.

## Predictions

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

- "requesting leaves" is cut to the stems `request` and `leav`, so it
  matches a Chunk that says "requests for leave". "the of and" holds only
  stop words, so the query is empty and matches nothing, without an error.
- The title match comes first, then the heading match, then the text
  match, because their weights are A, B and C.
- `"paid leave"` with quotes matches only where "paid" is directly followed
  by "leave". Without quotes, both words may sit anywhere in the Chunk, so
  "leave is paid" also matches.
- Nothing harmful happens. The text is sent as a bound parameter, so the
  database reads it as search words (`drop`, `tabl`, `chunk`), not as SQL.
  The table stays, and the result is the Chunks that contain those words.
- The reader gets only the visible Document's Chunk. The permission check
  sits in the same query, so the hidden Chunk is never ranked or returned.

## Design

Changed files:

- `migrations/versions/c7d8e9f0a1b2_english_full_text_search.py`: the SQL
  function `english_search_vector(title, heading_path, body)`, which builds
  `setweight(to_tsvector('english'::regconfig, ...))` with weights A, B
  and C; the column `chunks.search_en` (added nullable, backfilled from
  `documents.title`, then NOT NULL); the GIN index `ix_chunks_search_en`
  with `fastupdate = off`; and two triggers. The downgrade removes all of
  it in reverse order.
- `src/knowledge_service/persistence/schema.py`: declares the column and
  the index; `tests/test_indexes.py` lists `ix_chunks_search_en`.
- `src/knowledge_service/retrieval/lexical_search.py`:
  `search_english_chunks`, `english_search_statement`, `LexicalMatch`,
  `LexicalSearchError`, and `InvalidLexicalQuery`.
- `docs/architecture.md`: the `retrieval` row and the `chunks` rules row.
- Tests: `tests/test_lexical_search.py`,
  `tests/test_lexical_search_integration.py`.

Why triggers: a Chunk row holds no title, and a generated column cannot
read another table. The Chunk trigger runs `BEFORE INSERT OR UPDATE` on
every write, reads the title with `FOR SHARE` so it waits for a rename in
progress, and rebuilds `search_en`; so no writer, and no direct write to
the column, can leave it wrong. The Document trigger runs `AFTER UPDATE OF
title` only when the title really changed, and rebuilds that Document's
Chunks, because a rename rewrites no Chunk.

Why `fastupdate = off`: with the GIN pending list, new entries waited
outside the main index, and in the full test suite the planner chose a
sequential scan. Writing each entry straight into the index costs a little
on writes and keeps search on the index.

The search reaches PostgreSQL as one statement:
`search_en @@ websearch_to_tsquery('english', :query_text)` and
`eligible_chunks_predicate` in the same `WHERE`, `ORDER BY ts_rank DESC,
chunk_id`, `LIMIT`. The text is one bound parameter; the configuration is
named, never the server default. Text and limit are checked before the
database is used. The query runs in a savepoint; the one PostgreSQL error
typed text can still cause ("tsquery stack too small", for example 33
dashes) becomes `InvalidLexicalQuery` with the original message dropped,
because it repeats the query text.

The adversarial review of the prototype found three breaks, each fixed
with a regression test that failed against the unfixed code: a line of
dashes raised a raw database error that repeated the query; a Chunk
written during a title change kept the old title (now `FOR SHARE`); and a
direct write to `search_en` was never corrected (the trigger now fires on
every update).

## Verification

Commands, run with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set:

| Command | Result |
|---|---|
| `uv run pytest tests/test_lexical_search.py -q` | 15 passed |
| `uv run pytest tests/test_lexical_search_integration.py -q -m integration` | 22 passed |
| `make test-integration` | 222 passed, 896 deselected |
| `make format-check`, `make lint`, `make typecheck` | 164 files formatted; all checks passed; 0 errors |
| `make check` | 1115 passed, 3 deselected; coverage 95%, `lexical_search.py` 97% |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `uv run pre-commit run --all-files` | every hook passed |

A first run of `make test-integration` and `make check` failed with
unrelated errors because a review ran integration tests against the same
test database at the same time; run alone, both passed as above.

What the tests prove, through `search_english_chunks` on the real
database:

- Success: `test_english_stemming_matches_other_forms_of_a_word`,
  `test_a_title_match_outranks_a_heading_match_and_a_text_match`, and
  `test_quoted_words_must_appear_together_and_in_order`.
- Highest-risk edge: `test_a_better_match_the_reader_may_not_see_is_never_returned`,
  also with `limit=1`.
- Typed failure: `test_bad_text_or_limit_is_refused_before_the_database`
  (13 cases, against a connection that cannot be used) and
  `test_text_postgresql_cannot_parse_is_refused_and_the_transaction_survives`.
- Safe syntax: `test_any_typed_text_is_read_as_words_never_as_syntax`.
- Storage: the title-change, concurrent-rename, direct-write, tie-order,
  GIN-index (`EXPLAIN`), and migration-backfill tests.

Step 6 output from the test database:

```text
'employe':4C 'everi':8C 'guid':2A 'leav':7C 'onboard':3B 'paid':6C 'request':5C 'staff':1A 'year':9C
'request' & 'paid' <-> 'leav' & !'sick' | 'holiday'
'drop' & 'tabl' & 'chunk'
```

`websearch_to_tsquery('english', 'the of and')` returned an empty query
with the notice "text-search query contains only stop words or doesn't
contain lexemes, ignored".

Predictions compared: all five held. One detail was not predicted: in
`'request' & 'paid' <-> 'leav' & !'sick' | 'holiday'` the `&` binds
tighter than `|`, so `or holiday` is an alternative to the whole rest of
the query, not only to the last word.

Deliberate break, undone afterwards: with `eligibility` removed from the
`.where(...)` in `english_search_statement`, one test failed,
`test_a_better_match_the_reader_may_not_see_is_never_returned`; 21 passed.
After restoring, the file matched the verified version byte for byte.

Security and privacy: the query text is never part of the SQL text, the
permission check is in the same statement before ranking and `LIMIT`, and
the one handled database error drops its message. Other database errors
are re-raised unchanged and may carry the bound parameter in their
message; this is the same as vector search and is recorded below.

Known gaps from the Standards and Spec reviews (non-blocking):

- The handled error is recognised by its English message text.
- No test runs a 1000-character query end to end, uses identifier-like
  tokens such as `E-1042`, or excludes a tombstoned Document on this path.
- `LexicalMatch` and the bare tuple result differ from vector search's
  `VectorCandidate` and `VectorSearchResult`; fusion will need them aligned.
- Ukrainian Chunks also get an English vector; M05-T02 handles Ukrainian.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **What `search_english_chunks` hides.** A caller passes text, a reader,
   and a limit. It never deals with stemming, stop words, weights, the
   search-box syntax, the triggers that keep `search_en` current, the GIN
   index, the permission check, the tie-break, or the savepoint that keeps
   its transaction usable when PostgreSQL refuses a query.
2. **Moving the words into Python.** Every writer would have to remember
   to rebuild them: a title rename, a migration, a manual fix, or a new
   ingestion path could leave stale words, and search would quietly miss
   or wrongly find Chunks. Python would also need to copy PostgreSQL's
   stemming exactly, or stored words and queries would stop matching.
3. **Why the index must be used.** Without it, every search reads every
   Chunk, so time grows with the corpus and the 750 ms budget is lost at
   scale while the answers stay correct, which hides the problem.
   `EXPLAIN` on the real statement, as in
   `test_the_search_reads_the_gin_index`, and p95 latency on a large
   corpus show when it stops being used.
4. **Ukrainian through English.** The English stemmer does not know
   Ukrainian endings, so different forms of one Ukrainian word stay
   different and do not match each other; only the exact same form
   matches.
5. **Proving the ranking wrong.** Store a long Chunk that repeats a word
   many times in its text and a short Document with the word once in its
   title, and check which comes first. `ts_rank` counts frequency too, so
   the long Chunk may win; a labelled set of questions with the expected
   best Chunk would measure whether the order is right.
