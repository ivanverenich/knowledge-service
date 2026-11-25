# M09-T03 — Implement synchronization administration

Task: [M09-T03 — Implement synchronization administration](../curriculum/milestones/M09-product-interfaces/M09-T03-implement-sync-administration.md)

## What you will do

This lesson lets a content manager start a Source's synchronization by hand and follow its history over HTTP. Until now only the scheduler queued synchronization Jobs, once an hour. After this lesson, pressing "synchronize now" queues one Job, pressing it again returns the same Job, and the Source's page lists every synchronization it ever had.

A concrete example, for the handbook Source from M09-T02:

```text
POST /v1/sources/6f1c.../synchronizations
-> 202, Location: /v1/jobs/01a3...   {"job_id": "01a3...", "status": "queued", ...}

POST /v1/sources/6f1c.../synchronizations      (pressed again)
-> 200, Location: /v1/jobs/01a3...   the same Job, nothing new queued

GET /v1/sources/6f1c.../synchronizations?limit=2
-> {"items": [{"job_id": "01a3...", "status": "running", "latest_attempt": {...}}, ...],
    "next_cursor": "WzEsInN5bmNo..."}
```

The task names five operations: start, status, cancel, retry, and history. Status, retry, and cancel already exist on `/v1/jobs/{job_id}` from M08-T07 and M08-T08. This lesson builds the two missing routes the M09-T01 route table planned, closes one hole in retry, and pins every refusal of all five.

| What the task asks | Where it is done |
|---|---|
| Start a synchronization | Blocks 1 to 3; Steps 3 and 6 |
| Status, cancel, retry over durable Jobs, not broker internals | Blocks 3 and 5; Step 4 (retry) |
| History of a Source's synchronizations | Block 4; Steps 2, 3, and 6 |
| Idempotent start | Block 1; Step 8 |
| Ownership | Block 6; Step 8 |
| Terminal states | Block 1; Step 8 |
| Stable error responses | Block 5; Step 8 |
| Real infrastructure where its behaviour is under test | Step 8 (PostgreSQL for the open-Job index, locks, publishing, and paging) |
| Replay or cancellation explicit, external calls bounded | Blocks 1 and 3; Step 11 |
| Explain what would falsify the design | Step 9 and the reflection questions |

Terms this lesson needs:

- **Synchronization Job:** one stored request to read a Source once, a row in `jobs` with kind `synchronize_source` and the Source's ID as its target. Each try at it is an attempt.
- **Open Job:** a Job that is still going to do something: queued, running, or failed with a retry planned. Succeeded, failed for good, and cancelled are **final states**.
- **Idempotent:** doing it twice has the same effect as once, like pressing a lift button twice.
- **Publish:** send a Redis message naming the Job, so a worker picks it up. The message carries only the Job's ID.
- **UUIDv7:** an ID whose first 48 bits are the moment it was made, in milliseconds, so IDs made later sort later. Every ID in this project is one.

## Before you begin

This section explains each decision the lesson's code depends on. Each block shows a real situation in this project, what happens today, the fact underneath it, and the fix. Read it first: the steps rely on it.

### 1. A start asks for the same work the scheduler asks for, once

A content manager presses "synchronize now" twice in a second, and a colleague presses it from another browser at the same time.

| Moment | Manager, click 1 | Manager, click 2 | Colleague | `jobs` |
|---|---|---|---|---|
| 10:00:00.000 | Insert a queued Job for S | | | J1 queued |
| 10:00:00.300 | | Insert a queued Job for S | | J1 queued |
| 10:00:00.301 | | | Insert a queued Job for S | J1 queued |

Only J1 exists afterwards. The hidden fact is an index from M08-T03: `jobs` has a unique index on `(kind, target_id)` that covers only open Jobs (`WHERE status in ('queued', 'running') or (status = 'failed' and retry_at is not null)`). A second open synchronization Job for S simply cannot be stored. `enqueue_job` inserts with `ON CONFLICT DO NOTHING` and, when nothing was inserted, reads the open Job back:

```sql
INSERT INTO jobs (...) VALUES (...)
ON CONFLICT (kind, target_id) WHERE <open> DO NOTHING
RETURNING job_id;
-- no row: someone else's open Job is there
SELECT * FROM jobs WHERE kind = 'synchronize_source' AND target_id = :s AND <open>;
```

So a start never needs a check of its own. It reports which of the two happened:

| What `enqueue_job` did | Answer | Audit |
|---|---|---|
| stored a new Job | 202 Accepted, `Location: /v1/jobs/{job_id}` | one `job.queued`, the operator as actor |
| found an open Job | 200 OK, the same `Location` and body shape | none |

A client that retries a start after a timeout is therefore safe: the worst case is a 200 naming the Job it already asked for. Once the Job reaches a final state, the index no longer covers it, and the next start queues a new Job:

| State of the Source's last Job | Next start |
|---|---|
| queued, running, failed with a retry planned | 200, that Job |
| succeeded, failed for good, cancelled | 202, a new Job |

Think of a ticket machine at a counter that is already serving you: pressing again prints your current number, not a second one.

### 2. Only an enabled Source starts, and retry checks the same

A Source is disabled when nothing should read it anymore. A start must not queue a Job for it, and must not race a disable.

The start uses the scheduler's lock from M08-T10: it reads the Source with `SELECT ... FOR SHARE` (`lock_enabled_sources`), and M09-T02's disable takes `SELECT ... FOR UPDATE`, which waits for every share lock on the row. So either the Job is stored before the disable, or the start sees the Source disabled and stores nothing.

| Moment | Start | Disable | What the database holds |
|---|---|---|---|
| 10:00:00 | Locks S for share, sees `enabled` | | S enabled |
| 10:00:01 | | `FOR UPDATE` waits | S enabled, share lock held |
| 10:00:02 | Stores Job J, commits | | J queued |
| 10:00:02 | | Lock granted; S disabled | S disabled, J queued |

A missing Source answers 404 `source_not_found`; a disabled one 409 `source_disabled`.

The adversarial review found the same hole one door over. A synchronization Job that failed for good can be retried with `POST /v1/jobs/{job_id}/retry`, and retry never looked at the Source:

| Step | Request | Before the fix | After |
|---|---|---|---|
| 1 | Job J of Source S fails for good | | |
| 2 | Disable S | | |
| 3 | `POST /v1/sources/S/synchronizations` | 409 `source_disabled` | 409 |
| 4 | `POST /v1/jobs/J/retry` | 200, J queued again | 409 `source_disabled`, J stays failed |

How bad: small today, since the worker would read S, find it disabled, and fail J with `source_gone`. But a disabled Source means "read nothing", and the retry door contradicted the start door. Now `retry_job` share-locks the Source of a synchronization Job, exactly as a start does. It checks the Job's own status first, so a retry that would never fit, such as one of a Job that succeeded, still answers `decision_not_allowed` rather than telling the operator to enable the Source.

### 3. Store first, then publish

A start must survive a Redis outage. The order matters:

```python
# Was (what the wrong order would look like): publish, then store
publisher.publish(job)          # Redis has a message for J
await save(job)                 # crash here: a message names a Job that does not exist

# Became: store and audit in one transaction, then try once to publish
started = await start_synchronization(...)      # commits J and job.queued
await publish_job(runtime, job_id=..., publisher=..., now=now)
```

The hidden fact is ADR 0011: Redis only delivers, PostgreSQL is the record. A Job that is stored but not published stays queued with `published_at` empty, and the relay (`make worker-relay`) publishes every such Job later. A Job that is published is never published again by a repeated start, because `publish_job` locks only Jobs still unpublished. A failed send is logged by its error's type only, since its message could quote a URL.

| What failed | What the caller gets | What happens to the Job |
|---|---|---|
| nothing | 202 | stored and published |
| Redis refused the message | 202 | stored; the relay publishes it |
| the database | 503 or 500 | nothing stored, nothing published |

### 4. A history page starts after a Job's ID, because the ID never moves

The history lists a Source's synchronization Jobs, newest first, a page at a time, with the cursor design from M09-T01. The first prototype ordered by `(queued_at, job_id)`, like the decision list. The adversarial review broke it:

| Moment | Operator | `jobs` for Source S (queued_at) |
|---|---|---|
| 09:00 | | J1 09:00, J2 09:01, J3 09:02, J4 09:03, all failed for good |
| 10:00:00 | Page 1: J4, J3; cursor "after J3 at 09:02" | |
| 10:00:05 | An administrator retries J1 | J1's `queued_at` becomes 10:00:05 |
| 10:00:10 | Page 2: Jobs before 09:02 | J2 only; J1 is now above the cursor |

J1 was never listed. The hidden fact: a retry does not make a new Job; it queues the same row again and rewrites its `queued_at` (`Job._queued_again`). The same happens on every automatic retry. A key that moves cannot hold a page boundary.

The fix pages on the Job's ID alone. Every ID here is a UUIDv7, which begins with the moment it was made, so "newest ID first" is "newest made first", and the ID never changes:

