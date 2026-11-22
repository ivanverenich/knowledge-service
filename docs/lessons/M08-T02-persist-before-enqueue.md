# M08-T02 — Persist before enqueue

Task: [M08-T02 — Persist before enqueue](../curriculum/milestones/M08-jobs-and-confluence/M08-T02-persist-before-enqueue.md)

## What you will do

This lesson connects stored Jobs to the queue from M08-T01, so that no Job is ever lost on the way. A Job is first saved in PostgreSQL, and only then is a message sent to Redis. If sending fails, or the process stops at the wrong moment, a separate pass finds the Jobs nobody was told about and sends them. The worker then runs each Job at most once, even when two messages name it.

- **Persist first.** A Job is committed to PostgreSQL before any message names it.
- **Record the send.** A new column says when a message for the Job was handed to Redis.
- **Repair what was missed.** A relay pass sends every stored Job that has no message yet.
- **Messages carry only an ID.** What to do, and to which Document, is read from the database.
- **One effect per Job.** A worker runs a Job only while it is still queued, so a second message changes nothing.
- **Proof:** tests make the queue refuse a message and make the process "crash" between sending and recording, then check that the relay repairs it and the Job's effect happens once.

| The task asks for | Where it happens |
|---|---|
| Durable Job identity and intent in PostgreSQL before publishing | Step 4: `submit_job` commits, then publishes |
| Recovery for persisted-but-unpublished work | Step 2: the `published_at` column; Step 4: `publish_pending_jobs`; Step 6: `make worker-relay` |
| Failure injection between commit and publish is detected and repaired | Step 8: a refused message and a crash after sending, each repaired by the relay; Step 10: Redis stopped for real |
| Without duplicate effects | Step 5: the worker runs only queued Jobs; Step 8 and Step 9: duplicate and concurrent messages run the purge once |
| Success, boundary, typed failure or denial | Step 7 and Step 8: a published Job; the relay limit and order; `PublishFailed`, `UnsupportedJobKind`, `InvalidJobMessage` |
| Real infrastructure where its semantics are under test | Step 8: real PostgreSQL locks and commits; Step 9: real Redis and a real Dramatiq worker |
| Update only affected documentation | Step 12: `docs/architecture.md`, `docs/threat-model.md` |

This lesson includes coding: Steps 2 to 9 write code, a migration, and tests. Steps 1, 10, 11, and 12 do not, though Step 12 edits two documents.

Terms used in this lesson:

- **Persist before enqueue:** save the work in the database first, then announce it. Like writing an order in the shop's book before shouting it to the kitchen: if the kitchen misses the shout, the book still has the order.
- **Publish:** hand one message naming a Job to Redis.
- **Relay:** a pass that reads the database for Jobs with no message yet, and publishes them.
- **Row lock:** while one transaction holds a lock on a row, other transactions that ask for the same lock wait. `SKIP LOCKED` tells them to skip that row instead of waiting.
- **Duplicate effect:** the same work done twice, such as deleting the same Chunks twice or sending the same email twice.
- **Dead letter:** a message the worker refused or failed on. Dramatiq moves it aside and never runs it again.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **What already exists.** The `jobs` table and `Job` value have been there since M02 and M03. The purge Job is already stored in the same transaction that tombstones a Document, in `tombstone_missing`. But nothing sends it to the queue and nothing runs it. This lesson closes that gap.
- **Why not send first.** If the message went to Redis before the commit, a worker could pick it up and find no Job in the database. Worse, if the commit then failed, a message would exist for work that never happened.
- **Why not send inside the transaction.** A message sent before the commit has the same problem as above. So the code commits first, and publishes in a second, short transaction.
- **The send is recorded.** The new `published_at` column is empty until a message is handed to Redis. The relay looks only at queued Jobs whose `published_at` is empty.
- **The gap is repaired, not prevented.** The process can stop after sending but before recording. Then the relay sends a second message for the same Job. That is fine, because the worker ignores a Job that is no longer queued. We accept two messages to never have zero.
- **One publisher at a time per Job.** Publishing locks the Job's row with `SKIP LOCKED`. Two relays running at once never send the same Job at the same moment; the second simply skips the locked row.
- **A message carries only the Job's ID.** The worker reads the kind and the target from PostgreSQL. A message therefore cannot carry instructions the database does not have, and the worker checks the ID's spelling before using it.
- **Only kinds a worker runs.** Today only `purge_document_chunks` has a handler. Conversation purges and synchronization Jobs stay unpublished until a later lesson gives them one. `submit_job` refuses them with `UnsupportedJobKind`.
- **A retried Job needs a new message.** When a failed Job is queued again, saving it clears `published_at`, so the relay publishes the new attempt.
- **One event loop per message.** Dramatiq runs actors in threads, and a database engine belongs to one event loop. The worker therefore opens a fresh loop and engine for each message, and closes them afterwards.
- **Nothing calls `submit_job` yet.** M09-T03 adds the routes that submit Jobs. Today Jobs come from other workflows, such as tombstoning, and reach Redis through the relay.
- **What the review found.** An independent adversarial review attacked the first version:
  - The publisher counted conversation purges as runnable, because they share the `maintenance` queue. They were sent, dead-lettered, and stranded for good. The publisher now counts exactly the kinds a worker runs, and three tests fail on the first version.
  - It also tried a storm of 80 Jobs with three relays, injected crashes and refusals, and every message delivered twice. Every Job ran exactly once.
- **Known limits.** These stay as they are for now:
  - A message that is lost after it was published stays lost: a Redis restart, a message dead-lettered before the worker claimed the Job, or one older than the one-day age limit. The Job stays queued and published, and the relay skips it. Finding such stale Jobs is M08-T08.
  - Publishing holds the Job's row lock and one database connection while Redis answers, up to its 5-second timeout. With Redis hung and many submissions at once, Dramatiq makes the sends wait in line, and the database pool can run short. Nothing submits Jobs concurrently yet; revisit this when M09-T03 does.
  - A duplicate message for attempt 1 can run attempt 2 as soon as a retry is saved, before attempt 2's own message is sent. It is still one effect per attempt; M08-T04 owns retry timing.

## Walkthrough

Start PostgreSQL and Redis before you begin (`make redis-up`). Every command runs from the repository root. For the integration checks, set the test URLs in your terminal:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
export KNOWLEDGE_SERVICE_TEST_REDIS_URL="redis://127.0.0.1:6380/15"
```

### Step 1 — No coding in this step: write the invariant and your predictions

Before you change anything, write down what must be true when the lesson is done, and what you expect. Step 11 checks the last prediction.

Create `docs/evidence/M08-T02-persist-before-enqueue.md`:

```markdown
# M08-T02 — Persist before enqueue evidence

## Invariant

No message names a Job before the Job is committed to PostgreSQL. Every
queued Job of a kind a worker runs is either named by a message whose send
is recorded in `published_at`, or found and sent by the relay. A message
carries only the Job's ID, and a worker runs a Job only while it is still
queued, so duplicate or concurrent messages have the effect of one.

## Owners

- Durable truth: the `jobs` row in PostgreSQL; Redis only delivers.
- Publishing: `submit_job` once, then `publish_pending_jobs` (the relay).
- Running once: the handler's claim of a queued Job, in one transaction.
- Retries: the stored Job (M08-T04); Dramatiq retries nothing.
- Authorization: none yet; nothing outside the service submits Jobs.

## Expected failing check

Before the change,
`uv run pytest tests/test_job_dispatch_integration.py -q -m integration`
fails with
`ERROR: file or directory not found: tests/test_job_dispatch_integration.py`.

## Predictions

- What the relay prints when Redis is stopped and one Job waits:
- How many messages the relay sends for a Job whose first send was refused:
- What the worker does with a second message for a Job that already succeeded:
- How many tests fail if saving a queued Job stops clearing `published_at`:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: record when a Job was published

This step adds one column and one index to the `jobs` table.

**In plain words:** each stored Job gets a place to note when a message about it was handed to Redis. While that place is empty and the Job is waiting, the Job still needs a message. A small index lets the relay find those Jobs quickly, oldest first.

| Piece | What it holds or does |
|---|---|
| `published_at` | the time a message for the current queued attempt was handed to Redis; empty means "not sent yet" |
| `ix_jobs_unpublished_queued_at` | an index over only the queued, unsent Jobs, ordered by when they were queued |
| Migration `c2d3e4f5a6b7` | adds both; every existing Job starts unsent |

The index is **partial**: it holds only rows that match its condition. Most Jobs finish and leave it, so it stays small however many Jobs pile up.

