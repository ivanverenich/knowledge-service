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
- **Compared with LLM reranking.** `compare_llm_reranking` does the same
  with a RankingModel (see `retrieval.llm_reranking`), which orders each
  case's top Candidates in one request under a strict budget. A failed or
  rejected ranking keeps retrieval's order for that case and is counted,
  not raised. `render_llm_rerank_comparison` adds the fallbacks, the
  billed tokens, and the estimated cost.
- **Compared across strategies.** `compare_reranking_strategies` searches
  every case once, then gives the same Candidates to a cross-encoder and
  to a RankingModel, so the strategies differ only in their order. Every
  case is cross-encoded before any case is sent to the language model,
  so a failing cross-encoder stops the run before anything is billed.
  `render_strategy_comparison` puts no reranker, the cross-encoder, and
  the language model side by side: quality, moves, fallbacks, latency,
  and cost.
- **Compared with query rewriting.** `load_rewrite_dataset` joins the
  labelled rewrite cases (see `retrieval.rewrite_cases`) with the Chunks
  that answer them, and `compare_rewriting` searches every case as typed
  and, where its class may be rewritten, again with the checked rewrite
  (see `retrieval.query_rewriting`). Only the Question and its earlier
  Questions are sent to the rewriter. `retrieval.rewrite_evaluation`
  scores the gain per class and decides which classes may be rolled out.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
  MRR for every language direction, every question language (`en->*`,
  `uk->*`), and every query class (see `retrieval.ranking_metrics`).
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
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knowledge_service.access import AuthorizationContext
from knowledge_service.contracts import Usage
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
from knowledge_service.pricing import ModelPrice, estimate_cost
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
from knowledge_service.retrieval.llm_reranking import (
    LLMRerankResult,
    RankingBudget,
    RankingModel,
    rerank_with_llm,
)
from knowledge_service.retrieval.query_rewriting import (
    RewriteModel,
    RewriteOutcome,
    RewritePolicy,
    RewriteResult,
    RewriterIdentity,
    rewrite_question,
)
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
from knowledge_service.retrieval.rewrite_cases import RewriteCase, RewriteCaseSet
from knowledge_service.retrieval.rewrite_classes import REWRITABLE, RewriteClass
from knowledge_service.retrieval.sparse_search import (
    build_sparse_index,
    sparse_branch,
)
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES

DATASET_DIRECTORY = Path("datasets/bilingual-seed")
CASES_FILE = "cases.json"
REWRITE_RETRIEVAL_FILE = "rewrite-retrieval.json"
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
LLM_RERANK_TIMEOUT_SECONDS = 30.0
REWRITE_TIMEOUT_SECONDS = 30.0

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
# The language a Question is asked in, whatever the evidence's language.
QUESTION_LANGUAGES = tuple(f"{asked}->*" for asked in _LANGUAGES)

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
class LLMRerankComparison:
    """Fused retrieval before and after LLM reranking, and what it cost.

    `retrieved` and `reranked` hold the same cases; a reranked outcome's
    `elapsed_seconds` is the ranking time alone. `fallbacks` names, per
    case that kept retrieval's order, the error type. `usages` holds every
    billed answer, and `cost_usd` their estimated cost, or None when no
    price is known for one of them.
    """

    retrieved: DatasetReport
    reranked: DatasetReport
    fallbacks: tuple[tuple[str, str], ...]
    usages: tuple[Usage, ...]
    cost_usd: Decimal | None


