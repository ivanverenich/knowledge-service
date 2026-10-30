"""Combine the ranked Candidates of several branches into one ranked list.

Reciprocal Rank Fusion gives a Candidate `1 / (k + rank)` from every branch
that found it and adds the parts up. Only positions count: a vector distance
and a lexical score are on scales that mean nothing to each other, so fusion
never reads them. A larger `k` flattens the curve, so being found by several
branches counts for more than being first in one.

What `fuse_ranked_candidates` promises:

- **One per Chunk.** A Chunk found by several branches is returned once, with
  one contribution from each branch that found it.
- **Transparent.** Each fused Candidate lists, in branch order, the branch,
  its rank there, and the part it added. `score` is the exact sum of the
  parts, rounded to a float once, so adding the rounded parts can differ
  from it in the last digit.
- **Deterministic.** Fused Candidates are ordered by their exact score,
  highest first, then by Chunk ID, the tie-break every branch uses. Scores
  are compared as fractions, because two different sums can be equal while
  their floats differ in the last digit.
- **Missing branches.** A branch that is absent or found nothing adds
  nothing; the others are fused as usual.
- **Checked input.** A `k` that is not a positive int, a branch that is not
  a `RetrievalBranch`, a branch's Candidates that are not a tuple or a list,
  a Candidate without a `ChunkId`, ranks that are not 1, 2, 3, ..., a Chunk
  listed twice by one branch, or a Chunk two branches describe differently
  raise `InvalidFusionInput` before anything is returned. Messages name the
  branch and the position, never a value from the input.
- **Nothing new is read.** Fusion only reorders Candidates the branches
  already returned, so every fused Candidate passed the permission check of
  the branch that found it.
- **Quiet.** The fields holding Document text are kept out of repr.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.retrieval.candidates import Candidate

# The constant of the original Reciprocal Rank Fusion paper.
DEFAULT_RRF_K = 60


class RetrievalBranch(StrEnum):
    """One way of finding Candidates, in the order contributions are listed."""

    DENSE = "dense"
    LEXICAL = "lexical"


class FusionError(Exception):
    """Ranked Candidates could not be fused."""


class InvalidFusionInput(FusionError):
    """`k` or a branch's Candidates break the contract; nothing was fused."""


@dataclass(frozen=True, slots=True)
class BranchContribution:
    """What one branch added to a fused Candidate: `1 / (k + rank)`."""

    branch: RetrievalBranch
    rank: int
    contribution: float


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    """One eligible Chunk after fusion, where it came from, and why it ranks here.

    `rank` is its 1-based position in the fused result. `contributions` holds
    one part per branch that found the Chunk, in branch order, and `score` is
    their exact sum rounded once.
    The fields holding Document text are kept out of repr, so logging a
    Candidate never shows them.
    """

    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    score: float
    contributions: tuple[BranchContribution, ...]
    external_id: str = field(repr=False)
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_anchor: str | None = field(repr=False)
    text: str = field(repr=False)


@dataclass(slots=True)
class _Found:
    candidate: Candidate
    score: Fraction
    contributions: list[BranchContribution]


def fuse_ranked_candidates(
    ranked: Mapping[RetrievalBranch, Sequence[Candidate]],
    *,
    k: int = DEFAULT_RRF_K,
) -> tuple[FusedCandidate, ...]:
    """Fuse each branch's ranked Candidates by Chunk ID, best fused score first."""
    # bool is an int, so `True` would otherwise pass as a k of 1.
    if type(k) is not int or k < 1:
        raise InvalidFusionInput("k must be a positive int")
    for branch in ranked:
        # A plain "dense" equals RetrievalBranch.DENSE as a key, so check the type.
        if type(branch) is not RetrievalBranch:
            raise InvalidFusionInput("every branch must be a RetrievalBranch")

    found: dict[ChunkId, _Found] = {}
    for branch in RetrievalBranch:
        candidates = ranked.get(branch, ())
        if not isinstance(candidates, tuple | list):
            raise InvalidFusionInput(f"{branch} Candidates must be a tuple or a list")
        seen: set[ChunkId] = set()
        for position, candidate in enumerate(candidates, start=1):
            # Read with getattr, so a None or a stranger in the list is refused
            # with the typed error instead of an AttributeError.
            if type(getattr(candidate, "chunk_id", None)) is not ChunkId:
                raise InvalidFusionInput(
                    f"{branch} has no ChunkId at position {position}"
                )
            rank = getattr(candidate, "rank", None)
            if type(rank) is not int or rank != position:
                raise InvalidFusionInput(
                    f"{branch} has a wrong rank at position {position}"
                )
            if candidate.chunk_id in seen:
                raise InvalidFusionInput(
                    f"{branch} lists a Chunk twice, again at position {position}"
                )
            seen.add(candidate.chunk_id)

            part = Fraction(1, k + rank)
            contribution = BranchContribution(branch, rank, float(part))
            earlier = found.get(candidate.chunk_id)
            if earlier is None:
                found[candidate.chunk_id] = _Found(candidate, part, [contribution])
                continue
            if _provenance(earlier.candidate) != _provenance(candidate):
                raise InvalidFusionInput(
                    f"{branch} describes the Chunk at position {position} "
                    "differently from an earlier branch"
                )
            earlier.score += part
            earlier.contributions.append(contribution)

    ordered = sorted(
        found.values(),
        key=lambda item: (-item.score, item.candidate.chunk_id.value),
    )
    return tuple(_fused(rank, item) for rank, item in enumerate(ordered, start=1))


def _provenance(candidate: Candidate) -> tuple[object, ...]:
    return (
        candidate.chunk_id,
        candidate.document_id,
        candidate.source_id,
        candidate.content_version,
        candidate.ordinal,
        candidate.external_id,
        candidate.title,
        candidate.heading_path,
        candidate.source_anchor,
        candidate.text,
    )


def _fused(rank: int, item: _Found) -> FusedCandidate:
    candidate = item.candidate
    return FusedCandidate(
        rank=rank,
        chunk_id=candidate.chunk_id,
        document_id=candidate.document_id,
        source_id=candidate.source_id,
        content_version=candidate.content_version,
        ordinal=candidate.ordinal,
        score=float(item.score),
        contributions=tuple(item.contributions),
        external_id=candidate.external_id,
        title=candidate.title,
        heading_path=candidate.heading_path,
        source_anchor=candidate.source_anchor,
        text=candidate.text,
    )
