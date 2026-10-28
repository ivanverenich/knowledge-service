"""Real PostgreSQL tests for vector search filtered by eligibility in SQL."""

import math
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import EmbeddingModelIdentity
from knowledge_service.identifiers import (
    ChunkId,
    DocumentId,
    EmbeddingSpaceId,
    GroupId,
    SourceId,
    UserId,
)
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import SourceAccess, SourceRecord
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.documents import load_document_by_id
from knowledge_service.persistence.embeddings import (
    insert_chunk_embeddings,
    load_embedding_space_id,
)
from knowledge_service.persistence.schema import chunk_embeddings, embedding_spaces
from knowledge_service.retrieval.vector_search import (
    DistanceMetric,
    NoCurrentEmbeddingSpace,
    VectorCandidate,
    VectorSearchResult,
    search_similar_chunks,
)
from knowledge_service.settings import Settings

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000012")
OBSERVED_AT = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
IDENTITY = EmbeddingModelIdentity(provider="fake", model="fake-embedding", dimensions=3)
OTHER_IDENTITY = EmbeddingModelIdentity(
    provider="fake", model="older-embedding", dimensions=3
)
READER = UserId.parse("00000000-0000-0000-0000-0000000000a1")
OTHER_USER = UserId.parse("00000000-0000-0000-0000-0000000000a2")
READERS_GROUP = GroupId.parse("00000000-0000-0000-0000-0000000000b1")
OTHER_GROUP = GroupId.parse("00000000-0000-0000-0000-0000000000b2")
CONTEXT = AuthorizationContext(user_id=READER, group_ids=frozenset({READERS_GROUP}))
QUERY = (1.0, 0.0, 0.0)

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
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
    await a_space(runtime, IDENTITY, is_current=True)
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def a_space(
    runtime: DatabaseRuntime, identity: EmbeddingModelIdentity, *, is_current: bool
) -> EmbeddingSpaceId:
    space_id = EmbeddingSpaceId.new()
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.insert(embedding_spaces).values(
                space_id=space_id.value,
                provider=identity.provider,
                model=identity.model,
                dimensions=identity.dimensions,
                is_current=is_current,
                created_at=OBSERVED_AT,
            )
        )
    return space_id


def plain_text(external_id: str) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id=external_id,
            version="v1",
            media_type="text/plain",
            content=f"The {external_id} policy.".encode(),
            access=SourceAccess(public=True),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def a_document(
    runtime: DatabaseRuntime,
    external_id: str,
    *,
    readers: Sequence[Subject],
    vector: Sequence[float],
    space: EmbeddingModelIdentity = IDENTITY,
) -> DocumentId:
    """Store a one-Chunk Document and give its Chunk the given vector.

    The vector is stored at length 1, as every published vector is.
    """
    reconciled = await reconcile_document(
        runtime, plain_text(external_id), readers=readers, observed_at=OBSERVED_AT
    )
    document_id = reconciled.document.document_id
    async with runtime.transaction() as connection:
        space_id = await load_embedding_space_id(connection, space)
        assert space_id is not None
        (chunk,) = await load_chunks(connection, document_id=document_id)
        await insert_chunk_embeddings(
            connection,
            space_id=space_id,
            dimensions=space.dimensions,
            vectors={chunk.chunk_id: vector},
            created_at=OBSERVED_AT,
        )
    return document_id


async def search_result(
    runtime: DatabaseRuntime,
    *,
    context: AuthorizationContext = CONTEXT,
    limit: int = 10,
    identity: EmbeddingModelIdentity = IDENTITY,
    query: tuple[float, ...] = QUERY,
) -> VectorSearchResult:
    async with runtime.transaction() as connection:
        return await search_similar_chunks(
            connection, identity=identity, query=query, context=context, limit=limit
        )


async def search(
    runtime: DatabaseRuntime,
    *,
    context: AuthorizationContext = CONTEXT,
    limit: int = 10,
    identity: EmbeddingModelIdentity = IDENTITY,
    query: tuple[float, ...] = QUERY,
) -> tuple[VectorCandidate, ...]:
    result = await search_result(
        runtime, context=context, limit=limit, identity=identity, query=query
    )
    return result.candidates


def documents_of(candidates: Sequence[VectorCandidate]) -> list[DocumentId]:
    return [candidate.document_id for candidate in candidates]


