"""Job values: durable metadata for one piece of queued work.

A Job records what should happen and how far it got. It carries no payload and
no raw error text, so a queue row stays small and safe to read.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from knowledge_service.identifiers import JobId

MAX_ERROR_CODE_LENGTH = 64


class InvalidJob(ValueError):
    """A Job value violates a domain invariant."""


class IllegalJobTransition(InvalidJob):
    """A caller asked a Job to move to a status it cannot reach from here."""


class JobNotRetryable(InvalidJob):
    """A failed Job may not be queued again, or not yet.

    It used every attempt, its failure was permanent, or its retry time has
    not come.
    """


class JobAlreadyClaimed(InvalidJob):
    """A Job is no longer queued, so this delivery must not run it again.

    `job` is the Job as stored when the claim was refused: the outcome an
    earlier delivery already produced, or is producing.
    """

    def __init__(self, job: Job, reason: str | None = None) -> None:
        super().__init__(reason or f"only a queued job can run, not a {job.status} one")
        self.job = job


class JobKind(StrEnum):
    """What a Job was queued to do."""

    SYNCHRONIZE_SOURCE = "synchronize_source"
    PURGE_CONVERSATION = "purge_conversation"
    PURGE_DOCUMENT_CHUNKS = "purge_document_chunks"


class JobStatus(StrEnum):
    """Where a Job is in its life."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_FINISHED = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED})

_ALLOWED: Mapping[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED}),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
    JobStatus.SUCCEEDED: frozenset(),
}


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidJob(f"{field_name} must be a UTC-aware timestamp")


def _check_transition(current: JobStatus, target: JobStatus) -> None:
    if target not in _ALLOWED[current]:
        raise IllegalJobTransition(f"a {current} job cannot become {target}")


@dataclass(frozen=True, slots=True)
class Job:
    """One piece of durable queued work."""

    job_id: JobId
    kind: JobKind
    target_id: UUID
    status: JobStatus
    attempt: int
    max_attempts: int
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error_code: str | None
    # When a failed Job may be queued again; None means it never will be.
    retry_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_utc(self.queued_at, "queued_at")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise InvalidJob("max attempts must be a positive integer")
        if type(self.attempt) is not int or not 0 <= self.attempt <= self.max_attempts:
            raise InvalidJob("attempt must be between zero and max attempts")
        if self.started_at is not None:
            _require_utc(self.started_at, "started_at")
            if self.started_at < self.queued_at:
                raise InvalidJob("started_at must not be earlier than queued_at")
        if self.finished_at is not None:
            _require_utc(self.finished_at, "finished_at")
            if self.started_at is not None and self.finished_at < self.started_at:
                raise InvalidJob("finished_at must not be earlier than started_at")
        if (self.status in _FINISHED) != (self.finished_at is not None):
            raise InvalidJob("only a finished job carries a finished time")
        if (self.status is JobStatus.QUEUED) == (self.started_at is not None):
            raise InvalidJob("only a job that is not queued carries a started time")
        if (self.status is JobStatus.FAILED) != (self.last_error_code is not None):
            raise InvalidJob("only a failed job carries an error code")
        if self.last_error_code is not None and not self.last_error_code.strip():
            raise InvalidJob("an error code must not be empty")
        if self.retry_at is not None:
            _require_utc(self.retry_at, "retry_at")
            if self.status is not JobStatus.FAILED:
                raise InvalidJob("only a failed job carries a retry time")
            if self.attempt >= self.max_attempts:
                raise InvalidJob("a job with no attempts left has no retry time")
            if self.finished_at is not None and self.retry_at < self.finished_at:
                raise InvalidJob("retry_at must not be earlier than finished_at")

    @property
    def is_open(self) -> bool:
        """Whether this Job can still do its work.

        A Job is open while it is queued or running, and while it has failed
        with a retry scheduled, because it will be queued again. At most one
        open Job exists per kind and target.
        """
        if self.status is JobStatus.FAILED:
            return self.retry_at is not None
        return self.status is not JobStatus.SUCCEEDED

    @classmethod
    def request(
        cls,
        *,
        job_id: JobId,
        kind: JobKind,
        target_id: UUID,
        queued_at: datetime,
        max_attempts: int = 3,
    ) -> Job:
        """Queue one piece of work."""
        return cls(
            job_id=job_id,
            kind=kind,
            target_id=target_id,
            status=JobStatus.QUEUED,
            attempt=0,
            max_attempts=max_attempts,
            queued_at=queued_at,
            started_at=None,
            finished_at=None,
            last_error_code=None,
        )

    def start(self, *, started_at: datetime) -> Job:
        """Claim the Job and begin one attempt."""
        _check_transition(self.status, JobStatus.RUNNING)
        return replace(self, status=JobStatus.RUNNING, started_at=started_at)

    def succeed(self, *, finished_at: datetime) -> Job:
        """Finish the Job successfully."""
        _check_transition(self.status, JobStatus.SUCCEEDED)
        return replace(self, status=JobStatus.SUCCEEDED, finished_at=finished_at)

    def fail(
        self,
        *,
        error_code: str,
        finished_at: datetime,
        retry_at: datetime | None = None,
    ) -> Job:
        """Finish the attempt unsuccessfully, recording a short error code.

        `retry_at` schedules the next attempt; None makes the failure final.
        """
        _check_transition(self.status, JobStatus.FAILED)
        if not error_code.strip():
            raise InvalidJob("an error code must not be empty")
        if len(error_code) > MAX_ERROR_CODE_LENGTH:
            raise InvalidJob(
                f"an error code must be at most {MAX_ERROR_CODE_LENGTH} characters"
            )
        return replace(
            self,
            status=JobStatus.FAILED,
            attempt=self.attempt + 1,
            finished_at=finished_at,
            last_error_code=error_code,
            retry_at=retry_at,
        )

    def retry(self, *, queued_at: datetime) -> Job:
        """Queue the Job again for one more attempt, once its retry time came."""
        _check_transition(self.status, JobStatus.QUEUED)
        if self.attempt >= self.max_attempts:
            raise JobNotRetryable(
                f"the job already used all {self.max_attempts} attempts"
            )
        if self.retry_at is None:
            raise JobNotRetryable("the job failed permanently")
        if queued_at < self.retry_at:
            raise JobNotRetryable("the job may not be retried before its retry time")
        return replace(
            self,
            status=JobStatus.QUEUED,
            queued_at=queued_at,
            started_at=None,
            finished_at=None,
            last_error_code=None,
            retry_at=None,
        )
