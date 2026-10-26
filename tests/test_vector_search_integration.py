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
from knowledge_service.deletions import tombstone_missing
from knowledge_service.embeddings import EmbeddingModelIdentity
from knowledge_service.identifiers import (
    ChunkId,
    DocumentId,
    EmbeddingSpaceId,
    GroupId,
    SourceId,
    UserId,
)
from knowledge_service.normalization import NormalizedDocument, normalize
from knowledge_service.persistence import (
    embedding_spaces,
    insert_chunk_embeddings,
    load_chunks,
    load_document_by_id,
    load_embedding_space_id,
)
from knowledge_service.reconciliation import reconcile_document
from knowledge_service.settings import Settings
from knowledge_service.sources import SourceAccess, SourceRecord
from knowledge_service.vector_search import (
    NoCurrentEmbeddingSpace,
    VectorCandidate,
    search_similar_chunks,
)

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
    """Store a one-Chunk Document and give its Chunk the given vector."""
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


async def search(
    runtime: DatabaseRuntime,
    *,
    context: AuthorizationContext = CONTEXT,
    limit: int = 10,
    identity: EmbeddingModelIdentity = IDENTITY,
    query: tuple[float, ...] = QUERY,
) -> tuple[VectorCandidate, ...]:
    async with runtime.transaction() as connection:
        return await search_similar_chunks(
            connection, identity=identity, query=query, context=context, limit=limit
        )


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
