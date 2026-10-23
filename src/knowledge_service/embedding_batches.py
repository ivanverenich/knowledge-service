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
    texts: Sequence[str],
    model: EmbeddingModel,
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
                key=_key(model, start, chunk),
                start=start,
                texts=chunk,
                tokens=total,
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
                    batch.texts,
                    request_id=request_id,
                    timeout_seconds=timeout_seconds,
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
                    batch,
                    EmbeddingMalformedResponse("vectors do not match the batch"),
                )
                continue
            outcomes[position] = BatchOutcome(
                batch=batch,
                status=BatchStatus.SUCCEEDED,
                result=result,
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


def _failed(batch: PlannedBatch, error: Exception) -> BatchOutcome:
    return BatchOutcome(
        batch=batch, status=BatchStatus.FAILED, error=type(error).__name__
    )
