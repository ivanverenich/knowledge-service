# M07-T09 — Authorize Document fetches evidence

## Invariant

The answer stream reads Documents again as the request's reader before the
model sees any Evidence and before the client gets any Citation, through
`eligible_chunks_predicate` and each Chunk's current version. Evidence the
reader may not see now makes the stream end like an empty search, so the
model never sees it; a Citation that fails withdraws the Answer, which ends
with the same decision and message as an empty search and sends no
Citation. A valid ID of another User's Chunk never reaches the client as
text, title, link, or Citation, and the log holds counts only.

## Expected failing check

Before the change, `uv run pytest tests/test_citation_confirmation_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_citation_confirmation_integration.py`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- Which events alice's client receives when a retrieval bug hands her stream a real Chunk of bob's, and whether the model is called: only `metadata` and a `completion` with the empty-search abstention (`no_evidence`); `still_visible` says no before the model, so the model is never called and no text, title, or Citation of bob's reaches her.
- What alice's client receives if her grant is revoked while the model writes, and whether the text already streamed is taken back: the text deltas streamed so far, then `checking`, `usage`, and a `completion` with the empty-search abstention and no Citation; `confirm` withdraws the Answer, which replaces the text in the client's final answer, but the bytes already sent cannot be taken back.
- What the stream does when the check returns the Answer citing the same Evidence but under another Document: ends with an `error` event and no Citation: `_confirms` compares each Citation's Evidence, Chunk, Document, Source, and version, so a check cannot substitute other provenance identities.
- Which tests fail if the stream ignores a "no" from `still_visible`: 2, with 64 passing: `test_evidence_the_reader_may_not_see_never_reaches_the_model` and `test_another_users_chunk_is_never_cited_to_the_reader`, where the model would stream bob's text before the final check withdraws the Citation.

## Design

- **Two rereads, one protocol.** `CitationCheck` in `answering/streaming.py` has `still_visible(evidence, reader)` and `confirm(result, reader)`. `CurrentCitations` in `answering/current_citations.py` implements both over PostgreSQL, each in its own short transaction, so no connection is held while the model writes. `AnswerStreamer` requires one (`citations=`, no default) and keeps the reader in `_Plan`.
- **Before the model.** In `open`, after the source and before `assess_evidence`, `still_visible` runs under the deadline. `evidence_still_visible` rereads every Evidence's Chunk with `current_citations_statement` under `eligible_chunks_predicate` and requires the same Document, Source, and version (`_same`); it logs counts only. Anything but `True` or `False` is refused before any byte; `False` turns the Evidence into an empty set, so the stream abstains like an empty search and the model is never called.
- **Before the Citations.** In `_finished`, after `reply_result` and the streamed-text check, `confirm` runs `confirm_citations` (M06-T05) under the deadline: one failed Citation withdraws the Answer, which `decide` turns into the `no_evidence` abstention, and confirmed Citations carry the provenance read then.
- **The stream checks the check.** `_confirms` accepts a non-answer only as the same object, a withdrawal only with no text and no Citations, and an Answer only with the same text and, Citation by Citation, the same Evidence, Chunk, Document, Source, and version (`_identity`), as a tuple of exact `Citation`s.

## Verification

- Expected failing check: before the change, `uv run pytest tests/test_citation_confirmation_integration.py -q` failed with `ERROR: file or directory not found: tests/test_citation_confirmation_integration.py`.
- After Step 3, `uv run pyright src` reported 0 errors and the stream tests failed with `TypeError: AnswerStreamer.__init__() missing 1 required keyword-only argument: 'citations'`.
- `uv run pytest tests/test_streaming.py tests/test_stream_failures.py tests/test_app.py tests/test_app_authentication.py -q`: 190 passed, including the hidden-Evidence, yes-or-no, withdrawal, provenance, twelve wrong-reply, copied-abstention, slow-check, and failing-check tests.
- `uv run pytest -m integration tests/test_citation_confirmation_integration.py -q`: 4 passed over HTTP with the real `CurrentCitations`: a visible Citation with its provenance; bob's real Chunk handed to alice's stream, where the model is never called and the raw bytes hold no `SECRET`; a grant revoked while the model writes, which withdraws the Answer; and withdrawn and empty ending with the same decision and message.
- Gates: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2784 passed (+22); `make test-integration` 625 passed (+4); `make docs-check` passed.
- Deliberate break: without the `if not visible:` branch in `open`, 2 failed, 64 passed: `test_evidence_the_reader_may_not_see_never_reaches_the_model` and `test_another_users_chunk_is_never_cited_to_the_reader`, where the final check still withdrew bob's Citation but the model had already streamed bob's text. Restored: 66 passed. The prediction was right.
- Known limits: no endpoint reads a whole Document, an excerpt, or an export, and agent tools come in M11-T01; unscoped readers such as `load_document_by_id`, `load_chunks`, `load_conversation`, `load_messages`, `rate_message`, and `load_message_feedback` are not reachable from a request today but must be scoped when they get endpoints; text streamed before a mid-answer revocation cannot be unsent; nothing forces the check to be `CurrentCitations`; no production entry point builds the stream.

