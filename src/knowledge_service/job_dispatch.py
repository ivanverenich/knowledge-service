"""Store a Job before any message names it, then publish it.

Submitting a Job commits it to PostgreSQL first. Only then is a message sent,
under a lock on the Job's row, and the send is recorded in `published_at`.
If the queue refuses the message, or the process stops before the send is
recorded, the Job stays queued and unpublished, and `publish_pending_jobs`
sends it later. One Job can therefore be named by two messages; a worker runs
a Job only while it is still queued, so the second message changes nothing.

Redis is never the record: PostgreSQL says what was asked for (ADR 0011).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Protocol

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence.jobs import (
    enqueue_job,
    load_unpublished_job_ids,
    lock_unpublished_job,
    mark_job_published,
)

logger = logging.getLogger(__name__)

MAX_RELAY_BATCH: Final = 1_000


class PublishFailed(RuntimeError):
    """The queue did not accept the message; the Job stays unpublished."""


class UnsupportedJobKind(InvalidJob):
    """No worker runs Jobs of this kind, so none may be submitted."""


class JobPublisher(Protocol):
    """Hands one message naming a Job to the queue."""

    @property
    def kinds(self) -> frozenset[JobKind]:
        """The Job kinds a worker can run."""
        ...

    def publish(self, job: Job) -> None:
        """Send one message naming `job`, or raise `PublishFailed`."""
        ...


class PublishOutcome(StrEnum):
    """What one attempt to publish a Job did."""

    PUBLISHED = "published"
    # Already published, no longer queued, not a kind any worker runs, or
    # being published by someone else right now.
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Submission:
    """The open Job for the submitted intent, and what this call did.

    `duplicate` is True when an open Job with the same kind and target
    already existed: that Job is returned and nothing new is stored.
    `published` is True only when this call sent its message.
    """

    job_id: JobId
    published: bool
    duplicate: bool = False


@dataclass(frozen=True, slots=True)
class RelayReport:
    """What one pass over unpublished Jobs did."""

    published: int
    skipped: int
    failed: int
    # Found in this pass but not tried, because the queue had just failed.
    untried: int


def _require_utc(now: datetime) -> None:
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")


async def publish_job(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    publisher: JobPublisher,
    now: datetime,
) -> PublishOutcome:
    """Send one message for a queued, unpublished Job and record the send.

    The row stays locked while the message is sent, so no other publisher
    sends it at the same time. A send that fails leaves the Job unpublished.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        job = await lock_unpublished_job(connection, job_id=job_id)
        if job is None or job.kind not in publisher.kinds:
            return PublishOutcome.SKIPPED
        try:
            # The Redis client is synchronous and bounded by its own timeouts.
            await asyncio.to_thread(publisher.publish, job)
        except PublishFailed:
            return PublishOutcome.FAILED
        await mark_job_published(connection, job_id=job_id, published_at=now)
    return PublishOutcome.PUBLISHED


async def submit_job(
    runtime: DatabaseRuntime,
    job: Job,
    *,
    publisher: JobPublisher,
    now: datetime,
) -> Submission:
    """Store a new queued Job, then try once to publish it.

    Once this returns, the Job is stored, whether or not it was published.
    An unpublished Job is sent by a later `publish_pending_jobs`. When an
    open Job with the same kind and target exists, it is returned instead,
    and is published only if no message names it yet.
    """
    _require_utc(now)
    if job.status is not JobStatus.QUEUED or job.attempt != 0:
        raise InvalidJob("only a new queued job can be submitted")
    if job.kind not in publisher.kinds:
        raise UnsupportedJobKind(f"no worker runs {job.kind} jobs")
    async with runtime.transaction() as connection:
        enqueued = await enqueue_job(connection, job)
    job_id = enqueued.job.job_id
    duplicate = not enqueued.created
    try:
        outcome = await publish_job(
            runtime, job_id=job_id, publisher=publisher, now=now
        )
    except Exception as error:
        # The Job is committed; the relay will publish it. Only the type is
        # logged, because a message could quote a URL or a secret.
        logger.warning(
            "job %s stored but not published: %s",
            job_id.value,
            type(error).__name__,
        )
        return Submission(job_id=job_id, published=False, duplicate=duplicate)
    return Submission(
        job_id=job_id,
        published=outcome is PublishOutcome.PUBLISHED,
        duplicate=duplicate,
    )


async def publish_pending_jobs(
    runtime: DatabaseRuntime,
    *,
    publisher: JobPublisher,
    now: datetime,
    limit: int = 100,
) -> RelayReport:
    """Publish queued Jobs that no message names yet, oldest first.

    This repairs a Job stored by a process that stopped, or whose queue
    refused the message, before its send was recorded. The pass stops at the
    first failed send, so an unavailable queue is not hit once per Job.
    """
    _require_utc(now)
    if type(limit) is not int or not 1 <= limit <= MAX_RELAY_BATCH:
        raise ValueError(f"limit must be an integer from 1 to {MAX_RELAY_BATCH}")
    async with runtime.transaction() as connection:
        job_ids = await load_unpublished_job_ids(
            connection, kinds=publisher.kinds, limit=limit
        )
    counts = dict.fromkeys(PublishOutcome, 0)
    for index, job_id in enumerate(job_ids):
        outcome = await publish_job(
            runtime, job_id=job_id, publisher=publisher, now=now
        )
        counts[outcome] += 1
        if outcome is PublishOutcome.FAILED:
            return RelayReport(
                published=counts[PublishOutcome.PUBLISHED],
                skipped=counts[PublishOutcome.SKIPPED],
                failed=1,
                untried=len(job_ids) - index - 1,
            )
    return RelayReport(
        published=counts[PublishOutcome.PUBLISHED],
        skipped=counts[PublishOutcome.SKIPPED],
        failed=0,
        untried=0,
    )
