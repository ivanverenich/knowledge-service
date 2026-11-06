"""The local reranker adapter: provenance, transport failures, and quiet logs."""

import gzip
import json
import logging
from typing import cast
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from knowledge_service.retrieval.local_reranker import (
    MAX_RESPONSE_BYTES,
    LocalReranker,
    LocalRerankerSettings,
)
from knowledge_service.retrieval.reranking import (
    InvalidRerankRequest,
    RerankerError,
    RerankerMalformedResponse,
    RerankerRequestRefused,
    RerankerTimeout,
    RerankerUnavailable,
    RerankInputRejected,
    RerankRejection,
)
from reranker_stubs import URL, StubRerankerServer, local_reranker

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000044")
PLANTED = "the salary of Olena is 9000"
PROVENANCE = (
    '"model": "BAAI/bge-reranker-v2-m3",'
    ' "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e", "device": "cpu"'
)
NO_SCORES = ("{" + PROVENANCE + "}").encode()
# Python's JSON parser reads NaN, so the adapter must refuse it itself.
NAN_SCORE = ("{" + PROVENANCE + ', "scores": [1.0, NaN]}').encode()
# 1 MB of zeros packs into about 1 KB; unpacked, it would pass the cap.
PACKED = gzip.compress(b"0" * (1024 * 1024))


async def score(server: StubRerankerServer) -> tuple[float, ...]:
    return await local_reranker(server).score(
        PLANTED, [PLANTED, "VPN setup"], request_id=REQUEST_ID, timeout_seconds=5.0
    )


async def test_the_request_carries_the_question_passages_and_request_id() -> None:
    server = StubRerankerServer()

    scores = await score(server)

    (request,) = server.requests
    assert request.url == httpx.URL(URL + "/v1/rerank")
    assert json.loads(request.content) == {
        "query": PLANTED,
        "passages": [PLANTED, "VPN setup"],
    }
    assert request.headers["X-Request-ID"] == str(REQUEST_ID)
    assert request.headers["Accept-Encoding"] == "identity"
    assert scores == (6.0, 0.0)


def test_the_identity_names_the_pinned_revision() -> None:
    reranker = local_reranker(StubRerankerServer())

    assert reranker.identity.provider == "local"
    assert reranker.identity.model == (
        "BAAI/bge-reranker-v2-m3@953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
    )


@pytest.mark.parametrize(
    "server",
    [
        StubRerankerServer(model="BAAI/bge-reranker-base"),
        StubRerankerServer(revision="main"),
    ],
    ids=["other model", "other revision"],
)
async def test_scores_from_another_model_are_refused(
    server: StubRerankerServer,
) -> None:
    with pytest.raises(RerankerRequestRefused):
        await score(server)


