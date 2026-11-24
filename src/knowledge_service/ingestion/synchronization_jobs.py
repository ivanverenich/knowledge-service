"""Run one stored synchronization Job: claim it, sync its Source, record how it ended.

This is the one place a `synchronize_source` Job becomes a pass. In order:

1. Claim the Job in a short transaction: it must still be queued; it moves
   to running and its attempt's progress row is stored. The claim commits
   at once, because a pass takes minutes and must not hold the Job's lock.
2. Read the Source the Job names. A Source that is gone or disabled, or
   whose configuration no adapter can open, fails the Job for good.
3. Run the pass under the Source's lease, observed at the attempt's start
   time, so every retry observes later and may claim the fence. After each
   committed page, report the attempt's totals: the report is also the
   attempt's sign of life for recovery (M08-T08).
4. Record the end in a new transaction, but only if the Job is still
   running this attempt. Recovery may have failed it meanwhile, and its
   decision stands: this delivery then reports the stored outcome.

A failure is classified as every Job's is (`job_failures`): a Source or
database that is unavailable, or a lost lease, is retried later from the
pass's checkpoint; anything else is final. A cancellation, or the actor's
time limit, ends the attempt without recording anything: the Job stays
running until recovery finds it silent, and its retry resumes the pass.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.ingestion.checkpoints import FULL_PASS_INTERVAL
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.exclusive_sync import synchronize_source_exclusively
from knowledge_service.ingestion.grant_normalization import PrincipalDirectory
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceConfiguration,
    SourceGone,
    SourceKind,
    SourceMisconfigured,
    SourcePort,
)
from knowledge_service.ingestion.synchronization import PAGE_SIZE, PassTotals
from knowledge_service.job_failures import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
    fail_attempt,
)
from knowledge_service.job_progress import (
    JobCounters,
    JobStage,
    JobWarning,
    attempt_number,
)
from knowledge_service.job_reporting import report_progress
from knowledge_service.jobs import (
    InvalidJob,
    Job,
    JobAlreadyClaimed,
    JobKind,
    JobStatus,
)
from knowledge_service.leases import LeaseStore
from knowledge_service.persistence.job_attempts import begin_attempt, finish_attempt
from knowledge_service.persistence.jobs import load_job, save_job
from knowledge_service.persistence.sources import SourceRow, load_source_row


@dataclass(frozen=True, slots=True)
class SourceSettings:
    """What a stored Source says about how to read it; never a secret."""

    source_id: SourceId
    configuration: SourceConfiguration
    # Who may read every item of a Source whose items carry no access of
    # their own, such as a local folder.
    default_access: SourceAccess


type SourceOpener = Callable[[SourceSettings], SourcePort]
type LeaseStoreFactory = Callable[[], AbstractAsyncContextManager[LeaseStore]]


def open_source(settings: SourceSettings) -> SourcePort:
    """The adapter that reads a stored Source; `SourceMisconfigured` if none can."""
    configuration = settings.configuration
    if configuration.kind is SourceKind.LOCAL_DIRECTORY:
        root = Path(configuration.location)
        if not root.is_absolute():
            raise SourceMisconfigured("a local directory must be an absolute path")
        # A folder that is missing now is unavailable, and retried, not wrong.
        return DirectorySource(root, access=settings.default_access)
    raise SourceMisconfigured(f"no adapter reads {configuration.kind} sources yet")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SynchronizationJobs:
    """The handler of `synchronize_source` Jobs, with what it needs to run one.

    `leases` makes a lease store for one run and closes it after, because a
    Redis client belongs to the event loop that made it.
    """

    open_source: SourceOpener
    leases: LeaseStoreFactory
    principal_directory: PrincipalDirectory | None
    full_pass_interval: timedelta = FULL_PASS_INTERVAL
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY
    page_size: int = PAGE_SIZE
    clock: Callable[[], datetime] = field(default=_now)

    async def __call__(
        self, runtime: DatabaseRuntime, *, job_id: JobId, now: datetime
    ) -> Job:
        """Run one queued synchronization Job and return it as stored after.

        Raises `JobAlreadyClaimed` when another delivery claimed it first,
        or when recovery ended this attempt before it could finish.
        """
        job = await _claim(runtime, job_id=job_id, now=now)
        attempt = attempt_number(job)
        observed_at = cast(datetime, job.started_at)
        totals = PassTotals(processed=0, changed=0, quarantined=0, unresolved=0)
        # A stored warning never goes away, even if a later report of the same
        # item resolves it, and a heartbeat never goes back, even if this
        # machine's clock steps back: either would be refused.
        warnings: set[JobWarning] = set()
        heartbeat = observed_at

        async def report(reached: PassTotals) -> None:
            nonlocal totals, heartbeat
            totals = reached
            warnings.update(_warnings(reached))
            heartbeat = max(self.clock(), heartbeat)
            await report_progress(
                runtime,
                job_id=job_id,
                attempt=attempt,
                stage=JobStage.WRITING,
                counters=_counters(reached),
                warnings=warnings,
                now=heartbeat,
            )

        try:
            source = self.open_source(
                await _load_settings(runtime, SourceId(job.target_id))
            )
            async with self.leases() as leases:
                await synchronize_source_exclusively(
                    runtime,
                    source,
                    leases=leases,
                    source_id=SourceId(job.target_id),
                    observed_at=observed_at,
                    principal_directory=self.principal_directory,
                    page_size=self.page_size,
                    on_page=report,
                    full_pass_interval=self.full_pass_interval,
                )
        except Exception as error:
            return await self._finish(
                runtime, job=job, attempt=attempt, totals=totals, error=error
            )
        return await self._finish(
            runtime, job=job, attempt=attempt, totals=totals, error=None
        )

    async def _finish(
        self,
        runtime: DatabaseRuntime,
        *,
        job: Job,
        attempt: int,
        totals: PassTotals,
        error: Exception | None,
    ) -> Job:
        """Store how the attempt ended, if the Job still runs this attempt."""
        finished_at = max(self.clock(), cast(datetime, job.started_at))
        async with runtime.transaction() as connection:
            stored = await load_job(connection, job_id=job.job_id, for_update=True)
            if stored is None:
                raise InvalidJob("the synchronization job vanished")
            if stored.status is not JobStatus.RUNNING or (
                attempt_number(stored) != attempt
            ):
                # Recovery ended this attempt while it ran; its outcome stands.
                raise JobAlreadyClaimed(
                    stored, "the attempt was ended before it could finish"
                )
            ended = (
                stored.succeed(finished_at=finished_at)
                if error is None
                else fail_attempt(
                    stored, error, now=finished_at, policy=self.retry_policy
                )
            )
            await finish_attempt(connection, job=ended, counters=_counters(totals))
            return await save_job(connection, job=ended)


async def _claim(runtime: DatabaseRuntime, *, job_id: JobId, now: datetime) -> Job:
    """Move a queued synchronization Job to running, and commit at once."""
    async with runtime.transaction() as connection:
        job = await load_job(connection, job_id=job_id, for_update=True)
        if job is None:
            raise InvalidJob("the synchronization job does not exist")
        if job.kind is not JobKind.SYNCHRONIZE_SOURCE:
            raise InvalidJob("the job is not a source synchronization")
        if job.status is not JobStatus.QUEUED:
            raise JobAlreadyClaimed(job)
        # A Job queued by a clock ahead of this one starts no earlier than
        # it was queued.
        started = job.start(started_at=max(now, job.queued_at))
        await save_job(connection, job=started)
        await begin_attempt(connection, job=started)
        return started


async def _load_settings(
    runtime: DatabaseRuntime, source_id: SourceId
) -> SourceSettings:
    async with runtime.transaction() as connection:
        row = await load_source_row(connection, source_id=source_id)
    if row is None or not row.enabled:
        # `jobs.target_id` has no foreign key: the Source may be long gone.
        raise SourceGone("the source is missing or disabled")
    return _settings(row)


def _settings(row: SourceRow) -> SourceSettings:
    principals = tuple(p for p in row.default_principals if p is not None)
    if len(principals) != len(row.default_principals):
        # The column allows a NULL element; no reader is named by one.
        raise SourceMisconfigured("a stored default principal is empty")
    try:
        kind = SourceKind(row.kind)
        return SourceSettings(
            source_id=row.source_id,
            configuration=SourceConfiguration(
                kind=kind,
                location=row.location,
                credentials_reference=row.credentials_reference,
            ),
            default_access=SourceAccess(
                public=row.default_public, principals=principals
            ),
        )
    except ValueError as error:
        raise SourceMisconfigured(
            "the stored source configuration is invalid"
        ) from error


def _counters(totals: PassTotals) -> JobCounters:
    return JobCounters(
        processed=totals.processed,
        changed=totals.changed,
        skipped=totals.quarantined,
    )


def _warnings(totals: PassTotals) -> frozenset[JobWarning]:
    warnings: set[JobWarning] = set()
    if totals.unresolved:
        warnings.add(JobWarning.UNRESOLVED_READERS)
    if totals.quarantined:
        warnings.add(JobWarning.QUARANTINED_ITEMS)
    return frozenset(warnings)
