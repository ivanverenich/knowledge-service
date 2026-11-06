"""The reranker server: limits, provenance, raw scores, and quiet failures.

Most tests use a fake scorer, so they need neither PyTorch nor the model.
Tests marked `model` use the real reranker and skip when the pinned files
are not in the Hugging Face cache.
"""

import logging
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import reranker_server as server

PLANTED = "the salary of Olena is 9000"


class FakeScorer:
    """Counts one token per character of the pair; scores by passage length."""

    def __init__(self, *, fail: bool = False, scores: object = None) -> None:
        self.scored: list[tuple[str, list[str]]] = []
        self._fail = fail
        self._scores = scores

    def count_pair_tokens(self, query: str, passage: str) -> int:
        if passage == "uncountable":
            raise ValueError(f"cannot tokenize {passage}")
        return len(query) + len(passage)

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if self._fail:
            raise RuntimeError(f"failed on {query}")
        self.scored.append((query, list(passages)))
        if self._scores is not None:
            return self._scores  # type: ignore[return-value]
        return [float(len(passage)) for passage in passages]


def client(scorer: FakeScorer) -> TestClient:
    return TestClient(server.create_app(scorer, device="cpu"))


def test_scores_come_in_passage_order_with_the_pinned_provenance() -> None:
    scorer = FakeScorer()

    answer = client(scorer).post(
        "/v1/rerank", json={"query": "leave", "passages": ["abc", "a", "abcdef"]}
    )

    assert answer.status_code == 200
    assert answer.json() == {
        "model": "BAAI/bge-reranker-v2-m3",
        "revision": server.REVISION,
        "device": "cpu",
        "input_tokens": 3 * len("leave") + 3 + 1 + 6,
        "scores": [3.0, 1.0, 6.0],
    }
    assert scorer.scored == [("leave", ["abc", "a", "abcdef"])]


def test_the_model_endpoint_needs_no_text_and_states_the_limits() -> None:
    answer = client(FakeScorer()).get("/v1/model")

    assert answer.json() == {
        "model": "BAAI/bge-reranker-v2-m3",
        "revision": server.REVISION,
        "device": "cpu",
        "max_passages": 64,
        "max_pair_tokens": 8192,
        "max_request_tokens": 16_384,
    }


@pytest.mark.parametrize(
    ("body", "status", "reason", "index"),
    [
        ({"query": " ", "passages": ["a"]}, 422, "empty_query", None),
        ({"query": "q", "passages": ["a", " "]}, 422, "empty_passage", 1),
        ({"query": "q", "passages": ["uncountable"]}, 422, "uncountable_text", 0),
        ({"query": "q", "passages": ["a", "x" * 8192]}, 413, "pair_too_long", 1),
        (
            {"query": "q", "passages": ["x" * 8000, "x" * 8000, "x" * 400]},
            413,
            "request_too_long",
            2,
        ),
    ],
    ids=["empty query", "empty passage", "uncountable", "long pair", "long request"],
)
def test_a_request_over_the_limits_is_refused_and_never_truncated(
    body: dict[str, object], status: int, reason: str, index: int | None
) -> None:
    scorer = FakeScorer()

    answer = client(scorer).post("/v1/rerank", json=body)

    assert (answer.status_code, answer.json()) == (
        status,
        {"error": reason, "index": index},
    )
    assert scorer.scored == []


@pytest.mark.parametrize(
    "body",
    [
        {"query": PLANTED, "passages": []},
        {"query": PLANTED, "passages": ["a"] * 65},
        {"query": PLANTED, "passages": [PLANTED], "extra": 1},
        {"query": PLANTED, "passages": [7]},
        {"query": PLANTED, "passages": ["x" * (server.MAX_TEXT_CHARACTERS + 1)]},
    ],
    ids=["no passages", "too many", "unknown field", "not text", "too many chars"],
)
def test_an_invalid_request_is_refused_without_repeating_it(
    body: dict[str, object],
) -> None:
    answer = client(FakeScorer()).post("/v1/rerank", json=body)

    assert answer.status_code == 422
    assert answer.json() == {"error": "invalid_request", "index": None}


def test_a_scoring_failure_carries_no_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    answer = client(FakeScorer(fail=True)).post(
        "/v1/rerank", json={"query": PLANTED, "passages": [PLANTED]}
    )

    assert (answer.status_code, answer.json()) == (
        500,
        {"error": "reranking_failed", "index": None},
    )
    assert PLANTED not in answer.text
    assert PLANTED not in caplog.text


@pytest.mark.parametrize(
    "scores",
    [[1.0], [1.0, float("nan")], [1.0, float("inf")], [1.0, 2], [1.0, True], "x"],
    ids=["too few", "nan", "infinite", "int", "bool", "not a list"],
)
def test_invalid_scores_are_refused(scores: object) -> None:
    answer = client(FakeScorer(scores=scores)).post(
        "/v1/rerank", json={"query": "q", "passages": ["a", "b"]}
    )

    assert (answer.status_code, answer.json()) == (
        500,
        {"error": "reranking_failed", "index": None},
    )


def test_a_changed_or_missing_model_file_is_refused(tmp_path: Path) -> None:
    for name in server.FILE_HASHES:
        (tmp_path / name).write_bytes(b"not the model")

    with pytest.raises(server.ModelFilesChanged):
        server.check_files(tmp_path)


def _cached_model() -> Path | None:
    from huggingface_hub import snapshot_download

    try:
        folder = Path(
            snapshot_download(
                server.MODEL_ID,
                revision=server.REVISION,
                allow_patterns=server.MODEL_FILES,
                local_files_only=True,
            )
        )
    except Exception:
        return None
    return folder if (folder / "model.safetensors").is_file() else None


@pytest.fixture(scope="module")
def scorer() -> server.BgeRerankerScorer:
    folder = _cached_model()
    if folder is None:
        pytest.skip("the pinned reranker files are not cached")
    server.check_files(folder)
    return server.BgeRerankerScorer(folder, "cpu")


@pytest.mark.model
def test_the_application_pair_bound_is_never_too_low(
    scorer: server.BgeRerankerScorer,
) -> None:
    # The adapter bounds a pair by the sum of its two texts' bounds.
    import unicodedata

    def bound(text: str) -> int:
        return 3 + max(
            len(unicodedata.normalize("NFKC", text)),
            len(unicodedata.normalize("NFKD", text)),
        )

    texts = ["a", "Відпустка", "VPN-4031", "\ufb03 \u3131\u314f", "x" * 300]
    for query in texts:
        for passage in texts:
            exact = scorer.count_pair_tokens(query, passage)
            assert exact <= bound(query) + bound(passage), (query, passage)


@pytest.mark.model
def test_real_scores_prefer_the_answer_in_both_languages_and_ignore_batching(
    scorer: server.BgeRerankerScorer,
) -> None:
    passages = [
        "Employees get 24 days of paid annual leave per year.",
        "The VPN client must be updated before the first connection.",
        "Працівники мають 24 дні оплачуваної щорічної відпустки на рік.",
    ]

    english = scorer.score("How many days of annual leave do I get?", passages)
    ukrainian = scorer.score("Скільки днів відпустки мені належить?", passages)
    alone = scorer.score("How many days of annual leave do I get?", passages[:1])

    assert min(english[0], english[2]) > english[1]
    assert min(ukrainian[0], ukrainian[2]) > ukrainian[1]
    assert alone[0] == pytest.approx(english[0], abs=1e-3)