@pytest.mark.integration
async def test_nearest_chunks_come_first_with_provenance_and_distance(
    runtime: DatabaseRuntime,
) -> None:
    far = await a_document(
        runtime, "far.txt", readers=[Subject.public()], vector=[0.0, 1.0, 0.0]
    )
    near = await a_document(
        runtime, "near.txt", readers=[Subject.public()], vector=[1.0, 0.0, 0.0]
    )
    middle = await a_document(
        runtime, "middle.txt", readers=[Subject.public()], vector=[1.0, 1.0, 0.0]
    )

    found = await search(runtime)

    assert documents_of(found) == [near, middle, far]
    assert [round(c.distance, 6) for c in found] == [0.0, 0.292893, 1.0]
    async with runtime.transaction() as connection:
        document = await load_document_by_id(connection, document_id=near)
        (chunk,) = await load_chunks(connection, document_id=near)
    assert document is not None
    first = found[0]
    assert first.chunk_id == chunk.chunk_id
    assert first.source_id == SOURCE_ID
    assert first.external_id == "near.txt"
    assert first.title == document.title
    assert first.content_version == chunk.content_version
    assert first.ordinal == chunk.ordinal
    assert first.heading_path == chunk.heading_path
    assert first.source_anchor == chunk.source_anchor
    assert first.text == chunk.text


@pytest.mark.integration
async def test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked(
    runtime: DatabaseRuntime,
) -> None:
    # The hidden Chunk is an exact match. If it were dropped only after
    # ranking, limit=1 would leave nothing to return.
    await a_document(
        runtime,
        "hidden.txt",
        readers=[Subject.user(OTHER_USER)],
        vector=[1.0, 0.0, 0.0],
    )
    granted = await a_document(
        runtime,
        "granted.txt",
        readers=[Subject.user(READER)],
        vector=[0.0, 1.0, 0.0],
    )

    found = await search(runtime, limit=1)

    assert documents_of(found) == [granted]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("readers", "visible"),
    [
        ([Subject.public()], True),
        ([Subject.user(READER)], True),
        ([Subject.group(READERS_GROUP)], True),
        ([Subject.user(OTHER_USER), Subject.group(READERS_GROUP)], True),
        ([Subject.user(OTHER_USER)], False),
        ([Subject.group(OTHER_GROUP)], False),
        # The reader's own UUID, granted as a group, is another subject.
        ([Subject.group(GroupId(READER.value))], False),
        ([Subject.user(UserId(READERS_GROUP.value))], False),
        ([], False),
    ],
)
async def test_only_a_matching_access_grant_makes_a_chunk_eligible(
    runtime: DatabaseRuntime,
    readers: list[Subject],
    visible: bool,
) -> None:
    document_id = await a_document(
        runtime, "policy.txt", readers=readers, vector=[1.0, 0.0, 0.0]
    )

    found = await search(runtime)

    assert documents_of(found) == ([document_id] if visible else [])


@pytest.mark.integration
async def test_a_reader_without_groups_sees_only_public_and_own_grants(
    runtime: DatabaseRuntime,
) -> None:
    public = await a_document(
        runtime, "public.txt", readers=[Subject.public()], vector=[1.0, 0.0, 0.0]
    )
    await a_document(
        runtime,
        "group.txt",
        readers=[Subject.group(READERS_GROUP)],
        vector=[1.0, 0.0, 0.0],
    )

    found = await search(runtime, context=AuthorizationContext(user_id=READER))

    assert documents_of(found) == [public]


@pytest.mark.integration
async def test_revoked_and_tombstoned_documents_disappear_at_once(
    runtime: DatabaseRuntime,
) -> None:
    revoked = await a_document(
        runtime, "revoked.txt", readers=[Subject.public()], vector=[1.0, 0.0, 0.0]
    )
    tombstoned = await a_document(
        runtime, "gone.txt", readers=[Subject.public()], vector=[1.0, 0.0, 0.0]
    )
    kept = await a_document(
        runtime, "kept.txt", readers=[Subject.public()], vector=[0.0, 1.0, 0.0]
    )
    assert set(documents_of(await search(runtime))) == {revoked, tombstoned, kept}

    later = OBSERVED_AT + timedelta(hours=1)
    # Only the readers change, so the Chunk and its vector stay.
    await reconcile_document(
        runtime, plain_text("revoked.txt"), readers=[], observed_at=later
    )
    await tombstone_missing(
        runtime,
        source_id=SOURCE_ID,
        seen={"revoked.txt", "kept.txt"},
        observed_at=later,
    )

    assert documents_of(await search(runtime)) == [kept]


@pytest.mark.integration
async def test_only_vectors_of_the_current_space_are_compared(
    runtime: DatabaseRuntime,
) -> None:
    await a_space(runtime, OTHER_IDENTITY, is_current=False)
    await a_document(
        runtime,
        "old-model.txt",
        readers=[Subject.public()],
        vector=[1.0, 0.0, 0.0],
        space=OTHER_IDENTITY,
    )
    current = await a_document(
        runtime, "new-model.txt", readers=[Subject.public()], vector=[0.0, 1.0, 0.0]
    )

    assert documents_of(await search(runtime)) == [current]


