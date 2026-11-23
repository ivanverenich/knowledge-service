# M08-T04 — Assign retry and timeout ownership evidence

## Invariant

Only the stored Job retries. A failed attempt is classified by its error;
only a Source, database, or model that is unavailable is retried, after a
doubling, capped, jittered delay recorded in `retry_at`, until the Job's
attempts run out. Refusals and programming errors are final. A failed Job
goes back to the queue only through `requeue_due_retries`, and only once its
`retry_at` has come. Each call's own timeout ends a slow call before the
actor's time limit does.

## Owners

- Retries: the stored Job (`fail_attempt` sets `retry_at`,
  `requeue_due_retries` acts on it); Dramatiq and model SDKs retry nothing.
- Timeouts: each call's own deadline (database statement, pool, connect,
  model adapters); the actor's time limit is a backstop.
- Stuck Jobs after a cancellation or time limit: M08-T08.
- Authorization: none; nothing outside the service submits Jobs.

## Expected failing check

Before the change,
`uv run pytest tests/test_job_failures.py -q`
fails with
`ERROR: file or directory not found: tests/test_job_failures.py`.
Observed on 2026-11-23: `no tests ran in 0.11s`.

## Predictions

Written by Claude before the change, at the learner's request; not blind,
because Claude built and measured the prototype the guide comes from.

- What `retry_at` a purge gets when its delete loses the database connection on attempt 1, with the default policy: between 15 and 30 seconds after the failure (half of the 30-second ceiling plus a random share of the other half).
- How many attempts a purge that always loses the connection uses before it stops: 3 (the default `max_attempts`); the third failure has no `retry_at`.
- What happens to a purge that fails with a `KeyError`, and to the next synchronization pass: it fails once as `programming_error` with no retry, and later synchronization passes do not ask for a new purge of that Document.
- How many tests fail if `fail_attempt` stops checking whether a failure is transient: 4, the three `test_a_permanent_failure_is_never_retried` cases and `test_a_permanent_failure_is_never_queued_again`.

## Design

- **One retry layer: the stored Job.** `Job` gained `retry_at`: only a failed
  Job with attempts left carries it, in UTC and not before `finished_at`
  (`ck_jobs_retry_at` enforces the same in the database). `Job.retry` refuses
  a used-up budget, a permanent failure (`retry_at` is None), and an early
  time. A failed Job is open only while `retry_at` is set; the open-intent
  index from M08-T03 now uses that rule.
- **Classification.** `job_failures.classify_job_failure` maps an error to one
  of seven codes: Source, database, and model, each unavailable (transient)
  or refused/rejected (permanent), and `programming_error` for anything
  unrecognised. The database rule reuses `errors.database_unavailable`.
- **Backoff and jitter.** `RetryPolicy` (30 s base, 15 min cap) waits half of
  `min(cap, base * 2**(n-1))` plus a random share of the other half, in whole
  microseconds. `fail_attempt` plans `retry_at` only for a transient failure
  with attempts left and logs the code and error type, never the message.
- **The only path back.** `requeue_due_jobs` (`SKIP LOCKED`, oldest `retry_at`
  first) and `job_dispatch.requeue_due_retries` queue a failed Job again once
  its time has come; `make worker-relay` runs it before publishing. The
  M08-T03 shortcut that queued a failed Job at once on a repeat request is
  removed.
- **Purge.** `purge_document_chunks` uses `fail_attempt`; if its own
  transaction dies (a lost connection), `_record_lost_attempt` records the
  attempt in a new transaction, by the first error, only while the Job is
  still queued on the claimed attempt. `load_unpurged_documents` skips a
  Document whose latest purge (by `finished_at`) failed permanently.
