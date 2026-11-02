"""Search by meaning and by words at once, under one deadline, and fuse.

`retrieve_hybrid` starts every branch at the same time, each with its own
database connection, and gives them all one deadline. Each branch that
answers in time contributes its ranked Candidates; Reciprocal Rank Fusion
(see `retrieval.fusion`) combines them into one list.

What a caller can rely on in a `HybridRetrieval`:

- **Checked first.** A bad question, reader, language, limit, timeout,
  `k`, or set of branches raises `InvalidHybridQuery` before any branch
  starts. A question must also fit the lexical search's limit of
  `MAX_QUERY_CHARACTERS`, so no valid question is refused by a branch for
  its length.
- **One deadline.** Every branch must finish within `timeout_seconds` of
  the start. A branch still running then is cancelled, its database query
  with it, and is reported as timed out. A branch that answers only after
  the deadline, because it ignored the cancel or blocked the event loop,
  is also reported as timed out, and its answer is dropped.
- **Partial failure.** A branch that times out, or fails because a
  dependency is down or slow (`EmbeddingError`, `NoCurrentEmbeddingSpace`,
  `OperationalError`, `InterfaceError`, a pool `TimeoutError`, a
  `ConnectionError`, or its own `TimeoutError`) or because it refuses the
  question (`InvalidLexicalQuery`), is reported in `branches` and left out
  of the fusion. The others still count, and `degraded` says the result is
  incomplete. When no branch succeeds, `HybridRetrievalUnavailable` is
  raised with the same diagnostics.
- **Bugs are not hidden.** Any other error propagates. Raised inside a
  branch, it cancels the other branches first and arrives inside the
  `ExceptionGroup` of the task group; a malformed branch result raises
  from fusion.
- **Eligible.** Each branch applies the reader's permission check in its
  own SQL statement, and fusion only reorders what the branches returned.
- **Bounded.** Each branch asks for `limit` Candidates; at most `limit`
  fused Candidates come back, and `truncated` says whether more exist.
- **Quiet.** Diagnostics name an error by its type, never by its message,
  which may repeat the question; the question is kept out of every repr.
"""

import asyncio
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy import exc

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime
from knowledge_service.embeddings.port import EmbeddingError, EmbeddingModel
from knowledge_service.language import Language
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.fusion import (
    DEFAULT_RRF_K,
    FusedCandidate,
    RetrievalBranch,
    fuse_ranked_candidates,
)
from knowledge_service.retrieval.lexical_search import (
    MAX_QUERY_CHARACTERS,
    InvalidLexicalQuery,
    search_lexical_chunks,
)
from knowledge_service.retrieval.vector_search import (
    MAX_CANDIDATES,
    NoCurrentEmbeddingSpace,
    search_similar_chunks,
)

# Errors that mean a branch cannot answer this Question now: a dependency
# is down or slow, or the branch refuses this Question. Anything else, such
# as SQL that no longer compiles or a missing file, is a bug and propagates.

_BRANCH_FAILURES = (
    EmbeddingError,
    NoCurrentEmbeddingSpace,
    InvalidLexicalQuery,
    exc.OperationalError,
    exc.InterfaceError,
    exc.TimeoutError,
    ConnectionError,
    TimeoutError,
)


class HybridRetrievalError(Exception):
    """Hybrid retrieval could not return Candidates."""


class InvalidHybridQuery(HybridRetrievalError):
    """The question or a setting is not valid; no branch was started."""


class HybridRetrievalUnavailable(HybridRetrievalError):
    """No branch returned Candidates in time; `branches` says why."""

    def __init__(self, branches: tuple[BranchDiagnostics, ...]) -> None:
        self.branches = branches
        super().__init__("no retrieval branch succeeded")


class BranchStatus(StrEnum):
    """How one branch ended."""

    SUCCEEDED = "succeeded"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BranchDiagnostics:
    """How one branch ended, how long it took, and what it found.

    `error` is the type name of the error that failed the branch, or None.
    """

    branch: RetrievalBranch
    status: BranchStatus
    elapsed_seconds: float
    candidates: int
    truncated: bool
    error: str | None


@dataclass(frozen=True, slots=True)
class BranchRequest:
    """What every branch is asked: the same question, reader, and deadline.

    `timeout_seconds` is the time left until the shared deadline, for a
    branch that calls a provider with its own timeout.
    """

    question: str = field(repr=False)
    language: Language | None
    context: AuthorizationContext
    limit: int
    request_id: UUID
    timeout_seconds: float


class BranchResult(Protocol):
    """What a branch returns: ranked Candidates and whether more exist."""

    @property
    def candidates(self) -> Sequence[Candidate]: ...

    @property
    def truncated(self) -> bool: ...


type BranchSearch = Callable[[BranchRequest], Awaitable[BranchResult]]


@dataclass(frozen=True, slots=True)
class HybridRetrieval:
    """The fused Candidates of one Question, and how each branch ended."""

    limit: int
    k: int
    candidates: tuple[FusedCandidate, ...]
    truncated: bool
    branches: tuple[BranchDiagnostics, ...]

    @property
    def degraded(self) -> bool:
        """True when a branch timed out or failed, so Candidates may be missing."""
        return any(b.status is not BranchStatus.SUCCEEDED for b in self.branches)


@dataclass(slots=True)
class _Ended:
    diagnostics: BranchDiagnostics
    result: BranchResult | None