**Action 1:** create `migrations/versions/c2d3e4f5a6b7_job_publication.py`:

```python
"""job publication

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-11-21 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"


def upgrade() -> None:
    # Every existing row starts unpublished: no message has ever named it.
    op.add_column(
        "jobs",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_jobs_unpublished_queued_at",
        "jobs",
        ["queued_at", "job_id"],
        postgresql_where=sa.text("status = 'queued' and published_at is null"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_unpublished_queued_at", table_name="jobs")
    op.drop_column("jobs", "published_at")
```

**Action 2:** in `src/knowledge_service/persistence/schema.py`, in the `jobs` table, add the column after `last_error_code`:

```python
    # When a message naming this queued attempt was handed to the queue.
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
```

and add the index as the table's last item, after the `ck_jobs_last_error_code` check:

```python
    sa.Index(
        "ix_jobs_unpublished_queued_at",
        "queued_at",
        "job_id",
        postgresql_where=sa.text("status = 'queued' and published_at is null"),
    ),
```

**Action 3:** `tests/test_indexes.py` lists every index the project allows. Add the new name to `ACCESS_PATTERN_INDEXES`:

```python
    "ix_jobs_unpublished_queued_at",
```

**Check:** `uv run pytest tests/test_indexes.py -q` passes, and `uv run pytest tests/test_migrations_integration.py -q -m integration` passes, which proves the migration and the declared table agree.

### Step 3 — Coding step: find, lock, and mark unpublished Jobs

This step adds the database functions the relay and the publisher need.

**In plain words:** one function lists the waiting Jobs that have no message yet, oldest first. Another takes a lock on one such Job, or reports that it is gone or busy. A third notes the moment a message was sent. Saving a Job that was queued again for another try now also clears that note, so the new try gets its own message.

| Function | What it does |
|---|---|
| `load_unpublished_job_ids` | the IDs of queued, unsent Jobs of the given kinds, oldest first, up to a limit |
| `lock_unpublished_job` | locks one queued, unsent Job with `SKIP LOCKED`; `None` when it is sent, finished, missing, or locked by someone else |
| `mark_job_published` | writes the send time into `published_at` |
| `save_job` | unchanged, except that saving a queued Job clears `published_at` |

`lock_unpublished_job` checks "still queued and unsent" inside the same query that locks. So if another publisher sent the Job a moment ago, the row no longer matches and the function returns `None`.

**Action:** replace `src/knowledge_service/persistence/jobs.py` with:

```python
"""Store queued Jobs and whether a message has named them."""

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.identifiers import JobId
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence.schema import jobs


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
    )


async def enqueue_job(connection: AsyncConnection, job: Job) -> JobId:
    """Store a queued Job."""
    await connection.execute(sa.insert(jobs).values(_job_row(job)))
    return job.job_id


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

**Check:** `uv run pyright src/knowledge_service/persistence/jobs.py` reports 0 errors, and `uv run pytest tests/test_deletions_integration.py -q -m integration` still passes.

### Step 4 — Coding step: submit and relay

This step writes the workflow that stores a Job, publishes it, and repairs what was missed.

**In plain words:** submitting a Job saves it and commits first. Then, in a second short transaction, it locks the Job, hands the message to Redis, and notes the send. If Redis refuses, the Job simply stays unsent. The relay later goes through the unsent Jobs, oldest first, and does the same for each. It stops at the first refusal, so it does not hammer a Redis that is down.

| Piece | What it does |
|---|---|
| `JobPublisher` | the port: which kinds it can send, and `publish` one message or raise `PublishFailed` |
| `publish_job` | lock, send, record, in one transaction; returns `published`, `skipped`, or `failed` |
| `submit_job` | refuses a non-new Job or a kind no worker runs; commits the Job; tries `publish_job` once; returns a `Submission` |
| `publish_pending_jobs` | one relay pass: up to `limit` unsent Jobs, oldest first, stopping at the first failure; returns a `RelayReport` |

Three choices matter here:

- **The send runs in a thread.** The Redis client is synchronous. `asyncio.to_thread` keeps the event loop free while Redis answers, and Redis's own timeouts bound the wait.
- **After the commit, `submit_job` never raises for a publishing problem.** The Job is already durable, and the relay will send it. It logs only the error's type, because an error message could quote a URL with a password.
- **A cancelled submission is safe.** If the caller cancels while the message is being sent, the transaction rolls back and the Job stays unsent. The relay sends it later, so at worst the worker gets two messages.

**Action:** create `src/knowledge_service/job_dispatch.py`:

```python
"""Store a Job before any message names it, then publish it.

Submitting a Job commits it to PostgreSQL first. Only then is a message sent,
under a lock on the Job's row, and the send is recorded in `published_at`.
If the queue refuses the message, or the process stops before the send is
recorded, the Job stays queued and unpublished, and `publish_pending_jobs`
sends it later. One Job can therefore be named by two messages; a worker runs
a Job only while it is still queued, so the second message changes nothing.

Redis is never the record: PostgreSQL says what was asked for (ADR 0011).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Protocol

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence.jobs import (
    enqueue_job,
    load_unpublished_job_ids,
    lock_unpublished_job,
    mark_job_published,
)

logger = logging.getLogger(__name__)

MAX_RELAY_BATCH: Final = 1_000


class PublishFailed(RuntimeError):
    """The queue did not accept the message; the Job stays unpublished."""


class UnsupportedJobKind(InvalidJob):
    """No worker runs Jobs of this kind, so none may be submitted."""


class JobPublisher(Protocol):
    """Hands one message naming a Job to the queue."""

    @property
    def kinds(self) -> frozenset[JobKind]:
        """The Job kinds a worker can run."""
        ...

    def publish(self, job: Job) -> None:
        """Send one message naming `job`, or raise `PublishFailed`."""
        ...


class PublishOutcome(StrEnum):
    """What one attempt to publish a Job did."""

    PUBLISHED = "published"
    # Already published, no longer queued, not a kind any worker runs, or
    # being published by someone else right now.
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Submission:
    """A stored Job, and whether its message was sent yet."""

    job_id: JobId
    published: bool


@dataclass(frozen=True, slots=True)
class RelayReport:
    """What one pass over unpublished Jobs did."""

    published: int
    skipped: int
    failed: int
    # Found in this pass but not tried, because the queue had just failed.
    untried: int


def _require_utc(now: datetime) -> None:
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")


async def publish_job(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    publisher: JobPublisher,
    now: datetime,
) -> PublishOutcome:
    """Send one message for a queued, unpublished Job and record the send.

    The row stays locked while the message is sent, so no other publisher
    sends it at the same time. A send that fails leaves the Job unpublished.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        job = await lock_unpublished_job(connection, job_id=job_id)
        if job is None or job.kind not in publisher.kinds:
            return PublishOutcome.SKIPPED
        try:
            # The Redis client is synchronous and bounded by its own timeouts.
            await asyncio.to_thread(publisher.publish, job)
        except PublishFailed:
            return PublishOutcome.FAILED
        await mark_job_published(connection, job_id=job_id, published_at=now)
    return PublishOutcome.PUBLISHED


async def submit_job(
    runtime: DatabaseRuntime,
    job: Job,
    *,
    publisher: JobPublisher,
    now: datetime,
) -> Submission:
    """Store a new queued Job, then try once to publish it.

    Once this returns, the Job is stored, whether or not it was published.
    An unpublished Job is sent by a later `publish_pending_jobs`.
    """
    _require_utc(now)
    if job.status is not JobStatus.QUEUED or job.attempt != 0:
        raise InvalidJob("only a new queued job can be submitted")
    if job.kind not in publisher.kinds:
        raise UnsupportedJobKind(f"no worker runs {job.kind} jobs")
    async with runtime.transaction() as connection:
        await enqueue_job(connection, job)
    try:
        outcome = await publish_job(
            runtime, job_id=job.job_id, publisher=publisher, now=now
        )
    except Exception as error:
        # The Job is committed; the relay will publish it. Only the type is
        # logged, because a message could quote a URL or a secret.
        logger.warning(
            "job %s stored but not published: %s",
            job.job_id.value,
            type(error).__name__,
        )
        return Submission(job_id=job.job_id, published=False)
    return Submission(job_id=job.job_id, published=outcome is PublishOutcome.PUBLISHED)


