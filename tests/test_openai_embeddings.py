"""Tests for the OpenAI EmbeddingModel adapter, against a stubbed client."""

import json
import logging
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx2
import openai
import pytest
from openai.types import CreateEmbeddingResponse
from pydantic import ValidationError

from knowledge_service.embeddings.openai_adapter import OpenAIEmbeddingSettings
from knowledge_service.embeddings.port import (
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingMalformedResponse,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
    EmbeddingTimeout,
    EmbeddingUnavailable,
)
from knowledge_service.errors import RetryOwner, classify_failure, policy_for
from openai_stubs import (
    URL,
    StubEmbeddings,
    openai_embedding_model,
    response_for,
    status_error,
)

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000023")
SEEDED_TEXT = "the salary of Olena is 9000"


def everything_shown(error: BaseException) -> str:
    """Everything a log line could print about an error and its chain."""
    parts = [str(error), repr(error), *map(str, error.args)]
    parts += getattr(error, "__notes__", [])
    for chained in (error.__cause__, error.__context__):
        if chained is not None:
            parts.append(everything_shown(chained))
    return " ".join(parts)


async def test_a_request_carries_the_configured_model_dimensions_and_deadline() -> None:
    embeddings = StubEmbeddings()
    settings = OpenAIEmbeddingSettings(
        embedding_model="text-embedding-3-large",
        embedding_dimensions=256,
        embedding_timeout_seconds=5.0,
        embedding_price_id="openai-embedding-large",
    )
    model, client = openai_embedding_model(embeddings, settings)

    batch = await model.embed(
        ["vacation policy", "laptop setup"], request_id=REQUEST_ID, timeout_seconds=30.0
    )

    (call,) = embeddings.calls
    assert call["model"] == "text-embedding-3-large"
    assert call["dimensions"] == 256
    assert call["encoding_format"] == "float"
    assert call["input"] == ["vacation policy", "laptop setup"]
    assert call["timeout"] == 5.0
    assert call["extra_headers"] == {"X-Request-ID": str(REQUEST_ID)}
    assert client.options == {"max_retries": 0}
    assert (
        model.identity.provider,
        model.identity.model,
        model.identity.dimensions,
    ) == (
        "openai",
        "text-embedding-3-large",
        256,
    )
    assert batch.usage.input_tokens == 6
    assert batch.usage.price_id == "openai-embedding-large"


async def test_the_shorter_of_the_two_deadlines_is_used() -> None:
    embeddings = StubEmbeddings()
    model, _ = openai_embedding_model(embeddings)

    await model.embed(["vacation policy"], request_id=REQUEST_ID, timeout_seconds=0.5)

    assert embeddings.calls[0]["timeout"] == 0.5


async def test_a_provider_slower_than_the_deadline_times_out() -> None:
    model, _ = openai_embedding_model(StubEmbeddings(delay=1.0))

    with pytest.raises(EmbeddingTimeout):
        await model.embed(
            ["vacation policy"], request_id=REQUEST_ID, timeout_seconds=0.05
        )


def connection_error() -> openai.APIConnectionError:
    return openai.APIConnectionError(request=httpx2.Request("POST", URL))


def timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(httpx2.Request("POST", URL))


def unreadable_response() -> openai.APIResponseValidationError:
    request = httpx2.Request("POST", URL)
    return openai.APIResponseValidationError(
        httpx2.Response(200, request=request), body=None
    )


@pytest.mark.parametrize(
    ("make_error", "expected", "retry_owner"),
    [
        (timeout_error, EmbeddingTimeout, RetryOwner.SERVICE),
        (connection_error, EmbeddingUnavailable, RetryOwner.SERVICE),
        (lambda: status_error(500), EmbeddingUnavailable, RetryOwner.SERVICE),
        (lambda: status_error(503), EmbeddingUnavailable, RetryOwner.SERVICE),
        (lambda: status_error(408), EmbeddingTimeout, RetryOwner.SERVICE),
        (lambda: status_error(409), EmbeddingUnavailable, RetryOwner.SERVICE),
        (lambda: status_error(429), EmbeddingRateLimited, RetryOwner.CALLER),
        (lambda: status_error(400), EmbeddingRequestRefused, RetryOwner.NONE),
        (lambda: status_error(401), EmbeddingRequestRefused, RetryOwner.NONE),
        (lambda: status_error(403), EmbeddingRequestRefused, RetryOwner.NONE),
        (lambda: status_error(404), EmbeddingRequestRefused, RetryOwner.NONE),
        (lambda: status_error(413), EmbeddingRequestRefused, RetryOwner.NONE),
        (lambda: status_error(422), EmbeddingRequestRefused, RetryOwner.NONE),
        (unreadable_response, EmbeddingMalformedResponse, RetryOwner.SERVICE),
    ],
    ids=[
        "timeout",
        "connection",
        "500",
        "503",
        "408",
        "409",
        "429",
        "400",
        "401",
        "403",
        "404",
        "413",
        "422",
        "unreadable",
    ],
)
async def test_each_provider_failure_becomes_a_port_error_with_a_retry_owner(
    make_error: Callable[[], BaseException],
    expected: type[EmbeddingError],
    retry_owner: RetryOwner,
) -> None:
    error = make_error()
    model, _ = openai_embedding_model(StubEmbeddings(error=error))

    with pytest.raises(expected) as raised:
        await model.embed(
            ["vacation policy"], request_id=REQUEST_ID, timeout_seconds=2.0
        )

    assert policy_for(classify_failure(raised.value)).retry_owner is retry_owner


