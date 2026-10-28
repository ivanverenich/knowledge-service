"""Move search to a new embedding model in four steps, without downtime.

See `embeddings.migration` for the states. Each function here is one step
and opens its own short transactions (ADR 0012):

- `start_embedding_migration` creates the target space and enters
  `DUAL_WRITING`;
- `embedding_models_to_write` tells publication which models to embed with;
- `backfill_embeddings` embeds a few older Documents into the target;
- `switch_to_target_space` makes the target current once it is complete;
- `clean_up_source_space` deletes the old space and its vectors.

Search never needs to know a migration is running: it reads the current
space only, and exactly one space is current at every moment.
"""

import math
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.database import DatabaseRuntime
from knowledge_service.embeddings.index_configuration import (
    InvalidIndexConfiguration,
    current_index_configuration,
)
from knowledge_service.embeddings.migration import (
    EmbeddingMigration,
    EmbeddingMigrationError,
    InvalidMigrationTransition,
    MigrationState,
    MigrationStep,
    SpaceRole,
    next_state,
    written_spaces,
)
from knowledge_service.embeddings.port import (
    EmbeddingError,
    EmbeddingModel,
    EmbeddingModelIdentity,
)
from knowledge_service.embeddings.publication import (
    EmbeddingPublicationError,
    EmbeddingPublicationStatus,
    publish_document_embeddings,
)
from knowledge_service.identifiers import (
    DocumentId,
    EmbeddingMigrationId,
    EmbeddingSpaceId,
)
from knowledge_service.persistence.embedding_migrations import (
    count_chunks_missing_from_target,
    insert_embedding_migration,
    load_documents_missing_from_target,
    load_embedding_migration,
    load_open_embedding_migration,
    save_embedding_migration,
)
from knowledge_service.persistence.embeddings import (
    delete_embedding_space,
    ensure_index_configuration,
    insert_embedding_space,
    load_current_embedding_space,
    load_embedding_space,
    load_embedding_space_id,
    make_embedding_space_current,
)

# Bounds the Documents one backfill call embeds.
MAX_BACKFILL_DOCUMENTS = 100


class NothingToMigrate(EmbeddingMigrationError):
    """No space is current, or the target is the current model already."""


class MigrationInProgress(EmbeddingMigrationError):
    """Another migration has not been cleaned up yet."""


class InvalidTarget(EmbeddingMigrationError):
    """The target model cannot be described by a valid index configuration."""


class TargetSpaceExists(EmbeddingMigrationError):
    """A space for the target model exists already."""


class UnknownMigration(EmbeddingMigrationError):
    """No migration has this ID."""


class WrongModel(EmbeddingMigrationError):
    """The model given is not the one the target space holds."""


class IncompleteBackfill(EmbeddingMigrationError):
    """The target still lacks vectors the source has; nothing was switched."""

    def __init__(self, missing: int) -> None:
        self.missing = missing
        super().__init__(f"the target space still lacks {missing} vectors")


@dataclass(frozen=True, slots=True)
class BackfillProgress:
    """What one backfill call did, and what is left.

    `failed` lists the Documents the model could not embed, by ID only.
    `remaining` counts every Chunk vector the target still lacks, failed
    ones included. `resume_after` is where the next call should continue,
    or None once the end was reached.
    """

    published: int
    failed: tuple[DocumentId, ...]
    remaining: int
    resume_after: DocumentId | None


