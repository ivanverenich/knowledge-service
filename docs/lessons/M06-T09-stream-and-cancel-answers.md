# M06-T09 — Stream and cancel Answers

Source task: [M06-T09](../curriculum/milestones/M06-answers-and-conversations/M06-T09-stream-and-cancel-answers.md)

## What you will do

This lesson sends an Answer to the client piece by piece, while the model is still writing it, and stops all work the moment the client leaves. M06-T08 fixed what the events look like. Now a real HTTP endpoint sends them: it finds the Evidence, asks the model through a stream, passes the text on as it arrives, and ends with the policy's decision from M06-T07. A user sees the first words in a second instead of waiting for the whole Answer, and a closed browser tab no longer leaves the model generating, and billing, for nobody.

- `AnswerStreamer` runs one streamed Answer: Evidence first, then the model's stream, then the checks and the decision.
- `AnswerFieldReader` pulls the answer's text out of the model's JSON as it arrives.
- `OpenAIAnswerModel.stream` asks the Responses API for a stream and closes it on every exit.
- `EventStreamResponse` and `POST /v1/answer/stream` send the events over HTTP and notice at once when the client leaves.

| The task asks for | Where it happens |
|---|---|
| Expose SSE | Step 5: `POST /v1/answer/stream` returns `text/event-stream` |
| Backpressure | Step 3: each event is made only when the server asks for the next one; nothing is queued |
| Client cancellation | Step 5: `EventStreamResponse` listens for the disconnect and cancels the stream; Step 3 closes the model's stream |
| Deadline handling | Step 3: one deadline covers the search, the model's stream, and the checks; before the first byte it is an HTTP error, after it an `error` event |
| Final usage | Step 3: the billed usage is sent before the terminal event, also before an `error` |
| Cleanup of provider streams | Step 4: the OpenAI stream is closed on every exit, shielded and bounded |
| An HTTP test disconnects midstream and proves upstream work and spans terminate | Step 6: `test_a_client_that_leaves_midstream_stops_the_model_and_the_stream` |
| No seeded text in telemetry | Step 6: a planted marker never reaches a log record or a repr |

This lesson includes coding: Steps 2 to 6 write code. Steps 1, 7, and 8 do not. No model key, network, or database is needed; one live test runs only with `OPENAI_API_KEY`.

Terms used in this lesson:

- **Streaming:** sending a response in parts while it is still being made, like reading a letter page by page as it is written.
- **Backpressure:** a slow reader slows the writer down instead of letting unsent data pile up. Here the stream makes the next event only when the server is ready to send it, so a slow client slows the model's stream.
- **Cancellation:** stopping work that nobody needs any more. In Python's asyncio, a task is cancelled by raising `CancelledError` at the point where it waits.
- **Span:** one record of a piece of work from its start to its end. Here it is a pair of log records, "answer stream started" and "answer stream ended", until M10 brings OpenTelemetry.
- **Before and after the headers:** once the first byte of a response is sent, its status code is fixed at 200. A failure before that can still be a 403 or a 503; a failure after it can only be an `error` event.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **What already exists.** `answering/events.py` writes and reads the events. `answering/generation.py` checks a model's reply into one outcome. `answering/support.py` and `answering/abstention.py` check the claims and decide. `app.py` has `POST /v1/answer`, which answers in one piece.
- **Search before the first byte.** `AnswerStreamer.open` finds and judges the Evidence before the response starts. A denied search is then still a 403, and a search that takes the whole deadline is still a 503. Only the model's part streams.
- **Pulled, not pushed.** The stream is an async generator: it makes the next event only when the server asks for it, and the server asks only after the last one was written. There is no queue between the model and the client, so a slow client cannot make memory grow.
- **Text from inside JSON.** The model answers in the JSON shape of M06-T04, and the answer is its first field. `AnswerFieldReader` reads that string as it arrives and decodes its escapes, so `\n` becomes a line break and `\ud83d\ude00` an emoji, even when a chunk ends in the middle of one. At the first surprise it stops and lets the end send the text.
- **The streamed text is the checked text.** When the reply is complete, it is checked exactly as `generate_answer` checks it. If the checked answer starts with what was streamed, the rest is sent. If it does not, the client holds text that is not the Answer, so the stream abstains, and the client throws that text away.
- **The decision before the Citations.** Text may stream before the decision; Citations never do. They are sent only after the `AnswerFinisher` says `answer`, as M06-T08 requires. `PolicyFinisher` checks the claims and asks `decide`. Rechecking the Citations against the database (M06-T05) needs the reader's identity, which arrives in M07.
- **One deadline.** `timeout_seconds` starts when the request does and covers the search, every wait for the model, and the checks. Each wait gets the time that is left, never a fresh timeout.
- **Leaving is noticed at once.** Under ASGI 2.4, Starlette's own streaming response notices a disconnect only when its next write fails. A stream waiting on a silent model would then run on. `EventStreamResponse` always listens for the disconnect, cancels the writing once, and then closes the stream, which closes the model's stream. A client that leaves during the search cancels the search too.
- **Closing cannot be cut short or hang.** The model's stream is closed shielded from a second cancellation, so a provider's own cleanup, such as closing its connection, finishes. The close is also bounded by `CLOSE_SECONDS`, so a stuck close cannot hold the response open.
- **Every stream ends in a terminal event.** After the first byte, any failure, even an event the writer refuses, ends with an `error` event, and the billed usage is sent before it when there is one.
- **What the review found.** An adversarial review attacked the prototype with 4,000 random replies, disconnects at every stage under ASGI 2.3 and 2.4, and fake and real HTTP streams. It found no way for a client to keep text that is not the checked Answer, and no Citation sent before an abstention. It found these problems, each fixed with a test that fails without the fix:
  - An event the writer refused after the decision, such as an unreadable usage or a malformed decision, escaped the stream, so the response ended with no terminal event. Now those events sit in their own `try`, and a refusal ends in an `error` event.
  - Closing a model whose cleanup hung held the stream open for as long as the cleanup took. Now every close is bounded by `CLOSE_SECONDS`.
  - A client that left during the search did not stop it, and the stream left no record. Now the search is cancelled, and the record ends `cancelled`, also for a stream closed before its first event.
  - A cancelled anyio scope cancels again at every wait, so a model's own cleanup was cut short. Now the response cancels the writing once with plain asyncio, and every close is shielded.
  - A reply too long to be an Answer stopped the stream before its usage arrived. Now the reader stops reading the text, the reply is checked as invalid, and its usage is sent.
  - Half a character in the raw JSON ended the stream in an internal error. Now the reader stops at it, and the stream abstains.
  - The record's repr held the streamed text. Now it is hidden.
