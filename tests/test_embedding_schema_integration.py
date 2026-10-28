"""Real PostgreSQL tests for the pgvector schema of Chunk embeddings."""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.deletions import (
    purge_document_chunks,
    tombstone_missing,
)
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import SourceAccess, SourceRecord
from knowledge_service.persistence.schema import (
    chunk_embeddings,
    chunks,
    embedding_spaces,
)
from knowledge_service.settings import Settings

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
OBSERVED_AT = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
HANDBOOK = "# Handbook\n\nWelcome to the team.\n"

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
    try:
        yield runtime
    finally:
        await runtime.dispose()


def markdown(text: str) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id="handbook.md",
            version="v1",
            media_type="text/markdown",
            content=text.encode(),
            access=SourceAccess(public=True),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def a_chunk(runtime: DatabaseRuntime, text: str = HANDBOOK) -> UUID:
    """Publish the handbook and return the ID of its one Chunk."""
    await reconcile_document(
        runtime, markdown(text), readers=[Subject.public()], observed_at=OBSERVED_AT
    )
    async with runtime.transaction() as connection:
        return (await connection.execute(sa.select(chunks.c.chunk_id))).scalar_one()


async def add_space(
    connection: AsyncConnection,
    *,
    dimensions: int,
    model: str = "test-embedding",
    is_current: bool = False,
    provider: str = "test",
) -> UUID:
    space_id = uuid4()
    await connection.execute(
        sa.insert(embedding_spaces).values(
            space_id=space_id,
            provider=provider,
            model=model,
            dimensions=dimensions,
            is_current=is_current,
            created_at=OBSERVED_AT,
        )
    )
    return space_id


async def add_embedding(
    connection: AsyncConnection,
    *,
    chunk_id: UUID,
    space_id: UUID,
    vector: Sequence[float],
    dimensions: int | None = None,
) -> None:
    await connection.execute(
        sa.insert(chunk_embeddings).values(
            chunk_id=chunk_id,
            space_id=space_id,
            dimensions=len(vector) if dimensions is None else dimensions,
            embedding=list(vector),
            created_at=OBSERVED_AT,
        )
    )


async def stored_vectors(runtime: DatabaseRuntime) -> dict[UUID, list[float]]:
    """Read every stored vector by space.

    pgvector stores 32-bit floats, so tests use values such as 0.125 that 32
    bits hold exactly; 0.1 would not compare equal after a binary round trip.
    """
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(chunk_embeddings.c.space_id, chunk_embeddings.c.embedding)
        )
        return {space_id: [float(x) for x in vector] for space_id, vector in rows}


@pytest.mark.integration
async def test_a_vector_of_its_space_dimensions_is_stored_and_read_back(
    runtime: DatabaseRuntime,
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        space_id = await add_space(connection, dimensions=3, is_current=True)
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=space_id, vector=[0.25, -0.5, 1.0]
        )

    assert await stored_vectors(runtime) == {space_id: [0.25, -0.5, 1.0]}


@pytest.mark.integration
async def test_a_vector_of_another_length_is_refused(runtime: DatabaseRuntime) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        space_id = await add_space(connection, dimensions=3)

    with pytest.raises(IntegrityError, match="ck_chunk_embeddings_dimensions"):
        async with runtime.transaction() as connection:
            await add_embedding(
                connection,
                chunk_id=chunk_id,
                space_id=space_id,
                vector=[0.1, 0.2, 0.3, 0.4],
                dimensions=3,
            )
    # Claiming the vector's own length does not help: the space says 3.
    with pytest.raises(IntegrityError, match="fk_chunk_embeddings_space_dimensions"):
        async with runtime.transaction() as connection:
            await add_embedding(
                connection,
                chunk_id=chunk_id,
                space_id=space_id,
                vector=[0.1, 0.2, 0.3, 0.4],
            )

    assert await stored_vectors(runtime) == {}


@pytest.mark.integration
@pytest.mark.parametrize("value", [float("nan"), float("inf")], ids=["nan", "infinite"])
async def test_a_vector_that_is_not_finite_is_refused(
    runtime: DatabaseRuntime, value: float
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        space_id = await add_space(connection, dimensions=2)

    with pytest.raises(DataError):
        async with runtime.transaction() as connection:
            await add_embedding(
                connection, chunk_id=chunk_id, space_id=space_id, vector=[0.1, value]
            )


@pytest.mark.integration
async def test_two_spaces_keep_their_vectors_while_the_current_one_switches(
    runtime: DatabaseRuntime,
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        old = await add_space(connection, dimensions=3, model="old", is_current=True)
        new = await add_space(connection, dimensions=2, model="new")
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=old, vector=[0.125, 0.25, 0.375]
        )
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=new, vector=[0.5, 0.75]
        )

    # Switching is two updates in one transaction: clear the old, set the new.
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(embedding_spaces)
            .where(embedding_spaces.c.space_id == old)
            .values(is_current=False)
        )
        await connection.execute(
            sa.update(embedding_spaces)
            .where(embedding_spaces.c.space_id == new)
            .values(is_current=True)
        )
        current = (
            await connection.execute(
                sa.select(embedding_spaces.c.space_id).where(
                    embedding_spaces.c.is_current
                )
            )
        ).scalar_one()

    assert current == new
    assert await stored_vectors(runtime) == {
        old: [0.125, 0.25, 0.375],
        new: [0.5, 0.75],
    }


