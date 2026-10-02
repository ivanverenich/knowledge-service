"""Tests for the OpenAI ChatModel adapter."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import httpx2
import openai
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr, ValidationError

from knowledge_service.contracts import (
    GeneratedAnswer,
    Question,
)
from knowledge_service.model import (
    ChatModelMalformedResponse,
    ChatModelRateLimited,
    ChatModelRefused,
    ChatModelStructuredOutputUnsupported,
    ChatModelTimeout,
)
from knowledge_service.openai_model import (
    OpenAIChatModel,
    OpenAISettings,
)

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000001")


class FakeResponses:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeClient:
    def __init__(self, result: Any) -> None:
        self.responses = FakeResponses(result)


def make_adapter(result: Any) -> tuple[OpenAIChatModel, FakeClient]:
    client = FakeClient(result)
    settings = OpenAISettings(
        model_api_key=SecretStr("test-key"),
        model_name="test-model",
        model_timeout_seconds=5.0,
        model_price_id="test-price-v1",
    )
    adapter = OpenAIChatModel(cast(AsyncOpenAI, client), settings)
    return adapter, client


def response_with_usage() -> SimpleNamespace:
    return SimpleNamespace(
        output=[],
        output_parsed=SimpleNamespace(
            text="The answer", summary="A short fake summary."
        ),
        usage=SimpleNamespace(
            input_tokens=3,
            output_tokens=2,
            total_tokens=5,
        ),
    )


def response_with_invalid_answer_error() -> ValidationError:
    try:
        GeneratedAnswer.model_validate({"text": "Answer without a summary"})
    except ValidationError as error:
        return error
    raise AssertionError("Expected invalid answer data to fail validation")


async def test_success_maps_response_and_request_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticks = iter((10.0, 10.025))
    monkeypatch.setattr(
        "knowledge_service.openai_model.time.perf_counter",
        lambda: next(ticks),
    )

    adapter, client = make_adapter(response_with_usage())

    result = await adapter.answer(
        Question(text="A question"),
        request_id=REQUEST_ID,
        timeout_seconds=2.0,
    )

    assert result.text == "The answer"
    assert result.summary == "A short fake summary."
    assert result.request_id == REQUEST_ID
    assert result.usage.provider == "openai"
    assert result.usage.model == "test-model"
    assert result.usage.price_id == "test-price-v1"
    assert result.usage.latency_ms == 25
    assert result.usage.input_tokens == 3
    assert result.usage.output_tokens == 2
    assert result.usage.total_tokens == 5
    assert client.responses.calls == [
        {
            "model": "test-model",
            "input": "A question",
            "text_format": GeneratedAnswer,
            "timeout": 2.0,
            "extra_headers": {"X-Request-ID": str(REQUEST_ID)},
        }
    ]


def test_configured_api_key_is_redacted_and_available() -> None:
    settings = OpenAISettings(model_api_key=SecretStr("test-key"))

    assert settings.model_api_key is not None
    assert settings.model_api_key.get_secret_value() == "test-key"
    assert "test-key" not in repr(settings)


async def test_missing_usage_raises_typed_malformed_response() -> None:
    adapter, _ = make_adapter(
        SimpleNamespace(
            output_text="No usage",
            output=[],
            usage=None,
        )
    )

    with pytest.raises(ChatModelMalformedResponse, match="Missing usage data"):
        await adapter.answer(
            Question(text="A question"), request_id=REQUEST_ID, timeout_seconds=2.0
        )


async def test_timeout_is_translated() -> None:
    request = httpx2.Request("POST", "https://example.test")
    adapter, _ = make_adapter(openai.APITimeoutError(request))

    with pytest.raises(ChatModelTimeout):
        await adapter.answer(
            Question(text="A question"), request_id=REQUEST_ID, timeout_seconds=2.0
        )


async def test_rate_limit_is_translated() -> None:
    request = httpx2.Request("POST", "https://example.test")
    response = httpx2.Response(429, request=request)
    error = openai.RateLimitError("rate limited", response=response, body=None)
    adapter, _ = make_adapter(error)

    with pytest.raises(ChatModelRateLimited):
        await adapter.answer(
            Question(text="A question"), request_id=REQUEST_ID, timeout_seconds=2.0
        )


async def test_cancellation_is_preserved() -> None:
    adapter, _ = make_adapter(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await adapter.answer(
            Question(text="A question"), request_id=REQUEST_ID, timeout_seconds=2.0
        )


@pytest.mark.parametrize(
    ("timeout_seconds"),
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        0.0,
        -1.0,
    ],
)
def test_settings_raises_exception_on_incorrect_timeout_values(
    timeout_seconds: float,
) -> None:
    with pytest.raises(ValidationError):
        OpenAISettings(model_timeout_seconds=timeout_seconds)


def test_generated_answer_requires_non_empty_text() -> None:
    assert (
        GeneratedAnswer(
            text="A useful answer",
            summary="A short fake summary.",
        ).text
        == "A useful answer"
    )

    with pytest.raises(ValidationError):
        GeneratedAnswer(
            text="",
            summary="A short fake summary.",
        )

    with pytest.raises(ValidationError):
        GeneratedAnswer(text="A useful answer", summary="")


async def test_refusal_raises_typed_failure() -> None:
    refusal = SimpleNamespace(
        type="message",
        content=[SimpleNamespace(type="refusal")],
    )
    response = SimpleNamespace(
        output=[refusal],
        output_parsed=None,
        usage=SimpleNamespace(
            input_tokens=3,
            output_tokens=2,
            total_tokens=5,
        ),
    )
    adapter, _ = make_adapter(response)

    with pytest.raises(ChatModelRefused):
        await adapter.answer(
            Question(text="A question"),
            request_id=REQUEST_ID,
            timeout_seconds=2.0,
        )


async def test_invalid_structured_data_raises_typed_failure() -> None:
    adapter, _ = make_adapter(response_with_invalid_answer_error())

    with pytest.raises(ChatModelMalformedResponse):
        await adapter.answer(
            Question(text="A question"),
            request_id=REQUEST_ID,
            timeout_seconds=2.0,
        )


async def test_unsupported_structured_output_raises_typed_failure() -> None:
    request = httpx2.Request("POST", "https://example.test")
    response = httpx2.Response(400, request=request)
    error = openai.BadRequestError(
        "Structured output is unsupported",
        response=response,
        body={
            "message": "Structured output is unsupported",
            "param": "text.format",
            "code": "unsupported_value",
            "type": "invalid_request_error",
        },
    )
    adapter, _ = make_adapter(error)

    with pytest.raises(ChatModelStructuredOutputUnsupported):
        await adapter.answer(
            Question(text="A question"),
            request_id=REQUEST_ID,
            timeout_seconds=2.0,
        )


async def test_unsupported_structured_output_raises_bad_request_failure() -> None:
    request = httpx2.Request("POST", "https://example.test")
    response = httpx2.Response(400, request=request)
    error = openai.BadRequestError(
        "Structured output is unsupported",
        response=response,
        body={
            "message": "Structured output is unsupported",
            "param": "input",
            "code": "unsupported_value",
            "type": "invalid_request_error",
        },
    )
    adapter, _ = make_adapter(error)

    with pytest.raises(openai.BadRequestError):
        await adapter.answer(
            Question(text="A question"),
            request_id=REQUEST_ID,
            timeout_seconds=2.0,
        )