- **Where the code lives.** New `answering/streaming.py`; a stream method in `answering/openai_answering.py`; `reply_result` taken out of `generate_answer` in `answering/generation.py`; the endpoint in `app.py`.
- **Known limits.** Nothing calls `confirm_citations` yet, because it needs the reader's identity (M07); nothing provides a real `EvidenceSource` either, so the endpoint answers only when the app is built with one. The usage event holds the answering model's tokens, not a model judge's. A model whose own cleanup blocks after it is cancelled can outlast the deadline by that cleanup; the OpenAI adapter's close is bounded. The live test runs only with `OPENAI_API_KEY`. M06-T13 maps each failure to its exact retry advice.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before any code exists, write what must be true when the lesson is done. Step 7 checks the predictions.

Create `docs/evidence/M06-T09-stream-and-cancel-answers.md`:

```markdown
# M06-T09 — Stream and cancel Answers evidence

## Invariant

`POST /v1/answer/stream` finds and judges Evidence before the first byte,
so its failures are plain HTTP errors. After it, the response is SSE in
the M06-T08 order: the answer's text as the model writes it, then, only
after the policy decides `answer`, the Citations; the billed usage before
the terminal event; and always exactly one terminal event, `completion`
or `error`. A client keeps text only when it is exactly the checked
Answer. One deadline covers the search, the model's stream, and the
checks. Each event is made only when the server asks for it. When the
client leaves, at any point, the work is cancelled, the model's stream is
closed within `CLOSE_SECONDS`, and the stream's record ends `cancelled`.
Records hold no Question, Evidence, or answer text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.streaming"`
fails with `ModuleNotFoundError`.

## Predictions

- What a client receives when the model has written half its Answer and the deadline runs out: ...
- What happens to the model's stream when the browser tab is closed while the model is still thinking, and how a test can prove it: ...
- How many tests fail if the endpoint uses Starlette's plain `StreamingResponse` instead of `EventStreamResponse`: ...

## Design

## Verification

## Decision

## Reflection
```

Check: the file exists, the failing check fails, and every prediction is a full sentence Step 7 can prove wrong.

### Step 2 — Coding step: share how a reply is checked

This step takes the code that turns a model's reply into one outcome out of `generate_answer`, so streaming checks a reply exactly the same way.

**In plain words:** Today the check of a finished reply lives inside the function that also calls the model. Streaming gets its reply another way, piece by piece, but must judge it by the same rules. So the check moves into its own function that both use, and nothing about its result changes.

| Function | What it does |
|---|---|
| `reply_result` | Turns one reply into `REFUSED`, `INVALID` with its defect, `ABSTAINED`, or `ANSWERED` with Citations, keeping the usage |
| `generate_answer` | Calls the model as before, then uses `reply_result` |

In `src/knowledge_service/answering/generation.py`:

Find:

```python
    reply = await _ask(model, allocated.prompt, request_id, timeout_seconds)
    if reply.refused:
        result = _result(AnswerOutcome.REFUSED, context, usage=reply.usage)
    else:
        try:
            checked = checked_answer(reply.text, allocated.evidence)
        except InvalidAnswer as error:
            result = _result(
                AnswerOutcome.INVALID, context, usage=reply.usage, defect=error.defect
            )
        else:
            result = AnswerResult(
                outcome=(
                    AnswerOutcome.ABSTAINED
                    if checked.abstained
                    else AnswerOutcome.ANSWERED
                ),
                text=checked.text,
                reason=checked.reason,
                citations=checked.citations,
                defect=None,
                usage=reply.usage,
                context=context,
            )
    _log(request_id, model, result)
    return result

```

Replace with:

```python
    reply = await _ask(model, allocated.prompt, request_id, timeout_seconds)
    result = reply_result(reply, allocated.evidence, context)
    _log(request_id, model, result)
    return result


def reply_result(
    reply: ModelReply, evidence: EvidenceSet, context: ContextReport
) -> AnswerResult:
    """The one outcome of a reply to a prompt built from `evidence`."""
    if reply.refused:
        return _result(AnswerOutcome.REFUSED, context, usage=reply.usage)
    try:
        checked = checked_answer(reply.text, evidence)
    except InvalidAnswer as error:
        return _result(
            AnswerOutcome.INVALID, context, usage=reply.usage, defect=error.defect
        )
    return AnswerResult(
        outcome=(
            AnswerOutcome.ABSTAINED if checked.abstained else AnswerOutcome.ANSWERED
        ),
        text=checked.text,
        reason=checked.reason,
        citations=checked.citations,
        defect=None,
        usage=reply.usage,
        context=context,
    )

```

Check:

```bash
uv run pytest tests/test_generation.py -q
```

Expected: 51 passed, the same as before.

### Step 3 — Coding step: the streamer

This step writes the module that runs one streamed Answer from the search to the last event.

**In plain words:** Before anything is sent, it finds the Evidence and judges whether it is enough; any failure there is still a normal HTTP error. Then it asks the model for a stream and passes on the answer's words as they arrive, one event at a time, only as fast as the client takes them. When the model is done, it checks the whole reply, lets the policy decide, and sends the Citations and the cost only then. If the time runs out or something fails, the stream ends with an error event; if the client leaves, it closes the model's stream and stops.

| Part | What it holds or does |
|---|---|
| `StreamingAnswerModel` | The port: pieces of the reply's text, then one `ModelReply` with the whole text and usage; closing it early closes the provider's stream |
| `EvidenceSource`, `FoundEvidence` | The port that finds Evidence for a Question, with retrieval's signals |
| `AnswerFinisher`, `FinishedAnswer`, `PolicyFinisher` | Who decides about a checked result; the default checks the claims and asks `decide` |
| `AnswerStreamer.open` | The work before the first byte; returns an `AnswerStream` |
| `AnswerStreamer._events` | The events: deltas, then the decision, Citations, usage, and one terminal event |
| `AnswerStream` | The events of one Answer; closing it ends the record, even before the first event |
| `AnswerFieldReader` | Reads the answer's string out of the model's JSON as it arrives |
| `_Span`, `_close`, `_before` | The record of one stream; the shielded, bounded close; one deadline per wait |

Create `src/knowledge_service/answering/streaming.py`:

```python
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
  The whole reply is then checked as `generate_answer` checks it, and the
  `AnswerFinisher` decides. The client keeps the text only if the decision
  is `answer`, and the streamed text is then exactly the checked text: a
  missing end is sent, and any other difference abstains.
- **Citations after the decision.** They are sent only once the decision
  is `answer`, so the title of a Document behind an Answer the policy
  rejects never leaves the server.
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
  its first event. Never text.
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

    async def find(self, question: str, *, request_id: UUID) -> FoundEvidence: ...


