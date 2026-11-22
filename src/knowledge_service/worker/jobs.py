"""Messages that name stored Jobs, and the worker side that runs them.

A message carries only a Job's ID. What to do, and to which target, is read
from the Job's row in PostgreSQL, so a message never holds intent that the
database does not.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import dramatiq
import redis
from dramatiq.errors import BrokerError

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.ingestion.deletions import purge_document_chunks
from knowledge_service.job_dispatch import PublishFailed
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence.jobs import load_job
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import QueueName, WorkerConfigurationError

logger = logging.getLogger(__name__)

JOB_TIME_LIMIT_MS: Final = 60_000

# The queue each kind of Job travels on.
QUEUE_FOR_KIND: Final[Mapping[JobKind, QueueName]] = {
    JobKind.SYNCHRONIZE_SOURCE: QueueName.SYNCHRONIZATION,
    JobKind.PURGE_CONVERSATION: QueueName.MAINTENANCE,
    JobKind.PURGE_DOCUMENT_CHUNKS: QueueName.MAINTENANCE,
}

type JobHandler = Callable[..., Awaitable[Job]]

# The Jobs a worker can run today. Each handler claims a queued Job in its
# own transaction and refuses one that is no longer queued.
HANDLERS: Final[Mapping[JobKind, JobHandler]] = {
    JobKind.PURGE_DOCUMENT_CHUNKS: purge_document_chunks,
}


class InvalidJobMessage(ValueError):
    """A message does not name a stored Job a worker can run."""


def parse_job_id(value: object) -> JobId:
    """The Job ID a message carries, written as a canonical UUID string."""
    if type(value) is not str:
        raise InvalidJobMessage("a job message carries one job ID string")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise InvalidJobMessage("a job message carries a malformed job ID") from None
    if str(parsed) != value:
        raise InvalidJobMessage("a job message carries a malformed job ID")
    return JobId(parsed)


@dataclass(frozen=True, slots=True)
class JobActors:
    """The Job kinds a worker runs, and one actor per queue they travel on.

    `kinds` is exactly what the actors were declared for. Another kind that
    shares a queue is not included: no worker runs it.
    """

    kinds: frozenset[JobKind]
    by_queue: Mapping[QueueName, dramatiq.Actor[[str], None]]


def declare_job_actors(
    broker: dramatiq.Broker,
    *,
    run_job: Callable[[JobId], None],
    kinds: frozenset[JobKind],
) -> JobActors:
    """Declare a `run_<queue>_job` actor for each queue these kinds use."""

    def run(job_id: str) -> None:
        # The message came from Redis, so its argument is checked here.
        run_job(parse_job_id(job_id))

    by_queue: dict[QueueName, dramatiq.Actor[[str], None]] = {}
    for queue in sorted({QUEUE_FOR_KIND[kind] for kind in kinds}):
        declare = dramatiq.actor(
            broker=broker,
            actor_name=f"run_{queue}_job",
            queue_name=queue,
            time_limit=JOB_TIME_LIMIT_MS,
            max_retries=0,
        )
        by_queue[queue] = declare(run)
    return JobActors(kinds=kinds, by_queue=by_queue)


@dataclass(frozen=True, slots=True)
class DramatiqJobPublisher:
    """Publishes a Job as a message on its kind's queue."""

    actors: JobActors

    @property
    def kinds(self) -> frozenset[JobKind]:
        return self.actors.kinds

    def publish(self, job: Job) -> None:
        if job.kind not in self.actors.kinds:
            raise PublishFailed(f"no worker runs {job.kind} jobs")
        actor = self.actors.by_queue[QUEUE_FOR_KIND[job.kind]]
        try:
            actor.send(str(job.job_id.value))
        except (BrokerError, redis.RedisError, OSError) as error:
            raise PublishFailed("the queue did not accept the message") from error


async def run_stored_job(
    runtime: DatabaseRuntime,
    job_id: JobId,
    *,
    now: datetime,
    handlers: Mapping[JobKind, JobHandler] = HANDLERS,
) -> Job | None:
    """Run a stored queued Job once; None when the message was a duplicate.

    A Job that is no longer queued was run, or is running, because of an
    earlier message for it, so this message changes nothing.
    """
    async with runtime.transaction() as connection:
        job = await load_job(connection, job_id=job_id)
    if job is None:
        raise InvalidJobMessage("the message names no stored job")
    if job.status is not JobStatus.QUEUED:
        logger.info("job %s is %s; duplicate message ignored", job_id.value, job.status)
        return None
    handler = handlers.get(job.kind)
    if handler is None:
        raise InvalidJobMessage(f"no handler runs {job.kind} jobs")
    try:
        ran = await handler(runtime, job_id=job_id, now=now)
    except InvalidJob:
        # Another message for the same Job claimed it first.
        async with runtime.transaction() as connection:
            current = await load_job(connection, job_id=job_id)
        if current is not None and current.status is not JobStatus.QUEUED:
            logger.info("job %s was claimed by another message", job_id.value)
            return None
        raise
    logger.info("job %s (%s) ran: %s", job_id.value, ran.kind, ran.status)
    return ran


@dataclass(frozen=True, slots=True)
class JobRunner:
    """Runs one Job per message, with a database connection of its own."""

    settings: Settings

    def __post_init__(self) -> None:
        if self.settings.database_url is None:
            raise WorkerConfigurationError(
                "KNOWLEDGE_SERVICE_DATABASE_URL is required to run jobs"
            )

    def __call__(self, job_id: JobId) -> None:
        asyncio.run(self._run(job_id))

    async def _run(self, job_id: JobId) -> None:
        # An engine belongs to one event loop, and each message gets its own.
        runtime = create_database_runtime(self.settings)
        try:
            await run_stored_job(runtime, job_id, now=datetime.now(UTC))
        finally:
            await runtime.dispose()
