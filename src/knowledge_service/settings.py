"""Validated application configuration."""

from enum import StrEnum
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
    model_api_key: SecretStr | None = None
    database_url: SecretStr | None = None
    database_pool_size: int = Field(default=5, gt=0)
    database_pool_max_overflow: int = Field(default=5, ge=0)
    database_pool_timeout: float = Field(default=10.0, gt=0.0, allow_inf_nan=False)
    database_connect_timeout_seconds: int = Field(default=5, gt=0)
    database_statement_timeout_ms: int = Field(default=10_000, gt=0)

    @model_validator(mode="after")
    def require_production_secret(self) -> Self:
        if self.environment is Environment.PRODUCTION:
            if self.model_api_key is None:
                raise ValueError("MODEL_API_KEY is required in production")
            if self.database_url is None:
                raise ValueError("DATABASE_URL is required in production")

        return self
