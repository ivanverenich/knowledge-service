import asyncio
from uuid import UUID

import pytest

from fakes import FakeChatModel
from knowledge_service.contracts import Question
from knowledge_service.model import ChatModel, ChatModelUnavailable


async def test_chat_model_returns_structured_answer() -> None:
    model: ChatModel = FakeChatModel()
    request_id = UUID("00000000-0000-0000-0000-000000000001")

    result = await model.answer(
        Question(text="What is the capital of France?"),
        request_id=request_id,
    )

    assert result.request_id == request_id
    assert result.text
    assert result.usage.total_tokens >= 0


async def test_chat_model_raiser_error() -> None:
    model: ChatModel = FakeChatModel(raise_error=True)
    request_id = UUID("00000000-0000-0000-0000-000000000001")
    with pytest.raises(ChatModelUnavailable):
        await model.answer(
            Question(text="What is the capital of France?"),
            request_id=request_id,
        )


async def test_chat_model_cancel_request() -> None:
    blocker = asyncio.Event()
    model: ChatModel = FakeChatModel(wait_for=blocker)
    request_id = UUID("00000000-0000-0000-0000-000000000001")
    task = asyncio.create_task(
        model.answer(
            Question(text="What is the capital of France?"),
            request_id=request_id,
        )
    )

    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
