"""The local BGE-M3 adapter: provenance, transport errors, and quiet failures."""

import asyncio
import gzip
import json
import logging
import math
import unicodedata
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from fakes import word_weights
from knowledge_service.embeddings.local_adapter import (
    BGE_M3_REVISION,
    MAX_RESPONSE_BYTES,
    LocalEmbeddingSettings,
)
from knowledge_service.embeddings.port import (
    EmbeddingError,
    EmbeddingMalformedResponse,
    EmbeddingModelIdentity,
    EmbeddingRequestRefused,
    EmbeddingTimeout,
    EmbeddingUnavailable,
)
from knowledge_service.embeddings.sparse import SparseModelIdentity
from local_stubs import URL, StubBgeM3Server, local_embedding_model

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000031")
PLANTED = "the salary of Olena is 9000"


async def embed(server: StubBgeM3Server, texts: list[str]) -> None:
    model = local_embedding_model(server)
    await model.embed(texts, request_id=REQUEST_ID, timeout_seconds=2.0)


def chain(error: BaseException) -> list[BaseException]:
    """The error and every error linked to it, as a traceback would print."""
    seen: list[BaseException] = []
    pending: list[BaseException | None] = [error]
    while pending:
        item = pending.pop()
        if item is None or any(item is known for known in seen):
            continue
        seen.append(item)
        pending += [item.__cause__, item.__context__]
    return seen


def test_the_identity_pins_the_weights_revision() -> None:
    model = local_embedding_model(StubBgeM3Server())

    assert model.identity == EmbeddingModelIdentity(
        provider="local",
        model=f"BAAI/bge-m3@{BGE_M3_REVISION}",
        dimensions=1024,
    )


async def test_one_request_carries_the_texts_and_the_request_id() -> None:
    server = StubBgeM3Server()
    model = local_embedding_model(server)

    batch = await model.embed(
        ["vacation policy", "відпустка"], request_id=REQUEST_ID, timeout_seconds=2.0
    )

    [request] = server.requests
    assert request.url == httpx.URL(f"{URL}/v1/embeddings")
    assert request.headers["X-Request-ID"] == str(REQUEST_ID)
    assert json.loads(request.content) == {"inputs": ["vacation policy", "відпустка"]}
    assert batch.usage.input_tokens == 6


@pytest.mark.parametrize(
    "server",
    [
        StubBgeM3Server(model="BAAI/bge-m3-unsupervised"),
        StubBgeM3Server(revision="0" * 40),
        StubBgeM3Server(dimensions=768, vector_dimensions=1024),
        StubBgeM3Server(dimensions=1024.0),
    ],
    ids=["other model", "other revision", "other dimensions", "dimensions as float"],
)
async def test_vectors_from_other_weights_are_refused(server: StubBgeM3Server) -> None:
    # The highest-risk edge: these vectors would be stored in this model's
    # Embedding Space and silently mixed with vectors they cannot be compared to.
    with pytest.raises(EmbeddingRequestRefused):
        await embed(server, ["vacation policy"])


@pytest.mark.parametrize(
    ("server", "error"),
    [
        (StubBgeM3Server(error=httpx.ConnectError("refused")), EmbeddingUnavailable),
        (StubBgeM3Server(error=httpx.ReadTimeout("slow")), EmbeddingTimeout),
        (StubBgeM3Server(status=503), EmbeddingUnavailable),
        (StubBgeM3Server(status=500), EmbeddingUnavailable),
        (StubBgeM3Server(status=504), EmbeddingTimeout),
        (StubBgeM3Server(status=413), EmbeddingRequestRefused),
        (StubBgeM3Server(status=422), EmbeddingRequestRefused),
        (StubBgeM3Server(status=404), EmbeddingRequestRefused),
        (StubBgeM3Server(body=b"not json"), EmbeddingMalformedResponse),
        (StubBgeM3Server(body=b"[" * 100_000), EmbeddingMalformedResponse),
        (StubBgeM3Server(body=b"[]"), EmbeddingMalformedResponse),
    ],
    ids=[
        "no server",
        "read timeout",
        "busy",
        "server error",
        "gateway timeout",
        "too long",
        "invalid",
        "wrong url",
        "not json",
        "nested too deep",
        "not an object",
    ],
)
async def test_each_failure_becomes_one_typed_error(
    server: StubBgeM3Server, error: type[EmbeddingError]
) -> None:
    with pytest.raises(error):
        await embed(server, ["vacation policy"])


