# M08-T07 — Report progress and terminal outcomes evidence

## Invariant

Each attempt of a Job has one progress row in PostgreSQL. While the Job runs
that attempt, a report may move its stage and only raise its counters, add
warnings, and move its heartbeat forward; anything else is refused, and an
attempt the Job is not running cannot write. Each report commits on its own,
so a new process and the API read it after the reporter is gone. Ending an
attempt records one typed outcome that agrees with the finished Job, and
never fails on what a report stored.

## Owners

- Durable progress and outcome: `job_attempts` in PostgreSQL.
- Who may report: the attempt the Job is running (`lock_running_job`).
- Retries: the stored Job (M08-T04); recovery of dead attempts: M08-T08.
- Authorization: `GET /v1/jobs/{job_id}` needs `start_synchronization_runs`.

## Expected failing check

Before the change,
`uv run pytest tests/test_job_progress_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.
Observed on 2026-11-23: `no tests ran in 0.15s`.

## Predictions

Written by Claude before the change, at the learner's request; not blind,
because Claude built and measured the prototype the guide comes from.

- What happens to a report of 50 processed that arrives after 60 was stored: it is refused (`ProgressWentBackwards`) and the row keeps 60.
- What the API shows after the worker that reported 120 processed is killed: the Job stays `running` with 120 processed and the last heartbeat, until M08-T08 recovers it.
- What attempt 1's worker gets when it reports after attempt 2 began: `AttemptNotCurrent`, and nothing is written.
- How many tests fail if the domain stops checking counters, and whether the database still refuses a lower counter: 3; yes, the guarded `UPDATE` still refuses it.

## Design

- **Domain.** `job_progress.JobProgress` is one attempt as a frozen value:
  stage (`JobStage`, may repeat), counters (`JobCounters`: processed,
  changed, skipped, 0 to 2^63-1), warnings (`JobWarning` codes, never raw
  text), `started_at`, `heartbeat_at`, and once ended a typed
  `AttemptOutcome` (`succeeded`, `succeeded_with_warnings`,
  `retry_scheduled`, `superseded`, `failed`) with `finished_at`. `advance`
  refuses a lower counter, a lost warning, an older heartbeat
  (`ProgressWentBackwards`) and any report after the end (`AttemptFinished`).
  `outcome_of(job, warnings)` derives the outcome from the finished Job.
- **Storage.** Table `job_attempts` (migration `c9d0e1f2a3b4`), primary key
  `(job_id, attempt)`, FK to `jobs` `ON DELETE CASCADE`, CHECK constraints on
  stage, counters, warnings, outcome and times. `persistence.job_attempts`:
  `begin_attempt` (`ON CONFLICT DO NOTHING`, `AttemptAlreadyBegun`),
  `advance_attempt` and `finish_attempt`. Every save is an `UPDATE` whose
  `WHERE` repeats the monotonic rules, with a rowcount check, so the database
  refuses a backward write even without the domain check.
- **Who may write.** `lock_running_job` takes `FOR SHARE` only on a running
  Job, so a refused report never holds back the relay's `SKIP LOCKED`; a
  report for an attempt the Job is not running raises `AttemptNotCurrent`;
  `finish_attempt` refuses when a newer attempt exists. A report more than
  5 minutes ahead of the database clock raises `ReportFromTheFuture`.
- **Ending.** `finish_attempt` locks the Job, uses the Job's `finished_at`,
  pulls the heartbeat down to it and merges final counters by per-field
  maximum, so a finish never fails on what a report stored.
- **Reporting.** `job_reporting.report_progress` commits each report in its
  own transaction; totals, not increments, so a replay is harmless.
  `DatabaseJobReports.load` reads the Job and its newest attempt under
  `REPEATABLE READ`. The purge records its attempt when it ends.
- **API.** `GET /v1/jobs/{job_id}` requires `start_synchronization_runs`
  before parsing the ID, shows only `synchronize_source` Jobs, and gives the
  same 404 `job_not_found` for a missing Job and any other kind.

Changed files: `src/knowledge_service/{job_progress,job_reporting,app,contracts}.py`,
`src/knowledge_service/persistence/{job_attempts,jobs,schema}.py`,
`src/knowledge_service/ingestion/deletions.py`,
`migrations/versions/c9d0e1f2a3b4_job_attempt_progress.py`,
`tests/{test_job_progress,test_job_progress_integration,test_app_authentication}.py`,
`docs/{architecture,operations,threat-model}.md`.

## Verification

Narrow checks (2026-11-23):

- `uv run pytest tests/test_job_progress.py -q`: 33 passed.
- `uv run pyright src/knowledge_service`: 0 errors.
- `uv run pytest tests/test_indexes.py -q`: 4 passed.
- `uv run pytest tests/test_migrations_integration.py -q -m integration`: 3 passed.
- Deletions and retry integration tests: 27 passed.
- `uv run pytest tests/test_app_authentication.py -q`: 107 passed.
- `uv run pytest tests/test_job_progress_integration.py -q -m integration`: 22 passed.

Deliberate break (Step 6): with the counter check removed from
`JobProgress.advance`, `tests/test_job_progress.py` and
`tests/test_job_progress_integration.py` gave `3 failed, 52 passed`: the
three cases of `test_a_counter_never_goes_down`. The integration test of a
lower counter still passed, because the guarded `UPDATE` refuses it.
Restored: `55 passed`. This matches the prediction.

Gates (2026-11-23), one after another on one test database:
`make format-check` (341 files already formatted), `make lint` (all checks
passed), `make typecheck` (0 errors), `make test-unit` (3175 passed),
`make test-integration` (819 passed), `make docs-check` (exit 0),
`git diff --check` (clean).

## Review

An adversarial review attacked the prototype on real PostgreSQL in three
rounds before the guide was written. Each break was fixed with a test that
fails without the fix:

- A worker clock ahead, or final counters below a late report, could stop an
  attempt from ever ending; now the finish uses the Job's time, pulls the
  heartbeat down and merges counters.
- A report far in the future poisoned the heartbeat; now it is refused.
- A refused report held a share lock that kept the relay from requeuing a
  due retry; now only running Jobs are locked.
- The API read the Job and its attempt at two moments; now one snapshot.
- A stale worker could finish an old attempt after a newer one began; now
  refused.
- Untyped crashes on wrong types, a raw `IntegrityError` on a double begin,
  an attempt number above int32, and a non-UTC session time zone are now
  typed or handled; the handler rechecks the Job kind itself.

Closing reviews (standards and spec, each on its own database and Redis db):
nothing blocking. Non-blocking observations left as they are:

- `DatabaseJobReports.load` sets the isolation level with raw `SET
  TRANSACTION`, which must stay the first statement; elsewhere the repo uses
  `execution_options(isolation_level=...)`.
- `_save` builds the row twice to read its warnings.
- `succeeded_with_warnings`, `superseded` and `failed` are proven through
  `outcome_of` only; no test stores them through `finish_attempt`.
- No HTTP test reads a finished attempt (outcome, `finished_at`, the Job's
  `error_code` and `retry_at`).
- No test covers a report for a running Job that has no attempt row.
- `docs/architecture.md` says a handler stores the row with its claim
  (`begin_attempt`); no handler does yet, and the purge records its attempt
  only when it ends.
- `create_app` gets no `job_reports` by default, so the endpoint answers 503
  until a caller passes `DatabaseJobReports`, as with Conversations.

Known limits:

- No production handler reports progress until M08-T10.
- A dead or cancelled attempt stays `running` with its last heartbeat until
  M08-T08.
- A worker clock more than five minutes ahead of the database cannot report.
- `save_job` has no status guard (from before): a stale worker that saves the
  same failure again after its Job was queued again sets it back to `failed`.
- Merged counters can mix two reports: (10, 0) and a final (5, 5) end as
  (10, 5).
- Reading `jobs` rows under a non-UTC session time zone still fails (from
  before).

## Decision

Keep progress in PostgreSQL, one row per attempt, and commit each report on
its own, so it outlives the worker and the API reads it from the same place
the retry and recovery code reads. Reports carry totals, so a replay is
harmless, and the monotonic rules live twice: in the domain for a clear
typed error, and in the `UPDATE ... WHERE` so a buggy or stale writer is
still refused. Only the attempt the Job is running may write, and ending an
attempt adapts to what reports stored instead of failing on it.

## Reflection

Drafted by Claude at the learner's request, grounded in the implementation
and the reviews; the learner should rewrite these answers in their own words.

- *Killed at 10:06, read at 10:30:* the Job is `running`, attempt shows 120
  processed and `heartbeat_at` 10:05. The 25-minute-old heartbeat is the sign
  nobody is working on it; M08-T08 acts on it.
- *50 after 60:* `JobProgress.advance` refuses it with
  `ProgressWentBackwards`, and the `UPDATE ... WHERE processed <= 50`
  matches no row. The second matters when the domain check is skipped or
  broken, as the Step 6 break showed: the database still refused 50.
- *Totals, not increments:* a replayed "120 so far" writes the same row
  again; a replayed "+20" would count 20 documents twice and nothing could
  tell.
- *Attempt 1 after attempt 2 began:* the Job's attempt number no longer
  matches, so `AttemptNotCurrent`. Only running rows are locked so a refused
  report never holds a lock the relay's `SKIP LOCKED` would have to skip,
  which would delay a due retry.
- *After the caller believes it succeeded:* the last report committed, then
  the worker died before `finish_attempt`; the API shows all pages done but
  the Job stays `running` until M08-T08 recovers it.
- *Both checks removed:* `test_a_counter_never_goes_down` in the unit file
  fails first, then `test_a_lower_counter_is_refused_and_the_stored_one_stays`
  and the racing-reports test in the integration file.
