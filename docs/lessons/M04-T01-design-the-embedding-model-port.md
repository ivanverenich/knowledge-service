# M04-T01 — Design the EmbeddingModel port

Source task: [M04-T01](../curriculum/milestones/M04-dense-retrieval/M04-T01-design-the-embedding-model-port.md)

## What you will do

M04 makes the service search by meaning. An **embedding model** turns a text into a **vector**, a fixed-length list of numbers, so that texts with similar meaning get nearby vectors. Every Chunk is embedded once, each question is embedded at search time, and the nearest Chunks are the candidates.

This lesson defines the **port**: the interface every embedding adapter must meet, like the `ChatModel` port from M01. No provider is called yet. M04-T02 adds the OpenAI adapter, M04-T03 splits many Chunks into batches, and M04-T04 stores vectors in PostgreSQL. Here you build:

| Requirement | How this lesson meets it |
|---|---|
| Batch-oriented async interface | `EmbeddingModel.embed(texts, *, request_id, timeout_seconds)` takes a batch and is `async`. |
| Input limits | `EmbeddingLimits`: most inputs per call, most tokens per input, most tokens per call. `count_tokens` measures a text in the model's own tokens. |
| Dimensions and model identity | `EmbeddingModelIdentity`: provider, model, and vector length. Vectors of different identities must never be compared. |
| Usage | `EmbeddingUsage`: tokens and latency, no text. |
| Partial-failure policy | All or nothing: one vector per input, in order, or an exception. |
| Typed errors | `EmbeddingError` and five subclasses, classified by `classify_failure`. |
| A deterministic fake | `FakeEmbeddingModel` in `tests/fakes.py`: the same text always gets the same vector. |

This lesson includes coding: Steps 2–5 write code, while Steps 1 and 6 do not.

Terms used in this lesson:

- **Port:** a `Protocol` that names what callers may rely on. Adapters implement it, and tests use a fake that implements it too.
- **Token:** the unit a model counts text in, roughly a word or part of one. Each model has its own tokenizer, so only the adapter can count its tokens.
- **Batch:** several texts sent in one call. Providers limit how many texts and how many tokens one call may carry.
- **Deterministic:** the same input always gives the same output, on every run and machine.

## Before you begin

- **Why a port before an adapter.** Everything after this lesson (batching, storage, search, model migration) depends on what `embed` promises, not on OpenAI. Writing the promise first, with a fake that keeps it, lets M04-T03 to T06 be tested without network calls or API keys.
- **Why `count_tokens` is on the port.** M03's `Chunk.token_count` counts the project's own tokens (M03-T09), not the model's. Limits are in model tokens, so the model must count them; M04-T03's batcher will use `count_tokens` to fill batches.
- **All-or-nothing partial-failure policy.** Providers answer a batch as a whole. If the port returned some vectors and an error for the rest, every caller would have to track which Chunks got a vector. Instead, a failed call returns nothing, and the batcher retries or splits that batch.
- **What the adversarial review found.** An independent review attacked the first version and found six breaks, all fixed in the code below with a test that fails without the fix:
  - `checked_batch` let raw `TypeError` and `OverflowError` escape on responses such as `None`, a dictionary, or a huge integer.
  - It accepted strings, bytes, booleans, and all-zero vectors as vectors. A zero vector has no direction, so distance to it is undefined.
  - A single string passed as `texts` was embedded letter by letter, because a `str` is itself a sequence of strings.
  - A token count that was negative or `True` slipped past the batch budget.
  - Text with an invalid Unicode character crashed the fake, and a tokenizer error could carry the text inside a chained exception.
  - The fake ignored its timeout.
- **Files you will touch:**
  - new `src/knowledge_service/embeddings.py`
  - `src/knowledge_service/errors.py`
  - `tests/fakes.py`, `tests/test_errors.py`, and new `tests/test_embeddings.py`
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone. No database is needed for this lesson.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and the expected failing check

**Invariant:** for any batch, `embed` either returns exactly one finite vector of `identity.dimensions` numbers per input, in input order, or raises an `EmbeddingError` and returns nothing. A batch that breaks the limits is refused before anything is sent, naming the index of the bad input, never its text. The fake gives the same vector for the same text in any batch, order, or process.

**Action:** create `docs/evidence/M04-T01-embedding-model-port.md`:

