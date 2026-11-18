# M07-T08 — Enforce ACLs in lexical search evidence

## Invariant

Every search by words, English, Ukrainian by topic words, Ukrainian as typed,
or both at once, applies the same eligibility rule as vector search: a Chunk
is matched, ranked, counted, and highlighted only if its Document is
available and granted to everyone, to the reader, or to one of the reader's
Groups, decided in the ranked query's WHERE before its ORDER BY and LIMIT.
One security contract runs the same adversarial corpora through vector and
lexical search, and each must return exactly what the reference rule allows,
with no hidden title, text, or highlight.

## Expected failing check

Before the change, `uv run pytest tests/test_search_security_contract_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_search_security_contract_integration.py`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What English lexical search with limit 1 returns to a reader when a hidden Document has the question's words in its title and the reader's only Document has them in its text: the reader's own Document: the hidden one is dropped in the ranked query's WHERE before `ORDER BY ts_rank` and `LIMIT`, so its title match never competes for the one slot.
- Whether `ts_headline` ever reads the text of that hidden Document: no: `ts_headline` runs only in the outer query, over the rows the ranked subquery kept after the permission check and the limit.
- Whether `"щорічна відпустка"` in quotes and `щорічна відпустка` without them run the same SQL condition, and why that matters for testing: no: without quotes `simple` reads topic words through `_topic_match`, with quotes it reads the text as typed through `websearch_to_tsquery`; each branch builds its own condition, so a test that takes only one branch cannot see a check missing from the other.
- Which tests fail if `eligibility` is removed from the ranked query's WHERE in `lexical_search_statement`: 162, with 100 passing: all 12 cases of `test_the_permission_check_comes_before_ranking_and_highlighting` and all 30 seeds of each of the five lexical searches in the contract; the 30 vector runs still pass.

## Design

- **Where the check sits.** `lexical_search_statement` (M05) puts `eligible_chunks_predicate(context)` in the WHERE of its `ranked` subquery, beside the match condition of every configuration, before that subquery's `ORDER BY score DESC, chunk_id` and `LIMIT k + 1`. The outer query only calls `ts_headline`, once per configuration that matched, on the rows `ranked` kept. Since M07-T07 the predicate checks the reader with `checked_reader` and ties each Chunk to its own Document. No production code changed in this task.
- **Two Ukrainian branches.** `simple` builds one condition for topic words (`_topic_match`) and another for text with web-search syntax (`websearch_to_tsquery`); `english` builds a third. The check must sit beside each.
- **One contract.** `tests/test_search_security_contract_integration.py` runs 30 seeded corpora through six searches, `dense`, `english`, `ukrainian`, `ukrainian_typed`, `both`, and `both_typed`, each lexical one asserting its route. For four readers each search must return exactly the Documents `may_read` allows, put first a Document that is the reader's best match by the reference (the nearest vector, or a title match when an allowed Document has one), report `truncated` only when more than one is allowed, and show no hidden name in text, title, external ID, heading, or highlight. It replaces M07-T07's vector-only `tests/test_vector_acl_adversarial_integration.py` and keeps its corpus-hardness and forgotten-join tests.
- **SQL order.** `test_the_permission_check_comes_before_ranking_and_highlighting` compiles every configuration set with plain words, syntax, Ukrainian topic words, and Ukrainian quoted text, and checks that `access_grants` is read before the ranked query's last `ORDER BY` and `LIMIT`, and that `ts_headline` appears only in the outer query.

## Verification