@pytest.mark.integration
async def test_only_one_space_can_be_current(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await add_space(connection, dimensions=3, model="old", is_current=True)

    with pytest.raises(IntegrityError, match="uq_embedding_spaces_current"):
        async with runtime.transaction() as connection:
            await add_space(connection, dimensions=2, model="new", is_current=True)


@pytest.mark.integration
async def test_one_chunk_has_at_most_one_vector_per_space(
    runtime: DatabaseRuntime,
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        space_id = await add_space(connection, dimensions=2)
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=space_id, vector=[0.1, 0.2]
        )

    with pytest.raises(IntegrityError, match="chunk_embeddings_pkey"):
        async with runtime.transaction() as connection:
            await add_embedding(
                connection, chunk_id=chunk_id, space_id=space_id, vector=[0.3, 0.4]
            )


@pytest.mark.integration
async def test_a_new_chunk_run_removes_the_vectors_of_the_old_chunks(
    runtime: DatabaseRuntime,
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        space_id = await add_space(connection, dimensions=2, is_current=True)
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=space_id, vector=[0.1, 0.2]
        )

    await reconcile_document(
        runtime,
        markdown("# Handbook\n\nWelcome aboard, everyone.\n"),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )

    # The changed text replaced the Chunk, and its vector went with it.
    assert await stored_vectors(runtime) == {}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("provider", "model", "dimensions", "constraint"),
    [
        ("test", "m", 0, "ck_embedding_spaces_dimensions"),
        ("test", "m", 16001, "ck_embedding_spaces_dimensions"),
        (" ", "m", 3, "ck_embedding_spaces_identity"),
        (" test", "m", 3, "ck_embedding_spaces_identity"),
        ("test", "", 3, "ck_embedding_spaces_identity"),
    ],
    ids=[
        "no-dimensions",
        "too-many-dimensions",
        "blank-provider",
        "padded-provider",
        "blank-model",
    ],
)
async def test_an_impossible_space_is_refused(
    runtime: DatabaseRuntime,
    provider: str,
    model: str,
    dimensions: int,
    constraint: str,
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        async with runtime.transaction() as connection:
            await add_space(
                connection, dimensions=dimensions, model=model, provider=provider
            )


@pytest.mark.integration
async def test_the_same_model_identity_is_one_space(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await add_space(connection, dimensions=3)

    with pytest.raises(IntegrityError, match="uq_embedding_spaces_identity"):
        async with runtime.transaction() as connection:
            await add_space(connection, dimensions=3)


@pytest.mark.integration
async def test_deleting_a_space_removes_only_its_vectors(
    runtime: DatabaseRuntime,
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        old = await add_space(connection, dimensions=2, model="old")
        new = await add_space(connection, dimensions=2, model="new", is_current=True)
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=old, vector=[0.5, 1.0]
        )
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=new, vector=[1.5, 2.0]
        )

    async with runtime.transaction() as connection:
        await connection.execute(
            sa.delete(embedding_spaces).where(embedding_spaces.c.space_id == old)
        )

    assert await stored_vectors(runtime) == {new: [1.5, 2.0]}


@pytest.mark.integration
async def test_a_space_cannot_change_its_dimensions_while_it_holds_vectors(
    runtime: DatabaseRuntime,
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        space_id = await add_space(connection, dimensions=2)
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=space_id, vector=[0.5, 1.0]
        )

    with pytest.raises(IntegrityError, match="fk_chunk_embeddings_space_dimensions"):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.update(embedding_spaces)
                .where(embedding_spaces.c.space_id == space_id)
                .values(dimensions=3)
            )


@pytest.mark.integration
async def test_purging_a_deleted_document_removes_its_vectors(
    runtime: DatabaseRuntime,
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        space_id = await add_space(connection, dimensions=2, is_current=True)
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=space_id, vector=[0.5, 1.0]
        )
    (tombstone,) = await tombstone_missing(
        runtime,
        source_id=SOURCE_ID,
        seen=set(),
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )

    await purge_document_chunks(
        runtime, job_id=tombstone.purge_job_id, now=OBSERVED_AT + timedelta(hours=2)
    )

    assert await stored_vectors(runtime) == {}


# PostgreSQL's own rendering of the rules this task adds. The drift check does
# not compare CHECK bodies, index predicates, or vector dimensions, so a change
# to the migration must show up here; tests/test_embedding_schema.py pins the
# declarations in persistence.schema the same way.
STORED_RULES = {
    "ck_chunk_embeddings_dimensions": "CHECK ((vector_dims(embedding) = dimensions))",
    "ck_embedding_spaces_dimensions": (
        "CHECK (((dimensions >= 1) AND (dimensions <= 16000)))"
    ),
    "ck_embedding_spaces_identity": (
        "CHECK (((provider <> ''::text) AND (provider = btrim(provider))"
        " AND (model <> ''::text) AND (model = btrim(model))))"
    ),
    "fk_chunk_embeddings_space_dimensions": (
        "FOREIGN KEY (space_id, dimensions) REFERENCES"
        " embedding_spaces(space_id, dimensions) ON DELETE CASCADE"
    ),
    "uq_embedding_spaces_current": (
        "CREATE UNIQUE INDEX uq_embedding_spaces_current ON public.embedding_spaces"
        " USING btree (is_current) WHERE is_current"
    ),
    "embedding": "vector",
}

STORED_RULES_QUERY = sa.text(
    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
    " WHERE conname = ANY(:names)"
    " UNION ALL SELECT indexname, indexdef FROM pg_indexes"
    " WHERE indexname = ANY(:names)"
    " UNION ALL SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute"
    " WHERE attrelid = 'chunk_embeddings'::regclass AND attname = 'embedding'"
)


@pytest.mark.integration
async def test_the_migrated_database_holds_exactly_these_rules(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            STORED_RULES_QUERY, {"names": list(STORED_RULES)}
        )
        stored = {str(name): str(definition) for name, definition in rows}

    assert stored == STORED_RULES
