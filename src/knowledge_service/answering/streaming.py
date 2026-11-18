"""Streaming an Answer as SSE events, under one deadline, cancelled cleanly.

`AnswerStreamer.open` does the work that may still fail as a plain HTTP
error: it finds the Evidence and judges it, before any byte is sent. It
returns an `AnswerStream` of event bytes (see `answering.events`) that
asks the model, passes the Answer's text on as it arrives, and ends with
the policy's decision.

What the stream promises:

- **Pulled, never pushed.** Each event is made only when the server asks
  for the next one, so a slow reader slows the model's stream instead of
  filling a queue: that is the backpressure. Nothing is buffered but the
  few characters of an unfinished escape.
- **One deadline.** `timeout_seconds` covers finding Evidence, the model's
  whole stream, and the policy's checks. Before the first byte, running
  out is `AnswerDeadlineExceeded`; after it, the stream ends with an
  `error` event.
- **Text as it comes, the decision at the end.** The model writes JSON
  whose first field is the answer; its text is passed on piece by piece.
  When the reply is complete, a `checking` event says so. The whole reply
  is then checked as `generate_answer` checks it, and the
  `AnswerFinisher` decides. The client keeps the text only if the decision
  is `answer`, and the streamed text is then exactly the checked text: a
  missing end is sent, and any other difference abstains.
- **Citations after the decision.** They are sent only once the decision
  is `answer`, so the title of a Document behind an Answer the policy
  rejects never leaves the server.
- **Evidence as it is now.** Before the model is asked, the
  `CitationCheck` rereads every Evidence as the reader. If one is not
  visible, through a retrieval bug or a change since the search, the
  stream abstains like an empty search and the model never sees it, so
  none of its text can be streamed.
- **Citations as they are now.** Before the decision, the check rereads
  every cited Chunk again. If one fails, the Answer is `WITHDRAWN` and the
  stream ends with the same decision and message as an empty search. Text
  already streamed was written from Evidence the reader could see when
  the model was asked; the abstention replaces it but cannot unsend it.
  Citations are sent with the provenance the check read.
- **The usage once billed.** When the model's reply arrived, its usage is
  sent before the terminal event, also before an `error`, and also for a
  reply too long to be an Answer.
- **Always a terminal event.** A failure after the first byte, also an
  event the writer refuses, ends the stream with an `error` event.
- **Cancelled cleanly.** When the client goes away, the stream is
  cancelled or closed wherever it waits; it closes the model's stream at
  once, so the provider stops generating, and sends nothing more. The
  close is shielded from a second cancellation, so it is not cut short,
  and bounded by `CLOSE_SECONDS`, so a stuck close cannot hold it.
- **One record per stream.** "answer stream started" and "answer stream
  ended" hold the request ID, counts, tokens, timing, and how the stream
  ended: `completed`, `failed`, or `cancelled`, also when it ends before
  its first event. The end also holds the phase it ended in, its audit
  outcome, and what became of the partial text, as `stream_failures`
  defines them; for a completed abstention its reason; and, when the model
  replied, how its reply was judged (`answered`, `abstained`, `refused`,
  `invalid`) and the rule an invalid one broke, such as
  `invalid_citation`. Never text.
"""

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Protocol, cast
from uuid import UUID

import anyio

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.abstention import (
    AbstentionDecision,
    AbstentionPolicy,
    AbstentionReason,
    Decision,
    EvidenceSignals,
    Sufficiency,
    assess_evidence,
    decide,
)
from knowledge_service.answering.citations import Citation
from knowledge_service.answering.context_budget import (
    AllocatedContext,
    ContextBudget,
    allocate_context,
)
from knowledge_service.answering.events import AnswerEventWriter, InvalidEventStream
from knowledge_service.answering.evidence import EvidenceSet
from knowledge_service.answering.generation import (
    MAX_ANSWER_CHARACTERS,
    MAX_REPLY_CHARACTERS,
    AnswerDefect,
    AnswerOutcome,
    AnswerResult,
    InvalidAnswerRequest,
    ModelIdentity,
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
)
from knowledge_service.language import Language
from knowledge_service.model import ChatModelMalformedResponse