## Review

An independent adversarial review tried crafted check replies, subclasses, other provenance, a check slower than the deadline (the connection was released and the stream ended with `dependency_unavailable`), DEBUG logs, a survey of every read by ID, and 17 mutations of the wiring. The rereads themselves held. It found these problems, fixed with tests that failed before:

- The first version checked only after the model. With bob's Chunk handed to alice's stream, the Citation was withdrawn but the model's text quoting it had already been sent, and the test read only the rebuilt answer. Now `still_visible` runs before the model (`test_evidence_the_reader_may_not_see_never_reaches_the_model`), and the integration test checks the raw bytes (`test_another_users_chunk_is_never_cited_to_the_reader`).
- The documents claimed withdrawn and empty "read the same"; on the wire a withdrawn answer has text and `checking` events. Now they claim the same decision and message (`test_withdrawn_and_empty_end_with_the_same_decision_and_message`), and the threat model says streamed text cannot be unsent.
- `_confirms` accepted another Document, Source, or version under the same Evidence, and a list of Citations. Now it compares full identities (`test_a_check_that_answers_another_result_ends_with_an_error[another_document]`, `[another_chunk]`, `[citations_as_list]`).
- Mutations survived for lack of an abstention returned as a copy, an Answer returned as an abstention, withdrawn replies keeping text or Citations, and a slow check; each now has a test. Accepting an `AnswerResult` subclass still passes `_confirms` but fails in `decide`, which accepts only the exact type.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **A check before the model as well as after.** The stream sends text as the model writes it, so the only way to keep another User's text from the client is to keep it from the model. The check after the model is still needed for what changes while the model writes.
- **Withdrawn ends like empty.** A distinct message would tell the reader that a Document exists and was taken away from them, which is itself a leak; the same decision and message reveal nothing about why.
- **The stream checks the check.** The check is trusted code, but it decides what reaches the client; refusing anything but a yes or no, a withdrawal, or the same Answer with fresh provenance keeps a bug in it from sending other Documents' titles or links.
- **Left for later.** Faster revocation (M07-T10), existence leaks (M07-T11), and agent tools that read with the reader (M11-T01).

**Falsified assumption.** I assumed that rereading the Citations at the end protected the client from another User's Chunk. The review shows that the stream had already sent the model's text about that Chunk before the check ran, and my test missed it because it read only the rebuilt answer. So "a check before the Citations is a check before the client" is wrong for a stream; the Evidence has to be checked before the model sees it.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. It no longer knows which tables hold grants, how versions are compared, or how to build the permission predicate; it asks two questions with the reader and gets a yes or no, or an Answer with fresh provenance or withdrawn.
2. Alice would receive the model's text quoting bob's Chunk, "The SECRET-bob.txt policy.", as text deltas, and then an abstention; the final check would withdraw the Citation, but her client already holds the words, exactly as the deliberate break showed.
3. She has received the text written so far, from Evidence she could see when the model was asked; at the end she receives `checking`, the usage, and the empty-search abstention with no Citation. The bytes already left the server and sit on her machine, so no server can recall them; only not sending them in the first place, by waiting for the whole answer, would avoid it, at the cost of streaming.
4. "This document was withdrawn" tells the reader that a Document they may no longer see exists and touched their question, which is an existence leak; the same message as an empty search tells them nothing beyond "no answer".
5. First, read the Conversation only if it belongs to the requesting User; then read every cited Chunk through `eligible_chunks_predicate` with that User's current Authorization Context and its current version, returning nothing for any that fail, before a single byte of the export is written.