async def publish_pending_jobs(
    runtime: DatabaseRuntime,
    *,
    publisher: JobPublisher,
    now: datetime,
    limit: int = 100,
) -> RelayReport:
    """Publish queued Jobs that no message names yet, oldest first.

    This repairs a Job stored by a process that stopped, or whose queue
    refused the message, before its send was recorded. The pass stops at the
    first failed send, so an unavailable queue is not hit once per Job.
    """
    _require_utc(now)
    if type(limit) is not int or not 1 <= limit <= MAX_RELAY_BATCH:
        raise ValueError(f"limit must be an integer from 1 to {MAX_RELAY_BATCH}")
    async with runtime.transaction() as connection:
        job_ids = await load_unpublished_job_ids(
            connection, kinds=publisher.kinds, limit=limit
        )
    counts = dict.fromkeys(PublishOutcome, 0)
    for index, job_id in enumerate(job_ids):
        outcome = await publish_job(
            runtime, job_id=job_id, publisher=publisher, now=now
        )
        counts[outcome] += 1
        if outcome is PublishOutcome.FAILED:
            return RelayReport(
                published=counts[PublishOutcome.PUBLISHED],
                skipped=counts[PublishOutcome.SKIPPED],
                failed=1,
                untried=len(job_ids) - index - 1,
            )
    return RelayReport(
        published=counts[PublishOutcome.PUBLISHED],
        skipped=counts[PublishOutcome.SKIPPED],
        failed=0,
        untried=0,
    )
```

**Check:** `uv run pyright src/knowledge_service/job_dispatch.py` reports 0 errors.

### Step 5 — Coding step: the worker side of Job messages

This step teaches the worker to run a stored Job from a message that names it.

**In plain words:** for each queue that carries Jobs the worker can run, there is one actor. Its message holds only the Job's ID, written the standard way. The worker checks that spelling, reads the Job from the database, and runs it only if it is still waiting. If the Job already ran because of an earlier message, the worker logs it and does nothing else. The publisher on the sending side uses the same actors, so it sends each Job on its kind's queue.

| Piece | What it does |
|---|---|
| `QUEUE_FOR_KIND` | which queue each kind of Job travels on |
| `HANDLERS` | the Jobs a worker can run: today only `purge_document_chunks` |
| `parse_job_id` | accepts only a `str` in canonical UUID form; anything else raises `InvalidJobMessage` |
| `JobActors` / `declare_job_actors` | one `run_<queue>_job` actor per queue the given kinds use, with a 60-second limit and no retries; `kinds` is exactly the kinds declared |
| `DramatiqJobPublisher` | sends a Job's ID on its queue; a kind no worker runs, or a broker error, raises `PublishFailed` |
| `run_stored_job` | loads the Job; ignores it when not queued; runs its handler; treats losing the claim to another message as a duplicate |
| `JobRunner` | the actor's callable: a fresh event loop and database engine per message |

The duplicate check happens twice on purpose. The first check is cheap and skips most duplicates. The second is the handler's claim, which locks the row: two messages arriving together both pass the first check, but only one wins the claim.

**Action 1:** create `src/knowledge_service/worker/jobs.py`:

```python
"""Messages that name stored Jobs, and the worker side that runs them.

A message carries only a Job's ID. What to do, and to which target, is read
from the Job's row in PostgreSQL, so a message never holds intent that the
database does not.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import dramatiq
import redis
from dramatiq.errors import BrokerError

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.ingestion.deletions import purge_document_chunks
from knowledge_service.job_dispatch import PublishFailed
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence.jobs import load_job
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import QueueName, WorkerConfigurationError

logger = logging.getLogger(__name__)

JOB_TIME_LIMIT_MS: Final = 60_000

# The queue each kind of Job travels on.
QUEUE_FOR_KIND: Final[Mapping[JobKind, QueueName]] = {
    JobKind.SYNCHRONIZE_SOURCE: QueueName.SYNCHRONIZATION,
    JobKind.PURGE_CONVERSATION: QueueName.MAINTENANCE,
    JobKind.PURGE_DOCUMENT_CHUNKS: QueueName.MAINTENANCE,
}

type JobHandler = Callable[..., Awaitable[Job]]

# The Jobs a worker can run today. Each handler claims a queued Job in its
# own transaction and refuses one that is no longer queued.
HANDLERS: Final[Mapping[JobKind, JobHandler]] = {
    JobKind.PURGE_DOCUMENT_CHUNKS: purge_document_chunks,
}


class InvalidJobMessage(ValueError):
    """A message does not name a stored Job a worker can run."""


def parse_job_id(value: object) -> JobId:
    """The Job ID a message carries, written as a canonical UUID string."""
    if type(value) is not str:
        raise InvalidJobMessage("a job message carries one job ID string")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise InvalidJobMessage("a job message carries a malformed job ID") from None
    if str(parsed) != value:
        raise InvalidJobMessage("a job message carries a malformed job ID")
    return JobId(parsed)


@dataclass(frozen=True, slots=True)
class JobActors:
    """The Job kinds a worker runs, and one actor per queue they travel on.

    `kinds` is exactly what the actors were declared for. Another kind that
    shares a queue is not included: no worker runs it.
    """

    kinds: frozenset[JobKind]
    by_queue: Mapping[QueueName, dramatiq.Actor[[str], None]]


def declare_job_actors(
    broker: dramatiq.Broker,
    *,
    run_job: Callable[[JobId], None],
    kinds: frozenset[JobKind],
) -> JobActors:
    """Declare a `run_<queue>_job` actor for each queue these kinds use."""

    def run(job_id: str) -> None:
        # The message came from Redis, so its argument is checked here.
        run_job(parse_job_id(job_id))

    by_queue: dict[QueueName, dramatiq.Actor[[str], None]] = {}
    for queue in sorted({QUEUE_FOR_KIND[kind] for kind in kinds}):
        declare = dramatiq.actor(
            broker=broker,
            actor_name=f"run_{queue}_job",
            queue_name=queue,
            time_limit=JOB_TIME_LIMIT_MS,
            max_retries=0,
        )
        by_queue[queue] = declare(run)
    return JobActors(kinds=kinds, by_queue=by_queue)


@dataclass(frozen=True, slots=True)
class DramatiqJobPublisher:
    """Publishes a Job as a message on its kind's queue."""

    actors: JobActors

    @property
    def kinds(self) -> frozenset[JobKind]:
        return self.actors.kinds

    def publish(self, job: Job) -> None:
        if job.kind not in self.actors.kinds:
            raise PublishFailed(f"no worker runs {job.kind} jobs")
        actor = self.actors.by_queue[QUEUE_FOR_KIND[job.kind]]
        try:
            actor.send(str(job.job_id.value))
        except (BrokerError, redis.RedisError, OSError) as error:
            raise PublishFailed("the queue did not accept the message") from error


async def run_stored_job(
    runtime: DatabaseRuntime,
    job_id: JobId,
    *,
    now: datetime,
    handlers: Mapping[JobKind, JobHandler] = HANDLERS,
) -> Job | None:
    """Run a stored queued Job once; None when the message was a duplicate.

    A Job that is no longer queued was run, or is running, because of an
    earlier message for it, so this message changes nothing.
    """
    async with runtime.transaction() as connection:
        job = await load_job(connection, job_id=job_id)
    if job is None:
        raise InvalidJobMessage("the message names no stored job")
    if job.status is not JobStatus.QUEUED:
        logger.info("job %s is %s; duplicate message ignored", job_id.value, job.status)
        return None
    handler = handlers.get(job.kind)
    if handler is None:
        raise InvalidJobMessage(f"no handler runs {job.kind} jobs")
    try:
        ran = await handler(runtime, job_id=job_id, now=now)
    except InvalidJob:
        # Another message for the same Job claimed it first.
        async with runtime.transaction() as connection:
            current = await load_job(connection, job_id=job_id)
        if current is not None and current.status is not JobStatus.QUEUED:
            logger.info("job %s was claimed by another message", job_id.value)
            return None
        raise
    logger.info("job %s (%s) ran: %s", job_id.value, ran.kind, ran.status)
    return ran


@dataclass(frozen=True, slots=True)
class JobRunner:
    """Runs one Job per message, with a database connection of its own."""

    settings: Settings

    def __post_init__(self) -> None:
        if self.settings.database_url is None:
            raise WorkerConfigurationError(
                "KNOWLEDGE_SERVICE_DATABASE_URL is required to run jobs"
            )

    def __call__(self, job_id: JobId) -> None:
        asyncio.run(self._run(job_id))

    async def _run(self, job_id: JobId) -> None:
        # An engine belongs to one event loop, and each message gets its own.
        runtime = create_database_runtime(self.settings)
        try:
            await run_stored_job(runtime, job_id, now=datetime.now(UTC))
        finally:
            await runtime.dispose()