logger = logging.getLogger(__name__)

# The start of the model's JSON, up to the answer's opening quote.
_OPENING = re.compile(r'\s*\{\s*"answer"\s*:\s*"')
_LONGEST_OPENING = 64
# One piece of a JSON string: plain text, an escape, or its closing quote.
_PIECE = re.compile(r'[^"\\]+|\\u[0-9a-fA-F]{4}|\\["\\/bfnrt]|"')
_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
# How long closing the model's stream may take, even after the deadline.
CLOSE_SECONDS = 1.0
# The longest escape that may still be arriving: a surrogate pair.
_LONGEST_ESCAPE = 12


class StreamingAnswerModel(Protocol):
    """A chat model that streams its answer to an Evidence prompt."""

    @property
    def identity(self) -> ModelIdentity:
        """The provider and the pinned model."""
        ...

    def stream(
        self,
        prompt: EvidencePrompt,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> AsyncGenerator[str | ModelReply]:
        """Yield the reply's text in pieces as it arrives, then one `ModelReply`.

        The `ModelReply` holds the whole text and the billed usage. Failures
        raise a `ChatModelError`. Closing the generator early must close the
        provider's stream, so the provider stops generating.
        """
        ...


@dataclass(frozen=True, slots=True)
class FoundEvidence:
    """What retrieval found for one Question, and its signals."""

    evidence: EvidenceSet
    signals: EvidenceSignals


class EvidenceSource(Protocol):
    """Finds the Evidence the reader may see for one Question."""

    async def find(
        self, question: str, *, reader: AuthorizationContext, request_id: UUID
    ) -> FoundEvidence: ...


@dataclass(frozen=True, slots=True)
class FinishedAnswer:
    """The checked result and the policy's decision about it."""

    result: AnswerResult
    decision: AbstentionDecision


class CitationCheck(Protocol):
    """Rereads Evidence and Citations against the Documents as they are now.

    `CurrentCitations` is the implementation. `still_visible` says whether
    the reader may see every Evidence; `confirm` returns an Answer with
    current provenance, the Answer withdrawn, or anything else unchanged.
    """

    async def still_visible(
        self, evidence: EvidenceSet, *, reader: AuthorizationContext, request_id: UUID
    ) -> bool: ...

    async def confirm(
        self, result: AnswerResult, *, reader: AuthorizationContext, request_id: UUID
    ) -> AnswerResult: ...


class AnswerFinisher(Protocol):
    """Decides whether a checked result is shown to the client."""

    async def finish(
        self, result: AnswerResult, evidence: EvidenceSet, *, request_id: UUID
    ) -> FinishedAnswer: ...


class PolicyFinisher:
    """Check an Answer's claims, then let the abstention policy decide.

    Retrieval was already judged sufficient before the model was asked.
    """

    def __init__(
        self, judge: SupportJudge, policy: AbstentionPolicy, *, timeout_seconds: float
    ) -> None:
        if not positive_seconds(timeout_seconds):
            raise InvalidAnswerRequest("timeout_seconds must be a positive number")
        self._judge = judge
        self._policy = policy
        self._timeout_seconds = timeout_seconds

    async def finish(
        self, result: AnswerResult, evidence: EvidenceSet, *, request_id: UUID
    ) -> FinishedAnswer:
        if result.outcome is not AnswerOutcome.ANSWERED:
            decision = decide(Sufficiency.SUFFICIENT, result, None, None, self._policy)
            return FinishedAnswer(result, decision)
        report = await check_support(
            result,
            evidence,
            self._judge,
            request_id=request_id,
            timeout_seconds=self._timeout_seconds,
        )
        cited = EvidenceSet(
            tuple(
                item
                for citation in result.citations
                if (item := evidence.get(citation.evidence_id)) is not None
            )
        )
        decision = decide(Sufficiency.SUFFICIENT, result, cited, report, self._policy)
        return FinishedAnswer(result, decision)


@dataclass(frozen=True, slots=True)
class _Plan:
    """What `open` settled before the first byte."""

    request_id: UUID
    reader: AuthorizationContext
    deadline: float
    language: Language
    sufficiency: Sufficiency
    context: AllocatedContext | None


class AnswerStreamer:
    """Answer one Question as a stream of SSE events."""

    def __init__(
        self,
        source: EvidenceSource,
        model: StreamingAnswerModel,
        finisher: AnswerFinisher,
        policy: AbstentionPolicy,
        budget: ContextBudget,
        *,
        citations: CitationCheck,
        timeout_seconds: float,
    ) -> None:
        if not positive_seconds(timeout_seconds):
            raise InvalidAnswerRequest("timeout_seconds must be a positive number")
        self._source = source
        self._model = model
        self._citations = citations
        self._finisher = finisher
        self._policy = policy
        self._budget = budget
        self._timeout_seconds = timeout_seconds

    async def open(
        self, question: Question, *, reader: AuthorizationContext, request_id: UUID
    ) -> AnswerStream:
        """Find and judge the Evidence `reader` may see, then return the stream.

        Raises before any byte is sent: `AnswerDeadlineExceeded` when finding
        Evidence takes the whole deadline, the source's own errors, and the
        errors of `assess_evidence` and `allocate_context`. Its record ends
        here as `failed` or `cancelled` when it does.
        """
        span = _Span(request_id)
        try:
            if type(reader) is not AuthorizationContext:
                raise InvalidAnswerRequest("reader must be an AuthorizationContext")
            deadline = asyncio.get_running_loop().time() + self._timeout_seconds
            async with _before(deadline):
                found = await self._source.find(
                    question.text, reader=reader, request_id=request_id
                )
            if type(found) is not FoundEvidence:
                raise InvalidAnswerRequest("the source must return FoundEvidence")
            async with _before(deadline):
                visible = await self._citations.still_visible(
                    found.evidence, reader=reader, request_id=request_id
                )
            if type(visible) is not bool:
                raise InvalidAnswerRequest("the citation check must answer yes or no")
            if not visible:
                # Something the reader may not see: answer as if nothing was found.
                found = FoundEvidence(EvidenceSet(()), EvidenceSignals(0, None, None))
            sufficiency = assess_evidence(found.signals, self._policy)
            context = (
                allocate_context(question.text, found.evidence, self._budget)
                if sufficiency is Sufficiency.SUFFICIENT
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
            reader=reader,
            deadline=deadline,
            language=_language(question.text),
            sufficiency=sufficiency,
            context=context,
        )
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
                    answer = AnswerFieldReader()
                    parts = self._model.stream(
                        context.prompt,
                        request_id=plan.request_id,
                        timeout_seconds=self._timeout_seconds,
                    )
                    try:
                        while reply is None:
                            async with _before(plan.deadline):
                                part = await anext(parts)
                            if type(part) is not str:
                                reply = _reply(part)
                                continue
                            text = answer.feed(part)
                            if text:
                                span.sent(text)
                                yield writer.text_delta(text)
                    finally:
                        await _close(parts)
                    span.usage = reply.usage
                    # The model is done; checking its reply can take a while.
                    yield writer.checking()
                    finished = await self._finished(plan, context, reply, span)
                    span.reply = finished.result.outcome
                    span.defect = finished.result.defect
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failure = classify_failure(error)
                span.failure = type(error).__name__
            decision = None if failure is not None else self._decision(plan, finished)
            try:
                if (
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
                    span.reason = decision.reason
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
        context: AllocatedContext,
        reply: ModelReply,
        span: _Span,
    ) -> FinishedAnswer:
        result = reply_result(reply, context.evidence, context.report)
        streamed = "".join(span.text)
        if result.outcome is AnswerOutcome.ANSWERED and not result.text.startswith(
            streamed
        ):
            # The client holds text that is not the checked Answer.
            return FinishedAnswer(result, _abstain(self._policy))
        async with _before(plan.deadline):
            confirmed = await self._citations.confirm(
                result, reader=plan.reader, request_id=plan.request_id
            )
        if not _confirms(result, confirmed):
            raise InvalidAnswerRequest("the citation check must answer this result")
        result = confirmed
        async with _before(plan.deadline):
            finished = await self._finisher.finish(
                result, context.evidence, request_id=plan.request_id
            )
        if (
            type(finished) is not FinishedAnswer
            or finished.result is not result
            or type(finished.decision) is not AbstentionDecision
            or (
                finished.decision.decision is Decision.ANSWER
                and result.outcome is not AnswerOutcome.ANSWERED
            )
        ):
            raise InvalidAnswerRequest("the finisher must decide about this result")
        return finished

    def _decision(
        self, plan: _Plan, finished: FinishedAnswer | None
    ) -> AbstentionDecision:
        if finished is not None:
            return finished.decision
        # No model was asked: nothing found, too weak, or nothing fits.
        sufficiency = plan.sufficiency
        if sufficiency is Sufficiency.SUFFICIENT:
            sufficiency = Sufficiency.EMPTY
        return decide(sufficiency, None, None, None, self._policy)


class AnswerStream:
    """The events of one Answer, and the record that ends with them.

    Iterate it once. Closing it closes the events and the model's stream,
    and ends the record as `cancelled`, also when no event was asked for.
    """

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
            await self._events.aclose()
        finally:
            self._span.end()


class AnswerFieldReader:
    """Read the answer's text out of the model's JSON as it arrives.

    The text is passed on only while the reply starts with the answer
    field and its escapes are well formed; at the first surprise, reading
    stops, and the checked text is sent at the end instead.
    """

    def __init__(self) -> None:
        self._pending = ""
        self._opened = False
        self._stopped = False
        self._read = 0
        self._characters = 0

    def feed(self, chunk: str) -> str:
        self._read += len(chunk)
        if self._read > MAX_REPLY_CHARACTERS:
            # Too long to be an Answer; the check at the end says so, billed.
            self._stop()
        if self._stopped:
            return ""
        self._pending += chunk
        if not self._opened:
            opening = _OPENING.match(self._pending)
            if opening is None:
                if len(self._pending) > _LONGEST_OPENING:
                    self._stop()
                return ""
            self._opened = True
            self._pending = self._pending[opening.end() :]
        text = self._decoded()
        if self._characters + len(text) > MAX_ANSWER_CHARACTERS:
            self._stop()
            return ""
        self._characters += len(text)
        return text

    def _decoded(self) -> str:
        out: list[str] = []
        position = 0
        pending = self._pending
        while position < len(pending) and not self._stopped:
            piece = _PIECE.match(pending, position)
            if piece is None:
                # An escape may still be arriving; anything longer is wrong.
                if len(pending) - position > _LONGEST_ESCAPE or not pending[
                    position
                ].startswith("\\"):
                    self._stop()
                break
            value = piece.group()
            if value == '"':
                self._stop()
                break
            if not value.startswith("\\"):
                # JSON text can carry half a character; no Answer holds one.
                if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
                    self._stop()
                    break
                out.append(value)
            elif value.startswith("\\u"):
                code = int(value[2:], 16)
                if 0xDC00 <= code <= 0xDFFF:
                    self._stop()
                    break
                if 0xD800 <= code <= 0xDBFF:
                    low = _PIECE.match(pending, piece.end())
                    if low is None:
                        if len(pending) - piece.end() >= 6:
                            self._stop()
                        break
                    second = low.group()
                    low_code = int(second[2:], 16) if second.startswith("\\u") else -1
                    if not 0xDC00 <= low_code <= 0xDFFF:
                        self._stop()
                        break
                    out.append(
                        chr(0x10000 + (code - 0xD800) * 0x400 + low_code - 0xDC00)
                    )
                    position = low.end()
                    continue
                out.append(chr(code))
            else:
                out.append(_ESCAPES[value[1]])
            position = piece.end()
        self._pending = "" if self._stopped else pending[position:]
        return "".join(out)

    def _stop(self) -> None:
        self._stopped = True
        self._pending = ""


@dataclass(slots=True)
class _Span:
    """The record of one stream: counts, tokens, timing, how it ended."""

    request_id: UUID
    started_at: float = field(default_factory=time.perf_counter)
    text: list[str] = field(default_factory=list[str], repr=False)
    characters: int = 0
    deltas: int = 0
    citations: int = 0
    usage: Usage | None = None
    failure: str | None = None
    # Why the policy abstained, and which rule a reply broke: names only.
    reason: AbstentionReason | None = None
    reply: AnswerOutcome | None = None
    defect: AnswerDefect | None = None
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
        )

    def sent(self, text: str) -> None:
        self.text.append(text)
        self.characters += len(text)
        self.deltas += 1

    def end(self) -> None:
        if self.ended:
            return
        self.ended = True
        fields: dict[str, object] = {
            "request_id": str(self.request_id),
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
            "reason": None if self.reason is None else str(self.reason),
            "reply": None if self.reply is None else str(self.reply),
            "defect": None if self.defect is None else str(self.defect),
        }
        if self.usage is not None:
            fields["input_tokens"] = self.usage.input_tokens
            fields["output_tokens"] = self.usage.output_tokens
        level = logging.INFO if self.outcome == "completed" else logging.WARNING
        logger.log(level, "answer stream ended", extra={"answer_stream": fields})