async def test_a_provider_error_never_carries_the_request_text() -> None:
    # The SDK error keeps the HTTP request, whose body holds every input.
    error = status_error(400, body_text=SEEDED_TEXT)
    model, _ = openai_embedding_model(StubEmbeddings(error=error))

    with pytest.raises(EmbeddingRequestRefused) as raised:
        await model.embed([SEEDED_TEXT], request_id=REQUEST_ID, timeout_seconds=2.0)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert "Olena" not in everything_shown(raised.value)


def items(*indexes: int) -> CreateEmbeddingResponse:
    response = response_for(["a", "b", "c"], 1536, reverse=False)
    data = [
        item.model_copy(update={"index": i})
        for item, i in zip(response.data, indexes, strict=False)
    ]
    return response.model_copy(update={"data": data})


@pytest.mark.parametrize(
    "indexes",
    [(0, 1), (0, 1, 1), (0, 1, 3), (-1, 0, 1)],
    ids=["missing", "duplicate", "out-of-range", "negative"],
)
async def test_vectors_that_cannot_be_matched_to_inputs_are_refused(
    indexes: tuple[int, ...],
) -> None:
    model, _ = openai_embedding_model(StubEmbeddings(response=items(*indexes)))

    with pytest.raises(EmbeddingMalformedResponse):
        await model.embed(["a", "b", "c"], request_id=REQUEST_ID, timeout_seconds=2.0)


def usage(prompt_tokens: object) -> SimpleNamespace:
    return SimpleNamespace(prompt_tokens=prompt_tokens)


VALID = response_for(["a"], 1536)


@pytest.mark.parametrize(
    "response",
    [
        "not json at all",
        [1, 2],
        SimpleNamespace(),
        SimpleNamespace(data=None, usage=usage(1)),
        SimpleNamespace(data=VALID.data),
        SimpleNamespace(data=VALID.data, usage=None),
        SimpleNamespace(data=VALID.data, usage=usage(-1)),
        SimpleNamespace(data=VALID.data, usage=usage("3")),
        SimpleNamespace(data=VALID.data, usage=usage(True)),
    ],
    ids=[
        "text",
        "list",
        "empty",
        "no-data",
        "no-usage",
        "null-usage",
        "negative-tokens",
        "string-tokens",
        "boolean-tokens",
    ],
)
async def test_a_successful_status_with_a_malformed_body_is_refused(
    response: object,
) -> None:
    # The SDK does not validate a 200 body, so the adapter must.
    model, _ = openai_embedding_model(StubEmbeddings(response=response))

    with pytest.raises(EmbeddingMalformedResponse):
        await model.embed(["a"], request_id=REQUEST_ID, timeout_seconds=2.0)


async def test_a_body_that_is_not_json_is_refused_without_chaining() -> None:
    error = json.JSONDecodeError("Expecting value", "not json", 0)
    model, _ = openai_embedding_model(StubEmbeddings(error=error))

    with pytest.raises(EmbeddingMalformedResponse) as raised:
        await model.embed(["a"], request_id=REQUEST_ID, timeout_seconds=2.0)

    assert raised.value.__context__ is None


async def test_an_empty_batch_sends_nothing() -> None:
    embeddings = StubEmbeddings()
    model, _ = openai_embedding_model(embeddings)

    batch = await model.embed([], request_id=REQUEST_ID, timeout_seconds=2.0)

    assert batch.vectors == ()
    assert embeddings.calls == []


def test_token_counts_are_utf8_bytes_an_upper_bound() -> None:
    model, _ = openai_embedding_model(StubEmbeddings())

    assert model.count_tokens("vacation") == 8
    assert model.count_tokens("відпустка") == 18
    assert model.limits.max_inputs == 2048
    assert model.limits.max_input_tokens == 8192
    assert model.limits.max_batch_tokens == 300_000


async def test_each_call_logs_counts_and_outcome_but_never_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model, _ = openai_embedding_model(StubEmbeddings())
    failing, _ = openai_embedding_model(StubEmbeddings(error=status_error(429)))

    await model.embed([SEEDED_TEXT], request_id=REQUEST_ID, timeout_seconds=2.0)
    with pytest.raises(EmbeddingRateLimited):
        await failing.embed([SEEDED_TEXT], request_id=REQUEST_ID, timeout_seconds=2.0)
    with pytest.raises(EmbeddingInputRejected):
        await model.embed(
            [SEEDED_TEXT, " "], request_id=REQUEST_ID, timeout_seconds=2.0
        )

    ours = [
        record
        for record in caplog.records
        if record.name == "knowledge_service.embeddings.openai_adapter"
    ]
    # `extra` fields become attributes, which a type checker cannot see.
    fields: list[dict[str, Any]] = [record.__dict__["embedding"] for record in ours]
    assert [record.levelno for record in ours] == [
        logging.INFO,
        logging.WARNING,
        logging.WARNING,
    ]
    assert [f["outcome"] for f in fields] == [
        "succeeded",
        "EmbeddingRateLimited",
        "EmbeddingInputRejected",
    ]
    assert (fields[0]["inputs"], fields[0]["input_tokens"]) == (1, 3)
    assert all("Olena" not in str(vars(r)) for r in caplog.records)


@pytest.mark.parametrize(
    "field",
    [
        {"embedding_dimensions": 0},
        {"embedding_timeout_seconds": 0},
        {"embedding_max_inputs": 0},
        {"embedding_model": ""},
    ],
)
def test_impossible_settings_are_refused(field: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        OpenAIEmbeddingSettings.model_validate(field)
