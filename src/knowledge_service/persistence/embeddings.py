"""Store Embedding Spaces, their vectors, and index configurations."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.embeddings.index_configuration import (
    IndexConfiguration,
    IndexConfigurationConflict,
    settings_fingerprint,
)
from knowledge_service.embeddings.migration import EmbeddingSpace, MigrationState
from knowledge_service.embeddings.port import EmbeddingModelIdentity, unit_length
from knowledge_service.identifiers import (
    ChunkId,
    DocumentId,
    EmbeddingSpaceId,
    IndexConfigurationId,
)
from knowledge_service.persistence.schema import (
    chunk_embeddings,
    chunks,
    embedding_migrations,
    embedding_spaces,
    index_configurations,
)


async def load_embedding_space_id(
    connection: AsyncConnection,
    identity: EmbeddingModelIdentity,
) -> EmbeddingSpaceId | None:
    """Return the Embedding Space that holds this model identity's vectors.

    The space's row stays share-locked until the transaction ends, so it
    cannot be deleted or changed while vectors are written into it.
    """
    space_id = await connection.scalar(
        sa.select(embedding_spaces.c.space_id)
        .where(
            embedding_spaces.c.provider == identity.provider,
            embedding_spaces.c.model == identity.model,
            embedding_spaces.c.dimensions == identity.dimensions,
        )
        .with_for_update(read=True)
    )
    return None if space_id is None else EmbeddingSpaceId(cast(UUID, space_id))


async def load_current_embedding_space_id(
    connection: AsyncConnection,
    identity: EmbeddingModelIdentity,
) -> EmbeddingSpaceId | None:
    """Return the current Embedding Space if it holds this model identity."""
    space_id = await connection.scalar(
        sa.select(embedding_spaces.c.space_id).where(
            embedding_spaces.c.provider == identity.provider,
            embedding_spaces.c.model == identity.model,
            embedding_spaces.c.dimensions == identity.dimensions,
            embedding_spaces.c.is_current,
        )
    )
    return None if space_id is None else EmbeddingSpaceId(cast(UUID, space_id))


async def ensure_index_configuration(
    connection: AsyncConnection,
    configuration: IndexConfiguration,
    *,
    created_at: datetime,
) -> IndexConfigurationId:
    """Return the identity of this configuration, storing it if it is new.

    Equal settings always return the identity stored first; any changed
    setting gives a new fingerprint and so a new row. Two callers storing the
    same new configuration at once get one row: the second insert does
    nothing and reads the first one's row. Raises
    `IndexConfigurationConflict` if a stored row has the fingerprint but not
    the settings, which would mean the row was edited. The configuration is
    checked again first, so one altered after it was made is refused.
    """
    configuration = configuration.checked()
    fingerprint = configuration.fingerprint
    settings = configuration.settings()
    inserted = await connection.scalar(
        insert(index_configurations)
        .values(
            configuration_id=IndexConfigurationId.new().value,
            fingerprint=fingerprint,
            settings=settings,
            created_at=created_at,
        )
        .on_conflict_do_nothing(index_elements=[index_configurations.c.fingerprint])
        .returning(index_configurations.c.configuration_id)
    )
    if inserted is not None:
        return IndexConfigurationId(cast(UUID, inserted))
    stored = (
        await connection.execute(
            sa.select(
                index_configurations.c.configuration_id,
                index_configurations.c.settings,
            ).where(index_configurations.c.fingerprint == fingerprint)
        )
    ).one()
    # Hashing the stored JSON also catches 8.0 for 8 or true for 1, which
    # compare equal in Python but are other settings.
    if (
        stored.settings != settings
        or settings_fingerprint(stored.settings) != fingerprint
    ):
        raise IndexConfigurationConflict(
            "a stored configuration has this fingerprint but other settings"
        )
    return IndexConfigurationId(cast(UUID, stored.configuration_id))


async def insert_embedding_space(
    connection: AsyncConnection,
    *,
    space_id: EmbeddingSpaceId,
    identity: EmbeddingModelIdentity,
    is_current: bool,
    created_at: datetime,
) -> None:
    """Store a new Embedding Space for one model identity."""
    await connection.execute(
        sa.insert(embedding_spaces).values(
            space_id=space_id.value,
            provider=identity.provider,
            model=identity.model,
            dimensions=identity.dimensions,
            is_current=is_current,
            created_at=created_at,
        )
    )


def _space_from(record: Mapping[str, object]) -> EmbeddingSpace:
    return EmbeddingSpace(
        space_id=EmbeddingSpaceId(cast(UUID, record["space_id"])),
        identity=EmbeddingModelIdentity(
            provider=cast(str, record["provider"]),
            model=cast(str, record["model"]),
            dimensions=cast(int, record["dimensions"]),
        ),
        is_current=cast(bool, record["is_current"]),
    )


async def load_embedding_space(
    connection: AsyncConnection,
    space_id: EmbeddingSpaceId,
    *,
    for_update: bool = False,
) -> EmbeddingSpace | None:
    """Read one space; `for_update` locks its row until the commit.

    The lock waits for every publication holding the row `FOR SHARE` and
    keeps new ones out, so no vector is written into the space meanwhile.
    """
    statement = sa.select(embedding_spaces).where(
        embedding_spaces.c.space_id == space_id.value
    )
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    return None if record is None else _space_from(dict(record))


async def load_current_embedding_space(
    connection: AsyncConnection, *, for_update: bool = False
) -> EmbeddingSpace | None:
    """Read the space search reads now, if there is one."""
    statement = sa.select(embedding_spaces).where(embedding_spaces.c.is_current)
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    return None if record is None else _space_from(dict(record))


async def space_accepts_writes(
    connection: AsyncConnection, space_id: EmbeddingSpaceId
) -> bool:
    """Whether publication may still write vectors into this space.

    Only the current space, and the target of a migration still
    dual-writing, are ever read again; vectors written anywhere else would
    be lost.
    """
    current = sa.exists().where(
        embedding_spaces.c.space_id == space_id.value, embedding_spaces.c.is_current
    )
    building = sa.exists().where(
        embedding_migrations.c.target_space_id == space_id.value,
        embedding_migrations.c.state == MigrationState.DUAL_WRITING.value,
    )
    return bool(await connection.scalar(sa.select(sa.or_(current, building))))


async def make_embedding_space_current(
    connection: AsyncConnection,
    *,
    retired: EmbeddingSpaceId,
    current: EmbeddingSpaceId,
) -> None:
    """Move "current" from one space to another in the same transaction.

    The unique index on `is_current` is checked row by row, so the old space
    is cleared first. Readers see the old space until the commit and the new
    one after it, never neither.
    """
    await connection.execute(
        sa.update(embedding_spaces)
        .where(embedding_spaces.c.space_id == retired.value)
        .values(is_current=False)
    )
    await connection.execute(
        sa.update(embedding_spaces)
        .where(embedding_spaces.c.space_id == current.value)
        .values(is_current=True)
    )


async def delete_embedding_space(
    connection: AsyncConnection, space_id: EmbeddingSpaceId
) -> None:
    """Delete a space; its vectors go with it."""
    await connection.execute(
        sa.delete(embedding_spaces).where(embedding_spaces.c.space_id == space_id.value)
    )


async def load_embedded_chunk_ids(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
    space_id: EmbeddingSpaceId,
) -> frozenset[ChunkId]:
    """Return the Chunks of a Document that already have a vector in the space."""
    statement = (
        sa.select(chunk_embeddings.c.chunk_id)
        .join(chunks, chunks.c.chunk_id == chunk_embeddings.c.chunk_id)
        .where(
            chunks.c.document_id == document_id.value,
            chunk_embeddings.c.space_id == space_id.value,
        )
    )
    chunk_ids = await connection.scalars(statement)
    return frozenset(ChunkId(cast(UUID, chunk_id)) for chunk_id in chunk_ids)


async def insert_chunk_embeddings(
    connection: AsyncConnection,
    *,
    space_id: EmbeddingSpaceId,
    dimensions: int,
    vectors: Mapping[ChunkId, Sequence[float]],
    created_at: datetime,
) -> None:
    """Store one vector per Chunk in the space, in one statement.

    Each vector is stored scaled to length 1 (see `unit_length`), so search
    distances stay right in pgvector's 32-bit floats; a vector with no
    direction raises `InvalidEmbedding` and nothing is stored. The database
    refuses a vector whose length is not the space's dimensions, and
    pgvector's type needs a list, so each vector is converted here.
    """
    if not vectors:
        return
    await connection.execute(
        sa.insert(chunk_embeddings),
        [
            {
                "chunk_id": chunk_id.value,
                "space_id": space_id.value,
                "dimensions": dimensions,
                "embedding": list(unit_length(vector)),
                "created_at": created_at,
            }
            for chunk_id, vector in vectors.items()
        ],
    )
