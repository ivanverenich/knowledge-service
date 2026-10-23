# M04-T03 — Budget and batch embedding inputs

Source task: [M04-T03](../curriculum/milestones/M04-dense-retrieval/M04-T03-budget-and-batch-embedding-inputs.md)

## What you will do

M04-T01 and M04-T02 embed **one batch** per call. A document can have hundreds of Chunks, and a sync can have thousands, more than one request may carry. This lesson adds the **embedding batcher**: it splits many texts into batches that fit the model's limits, sends them with only a few requests in flight at once, and reports what happened to each batch, so a run that stopped half-way can continue without paying twice.

| Requirement | How this lesson meets it |
|---|---|
| Deterministic batching by item and token limits | `plan_batches` cuts the texts, in order, into the fewest consecutive batches within `max_inputs` and `max_batch_tokens`. The same texts and model always give the same batches. |
| Bounded concurrency | `embed_in_batches(..., concurrency=n)` never has more than `n` requests in flight. |
| Backpressure | Workers take the next batch only after finishing one, and a rate limit stops new batches from starting. |
| Resumable per-batch outcomes | Every batch ends `SUCCEEDED`, `FAILED`, `DEFERRED`, or `SKIPPED`. Each has a stable key; passing finished keys as `completed` skips them next time. |
| Property tests: no lost or reordered Chunk, concurrency within configuration | Two Hypothesis tests generate random texts, limits, failures, and concurrency, and check both. |

This lesson includes coding: Steps 2 and 3 write code, while Steps 1 and 4 do not. No database, network, or API key is needed.

Terms used in this lesson:

- **Concurrency:** how many requests may be waiting for the provider at the same time. More is faster, until the provider starts refusing.
- **Backpressure:** slowing the sender down to the speed of the receiver, instead of piling up work. Here: never start more than `concurrency` batches ahead of their results, and stop starting new ones when the provider says "too many requests".
- **Resumable:** a run that stopped part-way can be run again and does only the remaining work.
- **Property test:** a test that states a rule ("no text is ever lost") and lets Hypothesis generate hundreds of inputs to try to break it.

## Before you begin

- **Why batches must be cut by tokens as well as count.** OpenAI accepts up to 2048 inputs and 300,000 tokens per request. Long Ukrainian texts can hit the token budget long before the count. The planner uses the model's own `count_tokens` (UTF-8 bytes for OpenAI, from M04-T02), not `Chunk.token_count`, which counts the project's own tokens.
- **Why one failure must not lose the others.** Each request costs money. If one batch fails, the vectors already returned must stay usable, and a retry must send only what is missing.
- **What a key is.** A SHA-256 of the model identity, the batch's start position, and its exact texts. Same model and texts, same key; another model, other texts, or another position, another key. A caller stores a key **only after** storing that batch's vectors (M04-T05).
- **What the adversarial review found.** An independent review ran 700 property examples with random timing, random failures, and random `completed` keys. No text was lost or reordered, and concurrency never went above its setting. It found three breaks and one leak, all fixed in the code below with a test that fails without the fix:
  - A plain bug in the model, such as a `ValueError`, escaped as an `ExceptionGroup` and threw away every finished batch, so a resume paid for them again.
  - A model that returned one vector too many, one too few, or vectors from another model could put a vector on the wrong text's position.
  - `completed="some-key"`, one string, was read as a set of single letters, so nothing was skipped.
  - The automatic `repr` of a batch printed its texts, so logging a batch or an outcome would have logged document content.
- **Files you will touch:**
  - new `src/knowledge_service/embedding_batches.py`
  - new `tests/test_embedding_batches.py`
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and the expected failing check

**Invariant:** for any texts, the plan holds every text exactly once, in order, in the fewest batches that fit the model's limits, and the same input always gives the same plan and keys. A run never has more than `concurrency` requests in flight, ends every batch with exactly one outcome in plan order, attaches each vector to the position of its own text, and never sends a batch whose key is in `completed`. Outcomes carry error class names, never text.

**Action:** create `docs/evidence/M04-T03-embedding-batches.md`:

```markdown
# M04-T03 — Embedding batcher evidence

Completed: pending

## Invariant

Pending.

## Expected failing check

Before writing code, say which test fails first, and how, once the tests
exist but `embedding_batches.py` does not. Then, with limits of 3 inputs,
4 tokens per input, and 6 tokens per batch (one token per word), predict:
the batches for `a b c d "e e e e" "f f f" g`; what happens to a text of 5
words; the outcomes at concurrency 1 when the second batch is rate limited,
and when it times out; what a second run sends when it is given the keys of
the first run's succeeded batches.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and predictions.

### Step 2 — Coding step: the batcher

New module `src/knowledge_service/embedding_batches.py`.

| Name | What it does |
|---|---|
| `PlannedBatch` | `key`, `start` (position of its first text), `texts`, `tokens`. `texts` is kept out of `repr`, so logging a batch never shows document text. |
| `BatchStatus`, `BatchOutcome` | How a batch ended, its `EmbeddingBatch` if it succeeded, and the error's class name if it failed. |
| `plan_batches(texts, model)` | Counts each text once (refusing an empty, unencodable, or over-long text by its position in the whole list), then fills each batch greedily until the next text would break `max_inputs` or `max_batch_tokens`. |
| `embed_in_batches(model, texts, *, concurrency, request_id, timeout_seconds, completed=())` | Plans, marks batches in `completed` as `SKIPPED`, then starts `min(concurrency, waiting)` workers. Each worker takes the next batch from a shared iterator, embeds it, records the outcome, and repeats. |
| `vectors_by_position(outcomes)` | Maps each text's position in the original list to its vector, for succeeded batches only. |

Decisions:

- **Workers pull; nothing is queued ahead.** Only `concurrency` tasks exist, and each starts a new batch only after finishing one. In `asyncio`, `next(queue, None)` cannot be interrupted between two workers, so no batch is taken twice.
- **When to stop starting batches.** A rate limit (slow down), a refused request (it will fail again), or an unexpected error (a bug) sets `stopped`; batches in flight finish, and the rest end `DEFERRED`. A timeout, an unavailable provider, or a malformed response fails only its own batch, because the next request may well succeed.
- **Unexpected errors are outcomes too.** `except Exception` records any error by class name. Otherwise one bug would throw away every finished batch, and their cost. `CancelledError` is not an `Exception` in Python, so cancellation still stops everything.
- **The model is not trusted.** A result with the wrong number of vectors, or from another model, fails its batch with `EmbeddingMalformedResponse`, instead of sliding vectors onto other texts. `vectors_by_position` also zips with `strict=True`.
- **One string is not a list.** `plan_batches("abc", ...)` and `completed="key"` raise `TypeError`: a `str` is a sequence of strings, so it would otherwise be read as three texts or as a set of one-letter keys.

**Action:** create `src/knowledge_service/embedding_batches.py`:

```python
"""Split many texts into batches an embedding model accepts, and embed them.

`plan_batches` cuts texts, in order, into the fewest consecutive batches that
fit the model's limits. The plan depends only on the texts and the model, so
the same input always gives the same batches with the same keys.

`embed_in_batches` sends those batches with at most `concurrency` requests in
flight. Workers take the next batch only when they finish one, so no more
than `concurrency` batches are ever started ahead of their results: that is
the backpressure. A rate limit, a refused request, or an unexpected error
stops new batches from starting; batches already sent finish, and the rest
are deferred.

The module is the last check before vectors are stored, so it does not trust
the model either: a result with the wrong number of vectors, or from another
model, fails its batch instead of shifting vectors onto other texts.

Every batch ends with exactly one outcome, in plan order:

- `SUCCEEDED` with its vectors, in its texts' order;
- `FAILED` with the name of the error, never its text;
- `DEFERRED` when it was not started because the run stopped early;
- `SKIPPED` when its key was in `completed`, so a later run resumes where an
  earlier one stopped without paying for the same batch twice.

A key names the model, the batch's position, and its texts. Keys therefore
only match when the texts and the limits are the same as in the earlier run;
otherwise every batch is sent again, which costs money but never skips text.
Record a key as completed only once its vectors are stored.
"""

import asyncio
import hashlib
import json
import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID

from knowledge_service.embeddings import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingMalformedResponse,
    EmbeddingModel,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
    Vector,
    check_inputs,
)

# After these, starting more batches only adds load or repeats the refusal.
_STOP_STARTING = (EmbeddingRateLimited, EmbeddingRequestRefused)


@dataclass(frozen=True, slots=True)
class PlannedBatch:
    """Consecutive texts sent in one request, and where they start."""

    key: str
    start: int
    # Document text: kept out of repr, so logging a batch never shows it.
    texts: tuple[str, ...] = field(repr=False)
    tokens: int


