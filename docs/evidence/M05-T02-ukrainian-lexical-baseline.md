# M05-T02 — Ukrainian lexical baseline evidence

Completed: 2026-10-29

## Invariant

Every Chunk's `search_simple` holds the words of its Document's current
title (weight A), its headings (B), and its text (C), NFC-normalized and
lowercased by the named `simple` configuration, with no stemming and no
stop words, whatever wrote the row. Ukrainian search returns only Chunks
the reader may see, best match first, with ties broken by Chunk ID, and no
typed text raises anything but `InvalidLexicalQuery`.

## Expected failing check

Before the change, `tests/test_ukrainian_lexical_search_integration.py`
fails to import `search_ukrainian_chunks` from
`knowledge_service.retrieval.lexical_search`.

## Predictions

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

- "відпустку" and "ВІДПУСТКУ" find the Document, because `simple` only
  lowercases words and the stored word is exactly "відпустку".
  "відпустки" misses, because no stemming happens and it is a different
  word. "оплачувана відпустка" misses too: both words must match, and
  "оплачувана" is not "оплачувану".
- "на" is not a stop word in `simple`, so it finds every Chunk that
  contains it, which is most Ukrainian text.
- "сім'ї" with a plain apostrophe misses. The plain apostrophe splits it
  into "сім" and "ї", while the Ukrainian apostrophe keeps "сімʼї" as one
  word.
- "leave" finds the English Document, because `simple` stores English
  words as they are. "leaves" misses, because nothing cuts it to "leave".
- The reader gets only the visible Document. The permission check is part
  of the same query, so the hidden Chunk is never returned, however well
  it matches.

### Prediction check

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

All five predictions matched the fixture report and the Step 6 output. That
is expected, not a good guess: they were drafted after the prototype had
run. Step 6 showed the reason for each miss directly. "відпустка" in the
title and "відпустку" in the text are stored as two different words. The
query "ВІДПУСТКИ" becomes a third word, `'відпустки'`. "сім'ї" with a plain
apostrophe becomes `'сім' <-> 'ї'`, while "сімʼї" stays one word,
`'сімʼї'`, so the two spellings can never match the same text.

## Design

Changed files:

- `migrations/versions/d8e9f0a1b2c3_simple_full_text_search.py`: the SQL
  function `simple_search_vector(title, heading_path, body)`, which builds
  `setweight(to_tsvector('simple'::regconfig, normalize(..., NFC)))` with
  weights A, B and C; the column `chunks.search_simple` (added nullable,
  backfilled from `documents.title`, then NOT NULL); the GIN index
  `ix_chunks_search_simple` with `fastupdate = off`; and one trigger,
  `chunks_simple_search`. The downgrade removes all of it in reverse order.
- `src/knowledge_service/persistence/schema.py`: declares the column and
  the index; `tests/test_indexes.py` lists `ix_chunks_search_simple`.
- `src/knowledge_service/retrieval/lexical_search.py`:
  `search_ukrainian_chunks`, `ukrainian_search_statement`, and the `SIMPLE`
  configuration. English and Ukrainian search are thin wrappers over the
  private `_search` and `_search_statement`.
- `docs/architecture.md`: the `retrieval` row and the `chunks` rules row.
- Tests: `tests/test_lexical_search.py` (the bad-input test now runs
  against both searches; a new test checks the `simple` configuration) and
  the new `tests/test_ukrainian_lexical_search_integration.py`.

Why every Chunk gets `search_simple`: the language is not routed yet; that
is M05-T03. Storing both word lists for every Chunk lets the router pick a
configuration per question without another migration.

Why `simple`: PostgreSQL has no Ukrainian dictionary. `simple` only splits
and lowercases, so it is an honest baseline. It gives exact matches in any
letter case and misses every other form of a word, and later tasks must
beat it with measurements.

Why one trigger: the Chunk trigger runs `BEFORE INSERT OR UPDATE`, reads
the title `FOR SHARE`, and rebuilds the words. A title change already makes
the English Document trigger `documents_english_search` update the
Document's Chunks, and that update fires the Chunk trigger. A second
Document trigger only doubled the work on every rename, and no test could
tell it was there, so the review removed it. The migration comment records
the dependency and the lock order: a writer locks the Document before its
Chunks.

