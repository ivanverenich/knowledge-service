"""The Reranker port, and reranking retrieval's Candidates with it.

Retrieval finds Candidates cheaply: a vector distance or a word match per
Chunk. A reranker reads the Question and one Candidate's text together and
returns one relevance score, which is slower but sharper, so it only
reorders the few Candidates retrieval already found.

What every `Reranker` adapter promises:

- **One call is one request, all or nothing.** `score` returns exactly one
  score per passage, in passage order, or raises a `RerankerError`.
- **Inputs are checked before anything leaves the process.** An empty
  question or passage, a text that is not valid Unicode, a pair whose
  tokens cannot be counted, too many passages, a pair over the per-pair
  limit, or a request over the total limit raises `RerankInputRejected`
  naming the index, never the text.
- **Score semantics.** A score is the model's raw relevance score: any
  finite number, higher means more relevant. Scores compare only within
  one question and one `RerankerIdentity`; they are not probabilities and
  carry no fixed threshold.
- **Errors carry no text,** not even in a chained exception.

What `rerank_candidates` promises:

- **Checked first.** A bad question, timeout, or list of Candidates raises
  `InvalidRerankRequest`, and a Candidate the model cannot take raises
  `RerankInputRejected`, before the reranker is called. Each Candidate is
  read once, at the start, so changing the list later changes nothing.
- **Batch sizing.** Candidates go to the reranker in retrieval order, in as
  few requests as its limits allow: at most `max_passages` per request, and
  at most `max_request_tokens` per request by the reranker's own count.
- **One deadline.** Counting tokens and every request must finish within
  `timeout_seconds` of the start; each request gets only the time left.
  Past it, `RerankerTimeout` is raised and nothing is returned. A reranker
  that blocks the event loop cannot be interrupted, but its late answer is
  dropped all the same.
- **Only a new order.** The result holds exactly the Candidates it was
  given, each once, ordered by score, highest first, with retrieval rank
  breaking ties. It never adds, drops, or reads a Chunk, so every
  Candidate passed the permission check of the search that found it.
- **Provenance.** The result names the reranker identity behind its scores.
- **Bugs are not hidden.** An error from the reranker that is not a
  `RerankerError` propagates unchanged.
- **Quiet.** The fields holding Document text are kept out of repr.
"""

import asyncio
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES


class RerankerError(Exception):
    """Base error for reranking failures."""


class InvalidRerankRequest(RerankerError):
    """The question, timeout, or Candidates break the contract; nothing was sent."""


class RerankRejection(StrEnum):
    """Why a reranking request was refused before calling the model."""

    EMPTY_QUERY = "empty_query"
    EMPTY_PASSAGE = "empty_passage"
    UNENCODABLE_TEXT = "unencodable_text"
    UNCOUNTABLE_TEXT = "uncountable_text"
    TOO_MANY_PASSAGES = "too_many_passages"
    PAIR_TOO_LONG = "pair_too_long"
    REQUEST_TOO_LONG = "request_too_long"


class RerankInputRejected(RerankerError):
    """The request breaks the reranker's input limits; nothing was sent."""

    def __init__(self, reason: RerankRejection, *, index: int | None) -> None:
        self.reason = reason
        self.index = index
        where = "" if index is None else f" at passage {index}"
        super().__init__(f"rerank input rejected {reason.value}{where}")


class RerankerUnavailable(RerankerError):
    """The reranker cannot currently serve the request."""


class RerankerTimeout(RerankerError):
    """The reranker did not answer within the deadline."""


class RerankerMalformedResponse(RerankerError):
    """The reranker answered, but not with one finite score per passage."""


class RerankerRequestRefused(RerankerError):
    """The reranker refused the request or is not the configured model."""


@dataclass(frozen=True, slots=True)
class RerankerIdentity:
    """Which model produced a score; scores of different identities never mix."""

    provider: str
    model: str

    def __post_init__(self) -> None:
        if type(self.provider) is not str or type(self.model) is not str:
            raise InvalidRerankRequest("provider and model must be strings")
        if not self.provider.strip() or not self.model.strip():
            raise InvalidRerankRequest("provider and model must not be empty")


@dataclass(frozen=True, slots=True)
class RerankLimits:
    """The most one `score` call may carry, in model tokens."""

    max_passages: int
    max_pair_tokens: int
    max_request_tokens: int

    def __post_init__(self) -> None:
        for value in (self.max_passages, self.max_pair_tokens, self.max_request_tokens):
            if type(value) is not int or value < 1:
                raise InvalidRerankRequest("rerank limits must be positive integers")
        if self.max_pair_tokens > self.max_request_tokens:
            raise InvalidRerankRequest("one pair cannot be allowed more than a request")