class BatchStatus(StrEnum):
    """How one planned batch ended."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEFERRED = "deferred"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class BatchOutcome:
    """The result of one planned batch; `error` is an error class name."""

    batch: PlannedBatch
    status: BatchStatus
    result: EmbeddingBatch | None = None
    error: str | None = None


def plan_batches(
    texts: Sequence[str], model: EmbeddingModel
) -> tuple[PlannedBatch, ...]:
    """Cut `texts` into consecutive batches within the model's limits.

    A text that no batch can hold raises `EmbeddingInputRejected` with its
    position in `texts`, before anything is sent.
    """
    if isinstance(texts, str):
        raise TypeError("texts must be a sequence of strings, not one string")
    limits = model.limits
    counts = [_tokens(index, text, model) for index, text in enumerate(texts)]
    batches: list[PlannedBatch] = []
    start = 0
    while start < len(texts):
        end, total = start, 0
        while (
            end < len(texts)
            and end - start < limits.max_inputs
            and total + counts[end] <= limits.max_batch_tokens
        ):
            total += counts[end]
            end += 1
        chunk = tuple(texts[start:end])
        batches.append(
            PlannedBatch(
                key=_key(model, start, chunk), start=start, texts=chunk, tokens=total
            )
        )
        start = end
    return tuple(batches)


async def embed_in_batches(
    model: EmbeddingModel,
    texts: Sequence[str],
    *,
    concurrency: int,
    request_id: UUID,
    timeout_seconds: float,
    completed: Collection[str] = (),
) -> tuple[BatchOutcome, ...]:
    """Embed every planned batch not in `completed`; return one outcome each."""
    if type(concurrency) is not int or concurrency < 1:
        raise ValueError("concurrency must be a positive integer")
    if math.isnan(timeout_seconds):
        raise ValueError("timeout_seconds must be a number")
    if isinstance(completed, str):
        raise TypeError("completed must be a collection of keys, not one key")
    plan = plan_batches(texts, model)
    done = frozenset(completed)
    outcomes: dict[int, BatchOutcome] = {}
    waiting: list[int] = []
    for position, batch in enumerate(plan):
        if batch.key in done:
            outcomes[position] = BatchOutcome(batch=batch, status=BatchStatus.SKIPPED)
        else:
            waiting.append(position)
    queue = iter(waiting)
    stopped = False

    async def worker() -> None:
        nonlocal stopped
        while not stopped:
            position = next(queue, None)
            if position is None:
                return
            batch = plan[position]
            try:
                result = await model.embed(
                    batch.texts, request_id=request_id, timeout_seconds=timeout_seconds
                )
            except Exception as error:
                # Recorded by name only; finished batches keep their vectors.
                outcomes[position] = _failed(batch, error)
                if isinstance(error, _STOP_STARTING) or not isinstance(
                    error, EmbeddingError
                ):
                    stopped = True
                continue
            if (
                len(result.vectors) != len(batch.texts)
                or result.identity != model.identity
            ):
                outcomes[position] = _failed(
                    batch, EmbeddingMalformedResponse("vectors do not match the batch")
                )
                continue
            outcomes[position] = BatchOutcome(
                batch=batch, status=BatchStatus.SUCCEEDED, result=result
            )

    async with asyncio.TaskGroup() as group:
        for _ in range(min(concurrency, len(waiting))):
            group.create_task(worker())
    return tuple(
        outcomes.get(position) or BatchOutcome(batch=batch, status=BatchStatus.DEFERRED)
        for position, batch in enumerate(plan)
    )


def vectors_by_position(outcomes: Sequence[BatchOutcome]) -> dict[int, Vector]:
    """Map each text's position in the input to its vector, for succeeded batches."""
    vectors: dict[int, Vector] = {}
    for outcome in outcomes:
        if outcome.result is None:
            continue
        texts, results = outcome.batch.texts, outcome.result.vectors
        for offset, (_, vector) in enumerate(zip(texts, results, strict=True)):
            vectors[outcome.batch.start + offset] = vector
    return vectors


def _failed(batch: PlannedBatch, error: Exception) -> BatchOutcome:
    return BatchOutcome(
        batch=batch, status=BatchStatus.FAILED, error=type(error).__name__
    )


def _tokens(index: int, text: str, model: EmbeddingModel) -> int:
    reason = None
    try:
        tokens = check_inputs([text], model.limits, model.count_tokens)
    except EmbeddingInputRejected as error:
        reason = error.reason
        tokens = 0
    if reason is not None:
        # Raised outside the except block, with this text's position.
        raise EmbeddingInputRejected(reason, index=index)
    return tokens


