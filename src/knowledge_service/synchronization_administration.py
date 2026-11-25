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
