# M06-T10 — Persist bounded Conversations evidence

## Invariant

`start_conversation`, `add_message`, `read_conversation`, and
`delete_conversation` put the caller's `UserId` in their SQL, so another
User's Conversation never leaves the database. A Conversation that is
missing, another User's, deleted, or past its retention deadline reads as
None and refuses a Message with the same `ConversationNotFound`.
`add_message` gives each Message the next ordinal under a row lock, so
concurrent Messages get 0, 1, 2, and so on, with no gap or duplicate, and
never pass `max_messages`. `delete_conversation` marks the Conversation
deleted and queues one purge Job in the same transaction; from its commit
on, nothing of it can be read or added to. `ConversationLimits` refuses
text that is empty, too long, or not storable before any SQL runs. Errors
and reprs hold no Message text.

## Expected failing check

Before the change, `uv run python -c "from knowledge_service.persistence.conversations import add_message, delete_conversation"`
fails with `ImportError`: cannot import name `add_message`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What a User sees when they ask for another User's Conversation, compared with one that never existed: exactly the same thing: `read_conversation` returns None, `add_message` raises `ConversationNotFound` with the same message, and `delete_conversation` returns False, because the owner is part of the SQL `WHERE` and the row never leaves the database.
- What ordinals eight Messages get when they are added to one Conversation at the same moment, and what makes that so: 0 to 7, each once, with no gap, because `add_message` locks the Conversation's row with `FOR UPDATE`, so the eight transactions take turns, and each reads the highest ordinal only after it holds the lock, seeing every earlier commit under READ COMMITTED.
- How many integration tests fail if `add_message` stops locking the Conversation's row, and which ones: 4 of 19: the eight concurrent Messages, the limit under concurrency, the delete racing Messages, and reading then writing in one transaction; two Messages read the same highest ordinal and the unique rule refuses the second with an untyped `IntegrityError`.

## Design

- **Changed files.** `src/knowledge_service/conversations.py` gains `ConversationNotFound`, `ConversationLimits` with `checked_text`, `MAX_RETENTION`, and `Transcript`; `Message.text` leaves the repr; `Conversation.open` refuses a deadline past the calendar. `src/knowledge_service/persistence/conversations.py` gains `start_conversation`, `add_message`, `read_conversation`, and `delete_conversation`. New tests: `tests/test_conversation_limits.py` and `tests/test_conversation_store_integration.py`. The schema and `docs/architecture.md` do not change.
- **One predicate.** `_live` is this ID, this owner, `deleted_at` null, and `retention_deadline` after `now`. Read and add use it; delete uses the ID, the owner, and not deleted, so an owner can still delete an expired Conversation.
- **Ordinals.** `add_message` checks the text, role, and time before any SQL, locks the live row `FOR UPDATE`, then reads the count and the highest ordinal, refuses at `max_messages` with `ConversationFull`, and writes ordinal `max + 1` with a time never earlier than `last_message_at`.
- **Reads.** `read_conversation` takes no lock and returns a `Transcript` or None.
- **Deletion.** `delete_conversation` sets `deleted_at` to the later of `now` and `last_message_at` with `RETURNING`, and only when that returns a row queues one `purge_conversation` Job in the same transaction; it returns True only for the call that deleted.
- **Limits and time.** Retention is positive and at most 10 years; Messages and characters are positive integers; text must be plain, non-empty, within the limit, without NUL or a lone surrogate. Every `now` must carry a time zone and is converted to UTC.

## Verification

| Command | Result |
|---|---|
| `uv run pytest tests/test_conversation_limits.py -q` | 26 passed |
| `uv run pytest tests/test_conversation_store_integration.py -m integration -q` | 19 passed |
| `make format-check` | 251 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors |
| `make test-unit` | 2196 passed |
| `make test-integration` | 361 passed |
| `make docs-check` | passed |

- **Expected failing check.** Before Step 3, importing `add_message` and `delete_conversation` failed with `ImportError`.
- **Deliberate break.** Without `.with_for_update()`, 4 of 19 integration tests failed; restored, 19 passed. Details under Decision.
- **Known limits.** The functions expect READ COMMITTED, the project's default; at a stricter level, waiting for a turn becomes a serialization error. Deleted rows stay until the purge worker of M08 runs. An owner deleting their own expired Conversation gets True, while read and add treat it as missing; this reveals nothing about other Users. The M02 functions without an owner remain for system work and must never receive a User's request.

