"""The bilingual seed dataset: a small corpus and the cases that probe it.

The dataset lives in one folder. `cases.json` holds the Evaluation Cases,
and `corpus/` holds the Documents they search: `corpus/public/` is readable
by everyone, `corpus/restricted/` by nobody. Each case names the Chunks that
answer its Question by Document path and heading path, such as
`public/annual-leave.md` and `Annual leave policy > Days per year`, because
Chunk IDs are new on every ingestion.

What a caller can rely on:

- **Checked on load.** `load_dataset` raises `InvalidDataset` for a file
  that is not valid JSON, has an unknown or missing field, a duplicate case,
  relevant evidence outside `public/`, ineligible evidence outside
  `restricted/`, too few cases for a slice, or a key written twice. Its
  messages never repeat the file's values.
- **Checked against the corpus.** `verify_corpus` reads and chunks the
  corpus the way ingestion does, offline, and raises `InvalidDataset` unless
  every named Chunk exists exactly once and every relevant Document is in
  the case's evidence language.
- **Run as one reader.** `run_dataset` checks its settings, then ingests
  the corpus into an empty database, asks every Question as one ordinary
  employee, and records the rank of each relevant Chunk, or None when it is
  missing. A Question no branch can answer raises `CaseUnanswered`.
- **Compared with embeddings.** `compare_branches` also embeds every
  Chunk with one embedding model, then runs every case three times: words
  only, meaning only, and both fused. A Chunk that cannot be embedded
  raises `CorpusNotEmbedded` before any case runs. Every Chunk, restricted
  ones included, is sent to the embedding model's provider.
- **Compared with sparse weights.** `compare_sparse` indexes every Chunk
  with a sparse model such as BGE-M3 (see `retrieval.sparse_search`), then
  runs every case twice: PostgreSQL word search, and sparse search alone.
  It also returns what the index costs and how long each case took, which
  `render_sparse_comparison` adds to the report. This is the M05-T10
  prototype; nothing in production uses it.
- **Compared with reranking.** `compare_reranking` embeds every Chunk like
  `compare_branches`, runs every case with both branches fused, then
  reranks each case's fused Candidates with a Reranker (see
  `retrieval.reranking`). Only Candidates that passed the permission check
  reach the reranker. `render_rerank_comparison` reports the ranking delta
  per case and the reranking time on the reranker's device.
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

import json
import re
import statistics
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime
from knowledge_service.embeddings.port import EmbeddingModel, EmbeddingModelIdentity
from knowledge_service.embeddings.publication import (
    EmbeddingPublicationStatus,
    publish_document_embeddings,
)
from knowledge_service.embeddings.sparse import SparseModel, SparseModelIdentity
from knowledge_service.identifiers import EmbeddingSpaceId, SourceId, UserId
from knowledge_service.ingestion.chunking import chunk_by_structure
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.normalization import (
    NormalizedDocument,
    Quarantined,
    normalize,
)
from knowledge_service.ingestion.sources import SourceAccess, SourceError
from knowledge_service.ingestion.synchronization import synchronize_source
from knowledge_service.language import Language
from knowledge_service.persistence.documents import load_available_documents
from knowledge_service.persistence.embeddings import insert_embedding_space
from knowledge_service.persistence.schema import embedding_spaces, sources
from knowledge_service.retrieval.candidates import Candidate
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
from knowledge_service.retrieval.reranking import (
    Reranker,
    RerankerIdentity,
    RerankResult,
    rerank_candidates,
)
from knowledge_service.retrieval.sparse_search import (
    build_sparse_index,
    sparse_branch,
)
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES

DATASET_DIRECTORY = Path("datasets/bilingual-seed")
CASES_FILE = "cases.json"
CORPUS_DIRECTORY = "corpus"
MAX_FILE_BYTES = 1_000_000
MAX_NAME_CHARACTERS = 64
MIN_CASES_PER_SLICE = 2
DEFAULT_LIMIT = 10
CASE_TIMEOUT_SECONDS = 30.0
PAGE_SIZE = 100
# Recall is reported at these cut-offs, as far as the run's limit allows.
RECALL_CUTOFFS = (1, 3, 5, 10)
EMBEDDING_CONCURRENCY = 2
EMBEDDING_TIMEOUT_SECONDS = 60.0
RERANK_TIMEOUT_SECONDS = 60.0

MAX_PATH_CHARACTERS = 200

_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# Lowercase path segments, so a path is safe to print in an error message.
_PATH = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*")


class Folder(StrEnum):
    """Where a corpus Document lives, and so who may read it."""

    PUBLIC = "public"
    RESTRICTED = "restricted"


class QueryClass(StrEnum):
    """What kind of Question a case asks."""

    PLAIN = "plain"
    IDENTIFIER = "identifier"
    MORPHOLOGY = "morphology"


_LANGUAGES = (Language.ENGLISH, Language.UKRAINIAN)
DIRECTIONS = tuple(f"{asked}->{found}" for asked in _LANGUAGES for found in _LANGUAGES)

# Fixed, so two runs of the same dataset store the same Sources and reader.
SOURCE_IDS: Mapping[Folder, SourceId] = {
    Folder.PUBLIC: SourceId(UUID(int=0x5EED_0001)),
    Folder.RESTRICTED: SourceId(UUID(int=0x5EED_0002)),
}
READER = AuthorizationContext(user_id=UserId(UUID(int=0x5EED_0003)))
_REQUEST_IDS = UUID(int=0x5EED_0004)
SPACE_ID = EmbeddingSpaceId(UUID(int=0x5EED_0005))


class DatasetError(Exception):
    """The dataset could not be loaded, verified, or run."""


class InvalidDataset(DatasetError):
    """The cases file or the corpus breaks a rule of the dataset."""


class DatasetRunRefused(DatasetError):
    """The run cannot start: the database is not empty or a setting is wrong."""


class IneligibleChunkRetrieved(DatasetError):
    """Retrieval returned a Chunk the reader may not see."""


class CaseUnanswered(DatasetError):
    """No branch answered one case, so the run cannot score it."""


class CaseFailed(DatasetError):
    """A case raised an error no branch should raise; the run stopped."""


class CorpusNotEmbedded(DatasetError):
    """At least one Document of the corpus has no vectors, so no case ran."""


@dataclass(frozen=True, slots=True)
class ChunkReference:
    """One Chunk, named by its Document's path and its heading path."""

    folder: Folder
    external_id: str
    heading: str

    @property
    def document(self) -> str:
        """The Document's path inside the corpus, such as `public/a.md`."""
        return f"{self.folder}/{self.external_id}"


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """One Question, the Chunks that answer it, and the Chunks it must not see."""

    case_id: str
    question: str = field(repr=False)
    question_language: Language
    evidence_language: Language
    query_class: QueryClass
    relevant: tuple[ChunkReference, ...]
    ineligible: tuple[ChunkReference, ...]

    @property
    def direction(self) -> str:
        """The language asked in and the language answered in, as `uk->en`."""
        return f"{self.question_language}->{self.evidence_language}"


