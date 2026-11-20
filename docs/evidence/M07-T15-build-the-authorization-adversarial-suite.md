# M07-T15 — Build the authorization adversarial suite evidence

## Invariant

For every seeded scenario, every person, and every interface a reader can
reach (retrieval, the Evidence source, the answer stream, the Citation and
Evidence checks, Conversations, and History), the reader gets exactly the
Documents the plain-Python rule allows, and no marker or identifier of any
other Document appears in what they get, in the model's prompt, in the bytes
sent, in any log record, or in any audit row.

## Expected failing check

Before the change, `uv run pytest tests/test_authorization_suite_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_authorization_suite_integration.py`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- Whether a member of `/engineering/backend` reads a Document granted to `/engineering`: no; Keycloak groups do not inherit, and the token names only `/engineering/backend`.
- What a person gets from the Citation check when they cite another person's Document by its real IDs: the Citation is refused and the Answer is withdrawn, the same as citing a missing Chunk.
- What alice's History shows after a Document her earlier Answer cited is revoked: the Answer is withheld; none of its text or Citations are shown.
- How many suite tests fail if a group path also grants its parent group: 18 of 36 (every seed of the retrieval, answer, and direct-ID tests, because bob reads `/engineering` Documents).

## Design

- **The generator.** `tests/authorization_scenarios.py` is a plain module, not a test, so any later test can import it. `scenario(seed)` builds a reproducible company: alice (`/engineering`), bob (`/engineering/backend`), carol (one to three random groups), and dan (no groups). A fixed core is in every seed: Documents granted to `/engineering`, `/engineering/backend`, and the look-alike `/Engineering`, one each to alice and bob, one to nobody, a revoked and a deleted `/engineering` Document, and twin Documents with one body text (public, and private to bob). Random extras with random audiences and fates follow.
- **People from token claims.** `Person.context()` calls `build_authorization_context` with the claims a Keycloak token would carry, so group paths become Group IDs in production code; `ScenarioAuthenticator` hands the same context to the HTTP app.
- **The oracle.** `may_read` states the rule in plain Python: a kept Document, granted to everyone, to this person, or to a group path the token names exactly. Parent and child groups never count.
- **Markers and IDs.** Every Document's title holds a unique marker, and its text does too unless it is a twin. Before its real grants, each Document is published to a hidden snapshot reader, whose search captures its Document and Chunk IDs; then it gets its real readers and is revoked or tombstoned as its fate says.
- **A repeating model.** `CitingEverything` cites every Evidence it is shown and writes every marker in its prompt, so whatever reached the model reaches the client.
- **The doors.** Vector, lexical, and hybrid retrieval and the Evidence source, each at limit 20 and at limit 1 (checking `truncated`); the answer stream over HTTP for each person and both languages; the Citation and Evidence checks given every Document's real IDs; Conversations and History through the owner-scoped store; and a Document revoked or deleted while the model writes. Every test scans every field of every log record and every audit row for forbidden markers and IDs.

## Verification

Completed 2026-11-20.

- Expected failing check: before the change, the suite file did not exist.
- `uv run pytest tests/test_authorization_scenarios.py -q`: 54 passed. `uv run pytest -m integration tests/test_authorization_suite_integration.py -q`: 36 passed.
- Deliberate break (a group path also grants its parent, in `build_authorization_context`): 18 failed, 18 passed; restored: 36 passed, and `git diff src/` is empty. The prediction was right.
- Mutants: fifteen deliberate bugs in the application code were each run against the suite, and each was caught: any group matching, a user grant matching anyone, a tombstoned Document still readable, an unchecked Conversation owner, Citations or Evidence not rechecked, a deleted Document citable by ID, the owner's History not withholding, titles in log messages or log fields, History logging its text, group paths ignoring case, parent-group inheritance, and `truncated` counting the limit.
- Gates: `make format-check` (300 files), `make lint`, `make typecheck` (0 errors), `make docs-check`, `git diff --check` clean; `make test-unit` 2950 passed (+54); `make test-integration` 690 passed (+36) with PostgreSQL and Keycloak running.
- The guide was verified by applying it to a clean copy of HEAD: the result equals the implementation byte for byte, and the Ukrainian guide's code blocks equal the English ones.
- Privacy: no forbidden marker, Document ID, or Chunk ID appeared in any result, prompt, response byte, log record field, or audit row. No application code changed: the suite found no leak.
- Known limits: the fake embedding model gives every question the same vector, so only lexical search depends on the question's language; Conversations are tested at the store, since no Conversation endpoint exists yet; no agent tools or caches exist yet, and each joins the suite as a probe when it does.

