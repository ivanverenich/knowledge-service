# M08-T03 — Enforce job idempotency

Task: [M08-T03 — Enforce job idempotency](../curriculum/milestones/M08-jobs-and-confluence/M08-T03-enforce-job-idempotency.md)

## What you will do

This lesson makes sure that asking for the same work twice, or delivering the same message twice, never does the work twice. M08-T02 made sure no Job is lost; this lesson makes sure no Job is doubled, and that every duplicate gets the same answer as the original.

- **One open Job per intent.** A Job's intent is what it is for: its kind and its target, such as "purge the Chunks of Document D". The database keeps at most one open Job per intent.
- **A repeat request gets the open Job.** Asking again, even at the same moment, returns the Job that already exists instead of storing a second one.
- **A failed Job is picked up again.** When the open Job failed but still has attempts left, a new request queues that same Job again.
- **The outcome commits with the claim.** The purge handler takes the Job and stores its result, success or failure, in one transaction, so no other delivery can slip in between.
- **Duplicates report the stored outcome.** A second message for a Job returns the Job as the first message left it, so every delivery reports the same result.
- **Proof:** tests ask for the same work twice, at the same moment, after a failure, and during a migration, and check there is one Job and one effect.

| What the task asks | Where it is done |
|---|---|
| Idempotency keys derived from business intent | Step 3: the intent is kind plus target, enforced by the index `uq_jobs_open_intent` |
| Claim work transactionally | Step 5: the purge claims, deletes, and stores its outcome in one transaction |
| Return prior outcomes for safe duplicates | Step 4: `enqueue_job` returns the open Job; Step 6: `run_stored_job` returns the stored Job for a duplicate message |
| Concurrent duplicate deliveries: one effect, consistent terminal metadata | Step 7: two and three messages at once, also when the attempt fails |
| Success, boundary, and typed failure or denial | Step 7: `InvalidJob`, `JobIntentContended`, the database refusing a second open Job |
| Real infrastructure where its semantics are under test | Step 7: real PostgreSQL locks, unique index, and migration |
| Update only affected documentation | Step 9: `docs/architecture.md`, `docs/threat-model.md` |

This lesson includes coding: Steps 2 to 7 write code, a migration, and tests. Steps 1, 8, 9, and 10 need no code, although Step 9 edits two documents.

Terms used in this lesson:

- **Idempotent:** doing it twice has the same effect as doing it once, like pressing a lift button twice.
- **Intent:** what a Job is for, written as its kind plus its target. Two requests with the same intent ask for the same work.
- **Idempotency key:** the value that tells two requests apart or marks them as the same. Here it is the intent itself, not a random token the caller invents.
- **Open Job:** a Job that can still do its work: queued, running, or failed with attempts left.
- **Partial unique index:** a database rule that two rows may not share a value, applied only to rows that match a condition. Here: no two *open* Jobs may share an intent, while finished Jobs may.
- **Claim:** taking a queued Job for one attempt, under a lock on its row, so nobody else can take it at the same time.
- **Savepoint:** a checkpoint inside a transaction. Rolling back to it undoes only the work after it, and keeps everything before it, including locks.

## Before you begin

This section explains the choices the code rests on, so each step makes sense when you reach it.

- **What exists already.** M08-T02 stores a Job before any message names it, and a worker runs a Job only while it is queued. But nothing stops two Jobs for the same work: tombstoning a Document twice stores two purges, and `submit_job` stores a new Job every time.
- **Why the key comes from intent.** "Purge Document D" is the same work no matter who asks or when. Using kind plus target as the key needs nothing from the caller, and a caller cannot get around it by inventing a new token.
- **Why only open Jobs count.** Once a purge has finished, the same Document may need purging again later, for example after it comes back and is deleted again. So the rule covers only Jobs that can still run; finished Jobs stay as history.
- **Why a failed Job with attempts left is open.** It may still run again, so a second Job beside it would do the same work twice. Instead, a new request queues that failed Job again for its next attempt.
- **Why the database enforces it.** Checking "is there an open Job?" and then inserting lets two callers both see "no" and both insert. The unique index makes the second insert wait for the first and then find it, so no interleaving can store two.
- **Why the outcome commits with the claim.** M08-T02's purge stored a failure in a second transaction. Between the two, the Job looked queued again, so a waiting duplicate message ran the work a second time. Now a savepoint undoes the failed delete while the claim's lock stays, and the failure is stored before the lock is released.
- **What a duplicate gets back.** M08-T02 returned `None` for a duplicate message. Now it gets the stored Job, so every delivery of one message reports the same status, attempt, and finish time.
- **The migration cleans up first.** Old code could leave two open Jobs for one intent, and a unique index cannot be built over them. The migration keeps one per intent (running first, then queued, then failed; oldest first) and closes the others as failed with `superseded_duplicate`.
- **What the review found.** An independent adversarial review attacked the first version of this code:
  - A purge whose delete failed released its lock before storing the failure, so a waiting duplicate ran the work again and the others got a raw error. Now the outcome commits with the claim, and a test with three messages at once fails on the old shape.
  - A Document tombstoned again after a failed purge got that failed Job back and nothing ever ran it. Now the request queues it again, and two tests fail without that.
  - The migration failed on databases with duplicate open Jobs, and its first fix kept a failed Job over a queued one. Now it cleans up first and prefers the Job a worker will run.
  - `enqueue_job` accepted a finished Job. Now it accepts only a new queued one.
  - Storms of up to 12 concurrent requesters, rollbacks, and state changes never left two open Jobs for one intent.
- **Known limits.** These stay as they are for now:
  - A failed Job with attempts left runs again only when someone asks for the same work again. Nothing retries it on its own until M08-T04 assigns retry ownership.
  - A message for an earlier attempt can still start the next attempt once the Job is queued again (from M08-T02). Each attempt still has one effect; M08-T04 owns attempt timing.
  - Intent is the whole key. Two requests that mean different things for the same target and kind, such as a full and an incremental synchronization, would count as one; M08-T10 decides if synchronization needs a richer intent.
  - Handlers must raise `JobAlreadyClaimed` when a Job is no longer queued; any other error from a handler is not treated as a duplicate.