@dataclass(frozen=True, slots=True)
class EvaluationDataset:
    """A named, versioned list of verified cases."""

    name: str
    version: int
    cases: tuple[EvaluationCase, ...]


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """Where each relevant Chunk of one case ranked, or None if it was missed."""

    case_id: str
    direction: str
    query_class: QueryClass
    ranks: tuple[int | None, ...]
    degraded: bool
    # Wall time of the search, in seconds; not compared, as it varies by run.
    elapsed_seconds: float = field(default=0.0, compare=False)

    @property
    def found(self) -> bool:
        """True when at least one relevant Chunk was returned."""
        return any(rank is not None for rank in self.ranks)


@dataclass(frozen=True, slots=True)
class DatasetReport:
    """The outcome of every case of one run, in the dataset's order."""

    name: str
    version: int
    limit: int
    branches: tuple[RetrievalBranch, ...]
    outcomes: tuple[CaseOutcome, ...]
    # The model behind the dense branch; None when the run had no dense branch.
    embedding: EmbeddingModelIdentity | None = None
    # The model behind the sparse branch; None when the run had none.
    sparse: SparseModelIdentity | None = None
    # The reranker that reordered each case's Candidates; None when none did.
    reranker: RerankerIdentity | None = None


@dataclass(frozen=True, slots=True)
class SparseComparison:
    """Word search and sparse search on the same corpus, and what each costs.

    `chunks` and `weights` count the indexed Chunks and their stored
    (token, weight) pairs; `index_seconds` is the time to build the index.
    """

    lexical: DatasetReport
    sparse: DatasetReport
    chunks: int
    weights: int
    index_seconds: float