def _key(model: EmbeddingModel, start: int, texts: tuple[str, ...]) -> str:
    """Name a batch by its model, its position, and exactly its texts.

    Two identical batches at different positions get different keys, so
    resuming one never skips the other.
    """
    identity = model.identity
    payload = json.dumps(
        [identity.provider, identity.model, identity.dimensions, start, list(texts)],
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 3 — Coding step: tests

`RecordingModel` wraps `FakeEmbeddingModel`. It counts calls in flight (`active`, `most_active`), yields to the event loop an uneven number of times per call (1 to 8, from a hash of the batch's texts) so concurrent calls overlap and later batches often finish first, fails a batch that contains a chosen text, can block forever (for cancellation), and can alter a result (`tamper`) to imitate a broken model.

| Test | What it proves |
|---|---|
| `test_a_plan_keeps_every_text_once_in_order_within_the_limits` | Property: every text once, in order; contiguous starts; limits kept; fewest batches; same plan twice; unique keys. |
| `test_no_text_is_lost_or_reordered_and_concurrency_stays_bounded` | Property: random texts, concurrency 1–4, and failing texts; one outcome per batch in plan order; each vector equals its text embedded alone; failed batches have no vectors; `most_active <= concurrency`. |
| `test_batches_are_cut_by_count_and_by_tokens` | The example from Step 1. |
| `test_a_text_no_batch_can_hold_is_refused_by_its_position`, `test_nothing_is_sent_when_the_plan_is_refused` | The typed failure, before anything is sent. |
| `test_a_rate_limit_stops_new_batches_and_defers_the_rest`, `test_a_refused_request_also_stops_new_batches`, `test_a_timeout_fails_only_its_own_batch` | Backpressure and which errors stop the run. |
| `test_a_second_run_resumes_without_resending_finished_batches` | Resuming: only the unfinished batches are sent, and the two runs together cover every text. |
| `test_a_key_names_the_model_the_position_and_the_texts` | Keys. |
| `test_an_unexpected_error_fails_its_batch_and_keeps_the_finished_ones`, `test_vectors_that_do_not_match_their_batch_are_refused`, `test_one_key_is_not_a_collection_of_keys`, `test_logging_a_batch_or_its_outcome_never_shows_its_text` | The review's breaks. |
| The rest | Error names only, impossible concurrency, a NaN deadline, cancellation, an empty input, and one string. |

**Action:** create `tests/test_embedding_batches.py`:

```python
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
```

**Check:** `uv run pytest tests/test_embedding_batches.py -q` gives 24 passed.

Then break the module on purpose, one change at a time, and undo each change:

| Break | Test that must fail |
|---|---|
| Start a worker for every batch: `range(len(waiting))` | the concurrency property, and the stop and resume tests |
| Reverse the offsets in `vectors_by_position` | the no-lost-or-reordered property |
| Use `<` instead of `<=` for `max_batch_tokens` | the plan property (a batch is cut before it is full) |
| Never set `stopped` on a rate limit | `test_a_rate_limit_stops_new_batches_and_defers_the_rest` |
| Leave `start` out of the key | `test_a_key_names_the_model_the_position_and_the_texts` |
| Catch only `EmbeddingError` | `test_an_unexpected_error_fails_its_batch_and_keeps_the_finished_ones` |
| Give each result to the next waiting position, in finishing order | the no-lost-or-reordered property |

Why the delay must be uneven: if every call took the same number of turns, batches would always finish in the order they started, and a batcher that matched results by finishing order would pass every test.

### Step 4 — No coding in this step: run the gates, then record evidence

**Action:** run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_embedding_batches.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. In Design, record these notes:

- **Keys depend on the plan.** Changing the limits, or inserting a text near the start, changes every following key, so a resume sends everything again. That costs money but never skips a text.
- **A caller must record a key only after storing its vectors.** A key of a failed batch passed as `completed` would make its texts silently go without vectors.
- **The whole plan is built up front,** but only `concurrency` tasks ever exist.
- **A model whose token count changes between planning and sending** can still refuse a planned batch at send time; it ends `FAILED`, and retrying the same plan fails the same way.

Answer these reflection questions in your own words:

1. What does the batcher hide from its caller? List what a caller never has to count, track, or decide.
2. Which promise would be easiest to break in a later speed-up? For example, what could go wrong if someone wrote vectors into one shared list by "the next free slot" as results arrive?
3. Why does a rate limit stop new batches, but a timeout does not?
4. Why is a batch key built from its position as well as its texts? What would go wrong with two identical batches otherwise?
5. What measurement could show that your concurrency setting is wrong? What would you watch while raising it from 2 to 8?
6. Which test would let a real bug reach production if you deleted it? Name it and describe the bug.

**Check:** every command passes, and the evidence explains the batcher in your words.

## Completion checklist

- [ ] `plan_batches` cuts texts deterministically, in order, into the fewest batches within both limits, and refuses an impossible text by its position.
- [ ] `embed_in_batches` keeps at most `concurrency` requests in flight, stops starting batches after a rate limit, a refusal, or an unexpected error, and returns one outcome per batch in plan order.
- [ ] Batches whose keys are in `completed` are skipped and not sent.
- [ ] A result that does not match its batch fails that batch; no vector reaches another text's position.
- [ ] Outcomes and batches show no text in `repr`, and errors are recorded by class name.
- [ ] Both property tests pass, with the rest of the 24 tests.
- [ ] Format, lint, types, the deterministic suite, `make check`, and `make docs-check` pass.
- [ ] The evidence file holds your invariant, predictions, design, verification, and your own reflection.

When you're done, share your code or any error you hit, and I'll review it.
