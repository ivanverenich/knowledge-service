# M06-T11 — Construct bounded History evidence

## Invariant

`load_history` returns None for a Conversation that is not the reader's
live Conversation. Otherwise it returns the most recent stretch of turns
that fits `budget_tokens`: whole turns first, then older turns as their
Questions alone, with no gaps and never over the budget. An Answer's text
is included only when it was `answered` and every Chunk it and every
earlier Answer cited is current, in the same Document and version, and
visible to the reader now under `eligible_chunks_predicate`; otherwise it
is withheld and its Question stays. Each kept Answer carries the Chunks
it cited. `add_turn` stores a Question and its reply together, so a reply
always follows its own Question. Records and reprs hold no Question or
Answer text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.history"`
fails with `ModuleNotFoundError`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What History holds for a Conversation of 50 long turns with a budget for only a few: the newest turns whole, as many as fit (3 in the test), then the turns just before them as their Questions alone (5 in the test), and nothing of the 42 oldest; never more tokens than the budget, and no gap between what is kept.
- What happens to an earlier Answer, and to the Answers after it, when the reader loses access to a Document the earlier Answer cited: the earlier Answer's text and references are withheld, and so is every later Answer, because each was written with the earlier one in its History and may repeat it; all their Questions stay.
- How many integration tests fail if `load_history` stops checking whether the reader may see each cited Chunk, and why not all of the "changed Document" cases: 2 of 9, the access-revoked and tombstoned cases; the new-version case still passes, because a rewritten Document gives its Chunks new IDs, so the version check alone withholds the old Answer.

## Design

