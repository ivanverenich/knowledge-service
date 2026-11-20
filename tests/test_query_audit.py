"""Each answer stream records one `question.asked` audit event, first.

The stream runs with fakes from `test_streaming`; its `AuditTrail` is a
`Trail` that keeps events, or fails or stalls as a database might. The
decision's Citations and completion must never leave before its event is
written, and the event holds who asked and how it ended, never the text.
"""

import asyncio
import json
import logging
from typing import Any

import anyio
import pytest

from evidence_fakes import MARKER
from identity_fakes import READER
from knowledge_service.answering import streaming
from knowledge_service.answering.abstention import EvidenceSignals
from knowledge_service.answering.events import StreamOutcome
from knowledge_service.answering.evidence import EvidenceSet
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.contracts import Question
from knowledge_service.errors import AnswerDeadlineExceeded
from test_streaming import (
    QUESTION,
    REQUEST_ID,
    Model,
    Source,
    Trail,
    chunks,
    rebuilt,
    streamer,
)


def event_names(sent: list[bytes]) -> list[str]:
    return [chunk.decode().split("\n")[1].removeprefix("event: ") for chunk in sent]


async def test_an_answer_is_recorded_once_with_its_reader_and_outcome() -> None:
    trail = Trail()

    answer = rebuilt(await chunks(streamer(Model(), audit=trail)))

    assert answer.outcome is StreamOutcome.ANSWERED
    (event,) = trail.events
    assert event.action is AuditAction.QUESTION_ASKED
    assert (event.target_kind, event.target_id) == (AuditTargetKind.REQUEST, REQUEST_ID)
    assert event.request_id == REQUEST_ID
    assert event.actor_id == READER.user_id
    assert (event.decision, event.outcome) == (
        AuditDecision.ALLOWED,
        AuditOutcome.ANSWERED,
    )
    assert dict(event.metadata) == {"phase": "after_headers", "citations": 1}


async def test_an_abstention_is_recorded_with_no_citations() -> None:
    trail = Trail()

    answer = rebuilt(
        await chunks(streamer(Model(), source=Source(empty()), audit=trail))
    )

    assert answer.outcome is StreamOutcome.ABSTAINED
    (event,) = trail.events
    assert event.outcome is AuditOutcome.ABSTAINED
    assert event.metadata["citations"] == 0


def empty() -> FoundEvidence:
    return FoundEvidence(EvidenceSet(()), EvidenceSignals(0, None, None))


async def test_no_citation_or_completion_leaves_before_its_record() -> None:
    # The audit store is down: the decision is never sent, only an error.
    trail = Trail(error=ConnectionError("audit store down"))

    sent = await chunks(streamer(Model(), audit=trail))

    names = event_names(sent)
    assert "citation" not in names
    assert "completion" not in names
    assert names[-1] == "error"
    error = json.loads(sent[-1].decode().split("\n")[2].removeprefix("data: "))
    assert error["code"] == "dependency_unavailable"
    assert trail.events == []


async def test_a_record_that_takes_too_long_counts_as_not_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(streaming, "AUDIT_SECONDS", 0.05)
    trail = Trail(delay=1.0)

    sent = await chunks(streamer(Model(), audit=trail))

    assert "completion" not in event_names(sent)
    assert event_names(sent)[-1] == "error"


async def test_a_failure_before_the_first_byte_is_recorded() -> None:
    trail = Trail()
    failing = streamer(
        Model(), source=Source(error=AnswerDeadlineExceeded()), audit=trail
    )

    with pytest.raises(AnswerDeadlineExceeded):
        await failing.open(QUESTION, reader=READER, request_id=REQUEST_ID)

    (event,) = trail.events
    assert event.outcome is AuditOutcome.DEPENDENCY_ERROR
    assert event.metadata["phase"] == "before_headers"


async def test_a_client_that_leaves_is_recorded_as_cancelled() -> None:
    trail = Trail()
    model = Model(hold=2)
    events = await streamer(model, audit=trail).open(
        QUESTION, reader=READER, request_id=REQUEST_ID
    )
    await anext(events)
    await anext(events)

    await events.aclose()

    (event,) = trail.events
    assert event.outcome is AuditOutcome.CANCELLED


async def test_a_stream_closed_before_its_first_event_is_recorded() -> None:
    trail = Trail()
    events = await streamer(Model(), audit=trail).open(
        QUESTION, reader=READER, request_id=REQUEST_ID
    )

    await events.aclose()

    (event,) = trail.events
    assert event.outcome is AuditOutcome.CANCELLED
    assert event.metadata["phase"] == "before_headers"


async def test_the_record_holds_no_question_or_answer_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    trail = Trail()
    question = Question(text=f"How many days of leave? {MARKER}")

    await chunks(streamer(Model(), audit=trail), question)

    (event,) = trail.events
    assert MARKER not in repr(event)
    assert "leave" not in repr(event)
    for record in caplog.records:
        assert MARKER not in record.getMessage()
        assert MARKER not in repr(record.__dict__)
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["audited"] is True  # type: ignore[attr-defined]


async def test_a_failed_record_is_marked_in_telemetry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    await chunks(streamer(Model(), audit=Trail(error=ConnectionError("down"))))

    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["audited"] is False  # type: ignore[attr-defined]
    assert ended.answer_stream["outcome"] == "failed"  # type: ignore[attr-defined]


async def test_a_stream_cancelled_while_its_record_is_written_keeps_it() -> None:
    # The server cancels the task when the client leaves; the write goes on.
    trail = Trail(delay=0.2)
    events = await streamer(Model(), audit=trail).open(
        QUESTION, reader=READER, request_id=REQUEST_ID
    )

    async def read_all() -> list[bytes]:
        return [chunk async for chunk in events]

    reading = asyncio.ensure_future(read_all())
    await trail.started.wait()
    reading.cancel()

    with pytest.raises(asyncio.CancelledError):
        await reading
    (event,) = trail.events
    assert event.outcome is AuditOutcome.ANSWERED


async def test_a_cancel_scope_that_ends_the_stream_mid_record_absorbs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Starlette ends a stream through an anyio cancel scope. The scope must
    # absorb its own cancellation, and the wait must not spin meanwhile.
    waits = 0
    wait = asyncio.wait

    async def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal waits
        waits += 1
        return await wait(*args, **kwargs)

    monkeypatch.setattr(asyncio, "wait", counted)
    trail = Trail(delay=0.2)
    events = await streamer(Model(), audit=trail).open(
        QUESTION, reader=READER, request_id=REQUEST_ID
    )

    with anyio.CancelScope() as scope:

        async def cancel_once_writing() -> None:
            await trail.started.wait()
            scope.cancel()

        async with anyio.create_task_group() as group:
            group.start_soon(cancel_once_writing)
            async for _ in events:
                pass

    # Reaching this line means the cancellation stayed inside its scope.
    (event,) = trail.events
    assert event.outcome is AuditOutcome.ANSWERED
    assert waits < 5
