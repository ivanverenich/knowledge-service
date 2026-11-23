# M08-T05 — Coordinate with expiring locks evidence

## Invariant

At most one synchronization pass of a Source runs at a time while its lease
holds; a lease expires on its own and only its token's holder can renew or
release it. Whatever the lease does, only the pass with the newest
observation time may write a Source's Documents: every narrowing,
reconciliation, and tombstone checks the Source's fence in PostgreSQL inside
its own transaction, under a share lock, so no write of an older pass lands
after a newer pass claimed the fence.

## Owners

- Avoiding duplicate work: the Source's lease in Redis (`leases`,
  `ingestion.exclusive_sync`).
- Correctness: the Source's fence in PostgreSQL (`synchronize_source`).
- Retries: the stored Job (M08-T04); a superseded pass is never retried.
- Authorization: none; nothing outside the service starts a pass.

## Expected failing check

Before the change,
`uv run pytest tests/test_leases_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.
Observed on 2026-11-23: `no tests ran in 0.12s`.

## Predictions

Written by Claude before the change, at the learner's request; not blind,
because Claude built and measured the prototype the guide comes from.

- What a second pass gets while another pass of the same Source holds the lease: `SourceBusy`, before it reads anything; nothing is written and no audit record is made.
- What happens to a lease whose holder crashed, and after how long: the key expires by itself after its TTL, two minutes by default, and the next pass runs.
- What an older pass that wakes up after a newer pass finished does when it tries to tombstone: its tombstone transaction finds the fence holds the newer time, raises `SynchronizationSuperseded`, and rolls back; nothing is tombstoned.
- How many tests fail if `synchronize_source` stops passing its guard to the writes: 3, the tests where an older pass wakes up after a newer one.

## Design

- **Lease, for efficiency.** `leases.py`: `RedisLeaseStore` takes a lease with
  `SET key token NX PX ttl`, and renews or releases it with a Lua script that
  compares the holder's random token inside Redis. Names and TTLs are
  checked (also on `renew`), calls are bounded by 2-second timeouts with no
  hidden retries, and Redis failures become `LeaseStoreUnavailable`.
  `ingestion.exclusive_sync.synchronize_source_exclusively` takes the
  Source's lease (`source-sync:<id>`, 2 minutes) before reading, renews it
  after every page through `synchronize_source`'s `heartbeat`, and releases
  it in `finally`. Its errors (`SourceBusy`, `SourceLeaseLost`,
  `SourceCoordinationUnavailable`) are `SourceUnavailable`, so a Job retries.
- **Fence, for correctness.** `sources.synchronization_fence` (migration
  `a7b8c9d0e1f2`). Every `synchronize_source` pass refuses an `observed_at`
  more than five minutes ahead, claims the fence with a conditional UPDATE
  (only a newer time wins), and passes a guard to `narrow_access`,
  `reconcile_document`, and `tombstone_missing`; each runs it first in its
  transaction as `SELECT ... FOR SHARE` on the Source's row and refuses with
  `SynchronizationSuperseded` when the fence moved. A newer claim waits for
  those share locks, so no write of an older pass lands after it.
- **Retries.** `SynchronizationSuperseded` has its own permanent code,
  `superseded`; a retry must observe the Source again.
- Only synchronization takes a lease: purges, retries, the relay, and
  `enqueue_job` already have one effect through row locks, `SKIP LOCKED`, and
  the open-intent index.

Changed files: `src/knowledge_service/leases.py`,
`src/knowledge_service/ingestion/{exclusive_sync,fencing,synchronization,reconciliation,deletions,sources}.py`,
`src/knowledge_service/persistence/{sources,schema}.py`,
`src/knowledge_service/job_failures.py`,
`migrations/versions/a7b8c9d0e1f2_source_synchronization_fence.py`,
`tests/{test_leases,test_leases_integration,test_exclusive_sync_integration,test_job_failures}.py`,
`docs/{architecture,threat-model,operations}.md`.

## Verification

Narrow checks (2026-11-23):

- `uv run pytest tests/test_leases_integration.py -q -m integration`: 9 passed.
- `uv run pytest tests/test_migrations_integration.py -q -m integration`: 3 passed.
- `uv run pyright src/knowledge_service/ingestion`: 0 errors;
  `uv run pytest tests/test_deletions_integration.py -q -m integration`: 9 passed.
- `uv run pytest tests/test_job_failures.py -q`: 41 passed;
  `uv run pytest tests/test_synchronization_integration.py -q -m integration`: 23 passed.
- `uv run pytest tests/test_leases.py -q`: 26 passed.
- `uv run pytest tests/test_exclusive_sync_integration.py -q -m integration`: 13 passed.

Deliberate break (Step 8): with `guard=None` passed to `_publish`,
`tests/test_exclusive_sync_integration.py` and
`tests/test_synchronization_integration.py` gave `3 failed, 33 passed`: the
three tests where an older pass wakes up after a newer one. Restored:
`36 passed`. This matches the prediction.

Gates (2026-11-23), one after another on one test database and Redis db 15,
run again after the review repair: `make format-check` (331 files already
formatted), `make lint` (all checks passed), `make typecheck` (0 errors),
`make test-unit` (3113 passed), `make test-integration` (775 passed),
`make docs-check` (exit 0), `git diff --check` (clean).

## Review

An adversarial review attacked the prototype three times on real PostgreSQL
and Redis before the guide was written.

- First round: the lease-only design was not safe. An older pass that paused
  after its last renewal tombstoned a Document a newer pass had seen
  unchanged (its `updated_at` stayed old, so the `observed_at` guard in
  `tombstone_missing` did not fire), and an older pass whose first page
  arrived late re-granted a reader the newer pass had removed. Both existed
  in `synchronize_source` before; the fence fixes them, with tests that fail
  without it. A hand-built `Lease` with a zero or ten-year TTL could delete
  or pin a key through `renew`; `renew` now checks the TTL.
- Second round: a retry with the same `observed_at` was classified transient
  but could never succeed (now `superseded`, permanent), and an unfenced
  plain `synchronize_source` pass could be overwritten by an older fenced
  one (the fence is now part of every pass). A far-future `observed_at`
  could have held the fence indefinitely (now refused).
- Third round: nothing broke; a stress run of 87 overlapping passes with
  purges always ended in the newest pass's state, with no deadlocks, and the
  per-write share lock cost nothing measurable.

Closing reviews: the standards review found one blocking issue, a re-export
of `SynchronizationSuperseded` through `__all__` in `exclusive_sync`, which
ADR 0013 forbids. It was removed (tests import from `ingestion.sources`),
with a schema comment that pointed at the wrong module and a hard-coded key
prefix in a test; the guides were updated to match and all gates rerun.
The spec review found nothing blocking and no drift. Non-blocking
observations left as they are:

- A `source_id` with no `sources` row is reported as `superseded` instead of
  an unknown Source (the claim updates no row).
- No test drops only the `narrow_access` guard; narrowing by an older pass
  only removes readers, so this is a coverage gap, not a leak.
- `docs/operations.md` describes retrying a synchronization Job, which no
  handler runs yet (M08-T06/T10); that handler must take a new `observed_at`
  per attempt.
- Lease and expiry tests depend on wall-clock timing against short TTLs
  (150 ms margin); `SynchronizationSuperseded` subclasses `SourceError`,
  whose docstring speaks of adapters; one architecture line breaks the
  paragraph's wrap; the migration's create date is a day ahead.

Known limits:

- Cancelling a pass while it takes the lease can leave the key held by
  nobody for up to its TTL; a cancellation during release replaces the
  pass's own error. Both are bounded.
- `SourceBusy` uses one attempt of a Job's retry budget.
- A page longer than the lease's TTL loses the lease; the pass stops at the
  next renewal and the work is repeated, the data stays right.
- Writes an older pass committed before a newer pass claimed the fence stay
  until the newer pass overwrites them, and stay if it fails part-way.
- An `observed_at` up to five minutes ahead holds the fence until then.
- The fence holds as long as every write path of a pass runs the guard;
  a database trigger would enforce it without relying on the code.

Security: the fence closes a path where a stale pass re-granted a removed
reader. Lease tokens, Redis URLs, and error messages are never logged; the
one new warning carries the Source ID and the error type only.

## Decision

Use a Redis lease only to avoid duplicate synchronization work, and put
correctness in PostgreSQL with a fence (a fencing token) that every write of
a pass checks inside its own transaction under a share lock. A lease cannot
stop a holder that paused after its last check; only the place the data
lives can refuse a stale write. Use the observation time as the token, so
"newer" means "saw the Source later".

## Reflection

Drafted by Claude at the learner's request, grounded in the implementation
and the reviews; the learner should rewrite these answers in their own words.

- *A crashed holder?* At once B gets `SourceBusy`; after the two-minute TTL
  the key has expired and B runs. `PX` in `acquire`'s `SET` makes the
  difference.
- *Why one Lua script?* As two commands, A can read its own token, pause
  while the lease expires and B takes it, then delete B's key.
- *Why is a successful renewal not enough?* It is true only at that moment;
  the pass can pause after it, the lease expires, a newer pass runs, and the
  write still happens later.
- *Can the claim land between check and write?* No: the check is
  `SELECT ... FOR SHARE` in the writing transaction, and the claim's UPDATE
  waits for that lock until the write commits.
- *Superseded vs busy?* Busy may succeed later; superseded never can with the
  same `observed_at`, and a newer pass already covers the work. A retry must
  observe the Source again.
- *Signs the fence is not holding?* A Document tombstoned, or a grant
  restored, by a pass whose audit run started before the Source's current
  `synchronization_fence`.