@pytest.mark.parametrize(
    "fields",
    [
        {"input_tokens": True, "vectors": [[1.0] * 1024]},
        {"input_tokens": -1, "vectors": [[1.0] * 1024]},
        {"vectors": [[1.0] * 1024]},
        {"input_tokens": 3, "vectors": "1.0"},
        {"input_tokens": 3, "vectors": [[1.0] * 1024, [1.0] * 1024]},
    ],
    ids=["bool tokens", "negative tokens", "no tokens", "string", "extra vector"],
)
async def test_a_malformed_body_is_refused(fields: dict[str, object]) -> None:
    body = {"model": "BAAI/bge-m3", "revision": BGE_M3_REVISION, "dimensions": 1024}
    server = StubBgeM3Server(body=json.dumps({**body, **fields}).encode())

    with pytest.raises(EmbeddingMalformedResponse):
        await embed(server, ["vacation policy"])


async def test_a_failure_carries_no_text_in_its_chain_or_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The transport error holds the request, and the request holds the text.
    server = StubBgeM3Server(error=httpx.ConnectError(PLANTED))
    caplog.set_level(logging.DEBUG)

    with pytest.raises(EmbeddingUnavailable) as raised:
        await embed(server, [PLANTED])

    assert all(PLANTED not in repr(item) for item in chain(raised.value))
    assert raised.value.__context__ is None
    [record] = caplog.records
    assert PLANTED not in caplog.text
    assert PLANTED not in repr(record.__dict__)
    assert record.__dict__["embedding"]["outcome"] == "EmbeddingUnavailable"


async def test_the_server_check_sends_no_text_and_refuses_other_weights() -> None:
    good = StubBgeM3Server()
    other = StubBgeM3Server(revision="0" * 40)

    await local_embedding_model(good).verify_server(timeout_seconds=2.0)
    with pytest.raises(EmbeddingRequestRefused):
        await local_embedding_model(other).verify_server(timeout_seconds=2.0)

    assert [request.method for request in good.requests] == ["GET"]
    assert good.requests[0].url == httpx.URL(f"{URL}/v1/model")


async def test_the_server_check_reports_a_stopped_server() -> None:
    server = StubBgeM3Server(error=httpx.ConnectError("refused"))

    with pytest.raises(EmbeddingUnavailable):
        await local_embedding_model(server).verify_server(timeout_seconds=2.0)


async def test_a_trailing_slash_in_the_url_is_ignored() -> None:
    server = StubBgeM3Server()
    model = local_embedding_model(server, LocalEmbeddingSettings(url=f"{URL}/"))

    await model.embed(["vacation policy"], request_id=REQUEST_ID, timeout_seconds=2.0)

    assert server.requests[0].url == httpx.URL(f"{URL}/v1/embeddings")


@pytest.mark.parametrize(
    "url", ["http://[::1", "ftp://127.0.0.1:8765", "127.0.0.1:8765", "http://"]
)
def test_a_url_that_is_not_http_is_refused_at_start_up(url: str) -> None:
    with pytest.raises(ValidationError):
        LocalEmbeddingSettings(url=url)


async def test_the_server_check_keeps_the_configured_deadline() -> None:
    server = StubBgeM3Server(block=True)
    settings = LocalEmbeddingSettings(url=URL, timeout_seconds=0.05)
    model = local_embedding_model(server, settings)

    with pytest.raises(EmbeddingTimeout):
        await asyncio.wait_for(model.verify_server(timeout_seconds=math.inf), 2.0)
    with pytest.raises(ValueError, match="timeout_seconds"):
        await model.verify_server(timeout_seconds=math.nan)


def test_letters_the_tokenizer_keeps_apart_are_counted_apart() -> None:
    # Korean typed as separate letters (as macOS stores file names) stays
    # three tokens per syllable; NFKC alone would count it as one.
    model = local_embedding_model(StubBgeM3Server())
    text = unicodedata.normalize("NFD", "\uac01" * 10)

    assert model.count_tokens(text) >= 30 + 3