```sql
-- Was: the key moves on every retry
WHERE (queued_at, job_id) < (:queued_at, :job_id) ORDER BY queued_at DESC, job_id DESC

-- Became: the key never changes
WHERE job_id < :job_id ORDER BY job_id DESC
```

The cursor now holds only the last Job's ID. Each page reads the Jobs and their newest attempts in one `REPEATABLE READ` snapshot, so a Job that finishes meanwhile is never shown running with an ended attempt, and the newest attempts come from one `DISTINCT ON (job_id)` query rather than one query per Job. The partial index on synchronization Jobs gains the ID as a second column, `(target_id, job_id)`, so a page reads the index in order instead of sorting every Job the Source ever had; an hourly Source collects about 8,700 a year.

Think of a library's accession numbers: a book returned and lent again keeps its number, so a list by number never loses its place.

### 5. Every refusal has one fixed status, code, and message

A client branches on the answer: a script that sees `source_disabled` stops, one that sees `job_running` waits. If a code or message drifted, that script would break silently. So one test pins every refusal of the five operations exactly:

| Operation | Situation | Status | Code | Message |
|---|---|---|---|---|
| start | missing Source | 404 | `source_not_found` | No such Source |
| start | disabled Source | 409 | `source_disabled` | The Source is missing or disabled |
| history | missing Source | 404 | `source_not_found` | No such Source |
| status | missing Job | 404 | `job_not_found` | No such synchronization job |
| retry | missing Job | 404 | `job_not_found` | No such job |
| retry | another Job for the Source open | 409 | `job_already_open` | Another job for this work is open |
| retry | missing or disabled Source, once the Job's status fits | 409 | `source_disabled` | The Source is missing or disabled |
| retry | Job not failed for good | 409 | `decision_not_allowed` | This decision does not fit the job's status |
| cancel | missing Job | 404 | `job_not_found` | No such job |
| cancel | running Job | 409 | `job_running` | The job is still running |
| cancel | finished Job | 409 | `decision_not_allowed` | This decision does not fit the job's status |

Every one also has the `ErrorResponse` shape, `code`, `message`, and `request_id`, and nothing else.

### 6. Who may do what, and whose Jobs a page shows

The route table from M09-T01 already decided the roles:

| Operation | Route | Operation the role must permit | Role |
|---|---|---|---|
| start | `POST /v1/sources/{source_id}/synchronizations` | `start_synchronization_runs` | content-manager |
| history | `GET /v1/sources/{source_id}/synchronizations` | `start_synchronization_runs` | content-manager |
| status | `GET /v1/jobs/{job_id}` | `start_synchronization_runs` | content-manager |
| retry, cancel | `POST /v1/jobs/{job_id}/retry`, `/cancel` | `recover_jobs` | administrator |

Retry and cancel stay with the administrator, because they are recovery decisions over every kind of Job (M08-T08), not everyday synchronization.

"Ownership" here means a Job belongs to its Source. A Source's history holds only Jobs of kind `synchronize_source` aimed at that Source: not another Source's, and not a purge Job that happens to carry the same ID. Status already shows only synchronization Jobs and answers the same 404 for a Job of another kind. And each queued Job's audit event names the operator who asked for it.

### Known limits

- A start cannot choose a full or an incremental pass; the worker decides, as for a scheduled Job.
- `POST .../synchronizations` takes no body and ignores a query string, while `GET` refuses unknown parameters.
- A history cursor is only a position: given to another Source's history, it starts that list after the same ID, which shows nothing the caller may not read anyway.
- UUIDv7 order is creation order by the clock of the machine that made the ID; two machines with skewed clocks may order a little off, but a page never repeats or skips a Job.
- Without a publisher, or when Redis refuses, the Job waits for the next `make worker-relay` pass; nothing runs the relay continuously until the system is composed (M12-T03).
- A caller refused by role gets a 403 that no audit event records, as on every route.

## Step 1 — Read, predict, and see the check fail

No coding in this step.

Read the task file, then Blocks 1 to 6. Write down your answers before you code:

- Four starts for one Source arrive at once. How many Jobs are stored, how many audit events, and which statuses come back?
- A Source's Job failed for good, then the Source was disabled. What does `POST /v1/jobs/{job_id}/retry` answer?
- The history shows J4 and J3 on page 1, then someone retries J1. Does page 2 show J1?

Then run the test file this lesson creates, to see that it does not exist yet:

```bash
uv run pytest tests/test_synchronization_administration.py -q
```

Expected: `ERROR: file or directory not found: tests/test_synchronization_administration.py`, `no tests ran`, exit code 4.

## Step 2 — Read a Source's Jobs and their newest attempts

Coding step.

**In plain words:** the history needs two reads the project does not have yet. One returns the synchronization Jobs of one Source a page at a time, newest made first. The other returns the newest attempt of many Jobs in a single query, so a page of fifty Jobs costs two queries, not fifty-one.

| Function | File | What it does |
|---|---|---|
| `load_target_jobs` | `persistence/jobs.py` | Jobs of one kind and target, `ORDER BY job_id DESC`, after a Job's ID |
| `load_latest_attempts` | `persistence/job_attempts.py` | `SELECT DISTINCT ON (job_id) ... ORDER BY job_id, attempt DESC` |
| `ix_jobs_synchronization_target` | `schema.py` and a migration | `(target_id, job_id)` for synchronization Jobs |

In `src/knowledge_service/persistence/jobs.py`, add before `lock_running_job`:

```python
async def load_target_jobs(
    connection: AsyncConnection,
    *,
    kind: JobKind,
    target_id: UUID,
    limit: int,
    after: JobId | None = None,
) -> tuple[Job, ...]:
    """Up to `limit` Jobs of `kind` for one target, the newest made first.

    A Job's ID is a UUIDv7, which starts with the time it was made and never
    changes; `queued_at` does, on every retry. So the order and `after`, the
    Job the list starts after, use the ID alone, and a retry between two
    pages never moves a Job past the place a page ended.
    """
    statement = sa.select(jobs).where(
        jobs.c.kind == kind.value, jobs.c.target_id == target_id
    )
    if after is not None:
        statement = statement.where(jobs.c.job_id < after.value)
    statement = statement.order_by(jobs.c.job_id.desc()).limit(limit)
    records = (await connection.execute(statement)).mappings().all()
    return tuple(job_from_record(dict(record)) for record in records)
```

In `src/knowledge_service/persistence/job_attempts.py`, add this import beside the other `sqlalchemy` imports:

```python
from sqlalchemy.dialects import postgresql
```

and add before `begin_attempt`:

```python
async def load_latest_attempts(
    connection: AsyncConnection, *, job_ids: Iterable[JobId]
) -> dict[JobId, JobProgress]:
    """The newest attempt of each of `job_ids` that ever started one, in one query."""
    values = [job_id.value for job_id in job_ids]
    if not values:
        return {}
    statement = (
        sa.select(job_attempts)
        .where(job_attempts.c.job_id.in_(values))
        .ext(postgresql.distinct_on(job_attempts.c.job_id))
        .order_by(job_attempts.c.job_id, job_attempts.c.attempt.desc())
    )
    records = (await connection.execute(statement)).mappings().all()
    return {
        JobId(cast(UUID, record["job_id"])): _progress_from(dict(record))
        for record in records
    }
```

`DISTINCT ON` keeps the first row of each `job_id` in the `ORDER BY`, which is the highest attempt. `postgresql.distinct_on` is the current SQLAlchemy spelling; passing a column to `.distinct()` still works but is deprecated.

Give the history an index in ID order. In `src/knowledge_service/persistence/schema.py`, the synchronization target index of `jobs` becomes:

```python
    # Finds each Source's synchronization Jobs for the schedule, and pages a
    # Source's history in ID order without sorting every Job it ever had.
    sa.Index(
        "ix_jobs_synchronization_target",
        "target_id",
        "job_id",
        postgresql_where=sa.text("kind = 'synchronize_source'"),
    ),
    sa.Index(
        "ix_jobs_due_retry",
        "retry_at",
        "job_id",
        postgresql_where=sa.text("retry_at is not null"),
    ),
    # Finds queued and running Jobs that recovery may find stuck.
```

and create `migrations/versions/b4c5d6e7f8a9_synchronization_history_index.py`:

```python
"""synchronization history index

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-11-27 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a9"
down_revision: str | Sequence[str] | None = "a3b4c5d6e7f8"

SYNCHRONIZATIONS = sa.text("kind = 'synchronize_source'")


def upgrade() -> None:
    # The ID as a second column lets a history page read the index in order
    # instead of sorting every synchronization Job of the Source.
    op.drop_index("ix_jobs_synchronization_target", table_name="jobs")
    op.create_index(
        "ix_jobs_synchronization_target",
        "jobs",
        ["target_id", "job_id"],
        postgresql_where=SYNCHRONIZATIONS,
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_synchronization_target", table_name="jobs")
    op.create_index(
        "ix_jobs_synchronization_target",
        "jobs",
        ["target_id"],
        postgresql_where=SYNCHRONIZATIONS,
    )
```

