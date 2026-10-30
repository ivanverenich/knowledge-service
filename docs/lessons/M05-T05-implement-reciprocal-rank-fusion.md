# M05-T05 — Implement Reciprocal Rank Fusion

Source task: [M05-T05](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T05-implement-reciprocal-rank-fusion.md)

## What you will do

The project now has two searches that each return a ranked list of Candidates: vector search finds Chunks by meaning, lexical search by words. This lesson writes the piece that merges the two lists into one, so a Chunk that both searches like rises to the top. The method is **Reciprocal Rank Fusion** (RRF), and it only looks at positions in each list, never at the searches' own scores.

- A new module, `fusion.py`, holds one function: `fuse_ranked_candidates`.
- Each fused Candidate shows which search found it, at what position, and how many points that gave. Nothing about the final order is hidden.
- The constant `k`, which decides how much a top position is worth, is a setting with a default of 60.
- Unit tests cover lists that overlap, lists that share nothing, a missing search, ties, and the same Chunk listed twice.

| The task asks for | Where it happens |
|---|---|
| Fuse ranked dense and lexical lists | Step 2: `fuse_ranked_candidates` |
| By stable Chunk identity | Step 2: Candidates are matched by `chunk_id`, and each Chunk comes back once |
| Configurable k | Step 2: the `k` argument, default `DEFAULT_RRF_K = 60` |
| Transparent score contributions | Step 2: `BranchContribution`, one per search that found the Chunk |
| Overlap, disjoint lists, missing branches, ties, duplicated identities | Step 3: one test or more for each |
| Success, the highest-risk edge, one typed failure | Step 3: the overlap test; the exact-tie test; `InvalidFusionInput` |

This lesson includes coding: Steps 2 and 3 write code. Steps 1, 4, and 5 do not.

Terms used in this lesson:

- **Branch:** one way of searching. Here there are two: dense (vector search, by meaning) and lexical (by words).
- **Fusion:** turning several ranked lists into one. Like combining two judges' rankings in a contest into one final ranking.
- **Reciprocal Rank Fusion:** each branch gives a Chunk `1 / (k + rank)` points, and the points are added up. First place with k=60 gives 1/61, second place 1/62, and so on.
- **Contribution:** the points one branch gave one Chunk.
- **k:** the number added to every rank. A small k makes first place worth far more than fifth. A large k makes the places worth almost the same, so being found by both branches matters most.

## Before you begin

This section explains the decisions behind the code, so the steps make sense when you reach them.

- **What already exists.** Vector search returns `VectorCandidate`s with a distance, and lexical search returns `LexicalCandidate`s with a score. Both meet the `Candidate` contract from M05-T04, so both have `rank`, `chunk_id`, and the same provenance fields.
- **Why ranks, not scores.** A distance of 0.3 and a `ts_rank` of 0.06 mean nothing to each other, and one is better when lower, the other when higher. Positions are the only thing both lists share. RRF uses only positions, so it needs no tuning to make the two scales agree.
- **Why k = 60 by default.** The original RRF paper used 60 and found it worked well across many tests. The lesson keeps it as a setting, because M05-T07 will measure on this project's own Questions.
- **Matching by Chunk ID.** The same Chunk found by both branches has the same `chunk_id`. Fusion adds its points together and returns it once. If the two branches describe that Chunk differently, for example with different text or a different content version, something upstream is broken, so fusion refuses instead of picking one.
- **A missing branch.** If a branch is absent from the input, or found nothing, it adds no points. The other branch is fused as usual, and its order stays the same. Deciding what to do when a branch fails or times out is the next task, M05-T06.
- **Exact ties.** Two Chunks can have exactly the same points. Then the lower Chunk ID comes first, the same rule every branch uses. Points are added as exact fractions, not floats, because floats can break a real tie. With k=60, `1/63 + 1/140` equals `1/84 + 1/90` exactly, but as floats the first sum is one step smaller. With floats, the order of those two Chunks would depend on rounding instead of the tie rule. The float `score` the caller sees is the exact sum, rounded once. Adding up the rounded parts yourself can differ from it in the last digit.
- **Checked input.** Fusion trusts the branches only as far as it must. It raises `InvalidFusionInput` for any of these:
  - a `k` that is not a positive int;
  - a branch name that is not a `RetrievalBranch`;
  - a branch's Candidates that are not a tuple or a list, such as `None`;
  - a Candidate without a `ChunkId`;
  - ranks that are not 1, 2, 3 in order;
  - a Chunk one branch lists twice.

  Counting a duplicate twice would push it up unfairly, so it is refused rather than merged.
