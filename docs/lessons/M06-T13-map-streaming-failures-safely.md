# M06-T13 — Map streaming failures safely

Source task: [M06-T13](../curriculum/milestones/M06-answers-and-conversations/M06-T13-map-streaming-failures-safely.md)

## What you will do

This lesson writes down, in one place, what a streamed Answer does when something fails, and makes the code follow it. A stream has a turning point: the moment the response headers, with status `200`, go out. Before it, a failure can still be an ordinary HTTP error. After it, the status cannot change, so the stream must end with an `error` event instead, and the text already sent must be dealt with.

- A new module, `stream_failures`, holds the policy: for each kind of failure, what the client is told before the headers and after them.
- A database that is down or too slow becomes an outage (`503`), not a bug (`500`).
- The event writer and reader follow the after-headers policy, and the endpoint adds a `Retry-After` header to an outage or a limit.
- Every stream ends with one record of its phase, its audit outcome, and what became of its partial text.

| The task asks for | Where it happens |
|---|---|
| Behavior before and after response headers | Step 2: `stream_failure(kind, phase)` gives both columns of one table |
| Terminal SSE errors | Step 4: one `error` event, with the code, fixed message, and retry of the after-headers policy |
| Partial text policy | Step 2: `PartialText`; Step 4: kept only for a completed Answer, replaced by an abstention or an error, abandoned when the client leaves |
| Retry guidance | Steps 2 and 4: `Retry-After` before the headers for `503` and `429`; after them, the caller may send the Question again after an outage or a limit |
| Audit outcome | Steps 2 and 4: one `AuditOutcome` per stream in its record |
| Inject provider, database, validation, and cancellation failures at several stream points | Step 5: the search, before any text, in the middle of the text, after the text, as the headers go out, and after the terminal event |
| Telemetry holds no seeded sensitive text | Step 5: a planted marker in every error never reaches a response, a header, or a log record |

This lesson includes coding: Steps 2 to 5 write code. Steps 1, 6, and 7 do not. The integration tests need the test database.

Terms used in this lesson:

- **Response headers:** the first part of an HTTP response, with the status code. Once they are sent, the status is fixed; a streamed response sends them before its first event.
- **Phase:** here, whether a failure happens before or after the headers.
- **Terminal event:** the last event of a stream, `completion` or `error`. Exactly one ends every stream that is not cut off.
- **Partial text:** the Answer text a client already received when the stream fails.
- **Audit outcome:** one word from a closed list that says how a stream ended, such as `answered`, `throttled`, or `cancelled`, recorded for each stream.
- **`Retry-After`:** a standard HTTP header that tells a client how many seconds to wait before trying again.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **What already exists.** M01-T07 maps each error to a `FailureKind` with an HTTP status, a public code, a fixed message, and a retry owner. M06-T08 defines the events and a reader that rebuilds the Answer; an `error` already replaces streamed text with its fixed message. M06-T09 finds Evidence before the first byte, so its failures are HTTP errors, and ends every later failure with an `error` event. What is missing is one written policy that says how the two phases differ, and a few places where the code does not agree with it.
- **Two phases, one table.** `stream_failure(kind, phase)` returns everything about one failure: the status and `Retry-After` before the headers, the public code, message, and retry owner, what becomes of the partial text, and the audit outcome. The endpoint, the event writer, the event reader, and the stream's record all ask the same function, so they cannot drift apart.
- **Retry guidance after the headers.** M01 makes an outage the service's to retry. Once text is out, the service never retries, because a second model call could write a different text into the same stream. So after the headers an outage or a limit says the caller may send the Question again, and anything else says nobody should. Before the headers, an outage or a limit also gets `Retry-After: 5`.
- **Validation after the headers.** The request was already accepted, so a validation failure later, such as an input the support judge's embedding refuses, is the server's fault. It is reported as `internal_error`, never as "fix your request".
- **The database.** Until now any database error was a `500` with no retry. A database that is unreachable, out of connections, shut down, or too slow for its statement timeout is an outage, and sending the Question again later may work. Other database errors, such as a broken constraint or a value too large, are bugs. The rule reads the PostgreSQL error class (SQLSTATE): `08`, `40`, `53`, and `57` are outages, and so is a server that never answered.
- **Partial text.** The client keeps streamed text only when the stream completes with an Answer. An abstention or an `error` replaces it with a fixed message; a client that leaves holds a cut-off stream, which the reader refuses. The record says which: `kept`, `replaced`, `abandoned`, or `none` when nothing was streamed.
- **When the phase changes.** The headers go out when the server hands over the start of the response, which can happen before the first event. So the endpoint tells the stream when it hands them over, and the stream also marks it when its first event is asked for.
- **Recorded before it is handed over.** The end of a stream is recorded before its terminal event is handed to the server, and streamed text is counted before it is handed over. A client that reads the last event and then leaves is still recorded as answered.
- **What the review found.** An independent adversarial review injected failures and client departures at every point of a stream. Search failures gave the right status with `Retry-After` only for `503` and `429`; mid-stream failures ended in one `error` event; validation after the headers was reported as internal; no stream lacked a terminal event; and a marker planted in SQL, parameters, and driver messages never reached a response, a header, or a record. It found these problems, each fixed with a test that fails without the fix:
  - Every `OperationalError` was an outage, but PostgreSQL also uses it for errors a retry can never fix, such as a value too large for an index. Now only a server that never answered, a lost connection, and SQLSTATE classes `08`, `40`, `53`, and `57` are outages.
  - A client that left while the headers were being handed over was recorded as `before_headers`. Now the endpoint marks the headers as it hands them over.
  - Text sent at the end, when the model's JSON did not start with the answer, was counted only after it was handed over, so a client that left with it was recorded as holding nothing. Now it is counted before.
  - A client that read the terminal event and then left was recorded as `cancelled`. Now the end is recorded before the terminal event is handed over.
  - `/v1/answer` gave the same `503` and `429` without `Retry-After`. Now both endpoints use one `failure_response`.
  - Documented, not fixed: the reader's stricter retry check is a protocol change without a new version, because no client has shipped.
- **Where the code lives.** The new `answering/stream_failures.py`; small changes in `errors.py`, `answering/events.py`, `answering/streaming.py`, and `app.py`.
- **Known limits.** The event reader now refuses an error event with the old retry guidance, yet the protocol version stays `1`, because no client of it has shipped; after a release, such a change needs a new version. `Retry-After` is a fixed 5 seconds, not the provider's own hint. The audit outcome is recorded in the stream's log record, not as a stored Audit Event, because a stream has no verified actor until M07. A `503` for a missing streamer has no `Retry-After`, because waiting will not configure it.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before any code exists, write what must be true when the lesson is done. Step 6 checks the predictions.

