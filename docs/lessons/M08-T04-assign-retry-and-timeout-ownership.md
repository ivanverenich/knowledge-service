# M08-T04 — Assign retry and timeout ownership

Task: [M08-T04 — Assign retry and timeout ownership](../curriculum/milestones/M08-jobs-and-confluence/M08-T04-assign-retry-and-timeout-ownership.md)

## What you will do

This lesson decides who tries a failed Job again, when, and how many times. Today nothing does: a purge that fails because the database blinked stays failed forever.

A concrete example. At 10:00:00 the purge J1 runs, and the database drops the worker's connection: a failover, or a session ended by an administrator. The database itself answers again a moment later. After this lesson:

| Time | What happens to J1 |
|---|---|
| 10:00:00 | attempt 1 fails with "database unavailable", a kind of failure that may pass; through a fresh connection, the worker writes into J1's row a `retry_at` between 10:00:15 and 10:00:30, say 10:00:22 |
| 10:00:22 or later | the next `make worker-relay` pass sees the time has come, queues J1 again, and sends its message |
| 10:00:22 + | attempt 2 runs; if it fails the same way, the next wait is 30 to 60 seconds |
| after attempt 3 | the budget of 3 attempts is used up; J1 stays failed and is never retried |

If J1 had failed because of a bug (say a `KeyError`), it would stop after attempt 1, with no retry at all.

| What the task asks | Where it is done |
|---|---|
| Classify source, database, model, and programming failures | Step 3: `classify_job_failure` gives every error one of seven codes |
| Bounded retry at one layer | Steps 2, 5, 6: the stored Job's `retry_at`, set only by `fail_attempt`, and `requeue_due_retries`, the only path back to the queue |
| Backoff and jitter | Step 3: `RetryPolicy`, a doubling wait with a random spread |
| Permanent errors do not retry; temporary ones stop at the budget | Step 8: tests for both, plus the database check |
| Timeout ownership | Step 3: a contract test that the database gives up before the hard time limit; "Before you begin", block 5 |
| Replay or cancellation behaviour explicit | Step 8: a test of a cancelled attempt; the docstring of the purge |
| Update only affected documentation | Step 10: architecture, threat model, operations |

This lesson includes coding: Steps 2 to 8 write code, a migration, and tests. Steps 1, 9, 10, and 11 need no code, although Step 10 edits three documents.

Terms used in this lesson:

- **Transient failure:** a failure that may go away by itself, like a dropped connection. Trying again later can work.
- **Permanent failure:** a failure that trying again only repeats, like a wrong password or a bug. Someone must fix the cause first.
- **Retry budget:** the most attempts a Job may use; here `max_attempts`, 3 by default.
- **Backoff:** waiting longer after each failure, so a struggling service gets time to recover.
- **Jitter:** a random part of the wait, so Jobs that failed together do not all come back at the same second.
- **Timeout owner:** the one layer whose deadline should end a slow call. A deadline set too far out is useless; one set in two places fights itself.

## Before you begin

This section explains every choice the code rests on, with what happens today, step by step. Each block ends with what the code in this lesson changes.

### 1. Today, nobody tries a failed Job again

**The situation.** The purge J1 runs at 10:00:00. The database drops the worker's connection in the middle of the delete, then answers new connections at once.

**What happens today:**

| Time | Worker | Relay (`make worker-relay`) | What the `jobs` row says |
|---|---|---|---|
| 10:00:00 | runs J1; the delete raises an error | | `queued` |
| 10:00:00 | stores the failure (M08-T03 savepoint) | | `failed`, attempt 1 of 3 |
| 10:00:30 | | looks for `queued` Jobs with no message: none | `failed` |
| 11:00:00 | | still none | `failed`, forever |

**The hidden fact.** Every layer was built to *not* retry: Dramatiq has `max_retries=0`, the relay only sends `queued` Jobs, and the model adapters switch their SDK retries off. Each says "the caller owns retries", but no caller does yet. The only way J1 runs again today is a new request for the same work, which M08-T03 turns into "queue the failed Job again".

**How bad it is.** For purges, the Chunks of a deleted Document stay in the database until the same Document is deleted again, which may be never. Reads are still correct, because the Document is tombstoned. For synchronization, which later lessons build, a 30-second Confluence outage would stop a Source from syncing until someone noticed.

**What changes.** The failed Job records *when* it may run again, in a new column `retry_at`. A pass in `make worker-relay` queues every failed Job whose `retry_at` has come; the relay then sends its message as usual.

```python
# Before: a failure is just a failure.
failed = job.fail(error_code=PURGE_FAILED, finished_at=now)

# After: the failure is classified, and a retry is planned only if it may help.
failed = fail_attempt(job, error, now=now, policy=retry_policy)
# failed.retry_at is now + a delay, or None when the failure is final.
```

Think of a doctor's appointment card. A failed visit gets a new date written on the card, and the receptionist calls in everyone whose date has come. Nobody walks in early.

### 2. Not every failure deserves a retry

**The situation.** Three purges fail at 10:00:00 for three different reasons.

| Job | Error | What trying again does |
|---|---|---|
| J1 | the database dropped the connection | likely works in a minute |
| J2 | the database refused a row: a unique constraint broke | fails the same way every time |
| J3 | a `KeyError` in our code | fails the same way until the code is fixed |

**The hidden fact.** An error's type tells you which case you are in, and the project already wraps outside errors in its own types. Sources raise `SourceUnavailable` or `SourceUnauthorized`. Models raise `ChatModelUnavailable`, `ChatModelRequestRefused`, and friends. The database raises SQLAlchemy errors, and `errors.py` already knows which of those mean "the database is down" (lost connection, deadlock, statement timeout, too many connections). Anything else, including a raw `ConnectionError` an adapter forgot to wrap, is our bug.

**How bad it is without this.** Retrying J2 and J3 three times wastes two attempts each and floods the logs. Retrying a wrong Confluence password is worse: it can lock the account.

**What changes.** `classify_job_failure` maps every error to one of seven codes, and only three of them are transient:

| Code | Example error | Retried? |
|---|---|---|
| `source_unavailable` | `SourceUnavailable` | yes |
| `source_refused` | `SourceUnauthorized` | no |
| `database_unavailable` | lost connection, deadlock (`40P01`), statement timeout (`57014`) | yes |
| `database_rejected` | broken constraint (`23505`) | no |
| `model_unavailable` | `ChatModelUnavailable`, `EmbeddingTimeout`, `EmbeddingRateLimited` | yes |
| `model_refused` | `ChatModelRequestRefused`, `EmbeddingInputRejected` | no |
| `programming_error` | anything else: `KeyError`, `ValueError`, a raw `ConnectionError` | no |

The code is stored as the Job's `last_error_code`, so the `jobs` table now says *why* each Job failed, not just "purge failed".

### 3. How long to wait: backoff and jitter

**The situation.** The database restarts at 10:00:00, and 200 purges fail in the same second.

**Without a wait, or with the same wait for everyone:**

| Time | What happens |
|---|---|
| 10:00:00 | 200 purges fail |
| 10:00:30 | all 200 retry together; the database is still warming up; all 200 fail again |
| 10:01:00 | all 200 again |

**The hidden fact.** Waiting the same time puts every Job back into the same second, so the retries arrive as one wave, the "thundering herd". Doubling the wait gives the service more room each time, and a random part spreads the wave out.

**What changes.** `RetryPolicy` computes the wait after `n` failures. The ceiling is `min(15 minutes, 30 seconds * 2**(n-1))`. The wait is half the ceiling plus a random share of the other half, so it is never under half and always below the ceiling ("equal jitter").

| After failure | Ceiling | Wait |
|---|---|---|
| 1 | 30 s | 15 to 30 s |
| 2 | 60 s | 30 to 60 s |
| 3 | 120 s | 60 to 120 s (only if `max_attempts` is above 3) |
| 6 or later | 15 min | 7.5 to 15 min |

Now the 200 purges come back spread over 15 seconds instead of in one burst. The maths runs on whole microseconds, so a huge attempt count cannot overflow, and rounding never pushes a wait outside its window.

Think of a crowd leaving a stadium through one gate: if everyone tries again at the whistle, the gate jams again; if each person waits a slightly different time, they flow through.

### 4. One layer owns retries

**The situation.** Imagine two layers both retry the same failure: the model adapter retries a request 3 times, and the Job retries the attempt 3 times.

| Layer | Tries |
|---|---|
| model adapter (SDK retries on) | 3 per Job attempt |
| Job | 3 attempts |
| total calls to a model that is down | 9, with no shared wait between them |

**The hidden fact.** Retries multiply when stacked, and each layer's budget and backoff ignores the others. That is why the adapters switch SDK retries off and Dramatiq has `max_retries=0`. M08-T03 accidentally added a second layer: a repeated request queued a failed Job again *at once*, skipping any wait.

**What changes.** The stored Job is the only layer. `fail_attempt` is the only code that sets `retry_at`. `requeue_due_retries` is the only code that queues a failed Job again, and only once `retry_at` has come. `Job.retry` itself refuses a permanent failure and an early retry, so no future caller can skip the wait by accident. The M08-T03 shortcut is removed: a repeated request now gets the failed Job back, still waiting.

```python
# Before (M08-T03): enqueue_job queued a failed open Job again at once.
if existing is not None and existing.status is JobStatus.FAILED:
    existing = await _queue_again(connection, job)

# After: the open Job is returned as it is; it waits for its retry_at.
if existing is not None:
    return Enqueued(job=existing, created=False)
```

### 5. Who owns timeouts

**The situation.** The database hangs on one statement during a purge.

**The hidden fact.** Each layer has its own clock, and the one that fires first decides what happens:

| Layer | Deadline | When it fires |
|---|---|---|
| database statement | `statement_timeout` 10 s | the statement is cancelled; SQLAlchemy raises an error with SQLSTATE `57014`; classified `database_unavailable`, retried |
| connection pool | 10 s wait for a free connection | `TimeoutError` from the pool; classified `database_unavailable`, retried |
| opening a connection | 5 s | refused or timed out; classified `database_unavailable`, retried |
| model adapters | their own `timeout_seconds` | a typed `...Timeout` error; classified `model_unavailable`, retried |
| Dramatiq actor | 60 s time limit | raises `TimeLimitExceeded` into the worker thread |

`TimeLimitExceeded` is a `BaseException`, not an `Exception`, so no `except Exception` catches it. The whole transaction rolls back, the Job stays `queued`, and its message is already used. Nothing sends it again until M08-T08 adds a sweep for stuck Jobs.

**How bad it is.** So the inner deadlines must fire first. Then a slow database ends as an ordinary, classified, retried failure, and the 60-second limit stays a last-resort backstop.

**What changes.** No new timeout. A contract test checks that the worst single statement (pool wait + connect + statement timeout = 25 s) ends before the 60-second limit, so a later change to either number fails a test instead of silently making the backstop the owner.

### 6. Review finding: a real lost connection used to lose the Job

**The situation.** An adversarial review ended the database session in the middle of a purge with `pg_terminate_backend`, the way a database restart or failover does.

| Moment | Worker (first prototype) | What the `jobs` row says |
|---|---|---|
| 1 | claims J1, starts the delete inside a savepoint | `queued` (the claim is not committed yet) |
| 2 | the server ends the session; the delete raises | `queued` |
| 3 | rolls back to the savepoint: fails, the session is gone | `queued` |
| 4 | tries to store "failed": fails with `PendingRollbackError` | `queued` |
| 5 | the whole transaction is lost | `queued`, message already used, nobody sends it again |

**The hidden fact.** A savepoint protects you from a failed statement on a live connection. When the server ends the session, everything in it goes: the savepoint, the claim, and the row lock. There is nothing left to store the failure through. On top of that, the error that reached the code was `PendingRollbackError`, a consequence, which the first prototype classified as `programming_error`.

**What changes.** The purge remembers the first error of the attempt (`cause`). If the claim's own transaction dies, `_record_lost_attempt` opens a new one, locks J1, and stores the failure by that first error, so it is `database_unavailable` and gets a retry. The lock is gone anyway, so it first checks that J1 is still `queued` and still on the same attempt; otherwise another delivery got there first, and its outcome stands (`JobAlreadyClaimed`).

| | Live connection, delete fails | Server ends the session |
|---|---|---|
| where the failure is stored | same transaction, under the claim's lock | a new transaction, after checking J1 is still `queued` on the same attempt |
| what classifies it | the delete's error | the first error, not `PendingRollbackError` |
| result | `failed`, retry planned | `failed`, retry planned |

The review also found that the second transaction, without the "same attempt" check, could record a lost attempt 1 against attempt 2, if another delivery failed J1 and the retry pass queued it in between. That check closes it.

### 7. Review finding: a bug came back every synchronization pass

**The situation.** A purge fails with a `KeyError`: permanent, no retry. But each synchronization pass calls `requeue_unfinished_purges`, which asks for a new purge for every tombstoned Document that still has Chunks and no open purge.

| Sync pass | What happened in the first prototype |
|---|---|
| 1 | new purge J2; `KeyError`; final |
| 2 | new purge J3; `KeyError`; final |
| 5 | 5 failed Jobs for one Document, and counting |

**The hidden fact.** "Ask again" is the right move after an outage that has passed, and the wrong one after a refusal or a bug. Each new Job brings a fresh budget, so the per-Job budget did not bound anything across passes.

**What changes.** `load_unpurged_documents` skips a Document whose *latest* purge failed permanently. A person who fixes the cause asks for the purge again. A purge that ran out of attempts on a transient failure is still asked for again by the next pass, because the outage may be over.

"Latest" is decided by `finished_at`. The review showed that `queued_at` does not work here: the retry pass writes the relay's clock into it, while synchronization writes the time its run started, so an old Job retried late could look newer than a fresh permanent failure.

### 8. Known limits

These stay as they are for now:

- `retry_at` lives in the database, so it handles a database that blinks, not one that is down. If no new connection works either, nothing can store the failure: the attempt rolls back, the Job stays `queued` with its message spent, and the error goes to Dramatiq's log. The stuck-work sweep in M08-T08 finds such Jobs.
- A cancelled attempt, or one cut off by the actor's 60-second time limit, rolls back whole: nothing is deleted, and the Job stays `queued` with its message spent. A test pins this down; the stuck-work sweep in M08-T08 will send it again.
- A wrong database password or a missing database gives no SQLSTATE before the connection opens, so it is classified `database_unavailable` and retried up to the budget. That is bounded, but it is retried.
- A transient failure that used its whole budget gets a new purge from the next synchronization pass: a fresh budget per pass, at the pace of synchronization.
- Failed Jobs come back only when `make worker-relay` runs, so it must run on a schedule; the operations guide now says so.