@dataclass(frozen=True, slots=True)
class RerankComparison:
    """Fused retrieval before and after reranking, and where the scores ran.

    `retrieved` and `reranked` hold the same cases; a reranked outcome's
    `elapsed_seconds` is the reranking time alone. `device` is where the
    reranker scored, such as `cpu` or `mps`, and `requests` counts the
    requests sent to it over all cases.
    """

    retrieved: DatasetReport
    reranked: DatasetReport
    device: str
    requests: int


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


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _ReferenceFile(_File):
    document: str
    heading: str


class _CaseFile(_File):
    id: str
    question: str
    question_language: Literal["en", "uk"]
    evidence_language: Literal["en", "uk"]
    query_class: Literal["plain", "identifier", "morphology"]
    relevant: list[_ReferenceFile] = Field(min_length=1)
    ineligible: list[_ReferenceFile] = Field(default_factory=list[_ReferenceFile])


class _DatasetFile(_File):
    name: str
    version: int = Field(ge=1)
    cases: list[_CaseFile] = Field(min_length=1)


def load_dataset(path: Path) -> EvaluationDataset:
    """Read and check a cases file; raise `InvalidDataset` if it breaks a rule."""
    # A pipe or a device would block the read, or never end.
    if not path.is_file():
        raise InvalidDataset("the cases file is missing or not a regular file")
    try:
        with path.open("rb") as file:
            raw = file.read(MAX_FILE_BYTES + 1)
    except OSError as error:
        raise InvalidDataset(
            f"the cases file cannot be read: {type(error).__name__}"
        ) from None
    if len(raw) > MAX_FILE_BYTES:
        raise InvalidDataset(f"the cases file is larger than {MAX_FILE_BYTES} bytes")
    try:
        content = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_constant=_no_constant,
        )
    except ValueError, RecursionError:
        # UnicodeDecodeError and JSONDecodeError are both ValueErrors.
        raise InvalidDataset("the cases file is not valid UTF-8 JSON") from None
    try:
        parsed = _DatasetFile.model_validate(content)
    except ValidationError as error:
        raise InvalidDataset(_describe(error)) from None
    return _dataset(parsed)


async def verify_corpus(dataset: EvaluationDataset, corpus: Path) -> None:
    """Check every named Chunk against the corpus, chunked as ingestion does.

    Raises `InvalidDataset` when a Document is missing or cannot be ingested,
    when a reference matches no Chunk or more than one, or when a relevant
    Document is not in the case's evidence language.
    """
    documents: dict[tuple[Folder, str], tuple[Language, Counter[str | None]]] = {}
    for folder in Folder:
        for document in await _read_folder(corpus / folder, folder):
            headings = Counter(
                chunk.heading_path for chunk in chunk_by_structure(document.parsed)
            )
            key = (folder, document.provenance.external_id)
            documents[key] = (document.language.language, headings)
    for case in dataset.cases:
        for reference in (*case.relevant, *case.ineligible):
            found = documents.get((reference.folder, reference.external_id))
            if found is None:
                raise InvalidDataset(
                    f"case {case.case_id}: {reference.document} is not in the corpus"
                )
            matches = found[1][reference.heading]
            if matches != 1:
                raise InvalidDataset(
                    f"case {case.case_id}: {matches} Chunks of {reference.document}"
                    " have the named heading path; exactly 1 is required"
                )
        for reference in case.relevant:
            language = documents[(reference.folder, reference.external_id)][0]
            if language is not case.evidence_language:
                raise InvalidDataset(
                    f"case {case.case_id}: {reference.document} is detected as"
                    f" {language}, not {case.evidence_language}"
                )


