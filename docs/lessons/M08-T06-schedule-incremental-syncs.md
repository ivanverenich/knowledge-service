# M08-T06 — Schedule incremental Synchronization Runs

Task: [M08-T06 — Schedule incremental Synchronization Runs](../curriculum/milestones/M08-jobs-and-confluence/M08-T06-schedule-incremental-syncs.md)

## What you will do

This lesson makes synchronization start by itself. Every pass of `make worker-relay` queues one synchronization Job for each enabled Source whose last run is at least an hour old, and never queues one that is redundant.

A concrete example, with the default interval of one hour and the relay running every ten minutes:

| Time | Handbook (enabled) | Archive (disabled) |
|---|---|---|
| 09:00 | never synchronized: a Job is queued | skipped |
| 09:10 | its Job is still open: nothing | skipped |
| 09:50 | its run finished at 09:50 | skipped |
| 10:40 | 50 minutes since it finished: nothing | skipped |
| 10:50 | one hour: a Job is queued | skipped |

| What the task asks | Where it is done |
|---|---|
| Enqueue enabled Sources by policy | Steps 2 and 3: `load_due_source_ids` and `schedule_due_synchronizations`, with a configurable interval |
| Suppress redundant schedules | "Before you begin", block 2: open Jobs skipped, missed schedules queued once, two passes store one Job |
| Carry only stable identifiers, not secret configuration | Block 3: the Job holds the Source's ID only |
| Clock-controlled tests: due, disabled, already-running, missed | Step 5: every test passes its own `now` |
| Replay or cancellation behaviour explicit | Blocks 2 and 5 |
| Update only affected documentation | Step 7: architecture, operations, threat model |

This lesson includes coding: Steps 2 to 5 write code, a migration, and tests. Steps 1, 6, 7, and 8 need no code, although Step 7 edits three documents.

Terms used in this lesson:

- **Due:** a Source whose next synchronization should be queued now.
- **Interval:** how long after its last run a Source is due again; an hour by default.
- **Last activity:** when a Source's newest synchronization Job finished, or was queued if it never finished.
- **Redundant schedule:** a Job that would do the same work as one that already exists or just ran.

## Before you begin

This section explains every choice the code rests on, step by step. Each block ends with what this lesson changes.

### 1. When a Source is due

**The situation.** Handbook's last run was queued at 09:00 and finished at 09:50. The interval is one hour.

| Relay pass at | Last activity | Due? |
|---|---|---|
| 10:40 | 09:50 | no: 50 minutes |
| 10:50 | 09:50 | yes: one hour |

**The hidden fact.** Counting from when the run *finished*, not when it was queued, matters for long runs. If a run took two and a half hours, counting from the queue time would make the Source due the moment the run ends, so it would sync back to back. Counting from the finish time gives it a full interval of rest.

**What changes.** `load_due_source_ids` returns every enabled Source with no open synchronization Job whose last activity is at least one interval ago, or that never had a Job.

### 2. Redundant schedules are suppressed

| Case | What a naive scheduler would do | What this one does |
|---|---|---|
| the Source's Job is still queued or running | queue a second | skip it: it has an open Job |
| the Source ran 20 minutes ago | queue again | skip it: not due |
| the relay was down for ten hours | queue ten Jobs, one per missed hour | queue **one**: last activity is old, and once it has a Job it has an open Job |
| two relay passes run at the same moment | each queues one | one Job: the open-intent index from M08-T03 |

**The hidden fact.** "Due" is decided from the Jobs table itself, not from a list of planned times. There is no backlog of missed times to replay, so a missed schedule cannot pile up.

**Replay.** Running a pass twice at the same `now` queues nothing the second time: every Source it queued now has an open Job.

### 3. Only the ID travels

**The situation.** Handbook's row holds its location, `https://wiki.example.com/spaces/HB`, and a credentials reference, `vault:confluence/handbook`.

| Field of the queued Job | Value |
|---|---|
| `kind` | `synchronize_source` |
| `target_id` | Handbook's Source ID |
| anything else about the Source | nothing |

**The hidden fact.** A Job's message travels through Redis, which is not protected like PostgreSQL, and a Job can sit in the queue for a while. If it carried the location or the credentials reference, a reader of Redis could see them, and an old Job would run with old configuration. Carrying only the ID means the handler reads the current configuration from PostgreSQL when it runs.

**What changes.** The Job stores the Source's ID and nothing else, and a test checks the stored row contains neither the location nor the credentials reference. The handler that reads them arrives in M08-T10; until then these Jobs wait queued, because no worker runs that kind yet.

### 4. Review findings

An adversarial review attacked the first version of this code with real PostgreSQL. Each finding below was fixed, with a test that fails without the fix.

**Starvation.** With 150 Sources due and a pass limit of 100, the first version ordered by Source ID:

| Pass | Queued | Never reached |
|---|---|---|
| 1 | IDs 1–100 | 101–150 |
| 2, after those runs finished | IDs 1–100 again | 101–150 |

The longest-waiting Sources now come first (never synchronized, then oldest last activity), so the tail is reached next pass.

**A clock that ran ahead.** A worker whose clock was a year ahead recorded a finish time a year in the future. The Source looked "just synchronized" for a year. Now a finish time later than `now` is ignored and the Job counts from when it was queued; a Job queued in the future does not count.

**Disabled or deleted mid-pass.**

| Moment | Relay pass | Another session |
|---|---|---|
| 1 | reads: Handbook is due | |
| 2 | | disables Handbook, commits |
| 3 | queues a Job for Handbook (first version) | |

Now the pass rechecks the due Sources under a share lock (`SELECT ... WHERE enabled FOR SHARE`) before queuing. A change committed before the recheck is honoured; one attempted after it waits until the pass commits.

**A query that grew with history.** The first version scanned the whole `jobs` table for every Source, and the planner repeated the "last activity" subquery up to three times per Source. Now a partial index finds a Source's synchronization Jobs, and the candidates are a materialized CTE, so each Source's last activity is computed once:

```sql
WITH candidates AS MATERIALIZED (
  SELECT source_id, (SELECT max(...) FROM jobs WHERE ...) AS last_activity
  FROM sources WHERE enabled AND NOT EXISTS (an open synchronization Job)
)
SELECT source_id FROM candidates
WHERE last_activity IS NULL OR last_activity <= :now - :interval
ORDER BY last_activity NULLS FIRST, source_id
LIMIT :limit;
```

**A failing schedule stopped the relay.** Scheduling runs before publishing in the same relay pass, so a scheduling error held up every other Job's message. Now `relay_once` logs it by type, reports it, and still publishes; the command exits 1.

