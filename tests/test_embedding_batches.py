"""Tests for planning embedding batches and running them with backpressure."""

import asyncio
import hashlib
from collections.abc import Callable, Sequence
from dataclasses import replace
from itertools import pairwise
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fakes import FakeEmbeddingModel
from knowledge_service.embedding_batches import (
    BatchOutcome,
    BatchStatus,
    embed_in_batches,
    plan_batches,
    vectors_by_position,
)
from knowledge_service.embeddings import (
    EmbeddingBatch,
    EmbeddingInputRejected,
    EmbeddingLimits,
    EmbeddingModelIdentity,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
    EmbeddingTimeout,
    RejectionReason,
)

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000031")
LIMITS = EmbeddingLimits(max_inputs=3, max_input_tokens=4, max_batch_tokens=6)
SEEDED_TEXT = "the salary of Olena is high"


class RecordingModel:
    """A fake model that counts calls in flight and fails chosen batches.

    Each call yields to the event loop a number of times that depends on its
    texts, so concurrent calls overlap and finish in a different order from
    the one they started in. A batch containing a text in `failures` raises
    that error.
    """

    def __init__(
        self,
        *,
        limits: EmbeddingLimits = LIMITS,
        failures: dict[str, Exception] | None = None,
        block: bool = False,
        tamper: Callable[[EmbeddingBatch], EmbeddingBatch] | None = None,
    ) -> None:
        self._fake = FakeEmbeddingModel(limits=limits)
        self._failures = failures or {}
        self._block = block
        self._tamper = tamper
        self.active = 0
        self.most_active = 0
        self.two_in_flight = asyncio.Event()
        self.sent: list[tuple[str, ...]] = []

    @property
    def identity(self) -> EmbeddingModelIdentity:
        return self._fake.identity

    @property
    def limits(self) -> EmbeddingLimits:
        return self._fake.limits

    def count_tokens(self, text: str) -> int:
        return self._fake.count_tokens(text)

    async def embed(
        self, texts: Sequence[str], *, request_id: UUID, timeout_seconds: float
    ) -> EmbeddingBatch:
        self.active += 1
        self.most_active = max(self.most_active, self.active)
        if self.active >= 2:
            self.two_in_flight.set()
        self.sent.append(tuple(texts))
        try:
            if self._block:
                await asyncio.Event().wait()
            for _ in range(_yields(texts)):
                await asyncio.sleep(0)
            for text in texts:
                if text in self._failures:
                    raise self._failures[text]
            batch = await self._fake.embed(
                texts, request_id=request_id, timeout_seconds=timeout_seconds
            )
            if self._tamper is not None and "tampered" in texts:
                return self._tamper(batch)
            return batch
        finally:
            self.active -= 1


def _yields(texts: Sequence[str]) -> int:
    """A stable, uneven number of event-loop turns for a batch, from 1 to 8.

    It comes from a hash of the texts, so later batches often overtake earlier
    ones, as real requests do; a bug that matched results by finishing order
    would then attach vectors to the wrong texts.
    """
    return 1 + hashlib.sha256("\n".join(texts).encode()).digest()[0] % 8


async def run(
    model: RecordingModel,
    texts: Sequence[str],
    *,
    concurrency: int = 2,
    completed: Sequence[str] = (),
) -> tuple[BatchOutcome, ...]:
    return await embed_in_batches(
        model,
        texts,
        concurrency=concurrency,
        request_id=REQUEST_ID,
        timeout_seconds=1.0,
        completed=completed,
    )


def statuses(outcomes: Sequence[BatchOutcome]) -> list[BatchStatus]:
    return [outcome.status for outcome in outcomes]


async def vector_alone(text: str) -> tuple[float, ...]:
    batch = await FakeEmbeddingModel(limits=LIMITS).embed(
        [text], request_id=REQUEST_ID, timeout_seconds=1.0
    )
    return batch.vectors[0]


def text_of(index: int, words: int) -> str:
    """A text of `words` tokens that names its position."""
    return " ".join([f"t{index}"] + ["w"] * (words - 1))


WORD_COUNTS = st.lists(st.integers(min_value=1, max_value=4), max_size=30)


@given(WORD_COUNTS)
def test_a_plan_keeps_every_text_once_in_order_within_the_limits(
    counts: list[int],
) -> None:
    texts = [text_of(index, words) for index, words in enumerate(counts)]
    model = RecordingModel()

    plan = plan_batches(texts, model)

    assert [text for batch in plan for text in batch.texts] == texts
    assert [batch.start for batch in plan] == [
        sum(len(earlier.texts) for earlier in plan[:i]) for i in range(len(plan))
    ]
    for batch in plan:
        assert 1 <= len(batch.texts) <= LIMITS.max_inputs
        assert batch.tokens == sum(model.count_tokens(t) for t in batch.texts)
        assert batch.tokens <= LIMITS.max_batch_tokens
    # Fewest batches: each one is cut only when the next text cannot join it.
    for batch, following in pairwise(plan):
        next_tokens = model.count_tokens(following.texts[0])
        assert (
            len(batch.texts) == LIMITS.max_inputs
            or batch.tokens + next_tokens > LIMITS.max_batch_tokens
        )
    assert plan_batches(texts, model) == plan
    assert len({batch.key for batch in plan}) == len(plan)


