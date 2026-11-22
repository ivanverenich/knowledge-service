# M08-T02 — Persist before enqueue evidence

## Invariant

No message names a Job before the Job is committed to PostgreSQL. Every
queued Job of a kind a worker runs is either named by a message whose send
is recorded in `published_at`, or found and sent by the relay. A message
carries only the Job's ID, and a worker runs a Job only while it is still
queued, so duplicate or concurrent messages have the effect of one.

## Owners

- Durable truth: the `jobs` row in PostgreSQL; Redis only delivers.
- Publishing: `submit_job` once, then `publish_pending_jobs` (the relay).
- Running once: the handler's claim of a queued Job, in one transaction.
- Retries: the stored Job (M08-T04); Dramatiq retries nothing.
- Authorization: none yet; nothing outside the service submits Jobs.

## Expected failing check

Before the change,
`uv run pytest tests/test_job_dispatch_integration.py -q -m integration`
fails with
`ERROR: file or directory not found: tests/test_job_dispatch_integration.py`.
Observed on 2026-11-22: pytest reported `no tests ran` (exit 4), because the
file did not exist yet.

## Predictions

Written by Claude before the change, at the learner's request; not blind,
because the guide states some of the outcomes.

- What the relay prints when Redis is stopped and one Job waits: `published 0, skipped 0, failed 1, untried 0`, exit code 1.
- How many messages the relay sends for a Job whose first send was refused: one, on the first pass after Redis is back; later passes send none.
- What the worker does with a second message for a Job that already succeeded: logs that the Job is `succeeded` and the duplicate is ignored; nothing is deleted again and nothing is dead-lettered.
- How many tests fail if saving a queued Job stops clearing `published_at`: one, `test_a_job_queued_again_needs_a_new_message`.

## Design

- **Commit, then publish.** `submit_job` commits the Job in one transaction.
  `publish_job` then opens a second, short transaction: it locks the row with
  `SKIP LOCKED`, hands one message to Redis (in a thread, bounded by the
  Redis timeouts), and records `published_at`. A refused send rolls back and
  leaves the Job unpublished.
- **Record and repair.** Migration `c2d3e4f5a6b7` adds `jobs.published_at`
  and the partial index `ix_jobs_unpublished_queued_at` (queued and
  unpublished rows only). `publish_pending_jobs` sends such Jobs oldest
  first, up to a limit of 1 to 1,000, and stops at the first refused send.
  `make worker-relay` runs one pass and exits 1 when a send was refused.
- **ID-only messages.** One `run_<queue>_job` actor per queue of the kinds
  in `HANDLERS` (today only `purge_document_chunks`). The message carries one
  canonical UUID string; `parse_job_id` refuses every other shape before the
  database is read.
- **One effect.** `run_stored_job` ignores a Job that is not queued. The
  handler's claim locks the row with `FOR UPDATE` and claims, deletes, and
  succeeds in one transaction, so of two concurrent messages exactly one
  wins; the loser sees `InvalidJob`, reloads, and returns `None`.
- **Retries get a new message.** `save_job` clears `published_at` whenever
  it stores a queued Job.
- **Only runnable kinds.** `submit_job` raises `UnsupportedJobKind`; the
  relay lists and the publisher sends only `publisher.kinds`. Conversation
  purges and synchronization Jobs stay unpublished until a handler exists.

Changed files: `migrations/versions/c2d3e4f5a6b7_job_publication.py`,
`src/knowledge_service/persistence/schema.py`,
`src/knowledge_service/persistence/jobs.py`,
`src/knowledge_service/job_dispatch.py`,
`src/knowledge_service/worker/jobs.py`, `worker/main.py`, `worker/relay.py`,
`Makefile` (`WORKER_DB_ENV`, `worker`, `worker-relay`),
`tests/test_indexes.py`, `tests/test_worker.py`, `tests/job_fixtures.py`,
`tests/test_job_messages.py`, `tests/test_job_dispatch_integration.py`,
`tests/test_job_queue_integration.py`, `docs/architecture.md`,
`docs/threat-model.md`.

## Verification

Narrow checks (2026-11-22):

- `uv run pyright` on the changed source and tests: 0 errors.
- `uv run pytest tests/test_job_messages.py -q`: 15 passed.
- `uv run pytest tests/test_worker.py -q`: 40 passed.
- `uv run pytest tests/test_job_dispatch_integration.py -q -m integration`: 21 passed.
- `uv run pytest tests/test_job_queue_integration.py -q -m integration`: 1 passed (real Redis on 127.0.0.1:6380/15).
- `tests/test_indexes.py`, `tests/test_migrations_integration.py`, `tests/test_deletions_integration.py`: passed.

Failure injected for real (Step 10, demo database `m08_t02_demo`, dropped afterwards):