## Step-by-step instructions

Start PostgreSQL before you begin. Run every command from the repository root. For integration checks, set the test database URL in your terminal:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

Run only one integration command against this database at a time. Two runs at once migrate the same database together and can leave it broken.

### Step 1 — No coding in this step: write the invariant and your predictions

Before changing anything, write down what must be true at the end and what you expect to see. You check the predictions in Step 9 and Step 11.

Create `docs/evidence/M08-T04-assign-retry-and-timeout-ownership.md`:

```markdown
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

## Predictions

- What `retry_at` a purge gets when its delete loses the database connection on attempt 1, with the default policy:
- How many attempts a purge that always loses the connection uses before it stops:
- What happens to a purge that fails with a `KeyError`, and to the next synchronization pass:
- How many tests fail if `fail_attempt` stops checking whether a failure is transient:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: give a failed Job a retry time

This step teaches the Job value to carry *when* it may run again, and to refuse any retry that breaks the rules.

**In plain words:** a failed Job gets one more field: the earliest time it may be queued again, or nothing when it never will be. A Job only counts as "still open" when such a time is set. Queuing a Job again now checks three things first: it has attempts left, its failure was not final, and its time has come.

| Piece | What it holds or does |
|---|---|
| `retry_at` | when a failed Job may be queued again; `None` means never |
| `__post_init__` checks | only a failed Job with attempts left carries `retry_at`; it is in UTC and not before `finished_at` |
| `is_open` | a failed Job is open only while `retry_at` is set |
| `fail(..., retry_at=...)` | the caller passes the planned time, or leaves it out for a final failure |
| `retry` | refuses a used-up budget, a final failure, and a `queued_at` before `retry_at`; clears `retry_at` |


**Action 1:** in the Job value, first widen what `JobNotRetryable` means:

In `src/knowledge_service/jobs.py`, find:

```python
class IllegalJobTransition(InvalidJob):
    """A caller asked a Job to move to a status it cannot reach from here."""


class JobNotRetryable(InvalidJob):
    """A failed Job has already used every attempt it is allowed."""


class JobAlreadyClaimed(InvalidJob):
    """A Job is no longer queued, so this delivery must not run it again.

    `job` is the Job as stored when the claim was refused: the outcome an
    earlier delivery already produced, or is producing.
    """

    def __init__(self, job: Job) -> None:
        super().__init__(f"only a queued job can run, not a {job.status} one")
        self.job = job


class JobKind(StrEnum):
    """What a Job was queued to do."""
```

and replace it with:

```python
class IllegalJobTransition(InvalidJob):
    """A caller asked a Job to move to a status it cannot reach from here."""


class JobNotRetryable(InvalidJob):
    """A failed Job may not be queued again, or not yet.

    It used every attempt, its failure was permanent, or its retry time has
    not come.
    """


class JobAlreadyClaimed(InvalidJob):
    """A Job is no longer queued, so this delivery must not run it again.

    `job` is the Job as stored when the claim was refused: the outcome an
    earlier delivery already produced, or is producing.
    """

    def __init__(self, job: Job, reason: str | None = None) -> None:
        super().__init__(reason or f"only a queued job can run, not a {job.status} one")
        self.job = job


class JobKind(StrEnum):
    """What a Job was queued to do."""
```

Add the field, after `last_error_code`. It has a default, so `Job.request` and every existing caller keep working:

In `src/knowledge_service/jobs.py`, find:

```python
    max_attempts: int
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error_code: str | None

    def __post_init__(self) -> None:
        _require_utc(self.queued_at, "queued_at")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise InvalidJob("max attempts must be a positive integer")
```

and replace it with:

```python
    max_attempts: int
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error_code: str | None
    # When a failed Job may be queued again; None means it never will be.
    retry_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_utc(self.queued_at, "queued_at")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise InvalidJob("max attempts must be a positive integer")
