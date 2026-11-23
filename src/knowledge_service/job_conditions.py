"""What a Job's state and signs of life say about it, and what may be decided.

A running Job is alive while it is heard from within `stale_after`; one
silent longer is stale, its worker presumed dead. A queued Job whose message
was sent `orphan_after` ago and that no worker took up is orphaned. A Job
that failed with no retry planned is exhausted (its failure may pass, but
its attempts ran out) or failed permanently, and only an operator moves it
on, by a retry or a cancel. Nothing here reads the database or a clock.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from knowledge_service.job_failures import JobFailure
from knowledge_service.jobs import Job, JobStatus


class JobCondition(StrEnum):
    """What a Job's stored state and signs of life say about it."""

    WAITING = "waiting"
    ORPHANED = "orphaned"
    RUNNING = "running"
    STALE = "stale"
    RETRY_PLANNED = "retry_planned"
    EXHAUSTED = "exhausted"
    FAILED_PERMANENTLY = "failed_permanently"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"

    @property
    def needs_decision(self) -> bool:
        """Whether only an operator can move the Job on: retry or cancel."""
        return self in {JobCondition.EXHAUSTED, JobCondition.FAILED_PERMANENTLY}


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    """How long a Job may stay silent, or undelivered, before it is recovered.

    `stale_after` must be longer than the longest pause between two reports
    of a healthy worker, or a slow Job is taken for a dead one.
    """

    stale_after: timedelta = timedelta(minutes=5)
    orphan_after: timedelta = timedelta(minutes=30)

    def __post_init__(self) -> None:
        for name, value in (
            ("stale_after", self.stale_after),
            ("orphan_after", self.orphan_after),
        ):
            if type(value) is not timedelta:
                raise ValueError(f"{name} must be a timedelta")
            if not timedelta(minutes=1) <= value <= timedelta(days=7):
                raise ValueError(f"{name} must be from one minute to seven days")


DEFAULT_RECOVERY_POLICY: Final = RecoveryPolicy()


def condition_of(
    job: Job,
    *,
    now: datetime,
    policy: RecoveryPolicy = DEFAULT_RECOVERY_POLICY,
    published_at: datetime | None = None,
    last_heard_at: datetime | None = None,
) -> JobCondition:
    """Judge a Job at `now` by its status and its signs of life.

    `published_at` is when a queued Job's message was sent, if it was;
    `last_heard_at` is a running Job's latest heartbeat, and its start time
    when it never reported.
    """
    if job.status is JobStatus.QUEUED:
        if (
            published_at is not None
            and job.queued_at <= now
            and published_at <= now - policy.orphan_after
        ):
            return JobCondition.ORPHANED
        return JobCondition.WAITING
    if job.status is JobStatus.RUNNING:
        heard = last_heard_at or job.started_at
        if heard is not None and heard <= now - policy.stale_after:
            return JobCondition.STALE
        return JobCondition.RUNNING
    if job.status is JobStatus.FAILED:
        if job.retry_at is not None:
            return JobCondition.RETRY_PLANNED
        if _transient(job.last_error_code):
            # A failure that may pass, so it stopped only because attempts ran out.
            return JobCondition.EXHAUSTED
        return JobCondition.FAILED_PERMANENTLY
    if job.status is JobStatus.SUCCEEDED:
        return JobCondition.SUCCEEDED
    return JobCondition.CANCELLED


def _transient(error_code: str | None) -> bool:
    try:
        return JobFailure(error_code).transient
    except ValueError:
        return False


class JobDecision(StrEnum):
    """What an operator may decide about a Job."""

    RETRY = "retry"
    CANCEL = "cancel"


def allowed_decisions(job: Job) -> tuple[JobDecision, ...]:
    """The decisions that fit `job` in its current status, retry first.

    A retry fits a Job that failed with no retry planned; a cancel fits a
    queued or failed Job. A running or finished Job takes neither.
    """
    allowed: list[JobDecision] = []
    if job.status is JobStatus.FAILED and job.retry_at is None:
        allowed.append(JobDecision.RETRY)
    if job.status in {JobStatus.QUEUED, JobStatus.FAILED}:
        allowed.append(JobDecision.CANCEL)
    return tuple(allowed)