Create `docs/evidence/M06-T13-map-streaming-failures-safely.md`:

```markdown
# M06-T13 — Map streaming failures safely evidence

## Invariant

`stream_failure(kind, phase)` is the one policy for a failed streamed
Answer. Before the response headers a failure is an HTTP error with the
M01 status, code, and message, and a `Retry-After` header for an outage or
a limit. After them it is exactly one terminal `error` event whose code,
message, and retry the reader checks against the same policy: an outage or
a limit is the caller's to retry, a validation failure is internal. A
database that is unreachable, overloaded, or too slow is an outage; any
other database error is internal. Every stream is recorded once with its
phase, one audit outcome, and what became of its partial text. No
response, header, or record holds a Question, an Answer, SQL, or a
connection string.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.stream_failures"`
fails with `ModuleNotFoundError`.

## Predictions

- What a client gets when PostgreSQL is unreachable while Evidence is found, before and after this change: ...
- What a client gets when the model fails after half of the Answer's text was streamed, and what it shows the user: ...
- What the stream's record says when the client reads the `completion` event and then leaves at once: ...
- How many integration tests fail if a database outage is classified as a bug again: ...

## Design

## Verification

## Decision

## Reflection
```

Check: the file exists, the failing check fails, and every prediction is a full sentence Step 6 can prove wrong.

### Step 2 — Coding step: the failure policy

This step writes the table every other part will follow.

**In plain words:** Given a kind of failure and whether the headers were already sent, this module says what the client sees: a status and a wait time before the headers, or the code, message, and retry advice of the last event after them. It also says what becomes of any text already sent and which single word the audit record gets.

| Part | What it holds or does |
|---|---|
| `StreamPhase` | `before_headers` or `after_headers` |
| `AuditOutcome` | `answered`, `abstained`, the six M01 telemetry outcomes, one of which is `cancelled` |
| `PartialText` | `none`, `kept`, `replaced`, `abandoned` |
| `StreamFailure` | The kind the client sees, the phase, status, code, message, retry owner, `Retry-After`, partial text, audit outcome |
| `stream_failure` | The policy for one kind in one phase |
| `finished_outcome` | `answered` or `abstained` for a stream that ended with a decision |

Create `src/knowledge_service/answering/stream_failures.py`:

```python
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
  request was accepted, so it is reported as an internal error. The
  event reader accepts only this guidance; no client of the version `1`
  events had shipped before it changed, so the version stays `1`.

What every failure settles:

- **The partial text.** The client keeps streamed text only when the
  stream completes with an Answer. An abstention or an `error` replaces
  it with a fixed message; a client that leaves keeps an incomplete
  stream, which a reader refuses. Nothing streamed means nothing to keep.
- **The audit outcome.** Each stream ends with exactly one outcome from a
  closed list: answered, abstained, one of the M01 telemetry outcomes for
  a failure, or cancelled. It is recorded with the phase and what happened
  to the partial text, never a text.
- **Cancellation is not an error.** A client that left gets no status and
  no event; the outcome is `cancelled`.
