"""Real PostgreSQL tests for metadata filters combined with vector ranking."""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import DocumentMetadata, InvalidDocument
from knowledge_service.embeddings import EmbeddingModelIdentity
from knowledge_service.identifiers import DocumentId, EmbeddingSpaceId, SourceId, UserId
from knowledge_service.language import Language
from knowledge_service.normalization import NormalizedDocument, normalize
from knowledge_service.persistence import (
    document_metadata,
    embedding_spaces,
    insert_chunk_embeddings,
    load_chunks,
    load_document_metadata,
    load_embedding_space_id,
)
from knowledge_service.reconciliation import Change, reconcile_document
from knowledge_service.retrieval_filters import (
    FilterField,
    FilterOperator,
    MetadataCondition,
    UnsupportedFilter,
)
from knowledge_service.settings import Settings
from knowledge_service.sources import SourceAccess, SourceMetadata, SourceRecord
from knowledge_service.vector_search import VectorSearchResult, search_similar_chunks

HANDBOOK = SourceId.parse("00000000-0000-0000-0000-000000000021")
WIKI = SourceId.parse("00000000-0000-0000-0000-000000000022")
OBSERVED_AT = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
IDENTITY = EmbeddingModelIdentity(provider="fake", model="fake-embedding", dimensions=3)
READER = UserId.parse("00000000-0000-0000-0000-0000000000a1")
OTHER_USER = UserId.parse("00000000-0000-0000-0000-0000000000a2")
CONTEXT = AuthorizationContext(user_id=READER)
QUERY = (1.0, 0.0, 0.0)
SINCE = datetime(2026, 10, 1, tzinfo=UTC)

ENGLISH = "The leave policy explains how employees request paid time off each year."
UKRAINIAN = (
    "Політика відпусток пояснює, як працівники отримують ґрунтовні дозволи"
    " щороку та їдуть у відпустку, якщо є потреба."  # noqa: RUF001
)

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " :location, 'Handbook', true, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        for source_id in (HANDBOOK, WIKI):
            await connection.execute(
                INSERT_SOURCE,
                {"source_id": source_id.value, "location": f"/srv/{source_id}"},
            )
        await connection.execute(
            sa.insert(embedding_spaces).values(
                space_id=EmbeddingSpaceId.new().value,
                provider=IDENTITY.provider,
                model=IDENTITY.model,
                dimensions=IDENTITY.dimensions,
                is_current=True,
                created_at=OBSERVED_AT,
            )
        )
    try:
        yield runtime
    finally:
        await runtime.dispose()


def a_record(
    name: str,
    *,
    source: SourceId,
    media_type: str,
    text: str,
    modified_at: datetime | None,
    version: str = "v1",
) -> NormalizedDocument:
    content = f"# {name}\n\n{text}" if media_type == "text/markdown" else text
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version=version,
            media_type=media_type,
            content=content.encode(),
            access=SourceAccess(public=True),
            metadata=(
                None
                if modified_at is None
                else SourceMetadata(title=name, modified_at=modified_at, size_bytes=1)
            ),
        ),
        source_id=source,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def a_document(
    runtime: DatabaseRuntime,
    record: NormalizedDocument,
    *,
    vector: Sequence[float],
    readers: Sequence[Subject] = (Subject.public(),),
) -> DocumentId:
    """Reconcile a one-Chunk Document, then give its Chunk the given vector."""
    reconciled = await reconcile_document(
        runtime, record, readers=readers, observed_at=OBSERVED_AT
    )
    document_id = reconciled.document.document_id
    async with runtime.transaction() as connection:
        space_id = await load_embedding_space_id(connection, IDENTITY)
        assert space_id is not None
        (chunk,) = await load_chunks(connection, document_id=document_id)
        await insert_chunk_embeddings(
            connection,
            space_id=space_id,
            dimensions=IDENTITY.dimensions,
            vectors={chunk.chunk_id: vector},
            created_at=OBSERVED_AT,
        )
    return document_id


