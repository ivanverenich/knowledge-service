"""Unit tests for database runtime construction."""

import pytest
from pydantic import SecretStr

from knowledge_service.database import (
    DatabaseConfigurationError,
    create_database_runtime,
)
from knowledge_service.settings import Settings


def test_runtime_requires_database_url() -> None:
    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL"):
        create_database_runtime(Settings())


async def test_runtime_uses_async_psycopg_and_disposes() -> None:
    settings = Settings(
        database_url=SecretStr(
            "postgresql://test_user:test_password@localhost:5432/test_db"
        )
    )
    runtime = create_database_runtime(settings)
    try:
        assert runtime.engine.url.drivername == "postgresql+psycopg"
        assert runtime.engine.dialect.is_async
    finally:
        await runtime.dispose()