```

**Action 2:** replace `src/knowledge_service/worker/main.py` with:

```python
"""The worker process: `dramatiq knowledge_service.worker.main`.

Importing this module builds the broker from settings, makes it Dramatiq's
global broker, and declares every actor on it. Dramatiq then reads the
declared queues.
"""

import dramatiq

from knowledge_service.settings import Settings
from knowledge_service.worker.actors import declare_actors, log_ping
from knowledge_service.worker.broker import broker_from_settings
from knowledge_service.worker.jobs import HANDLERS, JobRunner, declare_job_actors

settings = Settings()
broker = broker_from_settings(settings)
dramatiq.set_broker(broker)
actors = declare_actors(broker, record_ping=log_ping)
job_actors = declare_job_actors(
    broker, run_job=JobRunner(settings), kinds=frozenset(HANDLERS)
)
```

**Action 3:** the worker now also needs a database URL. In `tests/test_worker.py`, replace `test_worker_entry_point_declares_ping_on_its_global_broker` with:

```python
def test_worker_entry_point_declares_ping_on_its_global_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "KNOWLEDGE_SERVICE_REDIS_URL", f"redis://127.0.0.1:{closed_port()}/0"
    )
    monkeypatch.setenv(
        "KNOWLEDGE_SERVICE_DATABASE_URL",
        f"postgresql+psycopg://user:pass@127.0.0.1:{closed_port()}/db",
    )
    monkeypatch.delitem(sys.modules, "knowledge_service.worker.main", raising=False)

    entry = importlib.import_module("knowledge_service.worker.main")
    try:
        assert dramatiq.get_broker() is entry.broker
        assert entry.broker.get_declared_actors() == {"ping", "run_maintenance_job"}
    finally:
        entry.broker.close()
        dramatiq.set_broker(StubBroker())
```

**Check:** `uv run pytest tests/test_worker.py -q` reports `40 passed`.

### Step 6 — Coding step: the relay command and Make targets

This step adds a command that runs one relay pass, and lets the worker reach PostgreSQL.

**In plain words:** the relay command connects to Redis and the database, sends every waiting Job that has no message yet, prints what it did, and exits. It exits 1 when Redis refused a message, so a scheduler can notice. The worker and the relay now both get a database URL; when you have not set one, it is built from the local PostgreSQL container when the command runs.

**Action 1:** create `src/knowledge_service/worker/relay.py`:

```python
"""Publish stored Jobs that no message names yet: one pass, then exit.

Run `python -m knowledge_service.worker.relay` after a crash or a Redis
outage, or on a schedule. It exits 0 when every Job it found was published
or skipped, and 1 when the queue refused a message; the rest wait for the
next pass.
"""

import asyncio
import sys
from datetime import UTC, datetime

