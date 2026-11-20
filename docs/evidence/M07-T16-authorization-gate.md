# M07-T16 — Authorization gate evidence

## Invariant

Every `/v1` operation decides who the caller is from a verified token
alone, checks that one of the caller's Application Roles permits the
operation before reading any input, and touches only the records the
caller may: Chunks through Access Grants checked inside PostgreSQL, and
Conversations through the owner in the SQL. A request that fails any of
these checks changes nothing, and its answer, logs, and audit rows never
reveal whether another User's record exists.

## Expected failing check

Before the change, `uv run pytest tests/test_authorization_gate.py -q`
fails with `ERROR: file or directory not found: tests/test_authorization_gate.py`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What bob gets when he deletes alice's Conversation by its real ID, and what changes in the database: 204 with no body, the same as for a missing ID; alice's Conversation stays, no purge Job is queued, and one `conversation.deleted` row records `denied` with bob as the actor.
- What a content manager gets when deleting with a malformed ID: 403 `forbidden`, because the role is checked before the ID is parsed.
- Which person reads a Document they may not after the injected change, and why only some seeds fail: carol, whose token holds `/Engineering`; only seeds where she has an upper-case path (4 and 5) can show it.
- How many tests in `tests/test_authorization_gate_integration.py` fail if `delete_conversation` forgets the owner: 1, the stranger's delete.

## Identity, roles, and grants

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

