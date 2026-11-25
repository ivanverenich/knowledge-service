"""An operator's decisions about Jobs recovery cannot move on by itself.

A Job that failed with no retry planned, because its attempts ran out or its
failure was permanent, stays failed until an operator retries or cancels it.
Each decision locks the Job, changes it through the domain, and records who
decided in the audit log, all in one transaction.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import exc as database_errors
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import AuditEventId, JobId, SourceId, UserId
from knowledge_service.jobs import InvalidJob, Job, JobKind
from knowledge_service.pagination import InvalidPage, decode_cursor, encode_cursor
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.job_recovery import load_failed_for_good
from knowledge_service.persistence.jobs import load_job, save_job
from knowledge_service.persistence.sources import lock_enabled_sources
from knowledge_service.source_administration import SourceDisabled

MAX_DECISION_LIST: Final = 500
_DECISION_LIST = "job-decisions"
_DECISION_CURSOR_KEYS = frozenset({"finished_at", "job_id"})


class JobNotFound(LookupError):
    """No Job has this ID."""


class JobIntentAlreadyOpen(InvalidJob):
    """Another Job for the same work is open, so this one cannot be queued again."""


async def load_jobs_needing_decision(
    runtime: DatabaseRuntime,
    *,
    limit: int = 100,
    after: tuple[datetime, JobId] | None = None,
) -> tuple[Job, ...]:
    """Jobs that failed for good and that no newer Job replaced, newest first.

    With `after`, from a `decision_position`, the list starts after that Job.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_DECISION_LIST:
        raise ValueError(f"limit must be an integer from 1 to {MAX_DECISION_LIST}")
    async with runtime.transaction() as connection:
        return await load_failed_for_good(connection, limit=limit, after=after)


def decision_cursor(job: Job) -> str:
    """The cursor of the page that starts after `job` in the decision list."""
    if job.finished_at is None:
        raise ValueError("only a finished Job ends a page of decisions")
    return encode_cursor(
        _DECISION_LIST,
        {"finished_at": job.finished_at.isoformat(), "job_id": str(job.job_id.value)},
    )


def decision_position(cursor: str) -> tuple[datetime, JobId]:
    """Where a `decision_cursor` says the next page starts.

    Only the exact text `decision_cursor` writes is accepted, so an offset
    other than UTC or another spelling of the same ID raises `InvalidPage`.
    """
    position = decode_cursor(cursor, listing=_DECISION_LIST, keys=_DECISION_CURSOR_KEYS)
    finished_text, job_text = position["finished_at"], position["job_id"]
    try:
        finished_at = datetime.fromisoformat(finished_text)
        job_id = UUID(job_text)
    except ValueError:
        raise InvalidPage("the cursor is not one this API gave") from None
    if (
        finished_at.utcoffset() != timedelta(0)
        or finished_at.isoformat() != finished_text
        or str(job_id) != job_text
    ):
        raise InvalidPage("the cursor is not one this API gave")
    return finished_at, JobId(job_id)


async def retry_job(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> Job:
    """Queue a Job that failed for good once more; the relay publishes it.

    A Job with a retry planned, or one that did not fail, is refused by the
    domain. While another Job for the same work is open, the retry is
    refused with `JobIntentAlreadyOpen`, so one piece of work never has two
    open Jobs. A synchronization Job whose Source is missing or disabled is
    refused with `SourceDisabled`, after the Job's own status is checked.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        job = await _lock(connection, job_id=job_id)
        # The Job's status first: a retry that never fits says so, whatever
        # state its Source is in.
        reopened = job.reopen(queued_at=now)
        if job.kind is JobKind.SYNCHRONIZE_SOURCE:
            # Share-locked, as a start does, so a disable waits for this retry.
            source_id = SourceId(job.target_id)
            enabled = await lock_enabled_sources(connection, source_ids={source_id})
            if source_id not in enabled:
                raise SourceDisabled("the source is missing or disabled")
        try:
            # A savepoint, so the refused update leaves the transaction usable.
            async with connection.begin_nested():
                await save_job(connection, job=reopened)
        except database_errors.IntegrityError as error:
            if _constraint(error) == "uq_jobs_open_intent":
                raise JobIntentAlreadyOpen(
                    "another job for the same work is open"
                ) from None
            raise
        await record_audit_event(
            connection,
            _decision_event(
                job,
                action=AuditAction.JOB_RETRIED,
                operator=operator,
                at=now,
                request_id=request_id,
            ),
        )
    return reopened


async def cancel_job(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> Job:
    """Close a queued or failed Job so nothing runs it again.

    A running Job is refused with `JobStillRunning`: its worker may still be
    writing. A message already sent for a queued Job finds it cancelled and
    does nothing.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        job = await _lock(connection, job_id=job_id)
        cancelled = job.cancel(finished_at=_not_before(job, now))
        await save_job(connection, job=cancelled)
        await record_audit_event(
            connection,
            _decision_event(
                job,
                action=AuditAction.JOB_CANCELLED,
                operator=operator,
                at=now,
                request_id=request_id,
            ),
        )
    return cancelled


async def _lock(connection: AsyncConnection, *, job_id: JobId) -> Job:
    job = await load_job(connection, job_id=job_id, for_update=True)
    if job is None:
        raise JobNotFound("no job has this ID")
    return job


def _not_before(job: Job, now: datetime) -> datetime:
    # A worker's clock may run ahead of this one; a Job never ends before
    # anything it already recorded.
    recorded = [job.queued_at, job.started_at, job.finished_at]
    return max([now, *(moment for moment in recorded if moment is not None)])


def _constraint(error: database_errors.IntegrityError) -> str | None:
    diagnostics = getattr(error.orig, "diag", None)
    name = getattr(diagnostics, "constraint_name", None)
    return name if type(name) is str else None


def _require_utc(now: datetime) -> None:
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")


def _decision_event(
    job: Job,
    *,
    action: AuditAction,
    operator: UserId,
    at: datetime,
    request_id: UUID | None,
) -> AuditEvent:
    """Who decided what about which Job, and the state it was in; no content."""
    metadata: dict[str, str | int | bool] = {
        "kind": job.kind.value,
        "from_status": job.status.value,
        "failed_attempts": job.attempt,
    }
    if job.last_error_code is not None:
        metadata["error_code"] = job.last_error_code
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=at,
        actor_id=operator,
        action=action,
        target_kind=AuditTargetKind.JOB,
        target_id=job.job_id.value,
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=request_id,
        run_id=None,
        metadata=metadata,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DatabaseJobDecisions:
    """The operator's Job decisions, stored in PostgreSQL, at the clock's time."""

    runtime: DatabaseRuntime
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def needing_decision(
        self, *, limit: int, after: tuple[datetime, JobId] | None = None
    ) -> tuple[Job, ...]:
        return await load_jobs_needing_decision(self.runtime, limit=limit, after=after)

    async def retry(self, *, job_id: JobId, operator: UserId, request_id: UUID) -> Job:
        return await retry_job(
            self.runtime,
            job_id=job_id,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )

    async def cancel(self, *, job_id: JobId, operator: UserId, request_id: UUID) -> Job:
        return await cancel_job(
            self.runtime,
            job_id=job_id,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )
