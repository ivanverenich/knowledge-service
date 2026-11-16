"""Stream failures: one policy before and after the headers, injected everywhere."""

import asyncio
import json
import logging
from typing import Any, cast
from uuid import UUID

import psycopg.errors as pg
import pytest
from sqlalchemy import exc as database_errors
from starlette.types import Message

from evidence_fakes import MARKER
from knowledge_service.answering.events import (
    AnswerEventReader,
    AnswerEventWriter,
    InvalidEventStream,
    StreamOutcome,
)
from knowledge_service.answering.stream_failures import (
    RETRY_AFTER_SECONDS,
    AuditOutcome,
    PartialText,
    StreamPhase,
    stream_failure,
)
from knowledge_service.answering.streaming import FinishedAnswer
from knowledge_service.app import create_app
from knowledge_service.contracts import Question
from knowledge_service.embeddings.port import (
    EmbeddingInputRejected,
    EmbeddingRateLimited,
    RejectionReason,
)
from knowledge_service.errors import (
    AuthorizationDenied,
    FailureKind,
    RetryOwner,
    classify_failure,
)
from knowledge_service.model import ChatModelRateLimited, ChatModelUnavailable
from knowledge_service.workflow import AnswerWorkflow
from test_streaming import (
    ANSWER,
    CITED,
    QUESTION,
    REQUEST_ID,
    Connection,
    Model,
    Source,
    chunks,
    client,
    names,
    rebuilt,
    scope,
    streamer,
)

BEFORE = StreamPhase.BEFORE_HEADERS
AFTER = StreamPhase.AFTER_HEADERS


def database_down() -> database_errors.OperationalError:
    """What the driver raises when PostgreSQL cannot be reached."""
    return database_errors.OperationalError(
        f"SELECT '{MARKER}'", None, ConnectionRefusedError(MARKER)
    )


