# M05-T04 — Lexical Candidates evidence

Completed: 2026-10-30

## Invariant

Vector and lexical search return Candidates with the same provenance, read
from the stored Chunk and Document: Chunk, Document, and Source IDs,
content version, ordinal, external ID, title, headings, anchor, and text.
Every Candidate is a Chunk the reader may see. Ranks count from 1, best
first, ties go by Chunk ID, and at most `limit` come back, with
`truncated` True only when more eligible Chunks exist. A lexical
highlight always points at a matched word inside the Candidate's text, or
there is none. No Document or Question text appears in a repr.

## Expected failing check

Before the change, `tests/test_candidate_contract_integration.py` fails to
import `knowledge_service.retrieval.candidates`.

## Predictions

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

- "requesting leaves" highlights "request" and "leave": English cuts both
  the Question's words and the text's words to the same stems, so the
  exact forms in the text are marked even though the Question used other
  forms. "parental" and "portal" stay unmarked.
- "E-1042" gets one highlight. PostgreSQL splits the identifier into two
  parts, `e` and `-1042`, so it probably marks them as two touching
  pieces; the code joins touching pieces into one.
- A Chunk that matches only through its title gets no highlights.
  Highlights are positions inside `text`, and the title is not part of it.
- Three Chunks match and the limit is 2: two come back and `truncated` is
  True, because the extra third row shows that more exist. If one of the
  three belongs to a hidden Document, only two are eligible, so both come
  back and `truncated` is False: the hidden Chunk is never counted.
- `policy or -leave` probably still highlights "leave". `ts_headline`
  marks every word that appears in the query, and it does not treat an
  excluded word differently.

### Prediction check

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

All five predictions held, and none was wrong. That is expected, not a
good guess: they were drafted after the prototype had run. In psql,
`ts_headline` marked `[request]` and `[leave]`, marked the identifier as
two touching pieces, `[E][-1042]`, and marked the excluded `[leave]` in
"The [policy] and the [leave].". It kept `<b>the</b>` and three spaces
unchanged, which is why the marks can become exact positions.

Through `search_lexical_chunks`, the same Questions gave these highlights
(`text[start:end]`):

| Question | Route | Spans | Words |
|---|---|---|---|
| `requesting leaves` | English | (10, 17), (27, 32) | request, leave |
| `E-1042` | both | (6, 12) | E-1042 |
| `policy or -leave` | English | (4, 10), (19, 24) | policy, leave |
| `the policy`, stated `mixed` | both | (0, 3), (4, 10) | The, policy |
| `розгортає deploying` | both | (8, 17), (32, 43) | розгортає, deployments |
| `vacation`, only in the title | English | none | none |

One detail the predictions left out: in "the policy" with both lists, only
`simple` marks "The", because English drops it as a stop word; the
highlights of the two lists are merged.

## Design

Changed files:

- `src/knowledge_service/retrieval/candidates.py` (new): `Candidate`, a
  typing `Protocol` with one read-only property per shared field: `rank`,
  `chunk_id`, `document_id`, `source_id`, `content_version`, `ordinal`,
  `external_id`, `title`, `heading_path`, `source_anchor`, and `text`. Its
  docstring states what every branch promises: eligible, ranked, bounded,
  quiet.
- `src/knowledge_service/retrieval/lexical_search.py`: `LexicalCandidate`
  replaces `LexicalMatch` and adds the shared provenance fields, `score`,
  and `highlights`; `Highlight` holds one `start`/`end` pair;
  `LexicalSearch` now holds `route`, `limit`, `candidates`, and
  `truncated`. `lexical_search_statement` ranks and limits in a subquery
  and adds one `ts_headline` column per configuration in the outer query.
  `_search` asks for `limit + 1` rows. `_candidate_from`, `_highlights`,
  and `_spans` build Candidates and highlights.
- `docs/architecture.md`: the `retrieval` row names `Candidate`.
- Tests: the new `tests/test_candidate_contract_integration.py` and
  `tests/test_lexical_candidates_integration.py`, two new unit tests in
  `tests/test_lexical_search.py`, and the renames `LexicalMatch` →
  `LexicalCandidate` and `result.matches` → `result.candidates` in three
  older lexical test files.

Vector search did not change: `VectorCandidate` already had every field.

Why a `Protocol`: any class with the fields fits, with no base class, so
vector search needs no change, and pyright checks both classes against it
(`test_both_candidate_types_meet_the_contract`). The fields are read-only
properties because both Candidate classes are frozen dataclasses.

What is shared and what is not: provenance and `rank` are shared. Each
branch keeps its own fit: vector search a cosine distance (lower is
better), lexical search a `ts_rank` score (higher is better). The scales
mean nothing to each other, so only `rank` compares across branches, and
M05-T05 fuses by rank.

