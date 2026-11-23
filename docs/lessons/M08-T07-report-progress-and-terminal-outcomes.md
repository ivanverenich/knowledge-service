# M08-T07 — Report progress and terminal outcomes

Task: [M08-T07 — Report progress and terminal outcomes](../curriculum/milestones/M08-jobs-and-confluence/M08-T07-report-progress-and-terminal-outcomes.md)

## What you will do

This lesson makes a running Job tell the world how far it got. Each attempt of a Job gets its own row in PostgreSQL: what it is doing now, how many items it has handled, what warnings it met, when it last spoke, and, at the end, how it ended. A content manager reads it at `GET /v1/jobs/{job_id}`.

A concrete example. Handbook's synchronization Job J7 runs on worker A, which reports after every page:

| Time | Worker A | What `GET /v1/jobs/J7` shows |
|---|---|---|
| 10:00 | claims J7, attempt 1 begins | `running`, attempt 1, stage `starting`, 0 processed |
| 10:02 | reports: reading, 100 processed | stage `reading`, 100 processed, heartbeat 10:02 |
| 10:05 | reports: writing, 120 processed, 2 quarantined | stage `writing`, 120 processed, 2 skipped, warning `quarantined_items`, heartbeat 10:05 |
| 10:06 | the process is killed | the same as at 10:05: the report was already committed |
| 10:30 | | still heartbeat 10:05: the sign that nobody is working on it |

| What the task asks | Where it is done |
|---|---|
| Persist monotonic counters, stage, heartbeat, warnings, and a typed terminal outcome | Steps 2 and 3: `JobProgress` and the `job_attempts` table |
| Without coupling the domain to Dramatiq messages | Block 1: a handler calls `report_progress`; no message is involved |
| API-visible progress survives worker restart | Step 4: `GET /v1/jobs/{job_id}`; Step 5: `test_progress_survives_the_worker_that_reported_it` |
| Rejects backward transitions | Blocks 2 and 3 |
| Replay or cancellation behaviour explicit | Block 2 (a replayed report changes nothing) and block 7 (cancellation) |
| Update only affected documentation | Step 7: architecture, operations, threat model |

This lesson includes coding: Steps 2 to 5 write code, a migration, and tests. Steps 1, 6, 7, and 8 need no code, although Step 7 edits three documents.

Terms used in this lesson:

- **Attempt:** one try at running a Job, numbered from 1. A Job that failed and was retried has two attempts.
- **Progress report:** one message from a running attempt to the database: its stage and its totals so far.
- **Heartbeat:** the time of the latest report. A heartbeat that stops moving means the worker stopped.
- **Monotonic:** only goes one way. A counter of processed items may grow or stay, never shrink.
- **Outcome:** how an attempt ended, one of five fixed values, such as `succeeded` or `retry_scheduled`.
- **Stale worker:** a worker still running an attempt that the Job has already left behind.

## Before you begin

This section explains every choice the code rests on, step by step. Each block ends with what this lesson changes.

### 1. Where progress lives: one committed row per attempt

**The situation.** Worker A is on attempt 1 of J7. At 10:05 it has processed 120 pages. At 10:06 its process is killed.

| Where worker A keeps "120 processed" | What anyone sees at 10:10 |
|---|---|
| in its own memory | nothing: it died with the process |
| in a queue message | nothing useful: Redis messages are not the record (ADR 0011), and a message is gone once taken |
| on the `jobs` row | 120, until attempt 2 starts and overwrites it: attempt 1's evidence is lost |
| in its own `job_attempts` row, committed per report | 120, heartbeat 10:05, and still there after attempt 2 starts |

**The hidden fact.** A report is visible to others only once its transaction commits. The purge handler does its whole attempt in one transaction, so nobody sees it "running". A long synchronization cannot work that way: each report is its own short transaction, so it is visible at once and survives the process.

**What changes.** The `job_attempts` table holds one row per `(job_id, attempt)`. A handler stores the row with its claim (`begin_attempt`), sends totals with `report_progress`, and ends the attempt with `finish_attempt` in the same transaction that stores the finished Job. None of these takes a message, an actor, or anything from Dramatiq.

### 2. Nothing goes back: counters, warnings, heartbeat

**The situation.** Worker A sends two reports quickly, from two coroutines, or retries one after a slow network. "processed 60" commits first; "processed 50" arrives second.

| Moment | Report "60" | Report "50" | Stored |
|---|---|---|---|
| 1 | locks the attempt row, writes 60, commits | waits for the lock | 60 |
| 2 | | gets the lock, reads 60, 50 < 60: refused with `ProgressWentBackwards` | 60 |

**The hidden fact.** Without a lock, both reports read the old value and the last one to commit wins, even if it is older. `SELECT ... FOR UPDATE` on the attempt row makes the second report wait and then read what the first one committed. Two layers then check it:

| Layer | Check |
|---|---|
| the domain, `JobProgress.advance` | every counter at least the stored one, no warning removed, heartbeat not earlier |
| the SQL, `_save` | the `UPDATE` itself has `WHERE processed <= :new AND changed <= :new AND skipped <= :new AND heartbeat_at <= :new AND warnings <@ :new`; if no row matches, it raises |

**Totals, not increments.** A report says "120 so far", never "+20". A replayed "+20" would count twice; a replayed "120" changes nothing, because equal values are allowed.

| Was (increments) | Became (totals) |
|---|---|
| `UPDATE ... SET processed = processed + 20` | `UPDATE ... SET processed = 120 WHERE processed <= 120 ...` |
| replay: 140, wrong | replay: 120, same |

**The stage may go back.** A synchronization reads a page, writes it, reads the next one. So `stage` is only "what it is doing now", not an ordered state.

**What changes.** `JobProgress.advance` and the `UPDATE` in `persistence.job_attempts` refuse anything that goes back. The analogy: a car's odometer. It may stand still, it never runs backwards, and someone who winds it back is caught both by the dial and by the service book.

### 3. A stale worker cannot write

**The situation.** Attempt 1 of J7 loses its database connection, is recorded as failed with a retry, and attempt 2 starts on worker B. Then worker A, still alive, sends one more report for attempt 1.

| Moment | Worker A (attempt 1) | Worker B (attempt 2) | `jobs` row |
|---|---|---|---|
| 10:05 | reports 120 | | `running`, attempt 1 |
| 10:06 | connection lost; attempt recorded as failed | | `failed`, retry at 10:07 |
| 10:07 | | claims J7, attempt 2 begins | `running`, attempt 2 |
| 10:08 | reports 130 for attempt 1: **refused**, `AttemptNotCurrent` | | unchanged |

**The hidden fact.** Every report first takes a share lock on the Job's row, and only while the Job is running that very attempt (`lock_running_job`: `SELECT ... WHERE status = 'running' FOR SHARE`). A share lock lets other reports in but keeps writers out, so the Job cannot finish or move to attempt 2 in the middle of a report. Locking only running rows matters: the relay queues due retries with `FOR UPDATE SKIP LOCKED`, which silently passes over any locked row, so a stale worker locking a failed Job would hold its retry back.

The same rule covers finishing: `finish_attempt` refuses an attempt when a newer attempt's row exists, so a stale worker's whole transaction rolls back, including the old Job state it tried to save.

**What changes.** `advance_attempt` refuses an attempt the Job is not running; `finish_attempt` refuses an attempt the Job has moved past; each attempt keeps its own row.

### 4. A typed outcome, and a finish that never fails

**The situation.** An operator wants to know how attempt 1 ended without decoding error codes.

| The Job when the attempt ended | Outcome |
|---|---|
| `succeeded`, no warnings | `succeeded` |
| `succeeded`, with warnings | `succeeded_with_warnings` |
| `failed`, error `superseded` | `superseded` |
| `failed`, with a `retry_at` | `retry_scheduled` |
| `failed`, no retry | `failed` |

`outcome_of` works this out from the finished Job, so the outcome always agrees with it.

**The hidden fact.** Finishing must never fail because of what a report stored, or the Job stays `running` forever. Two cases the review found:

| Case | First version | Now |
|---|---|---|
| a report's clock ran 10 seconds ahead of the finisher's | "cannot end before its heartbeat": the whole finish rolled back | the attempt ends at the Job's finish time; the heartbeat is pulled back to it |
| the final counters are lower than a report that raced in | "a counter would go down": rolled back | the higher value of each counter is kept |

A report stamped more than five minutes ahead of the database's clock is refused (`ReportFromTheFuture`), so one bad clock cannot set a heartbeat in the year 9999 and refuse every honest report after it.

### 5. Who can read it

**The situation.** `GET /v1/jobs/{job_id}` could show any Job. But a `purge_conversation` Job's target is a Conversation's ID.

| Caller and Job | Answer |
|---|---|
| no token, or a role without `start_synchronization_runs` | 401 or 403, before the ID is even parsed |
| content manager, a synchronization Job | 200 with the Job and its newest attempt |
| content manager, a purge Job | 404 `job_not_found` |
| content manager, an ID that does not exist | 404 `job_not_found`, the same answer |

**The hidden fact.** Two reads, of the Job and then of its attempt, can see two different moments: a Job that finishes between them shows as `running` with an ended attempt. `DatabaseJobReports.load` runs its transaction as `REPEATABLE READ`, so both reads see one snapshot.

### 6. Review findings

An adversarial review attacked this code three times on real PostgreSQL. Each finding below was fixed, with a test that fails without the fix:

- a reporter's clock ahead of the finisher's, or lower final counters, stopped the Job from ever finishing (block 4);
- a report stamped far in the future refused every honest report after it (block 4);
- a refused report locked a failed Job and held back its retry (block 3);
- the API read the Job and its attempt from two moments (block 5);
- wrong types (a stage as a plain string, counters as a dict) crashed with an untyped error; they now raise `InvalidJob`;
- beginning an attempt twice raised a raw database error; it now raises `AttemptAlreadyBegun`;
- the endpoint trusted the store to filter by kind; it checks again;
- a stale worker could end attempt 1 after attempt 2 began, and the Job could then never run again (block 3);
- an attempt's end time could disagree with its Job's (block 4);
- reading an attempt under a non-UTC session time zone failed; times are read back in UTC.

### 7. Known limits

- No production handler reports progress yet: the synchronization handler arrives in M08-T10, and it must call `begin_attempt` in its claim transaction, `report_progress` per page, and `finish_attempt` with the finished Job. Today only the purge records attempts, and the endpoint hides purge Jobs.
- A Job whose worker died, or whose attempt was cancelled, stays `running` with its last heartbeat until M08-T08 finds it.
- A worker whose clock is more than five minutes ahead of the database cannot report.
- `save_job` has no status guard (from before this lesson): a stale worker that saves the same failure again after its Job was queued again sets it back to `failed` until the next relay pass. The M08-T10 handler must check its claim before it stores a finish.
- Kept counters can mix two reports: a stored (10 processed, 0 changed) and a final (5, 5) end as (10, 5).
- Reading `jobs` rows under a non-UTC session time zone still fails (from before this lesson).

## Step-by-step instructions

Start PostgreSQL before you begin. Run every command from the repository root. For integration checks, set the test database URL in your terminal:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

Run only one integration command against this database at a time.

### Step 1 — No coding in this step: write the invariant and your predictions

Before changing anything, write down what must be true at the end and what you expect to see. You check the predictions in Step 6 and Step 8.

Create `docs/evidence/M08-T07-report-progress-and-terminal-outcomes.md`:

```markdown
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

## Predictions

- What happens to a report of 50 processed that arrives after 60 was stored:
- What the API shows after the worker that reported 120 processed is killed:
- What attempt 1's worker gets when it reports after attempt 2 began:
- How many tests fail if the domain stops checking counters, and whether the database still refuses a lower counter:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: the progress of one attempt

This step adds the domain value for one attempt, with every rule about what may change.

**In plain words:** this file describes one attempt as a plain value: its stage, three counters, warnings, heartbeat, and outcome. Asking it to take a newer report gives a new value, or refuses when anything would go back. Ending it picks the outcome from the finished Job. Nothing here touches the database or the queue.

| Piece | What it does |
|---|---|
| `JobStage` | `starting`, `reading`, `writing`, `cleaning_up`; may repeat |
| `JobWarning` | `unresolved_readers`, `quarantined_items`; codes, never text |
| `AttemptOutcome` | the five outcomes from block 4 |
| `JobCounters` | `processed`, `changed`, `skipped`, each a whole number from 0 to the bigint limit; `merged` keeps the higher of each |
| `attempt_number` | the attempt a running or finished Job is on, from `Job.attempt` (which counts failed attempts) |
| `outcome_of` | block 4's table |
| `JobProgress.begin` / `advance` / `finish` | block 2's and block 4's rules |


**Action 1:** create the module.

Create `src/knowledge_service/job_progress.py`:

```python
"""How far one attempt of a Job got, and how it ended.

Each attempt of a Job has its own progress: a stage, counters that only
grow, the warnings it met, a heartbeat, and, once it ends, a typed outcome.
It is stored in PostgreSQL, so it outlives the worker that wrote it, and a
later attempt never overwrites an earlier one. Nothing here knows about
queue messages: a handler reports progress, the database keeps it.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from knowledge_service.identifiers import JobId
from knowledge_service.job_failures import JobFailure
from knowledge_service.jobs import InvalidJob, Job, JobStatus

# The largest values a PostgreSQL bigint and integer hold.
MAX_COUNT: Final = 2**63 - 1
MAX_ATTEMPT: Final = 2**31 - 1


class ProgressWentBackwards(InvalidJob):
    """A report would make a counter, a warning, or the heartbeat go back."""


class ReportFromTheFuture(InvalidJob):
    """A report's time is far ahead of the database's clock."""


class AttemptFinished(InvalidJob):
    """The attempt already ended, so it takes no more progress."""


class JobStage(StrEnum):
    """What an attempt is doing now. Stages may repeat, page after page."""

    STARTING = "starting"
    READING = "reading"
    WRITING = "writing"
    CLEANING_UP = "cleaning_up"


class JobWarning(StrEnum):
    """Something an attempt met that did not stop it."""

    # A Source named readers the directory does not know.
    UNRESOLVED_READERS = "unresolved_readers"
    # An item could not be used and was set aside.
    QUARANTINED_ITEMS = "quarantined_items"


class AttemptOutcome(StrEnum):
    """How one attempt ended."""

    SUCCEEDED = "succeeded"
    SUCCEEDED_WITH_WARNINGS = "succeeded_with_warnings"
    # It failed, and the Job will be tried again at its retry time.
    RETRY_SCHEDULED = "retry_scheduled"
    # A newer run of the same work started; there is nothing to redo.
    SUPERSEDED = "superseded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class JobCounters:
    """Running totals of one attempt; each one only grows."""

    processed: int = 0
    changed: int = 0
    skipped: int = 0

    def __post_init__(self) -> None:
        for name in ("processed", "changed", "skipped"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= MAX_COUNT:
                raise InvalidJob(f"{name} must be an integer from 0 to {MAX_COUNT}")

    def merged(self, other: JobCounters) -> JobCounters:
        """The higher of each counter in the two."""
        return JobCounters(
            processed=max(self.processed, other.processed),
            changed=max(self.changed, other.changed),
            skipped=max(self.skipped, other.skipped),
        )

    def covers(self, earlier: JobCounters) -> bool:
        """Whether no counter is below its value in `earlier`."""
        return (
            self.processed >= earlier.processed
            and self.changed >= earlier.changed
            and self.skipped >= earlier.skipped
        )


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidJob(f"{field_name} must be a UTC-aware timestamp")


def _require_report(stage: object, counters: object) -> None:
    if type(stage) is not JobStage:
        raise InvalidJob("stage must be a JobStage")
    if type(counters) is not JobCounters:
        raise InvalidJob("counters must be JobCounters")


def attempt_number(job: Job) -> int:
    """The number, from 1, of the attempt a started or finished Job is on.

    `Job.attempt` counts failed attempts, so a failed Job's last attempt is
    that count, and a running or succeeded Job's is the next one.
    """
    if job.status is JobStatus.QUEUED:
        raise InvalidJob("a queued job has no attempt under way")
    if job.status is JobStatus.FAILED:
        return job.attempt
    return job.attempt + 1


def outcome_of(job: Job, warnings: Iterable[JobWarning] = ()) -> AttemptOutcome:
    """The outcome of the attempt a finished Job just ended."""
    if job.status is JobStatus.SUCCEEDED:
        if any(True for _ in warnings):
            return AttemptOutcome.SUCCEEDED_WITH_WARNINGS
        return AttemptOutcome.SUCCEEDED
    if job.status is not JobStatus.FAILED:
        raise InvalidJob("only a finished job has an outcome")
    if job.last_error_code == JobFailure.SUPERSEDED.value:
        return AttemptOutcome.SUPERSEDED
    if job.retry_at is not None:
        return AttemptOutcome.RETRY_SCHEDULED
    return AttemptOutcome.FAILED


@dataclass(frozen=True, slots=True)
class JobProgress:
    """Where one attempt of a Job is, or how it ended."""

    job_id: JobId
    attempt: int
    stage: JobStage
    counters: JobCounters
    warnings: frozenset[JobWarning]
    started_at: datetime
    heartbeat_at: datetime
    outcome: AttemptOutcome | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        if type(self.attempt) is not int or not 1 <= self.attempt <= MAX_ATTEMPT:
            raise InvalidJob(f"attempt must be an integer from 1 to {MAX_ATTEMPT}")
        _require_report(self.stage, self.counters)
        if type(self.warnings) is not frozenset or any(
            type(warning) is not JobWarning for warning in self.warnings
        ):
            raise InvalidJob("warnings must be a frozenset of JobWarning")
        if self.outcome is not None and type(self.outcome) is not AttemptOutcome:
            raise InvalidJob("outcome must be an AttemptOutcome")
        _require_utc(self.started_at, "started_at")
        _require_utc(self.heartbeat_at, "heartbeat_at")
        if self.heartbeat_at < self.started_at:
            raise InvalidJob("heartbeat_at must not be earlier than started_at")
        if (self.outcome is None) != (self.finished_at is None):
            raise InvalidJob("only a finished attempt carries an outcome")
        if self.finished_at is not None:
            _require_utc(self.finished_at, "finished_at")
            if self.finished_at < self.heartbeat_at:
                raise InvalidJob("finished_at must not be earlier than heartbeat_at")

    @classmethod
    def begin(cls, job: Job) -> JobProgress:
        """The progress of the attempt a Job has just started."""
        if job.status is not JobStatus.RUNNING or job.started_at is None:
            raise InvalidJob("only a running job begins an attempt")
        return cls(
            job_id=job.job_id,
            attempt=attempt_number(job),
            stage=JobStage.STARTING,
            counters=JobCounters(),
            warnings=frozenset(),
            started_at=job.started_at,
            heartbeat_at=job.started_at,
        )

    def advance(
        self,
        *,
        stage: JobStage,
        counters: JobCounters,
        warnings: Iterable[JobWarning] = (),
        at: datetime,
    ) -> JobProgress:
        """Record a newer report: counters, warnings, and time never go back.

        Reporting the same values again changes nothing, so a replayed report
        is harmless.
        """
        if self.outcome is not None:
            raise AttemptFinished("the attempt already ended")
        _require_report(stage, counters)
        _require_utc(at, "at")
        try:
            seen = frozenset(warnings)
        except TypeError:
            raise InvalidJob("warnings must be JobWarning values") from None
        if any(type(warning) is not JobWarning for warning in seen):
            raise InvalidJob("warnings must be JobWarning values")
        if not counters.covers(self.counters):
            raise ProgressWentBackwards("a counter would go down")
        if not seen >= self.warnings:
            raise ProgressWentBackwards("a warning would disappear")
        if at < self.heartbeat_at:
            raise ProgressWentBackwards("the heartbeat would go back in time")
        return replace(
            self, stage=stage, counters=counters, warnings=seen, heartbeat_at=at
        )

    def finish(self, *, outcome: AttemptOutcome, at: datetime) -> JobProgress:
        """End the attempt. Ending it again the same way changes nothing.

        Ending never fails on time: the attempt ends at `at`, its Job's own
        finish time, and a heartbeat later than that, from a reporter whose
        clock runs ahead, is pulled back to it, so the outcome is always
        recorded and agrees with the Job.
        """
        if type(outcome) is not AttemptOutcome:
            raise InvalidJob("outcome must be an AttemptOutcome")
        if self.outcome is not None:
            if self.outcome is outcome:
                return self
            raise AttemptFinished("the attempt already ended another way")
        _require_utc(at, "at")
        if at < self.started_at:
            raise InvalidJob("an attempt cannot end before it started")
        heartbeat = min(self.heartbeat_at, at)
        return replace(self, outcome=outcome, finished_at=at, heartbeat_at=heartbeat)
```

**Action 2:** unit tests for every rule.

Create `tests/test_job_progress.py`:

```python
"""Tests for the progress of one Job attempt: nothing in it goes back."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from knowledge_service.identifiers import JobId
from knowledge_service.job_failures import JobFailure
from knowledge_service.job_progress import (
    MAX_COUNT,
    AttemptFinished,
    AttemptOutcome,
    JobCounters,
    JobProgress,
    JobStage,
    JobWarning,
    ProgressWentBackwards,
    attempt_number,
    outcome_of,
)
from knowledge_service.jobs import InvalidJob, Job, JobKind

QUEUED_AT = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
STARTED_AT = QUEUED_AT + timedelta(seconds=1)
LATER = STARTED_AT + timedelta(seconds=30)


def running_job(*, max_attempts: int = 3) -> Job:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=uuid4(),
        queued_at=QUEUED_AT,
        max_attempts=max_attempts,
    )
    return job.start(started_at=STARTED_AT)


def begun() -> JobProgress:
    return JobProgress.begin(running_job())


def test_an_attempt_begins_at_its_start_with_nothing_counted() -> None:
    progress = begun()

    assert progress.attempt == 1
    assert progress.stage is JobStage.STARTING
    assert progress.counters == JobCounters()
    assert progress.warnings == frozenset()
    assert progress.heartbeat_at == STARTED_AT
    assert progress.outcome is None


def test_only_a_running_job_begins_an_attempt() -> None:
    queued = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=uuid4(),
        queued_at=QUEUED_AT,
    )

    with pytest.raises(InvalidJob):
        JobProgress.begin(queued)


def test_a_report_moves_the_stage_counters_warnings_and_heartbeat() -> None:
    advanced = begun().advance(
        stage=JobStage.WRITING,
        counters=JobCounters(processed=10, changed=3, skipped=1),
        warnings=[JobWarning.QUARANTINED_ITEMS],
        at=LATER,
    )

    assert advanced.stage is JobStage.WRITING
    assert advanced.counters == JobCounters(processed=10, changed=3, skipped=1)
    assert advanced.warnings == {JobWarning.QUARANTINED_ITEMS}
    assert advanced.heartbeat_at == LATER