```

Check the new field in `__post_init__`, and change `is_open`:

In `src/knowledge_service/jobs.py`, find:

```python
            raise InvalidJob("only a job that is not queued carries a started time")
        if (self.status is JobStatus.FAILED) != (self.last_error_code is not None):
            raise InvalidJob("only a failed job carries an error code")
        if self.last_error_code is not None and not self.last_error_code.strip():
            raise InvalidJob("an error code must not be empty")

    @property
    def is_open(self) -> bool:
        """Whether this Job can still do its work.

        A Job is open while it is queued or running, and while it has failed
        with attempts left, because it may still be queued again. At most one
        open Job exists per kind and target.
        """
        if self.status is JobStatus.FAILED:
            return self.attempt < self.max_attempts
        return self.status is not JobStatus.SUCCEEDED

    @classmethod
    def request(
        cls,
```

and replace it with:

```python
            raise InvalidJob("only a job that is not queued carries a started time")
        if (self.status is JobStatus.FAILED) != (self.last_error_code is not None):
            raise InvalidJob("only a failed job carries an error code")
        if self.last_error_code is not None and not self.last_error_code.strip():
            raise InvalidJob("an error code must not be empty")
        if self.retry_at is not None:
            _require_utc(self.retry_at, "retry_at")
            if self.status is not JobStatus.FAILED:
                raise InvalidJob("only a failed job carries a retry time")
            if self.attempt >= self.max_attempts:
                raise InvalidJob("a job with no attempts left has no retry time")
            if self.finished_at is not None and self.retry_at < self.finished_at:
                raise InvalidJob("retry_at must not be earlier than finished_at")

    @property
    def is_open(self) -> bool:
        """Whether this Job can still do its work.

        A Job is open while it is queued or running, and while it has failed
        with a retry scheduled, because it will be queued again. At most one
        open Job exists per kind and target.
        """
        if self.status is JobStatus.FAILED:
            return self.retry_at is not None
        return self.status is not JobStatus.SUCCEEDED

    @classmethod
    def request(
        cls,
```

Let `fail` take the planned time:

In `src/knowledge_service/jobs.py`, find:

```python
    def succeed(self, *, finished_at: datetime) -> Job:
        """Finish the Job successfully."""
        _check_transition(self.status, JobStatus.SUCCEEDED)
        return replace(self, status=JobStatus.SUCCEEDED, finished_at=finished_at)

    def fail(self, *, error_code: str, finished_at: datetime) -> Job:
        """Finish the Job unsuccessfully, recording a short error code."""
        _check_transition(self.status, JobStatus.FAILED)
        if not error_code.strip():
            raise InvalidJob("an error code must not be empty")
        if len(error_code) > MAX_ERROR_CODE_LENGTH:
            raise InvalidJob(
```

and replace it with:

```python
    def succeed(self, *, finished_at: datetime) -> Job:
        """Finish the Job successfully."""
        _check_transition(self.status, JobStatus.SUCCEEDED)
        return replace(self, status=JobStatus.SUCCEEDED, finished_at=finished_at)

    def fail(
        self,
        *,
        error_code: str,
        finished_at: datetime,
        retry_at: datetime | None = None,
    ) -> Job:
        """Finish the attempt unsuccessfully, recording a short error code.

        `retry_at` schedules the next attempt; None makes the failure final.
        """
        _check_transition(self.status, JobStatus.FAILED)
        if not error_code.strip():
            raise InvalidJob("an error code must not be empty")
        if len(error_code) > MAX_ERROR_CODE_LENGTH:
            raise InvalidJob(
```

Store it in `fail`; in `retry`, add the two refusals and clear the time when the Job is queued again:

In `src/knowledge_service/jobs.py`, find:

```python
            self,
            status=JobStatus.FAILED,
            attempt=self.attempt + 1,
            finished_at=finished_at,
            last_error_code=error_code,
        )

    def retry(self, *, queued_at: datetime) -> Job:
        """Queue the Job again for one more attempt."""
        _check_transition(self.status, JobStatus.QUEUED)
        if self.attempt >= self.max_attempts:
            raise JobNotRetryable(
                f"the job already used all {self.max_attempts} attempts"
            )
        return replace(
            self,
            status=JobStatus.QUEUED,
            queued_at=queued_at,
            started_at=None,
            finished_at=None,
            last_error_code=None,
        )
```

and replace it with:

```python
            self,
            status=JobStatus.FAILED,
            attempt=self.attempt + 1,
            finished_at=finished_at,
            last_error_code=error_code,
            retry_at=retry_at,
        )

    def retry(self, *, queued_at: datetime) -> Job:
        """Queue the Job again for one more attempt, once its retry time came."""
        _check_transition(self.status, JobStatus.QUEUED)
        if self.attempt >= self.max_attempts:
            raise JobNotRetryable(
                f"the job already used all {self.max_attempts} attempts"
            )
        if self.retry_at is None:
            raise JobNotRetryable("the job failed permanently")
        if queued_at < self.retry_at:
            raise JobNotRetryable("the job may not be retried before its retry time")
        return replace(
            self,
            status=JobStatus.QUEUED,
            queued_at=queued_at,
            started_at=None,
            finished_at=None,
            last_error_code=None,
            retry_at=None,
        )
```

**Action 2:** three existing tests in `tests/test_jobs.py` built failures without a retry time and then retried them, which is now refused. Give them a time, and add three tests for the new rules.

The new tests build an invalid Job with `replace`:

In `tests/test_jobs.py`, find:

```python
"""Tests for the Job state machine."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4
```

and replace it with:

```python
"""Tests for the Job state machine."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
```

In `test_a_failed_job_retries_until_its_attempts_run_out`, plan the first retry:

In `tests/test_jobs.py`, find:

```python
def test_a_failed_job_retries_until_its_attempts_run_out() -> None:
    job = requested_job(max_attempts=2)
    first = job.start(started_at=QUEUED_AT + timedelta(seconds=1)).fail(
        error_code="source_unreachable", finished_at=QUEUED_AT + timedelta(seconds=2)
    )
    second = first.retry(queued_at=QUEUED_AT + timedelta(seconds=3))
    second_failure = second.start(started_at=QUEUED_AT + timedelta(seconds=4)).fail(
```

and replace it with:

```python
def test_a_failed_job_retries_until_its_attempts_run_out() -> None:
    job = requested_job(max_attempts=2)
    first = job.start(started_at=QUEUED_AT + timedelta(seconds=1)).fail(
        error_code="source_unreachable",
        finished_at=QUEUED_AT + timedelta(seconds=2),
        retry_at=QUEUED_AT + timedelta(seconds=3),
    )
    second = first.retry(queued_at=QUEUED_AT + timedelta(seconds=3))
    second_failure = second.start(started_at=QUEUED_AT + timedelta(seconds=4)).fail(
```

In `test_a_job_is_open_until_it_can_do_no_more_work`, plan the retry of the first failure:

In `tests/test_jobs.py`, find:

```python
def test_a_job_is_open_until_it_can_do_no_more_work() -> None:
    job = requested_job(max_attempts=2)
    started = job.start(started_at=QUEUED_AT)
    failed_once = started.fail(error_code="boom", finished_at=QUEUED_AT)
    failed_twice = (
        failed_once.retry(queued_at=QUEUED_AT)
        .start(started_at=QUEUED_AT)
```

and replace it with:

```python
def test_a_job_is_open_until_it_can_do_no_more_work() -> None:
    job = requested_job(max_attempts=2)
    started = job.start(started_at=QUEUED_AT)
    failed_once = started.fail(
        error_code="boom", finished_at=QUEUED_AT, retry_at=QUEUED_AT
    )
    failed_twice = (
        failed_once.retry(queued_at=QUEUED_AT)
        .start(started_at=QUEUED_AT)
```

and show that a final failure is not open:

In `tests/test_jobs.py`, find:

```python

    assert job.is_open
    assert started.is_open
    # A failed Job with attempts left may still be queued again.
    assert failed_once.is_open
    assert not failed_twice.is_open
    assert not started.succeed(finished_at=QUEUED_AT).is_open
```

and replace it with:

```python

    assert job.is_open
    assert started.is_open
    # A failed Job with a retry scheduled will be queued again.
    assert failed_once.is_open
    # A failure with no retry time is final, even with attempts left.
    assert not started.fail(error_code="boom", finished_at=QUEUED_AT).is_open
    assert not failed_twice.is_open
    assert not started.succeed(finished_at=QUEUED_AT).is_open
```

Add the new tests at the end of the file:

In `tests/test_jobs.py`, find:

```python
    assert isinstance(refused, InvalidJob)
    assert refused.job is finished
    assert str(refused) == "only a queued job can run, not a succeeded one"
```

and replace it with:

```python
    assert isinstance(refused, InvalidJob)
    assert refused.job is finished
    assert str(refused) == "only a queued job can run, not a succeeded one"


def test_only_a_failed_job_with_attempts_left_carries_a_retry_time() -> None:
    running = requested_job(max_attempts=2).start(started_at=QUEUED_AT)
    later = QUEUED_AT + timedelta(minutes=1)

    with pytest.raises(InvalidJob, match="only a failed job"):
        replace(running, retry_at=later)
    with pytest.raises(InvalidJob, match="earlier than finished_at"):
        running.fail(error_code="boom", finished_at=later, retry_at=QUEUED_AT)
    with pytest.raises(InvalidJob, match="UTC"):
        running.fail(
            error_code="boom",
            finished_at=QUEUED_AT,
            retry_at=later.replace(tzinfo=None),
        )


def test_a_job_with_no_attempts_left_cannot_get_a_retry_time() -> None:
    running = requested_job(max_attempts=1).start(started_at=QUEUED_AT)

    with pytest.raises(InvalidJob, match="no attempts left"):
        running.fail(error_code="boom", finished_at=QUEUED_AT, retry_at=QUEUED_AT)


def test_a_retry_waits_for_its_time_and_never_follows_a_final_failure() -> None:
    running = requested_job(max_attempts=3).start(started_at=QUEUED_AT)
    due = QUEUED_AT + timedelta(minutes=1)
    scheduled = running.fail(error_code="boom", finished_at=QUEUED_AT, retry_at=due)
    final = running.fail(error_code="boom", finished_at=QUEUED_AT)

    with pytest.raises(JobNotRetryable, match="before its retry time"):
        scheduled.retry(queued_at=due - timedelta(seconds=1))
    with pytest.raises(JobNotRetryable, match="permanently"):
        final.retry(queued_at=due)
    retried = scheduled.retry(queued_at=due)
    assert (retried.status, retried.retry_at) == (JobStatus.QUEUED, None)
```

**Check:** `uv run pytest tests/test_jobs.py -q` reports `9 passed`.

### Step 3 — Coding step: classify failures and plan the next attempt

This step adds the one module that decides whether a failure is retried and when.

**In plain words:** given the error an attempt failed with, the module names the failure with a short code and says whether trying again may help. If it may, and the Job has attempts left, it picks a time: a wait that doubles each time, never longer than 15 minutes, with a random part. Then it stores the failure on the Job, with that time or with none.

| Piece | What it does |
|---|---|
| `JobFailure` | the seven codes; `.transient` is true for the three "unavailable" ones |
| `classify_job_failure` | maps an error to a code; anything unknown is `programming_error` |
| `RetryPolicy` | `base_delay` 30 s, `max_delay` 15 min, and a `jitter` source tests can fix |
| `RetryPolicy.delay` | the wait after `n` failures, in whole microseconds, within half and all of the ceiling |
| `fail_attempt` | classifies, plans `retry_at` only for a transient failure with attempts left, logs the code and error type but never the message, and calls `Job.fail` |

The database rule already lives in `errors.py`, which the HTTP layer uses. Make it public instead of copying it, so "the database is down" is decided in one place.


**Action 1:** in `src/knowledge_service/errors.py`, rename the private helper:

In `src/knowledge_service/errors.py`, find:

```python
_UNAVAILABLE_STATES = frozenset({"08", "40", "53", "57"})


def _database_unavailable(error: BaseException) -> bool:
    """True when sending the request again later may work."""
    # The pool had no free connection in time.
    if isinstance(error, database_errors.TimeoutError):
```

and replace it with:

```python
_UNAVAILABLE_STATES = frozenset({"08", "40", "53", "57"})


def database_unavailable(error: BaseException) -> bool:
    """True when sending the request again later may work."""
    # The pool had no free connection in time.
    if isinstance(error, database_errors.TimeoutError):
```

and its one call site:

In `src/knowledge_service/errors.py`, find:

```python
        return FailureKind.DEPENDENCY
    # The database is down, unreachable, too slow, or out of connections.
    # Any other database error, such as a broken constraint, is a bug.
    if _database_unavailable(error):
        return FailureKind.DEPENDENCY
    return FailureKind.INTERNAL
```

and replace it with:

```python
        return FailureKind.DEPENDENCY
    # The database is down, unreachable, too slow, or out of connections.
    # Any other database error, such as a broken constraint, is a bug.
    if database_unavailable(error):
        return FailureKind.DEPENDENCY
    return FailureKind.INTERNAL
```

**Action 2:** create the module.

Create `src/knowledge_service/job_failures.py`:

```python
"""Why a Job attempt failed, and whether and when to try it again.

The stored Job is the one layer that retries. Dramatiq retries nothing, model
adapters keep their SDK retries off, and repository code never loops, so a
failure is retried only by scheduling the Job's next attempt here.

Every failure gets one short code. Only a dependency that may answer later
(a Source, the database, or a model that is unavailable) is retried, after a
growing, jittered delay, until the Job's attempts run out. Anything refused or
unknown is permanent: retrying a bad credential or a bug only repeats it.
"""

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from sqlalchemy import exc as database_errors

from knowledge_service.embeddings.port import (
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingRequestRefused,
)
from knowledge_service.errors import database_unavailable
from knowledge_service.ingestion.sources import SourceUnauthorized, SourceUnavailable
from knowledge_service.jobs import Job
from knowledge_service.model import (
    ChatModelError,
    ChatModelRefused,
    ChatModelRequestRefused,
    ChatModelStructuredOutputUnsupported,
)

logger = logging.getLogger(__name__)


class JobFailure(StrEnum):
    """The error code a failed attempt stores: what failed, and how."""

    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_REFUSED = "source_refused"
    DATABASE_UNAVAILABLE = "database_unavailable"
    DATABASE_REJECTED = "database_rejected"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_REFUSED = "model_refused"
    PROGRAMMING_ERROR = "programming_error"

    @property
    def transient(self) -> bool:
        """Whether the same attempt may work later without anyone fixing it."""
        return self in _TRANSIENT


_TRANSIENT: Final = frozenset(
    {
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
    }
)

_MODEL_REFUSALS: Final = (
    ChatModelRequestRefused,
    ChatModelRefused,
    ChatModelStructuredOutputUnsupported,
    EmbeddingRequestRefused,
    EmbeddingInputRejected,
)


def classify_job_failure(error: Exception) -> JobFailure:
    """The failure an attempt's error stands for; unknown errors are bugs."""
    if isinstance(error, SourceUnavailable):
        return JobFailure.SOURCE_UNAVAILABLE
    if isinstance(error, SourceUnauthorized):
        return JobFailure.SOURCE_REFUSED
    if database_unavailable(error):
        return JobFailure.DATABASE_UNAVAILABLE
    if isinstance(error, database_errors.DBAPIError):
        # The server answered and said no, such as a broken constraint.
        return JobFailure.DATABASE_REJECTED
    if isinstance(error, _MODEL_REFUSALS):
        return JobFailure.MODEL_REFUSED
    if isinstance(error, (ChatModelError, EmbeddingError)):
        return JobFailure.MODEL_UNAVAILABLE
    return JobFailure.PROGRAMMING_ERROR


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How long to wait before each retry: doubling, capped, and jittered.

    The wait before attempt n+1 is half of min(max_delay, base_delay * 2**(n-1))
    plus a random share of the other half, so it is never under half the
    backoff, and Jobs that failed together do not all come back together.
    """

    base_delay: timedelta = timedelta(seconds=30)
    max_delay: timedelta = timedelta(minutes=15)
    # A number from 0 (inclusive) to 1 (exclusive); tests pass a fixed one.
    jitter: Callable[[], float] = field(default=random.random)

    def __post_init__(self) -> None:
        if self.base_delay <= timedelta(0):
            raise ValueError("base_delay must be positive")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must not be shorter than base_delay")

    def delay(self, failed_attempts: int) -> timedelta:
        """The wait after `failed_attempts` failures, at least one."""
        if type(failed_attempts) is not int or failed_attempts < 1:
            raise ValueError("failed_attempts must be a positive integer")
        share = self.jitter()
        if not 0.0 <= share < 1.0:
            raise ValueError("jitter must return a number from 0 up to 1")
        # Whole microseconds: Python integers cannot overflow, and flooring
        # keeps the result at least half the ceiling and below it.
        base = self.base_delay // timedelta(microseconds=1)
        cap = self.max_delay // timedelta(microseconds=1)
        ceiling = min(cap, base << min(failed_attempts - 1, cap.bit_length()))
        half = ceiling // 2
        return timedelta(microseconds=half + int((ceiling - half) * share))


DEFAULT_RETRY_POLICY: Final = RetryPolicy()


def fail_attempt(
    job: Job,
    error: Exception,
    *,
    now: datetime,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> Job:
    """`job` failed by `error`: classified, and retried later only if worth it.

    A transient failure with attempts left gets a retry time; any other
    failure is final. Only the error's type is logged, never its message.
    """
    failure = classify_job_failure(error)
    failed_attempts = job.attempt + 1
    retry_at = None
    if failure.transient and failed_attempts < job.max_attempts:
        retry_at = now + policy.delay(failed_attempts)
    logger.warning(
        "job %s attempt %d failed: %s (%s); %s",
        job.job_id.value,
        failed_attempts,
        failure.value,
        type(error).__name__,
        "retry planned" if retry_at is not None else "final",
    )
    return job.fail(error_code=failure.value, finished_at=now, retry_at=retry_at)
```

**Action 3:** test every code, the delay window, the budget, the logs, and the timeout order. The last test is the timeout contract from "Before you begin", block 5.

Create `tests/test_job_failures.py`:

```python
"""Tests for classifying Job failures and scheduling their retries."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from knowledge_service.embeddings.port import (
    EmbeddingInputRejected,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
    EmbeddingTimeout,
    RejectionReason,
)
from knowledge_service.identifiers import JobId
from knowledge_service.ingestion.sources import SourceUnauthorized, SourceUnavailable
from knowledge_service.job_failures import (
    JobFailure,
    RetryPolicy,
    classify_job_failure,
    fail_attempt,
)
from knowledge_service.jobs import InvalidJob, Job, JobKind
from knowledge_service.model import (
    ChatModelMalformedResponse,
    ChatModelRefused,
    ChatModelRequestRefused,
    ChatModelUnavailable,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.jobs import JOB_TIME_LIMIT_MS

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
# Stands in for driver text that could quote a URL or a credential.
MARKER = "leak-marker-5c1e"


class DriverError(Exception):
    """A database driver error with the SQLSTATE the server sent."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(MARKER)
        self.sqlstate = sqlstate


def database_error(
    kind: type[OperationalError | IntegrityError], sqlstate: str
) -> DBAPIError:
    return kind("DELETE FROM chunks", None, DriverError(sqlstate))


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (SourceUnavailable("the wiki is down"), JobFailure.SOURCE_UNAVAILABLE),
        (SourceUnauthorized("token expired"), JobFailure.SOURCE_REFUSED),
        (
            OperationalError("SELECT 1", None, ConnectionError("lost")),
            JobFailure.DATABASE_UNAVAILABLE,
        ),
        # A deadlock or serialization failure may pass on a second try.
        (database_error(OperationalError, "40P01"), JobFailure.DATABASE_UNAVAILABLE),
        # The statement timeout cancelled the statement.
        (database_error(OperationalError, "57014"), JobFailure.DATABASE_UNAVAILABLE),
        (database_error(IntegrityError, "23505"), JobFailure.DATABASE_REJECTED),
        (ChatModelUnavailable(), JobFailure.MODEL_UNAVAILABLE),
        (ChatModelMalformedResponse("no usage"), JobFailure.MODEL_UNAVAILABLE),
        (EmbeddingTimeout(), JobFailure.MODEL_UNAVAILABLE),
        (EmbeddingRateLimited(), JobFailure.MODEL_UNAVAILABLE),
        (ChatModelRequestRefused(), JobFailure.MODEL_REFUSED),
        (ChatModelRefused(), JobFailure.MODEL_REFUSED),
        (EmbeddingRequestRefused(), JobFailure.MODEL_REFUSED),
        (
            EmbeddingInputRejected(RejectionReason.EMPTY_TEXT, index=0),
            JobFailure.MODEL_REFUSED,
        ),
        (ValueError("bad input"), JobFailure.PROGRAMMING_ERROR),
        (KeyError("missing"), JobFailure.PROGRAMMING_ERROR),
        (InvalidJob("bad job"), JobFailure.PROGRAMMING_ERROR),
        # An adapter must wrap network errors; a raw one is a bug.
        (ConnectionError("reset"), JobFailure.PROGRAMMING_ERROR),
    ],
)
def test_every_failure_is_classified(error: Exception, failure: JobFailure) -> None:
    assert classify_job_failure(error) is failure


def test_only_dependencies_that_may_answer_later_are_transient() -> None:
    assert {failure for failure in JobFailure if failure.transient} == {
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
    }


def test_the_delay_doubles_until_its_cap_and_never_drops_below_half() -> None:
    lowest = RetryPolicy(jitter=lambda: 0.0)
    highest = RetryPolicy(jitter=lambda: 0.999)

    assert [lowest.delay(n) for n in (1, 2, 3)] == [
        timedelta(seconds=15),
        timedelta(seconds=30),
        timedelta(seconds=60),
    ]
    assert highest.delay(1) < timedelta(seconds=30)
    assert lowest.delay(10) == timedelta(minutes=7, seconds=30)
    assert highest.delay(10) < timedelta(minutes=15)
    # A huge attempt count stays at the cap instead of overflowing.
    assert lowest.delay(10_000) == timedelta(minutes=7, seconds=30)


@pytest.mark.parametrize("ceiling_us", [1, 3, 7, 1_000_001])
def test_the_delay_stays_in_its_window_for_any_ceiling(ceiling_us: int) -> None:
    ceiling = timedelta(microseconds=ceiling_us)
    lowest = RetryPolicy(base_delay=ceiling, max_delay=ceiling, jitter=lambda: 0.0)
    highest = RetryPolicy(
        base_delay=ceiling, max_delay=ceiling, jitter=lambda: 0.999999
    )

    assert lowest.delay(1) == timedelta(microseconds=ceiling_us // 2)
    assert highest.delay(1) < ceiling


def test_a_long_base_delay_reaches_the_cap_without_overflowing() -> None:
    policy = RetryPolicy(
        base_delay=timedelta(days=1_000),
        max_delay=timedelta(days=2_000),
        jitter=lambda: 0.0,
    )

    assert policy.delay(21) == timedelta(days=1_000)
    assert policy.delay(10_000) == timedelta(days=1_000)


@pytest.mark.parametrize("failed_attempts", [0, -1, True, 1.0])
def test_the_delay_needs_a_positive_attempt_count(failed_attempts: int) -> None:
    with pytest.raises(ValueError, match="failed_attempts"):
        RetryPolicy().delay(failed_attempts)


@pytest.mark.parametrize("share", [-0.1, 1.0, float("nan")])
def test_a_jitter_outside_zero_to_one_is_refused(share: float) -> None:
    with pytest.raises(ValueError, match="jitter"):
        RetryPolicy(jitter=lambda: share).delay(1)


def test_a_policy_needs_a_positive_delay_and_a_cap_above_it() -> None:
    with pytest.raises(ValueError, match="base_delay"):
        RetryPolicy(base_delay=timedelta(0))
    with pytest.raises(ValueError, match="max_delay"):
        RetryPolicy(base_delay=timedelta(minutes=2), max_delay=timedelta(minutes=1))


def running_job(*, attempt: int = 0, max_attempts: int = 3) -> Job:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_DOCUMENT_CHUNKS,
        target_id=JobId.new().value,
        queued_at=NOW,
        max_attempts=max_attempts,
    )
    for _ in range(attempt):
        job = job.start(started_at=NOW).fail(
            error_code="boom", finished_at=NOW, retry_at=NOW
        )
        job = job.retry(queued_at=NOW)
    return job.start(started_at=NOW)


def test_a_transient_failure_with_attempts_left_is_retried_after_the_delay() -> None:
    policy = RetryPolicy(jitter=lambda: 0.5)

    failed = fail_attempt(
        running_job(), SourceUnavailable("down"), now=NOW, policy=policy
    )

    assert (failed.attempt, failed.last_error_code) == (1, "source_unavailable")
    assert failed.retry_at == NOW + policy.delay(1)
    assert failed.is_open


def test_a_transient_failure_stops_at_the_attempt_budget() -> None:
    last = running_job(attempt=2, max_attempts=3)

    failed = fail_attempt(last, SourceUnavailable("down"), now=NOW)

    assert (failed.attempt, failed.retry_at) == (3, None)
    assert not failed.is_open


@pytest.mark.parametrize(
    "error",
    [SourceUnauthorized("revoked"), ChatModelRequestRefused(), ValueError("bug")],
)
def test_a_permanent_failure_is_never_retried(error: Exception) -> None:
    failed = fail_attempt(running_job(), error, now=NOW)

    assert (failed.attempt, failed.retry_at) == (1, None)
    assert not failed.is_open


def test_a_failure_logs_its_code_and_type_but_not_its_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    fail_attempt(running_job(), database_error(OperationalError, "08006"), now=NOW)

    assert "database_unavailable" in caplog.text
    assert "OperationalError" in caplog.text
    assert MARKER not in caplog.text


def test_database_deadlines_end_before_the_hard_job_time_limit() -> None:
    settings = Settings()
    # The worst single statement: waiting for a pooled connection, opening
    # one, and running until the statement timeout cancels it.
    worst_statement_seconds = (
        settings.database_pool_timeout
        + settings.database_connect_timeout_seconds
        + settings.database_statement_timeout_ms / 1000
    )

    assert worst_statement_seconds < JOB_TIME_LIMIT_MS / 1000
```

**Check:** `uv run pytest tests/test_job_failures.py -q` reports `40 passed`.

### Step 4 — Coding step: store the retry time, and let the database enforce it

This step adds the column, a rule the database itself checks, and an index for finding Jobs whose time has come.

**In plain words:** the `jobs` table gets a column for the retry time. The database refuses a retry time on any Job that has not failed, or has no attempts left. The rule for "open Job" from M08-T03 now looks at the retry time instead of the attempt count. A small index lets the retry pass find due Jobs quickly.

| Piece | What it holds or does |
|---|---|
| `retry_at` column | the retry time; `null` when there is none |
| `ck_jobs_retry_at` | `retry_at` only on a failed Job with attempts left, and not before `finished_at` |
| `OPEN_JOB_PREDICATE` | open now means queued, running, or failed with `retry_at` set |
| `ix_jobs_due_retry` | a partial index over Jobs that have a retry time, ordered by it |
| Migration `f6a7b8c9d0e1` | adds all of this |

The migration must not change which Jobs are open, or the unique index from M08-T03 could find two open Jobs for one intent. So on the way up it gives every failed Job with attempts left `retry_at = finished_at`: it was open before, it stays open, due at once. On the way down it marks a final failure with attempts left as used up, so the old rule does not count it as open.


**Action 1:** create the migration.

Create `migrations/versions/f6a7b8c9d0e1_job_retry_schedule.py`:

```python
"""job retry schedule

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-11-23 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"

OLD_OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and attempt < max_attempts)"
)
OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and retry_at is not null)"
)


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    # A failed Job with attempts left was open before; it stays open, due now.
    op.execute(
        "update jobs set retry_at = finished_at"
        " where status = 'failed' and attempt < max_attempts"
    )
    op.create_check_constraint(
        "ck_jobs_retry_at",
        "jobs",
        "retry_at is null or (status = 'failed' and attempt < max_attempts"
        " and retry_at >= finished_at)",
    )
    # The same rows are open under both predicates, so no new duplicates.
    op.drop_index("uq_jobs_open_intent", table_name="jobs")
    op.create_index(
        "uq_jobs_open_intent",
        "jobs",
        ["kind", "target_id"],
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    )
    op.create_index(
        "ix_jobs_due_retry",
        "jobs",
        ["retry_at", "job_id"],
        postgresql_where=sa.text("retry_at is not null"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_due_retry", table_name="jobs")
    op.drop_index("uq_jobs_open_intent", table_name="jobs")
    # Before this revision, a failed Job with attempts left was open. Keep a
    # permanent failure closed by marking its attempts as used up.
    op.execute(
        "update jobs set attempt = max_attempts"
        " where status = 'failed' and retry_at is null and attempt < max_attempts"
    )
    op.create_index(
        "uq_jobs_open_intent",
        "jobs",
        ["kind", "target_id"],
        unique=True,
        postgresql_where=sa.text(OLD_OPEN_JOB_PREDICATE),
    )
    op.drop_constraint("ck_jobs_retry_at", "jobs", type_="check")
    op.drop_column("jobs", "retry_at")
```

**Action 2:** in the schema, change the open-Job rule:

In `src/knowledge_service/persistence/schema.py`, find:

```python
)


# A Job that can still do its work: queued, running, or failed with attempts
# left. At most one such Job exists per kind and target (its intent).
OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and attempt < max_attempts)"
)

jobs = sa.Table(
```

and replace it with:

```python
)


# A Job that can still do its work: queued, running, or failed with a retry
# scheduled. At most one such Job exists per kind and target (its intent).
OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and retry_at is not null)"
)