The index still starts with `target_id`, so the scheduler's lookups use it as before.

Check: `uv run pyright src/knowledge_service/persistence` reports 0 errors.

## Step 3 — Write the synchronization administration module

Coding step.

**In plain words:** this file starts a synchronization and reads the history. A start locks the Source the way the scheduler does, refuses a Source that is missing or turned off, and asks for one Job, getting back the open one if there is any. It records who asked only when a Job was really queued, saves everything, and only then tries to send the message. The history reads a page of the Source's Jobs with their newest attempts, all from one consistent view.

| Part | What it does |
|---|---|
| `SourceDisabled` | in `source_administration.py`: the Source is missing or off |
| `SynchronizationStart` | the Job's report, and whether this start queued it |
| `start_synchronization` | share-lock, 404 or 409 refusals, `enqueue_job`, `job.queued` only when queued |
| `load_synchronization_history` | Source must exist; Jobs and newest attempts in one `REPEATABLE READ` snapshot |
| `history_cursor`, `history_position` | the cursor holds only the last Job's ID, in one spelling |
| `DatabaseSynchronizations` | the store the app uses: start, then one publish try; history |

In `src/knowledge_service/source_administration.py`, add before `SourceNeedsRepair`:

```python
class SourceDisabled(Exception):
    """The Source is turned off, so nothing may synchronize it.

    A retry also raises it for a Job whose Source is gone: a retry names the
    Job, not the Source, so its missing Source is not a missing resource.
    """
```

It lives with the other Source errors because Step 4's retry raises it too.

Create `src/knowledge_service/synchronization_administration.py`:

```python
"""Starting a Source's synchronization by hand, and reading its history.

A start asks for the same work the scheduler asks for: one synchronization
Job for one Source. While the Source has an open Job, queued, running, or
failed with a retry planned, a start returns that Job instead of queueing a
second, so pressing the button twice, or two operators at once, queue one
Job. Only an enabled Source can be started; the check locks the Source's
row the way the scheduler does, so disabling it waits for the start to
commit, and a start never queues a Job for a Source disabled after it read.

Everything an operator sees comes from PostgreSQL, the record of what was
asked for and how it ended; Redis only delivers. A start commits its Job
and its audit event first, then tries once to publish the Job; if that
fails, the relay publishes it later (`job_dispatch`).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import sqlalchemy as sa

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import AuditEventId, JobId, SourceId, UserId
from knowledge_service.job_dispatch import JobPublisher, publish_job
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import Job, JobKind
from knowledge_service.pagination import InvalidPage, decode_cursor, encode_cursor
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.job_attempts import (
    load_latest_attempt,
    load_latest_attempts,
)
from knowledge_service.persistence.jobs import enqueue_job, load_target_jobs
from knowledge_service.persistence.sources import (
    load_source_record,
    lock_enabled_sources,
)
from knowledge_service.source_administration import SourceDisabled, SourceNotFound

logger = logging.getLogger(__name__)

MAX_HISTORY_PAGE: Final = 500
_HISTORY_LIST = "synchronizations"
_HISTORY_CURSOR_KEYS = frozenset({"job_id"})


@dataclass(frozen=True, slots=True)
class SynchronizationStart:
    """The Job a start answers with, and whether this start queued it."""

    report: JobReport
    queued: bool


async def start_synchronization(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> SynchronizationStart:
    """Queue a synchronization Job for an enabled Source, or return its open one.

    Raises `SourceNotFound` for a missing Source and `SourceDisabled` for one
    turned off. Only a start that queues a Job records `job.queued`.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        enabled = await lock_enabled_sources(connection, source_ids={source_id})
        if source_id not in enabled:
            if await load_source_record(connection, source_id=source_id) is None:
                raise SourceNotFound("no source has this ID")
            raise SourceDisabled("the source is disabled")
        enqueued = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=source_id.value,
                queued_at=now,
            ),
        )
        job = enqueued.job
        if enqueued.created:
            await record_audit_event(
                connection,
                _queued_event(job, operator=operator, at=now, request_id=request_id),
            )
        latest = await load_latest_attempt(connection, job_id=job.job_id)
    return SynchronizationStart(
        report=JobReport(job=job, latest_attempt=latest), queued=enqueued.created
    )


async def load_synchronization_history(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    limit: int,
    after: JobId | None = None,
) -> tuple[JobReport, ...]:
    """A Source's synchronization Jobs, newest made first, each with its newest attempt.

    Raises `SourceNotFound` for a Source that does not exist. Jobs and
    attempts are read from one snapshot, so a Job that finishes meanwhile is
    never shown as running with an ended attempt.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_HISTORY_PAGE:
        raise ValueError(f"limit must be an integer from 1 to {MAX_HISTORY_PAGE}")
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        )
        if await load_source_record(connection, source_id=source_id) is None:
            raise SourceNotFound("no source has this ID")
        found = await load_target_jobs(
            connection,
            kind=JobKind.SYNCHRONIZE_SOURCE,
            target_id=source_id.value,
            limit=limit,
            after=after,
        )
        latest = await load_latest_attempts(
            connection, job_ids=[job.job_id for job in found]
        )
    return tuple(
        JobReport(job=job, latest_attempt=latest.get(job.job_id)) for job in found
    )


def history_cursor(job: Job) -> str:
    """The cursor of the history page that starts after `job`."""
    return encode_cursor(_HISTORY_LIST, {"job_id": str(job.job_id.value)})


def history_position(cursor: str) -> JobId:
    """Which Job a `history_cursor` says the next page starts after."""
    position = decode_cursor(cursor, listing=_HISTORY_LIST, keys=_HISTORY_CURSOR_KEYS)
    job_text = position["job_id"]
    try:
        job_id = UUID(job_text)
    except ValueError:
        raise InvalidPage("the cursor is not one this API gave") from None
    if str(job_id) != job_text:
        raise InvalidPage("the cursor is not one this API gave")
    return JobId(job_id)


def _queued_event(
    job: Job, *, operator: UserId, at: datetime, request_id: UUID | None
) -> AuditEvent:
    """Who asked for which Job; the Source is the Job's target, never its settings."""
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=at,
        actor_id=operator,
        action=AuditAction.JOB_QUEUED,
        target_kind=AuditTargetKind.JOB,
        target_id=job.job_id.value,
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=request_id,
        run_id=None,
        metadata={"kind": job.kind.value},
    )


def _require_utc(now: datetime) -> None:
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DatabaseSynchronizations:
    """Synchronization starts and history in PostgreSQL, at the clock's time.

    With a `publisher`, a start then tries once to send its Job's message;
    without one, or when the send fails, the relay sends it later.
    """

    runtime: DatabaseRuntime
    publisher: JobPublisher | None = None
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def start(
        self, *, source_id: SourceId, operator: UserId, request_id: UUID
    ) -> SynchronizationStart:
        now = self.clock()
        started = await start_synchronization(
            self.runtime,
            source_id=source_id,
            operator=operator,
            now=now,
            request_id=request_id,
        )
        if self.publisher is not None:
            try:
                await publish_job(
                    self.runtime,
                    job_id=started.report.job.job_id,
                    publisher=self.publisher,
                    now=now,
                )
            except Exception as error:
                # The Job is committed; the relay will publish it. Only the
                # type: a message could quote a URL or a secret.
                logger.warning(
                    "synchronization stored but not published: %s",
                    type(error).__name__,
                )
        return started

    async def history(
        self,
        *,
        source_id: SourceId,
        limit: int,
        after: JobId | None = None,
    ) -> tuple[JobReport, ...]:
        return await load_synchronization_history(
            self.runtime, source_id=source_id, limit=limit, after=after
        )
```

`start_synchronization` reads the latest attempt in the same transaction, because a start that finds a running Job should show how far that Job has got.

Check: `uv run pyright src/knowledge_service/synchronization_administration.py` reports 0 errors.

## Step 4 — Make retry check the Source

Coding step.

**In plain words:** an administrator's retry of a synchronization Job now checks its Source exactly as a start does. If the Source is missing or turned off, nothing is queued and the answer is the same 409 a start gives.

In `src/knowledge_service/job_decisions.py`, change the imports:

```python
from knowledge_service.identifiers import AuditEventId, JobId, SourceId, UserId
from knowledge_service.jobs import InvalidJob, Job, JobKind
from knowledge_service.persistence.jobs import load_job, save_job
from knowledge_service.persistence.sources import lock_enabled_sources
from knowledge_service.source_administration import SourceDisabled
```

and in `retry_job`, extend the docstring and check the Source after the Job's own status:

```python
    refused with `JobIntentAlreadyOpen`, so one piece of work never has two
    open Jobs. A synchronization Job whose Source is missing or disabled is
    refused with `SourceDisabled`, after the Job's own status is checked.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        job = await _lock(connection, job_id=job_id)
        # The Job's status first: a retry that never fits says so, whatever
        # state its Source is in.
```

