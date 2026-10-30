"""Checks on fusing the ranked Candidates of dense and lexical search."""

import random
from collections.abc import Mapping, Sequence
from dataclasses import replace
from fractions import Fraction
from typing import Any, cast
from uuid import UUID

import pytest

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.fusion import (
    DEFAULT_RRF_K,
    BranchContribution,
    FusedCandidate,
    InvalidFusionInput,
    RetrievalBranch,
    fuse_ranked_candidates,
)
from knowledge_service.retrieval.lexical_search import LexicalCandidate
from knowledge_service.retrieval.vector_search import VectorCandidate

DENSE = RetrievalBranch.DENSE
LEXICAL = RetrievalBranch.LEXICAL
MARKER = "zebramarker"


class _Int(int):
    """An int subclass, which is not a plain int."""


def _chunk(number: int) -> ChunkId:
    return ChunkId(UUID(int=number))


def _vector(number: int, rank: int, text: str = "") -> VectorCandidate:
    return VectorCandidate(
        rank=rank,
        chunk_id=_chunk(number),
        document_id=DocumentId(UUID(int=10_000 + number)),
        source_id=SourceId(UUID(int=1)),
        content_version=ContentVersion(1),
        ordinal=0,
        distance=rank / 100,
        external_id=f"doc-{number}{text}.txt",
        title=f"Title {number}{text}",
        heading_path=f"Heading {number}{text}",
        source_anchor=f"#chunk-{number}{text}",
        text=f"Text of Chunk {number}{text}",
    )


def _lexical(number: int, rank: int, text: str = "") -> LexicalCandidate:
    vector = _vector(number, rank, text)
    return LexicalCandidate(
        rank=rank,
        chunk_id=vector.chunk_id,
        document_id=vector.document_id,
        source_id=vector.source_id,
        content_version=vector.content_version,
        ordinal=vector.ordinal,
        score=1 / rank,
        highlights=(),
        external_id=vector.external_id,
        title=vector.title,
        heading_path=vector.heading_path,
        source_anchor=vector.source_anchor,
        text=vector.text,
    )


def _dense(*numbers: int) -> tuple[VectorCandidate, ...]:
    return tuple(_vector(number, rank) for rank, number in enumerate(numbers, 1))


def _words(*numbers: int) -> tuple[LexicalCandidate, ...]:
    return tuple(_lexical(number, rank) for rank, number in enumerate(numbers, 1))


def _order(fused: Sequence[FusedCandidate]) -> list[int]:
    return [candidate.chunk_id.value.int for candidate in fused]


def _part(rank: int, k: int = DEFAULT_RRF_K) -> float:
    return float(Fraction(1, k + rank))


def test_a_chunk_both_branches_found_is_returned_once_with_both_parts() -> None:
    fused = fuse_ranked_candidates({DENSE: _dense(1, 2, 3), LEXICAL: _words(3, 4)})

    assert _order(fused) == [3, 1, 2, 4]
    assert [candidate.rank for candidate in fused] == [1, 2, 3, 4]
    assert fused[0].contributions == (
        BranchContribution(DENSE, 3, _part(3)),
        BranchContribution(LEXICAL, 1, _part(1)),
    )
    assert fused[0].score == float(Fraction(1, 63) + Fraction(1, 61))
    assert fused[0].score == pytest.approx(
        sum(part.contribution for part in fused[0].contributions)
    )
    assert fused[1].contributions == (BranchContribution(DENSE, 1, _part(1)),)
    assert fused[3].contributions == (BranchContribution(LEXICAL, 2, _part(2)),)


def test_a_fused_candidate_keeps_the_provenance_of_its_chunk() -> None:
    fused = fuse_ranked_candidates({DENSE: _dense(1), LEXICAL: _words(1)})
    source = _vector(1, 1)

    assert fused[0].chunk_id == source.chunk_id
    assert fused[0].document_id == source.document_id
    assert fused[0].source_id == source.source_id
    assert fused[0].content_version == source.content_version
    assert fused[0].ordinal == source.ordinal
    assert fused[0].external_id == source.external_id
    assert fused[0].title == source.title
    assert fused[0].heading_path == source.heading_path
    assert fused[0].source_anchor == source.source_anchor
    assert fused[0].text == source.text


def test_disjoint_lists_interleave_and_equal_scores_go_by_chunk_id() -> None:
    fused = fuse_ranked_candidates({DENSE: _dense(5, 7), LEXICAL: _words(6, 4)})

    assert _order(fused) == [5, 6, 4, 7]
    assert [len(candidate.contributions) for candidate in fused] == [1, 1, 1, 1]
    assert fused[0].score == fused[1].score == _part(1)