Why NFC: a letter like "й" or "ї" can be stored as one code point or as a
base letter plus a mark, and the two forms never match. The SQL function
normalizes the stored text, and `_search` normalizes the question after
the input checks.

The shared search: one statement, `search_simple @@
websearch_to_tsquery('simple', :query_text)` with `eligible_chunks_predicate`
in the same `WHERE`, `ORDER BY ts_rank DESC, chunk_id`, `LIMIT`. Text and
limit are checked before the database is used. The query runs in a
savepoint. The one error typed text can cause becomes `InvalidLexicalQuery`,
raised outside the `except` block, so neither `__cause__` nor `__context__`
carries the database error that repeats the query text.

The adversarial review of the prototype found two breaks, each fixed with
a regression test that failed against the unfixed code. Decomposed letters
missed their composed form, on the query side and on the stored side; both
are now NFC-normalized. The refused-query error kept the database error in
its exception chain; it is now raised without one. It also found the
redundant Document trigger, removed as above.

## Verification

Commands, run with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set, one after
another against the test database:

| Command | Result |
|---|---|
| `uv run pytest tests/test_lexical_search.py -q` | 29 passed |
| `uv run pytest tests/test_ukrainian_lexical_search_integration.py -q -m ""` | 14 passed |
| `make test-integration` | 236 passed, 910 deselected |
| `make format-check`, `make lint`, `make typecheck` | 166 files formatted; all checks passed; 0 errors |
| `make check` | 1143 passed, 3 deselected; coverage 95%, `lexical_search.py` 98% |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `uv run pre-commit run --all-files` | every hook passed |

After Step 2 alone, `make test-integration` gave 1 failed, 221 passed: the
expected failure `test_the_migrated_schema_matches_the_declared_tables`,
fixed by Step 3. The test database is at `d8e9f0a1b2c3 (head)`, with
collation and ctype `en_US.utf8`.

Fixture report, from `FIXTURE_REPORT` in
`tests/test_ukrainian_lexical_search_integration.py`, through
`search_ukrainian_chunks` on the real database:

| Question | Found | Why |
|---|---|---|
| `відпустку` | vidpustka.txt, simya.txt | exact form in the text |
| `ВІДПУСТКА` | vidpustka.txt | exact form in a title, any letter case |
| `ЇХНЮ` | vidpustka.txt | Ukrainian capitals ї, і lowercase |
| `"оплачувану відпустку"` | vidpustka.txt | exact phrase |
| `непрацездатність` | likarnyanyi.md | exact form in a heading |
| `зв'язку` | simya.txt | both apostrophes split the word the same way |
| `сімʼї` | simya.txt | the U+02BC apostrophe keeps the word whole |
| `відпустки` | nothing | miss: another case of відпустка, no stemming |
| `лікарняного` | nothing | miss: another case of лікарняний, no stemming |
| `оплачувана відпустка` | nothing | miss: the text has оплачувану |
| `сім'ї` | nothing | miss: an ASCII apostrophe splits the word, the text has ʼ |
| `leave` | leave.txt | any language matches in its exact form |
| `leaves` | nothing | miss: no English stemming either |
| `every` | leave.txt | no stop words: English every is kept |
| `на` | likarnyanyi.md, vidpustka.txt | noise: no stop words, so на matches every Chunk with it |

What the other tests prove, through `search_ukrainian_chunks` unless noted:

- Success: the fixture report and
  `test_a_title_match_outranks_a_heading_match_and_a_text_match`.
- Highest-risk edge: `test_a_better_match_the_reader_may_not_see_is_never_returned`,
  also with `limit=1` and for another user.
- Typed failure: the parametrized pre-database refusal unit test for both
  searches, and
  `test_text_postgresql_cannot_parse_is_refused_and_the_transaction_survives`,
  which checks that the error has no cause or context and no planted
  marker in its message.
- Safe syntax: `test_any_typed_text_is_read_as_words_never_as_syntax`.
- Unicode: `test_decomposed_letters_match_their_composed_form`.
- Storage: the title-change test (checking both word lists for stale
  Chunks), the concurrent-rename, direct-write, GIN-index (`EXPLAIN` on
  `ukrainian_search_statement`), and migration-backfill tests.

