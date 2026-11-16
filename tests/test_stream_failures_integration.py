"""Real database failures before the headers: an outage is a 503 with Retry-After."""

import logging
from collections.abc import AsyncIterator
from typing import cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from evidence_fakes import MARKER
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.settings import Settings
from test_streaming import (
    EVIDENCE,
    QUESTION,
    SIGNALS,
    Model,
    Source,
    client,
    streamer,
)

pytestmark = pytest.mark.integration

# Nothing listens on port 1, so the connection is refused at once.
UNREACHABLE = "postgresql://reader:secret@127.0.0.1:1/knowledge"


class DatabaseSource:
    """Find Evidence after one query, as a real source would."""

    def __init__(self, runtime: DatabaseRuntime, query: str) -> None:
        self.runtime = runtime
        self.query = query

    async def find(self, question: str, *, request_id: UUID) -> FoundEvidence:
        async with self.runtime.transaction() as connection:
            await connection.execute(sa.text(self.query), {"marker": MARKER})
        return FoundEvidence(EVIDENCE, SIGNALS)


@pytest.fixture
async def unreachable() -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(
            database_url=SecretStr(UNREACHABLE), database_connect_timeout_seconds=2
        )
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


@pytest.fixture
async def impatient(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(
            database_url=SecretStr(migrated_database),
            database_statement_timeout_ms=50,
        )
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def search_fails(
    runtime: DatabaseRuntime, query: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Search with one failing query, then check what the client and log got."""
    caplog.set_level(logging.DEBUG)
    model = Model()
    source = cast(Source, DatabaseSource(runtime, query))
    app = create_app(streamer=streamer(model, source=source))

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert response.headers["retry-after"] == "5"
    assert model.calls == 0
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    stream = ended.answer_stream  # type: ignore[attr-defined]
    assert stream["audit_outcome"] == "dependency_error"
    assert stream["failure"] == "OperationalError"
    exposed = [response.text, str(response.headers), caplog.text]
    assert not any(MARKER in text or "secret" in text for text in exposed)


async def test_an_unreachable_database_is_a_503_with_retry_after(
    unreachable: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    await search_fails(unreachable, "SELECT :marker", caplog)


async def test_a_database_too_slow_for_its_timeout_is_a_503_with_retry_after(
    impatient: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    await search_fails(impatient, "SELECT pg_sleep(1), :marker", caplog)