@pytest.mark.parametrize(
    "ranked",
    [
        {LEXICAL: _words(2, 1)},
        {DENSE: (), LEXICAL: _words(2, 1)},
    ],
    ids=["absent", "empty"],
)
def test_a_missing_branch_adds_nothing(
    ranked: Mapping[RetrievalBranch, Sequence[Candidate]],
) -> None:
    fused = fuse_ranked_candidates(ranked)

    assert _order(fused) == [2, 1]
    assert fused[0].contributions == (BranchContribution(LEXICAL, 1, _part(1)),)


def test_one_branch_alone_keeps_its_own_order() -> None:
    fused = fuse_ranked_candidates({DENSE: _dense(9, 3, 6)})

    assert _order(fused) == [9, 3, 6]


@pytest.mark.parametrize(
    "ranked",
    [{}, {DENSE: (), LEXICAL: ()}],
    ids=["no-branches", "both-empty"],
)
def test_no_candidates_is_an_empty_result(
    ranked: Mapping[RetrievalBranch, Sequence[Candidate]],
) -> None:
    assert fuse_ranked_candidates(ranked) == ()


def test_the_order_of_branches_in_the_mapping_changes_nothing() -> None:
    forward = fuse_ranked_candidates({DENSE: _dense(1, 2), LEXICAL: _words(2, 3)})
    backward = fuse_ranked_candidates({LEXICAL: _words(2, 3), DENSE: _dense(1, 2)})

    assert forward == backward
    assert [part.branch for part in forward[0].contributions] == [DENSE, LEXICAL]


def test_equal_exact_scores_tie_by_chunk_id_even_when_their_floats_differ() -> None:
    # With k=60, 1/63 + 1/140 equals 1/84 + 1/90 exactly, but as floats the
    # first sum is one step smaller. Chunk 1 has the first pair and the lower
    # Chunk ID, so it must come first.
    dense = [1_000 + rank for rank in range(1, 81)]
    words = [2_000 + rank for rank in range(1, 81)]
    dense[3 - 1], words[80 - 1] = 1, 1
    dense[24 - 1], words[30 - 1] = 2, 2

    fused = fuse_ranked_candidates({DENSE: _dense(*dense), LEXICAL: _words(*words)})

    order = _order(fused)
    assert order.index(1) + 1 == order.index(2)
    assert fused[order.index(1)].score == fused[order.index(2)].score


def test_k_decides_whether_one_top_rank_beats_two_middle_ranks() -> None:
    ranked = {DENSE: _dense(1, 11, 12, 2), LEXICAL: _words(21, 22, 23, 2)}

    assert _order(fuse_ranked_candidates(ranked, k=1))[0] == 1
    assert _order(fuse_ranked_candidates(ranked, k=60))[0] == 2


def test_the_default_k_is_the_papers_60() -> None:
    fused = fuse_ranked_candidates({DENSE: _dense(1)})

    assert DEFAULT_RRF_K == 60
    assert fused[0].score == 1 / 61


@pytest.mark.parametrize("k", [0, -1, True, 1.5, 60.0, _Int(60), "60", None])
def test_a_bad_k_is_refused(k: object) -> None:
    with pytest.raises(InvalidFusionInput):
        fuse_ranked_candidates({DENSE: _dense(1)}, k=cast(int, k))


def test_a_branch_that_is_not_a_retrieval_branch_is_refused() -> None:
    ranked = cast(Mapping[RetrievalBranch, Sequence[Candidate]], {"dense": _dense(1)})

    with pytest.raises(InvalidFusionInput):
        fuse_ranked_candidates(ranked)


@pytest.mark.parametrize(
    "ranks",
    [(2, 3), (1, 1), (1, 3), (2, 1), (0, 1), (True, 2), (1.0, 2), (_Int(1), 2)],
)
def test_ranks_that_do_not_count_from_one_in_order_are_refused(
    ranks: tuple[int, int],
) -> None:
    candidates = (_vector(1, ranks[0]), _vector(2, ranks[1]))

    with pytest.raises(InvalidFusionInput):
        fuse_ranked_candidates({DENSE: candidates})


