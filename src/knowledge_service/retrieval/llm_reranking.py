"""The RankingModel port, and reranking retrieval's Candidates with a language model.

A cross-encoder scores each passage on its own. A language model can read
every passage at once and return them in order, a ranking, but it gives no
scores, it may answer with anything, and it is billed per token. This
module asks for a ranking, checks it, and keeps retrieval's order when the
answer cannot be trusted.

What every `RankingModel` adapter promises:

- **One call is one request.** `rank` returns a `RankingAnswer` holding the
  model's ranking, unchecked, and the usage the provider billed. When there
  is no answer to bill, because of a timeout, an outage, a rate limit, or
  a refused request, it raises a `RerankerError` instead.
- **A billed answer keeps its usage.** An answer that holds no readable
  list, such as a refusal or an answer cut off at its token limit, is
  returned with `ranking=None` and its usage, so its cost is never lost.
- **A safe count.** `count_request_tokens` never counts fewer input tokens
  than the provider bills for that request.
- **Errors and logs carry no text.**

What `rerank_with_llm` promises:

- **Checked first.** The question, timeout, and Candidates are checked and
  read once, as `rerank_candidates` does; a broken request raises
  `InvalidRerankRequest` before anything is sent.
- **One deadline.** Counting tokens and the request must finish within
  `timeout_seconds` of the start; the request gets only the time left.
- **Opaque IDs.** Each Candidate sent gets a random ID for this call only.
  The model sees the question, these IDs, and the Candidates' text: no
  Chunk ID, title, or retrieval rank. A passage cannot know its own ID in
  advance, so its text cannot ask for itself by name.
- **A strict budget.** At most `budget.max_candidates` Candidates are sent,
  from the top of retrieval's order, and only as many as fit
  `budget.max_input_tokens` by the model's own count. The rest keep
  retrieval's order below them. If fewer than two fit, nothing is sent.
- **A checked ranking.** The ranking must name every ID sent exactly once
  and nothing else. A duplicate, an omission, or an unknown ID rejects the
  whole ranking; nothing the model wrote is ever quoted.
- **Fallback, never half an order.** When the model fails, answers after
  the deadline, or returns a ranking that fails the check, the result is
  retrieval's order and `fallback` names the error type, one of the port's
  own; an unknown subclass is named `RerankerError`. An error that is not
  a `RerankerError` is a bug and propagates.
- **Cost is captured.** Whenever the model answered, its usage is in the
  result, also when its ranking was refused or arrived late.
- **Score semantics.** A ranking has no scores. A returned Candidate's
  `score` is how many Candidates the model put below it; every Candidate
  the model did not rank scores 0. Higher is better, and equal scores keep
  retrieval's order, as with `rerank_candidates`.
"""

import asyncio
import logging
import secrets
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID

from knowledge_service.contracts import Usage
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.reranking import (
    InvalidRerankRequest,
    RerankedCandidate,
    RerankerError,
    RerankerIdentity,
    RerankerMalformedResponse,
    RerankerRequestRefused,
    RerankerTimeout,
    RerankerUnavailable,
    RerankInputRejected,
    RerankRejection,
    read_candidates,
    reranked_candidate,
)

logger = logging.getLogger(__name__)

# 8 hex characters: 4 billion values, a handful of tokens each.
_ID_BYTES = 4


class RerankerRateLimited(RerankerError):
    """The provider asked to slow down; nothing was ranked."""


class RankingOverBudget(RerankerError):
    """Fewer than two Candidates fit the budget; nothing was sent."""


class RankingDefect(StrEnum):
    """Why a ranking was rejected; the model's words are never repeated."""

    NO_RANKING = "no_ranking"
    NOT_A_LIST = "not_a_list"
    UNKNOWN_ID = "unknown_id"
    DUPLICATE_ID = "duplicate_id"
    OMITTED_ID = "omitted_id"


class InvalidRanking(RerankerMalformedResponse):
    """The model answered, but not with every ID sent exactly once."""

    def __init__(self, defect: RankingDefect) -> None:
        self.defect = defect
        super().__init__(f"invalid ranking: {defect.value}")