@pytest.mark.integration
async def test_a_query_from_a_model_that_is_not_current_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    await a_space(runtime, OTHER_IDENTITY, is_current=False)

    with pytest.raises(NoCurrentEmbeddingSpace):
        await search(runtime, identity=OTHER_IDENTITY)


@pytest.mark.integration
async def test_equal_distances_are_ordered_by_chunk_id(
    runtime: DatabaseRuntime,
) -> None:
    for name in ("a.txt", "b.txt", "c.txt"):
        await a_document(
            runtime, name, readers=[Subject.public()], vector=[1.0, 0.0, 0.0]
        )

    found = await search(runtime)

    chunk_ids: list[ChunkId] = [candidate.chunk_id for candidate in found]
    assert chunk_ids == sorted(chunk_ids, key=lambda chunk_id: chunk_id.value)
    assert [c.chunk_id for c in await search(runtime)] == chunk_ids


@pytest.mark.integration
async def test_a_reader_with_many_groups_is_one_parameter(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(
        runtime,
        "policy.txt",
        readers=[Subject.group(READERS_GROUP)],
        vector=[1.0, 0.0, 0.0],
    )
    many = frozenset({READERS_GROUP} | {GroupId(uuid4()) for _ in range(70_000)})

    found = await search(
        runtime, context=AuthorizationContext(user_id=READER, group_ids=many)
    )

    assert documents_of(found) == [document_id]


@pytest.mark.integration
@pytest.mark.parametrize(
    "query",
    [
        # pgvector computes in 32-bit floats: these once raised a DataError,
        # returned NaN distances, or returned wrong ones.
        (1e39, 0.0, 0.0),
        (3e38, 3e38, 0.0),
        (1e20, 1e20, 0.0),
        (1e-30, 1e-30, 0.0),
        (1e-46, 0.0, 0.0),
        (1e-320, 0.0, 0.0),
    ],
)
async def test_distance_depends_on_direction_not_length(
    runtime: DatabaseRuntime, query: tuple[float, ...]
) -> None:
    await a_document(
        runtime, "slanted.txt", readers=[Subject.public()], vector=[1.0, 0.5, 0.0]
    )
    await a_document(
        runtime, "upright.txt", readers=[Subject.public()], vector=[0.0, 1.0, 0.0]
    )
    direction = tuple(1.0 if value else 0.0 for value in query)

    expected = await search(runtime, query=direction)
    found = await search(runtime, query=query)

    assert [c.chunk_id for c in found] == [c.chunk_id for c in expected]
    assert [c.distance for c in found] == pytest.approx(
        [c.distance for c in expected], abs=1e-6
    )
    assert all(math.isfinite(c.distance) for c in found)


# Five public Documents around QUERY = (1, 0, 0): two exact matches that tie,
# then one at 45 degrees, one at a right angle, and one opposite.
CORPUS = {
    "tie-a.txt": [1.0, 0.0, 0.0],
    "tie-b.txt": [2.0, 0.0, 0.0],
    "diagonal.txt": [1.0, 1.0, 0.0],
    "right-angle.txt": [0.0, 1.0, 0.0],
    "opposite.txt": [-1.0, 0.0, 0.0],
}
DISTANCES = {
    "tie-a.txt": 0.0,
    "tie-b.txt": 0.0,
    "diagonal.txt": 1 - 1 / math.sqrt(2),
    "right-angle.txt": 1.0,
    "opposite.txt": 2.0,
}


async def a_corpus(runtime: DatabaseRuntime, names: Sequence[str]) -> list[str]:
    """Store the named Documents in this order; return them in result order.

    The result order is by distance, then by Chunk ID.
    """
    chunk_ids: dict[str, ChunkId] = {}
    for name in names:
        document_id = await a_document(
            runtime, name, readers=[Subject.public()], vector=CORPUS[name]
        )
        async with runtime.transaction() as connection:
            (chunk,) = await load_chunks(connection, document_id=document_id)
        chunk_ids[name] = chunk.chunk_id
    return sorted(names, key=lambda name: (DISTANCES[name], chunk_ids[name].value))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("limit", "returned", "truncated"),
    [
        (1, 1, True),
        (2, 2, True),
        (3, 3, True),
        (4, 4, True),
        (5, 5, False),
        (6, 5, False),
        (100, 5, False),
    ],
)
async def test_top_k_returns_the_nearest_k_in_order(
    runtime: DatabaseRuntime, limit: int, returned: int, truncated: bool
) -> None:
    expected = await a_corpus(runtime, list(CORPUS))

    result = await search_result(runtime, limit=limit)

    assert result.metric is DistanceMetric.COSINE
    assert result.limit == limit
    assert result.truncated is truncated
    assert [c.external_id for c in result.candidates] == expected[:returned]
    assert [c.rank for c in result.candidates] == list(range(1, returned + 1))
    assert [c.distance for c in result.candidates] == pytest.approx(
        [DISTANCES[name] for name in expected[:returned]], abs=1e-6
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "names",
    [
        list(CORPUS),
        list(reversed(CORPUS)),
        ["diagonal.txt", "tie-b.txt", "opposite.txt", "tie-a.txt", "right-angle.txt"],
    ],
)
async def test_the_order_depends_only_on_distance_and_chunk_id(
    runtime: DatabaseRuntime, names: list[str]
) -> None:
    expected = await a_corpus(runtime, names)

    first = await search_result(runtime)
    second = await search_result(runtime)

    assert [c.external_id for c in first.candidates] == expected
    assert first == second


