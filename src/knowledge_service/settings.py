"""Validated application configuration."""

from enum import StrEnum
from typing import Literal, Self

from pydantic import SecretStr, model_validator
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

    @model_validator(mode="after")
    def require_production_secret(self) -> Self:
        if self.environment is Environment.PRODUCTION and self.model_api_key is None:
            raise ValueError("MODEL_API_KEY is required in production")

        return self