### 5. Known limits

- A Source whose synchronization Job is stuck open is not scheduled again until M08-T08 finds that Job. Until M08-T10 runs these Jobs, every Source is queued once and stays so.
- Disabling a Source does not cancel a Job already queued for it, and `jobs.target_id` has no foreign key: the handler in M08-T10 must treat a missing or disabled Source as a final outcome.
- Each pass computes the last activity of every enabled Source without an open Job, over all its synchronization Jobs; that grows with history until old Jobs are pruned.
- A failure while queuing due retries still stops the relay pass before publishing (from M08-T04).

## Step-by-step instructions

Start PostgreSQL before you begin. Run every command from the repository root. For integration checks, set the test database URL in your terminal:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

Run only one integration command against this database at a time.

### Step 1 — No coding in this step: write the invariant and your predictions

Before changing anything, write down what must be true at the end and what you expect to see. You check the predictions in Step 6 and Step 8.

Create `docs/evidence/M08-T06-schedule-incremental-syncs.md`:

```markdown
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

## Predictions

- What a pass queues for a Source whose last run finished 59 minutes ago, and at one hour:
- How many Jobs a pass queues for a Source after the relay was down for ten hours:
- What happens to a Source disabled by another session just after the pass read it:
- How many tests fail if the "at least one interval ago" condition is removed:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: find the Sources that are due

This step adds the query that decides which Sources are due, and an index that keeps it fast.

**In plain words:** the query looks at every enabled Source, skips the ones with a synchronization still open, and works out when each one last ran. A Source that never ran, or ran at least one interval ago, is due. The longest-waiting come first. A second query then locks those Sources and keeps only the ones that are still enabled.

| Piece | What it does |
|---|---|
| `ix_jobs_synchronization_target` | a partial index on `target_id` over synchronization Jobs only |
| Migration `b8c9d0e1f2a3` | adds it |
| `load_due_source_ids` | the query from block 4, with future times ignored and the longest-waiting first |
| `lock_enabled_sources` | `SELECT ... WHERE enabled FOR SHARE` over the due IDs |


**Action 1:** create the migration.

Create `migrations/versions/b8c9d0e1f2a3_job_synchronization_target_index.py`:

```python
"""job synchronization target index

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-11-23 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"


def upgrade() -> None:
    op.create_index(
        "ix_jobs_synchronization_target",
        "jobs",
        ["target_id"],
        postgresql_where=sa.text("kind = 'synchronize_source'"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_synchronization_target", table_name="jobs")
```

**Action 2:** declare the index in the `jobs` table, before `ix_jobs_due_retry`:

In `src/knowledge_service/persistence/schema.py`, find:

```python
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    ),
    sa.Index(
        "ix_jobs_due_retry",
        "retry_at",
```

and replace it with:

```python
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    ),
    # Finds each Source's synchronization Jobs for the schedule.
    sa.Index(
        "ix_jobs_synchronization_target",
        "target_id",
        postgresql_where=sa.text("kind = 'synchronize_source'"),
    ),
    sa.Index(
        "ix_jobs_due_retry",
        "retry_at",
```

**Action 3:** allow it in the index list:

In `tests/test_indexes.py`, find:

```python
    "uq_embedding_spaces_current",
    "uq_jobs_open_intent",
    "ix_jobs_due_retry",
    "uq_synchronization_runs_running_source",
}
```

and replace it with:

```python
    "uq_embedding_spaces_current",
    "uq_jobs_open_intent",
    "ix_jobs_due_retry",
    "ix_jobs_synchronization_target",
    "uq_synchronization_runs_running_source",
}
```

**Action 4:** the file gains two functions and new imports; replace it whole.

Replace the whole of `src/knowledge_service/persistence/sources.py` with:

```python
"""Statements over Sources, their synchronization runs, their fence, and schedule."""

from collections.abc import Collection
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.identifiers import SourceId
from knowledge_service.jobs import JobKind
from knowledge_service.persistence.jobs import open_job
from knowledge_service.persistence.schema import jobs, sources, synchronization_runs


def source_runs_statement(source_id: UUID, limit: int) -> sa.Select[Any]:
    """Select the most recent Synchronization Runs of one Source."""
    return (
        sa.select(synchronization_runs)
        .where(synchronization_runs.c.source_id == source_id)
        .order_by(synchronization_runs.c.started_at.desc())
        .limit(limit)
    )


async def claim_synchronization_fence(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> bool:
    """Make `observed_at` the Source's fence, if it is newer than the fence.

    False when a pass that observed the Source at the same time or later
    already claimed it. The update waits for every write that holds the
    fence (`synchronization_fence_holds`), so none of them lands after it.
    """
    claimed = await connection.scalar(
        sa.update(sources)
        .where(
            sources.c.source_id == source_id.value,
            sa.or_(
                sources.c.synchronization_fence.is_(None),
                sources.c.synchronization_fence < observed_at,
            ),
        )
        .values(synchronization_fence=observed_at)
        .returning(sources.c.source_id)
    )
    return claimed is not None


async def synchronization_fence_holds(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> bool:
    """Whether the pass that observed at `observed_at` still holds the fence.

    The Source's row is share-locked until the transaction ends, so a newer
    pass cannot claim the fence while this write is in progress.
    """
    fence = await connection.scalar(
        sa.select(sources.c.synchronization_fence)
        .where(sources.c.source_id == source_id.value)
        .with_for_update(read=True)
    )
    return fence == observed_at


async def load_due_source_ids(
    connection: AsyncConnection, *, now: datetime, interval: timedelta, limit: int
) -> tuple[SourceId, ...]:
    """Enabled Sources with no open synchronization Job whose last one is old.

    A Source is due when its newest synchronization Job, counted from when it
    finished, or was queued if it never finished, is at least `interval`
    before `now`, or when it never had one. However long ago that was, it is
    due once: a missed schedule is not caught up run by run. A time later
    than `now`, from a clock that ran ahead, is ignored: a finish time in the
    future counts from when the Job was queued instead, and a Job queued in
    the future does not count. The longest-waiting Sources come first, so
    when more are due than `limit`, the rest come next pass.
    """
    of_source = sa.and_(
        jobs.c.kind == JobKind.SYNCHRONIZE_SOURCE.value,
        jobs.c.target_id == sources.c.source_id,
    )
    # greatest() skips the NULL a future finish time becomes here.
    activity = sa.func.greatest(
        jobs.c.queued_at,
        sa.case((jobs.c.finished_at <= now, jobs.c.finished_at), else_=None),
    )
    candidates = (
        sa.select(
            sources.c.source_id,
            sa.select(sa.func.max(activity))
            .where(of_source, jobs.c.queued_at <= now)
            .scalar_subquery()
            .label("last_activity"),
        )
        .where(sources.c.enabled.is_(True), ~sa.exists().where(of_source, open_job()))
        # Materialized, so each Source's last activity is computed once; a
        # plain subquery is inlined and the planner repeats it per use.
        .cte("candidates")
        .prefix_with("MATERIALIZED")
    )
    statement = (
        sa.select(candidates.c.source_id)
        .where(
            sa.or_(
                candidates.c.last_activity.is_(None),
                candidates.c.last_activity <= now - interval,
            )
        )
        .order_by(
            candidates.c.last_activity.asc().nulls_first(), candidates.c.source_id
        )
        .limit(limit)
    )
    rows = await connection.scalars(statement)
    return tuple(SourceId(source_id) for source_id in rows)


async def lock_enabled_sources(
    connection: AsyncConnection, *, source_ids: Collection[SourceId]
) -> frozenset[SourceId]:
    """Those of `source_ids` that still exist and are enabled, share-locked.

    Disabling or deleting one of them now waits for this transaction, so a
    Job queued in it is queued before the change, never after.
    """
    if not source_ids:
        return frozenset()
    rows = await connection.scalars(
        sa.select(sources.c.source_id)
        .where(
            sources.c.source_id.in_([source_id.value for source_id in source_ids]),
            sources.c.enabled.is_(True),
        )
        .order_by(sources.c.source_id)
        .with_for_update(read=True)
    )
    return frozenset(SourceId(source_id) for source_id in rows)
```

**Check:** `uv run pytest tests/test_indexes.py -q` reports `4 passed`, and `uv run pytest tests/test_migrations_integration.py -q -m integration` reports `3 passed`.

### Step 3 — Coding step: the schedule

This step adds the module that queues the Jobs, and the setting for the interval.

**In plain words:** one function takes the current time and the schedule, finds the due Sources, rechecks them, and stores one queued Job for each, naming only the Source's ID. It reports which Jobs it stored and which it found already stored by a pass running at the same moment.

| Piece | What it does |
|---|---|
| `SynchronizationSchedule` | the interval, from 1 minute to 7 days, an hour by default |
| `ScheduleReport` | `queued` and `already_open` Job IDs |
| `schedule_due_synchronizations` | checks UTC and `limit` (1 to 1,000), then reads, rechecks, and enqueues in one transaction |
| `synchronization_interval_minutes` | the setting, 1 to 10,080, 60 by default |


**Action 1:** create the module.

Create `src/knowledge_service/ingestion/scheduling.py`:

```python
"""Queue a synchronization Job for every enabled Source whose time has come.