def test_the_same_report_twice_changes_nothing() -> None:
    counters = JobCounters(processed=4)
    once = begun().advance(stage=JobStage.READING, counters=counters, at=LATER)

    twice = once.advance(stage=JobStage.READING, counters=counters, at=LATER)

    assert twice == once


def test_the_stage_may_go_back_page_after_page() -> None:
    writing = begun().advance(
        stage=JobStage.WRITING, counters=JobCounters(processed=1), at=LATER
    )

    reading = writing.advance(
        stage=JobStage.READING, counters=JobCounters(processed=1), at=LATER
    )

    assert reading.stage is JobStage.READING


@pytest.mark.parametrize("name", ["processed", "changed", "skipped"])
def test_a_counter_never_goes_down(name: str) -> None:
    ahead = begun().advance(
        stage=JobStage.READING,
        counters=JobCounters(processed=5, changed=5, skipped=5),
        at=LATER,
    )
    lower = {"processed": 5, "changed": 5, "skipped": 5} | {name: 4}

    with pytest.raises(ProgressWentBackwards):
        ahead.advance(stage=JobStage.READING, counters=JobCounters(**lower), at=LATER)


def test_a_warning_never_disappears() -> None:
    warned = begun().advance(
        stage=JobStage.READING,
        counters=JobCounters(),
        warnings=[JobWarning.UNRESOLVED_READERS],
        at=LATER,
    )

    with pytest.raises(ProgressWentBackwards):
        warned.advance(stage=JobStage.READING, counters=JobCounters(), at=LATER)


def test_the_heartbeat_never_goes_back_in_time() -> None:
    progress = begun().advance(stage=JobStage.READING, counters=JobCounters(), at=LATER)

    with pytest.raises(ProgressWentBackwards):
        progress.advance(
            stage=JobStage.READING,
            counters=JobCounters(),
            at=LATER - timedelta(microseconds=1),
        )


def test_an_ended_attempt_takes_no_more_reports() -> None:
    ended = begun().finish(outcome=AttemptOutcome.SUCCEEDED, at=LATER)

    with pytest.raises(AttemptFinished):
        ended.advance(stage=JobStage.READING, counters=JobCounters(), at=LATER)


def test_ending_twice_the_same_way_changes_nothing() -> None:
    ended = begun().finish(outcome=AttemptOutcome.FAILED, at=LATER)

    assert ended.finish(outcome=AttemptOutcome.FAILED, at=LATER) is ended


def test_an_ended_attempt_cannot_end_another_way() -> None:
    ended = begun().finish(outcome=AttemptOutcome.FAILED, at=LATER)

    with pytest.raises(AttemptFinished):
        ended.finish(outcome=AttemptOutcome.SUCCEEDED, at=LATER)


@pytest.mark.parametrize("value", [-1, MAX_COUNT + 1, True, 1.0, "1"])
def test_a_counter_is_a_bounded_whole_number(value: object) -> None:
    with pytest.raises(InvalidJob):
        JobCounters(processed=value)  # type: ignore[arg-type]


def test_a_report_time_must_be_utc() -> None:
    kyiv = timezone(timedelta(hours=2))

    with pytest.raises(InvalidJob):
        begun().advance(
            stage=JobStage.READING,
            counters=JobCounters(),
            at=LATER.astimezone(kyiv),
        )


def test_a_warning_must_be_a_known_one() -> None:
    with pytest.raises(InvalidJob):
        JobProgress(
            job_id=JobId.new(),
            attempt=1,
            stage=JobStage.READING,
            counters=JobCounters(),
            warnings=frozenset({"token=secret"}),  # type: ignore[arg-type]
            started_at=STARTED_AT,
            heartbeat_at=STARTED_AT,
        )


def test_the_attempt_number_follows_the_failed_attempts() -> None:
    first = running_job()
    failed = first.fail(
        error_code=JobFailure.SOURCE_UNAVAILABLE.value,
        finished_at=LATER,
        retry_at=LATER + timedelta(minutes=1),
    )
    second = failed.retry(queued_at=LATER + timedelta(minutes=1)).start(
        started_at=LATER + timedelta(minutes=2)
    )

    assert attempt_number(first) == 1
    assert attempt_number(failed) == 1
    assert attempt_number(second) == 2
    assert attempt_number(second.succeed(finished_at=second.started_at)) == 2  # type: ignore[arg-type]


def test_a_queued_job_has_no_attempt_under_way() -> None:
    with pytest.raises(InvalidJob):
        attempt_number(
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=uuid4(),
                queued_at=QUEUED_AT,
            )
        )


def test_the_outcome_of_a_finished_job_is_typed() -> None:
    job = running_job()
    retry_at = LATER + timedelta(minutes=1)

    def failed(code: JobFailure, retry: datetime | None = None) -> Job:
        return job.fail(error_code=code.value, finished_at=LATER, retry_at=retry)

    assert outcome_of(job.succeed(finished_at=LATER)) is AttemptOutcome.SUCCEEDED
    assert (
        outcome_of(job.succeed(finished_at=LATER), [JobWarning.UNRESOLVED_READERS])
        is AttemptOutcome.SUCCEEDED_WITH_WARNINGS
    )
    assert (
        outcome_of(failed(JobFailure.SOURCE_UNAVAILABLE, retry_at))
        is AttemptOutcome.RETRY_SCHEDULED
    )
    assert outcome_of(failed(JobFailure.SUPERSEDED)) is AttemptOutcome.SUPERSEDED
    assert outcome_of(failed(JobFailure.PROGRAMMING_ERROR)) is AttemptOutcome.FAILED


def test_a_running_job_has_no_outcome_yet() -> None:
    with pytest.raises(InvalidJob):
        outcome_of(running_job())


# --- Found by the adversarial review ---------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"stage": "reading"},
        {"counters": {"processed": 1}},
        {"warnings": ["unresolved_readers"]},
        {"outcome": "succeeded", "finished_at": LATER},
        {"attempt": 2**31},
    ],
)
def test_every_field_of_an_attempt_has_its_own_type(change: dict[str, object]) -> None:
    fields: dict[str, object] = {
        "job_id": JobId.new(),
        "attempt": 1,
        "stage": JobStage.READING,
        "counters": JobCounters(),
        "warnings": frozenset(),
        "started_at": STARTED_AT,
        "heartbeat_at": LATER,
    }

    with pytest.raises(InvalidJob):
        JobProgress(**(fields | change))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "report",
    [
        {"stage": "reading", "counters": JobCounters()},
        {"stage": JobStage.READING, "counters": {"processed": 1}},
        {"stage": JobStage.READING, "counters": JobCounters(), "warnings": None},
    ],
)
def test_a_report_of_the_wrong_type_is_a_typed_error(report: dict[str, object]) -> None:
    with pytest.raises(InvalidJob):
        begun().advance(at=LATER, **report)  # type: ignore[arg-type]


def test_an_attempt_ending_before_its_heartbeat_pulls_the_heartbeat_back() -> None:
    progress = begun().advance(stage=JobStage.READING, counters=JobCounters(), at=LATER)

    ended = progress.finish(outcome=AttemptOutcome.SUCCEEDED, at=STARTED_AT)

    assert ended.finished_at == ended.heartbeat_at == STARTED_AT
```

**Check:** `uv run pytest tests/test_job_progress.py -q` reports `33 passed`.

### Step 3 — Coding step: store attempts in PostgreSQL

This step adds the table, the functions that read and write it, and wires the purge to record its attempts.

**In plain words:** a new table keeps one row per attempt, with checks that repeat the domain's rules. One function stores the row when an attempt begins, one records a report under the locks from blocks 2 and 3, and one records the end in the transaction that stores the finished Job. A small workflow module runs a report in its own transaction and reads a Job with its newest attempt for the API. The purge now records each attempt it runs.

| Piece | What it does |
|---|---|
| Migration `c9d0e1f2a3b4` and `job_attempts` | the table, removed with its Job (`ON DELETE CASCADE`) |
| `lock_running_job` | `SELECT ... WHERE status = 'running' FOR SHARE` (block 3) |
| `begin_attempt` | inserts the row; a second insert raises `AttemptAlreadyBegun` |
| `advance_attempt` | the running-attempt check, the five-minute clock check, the row lock, the domain check, the guarded `UPDATE` |
| `finish_attempt` | locks the Job, refuses a moved-past attempt, keeps higher counters, records the outcome |
| `report_progress` / `DatabaseJobReports` | one report per transaction; one snapshot per API read |


**Action 1:** create the migration.

Create `migrations/versions/c9d0e1f2a3b4_job_attempt_progress.py`:

```python
"""job attempt progress

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-11-24 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"


def upgrade() -> None:
    op.create_table(
        "job_attempts",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("processed", sa.BigInteger(), nullable=False),
        sa.Column("changed", sa.BigInteger(), nullable=False),
        sa.Column("skipped", sa.BigInteger(), nullable=False),
        sa.Column("warnings", ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt >= 1", name="ck_job_attempts_attempt"),
        sa.CheckConstraint(
            "stage in ('starting', 'reading', 'writing', 'cleaning_up')",
            name="ck_job_attempts_stage",
        ),
        sa.CheckConstraint(
            "processed >= 0 and changed >= 0 and skipped >= 0",
            name="ck_job_attempts_counters",
        ),
        sa.CheckConstraint(
            "warnings <@ array['unresolved_readers', 'quarantined_items']::varchar[]",
            name="ck_job_attempts_warnings",
        ),
        sa.CheckConstraint(
            "outcome is null or outcome in ('succeeded', 'succeeded_with_warnings',"
            " 'retry_scheduled', 'superseded', 'failed')",
            name="ck_job_attempts_outcome",
        ),
        sa.CheckConstraint(
            "(outcome is null) = (finished_at is null)",
            name="ck_job_attempts_finished_at",
        ),
        sa.CheckConstraint(
            "heartbeat_at >= started_at and (finished_at is null"
            " or finished_at >= heartbeat_at)",
            name="ck_job_attempts_times",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "attempt"),
    )


def downgrade() -> None:
    op.drop_table("job_attempts")
```

**Action 2:** declare the table in the schema. First import `ARRAY`:

In `src/knowledge_service/persistence/schema.py`, find:

```python

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

metadata = sa.MetaData()
```

and replace it with:

```python

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR

metadata = sa.MetaData()
```

then add the table after the `jobs` table:

In `src/knowledge_service/persistence/schema.py`, find:

```python
    ),
)

audit_events = sa.Table(
    "audit_events",
    metadata,
```

and replace it with:

```python
    ),
)

# One row per attempt of a Job: how far it got, and how it ended.
job_attempts = sa.Table(
    "job_attempts",
    metadata,
    sa.Column(
        "job_id",
        sa.Uuid(),
        sa.ForeignKey("jobs.job_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("attempt", sa.Integer(), primary_key=True),
    sa.Column("stage", sa.String(length=16), nullable=False),
    sa.Column("processed", sa.BigInteger(), nullable=False),
    sa.Column("changed", sa.BigInteger(), nullable=False),
    sa.Column("skipped", sa.BigInteger(), nullable=False),
    sa.Column("warnings", ARRAY(sa.String(length=32)), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("outcome", sa.String(length=32), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("attempt >= 1", name="ck_job_attempts_attempt"),
    sa.CheckConstraint(
        "stage in ('starting', 'reading', 'writing', 'cleaning_up')",
        name="ck_job_attempts_stage",
    ),
    sa.CheckConstraint(
        "processed >= 0 and changed >= 0 and skipped >= 0",
        name="ck_job_attempts_counters",
    ),
    sa.CheckConstraint(
        "warnings <@ array['unresolved_readers', 'quarantined_items']::varchar[]",
        name="ck_job_attempts_warnings",
    ),
    sa.CheckConstraint(
        "outcome is null or outcome in ('succeeded', 'succeeded_with_warnings',"
        " 'retry_scheduled', 'superseded', 'failed')",
        name="ck_job_attempts_outcome",
    ),
    sa.CheckConstraint(
        "(outcome is null) = (finished_at is null)",
        name="ck_job_attempts_finished_at",
    ),
    sa.CheckConstraint(
        "heartbeat_at >= started_at and (finished_at is null"
        " or finished_at >= heartbeat_at)",
        name="ck_job_attempts_times",
    ),
)

audit_events = sa.Table(
    "audit_events",
    metadata,
```

**Action 3:** add `lock_running_job` before `save_job`:

In `src/knowledge_service/persistence/jobs.py`, find:

```python
    return _job_from(dict(record))


async def save_job(connection: AsyncConnection, *, job: Job) -> Job:
    """Store the status a Job has reached.