async def run_dataset(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    limit: int = DEFAULT_LIMIT,
    branches: Mapping[RetrievalBranch, BranchSearch] | None = None,
) -> DatasetReport:
    """Ingest the corpus into an empty database and run every case.

    `branches` defaults to lexical search alone, which needs no provider.
    Raises `DatasetRunRefused` before writing anything when the database
    already holds a Source, `limit` is out of range, or `branches` is not a
    mapping of `RetrievalBranch` to a search; `IneligibleChunkRetrieved`
    when a case returns a restricted Chunk; `CaseUnanswered` when no branch
    answers a case; and `InvalidDataset` like `verify_corpus`. A run that
    fails after ingesting leaves the database used: start the next one on a
    fresh database.
    """
    # bool is an int, so `True` would otherwise pass as a limit of 1.
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    searches = (
        {RetrievalBranch.LEXICAL: lexical_branch(runtime)}
        if branches is None
        else branches
    )
    _check_branches(searches)
    await verify_corpus(dataset, corpus)
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


async def compare_sparse(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: SparseModel,
    limit: int = DEFAULT_LIMIT,
) -> SparseComparison:
    """Ingest and index the corpus once, then run every case two ways.

    Every Chunk, restricted ones included, is sent to `model` for its
    weights, because search must be able to score a restricted Chunk for
    the permission check to matter. Raises like `run_dataset`, and
    `CorpusNotEmbedded` when the corpus cannot be indexed.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    await verify_corpus(dataset, corpus)
    await _ingest(runtime, dataset, corpus, observed_at, embedding=None)
    started = time.perf_counter()
    failure = "no index"
    index = None
    try:
        index = await build_sparse_index(
            runtime,
            model,
            request_id=uuid5(_REQUEST_IDS, "sparse index"),
            timeout_seconds=EMBEDDING_TIMEOUT_SECONDS,
        )
    except Exception as error:
        # The model is outside code, and its message may quote Chunk text.
        failure = _error_names(error)
    # Raised outside the `except`, so no linked error carries the text.
    if index is None:
        raise CorpusNotEmbedded(f"the corpus could not be indexed: {failure}")
    index_seconds = time.perf_counter() - started
    lexical = await _run_cases(
        dataset,
        {RetrievalBranch.LEXICAL: lexical_branch(runtime)},
        limit,
        embedding=None,
    )
    sparse = await _run_cases(
        dataset,
        {RetrievalBranch.SPARSE: sparse_branch(runtime, model, index)},
        limit,
        embedding=None,
        sparse=index.identity,
    )
    return SparseComparison(
        lexical=lexical,
        sparse=sparse,
        chunks=index.chunks,
        weights=index.weights,
        index_seconds=index_seconds,
    )


async def compare_reranking(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: EmbeddingModel,
    reranker: Reranker,
    device: str,
    limit: int = DEFAULT_LIMIT,
) -> RerankComparison:
    """Ingest and embed the corpus once, run every case fused, then rerank it.

    Each case runs dense and lexical search fused, as `compare_branches`
    does, and sends that case's fused Candidates, and nothing else, to
    `reranker`. Raises like `compare_branches`; `DatasetRunRefused` also
    when `reranker` has no valid identity, and `CaseFailed` when reranking
    a case fails, naming the case and the error type only.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    identity = _identity(model)
    reranker_identity = _reranker_identity(reranker)
    await verify_corpus(dataset, corpus)
    await _ingest(runtime, dataset, corpus, observed_at, embedding=identity)
    await _embed_corpus(runtime, model, observed_at)
    searches: Mapping[RetrievalBranch, BranchSearch] = {
        RetrievalBranch.DENSE: dense_branch(runtime, model),
        RetrievalBranch.LEXICAL: lexical_branch(runtime),
    }
    retrieved: list[CaseOutcome] = []
    reranked: list[CaseOutcome] = []
    requests = 0
    for case in dataset.cases:
        result, elapsed = await _search_case(case, searches, limit)
        retrieved.append(_outcome(case, result.candidates, result.degraded, elapsed))
        started = time.perf_counter()
        ordered = await _rerank_case(case, reranker, result, reranker_identity)
        took = time.perf_counter() - started
        requests += ordered.requests
        reranked.append(_outcome(case, ordered.candidates, result.degraded, took))
    branches = tuple(branch for branch in RetrievalBranch if branch in searches)

    def report(
        outcomes: list[CaseOutcome], by: RerankerIdentity | None
    ) -> DatasetReport:
        return DatasetReport(
            name=dataset.name,
            version=dataset.version,
            limit=limit,
            branches=branches,
            outcomes=tuple(outcomes),
            embedding=identity,
            reranker=by,
        )

    return RerankComparison(
        retrieved=report(retrieved, None),
        reranked=report(reranked, reranker_identity),
        device=device,
        requests=requests,
    )


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