class Reranker(Protocol):
    """A model that scores how well each passage answers one question."""

    @property
    def identity(self) -> RerankerIdentity:
        """The provider and the model, with its revision."""
        ...

    @property
    def limits(self) -> RerankLimits:
        """The most one call to `score` may carry."""
        ...

    def count_pair_tokens(self, query: str, passage: str) -> int:
        """Count the pair in this model's tokens, never fewer than the model does."""
        ...

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        """Return one raw score per passage, in order, or raise a `RerankerError`."""
        ...


@dataclass(frozen=True, slots=True)
class RerankedCandidate:
    """One eligible Chunk after reranking, and where retrieval had put it.

    `rank` is its 1-based position after reranking, `score` the reranker's
    raw score, and `retrieval_rank` its rank before. The fields holding
    Document text are kept out of repr.
    """

    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    score: float
    retrieval_rank: int
    external_id: str = field(repr=False)
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_anchor: str | None = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Given:
    """One Candidate's fields, read once when reranking starts."""

    rank: object
    chunk_id: object
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    external_id: str = field(repr=False)
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_anchor: str | None = field(repr=False)
    text: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class RerankResult:
    """The Candidates in their new order, and how they were scored."""

    identity: RerankerIdentity
    candidates: tuple[RerankedCandidate, ...]
    requests: int


def check_rerank_inputs(
    query: str,
    passages: Sequence[str],
    limits: RerankLimits,
    count_pair_tokens: Callable[[str, str], int],
) -> int:
    """Refuse a request that breaks `limits`; return its total token count.

    Adapters call this before sending anything, so a rejected request never
    reaches the model. Failures are raised outside any `except` block, so no
    chained exception can carry the text.
    """
    if isinstance(passages, str):
        raise TypeError("passages must be a sequence of strings, not one string")
    if not query.strip():
        raise RerankInputRejected(RerankRejection.EMPTY_QUERY, index=None)
    if not _encodable(query):
        raise RerankInputRejected(RerankRejection.UNENCODABLE_TEXT, index=None)
    if len(passages) > limits.max_passages:
        raise RerankInputRejected(RerankRejection.TOO_MANY_PASSAGES, index=None)
    total = 0
    for index, passage in enumerate(passages):
        tokens = _pair_tokens(query, passage, index, limits, count_pair_tokens)
        total += tokens
        if total > limits.max_request_tokens:
            raise RerankInputRejected(RerankRejection.REQUEST_TOO_LONG, index=index)
    return total


def checked_scores(scores: object, *, expected: int) -> tuple[float, ...]:
    """Return the model's scores, or raise if they are not `expected` finite numbers.

    `scores` is whatever the model sent, so nothing about its shape is
    trusted: bool is refused although it is an int, and so is any number
    that is not finite as a float.
    """
    if not isinstance(scores, list | tuple):
        raise RerankerMalformedResponse("the response is not a list of scores")
    items = cast(Sequence[object], scores)
    if len(items) != expected:
        raise RerankerMalformedResponse(
            f"expected {expected} scores, received {len(items)}"
        )
    checked: list[float] = []
    for index, value in enumerate(items):
        number = _number(value)
        if number is None:
            raise RerankerMalformedResponse(f"score {index} is not a finite number")
        checked.append(number)
    return tuple(checked)


async def rerank_candidates(
    reranker: Reranker,
    question: str,
    candidates: Sequence[Candidate],
    *,
    request_id: UUID,
    timeout_seconds: float,
) -> RerankResult:
    """Score every Candidate against the question and return them in the new order."""
    loop = asyncio.get_running_loop()
    given = _snapshot(question, candidates, timeout_seconds)
    deadline = loop.time() + timeout_seconds
    identity = reranker.identity
    texts = [cast(str, item.text) for item in given]
    batches = _batches(question, texts, reranker.limits, reranker.count_pair_tokens)
    if loop.time() > deadline:
        raise RerankerTimeout()
    scores: list[float] = []
    for start, end in batches:
        remaining = deadline - loop.time()
        batch: object = None
        if remaining > 0:
            try:
                async with asyncio.timeout(remaining):
                    batch = await reranker.score(
                        question,
                        texts[start:end],
                        request_id=request_id,
                        timeout_seconds=remaining,
                    )
            except TimeoutError:
                batch = None
        # Raised outside the `except`: nothing linked can carry the texts.
        # A reranker that blocked the loop answers late; its answer is dropped.
        if batch is None or loop.time() > deadline:
            raise RerankerTimeout()
        scores += checked_scores(batch, expected=end - start)
    # Retrieval ranks are 1, 2, 3, ..., so the position breaks ties.
    order = sorted(range(len(given)), key=lambda index: (-scores[index], index))
    return RerankResult(
        identity=identity,
        candidates=tuple(
            _reranked(rank, given[index], scores[index])
            for rank, index in enumerate(order, start=1)
        ),
        requests=len(batches),
    )


