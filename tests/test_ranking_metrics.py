"""Recall@k and MRR: exact scores from the ranks of relevant Chunks."""

from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from knowledge_service.retrieval.ranking_metrics import (
    InvalidMetricInput,
    mean,
    recall_at,
    reciprocal_rank,
)

ranks_lists = st.lists(
    st.one_of(st.none(), st.integers(min_value=1, max_value=50)),
    min_size=1,
    max_size=6,
)


@pytest.mark.parametrize(
    ("ranks", "k", "expected"),
    [
        ((1,), 1, Fraction(1)),
        ((2,), 1, Fraction(0)),
        ((2,), 5, Fraction(1)),
        ((None,), 10, Fraction(0)),
        ((1, None), 5, Fraction(1, 2)),
        ((3, 7), 5, Fraction(1, 2)),
        ((3, 7), 10, Fraction(1)),
    ],
)
def test_recall_counts_the_relevant_chunks_in_the_top_k(
    ranks: tuple[int | None, ...], k: int, expected: Fraction
) -> None:
    assert recall_at(ranks, k) == expected


@pytest.mark.parametrize(
    ("ranks", "expected"),
    [
        ((1,), Fraction(1)),
        ((4,), Fraction(1, 4)),
        ((None,), Fraction(0)),
        ((None, 3, 2), Fraction(1, 2)),
    ],
)
def test_reciprocal_rank_uses_the_best_relevant_rank(
    ranks: tuple[int | None, ...], expected: Fraction
) -> None:
    assert reciprocal_rank(ranks) == expected


def test_the_mean_is_exact() -> None:
    scores = [Fraction(1), Fraction(1, 3), Fraction(0)]

    assert mean(scores) == Fraction(4, 9)


@pytest.mark.parametrize(
    "ranks",
    [(), (0,), (-1,), (True,), (1.0,), ("1",), "12", None],
)
def test_a_bad_rank_list_is_refused(ranks: object) -> None:
    with pytest.raises(InvalidMetricInput):
        recall_at(ranks, 5)  # pyright: ignore[reportArgumentType]
    with pytest.raises(InvalidMetricInput):
        reciprocal_rank(ranks)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("k", [0, -3, True, 2.0, None])
def test_a_bad_cutoff_is_refused(k: object) -> None:
    with pytest.raises(InvalidMetricInput):
        recall_at((1,), k)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("scores", [[], [0.5], [1], "ab", None])
def test_a_bad_list_of_scores_is_refused(scores: object) -> None:
    with pytest.raises(InvalidMetricInput):
        mean(scores)  # pyright: ignore[reportArgumentType]


@given(ranks=ranks_lists, k=st.integers(min_value=1, max_value=60))
def test_recall_stays_between_zero_and_one_and_grows_with_k(
    ranks: list[int | None], k: int
) -> None:
    recall = recall_at(ranks, k)

    assert Fraction(0) <= recall <= Fraction(1)
    assert recall_at(ranks, k + 1) >= recall


@given(ranks=ranks_lists, k=st.integers(min_value=1, max_value=60))
def test_a_found_chunk_in_the_top_k_shows_in_both_scores(
    ranks: list[int | None], k: int
) -> None:
    # Recall@k is above 0 exactly when the best rank is k or better, which
    # is exactly when the reciprocal rank is at least 1/k.
    assert (recall_at(ranks, k) > 0) == (reciprocal_rank(ranks) >= Fraction(1, k))