Highlights: `ts_headline` with `HighlightAll=true` returns the whole text
with each matched word between `\x02` and `\x03`, every other character
unchanged. `_spans` walks the marked text against the stored text and
returns positions; touching spans join, and the spans of every
configuration that matched are merged. When a marked text is not exactly
the stored text plus marks, for example because the text itself holds a
marker character, the Candidate gets no highlights: wrong positions would
be worse than none. Positions, not marked-up text, reach the caller, so
HTML stored in a Document cannot pose as a highlight.

Only returned rows are highlighted: the subquery applies the permission
check, orders by score then Chunk ID, and limits; `ts_headline`, which
reads a whole text, runs in the outer query on at most `limit + 1` rows,
and only for a configuration whose `@@` matched that row. The outer query
orders again by score then Chunk ID.

Bounded results: `limit` is still 1 to `MAX_CANDIDATES`; the query asks for
`limit + 1` rows, and the extra row sets `truncated`. The permission check
stays the `eligibility` argument of `lexical_search_statement`, the
predicate slot, in the same `WHERE` as the ranking.

The adversarial review of the prototype found no break in the search code
and three gaps in the shared suite, each closed with a test that fails on
the mistake it targets: a limit that kept the worst rows (the limit test
now has three different fits); a missing tie-break by Chunk ID (the tie
test stores six tied rows against Chunk ID order, and because PostgreSQL
may keep the right tied rows by chance, a unit test also checks that the
limited subquery orders ties by Chunk ID); and a hard-coded content
version (the provenance test stores a second version).

## Verification

Commands, run with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set, one after
another against the test database:

| Command | Result |
|---|---|
| `uv run pytest tests/test_lexical_search.py tests/test_lexical_routing.py tests/test_candidate_contract_integration.py -q -m "not integration"` | 112 passed, 20 deselected |
| `uv run pytest tests/test_candidate_contract_integration.py tests/test_lexical_candidates_integration.py -q -m integration` | 28 passed, 15 deselected |
| The two new suites plus the three older lexical integration suites, `-m integration` (Spec review run) | 84 passed |
| `uv run pytest tests/test_lexical_search.py tests/test_lexical_routing.py -q` | 97 passed |
| `make test-integration` | 284 passed, 993 deselected |
| `make format-check`, `make lint`, `make typecheck` | 172 files formatted; all checks passed; 0 errors |
| `make check` | 1274 passed, 3 deselected; coverage 95%, `lexical_search.py` 98% |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `uv run pre-commit run --all-files` | every hook passed |

No migration: the test database stays at `d8e9f0a1b2c3`.

The shared contract suite, each test run once for vector search and once
for lexical search, through `search_similar_chunks` and
`search_lexical_chunks`:

| Test | Proves for both branches |
|---|---|
| `test_each_candidate_carries_the_provenance_of_its_chunk` | Every field equals the stored Chunk and Document, for a second content version with two headed Chunks. |
| `test_ranks_count_from_one_best_first_and_ties_go_by_chunk_id` | Ranks 1 to n, best fit first, equal fits by Chunk ID. |
| `test_the_limit_bounds_the_candidates_and_truncated_says_more_exist` | At limits 1, 2, 3, and 100 the best Chunks are kept and `truncated` is right. |
| `test_the_limit_cuts_ties_by_chunk_id_whatever_order_rows_are_stored_in` | Six tied rows stored against Chunk ID order are cut by Chunk ID at every limit. |
| `test_a_better_chunk_the_reader_may_not_see_is_never_a_candidate` | A hidden, better Chunk never returns and never sets `truncated`; its reader gets it first. |
| `test_no_eligible_chunk_is_an_empty_result` | No eligible Chunk is an empty result. |
| `test_a_result_repr_shows_no_document_text` | A planted marker in the title, headings, text, and external ID never shows in a repr. |
| `test_a_bad_limit_is_refused_before_the_database` | 0, -1, 101, `True`, `2.0`, `"5"`, and `None` raise each branch's typed error before any query. |
| `test_both_candidate_types_meet_the_contract` | Pyright accepts `VectorCandidate` and `LexicalCandidate` as `Candidate`. |

Highlights, through `search_lexical_chunks` on the real database
(`tests/test_lexical_candidates_integration.py`): stemmed words ("request",
"leave" for "requesting leaves"), one highlight for `E-1042`, merged
highlights of two configurations ("The" from `simple`, "policy"), both
alphabets ("розгортає", "deployments"), none for a title-only match, and
none for three stored texts holding marker characters. Unit tests check
that `ts_headline` runs only outside the limited subquery and that the
subquery breaks ties by Chunk ID.