- **Permissions.** Fusion reads nothing from the database. It only reorders Candidates the branches already returned, and each branch ran the permission check in its own query. So fusion cannot leak a hidden Chunk, and it never needs the reader.
- **Quiet.** Error messages name the branch and the position, never a value from the input, so no Document text or Chunk ID. The fused Candidate keeps text fields out of its repr, like the other Candidates.
- **What the review found.** An independent review attacked the prototype and found three problems. Each is now fixed, with a test that fails on the old code:
  - The docs promised that `score` equals the sum of the listed parts. But each part is rounded on its own, so the two can differ in the last digit. The promise now says `score` is the exact sum rounded once, and a test checks both.
  - A branch given as `None`, a Candidate that is `None`, or a Chunk ID of the wrong type crashed with a plain Python error. A `DocumentId` used as a Chunk ID even let one Chunk come back twice. Fusion now refuses all of them with `InvalidFusionInput`.
  - The rank error printed the rank itself, so a broken branch that passed text as a rank leaked that text. The message now names only the branch and the position, and the leak test also checks notes and chained errors.

  Some findings are known limits, written in the evidence instead of fixed:
  - A provenance field whose own comparison raises an error would escape untyped. Real Candidates hold only IDs, ints, and strings, so this cannot happen with them.
  - With a huge k, such as 10**17, different exact scores can round to the same float. The shown scores then tie, while the order still follows the exact values.
  - Fusion drops what is special to each branch: the distance, the lexical score, and the highlights. The next task decides which of them to carry along.

  The review also confirmed what held. The caller's mapping order and Python's hash seed never changed a result. The result always held exactly the input Chunks. 100,000 Candidates per branch fused in about a second.

- **Running the tests.** These tests need no database:

  ```bash
  uv run pytest tests/test_reciprocal_rank_fusion.py -q
  ```

- **Files you will touch:**
  - new `src/knowledge_service/retrieval/fusion.py`
  - new `tests/test_reciprocal_rank_fusion.py`
  - `docs/architecture.md`, a new evidence file, and at the end `PROGRESS.md`

No migration and no database change this time.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

This step writes down, before any code, what must always be true and what you expect to see. You will compare your guesses with the real results in Step 4.

**Action:** create `docs/evidence/M05-T05-reciprocal-rank-fusion.md`:

```markdown
# M05-T05 — Reciprocal Rank Fusion evidence

Completed: pending

## Invariant

Fusion returns each Chunk that any branch found exactly once, and no other
Chunk. Its score is the sum of `1 / (k + rank)` over the branches that
found it, and it lists each branch's part in branch order. Fused
Candidates are ordered by exact score, highest first, then by Chunk ID.
Bad input is refused with `InvalidFusionInput` before anything is
returned, and no Document text appears in an error message or a repr.

## Expected failing check

Before the change, `tests/test_reciprocal_rank_fusion.py` fails to import
`knowledge_service.retrieval.fusion`.

## Predictions

Before running anything, write what you expect:

- Dense search ranks Chunk 1 first, and lexical search does not find it.
  Chunk 2 is fourth in both lists. Which comes first with k=60? With k=1?
- One Chunk is first in dense search only, another first in lexical search
  only. What are their scores, and which comes first?
- Lexical search found nothing. What does fusion return?
- Lexical search lists the same Chunk twice. What should fusion do?
- `1/63 + 1/140` and `1/84 + 1/90` are equal. Do Python floats agree?

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file exists, with your own predictions.

### Step 2 — Coding step: write the fusion function

This step builds the function that merges the ranked lists of both searches into one.

**In plain words:** This file is the fusion itself. It first checks that the setting and every list make sense. Then it walks each search's list in a fixed order, gives every Chunk points for its position, and adds the points up when both searches found the same Chunk. Finally it sorts the Chunks by points, breaks exact ties by Chunk ID, and returns them with a record of where each point came from.

| Part | What it does |
|---|---|
| `DEFAULT_RRF_K` | 60, the paper's constant |
| `RetrievalBranch` | the two branch names, `dense` and `lexical`, in the order contributions are listed |
| `InvalidFusionInput` | the one error fusion raises, a `FusionError` |
| `BranchContribution` | one branch's part: the branch, its rank there, and `1 / (k + rank)` |
| `FusedCandidate` | the provenance fields, the fused `rank`, `score`, and `contributions`; it meets the `Candidate` contract |
| `fuse_ranked_candidates` | checks the input, adds the parts as fractions, sorts, and numbers the result |
| `_provenance` | the fields two branches must agree on for the same Chunk |

A few lines need a word of explanation:

- `type(k) is not int` rejects `True`, because `bool` is a kind of `int` in Python. The project uses this check everywhere a number must be a real int.
- `type(branch) is not RetrievalBranch` rejects the plain string `"dense"`. A `StrEnum` member equals its string, so without the check, a plain string key would quietly work as a branch.
- The loop goes over `RetrievalBranch`, not over the mapping. So the contributions come out in the same order whatever order the caller built the mapping in.
- `isinstance(candidates, tuple | list)` refuses `None` and other shapes before the loop. A missing branch is written by leaving it out, or as an empty tuple.
- `getattr(candidate, "chunk_id", None)` reads the ID without crashing on a `None` Candidate. The `type(...) is not ChunkId` check then refuses a `None`, a bare UUID, or a `DocumentId` in its place.
- `rank != position` checks that ranks are 1, 2, 3 in list order. RRF trusts the rank, so a list with a gap or a repeat would give wrong points.
- `Fraction(1, k + rank)` is the exact part. The sort uses the exact sum, and `float()` is applied only when building the result.

**Action:** create `src/knowledge_service/retrieval/fusion.py`:

```python
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
```

**Check:**

```bash
uv run ruff check src/knowledge_service/retrieval/fusion.py
uv run pyright src/knowledge_service/retrieval/fusion.py
```

Expected: no issues, and `0 errors`.

### Step 3 — Coding step: test fusion at its interface

This step proves the fusion keeps every promise, using small hand-made lists instead of a database.

**In plain words:** These tests build little ranked lists from both searches, where each Chunk is just a number, and check what fusion returns. They cover lists that share Chunks, lists that share nothing, a missing search, exact ties, a changed `k`, and every kind of bad input. A last test fuses 500 random pairs of lists and checks the order against exact arithmetic.

| Test | What it proves |
|---|---|
| `test_a_chunk_both_branches_found_is_returned_once_with_both_parts` | Overlap: one result per Chunk, both parts listed, score is their exact sum rounded once |
| `test_a_fused_candidate_keeps_the_provenance_of_its_chunk` | Every provenance field comes through unchanged |
| `test_disjoint_lists_interleave_and_equal_scores_go_by_chunk_id` | Disjoint lists: equal places tie, and the lower Chunk ID wins |
| `test_a_missing_branch_adds_nothing` | An absent or empty branch adds no points |
| `test_one_branch_alone_keeps_its_own_order` | With one branch, fusion keeps its order |
| `test_no_candidates_is_an_empty_result` | Nothing in, nothing out |
| `test_the_order_of_branches_in_the_mapping_changes_nothing` | The caller's mapping order does not change the result |
| `test_equal_exact_scores_tie_by_chunk_id_even_when_their_floats_differ` | The highest-risk edge: a real tie that floats would break |
| `test_k_decides_whether_one_top_rank_beats_two_middle_ranks` | `k` is really used and changes the order |
| `test_the_default_k_is_the_papers_60` | The default is 60 |
| `test_a_bad_k_is_refused` | 0, -1, `True`, 1.5, 60.0, an int subclass, `"60"`, and `None` raise `InvalidFusionInput` |
| `test_a_branch_that_is_not_a_retrieval_branch_is_refused` | A plain `"dense"` key is refused |
| `test_candidates_that_are_not_a_tuple_or_a_list_are_refused` | `None`, a string, an iterator, and a set are refused |
| `test_a_candidate_without_a_chunk_id_is_refused` | `None`, a bare UUID, a `DocumentId`, a list, and no ID are refused |
| `test_ranks_that_do_not_count_from_one_in_order_are_refused` | Gaps, repeats, wrong order, 0, `True`, 1.0, and an int subclass are refused |
| `test_a_chunk_one_branch_lists_twice_is_refused` | Duplicated identity in one branch is refused |
| `test_a_chunk_two_branches_describe_differently_is_refused` | Every provenance field must agree across branches |
| `test_errors_and_reprs_never_show_document_text` | A planted marker never shows in a repr, an error message, its notes, or a chained error |
| `test_a_fused_candidate_meets_the_candidate_contract` | Pyright accepts a fused Candidate as a `Candidate` |
| `test_random_lists_fuse_by_exact_score_then_chunk_id` | 500 random cases: right Chunks, right parts, exact order |

`_Int` is a subclass of `int`, used to check that fusion wants a plain int. The helpers make Candidates from plain numbers. `_chunk(5)` is the Chunk ID whose UUID is the number 5, so the tie rule is easy to read: Chunk 5 comes before Chunk 7. `_dense(1, 2, 3)` gives a dense list with Chunk 1 at rank 1, Chunk 2 at rank 2, and Chunk 3 at rank 3. `_words` does the same for lexical search.

The tie test needs lists 80 long. It puts Chunk 1 at dense rank 3 and lexical rank 80, and Chunk 2 at dense rank 24 and lexical rank 30. Those two sums are equal exactly, and every other place holds a filler Chunk.

**Action:** create `tests/test_reciprocal_rank_fusion.py`:

```python
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
```

**Check:**

```bash
uv run pytest tests/test_reciprocal_rank_fusion.py -q
```

Expected: `51 passed`.

**Deliberate break.** Make fusion add floats instead of fractions. In `fusion.py`, change:

```python
            part = Fraction(1, k + rank)
