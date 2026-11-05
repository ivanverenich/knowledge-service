# M05-T08 — Measure managed multilingual embeddings

Source task: [M05-T08](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T08-measure-managed-multilingual-embeddings.md)

## What you will do

In M05-T07 you built a fixed set of 16 questions and ran it with word search only. In this lesson you add search by meaning to that run and turn the raw ranks into two standard scores. You run the questions three ways (words only, meaning only, both merged), score each language direction separately, and record what the numbers prove wrong about your expectations.

- A small module, `ranking_metrics.py`, computes **Recall@k** and **MRR** from ranks.
- `evaluation_dataset.py` gains `compare_branches`. It stores the corpus once, gives every passage a vector from one embedding model, then runs every question three times.
- The report gets the scores per slice and a short configuration block: which searches ran, which model, how many results were kept.
- The script gets a `--managed-embeddings` option that uses the OpenAI embedding model.
- You run it once with a real API key and write the result, the misses, and at least one falsified assumption into the evidence file.

| The task asks for | Where it happens |
|---|---|
| Run the seed dataset through the managed embeddings path | Step 4: `compare_branches` embeds the corpus with the model; Step 6: `--managed-embeddings` uses OpenAI; Step 8: the real run |
| Slice Recall@k and MRR by language direction | Step 2: the scores; Step 5: one row per direction and per query class |
| Deterministic tests for success, the highest-risk edge, one typed failure or denial | Step 7: three runs on a fake model; the nearest passage is restricted and still never returned; a failing model raises `CorpusNotEmbedded` |
| A versioned report with configuration, misses, and a falsified assumption | Step 5: the configuration block; Step 8: the report goes into the evidence file |
| Authorization preserved, no sensitive text in telemetry | Step 7: the permission test and the checks on the report, errors, and logs |

This lesson includes coding: Steps 2 to 7 write Python. Steps 1, 8, and 9 do not involve code. Step 8 needs your OpenAI API key and costs less than one cent.

Terms used in this lesson:

- **Embedding:** a list of numbers that stands for the meaning of a text. Texts with similar meaning get nearby lists, even in different languages. "Annual leave" and "щорічна відпустка" should land close together.
- **Managed model:** a model run by a provider, here OpenAI, that you call over the network. You do not control its version or its servers.
- **Embedding Space:** the project's record of one model (provider, name, number of dimensions). Vectors from different spaces are never compared. Search reads only the one space marked current.
- **Dense search:** search by meaning. It compares the question's vector with every passage's vector. **Lexical search** is search by words, from M05-T01 to M05-T04.
- **Recall@k:** the share of a question's right passages that appear in the top k results. One right passage at rank 3 gives Recall@1 = 0 and Recall@5 = 1.
- **Reciprocal rank:** 1 divided by the rank of the first right passage, or 0 when none was found. Rank 1 gives 1, rank 4 gives 0.25. **MRR** (mean reciprocal rank) is its average over many questions. It rewards finding the answer near the top, not only somewhere in the list.
- **Slice:** a group of questions with the same label, such as `uk->en` (a Ukrainian question, an English answer). One total can hide a weak language, so every slice gets its own scores.
- **Falsified assumption:** something you believed before the run that the numbers show to be wrong. Writing it down is how you learn from a measurement instead of just reading it.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **What already exists.** `run_dataset` from M05-T07 stores the corpus in an empty database and asks every question as one ordinary reader, `READER`. It records the rank of each right passage, or `None` when it is missing. `publish_document_embeddings` from M04-T05 gives every passage of one document a vector, all at once or not at all. `dense_branch` and `lexical_branch` from M05-T06 are the two searches `retrieve_hybrid` can run and merge.
- **Why the corpus is stored and embedded only once.** The run needs an empty database, so it cannot simply be called three times. `compare_branches` stores and embeds once, then asks the questions three times. That also means the three runs search exactly the same vectors, so any difference comes from the search method alone.
- **Why restricted passages get vectors too.** In the real service every document is embedded, whoever may read it, and the permission check happens at search time. A test of that check only means something if a restricted passage could be found. So the restricted page is embedded like the others. Its text is sent to OpenAI; the seed corpus is made up, so nothing private leaves your machine.
- **The highest-risk edge.** Search by meaning can return a passage that shares no word with the question. The restricted page is about annual leave, like a public one, so a meaning search for leave days ranks it high. The permission check inside the search's SQL must still drop it. The test in Step 7 makes the restricted passage the single best match on purpose, then checks that the reader never gets it.
- **Why scores are exact fractions.** Recall and MRR are averages of small fractions such as 1/3 and 1/4. Python's `Fraction` keeps them exact, so two runs with the same ranks always print the same numbers. The report rounds them to three digits only when it prints them.
- **Which ranks are scored.** Each run keeps the top 10 results, so ranks only go up to 10. Recall is shown at 1, 3, 5, and 10; a cut-off above the run's limit would be meaningless and is left out. In the merged run, a rank is the passage's place in the merged list.
- **What can fail, and what happens then.** A model that fails while the corpus is embedded stops the run with `CorpusNotEmbedded` before any question is asked, so a half-embedded corpus is never scored. A search that cannot answer one question already raises `CaseUnanswered`. Any other error from a question now raises `CaseFailed`. Every one of these names only the case ID and the error type, never the question or document text.
- **Known limits.** Questions are embedded again for the merged run, so if the provider returns slightly different numbers, the dense ranks inside the merged run can differ a little from the meaning-only run. One failed question in the meaning-only run stops the whole comparison, after the corpus was already embedded. The summary table at the top has no "degraded" column; the full reports below it do. Request IDs are fixed per case, so every run sends the same IDs to OpenAI. A run that fails after storing the corpus leaves the database used, so start the next one on a fresh database.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

This step writes down what the change must guarantee and what you expect the real run to show, before any code exists. Predictions written after the run are not predictions.

Create `docs/evidence/M05-T08-managed-multilingual-embeddings.md` with this skeleton, then answer the prediction questions in your own words:

```markdown
# M05-T08 — Managed multilingual embeddings evidence

## Invariant

`compare_branches` stores and embeds the bilingual seed corpus once, in an
empty database, with one embedding model. It then asks every case as one
ordinary reader three ways: lexical only, dense only, and both fused. No
Chunk from `restricted/` is ever returned, even when it is the nearest
vector. The report gives Recall@k and MRR per language direction and query
class, names the configuration, and holds no Question or Document text.

## Expected failing check

Before the change, `uv run pytest tests/test_ranking_metrics.py` fails to
import `knowledge_service.retrieval.ranking_metrics`.

## Predictions

- Which slice will gain the most from dense search, and why?
- Will dense search find the bare identifier question "VPN-4031" at rank 1?
- Will dense search fix the morphology misses, such as "Хто оплачує
  лікарняні?" against "Оплата лікарняного"?
- Will merging both searches beat the better single search on every slice?
- If you ran the same thing twice on fresh databases, would the report be
  byte-identical?

## Design

## Verification

## Reflection
```

Check: the file exists and every prediction has an answer and a one-line reason.

### Step 2 — Coding step: compute Recall@k and MRR

