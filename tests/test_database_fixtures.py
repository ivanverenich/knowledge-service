"""Tests for the PostgreSQL test-database helpers."""

import pytest
from sqlalchemy.engine import make_url

from database_fixtures import (
    InvalidWorkerName,
    worker_database_name,
    worker_database_url,
)

BASE_URL = "postgresql://test_user:secret@localhost:5433/knowledge_service_test"


def test_a_worker_gets_its_own_database() -> None:
    assert worker_database_name("knowledge_service_test", "gw3") == (
        "knowledge_service_test_gw3"
    )


def test_a_run_without_workers_uses_the_configured_database() -> None:
    assert worker_database_name("knowledge_service_test", None) == (
        "knowledge_service_test"
    )
    assert worker_database_name("knowledge_service_test", "master") == (
        "knowledge_service_test"
    )


def test_only_the_database_name_changes() -> None:
    base = make_url(BASE_URL)

    worker = worker_database_url(base, "gw0")

    assert worker.database == "knowledge_service_test_gw0"
    assert worker.host == base.host
    assert worker.port == base.port
    assert worker.username == base.username
    assert worker.password == base.password


def test_a_worker_name_that_is_not_a_plain_word_is_rejected() -> None:
    with pytest.raises(InvalidWorkerName, match="not usable"):
        worker_database_name("knowledge_service_test", "gw-0; drop database")
