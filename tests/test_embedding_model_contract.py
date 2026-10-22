"""Behavioural contract tests shared by every EmbeddingModel adapter."""

import asyncio
import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import pytest
from openai import AsyncOpenAI

from fakes import FakeEmbeddingModel
from knowledge_service.embeddings import (
    EmbeddingInputRejected,
    EmbeddingMalformedResponse,
    EmbeddingModel,
    EmbeddingRateLimited,
    RejectionReason,
)
from knowledge_service.openai_embeddings import (
    OpenAIEmbeddingModel,
    OpenAIEmbeddingSettings,
)
from openai_stubs import StubEmbeddings, openai_embedding_model, status_error

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000022")


@dataclass(frozen=True)
class Adapter:
    """One adapter in the states the contract needs, and how many calls it sent."""

    model: EmbeddingModel
    sent: Callable[[], int]


@dataclass(frozen=True)
class AdapterCase:
    name: str
    working: Callable[[], Adapter]
    rate_limited: Callable[[], Adapter]
    wrong_dimensions: Callable[[], Adapter]
    blocked: Callable[[], Adapter]


def fake(model: FakeEmbeddingModel) -> Adapter:
    return Adapter(model=model, sent=lambda: len(model.batches))


def openai_adapter(embeddings: StubEmbeddings) -> Adapter:
    model, _ = openai_embedding_model(embeddings)
    return Adapter(model=model, sent=lambda: len(embeddings.calls))


CASES = (
    AdapterCase(
        name="fake",
        working=lambda: fake(FakeEmbeddingModel()),
        rate_limited=lambda: fake(FakeEmbeddingModel(error=EmbeddingRateLimited())),
        wrong_dimensions=lambda: fake(FakeEmbeddingModel(returned_dimensions=3)),
        blocked=lambda: fake(FakeEmbeddingModel(wait_for=asyncio.Event())),
    ),
    AdapterCase(
        name="openai",
        working=lambda: openai_adapter(StubEmbeddings()),
        rate_limited=lambda: openai_adapter(StubEmbeddings(error=status_error(429))),
        wrong_dimensions=lambda: openai_adapter(StubEmbeddings(dimensions=3)),
        blocked=lambda: openai_adapter(StubEmbeddings(block=True)),
    ),
)
IDS = [case.name for case in CASES]


async def embed(
    model: EmbeddingModel, texts: list[str]
) -> tuple[tuple[float, ...], ...]:
    batch = await model.embed(texts, request_id=REQUEST_ID, timeout_seconds=2.0)
    assert batch.identity == model.identity
    assert all(len(vector) == model.identity.dimensions for vector in batch.vectors)
    return batch.vectors


def over_the_input_limit(model: EmbeddingModel) -> str:
    text = "word"
    while model.count_tokens(text) <= model.limits.max_input_tokens:
        text = f"{text} {text}"
    return text


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_each_vector_belongs_to_its_input(case: AdapterCase) -> None:
    model = case.working().model
    texts = ["vacation policy", "laptop setup", "expense report"]

    batch = await embed(model, texts)
    alone = [(await embed(model, [text]))[0] for text in texts]

    assert list(batch) == alone
    assert len(set(batch)) == 3


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_an_input_over_the_token_limit_is_refused_before_sending(
    case: AdapterCase,
) -> None:
    adapter = case.working()

    with pytest.raises(EmbeddingInputRejected) as rejected:
        await embed(adapter.model, ["fine", over_the_input_limit(adapter.model)])

    assert (rejected.value.reason, rejected.value.index) == (
        RejectionReason.INPUT_TOO_LONG,
        1,
    )
    assert adapter.sent() == 0


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_a_deadline_that_is_not_a_number_is_refused_before_sending(
    case: AdapterCase,
) -> None:
    adapter = case.working()

    with pytest.raises(ValueError, match="timeout_seconds"):
        await adapter.model.embed(
            ["vacation policy"], request_id=REQUEST_ID, timeout_seconds=math.nan
        )

    assert adapter.sent() == 0


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_a_rate_limit_is_a_typed_error(case: AdapterCase) -> None:
    with pytest.raises(EmbeddingRateLimited):
        await embed(case.rate_limited().model, ["vacation policy"])


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_vectors_of_the_wrong_dimensions_are_refused(case: AdapterCase) -> None:
    with pytest.raises(EmbeddingMalformedResponse):
        await embed(case.wrong_dimensions().model, ["vacation policy"])


@pytest.mark.parametrize("case", CASES, ids=IDS)
async def test_cancellation_reaches_the_caller(case: AdapterCase) -> None:
    task = asyncio.create_task(embed(case.blocked().model, ["vacation policy"]))
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.live
async def test_openai_live_embeddings_keep_the_contract() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key is None:
        pytest.skip("OPENAI_API_KEY is required for the opt-in live test")

    client = AsyncOpenAI(api_key=api_key)
    try:
        model = OpenAIEmbeddingModel(
            client, OpenAIEmbeddingSettings(embedding_dimensions=256)
        )
        texts = ["How early must I request vacation?", "Як замовити ноутбук?"]
        batch = await model.embed(texts, request_id=REQUEST_ID, timeout_seconds=30.0)
    finally:
        await client.close()

    assert len(batch.vectors) == 2
    assert all(len(vector) == 256 for vector in batch.vectors)
    assert batch.usage.input_tokens > 0
