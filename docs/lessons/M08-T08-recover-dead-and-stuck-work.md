# M08-T08 — Recover dead and stuck work

Task: [M08-T08 — Recover dead and stuck work](../curriculum/milestones/M08-jobs-and-confluence/M08-T08-recover-dead-and-stuck-work.md)

## What you will do

This lesson finds Jobs that stopped moving and moves them on. A worker can die in the middle of a Job, and a queue message can get lost before any worker takes it. Today both leave the Job stuck forever: `running` with an old heartbeat, or `queued` with nobody coming. After this lesson, every relay pass finds such Jobs, fails the lost attempt with a clear code, and the retry rules from M08-T04 take it from there. A Job that runs out of attempts waits for an administrator, who can retry it or cancel it through the API.

A concrete example. Two synchronization Jobs run at the same time. J7 is on worker A, which is killed. J8 is on worker B, which is slow but alive. The relay runs every minute.

| Time | Worker A (J7) | Worker B (J8) | What the relay pass does |
|---|---|---|---|
| 10:00 | claims J7, attempt 1 | claims J8, attempt 1 | nothing to recover |
| 10:04 | reports 120 processed | reports 40 processed | nothing: both were heard from just now |
| 10:05 | the process is killed | | nothing: J7 was heard from one minute ago |
| 10:08 | | reports 80 processed | nothing |
| 10:09 | | | J7 has been silent for five minutes: attempt 1 is failed with `worker_lost`, its row keeps "120 processed", and a retry is planned about 15 to 30 seconds later |
| 10:10 | | | J7 is queued again and published; a worker claims it as attempt 2 |
| 11:00 | | still reporting every four minutes | J8 is left alone: slow is not dead |

| What the task asks | Where it is done |
|---|---|
| Detect stale Jobs (running, worker gone) | Blocks 1 and 2; Step 3: `recover_stuck_jobs` |
| Detect orphaned Jobs (queued, message gone) | Block 3; Step 3 |
| Detect exhausted Jobs | Block 4; Step 2: `JobCondition.EXHAUSTED` |
| Expose operator decisions | Block 4; Step 4: `GET /v1/job-decisions` |
| Safe retry or cancellation | Block 4; Step 4: `POST /v1/jobs/{job_id}/retry` and `/cancel` |
| A recovery drill tells running-slow from dead and keeps the prior attempt's evidence | Step 5: the first seven tests |
| Replay and cancellation behaviour explicit | Blocks 2 and 4 |
| Update only affected documentation | Step 7: architecture, operations, security, threat model |

This lesson includes coding: Steps 2 to 5 write code, a migration, and tests. Steps 1, 6, 7, and 8 need no code, although Step 7 edits four documents.

Terms used in this lesson:

- **Stale Job:** a running Job nobody has heard from for five minutes. Its worker is presumed dead.
- **Orphaned Job:** a queued Job whose message was sent 30 minutes ago and that no worker took up. The message is presumed lost.
- **Exhausted Job:** a Job that failed on a passing problem, such as the database being down, and used up all its attempts.
- **Failed permanently:** a Job that failed on something retrying will not fix, such as a refused credential.
- **Operator decision:** what an administrator chooses for a Job that cannot move on by itself: retry it once more, or cancel it.
- **Recovery pass:** the first step of every `make worker-relay` run, which finds stale and orphaned Jobs and fails their lost attempt.

## Before you begin

This section explains every choice the code rests on, step by step. Each block ends with what this lesson changes.

### 1. Slow or dead: listen by the database's clock

**The situation.** J7 and J8 are both `running`. From the outside they look the same: a row that says `running`. Nobody can watch a process die. All we can see is that it stopped talking.

**The hidden fact.** "Stopped talking" needs a clock. Every report in M08-T07 carries a heartbeat stamped by the worker's own clock. Clocks on different machines disagree. The adversarial review showed what that does:

| Moment (real time) | Worker A, clock six minutes behind | `heartbeat_at` stored | Relay at 10:11, judging by `heartbeat_at` |
|---|---|---|---|
| 10:10 | reports, stamped 10:04 | 10:04 | |
| 10:11 | | 10:04 | "silent for seven minutes": a live worker is declared dead, and its next report is refused |

**What changes.** Each attempt row gets a new column, `heard_at`. The database stamps it with its own clock on every report, so no worker's clock decides it. Recovery measures silence as `now() - heard_at`, both in the database.

| Was | Became |
|---|---|
| silent = relay's `now` minus the worker's `heartbeat_at` | silent = the database's `now()` minus `heard_at`, stamped by the database |
| a worker with a wrong clock looks dead or alive by mistake | the worker's clock does not matter |

A report that is refused still counts as hearing from the worker. The review found a worker whose clock stepped back three minutes: every report it sent was refused as "going back", so `heard_at` never moved, and the live worker was declared dead. Now, when a report is refused for what it says (a value going back, or a time too far ahead), `report_progress` stamps `heard_at` in a second, separate transaction, then raises the refusal.

A Job that has no attempt row, because its handler never stored one, falls back to its start time. That time comes from the worker's clock, which is a known limit.

`heartbeat_at` stays as it was. It is what the worker says about itself and what the API shows. `heard_at` is what the database observed, and only recovery uses it.

The analogy: a lifeguard does not ask swimmers what time it is. They watch the water and notice who has stopped moving.

### 2. Recover under a lock, and let the dead stay dead

**The situation.** The relay reads "J7 silent for six minutes". Before it acts, worker A sends a report: it was only paused by a long garbage collection.

| Moment | Relay | Worker A | `jobs` row of J7 |
|---|---|---|---|
| 1 | reads the list of silent Jobs: J7 is on it | | `running` |
| 2 | | reports; `heard_at` becomes now; commits | `running` |
| 3 | locks J7's row and measures the silence again: one second | | `running` |
| 4 | leaves J7 alone | | `running` |

**The hidden fact.** The first read is only a list of candidates. Each candidate is judged again after its row is locked, in its own transaction, and the silence is read after the lock, so a report that committed meanwhile is seen. The lock is `FOR UPDATE SKIP LOCKED`: if a worker holds the row right now, mid-report or mid-claim, the relay skips it, because whoever holds it is alive.

**Once recovered, the worker cannot come back.** Recovery makes the Job `failed` with `worker_lost`. If worker A was not dead after all and reports again, M08-T07's rule refuses it with `AttemptNotCurrent`: the Job is no longer running that attempt.

**What it keeps.** The attempt's row is ended, never deleted. It keeps "120 processed" and its last heartbeat, and gets outcome `retry_scheduled` or `failed`. The next attempt gets its own row.

| Attempt | processed | outcome |
|---|---|---|
| 1 | 120 | `retry_scheduled` |
| 2 | 0 | (running) |

**What changes.** `recover_stuck_jobs` in `job_recovery` does this for up to 100 stale and 100 orphaned Jobs per pass, each in its own transaction. One Job that cannot be recovered is logged by its error type and counted, and does not stop the others. The relay runs it first, before requeuing retries, so a recovered Job's retry is picked up by a later pass.

### 3. Orphaned messages: count the lost delivery as an attempt

**The situation.** A purge Job P1 is published at 09:00. Redis restarts and loses the message, or the actor's 60-second time limit kills the worker mid-claim, which rolls the claim back and leaves P1 `queued`. In both cases P1 stays `queued` and published, and no message will ever come.

**The decision.** After 30 minutes, recovery claims P1 itself and fails it with `delivery_lost`, so the lost delivery counts as one attempt.

| Choice | A message that keeps getting lost | A message that is only slow |
|---|---|---|
| republish, count nothing | loops forever, unseen | arrives late; the new one is a duplicate, which is harmless |
| count an attempt (chosen) | after three attempts: `exhausted`, shown to an operator | costs one attempt; the late original finds the Job `failed` and does nothing |

The chosen way trades a slow queue's attempt for never looping forever. With a backlog longer than 30 minutes, healthy Jobs lose attempts. That is a known limit.

**The hidden fact.** A late message is harmless, because every handler claims the Job under a row lock and runs it only if it is still `queued` (M08-T03).

### 4. What only an operator can decide

**The situation.** A Job ends `failed` with no retry planned in one of two ways:

| Condition | Example | Why it stopped |
|---|---|---|
| `exhausted` | `database_unavailable` three times | the problem may pass, but the attempts ran out |
| `failed_permanently` | `source_refused` | retrying will not help until someone fixes the cause |

Nothing moves such a Job on by itself. `GET /v1/job-decisions` lists every one that no newer Job for the same work replaced, newest first. Each comes with its allowed decisions.

| Decision | Allowed for | What it does | Refused when |
|---|---|---|---|
| retry | a failed Job with no retry planned | queues it again; an exhausted Job gets exactly one more attempt; the relay publishes it | another Job for the same work is open (`job_already_open`); a retry is already planned |
| cancel | a queued or failed Job | new status `cancelled`: closed, never run; a failed Job keeps its error code | the Job is running (`job_running`), because its worker may still be writing |

**The hidden fact about retry.** One piece of work, such as "purge Document D", may have at most one open Job: the unique index from M08-T03. Retrying an old failed purge while a newer purge of D is queued would break that. Two operators retrying two old purges of D at the same moment would too. The retry writes inside a savepoint. If the index refuses it, only the savepoint rolls back, and the caller gets `JobIntentAlreadyOpen`, which the API answers with 409.

**The hidden fact about cancel.** A message for a cancelled Job may still be in the queue. When it arrives, the claim sees `cancelled`, not `queued`, and does nothing.

Cancelling a purge does not keep deleted content. The next synchronization pass sees a tombstoned Document that still has Chunks and queues a new purge.

**Every decision is audited.** The decision, the Job's new state, and an audit event with the administrator as actor commit together: `job.retried` or `job.cancelled`, with the kind, previous status, failed attempts, and error code, and no content. Decisions need the new `recover_jobs` operation, which only the `administrator` role has.

| Was | Became |
|---|---|
| a failed Job with no retry stayed failed forever; nobody saw it | `GET /v1/job-decisions` lists it with its allowed decisions |
| no way to stop a queued or failing Job | cancel, audited, refused while it runs |

### 5. Clocks again: never start before you were queued

Two more clock problems came from the review:

- **A claim before the queue time.** An operator's retry stamps `queued_at` with the API's clock. A worker whose clock is ten seconds behind claimed the Job "before it was queued", and the domain refused that. The message was spent, so recovery later burned the attempt the operator had just granted. Now a claim starts at `max(now, queued_at)`, and recovery ends an attempt no earlier than anything the Job already recorded.
- **Candidates that can never be recovered.** A Job queued by a clock ahead of the relay's was picked first on every pass and then judged "not orphaned yet". With 100 of them, a real orphan was never reached. Now the query leaves out Jobs queued after `now`, the same rule `condition_of` uses.

### 6. Review findings

An adversarial review attacked this code three times on real PostgreSQL. Each finding below was fixed, with a test that fails without the fix:

- a worker whose clock ran behind the relay's was declared dead while it reported (block 1);
- a worker whose clock stepped back had every report refused and was declared dead (block 1);
- an operator's retry could be burned by a worker clock behind the API's (block 5);
- Jobs queued by a clock ahead filled every pass and starved real orphans (block 5);
- a database error while recording a refused report replaced the typed refusal with an untyped error.

The same review raced recovery against reports, claims, requeues, cancels, and a second relay, and found nothing: 80 stuck Jobs recovered by three relays at once were each recovered exactly once.

### 7. Known limits

- A message that waits in the queue longer than 30 minutes costs an attempt, so a long backlog can exhaust healthy Jobs (block 3).
- A worker silent for five minutes between reports is taken for dead, and its work may run twice. The M08-T10 handler must report at least every few minutes.
- A Job with no attempt row is judged by its worker's start time. Existing attempts count as heard from at the moment of the migration.
- A worker that keeps sending refused reports stays alive, and its Job stays running. A running Job cannot be cancelled, so its process must be stopped.
- A pass can be filled by candidates it cannot recover, such as rows held locked for a long time.
- A Job claimed by a clock far ahead is recovered with finish and retry times that far ahead.
- A retry does not check whether a synchronization's Source is still enabled; the M08-T10 handler must treat a disabled Source as final.
- The decision list shows at most 100 Jobs, with no paging.
- `save_job` still has no status guard (from M08-T07).

## Step-by-step instructions

Start PostgreSQL before you begin. Run every command from the repository root. For integration checks, set the test database URL in your terminal:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

Run only one integration command against this database at a time.

### Step 1 — No coding in this step: write the invariant and your predictions

Before changing anything, write down what must be true at the end and what you expect to see. You check the predictions in Step 6 and Step 8.

Create `docs/evidence/M08-T08-recover-dead-and-stuck-work.md`:

```markdown
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

## Predictions

- What a relay pass does to a Job that reported 40 seconds ago but started an hour ago:
- What happens to the attempt row of a Job recovered as stale:
- What an administrator gets when retrying an old purge while a newer purge of the same Document is queued:
- How many tests fail if `condition_of` judges a running Job by its start time only, and whether the slow Job of the drill is still left alone:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: the domain of recovery

This step teaches the Job a new ending and the rules that judge it.

**In plain words:** a Job can now be cancelled, and a Job that failed for good can be queued once more by a person. Two new failure codes name what recovery found: a worker that went silent, and a message that never arrived. A small new module looks at a Job and says what state it is really in: waiting, running, stale, orphaned, retrying, exhausted, failed for good, done, or cancelled. It also says which decisions fit. Nothing here touches the database or a clock.

| Piece | What it does |
|---|---|
| `JobStatus.CANCELLED` | a closed Job no one will run; it may or may not have started, and keeps its error code |
| `Job.cancel` | queued or failed only; a running Job raises `JobStillRunning` |
| `Job.reopen` | a failed Job with no retry planned, queued again; an exhausted one gets one more attempt |
| `WORKER_LOST`, `DELIVERY_LOST` | transient failure codes, so a retry is planned while attempts are left |
| `fail_with` | fails a Job with a known code, without an exception to classify |
| `job_conditions` | `JobCondition`, `RecoveryPolicy` (5 and 30 minutes), `condition_of`, `allowed_decisions` |

`is_open` changes too: before, "not succeeded" meant open, which would have made a cancelled Job open. Now only `queued` and `running` are open, plus a failure with a retry planned.


**Action 1:** in `src/knowledge_service/jobs.py`, add the new error after `JobNotRetryable`:

In `src/knowledge_service/jobs.py`, find:

```python
    """


class JobAlreadyClaimed(InvalidJob):
    """A Job is no longer queued, so this delivery must not run it again.
```

and replace it with:

```python
    """


class JobStillRunning(IllegalJobTransition):
    """A running Job cannot be cancelled: its worker may still be doing it.

    Wait for it to finish, or for recovery to find it dead.
    """


class JobAlreadyClaimed(InvalidJob):
    """A Job is no longer queued, so this delivery must not run it again.
```

add the status and its transitions:

In `src/knowledge_service/jobs.py`, find:

```python
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_FINISHED = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED})

_ALLOWED: Mapping[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED}),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
    JobStatus.SUCCEEDED: frozenset(),
}
```

and replace it with:

```python
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # An operator closed it; nothing will run it again.
    CANCELLED = "cancelled"


_FINISHED = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})
# Statuses that always come from a claim, so they carry a started time.
_STARTED = frozenset({JobStatus.RUNNING, JobStatus.SUCCEEDED, JobStatus.FAILED})

_ALLOWED: Mapping[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED}),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}
```

make the invariants allow a cancelled Job:

In `src/knowledge_service/jobs.py`, find:

```python
            _require_utc(self.finished_at, "finished_at")
            if self.started_at is not None and self.finished_at < self.started_at:
                raise InvalidJob("finished_at must not be earlier than started_at")
        if (self.status in _FINISHED) != (self.finished_at is not None):
            raise InvalidJob("only a finished job carries a finished time")
        if (self.status is JobStatus.QUEUED) == (self.started_at is not None):
            raise InvalidJob("only a job that is not queued carries a started time")
        if (self.status is JobStatus.FAILED) != (self.last_error_code is not None):
            raise InvalidJob("only a failed job carries an error code")
        if self.last_error_code is not None and not self.last_error_code.strip():
            raise InvalidJob("an error code must not be empty")
        if self.retry_at is not None:
