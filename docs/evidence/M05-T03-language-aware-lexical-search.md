# M05-T03 — Language-aware lexical search evidence

Completed: 2026-10-29

## Invariant

Every lexical Question is matched against each configuration that one of
its signals supports: the language the caller states, the alphabet of its
words (identifiers aside), and whether English keeps any of its words. It
is matched against one configuration only when all signals agree. On every
route, only Chunks the reader may see come back, each once, best first,
with ties broken by Chunk ID, and the route holds no Question text.

## Expected failing check

Before the change, `tests/test_lexical_routing.py` fails to import
`knowledge_service.retrieval.lexical_routing`.

## Predictions

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

- "requesting leaves" with no stated language gets only the English list:
  every word is Latin and nothing else is stated. With Ukrainian stated it
  gets both lists, because the stated language adds `simple` but cannot
  remove the English list that the alphabet supports.
- "розгортає deploying" uses both alphabets, so it gets both lists. It
  finds the Chunk "розгортає сервіси через deployments": English stems
  "deploying" and "deployments" to "deploy", and leaves "розгортає"
  unchanged, so both words match in the English list.
- "помилка E-1042" gets only `simple`: the identifier is skipped, and the
  remaining word is Cyrillic. "E-1042" alone has no alphabet left, so it
  gets both lists. Both lists split it the same way, so either finds it.
- English treats "IT" as the stop word "it" and drops it, so the query is
  empty and matches nothing. The search should notice that English keeps
  no word and add `simple`, which keeps "it" and finds the Chunks that
  contain it.
- The reader gets only the visible Document's Chunk, once. Searching both
  lists widens what can match, but the permission check sits in the same
  query, so the hidden Document never comes back, however well it matches.

### Prediction check

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

All five predictions held, and none was wrong. That is expected, not a
good guess: they were drafted after the prototype had run. The Step 2
command printed the routes as predicted. In psql, English turned "IT" into
an empty query (`numnode` 0, with the "only stop words" notice), while
`simple` kept `'it'`. "розгортає deploying" became `'розгортає' & 'deploy'`
in English and `'розгортає' & 'deploying'` in `simple`. One detail the
predictions left out: for "IT" the search does not swap English for
`simple`; it searches both, because the Latin alphabet still supports
English. And "leave_policies оновлено" showed why an underscore is not an
identifier: English split it into `'leav' <-> 'polici'`, so skipping it
would have lost the stemming.

## Design

Changed files:

- `src/knowledge_service/retrieval/lexical_routing.py` (new):
  `LexicalConfiguration` (`english`, `simple`), `QuestionScript` (Latin,
  Cyrillic, mixed, none), `InvalidLexicalRoute`, the frozen `LexicalRoute`
  (configurations non-empty, unique, and in enum order, plus the clues that
  chose them), `route_lexical_question`, and `question_script`.
- `src/knowledge_service/retrieval/lexical_search.py`:
  `search_lexical_chunks`, which returns a `LexicalSearch` with the route
  and the matches; `lexical_search_statement`, which builds the query for
  one or two configurations; `english_search_statement` and
  `ukrainian_search_statement`, now thin wrappers over it; and `_execute`,
  which holds the savepoint and the refusal. The private
  `_search_statement` is gone.
- `docs/architecture.md`: the `retrieval` row names `search_lexical_chunks`.
- Tests: the new `tests/test_lexical_routing.py` and
  `tests/test_language_aware_lexical_search_integration.py`, and new cases
  in `tests/test_lexical_search.py`.

The clues and what each adds:

| Clue | English | `simple` |
|---|---|---|
| Stated `en` | yes | |
| Stated `uk` | | yes |
| Stated `mixed` | yes | yes |
| Stated `unknown`, or nothing | | |
| Words only in Latin letters | yes | |
| Words only in Cyrillic letters | | yes |
| Both alphabets, or no letters at all | yes | yes |
| English keeps no word of the Question | | yes |

Why no clue is trusted alone: the stated language comes from a setting or
a form, not from this Question, and a Ukrainian reader may type "parental
leave". The alphabet is a guess from a few words. So the route is the union
of what the clues support. A clue can add a configuration but never remove
one, so a wrong clue costs a second index lookup and never loses a Chunk.

Why not `detect_language`: it labels whole Documents, needs at least 20
letters, and calls Cyrillic Ukrainian only when it sees letters such as
"ї" or "є". "відпустку" would come back unknown. For choosing a word list
only the alphabet matters, because `simple` is the only list for any
Cyrillic text.

