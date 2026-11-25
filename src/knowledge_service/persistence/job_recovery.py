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
    connection: AsyncConnection,
    *,
    limit: int,
    after: tuple[datetime, JobId] | None = None,
) -> tuple[Job, ...]:
    """Jobs that failed with no retry planned, that no newer Job replaced.

    Newest failure first. A Job for the same work queued after it, such as
    the next scheduled synchronization, replaces it, so it is not listed.
    With `after`, the list starts after the Job that finished then with
    that ID, so pages never repeat or skip a Job, even one that finished at
    the same moment.
    """
    conditions = [
        jobs.c.status == JobStatus.FAILED.value,
        jobs.c.retry_at.is_(None),
        ~_newer_of_same_intent(),
    ]
    if after is not None:
        finished_at, job_id = after
        conditions.append(
            sa.tuple_(jobs.c.finished_at, jobs.c.job_id)
            < sa.tuple_(sa.literal(finished_at), sa.literal(job_id.value))
        )
    statement = (
        sa.select(jobs)
        .where(*conditions)
        .order_by(jobs.c.finished_at.desc(), jobs.c.job_id.desc())
        .limit(limit)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(job_from_record(dict(record)) for record in records)