@pytest.mark.integration
async def test_distances_stay_between_zero_and_two(runtime: DatabaseRuntime) -> None:
    await a_corpus(runtime, list(CORPUS))

    result = await search_result(runtime)

    assert result.candidates[0].distance == 0.0
    assert result.candidates[-1].distance == 2.0
    assert all(0.0 <= c.distance <= 2.0 for c in result.candidates)


@pytest.mark.integration
async def test_no_eligible_chunk_is_an_empty_result(runtime: DatabaseRuntime) -> None:
    await a_document(
        runtime,
        "private.txt",
        readers=[Subject.user(OTHER_USER)],
        vector=[1.0, 0.0, 0.0],
    )

    result = await search_result(runtime, limit=1)

    assert result.candidates == ()
    assert result.truncated is False


@pytest.mark.integration
async def test_truncated_counts_only_chunks_the_reader_may_see(
    runtime: DatabaseRuntime,
) -> None:
    # Hidden Chunks beyond the limit must not show through `truncated`.
    for name in ("hidden-1.txt", "hidden-2.txt", "hidden-3.txt"):
        await a_document(
            runtime, name, readers=[Subject.user(OTHER_USER)], vector=[1.0, 0.0, 0.0]
        )
    visible = await a_document(
        runtime, "visible.txt", readers=[Subject.public()], vector=[0.0, 1.0, 0.0]
    )

    result = await search_result(runtime, limit=1)

    assert documents_of(result.candidates) == [visible]
    assert result.truncated is False


@pytest.mark.integration
async def test_a_chunk_whose_vector_has_no_direction_is_never_a_candidate(
    runtime: DatabaseRuntime,
) -> None:
    # The application refuses to store such a vector, so write the row
    # directly, as a bug or a manual fix could.
    zero = await a_document(
        runtime, "zero.txt", readers=[Subject.public()], vector=[1.0, 0.0, 0.0]
    )
    async with runtime.transaction() as connection:
        (chunk,) = await load_chunks(connection, document_id=zero)
        await connection.execute(
            sa.update(chunk_embeddings)
            .where(chunk_embeddings.c.chunk_id == chunk.chunk_id.value)
            .values(embedding=[0.0, 0.0, 0.0])
        )
    kept = await a_document(
        runtime, "kept.txt", readers=[Subject.public()], vector=[0.0, 1.0, 0.0]
    )

    result = await search_result(runtime, limit=1)

    assert documents_of(result.candidates) == [kept]
    assert result.truncated is False


@pytest.mark.integration
@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        # Once wrong in pgvector's 32-bit floats: squaring overflowed or
        # rounded to zero before vectors were stored at length 1.
        ([3e38, 3e38, 0.0], 1 - 1 / math.sqrt(2)),
        ([2e19, 2e19, 0.0], 1 - 1 / math.sqrt(2)),
        ([1e-30, 1e-30, 0.0], 1 - 1 / math.sqrt(2)),
        ([1e-40, 1e-40, 0.0], 1 - 1 / math.sqrt(2)),
        ([1e-30, 1e-25, 0.0], 1.0 - 1e-5 / math.sqrt(1 + 1e-10)),
    ],
)
async def test_a_stored_vector_of_any_size_gets_its_true_distance(
    runtime: DatabaseRuntime, stored: list[float], expected: float
) -> None:
    await a_document(runtime, "sized.txt", readers=[Subject.public()], vector=stored)

    result = await search_result(runtime)

    (candidate,) = result.candidates
    assert candidate.distance == pytest.approx(expected, abs=1e-6)


@pytest.mark.integration
async def test_a_tiny_sideways_vector_does_not_outrank_a_near_one(
    runtime: DatabaseRuntime,
) -> None:
    near = await a_document(
        runtime, "near.txt", readers=[Subject.public()], vector=[1.0, 0.01, 0.0]
    )
    await a_document(
        runtime, "tiny.txt", readers=[Subject.public()], vector=[1e-30, 1e-25, 0.0]
    )

    result = await search_result(runtime, limit=1)

    assert documents_of(result.candidates) == [near]
    assert result.truncated is True
