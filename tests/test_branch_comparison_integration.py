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