A pass of the schedule runs with every `make worker-relay`, on the clock it
is given. For each enabled Source with no open synchronization Job whose last
Job ended at least one interval ago, it stores one queued Job. The Job names
the Source by its ID and nothing else: the worker reads the Source's
location, credentials reference, and checkpoint from PostgreSQL when it
runs, so no configuration or secret travels through Redis, and a changed
configuration is seen by the next run.

Redundant schedules are suppressed three ways: a Source with an open Job is
skipped, a Source that ran within the interval is not due, and a missed
schedule, after the scheduler was down, queues one Job, not one per missed
interval. Two passes at once still store one Job per Source, through the
open-intent index (M08-T03). When more Sources are due than a pass may
queue, the longest-waiting go first, so none is starved.

A Source with an open synchronization Job is not scheduled again until that
Job ends: a Job stuck running waits for M08-T08, and until the handler of
M08-T10 runs these Jobs they stay queued, so each Source is queued once.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId
from knowledge_service.jobs import Job, JobKind
from knowledge_service.persistence.jobs import enqueue_job
from knowledge_service.persistence.sources import (
    load_due_source_ids,
    lock_enabled_sources,
)

MIN_INTERVAL: Final = timedelta(minutes=1)
MAX_INTERVAL: Final = timedelta(days=7)
MAX_SCHEDULE_BATCH: Final = 1_000


@dataclass(frozen=True, slots=True)
class SynchronizationSchedule:
    """How often each enabled Source is synchronized."""

    interval: timedelta = timedelta(hours=1)

    def __post_init__(self) -> None:
        if type(self.interval) is not timedelta or not (
            MIN_INTERVAL <= self.interval <= MAX_INTERVAL
        ):
            raise ValueError(f"interval must be from {MIN_INTERVAL} to {MAX_INTERVAL}")


@dataclass(frozen=True, slots=True)
class ScheduleReport:
    """What one pass of the schedule queued, and what it found already open."""

    queued: tuple[JobId, ...]
    already_open: tuple[JobId, ...]


async def schedule_due_synchronizations(
    runtime: DatabaseRuntime,
    *,
    now: datetime,
    schedule: SynchronizationSchedule,
    limit: int = 100,
) -> ScheduleReport:
    """Queue one synchronization Job per due Source, up to `limit`.

    `already_open` lists Sources whose Job another pass stored between this
    pass's read and its write; nothing new is stored for them.
    """
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")
    if type(limit) is not int or not 1 <= limit <= MAX_SCHEDULE_BATCH:
        raise ValueError(f"limit must be an integer from 1 to {MAX_SCHEDULE_BATCH}")
    queued: list[JobId] = []
    already_open: list[JobId] = []
    async with runtime.transaction() as connection:
        due = await load_due_source_ids(
            connection, now=now, interval=schedule.interval, limit=limit
        )
        # A Source disabled or deleted since that read is left out.
        still_enabled = await lock_enabled_sources(connection, source_ids=due)
        for source_id in (s for s in due if s in still_enabled):
            enqueued = await enqueue_job(
                connection,
                Job.request(
                    job_id=JobId.new(),
                    kind=JobKind.SYNCHRONIZE_SOURCE,
                    target_id=source_id.value,
                    queued_at=now,
                ),
            )
            (queued if enqueued.created else already_open).append(enqueued.job.job_id)
    return ScheduleReport(queued=tuple(queued), already_open=tuple(already_open))