Identifiers: a word with a digit, such as `E-1042` or `api_v2`, is
skipped when reading the alphabet. Both configurations split it the same
way, so it matches on any route. An underscore alone does not make an
identifier: PostgreSQL splits `leave_policies` into words that English
stems.

Two configurations in one query: `search_en @@ q OR search_simple @@ q`,
with one bound `query_text` and the permission check in the same `WHERE`,
so PostgreSQL can combine both GIN indexes. The score is
`greatest(CASE WHEN matched THEN ts_rank END, ...)`: a configuration counts
only if it matched the whole Question, because `ts_rank` still scores a
partial match. Order is score descending, then Chunk ID, then `LIMIT`.

The stop-word question: only when the route is English alone,
`search_lexical_chunks` runs `SELECT numnode(websearch_to_tsquery('english',
:query_text))`. If it is 0, English keeps no word ("IT", "the who"), and the
Question is routed again with `english_words=False`, which adds `simple`.
Other Questions still make one database call.

The adversarial review of the prototype found four breaks, each fixed with
a regression test that failed against the unfixed code: a Latin Question
of English stop words found nothing (the stop-word question); a word
joined by an underscore was skipped as an identifier and lost English
stemming (only a digit makes an identifier now); the router raised an
untyped error on text that is not a string (`InvalidLexicalRoute` now);
and the statement builder accepted repeated or wrongly typed
configurations (`InvalidLexicalQuery` now).

## Verification

Commands, run with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set, one after
another against the test database:

| Command | Result |
|---|---|
| `uv run pytest tests/test_lexical_routing.py tests/test_lexical_search.py -q` | 95 passed (44 routing, 51 search) |
| `uv run pytest tests/test_language_aware_lexical_search_integration.py -q -m integration` | 20 passed |
| `uv run pytest tests/test_lexical_search_integration.py tests/test_ukrainian_lexical_search_integration.py -q -m integration` (after Step 3) | 36 passed |
| `make test-integration` | 256 passed, 976 deselected |
| `make format-check`, `make lint`, `make typecheck` | 169 files formatted; all checks passed; 0 errors |
| `make check` | 1229 passed, 3 deselected; coverage 95%, `lexical_search.py` 98% |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `uv run pre-commit run --all-files` | every hook passed |

No migration: the test database stays at `d8e9f0a1b2c3`.

Routes and results through `search_lexical_chunks` on the real database,
from `tests/test_language_aware_lexical_search_integration.py`. The corpus
is leave.txt (English), vidpustka.txt (Ukrainian), and deploy.txt
(Ukrainian with "deployments"); all three mention `E-1042`.

| Question | Stated | Route | Found |
|---|---|---|---|
| `requesting leaves` | `en`, none, or `unknown` | English | leave.txt |
| `requesting leaves` | `uk` | both | leave.txt |
| `відпустку` | `uk`, none, or `unknown` | `simple` | vidpustka.txt |
| `розгортає deploying` | none or `uk` | both | deploy.txt |
| `E-1042` | none | both | all three, each once |
| `error E-1042` | none | English | leave.txt |
| `помилка E-1042` | none | `simple` | vidpustka.txt, deploy.txt |
| `IT`, `it` | none or `en` | both (English keeps no word) | it.txt |
| `the who` | none or `en` | both (English keeps no word) | who.txt |
| `leave_policies оновлено` | none | both | policy.txt ("leave_policy") |

What the other tests prove:

- Permissions: `test_a_better_match_the_reader_may_not_see_is_never_returned`,
  on a two-configuration route, also with `limit=1`, while the other user
  does get the hidden Document first.
- No duplicates and order: `test_a_chunk_found_by_both_configurations_is_returned_once`.
- Refusal: `test_a_refused_question_on_any_route_keeps_no_question_text`,
  for a Cyrillic and a Latin Question: `InvalidLexicalQuery` with no cause
  or context, no planted marker, and a usable transaction.
- Indexes: `test_both_routes_read_both_gin_indexes` finds
  `ix_chunks_search_en` and `ix_chunks_search_simple` in the `EXPLAIN`.
- Score guard: a unit test checks that two configurations share one bound
  parameter and score with `greatest(CASE WHEN ...)`. No behaviour test
  could show the guard, because partial ranks never beat full ranks in
  probes.
- Router: every combination of three words from a hostile list, with every
  stated language, gets a non-empty route; the route's `repr` holds no
  Question text; bad text, language, or `english_words` raise
  `InvalidLexicalRoute`.