```markdown
# M04-T01 — EmbeddingModel port evidence

Completed: pending

## Invariant

Pending.

## Expected failing check

Before writing code, say which test you expect to fail first, and how, once
the tests exist but `embeddings.py` does not. Then predict what `embed`
returns or raises for: two texts in either order; a batch of exactly
`max_inputs` texts, and one more; a text of exactly `max_input_tokens`
tokens, and one more; a batch whose total tokens pass `max_batch_tokens` at
its third text; an empty or blank text; an empty batch; and a provider
failure.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and predictions.

### Step 2 — Coding step: the port

New module `src/knowledge_service/embeddings.py`.

| Name | What it holds or does |
|---|---|
| `EmbeddingModelIdentity` | Provider, model, and dimensions; a later lesson stores it next to every vector. |
| `EmbeddingLimits` | `max_inputs`, `max_input_tokens`, `max_batch_tokens`; all positive, and one input may not be allowed more than a whole batch. |
| `EmbeddingUsage` | Input tokens, latency, optional price ID; never text. |
| `EmbeddingBatch`, `Vector` | The identity, the vectors as tuples (immutable), and the usage. It refuses a vector of the wrong length, so the promise holds even for an adapter that skips `checked_batch`. |
| `EmbeddingModel` | The `Protocol`: `identity`, `limits`, `count_tokens`, and `async embed`. |
| `EmbeddingInputRejected` | Raised before sending, with a `RejectionReason` and the input's `index`. Reasons: an empty text, a text that is not valid Unicode, a text the tokenizer cannot count, too many inputs, an input too long, a batch too long. |
| `EmbeddingUnavailable`, `EmbeddingTimeout`, `EmbeddingRateLimited`, `EmbeddingMalformedResponse` | Provider failures, for M04-T02 to raise. |
| `check_inputs` | The shared input check every adapter calls before sending; returns the batch's total tokens. |
| `checked_batch` | The shared response check every adapter calls on what came back. |

Decisions:

- **The checks live in the port module, not in each adapter.** The rules are the port's promise, so they are written once; an adapter cannot forget them, and the fake and the OpenAI adapter behave the same.
- **Errors name an index and a reason, never the text.** Errors end up in logs, and Chunk text is document content (ADR 0010).
- **An empty batch is not an error.** It returns no vectors and calls nothing, so a batcher never has to special-case it, and it cannot fail.
- **Nothing about a response is trusted.** `checked_batch` takes `vectors: object`, because it is whatever the provider sent. Only `int` and `float` values count as numbers: not `bool` (a subclass of `int` in Python), not numeric strings. An all-zero vector is refused.
- **No chained exception may carry text.** A tokenizer's own error might quote its input, and a `UnicodeEncodeError` keeps the whole string in its `object` attribute. `check_inputs` therefore checks in small helpers that return `None` or `False`, and raises its own error outside any `except` block, so `__context__` stays empty.
- **One string is a programming error.** `check_inputs("abc", ...)` raises `TypeError`: Pyright cannot catch it, because a `str` is a `Sequence[str]`.
- **`InvalidEmbedding` is a `ValueError`,** like `InvalidDocument`, for impossible settings; `EmbeddingError` is for calls that fail.

**Action:** create `src/knowledge_service/embeddings.py`:

```python
"""The EmbeddingModel port: turn a batch of texts into vectors.

An embedding model maps text to a fixed-length vector of numbers, so that
texts with similar meaning get nearby vectors. Retrieval embeds every Chunk
once and each question at search time, then compares the vectors.

The port promises, for every adapter:

- **One call is one batch, and it is all or nothing.** `embed` returns exactly
  one vector per input text, in input order, or raises. There are no partial
  results: a caller that splits work into batches retries or splits a failed
  batch as a whole.
- **An empty batch returns no vectors** and never calls the model, so it
  cannot fail.
- **Inputs are checked before anything leaves the process.** An empty text,
  a text that is not valid Unicode, a text whose tokens cannot be counted,
  too many texts, a text over the per-input token limit, or a batch over the
  total token limit raises `EmbeddingInputRejected` naming the index of the
  offending input, never its text.
- **Every vector is a list of the model's dimensions of finite numbers, not
  all zero.** Any other response raises `EmbeddingMalformedResponse`.
- **Errors carry no input text,** not even in a chained exception, so they
  are safe to log.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID


class InvalidEmbedding(ValueError):
    """An embedding value violates a domain invariant."""


class EmbeddingError(Exception):
    """Base error for embedding failures."""


class RejectionReason(StrEnum):
    """Why an input batch was refused before calling the model."""

    EMPTY_TEXT = "empty_text"
    UNENCODABLE_TEXT = "unencodable_text"
    UNCOUNTABLE_TEXT = "uncountable_text"
    TOO_MANY_INPUTS = "too_many_inputs"
    INPUT_TOO_LONG = "input_too_long"
    BATCH_TOO_LONG = "batch_too_long"


class EmbeddingInputRejected(EmbeddingError):
    """The batch breaks the model's input limits; nothing was sent."""

    def __init__(self, reason: RejectionReason, *, index: int | None) -> None:
        self.reason = reason
        self.index = index
        where = "" if index is None else f" at input {index}"
        super().__init__(f"embedding input rejected: {reason.value}{where}")


