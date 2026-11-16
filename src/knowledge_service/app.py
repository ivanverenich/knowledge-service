import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Coroutine
from uuid import UUID, uuid4

import anyio
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from knowledge_service.answering.stream_failures import StreamPhase, stream_failure
from knowledge_service.answering.streaming import (
    CLOSE_SECONDS,
    AnswerStream,
    AnswerStreamer,
)
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
    """The JSON error for `kind`: a response not yet started has no stream."""
    policy = policy_for(kind)
    # An outage or a limit says when to send the request again.
    retry_after = stream_failure(kind, StreamPhase.BEFORE_HEADERS).retry_after_seconds

    status_code = policy.status_code
    public_code = policy.public_code
    client_message = policy.client_message

    if status_code is None or public_code is None or client_message is None:
        raise RuntimeError("Failure policy has no public HTTP response")

    response = error_response(
        status_code=status_code,
        code=public_code,
        message=client_message,
        request_id=request_id,
    )
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


def request_id_from(request: Request) -> UUID:
    raw_request_id = request.headers.get("X-Request-ID")
    if raw_request_id is None:
        return uuid4()
    try:
        return UUID(raw_request_id)
    except ValueError:
        return uuid4()


class EventStreamResponse(StreamingResponse):
    """An SSE response that stops its events the moment the client leaves.

    Under ASGI 2.4, Starlette notices a disconnect only when its next write
    fails, so a stream waiting on a silent model would run on. This
    response always listens for the disconnect. The writing task is then
    cancelled once, so the model's own cleanup is not cut short, and the
    events are closed, so the model's stream is closed even when they were
    waiting to be written. Each wait is bounded by `CLOSE_SECONDS`.
    """

    media_type = "text/event-stream"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        writing = asyncio.ensure_future(self._write(send))
        watching = asyncio.ensure_future(self.listen_for_disconnect(receive))
        try:
            await asyncio.wait({writing, watching}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            watching.cancel()
            writing.cancel()
            await asyncio.wait({writing, watching}, timeout=2 * CLOSE_SECONDS)
            close = getattr(self.body_iterator, "aclose", None)
            # A writing task still running holds the events; leave it be.
            if writing.done() and close is not None:
                # Shielded, so it runs even while cancelled; bounded, so a
                # stuck close cannot hold the response open.
                with anyio.move_on_after(2 * CLOSE_SECONDS, shield=True):
                    await close()
        if writing.done() and not writing.cancelled():
            writing.result()

    async def _write(self, send: Send) -> None:
        async def sending(message: Message) -> None:
            # Recorded as the headers are handed over, before any event.
            if message["type"] == "http.response.start":
                headers_sent = getattr(self.body_iterator, "headers_sent", None)
                if callable(headers_sent):
                    headers_sent()
            await send(message)

        # Under ASGI 2.4, a write after the client left raises OSError.
        with contextlib.suppress(OSError):
            await self.stream_response(sending)


async def _unless_client_leaves(
    request: Request, opening: Coroutine[object, object, AnswerStream]
) -> AnswerStream | None:
    """Await `opening`, or cancel it and return None when the client leaves.

    Its errors are raised as they are, so they still map to an HTTP error.
    """

    async def left() -> None:
        while (await request.receive())["type"] != "http.disconnect":
            pass

    work = asyncio.ensure_future(opening)
    watch = asyncio.ensure_future(left())
    try:
        await asyncio.wait({work, watch}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (watch, work):
            if not task.done():
                task.cancel()
        # Each task finishes its own cleanup, such as ending its record.
        await asyncio.wait({work, watch})
    if watch.cancelled():
        return work.result()
    if not work.cancelled() and work.exception() is None:
        await work.result().aclose()
    return None


def create_app(
    readiness_check: ReadinessCheck | None = None,
    workflow: AnswerWorkflow | None = None,
    streamer: AnswerStreamer | None = None,
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

    @app.post("/v1/answer/stream")
    async def answer_stream(question: Question, request: Request) -> Response:
        request_id = request_id_from(request)
        if streamer is None:
            return error_response(
                status_code=503,
                code="workflow_unavailable",
                message="Answer workflow is unavailable",
                request_id=request_id,
            )
        try:
            events = await _unless_client_leaves(
                request, streamer.open(question, request_id=request_id)
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Nothing was sent yet, so a failure is still a plain HTTP error.
            return failure_response(kind=classify_failure(error), request_id=request_id)
        if events is None:
            # Nobody is left to read it; 499 marks the request in access logs.
            return Response(status_code=499)
        return EventStreamResponse(
            events,
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Request-ID": str(request_id),
            },
        )

    return app