async def _close(parts: AsyncGenerator[str | ModelReply]) -> None:
    """Close the model's stream, even while cancelled, within `CLOSE_SECONDS`."""
    # Shielded: a cancelled task would otherwise cut the model's own cleanup.
    with anyio.move_on_after(CLOSE_SECONDS, shield=True) as scope:
        try:
            await parts.aclose()
        except Exception as error:
            logger.warning(
                "model stream closed with an error",
                extra={"answer_stream": {"failure": type(error).__name__}},
            )
    if scope.cancelled_caught:
        logger.warning("model stream did not close in time")


@contextlib.asynccontextmanager
async def _before(deadline: float) -> AsyncGenerator[None]:
    """Run the block before `deadline`, or raise `AnswerDeadlineExceeded`."""
    scope = asyncio.timeout_at(deadline)
    expired = False
    try:
        async with scope:
            yield
    except TimeoutError:
        # Only the deadline's own timeout is ours; any other is a bug.
        if not scope.expired():
            raise
        expired = True
    if expired:
        raise AnswerDeadlineExceeded()


def _reply(part: object) -> ModelReply | None:
    """The final reply, None for a piece of text, or a malformed stream."""
    if type(part) is str:
        return None
    if not well_formed_reply(part):
        raise ChatModelMalformedResponse("the model streamed something else")
    return cast(ModelReply, part)