@pytest.mark.parametrize(
    ("server", "error"),
    [
        (StubRerankerServer(status=503), RerankerUnavailable),
        (StubRerankerServer(status=500), RerankerUnavailable),
        (StubRerankerServer(status=504), RerankerTimeout),
        (StubRerankerServer(status=413), RerankerRequestRefused),
        (StubRerankerServer(error=httpx.ConnectError("refused")), RerankerUnavailable),
        (StubRerankerServer(error=httpx.ReadTimeout("slow")), RerankerTimeout),
        (StubRerankerServer(body=b"not json"), RerankerMalformedResponse),
        (StubRerankerServer(body=b"[1.0]"), RerankerMalformedResponse),
        (StubRerankerServer(body=NAN_SCORE), RerankerMalformedResponse),
        (StubRerankerServer(scores=[1.0, True]), RerankerMalformedResponse),
        (StubRerankerServer(scores=[1.0, "2.0"]), RerankerMalformedResponse),
        (StubRerankerServer(body=NO_SCORES), RerankerMalformedResponse),
        (
            StubRerankerServer(body=b" " * (MAX_RESPONSE_BYTES + 1)),
            RerankerMalformedResponse,
        ),
        (
            StubRerankerServer(body=PACKED, headers={"Content-Encoding": "gzip"}),
            RerankerMalformedResponse,
        ),
    ],
    ids=[
        "503",
        "500",
        "504",
        "413",
        "connection refused",
        "read timeout",
        "not json",
        "not an object",
        "nan score",
        "bool score",
        "string score",
        "no scores",
        "too large",
        "compressed",
    ],
)
async def test_every_failure_is_typed_and_carries_no_text(
    server: StubRerankerServer,
    error: type[RerankerError],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    with pytest.raises(error) as raised:
        await score(server)

    assert PLANTED not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert PLANTED not in caplog.text


async def test_one_log_record_per_call_with_counts_and_device_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    await score(StubRerankerServer(device="mps"))

    (record,) = [r for r in caplog.records if r.name.endswith("local_reranker")]
    assert record.rerank == {  # type: ignore[attr-defined]
        "request_id": str(REQUEST_ID),
        "provider": "local",
        "model": "BAAI/bge-reranker-v2-m3@953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
        "passages": 2,
        "latency_ms": record.rerank["latency_ms"],  # type: ignore[attr-defined]
        "outcome": "succeeded",
        "device": "mps",
    }
    assert PLANTED not in caplog.text


async def test_verify_server_returns_the_device_and_sends_no_text() -> None:
    server = StubRerankerServer(device="mps")

    device = await local_reranker(server).verify_server(timeout_seconds=5.0)

    assert device == "mps"
    assert [request.method for request in server.requests] == ["GET"]


@pytest.mark.parametrize(
    "device",
    [None, "", "x" * 17, "gpu\u00e9", "cpu\n## |x|", "CPU", 3],
    ids=["none", "empty", "long", "accent", "markdown", "upper case", "int"],
)
async def test_a_server_that_names_no_plain_device_is_malformed(
    device: object,
) -> None:
    with pytest.raises(RerankerMalformedResponse):
        await local_reranker(StubRerankerServer(device=device)).verify_server(
            timeout_seconds=5.0
        )


async def test_a_pair_over_the_limit_is_refused_before_sending() -> None:
    server = StubRerankerServer()
    reranker = local_reranker(
        server, LocalRerankerSettings(url=URL, max_pair_tokens=20)
    )

    with pytest.raises(RerankInputRejected) as raised:
        await reranker.score(
            "q", ["x" * 20], request_id=REQUEST_ID, timeout_seconds=5.0
        )

    assert (raised.value.reason, raised.value.index) == (
        RerankRejection.PAIR_TOO_LONG,
        0,
    )
    assert server.sent == 0


def test_the_pair_bound_adds_both_texts_bounds() -> None:
    reranker = local_reranker(StubRerankerServer())

    # NFKD splits the ligature into three letters; each text adds three.
    assert reranker.count_pair_tokens("ab", "ﬃ") == (2 + 3) + (3 + 3)


@pytest.mark.parametrize("url", ["", "ftp://x", "not a url", "http://"])
def test_a_bad_url_fails_at_start_up(url: str) -> None:
    with pytest.raises(ValidationError):
        LocalRerankerSettings(url=url)


@pytest.mark.parametrize(
    "timeout_seconds",
    [float("nan"), 0.0, -1.0, "5", True],
    ids=["nan", "zero", "negative", "string", "bool"],
)
async def test_a_bad_timeout_is_a_typed_refusal(timeout_seconds: object) -> None:
    server = StubRerankerServer()

    with pytest.raises(InvalidRerankRequest):
        await local_reranker(server).score(
            "q",
            ["a"],
            request_id=REQUEST_ID,
            timeout_seconds=cast(float, timeout_seconds),
        )

    assert server.sent == 0


async def test_a_closed_client_is_unavailable_and_quiet() -> None:
    server = StubRerankerServer()
    client = httpx.AsyncClient(transport=httpx.MockTransport(server.handle))
    await client.aclose()
    reranker = LocalReranker(client, LocalRerankerSettings(url=URL))

    with pytest.raises(RerankerUnavailable) as raised:
        await reranker.score(
            PLANTED, [PLANTED], request_id=REQUEST_ID, timeout_seconds=5.0
        )

    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    ("name", "value"),
    [("max_passages", 65), ("max_pair_tokens", 8193), ("max_request_tokens", 16_385)],
)
def test_limits_above_the_server_s_fail_at_start_up(name: str, value: int) -> None:
    with pytest.raises(ValidationError):
        LocalRerankerSettings.model_validate({"url": URL, name: value})
