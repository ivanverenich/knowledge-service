"""Recall@k and MRR: how well one ranked list found the relevant Chunks.

Both scores start from the same input: for one case, the rank of each
relevant Chunk in the list a search returned, counted from 1, or None when
the Chunk was not returned at all.

- **Recall@k** is the share of a case's relevant Chunks found in the top
  `k`. A case with two relevant Chunks, ranked 1 and missed, has Recall@5
  of 1/2.
- **Reciprocal rank** is 1 divided by the rank of the first relevant Chunk,
  or 0 when none was found. Ranked 1 gives 1, ranked 4 gives 1/4. The mean
  over many cases is the MRR (mean reciprocal rank).

Scores are exact fractions, so two runs with equal ranks always give equal
scores, and a mean never drifts in the last digit. Bad input raises
`InvalidMetricInput`, whose message never repeats a value.
"""

from collections.abc import Sequence
from fractions import Fraction
from typing import cast


class InvalidMetricInput(ValueError):
    """A rank list or a cut-off breaks the rules of the metric."""


def recall_at(ranks: Sequence[int | None], k: int) -> Fraction:
    """Return the share of relevant Chunks ranked at `k` or better."""
    _check_ranks(ranks)
    # bool is an int, so `True` would otherwise pass as a cut-off of 1.
    if type(k) is not int or k < 1:
        raise InvalidMetricInput("k must be a positive int")
    found = sum(1 for rank in ranks if rank is not None and rank <= k)
    return Fraction(found, len(ranks))


def reciprocal_rank(ranks: Sequence[int | None]) -> Fraction:
    """Return 1 / the best rank of a relevant Chunk, or 0 if none was found."""
    _check_ranks(ranks)
    found = [rank for rank in ranks if rank is not None]
    return Fraction(1, min(found)) if found else Fraction(0)


def mean(scores: Sequence[Fraction]) -> Fraction:
    """Return the exact average of one score over several cases."""
    # Checked at run time too, because a caller may pass any object.
    if isinstance(scores, (str, bytes)) or not isinstance(
        cast(object, scores), Sequence
    ):
        raise InvalidMetricInput("scores must be a sequence of fractions")
    if not scores:
        raise InvalidMetricInput("a mean needs at least one score")
    if any(type(score) is not Fraction for score in scores):
        raise InvalidMetricInput("every score must be a Fraction")
    return sum(scores, Fraction(0)) / len(scores)


def _check_ranks(ranks: Sequence[int | None]) -> None:
    # A string is a sequence too, of characters, not ranks.
    if isinstance(ranks, (str, bytes)) or not isinstance(cast(object, ranks), Sequence):
        raise InvalidMetricInput("ranks must be a sequence")
    if not ranks:
        raise InvalidMetricInput("a case needs at least one relevant Chunk")
    for rank in ranks:
        if rank is not None and (type(rank) is not int or rank < 1):
            raise InvalidMetricInput("a rank must be None or an int from 1")
