"""Sparse term weights: which vocabulary pieces a text holds, and how much each counts.

A sparse model such as BGE-M3 gives every text a short list of token IDs
from its vocabulary, each with a positive weight. Two texts match as much as
the weights of the tokens they share: the sum of the products, so a rare,
important piece shared by both counts far more than a shared comma. Unlike
a dense vector, almost every entry is zero, so only the others are kept.

This module is the port a sparse model fills. It is a prototype for the
M05-T10 comparison with PostgreSQL full-text search; nothing in production
uses it.

What a `SparseVector` promises, once `checked_sparse_vectors` returned it:

- **Pairs by token ID.** `(token_id, weight)` pairs in ascending token ID
  order, each ID once, each an int inside the model's vocabulary.
- **Positive, bounded weights.** Above zero, since a zero weight is left
  out instead, and at most `MAX_SPARSE_WEIGHT`, so multiplying and summing
  them can never overflow to infinity and flatten the ranking.
- **Possibly empty.** A text of only punctuation may hold no weighted token;
  it then matches nothing.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from knowledge_service.embeddings.port import (
    EmbeddingLimits,
    EmbeddingMalformedResponse,
    InvalidEmbedding,
)

type SparseVector = tuple[tuple[int, float], ...]

# BGE-M3's weights stay well below 10. A bound far above that keeps every
# product and every sum of up to 8192 of them a finite number.
MAX_SPARSE_WEIGHT = 1_000.0


@dataclass(frozen=True, slots=True)
class SparseModelIdentity:
    """Which model produced sparse weights; weights of two identities never mix."""

    provider: str
    model: str
    vocabulary_size: int

    def __post_init__(self) -> None:
        if type(self.provider) is not str or type(self.model) is not str:
            raise InvalidEmbedding("provider and model must be strings")
        if not self.provider.strip() or not self.model.strip():
            raise InvalidEmbedding("provider and model must not be empty")
        if type(self.vocabulary_size) is not int or self.vocabulary_size < 1:
            raise InvalidEmbedding("vocabulary_size must be a positive integer")


class SparseModel(Protocol):
    """A model that turns batches of texts into sparse term weights."""

    @property
    def sparse_identity(self) -> SparseModelIdentity:
        """The provider, model, and vocabulary size."""
        ...

    @property
    def limits(self) -> EmbeddingLimits:
        """The most one call to `encode_sparse` may carry."""
        ...

    def count_tokens(self, text: str) -> int:
        """Count `text` in this model's tokens, never fewer than the model does."""
        ...

    async def encode_sparse(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[SparseVector, ...]:
        """Return one sparse vector per text, in order, or raise an `EmbeddingError`."""
        ...


def checked_sparse_vectors(
    items: object, *, expected: int, vocabulary_size: int
) -> tuple[SparseVector, ...]:
    """Return the model's weights as sparse vectors, or raise if any is invalid.

    `items` is whatever the model sent: a list with one list of
    `[token_id, weight]` pairs per text. Nothing about its shape is trusted;
    any fault raises `EmbeddingMalformedResponse` naming the text's index.
    """
    vectors = _list(items)
    if vectors is None or len(vectors) != expected:
        raise EmbeddingMalformedResponse(f"expected {expected} sparse vectors")
    return tuple(
        _checked(item, index, vocabulary_size) for index, item in enumerate(vectors)
    )


def _checked(item: object, index: int, vocabulary_size: int) -> SparseVector:
    pairs = _list(item)
    if pairs is None:
        raise EmbeddingMalformedResponse(f"sparse vector {index} is not a list")
    result: list[tuple[int, float]] = []
    for pair in pairs:
        values = _list(pair)
        if values is None or len(values) != 2:
            raise EmbeddingMalformedResponse(f"sparse vector {index} has a bad pair")
        token, weight = values
        # bool is an int, so `True` would otherwise pass as token 1.
        if type(token) is not int or not 0 <= token < vocabulary_size:
            raise EmbeddingMalformedResponse(f"sparse vector {index} has a bad token")
        if (
            type(weight) is not float
            or not math.isfinite(weight)
            or not 0 < weight <= MAX_SPARSE_WEIGHT
        ):
            raise EmbeddingMalformedResponse(f"sparse vector {index} has a bad weight")
        if result and token <= result[-1][0]:
            raise EmbeddingMalformedResponse(
                f"sparse vector {index} is not ordered by token"
            )
        result.append((token, weight))
    return tuple(result)


def _list(value: object) -> list[object] | None:
    return cast(list[object], value) if isinstance(value, list) else None