```

to:

```python
            part = 1 / (k + rank)
```

Run the tests again. Expected: 2 failed, `test_equal_exact_scores_tie_by_chunk_id_even_when_their_floats_differ` and `test_random_lists_fuse_by_exact_score_then_chunk_id`. Pyright also complains, because a float is not a `Fraction`. Undo the change and run the tests again: all pass.

### Step 4 — No coding in this step: watch k change the order

This step shows the contributions for yourself, and how `k` decides between "first in one list" and "found by both".

**Action:** from the project root, run:

```bash
uv run python - <<'PY'
import sys

sys.path.insert(0, "tests")
from test_reciprocal_rank_fusion import DENSE, LEXICAL, _dense, _words

from knowledge_service.retrieval.fusion import fuse_ranked_candidates

ranked = {DENSE: _dense(1, 11, 12, 2), LEXICAL: _words(21, 22, 23, 2)}
for k in (60, 1):
    print(f"k={k}")
    for fused in fuse_ranked_candidates(ranked, k=k)[:3]:
        parts = ", ".join(
            f"{part.branch} #{part.rank} +{part.contribution:.4f}"
            for part in fused.contributions
        )
        print(f"  {fused.rank}. Chunk {fused.chunk_id.value.int}: {fused.score:.4f} = {parts}")