@dataclass(frozen=True, slots=True)
class FinishedAnswer:
    """The checked result and the policy's decision about it."""

    result: AnswerResult
    decision: AbstentionDecision


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
        timeout_seconds: float,
    ) -> None:
        if not positive_seconds(timeout_seconds):
            raise InvalidAnswerRequest("timeout_seconds must be a positive number")
        self._source = source
        self._model = model
        self._finisher = finisher
        self._policy = policy
        self._budget = budget
        self._timeout_seconds = timeout_seconds

    async def open(self, question: Question, *, request_id: UUID) -> AnswerStream:
        """Find and judge the Evidence, then return the stream of events.

        Raises before any byte is sent: `AnswerDeadlineExceeded` when finding
        Evidence takes the whole deadline, the source's own errors, and the
        errors of `assess_evidence` and `allocate_context`. Its record ends
        here as `failed` or `cancelled` when it does.
        """
        span = _Span(request_id)
        try:
            deadline = asyncio.get_running_loop().time() + self._timeout_seconds
            async with _before(deadline):
                found = await self._source.find(question.text, request_id=request_id)
            if type(found) is not FoundEvidence:
                raise InvalidAnswerRequest("the source must return FoundEvidence")
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
            span.failure = type(error).__name__
            span.outcome = "failed"
            span.end()
            raise
        plan = _Plan(
            request_id=request_id,
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
                    finished = await self._finished(plan, context, reply, span)
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
    outcome: str = "cancelled"
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
```

Three details matter here. Every `yield` sits outside `_before`, the deadline wrapper, because a timeout must never span the time the client takes to read. The model's stream is closed in a `finally`, so a deadline, an error, a cancellation, and a closed generator all close it. And the events after the decision sit in their own `try`, so even an event the writer refuses still ends in an `error` event.

Check:

```bash
uv run python -c "import knowledge_service.answering.streaming"
uv run pyright src/knowledge_service/answering/streaming.py
```

Expected: the import prints nothing, and pyright reports 0 errors.

### Step 4 — Coding step: stream from OpenAI

This step adds the provider side: a method that asks the Responses API for a stream.

**In plain words:** The adapter already sends one request and waits for the whole reply. Now it can also ask for the reply in pieces, pass each piece of text on, and at the end return the whole reply with its billed tokens, read by the same helpers as before. Whatever happens, it closes the connection to the provider, so the provider stops generating.

| Part | What it does |
|---|---|
| `_request` | The arguments of one request, shared by `generate` and `stream`, so both send the same thing |
| `stream` | Yields each `response.output_text.delta`, then one `ModelReply` from the final response; maps failures to the port's errors |
| `_CLOSE_SECONDS` | How long closing the provider's stream may take, shielded from a second cancellation |

In `src/knowledge_service/answering/openai_answering.py`:

**1.** Find:

```python
import time
from decimal import Decimal
from typing import Self, cast
from uuid import UUID

import openai
from openai import AsyncOpenAI, omit
from openai.types.shared_params import Reasoning, ReasoningEffort
```

Replace with:

```python
import time
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any, Self, cast
from uuid import UUID

import anyio
import httpx
import openai
from openai import AsyncOpenAI, AsyncStream, omit
from openai.types.responses import Response, ResponseStreamEvent
from openai.types.shared_params import Reasoning, ReasoningEffort
```

**2.** Find:

```python
PROVIDER = "openai"

```

Replace with:

```python
PROVIDER = "openai"
# How long closing a stream may take, even for a cancelled caller.
_CLOSE_SECONDS = 1.0
# The events that end a stream with the whole response and its usage.
_FINAL_EVENTS = frozenset(
    {"response.completed", "response.incomplete", "response.failed"}
)

```

**3.** Find:

```python
        deadline = self._deadline(timeout_seconds)
        effort = self._settings.reasoning_effort
        started_at = time.perf_counter()
```

Replace with:

```python
        deadline = self._deadline(timeout_seconds)
        started_at = time.perf_counter()
```

**4.** Find:

```python
            async with asyncio.timeout(deadline):
                response = await self._client.responses.create(
                    model=self._settings.model,
                    instructions=prompt.instructions,
                    input=prompt.input,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "answer",
                            "strict": True,
                            "schema": prompt.response_schema(),
                        }
                    },
                    max_output_tokens=self._settings.max_output_tokens,
                    store=False,
                    timeout=deadline,
                    extra_headers={"X-Request-ID": str(request_id)},
                    reasoning=omit if effort is None else Reasoning(effort=effort),
                )
```

Replace with:

```python
            async with asyncio.timeout(deadline):
                # Unpacked arguments hide which overload is called: one
                # without streaming, which returns a Response.
                response = cast(
                    Response,
                    await self._client.responses.create(
                        **self._request(prompt, request_id, deadline)
                    ),
                )
```

**5.** Find:

```python
        return ModelReply(text=text, refused=refused, usage=usage)

```

Replace with:

```python
        return ModelReply(text=text, refused=refused, usage=usage)

    async def stream(
        self,
        prompt: EvidencePrompt,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> AsyncGenerator[str | ModelReply]:
        """Stream the reply's text, then yield one `ModelReply` with its usage.

        The caller's deadline bounds the whole stream. Closing the generator
        early closes the HTTP response, so the provider stops generating.
        """
        if type(prompt) is not EvidencePrompt:
            raise InvalidAnswerRequest("prompt must be an EvidencePrompt")
        deadline = self._deadline(timeout_seconds)
        started_at = time.perf_counter()
        events: AsyncStream[ResponseStreamEvent] | None = None
        final: object = None
        failure: ChatModelError | None = None
        usage: Usage | None = None
        outcome = "cancelled"
        try:
            try:
                events = await self._client.responses.create(
                    **self._request(prompt, request_id, deadline), stream=True
                )
                async for event in events:
                    if event.type == "response.output_text.delta" and event.delta:
                        yield event.delta
                    elif event.type in _FINAL_EVENTS:
                        final = getattr(event, "response", None)
                        break
                    elif event.type == "error":
                        failure = ChatModelUnavailable()
                        break
            except (TimeoutError, openai.APIError) as error:
                failure = _translated(error)
            except ValueError:
                failure = ChatModelMalformedResponse("the stream was not readable")
            except httpx.HTTPError:
                # The connection broke while the stream was being read.
                failure = ChatModelUnavailable()
            latency_ms = _since(started_at)
            if failure is None and final is None:
                failure = ChatModelMalformedResponse("the stream ended without a reply")
            if failure is not None:
                outcome = type(failure).__name__
                # Raised outside the SDK's except block: its error holds the request.
                raise failure
            usage = self._usage(final, latency_ms)
            text, refused = _reply(final)
            outcome = (
                "refused"
                if refused
                else "succeeded"
                if text is not None
                else "unreadable"
            )
            reply = ModelReply(text=text, refused=refused, usage=usage)
        except ChatModelError as error:
            outcome = type(error).__name__
            raise
        finally:
            if events is not None:
                # Shielded: a cancelled caller must not cut the connection's
                # close short; bounded, so a stuck close cannot hang it.
                with anyio.move_on_after(_CLOSE_SECONDS, shield=True):
                    await events.close()
            _log(request_id, self._identity, _since(started_at), outcome, usage)
        yield reply

    def _request(
        self, prompt: EvidencePrompt, request_id: UUID, deadline: float
    ) -> dict[str, Any]:
        """The arguments of one Responses API request for this prompt."""
        effort = self._settings.reasoning_effort
        return {
            "model": self._settings.model,
            "instructions": prompt.instructions,
            "input": prompt.input,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "answer",
                    "strict": True,
                    "schema": prompt.response_schema(),
                }
            },
            "max_output_tokens": self._settings.max_output_tokens,
            "store": False,
            "timeout": deadline,
            "extra_headers": {"X-Request-ID": str(request_id)},
            "reasoning": omit if effort is None else Reasoning(effort=effort),
        }

