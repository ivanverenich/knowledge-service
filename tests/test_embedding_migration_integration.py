"""Real PostgreSQL tests: moving search to a new model never mixes spaces."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncConnection

import knowledge_service.retrieval.vector_search as vector_search
from fakes import FakeEmbeddingModel
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.migration import (
    EmbeddingMigration,
    EmbeddingMigrationError,
    InvalidMigrationTransition,
    MigrationState,
)
from knowledge_service.embeddings.migration_workflow import (
    BackfillProgress,
    IncompleteBackfill,
    InvalidTarget,
    MigrationInProgress,
    NothingToMigrate,
    TargetSpaceExists,
    UnknownMigration,
    WrongModel,
    backfill_embeddings,
    clean_up_source_space,
    embedding_models_to_write,
    start_embedding_migration,
    switch_to_target_space,
)
from knowledge_service.embeddings.port import (
    EmbeddingBatch,
    EmbeddingLimits,
    EmbeddingModel,
    EmbeddingModelIdentity,
    EmbeddingUnavailable,
)
from knowledge_service.embeddings.publication import (
    EmbeddingPublicationStatus,
    publish_document_embeddings,
)
from knowledge_service.identifiers import (
    DocumentId,
    EmbeddingMigrationId,
    EmbeddingSpaceId,
    SourceId,
    UserId,
)
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import SourceAccess, SourceRecord
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.embeddings import insert_embedding_space
from knowledge_service.persistence.schema import chunk_embeddings, embedding_spaces
from knowledge_service.retrieval.vector_search import (
    NoCurrentEmbeddingSpace,
    search_similar_chunks,
)
from knowledge_service.settings import Settings

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000031")
STARTED_AT = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
OLD = FakeEmbeddingModel(dimensions=8)
NEW = FakeEmbeddingModel(dimensions=4)
READER = AuthorizationContext(
    user_id=UserId.parse("00000000-0000-0000-0000-0000000000a1")
)

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
        await insert_embedding_space(
            connection,
            space_id=EmbeddingSpaceId.new(),
            identity=OLD.identity,
            is_current=True,
            created_at=STARTED_AT,
        )
    try:
        yield runtime
    finally:
        await runtime.dispose()


def plain_text(name: str) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version="v1",
            media_type="text/plain",
            content=f"The {name} policy explains the rules.".encode(),
            access=SourceAccess(public=True),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def a_document(runtime: DatabaseRuntime, name: str) -> DocumentId:
    reconciled = await reconcile_document(
        runtime, plain_text(name), readers=[Subject.public()], observed_at=STARTED_AT
    )
    return reconciled.document.document_id


async def publish(
    runtime: DatabaseRuntime, model: EmbeddingModel, document_id: DocumentId
) -> EmbeddingPublicationStatus:
    publication = await publish_document_embeddings(
        runtime,
        model,
        document_id=document_id,
        concurrency=1,
        request_id=UUID(int=1),
        timeout_seconds=5.0,
        now=STARTED_AT,
    )
    return publication.status


async def published_document(runtime: DatabaseRuntime, name: str) -> DocumentId:
    """A Document with vectors in every space publication writes to now."""
    document_id = await a_document(runtime, name)
    async with runtime.transaction() as connection:
        identities = await embedding_models_to_write(connection)
    for model in (OLD, NEW):
        if model.identity in identities:
            await publish(runtime, model, document_id)
    return document_id


async def backfill_step(
    runtime: DatabaseRuntime,
    migration: EmbeddingMigration,
    *,
    model: EmbeddingModel = NEW,
    after: DocumentId | None = None,
    limit: int = 10,
    concurrency: int = 1,
    timeout_seconds: float = 5.0,
) -> BackfillProgress:
    return await backfill_embeddings(
        runtime,
        model,
        migration_id=migration.migration_id,
        after=after,
        limit=limit,
        concurrency=concurrency,
        request_id=UUID(int=2),
        timeout_seconds=timeout_seconds,
        now=STARTED_AT,
    )


async def backfill(
    runtime: DatabaseRuntime,
    migration: EmbeddingMigration,
    *,
    model: EmbeddingModel = NEW,
    limit: int = 10,
) -> int:
    """Run one backfill call from the start; return what is still missing."""
    progress = await backfill_step(runtime, migration, model=model, limit=limit)
    return progress.remaining


async def search(runtime: DatabaseRuntime, model: EmbeddingModel) -> set[DocumentId]:
    """Search with a query of the model's dimensions; return the Documents."""
    async with runtime.transaction() as connection:
        result = await search_similar_chunks(
            connection,
            identity=model.identity,
            query=(1.0,) * model.identity.dimensions,
            context=READER,
            limit=100,
        )
    return {candidate.document_id for candidate in result.candidates}