# Five Documents, nearest to QUERY first. Each differs in Source, media type,
# language, or when the Source last changed it; "undated" has no such time.
CORPUS = {
    "en-old.txt": (HANDBOOK, "text/plain", ENGLISH, datetime(2026, 1, 1, tzinfo=UTC)),
    "undated.txt": (HANDBOOK, "text/plain", ENGLISH, None),
    "uk-new.md": (
        HANDBOOK,
        "text/markdown",
        UKRAINIAN,
        datetime(2026, 10, 20, tzinfo=UTC),
    ),
    "en-new.md": (WIKI, "text/markdown", ENGLISH, datetime(2026, 10, 21, tzinfo=UTC)),
    "uk-old.txt": (WIKI, "text/plain", UKRAINIAN, datetime(2026, 2, 1, tzinfo=UTC)),
}
VECTORS = {
    "en-old.txt": [1.0, 0.0, 0.0],
    "undated.txt": [1.0, 0.5, 0.0],
    "uk-new.md": [1.0, 1.0, 0.0],
    "en-new.md": [0.0, 1.0, 0.0],
    "uk-old.txt": [-1.0, 0.0, 0.0],
}


async def a_corpus(runtime: DatabaseRuntime) -> None:
    for name, (source, media_type, text, modified_at) in CORPUS.items():
        record = a_record(
            name,
            source=source,
            media_type=media_type,
            text=text,
            modified_at=modified_at,
        )
        await a_document(runtime, record, vector=VECTORS[name])


async def search(
    runtime: DatabaseRuntime,
    filters: Sequence[MetadataCondition],
    *,
    limit: int = 10,
    context: AuthorizationContext = CONTEXT,
) -> VectorSearchResult:
    async with runtime.transaction() as connection:
        return await search_similar_chunks(
            connection,
            identity=IDENTITY,
            query=QUERY,
            context=context,
            limit=limit,
            filters=filters,
        )


def names(result: VectorSearchResult) -> list[str]:
    return [candidate.external_id for candidate in result.candidates]


def sources(*source_ids: SourceId) -> MetadataCondition:
    return MetadataCondition(
        FilterField.SOURCE, FilterOperator.ANY_OF, frozenset(source_ids)
    )


def media_types(*values: str) -> MetadataCondition:
    return MetadataCondition(
        FilterField.MEDIA_TYPE, FilterOperator.ANY_OF, frozenset(values)
    )


def languages(*values: Language) -> MetadataCondition:
    return MetadataCondition(
        FilterField.LANGUAGE, FilterOperator.ANY_OF, frozenset(values)
    )


def modified_since(moment: datetime) -> MetadataCondition:
    return MetadataCondition(
        FilterField.MODIFIED_AT, FilterOperator.ON_OR_AFTER, moment
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ((), ["en-old.txt", "undated.txt", "uk-new.md", "en-new.md", "uk-old.txt"]),
        ((sources(HANDBOOK),), ["en-old.txt", "undated.txt", "uk-new.md"]),
        ((sources(WIKI),), ["en-new.md", "uk-old.txt"]),
        (
            (sources(HANDBOOK, WIKI),),
            ["en-old.txt", "undated.txt", "uk-new.md", "en-new.md", "uk-old.txt"],
        ),
        ((media_types("text/markdown"),), ["uk-new.md", "en-new.md"]),
        ((media_types("text/html"),), []),
        ((languages(Language.UKRAINIAN),), ["uk-new.md", "uk-old.txt"]),
        ((languages(Language.ENGLISH),), ["en-old.txt", "undated.txt", "en-new.md"]),
        # A Document whose Source gives no time never matches a recency filter.
        ((modified_since(SINCE),), ["uk-new.md", "en-new.md"]),
        ((modified_since(datetime(2026, 10, 21, tzinfo=UTC)),), ["en-new.md"]),
        ((sources(HANDBOOK), languages(Language.UKRAINIAN)), ["uk-new.md"]),
        (
            (media_types("text/markdown"), modified_since(SINCE)),
            ["uk-new.md", "en-new.md"],
        ),
        (
            (
                sources(WIKI),
                languages(Language.UKRAINIAN),
                media_types("text/markdown"),
            ),
            [],
        ),
    ],
)
async def test_each_filter_narrows_the_ranked_result(
    runtime: DatabaseRuntime,
    filters: tuple[MetadataCondition, ...],
    expected: list[str],
) -> None:
    await a_corpus(runtime)

    everything = await search(runtime, filters)
    nearest = await search(runtime, filters, limit=1)

    assert names(everything) == expected
    assert everything.truncated is False
    assert names(nearest) == expected[:1]
    assert nearest.truncated is (len(expected) > 1)