Step 6 output from the test database:

```text
'відпустка':1A 'відпустку':9C 'має':4C 'на':6C 'оплачувану':8C 'правила':2B 'право':5C 'працівник':3C 'щорічну':7C
'відпустки' & 'оплачувану' <-> 'відпустку' & !'лікарняний' | 'відгул'
'сім' <-> 'ї'
'сімʼї'
```

Deliberate break, undone afterwards: with `'simple'` changed to
`'english'` in `SIMPLE`, two tests failed:
`test_the_ukrainian_query_is_a_bound_parameter_with_the_simple_configuration`
and `test_the_fixture_report_shows_exact_matches_and_known_misses` (English
stemming made "leaves" match); 41 passed. After restoring, the file matched
the verified version byte for byte and all 43 passed.

Security and privacy: the question is one bound parameter, never part of
the SQL text. The permission check sits in the same statement, before
ranking and `LIMIT`. The module logs nothing, and the one handled database
error is replaced with no chain and no query text. The review's 3,000
random hostile questions never returned a hidden Document and raised
nothing but `InvalidLexicalQuery`.

Standards and Spec reviews: nothing blocking.

Known limits (the limitation report):

- No stemming: every case form is a separate word, so "відпустки" misses
  "відпустку". English words in `search_simple` are not stemmed either.
- No stop words: "на" matches every Chunk containing it and scans
  everything, as does a query that is only a negated word.
- Apostrophes: ASCII `'` and U+2019 split a word; U+02BC keeps it whole.
  So "сім'ї" never matches "сімʼї".
- Stress marks such as "за́мок" stay inside the word, so "замок" misses it.
- Lowercasing Cyrillic depends on the database ctype. Under `C` it would
  not lowercase Cyrillic and would keep ’ inside words; the fixture report
  would catch that. The test database uses `en_US.utf8`.
- The `simple` configuration can be changed on the server, and the stored
  words would not be rebuilt.
- Every Chunk gets `search_simple`, and Ukrainian search matches exact
  words in any language; routing questions by language is M05-T03.
- A raw `UPDATE chunks` during a concurrent rename of its Document can
  deadlock, as in M05-T01. The application is safe because
  `reconcile_document` locks the Document first.
- Other database errors, such as a timeout, are re-raised unchanged and may
  carry the query text in their parameters.
- English search now NFC-normalizes the question too, while `search_en` is
  not normalized; text that bypasses ingestion's own NFC step could miss.
- `search_simple` follows a title change only through the English Document
  trigger; a later change that narrows that trigger would leave Ukrainian
  words stale. The title-change test checks both word lists.
- No test on this path uses a tombstoned Document; the shared eligibility
  check covers it.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **What `search_ukrainian_chunks` hides.** A caller passes text, a
   reader, and a limit. It never deals with Unicode forms, which word list
   and configuration are read, the search-box syntax, weights, the
   triggers that keep `search_simple` current, the GIN index, the
   permission check, the tie-break, or the savepoint that keeps its
   transaction usable when PostgreSQL refuses a query.
2. **The easiest invariant to break.** Someone speeding up renames could
   make the English Document trigger update only `search_en`, or give the
   Chunk trigger a `WHEN` clause or a column list. Ukrainian words would
   then keep the old title. `test_a_title_only_change_updates_what_the_chunks_match`
   would catch it, because its stale-Chunk check compares both word lists
   with what the current title gives.
3. **Expected misses that start matching.** The failure is useful. It
   says the behaviour changed on purpose or by accident, and the person
   should check which: if a Ukrainian dictionary was added, move the row
   from the misses to the matches and record the improvement; if not, find
   what changed the configuration.
4. **"на" in a long question.** Search words are joined with AND, so "на"
   rarely removes a result, but it adds rank to every Chunk with many
   "на", which can push a better Chunk down. A labelled question set with
   the expected best Chunk, compared with and without such words, would
   measure the harm.
5. **Proving the baseline good or bad.** Questions written by real
   Ukrainian speakers who have not read the Documents, using the case
   forms people naturally type, with the expected Chunks labelled. M05-T07
   builds the first such set; recall on it shows how much `simple` misses.
