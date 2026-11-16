"""The OpenAI adapter's stream: pieces, the final reply, failures, cleanup."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import anyio
import httpx
import pytest
from openai import AsyncOpenAI

from evidence_fakes import candidate
from knowledge_service.answering.context_budget import allocate_context
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import ModelReply
from knowledge_service.answering.openai_answering import (
    AnswerSettings,
    OpenAIAnswerModel,
)
from knowledge_service.model import (
    ChatModelError,
    ChatModelMalformedResponse,
    ChatModelRateLimited,
    ChatModelUnavailable,
)
from openai_stubs import status_error
from ranking_stubs import response_for

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000051")
QUESTION = "How many days of annual leave do I get?"
EVIDENCE = build_evidence(
    [candidate(1, text="Every employee receives 24 days of leave.")]
)
PROMPT = allocate_context(QUESTION, EVIDENCE, AnswerSettings().budget).prompt
CITED = EVIDENCE.items[0].evidence_id.value
REPLY = json.dumps(
    {
        "answer": "You get 24 days.",
        "citations": [CITED],
        "abstained": False,
        "reason": None,
    }
)


def delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def completed(text: str | None = REPLY) -> SimpleNamespace:
    return SimpleNamespace(type="response.completed", response=response_for(text))


class StubStream:
    """The SDK's AsyncStream: events in order, then `error`; records close."""

    def __init__(self, events: list[object], error: BaseException | None) -> None:
        self._events = events
        self._error = error
        self.closed = False

    def __aiter__(self) -> AsyncIterator[object]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[object]:
        for event in self._events:
            yield event
        if self._error is not None:
            raise self._error

    async def close(self) -> None:
        self.closed = True


class StubResponses:
    def __init__(
        self,
        events: list[object],
        *,
        error: BaseException | None = None,
        refuse: BaseException | None = None,
    ) -> None:
        self.stream = StubStream(events, error)
        self.refuse = refuse
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> StubStream:
        self.calls.append(kwargs)
        if self.refuse is not None:
            raise self.refuse
        return self.stream


class StubClient:
    def __init__(self, responses: StubResponses) -> None:
        self.responses = responses

    def with_options(self, **options: Any) -> StubClient:
        return self


def model_for(responses: StubResponses) -> OpenAIAnswerModel:
    return OpenAIAnswerModel(
        StubClient(responses),  # pyright: ignore[reportArgumentType]
        AnswerSettings(),
    )


async def drain(model: OpenAIAnswerModel) -> list[str | ModelReply]:
    parts = model.stream(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)
    return [part async for part in parts]


async def test_the_stream_yields_text_pieces_then_the_billed_reply() -> None:
    responses = StubResponses([delta(REPLY[:10]), delta(REPLY[10:]), completed()])

    *texts, reply = await drain(model_for(responses))

    assert texts == [REPLY[:10], REPLY[10:]]
    assert isinstance(reply, ModelReply)
    assert reply.text == REPLY
    assert reply.usage.input_tokens == 120
    assert responses.calls[0]["stream"] is True
    assert responses.calls[0]["store"] is False
    assert responses.stream.closed


async def test_a_refusal_streams_no_text_and_says_refused() -> None:
    responses = StubResponses([completed(None)])

    (reply,) = await drain(model_for(responses))

    assert isinstance(reply, ModelReply)
    assert reply.refused


async def test_closing_the_stream_early_closes_the_provider_stream() -> None:
    responses = StubResponses([delta("{"), delta('"answer"'), completed()])
    parts = model_for(responses).stream(
        PROMPT, request_id=REQUEST_ID, timeout_seconds=5
    )

    await anext(parts)
    await parts.aclose()

    assert responses.stream.closed


@pytest.mark.parametrize(
    ("events", "error", "refuse", "expected"),
    [
        ([delta("{")], None, None, ChatModelMalformedResponse),
        ([delta("{"), SimpleNamespace(type="error")], None, None, ChatModelUnavailable),
        ([delta("{")], httpx.ReadError("gone"), None, ChatModelUnavailable),
        ([], None, status_error(429), ChatModelRateLimited),
        ([delta("{")], status_error(429), None, ChatModelRateLimited),
    ],
)
async def test_failures_become_port_errors_and_close_the_stream(
    events: list[object],
    error: BaseException | None,
    refuse: BaseException | None,
    expected: type[ChatModelError],
) -> None:
    responses = StubResponses(events, error=error, refuse=refuse)

    with pytest.raises(expected) as caught:
        await drain(model_for(responses))

    assert caught.value.__context__ is None
    assert responses.stream.closed or refuse is not None


class SlowClosingStream(StubStream):
    """A stream that stalls after its first event and awaits while closing."""

    async def _iterate(self) -> AsyncIterator[object]:
        yield delta("{")
        await asyncio.Event().wait()

    async def close(self) -> None:
        await asyncio.sleep(0)
        self.closed = True


async def test_a_cancelled_caller_still_closes_the_provider_stream() -> None:
    responses = StubResponses([])
    responses.stream = SlowClosingStream([], None)
    parts = model_for(responses).stream(
        PROMPT, request_id=REQUEST_ID, timeout_seconds=5
    )

    # The scope stays cancelled, so every await inside it is cancelled again.
    with anyio.move_on_after(0.05):
        async for _ in parts:
            pass

    assert responses.stream.closed


@pytest.mark.live
async def test_openai_live_stream_answers_in_pieces() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key is None:
        pytest.skip("OPENAI_API_KEY is required for the opt-in live test")

    client = AsyncOpenAI(api_key=api_key)
    try:
        parts = await drain(OpenAIAnswerModel(client, AnswerSettings()))
    finally:
        await client.close()

    *texts, reply = parts
    assert isinstance(reply, ModelReply)
    assert len(texts) > 1
    assert "".join(str(t) for t in texts) == reply.text
    assert "24" in str(reply.text)
