"""Tests for settings.py."""

import os
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from knowledge_service.settings import Settings


@pytest.fixture(autouse=True)
def isolate_settings_sources(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for variable in tuple(os.environ):
        if variable.upper().startswith("KNOWLEDGE_SERVICE_"):
            monkeypatch.delenv(variable)

    monkeypatch.chdir(tmp_path)


def test_default_values() -> None:
    settings = Settings()

    assert settings.environment == "local"
    assert settings.log_level == "INFO"
    assert settings.model_api_key is None
    assert settings.database_url is None
    assert settings.database_pool_size == 5
    assert settings.database_pool_max_overflow == 5
    assert settings.database_connect_timeout_seconds == 5
    assert settings.database_statement_timeout_ms == 10_000


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_pool_size", 0),
        ("database_pool_timeout", float("inf")),
        ("database_pool_timeout", float("nan")),
        ("database_connect_timeout_seconds", 0),
        ("database_statement_timeout_ms", 0),
    ],
)
def test_database_limits_reject_unbounded_or_nonpositive_values(
    field: str,
    value: int | float,
) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({field: value})


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_SERVICE_ENVIRONMENT", "production")
    monkeypatch.setenv("KNOWLEDGE_SERVICE_MODEL_API_KEY", "TEST_API_KEY")
    monkeypatch.setenv("KNOWLEDGE_SERVICE_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv(
        "KNOWLEDGE_SERVICE_DATABASE_URL", "postgresql://user:password@localhost/dbname"
    )

    settings = Settings()

    assert settings.environment == "production"
    assert settings.model_api_key is not None
    assert settings.model_api_key.get_secret_value() == "TEST_API_KEY"
    assert settings.log_level == "DEBUG"
    assert settings.database_url is not None
    assert str(settings.database_url) == "**********"


def test_missing_model_api_key_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_SERVICE_ENVIRONMENT", "production")
    monkeypatch.delenv("KNOWLEDGE_SERVICE_MODEL_API_KEY", raising=False)

    with pytest.raises(ValueError, match="MODEL_API_KEY is required in production"):
        Settings()


def test_missing_database_url_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_SERVICE_ENVIRONMENT", "production")
    monkeypatch.setenv("KNOWLEDGE_SERVICE_MODEL_API_KEY", "TEST_API_KEY")
    monkeypatch.delenv("KNOWLEDGE_SERVICE_DATABASE_URL", raising=False)

    with pytest.raises(ValueError, match="DATABASE_URL is required in production"):
        Settings()


def test_database_url_is_redacted() -> None:
    raw_url = "postgresql://test_user:test_password@localhost:5432/test_db"

    settings = Settings(database_url=SecretStr(raw_url))

    assert settings.database_url is not None
    assert str(settings.database_url) == "**********"
    assert raw_url not in repr(settings)


def test_test_environment_does_not_require_model_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KNOWLEDGE_SERVICE_ENVIRONMENT", "test")

    settings = Settings()

    assert settings.environment == "test"
    assert settings.model_api_key is None


def test_redaction_of_model_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_value = "visible-test-value"
    monkeypatch.setenv("KNOWLEDGE_SERVICE_MODEL_API_KEY", raw_value)

    settings = Settings()

    assert str(settings.model_api_key) == "**********"
    assert raw_value not in repr(settings)
    assert settings.model_api_key is not None
    assert settings.model_api_key.get_secret_value() == raw_value
