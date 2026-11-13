"""The OpenAI answer adapter: what it sends, what it reads, how it fails."""

import asyncio
import json
import logging
import os
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx2
import openai
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.context_budget import ContextBudget, allocate_context
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import (
    AnswerOutcome,
    InvalidAnswerRequest,
    generate_answer,
)
from knowledge_service.answering.openai_answering import (
    AnswerSettings,
    OpenAIAnswerModel,
)
from knowledge_service.model import (
    ChatModelError,
    ChatModelMalformedResponse,
    ChatModelRateLimited,
    ChatModelRequestRefused,
    ChatModelTimeout,
    ChatModelUnavailable,
)
from openai_stubs import status_error
from ranking_stubs import response_for

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000043")
QUESTION = "How many days of annual leave do I get?"
EVIDENCE = build_evidence(
    [candidate(1, text=f"Every employee receives 24 days of leave. {MARKER}")]
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


class StubResponses:
    """Replace only `AsyncOpenAI.responses`; record every request.

    By default it answers `REPLY`. `response` answers with that object
    instead, `error` raises, and `block` never answers.
    """

    def __init__(
        self,
        *,
        response: object = None,
        error: BaseException | None = None,
        block: bool = False,
    ) -> None:
        self._response = response
        self._error = error
        self._block = block
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._block:
            await asyncio.Event().wait()
        if self._error is not None:
            raise self._error
        if self._response is not None:
            return self._response
        return response_for(REPLY)


class StubClient:
    """Enough of AsyncOpenAI for the adapter; only the configured copy answers."""

    def __init__(self, responses: StubResponses) -> None:
        self._configured = responses
        self.responses: StubResponses | None = None
        self.option_calls: list[dict[str, Any]] = []

    def with_options(self, **options: Any) -> StubClient:
        self.option_calls.append(options)
        copy = StubClient(self._configured)
        copy.responses = self._configured
        return copy


def answerer(
    responses: StubResponses, **settings: Any
) -> tuple[OpenAIAnswerModel, StubClient]:
    client = StubClient(responses)
    model = OpenAIAnswerModel(
        client,  # pyright: ignore[reportArgumentType]
        AnswerSettings(**settings),
    )
    return model, client


async def test_the_request_sends_the_counted_prompt_and_its_schema() -> None:
    responses = StubResponses()
    model, client = answerer(responses, timeout_seconds=7.0)

    reply = await model.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=30)

    (call,) = responses.calls
    assert client.option_calls == [{"max_retries": 0}]
    assert call["model"] == "gpt-5.4-mini-2026-03-17"
    assert call["instructions"] == PROMPT.instructions
    assert call["input"] == PROMPT.input
    assert call["text"] == {
        "format": {
            "type": "json_schema",
            "name": "answer",
            "strict": True,
            "schema": PROMPT.response_schema(),
        }
    }
    assert call["max_output_tokens"] == 1_000
    assert call["store"] is False
    assert call["timeout"] == 7.0
    assert call["extra_headers"] == {"X-Request-ID": str(REQUEST_ID)}
    assert (reply.text, reply.refused) == (REPLY, False)
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (120, 12)
    assert reply.usage.price_id == "gpt-5.4-mini-2026-03-17"


async def test_a_refusal_is_reported_with_its_usage() -> None:
    model, _ = answerer(StubResponses(response=response_for(None)))

    reply = await model.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)

    assert (reply.text, reply.refused) == (None, True)
    assert reply.usage.input_tokens == 120


@pytest.mark.parametrize(
    "response",
    [
        response_for(REPLY, status="incomplete"),
        SimpleNamespace(
            status="completed",
            output="not a list",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        ),
        SimpleNamespace(
            status="completed",
            output=[SimpleNamespace(content=[SimpleNamespace(type="output_text")])],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        ),
    ],
    ids=["cut-off", "output-not-a-list", "text-missing"],
)
async def test_an_unreadable_reply_has_no_text_and_keeps_its_usage(
    response: object,
) -> None:
    model, _ = answerer(StubResponses(response=response))

    reply = await model.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)

    assert (reply.text, reply.refused) == (None, False)
    assert reply.usage.input_tokens >= 1


@pytest.mark.parametrize(
    "usage",
    [None, SimpleNamespace(input_tokens=-1, output_tokens=3)],
    ids=["missing", "negative"],
)
async def test_a_reply_without_usage_is_malformed(usage: object) -> None:
    response = SimpleNamespace(status="completed", output=[], usage=usage)
    model, _ = answerer(StubResponses(response=response))

    with pytest.raises(ChatModelMalformedResponse, match="no token usage"):
        await model.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)


def _connection_error() -> openai.APIConnectionError:
    request = httpx2.Request("POST", "https://api.openai.test", json={"t": MARKER})
    return openai.APIConnectionError(request=request)