## Step-by-step instructions

Start PostgreSQL before you begin. Run every command from the repository root. For integration checks, set the test database URL in your terminal:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

Run only one integration command against this database at a time. Two runs at once migrate the same database together and can leave it in a broken state.

### Step 1 — No coding in this step: write the invariant and your predictions

Before changing anything, write down what must be true at the end and what you expect to see. You check the predictions in Step 8 and Step 10.

Create `docs/evidence/M08-T03-enforce-job-idempotency.md`:

```markdown
# M08-T03 — Enforce job idempotency evidence

## Invariant

At most one open Job (queued, running, or failed with attempts left) exists
per intent, its kind and target, under any interleaving of requests. A
repeated request returns the open Job instead of storing a second one, and
queues it again if it failed. A handler claims a queued Job and stores its
outcome in the same transaction, so concurrent deliveries of one Job have
one effect, and every delivery reports the Job as stored.

## Owners

- Durable truth: the `jobs` row and the `uq_jobs_open_intent` index.
- One Job per intent: `enqueue_job`, for every caller.
- Running once: the handler's claim under a row lock, in one transaction.
- Retries: the stored Job (M08-T04); a new request also queues a failed Job again.
- Authorization: none yet; nothing outside the service submits Jobs.

## Expected failing check

Before the change,
`uv run pytest tests/test_job_idempotency_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.

## Predictions

- What a second `tombstone_missing` of the same Document returns as its purge Job:
- What three messages at once for a purge whose delete fails do to the Chunks and to the attempt count:
- How many tests fail if `enqueue_job` stops using `ON CONFLICT DO NOTHING`:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: say when a Job is open, and how a refused claim looks

This step adds two small pieces to the Job value that the rest of the lesson uses.

**In plain words:** a Job learns to answer one question: can it still do its work? It can while it waits, while it runs, and after a failure that still has tries left. A second piece is a clear error for "this Job was already taken"; it carries the Job as it was stored, so whoever gets the error can report what the earlier delivery did.

| Piece | What it does |
|---|---|
| `Job.is_open` | true for queued and running Jobs, and for failed Jobs with attempts left |
| `JobAlreadyClaimed` | an `InvalidJob` raised by a claim that finds the Job no longer queued; `.job` is the stored Job |

The message stays "only a queued job can run, not a ... one", so the existing test that matches that text keeps passing.


**Action 1:** add the error just before `JobKind`.

In `src/knowledge_service/jobs.py`, find:

```python
class JobKind(StrEnum):
    """What a Job was queued to do."""
```

and replace it with:

```python
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

**Action 2:** add the property just before `request`, at the end of `__post_init__`.

In `src/knowledge_service/jobs.py`, find:

```python
            raise InvalidJob("an error code must not be empty")

    @classmethod
    def request(
```

and replace it with:

```python
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
```

**Action 3:** test both pieces. First add the import.

In `tests/test_jobs.py`, find:

```python
    IllegalJobTransition,
    InvalidJob,
    Job,
```

and replace it with:

```python
    IllegalJobTransition,
    InvalidJob,
    Job,
    JobAlreadyClaimed,
```

Then add these two tests at the end of `tests/test_jobs.py`:

```python
def test_a_job_is_open_until_it_can_do_no_more_work() -> None:
    job = requested_job(max_attempts=2)
    started = job.start(started_at=QUEUED_AT)
    failed_once = started.fail(error_code="boom", finished_at=QUEUED_AT)
    failed_twice = (
        failed_once.retry(queued_at=QUEUED_AT)
        .start(started_at=QUEUED_AT)
        .fail(error_code="boom", finished_at=QUEUED_AT)
    )

    assert job.is_open
    assert started.is_open
    # A failed Job with attempts left may still be queued again.
    assert failed_once.is_open
    assert not failed_twice.is_open
    assert not started.succeed(finished_at=QUEUED_AT).is_open


def test_a_refused_claim_carries_the_job_as_stored() -> None:
    finished = (
        requested_job().start(started_at=QUEUED_AT).succeed(finished_at=QUEUED_AT)
    )

    refused = JobAlreadyClaimed(finished)

    assert isinstance(refused, InvalidJob)
    assert refused.job is finished
    assert str(refused) == "only a queued job can run, not a succeeded one"
```

**Check:** `uv run pytest tests/test_jobs.py -q` reports `6 passed`.

### Step 3 — Coding step: let the database allow one open Job per intent

This step adds the rule to the database, so no code path can break it.

**In plain words:** the database learns that two Jobs that can still run may not share a kind and a target. Finished Jobs do not count, so history is kept. Before the rule is switched on, a migration tidies up any duplicates older code may have left, keeping the one a worker is most likely to run.

| Piece | What it holds or does |
|---|---|
| `OPEN_JOB_PREDICATE` | the condition for an open Job, written once in SQL, shared by the index and by `enqueue_job` |
| `uq_jobs_open_intent` | a partial unique index on `kind, target_id` over open Jobs only |
| Migration `e5f6a7b8c9d0` | closes duplicate open Jobs, then creates the index |

The migration keeps, per intent, a running Job first, then a queued one, then a failed one, and the oldest within each. It closes the rest as failed with no attempts left and the code `superseded_duplicate`. Their work is the same, so the Job it keeps still does it. The migration repeats the condition as text instead of importing it, because a migration must keep working even when the application code changes later.


**Action 1:** create the migration.

Create `migrations/versions/e5f6a7b8c9d0_job_open_intent.py`:

```python
"""job open intent

Revision ID: e5f6a7b8c9d0
Revises: c2d3e4f5a6b7
Create Date: 2026-11-22 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "c2d3e4f5a6b7"

OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and attempt < max_attempts)"
)

# Earlier code queued a new Job for every request, so two open Jobs can share
# an intent. Keep one per intent and close the others as failed with no
# attempts left. Their work is the same, so the one kept still does it. A
# running Job is kept first, then a queued one, because a worker will run it;
# a failed one waits for a retry, so it is kept only when nothing else is.
CLOSE_DUPLICATE_OPEN_JOBS = """
with ranked as (
    select
        job_id,
        row_number() over (
            partition by kind, target_id
            order by
                case status when 'running' then 0 when 'queued' then 1 else 2 end,
                queued_at,
                job_id
        ) as rank
    from jobs
    where status in ('queued', 'running')
        or (status = 'failed' and attempt < max_attempts)
)
update jobs
set status = 'failed',
    attempt = max_attempts,
    started_at = coalesce(started_at, queued_at),
    finished_at = coalesce(finished_at, greatest(now(), started_at, queued_at)),
    last_error_code = 'superseded_duplicate'
from ranked
where jobs.job_id = ranked.job_id and ranked.rank > 1
"""


def upgrade() -> None:
    op.execute(CLOSE_DUPLICATE_OPEN_JOBS)
    op.create_index(
        "uq_jobs_open_intent",
        "jobs",
        ["kind", "target_id"],
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    )


def downgrade() -> None:
    # Duplicates closed by the upgrade stay closed: reopening them would
    # queue the same work twice.
    op.drop_index("uq_jobs_open_intent", table_name="jobs")
```

**Action 2:** declare the same rule in the schema. First the condition, just above the `jobs` table:

In `src/knowledge_service/persistence/schema.py`, find:

```python
jobs = sa.Table(
```

and replace it with:

```python
# A Job that can still do its work: queued, running, or failed with attempts
# left. At most one such Job exists per kind and target (its intent).
OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and attempt < max_attempts)"
)

jobs = sa.Table(
```

Then the index, as the table's last item:

In `src/knowledge_service/persistence/schema.py`, find:

```python
        postgresql_where=sa.text("status = 'queued' and published_at is null"),
    ),
)
```

and replace it with:

```python
        postgresql_where=sa.text("status = 'queued' and published_at is null"),
    ),
    sa.Index(
        "uq_jobs_open_intent",
        "kind",
        "target_id",
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    ),
)
```

**Action 3:** allow the new index in the index list.

In `tests/test_indexes.py`, find:

```python
    "uq_embedding_spaces_current",

```

and replace it with:

```python
    "uq_embedding_spaces_current",
    "uq_jobs_open_intent",

```

**Check:** `uv run pytest tests/test_indexes.py -q` reports `4 passed`, and `uv run pytest tests/test_migrations_integration.py -q -m integration` reports `3 passed`, which proves the migration and the declared table agree.

### Step 4 — Coding step: store a Job only when its intent has no open Job

This step changes how every Job is stored, so a repeat request returns the Job that already exists.

**In plain words:** storing a Job now first tries to insert it, and the database quietly skips the insert when an open Job with the same intent exists. The code then reads that open Job and returns it. If the open Job had failed, it is queued again for its next try. If two requests arrive together, the second waits for the first to commit and then finds its Job, so only one is ever stored.

| Piece | What it does |
|---|---|
| `Enqueued` | the open Job for the intent, and `created`: whether this call stored it |
| `enqueue_job` | refuses anything but a new queued Job; inserts with `ON CONFLICT DO NOTHING` on the open-intent index; otherwise returns the open Job, queuing a failed one again |
| `open_job` / `load_open_job` | the SQL condition for an open Job, and reading the open Job for an intent |
| `_queue_again` | locks the failed open Job, checks it is still failed, and saves `retry` |
| `JobIntentContended` | raised if, three times in a row, the open Job finished between the insert and the read |

Three choices matter here:

- **The database decides, not a check first.** `ON CONFLICT DO NOTHING` makes the second insert wait for the first transaction and then skip. A "select, then insert" would let both callers see nothing and both insert.
- **The read can miss, so it is bounded.** The open Job can finish between the skipped insert and the read. Then the code tries again, at most three rounds, and raises a typed error instead of looping.
- **Queuing a failed Job again takes its lock.** Two requests may both see the failed Job; the lock makes the second one see it already queued, so it is queued once.

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


def open_job() -> sa.ColumnElement[bool]:
    """Rows of Jobs that can still do their work; see `Job.is_open`."""
    return sa.or_(
        jobs.c.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
        sa.and_(
            jobs.c.status == JobStatus.FAILED.value,
            jobs.c.attempt < jobs.c.max_attempts,
        ),
    )


async def load_open_job(
    connection: AsyncConnection,
    *,
    kind: JobKind,
    target_id: UUID,
    for_update: bool = False,
) -> Job | None:
    """The open Job of this kind for this target, if there is one."""
    statement = sa.select(jobs).where(
        jobs.c.kind == kind.value, jobs.c.target_id == target_id, open_job()
    )
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def enqueue_job(connection: AsyncConnection, job: Job) -> Enqueued:
    """Store new queued `job`, unless an open Job with the same intent exists.

    The intent is the Job's kind and target. When an open Job for it exists,
    that Job is returned and `job` is not stored, so asking twice for the
    same work, even at the same moment, leaves one Job to do it. An open Job
    that failed with attempts left is queued again for its next attempt,
    because the new request still wants the work done.
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
        if existing is not None and existing.status is JobStatus.FAILED:
            existing = await _queue_again(connection, job)
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


async def _queue_again(connection: AsyncConnection, job: Job) -> Job | None:
    """Queue the failed open Job for `job`'s intent again, under its row lock."""
    existing = await load_open_job(
        connection, kind=job.kind, target_id=job.target_id, for_update=True
    )
    if existing is None or existing.status is not JobStatus.FAILED:
        # Another request queued it again first, or it is gone: use what is.
        return existing
    return await save_job(connection, job=existing.retry(queued_at=job.queued_at))


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

**Action 2:** `load_unpurged_documents` already spelled out the same condition by hand. Use the shared one, so the two can never drift apart. First the imports:

In `src/knowledge_service/persistence/documents.py`, find:

```python
from knowledge_service.jobs import JobKind, JobStatus
from knowledge_service.language import Language

```

and replace it with:

```python
from knowledge_service.jobs import JobKind
from knowledge_service.language import Language
from knowledge_service.persistence.jobs import open_job

```

Then the condition itself:

In `src/knowledge_service/persistence/documents.py`, find:

```python
        jobs.c.target_id == documents.c.document_id,
        sa.or_(
            jobs.c.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            sa.and_(
                jobs.c.status == JobStatus.FAILED.value,
                jobs.c.attempt < jobs.c.max_attempts,
            ),
        ),
    )
```

and replace it with:

```python
        jobs.c.target_id == documents.c.document_id,
        open_job(),
    )
```

**Check:** `uv run pyright src/knowledge_service/persistence` reports 0 errors. The callers change in the next two steps.

### Step 5 — Coding step: claim the purge and store its outcome in one transaction

This step fixes the purge so a failed attempt can never be run twice.

**In plain words:** the purge takes its Job, tries to delete the Chunks, and stores the result, all before it lets go of the Job. If the delete fails, only the delete is undone; the Job stays taken until the failure is written down. A second message waiting for the same Job then sees the stored result, never a Job that looks untouched. Tombstoning a Document now also reuses its open purge instead of storing another.

| Piece | What changes |
|---|---|
| `purge_document_chunks` | one transaction: claim, delete inside a savepoint, store `succeeded` or `failed` |
| `_claim` | raises `JobAlreadyClaimed` with the stored Job when it is not queued |
| `_queue_purge` | returns the open purge from `enqueue_job`, new or existing |
| `Tombstone`, `requeue_unfinished_purges` | docstrings say a purge may be reused |

`connection.begin_nested()` starts a savepoint. The project already uses it in `persistence/conversations.py`.


**Action 1:** import the new error.

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus

```

and replace it with:

```python
from knowledge_service.jobs import (
    InvalidJob,
    Job,
    JobAlreadyClaimed,
    JobKind,
    JobStatus,
)

```

**Action 2:** update the two docstrings. First `Tombstone`:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
    """One Document made unavailable, and the Job that will purge its Chunks."""
```

and replace it with:

```python
    """One Document made unavailable, and the open Job that will purge its Chunks.

    When a purge of the Document was already open, that Job is reused.
    """
```

Then `requeue_unfinished_purges`:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
    """Queue a new purge for each tombstoned Document no purge can still clean."""
```

and replace it with:

```python
    """Queue a purge for each tombstoned Document no open purge can clean.

    Two calls at the same moment leave one open purge per Document; both
    return its ID.
    """
```

**Action 3:** replace the body of `purge_document_chunks`, from the end of its docstring to the end of the function:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
    `Job.retry` can queue it again.
    """
    try:
        async with runtime.transaction() as connection:
            job = await _claim(connection, job_id=job_id, now=now)
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
            return await save_job(connection, job=job.succeed(finished_at=now))
    except InvalidJob:
        raise
    except Exception:
        # The failed transaction rolled back; record the failure separately,
        # unless another worker has finished the Job in the meantime.
        async with runtime.transaction() as connection:
            stored = await load_job(connection, job_id=job_id, for_update=True)
            if stored is None or stored.status is not JobStatus.QUEUED:
                raise
            failed = stored.start(started_at=now).fail(
                error_code=PURGE_FAILED, finished_at=now
            )
            return await save_job(connection, job=failed)
```

and replace it with:

```python
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
```

**Action 4:** let `_queue_purge` return the open purge. First its docstring:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
    connection: AsyncConnection, *, document_id: DocumentId, now: datetime
) -> Job:
    job = Job.request(
```

and replace it with:

```python
    connection: AsyncConnection, *, document_id: DocumentId, now: datetime
) -> Job:
    """The open purge of this Document: a new one, or the one already open."""
    job = Job.request(
```

Then its last lines:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
        queued_at=now,
    )
    await enqueue_job(connection, job)
    return job
```

and replace it with:

```python
        queued_at=now,
    )
    return (await enqueue_job(connection, job)).job
```

**Action 5:** in `_claim`, raise the new error:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
        raise InvalidJob(f"only a queued job can run, not a {job.status} one")
```

and replace it with:

```python
        raise JobAlreadyClaimed(job)
```

**Check:** `uv run pytest tests/test_deletions_integration.py -q -m integration` reports `9 passed`.

### Step 6 — Coding step: report the open Job to submitters, and the stored outcome to duplicates

This step passes the new answers up to the two callers that M08-T02 added.

**In plain words:** submitting work now says whether the Job already existed, and still sends a message for it if none was sent yet. On the worker side, a second message for a Job no longer gets "nothing"; it gets the Job as the first message left it. So every message for one Job reports the same status, attempt, and finish time.

| Piece | What changes |
|---|---|
| `Submission.duplicate` | true when an open Job with the same intent already existed; it defaults to false, so M08-T02's tests still match |
| `submit_job` | publishes the open Job's ID, whichever Job it is |
| `JobOutcome` | the Job as stored after a message, and `duplicate` |
| `run_stored_job` | returns `JobOutcome`; a Job that is not queued, or a `JobAlreadyClaimed` from the handler, is a duplicate |


**Action 1:** in the submitter, first the result type:

In `src/knowledge_service/job_dispatch.py`, find:

```python
class Submission:
    """A stored Job, and whether its message was sent yet."""

    job_id: JobId
    published: bool

```

and replace it with:

```python
class Submission:
    """The open Job for the submitted intent, and what this call did.

    `duplicate` is True when an open Job with the same kind and target
    already existed: that Job is returned and nothing new is stored.
    `published` is True only when this call sent its message.
    """

    job_id: JobId
    published: bool
    duplicate: bool = False

```

Then the docstring of `submit_job`:

In `src/knowledge_service/job_dispatch.py`, find:

```python
    An unpublished Job is sent by a later `publish_pending_jobs`.
    """
```

and replace it with:

```python
    An unpublished Job is sent by a later `publish_pending_jobs`. When an
    open Job with the same kind and target exists, it is returned instead,
    and is published only if no message names it yet.
    """
```

Then its body, which now publishes whichever Job is open:

In `src/knowledge_service/job_dispatch.py`, find:

```python
    async with runtime.transaction() as connection:
        await enqueue_job(connection, job)
    try:
        outcome = await publish_job(
            runtime, job_id=job.job_id, publisher=publisher, now=now
        )
```

and replace it with:

```python
    async with runtime.transaction() as connection:
        enqueued = await enqueue_job(connection, job)
    job_id = enqueued.job.job_id
    duplicate = not enqueued.created
    try:
        outcome = await publish_job(
            runtime, job_id=job_id, publisher=publisher, now=now
        )
```

And its two return statements:

In `src/knowledge_service/job_dispatch.py`, find:

```python
            job.job_id.value,
            type(error).__name__,
        )
        return Submission(job_id=job.job_id, published=False)
    return Submission(job_id=job.job_id, published=outcome is PublishOutcome.PUBLISHED)