The Job is locked and its status checked first, so a retry that never fits says so; only then is the Source share-locked. A disable that holds `FOR UPDATE` on the Source makes the retry wait, so a retry commits either before the disable or not at all.

Check: `uv run pytest tests/test_job_recovery_integration.py -q -m integration` still passes: its Jobs belong to an enabled Source.

## Step 5 — Add the history page model

Coding step.

**In plain words:** a history page has the same shape as every list in the API: the items, then the cursor of the next page or nothing.

In `src/knowledge_service/contracts.py`, add before `OperatorJob`:

```python
class SynchronizationHistory(BaseModel):
    """A page of a Source's synchronization Jobs, newest first.

    `next_cursor` starts the next page; None on the last one.
    """

    items: list[SynchronizationJobReport]
    next_cursor: str | None
```

Each item is the `SynchronizationJobReport` that `GET /v1/jobs/{job_id}` already answers with, so a client reads a Job the same way from both routes.

## Step 6 — Serve the two routes

Coding step.

**In plain words:** the app gets the start and the history. Both check the caller and their role first, then the Source's ID and the page, then ask the store. A start answers 202 when it queued a Job and 200 when it found one open, with the Job's address either way. The retry route learns to turn the new refusal into the same 409 a start gives.

| Route | Answer |
|---|---|
| `POST /v1/sources/{source_id}/synchronizations` | 202 or 200 with `Location`; 404; 409 `source_disabled`; 422 bad ID |
| `GET /v1/sources/{source_id}/synchronizations` | 200 page; 404; 422 bad ID, `limit`, `cursor`, or parameter |
| `POST /v1/jobs/{job_id}/retry` | also 409 `source_disabled` |

In `src/knowledge_service/app.py`, the `knowledge_service.contracts` import gains `SynchronizationHistory`:

```python
from knowledge_service.contracts import (
    ErrorResponse,
    JobAttemptProgress,
    JobsNeedingDecision,
    OperatorJob,
    Question,
    SourceChanges,
    SourcePage,
    SourceRegistration,
    SourceView,
    SynchronizationHistory,
    SynchronizationJobReport,
)
```

`SourceDisabled` joins the `source_administration` import, and a new import follows it:

```python
from knowledge_service.source_administration import (
    InvalidSourceSettings,
    SourceDefinition,
    SourceDisabled,
    SourceExists,
    SourceNeedsRepair,
    SourceNotFound,
    source_cursor,
    source_position,
)
from knowledge_service.synchronization_administration import (
    SynchronizationStart,
    history_cursor,
    history_position,
)
```

Add the store's protocol before `source_view`:

```python
class Synchronizations(Protocol):
    """A Source's synchronizations as an operator starts and follows them.

    `DatabaseSynchronizations` is the implementation. Both raise
    `SourceNotFound` for a missing Source; `start` raises `SourceDisabled`
    for one turned off, and returns the open Job when there is one.
    """

    async def start(
        self, *, source_id: SourceId, operator: UserId, request_id: UUID
    ) -> SynchronizationStart: ...

    async def history(
        self, *, source_id: SourceId, limit: int, after: JobId | None = None
    ) -> tuple[JobReport, ...]: ...
```

Give `create_app` the store, after `sources`:

```python
    synchronizations: Synchronizations | None = None,
```

In `decide`, the shared body of retry and cancel, add one refusal after `JobStillRunning`:

```python
        except JobStillRunning:
            return _conflict("job_running", "The job is still running", request_id)
        except SourceDisabled:
            return _source_disabled(request_id)
```

Add the two routes at the end of `create_app`, before `return app`:

```python
    @app.post(
        "/v1/sources/{source_id}/synchronizations",
        status_code=202,
        response_model=SynchronizationJobReport,
        responses={
            200: {
                "model": SynchronizationJobReport,
                "description": "The Source's open Job; nothing new was queued",
            },
            **error_responses(404, 409),
        },
    )
    async def start_synchronization(
        request: Request, source_id: Identifier
    ) -> JSONResponse:
        # Who, then whether they may, then which Source.
        caller = await authenticate(request)
        require(caller.roles, Operation.START_SYNCHRONIZATION_RUNS)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if synchronizations is None:
            return _jobs_unavailable(request_id)
        try:
            started = await synchronizations.start(
                source_id=SourceId.parse(source_id),
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except SourceNotFound:
            return _source_not_found(request_id)
        except SourceDisabled:
            return _source_disabled(request_id)
        except Exception as error:
            logger.error("synchronization start failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        job_id = started.report.job.job_id.value
        # 202: queued now; 200: the open Job, so a repeated start is harmless.
        return JSONResponse(
            status_code=202 if started.queued else 200,
            content=synchronization_job_report(started.report).model_dump(mode="json"),
            headers={"Location": f"/v1/jobs/{job_id}"},
        )

    @app.get(
        "/v1/sources/{source_id}/synchronizations",
        response_model=SynchronizationHistory,
        responses=error_responses(404),
        openapi_extra={"parameters": PAGE_PARAMETERS},
    )
    async def list_synchronizations(
        request: Request, source_id: Identifier
    ) -> SynchronizationHistory | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.START_SYNCHRONIZATION_RUNS)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        try:
            limit, cursor = page_request(request)
            after = None if cursor is None else history_position(cursor)
        except InvalidPage:
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if synchronizations is None:
            return _jobs_unavailable(request_id)
        try:
            # One more than the page, to learn whether another page follows.
            reports = await synchronizations.history(
                source_id=SourceId.parse(source_id), limit=limit + 1, after=after
            )
        except asyncio.CancelledError:
            raise
        except SourceNotFound:
            return _source_not_found(request_id)
        except Exception as error:
            logger.error("synchronization history failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        page = reports[:limit]
        return SynchronizationHistory(
            items=[synchronization_job_report(report) for report in page],
            next_cursor=history_cursor(page[-1].job) if len(reports) > limit else None,
        )
```

and the shared refusal before `_conflict`:

```python
def _source_disabled(request_id: UUID) -> JSONResponse:
    return _conflict("source_disabled", "The Source is missing or disabled", request_id)
```

The start route builds its own `JSONResponse` because the status depends on the result: FastAPI's `status_code=202` only sets the default and documents it, and the 200 case is documented through `responses`.

Check: `uv run pyright src/knowledge_service/app.py` reports 0 errors.

## Step 7 — Mark the routes served and update the existing tests

Coding step.

**In plain words:** the route table still lists the two routes as planned, and the OpenAPI and role tests now fail because the app serves them. This step marks them served, tells the OpenAPI test which extra errors they document, and gives the role test a fake synchronization store, so it proves each route admits exactly the content-manager role.

In `src/knowledge_service/api_routes.py`, change `_PLANNED` to `_SERVED` in the two rows with task `"M09-T03"`:

```python
    Route(
        _POST,
        "/v1/sources/{source_id}/synchronizations",
        Operation.START_SYNCHRONIZATION_RUNS,
        _SERVED,
        "M09-T03",
        "Start a synchronization, or return the one already open",
    ),
    Route(
        _GET,
        "/v1/sources/{source_id}/synchronizations",
        Operation.START_SYNCHRONIZATION_RUNS,
        _SERVED,
        "M09-T03",
        "List a Source's synchronization Jobs",
        paginated=True,
    ),
```

In `tests/test_api_routes.py`, add to `ROUTE_ERRORS`:

```python
    ("POST", "/v1/sources/{source_id}/synchronizations"): {404, 409},
    ("GET", "/v1/sources/{source_id}/synchronizations"): {404},
```

In `tests/test_app_authentication.py`, add the import beside the other `knowledge_service` imports:

```python
from knowledge_service.synchronization_administration import SynchronizationStart
```

add the fake store and wire it into `full_app`:

```python
class Synchronizing:
    """A synchronization store that records each request, and finds no Source."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def start(self, **request: Any) -> SynchronizationStart:
        self.calls.append(request)
        raise SourceNotFound("no source has this ID")

    async def history(self, **request: Any) -> tuple[JobReport, ...]:
        self.calls.append(request)
        raise SourceNotFound("no source has this ID")


def full_app(
    source: Source,
    tokens: dict[str, Any] | None = None,
    conversations: Deleting | None = None,
    jobs: Reading | None = None,
    decisions: Deciding | None = None,
    sources: Configuring | None = None,
    synchronizations: Synchronizing | None = None,
) -> Any:
    """The app with every endpoint wired, so every route is real."""
    return create_app(
        streamer=streamer(Model(), source=source),
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
        conversations=conversations or Deleting(),
        job_reports=jobs or Reading(),
        job_decisions=decisions or Deciding(),
        sources=sources or Configuring(),
        synchronizations=synchronizations or Synchronizing(),
    )
```