@settings(max_examples=60, deadline=None)
@given(
    WORD_COUNTS,
    st.integers(min_value=1, max_value=4),
    st.sets(st.integers(min_value=0, max_value=29)),
)
def test_no_text_is_lost_or_reordered_and_concurrency_stays_bounded(
    counts: list[int], concurrency: int, failing: set[int]
) -> None:
    texts = [text_of(index, words) for index, words in enumerate(counts)]
    model = RecordingModel(
        failures={texts[i]: EmbeddingTimeout() for i in failing if i < len(texts)}
    )

    outcomes = asyncio.run(run(model, texts, concurrency=concurrency))

    plan = plan_batches(texts, model)
    assert [outcome.batch for outcome in outcomes] == list(plan)
    assert model.most_active <= concurrency
    vectors = vectors_by_position(outcomes)
    for outcome in outcomes:
        positions = range(
            outcome.batch.start, outcome.batch.start + len(outcome.batch.texts)
        )
        if outcome.status is BatchStatus.SUCCEEDED:
            for position in positions:
                expected = asyncio.run(vector_alone(texts[position]))
                assert vectors[position] == expected
        else:
            assert outcome.status is BatchStatus.FAILED
            assert not any(position in vectors for position in positions)


def test_batches_are_cut_by_count_and_by_tokens() -> None:
    texts = ["a", "b", "c", "d", "e e e e", "f f f", "g"]

    plan = plan_batches(texts, RecordingModel())

    assert [batch.texts for batch in plan] == [
        ("a", "b", "c"),
        ("d", "e e e e"),
        ("f f f", "g"),
    ]
    assert [(batch.start, batch.tokens) for batch in plan] == [(0, 3), (3, 5), (5, 4)]


def test_a_text_no_batch_can_hold_is_refused_by_its_position() -> None:
    texts = ["a", "b", "c", "d", "w w w w w"]

    with pytest.raises(EmbeddingInputRejected) as rejected:
        plan_batches(texts, RecordingModel())

    assert (rejected.value.reason, rejected.value.index) == (
        RejectionReason.INPUT_TOO_LONG,
        4,
    )


async def test_nothing_is_sent_when_the_plan_is_refused() -> None:
    model = RecordingModel()

    with pytest.raises(EmbeddingInputRejected):
        await run(model, ["a", "", "c"])

    assert model.sent == []


async def test_a_rate_limit_stops_new_batches_and_defers_the_rest() -> None:
    texts = ["a", "b", "c", "slow", "e", "f", "g", "h", "i", "j"]
    model = RecordingModel(failures={"slow": EmbeddingRateLimited()})

    outcomes = await run(model, texts, concurrency=1)

    assert statuses(outcomes) == [
        BatchStatus.SUCCEEDED,
        BatchStatus.FAILED,
        BatchStatus.DEFERRED,
        BatchStatus.DEFERRED,
    ]
    assert outcomes[1].error == "EmbeddingRateLimited"
    assert len(model.sent) == 2


async def test_a_refused_request_also_stops_new_batches() -> None:
    model = RecordingModel(failures={"a": EmbeddingRequestRefused("bad key")})

    outcomes = await run(model, ["a", "b", "c", "d"], concurrency=1)

    assert statuses(outcomes) == [BatchStatus.FAILED, BatchStatus.DEFERRED]


async def test_a_timeout_fails_only_its_own_batch() -> None:
    texts = ["a", "b", "c", "slow", "e", "f", "g"]
    model = RecordingModel(failures={"slow": EmbeddingTimeout()})

    outcomes = await run(model, texts)

    assert statuses(outcomes) == [
        BatchStatus.SUCCEEDED,
        BatchStatus.FAILED,
        BatchStatus.SUCCEEDED,
    ]
    assert sorted(vectors_by_position(outcomes)) == [0, 1, 2, 6]


async def test_a_second_run_resumes_without_resending_finished_batches() -> None:
    texts = ["a", "b", "c", "slow", "e", "f", "g", "h", "i", "j"]
    first = await run(
        RecordingModel(failures={"slow": EmbeddingRateLimited()}), texts, concurrency=1
    )
    finished = [o.batch.key for o in first if o.status is BatchStatus.SUCCEEDED]
    retry = RecordingModel()

    second = await run(retry, texts, completed=finished)

    assert statuses(second) == [
        BatchStatus.SKIPPED,
        BatchStatus.SUCCEEDED,
        BatchStatus.SUCCEEDED,
        BatchStatus.SUCCEEDED,
    ]
    assert sorted(retry.sent) == [("g", "h", "i"), ("j",), ("slow", "e", "f")]
    merged = vectors_by_position(first) | vectors_by_position(second)
    assert sorted(merged) == list(range(len(texts)))