| | Identity | Application Role | Access Grant |
|---|---|---|---|
| Question it answers | Who is asking? | May they run this operation at all? | Which records may they touch? |
| Where the answer comes from | The verified token's `sub` and `groups` claims | The token's realm roles, mapped through `PERMISSIONS` in code | The Source's own permissions, written by synchronization; for a Conversation, its owner |
| Where it is checked, and when | `Authenticator.authenticate` (`identity/context.py`), first: `OidcVerifier` checks the token, then `build_authorization_context` makes User and Group IDs, and `application_roles` reads the roles apart from them | `require` (`roles.py`), called by `admit` and by the delete route in `app.py` right after authentication and before any body or ID is read | Inside PostgreSQL: `eligible_chunks_predicate` in every search and Citation recheck, and `_live` or the owner condition in every Conversation statement, in the same query that reads the rows |
| What a failing request gets | 401 (or 400 for a malformed header, 503 if keys cannot be checked); nothing runs | 403 `forbidden`; no body, ID, search, or store call | No error at all: the record is simply not there, so the answer is the same as for a missing one (an abstention, or the delete's 204) |

**Example.** A user with the `administrator` and `user` roles, in no group, asks about a Document granted to `/engineering`. Identity: the token is valid, so the reader is that User with no Group IDs. Role: `user` permits `ask_questions`, so the request goes on; `administrator` adds `manage_policy` and `manage_operational_configuration`, neither of which reads anything. Grant: the predicate needs a grant to everyone, to this User, or to one of their Groups; there is none, so the Document's Chunks never leave PostgreSQL, and the answer is the same abstention as for an empty search. `test_an_administrator_reads_exactly_what_the_same_user_reads` shows the reader is identical with or without `administrator`.

## Trust zones

The old diagram in `docs/security.md` had the right boxes but no checks and no labels. The new one draws the zones as subgraphs, numbers the three checks in the order a request meets them (identity and role at the API, grants inside PostgreSQL), and labels each boundary with what crosses it: the bearer token, the signed claims and public keys from the identity provider, the caller's User and Group IDs to PostgreSQL, the Question and eligible Evidence under opaque IDs to the model providers, replies as untrusted data, and Access Grants with Chunks written by workers in one transaction. Workers carry no number because they never answer a User. Three labels were corrected after the review (see Review).

## Injected leak

- **Symptom.** `tests/test_authorization_context.py`: 2 failed, 87 passed (`test_plain_paths_in_any_script_are_kept` for `/Data Science` and the Cyrillic path). `tests/test_authorization_scenarios.py`: 54 passed, so the generator's own tests miss it. `tests/test_authorization_suite_integration.py`: 6 failed, 30 passed: the retrieval, answer-stream, and direct-ID tests, for seeds 4 and 5 only. The first message reads `vector/en/carol/seed 4` with `{'mark0s108177', 'twin8s4'} == {'mark2s094476', 'twin8s4'}`.
- **Who and what.** carol. In seed 4 her only group is `/Engineering`; she gets `mark0` (the `/engineering` Document) and loses `mark2` (the `/Engineering` one). In seed 5 she also holds `/Engineering`.
- **Cause.** `_group_paths` in `identity/context.py` now adds `path.casefold()` instead of `path`, so the token's paths are lower-cased while Access Grants keep the Source's spelling.
- **Chain.** `groups` claim `/Engineering` → `_group_paths` gives `/engineering` → `group_id_for` gives the `/engineering` Group ID → `eligible_chunks_predicate` finds the `/engineering` grant → the Chunk is ranked, cited, and confirmed.
- **Why only some seeds.** The leak needs a person whose token holds a path with an upper-case letter that differs from another group's path only by case. alice and bob are always lower-case; carol has `/Engineering` only in seeds 4 and 5 of the six.
- **Undone.** `git checkout -- src/knowledge_service/identity/context.py`; the suite gave 36 passed again.

## New operation

Implemented by Claude from the guide's reference at the learner's request, so there is no independent design to compare yet; the learner should sketch their own design from the brief and note the differences here.

## Design

- **Route.** `DELETE /v1/conversations/{conversation_id}` in `app.py`: `authenticate`, then `require(caller.roles, Operation.MANAGE_OWN_CONVERSATIONS)`, then the ID. The ID is a plain path string, accepted only in the canonical `8-4-4-4-12` hex spelling (`CANONICAL_ID`) and parsed with `ConversationId.parse`; anything else is 422 `validation_error`. `delete_conversation` raises `TypeError` for a request ID that is not a `UUID`, before any SQL. Without a store the route answers 503 `conversations_unavailable`; a store failure goes through `classify_failure` (503 with `Retry-After` for an unreachable database, 500 otherwise) and logs only the error's type.
- **The port.** `ConversationStore` in `app.py`, implemented by `DatabaseConversationStore` (`answering/conversation_store.py`, beside `DatabaseAuditTrail`, as ADR 0013 places a module in the package it serves): one transaction per call, the owner always `caller.reader.user_id`, and `delete` returns `None`, so the HTTP layer cannot tell a deletion from nothing to delete. The response is 204 with no body either way.
- **Storage.** `delete_conversation` keeps the owner in its `UPDATE`, queues the purge Job only for a real deletion, and now records one `conversation.deleted` audit event in the same transaction: `allowed`/`succeeded` or `denied`/`failed`, with the caller as actor and the request ID. If the audit insert fails, the transaction rolls back and nothing is deleted.
- **Route registry.** `tests/test_app_authentication.py` lists the route with its operation, so the role matrix test checks it against all 16 role sets.

## Verification

Completed 2026-11-20.

- Expected failing check: before the change, `tests/test_authorization_gate.py` did not exist; adding the route alone made `test_every_route_the_app_serves_is_listed_with_its_operation` fail until the route was listed.
- `uv run pytest tests/test_authorization_gate.py tests/test_app_authentication.py -q`: 110 passed. `uv run pytest -m integration tests/test_authorization_gate_integration.py -q`: 5 passed.
- Injected leak: as under "Injected leak"; undone, 36 passed.
- Deliberate break (owner condition removed from `delete_conversation`): 1 failed (`test_a_strangers_delete_reads_like_a_missing_one_and_changes_nothing`), 4 passed; restored, 5 passed. The response stayed 204, so only the database check caught it.
- Gates: `make format-check` (303 files), `make lint`, `make typecheck` (0 errors), `make docs-check`, `git diff --check` clean; `make test-unit` 2986 passed (+36); `make test-integration` 695 passed (+5) with PostgreSQL and Keycloak running. Before the closing fixes, one `make test-unit` run of five failed `tests/test_support.py::test_splitting_a_long_run_of_dots_takes_linear_time`, a timing test this change does not touch, on a run that took 42 s instead of about 25 s; the other four runs passed.
- Fail-closed: no token (401), no role (403, also for a malformed ID), a malformed ID (422), an unreachable database (503), and a refused audit insert (500) all leave the store untouched or the Conversation intact, with no purge Job.
- Privacy: a marker in alice's Question and reply, and in a store error's message, appears in no response, log record field, or audit row.
- The guide was verified by applying it to a clean copy of HEAD: the result equals the implementation byte for byte, and the Ukrainian guide's 48 code blocks equal the English ones.
- Known limits: only deleting has a route; reading a Conversation or its History has none. A 403 or 422 writes no audit event. A real deletion also inserts a purge Job, so it takes slightly longer than a stranger's. Any User can write one `denied` audit row per request until rate limits (M09-T10), and the row's request ID is the one the client sent. The purge itself runs in M08.

## Review

- **Adversarial review of the prototype** (before the guide): hostile IDs, headers, bodies, and fifteen mutants; every guarantee held and every mutant failed at least one test. Fixed, each with a test that fails without the fix: `UUID()` accepted braces, `urn:uuid:`, no dashes, a `+` sign, and Arabic-Indic digits (now only the canonical spelling); a store that reported "not found" with a 404 passed every unit test (now `test_the_answer_cannot_tell_a_deletion_from_nothing_to_delete`); a failed deletion logged nothing (now its type); three diagram and threat-model labels were inaccurate about what crosses a boundary.
- **Closing Standards review:** one blocking finding: the store adapter was a new top-level module, which ADR 0013 forbids (a new module goes into the package it serves); moved to `answering/conversation_store.py`, beside `DatabaseAuditTrail` and History. Non-blocking, all applied: the canonical-ID check returns 422 directly instead of raising to catch; the store's request-ID guard raises `TypeError`, like `rate_message`, with `test_the_store_refuses_a_request_id_that_is_not_a_uuid`; the trigger test drops with `IF EXISTS` and says why a real trigger is used; the store's docstring and the diagram's owner label say exactly what is true today.
- **Closing Spec review:** no blocking gaps; it reproduced the injected leak (2 failed, 6 failed) and the deliberate break. Non-blocking, left as they are: the integration tests build callers with a fake authenticator (the unit tests cover the real `Authenticator`); the diagram's owner label holds only while no route reaches `rate_message`; nothing consumes `purge_conversation` Jobs until M08; the audit-trigger test does not scan logs (the unit test covers that path).

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **204 for every ID.** A delete has nothing to return, and any difference between "deleted" and "not yours" is an existence oracle. Operators still see the difference, in the audit trail.
- **The ID after the role.** FastAPI would validate a typed `UUID` path before the handler, so a caller with no token or role would learn the ID was malformed; parsing by hand after `require` keeps the order: who, whether, which.
- **The audit event in the deletion's transaction.** A deletion that could commit without its record would leave the trail incomplete exactly when something went wrong; in one transaction, both happen or neither does.
- **No grant for Conversations.** A Conversation is its User's own record, not Source content, so ownership in the SQL plays the part grants play for Chunks.

**Falsified assumption.** I assumed that `ConversationId.parse`, which wraps `uuid.UUID`, accepts only well-formed IDs. The adversarial review shows it also reads `{...}`, `urn:uuid:`, a `+` sign, and non-ASCII digits as the same UUID. So "the domain parser defines the API's ID format" is wrong: the HTTP boundary must state its own canonical spelling.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. It would mix up roles and grants: a role would start answering "which records", which only grants answer. An administrator would see every Document, including payroll and private ones no Source granted them, and every Answer they get would cite them.
2. Not between the two sides, since both would map `/Engineering` and `/engineering` to one Group ID; but two different groups would merge, so members of either would read both. Deciding needs to know whether the identity provider treats names that differ only by case as the same group; Keycloak does not, so exact comparison is right.
3. The client cannot tell a typo from success, or show "already deleted". That is worth it because the alternative lets any User probe which Conversation IDs exist, and the client knows which IDs it has anyway.
4. Operators with access to `audit_events`, not Users. It shows that bob tried to delete a Conversation that belongs to alice, with the time and request, which the 204 hides from bob.
5. Copy the route listing, the role-before-ID order, the owner-from-token tests, and the stranger-reads-like-missing test. Reading adds content: the Answers in it cite Documents, so it must withhold Answers whose Citations the reader may no longer see, as `load_history` does, and keep the text out of logs.
