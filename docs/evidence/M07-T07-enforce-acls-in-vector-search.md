# M07-T07 — Enforce ACLs in vector search evidence

## Invariant

A vector search returns only Chunks whose Document is available and granted
to everyone, to the reader's User, or to one of the reader's Groups, decided
in the same SQL statement that orders by distance and applies the limit; a
Chunk the reader may not see is never ranked, counted, or returned, however
near it is. The check accepts only an exact, unchanged `AuthorizationContext`
and ties each Chunk to its own Document. The answer stream's Evidence comes
from that search as the request's reader, and no hidden text reaches the
model's prompt, the client, or a log.

## Expected failing check

Before the change, `uv run pytest tests/test_vector_evidence_integration.py -q`
fails with `ModuleNotFoundError: No module named 'knowledge_service.answering.vector_evidence'`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What `VectorEvidenceSource` with limit 1 returns to a reader in `/engineering` when a Chunk granted only to another user is an exact match and the engineering Chunk is farther away: one Evidence, the engineering Chunk, with `eligible` 1: the exact match is dropped inside the SQL before ordering and the limit, so it never takes the one slot.
- What `eligible_chunks_predicate` does with a real context whose `group_ids` was replaced afterwards with a plain `set`: raises `InvalidAuthorizationContext` through `checked_reader` before any SQL is built, because the fields are checked again, not only the type.
- What `find` does when the embedding model replies with another model's identity: raises `InvalidEmbedding` after the embedding call and before any search, so nothing is compared against vectors of another model.
- Which tests fail if `eligibility` is removed from the WHERE of `vector_search_statement`: 33, with 11 passing: all 30 seeds of `test_only_eligible_chunks_come_back_however_near_the_others_are`, plus `test_the_reader_gets_only_evidence_it_may_see`, `test_a_reader_with_no_grant_finds_nothing`, and `test_a_hidden_exact_match_never_reaches_the_model_or_the_client`.

## Design

- **Where the check sits.** `vector_search_statement` (M04-T06) already puts `eligible_chunks_predicate(context)` in the same WHERE as the current-space check and the NaN guard, before `ORDER BY distance, chunk_id` and `LIMIT k + 1`; the query itself is unchanged. The gap was that no Evidence source over the database passed the request's reader to it.
- **`checked_reader`.** `src/knowledge_service/access.py` moves the field checks of `AuthorizationContext` into `_check_reader_fields` and adds `checked_reader`, which accepts only an exact `AuthorizationContext` and checks its fields again, so a subclass, a look-alike, or a context changed with `object.__setattr__` is refused with `InvalidAuthorizationContext`.
- **A predicate that stands alone.** `eligible_chunks_predicate` calls `checked_reader` first and includes `documents.document_id = chunks.document_id`, so a statement that forgets the join narrows nothing instead of pairing every Chunk with every visible Document. All existing searches already join on those columns.
- **`VectorEvidenceSource`.** `src/knowledge_service/answering/vector_evidence.py` implements the answer stream's `EvidenceSource`: `checked_reader` before any embedding (a failure becomes `InvalidAnswerRequest`), one `embed` call with the configured timeout, exactly one vector of the model's own identity (else `InvalidEmbedding`), `search_similar_chunks` with the reader and a limit of 1 to 20, then `build_evidence`. Its signals carry the number of Chunks found and no score.

## Verification

- Expected failing check: before the change, `uv run pytest tests/test_vector_evidence_integration.py -q` failed with `ModuleNotFoundError: No module named 'knowledge_service.answering.vector_evidence'`.
- `uv run pytest tests/test_authorization_context.py -q`: 89 passed after Step 2, unchanged. The existing vector, lexical, and current-citation integration tests: 78 passed after Step 3, unchanged.
- `uv run pytest tests/test_eligibility_predicate.py -q`: 8 passed. `uv run pytest -m "integration or not integration" tests/test_vector_evidence_integration.py tests/test_vector_acl_adversarial_integration.py -q`: 53 passed against PostgreSQL, including 30 seeded corpora checked against the `may_read` reference model for four readers, and a hidden exact match that never reaches the model's prompt or the HTTP response.
- Gates: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2750 passed (+17); `make test-integration` 471 passed (+44); `make docs-check` passed.
- Deliberate break: without `eligibility` in the WHERE of `vector_search_statement`, 33 failed, 11 passed: all 30 seeds of `test_only_eligible_chunks_come_back_however_near_the_others_are`, `test_the_reader_gets_only_evidence_it_may_see`, `test_a_reader_with_no_grant_finds_nothing`, and `test_a_hidden_exact_match_never_reaches_the_model_or_the_client`, where the hidden text entered the model's prompt. Restored: 44 passed. The prediction was right.
- Known limits: `dense_branch` in hybrid retrieval still trusts the model's reply; vector and lexical queries do not require a Chunk's content version to match its Document's (a freshness gap, not a permission one); no test covers the current Embedding Space switching mid-query; no production entry point assembles the app with this source; no reranker, so a score-threshold abstention policy refuses every request.

