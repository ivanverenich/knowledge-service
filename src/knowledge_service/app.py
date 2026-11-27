import asyncio
import contextlib
import logging
import re
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any, Protocol, cast
from uuid import UUID, uuid4

import anyio
from fastapi import FastAPI, Path, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Message, Receive, Scope, Send

from knowledge_service.answering.stream_failures import StreamPhase, stream_failure
from knowledge_service.answering.streaming import (
    CLOSE_SECONDS,
    AnswerStream,
    AnswerStreamer,
)
from knowledge_service.compatibility.models import (
    CompatibleModel,
    ModelList,
    compatible_error,
    model_list,
)
from knowledge_service.contracts import (
    ErrorResponse,
    JobAttemptProgress,
    JobsNeedingDecision,
    OperatorJob,
    Question,
    SourceChanges,
    SourcePage,
    SourceRegistration,
    SourceView,
    SynchronizationHistory,
    SynchronizationJobReport,
)
from knowledge_service.errors import (
    FailureKind,
    classify_failure,
    policy_for,
)
from knowledge_service.identifiers import ConversationId, JobId, SourceId, UserId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.job_conditions import allowed_decisions, condition_of
from knowledge_service.job_decisions import (
    JobIntentAlreadyOpen,
    JobNotFound,
    decision_cursor,
    decision_position,
)
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import (
    IllegalJobTransition,
    Job,
    JobKind,
    JobNotRetryable,
    JobStillRunning,
)
from knowledge_service.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_CURSOR_CHARACTERS,
    MAX_PAGE_SIZE,
    InvalidPage,
    page_size,
)
from knowledge_service.persistence.sources import SourceRecord
from knowledge_service.roles import Operation, PermissionDenied, require
from knowledge_service.source_administration import (
    InvalidSourceSettings,
    SourceDefinition,
    SourceDisabled,
    SourceExists,
    SourceNeedsRepair,
    SourceNotFound,
    source_cursor,
    source_position,
)
from knowledge_service.synchronization_administration import (
    SynchronizationStart,
    history_cursor,
    history_position,
)

logger = logging.getLogger(__name__)

ReadinessCheck = Callable[[], Awaitable[bool]]
# A Question is at most 4,000 characters; even escaped as JSON it fits.
MAX_BODY_BYTES = 65_536
# The one spelling of an ID the API accepts: `UUID()` alone also reads braces,
# `urn:uuid:`, a missing dash, a sign, or non-ASCII digits.
CANONICAL_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)


# A path ID, documented as a UUID but read as text: the route checks its
# spelling itself, after the caller's role, so it never answers first.
Identifier = Annotated[str, Path(json_schema_extra={"format": "uuid"})]


class RequestAuthenticator(Protocol):
    """Gives a request's caller from its `Authorization` headers.

    `Authenticator` is the implementation; it raises `Unauthenticated`, or
    `IdentityProviderUnavailable` when tokens cannot be checked.
    """

    async def authenticate(self, headers: list[str]) -> Caller: ...


class ConversationStore(Protocol):
    """The caller's own Conversations.

    `DatabaseConversationStore` is the implementation. `delete` returns
    nothing whether or not there was a Conversation of the owner's to delete.
    """

    async def delete(
        self, *, owner_id: UserId, conversation_id: ConversationId, request_id: UUID
    ) -> None: ...


class JobReports(Protocol):
    """Jobs and the progress of their newest attempt.

    `DatabaseJobReports` is the implementation. `load` returns None for a
    Job that does not exist or is not one of `kinds`.
    """

    async def load(
        self, *, job_id: JobId, kinds: frozenset[JobKind]
    ) -> JobReport | None: ...


class JobDecisions(Protocol):
    """An operator's decisions about Jobs that recovery cannot move on.

    `DatabaseJobDecisions` is the implementation. `retry` and `cancel`
    raise `JobNotFound` for a missing Job, and a domain error for a Job the
    decision does not fit.
    """

    async def needing_decision(
        self, *, limit: int, after: tuple[datetime, JobId] | None = None
    ) -> tuple[Job, ...]: ...

    async def retry(
        self, *, job_id: JobId, operator: UserId, request_id: UUID
    ) -> Job: ...

    async def cancel(
        self, *, job_id: JobId, operator: UserId, request_id: UUID
    ) -> Job: ...