This step writes the two scores as a small module of pure functions, with no database and no model.

**In plain words:** Given the ranks of a question's right passages, these functions say what share of them landed in the top k, and how high the first one landed. A third function averages one score over many questions. They refuse nonsense, such as a rank of 0 or an empty list, with one clear error.

| Function | Input | Returns |
|---|---|---|
| `recall_at(ranks, k)` | one rank per right passage, `None` for a miss; a cut-off `k` | the share ranked at `k` or better |
| `reciprocal_rank(ranks)` | the same ranks | 1 / the best rank, or 0 |
| `mean(scores)` | one score per question | their exact average |
| `InvalidMetricInput` | raised for an empty list, a rank below 1, a `bool`, a float, a string, or a bad `k` | |

Create `src/knowledge_service/retrieval/ranking_metrics.py`:

```python
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
```

Notes on the less obvious lines:

- `type(k) is not int` refuses `True`, because `bool` is a kind of `int` in Python and would pass as 1.
- `cast(object, ranks)` tells the type checker the check is deliberate. A caller can pass anything at run time, even though the annotation says `Sequence`.
- A string is a sequence of characters, so it is refused first. Otherwise `"12"` would look like two ranks.

Now create `tests/test_ranking_metrics.py`. The fixed examples pin the definitions. The two property tests use Hypothesis to try hundreds of random rank lists. They check that Recall stays between 0 and 1, never drops when k grows, and agrees with the reciprocal rank about whether something was found in the top k.

```python
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
```

Check: `uv run pytest tests/test_ranking_metrics.py -q` gives `32 passed`.

### Step 3 — Coding step: prepare the dataset module

This step adds the names the next two steps use: imports, a few constants, two new errors, and room in the report for the model and the scores. Nothing runs differently yet.

**In plain words:** The dataset module needs to know about embedding models, the stored vectors, and the new scores. This step brings those in and adds two ways for a run to fail cleanly: the corpus could not be embedded, or a question raised an unexpected error. The report learns to remember which model it used, and a new record holds the scores of one slice.

| Addition | What it is for |
|---|---|
| `RECALL_CUTOFFS` | the k values Recall is shown at |
| `EMBEDDING_CONCURRENCY`, `EMBEDDING_TIMEOUT_SECONDS` | how many requests embed the corpus at once, and how long each may take |
| `SPACE_ID` | a fixed ID for the run's Embedding Space, like the fixed Source IDs |
| `CaseFailed` | a question raised an error no search should raise |
| `CorpusNotEmbedded` | at least one document got no vectors, so no question ran |
| `DatasetReport.embedding` | the model behind the dense search, or `None` |
| `SliceScore` | Recall at each cut-off and MRR for one slice |

All edits are in `src/knowledge_service/retrieval/evaluation_dataset.py`. Make them in order; each "Find" text appears exactly once.

First, the module docstring, so it describes the new behavior:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
  missing. A Question no branch can answer raises `CaseUnanswered`.
- **Eligible.** A Candidate from `restricted/` raises
  `IneligibleChunkRetrieved` and stops the run, however relevant it is.
- **Quiet.** Reports and errors name cases by ID and give ranks only; they
  never hold a Question or Document text.
"""
```

Replace with:

```python
  missing. A Question no branch can answer raises `CaseUnanswered`.
- **Compared with embeddings.** `compare_branches` also embeds every
  Chunk with one embedding model, then runs every case three times: words
  only, meaning only, and both fused. A Chunk that cannot be embedded
  raises `CorpusNotEmbedded` before any case runs. Every Chunk, restricted
  ones included, is sent to the embedding model's provider.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
  MRR for every language direction and query class (see
  `retrieval.ranking_metrics`).
- **Eligible.** A Candidate from `restricted/` raises
  `IneligibleChunkRetrieved` and stops the run, however relevant it is.
- **Typed.** Any error a case raises becomes `CaseFailed`, naming the case
  and the error types only.
- **Quiet.** Reports and errors name cases by ID and give ranks only; they
  never hold a Question or Document text, not even in a linked error.
"""
```

Then the imports. `Fraction` holds exact scores:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
from enum import StrEnum
```

Replace with:

```python
from enum import StrEnum
from fractions import Fraction
```

The embedding model, its publication, and the Embedding Space ID:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
from knowledge_service.identifiers import SourceId, UserId
```

Replace with:

```python
from knowledge_service.embeddings.port import EmbeddingModel, EmbeddingModelIdentity
from knowledge_service.embeddings.publication import (
    EmbeddingPublicationStatus,
    publish_document_embeddings,
)
from knowledge_service.identifiers import EmbeddingSpaceId, SourceId, UserId
```

The stored documents and spaces, the dense search, and the new scores:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
from knowledge_service.persistence.schema import sources
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    BranchSearch,
    HybridRetrievalUnavailable,
    lexical_branch,
    retrieve_hybrid,
)
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
```

Replace with:

```python
from knowledge_service.persistence.documents import load_available_documents
from knowledge_service.persistence.embeddings import insert_embedding_space
from knowledge_service.persistence.schema import embedding_spaces, sources
from knowledge_service.retrieval.fusion import DEFAULT_RRF_K, RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    BranchSearch,
    HybridRetrieval,
    HybridRetrievalUnavailable,
    dense_branch,
    lexical_branch,
    retrieve_hybrid,
)
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.ranking_metrics import (
    mean,
    recall_at,
    reciprocal_rank,
)
```

The constants:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
PAGE_SIZE = 100
```

Replace with:

```python
PAGE_SIZE = 100
# Recall is reported at these cut-offs, as far as the run's limit allows.
RECALL_CUTOFFS = (1, 3, 5, 10)
EMBEDDING_CONCURRENCY = 2
EMBEDDING_TIMEOUT_SECONDS = 60.0
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
_REQUEST_IDS = UUID(int=0x5EED_0004)
```

Replace with:

```python
_REQUEST_IDS = UUID(int=0x5EED_0004)
SPACE_ID = EmbeddingSpaceId(UUID(int=0x5EED_0005))
```

The two new errors:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
class CaseUnanswered(DatasetError):
    """No branch answered one case, so the run cannot score it."""
```

Replace with:

```python
class CaseUnanswered(DatasetError):
    """No branch answered one case, so the run cannot score it."""


class CaseFailed(DatasetError):
    """A case raised an error no branch should raise; the run stopped."""


class CorpusNotEmbedded(DatasetError):
    """At least one Document of the corpus has no vectors, so no case ran."""
```

The report's new field and the slice score. `embedding` has a default, so every existing `DatasetReport(...)` in the tests still works:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    branches: tuple[RetrievalBranch, ...]
    outcomes: tuple[CaseOutcome, ...]
```

Replace with:

```python
    branches: tuple[RetrievalBranch, ...]
    outcomes: tuple[CaseOutcome, ...]
    # The model behind the dense branch; None when the run had no dense branch.
    embedding: EmbeddingModelIdentity | None = None


@dataclass(frozen=True, slots=True)
class SliceScore:
    """Recall@k and MRR over the cases of one slice.

    `recall` pairs each cut-off with the mean Recall at it. A slice with no
    cases has no scores: `recall` is empty and `mrr` is None.
    """

    name: str
    cases: int
    found: int
    degraded: int
    recall: tuple[tuple[int, Fraction], ...]
    mrr: Fraction | None