@pytest.mark.integration
async def test_a_filter_never_shows_a_document_the_reader_may_not_see(
    runtime: DatabaseRuntime,
) -> None:
    # The hidden Document matches every filter and is an exact match.
    await a_document(
        runtime,
        a_record(
            "hidden.md",
            source=WIKI,
            media_type="text/markdown",
            text=UKRAINIAN,
            modified_at=datetime(2026, 10, 25, tzinfo=UTC),
        ),
        vector=[1.0, 0.0, 0.0],
        readers=[Subject.user(OTHER_USER)],
    )
    await a_document(
        runtime,
        a_record(
            "visible.md",
            source=WIKI,
            media_type="text/markdown",
            text=UKRAINIAN,
            modified_at=datetime(2026, 10, 25, tzinfo=UTC),
        ),
        vector=[0.0, 1.0, 0.0],
    )
    every_filter = (
        sources(WIKI),
        media_types("text/markdown"),
        languages(Language.UKRAINIAN),
        modified_since(SINCE),
    )

    result = await search(runtime, every_filter, limit=1)

    assert names(result) == ["visible.md"]
    assert result.truncated is False


@pytest.mark.integration
async def test_reconciling_stores_and_updates_the_metadata(
    runtime: DatabaseRuntime,
) -> None:
    first = a_record(
        "policy.md",
        source=HANDBOOK,
        media_type="text/markdown",
        text=ENGLISH,
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    document_id = await a_document(runtime, first, vector=[1.0, 0.0, 0.0])
    translated = a_record(
        "policy.md",
        source=HANDBOOK,
        media_type="text/markdown",
        text=UKRAINIAN,
        modified_at=datetime(2026, 10, 20, tzinfo=UTC),
        version="v2",
    )

    async with runtime.transaction() as connection:
        stored = await load_document_metadata(connection, document_id=document_id)
        unknown = await load_document_metadata(connection, document_id=DocumentId.new())
    await reconcile_document(
        runtime,
        translated,
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )
    async with runtime.transaction() as connection:
        updated = await load_document_metadata(connection, document_id=document_id)

    assert unknown is None
    assert stored == DocumentMetadata(
        document_id=document_id,
        media_type="text/markdown",
        language=Language.ENGLISH,
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
        observed_at=OBSERVED_AT,
    )
    assert updated == DocumentMetadata(
        document_id=document_id,
        media_type="text/markdown",
        language=Language.UKRAINIAN,
        modified_at=datetime(2026, 10, 20, tzinfo=UTC),
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )


@pytest.mark.integration
async def test_an_unchanged_reconcile_does_not_rewrite_the_metadata(
    runtime: DatabaseRuntime,
) -> None:
    record = a_record(
        "policy.txt",
        source=HANDBOOK,
        media_type="text/plain",
        text=ENGLISH,
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await a_document(runtime, record, vector=[1.0, 0.0, 0.0])
    row_version = sa.text("SELECT xmin::text FROM document_metadata")

    async with runtime.transaction() as connection:
        before = await connection.scalar(row_version)
    await reconcile_document(
        runtime,
        record,
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )
    async with runtime.transaction() as connection:
        after = await connection.scalar(row_version)

    assert after == before


@pytest.mark.integration
async def test_a_document_without_metadata_matches_only_source_filters(
    runtime: DatabaseRuntime,
) -> None:
    record = a_record(
        "legacy.txt",
        source=HANDBOOK,
        media_type="text/plain",
        text=ENGLISH,
        modified_at=datetime(2026, 10, 20, tzinfo=UTC),
    )
    await a_document(runtime, record, vector=[1.0, 0.0, 0.0])
    # As for a Document reconciled before metadata was stored.
    async with runtime.transaction() as connection:
        await connection.execute(sa.delete(document_metadata))

    assert names(await search(runtime, ())) == ["legacy.txt"]
    assert names(await search(runtime, (sources(HANDBOOK),))) == ["legacy.txt"]
    assert names(await search(runtime, (languages(Language.ENGLISH),))) == []

    # The next reconcile, even an unchanged one, stores it.
    await reconcile_document(
        runtime,
        record,
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )

    assert names(await search(runtime, (languages(Language.ENGLISH),))) == [
        "legacy.txt"
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    "filters",
    [
        # Two conditions on one field.
        (languages(Language.ENGLISH), languages(Language.UKRAINIAN)),
        # A raw SQL fragment instead of a condition.
        ("language = 'uk' OR true",),
    ],
)
async def test_an_unsupported_filter_is_refused_before_the_search(
    runtime: DatabaseRuntime, filters: tuple[object, ...]
) -> None:
    await a_corpus(runtime)

    with pytest.raises(UnsupportedFilter):
        await search(runtime, cast(tuple[MetadataCondition, ...], filters))


@pytest.mark.integration
async def test_an_altered_operator_is_refused_before_the_search(
    runtime: DatabaseRuntime,
) -> None:
    await a_corpus(runtime)
    condition = languages(Language.UKRAINIAN)
    object.__setattr__(condition, "operator", "like")

    with pytest.raises(UnsupportedFilter):
        await search(runtime, (condition,))


@pytest.mark.integration
async def test_a_metadata_only_change_is_reported_and_stored(
    runtime: DatabaseRuntime,
) -> None:
    def observed(modified_at: datetime) -> NormalizedDocument:
        return a_record(
            "policy.txt",
            source=HANDBOOK,
            media_type="text/plain",
            text=ENGLISH,
            modified_at=modified_at,
        )

    document_id = await a_document(
        runtime, observed(datetime(2026, 1, 1, tzinfo=UTC)), vector=[1.0, 0.0, 0.0]
    )

    # Same text, readers, and source version: only the Source's time moved.
    result = await reconcile_document(
        runtime,
        observed(datetime(2026, 10, 20, tzinfo=UTC)),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )

    assert result.change is Change.METADATA
    assert names(await search(runtime, (modified_since(SINCE),))) == ["policy.txt"]
    async with runtime.transaction() as connection:
        stored = await load_document_metadata(connection, document_id=document_id)
    assert stored is not None
    assert stored.observed_at == OBSERVED_AT + timedelta(hours=1)


@pytest.mark.integration
async def test_a_late_observation_cannot_roll_the_metadata_back(
    runtime: DatabaseRuntime,
) -> None:
    def observed(modified_at: datetime) -> NormalizedDocument:
        return a_record(
            "policy.txt",
            source=HANDBOOK,
            media_type="text/plain",
            text=ENGLISH,
            modified_at=modified_at,
        )

    older, newer = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 10, 20, tzinfo=UTC)
    document_id = await a_document(runtime, observed(older), vector=[1.0, 0.0, 0.0])
    await reconcile_document(
        runtime,
        observed(newer),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=2),
    )

    # Made at +1h but delivered last, it still shows the older time.
    with pytest.raises(InvalidDocument):
        await reconcile_document(
            runtime,
            observed(older),
            readers=[Subject.public()],
            observed_at=OBSERVED_AT + timedelta(hours=1),
        )

    async with runtime.transaction() as connection:
        stored = await load_document_metadata(connection, document_id=document_id)
    assert stored is not None
    assert stored.modified_at == newer