```

**Action 2:** add the setting after `database_statement_timeout_ms`:

In `src/knowledge_service/settings.py`, find:

```python
    database_pool_timeout: float = Field(default=10.0, gt=0.0, allow_inf_nan=False)
    database_connect_timeout_seconds: int = Field(default=5, gt=0)
    database_statement_timeout_ms: int = Field(default=10_000, gt=0)
    # The Redis broker that carries worker messages; it may hold a password.
    redis_url: SecretStr | None = None
    redis_health_timeout_seconds: float = Field(
```

and replace it with:

```python
    database_pool_timeout: float = Field(default=10.0, gt=0.0, allow_inf_nan=False)
    database_connect_timeout_seconds: int = Field(default=5, gt=0)
    database_statement_timeout_ms: int = Field(default=10_000, gt=0)
    # How often each enabled Source is synchronized (`ingestion.scheduling`).
    synchronization_interval_minutes: int = Field(default=60, ge=1, le=7 * 24 * 60)
    # The Redis broker that carries worker messages; it may hold a password.
    redis_url: SecretStr | None = None
    redis_health_timeout_seconds: float = Field(
```

**Action 3:** unit tests for the interval and the setting.

Create `tests/test_scheduling.py`:

```python
"""Tests for the synchronization schedule's bounds and its setting."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from knowledge_service.ingestion.scheduling import (
    MAX_INTERVAL,
    MIN_INTERVAL,
    SynchronizationSchedule,
)
from knowledge_service.settings import Settings


def test_every_source_is_synchronized_hourly_by_default() -> None:
    assert SynchronizationSchedule().interval == timedelta(hours=1)
    assert Settings.model_fields["synchronization_interval_minutes"].default == 60


@pytest.mark.parametrize("interval", [MIN_INTERVAL, timedelta(hours=6), MAX_INTERVAL])
def test_an_interval_within_bounds_is_accepted(interval: timedelta) -> None:
    assert SynchronizationSchedule(interval=interval).interval == interval


@pytest.mark.parametrize(
    "interval",
    [
        MIN_INTERVAL - timedelta(seconds=1),
        MAX_INTERVAL + timedelta(seconds=1),
        timedelta(0),
        60,
        None,
    ],
)
def test_an_interval_out_of_bounds_is_refused(interval: object) -> None:
    with pytest.raises(ValueError, match="interval"):
        SynchronizationSchedule(interval=interval)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("minutes", [0, -5, 7 * 24 * 60 + 1])
def test_the_interval_setting_is_bounded(minutes: int) -> None:
    with pytest.raises(ValidationError):
        Settings(synchronization_interval_minutes=minutes)
```

**Check:** `uv run pytest tests/test_scheduling.py -q` reports `12 passed`.

### Step 4 — Coding step: run the schedule in every relay pass

This step makes `make worker-relay` queue due synchronizations between requeuing retries and publishing.

**In plain words:** the relay pass now has three parts: bring back due retries, queue due synchronizations, then send messages. If the middle part fails, the relay logs it and still sends the messages of every other Job, then exits with 1 so a scheduler notices.

| Piece | What it does |
|---|---|
| `RelayPass` | what the pass did, including `schedule_failed` |
| `relay_once` | the three parts, with the scheduling failure isolated; public so it can be tested |
| `main` | prints `requeued N, scheduled M, published ...` |

**Action 1:** the relay changes throughout; replace it whole.


Replace the whole of `src/knowledge_service/worker/relay.py` with:

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
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.ingestion.scheduling import (
    ScheduleReport,
    SynchronizationSchedule,
    schedule_due_synchronizations,
)
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

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RelayPass:
    """What one relay pass did, in the order it did it."""

    requeued: tuple[JobId, ...]
    scheduled: ScheduleReport
    schedule_failed: bool
    published: RelayReport


async def relay_once(
    runtime: DatabaseRuntime,
    *,
    publisher: JobPublisher,
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
            runtime, now=now, schedule=schedule
        )
        schedule_failed = False
    except Exception as error:
        logger.warning("scheduling synchronizations failed: %s", type(error).__name__)
        scheduled = ScheduleReport(queued=(), already_open=())
        schedule_failed = True
    published = await publish_pending_jobs(runtime, publisher=publisher, now=now)
    return RelayPass(
        requeued=requeued,
        scheduled=scheduled,
        schedule_failed=schedule_failed,
        published=published,
    )


async def _relay(settings: Settings, publisher: JobPublisher) -> RelayPass:
    runtime = create_database_runtime(settings)
    schedule = SynchronizationSchedule(
        interval=timedelta(minutes=settings.synchronization_interval_minutes)
    )
    try:
        return await relay_once(
            runtime, publisher=publisher, now=datetime.now(UTC), schedule=schedule
        )
    finally:
        await runtime.dispose()


def main() -> int:
    settings = Settings()
    broker = broker_from_settings(settings)
    try:
        actors = declare_job_actors(
            broker, run_job=JobRunner(settings), kinds=frozenset(HANDLERS)
        )
        done = asyncio.run(_relay(settings, DramatiqJobPublisher(actors)))
    finally:
        broker.close()
    report = done.published
    sys.stdout.write(
        f"requeued {len(done.requeued)}, scheduled {len(done.scheduled.queued)}, "
        f"published {report.published}, skipped {report.skipped}, "
        f"failed {report.failed}, untried {report.untried}\n"
    )
    return 1 if report.failed or done.schedule_failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

**Check:** `uv run pyright src` reports 0 errors.

### Step 5 — Coding step: prove the schedule on a clock the tests set

This step tests every case in "Before you begin" against real PostgreSQL, with `now` passed explicitly.

**In plain words:** each test puts Sources and past Jobs in the database, runs a pass at an exact time, and checks which Jobs exist afterwards. No test waits for real time.

| Test | What it proves |
|---|---|
| `test_a_source_that_never_synchronized_is_due_at_once` | due |
| `test_a_source_is_due_one_interval_after_its_last_run_finished` | 59 minutes: no; one hour: yes |
| `test_a_long_run_is_counted_from_when_it_finished` | block 1 |
| `test_a_disabled_source_is_never_scheduled` | disabled |
| `test_a_source_whose_run_is_still_open_is_not_scheduled_again` | already running: queued, running, or failed with a retry |
| `test_a_missed_schedule_queues_one_run_not_one_per_interval` | missed |
| `test_two_passes_at_once_store_one_run_per_source` | concurrency |
| `test_a_run_names_its_source_by_id_and_carries_no_configuration` | block 3 |
| `test_a_pass_takes_sources_in_a_stable_order_up_to_its_limit`, `..._limit_is_bounded`, `..._needs_utc` | bounds |
| `test_an_unknown_target_id_is_not_a_source` | a Job of a deleted Source makes nothing due |
| `test_the_longest_waiting_sources_go_first_so_none_is_starved` | block 4, starvation |
| `test_a_time_from_a_fast_clock_delays_a_source_by_one_interval_at_most` | block 4, clock |
| `test_a_source_disabled_or_deleted_during_a_pass_gets_no_job` | block 4, mid-pass change |
| `test_a_failed_scheduling_step_does_not_stop_publishing` | block 4, relay |


Create `tests/test_scheduling_integration.py`:

```python
"""Scheduling synchronizations against real PostgreSQL, on a clock the tests set.

Every test passes `now` itself, so "due", "not due yet", and "missed for ten
hours" are exact moments, not waits.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.ingestion import scheduling
from knowledge_service.ingestion.scheduling import (
    ScheduleReport,
    SynchronizationSchedule,
    schedule_due_synchronizations,
)
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
from knowledge_service.persistence.schema import jobs
from knowledge_service.settings import Settings
from knowledge_service.worker.relay import relay_once

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
HOURLY = SynchronizationSchedule(interval=timedelta(hours=1))
FIRST = SourceId.parse("00000000-0000-0000-0000-000000000061")
SECOND = SourceId.parse("00000000-0000-0000-0000-000000000062")
THIRD = SourceId.parse("00000000-0000-0000-0000-000000000063")
# The name of a credential, which a Job must not carry either.
CREDENTIALS_NAME = "vault:confluence/handbook"

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, credentials_reference,"
    " display_name, enabled, created_at, updated_at) VALUES (:source_id,"
    " 'confluence', :location, :credentials, 'Handbook', :enabled, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def add_source(
    runtime: DatabaseRuntime, source_id: SourceId, *, enabled: bool = True
) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            INSERT_SOURCE,
            {
                "source_id": source_id.value,
                "location": f"https://wiki.example.com/spaces/{source_id.value}",
                "credentials": CREDENTIALS_NAME,
                "enabled": enabled,
            },
        )