```

Check: `uv run pytest tests/test_evaluation_dataset.py -q` still gives `63 passed`. `make lint` and `make typecheck` complain about unused imports until Steps 4 and 5 use them; that is expected for now.

### Step 4 — Coding step: embed the corpus once and run three ways

This step writes `compare_branches`, the function the script will call, and the steps it needs.

**In plain words:** The comparison first checks its settings, then stores the corpus in an empty database and creates one Embedding Space for the model. Next it gives every document's passages their vectors, restricted ones included. Only then does it ask every question three times: with word search, with meaning search, and with both merged. Any failure on the way stops the run with an error that names a type, never a text.

| Piece | What it does |
|---|---|
| `compare_branches` | checks the limit and the model, verifies the corpus, stores, embeds, and returns three reports in a fixed order |
| `_ingest(..., embedding=...)` | with a model, also refuses a database that already holds an Embedding Space, then creates the run's space as current |
| `_embed_corpus` | calls `publish_document_embeddings` for every stored document; anything but `published` raises `CorpusNotEmbedded` |
| `_run_cases` | asks every case with one set of searches and builds one report |
| `_run_case` | now turns any unexpected error into `CaseFailed` |
| `_error_names` | names an error by its type, or a group of errors by the types inside it |
| `_identity` | reads the model's identity once, before anything is written |

Replace the end of `run_dataset` and add `compare_branches` after it. `run_dataset` keeps its behavior; it now shares `_run_cases` with the comparison:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    await _ingest(runtime, dataset, corpus, observed_at)
    outcomes = [await _run_case(case, searches, limit) for case in dataset.cases]
    return DatasetReport(
        name=dataset.name,
        version=dataset.version,
        limit=limit,
        branches=tuple(branch for branch in RetrievalBranch if branch in searches),
        outcomes=tuple(outcomes),
    )
```

Replace with:

```python
    await _ingest(runtime, dataset, corpus, observed_at, embedding=None)
    return await _run_cases(dataset, searches, limit, embedding=None)


async def compare_branches(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: EmbeddingModel,
    limit: int = DEFAULT_LIMIT,
) -> tuple[DatasetReport, ...]:
    """Ingest and embed the corpus once, then run every case three ways.

    Returns three reports in a fixed order: lexical only, dense only, and
    both fused. Every Chunk, restricted ones included, is embedded with
    `model` into one new current Embedding Space, because search must be
    able to rank a restricted Chunk for the permission check to matter.
    Raises like `run_dataset`; `DatasetRunRefused` also when the database
    already holds an Embedding Space or `model` has no valid identity, and
    `CorpusNotEmbedded` when any Document cannot be embedded. Questions are
    embedded again in the fused run, so its dense ranks can differ slightly
    from the dense-only run when the provider is not exactly repeatable.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    identity = _identity(model)
    await verify_corpus(dataset, corpus)
    await _ingest(runtime, dataset, corpus, observed_at, embedding=identity)
    await _embed_corpus(runtime, model, observed_at)
    lexical = lexical_branch(runtime)
    dense = dense_branch(runtime, model)
    setups: tuple[Mapping[RetrievalBranch, BranchSearch], ...] = (
        {RetrievalBranch.LEXICAL: lexical},
        {RetrievalBranch.DENSE: dense},
        {RetrievalBranch.DENSE: dense, RetrievalBranch.LEXICAL: lexical},
    )
    return tuple(
        [
            await _run_cases(dataset, searches, limit, embedding=identity)
            for searches in setups
        ]
    )
```

Give `_ingest` the model's identity:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    corpus: Path,
    observed_at: datetime,
) -> None:
```

Replace with:

```python
    corpus: Path,
    observed_at: datetime,
    *,
    embedding: EmbeddingModelIdentity | None,
) -> None:
```

Inside `_ingest`, right after the check for existing Sources, refuse an existing space and create the run's own. This happens in the same locked transaction, so two runs started together cannot both pass the check:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
            raise DatasetRunRefused("the dataset runs only on an empty database")
        for folder, source_id in SOURCE_IDS.items():
```

Replace with:

```python
            raise DatasetRunRefused("the dataset runs only on an empty database")
        # Another space would hold vectors this run did not make, or be the
        # current one and turn every dense search away.
        if embedding is not None and await connection.scalar(
            sa.select(sa.exists().select_from(embedding_spaces))
        ):
            raise DatasetRunRefused("the dataset runs only on an empty database")
        if embedding is not None:
            await insert_embedding_space(
                connection,
                space_id=SPACE_ID,
                identity=embedding,
                is_current=True,
                created_at=observed_at,
            )
        for folder, source_id in SOURCE_IDS.items():
```

Add `_embed_corpus` and `_run_cases` just before `_run_case`. The error is raised after the `except` block ends, not inside it. Python links an error raised inside `except` to the one being handled, and a traceback prints both. The model's own error could quote a passage:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
async def _run_case(
```

Replace with:

```python
async def _embed_corpus(
    runtime: DatabaseRuntime, model: EmbeddingModel, observed_at: datetime
) -> None:
    async with runtime.transaction() as connection:
        stored = [
            document
            for source_id in SOURCE_IDS.values()
            for document in await load_available_documents(
                connection, source_id=source_id
            )
        ]
    for document in stored:
        failure: str
        try:
            publication = await publish_document_embeddings(
                runtime,
                model,
                document_id=document.document_id,
                concurrency=EMBEDDING_CONCURRENCY,
                request_id=uuid5(_REQUEST_IDS, f"embed {document.document_id.value}"),
                timeout_seconds=EMBEDDING_TIMEOUT_SECONDS,
                now=observed_at,
            )
            failure = str(publication.status)
        except Exception as error:
            # Any error, not only an EmbeddingError: the model is outside
            # code. Its type only, because its message may quote Chunk text.
            failure = _error_names(error)
        # Raised outside the `except`, so no linked error carries the text.
        if failure != EmbeddingPublicationStatus.PUBLISHED:
            raise CorpusNotEmbedded(f"the corpus could not be embedded: {failure}")


async def _run_cases(
    dataset: EvaluationDataset,
    searches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
    *,
    embedding: EmbeddingModelIdentity | None,
) -> DatasetReport:
    outcomes = [await _run_case(case, searches, limit) for case in dataset.cases]
    return DatasetReport(
        name=dataset.name,
        version=dataset.version,
        limit=limit,
        branches=tuple(branch for branch in RetrievalBranch if branch in searches),
        outcomes=tuple(outcomes),
        embedding=embedding if RetrievalBranch.DENSE in searches else None,
    )


async def _run_case(
```

Now `_run_case`. The search result starts as `None`, so the code after the `try` can tell whether the search succeeded:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
) -> CaseOutcome:
    try:
```

Replace with:

```python
) -> CaseOutcome:
    result: HybridRetrieval | None = None
    unexpected = "no result"
    try:
```

Catch any other error the search raises. `retrieve_hybrid` runs the searches in a task group, so a bug in a search arrives as an `ExceptionGroup`, whose message can contain the question:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
        ) from None
    ranks: dict[tuple[str, str | None], int] = {}
```

Replace with:

```python
        ) from None
    except Exception as error:
        # A bug in a branch arrives in an ExceptionGroup whose message may
        # repeat the Question. Only the error types leave this function.
        unexpected = _error_names(error)
    # Raised outside the `except`, so no linked error carries the Question.
    if result is None:
        raise CaseFailed(f"case {case.case_id}: the search raised {unexpected}")
    ranks: dict[tuple[str, str | None], int] = {}
