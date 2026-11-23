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