"""

from dataclasses import dataclass
from enum import StrEnum

from knowledge_service.answering.abstention import Decision
from knowledge_service.errors import FailureKind, RetryOwner, policy_for

# How long a client should wait before sending the Question again, when
# the failure is an outage or a limit.
RETRY_AFTER_SECONDS = 5


class StreamPhase(StrEnum):
    """Whether the response headers were already sent."""

    BEFORE_HEADERS = "before_headers"
    AFTER_HEADERS = "after_headers"


class AuditOutcome(StrEnum):
    """How one streamed Answer ended, for the audit trail."""

    ANSWERED = "answered"
    ABSTAINED = "abstained"
    REJECTED = "rejected"
    DENIED = "denied"
    DEPENDENCY_ERROR = "dependency_error"
    THROTTLED = "throttled"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


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
```

Check:

```bash
uv run python -c "import knowledge_service.answering.stream_failures"
uv run pyright src/knowledge_service/answering/stream_failures.py
```

Expected: the import prints nothing, and pyright reports 0 errors.

### Step 3 — Coding step: a database outage is a dependency failure

This step teaches the error taxonomy what a database outage looks like.

**In plain words:** When the database cannot be reached, is out of connections, was shut down, or stopped a statement that took too long, the service now says "a required service is unavailable, try again later" instead of "internal error". A database error that a retry can never fix, such as a broken rule in the data, stays an internal error.

| Error | Kind |
|---|---|
| No free connection in the pool in time (`TimeoutError`) | `dependency` |
| A connection SQLAlchemy marked as lost | `dependency` |
| `OperationalError` with no SQLSTATE, or class `08`, `40`, `53`, `57` | `dependency` |
| Any other database error | `internal`, as before |

In `src/knowledge_service/errors.py`:

**1.** Find:

```python
import asyncio
from dataclasses import dataclass
from enum import StrEnum

from knowledge_service.embeddings.port import (
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
```

Replace with:

```python
import asyncio
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import exc as database_errors

from knowledge_service.embeddings.port import (
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
```

**2.** Find:

```python
        RetryOwner.NONE,
        TelemetryOutcome.INTERNAL_ERROR,
    ),
}


def classify_failure(error: BaseException) -> FailureKind:
    if isinstance(error, asyncio.CancelledError):
        return FailureKind.CANCELLATION
    if isinstance(error, AuthorizationDenied):
        return FailureKind.AUTHORIZATION
    if isinstance(error, EmbeddingInputRejected):
```

Replace with:

```python
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
```

**3.** Find:

```python
    if isinstance(error, (ChatModelRequestRefused, EmbeddingRequestRefused)):
        return FailureKind.INTERNAL
    if isinstance(error, (ChatModelRateLimited, EmbeddingRateLimited)):
        return FailureKind.CAPACITY
    if isinstance(error, (AnswerDeadlineExceeded, ChatModelError, EmbeddingError)):
        return FailureKind.DEPENDENCY
    return FailureKind.INTERNAL


def policy_for(kind: FailureKind) -> FailurePolicy:
    return POLICIES[kind]
```

Replace with:

```python
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
```

Check:

```bash
uv run pytest tests/test_errors.py -q
```

Expected: the M01 tests still pass.

### Step 4 — Coding step: the events, the stream, and the endpoint follow the policy

This step makes the three places that report failures ask the policy.

**In plain words:** The `error` event now carries the after-headers code and retry advice, and the event reader accepts only that. The stream's final record gains the phase, the audit outcome, and what became of the partial text, and it is written before the last event is handed over. The endpoint adds `Retry-After` to an outage or a limit and tells the stream when its headers go out.

| File | What changes |
|---|---|
| `answering/events.py` | `error` and the reader's table of known errors use `stream_failure(kind, AFTER_HEADERS)` |
| `answering/streaming.py` | The record gains `phase`, `audit_outcome`, `partial_text`; end text is counted before it is sent; the terminal event is recorded before it is handed over; `AnswerStream.headers_sent` |
| `app.py` | `failure_response` adds `Retry-After`; `EventStreamResponse` calls `headers_sent` as it hands over the start of the response |

In `src/knowledge_service/answering/events.py`:

**1.** Find:

```python
    CitationPayload,
    citation_payload,
)
from knowledge_service.answering.evidence import MAX_EVIDENCE, EvidenceId
from knowledge_service.answering.generation import MAX_ANSWER_CHARACTERS
from knowledge_service.answering.prompt import has_visible_text
from knowledge_service.contracts import Usage
from knowledge_service.errors import FailureKind, RetryOwner, policy_for
from knowledge_service.language import Language

EVENT_PROTOCOL_VERSION = "1"
MAX_EVENT_CHARACTERS = 131_072
_ID = re.compile(r"id: (0|[1-9][0-9]{0,8})")
_STRICT = ConfigDict(extra="forbid", strict=True, frozen=True)
```

Replace with:

```python
    CitationPayload,
    citation_payload,
)
from knowledge_service.answering.evidence import MAX_EVIDENCE, EvidenceId
from knowledge_service.answering.generation import MAX_ANSWER_CHARACTERS
from knowledge_service.answering.prompt import has_visible_text
from knowledge_service.answering.stream_failures import StreamPhase, stream_failure
from knowledge_service.contracts import Usage
from knowledge_service.errors import FailureKind, RetryOwner
from knowledge_service.language import Language

EVENT_PROTOCOL_VERSION = "1"
MAX_EVENT_CHARACTERS = 131_072
_ID = re.compile(r"id: (0|[1-9][0-9]{0,8})")
_STRICT = ConfigDict(extra="forbid", strict=True, frozen=True)
```

**2.** Find:

```python

        return self._write(EventName.COMPLETION, build)

    def error(self, kind: FailureKind) -> bytes:
        if type(kind) is not FailureKind:
            raise InvalidEventStream(StreamDefect.MALFORMED)
        policy = policy_for(kind)
        # A cancelled request has nobody left to tell.
        if policy.public_code is None or policy.client_message is None:
            raise InvalidEventStream(StreamDefect.UNKNOWN_ERROR)
        code, message = policy.public_code, policy.client_message
        return self._write(
            EventName.ERROR,
            lambda: ErrorData(code=code, message=message, retry=policy.retry_owner),
        )

    def _write(self, name: EventName, build: Callable[[], EventData]) -> bytes:
        event, block = _built(self._rules.next_id, name, build)
        # Checked before the rules change, so a refused event leaves no trace.
        if len(block) > MAX_EVENT_CHARACTERS:
```

Replace with:

```python

        return self._write(EventName.COMPLETION, build)

    def error(self, kind: FailureKind) -> bytes:
        if type(kind) is not FailureKind:
            raise InvalidEventStream(StreamDefect.MALFORMED)
        # What a client is told once the headers are out (`stream_failures`).
        failure = stream_failure(kind, StreamPhase.AFTER_HEADERS)
        # A cancelled request has nobody left to tell.
        if failure.public_code is None or failure.message is None:
            raise InvalidEventStream(StreamDefect.UNKNOWN_ERROR)
        code, message = failure.public_code, failure.message
        return self._write(
            EventName.ERROR,
            lambda: ErrorData(code=code, message=message, retry=failure.retry),
        )

    def _write(self, name: EventName, build: Callable[[], EventData]) -> bytes:
        event, block = _built(self._rules.next_id, name, build)
        # Checked before the rules change, so a refused event leaves no trace.
        if len(block) > MAX_EVENT_CHARACTERS:
```

**3.** Find:

```python
class _PublicError:
    client_message: str
    retry_owner: RetryOwner


_PUBLIC_ERRORS: dict[str, _PublicError] = {
    policy.public_code: _PublicError(policy.client_message, policy.retry_owner)
    for policy in map(policy_for, FailureKind)
    if policy.public_code is not None and policy.client_message is not None
}


def _messages(reason: AbstentionReason) -> frozenset[str]:
    return frozenset(abstention_message(reason, language) for language in Language)

```

Replace with:

```python
class _PublicError:
    client_message: str
    retry_owner: RetryOwner


_PUBLIC_ERRORS: dict[str, _PublicError] = {
    failure.public_code: _PublicError(failure.message, failure.retry)
    for kind in FailureKind
    if (failure := stream_failure(kind, StreamPhase.AFTER_HEADERS)).public_code
    is not None
    and failure.message is not None
}


def _messages(reason: AbstentionReason) -> frozenset[str]:
    return frozenset(abstention_message(reason, language) for language in Language)

```

In `src/knowledge_service/answering/streaming.py`:

**1.** Find:

```python
  once, so the provider stops generating, and sends nothing more. The
  close is shielded from a second cancellation, so it is not cut short,
  and bounded by `CLOSE_SECONDS`, so a stuck close cannot hold it.
- **One record per stream.** "answer stream started" and "answer stream
  ended" hold the request ID, counts, tokens, timing, and how the stream
  ended: `completed`, `failed`, or `cancelled`, also when it ends before
  its first event. Never text.
"""

import asyncio
import contextlib
import logging
import re
```

Replace with:

```python
  once, so the provider stops generating, and sends nothing more. The
  close is shielded from a second cancellation, so it is not cut short,
  and bounded by `CLOSE_SECONDS`, so a stuck close cannot hold it.
- **One record per stream.** "answer stream started" and "answer stream
  ended" hold the request ID, counts, tokens, timing, and how the stream
  ended: `completed`, `failed`, or `cancelled`, also when it ends before
  its first event. The end also holds the phase it ended in, its audit
  outcome, and what became of the partial text, as `stream_failures`
  defines them. Never text.