- **Migration `a9b0c1d2e3f4`.** Adds a nullable `messages.outcome` (`answered`, `abstained`, `failed`), allowed only on an assistant Message by `ck_messages_outcome`, and a `message_references` table: one row per cited Chunk, keyed by Message and position, with the Chunk, its Document, and the content version read. It cascades with its Message and has no foreign key to `chunks`, so a reference outlives a deleted Chunk. Old rows keep a NULL outcome and are withheld.
- **Domain.** `MessageOutcome` and `MessageReference` in `conversations.py`. A `Message` checks its outcome and references together: only an assistant Message has an outcome, and only an `ANSWERED` one cites Chunks, 1 to `MAX_MESSAGE_REFERENCES` (20), each once. A version above `MAX_STORED_VERSION` (2^31 - 1) is refused before SQL.
- **Store.** `add_message` validates a draft Message first, then writes it and its references in one transaction; `read_conversation` reads them back. `add_turn` stores a Question and its reply under one savepoint, so a reply always follows its own Question and a turn that does not fit leaves nothing behind.
- **History.** `load_history` reads the Conversation through `read_conversation` (None unless it is the reader's live Conversation), then asks once which cited Chunks are still in their Document's current version and pass `eligible_chunks_predicate` for this reader. `build_history` pairs each reply with the Question right before it; shows an Answer only when it was answered and it and every earlier answered Answer cite only current Chunks; then fits the newest turns whole, then older turns as Questions, then leaves the rest out.
- **Token count.** UTF-8 bytes plus `TEXT_FRAMING_TOKENS` (8) per text, an upper bound for byte-level tokenizers, the same rule as the context budget.

## Verification

- `uv run python -c "import knowledge_service.answering.history"`: failed with `ModuleNotFoundError` before, imports now.
- `uv run pytest tests/test_history.py -q`: 25 passed. `pytest -m integration tests/test_history_integration.py`: 9 passed.
- Gates: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2221 passed (+25); `make test-integration` 370 passed (+9); `make docs-check` passed. `alembic heads`: `a9b0c1d2e3f4 (head)`.
- Deliberate break: `eligibility=sa.true()` in `load_history` gave 2 failed (access-revoked, tombstoned), 7 passed; restored, 9 passed.
- Known limits: nothing calls `load_history` or `add_turn` until M06-T12; withholding is conservative, because which earlier Answers each Answer saw is not stored; READ COMMITTED is required, as in M06-T10; rows written past the store can make a Conversation unreadable.

## Review

An adversarial review attacked the prototype with forged references, wrong versions, purged Chunks, revoked Groups, and legacy rows: all were withheld, and History was never over budget. It found four problems:

- A later Answer that repeated a revoked one still reached History. Now withholding carries forward to every later Answer (`test_a_later_answer_that_may_repeat_a_withheld_one_is_withheld_too`).
- Turns stored at once interleaved, so a reply paired with the wrong Question. Now `add_turn` stores both together and a reply pairs only with the Question right before it (`test_turns_stored_at_once_never_interleave`).
- A content version past the integer range failed with an untyped database error. Now `MessageReference` refuses it (`test_a_version_too_large_to_store_is_refused`).
- A row written past the store, such as an unknown outcome, makes its Conversation unreadable with a typed error. Documented; only the store writes Messages.

The unit regressions failed against the first version; the integration regressions need `add_turn`, which the first version did not have.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **Permissions now, not when written.** An old Answer is a copy of what its Chunks said. If the reader has since lost access, or the Document was deleted or rewritten, sending that copy to the model again would leak what the access check now forbids. So every cited Chunk is checked against `eligible_chunks_predicate` and the Document's current version at the moment History is built.
- **A later Answer goes with an earlier one.** Each Answer was written with the earlier Answers in its History, so it may repeat them ("as said, the budget is 2M"). We do not record which earlier Answers each one saw, so the safe rule is: once an Answer is withheld for a stale reference, every later Answer is withheld too. A failed or abstained Answer holds no knowledge and does not taint what follows.
- **Older turns as their Questions.** A follow-up such as "and for contractors?" most needs the subject it continues, and the User's own Questions name it in a few tokens. Answers are long and are the part that may go stale, so they are the first to drop.
- **No foreign key from a reference to `chunks`.** A Chunk is deleted when its Document is rewritten or tombstoned. A foreign key would either block that, or cascade and silently erase the reference, so History could no longer tell the Answer relied on something that is gone. Without it, the reference survives and simply finds no current Chunk.
- **Left for later.** M06-T12 builds the follow-up flow that calls `load_history` and `add_turn`. Exact tracking of which earlier Answers each Answer saw would let us withhold less; for now the forward taint is the conservative stand-in.

**Falsified assumption.** I assumed that checking each Answer's own references was enough to keep a revoked Document out of History. The review showed a later Answer that repeated the revoked one ("Budget PLANTED-MARKER-7719 as said") still reached History. So the per-Answer check was wrong; withholding now propagates forward, and the revoke, rewrite, and tombstone tests expect all three Answers withheld.

**Break run.** With `eligibility=sa.true()` in `load_history`: 2 failed (access-revoked, tombstoned), 7 passed. The new-version case still passes, because a rewritten Document's Chunks get new IDs and the version check alone withholds the Answer. Restored: 9 passed.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. The follow-up code never has to check who owns the Conversation, whether it was deleted or expired, whether an old Answer succeeded, whether its Documents still exist, are still the same version, or are still visible to this reader, whether an Answer repeats one that is now hidden, or whether the turns fit the budget. It gets plain text that is safe to send.
2. A cached History could still show an Answer from a Document the reader lost access to, or one that was rewritten or deleted after the cache was filled. The parametrized `test_an_answer_whose_document_changed_is_withheld` cases would catch it, because they change the Document between writing and reading.
3. Each Answer would need to store which earlier Answers were in its History, for example the IDs of the earlier Messages its prompt held. Then only Answers that really saw a revoked Answer, directly or through another, would be withheld.
4. It keeps the newest turns whole while they fit and the Questions of older turns, so "And for contractors?" still sees the leave Questions that name the subject. Usually that is enough; if even the last Question did not fit, the follow-up would lose its subject, which M06-T12 must handle.
5. A Conversation where a revoked Answer is quoted by a later Answer that cites only a public Chunk. History should withhold both, because the later one comes after a stale Answer.