async def start_embedding_migration(
    runtime: DatabaseRuntime,
    target: EmbeddingModelIdentity,
    *,
    now: datetime,
) -> EmbeddingMigration:
    """Create the target space, not current, and start dual-writing.

    The current space's row stays locked until the commit, so two starts run
    one after the other and the second sees the first.
    """
    try:
        configuration = current_index_configuration(target)
    except InvalidIndexConfiguration as error:
        raise InvalidTarget(str(error)) from None
    async with runtime.transaction() as connection:
        current = await load_current_embedding_space(connection, for_update=True)
        if current is None:
            raise NothingToMigrate("no embedding space is current")
        if current.identity == target:
            raise NothingToMigrate("the target model is the current one")
        if await load_open_embedding_migration(connection) is not None:
            raise MigrationInProgress("another migration is not cleaned up yet")
        if await load_embedding_space_id(connection, target) is not None:
            raise TargetSpaceExists("a space for the target model exists")
        configuration_id = await ensure_index_configuration(
            connection, configuration, created_at=now
        )
        target_space_id = EmbeddingSpaceId.new()
        await insert_embedding_space(
            connection,
            space_id=target_space_id,
            identity=target,
            is_current=False,
            created_at=now,
        )
        migration = EmbeddingMigration(
            migration_id=EmbeddingMigrationId.new(),
            source_space_id=current.space_id,
            target_space_id=target_space_id,
            target_configuration_id=configuration_id,
            state=MigrationState.DUAL_WRITING,
            started_at=now,
            updated_at=now,
        )
        await insert_embedding_migration(connection, migration)
    return migration


async def embedding_models_to_write(
    connection: AsyncConnection,
) -> tuple[EmbeddingModelIdentity, ...]:
    """The models every publication must embed with now.

    The current model, and while dual-writing also the target's, so that no
    Document published during a migration is missing from either space.
    """
    current = await load_current_embedding_space(connection)
    migration = await load_open_embedding_migration(connection)
    if migration is None:
        return () if current is None else (current.identity,)
    identities: list[EmbeddingModelIdentity] = []
    for role in written_spaces(migration.state):
        space_id = (
            migration.source_space_id
            if role is SpaceRole.SOURCE
            else migration.target_space_id
        )
        space = (
            None
            if space_id is None
            else await load_embedding_space(connection, space_id)
        )
        if space is not None:
            identities.append(space.identity)
    return tuple(identities)


async def backfill_embeddings(
    runtime: DatabaseRuntime,
    model: EmbeddingModel,
    *,
    migration_id: EmbeddingMigrationId,
    after: DocumentId | None,
    limit: int,
    concurrency: int,
    request_id: UUID,
    timeout_seconds: float,
    now: datetime,
) -> BackfillProgress:
    """Embed up to `limit` Documents, after `after`, that the target lacks.

    Each Document is published as in M04-T05: all its Chunks or none, with
    no connection held while the model works. A Document the model cannot
    embed is listed in `failed` and the rest go on, so one bad Document
    never stops the others. Pass `resume_after` back as `after` until it is
    None, then start again from None until `remaining` is 0.
    """
    _check_backfill(limit, concurrency, timeout_seconds)
    async with runtime.transaction() as connection:
        migration = await _load(connection, migration_id)
        if migration.state is not MigrationState.DUAL_WRITING:
            raise InvalidMigrationTransition("only a dual_writing migration backfills")
        source, target = _spaces(migration)
        target_space = await load_embedding_space(connection, target)
        if target_space is None or target_space.identity != model.identity:
            raise WrongModel("the model is not the target space's")
        document_ids = await load_documents_missing_from_target(
            connection, source=source, target=target, after=after, limit=limit
        )
    published = 0
    failed: list[DocumentId] = []
    for document_id in document_ids:
        try:
            publication = await publish_document_embeddings(
                runtime,
                model,
                document_id=document_id,
                concurrency=concurrency,
                request_id=request_id,
                timeout_seconds=timeout_seconds,
                now=now,
            )
        except EmbeddingError, EmbeddingPublicationError:
            # Only the ID is kept: the error may describe the Document's text.
            failed.append(document_id)
            continue
        if publication.status is EmbeddingPublicationStatus.PUBLISHED:
            published += 1
    async with runtime.transaction() as connection:
        remaining = await count_chunks_missing_from_target(
            connection, source=source, target=target
        )
    return BackfillProgress(
        published=published,
        failed=tuple(failed),
        remaining=remaining,
        resume_after=document_ids[-1] if len(document_ids) == limit else None,
    )


