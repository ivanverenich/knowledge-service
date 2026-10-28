"""Real PostgreSQL tests for publishing a Document's embeddings atomically."""

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.pool import QueuePool

import knowledge_service.embeddings.publication as embedding_publication
from fakes import FakeEmbeddingModel
from knowledge_service.access import Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingLimits,
    EmbeddingModelIdentity,
    EmbeddingRateLimited,
    EmbeddingUnavailable,
)
from knowledge_service.embeddings.publication import (
    EmbeddingPublication,
    EmbeddingPublicationStatus,
    IncompleteEmbedding,
    UnknownEmbeddingSpace,
    publish_document_embeddings,
)
from knowledge_service.identifiers import DocumentId, EmbeddingSpaceId, SourceId
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.publishing import Publication, publish_document
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import SourceAccess, SourceRecord
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.documents import load_document_by_id
from knowledge_service.persistence.schema import (
    chunk_embeddings,
    chunks,
    embedding_spaces,
)
from knowledge_service.settings import Settings

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000011")
OBSERVED_AT = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
NOW = OBSERVED_AT + timedelta(minutes=5)
# Planted in the text; it must never appear in an error or a log record.
MARKER = "zebramarker"

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


def section(heading: str, word: str) -> str:
    return f"## {heading}\n\n" + " ".join(f"{word}{i}" for i in range(150)) + ".\n\n"


# Three sections long enough to become three Chunks.
HANDBOOK = (
    "# Handbook\n\n"
    + section("Leave", MARKER)
    + section("Equipment", "laptop")
    + section("Travel", "trip")
)
EDITED = HANDBOOK.replace("trip149", "flight")


class ControlledModel:
    """The fake model, one Chunk per call, that can pause and fail one call.

    `started` is set when a call reaches the model. With `paused`, every call
    then waits for `release`, so a test can act while the model works.
    """

    def __init__(
        self,
        *,
        dimensions: int = 8,
        paused: bool = False,
        fail_on: int | None = None,
        error: EmbeddingError | None = None,
    ) -> None:
        self._model = FakeEmbeddingModel(
            dimensions=dimensions,
            limits=EmbeddingLimits(
                max_inputs=1, max_input_tokens=200, max_batch_tokens=200
            ),
        )
        self._fail_on = fail_on
        self._error = error
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        if not paused:
            self.release.set()
        self.calls: list[tuple[str, ...]] = []

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
        self.calls.append(tuple(texts))
        self.started.set()
        await self.release.wait()
        if len(self.calls) == self._fail_on and self._error is not None:
            raise self._error
        return await self._model.embed(
            texts, request_id=request_id, timeout_seconds=timeout_seconds
        )


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
        await connection.execute(
            sa.insert(embedding_spaces).values(
                space_id=uuid4(),
                provider="fake",
                model="fake-embedding",
                dimensions=8,
                is_current=True,
                created_at=OBSERVED_AT,
            )
        )
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


async def a_document(
    runtime: DatabaseRuntime,
    text: str = HANDBOOK,
    *,
    hours_later: int = 0,
) -> DocumentId:
    reconciled = await reconcile_document(
        runtime,
        markdown(text),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=hours_later),
    )
    return reconciled.document.document_id


async def publish(
    runtime: DatabaseRuntime, model: ControlledModel, document_id: DocumentId
) -> EmbeddingPublication:
    return await publish_document_embeddings(
        runtime,
        model,
        document_id=document_id,
        concurrency=1,
        request_id=UUID(int=1),
        timeout_seconds=5.0,
        now=NOW,
    )


async def stored(runtime: DatabaseRuntime) -> dict[str, list[float]]:
    """Map the text of each Chunk that has a vector to that vector."""
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(chunks.c.text, chunk_embeddings.c.embedding).join(
                chunks, chunks.c.chunk_id == chunk_embeddings.c.chunk_id
            )
        )
        return {text: [float(x) for x in vector] for text, vector in rows}


async def chunk_texts(runtime: DatabaseRuntime) -> set[str]:
    async with runtime.transaction() as connection:
        return set(await connection.scalars(sa.select(chunks.c.text)))