```

and replace it with:

```python
            _require_utc(self.finished_at, "finished_at")
            if self.started_at is not None and self.finished_at < self.started_at:
                raise InvalidJob("finished_at must not be earlier than started_at")
            if self.finished_at < self.queued_at:
                raise InvalidJob("finished_at must not be earlier than queued_at")
        if (self.status in _FINISHED) != (self.finished_at is not None):
            raise InvalidJob("only a finished job carries a finished time")
        if self.status is JobStatus.QUEUED and self.started_at is not None:
            raise InvalidJob("a queued job carries no started time")
        if self.status in _STARTED and self.started_at is None:
            raise InvalidJob("a claimed job carries a started time")
        if (self.status is JobStatus.FAILED) and self.last_error_code is None:
            raise InvalidJob("a failed job carries an error code")
        if self.last_error_code is not None and self.status not in {
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            raise InvalidJob("only a failed or cancelled job carries an error code")
        if self.last_error_code is not None and not self.last_error_code.strip():
            raise InvalidJob("an error code must not be empty")
        if self.retry_at is not None:
```

fix `is_open`:

In `src/knowledge_service/jobs.py`, find:

```python
        """
        if self.status is JobStatus.FAILED:
            return self.retry_at is not None
        return self.status is not JobStatus.SUCCEEDED

    @classmethod
    def request(
```

and replace it with:

```python
        """
        if self.status is JobStatus.FAILED:
            return self.retry_at is not None
        return self.status in {JobStatus.QUEUED, JobStatus.RUNNING}

    @classmethod
    def request(
```

and add `reopen` and `cancel` after `retry`:

In `src/knowledge_service/jobs.py`, find:

```python
            raise JobNotRetryable("the job failed permanently")
        if queued_at < self.retry_at:
            raise JobNotRetryable("the job may not be retried before its retry time")
        return replace(
            self,
            status=JobStatus.QUEUED,
```

and replace it with:

```python
            raise JobNotRetryable("the job failed permanently")
        if queued_at < self.retry_at:
            raise JobNotRetryable("the job may not be retried before its retry time")
        return self._queued_again(queued_at=queued_at)

    def reopen(self, *, queued_at: datetime) -> Job:
        """Queue a Job that failed for good once more, by an operator's decision.

        Only a Job with no retry planned qualifies. One that used every
        attempt is given exactly one more; its failed attempts stay counted.
        """
        _check_transition(self.status, JobStatus.QUEUED)
        if self.retry_at is not None:
            raise JobNotRetryable("the job already has a retry planned")
        reopened = replace(self, max_attempts=max(self.max_attempts, self.attempt + 1))
        return reopened._queued_again(queued_at=queued_at)

    def cancel(self, *, finished_at: datetime) -> Job:
        """Close a queued or failed Job, by an operator's decision.

        A failed Job keeps its error code, so the reason it failed stays
        visible; a planned retry is dropped. A running Job is refused with
        `JobStillRunning`.
        """
        if self.status is JobStatus.RUNNING:
            raise JobStillRunning("a running job cannot be cancelled")
        _check_transition(self.status, JobStatus.CANCELLED)
        return replace(
            self, status=JobStatus.CANCELLED, finished_at=finished_at, retry_at=None
        )

    def _queued_again(self, *, queued_at: datetime) -> Job:
        return replace(
            self,
            status=JobStatus.QUEUED,
```

**Action 2:** in `src/knowledge_service/job_failures.py`, add the two codes:

In `src/knowledge_service/job_failures.py`, find:

```python
    PROGRAMMING_ERROR = "programming_error"
    # A newer run of the same work has started; there is nothing to redo.
    SUPERSEDED = "superseded"

    @property
    def transient(self) -> bool:
```

and replace it with:

```python
    PROGRAMMING_ERROR = "programming_error"
    # A newer run of the same work has started; there is nothing to redo.
    SUPERSEDED = "superseded"
    # Recovery found the attempt's worker silent for too long.
    WORKER_LOST = "worker_lost"
    # Recovery found a message that no worker took up in time.
    DELIVERY_LOST = "delivery_lost"

    @property
    def transient(self) -> bool:
```

make them transient:

In `src/knowledge_service/job_failures.py`, find:

```python
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
    }
)
```

and replace it with:

```python
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
        JobFailure.WORKER_LOST,
        JobFailure.DELIVERY_LOST,
    }
)
```

let `fail_attempt` hand over to a new `fail_with`:

In `src/knowledge_service/job_failures.py`, find:

```python
    A transient failure with attempts left gets a retry time; any other
    failure is final. Only the error's type is logged, never its message.
    """
    failure = classify_job_failure(error)
    failed_attempts = job.attempt + 1
    retry_at = None
    if failure.transient and failed_attempts < job.max_attempts:
```

and replace it with:

```python
    A transient failure with attempts left gets a retry time; any other
    failure is final. Only the error's type is logged, never its message.
    """
    return fail_with(
        job,
        classify_job_failure(error),
        now=now,
        policy=policy,
        cause=type(error).__name__,
    )


def fail_with(
    job: Job,
    failure: JobFailure,
    *,
    now: datetime,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    cause: str = "recovery",
) -> Job:
    """`job` failed with a known `failure`, retried later only if worth it.

    `cause` names what reported it, for the log; it is never an error message.
    """
    failed_attempts = job.attempt + 1
    retry_at = None
    if failure.transient and failed_attempts < job.max_attempts:
```

and log what reported the failure, not an exception's type:

In `src/knowledge_service/job_failures.py`, find:

```python
        job.job_id.value,
        failed_attempts,
        failure.value,
        type(error).__name__,
        "retry planned" if retry_at is not None else "final",
    )
    return job.fail(error_code=failure.value, finished_at=now, retry_at=retry_at)
```

and replace it with:

```python
        job.job_id.value,
        failed_attempts,
        failure.value,
        cause,
        "retry planned" if retry_at is not None else "final",
    )
    return job.fail(error_code=failure.value, finished_at=now, retry_at=retry_at)
```

**Action 3:** in `src/knowledge_service/job_progress.py`, a cancelled Job has no attempt under way either:

In `src/knowledge_service/job_progress.py`, find:

```python
    `Job.attempt` counts failed attempts, so a failed Job's last attempt is
    that count, and a running or succeeded Job's is the next one.
    """
    if job.status is JobStatus.QUEUED:
        raise InvalidJob("a queued job has no attempt under way")
    if job.status is JobStatus.FAILED:
        return job.attempt
    return job.attempt + 1
```

and replace it with:

```python
    `Job.attempt` counts failed attempts, so a failed Job's last attempt is
    that count, and a running or succeeded Job's is the next one.
    """
    if job.status in {JobStatus.QUEUED, JobStatus.CANCELLED}:
        raise InvalidJob(f"a {job.status} job has no attempt under way")
    if job.status is JobStatus.FAILED:
        return job.attempt
    return job.attempt + 1
```

**Action 4:** in `tests/test_job_failures.py`, the two new codes are transient:

In `tests/test_job_failures.py`, find:

```python
    assert classify_job_failure(error) is failure


def test_only_dependencies_that_may_answer_later_are_transient() -> None:
    assert {failure for failure in JobFailure if failure.transient} == {
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
    }
```

and replace it with:

```python
    assert classify_job_failure(error) is failure


def test_only_failures_that_may_pass_later_are_transient() -> None:
    # A dependency that may answer later, or a worker or message lost on the way.
    assert {failure for failure in JobFailure if failure.transient} == {
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
        JobFailure.WORKER_LOST,
        JobFailure.DELIVERY_LOST,
    }
```

**Action 5:** create the conditions module.

Create `src/knowledge_service/job_conditions.py`:

```python
"""What a Job's state and signs of life say about it, and what may be decided.

A running Job is alive while it is heard from within `stale_after`; one
silent longer is stale, its worker presumed dead. A queued Job whose message
was sent `orphan_after` ago and that no worker took up is orphaned. A Job
that failed with no retry planned is exhausted (its failure may pass, but
its attempts ran out) or failed permanently, and only an operator moves it
on, by a retry or a cancel. Nothing here reads the database or a clock.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from knowledge_service.job_failures import JobFailure
from knowledge_service.jobs import Job, JobStatus


class JobCondition(StrEnum):
    """What a Job's stored state and signs of life say about it."""

    WAITING = "waiting"
    ORPHANED = "orphaned"
    RUNNING = "running"
    STALE = "stale"
    RETRY_PLANNED = "retry_planned"
    EXHAUSTED = "exhausted"
    FAILED_PERMANENTLY = "failed_permanently"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"

    @property
    def needs_decision(self) -> bool:
        """Whether only an operator can move the Job on: retry or cancel."""
        return self in {JobCondition.EXHAUSTED, JobCondition.FAILED_PERMANENTLY}


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    """How long a Job may stay silent, or undelivered, before it is recovered.

    `stale_after` must be longer than the longest pause between two reports
    of a healthy worker, or a slow Job is taken for a dead one.
    """

    stale_after: timedelta = timedelta(minutes=5)
    orphan_after: timedelta = timedelta(minutes=30)

    def __post_init__(self) -> None:
        for name, value in (
            ("stale_after", self.stale_after),
            ("orphan_after", self.orphan_after),
        ):
            if type(value) is not timedelta:
                raise ValueError(f"{name} must be a timedelta")
            if not timedelta(minutes=1) <= value <= timedelta(days=7):
                raise ValueError(f"{name} must be from one minute to seven days")


DEFAULT_RECOVERY_POLICY: Final = RecoveryPolicy()


def condition_of(
    job: Job,
    *,
    now: datetime,
    policy: RecoveryPolicy = DEFAULT_RECOVERY_POLICY,
    published_at: datetime | None = None,
    last_heard_at: datetime | None = None,
) -> JobCondition:
    """Judge a Job at `now` by its status and its signs of life.

    `published_at` is when a queued Job's message was sent, if it was;
    `last_heard_at` is a running Job's latest heartbeat, and its start time
    when it never reported.
    """
    if job.status is JobStatus.QUEUED:
        if (
            published_at is not None
            and job.queued_at <= now
            and published_at <= now - policy.orphan_after
        ):
            return JobCondition.ORPHANED
        return JobCondition.WAITING
    if job.status is JobStatus.RUNNING:
        heard = last_heard_at or job.started_at
        if heard is not None and heard <= now - policy.stale_after:
            return JobCondition.STALE
        return JobCondition.RUNNING
    if job.status is JobStatus.FAILED:
        if job.retry_at is not None:
            return JobCondition.RETRY_PLANNED
        if _transient(job.last_error_code):
            # A failure that may pass, so it stopped only because attempts ran out.
            return JobCondition.EXHAUSTED
        return JobCondition.FAILED_PERMANENTLY
    if job.status is JobStatus.SUCCEEDED:
        return JobCondition.SUCCEEDED
    return JobCondition.CANCELLED


def _transient(error_code: str | None) -> bool:
    try:
        return JobFailure(error_code).transient
    except ValueError:
        return False


class JobDecision(StrEnum):
    """What an operator may decide about a Job."""

    RETRY = "retry"
    CANCEL = "cancel"


def allowed_decisions(job: Job) -> tuple[JobDecision, ...]:
    """The decisions that fit `job` in its current status, retry first.

    A retry fits a Job that failed with no retry planned; a cancel fits a
    queued or failed Job. A running or finished Job takes neither.
    """
    allowed: list[JobDecision] = []
    if job.status is JobStatus.FAILED and job.retry_at is None:
        allowed.append(JobDecision.RETRY)
    if job.status in {JobStatus.QUEUED, JobStatus.FAILED}:
        allowed.append(JobDecision.CANCEL)
    return tuple(allowed)
```

**Action 6:** unit tests for every condition and decision.

Create `tests/test_job_conditions.py`:

```python
"""Telling a slow Job from a dead one, and the decisions an operator may take."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from knowledge_service.identifiers import JobId
from knowledge_service.job_conditions import (
    JobCondition,
    JobDecision,
    RecoveryPolicy,
    allowed_decisions,
    condition_of,
)
from knowledge_service.jobs import (
    IllegalJobTransition,
    InvalidJob,
    Job,
    JobKind,
    JobNotRetryable,
    JobStatus,
    JobStillRunning,
)

QUEUED_AT = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
STARTED_AT = QUEUED_AT + timedelta(minutes=1)
FAILED_AT = STARTED_AT + timedelta(minutes=1)
POLICY = RecoveryPolicy()


def queued() -> Job:
    return Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_DOCUMENT_CHUNKS,
        target_id=uuid4(),
        queued_at=QUEUED_AT,
    )


def running() -> Job:
    return queued().start(started_at=STARTED_AT)


def failed(code: str = "database_unavailable", *, retry: bool = False) -> Job:
    retry_at = FAILED_AT + timedelta(seconds=30) if retry else None
    return running().fail(error_code=code, finished_at=FAILED_AT, retry_at=retry_at)


def exhausted() -> Job:
    job = failed()
    for _ in range(2):
        job = job.reopen(queued_at=FAILED_AT)
        job = job.start(started_at=FAILED_AT).fail(
            error_code="database_unavailable", finished_at=FAILED_AT
        )
    return job


# Slow or dead: a running Job is judged by when it was last heard from.


def test_a_running_job_heard_from_recently_is_running_however_long_it_ran() -> None:
    hours_later = STARTED_AT + timedelta(hours=5)

    condition = condition_of(
        running(),
        now=hours_later,
        last_heard_at=hours_later - timedelta(minutes=4, seconds=59),
    )

    assert condition is JobCondition.RUNNING


def test_a_running_job_silent_for_the_stale_time_is_stale() -> None:
    heard = STARTED_AT + timedelta(minutes=30)

    at_limit = condition_of(
        running(), now=heard + POLICY.stale_after, last_heard_at=heard
    )
    just_before = condition_of(
        running(),
        now=heard + POLICY.stale_after - timedelta(microseconds=1),
        last_heard_at=heard,
    )

    assert (at_limit, just_before) == (JobCondition.STALE, JobCondition.RUNNING)


def test_a_running_job_that_never_reported_is_judged_by_its_start() -> None:
    assert (
        condition_of(running(), now=STARTED_AT + timedelta(minutes=5))
        is JobCondition.STALE
    )


def test_a_clock_behind_the_job_never_calls_it_stale() -> None:
    assert condition_of(running(), now=QUEUED_AT) is JobCondition.RUNNING


# Orphaned: a queued Job whose message was sent long ago.


@pytest.mark.parametrize(
    ("published_ago", "condition"),
    [
        (None, JobCondition.WAITING),
        (timedelta(minutes=29), JobCondition.WAITING),
        (timedelta(minutes=30), JobCondition.ORPHANED),
    ],
)
def test_a_queued_job_is_orphaned_once_its_message_is_old(
    published_ago: timedelta | None, condition: JobCondition
) -> None:
    now = QUEUED_AT + timedelta(hours=1)
    published_at = None if published_ago is None else now - published_ago

    assert condition_of(queued(), now=now, published_at=published_at) is condition


def test_a_job_queued_after_the_clock_is_never_orphaned() -> None:
    earlier = QUEUED_AT - timedelta(hours=1)
    published_at = earlier - timedelta(hours=1)

    condition = condition_of(queued(), now=earlier, published_at=published_at)

    assert condition is JobCondition.WAITING


# Failed: planned retry, exhausted, or permanent.


@pytest.mark.parametrize(
    ("job", "condition"),
    [
        (failed(retry=True), JobCondition.RETRY_PLANNED),
        (failed("database_unavailable"), JobCondition.EXHAUSTED),
        (failed("worker_lost"), JobCondition.EXHAUSTED),
        (failed("source_refused"), JobCondition.FAILED_PERMANENTLY),
        (failed("superseded"), JobCondition.FAILED_PERMANENTLY),
        (failed("an_old_code"), JobCondition.FAILED_PERMANENTLY),
    ],
)
def test_a_failed_job_is_judged_by_its_retry_and_its_code(
    job: Job, condition: JobCondition
) -> None:
    assert condition_of(job, now=FAILED_AT) is condition


def test_only_failures_with_no_retry_need_a_decision() -> None:
    assert {condition for condition in JobCondition if condition.needs_decision} == {
        JobCondition.EXHAUSTED,
        JobCondition.FAILED_PERMANENTLY,
    }


@pytest.mark.parametrize("value", [timedelta(seconds=59), timedelta(days=8), 300, None])
def test_a_recovery_policy_refuses_an_unbounded_time(value: object) -> None:
    with pytest.raises(ValueError, match="stale_after"):
        RecoveryPolicy(stale_after=value)  # type: ignore[arg-type]


# An operator's decisions in the domain.


def test_a_retry_gives_a_job_that_used_every_attempt_one_more() -> None:
    job = exhausted()

    reopened = job.reopen(queued_at=FAILED_AT + timedelta(hours=1))

    assert (reopened.status, reopened.attempt, reopened.max_attempts) == (
        JobStatus.QUEUED,
        3,
        4,
    )
    assert reopened.last_error_code is None
    assert reopened.started_at is None


def test_a_retry_of_a_permanent_failure_keeps_its_attempt_limit() -> None:
    reopened = failed("source_refused").reopen(queued_at=FAILED_AT)

    assert (reopened.attempt, reopened.max_attempts) == (1, 3)


def test_a_retry_is_refused_while_one_is_planned() -> None:
    with pytest.raises(JobNotRetryable):
        failed(retry=True).reopen(queued_at=FAILED_AT)


@pytest.mark.parametrize("job", [queued(), running()])
def test_only_a_failed_job_can_be_retried_by_an_operator(job: Job) -> None:
    with pytest.raises(IllegalJobTransition):
        job.reopen(queued_at=FAILED_AT)


def test_cancelling_a_queued_job_closes_it_without_a_start() -> None:
    cancelled = queued().cancel(finished_at=QUEUED_AT)

    assert (cancelled.status, cancelled.started_at, cancelled.is_open) == (
        JobStatus.CANCELLED,
        None,
        False,
    )


def test_cancelling_a_failed_job_drops_its_retry_and_keeps_its_code() -> None:
    cancelled = failed(retry=True).cancel(finished_at=FAILED_AT)

    assert (cancelled.retry_at, cancelled.last_error_code) == (
        None,
        "database_unavailable",
    )


def test_a_running_job_cannot_be_cancelled() -> None:
    with pytest.raises(JobStillRunning):
        running().cancel(finished_at=FAILED_AT)


@pytest.mark.parametrize(
    "job",
    [
        running().succeed(finished_at=FAILED_AT),
        queued().cancel(finished_at=QUEUED_AT),
    ],
)
def test_a_finished_job_cannot_be_cancelled_or_retried(job: Job) -> None:
    with pytest.raises(IllegalJobTransition):
        job.cancel(finished_at=FAILED_AT)
    with pytest.raises(IllegalJobTransition):
        job.reopen(queued_at=FAILED_AT)


def test_a_cancel_never_ends_before_the_job_was_queued() -> None:
    with pytest.raises(InvalidJob, match="queued_at"):
        queued().cancel(finished_at=QUEUED_AT - timedelta(seconds=1))


@pytest.mark.parametrize(
    ("job", "decisions"),
    [
        (queued(), (JobDecision.CANCEL,)),
        (running(), ()),
        (failed(retry=True), (JobDecision.CANCEL,)),
        (failed(), (JobDecision.RETRY, JobDecision.CANCEL)),
        (running().succeed(finished_at=FAILED_AT), ()),
        (queued().cancel(finished_at=QUEUED_AT), ()),
    ],
)
def test_only_the_decisions_that_fit_are_offered(
    job: Job, decisions: tuple[JobDecision, ...]
) -> None:
    assert allowed_decisions(job) == decisions
```

**Check:** `uv run pytest tests/test_jobs.py tests/test_job_failures.py tests/test_job_progress.py tests/test_job_conditions.py -q` reports `119 passed`.

### Step 3 — Coding step: find and recover stuck Jobs

This step stores the database's own "last heard" time and runs recovery in every relay pass.

**In plain words:** every attempt row gets a time the database writes itself whenever it hears from the worker, even when it refuses what the worker said. A recovery pass asks the database which running Jobs have been silent for five minutes and which queued Jobs were published half an hour ago, then takes each one under a lock, checks again, and fails its lost attempt. The relay runs this pass before anything else. The purge claim also learns not to trip over a queue time stamped by a clock ahead of its own.

| Piece | What it does |
|---|---|
| migration `d0e1f2a3b4c5` | `heard_at` on `job_attempts`; `cancelled` in the `jobs` checks; two indexes; two audit actions |
| `_save` and `record_heard` | stamp `heard_at` with `clock_timestamp()`, the database's time at the write |
| `report_progress` | on a refused report, records the worker as heard from in a second transaction, then raises the refusal |
| `persistence/job_recovery.py` | the candidate queries, the locked re-read, and the decision list |
| `job_recovery.py` | `recover_stuck_jobs`: judge again under the lock, fail the attempt, end its row |
| `relay_once` | recovery first; a failure there is logged and reported, and the other steps still run |


**Action 1:** create the migration.

Create `migrations/versions/d0e1f2a3b4c5_job_recovery_decisions.py`:

```python
"""job recovery decisions

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-11-24 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b4"

