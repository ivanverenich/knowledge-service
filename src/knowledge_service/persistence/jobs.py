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
