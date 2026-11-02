"""Checks on running both retrieval branches at once and fusing the result.

The branches here are fakes, so each test decides exactly when a branch
answers, fails, or never answers.
"""

import asyncio
import contextlib
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import exc

from knowledge_service.access import AuthorizationContext
from knowledge_service.documents import ContentVersion
from knowledge_service.embeddings.port import EmbeddingUnavailable
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId, UserId
from knowledge_service.language import Language
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    BranchRequest,
    BranchResult,
    BranchSearch,
    BranchStatus,
    HybridRetrieval,
    HybridRetrievalUnavailable,
    InvalidHybridQuery,
    retrieve_hybrid,
)
from knowledge_service.retrieval.lexical_search import InvalidLexicalQuery
from knowledge_service.retrieval.vector_search import (
    InvalidVectorQuery,
    NoCurrentEmbeddingSpace,
    VectorCandidate,
)

DENSE = RetrievalBranch.DENSE
LEXICAL = RetrievalBranch.LEXICAL
CONTEXT = AuthorizationContext(user_id=UserId(UUID(int=7)))
REQUEST_ID = UUID(int=99)
QUESTION = "How do I request parental leave?"
MARKER = "zebramarker"
# Long enough for every fake that answers, short enough to keep tests fast.
SHORT = 0.05


def _candidate(number: int, rank: int) -> VectorCandidate:
    return VectorCandidate(
        rank=rank,
        chunk_id=ChunkId(UUID(int=number)),
        document_id=DocumentId(UUID(int=10_000 + number)),
        source_id=SourceId(UUID(int=1)),
        content_version=ContentVersion(1),
        ordinal=0,
        distance=rank / 100,
        external_id=f"doc-{number}.txt",
        title=f"Title {number}",
        heading_path=None,
        source_anchor=None,
        text=f"Text of Chunk {number}",
    )


def _ranked(*numbers: int) -> tuple[VectorCandidate, ...]:
    return tuple(_candidate(number, rank) for rank, number in enumerate(numbers, 1))


@dataclass(frozen=True, slots=True)
class Found:
    candidates: Sequence[Candidate]
    truncated: bool = False


@dataclass(slots=True)
class FakeBranch:
    """A branch that records what it was asked and answers as told."""

    found: Sequence[Candidate] = ()
    truncated: bool = False
    error: Exception | None = None
    # When set, the branch waits for it before answering.
    wait_for: asyncio.Event | None = None
    # When set, the branch sets it as soon as it starts.
    started: asyncio.Event | None = None
    requests: list[BranchRequest] = field(default_factory=list[BranchRequest])
    cancelled: bool = False

    async def __call__(self, request: BranchRequest) -> BranchResult:
        self.requests.append(request)
        if self.started is not None:
            self.started.set()
        try:
            if self.wait_for is not None:
                await self.wait_for.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error is not None:
            raise self.error
        return Found(self.found, self.truncated)


def _never() -> asyncio.Event:
    return asyncio.Event()


async def _retrieve(
    branches: Mapping[RetrievalBranch, BranchSearch],
    *,
    question: str = QUESTION,
    limit: int = 10,
    timeout_seconds: float = 5.0,
    k: int = 60,
) -> HybridRetrieval:
    return await retrieve_hybrid(
        question,
        context=CONTEXT,
        branches=branches,
        limit=limit,
        timeout_seconds=timeout_seconds,
        request_id=REQUEST_ID,
        language=Language.ENGLISH,
        k=k,
    )


def _order(result_candidates: Sequence[Candidate]) -> list[int]:
    return [candidate.chunk_id.value.int for candidate in result_candidates]