OLD_ACTIONS = (
    "'source.registered', 'document.availability_changed',"
    " 'conversation.deleted', 'job.queued', 'job.failed',"
    " 'evaluation_run.started', 'question.asked', 'document.read',"
    " 'document.access_changed', 'synchronization.finished',"
    " 'message.rated', 'embedding_migration.started',"
    " 'embedding_migration.switched'"
)
NEW_ACTIONS = OLD_ACTIONS + ", 'job.retried', 'job.cancelled'"


def _job_checks(*, cancelled: bool) -> None:
    for name in (
        "ck_jobs_status",
        "ck_jobs_finished_at",
        "ck_jobs_started_at",
        "ck_jobs_last_error_code",
    ):
        op.drop_constraint(name, "jobs", type_="check")
    if cancelled:
        statuses = "'queued', 'running', 'succeeded', 'failed', 'cancelled'"
        finished = "'succeeded', 'failed', 'cancelled'"
        exempt = "status = 'cancelled' or "
    else:
        statuses = "'queued', 'running', 'succeeded', 'failed'"
        finished = "'succeeded', 'failed'"
        exempt = ""
    op.create_check_constraint("ck_jobs_status", "jobs", f"status in ({statuses})")
    op.create_check_constraint(
        "ck_jobs_finished_at",
        "jobs",
        f"(status in ({finished})) = (finished_at is not null)",
    )
    op.create_check_constraint(
        "ck_jobs_started_at",
        "jobs",
        f"{exempt}(status = 'queued') = (started_at is null)",
    )
    op.create_check_constraint(
        "ck_jobs_last_error_code",
        "jobs",
        f"{exempt}(status = 'failed') = (last_error_code is not null)",
    )


def _audit_actions(actions: str) -> None:
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_action", "audit_events", f"action in ({actions})"
    )


def upgrade() -> None:
    # An attempt stored before this revision counts as heard from now.
    op.add_column(
        "job_attempts",
        sa.Column(
            "heard_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    _job_checks(cancelled=True)
    _audit_actions(NEW_ACTIONS)
    op.create_index(
        "ix_jobs_in_flight",
        "jobs",
        ["status"],
        postgresql_where=sa.text("status in ('queued', 'running')"),
    )
    op.create_index(
        "ix_jobs_failed_for_good",
        "jobs",
        ["finished_at", "job_id"],
        postgresql_where=sa.text("status = 'failed' and retry_at is null"),
    )


def downgrade() -> None:
    # The audit log is append-only: an operator's decision is never deleted
    # to make a downgrade fit, so the downgrade stops instead.
    op.execute(
        "do $$ begin"
        " if exists (select 1 from audit_events"
        " where action in ('job.retried', 'job.cancelled')) then"
        " raise exception 'operator decisions are recorded; cannot downgrade';"
        " end if; end $$"
    )
    op.drop_index("ix_jobs_failed_for_good", table_name="jobs")
    op.drop_index("ix_jobs_in_flight", table_name="jobs")
    # Before this revision a Job could not be cancelled; the closest closed
    # state is a final failure, which keeps the Job out of every retry.
    op.execute(
        "update jobs set status = 'failed',"
        " started_at = coalesce(started_at, queued_at),"
        " last_error_code = coalesce(last_error_code, 'cancelled')"
        " where status = 'cancelled'"
    )
    _job_checks(cancelled=False)
    _audit_actions(OLD_ACTIONS)
    op.drop_column("job_attempts", "heard_at")
```

**Action 2:** in `src/knowledge_service/persistence/schema.py`, allow the new status:

In `src/knowledge_service/persistence/schema.py`, find:

```python
        name="ck_jobs_kind",
    ),
    sa.CheckConstraint(
        "status in ('queued', 'running', 'succeeded', 'failed')",
        name="ck_jobs_status",
    ),
    sa.CheckConstraint(
```

and replace it with:

```python
        name="ck_jobs_kind",
    ),
    sa.CheckConstraint(
        "status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
        name="ck_jobs_status",
    ),
    sa.CheckConstraint(
```

allow a cancelled Job's times and error code:

In `src/knowledge_service/persistence/schema.py`, find:

```python
        name="ck_jobs_attempt",
    ),
    sa.CheckConstraint(
        "(status in ('succeeded', 'failed')) = (finished_at is not null)",
        name="ck_jobs_finished_at",
    ),
    sa.CheckConstraint(
        "(status = 'queued') = (started_at is null)", name="ck_jobs_started_at"
    ),
    sa.CheckConstraint(
        "(status = 'failed') = (last_error_code is not null)",
        name="ck_jobs_last_error_code",
    ),
    sa.CheckConstraint(
```

and replace it with:

```python
        name="ck_jobs_attempt",
    ),
    sa.CheckConstraint(
        "(status in ('succeeded', 'failed', 'cancelled')) = (finished_at is not null)",
        name="ck_jobs_finished_at",
    ),
    # A cancelled Job may or may not have been claimed before.
    sa.CheckConstraint(
        "status = 'cancelled' or (status = 'queued') = (started_at is null)",
        name="ck_jobs_started_at",
    ),
    # A cancelled Job keeps the error code of a failure before it, if any.
    sa.CheckConstraint(
        "status = 'cancelled' or (status = 'failed') = (last_error_code is not null)",
        name="ck_jobs_last_error_code",
    ),
    sa.CheckConstraint(
```

add the two indexes:

In `src/knowledge_service/persistence/schema.py`, find:

```python
        "job_id",
        postgresql_where=sa.text("retry_at is not null"),
    ),
)

# One row per attempt of a Job: how far it got, and how it ended.
```

and replace it with:

```python
        "job_id",
        postgresql_where=sa.text("retry_at is not null"),
    ),
    # Finds queued and running Jobs that recovery may find stuck.
    sa.Index(
        "ix_jobs_in_flight",
        "status",
        postgresql_where=sa.text("status in ('queued', 'running')"),
    ),
    # Finds Jobs that failed for good, for an operator's decision.
    sa.Index(
        "ix_jobs_failed_for_good",
        "finished_at",
        "job_id",
        postgresql_where=sa.text("status = 'failed' and retry_at is null"),
    ),
)

# One row per attempt of a Job: how far it got, and how it ended.
```

add `heard_at` to `job_attempts`:

In `src/knowledge_service/persistence/schema.py`, find:

```python
    sa.Column("warnings", ARRAY(sa.String(length=32)), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("outcome", sa.String(length=32), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("attempt >= 1", name="ck_job_attempts_attempt"),
```

and replace it with:

```python
    sa.Column("warnings", ARRAY(sa.String(length=32)), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
    # When the database last heard from the attempt, by its own clock, so
    # recovery never judges a worker by that worker's clock.
    sa.Column(
        "heard_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("outcome", sa.String(length=32), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("attempt >= 1", name="ck_job_attempts_attempt"),
```

and the two audit actions:

In `src/knowledge_service/persistence/schema.py`, find:

```python
        " 'evaluation_run.started', 'question.asked', 'document.read',"
        " 'document.access_changed', 'synchronization.finished',"
        " 'message.rated', 'embedding_migration.started',"
        " 'embedding_migration.switched')",
        name="ck_audit_events_action",
    ),
    sa.CheckConstraint(
```

and replace it with:

```python
        " 'evaluation_run.started', 'question.asked', 'document.read',"
        " 'document.access_changed', 'synchronization.finished',"
        " 'message.rated', 'embedding_migration.started',"
        " 'embedding_migration.switched', 'job.retried', 'job.cancelled')",
        name="ck_audit_events_action",
    ),
    sa.CheckConstraint(
```

**Action 3:** in `tests/test_indexes.py`, name the two indexes:

In `tests/test_indexes.py`, find:

```python
    "uq_embedding_spaces_current",
    "uq_jobs_open_intent",
    "ix_jobs_due_retry",
    "ix_jobs_synchronization_target",
    "uq_synchronization_runs_running_source",
}
```

and replace it with:

```python
    "uq_embedding_spaces_current",
    "uq_jobs_open_intent",
    "ix_jobs_due_retry",
    "ix_jobs_in_flight",
    "ix_jobs_failed_for_good",
    "ix_jobs_synchronization_target",
    "uq_synchronization_runs_running_source",
}
```

**Action 4:** in `src/knowledge_service/persistence/jobs.py`, make the row reader public, because recovery reads `jobs` rows too. Rename `_job_from` to `job_from_record` and give it a docstring:

In `src/knowledge_service/persistence/jobs.py`, find:

```python
    }


def _job_from(record: Mapping[str, object]) -> Job:
    try:
        kind = JobKind(cast(str, record["kind"]))
        status = JobStatus(cast(str, record["status"]))
```

and replace it with:

```python
    }


def job_from_record(record: Mapping[str, object]) -> Job:
    """The Job a stored `jobs` row holds."""
    try:
        kind = JobKind(cast(str, record["kind"]))
        status = JobStatus(cast(str, record["status"]))
```

then use the new name in `load_open_job`:

In `src/knowledge_service/persistence/jobs.py`, find:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def enqueue_job(connection: AsyncConnection, job: Job) -> Enqueued:
```

and replace it with:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return job_from_record(dict(record))


async def enqueue_job(connection: AsyncConnection, job: Job) -> Enqueued:
```

in `load_job`:

In `src/knowledge_service/persistence/jobs.py`, find:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def lock_running_job(connection: AsyncConnection, *, job_id: JobId) -> Job | None:
```

and replace it with:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return job_from_record(dict(record))


async def lock_running_job(connection: AsyncConnection, *, job_id: JobId) -> Job | None:
```

in `lock_running_job`:

In `src/knowledge_service/persistence/jobs.py`, find:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def save_job(connection: AsyncConnection, *, job: Job) -> Job:
```

and replace it with:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return job_from_record(dict(record))


async def save_job(connection: AsyncConnection, *, job: Job) -> Job:
```

in `requeue_due_jobs`:

In `src/knowledge_service/persistence/jobs.py`, find:

```python
    records = (await connection.execute(statement)).mappings().all()
    return tuple(
        [
            await save_job(connection, job=_job_from(dict(record)).retry(queued_at=now))
            for record in records
        ]
    )
```

and replace it with:

```python
    records = (await connection.execute(statement)).mappings().all()
    return tuple(
        [
            await save_job(
                connection, job=job_from_record(dict(record)).retry(queued_at=now)
            )
            for record in records
        ]
    )
```

and in `lock_unpublished_job`:

In `src/knowledge_service/persistence/jobs.py`, find:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def mark_job_published(
```

and replace it with:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return job_from_record(dict(record))


async def mark_job_published(
```

**Action 5:** in `src/knowledge_service/persistence/job_attempts.py`, add `record_heard` before `finish_attempt`:

In `src/knowledge_service/persistence/job_attempts.py`, find:

```python
    return advanced


async def finish_attempt(
    connection: AsyncConnection,
    *,
```

and replace it with:

```python
    return advanced


async def record_heard(
    connection: AsyncConnection, *, job_id: JobId, attempt: int
) -> bool:
    """Record that the worker of a running attempt was just heard from.

    Only the attempt the Job is running is touched, after the Job's row is
    share-locked, as a report does; returns whether it was. Nothing else in
    the attempt changes.
    """
    job = await lock_running_job(connection, job_id=job_id)
    if job is None or attempt_number(job) != attempt:
        return False
    result = await connection.execute(
        sa.update(job_attempts)
        .where(
            job_attempts.c.job_id == job_id.value,
            job_attempts.c.attempt == attempt,
            job_attempts.c.outcome.is_(None),
        )
        .values(heard_at=sa.func.clock_timestamp())
    )
    return result.rowcount == 1


async def finish_attempt(
    connection: AsyncConnection,
    *,
```

and stamp `heard_at` in `_save`:

In `src/knowledge_service/persistence/job_attempts.py`, find:

```python
    ]
    if not ending:
        conditions.append(columns.heartbeat_at <= progress.heartbeat_at)
    result = await connection.execute(
        sa.update(job_attempts).where(*conditions).values(_row(progress))
    )
    if result.rowcount != 1:
        raise ProgressWentBackwards("the stored attempt is further along")
```

and replace it with:

```python
    ]
    if not ending:
        conditions.append(columns.heartbeat_at <= progress.heartbeat_at)
    # Stamped by the database, whatever the reporter's clock says, at the
    # moment of the write rather than when the transaction began.
    values = {**_row(progress), "heard_at": sa.func.clock_timestamp()}
    result = await connection.execute(
        sa.update(job_attempts).where(*conditions).values(values)
    )
    if result.rowcount != 1:
        raise ProgressWentBackwards("the stored attempt is further along")
```

**Action 6:** in `src/knowledge_service/job_reporting.py`, import `logging`:

In `src/knowledge_service/job_reporting.py`, find:

```python
its queue messages are never involved.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
```

and replace it with:

```python
its queue messages are never involved.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
```

import the refusals and `record_heard`, and add a logger:

In `src/knowledge_service/job_reporting.py`, find:

```python
    JobProgress,
    JobStage,
    JobWarning,
)
from knowledge_service.jobs import Job, JobKind
from knowledge_service.persistence.job_attempts import (
    advance_attempt,
    load_latest_attempt,
)
from knowledge_service.persistence.jobs import load_job


