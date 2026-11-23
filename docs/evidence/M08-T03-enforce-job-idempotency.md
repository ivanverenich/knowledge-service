# M08-T03 — Enforce job idempotency evidence

## Invariant

At most one open Job (queued, running, or failed with attempts left) exists
per intent, its kind and target, under any interleaving of requests. A
repeated request returns the open Job instead of storing a second one, and
queues it again if it failed. A handler claims a queued Job and stores its
outcome in the same transaction, so concurrent deliveries of one Job have
one effect, and every delivery reports the Job as stored.

## Owners

- Durable truth: the `jobs` row and the `uq_jobs_open_intent` index.
- One Job per intent: `enqueue_job`, for every caller.
- Running once: the handler's claim under a row lock, in one transaction.
- Retries: the stored Job (M08-T04); a new request also queues a failed Job again.
- Authorization: none yet; nothing outside the service submits Jobs.

## Expected failing check

Before the change,
`uv run pytest tests/test_job_idempotency_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.
Observed on 2026-11-23: `no tests ran in 0.12s`.

## Predictions

Written by Claude before the change, at the learner's request; not blind,
because Claude built and measured the prototype the guide comes from.

- What a second `tombstone_missing` of the same Document returns as its purge Job: the same purge Job ID as the first, while that purge is still open; no second Job is stored.
- What three messages at once for a purge whose delete fails do to the Chunks and to the attempt count: the delete runs once and is undone, the Chunks stay, the Job is `failed` with attempt 1, and all three messages report that same stored Job.
- How many tests fail if `enqueue_job` stops using `ON CONFLICT DO NOTHING`: 8, all in `tests/test_job_idempotency_integration.py`, each with an `IntegrityError` on `uq_jobs_open_intent`.

## Design

- **Intent as the key.** A Job's intent is its kind and target. The partial
  unique index `uq_jobs_open_intent` (migration `e5f6a7b8c9d0`,
  `OPEN_JOB_PREDICATE` in `schema.py`) allows one open Job per intent: queued,
  running, or failed with attempts left. Finished Jobs stay as history.
- **Store or find.** `enqueue_job` accepts only a new queued Job, inserts it
  with `ON CONFLICT DO NOTHING` on that index, and otherwise returns the open
  Job (`Enqueued.created` false). A failed open Job is locked and queued again
  (`_queue_again`). Three rounds in which the open Job keeps finishing raise
  `JobIntentContended`. Every caller (`tombstone_missing`,
  `requeue_unfinished_purges`, `delete_conversation`, `submit_job`) goes
  through it; `submit_job` reports `Submission.duplicate`.
- **Claim and outcome in one transaction.** `purge_document_chunks` claims
  the Job under a row lock, deletes inside a savepoint (`begin_nested`), and
  stores `succeeded` or `failed` before the transaction commits, so a waiting
  delivery never sees the Job as queued again. `_claim` raises
  `JobAlreadyClaimed` carrying the stored Job.
- **Prior outcome for duplicates.** `run_stored_job` returns `JobOutcome`:
  the stored Job and `duplicate`. Every delivery of one Job reports the same
  status, attempt, and finish time.
- **Migration clean-up.** Before building the index, the migration keeps one
  open Job per intent (running, then queued, then failed; oldest first) and
  closes the rest as failed with no attempts left and `superseded_duplicate`.

Changed files: `src/knowledge_service/jobs.py`,
`src/knowledge_service/persistence/{schema,jobs,documents}.py`,
`src/knowledge_service/ingestion/deletions.py`,
`src/knowledge_service/job_dispatch.py`,
`src/knowledge_service/worker/jobs.py`,
`migrations/versions/e5f6a7b8c9d0_job_open_intent.py`,
`tests/{job_fixtures,test_indexes,test_jobs,test_job_dispatch_integration,test_job_idempotency_integration}.py`,
`docs/architecture.md`, `docs/threat-model.md`.

## Verification

Narrow checks (2026-11-23):

- `uv run pytest tests/test_jobs.py -q`: 6 passed.
- `uv run pytest tests/test_indexes.py -q`: 4 passed.
- `uv run pytest tests/test_migrations_integration.py -q -m integration`: 3 passed.
- `uv run pyright src` and `uv run pyright tests`: 0 errors.
- `uv run pytest tests/test_deletions_integration.py -q -m integration`: 9 passed.
- `uv run pytest tests/test_job_idempotency_integration.py tests/test_job_dispatch_integration.py -q -m integration`: 37 passed.

Deliberate break (Step 8): with `.on_conflict_do_nothing(...)` removed from
`enqueue_job`, the four Job test files gave `8 failed, 44 passed`, all eight
in `tests/test_job_idempotency_integration.py` (asking twice, two requests at
once, the contended case, re-tombstone reuse, both submission tests, both
after-failure tests). Restored: `52 passed`. This matches the prediction.

Each regression test from the adversarial review was confirmed to fail
without its fix while the prototype was built: the three concurrent
deliveries of a failing purge (old two-transaction shape: a raw error reached
a duplicate), both after-failure tests, the migration test (without the
clean-up, and with the old running-then-oldest ranking), and the
new-queued-only check.

Gates (2026-11-23), run one after another on one test database:
`make format-check` (320 files already formatted), `make lint` (all checks
passed), `make typecheck` (0 errors), `make test-unit` (3043 passed),
`make test-integration` (735 passed), `make docs-check` (exit 0),
`git diff --check` (clean).

## Review

An adversarial review attacked the prototype before the guide was written.
It found five breaks, all fixed with tests that fail on the unfixed code:

- A purge whose delete failed rolled back its claim before a second
  transaction stored the failure; a delivery waiting on the row lock saw the
  Job queued and ran it again, and another got a raw error. Now the outcome
  commits with the claim, inside one transaction, using a savepoint.
- A Document tombstoned again after a failed purge got that failed Job back,
  and nothing ever ran it. Now a new request queues the failed open Job again.
- The migration failed on databases with duplicate open Jobs; its first fix
  kept a failed Job over a queued one. Now it cleans up first and ranks
  running, queued, failed.
- `enqueue_job` accepted a finished Job. Now only a new queued Job.
- Handlers must raise `JobAlreadyClaimed` to be treated as a duplicate
  (contract change, documented).

Storms of up to 12 concurrent requesters with rollbacks and state changes,
and mixed tombstone/requeue/run storms with failing deletes, never left two
open Jobs for one intent and raised no untyped error.

The closing reviews (standards and spec) found nothing blocking and no drift
from the guide. One stale line was fixed: the `worker` row of
`docs/architecture.md` said a duplicate is ignored; it now says the stored
Job is returned. Non-blocking observations left as they are:

- "Open" is written three times: `Job.is_open`, `open_job()` in
  `persistence/jobs.py`, and `OPEN_JOB_PREDICATE` (copied into the
  migration). A change to one without the others makes enqueue and the index
  disagree. `open_job()` also reads like a verb.
- `superseded_duplicate` is not described in the docs; the migration comment
  is the only reference.
- `submit_job`'s docstring still opens with "Store a new queued Job".
- `RecordingPublisher` repeats `FakePublisher` from the dispatch tests.
- `submit_job` and `enqueue_job` both check "new queued Job".

Known limits:

- A failed Job with attempts left runs again only when the same work is
  requested again; nothing retries it on its own until M08-T04.
- A message for an earlier attempt can still start the next attempt once the
  Job is queued again (from M08-T02; M08-T04).
- Cancellation: `purge_document_chunks` catches `Exception` only. A
  cancellation or Dramatiq time-limit interrupt rolls back the whole
  transaction, claim included, so the Job stays queued with its message used
  up and nothing deleted. Still one effect; it waits for a new message
  (M08-T08).
- A queued Job whose message was lost after its send was recorded now also
  absorbs later requests for the same intent: `submit_job` returns it as a
  duplicate and does not publish it. Finding such Jobs is M08-T08.
- The intent is the whole key; if a full and an incremental synchronization
  must be different work, M08-T10 needs a richer intent.

Security: no new input crosses a trust boundary. Messages still carry only a
canonical Job ID; logs carry the Job ID, kind, and status only.

## Decision

Derive the idempotency key from business intent (kind plus target) and let
PostgreSQL enforce one open Job per intent with a partial unique index,
rather than trusting callers to check first or to supply a token. For
duplicate deliveries, keep the claim and the outcome in one transaction, so
the stored Job is the single answer every delivery reports.

## Reflection

Drafted by Claude at the learner's request, grounded in the implementation
and the review; the learner should rewrite these answers in their own words.

- *Second tombstone after a failed purge?* It gets the same purge Job, now
  queued again for attempt 2, and the relay sends it. A new purge beside it
  would be a second open Job doing the same work, and the failed one could
  later run as well.
- *Select, then insert?* Request A selects: nothing. Request B selects:
  nothing. A inserts, B inserts: two Jobs. Now the unique index makes B's
  insert wait for A's commit, then `ON CONFLICT DO NOTHING` skips it and B
  reads A's Job.
- *Three messages, failing purge, M08-T02 shape?* Worker 1 claims and fails;
  its rollback undoes the claim and releases the lock while the row still
  says queued; worker 2, waiting on the lock, sees queued and runs the work
  again; worker 1's second transaction then finds the Job not queued and
  raises. The new `begin_nested()` savepoint undoes only the delete, and
  `job.fail(...)` is stored before the lock is released.
- *Why the stored Job instead of `None`?* So every delivery reports one
  result; logs, callers, and later retry logic see the same status and
  attempt instead of `None`, a raw error, or a different outcome.
- *Key too coarse?* Two requests that differ in a parameter the target does
  not capture, such as full versus incremental synchronization, would merge;
  you would see a request answered with a Job that does the other kind of
  work.
- *What would prove it wrong?* Two rows in `jobs` with the same kind and
  target that both satisfy the open predicate, a purge with more delete
  calls than recorded attempts, or a duplicate delivery reporting a
  different status than the stored row.