```

Finally, the two helpers, just before `_unique_keys`:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
```

Replace with:

```python
def _error_names(error: BaseException) -> str:
    """Name an error by its type; a group by the types inside it."""
    if isinstance(error, BaseExceptionGroup):
        inner = cast(BaseExceptionGroup[BaseException], error).exceptions
        return ", ".join(sorted({_error_names(item) for item in inner}))
    return type(error).__name__


def _identity(model: EmbeddingModel) -> EmbeddingModelIdentity:
    # Read once here, so a model that cannot name itself is refused before
    # anything is written.
    try:
        identity = model.identity
    except Exception:
        identity = None
    if type(identity) is not EmbeddingModelIdentity:
        raise DatasetRunRefused("the model must have an EmbeddingModelIdentity")
    return identity


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
```

Check: `uv run pytest tests/test_evaluation_dataset.py -q` still gives `63 passed`. `make typecheck` now reports exactly four unused imports, `DEFAULT_RRF_K`, `mean`, `recall_at`, and `reciprocal_rank`, which Step 5 uses.

### Step 5 — Coding step: score the slices and print the configuration

This step turns the ranks into scores per slice and adds them to the report, with a configuration block at the top.

**In plain words:** For every slice (all questions, each language direction, each kind of question), the report now shows how many questions there are, how many found something, Recall at each cut-off, MRR, and how many runs were degraded. A comparison page puts the three runs side by side first, then shows each full report. Every page starts by saying which searches ran, which model, and how many results each question kept.

| Function | What it does |
|---|---|
| `score_slices(report)` | one `SliceScore` for `all`, each direction, and each query class |
| `render_comparison(reports)` | the side-by-side table of Recall and MRR, then every full report |
| `render_report(report)` | as before, plus the configuration block and the score columns |
| `_configuration` | the searches, the limit, the model, and the merge setting, as bullet lines |
| `_score` | the scores of one slice; a slice with no questions gets `n/a` |

Add `score_slices` and `render_comparison` just before `render_report`:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
def render_report(report: DatasetReport) -> str:
```

Replace with:

```python
def score_slices(report: DatasetReport) -> tuple[SliceScore, ...]:
    """Score every case together, then each direction, then each query class."""
    cutoffs = tuple(k for k in RECALL_CUTOFFS if k <= report.limit)
    groups: list[tuple[str, list[CaseOutcome]]] = [("all", list(report.outcomes))]
    groups += [
        (direction, [o for o in report.outcomes if o.direction == direction])
        for direction in DIRECTIONS
    ]
    groups += [
        (str(query_class), [o for o in report.outcomes if o.query_class is query_class])
        for query_class in QueryClass
    ]
    return tuple(_score(name, outcomes, cutoffs) for name, outcomes in groups)


def render_comparison(reports: tuple[DatasetReport, ...]) -> str:
    """Return a side-by-side summary of several runs, then each full report."""
    if not reports:
        raise ValueError("at least one report is required")
    first = reports[0]
    scored = [score_slices(report) for report in reports]
    cutoff = max(k for k in RECALL_CUTOFFS if k <= first.limit)
    lines = [
        f"# Retrieval comparison: {first.name} v{first.version}",
        "",
        *_configuration(reports),
        "",
        f"## Recall@{cutoff} and MRR by slice",
        "",
        "| Slice | Cases | "
        + " | ".join(
            f"{_label(report)} R@{cutoff} | {_label(report)} MRR" for report in reports
        )
        + " |",
        "|---|---:|" + "---:|---:|" * len(reports),
    ]
    for index, first_score in enumerate(scored[0]):
        cells = [f"{first_score.name} | {first_score.cases}"]
        for scores in scored:
            score = scores[index]
            recall = dict(score.recall).get(cutoff)
            cells.append(f"{_number(recall)} | {_number(score.mrr)}")
        lines.append("| " + " | ".join(cells) + " |")
    body = "\n".join(lines) + "\n"
    return body + "".join("\n" + render_report(report) for report in reports)


def render_report(report: DatasetReport) -> str:
```

In `render_report`, replace the old header and slice table:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    branches = ", ".join(report.branches)
    lines = [
        f"# Retrieval dataset report: {report.name} v{report.version}",
        "",
        f"Branches: {branches}. Each case keeps its top {report.limit} Candidates;"
        " a rank is where a relevant Chunk appears among them.",
        "",
        "## By slice",
        "",
        "| Slice | Cases | Found |",
        "|---|---:|---:|",
    ]
    slices: list[tuple[str, list[CaseOutcome]]] = [
        (direction, [o for o in report.outcomes if o.direction == direction])
        for direction in DIRECTIONS
    ]
    slices += [
        (str(query_class), [o for o in report.outcomes if o.query_class is query_class])
        for query_class in QueryClass
    ]
    for name, outcomes in slices:
        found = sum(outcome.found for outcome in outcomes)
        lines.append(f"| {name} | {len(outcomes)} | {found} |")
```

Replace with:

```python
    scores = score_slices(report)
    cutoffs = [k for k, _ in scores[0].recall]
    lines = [
        f"# Retrieval dataset report: {report.name} v{report.version}",
        "",
        *_configuration((report,)),
        "",
        "## By slice",
        "",
        "| Slice | Cases | Found | "
        + "".join(f"Recall@{k} | " for k in cutoffs)
        + "MRR | Degraded |",
        "|---|---:|---:|" + "---:|" * len(cutoffs) + "---:|---:|",
    ]
    for score in scores:
        recall = "".join(f"{_number(value)} | " for _, value in score.recall)
        if score.cases == 0:
            recall = "n/a | " * len(cutoffs)
        lines.append(
            f"| {score.name} | {score.cases} | {score.found} | {recall}"
            f"{_number(score.mrr)} | {score.degraded} |"
        )
```

Add the formatting helpers just before `_error_names`:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
def _error_names(error: BaseException) -> str:
```

Replace with:

```python
def _configuration(reports: tuple[DatasetReport, ...]) -> list[str]:
    first = reports[0]
    if len(reports) == 1:
        runs = f"- Branches: {', '.join(first.branches)}."
    else:
        runs = f"- Runs: {'; '.join(_label(report) for report in reports)}."
    lines = [
        runs,
        f"- Each case keeps its top {first.limit} Candidates; a rank is where"
        " a relevant Chunk appears among them.",
    ]
    model = next((r.embedding for r in reports if r.embedding is not None), None)
    if model is not None:
        lines.append(
            f"- Embedding model: {model.provider} {model.model},"
            f" {model.dimensions} dimensions."
        )
    if any(len(report.branches) > 1 for report in reports):
        lines.append(f"- Fusion: Reciprocal Rank Fusion, k = {DEFAULT_RRF_K}.")
    return lines


