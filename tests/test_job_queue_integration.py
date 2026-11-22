"""A stored Job through real Redis and a real Dramatiq worker, end to end."""

import os
import socket
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta

import dramatiq
import pytest
from dramatiq.brokers.redis import RedisBroker
from pydantic import SecretStr

from job_fixtures import (
    OBSERVED_AT,
    chunk_count,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.job_dispatch import RelayReport, publish_pending_jobs
from knowledge_service.jobs import JobStatus
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import QueueName, build_broker, redis_client
from knowledge_service.worker.jobs import (
    HANDLERS,
    DramatiqJobPublisher,
    JobActors,
    JobRunner,
    declare_job_actors,
)

pytestmark = pytest.mark.integration

REDIS_URL_ENV = "KNOWLEDGE_SERVICE_TEST_REDIS_URL"
NOW = OBSERVED_AT + timedelta(minutes=5)


@pytest.fixture
def redis_url() -> str:
    configured = os.environ.get(REDIS_URL_ENV)
    if not configured:
        pytest.skip(f"set {REDIS_URL_ENV} to a disposable Redis server")
    return configured


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    await insert_source(runtime)
    try:
        yield runtime
    finally:
        await runtime.dispose()


@pytest.fixture
def redis_broker(redis_url: str) -> Iterator[RedisBroker]:
    namespace = f"knowledge-service.test-{uuid.uuid4()}"
    broker = build_broker(redis_url, namespace=namespace)
    broker.emit_after("process_boot")
    yield broker
    broker.flush_all()
    broker.close()
    cleaner = redis_client(redis_url)
    try:
        cleaner.delete(f"{namespace}:__heartbeats__")
    finally:
        cleaner.close()


@pytest.fixture
def job_actors(redis_broker: RedisBroker, migrated_database: str) -> JobActors:
    runner = JobRunner(Settings(database_url=SecretStr(migrated_database)))
    return declare_job_actors(redis_broker, run_job=runner, kinds=frozenset(HANDLERS))


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def test_a_job_stored_while_redis_was_down_runs_once_after_the_relay(
    runtime: DatabaseRuntime,
    redis_broker: RedisBroker,
    job_actors: JobActors,
    redis_url: str,
) -> None:
    # A synchronization pass stored the purge Job; no message names it yet.
    document, purge_id = await tombstoned_with_purge(runtime)

    # The queue is unreachable: the relay fails and the Job keeps waiting.
    down = build_broker(f"redis://127.0.0.1:{closed_port()}/0")
    try:
        down_actors = declare_job_actors(
            down, run_job=lambda _job_id: None, kinds=frozenset(HANDLERS)
        )
        refused = await publish_pending_jobs(
            runtime, publisher=DramatiqJobPublisher(down_actors), now=NOW
        )
    finally:
        down.close()
    assert refused == RelayReport(published=0, skipped=0, failed=1, untried=0)
    assert (await stored_job(runtime, purge_id)).status is JobStatus.QUEUED

    # Redis is back: the relay publishes it, and a worker runs it.
    publisher = DramatiqJobPublisher(job_actors)
    relayed = await publish_pending_jobs(runtime, publisher=publisher, now=NOW)
    assert relayed == RelayReport(published=1, skipped=0, failed=0, untried=0)

    worker = dramatiq.Worker(redis_broker, worker_timeout=100, worker_threads=2)
    worker.start()
    try:
        redis_broker.join(QueueName.MAINTENANCE, timeout=20_000)
        finished = await stored_job(runtime, purge_id)
        assert finished.status is JobStatus.SUCCEEDED
        assert await chunk_count(runtime, document.document_id) == 0

        # A second message for the same Job, as after a crash before its send
        # was recorded, changes nothing.
        publisher.publish(finished)
        redis_broker.join(QueueName.MAINTENANCE, timeout=20_000)
        assert await stored_job(runtime, purge_id) == finished
    finally:
        worker.stop()

    # Neither message failed: nothing reached the dead-letter queue.
    inspector = redis_client(redis_url)
    try:
        dead = inspector.zcard(f"{redis_broker.namespace}:maintenance.XQ")
    finally:
        inspector.close()
    assert dead == 0