```

and replace it with:

```python
            job_id.value,
            type(error).__name__,
        )
        return Submission(job_id=job_id, published=False, duplicate=duplicate)
    return Submission(
        job_id=job_id,
        published=outcome is PublishOutcome.PUBLISHED,
        duplicate=duplicate,
    )
```

**Action 2:** in the worker, first the import:

In `src/knowledge_service/worker/jobs.py`, find:

```python
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus

```

and replace it with:

```python
from knowledge_service.jobs import Job, JobAlreadyClaimed, JobKind, JobStatus

```

Then replace `run_stored_job` whole:

In `src/knowledge_service/worker/jobs.py`, find:

```python
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
```

and replace it with:

```python
@dataclass(frozen=True, slots=True)
class JobOutcome:
    """A Job as stored after a message for it, and whether it was a duplicate.

    For a duplicate, `job` is the outcome an earlier message produced (or is
    producing), so every message for one Job reports the same result.
    """

    job: Job
    duplicate: bool


async def run_stored_job(
    runtime: DatabaseRuntime,
    job_id: JobId,
    *,
    now: datetime,
    handlers: Mapping[JobKind, JobHandler] = HANDLERS,
) -> JobOutcome:
    """Run a stored queued Job once, or return what an earlier message did.

    A Job that is no longer queued was run, or is running, because of an
    earlier message for it, so this message changes nothing.
    """
    async with runtime.transaction() as connection:
        job = await load_job(connection, job_id=job_id)
    if job is None:
        raise InvalidJobMessage("the message names no stored job")
    if job.status is not JobStatus.QUEUED:
        return _duplicate(job)
    handler = handlers.get(job.kind)
    if handler is None:
        raise InvalidJobMessage(f"no handler runs {job.kind} jobs")
    try:
        ran = await handler(runtime, job_id=job_id, now=now)
    except JobAlreadyClaimed as claimed:
        # Another message claimed it first; the claim waited for its commit.
        return _duplicate(claimed.job)
    logger.info("job %s (%s) ran: %s", job_id.value, ran.kind, ran.status)
    return JobOutcome(job=ran, duplicate=False)