async def test_both_branches_are_fused_and_reported() -> None:
    dense = FakeBranch(found=_ranked(1, 2, 3))
    lexical = FakeBranch(found=_ranked(3, 4))

    result = await _retrieve({DENSE: dense, LEXICAL: lexical})

    assert _order(result.candidates) == [3, 1, 2, 4]
    assert [part.branch for part in result.candidates[0].contributions] == [
        DENSE,
        LEXICAL,
    ]
    assert [(b.branch, b.status, b.candidates) for b in result.branches] == [
        (DENSE, BranchStatus.SUCCEEDED, 3),
        (LEXICAL, BranchStatus.SUCCEEDED, 2),
    ]
    assert all(b.error is None and b.elapsed_seconds >= 0 for b in result.branches)
    assert not result.degraded
    assert not result.truncated
    assert (result.limit, result.k) == (10, 60)


async def test_every_branch_gets_the_same_question_reader_and_deadline() -> None:
    dense, lexical = FakeBranch(), FakeBranch()

    await _retrieve({DENSE: dense, LEXICAL: lexical}, limit=7, timeout_seconds=5.0)

    for branch in (dense, lexical):
        (request,) = branch.requests
        assert request.question == QUESTION
        assert request.context is CONTEXT
        assert request.language is Language.ENGLISH
        assert request.limit == 7
        assert request.request_id == REQUEST_ID
        assert 0 < request.timeout_seconds <= 5.0


async def test_the_branches_run_at_the_same_time() -> None:
    # Each branch waits until the other has started, so run one after the
    # other they would both miss the deadline.
    dense_started, lexical_started = asyncio.Event(), asyncio.Event()
    dense = FakeBranch(
        found=_ranked(1), started=dense_started, wait_for=lexical_started
    )
    lexical = FakeBranch(
        found=_ranked(2), started=lexical_started, wait_for=dense_started
    )

    result = await _retrieve({DENSE: dense, LEXICAL: lexical}, timeout_seconds=1.0)

    assert [b.status for b in result.branches] == [BranchStatus.SUCCEEDED] * 2


@pytest.mark.parametrize(("slow", "fast"), [(DENSE, LEXICAL), (LEXICAL, DENSE)])
async def test_a_branch_that_misses_the_deadline_is_cancelled_and_reported(
    slow: RetrievalBranch, fast: RetrievalBranch
) -> None:
    late = FakeBranch(found=_ranked(1), wait_for=_never())
    on_time = FakeBranch(found=_ranked(2, 3))
    loop = asyncio.get_running_loop()
    started = loop.time()

    result = await _retrieve({slow: late, fast: on_time}, timeout_seconds=SHORT)

    assert loop.time() - started < 1.0
    assert late.cancelled
    assert _order(result.candidates) == [2, 3]
    report = {b.branch: b for b in result.branches}
    assert report[slow].status is BranchStatus.TIMED_OUT
    assert (report[slow].candidates, report[slow].error) == (0, None)
    assert report[slow].elapsed_seconds >= SHORT * 0.9
    assert report[fast].status is BranchStatus.SUCCEEDED
    assert report[fast].elapsed_seconds < SHORT
    assert result.degraded


@pytest.mark.parametrize(
    "error",
    [
        EmbeddingUnavailable(),
        NoCurrentEmbeddingSpace("the current embedding space is another model"),
        InvalidLexicalQuery("the query has no words"),
        exc.OperationalError("SELECT 1", {}, Exception("server closed")),
        exc.InterfaceError("SELECT 1", {}, Exception("connection already closed")),
        exc.TimeoutError("QueuePool limit reached"),
        ConnectionResetError(),
        TimeoutError(),
    ],
    ids=lambda error: type(error).__name__,
)
async def test_a_branch_that_fails_is_reported_by_its_error_type(
    error: Exception,
) -> None:
    dense = FakeBranch(error=error)
    lexical = FakeBranch(found=_ranked(5))

    result = await _retrieve({DENSE: dense, LEXICAL: lexical})

    assert _order(result.candidates) == [5]
    assert result.branches[0].status is BranchStatus.FAILED
    assert result.branches[0].error == type(error).__name__
    assert result.degraded