def _timeout_error() -> openai.APITimeoutError:
    request = httpx2.Request("POST", "https://api.openai.test", json={"t": MARKER})
    return openai.APITimeoutError(request=request)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(400, body_text=MARKER), ChatModelRequestRefused),
        (status_error(401, body_text=MARKER), ChatModelRequestRefused),
        (status_error(408, body_text=MARKER), ChatModelTimeout),
        (status_error(409, body_text=MARKER), ChatModelUnavailable),
        (status_error(429, body_text=MARKER), ChatModelRateLimited),
        (status_error(500, body_text=MARKER), ChatModelUnavailable),
        (_connection_error(), ChatModelUnavailable),
        (_timeout_error(), ChatModelTimeout),
    ],
)
async def test_each_provider_failure_becomes_one_port_error_without_text(
    error: BaseException, expected: type[ChatModelError]
) -> None:
    model, _ = answerer(StubResponses(error=error))

    with pytest.raises(ChatModelError) as raised:
        await model.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)

    assert type(raised.value) is expected
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert MARKER not in str(raised.value)


async def test_a_stalled_provider_times_out_at_the_deadline() -> None:
    model, _ = answerer(StubResponses(block=True))

    with pytest.raises(ChatModelTimeout):
        await model.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=0.05)


@pytest.mark.parametrize(
    ("prompt", "timeout_seconds"),
    [
        (PROMPT, 0),
        (PROMPT, float("nan")),
        (PROMPT, True),
        (PROMPT, 10**400),
        ({"input": "x"}, 5),
    ],
    ids=["zero", "nan", "bool", "huge", "not-a-prompt"],
)
async def test_a_bad_request_is_refused_before_sending(
    prompt: object, timeout_seconds: float
) -> None:
    responses = StubResponses()
    model, _ = answerer(responses)

    with pytest.raises(InvalidAnswerRequest):
        await model.generate(
            prompt,  # type: ignore[arg-type]
            request_id=REQUEST_ID,
            timeout_seconds=timeout_seconds,
        )

    assert responses.calls == []


async def test_one_log_record_per_call_with_counts_and_tokens_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model, _ = answerer(StubResponses())
    failing, _ = answerer(StubResponses(error=status_error(500, body_text=MARKER)))

    await model.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)
    with pytest.raises(ChatModelUnavailable):
        await failing.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)

    records = [r for r in caplog.records if r.message == "answer model call finished"]
    assert [r.__dict__["answer_model"]["outcome"] for r in records] == [
        "succeeded",
        "ChatModelUnavailable",
    ]
    assert records[0].__dict__["answer_model"]["input_tokens"] == 120
    assert MARKER not in caplog.text
    assert all(MARKER not in repr(r.__dict__) for r in records)


async def test_the_adapter_and_the_workflow_answer_together() -> None:
    model, _ = answerer(StubResponses())

    result = await generate_answer(
        QUESTION,
        EVIDENCE,
        AnswerSettings().budget,
        model,
        request_id=REQUEST_ID,
        timeout_seconds=5,
    )

    assert result.outcome is AnswerOutcome.ANSWERED
    assert [c.evidence_id.value for c in result.citations] == [CITED]


def test_the_settings_give_the_budget_and_the_price() -> None:
    settings = AnswerSettings()
    model, _ = answerer(StubResponses(), input_usd_per_million_tokens="1.5")

    assert settings.budget == ContextBudget(32_000, 1_000, 4_000)
    assert model.price.price_id == "gpt-5.4-mini-2026-03-17"
    assert str(model.price.input_usd_per_million_tokens) == "1.5"


@pytest.mark.parametrize(
    "setting",
    [
        {"timeout_seconds": 0},
        {"max_output_tokens": 0},
        {"context_tokens": 4_000, "history_tokens": 3_000},
        {"model": ""},
    ],
)
def test_settings_refuse_values_that_cannot_work(setting: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AnswerSettings(**setting)  # pyright: ignore[reportArgumentType]


def test_the_settings_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANSWER_CONTEXT_TOKENS", "16000")

    assert AnswerSettings().budget.context_tokens == 16_000


@pytest.mark.live
async def test_openai_live_answer_cites_the_evidence() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key is None:
        pytest.skip("OPENAI_API_KEY is required for the opt-in live test")

    client = AsyncOpenAI(api_key=api_key)
    settings = AnswerSettings()
    try:
        result = await generate_answer(
            QUESTION,
            EVIDENCE,
            settings.budget,
            OpenAIAnswerModel(client, settings),
            request_id=REQUEST_ID,
            timeout_seconds=30.0,
        )
    finally:
        await client.close()

    assert result.outcome is AnswerOutcome.ANSWERED
    assert [c.evidence_id.value for c in result.citations] == [CITED]
    assert "24" in result.text
    assert result.usage is not None
    assert 0 < result.usage.input_tokens < 2_000