class SourceAdministration(Protocol):
    """Sources as an operator registers and changes them.

    `DatabaseSourceAdministration` is the implementation. `register` raises
    `SourceExists` for a kind and location already registered; `change` and
    `set_enabled` raise `SourceNotFound` for a missing Source, and `change`
    raises `InvalidSourceSettings` for a result that breaks a rule.
    """

    async def page(
        self, *, limit: int, after: tuple[datetime, SourceId] | None = None
    ) -> tuple[SourceRecord, ...]: ...

    async def load(self, *, source_id: SourceId) -> SourceRecord | None: ...

    async def register(
        self, *, definition: SourceDefinition, operator: UserId, request_id: UUID
    ) -> SourceRecord: ...

    async def change(
        self,
        *,
        source_id: SourceId,
        changes: Mapping[str, object],
        operator: UserId,
        request_id: UUID,
    ) -> SourceRecord: ...

    async def set_enabled(
        self, *, source_id: SourceId, enabled: bool, operator: UserId, request_id: UUID
    ) -> SourceRecord: ...


class Synchronizations(Protocol):
    """A Source's synchronizations as an operator starts and follows them.

    `DatabaseSynchronizations` is the implementation. Both raise
    `SourceNotFound` for a missing Source; `start` raises `SourceDisabled`
    for one turned off, and returns the open Job when there is one.
    """

    async def start(
        self, *, source_id: SourceId, operator: UserId, request_id: UUID
    ) -> SynchronizationStart: ...

    async def history(
        self, *, source_id: SourceId, limit: int, after: JobId | None = None
    ) -> tuple[JobReport, ...]: ...


