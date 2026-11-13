"""The AnswerModel port, and generating a structured Answer from Evidence.

`generate_answer` fits the Question and its Evidence into the context
budget (see `answering.context_budget`), asks a chat model for an answer
that follows the prompt's schema, and checks that answer on the server.
Nothing the model writes is trusted until it passes `checked_answer`.

What every `AnswerModel` adapter promises:

- **One call is one request.** `generate` returns a `ModelReply` holding
  the model's text, unchecked, whether the model refused, and the usage
  the provider billed. When there is nothing to bill, because of a
  timeout, an outage, a rate limit, or a refused request, it raises a
  `ChatModelError` instead, the same errors as the M01 `ChatModel` port.
- **A billed reply keeps its usage.** A refusal, or an answer cut off at
  its token limit, is returned with its usage, so its cost is never lost.
- **Errors and logs carry no text.**

What `generate_answer` promises:

- **Checked first.** A timeout that is not a positive number raises
  `InvalidAnswerRequest`; a bad Question, Evidence, or budget raises the
  errors of `allocate_context`. Both happen before anything is sent.
- **No Evidence, no call.** When no Evidence fits, or there was none, the
  model is not called: the result is `NO_EVIDENCE`, with no usage.
- **One deadline.** The model must answer within `timeout_seconds`, or
  `ChatModelTimeout` is raised, with no chained error. A `TimeoutError`
  the model raises itself is a bug, and propagates.
- **Provider failures propagate.** A `ChatModelError` from the model is
  raised as it is, so the HTTP layer maps it as it maps the M01 port's.
  Anything else the model raises is a bug and propagates too.
- **Every reply becomes one outcome.** `ANSWERED` with Citations built by
  `resolve_citations` from the kept Evidence only; `ABSTAINED` with a
  reason and no Citations; `REFUSED` when the model declined; `INVALID`
  with an `AnswerDefect` when the reply breaks a rule. Each carries the
  billed usage and the context report.
- **A checked reply.** `checked_answer` accepts only one JSON object with
  exactly `answer`, `citations`, `abstained`, and `reason`, each of its
  type and none repeated. An answer must hold a visible character and
  cite at least one Evidence it was given; an abstention must give a
  reason with a visible character and cite nothing. Texts must be valid
  Unicode and within their limits.
- **Quiet.** Results hide the answer and the reason from their repr; the
  one log record per call holds IDs, counts, and names only. A reply's
  token counts must be plain integers, and a model identity that is not
  plain text is logged as "unknown".
"""

import asyncio
import json
import logging
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID

from knowledge_service.answering.citations import (
    Citation,
    InvalidCitation,
    resolve_citations,
)
from knowledge_service.answering.context_budget import (
    ContextBudget,
    ContextReport,
    allocate_context,
)
from knowledge_service.answering.evidence import EvidenceSet
from knowledge_service.answering.prompt import EvidencePrompt, has_visible_text
from knowledge_service.contracts import Usage
from knowledge_service.model import (
    ChatModelError,
    ChatModelMalformedResponse,
    ChatModelTimeout,
)

logger = logging.getLogger(__name__)

MAX_REPLY_CHARACTERS = 40_000
MAX_ANSWER_CHARACTERS = 16_000
MAX_REASON_CHARACTERS = 1_000
_FIELDS = frozenset({"answer", "citations", "abstained", "reason"})


class InvalidAnswerRequest(ValueError):
    """The caller's request is broken; nothing was sent."""


class AnswerDefect(StrEnum):
    """Why a reply was rejected; the model's words are never repeated."""

    UNREADABLE = "unreadable"
    TOO_LONG = "too_long"
    INVALID_TEXT = "invalid_text"
    EMPTY_ANSWER = "empty_answer"
    UNCITED = "uncited"
    INVALID_CITATION = "invalid_citation"
    CITED_ABSTENTION = "cited_abstention"
    NO_REASON = "no_reason"


class InvalidAnswer(ChatModelMalformedResponse):
    """The model answered, but its reply breaks a rule."""

    def __init__(self, defect: AnswerDefect) -> None:
        self.defect = defect
        super().__init__(f"invalid answer: {defect.value}")