@dataclass(frozen=True, slots=True)
class JobReport:
```

and replace it with:

```python
    JobProgress,
    JobStage,
    JobWarning,
    ProgressWentBackwards,
    ReportFromTheFuture,
)
from knowledge_service.jobs import Job, JobKind
from knowledge_service.persistence.job_attempts import (
    advance_attempt,
    load_latest_attempt,
    record_heard,
)
from knowledge_service.persistence.jobs import load_job

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobReport:
```

and record a refused report as heard:

In `src/knowledge_service/job_reporting.py`, find:

```python
    the same report twice changes nothing. A counter, a warning, or the
    heartbeat that would go back raises `ProgressWentBackwards`; a report
    for an attempt the Job is not running raises `AttemptNotCurrent`.
    """
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")
    async with runtime.transaction() as connection:
        return await advance_attempt(
            connection,
            job_id=job_id,
            attempt=attempt,
            stage=stage,
            counters=counters,
            warnings=warnings,
            now=now,
        )


@dataclass(frozen=True, slots=True)
```

and replace it with:

```python
    the same report twice changes nothing. A counter, a warning, or the
    heartbeat that would go back raises `ProgressWentBackwards`; a report
    for an attempt the Job is not running raises `AttemptNotCurrent`.

    A report refused for what it says, such as one from a clock that
    stepped back, still shows the worker is alive: the attempt is recorded
    as heard from in a transaction of its own, then the refusal is raised.
    """
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")
    try:
        async with runtime.transaction() as connection:
            return await advance_attempt(
                connection,
                job_id=job_id,
                attempt=attempt,
                stage=stage,
                counters=counters,
                warnings=warnings,
                now=now,
            )
    except ProgressWentBackwards, ReportFromTheFuture:
        try:
            async with runtime.transaction() as connection:
                await record_heard(connection, job_id=job_id, attempt=attempt)
        except Exception as error:
            # The refusal is what the caller must see, not this second failure.
            logger.warning(
                "job %s: recording a refused report failed: %s",
                job_id.value,
                type(error).__name__,
            )
        raise


@dataclass(frozen=True, slots=True)
```

**Action 7:** create the recovery queries.

Create `src/knowledge_service/persistence/job_recovery.py`:

```python
"""Find Jobs that stopped moving, and Jobs waiting for an operator's decision."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.identifiers import JobId
from knowledge_service.jobs import Job, JobStatus
from knowledge_service.persistence.jobs import job_from_record
from knowledge_service.persistence.schema import job_attempts, jobs


@dataclass(frozen=True, slots=True)
class RecoveryCandidate:
    """A locked Job with the signs recovery judges it by.

    `published_at` is when a message for a queued Job was sent. `silent_for`
    is how long, by the database's clock, a running Job has gone unheard:
    since its current attempt's last report, or since its start when it has
    no attempt row. Each is None when it does not apply.
    """

    job: Job
    published_at: datetime | None
    silent_for: timedelta | None


def _current_attempt() -> sa.ColumnElement[bool]:
    # A running Job's current attempt is the one after its failed ones.
    return sa.and_(
        job_attempts.c.job_id == jobs.c.job_id,
        job_attempts.c.attempt == jobs.c.attempt + 1,
    )


def _silence() -> sa.ColumnElement[timedelta]:
    # The start time stands in only for a Job with no attempt row; it comes
    # from the worker's clock.
    heard = sa.func.coalesce(job_attempts.c.heard_at, jobs.c.started_at)
    return sa.func.now() - heard


async def load_silent_job_ids(
    connection: AsyncConnection, *, stale_after: timedelta, limit: int
) -> tuple[JobId, ...]:
    """Running Jobs unheard for at least `stale_after`, longest silent first.

    Silence is measured by the database's clock alone.
    """
    silence = _silence()
    statement = (
        sa.select(jobs.c.job_id)
        .select_from(jobs.outerjoin(job_attempts, _current_attempt()))
        .where(jobs.c.status == JobStatus.RUNNING.value, silence >= stale_after)
        .order_by(silence.desc(), jobs.c.job_id)
        .limit(limit)
    )
    return tuple(JobId(job_id) for job_id in await connection.scalars(statement))


async def load_undelivered_job_ids(
    connection: AsyncConnection, *, now: datetime, orphan_after: timedelta, limit: int
) -> tuple[JobId, ...]:
    """Queued Jobs whose message was sent at least `orphan_after` before `now`.

    A Job queued after `now`, by a clock ahead of this one, is left out, as
    recovery would not judge it orphaned; it must not take another's place.
    """
    statement = (
        sa.select(jobs.c.job_id)
        .where(
            jobs.c.status == JobStatus.QUEUED.value,
            jobs.c.published_at <= now - orphan_after,
            jobs.c.queued_at <= now,
        )
        .order_by(jobs.c.published_at, jobs.c.job_id)
        .limit(limit)
    )
    return tuple(JobId(job_id) for job_id in await connection.scalars(statement))


async def lock_recovery_candidate(
    connection: AsyncConnection, *, job_id: JobId
) -> RecoveryCandidate | None:
    """Lock a Job for recovery and read its signs, or None if it is busy.

    A row someone else holds, such as a worker's claim or a report in
    progress, is skipped: whoever holds it is alive. The silence is read
    after the lock, so a report that committed meanwhile is seen.
    """
    statement = (
        sa.select(jobs)
        .where(jobs.c.job_id == job_id.value)
        .with_for_update(skip_locked=True)
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    job = job_from_record(dict(record))
    published = cast("datetime | None", record["published_at"])
    silent_for = None
    if job.status is JobStatus.RUNNING:
        heard = (
            sa.select(job_attempts.c.heard_at)
            .where(
                job_attempts.c.job_id == job_id.value,
                job_attempts.c.attempt == job.attempt + 1,
            )
            .scalar_subquery()
        )
        silent_for = await connection.scalar(
            sa.select(sa.func.now() - sa.func.coalesce(heard, job.started_at))
        )
    return RecoveryCandidate(
        job=job,
        published_at=None if published is None else published.astimezone(UTC),
        silent_for=cast("timedelta | None", silent_for),
    )


def _newer_of_same_intent() -> sa.Exists:
    newer = jobs.alias("newer")
    return sa.exists().where(
        newer.c.kind == jobs.c.kind,
        newer.c.target_id == jobs.c.target_id,
        sa.tuple_(newer.c.queued_at, newer.c.job_id)
        > sa.tuple_(jobs.c.queued_at, jobs.c.job_id),
    )


async def load_failed_for_good(
    connection: AsyncConnection, *, limit: int
) -> tuple[Job, ...]:
    """Jobs that failed with no retry planned, that no newer Job replaced.

    Newest failure first. A Job for the same work queued after it, such as
    the next scheduled synchronization, replaces it, so it is not listed.
    """
    statement = (
        sa.select(jobs)
        .where(
            jobs.c.status == JobStatus.FAILED.value,
            jobs.c.retry_at.is_(None),
            ~_newer_of_same_intent(),
        )
        .order_by(jobs.c.finished_at.desc(), jobs.c.job_id.desc())
        .limit(limit)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(job_from_record(dict(record)) for record in records)
```

**Action 8:** create the recovery pass.

Create `src/knowledge_service/job_recovery.py`:

```python
"""Tell a slow Job from a dead one, and recover the Jobs that stopped moving.

A running Job is alive while its worker keeps reporting: a report at least
every `stale_after` keeps it running however long it takes. Silence is
measured by the database's clock, from when it stored the attempt's last
report, so a worker's own clock never makes it look dead or alive. A Job
silent longer is stale: its worker is presumed dead, and its attempt is
failed with `worker_lost`. A queued Job whose message was sent more than
`orphan_after` ago and that no worker took up is orphaned: the message is
presumed lost, and the attempt it stood for is failed with `delivery_lost`.

Both failures are transient, so the stored Job plans a retry while attempts
are left (M08-T04). A Job that failed with none planned waits for an
operator to retry or cancel it (`job_decisions`). An attempt's progress row
is ended, never deleted, so what the dead attempt did stays visible.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId
from knowledge_service.job_conditions import (
    DEFAULT_RECOVERY_POLICY,
    JobCondition,
    RecoveryPolicy,
    condition_of,
)
from knowledge_service.job_failures import (
    DEFAULT_RETRY_POLICY,
    JobFailure,
    RetryPolicy,
    fail_with,
)
from knowledge_service.persistence.job_attempts import finish_attempt
from knowledge_service.persistence.job_recovery import (
    load_silent_job_ids,
    load_undelivered_job_ids,
    lock_recovery_candidate,
)
from knowledge_service.persistence.jobs import save_job

logger = logging.getLogger(__name__)

MAX_RECOVERY_BATCH: Final = 1_000


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """The Jobs one recovery pass failed, by why, and those it could not."""

    stale: tuple[JobId, ...]
    orphaned: tuple[JobId, ...]
    errors: tuple[JobId, ...]


async def recover_stuck_jobs(
    runtime: DatabaseRuntime,
    *,
    now: datetime,
    policy: RecoveryPolicy = DEFAULT_RECOVERY_POLICY,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    limit: int = 100,
) -> RecoveryReport:
    """Fail the attempts of up to `limit` stale and `limit` orphaned Jobs.

    Each Job is judged again under its own lock, in its own transaction, so
    a report or a claim that arrived meanwhile keeps it alive, and one Job
    that cannot be recovered does not hold up the others.
    """
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")
    if type(limit) is not int or not 1 <= limit <= MAX_RECOVERY_BATCH:
        raise ValueError(f"limit must be an integer from 1 to {MAX_RECOVERY_BATCH}")
    async with runtime.transaction() as connection:
        silent = await load_silent_job_ids(
            connection, stale_after=policy.stale_after, limit=limit
        )
        undelivered = await load_undelivered_job_ids(
            connection, now=now, orphan_after=policy.orphan_after, limit=limit
        )
    recovered: dict[JobCondition, list[JobId]] = {
        JobCondition.STALE: [],
        JobCondition.ORPHANED: [],
    }
    errors: list[JobId] = []
    for job_id in (*silent, *undelivered):
        try:
            condition = await _recover(
                runtime,
                job_id=job_id,
                now=now,
                policy=policy,
                retry_policy=retry_policy,
            )
        except Exception as error:
            logger.warning(
                "job %s could not be recovered: %s", job_id.value, type(error).__name__
            )
            errors.append(job_id)
            continue
        if condition is not None:
            recovered[condition].append(job_id)
    return RecoveryReport(
        stale=tuple(recovered[JobCondition.STALE]),
        orphaned=tuple(recovered[JobCondition.ORPHANED]),
        errors=tuple(errors),
    )


async def _recover(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    now: datetime,
    policy: RecoveryPolicy,
    retry_policy: RetryPolicy,
) -> JobCondition | None:
    async with runtime.transaction() as connection:
        candidate = await lock_recovery_candidate(connection, job_id=job_id)
        if candidate is None:
            return None
        job = candidate.job
        # The silence was measured by the database's clock; placed before
        # `now`, it is judged the same whatever clock the worker had.
        silent_for = candidate.silent_for
        condition = condition_of(
            job,
            now=now,
            policy=policy,
            published_at=candidate.published_at,
            last_heard_at=None if silent_for is None else now - silent_for,
        )
        # Never end an attempt before anything the Job already recorded.
        at = max(now, job.queued_at, job.started_at or now)
        if condition is JobCondition.STALE:
            failed = fail_with(job, JobFailure.WORKER_LOST, now=at, policy=retry_policy)
        elif condition is JobCondition.ORPHANED:
            # The lost message stood for an attempt: count it, so a message
            # that keeps getting lost ends in a decision, not a loop.
            failed = fail_with(
                job.start(started_at=at),
                JobFailure.DELIVERY_LOST,
                now=at,
                policy=retry_policy,
            )
        else:
            return None
        await finish_attempt(connection, job=failed)
        await save_job(connection, job=failed)
    logger.info("job %s recovered: %s", job_id.value, condition)
    return condition
```

**Action 9:** in `src/knowledge_service/ingestion/deletions.py`, never start or end before the queue time, in the purge:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
    try:
        async with runtime.transaction() as connection:
            job = await _claim(connection, job_id=job_id, now=now)
            claimed_attempt = job.attempt
            try:
                # A savepoint: a failed delete is undone, while the claim
```

and replace it with:

```python
    try:
        async with runtime.transaction() as connection:
            job = await _claim(connection, job_id=job_id, now=now)
            # A Job queued by a clock ahead of this one starts, and ends, no
            # earlier than it was queued.
            now = max(now, job.queued_at)
            claimed_attempt = job.attempt
            try:
                # A savepoint: a failed delete is undone, while the claim
```

in `_record_lost_attempt`:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
            raise JobAlreadyClaimed(
                stored, "another delivery already ran the attempt this one lost"
            )
        failed = fail_attempt(
            stored.start(started_at=now), cause, now=now, policy=retry_policy
        )
```

and replace it with:

```python
            raise JobAlreadyClaimed(
                stored, "another delivery already ran the attempt this one lost"
            )
        now = max(now, stored.queued_at)
        failed = fail_attempt(
            stored.start(started_at=now), cause, now=now, policy=retry_policy
        )
```

and in `_claim`:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
        raise InvalidJob("the job is not a document chunk purge")
    if job.status is not JobStatus.QUEUED:
        raise JobAlreadyClaimed(job)
    return job.start(started_at=now)
```

and replace it with:

```python
        raise InvalidJob("the job is not a document chunk purge")
    if job.status is not JobStatus.QUEUED:
        raise JobAlreadyClaimed(job)
    return job.start(started_at=max(now, job.queued_at))
```

**Action 10:** in `src/knowledge_service/worker/relay.py`, describe the new first step:

In `src/knowledge_service/worker/relay.py`, find:

```python
"""Queue due retries and due synchronizations, then publish: one pass.

Run `python -m knowledge_service.worker.relay` after a crash or a Redis
outage, and on a schedule, because it is also what brings a failed Job back
once its retry time has come, and what queues each enabled Source's next
synchronization. It exits 0 when every Job it found was published or
skipped, and 1 when the queue refused a message or scheduling failed; the
rest wait for the next pass. A failed scheduling step does not stop the
publishing step.
"""

import asyncio
```

and replace it with:

```python
"""Recover stuck Jobs, queue due retries and synchronizations, then publish.

Run `python -m knowledge_service.worker.relay` after a crash or a Redis
outage, and on a schedule, because it is also what fails the attempts of
stale and orphaned Jobs, brings a failed Job back once its retry time has
come, and queues each enabled Source's next synchronization. It exits 0
when every Job it found was published or skipped, and 1 when the queue
refused a message, or recovery or scheduling failed; the rest wait for the
next pass. A failed recovery or scheduling step does not stop the steps
after it.
"""

import asyncio
```

import it:

In `src/knowledge_service/worker/relay.py`, find:

```python
    publish_pending_jobs,
    requeue_due_retries,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import broker_from_settings
from knowledge_service.worker.jobs import (
```

and replace it with:

```python
    publish_pending_jobs,
    requeue_due_retries,
)
from knowledge_service.job_recovery import RecoveryReport, recover_stuck_jobs
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import broker_from_settings
from knowledge_service.worker.jobs import (
```

report it:

In `src/knowledge_service/worker/relay.py`, find:

```python
class RelayPass:
    """What one relay pass did, in the order it did it."""

    requeued: tuple[JobId, ...]
    scheduled: ScheduleReport
    schedule_failed: bool
```

and replace it with:

```python
class RelayPass:
    """What one relay pass did, in the order it did it."""

    recovered: RecoveryReport
    recovery_failed: bool
    requeued: tuple[JobId, ...]
    scheduled: ScheduleReport
    schedule_failed: bool
```

run it first:

In `src/knowledge_service/worker/relay.py`, find:

```python
    now: datetime,
    schedule: SynchronizationSchedule,
) -> RelayPass:
    """Queue due retries, queue due synchronizations, then publish.

    A failure while scheduling is logged by type and reported; publishing
    still runs, so other Jobs are not held up by it.
    """
    requeued = await requeue_due_retries(runtime, now=now)
    try:
        scheduled = await schedule_due_synchronizations(
```

and replace it with:

```python
    now: datetime,
    schedule: SynchronizationSchedule,
) -> RelayPass:
    """Recover stuck Jobs, queue due retries and synchronizations, then publish.

    A failure while recovering or scheduling is logged by type and
    reported; the later steps still run, so other Jobs are not held up by it.
    """
    try:
        recovered = await recover_stuck_jobs(runtime, now=now)
        recovery_failed = bool(recovered.errors)
    except Exception as error:
        logger.warning("recovering stuck jobs failed: %s", type(error).__name__)
        recovered = RecoveryReport(stale=(), orphaned=(), errors=())
        recovery_failed = True
    requeued = await requeue_due_retries(runtime, now=now)
    try:
        scheduled = await schedule_due_synchronizations(
```

return it:

In `src/knowledge_service/worker/relay.py`, find:

```python
        schedule_failed = True
    published = await publish_pending_jobs(runtime, publisher=publisher, now=now)
    return RelayPass(
        requeued=requeued,
        scheduled=scheduled,
        schedule_failed=schedule_failed,
```

