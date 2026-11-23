"""Tell a slow Job from a dead one, and recover the Jobs that stopped moving.

A running Job is alive while its worker keeps reporting: a report at least
every `stale_after` keeps it running however long it takes. Silence is
measured by the database's clock, from when it stored the attempt's last
report, so a worker's own clock never makes it look dead or alive. A Job
silent longer is stale: its worker is presumed dead, and its attempt is
failed with `worker_lost`. A queued Job whose message was sent more than
`orphan_after` ago and that no worker took up is orphaned: the message is
presumed lost, and the attempt it stood for is failed with `delivery_lost`.

Both failures are transient, so the stored Job plans a retry while attempts
are left (M08-T04). A Job that failed with none planned waits for an
operator to retry or cancel it (`job_decisions`). An attempt's progress row
is ended, never deleted, so what the dead attempt did stays visible.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId
from knowledge_service.job_conditions import (
    DEFAULT_RECOVERY_POLICY,
    JobCondition,
    RecoveryPolicy,
    condition_of,
)
from knowledge_service.job_failures import (
    DEFAULT_RETRY_POLICY,
    JobFailure,
    RetryPolicy,
    fail_with,
)
from knowledge_service.persistence.job_attempts import finish_attempt
from knowledge_service.persistence.job_recovery import (
    load_silent_job_ids,
    load_undelivered_job_ids,
    lock_recovery_candidate,
)
from knowledge_service.persistence.jobs import save_job

logger = logging.getLogger(__name__)

MAX_RECOVERY_BATCH: Final = 1_000


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """The Jobs one recovery pass failed, by why, and those it could not."""

    stale: tuple[JobId, ...]
    orphaned: tuple[JobId, ...]
    errors: tuple[JobId, ...]


async def recover_stuck_jobs(
    runtime: DatabaseRuntime,
    *,
    now: datetime,
    policy: RecoveryPolicy = DEFAULT_RECOVERY_POLICY,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    limit: int = 100,
) -> RecoveryReport:
    """Fail the attempts of up to `limit` stale and `limit` orphaned Jobs.

    Each Job is judged again under its own lock, in its own transaction, so
    a report or a claim that arrived meanwhile keeps it alive, and one Job
    that cannot be recovered does not hold up the others.
    """
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")
    if type(limit) is not int or not 1 <= limit <= MAX_RECOVERY_BATCH:
        raise ValueError(f"limit must be an integer from 1 to {MAX_RECOVERY_BATCH}")
    async with runtime.transaction() as connection:
        silent = await load_silent_job_ids(
            connection, stale_after=policy.stale_after, limit=limit
        )
        undelivered = await load_undelivered_job_ids(
            connection, now=now, orphan_after=policy.orphan_after, limit=limit
        )
    recovered: dict[JobCondition, list[JobId]] = {
        JobCondition.STALE: [],
        JobCondition.ORPHANED: [],
    }
    errors: list[JobId] = []
    for job_id in (*silent, *undelivered):
        try:
            condition = await _recover(
                runtime,
                job_id=job_id,
                now=now,
                policy=policy,
                retry_policy=retry_policy,
            )
        except Exception as error:
            logger.warning(
                "job %s could not be recovered: %s", job_id.value, type(error).__name__
            )
            errors.append(job_id)
            continue
        if condition is not None:
            recovered[condition].append(job_id)
    return RecoveryReport(
        stale=tuple(recovered[JobCondition.STALE]),
        orphaned=tuple(recovered[JobCondition.ORPHANED]),
        errors=tuple(errors),
    )


async def _recover(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    now: datetime,
    policy: RecoveryPolicy,
    retry_policy: RetryPolicy,
) -> JobCondition | None:
    async with runtime.transaction() as connection:
        candidate = await lock_recovery_candidate(connection, job_id=job_id)
        if candidate is None:
            return None
        job = candidate.job
        # The silence was measured by the database's clock; placed before
        # `now`, it is judged the same whatever clock the worker had.
        silent_for = candidate.silent_for
        condition = condition_of(
            job,
            now=now,
            policy=policy,
            published_at=candidate.published_at,
            last_heard_at=None if silent_for is None else now - silent_for,
        )
        # Never end an attempt before anything the Job already recorded.
        at = max(now, job.queued_at, job.started_at or now)
        if condition is JobCondition.STALE:
            failed = fail_with(job, JobFailure.WORKER_LOST, now=at, policy=retry_policy)
        elif condition is JobCondition.ORPHANED:
            # The lost message stood for an attempt: count it, so a message
            # that keeps getting lost ends in a decision, not a loop.
            failed = fail_with(
                job.start(started_at=at),
                JobFailure.DELIVERY_LOST,
                now=at,
                policy=retry_policy,
            )
        else:
            return None
        await finish_attempt(connection, job=failed)
        await save_job(connection, job=failed)
    logger.info("job %s recovered: %s", job_id.value, condition)
    return condition
