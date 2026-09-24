from uuid import UUID

import pytest

from fakes import FakeChatModel
from knowledge_service.contracts import Question
from knowledge_service.model import ChatModelUnavailable
from knowledge_service.workflow import AnswerWorkflow

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000001")


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