@dataclass(frozen=True, slots=True)
class StrategyComparison:
    """No reranker, a cross-encoder, and a language model on the same Candidates.

    `retrieved`, `cross_encoder`, and `llm` hold the same cases in the same
    order; a reranked outcome's `elapsed_seconds` is that reranker's time
    alone. `device` and `requests` describe the cross-encoder, as in
    `RerankComparison`; `fallbacks`, `usages`, and `cost_usd` describe the
    language model, as in `LLMRerankComparison`. `partial` lists, per case
    the language model ranked without falling back, how many Candidates
    fit its budget out of how many retrieval returned, when not all did.
    """

    retrieved: DatasetReport
    cross_encoder: DatasetReport
    llm: DatasetReport
    device: str
    requests: int
    fallbacks: tuple[tuple[str, str], ...]
    usages: tuple[Usage, ...]
    cost_usd: Decimal | None
    partial: tuple[tuple[str, int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class RewriteDataset:
    """Labelled rewrite cases and the Chunks that answer them, in one order.

    `dataset.cases[i]` and `labelled[i]` are the same case: the first holds
    the Chunks, the second the earlier Questions and the label.
    """

    dataset: EvaluationDataset
    labelled: tuple[RewriteCase, ...]


@dataclass(frozen=True, slots=True)
class RewriteCaseOutcome:
    """One case searched as typed and, if it was rewritten, as rewritten.

    `after` equals `before` when the case was not rewritten. `fallback`
    names the error type when a rewrite was asked for and not used.
    """

    case_id: str
    rewrite_class: RewriteClass
    outcome: RewriteOutcome
    fallback: str | None
    before: CaseOutcome
    after: CaseOutcome


@dataclass(frozen=True, slots=True)
class RewriteComparison:
    """Every case as typed and as rewritten, and what the rewrites cost."""

    name: str
    version: int
    limit: int
    embedding: EmbeddingModelIdentity
    rewriter: RewriterIdentity
    cases: tuple[RewriteCaseOutcome, ...]
    usages: tuple[Usage, ...]
    cost_usd: Decimal | None


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


class _RewriteCaseFile(_File):
    id: str
    evidence_language: Literal["en", "uk"]
    query_class: Literal["plain", "identifier", "morphology"]
    relevant: list[_ReferenceFile] = Field(min_length=1)
    ineligible: list[_ReferenceFile] = Field(default_factory=list[_ReferenceFile])


class _RewriteDatasetFile(_File):
    name: str
    version: int = Field(ge=1)
    cases: list[_RewriteCaseFile] = Field(min_length=1)


def load_dataset(path: Path) -> EvaluationDataset:
    """Read and check a cases file; raise `InvalidDataset` if it breaks a rule."""
    try:
        parsed = _DatasetFile.model_validate(_read_json(path))
    except ValidationError as error:
        raise InvalidDataset(_describe(error)) from None
    return _dataset(parsed)


def load_rewrite_dataset(path: Path, labelled: RewriteCaseSet) -> RewriteDataset:
    """Join each labelled rewrite case with the Chunks that answer it.

    Raises `InvalidDataset` for a file that breaks a rule of `load_dataset`,
    for an ID used twice or with no labelled case, or for a case whose
    Question word search would refuse.
    """
    try:
        parsed = _RewriteDatasetFile.model_validate(_read_json(path))
    except ValidationError as error:
        raise InvalidDataset(_describe(error)) from None
    if not _is_name(parsed.name):
        raise InvalidDataset("name must be lowercase words joined by hyphens")
    labels = {case.case_id: case for case in labelled.cases}
    cases: list[EvaluationCase] = []
    joined: list[RewriteCase] = []
    for index, item in enumerate(parsed.cases):
        label = labels.pop(item.id, None)
        if label is None:
            raise InvalidDataset(
                f"cases.{index}.id names no labelled case, or one used twice"
            )
        joined.append(label)
        cases.append(
            _case(
                _CaseFile(
                    id=item.id,
                    question=label.question,
                    question_language=cast(Literal["en", "uk"], str(label.language)),
                    evidence_language=item.evidence_language,
                    query_class=item.query_class,
                    relevant=item.relevant,
                    ineligible=item.ineligible,
                )
            )
        )
    dataset = EvaluationDataset(
        name=parsed.name, version=parsed.version, cases=tuple(cases)
    )
    return RewriteDataset(dataset=dataset, labelled=tuple(joined))


def _read_json(path: Path) -> object:
    """Read a small, strict JSON file; raise `InvalidDataset` if it is not one."""
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
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_constant=_no_constant,
        )
    except ValueError, RecursionError:
        # UnicodeDecodeError and JSONDecodeError are both ValueErrors.
        raise InvalidDataset("the cases file is not valid UTF-8 JSON") from None


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


async def compare_llm_reranking(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: EmbeddingModel,
    ranker: RankingModel,
    budget: RankingBudget,
    price: ModelPrice | None,
    limit: int = DEFAULT_LIMIT,
) -> LLMRerankComparison:
    """Ingest and embed the corpus once, run every case fused, then LLM-rerank it.

    Each case runs dense and lexical search fused, as `compare_reranking`
    does, and sends that case's top fused Candidates, and nothing else, to
    `ranker` within `budget`. A case whose ranking fails keeps retrieval's
    order and is listed in `fallbacks`. Raises like `compare_reranking`.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    identity = _identity(model)
    ranker_identity = _reranker_identity(ranker)
    await verify_corpus(dataset, corpus)
    await _ingest(runtime, dataset, corpus, observed_at, embedding=identity)
    await _embed_corpus(runtime, model, observed_at)
    searches: Mapping[RetrievalBranch, BranchSearch] = {
        RetrievalBranch.DENSE: dense_branch(runtime, model),
        RetrievalBranch.LEXICAL: lexical_branch(runtime),
    }
    retrieved: list[CaseOutcome] = []
    reranked: list[CaseOutcome] = []
    fallbacks: list[tuple[str, str]] = []
    usages: list[Usage] = []
    for case in dataset.cases:
        result, elapsed = await _search_case(case, searches, limit)
        retrieved.append(_outcome(case, result.candidates, result.degraded, elapsed))
        started = time.perf_counter()
        ordered = await _llm_rerank_case(case, ranker, result, budget, ranker_identity)
        took = time.perf_counter() - started
        if ordered.fallback is not None:
            fallbacks.append((case.case_id, ordered.fallback))
        if ordered.usage is not None:
            usages.append(ordered.usage)
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

    return LLMRerankComparison(
        retrieved=report(retrieved, None),
        reranked=report(reranked, ranker_identity),
        fallbacks=tuple(fallbacks),
        usages=tuple(usages),
        cost_usd=_cost(usages, price),
    )


async def compare_reranking_strategies(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: EmbeddingModel,
    reranker: Reranker,
    device: str,
    ranker: RankingModel,
    budget: RankingBudget,
    price: ModelPrice | None,
    limit: int = DEFAULT_LIMIT,
) -> StrategyComparison:
    """Search every case once, then rerank the same Candidates two ways.

    Each case runs dense and lexical search fused, as `compare_reranking`
    does. Its Candidates, and nothing else, go to `reranker`, and once
    every case is reranked, to `ranker` within `budget`, so the three
    strategies differ only in their order. A cross-encoder failure stops
    the run with `CaseFailed` before any case reaches the language model;
    a failed ranking keeps retrieval's order and is listed in `fallbacks`.
    Raises like `compare_llm_reranking`; `DatasetRunRefused` also when
    both rerankers name the same identity, or when `limit` is above the
    number of Candidates `budget` lets the language model see.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    if limit > budget.max_candidates:
        raise DatasetRunRefused("limit must not exceed the budget's max_candidates")
    identity = _identity(model)
    cross_identity = _reranker_identity(reranker)
    llm_identity = _reranker_identity(ranker)
    # The report tells the strategies apart by these names.
    if cross_identity == llm_identity:
        raise DatasetRunRefused("the two rerankers must have different identities")
    await verify_corpus(dataset, corpus)
    await _ingest(runtime, dataset, corpus, observed_at, embedding=identity)
    await _embed_corpus(runtime, model, observed_at)
    searches: Mapping[RetrievalBranch, BranchSearch] = {
        RetrievalBranch.DENSE: dense_branch(runtime, model),
        RetrievalBranch.LEXICAL: lexical_branch(runtime),
    }
    results: list[HybridRetrieval] = []
    retrieved: list[CaseOutcome] = []
    cross: list[CaseOutcome] = []
    requests = 0
    for case in dataset.cases:
        result, elapsed = await _search_case(case, searches, limit)
        results.append(result)
        retrieved.append(_outcome(case, result.candidates, result.degraded, elapsed))
        started = time.perf_counter()
        scored = await _rerank_case(case, reranker, result, cross_identity)
        took = time.perf_counter() - started
        requests += scored.requests
        cross.append(_outcome(case, scored.candidates, result.degraded, took))
    # Only now, so a cross-encoder failure leaves nothing billed.
    llm: list[CaseOutcome] = []
    fallbacks: list[tuple[str, str]] = []
    usages: list[Usage] = []
    partial: list[tuple[str, int, int]] = []
    for case, result in zip(dataset.cases, results, strict=True):
        started = time.perf_counter()
        ranked = await _llm_rerank_case(case, ranker, result, budget, llm_identity)
        took = time.perf_counter() - started
        given = len(result.candidates)
        if ranked.fallback is not None:
            fallbacks.append((case.case_id, ranked.fallback))
        elif ranked.ranked < given:
            partial.append((case.case_id, ranked.ranked, given))
        if ranked.usage is not None:
            usages.append(ranked.usage)
        llm.append(_outcome(case, ranked.candidates, result.degraded, took))
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

    return StrategyComparison(
        retrieved=report(retrieved, None),
        cross_encoder=report(cross, cross_identity),
        llm=report(llm, llm_identity),
        device=device,
        requests=requests,
        fallbacks=tuple(fallbacks),
        usages=tuple(usages),
        cost_usd=_cost(usages, price),
        partial=tuple(partial),
    )


async def compare_rewriting(
    runtime: DatabaseRuntime,
    rewrite: RewriteDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: EmbeddingModel,
    rewriter: RewriteModel,
    price: ModelPrice | None,
    limit: int = DEFAULT_LIMIT,
) -> RewriteComparison:
    """Ingest and embed the corpus once, then search each case as typed and rewritten.

    Every rewritable class is switched on, so the run measures what each
    class would gain. Each case runs dense and lexical search fused with
    the Question as typed. If its rewrite is used, the case runs again with
    the rewrite; otherwise its ranks stay the same. Only the Question and
    its earlier Questions reach `rewriter`, never a Chunk. Raises like
    `compare_branches`; a rewriter that raises an error outside its port,
    or changes its identity, raises `CaseFailed`.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    # Both searches and the rewriter must see one Question per case.
    if type(rewrite) is not RewriteDataset or [
        (case.case_id, case.question, case.question_language)
        for case in rewrite.dataset.cases
    ] != [(case.case_id, case.question, case.language) for case in rewrite.labelled]:
        raise DatasetRunRefused("the rewrite dataset must come from its loader")
    identity = _identity(model)
    rewriter_identity = _rewriter_identity(rewriter)
    dataset = rewrite.dataset
    await verify_corpus(dataset, corpus)
    await _ingest(runtime, dataset, corpus, observed_at, embedding=identity)
    await _embed_corpus(runtime, model, observed_at)
    searches: Mapping[RetrievalBranch, BranchSearch] = {
        RetrievalBranch.DENSE: dense_branch(runtime, model),
        RetrievalBranch.LEXICAL: lexical_branch(runtime),
    }
    policy = RewritePolicy(REWRITABLE)
    outcomes: list[RewriteCaseOutcome] = []
    usages: list[Usage] = []
    for case, label in zip(dataset.cases, rewrite.labelled, strict=True):
        result, elapsed = await _search_case(case, searches, limit)
        before = _outcome(case, result.candidates, result.degraded, elapsed)
        rewritten = await _rewrite_case(label, rewriter, policy, rewriter_identity)
        after = before
        if rewritten.outcome is RewriteOutcome.REWRITTEN:
            searched = replace(case, question=rewritten.search_text)
            result, elapsed = await _search_case(searched, searches, limit)
            after = _outcome(searched, result.candidates, result.degraded, elapsed)
        if rewritten.usage is not None:
            usages.append(rewritten.usage)
        outcomes.append(
            RewriteCaseOutcome(
                case_id=case.case_id,
                rewrite_class=rewritten.plan.rewrite_class,
                outcome=rewritten.outcome,
                fallback=rewritten.fallback,
                before=before,
                after=after,
            )
        )
    return RewriteComparison(
        name=dataset.name,
        version=dataset.version,
        limit=limit,
        embedding=identity,
        rewriter=rewriter_identity,
        cases=tuple(outcomes),
        usages=tuple(usages),
        cost_usd=_cost(usages, price),
    )


def score_slices(report: DatasetReport) -> tuple[SliceScore, ...]:
    """Score every case together, then each direction, each question language,
    then each query class.

    A question-language slice such as `uk->*` holds every case asked in that
    language, `uk->uk` and `uk->en` together: what a change to how
    Questions are read, such as Ukrainian word search, moves first.
    """
    cutoffs = tuple(k for k in RECALL_CUTOFFS if k <= report.limit)
    groups: list[tuple[str, list[CaseOutcome]]] = [("all", list(report.outcomes))]
    groups += [
        (direction, [o for o in report.outcomes if o.direction == direction])
        for direction in DIRECTIONS
    ]
    groups += [
        (asked, [o for o in report.outcomes if o.direction.startswith(asked[:-1])])
        for asked in QUESTION_LANGUAGES
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
    lines = [
        *_delta_lines(before, after),
        "",
        "## Latency",
        "",
        "Wall time per case. It changes from run to run.",
        "",
        f"- Reranker device: {comparison.device}.",
        f"- Requests to the reranker: {comparison.requests}"
        f" for {len(after.outcomes)} cases.",
        "",
        *_latency_lines(before, after),
    ]
    return render_comparison((before, after)) + "\n".join(lines) + "\n"


def render_llm_rerank_comparison(comparison: LLMRerankComparison) -> str:
    """Return the side-by-side report, each case's move, fallbacks, timing, cost.

    A language model may order the same Candidates differently on two runs,
    so unlike the cross-encoder report, two runs may differ above
    "## Latency"; compare them case by case.
    """
    before, after = comparison.retrieved, comparison.reranked
    lines = [
        *_delta_lines(before, after),
        "",
        "## Fallbacks",
        "",
        "Cases that kept retrieval's order, and why.",
        "",
    ]
    if comparison.fallbacks:
        lines += ["| Case | Reason |", "|---|---|"]
        lines += [f"| {case} | {reason} |" for case, reason in comparison.fallbacks]
    else:
        lines.append("- None.")
    usages = comparison.usages
    cost = comparison.cost_usd
    lines += [
        "",
        "## Latency",
        "",
        "Wall time per case. It changes from run to run.",
        "",
        *_latency_lines(before, after),
        "",
        "## Cost",
        "",
        f"- Billed answers: {len(usages)} for {len(after.outcomes)} cases.",
        f"- Input tokens: {sum(u.input_tokens for u in usages)};"
        f" output tokens: {sum(u.output_tokens for u in usages)}.",
        "- Estimated cost: "
        + ("unknown" if cost is None else f"{cost:.6f} USD")
        + ", at the configured list price.",
    ]
    return render_comparison((before, after)) + "\n".join(lines) + "\n"


def render_strategy_comparison(comparison: StrategyComparison) -> str:
    """Return no reranker, the cross-encoder, and the language model side by side.

    Every strategy only reorders the same Candidates, so Recall at the
    run's limit cannot change; quality is shown as Recall@1 and MRR.
    Everything above "## Latency and cost" depends only on the data and the
    models, although the language model may order differently between
    runs and a timeout fallback depends on the run; that last section
    changes from run to run. Raises `ValueError` when the three reports do
    not describe the same cases in the same order, with no reranker, the
    cross-encoder, and the language model in that order.
    """
    _check_strategies(comparison)
    retrieved = comparison.retrieved
    runs = (
        ("none", retrieved),
        ("cross-encoder", comparison.cross_encoder),
        ("LLM", comparison.llm),
    )
    lines = [
        f"# Reranking strategies: {retrieved.name} v{retrieved.version}",
        "",
        *_configuration((retrieved,)),
        f"- Cross-encoder: {_reranker_name(comparison.cross_encoder)}.",
        f"- Language model: {_reranker_name(comparison.llm)}.",
        "- Both rerankers get the same Candidates of each case; the language"
        " model orders those that fit its budget.",
        "",
        *_strategy_quality(runs),
        "",
        *_strategy_cases(runs),
        "",
        "## Fallbacks",
        "",
        "Cases where the language model's ranking was not used, and why.",
        "",
    ]
    if comparison.fallbacks:
        lines += ["| Case | Reason |", "|---|---|"]
        lines += [f"| {case} | {reason} |" for case, reason in comparison.fallbacks]
    else:
        lines.append("- None.")
    lines += [
        "",
        "Cases where only some Candidates fit the language model's budget:",
        "",
    ]
    lines += [
        f"- {case}: {ranked} of {given} ordered; the rest kept retrieval's order."
        for case, ranked, given in comparison.partial
    ] or ["- None."]
    return "\n".join([*lines, "", *_strategy_costs(comparison)]) + "\n"


def _check_strategies(comparison: StrategyComparison) -> None:
    """Refuse three reports that do not line up case by case."""
    runs = (comparison.retrieved, comparison.cross_encoder, comparison.llm)
    first = runs[0]
    for report in runs:
        same = (report.name, report.version, report.limit, report.branches)
        if same != (first.name, first.version, first.limit, first.branches):
            raise ValueError("the three reports must come from one run")
        cases = [o.case_id for o in report.outcomes]
        if cases != [o.case_id for o in first.outcomes]:
            raise ValueError("the three reports must hold the same cases in order")
    cross, llm = comparison.cross_encoder.reranker, comparison.llm.reranker
    if first.reranker is not None or cross is None or llm is None or cross == llm:
        raise ValueError(
            "expected no reranker, then two different rerankers, in that order"
        )


def _strategy_quality(runs: tuple[tuple[str, DatasetReport], ...]) -> list[str]:
    """Recall@1 and MRR per slice, one pair of columns per strategy."""
    lines = [
        "## Quality by slice",
        "",
        "| Slice | Cases | "
        + " | ".join(f"{name} R@1 | {name} MRR" for name, _ in runs)
        + " |",
        "|---|---:|" + "---:|---:|" * len(runs),
    ]
    scored = [score_slices(report) for _, report in runs]
    for row in zip(*scored, strict=True):
        cells = [f"{row[0].name} | {row[0].cases}"]
        cells += [
            f"{_number(dict(score.recall).get(1))} | {_number(score.mrr)}"
            for score in row
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _strategy_cases(runs: tuple[tuple[str, DatasetReport], ...]) -> list[str]:
    """Each case's ranks per strategy, the moves, and where rerankers differ."""
    lines = [
        "## By case",
        "",
        "Where each case's relevant Chunks ranked under each strategy.",
        "",
        "| Case | Direction | Class | " + " | ".join(n for n, _ in runs) + " |",
        "|---|---|---|" + "---|" * len(runs),
    ]
    rows = list(zip(*(report.outcomes for _, report in runs), strict=True))
    for row in rows:
        first = row[0]
        lines.append(
            f"| {first.case_id} | {first.direction} | {first.query_class} | "
            + " | ".join(_ranks(outcome.ranks) for outcome in row)
            + " |"
        )
    lines.append("")
    for index, (name, _) in enumerate(runs[1:], start=1):
        moves = Counter(_move(row[0], row[index]) for row in rows)
        lines.append(
            f"- {name} against none: up {moves['up']}, down {moves['down']},"
            f" same {moves['same']}."
        )
    differ = [row[0].case_id for row in rows if len({o.ranks for o in row[1:]}) > 1]
    lines.append(
        f"- The rerankers rank differently in {len(differ)} of {len(rows)} cases"
        + (": " + ", ".join(differ) + "." if differ else ".")
    )
    return lines


def _strategy_costs(comparison: StrategyComparison) -> list[str]:
    """What each step adds per case in time and money."""
    cases = len(comparison.retrieved.outcomes)
    usages, cost = comparison.usages, comparison.cost_usd
    per_case = "unknown" if cost is None else f"{cost / max(cases, 1):.6f}"
    lines = [
        "## Latency and cost",
        "",
        "Per case. Times and tokens change from run to run.",
        "",
        f"- Cross-encoder device: {comparison.device}.",
        "",
        "| Step | Median ms | Slowest ms | Requests | USD per case |",
        "|---|---:|---:|---:|---:|",
    ]
    steps = (
        ("retrieval", comparison.retrieved, cases, "0"),
        ("cross-encoder", comparison.cross_encoder, comparison.requests, "0"),
        ("LLM", comparison.llm, len(usages), per_case),
    )
    for name, report, requests, usd in steps:
        median, slowest = _times(report)
        lines.append(f"| {name} | {median:.1f} | {slowest:.1f} | {requests} | {usd} |")
    lines += [
        "",
        "- The cross-encoder runs on a local server; it has no bill per request.",
        f"- LLM tokens: {sum(u.input_tokens for u in usages)} in,"
        f" {sum(u.output_tokens for u in usages)} out; estimated cost: "
        + ("unknown" if cost is None else f"{cost:.6f} USD")
        + ", at the configured list price.",
    ]
    return lines


def _reranker_name(report: DatasetReport) -> str:
    reranker = report.reranker
    return "none" if reranker is None else f"{reranker.provider} {reranker.model}"


def _move(old: CaseOutcome, new: CaseOutcome) -> str:
    """Whether a case's best relevant rank got better, worse, or stayed."""
    change = reciprocal_rank(new.ranks) - reciprocal_rank(old.ranks)
    return "up" if change > 0 else "down" if change < 0 else "same"


def _times(report: DatasetReport) -> tuple[float, float]:
    """The median and slowest wall time per case, in milliseconds."""
    times = [outcome.elapsed_seconds * 1000 for outcome in report.outcomes]
    return statistics.median(times) if times else 0.0, max(times, default=0.0)


def _delta_lines(before: DatasetReport, after: DatasetReport) -> list[str]:
    """Each case's ranks before and after reranking, and the moves counted."""
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
        move = _move(old, new)
        moves[move] += 1
        lines.append(
            f"| {old.case_id} | {old.direction} | {old.query_class}"
            f" | {_ranks(old.ranks)} | {_ranks(new.ranks)} | {move} |"
        )
    lines += [
        "",
        f"- Moved up: {moves['up']}; moved down: {moves['down']};"
        f" unchanged: {moves['same']}.",
    ]
    return lines


def _latency_lines(before: DatasetReport, after: DatasetReport) -> list[str]:
    """Median and slowest wall time per case, for retrieval and reranking."""
    lines = ["| Step | Median ms | Slowest ms |", "|---|---:|---:|"]
    for name, report in (("retrieval", before), ("reranking", after)):
        median, slowest = _times(report)
        lines.append(f"| {name} | {median:.1f} | {slowest:.1f} |")
    return lines


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


def _reranker_identity(reranker: Reranker | RankingModel) -> RerankerIdentity:
    # Read once here, so a reranker that cannot name itself is refused before
    # anything is written.
    try:
        identity = reranker.identity
    except Exception:
        identity = None
    if type(identity) is not RerankerIdentity:
        raise DatasetRunRefused("the reranker must have a RerankerIdentity")
    return identity


def _rewriter_identity(rewriter: RewriteModel) -> RewriterIdentity:
    # Read once here, so a rewriter that cannot name itself is refused
    # before anything is written.
    try:
        identity = rewriter.identity
    except Exception as error:
        raise DatasetRunRefused(
            f"the rewriter has no identity: {type(error).__name__}"
        ) from None
    if type(identity) is not RewriterIdentity:
        raise DatasetRunRefused("the rewriter has no identity")
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
    for model in (
        _DatasetFile,
        _CaseFile,
        _ReferenceFile,
        _RewriteDatasetFile,
        _RewriteCaseFile,
    )
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


async def _llm_rerank_case(
    case: EvaluationCase,
    ranker: RankingModel,
    result: HybridRetrieval,
    budget: RankingBudget,
    identity: RerankerIdentity,
) -> LLMRerankResult:
    """Rank one case's fused Candidates, and only those, with one model."""
    ranked: LLMRerankResult | None = None
    failure = "no result"
    try:
        ranked = await rerank_with_llm(
            ranker,
            case.question,
            result.candidates,
            budget=budget,
            request_id=uuid5(_REQUEST_IDS, f"llm rerank {case.case_id}"),
            timeout_seconds=LLM_RERANK_TIMEOUT_SECONDS,
        )
    except Exception as error:
        # Only a bug or a broken request gets here; its message may quote text.
        failure = _error_names(error)
    # Raised outside the `except`, so no linked error carries a text.
    if ranked is None:
        raise CaseFailed(f"case {case.case_id}: LLM reranking raised {failure}")
    if ranked.identity != identity:
        raise CaseFailed(f"case {case.case_id}: the reranker changed its identity")
    return ranked


async def _rewrite_case(
    label: RewriteCase,
    rewriter: RewriteModel,
    policy: RewritePolicy,
    identity: RewriterIdentity,
) -> RewriteResult:
    """Rewrite one case's Question, and only its conversation, with one model."""
    rewritten: RewriteResult | None = None
    failure = "no result"
    try:
        rewritten = await rewrite_question(
            rewriter,
            label.question,
            language=label.language,
            history=label.history,
            policy=policy,
            request_id=uuid5(_REQUEST_IDS, f"rewrite {label.case_id}"),
            timeout_seconds=REWRITE_TIMEOUT_SECONDS,
        )
    except Exception as error:
        # Only a bug or a broken request gets here; its message may quote text.
        failure = _error_names(error)
    # Raised outside the `except`, so no linked error carries a text.
    if rewritten is None:
        raise CaseFailed(f"case {label.case_id}: rewriting raised {failure}")
    if rewritten.identity != identity:
        raise CaseFailed(f"case {label.case_id}: the rewriter changed its identity")
    return rewritten


def _cost(usages: Sequence[Usage], price: ModelPrice | None) -> Decimal | None:
    """Sum the estimated cost of every billed answer; None if any is unknown."""
    if price is None:
        return None
    prices = {price.price_id: price}
    total = Decimal(0)
    for usage in usages:
        cost = estimate_cost(usage, prices)
        if cost is None:
            return None
        total += cost
    return total


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