class Raising:
    """A finisher that fails the way `error` says, after the model replied."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def finish(
        self, result: Any, evidence: Any, *, request_id: UUID
    ) -> FinishedAnswer:
        raise self.error


def ended(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    (record,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    return record.answer_stream  # type: ignore[attr-defined, no-any-return]


def error_data(sent: list[bytes]) -> dict[str, Any]:
    data = sent[-1].split(b"\n")[2].removeprefix(b"data: ")
    return json.loads(data)


def leaks(caplog: pytest.LogCaptureFixture, *texts: bytes) -> bool:
    records = any(
        MARKER in r.getMessage() or MARKER in repr(r.__dict__) for r in caplog.records
    )
    return records or any(MARKER.encode() in text for text in texts)


# --- The policy -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "status", "code", "retry", "retry_after", "outcome"),
    [
        (
            FailureKind.VALIDATION,
            422,
            "validation_error",
            RetryOwner.CALLER,
            None,
            AuditOutcome.REJECTED,
        ),
        (
            FailureKind.AUTHORIZATION,
            403,
            "authorization_denied",
            RetryOwner.NONE,
            None,
            AuditOutcome.DENIED,
        ),
        (
            FailureKind.DEPENDENCY,
            503,
            "dependency_unavailable",
            RetryOwner.SERVICE,
            RETRY_AFTER_SECONDS,
            AuditOutcome.DEPENDENCY_ERROR,
        ),
        (
            FailureKind.CAPACITY,
            429,
            "capacity_exceeded",
            RetryOwner.CALLER,
            RETRY_AFTER_SECONDS,
            AuditOutcome.THROTTLED,
        ),
        (
            FailureKind.INTERNAL,
            500,
            "internal_error",
            RetryOwner.NONE,
            None,
            AuditOutcome.INTERNAL_ERROR,
        ),
        (
            FailureKind.CANCELLATION,
            None,
            None,
            RetryOwner.NONE,
            None,
            AuditOutcome.CANCELLED,
        ),
    ],
)
def test_before_the_headers_a_failure_is_a_plain_http_error(
    kind: FailureKind,
    status: int | None,
    code: str | None,
    retry: RetryOwner,
    retry_after: int | None,
    outcome: AuditOutcome,
) -> None:
    failure = stream_failure(kind, BEFORE)

    assert failure.status_code == status
    assert failure.public_code == code
    assert failure.retry is retry
    assert failure.retry_after_seconds == retry_after
    assert failure.partial_text is PartialText.NONE
    assert failure.audit_outcome is outcome


@pytest.mark.parametrize(
    ("kind", "code", "retry", "partial", "outcome"),
    [
        (
            FailureKind.VALIDATION,
            "internal_error",
            RetryOwner.NONE,
            PartialText.REPLACED,
            AuditOutcome.INTERNAL_ERROR,
        ),
        (
            FailureKind.AUTHORIZATION,
            "authorization_denied",
            RetryOwner.NONE,
            PartialText.REPLACED,
            AuditOutcome.DENIED,
        ),
        (
            FailureKind.DEPENDENCY,
            "dependency_unavailable",
            RetryOwner.CALLER,
            PartialText.REPLACED,
            AuditOutcome.DEPENDENCY_ERROR,
        ),
        (
            FailureKind.CAPACITY,
            "capacity_exceeded",
            RetryOwner.CALLER,
            PartialText.REPLACED,
            AuditOutcome.THROTTLED,
        ),
        (
            FailureKind.INTERNAL,
            "internal_error",
            RetryOwner.NONE,
            PartialText.REPLACED,
            AuditOutcome.INTERNAL_ERROR,
        ),
        (
            FailureKind.CANCELLATION,
            None,
            RetryOwner.NONE,
            PartialText.ABANDONED,
            AuditOutcome.CANCELLED,
        ),
    ],
)
def test_after_the_headers_a_failure_is_a_terminal_event_and_never_a_status(
    kind: FailureKind,
    code: str | None,
    retry: RetryOwner,
    partial: PartialText,
    outcome: AuditOutcome,
) -> None:
    failure = stream_failure(kind, AFTER)

    assert failure.status_code is None
    assert failure.retry_after_seconds is None
    assert failure.public_code == code
    assert failure.retry is retry
    assert failure.partial_text is partial
    assert failure.audit_outcome is outcome


def database_state(error: type[Exception]) -> database_errors.OperationalError:
    """An OperationalError whose driver error carries one SQLSTATE."""
    return database_errors.OperationalError(f"SELECT '{MARKER}'", None, error(MARKER))


@pytest.mark.parametrize(
    "error",
    [
        database_down(),
        database_state(pg.QueryCanceled),
        database_state(pg.AdminShutdown),
        database_state(pg.TooManyConnections),
        database_state(pg.SerializationFailure),
        database_errors.TimeoutError(MARKER),
    ],
    ids=[
        "unreachable",
        "statement-timeout",
        "shutdown",
        "too-many-connections",
        "serialization",
        "pool-exhausted",
    ],
)
def test_a_database_that_is_unavailable_is_a_dependency_failure(
    error: Exception,
) -> None:
    assert classify_failure(error) is FailureKind.DEPENDENCY


def test_a_database_error_that_is_a_bug_stays_internal() -> None:
    broken = database_errors.IntegrityError("INSERT", None, Exception(MARKER))

    assert classify_failure(broken) is FailureKind.INTERNAL


# --- Failures injected before the headers ---------------------------------------------


@pytest.mark.parametrize(
    ("error", "status", "code", "retry_after", "outcome"),
    [
        (database_down(), 503, "dependency_unavailable", "5", "dependency_error"),
        (EmbeddingRateLimited(MARKER), 429, "capacity_exceeded", "5", "throttled"),
        (
            EmbeddingInputRejected(RejectionReason.EMPTY_TEXT, index=0),
            422,
            "validation_error",
            None,
            "rejected",
        ),
        (AuthorizationDenied(MARKER), 403, "authorization_denied", None, "denied"),
        (RuntimeError(MARKER), 500, "internal_error", None, "internal_error"),
    ],
    ids=["database", "provider-limit", "validation", "denied", "bug"],
)
async def test_a_failure_in_the_search_is_an_http_error_with_retry_guidance(
    error: Exception,
    status: int,
    code: str,
    retry_after: str | None,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = Model()
    app = create_app(streamer=streamer(model, source=Source(error=error)))

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.headers.get("retry-after") == retry_after
    assert model.calls == 0
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("before_headers", outcome)
    assert record["partial_text"] == "none"
    assert not leaks(caplog, response.content, str(response.headers).encode())


# --- Failures injected after the headers ----------------------------------------------


def at(point: str, error: Exception) -> dict[str, Any]:
    """Where the failure happens once the stream has started."""
    if point == "before-text":
        return {"model": Model(parts=[], error=error)}  # type: ignore[arg-type]
    if point == "mid-text":
        return {"model": Model(error=error)}  # type: ignore[arg-type]
    return {"model": Model(), "finisher": Raising(error)}


@pytest.mark.parametrize(
    ("point", "error", "code", "retry", "outcome"),
    [
        (
            "before-text",
            ChatModelUnavailable(MARKER),
            "dependency_unavailable",
            "caller",
            "dependency_error",
        ),
        (
            "mid-text",
            ChatModelUnavailable(MARKER),
            "dependency_unavailable",
            "caller",
            "dependency_error",
        ),
        (
            "mid-text",
            ChatModelRateLimited(MARKER),
            "capacity_exceeded",
            "caller",
            "throttled",
        ),
        (
            "after-text",
            database_down(),
            "dependency_unavailable",
            "caller",
            "dependency_error",
        ),
        (
            "after-text",
            EmbeddingInputRejected(RejectionReason.EMPTY_TEXT, index=0),
            "internal_error",
            "none",
            "internal_error",
        ),
        (
            "after-text",
            RuntimeError(MARKER),
            "internal_error",
            "none",
            "internal_error",
        ),
    ],
    ids=[
        "provider-before-text",
        "provider-mid-text",
        "provider-limit-mid-text",
        "database-after-text",
        "validation-after-text",
        "bug-after-text",
    ],
)
async def test_a_failure_after_the_headers_ends_in_one_error_event(
    point: str,
    error: Exception,
    code: str,
    retry: str,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    sent = await chunks(streamer(**at(point, error)))

    assert names(sent)[0] == "metadata"
    assert names(sent)[-1] == "error"
    assert names(sent).count("error") == 1
    assert "citation" not in names(sent)
    data = error_data(sent)
    assert (data["code"], data["retry"]) == (code, retry)
    result = rebuilt(sent)
    assert result.outcome is StreamOutcome.FAILED
    # The text already streamed is replaced by the fixed message.
    assert result.text == ""
    assert result.message == data["message"]
    streamed = "text_delta" in names(sent)
    assert streamed is (point != "before-text")
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("after_headers", outcome)
    assert record["partial_text"] == ("replaced" if streamed else "none")
    assert not leaks(caplog, *sent)


async def test_a_finished_stream_records_what_the_client_keeps(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    sent = await chunks(streamer(Model()))

    assert rebuilt(sent).outcome is StreamOutcome.ANSWERED
    record = ended(caplog)
    assert (record["audit_outcome"], record["partial_text"]) == ("answered", "kept")


# --- Cancellation at several points ---------------------------------------------------


async def test_a_client_that_leaves_during_the_search_is_recorded_before_the_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    source = Source(wait=True)
    opening = asyncio.create_task(
        streamer(Model(), source=source).open(QUESTION, request_id=REQUEST_ID)
    )
    await asyncio.sleep(0)

    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening

    assert source.cancelled
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("before_headers", "cancelled")
    assert record["partial_text"] == "none"


async def test_a_client_that_leaves_mid_text_abandons_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model(hold=2)
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)
    sent = [await anext(events), await anext(events)]

    await events.aclose()

    assert names(sent) == ["metadata", "text_delta"]
    assert model.closed
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("after_headers", "cancelled")
    assert record["partial_text"] == "abandoned"
    # What the client holds is cut off, and a reader refuses it.
    reader = AnswerEventReader()
    for chunk in sent:
        reader.feed(chunk)
    with pytest.raises(InvalidEventStream, match="truncated"):
        reader.close()


# --- The event contract follows the policy --------------------------------------------


def test_an_error_event_with_the_retry_owner_of_the_http_policy_is_refused() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    reader = AnswerEventReader()
    reader.feed(writer.metadata())
    sent = writer.error(FailureKind.DEPENDENCY)
    # Before the headers an outage is the service's to retry; not after.
    stale = sent.replace(b'"retry":"caller"', b'"retry":"service"')

    assert stale != sent
    with pytest.raises(InvalidEventStream, match="malformed"):
        reader.feed(stale)


# --- Review regressions ---------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        database_state(pg.ProgramLimitExceeded),
        database_state(pg.ObjectNotInPrerequisiteState),
        database_errors.InterfaceError("SELECT 1", None, Exception(MARKER)),
    ],
    ids=["program-limit", "wrong-state", "driver-misuse"],
)
async def test_a_database_error_retrying_cannot_fix_is_internal_without_retry_after(
    error: Exception,
) -> None:
    app = create_app(streamer=streamer(Model(), source=Source(error=error)))

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert classify_failure(error) is FailureKind.INTERNAL
    assert response.status_code == 500
    assert "retry-after" not in response.headers


class LeavesOnHeaders(Connection):
    """Leaves while the response headers are still being handed over."""

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.left.set()
            await asyncio.Event().wait()
        await super().send(message)


async def test_a_client_that_leaves_as_the_headers_go_out_is_after_the_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model()
    connection = LeavesOnHeaders(leave_after=1)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send), timeout=5
    )

    assert connection.bodies == []
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("after_headers", "cancelled")
    assert record["partial_text"] == "none"


async def test_text_sent_at_the_end_is_counted_when_the_client_leaves(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    # In this key order nothing streams live; the whole text goes at the end.
    final = json.dumps(
        {"citations": [CITED], "abstained": False, "reason": None, "answer": ANSWER}
    )
    events = await streamer(Model(final=final)).open(QUESTION, request_id=REQUEST_ID)
    sent = [await anext(events), await anext(events)]

    await events.aclose()

    assert names(sent) == ["metadata", "text_delta"]
    record = ended(caplog)
    assert record["characters"] == len(ANSWER)
    assert (record["audit_outcome"], record["partial_text"]) == (
        "cancelled",
        "abandoned",
    )


@pytest.mark.parametrize(
    ("finisher", "outcome", "partial"),
    [(None, "answered", "kept"), ("failing", "dependency_error", "replaced")],
    ids=["completion", "error"],
)
async def test_a_client_that_read_the_terminal_event_and_left_is_not_cancelled(
    finisher: str | None,
    outcome: str,
    partial: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    failing = Raising(database_down()) if finisher else None
    events = await streamer(Model(), finisher=failing).open(
        QUESTION, request_id=REQUEST_ID
    )
    sent: list[bytes] = []
    while not sent or names(sent)[-1] not in ("completion", "error"):
        sent.append(await anext(events))

    # It leaves before asking for more, while the stream waits at the end.
    await events.aclose()

    record = ended(caplog)
    assert (record["audit_outcome"], record["partial_text"]) == (outcome, partial)


class FailingWorkflow:
    async def answer(self, question: Question, *, request_id: UUID) -> Any:
        raise ChatModelUnavailable(MARKER)


async def test_the_answer_endpoint_gives_the_same_retry_guidance() -> None:
    app = create_app(workflow=cast(AnswerWorkflow, FailingWorkflow()))

    async with client(app) as http:
        response = await http.post("/v1/answer", json={"text": QUESTION.text})

    assert response.status_code == 503
    assert response.headers["retry-after"] == str(RETRY_AFTER_SECONDS)