```

and replace it with:

```python
    return _job_from(dict(record))


async def lock_running_job(connection: AsyncConnection, *, job_id: JobId) -> Job | None:
    """Share-lock a running Job's row, or return None if it is not running.

    Only a running row is locked, so a refused caller never holds back the
    relay, whose `SKIP LOCKED` would pass over a locked failed Job.
    """
    statement = (
        sa.select(jobs)
        .where(jobs.c.job_id == job_id.value, jobs.c.status == JobStatus.RUNNING.value)
        .with_for_update(read=True)
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def save_job(connection: AsyncConnection, *, job: Job) -> Job:
    """Store the status a Job has reached.
```

**Action 4:** create the attempt store.

Create `src/knowledge_service/persistence/job_attempts.py`:

```python
"""Store the progress and outcome of each attempt of a Job."""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.identifiers import JobId
from knowledge_service.job_progress import (
    AttemptOutcome,
    JobCounters,
    JobProgress,
    JobStage,
    JobWarning,
    ProgressWentBackwards,
    ReportFromTheFuture,
    attempt_number,
    outcome_of,
)
from knowledge_service.jobs import InvalidJob, Job
from knowledge_service.persistence.jobs import load_job, lock_running_job
from knowledge_service.persistence.schema import job_attempts

# How far ahead of the database's clock a report may be. A heartbeat far in
# the future would refuse every honest report after it.
MAX_REPORT_SKEW: Final = timedelta(minutes=5)


class AttemptNotCurrent(InvalidJob):
    """The Job is not running this attempt, so its report is refused.

    The attempt ended, or the Job moved on to another one, as when a worker
    that was presumed dead wakes up and reports.
    """


class AttemptAlreadyBegun(InvalidJob):
    """The attempt's progress was already stored when it began."""


def _row(progress: JobProgress) -> dict[str, object]:
    return {
        "job_id": progress.job_id.value,
        "attempt": progress.attempt,
        "stage": progress.stage.value,
        "processed": progress.counters.processed,
        "changed": progress.counters.changed,
        "skipped": progress.counters.skipped,
        "warnings": sorted(warning.value for warning in progress.warnings),
        "started_at": progress.started_at,
        "heartbeat_at": progress.heartbeat_at,
        "outcome": None if progress.outcome is None else progress.outcome.value,
        "finished_at": progress.finished_at,
    }


def _progress_from(record: Mapping[str, object]) -> JobProgress:
    try:
        stage = JobStage(cast(str, record["stage"]))
        warnings = frozenset(
            JobWarning(value) for value in cast("list[str]", record["warnings"])
        )
        outcome = record["outcome"]
        finished = cast("datetime | None", record["finished_at"])
        ended = None if outcome is None else AttemptOutcome(cast(str, outcome))
    except ValueError:
        raise InvalidJob("stored attempt holds an unknown value") from None
    return JobProgress(
        job_id=JobId(cast(UUID, record["job_id"])),
        attempt=cast(int, record["attempt"]),
        stage=stage,
        counters=JobCounters(
            processed=cast(int, record["processed"]),
            changed=cast(int, record["changed"]),
            skipped=cast(int, record["skipped"]),
        ),
        warnings=warnings,
        # Read in UTC whatever the session's time zone.
        started_at=cast(datetime, record["started_at"]).astimezone(UTC),
        heartbeat_at=cast(datetime, record["heartbeat_at"]).astimezone(UTC),
        outcome=ended,
        finished_at=None if finished is None else finished.astimezone(UTC),
    )


async def _load(
    connection: AsyncConnection, *, job_id: JobId, attempt: int, for_update: bool
) -> JobProgress | None:
    statement = sa.select(job_attempts).where(
        job_attempts.c.job_id == job_id.value, job_attempts.c.attempt == attempt
    )
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    return None if record is None else _progress_from(dict(record))


async def load_latest_attempt(
    connection: AsyncConnection, *, job_id: JobId
) -> JobProgress | None:
    """The progress of a Job's newest attempt, if it ever started one."""
    statement = (
        sa.select(job_attempts)
        .where(job_attempts.c.job_id == job_id.value)
        .order_by(job_attempts.c.attempt.desc())
        .limit(1)
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    return None if record is None else _progress_from(dict(record))


async def begin_attempt(connection: AsyncConnection, *, job: Job) -> JobProgress:
    """Store the progress of the attempt a running Job has just started.

    Call it in the transaction that stores the claim, so a report never
    finds a running attempt without its row.
    """
    progress = JobProgress.begin(job)
    inserted = await connection.scalar(
        insert(job_attempts)
        .values(_row(progress))
        .on_conflict_do_nothing()
        .returning(job_attempts.c.attempt)
    )
    if inserted is None:
        raise AttemptAlreadyBegun("the attempt's progress is already stored")
    return progress


async def advance_attempt(
    connection: AsyncConnection,
    *,
    job_id: JobId,
    attempt: int,
    stage: JobStage,
    counters: JobCounters,
    warnings: Iterable[JobWarning] = (),
    now: datetime,
) -> JobProgress:
    """Record a report of a running attempt; nothing in it may go back.

    The Job's row is share-locked first, only while it runs, so the Job
    cannot finish or move to another attempt until this report commits; a
    report from an attempt the Job is no longer running is refused with
    `AttemptNotCurrent`, and one stamped more than `MAX_REPORT_SKEW` ahead of
    the database's clock with `ReportFromTheFuture`.
    """
    job = await lock_running_job(connection, job_id=job_id)
    if job is None or attempt_number(job) != attempt:
        raise AttemptNotCurrent("the job is not running this attempt")
    database_now = await connection.scalar(sa.select(sa.func.now()))
    if database_now is None or now > database_now + MAX_REPORT_SKEW:
        raise ReportFromTheFuture("the report is stamped ahead of the database")
    stored = await _load(connection, job_id=job_id, attempt=attempt, for_update=True)
    if stored is None:
        raise AttemptNotCurrent("the attempt was never started")
    advanced = stored.advance(stage=stage, counters=counters, warnings=warnings, at=now)
    await _save(connection, advanced, earlier=stored)
    return advanced


async def finish_attempt(
    connection: AsyncConnection,
    *,
    job: Job,
    counters: JobCounters | None = None,
) -> JobProgress:
    """Record how the attempt a finished Job just ended, with its outcome.

    Call it in the transaction that stores the finished Job. `counters`, if
    given, are the attempt's final totals; a report that raced in with
    higher ones keeps them. An attempt that never stored its start, such as
    one whose transaction was lost, is recorded here. Nothing a report
    stored can stop the outcome from being recorded; ending an attempt
    the Job has moved past raises `AttemptNotCurrent`, which rolls back the
    stale worker's whole transaction.
    """
    if job.finished_at is None or job.started_at is None:
        raise InvalidJob("only a finished job ends an attempt")
    # The Job's row first, then the attempt's, the order a report takes.
    await load_job(connection, job_id=job.job_id, for_update=True)
    attempt = attempt_number(job)
    newer = await connection.scalar(
        sa.select(sa.func.max(job_attempts.c.attempt)).where(
            job_attempts.c.job_id == job.job_id.value
        )
    )
    if newer is not None and newer > attempt:
        # A worker presumed dead ends an attempt its Job has moved past.
        raise AttemptNotCurrent("the job moved on to a later attempt")
    stored = await _load(
        connection, job_id=job.job_id, attempt=attempt, for_update=True
    )
    if stored is None:
        started = JobProgress(
            job_id=job.job_id,
            attempt=attempt,
            stage=JobStage.STARTING,
            counters=JobCounters(),
            warnings=frozenset(),
            started_at=job.started_at,
            heartbeat_at=job.started_at,
        )
        finished = _ended(started, job=job, counters=counters)
        await connection.execute(sa.insert(job_attempts).values(_row(finished)))
        return finished
    finished = _ended(stored, job=job, counters=counters)
    if finished is not stored:
        await _save(connection, finished, earlier=stored, ending=True)
    return finished


def _ended(
    progress: JobProgress, *, job: Job, counters: JobCounters | None
) -> JobProgress:
    finished_at = cast(datetime, job.finished_at)
    if counters is not None and progress.outcome is None:
        progress = replace(progress, counters=progress.counters.merged(counters))
    return progress.finish(outcome=outcome_of(job, progress.warnings), at=finished_at)


async def _save(
    connection: AsyncConnection,
    progress: JobProgress,
    *,
    earlier: JobProgress,
    ending: bool = False,
) -> None:
    """Update an unfinished attempt, only if nothing in it would go back.

    The conditions repeat the domain's checks in SQL, so even a writer that
    skipped them cannot move a counter or the heartbeat backwards. Only
    `ending` may pull the heartbeat back, to the Job's finish time.
    """
    columns = job_attempts.c
    conditions = [
        columns.job_id == earlier.job_id.value,
        columns.attempt == earlier.attempt,
        columns.outcome.is_(None),
        columns.processed <= progress.counters.processed,
        columns.changed <= progress.counters.changed,
        columns.skipped <= progress.counters.skipped,
        columns.warnings.contained_by(_row(progress)["warnings"]),
    ]
    if not ending:
        conditions.append(columns.heartbeat_at <= progress.heartbeat_at)
    result = await connection.execute(
        sa.update(job_attempts).where(*conditions).values(_row(progress))
    )
    if result.rowcount != 1:
        raise ProgressWentBackwards("the stored attempt is further along")
```

**Action 5:** create the workflow module.

Create `src/knowledge_service/job_reporting.py`:

```python
"""Report a running Job's progress, and read a Job with its latest attempt.

Each report commits on its own, so the progress is visible, and survives,
while the Job still runs. A handler calls `report_progress`; the worker and
its queue messages are never involved.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId
from knowledge_service.job_progress import (
    JobCounters,
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
    """A Job as stored, and the progress of its newest attempt, if any."""

    job: Job
    latest_attempt: JobProgress | None


async def report_progress(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    attempt: int,
    stage: JobStage,
    counters: JobCounters,
    warnings: Iterable[JobWarning] = (),
    now: datetime,
) -> JobProgress:
    """Store one progress report of a running attempt, in its own transaction.

    `counters` and `warnings` are totals so far, not increments, so sending
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
class DatabaseJobReports:
    """Reads Jobs and their progress from PostgreSQL for the API."""

    runtime: DatabaseRuntime

    async def load(
        self, *, job_id: JobId, kinds: frozenset[JobKind]
    ) -> JobReport | None:
        """The Job and its newest attempt, or None if it is not of `kinds`.

        Both are read from one snapshot, so a Job that finishes meanwhile is
        never shown as running with an ended attempt.
        """
        async with self.runtime.transaction() as connection:
            await connection.execute(
                sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            )
            job = await load_job(connection, job_id=job_id)
            if job is None or job.kind not in kinds:
                return None
            latest = await load_latest_attempt(connection, job_id=job_id)
        return JobReport(job=job, latest_attempt=latest)
```

**Action 6:** make the purge record its attempts. First the imports:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
    RetryPolicy,
    fail_attempt,
)
from knowledge_service.jobs import (
    InvalidJob,
    Job,
```

and replace it with:

```python
    RetryPolicy,
    fail_attempt,
)
from knowledge_service.job_progress import JobCounters
from knowledge_service.jobs import (
    InvalidJob,
    Job,
```

and:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
    load_unpurged_documents,
    upsert_document,
)
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
```

and replace it with:

```python
    load_unpurged_documents,
    upsert_document,
)
from knowledge_service.persistence.job_attempts import finish_attempt
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
```

then remember whether the Chunks were deleted:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
                        document_id=DocumentId(job.target_id),
                        for_update=True,
                    )
                    if (
                        document is not None
                        and document.availability is DocumentAvailability.TOMBSTONED
```