def _check_backfill(limit: int, concurrency: int, timeout_seconds: float) -> None:
    # bool is an int, so `True` would otherwise pass as 1.
    if type(limit) is not int or not 1 <= limit <= MAX_BACKFILL_DOCUMENTS:
        raise EmbeddingMigrationError(
            f"limit must be between 1 and {MAX_BACKFILL_DOCUMENTS}"
        )
    if type(concurrency) is not int or concurrency < 1:
        raise EmbeddingMigrationError("concurrency must be a positive int")
    if (
        type(timeout_seconds) not in (int, float)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise EmbeddingMigrationError("timeout_seconds must be a positive number")


async def switch_to_target_space(
    runtime: DatabaseRuntime,
    *,
    migration_id: EmbeddingMigrationId,
    now: datetime,
) -> EmbeddingMigration:
    """Make the target space current, if it has every vector the source has.

    Both spaces' rows are locked first, so no publication writes into either
    while the check and the switch run; search sees the old space until the
    commit and the new one after it. Raises `IncompleteBackfill`, and
    switches nothing, while any searchable Chunk lacks a target vector.
    """
    async with runtime.transaction() as connection:
        migration = await _load(connection, migration_id, for_update=True)
        after = next_state(migration.state, MigrationStep.SWITCH)
        _check_time(migration, now)
        source, target = _spaces(migration)
        # A fixed order, so two callers locking both rows never wait in a cycle.
        locked = {
            space_id: await load_embedding_space(connection, space_id, for_update=True)
            for space_id in sorted((source, target), key=lambda space: space.value)
        }
        source_space, target_space = locked[source], locked[target]
        if source_space is None or target_space is None or not source_space.is_current:
            raise InvalidMigrationTransition("the source space is no longer current")
        missing = await count_chunks_missing_from_target(
            connection, source=source, target=target
        )
        if missing:
            raise IncompleteBackfill(missing)
        await make_embedding_space_current(connection, retired=source, current=target)
        switched = replace(migration, state=after, updated_at=now)
        await save_embedding_migration(connection, switched)
    return switched


async def clean_up_source_space(
    runtime: DatabaseRuntime,
    *,
    migration_id: EmbeddingMigrationId,
    now: datetime,
) -> EmbeddingMigration:
    """Delete the old space and its vectors once search has switched away."""
    async with runtime.transaction() as connection:
        migration = await _load(connection, migration_id, for_update=True)
        after = next_state(migration.state, MigrationStep.CLEAN_UP)
        _check_time(migration, now)
        if migration.source_space_id is not None:
            source = await load_embedding_space(
                connection, migration.source_space_id, for_update=True
            )
            if source is not None and source.is_current:
                raise InvalidMigrationTransition("the source space is current")
            await delete_embedding_space(connection, migration.source_space_id)
        cleaned = replace(migration, state=after, source_space_id=None, updated_at=now)
        await save_embedding_migration(connection, cleaned)
    return cleaned


async def _load(
    connection: AsyncConnection,
    migration_id: EmbeddingMigrationId,
    *,
    for_update: bool = False,
) -> EmbeddingMigration:
    migration = await load_embedding_migration(
        connection, migration_id, for_update=for_update
    )
    if migration is None:
        raise UnknownMigration("no migration has this ID")
    return migration


def _check_time(migration: EmbeddingMigration, now: datetime) -> None:
    if now < migration.updated_at:
        raise EmbeddingMigrationError("a step cannot happen before the last one")


def _spaces(
    migration: EmbeddingMigration,
) -> tuple[EmbeddingSpaceId, EmbeddingSpaceId]:
    if migration.source_space_id is None or migration.target_space_id is None:
        raise InvalidMigrationTransition("a space of this migration was deleted")
    return migration.source_space_id, migration.target_space_id