class AnswerOutcome(StrEnum):
    """What came of one request."""

    ANSWERED = "answered"
    ABSTAINED = "abstained"
    NO_EVIDENCE = "no_evidence"
    REFUSED = "refused"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    """The provider and the pinned model behind an answer."""

    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class ModelReply:
    """What the model returned, unchecked, and what the provider billed.

    `text` is None when the reply held no readable text; `refused` is True
    when the model declined to answer.
    """

    text: object = field(repr=False)
    refused: bool
    usage: Usage


class AnswerModel(Protocol):
    """A chat model that answers an Evidence prompt in its JSON schema."""

    @property
    def identity(self) -> ModelIdentity:
        """The provider and the pinned model."""
        ...

    async def generate(
        self,
        prompt: EvidencePrompt,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> ModelReply:
        """Return the model's reply and usage, or raise a `ChatModelError`."""
        ...


@dataclass(frozen=True, slots=True)
class CheckedAnswer:
    """A reply that passed every rule; Citations come from the server."""

    text: str = field(repr=False)
    abstained: bool
    reason: str | None = field(repr=False)
    citations: tuple[Citation, ...]


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """The outcome of one request, with its cost and its context report.

    `text` and `reason` are empty and None unless the outcome is
    `ANSWERED` or `ABSTAINED`; `usage` is None when the model was not
    called; `defect` is set only for `INVALID`.
    """

    outcome: AnswerOutcome
    text: str = field(repr=False)
    reason: str | None = field(repr=False)
    citations: tuple[Citation, ...]
    defect: AnswerDefect | None
    usage: Usage | None
    context: ContextReport


def checked_answer(text: object, evidence: EvidenceSet) -> CheckedAnswer:
    """Check one reply against the rules; raise `InvalidAnswer` otherwise."""
    if type(text) is not str or len(text) > MAX_REPLY_CHARACTERS:
        raise InvalidAnswer(
            AnswerDefect.TOO_LONG if type(text) is str else AnswerDefect.UNREADABLE
        )
    body = _parsed(text)
    answer, references = body["answer"], body["citations"]
    abstained, reason = body["abstained"], body["reason"]
    if (
        type(answer) is not str
        or type(references) is not list
        or type(abstained) is not bool
        or not (reason is None or type(reason) is str)
    ):
        raise InvalidAnswer(AnswerDefect.UNREADABLE)
    if len(answer) > MAX_ANSWER_CHARACTERS or (
        reason is not None and len(reason) > MAX_REASON_CHARACTERS
    ):
        raise InvalidAnswer(AnswerDefect.TOO_LONG)
    if not _valid(answer) or (reason is not None and not _valid(reason)):
        raise InvalidAnswer(AnswerDefect.INVALID_TEXT)
    if abstained:
        if references:
            raise InvalidAnswer(AnswerDefect.CITED_ABSTENTION)
        if reason is None or not has_visible_text(reason):
            raise InvalidAnswer(AnswerDefect.NO_REASON)
        return CheckedAnswer(text=answer, abstained=True, reason=reason, citations=())
    if not has_visible_text(answer):
        raise InvalidAnswer(AnswerDefect.EMPTY_ANSWER)
    try:
        citations = resolve_citations(evidence, cast(list[object], references))
    except InvalidCitation:
        raise InvalidAnswer(AnswerDefect.INVALID_CITATION) from None
    if not citations:
        raise InvalidAnswer(AnswerDefect.UNCITED)
    return CheckedAnswer(text=answer, abstained=False, reason=None, citations=citations)


async def generate_answer(
    question: str,
    evidence: EvidenceSet,
    budget: ContextBudget,
    model: AnswerModel,
    *,
    request_id: UUID,
    timeout_seconds: float,
) -> AnswerResult:
    """Ask the model to answer from the Evidence that fits, and check its reply."""
    if not positive_seconds(timeout_seconds):
        raise InvalidAnswerRequest("timeout_seconds must be a positive number")
    allocated = allocate_context(question, evidence, budget)
    context = allocated.report
    if not allocated.evidence.items:
        result = _result(AnswerOutcome.NO_EVIDENCE, context, usage=None)
        _log(request_id, model, result)
        return result
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


def positive_seconds(value: object) -> bool:
    """True for a finite number of seconds above zero; never raises."""
    # bool is an int, and NaN compares false with everything; neither is.
    if type(value) not in (int, float):
        return False
    try:
        seconds = float(cast(float, value))
    except OverflowError:
        return False
    return math.isfinite(seconds) and seconds > 0


async def _ask(
    model: AnswerModel,
    prompt: EvidencePrompt,
    request_id: UUID,
    timeout_seconds: float,
) -> ModelReply:
    """One call under one deadline; a reply of the wrong shape is malformed."""
    deadline = asyncio.timeout(timeout_seconds)
    reply: object = None
    try:
        async with deadline:
            reply = await model.generate(
                prompt, request_id=request_id, timeout_seconds=timeout_seconds
            )
    except TimeoutError:
        # Only the deadline's own timeout is ours; the model's is a bug.
        if not deadline.expired():
            raise
    except ChatModelError as error:
        _log_failure(request_id, model, type(error).__name__)
        raise
    if deadline.expired():
        # Raised here, not in the except block, so it chains no error.
        _log_failure(request_id, model, ChatModelTimeout.__name__)
        raise ChatModelTimeout()
    if not _well_formed(reply):
        raise ChatModelMalformedResponse("the model did not return a ModelReply")
    return cast(ModelReply, reply)


def _well_formed(reply: object) -> bool:
    """A ModelReply whose flag and token counts are exactly what they claim."""
    if type(reply) is not ModelReply or type(reply.refused) is not bool:
        return False
    usage = cast(object, reply.usage)
    if type(usage) is not Usage:
        return False
    # model_construct skips validation, so the counts are checked again.
    counts = (usage.input_tokens, usage.output_tokens, usage.total_tokens)
    return all(type(count) is int and count >= 0 for count in counts)


def _parsed(text: str) -> dict[str, object]:
    """The reply as one JSON object with exactly the four fields, each once."""

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        keys = [key for key, _ in pairs]
        if len(set(keys)) != len(keys):
            raise ValueError("a key appears twice")
        return dict(pairs)

    try:
        body: object = json.loads(text, object_pairs_hook=unique)
    except ValueError, RecursionError:
        raise InvalidAnswer(AnswerDefect.UNREADABLE) from None
    if type(body) is not dict or frozenset(cast(dict[str, object], body)) != _FIELDS:
        raise InvalidAnswer(AnswerDefect.UNREADABLE)
    return cast(dict[str, object], body)


def _valid(text: str) -> bool:
    """True when `text` is valid Unicode: JSON can hold a lone surrogate."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _result(
    outcome: AnswerOutcome,
    context: ContextReport,
    *,
    usage: Usage | None,
    defect: AnswerDefect | None = None,
) -> AnswerResult:
    return AnswerResult(
        outcome=outcome,
        text="",
        reason=None,
        citations=(),
        defect=defect,
        usage=usage,
        context=context,
    )


def _identity(model: AnswerModel) -> tuple[str, str]:
    """The provider and model as plain text, or "unknown"; never raises."""
    # A broken identity must not hide the outcome being logged.
    try:
        identity = model.identity
        names = (identity.provider, identity.model)
    except Exception:
        return "unknown", "unknown"
    if not all(type(name) is str for name in names):
        return "unknown", "unknown"
    return names


def _log(request_id: UUID, model: AnswerModel, result: AnswerResult) -> None:
    """Record one request: IDs, counts, tokens, outcome. No text."""
    provider, name = _identity(model)
    fields: dict[str, object] = {
        "request_id": str(request_id),
        "provider": provider,
        "model": name,
        "outcome": result.outcome.value,
        "defect": None if result.defect is None else result.defect.value,
        "evidence_kept": len(result.context.kept),
        "evidence_dropped": len(result.context.dropped),
        "prompt_tokens": result.context.prompt_tokens,
        "citations": len(result.citations),
    }
    if result.usage is not None:
        fields["input_tokens"] = result.usage.input_tokens
        fields["output_tokens"] = result.usage.output_tokens
    level = logging.INFO if result.outcome in _EXPECTED else logging.WARNING
    logger.log(level, "answer request finished", extra={"answer": fields})


def _log_failure(request_id: UUID, model: AnswerModel, error: str) -> None:
    provider, name = _identity(model)
    fields = {
        "request_id": str(request_id),
        "provider": provider,
        "model": name,
        "outcome": error,
    }
    logger.warning("answer request failed", extra={"answer": fields})


_EXPECTED = frozenset(
    {AnswerOutcome.ANSWERED, AnswerOutcome.ABSTAINED, AnswerOutcome.NO_EVIDENCE}
)