async def test_an_answer_over_the_size_cap_is_refused_unread() -> None:
    # A valid answer padded past the cap: nothing beyond the cap is trusted.
    valid = {
        "model": "BAAI/bge-m3",
        "revision": BGE_M3_REVISION,
        "dimensions": 1024,
        "input_tokens": 3,
        "vectors": [[1.0] * 1024],
    }
    padded = json.dumps(valid).encode() + b" " * MAX_RESPONSE_BYTES

    with pytest.raises(EmbeddingMalformedResponse, match="too large"):
        await embed(StubBgeM3Server(body=padded), ["vacation policy"])


async def test_sparse_weights_come_from_the_pinned_model_in_order() -> None:
    server = StubBgeM3Server()
    model = local_embedding_model(server)

    vectors = await model.encode_sparse(
        ["VPN error", "відпустка"], request_id=REQUEST_ID, timeout_seconds=2.0
    )

    assert model.sparse_identity == SparseModelIdentity(
        provider="local",
        model=f"BAAI/bge-m3@{BGE_M3_REVISION}",
        vocabulary_size=250_002,
    )
    assert [list(map(list, vector)) for vector in vectors] == [
        word_weights("VPN error"),
        word_weights("відпустка"),
    ]
    assert server.requests[0].url.path == "/v1/sparse"


@pytest.mark.parametrize(
    "server",
    [
        StubBgeM3Server(revision="0" * 40),
        StubBgeM3Server(model="BAAI/bge-m3-unsupervised"),
        StubBgeM3Server(vocabulary_size=250_001),
        StubBgeM3Server(vocabulary_size=250_002.0),
    ],
    ids=["other revision", "other model", "other vocabulary", "vocabulary as float"],
)
async def test_sparse_weights_from_another_model_are_refused(
    server: StubBgeM3Server,
) -> None:
    model = local_embedding_model(server)

    with pytest.raises(EmbeddingRequestRefused):
        await model.encode_sparse(
            ["vacation"], request_id=REQUEST_ID, timeout_seconds=2.0
        )


@pytest.mark.parametrize(
    "weights",
    [
        [[7, -0.5]],
        [[7, 0.0]],
        [[7, 1]],
        [[250_002, 0.5]],
        [[True, 0.5]],
        [[9, 0.5], [3, 0.5]],
        [[3, 0.5], [3, 0.5]],
        [[3, 0.5, 1.0]],
        "weights",
    ],
    ids=[
        "negative",
        "zero",
        "int weight",
        "outside the vocabulary",
        "bool token",
        "out of order",
        "repeated token",
        "three values",
        "not a list",
    ],
)
async def test_malformed_sparse_weights_are_refused(weights: object) -> None:
    model = local_embedding_model(StubBgeM3Server(sparse_weights=weights))

    with pytest.raises(EmbeddingMalformedResponse):
        await model.encode_sparse(
            ["vacation"], request_id=REQUEST_ID, timeout_seconds=2.0
        )


async def test_a_sparse_failure_carries_no_text_in_its_chain_or_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = local_embedding_model(
        StubBgeM3Server(error=httpx.ConnectError(f"refused {PLANTED}"))
    )

    with pytest.raises(EmbeddingUnavailable) as raised:
        await model.encode_sparse([PLANTED], request_id=REQUEST_ID, timeout_seconds=2.0)

    assert all(PLANTED not in repr(error) for error in chain(raised.value))
    assert PLANTED not in caplog.text
    (record,) = caplog.records
    assert record.__dict__["embedding"]["operation"] == "sparse"


async def test_more_sparse_weights_than_the_text_has_tokens_are_refused() -> None:
    # "hi" holds at most a few tokens; thousands of pairs would swell the index.
    weights = [[token, 0.5] for token in range(5_000)]
    model = local_embedding_model(StubBgeM3Server(sparse_weights=weights))

    with pytest.raises(EmbeddingMalformedResponse):
        await model.encode_sparse(["hi"], request_id=REQUEST_ID, timeout_seconds=2.0)


async def test_a_compressed_answer_is_refused_before_it_is_unpacked() -> None:
    # 64 MB of zeros packs into about 64 KB; unpacked, it would pass the cap.
    packed = gzip.compress(b"0" * (64 * 1024 * 1024))
    server = StubBgeM3Server(body=packed, headers={"Content-Encoding": "gzip"})

    with pytest.raises(EmbeddingMalformedResponse, match="encoded"):
        await embed(server, ["vacation policy"])
