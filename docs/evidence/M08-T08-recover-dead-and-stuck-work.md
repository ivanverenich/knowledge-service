# M08-T08 — Recover dead and stuck work evidence

## Invariant

A running Job the database has not heard from for `stale_after` (five
minutes) has its attempt failed with `worker_lost`; a queued Job whose
message was sent `orphan_after` (30 minutes) ago has the attempt it stood
for failed with `delivery_lost`; a Job heard from within `stale_after` is
never touched, whatever its worker's clock says. Each recovered attempt's
row keeps its progress and gets an outcome. A Job that failed with no retry
planned moves on only by an administrator's audited retry or cancel; a
retry never opens a second Job for the same work, and a running Job is
never cancelled.

## Owners

- Liveness: `job_attempts.heard_at`, stamped by PostgreSQL.
- Recovery: `recover_stuck_jobs`, the first step of every relay pass.
- Retries after recovery: the stored Job (M08-T04).
- Decisions and authorization: `recover_jobs`, the `administrator` role.

## Expected failing check

Before the change,
`uv run pytest tests/test_job_recovery_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.
Observed on 2026-11-24: `no tests ran in 0.34s`.

## Predictions

Written by Claude before the change, at the learner's request; not blind,
because Claude built and measured the prototype the guide comes from.

- What a relay pass does to a Job that reported 40 seconds ago but started an hour ago: nothing; the database heard from it 40 seconds ago, so the query never offers it.
- What happens to the attempt row of a Job recovered as stale: it keeps its counters and heartbeat, and gets outcome `retry_scheduled` (or `failed` with no attempts left) and the recovery time as `finished_at`.
- What an administrator gets when retrying an old purge while a newer purge of the same Document is queued: 409 `job_already_open`; the old Job stays failed and nothing is audited.
- How many tests fail if `condition_of` judges a running Job by its start time only, and whether the slow Job of the drill is still left alone: 3 (two unit tests and the report-before-lock race); yes, the slow Job is still left alone, because the candidate query measures silence with `heard_at`.

## Design

- **Conditions (domain).** `job_conditions.condition_of` judges a Job:
  queued is `waiting` or `orphaned` (published at least `orphan_after`, 30
  min, ago and not queued after `now`); running is `running` or `stale`
  (unheard for `stale_after`, 5 min); failed is `retry_planned`,
  `exhausted` (transient code, no retry left) or `failed_permanently`;
  then `succeeded` and `cancelled`. `allowed_decisions` offers retry to a
  failed Job with no retry planned, and cancel to a queued or failed one.
- **Liveness by the database's clock.** `job_attempts.heard_at` (migration
  `d0e1f2a3b4c5`) is stamped with `clock_timestamp()` on every accepted
  report, and by `record_heard` in its own transaction when a report is
  refused for going back or being from the future; the refusal stays typed
  even if that second transaction fails. Silence is `now() - coalesce(heard_at,
  started_at)` in SQL, so no worker's clock decides it.
- **Recovery.** `job_recovery.recover_stuck_jobs` (first step of
  `relay_once`) lists up to 100 silent and 100 undelivered Jobs, then, per
  Job in its own transaction, locks the row `FOR UPDATE SKIP LOCKED`, reads
  the silence again and re-judges it. A stale attempt fails with
  `worker_lost`; an orphaned Job is started and failed with
  `delivery_lost`, counting the lost delivery as an attempt. Both codes are
  transient, so `fail_with` plans a retry while attempts are left.
  `finish_attempt` ends the attempt row, which keeps its counters and
  heartbeat. Errors are logged by type, counted, and make the relay exit 1;
  the other steps still run.
- **Decisions.** `JobStatus.CANCELLED` (finish time, keeps any start and
  error code; never open). `Job.reopen` queues a failed Job with no retry
  planned and gives an exhausted one exactly one more attempt; `Job.cancel`
  refuses a running Job with `JobStillRunning`. `job_decisions.retry_job`
  saves inside a savepoint and turns a `uq_jobs_open_intent` violation into
  `JobIntentAlreadyOpen`; both decisions lock the Job and write an audit
  event (`job.retried` / `job.cancelled`, actor = administrator, metadata:
  kind, from_status, failed_attempts, error_code) in the same transaction.
- **API.** `GET /v1/job-decisions` (failed for good, not replaced by a newer
  Job of the same intent, at most 100), `POST /v1/jobs/{job_id}/retry` and
  `/cancel`, all behind the new `recover_jobs` operation (administrator),
  checked before the ID is parsed; 404 `job_not_found`, 409
  `job_already_open` / `job_running` / `decision_not_allowed`, codes only.
- **Clocks.** The purge claim and the lost-attempt path start at
  `max(now, queued_at)`; recovery ends attempts no earlier than anything the
  Job recorded.

Changed files: `src/knowledge_service/{job_conditions,job_recovery,job_decisions}.py` (new),
`src/knowledge_service/persistence/job_recovery.py` (new),
`src/knowledge_service/{jobs,job_failures,job_progress,job_reporting,app,contracts,roles,audit}.py`,
`src/knowledge_service/persistence/{jobs,job_attempts,schema}.py`,
`src/knowledge_service/ingestion/deletions.py`, `src/knowledge_service/worker/relay.py`,
`migrations/versions/d0e1f2a3b4c5_job_recovery_decisions.py`,
`tests/{test_job_conditions,test_job_recovery_integration}.py` (new),
`tests/{test_app_authentication,test_indexes,test_job_failures,test_roles}.py`,
`docs/{architecture,operations,security,threat-model}.md`.

## Verification

Narrow checks (2026-11-24):

- Step 2, `tests/test_jobs.py tests/test_job_failures.py tests/test_job_progress.py tests/test_job_conditions.py`: 119 passed.
- Step 3: `uv run pyright src` 0 errors; `tests/test_indexes.py` 4 passed;
  `tests/test_migrations_integration.py` 3 passed; progress, deletions,
  retry and scheduling integration tests: 71 passed.
- Step 4, `tests/test_roles.py tests/test_app_authentication.py`: 227 passed.
- Step 5, `tests/test_job_recovery_integration.py -m integration`: 29 passed.

Deliberate break (Step 6): with `heard = job.started_at` in
`condition_of`, `tests/test_job_conditions.py` and
`tests/test_job_recovery_integration.py` gave `3 failed, 62 passed`: the two
unit tests that judge a running Job by its last report, and
`test_a_report_that_lands_before_the_lock_keeps_the_job_alive`. The slow
Job of the drill was still left alone, because the candidate query measures
silence with `heard_at`. Restored: `65 passed`. This matches the prediction.

Gates (2026-11-24), one after another on one test database:
`make format-check` (348 files already formatted), `make lint` (all checks
passed), `make typecheck` (0 errors), `make test-unit` (3266 passed),
`make test-integration` (848 passed), `make docs-check` (exit 0),
`git diff --check` (clean).

## Review

An adversarial review attacked the prototype on real PostgreSQL in three
rounds before the guide was written. Each break was fixed with a test that
fails without the fix:

- A worker whose clock ran behind the relay's was declared dead while it
  reported; liveness now uses `heard_at`, stamped by the database.
- A worker whose clock stepped back had every report refused and was
  declared dead; a refused report now records the worker as heard.
- An operator's retry was burned by a worker clock behind the API's; the
  claim now starts at `max(now, queued_at)`.
- Jobs queued by a clock ahead filled every pass and starved real orphans;
  the query now leaves out Jobs queued after `now`.
- A database error while recording a refused report replaced the typed
  refusal; the refusal is now always raised.

Races held: three relays at once recovered 80 stuck Jobs exactly once
each; recovery against reports, claims, cancels and requeues never lost a
report or recovered a live Job.

Closing reviews (standards and spec, each on its own database and Redis
db): nothing blocking. Non-blocking observations left as they are:

- `job_decisions` catches `IntegrityError` and reads the constraint name,
  a database detail ADR 0013 would place in `persistence.jobs`;
  `JobNotFound` and `JobIntentAlreadyOpen` live in the workflow module, not
  next to the other Job errors in `jobs.py`.
- The endpoints judge `condition` with `datetime.now(UTC)`, not the
  injectable clock of `DatabaseJobDecisions`; `read_synchronization_job`
  still has its own 503 block instead of `_jobs_unavailable`.
- A Job that a newer Job replaced is hidden from the list but can still be
  retried by ID.
- A repeated retry or cancel answers 409 `decision_not_allowed`, not the
  first result.
- No test covers the relay's recovery error path (exit 1) or the 503 when
  no decisions store is wired; as with `job_reports`, no production
  composition passes `DatabaseJobDecisions` yet.

Known limits:

- A message that waits in the queue longer than 30 minutes costs an
  attempt, so a long backlog can exhaust healthy Jobs.
- A worker silent for five minutes between reports is taken for dead and
  its work may run twice; the M08-T10 handler must report more often.
- A Job with no attempt row is judged by its worker's start time; existing
  attempts count as heard at the migration.
- A worker that keeps sending refused reports keeps its Job running, and a
  running Job cannot be cancelled.
- A pass can be filled by candidates it cannot recover, such as rows held
  locked for long.
- A Job claimed by a clock far ahead is recovered with finish and retry
  times that far ahead.
- A retry does not check that a synchronization's Source is enabled.
- The decision list shows at most 100 Jobs, with no paging.
- `save_job` has no status guard (from M08-T07): a worker presumed dead
  that wakes after its Job was requeued can still set it back to `failed`;
  no handler reaches this today, and the M08-T10 handler must check its
  claim before it stores a finish.

## Decision

Judge liveness by what the database observed, not by what a worker says:
`heard_at` is stamped by PostgreSQL on every report, even a refused one,
and silence is measured in SQL. Recovery never acts on its first read; it
re-judges each Job under its own row lock and skips a row someone holds.
Count a lost delivery as an attempt, so a message that keeps getting lost
ends as `exhausted` in front of an operator instead of looping unseen,
accepting that a backlog over 30 minutes costs attempts. Leave anything
that cannot move on by itself to an audited administrator decision; never
cancel a running Job, and never let a retry open a second Job for the same
work.

**What would falsify this design:** a Job that keeps reporting being failed
as `worker_lost`; an attempt row lost or overwritten by recovery; one Job
recovered twice by concurrent relays; a retry leaving two open Jobs for one
intent; a cancelled Job running; or, in production, healthy Jobs routinely
ending `exhausted` with `delivery_lost`, which would show that 30 minutes
is shorter than the real queue delay and that counting lost deliveries is
the wrong trade-off.

## Reflection

Drafted by Claude at the learner's request, grounded in the implementation
and the reviews; the learner should rewrite these answers in their own words.

- *J8, started an hour ago, reported 40 s ago:* recovery reads
  `job_attempts.heard_at`, stamped by the database at the report.
  `heartbeat_at` is the worker's own clock, which may run behind or step
  back and make a live worker look dead.
- *Worker A paused six minutes:* its next report raises
  `AttemptNotCurrent`; nothing it reports is stored. Its attempt row keeps
  what it had reported before going silent, and the work runs again in
  attempt 2, which is why handlers must be idempotent.
- *A message 35 minutes behind a backlog:* recovery started and failed the
  Job with `delivery_lost` and planned a retry; the late message finds it
  `failed`, not `queued`, and does nothing. Republishing without counting
  would cost nothing for a slow queue but loop forever, unseen, for a
  message that is always lost.
- *After the caller believes it succeeded:* a worker that finished its
  writes but went silent for five minutes before storing the outcome is
  recovered as `worker_lost`, and the work runs again.
- *Role removed mid-session:* the next retry is refused with 403 before the
  ID is parsed; the earlier decision stays in `audit_events` with the
  administrator as actor.
- *Waiting instead of skipping a locked row:* `test_a_job_whose_row_a_worker_holds_is_skipped`
  would wait on the row its own test holds, and hang. *No savepoint around
  the retry's save:* no test would fail today, because the refused update
  is turned into `JobIntentAlreadyOpen` at once and the transaction is
  rolled back; the savepoint only protects code that would keep using the
  transaction afterwards, which nothing tests.
