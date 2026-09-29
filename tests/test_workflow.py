import asyncio
from uuid import UUID

import pytest

from fakes import FakeChatModel, SlowChatModel
from knowledge_service.contracts import Answer, Question
from knowledge_service.errors import AnswerDeadlineExceeded
from knowledge_service.model import ChatModelUnavailable
from knowledge_service.workflow import AnswerWorkflow

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000001")


class RawTimeoutModel:
    async def answer(
        self,
        question: Question,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> Answer:
        raise TimeoutError("model implementation timed out")


async def test_workflow_returns_answer() -> None:
    workflow = AnswerWorkflow(FakeChatModel())
    result = await workflow.answer(
        Question(text="What is the capital of France?"), request_id=REQUEST_ID
    )

    assert result.text
    assert result.request_id == REQUEST_ID


async def test_workflow_propagates_model_failure() -> None:
    model = FakeChatModel(raise_error=True)
    workflow = AnswerWorkflow(model)
    with pytest.raises(ChatModelUnavailable):
        await workflow.answer(
            Question(text="What is the capital of France?"), request_id=REQUEST_ID
        )


async def test_workflow_deadline_stops_work_and_cleans_up() -> None:
    model = SlowChatModel()
    workflow = AnswerWorkflow(model, timeout_seconds=0.01)

    with pytest.raises(AnswerDeadlineExceeded):
        await workflow.answer(
            Question(text="A question"),
            request_id=REQUEST_ID,
        )

    assert model.started.is_set()
    assert model.cleaned_up.is_set()


async def test_workflow_preserves_model_timeout_error() -> None:
    workflow = AnswerWorkflow(RawTimeoutModel())

    with pytest.raises(TimeoutError, match="model implementation timed out"):
        await workflow.answer(
            Question(text="A question"),
            request_id=REQUEST_ID,
        )


async def test_workflow_preserves_cancellation_and_cleans_up() -> None:
    model = SlowChatModel()
    workflow = AnswerWorkflow(model, timeout_seconds=1.0)

    task = asyncio.create_task(
        workflow.answer(Question(text="A question"), request_id=REQUEST_ID)
    )
    await model.started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert model.cleaned_up.is_set()


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
def test_workflow_raises_exception_on_incorrect_timeout_values(
    timeout_seconds: float,
) -> None:
    model = FakeChatModel()

    with pytest.raises(ValueError, match="timeout_seconds must be finite and positive"):
        AnswerWorkflow(model, timeout_seconds=timeout_seconds)