async def test_no_branch_succeeding_raises_unavailable_with_diagnostics() -> None:
    dense = FakeBranch(error=EmbeddingUnavailable())
    lexical = FakeBranch(wait_for=_never())

    with pytest.raises(HybridRetrievalUnavailable) as caught:
        await _retrieve({DENSE: dense, LEXICAL: lexical}, timeout_seconds=SHORT)

    assert [(b.status, b.error) for b in caught.value.branches] == [
        (BranchStatus.FAILED, "EmbeddingUnavailable"),
        (BranchStatus.TIMED_OUT, None),
    ]


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("a bug"),
        exc.ProgrammingError("SELECT no_such_column", {}, Exception("no column")),
        exc.CompileError("cannot compile"),
        FileNotFoundError("config.toml"),
        InvalidVectorQuery("the query has the wrong length"),
    ],
    ids=lambda error: type(error).__name__,
)
async def test_a_bug_in_a_branch_propagates_and_cancels_the_other(
    error: Exception,
) -> None:
    dense = FakeBranch(error=error)
    lexical = FakeBranch(wait_for=_never())

    with pytest.raises(ExceptionGroup) as caught:
        await _retrieve({DENSE: dense, LEXICAL: lexical})

    assert caught.group_contains(type(error))
    assert lexical.cancelled


async def _answers_despite_the_cancel(request: BranchRequest) -> BranchResult:
    with contextlib.suppress(asyncio.CancelledError):
        await _never().wait()
    return Found(_ranked(7))


async def _blocks_the_event_loop(request: BranchRequest) -> BranchResult:
    # Blocks on purpose, as CPU-bound or synchronous work would.
    time.sleep(SHORT * 3)  # noqa: ASYNC251
    return Found(_ranked(7))


async def _fails_while_cancelled(request: BranchRequest) -> BranchResult:
    try:
        await _never().wait()
    except asyncio.CancelledError:
        raise exc.OperationalError("ROLLBACK", {}, Exception("lost")) from None
    return Found(_ranked(7))


@pytest.mark.parametrize(
    "late",
    [_answers_despite_the_cancel, _blocks_the_event_loop, _fails_while_cancelled],
    ids=lambda search: search.__name__,
)
async def test_whatever_a_branch_does_after_the_deadline_counts_as_timed_out(
    late: BranchSearch,
) -> None:
    # Dense starts first and answers at once, before the late branch runs.
    on_time = FakeBranch(found=_ranked(2))

    result = await _retrieve({DENSE: on_time, LEXICAL: late}, timeout_seconds=SHORT)

    assert _order(result.candidates) == [2]
    assert [(b.status, b.candidates, b.error) for b in result.branches] == [
        (BranchStatus.SUCCEEDED, 1, None),
        (BranchStatus.TIMED_OUT, 0, None),
    ]


async def test_cancelling_the_caller_cancels_every_branch() -> None:
    dense_started, lexical_started = asyncio.Event(), asyncio.Event()
    dense = FakeBranch(started=dense_started, wait_for=_never())
    lexical = FakeBranch(started=lexical_started, wait_for=_never())
    task = asyncio.create_task(_retrieve({DENSE: dense, LEXICAL: lexical}))
    await dense_started.wait()
    await lexical_started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert dense.cancelled
    assert lexical.cancelled


async def test_a_branch_left_out_is_neither_run_nor_reported() -> None:
    lexical = FakeBranch(found=_ranked(4, 2))

    result = await _retrieve({LEXICAL: lexical})

    assert _order(result.candidates) == [4, 2]
    assert [b.branch for b in result.branches] == [LEXICAL]
    assert not result.degraded