"""

import asyncio
import contextlib
import logging
import re
```

**2.** Find:

```python
    ModelReply,
    positive_seconds,
    reply_result,
    well_formed_reply,
)
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.support import SupportJudge, check_support
from knowledge_service.contracts import Question, Usage
from knowledge_service.errors import (
    AnswerDeadlineExceeded,
    FailureKind,
    classify_failure,
```

Replace with:

```python
    ModelReply,
    positive_seconds,
    reply_result,
    well_formed_reply,
)
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.stream_failures import (
    AuditOutcome,
    PartialText,
    StreamPhase,
    finished_outcome,
    stream_failure,
)
from knowledge_service.answering.support import SupportJudge, check_support
from knowledge_service.contracts import Question, Usage
from knowledge_service.errors import (
    AnswerDeadlineExceeded,
    FailureKind,
    classify_failure,
```

**3.** Find:

```python
                else None
            )
        except asyncio.CancelledError:
            span.end()
            raise
        except Exception as error:
            span.failure = type(error).__name__
            span.outcome = "failed"
            span.end()
            raise
        plan = _Plan(
            request_id=request_id,
            deadline=deadline,
            language=_language(question.text),
```

Replace with:

```python
                else None
            )
        except asyncio.CancelledError:
            span.end()
            raise
        except Exception as error:
            failed = stream_failure(classify_failure(error), StreamPhase.BEFORE_HEADERS)
            span.failure = type(error).__name__
            span.outcome = "failed"
            span.audit_outcome = failed.audit_outcome
            span.end()
            raise
        plan = _Plan(
            request_id=request_id,
            deadline=deadline,
            language=_language(question.text),
```

**4.** Find:

```python
        return AnswerStream(self._events(plan, span), span)

    async def _events(self, plan: _Plan, span: _Span) -> AsyncGenerator[bytes]:
        writer = AnswerEventWriter(plan.request_id)
        finished: FinishedAnswer | None = None
        failure: FailureKind | None = None
        try:
            yield writer.metadata()
            context = plan.context
            try:
                if context is not None and context.evidence.items:
                    reply: ModelReply | None = None
```

Replace with:

```python
        return AnswerStream(self._events(plan, span), span)

    async def _events(self, plan: _Plan, span: _Span) -> AsyncGenerator[bytes]:
        writer = AnswerEventWriter(plan.request_id)
        finished: FinishedAnswer | None = None
        failure: FailureKind | None = None
        # The response sends its headers before it asks for the first event.
        span.phase = StreamPhase.AFTER_HEADERS
        try:
            yield writer.metadata()
            context = plan.context
            try:
                if context is not None and context.evidence.items:
                    reply: ModelReply | None = None
```

**5.** Find:

```python
                    decision is not None
                    and finished is not None
                    and decision.decision is Decision.ANSWER
                ):
                    rest = finished.result.text[span.characters :]
                    if rest:
                        yield writer.text_delta(rest)
                        span.sent(rest)
                    for citation in finished.result.citations:
                        yield writer.citation(citation)
                        span.citations += 1
                if span.usage is not None:
                    yield writer.usage(span.usage)
                if decision is not None:
                    yield writer.completion(decision, plan.language)
                    span.outcome = "completed"
            except InvalidEventStream as error:
                # A refused event changes nothing, so an error can still end it.
                decision = None
                failure = FailureKind.INTERNAL
                span.failure = f"{type(error).__name__}:{error.defect.value}"
            if decision is None:
                yield writer.error(failure or FailureKind.INTERNAL)
                span.outcome = "failed"
        finally:
            span.end()

    async def _finished(
        self,
        plan: _Plan,
```

Replace with:

```python
                    decision is not None
                    and finished is not None
                    and decision.decision is Decision.ANSWER
                ):
                    rest = finished.result.text[span.characters :]
                    if rest:
                        delta = writer.text_delta(rest)
                        # Counted before it is handed over, as in the loop above.
                        span.sent(rest)
                        yield delta
                    for citation in finished.result.citations:
                        yield writer.citation(citation)
                        span.citations += 1
                if span.usage is not None:
                    yield writer.usage(span.usage)
                if decision is not None:
                    completion = writer.completion(decision, plan.language)
                    # Recorded before it is handed over: a client that leaves
                    # after reading it got the whole stream.
                    span.outcome = "completed"
                    span.audit_outcome = finished_outcome(decision.decision)
                    span.partial_text = (
                        PartialText.KEPT
                        if decision.decision is Decision.ANSWER
                        else PartialText.REPLACED
                    )
                    yield completion
            except InvalidEventStream as error:
                # A refused event changes nothing, so an error can still end it.
                decision = None
                failure = FailureKind.INTERNAL
                span.failure = f"{type(error).__name__}:{error.defect.value}"
            if decision is None:
                failed = stream_failure(
                    failure or FailureKind.INTERNAL, StreamPhase.AFTER_HEADERS
                )
                error_event = writer.error(failed.kind)
                span.outcome = "failed"
                span.audit_outcome = failed.audit_outcome
                span.partial_text = failed.partial_text
                yield error_event
        finally:
            span.end()

    async def _finished(
        self,
        plan: _Plan,
```

**6.** Find:

```python
    def __init__(self, events: AsyncGenerator[bytes], span: _Span) -> None:
        self._events = events
        self._span = span

    def __aiter__(self) -> AnswerStream:
        return self

    async def __anext__(self) -> bytes:
        return await anext(self._events)

    async def aclose(self) -> None:
        try:
```

Replace with:

```python
    def __init__(self, events: AsyncGenerator[bytes], span: _Span) -> None:
        self._events = events
        self._span = span

    def __aiter__(self) -> AnswerStream:
        return self

    def headers_sent(self) -> None:
        """Record that the response headers went out, before any event."""
        self._span.phase = StreamPhase.AFTER_HEADERS

    async def __anext__(self) -> bytes:
        return await anext(self._events)

    async def aclose(self) -> None:
        try:
```

**7.** Find:

```python
    characters: int = 0
    deltas: int = 0
    citations: int = 0
    usage: Usage | None = None
    failure: str | None = None
    outcome: str = "cancelled"
    ended: bool = False

    def __post_init__(self) -> None:
        logger.info(
            "answer stream started",
            extra={"answer_stream": {"request_id": str(self.request_id)}},
```

Replace with:

```python
    characters: int = 0
    deltas: int = 0
    citations: int = 0
    usage: Usage | None = None
    failure: str | None = None
    outcome: str = "cancelled"
    phase: StreamPhase = StreamPhase.BEFORE_HEADERS
    audit_outcome: AuditOutcome = AuditOutcome.CANCELLED
    # What a client that left holds; a decision or an error sets it.
    partial_text: PartialText = PartialText.ABANDONED
    ended: bool = False

    def __post_init__(self) -> None:
        logger.info(
            "answer stream started",
            extra={"answer_stream": {"request_id": str(self.request_id)}},
```

**8.** Find:

```python
            "outcome": self.outcome,
            "failure": self.failure,
            "duration_ms": round((time.perf_counter() - self.started_at) * 1_000),
            "text_deltas": self.deltas,
            "characters": self.characters,
            "citations": self.citations,
        }
        if self.usage is not None:
            fields["input_tokens"] = self.usage.input_tokens
            fields["output_tokens"] = self.usage.output_tokens
        level = logging.INFO if self.outcome == "completed" else logging.WARNING
        logger.log(level, "answer stream ended", extra={"answer_stream": fields})
```

Replace with:

```python
            "outcome": self.outcome,
            "failure": self.failure,
            "duration_ms": round((time.perf_counter() - self.started_at) * 1_000),
            "text_deltas": self.deltas,
            "characters": self.characters,
            "citations": self.citations,
            "phase": str(self.phase),
            "audit_outcome": str(self.audit_outcome),
            "partial_text": str(
                self.partial_text if self.characters else PartialText.NONE
            ),
        }
        if self.usage is not None:
            fields["input_tokens"] = self.usage.input_tokens
            fields["output_tokens"] = self.usage.output_tokens
        level = logging.INFO if self.outcome == "completed" else logging.WARNING
        logger.log(level, "answer stream ended", extra={"answer_stream": fields})
```

In `src/knowledge_service/app.py`:

**1.** Find:

```python
from uuid import UUID, uuid4

import anyio
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from knowledge_service.answering.streaming import (
    CLOSE_SECONDS,
    AnswerStream,
    AnswerStreamer,
)
from knowledge_service.contracts import Answer, ErrorResponse, Question
```

Replace with:

```python
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
```

**2.** Find:

```python

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
```

Replace with:

```python

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
```

**3.** Find:

```python
                with anyio.move_on_after(2 * CLOSE_SECONDS, shield=True):
                    await close()
        if writing.done() and not writing.cancelled():
            writing.result()

    async def _write(self, send: Send) -> None:
        # Under ASGI 2.4, a write after the client left raises OSError.
        with contextlib.suppress(OSError):
            await self.stream_response(send)


async def _unless_client_leaves(
    request: Request, opening: Coroutine[object, object, AnswerStream]
) -> AnswerStream | None:
    """Await `opening`, or cancel it and return None when the client leaves.
