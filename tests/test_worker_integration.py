"""The worker against a real Redis: readiness and one consumed ping."""

import os
import threading
import uuid
from collections.abc import Iterator

import dramatiq
import pytest
from dramatiq.brokers.redis import RedisBroker

from knowledge_service.worker.actors import declare_actors, send_ping
from knowledge_service.worker.broker import build_broker, redis_client
from knowledge_service.worker.health import broker_ready

pytestmark = pytest.mark.integration

REDIS_URL_ENV = "KNOWLEDGE_SERVICE_TEST_REDIS_URL"


@pytest.fixture
def redis_url() -> str:
    configured = os.environ.get(REDIS_URL_ENV)
    if not configured:
        pytest.skip(f"set {REDIS_URL_ENV} to a disposable Redis server")
    return configured


@pytest.fixture
def redis_broker(redis_url: str) -> Iterator[RedisBroker]:
    # A namespace of its own, so the test never reads another run's messages.
    namespace = f"knowledge-service.test-{uuid.uuid4()}"
    broker = build_broker(redis_url, namespace=namespace)
    yield broker
    broker.flush_all()
    broker.close()
    # flush_all empties the queues but keeps the workers' heartbeat key.
    cleaner = redis_client(redis_url)
    try:
        cleaner.delete(f"{namespace}:__heartbeats__")
    finally:
        cleaner.close()


def test_broker_is_ready_when_redis_answers(redis_url: str) -> None:
    assert broker_ready(redis_url, timeout_seconds=2.0) is True


def test_worker_consumes_a_ping_through_redis(redis_broker: RedisBroker) -> None:
    received: list[str] = []
    arrived = threading.Event()

    def record(token: str) -> None:
        received.append(token)
        arrived.set()

    actors = declare_actors(redis_broker, record_ping=record)
    worker = dramatiq.Worker(redis_broker, worker_timeout=100)
    worker.start()
    try:
        send_ping(actors, "through-redis")
        assert arrived.wait(timeout=10)
    finally:
        worker.stop()

    assert received == ["through-redis"]
