"""Validated application configuration."""

from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Environment enum for the application."""

    LOCAL = "local"
    PRODUCTION = "production"
    TEST = "test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KNOWLEDGE_SERVICE_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    environment: Environment = Environment.LOCAL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Development only: lets logs hold raw Questions, Evidence, and Answers.
    telemetry_diagnostics: bool = False
    model_api_key: SecretStr | None = None
    database_url: SecretStr | None = None
    database_pool_size: int = Field(default=5, gt=0)
    database_pool_max_overflow: int = Field(default=5, ge=0)
    database_pool_timeout: float = Field(default=10.0, gt=0.0, allow_inf_nan=False)
    database_connect_timeout_seconds: int = Field(default=5, gt=0)
    database_statement_timeout_ms: int = Field(default=10_000, gt=0)
    # How often each enabled Source is synchronized (`ingestion.scheduling`).
    synchronization_interval_minutes: int = Field(default=60, ge=1, le=7 * 24 * 60)
    # How long a Source that follows changes may go without a full pass.
    full_synchronization_interval_hours: int = Field(default=24, ge=1, le=30 * 24)
    # A JSON file mapping Source principals to identity-provider users and
    # groups (`ingestion.grant_normalization.PrincipalDirectory`). Without
    # it, only items a Source marks public are readable.
    principal_directory_file: Path | None = None
    # The Redis broker that carries worker messages; it may hold a password.
    redis_url: SecretStr | None = None
    redis_health_timeout_seconds: float = Field(
        default=2.0, ge=0.05, le=30.0, allow_inf_nan=False
    )

    @model_validator(mode="after")
    def require_production_secret(self) -> Self:
        if self.environment is Environment.PRODUCTION:
            if self.model_api_key is None:
                raise ValueError("MODEL_API_KEY is required in production")
            if self.database_url is None:
                raise ValueError("DATABASE_URL is required in production")
            if self.telemetry_diagnostics:
                raise ValueError("TELEMETRY_DIAGNOSTICS is not allowed in production")

        return self
