"""Tests for the Alembic migration environment."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from knowledge_service.database import DatabaseConfigurationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def migration_config() -> Config:
    return Config(PROJECT_ROOT / "alembic.ini")


def test_baseline_is_the_single_root_revision() -> None:
    scripts = ScriptDirectory.from_config(migration_config())

    heads = scripts.get_heads()
    assert len(heads) == 1, "There should be a single head revision"
    baseline = scripts.get_revision(heads[0])
    assert baseline is not None, "Baseline revision should exist"
    assert baseline.down_revision is None, (
        "Baseline revision should have no down_revision"
    )


def test_upgrade_requires_a_database_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("KNOWLEDGE_SERVICE_ENVIRONMENT", "local")
    monkeypatch.delenv("KNOWLEDGE_SERVICE_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(DatabaseConfigurationError, match="DATABASE_URL"):
        command.upgrade(migration_config(), "head")
