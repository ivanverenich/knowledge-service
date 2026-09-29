"""Tests FastAPI application."""

import asyncio
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI

from fakes import FakeChatModel, SlowChatModel
from knowledge_service.app import create_app
from knowledge_service.contracts import Answer, Question
from knowledge_service.errors import AuthorizationDenied
from knowledge_service.model import (
    ChatModelRateLimited,
    ChatModelTimeout,
)
from knowledge_service.workflow import AnswerWorkflow

REQUEST_ID = "00000000-0000-0000-0000-000000000001"


def get_client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    )


async def test_liveness_endpoint_returns_ok() -> None:
    app = create_app()
    async with get_client(app) as client:
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


async def test_readiness_endpoint_returns_failure() -> None:
    async def not_ready() -> bool:
        return False

    app = create_app(readiness_check=not_ready)
    async with get_client(app) as client:
        response = await client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {"status": "not_ready"}


async def test_readiness_endpoint_returns_success() -> None:
    async def ready() -> bool:
        return True

    app = create_app(readiness_check=ready)
    async with get_client(app) as client:
        response = await client.get("/health/ready")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}


async def test_answer_endpoint_returns_answer() -> None:
    workflow = AnswerWorkflow(model=FakeChatModel())
    app = create_app(workflow=workflow)
    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": "What is the capital of France?"},
            headers={"X-Request-ID": REQUEST_ID},
        )
        assert response.status_code == 200
        assert response.json()["request_id"] == REQUEST_ID


class ErrorModel:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def answer(
        self,
        question: Question,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> Answer:
        raise self.error


def error_workflow(error: BaseException) -> AnswerWorkflow:
    return AnswerWorkflow(model=ErrorModel(error))


@pytest.mark.parametrize(
    ("error", "status_code", "code", "message"),
    [
        (
            ChatModelTimeout(),
            503,
            "dependency_unavailable",
            "A required service is unavailable",
        ),
        (
            ChatModelRateLimited(),
            429,
            "capacity_exceeded",
            "Service capacity was exceeded",
        ),
        (
            AuthorizationDenied(),
            403,
            "authorization_denied",
            "Access denied",
        ),
        (
            RuntimeError(),
            500,
            "internal_error",
            "An internal error occurred",
        ),
    ],
)
async def test_answer_endpoint_maps_failures(
    error: BaseException,
    status_code: int,
    code: str,
    message: str,
) -> None:
    app = create_app(workflow=error_workflow(error))

    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": "A question"},
            headers={"X-Request-ID": REQUEST_ID},
        )

    assert response.status_code == status_code
    assert response.json() == {
        "code": code,
        "message": message,
        "request_id": REQUEST_ID,
    }


async def test_answer_endpoint_preserves_cancellation() -> None:
    app = create_app(workflow=error_workflow(asyncio.CancelledError()))

    async with get_client(app) as client:
        with pytest.raises(asyncio.CancelledError):
            await client.post(
                "/v1/answer",
                json={"text": "A question"},
                headers={"X-Request-ID": REQUEST_ID},
            )


async def test_answer_endpoint_returns_workflow_unavailable_error() -> None:
    app = create_app()

    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": "A question"},
            headers={"X-Request-ID": REQUEST_ID},
        )

    assert response.status_code == 503
    assert response.json() == {
        "code": "workflow_unavailable",
        "message": "Answer workflow is unavailable",
        "request_id": REQUEST_ID,
    }


async def test_answer_endpoint_returns_validation_error() -> None:
    app = create_app(workflow=AnswerWorkflow(model=FakeChatModel()))

    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": ""},
            headers={"X-Request-ID": REQUEST_ID},
        )

    assert response.status_code == 422
    assert response.json() == {
        "code": "validation_error",
        "message": "Request validation failed",
        "request_id": REQUEST_ID,
    }


async def test_answer_endpoint_returns_correct_response_on_timeout() -> None:
    model = SlowChatModel()
    workflow = AnswerWorkflow(model=model, timeout_seconds=0.01)
    app = create_app(workflow=workflow)

    async with get_client(app) as client:
        response = await client.post(
            "/v1/answer",
            json={"text": "A Question"},
            headers={"X-Request-ID": REQUEST_ID},
        )

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
