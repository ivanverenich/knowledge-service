"""Tests for settings.py."""

import os
from pathlib import Path

import pytest

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


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_SERVICE_ENVIRONMENT", "production")
    monkeypatch.setenv("KNOWLEDGE_SERVICE_MODEL_API_KEY", "TEST_API_KEY")
    monkeypatch.setenv("KNOWLEDGE_SERVICE_LOG_LEVEL", "DEBUG")

    settings = Settings()

    assert settings.environment == "production"
    assert settings.model_api_key is not None
    assert settings.model_api_key.get_secret_value() == "TEST_API_KEY"
    assert settings.log_level == "DEBUG"


def test_missing_model_api_key_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KNOWLEDGE_SERVICE_ENVIRONMENT", "production")
    monkeypatch.delenv("KNOWLEDGE_SERVICE_MODEL_API_KEY", raising=False)

    with pytest.raises(ValueError, match="MODEL_API_KEY is required in production"):
        Settings()


def test_redaction_of_model_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_value = "visible-test-value"
    monkeypatch.setenv("KNOWLEDGE_SERVICE_MODEL_API_KEY", raw_value)

    settings = Settings()

    assert str(settings.model_api_key) == "**********"
    assert raw_value not in repr(settings)
    assert settings.model_api_key is not None
    assert settings.model_api_key.get_secret_value() == raw_value
