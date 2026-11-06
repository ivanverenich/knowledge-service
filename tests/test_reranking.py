"""Reranking Candidates: a new order only, batches within limits, one deadline."""

import math
import time
from collections.abc import Sequence
from typing import cast
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fakes import FakeReranker
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.fusion import FusedCandidate
from knowledge_service.retrieval.reranking import (
    InvalidRerankRequest,
    RerankerIdentity,
    RerankerMalformedResponse,
    RerankerTimeout,
    RerankInputRejected,
    RerankLimits,
    RerankRejection,
    RerankResult,
    rerank_candidates,
)

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000055")
PLANTED = "the salary of Olena is 9000"


def candidate(rank: int, text: str, *, chunk: int | None = None) -> FusedCandidate:
    return FusedCandidate(
        rank=rank,
        chunk_id=ChunkId(UUID(int=chunk if chunk is not None else rank)),
        document_id=DocumentId(UUID(int=1000 + rank)),
        source_id=SourceId(UUID(int=7)),
        content_version=ContentVersion(1),
        ordinal=rank,
        score=1 / (60 + rank),
        contributions=(),
        external_id=f"doc-{rank}.md",
        title="Policy",
        heading_path=None,
        source_anchor=None,
        text=text,
    )


def ranked(*texts: str) -> list[FusedCandidate]:
    return [candidate(rank, text) for rank, text in enumerate(texts, start=1)]


async def rerank(
    reranker: FakeReranker,
    candidates: Sequence[Candidate],
    *,
    question: str = "annual leave days",
    timeout_seconds: float = 5.0,
) -> RerankResult:
    return await rerank_candidates(
        reranker,
        question,
        candidates,
        request_id=REQUEST_ID,
        timeout_seconds=timeout_seconds,
    )


async def test_the_best_scored_candidate_comes_first_and_keeps_its_old_rank() -> None:
    result = await rerank(
        FakeReranker(), ranked("VPN setup", "leave a note", "annual leave is 24 days")
    )

    assert [c.text for c in result.candidates] == [
        "annual leave is 24 days",
        "leave a note",
        "VPN setup",
    ]
    assert [c.rank for c in result.candidates] == [1, 2, 3]
    assert [c.retrieval_rank for c in result.candidates] == [3, 2, 1]
    assert [c.score for c in result.candidates] == [3.0, 1.0, 0.0]
    assert result.identity == RerankerIdentity(provider="fake", model="fake-reranker")
    assert result.requests == 1


async def test_equal_scores_keep_the_retrieval_order() -> None:
    result = await rerank(FakeReranker(), ranked("a", "b", "c"))

    assert [c.retrieval_rank for c in result.candidates] == [1, 2, 3]


async def test_no_candidates_send_nothing() -> None:
    reranker = FakeReranker()

    result = await rerank(reranker, [])

    assert (result.candidates, result.requests, reranker.calls) == ((), 0, [])


@settings(max_examples=60, deadline=None)
@given(
    words=st.lists(
        st.lists(
            st.sampled_from(["annual", "leave", "days", "vpn", "note"]), max_size=4
        ),
        max_size=40,
    ),
    max_passages=st.integers(min_value=1, max_value=8),
)
async def test_the_result_is_always_the_same_candidates_in_score_order(
    words: list[list[str]], max_passages: int
) -> None:
    texts = [" ".join(["x", *chosen]) for chosen in words]
    reranker = FakeReranker(
        limits=RerankLimits(
            max_passages=max_passages, max_pair_tokens=50, max_request_tokens=200
        )
    )

    result = await rerank(reranker, ranked(*texts))

    out = result.candidates
    assert sorted(c.retrieval_rank for c in out) == list(range(1, len(texts) + 1))
    assert len({c.chunk_id for c in out}) == len(texts)
    assert [c.rank for c in out] == list(range(1, len(texts) + 1))
    keys = [(-c.score, c.retrieval_rank) for c in out]
    assert keys == sorted(keys)
    # Every batch respects the limits, and together they keep the order.
    assert all(len(call) <= max_passages for call in reranker.calls)
    assert [text for call in reranker.calls for text in call] == texts


async def test_batches_stay_within_the_token_limit() -> None:
    # Each pair counts 3 question words + 2 passage words + 4 = 9 tokens.
    reranker = FakeReranker(
        limits=RerankLimits(max_passages=10, max_pair_tokens=20, max_request_tokens=20)
    )

    result = await rerank(reranker, ranked(*["leave days"] * 5))

    assert [len(call) for call in reranker.calls] == [2, 2, 1]
    assert result.requests == 3


async def test_a_pair_over_the_limit_is_refused_before_any_request() -> None:
    reranker = FakeReranker(
        limits=RerankLimits(max_passages=10, max_pair_tokens=10, max_request_tokens=20)
    )

    with pytest.raises(RerankInputRejected) as raised:
        await rerank(reranker, ranked("leave", " ".join([PLANTED] * 3)))

    assert (raised.value.reason, raised.value.index) == (
        RerankRejection.PAIR_TOO_LONG,
        1,
    )
    assert PLANTED not in str(raised.value)
    assert reranker.calls == []