```

Replace with:

```python
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
```

Check:

```bash
uv run pyright src
uv run pytest tests/test_answer_events.py tests/test_streaming.py tests/test_app.py -q
```

Expected: 0 errors, and the M06-T08, M06-T09, and app tests still pass.

### Step 5 — Coding step: tests that inject failures everywhere

This step proves the policy holds at every point a stream can fail.

**In plain words:** The unit tests check the policy table for every kind in both phases. Then they break a fake stream on purpose in the search, before any text, in the middle of the text, and after the text, with provider, database, validation, and unknown errors, and check the status or the last event, the record, and that a planted marker never escapes. Other tests make the client leave at different moments. The integration tests use a real PostgreSQL that cannot be reached or that is too slow, and expect a `503` with `Retry-After`.

| Test | What it proves |
|---|---|
| `test_before_the_headers_a_failure_is_a_plain_http_error` | The before-headers column of the policy |
| `test_after_the_headers_a_failure_is_a_terminal_event_and_never_a_status` | The after-headers column |
| `test_a_failure_in_the_search_is_an_http_error_with_retry_guidance` | Provider, database, validation, denied, and bug failures before the headers |
| `test_a_failure_after_the_headers_ends_in_one_error_event` | The same failures before, during, and after the text: the highest-risk edge |
| `test_a_client_that_leaves_mid_text_abandons_it` | Cancellation mid-text |
| `test_a_client_that_read_the_terminal_event_and_left_is_not_cancelled` | The record survives a client that leaves at the very end |
| `test_a_database_error_retrying_cannot_fix_is_internal_without_retry_after` | Typed failure: a database bug stays `500` |
| `test_an_unreachable_database_is_a_503_with_retry_after` | A real refused connection |
| `test_a_database_too_slow_for_its_timeout_is_a_503_with_retry_after` | A real statement timeout |

Create `tests/test_stream_failures.py`:

```python
"""Stream failures: one policy before and after the headers, injected everywhere."""

import asyncio
import json
import logging
from typing import Any, cast
from uuid import UUID

import psycopg.errors as pg
import pytest
from sqlalchemy import exc as database_errors
from starlette.types import Message

from evidence_fakes import MARKER
from knowledge_service.answering.events import (
    AnswerEventReader,
    AnswerEventWriter,
    InvalidEventStream,
    StreamOutcome,
)
from knowledge_service.answering.stream_failures import (
    RETRY_AFTER_SECONDS,
    AuditOutcome,
    PartialText,
    StreamPhase,
    stream_failure,
)
from knowledge_service.answering.streaming import FinishedAnswer
from knowledge_service.app import create_app
from knowledge_service.contracts import Question
from knowledge_service.embeddings.port import (
    EmbeddingInputRejected,
    EmbeddingRateLimited,
    RejectionReason,
)
from knowledge_service.errors import (
    AuthorizationDenied,
    FailureKind,
    RetryOwner,
    classify_failure,
)
from knowledge_service.model import ChatModelRateLimited, ChatModelUnavailable
from knowledge_service.workflow import AnswerWorkflow
from test_streaming import (
    ANSWER,
    CITED,
    QUESTION,
    REQUEST_ID,
    Connection,
    Model,
    Source,
    chunks,
    client,
    names,
    rebuilt,
    scope,
    streamer,
)

BEFORE = StreamPhase.BEFORE_HEADERS
AFTER = StreamPhase.AFTER_HEADERS


def database_down() -> database_errors.OperationalError:
    """What the driver raises when PostgreSQL cannot be reached."""
    return database_errors.OperationalError(
        f"SELECT '{MARKER}'", None, ConnectionRefusedError(MARKER)
    )


