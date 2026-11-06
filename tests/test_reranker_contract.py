"""Behavioural contract tests shared by every Reranker adapter."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import pytest

from fakes import FakeReranker
from knowledge_service.retrieval.reranking import (
    Reranker,
    RerankerMalformedResponse,
    RerankerTimeout,
    RerankInputRejected,
    RerankRejection,
)
from reranker_stubs import StubRerankerServer, local_reranker

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000033")
PLANTED = "the salary of Olena is 9000"


@dataclass(frozen=True)
class Adapter:
    """One adapter in the state a test needs, and how many calls it sent."""

    reranker: Reranker
    sent: Callable[[], int]


def fake(*, delay_seconds: float = 0.0, extra_scores: int = 0) -> Adapter:
    reranker = FakeReranker(delay_seconds=delay_seconds, extra_scores=extra_scores)
    return Adapter(reranker=reranker, sent=lambda: len(reranker.calls))


def local(server: StubRerankerServer) -> Adapter:
    return Adapter(reranker=local_reranker(server), sent=lambda: server.sent)


WORKING: dict[str, Callable[[], Adapter]] = {
    "fake": fake,
    "local": lambda: local(StubRerankerServer()),
}
SHORT_ANSWER: dict[str, Callable[[], Adapter]] = {
    "fake": lambda: fake(extra_scores=1),
    "local": lambda: local(StubRerankerServer(scores=[1.0])),
}
SLOW: dict[str, Callable[[], Adapter]] = {
    "fake": lambda: fake(delay_seconds=5.0),
    "local": lambda: local(StubRerankerServer(block=True)),
}


@pytest.mark.parametrize("name", WORKING)
async def test_one_score_per_passage_in_order_higher_for_the_answer(
    name: str,
) -> None:
    adapter = WORKING[name]()

    scores = await adapter.reranker.score(
        "annual leave days",
        ["VPN setup", "annual leave is 24 days", "leave a note"],
        request_id=REQUEST_ID,
        timeout_seconds=5.0,
    )

    assert len(scores) == 3
    assert all(type(score) is float for score in scores)
    assert scores[1] > scores[2] > scores[0]
    assert adapter.sent() == 1


@pytest.mark.parametrize("name", WORKING)
async def test_no_passages_score_nothing_and_send_nothing(name: str) -> None:
    adapter = WORKING[name]()

    scores = await adapter.reranker.score(
        "annual leave", [], request_id=REQUEST_ID, timeout_seconds=5.0
    )

    assert (scores, adapter.sent()) == ((), 0)


@pytest.mark.parametrize("name", WORKING)
@pytest.mark.parametrize(
    ("query", "passages", "reason", "index"),
    [
        (" ", ["a"], RerankRejection.EMPTY_QUERY, None),
        (PLANTED, ["a", " "], RerankRejection.EMPTY_PASSAGE, 1),
        (PLANTED, ["\ud800"], RerankRejection.UNENCODABLE_TEXT, 0),
        (PLANTED, ["a"] * 17, RerankRejection.TOO_MANY_PASSAGES, None),
    ],
    ids=["empty query", "empty passage", "unencodable", "too many"],
)
async def test_a_bad_request_is_refused_before_sending_and_names_no_text(
    name: str,
    query: str,
    passages: list[str],
    reason: RerankRejection,
    index: int | None,
) -> None:
    adapter = WORKING[name]()

    with pytest.raises(RerankInputRejected) as raised:
        await adapter.reranker.score(
            query, passages, request_id=REQUEST_ID, timeout_seconds=5.0
        )

    assert (raised.value.reason, raised.value.index) == (reason, index)
    assert PLANTED not in str(raised.value)
    assert adapter.sent() == 0


@pytest.mark.parametrize("name", SHORT_ANSWER)
async def test_a_wrong_number_of_scores_is_malformed(name: str) -> None:
    adapter = SHORT_ANSWER[name]()

    with pytest.raises(RerankerMalformedResponse):
        await adapter.reranker.score(
            "q", ["a", "b"], request_id=REQUEST_ID, timeout_seconds=5.0
        )


@pytest.mark.parametrize("name", SLOW)
async def test_the_timeout_is_honoured(name: str) -> None:
    adapter = SLOW[name]()

    with pytest.raises(RerankerTimeout) as raised:
        await adapter.reranker.score(
            PLANTED, [PLANTED], request_id=REQUEST_ID, timeout_seconds=0.05
        )

    assert PLANTED not in str(raised.value)
    assert raised.value.__context__ is None or PLANTED not in str(
        raised.value.__context__
    )