- Expected failing check: before the change, `uv run pytest tests/test_search_security_contract_integration.py -q` failed with `ERROR: file or directory not found: tests/test_search_security_contract_integration.py`.
- `uv run pytest tests/test_lexical_search.py -q`: 80 passed, including the 12 SQL-order cases, which pass at once because the code was already right.
- `uv run pytest -m "integration or not integration" tests/test_search_security_contract_integration.py -q`: 182 passed against PostgreSQL (6 searches × 30 seeds, the corpus check, the forgotten-join test), in about a minute.
- Gates: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2762 passed (+12); `make test-integration` 621 passed (+150); `make docs-check` passed.
- Deliberate break: with `.where(matched)` instead of `.where(matched, eligibility)` in `lexical_search_statement`, 162 failed, 100 passed: all 12 SQL-order cases and all 30 seeds of each of the five lexical searches; the 30 vector runs still passed. Restored: 262 passed. The prediction was right.
- Known limits: sparse search applies the same predicate but is outside the contract; `load_searchable_chunks` in `persistence.chunks` reads Chunks with no permission check and nothing in `src` calls it; each corpus Document has one Chunk and no headings; lexical and hybrid retrieval are not in the answer stream yet.

## Review

An independent adversarial review ran 72 hostile questions with five language settings and two limits (quotes, `or`, exclusions, stop words only, 33 dashes, decomposed letters, apostrophe variants, tsquery syntax, invisible and right-to-left characters, 1,000-character questions), compared results, scores, highlights, and `truncated` before and after inserting hidden Documents, read 6.7 MB of DEBUG logs from SQLAlchemy and psycopg, checked every other reader of Chunk text for the predicate, and ran 25 mutations. Nothing hidden leaked, nothing depended on hidden rows, and every mutation that moved or dropped the check on a covered branch was killed. It found two gaps in the tests, both fixed:

- The contract never took the Ukrainian as-typed branch: a mutation that dropped the check only there survived every suite. Now the contract has `ukrainian_typed` and `both_typed`, and the SQL-order test compiles quoted text; the mutation is killed.
- The contract checked the best result only against its own longer list, which a wrong sort order also passes (a vector `ORDER BY chunk_id` mutation survived). Now it checks against the reference model; the mutation is killed.

Documented, not fixed: sparse search outside the contract, the unused `load_searchable_chunks`, uniform corpora, and an outer `ORDER BY` whose removal no test notices (ordering only, not permissions).

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **A shared contract.** The permission rule is shared, but each search writes its own SQL and decides where the rule goes. One contract held by every search makes a new search, or a new branch, meet the same standard, instead of each kind of search having its own idea of "enough tests".
- **One search per branch.** Coverage is about SQL branches, not functions: `search_lexical_chunks` is one function with three ways of building its match. A branch the contract never takes is a branch where the check can disappear unnoticed, as the review showed.
- **The best result against the reference.** Checking the limit-1 result against the longer list only proves the search agrees with itself. Checking it against the rule, the nearest allowed vector or a title match when one exists, catches a search that returns allowed Documents in the wrong order.
- **Left for later.** Reading Documents by ID with the same rule (M07-T09), existence leaks (M07-T11), and the full adversarial suite (M07-T15).

**Falsified assumption.** I assumed that dozens of Ukrainian lexical tests covered the Ukrainian search. The review shows that none of them used search syntax, so the as-typed branch, with its own SQL condition, was never run, and a check removed from it went unnoticed. So "many tests of a function cover all of it" is wrong; what counts is which branches the tests take.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. The corpora, the readers, the reference rule, the hidden-text check, and the ranking check come for free; the author adds one entry to `SEARCHES` that runs the new search and, if it has branches, one entry per branch.
2. `ts_headline` would read the text of every matching row, including Documents the reader may not see, before the limit cuts them, and highlighting a whole text for every match would cost far more than for the top k. `test_the_permission_check_comes_before_ranking_and_highlighting` would fail, because `ts_headline` would move into the ranked query.
3. They all used Ukrainian words without quotes, `or`, or `-`, so they all took the topic-word branch; the as-typed branch builds its own condition and was never executed.
4. `ts_rank` depends on word counts, weights, and saturation, so exact scores are hard to predict, and ties are broken by Chunk ID. The title rule is simple, stable, and still catches a search that ranks a weaker allowed Document first.
5. All six return alice's own Document, if each matches it: `dense` by vector, the lexical ones because her text holds the words. Her Document needs both Ukrainian words for the topic-word search and the exact phrase for the quoted ones; if it only says "відпустка" once, the Ukrainian searches return nothing. None of them ever returns or names the hidden "Щорічна відпустка 2027".
