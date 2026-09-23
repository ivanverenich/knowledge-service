"""Tests for the reference-project comparison contract."""

from pathlib import Path

REFERENCE_LESSONS = Path(__file__).parents[1] / "docs" / "reference-project-lessons.md"
REQUIRED_REFERENCE_PATHS = (
    "`pyproject.toml`",
    "`browser_use/llm/base.py`",
    "`browser_use/llm/views.py`",
    "`browser_use/tokens/views.py`",
    "`tests/ci/infrastructure/test_config.py`",
    "`.github/workflows/lint.yml`",
    "`.github/workflows/test.yaml`",
    "`tests/ci/`",
)


def test_reference_comparison_lists_concrete_paths() -> None:
    comparison = REFERENCE_LESSONS.read_text(encoding="utf-8")

    assert "## M00-T12 concrete comparison" in comparison
    for reference_path in REQUIRED_REFERENCE_PATHS:
        assert reference_path in comparison


def test_reference_comparison_records_rejected_complexity() -> None:
    comparison = REFERENCE_LESSONS.read_text(encoding="utf-8")

    assert "### Complexity deliberately rejected" in comparison
    assert "Browser automation dependencies" in comparison
    assert "large CI matrix" in comparison
