"""The RewriteModel port, and rewriting a Question before search, safely.

A rewrite turns a Question into a better search text: "And abroad?" after
a question about the daily allowance becomes "What is the daily allowance
abroad?". It can also break a Question, so `classify_question`
(M05-T14) decides first which Questions a rewrite may touch and which
words it must keep. This module asks a language model for the rewrite,
checks it, and searches with the Question as typed whenever the rewrite
cannot be trusted.

What every `RewriteModel` adapter promises:

- **One call is one request.** `rewrite` returns a `RewriteAnswer` holding
  the model's text, unchecked, and the usage the provider billed. When
  there is no answer to bill, because of a timeout, an outage, a rate
  limit, or a refused request, it raises a `RewriterError` instead.
- **A billed answer keeps its usage.** An answer that holds no readable
  text, such as a refusal or an answer cut off at its token limit, is
  returned with `text=None` and its usage, so its cost is never lost.
- **Errors and logs carry no text.**

What `rewrite_question` promises:

- **Behind a policy.** Only a Question whose class is rewritable and is
  switched on in the `RewritePolicy` reaches the model. The default policy
  switches nothing on, so by default nothing is sent and every Question is
  searched as typed.
- **Checked first.** The Question and its history are checked by
  `classify_question`, which raises `InvalidQuestion`; a policy that is
  not a `RewritePolicy` or a timeout that is not a positive number raises
  `InvalidRewriteRequest`. Both happen before anything is sent.
- **Only the conversation is sent.** The model sees the Question, the
  earlier Questions, the stated language, the class, and the words to
  keep. No Chunk, Document, or reader is ever part of a request.
- **One deadline.** The request must answer within `timeout_seconds`.
- **A checked rewrite.** `checked_rewrite` rejects the whole rewrite
  unless it keeps every rule below; nothing the model wrote is ever
  quoted.
  - Plain text with a letter or a digit, at most `MAX_QUERY_CHARACTERS`
    long, valid Unicode, and no control, invisible, combining, or symbol
    character.
  - No search operator. Word search reads a `-` at the start or after a
    character that is not a letter or digit as "without", an "or" with
    no letter or digit on either side as "either", and quotes as a
    phrase, so a rewrite could exclude the very word it must keep. A `-`
    inside a word, as in `EXP-2041` or `day-to-day`, stays.
  - Every word to keep is a whole word of it, exactly as typed.
  - Every word with a digit was typed by the user, in the Question or an
    earlier one: a rewrite may carry `EXP-2041` over, never change it.
  - Every other letter is in the stated language's alphabet: a to z for
    English, the 33 Ukrainian letters and the apostrophe for Ukrainian.
    Look-alikes from Greek, Russian, fullwidth, or styled letters are
    refused. Words the user typed keep their own letters.
- **Fallback, never half a rewrite.** When the model fails, answers after
  the deadline, or returns a rewrite that fails the check, the search
  text is the Question as typed and `fallback` names the error type, one
  of the port's own; an unknown subclass is named `RewriterError`. An
  error that is not a `RewriterError` is a bug and propagates.
- **Cost is captured.** Whenever the model answered, its usage is in the
  result, also when its rewrite was refused or arrived late.
- **Quiet.** The result hides the search text from its repr, and the one
  log record per fallback holds counts and names only.
"""

import asyncio
import logging
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from knowledge_service.contracts import Usage
from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.rewrite_classes import (
    REWRITABLE,
    RewriteClass,
    RewritePlan,
    classify_question,
    question_words,
)

logger = logging.getLogger(__name__)

# The alphabet a word must not use, by the Question's stated language.
_ENGLISH_LETTERS = "abcdefghijklmnopqrstuvwxyz"
_UKRAINIAN_LETTERS = "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя"
# The Ukrainian apostrophe is a letter in Unicode, but of no alphabet.
_ALPHABETS = {
    Language.ENGLISH: frozenset(_ENGLISH_LETTERS + _ENGLISH_LETTERS.upper()),
    Language.UKRAINIAN: frozenset(
        _UKRAINIAN_LETTERS + _UKRAINIAN_LETTERS.upper() + "\u02bc"
    ),
}
# Control, invisible, unassigned, combining, and symbol characters.
_REFUSED = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Mn", "Me", "So"})
# PostgreSQL's web search syntax: "or" between non-word characters.
_OR = re.compile(r"or", re.IGNORECASE)


class RewriterError(Exception):
    """The rewrite could not be used; the Question is searched as typed."""


class RewriterTimeout(RewriterError):
    """No answer came before the deadline."""


class RewriterUnavailable(RewriterError):
    """The provider could not be reached or failed on its side."""


class RewriterRateLimited(RewriterError):
    """The provider asked to slow down; nothing was rewritten."""


class RewriterRequestRefused(RewriterError):
    """The provider refused the request, such as for a bad key."""