## Review

An independent adversarial review tried kind confusion between user and group IDs, duplicate grants, metadata filters naming hidden Documents, tombstoned Documents, non-current spaces, 20,000 groups, ties at the limit, 60 concurrent readers on one runtime, `EXPLAIN` of the plan, and full root, SQLAlchemy, and psycopg logging at DEBUG with a planted marker, plus 30 code mutations. Nothing hidden came back, showed in `truncated` or a count, or reached a log; 116 of 120 seed and reader pairs were hard. It made these remarks, fixed with tests that failed before:

- A context changed after it was made (`group_ids` replaced with a plain set of other Groups) was not checked again and widened the search. Now `checked_reader` checks the fields (`test_anything_but_an_authorization_context_is_refused[groups-set]`, `[user-as-group]`, `[group-as-user]`).
- The predicate named `documents` without linking it to `chunks`, so a statement that forgot the join would return every Chunk once any Document was visible. Now the link is inside it (`test_the_predicate_ties_each_chunk_to_its_own_document`, `test_a_query_that_forgets_to_join_documents_still_sees_only_eligible_chunks`).
- `VectorEvidenceSource` trusted the model's reply: another model's identity, no vector, two vectors. Now it requires one vector of its own (`test_a_model_reply_that_is_not_one_vector_of_its_own_is_refused`).
- Two mutations survived: a subclass reader passing the source's own check and costing an embedding call, and the timeout never reaching the model. Each now has a test (`test_a_reader_that_is_not_a_context_is_refused_before_embedding[subclass]`, `test_the_question_is_embedded_with_the_configured_timeout`).

Documented, not fixed: the `dense_branch` reply check, the content-version freshness gap, the space-switch race, and the score-threshold trap.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **Check the reader in the predicate.** Every search turns a reader into SQL through `eligible_chunks_predicate`, so checking there covers vector, lexical, sparse, history, and citation reads at once, and catches a reader that was faked or changed after the token check.
- **The predicate joins by itself.** A permission check that is only correct inside a correctly joined query is a trap for the next query someone writes. Adding the link costs nothing for existing queries and turns a forgotten join from "everything" into "only what is allowed".
- **Seeded corpora and a reference model.** Hand-picked cases cover what someone thought of; generated corpora with forbidden Chunks on the query, checked against an independent statement of the rule, cover combinations nobody listed, and a seed reproduces any failure exactly.
- **Left for later.** The same contract for lexical search (M07-T08), reading Documents by ID (M07-T09), and existence leaks (M07-T11).

**Falsified assumption.** I assumed that a reader checked when it was built stays sound, because `AuthorizationContext` is frozen. The review shows that `object.__setattr__` can replace `group_ids` afterwards with Groups that were never checked, and the search then returned a Document granted to them. So "frozen means checked forever" is wrong; the check has to run where the reader becomes SQL.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. It no longer knows that the Question must be embedded, which model and timeout to use, that only the current Embedding Space is searched, how a connection is opened, or how permissions become SQL; it passes text and a reader and gets Evidence and a count back.
2. With an index read first and the permission check after it, the 40 nearest vectors could all belong to Documents the reader may not see, so the reader would get few or no results even though allowed ones exist, and `truncated` would be wrong. The adversarial corpora would notice: with forbidden Chunks on the query, `test_only_eligible_chunks_come_back_however_near_the_others_are` expects every eligible Chunk back.
3. The token check builds a sound reader once; it cannot see what happens to that object later, or whether some other code builds a subclass or a look-alike. `checked_reader` runs at the moment the reader becomes SQL, so it also catches a context changed afterwards or one that never went through the token check.
4. Mistakes that need more than three dimensions, many Chunks per Document, metadata filters, or more than 12 Documents, such as a bug that appears only past the `limit + 1` row or with several Chunks per Document. I would add multi-Chunk Documents, random filters, and limits near the eligible count to the generator.
5. Alice gets the engineering Chunk, because bob's exact match is never ranked for her. Bob gets his exact match, because it is granted to him and it is nearest. Carol gets nothing: no grant names her or a group she is in, so `find` returns no Evidence and `eligible` 0.
