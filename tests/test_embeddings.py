"""Tests for the EmbeddingModel port, through its deterministic fake."""

import asyncio
import math
from collections.abc import Callable, Sequence
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from fakes import FakeEmbeddingModel
from knowledge_service.embeddings import (
    EmbeddingBatch,
    EmbeddingInputRejected,
    EmbeddingLimits,
    EmbeddingMalformedResponse,
    EmbeddingModel,
    EmbeddingModelIdentity,
    EmbeddingRateLimited,
    EmbeddingTimeout,
    EmbeddingUsage,
    InvalidEmbedding,
    RejectionReason,
    Vector,
    check_inputs,
    checked_batch,
)

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000021")
LIMITS = EmbeddingLimits(max_inputs=3, max_input_tokens=4, max_batch_tokens=6)
IDENTITY = EmbeddingModelIdentity(provider="fake", model="fake-embedding", dimensions=4)
USAGE = EmbeddingUsage(input_tokens=1, latency_ms=0)
SEEDED_TEXT = "the salary of Olena is 9000"


async def embed(model: EmbeddingModel, texts: Sequence[str]) -> tuple[Vector, ...]:
    batch = await model.embed(texts, request_id=REQUEST_ID, timeout_seconds=1.0)
    return batch.vectors


def everything_shown(error: BaseException) -> str:
    """Everything a log line could print about an error and its chain."""
    parts = [str(error), repr(error), *map(str, error.args)]
    parts += getattr(error, "__notes__", [])
    for chained in (error.__cause__, error.__context__):
        if chained is not None:
            parts.append(everything_shown(chained))
    return " ".join(parts)


async def test_the_same_text_gets_the_same_vector_in_any_batch_and_order() -> None:
    model = FakeEmbeddingModel()

    first = await embed(model, ["vacation policy", "laptop setup"])
    reversed_order = await embed(model, ["laptop setup", "vacation policy"])
    alone = await embed(FakeEmbeddingModel(), ["vacation policy"])

    assert first == (reversed_order[1], reversed_order[0])
    assert alone[0] == first[0]
    assert first[0] != first[1]


async def test_each_vector_has_the_model_dimensions_and_unit_length() -> None:
    model = FakeEmbeddingModel(dimensions=64)

    batch = await model.embed(
        ["one two", "three"], request_id=REQUEST_ID, timeout_seconds=1.0
    )

    assert batch.identity == model.identity
    assert [len(vector) for vector in batch.vectors] == [64, 64]
    assert all(math.isclose(math.hypot(*vector), 1.0) for vector in batch.vectors)
    assert batch.usage.input_tokens == 3


async def test_a_full_batch_is_accepted_and_one_more_input_is_refused() -> None:
    model = FakeEmbeddingModel(limits=LIMITS)

    assert len(await embed(model, ["a", "b", "c"])) == 3
    with pytest.raises(EmbeddingInputRejected) as rejected:
        await embed(model, ["a", "b", "c", "d"])

    assert (rejected.value.reason, rejected.value.index) == (
        RejectionReason.TOO_MANY_INPUTS,
        None,
    )
    assert model.batches == [("a", "b", "c")]


async def test_an_input_at_the_token_limit_passes_and_one_over_is_refused() -> None:
    model = FakeEmbeddingModel(limits=LIMITS)

    assert len(await embed(model, ["w w w w"])) == 1
    with pytest.raises(EmbeddingInputRejected) as rejected:
        await embed(model, ["short", "w w w w w"])

    assert (rejected.value.reason, rejected.value.index) == (
        RejectionReason.INPUT_TOO_LONG,
        1,
    )
    assert model.batches == [("w w w w",)]


async def test_a_batch_over_its_token_budget_is_refused_where_it_overflows() -> None:
    model = FakeEmbeddingModel(limits=LIMITS)

    assert len(await embed(model, ["w w w", "w w w"])) == 2
    with pytest.raises(EmbeddingInputRejected) as rejected:
        await embed(model, ["w w w", "w w", "w w"])

    assert (rejected.value.reason, rejected.value.index) == (
        RejectionReason.BATCH_TOO_LONG,
        2,
    )
    assert model.batches == [("w w w", "w w w")]


@pytest.mark.parametrize(
    "text", ["", "   ", "\n\t"], ids=["empty", "spaces", "newline-tab"]
)
async def test_an_empty_text_is_refused_by_its_index(text: str) -> None:
    model = FakeEmbeddingModel()

    with pytest.raises(EmbeddingInputRejected) as rejected:
        await embed(model, ["fine", text])

    assert (rejected.value.reason, rejected.value.index) == (
        RejectionReason.EMPTY_TEXT,
        1,
    )
    assert model.batches == []


async def test_an_empty_batch_returns_no_vectors_without_calling_the_model() -> None:
    model = FakeEmbeddingModel()

    batch = await model.embed([], request_id=REQUEST_ID, timeout_seconds=1.0)

    assert batch.vectors == ()
    assert batch.usage.input_tokens == 0
    assert model.batches == []


async def test_a_rejection_never_carries_the_input_text() -> None:
    model = FakeEmbeddingModel(limits=LIMITS)

    with pytest.raises(EmbeddingInputRejected) as rejected:
        await embed(model, [SEEDED_TEXT])

    assert "Olena" not in everything_shown(rejected.value)


async def test_a_provider_failure_reaches_the_caller_as_a_typed_error() -> None:
    model = FakeEmbeddingModel(error=EmbeddingRateLimited())

    with pytest.raises(EmbeddingRateLimited):
        await embed(model, ["vacation policy"])

    assert model.batches == [("vacation policy",)]


