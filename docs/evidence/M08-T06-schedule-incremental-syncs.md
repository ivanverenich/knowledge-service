# M08-T06 — Schedule incremental Synchronization Runs evidence

## Invariant

Each relay pass queues at most one synchronization Job per enabled Source,
and only for a Source with no open synchronization Job whose last one
finished, or was queued, at least one interval before the pass's clock, or
that never had one. A missed schedule queues one Job, not one per missed
interval; two passes at once store one Job per Source. A queued Job names its
Source by ID and carries no configuration or credentials reference.

## Owners

- When a Source is due: `load_due_source_ids`, from the Jobs table.
- One Job per Source: the open-intent index (M08-T03).
- Running the Job, reading configuration and checkpoint: M08-T10.
- Authorization: none; nothing outside the service schedules.

## Expected failing check

Before the change,
`uv run pytest tests/test_scheduling_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.
Observed on 2026-11-23: `no tests ran in 0.12s`.

## Predictions

Written by Claude before the change, at the learner's request; not blind,
because Claude built and measured the prototype the guide comes from.

- What a pass queues for a Source whose last run finished 59 minutes ago, and at one hour: nothing at 59 minutes; one synchronization Job at one hour.
- How many Jobs a pass queues for a Source after the relay was down for ten hours: one.
- What happens to a Source disabled by another session just after the pass read it: the pass rechecks it under a share lock and queues nothing for it.
- How many tests fail if the "at least one interval ago" condition is removed: 3, the tests that count the interval.

## Design

- **When a Source is due.** `persistence.sources.load_due_source_ids`
  returns enabled Sources with no open synchronization Job whose last
  activity (newest of a Job's `queued_at` and its `finished_at`, ignoring a
  finish time later than `now`; Jobs queued after `now` do not count) is at
  least one interval before `now`, or that never had a Job. Candidates are a
  materialized CTE, so each Source's last activity is computed once; order is
  never-synchronized first, then oldest activity, then Source ID, so a
  limited pass cannot starve the tail. Partial index
  `ix_jobs_synchronization_target` (migration `b8c9d0e1f2a3`) finds a
  Source's synchronization Jobs.
- **Queuing.** `ingestion.scheduling.schedule_due_synchronizations` (UTC
  `now`, limit 1..1,000, default 100) reads the due Sources, rechecks them
  with `lock_enabled_sources` (`SELECT ... WHERE enabled FOR SHARE`), and
  stores `Job.request(kind=synchronize_source, target_id=<Source ID>)` for
  each through `enqueue_job`, all in one transaction. The open-intent index
  (M08-T03) keeps two passes at once to one Job per Source.
- **Policy.** `SynchronizationSchedule(interval)`, 1 minute to 7 days;
  `KNOWLEDGE_SERVICE_SYNCHRONIZATION_INTERVAL_MINUTES`, default 60.
- **Relay.** `worker.relay.relay_once` runs requeue, schedule, publish. A
  scheduling failure is logged by exception type, reported, and publishing
  still runs; `main` exits 1.
- "Enqueued" means the Job is stored in PostgreSQL. No handler runs
  `synchronize_source` yet, so the publisher sends no message for it until
  M08-T10.

Changed files: `src/knowledge_service/ingestion/scheduling.py`,
`src/knowledge_service/persistence/{sources,schema}.py`,
`src/knowledge_service/settings.py`, `src/knowledge_service/worker/relay.py`,
`migrations/versions/b8c9d0e1f2a3_job_synchronization_target_index.py`,
`tests/{test_indexes,test_scheduling,test_scheduling_integration}.py`,
`docs/{architecture,operations,threat-model}.md`.

## Verification

Narrow checks (2026-11-23):

- `uv run pytest tests/test_indexes.py -q`: 4 passed.
- `uv run pytest tests/test_migrations_integration.py -q -m integration`: 3 passed.
- `uv run pytest tests/test_scheduling.py -q`: 12 passed.
- `uv run pyright src/knowledge_service`: 0 errors.
- `uv run pytest tests/test_scheduling_integration.py -q -m integration`: 22 passed.

Deliberate break (Step 6): with `candidates.c.last_activity <= now - interval`
replaced by `sa.true()`, `tests/test_scheduling.py` and
`tests/test_scheduling_integration.py` gave `3 failed, 31 passed`: the
interval test, the long-run test, and the fast-clock test. The missed-schedule
and already-running tests still passed, because an open Job alone stops a
second one. Restored: `34 passed`. This matches the prediction.

Gates (2026-11-23), one after another on one test database:
`make format-check` (335 files already formatted), `make lint` (all checks
passed), `make typecheck` (0 errors), `make test-unit` (3125 passed),
`make test-integration` (797 passed), `make docs-check` (exit 0),
`git diff --check` (clean).

## Review

An adversarial review attacked the prototype on real PostgreSQL before the
guide was written. Each break was fixed with a test that fails without the
fix:

- Starvation: ordering by Source ID let a limited pass reach the same first
  Sources every time; now the longest-waiting come first.
- A clock ahead: a finish time a year in the future made a Source look just
  synchronized for a year; future finish times are ignored and Jobs queued
  in the future do not count.
- Disabled or deleted mid-pass: a Source disabled after the read still got a
  Job; the share-lock recheck now leaves it out.
- Query cost: a full `jobs` scan per Source and the last-activity subquery
  computed up to three times; now a partial index and a materialized CTE.
- A scheduling error held up publishing every other Job; now it is isolated.

Closing reviews (standards and spec, each on its own database and Redis db):
nothing blocking. Non-blocking observations left as they are:

- The `schedule_due_synchronizations` and `ScheduleReport` docstrings say
  `already_open` lists Sources; it holds the IDs of the Jobs that were
  already open.
- The `worker-relay` help text in the `Makefile` still says "Queue due
  retries, then publish"; it now also schedules.
- The threat-model row's "at most 1,000" is the limit's ceiling; a relay pass
  uses the default of 100.
- No test drives a successful `relay_once` that stores a scheduled Job, or
  `main`'s exit 1 after a scheduling failure.
- The interval bounds are written in both `settings.py` and `scheduling.py`
  (a unit test checks they agree); the index predicate is a literal rather
  than built from `JobKind`; the migration's create date is earlier than its
  parent's.
- A Source whose synchronization fails permanently becomes due again one
  interval after it finished, so it is tried once per interval.

Known limits:

- A Source whose synchronization Job is stuck open is not scheduled again
  until M08-T08; until M08-T10 runs these Jobs, each Source is queued once
  and stays queued.
- Disabling a Source does not cancel a Job already queued for it, and
  `jobs.target_id` has no foreign key: the M08-T10 handler must treat a
  missing or disabled Source as final.
- Each pass computes last activity over all synchronization Jobs of every
  candidate Source; that grows with history until old Jobs are pruned.
- A failure while requeuing due retries still stops the pass before
  publishing (from M08-T04).

## Decision

Decide "due" from the Jobs table itself, not from a list of planned times:
a Source is due when it has no open synchronization Job and its last
activity is at least one interval old. There is no backlog to replay, so a
missed schedule queues one Job, and the open-intent index makes concurrent
passes safe. Count from when a run finished so a long run gets a full rest.
Store only the Source's ID, so the handler reads current configuration and
credentials from PostgreSQL when it runs.

## Reflection

Drafted by Claude at the learner's request, grounded in the implementation
and the reviews; the learner should rewrite these answers in their own words.

- *Queued 08:00, finished 10:30, hourly:* due at 11:30. Last activity is the
  finish time, so a run longer than the interval is not followed at once by
  another.
- *Relay down 00:00–10:00:* one Job. Due is computed from the last activity,
  not from missed slots, and once the Job exists the Source has an open Job.
- *Why only the ID?* A credentials reference would sit in Redis, which is not
  protected like PostgreSQL; a stored location would make an old Job run
  against configuration that has since changed.
- *150 due, limit 100:* the 50 with the most recent activity wait; the 100
  queued get open Jobs and then newer activity, so the waiting 50 sort first
  next pass.
- *Signs of redundant Jobs:* more than one open `synchronize_source` Job per
  `target_id` (the index forbids it), or Jobs for the same Source queued less
  than one interval after the previous one finished.
