from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from knowledge_service.contracts import Answer, ErrorResponse, Question
from knowledge_service.model import ChatModelRateLimited, ChatModelTimeout
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
        return error_response(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            code="validation_error",
            message="Request validation failed",
            request_id=request_id_from(request),
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
        except ChatModelTimeout:
            return error_response(
                status_code=504,
                code="model_timeout",
                message="The model request timed out",
                request_id=request_id,
            )
        except ChatModelRateLimited:
            return error_response(
                status_code=429,
                code="model_rate_limited",
                message="The model rate limit was reached",
                request_id=request_id,
            )

    return app