async def past_job(
    runtime: DatabaseRuntime,
    source_id: SourceId,
    *,
    queued_at: datetime,
    finished_at: datetime | None = None,
    status: JobStatus = JobStatus.SUCCEEDED,
) -> Job:
    """A synchronization Job of `source_id` that reached `status`."""
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=source_id.value,
        queued_at=queued_at,
    )
    async with runtime.transaction() as connection:
        await enqueue_job(connection, job)
        if status is JobStatus.QUEUED:
            return job
        job = job.start(started_at=queued_at)
        if status is JobStatus.SUCCEEDED:
            assert finished_at is not None
            job = job.succeed(finished_at=finished_at)
        elif status is JobStatus.FAILED:
            assert finished_at is not None
            job = job.fail(
                error_code="source_unavailable",
                finished_at=finished_at,
                retry_at=finished_at + timedelta(minutes=5),
            )
        return await save_job(connection, job=job)


async def schedule(runtime: DatabaseRuntime, now: datetime) -> ScheduleReport:
    return await schedule_due_synchronizations(runtime, now=now, schedule=HOURLY)


async def sync_jobs(runtime: DatabaseRuntime, source_id: SourceId) -> list[Job]:
    async with runtime.transaction() as connection:
        ids = await connection.scalars(
            sa.select(jobs.c.job_id)
            .where(
                jobs.c.kind == JobKind.SYNCHRONIZE_SOURCE.value,
                jobs.c.target_id == source_id.value,
            )
            .order_by(jobs.c.queued_at)
        )
        found = [await load_job(connection, job_id=JobId(job_id)) for job_id in ids]
    return [job for job in found if job is not None]


async def test_a_source_that_never_synchronized_is_due_at_once(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)

    report = await schedule(runtime, NOW)

    [job] = await sync_jobs(runtime, FIRST)
    assert report == ScheduleReport(queued=(job.job_id,), already_open=())
    assert (job.status, job.queued_at) == (JobStatus.QUEUED, NOW)


async def test_a_source_is_due_one_interval_after_its_last_run_finished(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)
    await past_job(
        runtime, FIRST, queued_at=NOW - timedelta(minutes=10), finished_at=NOW
    )

    early = await schedule(runtime, NOW + timedelta(minutes=59))
    due = await schedule(runtime, NOW + timedelta(hours=1))

    assert early.queued == ()
    assert len(due.queued) == 1


async def test_a_long_run_is_counted_from_when_it_finished(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)
    # Queued three hours ago, it took two hours and a half.
    await past_job(
        runtime,
        FIRST,
        queued_at=NOW - timedelta(hours=3),
        finished_at=NOW - timedelta(minutes=30),
    )

    assert (await schedule(runtime, NOW)).queued == ()
    assert len((await schedule(runtime, NOW + timedelta(minutes=30))).queued) == 1


async def test_a_disabled_source_is_never_scheduled(runtime: DatabaseRuntime) -> None:
    await add_source(runtime, FIRST, enabled=False)

    report = await schedule(runtime, NOW + timedelta(days=30))

    assert report.queued == ()
    assert await sync_jobs(runtime, FIRST) == []


@pytest.mark.parametrize(
    "status", [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED]
)
async def test_a_source_whose_run_is_still_open_is_not_scheduled_again(
    runtime: DatabaseRuntime, status: JobStatus
) -> None:
    await add_source(runtime, FIRST)
    # Queued, running, or failed with a retry planned: still open.
    open_job = await past_job(
        runtime,
        FIRST,
        queued_at=NOW - timedelta(hours=5),
        finished_at=NOW - timedelta(hours=4),
        status=status,
    )

    report = await schedule(runtime, NOW)

    assert report.queued == ()
    assert [job.job_id for job in await sync_jobs(runtime, FIRST)] == [open_job.job_id]


async def test_a_missed_schedule_queues_one_run_not_one_per_interval(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)
    # The scheduler was down for ten hours: ten intervals were missed.
    await past_job(
        runtime,
        FIRST,
        queued_at=NOW - timedelta(hours=10, minutes=5),
        finished_at=NOW - timedelta(hours=10),
    )

    first = await schedule(runtime, NOW)
    second = await schedule(runtime, NOW)

    assert len(first.queued) == 1
    assert second.queued == ()
    assert len(await sync_jobs(runtime, FIRST)) == 2


