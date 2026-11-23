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