class EmbeddingUnavailable(EmbeddingError):
    """The embedding model cannot currently serve the request."""


class EmbeddingTimeout(EmbeddingError):
    """The embedding model did not answer within the deadline."""


class EmbeddingRateLimited(EmbeddingError):
    """The embedding model refused the request because of a rate limit."""


class EmbeddingMalformedResponse(EmbeddingError):
    """The model answered, but not with one valid vector per input."""


@dataclass(frozen=True, slots=True)
class EmbeddingModelIdentity:
    """Which model produced a vector; vectors of different identities never mix."""

    provider: str
    model: str
    dimensions: int

    def __post_init__(self) -> None:
        if not self.provider.strip() or not self.model.strip():
            raise InvalidEmbedding("provider and model must not be empty")
        if type(self.dimensions) is not int or self.dimensions < 1:
            raise InvalidEmbedding("dimensions must be a positive integer")


@dataclass(frozen=True, slots=True)
class EmbeddingLimits:
    """The most one `embed` call may carry, in model tokens."""

    max_inputs: int
    max_input_tokens: int
    max_batch_tokens: int

    def __post_init__(self) -> None:
        for value in (self.max_inputs, self.max_input_tokens, self.max_batch_tokens):
            if type(value) is not int or value < 1:
                raise InvalidEmbedding("embedding limits must be positive integers")
        if self.max_input_tokens > self.max_batch_tokens:
            raise InvalidEmbedding("one input cannot be allowed more than a batch")


@dataclass(frozen=True, slots=True)
class EmbeddingUsage:
    """Safe usage metadata for one call: counts and timing, no text."""

    input_tokens: int
    latency_ms: int
    price_id: str | None = None

    def __post_init__(self) -> None:
        if type(self.input_tokens) is not int or self.input_tokens < 0:
            raise InvalidEmbedding("input tokens must not be negative")
        if type(self.latency_ms) is not int or self.latency_ms < 0:
            raise InvalidEmbedding("latency must not be negative")


type Vector = tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    """The vectors for one batch, in input order, and what producing them cost."""

    identity: EmbeddingModelIdentity
    vectors: tuple[Vector, ...]
    usage: EmbeddingUsage

    def __post_init__(self) -> None:
        if any(len(vector) != self.identity.dimensions for vector in self.vectors):
            raise InvalidEmbedding("every vector must have the model's dimensions")


