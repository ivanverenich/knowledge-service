"""What a failed streamed Answer looks like, before and after the headers.

A streamed Answer can fail in two phases, and the client can be told
different things in each:

- **Before the headers.** Nothing has been sent, so a failure is still a
  plain HTTP error: a status, the public code and message of the M01
  error policy, and, for an outage or a limit, a `Retry-After` header that
  says when sending the Question again may work. The retry owner stays the
  M01 one: for an outage it is the service, which says when through
  `Retry-After`.
- **After the headers.** The status `200` and maybe some text are already
  out, so the status cannot change. The stream ends with one `error`
  event holding a public code, its fixed message, and who may retry. The
  service never retries a stream once text is out, because the text could
  differ, so an outage or a limit tells the caller to send the Question
  again. A validation failure here is the server's own, because the
  request was accepted, so it is reported as an internal error. Events
  of protocol version `2` carry this guidance; the reader still reads a
  version `1` error with the M01 retry owner it had then.

What every failure settles:

- **The partial text.** The client keeps streamed text only when the
  stream completes with an Answer. An abstention or an `error` replaces
  it with a fixed message; a client that leaves keeps an incomplete
  stream, which a reader refuses. Nothing streamed means nothing to keep.
- **The audit outcome.** Each stream ends with exactly one outcome from a
  closed list: answered, abstained, one of the M01 telemetry outcomes for
  a failure, or cancelled (`AuditOutcome`, shared with every audit event in
  `knowledge_service.audit`). It is recorded with the phase and what
  happened to the partial text, never a text.
- **Cancellation is not an error.** A client that left gets no status and
  no event; the outcome is `cancelled`.
"""

from dataclasses import dataclass
from enum import StrEnum

from knowledge_service.answering.abstention import Decision
from knowledge_service.audit import AuditOutcome
from knowledge_service.errors import FailureKind, RetryOwner, policy_for

# How long a client should wait before sending the Question again, when
# the failure is an outage or a limit.
RETRY_AFTER_SECONDS = 5


class StreamPhase(StrEnum):
    """Whether the response headers were already sent."""

    BEFORE_HEADERS = "before_headers"
    AFTER_HEADERS = "after_headers"


class PartialText(StrEnum):
    """What becomes of the text the client already holds."""

    NONE = "none"
    KEPT = "kept"
    REPLACED = "replaced"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class StreamFailure:
    """What the client is told about one failure, and what is recorded.

    `kind` is the kind the client sees: after the headers, a validation
    failure is reported as internal. `status_code` and `retry_after_seconds`
    exist only before the headers; `public_code` and `message` are None for
    a cancellation, which tells nobody anything. `partial_text` is what
    becomes of streamed text, when there is any.
    """

    kind: FailureKind
    phase: StreamPhase
    status_code: int | None
    public_code: str | None
    message: str | None
    retry: RetryOwner
    retry_after_seconds: int | None
    partial_text: PartialText
    audit_outcome: AuditOutcome


_RETRY_AFTER = frozenset({FailureKind.DEPENDENCY, FailureKind.CAPACITY})


def stream_failure(kind: FailureKind, phase: StreamPhase) -> StreamFailure:
    """The policy for one failure kind in one phase."""
    if type(kind) is not FailureKind or type(phase) is not StreamPhase:
        raise TypeError("kind must be a FailureKind and phase a StreamPhase")
    before = phase is StreamPhase.BEFORE_HEADERS
    reported = kind
    if not before and kind is FailureKind.VALIDATION:
        # The request was accepted; whatever failed later is the server's.
        reported = FailureKind.INTERNAL
    policy = policy_for(reported)
    cancelled = reported is FailureKind.CANCELLATION
    if before:
        retry = policy.retry_owner
        partial = PartialText.NONE
    else:
        retry = RetryOwner.CALLER if reported in _RETRY_AFTER else RetryOwner.NONE
        partial = PartialText.ABANDONED if cancelled else PartialText.REPLACED
    return StreamFailure(
        kind=reported,
        phase=phase,
        status_code=policy.status_code if before else None,
        public_code=policy.public_code,
        message=policy.client_message,
        retry=retry,
        retry_after_seconds=(
            RETRY_AFTER_SECONDS if before and reported in _RETRY_AFTER else None
        ),
        partial_text=partial,
        audit_outcome=AuditOutcome(policy.telemetry_outcome.value),
    )


def finished_outcome(decision: Decision) -> AuditOutcome:
    """The audit outcome of a stream that ended with a decision."""
    if decision is Decision.ANSWER:
        return AuditOutcome.ANSWERED
    return AuditOutcome.ABSTAINED