jobs = sa.Table(
```

add the column after `published_at`:

In `src/knowledge_service/persistence/schema.py`, find:

```python
    sa.Column("last_error_code", sa.String(length=64), nullable=True),
    # When a message naming this queued attempt was handed to the queue.
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(
        "kind in ('synchronize_source', 'purge_conversation', 'purge_document_chunks')",
        name="ck_jobs_kind",
```

and replace it with:

```python
    sa.Column("last_error_code", sa.String(length=64), nullable=True),
    # When a message naming this queued attempt was handed to the queue.
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    # When a failed Job may be queued again; null when it never will be.
    sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(
        "kind in ('synchronize_source', 'purge_conversation', 'purge_document_chunks')",
        name="ck_jobs_kind",
```

add the check after `ck_jobs_last_error_code`:

In `src/knowledge_service/persistence/schema.py`, find:

```python
        "(status = 'failed') = (last_error_code is not null)",
        name="ck_jobs_last_error_code",
    ),
    sa.Index(
        "ix_jobs_unpublished_queued_at",
        "queued_at",
```

and replace it with:

```python
        "(status = 'failed') = (last_error_code is not null)",
        name="ck_jobs_last_error_code",
    ),
    sa.CheckConstraint(
        "retry_at is null or (status = 'failed' and attempt < max_attempts"
        " and retry_at >= finished_at)",
        name="ck_jobs_retry_at",
    ),
    sa.Index(
        "ix_jobs_unpublished_queued_at",
        "queued_at",
```

and add the index as the table's last item:

In `src/knowledge_service/persistence/schema.py`, find:

```python
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    ),
)

audit_events = sa.Table(
```

and replace it with:

```python
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    ),
    sa.Index(
        "ix_jobs_due_retry",
        "retry_at",
        "job_id",
        postgresql_where=sa.text("retry_at is not null"),
    ),
)

audit_events = sa.Table(
```

**Action 3:** allow the new index:

In `tests/test_indexes.py`, find:

```python
    "ix_synchronization_runs_source_started_at",
    "uq_embedding_spaces_current",
    "uq_jobs_open_intent",
    "uq_synchronization_runs_running_source",
}
```

and replace it with:

```python
    "ix_synchronization_runs_source_started_at",
    "uq_embedding_spaces_current",
    "uq_jobs_open_intent",
    "ix_jobs_due_retry",
    "uq_synchronization_runs_running_source",
}
```

**Check:** `uv run pytest tests/test_indexes.py -q` reports `4 passed`, and `uv run pytest tests/test_migrations_integration.py -q -m integration` reports `3 passed`.

### Step 5 — Coding step: read and write the retry time, and queue due Jobs

This step teaches the database layer the new column, removes the M08-T03 shortcut, and adds the query that queues due Jobs.

**In plain words:** Jobs are now saved and loaded with their retry time. Asking for work that already has a failed, waiting Job returns that Job as it is, instead of queuing it at once. A new function finds failed Jobs whose time has come, oldest first, and queues each one again; Jobs another pass is handling at that moment are skipped, so two passes never queue one Job twice.

| Piece | What changes |
|---|---|
| `_job_row`, `_job_from` | carry `retry_at` |
| `open_job` | open means `retry_at` is set for a failed Job |
| `load_open_job` | loses `for_update`, which only the removed shortcut used |
| `enqueue_job` | returns an open failed Job unchanged; `_queue_again` is gone |
| `requeue_due_jobs` | locks due failed Jobs with `SKIP LOCKED`, oldest `retry_at` first, and saves `retry` for each |

**Action 1:** the file changes in many places, so replace it whole.


Replace the whole of `src/knowledge_service/persistence/jobs.py` with:

```python
"""Store queued Jobs, at most one open Job per intent, and their messages."""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.identifiers import JobId
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence.schema import OPEN_JOB_PREDICATE, jobs

# An open Job can finish between our insert and our read of it; each such
# race costs one round, and three in a row mean something keeps closing Jobs.
MAX_ENQUEUE_ROUNDS: Final = 3


class JobIntentContended(RuntimeError):
    """The open Job for an intent kept finishing while a new one was requested."""


@dataclass(frozen=True, slots=True)
class Enqueued:
    """The open Job for an intent, and whether this request created it."""

    job: Job
    created: bool


def _job_row(job: Job) -> dict[str, object]:
    return {
        "job_id": job.job_id.value,
        "kind": job.kind.value,
        "target_id": job.target_id,
        "status": job.status.value,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "queued_at": job.queued_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "last_error_code": job.last_error_code,
        "retry_at": job.retry_at,
    }


def _job_from(record: Mapping[str, object]) -> Job:
    try:
        kind = JobKind(cast(str, record["kind"]))
        status = JobStatus(cast(str, record["status"]))
    except ValueError:
        raise InvalidJob("stored job kind or status is not a known value") from None

    return Job(
        job_id=JobId(cast(UUID, record["job_id"])),
        kind=kind,
        target_id=cast(UUID, record["target_id"]),
        status=status,
        attempt=cast(int, record["attempt"]),
        max_attempts=cast(int, record["max_attempts"]),
        queued_at=cast(datetime, record["queued_at"]),
        started_at=cast("datetime | None", record["started_at"]),
        finished_at=cast("datetime | None", record["finished_at"]),
        last_error_code=cast("str | None", record["last_error_code"]),
        retry_at=cast("datetime | None", record["retry_at"]),
    )


def open_job() -> sa.ColumnElement[bool]:
    """Rows of Jobs that can still do their work; see `Job.is_open`."""
    return sa.or_(
        jobs.c.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
        sa.and_(
            jobs.c.status == JobStatus.FAILED.value,
            jobs.c.retry_at.is_not(None),
        ),
    )


async def load_open_job(
    connection: AsyncConnection, *, kind: JobKind, target_id: UUID
) -> Job | None:
    """The open Job of this kind for this target, if there is one."""
    statement = sa.select(jobs).where(
        jobs.c.kind == kind.value, jobs.c.target_id == target_id, open_job()
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def enqueue_job(connection: AsyncConnection, job: Job) -> Enqueued:
    """Store new queued `job`, unless an open Job with the same intent exists.

    The intent is the Job's kind and target. When an open Job for it exists,
    that Job is returned and `job` is not stored, so asking twice for the
    same work, even at the same moment, leaves one Job to do it. An open Job
    that failed waits for its retry time; a new request does not hurry it.
    """
    if job.status is not JobStatus.QUEUED or job.attempt != 0:
        raise InvalidJob("only a new queued job can be enqueued")
    for _ in range(MAX_ENQUEUE_ROUNDS):
        inserted = await connection.scalar(
            insert(jobs)
            .values(_job_row(job))
            .on_conflict_do_nothing(
                index_elements=[jobs.c.kind, jobs.c.target_id],
                index_where=sa.text(OPEN_JOB_PREDICATE),
            )
            .returning(jobs.c.job_id)
        )
        if inserted is not None:
            return Enqueued(job=job, created=True)
        existing = await load_open_job(
            connection, kind=job.kind, target_id=job.target_id
        )
        if existing is not None:
            return Enqueued(job=existing, created=False)
    raise JobIntentContended(f"the open {job.kind} job kept changing")


async def load_job(
    connection: AsyncConnection,
    *,
    job_id: JobId,
    for_update: bool = False,
) -> Job | None:
    """Read a stored Job, if it exists, optionally locking its row."""
    statement = sa.select(jobs).where(jobs.c.job_id == job_id.value)
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def save_job(connection: AsyncConnection, *, job: Job) -> Job:
    """Store the status a Job has reached.

    A Job queued again for another attempt needs a new message, so saving a
    queued Job marks it unpublished.
    """
    values = _job_row(job)
    if job.status is JobStatus.QUEUED:
        values["published_at"] = None
    statement = sa.update(jobs).where(jobs.c.job_id == job.job_id.value).values(values)
    await connection.execute(statement)
    return job


async def requeue_due_jobs(
    connection: AsyncConnection, *, now: datetime, limit: int
) -> tuple[Job, ...]:
    """Queue again up to `limit` failed Jobs whose retry time has come.

    Oldest retry time first. Rows another transaction has locked are skipped,
    so two passes at once never queue the same Job twice.
    """
    statement = (
        sa.select(jobs)
        .where(jobs.c.status == JobStatus.FAILED.value, jobs.c.retry_at <= now)
        .order_by(jobs.c.retry_at, jobs.c.job_id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(
        [
            await save_job(connection, job=_job_from(dict(record)).retry(queued_at=now))
            for record in records
        ]
    )


def _unpublished() -> sa.ColumnElement[bool]:
    return sa.and_(
        jobs.c.status == JobStatus.QUEUED.value, jobs.c.published_at.is_(None)
    )


async def load_unpublished_job_ids(
    connection: AsyncConnection,
    *,
    kinds: Collection[JobKind],
    limit: int,
) -> tuple[JobId, ...]:
    """IDs of queued Jobs of these kinds that no message names, oldest first."""
    statement = (
        sa.select(jobs.c.job_id)
        .where(_unpublished(), jobs.c.kind.in_([kind.value for kind in kinds]))
        .order_by(jobs.c.queued_at, jobs.c.job_id)
        .limit(limit)
    )
    rows = await connection.scalars(statement)
    return tuple(JobId(job_id) for job_id in rows)


async def lock_unpublished_job(
    connection: AsyncConnection, *, job_id: JobId
) -> Job | None:
    """Lock a queued, unpublished Job, or None when it is not one.

    A row another transaction has locked counts as not one, so two
    publishers never send the same Job at the same time.
    """
    statement = (
        sa.select(jobs)
        .where(jobs.c.job_id == job_id.value, _unpublished())
        .with_for_update(skip_locked=True)
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def mark_job_published(
    connection: AsyncConnection, *, job_id: JobId, published_at: datetime
) -> None:
    """Record that a message naming this queued Job was handed to the queue."""
    await connection.execute(
        sa.update(jobs)
        .where(jobs.c.job_id == job_id.value)
        .values(published_at=published_at)
    )
```

**Action 2:** in `src/knowledge_service/persistence/documents.py`, stop asking again for a purge whose latest attempt failed permanently ("Before you begin", block 7). The subquery picks the latest purge by `finished_at`.

First the import:

In `src/knowledge_service/persistence/documents.py`, find:

```python
    InvalidDocument,
)
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.jobs import JobKind
from knowledge_service.language import Language
from knowledge_service.persistence.jobs import open_job
```

and replace it with:

```python
    InvalidDocument,
)
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.job_failures import JobFailure
from knowledge_service.jobs import JobKind
from knowledge_service.language import Language
from knowledge_service.persistence.jobs import open_job
```

Then the list of permanent codes, just above `load_unpurged_documents`:

In `src/knowledge_service/persistence/documents.py`, find:

```python
    return tuple(as_document(dict(record)) for record in records)


async def load_unpurged_documents(
    connection: AsyncConnection,
    *,
```

and replace it with:

```python
    return tuple(as_document(dict(record)) for record in records)


# Codes of a purge failure that a new attempt would only repeat.
_PERMANENT_FAILURES = [failure.value for failure in JobFailure if not failure.transient]


async def load_unpurged_documents(
    connection: AsyncConnection,
    *,
```

Then the start of the function:

In `src/knowledge_service/persistence/documents.py`, find:

```python
) -> tuple[Document, ...]:
    """Read tombstoned Documents that still hold Chunks and no purge can delete.

    A purge Job counts while it is queued, running, or failed with attempts
    left; a Document whose last purge used every attempt is returned.
    """
    open_purge = sa.exists().where(
        jobs.c.kind == JobKind.PURGE_DOCUMENT_CHUNKS.value,
        jobs.c.target_id == documents.c.document_id,
        open_job(),
    )
    statement = (
        sa.select(documents)
```

and replace it with:

```python
) -> tuple[Document, ...]:
    """Read tombstoned Documents that still hold Chunks and no purge can delete.

    A purge Job counts while it is open: queued, running, or failed with a
    retry scheduled. A Document whose last purge ran out of attempts on a
    passing failure is returned, so a later pass tries again. One whose last
    purge failed permanently is not: asking again would only repeat the
    refusal or the bug, so a person asks once the cause is fixed.
    """
    purge_of_document = sa.and_(
        jobs.c.kind == JobKind.PURGE_DOCUMENT_CHUNKS.value,
        jobs.c.target_id == documents.c.document_id,
    )
    open_purge = sa.exists().where(purge_of_document, open_job())
    last_purge_error = (
        sa.select(jobs.c.last_error_code)
        .where(purge_of_document)
        # Open purges are excluded, so every row here has finished. Finish
        # times come from the attempts themselves; queue times do not.
        .order_by(jobs.c.finished_at.desc(), jobs.c.job_id.desc())
        .limit(1)
        .scalar_subquery()
    )
    statement = (
        sa.select(documents)
```

And the extra condition in its query:

In `src/knowledge_service/persistence/documents.py`, find:

```python
            documents.c.availability == DocumentAvailability.TOMBSTONED.value,
            sa.exists().where(chunks.c.document_id == documents.c.document_id),
            ~open_purge,
        )
        .order_by(documents.c.external_id)
    )
```

and replace it with:

```python
            documents.c.availability == DocumentAvailability.TOMBSTONED.value,
            sa.exists().where(chunks.c.document_id == documents.c.document_id),
            ~open_purge,
            sa.func.coalesce(last_purge_error, "").not_in(_PERMANENT_FAILURES),
        )
        .order_by(documents.c.external_id)
    )