def _label(report: DatasetReport) -> str:
    return " + ".join(report.branches)


def _number(value: Fraction | None) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def _score(
    name: str, outcomes: list[CaseOutcome], cutoffs: tuple[int, ...]
) -> SliceScore:
    found = sum(outcome.found for outcome in outcomes)
    degraded = sum(outcome.degraded for outcome in outcomes)
    if not outcomes:
        return SliceScore(name, 0, 0, 0, recall=(), mrr=None)
    recall = tuple(
        (k, mean([recall_at(outcome.ranks, k) for outcome in outcomes]))
        for k in cutoffs
    )
    mrr = mean([reciprocal_rank(outcome.ranks) for outcome in outcomes])
    return SliceScore(name, len(outcomes), found, degraded, recall=recall, mrr=mrr)


def _error_names(error: BaseException) -> str:
```

Check: `uv run pytest tests/test_evaluation_dataset.py -q` gives `63 passed`. The old report test still holds, because each old row, such as `| en->en | 2 | 1 |`, is still the start of the new, longer row. Run `make lint` and `make typecheck`: both are clean again.

### Step 6 — Coding step: add the managed embeddings option to the script

This step lets you run the comparison from the command line with OpenAI.

**In plain words:** Without the new option the script behaves as before: word search only, one report. With `--managed-embeddings` it builds the OpenAI embedding model from your settings, runs the comparison, and writes the side-by-side page to its own file. It refuses to start without an API key. The check-only mode still needs no settings at all.

| Change | Why |
|---|---|
| `--managed-embeddings` | runs `compare_branches` with `OpenAIEmbeddingModel` |
| `Settings()` read after `--verify-only` | a broken setting cannot stop the offline check |
| the key comes from `KNOWLEDGE_SERVICE_MODEL_API_KEY` | the same setting the chat model uses; it is never printed |
| `artifacts/evaluations/bilingual-seed-managed.md` | a separate report file, ignored by Git like the first one |

The model and its dimensions come from `OpenAIEmbeddingSettings`, which reads `EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` from the environment. The defaults are `text-embedding-3-small` and 1536. Replace the whole of `scripts/run_retrieval_dataset.py` with:

```python
"""Verify the bilingual seed dataset and, given a database, run every case.

`--verify-only` checks the cases against the corpus and needs no database.
Otherwise point `KNOWLEDGE_SERVICE_DATABASE_URL` at an empty, migrated
database; the run refuses any other. By default only word search runs and
the report goes to `artifacts/evaluations/bilingual-seed.md`.

`--managed-embeddings` also embeds the corpus with the OpenAI model set by
`EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` (default
`text-embedding-3-small`, 1536), using `KNOWLEDGE_SERVICE_MODEL_API_KEY`.
It runs every case three ways, words only, meaning only, and both fused, and
writes `artifacts/evaluations/bilingual-seed-managed.md`. Git ignores both
reports. Every Chunk, the restricted ones included, is sent to OpenAI; the
seed corpus is made up, so nothing private leaves the machine.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from openai import AsyncOpenAI
from pydantic import SecretStr

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.openai_adapter import (
    OpenAIEmbeddingModel,
    OpenAIEmbeddingSettings,
)
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DEFAULT_LIMIT,
    DatasetError,
    EvaluationDataset,
    compare_branches,
    load_dataset,
    render_comparison,
    render_report,
    run_dataset,
    verify_corpus,
)
from knowledge_service.settings import Settings

REPORT = Path("artifacts/evaluations/bilingual-seed.md")
MANAGED_REPORT = Path("artifacts/evaluations/bilingual-seed-managed.md")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_DIRECTORY)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--managed-embeddings", action="store_true")
    arguments = parser.parse_args()
    corpus = arguments.dataset / CORPUS_DIRECTORY
    try:
        dataset = load_dataset(arguments.dataset / CASES_FILE)
        if arguments.verify_only:
            asyncio.run(verify_corpus(dataset, corpus))
            sys.stdout.write(f"{len(dataset.cases)} cases verified\n")
            return 0
        # Read only for a run, so a bad setting cannot stop the check above.
        settings = Settings()
        api_key = settings.model_api_key
        if arguments.managed_embeddings and api_key is None:
            sys.stderr.write(
                "set KNOWLEDGE_SERVICE_MODEL_API_KEY to embed the corpus\n"
            )
            return 1
        runtime = create_database_runtime(settings)
        rendered = asyncio.run(
            _run(
                runtime,
                dataset,
                corpus,
                arguments.limit,
                api_key if arguments.managed_embeddings else None,
            )
        )
    except DatasetError as error:
        sys.stderr.write(f"dataset run failed: {error}\n")
        return 1
    default = MANAGED_REPORT if arguments.managed_embeddings else REPORT
    output: Path = arguments.output or default
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"{rendered}\nFull report: {output}\n")
    return 0


async def _run(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    limit: int,
    api_key: SecretStr | None,
) -> str:
    observed_at = datetime.now(UTC)
    try:
        if api_key is None:
            report = await run_dataset(
                runtime, dataset, corpus, observed_at=observed_at, limit=limit
            )
            return render_report(report)
        client = AsyncOpenAI(api_key=api_key.get_secret_value())
        try:
            model = OpenAIEmbeddingModel(client, OpenAIEmbeddingSettings())
            reports = await compare_branches(
                runtime,
                dataset,
                corpus,
                observed_at=observed_at,
                model=model,
                limit=limit,
            )
        finally:
            await client.close()
        return render_comparison(reports)
    finally:
        await runtime.dispose()


if __name__ == "__main__":
    sys.exit(main())
```

Check: `uv run python scripts/run_retrieval_dataset.py --verify-only` prints `16 cases verified`. With the API key unset, `uv run python scripts/run_retrieval_dataset.py --managed-embeddings` prints `set KNOWLEDGE_SERVICE_MODEL_API_KEY to embed the corpus` and exits with 1.

### Step 7 — Coding step: test the scores and the comparison

This step adds the tests the task asks for: success, the highest-risk edge, and typed failures. They use `FakeEmbeddingModel` from `tests/fakes.py`, so they need no network and give the same vectors every time.

**In plain words:** The unit tests check the scores and the report on a small hand-made run, and check that bad settings are refused before the database is touched. The database tests run the whole comparison on the real seed corpus with the fake model. They check that all three runs finish and that the restricted passage is never returned even when it is the closest match. They also check that a failing model or a broken search stops the run with a clean error, and that no question or passage text leaks anywhere.

The fake model hashes each text into a vector. Its vectors carry no meaning, so the dense ranks in these tests are random. What matters is that equal texts get equal vectors. The highest-risk test uses that: it embeds the restricted passage's own text as the question, so that passage is at distance 0, the best possible match.

| Test | Proves |
|---|---|
| `test_slices_get_exact_recall_and_mrr` | exact Recall and MRR per slice; cut-offs above the limit left out; empty slices get no scores |
| `test_the_report_shows_the_configuration_and_the_scores`, `test_the_comparison_puts_every_run_side_by_side` | the printed numbers and configuration match the scores |
| `test_a_wrong_comparison_setting_is_refused_before_any_work` | a bad limit or a model without an identity raises `DatasetRunRefused` with no connection made |
| `test_verify_only_needs_no_settings` | the check-only mode survives a broken setting |
| `test_every_case_runs_three_ways_and_the_report_names_no_text` | success: three reports in order, every passage embedded, the word search ranks unchanged, no text in the report or the DEBUG logs |
| `test_the_nearest_chunk_is_restricted_and_still_never_returned` | the highest-risk edge: without the permission check the restricted passage ranks first; with it, the reader gets the 19 public passages only |
| `test_a_model_that_fails_stops_the_run_before_any_case` | the typed failure: `CorpusNotEmbedded`, no vectors stored, no question sent, nothing linked to the error |
| `test_a_model_that_breaks_during_embedding_raises_a_typed_error` | a model whose identity breaks after the first read still gives `CorpusNotEmbedded` |
| `test_a_database_with_an_embedding_space_is_refused_before_any_write` | an existing space is refused before any Source is written |
| `test_an_unexpected_error_in_a_case_is_typed_and_quiet` | a search bug becomes `CaseFailed` naming the case and `RuntimeError`, with the question nowhere in the error chain |

In `tests/test_evaluation_dataset.py`, extend the imports:

Find in `tests/test_evaluation_dataset.py`:

```python
import shutil
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
```

Replace with:

```python
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any
```

Find in `tests/test_evaluation_dataset.py`:

```python
from knowledge_service.database import create_database_runtime
from knowledge_service.retrieval import evaluation_dataset
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DIRECTIONS,
    MAX_FILE_BYTES,
    CaseOutcome,
    DatasetReport,
    DatasetRunRefused,
    InvalidDataset,
    QueryClass,
    load_dataset,
    render_report,
    run_dataset,
    verify_corpus,
)
```

Replace with:

```python
from fakes import FakeEmbeddingModel
from knowledge_service.database import create_database_runtime
from knowledge_service.embeddings.port import EmbeddingModel, EmbeddingModelIdentity
from knowledge_service.retrieval import evaluation_dataset
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DIRECTIONS,
    MAX_FILE_BYTES,
    CaseOutcome,
    DatasetReport,
    DatasetRunRefused,
    InvalidDataset,
    QueryClass,
    compare_branches,
    load_dataset,
    render_comparison,
    render_report,
    run_dataset,
    score_slices,
    verify_corpus,
)
```

Then add the new tests at the end of the file. The first line below is the last line of the existing report test:

Find in `tests/test_evaluation_dataset.py`:

```python
    assert rendered.startswith("# Retrieval dataset report: bilingual-seed v1\n")
```

Replace with:

```python
    assert rendered.startswith("# Retrieval dataset report: bilingual-seed v1\n")


SCORED = DatasetReport(
    name="bilingual-seed",
    version=1,
    limit=5,
    branches=(RetrievalBranch.DENSE, RetrievalBranch.LEXICAL),
    outcomes=(
        CaseOutcome("a", "en->en", QueryClass.PLAIN, (1,), degraded=False),
        CaseOutcome("b", "en->en", QueryClass.IDENTIFIER, (4,), degraded=False),
        CaseOutcome("c", "en->en", QueryClass.PLAIN, (None,), degraded=True),
        CaseOutcome("d", "uk->en", QueryClass.PLAIN, (None, 3), degraded=False),
    ),
    embedding=EmbeddingModelIdentity(
        provider="openai", model="text-embedding-3-small", dimensions=1536
    ),
)


def test_slices_get_exact_recall_and_mrr() -> None:
    scores = {score.name: score for score in score_slices(SCORED)}

    en_en = scores["en->en"]
    assert (en_en.cases, en_en.found, en_en.degraded) == (3, 2, 1)
    # Cut-offs above the run's limit of 5 are left out.
    assert en_en.recall == (
        (1, Fraction(1, 3)),
        (3, Fraction(1, 3)),
        (5, Fraction(2, 3)),
    )
    assert en_en.mrr == Fraction(5, 12)
    # Two relevant Chunks, one found at rank 3: half of them, best rank 3.
    assert scores["uk->en"].recall[-1] == (5, Fraction(1, 2))
    assert scores["uk->en"].mrr == Fraction(1, 3)
    assert scores["all"].cases == 4
    assert scores["uk->uk"].recall == ()
    assert scores["uk->uk"].mrr is None


def test_the_report_shows_the_configuration_and_the_scores() -> None:
    rendered = render_report(SCORED)

    assert "- Branches: dense, lexical." in rendered
    assert "- Embedding model: openai text-embedding-3-small, 1536 dimensions." in (
        rendered
    )
    assert "- Fusion: Reciprocal Rank Fusion, k = 60." in rendered
    assert "| Slice | Cases | Found | Recall@1 | Recall@3 | Recall@5 | MRR |" in (
        rendered
    )
    assert "| en->en | 3 | 2 | 0.333 | 0.333 | 0.667 | 0.417 | 1 |" in rendered
    assert "| uk->uk | 0 | 0 | n/a | n/a | n/a | n/a | 0 |" in rendered


def test_the_comparison_puts_every_run_side_by_side() -> None:
    lexical = DatasetReport(
        name="bilingual-seed",
        version=1,
        limit=5,
        branches=(RetrievalBranch.LEXICAL,),
        outcomes=tuple(
            CaseOutcome(
                o.case_id,
                o.direction,
                o.query_class,
                (None,) * len(o.ranks),
                degraded=False,
            )
            for o in SCORED.outcomes
        ),
    )

    rendered = render_comparison((lexical, SCORED))

    assert rendered.startswith("# Retrieval comparison: bilingual-seed v1\n")
    assert "- Runs: lexical; dense + lexical." in rendered
    assert "- Embedding model: openai text-embedding-3-small" in rendered
    assert (
        "| Slice | Cases | lexical R@5 | lexical MRR"
        " | dense + lexical R@5 | dense + lexical MRR |"
    ) in rendered
    assert "| en->en | 3 | 0.000 | 0.000 | 0.667 | 0.417 |" in rendered
    assert rendered.count("# Retrieval dataset report:") == 2


class NamelessModel(FakeEmbeddingModel):
    @property
    def identity(self) -> EmbeddingModelIdentity:
        raise RuntimeError("no identity")


@pytest.mark.parametrize(
    ("limit", "model", "message"),
    [
        (0, FakeEmbeddingModel(), "limit"),
        (True, FakeEmbeddingModel(), "limit"),
        (10, NamelessModel(), "EmbeddingModelIdentity"),
    ],
)
async def test_a_wrong_comparison_setting_is_refused_before_any_work(
    limit: int, model: EmbeddingModel, message: str
) -> None:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr("postgresql+psycopg://u:p@localhost:1/x"))
    )
    dataset = load_dataset(DATASET_DIRECTORY / CASES_FILE)

    with pytest.raises(DatasetRunRefused, match=message):
        await compare_branches(
            runtime,
            dataset,
            DATASET_DIRECTORY / CORPUS_DIRECTORY,
            observed_at=datetime(2026, 11, 4, tzinfo=UTC),
            model=model,
            limit=limit,
        )
    await runtime.dispose()


def test_verify_only_needs_no_settings() -> None:
    # Only a run reads the settings, so a bad one cannot stop the check.
    finished = subprocess.run(
        [sys.executable, "scripts/run_retrieval_dataset.py", "--verify-only"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "KNOWLEDGE_SERVICE_LOG_LEVEL": "not-a-level"},
        timeout=60,
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout == "16 cases verified\n"
```

Create `tests/test_branch_comparison_integration.py`. The `chain` helper walks every error linked to the one raised, the same way a traceback prints them, so the tests can prove no linked error is left behind:

```python
"""The seed dataset embedded and run three ways on real PostgreSQL."""

import logging
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from fakes import FakeEmbeddingModel
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import (
    EmbeddingLimits,
    EmbeddingModelIdentity,
    EmbeddingUnavailable,
)
from knowledge_service.identifiers import EmbeddingSpaceId
from knowledge_service.persistence.embeddings import insert_embedding_space
from knowledge_service.persistence.schema import (
    chunk_embeddings,
    chunks,
    documents,
    sources,
)
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    READER,
    SOURCE_IDS,
    SPACE_ID,
    CaseFailed,
    CorpusNotEmbedded,
    DatasetRunRefused,
    EvaluationDataset,
    Folder,
    compare_branches,
    load_dataset,
    render_comparison,
    run_dataset,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import BranchRequest, BranchResult
from knowledge_service.retrieval.vector_search import (
    search_similar_chunks,
    vector_search_statement,
)
from knowledge_service.settings import Settings

OBSERVED_AT = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
# The fake counts words as tokens; every seed Chunk fits these limits.
LIMITS = EmbeddingLimits(max_inputs=64, max_input_tokens=1_000, max_batch_tokens=20_000)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


@pytest.fixture
def dataset() -> EvaluationDataset:
    return load_dataset(DATASET_DIRECTORY / CASES_FILE)


def chain(error: BaseException) -> Iterator[BaseException]:
    """Yield the error and every error linked to it, as a traceback shows them."""
    seen: list[BaseException] = []
    pending = [error]
    while pending:
        current = pending.pop()
        if any(current is item for item in seen):
            continue
        seen.append(current)
        yield current
        pending += [e for e in (current.__cause__, current.__context__) if e]
        if isinstance(current, BaseExceptionGroup):
            group = cast(BaseExceptionGroup[BaseException], current)
            pending += list(group.exceptions)


class IdentityFailsLater(FakeEmbeddingModel):
    """Names itself once, then fails: a model that breaks after the run began."""

    reads = 0

    @property
    def identity(self) -> EmbeddingModelIdentity:
        self.reads += 1
        if self.reads > 1:
            raise RuntimeError(f"identity lost {RESTRICTED_MARKER}")
        return super().identity


async def count(runtime: DatabaseRuntime, table: sa.Table) -> int:
    async with runtime.transaction() as connection:
        total = await connection.scalar(sa.select(sa.func.count()).select_from(table))
    return int(total or 0)


@pytest.mark.integration
async def test_every_case_runs_three_ways_and_the_report_names_no_text(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = FakeEmbeddingModel(limits=LIMITS)

    reports = await compare_branches(
        runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model
    )

    assert [report.branches for report in reports] == [
        (RetrievalBranch.LEXICAL,),
        (RetrievalBranch.DENSE,),
        (RetrievalBranch.DENSE, RetrievalBranch.LEXICAL),
    ]
    assert [report.embedding for report in reports] == [
        None,
        model.identity,
        model.identity,
    ]
    for report in reports:
        assert [o.case_id for o in report.outcomes] == [
            c.case_id for c in dataset.cases
        ]
        assert not any(outcome.degraded for outcome in report.outcomes)
    # Words only gives the same ranks as the M05-T07 baseline.
    lexical = {o.case_id: o.ranks for o in reports[0].outcomes}
    assert lexical["en-en-expense-deadline"] == (1,)
    assert lexical["uk-uk-per-diem-abroad"] == (1,)
    # Every Chunk, the two restricted ones included, has one vector.
    assert await count(runtime, chunk_embeddings) == await count(runtime, chunks) == 21
    rendered = render_comparison(reports)
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in dataset.cases)


@pytest.mark.integration
async def test_the_nearest_chunk_is_restricted_and_still_never_returned(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    model = FakeEmbeddingModel(limits=LIMITS)
    await compare_branches(
        runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model
    )
    async with runtime.transaction() as connection:
        restricted_text = await connection.scalar(
            sa.select(chunks.c.text)
            .select_from(chunks.join(documents))
            .where(documents.c.source_id == SOURCE_IDS[Folder.RESTRICTED].value)
            .order_by(chunks.c.ordinal)
            .limit(1)
        )
    assert restricted_text is not None
    # The fake gives equal text equal vectors, so this query sits exactly on
    # a restricted Chunk: distance 0, the best match a search could find.
    batch = await model.embed(
        [restricted_text], request_id=READER.user_id.value, timeout_seconds=5.0
    )
    query = batch.vectors[0]

    async with runtime.transaction() as connection:
        unfiltered = (
            (
                await connection.execute(
                    vector_search_statement(
                        space_id=SPACE_ID,
                        query=query,
                        eligibility=sa.true(),
                        restriction=sa.true(),
                        limit=1,
                    )
                )
            )
            .mappings()
            .one()
        )
        found = await search_similar_chunks(
            connection,
            identity=model.identity,
            query=query,
            context=READER,
            limit=21,
        )

    # Without the permission check the restricted Chunk ranks first ...
    assert unfiltered["source_id"] == SOURCE_IDS[Folder.RESTRICTED].value
    # ... with it, the reader gets every public Chunk and no restricted one.
    assert len(found.candidates) == 19
    assert {c.source_id for c in found.candidates} == {SOURCE_IDS[Folder.PUBLIC]}


@pytest.mark.integration
async def test_a_model_that_fails_stops_the_run_before_any_case(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    model = FakeEmbeddingModel(limits=LIMITS, error=EmbeddingUnavailable())

    with pytest.raises(CorpusNotEmbedded) as raised:
        await compare_branches(
            runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model
        )

    assert str(raised.value) == (
        "the corpus could not be embedded: IncompleteEmbedding"
    )
    assert [type(error) for error in chain(raised.value)] == [CorpusNotEmbedded]
    assert await count(runtime, chunk_embeddings) == 0
    # Only the corpus was sent: no Question reached the model.
    questions = {case.question for case in dataset.cases}
    assert not any(text in questions for batch in model.batches for text in batch)


@pytest.mark.integration
async def test_a_database_with_an_embedding_space_is_refused_before_any_write(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    async with runtime.transaction() as connection:
        await insert_embedding_space(
            connection,
            space_id=EmbeddingSpaceId.new(),
            identity=EmbeddingModelIdentity(
                provider="other", model="older-model", dimensions=8
            ),
            is_current=True,
            created_at=OBSERVED_AT,
        )
    model = FakeEmbeddingModel(limits=LIMITS)

    with pytest.raises(DatasetRunRefused, match="empty database"):
        await compare_branches(
            runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model
        )

    assert await count(runtime, sources) == 0
    assert model.batches == []


@pytest.mark.integration
async def test_a_model_that_breaks_during_embedding_raises_a_typed_error(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    with pytest.raises(CorpusNotEmbedded, match="RuntimeError") as raised:
        await compare_branches(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=IdentityFailsLater(limits=LIMITS),
        )

    assert [type(error) for error in chain(raised.value)] == [CorpusNotEmbedded]
    assert await count(runtime, chunk_embeddings) == 0


@pytest.mark.integration
async def test_an_unexpected_error_in_a_case_is_typed_and_quiet(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    async def broken(request: BranchRequest) -> BranchResult:
        raise RuntimeError(f"cannot answer {request.question}")

    with pytest.raises(CaseFailed) as raised:
        await run_dataset(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            branches={RetrievalBranch.LEXICAL: broken},
        )

    first = dataset.cases[0]
    assert str(raised.value) == f"case {first.case_id}: the search raised RuntimeError"
    # Nothing linked to the error, which a traceback would print, holds the
    # Question.
    assert [type(error) for error in chain(raised.value)] == [CaseFailed]
```

Check, in this order:

```bash
uv run pytest tests/test_ranking_metrics.py tests/test_evaluation_dataset.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_branch_comparison_integration.py tests/test_evaluation_dataset_integration.py -q
```

Expected: `102 passed`, then `12 passed`.

### Step 8 — No coding in this step: run with OpenAI and read the report

This step runs the comparison with the real managed model and checks the result against your predictions. It also breaks the permission check on purpose, to see the tests catch it.

**Action 1:** create an empty database, migrate it, and run. The database URL holds the password, so it lives only in a shell variable. Type the API key into the variable without echoing it:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_dataset#')"
uv run alembic upgrade head
read -rs KNOWLEDGE_SERVICE_MODEL_API_KEY && export KNOWLEDGE_SERVICE_MODEL_API_KEY
uv run python scripts/run_retrieval_dataset.py --managed-embeddings
```

The run sends 21 passages and 32 questions to OpenAI: a few thousand tokens. The page starts with the configuration and the side-by-side table, then the three full reports. I could not run this step while preparing the lesson, because no API key was available. Your numbers are the first real ones.

**Action 2:** test one assumption about repeatability. Save the report, run again on a fresh database, and compare:

```bash
cp artifacts/evaluations/bilingual-seed-managed.md /tmp/first-managed.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --managed-embeddings > /dev/null
diff /tmp/first-managed.md artifacts/evaluations/bilingual-seed-managed.md && echo same
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
unset KNOWLEDGE_SERVICE_DATABASE_URL KNOWLEDGE_SERVICE_MODEL_API_KEY
```

The word search part must be identical. The dense parts may or may not be, because the provider's numbers can vary in the last digits. Either answer is evidence; write down which one you got.

**Action 3:** read the report against your predictions, slice by slice.

- Compare the `lexical` columns with the M05-T07 baseline. They must match: 5 of 16 found, all at rank 1.
- Look at `en->uk` and `uk->en` under `dense`. This is the result the lesson exists for: word search scored 0 there.
- Look at the identifier slice under `dense` and under `dense + lexical`. Codes such as `VPN-4031` carry little meaning, so this is where word search may still win.
- Read the "By case" table of the dense run for every miss. Note each missed case ID and, in one line, why you think it missed.

Then write at least one **falsified assumption** in the evidence file. Use this shape: "I assumed X. The report shows Y (case IDs and numbers). So Z is wrong." If every prediction held, the repeatability check, or the merged run against the better single run, is the usual place to find one.

**Action 4:** break the permission check on purpose. In `src/knowledge_service/retrieval/vector_search.py`, in `search_similar_chunks`, change `eligibility=eligible_chunks_predicate(context),` to `eligibility=sa.true(),`. Then run:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_branch_comparison_integration.py -q
```

Expected: `test_the_nearest_chunk_is_restricted_and_still_never_returned` fails, because the reader now gets the restricted passage. `test_every_case_runs_three_ways_and_the_report_names_no_text` fails too: the run's own guard stops it with `IneligibleChunkRetrieved` on `en-en-annual-leave-days`. Undo the change and run the tests again until they pass. Do not run the paid script with the break in place.

### Step 9 — No coding in this step: gates, docs, evidence, and progress

This step proves everything still works, updates the one architecture line the change made out of date, and records the result.

**Action 1:** in `docs/architecture.md`, change the `retrieval` row of the "Code layout" table to:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR) |
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_ranking_metrics.py tests/test_evaluation_dataset.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave `make test-unit` 1199 passed and `make test-integration` 300 passed.

**Action 3:** complete the evidence file:

- **Design:** the changed files; why the corpus is stored and embedded once; why restricted passages get vectors; why scores are exact fractions; why errors are raised outside `except` blocks.
- **Verification:** the commands and counts, the side-by-side table from Step 8, the misses with one line each, the falsified assumption, the repeatability result, the deliberate break, and the known limits from "Before you begin". Paste the report into the evidence file: `artifacts/` is ignored by Git, and the task asks for a versioned report.
- **Review:** an adversarial review of the prototype found three problems, all fixed with regression tests that failed before the fix. A search bug escaped as an untyped error group carrying the question text; it now becomes `CaseFailed` with type names only. A model that broke after its first read escaped untyped after rows were written; it now becomes `CorpusNotEmbedded`. The check-only mode had started to need valid settings; it no longer reads them.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M05-T08 row to `docs/curriculum/PROGRESS.md`, set the next task to M05-T09, and commit with `m05-t08: measure managed multilingual embeddings`.

## Reflection questions

1. What does `compare_branches` hide from the script that calls it? List what the script never has to think about, such as creating the Embedding Space or the order of the three runs.
2. Someone later speeds up the comparison by embedding only the public documents, because "the reader can't see the restricted ones anyway". What would the permission test then stop proving, and why?
3. MRR and Recall@10 can move in different directions. Describe a change to the search that would raise Recall@10 but lower MRR, and say which number a person asking a question would care about more.
4. Which of your predictions was wrong, and what did you believe about embeddings that made you predict it?
5. Sixteen cases are few. In which slice would one more case most likely change the conclusion you wrote, and what would that case look like?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before running anything.
- [ ] `ranking_metrics.py` exists and `tests/test_ranking_metrics.py` gives 32 passed.
- [ ] The report shows a configuration block and Recall@1, @3, @5, @10 and MRR for every slice.
- [ ] `compare_branches` stores and embeds once and returns lexical, dense, and merged reports.
- [ ] The restricted passage is embedded, is the nearest match in the test, and is never returned to the reader.
- [ ] A failing model raises `CorpusNotEmbedded`, and a search bug raises `CaseFailed`; neither carries any text.
- [ ] You ran `--managed-embeddings` with OpenAI and pasted the report into the evidence file.
- [ ] The evidence file names every miss and at least one falsified assumption.
- [ ] You ran the deliberate break from Step 8, saw a test fail, and undid it.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The architecture line, evidence, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