```text
purge job 01a203d5-f3c0-75e4-be5f-1c4d81777a50, chunks 1
jobs: [('queued', False)] chunks: 1
# Redis stopped
$ make worker-relay
published 0, skipped 0, failed 1, untried 0      (relay exit 1)
jobs: [('queued', False)] chunks: 1
# Redis started, worker started
$ make worker-relay
published 1, skipped 0, failed 0, untried 0
worker: job 01a203d5-f3c0-75e4-be5f-1c4d81777a50 (purge_document_chunks) ran: succeeded
jobs: [('succeeded', True)] chunks: 0
$ make worker-relay
published 0, skipped 0, failed 0, untried 0
```

Deliberate break (Step 11): with the two lines that clear `published_at` in
`save_job` removed, the three Job test files gave `1 failed, 36 passed`; the
failure was `test_a_job_queued_again_needs_a_new_message`. Restored: `37 passed`.
This matches the prediction.

Gates (2026-11-22): `make format-check` (318 files already formatted),
`make lint` (all checks passed), `make typecheck` (0 errors), `make test-unit`
(3041 passed), `make test-integration` (719 passed), `make docs-check` (exit 0).

The first `make test-integration` run reported 554 errors and 12 failures:
a reviewer's integration run migrated the same test database at the same
time and left two rows in `alembic_version`. The disposable
`knowledge_service_test` schema was reset and the gate rerun alone: 719 passed.

## Review

Two independent reviews ran against the worktree, one on standards and one on
the spec. Neither found a blocking issue. The spec review diffed every code
block of the guide against the repository and found no drift.

Non-blocking observations, left as they are:

- `JobHandler = Callable[..., Awaitable[Job]]` (`worker/jobs.py`) means pyright
  does not check the handler call against `purge_document_chunks`; a
  `Protocol` would.
- `WORKER_DB_ENV` yields an empty URL when the local Postgres container is
  missing; `JobRunner` accepts `SecretStr("")`, so `make worker` starts and
  fails on its first message instead of at startup.
- Test helpers (`closed_port`, the `redis_url` and `runtime` fixtures) are
  duplicated across test files.
- `publish_pending_jobs` catches only `PublishFailed`; any other error aborts
  the pass with a traceback instead of a report (nothing is lost; exit is
  non-zero).
- `Submission.published` is `False` when a relay is publishing the same Job
  at that moment; the Job is still published once.

Known limits (also in the guide):

- A message lost after its send was recorded (Redis restart, dead letter
  before the claim, the one-day age limit) leaves the Job queued and
  published; the relay skips it. Finding such Jobs is M08-T08.
- A publish holds the Job's row lock and one database connection while Redis
  answers, up to its 5-second timeout. Revisit when routes submit Jobs
  concurrently (M09-T03).
- A duplicate message for attempt 1 can start attempt 2 as soon as the retry
  is saved, before attempt 2's own message is sent. Still one effect per
  attempt; retry timing is M08-T04.

Security: messages are JSON and carry only a canonical Job ID; the worker
reads intent from PostgreSQL. Errors after the commit log only their type,
and the relay prints only counts, so no URL or password reaches a log.

## Decision

Accept at-least-once delivery with a worker that runs a Job only while it is
queued, rather than trying to send exactly once. Two messages for one Job are
harmless; zero messages would lose work. PostgreSQL stays the only record;
Redis only delivers.

## Reflection

Drafted by Claude at the learner's request, grounded in the implementation;
the learner should rewrite these answers in their own words.

- *`published=False` — is the work lost?* No. The Job is committed and
  queued with `published_at` null, so the next `make worker-relay` sends it.
  It can still fail to run if the relay never runs, if Redis loses the
  message after the send is recorded (M08-T08), or if its kind has no handler.
- *Message first, Job second?* The commit fails after the send, so a
  worker gets a message naming a Job that never existed. Or the worker takes
  the message before the commit lands, finds no Job, and the message is
  dead-lettered; the commit then succeeds, and a Job exists that no message
  will ever name (it would not even be found as unpublished if the send had
  been recorded).
- *Why accept a second message?* Exactly-once across PostgreSQL and Redis
  needs a distributed transaction we do not have. A second message is
  harmless because the worker checks `queued` under a row lock.
- *Two messages at once:* both see `queued` in the cheap check; both call the
  handler; `_claim`'s `SELECT ... FOR UPDATE` makes one wait; the first sets
  `running`, deletes, and commits `succeeded`; the second then sees a
  non-queued Job, gets `InvalidJob`, reloads, and returns `None`. The row lock
  in the claim decides.
- *What would prove the design wrong?* Jobs stuck in `queued` with
  `published_at` set for longer than the relay interval plus run time, or a
  purge whose Chunks were deleted twice, or `ix_jobs_unpublished_queued_at`
  growing faster than relay passes drain it.