and replace it with:

```python
                        document_id=DocumentId(job.target_id),
                        for_update=True,
                    )
                    purged = False
                    if (
                        document is not None
                        and document.availability is DocumentAvailability.TOMBSTONED
```

record each ending, failed or successful, before the Job is saved:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
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
```

and replace it with:

```python
                        await delete_chunks(
                            connection, document_id=document.document_id
                        )
                        purged = True
            except Exception as error:
                cause = error
                failed = fail_attempt(job, error, now=now, policy=retry_policy)
                await finish_attempt(connection, job=failed)
                return await save_job(connection, job=failed)
            succeeded = job.succeed(finished_at=now)
            # One Document looked at: purged, or skipped as restored or gone.
            await finish_attempt(
                connection,
                job=succeeded,
                counters=JobCounters(
                    processed=1, changed=int(purged), skipped=int(not purged)
                ),
            )
            return await save_job(connection, job=succeeded)
    except InvalidJob:
        raise
    except Exception as error:
```

and the ending of an attempt whose transaction was lost:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
        failed = fail_attempt(
            stored.start(started_at=now), cause, now=now, policy=retry_policy
        )
        return await save_job(connection, job=failed)
```

and replace it with:

```python
        failed = fail_attempt(
            stored.start(started_at=now), cause, now=now, policy=retry_policy
        )
        await finish_attempt(connection, job=failed)
        return await save_job(connection, job=failed)
```

**Check:** `uv run pyright src` reports 0 errors; `uv run pytest tests/test_indexes.py -q` reports `4 passed`; `uv run pytest tests/test_migrations_integration.py -q -m integration` reports `3 passed`; `uv run pytest tests/test_deletions_integration.py tests/test_job_retry_integration.py -q -m integration` reports `27 passed`.

### Step 4 — Coding step: show a synchronization Job over HTTP

This step adds `GET /v1/jobs/{job_id}` and its response shape.

**In plain words:** the endpoint checks who is asking and whether they may start synchronizations, then checks the ID's spelling, then asks the store for the Job. It shows only synchronization Jobs, with their newest attempt; anything else looks exactly like a Job that does not exist.

| Piece | What it does |
|---|---|
| `JobAttemptProgress`, `SynchronizationJobReport` | the response: the Job's status, times, error code, and its newest attempt or `null` |
| `JobReports` | the protocol `create_app` takes; `DatabaseJobReports` is the implementation |
| `read_synchronization_job` | block 5's table |
| `test_app_authentication.py` | lists the route with its operation, adds a fake store, and checks the endpoint never shows another kind |


**Action 1:** in the contracts, import `datetime`:

In `src/knowledge_service/contracts.py`, find:

```python
"""Pydantic models for the knowledge service."""

from uuid import UUID

from pydantic import BaseModel, Field
```

and replace it with:

```python
"""Pydantic models for the knowledge service."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field
```

and add the two response models at the end:

In `src/knowledge_service/contracts.py`, find:

```python
    code: str
    message: str
    request_id: UUID
```

and replace it with:

```python
    code: str
    message: str
    request_id: UUID


class JobAttemptProgress(BaseModel):
    """How far one attempt of a Job got, and how it ended."""

    attempt: int = Field(ge=1)
    stage: str
    processed: int = Field(ge=0)
    changed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    warnings: list[str]
    started_at: datetime
    heartbeat_at: datetime
    outcome: str | None
    finished_at: datetime | None


class SynchronizationJobReport(BaseModel):
    """A synchronization Job and the progress of its newest attempt."""

    job_id: UUID
    source_id: UUID
    status: str
    failed_attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    retry_at: datetime | None
    latest_attempt: JobAttemptProgress | None
```

**Action 2:** in the application, import what the endpoint needs:

In `src/knowledge_service/app.py`, find:

```python
    AnswerStream,
    AnswerStreamer,
)
from knowledge_service.contracts import Answer, ErrorResponse, Question
from knowledge_service.errors import (
    FailureKind,
    classify_failure,
    policy_for,
)
from knowledge_service.identifiers import ConversationId, UserId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.roles import Operation, PermissionDenied, require
from knowledge_service.workflow import AnswerWorkflow
```

and replace it with:

```python
    AnswerStream,
    AnswerStreamer,
)
from knowledge_service.contracts import (
    Answer,
    ErrorResponse,
    JobAttemptProgress,
    Question,
    SynchronizationJobReport,
)
from knowledge_service.errors import (
    FailureKind,
    classify_failure,
    policy_for,
)
from knowledge_service.identifiers import ConversationId, JobId, UserId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import JobKind
from knowledge_service.roles import Operation, PermissionDenied, require
from knowledge_service.workflow import AnswerWorkflow
```

add the protocol, the visible kinds, and the mapping to the response after `ConversationStore`:

In `src/knowledge_service/app.py`, find:

```python
    ) -> None: ...


class _NotAuthenticated(Exception):
    def __init__(self, error: AuthenticationError) -> None:
        self.error = error
```

and replace it with:

```python
    ) -> None: ...


class JobReports(Protocol):
    """Jobs and the progress of their newest attempt.

    `DatabaseJobReports` is the implementation. `load` returns None for a
    Job that does not exist or is not one of `kinds`.
    """

    async def load(
        self, *, job_id: JobId, kinds: frozenset[JobKind]
    ) -> JobReport | None: ...


# The Jobs the job endpoint shows: those a Synchronization Run starter owns.
VISIBLE_JOB_KINDS = frozenset({JobKind.SYNCHRONIZE_SOURCE})


def synchronization_job_report(report: JobReport) -> SynchronizationJobReport:
    """The API view of a synchronization Job and its newest attempt."""
    job, latest = report.job, report.latest_attempt
    return SynchronizationJobReport(
        job_id=job.job_id.value,
        source_id=job.target_id,
        status=job.status.value,
        failed_attempts=job.attempt,
        max_attempts=job.max_attempts,
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_code=job.last_error_code,
        retry_at=job.retry_at,
        latest_attempt=None
        if latest is None
        else JobAttemptProgress(
            attempt=latest.attempt,
            stage=latest.stage.value,
            processed=latest.counters.processed,
            changed=latest.counters.changed,
            skipped=latest.counters.skipped,
            warnings=sorted(warning.value for warning in latest.warnings),
            started_at=latest.started_at,
            heartbeat_at=latest.heartbeat_at,
            outcome=None if latest.outcome is None else latest.outcome.value,
            finished_at=latest.finished_at,
        ),
    )


class _NotAuthenticated(Exception):
    def __init__(self, error: AuthenticationError) -> None:
        self.error = error
```

take the store in `create_app`:

In `src/knowledge_service/app.py`, find:

```python
    streamer: AnswerStreamer | None = None,
    authenticator: RequestAuthenticator | None = None,
    conversations: ConversationStore | None = None,
) -> FastAPI:
    """The HTTP application.
```

and replace it with:

```python
    streamer: AnswerStreamer | None = None,
    authenticator: RequestAuthenticator | None = None,
    conversations: ConversationStore | None = None,
    job_reports: JobReports | None = None,
) -> FastAPI:
    """The HTTP application.
```

and add the endpoint after the Conversation delete:

In `src/knowledge_service/app.py`, find:

```python
        # The same whether it was deleted, missing, or another User's.
        return Response(status_code=204)

    return app
```

and replace it with:

```python
        # The same whether it was deleted, missing, or another User's.
        return Response(status_code=204)

    @app.get("/v1/jobs/{job_id}", response_model=SynchronizationJobReport)
    async def read_synchronization_job(
        request: Request, job_id: str
    ) -> SynchronizationJobReport | JSONResponse:
        # Who, then whether they may, then which Job.
        caller = await authenticate(request)
        require(caller.roles, Operation.START_SYNCHRONIZATION_RUNS)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(job_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if job_reports is None:
            return error_response(
                status_code=503,
                code="jobs_unavailable",
                message="Jobs are unavailable",
                request_id=request_id,
            )
        try:
            report = await job_reports.load(
                job_id=JobId.parse(job_id), kinds=VISIBLE_JOB_KINDS
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("job read failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        if report is None or report.job.kind not in VISIBLE_JOB_KINDS:
            # The same for a missing Job and for a Job of another kind.
            return error_response(
                status_code=404,
                code="job_not_found",
                message="No such synchronization job",
                request_id=request_id,
            )
        return synchronization_job_report(report)

    return app
```

**Action 3:** in the authentication tests, import `datetime`:

In `tests/test_app_authentication.py`, find:

```python
"""

import logging
from typing import Any, cast
from uuid import uuid4
```

and replace it with:

```python
"""

import logging
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4
```

then `JobId`:

In `tests/test_app_authentication.py`, find:

```python
from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.generation import InvalidAnswerRequest
from knowledge_service.app import create_app
from knowledge_service.identity.context import (
    Authenticator,
    Caller,
```

and replace it with:

```python
from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.generation import InvalidAnswerRequest
from knowledge_service.app import create_app
from knowledge_service.identifiers import JobId
from knowledge_service.identity.context import (
    Authenticator,
    Caller,
```

and the Job types:

In `tests/test_app_authentication.py`, find:

```python
    user_id_for,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.roles import ApplicationRole, Operation
from knowledge_service.workflow import AnswerWorkflow
from test_authorization_context import (
```

and replace it with:

```python
    user_id_for,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import Job, JobKind
from knowledge_service.roles import ApplicationRole, Operation
from knowledge_service.workflow import AnswerWorkflow
from test_authorization_context import (
```

list the route with its operation:

In `tests/test_app_authentication.py`, find:

```python
    ("DELETE", "/v1/conversations/{conversation_id}"): (
        Operation.MANAGE_OWN_CONVERSATIONS
    ),
}
PROTECTED = [(route, operation) for route, operation in ROUTES.items() if operation]
# The routes that read a body, to check none is read before the role.
```

