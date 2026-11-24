# M08-T10 — Orchestrate full and incremental sync evidence

## Invariant

The worker runs every queued `synchronize_source` Job through
`ingestion.synchronization_jobs`: it claims the Job in its own transaction,
reads the Source the Job names, and runs the resumable pass observed at the
attempt's start, reporting the attempt's totals after every committed page.
It stores the end only while the Job still runs that attempt. A full pass
tombstones every available Document it never saw offered; an incremental
pass, only for a Source with a change feed, a stored cursor, and a full pass
within the full-pass interval, reads only changes after the cursor and
tombstones only what the Source reported deleted. A missing or disabled
Source, or one no adapter can open, fails for good; an unavailable Source,
database, or lease is retried, and the retry resumes.

## Owners

- Running a Job: the worker, through `run_stored_job` and the handler map
  built by `worker.handlers.job_handlers`.
- Claim, progress, and finish: `ingestion.synchronization_jobs`.
- Mode, cursor, and progress of a pass: `ingestion.checkpoints` and
  PostgreSQL (`sources`, `synchronization_seen`).
- Retries and recovery: the stored Job (M08-T04, M08-T08).
- Authorization of local-folder items: the Source row's `default_public` and
  `default_principals` (nobody by default); principals resolve through the
  principal directory file.

## Expected failing check

