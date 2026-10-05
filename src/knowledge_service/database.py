"""Asynchronous PostgreSQL engine and session lifecycle."""

from dataclasses import dataclass

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from knowledge_service.settings import Settings


class DatabaseConfigurationError(ValueError):
    """Database configuration is absent or cannot create a PostgreSQL runtime."""


@dataclass(frozen=True, slots=True)
class DatabaseRuntime:
    """Process-owned async engine and factory for per-operation sessions."""

    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    async def dispose(self) -> None:
        """Close pooled connections during application shutdown."""
        await self.engine.dispose()


def create_database_runtime(settings: Settings) -> DatabaseRuntime:
    """Create a lazy PostgreSQL runtime from validated settings."""
    if settings.database_url is None:
        raise DatabaseConfigurationError("DATABASE_URL is required")

    try:
        parsed_url: URL = make_url(settings.database_url.get_secret_value())
    except ArgumentError:
        raise DatabaseConfigurationError("DATABASE_URL is invalid") from None

    if parsed_url.drivername not in {
        "postgresql",
        "postgresql+psycopg",
        "postgresql+psycopg_async",
    }:
        raise DatabaseConfigurationError("DATABASE_URL must use PostgreSQL")

    async_url = parsed_url.set(drivername="postgresql+psycopg")
    engine = create_async_engine(
        async_url,
        echo=False,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_max_overflow,
        pool_timeout=settings.database_pool_timeout,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": settings.database_connect_timeout_seconds,
            "options": (
                f"-c statement_timeout={settings.database_statement_timeout_ms}"
            ),
        },
    )
    sessions = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    return DatabaseRuntime(engine=engine, sessions=sessions)