def source_view(record: SourceRecord) -> SourceView:
    """The API view of a stored Source."""
    return SourceView(
        source_id=record.source_id.value,
        kind=record.kind,
        location=record.location,
        display_name=record.display_name,
        credentials_reference=record.credentials_reference,
        enabled=record.enabled,
        default_public=record.default_public,
        default_principals=list(record.default_principals),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def operator_job(job: Job, *, now: datetime) -> OperatorJob:
    """The API view of a Job for an operator, judged at `now`."""
    return OperatorJob(
        job_id=job.job_id.value,
        kind=job.kind.value,
        target_id=job.target_id,
        status=job.status.value,
        condition=condition_of(job, now=now).value,
        failed_attempts=job.attempt,
        max_attempts=job.max_attempts,
        queued_at=job.queued_at,
        finished_at=job.finished_at,
        error_code=job.last_error_code,
        decisions=[decision.value for decision in allowed_decisions(job)],
    )


# The Jobs the job endpoint shows: those a Synchronization Run starter owns.
VISIBLE_JOB_KINDS = frozenset({JobKind.SYNCHRONIZE_SOURCE})


def synchronization_job_report(report: JobReport) -> SynchronizationJobReport:
    """The API view of a synchronization Job and its newest attempt."""
    job, latest = report.job, report.latest_attempt
    return SynchronizationJobReport(
        job_id=job.job_id.value,
        source_id=job.target_id,
        status=job.status.value,
        failed_attempts=job.attempt,
        max_attempts=job.max_attempts,
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_code=job.last_error_code,
        retry_at=job.retry_at,
        latest_attempt=None
        if latest is None
        else JobAttemptProgress(
            attempt=latest.attempt,
            stage=latest.stage.value,
            processed=latest.counters.processed,
            changed=latest.counters.changed,
            skipped=latest.counters.skipped,
            warnings=sorted(warning.value for warning in latest.warnings),
            started_at=latest.started_at,
            heartbeat_at=latest.heartbeat_at,
            outcome=None if latest.outcome is None else latest.outcome.value,
            finished_at=latest.finished_at,
        ),
    )


class _NotAuthenticated(Exception):
    def __init__(self, error: AuthenticationError) -> None:
        self.error = error


class _CannotAuthenticate(Exception):
    pass


class _BodyTooLarge(Exception):
    pass


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


@dataclass(frozen=True, slots=True)
class Refusal:
    """What a refused request is told, whichever error shape carries it."""

    status_code: int
    code: str
    message: str
    headers: dict[str, str] = field(default_factory=dict[str, str])


def failure_refusal(kind: FailureKind) -> Refusal:
    """The refusal for `kind`: a response not yet started has no stream."""
    policy = policy_for(kind)
    status_code = policy.status_code
    public_code = policy.public_code
    client_message = policy.client_message
    if status_code is None or public_code is None or client_message is None:
        raise RuntimeError("Failure policy has no public HTTP response")
    # An outage or a limit says when to send the request again.
    retry_after = stream_failure(kind, StreamPhase.BEFORE_HEADERS).retry_after_seconds
    headers = {} if retry_after is None else {"Retry-After": str(retry_after)}
    return Refusal(status_code, public_code, client_message, headers)


def not_authenticated_refusal(error: AuthenticationError) -> Refusal:
    """A missing, invalid, or malformed bearer token, with its RFC 6750 challenge."""
    malformed = error is AuthenticationError.INVALID_REQUEST
    # RFC 6750 §3: no error code when no Bearer token was sent.
    challenge = f'Bearer error="{error}"' if error else "Bearer"
    if malformed:
        return Refusal(
            400,
            "invalid_request",
            "The Authorization header is malformed",
            {"WWW-Authenticate": challenge},
        )
    return Refusal(
        401,
        "unauthenticated",
        "A valid access token is required",
        {"WWW-Authenticate": challenge},
    )


# Not an RFC 6750 challenge: another token for the same User has the same
# roles, so there is nothing for the client to retry.
FORBIDDEN = Refusal(403, "forbidden", "Your role does not permit this operation")


def native_error(refusal: Refusal, request_id: UUID) -> JSONResponse:
    """`refusal` in the native API's `ErrorResponse` shape."""
    response = error_response(
        status_code=refusal.status_code,
        code=refusal.code,
        message=refusal.message,
        request_id=request_id,
    )
    response.headers.update(refusal.headers)
    return response


def failure_response(
    *,
    kind: FailureKind,
    request_id: UUID,
) -> JSONResponse:
    """The native JSON error for `kind`."""
    return native_error(failure_refusal(kind), request_id)


# Where the OpenAI-compatible adapter lives; a client's base URL ends here.
COMPATIBLE_PREFIX = "/openai/v1"


def is_compatible(request: Request) -> bool:
    """Whether a request is for the OpenAI-compatible adapter.

    The path is read without `root_path`, as routing reads it, so a service
    behind a path prefix such as `/api` still answers in OpenAI's shape.
    """
    path = request.url.path
    root_path: str = request.scope.get("root_path", "")
    if root_path and path.startswith(root_path):
        path = path[len(root_path) :]
    return path == COMPATIBLE_PREFIX or path.startswith(f"{COMPATIBLE_PREFIX}/")


def compatible_refusal(refusal: Refusal, request: Request) -> JSONResponse:
    """`refusal` in OpenAI's error shape.

    OpenAI's body has no field for a request ID, so it travels as the
    `X-Request-ID` header, for matching a client's error with the logs.
    """
    return compatible_error(
        status_code=refusal.status_code,
        code=refusal.code,
        message=refusal.message,
        headers={**refusal.headers, "X-Request-ID": str(request_id_from(request))},
    )


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
    streamer: AnswerStreamer | None = None,
    authenticator: RequestAuthenticator | None = None,
    conversations: ConversationStore | None = None,
    job_reports: JobReports | None = None,
    job_decisions: JobDecisions | None = None,
    sources: SourceAdministration | None = None,
    synchronizations: Synchronizations | None = None,
    compatible_model: CompatibleModel | None = None,
) -> FastAPI:
    """The HTTP application.

    Every `/v1` endpoint needs a bearer token and the role that permits its
    operation: without an `authenticator` they answer 503, and the reader or
    owner is only ever the one in the context it returns.
    """
    # No redirect from `/v1/x/` to `/v1/x`: a redirect answers before identity.
    app = FastAPI(title="knowledge-service", redirect_slashes=False)
    offered_model = compatible_model or CompatibleModel()

    async def admit(request: Request, operation: Operation) -> tuple[Caller, Question]:
        """The caller, their permission, then the Question, in that order.

        The body is not read until the caller is known and permitted.
        """
        caller = await authenticate(request)
        require(caller.roles, operation)
        return caller, await read_question(request)

    async def authenticate(request: Request) -> Caller:
        if authenticator is None:
            raise _CannotAuthenticate
        # Every Authorization header, whatever the case of its name.
        headers = [
            value.decode("latin-1")
            for name, value in request.headers.raw
            if name.lower() == b"authorization"
        ]
        try:
            caller = await authenticator.authenticate(headers)
        except Unauthenticated as refused:
            logger.info("request not authenticated: %s", refused.reason)
            error = refused.error
        except IdentityProviderUnavailable:
            logger.warning("request not authenticated: identity provider unavailable")
            raise _CannotAuthenticate from None
        except Exception as unexpected:
            # Only the type: the message may quote the token or its claims.
            logger.error("authentication failed: %s", type(unexpected).__name__)
            raise _CannotAuthenticate from None
        else:
            if type(caller) is not Caller:
                logger.error("authentication failed: not a Caller")
                raise _CannotAuthenticate
            return caller
        raise _NotAuthenticated(error)

    @app.exception_handler(_NotAuthenticated)
    async def not_authenticated_handler(
        request: Request, refused: _NotAuthenticated
    ) -> JSONResponse:
        return native_error(
            not_authenticated_refusal(refused.error), request_id_from(request)
        )

    @app.exception_handler(PermissionDenied)
    async def permission_denied_handler(
        request: Request, denied: PermissionDenied
    ) -> JSONResponse:
        logger.info("request not permitted: %s", denied.operation)
        return native_error(FORBIDDEN, request_id_from(request))

    @app.exception_handler(_CannotAuthenticate)
    async def cannot_authenticate_handler(
        request: Request, _error: _CannotAuthenticate
    ) -> JSONResponse:
        return failure_response(
            kind=FailureKind.DEPENDENCY, request_id=request_id_from(request)
        )

    @app.exception_handler(_BodyTooLarge)
    async def body_too_large_handler(
        request: Request, _error: _BodyTooLarge
    ) -> JSONResponse:
        return error_response(
            status_code=413,
            code="request_too_large",
            message="The request body is too large",
            request_id=request_id_from(request),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        # An unknown path or method: the same error shape as every other
        # route under that prefix, OpenAI's for the compatible adapter.
        code, message = HTTP_ERRORS.get(
            error.status_code, ("http_error", "The request cannot be served")
        )
        refusal = Refusal(error.status_code, code, message, dict(error.headers or {}))
        if is_compatible(request):
            return compatible_refusal(refusal, request)
        return native_error(refusal, request_id_from(request))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        _error: RequestValidationError,
    ) -> JSONResponse:
        return failure_response(
            kind=FailureKind.VALIDATION, request_id=request_id_from(request)
        )

    # The OpenAI-compatible adapter (ADR 0004): OpenAI's shapes, so it stays
    # out of the native OpenAPI document.
    @app.get(
        f"{COMPATIBLE_PREFIX}/models", response_model=ModelList, include_in_schema=False
    )
    async def list_compatible_models(request: Request) -> ModelList | JSONResponse:
        # Who, then whether they may: the same checks as the native routes,
        # answered in OpenAI's error shape.
        try:
            caller = await authenticate(request)
            require(caller.roles, Operation.ASK_QUESTIONS)
        except _NotAuthenticated as refused:
            refusal = not_authenticated_refusal(refused.error)
        except _CannotAuthenticate:
            refusal = failure_refusal(FailureKind.DEPENDENCY)
        except PermissionDenied as denied:
            logger.info("request not permitted: %s", denied.operation)
            refusal = FORBIDDEN
        else:
            return model_list(offered_model)
        return compatible_refusal(refusal, request)

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

    @app.post(
        "/v1/questions",
        responses={
            200: {
                "description": "The Answer as Server-Sent Events",
                "content": {"text/event-stream": {}},
            },
            **error_responses(413, 429),
        },
        openapi_extra=body_schema(Question),
    )
    async def ask_question(request: Request) -> Response:
        caller, question = await admit(request, Operation.ASK_QUESTIONS)
        request_id = request_id_from(request)
        if streamer is None:
            return error_response(
                status_code=503,
                code="answering_unavailable",
                message="Answering is unavailable",
                request_id=request_id,
            )
        try:
            events = await _unless_client_leaves(
                request,
                streamer.open(question, reader=caller.reader, request_id=request_id),
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

    @app.delete(
        "/v1/conversations/{conversation_id}",
        status_code=204,
        responses=error_responses(),
    )
    async def delete_own_conversation(
        request: Request, conversation_id: Identifier
    ) -> Response:
        # Who, then whether they may, then which Conversation: the ID is not
        # even parsed for a caller who may not delete Conversations.
        caller = await authenticate(request)
        require(caller.roles, Operation.MANAGE_OWN_CONVERSATIONS)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(conversation_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        target = ConversationId.parse(conversation_id)
        if conversations is None:
            return error_response(
                status_code=503,
                code="conversations_unavailable",
                message="Conversations are unavailable",
                request_id=request_id,
            )
        try:
            # The owner is the caller; nothing in the request can name another.
            await conversations.delete(
                owner_id=caller.reader.user_id,
                conversation_id=target,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Only the type: the message may name the Conversation or its text.
            logger.error("conversation delete failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        # The same whether it was deleted, missing, or another User's.
        return Response(status_code=204)

    @app.get(
        "/v1/jobs/{job_id}",
        response_model=SynchronizationJobReport,
        responses=error_responses(404),
    )
    async def read_synchronization_job(
        request: Request, job_id: Identifier
    ) -> SynchronizationJobReport | JSONResponse:
        # Who, then whether they may, then which Job.
        caller = await authenticate(request)
        require(caller.roles, Operation.START_SYNCHRONIZATION_RUNS)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(job_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if job_reports is None:
            return error_response(
                status_code=503,
                code="jobs_unavailable",
                message="Jobs are unavailable",
                request_id=request_id,
            )
        try:
            report = await job_reports.load(
                job_id=JobId.parse(job_id), kinds=VISIBLE_JOB_KINDS
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("job read failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        if report is None or report.job.kind not in VISIBLE_JOB_KINDS:
            # The same for a missing Job and for a Job of another kind.
            return error_response(
                status_code=404,
                code="job_not_found",
                message="No such synchronization job",
                request_id=request_id,
            )
        return synchronization_job_report(report)

    @app.get(
        "/v1/job-decisions",
        response_model=JobsNeedingDecision,
        responses=error_responses(),
        openapi_extra={"parameters": PAGE_PARAMETERS},
    )
    async def list_jobs_needing_decision(
        request: Request,
    ) -> JobsNeedingDecision | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.RECOVER_JOBS)
        request_id = request_id_from(request)
        try:
            limit, cursor = page_request(request)
            after = None if cursor is None else decision_position(cursor)
        except InvalidPage:
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if job_decisions is None:
            return _jobs_unavailable(request_id)
        try:
            # One more than the page, to learn whether another page follows.
            jobs = await job_decisions.needing_decision(limit=limit + 1, after=after)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("job decision list failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        page = jobs[:limit]
        now = datetime.now(UTC)
        return JobsNeedingDecision(
            items=[operator_job(job, now=now) for job in page],
            next_cursor=decision_cursor(page[-1]) if len(jobs) > limit else None,
        )

    @app.post(
        "/v1/jobs/{job_id}/retry",
        response_model=OperatorJob,
        responses=error_responses(404, 409),
    )
    async def retry_failed_job(
        request: Request, job_id: Identifier
    ) -> OperatorJob | JSONResponse:
        return await decide(request, job_id, retry=True)

    @app.post(
        "/v1/jobs/{job_id}/cancel",
        response_model=OperatorJob,
        responses=error_responses(404, 409),
    )
    async def cancel_unfinished_job(
        request: Request, job_id: Identifier
    ) -> OperatorJob | JSONResponse:
        return await decide(request, job_id, retry=False)

    async def decide(
        request: Request, job_id: str, *, retry: bool
    ) -> OperatorJob | JSONResponse:
        # Who, then whether they may, then which Job.
        caller = await authenticate(request)
        require(caller.roles, Operation.RECOVER_JOBS)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(job_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if job_decisions is None:
            return _jobs_unavailable(request_id)
        decision = job_decisions.retry if retry else job_decisions.cancel
        try:
            decided = await decision(
                job_id=JobId.parse(job_id),
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except JobNotFound:
            return error_response(
                status_code=404,
                code="job_not_found",
                message="No such job",
                request_id=request_id,
            )
        except JobIntentAlreadyOpen:
            return _conflict(
                "job_already_open", "Another job for this work is open", request_id
            )
        except JobStillRunning:
            return _conflict("job_running", "The job is still running", request_id)
        except SourceDisabled:
            return _source_disabled(request_id)
        except JobNotRetryable, IllegalJobTransition:
            return _conflict(
                "decision_not_allowed",
                "This decision does not fit the job's status",
                request_id,
            )
        except Exception as error:
            logger.error("job decision failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        return operator_job(decided, now=datetime.now(UTC))

    @app.get(
        "/v1/sources",
        response_model=SourcePage,
        responses=error_responses(),
        openapi_extra={"parameters": PAGE_PARAMETERS},
    )
    async def list_sources(request: Request) -> SourcePage | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        request_id = request_id_from(request)
        try:
            limit, cursor = page_request(request)
            after = None if cursor is None else source_position(cursor)
        except InvalidPage:
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            # One more than the page, to learn whether another page follows.
            records = await sources.page(limit=limit + 1, after=after)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("source list failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        page = records[:limit]
        return SourcePage(
            items=[source_view(record) for record in page],
            next_cursor=source_cursor(page[-1]) if len(records) > limit else None,
        )

    @app.post(
        "/v1/sources",
        status_code=201,
        response_model=SourceView,
        responses=error_responses(409, 413),
        openapi_extra=body_schema(SourceRegistration),
    )
    async def register_source(request: Request) -> JSONResponse:
        # Who, then whether they may, then what they send.
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        registration = cast(
            SourceRegistration, await read_body(request, SourceRegistration)
        )
        request_id = request_id_from(request)
        try:
            definition = SourceDefinition.of(**registration.model_dump())
        except InvalidSourceSettings as invalid:
            return _invalid_source(invalid, request_id)
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            record = await sources.register(
                definition=definition,
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except SourceExists:
            return _conflict(
                "source_exists",
                "A Source of this kind and location is registered",
                request_id,
            )
        except Exception as error:
            logger.error("source registration failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        return JSONResponse(
            status_code=201,
            content=source_view(record).model_dump(mode="json"),
            headers={"Location": f"/v1/sources/{record.source_id.value}"},
        )

    @app.get(
        "/v1/sources/{source_id}",
        response_model=SourceView,
        responses=error_responses(404),
    )
    async def read_source(
        request: Request, source_id: Identifier
    ) -> SourceView | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            record = await sources.load(source_id=SourceId.parse(source_id))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("source read failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        if record is None:
            return _source_not_found(request_id)
        return source_view(record)

    @app.patch(
        "/v1/sources/{source_id}",
        response_model=SourceView,
        responses=error_responses(404, 409, 413),
        openapi_extra=body_schema(SourceChanges),
    )
    async def change_source(
        request: Request, source_id: Identifier
    ) -> SourceView | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        requested = cast(SourceChanges, await read_body(request, SourceChanges))
        # Only the fields the body names: one left out keeps its value.
        changes = {
            name: getattr(requested, name) for name in requested.model_fields_set
        }
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            record = await sources.change(
                source_id=SourceId.parse(source_id),
                changes=changes,
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except SourceNotFound:
            return _source_not_found(request_id)
        except SourceNeedsRepair:
            return _conflict(
                "source_needs_repair",
                "The stored Source breaks a rule; register it again",
                request_id,
            )
        except InvalidSourceSettings as invalid:
            return _invalid_source(invalid, request_id)
        except Exception as error:
            logger.error("source change failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        return source_view(record)

    @app.post(
        "/v1/sources/{source_id}/enable",
        response_model=SourceView,
        responses=error_responses(404),
    )
    async def enable_source(
        request: Request, source_id: Identifier
    ) -> SourceView | JSONResponse:
        return await switch(request, source_id, enabled=True)

    @app.post(
        "/v1/sources/{source_id}/disable",
        response_model=SourceView,
        responses=error_responses(404),
    )
    async def disable_source(
        request: Request, source_id: Identifier
    ) -> SourceView | JSONResponse:
        return await switch(request, source_id, enabled=False)

    async def switch(
        request: Request, source_id: str, *, enabled: bool
    ) -> SourceView | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            record = await sources.set_enabled(
                source_id=SourceId.parse(source_id),
                enabled=enabled,
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except SourceNotFound:
            return _source_not_found(request_id)
        except Exception as error:
            logger.error("source switch failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        return source_view(record)

    @app.post(
        "/v1/sources/{source_id}/synchronizations",
        status_code=202,
        response_model=SynchronizationJobReport,
        responses={
            200: {
                "model": SynchronizationJobReport,
                "description": "The Source's open Job; nothing new was queued",
            },
            **error_responses(404, 409),
        },
    )
    async def start_synchronization(
        request: Request, source_id: Identifier
    ) -> JSONResponse:
        # Who, then whether they may, then which Source.
        caller = await authenticate(request)
        require(caller.roles, Operation.START_SYNCHRONIZATION_RUNS)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if synchronizations is None:
            return _jobs_unavailable(request_id)
        try:
            started = await synchronizations.start(
                source_id=SourceId.parse(source_id),
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except SourceNotFound:
            return _source_not_found(request_id)
        except SourceDisabled:
            return _source_disabled(request_id)
        except Exception as error:
            logger.error("synchronization start failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        job_id = started.report.job.job_id.value
        # 202: queued now; 200: the open Job, so a repeated start is harmless.
        return JSONResponse(
            status_code=202 if started.queued else 200,
            content=synchronization_job_report(started.report).model_dump(mode="json"),
            headers={"Location": f"/v1/jobs/{job_id}"},
        )

    @app.get(
        "/v1/sources/{source_id}/synchronizations",
        response_model=SynchronizationHistory,
        responses=error_responses(404),
        openapi_extra={"parameters": PAGE_PARAMETERS},
    )
    async def list_synchronizations(
        request: Request, source_id: Identifier
    ) -> SynchronizationHistory | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.START_SYNCHRONIZATION_RUNS)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        try:
            limit, cursor = page_request(request)
            after = None if cursor is None else history_position(cursor)
        except InvalidPage:
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if synchronizations is None:
            return _jobs_unavailable(request_id)
        try:
            # One more than the page, to learn whether another page follows.
            reports = await synchronizations.history(
                source_id=SourceId.parse(source_id), limit=limit + 1, after=after
            )
        except asyncio.CancelledError:
            raise
        except SourceNotFound:
            return _source_not_found(request_id)
        except Exception as error:
            logger.error("synchronization history failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        page = reports[:limit]
        return SynchronizationHistory(
            items=[synchronization_job_report(report) for report in page],
            next_cursor=history_cursor(page[-1].job) if len(reports) > limit else None,
        )

    return app


# What each error status means; every `/v1` route can answer the first six.
ERROR_STATUSES: dict[int, str] = {
    400: "The Authorization header is malformed",
    401: "No valid access token",
    403: "No role of the caller permits this operation",
    422: "The request is not valid",
    500: "An internal error",
    503: "A service it needs is unavailable",
    404: "Nothing with this ID",
    409: "The request does not fit the resource's current state",
    413: "The request body is too large",
    429: "Too many requests, or the service is at capacity",
}
COMMON_ERRORS = (400, 401, 403, 422, 500, 503)
# The error code and message for a path or method the API does not have.
HTTP_ERRORS: dict[int, tuple[str, str]] = {
    404: ("not_found", "No such resource"),
    405: ("method_not_allowed", "This resource does not allow this method"),
}

# `limit` and `cursor`, described by hand: the route reads them itself, after
# the caller's role, so a bad one never answers 422 before the 401 or 403.
PAGE_PARAMETERS: list[dict[str, Any]] = [
    {
        "name": "limit",
        "in": "query",
        "required": False,
        "description": "How many items to return",
        "schema": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_PAGE_SIZE,
            "default": DEFAULT_PAGE_SIZE,
        },
    },
    {
        "name": "cursor",
        "in": "query",
        "required": False,
        "description": "`next_cursor` of the page before; absent for the first page",
        "schema": {"type": "string", "maxLength": MAX_CURSOR_CHARACTERS},
    },
]


def error_responses(*extra: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI responses for every error status a route may answer."""
    return {
        status: {"model": ErrorResponse, "description": ERROR_STATUSES[status]}
        for status in (*COMMON_ERRORS, *extra)
    }


def page_request(request: Request) -> tuple[int, str | None]:
    """`limit` and `cursor`, each at most once and nothing else, else `InvalidPage`.

    A misspelled parameter, such as `Limit`, is refused rather than ignored.
    """
    if set(request.query_params) - {"limit", "cursor"}:
        raise InvalidPage("only limit and cursor are accepted")
    for name in ("limit", "cursor"):
        if len(request.query_params.getlist(name)) > 1:
            raise InvalidPage(f"{name} is given more than once")
    return (
        page_size(request.query_params.get("limit")),
        request.query_params.get("cursor"),
    )


def _jobs_unavailable(request_id: UUID) -> JSONResponse:
    return error_response(
        status_code=503,
        code="jobs_unavailable",
        message="Jobs are unavailable",
        request_id=request_id,
    )


def _sources_unavailable(request_id: UUID) -> JSONResponse:
    return error_response(
        status_code=503,
        code="sources_unavailable",
        message="Sources are unavailable",
        request_id=request_id,
    )


def _source_not_found(request_id: UUID) -> JSONResponse:
    return error_response(
        status_code=404,
        code="source_not_found",
        message="No such Source",
        request_id=request_id,
    )


def _invalid_source(invalid: InvalidSourceSettings, request_id: UUID) -> JSONResponse:
    # The message names a rule and never a value, so it is safe to send.
    return error_response(
        status_code=422,
        code="invalid_source_settings",
        message=str(invalid),
        request_id=request_id,
    )


def _source_disabled(request_id: UUID) -> JSONResponse:
    return _conflict("source_disabled", "The Source is missing or disabled", request_id)


def _conflict(code: str, message: str, request_id: UUID) -> JSONResponse:
    return error_response(
        status_code=409, code=code, message=message, request_id=request_id
    )


def body_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The OpenAPI request body of a route that reads its body itself."""
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        }
    }


async def read_question(request: Request) -> Question:
    """The body as a Question, read only up to `MAX_BODY_BYTES`."""
    return cast(Question, await read_body(request, Question))


async def read_body(request: Request, model: type[BaseModel]) -> BaseModel:
    """The body as a `model`, read only up to `MAX_BODY_BYTES`."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _BodyTooLarge
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise _BodyTooLarge
    try:
        return model.model_validate_json(bytes(body))
    except ValidationError as invalid:
        raise RequestValidationError(invalid.errors()) from None