## Review

- **Adversarial review of the prototype** (before the guide): seven new mutants all passed the first version. Deleted and revoked Documents were never tried by direct ID; the owner's own History was never checked after a revocation; only log messages were scanned, not their structured fields, and only in one test; the race test did not check that the stream ended properly; only markers were searched, never Document or Chunk IDs; `truncated` was never tested. Each is now caught: the snapshot reader captures every Document's IDs, History is checked after a revocation, every test scans every log field, the race test checks status, completion, and a `denied` audit row, forbidden IDs are searched, and each search also runs at limit 1.
- **Closing Standards review:** the answer-stream test cleared the captured logs on each pass but checked only the reader's forbidden markers, so a readable title logged on any pass but the last went unseen (blocking). Each pass now checks every marker. A mutant that logs cited titles only for readers with a group passed the old test (6 passed) and fails the new one (6 failed). Non-blocking cleanups applied: `ISSUER` imported instead of redefined, `LATER` moved up with the other constants, the `may_read` docstring reflowed, and `_cited` renamed `cited`. Left as suggestions: a shared subject helper, and renaming `Secret`.
- **Closing Spec review:** no blocking gaps; it reproduced the deliberate break (18 failed, plus one unit test, `test_a_child_groups_member_does_not_read_the_parents_documents`). Non-blocking observations: the race takes a Document away only after the prompt is built, not between retrieval and the `still_visible` recheck; the Conversation and race tests use the English probe only; the race always targets a group Document; the twins test title, marker, and IDs, not the shared text, which is correct.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **One generator for every door.** Separate small worlds per door let a rule broken at one door pass elsewhere; one scenario walked through every door checks that they all agree.
- **People from token claims.** Group paths become Group IDs inside `build_authorization_context`; building contexts by hand would skip the very place nested and look-alike paths are decided.
- **Positive checks.** "No forbidden marker" passes when a door returns nothing; requiring exactly the readable set makes an empty result a failure.
- **Mutants prove the suite.** A green suite only shows that nothing it looks at leaked; failing on each deliberate bug shows it looks at the right places.

**Falsified assumption.** I assumed that scanning `caplog.text` for markers covers the logs. The review's mutant that puts a title in a log record's `extra` field passed the first suite. So "the log text is the log" is wrong: the service's structured fields live on the record, and the suite must scan every field of every record.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. Anything inside the search: the SQL, indexes, query plans, or ranking, as long as each door returns the same set. The suite would pass on wrong code if the oracle shared the bug, for example if `may_read` also let a parent group in, or if no scenario held the case that breaks.
2. `test_every_retrieval_returns_exactly_what_each_person_may_read`, or the answer stream test: one person's cached result would be served to another person with the same Question, so their returned set would differ from `may_read`, even though each search was correct when it ran.
3. The words are public through the public twin, so seeing them proves nothing. dan must never see the private twin's title (its marker), Document ID, Chunk ID, or provenance, because those prove the private Document exists.
4. They all broke a place the suite did not look: an ID instead of a marker, a log field instead of the message, a fate never tried by ID, a limit never reached. A "zero leakage" test is only as wide as what it scans and which cases it builds.
5. A probe that calls the tool as each person with each probe Question and compares the Documents it returns with `scenario.readable(person)`, reusing the marker and ID scan over its output, the logs, and the audit rows, and the direct-ID test for a tool that takes an ID.