class RewriterMalformedResponse(RewriterError):
    """The provider answered with something that is not an answer."""


class InvalidRewriteRequest(ValueError):
    """The caller's request is broken; nothing was sent."""


class RewriteDefect(StrEnum):
    """Why a rewrite was rejected; the model's words are never repeated."""

    NO_TEXT = "no_text"
    NOT_TEXT = "not_text"
    NO_WORDS = "no_words"
    TOO_LONG = "too_long"
    INVALID_CHARACTER = "invalid_character"
    SEARCH_OPERATOR = "search_operator"
    DROPPED_WORD = "dropped_word"
    INVENTED_IDENTIFIER = "invented_identifier"
    CHANGED_LANGUAGE = "changed_language"


class InvalidRewrite(RewriterMalformedResponse):
    """The model answered, but its rewrite breaks a rule."""

    def __init__(self, defect: RewriteDefect) -> None:
        self.defect = defect
        super().__init__(f"invalid rewrite: {defect.value}")


@dataclass(frozen=True, slots=True)
class RewriterIdentity:
    """The provider and the pinned model behind a rewrite."""

    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class RewriteAnswer:
    """What the model returned, unchecked, and what the provider billed.

    `text` is None when the answer held no readable rewrite.
    """

    text: object = field(repr=False)
    usage: Usage


class RewriteModel(Protocol):
    """A language model that rewrites one Question into a search text."""

    @property
    def identity(self) -> RewriterIdentity:
        """The provider and the pinned model."""
        ...

    async def rewrite(
        self,
        question: str,
        plan: RewritePlan,
        *,
        history: Sequence[str],
        request_id: UUID,
        timeout_seconds: float,
    ) -> RewriteAnswer:
        """Return the model's rewrite and usage, or raise a `RewriterError`."""
        ...


@dataclass(frozen=True, slots=True)
class RewritePolicy:
    """Which rewrite classes may be sent to the model; none by default."""

    classes: frozenset[RewriteClass] = frozenset()

    def __post_init__(self) -> None:
        if type(self.classes) is not frozenset or not all(
            type(name) is RewriteClass for name in self.classes
        ):
            raise InvalidRewriteRequest("a policy holds a frozenset of RewriteClass")
        if not self.classes <= REWRITABLE:
            raise InvalidRewriteRequest(
                "a policy may switch on only follow_up, acronym, and cross_language"
            )


class RewriteOutcome(StrEnum):
    """What happened to one Question."""

    REWRITTEN = "rewritten"
    UNCHANGED = "unchanged"
    NOT_REWRITABLE = "not_rewritable"
    SWITCHED_OFF = "switched_off"
    FALLBACK = "fallback"


@dataclass(frozen=True, slots=True)
class RewriteResult:
    """The text to search with, and how it was chosen.

    `search_text` is the checked rewrite when `outcome` is `rewritten`, and
    the Question as typed otherwise. `fallback` names the error type when
    a rewrite was asked for and not used. `usage` is None when the model
    did not answer.
    """

    identity: RewriterIdentity
    plan: RewritePlan
    search_text: str = field(repr=False)
    outcome: RewriteOutcome
    fallback: str | None
    usage: Usage | None


# The port errors a fallback may name; any other type is reported as RewriterError.
_REASONS: frozenset[type[RewriterError]] = frozenset(
    {
        InvalidRewrite,
        RewriterMalformedResponse,
        RewriterRateLimited,
        RewriterRequestRefused,
        RewriterTimeout,
        RewriterUnavailable,
    }
)


def checked_rewrite(
    text: object,
    plan: RewritePlan,
    *,
    question: str,
    history: Sequence[str] = (),
) -> str:
    """Return the rewrite when it keeps every rule, or raise `InvalidRewrite`.

    `text` is whatever the model sent, so nothing about it is trusted.
    `question` and `history` are what the user typed.
    """
    if text is None:
        raise InvalidRewrite(RewriteDefect.NO_TEXT)
    # A str subclass could run its own code while being read; not this one.
    if type(text) is not str:
        raise InvalidRewrite(RewriteDefect.NOT_TEXT)
    # Word search refuses a text with no letter or digit, such as "???".
    if not any(character.isalnum() for character in text):
        raise InvalidRewrite(RewriteDefect.NO_WORDS)
    if len(text) > MAX_QUERY_CHARACTERS:
        raise InvalidRewrite(RewriteDefect.TOO_LONG)
    # NUL, a lone surrogate, or a hidden direction mark is not plain text.
    if any(unicodedata.category(character) in _REFUSED for character in text):
        raise InvalidRewrite(RewriteDefect.INVALID_CHARACTER)
    if _has_operator(text):
        raise InvalidRewrite(RewriteDefect.SEARCH_OPERATOR)
    words = question_words(text)
    if not all(word in words for word in plan.preserve):
        raise InvalidRewrite(RewriteDefect.DROPPED_WORD)
    typed = {
        word
        for typed_text in (question, *history)
        for word in question_words(typed_text)
    }
    if any(_has_digit(word) and word not in typed for word in words):
        raise InvalidRewrite(RewriteDefect.INVENTED_IDENTIFIER)
    letters = _ALPHABETS.get(plan.language)
    if letters is not None and any(
        word not in typed and not _written_in(word, letters) for word in words
    ):
        raise InvalidRewrite(RewriteDefect.CHANGED_LANGUAGE)
    return text


