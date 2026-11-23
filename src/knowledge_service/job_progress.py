"""How far one attempt of a Job got, and how it ended.

Each attempt of a Job has its own progress: a stage, counters that only
grow, the warnings it met, a heartbeat, and, once it ends, a typed outcome.
It is stored in PostgreSQL, so it outlives the worker that wrote it, and a
later attempt never overwrites an earlier one. Nothing here knows about
queue messages: a handler reports progress, the database keeps it.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from knowledge_service.identifiers import JobId
from knowledge_service.job_failures import JobFailure
from knowledge_service.jobs import InvalidJob, Job, JobStatus

# The largest values a PostgreSQL bigint and integer hold.
MAX_COUNT: Final = 2**63 - 1
MAX_ATTEMPT: Final = 2**31 - 1


class ProgressWentBackwards(InvalidJob):
    """A report would make a counter, a warning, or the heartbeat go back."""


class ReportFromTheFuture(InvalidJob):
    """A report's time is far ahead of the database's clock."""


class AttemptFinished(InvalidJob):
    """The attempt already ended, so it takes no more progress."""


class JobStage(StrEnum):
    """What an attempt is doing now. Stages may repeat, page after page."""

    STARTING = "starting"
    READING = "reading"
    WRITING = "writing"
    CLEANING_UP = "cleaning_up"


class JobWarning(StrEnum):
    """Something an attempt met that did not stop it."""

    # A Source named readers the directory does not know.
    UNRESOLVED_READERS = "unresolved_readers"
    # An item could not be used and was set aside.
    QUARANTINED_ITEMS = "quarantined_items"


class AttemptOutcome(StrEnum):
    """How one attempt ended."""

    SUCCEEDED = "succeeded"
    SUCCEEDED_WITH_WARNINGS = "succeeded_with_warnings"
    # It failed, and the Job will be tried again at its retry time.
    RETRY_SCHEDULED = "retry_scheduled"
    # A newer run of the same work started; there is nothing to redo.
    SUPERSEDED = "superseded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class JobCounters:
    """Running totals of one attempt; each one only grows."""

    processed: int = 0
    changed: int = 0
    skipped: int = 0

    def __post_init__(self) -> None:
        for name in ("processed", "changed", "skipped"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= MAX_COUNT:
                raise InvalidJob(f"{name} must be an integer from 0 to {MAX_COUNT}")

    def merged(self, other: JobCounters) -> JobCounters:
        """The higher of each counter in the two."""
        return JobCounters(
            processed=max(self.processed, other.processed),
            changed=max(self.changed, other.changed),
            skipped=max(self.skipped, other.skipped),
        )

    def covers(self, earlier: JobCounters) -> bool:
        """Whether no counter is below its value in `earlier`."""
        return (
            self.processed >= earlier.processed
            and self.changed >= earlier.changed
            and self.skipped >= earlier.skipped
        )


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidJob(f"{field_name} must be a UTC-aware timestamp")


def _require_report(stage: object, counters: object) -> None:
    if type(stage) is not JobStage:
        raise InvalidJob("stage must be a JobStage")
    if type(counters) is not JobCounters:
        raise InvalidJob("counters must be JobCounters")


def attempt_number(job: Job) -> int:
    """The number, from 1, of the attempt a started or finished Job is on.

    `Job.attempt` counts failed attempts, so a failed Job's last attempt is
    that count, and a running or succeeded Job's is the next one.
    """
    if job.status in {JobStatus.QUEUED, JobStatus.CANCELLED}:
        raise InvalidJob(f"a {job.status} job has no attempt under way")
    if job.status is JobStatus.FAILED:
        return job.attempt
    return job.attempt + 1


def outcome_of(job: Job, warnings: Iterable[JobWarning] = ()) -> AttemptOutcome:
    """The outcome of the attempt a finished Job just ended."""
    if job.status is JobStatus.SUCCEEDED:
        if any(True for _ in warnings):
            return AttemptOutcome.SUCCEEDED_WITH_WARNINGS
        return AttemptOutcome.SUCCEEDED
    if job.status is not JobStatus.FAILED:
        raise InvalidJob("only a finished job has an outcome")
    if job.last_error_code == JobFailure.SUPERSEDED.value:
        return AttemptOutcome.SUPERSEDED
    if job.retry_at is not None:
        return AttemptOutcome.RETRY_SCHEDULED
    return AttemptOutcome.FAILED


@dataclass(frozen=True, slots=True)
class JobProgress:
    """Where one attempt of a Job is, or how it ended."""

    job_id: JobId
    attempt: int
    stage: JobStage
    counters: JobCounters
    warnings: frozenset[JobWarning]
    started_at: datetime
    heartbeat_at: datetime
    outcome: AttemptOutcome | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        if type(self.attempt) is not int or not 1 <= self.attempt <= MAX_ATTEMPT:
            raise InvalidJob(f"attempt must be an integer from 1 to {MAX_ATTEMPT}")
        _require_report(self.stage, self.counters)
        if type(self.warnings) is not frozenset or any(
            type(warning) is not JobWarning for warning in self.warnings
        ):
            raise InvalidJob("warnings must be a frozenset of JobWarning")
        if self.outcome is not None and type(self.outcome) is not AttemptOutcome:
            raise InvalidJob("outcome must be an AttemptOutcome")
        _require_utc(self.started_at, "started_at")
        _require_utc(self.heartbeat_at, "heartbeat_at")
        if self.heartbeat_at < self.started_at:
            raise InvalidJob("heartbeat_at must not be earlier than started_at")
        if (self.outcome is None) != (self.finished_at is None):
            raise InvalidJob("only a finished attempt carries an outcome")
        if self.finished_at is not None:
            _require_utc(self.finished_at, "finished_at")
            if self.finished_at < self.heartbeat_at:
                raise InvalidJob("finished_at must not be earlier than heartbeat_at")

    @classmethod
    def begin(cls, job: Job) -> JobProgress:
        """The progress of the attempt a Job has just started."""
        if job.status is not JobStatus.RUNNING or job.started_at is None:
            raise InvalidJob("only a running job begins an attempt")
        return cls(
            job_id=job.job_id,
            attempt=attempt_number(job),
            stage=JobStage.STARTING,
            counters=JobCounters(),
            warnings=frozenset(),
            started_at=job.started_at,
            heartbeat_at=job.started_at,
        )

    def advance(
        self,
        *,
        stage: JobStage,
        counters: JobCounters,
        warnings: Iterable[JobWarning] = (),
        at: datetime,
    ) -> JobProgress:
        """Record a newer report: counters, warnings, and time never go back.

        Reporting the same values again changes nothing, so a replayed report
        is harmless.
        """
        if self.outcome is not None:
            raise AttemptFinished("the attempt already ended")
        _require_report(stage, counters)
        _require_utc(at, "at")
        try:
            seen = frozenset(warnings)
        except TypeError:
            raise InvalidJob("warnings must be JobWarning values") from None
        if any(type(warning) is not JobWarning for warning in seen):
            raise InvalidJob("warnings must be JobWarning values")
        if not counters.covers(self.counters):
            raise ProgressWentBackwards("a counter would go down")
        if not seen >= self.warnings:
            raise ProgressWentBackwards("a warning would disappear")
        if at < self.heartbeat_at:
            raise ProgressWentBackwards("the heartbeat would go back in time")
        return replace(
            self, stage=stage, counters=counters, warnings=seen, heartbeat_at=at
        )

    def finish(self, *, outcome: AttemptOutcome, at: datetime) -> JobProgress:
        """End the attempt. Ending it again the same way changes nothing.

        Ending never fails on time: the attempt ends at `at`, its Job's own
        finish time, and a heartbeat later than that, from a reporter whose
        clock runs ahead, is pulled back to it, so the outcome is always
        recorded and agrees with the Job.
        """
        if type(outcome) is not AttemptOutcome:
            raise InvalidJob("outcome must be an AttemptOutcome")
        if self.outcome is not None:
            if self.outcome is outcome:
                return self
            raise AttemptFinished("the attempt already ended another way")
        _require_utc(at, "at")
        if at < self.started_at:
            raise InvalidJob("an attempt cannot end before it started")
        heartbeat = min(self.heartbeat_at, at)
        return replace(self, outcome=outcome, finished_at=at, heartbeat_at=heartbeat)