and replace it with:

```python
        schedule_failed = True
    published = await publish_pending_jobs(runtime, publisher=publisher, now=now)
    return RelayPass(
        recovered=recovered,
        recovery_failed=recovery_failed,
        requeued=requeued,
        scheduled=scheduled,
        schedule_failed=schedule_failed,
```

and print it and fail the exit code on it:

In `src/knowledge_service/worker/relay.py`, find:

```python
        broker.close()
    report = done.published
    sys.stdout.write(
        f"requeued {len(done.requeued)}, scheduled {len(done.scheduled.queued)}, "
        f"published {report.published}, skipped {report.skipped}, "
        f"failed {report.failed}, untried {report.untried}\n"
    )
    return 1 if report.failed or done.schedule_failed else 0


if __name__ == "__main__":
```

and replace it with:

```python
        broker.close()
    report = done.published
    sys.stdout.write(
        f"stale {len(done.recovered.stale)}, orphaned {len(done.recovered.orphaned)}, "
        f"requeued {len(done.requeued)}, scheduled {len(done.scheduled.queued)}, "
        f"published {report.published}, skipped {report.skipped}, "
        f"failed {report.failed}, untried {report.untried}\n"
    )
    failed = report.failed or done.schedule_failed or done.recovery_failed
    return 1 if failed else 0


if __name__ == "__main__":
```

**Check:** `uv run pyright src` reports 0 errors; `uv run pytest tests/test_indexes.py -q` reports `4 passed`; `uv run pytest tests/test_migrations_integration.py -q -m integration` reports `3 passed`; and `uv run pytest tests/test_job_progress_integration.py tests/test_deletions_integration.py tests/test_job_retry_integration.py tests/test_scheduling_integration.py -q -m integration` reports `71 passed`.

### Step 4 — Coding step: decisions and the API

This step lets an administrator see the Jobs that are stuck for good, and retry or cancel one.

**In plain words:** a new permission, held only by administrators, opens three endpoints. One lists the Jobs that failed for good and that nothing newer replaced, each with the decisions that fit it. The other two retry or cancel one Job. Each decision locks the Job, changes it through the rules from Step 2, and writes an audit record naming who decided, all in one transaction. The API answers with codes only, never with error text.

| Endpoint | Answers |
|---|---|
| `GET /v1/job-decisions` | 200 with up to 100 Jobs |
| `POST /v1/jobs/{job_id}/retry` | 200 with the queued Job; 404 `job_not_found`; 409 `job_already_open` or `decision_not_allowed` |
| `POST /v1/jobs/{job_id}/cancel` | 200 with the cancelled Job; 404; 409 `job_running` or `decision_not_allowed` |

Every endpoint checks the token and the `recover_jobs` operation before it parses the ID, as the Conversation delete does.


**Action 1:** in `src/knowledge_service/audit.py`, add the two actions:

In `src/knowledge_service/audit.py`, find:

```python
    MESSAGE_RATED = "message.rated"
    EMBEDDING_MIGRATION_STARTED = "embedding_migration.started"
    EMBEDDING_MIGRATION_SWITCHED = "embedding_migration.switched"


class AuditTargetKind(StrEnum):
```

and replace it with:

```python
    MESSAGE_RATED = "message.rated"
    EMBEDDING_MIGRATION_STARTED = "embedding_migration.started"
    EMBEDDING_MIGRATION_SWITCHED = "embedding_migration.switched"
    JOB_RETRIED = "job.retried"
    JOB_CANCELLED = "job.cancelled"


class AuditTargetKind(StrEnum):
```

**Action 2:** in `src/knowledge_service/roles.py`, add the operation:

In `src/knowledge_service/roles.py`, find:

```python
    RUN_PAID_EVALUATION = "run_paid_evaluation"
    MANAGE_POLICY = "manage_policy"
    MANAGE_OPERATIONAL_CONFIGURATION = "manage_operational_configuration"


PERMISSIONS: Mapping[ApplicationRole, frozenset[Operation]] = MappingProxyType(
```

and replace it with:

```python
    RUN_PAID_EVALUATION = "run_paid_evaluation"
    MANAGE_POLICY = "manage_policy"
    MANAGE_OPERATIONAL_CONFIGURATION = "manage_operational_configuration"
    RECOVER_JOBS = "recover_jobs"


PERMISSIONS: Mapping[ApplicationRole, frozenset[Operation]] = MappingProxyType(
```

and give it to administrators:

In `src/knowledge_service/roles.py`, find:

```python
            {Operation.MANAGE_EVALUATION_DATASETS, Operation.RUN_PAID_EVALUATION}
        ),
        ApplicationRole.ADMINISTRATOR: frozenset(
            {Operation.MANAGE_POLICY, Operation.MANAGE_OPERATIONAL_CONFIGURATION}
        ),
    }
)
```

and replace it with:

```python
            {Operation.MANAGE_EVALUATION_DATASETS, Operation.RUN_PAID_EVALUATION}
        ),
        ApplicationRole.ADMINISTRATOR: frozenset(
            {
                Operation.MANAGE_POLICY,
                Operation.MANAGE_OPERATIONAL_CONFIGURATION,
                Operation.RECOVER_JOBS,
            }
        ),
    }
)
```

**Action 3:** in `tests/test_roles.py`, add it to the matrix:

In `tests/test_roles.py`, find:

```python
    Operation.RUN_PAID_EVALUATION: {EVALUATOR},
    Operation.MANAGE_POLICY: {ADMINISTRATOR},
    Operation.MANAGE_OPERATIONAL_CONFIGURATION: {ADMINISTRATOR},
}
ROLE_SETS = [
    frozenset(chosen)
```

and replace it with:

```python
    Operation.RUN_PAID_EVALUATION: {EVALUATOR},
    Operation.MANAGE_POLICY: {ADMINISTRATOR},
    Operation.MANAGE_OPERATIONAL_CONFIGURATION: {ADMINISTRATOR},
    Operation.RECOVER_JOBS: {ADMINISTRATOR},
}
ROLE_SETS = [
    frozenset(chosen)
```

**Action 4:** create the decisions workflow.

Create `src/knowledge_service/job_decisions.py`:

```python
"""An operator's decisions about Jobs recovery cannot move on by itself.

A Job that failed with no retry planned, because its attempts ran out or its
failure was permanent, stays failed until an operator retries or cancels it.
Each decision locks the Job, changes it through the domain, and records who
decided in the audit log, all in one transaction.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import exc as database_errors
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import AuditEventId, JobId, UserId
from knowledge_service.jobs import InvalidJob, Job
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.job_recovery import load_failed_for_good
from knowledge_service.persistence.jobs import load_job, save_job

MAX_DECISION_LIST: Final = 500


class JobNotFound(LookupError):
    """No Job has this ID."""


class JobIntentAlreadyOpen(InvalidJob):
    """Another Job for the same work is open, so this one cannot be queued again."""


async def load_jobs_needing_decision(
    runtime: DatabaseRuntime, *, limit: int = 100
) -> tuple[Job, ...]:
    """Jobs that failed for good and that no newer Job replaced, newest first."""
    if type(limit) is not int or not 1 <= limit <= MAX_DECISION_LIST:
        raise ValueError(f"limit must be an integer from 1 to {MAX_DECISION_LIST}")
    async with runtime.transaction() as connection:
        return await load_failed_for_good(connection, limit=limit)


async def retry_job(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> Job:
    """Queue a Job that failed for good once more; the relay publishes it.

    A Job with a retry planned, or one that did not fail, is refused by the
    domain. While another Job for the same work is open, the retry is
    refused with `JobIntentAlreadyOpen`, so one piece of work never has two
    open Jobs.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        job = await _lock(connection, job_id=job_id)
        reopened = job.reopen(queued_at=now)
        try:
            # A savepoint, so the refused update leaves the transaction usable.
            async with connection.begin_nested():
                await save_job(connection, job=reopened)
        except database_errors.IntegrityError as error:
            if _constraint(error) == "uq_jobs_open_intent":
                raise JobIntentAlreadyOpen(
                    "another job for the same work is open"
                ) from None
            raise
        await record_audit_event(
            connection,
            _decision_event(
                job,
                action=AuditAction.JOB_RETRIED,
                operator=operator,
                at=now,
                request_id=request_id,
            ),
        )
    return reopened


async def cancel_job(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> Job:
    """Close a queued or failed Job so nothing runs it again.

    A running Job is refused with `JobStillRunning`: its worker may still be
    writing. A message already sent for a queued Job finds it cancelled and
    does nothing.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        job = await _lock(connection, job_id=job_id)
        cancelled = job.cancel(finished_at=_not_before(job, now))
        await save_job(connection, job=cancelled)
        await record_audit_event(
            connection,
            _decision_event(
                job,
                action=AuditAction.JOB_CANCELLED,
                operator=operator,
                at=now,
                request_id=request_id,
            ),
        )
    return cancelled


async def _lock(connection: AsyncConnection, *, job_id: JobId) -> Job:
    job = await load_job(connection, job_id=job_id, for_update=True)
    if job is None:
        raise JobNotFound("no job has this ID")
    return job


def _not_before(job: Job, now: datetime) -> datetime:
    # A worker's clock may run ahead of this one; a Job never ends before
    # anything it already recorded.
    recorded = [job.queued_at, job.started_at, job.finished_at]
    return max([now, *(moment for moment in recorded if moment is not None)])


def _constraint(error: database_errors.IntegrityError) -> str | None:
    diagnostics = getattr(error.orig, "diag", None)
    name = getattr(diagnostics, "constraint_name", None)
    return name if type(name) is str else None


def _require_utc(now: datetime) -> None:
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")


def _decision_event(
    job: Job,
    *,
    action: AuditAction,
    operator: UserId,
    at: datetime,
    request_id: UUID | None,
) -> AuditEvent:
    """Who decided what about which Job, and the state it was in; no content."""
    metadata: dict[str, str | int | bool] = {
        "kind": job.kind.value,
        "from_status": job.status.value,
        "failed_attempts": job.attempt,
    }
    if job.last_error_code is not None:
        metadata["error_code"] = job.last_error_code
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=at,
        actor_id=operator,
        action=action,
        target_kind=AuditTargetKind.JOB,
        target_id=job.job_id.value,
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=request_id,
        run_id=None,
        metadata=metadata,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DatabaseJobDecisions:
    """The operator's Job decisions, stored in PostgreSQL, at the clock's time."""

    runtime: DatabaseRuntime
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def needing_decision(self, *, limit: int) -> tuple[Job, ...]:
        return await load_jobs_needing_decision(self.runtime, limit=limit)

    async def retry(self, *, job_id: JobId, operator: UserId, request_id: UUID) -> Job:
        return await retry_job(
            self.runtime,
            job_id=job_id,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )

    async def cancel(self, *, job_id: JobId, operator: UserId, request_id: UUID) -> Job:
        return await cancel_job(
            self.runtime,
            job_id=job_id,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )
```

**Action 5:** in `src/knowledge_service/contracts.py`, add the two response models after `SynchronizationJobReport`:

In `src/knowledge_service/contracts.py`, find:

```python
    error_code: str | None
    retry_at: datetime | None
    latest_attempt: JobAttemptProgress | None
```

and replace it with:

```python
    error_code: str | None
    retry_at: datetime | None
    latest_attempt: JobAttemptProgress | None


class OperatorJob(BaseModel):
    """A Job as an operator decides about it: what it is, and how it ended."""

    job_id: UUID
    kind: str
    target_id: UUID
    status: str
    condition: str
    failed_attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    queued_at: datetime
    finished_at: datetime | None
    error_code: str | None
    # What an operator may decide now: "retry", "cancel", both, or neither.
    decisions: list[str]


class JobsNeedingDecision(BaseModel):
    """Jobs that failed for good and that no newer Job replaced, newest first."""

    jobs: list[OperatorJob]
```

**Action 6:** in `src/knowledge_service/app.py`, import the clock:

In `src/knowledge_service/app.py`, find:

```python
import logging
import re
from collections.abc import Awaitable, Callable, Coroutine
from typing import Protocol
from uuid import UUID, uuid4
```

and replace it with:

```python
import logging
import re
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4
```

the response models:

In `src/knowledge_service/app.py`, find:

```python
    Answer,
    ErrorResponse,
    JobAttemptProgress,
    Question,
    SynchronizationJobReport,
)
```

and replace it with:

```python
    Answer,
    ErrorResponse,
    JobAttemptProgress,
    JobsNeedingDecision,
    OperatorJob,
    Question,
    SynchronizationJobReport,
)
```

and the domain:

In `src/knowledge_service/app.py`, find:

```python
    Unauthenticated,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import JobKind
from knowledge_service.roles import Operation, PermissionDenied, require
from knowledge_service.workflow import AnswerWorkflow
```

and replace it with:

```python
    Unauthenticated,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.job_conditions import allowed_decisions, condition_of
from knowledge_service.job_decisions import JobIntentAlreadyOpen, JobNotFound
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import (
    IllegalJobTransition,
    Job,
    JobKind,
    JobNotRetryable,
    JobStillRunning,
)
from knowledge_service.roles import Operation, PermissionDenied, require
from knowledge_service.workflow import AnswerWorkflow
```

add the protocol and the view after `JobReports`:

In `src/knowledge_service/app.py`, find:

```python
    ) -> JobReport | None: ...


# The Jobs the job endpoint shows: those a Synchronization Run starter owns.
VISIBLE_JOB_KINDS = frozenset({JobKind.SYNCHRONIZE_SOURCE})
```

and replace it with:

```python
    ) -> JobReport | None: ...


class JobDecisions(Protocol):
    """An operator's decisions about Jobs that recovery cannot move on.

    `DatabaseJobDecisions` is the implementation. `retry` and `cancel`
    raise `JobNotFound` for a missing Job, and a domain error for a Job the
    decision does not fit.
    """

    async def needing_decision(self, *, limit: int) -> tuple[Job, ...]: ...

    async def retry(
        self, *, job_id: JobId, operator: UserId, request_id: UUID
    ) -> Job: ...

    async def cancel(
        self, *, job_id: JobId, operator: UserId, request_id: UUID
    ) -> Job: ...


# How many Jobs the decision list shows at most.
DECISION_LIST_LIMIT = 100


def operator_job(job: Job, *, now: datetime) -> OperatorJob:
    """The API view of a Job for an operator, judged at `now`."""
    return OperatorJob(
        job_id=job.job_id.value,
        kind=job.kind.value,
        target_id=job.target_id,
        status=job.status.value,
        condition=condition_of(job, now=now).value,
        failed_attempts=job.attempt,
        max_attempts=job.max_attempts,
        queued_at=job.queued_at,
        finished_at=job.finished_at,
        error_code=job.last_error_code,
        decisions=[decision.value for decision in allowed_decisions(job)],
    )


# The Jobs the job endpoint shows: those a Synchronization Run starter owns.
VISIBLE_JOB_KINDS = frozenset({JobKind.SYNCHRONIZE_SOURCE})
```

take the store in `create_app`:

In `src/knowledge_service/app.py`, find:

```python
    authenticator: RequestAuthenticator | None = None,
    conversations: ConversationStore | None = None,
    job_reports: JobReports | None = None,
) -> FastAPI:
    """The HTTP application.
```

and replace it with:

```python
    authenticator: RequestAuthenticator | None = None,
    conversations: ConversationStore | None = None,
    job_reports: JobReports | None = None,
    job_decisions: JobDecisions | None = None,
) -> FastAPI:
    """The HTTP application.
```

and add the three endpoints at the end of `create_app`, with two small helpers after it:

In `src/knowledge_service/app.py`, find:

```python
            )
        return synchronization_job_report(report)

    return app


async def read_question(request: Request) -> Question:
    """The body as a Question, read only up to `MAX_BODY_BYTES`."""
    declared = request.headers.get("content-length", "")
```

and replace it with:

```python
            )
        return synchronization_job_report(report)

    @app.get("/v1/job-decisions", response_model=JobsNeedingDecision)
    async def list_jobs_needing_decision(
        request: Request,
    ) -> JobsNeedingDecision | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.RECOVER_JOBS)
        request_id = request_id_from(request)
        if job_decisions is None:
            return _jobs_unavailable(request_id)
        try:
            jobs = await job_decisions.needing_decision(limit=DECISION_LIST_LIMIT)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("job decision list failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        now = datetime.now(UTC)
        return JobsNeedingDecision(jobs=[operator_job(job, now=now) for job in jobs])

    @app.post("/v1/jobs/{job_id}/retry", response_model=OperatorJob)
    async def retry_failed_job(
        request: Request, job_id: str
    ) -> OperatorJob | JSONResponse:
        return await decide(request, job_id, retry=True)

    @app.post("/v1/jobs/{job_id}/cancel", response_model=OperatorJob)
    async def cancel_unfinished_job(
        request: Request, job_id: str
    ) -> OperatorJob | JSONResponse:
        return await decide(request, job_id, retry=False)

    async def decide(
        request: Request, job_id: str, *, retry: bool
    ) -> OperatorJob | JSONResponse:
        # Who, then whether they may, then which Job.
        caller = await authenticate(request)
        require(caller.roles, Operation.RECOVER_JOBS)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(job_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if job_decisions is None:
            return _jobs_unavailable(request_id)
        decision = job_decisions.retry if retry else job_decisions.cancel
        try:
            decided = await decision(
                job_id=JobId.parse(job_id),
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except JobNotFound:
            return error_response(
                status_code=404,
                code="job_not_found",
                message="No such job",
                request_id=request_id,
            )
        except JobIntentAlreadyOpen:
            return _conflict(
                "job_already_open", "Another job for this work is open", request_id
            )
        except JobStillRunning:
            return _conflict("job_running", "The job is still running", request_id)
        except JobNotRetryable, IllegalJobTransition:
            return _conflict(
                "decision_not_allowed",
                "This decision does not fit the job's status",
                request_id,
            )
        except Exception as error:
            logger.error("job decision failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        return operator_job(decided, now=datetime.now(UTC))

    return app


def _jobs_unavailable(request_id: UUID) -> JSONResponse:
    return error_response(
        status_code=503,
        code="jobs_unavailable",
        message="Jobs are unavailable",
        request_id=request_id,
    )


def _conflict(code: str, message: str, request_id: UUID) -> JSONResponse:
    return error_response(
        status_code=409, code=code, message=message, request_id=request_id
    )


async def read_question(request: Request) -> Question:
    """The body as a Question, read only up to `MAX_BODY_BYTES`."""
    declared = request.headers.get("content-length", "")
```

**Action 7:** in `tests/test_app_authentication.py`, import `JobNotFound`:

In `tests/test_app_authentication.py`, find:

```python
    user_id_for,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import Job, JobKind
from knowledge_service.roles import ApplicationRole, Operation
```

and replace it with:

```python
    user_id_for,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.job_decisions import JobNotFound
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import Job, JobKind
from knowledge_service.roles import ApplicationRole, Operation
```

list the three routes:

In `tests/test_app_authentication.py`, find:

```python
        Operation.MANAGE_OWN_CONVERSATIONS
    ),
    ("GET", "/v1/jobs/{job_id}"): Operation.START_SYNCHRONIZATION_RUNS,
}
PROTECTED = [(route, operation) for route, operation in ROUTES.items() if operation]
# The routes that read a body, to check none is read before the role.
```

and replace it with:

```python
        Operation.MANAGE_OWN_CONVERSATIONS
    ),
    ("GET", "/v1/jobs/{job_id}"): Operation.START_SYNCHRONIZATION_RUNS,
    ("GET", "/v1/job-decisions"): Operation.RECOVER_JOBS,
    ("POST", "/v1/jobs/{job_id}/retry"): Operation.RECOVER_JOBS,
    ("POST", "/v1/jobs/{job_id}/cancel"): Operation.RECOVER_JOBS,
}
PROTECTED = [(route, operation) for route, operation in ROUTES.items() if operation]
# The routes that read a body, to check none is read before the role.
```

add a fake decisions store after `Reading`, and take it in `full_app`:

In `tests/test_app_authentication.py`, find:

```python
        self.calls.append(request)


def full_app(
    source: Source,
    workflow: Any = None,
    tokens: dict[str, Any] | None = None,
    conversations: Deleting | None = None,
    jobs: Reading | None = None,
) -> Any:
    """The app with every endpoint wired, so every route is real."""
    return create_app(
```

and replace it with:

```python
        self.calls.append(request)


class Deciding:
    """An operator's Job store that records each decision, and finds no Job."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def needing_decision(self, **request: Any) -> tuple[Job, ...]:
        self.calls.append(request)
        return ()

    async def retry(self, **request: Any) -> Job:
        self.calls.append(request)
        raise JobNotFound("no job has this ID")

    async def cancel(self, **request: Any) -> Job:
        self.calls.append(request)
        raise JobNotFound("no job has this ID")


def full_app(
    source: Source,
    workflow: Any = None,
    tokens: dict[str, Any] | None = None,
    conversations: Deleting | None = None,
    jobs: Reading | None = None,
    decisions: Deciding | None = None,
) -> Any:
    """The app with every endpoint wired, so every route is real."""
    return create_app(
```

pass it to `create_app`:

In `tests/test_app_authentication.py`, find:

```python
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
        conversations=conversations or Deleting(),
        job_reports=jobs or Reading(),
    )
```

and replace it with:

```python
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
        conversations=conversations or Deleting(),
        job_reports=jobs or Reading(),
        job_decisions=decisions or Deciding(),
    )
```

pass it in the role test:

In `tests/test_app_authentication.py`, find:

```python
    roles: frozenset[ApplicationRole],
) -> None:
    source, workflow, conversations = Source(), Counting(), Deleting()
    jobs = Reading()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(source, workflow, {"token": token}, conversations, jobs)
    method, path = route

    async with client(app) as http:
```

and replace it with:

```python
    roles: frozenset[ApplicationRole],
) -> None:
    source, workflow, conversations = Source(), Counting(), Deleting()
    jobs, decisions = Reading(), Deciding()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(source, workflow, {"token": token}, conversations, jobs, decisions)
    method, path = route

    async with client(app) as http:
```

and count its calls:

In `tests/test_app_authentication.py`, find:

```python

    allowed = bool(roles & MATRIX[operation])
    assert (response.status_code == 403) is not allowed
    reached = source.readers or workflow.calls or conversations.calls or jobs.calls
    assert bool(reached) is allowed
```

and replace it with:

```python

    allowed = bool(roles & MATRIX[operation])
    assert (response.status_code == 403) is not allowed
    reached = (
        source.readers
        or workflow.calls
        or conversations.calls
        or jobs.calls
        or decisions.calls
    )
    assert bool(reached) is allowed
```

**Check:** `uv run pytest tests/test_roles.py tests/test_app_authentication.py -q` reports `227 passed`.

### Step 5 — Coding step: the recovery drill on real PostgreSQL

This step proves the whole lesson against a real database.

**In plain words:** these tests make Jobs go silent, lose their messages, and fail for good, then run recovery and the API and check what happened. They do not wait five real minutes. Instead, `go_silent` moves an attempt's `heard_at` back with the database's own clock, which is what five quiet minutes would leave behind.

| Tests | What they prove |
|---|---|
| `test_a_slow_job_that_keeps_reporting_is_left_running`, `test_a_silent_job_is_failed_and_its_attempt_keeps_its_progress`, `test_the_next_attempt_gets_its_own_row_and_the_dead_one_stays` | the drill: slow is not dead; the prior attempt's evidence stays |
| `test_a_worker_presumed_dead_cannot_report_after_recovery`, `test_a_job_that_never_reported_is_judged_by_its_start` | block 2, and the fallback to the start time |
| `test_a_report_that_lands_before_the_lock_keeps_the_job_alive`, `test_a_job_whose_row_a_worker_holds_is_skipped` | the re-check under the lock, and `SKIP LOCKED` |
| `test_a_recently_published_job_is_left_waiting` to `test_a_message_lost_every_time_ends_in_a_decision` | block 3 |
| `test_an_operator_retry_gives_an_exhausted_job_one_more_attempt` to `test_the_decision_list_shows_only_failures_nothing_replaced` | block 4, through HTTP, with the audit |
| `test_the_relay_recovers_before_it_requeues_and_publishes`, the two migration tests, `test_the_database_refuses_a_cancelled_job_with_a_planned_retry` | the relay and the schema |
| the tests under "Found by the adversarial review" | blocks 1 and 5, one per finding |


Create `tests/test_job_recovery_integration.py`:

```python
"""Recovering stuck Jobs, and an operator's decisions, on real PostgreSQL.

The drill: a Job that keeps reporting is left running however long it
takes, while one that went silent is failed with `worker_lost`, and its
attempt keeps what it reported. A message no worker took up counts as a lost
attempt, so one that keeps getting lost ends in an operator's decision, not
a loop. An operator retries or cancels through the API, and each decision is
in the audit log.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from authorization_scenarios import Person
from database_fixtures import migration_config, migration_environment
from job_fixtures import (
    OBSERVED_AT,
    SOURCE_ID,
    database_went_away,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service import job_recovery, job_reporting
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.ingestion.deletions import (
    purge_document_chunks,
    requeue_unfinished_purges,
)
from knowledge_service.job_conditions import JobCondition, condition_of
from knowledge_service.job_decisions import (
    DatabaseJobDecisions,
    cancel_job,
    load_jobs_needing_decision,
    retry_job,
)
from knowledge_service.job_failures import RetryPolicy
from knowledge_service.job_progress import (
    AttemptOutcome,
    JobCounters,
    JobProgress,
    JobStage,
    ProgressWentBackwards,
)
from knowledge_service.job_recovery import RecoveryReport, recover_stuck_jobs
from knowledge_service.job_reporting import report_progress
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.persistence.job_attempts import (
    AttemptNotCurrent,
    begin_attempt,
    load_latest_attempt,
)
from knowledge_service.persistence.jobs import (
    enqueue_job,
    mark_job_published,
    requeue_due_jobs,
    save_job,
)
from knowledge_service.persistence.schema import audit_events, job_attempts, jobs
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings
from knowledge_service.worker.jobs import run_stored_job

pytestmark = pytest.mark.integration

STARTED_AT = OBSERVED_AT + timedelta(minutes=1)
POLICY = RetryPolicy(jitter=lambda: 0.5)
# The first retry waits half of 30 s plus half of the other half.
FIRST_DELAY = timedelta(seconds=22.5)

AVERY = Person("avery", ())
CALLERS = {
    "Bearer avery": Caller(
        reader=AVERY.context(), roles=frozenset({ApplicationRole.ADMINISTRATOR})
    ),
}


class Tokens:
    """Gives each test token the caller its claims would give."""

    async def authenticate(self, headers: list[str]) -> Caller:
        if len(headers) == 1 and headers[0] in CALLERS:
            return CALLERS[headers[0]]
        raise Unauthenticated("test", error=AuthenticationError.INVALID_TOKEN)


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


async def running_synchronization(runtime: DatabaseRuntime) -> Job:
    """A synchronization Job whose claim and attempt row committed at 09:01."""
    async with runtime.transaction() as connection:
        queued = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=SOURCE_ID.value,
                queued_at=OBSERVED_AT,
            ),
        )
        running = queued.job.start(started_at=STARTED_AT)
        await save_job(connection, job=running)
        await begin_attempt(connection, job=running)
    return running


async def heartbeat(
    runtime: DatabaseRuntime, job: Job, *, minutes: float, processed: int
) -> None:
    await report_progress(
        runtime,
        job_id=job.job_id,
        attempt=job.attempt + 1,
        stage=JobStage.READING,
        counters=JobCounters(processed=processed),
        now=STARTED_AT + timedelta(minutes=minutes),
    )


async def go_silent(runtime: DatabaseRuntime, job: Job, *, minutes: float) -> None:
    """Make the database have last heard from `job`'s attempt `minutes` ago."""
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(job_attempts)
            .where(
                job_attempts.c.job_id == job.job_id.value,
                job_attempts.c.attempt == job.attempt + 1,
            )
            .values(heard_at=sa.func.now() - timedelta(minutes=minutes))
        )


async def database_now(runtime: DatabaseRuntime) -> datetime:
    async with runtime.transaction() as connection:
        now = await connection.scalar(sa.select(sa.func.now()))
    assert now is not None
    return now.astimezone(UTC)


async def recover(runtime: DatabaseRuntime, *, minutes: float) -> RecoveryReport:
    return await recover_stuck_jobs(
        runtime, now=STARTED_AT + timedelta(minutes=minutes), retry_policy=POLICY
    )


# One row per attempt: its number, how many it processed, and its outcome.
type AttemptRow = tuple[int, int, str | None]
# One row per decision: the action, who took it, and its metadata.
type DecisionRow = tuple[str, uuid.UUID, dict[str, object]]


async def attempts_of(runtime: DatabaseRuntime, job_id: JobId) -> list[AttemptRow]:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(
                job_attempts.c.attempt,
                job_attempts.c.processed,
                job_attempts.c.outcome,
            )
            .where(job_attempts.c.job_id == job_id.value)
            .order_by(job_attempts.c.attempt)
        )
        return [cast(AttemptRow, tuple(row)) for row in rows]


async def latest(runtime: DatabaseRuntime, job_id: JobId) -> JobProgress:
    async with runtime.transaction() as connection:
        progress = await load_latest_attempt(connection, job_id=job_id)
    assert progress is not None
    return progress


async def published_purge(runtime: DatabaseRuntime, *, at: timedelta) -> Job:
    """A queued purge whose message was sent at OBSERVED_AT + `at`."""
    _document, job_id = await tombstoned_with_purge(runtime)
    async with runtime.transaction() as connection:
        await mark_job_published(
            connection, job_id=job_id, published_at=OBSERVED_AT + at
        )
    return await stored_job(runtime, job_id)


async def failed_for_good(
    runtime: DatabaseRuntime, job: Job, *, error_code: str = "database_rejected"
) -> Job:
    """`job`, claimed at 09:01 and failed at 09:02 with no retry planned."""
    failed = job.start(started_at=STARTED_AT).fail(
        error_code=error_code, finished_at=STARTED_AT + timedelta(minutes=1)
    )
    async with runtime.transaction() as connection:
        await save_job(connection, job=failed)
    return failed


async def decisions_of(runtime: DatabaseRuntime, job_id: JobId) -> list[DecisionRow]:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(
                audit_events.c.action,
                audit_events.c.actor_id,
                audit_events.c.metadata,
            ).where(audit_events.c.target_id == job_id.value)
        )
        return [cast(DecisionRow, tuple(row)) for row in rows]


async def over_http(runtime: DatabaseRuntime, method: str, path: str) -> httpx.Response:
    app = create_app(
        authenticator=Tokens(),
        job_decisions=DatabaseJobDecisions(
            runtime, clock=lambda: STARTED_AT + timedelta(hours=2)
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.request(
            method, path, headers={"Authorization": "Bearer avery"}
        )


# The recovery drill: slow is not dead.


async def test_a_slow_job_that_keeps_reporting_is_left_running(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    # An hour of work, a report every four minutes.
    for minute in range(4, 61, 4):
        await heartbeat(runtime, job, minutes=minute, processed=minute * 10)
    # The last report reached the database four minutes ago.
    await go_silent(runtime, job, minutes=4)

    report = await recover(runtime, minutes=63)

    assert report == RecoveryReport(stale=(), orphaned=(), errors=())
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.RUNNING


async def test_a_silent_job_is_failed_and_its_attempt_keeps_its_progress(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=4, processed=120)
    # Nothing reached the database for six minutes since.
    await go_silent(runtime, job, minutes=6)

    report = await recover(runtime, minutes=10)

    assert report.stale == (job.job_id,)
    stored = await stored_job(runtime, job.job_id)
    recovered_at = STARTED_AT + timedelta(minutes=10)
    assert (stored.status, stored.last_error_code) == (JobStatus.FAILED, "worker_lost")
    assert stored.retry_at == recovered_at + FIRST_DELAY
    attempt = await latest(runtime, job.job_id)
    # What the dead attempt did stays: 120 processed, last heard at 09:05.
    assert attempt.counters.processed == 120
    assert attempt.heartbeat_at == STARTED_AT + timedelta(minutes=4)
    assert (attempt.outcome, attempt.finished_at) == (
        AttemptOutcome.RETRY_SCHEDULED,
        recovered_at,
    )


async def test_the_next_attempt_gets_its_own_row_and_the_dead_one_stays(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=4, processed=120)
    await go_silent(runtime, job, minutes=6)
    await recover(runtime, minutes=10)
    retry_at = STARTED_AT + timedelta(minutes=10) + FIRST_DELAY
    async with runtime.transaction() as connection:
        [queued] = await requeue_due_jobs(connection, now=retry_at, limit=10)
        second = queued.start(started_at=retry_at)
        await save_job(connection, job=second)
        await begin_attempt(connection, job=second)

    assert await attempts_of(runtime, job.job_id) == [
        (1, 120, "retry_scheduled"),
        (2, 0, None),
    ]


async def test_a_worker_presumed_dead_cannot_report_after_recovery(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=1, processed=10)
    await go_silent(runtime, job, minutes=9)
    await recover(runtime, minutes=10)

    # The worker was only paused; it wakes up and reports.
    with pytest.raises(AttemptNotCurrent):
        await heartbeat(runtime, job, minutes=11, processed=500)
    assert (await latest(runtime, job.job_id)).counters.processed == 10


async def test_a_job_that_never_reported_is_judged_by_its_start(
    runtime: DatabaseRuntime,
) -> None:
    now = await database_now(runtime)
    started: dict[int, Job] = {}
    async with runtime.transaction() as connection:
        for minutes in (4, 5):
            queued = await enqueue_job(
                connection,
                Job.request(
                    job_id=JobId.new(),
                    kind=JobKind.PURGE_CONVERSATION,
                    target_id=uuid.uuid4(),
                    queued_at=now - timedelta(minutes=10),
                ),
            )
            # Claimed, but its handler stored no attempt row.
            started[minutes] = queued.job.start(
                started_at=now - timedelta(minutes=minutes)
            )
            await save_job(connection, job=started[minutes])

    report = await recover_stuck_jobs(runtime, now=now, retry_policy=POLICY)

    assert report.stale == (started[5].job_id,)


async def test_a_report_that_lands_before_the_lock_keeps_the_job_alive(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await running_synchronization(runtime)
    await go_silent(runtime, job, minutes=6)
    real = job_recovery.load_silent_job_ids

    async def then_a_report(*args: object, **kwargs: object) -> tuple[JobId, ...]:
        found = await real(*args, **kwargs)  # type: ignore[arg-type]
        # Read as silent, then the worker reports before recovery locks it.
        await heartbeat(runtime, job, minutes=9, processed=30)
        return found

    monkeypatch.setattr(job_recovery, "load_silent_job_ids", then_a_report)

    report = await recover(runtime, minutes=10)

    assert report.stale == ()
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.RUNNING


async def test_a_job_whose_row_a_worker_holds_is_skipped(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await go_silent(runtime, job, minutes=6)
    async with runtime.transaction() as connection:
        # A report in progress holds a share lock on the Job's row.
        await connection.execute(
            sa.select(jobs.c.job_id)
            .where(jobs.c.job_id == job.job_id.value)
            .with_for_update(read=True)
        )
        report = await recover(runtime, minutes=10)

    assert report.stale == ()


# Orphaned: a message no worker took up.


async def test_a_recently_published_job_is_left_waiting(
    runtime: DatabaseRuntime,
) -> None:
    await published_purge(runtime, at=timedelta(0))

    # Published at 09:00; 29 minutes later it may still be in the queue.
    report = await recover(runtime, minutes=28)

    assert report == RecoveryReport(stale=(), orphaned=(), errors=())


async def test_an_undelivered_message_counts_as_a_lost_attempt(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))

    report = await recover(runtime, minutes=29)

    assert report.orphaned == (purge.job_id,)
    stored = await stored_job(runtime, purge.job_id)
    assert (stored.status, stored.attempt, stored.last_error_code) == (
        JobStatus.FAILED,
        1,
        "delivery_lost",
    )
    assert await attempts_of(runtime, purge.job_id) == [(1, 0, "retry_scheduled")]


async def test_the_lost_message_arriving_late_does_nothing(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await recover(runtime, minutes=29)

    late = await run_stored_job(
        runtime, purge.job_id, now=STARTED_AT + timedelta(minutes=30)
    )

    assert late.duplicate
    assert (await stored_job(runtime, purge.job_id)).last_error_code == "delivery_lost"


async def test_a_message_lost_every_time_ends_in_a_decision(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    minutes = 29.0
    for _ in range(3):
        await recover(runtime, minutes=minutes)
        stored = await stored_job(runtime, purge.job_id)
        if stored.retry_at is None:
            break
        async with runtime.transaction() as connection:
            await requeue_due_jobs(connection, now=stored.retry_at, limit=10)
            await mark_job_published(
                connection, job_id=purge.job_id, published_at=stored.retry_at
            )
        minutes += 31

    stored = await stored_job(runtime, purge.job_id)
    assert (stored.attempt, stored.retry_at) == (3, None)
    assert condition_of(stored, now=stored.queued_at) is JobCondition.EXHAUSTED
    assert [job.job_id for job in await load_jobs_needing_decision(runtime)] == [
        purge.job_id
    ]
    assert [row[2] for row in await attempts_of(runtime, purge.job_id)] == [
        "retry_scheduled",
        "retry_scheduled",
        "failed",
    ]


# An operator's decisions.


async def test_an_operator_retry_gives_an_exhausted_job_one_more_attempt(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    exhausted = await failed_for_good(runtime, purge, error_code="database_unavailable")
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(jobs).where(jobs.c.job_id == purge.job_id.value).values(attempt=3)
        )

    response = await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/retry")

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["failed_attempts"], body["max_attempts"]) == (
        "queued",
        3,
        4,
    )
    assert body["decisions"] == ["cancel"]
    async with runtime.transaction() as connection:
        published_at = await connection.scalar(
            sa.select(jobs.c.published_at).where(jobs.c.job_id == purge.job_id.value)
        )
    # Unpublished, so the next relay pass sends a new message.
    assert published_at is None
    [(action, actor, metadata)] = await decisions_of(runtime, exhausted.job_id)
    assert (action, actor) == (
        "job.retried",
        CALLERS["Bearer avery"].reader.user_id.value,
    )
    assert metadata == {
        "kind": "purge_document_chunks",
        "from_status": "failed",
        "failed_attempts": 3,
        "error_code": "database_unavailable",
    }


async def test_a_retry_is_refused_while_newer_work_is_open(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await failed_for_good(runtime, purge, error_code="database_unavailable")
    # The next synchronization pass asks for a new purge of the same Document.
    [newer] = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=STARTED_AT + timedelta(hours=1)
    )

    response = await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/retry")

    assert response.status_code == 409
    assert response.json()["code"] == "job_already_open"
    assert (await stored_job(runtime, purge.job_id)).status is JobStatus.FAILED
    assert (await stored_job(runtime, newer)).status is JobStatus.QUEUED
    assert await decisions_of(runtime, purge.job_id) == []


async def test_two_retries_of_the_same_work_at_once_leave_one_open(
    runtime: DatabaseRuntime,
) -> None:
    first = await published_purge(runtime, at=timedelta(0))
    await failed_for_good(runtime, first)
    async with runtime.transaction() as connection:
        second = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=first.target_id,
                queued_at=STARTED_AT,
            ),
        )
    await failed_for_good(runtime, second.job)
    operator = CALLERS["Bearer avery"].reader.user_id
    now = STARTED_AT + timedelta(hours=1)

    results = await asyncio.gather(
        *(
            retry_job(runtime, job_id=job.job_id, operator=operator, now=now)
            for job in (first, second.job)
        ),
        return_exceptions=True,
    )

    assert sorted(type(result).__name__ for result in results) == [
        "Job",
        "JobIntentAlreadyOpen",
    ]


async def test_cancel_closes_a_queued_job_and_its_message_does_nothing(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))

    response = await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/cancel")

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["decisions"]) == (
        "cancelled",
        [],
    )
    late = await run_stored_job(
        runtime, purge.job_id, now=STARTED_AT + timedelta(hours=3)
    )
    assert late.duplicate
    assert late.job.status is JobStatus.CANCELLED
    [(action, _actor, metadata)] = await decisions_of(runtime, purge.job_id)
    assert (action, metadata["from_status"]) == ("job.cancelled", "queued")


async def test_a_cancelled_purge_is_asked_for_again_while_its_chunks_remain(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/cancel")

    # Cancelling does not keep a deleted Document's Chunks: a pass queues
    # a new purge for them.
    requeued = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=STARTED_AT + timedelta(hours=3)
    )

    assert len(requeued) == 1
    assert requeued[0] != purge.job_id


async def test_cancelling_a_failed_job_keeps_why_it_failed(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await failed_for_good(runtime, purge, error_code="source_refused")

    response = await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/cancel")

    assert response.status_code == 200
    stored = await stored_job(runtime, purge.job_id)
    assert (stored.status, stored.last_error_code) == (
        JobStatus.CANCELLED,
        "source_refused",
    )


async def test_a_running_job_cannot_be_cancelled(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)

    response = await over_http(runtime, "POST", f"/v1/jobs/{job.job_id.value}/cancel")

    assert response.status_code == 409
    assert response.json()["code"] == "job_running"
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.RUNNING


async def test_a_decision_that_does_not_fit_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    retried = await over_http(runtime, "POST", f"/v1/jobs/{job.job_id.value}/retry")
    missing = await over_http(runtime, "POST", f"/v1/jobs/{uuid.uuid4()}/retry")

    assert (retried.status_code, retried.json()["code"]) == (
        409,
        "decision_not_allowed",
    )
    assert (missing.status_code, missing.json()["code"]) == (404, "job_not_found")


async def test_the_decision_list_shows_only_failures_nothing_replaced(
    runtime: DatabaseRuntime,
) -> None:
    replaced = await published_purge(runtime, at=timedelta(0))
    await failed_for_good(runtime, replaced, error_code="database_unavailable")
    [newer] = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=STARTED_AT + timedelta(hours=1)
    )
    async with runtime.transaction() as connection:
        newer_job = await stored_job(runtime, newer)
        newest = newer_job.start(started_at=STARTED_AT + timedelta(hours=1)).fail(
            error_code="database_unavailable",
            finished_at=STARTED_AT + timedelta(hours=1, minutes=1),
        )
        await save_job(connection, job=newest)

    response = await over_http(runtime, "GET", "/v1/job-decisions")

    assert response.status_code == 200
    [listed] = response.json()["jobs"]
    assert listed["job_id"] == str(newer.value)
    assert (listed["condition"], listed["decisions"]) == (
        "exhausted",
        ["retry", "cancel"],
    )


# The relay and the migration.


async def test_the_relay_recovers_before_it_requeues_and_publishes(
    runtime: DatabaseRuntime,
) -> None:
    from knowledge_service.ingestion.scheduling import SynchronizationSchedule
    from knowledge_service.worker.relay import relay_once

    job = await running_synchronization(runtime)
    await go_silent(runtime, job, minutes=6)
    sent: list[JobId] = []

    class Recording:
        kinds = frozenset({JobKind.PURGE_DOCUMENT_CHUNKS})

        def publish(self, job: Job) -> None:
            sent.append(job.job_id)

    done = await relay_once(
        runtime,
        publisher=Recording(),
        now=STARTED_AT + timedelta(minutes=10),
        schedule=SynchronizationSchedule(interval=timedelta(hours=1)),
    )

    assert done.recovered.stale == (job.job_id,)
    assert not done.recovery_failed


def test_the_migration_turns_cancelled_jobs_into_final_failures_on_the_way_down(
    migrated_database: str,
) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    job_id = uuid.uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "insert into jobs (job_id, kind, target_id, status, attempt,"
                    " max_attempts, queued_at, finished_at) values (:job_id,"
                    " 'purge_document_chunks', :target_id, 'cancelled', 0, 3,"
                    " :at, :at)"
                ),
                {"job_id": job_id, "target_id": uuid.uuid4(), "at": OBSERVED_AT},
            )
        with migration_environment(url):
            command.downgrade(config, "c9d0e1f2a3b4")
        with engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "select status, last_error_code, started_at from jobs"
                    " where job_id = :job_id"
                ),
                {"job_id": job_id},
            ).one()
        assert tuple(row) == ("failed", "cancelled", OBSERVED_AT)
    finally:
        with migration_environment(url):
            command.upgrade(config, "head")
        engine.dispose()


async def test_the_migration_will_not_go_down_past_recorded_decisions(
    migrated_database: str, runtime: DatabaseRuntime
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await cancel_job(
        runtime,
        job_id=purge.job_id,
        operator=CALLERS["Bearer avery"].reader.user_id,
        now=STARTED_AT,
    )
    url = make_url(migrated_database)

    def downgrade() -> None:
        with migration_environment(url):
            command.downgrade(migration_config(), "c9d0e1f2a3b4")

    with pytest.raises(DBAPIError, match="operator decisions are recorded"):
        await asyncio.to_thread(downgrade)
    # Nothing changed: the Job is still cancelled.
    assert (await stored_job(runtime, purge.job_id)).status is JobStatus.CANCELLED


async def test_the_database_refuses_a_cancelled_job_with_a_planned_retry(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))

    with pytest.raises(DBAPIError, match="ck_jobs_retry_at"):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.update(jobs)
                .where(jobs.c.job_id == purge.job_id.value)
                .values(
                    status="cancelled",
                    finished_at=STARTED_AT,
                    retry_at=STARTED_AT,
                )
            )


# Found by the adversarial review.


async def test_a_worker_whose_clock_runs_behind_is_not_taken_for_dead(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    # The worker reports just now, but its clock runs six minutes behind
    # the relay's: by the worker's stamps it looks silent.
    await heartbeat(runtime, job, minutes=4, processed=40)

    report = await recover(runtime, minutes=10)

    assert report.stale == ()
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.RUNNING


async def test_a_purge_queued_by_a_clock_ahead_of_the_worker_still_runs(
    runtime: DatabaseRuntime,
) -> None:
    _document, job_id = await tombstoned_with_purge(runtime)

    # Queued at 09:00 by a clock ten seconds ahead of this worker's.
    ran = await purge_document_chunks(
        runtime, job_id=job_id, now=OBSERVED_AT - timedelta(seconds=10)
    )

    assert ran.status is JobStatus.SUCCEEDED
    assert ran.started_at == OBSERVED_AT


async def test_a_job_queued_by_a_clock_ahead_does_not_crowd_out_a_real_orphan(
    runtime: DatabaseRuntime,
) -> None:
    orphan = await published_purge(runtime, at=timedelta(0))
    async with runtime.transaction() as connection:
        ahead = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=SOURCE_ID.value,
                queued_at=OBSERVED_AT + timedelta(days=1),
            ),
        )
        # Published earlier than the orphan, so it would be tried first.
        await mark_job_published(
            connection,
            job_id=ahead.job.job_id,
            published_at=OBSERVED_AT - timedelta(hours=1),
        )

    report = await recover_stuck_jobs(
        runtime, now=STARTED_AT + timedelta(minutes=29), retry_policy=POLICY, limit=1
    )

    assert report.orphaned == (orphan.job_id,)


async def test_a_refused_report_still_shows_the_worker_is_alive(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=4, processed=40)
    await go_silent(runtime, job, minutes=6)
    # The worker's clock stepped back three minutes: its report is refused,
    # yet it was sent just now.
    with pytest.raises(ProgressWentBackwards):
        await heartbeat(runtime, job, minutes=1, processed=50)

    report = await recover(runtime, minutes=10)

    assert report.stale == ()
    assert (await latest(runtime, job.job_id)).counters.processed == 40


async def test_a_refusal_stays_typed_when_recording_the_worker_fails(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=4, processed=40)

    async def unreachable(*args: object, **kwargs: object) -> bool:
        raise database_went_away()

    monkeypatch.setattr(job_reporting, "record_heard", unreachable)

    with pytest.raises(ProgressWentBackwards):
        await heartbeat(runtime, job, minutes=1, processed=50)
```

**Check:** `uv run pytest tests/test_job_recovery_integration.py -q -m integration` reports `29 passed`.

### Step 6 — No coding in this step: break it on purpose

This step makes recovery judge a running Job by its start time only, as if it never listened to reports.

**Action 1:** in `condition_of` in `src/knowledge_service/job_conditions.py`, change this line:

```python
        heard = last_heard_at or job.started_at
```

to:

```python
        heard = job.started_at
```

**Action 2:** run:

```bash
uv run pytest tests/test_job_conditions.py tests/test_job_recovery_integration.py -q -m "integration or not integration"
```

You see `3 failed, 62 passed`. The two unit tests that judge a running Job by its last report fail, and so does `test_a_report_that_lands_before_the_lock_keeps_the_job_alive`: a Job read as silent, which reported before recovery locked it, is now failed although it is alive.

The slow Job of the drill still passes, and so do the clock tests. The query that picks candidates measures silence with `heard_at` in SQL, so it never offers a Job that reported recently. `condition_of` is the second layer: it matters when the first read is already out of date, as in the race. That is block 2's "judge again under the lock", seen from the inside.

**Action 3:** undo the change and run the command again: `65 passed`.

**Check:** compare the count with your Step 1 prediction, and note it in the evidence file.

### Step 7 — No coding in this step: update four documents

This step records recovery and the decisions where readers look for them.


**Action 1:** in the architecture, describe the three endpoints after the job endpoint:

In `docs/architecture.md`, find:

```markdown
through `DatabaseJobReports` (`job_reporting`), read from one snapshot. A
missing Job and a Job of any other kind give the same 404, so a purge's
target, such as a Conversation, is never shown.

`job_dispatch` puts a Job on the queue without ever losing it. `submit_job`
commits the Job to PostgreSQL first, then sends one message naming only its
```

and replace it with:

```markdown
through `DatabaseJobReports` (`job_reporting`), read from one snapshot. A
missing Job and a Job of any other kind give the same 404, so a purge's
target, such as a Conversation, is never shown.
`GET /v1/job-decisions`, `POST /v1/jobs/{job_id}/retry`, and
`POST /v1/jobs/{job_id}/cancel` require `recover_jobs` (the administrator
role) before the ID is parsed. They list the Jobs that failed for good and
that no newer Job replaced, and retry or cancel one through
`DatabaseJobDecisions` (`job_decisions`), which records each decision in the
audit log with the operator as its actor.

`job_dispatch` puts a Job on the queue without ever losing it. `submit_job`
commits the Job to PostgreSQL first, then sends one message naming only its
```