and in `test_each_protected_route_admits_exactly_the_roles_that_permit_it`, make the app with the new store and count its calls:

```python
    jobs, decisions, sources = Reading(), Deciding(), Configuring()
    synchronizations = Synchronizing()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(
        source,
        {"token": token},
        conversations,
        jobs,
        decisions,
        sources,
        synchronizations,
    )
```

```python
    reached = (
        source.readers
        or conversations.calls
        or jobs.calls
        or decisions.calls
        or sources.calls
        or synchronizations.calls
    )
```

Check:

```bash
uv run pytest tests/test_api_routes.py tests/test_app_authentication.py -q
```

Expected: all pass.

## Step 8 — Write the new tests

Coding step.

**In plain words:** two new test files prove the contract. The first runs without services: it calls the routes over fake stores, checks 202 against 200, the history's pages and cursors, that the role comes before the ID or page, and pins every refusal of the five operations. The second runs on real PostgreSQL, where the open-Job index, the locks, publishing, retries, and paging actually happen.

| File | What it proves |
|---|---|
| `tests/test_synchronization_administration.py` | 202 with `Location`; 200 with the same Job; bad IDs and pages refused before the store; role before ID or page; no leak when a start or a history read fails; pages to the end, a full last page; cursor spelling; one fixed status, code, and message per refusal |
| `tests/test_synchronization_administration_integration.py` | one Job and one `job.queued` with the operator; four starts at once over HTTP give one 202 and three 200s for one Job; running and retry-planned Jobs returned with 200; succeeded, failed for good, and cancelled let a new Job start with 202; retry of an old Job while a new one is open is 409; disabled or missing Source queues nothing; publish once, and a refused send loses nothing; history holds only its Source's synchronization Jobs; 404 history; pages never repeat or skip; a newest attempt shown; a Job retried between pages listed once; retry refused for a disabled Source, but `decision_not_allowed` first when the Job's status does not fit; a disable waits for a start holding the Source; the history index is `(target_id, job_id)` |

Create `tests/test_synchronization_administration.py`:

```python
"""Synchronization administration over HTTP, with fake stores.

A start answers 202 when it queued a Job and 200 when the Source already had
an open one, and both name the Job. The history is a Source's Jobs a page
at a time. Every refusal of the five operations, start, status, history,
retry, and cancel, has one fixed status, code, and message: a client may
branch on them, so a test pins each one.
"""

import logging
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest

from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.app import (
    JobDecisions,
    JobReports,
    Synchronizations,
    create_app,
)
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.identity.context import Caller
from knowledge_service.job_decisions import JobIntentAlreadyOpen, JobNotFound
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import (
    IllegalJobTransition,
    Job,
    JobKind,
    JobNotRetryable,
    JobStillRunning,
)
from knowledge_service.pagination import InvalidPage, encode_cursor
from knowledge_service.roles import ApplicationRole
from knowledge_service.source_administration import SourceDisabled, SourceNotFound
from knowledge_service.synchronization_administration import (
    SynchronizationStart,
    history_cursor,
    history_position,
)

MARKER = "SECRET-MARKER-7f3a"
QUEUED_AT = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.new()


def queued(queued_at: datetime = QUEUED_AT) -> Job:
    return Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=SOURCE_ID.value,
        queued_at=queued_at,
    )


class Synchronizing:
    """Holds a Source's Jobs newest first; records each request; can refuse."""

    def __init__(self, jobs: list[Job] | None = None) -> None:
        self.jobs = jobs or []
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.open: Job | None = None

    async def start(self, **request: Any) -> SynchronizationStart:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        if self.open is not None:
            return SynchronizationStart(JobReport(self.open, None), queued=False)
        self.open = queued()
        return SynchronizationStart(JobReport(self.open, None), queued=True)

    async def history(
        self,
        *,
        source_id: SourceId,
        limit: int,
        after: JobId | None = None,
    ) -> tuple[JobReport, ...]:
        self.calls.append({"source_id": source_id, "limit": limit, "after": after})
        if self.error is not None:
            raise self.error
        start = 0
        if after is not None:
            start = next(
                (i for i, j in enumerate(self.jobs) if j.job_id.value < after.value),
                len(self.jobs),
            )
        return tuple(JobReport(job, None) for job in self.jobs[start : start + limit])


class Refusing:
    """A Job store whose every answer is the error a test sets."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def load(self, **_request: Any) -> None:
        if not isinstance(self.error, JobNotFound):
            raise self.error

    async def retry(self, **_request: Any) -> Job:
        raise self.error

    async def cancel(self, **_request: Any) -> Job:
        raise self.error


class Staff:
    """Gives every request a caller with every role but `user`."""

    async def authenticate(self, headers: list[str]) -> Caller:
        return Caller(
            reader=READER,
            roles=frozenset(
                {ApplicationRole.CONTENT_MANAGER, ApplicationRole.ADMINISTRATOR}
            ),
        )


async def call(
    method: str,
    path: str,
    *,
    synchronizations: Synchronizing | None = None,
    jobs: Refusing | None = None,
) -> httpx.Response:
    app = create_app(
        authenticator=Staff(),
        synchronizations=cast(Synchronizations, synchronizations or Synchronizing()),
        job_reports=cast(JobReports, jobs or Refusing(JobNotFound("x"))),
        job_decisions=cast(JobDecisions, jobs or Refusing(JobNotFound("x"))),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.request(method, path, headers={"Authorization": "Bearer t"})


START = f"/v1/sources/{SOURCE_ID.value}/synchronizations"


async def test_a_start_that_queues_a_job_answers_202_with_where_it_lives() -> None:
    synchronizations = Synchronizing()

    response = await call("POST", START, synchronizations=synchronizations)

    body = response.json()
    assert response.status_code == 202
    assert response.headers["location"] == f"/v1/jobs/{body['job_id']}"
    assert (body["status"], body["source_id"]) == ("queued", str(SOURCE_ID.value))
    assert synchronizations.calls[0]["operator"] == READER.user_id


async def test_a_second_start_answers_200_with_the_same_job() -> None:
    synchronizations = Synchronizing()

    first = await call("POST", START, synchronizations=synchronizations)
    second = await call("POST", START, synchronizations=synchronizations)

    assert (first.status_code, second.status_code) == (202, 200)
    assert second.json() == first.json()
    assert second.headers["location"] == first.headers["location"]


@pytest.mark.parametrize(
    "source_id", ["not-an-id", "{00000000-0000-0000-0000-000000000001}", "0" * 32]
)
@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_an_id_in_another_spelling_is_refused_before_the_store(
    method: str, source_id: str
) -> None:
    synchronizations = Synchronizing()

    response = await call(
        method,
        f"/v1/sources/{source_id}/synchronizations",
        synchronizations=synchronizations,
    )

    assert response.status_code == 422
    assert synchronizations.calls == []


@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_a_store_failure_names_nothing_to_the_client_or_the_log(
    method: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    synchronizations = Synchronizing()
    synchronizations.error = RuntimeError(f"query failed for {MARKER}")

    response = await call(method, START, synchronizations=synchronizations)

    assert response.status_code == 500
    assert MARKER not in response.text + caplog.text


@pytest.mark.parametrize(
    ("method", "target"),
    [
        ("POST", "/v1/sources/not-an-id/synchronizations"),
        ("GET", "/v1/sources/not-an-id/synchronizations"),
        ("GET", START + "?limit=0"),
        ("GET", START + "?cursor=%25%25"),
    ],
)
@pytest.mark.parametrize(
    ("headers", "status"),
    [({}, 401), (AUTHORIZATION, 403)],
    ids=["no token", "user role"],
)
async def test_the_role_is_checked_before_the_id_or_page_is_read(
    method: str, target: str, headers: dict[str, str], status: int
) -> None:
    synchronizations = Synchronizing()
    app = create_app(
        authenticator=TRUSTED,
        synchronizations=cast(Synchronizations, synchronizations),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.request(method, target, headers=headers)

    assert response.status_code == status
    assert synchronizations.calls == []


# The history.


def history(n: int) -> list[Job]:
    """`n` Jobs, the newest made first, as the store orders them."""
    return sorted(
        (queued() for _ in range(n)), key=lambda j: j.job_id.value, reverse=True
    )


async def test_pages_of_history_follow_each_other_to_the_end() -> None:
    jobs = history(5)
    synchronizations = Synchronizing(jobs)
    seen: list[str] = []
    query = "?limit=2"

    for _ in range(10):
        page = (
            await call("GET", START + query, synchronizations=synchronizations)
        ).json()
        seen += [item["job_id"] for item in page["items"]]
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    assert seen == [str(job.job_id.value) for job in jobs]
    assert [c["limit"] for c in synchronizations.calls] == [3, 3, 3]


async def test_a_full_last_page_of_history_says_no_page_follows() -> None:
    synchronizations = Synchronizing(history(4))

    first = (
        await call("GET", START + "?limit=2", synchronizations=synchronizations)
    ).json()
    cursor = first["next_cursor"]
    second = (
        await call(
            "GET", f"{START}?limit=2&cursor={cursor}", synchronizations=synchronizations
        )
    ).json()

    assert len(second["items"]) == 2
    assert second["next_cursor"] is None


@pytest.mark.parametrize(
    "query",
    [
        "?limit=0",
        "?offset=2",
        "?cursor=" + encode_cursor("sources", {"created_at": "x", "source_id": "y"}),
    ],
)
async def test_a_bad_page_request_is_refused_before_the_store(query: str) -> None:
    synchronizations = Synchronizing()

    response = await call("GET", START + query, synchronizations=synchronizations)

    assert response.status_code == 422
    assert synchronizations.calls == []


def test_a_history_cursor_starts_after_its_job() -> None:
    job = queued()

    assert history_position(history_cursor(job)) == job.job_id


@pytest.mark.parametrize(
    "position",
    [
        {"job_id": "{00000000-0000-0000-0000-000000000001}"},
        {"job_id": "00000000-0000-0000-0000-00000000000A"},
        {"job_id": "not an id"},
        {"job_id": str(uuid4()), "queued_at": "2026-11-27T09:00:00+00:00"},
    ],
)
def test_a_history_cursor_in_another_spelling_is_refused(
    position: dict[str, str],
) -> None:
    with pytest.raises(InvalidPage):
        history_position(encode_cursor("synchronizations", position))


# Stable error responses: what a client may branch on, pinned.

JOB = f"/v1/jobs/{uuid4()}"
ERRORS = [
    (
        "start, missing Source",
        "POST",
        START,
        "synchronizations",
        SourceNotFound("x"),
        (404, "source_not_found", "No such Source"),
    ),
    (
        "start, disabled Source",
        "POST",
        START,
        "synchronizations",
        SourceDisabled("x"),
        (409, "source_disabled", "The Source is missing or disabled"),
    ),
    (
        "history, missing Source",
        "GET",
        START,
        "synchronizations",
        SourceNotFound("x"),
        (404, "source_not_found", "No such Source"),
    ),
    (
        "status, missing Job",
        "GET",
        JOB,
        "jobs",
        JobNotFound("x"),
        (404, "job_not_found", "No such synchronization job"),
    ),
    (
        "retry, missing Job",
        "POST",
        JOB + "/retry",
        "jobs",
        JobNotFound("x"),
        (404, "job_not_found", "No such job"),
    ),
    (
        "retry, another Job open",
        "POST",
        JOB + "/retry",
        "jobs",
        JobIntentAlreadyOpen("x"),
        (409, "job_already_open", "Another job for this work is open"),
    ),
    (
        "retry, finished Job",
        "POST",
        JOB + "/retry",
        "jobs",
        JobNotRetryable("x"),
        (409, "decision_not_allowed", "This decision does not fit the job's status"),
    ),
    (
        "retry, disabled Source",
        "POST",
        JOB + "/retry",
        "jobs",
        SourceDisabled("x"),
        (409, "source_disabled", "The Source is missing or disabled"),
    ),
    (
        "cancel, missing Job",
        "POST",
        JOB + "/cancel",
        "jobs",
        JobNotFound("x"),
        (404, "job_not_found", "No such job"),
    ),
    (
        "cancel, running Job",
        "POST",
        JOB + "/cancel",
        "jobs",
        JobStillRunning("x"),
        (409, "job_running", "The job is still running"),
    ),
    (
        "cancel, finished Job",
        "POST",
        JOB + "/cancel",
        "jobs",
        IllegalJobTransition("x"),
        (409, "decision_not_allowed", "This decision does not fit the job's status"),
    ),
]


@pytest.mark.parametrize(
    ("method", "path", "store", "error", "expected"),
    [case[1:] for case in ERRORS],
    ids=[case[0] for case in ERRORS],
)
async def test_each_refusal_has_one_fixed_status_code_and_message(
    method: str,
    path: str,
    store: str,
    error: Exception,
    expected: tuple[int, str, str],
) -> None:
    synchronizations = Synchronizing()
    if store == "synchronizations":
        synchronizations.error = error

    response = await call(
        method,
        path,
        synchronizations=synchronizations,
        jobs=Refusing(error) if store == "jobs" else None,
    )

    body = response.json()
    assert (response.status_code, body["code"], body["message"]) == expected
    assert set(body) == {"code", "message", "request_id"}
```

