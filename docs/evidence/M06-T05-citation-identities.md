# M06-T05 — Citation identities evidence

## Invariant

`resolve_citations` accepts only IDs of the request's own Evidence and
cites a repeated ID once, at its first position; `read_citations` still
refuses a repeat. `confirm_citations` returns any outcome other than
`ANSWERED` unchanged and without a query, and refuses with `TypeError`,
before any SQL, a result, outcome, or Citation that is not exactly what
generation builds. For an Answer, one statement selects the cited Chunks
under the retrieval permission check; every Citation must match its
stored Chunk, Document, Source, and content version, in an available
Document the reader may see. Otherwise the whole Answer is `WITHDRAWN`,
with no text, no reason, and no Citations, keeping its usage and context
report. A confirmed Citation is rebuilt from the stored rows, except its
Evidence ID. No log record, repr, or error holds a title, link, or text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.current_citations"`
fails with `ModuleNotFoundError`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What a model's reply that cites the same Evidence ID three times became in M06-T04, and what it becomes after this lesson: in M06-T04 `resolve_citations` refused the repeat, so the reply became `INVALID` with the defect `INVALID_CITATION` and the user lost an Answer that was already paid for; after this lesson it is `ANSWERED` with that Evidence cited once, at its first position.
- What the reader gets when a cited Document was renamed between retrieval and confirmation, and when access to it was revoked: after a rename the Answer passes unchanged except that the Citation shows the new title, because the Chunk and its version are the same; after a revoke the whole Answer is `WITHDRAWN`, with no text and no Citations, only its usage kept.
- How many tests fail if the permission check is left out of the confirmation statement, and which kinds of change they cover: 6 of 32; the statement test, revoked access, the tombstoned Document, another reader, the forged private Citation, and the log test. A new version and purged Chunks still pass, because their old Chunk IDs find nothing anyway.

## Design

- **Changed files.** New `src/knowledge_service/answering/current_citations.py`, `tests/test_current_citations.py`, and `tests/test_current_citations_integration.py`. Small edits: `resolve_citations` and `_known` in `answering/citations.py`, the public `EXCERPT_SOURCE_CHARACTERS` in `answering/evidence.py`, `AnswerOutcome.WITHDRAWN` in `answering/generation.py`, and the tests in `tests/test_citations.py` and `tests/test_generation.py` that expected a repeat to be refused. The `answering` row of `docs/architecture.md` describes both changes.
- **Repeats.** `resolve_citations` cites a repeated ID once, at its first position; `read_citations` still refuses a repeat with `DUPLICATE`. The duplicate check moved out of `_known` into each caller, because the two rules now differ.
- **What `confirm_citations` checks, in order.** The context is exactly an `AuthorizationContext`; the result is exactly an `AnswerResult` with a real `AnswerOutcome`. Any outcome other than `ANSWERED` must hold no Citations and is returned unchanged, without a query. An Answer must hold 1 to 20 exact `Citation` objects whose identifiers hold exactly a UUID, a `str`, or an `int`, each Evidence and Chunk once. Every failure here is a `TypeError` before any SQL.
- **The query.** `current_citations_statement` selects the cited Chunks by one array parameter, joined to their Documents, only where the Chunk's version is the Document's, under `eligible_chunks_predicate`, the same permission check retrieval uses. It reads only the first 4,000 characters of each Chunk's text, for the excerpt.
- **All or nothing.** A Citation passes only when its row exists and matches its Document, Source, and version. If one fails, the outcome becomes `WITHDRAWN`, with no text, reason, or Citations; usage and the context report are kept. Every cause gives the same result.
- **Rebuilt from storage.** A confirmed Citation keeps only its Evidence ID; identifiers, version, title, heading path, source link, and excerpt are rebuilt from the row and cleaned for display as Evidence is. One log record holds the request ID, counts, and the outcome.

## Verification

| Command | Result |
|---|---|
| `uv run pytest tests/test_current_citations.py tests/test_citations.py tests/test_generation.py -q` | 109 passed |
| `uv run pytest tests/test_current_citations_integration.py -m integration -q` | 13 passed |
| `make format-check` | 240 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors |
| `make test-unit` | 1909 passed |
| `make test-integration` | 342 passed |
| `make docs-check` | passed |

- **Expected failing check.** Before Step 3, importing `knowledge_service.answering.current_citations` failed with `ModuleNotFoundError`.
- **Deliberate break.** Removing the permission check from the statement failed 6 of 32 tests; restored, 32 passed. Details under Decision.
- **Known limits.** Nothing calls `confirm_citations` yet; M06-T09 will. A Document can still change between confirmation and delivery. The check confirms identity and permission, not whether a claim is supported (M06-T06).

