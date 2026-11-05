"""The sparse prototype on real PostgreSQL: the seed run, eligibility, failure."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from fakes import FakeSparseModel
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import EmbeddingLimits, EmbeddingUnavailable
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    READER,
    SOURCE_IDS,
    CorpusNotEmbedded,
    EvaluationDataset,
    Folder,
    compare_sparse,
    load_dataset,
    render_sparse_comparison,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.sparse_search import (
    build_sparse_index,
    search_sparse_chunks,
    sparse_search_statement,
)
from knowledge_service.settings import Settings

OBSERVED_AT = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
REQUEST_ID = UUID(int=0x5EED_0010)
# Small batches, so indexing the 21 seed Chunks takes several requests.
LIMITS = EmbeddingLimits(max_inputs=4, max_input_tokens=1_000, max_batch_tokens=4_000)


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


@pytest.mark.integration
async def test_every_case_runs_with_words_and_with_sparse_weights_quietly(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = FakeSparseModel(limits=LIMITS)

    comparison = await compare_sparse(
        runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model
    )

    assert comparison.lexical.branches == (RetrievalBranch.LEXICAL,)
    assert comparison.sparse.branches == (RetrievalBranch.SPARSE,)
    assert comparison.sparse.sparse == model.sparse_identity
    assert comparison.chunks == 21
    assert comparison.weights > 0
    # Every Chunk was indexed, in batches the model's limits allow.
    assert sum(len(batch) for batch in model.batches) == 21 + len(dataset.cases)
    assert all(len(batch) <= LIMITS.max_inputs for batch in model.batches)
    sparse = {o.case_id: o.ranks for o in comparison.sparse.outcomes}
    # The fake shares a token per shared word, so a code is found by itself.
    assert sparse["en-en-error-vpn-4031"] == (1,)
    assert all(o.elapsed_seconds > 0 for o in comparison.sparse.outcomes)
    rendered = render_sparse_comparison(comparison)
    assert "## Cost" in rendered
    assert "## Latency" in rendered
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in dataset.cases)


@pytest.mark.integration
async def test_the_best_scored_chunk_is_restricted_and_still_never_returned(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    model = FakeSparseModel(limits=LIMITS)
    await compare_sparse(runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model)
    index = await build_sparse_index(
        runtime, model, request_id=REQUEST_ID, timeout_seconds=5.0
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
    # The query is the restricted Chunk's own text: every one of its tokens
    # matches, so no other Chunk can score higher.
    (query,) = await model.encode_sparse(
        [restricted_text], request_id=REQUEST_ID, timeout_seconds=5.0
    )
    scores = {
        chunk_id: sum(
            weight * dict(index.postings.get(token, ())).get(chunk_id, 0.0)
            for token, weight in query
        )
        for chunk_id in {c for pairs in index.postings.values() for c, _ in pairs}
    }
    scores = {chunk_id: score for chunk_id, score in scores.items() if score > 0}

    async with runtime.transaction() as connection:
        unfiltered = (
            (
                await connection.execute(
                    sparse_search_statement(
                        scores=scores, eligibility=sa.true(), limit=1
                    )
                )
            )
            .mappings()
            .one()
        )
        found = await search_sparse_chunks(
            connection,
            index=index,
            query=query,
            identity=model.sparse_identity,
            context=READER,
            limit=21,
        )

    # Without the permission check the restricted Chunk ranks first ...
    assert unfiltered["source_id"] == SOURCE_IDS[Folder.RESTRICTED].value
    # ... with it, no restricted Chunk is ranked, counted, or returned.
    assert found.candidates
    assert {c.source_id for c in found.candidates} == {SOURCE_IDS[Folder.PUBLIC]}
    assert [c.rank for c in found.candidates] == list(
        range(1, len(found.candidates) + 1)
    )
    assert found.truncated is False
    assert all(RESTRICTED_MARKER not in c.text for c in found.candidates)


@pytest.mark.integration
async def test_a_model_that_fails_while_indexing_stops_the_run_quietly(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    model = FakeSparseModel(limits=LIMITS, error=EmbeddingUnavailable())

    with pytest.raises(CorpusNotEmbedded) as raised:
        await compare_sparse(
            runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model
        )

    assert "EmbeddingUnavailable" in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    # One batch was tried, and no case ran.
    assert len(model.batches) == 1