class Raising:
    """A finisher that fails the way `error` says, after the model replied."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def finish(
        self, result: Any, evidence: Any, *, request_id: UUID
    ) -> FinishedAnswer:
        raise self.error


def ended(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    (record,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    return record.answer_stream  # type: ignore[attr-defined, no-any-return]


def error_data(sent: list[bytes]) -> dict[str, Any]:
    data = sent[-1].split(b"\n")[2].removeprefix(b"data: ")
    return json.loads(data)


def leaks(caplog: pytest.LogCaptureFixture, *texts: bytes) -> bool:
    records = any(
        MARKER in r.getMessage() or MARKER in repr(r.__dict__) for r in caplog.records
    )
    return records or any(MARKER.encode() in text for text in texts)


# --- The policy -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "status", "code", "retry", "retry_after", "outcome"),
    [
        (
            FailureKind.VALIDATION,
            422,
            "validation_error",
            RetryOwner.CALLER,
            None,
            AuditOutcome.REJECTED,
        ),
        (
            FailureKind.AUTHORIZATION,
            403,
            "authorization_denied",
            RetryOwner.NONE,
            None,
            AuditOutcome.DENIED,
        ),
        (
            FailureKind.DEPENDENCY,
            503,
            "dependency_unavailable",
            RetryOwner.SERVICE,
            RETRY_AFTER_SECONDS,
            AuditOutcome.DEPENDENCY_ERROR,
        ),
        (
            FailureKind.CAPACITY,
            429,
            "capacity_exceeded",
            RetryOwner.CALLER,
            RETRY_AFTER_SECONDS,
            AuditOutcome.THROTTLED,
        ),
        (
            FailureKind.INTERNAL,
            500,
            "internal_error",
            RetryOwner.NONE,
            None,
            AuditOutcome.INTERNAL_ERROR,
        ),
        (
            FailureKind.CANCELLATION,
            None,
            None,
            RetryOwner.NONE,
            None,
            AuditOutcome.CANCELLED,
        ),
    ],
)
def test_before_the_headers_a_failure_is_a_plain_http_error(
    kind: FailureKind,
    status: int | None,
    code: str | None,
    retry: RetryOwner,
    retry_after: int | None,
    outcome: AuditOutcome,
) -> None:
    failure = stream_failure(kind, BEFORE)

    assert failure.status_code == status
    assert failure.public_code == code
    assert failure.retry is retry
    assert failure.retry_after_seconds == retry_after
    assert failure.partial_text is PartialText.NONE
    assert failure.audit_outcome is outcome


@pytest.mark.parametrize(
    ("kind", "code", "retry", "partial", "outcome"),
    [
        (
            FailureKind.VALIDATION,
            "internal_error",
            RetryOwner.NONE,
            PartialText.REPLACED,
            AuditOutcome.INTERNAL_ERROR,
        ),
        (
            FailureKind.AUTHORIZATION,
            "authorization_denied",
            RetryOwner.NONE,
            PartialText.REPLACED,
            AuditOutcome.DENIED,
        ),
        (
            FailureKind.DEPENDENCY,
            "dependency_unavailable",
            RetryOwner.CALLER,
            PartialText.REPLACED,
            AuditOutcome.DEPENDENCY_ERROR,
        ),
        (
            FailureKind.CAPACITY,
            "capacity_exceeded",
            RetryOwner.CALLER,
            PartialText.REPLACED,
            AuditOutcome.THROTTLED,
        ),
        (
            FailureKind.INTERNAL,
            "internal_error",
            RetryOwner.NONE,
            PartialText.REPLACED,
            AuditOutcome.INTERNAL_ERROR,
        ),
        (
            FailureKind.CANCELLATION,
            None,
            RetryOwner.NONE,
            PartialText.ABANDONED,
            AuditOutcome.CANCELLED,
        ),
    ],
)
def test_after_the_headers_a_failure_is_a_terminal_event_and_never_a_status(
    kind: FailureKind,
    code: str | None,
    retry: RetryOwner,
    partial: PartialText,
    outcome: AuditOutcome,
) -> None:
    failure = stream_failure(kind, AFTER)

    assert failure.status_code is None
    assert failure.retry_after_seconds is None
    assert failure.public_code == code
    assert failure.retry is retry
    assert failure.partial_text is partial
    assert failure.audit_outcome is outcome


def database_state(error: type[Exception]) -> database_errors.OperationalError:
    """An OperationalError whose driver error carries one SQLSTATE."""
    return database_errors.OperationalError(f"SELECT '{MARKER}'", None, error(MARKER))


@pytest.mark.parametrize(
    "error",
    [
        database_down(),
        database_state(pg.QueryCanceled),
        database_state(pg.AdminShutdown),
        database_state(pg.TooManyConnections),
        database_state(pg.SerializationFailure),
        database_errors.TimeoutError(MARKER),
    ],
    ids=[
        "unreachable",
        "statement-timeout",
        "shutdown",
        "too-many-connections",
        "serialization",
        "pool-exhausted",
    ],
)
def test_a_database_that_is_unavailable_is_a_dependency_failure(
    error: Exception,
) -> None:
    assert classify_failure(error) is FailureKind.DEPENDENCY


def test_a_database_error_that_is_a_bug_stays_internal() -> None:
    broken = database_errors.IntegrityError("INSERT", None, Exception(MARKER))

    assert classify_failure(broken) is FailureKind.INTERNAL


# --- Failures injected before the headers ---------------------------------------------


@pytest.mark.parametrize(
    ("error", "status", "code", "retry_after", "outcome"),
    [
        (database_down(), 503, "dependency_unavailable", "5", "dependency_error"),
        (EmbeddingRateLimited(MARKER), 429, "capacity_exceeded", "5", "throttled"),
        (
            EmbeddingInputRejected(RejectionReason.EMPTY_TEXT, index=0),
            422,
            "validation_error",
            None,
            "rejected",
        ),
        (AuthorizationDenied(MARKER), 403, "authorization_denied", None, "denied"),
        (RuntimeError(MARKER), 500, "internal_error", None, "internal_error"),
    ],
    ids=["database", "provider-limit", "validation", "denied", "bug"],
)
async def test_a_failure_in_the_search_is_an_http_error_with_retry_guidance(
    error: Exception,
    status: int,
    code: str,
    retry_after: str | None,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = Model()
    app = create_app(streamer=streamer(model, source=Source(error=error)))

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.headers.get("retry-after") == retry_after
    assert model.calls == 0
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("before_headers", outcome)
    assert record["partial_text"] == "none"
    assert not leaks(caplog, response.content, str(response.headers).encode())


# --- Failures injected after the headers ----------------------------------------------


def at(point: str, error: Exception) -> dict[str, Any]:
    """Where the failure happens once the stream has started."""
    if point == "before-text":
        return {"model": Model(parts=[], error=error)}  # type: ignore[arg-type]
    if point == "mid-text":
        return {"model": Model(error=error)}  # type: ignore[arg-type]
    return {"model": Model(), "finisher": Raising(error)}


@pytest.mark.parametrize(
    ("point", "error", "code", "retry", "outcome"),
    [
        (
            "before-text",
            ChatModelUnavailable(MARKER),
            "dependency_unavailable",
            "caller",
            "dependency_error",
        ),
        (
            "mid-text",
            ChatModelUnavailable(MARKER),
            "dependency_unavailable",
            "caller",
            "dependency_error",
        ),
        (
            "mid-text",
            ChatModelRateLimited(MARKER),
            "capacity_exceeded",
            "caller",
            "throttled",
        ),
        (
            "after-text",
            database_down(),
            "dependency_unavailable",
            "caller",
            "dependency_error",
        ),
        (
            "after-text",
            EmbeddingInputRejected(RejectionReason.EMPTY_TEXT, index=0),
            "internal_error",
            "none",
            "internal_error",
        ),
        (
            "after-text",
            RuntimeError(MARKER),
            "internal_error",
            "none",
            "internal_error",
        ),
    ],
    ids=[
        "provider-before-text",
        "provider-mid-text",
        "provider-limit-mid-text",
        "database-after-text",
        "validation-after-text",
        "bug-after-text",
    ],
)
async def test_a_failure_after_the_headers_ends_in_one_error_event(
    point: str,
    error: Exception,
    code: str,
    retry: str,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    sent = await chunks(streamer(**at(point, error)))

    assert names(sent)[0] == "metadata"
    assert names(sent)[-1] == "error"
    assert names(sent).count("error") == 1
    assert "citation" not in names(sent)
    data = error_data(sent)
    assert (data["code"], data["retry"]) == (code, retry)
    result = rebuilt(sent)
    assert result.outcome is StreamOutcome.FAILED
    # The text already streamed is replaced by the fixed message.
    assert result.text == ""
    assert result.message == data["message"]
    streamed = "text_delta" in names(sent)
    assert streamed is (point != "before-text")
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("after_headers", outcome)
    assert record["partial_text"] == ("replaced" if streamed else "none")
    assert not leaks(caplog, *sent)


async def test_a_finished_stream_records_what_the_client_keeps(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    sent = await chunks(streamer(Model()))

    assert rebuilt(sent).outcome is StreamOutcome.ANSWERED
    record = ended(caplog)
    assert (record["audit_outcome"], record["partial_text"]) == ("answered", "kept")


# --- Cancellation at several points ---------------------------------------------------


async def test_a_client_that_leaves_during_the_search_is_recorded_before_the_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    source = Source(wait=True)
    opening = asyncio.create_task(
        streamer(Model(), source=source).open(QUESTION, request_id=REQUEST_ID)
    )
    await asyncio.sleep(0)

    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening

    assert source.cancelled
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("before_headers", "cancelled")
    assert record["partial_text"] == "none"


async def test_a_client_that_leaves_mid_text_abandons_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model(hold=2)
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)
    sent = [await anext(events), await anext(events)]

    await events.aclose()

    assert names(sent) == ["metadata", "text_delta"]
    assert model.closed
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("after_headers", "cancelled")
    assert record["partial_text"] == "abandoned"
    # What the client holds is cut off, and a reader refuses it.
    reader = AnswerEventReader()
    for chunk in sent:
        reader.feed(chunk)
    with pytest.raises(InvalidEventStream, match="truncated"):
        reader.close()


# --- The event contract follows the policy --------------------------------------------


def test_an_error_event_with_the_retry_owner_of_the_http_policy_is_refused() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    reader = AnswerEventReader()
    reader.feed(writer.metadata())
    sent = writer.error(FailureKind.DEPENDENCY)
    # Before the headers an outage is the service's to retry; not after.
    stale = sent.replace(b'"retry":"caller"', b'"retry":"service"')

    assert stale != sent
    with pytest.raises(InvalidEventStream, match="malformed"):
        reader.feed(stale)


# --- Review regressions ---------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        database_state(pg.ProgramLimitExceeded),
        database_state(pg.ObjectNotInPrerequisiteState),
        database_errors.InterfaceError("SELECT 1", None, Exception(MARKER)),
    ],
    ids=["program-limit", "wrong-state", "driver-misuse"],
)
async def test_a_database_error_retrying_cannot_fix_is_internal_without_retry_after(
    error: Exception,
) -> None:
    app = create_app(streamer=streamer(Model(), source=Source(error=error)))

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert classify_failure(error) is FailureKind.INTERNAL
    assert response.status_code == 500
    assert "retry-after" not in response.headers


class LeavesOnHeaders(Connection):
    """Leaves while the response headers are still being handed over."""

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.left.set()
            await asyncio.Event().wait()
        await super().send(message)


async def test_a_client_that_leaves_as_the_headers_go_out_is_after_the_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model()
    connection = LeavesOnHeaders(leave_after=1)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send), timeout=5
    )

    assert connection.bodies == []
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("after_headers", "cancelled")
    assert record["partial_text"] == "none"


async def test_text_sent_at_the_end_is_counted_when_the_client_leaves(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    # In this key order nothing streams live; the whole text goes at the end.
    final = json.dumps(
        {"citations": [CITED], "abstained": False, "reason": None, "answer": ANSWER}
    )
    events = await streamer(Model(final=final)).open(QUESTION, request_id=REQUEST_ID)
    sent = [await anext(events), await anext(events)]

    await events.aclose()

    assert names(sent) == ["metadata", "text_delta"]
    record = ended(caplog)
    assert record["characters"] == len(ANSWER)
    assert (record["audit_outcome"], record["partial_text"]) == (
        "cancelled",
        "abandoned",
    )


@pytest.mark.parametrize(
    ("finisher", "outcome", "partial"),
    [(None, "answered", "kept"), ("failing", "dependency_error", "replaced")],
    ids=["completion", "error"],
)
async def test_a_client_that_read_the_terminal_event_and_left_is_not_cancelled(
    finisher: str | None,
    outcome: str,
    partial: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    failing = Raising(database_down()) if finisher else None
    events = await streamer(Model(), finisher=failing).open(
        QUESTION, request_id=REQUEST_ID
    )
    sent: list[bytes] = []
    while not sent or names(sent)[-1] not in ("completion", "error"):
        sent.append(await anext(events))

    # It leaves before asking for more, while the stream waits at the end.
    await events.aclose()

    record = ended(caplog)
    assert (record["audit_outcome"], record["partial_text"]) == (outcome, partial)


class FailingWorkflow:
    async def answer(self, question: Question, *, request_id: UUID) -> Any:
        raise ChatModelUnavailable(MARKER)


async def test_the_answer_endpoint_gives_the_same_retry_guidance() -> None:
    app = create_app(workflow=cast(AnswerWorkflow, FailingWorkflow()))

    async with client(app) as http:
        response = await http.post("/v1/answer", json={"text": QUESTION.text})

    assert response.status_code == 503
    assert response.headers["retry-after"] == str(RETRY_AFTER_SECONDS)
```

Create `tests/test_stream_failures_integration.py`:

```python
"""Real database failures before the headers: an outage is a 503 with Retry-After."""

import logging
from collections.abc import AsyncIterator
from typing import cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from evidence_fakes import MARKER
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.settings import Settings
from test_streaming import (
    EVIDENCE,
    QUESTION,
    SIGNALS,
    Model,
    Source,
    client,
    streamer,
)

pytestmark = pytest.mark.integration

# Nothing listens on port 1, so the connection is refused at once.
UNREACHABLE = "postgresql://reader:secret@127.0.0.1:1/knowledge"


class DatabaseSource:
    """Find Evidence after one query, as a real source would."""

    def __init__(self, runtime: DatabaseRuntime, query: str) -> None:
        self.runtime = runtime
        self.query = query

    async def find(self, question: str, *, request_id: UUID) -> FoundEvidence:
        async with self.runtime.transaction() as connection:
            await connection.execute(sa.text(self.query), {"marker": MARKER})
        return FoundEvidence(EVIDENCE, SIGNALS)


@pytest.fixture
async def unreachable() -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(
            database_url=SecretStr(UNREACHABLE), database_connect_timeout_seconds=2
        )
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


@pytest.fixture
async def impatient(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(
            database_url=SecretStr(migrated_database),
            database_statement_timeout_ms=50,
        )
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def search_fails(
    runtime: DatabaseRuntime, query: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Search with one failing query, then check what the client and log got."""
    caplog.set_level(logging.DEBUG)
    model = Model()
    source = cast(Source, DatabaseSource(runtime, query))
    app = create_app(streamer=streamer(model, source=source))

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert response.headers["retry-after"] == "5"
    assert model.calls == 0
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    stream = ended.answer_stream  # type: ignore[attr-defined]
    assert stream["audit_outcome"] == "dependency_error"
    assert stream["failure"] == "OperationalError"
    exposed = [response.text, str(response.headers), caplog.text]
    assert not any(MARKER in text or "secret" in text for text in exposed)


