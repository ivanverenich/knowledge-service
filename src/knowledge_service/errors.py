import asyncio
from dataclasses import dataclass
from enum import StrEnum

from knowledge_service.model import ChatModelError, ChatModelRateLimited


class FailureKind(StrEnum):
    VALIDATION = "validation"
    AUTHORIZATION = "authorization"
    DEPENDENCY = "dependency"
    CAPACITY = "capacity"
    CANCELLATION = "cancellation"
    INTERNAL = "internal"


class RetryOwner(StrEnum):
    CALLER = "caller"
    SERVICE = "service"
    NONE = "none"


class TelemetryOutcome(StrEnum):
    REJECTED = "rejected"
    DENIED = "denied"
    DEPENDENCY_ERROR = "dependency_error"
    THROTTLED = "throttled"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class FailurePolicy:
    status_code: int | None
    public_code: str | None
    client_message: str | None
    retry_owner: RetryOwner
    telemetry_outcome: TelemetryOutcome


class AuthorizationDenied(Exception):
    """The caller may not perform the requested operation."""


class AnswerDeadlineExceeded(Exception):
    """The complete answer operation exceeded its deadline."""


POLICIES = {
    FailureKind.VALIDATION: FailurePolicy(
        422,
        "validation_error",
        "Request validation failed",
        RetryOwner.CALLER,
        TelemetryOutcome.REJECTED,
    ),
    FailureKind.AUTHORIZATION: FailurePolicy(
        403,
        "authorization_denied",
        "Access denied",
        RetryOwner.NONE,
        TelemetryOutcome.DENIED,
    ),
    FailureKind.DEPENDENCY: FailurePolicy(
        503,
        "dependency_unavailable",
        "A required service is unavailable",
        RetryOwner.SERVICE,
        TelemetryOutcome.DEPENDENCY_ERROR,
    ),
    FailureKind.CAPACITY: FailurePolicy(
        429,
        "capacity_exceeded",
        "Service capacity was exceeded",
        RetryOwner.CALLER,
        TelemetryOutcome.THROTTLED,
    ),
    FailureKind.CANCELLATION: FailurePolicy(
        None,
        None,
        None,
        RetryOwner.NONE,
        TelemetryOutcome.CANCELLED,
    ),
    FailureKind.INTERNAL: FailurePolicy(
        500,
        "internal_error",
        "An internal error occurred",
        RetryOwner.NONE,
        TelemetryOutcome.INTERNAL_ERROR,
    ),
}


def classify_failure(error: BaseException) -> FailureKind:
    if isinstance(error, asyncio.CancelledError):
        return FailureKind.CANCELLATION
    if isinstance(error, AuthorizationDenied):
        return FailureKind.AUTHORIZATION
    if isinstance(error, ChatModelRateLimited):
        return FailureKind.CAPACITY
    if isinstance(error, (AnswerDeadlineExceeded, ChatModelError)):
        return FailureKind.DEPENDENCY
    return FailureKind.INTERNAL


def policy_for(kind: FailureKind) -> FailurePolicy:
    return POLICIES[kind]
