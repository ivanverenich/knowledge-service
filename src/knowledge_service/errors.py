import asyncio
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import exc as database_errors

from knowledge_service.embeddings.port import (
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
)
from knowledge_service.model import (
    ChatModelError,
    ChatModelRateLimited,
    ChatModelRequestRefused,
)


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


# SQLSTATE classes of a database that is unreachable or overloaded, or
# that gave up on a statement which may work again: a lost connection
# (08), a serialization failure or deadlock (40), too many connections or
# no space (53), and a cancelled statement or a shutdown (57).
_UNAVAILABLE_STATES = frozenset({"08", "40", "53", "57"})


def _database_unavailable(error: BaseException) -> bool:
    """True when sending the request again later may work."""
    # The pool had no free connection in time.
    if isinstance(error, database_errors.TimeoutError):
        return True
    if not isinstance(error, database_errors.DBAPIError):
        return False
    if error.connection_invalidated:
        return True
    if not isinstance(error, database_errors.OperationalError):
        return False
    # No SQLSTATE means the server never answered, such as a refused connection.
    state = getattr(error.orig, "sqlstate", None)
    return state is None or str(state)[:2] in _UNAVAILABLE_STATES


def classify_failure(error: BaseException) -> FailureKind:
    if isinstance(error, asyncio.CancelledError):
        return FailureKind.CANCELLATION
    if isinstance(error, AuthorizationDenied):
        return FailureKind.AUTHORIZATION
    if isinstance(error, EmbeddingInputRejected):
        return FailureKind.VALIDATION
    if isinstance(error, (ChatModelRequestRefused, EmbeddingRequestRefused)):
        return FailureKind.INTERNAL
    if isinstance(error, (ChatModelRateLimited, EmbeddingRateLimited)):
        return FailureKind.CAPACITY
    if isinstance(error, (AnswerDeadlineExceeded, ChatModelError, EmbeddingError)):
        return FailureKind.DEPENDENCY
    # The database is down, unreachable, too slow, or out of connections.
    # Any other database error, such as a broken constraint, is a bug.
    if _database_unavailable(error):
        return FailureKind.DEPENDENCY
    return FailureKind.INTERNAL


def policy_for(kind: FailureKind) -> FailurePolicy:
    return POLICIES[kind]