@pytest.mark.parametrize(
    "candidates",
    [None, "ab", iter(_dense(1)), {_vector(1, 1)}],
    ids=["none", "string", "iterator", "set"],
)
def test_candidates_that_are_not_a_tuple_or_a_list_are_refused(
    candidates: object,
) -> None:
    ranked = cast(Mapping[RetrievalBranch, Sequence[Candidate]], {DENSE: candidates})

    with pytest.raises(InvalidFusionInput):
        fuse_ranked_candidates(ranked)


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        replace(_vector(1, 1), chunk_id=cast(ChunkId, UUID(int=1))),
        replace(_vector(1, 1), chunk_id=cast(ChunkId, DocumentId(UUID(int=1)))),
        replace(_vector(1, 1), chunk_id=cast(ChunkId, [1])),
        replace(_vector(1, 1), chunk_id=cast(ChunkId, None)),
    ],
    ids=["none", "uuid", "document-id", "unhashable", "no-id"],
)
def test_a_candidate_without_a_chunk_id_is_refused(candidate: object) -> None:
    ranked = {DENSE: _dense(1), LEXICAL: (cast(Candidate, candidate),)}

    with pytest.raises(InvalidFusionInput):
        fuse_ranked_candidates(ranked)


def test_a_chunk_one_branch_lists_twice_is_refused() -> None:
    candidates = (_lexical(1, 1), _lexical(2, 2), _lexical(1, 3))

    with pytest.raises(InvalidFusionInput):
        fuse_ranked_candidates({LEXICAL: candidates})


@pytest.mark.parametrize(
    "change",
    [
        {"document_id": DocumentId(UUID(int=99))},
        {"source_id": SourceId(UUID(int=99))},
        {"content_version": ContentVersion(2)},
        {"ordinal": 1},
        {"external_id": "other.txt"},
        {"title": "Other"},
        {"heading_path": None},
        {"source_anchor": None},
        {"text": "Other text"},
    ],
)
def test_a_chunk_two_branches_describe_differently_is_refused(
    change: dict[str, Any],
) -> None:
    lexical = replace(_lexical(1, 1), **change)

    with pytest.raises(InvalidFusionInput):
        fuse_ranked_candidates({DENSE: _dense(1), LEXICAL: (lexical,)})


def test_errors_and_reprs_never_show_document_text() -> None:
    dense = (_vector(1, 1, MARKER), _vector(2, 2, MARKER))
    fused = fuse_ranked_candidates({DENSE: dense})

    assert MARKER not in repr(fused)

    text_as_rank = replace(dense[0], rank=cast(int, dense[0].text))
    failures: list[Mapping[RetrievalBranch, Sequence[Candidate]]] = [
        {DENSE: (*dense, _vector(1, 3, MARKER))},
        {DENSE: dense, LEXICAL: (_lexical(1, 1, MARKER + "x"),)},
        {DENSE: (_vector(1, 2, MARKER),)},
        {DENSE: (text_as_rank,)},
    ]
    for ranked in failures:
        with pytest.raises(InvalidFusionInput) as caught:
            fuse_ranked_candidates(ranked)
        error = caught.value
        shown = [str(error), repr(error.args), *getattr(error, "__notes__", [])]
        assert all(MARKER not in text for text in shown)
        assert all(str(_chunk(1).value) not in text for text in shown)
        assert error.__cause__ is None
        assert error.__context__ is None


def test_a_fused_candidate_meets_the_candidate_contract() -> None:
    fused: Sequence[Candidate] = fuse_ranked_candidates({DENSE: _dense(1)})

    assert fused[0].rank == 1


def test_random_lists_fuse_by_exact_score_then_chunk_id() -> None:
    generator = random.Random(5)  # noqa: S311
    for _ in range(500):
        k = generator.randint(1, 100)
        dense = generator.sample(range(40), generator.randint(0, 25))
        words = generator.sample(range(40), generator.randint(0, 25))
        ranked = {DENSE: _dense(*dense), LEXICAL: _words(*words)}

        fused = fuse_ranked_candidates(ranked, k=k)

        assert sorted(_order(fused)) == sorted(set(dense) | set(words))
        assert [candidate.rank for candidate in fused] == list(range(1, len(fused) + 1))
        keys: list[tuple[Fraction, int]] = []
        for candidate in fused:
            number = candidate.chunk_id.value.int
            expected = [
                BranchContribution(branch, numbers.index(number) + 1, 0.0)
                for branch, numbers in ((DENSE, dense), (LEXICAL, words))
                if number in numbers
            ]
            assert [(p.branch, p.rank) for p in candidate.contributions] == [
                (p.branch, p.rank) for p in expected
            ]
            exact = sum(
                (Fraction(1, k + part.rank) for part in candidate.contributions),
                Fraction(0),
            )
            assert candidate.score == float(exact)
            keys.append((-exact, number))
        assert keys == sorted(keys)