def _snapshot(
    question: str, candidates: Sequence[Candidate], timeout_seconds: float
) -> tuple[_Given, ...]:
    """Check the request and read every Candidate once; raise if any is wrong."""
    if type(question) is not str or not question.strip():
        raise InvalidRerankRequest("the question must be a non-empty string")
    if (
        type(timeout_seconds) not in (int, float)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise InvalidRerankRequest("timeout_seconds must be a positive number")
    if not isinstance(candidates, tuple | list):
        raise InvalidRerankRequest("the Candidates must be a tuple or a list")
    if len(candidates) > MAX_CANDIDATES:
        raise InvalidRerankRequest(f"at most {MAX_CANDIDATES} Candidates")
    given = tuple(_read(candidate) for candidate in tuple(candidates))
    seen: set[ChunkId] = set()
    for position, item in enumerate(given, start=1):
        if item is None:
            raise InvalidRerankRequest(
                f"an unreadable Candidate at position {position}"
            )
        if type(item.chunk_id) is not ChunkId:
            raise InvalidRerankRequest(f"no ChunkId at position {position}")
        if type(item.rank) is not int or item.rank != position:
            raise InvalidRerankRequest(f"a wrong rank at position {position}")
        if type(item.text) is not str:
            raise InvalidRerankRequest(f"no text at position {position}")
        if item.chunk_id in seen:
            raise InvalidRerankRequest(f"a Chunk listed twice, at position {position}")
        seen.add(item.chunk_id)
    return cast(tuple[_Given, ...], given)


def _read(candidate: object) -> _Given | None:
    # A property may raise, and its error may quote the text: only its
    # absence is kept, and the caller raises outside any `except`.
    try:
        item = cast(Candidate, candidate)
        return _Given(
            rank=item.rank,
            chunk_id=item.chunk_id,
            document_id=item.document_id,
            source_id=item.source_id,
            content_version=item.content_version,
            ordinal=item.ordinal,
            external_id=item.external_id,
            title=item.title,
            heading_path=item.heading_path,
            source_anchor=item.source_anchor,
            text=item.text,
        )
    except Exception:
        return None


def _batches(
    question: str,
    texts: Sequence[str],
    limits: RerankLimits,
    count_pair_tokens: Callable[[str, str], int],
) -> list[tuple[int, int]]:
    """Split the texts into requests within the limits, keeping their order."""
    if not texts:
        return []
    if not question.strip():
        raise RerankInputRejected(RerankRejection.EMPTY_QUERY, index=None)
    if not _encodable(question):
        raise RerankInputRejected(RerankRejection.UNENCODABLE_TEXT, index=None)
    batches: list[tuple[int, int]] = []
    start = 0
    total = 0
    for index, text in enumerate(texts):
        tokens = _pair_tokens(question, text, index, limits, count_pair_tokens)
        if index - start == limits.max_passages or (
            total + tokens > limits.max_request_tokens
        ):
            batches.append((start, index))
            start, total = index, 0
        total += tokens
    batches.append((start, len(texts)))
    return batches


def _pair_tokens(
    query: str,
    passage: str,
    index: int,
    limits: RerankLimits,
    count_pair_tokens: Callable[[str, str], int],
) -> int:
    if not passage.strip():
        raise RerankInputRejected(RerankRejection.EMPTY_PASSAGE, index=index)
    if not _encodable(passage):
        raise RerankInputRejected(RerankRejection.UNENCODABLE_TEXT, index=index)
    tokens = _count(query, passage, count_pair_tokens)
    if tokens is None:
        raise RerankInputRejected(RerankRejection.UNCOUNTABLE_TEXT, index=index)
    if tokens > limits.max_pair_tokens:
        raise RerankInputRejected(RerankRejection.PAIR_TOO_LONG, index=index)
    return tokens


def _count(
    query: str, passage: str, count_pair_tokens: Callable[[str, str], int]
) -> int | None:
    # A tokenizer's own error may quote the text, so it is dropped here.
    try:
        tokens = count_pair_tokens(query, passage)
    except Exception:
        return None
    return tokens if type(tokens) is int and tokens > 0 else None


def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _number(value: object) -> float | None:
    # bool is a subclass of int, and a string may look numeric; neither is.
    if type(value) is not int and type(value) is not float:
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _reranked(rank: int, given: _Given, score: float) -> RerankedCandidate:
    return RerankedCandidate(
        rank=rank,
        chunk_id=cast(ChunkId, given.chunk_id),
        document_id=given.document_id,
        source_id=given.source_id,
        content_version=given.content_version,
        ordinal=given.ordinal,
        score=score,
        retrieval_rank=cast(int, given.rank),
        external_id=given.external_id,
        title=given.title,
        heading_path=given.heading_path,
        source_anchor=given.source_anchor,
        text=cast(str, given.text),
    )
