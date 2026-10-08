# M02-T09 — Conversations and feedback evidence

Completed: 2026-10-08

## Invariant

A Conversation belongs to exactly one User and carries the moment it expires,
computed when it opens. Only its owner may act on it. Its Messages are one per
ordinal, so reading them back returns the exchange in the order it happened, and
the Conversation refuses a Message once it holds as many as its bound allows.
A Conversation that has passed its deadline and has not been deleted is what a
deletion query selects, so the deadline alone must not be read as "already
gone". A Message carries at most one rating per User, and rating it again
replaces that rating rather than adding a second signal.

## Prediction

I expect the revision to create `conversations`, `messages`, and
`message_feedback`. `messages` should carry `UNIQUE (conversation_id, ordinal)`
and a cascading foreign key, and `message_feedback` should be keyed by
`(message_id, user_id)` so the rating policy is a key rather than a convention.
`append_message` should refuse the Message past the bound with
`ConversationFull`, and `load_conversations_due_for_deletion` should select only
the expired Conversation that is not deleted. Rating one Message twice from the
same User should leave one row holding the later value, while a second User
should produce a second row.

## Verification

- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_conversations.py tests/test_indentifiers.py -q`
  — 106 passed. The 7 conversation tests cover the deadline computed at open,
  the owner check including the refusal, the exact deadline boundary and the
  deleted case, the last-message time advancing and refusing to move backwards,
  a non-positive retention, empty Message text, and a thumbs rating. The
  identifier suite now covers `MessageId` through the same parametrized cases as
  every other identifier type.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m integration -q`
  — 8 passed in 1.39s against PostgreSQL 17.11, covering migrations, Sources,
  Documents, Chunks, Access Grants, and Conversations. The conversation test
  passed on two consecutive runs. Reading the tables after the run shows three
  Conversations with deadlines `2026-09-08` (expired, live), `2026-09-28`
  (deleted), and `2026-11-07` (live); three Messages at ordinals `0`, `1`, `2`
  reading `user`, `assistant`, `user`; and two feedback rows where the owner's
  rating is `down` after being re-rated from `up`. Only the expired, undeleted
  Conversation was selected for deletion, and the reloaded Conversation's
  last-message time had advanced to the final Message. A duplicate ordinal and
  a Message past the bound were both refused. No connection URL or credentials
  are recorded.
- `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=<disposable URL> UV_CACHE_DIR=/tmp/uv-cache-rag-project make check`
  — Ruff format and lint passed, Pyright strict clean, 241 passed, 2 deselected,
  total coverage 89%.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` — passed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pre-commit run --all-files` —
  all 12 hooks passed, including secret scanning, formatting, lint, type
  checking, and deterministic tests.
- `git diff --check` — passed.

Implementation files: `src/knowledge_service/identifiers.py`,
`src/knowledge_service/conversations.py`,
`migrations/versions/4c3e640575eb_conversations_and_feedback.py`,
`src/knowledge_service/persistence.py`, `tests/test_indentifiers.py`,
`tests/test_conversations.py`, and `tests/test_conversations_integration.py`.
The tables store internal identifiers, roles, ordinals, and thumbs values only,
so no credential, connection string, or private source content entered a
tracked artifact; the integration test reads only the dedicated
disposable-database variable.

## Reflection

1. `Conversation.open()` owns the date arithmetic and refuses a retention that
   is not positive, and `require_owner()` decides who may act. Without them a
   caller would have to add the duration to the open time itself, remember to
   reject a zero or negative window, and remember to compare the caller's
   identity with the stored owner on every path that touches the Conversation —
   and any one of those omissions would be invisible until the wrong User read
   someone else's exchange.

2. Keeping every rating and picking the newest on read would move the decision
   into application code. A query that wanted to count negative signals would
   need a latest-wins subquery per Message to avoid counting a rating the User
   already changed, and every caller would have to repeat that subquery or risk
   disagreeing with the others. With one row per Message and User, the current
   rating is simply the row, and re-rating is an update instead of an insert
   that grows the table with every click.

3. Without that assertion, two Messages could share an ordinal and the
   Conversation would read back in an arbitrary order between them, so an Answer
   could be shown next to the wrong question. Nothing would crash, and the
   tables would still look consistent, so the fault would surface as an
   occasional confusing transcript rather than as an error.