async def test_two_passes_at_once_store_one_run_per_source(
    runtime: DatabaseRuntime,
) -> None:
    for source_id in (FIRST, SECOND):
        await add_source(runtime, source_id)

    reports = await asyncio.gather(schedule(runtime, NOW), schedule(runtime, NOW))

    stored = [
        job.job_id for s in (FIRST, SECOND) for job in await sync_jobs(runtime, s)
    ]
    assert len(stored) == 2
    # Each Job was stored by exactly one pass; the other found it or skipped it.
    assert sum(len(r.queued) for r in reports) == 2
    reported = {job_id for r in reports for job_id in r.queued + r.already_open}
    assert reported <= set(stored)


async def test_a_run_names_its_source_by_id_and_carries_no_configuration(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)

    [job_id] = (await schedule(runtime, NOW)).queued

    async with runtime.transaction() as connection:
        row = (
            (
                await connection.execute(
                    sa.select(jobs).where(jobs.c.job_id == job_id.value)
                )
            )
            .mappings()
            .one()
        )
    assert row["kind"] == JobKind.SYNCHRONIZE_SOURCE.value
    assert row["target_id"] == FIRST.value
    stored = " ".join(str(value) for value in row.values())
    assert CREDENTIALS_NAME not in stored
    assert "wiki.example.com" not in stored


async def test_a_pass_takes_sources_in_a_stable_order_up_to_its_limit(
    runtime: DatabaseRuntime,
) -> None:
    for source_id in (THIRD, FIRST, SECOND):
        await add_source(runtime, source_id)

    first = await schedule_due_synchronizations(
        runtime, now=NOW, schedule=HOURLY, limit=2
    )
    second = await schedule_due_synchronizations(
        runtime, now=NOW, schedule=HOURLY, limit=2
    )

    assert [len(first.queued), len(second.queued)] == [2, 1]
    assert [job.target_id for job in await sync_jobs(runtime, THIRD)] == [THIRD.value]
    queued_first = {
        job.target_id
        for source_id in (FIRST, SECOND)
        for job in await sync_jobs(runtime, source_id)
        if job.job_id in first.queued
    }
    assert queued_first == {FIRST.value, SECOND.value}


@pytest.mark.parametrize("limit", [0, 1_001, True, 2.0])
async def test_the_pass_limit_is_bounded(
    runtime: DatabaseRuntime, limit: object
) -> None:
    with pytest.raises(ValueError, match="limit"):
        await schedule_due_synchronizations(
            runtime,
            now=NOW,
            schedule=HOURLY,
            limit=limit,  # pyright: ignore[reportArgumentType]
        )