@pytest.mark.parametrize(
    ("limit", "dense_truncated", "expected", "truncated"),
    [
        (2, False, [3, 1], True),
        (4, False, [3, 1, 2, 4], False),
        (4, True, [3, 1, 2, 4], True),
        (10, False, [3, 1, 2, 4], False),
    ],
)
async def test_the_fused_list_is_cut_to_the_limit_and_says_when_more_exist(
    limit: int, dense_truncated: bool, expected: list[int], truncated: bool
) -> None:
    dense = FakeBranch(found=_ranked(1, 2, 3), truncated=dense_truncated)
    lexical = FakeBranch(found=_ranked(3, 4))

    result = await _retrieve({DENSE: dense, LEXICAL: lexical}, limit=limit)

    assert _order(result.candidates) == expected
    assert [b.truncated for b in result.branches] == [dense_truncated, False]
    assert [candidate.rank for candidate in result.candidates] == list(
        range(1, len(expected) + 1)
    )
    assert result.truncated is truncated


async def test_k_reaches_the_fusion() -> None:
    dense = FakeBranch(found=_ranked(1, 11, 12, 2))
    lexical = FakeBranch(found=_ranked(21, 22, 23, 2))

    by_default = await _retrieve({DENSE: dense, LEXICAL: lexical})
    by_one = await _retrieve({DENSE: dense, LEXICAL: lexical}, k=1)

    assert _order(by_default.candidates)[0] == 2
    assert _order(by_one.candidates)[0] == 1
    assert by_one.k == 1
    assert by_one.candidates[0].contributions[0].contribution == 1 / 2


@pytest.mark.parametrize(
    "change",
    [
        {"question": ""},
        {"question": "   \n"},
        {"question": None},
        {"question": 5},
        {"question": "x" * 1_001},
        {"question": "parental" + chr(0) + "leave"},
        {"question": "leave" + chr(0xD800)},
        {"context": None},
        {"language": "english"},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"limit": 1.0},
        {"timeout_seconds": 0},
        {"timeout_seconds": -1.0},
        {"timeout_seconds": math.nan},
        {"timeout_seconds": math.inf},
        {"timeout_seconds": True},
        {"timeout_seconds": 10**400},
        {"timeout_seconds": "1"},
        {"k": 0},
        {"k": True},
        {"branches": {}},
        {"branches": "dense"},
        {"branches": {DENSE: None}},
    ],
    ids=repr,
)
async def test_bad_input_is_refused_before_any_branch_starts(
    change: dict[str, object],
) -> None:
    dense, lexical = FakeBranch(), FakeBranch()
    arguments: dict[str, object] = {
        "question": QUESTION,
        "context": CONTEXT,
        "branches": {DENSE: dense, LEXICAL: lexical},
        "limit": 10,
        "timeout_seconds": 5.0,
        "request_id": REQUEST_ID,
        "k": 60,
    }
    arguments.update(change)

    with pytest.raises(InvalidHybridQuery):
        await retrieve_hybrid(**cast(dict[str, Any], arguments))

    assert dense.requests == lexical.requests == []


async def test_a_branch_that_is_not_a_retrieval_branch_is_refused() -> None:
    lexical = FakeBranch()
    branches = cast(Mapping[RetrievalBranch, BranchSearch], {"lexical": lexical})

    with pytest.raises(InvalidHybridQuery):
        await _retrieve(branches)

    assert lexical.requests == []


async def test_nothing_shows_the_question_or_an_error_message() -> None:
    question = f"{MARKER} parental leave"
    dense = FakeBranch(error=EmbeddingUnavailable(MARKER))
    lexical = FakeBranch(found=_ranked(1))

    result = await _retrieve({DENSE: dense, LEXICAL: lexical}, question=question)

    assert MARKER not in repr(result)
    assert MARKER not in repr(dense.requests[0])

    failing = FakeBranch(error=InvalidLexicalQuery(MARKER))
    with pytest.raises(HybridRetrievalUnavailable) as caught:
        await _retrieve({LEXICAL: failing}, question=question)
    error = caught.value
    shown = [str(error), repr(error.args), repr(error.branches)]
    assert all(MARKER not in text for text in shown)
    assert error.__cause__ is None
    assert error.__context__ is None