```

**Check:** `uv run pyright src/knowledge_service/persistence` reports 0 errors.

### Step 6 — Coding step: run the retry pass before every relay pass

This step connects the new query to the command that already runs regularly.

**In plain words:** one new function queues due retries, with the same limits and UTC check as the relay. The relay command calls it first, then publishes as before, so a Job queued again gets its message in the same pass. The command now also prints how many Jobs it queued again.

| Piece | What it does |
|---|---|
| `requeue_due_retries` | checks `now` is UTC and `limit` is 1 to 1,000, then runs `requeue_due_jobs` in one transaction; returns the Job IDs |
| `relay.main` | prints `requeued N, published ...` |


**Action 1:** import the query:

In `src/knowledge_service/job_dispatch.py`, find:

```python
    load_unpublished_job_ids,
    lock_unpublished_job,
    mark_job_published,
)

logger = logging.getLogger(__name__)
```

and replace it with:

```python
    load_unpublished_job_ids,
    lock_unpublished_job,
    mark_job_published,
    requeue_due_jobs,
)

logger = logging.getLogger(__name__)
```

and add the function at the end of the file:

In `src/knowledge_service/job_dispatch.py`, find:

```python
        failed=0,
        untried=0,
    )
```

and replace it with:

```python
        failed=0,
        untried=0,
    )


async def requeue_due_retries(
    runtime: DatabaseRuntime, *, now: datetime, limit: int = 100
) -> tuple[JobId, ...]:
    """Queue again failed Jobs whose retry time has come, oldest first.

    This is the only place a failed Job goes back to the queue. Each one is
    then unpublished, so the next `publish_pending_jobs` sends its message.
    """
    _require_utc(now)
    if type(limit) is not int or not 1 <= limit <= MAX_RELAY_BATCH:
        raise ValueError(f"limit must be an integer from 1 to {MAX_RELAY_BATCH}")
    async with runtime.transaction() as connection:
        requeued = await requeue_due_jobs(connection, now=now, limit=limit)
    return tuple(job.job_id for job in requeued)
```

**Action 2:** the relay command changes in several places; replace it whole.

Replace the whole of `src/knowledge_service/worker/relay.py` with:

```python
"""Queue due retries, then publish Jobs no message names yet: one pass.

Run `python -m knowledge_service.worker.relay` after a crash or a Redis
outage, and on a schedule, because it is also what brings a failed Job back
once its retry time has come. It exits 0 when every Job it found was
published or skipped, and 1 when the queue refused a message; the rest wait
for the next pass.
"""

import asyncio
import sys
from datetime import UTC, datetime