def render_sparse_comparison(comparison: SparseComparison) -> str:
    """Return the side-by-side report, then what the index costs, then timing.

    Everything above "## Latency" depends only on the data and the model, so
    two runs can be compared with `diff`; the latency section changes from
    run to run.
    """
    reports = (comparison.lexical, comparison.sparse)
    chunks, weights = comparison.chunks, comparison.weights
    per_chunk = weights / chunks if chunks else 0.0
    # pgvector's sparsevec: 4 bytes of index and 4 of value per weight, plus
    # a 16-byte header per vector.
    stored = 8 * weights + 16 * chunks
    lines = [
        "",
        "## Cost",
        "",
        f"- Indexed Chunks: {chunks}, restricted ones included.",
        f"- Stored weights: {weights}, {per_chunk:.1f} per Chunk on average.",
        f"- As pgvector sparsevec: about {stored / 1024:.1f} KiB for this corpus.",
        "- Per question: word search runs one SQL statement; sparse search"
        " also calls the model once, so it needs the BGE-M3 server running.",
        "",
        "## Latency",
        "",
        "Wall time per case, from the question to the ranked Candidates. It"
        " changes from run to run.",
        "",
        f"- Index build: {comparison.index_seconds:.1f} s.",
        "",
        "| Run | Median ms | Slowest ms |",
        "|---|---:|---:|",
    ]
    for report in reports:
        times = [outcome.elapsed_seconds * 1000 for outcome in report.outcomes]
        median = statistics.median(times) if times else 0.0
        slowest = max(times, default=0.0)
        lines.append(f"| {_label(report)} | {median:.1f} | {slowest:.1f} |")
    return render_comparison(reports) + "\n".join(lines) + "\n"


def render_rerank_comparison(comparison: RerankComparison) -> str:
    """Return the side-by-side report, then each case's move, then timing.

    Everything above "## Latency" depends only on the data and the models,
    so two runs on one device can be compared with `diff`; the latency
    section changes from run to run.
    """
    before, after = comparison.retrieved, comparison.reranked
    moves = Counter[str]()
    lines = [
        "",
        "## Ranking delta",
        "",
        "Where each case's relevant Chunks ranked before and after reranking.",
        "",
        "| Case | Direction | Class | Before | After | Move |",
        "|---|---|---|---|---|---|",
    ]
    for old, new in zip(before.outcomes, after.outcomes, strict=True):
        change = reciprocal_rank(new.ranks) - reciprocal_rank(old.ranks)
        move = "up" if change > 0 else "down" if change < 0 else "same"
        moves[move] += 1
        lines.append(
            f"| {old.case_id} | {old.direction} | {old.query_class}"
            f" | {_ranks(old.ranks)} | {_ranks(new.ranks)} | {move} |"
        )
    lines += [
        "",
        f"- Moved up: {moves['up']}; moved down: {moves['down']};"
        f" unchanged: {moves['same']}.",
        "",
        "## Latency",
        "",
        "Wall time per case. It changes from run to run.",
        "",
        f"- Reranker device: {comparison.device}.",
        f"- Requests to the reranker: {comparison.requests}"
        f" for {len(after.outcomes)} cases.",
        "",
        "| Step | Median ms | Slowest ms |",
        "|---|---:|---:|",
    ]
    for name, report in (("retrieval", before), ("reranking", after)):
        times = [outcome.elapsed_seconds * 1000 for outcome in report.outcomes]
        median = statistics.median(times) if times else 0.0
        slowest = max(times, default=0.0)
        lines.append(f"| {name} | {median:.1f} | {slowest:.1f} |")
    return render_comparison((before, after)) + "\n".join(lines) + "\n"