```

Check:

```bash
uv run pytest tests/test_openai_answering.py -q
uv run pyright src/knowledge_service/answering/openai_answering.py
```

Expected: 29 passed and 1 deselected, the same as before, and 0 errors.

### Step 5 — Coding step: the endpoint

This step puts the stream on HTTP.

**In plain words:** A new address takes a Question and answers with a stream of events. Before the first byte, it waits for the search, and stops it if the client leaves. After that, a special response sends the events and listens for the client leaving the whole time; when it does, it stops the stream and makes sure the model's stream is closed.

| Part | What it does |
|---|---|
| `EventStreamResponse` | Sends the events, listens for the disconnect, cancels the writing once, then closes the stream, each wait bounded |
| `_unless_client_leaves` | Runs `open` but cancels it when the client leaves first; its errors stay plain HTTP errors |
| `POST /v1/answer/stream` | 503 without a streamer, an HTTP error for a failure before the first byte, otherwise the events |

In `src/knowledge_service/app.py`:

**1.** Find:

```python
import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from knowledge_service.contracts import Answer, ErrorResponse, Question
```

Replace with:

```python
import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Coroutine
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

**2.** Find:

```python

def create_app(
```

Replace with:

```python

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
        # Under ASGI 2.4, a write after the client left raises OSError.
        with contextlib.suppress(OSError):
            await self.stream_response(send)


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
```

**3.** Find:

```python
    workflow: AnswerWorkflow | None = None,
) -> FastAPI:
```

Replace with:

```python
    workflow: AnswerWorkflow | None = None,
    streamer: AnswerStreamer | None = None,
) -> FastAPI:
```

**4.** Find:

```python

    return app
```

Replace with:

```python

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
```

The response cancels the writing task once, with plain asyncio, instead of with a cancelled anyio scope. An anyio scope cancels again at every wait inside it, which would cut the model's own cleanup short. A single cancellation lets that cleanup finish, and the bounded wait stops it from hanging.

Check:

```bash
uv run pyright src/knowledge_service/app.py
uv run pytest tests/test_app.py -q
```

Expected: 0 errors, and the existing app tests pass as before.

### Step 6 — Coding step: tests for streaming, deadlines, and leaving

This step proves each promise, over the streamer and over HTTP.

**In plain words:** The tests use a fake model that streams a reply in small pieces, can go quiet at any point, and records whether its stream was closed and whether it ever finished. They read every stream back with the M06-T08 reader. The HTTP tests call the app the way a server does, and play a client that leaves in the middle, during the search, or while the server is stuck writing.

| Test | What it proves |
|---|---|
| `test_a_client_rebuilds_the_checked_answer_from_the_stream` | Success: text, Citations, usage, and completion, in order |
| `test_text_reaches_the_client_before_the_model_has_finished` | Text streams while the model is still working |
| `test_streamed_text_that_is_not_the_checked_answer_abstains` | The highest-risk edge: the client never keeps text that is not the checked Answer |
| `test_an_unsupported_answer_is_withdrawn_and_sends_no_citation` | Citations only after an `answer` decision |
| `test_the_deadline_ends_the_stream_with_an_error_and_closes_the_model` | Deadline: an `error` event, and the model's stream closed |
| `test_a_slow_policy_check_ends_in_an_error_after_the_usage` | The billed usage comes before the `error` |
| `test_a_failure_before_the_first_byte_is_a_plain_http_error` | Typed failure: 403 and 503 before the headers |
| `test_a_client_that_leaves_midstream_stops_the_model_and_the_stream` | The task's HTTP test, under ASGI 2.3 and 2.4: the model never finishes, its stream is closed, and the record ends `cancelled` |
| `test_a_client_that_leaves_during_the_search_cancels_it` | Leaving before the first byte cancels the search |
| `test_the_stream_record_holds_no_text` | A planted marker never reaches a log record |
| `test_the_answer_field_is_decoded_however_the_json_is_cut` | Fuzz: any text, cut anywhere, decodes to a prefix of the answer |
| `test_a_cancelled_caller_still_closes_the_provider_stream` | The OpenAI stream is closed even inside a cancelled scope |

Create `tests/test_streaming.py`:

```python
"""Streaming an Answer: deltas, the decision, the deadline, and cancellation."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from starlette.types import Message

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.abstention import (
    POLICY_VERSION,
    AbstentionDecision,
    AbstentionPolicy,
    AbstentionReason,
    Decision,
    EvidenceSignals,
)
from knowledge_service.answering.events import (
    AnswerEventReader,
    StreamedAnswer,
    StreamOutcome,
)
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import ModelIdentity, ModelReply
from knowledge_service.answering.openai_answering import AnswerSettings
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.streaming import (
    CLOSE_SECONDS,
    AnswerFieldReader,
    AnswerFinisher,
    AnswerStreamer,
    FinishedAnswer,
    FoundEvidence,
    PolicyFinisher,
    _Span,  # pyright: ignore[reportPrivateUsage]
)
from knowledge_service.answering.support import LexicalSupportJudge
from knowledge_service.app import create_app
from knowledge_service.contracts import Question, Usage
from knowledge_service.errors import AnswerDeadlineExceeded, AuthorizationDenied
from knowledge_service.model import ChatModelError, ChatModelRateLimited
from knowledge_service.retrieval.reranking import RerankerIdentity

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000050")
QUESTION = Question(text="How many days of annual leave do I get?")
EVIDENCE = build_evidence(
    [candidate(1, text="Every employee receives 24 days of leave.")]
)
CITED = EVIDENCE.items[0].evidence_id.value
POLICY = AbstentionPolicy(reranker=None, min_top_score=None, min_supported_share=1.0)
SIGNALS = EvidenceSignals(eligible=1, top_score=None, reranker=None)
USAGE = Usage(
    provider="fake",
    model="fake-1",
    latency_ms=5,
    input_tokens=300,
    output_tokens=30,
    total_tokens=330,
)
ANSWER = "Every employee receives 24 days of leave."


def reply_json(answer: str = ANSWER, **fields: Any) -> str:
    body = {"answer": answer, "citations": [CITED], "abstained": False, "reason": None}
    body.update(fields)
    return json.dumps(body)


def pieces(text: str, size: int = 7) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


class Source:
    """Return fixed Evidence, or fail, or wait."""

    def __init__(
        self,
        found: object = None,
        *,
        error: BaseException | None = None,
        wait: bool = False,
    ) -> None:
        self.found = FoundEvidence(EVIDENCE, SIGNALS) if found is None else found
        self.error = error
        self.wait = wait
        self.cancelled = False

    async def find(self, question: str, *, request_id: UUID) -> FoundEvidence:
        if self.wait:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if self.error is not None:
            raise self.error
        return self.found  # type: ignore[return-value]


class Model:
    """Stream `parts`, then a ModelReply of `final`; record how it ended.

    `hold` waits before that part until `release` is set; `error` is
    raised instead of the reply.
    """

    identity = ModelIdentity(provider="fake", model="fake-1")

    def __init__(
        self,
        parts: list[str] | None = None,
        *,
        final: str | None = None,
        hold: int | None = None,
        error: ChatModelError | None = None,
        close_delay: float = 0,
        usage: Usage = USAGE,
    ) -> None:
        self.final = reply_json() if final is None else final
        self.parts = pieces(self.final) if parts is None else parts
        self.hold = hold
        self.error = error
        self.close_delay = close_delay
        self.usage = usage
        self.release = asyncio.Event()
        self.waiting = asyncio.Event()
        self.calls = 0
        self.closed = False
        self.finished = False

    async def stream(
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> AsyncGenerator[str | ModelReply]:
        self.calls += 1
        try:
            for index, part in enumerate(self.parts):
                if index == self.hold:
                    self.waiting.set()
                    await self.release.wait()
                yield part
            if self.error is not None:
                raise self.error
            yield ModelReply(text=self.final, refused=False, usage=self.usage)
            self.finished = True
        finally:
            # A provider's close awaits too, such as closing its connection.
            if self.close_delay:
                await asyncio.sleep(self.close_delay)
            self.closed = True


class SlowFinisher:
    """Never decides."""

    async def finish(
        self, result: Any, evidence: Any, *, request_id: UUID
    ) -> FinishedAnswer:
        await asyncio.Event().wait()
        raise AssertionError


def streamer(
    model: Model,
    *,
    source: Source | None = None,
    finisher: AnswerFinisher | None = None,
    policy: AbstentionPolicy = POLICY,
    timeout_seconds: float = 5.0,
) -> AnswerStreamer:
    return AnswerStreamer(
        source or Source(),
        model,
        finisher or PolicyFinisher(LexicalSupportJudge(), policy, timeout_seconds=5),
        policy,
        AnswerSettings().budget,
        timeout_seconds=timeout_seconds,
    )


async def chunks(
    answering: AnswerStreamer, question: Question = QUESTION
) -> list[bytes]:
    events = await answering.open(question, request_id=REQUEST_ID)
    return [chunk async for chunk in events]


def rebuilt(sent: list[bytes]) -> StreamedAnswer:
    reader = AnswerEventReader()
    for chunk in sent:
        reader.feed(chunk)
    return reader.close()


def names(sent: list[bytes]) -> list[str]:
    return [chunk.split(b"\n")[1].decode().removeprefix("event: ") for chunk in sent]


# --- A complete stream ---------------------------------------------------------


async def test_a_client_rebuilds_the_checked_answer_from_the_stream() -> None:
    sent = await chunks(streamer(Model()))
    result = rebuilt(sent)

    assert result.outcome is StreamOutcome.ANSWERED
    assert result.text == ANSWER
    assert [c.evidence_id for c in result.citations] == [CITED]
    assert result.usage is not None
    assert result.usage.total_tokens == 330
    assert names(sent)[:2] == ["metadata", "text_delta"]
    assert names(sent)[-3:] == ["citation", "usage", "completion"]


async def test_text_reaches_the_client_before_the_model_has_finished() -> None:
    model = Model(hold=4)
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)

    first = [await anext(events) for _ in range(4)]
    assert names(first) == ["metadata", "text_delta", "text_delta", "text_delta"]
    assert not model.finished
    model.release.set()
    rest = [chunk async for chunk in events]

    assert rebuilt(first + rest).text == ANSWER


async def test_an_unsupported_answer_is_withdrawn_and_sends_no_citation() -> None:
    sent = await chunks(streamer(Model(final=reply_json("You get 99 days and a car."))))
    result = rebuilt(sent)

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.reason is AbstentionReason.UNSUPPORTED
    assert result.text == ""
    assert "citation" not in names(sent)
    assert result.usage is not None


async def test_the_models_own_abstention_abstains() -> None:
    final = reply_json("", citations=[], abstained=True, reason="Not in the documents.")

    result = rebuilt(await chunks(streamer(Model(final=final))))

    assert result.reason is AbstentionReason.INSUFFICIENT_EVIDENCE


async def test_no_evidence_abstains_without_asking_the_model() -> None:
    model = Model()
    empty = FoundEvidence(build_evidence([]), EvidenceSignals(0, None, None))

    sent = await chunks(streamer(model, source=Source(empty)))

    assert model.calls == 0
    assert names(sent) == ["metadata", "completion"]
    assert rebuilt(sent).reason is AbstentionReason.NO_EVIDENCE


async def test_weak_evidence_abstains_without_asking_the_model() -> None:
    reranker = RerankerIdentity(provider="local", model="bge-reranker-v2-m3")
    policy = AbstentionPolicy(reranker, min_top_score=0.5, min_supported_share=1.0)
    weak = FoundEvidence(EVIDENCE, EvidenceSignals(1, 0.1, reranker))
    model = Model()

    sent = await chunks(streamer(model, source=Source(weak), policy=policy))

    assert model.calls == 0
    assert rebuilt(sent).reason is AbstentionReason.WEAK_EVIDENCE


async def test_a_ukrainian_question_gets_a_ukrainian_abstention() -> None:
    empty = FoundEvidence(build_evidence([]), EvidenceSignals(0, None, None))
    question = Question(text="Скільки днів відпустки я маю?")

    sent = await chunks(streamer(Model(), source=Source(empty)), question)

    assert rebuilt(sent).message == (
        "Я не знайшов документів, які відповідають на це запитання."
    )


# --- The streamed text is the checked text -------------------------------------


async def test_a_reply_in_another_key_order_sends_the_text_at_the_end() -> None:
    final = json.dumps(
        {"citations": [CITED], "abstained": False, "reason": None, "answer": ANSWER}
    )

    sent = await chunks(streamer(Model(final=final)))

    assert names(sent) == [
        "metadata",
        "text_delta",
        "citation",
        "usage",
        "completion",
    ]
    assert rebuilt(sent).text == ANSWER


async def test_streamed_text_that_is_not_the_checked_answer_abstains() -> None:
    model = Model(parts=pieces(reply_json("You get 99 days.")), final=reply_json())

    result = rebuilt(await chunks(streamer(model)))

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.reason is AbstentionReason.ANSWER_FAILED


@settings(max_examples=200, deadline=None)
@given(
    st.text(min_size=1, max_size=60),
    st.booleans(),
    st.lists(st.integers(min_value=1, max_value=9), min_size=1, max_size=40),
)
def test_the_answer_field_is_decoded_however_the_json_is_cut(
    answer: str, ascii_only: bool, sizes: list[int]
) -> None:
    raw = json.dumps({"answer": answer, "citations": []}, ensure_ascii=ascii_only)
    field = AnswerFieldReader()
    out: list[str] = []
    position = 0
    for size in sizes * (len(raw) // len(sizes) + 1):
        if position >= len(raw):
            break
        out.append(field.feed(raw[position : position + size]))
        position += size

    decoded = "".join(out)
    assert answer.startswith(decoded)
    # Only a lone surrogate, which no valid Answer holds, stops it early.
    if not any(0xD800 <= ord(c) <= 0xDFFF for c in answer):
        assert decoded == answer


# --- Failures after the first byte ---------------------------------------------


async def test_the_deadline_ends_the_stream_with_an_error_and_closes_the_model() -> (
    None
):
    model = Model(hold=3)

    sent = await chunks(streamer(model, timeout_seconds=0.2))
    result = rebuilt(sent)

    assert result.outcome is StreamOutcome.FAILED
    assert result.error_code == "dependency_unavailable"
    assert result.text == ""
    assert model.closed
    assert not model.finished


async def test_a_slow_policy_check_ends_in_an_error_after_the_usage() -> None:
    sent = await chunks(streamer(Model(), finisher=SlowFinisher(), timeout_seconds=0.2))

    assert names(sent)[-2:] == ["usage", "error"]
    assert rebuilt(sent).usage is not None


async def test_a_provider_failure_mid_stream_is_a_typed_error_event() -> None:
    model = Model(error=ChatModelRateLimited())

    result = rebuilt(await chunks(streamer(model)))

    assert result.outcome is StreamOutcome.FAILED
    assert result.error_code == "capacity_exceeded"


async def test_a_reply_past_the_limit_abstains_and_keeps_its_usage() -> None:
    final = reply_json("x" * 50_000)
    model = Model(parts=pieces(final, 10_000), final=final)

    result = rebuilt(await chunks(streamer(model)))

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.reason is AbstentionReason.ANSWER_FAILED
    assert result.usage is not None


async def test_raw_half_a_character_abstains_instead_of_failing() -> None:
    lone = chr(0xD83D)
    final = '{"answer":"Leave' + lone + '","citations":[],"abstained":false}'

    result = rebuilt(await chunks(streamer(Model(final=final))))

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.reason is AbstentionReason.ANSWER_FAILED


class Deciding:
    """Return a fixed decision about whatever result it gets."""

    def __init__(self, decision: AbstentionDecision) -> None:
        self.decision = decision

    async def finish(
        self, result: Any, evidence: Any, *, request_id: UUID
    ) -> FinishedAnswer:
        return FinishedAnswer(result, self.decision)


@pytest.mark.parametrize(
    "decision",
    [
        AbstentionDecision(Decision.ABSTAIN, None, POLICY_VERSION),
        AbstentionDecision(
            Decision.ANSWER, AbstentionReason.ANSWER_FAILED, POLICY_VERSION
        ),
        AbstentionDecision(Decision.ANSWER, None, ""),
    ],
)
async def test_an_event_the_writer_refuses_still_ends_in_an_error(
    decision: AbstentionDecision,
) -> None:
    sent = await chunks(streamer(Model(), finisher=Deciding(decision)))

    assert names(sent)[-1] == "error"
    assert rebuilt(sent).outcome is StreamOutcome.FAILED


async def test_usage_a_client_cannot_read_ends_in_an_error() -> None:
    huge = USAGE.model_copy(update={"input_tokens": 2**53})

    sent = await chunks(streamer(Model(usage=huge)))

    assert names(sent)[-1] == "error"
    assert "usage" not in names(sent)


# --- Failures before the first byte --------------------------------------------


async def test_a_source_failure_is_raised_before_any_byte() -> None:
    answering = streamer(Model(), source=Source(error=AuthorizationDenied()))

    with pytest.raises(AuthorizationDenied):
        await answering.open(QUESTION, request_id=REQUEST_ID)


async def test_a_slow_source_runs_out_of_the_deadline_before_any_byte() -> None:
    answering = streamer(Model(), source=Source(wait=True), timeout_seconds=0.1)

    with pytest.raises(AnswerDeadlineExceeded):
        await answering.open(QUESTION, request_id=REQUEST_ID)


# --- Cancellation ---------------------------------------------------------------


async def test_closing_the_stream_closes_the_model_and_records_a_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model(hold=2)
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)
    await anext(events)
    await anext(events)

    await events.aclose()

    assert model.closed
    assert not model.finished
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


async def test_a_stream_closed_before_its_first_event_ends_its_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model()
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)

    await events.aclose()

    assert model.calls == 0
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


async def test_a_model_whose_close_never_ends_cannot_hold_the_stream() -> None:
    model = Model(hold=2, close_delay=60)
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)
    await anext(events)
    await anext(events)

    await asyncio.wait_for(events.aclose(), timeout=CLOSE_SECONDS + 1)

    assert not model.closed


async def test_cancelling_the_task_closes_the_model() -> None:
    model = Model(hold=2)

    async def consume() -> None:
        await chunks(streamer(model))

    task = asyncio.create_task(consume())
    await model.waiting.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert model.closed
    assert not model.finished


async def test_the_stream_record_holds_no_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    answer = f"Every employee receives 24 days of leave. {MARKER}"

    await chunks(streamer(Model(final=reply_json(answer))))

    assert caplog.records
    for record in caplog.records:
        assert MARKER not in record.getMessage()
        assert MARKER not in repr(record.__dict__)


def test_the_stream_record_hides_the_text_it_counts() -> None:
    span = _Span(REQUEST_ID)  # pyright: ignore[reportPrivateUsage]
    span.sent(MARKER)

    assert MARKER not in repr(span)


# --- Over HTTP -------------------------------------------------------------------


def client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


async def test_the_endpoint_streams_events_a_client_can_rebuild() -> None:
    app = create_app(streamer=streamer(Model()))
    async with client(app) as http:
        response = await http.post(
            "/v1/answer/stream",
            json={"text": QUESTION.text},
            headers={"X-Request-ID": str(REQUEST_ID)},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert rebuilt([response.content]).text == ANSWER


@pytest.mark.parametrize(
    ("answering", "status", "code"),
    [
        (None, 503, "workflow_unavailable"),
        ("denied", 403, "authorization_denied"),
        ("slow", 503, "dependency_unavailable"),
    ],
)
async def test_a_failure_before_the_first_byte_is_a_plain_http_error(
    answering: str | None, status: int, code: str
) -> None:
    sources = {
        "denied": Source(error=AuthorizationDenied()),
        "slow": Source(wait=True),
    }
    app = create_app(
        streamer=None
        if answering is None
        else streamer(Model(), source=sources[answering], timeout_seconds=0.1)
    )
    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": "Leave?"})

    assert response.status_code == status
    assert response.json()["code"] == code


async def test_an_empty_question_is_refused_before_any_work() -> None:
    model = Model()
    app = create_app(streamer=streamer(model))
    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": ""})

    assert response.status_code == 422
    assert model.calls == 0


class Connection:
    """An ASGI client that leaves after it has read `leave_after` bodies.

    With `stall`, it stops reading after the first body, so the server
    waits to write, then leaves.
    """

    def __init__(self, *, leave_after: int, stall: bool = False) -> None:
        self.leave_after = leave_after
        self.stall = stall
        self.bodies: list[bytes] = []
        self.left = asyncio.Event()
        self._asked = False

    async def receive(self) -> Message:
        if not self._asked:
            self._asked = True
            body = json.dumps({"text": QUESTION.text}).encode()
            return {"type": "http.request", "body": body, "more_body": False}
        await self.left.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: Message) -> None:
        if message["type"] != "http.response.body":
            return
        if self.stall and self.bodies:
            self.left.set()
            await asyncio.Event().wait()
        self.bodies.append(message["body"])
        if len(self.bodies) >= self.leave_after:
            self.left.set()


def scope(spec_version: str) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/answer/stream",
        "raw_path": b"/v1/answer/stream",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1),
        "server": ("t", 80),
    }


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_a_client_that_leaves_midstream_stops_the_model_and_the_stream(
    spec_version: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    # The model goes quiet after two pieces and would never finish alone.
    model = Model(hold=2)
    connection = Connection(leave_after=2)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert model.closed
    assert not model.finished
    assert all(b"completion" not in body for body in connection.bodies)
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_a_model_that_awaits_while_closing_is_closed_when_the_client_leaves(
    spec_version: str,
) -> None:
    model = Model(hold=2, close_delay=0.05)
    connection = Connection(leave_after=2)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert model.closed
    assert not model.finished


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_a_client_that_leaves_during_the_search_cancels_it(
    spec_version: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    source = Source(wait=True)
    model = Model()
    connection = Connection(leave_after=0)
    connection.left.set()
    app = create_app(streamer=streamer(model, source=source))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert source.cancelled
    assert model.calls == 0
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


async def test_a_stuck_close_cannot_hold_the_response_open() -> None:
    model = Model(hold=4, close_delay=60)
    connection = Connection(leave_after=100, stall=True)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send),
        timeout=2 * CLOSE_SECONDS + 1,
    )


async def test_a_client_that_stops_reading_and_leaves_closes_the_model() -> None:
    # The server is stuck writing the second event when the client leaves.
    model = Model(hold=4)
    connection = Connection(leave_after=100, stall=True)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send), timeout=2
    )

    assert model.closed
    assert not model.finished
```