from knowledge_service.database import create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.job_dispatch import (
    JobPublisher,
    RelayReport,
    publish_pending_jobs,
    requeue_due_retries,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import broker_from_settings
from knowledge_service.worker.jobs import (
    HANDLERS,
    DramatiqJobPublisher,
    JobRunner,
    declare_job_actors,
)


async def _relay(
    settings: Settings, publisher: JobPublisher
) -> tuple[tuple[JobId, ...], RelayReport]:
    runtime = create_database_runtime(settings)
    now = datetime.now(UTC)
    try:
        requeued = await requeue_due_retries(runtime, now=now)
        report = await publish_pending_jobs(runtime, publisher=publisher, now=now)
    finally:
        await runtime.dispose()
    return requeued, report


def main() -> int:
    settings = Settings()
    broker = broker_from_settings(settings)
    try:
        actors = declare_job_actors(
            broker, run_job=JobRunner(settings), kinds=frozenset(HANDLERS)
        )
        requeued, report = asyncio.run(_relay(settings, DramatiqJobPublisher(actors)))
    finally:
        broker.close()
    sys.stdout.write(
        f"requeued {len(requeued)}, "
        f"published {report.published}, skipped {report.skipped}, "
        f"failed {report.failed}, untried {report.untried}\n"
    )
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

**Check:** `uv run pyright src/knowledge_service/job_dispatch.py src/knowledge_service/worker` reports 0 errors.

### Step 7 — Coding step: let the purge classify its failures, also when the connection dies

This step changes the only handler, the purge, to use `fail_attempt`, and adds the second transaction from "Before you begin", block 6.

**In plain words:** when the delete fails, the purge now hands the error to the new module, which decides the code and the retry time. If the database ends the session, so the purge cannot even save that, it opens a fresh transaction and saves the failure there, judged by the first error. Before saving, it checks that the Job is still waiting on the same attempt; if another delivery already ran it, that delivery's result stands.

| Piece | What changes |
|---|---|
| `PURGE_FAILED` | removed; the stored code says why it failed |
| `purge_document_chunks` | takes `retry_policy`; remembers `cause` and `claimed_attempt`; on a dead transaction calls `_record_lost_attempt` |
| `_record_lost_attempt` | locks the Job in a new transaction; `JobAlreadyClaimed` unless it is still `queued` on the claimed attempt; otherwise stores `fail_attempt` |
| docstring | says what a cancellation and the time limit do |


**Action 1:** import the new module:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import AuditEventId, DocumentId, JobId, SourceId
from knowledge_service.jobs import (
    InvalidJob,
    Job,
```

and replace it with:

```python
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import AuditEventId, DocumentId, JobId, SourceId
from knowledge_service.job_failures import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
    fail_attempt,
)
from knowledge_service.jobs import (
    InvalidJob,
    Job,
```

Remove the old code constant:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
)
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job

PURGE_FAILED = "chunk_purge_failed"


@dataclass(frozen=True, slots=True)
class Tombstone:
```

and replace it with:

```python
)
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job


@dataclass(frozen=True, slots=True)
class Tombstone:
```

**Action 2:** replace `purge_document_chunks` from its signature to the end of the function, and add `_record_lost_attempt` after it:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
    *,
    job_id: JobId,
    now: datetime,
) -> Job:
    """Run one queued purge Job and return it as stored afterwards.

    The Chunks are deleted only if the Document is still tombstoned; a
    Document restored in the meantime keeps its new Chunks. If deleting fails,
    nothing is deleted, the Job is stored as failed with `PURGE_FAILED`, and
    `Job.retry` can queue it again. Either way the outcome commits with the
    claim, so another delivery waiting on the claim sees it, never `queued`.
    """
    async with runtime.transaction() as connection:
        job = await _claim(connection, job_id=job_id, now=now)
        try:
            # A savepoint: a failed delete is undone, while the claim keeps
            # its row lock until the failure below is stored.
            async with connection.begin_nested():
                # Lock the row, so a restore cannot interleave with the delete.
                document = await load_document_by_id(
                    connection,
                    document_id=DocumentId(job.target_id),
                    for_update=True,
                )
                if (
                    document is not None
                    and document.availability is DocumentAvailability.TOMBSTONED
                ):
                    await delete_chunks(connection, document_id=document.document_id)
        except Exception:
            failed = job.fail(error_code=PURGE_FAILED, finished_at=now)
            return await save_job(connection, job=failed)
        return await save_job(connection, job=job.succeed(finished_at=now))


def availability_event(document: Document) -> AuditEvent:
```

and replace it with:

```python
    *,
    job_id: JobId,
    now: datetime,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> Job:
    """Run one queued purge Job and return it as stored afterwards.

    The Chunks are deleted only if the Document is still tombstoned; a
    Document restored in the meantime keeps its new Chunks. If deleting fails,
    nothing is deleted, and the Job is stored as failed with the failure's
    code; `fail_attempt` schedules a retry only for a failure that may pass.
    The outcome commits with the claim, so another delivery waiting on the
    claim sees it, never `queued`.

    If the claim's own transaction dies, as when the database drops the
    connection, its row lock dies with it; the attempt is then recorded in a
    new transaction, by the error that started it. A cancellation or the
    actor's time limit is not an `Exception`: it rolls back the attempt and
    leaves the Job queued, with its message spent, for M08-T08 to find.
    """
    # The first error of the attempt, so a dead connection is not blamed on
    # the errors it causes later, such as a pending rollback.
    cause: Exception | None = None
    # The attempt this delivery claimed, so a lost one is never recorded
    # against a later attempt another delivery started meanwhile.
    claimed_attempt: int | None = None
    try:
        async with runtime.transaction() as connection:
            job = await _claim(connection, job_id=job_id, now=now)
            claimed_attempt = job.attempt
            try:
                # A savepoint: a failed delete is undone, while the claim
                # keeps its row lock until the failure below is stored.
                async with connection.begin_nested():
                    # Lock the row, so a restore cannot interleave with the delete.
                    document = await load_document_by_id(
                        connection,
                        document_id=DocumentId(job.target_id),
                        for_update=True,
                    )
                    if (
                        document is not None
                        and document.availability is DocumentAvailability.TOMBSTONED
                    ):
                        await delete_chunks(
                            connection, document_id=document.document_id
                        )
            except Exception as error:
                cause = error
                failed = fail_attempt(job, error, now=now, policy=retry_policy)
                return await save_job(connection, job=failed)
            return await save_job(connection, job=job.succeed(finished_at=now))
    except InvalidJob:
        raise
    except Exception as error:
        return await _record_lost_attempt(
            runtime,
            job_id=job_id,
            cause=cause or error,
            claimed_attempt=claimed_attempt,
            now=now,
            retry_policy=retry_policy,
        )


async def _record_lost_attempt(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    cause: Exception,
    claimed_attempt: int | None,
    now: datetime,
    retry_policy: RetryPolicy,
) -> Job:
    """Record an attempt whose transaction could not commit its outcome.

    Its lock is gone, so another delivery may have run the Job meanwhile,
    or even a later attempt of it; then that outcome stands. If this
    transaction fails too, the error propagates and the Job stays queued for
    M08-T08.
    """
    async with runtime.transaction() as connection:
        stored = await load_job(connection, job_id=job_id, for_update=True)
        if stored is None:
            raise cause
        if stored.status is not JobStatus.QUEUED:
            raise JobAlreadyClaimed(stored)
        if claimed_attempt is not None and stored.attempt != claimed_attempt:
            raise JobAlreadyClaimed(
                stored, "another delivery already ran the attempt this one lost"
            )
        failed = fail_attempt(
            stored.start(started_at=now), cause, now=now, policy=retry_policy
        )
        return await save_job(connection, job=failed)


def availability_event(document: Document) -> AuditEvent:
```

**Check:** `uv run pyright src` reports 0 errors.

### Step 8 — Coding step: update the tests that relied on the old behaviour, and test retries for real

This step updates five test files to the new rules and adds the retry tests against real PostgreSQL.

**In plain words:** older tests failed a purge with a plain `ConnectionError`, which is now rightly a bug and never retried. They now raise a real database "connection lost" error instead. Tests that retried by hand wait for the retry time. Two M08-T03 tests that expected a repeated request to queue a failed Job at once now expect it to wait. The new tests run real purges through every case in "Before you begin".

**Action 1:** the shared fixtures get a transient database error. `OperationalError` with a `ConnectionError` inside and no SQLSTATE is what SQLAlchemy raises when the server stops answering.


First the import:

In `tests/job_fixtures.py`, find:

```python
from datetime import UTC, datetime

from sqlalchemy import text

from knowledge_service.access import Subject
from knowledge_service.database import DatabaseRuntime
```

and replace it with:

```python
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from knowledge_service.access import Subject
from knowledge_service.database import DatabaseRuntime
```

Then the helper, at the end of the file:

In `tests/job_fixtures.py`, find:

```python
async def chunk_count(runtime: DatabaseRuntime, document_id: DocumentId) -> int:
    async with runtime.transaction() as connection:
        return len(await load_chunks(connection, document_id=document_id))
```

and replace it with:

```python
async def chunk_count(runtime: DatabaseRuntime, document_id: DocumentId) -> int:
    async with runtime.transaction() as connection:
        return len(await load_chunks(connection, document_id=document_id))


def database_went_away() -> OperationalError:
    """A database error that may pass: the connection was lost mid-statement."""
    return OperationalError(
        "DELETE FROM chunks", None, ConnectionError("the database went away")
    )
```

**Action 2:** the deletion tests use it, and wait for the retry time when they retry by hand.

Import the helper and the codes, and drop the old constant:

In `tests/test_deletions_integration.py`, find:

```python
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import Subject
from knowledge_service.audit import AuditAction
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import DocumentId, JobId, SourceId
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import (
    PURGE_FAILED,
    purge_document_chunks,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import Change, reconcile_document
from knowledge_service.ingestion.sources import SourceAccess, SourceRecord
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobNotRetryable, JobStatus
from knowledge_service.persistence.access import load_access_grants
from knowledge_service.persistence.audit import load_audit_events
from knowledge_service.persistence.chunks import load_chunks, load_searchable_chunks
from knowledge_service.persistence.documents import load_document
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
from knowledge_service.settings import Settings
```

and replace it with:

```python
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from job_fixtures import database_went_away
from knowledge_service.access import Subject
from knowledge_service.audit import AuditAction
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import DocumentId, JobId, SourceId
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import (
    purge_document_chunks,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import Change, reconcile_document
from knowledge_service.ingestion.sources import SourceAccess, SourceRecord
from knowledge_service.job_failures import JobFailure
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobNotRetryable, JobStatus
from knowledge_service.persistence.access import load_access_grants
from knowledge_service.persistence.audit import load_audit_events
from knowledge_service.persistence.chunks import load_chunks, load_searchable_chunks
from knowledge_service.persistence.documents import load_document
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
from knowledge_service.settings import Settings
```

In `test_a_failed_purge_keeps_chunks_and_can_be_retried`, raise the transient error and expect its code and a retry time:

In `tests/test_deletions_integration.py`, find:

```python
    retired = await publish(runtime, "old-policy.md", OLD_POLICY)
    (tombstone,) = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )

    async def broken_delete(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        raise ConnectionError("the database went away")

    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(
            runtime, job_id=tombstone.purge_job_id, now=LATER
        )

    assert (failed.status, failed.attempt, failed.last_error_code) == (
        JobStatus.FAILED,
        1,
        PURGE_FAILED,
    )
    assert await stored_chunk_count(runtime, retired.document_id) > 0
    assert await searchable_text(runtime) == []

    async with runtime.transaction() as connection:
        stored_job = await load_job(connection, job_id=tombstone.purge_job_id)
    assert stored_job == failed
    async with runtime.transaction() as connection:
        await save_job(connection, job=failed.retry(queued_at=LATEST))
```

and replace it with:

```python
    retired = await publish(runtime, "old-policy.md", OLD_POLICY)
    (tombstone,) = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )

    async def broken_delete(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        raise database_went_away()

    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(
            runtime, job_id=tombstone.purge_job_id, now=LATER
        )

    assert (failed.status, failed.attempt, failed.last_error_code) == (
        JobStatus.FAILED,
        1,
        JobFailure.DATABASE_UNAVAILABLE,
    )
    assert failed.retry_at is not None
    assert await stored_chunk_count(runtime, retired.document_id) > 0
    assert await searchable_text(runtime) == []

    async with runtime.transaction() as connection:
        stored_job = await load_job(connection, job_id=tombstone.purge_job_id)
    assert stored_job == failed
    async with runtime.transaction() as connection:
        await save_job(connection, job=failed.retry(queued_at=LATEST))
```

In `test_a_purge_that_used_every_attempt_is_queued_again`, raise the transient error, retry each time at the planned time, and check the budget ended it:

In `tests/test_deletions_integration.py`, find:

```python
    retired = await publish(runtime, "old-policy.md", OLD_POLICY)
    (tombstone,) = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )

    async def broken_delete(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        raise ConnectionError("the database went away")

    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(
            runtime, job_id=tombstone.purge_job_id, now=LATER
        )
        # Nothing is queued again while the job still has attempts left.
        assert (
            await requeue_unfinished_purges(runtime, source_id=SOURCE_ID, now=LATER)
            == ()
        )
        for _ in range(failed.max_attempts - 1):
            async with runtime.transaction() as connection:
                await save_job(connection, job=failed.retry(queued_at=LATER))
            failed = await purge_document_chunks(
                runtime, job_id=tombstone.purge_job_id, now=LATER
            )
    with pytest.raises(JobNotRetryable):
        failed.retry(queued_at=LATEST)

    (requeued,) = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=LATEST
    )
    await purge_document_chunks(runtime, job_id=requeued, now=LATEST)
```

and replace it with:

```python
    retired = await publish(runtime, "old-policy.md", OLD_POLICY)
    (tombstone,) = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )

    async def broken_delete(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        raise database_went_away()

    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(
            runtime, job_id=tombstone.purge_job_id, now=LATER
        )
        # Nothing is queued again while the job still has attempts left.
        assert (
            await requeue_unfinished_purges(runtime, source_id=SOURCE_ID, now=LATER)
            == ()
        )
        for _ in range(failed.max_attempts - 1):
            assert failed.retry_at is not None
            due = failed.retry_at
            async with runtime.transaction() as connection:
                await save_job(connection, job=failed.retry(queued_at=due))
            failed = await purge_document_chunks(
                runtime, job_id=tombstone.purge_job_id, now=due
            )
    # The last attempt used the budget, so no retry is scheduled.
    assert (failed.attempt, failed.retry_at) == (failed.max_attempts, None)
    with pytest.raises(JobNotRetryable):
        failed.retry(queued_at=LATEST)

    (requeued,) = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=LATEST
    )
    await purge_document_chunks(runtime, job_id=requeued, now=LATEST)
```

**Action 3:** the M08-T03 idempotency tests. Two of them change meaning: a repeated request now waits for the retry time.

Import the helper:

In `tests/test_job_idempotency_integration.py`, find:

```python
    OBSERVED_AT,
    SOURCE_ID,
    chunk_count,
    insert_source,
    offer_retired,
    stored_job,
```

and replace it with:

```python
    OBSERVED_AT,
    SOURCE_ID,
    chunk_count,
    database_went_away,
    insert_source,
    offer_retired,
    stored_job,
```

Import the retry pass and the codes, and drop the old constant:

In `tests/test_job_idempotency_integration.py`, find:

```python
from knowledge_service.identifiers import DocumentId, JobId
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import (
    PURGE_FAILED,
    purge_document_chunks,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.job_dispatch import PublishFailed, Submission, submit_job
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence import jobs as job_store
from knowledge_service.persistence.jobs import (
```

and replace it with:

```python
from knowledge_service.identifiers import DocumentId, JobId
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import (
    purge_document_chunks,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.job_dispatch import (
    PublishFailed,
    Submission,
    requeue_due_retries,
    submit_job,
)
from knowledge_service.job_failures import JobFailure
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence import jobs as job_store
from knowledge_service.persistence.jobs import (
```

Make `broken_delete` a transient database error:

In `tests/test_job_idempotency_integration.py`, find:

```python


async def broken_delete(connection: AsyncConnection, *, document_id: DocumentId) -> int:
    raise ConnectionError("the database went away")


async def stored_job_ids(runtime: DatabaseRuntime) -> list[JobId]:
```

and replace it with:

```python


async def broken_delete(connection: AsyncConnection, *, document_id: DocumentId) -> int:
    raise database_went_away()


async def stored_job_ids(runtime: DatabaseRuntime) -> list[JobId]:
```

Replace the two after-failure tests:

In `tests/test_job_idempotency_integration.py`, find:

```python
    assert await stored_job_ids(runtime) == []


async def test_asking_again_after_a_failed_attempt_queues_the_job_again(
    runtime: DatabaseRuntime,
) -> None:
    job = (await enqueue(runtime, new_job())).job
    async with runtime.transaction() as connection:
        await save_job(
            connection,
            job=job.start(started_at=NOW).fail(error_code="boom", finished_at=NOW),
        )

    again = await enqueue(runtime, new_job())

    assert not again.created
    assert (again.job.job_id, again.job.status, again.job.attempt) == (
        job.job_id,
        JobStatus.QUEUED,
        1,
    )
    assert await stored_job(runtime, job.job_id) == again.job


async def test_a_document_tombstoned_again_after_a_failed_purge_is_purged(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(runtime, job_id=purge_id, now=NOW)
    assert failed.status is JobStatus.FAILED
    await offer_retired(runtime, observed_at=NOW)

    [again] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )
    purged = await purge_document_chunks(runtime, job_id=again.purge_job_id, now=LATER)

    assert again.purge_job_id == purge_id
    assert (purged.status, purged.attempt) == (JobStatus.SUCCEEDED, 1)
    assert await chunk_count(runtime, document.document_id) == 0
```

and replace it with:

```python
    assert await stored_job_ids(runtime) == []


async def test_asking_again_after_a_failed_attempt_waits_for_its_retry_time(
    runtime: DatabaseRuntime,
) -> None:
    job = (await enqueue(runtime, new_job())).job
    failed = job.start(started_at=NOW).fail(
        error_code="boom", finished_at=NOW, retry_at=LATER
    )
    async with runtime.transaction() as connection:
        await save_job(connection, job=failed)

    again = await enqueue(runtime, new_job())

    # The same Job comes back, still waiting: a request does not hurry it.
    assert not again.created
    assert again.job == failed
    assert await stored_job(runtime, job.job_id) == failed


async def test_a_document_tombstoned_again_after_a_failed_purge_is_purged_when_due(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(runtime, job_id=purge_id, now=NOW)
    assert failed.retry_at is not None
    await offer_retired(runtime, observed_at=NOW)

    [again] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=NOW
    )
    assert again.purge_job_id == purge_id
    # Nothing runs it before its retry time; the retry pass does after.
    assert await requeue_due_retries(runtime, now=NOW) == ()
    assert await requeue_due_retries(runtime, now=failed.retry_at) == (purge_id,)
    purged = await purge_document_chunks(runtime, job_id=purge_id, now=failed.retry_at)

    assert (purged.status, purged.attempt) == (JobStatus.SUCCEEDED, 1)
    assert await chunk_count(runtime, document.document_id) == 0
```

In `test_two_messages_at_once_for_a_failing_attempt_run_it_once`, raise the transient error:

In `tests/test_job_idempotency_integration.py`, find:

```python
        calls.append(document_id)
        # Long enough for the other messages to queue up behind the claim.
        await asyncio.sleep(0.2)
        raise ConnectionError("the database went away")

    monkeypatch.setattr(deletions, "delete_chunks", slow_broken_delete)
    results = await asyncio.gather(
```

and replace it with:

```python
        calls.append(document_id)
        # Long enough for the other messages to queue up behind the claim.
        await asyncio.sleep(0.2)
        raise database_went_away()

    monkeypatch.setattr(deletions, "delete_chunks", slow_broken_delete)
    results = await asyncio.gather(
```

and expect its code:

In `tests/test_job_idempotency_integration.py`, find:

```python
    assert (stored.status, stored.attempt, stored.last_error_code) == (
        JobStatus.FAILED,
        1,
        PURGE_FAILED,
    )
```

and replace it with:

```python
    assert (stored.status, stored.attempt, stored.last_error_code) == (
        JobStatus.FAILED,
        1,
        JobFailure.DATABASE_UNAVAILABLE,
    )
```

**Action 4:** two more tests saved a failure and then retried it, so they plan a retry time.

In `test_a_job_queued_again_needs_a_new_message`:

In `tests/test_job_dispatch_integration.py`, find:

```python
    job = new_job()
    await submit_job(runtime, job, publisher=FakePublisher(), now=NOW)
    async with runtime.transaction() as connection:
        failed = job.start(started_at=NOW).fail(error_code="boom", finished_at=NOW)
        await save_job(connection, job=failed)
        await save_job(connection, job=failed.retry(queued_at=LATER))
    assert await published_at(runtime, job.job_id) is None
```

and replace it with:

```python
    job = new_job()
    await submit_job(runtime, job, publisher=FakePublisher(), now=NOW)
    async with runtime.transaction() as connection:
        failed = job.start(started_at=NOW).fail(
            error_code="boom", finished_at=NOW, retry_at=LATER
        )
        await save_job(connection, job=failed)
        await save_job(connection, job=failed.retry(queued_at=LATER))
    assert await published_at(runtime, job.job_id) is None
```

In `tests/test_records_integration.py`, plan the retry:

In `tests/test_records_integration.py`, find:

```python
            failed = running.fail(
                error_code="source_unreachable",
                finished_at=OBSERVED_AT + timedelta(seconds=2),
            )
            await save_job(connection, job=failed)
```

and replace it with:

```python
            failed = running.fail(
                error_code="source_unreachable",
                finished_at=OBSERVED_AT + timedelta(seconds=2),
                retry_at=OBSERVED_AT + timedelta(seconds=3),
            )
            await save_job(connection, job=failed)
```

and check the time survives the database:

In `tests/test_records_integration.py`, find:

```python
        assert stored_job.status is JobStatus.FAILED
        assert stored_job.attempt == 1
        assert stored_job.last_error_code == "source_unreachable"

        async with engine.begin() as connection:
            retried = failed.retry(queued_at=OBSERVED_AT + timedelta(seconds=3))
```

and replace it with:

```python
        assert stored_job.status is JobStatus.FAILED
        assert stored_job.attempt == 1
        assert stored_job.last_error_code == "source_unreachable"
        assert stored_job.retry_at == OBSERVED_AT + timedelta(seconds=3)

        async with engine.begin() as connection:
            retried = failed.retry(queued_at=OBSERVED_AT + timedelta(seconds=3))
```

**Action 5:** create the retry tests. They use a fixed jitter of 0.5, so every retry time is exact.

| Test | What it proves |
|---|---|
| `test_a_transient_failure_is_queued_again_only_when_due` | one second early: nothing; on time: queued, unpublished, sent, then succeeds |
| `test_a_transient_failure_stops_at_the_attempt_budget` | exactly 3 attempts, the planned waits, then nothing ever again |
| `test_a_permanent_failure_is_never_queued_again` | a `KeyError` stops at attempt 1 |
| `test_a_permanent_purge_failure_is_not_asked_for_again_by_later_passes` | block 7: three sync passes add nothing |
| `test_a_permanent_failure_stops_the_loop_even_after_a_late_retry` | block 7: "latest" by `finished_at`, not `queued_at` |
| `test_a_lost_attempt_is_not_recorded_against_a_later_one` | block 6: the same-attempt check |
| `test_a_lost_connection_is_recorded_as_a_retried_failure` | block 6: a real `pg_terminate_backend` |
| `test_a_cancelled_attempt_leaves_the_job_queued_and_nothing_deleted` | block 5 and known limits: cancellation and `TimeLimitExceeded` roll back whole |
| `test_two_retry_passes_at_once_queue_each_job_once` | `SKIP LOCKED` keeps two passes apart |
| `test_the_retry_pass_takes_the_oldest_retry_first_up_to_its_limit`, `..._limit_is_bounded`, `..._needs_utc` | order and bounds |
| `test_the_database_refuses_a_retry_time_on_a_job_that_did_not_fail` | `ck_jobs_retry_at` |
| `test_the_migration_schedules_open_failures_and_keeps_final_ones_closed` | the migration up and down |


Create `tests/test_job_retry_integration.py`:

```python
"""Retries owned by the stored Job, against real PostgreSQL.

A failed attempt is classified. A transient one gets a retry time, and the
retry pass queues the Job again once that time has come, until its attempts
run out. A permanent one is never queued again.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic import command
from dramatiq.middleware.time_limit import TimeLimitExceeded
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from database_fixtures import migration_config, migration_environment
from job_fixtures import (
    OBSERVED_AT,
    SOURCE_ID,
    chunk_count,
    database_went_away,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, JobId
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import (
    purge_document_chunks,
    requeue_unfinished_purges,
)
from knowledge_service.job_dispatch import publish_pending_jobs, requeue_due_retries
from knowledge_service.job_failures import JobFailure, RetryPolicy
from knowledge_service.jobs import Job, JobAlreadyClaimed, JobKind, JobStatus
from knowledge_service.persistence.jobs import enqueue_job, requeue_due_jobs, save_job
from knowledge_service.persistence.schema import chunks, jobs
from knowledge_service.settings import Settings

pytestmark = pytest.mark.integration

NOW = OBSERVED_AT + timedelta(minutes=5)
FAR_FUTURE = OBSERVED_AT + timedelta(days=30)
# The middle of every delay window, so retry times are exact in assertions.
POLICY = RetryPolicy(jitter=lambda: 0.5)


class RecordingPublisher:
    """Records every Job it is asked to send."""

    kinds = frozenset({JobKind.PURGE_DOCUMENT_CHUNKS})

    def __init__(self) -> None:
        self.sent: list[JobId] = []

    def publish(self, job: Job) -> None:
        self.sent.append(job.job_id)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    await insert_source(runtime)
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def job_ids(runtime: DatabaseRuntime) -> list[JobId]:
    async with runtime.transaction() as connection:
        return [
            JobId(job_id)
            for job_id in await connection.scalars(sa.select(jobs.c.job_id))
        ]


async def drop_my_connection(
    connection: AsyncConnection, *, document_id: DocumentId
) -> int:
    # The server ends this session, as a restart or a failover would.
    await connection.execute(sa.text("select pg_terminate_backend(pg_backend_pid())"))
    return 0


async def lost_connection(
    connection: AsyncConnection, *, document_id: DocumentId
) -> int:
    raise database_went_away()


async def bug(connection: AsyncConnection, *, document_id: DocumentId) -> int:
    raise KeyError("a programming error")


async def purge(runtime: DatabaseRuntime, job_id: JobId, now: datetime) -> Job:
    return await purge_document_chunks(
        runtime, job_id=job_id, now=now, retry_policy=POLICY
    )


async def test_a_transient_failure_is_queued_again_only_when_due(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", lost_connection)

    failed = await purge(runtime, purge_id, NOW)

    assert (failed.status, failed.attempt, failed.last_error_code) == (
        JobStatus.FAILED,
        1,
        JobFailure.DATABASE_UNAVAILABLE,
    )
    assert failed.retry_at == NOW + POLICY.delay(1)
    due = failed.retry_at
    assert due is not None
    assert await requeue_due_retries(runtime, now=due - timedelta(seconds=1)) == ()
    assert await requeue_due_retries(runtime, now=due) == (purge_id,)

    requeued = await stored_job(runtime, purge_id)
    assert (requeued.status, requeued.attempt, requeued.retry_at) == (
        JobStatus.QUEUED,
        1,
        None,
    )
    # Queued again means unpublished again: the relay sends a new message.
    publisher = RecordingPublisher()
    await publish_pending_jobs(runtime, publisher=publisher, now=due)
    assert publisher.sent == [purge_id]

    monkeypatch.undo()
    succeeded = await purge(runtime, purge_id, due)
    assert (succeeded.status, succeeded.attempt) == (JobStatus.SUCCEEDED, 1)
    assert await chunk_count(runtime, document.document_id) == 0


async def test_a_transient_failure_stops_at_the_attempt_budget(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", lost_connection)
    now = NOW
    attempts: list[Job] = []

    for _ in range(10):
        attempts.append(await purge(runtime, purge_id, now))
        if attempts[-1].retry_at is None:
            break
        now = attempts[-1].retry_at
        assert await requeue_due_retries(runtime, now=now) == (purge_id,)

    last = attempts[-1]
    assert len(attempts) == last.max_attempts == 3
    assert (last.status, last.attempt, last.retry_at) == (JobStatus.FAILED, 3, None)
    assert [job.retry_at for job in attempts[:-1]] == [
        NOW + POLICY.delay(1),
        NOW + POLICY.delay(1) + POLICY.delay(2),
    ]
    assert await requeue_due_retries(runtime, now=FAR_FUTURE) == ()


async def test_a_permanent_failure_is_never_queued_again(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", bug)

    failed = await purge(runtime, purge_id, NOW)

    assert (failed.attempt, failed.last_error_code, failed.retry_at) == (
        1,
        JobFailure.PROGRAMMING_ERROR,
        None,
    )
    assert await requeue_due_retries(runtime, now=FAR_FUTURE) == ()
    assert (await stored_job(runtime, purge_id)).status is JobStatus.FAILED


async def test_a_permanent_purge_failure_is_not_asked_for_again_by_later_passes(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", bug)
    await purge(runtime, purge_id, NOW)

    # Each synchronization pass asks for unfinished purges; none comes back.
    for _ in range(3):
        assert (
            await requeue_unfinished_purges(
                runtime, source_id=SOURCE_ID, now=FAR_FUTURE
            )
            == ()
        )

    assert await job_ids(runtime) == [purge_id]
    assert await chunk_count(runtime, document.document_id) > 0


async def test_a_permanent_failure_stops_the_loop_even_after_a_late_retry(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", lost_connection)
    now = NOW
    while (failed := await purge(runtime, purge_id, now)).retry_at is not None:
        now = failed.retry_at
        await requeue_due_retries(runtime, now=now)
    # A synchronization pass that started before that last retry asks for a
    # new purge, which then fails permanently.
    monkeypatch.setattr(deletions, "delete_chunks", bug)
    [second] = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=NOW + timedelta(seconds=1)
    )
    await purge(runtime, second, now + timedelta(hours=1))

    for hours in (2, 3, 4):
        assert (
            await requeue_unfinished_purges(
                runtime, source_id=SOURCE_ID, now=NOW + timedelta(seconds=hours)
            )
            == ()
        )
    assert len(await job_ids(runtime)) == 2
    assert await chunk_count(runtime, document.document_id) > 0


async def test_a_lost_attempt_is_not_recorded_against_a_later_one(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    load_job = deletions.load_job
    loads: list[JobId] = []

    async def meanwhile_another_delivery(
        connection: AsyncConnection, *, job_id: JobId, for_update: bool = False
    ) -> Job | None:
        loads.append(job_id)
        # The second load is this delivery's own, after its connection died.
        # Before it, another delivery fails the Job and it is queued again.
        if len(loads) == 2:
            monkeypatch.setattr(deletions, "delete_chunks", lost_connection)
            other = await purge(runtime, purge_id, NOW)
            assert other.retry_at is not None
            await requeue_due_retries(runtime, now=other.retry_at)
        return await load_job(connection, job_id=job_id, for_update=for_update)

    monkeypatch.setattr(deletions, "load_job", meanwhile_another_delivery)
    monkeypatch.setattr(deletions, "delete_chunks", drop_my_connection)

    # This delivery's clock is an hour ahead of the relay's.
    with pytest.raises(JobAlreadyClaimed, match="another delivery") as refused:
        await purge(runtime, purge_id, NOW + timedelta(hours=1))

    stored = await stored_job(runtime, purge_id)
    assert (stored.status, stored.attempt) == (JobStatus.QUEUED, 1)
    assert refused.value.job == stored


async def test_a_lost_connection_is_recorded_as_a_retried_failure(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)

    monkeypatch.setattr(deletions, "delete_chunks", drop_my_connection)
    failed = await purge(runtime, purge_id, NOW)

    assert (failed.status, failed.attempt, failed.last_error_code) == (
        JobStatus.FAILED,
        1,
        JobFailure.DATABASE_UNAVAILABLE,
    )
    assert failed.retry_at == NOW + POLICY.delay(1)
    assert await stored_job(runtime, purge_id) == failed
    assert await chunk_count(runtime, document.document_id) > 0


@pytest.mark.parametrize(
    "interrupt", [asyncio.CancelledError, TimeLimitExceeded], ids=["cancel", "limit"]
)
async def test_a_cancelled_attempt_leaves_the_job_queued_and_nothing_deleted(
    runtime: DatabaseRuntime,
    monkeypatch: pytest.MonkeyPatch,
    interrupt: type[BaseException],
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    chunks_before = await chunk_count(runtime, document.document_id)

    async def interrupted(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        await connection.execute(
            sa.delete(chunks).where(chunks.c.document_id == document_id.value)
        )
        raise interrupt()

    monkeypatch.setattr(deletions, "delete_chunks", interrupted)
    with pytest.raises(interrupt):
        await purge(runtime, purge_id, NOW)

    # Rolled back whole: the stuck-work sweep (M08-T08) owns this Job now.
    stored = await stored_job(runtime, purge_id)
    assert (stored.status, stored.attempt) == (JobStatus.QUEUED, 0)
    assert await chunk_count(runtime, document.document_id) == chunks_before
    assert await requeue_due_retries(runtime, now=FAR_FUTURE) == ()


async def test_two_retry_passes_at_once_queue_each_job_once(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        for _ in range(4):
            job = Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=uuid.uuid4(),
                queued_at=OBSERVED_AT,
            )
            await enqueue_job(connection, job)
            failed = job.start(started_at=NOW).fail(
                error_code="boom", finished_at=NOW, retry_at=NOW
            )
            await save_job(connection, job=failed)

    async def pass_and_hold() -> tuple[Job, ...]:
        async with runtime.transaction() as connection:
            requeued = await requeue_due_jobs(connection, now=NOW, limit=10)
            # Keep the rows locked, so the other pass runs meanwhile.
            await asyncio.sleep(0.2)
            return requeued

    first, second = await asyncio.gather(pass_and_hold(), pass_and_hold())

    ids = [job.job_id for job in first + second]
    assert len(ids) == len(set(ids)) == 4


async def test_the_retry_pass_takes_the_oldest_retry_first_up_to_its_limit(
    runtime: DatabaseRuntime,
) -> None:
    stored: list[Job] = []
    async with runtime.transaction() as connection:
        for minutes in (2, 1):
            job = Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=uuid.uuid4(),
                queued_at=OBSERVED_AT,
            )
            await enqueue_job(connection, job)
            retry_at = NOW + timedelta(minutes=minutes)
            failed = job.start(started_at=NOW).fail(
                error_code="boom", finished_at=NOW, retry_at=retry_at
            )
            stored.append(await save_job(connection, job=failed))

    requeued = await requeue_due_retries(runtime, now=FAR_FUTURE, limit=1)

    assert requeued == (stored[1].job_id,)


@pytest.mark.parametrize("limit", [0, 1_001, True, 2.0])
async def test_the_retry_pass_limit_is_bounded(
    runtime: DatabaseRuntime, limit: object
) -> None:
    with pytest.raises(ValueError, match="limit"):
        await requeue_due_retries(runtime, now=NOW, limit=limit)  # pyright: ignore[reportArgumentType]


async def test_the_retry_pass_needs_utc(runtime: DatabaseRuntime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        await requeue_due_retries(runtime, now=datetime(2026, 11, 23, 9, 0))


async def test_the_database_refuses_a_retry_time_on_a_job_that_did_not_fail(
    runtime: DatabaseRuntime,
) -> None:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_DOCUMENT_CHUNKS,
        target_id=uuid.uuid4(),
        queued_at=OBSERVED_AT,
    )
    async with runtime.transaction() as connection:
        await enqueue_job(connection, job)

    with pytest.raises(IntegrityError, match="ck_jobs_retry_at"):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.update(jobs)
                .where(jobs.c.job_id == job.job_id.value)
                .values(retry_at=NOW)
            )


def test_the_migration_schedules_open_failures_and_keeps_final_ones_closed(
    migrated_database: str,
) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    with migration_environment(url):
        command.downgrade(config, "e5f6a7b8c9d0")
    insert_failed = sa.text(
        "insert into jobs (job_id, kind, target_id, status, attempt, max_attempts,"
        " queued_at, started_at, finished_at, last_error_code) values (:job_id,"
        " 'purge_document_chunks', :target_id, 'failed', :attempt, 3, :at, :at,"
        " :at, 'chunk_purge_failed')"
    )
    with_attempts_left, used_up = uuid.uuid4(), uuid.uuid4()
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            for job_id, attempt in ((with_attempts_left, 1), (used_up, 3)):
                connection.execute(
                    insert_failed,
                    {
                        "job_id": job_id,
                        "target_id": uuid.uuid4(),
                        "attempt": attempt,
                        "at": NOW,
                    },
                )

        with migration_environment(url):
            command.upgrade(config, "head")
        with engine.connect() as connection:
            retry_at = dict(
                connection.execute(sa.select(jobs.c.job_id, jobs.c.retry_at)).all()
            )

        # Open before, so still open: due at once. Used up stays closed.
        assert retry_at == {with_attempts_left: NOW, used_up: None}

        # A permanent failure with attempts left must stay closed on the way down.
        with engine.begin() as connection:
            connection.execute(
                sa.update(jobs)
                .where(jobs.c.job_id == with_attempts_left)
                .values(retry_at=None)
            )
        with migration_environment(url):
            command.downgrade(config, "e5f6a7b8c9d0")
        with engine.connect() as connection:
            attempt = connection.scalar(
                sa.text("select attempt from jobs where job_id = :job_id"),
                {"job_id": with_attempts_left},
            )
        assert attempt == 3
        with migration_environment(url):
            command.upgrade(config, "head")
    finally:
        engine.dispose()
```

**Check:** `uv run pytest tests/test_job_retry_integration.py tests/test_deletions_integration.py tests/test_job_idempotency_integration.py tests/test_job_dispatch_integration.py tests/test_records_integration.py -q -m integration` reports `65 passed`.

### Step 9 — No coding in this step: break it on purpose

This step makes every failure look retryable and shows which tests notice.

**Action 1:** in `fail_attempt` in `src/knowledge_service/job_failures.py`, change:

```python
    if failure.transient and failed_attempts < job.max_attempts:
```

to:

```python
    if failed_attempts < job.max_attempts:
```

**Action 2:** run:

```bash
uv run pytest tests/test_job_failures.py tests/test_job_retry_integration.py -q -m "integration or not integration"
```

You see `4 failed, 54 passed`. Bugs and refusals now get a retry time, so the tests that say "permanent failures never retry" fail.

**Action 3:** undo the change and run the command again: `58 passed`.

**Check:** compare the count with your Step 1 prediction, and note it in the evidence file.

### Step 10 — No coding in this step: update three documents

This step records who owns retries and timeouts where readers look for it.


**Action 1:** in the architecture, say in the `worker` row that the relay runs the retry pass first:

In `docs/architecture.md`, find:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs and no tools, where a reply holding a tool call is unreadable; `AnswerSettings` gives the budget), `has_active_content` / `unsourced_links` (`answer_content`, what an Answer may hold whatever a Document told the model: no Markdown image, loading HTML tag, or mermaid block, and only links an Evidence it cites contains; `held_from` marks where a stream stops sending text until the check), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`, with `DatabaseAuditTrail` (`audit_trail`) as its `AuditTrail`: one `question.asked` audit event per stream, committed before the decision's Citations and completion are sent; finds the Evidence the request's reader may see, rereads it as the reader with `evidence_still_visible`, and judges it before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply, has its `CitationCheck` (`CurrentCitations`, each call in a short transaction of its own) reread every cited Chunk as the reader with `confirm_citations`, withdrawing the Answer if one fails, and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `VectorEvidenceSource` (`vector_evidence`, the stream's Evidence from PostgreSQL: checks the reader, embeds the Question, requires one vector from its own model, and runs the vector search with the request's Authorization Context, so eligibility is decided in the same SQL that ranks; no reranking, so its signals carry no score), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |
| `worker` | Queue transport: the Redis broker and the worker process | `build_broker` (`broker`, a Dramatiq `RedisBroker` with JSON messages, the `knowledge-service.v1` namespace, the `maintenance` and `synchronization` queues, a 60-second default time limit, a one-day message age limit, and no Dramatiq retries; `ActorOwnsOptions` drops any limit a message sets for itself, so only its actor's limits apply; Redis calls make one attempt with bounded timeouts), `declare_actors` / `send_ping` (`actors`, the harmless `ping` job, whose token is checked on both sides), `broker_ready` (`health`, a readiness probe that is false, within one overall deadline, whenever Redis does not answer PING or any part of the check fails), `declare_job_actors` / `DramatiqJobPublisher` / `run_stored_job` (`jobs`, one `run_<queue>_job` actor per queue of the kinds in `HANDLERS`, today only `purge_document_chunks`; a message carries one canonical Job ID, and the worker reads the Job from PostgreSQL, runs it only while it is queued, and returns the stored Job as the outcome of a duplicate; `JobRunner` gives each message its own event loop and database engine), `relay` (one pass of `publish_pending_jobs`), and `main`, the module the `dramatiq` command loads |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level. Among them, `roles` holds the Application Roles policy:
```

and replace it with:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs and no tools, where a reply holding a tool call is unreadable; `AnswerSettings` gives the budget), `has_active_content` / `unsourced_links` (`answer_content`, what an Answer may hold whatever a Document told the model: no Markdown image, loading HTML tag, or mermaid block, and only links an Evidence it cites contains; `held_from` marks where a stream stops sending text until the check), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`, with `DatabaseAuditTrail` (`audit_trail`) as its `AuditTrail`: one `question.asked` audit event per stream, committed before the decision's Citations and completion are sent; finds the Evidence the request's reader may see, rereads it as the reader with `evidence_still_visible`, and judges it before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply, has its `CitationCheck` (`CurrentCitations`, each call in a short transaction of its own) reread every cited Chunk as the reader with `confirm_citations`, withdrawing the Answer if one fails, and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `VectorEvidenceSource` (`vector_evidence`, the stream's Evidence from PostgreSQL: checks the reader, embeds the Question, requires one vector from its own model, and runs the vector search with the request's Authorization Context, so eligibility is decided in the same SQL that ranks; no reranking, so its signals carry no score), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |
| `worker` | Queue transport: the Redis broker and the worker process | `build_broker` (`broker`, a Dramatiq `RedisBroker` with JSON messages, the `knowledge-service.v1` namespace, the `maintenance` and `synchronization` queues, a 60-second default time limit, a one-day message age limit, and no Dramatiq retries; `ActorOwnsOptions` drops any limit a message sets for itself, so only its actor's limits apply; Redis calls make one attempt with bounded timeouts), `declare_actors` / `send_ping` (`actors`, the harmless `ping` job, whose token is checked on both sides), `broker_ready` (`health`, a readiness probe that is false, within one overall deadline, whenever Redis does not answer PING or any part of the check fails), `declare_job_actors` / `DramatiqJobPublisher` / `run_stored_job` (`jobs`, one `run_<queue>_job` actor per queue of the kinds in `HANDLERS`, today only `purge_document_chunks`; a message carries one canonical Job ID, and the worker reads the Job from PostgreSQL, runs it only while it is queued, and returns the stored Job as the outcome of a duplicate; `JobRunner` gives each message its own event loop and database engine), `relay` (one pass: `requeue_due_retries`, then `publish_pending_jobs`), and `main`, the module the `dramatiq` command loads |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level. Among them, `roles` holds the Application Roles policy:
```

update the intent paragraph, and add the retries and timeouts paragraph after it:

In `docs/architecture.md`, find:

```markdown

A Job's intent is its kind and target, such as "purge the Chunks of Document
D". `enqueue_job` keeps at most one open Job per intent: queued, running, or
failed with attempts left. The partial unique index `uq_jobs_open_intent`
enforces it, so asking for the same work again, even at the same moment,
returns the open Job (`Enqueued.created` is false, `Submission.duplicate` is
true) instead of storing a second one, and an open Job that failed is queued
again for its next attempt. A handler claims a queued Job under a row lock and
stores its outcome, success or failure, in the same transaction; a claim that
finds the Job no longer queued raises `JobAlreadyClaimed` with the stored Job,
and `run_stored_job` returns that Job as the outcome of every duplicate
message, so every delivery reports the same result.

`telemetry` decides what leaves the process as logs. `configure_telemetry`
installs one root handler that writes JSON lines, and its `TelemetryFilter`
keeps only the fields `TELEMETRY_FIELDS` lists, with plain values, only this
```

and replace it with:

```markdown

A Job's intent is its kind and target, such as "purge the Chunks of Document
D". `enqueue_job` keeps at most one open Job per intent: queued, running, or
failed with a retry scheduled. The partial unique index `uq_jobs_open_intent`
enforces it, so asking for the same work again, even at the same moment,
returns the open Job (`Enqueued.created` is false, `Submission.duplicate` is
true) instead of storing a second one, and an open Job that failed keeps
waiting for its retry time. A handler claims a queued Job under a row lock and
stores its outcome, success or failure, in the same transaction; a claim that
finds the Job no longer queued raises `JobAlreadyClaimed` with the stored Job,
and `run_stored_job` returns that Job as the outcome of every duplicate
message, so every delivery reports the same result.

The stored Job is the one layer that retries. Dramatiq retries nothing, model
adapters keep their SDK retries off, and repository code never loops.
`fail_attempt` (`job_failures`) classifies a failed attempt by its error as a
Source, database, or model that is unavailable (transient) or refused
(permanent), or a programming error (permanent), and stores that code. Only a
transient failure with attempts left gets a `retry_at`: half of a doubling
backoff (30 seconds, capped at 15 minutes) plus a random share of the other
half. `requeue_due_retries`, run first by every `make worker-relay` pass, is
the only path a failed Job takes back to the queue, and only once its
`retry_at` has come; `Job.retry` refuses a permanent failure and an early one.
Each call owns its own timeout: the database's statement, connect, and pool
timeouts and each model adapter's deadline end a slow call as a classified,
retried failure. When the database drops the connection under an attempt,
the claim's lock goes with it, so the purge records the attempt in a new
transaction by the error that started it. The actor's 60-second time limit
and a cancellation are only backstops: the attempt's transaction rolls back
and the Job waits as queued for M08-T08. A tombstoned Document whose last
purge failed permanently is not given a new purge by later synchronization
passes; one that ran out of attempts on a passing failure is.

`telemetry` decides what leaves the process as logs. `configure_telemetry`
installs one root handler that writes JSON lines, and its `TelemetryFilter`
keeps only the fields `TELEMETRY_FIELDS` lists, with plain values, only this
```

and update the `jobs` row of the invariants table:

In `docs/architecture.md`, find:

```markdown
| `messages` | One Message per `(conversation_id, ordinal)`; only an assistant Message has an `outcome`: `answered`, `abstained`, or `failed`. |
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code; at most one open Job (queued, running, or failed with attempts left) per kind and target. |
| `audit_events` | An action, target kind, decision (`allowed` or `denied`), and outcome from fixed lists, with optional request and Synchronization Run IDs; no raw content: metadata keys are short names, string values one plain word, and the bag is capped at 1024 bytes. Rows are only ever inserted. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |
```

and replace it with:

```markdown
| `messages` | One Message per `(conversation_id, ordinal)`; only an assistant Message has an `outcome`: `answered`, `abstained`, or `failed`. |
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code; a retry time only on a failed Job with attempts left, never before it finished; at most one open Job (queued, running, or failed with a retry scheduled) per kind and target. |
| `audit_events` | An action, target kind, decision (`allowed` or `denied`), and outcome from fixed lists, with optional request and Synchronization Run IDs; no raw content: metadata keys are short names, string values one plain word, and the bag is capped at 1024 bytes. Rows are only ever inserted. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |
```

**Action 2:** in the threat model, update the residual of `A replayed job writes twice` and add a row after it:

In `docs/threat-model.md`, find:

```markdown
| A file removed or unreadable deletes or keeps the wrong Documents | An unreadable folder is unavailable, not deleted; a failed pass tombstones nothing | Incremental sync (M08-T10) |
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one; whatever the model then writes passes the Answer's content checks | None |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A failed Job with attempts left runs again only when the same work is requested again, until retries are owned (M08-T04); a message for an earlier attempt can start the next one (M08-T04) |

### Retrieval
```

and replace it with:

```markdown
| A file removed or unreadable deletes or keeps the wrong Documents | An unreadable folder is unavailable, not deleted; a failed pass tombstones nothing | Incremental sync (M08-T10) |
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one; whatever the model then writes passes the Answer's content checks | None |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |

### Retrieval
```

**Action 3:** in the operations guide, say that the relay must run on a schedule:

In `docs/operations.md`, find:

```markdown

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

and replace it with:

```markdown

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. A failed Job comes back only through `make worker-relay`, which queues due retries before it publishes, so run it on a schedule. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

**Check:** `make docs-check` exits 0.

### Step 11 — No coding in this step: gates, evidence, and progress

This step runs every gate and finishes the records.

**Action 1:** run every gate, one after another:

```bash
make format-check
make lint
make typecheck
make test-unit
make test-integration
make docs-check
```

**Action 2:** finish the evidence file: the gate results and the Step 9 count under "Verification", the review findings and known limits under "Review", the design under "Design" and "Decision", and your answers to the reflection questions below. Then mark the task completed in `docs/curriculum/PROGRESS.md` and commit with `m08-t04: assign retry and timeout ownership`.

**Check:** every gate passes, and `git status` is clean after the commit.

## Reflection questions

- A purge fails at 10:00:00 because the database dropped the connection. Using the table in "Before you begin", block 3, between which two times will its second attempt run, and what decides the exact moment?
- Suppose the model adapter kept its SDK retries on, 3 per call, and the Job also retried 3 times. How many calls would a model that is down receive for one Job, and why does the waiting between them no longer follow one plan?
- A Confluence token expires. Which code does the synchronization Job get, why is it not retried, and what must a person do before the work can succeed?
- The database ends the worker's session in the middle of a purge. Walk through what the purge does now, and say why it must check that the Job is still on the same attempt before saving the failure.
- What happens to a purge cut off by the 60-second time limit, and which later lesson makes sure it runs again?
- What would you see in the `jobs` table, or in the logs, that tells you the classification is wrong: a failure that should be retried but is not, or the other way round?

## Completion checklist

- [ ] The evidence file has the invariant, the owners, and your predictions, written before the change.
- [ ] A Job carries `retry_at`; `retry` refuses a final failure, a used-up budget, and an early time; `tests/test_jobs.py` has 9 tests and passes.
- [ ] `job_failures.py` classifies errors into seven codes and plans `retry_at` only for a transient failure with attempts left; `tests/test_job_failures.py` (40) passes.
- [ ] Migration `f6a7b8c9d0e1` adds `retry_at`, `ck_jobs_retry_at`, `ix_jobs_due_retry`, and the new open-Job rule, and runs both ways.
- [ ] `enqueue_job` no longer queues a failed Job at once; `requeue_due_jobs` and `requeue_due_retries` are the only path back to the queue; `make worker-relay` runs it first.
- [ ] The purge uses `fail_attempt`, records a lost attempt in a new transaction on the same attempt only, and a permanently failed purge is not asked for again.
- [ ] `tests/test_job_retry_integration.py` (18) and the updated integration tests pass.
- [ ] You ran the deliberate break, saw the tests fail, and undid it.
- [ ] `docs/architecture.md`, `docs/threat-model.md`, and `docs/operations.md` describe who owns retries and timeouts.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