def render_report(report: DatasetReport) -> str:
    """Return the report as Markdown: found counts per slice, then every case."""
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
    lines += [
        "",
        "## By case",
        "",
        "| Case | Direction | Class | Ranks | Degraded |",
        "|---|---|---|---|---|",
    ]
    for outcome in report.outcomes:
        degraded = "yes" if outcome.degraded else "no"
        lines.append(
            f"| {outcome.case_id} | {outcome.direction} | {outcome.query_class}"
            f" | {_ranks(outcome.ranks)} | {degraded} |"
        )
    return "\n".join(lines) + "\n"


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
    sparse = next((r.sparse for r in reports if r.sparse is not None), None)
    if sparse is not None:
        lines.append(
            f"- Sparse model: {sparse.provider} {sparse.model},"
            f" vocabulary of {sparse.vocabulary_size} tokens."
        )
    if any(len(report.branches) > 1 for report in reports):
        lines.append(f"- Fusion: Reciprocal Rank Fusion, k = {DEFAULT_RRF_K}.")
    reranker = next((r.reranker for r in reports if r.reranker is not None), None)
    if reranker is not None:
        lines.append(f"- Reranker: {reranker.provider} {reranker.model}.")
    return lines


def _label(report: DatasetReport) -> str:
    label = " + ".join(report.branches)
    return label if report.reranker is None else f"{label}, reranked"


def _ranks(ranks: tuple[int | None, ...]) -> str:
    return ", ".join("missed" if rank is None else str(rank) for rank in ranks)


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


def _reranker_identity(reranker: Reranker) -> RerankerIdentity:
    # Read once here, so a reranker that cannot name itself is refused before
    # anything is written.
    try:
        identity = reranker.identity
    except Exception:
        identity = None
    if type(identity) is not RerankerIdentity:
        raise DatasetRunRefused("the reranker must have a RerankerIdentity")
    return identity


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        # Otherwise the last copy wins, and a reviewer reads the other one.
        raise ValueError("a key appears twice in one object")
    return dict(pairs)


def _no_constant(name: str) -> None:
    raise ValueError("NaN and Infinity are not JSON")


_FIELDS = frozenset(
    name
    for model in (_DatasetFile, _CaseFile, _ReferenceFile)
    for name in model.model_fields
)


def _describe(error: ValidationError) -> str:
    # The message names the place and the rule, never a value. An unknown
    # key is a value too, so only known field names and indexes are shown.
    first = error.errors(include_input=False, include_url=False)[0]
    parts = [
        str(part) if isinstance(part, int) or part in _FIELDS else "<unknown>"
        for part in first["loc"]
    ]
    return f"{'.'.join(parts) or 'file'}: {first['msg']}"


def _dataset(parsed: _DatasetFile) -> EvaluationDataset:
    if not _is_name(parsed.name):
        raise InvalidDataset("name must be lowercase words joined by hyphens")
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for index, item in enumerate(parsed.cases):
        if not _is_name(item.id):
            raise InvalidDataset(
                f"cases.{index}.id must be lowercase words joined by hyphens"
            )
        if item.id in seen:
            raise InvalidDataset(f"case {item.id} appears more than once")
        seen.add(item.id)
        cases.append(_case(item))
    _check_coverage(cases)
    return EvaluationDataset(
        name=parsed.name, version=parsed.version, cases=tuple(cases)
    )


def _case(item: _CaseFile) -> EvaluationCase:
    question = item.question
    # Word search refuses a Question with no letter or digit, such as one
    # made of dashes, and that refusal would fail the whole run.
    if not any(c.isalnum() for c in question) or len(question) > MAX_QUERY_CHARACTERS:
        raise InvalidDataset(
            f"case {item.id}: the question must be 1 to"
            f" {MAX_QUERY_CHARACTERS} characters with a letter or digit"
        )
    # PostgreSQL text cannot hold NUL. The JSON parser already refuses a
    # lone surrogate, which UTF-8 cannot hold.
    if "\x00" in question:
        raise InvalidDataset(f"case {item.id}: the question must be valid text")
    relevant = tuple(_reference(item.id, ref) for ref in item.relevant)
    ineligible = tuple(_reference(item.id, ref) for ref in item.ineligible)
    if any(ref.folder is not Folder.PUBLIC for ref in relevant):
        raise InvalidDataset(f"case {item.id}: relevant evidence must be in public/")
    if any(ref.folder is not Folder.RESTRICTED for ref in ineligible):
        raise InvalidDataset(
            f"case {item.id}: ineligible evidence must be in restricted/"
        )
    if len(set(relevant + ineligible)) != len(relevant) + len(ineligible):
        raise InvalidDataset(f"case {item.id} names the same Chunk twice")
    return EvaluationCase(
        case_id=item.id,
        question=question,
        question_language=Language(item.question_language),
        evidence_language=Language(item.evidence_language),
        query_class=QueryClass(item.query_class),
        relevant=relevant,
        ineligible=ineligible,
    )