Step 5 output from the test database:

```text
NOTICE:  text-search query contains only stop words or doesn't contain lexemes, ignored
0|'it'
'розгортає' & 'deploy'|'розгортає' & 'deploying'
'leav' <-> 'polici' & 'оновлено'|'leave' <-> 'policies' & 'оновлено'
```

Deliberate break, undone afterwards: with the router trusting a stated
language alone (`wanted = set(_STATED[stated])`), five tests failed. Three
were routing cases (Latin with `uk`, Cyrillic with `en`, `E-1042` with
`en`), 92 passed. Two were integration tests,
`test_a_wrong_stated_language_does_not_lose_english_stemming` and
`test_a_mixed_question_keeps_english_stemming_for_its_english_words`, 18
passed. After restoring, the file matched the verified version byte for
byte, and 95 and 20 passed.

Security and privacy: the Question is one bound parameter, never part of
the SQL text, on every route. The permission check sits in the same
statement, before ranking and `LIMIT`. The route holds no Question text,
so it is safe to log; it never decides who may read a Chunk. The review's
600 random hostile Questions never returned a hidden Document or a
repeated Chunk, the order was always the same, and a smaller limit always
returned the start of the larger result.

Standards and Spec reviews: nothing blocking. Non-blocking notes:
`LexicalRoute.english_words` stays `True` whenever the stop-word question
did not run (any route that already has `simple`), so a logged route can
claim English keeps a word when nobody checked; results are unaffected.
The integration helper `found` runs each Question a second time instead of
reading the names from the first result. The probe and the statement each
build the bound `query_text` and the English `websearch_to_tsquery`. Stated
`mixed` is tested only at the router, not through `search_lexical_chunks`.

Known limits:

- Scores depend on the route: `simple` keeps stop words, so the same Chunk
  can score higher when both lists are searched. Compare positions within
  one result, never scores across routes; M05-T05 fuses by position.
- Other database errors, such as a timeout, are re-raised unchanged and
  may carry the Question text, as in M05-T01 and M05-T02.
- Fullwidth Latin letters, such as "ｌｅａｖｅ", count as no alphabet, so
  they get both lists.
- A word glued to digits, such as "відпустка2024", is skipped when reading
  the alphabet, and only matches in that exact form.
- The alphabet cannot tell Ukrainian from Russian. Today that does not
  matter, because `simple` is the only list for Cyrillic.
- For Cyrillic words the two lists are identical today, so routing only
  changes how Latin words match. A Ukrainian dictionary would make the
  routing table matter for Cyrillic too.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **What `search_lexical_chunks` hides.** A caller passes text, a reader,
   a limit, and maybe a language. It never decides which word list to
   read, how to treat identifiers or mixed alphabets, what to do when
   English drops every word, how two lists are combined and scored without
   repeating a Chunk, the permission check, the tie-break, or the savepoint
   and refusal. The returned route tells it what was chosen and why.
2. **Using only the stated language.** A Ukrainian-stated English Question
   such as "requesting leaves" would lose stemming and miss "request" and
   "leave"; a mixed Question would lose "deploying" → "deployments"; an
   English-stated Cyrillic Question would lose nothing today, but only
   because the lists agree on Cyrillic. The deliberate break showed it:
   `test_a_wrong_stated_language_does_not_lose_english_stemming` and the
   mixed-Question test fail.
3. **A Ukrainian dictionary.** The Cyrillic rows: "only Cyrillic letters →
   `simple`" would have to become the new Ukrainian list, and a stated
   `uk` with Latin text would need to keep English. The "no letters" row
   and identifiers need checking too, because a dictionary may split or
   change words like `E-1042` differently, and the claim that identifiers
   match on any route would no longer be free.
4. **Higher scores with both lists.** `simple` keeps stop words and exact
   forms, so a Chunk can rank higher by `simple` than by English, and
   `greatest` takes the higher. Within one result every Chunk is scored the
   same way, so the order is fair. Comparing a score from one route with a
   score from another, or fusing results by score, would mix two scales;
   that is why M05-T05 fuses by position.
5. **Questions that test the router.** Real Questions from the people who
   will use the service: Ukrainian speakers typing in Ukrainian, in
   English, and mixed, with product names and error codes, and with the
   language their settings state. Label the Chunks each should find. A
   wrong choice is a Question where a different route finds a labelled
   Chunk this route misses; count those, and the extra lookups where one
   list would have found the same. M05-T07 builds the first such set.
