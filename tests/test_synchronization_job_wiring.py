"""Unit tests for what wires synchronization Jobs into the worker.

Nothing here touches a database, Redis, or the network.
"""

import json
import os
from pathlib import Path

import dramatiq
import pytest
from dramatiq.brokers.stub import StubBroker
from pydantic import SecretStr

from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourceGone,
    SourceMisconfigured,
    SourcePage,
)
from knowledge_service.ingestion.synchronization_jobs import SynchronizationJobs
from knowledge_service.job_failures import JobFailure, classify_job_failure
from knowledge_service.jobs import JobKind
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import QueueName, WorkerConfigurationError
from knowledge_service.worker.handlers import job_handlers, load_principal_directory
from knowledge_service.worker.jobs import (
    RUNNABLE_KINDS,
    TIME_LIMIT_MS_FOR_QUEUE,
    declare_job_actors,
)


@pytest.fixture(autouse=True)
def isolate_settings_sources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No environment variable or `.env` file may change these settings."""
    for variable in tuple(os.environ):
        if variable.upper().startswith("KNOWLEDGE_SERVICE_"):
            monkeypatch.delenv(variable)
    monkeypatch.chdir(tmp_path)


def test_only_the_last_page_carries_a_cursor() -> None:
    assert SourcePage(records=(), next_checkpoint=None, cursor="c1").cursor == "c1"
    with pytest.raises(InvalidSource, match="only the last page"):
        SourcePage(records=(), next_checkpoint="2", cursor="c1")
    with pytest.raises(InvalidSource, match="must not be empty"):
        SourcePage(records=(), next_checkpoint=None, cursor=" ")


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (SourceGone("gone"), JobFailure.SOURCE_GONE),
        (SourceMisconfigured("bad"), JobFailure.SOURCE_MISCONFIGURED),
    ],
)
def test_a_source_that_cannot_be_synchronized_fails_for_good(
    error: Exception, failure: JobFailure
) -> None:
    assert classify_job_failure(error) is failure
    assert not failure.transient


def test_the_worker_runs_synchronizations_and_purges() -> None:
    handlers = job_handlers(Settings(redis_url=SecretStr("redis://127.0.0.1:1/0")))

    assert frozenset(handlers) == RUNNABLE_KINDS
    assert JobKind.SYNCHRONIZE_SOURCE in RUNNABLE_KINDS
    assert isinstance(handlers[JobKind.SYNCHRONIZE_SOURCE], SynchronizationJobs)


def test_the_worker_refuses_to_start_without_redis() -> None:
    with pytest.raises(WorkerConfigurationError, match="REDIS_URL"):
        job_handlers(Settings())


def test_a_synchronization_message_may_run_for_half_an_hour() -> None:
    broker = StubBroker()
    dramatiq.set_broker(broker)
    actors = declare_job_actors(
        broker, run_job=lambda _job_id: None, kinds=RUNNABLE_KINDS
    )

    synchronization = actors.by_queue[QueueName.SYNCHRONIZATION]
    assert synchronization.options["time_limit"] == 30 * 60_000
    assert TIME_LIMIT_MS_FOR_QUEUE[QueueName.MAINTENANCE] == 60_000


def test_a_principal_directory_file_is_read(tmp_path: Path) -> None:
    path = tmp_path / "directory.json"
    path.write_text(
        json.dumps(
            {
                "issuer": "http://localhost:8180/realms/knowledge-service",
                "users": {"carol@example.test": "0f5e7d2a"},
                "groups": {"engineering": "/engineering"},
            }
        )
    )

    directory = load_principal_directory(path)

    assert directory.groups == {"engineering": "/engineering"}


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", "{}", '{"issuer": "x", "groups": {"g": "no-slash"}}'],
    ids=["not-json", "not-an-object", "no-issuer", "invalid-group-path"],
)
def test_a_bad_principal_directory_file_stops_the_worker(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "directory.json"
    path.write_text(content)

    with pytest.raises(WorkerConfigurationError):
        load_principal_directory(path)


def test_a_missing_principal_directory_file_stops_the_worker(tmp_path: Path) -> None:
    settings = Settings(
        redis_url=SecretStr("redis://127.0.0.1:1/0"),
        principal_directory_file=tmp_path / "missing.json",
    )

    with pytest.raises(WorkerConfigurationError, match="cannot be read"):
        job_handlers(settings)