def _reference(case_id: str, item: _ReferenceFile) -> ChunkReference:
    folder, _, external_id = item.document.partition("/")
    if (
        folder not in set(Folder)
        or len(item.document) > MAX_PATH_CHARACTERS
        or _PATH.fullmatch(external_id) is None
    ):
        raise InvalidDataset(
            f"case {case_id}: a document must be public/<path> or"
            " restricted/<path>, in lowercase letters, digits, and . _ -"
        )
    if not item.heading.strip():
        raise InvalidDataset(f"case {case_id}: a heading must not be blank")
    return ChunkReference(Folder(folder), external_id, item.heading)


def _check_coverage(cases: list[EvaluationCase]) -> None:
    counts = Counter(case.direction for case in cases)
    counts.update(str(case.query_class) for case in cases)
    required = [*DIRECTIONS, QueryClass.IDENTIFIER, QueryClass.MORPHOLOGY]
    for name in required:
        if counts[str(name)] < MIN_CASES_PER_SLICE:
            raise InvalidDataset(
                f"slice {name} needs at least {MIN_CASES_PER_SLICE} cases"
            )
    if not any(case.ineligible for case in cases):
        raise InvalidDataset("at least one case must name ineligible evidence")


def _check_branches(searches: Mapping[RetrievalBranch, BranchSearch]) -> None:
    # Checked here, not only in `retrieve_hybrid`, so a wrong setting is
    # refused before the corpus is written.
    if not isinstance(cast(object, searches), Mapping) or not searches:
        raise DatasetRunRefused("at least one branch is required")
    for branch, search in searches.items():
        if type(branch) is not RetrievalBranch or not callable(search):
            raise DatasetRunRefused(
                "every branch must map a RetrievalBranch to a search"
            )


def _is_name(value: str) -> bool:
    return len(value) <= MAX_NAME_CHARACTERS and _NAME.fullmatch(value) is not None


async def _read_folder(root: Path, folder: Folder) -> list[NormalizedDocument]:
    source = DirectorySource(root, access=SourceAccess(public=True))
    documents: list[NormalizedDocument] = []
    checkpoint: str | None = None
    while True:
        try:
            page = await source.fetch_page(checkpoint=checkpoint, page_size=PAGE_SIZE)
        except SourceError:
            raise InvalidDataset(
                f"the corpus folder {folder}/ cannot be read"
            ) from None
        for record in page.records:
            outcome = normalize(record, source_id=SOURCE_IDS[folder])
            if isinstance(outcome, Quarantined):
                raise InvalidDataset(
                    f"{folder}/{record.external_id} cannot be ingested:"
                    f" {outcome.reason}"
                )
            documents.append(outcome)
        if page.next_checkpoint is None:
            return documents
        checkpoint = page.next_checkpoint


async def _ingest(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    observed_at: datetime,
    *,
    embedding: EmbeddingModelIdentity | None,
) -> None:
    async with runtime.transaction() as connection:
        # Held until commit, so a second run started at the same moment
        # waits here, then finds these Sources and is refused.
        await connection.execute(
            sa.text("LOCK TABLE sources IN SHARE ROW EXCLUSIVE MODE")
        )
        if await connection.scalar(sa.select(sa.exists().select_from(sources))):
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
            await connection.execute(
                sa.insert(sources).values(
                    source_id=source_id.value,
                    kind="local_directory",
                    location=f"/datasets/{dataset.name}/{folder}",
                    display_name=f"{dataset.name} {folder}",
                    enabled=True,
                    created_at=observed_at,
                    updated_at=observed_at,
                )
            )
    for folder, source_id in SOURCE_IDS.items():
        # Only public/ is readable; restricted/ is granted to nobody.
        access = SourceAccess(public=folder is Folder.PUBLIC)
        summary = await synchronize_source(
            runtime,
            DirectorySource(corpus / folder, access=access),
            source_id=source_id,
            observed_at=observed_at,
        )
        if summary.quarantined:
            raise InvalidDataset(f"a Document in {folder}/ could not be ingested")


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
    sparse: SparseModelIdentity | None = None,
) -> DatasetReport:
    outcomes = [await _run_case(case, searches, limit) for case in dataset.cases]
    return DatasetReport(
        name=dataset.name,
        version=dataset.version,
        limit=limit,
        branches=tuple(branch for branch in RetrievalBranch if branch in searches),
        outcomes=tuple(outcomes),
        embedding=embedding if RetrievalBranch.DENSE in searches else None,
        sparse=sparse if RetrievalBranch.SPARSE in searches else None,
    )


