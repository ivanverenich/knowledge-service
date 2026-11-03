"""The bilingual seed dataset on real PostgreSQL: ingested, run, and quiet."""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.persistence.schema import chunks, documents, sources
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    READER,
    SOURCE_IDS,
    CaseUnanswered,
    DatasetReport,
    DatasetRunRefused,
    EvaluationDataset,
    Folder,
    IneligibleChunkRetrieved,
    load_dataset,
    render_report,
    run_dataset,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import BranchRequest, BranchResult
from knowledge_service.retrieval.lexical_search import (
    InvalidLexicalQuery,
    search_lexical_chunks,
)
from knowledge_service.settings import Settings

OBSERVED_AT = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
# Written into every section of the restricted Document.
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"


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


@dataclass(frozen=True, slots=True)
class FakeCandidate:
    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    external_id: str
    title: str
    heading_path: str | None
    source_anchor: str | None
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class FakeResult:
    candidates: tuple[FakeCandidate, ...]
    truncated: bool = False


@pytest.mark.integration
async def test_every_case_runs_and_the_report_names_no_text(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    report = await run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)

    outcomes = {outcome.case_id: outcome for outcome in report.outcomes}
    assert [o.case_id for o in report.outcomes] == [c.case_id for c in dataset.cases]
    assert report.branches == (RetrievalBranch.LEXICAL,)
    assert not any(outcome.degraded for outcome in report.outcomes)
    # Every word of these Questions is in the relevant section.
    assert outcomes["en-en-expense-deadline"].ranks == (1,)
    assert outcomes["en-en-error-vpn-4031"].ranks == (1,)
    assert outcomes["uk-uk-per-diem-abroad"].ranks == (1,)
    rendered = render_report(report)
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in dataset.cases)


@pytest.mark.integration
async def test_the_restricted_document_is_stored_but_never_found(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    await run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)

    async with runtime.transaction() as connection:
        stored = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(chunks.join(documents))
            .where(documents.c.source_id == SOURCE_IDS[Folder.RESTRICTED].value)
        )
        found = await search_lexical_chunks(
            connection, text=RESTRICTED_MARKER, context=READER, limit=10
        )
    assert stored == 2
    assert found.candidates == ()


@pytest.mark.integration
async def test_a_branch_that_returns_a_restricted_chunk_stops_the_run(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    async def leaking(request: BranchRequest) -> BranchResult:
        return FakeResult(
            candidates=(
                FakeCandidate(
                    rank=1,
                    chunk_id=ChunkId.new(),
                    document_id=DocumentId.new(),
                    source_id=SOURCE_IDS[Folder.RESTRICTED],
                    content_version=ContentVersion(1),
                    ordinal=1,
                    external_id="executive-leave.md",
                    title="Executive annual leave",
                    heading_path="Executive annual leave > Executive days per year",
                    source_anchor=None,
                    text=RESTRICTED_MARKER,
                ),
            )
        )

    with pytest.raises(IneligibleChunkRetrieved) as raised:
        await run_dataset(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            branches={RetrievalBranch.LEXICAL: leaking},
        )
    assert dataset.cases[0].case_id in str(raised.value)
    assert RESTRICTED_MARKER not in str(raised.value)


@pytest.mark.integration
async def test_a_database_that_is_not_empty_is_refused_before_any_write(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    await run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)
    async with runtime.transaction() as connection:
        before = await connection.scalar(sa.select(sa.func.count()).select_from(chunks))

    with pytest.raises(DatasetRunRefused, match="empty database"):
        await run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)

    async with runtime.transaction() as connection:
        after = await connection.scalar(sa.select(sa.func.count()).select_from(chunks))
        source_rows = await connection.scalar(
            sa.select(sa.func.count()).select_from(sources)
        )
    assert after == before
    assert source_rows == 2


@pytest.mark.integration
async def test_a_case_no_branch_answers_raises_a_typed_error(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    async def refusing(request: BranchRequest) -> BranchResult:
        raise InvalidLexicalQuery("refused")

    with pytest.raises(CaseUnanswered, match="InvalidLexicalQuery") as raised:
        await run_dataset(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            branches={RetrievalBranch.LEXICAL: refusing},
        )
    assert dataset.cases[0].case_id in str(raised.value)


@pytest.mark.integration
async def test_two_runs_at_once_give_one_report_and_one_refusal(
    migrated_database: str, dataset: EvaluationDataset
) -> None:
    runtimes = [
        create_database_runtime(Settings(database_url=SecretStr(migrated_database)))
        for _ in range(2)
    ]
    try:
        results = await asyncio.gather(
            *(
                run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)
                for runtime in runtimes
            ),
            return_exceptions=True,
        )
    finally:
        for runtime in runtimes:
            await runtime.dispose()

    kinds = sorted(type(result).__name__ for result in results)
    assert kinds == sorted([DatasetReport.__name__, DatasetRunRefused.__name__])
