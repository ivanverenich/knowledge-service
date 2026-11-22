"""Tests for the worker package: broker setup, the ping actor, and readiness."""

import importlib
import os
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import dramatiq
import pytest
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import (
    AgeLimit,
    Retries,
    ShutdownNotifications,
    TimeLimit,
    TimeLimitExceeded,
)

from knowledge_service.settings import Settings
from knowledge_service.worker import health
from knowledge_service.worker.actors import (
    PING_TIME_LIMIT_MS,
    Actors,
    InvalidPing,
    declare_actors,
    send_ping,
)
from knowledge_service.worker.broker import (
    NAMESPACE,
    ActorOwnsOptions,
    QueueName,
    WorkerConfigurationError,
    broker_from_settings,
    build_broker,
    declare_queues,
    queue_middleware,
)
from knowledge_service.worker.health import broker_ready


@pytest.fixture
def stub_broker() -> Iterator[StubBroker]:
    broker = StubBroker(middleware=queue_middleware())
    declare_queues(broker)
    # The dramatiq command sends this signal; it starts the time-limit timer.
    broker.emit_after("process_boot")
    yield broker
    broker.close()


@pytest.fixture
def received() -> list[str]:
    return []


@pytest.fixture
def actors(stub_broker: StubBroker, received: list[str]) -> Actors:
    return declare_actors(stub_broker, record_ping=received.append)


def run_worker_until_empty(broker: StubBroker) -> None:
    """Consume every queued message, raising the first actor failure."""
    worker = dramatiq.Worker(broker, worker_timeout=100)
    worker.start()
    try:
        broker.join(QueueName.MAINTENANCE, timeout=10_000, fail_fast=True)
        worker.join()
    finally:
        worker.stop()


