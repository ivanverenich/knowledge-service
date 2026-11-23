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
