"""Real PostgreSQL tests for transaction and engine lifecycle."""

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from knowledge_service.database import create_database_runtime
from knowledge_service.settings import Settings


@pytest.mark.integration
async def test_postgresql_commit_rollback_timeout_and_disposal(
    migrated_database: str,
) -> None:
    settings = Settings(
        database_url=SecretStr(migrated_database),
        database_statement_timeout_ms=100,
    )
    runtime = create_database_runtime(settings)
    try:
        async with runtime.sessions() as session:
            original_backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            await session.execute(
                text(
                    "CREATE TEMPORARY TABLE m02_t03_probe "
                    "(value integer) ON COMMIT PRESERVE ROWS"
                )
            )
            await session.execute(text("INSERT INTO m02_t03_probe (value) VALUES (1)"))
            await session.commit()

            await session.execute(text("INSERT INTO m02_t03_probe (value) VALUES (2)"))
            await session.rollback()

            count = await session.scalar(text("SELECT count(*) FROM m02_t03_probe"))
            assert count == 1

            with pytest.raises(DBAPIError):
                await session.execute(text("SELECT pg_sleep(0.25)"))
            await session.rollback()

        await runtime.dispose()
        async with runtime.sessions() as session:
            replacement_backend_pid = await session.scalar(
                text("SELECT pg_backend_pid()")
            )
        assert replacement_backend_pid != original_backend_pid
    finally:
        await runtime.dispose()