@pytest.mark.integration
async def test_every_chunk_gets_its_vector_in_one_publication(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(runtime)
    model = ControlledModel()

    result = await publish(runtime, model, document_id)

    texts = await chunk_texts(runtime)
    vectors = await stored(runtime)
    assert len(texts) == 3
    assert result.status is EmbeddingPublicationStatus.PUBLISHED
    assert result.embedded == 3
    assert set(vectors) == texts
    for text in texts:
        (expected,) = (
            await model.embed([text], request_id=UUID(int=2), timeout_seconds=5.0)
        ).vectors
        # pgvector stores 32-bit floats, so compare within their precision.
        assert vectors[text] == pytest.approx(expected, abs=1e-6)


@pytest.mark.integration
async def test_a_published_document_is_not_embedded_again(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(runtime)
    await publish(runtime, ControlledModel(), document_id)
    model = ControlledModel()

    result = await publish(runtime, model, document_id)

    assert result.status is EmbeddingPublicationStatus.UNCHANGED
    assert model.calls == []


@pytest.mark.integration
async def test_only_changed_text_is_embedded_again(runtime: DatabaseRuntime) -> None:
    document_id = await a_document(runtime)
    await publish(runtime, ControlledModel(), document_id)

    # Only who may read it changes: the Chunks and their vectors stay.
    await reconcile_document(
        runtime,
        markdown(HANDBOOK),
        readers=[],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )
    access_only = ControlledModel()
    unchanged = await publish(runtime, access_only, document_id)
    # The text changes: new Chunks replace the old ones and their vectors.
    await a_document(runtime, EDITED, hours_later=2)
    edited = ControlledModel()
    republished = await publish(runtime, edited, document_id)

    assert unchanged.status is EmbeddingPublicationStatus.UNCHANGED
    assert access_only.calls == []
    assert republished.status is EmbeddingPublicationStatus.PUBLISHED
    assert len(edited.calls) == 3
    assert set(await stored(runtime)) == await chunk_texts(runtime)


@pytest.mark.integration
async def test_no_connection_is_held_while_the_model_works(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(runtime)
    model = ControlledModel(paused=True)

    task = asyncio.create_task(publish(runtime, model, document_id))
    await model.started.wait()
    held = cast(QueuePool, runtime.engine.pool).checkedout()
    model.release.set()
    result = await task

    assert held == 0
    assert result.status is EmbeddingPublicationStatus.PUBLISHED


@pytest.mark.integration
async def test_a_content_change_during_embedding_publishes_nothing(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(runtime)
    model = ControlledModel(paused=True)

    task = asyncio.create_task(publish(runtime, model, document_id))
    await model.started.wait()
    # The edit commits while the model works: no lock is held to wait for.
    async with asyncio.timeout(5):
        await a_document(runtime, EDITED, hours_later=1)
    model.release.set()
    result = await task

    assert result.status is EmbeddingPublicationStatus.STALE
    assert await stored(runtime) == {}
    # The next run embeds the new text.
    retry = await publish(runtime, ControlledModel(), document_id)
    assert retry.status is EmbeddingPublicationStatus.PUBLISHED
    assert set(await stored(runtime)) == await chunk_texts(runtime)


@pytest.mark.integration
async def test_a_tombstone_during_embedding_publishes_nothing(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(runtime)
    model = ControlledModel(paused=True)

    task = asyncio.create_task(publish(runtime, model, document_id))
    await model.started.wait()
    await tombstone_missing(
        runtime,
        source_id=SOURCE_ID,
        seen=set(),
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )
    model.release.set()

    assert (await task).status is EmbeddingPublicationStatus.STALE
    assert await stored(runtime) == {}


@pytest.mark.integration
async def test_two_publishers_of_one_document_write_one_set_of_vectors(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(runtime)
    first, second = ControlledModel(paused=True), ControlledModel(paused=True)

    tasks = [
        asyncio.create_task(publish(runtime, first, document_id)),
        asyncio.create_task(publish(runtime, second, document_id)),
    ]
    await first.started.wait()
    await second.started.wait()
    first.release.set()
    second.release.set()
    results = await asyncio.gather(*tasks)

    assert sorted(result.status for result in results) == [
        EmbeddingPublicationStatus.PUBLISHED,
        EmbeddingPublicationStatus.STALE,
    ]
    assert set(await stored(runtime)) == await chunk_texts(runtime)


@pytest.mark.integration
async def test_new_text_under_the_same_chunk_ids_publishes_nothing(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(runtime)
    model = ControlledModel(paused=True)

    task = asyncio.create_task(publish(runtime, model, document_id))
    await model.started.wait()
    # `publish_document` keeps the caller's Chunk IDs and accepts the same
    # content version, so only the text tells the old Chunks from the new.
    async with runtime.transaction() as connection:
        document = await load_document_by_id(connection, document_id=document_id)
        assert document is not None
        rewritten = tuple(
            replace(chunk, text=f"Rewritten section {chunk.ordinal}.")
            for chunk in await load_chunks(connection, document_id=document_id)
        )
        await publish_document(
            connection, Publication(document=document, chunks=rewritten)
        )
    model.release.set()

    assert (await task).status is EmbeddingPublicationStatus.STALE
    assert await stored(runtime) == {}


@pytest.mark.integration
async def test_a_tombstone_waits_until_the_snapshot_is_read(
    runtime: DatabaseRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await a_document(runtime)
    tombstoning: list[asyncio.Task[object]] = []

    async def load_chunks_while_tombstoning(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> object:
        if not tombstoning:
            tombstoning.append(
                asyncio.create_task(
                    tombstone_missing(
                        runtime,
                        source_id=SOURCE_ID,
                        seen=set(),
                        observed_at=OBSERVED_AT + timedelta(hours=1),
                    )
                )
            )
            # Give the tombstone time to commit, if nothing holds it back.
            await asyncio.sleep(0.3)
            assert not tombstoning[0].done(), "the tombstone did not wait"
        return await load_chunks(connection, document_id=document_id)

    monkeypatch.setattr(
        embedding_publication, "load_chunks", load_chunks_while_tombstoning
    )
    result = await publish(runtime, ControlledModel(), document_id)
    await tombstoning[0]

    # The text was read while the Document was available; the write saw the
    # tombstone and published nothing.
    assert result.status is EmbeddingPublicationStatus.STALE
    assert await stored(runtime) == {}


@pytest.mark.integration
async def test_a_space_replaced_during_embedding_publishes_nothing(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(runtime)
    model = ControlledModel(paused=True)

    task = asyncio.create_task(publish(runtime, model, document_id))
    await model.started.wait()
    # The space is deleted and created again for the same model identity.
    async with runtime.transaction() as connection:
        await connection.execute(sa.delete(embedding_spaces))
        await connection.execute(
            sa.insert(embedding_spaces).values(
                space_id=uuid4(),
                provider="fake",
                model="fake-embedding",
                dimensions=8,
                is_current=True,
                created_at=OBSERVED_AT,
            )
        )
    model.release.set()

    assert (await task).status is EmbeddingPublicationStatus.STALE
    assert await stored(runtime) == {}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("error", "deferred"),
    [(EmbeddingUnavailable(), 0), (EmbeddingRateLimited(), 1)],
    ids=["one-batch-fails", "the-rest-are-deferred"],
)
async def test_a_failed_batch_publishes_no_vector(
    runtime: DatabaseRuntime,
    error: EmbeddingError,
    deferred: int,
) -> None:
    document_id = await a_document(runtime)
    model = ControlledModel(fail_on=2, error=error)

    with pytest.raises(IncompleteEmbedding) as raised:
        await publish(runtime, model, document_id)

    # The first batch succeeded, yet none of its vectors were stored.
    assert raised.value.failed == 1
    assert raised.value.deferred == deferred
    assert raised.value.errors == (type(error).__name__,)
    assert await stored(runtime) == {}


@pytest.mark.integration
async def test_a_model_without_a_space_is_refused_before_sending(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(runtime)
    model = ControlledModel(dimensions=4)

    with pytest.raises(UnknownEmbeddingSpace):
        await publish(runtime, model, document_id)

    assert model.calls == []


@pytest.mark.integration
async def test_a_tombstoned_document_is_never_sent(runtime: DatabaseRuntime) -> None:
    document_id = await a_document(runtime)
    await tombstone_missing(
        runtime,
        source_id=SOURCE_ID,
        seen=set(),
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )
    model = ControlledModel()

    result = await publish(runtime, model, document_id)

    assert result.status is EmbeddingPublicationStatus.UNAVAILABLE
    assert model.calls == []


@pytest.mark.integration
async def test_errors_and_logs_carry_no_chunk_text(
    runtime: DatabaseRuntime,
    caplog: pytest.LogCaptureFixture,
) -> None:
    document_id = await a_document(runtime)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(IncompleteEmbedding) as raised:
        await publish(
            runtime,
            ControlledModel(fail_on=1, error=EmbeddingUnavailable()),
            document_id,
        )

    assert MARKER not in str(raised.value)
    assert MARKER not in repr(raised.value)
    assert MARKER not in caplog.text


@pytest.mark.integration
async def test_a_snapshot_never_shows_chunk_text(
    runtime: DatabaseRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = await a_document(runtime)
    # The snapshot is private, so record the ones the real call takes.
    take_snapshot = embedding_publication._snapshot  # pyright: ignore[reportPrivateUsage]
    snapshots: list[object] = []

    async def recording(
        connection: AsyncConnection,
        *,
        document_id: DocumentId,
        space_id: EmbeddingSpaceId,
    ) -> object:
        snapshot = await take_snapshot(
            connection, document_id=document_id, space_id=space_id
        )
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(embedding_publication, "_snapshot", recording)
    await publish(runtime, ControlledModel(), document_id)

    assert len(snapshots) == 2
    assert all(MARKER not in repr(snapshot) for snapshot in snapshots)