Create `tests/test_synchronization_administration_integration.py`:

```python
"""Synchronization administration on real PostgreSQL.

A start is idempotent while the Source has an open Job, even when several
arrive at once; once the Job ends, in any final state, the next start queues
a new one. A Source's history holds its own synchronization Jobs only. A
start commits before it publishes, so a queue that refuses the message
loses nothing.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from authorization_scenarios import Person
from knowledge_service.app import create_app
from knowledge_service.audit import AuditAction
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.job_decisions import DatabaseJobDecisions, retry_job
from knowledge_service.job_dispatch import PublishFailed
from knowledge_service.job_reporting import DatabaseJobReports
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.persistence.audit import load_audit_events
from knowledge_service.persistence.job_attempts import begin_attempt
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
from knowledge_service.persistence.schema import jobs
from knowledge_service.persistence.sources import lock_enabled_sources
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings
from knowledge_service.source_administration import (
    SourceDefinition,
    SourceDisabled,
    SourceNotFound,
    register_source,
    set_source_enabled,
)
from knowledge_service.synchronization_administration import (
    DatabaseSynchronizations,
    start_synchronization,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
MARKER = "SECRET-MARKER-7f3a"
DANA = Person("dana", ())
OPERATOR = DANA.context().user_id
CALLERS = {
    "Bearer dana": Caller(
        reader=DANA.context(),
        roles=frozenset(
            {ApplicationRole.CONTENT_MANAGER, ApplicationRole.ADMINISTRATOR}
        ),
    ),
}


class Tokens:
    """Gives each test token the caller its claims would give."""

    async def authenticate(self, headers: list[str]) -> Caller:
        if len(headers) == 1 and headers[0] in CALLERS:
            return CALLERS[headers[0]]
        raise Unauthenticated("test", error=AuthenticationError.INVALID_TOKEN)


@dataclass
class Publisher:
    """Records each Job it sends; refuses all of them when told to."""

    refuse: bool = False
    kinds: frozenset[JobKind] = frozenset({JobKind.SYNCHRONIZE_SOURCE})
    sent: list[JobId] = field(default_factory=lambda: list[JobId]())

    def publish(self, job: Job) -> None:
        if self.refuse:
            raise PublishFailed(f"the queue is down near {MARKER}")
        self.sent.append(job.job_id)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def folder(runtime: DatabaseRuntime, name: str = "handbook") -> SourceId:
    record = await register_source(
        runtime,
        definition=SourceDefinition.of(
            kind="local_directory", location=f"/srv/{name}", display_name=name
        ),
        operator=OPERATOR,
        now=NOW,
    )
    return record.source_id


async def start(runtime: DatabaseRuntime, source_id: SourceId) -> Any:
    return await start_synchronization(
        runtime, source_id=source_id, operator=OPERATOR, now=NOW
    )


async def store(runtime: DatabaseRuntime, job: Job) -> None:
    async with runtime.transaction() as connection:
        await save_job(connection, job=job)


async def queued_events(runtime: DatabaseRuntime, job_id: JobId) -> list[Any]:
    async with runtime.transaction() as connection:
        events = await load_audit_events(connection, target_id=job_id.value, limit=10)
    return [(e.action, e.actor_id, dict(e.metadata)) for e in events]


def app(runtime: DatabaseRuntime, publisher: Publisher | None = None) -> Any:
    return create_app(
        authenticator=Tokens(),
        synchronizations=DatabaseSynchronizations(
            runtime, publisher=publisher, clock=lambda: NOW
        ),
        job_reports=DatabaseJobReports(runtime),
        job_decisions=DatabaseJobDecisions(runtime, clock=lambda: NOW),
    )


async def over_http(
    runtime: DatabaseRuntime,
    method: str,
    path: str,
    publisher: Publisher | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app(runtime, publisher)),
        base_url="http://t",
    ) as http:
        return await http.request(
            method, path, headers={"Authorization": "Bearer dana"}
        )


def path(source_id: SourceId) -> str:
    return f"/v1/sources/{source_id.value}/synchronizations"


async def test_a_start_queues_one_job_and_records_who_asked(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)

    response = await over_http(runtime, "POST", path(source_id))
    job_id = JobId.parse(response.json()["job_id"])

    assert response.status_code == 202
    async with runtime.transaction() as connection:
        stored = await load_job(connection, job_id=job_id)
    assert stored is not None
    assert (stored.kind, stored.target_id, stored.status) == (
        JobKind.SYNCHRONIZE_SOURCE,
        source_id.value,
        JobStatus.QUEUED,
    )
    assert await queued_events(runtime, job_id) == [
        (AuditAction.JOB_QUEUED, OPERATOR, {"kind": "synchronize_source"})
    ]


async def test_starts_at_once_queue_one_job_and_one_event(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)

    responses = await asyncio.gather(
        *(over_http(runtime, "POST", path(source_id)) for _ in range(4))
    )

    assert sorted(r.status_code for r in responses) == [200, 200, 200, 202]
    assert len({r.json()["job_id"] for r in responses}) == 1
    assert len({r.headers["location"] for r in responses}) == 1
    job_id = JobId.parse(responses[0].json()["job_id"])
    assert len(await queued_events(runtime, job_id)) == 1


async def test_a_repeated_start_over_http_answers_200_with_the_same_job(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)

    first = await over_http(runtime, "POST", path(source_id))
    second = await over_http(runtime, "POST", path(source_id))

    assert (first.status_code, second.status_code) == (202, 200)
    assert second.json()["job_id"] == first.json()["job_id"]


@pytest.mark.parametrize(
    "ending",
    ["running", "retry planned"],
)
async def test_an_open_job_is_returned_whatever_its_state(
    runtime: DatabaseRuntime, ending: str
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    running = job.start(started_at=NOW + timedelta(seconds=1))
    if ending == "running":
        await store(runtime, running)
    else:
        await store(
            runtime,
            running.fail(
                error_code="source_unavailable",
                finished_at=NOW + timedelta(seconds=2),
                retry_at=NOW + timedelta(minutes=1),
            ),
        )

    again = await over_http(runtime, "POST", path(source_id))

    assert (again.status_code, again.json()["job_id"]) == (200, str(job.job_id.value))
    assert again.json()["status"] == ("running" if ending == "running" else "failed")


@pytest.mark.parametrize("ending", ["succeeded", "failed for good", "cancelled"])
async def test_after_a_final_state_a_start_queues_a_new_job(
    runtime: DatabaseRuntime, ending: str
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    later = NOW + timedelta(seconds=2)
    if ending == "cancelled":
        await store(runtime, job.cancel(finished_at=later))
    else:
        running = job.start(started_at=NOW + timedelta(seconds=1))
        await store(
            runtime,
            running.succeed(finished_at=later)
            if ending == "succeeded"
            else running.fail(error_code="source_gone", finished_at=later),
        )

    again = await over_http(runtime, "POST", path(source_id))

    assert again.status_code == 202
    assert again.json()["job_id"] != str(job.job_id.value)


async def test_retrying_a_failed_job_while_a_new_one_is_open_is_a_409(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    await store(
        runtime,
        job.start(started_at=NOW + timedelta(seconds=1)).fail(
            error_code="source_gone", finished_at=NOW + timedelta(seconds=2)
        ),
    )
    await start(runtime, source_id)

    response = await over_http(runtime, "POST", f"/v1/jobs/{job.job_id.value}/retry")

    assert (response.status_code, response.json()["code"]) == (409, "job_already_open")


async def test_a_disabled_or_missing_source_queues_nothing(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    await set_source_enabled(
        runtime, source_id=source_id, enabled=False, operator=OPERATOR, now=NOW
    )

    disabled = await over_http(runtime, "POST", path(source_id))
    missing = await over_http(runtime, "POST", path(SourceId.new()))

    assert (disabled.status_code, disabled.json()["code"]) == (409, "source_disabled")
    assert (missing.status_code, missing.json()["code"]) == (404, "source_not_found")
    with pytest.raises(SourceDisabled):
        await start(runtime, source_id)
    with pytest.raises(SourceNotFound):
        await start(runtime, SourceId.new())

    async with runtime.transaction() as connection:
        count = await connection.scalar(sa.select(sa.func.count()).select_from(jobs))
    assert count == 0


async def test_a_start_publishes_once_and_a_refused_send_loses_nothing(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    published, refused = await folder(runtime, "a"), await folder(runtime, "b")
    publisher, down = Publisher(), Publisher(refuse=True)

    first = await over_http(runtime, "POST", path(published), publisher)
    await over_http(runtime, "POST", path(published), publisher)
    stored = await over_http(runtime, "POST", path(refused), down)

    assert publisher.sent == [JobId.parse(first.json()["job_id"])]
    assert stored.status_code == 202
    async with runtime.transaction() as connection:
        job = await load_job(connection, job_id=JobId.parse(stored.json()["job_id"]))
        unpublished = await connection.scalar(
            sa.select(jobs.c.published_at).where(
                jobs.c.job_id == JobId.parse(stored.json()["job_id"]).value
            )
        )
    assert job is not None
    assert job.status is JobStatus.QUEUED
    assert unpublished is None
    assert MARKER not in caplog.text


async def test_a_history_holds_only_its_sources_synchronization_jobs(
    runtime: DatabaseRuntime,
) -> None:
    mine, other = await folder(runtime, "mine"), await folder(runtime, "other")
    own = (await start(runtime, mine)).report.job
    await start(runtime, other)
    # Another kind of Job aimed at the same ID is not a synchronization.
    async with runtime.transaction() as connection:
        await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=mine.value,
                queued_at=NOW,
            ),
        )

    page = (await over_http(runtime, "GET", path(mine))).json()

    assert [item["job_id"] for item in page["items"]] == [str(own.job_id.value)]
    assert page["next_cursor"] is None


async def test_a_history_of_a_missing_source_is_a_404(
    runtime: DatabaseRuntime,
) -> None:
    response = await over_http(runtime, "GET", path(SourceId.new()))

    assert (response.status_code, response.json()["code"]) == (404, "source_not_found")


async def test_pages_of_history_never_repeat_or_skip_a_job(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    # Older Jobs get newer IDs here, so only an order by ID passes.
    async with runtime.transaction() as connection:
        for n in range(6):
            job = Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=source_id.value,
                queued_at=NOW - timedelta(hours=1 + n // 2),
            )
            await enqueue_job(connection, job)
            started = job.start(started_at=job.queued_at + timedelta(seconds=1))
            await save_job(
                connection,
                job=started.succeed(finished_at=job.queued_at + timedelta(seconds=2)),
            )
    seen: list[str] = []
    query = "?limit=2"

    for page_number in range(10):
        page = (await over_http(runtime, "GET", path(source_id) + query)).json()
        seen += [item["job_id"] for item in page["items"]]
        if page_number == 0:
            # A start between pages is newer: it starts no later page.
            await start(runtime, source_id)
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(jobs.c.job_id)
            .where(jobs.c.target_id == source_id.value, jobs.c.status == "succeeded")
            .order_by(jobs.c.job_id.desc())
        )
        expected = [str(row.job_id) for row in rows]
    assert seen == expected


async def test_a_history_item_shows_its_jobs_newest_attempt(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    first = job.start(started_at=NOW + timedelta(seconds=1))
    async with runtime.transaction() as connection:
        await save_job(connection, job=first)
        await begin_attempt(connection, job=first)
        failed = first.fail(
            error_code="source_unavailable",
            finished_at=NOW + timedelta(seconds=2),
            retry_at=NOW + timedelta(seconds=3),
        )
        await save_job(connection, job=failed)
        second = failed.retry(queued_at=NOW + timedelta(seconds=3)).start(
            started_at=NOW + timedelta(seconds=4)
        )
        await save_job(connection, job=second)
        await begin_attempt(connection, job=second)

    page = (await over_http(runtime, "GET", path(source_id))).json()

    assert page["items"][0]["latest_attempt"]["attempt"] == 2


async def failed_for_good(runtime: DatabaseRuntime, source_id: SourceId) -> Job:
    """A new synchronization Job of `source_id`, claimed and failed for good."""
    job = (await start(runtime, source_id)).report.job
    failed = job.start(started_at=NOW + timedelta(seconds=1)).fail(
        error_code="source_unavailable", finished_at=NOW + timedelta(seconds=2)
    )
    await store(runtime, failed)
    return failed


async def test_a_job_retried_between_pages_is_still_listed_once(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    failed = [await failed_for_good(runtime, source_id) for _ in range(4)]
    seen: list[str] = []
    query = "?limit=2"

    for page_number in range(10):
        page = (await over_http(runtime, "GET", path(source_id) + query)).json()
        seen += [item["job_id"] for item in page["items"]]
        if page_number == 0:
            # A retry queues the oldest Job again, at a later time.
            await retry_job(
                runtime,
                job_id=failed[0].job_id,
                operator=OPERATOR,
                now=NOW + timedelta(hours=1),
            )
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    assert sorted(seen) == sorted(str(job.job_id.value) for job in failed)
    assert len(seen) == len(set(seen))


async def test_a_retry_queues_nothing_for_a_disabled_source(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    failed = await failed_for_good(runtime, source_id)
    await set_source_enabled(
        runtime, source_id=source_id, enabled=False, operator=OPERATOR, now=NOW
    )

    response = await over_http(runtime, "POST", f"/v1/jobs/{failed.job_id.value}/retry")

    assert (response.status_code, response.json()["code"]) == (409, "source_disabled")
    async with runtime.transaction() as connection:
        stored = await load_job(connection, job_id=failed.job_id)
    assert stored is not None
    assert stored.status is JobStatus.FAILED


async def test_a_retry_that_does_not_fit_the_job_says_so_even_for_a_disabled_source(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    running = job.start(started_at=NOW + timedelta(seconds=1))
    await store(runtime, running.succeed(finished_at=NOW + timedelta(seconds=2)))
    await set_source_enabled(
        runtime, source_id=source_id, enabled=False, operator=OPERATOR, now=NOW
    )

    response = await over_http(runtime, "POST", f"/v1/jobs/{job.job_id.value}/retry")

    assert (response.status_code, response.json()["code"]) == (
        409,
        "decision_not_allowed",
    )


async def test_a_disable_waits_for_a_start_that_holds_the_source(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    async with runtime.transaction() as connection:
        # What a start holds between its check and its commit.
        await lock_enabled_sources(connection, source_ids={source_id})
        disabling = asyncio.ensure_future(
            set_source_enabled(
                runtime, source_id=source_id, enabled=False, operator=OPERATOR, now=NOW
            )
        )
        await asyncio.sleep(0.3)
        waited = not disabling.done()
    disabled = await asyncio.wait_for(disabling, timeout=5)

    assert waited
    assert disabled.enabled is False


async def test_a_history_page_reads_an_index_in_job_order(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        definition = await connection.scalar(
            sa.text(
                "SELECT indexdef FROM pg_indexes"
                " WHERE indexname = 'ix_jobs_synchronization_target'"
            )
        )

    assert "(target_id, job_id)" in str(definition)
    assert "synchronize_source" in str(definition)
```

