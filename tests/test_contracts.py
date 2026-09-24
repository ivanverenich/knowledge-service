"""Tests for Pydantic transport contracts."""

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from knowledge_service.contracts import Answer, ErrorResponse, Question, Usage


def test_question_accepted() -> None:
    question = Question(text="What is the capital of France?")

    assert question.text == "What is the capital of France?"


@pytest.mark.parametrize(
    "payload",
    [
        {"text": ""},
        {"text": "a" * 4_001},
    ],
)
def test_invalid_question_text_rejected(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        Question.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"input_tokens": -1, "output_tokens": 0, "total_tokens": 0},
        {"input_tokens": 0, "output_tokens": -1, "total_tokens": 0},
        {"input_tokens": 0, "output_tokens": 0, "total_tokens": -1},
    ],
)
def test_negative_usage_rejected(payload: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        Usage.model_validate(payload)


def test_openapi_documents_contracts() -> None:
    probe = FastAPI()

    @probe.post(
        "/answer",
        response_model=Answer,
        responses={400: {"model": ErrorResponse}},
    )
    async def answer(question: Question) -> Answer:
        raise NotImplementedError

    schema = probe.openapi()
    components = schema["components"]["schemas"]
    question_schema = components["Question"]

    assert question_schema["properties"]["text"]["minLength"] == 1
    assert question_schema["properties"]["text"]["maxLength"] == 4_000
    assert question_schema["properties"]["text"]["description"]
    assert "Answer" in components
    assert "ErrorResponse" in components