@dataclass(frozen=True, slots=True)
class RankingPassage:
    """One Candidate's text as the model sees it, under its opaque ID."""

    id: str
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class RankingBudget:
    """The most one ranking request may carry.

    `max_input_tokens` is counted by the model's `count_request_tokens`,
    instructions and question included.
    """

    max_candidates: int
    max_input_tokens: int

    def __post_init__(self) -> None:
        for value in (self.max_candidates, self.max_input_tokens):
            if type(value) is not int or value < 1:
                raise InvalidRerankRequest("a ranking budget must be positive integers")
        if self.max_candidates < 2:
            raise InvalidRerankRequest("a ranking needs at least two Candidates")


@dataclass(frozen=True, slots=True)
class RankingAnswer:
    """What the model returned, unchecked, and what the provider billed.

    `ranking` is None when the answer held no readable list.
    """

    ranking: object
    usage: Usage


class RankingModel(Protocol):
    """A language model that orders passages by how well they answer a question."""

    @property
    def identity(self) -> RerankerIdentity:
        """The provider and the pinned model."""
        ...

    def count_request_tokens(
        self, question: str, passages: Sequence[RankingPassage]
    ) -> int:
        """Count the whole request's input tokens, never fewer than billed."""
        ...

    async def rank(
        self,
        question: str,
        passages: Sequence[RankingPassage],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> RankingAnswer:
        """Return the model's ranking and usage, or raise a `RerankerError`."""
        ...


@dataclass(frozen=True, slots=True)
class LLMRerankResult:
    """The Candidates in their new order, and what the ranking cost.

    `ranked` counts the Candidates sent to the model, 0 when none were.
    `fallback` names the error type when retrieval's order was kept, and is
    None otherwise. `usage` is None when the model did not answer.
    """

    identity: RerankerIdentity
    candidates: tuple[RerankedCandidate, ...]
    ranked: int
    fallback: str | None
    usage: Usage | None


# The port errors a fallback may name; any other type is reported as RerankerError.
_REASONS: frozenset[type[RerankerError]] = frozenset(
    {
        InvalidRanking,
        InvalidRerankRequest,
        RankingOverBudget,
        RerankerMalformedResponse,
        RerankerRateLimited,
        RerankerRequestRefused,
        RerankerTimeout,
        RerankerUnavailable,
        RerankInputRejected,
    }
)


def checked_ranking(ranking: object, ids: Sequence[str]) -> tuple[int, ...]:
    """Return the position of each ID in the model's order, or raise.

    `ranking` is whatever the model sent, so nothing about it is trusted:
    it must be a list of strings naming every ID in `ids` exactly once.
    """
    if ranking is None:
        raise InvalidRanking(RankingDefect.NO_RANKING)
    # A list subclass could run its own code while being read; not this one.
    if type(ranking) is not list:
        raise InvalidRanking(RankingDefect.NOT_A_LIST)
    position = {value: index for index, value in enumerate(ids)}
    order: list[int] = []
    seen: set[int] = set()
    for item in cast(list[object], ranking):
        index = position.get(item) if type(item) is str else None
        if index is None:
            raise InvalidRanking(RankingDefect.UNKNOWN_ID)
        if index in seen:
            raise InvalidRanking(RankingDefect.DUPLICATE_ID)
        seen.add(index)
        order.append(index)
    if len(order) != len(ids):
        raise InvalidRanking(RankingDefect.OMITTED_ID)
    return tuple(order)


async def rerank_with_llm(
    model: RankingModel,
    question: str,
    candidates: Sequence[Candidate],
    *,
    budget: RankingBudget,
    request_id: UUID,
    timeout_seconds: float,
) -> LLMRerankResult:
    """Ask the model to order the top Candidates; if it cannot, keep retrieval's."""
    loop = asyncio.get_running_loop()
    given = read_candidates(question, candidates, timeout_seconds)
    deadline = loop.time() + timeout_seconds
    if type(budget) is not RankingBudget:
        raise InvalidRerankRequest("budget must be a RankingBudget")
    identity = model.identity
    head = given[: budget.max_candidates]
    passages = [
        RankingPassage(id=opaque_id, text=cast(str, item.text))
        for opaque_id, item in zip(_new_ids(len(head)), head, strict=True)
    ]
    sent: list[RankingPassage] = []
    usage: Usage | None = None
    failure: str | None = None
    order: tuple[int, ...] = ()
    if len(given) >= 2:
        try:
            sent = _fitting(model, question, passages, budget, deadline, loop)
            answer = await _ask(model, question, sent, request_id, deadline, loop)
            usage = answer.usage
            # A model that blocked the loop answers late: billed, not used.
            if loop.time() > deadline:
                raise RerankerTimeout()
            order = checked_ranking(answer.ranking, [passage.id for passage in sent])
        except RerankerError as error:
            # Only a known port error is named: a class name is text too.
            known = type(error) in _REASONS
            failure = type(error).__name__ if known else RerankerError.__name__
            order = ()
    if failure is not None:
        _log_fallback(request_id, identity, len(sent), failure)
    # The model's order first, then every other Candidate in retrieval's.
    ranked = set(order)
    rest = [index for index in range(len(given)) if index not in ranked]
    scores = {index: float(len(order) - 1 - place) for place, index in enumerate(order)}
    return LLMRerankResult(
        identity=identity,
        candidates=tuple(
            reranked_candidate(rank, given[index], scores.get(index, 0.0))
            for rank, index in enumerate([*order, *rest], start=1)
        ),
        ranked=len(sent),
        fallback=failure,
        usage=usage,
    )


async def _ask(
    model: RankingModel,
    question: str,
    sent: Sequence[RankingPassage],
    request_id: UUID,
    deadline: float,
    loop: asyncio.AbstractEventLoop,
) -> RankingAnswer:
    """Send one request with the time left; raise if no answer came."""
    remaining = deadline - loop.time()
    if remaining <= 0:
        raise RerankerTimeout()
    answer: object = None
    scope = asyncio.timeout(remaining)
    try:
        async with scope:
            answer = await model.rank(
                question, sent, request_id=request_id, timeout_seconds=remaining
            )
    except TimeoutError:
        # Only this deadline means a timeout; a TimeoutError the model
        # raised itself is a bug, and propagates.
        if not scope.expired():
            raise
    if answer is None:
        raise RerankerTimeout()
    if type(answer) is not RankingAnswer or type(answer.usage) is not Usage:
        raise RerankerMalformedResponse("the model did not return a RankingAnswer")
    return answer


def _fitting(
    model: RankingModel,
    question: str,
    passages: list[RankingPassage],
    budget: RankingBudget,
    deadline: float,
    loop: asyncio.AbstractEventLoop,
) -> list[RankingPassage]:
    """Return the longest head of `passages` within the token budget.

    Counting is part of the call, so it stops at the deadline too.
    """
    for count in range(len(passages), 1, -1):
        tokens = _count(model, question, passages[:count])
        if loop.time() > deadline:
            raise RerankerTimeout()
        if tokens is None:
            raise RerankInputRejected(RerankRejection.UNCOUNTABLE_TEXT, index=None)
        if tokens <= budget.max_input_tokens:
            return passages[:count]
    raise RankingOverBudget()


def _count(
    model: RankingModel, question: str, passages: Sequence[RankingPassage]
) -> int | None:
    # A counter's own error may quote the text, so it is dropped here.
    try:
        tokens = model.count_request_tokens(question, passages)
    except Exception:
        return None
    return tokens if type(tokens) is int and tokens > 0 else None


def _new_ids(count: int) -> list[str]:
    """Return `count` distinct random IDs, fresh for every request."""
    ids: list[str] = []
    while len(ids) < count:
        candidate = secrets.token_hex(_ID_BYTES)
        if candidate not in ids:
            ids.append(candidate)
    return ids


def _log_fallback(
    request_id: UUID, identity: RerankerIdentity, sent: int, reason: str
) -> None:
    """Record that retrieval's order was kept, and why. No text."""
    fields: dict[str, object] = {
        "request_id": str(request_id),
        "provider": identity.provider,
        "model": identity.model,
        "passages": sent,
        "outcome": "fallback",
        "reason": reason,
    }
    logger.warning("llm rerank fell back", extra={"rerank": fields})