def _confirms(result: AnswerResult, confirmed: object) -> bool:
    """Whether `confirmed` is a check of `result`, not some other result.

    Anything but an Answer comes back unchanged. An Answer comes back
    withdrawn, or with the same text and the same Evidence cited in the
    same order, only its provenance read again.
    """
    if type(confirmed) is not AnswerResult:
        return False
    if result.outcome is not AnswerOutcome.ANSWERED:
        return confirmed is result
    if confirmed.outcome is AnswerOutcome.WITHDRAWN:
        return confirmed.citations == () and confirmed.text == ""
    if (
        confirmed.outcome is not AnswerOutcome.ANSWERED
        or confirmed.text != result.text
        or type(confirmed.citations) is not tuple
        or len(confirmed.citations) != len(result.citations)
    ):
        return False
    # Only the provenance may be read again; the same Evidence must be
    # cited, as the same Chunk of the same Document, Source, and version.
    return all(
        type(new) is Citation and _identity(new) == _identity(old)
        for new, old in zip(confirmed.citations, result.citations, strict=True)
    )


def _identity(citation: Citation) -> tuple[object, ...]:
    return (
        citation.evidence_id,
        citation.chunk_id,
        citation.document_id,
        citation.source_id,
        citation.content_version,
    )


def _abstain(policy: AbstentionPolicy) -> AbstentionDecision:
    return AbstentionDecision(
        decision=Decision.ABSTAIN,
        reason=AbstentionReason.ANSWER_FAILED,
        policy_version=policy.version,
    )


def _language(text: str) -> Language:
    """Ukrainian when the Question has more Cyrillic letters than Latin ones."""
    cyrillic = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    return Language.UKRAINIAN if cyrillic > latin else Language.ENGLISH