Check:

```bash
uv run pytest tests/test_synchronization_administration.py -q
uv run pytest tests/test_synchronization_administration_integration.py -q -m integration
```

Expected: `39 passed`, then `20 passed`.

## Step 9 — Let a disabled Source start, on purpose

No coding in this step.

This step proves the integration tests guard "only an enabled Source starts". In `src/knowledge_service/synchronization_administration.py`, in `start_synchronization`, change

```python
        if source_id not in enabled:
```

to `if False:`, then run with bytecode off:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider tests/test_synchronization_administration_integration.py -q -m integration
```

Expected: `1 failed, 19 passed`, and the failure is `test_a_disabled_or_missing_source_queues_nothing`: a Job was stored for a disabled Source, and the missing Source got one too. Undo the change and run again: `20 passed`.

What would falsify the design: two open synchronization Jobs for one Source, a Job stored for a disabled Source by a start or a retry, a 202 that queued nothing or a 200 that queued something, a Job listed twice or never while paging, a published Job lost because a send failed, or a refusal whose status, code, or message changed.

## Step 10 — Update the architecture and threat model

No coding in this step.

In `docs/architecture.md`, before the paragraph that starts with `` `api_routes` is the native API's design ``, add:

```markdown
`POST /v1/sources/{source_id}/synchronizations` and
`GET /v1/sources/{source_id}/synchronizations` require
`start_synchronization_runs` (the content-manager role) before the ID or
page is read. `synchronization_administration` starts a synchronization
the way the scheduler does: it share-locks the Source, refuses one that
is missing (404) or disabled (409 `source_disabled`), and asks
`enqueue_job` for one Job; while the Source has an open Job (queued,
running, or failed with a retry planned) that Job comes back with 200,
and only a start that queues one answers 202 and records `job.queued`
with the operator as actor. The start commits before it tries once to
publish, so a refused message is sent later by the relay. The history
lists a Source's synchronization Jobs a page at a time, the newest
made first, each with its newest attempt, from one snapshot. It pages
on the Job's ID, a UUIDv7 that never changes, not on `queued_at`,
which every retry rewrites. Status, retry, and cancel stay on
`/v1/jobs/{job_id}`; a retry of a synchronization Job, once its status
fits, share-locks the Source as a start does and refuses one that is
missing or disabled with 409 `source_disabled`. Every refusal of these
five operations has one fixed status, code, and message. A Source's
history pages on `ix_jobs_synchronization_target (target_id, job_id)`.
```

In `docs/threat-model.md`:

- in the entry points table, before the `GET /health/live` row, add:

```markdown
| `POST /v1/sources/{source_id}/synchronizations`, `GET /v1/sources/{source_id}/synchronizations` | Any client that reaches the API | Starts a Source's synchronization, or returns its open Job, and lists its synchronization Jobs a page at a time. Needs a bearer token and the `start_synchronization_runs` operation (the `content-manager` role), both checked before the ID, `limit`, or `cursor` is read; only an enabled Source starts, and a retry of its Job is refused the same way; a start that queues a Job is an audit event with the operator as actor; the message carries only the Job's ID |
```

- in the Operations row "A role widens what its holder reads", the last cell becomes "Role checks on the evaluation and policy entry points when they exist";
- in the known gaps, replace the bullet that starts "Only `ask_questions` and `manage_own_conversations` have entry points" with:

```markdown
- `ask_questions`, `manage_own_conversations` (only for deleting), `configure_sources`, `start_synchronization_runs`, and `recover_jobs` have entry points that check them; reading a Conversation or its History has no route yet. Running evaluations is a script a developer runs with no identity, and the scheduler queues synchronizations as the service itself; each operation gets its role check with its entry point.
```

In `docs/operations.md`, in the Telemetry paragraph, before "Each Job attempt's stage", add:

```markdown
A content manager starts a Source's synchronization with `POST /v1/sources/{source_id}/synchronizations` (202 when it queues a Job, 200 with the Job already open) and follows it with `GET /v1/sources/{source_id}/synchronizations`.
```

Check: `make docs-check` passes.

## Step 11 — Run the gates and write the evidence

No coding in this step.

Add the new files to git before the hooks run: pre-commit only looks at files git tracks, so a brand-new file would otherwise pass unchecked.

```bash
git add -A
uv run ruff format --check .
uv run ruff check .
uv run pyright
make docs-check
make test-unit
make test-integration
git diff --check
uv run pre-commit run
```

On the prototype these gave: 371 files formatted, all checks passed, 0 errors, docs-check exit 0, `3681 passed` unit, `948 passed` integration with Keycloak's secrets and Redis available, and every hook passed on the staged files.

Then write `docs/evidence/M09-T03-synchronization-administration.md` in your own words: the invariant and owners, your predictions from Step 1 and what happened, the expected failing check, the five operations and their roles, the 202 and 200 rule, the order of storing and publishing, why history pages on the ID, the error table, the deliberate break, the gate results, and what would falsify the design. A start makes one Redis call at most, bounded by the client's own timeouts, and no paid call. A repeated start is safe by design; a cancelled Job is final, and the next start queues a new one.

## Completion checklist

- [ ] `POST /v1/sources/{source_id}/synchronizations` answers 202 with `Location` when it queues a Job, and 200 with the same Job while one is open.
- [ ] Four starts at once store one Job and one `job.queued` event naming the operator.
- [ ] After succeeded, failed for good, or cancelled, the next start queues a new Job.
- [ ] A start for a missing Source gives 404 `source_not_found`; a disabled one gives 409 `source_disabled`, and so does a retry once the Job's status fits; nothing is queued.
- [ ] A start commits before it publishes; a refused send still answers 202, and the Job waits for the relay.
- [ ] `GET /v1/sources/{source_id}/synchronizations` lists only that Source's synchronization Jobs, newest made first, with their newest attempts, and pages on the Job's ID.
- [ ] Every refusal of start, status, history, retry, and cancel has the status, code, and message in Block 5.
- [ ] A caller without the content-manager role gets 401 or 403 before any ID or page is read.
- [ ] `tests/test_synchronization_administration.py` (39) and `tests/test_synchronization_administration_integration.py` (20) pass.
- [ ] The deliberate break gave 1 failed, 19 passed, and the restored code passes.
- [ ] `docs/architecture.md` and `docs/threat-model.md` are updated, and every gate in Step 11 passes, hooks included.
- [ ] The evidence file is written in your own words.

## Reflection questions

1. Two people press "synchronize now" for the same Source at the same moment. Which database object makes sure only one Job is stored, and why does the code not need to check for an open Job before inserting?
2. Why does a start answer 202 the first time and 200 the second time, instead of 409 the second time? Think about a client that retries a request after a network timeout.
3. The start saves the Job and its audit event before it sends the Redis message. What could go wrong with the opposite order, and who sends the message if Redis refuses it?
4. The first history design ordered Jobs by `queued_at`. Walk through how a retry between two page requests made a Job disappear, and why ordering by a UUIDv7 ID cannot have that problem.
5. Retry and start are different routes owned by different roles. Why was it still a bug that retry did not check whether the Source was enabled?
6. A client script branches on `source_disabled` and `job_running`. Why does the lesson pin the exact status, code, and message of every refusal in a test, and what would break silently without that test?