and replace it with:

```python
    ("DELETE", "/v1/conversations/{conversation_id}"): (
        Operation.MANAGE_OWN_CONVERSATIONS
    ),
    ("GET", "/v1/jobs/{job_id}"): Operation.START_SYNCHRONIZATION_RUNS,
}
PROTECTED = [(route, operation) for route, operation in ROUTES.items() if operation]
# The routes that read a body, to check none is read before the role.
```

add a fake store after `Deleting`:

In `tests/test_app_authentication.py`, find:

```python
        self.calls.append(request)


def full_app(
    source: Source,
    workflow: Any = None,
    tokens: dict[str, Any] | None = None,
    conversations: Deleting | None = None,
) -> Any:
    """The app with every endpoint wired, so every route is real."""
    return create_app(
```

and replace it with:

```python
        self.calls.append(request)


class Reading:
    """A Job store that records each Job it is asked for, and finds none."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def load(self, **request: Any) -> None:
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

wire it into `full_app`:

In `tests/test_app_authentication.py`, find:

```python
        streamer=streamer(Model(), source=source),
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
        conversations=conversations or Deleting(),
    )
```

and replace it with:

```python
        streamer=streamer(Model(), source=source),
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
        conversations=conversations or Deleting(),
        job_reports=jobs or Reading(),
    )
```

count it in the role test:

In `tests/test_app_authentication.py`, find:

```python
    roles: frozenset[ApplicationRole],
) -> None:
    source, workflow, conversations = Source(), Counting(), Deleting()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(source, workflow, {"token": token}, conversations)
    method, path = route

    async with client(app) as http:
        response = await http.request(
            method,
            path.replace("{conversation_id}", str(uuid4())),
            json={"text": QUESTION.text},
            headers={"Authorization": "Bearer token"},
        )

    allowed = bool(roles & MATRIX[operation])
    assert (response.status_code == 403) is not allowed
    assert bool(source.readers or workflow.calls or conversations.calls) is allowed


@pytest.mark.parametrize("path", WITH_BODY)
```

and replace it with:

```python
    roles: frozenset[ApplicationRole],
) -> None:
    source, workflow, conversations = Source(), Counting(), Deleting()
    jobs = Reading()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(source, workflow, {"token": token}, conversations, jobs)
    method, path = route

    async with client(app) as http:
        response = await http.request(
            method,
            path.replace("{conversation_id}", str(uuid4())).replace(
                "{job_id}", str(uuid4())
            ),
            json={"text": QUESTION.text},
            headers={"Authorization": "Bearer token"},
        )

    allowed = bool(roles & MATRIX[operation])
    assert (response.status_code == 403) is not allowed
    reached = source.readers or workflow.calls or conversations.calls or jobs.calls
    assert bool(reached) is allowed


@pytest.mark.parametrize("path", WITH_BODY)
```

and add the test that a careless store cannot leak another kind:

In `tests/test_app_authentication.py`, find:

```python
        "request not authenticated: signature",
        "request not authenticated: missing",
    ]
```

and replace it with:

```python
        "request not authenticated: signature",
        "request not authenticated: missing",
    ]


async def test_the_job_endpoint_shows_only_synchronization_jobs() -> None:
    purge = Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_CONVERSATION,
        target_id=uuid4(),
        queued_at=datetime(2026, 11, 24, 9, 0, tzinfo=UTC),
    )

    class Careless(Reading):
        """A Job store that forgets to filter by kind."""

        async def load(self, **request: Any) -> JobReport:  # type: ignore[override]
            self.calls.append(request)
            return JobReport(job=purge, latest_attempt=None)

    app = full_app(Source(), tokens={"token": TOKENS["staff-token"]}, jobs=Careless())
    async with client(app) as http:
        response = await http.get(
            f"/v1/jobs/{purge.job_id.value}", headers={"Authorization": "Bearer token"}
        )

    # A purge's target, such as a Conversation, is never shown.
    assert response.status_code == 404
    assert str(purge.target_id) not in response.text