Before the change,
`uv run pytest tests/test_synchronization_jobs_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.
Observed on 2026-11-24 in a clean checkout of `31571ce`:
`no tests ran in 0.62s`.

## Predictions

Claude wrote these while preparing the lesson, before the final
implementation; they are not blind predictions by the learner.

- A Source has a change feed and a cursor. Its incremental pass gets one
  edited item and one deleted item. An unchanged item was not offered. Which
  Documents are tombstoned: only the deleted item; the edited one is updated,
  the unchanged one is untouched.
- The worker sleeps for six minutes mid-pass, recovery fails the attempt,
  then the worker wakes and finishes. What does the Job's row say
  afterwards: recovery's outcome (`worker_lost`, retry planned); the late
  worker's end is not stored.
- How many tests in the new suite fail if the finish skips the "still
  running this attempt" check: 1 (observed, see Verification).
- In the drill, after deleting one file and waiting for the next scheduled
  pass, what does `jobs` contain: two succeeded `synchronize_source` Jobs and
  one queued `purge_document_chunks` Job.

## Design

Changed files:

- `migrations/versions/f2a3b4c5d6e7_synchronization_modes.py` (new):
  `sources.default_public`, `sources.default_principals`
  (`ck_sources_default_access`), `sources.sync_cursor`,
  `sources.last_full_pass_at`, `sources.pass_mode`
  (`ck_sources_pass_mode`; a pass already in progress becomes `full`), and
  `synchronization_seen.deleted`. The downgrade drops incremental progress
  and deleted seen rows first, so every check holds both ways.
- `src/knowledge_service/persistence/schema.py`: the same shapes.
- `src/knowledge_service/ingestion/sources.py`: `SourcePage.cursor` (last
  page only), the optional `ChangeFeed` protocol with `fetch_changes`,
  `SyncMode`, and the typed errors `ObservedInTheFuture`, `SourceGone`,
  `SourceMisconfigured`.
- `src/knowledge_service/job_failures.py`: `clock_ahead` (transient),
  `source_gone` and `source_misconfigured` (permanent).
- `src/knowledge_service/persistence/synchronization_progress.py`:
  `PassProgress` with the mode and cursor; `start_or_resume_pass` picks the
  mode; `end_pass` stores the cursor and, for a full pass,
  `last_full_pass_at`; `record_reports` keeps each ID's last report.
- `src/knowledge_service/persistence/sources.py`: `SourceRow`,
  `load_source_row`.
- `src/knowledge_service/ingestion/deletions.py`:
  `tombstone_reported_deleted`.
- `src/knowledge_service/ingestion/checkpoints.py`: `FULL_PASS_INTERVAL`
  (24 hours), `mode_of`, mode-aware `begin_pass` and `finish_pass`.
- `src/knowledge_service/ingestion/synchronization.py`: reads with
  `fetch_page` or `fetch_changes` by mode, calls `on_page` after every page
  commit, refuses an incremental pass without a new cursor, and adds `mode`
  to the summary and the audit event.
- `src/knowledge_service/ingestion/exclusive_sync.py`: passes the new
  options through.
- `src/knowledge_service/ingestion/synchronization_jobs.py` (new):
  `SynchronizationJobs` (claim, run, report, finish) and `open_source`.
- `src/knowledge_service/settings.py`:
  `full_synchronization_interval_hours`, `principal_directory_file`.
- `src/knowledge_service/worker/handlers.py` (new), `worker/jobs.py`
  (`RUNNABLE_KINDS`, per-queue time limits: synchronization 30 minutes),
  `worker/main.py`, `worker/relay.py`, `ingestion/scheduling.py`
  (docstring).
- Tests: `tests/test_synchronization_jobs_integration.py` (new, 25),
  `tests/test_synchronization_job_wiring.py` (new, 12); updated
  `test_checkpoints_integration.py`, `test_audit_events_integration.py`,
  `test_worker.py`, `test_job_failures.py`.
- `docs/architecture.md`, `docs/operations.md`, `docs/threat-model.md`.

Behaviour proved through the Job handler, with a fake Source on real
PostgreSQL and Redis:

| Case | Test | Result |
|---|---|---|
| empty | `test_an_empty_source_succeeds_with_nothing_stored` | succeeded, nothing stored |
| multi-page | `test_a_multi_page_source_is_stored_and_its_progress_reported` | all pages stored, progress per page |
| update | `test_an_update_changes_only_the_record_that_changed` | one changed |
| delete | `test_a_file_the_source_no_longer_lists_is_tombstoned` | tombstoned, purge queued |
| retry | `test_a_failed_attempt_is_retried_and_resumes_from_its_checkpoint` | retry resumes |
| cancellation | `test_a_cancelled_attempt_is_recovered_and_its_retry_resumes` | recovery fails it; retry resumes |
| replay | `test_a_second_message_for_a_finished_job_runs_nothing` | nothing runs |
| late worker | `test_an_attempt_ended_by_recovery_keeps_recoverys_outcome` | recovery's outcome kept |
| denial | disabled, missing, or unopenable Source; NULL principal | failed for good |
| incremental | five tests: changes after the cursor only, full pass after the interval, resume keeps the mode, no new cursor fails and keeps the cursor, a lost feed starts over in full | as designed |
| clocks | stepping back between pages; Job queued far ahead | no failure; retried |
| migration | `test_the_migration_goes_down_and_up_with_passes_in_progress` | down and up |

## Verification

Narrow check (2026-11-24):
`uv run pytest tests/test_synchronization_jobs_integration.py tests/test_synchronization_job_wiring.py -q -m "integration or not integration"`:
37 passed.

Deliberate break (guide Step 10): with `_finish`'s check changed to
`if False and (`,
`uv run pytest tests/test_synchronization_jobs_integration.py -q -m integration`
gave `1 failed, 24 passed`:
`test_an_attempt_ended_by_recovery_keeps_recoverys_outcome`. Restored:
`25 passed`. While preparing the lesson, other mutations each failed at
least one test: no claim check, incremental tombstoning of unseen items,
never incremental, no progress report, no `last_full_pass_at`, and each of
the six review fixes undone.

Drill (guide Step 11), run while preparing the lesson on the same code, with
a separate database `knowledge_service_drill` and Redis database 12:
`make worker-relay` scheduled and published one Job; `make worker` ran it:
one succeeded `synchronize_source` Job, attempt `processed` 2, `changed` 2,
both files available. After deleting `leave.md` and setting the interval to
one minute, the second pass succeeded, tombstoned `leave.md`, and queued a
`purge_document_chunks` Job. The drill database and Redis database were
removed afterwards.

Gates (2026-11-24), one after another on one test database:
`uv run ruff format --check .` (357 files already formatted),
`uv run ruff check .` (all checks passed), `uv run pyright` (0 errors),
`make test-unit` (3278 passed), `make test-integration` (893 passed),
`make docs-check` (exit 0), `git diff --check` (clean),
`uv run pre-commit run --all-files` (all passed).

Security and privacy: Jobs still carry only the Source ID; location,
readers, and progress come from PostgreSQL. A local folder's items are
readable by nobody unless the Source row says otherwise. No new log line
carries content or principals; the audit event gains only `mode`. No
network call, model call, or paid service was used. External calls are
bounded by the page size, the lease renewed per page, and the 30-minute
actor time limit.

## Review

An adversarial review of the prototype found six breaks, each now fixed with
a test that fails without the fix:

1. The downgrade violated a check when an incremental pass was in progress.
   It now drops incremental progress first.
2. Warning codes went backwards when a later report resolved an earlier
   warning, so the progress report was refused. Warnings now only
   accumulate within an attempt.
3. A clock stepping back between pages made the heartbeat go backwards. The
   heartbeat is now the later of the clock and the last report.
4. A Job queued by a clock six minutes ahead failed for good as a programming
   error. It is now `clock_ahead`, retried.
5. An incremental pass resumed after its Source lost its change feed. It now
   starts over in full.
6. A NULL default principal crashed as a programming error. It is now
   `source_misconfigured`, final.

The closing Standards and Spec reviews found nothing blocking. Their
non-blocking notes, left unchanged so the code matches the lesson guide:

- The `SourceMisconfigured` docstring mentions "a folder that does not
  exist", but a missing folder is `SourceUnavailable` and retried.
- `_fetch` picks its reader by `progress.since is None` rather than
  `mode_of(progress)`; they differ only after a manual database edit.
- The relay's `JobRunner` keeps the default handlers; it only publishes, so
  it never runs a synchronization Job.
- `SourceId(job.target_id)` is built twice in `SynchronizationJobs`.

Known limits:

- Only the fake test Source has a change feed; `DirectorySource` is always
  read in full. No production adapter implements `fetch_changes` yet.
- Between full passes, a deletion or a permission change the feed does not
  report waits for the next full pass (24 hours by default).
- A page that takes longer than five minutes makes a healthy worker look
  dead to recovery.
- A synchronization message is cut off after 30 minutes; its retry resumes
  from the checkpoint. Because `MAX_RESUME_AGE` (one hour) counts from the
  pass's first start, a Source that needs more than about an hour of reading
  starts over again and again and never finishes.
- A change-feed Source whose full pass ends without a cursor stores none, so
  its next pass is full again; this is safe but not reported.
- A Source refusing access (`source_refused`) is classified final in
  `tests/test_job_failures.py`, but no test drives it through the handler.

## Decision

One module owns a synchronization Job from claim to finish, and the pass
itself chooses its mode from durable state: the Source's capability, the
stored cursor, and the last full pass time. Incremental tombstones only
trust explicit deletions, and a periodic full pass repairs whatever a feed
missed. The end is stored under the Job's row lock only when the Job still
runs that attempt, so recovery's decision always wins over a late worker.

What would falsify the design: a Job whose stored outcome does not match the
attempt actually running, an incremental pass that tombstones an item the
Source did not report deleted, or a Source with a feed that never gets a
full pass.

## Reflection

Claude drafted these answers; the learner should rewrite them in their own
words.

- Which failure can occur after the caller believes this work succeeded? An
  incremental pass succeeds, but the feed missed a deletion or a removed
  reader; it stays until the next full pass, up to 24 hours later.
- How does the design behave under replay, cancellation, or authorization
  change? A replayed message finds the Job no longer queued and runs
  nothing. A cancelled attempt records nothing; recovery fails it and the
  retry resumes from the checkpoint. A removed reader applies on the page
  that reports it, or at the next full pass.
- Which metric or contract test protects future refactoring?
  `tests/test_synchronization_jobs_integration.py`, especially
  `test_an_attempt_ended_by_recovery_keeps_recoverys_outcome` and
  `test_an_incremental_pass_reads_only_changes_after_the_cursor`; the audit
  key `mode` shows how many passes run in each mode.