async def vectors_per_dimensions(runtime: DatabaseRuntime) -> dict[int, int]:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(chunk_embeddings.c.dimensions, sa.func.count()).group_by(
                chunk_embeddings.c.dimensions
            )
        )
        return {int(dimensions): int(count) for dimensions, count in rows}


async def current_dimensions(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        value = await connection.scalar(
            sa.select(embedding_spaces.c.dimensions).where(
                embedding_spaces.c.is_current
            )
        )
    assert value is not None
    return int(value)


async def assert_search_reads_only(
    runtime: DatabaseRuntime,
    reader: EmbeddingModel,
    refused: EmbeddingModel,
    expected: set[DocumentId],
) -> None:
    """Search answers queries of the current dimensions only, from one space."""
    assert await current_dimensions(runtime) == reader.identity.dimensions
    assert await search(runtime, reader) == expected
    with pytest.raises(NoCurrentEmbeddingSpace):
        await search(runtime, refused)


@pytest.mark.integration
async def test_a_migration_moves_search_without_ever_mixing_dimensions(
    runtime: DatabaseRuntime,
) -> None:
    before = {await published_document(runtime, f"doc-{i}") for i in range(3)}
    await assert_search_reads_only(runtime, OLD, NEW, before)

    migration = await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)
    async with runtime.transaction() as connection:
        writes = await embedding_models_to_write(connection)
    assert migration.state is MigrationState.DUAL_WRITING
    assert writes == (OLD.identity, NEW.identity)
    await assert_search_reads_only(runtime, OLD, NEW, before)

    # Dual-write: a Document published now lands in both spaces.
    during = await published_document(runtime, "doc-during")
    everything = before | {during}
    # Both spaces now hold vectors of other lengths for the same Chunks;
    # search still reads only the current one.
    assert (await vectors_per_dimensions(runtime))[NEW.identity.dimensions] == 1
    await assert_search_reads_only(runtime, OLD, NEW, everything)

    assert await backfill(runtime, migration, limit=2) > 0
    await assert_search_reads_only(runtime, OLD, NEW, everything)
    assert await backfill(runtime, migration, limit=2) == 0

    switched = await switch_to_target_space(
        runtime,
        migration_id=migration.migration_id,
        now=STARTED_AT + timedelta(hours=1),
    )
    async with runtime.transaction() as connection:
        writes = await embedding_models_to_write(connection)
    assert switched.state is MigrationState.SWITCHED
    assert writes == (NEW.identity,)
    await assert_search_reads_only(runtime, NEW, OLD, everything)

    cleaned = await clean_up_source_space(
        runtime,
        migration_id=migration.migration_id,
        now=STARTED_AT + timedelta(hours=2),
    )
    assert cleaned.state is MigrationState.CLEANED
    assert cleaned.source_space_id is None
    assert await vectors_per_dimensions(runtime) == {NEW.identity.dimensions: 4}
    await assert_search_reads_only(runtime, NEW, OLD, everything)


@pytest.mark.integration
async def test_switching_before_the_backfill_is_done_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    before = {await published_document(runtime, f"doc-{i}") for i in range(3)}
    migration = await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)

    with pytest.raises(IncompleteBackfill) as refused:
        await switch_to_target_space(
            runtime, migration_id=migration.migration_id, now=STARTED_AT
        )

    assert refused.value.missing == 3
    await assert_search_reads_only(runtime, OLD, NEW, before)


@pytest.mark.integration
async def test_a_vector_written_only_to_the_old_space_blocks_the_switch(
    runtime: DatabaseRuntime,
) -> None:
    await published_document(runtime, "doc-0")
    migration = await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)
    assert await backfill(runtime, migration) == 0
    # A writer that ignored dual-writing: the new space lacks this Document.
    await publish(runtime, OLD, await a_document(runtime, "doc-late"))

    with pytest.raises(IncompleteBackfill) as refused:
        await switch_to_target_space(
            runtime, migration_id=migration.migration_id, now=STARTED_AT
        )

    assert refused.value.missing == 1
    assert await backfill(runtime, migration) == 0
    await switch_to_target_space(
        runtime, migration_id=migration.migration_id, now=STARTED_AT
    )


@pytest.mark.integration
async def test_a_tombstoned_document_does_not_block_the_switch(
    runtime: DatabaseRuntime,
) -> None:
    kept = await published_document(runtime, "kept")
    await published_document(runtime, "gone")
    migration = await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)
    await tombstone_missing(
        runtime,
        source_id=SOURCE_ID,
        seen={"kept"},
        observed_at=STARTED_AT + timedelta(minutes=1),
    )

    assert await backfill(runtime, migration) == 0
    await switch_to_target_space(
        runtime, migration_id=migration.migration_id, now=STARTED_AT
    )

    await assert_search_reads_only(runtime, NEW, OLD, {kept})