async def rewrite_question(
    model: RewriteModel,
    question: str,
    *,
    language: Language,
    history: Sequence[str] = (),
    policy: RewritePolicy,
    request_id: UUID,
    timeout_seconds: float,
) -> RewriteResult:
    """Ask the model to rewrite the Question if the policy allows; else keep it."""
    plan = classify_question(question, language=language, history=history)
    if type(policy) is not RewritePolicy:
        raise InvalidRewriteRequest("policy must be a RewritePolicy")
    # bool is an int, and NaN compares false with everything; neither is.
    if (
        type(timeout_seconds) not in (int, float)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise InvalidRewriteRequest("timeout_seconds must be a positive number")
    identity = model.identity
    search_text = question
    fallback: str | None = None
    usage: Usage | None = None
    if not plan.may_rewrite:
        outcome = RewriteOutcome.NOT_REWRITABLE
    elif plan.rewrite_class not in policy.classes:
        outcome = RewriteOutcome.SWITCHED_OFF
    else:
        try:
            answer = await _ask(
                model, question, plan, history, request_id, timeout_seconds
            )
            usage = answer.usage
            # A model that blocked the loop answers late: billed, not used.
            if answer.late:
                raise RewriterTimeout()
            search_text = checked_rewrite(
                answer.text, plan, question=question, history=history
            )
        except RewriterError as error:
            # Only a known port error is named: a class name is text too.
            known = type(error) in _REASONS
            fallback = type(error).__name__ if known else RewriterError.__name__
            search_text = question
            _log_fallback(request_id, identity, plan.rewrite_class, fallback)
        if fallback is not None:
            outcome = RewriteOutcome.FALLBACK
        elif search_text == question:
            outcome = RewriteOutcome.UNCHANGED
        else:
            outcome = RewriteOutcome.REWRITTEN
    return RewriteResult(
        identity=identity,
        plan=plan,
        search_text=search_text,
        outcome=outcome,
        fallback=fallback,
        usage=usage,
    )


@dataclass(frozen=True, slots=True)
class _Answered:
    text: object = field(repr=False)
    usage: Usage
    late: bool


async def _ask(
    model: RewriteModel,
    question: str,
    plan: RewritePlan,
    history: Sequence[str],
    request_id: UUID,
    timeout_seconds: float,
) -> _Answered:
    """Send one request under the deadline; raise if no answer came."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    answer: object = None
    scope = asyncio.timeout(timeout_seconds)
    try:
        async with scope:
            answer = await model.rewrite(
                question,
                plan,
                history=tuple(history),
                request_id=request_id,
                timeout_seconds=timeout_seconds,
            )
    except TimeoutError:
        # Only this deadline means a timeout; a TimeoutError the model
        # raised itself is a bug, and propagates.
        if not scope.expired():
            raise
    if answer is None:
        raise RewriterTimeout()
    if type(answer) is not RewriteAnswer or type(answer.usage) is not Usage:
        raise RewriterMalformedResponse("the model did not return a RewriteAnswer")
    return _Answered(text=answer.text, usage=answer.usage, late=loop.time() > deadline)


def _has_digit(word: str) -> bool:
    return any(character.isnumeric() for character in word)


def _written_in(word: str, letters: frozenset[str]) -> bool:
    """True when every letter of `word` is one of `letters`."""
    return all(character in letters for character in word if character.isalpha())


def _has_operator(text: str) -> bool:
    """True when web search syntax would read part of `text` as an operator."""
    if '"' in text:
        return True
    for index, character in enumerate(text):
        # A minus inside a word joins it; anywhere else it means "without".
        if character == "-" and (index == 0 or not text[index - 1].isalnum()):
            return True
    for match in _OR.finditer(text):
        before = text[match.start() - 1] if match.start() > 0 else " "
        after = text[match.end()] if match.end() < len(text) else " "
        if not before.isalnum() and not after.isalnum():
            return True
    return False


def _log_fallback(
    request_id: UUID,
    identity: RewriterIdentity,
    rewrite_class: RewriteClass,
    reason: str,
) -> None:
    """Record that the Question was searched as typed, and why. No text."""
    fields: dict[str, object] = {
        "request_id": str(request_id),
        "provider": identity.provider,
        "model": identity.model,
        "rewrite_class": str(rewrite_class),
        "outcome": "fallback",
        "reason": reason,
    }
    logger.warning("query rewrite fell back", extra={"rewrite": fields})
