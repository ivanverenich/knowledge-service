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

from knowledge_service.contracts import Question
from knowledge_service.model import (
    ChatModelMalformedResponse,
    ChatModelRateLimited,
    ChatModelTimeout,
)
from knowledge_service.openai_model import OpenAIChatModel, OpenAISettings

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000001")


class FakeResponses:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
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
    )
    adapter = OpenAIChatModel(cast(AsyncOpenAI, client), settings)
    return adapter, client


def response_with_usage() -> SimpleNamespace:
    return SimpleNamespace(
        output_text="The answer",
        usage=SimpleNamespace(
            input_tokens=3,
            output_tokens=2,
            total_tokens=5,
        ),
    )


async def test_success_maps_response_and_request_metadata() -> None:
    adapter, client = make_adapter(response_with_usage())

    result = await adapter.answer(
        Question(text="A question"), request_id=REQUEST_ID, timeout_seconds=2.0
    )

    assert result.text == "The answer"
    assert result.request_id == REQUEST_ID
    assert result.usage.total_tokens == 5
    assert client.responses.calls == [
        {
            "model": "test-model",
            "input": "A question",
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
    adapter, _ = make_adapter(SimpleNamespace(output_text="No usage", usage=None))

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