def test_a_key_names_the_model_the_position_and_the_texts() -> None:
    model = RecordingModel()
    other = FakeEmbeddingModel(limits=LIMITS, dimensions=16)

    same = plan_batches(["a", "b", "c", "a", "b", "c"], model)

    assert same[0].texts == same[1].texts
    assert same[0].key != same[1].key
    assert plan_batches(["a"], model)[0].key != plan_batches(["a"], other)[0].key
    assert plan_batches(["a"], model)[0].key != plan_batches(["b"], model)[0].key


async def test_a_failure_is_recorded_by_name_never_with_text() -> None:
    wide = EmbeddingLimits(max_inputs=3, max_input_tokens=8, max_batch_tokens=16)
    model = RecordingModel(limits=wide, failures={SEEDED_TEXT: EmbeddingTimeout()})

    (outcome,) = await run(model, [SEEDED_TEXT, "b"])

    assert outcome.error == "EmbeddingTimeout"
    assert "Olena" not in repr(outcome)


@pytest.mark.parametrize("concurrency", [0, -1, True])
async def test_impossible_concurrency_is_refused(concurrency: int) -> None:
    with pytest.raises(ValueError, match="concurrency"):
        await run(RecordingModel(), ["a"], concurrency=concurrency)


async def test_a_deadline_that_is_not_a_number_is_refused_before_sending() -> None:
    model = RecordingModel()

    with pytest.raises(ValueError, match="timeout_seconds"):
        await embed_in_batches(
            model,
            ["a"],
            concurrency=1,
            request_id=REQUEST_ID,
            timeout_seconds=float("nan"),
        )

    assert model.sent == []


async def test_cancellation_stops_every_batch_in_flight() -> None:
    model = RecordingModel(block=True)
    task = asyncio.create_task(run(model, ["a", "b", "c", "d", "e", "f", "g"]))
    await model.two_in_flight.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert model.active == 0


async def test_an_empty_input_sends_nothing() -> None:
    model = RecordingModel()

    assert await run(model, []) == ()
    assert model.sent == []


def test_one_string_is_not_a_list_of_texts() -> None:
    with pytest.raises(TypeError):
        plan_batches("abc", RecordingModel())


async def test_an_unexpected_error_fails_its_batch_and_keeps_the_finished_ones() -> (
    None
):
    texts = ["a", "b", "c", "boom", "e", "f", "g", "h", "i", "j"]
    model = RecordingModel(failures={"boom": ValueError("a bug in the model")})

    outcomes = await run(model, texts, concurrency=1)

    assert statuses(outcomes) == [
        BatchStatus.SUCCEEDED,
        BatchStatus.FAILED,
        BatchStatus.DEFERRED,
        BatchStatus.DEFERRED,
    ]
    assert outcomes[1].error == "ValueError"
    assert sorted(vectors_by_position(outcomes)) == [0, 1, 2]


def extra_vector(batch: EmbeddingBatch) -> EmbeddingBatch:
    return replace(batch, vectors=(*batch.vectors, batch.vectors[0]))


def missing_vector(batch: EmbeddingBatch) -> EmbeddingBatch:
    return replace(batch, vectors=batch.vectors[:-1])


def other_model(batch: EmbeddingBatch) -> EmbeddingBatch:
    return replace(batch, identity=replace(batch.identity, model="another-model"))


@pytest.mark.parametrize(
    "tamper",
    [extra_vector, missing_vector, other_model],
    ids=["extra", "missing", "other-model"],
)
async def test_vectors_that_do_not_match_their_batch_are_refused(
    tamper: Callable[[EmbeddingBatch], EmbeddingBatch],
) -> None:
    texts = ["a", "b", "c", "tampered", "e", "f", "g"]
    model = RecordingModel(tamper=tamper)

    outcomes = await run(model, texts)

    assert statuses(outcomes) == [
        BatchStatus.SUCCEEDED,
        BatchStatus.FAILED,
        BatchStatus.SUCCEEDED,
    ]
    assert outcomes[1].error == "EmbeddingMalformedResponse"
    assert sorted(vectors_by_position(outcomes)) == [0, 1, 2, 6]


async def test_one_key_is_not_a_collection_of_keys() -> None:
    model = RecordingModel()
    key = plan_batches(["a"], model)[0].key

    with pytest.raises(TypeError, match="completed"):
        await run(model, ["a"], completed=key)


async def test_logging_a_batch_or_its_outcome_never_shows_its_text() -> None:
    wide = EmbeddingLimits(max_inputs=3, max_input_tokens=8, max_batch_tokens=16)
    model = RecordingModel(limits=wide)

    (outcome,) = await run(model, [SEEDED_TEXT])

    assert "Olena" not in repr(outcome.batch)
    assert "Olena" not in repr(outcome)
    assert "Olena" not in str(outcome)
