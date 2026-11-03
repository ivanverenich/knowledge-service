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
- **Eligible.** A Candidate from `restricted/` raises
  `IneligibleChunkRetrieved` and stops the run, however relevant it is.
- **Quiet.** Reports and errors name cases by ID and give ranks only; they
  never hold a Question or Document text.
"""

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import SourceId, UserId
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
from knowledge_service.persistence.schema import sources
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    BranchSearch,
    HybridRetrievalUnavailable,
    lexical_branch,
    retrieve_hybrid,
)
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
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
    await _ingest(runtime, dataset, corpus, observed_at)
    outcomes = [await _run_case(case, searches, limit) for case in dataset.cases]
    return DatasetReport(
        name=dataset.name,
        version=dataset.version,
        limit=limit,
        branches=tuple(branch for branch in RetrievalBranch if branch in searches),
        outcomes=tuple(outcomes),
    )


def render_report(report: DatasetReport) -> str:
    """Return the report as Markdown: found counts per slice, then every case."""
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
    lines += [
        "",
        "## By case",
        "",
        "| Case | Direction | Class | Ranks | Degraded |",
        "|---|---|---|---|---|",
    ]
    for outcome in report.outcomes:
        ranks = ", ".join(
            "missed" if rank is None else str(rank) for rank in outcome.ranks
        )
        degraded = "yes" if outcome.degraded else "no"
        lines.append(
            f"| {outcome.case_id} | {outcome.direction} | {outcome.query_class}"
            f" | {ranks} | {degraded} |"
        )
    return "\n".join(lines) + "\n"


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
) -> None:
    async with runtime.transaction() as connection:
        # Held until commit, so a second run started at the same moment
        # waits here, then finds these Sources and is refused.
        await connection.execute(
            sa.text("LOCK TABLE sources IN SHARE ROW EXCLUSIVE MODE")
        )
        if await connection.scalar(sa.select(sa.exists().select_from(sources))):
            raise DatasetRunRefused("the dataset runs only on an empty database")
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


async def _run_case(
    case: EvaluationCase,
    searches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
) -> CaseOutcome:
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
    ranks: dict[tuple[str, str | None], int] = {}
    for candidate in result.candidates:
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
        degraded=result.degraded,
    )