```

**Check:** `uv run pytest tests/test_app_authentication.py -q` reports `107 passed`.

### Step 5 — Coding step: prove it on real PostgreSQL

This step tests every block of "Before you begin" with real transactions, real locks, and real HTTP.

**In plain words:** each test starts a synchronization Job the way the M08-T10 handler will, sends reports, and checks what is stored and what the API shows. Some tests kill the reporting runtime, run reports at the same time, or let a stale worker try to write.

| Test | What it proves |
|---|---|
| `test_progress_survives_the_worker_that_reported_it` | block 1 and the API |
| `test_a_lower_counter_is_refused_and_the_stored_one_stays`, `test_an_older_heartbeat_or_a_lost_warning_is_refused` | block 2 |
| `test_a_replayed_report_changes_nothing` | replay |
| `test_reports_racing_each_other_never_lower_what_is_stored` | 30 reports at once, out of order |
| `test_a_report_from_an_attempt_the_job_left_is_refused`, `test_a_finished_job_takes_no_more_reports`, `test_a_report_for_an_unknown_job_is_refused` | block 3 |
| `test_the_database_refuses_a_counter_below_zero`, `test_deleting_a_job_deletes_its_attempts` | the table's own checks |
| `test_a_purge_records_its_attempt_and_outcome`, `test_each_purge_attempt_keeps_its_own_outcome` | the purge and per-attempt rows |
| `test_the_api_shows_a_queued_job_with_no_attempt_yet`, `test_the_api_hides_jobs_that_are_not_synchronizations` | block 5 |
| the tests under "Found by the adversarial review" | block 6, one per finding |


Create `tests/test_job_progress_integration.py`:

```python
"""Job progress and outcomes stored in PostgreSQL, read through the API.

A running attempt reports its stage, counters, warnings, and heartbeat; each
report commits on its own, so a new worker process, or the API, reads it
after the reporting one is gone. Nothing in a report may go back, and an
attempt the Job is no longer running cannot report. Each attempt keeps its
own row and typed outcome.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from authorization_scenarios import Person
from job_fixtures import (
    OBSERVED_AT,
    SOURCE_ID,
    database_went_away,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service import job_reporting
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import purge_document_chunks
from knowledge_service.job_failures import RetryPolicy
from knowledge_service.job_progress import (
    AttemptOutcome,
    JobCounters,
    JobProgress,
    JobStage,
    JobWarning,
    ProgressWentBackwards,
    ReportFromTheFuture,
)
from knowledge_service.job_reporting import DatabaseJobReports, report_progress
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.persistence.job_attempts import (
    AttemptAlreadyBegun,
    AttemptNotCurrent,
    advance_attempt,
    begin_attempt,
    finish_attempt,
    load_latest_attempt,
)
from knowledge_service.persistence.jobs import (
    enqueue_job,
    load_job,
    requeue_due_jobs,
    save_job,
)
from knowledge_service.persistence.schema import job_attempts, jobs
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings

pytestmark = pytest.mark.integration

STARTED_AT = OBSERVED_AT + timedelta(minutes=1)
LATER = STARTED_AT + timedelta(seconds=30)
POLICY = RetryPolicy(jitter=lambda: 0.5)

CALLERS = {
    "Bearer morgan": Caller(
        reader=Person("morgan", ("/engineering",)).context(),
        roles=frozenset({ApplicationRole.CONTENT_MANAGER}),
    ),
}


class Tokens:
    """Gives each test token the caller its claims would give."""

    async def authenticate(self, headers: list[str]) -> Caller:
        if len(headers) == 1 and headers[0] in CALLERS:
            return CALLERS[headers[0]]
        raise Unauthenticated("test", error=AuthenticationError.INVALID_TOKEN)


def new_runtime(url: str) -> DatabaseRuntime:
    return create_database_runtime(Settings(database_url=SecretStr(url)))


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = new_runtime(migrated_database)
    await insert_source(runtime)
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def running_synchronization(runtime: DatabaseRuntime) -> Job:
    """A synchronization Job whose claim committed, as its handler will do."""
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


async def latest(runtime: DatabaseRuntime, job_id: JobId) -> JobProgress:
    async with runtime.transaction() as connection:
        progress = await load_latest_attempt(connection, job_id=job_id)
    assert progress is not None
    return progress


async def report(
    runtime: DatabaseRuntime,
    job: Job,
    *,
    processed: int,
    at: timedelta = timedelta(seconds=30),
    attempt: int = 1,
    warnings: tuple[JobWarning, ...] = (),
) -> JobProgress:
    return await report_progress(
        runtime,
        job_id=job.job_id,
        attempt=attempt,
        stage=JobStage.READING,
        counters=JobCounters(processed=processed),
        warnings=warnings,
        now=STARTED_AT + at,
    )


async def read_over_http(runtime: DatabaseRuntime, job_id: JobId) -> httpx.Response:
    app = create_app(authenticator=Tokens(), job_reports=DatabaseJobReports(runtime))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.get(
            f"/v1/jobs/{job_id.value}", headers={"Authorization": "Bearer morgan"}
        )


async def test_progress_survives_the_worker_that_reported_it(
    migrated_database: str, runtime: DatabaseRuntime
) -> None:
    job = await running_synchronization(runtime)
    worker = new_runtime(migrated_database)
    await report_progress(
        worker,
        job_id=job.job_id,
        attempt=1,
        stage=JobStage.WRITING,
        counters=JobCounters(processed=120, changed=7, skipped=2),
        warnings=[JobWarning.QUARANTINED_ITEMS],
        now=LATER,
    )
    # The worker process ends; its connections go with it.
    await worker.dispose()

    response = await read_over_http(runtime, job.job_id)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["source_id"] == str(SOURCE_ID.value)
    assert body["latest_attempt"] == {
        "attempt": 1,
        "stage": "writing",
        "processed": 120,
        "changed": 7,
        "skipped": 2,
        "warnings": ["quarantined_items"],
        "started_at": "2026-11-21T09:01:00Z",
        "heartbeat_at": "2026-11-21T09:01:30Z",
        "outcome": None,
        "finished_at": None,
    }


async def test_a_lower_counter_is_refused_and_the_stored_one_stays(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await report(runtime, job, processed=50)

    with pytest.raises(ProgressWentBackwards):
        await report(runtime, job, processed=49, at=timedelta(minutes=1))

    stored = await latest(runtime, job.job_id)
    assert stored.counters.processed == 50
    assert stored.heartbeat_at == STARTED_AT + timedelta(seconds=30)


async def test_an_older_heartbeat_or_a_lost_warning_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await report(runtime, job, processed=1, warnings=(JobWarning.UNRESOLVED_READERS,))

    with pytest.raises(ProgressWentBackwards):
        await report(
            runtime,
            job,
            processed=2,
            at=timedelta(seconds=29),
            warnings=(JobWarning.UNRESOLVED_READERS,),
        )
    with pytest.raises(ProgressWentBackwards):
        await report(runtime, job, processed=2, at=timedelta(minutes=1))

    stored = await latest(runtime, job.job_id)
    assert stored.counters.processed == 1
    assert stored.warnings == {JobWarning.UNRESOLVED_READERS}


async def test_a_replayed_report_changes_nothing(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)
    first = await report(runtime, job, processed=10)

    again = await report(runtime, job, processed=10)

    assert again == first == await latest(runtime, job.job_id)


async def test_reports_racing_each_other_never_lower_what_is_stored(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    # Even totals high to low, then odd ones low to high: out of order.
    totals = [*range(30, 0, -2), *range(1, 31, 2)]

    async def send(total: int) -> int | None:
        try:
            await report(runtime, job, processed=total, at=timedelta(seconds=total))
        except ProgressWentBackwards:
            return None
        return total

    accepted = [t for t in await asyncio.gather(*map(send, totals)) if t]

    # Every report either landed or was refused as going back; the highest won.
    assert 30 in accepted
    assert (await latest(runtime, job.job_id)).counters.processed == 30


async def test_a_report_from_an_attempt_the_job_left_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await report(runtime, job, processed=5)
    retry_at = LATER + timedelta(minutes=1)
    async with runtime.transaction() as connection:
        failed = job.fail(
            error_code="source_unavailable", finished_at=LATER, retry_at=retry_at
        )
        await save_job(connection, job=failed)
        await finish_attempt(connection, job=failed)
        [requeued] = await requeue_due_jobs(connection, now=retry_at, limit=10)
        second = requeued.start(started_at=retry_at)
        await save_job(connection, job=second)
        await begin_attempt(connection, job=second)

    # The first attempt's worker wakes up and reports.
    with pytest.raises(AttemptNotCurrent):
        await report(runtime, job, processed=99, at=timedelta(minutes=5))

    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    job_attempts.c.attempt,
                    job_attempts.c.processed,
                    job_attempts.c.outcome,
                ).order_by(job_attempts.c.attempt)
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        (1, 5, "retry_scheduled"),
        (2, 0, None),
    ]


async def test_a_finished_job_takes_no_more_reports(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)
    async with runtime.transaction() as connection:
        succeeded = job.succeed(finished_at=LATER)
        await save_job(connection, job=succeeded)
        await finish_attempt(connection, job=succeeded)

    with pytest.raises(AttemptNotCurrent):
        await report(runtime, job, processed=1, at=timedelta(minutes=1))

    assert (await latest(runtime, job.job_id)).outcome is AttemptOutcome.SUCCEEDED


async def test_a_report_for_an_unknown_job_is_refused(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)

    with pytest.raises(AttemptNotCurrent):
        await report_progress(
            runtime,
            job_id=JobId.new(),
            attempt=1,
            stage=JobStage.READING,
            counters=JobCounters(),
            now=LATER,
        )
    with pytest.raises(AttemptNotCurrent):
        await report(runtime, job, processed=1, attempt=2)


async def test_the_database_refuses_a_counter_below_zero(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    with pytest.raises(IntegrityError):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.update(job_attempts)
                .where(job_attempts.c.job_id == job.job_id.value)
                .values(processed=-1)
            )


async def test_a_purge_records_its_attempt_and_outcome(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)

    await purge_document_chunks(runtime, job_id=purge_id, now=LATER)

    progress = await latest(runtime, purge_id)
    assert progress.attempt == 1
    assert progress.counters == JobCounters(processed=1, changed=1, skipped=0)
    assert progress.outcome is AttemptOutcome.SUCCEEDED
    assert progress.finished_at == LATER


async def test_each_purge_attempt_keeps_its_own_outcome(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    real_delete = deletions.delete_chunks

    async def lost_connection(*_args: object, **_kwargs: object) -> int:
        raise database_went_away()

    monkeypatch.setattr(deletions, "delete_chunks", lost_connection)
    failed = await purge_document_chunks(
        runtime, job_id=purge_id, now=LATER, retry_policy=POLICY
    )
    assert failed.retry_at is not None
    monkeypatch.setattr(deletions, "delete_chunks", real_delete)
    async with runtime.transaction() as connection:
        await requeue_due_jobs(connection, now=failed.retry_at, limit=10)
    await purge_document_chunks(runtime, job_id=purge_id, now=failed.retry_at)

    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(job_attempts.c.attempt, job_attempts.c.outcome)
                .where(job_attempts.c.job_id == purge_id.value)
                .order_by(job_attempts.c.attempt)
            )
        ).all()
    assert [tuple(row) for row in rows] == [(1, "retry_scheduled"), (2, "succeeded")]
    assert (await stored_job(runtime, purge_id)).status is JobStatus.SUCCEEDED


async def test_the_api_shows_a_queued_job_with_no_attempt_yet(
    runtime: DatabaseRuntime,
) -> None:
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

    response = await read_over_http(runtime, queued.job.job_id)

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["latest_attempt"] is None


async def test_the_api_hides_jobs_that_are_not_synchronizations(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)

    hidden = await read_over_http(runtime, purge_id)
    missing = await read_over_http(runtime, JobId.new())

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["code"] == missing.json()["code"] == "job_not_found"


async def test_deleting_a_job_deletes_its_attempts(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)

    async with runtime.transaction() as connection:
        await connection.execute(
            sa.delete(jobs).where(jobs.c.job_id == job.job_id.value)
        )
        left = await connection.scalar(
            sa.select(sa.func.count()).select_from(job_attempts)
        )
        stored = await load_job(connection, job_id=job.job_id)

    assert left == 0
    assert stored is None


# --- Found by the adversarial review ---------------------------------------


async def test_a_heartbeat_later_than_the_finish_does_not_stop_the_finish(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    # The reporting worker's clock runs ten seconds ahead of the finisher's.
    await report(runtime, job, processed=3, at=timedelta(seconds=40))

    async with runtime.transaction() as connection:
        succeeded = job.succeed(finished_at=STARTED_AT + timedelta(seconds=30))
        await save_job(connection, job=succeeded)
        ended = await finish_attempt(connection, job=succeeded)

    assert ended.outcome is AttemptOutcome.SUCCEEDED
    # The attempt ends when its Job did; the skewed heartbeat is pulled back.
    assert ended.finished_at == ended.heartbeat_at == STARTED_AT + timedelta(seconds=30)
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.SUCCEEDED


async def test_final_counters_below_a_late_report_do_not_stop_the_finish(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await report(runtime, job, processed=10)

    async with runtime.transaction() as connection:
        succeeded = job.succeed(finished_at=LATER + timedelta(seconds=5))
        await save_job(connection, job=succeeded)
        ended = await finish_attempt(
            connection, job=succeeded, counters=JobCounters(processed=9, changed=2)
        )

    assert ended.counters == JobCounters(processed=10, changed=2)
    assert ended.outcome is AttemptOutcome.SUCCEEDED


async def test_a_report_from_a_clock_far_ahead_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    with pytest.raises(ReportFromTheFuture):
        await report(runtime, job, processed=1, at=timedelta(days=3650))

    # The attempt is not poisoned: an honest report still lands.
    assert (await report(runtime, job, processed=1)).counters.processed == 1


async def test_a_refused_report_does_not_hold_back_a_due_retry(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    retry_at = LATER + timedelta(minutes=1)
    async with runtime.transaction() as connection:
        failed = job.fail(
            error_code="source_unavailable", finished_at=LATER, retry_at=retry_at
        )
        await save_job(connection, job=failed)
        await finish_attempt(connection, job=failed)

    async with runtime.transaction() as stale:
        with pytest.raises(AttemptNotCurrent):
            await advance_attempt(
                stale,
                job_id=job.job_id,
                attempt=1,
                stage=JobStage.READING,
                counters=JobCounters(processed=1),
                now=retry_at,
            )
        # The stale report's transaction is still open.
        async with runtime.transaction() as relay:
            requeued = await requeue_due_jobs(relay, now=retry_at, limit=10)

    assert [j.job_id for j in requeued] == [job.job_id]


async def test_the_api_shows_the_job_and_its_attempt_from_one_moment(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await running_synchronization(runtime)
    real_load = job_reporting.load_latest_attempt

    async def finish_meanwhile(connection: object, *, job_id: JobId) -> object:
        # Another session finishes the Job between the API's two reads.
        async with runtime.transaction() as other:
            succeeded = job.succeed(finished_at=LATER)
            await save_job(other, job=succeeded)
            await finish_attempt(other, job=succeeded)
        return await real_load(connection, job_id=job_id)  # type: ignore[arg-type]

    monkeypatch.setattr(job_reporting, "load_latest_attempt", finish_meanwhile)

    body = (await read_over_http(runtime, job.job_id)).json()

    assert body["status"] == "running"
    assert body["latest_attempt"]["outcome"] is None


async def test_beginning_an_attempt_twice_is_a_typed_error(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    with pytest.raises(AttemptAlreadyBegun):
        async with runtime.transaction() as connection:
            await begin_attempt(connection, job=job)


async def test_a_stale_worker_cannot_end_an_attempt_the_job_left(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    retry_at = LATER + timedelta(minutes=1)
    stale = job.fail(
        error_code="source_unavailable", finished_at=LATER, retry_at=retry_at
    )
    async with runtime.transaction() as connection:
        await save_job(connection, job=stale)
        await finish_attempt(connection, job=stale)
        [requeued] = await requeue_due_jobs(connection, now=retry_at, limit=10)
        second = requeued.start(started_at=retry_at)
        await save_job(connection, job=second)
        await begin_attempt(connection, job=second)

    async def store_stale_outcome() -> None:
        # The first attempt's worker, presumed dead, stores its outcome again.
        async with runtime.transaction() as connection:
            await save_job(connection, job=stale)
            await finish_attempt(connection, job=stale)

    with pytest.raises(AttemptNotCurrent):
        await store_stale_outcome()

    stored = await stored_job(runtime, job.job_id)
    assert stored.status is JobStatus.RUNNING
    assert (await latest(runtime, job.job_id)).attempt == 2


async def test_an_attempt_reads_back_in_utc_whatever_the_session_time_zone(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    async with runtime.transaction() as connection:
        await connection.execute(sa.text("SET LOCAL TIME ZONE 'Asia/Tokyo'"))
        progress = await load_latest_attempt(connection, job_id=job.job_id)

    assert progress is not None
    assert progress.started_at == STARTED_AT
```

**Check:** `uv run pytest tests/test_job_progress_integration.py -q -m integration` reports `22 passed`.

### Step 6 — No coding in this step: break it on purpose

This step removes the domain's counter check, and shows which layer still holds.

**Action 1:** in `JobProgress.advance` in `src/knowledge_service/job_progress.py`, delete these two lines:

```python
        if not counters.covers(self.counters):
            raise ProgressWentBackwards("a counter would go down")
```

**Action 2:** run:

```bash
uv run pytest tests/test_job_progress.py tests/test_job_progress_integration.py -q -m "integration or not integration"
```

You see `3 failed, 52 passed`. The three domain tests for a counter going down fail. `test_a_lower_counter_is_refused_and_the_stored_one_stays` still passes: the `UPDATE`'s own `WHERE processed <= :new` matched no row, so the database refused it. That is the second layer from block 2.

**Action 3:** put the two lines back and run the command again: `55 passed`.

**Check:** compare the count with your Step 1 prediction, and note it in the evidence file.

### Step 7 — No coding in this step: update three documents

This step records the progress model where readers look for it.


**Action 1:** in the architecture, describe the endpoint after the Conversation delete:

In `docs/architecture.md`, find:

```markdown
`DatabaseConversationStore` (`answering.conversation_store`), which passes
the token's User as the owner and returns nothing, so the response is the same whether the
Conversation was deleted, missing, or another User's.

`job_dispatch` puts a Job on the queue without ever losing it. `submit_job`
commits the Job to PostgreSQL first, then sends one message naming only its
```

and replace it with:

```markdown
`DatabaseConversationStore` (`answering.conversation_store`), which passes
the token's User as the owner and returns nothing, so the response is the same whether the
Conversation was deleted, missing, or another User's.
`GET /v1/jobs/{job_id}` requires `start_synchronization_runs` before it
parses the ID, and shows a synchronization Job with its newest attempt
through `DatabaseJobReports` (`job_reporting`), read from one snapshot. A
missing Job and a Job of any other kind give the same 404, so a purge's
target, such as a Conversation, is never shown.

`job_dispatch` puts a Job on the queue without ever losing it. `submit_job`
commits the Job to PostgreSQL first, then sends one message naming only its
```

add the progress paragraph after the retry paragraph:

In `docs/architecture.md`, find:

```markdown
purge failed permanently is not given a new purge by later synchronization
passes; one that ran out of attempts on a passing failure is.

`leases` holds short-lived, owned locks in Redis. A lease is a key that
expires on its own, holding its holder's random token; renewing and
releasing compare that token inside Redis, so a holder whose lease expired
```

and replace it with:

```markdown
purge failed permanently is not given a new purge by later synchronization
passes; one that ran out of attempts on a passing failure is.

Each attempt of a Job has its own progress row (`job_progress`,
`persistence.job_attempts`): a stage, three counters (processed, changed,
skipped), warning codes, a heartbeat, and, once it ends, a typed outcome
(`succeeded`, `succeeded_with_warnings`, `retry_scheduled`, `superseded`, or
`failed`). A handler stores the row with its claim (`begin_attempt`),
reports totals so far with `report_progress`, each report in its own
transaction, and ends it with `finish_attempt` in the transaction that
stores the finished Job. A report shares the lock of the Job's row only
while the Job runs that attempt, so a worker that lost its attempt cannot
write; counters and warnings never go down and the heartbeat never goes
back, both in the domain and in the `UPDATE`'s own conditions; a report
stamped more than five minutes ahead of the database's clock is refused.
Ending never fails on what a report stored: higher counters stay, and a
heartbeat later than the Job's finish time is pulled back to it. A worker
presumed dead that ends an attempt its Job has moved past gets
`AttemptNotCurrent`, so its whole transaction rolls back. Nothing here knows
about queue messages; a later attempt never touches an earlier one's row.

`leases` holds short-lived, owned locks in Redis. A lease is a key that
expires on its own, holding its holder's random token; renewing and
releasing compare that token inside Redis, so a holder whose lease expired
```

and add the table's row after `jobs`:

In `docs/architecture.md`, find:

```markdown
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code; a retry time only on a failed Job with attempts left, never before it finished; at most one open Job (queued, running, or failed with a retry scheduled) per kind and target. |
| `audit_events` | An action, target kind, decision (`allowed` or `denied`), and outcome from fixed lists, with optional request and Synchronization Run IDs; no raw content: metadata keys are short names, string values one plain word, and the bag is capped at 1024 bytes. Rows are only ever inserted. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |
```

and replace it with:

```markdown
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code; a retry time only on a failed Job with attempts left, never before it finished; at most one open Job (queued, running, or failed with a retry scheduled) per kind and target. |
| `job_attempts` | One row per `(job_id, attempt)`, removed with its Job; a known stage, warnings, and outcome; counters never below zero; an outcome exactly when it has a finish time; a heartbeat no earlier than the start and no later than the finish. |
| `audit_events` | An action, target kind, decision (`allowed` or `denied`), and outcome from fixed lists, with optional request and Synchronization Run IDs; no raw content: metadata keys are short names, string values one plain word, and the bag is capped at 1024 bytes. Rows are only ever inserted. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |
```

**Action 2:** in the operations guide, say where progress is and what a stopped heartbeat means:

In `docs/operations.md`, find:

```markdown

## Telemetry

Use OpenTelemetry for traces and metrics and structured JSON logs with shared request, Conversation, Synchronization Run, Evaluation Run, and job identifiers. Collect queue depth, run age, retry counts, retrieval-stage latency, Candidate counts, model tokens, estimated cost, streaming cancellation, and provider errors. Production content fields are redacted by default.

## Recovery and failure ownership
```

and replace it with:

```markdown

## Telemetry

Use OpenTelemetry for traces and metrics and structured JSON logs with shared request, Conversation, Synchronization Run, Evaluation Run, and job identifiers. Each Job attempt's stage, counters, warnings, heartbeat, and outcome are stored in `job_attempts` and shown by `GET /v1/jobs/{job_id}` for synchronization Jobs; a running attempt whose heartbeat stops moving is the sign of a dead worker. Collect queue depth, run age, retry counts, retrieval-stage latency, Candidate counts, model tokens, estimated cost, streaming cancellation, and provider errors. Production content fields are redacted by default.

## Recovery and failure ownership
```

**Action 3:** in the threat model, add the entry point after the Conversation delete:

In `docs/threat-model.md`, find:

```markdown
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. Needs a bearer token and the `user` role, both checked before the body is read; the Evidence source searches as the token's User and Groups |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: needs a bearer token and the `user` role, then sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `DELETE /v1/conversations/{conversation_id}` | Any client that reaches the API | Deletes the caller's own Conversation. Needs a bearer token and the `manage_own_conversations` operation (the `user` role), both checked before the ID is parsed; the owner is the token's User, and only the canonical spelling of an ID is accepted; the answer is 204 whether the Conversation was deleted, missing, or another User's |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Redis, `127.0.0.1:6380` | Developers and local workers | Bound to 127.0.0.1, with no password: anyone on this machine can read or write its queues, so a worker checks every message argument again. It never evicts a key and keeps nothing across a restart |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
```

and replace it with:

```markdown
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. Needs a bearer token and the `user` role, both checked before the body is read; the Evidence source searches as the token's User and Groups |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: needs a bearer token and the `user` role, then sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `DELETE /v1/conversations/{conversation_id}` | Any client that reaches the API | Deletes the caller's own Conversation. Needs a bearer token and the `manage_own_conversations` operation (the `user` role), both checked before the ID is parsed; the owner is the token's User, and only the canonical spelling of an ID is accepted; the answer is 204 whether the Conversation was deleted, missing, or another User's |
| `GET /v1/jobs/{job_id}` | Any client that reaches the API | Shows one synchronization Job and its newest attempt's progress. Needs a bearer token and the `start_synchronization_runs` operation (the `content-manager` role), both checked before the ID is parsed; a missing Job and a Job of another kind give the same 404; no error text, only codes |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Redis, `127.0.0.1:6380` | Developers and local workers | Bound to 127.0.0.1, with no password: anyone on this machine can read or write its queues, so a worker checks every message argument again. It never evicts a key and keeps nothing across a restart |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
```

and the abuse case before the scheduled-Job row:

In `docs/threat-model.md`, find:

```markdown
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |
| Two synchronization passes of one Source run at once: double Source calls, and an older pass finishing late re-grants a removed reader, rolls content back, or tombstones what a newer pass saw | `synchronize_source_exclusively` holds the Source's Redis lease, with an owner token, renewed after every page, so a second pass gets `SourceBusy` before reading; a crashed holder's lease expires in two minutes. The Source's fence in PostgreSQL decides who may write: every pass claims it with its observation time, and every narrowing, reconciliation, and tombstone checks it under a share lock on the Source's row, so after a newer pass claims it nothing of an older pass lands; an observation more than five minutes in the future is refused | Writes an older pass committed before the newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way; a pass observed up to five minutes in the future holds the fence until then |
| A scheduled synchronization Job carries a Source's location or credentials through Redis, or the scheduler floods the queue | A scheduled Job stores only the Source's ID, and its handler reads the configuration and credentials reference from PostgreSQL when it runs; a Source with an open Job is skipped, a missed schedule queues one Job, and a pass queues at most 1,000 | Disabling a Source does not cancel a Job already queued for it; a Source whose synchronization Job is stuck open is not scheduled again until M08-T08 |

### Retrieval
```

and replace it with:

```markdown
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |
| Two synchronization passes of one Source run at once: double Source calls, and an older pass finishing late re-grants a removed reader, rolls content back, or tombstones what a newer pass saw | `synchronize_source_exclusively` holds the Source's Redis lease, with an owner token, renewed after every page, so a second pass gets `SourceBusy` before reading; a crashed holder's lease expires in two minutes. The Source's fence in PostgreSQL decides who may write: every pass claims it with its observation time, and every narrowing, reconciliation, and tombstone checks it under a share lock on the Source's row, so after a newer pass claims it nothing of an older pass lands; an observation more than five minutes in the future is refused | Writes an older pass committed before the newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way; a pass observed up to five minutes in the future holds the fence until then |
| A stale or replayed worker rewrites a Job's progress, or a bad clock blocks it | A report shares the Job's row lock only while the Job runs that attempt, counters and warnings never go down and the heartbeat never goes back (checked again in the `UPDATE`), a report far ahead of the database's clock is refused, each attempt has its own row, and ending an attempt the Job has moved past is refused; ending an attempt never fails on what a report stored | No production handler reports progress until M08-T10; a Job whose worker died stays running with its last heartbeat until M08-T08; a worker more than five minutes ahead of the database cannot report |
| A scheduled synchronization Job carries a Source's location or credentials through Redis, or the scheduler floods the queue | A scheduled Job stores only the Source's ID, and its handler reads the configuration and credentials reference from PostgreSQL when it runs; a Source with an open Job is skipped, a missed schedule queues one Job, and a pass queues at most 1,000 | Disabling a Source does not cancel a Job already queued for it; a Source whose synchronization Job is stuck open is not scheduled again until M08-T08 |

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

**Action 2:** finish the evidence file: the gate results and the Step 6 count under "Verification", the review findings and known limits under "Review", the design under "Design" and "Decision", and your answers to the reflection questions below. Then mark the task completed in `docs/curriculum/PROGRESS.md` and commit with `m08-t07: report progress and terminal outcomes`.

**Check:** every gate passes, and `git status` is clean after the commit.

## Reflection questions

- Worker A reported 120 processed at 10:05 and was killed at 10:06. What does `GET /v1/jobs/J7` show at 10:30, and which field tells an operator that nobody is working on it?
- A report of 50 processed arrives after 60 was stored. Which two checks refuse it, and why does the second one matter if the first exists?
- Why does a report carry totals ("120 so far") and not increments ("+20")? What would a replayed "+20" do?
- Attempt 1's worker wakes up after attempt 2 began. Why can it not write, and why does `lock_running_job` lock only running rows?
- Which failure can still happen after a caller believes the work succeeded? Think of a worker that reported its last page and died before the attempt ended.
- Which test in this lesson would fail first if someone later rewrote `_save` without its `WHERE` conditions and also removed the domain check?

## Completion checklist

- [ ] The evidence file has the invariant, the owners, and your predictions, written before the change.
- [ ] `job_progress.py` exists, and `tests/test_job_progress.py` (33) passes.
- [ ] Migration `c9d0e1f2a3b4` adds `job_attempts`; `lock_running_job`, `persistence/job_attempts.py`, and `job_reporting.py` exist; the purge records its attempts.
- [ ] `GET /v1/jobs/{job_id}` needs `start_synchronization_runs` and shows only synchronization Jobs; `tests/test_app_authentication.py` (107) passes.
- [ ] `tests/test_job_progress_integration.py` (22) passes: restart, backwards, replay, races, stale worker, outcomes, API, review findings.
- [ ] You ran the deliberate break, saw three domain tests fail while the database test held, and undid it.
- [ ] `docs/architecture.md`, `docs/operations.md`, and `docs/threat-model.md` describe the progress model.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
