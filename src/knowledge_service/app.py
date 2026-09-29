import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from knowledge_service.contracts import Answer, ErrorResponse, Question
from knowledge_service.errors import (
    FailureKind,
    classify_failure,
    policy_for,
)
from knowledge_service.workflow import AnswerWorkflow

ReadinessCheck = Callable[[], Awaitable[bool]]


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request_id: UUID,
) -> JSONResponse:
    body = ErrorResponse(
        code=code,
        message=message,
        request_id=request_id,
    )

    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
    )


def failure_response(
    *,
    kind: FailureKind,
    request_id: UUID,
) -> JSONResponse:
    policy = policy_for(kind)

    status_code = policy.status_code
    public_code = policy.public_code
    client_message = policy.client_message

    if status_code is None or public_code is None or client_message is None:
        raise RuntimeError("Failure policy has no public HTTP response")

    return error_response(
        status_code=status_code,
        code=public_code,
        message=client_message,
        request_id=request_id,
    )


def request_id_from(request: Request) -> UUID:
    raw_request_id = request.headers.get("X-Request-ID")
    if raw_request_id is None:
        return uuid4()
    try:
        return UUID(raw_request_id)
    except ValueError:
        return uuid4()


def create_app(
    readiness_check: ReadinessCheck | None = None,
    workflow: AnswerWorkflow | None = None,
) -> FastAPI:
    app = FastAPI(title="knowledge-service")

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return failure_response(
            kind=FailureKind.VALIDATION, request_id=request_id_from(request)
        )

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def readiness() -> JSONResponse:
        ready = readiness_check is None or await readiness_check()
        if not ready:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "not_ready"},
            )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "ready"},
        )

    @app.post("/v1/answer", response_model=Answer)
    async def answer(
        question: Question,
        request: Request,
    ) -> Answer | JSONResponse:
        request_id = request_id_from(request)

        if workflow is None:
            return error_response(
                status_code=503,
                code="workflow_unavailable",
                message="Answer workflow is unavailable",
                request_id=request_id,
            )

        try:
            return await workflow.answer(
                question,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return failure_response(
                kind=classify_failure(error),
                request_id=request_id,
            )

    return app