@pytest.mark.integration
async def test_steps_out_of_order_are_refused(runtime: DatabaseRuntime) -> None:
    await published_document(runtime, "doc-0")
    migration = await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)
    migration_id = migration.migration_id

    with pytest.raises(InvalidMigrationTransition):
        await clean_up_source_space(runtime, migration_id=migration_id, now=STARTED_AT)
    with pytest.raises(WrongModel):
        await backfill(runtime, migration, model=OLD)
    await backfill(runtime, migration)
    await switch_to_target_space(runtime, migration_id=migration_id, now=STARTED_AT)
    with pytest.raises(InvalidMigrationTransition):
        await switch_to_target_space(runtime, migration_id=migration_id, now=STARTED_AT)
    with pytest.raises(InvalidMigrationTransition):
        await backfill(runtime, migration)
    await clean_up_source_space(runtime, migration_id=migration_id, now=STARTED_AT)
    with pytest.raises(InvalidMigrationTransition):
        await clean_up_source_space(runtime, migration_id=migration_id, now=STARTED_AT)
    with pytest.raises(UnknownMigration):
        await switch_to_target_space(
            runtime, migration_id=EmbeddingMigrationId.new(), now=STARTED_AT
        )


@pytest.mark.integration
@pytest.mark.parametrize("limit", [0, 101, True])
async def test_a_backfill_limit_out_of_range_is_refused(
    runtime: DatabaseRuntime, limit: int
) -> None:
    migration = await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)

    with pytest.raises(EmbeddingMigrationError):
        await backfill(runtime, migration, limit=limit)


@pytest.mark.integration
async def test_a_migration_needs_a_new_model_and_no_other_open_migration(
    runtime: DatabaseRuntime,
) -> None:
    with pytest.raises(NothingToMigrate):
        await start_embedding_migration(runtime, OLD.identity, now=STARTED_AT)
    await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)

    with pytest.raises(MigrationInProgress):
        await start_embedding_migration(
            runtime, FakeEmbeddingModel(dimensions=16).identity, now=STARTED_AT
        )


@pytest.mark.integration
async def test_a_model_that_already_has_a_space_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        await insert_embedding_space(
            connection,
            space_id=EmbeddingSpaceId.new(),
            identity=NEW.identity,
            is_current=False,
            created_at=STARTED_AT,
        )

    with pytest.raises(TargetSpaceExists):
        await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)


@pytest.mark.integration
async def test_without_a_current_space_there_is_nothing_to_migrate(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(sa.update(embedding_spaces).values(is_current=False))

    with pytest.raises(NothingToMigrate):
        await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)


@pytest.mark.integration
async def test_two_starts_at_once_open_one_migration(runtime: DatabaseRuntime) -> None:
    outcomes = await asyncio.gather(
        start_embedding_migration(runtime, NEW.identity, now=STARTED_AT),
        start_embedding_migration(runtime, NEW.identity, now=STARTED_AT),
        return_exceptions=True,
    )

    started = [o for o in outcomes if isinstance(o, EmbeddingMigration)]
    refused = [o for o in outcomes if isinstance(o, MigrationInProgress)]
    assert len(started) == 1
    assert len(refused) == 1


class FailingModel:
    """The new model, except that it always fails on one Document's text."""

    def __init__(self, failing_text: str) -> None:
        self._model = FakeEmbeddingModel(dimensions=NEW.identity.dimensions)
        self._failing_text = failing_text

    @property
    def identity(self) -> EmbeddingModelIdentity:
        return self._model.identity

    @property
    def limits(self) -> EmbeddingLimits:
        return self._model.limits

    def count_tokens(self, text: str) -> int:
        return self._model.count_tokens(text)

    async def embed(
        self, texts: Sequence[str], *, request_id: UUID, timeout_seconds: float
    ) -> EmbeddingBatch:
        if any(self._failing_text in text for text in texts):
            raise EmbeddingUnavailable()
        return await self._model.embed(
            texts, request_id=request_id, timeout_seconds=timeout_seconds
        )


@pytest.mark.integration
async def test_one_document_the_model_cannot_embed_does_not_stop_the_others(
    runtime: DatabaseRuntime,
) -> None:
    documents = sorted(
        [await published_document(runtime, f"doc-{i}") for i in range(4)],
        key=lambda document_id: document_id.value,
    )
    migration = await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)
    # The Document met first every time, which once stopped every call.
    failing = FailingModel(failing_text=await first_chunk_text(runtime, documents[0]))

    progress = await backfill_step(runtime, migration, model=failing)

    assert progress.failed == (documents[0],)
    assert progress.published == 3
    assert progress.remaining == 1
    with pytest.raises(IncompleteBackfill):
        await switch_to_target_space(
            runtime, migration_id=migration.migration_id, now=STARTED_AT
        )