async def test_cancellation_reaches_the_caller() -> None:
    model = FakeEmbeddingModel(wait_for=asyncio.Event())
    task = asyncio.create_task(embed(model, ["vacation policy"]))
    await asyncio.sleep(0)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


GOOD = [0.1, 0.2, 0.3, 0.4]


@pytest.mark.parametrize(
    "vectors",
    [
        [GOOD],
        [GOOD, [0.1, 0.2, 0.3]],
        [GOOD, [0.1, 0.2, 0.3, float("nan")]],
        [GOOD, [0.1, 0.2, 0.3, float("inf")]],
        [GOOD, [0.1, 0.2, 0.3, "x"]],
        [GOOD, ["1", "2", "3", "4"]],
        [GOOD, "1234"],
        [GOOD, b"\x01\x02\x03\x04"],
        [GOOD, [True, False, True, False]],
        [GOOD, [10**400, 1, 1, 1]],
        [GOOD, [0, 0.0, 0, 0.0]],
        [GOOD, None],
        None,
        "not vectors",
        {0: GOOD, 1: GOOD},
    ],
    ids=[
        "too-few",
        "wrong-dimensions",
        "nan",
        "infinite",
        "not-numeric",
        "numeric-strings",
        "string",
        "bytes",
        "booleans",
        "too-large",
        "all-zeros",
        "no-vector",
        "no-response",
        "text-response",
        "mapping",
    ],
)
def test_a_malformed_response_is_refused(vectors: object) -> None:
    with pytest.raises(EmbeddingMalformedResponse):
        checked_batch(IDENTITY, vectors, USAGE, expected=2)


ANYTHING = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats()
    | st.text(max_size=4)
    | st.binary(max_size=4),
    lambda children: (
        st.lists(children, max_size=5)
        | st.dictionaries(st.integers(), children, max_size=3)
    ),
    max_leaves=20,
)


@given(ANYTHING)
def test_any_response_is_a_valid_batch_or_a_typed_refusal(vectors: object) -> None:
    try:
        batch = checked_batch(IDENTITY, vectors, USAGE, expected=2)
    except EmbeddingMalformedResponse:
        return
    assert len(batch.vectors) == 2
    for vector in batch.vectors:
        assert len(vector) == IDENTITY.dimensions
        assert all(type(value) is float and math.isfinite(value) for value in vector)
        assert any(vector)


def test_a_valid_response_keeps_its_order() -> None:
    batch = checked_batch(IDENTITY, [[1, 0, 0, 0], [0, 1, 0, 0]], USAGE, expected=2)

    assert batch.vectors == ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0))


@pytest.mark.parametrize(
    "make",
    [
        lambda: EmbeddingModelIdentity(provider="", model="m", dimensions=4),
        lambda: EmbeddingModelIdentity(provider="p", model="m", dimensions=0),
        lambda: EmbeddingLimits(max_inputs=0, max_input_tokens=1, max_batch_tokens=1),
        lambda: EmbeddingLimits(max_inputs=1, max_input_tokens=5, max_batch_tokens=4),
        lambda: EmbeddingUsage(input_tokens=-1, latency_ms=0),
        lambda: EmbeddingUsage(input_tokens=0, latency_ms=-1),
    ],
    ids=[
        "no-provider",
        "no-dimensions",
        "no-inputs",
        "input-over-batch",
        "negative-tokens",
        "negative-latency",
    ],
)
def test_impossible_settings_are_refused(make: Callable[[], object]) -> None:
    with pytest.raises(InvalidEmbedding):
        make()


def test_one_string_is_not_a_batch() -> None:
    with pytest.raises(TypeError):
        check_inputs("abc", LIMITS, len)


def broken_tokenizer(text: str) -> int:
    raise ValueError(f"cannot tokenize {text!r}")


def negative_count(text: str) -> int:
    return -1


def zero_count(text: str) -> int:
    return 0


def boolean_count(text: str) -> int:
    return True


@pytest.mark.parametrize(
    "count_tokens",
    [broken_tokenizer, negative_count, zero_count, boolean_count],
    ids=["raises", "negative", "zero", "boolean"],
)
def test_a_text_whose_tokens_cannot_be_counted_is_refused_without_its_text(
    count_tokens: Callable[[str], int],
) -> None:
    with pytest.raises(EmbeddingInputRejected) as rejected:
        check_inputs(["fine", SEEDED_TEXT], LIMITS, count_tokens)

    assert (rejected.value.reason, rejected.value.index) == (
        RejectionReason.UNCOUNTABLE_TEXT,
        0,
    )
    assert rejected.value.__context__ is None
    assert rejected.value.__cause__ is None


async def test_a_text_that_is_not_valid_unicode_is_refused_without_its_text() -> None:
    model = FakeEmbeddingModel()

    with pytest.raises(EmbeddingInputRejected) as rejected:
        await embed(model, ["fine", "Olena \ud800 salary"])

    assert (rejected.value.reason, rejected.value.index) == (
        RejectionReason.UNENCODABLE_TEXT,
        1,
    )
    assert rejected.value.__context__ is None
    assert model.batches == []


async def test_a_model_slower_than_the_deadline_times_out_as_a_typed_error() -> None:
    model = FakeEmbeddingModel(wait_for=asyncio.Event())

    with pytest.raises(EmbeddingTimeout):
        await model.embed(
            ["vacation policy"], request_id=REQUEST_ID, timeout_seconds=0.01
        )


def test_a_batch_cannot_hold_a_vector_of_other_dimensions() -> None:
    with pytest.raises(InvalidEmbedding):
        EmbeddingBatch(identity=IDENTITY, vectors=((1.0,),), usage=USAGE)
