"""Hybrid retrieval against the real database, with both real branches.

The dense branch embeds the question with a fake model and searches the
stored vectors; the lexical branch searches the stored words. Both run on
their own connections from one runtime.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.pool import QueuePool

from fakes import FakeEmbeddingModel
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, EmbeddingSpaceId, SourceId, UserId
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.embeddings import (
    insert_chunk_embeddings,
    load_embedding_space_id,
)
from knowledge_service.persistence.schema import embedding_spaces
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    BranchRequest,
    BranchResult,
    BranchSearch,
    BranchStatus,
    HybridRetrieval,
    dense_branch,
    lexical_branch,
    retrieve_hybrid,
)
from knowledge_service.settings import Settings

DENSE = RetrievalBranch.DENSE
LEXICAL = RetrievalBranch.LEXICAL
SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000046")
OBSERVED_AT = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
READER = UserId.parse("00000000-0000-0000-0000-0000000000f1")
OTHER_USER = UserId.parse("00000000-0000-0000-0000-0000000000f2")
QUESTION = "parental leave"
# Tags the slow query, so the test can look for it on the server.
SLOW_QUERY = "SELECT pg_sleep(30) /* hybrid-slow-branch */"

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    model = FakeEmbeddingModel(dimensions=3)
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE.value})
        await connection.execute(
            sa.insert(embedding_spaces).values(
                space_id=EmbeddingSpaceId.new().value,
                provider=model.identity.provider,
                model=model.identity.model,
                dimensions=model.identity.dimensions,
                is_current=True,
                created_at=OBSERVED_AT,
            )
        )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def a_document(
    runtime: DatabaseRuntime,
    name: str,
    *,
    text: str,
    vector: Sequence[float],
    readers: Sequence[Subject] = (Subject.public(),),
) -> DocumentId:
    """Store a Document and give each of its Chunks the same vector."""
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version="v1",
            media_type="text/plain",
            content=text.encode(),
            access=SourceAccess(public=True),
            metadata=SourceMetadata(
                title="Handbook", modified_at=OBSERVED_AT, size_bytes=1
            ),
        ),
        source_id=SOURCE,
    )
    assert isinstance(outcome, NormalizedDocument)
    reconciled = await reconcile_document(
        runtime, outcome, readers=readers, observed_at=OBSERVED_AT
    )
    document_id = reconciled.document.document_id
    model = FakeEmbeddingModel(dimensions=3)
    async with runtime.transaction() as connection:
        space_id = await load_embedding_space_id(connection, model.identity)
        assert space_id is not None
        stored = await load_chunks(connection, document_id=document_id)
        await insert_chunk_embeddings(
            connection,
            space_id=space_id,
            dimensions=model.identity.dimensions,
            vectors={chunk.chunk_id: vector for chunk in stored},
            created_at=OBSERVED_AT,
        )
    return document_id


async def question_vector() -> tuple[float, ...]:
    batch = await FakeEmbeddingModel(dimensions=3).embed(
        [QUESTION], request_id=UUID(int=1), timeout_seconds=1.0
    )
    return tuple(batch.vectors[0])


async def retrieve(
    branches: dict[RetrievalBranch, BranchSearch],
    *,
    reader: UserId = READER,
    timeout_seconds: float = 5.0,
) -> HybridRetrieval:
    return await retrieve_hybrid(
        QUESTION,
        context=AuthorizationContext(user_id=reader),
        branches=branches,
        limit=10,
        timeout_seconds=timeout_seconds,
        request_id=UUID(int=2),
    )


@pytest.mark.integration
async def test_both_real_branches_search_as_the_reader(
    runtime: DatabaseRuntime,
) -> None:
    near = await question_vector()
    leave = await a_document(
        runtime, "leave.txt", text="Parental leave policy for staff.", vector=near
    )
    secret = await a_document(
        runtime,
        "secret.txt",
        text="Parental leave plans before the announcement.",
        vector=near,
        readers=(Subject.user(OTHER_USER),),
    )
    branches: dict[RetrievalBranch, BranchSearch] = {
        DENSE: dense_branch(runtime, FakeEmbeddingModel(dimensions=3)),
        LEXICAL: lexical_branch(runtime),
    }

    seen = await retrieve(branches)
    other = await retrieve(branches, reader=OTHER_USER)

    assert [b.status for b in seen.branches] == [BranchStatus.SUCCEEDED] * 2
    assert {c.document_id for c in seen.candidates} == {leave}
    assert [p.branch for p in seen.candidates[0].contributions] == [DENSE, LEXICAL]
    assert {c.document_id for c in other.candidates} == {leave, secret}
    found_secret = next(c for c in other.candidates if c.document_id == secret)
    assert [p.branch for p in found_secret.contributions] == [DENSE, LEXICAL]


def sleeping_branch(runtime: DatabaseRuntime) -> BranchSearch:
    """A branch whose query outlasts any deadline in these tests."""

    async def search(request: BranchRequest) -> BranchResult:
        async with runtime.transaction() as connection:
            await connection.execute(sa.text(SLOW_QUERY))
        raise AssertionError("the slow query was expected to be cancelled")

    return search


async def running_slow_queries(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        found = await connection.execute(
            sa.text(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE state = 'active' AND query = :query"
            ),
            {"query": SLOW_QUERY},
        )
        return int(found.scalar_one())


@pytest.mark.integration
async def test_a_slow_query_is_cancelled_on_the_server_at_the_deadline(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "leave.txt", text="Parental leave policy.", vector=(1.0, 0.0, 0.0)
    )
    branches: dict[RetrievalBranch, BranchSearch] = {
        DENSE: dense_branch(runtime, FakeEmbeddingModel(dimensions=3)),
        LEXICAL: sleeping_branch(runtime),
    }
    loop = asyncio.get_running_loop()
    started = loop.time()

    result = await retrieve(branches, timeout_seconds=1.0)

    assert loop.time() - started < 10
    report = {b.branch: b.status for b in result.branches}
    assert report == {DENSE: BranchStatus.SUCCEEDED, LEXICAL: BranchStatus.TIMED_OUT}
    assert len(result.candidates) == 1
    # The server may take a moment to act on the cancel request.
    for _ in range(50):
        if await running_slow_queries(runtime) == 0:
            break
        await asyncio.sleep(0.1)
    assert await running_slow_queries(runtime) == 0
    assert cast(QueuePool, runtime.engine.pool).checkedout() == 0


@pytest.mark.integration
async def test_a_model_the_current_space_does_not_hold_fails_only_dense(
    runtime: DatabaseRuntime,
) -> None:
    leave = await a_document(
        runtime, "leave.txt", text="Parental leave policy.", vector=(1.0, 0.0, 0.0)
    )
    branches: dict[RetrievalBranch, BranchSearch] = {
        DENSE: dense_branch(runtime, FakeEmbeddingModel(dimensions=4)),
        LEXICAL: lexical_branch(runtime),
    }

    result = await retrieve(branches)

    assert [(b.status, b.error) for b in result.branches] == [
        (BranchStatus.FAILED, "NoCurrentEmbeddingSpace"),
        (BranchStatus.SUCCEEDED, None),
    ]
    assert {c.document_id for c in result.candidates} == {leave}
    assert result.degraded


@pytest.mark.integration
async def test_dense_holds_no_connection_while_it_waits_for_the_embedding(
    runtime: DatabaseRuntime,
) -> None:
    leave = await a_document(
        runtime, "leave.txt", text="Parental leave policy.", vector=(1.0, 0.0, 0.0)
    )
    answer = asyncio.Event()
    model = FakeEmbeddingModel(dimensions=3, wait_for=answer)
    pool = cast(QueuePool, runtime.engine.pool)
    task = asyncio.create_task(retrieve({DENSE: dense_branch(runtime, model)}))
    # Wait until the dense branch is inside the embedding call.
    for _ in range(500):
        if model.batches:
            break
        await asyncio.sleep(0.01)
    assert model.batches

    held_while_embedding = pool.checkedout()
    answer.set()
    result = await task

    assert held_while_embedding == 0
    assert {c.document_id for c in result.candidates} == {leave}
    assert pool.checkedout() == 0