async def retrieve_hybrid(
    question: str,
    *,
    context: AuthorizationContext,
    branches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
    timeout_seconds: float,
    request_id: UUID,
    language: Language | None = None,
    k: int = DEFAULT_RRF_K,
) -> HybridRetrieval:
    """Run every branch at once under one deadline and fuse what returns."""
    _check(
        question,
        context=context,
        language=language,
        branches=branches,
        limit=limit,
        timeout=timeout_seconds,
        k=k,
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    request = BranchRequest(
        question=question,
        language=language,
        context=context,
        limit=limit,
        request_id=request_id,
        timeout_seconds=timeout_seconds,
    )
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(
                _run(
                    branch,
                    branches[branch],
                    request,
                    deadline,
                )
            )
            for branch in RetrievalBranch
            if branch in branches
        ]
    ended = [task.result() for task in tasks]
    diagnostics = tuple(item.diagnostics for item in ended)
    succeeded = {
        item.diagnostics.branch: item.result
        for item in ended
        if item.result is not None
    }
    if not succeeded:
        raise HybridRetrievalUnavailable(diagnostics)
    fused = fuse_ranked_candidates(
        {branch: tuple(result.candidates) for branch, result in succeeded.items()},
        k=k,
    )
    return HybridRetrieval(
        limit=limit,
        k=k,
        candidates=fused[:limit],
        truncated=len(fused) > limit
        or any(result.truncated for result in succeeded.values()),
        branches=diagnostics,
    )


def dense_branch(runtime: DatabaseRuntime, model: EmbeddingModel) -> BranchSearch:
    """Embed the question, then search the current Embedding Space."""

    async def search(request: BranchRequest) -> BranchResult:
        batch = await model.embed(
            [request.question],
            request_id=request.request_id,
            timeout_seconds=request.timeout_seconds,
        )
        async with runtime.transaction() as connection:
            return await search_similar_chunks(
                connection,
                identity=batch.identity,
                query=batch.vectors[0],
                context=request.context,
                limit=request.limit,
            )

    return search


def lexical_branch(runtime: DatabaseRuntime) -> BranchSearch:
    """Search the stored words, routed by the question's language."""

    async def search(request: BranchRequest) -> BranchResult:
        async with runtime.transaction() as connection:
            return await search_lexical_chunks(
                connection,
                text=request.question,
                context=request.context,
                limit=request.limit,
                language=request.language,
            )

    return search


def _check(
    question: str,
    *,
    context: AuthorizationContext,
    language: Language | None,
    branches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
    timeout: float,
    k: int,
) -> None:
    if type(question) is not str or not question.strip():
        raise InvalidHybridQuery("the question must be text that is not blank")
    if len(question) > MAX_QUERY_CHARACTERS:
        raise InvalidHybridQuery(
            f"the question must be at most {MAX_QUERY_CHARACTERS} characters"
        )
    # PostgreSQL text cannot hold NUL, and UTF-8 cannot hold a lone surrogate.
    if "\x00" in question or not _encodable(question):
        raise InvalidHybridQuery("the question must be valid text")
    if type(context) is not AuthorizationContext:
        raise InvalidHybridQuery("context must be an AuthorizationContext")
    if language is not None and type(language) is not Language:
        raise InvalidHybridQuery("language must be a Language or None")
    # bool is an int, so `True` would otherwise pass as a limit of 1.
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise InvalidHybridQuery(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    # The deadline is a float, so a huge int must not overflow it.
    if type(timeout) not in (int, float) or not 0 < timeout <= sys.float_info.max:
        raise InvalidHybridQuery("timeout_seconds must be finite and positive")
    if type(k) is not int or k < 1:
        raise InvalidHybridQuery("k must be a positive int")
    # Checked at run time too, because a caller may pass any object.
    if not isinstance(cast(object, branches), Mapping) or not branches:
        raise InvalidHybridQuery("at least one branch is required")
    for branch, search in branches.items():
        if type(branch) is not RetrievalBranch or not callable(search):
            raise InvalidHybridQuery(
                "every branch must map a RetrievalBranch to a search"
            )


def _encodable(text: str) -> bool:
    try:
        text.encode()
    except UnicodeEncodeError:
        return False
    return True


async def _run(
    branch: RetrievalBranch,
    search: BranchSearch,
    request: BranchRequest,
    deadline: float,
) -> _Ended:
    loop = asyncio.get_running_loop()
    started = loop.time()
    remaining = max(deadline - started, 0.0)
    budget = asyncio.timeout_at(deadline)
    status = BranchStatus.FAILED
    error: str | None = None
    result: BranchResult | None = None
    try:
        async with budget:
            result = await search(replace(request, timeout_seconds=remaining))
        status = BranchStatus.SUCCEEDED
    except _BRANCH_FAILURES as failure:
        error = type(failure).__name__
    # Past the deadline, whatever the branch did counts as timed out: the
    # cancel itself, an error raised while it was being cancelled, or a late
    # answer from a branch that ignored the cancel or blocked the event loop.
    if budget.expired() or loop.time() > deadline:
        status, error, result = BranchStatus.TIMED_OUT, None, None
    return _Ended(
        BranchDiagnostics(
            branch=branch,
            status=status,
            elapsed_seconds=loop.time() - started,
            candidates=len(result.candidates) if result is not None else 0,
            truncated=result.truncated if result is not None else False,
            error=error,
        ),
        result,
    )