## Review

An adversarial review found no path from real retrieval and generation to a Citation the reader may not see: revoked, rewritten, tombstoned, purged, restored, group and public grant changes, and another reader all gave `WITHDRAWN`, and a 3,000-case fuzz of the repeat merging always gave the first-seen unique subset. It found five ways a hand-built or forged result got through, each fixed with a test that failed before the fix: an outcome given as the plain text "answered" skipped the check; a result that was not an Answer kept its Citations unchecked; an identifier holding a lying UUID passed the comparison; a Chunk ID holding text crashed in the database with the text in the error message; a forged title or excerpt was kept.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **Repeats: merged from the model, refused when read back.** A repeated ID from the model names the same Evidence again; it cannot invent or relabel a source. Refusing it turned a paid, correct Answer into `INVALID`, and strict mode cannot forbid repeats in the schema, so `resolve_citations` now cites the first one and drops the rest. `read_citations` reads Citations the server stored itself, and the server never writes a repeat, so there a repeat means the data was changed, and it is still refused.
- **One failed Citation withdraws the whole Answer.** The text was written from the cited Evidence. Dropping only the failed Citation would still show what that Evidence said, now without a source, to a reader who may no longer see it. Withdrawing everything keeps the rule simple and safe; the usage and context report stay, so the cost is not lost.
- **Every failure looks the same.** A revoked grant, a deleted Chunk, a new version, and a tombstoned Document all give `WITHDRAWN` with nothing else. A client that could tell them apart would learn that a Document it may not see exists, or when it changed.
- **Rebuilt from storage, not copied.** Only the Evidence ID comes from the result. Identifiers, version, title, heading, link, and excerpt come from the stored rows, so a rename shows the new title and a forged field never reaches the client.
- **Left for later.** Calling `confirm_citations` from the streaming endpoint before any Citation is sent is M06-T09. Checking that each claim is supported by its Evidence is M06-T06. What the user is told about a withdrawn Answer is M06-T07 and M06-T13. A Document can still change between confirmation and delivery; no check closes that gap.
- **Deliberate break.** Removing `eligibility,` from `current_citations_statement` failed 6 of 32 tests: the statement test, revoked access, the tombstoned Document, another reader, the forged private Citation, and the log test. A new version and purged Chunks still passed, because their old Chunk IDs find nothing anyway. Restored; 32 passed.
- **Falsified assumption.** I assumed that checking the outer types, an `AnswerResult` holding `Citation` objects, was enough to trust the fields inside. The review shows that an identifier could hold a UUID subclass that equals anything, or a text that crashed the query and put itself in the error message, and that a forged title or excerpt was kept. So "the right outer type means the right content" is wrong: every identifier must hold exactly a UUID, and every shown field is rebuilt from storage.
- **Second falsified assumption.** I assumed a result that is not `ANSWERED` could be passed through untouched. The review shows an outcome given as the plain text "answered" skipped the check, and an abstention could carry unchecked Citations. So the outcome must be a real `AnswerOutcome`, and only an Answer may hold Citations.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. **What `confirm_citations` hides from M06-T09.** The streaming endpoint never has to check permissions again, compare versions, notice a tombstoned Document or purged Chunks, re-read titles after a rename, decide what to do when only some Citations fail, guard against forged result objects, or keep the cost of a withdrawn Answer. It calls one function in a short transaction and sends what comes back.
2. **Skipping the check for fast Answers.** A revoke or a deletion can commit at any moment, also in the first second, so a fast Answer can still cite a Document the reader just lost. The tests change the Documents after generation and before confirmation, regardless of time; `test_an_answer_citing_a_document_that_changed_is_withdrawn[access-revoked]` would fail if the check were skipped.
3. **Withdrawing all five.** Exactly right. The text mixes what all five Evidence said, and the server cannot tell which sentence came from the failed one. Showing the other four would still show the hidden one's content. The user loses one Answer and can ask again; retrieval will then use only what they may see.
4. **Why "withdrawn" says nothing more.** If the client could tell "deleted" from "hidden from you", it could learn that a Document it may not see exists, and when its permissions changed. One result for every cause leaks nothing.
5. **The next forged result.** An `AnswerResult` whose `context` or `usage` is a hand-built object with text in it, to see whether anything in the withdrawn result or the log could carry that text, since those two fields are passed through unchecked.
