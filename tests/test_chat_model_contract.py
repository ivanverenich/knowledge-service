"""Behavioral contract tests shared by every ChatModel adapter."""

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

import httpx2
import openai
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr

from fakes import FakeChatModel
from knowledge_service.contracts import Answer, Question
from knowledge_service.model import ChatModel, ChatModelError
from knowledge_service.openai_model import OpenAIChatModel, OpenAISettings

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000011")


class StubResponses:
    """Replace only AsyncOpenAI.responses for
    deterministic adapter tests."""

    def __init__(self, result: Any = None, *, block: bool = False) -> None:
        self._result = result
        self._block = block

    async def parse(self, **_kwargs: Any) -> Any:
        if self._block:
            await asyncio.Event().wait()
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class StubClient:
    def __init__(self, responses: StubResponses) -> None:
        self.responses = responses


def openai_model(responses: StubResponses) -> ChatModel:
    settings = OpenAISettings(
        model_api_key=SecretStr("test-key"),
        model_name="test-model",
        model_timeout_seconds=5.0,
    )
    client = cast(AsyncOpenAI, StubClient(responses))
    return OpenAIChatModel(client, settings)


def openai_success() -> ChatModel:
    response = SimpleNamespace(
        output=[],
        output_parsed=SimpleNamespace(text="A managed answer"),
        usage=SimpleNamespace(
            input_tokens=3,
            output_tokens=2,
            total_tokens=5,
        ),
    )
    return openai_model(StubResponses(response))


def openai_failure() -> ChatModel:
    request = httpx2.Request("POST", "https://example.test")
    return openai_model(StubResponses(openai.APITimeoutError(request)))


def openai_blocked() -> ChatModel:
    return openai_model(StubResponses(block=True))


@dataclass(frozen=True)
class AdapterCase:
    name: str
    success: Callable[[], ChatModel]
    failure: Callable[[], ChatModel]
    blocked: Callable[[], ChatModel]


CASES = (
    AdapterCase(
        name="fake",
        success=FakeChatModel,
        failure=lambda: FakeChatModel(raise_error=True),
        blocked=lambda: FakeChatModel(wait_for=asyncio.Event()),
    ),
    AdapterCase(
        name="openai",
        success=openai_success,
        failure=openai_failure,
        blocked=openai_blocked,
    ),
)


def assert_success_contract(answer: Answer) -> None:
    assert answer.request_id == REQUEST_ID
    assert answer.text.strip()
    assert answer.usage.provider
    assert answer.usage.model
    assert answer.usage.latency_ms >= 0
    assert answer.usage.input_tokens >= 0
    assert answer.usage.output_tokens >= 0
    assert answer.usage.total_tokens >= 0


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
async def test_success_contract(case: AdapterCase) -> None:
    answer = await case.success().answer(
        Question(text="What behavior does the port promise?"),
        request_id=REQUEST_ID,
        timeout_seconds=2.0,
    )

    assert_success_contract(answer)


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
async def test_typed_failure_contract(case: AdapterCase) -> None:
    with pytest.raises(ChatModelError):
        await case.failure().answer(
            Question(text="Trigger a model failure"),
            request_id=REQUEST_ID,
            timeout_seconds=2.0,
        )


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
async def test_cancellation_contract(case: AdapterCase) -> None:
    task = asyncio.create_task(
        case.blocked().answer(
            Question(text="Cancel this request"),
            request_id=REQUEST_ID,
            timeout_seconds=2.0,
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.live
async def test_openai_live_success_contract() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key is None:
        pytest.skip("OPENAI_API_KEY is required for the opt-in live test")

    client = AsyncOpenAI(api_key=api_key)
    settings = OpenAISettings(
        model_api_key=SecretStr(api_key),
        model_name="gpt-4.1-mini",
        model_timeout_seconds=30.0,
    )
    try:
        answer = await OpenAIChatModel(client, settings).answer(
            Question(text="Reply with one short sentence about testing."),
            request_id=REQUEST_ID,
            timeout_seconds=30.0,
        )
    finally:
        await client.close()

    assert_success_contract(answer)
