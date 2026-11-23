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