from knowledge_service.database import create_database_runtime
from knowledge_service.job_dispatch import (
    JobPublisher,
    RelayReport,
    publish_pending_jobs,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import broker_from_settings
from knowledge_service.worker.jobs import (
    HANDLERS,
    DramatiqJobPublisher,
    JobRunner,
    declare_job_actors,
)


async def _relay(settings: Settings, publisher: JobPublisher) -> RelayReport:
    runtime = create_database_runtime(settings)
    try:
        return await publish_pending_jobs(
            runtime, publisher=publisher, now=datetime.now(UTC)
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
        report = asyncio.run(_relay(settings, DramatiqJobPublisher(actors)))
    finally:
        broker.close()
    sys.stdout.write(
        f"published {report.published}, skipped {report.skipped}, "
        f"failed {report.failed}, untried {report.untried}\n"
    )
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

**Action 2:** in the `Makefile`, add `worker-relay` to the `.PHONY` line:

```makefile
.PHONY: do_good setup format format-check lint typecheck test-unit test-integration ml-check keycloak-up keycloak-down keycloak-reset redis-up redis-down worker worker-ready worker-ping worker-relay check hooks-install hooks docs-check
```

**Action 3:** right after the `WORKER_ENV = ...` line, add:

```makefile
# Running Jobs and relaying them reads PostgreSQL too. When
# KNOWLEDGE_SERVICE_DATABASE_URL is unset, the URL is built from the local
# container at run time, so its password never lands in a file.
WORKER_DB_ENV = KNOWLEDGE_SERVICE_DATABASE_URL="$${KNOWLEDGE_SERVICE_DATABASE_URL:-$$(uv run python scripts/test_database_url.py $(TEST_DB_CONTAINER))}"
```

**Action 4:** in the `worker` recipe, give Dramatiq the database URL as well. The second line of the recipe becomes:

```makefile
	@$(WORKER_ENV) $(WORKER_DB_ENV) .venv/bin/dramatiq knowledge_service.worker.main --processes 1 --threads 2
```

**Action 5:** after the `worker-ping` target, add:

```makefile
worker-relay: ## Publish stored Jobs that no message names yet (one pass)
	@$(WORKER_ENV) $(WORKER_DB_ENV) uv run python -m knowledge_service.worker.relay
```

**Check:** `make` lists `worker-relay` in its menu, and `uv run pyright src/knowledge_service/worker` reports 0 errors. Step 10 runs the relay for real.

### Step 7 — Coding step: unit tests for Job messages

This step proves the message format and the publisher without PostgreSQL, using Dramatiq's in-memory broker.

**In plain words:** the tests send a Job through the publisher and look at the raw message: it holds the Job's ID and nothing else. They check that only the queues of runnable kinds get an actor, and that kinds no worker runs are refused. They point the publisher at a closed port and check it fails quickly with the typed error. Then they feed the worker messages with every wrong shape of ID and check none reaches the runner.

| Test | What it proves |
|---|---|
| `test_a_published_job_reaches_the_worker_as_its_id_only` | the message carries one canonical ID; the worker passes it to the runner |
| `test_actors_exist_only_for_the_queues_of_runnable_kinds` | one actor, 60-second limit, no retries; conversation purges are not counted |
| `test_a_kind_no_worker_runs_is_refused_as_a_failed_publish` | synchronization and conversation purges raise `PublishFailed`; nothing is queued |
| `test_an_unreachable_queue_is_a_failed_publish_within_its_timeouts` | a closed port gives `PublishFailed` in under five seconds |
| `test_the_worker_refuses_a_message_that_names_no_canonical_job_id` | eight hostile shapes: `InvalidJobMessage`, one dead letter, the runner never called |
| `test_parse_job_id_accepts_the_canonical_spelling`, `test_a_job_runner_requires_a_database_url` | the accepted form, and `WorkerConfigurationError` |

**Action:** create `tests/test_job_messages.py`:

```python
"""Tests for job messages: what they carry, how they are sent and refused."""

import json
import socket
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import dramatiq
import pytest
from dramatiq.brokers.stub import StubBroker

from knowledge_service.identifiers import JobId
from knowledge_service.job_dispatch import PublishFailed
from knowledge_service.jobs import Job, JobKind
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import (
    QueueName,
    WorkerConfigurationError,
    build_broker,
    declare_queues,
    queue_middleware,
)
from knowledge_service.worker.jobs import (
    DramatiqJobPublisher,
    InvalidJobMessage,
    JobActors,
    JobRunner,
    declare_job_actors,
    parse_job_id,
)

QUEUED_AT = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
JOB_ID = JobId(uuid.UUID("0b8c2d5e-4f6a-4b1c-9d3e-2a7f8e6c5b40"))


def purge_job(kind: JobKind = JobKind.PURGE_DOCUMENT_CHUNKS) -> Job:
    return Job.request(
        job_id=JOB_ID, kind=kind, target_id=uuid.uuid4(), queued_at=QUEUED_AT
    )


@pytest.fixture
def stub_broker() -> Iterator[StubBroker]:
    broker = StubBroker(middleware=queue_middleware())
    declare_queues(broker)
    broker.emit_after("process_boot")
    yield broker
    broker.close()


@pytest.fixture
def ran() -> list[JobId]:
    return []


@pytest.fixture
def job_actors(stub_broker: StubBroker, ran: list[JobId]) -> JobActors:
    return declare_job_actors(
        stub_broker,
        run_job=ran.append,
        kinds=frozenset({JobKind.PURGE_DOCUMENT_CHUNKS}),
    )


def run_worker_until_empty(broker: StubBroker) -> None:
    worker = dramatiq.Worker(broker, worker_timeout=100)
    worker.start()
    try:
        broker.join(QueueName.MAINTENANCE, timeout=10_000, fail_fast=True)
        worker.join()
    finally:
        worker.stop()


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_published_job_reaches_the_worker_as_its_id_only(
    stub_broker: StubBroker, job_actors: JobActors, ran: list[JobId]
) -> None:
    DramatiqJobPublisher(job_actors).publish(purge_job())

    [raw] = list(stub_broker.queues[QueueName.MAINTENANCE].queue)
    message = json.loads(raw)
    assert message["actor_name"] == "run_maintenance_job"
    assert message["args"] == [str(JOB_ID.value)]
    assert message["kwargs"] == {}

    run_worker_until_empty(stub_broker)

    assert ran == [JOB_ID]


def test_actors_exist_only_for_the_queues_of_runnable_kinds(
    stub_broker: StubBroker, job_actors: JobActors
) -> None:
    assert set(job_actors.by_queue) == {QueueName.MAINTENANCE}
    # Conversation purges share the queue, but no worker runs them.
    assert job_actors.kinds == {JobKind.PURGE_DOCUMENT_CHUNKS}
    assert stub_broker.get_declared_actors() == {"run_maintenance_job"}
    actor = job_actors.by_queue[QueueName.MAINTENANCE]
    assert actor.options == {"time_limit": 60_000, "max_retries": 0}


@pytest.mark.parametrize(
    "kind", [JobKind.SYNCHRONIZE_SOURCE, JobKind.PURGE_CONVERSATION]
)
def test_a_kind_no_worker_runs_is_refused_as_a_failed_publish(
    stub_broker: StubBroker, job_actors: JobActors, kind: JobKind
) -> None:
    publisher = DramatiqJobPublisher(job_actors)

    assert kind not in publisher.kinds
    with pytest.raises(PublishFailed):
        publisher.publish(purge_job(kind))
    assert stub_broker.queues[QueueName.MAINTENANCE].qsize() == 0


def test_an_unreachable_queue_is_a_failed_publish_within_its_timeouts() -> None:
    broker = build_broker(f"redis://127.0.0.1:{closed_port()}/0")
    try:
        actors = declare_job_actors(
            broker,
            run_job=lambda _job_id: None,
            kinds=frozenset({JobKind.PURGE_DOCUMENT_CHUNKS}),
        )
        started = time.monotonic()
        with pytest.raises(PublishFailed):
            DramatiqJobPublisher(actors).publish(purge_job())
        assert time.monotonic() - started < 5.0
    finally:
        broker.close()


@pytest.mark.parametrize(
    "argument",
    [
        123,
        None,
        "not-a-uuid",
        str(JOB_ID.value).upper(),
        "{" + str(JOB_ID.value) + "}",
        str(JOB_ID.value).replace("-", ""),
        f" {JOB_ID.value}",
        [str(JOB_ID.value)],
    ],
)
def test_the_worker_refuses_a_message_that_names_no_canonical_job_id(
    stub_broker: StubBroker,
    job_actors: JobActors,
    ran: list[JobId],
    argument: object,
) -> None:
    actor = job_actors.by_queue[QueueName.MAINTENANCE]
    actor.send_with_options(args=(argument,))  # pyright: ignore[reportUnknownMemberType]

    with pytest.raises(InvalidJobMessage):
        run_worker_until_empty(stub_broker)

    assert ran == []
    assert len(stub_broker.dead_letters) == 1


def test_parse_job_id_accepts_the_canonical_spelling() -> None:
    assert parse_job_id(str(JOB_ID.value)) == JOB_ID


def test_a_job_runner_requires_a_database_url() -> None:
    with pytest.raises(WorkerConfigurationError):
        JobRunner(Settings(database_url=None))
```

**Check:** `uv run pytest tests/test_job_messages.py -q` reports `15 passed`.

### Step 8 — Coding step: integration tests against real PostgreSQL

This step proves the commit order, the repair, and the single effect where they really happen: in PostgreSQL.

**In plain words:** a fake publisher stands in for Redis, so each test can make it refuse a message or "crash" right after sending. One test opens a second database connection at the moment of sending and checks that the Job is already committed. Others make the send fail, then run the relay and check it repairs the Job exactly once. Two relays run at the same time and must send each Job once. Finally a real purge Job is run twice, and at the same time from two messages, and must delete its Chunks once.

| Test group | What it proves |
|---|---|
| `test_a_job_is_committed_before_its_message_is_sent` | another connection sees the queued, unsent row at send time |
| `test_a_refused_message_...`, `test_a_crash_after_sending_...` | failure injection on both sides of the send; the relay repairs it, once |
| `test_the_relay_finds_jobs_stored_inside_another_transaction` | a purge Job stored by `tombstone_missing` is relayed |
| `test_two_relays_at_once_send_each_job_once` | `SKIP LOCKED` keeps concurrent relays apart |
| `test_the_relay_stops_...`, `test_the_relay_publishes_oldest_first_...`, `test_the_relay_limit_is_bounded` | stop at the first failure; order; limit 1 to 1,000 |
| `test_jobs_no_worker_runs_...`, `test_the_real_publisher_leaves_kinds_without_a_handler_waiting` | `UnsupportedJobKind`; conversation purges stay unsent |
| `test_a_job_queued_again_needs_a_new_message`, `test_a_finished_or_running_job_is_never_published` | retries are republished; running Jobs are not |
| `test_only_a_new_queued_job_can_be_submitted`, `test_times_must_be_utc` | typed refusals; nothing stored |
| `test_a_duplicate_message_runs_the_job_once`, `test_two_messages_at_once_run_the_job_once` | the real purge runs once; its Job keeps attempt 0 and its first finish time |
| `test_a_message_for_no_stored_job_...`, `test_a_message_for_a_kind_without_a_handler_...` | `InvalidJobMessage`; the Job is left queued |

The shared setup lives in its own module, so Step 9 can reuse it. `tombstoned_with_purge` stores one Document with Chunks, then tombstones it, which stores its purge Job the way a real synchronization pass would.

**Action 1:** create `tests/job_fixtures.py`:

```python
"""Shared setup for the Job dispatch tests against real PostgreSQL."""

from datetime import UTC, datetime

from sqlalchemy import text

from knowledge_service.access import Subject
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document
from knowledge_service.identifiers import DocumentId, JobId, SourceId
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import SourceAccess, SourceRecord
from knowledge_service.jobs import Job
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.jobs import load_job

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000020")
OBSERVED_AT = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)

INSERT_SOURCE = text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/jobs', 'Jobs', true, now(), now())"
)


async def insert_source(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})


async def stored_job(runtime: DatabaseRuntime, job_id: JobId) -> Job:
    async with runtime.transaction() as connection:
        job = await load_job(connection, job_id=job_id)
    assert job is not None
    return job


async def tombstoned_with_purge(runtime: DatabaseRuntime) -> tuple[Document, JobId]:
    """A tombstoned Document that still has Chunks, and its stored purge Job."""
    normalized = normalize(
        SourceRecord.present(
            external_id="retired.md",
            version="v1",
            media_type="text/markdown",
            content=b"# Retired\n\nThis policy was retired.\n",
            access=SourceAccess(public=True),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(normalized, NormalizedDocument)
    result = await reconcile_document(
        runtime, normalized, readers=[Subject.public()], observed_at=OBSERVED_AT
    )
    [tombstone] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=OBSERVED_AT
    )
    return result.document, tombstone.purge_job_id


async def chunk_count(runtime: DatabaseRuntime, document_id: DocumentId) -> int:
    async with runtime.transaction() as connection:
        return len(await load_chunks(connection, document_id=document_id))
```

**Action 2:** create `tests/test_job_dispatch_integration.py`:

```python
"""Persist before enqueue, against real PostgreSQL.

A fake publisher stands in for Redis here, so each test can make the queue
refuse a message or the process "crash" at an exact point.
"""

import asyncio
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from dramatiq.brokers.stub import StubBroker
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from job_fixtures import (
    OBSERVED_AT,
    chunk_count,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.job_dispatch import (
    PublishFailed,
    PublishOutcome,
    RelayReport,
    Submission,
    UnsupportedJobKind,
    publish_job,
    publish_pending_jobs,
    submit_job,
)
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence.jobs import enqueue_job, save_job
from knowledge_service.persistence.schema import jobs
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import QueueName, declare_queues, queue_middleware
from knowledge_service.worker.jobs import (
    HANDLERS,
    DramatiqJobPublisher,
    InvalidJobMessage,
    declare_job_actors,
    run_stored_job,
)

pytestmark = pytest.mark.integration

NOW = OBSERVED_AT + timedelta(minutes=5)
LATER = OBSERVED_AT + timedelta(minutes=10)
PURGE = JobKind.PURGE_DOCUMENT_CHUNKS
RUNNABLE = frozenset({PURGE})


@dataclass
class FakePublisher:
    """Records every message it is asked to send, and can fail on purpose."""

    kinds: frozenset[JobKind] = RUNNABLE
    sent: list[JobId] = field(default_factory=lambda: list[JobId]())
    # Called with each Job before it counts as sent; may raise.
    before_send: Callable[[Job], None] | None = None
    # Called after the message counts as sent; may raise.
    after_send: Callable[[Job], None] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def publish(self, job: Job) -> None:
        if self.before_send is not None:
            self.before_send(job)
        with self.lock:
            self.sent.append(job.job_id)
        if self.after_send is not None:
            self.after_send(job)


def refuse(_job: Job) -> None:
    raise PublishFailed("the queue is down")


def crash(_job: Job) -> None:
    raise RuntimeError("the process stopped before recording the send")


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


def new_job(kind: JobKind = PURGE) -> Job:
    return Job.request(
        job_id=JobId.new(), kind=kind, target_id=uuid.uuid4(), queued_at=OBSERVED_AT
    )


async def published_at(runtime: DatabaseRuntime, job_id: JobId) -> datetime | None:
    async with runtime.transaction() as connection:
        return await connection.scalar(
            sa.select(jobs.c.published_at).where(jobs.c.job_id == job_id.value)
        )


async def job_count(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        result = await connection.execute(sa.select(sa.func.count()).select_from(jobs))
        return result.scalar_one()


async def test_a_job_is_committed_before_its_message_is_sent(
    runtime: DatabaseRuntime, migrated_database: str
) -> None:
    seen_at_send: list[tuple[str, bool]] = []
    other = sa.create_engine(
        make_url(migrated_database).set(drivername="postgresql+psycopg")
    )

    def check_committed(job: Job) -> None:
        # Another connection sees the row only once it is committed.
        with other.connect() as connection:
            row = connection.execute(
                sa.select(jobs.c.status, jobs.c.published_at).where(
                    jobs.c.job_id == job.job_id.value
                )
            ).one()
        seen_at_send.append((row.status, row.published_at is None))

    job = new_job()
    publisher = FakePublisher(before_send=check_committed)

    try:
        submission = await submit_job(runtime, job, publisher=publisher, now=NOW)
    finally:
        other.dispose()

    assert submission == Submission(job_id=job.job_id, published=True)
    assert seen_at_send == [("queued", True)]
    assert publisher.sent == [job.job_id]
    assert await published_at(runtime, job.job_id) == NOW


async def test_a_refused_message_leaves_the_job_stored_for_the_relay(
    runtime: DatabaseRuntime,
) -> None:
    job = new_job()

    submission = await submit_job(
        runtime, job, publisher=FakePublisher(before_send=refuse), now=NOW
    )

    assert submission == Submission(job_id=job.job_id, published=False)
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.QUEUED
    assert await published_at(runtime, job.job_id) is None

    relay = FakePublisher()
    first = await publish_pending_jobs(runtime, publisher=relay, now=LATER)
    second = await publish_pending_jobs(runtime, publisher=relay, now=LATER)

    assert first == RelayReport(published=1, skipped=0, failed=0, untried=0)
    assert second == RelayReport(published=0, skipped=0, failed=0, untried=0)
    assert relay.sent == [job.job_id]
    assert await published_at(runtime, job.job_id) == LATER


async def test_a_crash_after_sending_is_repaired_by_sending_again(
    runtime: DatabaseRuntime,
) -> None:
    job = new_job()
    publisher = FakePublisher(after_send=crash)

    submission = await submit_job(runtime, job, publisher=publisher, now=NOW)

    # The message left, but the send was never recorded.
    assert submission == Submission(job_id=job.job_id, published=False)
    assert publisher.sent == [job.job_id]
    assert await published_at(runtime, job.job_id) is None

    relay = FakePublisher()
    report = await publish_pending_jobs(runtime, publisher=relay, now=LATER)

    assert report.published == 1
    assert relay.sent == [job.job_id]
    assert await published_at(runtime, job.job_id) == LATER


async def test_the_relay_finds_jobs_stored_inside_another_transaction(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    assert await published_at(runtime, purge_id) is None

    relay = FakePublisher()
    report = await publish_pending_jobs(runtime, publisher=relay, now=NOW)

    assert report.published == 1
    assert relay.sent == [purge_id]


async def test_two_relays_at_once_send_each_job_once(
    runtime: DatabaseRuntime,
) -> None:
    stored = [new_job() for _ in range(6)]
    async with runtime.transaction() as connection:
        for job in stored:
            await enqueue_job(connection, job)

    def slow(_job: Job) -> None:
        time.sleep(0.05)

    publisher = FakePublisher(before_send=slow)
    reports = await asyncio.gather(
        publish_pending_jobs(runtime, publisher=publisher, now=NOW),
        publish_pending_jobs(runtime, publisher=publisher, now=NOW),
    )

    assert sorted(publisher.sent, key=str) == sorted(
        (job.job_id for job in stored), key=str
    )
    assert sum(report.published for report in reports) == 6


async def test_the_relay_stops_at_the_first_refused_message(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        for _ in range(3):
            await enqueue_job(connection, new_job())
    attempts: list[Job] = []

    def count_and_refuse(job: Job) -> None:
        attempts.append(job)
        refuse(job)

    report = await publish_pending_jobs(
        runtime, publisher=FakePublisher(before_send=count_and_refuse), now=NOW
    )

    assert report == RelayReport(published=0, skipped=0, failed=1, untried=2)
    assert len(attempts) == 1


async def test_the_relay_publishes_oldest_first_up_to_its_limit(
    runtime: DatabaseRuntime,
) -> None:
    older = Job.request(
        job_id=JobId.new(), kind=PURGE, target_id=uuid.uuid4(), queued_at=OBSERVED_AT
    )
    newer = Job.request(
        job_id=JobId.new(), kind=PURGE, target_id=uuid.uuid4(), queued_at=NOW
    )
    async with runtime.transaction() as connection:
        await enqueue_job(connection, newer)
        await enqueue_job(connection, older)
    relay = FakePublisher()

    report = await publish_pending_jobs(runtime, publisher=relay, now=LATER, limit=1)

    assert report.published == 1
    assert relay.sent == [older.job_id]


async def test_jobs_no_worker_runs_are_neither_submitted_nor_relayed(
    runtime: DatabaseRuntime,
) -> None:
    with pytest.raises(UnsupportedJobKind):
        await submit_job(
            runtime,
            new_job(JobKind.SYNCHRONIZE_SOURCE),
            publisher=FakePublisher(),
            now=NOW,
        )
    assert await job_count(runtime) == 0

    waiting = new_job(JobKind.PURGE_CONVERSATION)
    async with runtime.transaction() as connection:
        await enqueue_job(connection, waiting)
    relay = FakePublisher()

    report = await publish_pending_jobs(runtime, publisher=relay, now=NOW)

    assert report.published == 0
    assert relay.sent == []
    assert await published_at(runtime, waiting.job_id) is None


async def test_a_job_queued_again_needs_a_new_message(
    runtime: DatabaseRuntime,
) -> None:
    job = new_job()
    await submit_job(runtime, job, publisher=FakePublisher(), now=NOW)
    async with runtime.transaction() as connection:
        failed = job.start(started_at=NOW).fail(error_code="boom", finished_at=NOW)
        await save_job(connection, job=failed)
        await save_job(connection, job=failed.retry(queued_at=LATER))
    assert await published_at(runtime, job.job_id) is None

    relay = FakePublisher()
    await publish_pending_jobs(runtime, publisher=relay, now=LATER)

    assert relay.sent == [job.job_id]


async def test_a_finished_or_running_job_is_never_published(
    runtime: DatabaseRuntime,
) -> None:
    job = new_job()
    async with runtime.transaction() as connection:
        await enqueue_job(connection, job)
        await save_job(connection, job=job.start(started_at=NOW))
    publisher = FakePublisher()

    outcome = await publish_job(
        runtime, job_id=job.job_id, publisher=publisher, now=NOW
    )

    assert outcome is PublishOutcome.SKIPPED
    assert publisher.sent == []


async def test_only_a_new_queued_job_can_be_submitted(runtime: DatabaseRuntime) -> None:
    running = new_job().start(started_at=NOW)

    with pytest.raises(InvalidJob):
        await submit_job(runtime, running, publisher=FakePublisher(), now=NOW)

    assert await job_count(runtime) == 0


@pytest.mark.parametrize("limit", [0, 1_001, True, 2.0])
async def test_the_relay_limit_is_bounded(runtime: DatabaseRuntime, limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        await publish_pending_jobs(
            runtime, publisher=FakePublisher(), now=NOW, limit=limit
        )


async def test_times_must_be_utc(runtime: DatabaseRuntime) -> None:
    local = datetime(2026, 11, 21, 9, 0)
    with pytest.raises(ValueError, match="UTC"):
        await submit_job(runtime, new_job(), publisher=FakePublisher(), now=local)
    assert await job_count(runtime) == 0


async def test_a_duplicate_message_runs_the_job_once(
    runtime: DatabaseRuntime,
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    assert await chunk_count(runtime, document.document_id) > 0

    first = await run_stored_job(runtime, purge_id, now=NOW)
    second = await run_stored_job(runtime, purge_id, now=LATER)

    assert first is not None
    assert first.status is JobStatus.SUCCEEDED
    assert second is None
    assert await chunk_count(runtime, document.document_id) == 0
    stored = await stored_job(runtime, purge_id)
    assert (stored.status, stored.attempt, stored.finished_at) == (
        JobStatus.SUCCEEDED,
        0,
        NOW,
    )


async def test_two_messages_at_once_run_the_job_once(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)

    results = await asyncio.gather(
        run_stored_job(runtime, purge_id, now=NOW),
        run_stored_job(runtime, purge_id, now=NOW),
    )

    assert sum(result is not None for result in results) == 1
    assert (await stored_job(runtime, purge_id)).status is JobStatus.SUCCEEDED


async def test_a_message_for_no_stored_job_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    with pytest.raises(InvalidJobMessage):
        await run_stored_job(runtime, JobId.new(), now=NOW)


async def test_a_message_for_a_kind_without_a_handler_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    waiting = new_job(JobKind.PURGE_CONVERSATION)
    async with runtime.transaction() as connection:
        await enqueue_job(connection, waiting)

    with pytest.raises(InvalidJobMessage):
        await run_stored_job(runtime, waiting.job_id, now=NOW)

    assert (await stored_job(runtime, waiting.job_id)).status is JobStatus.QUEUED


async def test_the_real_publisher_leaves_kinds_without_a_handler_waiting(
    runtime: DatabaseRuntime,
) -> None:
    broker = StubBroker(middleware=queue_middleware())
    declare_queues(broker)
    try:
        publisher = DramatiqJobPublisher(
            declare_job_actors(
                broker, run_job=lambda _job_id: None, kinds=frozenset(HANDLERS)
            )
        )
        conversation_purge = new_job(JobKind.PURGE_CONVERSATION)
        async with runtime.transaction() as connection:
            await enqueue_job(connection, conversation_purge)

        with pytest.raises(UnsupportedJobKind):
            await submit_job(
                runtime,
                new_job(JobKind.PURGE_CONVERSATION),
                publisher=publisher,
                now=NOW,
            )
        report = await publish_pending_jobs(runtime, publisher=publisher, now=NOW)

        assert report == RelayReport(published=0, skipped=0, failed=0, untried=0)
        assert await published_at(runtime, conversation_purge.job_id) is None
        assert broker.queues[QueueName.MAINTENANCE].qsize() == 0
    finally:
        broker.close()
```

**Check:** `uv run pytest tests/test_job_dispatch_integration.py -q -m integration` reports `21 passed`.

### Step 9 — Coding step: one Job end to end through Redis

This step runs the whole path with real Redis and a real Dramatiq worker.

**In plain words:** a purge Job is stored by tombstoning a Document. The relay first tries a Redis that is not there, fails, and leaves the Job waiting. Then the relay runs against the real Redis, a worker runs the Job, and the Document's Chunks are gone. Finally a second message for the same Job is sent, and the stored Job does not change and nothing is dead-lettered.

**Action:** create `tests/test_job_queue_integration.py`:

```python
"""A stored Job through real Redis and a real Dramatiq worker, end to end."""

import os
import socket
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta

import dramatiq
import pytest
from dramatiq.brokers.redis import RedisBroker
from pydantic import SecretStr

from job_fixtures import (
    OBSERVED_AT,
    chunk_count,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.job_dispatch import RelayReport, publish_pending_jobs
from knowledge_service.jobs import JobStatus
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import QueueName, build_broker, redis_client
from knowledge_service.worker.jobs import (
    HANDLERS,
    DramatiqJobPublisher,
    JobActors,
    JobRunner,
    declare_job_actors,
)

pytestmark = pytest.mark.integration

REDIS_URL_ENV = "KNOWLEDGE_SERVICE_TEST_REDIS_URL"
NOW = OBSERVED_AT + timedelta(minutes=5)


@pytest.fixture
def redis_url() -> str:
    configured = os.environ.get(REDIS_URL_ENV)
    if not configured:
        pytest.skip(f"set {REDIS_URL_ENV} to a disposable Redis server")
    return configured


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


@pytest.fixture
def redis_broker(redis_url: str) -> Iterator[RedisBroker]:
    namespace = f"knowledge-service.test-{uuid.uuid4()}"
    broker = build_broker(redis_url, namespace=namespace)
    broker.emit_after("process_boot")
    yield broker
    broker.flush_all()
    broker.close()
    cleaner = redis_client(redis_url)
    try:
        cleaner.delete(f"{namespace}:__heartbeats__")
    finally:
        cleaner.close()


@pytest.fixture
def job_actors(redis_broker: RedisBroker, migrated_database: str) -> JobActors:
    runner = JobRunner(Settings(database_url=SecretStr(migrated_database)))
    return declare_job_actors(redis_broker, run_job=runner, kinds=frozenset(HANDLERS))


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def test_a_job_stored_while_redis_was_down_runs_once_after_the_relay(
    runtime: DatabaseRuntime,
    redis_broker: RedisBroker,
    job_actors: JobActors,
    redis_url: str,
) -> None:
    # A synchronization pass stored the purge Job; no message names it yet.
    document, purge_id = await tombstoned_with_purge(runtime)

    # The queue is unreachable: the relay fails and the Job keeps waiting.
    down = build_broker(f"redis://127.0.0.1:{closed_port()}/0")
    try:
        down_actors = declare_job_actors(
            down, run_job=lambda _job_id: None, kinds=frozenset(HANDLERS)
        )
        refused = await publish_pending_jobs(
            runtime, publisher=DramatiqJobPublisher(down_actors), now=NOW
        )
    finally:
        down.close()
    assert refused == RelayReport(published=0, skipped=0, failed=1, untried=0)
    assert (await stored_job(runtime, purge_id)).status is JobStatus.QUEUED

    # Redis is back: the relay publishes it, and a worker runs it.
    publisher = DramatiqJobPublisher(job_actors)
    relayed = await publish_pending_jobs(runtime, publisher=publisher, now=NOW)
    assert relayed == RelayReport(published=1, skipped=0, failed=0, untried=0)

    worker = dramatiq.Worker(redis_broker, worker_timeout=100, worker_threads=2)
    worker.start()
    try:
        redis_broker.join(QueueName.MAINTENANCE, timeout=20_000)
        finished = await stored_job(runtime, purge_id)
        assert finished.status is JobStatus.SUCCEEDED
        assert await chunk_count(runtime, document.document_id) == 0

        # A second message for the same Job, as after a crash before its send
        # was recorded, changes nothing.
        publisher.publish(finished)
        redis_broker.join(QueueName.MAINTENANCE, timeout=20_000)
        assert await stored_job(runtime, purge_id) == finished
    finally:
        worker.stop()

    # Neither message failed: nothing reached the dead-letter queue.
    inspector = redis_client(redis_url)
    try:
        dead = inspector.zcard(f"{redis_broker.namespace}:maintenance.XQ")
    finally:
        inspector.close()
    assert dead == 0
```

**Check:** `uv run pytest tests/test_job_queue_integration.py -q -m integration` reports `1 passed`.

### Step 10 — No coding in this step: inject the failure for real

This step shows the acceptance criterion with a real worker process: Redis is down when the relay runs, and the Job is repaired once Redis is back.

It uses a separate demo database, so your local data is never touched. Run every command in the **same terminal**, and do not pipe `source` or the blocks below into another command, or the database URL is lost.

**Action 1:** create the demo database, migrate it, and store one purge Job:

```bash
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/[^/]*$#/m08_t02_demo#')"
uv run python - <<'EOF'
import os

import sqlalchemy as sa
from sqlalchemy.engine import make_url

url = make_url(os.environ["KNOWLEDGE_SERVICE_DATABASE_URL"])
admin = sa.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
with admin.connect() as connection:
    connection.execute(sa.text("DROP DATABASE IF EXISTS m08_t02_demo"))
    connection.execute(sa.text("CREATE DATABASE m08_t02_demo"))
EOF
uv run alembic upgrade head
PYTHONPATH=tests uv run python - <<'EOF'
import asyncio

from job_fixtures import chunk_count, insert_source, tombstoned_with_purge
from knowledge_service.database import create_database_runtime
from knowledge_service.settings import Settings


async def main() -> None:
    runtime = create_database_runtime(Settings())
    try:
        await insert_source(runtime)
        document, job_id = await tombstoned_with_purge(runtime)
        chunks = await chunk_count(runtime, document.document_id)
        print(f"purge job {job_id.value}, chunks {chunks}")
    finally:
        await runtime.dispose()


asyncio.run(main())
EOF
```

You see `purge job <id>, chunks 1`.

**Action 2:** keep this helper at hand; it prints the Job's state and the Chunk count:

```bash
uv run python - <<'EOF'
import os

import sqlalchemy as sa
from sqlalchemy.engine import make_url

url = make_url(os.environ["KNOWLEDGE_SERVICE_DATABASE_URL"])
engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
with engine.connect() as connection:
    jobs = connection.execute(
        sa.text("select status, published_at is not null as published from jobs")
    ).all()
    chunks = connection.execute(sa.text("select count(*) from chunks")).scalar_one()
print("jobs:", [tuple(row) for row in jobs], "chunks:", chunks)
EOF
```

It prints `jobs: [('queued', False)] chunks: 1`.

**Action 3:** stop Redis and run the relay:

```bash
docker compose -p knowledge-service-redis stop
make worker-relay
```

It prints `published 0, skipped 0, failed 1, untried 0` and fails. The helper still prints `('queued', False)`.

**Action 4:** start Redis, start a worker in a **second terminal** with the same `KNOWLEDGE_SERVICE_DATABASE_URL` exported, and run the relay again in the first:

```bash
make redis-up
make worker-relay
```

The relay prints `published 1`. The worker logs `job <id> (purge_document_chunks) ran: succeeded`, and the helper prints `jobs: [('succeeded', True)] chunks: 0`. A third `make worker-relay` prints `published 0`.

**Action 5:** stop the worker with Ctrl+C, then drop the demo database:

```bash
uv run python - <<'EOF'
import os

import sqlalchemy as sa
from sqlalchemy.engine import make_url

url = make_url(os.environ["KNOWLEDGE_SERVICE_DATABASE_URL"])
admin = sa.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
with admin.connect() as connection:
    connection.execute(sa.text("DROP DATABASE IF EXISTS m08_t02_demo"))
EOF
unset KNOWLEDGE_SERVICE_DATABASE_URL
```

**Check:** paste the relay outputs, the worker's log line, and the helper's three outputs into "Verification" in your evidence file.

### Step 11 — No coding in this step: break it on purpose

This step removes the line that makes a retried Job need a new message, and shows which test notices.

**Action 1:** in `save_job` in `src/knowledge_service/persistence/jobs.py`, delete these two lines:

```python
    if job.status is JobStatus.QUEUED:
        values["published_at"] = None
```

**Action 2:** run the three test files:

```bash
uv run pytest tests/test_job_messages.py tests/test_job_dispatch_integration.py tests/test_job_queue_integration.py -q -m "integration or not integration"
```

One test fails: `test_a_job_queued_again_needs_a_new_message`. Without the line, a retried Job keeps the old send time, so the relay thinks it was already sent and the new attempt never reaches a worker.

**Action 3:** put the lines back and run the command again: all 37 pass.

**Check:** compare the count with your Step 1 prediction, and note it in the evidence file.

### Step 12 — No coding in this step: documents, gates, evidence, and progress

This step brings the two documents up to date and runs every gate.

**Action 1:** in `docs/architecture.md`, add this paragraph just before the paragraph that starts with `` `telemetry` decides what leaves the process as logs``:

```markdown
`job_dispatch` puts a Job on the queue without ever losing it. `submit_job`
commits the Job to PostgreSQL first, then sends one message naming only its
ID, under a lock on its row, and records the send in `published_at`. When
the queue refuses the message, or the process stops before the send is
recorded, the Job stays queued and unpublished; `publish_pending_jobs`
(`make worker-relay`) sends such Jobs later, oldest first, and stops at the
first refused message. Jobs that other workflows store in their own
transactions, such as the purge `tombstone_missing` queues, reach the queue
the same way. A Job queued again for another attempt is unpublished again.
Only kinds a worker runs are submitted or sent. A Job can be named by two
messages; the worker runs it only while it is still queued.
```

and replace the `worker` row of the code layout table with:

```markdown
| `worker` | Queue transport: the Redis broker and the worker process | `build_broker` (`broker`, a Dramatiq `RedisBroker` with JSON messages, the `knowledge-service.v1` namespace, the `maintenance` and `synchronization` queues, a 60-second default time limit, a one-day message age limit, and no Dramatiq retries; `ActorOwnsOptions` drops any limit a message sets for itself, so only its actor's limits apply; Redis calls make one attempt with bounded timeouts), `declare_actors` / `send_ping` (`actors`, the harmless `ping` job, whose token is checked on both sides), `broker_ready` (`health`, a readiness probe that is false, within one overall deadline, whenever Redis does not answer PING or any part of the check fails), `declare_job_actors` / `DramatiqJobPublisher` / `run_stored_job` (`jobs`, one `run_<queue>_job` actor per queue of the kinds in `HANDLERS`, today only `purge_document_chunks`; a message carries one canonical Job ID, and the worker reads the Job from PostgreSQL, runs it only while it is queued, and ignores a duplicate; `JobRunner` gives each message its own event loop and database engine), `relay` (one pass of `publish_pending_jobs`), and `main`, the module the `dramatiq` command loads |
```

**Action 2:** in `docs/threat-model.md`, replace the `Jobs` row with:

```markdown
| Jobs | Workers | Stored jobs with typed errors. A queue message, JSON and never pickle, carries only the harmless `ping` token or one canonical Job ID; the worker reads what to do from PostgreSQL. A stored Job is committed before any message names it, and a Job that is no longer queued is not run again. A Job whose message was sent but then lost (a Redis restart, a message dead-lettered before the Job was claimed, or one dropped by the one-day age limit) stays queued and published until M08-T08 |
```

**Action 3:** run every gate:

```bash
make format-check
make lint
make typecheck
make test-unit
make test-integration
make docs-check
```

**Action 4:** finish the evidence file: the gate results under "Verification", the review findings and known limits under "Review", the design under "Design" and "Decision", and your answers to the reflection questions below. Then mark the task completed in `docs/curriculum/PROGRESS.md` and commit with `m08-t02: persist before enqueue`.

**Check:** every gate passes, and `git status` is clean after the commit.

## Reflection questions

- `submit_job` returns `published=False`. Is the work lost? What will make it run, and what could still stop it from running?
- Suppose we sent the message first and saved the Job second. Describe one sequence of events where a worker gets a message for a Job that does not exist, and one where a Job exists that no message will ever name.
- The relay can send a second message for a Job that already has one. Why did we accept that instead of trying to send exactly once, and what makes the second message harmless?
- Two messages for the same purge Job reach two worker threads at the same moment. Walk through what each thread does, and say which database step decides that only one of them deletes the Chunks.
- What observation would prove this design wrong? For example: what would you need to see in production to decide the relay is not enough on its own?

## Completion checklist

- [ ] The evidence file has the invariant, the owners, and your predictions, written before the change.
- [ ] Migration `c2d3e4f5a6b7` adds `jobs.published_at` and the partial index; the index test and migration test pass.
- [ ] `persistence/jobs.py` lists, locks with `SKIP LOCKED`, and marks unsent Jobs; saving a queued Job clears `published_at`.
- [ ] `job_dispatch.py` commits before publishing, never raises for a publishing problem after the commit, and the relay stops at the first failure.
- [ ] Messages carry only a canonical Job ID; the worker runs a Job only while it is queued; only `purge_document_chunks` is published.
- [ ] `make worker-relay` exists, and the worker reaches PostgreSQL.
- [ ] `tests/test_job_messages.py` (15), `tests/test_job_dispatch_integration.py` (21), and `tests/test_job_queue_integration.py` (1) pass, and `tests/test_worker.py` still has 40.
- [ ] You saw the relay fail with Redis down, then publish once, and the worker run the Job once.
- [ ] You ran the deliberate break, saw 1 test fail, and undid it.
- [ ] `docs/architecture.md` and `docs/threat-model.md` describe publishing, the relay, and the known limits.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
