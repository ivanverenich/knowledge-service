"""The BGE-M3 server: limits, provenance, and quiet failures.

Most tests use a fake encoder, so they need neither PyTorch nor the model.
Tests marked `model` use the real tokenizer and skip when the pinned files
are not in the Hugging Face cache.
"""

import logging
import random
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

import bge_m3_server as server

PLANTED = "the salary of Olena is 9000"


class FakeEncoder:
    """Counts one token per character and records what it encoded."""

    def __init__(
        self,
        *,
        fail: bool = False,
        value: float = 1.0,
        sparse: object = None,
    ) -> None:
        self.encoded: list[list[str]] = []
        self._fail = fail
        self._value = value
        # What `encode_sparse` returns for every text; one pair by default.
        self._sparse = {7: 0.5, 3: 0.25} if sparse is None else sparse

    def count_tokens(self, text: str) -> int:
        if text == "uncountable":
            raise ValueError(f"cannot tokenize {text}")
        return len(text)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if self._fail:
            raise RuntimeError(f"out of memory while encoding {texts[0]}")
        self.encoded.append(list(texts))
        return [[self._value] + [0.0] * (server.DIMENSIONS - 1) for _ in texts]

    def encode_sparse(self, texts: Sequence[str]) -> list[dict[int, float]]:
        if self._fail:
            raise RuntimeError(f"out of memory while encoding {texts[0]}")
        self.encoded.append(list(texts))
        # Deliberately unchecked: tests pass weights the server must refuse.
        return [cast(dict[int, float], self._sparse) for _ in texts]


def client(encoder: FakeEncoder) -> TestClient:
    return TestClient(server.create_app(encoder), raise_server_exceptions=False)


def test_every_response_names_the_pinned_model() -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post("/v1/embeddings", json={"inputs": ["a", "bc"]})

    body = answer.json()
    assert answer.status_code == 200
    assert (body["model"], body["revision"], body["dimensions"]) == (
        "BAAI/bge-m3",
        server.REVISION,
        1024,
    )
    assert body["input_tokens"] == 3
    assert len(body["vectors"]) == 2
    assert encoder.encoded == [["a", "bc"]]


def test_the_model_endpoint_needs_no_text() -> None:
    answer = client(FakeEncoder()).get("/v1/model")

    assert answer.json() == {
        "model": "BAAI/bge-m3",
        "revision": server.REVISION,
        "dimensions": 1024,
    }


@pytest.mark.parametrize(
    ("inputs", "status", "reason", "index"),
    [
        (["fine", "x" * (server.MAX_INPUT_TOKENS + 1)], 413, "input_too_long", 1),
        (["x" * 8000, "y" * 8000, "z" * 500], 413, "batch_too_long", 2),
        (["fine", "   "], 422, "empty_text", 1),
        (["uncountable"], 422, "uncountable_text", 0),
    ],
    ids=["one input too long", "batch too long", "blank", "uncountable"],
)
def test_a_request_over_the_limits_is_refused_and_never_truncated(
    inputs: list[str], status: int, reason: str, index: int
) -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post("/v1/embeddings", json={"inputs": inputs})

    assert answer.status_code == status
    assert answer.json() == {"error": reason, "index": index}
    assert encoder.encoded == []


@pytest.mark.parametrize(
    "body",
    [
        {"inputs": [PLANTED, 5]},
        {"inputs": []},
        {"inputs": [PLANTED] * (server.MAX_INPUTS + 1)},
        {"inputs": [PLANTED], "extra": PLANTED},
        {"inputs": ["x" * (server.MAX_INPUT_CHARACTERS + 1)]},
        {"texts": [PLANTED]},
    ],
    ids=["not a string", "empty", "too many", "extra field", "too long", "wrong field"],
)
def test_an_invalid_request_is_refused_without_repeating_it(
    body: dict[str, object],
) -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post("/v1/embeddings", json=body)

    assert answer.status_code == 422
    assert answer.json() == {"error": "invalid_request", "index": None}
    assert encoder.encoded == []