- **Timeouts.** Each call owns its deadline (statement 10 s, pool 10 s,
  connect 5 s, model adapters' own); the actor's 60 s time limit is a
  backstop. A contract test pins the worst single statement (25 s) under it.
- **Migration `f6a7b8c9d0e1`.** Adds `retry_at`, the check, `ix_jobs_due_retry`,
  and the new open rule; the upgrade backfills `retry_at = finished_at` for
  failed Jobs with attempts left, the downgrade marks permanent failures used
  up, so the open set is the same on both sides.

Changed files: `src/knowledge_service/{jobs,job_failures,errors,job_dispatch}.py`,
`src/knowledge_service/persistence/{jobs,schema,documents}.py`,
`src/knowledge_service/ingestion/deletions.py`,
`src/knowledge_service/worker/relay.py`, `Makefile` (relay help text),
`migrations/versions/f6a7b8c9d0e1_job_retry_schedule.py`,
`tests/{job_fixtures,test_jobs,test_job_failures,test_job_retry_integration,test_deletions_integration,test_job_idempotency_integration,test_job_dispatch_integration,test_records_integration,test_indexes}.py`,
`docs/{architecture,threat-model,operations}.md`.

## Verification

Narrow checks (2026-11-23):

- `uv run pytest tests/test_jobs.py -q`: 9 passed.
- `uv run pytest tests/test_job_failures.py -q`: 40 passed.
- `uv run pytest tests/test_indexes.py -q`: 4 passed;
  `uv run pytest tests/test_migrations_integration.py -q -m integration`: 3 passed.
- `uv run pyright src` and `uv run pyright tests`: 0 errors.
- `uv run pytest tests/test_job_retry_integration.py tests/test_deletions_integration.py tests/test_job_idempotency_integration.py tests/test_job_dispatch_integration.py tests/test_records_integration.py -q -m integration`: 65 passed.

Deliberate break (Step 9): with `failure.transient and` removed from
`fail_attempt`, `tests/test_job_failures.py` and
`tests/test_job_retry_integration.py` gave `4 failed, 54 passed`: the three
`test_a_permanent_failure_is_never_retried` cases and
`test_a_permanent_failure_is_never_queued_again`. Restored: `58 passed`. This
matches the prediction.

Gates (2026-11-23), one after another on one test database:
`make format-check` (324 files already formatted), `make lint` (all checks
passed), `make typecheck` (0 errors), `make test-unit` (3086 passed),
`make test-integration` (753 passed), `make docs-check` (exit 0),
`git diff --check` (clean).

## Review

An adversarial review attacked the prototype three times before the guide
was written, on real PostgreSQL. Breaks found and fixed, each with a test
confirmed to fail without its fix:

- A real lost connection (`pg_terminate_backend`) killed the claim's
  transaction; storing the failure raised `PendingRollbackError`, the Job
  stayed queued with its message spent, and the consequence was classified
  `programming_error`. Now the attempt is recorded in a new transaction by
  its first error.
- That fallback could record a lost attempt against a later attempt another
  delivery had started. Now it requires the same attempt.
- A permanent purge failure came back on every synchronization pass with a
  fresh budget. Now a Document whose latest purge failed permanently is not
  asked for again.
- "Latest purge" ordered by `queued_at` mixed the relay's clock with the
  synchronization start time; now it orders by `finished_at`.
- The delay could leave its window by rounding and overflow on huge
  configurations; now it runs on whole microseconds.

Cancellation and the actor time limit were made explicit and tested rather
than changed. Storms of concurrent requeue, enqueue, and purge deliveries
left one open Job per intent and no untyped errors.

Closing reviews (standards and spec) found nothing blocking and no drift
from the guide. One stale line was fixed: the `make worker-relay` help text
now says it queues due retries first. Non-blocking observations left as
they are:

- `OpenAIChatModel` (M01, `openai_model.py`) keeps the OpenAI SDK's default
  retries and leaves some errors unwrapped, so "model adapters keep their SDK
  retries off" is true only for the adapters built since M03. No Job uses
  this adapter yet.
- `persistence/documents.py` imports `job_failures`, which imports
  `ingestion.sources`, so persistence now depends on ingestion indirectly
  (no cycle); `job_failures` is not listed in ADR 0013.
- The timeout contract bounds one statement, not a whole attempt: under lock
  contention an attempt can still reach the 60 s limit, which then rolls it
  back safely for M08-T08.
- The migration backfill also schedules failed Jobs of kinds no worker runs
  yet; they are queued again but never published, as before.
- Minor: a loop variable named `hours` used as seconds in one test, a test
  that reads `Settings()` from the environment, a module-level list constant.

Known limits:

- `retry_at` lives in the database: if no new connection works, nothing can
  store the failure; the attempt rolls back and the Job stays queued with its
  message spent (M08-T08).
- A cancellation or the actor's time limit rolls the attempt back whole; the
  Job stays queued for M08-T08.
- A wrong database password or missing database has no SQLSTATE before
  connecting and is retried as `database_unavailable` up to the budget.
- A purge that ran out of attempts on a transient failure gets a new purge
  from the next synchronization pass: a fresh budget per pass.
- Failed Jobs come back only when `make worker-relay` runs; it must run on a
  schedule.

Security: logs carry the Job ID, attempt, failure code, and error type only,
never an error message; a test plants a marker in the message and checks it
never reaches the log. No new input crosses a trust boundary.

## Decision

Put retries at exactly one layer, the stored Job, because PostgreSQL is the
only place a retry plan survives a worker or relay restart. Classify by the
error's type and treat anything unrecognised as a bug: a missed retry is
visible in the logs and fixed at the adapter, while retrying a bug or a
refused credential only repeats the harm. Wait with equal jitter so the
wait is never shorter than half the backoff and failures that happened
together come back spread out.

## Reflection

Drafted by Claude at the learner's request, grounded in the implementation
and the review; the learner should rewrite these answers in their own words.

- *Second attempt after a 10:00:00 failure?* Between 10:00:15 and 10:00:30
  is its `retry_at`; it runs at the first `make worker-relay` pass at or
  after that time, plus the time for a worker to take the message.
- *SDK retries plus Job retries?* Up to 3 x 3 = 9 calls for one Job, with the
  SDK's short waits inside each attempt and the Job's backoff between
  attempts, so neither plan controls the total.
- *Expired Confluence token?* `source_refused`; it is permanent because the
  same token fails the same way; a person must renew the credential and
  then ask for the work again.
- *Session ended mid-purge?* The claim's transaction dies with the session;
  the purge opens a new transaction, locks the Job, and stores the failure
  by its first error, `database_unavailable`, with a retry. It checks the
  attempt because the lock is gone: another delivery may have run and
  re-queued the Job, and recording against that attempt would burn a try
  that never ran.
- *Purge cut off by the time limit?* It rolls back whole and stays queued
  with its message spent; M08-T08's stuck-work sweep sends it again.
- *Wrong classification?* A `programming_error` row whose error type is a
  network or timeout error (an adapter forgot to wrap it), or a Job retried
  three times with `source_refused`-like failures such as an authentication
  error that was not wrapped.