add the recovery paragraph after the progress paragraph:

In `docs/architecture.md`, find:

```markdown
write; counters and warnings never go down and the heartbeat never goes
back, both in the domain and in the `UPDATE`'s own conditions; a report
stamped more than five minutes ahead of the database's clock is refused.
Ending never fails on what a report stored: higher counters stay, and a
heartbeat later than the Job's finish time is pulled back to it. A worker
presumed dead that ends an attempt its Job has moved past gets
```

and replace it with:

```markdown
write; counters and warnings never go down and the heartbeat never goes
back, both in the domain and in the `UPDATE`'s own conditions; a report
stamped more than five minutes ahead of the database's clock is refused.

`job_conditions` tells a slow Job from a dead one, and `job_recovery`, run
first in every relay pass, acts on it. A running Job is alive
while the database heard from it within `stale_after` (five minutes): each
report stamps the attempt's `heard_at` with the database's own clock, even
one refused for going back, so no worker's clock decides it, and a Job with
no attempt row falls back to its start time. One silent longer is stale, and its attempt is failed with
`worker_lost`. A queued Job whose message was sent
`orphan_after` (30 minutes) ago and that no worker took up is orphaned, and
the attempt the lost message stood for is failed with `delivery_lost`. Both
codes are transient, so the stored Job plans a retry while attempts are
left, and a message that keeps getting lost ends as `exhausted`, not in a
loop. Each Job is judged again under its row lock, skipping a row a worker
holds, and its attempt row is ended, never deleted. A Job that failed with
no retry planned waits for an operator: a retry gives an exhausted Job one
more attempt and is refused while another Job for the same work is open; a
cancel closes a queued or failed Job as `cancelled`, and is refused for a
running one, whose worker may still write.
Ending never fails on what a report stored: higher counters stay, and a
heartbeat later than the Job's finish time is pulled back to it. A worker
presumed dead that ends an attempt its Job has moved past gets
```

and update the `jobs` and `job_attempts` rows:

In `docs/architecture.md`, find:

```markdown
| `messages` | One Message per `(conversation_id, ordinal)`; only an assistant Message has an `outcome`: `answered`, `abstained`, or `failed`. |
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code; a retry time only on a failed Job with attempts left, never before it finished; at most one open Job (queued, running, or failed with a retry scheduled) per kind and target. |
| `job_attempts` | One row per `(job_id, attempt)`, removed with its Job; a known stage, warnings, and outcome; counters never below zero; an outcome exactly when it has a finish time; a heartbeat no earlier than the start and no later than the finish. |
| `audit_events` | An action, target kind, decision (`allowed` or `denied`), and outcome from fixed lists, with optional request and Synchronization Run IDs; no raw content: metadata keys are short names, string values one plain word, and the bag is capped at 1024 bytes. Rows are only ever inserted. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |
```

and replace it with:

```markdown
| `messages` | One Message per `(conversation_id, ordinal)`; only an assistant Message has an `outcome`: `answered`, `abstained`, or `failed`. |
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries (a `cancelled` Job has a finish time, and keeps the start and error code it had, if any); a bounded attempt count; a capped error code; a retry time only on a failed Job with attempts left, never before it finished; at most one open Job (queued, running, or failed with a retry scheduled) per kind and target. |
| `job_attempts` | One row per `(job_id, attempt)`, removed with its Job; a `heard_at` the database stamps on every report; a known stage, warnings, and outcome; counters never below zero; an outcome exactly when it has a finish time; a heartbeat no earlier than the start and no later than the finish. |
| `audit_events` | An action, target kind, decision (`allowed` or `denied`), and outcome from fixed lists, with optional request and Synchronization Run IDs; no raw content: metadata keys are short names, string values one plain word, and the bag is capped at 1024 bytes. Rows are only ever inserted. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |
```

**Action 2:** in the operations guide, say what the relay recovers and where decisions wait:

In `docs/operations.md`, find:

```markdown

## Telemetry

Use OpenTelemetry for traces and metrics and structured JSON logs with shared request, Conversation, Synchronization Run, Evaluation Run, and job identifiers. Each Job attempt's stage, counters, warnings, heartbeat, and outcome are stored in `job_attempts` and shown by `GET /v1/jobs/{job_id}` for synchronization Jobs; a running attempt whose heartbeat stops moving is the sign of a dead worker. Collect queue depth, run age, retry counts, retrieval-stage latency, Candidate counts, model tokens, estimated cost, streaming cancellation, and provider errors. Production content fields are redacted by default.

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. A failed Job comes back, and each enabled Source's next synchronization is queued, only through `make worker-relay`, which queues due retries and due synchronizations before it publishes, so run it on a schedule more often than `KNOWLEDGE_SERVICE_SYNCHRONIZATION_INTERVAL_MINUTES` (60 by default). A synchronization pass takes its Source's lease in Redis first; while Redis is down no pass starts, and its Job is retried later. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

and replace it with:

```markdown

## Telemetry

Use OpenTelemetry for traces and metrics and structured JSON logs with shared request, Conversation, Synchronization Run, Evaluation Run, and job identifiers. Each Job attempt's stage, counters, warnings, heartbeat, and outcome are stored in `job_attempts` and shown by `GET /v1/jobs/{job_id}` for synchronization Jobs; a running attempt whose heartbeat stops moving is the sign of a dead worker. Each `make worker-relay` pass first fails the attempts of stale Jobs (silent for five minutes) with `worker_lost` and of orphaned ones (published 30 minutes ago and never taken up) with `delivery_lost`; a Job that failed with no retry planned is listed by `GET /v1/job-decisions` until an administrator retries or cancels it. Collect queue depth, run age, retry counts, retrieval-stage latency, Candidate counts, model tokens, estimated cost, streaming cancellation, and provider errors. Production content fields are redacted by default.

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. A failed Job comes back, and each enabled Source's next synchronization is queued, only through `make worker-relay`, which recovers stale and orphaned Jobs, then queues due retries and due synchronizations, before it publishes, so run it on a schedule more often than `KNOWLEDGE_SERVICE_SYNCHRONIZATION_INTERVAL_MINUTES` (60 by default). A synchronization pass takes its Source's lease in Redis first; while Redis is down no pass starts, and its Job is retried later. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

**Action 3:** in the security model, give administrators the new duty:

In `docs/security.md`, find:

```markdown
- **User** asks questions and manages their retained Conversations.
- **Content manager** configures Sources and starts Synchronization Runs.
- **Evaluator** manages approved Evaluation Datasets and runs paid evaluation.
- **Administrator** manages policy and operational configuration.

Application Roles never imply Document access.
```

and replace it with:

```markdown
- **User** asks questions and manages their retained Conversations.
- **Content manager** configures Sources and starts Synchronization Runs.
- **Evaluator** manages approved Evaluation Datasets and runs paid evaluation.
- **Administrator** manages policy and operational configuration, and retries or cancels Jobs that failed for good.

Application Roles never imply Document access.
```

**Action 4:** in the threat model, add the entry point before the health checks:

In `docs/threat-model.md`, find:

```markdown
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: needs a bearer token and the `user` role, then sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `DELETE /v1/conversations/{conversation_id}` | Any client that reaches the API | Deletes the caller's own Conversation. Needs a bearer token and the `manage_own_conversations` operation (the `user` role), both checked before the ID is parsed; the owner is the token's User, and only the canonical spelling of an ID is accepted; the answer is 204 whether the Conversation was deleted, missing, or another User's |
| `GET /v1/jobs/{job_id}` | Any client that reaches the API | Shows one synchronization Job and its newest attempt's progress. Needs a bearer token and the `start_synchronization_runs` operation (the `content-manager` role), both checked before the ID is parsed; a missing Job and a Job of another kind give the same 404; no error text, only codes |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Redis, `127.0.0.1:6380` | Developers and local workers | Bound to 127.0.0.1, with no password: anyone on this machine can read or write its queues, so a worker checks every message argument again. It never evicts a key and keeps nothing across a restart |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors. A queue message, JSON and never pickle, carries only the harmless `ping` token or one canonical Job ID; the worker reads what to do from PostgreSQL. A stored Job is committed before any message names it, and a Job that is no longer queued is not run again. A Job whose message was sent but then lost (a Redis restart, a message dead-lettered before the Job was claimed, or one dropped by the one-day age limit) stays queued and published until M08-T08 |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
| OpenAI-compatible adapter, Open WebUI | Users | M09 |
| MCP server and agent tools | Agents | M11 |
```

and replace it with:

```markdown
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: needs a bearer token and the `user` role, then sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `DELETE /v1/conversations/{conversation_id}` | Any client that reaches the API | Deletes the caller's own Conversation. Needs a bearer token and the `manage_own_conversations` operation (the `user` role), both checked before the ID is parsed; the owner is the token's User, and only the canonical spelling of an ID is accepted; the answer is 204 whether the Conversation was deleted, missing, or another User's |
| `GET /v1/jobs/{job_id}` | Any client that reaches the API | Shows one synchronization Job and its newest attempt's progress. Needs a bearer token and the `start_synchronization_runs` operation (the `content-manager` role), both checked before the ID is parsed; a missing Job and a Job of another kind give the same 404; no error text, only codes |
| `GET /v1/job-decisions`, `POST /v1/jobs/{job_id}/retry`, `POST /v1/jobs/{job_id}/cancel` | Any client that reaches the API | Lists Jobs of every kind that failed for good, with their kind, target ID, and error code, and retries or cancels one. Needs a bearer token and the `recover_jobs` operation (the `administrator` role), both checked before the ID is parsed; each decision is an audit event with the operator as actor; no error text, only codes |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Redis, `127.0.0.1:6380` | Developers and local workers | Bound to 127.0.0.1, with no password: anyone on this machine can read or write its queues, so a worker checks every message argument again. It never evicts a key and keeps nothing across a restart |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors. A queue message, JSON and never pickle, carries only the harmless `ping` token or one canonical Job ID; the worker reads what to do from PostgreSQL. A stored Job is committed before any message names it, and a Job that is no longer queued is not run again. A Job whose message was sent but then lost (a Redis restart, a message dead-lettered before the Job was claimed, or one dropped by the one-day age limit) stays queued and published until recovery, 30 minutes after it was published, counts it as a lost attempt |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
| OpenAI-compatible adapter, Open WebUI | Users | M09 |
| MCP server and agent tools | Agents | M11 |
```

and update the rows that waited for this lesson, adding the new abuse case at the end:

In `docs/threat-model.md`, find:

```markdown
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one; whatever the model then writes passes the Answer's content checks | None |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |
| Two synchronization passes of one Source run at once: double Source calls, and an older pass finishing late re-grants a removed reader, rolls content back, or tombstones what a newer pass saw | `synchronize_source_exclusively` holds the Source's Redis lease, with an owner token, renewed after every page, so a second pass gets `SourceBusy` before reading; a crashed holder's lease expires in two minutes. The Source's fence in PostgreSQL decides who may write: every pass claims it with its observation time, and every narrowing, reconciliation, and tombstone checks it under a share lock on the Source's row, so after a newer pass claims it nothing of an older pass lands; an observation more than five minutes in the future is refused | Writes an older pass committed before the newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way; a pass observed up to five minutes in the future holds the fence until then |
| A stale or replayed worker rewrites a Job's progress, or a bad clock blocks it | A report shares the Job's row lock only while the Job runs that attempt, counters and warnings never go down and the heartbeat never goes back (checked again in the `UPDATE`), a report far ahead of the database's clock is refused, each attempt has its own row, and ending an attempt the Job has moved past is refused; ending an attempt never fails on what a report stored | No production handler reports progress until M08-T10; a Job whose worker died stays running with its last heartbeat until M08-T08; a worker more than five minutes ahead of the database cannot report |
| A scheduled synchronization Job carries a Source's location or credentials through Redis, or the scheduler floods the queue | A scheduled Job stores only the Source's ID, and its handler reads the configuration and credentials reference from PostgreSQL when it runs; a Source with an open Job is skipped, a missed schedule queues one Job, and a pass queues at most 1,000 | Disabling a Source does not cancel a Job already queued for it; a Source whose synchronization Job is stuck open is not scheduled again until M08-T08 |

### Retrieval
```

and replace it with:

```markdown
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one; whatever the model then writes passes the Answer's content checks | None |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued until recovery counts it as a lost delivery |
| Two synchronization passes of one Source run at once: double Source calls, and an older pass finishing late re-grants a removed reader, rolls content back, or tombstones what a newer pass saw | `synchronize_source_exclusively` holds the Source's Redis lease, with an owner token, renewed after every page, so a second pass gets `SourceBusy` before reading; a crashed holder's lease expires in two minutes. The Source's fence in PostgreSQL decides who may write: every pass claims it with its observation time, and every narrowing, reconciliation, and tombstone checks it under a share lock on the Source's row, so after a newer pass claims it nothing of an older pass lands; an observation more than five minutes in the future is refused | Writes an older pass committed before the newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way; a pass observed up to five minutes in the future holds the fence until then |
| A stale or replayed worker rewrites a Job's progress, or a bad clock blocks it | A report shares the Job's row lock only while the Job runs that attempt, counters and warnings never go down and the heartbeat never goes back (checked again in the `UPDATE`), a report far ahead of the database's clock is refused, each attempt has its own row, and ending an attempt the Job has moved past is refused; ending an attempt never fails on what a report stored | No production handler reports progress until M08-T10; a Job whose worker died stays running with its last heartbeat until recovery finds it silent for five minutes; a worker more than five minutes ahead of the database cannot report |
| A scheduled synchronization Job carries a Source's location or credentials through Redis, or the scheduler floods the queue | A scheduled Job stores only the Source's ID, and its handler reads the configuration and credentials reference from PostgreSQL when it runs; a Source with an open Job is skipped, a missed schedule queues one Job, and a pass queues at most 1,000 | Disabling a Source does not cancel a Job already queued for it; a Source whose synchronization Job is stuck open is not scheduled again until recovery fails its attempt |
| A live worker is taken for a dead one, or an operator's decision races a worker or another operator | Recovery judges a Job again under its row lock and skips a row a worker holds, so a report or claim in progress keeps it alive; a worker that wakes after recovery cannot report or end the attempt; a cancel refuses a running Job; a retry is refused while another Job for the same work is open, enforced by the open-intent index; each decision is audited | A worker silent for five minutes between reports is taken for dead and its work may run twice; a Job claimed without an attempt row is judged by its worker's start time; a message that waits in the queue longer than 30 minutes costs an attempt, so a long backlog can exhaust healthy Jobs; a cancelled purge is queued again by the next pass while its Document's Chunks remain; a worker that keeps sending refused reports stays alive and its Job stays running, and a running Job cannot be cancelled, so its process must be stopped; a pass can be filled by candidates it cannot recover, such as rows held locked |

### Retrieval
```

**Check:** `make docs-check` exits 0.

### Step 8 — No coding in this step: gates, evidence, and progress

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

**Action 2:** finish the evidence file: the gate results and the Step 6 count under "Verification", the review findings and known limits under "Review", the design under "Design" and "Decision", and your answers to the reflection questions below. Then mark the task completed in `docs/curriculum/PROGRESS.md` and commit with `m08-t08: recover dead and stuck work`.

**Check:** every gate passes, and `git status` is clean after the commit.

## Reflection questions

- J8 started an hour ago and last reported 40 seconds ago. Which column does recovery read to decide it is alive, and why not `heartbeat_at`?
- Worker A was only paused for six minutes, and recovery failed its attempt. What happens when it wakes up and reports, and what happens to the work it already did?
- A purge's message waited 35 minutes behind a backlog. What did recovery do, and what does the late message do when it finally arrives? Would you rather republish without counting an attempt? What would that cost?
- Which failure can still happen after a caller believes the work succeeded? Think of a worker that finished its writes, then went silent for five minutes before it stored the outcome.
- An administrator's role is removed while they have the decision list open. What happens to their next retry, and where is their earlier decision recorded?
- Which test would fail first if someone later made recovery wait for a locked row instead of skipping it? Which if they deleted the savepoint around the retry's save?

## Completion checklist

- [ ] The evidence file has the invariant, the owners, and your predictions, written before the change.
- [ ] `jobs.py` has `cancelled`, `reopen`, and `cancel`; `job_conditions.py` exists; the Step 2 unit tests pass.
- [ ] Migration `d0e1f2a3b4c5`, `heard_at`, `record_heard`, `persistence/job_recovery.py`, and `job_recovery.py` exist; the relay recovers first; the purge claim never starts before the queue time.
- [ ] `job_decisions.py` and the three endpoints exist, behind `recover_jobs`; the role and route tests pass.
- [ ] `tests/test_job_recovery_integration.py` passes: the drill, orphans, decisions, the relay, the migration, and the review findings.
- [ ] You ran the deliberate break, saw the re-check under the lock fail while the query kept the slow Job alive, and undid it.
- [ ] `docs/architecture.md`, `docs/operations.md`, `docs/security.md`, and `docs/threat-model.md` describe recovery and decisions.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