@pytest.mark.parametrize(
    "encoder",
    [FakeEncoder(fail=True), FakeEncoder(value=float("nan"))],
    ids=["raises", "not a number"],
)
def test_an_encoding_failure_carries_no_text(
    encoder: FakeEncoder, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    answer = client(encoder).post("/v1/embeddings", json={"inputs": [PLANTED]})

    assert answer.status_code == 500
    assert answer.json() == {"error": "encoding_failed", "index": None}
    assert PLANTED not in answer.text
    assert PLANTED not in caplog.text
    assert "encoding failed" in caplog.text


def test_a_changed_or_missing_model_file_is_refused(tmp_path: Path) -> None:
    for name in server.FILE_HASHES:
        (tmp_path / name).write_bytes(b"not the pinned weights")

    with pytest.raises(server.ModelFilesChanged):
        server.check_files(tmp_path)
    (tmp_path / "pytorch_model.bin").unlink()
    with pytest.raises(server.ModelFilesChanged):
        server.check_files(tmp_path)


def test_sparse_weights_name_the_pinned_model_and_come_by_token_id() -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post("/v1/sparse", json={"inputs": ["a", "bc"]})

    assert answer.status_code == 200
    assert answer.json() == {
        "model": "BAAI/bge-m3",
        "revision": server.REVISION,
        "vocabulary_size": server.VOCABULARY_SIZE,
        "input_tokens": 3,
        "weights": [[[3, 0.25], [7, 0.5]], [[3, 0.25], [7, 0.5]]],
    }
    assert encoder.encoded == [["a", "bc"]]


def test_sparse_weights_obey_the_same_limits() -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post(
        "/v1/sparse", json={"inputs": ["x" * (server.MAX_INPUT_TOKENS + 1)]}
    )

    assert answer.status_code == 413
    assert answer.json() == {"error": "input_too_long", "index": 0}
    assert encoder.encoded == []


@pytest.mark.parametrize(
    "weights",
    [
        {7: -0.5},
        {7: 0.0},
        {7: float("nan")},
        {7: float("inf")},
        {server.VOCABULARY_SIZE: 0.5},
        {-1: 0.5},
        {"7": 0.5},
        {7: 1},
        [0.5],
    ],
    ids=[
        "negative",
        "zero",
        "not a number",
        "infinite",
        "outside the vocabulary",
        "negative token",
        "token as text",
        "weight as int",
        "not a mapping",
    ],
)
def test_invalid_sparse_weights_are_refused_without_text(
    weights: object, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    answer = client(FakeEncoder(sparse=weights)).post(
        "/v1/sparse", json={"inputs": [PLANTED]}
    )

    assert answer.status_code == 500
    assert answer.json() == {"error": "encoding_failed", "index": None}
    assert PLANTED not in answer.text
    assert PLANTED not in caplog.text


def test_a_sparse_encoding_failure_carries_no_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    answer = client(FakeEncoder(fail=True)).post(
        "/v1/sparse", json={"inputs": [PLANTED]}
    )

    assert answer.status_code == 500
    assert PLANTED not in answer.text
    assert PLANTED not in caplog.text


def _cached_model() -> Path | None:
    from huggingface_hub import snapshot_download

    try:
        folder = snapshot_download(
            server.MODEL_ID,
            revision=server.REVISION,
            allow_patterns=server.MODEL_FILES,
            local_files_only=True,
        )
    except Exception:
        return None
    return Path(folder)


@pytest.mark.model
def test_the_application_token_bound_is_never_too_low() -> None:
    # Mirrors LocalEmbeddingModel.count_tokens in the application, which must
    # never count fewer tokens than the server, or a batch it lets through
    # would be refused here.
    folder = _cached_model()
    if folder is None:
        pytest.skip("the pinned BGE-M3 files are not in the Hugging Face cache")
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))

    def bound(text: str) -> int:
        return (
            max(
                len(unicodedata.normalize("NFKC", text)),
                len(unicodedata.normalize("NFKD", text)),
            )
            + 3
        )

    every_character = [
        chr(point) for point in range(0x110000) if not 0xD800 <= point <= 0xDFFF
    ]
    rng = random.Random(9)  # noqa: S311 - a repeatable sample, not a secret
    mixed = [
        "".join(
            rng.choice(every_character) if rng.random() < 0.5 else rng.choice(" \tЇя1-")
            for _ in range(rng.randint(1, 40))
        )
        for _ in range(20_000)
    ]
    # Each character alone, decomposed, and between letters, as text has it.
    in_words = [f"a{character}b {character}" for character in every_character]
    decomposed = [unicodedata.normalize("NFD", text) for text in every_character]
    accented = [
        f"{character}\N{COMBINING ACUTE ACCENT}" * 10 for character in every_character
    ]
    texts = every_character + in_words + decomposed + accented + mixed
    for start in range(0, len(texts), 10_000):
        group = texts[start : start + 10_000]
        encoded = tokenizer.encode_batch(group, add_special_tokens=True)
        assert all(
            len(item.ids) <= bound(text)
            for text, item in zip(group, encoded, strict=True)
        )


@pytest.mark.model
def test_real_sparse_weights_are_positive_repeatable_and_skip_special_tokens() -> None:
    folder = _cached_model()
    if folder is None or not (folder / "sparse_linear.pt").is_file():
        pytest.skip("the pinned BGE-M3 files are not in the Hugging Face cache")
    from tokenizers import Tokenizer

    encoder = server.BgeM3Encoder(folder, "cpu")
    texts = ["Помилка VPN-4031 під час підключення", "відпустка"]

    first = encoder.encode_sparse(texts)
    second = encoder.encode_sparse(list(reversed(texts)))

    assert first == list(reversed(second))
    tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
    vpn = tokenizer.token_to_id("\N{LOWER ONE EIGHTH BLOCK}VPN")
    assert vpn in first[0]
    special = {0, 1, 2, 3}  # <s>, <pad>, </s>, <unk>
    for weights in first:
        assert weights
        assert not special & weights.keys()
        assert all(0 < value < 10 for value in weights.values())
