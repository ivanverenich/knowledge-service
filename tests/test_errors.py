import asyncio

import pytest

from knowledge_service.errors import (
    AuthorizationDenied,
    FailureKind,
    RetryOwner,
    TelemetryOutcome,
    classify_failure,
    policy_for,
)
from knowledge_service.model import (
    ChatModelMalformedResponse,
    ChatModelRateLimited,
    ChatModelTimeout,
    ChatModelUnavailable,
)


@pytest.mark.parametrize(
    ("error", "expected_kind"),
    [
        (AuthorizationDenied(), FailureKind.AUTHORIZATION),
        (ChatModelUnavailable(), FailureKind.DEPENDENCY),
        (ChatModelMalformedResponse("bad response"), FailureKind.DEPENDENCY),
        (ChatModelTimeout(), FailureKind.DEPENDENCY),
        (ChatModelRateLimited(), FailureKind.CAPACITY),
        (asyncio.CancelledError(), FailureKind.CANCELLATION),
        (RuntimeError("unexpected"), FailureKind.INTERNAL),
    ],
)
def test_classifies_failures(
    error: BaseException,
    expected_kind: FailureKind,
) -> None:
    assert classify_failure(error) is expected_kind


@pytest.mark.parametrize(
    (
        "status_code",
        "public_code",
        "client_message",
        "kind",
        "retry_owner",
        "telemetry_outcome",
    ),
    [
        (
            422,
            "validation_error",
            "Request validation failed",
            FailureKind.VALIDATION,
            RetryOwner.CALLER,
            TelemetryOutcome.REJECTED,
        ),
        (
            403,
            "authorization_denied",
            "Access denied",
            FailureKind.AUTHORIZATION,
            RetryOwner.NONE,
            TelemetryOutcome.DENIED,
        ),
        (
            503,
            "dependency_unavailable",
            "A required service is unavailable",
            FailureKind.DEPENDENCY,
            RetryOwner.SERVICE,
            TelemetryOutcome.DEPENDENCY_ERROR,
        ),
        (
            429,
            "capacity_exceeded",
            "Service capacity was exceeded",
            FailureKind.CAPACITY,
            RetryOwner.CALLER,
            TelemetryOutcome.THROTTLED,
        ),
        (
            None,
            None,
            None,
            FailureKind.CANCELLATION,
            RetryOwner.NONE,
            TelemetryOutcome.CANCELLED,
        ),
        (
            500,
            "internal_error",
            "An internal error occurred",
            FailureKind.INTERNAL,
            RetryOwner.NONE,
            TelemetryOutcome.INTERNAL_ERROR,
        ),
    ],
)
def test_failure_policy_assigns_ownership_and_telemetry(
    status_code: int,
    public_code: str,
    client_message: str,
    kind: FailureKind,
    retry_owner: RetryOwner,
    telemetry_outcome: TelemetryOutcome,
) -> None:
    policy = policy_for(kind)

    assert policy.status_code == status_code
    assert policy.public_code == public_code
    assert policy.client_message == client_message
    assert policy.retry_owner is retry_owner
    assert policy.telemetry_outcome is telemetry_outcome
