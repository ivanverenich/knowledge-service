"""The events of a streamed Answer, and the rules for their order.

The server sends one Answer as Server-Sent Events (SSE): plain text, one
event per block, each block an `id:` line, an `event:` line, a `data:`
line holding one JSON object, and a blank line. `AnswerEventWriter` writes
the events; `AnswerEventReader` reads them back as a client would and
rebuilds the Answer. Both check every event against one set of rules, so
whatever the writer sends, the reader accepts.

The order of one stream:

    metadata  text_delta*  citation*  usage?  (completion | error)

- **`metadata` first.** Exactly once: the protocol version and the
  request ID. A request that fails before it is an HTTP error, not a
  stream.
- **Text, then Citations, then usage.** `text_delta` events hold the
  Answer's text in pieces; joined, they are the text. `citation` events
  follow, each Evidence once; `usage` follows, at most once.
- **One terminal event, last.** `completion` or `error` ends the stream;
  nothing may follow it, and a stream that stops without one is cut off.
- **What the client keeps.** `completion` with `answer` keeps the text and
  the Citations; it needs visible text, a Citation, and the usage.
  `completion` with `abstain` replaces any streamed text with the fixed
  message of its reason, and an `error` replaces it with the fixed message
  of its code. Citations come only after the server has decided to
  answer: an abstention after a Citation is refused, so a server that
  sends Citations before it decides fails its tests.
- **Nothing lost or repeated.** Event IDs count up from 0 by one.
- **One spelling.** The reader accepts an event only in the exact bytes
  the writer would send: no extra space, no escaped letter, no other order
  of keys. A Citation must name its Evidence and Document as the server
  does, and token counts stay below 2**53, the largest whole number a
  JavaScript client reads exactly.
- **Bounded.** The text holds at most `MAX_ANSWER_CHARACTERS`, Citations
  are at most `MAX_EVIDENCE`, and one event at most `MAX_EVENT_CHARACTERS`,
  on both sides.
- **Quiet.** Errors name a defect, never a value from the stream, and keep
  no chained error that could; the reprs hide text and messages.
"""