def closed_port() -> int:
    """A local port with nothing listening on it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_worker_consumes_a_ping(
    stub_broker: StubBroker, actors: Actors, received: list[str]
) -> None:
    send_ping(actors, "first-ping_1")

    run_worker_until_empty(stub_broker)

    assert received == ["first-ping_1"]


def test_ping_runs_on_the_maintenance_queue_with_explicit_limits(
    actors: Actors,
) -> None:
    assert actors.ping.queue_name == QueueName.MAINTENANCE
    assert actors.ping.options["time_limit"] == PING_TIME_LIMIT_MS
    assert actors.ping.options["max_retries"] == 0


def test_the_broker_runs_only_the_chosen_middleware() -> None:
    kinds = [type(middleware) for middleware in queue_middleware()]

    assert kinds == [
        ActorOwnsOptions,
        AgeLimit,
        TimeLimit,
        ShutdownNotifications,
        Retries,
    ]


def test_build_broker_declares_queues_and_json_without_connecting() -> None:
    dramatiq.set_encoder(dramatiq.PickleEncoder())
    broker = build_broker(f"redis://127.0.0.1:{closed_port()}/0")
    try:
        assert broker.namespace == NAMESPACE
        assert set(QueueName) <= broker.get_declared_queues()
        assert type(dramatiq.get_encoder()) is dramatiq.JSONEncoder
    finally:
        broker.close()


@pytest.mark.parametrize(
    "token",
    ["", "two words", "x" * 65, "line\nbreak", "ping;DROP", "пінг"],
)
def test_send_ping_refuses_an_unsafe_token(
    stub_broker: StubBroker, actors: Actors, token: str
) -> None:
    with pytest.raises(InvalidPing):
        send_ping(actors, token)

    assert stub_broker.queues[QueueName.MAINTENANCE].qsize() == 0


def test_send_ping_accepts_the_longest_token(
    stub_broker: StubBroker, actors: Actors, received: list[str]
) -> None:
    send_ping(actors, "x" * 64)

    run_worker_until_empty(stub_broker)

    assert received == ["x" * 64]


@pytest.mark.parametrize("argument", ["two words", 123, None, ["list"]])
def test_worker_refuses_a_hostile_message_without_retrying(
    stub_broker: StubBroker, actors: Actors, received: list[str], argument: object
) -> None:
    # Another producer with Redis access skips send_ping's check.
    actors.ping.send_with_options(args=(argument,))  # pyright: ignore[reportUnknownMemberType]

    with pytest.raises(InvalidPing):
        run_worker_until_empty(stub_broker)

    assert received == []
    assert len(stub_broker.dead_letters) == 1


def test_a_ping_past_its_time_limit_is_stopped(stub_broker: StubBroker) -> None:
    finished: list[str] = []

    def slow_recorder(token: str) -> None:
        for _ in range(100):
            time.sleep(0.05)
        finished.append(token)

    slow = declare_actors(
        stub_broker, record_ping=slow_recorder, ping_time_limit_ms=200
    )
    send_ping(slow, "slow")

    with pytest.raises(TimeLimitExceeded):
        run_worker_until_empty(stub_broker)

    assert finished == []


def test_broker_from_settings_requires_a_redis_url() -> None:
    with pytest.raises(WorkerConfigurationError):
        broker_from_settings(Settings(redis_url=None))


def test_broker_is_not_ready_when_nothing_listens() -> None:
    url = f"redis://127.0.0.1:{closed_port()}/0"

    assert broker_ready(url, timeout_seconds=1.0) is False


@pytest.mark.parametrize("url", ["not-a-url", "http://127.0.0.1:1/0", ""])
def test_broker_is_not_ready_for_a_malformed_url(url: str) -> None:
    assert broker_ready(url, timeout_seconds=1.0) is False


def test_readiness_ends_within_its_timeout_when_redis_never_answers() -> None:
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        port = silent.getsockname()[1]
        accepted: list[socket.socket] = []

        def accept_and_stay_silent() -> None:
            connection, _ = silent.accept()
            accepted.append(connection)

        threading.Thread(target=accept_and_stay_silent, daemon=True).start()
        started = time.monotonic()

        ready = broker_ready(f"redis://127.0.0.1:{port}/0", timeout_seconds=0.3)

        elapsed = time.monotonic() - started
        for connection in accepted:
            connection.close()

    assert ready is False
    assert elapsed < 2.0


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for variable in tuple(os.environ):
        if variable.upper().startswith("KNOWLEDGE_SERVICE_"):
            monkeypatch.delenv(variable)
    monkeypatch.chdir(tmp_path)


@pytest.mark.usefixtures("clean_environment")
def test_health_command_exits_2_without_a_redis_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert health.main() == 2
    assert "not set" in capsys.readouterr().err


@pytest.mark.usefixtures("clean_environment")
def test_health_command_exits_1_without_printing_the_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"redis://:s3cret-password@127.0.0.1:{closed_port()}/0"
    monkeypatch.setenv("KNOWLEDGE_SERVICE_REDIS_URL", url)

    assert health.main() == 1

    output = capsys.readouterr()
    assert "s3cret-password" not in output.out + output.err
    assert "did not answer" in output.err


@pytest.mark.usefixtures("clean_environment")
def test_worker_entry_point_declares_ping_on_its_global_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "KNOWLEDGE_SERVICE_REDIS_URL", f"redis://127.0.0.1:{closed_port()}/0"
    )
    monkeypatch.setenv(
        "KNOWLEDGE_SERVICE_DATABASE_URL",
        f"postgresql+psycopg://user:pass@127.0.0.1:{closed_port()}/db",
    )
    monkeypatch.delitem(sys.modules, "knowledge_service.worker.main", raising=False)

    entry = importlib.import_module("knowledge_service.worker.main")
    try:
        assert dramatiq.get_broker() is entry.broker
        assert entry.broker.get_declared_actors() == {"ping", "run_maintenance_job"}
    finally:
        entry.broker.close()
        dramatiq.set_broker(StubBroker())


def test_a_message_cannot_lift_its_actors_time_limit(stub_broker: StubBroker) -> None:
    finished: list[str] = []

    def slow_recorder(token: str) -> None:
        for _ in range(100):
            time.sleep(0.05)
        finished.append(token)

    slow = declare_actors(
        stub_broker, record_ping=slow_recorder, ping_time_limit_ms=200
    )
    slow.ping.send_with_options(args=("slow",), time_limit=60_000_000)  # pyright: ignore[reportUnknownMemberType]

    with pytest.raises(TimeLimitExceeded):
        run_worker_until_empty(stub_broker)

    assert finished == []


def test_a_message_cannot_turn_retries_back_on(stub_broker: StubBroker) -> None:
    attempts: list[object] = []

    def count_attempts(token: str) -> None:
        attempts.append(token)
        raise RuntimeError("planted failure")

    failing = declare_actors(stub_broker, record_ping=count_attempts)
    failing.ping.send_with_options(  # pyright: ignore[reportUnknownMemberType]
        args=("again",), max_retries=5, min_backoff=1, max_backoff=1
    )

    with pytest.raises(RuntimeError):
        run_worker_until_empty(stub_broker)

    assert attempts == ["again"]
    assert len(stub_broker.dead_letters) == 1


@pytest.mark.parametrize(
    "options",
    [{"throws": ["x"]}, {"on_retry_exhausted": "ping"}, {"retry_when": "x"}],
)
def test_a_message_option_cannot_stop_the_next_ping(
    stub_broker: StubBroker,
    actors: Actors,
    received: list[str],
    options: dict[str, Any],
) -> None:
    actors.ping.send_with_options(args=("two words",), **options)  # pyright: ignore[reportUnknownMemberType]
    send_ping(actors, "after")
    worker = dramatiq.Worker(stub_broker, worker_timeout=100, worker_threads=1)
    worker.start()
    try:
        stub_broker.join(QueueName.MAINTENANCE, timeout=10_000, fail_fast=False)
        worker.join()
    finally:
        worker.stop()

    assert received == ["after"]
    assert len(stub_broker.dead_letters) == 1


def serve_once(reply: bytes, *, pause_seconds: float = 0.0) -> int:
    """A one-connection server that sends `reply`, one byte per pause."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()

    def answer() -> None:
        with server:
            connection, _ = server.accept()
            with connection:
                connection.recv(1024)
                for byte in reply:
                    try:
                        connection.sendall(bytes([byte]))
                    except OSError:
                        return
                    time.sleep(pause_seconds)
                time.sleep(1.0)

    threading.Thread(target=answer, daemon=True).start()
    return server.getsockname()[1]