## Review

An adversarial review attacked ownership, concurrent ordering, deletion, retention boundaries, time zones, and hostile input on real PostgreSQL. It found no way to read, add to, or delete another User's Conversation, no gap or duplicate in ordinals under READ COMMITTED, and no Message text in any error or repr. It found five problems; four were fixed with tests that failed before the fix (8 tests), and one is documented: a shared lock on read followed by an add or delete in the same transaction deadlocked under concurrency; a start near the end of the calendar or a retention of `timedelta.max` raised an untyped `OverflowError`; a time zone with a zero offset in winter, such as London, moved the deadline by the summer-time hour or was refused; a deletion could be earlier than a Message committed before it; and REPEATABLE READ or SERIALIZABLE turned waiting into serialization errors, now documented as requiring READ COMMITTED.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **Ownership inside the SQL.** Loading a Conversation and then calling `require_owner` brings another User's row into memory first, where a log line, a cache, or a forgotten check can expose it. With `owner_id` in every `WHERE`, PostgreSQL never returns a row that is not the caller's, and all four functions share one predicate, `_live`, so the rule cannot drift.
- **One answer for "not yours".** If another User's Conversation answered differently from a missing one, a User could probe IDs to learn that a Conversation exists. Missing, foreign, deleted, and expired therefore read as None and refuse with the same `ConversationNotFound`.
- **The database picks the ordinal, under a lock.** A caller that sends its own ordinal can send a gap or a duplicate, and two requests that count first and insert later both see the same count. Locking the Conversation's row makes concurrent Messages take turns, so the next ordinal and the limit are read by one transaction at a time.
- **Mark now, purge later.** Setting `deleted_at` is one small update that every owner-scoped read and write honours at once, and it can be undone if a purge goes wrong. Removing the rows is slower work for a background Job, queued in the same transaction, so a deletion is never lost between the two.
- **Left for later.** The purge worker comes with M08; building bounded History from a `Transcript` is M06-T11.
- **Deliberate break.** Deleting `.with_for_update()` from `add_message` failed 4 of 19 integration tests: eight concurrent Messages, the limit under concurrency, a delete racing Messages, and reading then writing in one transaction. Two Messages read the same highest ordinal, and the unique rule from M02 refused the second with an untyped `IntegrityError`. The data stayed correct, but a User would get a crash instead of their turn. Restored; 19 passed.
- **Falsified assumption.** I assumed a shared lock on read would make reading safer. The review shows that two transactions holding a shared lock that both then want the exclusive one deadlock, so reading the history and then adding the question, the likely answering flow, failed for one of two requests. Since a delete only marks the row, the read needs no lock at all.
- **Second falsified assumption.** I assumed that checking `utcoffset() == 0` meant a time was UTC. The review shows a London time in winter has offset zero, and adding 180 days to it moves the deadline by the summer-time hour, or makes it fail. So every `now` is converted to UTC first.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. **What the store hides from the follow-up code.** That code never checks who owns a Conversation, whether it was deleted or expired, which ordinal comes next, whether two requests race, how many Messages fit, whether text can be stored, which time zone `now` is in, or that a purge must be queued. It asks for the owner's `Transcript` and adds a Message; anything else is `ConversationNotFound`, `ConversationFull`, or `InvalidConversation`.
2. **A cache keyed by Conversation ID.** A cache that forgets the owner would serve one User's Transcript to another, and one that is not cleared on delete would keep serving a deleted Conversation. `test_another_user_can_neither_read_add_nor_delete` and `test_deletion_is_immediate_and_queues_one_purge` would catch both, if the cache sits under `read_conversation`.
3. **Why the store picks the ordinal.** With the old `append_message`, two requests both counted 3 Messages and both sent ordinal 3: one crashed on the unique rule, or, with a gap in the caller's own numbers, History later read Messages in the wrong order. The store reads the highest ordinal under the lock, so the next number is never a guess.
4. **Deleting while a question is being sent.** Both requests wait for the Conversation's row. If the add locks first, the Message is stored and then the delete marks the Conversation; the Message is deleted with it. If the delete commits first, the add finds no live Conversation and raises `ConversationNotFound`. In neither order does a Message appear after the deletion.
5. **The next case.** An owner who, in one transaction, reads at `retention_deadline` minus one microsecond and then adds a Message at the deadline itself. I expect the read to return the Transcript and the add to raise `ConversationNotFound`, since each statement checks the deadline against its own `now`.