async def test_an_unreachable_database_is_a_503_with_retry_after(
    unreachable: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    await search_fails(unreachable, "SELECT :marker", caplog)


async def test_a_database_too_slow_for_its_timeout_is_a_503_with_retry_after(
    impatient: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    await search_fails(impatient, "SELECT pg_sleep(1), :marker", caplog)
```

Check:

```bash
uv run pytest tests/test_stream_failures.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_stream_failures_integration.py -m integration -q
```

Expected: 42 passed, then 2 passed.

### Step 6 — No coding in this step: break it and decide

This step shows what the database rule protects, and records what you learned.

**Action 1:** break the classification on purpose. In `src/knowledge_service/errors.py`, in `classify_failure`, delete these two lines:

```python
    if _database_unavailable(error):
        return FailureKind.DEPENDENCY
```

Run:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_stream_failures_integration.py -m integration -q
```

Expected: 2 failed: both real database failures are now `500 internal_error` without `Retry-After`. The unit tests that inject a database error fail too (9 of 42), because the policy table is not the problem; the classification is. Put the lines back and run again until all pass.

**Action 2:** under `## Decision`, write:

- why a failure after the headers cannot change the status, and what the client must do with the text it already has;
- why an outage is the service's to retry before the headers and the caller's after them;
- why a validation failure after the headers is reported as internal;
- why only some database errors are outages;
- why the end of a stream is recorded before its last event is handed over;
- what is left for later: a stored Audit Event once there is a verified actor (M07), and a new protocol version for the next change a shipped client would notice;
- at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."

### Step 7 — No coding in this step: architecture, gates, evidence, and progress

This step updates the text the change made incomplete, proves everything still works, and records the result.

**Action 1:** add the policy to the `answering` row and the two phases to the answering diagram.

In `docs/architecture.md`:

**1.** Find:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

Replace with:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, and what became of its partial text), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

**2.** Find:

````markdown
    DB-->>Retrieval: authorized Candidates
    Retrieval->>Ranker: bounded Candidate set
    Ranker-->>Retrieval: ranked Evidence
    API->>Model: instructions + numbered Evidence
    Model-->>API: streamed Answer events
    API->>API: validate citations
    API-->>User: Answer or safe failure
```

## Deliberate exclusions from the default path

- LangGraph and multi-agent orchestration do not replace ordinary question answering.
- MCP is an additional protocol adapter, not the owner of search logic.
````

Replace with:

````markdown
    DB-->>Retrieval: authorized Candidates
    Retrieval->>Ranker: bounded Candidate set
    Ranker-->>Retrieval: ranked Evidence
    API->>Model: instructions + numbered Evidence
    Model-->>API: streamed Answer events
    API->>API: validate citations
    alt a failure before the headers
        API-->>User: HTTP error, with Retry-After for 503 or 429
    else after the headers
        API-->>User: text, Citations, usage, then completion or one error event
    end
```

## Deliberate exclusions from the default path

- LangGraph and multi-agent orchestration do not replace ordinary question answering.
- MCP is an additional protocol adapter, not the owner of search logic.
````

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_stream_failures.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2297 passed for `make test-unit`, 42 more than M06-T12, and 378 passed for `make test-integration`, 2 more. If it says there is no test database, start it with `docker start knowledge-service-postgres`.

**Action 3:** complete the evidence file:

- **Design:** the two phases and the table, the database rule, the retry guidance, the partial text, the record, and when the phase changes.
- **Verification:** the commands and counts, the deliberate break, and the known limits from "Before you begin".
- **Review:** an independent adversarial review injected failures and client departures at every point of a stream: every stream ended in exactly one terminal event, and no marker leaked. It found five problems, all fixed with tests that failed before the fix: an unfixable database error was a `503`; a client leaving as the headers went out was recorded before the headers; end text was not counted for a client that left with it; a client that read the terminal event and left was recorded as cancelled; `/v1/answer` had no `Retry-After`. One limit is documented: the reader's stricter check kept protocol version `1`.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M06-T13 row to `docs/curriculum/PROGRESS.md`, set the next task to M06-T14, and commit with `m06-t13: map streaming failures safely`.

## Reflection questions

1. What does `stream_failure` hide from the endpoint, the event writer, and the reader? List what each of them no longer has to decide.
2. Someone wants the service to retry the model by itself when it fails halfway through an Answer. What would the client see, and what would have to change in the policy and the events?
3. A slow database makes every request wait for its statement timeout, and each gets a `503` with `Retry-After: 5`. What happens if a thousand clients all retry after exactly five seconds, and what would you change?
4. Which change during a later optimization would most easily break "recorded before it is handed over", and which test would catch it?
5. Which failure would you inject next to look for a stream with no terminal event or the wrong audit outcome, and what do you expect to see?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the code.
- [ ] `stream_failure` gives the before-headers and after-headers behavior for every failure kind.
- [ ] Before the headers, an outage or a limit is an HTTP error with `Retry-After`; after them, it is one `error` event the caller may retry.
- [ ] After the headers, a validation failure is reported as internal.
- [ ] A database that is unreachable, overloaded, or too slow is a `503`; any other database error stays a `500`.
- [ ] The event writer and reader agree on the after-headers guidance.
- [ ] Every stream is recorded once with its phase, its audit outcome, and what became of its partial text, also when the client leaves at the very end.
- [ ] No response, header, or record holds a Question, an Answer, SQL, or a connection string.
- [ ] You ran the deliberate break, saw the tests fail, and undid it.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