async def test_the_pass_needs_utc(runtime: DatabaseRuntime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        await schedule(runtime, datetime(2026, 11, 20, 9, 0))


async def test_an_unknown_target_id_is_not_a_source(runtime: DatabaseRuntime) -> None:
    # A synchronization Job whose Source was deleted does not make a Source due.
    await past_job(
        runtime,
        SourceId(uuid.uuid4()),
        queued_at=NOW - timedelta(hours=5),
        finished_at=NOW - timedelta(hours=4),
    )

    assert (await schedule(runtime, NOW)).queued == ()


async def test_the_longest_waiting_sources_go_first_so_none_is_starved(
    runtime: DatabaseRuntime,
) -> None:
    sources = [
        SourceId.parse(f"00000000-0000-0000-0000-0000000000{n}") for n in range(70, 75)
    ]
    for source_id in sources:
        await add_source(runtime, source_id)
    ever_queued: set[SourceId] = set()

    for hours in (0, 2, 4):
        report = await schedule_due_synchronizations(
            runtime, now=NOW + timedelta(hours=hours), schedule=HOURLY, limit=2
        )
        # Each run finishes a minute later, so its Source is due again soon.
        async with runtime.transaction() as connection:
            for job_id in report.queued:
                job = await load_job(connection, job_id=job_id)
                assert job is not None
                ended = job.start(started_at=job.queued_at).succeed(
                    finished_at=job.queued_at + timedelta(minutes=1)
                )
                await save_job(connection, job=ended)
                ever_queued.add(SourceId(job.target_id))

    # Two per pass over three passes reach all five, low IDs or not.
    assert ever_queued == set(sources)


async def test_a_time_from_a_fast_clock_delays_a_source_by_one_interval_at_most(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)
    # A worker whose clock ran a year ahead recorded this finish time.
    await past_job(
        runtime,
        FIRST,
        queued_at=NOW - timedelta(minutes=5),
        finished_at=NOW + timedelta(days=365),
    )

    assert (await schedule(runtime, NOW)).queued == ()
    assert len((await schedule(runtime, NOW + timedelta(hours=1))).queued) == 1


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE sources SET enabled = false WHERE source_id = :source_id",
        "DELETE FROM sources WHERE source_id = :source_id",
    ],
    ids=["disabled", "deleted"],
)
async def test_a_source_disabled_or_deleted_during_a_pass_gets_no_job(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    await add_source(runtime, FIRST)
    load_due = scheduling.load_due_source_ids

    async def then_someone_changes_it(*args: object, **kwargs: object) -> object:
        due = await load_due(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        # Another session commits the change right after this pass read it.
        async with runtime.transaction() as other:
            await other.execute(sa.text(change), {"source_id": FIRST.value})
        return due

    monkeypatch.setattr(scheduling, "load_due_source_ids", then_someone_changes_it)
    report = await schedule(runtime, NOW)

    assert report.queued == ()
    assert await sync_jobs(runtime, FIRST) == []


async def test_a_failed_scheduling_step_does_not_stop_publishing(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    purge = Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_DOCUMENT_CHUNKS,
        target_id=uuid.uuid4(),
        queued_at=NOW,
    )
    async with runtime.transaction() as connection:
        await enqueue_job(connection, purge)
    sent: list[JobId] = []

    class Recording:
        kinds = frozenset({JobKind.PURGE_DOCUMENT_CHUNKS})

        def publish(self, job: Job) -> None:
            sent.append(job.job_id)

    async def broken(*args: object, **kwargs: object) -> ScheduleReport:
        raise RuntimeError("the schedule query timed out")

    monkeypatch.setattr(
        "knowledge_service.worker.relay.schedule_due_synchronizations", broken
    )
    done = await relay_once(runtime, publisher=Recording(), now=NOW, schedule=HOURLY)

    assert done.schedule_failed
    assert sent == [purge.job_id]
    assert done.published.published == 1
```

**Check:** `uv run pytest tests/test_scheduling_integration.py -q -m integration` reports `22 passed`.

### Step 6 — No coding in this step: break it on purpose

This step makes every enabled Source look due, and shows which tests notice.

**Action 1:** in `load_due_source_ids` in `src/knowledge_service/persistence/sources.py`, change:

```python
                candidates.c.last_activity <= now - interval,
```

to:

```python
                sa.true(),
```

**Action 2:** run:

```bash
uv run pytest tests/test_scheduling.py tests/test_scheduling_integration.py -q -m "integration or not integration"
```

You see `3 failed, 31 passed`. The three tests that count the interval fail. The missed-schedule and already-running tests still pass, because an open Job alone already stops a second one: suppression has more than one layer.

**Action 3:** undo the change and run the command again: `34 passed`.

**Check:** compare the count with your Step 1 prediction, and note it in the evidence file.

### Step 7 — No coding in this step: update three documents

This step records the schedule where readers look for it.


**Action 1:** in the architecture, update the `ingestion` and `worker` rows of the module table:

In `docs/architecture.md`, find:

```markdown

| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source` (claims the Source's fence and checks it in every write; an optional `heartbeat` runs after every page), `synchronize_source_exclusively` (`exclusive_sync`, the same pass while holding the Source's lease), `normalize`, `reconcile_document`, `normalize_access` (`grant_normalization`, a Source's `user:<id>` and `group:<id>` principals to Access Grant subjects through its `PrincipalDirectory`, as the same UUIDv5 IDs the Authorization Context uses, and only to subjects and paths a token could carry; distinct, in a stable order, and failing closed: an unmapped or malformed principal grants nothing and is reported), `narrow_access` (a record that fails to normalize keeps its last good version only for readers its old and new access both name) |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs and no tools, where a reply holding a tool call is unreadable; `AnswerSettings` gives the budget), `has_active_content` / `unsourced_links` (`answer_content`, what an Answer may hold whatever a Document told the model: no Markdown image, loading HTML tag, or mermaid block, and only links an Evidence it cites contains; `held_from` marks where a stream stops sending text until the check), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`, with `DatabaseAuditTrail` (`audit_trail`) as its `AuditTrail`: one `question.asked` audit event per stream, committed before the decision's Citations and completion are sent; finds the Evidence the request's reader may see, rereads it as the reader with `evidence_still_visible`, and judges it before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply, has its `CitationCheck` (`CurrentCitations`, each call in a short transaction of its own) reread every cited Chunk as the reader with `confirm_citations`, withdrawing the Answer if one fails, and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `VectorEvidenceSource` (`vector_evidence`, the stream's Evidence from PostgreSQL: checks the reader, embeds the Question, requires one vector from its own model, and runs the vector search with the request's Authorization Context, so eligibility is decided in the same SQL that ranks; no reranking, so its signals carry no score), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |
| `worker` | Queue transport: the Redis broker and the worker process | `build_broker` (`broker`, a Dramatiq `RedisBroker` with JSON messages, the `knowledge-service.v1` namespace, the `maintenance` and `synchronization` queues, a 60-second default time limit, a one-day message age limit, and no Dramatiq retries; `ActorOwnsOptions` drops any limit a message sets for itself, so only its actor's limits apply; Redis calls make one attempt with bounded timeouts), `declare_actors` / `send_ping` (`actors`, the harmless `ping` job, whose token is checked on both sides), `broker_ready` (`health`, a readiness probe that is false, within one overall deadline, whenever Redis does not answer PING or any part of the check fails), `declare_job_actors` / `DramatiqJobPublisher` / `run_stored_job` (`jobs`, one `run_<queue>_job` actor per queue of the kinds in `HANDLERS`, today only `purge_document_chunks`; a message carries one canonical Job ID, and the worker reads the Job from PostgreSQL, runs it only while it is queued, and returns the stored Job as the outcome of a duplicate; `JobRunner` gives each message its own event loop and database engine), `relay` (one pass: `requeue_due_retries`, then `publish_pending_jobs`), and `main`, the module the `dramatiq` command loads |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level. Among them, `roles` holds the Application Roles policy:
```

and replace it with:

```markdown

| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source` (claims the Source's fence and checks it in every write; an optional `heartbeat` runs after every page), `synchronize_source_exclusively` (`exclusive_sync`, the same pass while holding the Source's lease), `schedule_due_synchronizations` (`scheduling`, a synchronization Job for each enabled Source that is due), `normalize`, `reconcile_document`, `normalize_access` (`grant_normalization`, a Source's `user:<id>` and `group:<id>` principals to Access Grant subjects through its `PrincipalDirectory`, as the same UUIDv5 IDs the Authorization Context uses, and only to subjects and paths a token could carry; distinct, in a stable order, and failing closed: an unmapped or malformed principal grants nothing and is reported), `narrow_access` (a record that fails to normalize keeps its last good version only for readers its old and new access both name) |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs and no tools, where a reply holding a tool call is unreadable; `AnswerSettings` gives the budget), `has_active_content` / `unsourced_links` (`answer_content`, what an Answer may hold whatever a Document told the model: no Markdown image, loading HTML tag, or mermaid block, and only links an Evidence it cites contains; `held_from` marks where a stream stops sending text until the check), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`, with `DatabaseAuditTrail` (`audit_trail`) as its `AuditTrail`: one `question.asked` audit event per stream, committed before the decision's Citations and completion are sent; finds the Evidence the request's reader may see, rereads it as the reader with `evidence_still_visible`, and judges it before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply, has its `CitationCheck` (`CurrentCitations`, each call in a short transaction of its own) reread every cited Chunk as the reader with `confirm_citations`, withdrawing the Answer if one fails, and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `VectorEvidenceSource` (`vector_evidence`, the stream's Evidence from PostgreSQL: checks the reader, embeds the Question, requires one vector from its own model, and runs the vector search with the request's Authorization Context, so eligibility is decided in the same SQL that ranks; no reranking, so its signals carry no score), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |
| `worker` | Queue transport: the Redis broker and the worker process | `build_broker` (`broker`, a Dramatiq `RedisBroker` with JSON messages, the `knowledge-service.v1` namespace, the `maintenance` and `synchronization` queues, a 60-second default time limit, a one-day message age limit, and no Dramatiq retries; `ActorOwnsOptions` drops any limit a message sets for itself, so only its actor's limits apply; Redis calls make one attempt with bounded timeouts), `declare_actors` / `send_ping` (`actors`, the harmless `ping` job, whose token is checked on both sides), `broker_ready` (`health`, a readiness probe that is false, within one overall deadline, whenever Redis does not answer PING or any part of the check fails), `declare_job_actors` / `DramatiqJobPublisher` / `run_stored_job` (`jobs`, one `run_<queue>_job` actor per queue of the kinds in `HANDLERS`, today only `purge_document_chunks`; a message carries one canonical Job ID, and the worker reads the Job from PostgreSQL, runs it only while it is queued, and returns the stored Job as the outcome of a duplicate; `JobRunner` gives each message its own event loop and database engine), `relay` (one pass: `requeue_due_retries`, `schedule_due_synchronizations`, then `publish_pending_jobs`), and `main`, the module the `dramatiq` command loads |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level. Among them, `roles` holds the Application Roles policy:
```

and add the scheduling paragraph after the leases paragraph:

In `docs/architecture.md`, find:

```markdown
`SourceUnavailable`, so a Job that meets one is retried later, with a new
observation time.

`telemetry` decides what leaves the process as logs. `configure_telemetry`
installs one root handler that writes JSON lines, and its `TelemetryFilter`
keeps only the fields `TELEMETRY_FIELDS` lists, with plain values, only this
```

and replace it with:

```markdown
`SourceUnavailable`, so a Job that meets one is retried later, with a new
observation time.

`schedule_due_synchronizations` (`ingestion.scheduling`), run by every
`make worker-relay` pass after the retries, queues one `synchronize_source`
Job for each enabled Source with no open synchronization Job whose last one
finished, or was queued if it never finished, at least one interval ago
(`synchronization_interval_minutes`, an hour by default). A Source that
never synchronized is due at once; a disabled one never is. A schedule
missed while the relay was not running queues one Job, not one per missed
interval, and two passes at once still store one Job per Source through the
open-intent index. When more Sources are due than a pass may queue, the
longest-waiting go first, so none is starved; a finish time from a clock that
ran ahead is ignored, so it cannot silence a Source; and the pass rechecks,
under a share lock, that each Source still exists and is enabled before it
queues its Job. A Source with an open synchronization Job is not scheduled
again until that Job ends, so a Job stuck running waits for M08-T08. A
failure while scheduling is logged and makes the relay exit 1, but the relay
still publishes. The Job carries the Source's ID only: its handler
(M08-T10) reads the location, credentials reference, and checkpoint from
PostgreSQL when it runs, so no configuration or secret travels through
Redis. Until then the Jobs wait queued and unpublished, as no worker runs
that kind yet.

`telemetry` decides what leaves the process as logs. `configure_telemetry`
installs one root handler that writes JSON lines, and its `TelemetryFilter`
keeps only the fields `TELEMETRY_FIELDS` lists, with plain values, only this
```

**Action 2:** in the operations guide, say the relay also schedules synchronizations:

In `docs/operations.md`, find:

```markdown

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. A failed Job comes back only through `make worker-relay`, which queues due retries before it publishes, so run it on a schedule. A synchronization pass takes its Source's lease in Redis first; while Redis is down no pass starts, and its Job is retried later. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

and replace it with:

```markdown

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. A failed Job comes back, and each enabled Source's next synchronization is queued, only through `make worker-relay`, which queues due retries and due synchronizations before it publishes, so run it on a schedule more often than `KNOWLEDGE_SERVICE_SYNCHRONIZATION_INTERVAL_MINUTES` (60 by default). A synchronization pass takes its Source's lease in Redis first; while Redis is down no pass starts, and its Job is retried later. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

**Action 3:** in the threat model, add a row after `Two synchronization passes of one Source run at once`:

In `docs/threat-model.md`, find:

```markdown
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |
| Two synchronization passes of one Source run at once: double Source calls, and an older pass finishing late re-grants a removed reader, rolls content back, or tombstones what a newer pass saw | `synchronize_source_exclusively` holds the Source's Redis lease, with an owner token, renewed after every page, so a second pass gets `SourceBusy` before reading; a crashed holder's lease expires in two minutes. The Source's fence in PostgreSQL decides who may write: every pass claims it with its observation time, and every narrowing, reconciliation, and tombstone checks it under a share lock on the Source's row, so after a newer pass claims it nothing of an older pass lands; an observation more than five minutes in the future is refused | Writes an older pass committed before the newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way; a pass observed up to five minutes in the future holds the fence until then |

### Retrieval
```

and replace it with:

```markdown
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |
| Two synchronization passes of one Source run at once: double Source calls, and an older pass finishing late re-grants a removed reader, rolls content back, or tombstones what a newer pass saw | `synchronize_source_exclusively` holds the Source's Redis lease, with an owner token, renewed after every page, so a second pass gets `SourceBusy` before reading; a crashed holder's lease expires in two minutes. The Source's fence in PostgreSQL decides who may write: every pass claims it with its observation time, and every narrowing, reconciliation, and tombstone checks it under a share lock on the Source's row, so after a newer pass claims it nothing of an older pass lands; an observation more than five minutes in the future is refused | Writes an older pass committed before the newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way; a pass observed up to five minutes in the future holds the fence until then |
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

**Action 2:** finish the evidence file: the gate results and the Step 6 count under "Verification", the review findings and known limits under "Review", the design under "Design" and "Decision", and your answers to the reflection questions below. Then mark the task completed in `docs/curriculum/PROGRESS.md` and commit with `m08-t06: schedule incremental synchronization runs`.

**Check:** every gate passes, and `git status` is clean after the commit.

## Reflection questions

- Handbook's last run was queued at 08:00 and finished at 10:30. With an hourly interval, at what time is it due again, and why not at 09:00?
- The relay was down from 00:00 to 10:00. How many synchronization Jobs does the first pass at 10:00 queue for Handbook, and what stops it from queuing ten?
- Why does the Job carry only the Source's ID? Name one problem that carrying the credentials reference would cause, and one that carrying the location would cause.
- 150 Sources are due and a pass may queue 100. Which 50 wait, and what makes sure they are first next time?
- What would you see in the `jobs` table that tells you the schedule is not suppressing redundant Jobs?

## Completion checklist

- [ ] The evidence file has the invariant, the owners, and your predictions, written before the change.
- [ ] Migration `b8c9d0e1f2a3` adds `ix_jobs_synchronization_target`; `load_due_source_ids` and `lock_enabled_sources` exist.
- [ ] `schedule_due_synchronizations` queues one Job per due, enabled Source with the Source's ID only; `tests/test_scheduling.py` (12) passes.
- [ ] `make worker-relay` runs requeue, schedule, publish, and a scheduling failure does not stop publishing.
- [ ] `tests/test_scheduling_integration.py` (22) passes: due, disabled, already running, missed, starvation, clock, mid-pass change.
- [ ] You ran the deliberate break, saw the three interval tests fail, and undid it.
- [ ] `docs/architecture.md`, `docs/operations.md`, and `docs/threat-model.md` describe the schedule.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