async def _run_case(
    case: EvaluationCase,
    searches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
) -> CaseOutcome:
    result, elapsed = await _search_case(case, searches, limit)
    return _outcome(case, result.candidates, result.degraded, elapsed)


async def _search_case(
    case: EvaluationCase,
    searches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
) -> tuple[HybridRetrieval, float]:
    """Run one case's search; return the result and its wall time in seconds."""
    result: HybridRetrieval | None = None
    unexpected = "no result"
    started = time.perf_counter()
    try:
        result = await retrieve_hybrid(
            case.question,
            context=READER,
            branches=searches,
            limit=limit,
            timeout_seconds=CASE_TIMEOUT_SECONDS,
            request_id=uuid5(_REQUEST_IDS, case.case_id),
            language=case.question_language,
        )
    except HybridRetrievalUnavailable as error:
        failures = ", ".join(
            f"{b.branch} {b.error or b.status}" for b in error.branches
        )
        raise CaseUnanswered(
            f"case {case.case_id}: no branch answered ({failures})"
        ) from None
    except Exception as error:
        # A bug in a branch arrives in an ExceptionGroup whose message may
        # repeat the Question. Only the error types leave this function.
        unexpected = _error_names(error)
    elapsed = time.perf_counter() - started
    # Raised outside the `except`, so no linked error carries the Question.
    if result is None:
        raise CaseFailed(f"case {case.case_id}: the search raised {unexpected}")
    return result, elapsed


async def _rerank_case(
    case: EvaluationCase,
    reranker: Reranker,
    result: HybridRetrieval,
    identity: RerankerIdentity,
) -> RerankResult:
    """Rerank one case's fused Candidates, and only those, with one model."""
    reranked: RerankResult | None = None
    failure = "no result"
    try:
        reranked = await rerank_candidates(
            reranker,
            case.question,
            result.candidates,
            request_id=uuid5(_REQUEST_IDS, f"rerank {case.case_id}"),
            timeout_seconds=RERANK_TIMEOUT_SECONDS,
        )
    except Exception as error:
        # The reranker is outside code, and its message may quote a text.
        failure = _error_names(error)
    # Raised outside the `except`, so no linked error carries a text.
    if reranked is None:
        raise CaseFailed(f"case {case.case_id}: reranking raised {failure}")
    # The report names one reranker; scores of another must not mix in.
    if reranked.identity != identity:
        raise CaseFailed(f"case {case.case_id}: the reranker changed its identity")
    return reranked


def _outcome(
    case: EvaluationCase,
    candidates: Sequence[Candidate],
    degraded: bool,
    elapsed: float,
) -> CaseOutcome:
    """Record where each relevant Chunk ranked; refuse any restricted one."""
    ranks: dict[tuple[str, str | None], int] = {}
    for candidate in candidates:
        if candidate.source_id != SOURCE_IDS[Folder.PUBLIC]:
            raise IneligibleChunkRetrieved(
                f"case {case.case_id} returned a Chunk the reader may not see"
            )
        ranks.setdefault(
            (candidate.external_id, candidate.heading_path), candidate.rank
        )
    return CaseOutcome(
        case_id=case.case_id,
        direction=case.direction,
        query_class=case.query_class,
        ranks=tuple(ranks.get((ref.external_id, ref.heading)) for ref in case.relevant),
        degraded=degraded,
        elapsed_seconds=elapsed,
    )