Create `tests/test_openai_streaming.py`:

```python
"""The OpenAI adapter's stream: pieces, the final reply, failures, cleanup."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import anyio
import httpx
import pytest
from openai import AsyncOpenAI

from evidence_fakes import candidate
from knowledge_service.answering.context_budget import allocate_context
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import ModelReply
from knowledge_service.answering.openai_answering import (
    AnswerSettings,
    OpenAIAnswerModel,
)
from knowledge_service.model import (
    ChatModelError,
    ChatModelMalformedResponse,
    ChatModelRateLimited,
    ChatModelUnavailable,
)
from openai_stubs import status_error
from ranking_stubs import response_for

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000051")
QUESTION = "How many days of annual leave do I get?"
EVIDENCE = build_evidence(
    [candidate(1, text="Every employee receives 24 days of leave.")]
)
PROMPT = allocate_context(QUESTION, EVIDENCE, AnswerSettings().budget).prompt
CITED = EVIDENCE.items[0].evidence_id.value
REPLY = json.dumps(
    {
        "answer": "You get 24 days.",
        "citations": [CITED],
        "abstained": False,
        "reason": None,
    }
)


def delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def completed(text: str | None = REPLY) -> SimpleNamespace:
    return SimpleNamespace(type="response.completed", response=response_for(text))


class StubStream:
    """The SDK's AsyncStream: events in order, then `error`; records close."""

    def __init__(self, events: list[object], error: BaseException | None) -> None:
        self._events = events
        self._error = error
        self.closed = False

    def __aiter__(self) -> AsyncIterator[object]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[object]:
        for event in self._events:
            yield event
        if self._error is not None:
            raise self._error

    async def close(self) -> None:
        self.closed = True


class StubResponses:
    def __init__(
        self,
        events: list[object],
        *,
        error: BaseException | None = None,
        refuse: BaseException | None = None,
    ) -> None:
        self.stream = StubStream(events, error)
        self.refuse = refuse
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> StubStream:
        self.calls.append(kwargs)
        if self.refuse is not None:
            raise self.refuse
        return self.stream


class StubClient:
    def __init__(self, responses: StubResponses) -> None:
        self.responses = responses

    def with_options(self, **options: Any) -> StubClient:
        return self


def model_for(responses: StubResponses) -> OpenAIAnswerModel:
    return OpenAIAnswerModel(
        StubClient(responses),  # pyright: ignore[reportArgumentType]
        AnswerSettings(),
    )


async def drain(model: OpenAIAnswerModel) -> list[str | ModelReply]:
    parts = model.stream(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)
    return [part async for part in parts]


async def test_the_stream_yields_text_pieces_then_the_billed_reply() -> None:
    responses = StubResponses([delta(REPLY[:10]), delta(REPLY[10:]), completed()])

    *texts, reply = await drain(model_for(responses))

    assert texts == [REPLY[:10], REPLY[10:]]
    assert isinstance(reply, ModelReply)
    assert reply.text == REPLY
    assert reply.usage.input_tokens == 120
    assert responses.calls[0]["stream"] is True
    assert responses.calls[0]["store"] is False
    assert responses.stream.closed


async def test_a_refusal_streams_no_text_and_says_refused() -> None:
    responses = StubResponses([completed(None)])

    (reply,) = await drain(model_for(responses))

    assert isinstance(reply, ModelReply)
    assert reply.refused


async def test_closing_the_stream_early_closes_the_provider_stream() -> None:
    responses = StubResponses([delta("{"), delta('"answer"'), completed()])
    parts = model_for(responses).stream(
        PROMPT, request_id=REQUEST_ID, timeout_seconds=5
    )

    await anext(parts)
    await parts.aclose()

    assert responses.stream.closed


@pytest.mark.parametrize(
    ("events", "error", "refuse", "expected"),
    [
        ([delta("{")], None, None, ChatModelMalformedResponse),
        ([delta("{"), SimpleNamespace(type="error")], None, None, ChatModelUnavailable),
        ([delta("{")], httpx.ReadError("gone"), None, ChatModelUnavailable),
        ([], None, status_error(429), ChatModelRateLimited),
        ([delta("{")], status_error(429), None, ChatModelRateLimited),
    ],
)
async def test_failures_become_port_errors_and_close_the_stream(
    events: list[object],
    error: BaseException | None,
    refuse: BaseException | None,
    expected: type[ChatModelError],
) -> None:
    responses = StubResponses(events, error=error, refuse=refuse)

    with pytest.raises(expected) as caught:
        await drain(model_for(responses))

    assert caught.value.__context__ is None
    assert responses.stream.closed or refuse is not None


class SlowClosingStream(StubStream):
    """A stream that stalls after its first event and awaits while closing."""

    async def _iterate(self) -> AsyncIterator[object]:
        yield delta("{")
        await asyncio.Event().wait()

    async def close(self) -> None:
        await asyncio.sleep(0)
        self.closed = True


async def test_a_cancelled_caller_still_closes_the_provider_stream() -> None:
    responses = StubResponses([])
    responses.stream = SlowClosingStream([], None)
    parts = model_for(responses).stream(
        PROMPT, request_id=REQUEST_ID, timeout_seconds=5
    )

    # The scope stays cancelled, so every await inside it is cancelled again.
    with anyio.move_on_after(0.05):
        async for _ in parts:
            pass

    assert responses.stream.closed


@pytest.mark.live
async def test_openai_live_stream_answers_in_pieces() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key is None:
        pytest.skip("OPENAI_API_KEY is required for the opt-in live test")

    client = AsyncOpenAI(api_key=api_key)
    try:
        parts = await drain(OpenAIAnswerModel(client, AnswerSettings()))
    finally:
        await client.close()

    *texts, reply = parts
    assert isinstance(reply, ModelReply)
    assert len(texts) > 1
    assert "".join(str(t) for t in texts) == reply.text
    assert "24" in str(reply.text)
```

