"""Real PostgreSQL tests: equal settings reuse an identity, changed ones do not."""

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import cast

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings import EmbeddingModelIdentity
from knowledge_service.identifiers import IndexConfigurationId
from knowledge_service.index_configuration import (
    IndexConfiguration,
    IndexConfigurationConflict,
    InvalidIndexConfiguration,
    NormalizationSettings,
    current_index_configuration,
)
from knowledge_service.persistence import (
    ensure_index_configuration,
    index_configurations,
)
from knowledge_service.settings import Settings

CREATED_AT = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)
MODEL = EmbeddingModelIdentity(provider="openai", model="embed-small", dimensions=8)
CURRENT = current_index_configuration(MODEL)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def ensure(
    runtime: DatabaseRuntime, configuration: IndexConfiguration
) -> IndexConfigurationId:
    async with runtime.transaction() as connection:
        return await ensure_index_configuration(
            connection, configuration, created_at=CREATED_AT
        )


async def stored_rows(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        count = await connection.scalar(
            sa.select(sa.func.count()).select_from(index_configurations)
        )
    return int(count or 0)


@pytest.mark.integration
async def test_identical_settings_reuse_the_stored_identity(
    runtime: DatabaseRuntime,
) -> None:
    first = await ensure(runtime, CURRENT)
    again = await ensure(runtime, current_index_configuration(MODEL))

    assert again == first
    assert await stored_rows(runtime) == 1


@pytest.mark.integration
async def test_the_stored_row_holds_the_fingerprint_and_settings(
    runtime: DatabaseRuntime,
) -> None:
    configuration_id = await ensure(runtime, CURRENT)

    async with runtime.transaction() as connection:
        row = (
            await connection.execute(
                sa.select(
                    index_configurations.c.fingerprint,
                    index_configurations.c.settings,
                ).where(
                    index_configurations.c.configuration_id == configuration_id.value
                )
            )
        ).one()

    assert row.fingerprint == CURRENT.fingerprint
    assert row.settings == CURRENT.settings()


@pytest.mark.integration
@pytest.mark.parametrize(
    "changed",
    [
        dataclasses.replace(
            CURRENT, embedding=EmbeddingModelIdentity("openai", "embed-large", 8)
        ),
        dataclasses.replace(
            CURRENT, embedding=EmbeddingModelIdentity("openai", "embed-small", 16)
        ),
        dataclasses.replace(
            CURRENT,
            chunker=dataclasses.replace(
                CURRENT.chunker, max_tokens=CURRENT.chunker.max_tokens + 1
            ),
        ),
        dataclasses.replace(
            CURRENT,
            normalization=dataclasses.replace(
                CURRENT.normalization, version="normalization/999"
            ),
        ),
        dataclasses.replace(CURRENT, vector_scaling="unit_length/999"),
    ],
    ids=["model", "dimensions", "chunker", "normalization", "scaling"],
)
async def test_a_changed_setting_creates_a_new_identity(
    runtime: DatabaseRuntime, changed: IndexConfiguration
) -> None:
    original = await ensure(runtime, CURRENT)

    new = await ensure(runtime, changed)
    again = await ensure(runtime, changed)

    assert new != original
    assert again == new
    assert await stored_rows(runtime) == 2


@pytest.mark.integration
async def test_settings_listed_in_another_order_reuse_the_identity(
    runtime: DatabaseRuntime,
) -> None:
    original = await ensure(runtime, CURRENT)
    reordered = dataclasses.replace(
        CURRENT,
        normalization=NormalizationSettings(
            version=CURRENT.normalization.version,
            parsers=tuple(reversed(CURRENT.normalization.parsers)),
            aliases=tuple(reversed(CURRENT.normalization.aliases)),
            unicode_version=CURRENT.normalization.unicode_version,
        ),
    )

    assert await ensure(runtime, reordered) == original


@pytest.mark.integration
async def test_two_callers_storing_one_new_configuration_get_one_row(
    runtime: DatabaseRuntime,
) -> None:
    identities = await asyncio.gather(*(ensure(runtime, CURRENT) for _ in range(4)))

    assert len(set(identities)) == 1
    assert await stored_rows(runtime) == 1


@pytest.mark.integration
async def test_a_stored_row_edited_after_the_fact_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    await ensure(runtime, CURRENT)
    # Rows are never updated by the application; simulate a manual edit.
    edited = CURRENT.settings() | {"vector_scaling": "edited"}
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(index_configurations).values(settings=edited)
        )

    with pytest.raises(IndexConfigurationConflict):
        await ensure(runtime, CURRENT)


@pytest.mark.integration
async def test_the_database_refuses_a_malformed_fingerprint(
    runtime: DatabaseRuntime,
) -> None:
    with pytest.raises(IntegrityError):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.insert(index_configurations).values(
                    configuration_id=IndexConfigurationId.new().value,
                    fingerprint="not-a-sha256",
                    settings={},
                    created_at=CREATED_AT,
                )
            )


@pytest.mark.integration
async def test_a_stored_number_written_as_a_float_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    await ensure(runtime, CURRENT)
    # 8.0 equals 8 in Python, so only the fingerprint of the row tells them
    # apart; this edit once went unnoticed.
    settings = CURRENT.settings()
    embedding = cast(dict[str, object], settings["embedding"])
    edited = settings | {"embedding": embedding | {"dimensions": 8.0}}
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(index_configurations).values(settings=edited)
        )

    with pytest.raises(IndexConfigurationConflict):
        await ensure(runtime, CURRENT)


@pytest.mark.integration
async def test_a_configuration_altered_after_it_was_made_is_never_stored(
    runtime: DatabaseRuntime,
) -> None:
    altered = dataclasses.replace(CURRENT)
    # Once stored, then failed in PostgreSQL with a raw DataError.
    object.__setattr__(altered, "vector_scaling", "unit\x00length")

    with pytest.raises(InvalidIndexConfiguration):
        await ensure(runtime, altered)
    assert await stored_rows(runtime) == 0