Step 5 output from the test database:

```text
Employees [request] parental [leave] in the portal.
Error [E][-1042] means the request was rejected.
The [policy] and the [leave].
Read <b>the</b> [leave]   policy.
```

Deliberate break, undone afterwards: with `limit=limit` instead of
`limit=limit + 1` in `_search`, two tests failed,
`test_the_limit_bounds_the_candidates_and_truncated_says_more_exist[1-1-True-lexical]`
and `[2-2-True-lexical]`; 26 passed. After restoring, the file matched the
learner's version byte for byte and 28 passed. Other mutations each failed
at least one test: the subquery ordered worst first, the subquery without
its Chunk ID tie-break, a hard-coded content version, and touching
highlights not joined.

Security and privacy: the permission check sits in the same statement as
the ranking, before `LIMIT` and before `ts_headline`, so a hidden Chunk is
never ranked, counted, highlighted, or returned. The Question is one bound
parameter. Document text fields are kept out of repr; `Highlight` holds
only integers. The review's fuzzing planted a marker in a hidden Document;
it never appeared in any field or repr, and `truncated` never counted it.

The review also confirmed: 1,410 fuzzed Questions over 20 hostile stored
texts gave 1,532 highlights, each inside the text, ordered, not
overlapping, and on a matched word; 1,500 random Questions raised nothing
but `InvalidLexicalQuery`; a smaller limit gave a prefix of a larger one on
every route; and on 25,000 Chunks with 5,000 matching, `EXPLAIN ANALYZE`
showed `ts_headline` on 11 rows for a limit of 10, with both GIN indexes
on the mixed route.

Standards and Spec reviews: nothing blocking. Non-blocking notes: no
test reads `LexicalSearch.limit`; `search_english_chunks` and
`search_ukrainian_chunks` return Candidates without `truncated`;
`Candidate` leaves out distance, score, and highlights on purpose, so
fusion must use `rank`; `Branch.name` in the contract suite is never read;
two tests that need no database sit in an `_integration` file (they still
run with the unit tests); and the outer query lists the provenance columns
again, so a new field means three edits.

Known limits:

- A word the Question excludes, such as `leave` in `policy or -leave`, is
  still highlighted where it occurs.
- A quoted phrase highlights each of its words anywhere, not only where
  they stand together.
- A token over 2,047 characters makes `ts_headline` change the text, so
  that Chunk gets no highlights.
- Stored text that is not NFC-normalized can match with no highlights,
  because the stored words are built from NFC text and `ts_headline` reads
  the raw text.
- A match only in the title or the headings has no highlight.
- Joined pieces vary: "E-1042" is one highlight, "policy-leave" stays two.
- Lexical search has no metadata filters yet; the permission check is its
  only predicate slot.
- `search_english_chunks` and `search_ukrainian_chunks` return Candidates
  without `truncated`.
- Without a tie-break, PostgreSQL may keep the right tied rows by chance,
  so the tie behaviour test cannot fail every time; the SQL-shape unit
  test is the reliable guard.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **What `search_lexical_chunks` hides.** A caller passes a Question, a
   reader, a limit, and maybe a language. It never sees the routing, the
   stop-word question, the two-list query and its score, the permission
   check, the subquery that limits before highlighting, the marker
   characters and how they become positions, the extra row behind
   `truncated`, or the savepoint and refusal. It gets Candidates it can
   cite and fuse like vector Candidates.
2. **Highlighting in the ranking query.** `ts_headline` would read the
   text of every matching Chunk instead of `limit + 1`, so a common word
   that matches thousands of Chunks would make every search slow.
   `test_only_the_ranked_and_limited_rows_are_highlighted` would fail,
   because `ts_headline` would sit inside the limited subquery.
3. **Distance and score left out.** They measure fit on different scales,
   in opposite directions, and a lexical score even depends on the route.
   A shared field would invite comparing them. Rank is the only measure
   both lists share, so M05-T05 should fuse by rank, as Reciprocal Rank
   Fusion does.
4. **Positions for a web page.** Positions carry no markup. The page builds
   its own highlighting around `text[start:end]` and escapes the rest, so
   a Document holding `<script>` or a fake marker cannot inject anything or
   fake a highlight. Marked-up text would make the page trust markup that
   came from the Document.
5. **A wrong search the suite still lets pass.** A lexical search whose
   highlights are valid positions but point at an unmatched word would
   pass, as long as it happens in a text none of the highlight tests uses.
   A search that breaks ties by Document ID instead of Chunk ID would also
   pass, because every tied Document in the suite has one Chunk. A fuzz
   test that checks each highlighted word against the query's words, and a
   tie test with several Chunks per Document, would catch these.
