# M08-T09 — Commit Source checkpoints safely evidence

## Invariant

A Source's checkpoint moves past a page only in the transaction that also
stores the page's offered external IDs, forgets the ones it reported
deleted, and records the checkpoint as used, and that transaction commits
only after every record of the page is reconciled. A pass that dies at any
point resumes on its next attempt from its last committed page, if the pass
started at most `MAX_RESUME_AGE` ago; its end-of-pass tombstones count every
page any attempt committed. Only the pass that holds the Source's fence can
write progress, and a checkpoint the pass stored before is never stored
again.

## Owners

- Durable progress: PostgreSQL (`sources.checkpoint`,
  `sources.pass_started_at`, `synchronization_seen`,
  `synchronization_checkpoints`).
- Page commit and resume: `ingestion.checkpoints`.
- Retries of a failed attempt: the stored Job (M08-T04) and recovery
  (M08-T08); a retry is a new attempt of the same pass, with a later
  observation time.
- Write authority: the Source's fence (M08-T05).

## Expected failing check

Before the change,
`uv run pytest tests/test_checkpoints_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.
Observed on 2026-11-24: `no tests ran in 0.10s`.

## Predictions

Claude wrote these while preparing the lesson, before the final
implementation; they are not blind predictions by the learner.

- A pass dies while fetching page 3 of 3. Which page does the retry read
  first: page 3 (checkpoint `"2"`); pages 1 and 2 are not read again.
- The same pass, but the seen list is kept in memory only. Which Documents
  does the retry tombstone: every available Document not on page 3, including
  the live `a.txt` and `b.txt`.
- How many tests fail if the page commit runs before the page's records are
  reconciled: the tests that crash inside a page; observed 3 (see
  Verification).
- A Source reports `x.txt` deleted on page 1 and offers it, unreadable, on
  page 2. Is `x.txt` available after the pass: yes; the last report wins, and
  nothing is tombstoned before the last page.

## Design

Changed files:

- `migrations/versions/e1f2a3b4c5d6_synchronization_progress.py`: adds
  `sources.pass_started_at`, the check `ck_sources_checkpoint_pass`
  (a checkpoint needs a pass start), and the tables `synchronization_seen`
  and `synchronization_checkpoints`; clears stray checkpoints both ways.
- `src/knowledge_service/persistence/schema.py`: the same shapes.
- `src/knowledge_service/persistence/synchronization_progress.py` (new):
  `start_or_resume_pass`, the fenced `advance_checkpoint` and `end_pass`,
  `remember_checkpoint`, batched `record_seen` and `forget_seen` (1,000 IDs
  per statement), `forget_pass`, `load_unseen_available`.
- `src/knowledge_service/ingestion/checkpoints.py` (new): `begin_pass`
  (claims the fence and resumes or starts in one transaction),
  `commit_page`, `finish_pass`, `MAX_RESUME_AGE` (one hour).
- `src/knowledge_service/ingestion/deletions.py`: `_tombstone` extracted;
  `tombstone_missing` unchanged in behaviour; `tombstone_unseen` reads the
  stored seen list inside the caller's transaction.
- `src/knowledge_service/ingestion/synchronization.py`: `synchronize_source`
  begins through `begin_pass`, commits each page after its narrowing and
  reconciliation and before the heartbeat, and finishes through
  `finish_pass`; the in-memory `seen` and `used` sets are gone;
  `SynchronizationSummary.resumed` and the audit metadata key `resumed`.
- `tests/test_checkpoints_integration.py` (new, 20 tests);
  `tests/test_audit_events_integration.py` expects `"resumed": False`.
- `docs/architecture.md`, `docs/operations.md`, `docs/security.md`.

Behaviour proved on real PostgreSQL, by crashing a pass at each boundary
and running it again with a later observation time:

| Boundary | Test | Result |
|---|---|---|
| after a page commit, before the next fetch | `test_a_pass_resumes_after_the_last_committed_page` | retry reads only `"2"` |
| after the records, before the page commit | `test_a_page_reconciled_but_not_committed_is_replayed_harmlessly` | page replayed, `UNCHANGED` |
| two records, one landed | `test_a_page_of_two_records_is_replayed_after_one_landed` | page replayed |
| inside a record's reconciliation | `test_the_checkpoint_never_passes_a_record_that_failed_to_reconcile` | checkpoint stays `"1"` |
| inside a page's narrowing | `test_a_crash_while_narrowing_a_page_replays_it` | page replayed |
| inside a page commit | `test_a_page_commit_is_all_or_nothing` | checkpoint move rolled back |
| after the last page commit | `test_a_resumed_pass_does_not_tombstone_what_an_earlier_attempt_saw` | only `old.txt` tombstoned |
| inside the finish | `test_a_failed_finish_tombstones_nothing_and_keeps_the_progress` | nothing tombstoned; resumed |
| after the finish, before purge requeue | `test_a_crash_after_the_finish_starts_the_next_pass_over` | next pass starts over |
| cancellation | `test_a_cancelled_pass_resumes_like_a_crashed_one` | resumed from `"2"` |
| deletions across a crash | two tests | tombstoned at the end |
| last report wins | `test_an_item_deleted_then_offered_unparseable_stays_available` | stays available |
| denial | older pass; repeated checkpoint within and across attempts | refused, nothing stored |
| bounds | stale progress; 40,000-ID page; migration down and up | as designed |

## Verification

Narrow check (2026-11-24):
`uv run pytest tests/test_checkpoints_integration.py -q -m integration`:
20 passed.

Deliberate break (guide Step 8): with the page commit moved above the
reconcile loop in `_publish`,
`tests/test_checkpoints_integration.py tests/test_synchronization_integration.py`
gave `3 failed, 40 passed`:
`test_a_page_reconciled_but_not_committed_is_replayed_harmlessly`,
`test_the_checkpoint_never_passes_a_record_that_failed_to_reconcile`, and
`test_a_page_of_two_records_is_replayed_after_one_landed`. The 23 older
synchronization tests never crash mid-page and still passed. Restored:
`43 passed`. Other mutations each failed at least one new test: no durable
seen list (9 tests in the first version), never resuming, no age bound, an
unfenced page commit, no batching, no durable repeat check, no
forgetting of reported deletions, per-page tombstoning.

Gates (2026-11-24), one after another on one test database:
`make format-check` (352 files already formatted), `make lint` (all checks
passed), `make typecheck` (0 errors), `make test-unit` (3266 passed),
`make test-integration` (868 passed), `make docs-check` (exit 0),
`git diff --check` (clean).

Security and privacy: no new log line; the audit event gains only the
boolean `resumed`. The seen list stores external IDs, which `documents`
already stores. No network call, model call, or paid service was used.

## Review

An adversarial review of the first prototype found three breaks, each now
fixed with a test that fails without the fix:

1. Per-page tombstoning broke "the last report wins": an item reported
   deleted on page 1 and offered, unreadable, on page 2 ended tombstoned.
   Now a page commit only forgets reported deletions; tombstones wait for
   `finish_pass`.
2. A repeated checkpoint was stored by the next attempt, because the used
   set lived in memory. Now `synchronization_checkpoints` refuses a repeat
   across attempts, inside the page commit, so it is never stored.
3. A page of about 33,000 IDs exceeded PostgreSQL's 65,535 parameters per
   statement and could never commit. Now IDs are written 1,000 at a time.

Attacks that held: random page layouts with random crash points converged
to the expected state; 40 rounds of three overlapping passes with a
scheduler share-locking the same row ended without a deadlock; a superseded
pass's commit and finish wrote nothing.

The closing Standards and Spec reviews found nothing blocking. Applied from
them: a false docstring in `synchronization_progress.py` (only
`advance_checkpoint` and `end_pass` check the fence), `_revoke_first` takes
a required guard, clearer `deletions.py` wording, and four boundary tests
(narrowing, two records per page, cancellation, after the finish).

Known limits:

- A resumed pass does not re-read pages before its checkpoint, so a change
  there, including a removed reader, waits up to about an hour plus the
  synchronization interval (`docs/security.md`).
- `MAX_RESUME_AGE` counts from the pass's first attempt; a pass that keeps
  failing for longer starts over on every retry.
- Every retry must use a later `observed_at`; reusing the Job's original
  time is refused by the fence. The M08-T10 handler must follow this.
- A checkpoint must stay meaningful for an hour; an offset-based Source
  could shift. M08-T12 should use stable, key-based tokens.
- Summary counts cover one attempt; `resumed` marks a partial view.
- A cancelled pass records no `synchronization.finished` event, as before.
- `tombstone_missing` has no production caller now; tests and direct
  callers keep it.

## Decision

Keep the progress in PostgreSQL next to the fence: the resume point on
`sources`, the seen list and used checkpoints in two tables keyed by Source.
Each page commit is one transaction whose first statement is the fenced
`UPDATE`, so it checks ownership and takes the row's write lock at once,
avoiding a share-then-update lock upgrade. Records keep their own short
commits, so a removed reader still loses access before the page's slow
work; replaying them is harmless because reconciliation compares
fingerprints. Tombstones wait for the end of the pass, where the last
report of every item is known.

What would falsify the design: any crash test where a record is missing
after the retry, a Document tombstoned that some attempt of the pass saw,
or a superseded pass whose write lands.

## Reflection

Claude drafted these answers; the learner should rewrite them in their own
words.

- Which failure can occur after the caller believes this work succeeded? A
  pass that resumed finished successfully, but the Source changed a page the
  resume skipped; that change, even a removed reader, waits for the next
  pass, up to about two hours by default.
- How does the design behave under replay, cancellation, or authorization
  change? Replay of an uncommitted page writes nothing new. Cancellation
  rolls back the open transaction, and the next attempt resumes from the
  last page commit. A newer pass that claims the fence makes every later
  write of the older pass fail with `SynchronizationSuperseded`.
- Which metric or contract test protects future refactoring?
  `tests/test_checkpoints_integration.py`, especially
  `test_the_checkpoint_never_passes_a_record_that_failed_to_reconcile` for
  the order and
  `test_a_resumed_pass_does_not_tombstone_what_an_earlier_attempt_saw` for
  the durable seen list; the audit key `resumed` shows how often passes
  resume.