def _duplicate(job: Job) -> JobOutcome:
    logger.info("job %s is %s; duplicate message", job.job_id.value, job.status)
    return JobOutcome(job=job, duplicate=True)
```

**Check:** `uv run pyright src` reports 0 errors.

### Step 7 — Coding step: prove one Job per intent and one effect per Job

This step tests the new behavior against real PostgreSQL, where locks, the unique index, and the migration really run.

**In plain words:** the tests ask for the same work twice, and at the same moment, and check that one Job is stored. They check that other kinds or targets are other work, that finished Jobs do not block new work, and that a failed Job is queued again. They tombstone a Document twice, run three messages at once for a purge whose delete fails, and run the migration over duplicates that old code could leave.

| Test | What it proves |
|---|---|
| `test_asking_twice_...`, `test_another_kind_or_target_is_other_work` | same intent gives the same Job; anything else is new work |
| `test_a_job_that_can_do_no_more_work_...`, `test_asking_again_after_a_failed_attempt_...` | a finished Job allows new work; a failed one is queued again with its attempt count |
| `test_two_requests_at_once_store_one_job` | two transactions at once store one Job |
| `test_the_database_refuses_...`, `test_an_open_job_that_keeps_finishing_...`, `test_only_a_new_queued_job_...` | the index stops a plain insert; `JobIntentContended`; `InvalidJob` |
| `test_a_document_tombstoned_again_...` (two tests) | the open purge is reused, and after a failure it runs and purges |
| `test_two_requeues_at_once_...` | concurrent requeues leave one open purge |
| `test_submitting_...`, `test_a_duplicate_submission_...` | `Submission.duplicate`; one message; an unsent open Job is sent |
| `test_two_messages_at_once_for_a_failing_attempt_run_it_once` | one delete call, one stored failure, three equal reports |
| `test_the_migration_keeps_one_open_job_per_intent` | running over queued over failed; the rest become `superseded_duplicate` |

`test_two_requests_at_once_store_one_job` keeps each transaction open for a moment, so the two really overlap. `test_two_requeues_at_once_leave_one_open_purge` passes whether or not its two calls overlap, because it checks the union of what they return.


**Action 1:** the shared fixtures need a way to offer the test Document again. Split `tombstoned_with_purge` in two. First its start:

In `tests/job_fixtures.py`, find:

```python
async def tombstoned_with_purge(runtime: DatabaseRuntime) -> tuple[Document, JobId]:
    """A tombstoned Document that still has Chunks, and its stored purge Job."""
    normalized = normalize(
```

and replace it with:

```python
async def offer_retired(runtime: DatabaseRuntime, *, observed_at: datetime) -> Document:
    """Reconcile the one Document these tests use, as its Source offers it."""
    normalized = normalize(
```

Then its end:

In `tests/job_fixtures.py`, find:

```python
    result = await reconcile_document(
        runtime, normalized, readers=[Subject.public()], observed_at=OBSERVED_AT
    )
    [tombstone] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=OBSERVED_AT
    )
    return result.document, tombstone.purge_job_id
```

and replace it with:

```python
    result = await reconcile_document(
        runtime, normalized, readers=[Subject.public()], observed_at=observed_at
    )
    return result.document


async def tombstoned_with_purge(runtime: DatabaseRuntime) -> tuple[Document, JobId]:
    """A tombstoned Document that still has Chunks, and its stored purge Job."""
    document = await offer_retired(runtime, observed_at=OBSERVED_AT)
    [tombstone] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=OBSERVED_AT
    )
    return document, tombstone.purge_job_id