Check:

```bash
uv run pytest tests/test_streaming.py tests/test_openai_streaming.py -q
```

Expected: 49 passed, 1 deselected (the live test).

### Step 7 — No coding in this step: break it and decide

This step shows why the endpoint needs its own response class, and records what you learned.

**Action 1:** break the endpoint on purpose. In `src/knowledge_service/app.py`, in `answer_stream`, replace:

```python
        return EventStreamResponse(
            events,
```

with Starlette's plain response:

```python
        return StreamingResponse(
            events,
            media_type="text/event-stream",
```

Run:

```bash
uv run pytest tests/test_streaming.py -q
```

Expected: 5 failed, 35 passed, and the run is slower than usual (13 to 70 seconds in the reference runs), because a stuck close now waits for its test's timeout. Under ASGI 2.4 a client that leaves while the model is quiet is never noticed, a model that awaits while closing is cut short, and a client that stops reading holds the stream: Starlette's response notices a disconnect only on its next write, and cancels with an anyio scope. Put the lines back and run again until all pass.

**Action 2:** under `## Decision`, write:

- why the search runs before the first byte and the model after it;
- why the stream is pulled, and what a queue between the model and the client would cost;
- why the client keeps text only when it is exactly the checked Answer;
- why the response listens for the disconnect itself, and why it cancels only once;
- what is left for later: rechecking Citations with the reader's identity (M07), a real `EvidenceSource`, and the failure map of M06-T13;
- at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."