@pytest.mark.integration
async def test_the_cursor_moves_past_a_document_that_keeps_failing(
    runtime: DatabaseRuntime,
) -> None:
    documents = sorted(
        [await published_document(runtime, f"doc-{i}") for i in range(3)],
        key=lambda document_id: document_id.value,
    )
    migration = await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)
    failing = FailingModel(failing_text=await first_chunk_text(runtime, documents[0]))

    first = await backfill_step(runtime, migration, model=failing, limit=1)
    second = await backfill_step(
        runtime, migration, model=failing, after=first.resume_after, limit=1
    )

    assert first.failed == (documents[0],)
    assert first.resume_after == documents[0]
    assert second.published == 1
    assert second.resume_after == documents[1]


@pytest.mark.integration
async def test_a_search_across_the_cutover_says_so_instead_of_finding_nothing(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    for i in range(3):
        await published_document(runtime, f"doc-{i}")
    migration = await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)
    await backfill(runtime, migration)
    read_space = vector_search.load_current_embedding_space_id

    async def read_then_cut_over(
        connection: AsyncConnection, identity: EmbeddingModelIdentity
    ) -> EmbeddingSpaceId | None:
        space_id = await read_space(connection, identity)
        if identity == OLD.identity and space_id is not None:
            # Between the search's two reads, the switch and the clean-up
            # commit. This once returned an empty result with no error.
            await switch_to_target_space(
                runtime, migration_id=migration.migration_id, now=STARTED_AT
            )
            await clean_up_source_space(
                runtime, migration_id=migration.migration_id, now=STARTED_AT
            )
        return space_id

    monkeypatch.setattr(
        vector_search, "load_current_embedding_space_id", read_then_cut_over
    )

    with pytest.raises(NoCurrentEmbeddingSpace):
        await search(runtime, OLD)


@pytest.mark.integration
async def test_a_late_write_into_the_old_space_is_refused_after_the_switch(
    runtime: DatabaseRuntime,
) -> None:
    await published_document(runtime, "doc-0")
    migration = await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)
    await backfill(runtime, migration)
    await switch_to_target_space(
        runtime, migration_id=migration.migration_id, now=STARTED_AT
    )
    late = await a_document(runtime, "doc-late")
    old_vectors = (await vectors_per_dimensions(runtime))[OLD.identity.dimensions]

    # A publisher that chose the old model before the switch. Its vectors
    # would never be read again; they once landed in the retired space.
    status = await publish(runtime, OLD, late)

    assert status is EmbeddingPublicationStatus.STALE
    assert (await vectors_per_dimensions(runtime))[
        OLD.identity.dimensions
    ] == old_vectors
    assert await publish(runtime, NEW, late) is EmbeddingPublicationStatus.PUBLISHED


@pytest.mark.integration
async def test_a_step_dated_before_the_last_one_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    migration = await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)
    await switch_to_target_space(
        runtime,
        migration_id=migration.migration_id,
        now=STARTED_AT + timedelta(hours=2),
    )

    with pytest.raises(EmbeddingMigrationError):
        await clean_up_source_space(
            runtime,
            migration_id=migration.migration_id,
            now=STARTED_AT + timedelta(hours=1),
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("concurrency", "timeout_seconds"),
    [(0, 5.0), (True, 5.0), (1, float("nan")), (1, 0.0), (1, -1.0)],
)
async def test_bad_backfill_settings_are_refused_even_with_nothing_to_do(
    runtime: DatabaseRuntime, concurrency: int, timeout_seconds: float
) -> None:
    migration = await start_embedding_migration(runtime, NEW.identity, now=STARTED_AT)

    with pytest.raises(EmbeddingMigrationError):
        await backfill_step(
            runtime,
            migration,
            concurrency=concurrency,
            timeout_seconds=timeout_seconds,
        )


@pytest.mark.integration
async def test_a_target_no_index_configuration_can_describe_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    with pytest.raises(InvalidTarget):
        await start_embedding_migration(
            runtime, EmbeddingModelIdentity("fake", "huge", 16_001), now=STARTED_AT
        )


async def first_chunk_text(runtime: DatabaseRuntime, document_id: DocumentId) -> str:
    async with runtime.transaction() as connection:
        (chunk,) = await load_chunks(connection, document_id=document_id)
    return chunk.text