```

**Action 2:** two M08-T02 tests expected `None` for a duplicate. In `test_a_duplicate_message_runs_the_job_once`:

In `tests/test_job_dispatch_integration.py`, find:

```python
    assert first is not None
    assert first.status is JobStatus.SUCCEEDED
    assert second is None
```

and replace it with:

```python
    assert not first.duplicate
    assert first.job.status is JobStatus.SUCCEEDED
    # The duplicate reports the outcome the first message produced.
    assert second.duplicate
    assert second.job == first.job
```

And in `test_two_messages_at_once_run_the_job_once`, which now also checks both reports are the same stored Job:

In `tests/test_job_dispatch_integration.py`, find:

```python
    assert sum(result is not None for result in results) == 1
    assert (await stored_job(runtime, purge_id)).status is JobStatus.SUCCEEDED
```

and replace it with:

```python
    assert sorted(result.duplicate for result in results) == [False, True]
    stored = await stored_job(runtime, purge_id)
    assert stored.status is JobStatus.SUCCEEDED
    assert [result.job for result in results] == [stored, stored]
```

**Action 3:** create the idempotency tests.

Create `tests/test_job_idempotency_integration.py`:

```python
"""One open Job per intent, against real PostgreSQL.

A Job's intent is its kind and target: "purge the Chunks of Document D".
Asking for the same intent again, even at the same moment, returns the Job
that is already open instead of storing a second one.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from database_fixtures import migration_config, migration_environment
from job_fixtures import (
    OBSERVED_AT,
    SOURCE_ID,
    chunk_count,
    insert_source,
    offer_retired,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
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
    Enqueued,
    JobIntentContended,
    enqueue_job,
    save_job,
)
from knowledge_service.persistence.schema import jobs
from knowledge_service.settings import Settings
from knowledge_service.worker.jobs import run_stored_job

pytestmark = pytest.mark.integration

NOW = OBSERVED_AT + timedelta(minutes=5)
LATER = OBSERVED_AT + timedelta(minutes=10)
PURGE = JobKind.PURGE_DOCUMENT_CHUNKS
TARGET = uuid.UUID("5f1d2c3b-4a59-4e6f-8a7b-9c0d1e2f3a4b")


@dataclass
class RecordingPublisher:
    """Records every message it is asked to send; can refuse them all."""

    refuse: bool = False
    kinds: frozenset[JobKind] = frozenset({PURGE})
    sent: list[JobId] = field(default_factory=lambda: list[JobId]())

    def publish(self, job: Job) -> None:
        if self.refuse:
            raise PublishFailed("the queue is down")
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


def new_job(kind: JobKind = PURGE, target_id: uuid.UUID = TARGET) -> Job:
    return Job.request(
        job_id=JobId.new(), kind=kind, target_id=target_id, queued_at=OBSERVED_AT
    )


async def enqueue(runtime: DatabaseRuntime, job: Job) -> Enqueued:
    async with runtime.transaction() as connection:
        return await enqueue_job(connection, job)


async def broken_delete(connection: AsyncConnection, *, document_id: DocumentId) -> int:
    raise ConnectionError("the database went away")


async def stored_job_ids(runtime: DatabaseRuntime) -> list[JobId]:
    async with runtime.transaction() as connection:
        rows = await connection.scalars(sa.select(jobs.c.job_id))
        return [JobId(job_id) for job_id in rows]


async def test_asking_twice_for_the_same_work_leaves_one_job(
    runtime: DatabaseRuntime,
) -> None:
    first = await enqueue(runtime, new_job())
    second = await enqueue(runtime, new_job())

    assert first.created
    assert not second.created
    assert second.job == first.job
    assert await stored_job_ids(runtime) == [first.job.job_id]


@pytest.mark.parametrize(
    ("kind", "target_id"),
    [
        (JobKind.PURGE_CONVERSATION, TARGET),
        (PURGE, uuid.UUID("0c9b8a7d-6e5f-4a3b-8c1d-2e3f4a5b6c7d")),
    ],
)
async def test_another_kind_or_target_is_other_work(
    runtime: DatabaseRuntime, kind: JobKind, target_id: uuid.UUID
) -> None:
    await enqueue(runtime, new_job())

    other = await enqueue(runtime, new_job(kind, target_id))

    assert other.created
    assert len(await stored_job_ids(runtime)) == 2


async def test_a_job_that_can_do_no_more_work_does_not_block_new_work(
    runtime: DatabaseRuntime,
) -> None:
    done = (await enqueue(runtime, new_job())).job
    async with runtime.transaction() as connection:
        await save_job(
            connection, job=done.start(started_at=NOW).succeed(finished_at=NOW)
        )

    again = await enqueue(runtime, new_job())

    assert again.created
    assert again.job.job_id != done.job_id


async def test_two_requests_at_once_store_one_job(runtime: DatabaseRuntime) -> None:
    async def request_and_hold() -> Enqueued:
        async with runtime.transaction() as connection:
            enqueued = await enqueue_job(connection, new_job())
            # Keep the transaction open, so the other request runs meanwhile.
            await asyncio.sleep(0.2)
            return enqueued

    results = await asyncio.gather(request_and_hold(), request_and_hold())

    assert sorted(result.created for result in results) == [False, True]
    assert results[0].job.job_id == results[1].job.job_id
    assert len(await stored_job_ids(runtime)) == 1


async def test_the_database_refuses_a_second_open_job_for_one_intent(
    runtime: DatabaseRuntime,
) -> None:
    await enqueue(runtime, new_job())
    second = new_job()

    with pytest.raises(IntegrityError, match="uq_jobs_open_intent"):
        async with runtime.transaction() as connection:
            # A plain insert, as code that skipped `enqueue_job` would do.
            await connection.execute(
                sa.insert(jobs).values(
                    job_id=second.job_id.value,
                    kind=second.kind.value,
                    target_id=second.target_id,
                    status=second.status.value,
                    attempt=0,
                    max_attempts=second.max_attempts,
                    queued_at=second.queued_at,
                )
            )


async def test_an_open_job_that_keeps_finishing_is_a_typed_failure(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await enqueue(runtime, new_job())

    async def always_finished(*_args: object, **_kwargs: object) -> None:
        return None

    # Each round finds the conflict but never the open Job behind it.
    monkeypatch.setattr(job_store, "load_open_job", always_finished)
    with pytest.raises(JobIntentContended):
        await enqueue(runtime, new_job())

    assert await stored_job_ids(runtime) == [first.job.job_id]


async def test_a_document_tombstoned_again_reuses_its_open_purge(
    runtime: DatabaseRuntime,
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    # The Source offers the Document again, then stops offering it.
    await offer_retired(runtime, observed_at=NOW)
    [again] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )

    assert again.purge_job_id == purge_id
    assert await stored_job_ids(runtime) == [purge_id]
    assert await chunk_count(runtime, document.document_id) > 0


async def test_two_requeues_at_once_leave_one_open_purge(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    # The purge ended without deleting anything, so the Chunks still need one.
    finished = (await stored_job(runtime, purge_id)).start(started_at=NOW)
    async with runtime.transaction() as connection:
        await save_job(connection, job=finished.succeed(finished_at=NOW))

    results = await asyncio.gather(
        requeue_unfinished_purges(runtime, source_id=SOURCE_ID, now=LATER),
        requeue_unfinished_purges(runtime, source_id=SOURCE_ID, now=LATER),
    )

    requeued = {job_id for result in results for job_id in result}
    assert len(requeued) == 1
    [requeued_id] = requeued
    assert (await stored_job(runtime, requeued_id)).status is JobStatus.QUEUED
    assert len(await stored_job_ids(runtime)) == 2


async def test_submitting_the_same_work_twice_returns_the_open_job(
    runtime: DatabaseRuntime,
) -> None:
    publisher = RecordingPublisher()
    first = await submit_job(runtime, new_job(), publisher=publisher, now=NOW)

    second = await submit_job(runtime, new_job(), publisher=publisher, now=LATER)

    assert first == Submission(job_id=first.job_id, published=True)
    assert second == Submission(job_id=first.job_id, published=False, duplicate=True)
    assert publisher.sent == [first.job_id]


async def test_a_duplicate_submission_sends_an_open_job_no_message_names(
    runtime: DatabaseRuntime,
) -> None:
    first = await submit_job(
        runtime, new_job(), publisher=RecordingPublisher(refuse=True), now=NOW
    )
    publisher = RecordingPublisher()

    second = await submit_job(runtime, new_job(), publisher=publisher, now=LATER)

    assert not first.published
    assert second == Submission(job_id=first.job_id, published=True, duplicate=True)
    assert publisher.sent == [first.job_id]


async def test_only_a_new_queued_job_can_be_enqueued(runtime: DatabaseRuntime) -> None:
    finished = new_job().start(started_at=NOW).succeed(finished_at=NOW)

    with pytest.raises(InvalidJob, match="only a new queued job"):
        await enqueue(runtime, finished)

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


async def test_two_messages_at_once_for_a_failing_attempt_run_it_once(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    calls: list[DocumentId] = []

    async def slow_broken_delete(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        calls.append(document_id)
        # Long enough for the other messages to queue up behind the claim.
        await asyncio.sleep(0.2)
        raise ConnectionError("the database went away")

    monkeypatch.setattr(deletions, "delete_chunks", slow_broken_delete)
    results = await asyncio.gather(
        *(run_stored_job(runtime, purge_id, now=NOW) for _ in range(3))
    )

    stored = await stored_job(runtime, purge_id)
    assert len(calls) == 1
    assert sorted(result.duplicate for result in results) == [False, True, True]
    assert [result.job for result in results] == [stored] * 3
    assert (stored.status, stored.attempt, stored.last_error_code) == (
        JobStatus.FAILED,
        1,
        PURGE_FAILED,
    )


def test_the_migration_keeps_one_open_job_per_intent(migrated_database: str) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    with migration_environment(url):
        command.downgrade(config, "c2d3e4f5a6b7")
    insert_job = sa.text(
        "insert into jobs (job_id, kind, target_id, status, attempt, max_attempts,"
        " queued_at, started_at, finished_at, last_error_code) values (:job_id,"
        " 'purge_document_chunks', :target_id, :status, :attempt, 3, :queued_at,"
        " :started_at, :finished_at, :error)"
    )
    other_target = uuid.uuid4()
    failed_target = uuid.uuid4()
    rows = {
        "newer_queued": ("queued", LATER, TARGET),
        "running": ("running", LATER, TARGET),
        "older_queued": ("queued", NOW, TARGET),
        "alone": ("queued", NOW, other_target),
        "older_failed": ("failed", NOW, failed_target),
        "newer_queued_after_failure": ("queued", LATER, failed_target),
    }
    ids = {name: uuid.uuid4() for name in rows}
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            for name, (status, queued_at, target_id) in rows.items():
                failed = status == "failed"
                connection.execute(
                    insert_job,
                    {
                        "job_id": ids[name],
                        "target_id": target_id,
                        "status": status,
                        "attempt": 1 if failed else 0,
                        "queued_at": queued_at,
                        "started_at": None if status == "queued" else queued_at,
                        "finished_at": queued_at if failed else None,
                        "error": "chunk_purge_failed" if failed else None,
                    },
                )

        with migration_environment(url):
            command.upgrade(config, "head")

        with engine.connect() as connection:
            stored = {
                row.job_id: (row.status, row.attempt, row.last_error_code)
                for row in connection.execute(
                    sa.select(
                        jobs.c.job_id,
                        jobs.c.status,
                        jobs.c.attempt,
                        jobs.c.last_error_code,
                    )
                )
            }
    finally:
        engine.dispose()

    # A running Job is kept first; the queued duplicates are closed.
    assert stored[ids["running"]] == ("running", 0, None)
    assert stored[ids["older_queued"]] == ("failed", 3, "superseded_duplicate")
    assert stored[ids["newer_queued"]] == ("failed", 3, "superseded_duplicate")
    assert stored[ids["alone"]] == ("queued", 0, None)
    # A queued Job will run, so it is kept over an older failed one.
    assert stored[ids["newer_queued_after_failure"]] == ("queued", 0, None)
    assert stored[ids["older_failed"]] == ("failed", 3, "superseded_duplicate")
```

**Check:** `uv run pytest tests/test_job_idempotency_integration.py tests/test_job_dispatch_integration.py -q -m integration` reports `37 passed`.

### Step 8 — No coding in this step: break it on purpose

This step removes the line that lets the database settle duplicates, and shows which tests notice.

**Action 1:** in `enqueue_job` in `src/knowledge_service/persistence/jobs.py`, delete these lines:

```python
            .on_conflict_do_nothing(
                index_elements=[jobs.c.kind, jobs.c.target_id],
                index_where=sa.text(OPEN_JOB_PREDICATE),
            )
```

**Action 2:** run:

```bash
uv run pytest tests/test_jobs.py tests/test_job_idempotency_integration.py tests/test_job_dispatch_integration.py tests/test_deletions_integration.py -q -m "integration or not integration"
```

You see `8 failed, 44 passed`. Every failure is in `tests/test_job_idempotency_integration.py`: without the clause, a second request for an open intent hits the unique index and raises `IntegrityError` instead of getting the open Job.

**Action 3:** put the lines back and run the command again: `52 passed`.

**Check:** compare the count with your Step 1 prediction, and note it in the evidence file.

### Step 9 — No coding in this step: update the two documents

This step records the new rule where readers look for it: the architecture and the threat model.


**Action 1:** in the architecture, add a paragraph after the `job_dispatch` paragraph.

In `docs/architecture.md`, find:

```markdown
Only kinds a worker runs are submitted or sent. A Job can be named by two
messages; the worker runs it only while it is still queued.
```

and replace it with:

```markdown
Only kinds a worker runs are submitted or sent. A Job can be named by two
messages; the worker runs it only while it is still queued.

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
```

**Action 2:** extend the `jobs` row of the invariants table.

In `docs/architecture.md`, find:

```markdown
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code. |
```

and replace it with:

```markdown
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code; at most one open Job (queued, running, or failed with attempts left) per kind and target. |
```

**Action 3:** in the synchronization sequence diagram, change the tombstone line.

In `docs/architecture.md`, find:

```markdown
    Del->>DB: one transaction: tombstone, audit event, queue purge Job
```

and replace it with:

```markdown
    Del->>DB: one transaction: tombstone, audit event, queue purge Job or reuse the open one
```

**Action 4:** in the threat model, replace the `A replayed job writes twice` row.

In `docs/threat-model.md`, find:

```markdown
| A replayed job writes twice | Jobs are stored with typed errors and bounded retries | Job idempotency (M08-T03) |
```

and replace it with:

```markdown
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A failed Job with attempts left runs again only when the same work is requested again, until retries are owned (M08-T04); a message for an earlier attempt can start the next one (M08-T04) |
```

**Check:** `make docs-check` exits 0.

### Step 10 — No coding in this step: gates, evidence, and progress

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

**Action 2:** finish the evidence file: the gate results and the Step 8 count under "Verification", the review findings and known limits under "Review", the design under "Design" and "Decision", and your answers to the reflection questions below. Then mark the task completed in `docs/curriculum/PROGRESS.md` and commit with `m08-t03: enforce job idempotency`.

**Check:** every gate passes, and `git status` is clean after the commit.

## Reflection questions

- A Document is tombstoned, its purge fails once, the Source offers the Document again, and then stops offering it. What does the second tombstone get as its purge Job, and why is that better than storing a new purge?
- Suppose `enqueue_job` first selected the open Job and inserted only when it found none. Describe one sequence of two requests that would store two Jobs, and say what in the database stops that now.
- In M08-T02, a purge stored its failure in a second transaction. Walk through how three messages for the same failing purge could then delete twice, and say which line of the new code closes that gap.
- Why does a duplicate message now get the stored Job back instead of `None`? Who benefits from every delivery reporting the same result?
- The intent is only kind plus target. Think of a future Job where two requests with the same kind and target should be different work. What would you see in tests or production that tells you the key is too coarse?
- What observation would prove this design wrong? For example, what would you need to see in the `jobs` table to doubt that the index holds?

## Completion checklist

- [ ] The evidence file has the invariant, the owners, and your predictions, written before the change.
- [ ] `Job.is_open` and `JobAlreadyClaimed` exist; `tests/test_jobs.py` has 6 tests and passes.
- [ ] Migration `e5f6a7b8c9d0` closes duplicate open Jobs and adds `uq_jobs_open_intent`; the index and migration tests pass.
- [ ] `enqueue_job` returns `Enqueued`, stores at most one open Job per intent, queues a failed open Job again, and refuses anything but a new queued Job.
- [ ] `purge_document_chunks` claims, deletes in a savepoint, and stores its outcome in one transaction; `_claim` raises `JobAlreadyClaimed`.
- [ ] `submit_job` reports `duplicate`; `run_stored_job` returns `JobOutcome` with the stored Job for duplicates.
- [ ] `tests/test_job_idempotency_integration.py` (16), `tests/test_job_dispatch_integration.py` (21), and `tests/test_deletions_integration.py` (9) pass.
- [ ] You ran the deliberate break, saw 8 tests fail, and undid it.
- [ ] `docs/architecture.md` and `docs/threat-model.md` describe one open Job per intent and its limits.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
