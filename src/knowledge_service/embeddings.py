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
        super().__init__(f"embedding input rejected {reason.value}{where}")


class EmbeddingUnavailable(EmbeddingError):
    """The embedding model cannot currently serve the request."""


class EmbeddingTimeout(EmbeddingError):
    """The embedding model did not answer within the deadline."""


class EmbeddingRateLimited(EmbeddingError):
    """The embedding model refused the request because of a rate limit."""


class EmbeddingMalformedResponse(EmbeddingError):
    """The model answered, but not with one valid vector per input."""


class EmbeddingRequestRefused(EmbeddingError):
    """The provider refused the request itself; sending it again cannot help.

    For example a wrong API key, an unknown model, or dimensions the model
    does not support: a configuration problem, not a passing failure.
    """


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
