"""Tests for job messages: what they carry, how they are sent and refused."""

import json
import socket
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import dramatiq
import pytest
from dramatiq.brokers.stub import StubBroker

from knowledge_service.identifiers import JobId
from knowledge_service.job_dispatch import PublishFailed
from knowledge_service.jobs import Job, JobKind
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import (
    QueueName,
    WorkerConfigurationError,
    build_broker,
    declare_queues,
    queue_middleware,
)
from knowledge_service.worker.jobs import (
    DramatiqJobPublisher,
    InvalidJobMessage,
    JobActors,
    JobRunner,
    declare_job_actors,
    parse_job_id,
)

QUEUED_AT = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
JOB_ID = JobId(uuid.UUID("0b8c2d5e-4f6a-4b1c-9d3e-2a7f8e6c5b40"))


def purge_job(kind: JobKind = JobKind.PURGE_DOCUMENT_CHUNKS) -> Job:
    return Job.request(
        job_id=JOB_ID, kind=kind, target_id=uuid.uuid4(), queued_at=QUEUED_AT
    )


@pytest.fixture
def stub_broker() -> Iterator[StubBroker]:
    broker = StubBroker(middleware=queue_middleware())
    declare_queues(broker)
    broker.emit_after("process_boot")
    yield broker
    broker.close()


@pytest.fixture
def ran() -> list[JobId]:
    return []


@pytest.fixture
def job_actors(stub_broker: StubBroker, ran: list[JobId]) -> JobActors:
    return declare_job_actors(
        stub_broker,
        run_job=ran.append,
        kinds=frozenset({JobKind.PURGE_DOCUMENT_CHUNKS}),
    )


def run_worker_until_empty(broker: StubBroker) -> None:
    worker = dramatiq.Worker(broker, worker_timeout=100)
    worker.start()
    try:
        broker.join(QueueName.MAINTENANCE, timeout=10_000, fail_fast=True)
        worker.join()
    finally:
        worker.stop()


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_published_job_reaches_the_worker_as_its_id_only(
    stub_broker: StubBroker, job_actors: JobActors, ran: list[JobId]
) -> None:
    DramatiqJobPublisher(job_actors).publish(purge_job())

    [raw] = list(stub_broker.queues[QueueName.MAINTENANCE].queue)
    message = json.loads(raw)
    assert message["actor_name"] == "run_maintenance_job"
    assert message["args"] == [str(JOB_ID.value)]
    assert message["kwargs"] == {}

    run_worker_until_empty(stub_broker)

    assert ran == [JOB_ID]


def test_actors_exist_only_for_the_queues_of_runnable_kinds(
    stub_broker: StubBroker, job_actors: JobActors
) -> None:
    assert set(job_actors.by_queue) == {QueueName.MAINTENANCE}
    # Conversation purges share the queue, but no worker runs them.
    assert job_actors.kinds == {JobKind.PURGE_DOCUMENT_CHUNKS}
    assert stub_broker.get_declared_actors() == {"run_maintenance_job"}
    actor = job_actors.by_queue[QueueName.MAINTENANCE]
    assert actor.options == {"time_limit": 60_000, "max_retries": 0}


@pytest.mark.parametrize(
    "kind", [JobKind.SYNCHRONIZE_SOURCE, JobKind.PURGE_CONVERSATION]
)
def test_a_kind_no_worker_runs_is_refused_as_a_failed_publish(
    stub_broker: StubBroker, job_actors: JobActors, kind: JobKind
) -> None:
    publisher = DramatiqJobPublisher(job_actors)

    assert kind not in publisher.kinds
    with pytest.raises(PublishFailed):
        publisher.publish(purge_job(kind))
    assert stub_broker.queues[QueueName.MAINTENANCE].qsize() == 0


def test_an_unreachable_queue_is_a_failed_publish_within_its_timeouts() -> None:
    broker = build_broker(f"redis://127.0.0.1:{closed_port()}/0")
    try:
        actors = declare_job_actors(
            broker,
            run_job=lambda _job_id: None,
            kinds=frozenset({JobKind.PURGE_DOCUMENT_CHUNKS}),
        )
        started = time.monotonic()
        with pytest.raises(PublishFailed):
            DramatiqJobPublisher(actors).publish(purge_job())
        assert time.monotonic() - started < 5.0
    finally:
        broker.close()


@pytest.mark.parametrize(
    "argument",
    [
        123,
        None,
        "not-a-uuid",
        str(JOB_ID.value).upper(),
        "{" + str(JOB_ID.value) + "}",
        str(JOB_ID.value).replace("-", ""),
        f" {JOB_ID.value}",
        [str(JOB_ID.value)],
    ],
)
def test_the_worker_refuses_a_message_that_names_no_canonical_job_id(
    stub_broker: StubBroker,
    job_actors: JobActors,
    ran: list[JobId],
    argument: object,
) -> None:
    actor = job_actors.by_queue[QueueName.MAINTENANCE]
    actor.send_with_options(args=(argument,))  # pyright: ignore[reportUnknownMemberType]

    with pytest.raises(InvalidJobMessage):
        run_worker_until_empty(stub_broker)

    assert ran == []
    assert len(stub_broker.dead_letters) == 1


def test_parse_job_id_accepts_the_canonical_spelling() -> None:
    assert parse_job_id(str(JOB_ID.value)) == JOB_ID


def test_a_job_runner_requires_a_database_url() -> None:
    with pytest.raises(WorkerConfigurationError):
        JobRunner(Settings(database_url=None))
