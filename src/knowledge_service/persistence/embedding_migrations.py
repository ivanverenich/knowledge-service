"""Store embedding migrations and find what the target space still lacks."""

from collections.abc import Mapping
from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.documents import DocumentAvailability
from knowledge_service.embeddings.migration import (
    EmbeddingMigration,
    EmbeddingMigrationError,
    MigrationState,
)
from knowledge_service.identifiers import (
    DocumentId,
    EmbeddingMigrationId,
    EmbeddingSpaceId,
    IndexConfigurationId,
)
from knowledge_service.persistence.schema import (
    chunk_embeddings,
    chunks,
    documents,
    embedding_migrations,
)


def _missing_from_target(
    source: EmbeddingSpaceId, target: EmbeddingSpaceId
) -> sa.ColumnElement[bool]:
    """A Chunk of an available Document with a source vector and no target one."""
    source_vector = sa.exists().where(
        chunk_embeddings.c.chunk_id == chunks.c.chunk_id,
        chunk_embeddings.c.space_id == source.value,
    )
    target_vector = sa.exists().where(
        chunk_embeddings.c.chunk_id == chunks.c.chunk_id,
        chunk_embeddings.c.space_id == target.value,
    )
    return sa.and_(
        documents.c.availability == DocumentAvailability.AVAILABLE.value,
        source_vector,
        ~target_vector,
    )


async def count_chunks_missing_from_target(
    connection: AsyncConnection,
    *,
    source: EmbeddingSpaceId,
    target: EmbeddingSpaceId,
) -> int:
    """Count the searchable Chunks the target space has no vector for yet."""
    count = await connection.scalar(
        sa.select(sa.func.count())
        .select_from(chunks)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(_missing_from_target(source, target))
    )
    return int(count or 0)


async def load_documents_missing_from_target(
    connection: AsyncConnection,
    *,
    source: EmbeddingSpaceId,
    target: EmbeddingSpaceId,
    after: DocumentId | None,
    limit: int,
) -> tuple[DocumentId, ...]:
    """Return up to `limit` Documents with a Chunk the target still lacks.

    Only Documents ordered after `after` count, so a caller can move past a
    Document that keeps failing instead of meeting it first every time.
    """
    after_clause = sa.true() if after is None else chunks.c.document_id > after.value
    document_ids = await connection.scalars(
        sa.select(chunks.c.document_id)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(_missing_from_target(source, target), after_clause)
        .group_by(chunks.c.document_id)
        .order_by(chunks.c.document_id)
        .limit(limit)
    )
    return tuple(DocumentId(cast(UUID, value)) for value in document_ids)


def _migration_row(migration: EmbeddingMigration) -> dict[str, object]:
    return {
        "migration_id": migration.migration_id.value,
        "source_space_id": _optional_id(migration.source_space_id),
        "target_space_id": _optional_id(migration.target_space_id),
        "target_configuration_id": migration.target_configuration_id.value,
        "state": migration.state.value,
        "started_at": migration.started_at,
        "updated_at": migration.updated_at,
    }


def _optional_id(identifier: EmbeddingSpaceId | None) -> UUID | None:
    return None if identifier is None else identifier.value


def _migration_from(record: Mapping[str, object]) -> EmbeddingMigration:
    try:
        state = MigrationState(cast(str, record["state"]))
    except ValueError:
        raise EmbeddingMigrationError("stored state is not a known value") from None
    source = cast("UUID | None", record["source_space_id"])
    target = cast("UUID | None", record["target_space_id"])
    return EmbeddingMigration(
        migration_id=EmbeddingMigrationId(cast(UUID, record["migration_id"])),
        source_space_id=None if source is None else EmbeddingSpaceId(source),
        target_space_id=None if target is None else EmbeddingSpaceId(target),
        target_configuration_id=IndexConfigurationId(
            cast(UUID, record["target_configuration_id"])
        ),
        state=state,
        started_at=cast(datetime, record["started_at"]),
        updated_at=cast(datetime, record["updated_at"]),
    )


async def insert_embedding_migration(
    connection: AsyncConnection, migration: EmbeddingMigration
) -> None:
    """Store a new Embedding Migration."""
    await connection.execute(
        sa.insert(embedding_migrations).values(_migration_row(migration))
    )


async def load_embedding_migration(
    connection: AsyncConnection,
    migration_id: EmbeddingMigrationId,
    *,
    for_update: bool = False,
) -> EmbeddingMigration | None:
    """Read one migration; `for_update` locks its row until the commit."""
    statement = sa.select(embedding_migrations).where(
        embedding_migrations.c.migration_id == migration_id.value
    )
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    return None if record is None else _migration_from(dict(record))


async def load_open_embedding_migration(
    connection: AsyncConnection,
) -> EmbeddingMigration | None:
    """Read the migration that still has a step to take, if there is one."""
    record = (
        (
            await connection.execute(
                sa.select(embedding_migrations)
                .where(embedding_migrations.c.state != MigrationState.CLEANED.value)
                .order_by(embedding_migrations.c.started_at)
                .limit(1)
            )
        )
        .mappings()
        .one_or_none()
    )
    return None if record is None else _migration_from(dict(record))


async def save_embedding_migration(
    connection: AsyncConnection, migration: EmbeddingMigration
) -> None:
    """Store a migration's new state and time."""
    await connection.execute(
        sa.update(embedding_migrations)
        .where(embedding_migrations.c.migration_id == migration.migration_id.value)
        .values(state=migration.state.value, updated_at=migration.updated_at)
    )