async def test_one_deadline_covers_every_request() -> None:
    # Four requests of 0.3 s each cannot finish within 0.5 s.
    reranker = FakeReranker(
        limits=RerankLimits(max_passages=1, max_pair_tokens=50, max_request_tokens=50),
        delay_seconds=0.3,
    )

    with pytest.raises(RerankerTimeout):
        await rerank(reranker, ranked("a", "b", "c", "d"), timeout_seconds=0.5)

    # The second request was given only the time left, not a fresh 0.5 s.
    assert len(reranker.calls) == 2
    assert reranker.timeouts[0] == pytest.approx(0.5, abs=0.05)
    assert reranker.timeouts[1] < 0.25


async def test_a_wrong_number_of_scores_is_malformed() -> None:
    with pytest.raises(RerankerMalformedResponse):
        await rerank(FakeReranker(extra_scores=1), ranked("a", "b"))


@pytest.mark.parametrize(
    "timeout_seconds",
    [0, -1.0, math.nan, math.inf, True],
    ids=["zero", "negative", "nan", "infinite", "bool"],
)
async def test_a_bad_timeout_is_refused_before_any_request(
    timeout_seconds: float,
) -> None:
    reranker = FakeReranker()

    with pytest.raises(InvalidRerankRequest):
        await rerank(reranker, ranked("a"), timeout_seconds=timeout_seconds)

    assert reranker.calls == []


@pytest.mark.parametrize(
    "candidates",
    [
        [candidate(2, "a")],
        [candidate(1, "a", chunk=9), candidate(2, "b", chunk=9)],
        [None],
        ranked(*["a"] * 101),
        "not a list",
    ],
    ids=["wrong rank", "chunk twice", "not a candidate", "too many", "a string"],
)
async def test_bad_candidates_are_refused_before_any_request(
    candidates: object,
) -> None:
    reranker = FakeReranker()

    with pytest.raises(InvalidRerankRequest):
        await rerank(reranker, cast(Sequence[Candidate], candidates))

    assert reranker.calls == []


@pytest.mark.parametrize("question", ["", "   "], ids=["empty", "blank"])
async def test_an_empty_question_is_refused(question: str) -> None:
    with pytest.raises(InvalidRerankRequest):
        await rerank(FakeReranker(), ranked("a"), question=question)


async def test_the_text_is_kept_out_of_the_repr() -> None:
    result = await rerank(FakeReranker(), ranked(PLANTED), question=PLANTED)

    assert PLANTED not in repr(result)


class LoopBlockingReranker(FakeReranker):
    """Blocks the whole event loop while scoring, so no timeout can fire."""

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        # On purpose: this reranker misbehaves by blocking the loop.
        time.sleep(0.3)  # noqa: ASYNC251
        return await super().score(
            query, passages, request_id=request_id, timeout_seconds=timeout_seconds
        )


class SlowCountingReranker(FakeReranker):
    """Takes 0.1 s to count each pair's tokens."""

    def count_pair_tokens(self, query: str, passage: str) -> int:
        time.sleep(0.1)
        return super().count_pair_tokens(query, passage)


class ListChangingReranker(FakeReranker):
    """Changes the caller's list of Candidates while it scores them."""

    def __init__(self, given: list[FusedCandidate]) -> None:
        super().__init__()
        self._given = given

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        self._given[0] = candidate(1, "intruder annual leave days", chunk=999)
        self._given.pop()
        return await super().score(
            query, passages, request_id=request_id, timeout_seconds=timeout_seconds
        )


class TextRaises:
    """A Candidate whose text cannot be read; the error repeats a secret."""

    rank = 1
    chunk_id = ChunkId(UUID(int=1))

    @property
    def text(self) -> str:
        raise ValueError(PLANTED)


async def test_a_late_answer_is_dropped_even_when_the_reranker_blocks_the_loop() -> (
    None
):
    with pytest.raises(RerankerTimeout):
        await rerank(LoopBlockingReranker(), ranked("a"), timeout_seconds=0.1)


async def test_slow_token_counting_counts_against_the_deadline() -> None:
    reranker = SlowCountingReranker()

    with pytest.raises(RerankerTimeout):
        await rerank(reranker, ranked("a", "b", "c"), timeout_seconds=0.2)

    assert reranker.calls == []


async def test_the_result_holds_the_candidates_given_even_if_the_list_changes() -> None:
    given = ranked("annual leave", "leave", "VPN setup")
    expected = [c.chunk_id for c in given]

    result = await rerank(ListChangingReranker(given), given)

    assert sorted(c.chunk_id.value for c in result.candidates) == sorted(
        chunk_id.value for chunk_id in expected
    )
    assert all("intruder" not in c.text for c in result.candidates)


async def test_a_candidate_that_cannot_be_read_is_refused_without_its_text() -> None:
    reranker = FakeReranker()

    with pytest.raises(InvalidRerankRequest) as raised:
        await rerank(reranker, cast(Sequence[Candidate], [TextRaises()]))

    assert PLANTED not in str(raised.value)
    assert raised.value.__context__ is None
    assert reranker.calls == []