PY
```

The reference run printed:

```text
k=60
  1. Chunk 2: 0.0312 = dense #4 +0.0156, lexical #4 +0.0156
  2. Chunk 1: 0.0164 = dense #1 +0.0164
  3. Chunk 21: 0.0164 = lexical #1 +0.0164
k=1
  1. Chunk 1: 0.5000 = dense #1 +0.5000
  2. Chunk 21: 0.5000 = lexical #1 +0.5000
  3. Chunk 2: 0.4000 = dense #4 +0.2000, lexical #4 +0.2000
```

How to read it:

- With k=60, Chunk 2 wins. Fourth place in both lists gives 2/64, more than first place in one list, 1/61.
- With k=1, Chunk 1 wins. First place is worth 1/2, while fourth place twice gives only 2/5.
- Chunks 1 and 21 tie in both runs, and the lower Chunk ID, 1, comes first.
- Each line shows where every point came from. That is the "transparent score contributions" the task asks for.

Write in the evidence which of your Step 1 predictions were right.

**Check:** your output matches the reference, and your evidence names one prediction you got wrong, or says that none were.

### Step 5 — No coding in this step: gates, docs, evidence, and progress

This step proves everything still works, updates the one architecture line the change made out of date, and records the result.

**Action 1:** in `docs/architecture.md`, change the `retrieval` row of the "Code layout" table to:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `MetadataCondition` |
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_reciprocal_rank_fusion.py -q
make format-check
make lint
make typecheck
make test-integration
make check
make docs-check
```

Expected: all pass. The reference run gave `make check` 1325 passed, 3 deselected, and `make test-integration` 284 passed. Run `make check` with the database URL set, or its database tests are skipped instead of run:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make check
```

**Action 3:** complete the evidence file:

- **Design:** the function and its types, why fusion uses ranks and not scores, why `k` is 60 by default, exact fractions for ties, and what the review changed.
- **Verification:** the commands and counts, a table of the tests with what each proves, the Step 4 output, the deliberate break from Step 3, and the known limits from "Before you begin".
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M05-T05 row to `docs/curriculum/PROGRESS.md`, set the next task to M05-T06, and commit with `m05-t05: implement reciprocal rank fusion`.

## Reflection questions

1. What does `fuse_ranked_candidates` hide from the code that calls it? List what a caller never has to think about, such as how ties are broken or what happens when one search returns nothing.
2. Someone later speeds fusion up by adding floats instead of fractions. Which promise breaks, how often would a reader notice, and which test catches it?
3. Someone else "simplifies" fusion by merging a Chunk that one search lists twice, instead of refusing it. Why would that be unfair to the other Chunks?
4. How would you find out whether k = 60 is right for this project? Think about what you would measure on real Questions, and what result would make you pick a different k.
5. Fusion only trusts positions. Think of a case where one search is clearly better for a Question, such as an error code like `E-1042`. How could RRF give a worse order than that search alone?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before running anything.
- [ ] `fusion.py` defines `fuse_ranked_candidates`, `FusedCandidate`, `BranchContribution`, `RetrievalBranch`, and `InvalidFusionInput`.
- [ ] Each Chunk comes back once, with one contribution per search that found it, and a score equal to their exact sum.
- [ ] `k` is a setting with a default of 60, and a bad `k` is refused.
- [ ] Bad input, such as `None` or a wrong ID type, raises `InvalidFusionInput`, never a plain Python error.
- [ ] Exact ties go by Chunk ID, even when floats would disagree.
- [ ] The tests cover overlap, disjoint lists, a missing branch, ties, and duplicated identities, and all pass.
- [ ] You ran the deliberate break and saw it fail, then undid it.
- [ ] You ran the Step 4 command and compared it with your predictions.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The architecture line, evidence, PROGRESS row, and commit are done.

When you have written the code, share it or any error output, and I will review it against this checklist.