### Step 8 — No coding in this step: architecture, gates, evidence, and progress

This step updates the text the change made incomplete, proves everything still works, and records the result.

**Action 1:** in `docs/architecture.md`, add the streamer to the `answering` row:

Find:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), and `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages) |
```

Replace with:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`) |
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_streaming.py tests/test_openai_streaming.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2170 passed for `make test-unit`, 49 more than M06-T08. This lesson touches no database code, so `make test-integration` should give the same 342 passed. If it says there is no test database, start it with `docker start knowledge-service-postgres`.

**Action 3:** complete the evidence file:

- **Design:** the changed files; what happens before and after the first byte; how text is read out of the JSON and matched to the checked Answer; the order of events; the deadline; how leaving is noticed and the model's stream closed; the record of one stream.
- **Verification:** the commands and counts, the deliberate break, and the known limits from "Before you begin".
- **Review:** an adversarial review with 4,000 random replies and disconnects at every stage under ASGI 2.3 and 2.4 found no way to keep text that is not the checked Answer, and seven problems, each fixed with a test that failed before the fix (15 tests): a refused event after the decision left no terminal event; a hung model close held the stream; leaving during the search did not stop it and left no record; a cancelled anyio scope cut the model's cleanup short; an over-long reply lost its usage; half a character ended in an internal error; the record's repr held the text.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M06-T09 row to `docs/curriculum/PROGRESS.md`, set the next task to M06-T10, and commit with `m06-t09: stream and cancel answers`.

## Reflection questions

1. What does `AnswerStreamer` hide from the HTTP endpoint? List what the endpoint never has to know about the model, the Evidence, or the policy.
2. Someone wants to read the model's whole stream into a list first and then send it, "to keep the code simple". What would a user notice, and what would happen to memory and to cancellation?
3. Which promise of this lesson would be easiest to break by accident when M06-T13 adds retry advice to the error events, and which test would catch it?
4. The stream abstains when the streamed text is not the start of the checked Answer. When could that happen with a real model, and what does the user see?
5. How would you show, on a running server with a real model, that closing the browser tab stops the provider from generating? What would you look at?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the code.
- [ ] `reply_result` checks a reply, and `generate_answer` uses it.
- [ ] `AnswerStreamer.open` finds and judges Evidence before the first byte; its failures are HTTP errors.
- [ ] Text streams as the model writes it, and a client keeps it only when it is exactly the checked Answer.
- [ ] Citations come only after an `answer` decision; the billed usage comes before the terminal event.
- [ ] Every stream that started ends with exactly one `completion` or `error`.
- [ ] One deadline covers the search, the model's stream, and the checks.
- [ ] A client that leaves, at any point, cancels the work and closes the model's stream, within `CLOSE_SECONDS`.
- [ ] `OpenAIAnswerModel.stream` closes the provider's stream on every exit.
- [ ] No Question, Evidence, or answer text reaches a log record or a repr.
- [ ] You ran the deliberate break, saw the tests fail, and undid it.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