class EmbeddingModel(Protocol):
    """A model that turns batches of texts into vectors."""

    @property
    def identity(self) -> EmbeddingModelIdentity:
        """The provider, model, and vector dimensions."""
        ...

    @property
    def limits(self) -> EmbeddingLimits:
        """The most one call to `embed` may carry."""
        ...

    def count_tokens(self, text: str) -> int:
        """Count `text` in this model's tokens, for budgeting batches."""
        ...

    async def embed(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> EmbeddingBatch:
        """Return one vector per text, in order, or raise an `EmbeddingError`."""
        ...


def check_inputs(
    texts: Sequence[str],
    limits: EmbeddingLimits,
    count_tokens: Callable[[str], int],
) -> int:
    """Refuse a batch that breaks `limits`; return its total token count.

    Adapters call this before sending anything, so a rejected batch never
    reaches the provider. A failure is raised outside any `except` block, so
    no chained exception can carry the text.
    """
    if isinstance(texts, str):
        raise TypeError("texts must be a sequence of strings, not one string")
    if len(texts) > limits.max_inputs:
        raise EmbeddingInputRejected(RejectionReason.TOO_MANY_INPUTS, index=None)
    total = 0
    for index, text in enumerate(texts):
        if not text.strip():
            raise EmbeddingInputRejected(RejectionReason.EMPTY_TEXT, index=index)
        if not _encodable(text):
            raise EmbeddingInputRejected(RejectionReason.UNENCODABLE_TEXT, index=index)
        tokens = _token_count(text, count_tokens)
        if tokens is None:
            raise EmbeddingInputRejected(RejectionReason.UNCOUNTABLE_TEXT, index=index)
        if tokens > limits.max_input_tokens:
            raise EmbeddingInputRejected(RejectionReason.INPUT_TOO_LONG, index=index)
        total += tokens
        if total > limits.max_batch_tokens:
            raise EmbeddingInputRejected(RejectionReason.BATCH_TOO_LONG, index=index)
    return total


def checked_batch(
    identity: EmbeddingModelIdentity,
    vectors: object,
    usage: EmbeddingUsage,
    *,
    expected: int,
) -> EmbeddingBatch:
    """Return the provider's vectors as a batch, or raise if any is invalid.

    Adapters call this on every response, so a short, padded, or corrupted
    answer never reaches storage. `vectors` is whatever the provider sent, so
    nothing about its shape is trusted.
    """
    items = _sequence(vectors)
    if items is None:
        raise EmbeddingMalformedResponse("the response is not a list of vectors")
    if len(items) != expected:
        raise EmbeddingMalformedResponse(
            f"expected {expected} vectors, received {len(items)}"
        )
    checked = tuple(
        _checked_vector(item, index, identity.dimensions)
        for index, item in enumerate(items)
    )
    return EmbeddingBatch(identity=identity, vectors=checked, usage=usage)


def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _token_count(text: str, count_tokens: Callable[[str], int]) -> int | None:
    # A tokenizer's own error may quote the text, so it is dropped here.
    try:
        tokens = count_tokens(text)
    except Exception:
        return None
    if type(tokens) is not int or tokens < 1:
        return None
    return tokens


def _sequence(value: object) -> Sequence[object] | None:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        return None
    return cast(Sequence[object], value)


def _checked_vector(item: object, index: int, dimensions: int) -> Vector:
    values = _sequence(item)
    if values is None:
        raise EmbeddingMalformedResponse(f"vector {index} is not a list of numbers")
    if len(values) != dimensions:
        raise EmbeddingMalformedResponse(
            f"vector {index} has {len(values)} dimensions, expected {dimensions}"
        )
    numbers = tuple(_number(value) for value in values)
    if None in numbers:
        raise EmbeddingMalformedResponse(f"vector {index} is not finite numbers")
    vector = cast(Vector, numbers)
    if not any(vector):
        raise EmbeddingMalformedResponse(f"vector {index} is all zeros")
    return vector


def _number(value: object) -> float | None:
    # bool is a subclass of int, and a string may look numeric; neither is.
    if type(value) is not int and type(value) is not float:
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None
```

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 3 — Coding step: classify embedding failures

`classify_failure` (M01-T07) decides the HTTP status, retry owner, and telemetry outcome for any exception. Embedding errors need a place in it, because question embedding will happen inside a request in M04-T06.

| Error | Kind | Why |
|---|---|---|
| `EmbeddingInputRejected` | `VALIDATION` | The input broke a limit; retrying the same input cannot help. |
| `EmbeddingRateLimited` | `CAPACITY` | Retry later. |
| any other `EmbeddingError` | `DEPENDENCY` | The provider failed. |

`EmbeddingInputRejected` and `EmbeddingRateLimited` are subclasses of `EmbeddingError`, so they must be checked first.

**Action:** in `src/knowledge_service/errors.py`, add the import and make the middle of `classify_failure` read:

```python
from knowledge_service.embeddings import (
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingRateLimited,
)
```

```python
    if isinstance(error, EmbeddingInputRejected):
        return FailureKind.VALIDATION
    if isinstance(error, (ChatModelRateLimited, EmbeddingRateLimited)):
        return FailureKind.CAPACITY
    if isinstance(error, (AnswerDeadlineExceeded, ChatModelError, EmbeddingError)):
```

In `tests/test_errors.py`, add this import after the other `knowledge_service` imports:

```python
from knowledge_service.embeddings import (
    EmbeddingInputRejected,
    EmbeddingMalformedResponse,
    EmbeddingRateLimited,
    EmbeddingUnavailable,
    RejectionReason,
)
```

and add these rows to the `test_classifies_failures` parameters, after the `ChatModelRateLimited` row:

```python
        (EmbeddingUnavailable(), FailureKind.DEPENDENCY),
        (EmbeddingMalformedResponse("bad vectors"), FailureKind.DEPENDENCY),
        (EmbeddingRateLimited(), FailureKind.CAPACITY),
        (
            EmbeddingInputRejected(RejectionReason.EMPTY_TEXT, index=0),
            FailureKind.VALIDATION,
        ),
```

**Check:** `uv run pytest tests/test_errors.py -q` passes.

### Step 4 — Coding step: the deterministic fake

`FakeEmbeddingModel` goes in `tests/fakes.py`, next to `FakeChatModel`.

| Behaviour | How |
|---|---|
| Stable vectors | A SHAKE-256 hash of the text gives one byte per dimension, mapped to the range −1 to 1 and scaled to length 1. A hash, not Python's `hash()`, which changes between processes. |
| Model tokens | One token per whitespace-separated word. |
| Limits and response checks | It calls `check_inputs` and `checked_batch`, like a real adapter. |
| What was sent | `batches` records every batch that passed the input check, so tests can prove a rejected batch was never sent. |
| Failures | `error=` raises that error after the batch is "sent". `wait_for=` blocks until an event is set, but no longer than `timeout_seconds`; then it raises `EmbeddingTimeout`. |

**Action:** in `tests/fakes.py`, add `import hashlib` and `import math` to the standard-library imports, add this import after the `knowledge_service.contracts` import:

```python
from knowledge_service.embeddings import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingLimits,
    EmbeddingModelIdentity,
    EmbeddingTimeout,
    EmbeddingUsage,
    check_inputs,
    checked_batch,
)
```

and append:

```python
class FakeEmbeddingModel:
    """Deterministic EmbeddingModel: the same text always gets the same vector.

    A token is a whitespace-separated word. Each vector is derived from a
    SHAKE-256 hash of its text and scaled to length 1, so it is stable across
    calls, batches, and processes. `batches` records every batch that reached
    the "provider", so tests can check what was sent.
    """

    def __init__(
        self,
        *,
        dimensions: int = 8,
        limits: EmbeddingLimits | None = None,
        error: EmbeddingError | None = None,
        wait_for: asyncio.Event | None = None,
    ) -> None:
        self._identity = EmbeddingModelIdentity(
            provider="fake", model="fake-embedding", dimensions=dimensions
        )
        self._limits = limits or EmbeddingLimits(
            max_inputs=16, max_input_tokens=64, max_batch_tokens=256
        )
        self._error = error
        self._wait_for = wait_for
        self.batches: list[tuple[str, ...]] = []

    @property
    def identity(self) -> EmbeddingModelIdentity:
        return self._identity

    @property
    def limits(self) -> EmbeddingLimits:
        return self._limits

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    async def embed(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> EmbeddingBatch:
        tokens = check_inputs(texts, self._limits, self.count_tokens)
        if not texts:
            return checked_batch(
                self._identity,
                [],
                EmbeddingUsage(input_tokens=0, latency_ms=0),
                expected=0,
            )
        self.batches.append(tuple(texts))
        if self._wait_for is not None:
            try:
                async with asyncio.timeout(timeout_seconds):
                    await self._wait_for.wait()
            except TimeoutError:
                raise EmbeddingTimeout() from None
        if self._error is not None:
            raise self._error
        return checked_batch(
            self._identity,
            [self._vector(text) for text in texts],
            EmbeddingUsage(input_tokens=tokens, latency_ms=0),
            expected=len(texts),
        )

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.shake_256(text.encode()).digest(self._identity.dimensions)
        raw = [byte / 127.5 - 1.0 for byte in digest]
        norm = math.sqrt(sum(value * value for value in raw))
        return [value / norm for value in raw]
```

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright tests/fakes.py` passes.

### Step 5 — Coding step: tests

| Test | What it proves |
|---|---|
| `test_the_same_text_gets_the_same_vector_in_any_batch_and_order` | Stable vectors and input order. |
| `test_each_vector_has_the_model_dimensions_and_unit_length` | Dimensions, identity, and usage. |
| `test_a_full_batch_is_accepted_and_one_more_input_is_refused`, `test_an_input_at_the_token_limit_passes_and_one_over_is_refused`, `test_a_batch_over_its_token_budget_is_refused_where_it_overflows` | Exact batch boundaries, and that nothing is sent when a batch is refused. |
| `test_an_empty_text_is_refused_by_its_index`, `test_an_empty_batch_returns_no_vectors_without_calling_the_model` | Validation. |
| `test_a_rejection_never_carries_the_input_text` | Seeded sensitive text never reaches an error, its notes, or a chained exception (`everything_shown`). |
| `test_one_string_is_not_a_batch`, `test_a_text_whose_tokens_cannot_be_counted_is_refused_without_its_text`, `test_a_text_that_is_not_valid_unicode_is_refused_without_its_text` | The review's input breaks. |
| `test_a_provider_failure_reaches_the_caller_as_a_typed_error`, `test_a_model_slower_than_the_deadline_times_out_as_a_typed_error`, `test_cancellation_reaches_the_caller` | Failure translation, the deadline, and cancellation. |
| `test_a_malformed_response_is_refused`, `test_any_response_is_a_valid_batch_or_a_typed_refusal`, `test_a_valid_response_keeps_its_order` | `checked_batch` refuses 15 kinds of bad response, and a Hypothesis property (generated inputs) shows any response is either a valid batch or an `EmbeddingMalformedResponse`, never another exception. |
| `test_impossible_settings_are_refused`, `test_a_batch_cannot_hold_a_vector_of_other_dimensions` | `InvalidEmbedding` for impossible identity, limits, usage, or batch. |

**Action:** create `tests/test_embeddings.py`:

```python
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
```

**Check:** `uv run pytest tests/test_embeddings.py -q` gives 43 passed.

Then break the code on purpose, one change at a time, and undo each change:

| Break | Test that must fail |
|---|---|
| In `check_inputs`, use `>=` instead of `>` for `max_inputs` | `test_a_full_batch_is_accepted_and_one_more_input_is_refused` |
| In the fake, record the batch before calling `check_inputs` | the boundary and validation tests (a refused batch was "sent") |
| Raise the `INPUT_TOO_LONG` rejection `from ValueError(text)` | `test_a_rejection_never_carries_the_input_text` |
| In `_number`, accept `isinstance(value, (int, float))` | `test_a_malformed_response_is_refused[booleans]` |
| Remove the `if not any(vector)` check | `test_a_malformed_response_is_refused[all-zeros]` |

### Step 6 — No coding in this step: run the gates, then record evidence

**Action:** run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_embeddings.py tests/test_errors.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. Answer these reflection questions in your own words:

1. What does the port hide from its callers? List what a caller of `embed` never has to check or count.
2. Which promise would be easiest to break during a later speed-up? For example, what could go wrong if M04-T03 ran batches in parallel and wrote vectors back by position?
3. Why does a refused input name its index but never its text? Where would that text end up otherwise?
4. Why is the partial-failure policy all-or-nothing? What would a caller have to track if `embed` could return some vectors and an error?
5. What test or measurement could prove your design wrong? For example, what would show that one token limit is not enough for Ukrainian text?
6. Which test would let a real bug reach production if you deleted it? Name it and describe the bug.

**Check:** every command passes, and the evidence explains the port in your words.

## Completion checklist

- [ ] `embeddings.py` defines the identity, limits, usage, batch, the `EmbeddingModel` port, the typed errors, `check_inputs`, and `checked_batch`.
- [ ] `classify_failure` maps rejected input to validation, rate limits to capacity, and other embedding errors to dependency.
- [ ] `FakeEmbeddingModel` gives stable, unit-length vectors and records what it sent.
- [ ] The tests prove stable vectors, exact batch boundaries, validation, failure translation, and no input text in errors.
- [ ] Format, lint, types, the deterministic suite, `make check`, and `make docs-check` pass.
- [ ] The evidence file holds your invariant, predictions, design, verification, and your own reflection.

When you're done, share your code or any error you hit, and I'll review it.