def test_readiness_deadline_covers_a_reply_that_trickles_in() -> None:
    port = serve_once(b"$100\r\n" + b"x" * 100 + b"\r\n", pause_seconds=0.2)
    started = time.monotonic()

    ready = broker_ready(f"redis://127.0.0.1:{port}/0", timeout_seconds=0.5)

    assert ready is False
    assert time.monotonic() - started < 1.5


@pytest.mark.parametrize("reply", [b"+OK\r\n", b"+PANG\r\n", b"garbage\r\n"])
def test_readiness_is_false_for_a_reply_redis_py_cannot_read(reply: bytes) -> None:
    port = serve_once(reply)

    assert broker_ready(f"redis://127.0.0.1:{port}/0", timeout_seconds=1.0) is False


@pytest.mark.parametrize(
    "query", ["timeout=1", "socket_timeout=-1", "socket_timeout=nan", "driver_info=y"]
)
def test_readiness_is_false_for_hostile_url_options(query: str) -> None:
    url = f"redis://127.0.0.1:{closed_port()}/0?{query}"

    assert broker_ready(url, timeout_seconds=1.0) is False


def test_readiness_is_false_for_a_host_label_too_long_to_encode() -> None:
    url = f"redis://{'a' * 64}.invalid:6379/0"

    assert broker_ready(url, timeout_seconds=1.0) is False


@pytest.mark.usefixtures("clean_environment")
def test_health_command_exits_2_for_invalid_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KNOWLEDGE_SERVICE_REDIS_HEALTH_TIMEOUT_SECONDS", "0.001")

    assert health.main() == 2
    assert "invalid" in capsys.readouterr().err