import codecs
import contextlib
import json
import re
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from knowledge_service.answering.abstention import (
    AbstentionDecision,
    AbstentionReason,
    Decision,
    abstention_message,
)
from knowledge_service.answering.citations import (
    Citation,
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
_TOKENS = ("input_tokens", "output_tokens", "total_tokens")
MAX_TOKENS = 2**53 - 1


class StreamDefect(StrEnum):
    """Why a stream was refused; the stream's values are never repeated."""

    MALFORMED = "malformed"
    OUT_OF_ORDER = "out_of_order"
    AFTER_TERMINAL = "after_terminal"
    TRUNCATED = "truncated"
    GAP = "gap"
    TOO_LARGE = "too_large"
    DUPLICATE_CITATION = "duplicate_citation"
    INCOMPLETE_ANSWER = "incomplete_answer"
    UNKNOWN_ERROR = "unknown_error"


class InvalidEventStream(ValueError):
    """An event breaks the stream's rules; nothing of the stream is quoted."""

    def __init__(self, defect: StreamDefect) -> None:
        self.defect = defect
        super().__init__(f"answer event stream: {defect.value}")


class EventName(StrEnum):
    """The `event:` line of each kind of event."""

    METADATA = "metadata"
    TEXT_DELTA = "text_delta"
    CITATION = "citation"
    USAGE = "usage"
    COMPLETION = "completion"
    ERROR = "error"


_NAMES = frozenset(name.value for name in EventName)


class MetadataData(BaseModel):
    """The first event: which protocol, and which request."""

    model_config = _STRICT

    version: Literal["1"]
    request_id: str


class TextDeltaData(BaseModel):
    """The next piece of the Answer's text."""

    model_config = _STRICT

    text: str = Field(min_length=1, repr=False)


class UsageData(BaseModel):
    """The tokens the provider billed for this Answer."""

    model_config = _STRICT

    input_tokens: int = Field(ge=0, le=MAX_TOKENS)
    output_tokens: int = Field(ge=0, le=MAX_TOKENS)
    total_tokens: int = Field(ge=0, le=MAX_TOKENS)


class CompletionData(BaseModel):
    """The decision: keep the Answer, or show the reason's fixed message."""

    model_config = _STRICT

    decision: Decision
    reason: AbstentionReason | None
    message: str | None = Field(repr=False)
    policy_version: str = Field(min_length=1)


class ErrorData(BaseModel):
    """A failure: its public code, fixed message, and who may retry."""

    model_config = _STRICT

    code: str
    message: str = Field(repr=False)
    retry: RetryOwner


type EventData = (
    MetadataData
    | TextDeltaData
    | CitationPayload
    | UsageData
    | CompletionData
    | ErrorData
)

_DATA: dict[EventName, type[BaseModel]] = {
    EventName.METADATA: MetadataData,
    EventName.TEXT_DELTA: TextDeltaData,
    EventName.CITATION: CitationPayload,
    EventName.USAGE: UsageData,
    EventName.COMPLETION: CompletionData,
    EventName.ERROR: ErrorData,
}


@dataclass(frozen=True, slots=True)
class AnswerEvent:
    """One event: its position, its kind, and its checked data."""

    event_id: int
    name: EventName
    data: EventData


class StreamOutcome(StrEnum):
    """What the client shows at the end."""

    ANSWERED = "answered"
    ABSTAINED = "abstained"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StreamedAnswer:
    """The Answer a client rebuilt from a complete stream.

    `text` and `citations` are empty unless `ANSWERED`; `message` is the
    fixed message to show instead when `ABSTAINED` or `FAILED`.
    """

    request_id: UUID
    outcome: StreamOutcome
    text: str = field(repr=False)
    citations: tuple[CitationPayload, ...]
    usage: UsageData | None
    reason: AbstentionReason | None
    error_code: str | None
    message: str | None = field(repr=False)


class _Phase(StrEnum):
    START = "start"
    METADATA = "metadata"
    TEXT = "text"
    CITATIONS = "citations"
    USAGE = "usage"
    DONE = "done"


# Which phases each event may follow, and the phase it moves the stream to.
_AFTER: dict[EventName, tuple[frozenset[_Phase], _Phase]] = {
    EventName.METADATA: (frozenset({_Phase.START}), _Phase.METADATA),
    EventName.TEXT_DELTA: (
        frozenset({_Phase.METADATA, _Phase.TEXT}),
        _Phase.TEXT,
    ),
    EventName.CITATION: (
        frozenset({_Phase.METADATA, _Phase.TEXT, _Phase.CITATIONS}),
        _Phase.CITATIONS,
    ),
    EventName.USAGE: (
        frozenset({_Phase.METADATA, _Phase.TEXT, _Phase.CITATIONS}),
        _Phase.USAGE,
    ),
    EventName.COMPLETION: (
        frozenset({_Phase.METADATA, _Phase.TEXT, _Phase.CITATIONS, _Phase.USAGE}),
        _Phase.DONE,
    ),
    EventName.ERROR: (
        frozenset({_Phase.METADATA, _Phase.TEXT, _Phase.CITATIONS, _Phase.USAGE}),
        _Phase.DONE,
    ),
}


class _Rules:
    """The order and content rules both the writer and the reader keep."""

    def __init__(self) -> None:
        self.phase = _Phase.START
        self.next_id = 0
        self.request_id: UUID | None = None
        self.text: list[str] = []
        self.characters = 0
        self.citations: list[CitationPayload] = []
        self.usage: UsageData | None = None
        self.result: StreamedAnswer | None = None

    def accept(self, event: AnswerEvent) -> None:
        """Take one event, or raise `InvalidEventStream` and change nothing."""
        if self.phase is _Phase.DONE:
            raise InvalidEventStream(StreamDefect.AFTER_TERMINAL)
        if event.event_id != self.next_id:
            raise InvalidEventStream(StreamDefect.GAP)
        allowed, following = _AFTER[event.name]
        if self.phase not in allowed:
            raise InvalidEventStream(StreamDefect.OUT_OF_ORDER)
        self._take(event.data)
        self.phase = following
        self.next_id += 1

    def _take(self, data: EventData) -> None:
        match data:
            case MetadataData():
                self.request_id = _request_id(data.request_id)
            case TextDeltaData():
                if self.characters + len(data.text) > MAX_ANSWER_CHARACTERS:
                    raise InvalidEventStream(StreamDefect.TOO_LARGE)
                self.text.append(data.text)
                self.characters += len(data.text)
            case CitationPayload():
                if not _server_ids(data):
                    raise InvalidEventStream(StreamDefect.MALFORMED)
                if len(self.citations) >= MAX_EVIDENCE:
                    raise InvalidEventStream(StreamDefect.TOO_LARGE)
                if any(c.evidence_id == data.evidence_id for c in self.citations):
                    raise InvalidEventStream(StreamDefect.DUPLICATE_CITATION)
                self.citations.append(data)
            case UsageData():
                self.usage = data
            case CompletionData():
                self.result = self._completed(data)
            case ErrorData():
                self.result = self._failed(data)

    def _completed(self, data: CompletionData) -> StreamedAnswer:
        if data.decision is Decision.ANSWER:
            text = "".join(self.text)
            if data.reason is not None or data.message is not None:
                raise InvalidEventStream(StreamDefect.MALFORMED)
            if not has_visible_text(text) or not self.citations or self.usage is None:
                raise InvalidEventStream(StreamDefect.INCOMPLETE_ANSWER)
            return self._result(StreamOutcome.ANSWERED, text=text)
        if data.reason is None or data.message not in _messages(data.reason):
            raise InvalidEventStream(StreamDefect.MALFORMED)
        # Citations go only with an Answer the client keeps.
        if self.citations:
            raise InvalidEventStream(StreamDefect.OUT_OF_ORDER)
        return self._result(
            StreamOutcome.ABSTAINED, reason=data.reason, message=data.message
        )

    def _failed(self, data: ErrorData) -> StreamedAnswer:
        policy = _PUBLIC_ERRORS.get(data.code)
        if policy is None:
            raise InvalidEventStream(StreamDefect.UNKNOWN_ERROR)
        if data.message != policy.client_message or data.retry is not (
            policy.retry_owner
        ):
            raise InvalidEventStream(StreamDefect.MALFORMED)
        return self._result(
            StreamOutcome.FAILED, error_code=data.code, message=data.message
        )

    def _result(
        self,
        outcome: StreamOutcome,
        *,
        text: str = "",
        reason: AbstentionReason | None = None,
        error_code: str | None = None,
        message: str | None = None,
    ) -> StreamedAnswer:
        return StreamedAnswer(
            request_id=cast(UUID, self.request_id),
            outcome=outcome,
            text=text,
            citations=tuple(self.citations)
            if outcome is StreamOutcome.ANSWERED
            else (),
            usage=self.usage,
            reason=reason,
            error_code=error_code,
            message=message,
        )


class AnswerEventWriter:
    """Encode one Answer's events in order; refuse any event out of order.

    Each method returns the bytes of one event, ready to send. A refused
    event raises `InvalidEventStream`, and the stream stays where it was.
    """

    def __init__(self, request_id: UUID) -> None:
        if type(request_id) is not UUID:
            raise InvalidEventStream(StreamDefect.MALFORMED)
        self._request_id = request_id
        self._rules = _Rules()

    def metadata(self) -> bytes:
        return self._write(
            EventName.METADATA,
            lambda: MetadataData(
                version=EVENT_PROTOCOL_VERSION, request_id=str(self._request_id)
            ),
        )

    def text_delta(self, text: str) -> bytes:
        return self._write(EventName.TEXT_DELTA, lambda: TextDeltaData(text=text))

    def citation(self, citation: Citation) -> bytes:
        if type(citation) is not Citation:
            raise InvalidEventStream(StreamDefect.MALFORMED)
        return self._write(EventName.CITATION, lambda: citation_payload(citation))

    def usage(self, usage: Usage) -> bytes:
        if type(usage) is not Usage:
            raise InvalidEventStream(StreamDefect.MALFORMED)
        # A Usage built with model_construct may lack a field or hold any type.
        counts = {name: getattr(usage, name, None) for name in _TOKENS}
        return self._write(EventName.USAGE, lambda: UsageData.model_validate(counts))

    def completion(self, decision: AbstentionDecision, language: Language) -> bytes:
        if type(decision) is not AbstentionDecision or type(language) is not Language:
            raise InvalidEventStream(StreamDefect.MALFORMED)

        def build() -> CompletionData:
            reason = decision.reason
            return CompletionData(
                decision=decision.decision,
                reason=reason,
                message=(
                    None if reason is None else abstention_message(reason, language)
                ),
                policy_version=decision.policy_version,
            )

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
            raise InvalidEventStream(StreamDefect.TOO_LARGE)
        self._rules.accept(event)
        return block.encode("utf-8")


class AnswerEventReader:
    """Read a stream's bytes as they arrive, and rebuild the Answer.

    `feed` returns the events completed by these bytes; `close` returns
    the `StreamedAnswer` once the stream has ended. After a refused event
    the reader stays broken and raises the same defect again.
    """

    def __init__(self) -> None:
        self._rules = _Rules()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        # The start of a line still arriving, kept in pieces: joining them
        # on every chunk would cost the square of a long line.
        self._pending: list[str] = []
        self._pending_characters = 0
        self._block: list[str] = []
        self._block_characters = 0
        self._broken: StreamDefect | None = None

    def feed(self, chunk: bytes) -> tuple[AnswerEvent, ...]:
        if self._broken is None and type(chunk) is not bytes:
            self._broken = StreamDefect.MALFORMED
        with self._guard():
            return self._read(self._decode(chunk, final=False))

    def close(self) -> StreamedAnswer:
        with self._guard():
            self._read(self._decode(b"", final=True))
            result = self._rules.result
            if self._pending or self._block or result is None:
                raise InvalidEventStream(StreamDefect.TRUNCATED)
            return result

    @contextlib.contextmanager
    def _guard(self) -> Generator[None]:
        """Refuse a broken stream, and remember the defect that broke it."""
        if self._broken is not None:
            raise InvalidEventStream(self._broken)
        try:
            yield
        except InvalidEventStream as error:
            self._broken = error.defect
            raise

    def _decode(self, chunk: bytes, *, final: bool) -> str:
        try:
            return self._decoder.decode(chunk, final=final)
        except UnicodeDecodeError:
            pass
        raise InvalidEventStream(StreamDefect.MALFORMED)

    def _read(self, text: str) -> tuple[AnswerEvent, ...]:
        # The writer never sends a carriage return; JSON escapes it.
        if "\r" in text:
            raise InvalidEventStream(StreamDefect.MALFORMED)
        if text and self._rules.phase is _Phase.DONE:
            raise InvalidEventStream(StreamDefect.AFTER_TERMINAL)
        *lines, rest = text.split("\n")
        if lines:
            lines[0] = "".join((*self._pending, lines[0]))
            self._pending, self._pending_characters = [], 0
        events: list[AnswerEvent] = []
        for line in lines:
            event = self._line(line)
            if event is not None:
                events.append(event)
        if rest:
            if self._rules.phase is _Phase.DONE:
                raise InvalidEventStream(StreamDefect.AFTER_TERMINAL)
            self._pending.append(rest)
            self._pending_characters += len(rest)
        if self._block_characters + self._pending_characters > MAX_EVENT_CHARACTERS:
            raise InvalidEventStream(StreamDefect.TOO_LARGE)
        return tuple(events)

    def _line(self, line: str) -> AnswerEvent | None:
        if self._rules.phase is _Phase.DONE:
            raise InvalidEventStream(StreamDefect.AFTER_TERMINAL)
        if line.startswith(":") and not self._block:
            return None  # a comment, such as a keep-alive
        # The block's lines, their line ends, and the blank line ending it.
        self._block_characters += len(line) + 1
        if self._block_characters + 1 > MAX_EVENT_CHARACTERS:
            raise InvalidEventStream(StreamDefect.TOO_LARGE)
        if line:
            if len(self._block) == 3:
                raise InvalidEventStream(StreamDefect.MALFORMED)
            self._block.append(line)
            return None
        if not self._block:
            raise InvalidEventStream(StreamDefect.MALFORMED)
        block, self._block, self._block_characters = self._block, [], 0
        event = _decoded(block)
        self._rules.accept(event)
        return event


@dataclass(frozen=True, slots=True)
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


def _built(
    event_id: int, name: EventName, build: Callable[[], EventData]
) -> tuple[AnswerEvent, str]:
    """An event built and encoded from the caller's values, or a refusal."""
    built: tuple[AnswerEvent, str] | None = None
    try:
        event = AnswerEvent(event_id, name, build())
        block = _encode(event)
        block.encode("utf-8")
        built = event, block
    except Exception:  # noqa: S110 - a caller's object may raise anything
        # An error may quote the caller's text; nothing of it is kept.
        pass
    if built is None:
        raise InvalidEventStream(StreamDefect.MALFORMED)
    return built


def _encode(event: AnswerEvent) -> str:
    body = _body(event.data)
    return f"id: {event.event_id}\nevent: {event.name.value}\ndata: {body}\n\n"


def _body(data: EventData) -> str:
    """The one spelling of an event's JSON."""
    return json.dumps(
        data.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    )


def _decoded(block: list[str]) -> AnswerEvent:
    """One block of three lines as a checked event, or `MALFORMED`."""
    if len(block) != 3:
        raise InvalidEventStream(StreamDefect.MALFORMED)
    id_line, name_line, data_line = block
    matched = _ID.fullmatch(id_line)
    name = name_line.removeprefix("event: ")
    if (
        matched is None
        or not name_line.startswith("event: ")
        or name not in _NAMES
        or not data_line.startswith("data: ")
    ):
        raise InvalidEventStream(StreamDefect.MALFORMED)
    kind = EventName(name)
    data = _validated(_DATA[kind], data_line.removeprefix("data: "))
    return AnswerEvent(int(matched.group(1)), kind, data)


def _validated(model: type[BaseModel], text: str) -> EventData:
    """The JSON object of one `data:` line, checked by its model."""
    data: EventData | None = None
    # A validation error quotes its input; nothing of it is kept.
    with contextlib.suppress(ValueError, RecursionError):
        data = cast(EventData, model.model_validate_json(text))
    # Only the writer's own spelling is accepted: this also refuses a key
    # given twice, which the model would read as its last value.
    if data is None or _body(data) != text:
        raise InvalidEventStream(StreamDefect.MALFORMED)
    return data


def _server_ids(payload: CitationPayload) -> bool:
    """True when a Citation names its Evidence and Document as the server does."""
    try:
        EvidenceId(payload.evidence_id)
        document = UUID(payload.document_id)
    except ValueError:
        return False
    return str(document) == payload.document_id


def _request_id(value: str) -> UUID:
    try:
        parsed = UUID(value)
    except ValueError:
        parsed = None
    if parsed is None or str(parsed) != value:
        raise InvalidEventStream(StreamDefect.MALFORMED)
    return parsed
