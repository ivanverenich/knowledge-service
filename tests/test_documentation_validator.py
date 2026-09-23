from pathlib import Path

from knowledge_service.documentation import ValidationIssue, validate_repository

TASK = """---
id: M00-T01
title: Example
milestone: M00
track: critical
depends_on: []
estimate: 10m
topics: [docs]
deliverables: [validator]
status: planned
---

# Example
"""


def test_validator_accepts_valid_documentation(tmp_path: Path) -> None:
    task = tmp_path / "docs/curriculum/milestones/M00-foundations/M00-T01-example.md"
    task.parent.mkdir(parents=True)
    task.write_text(TASK)
    (tmp_path / "README.md").write_text(
        "[task](docs/curriculum/milestones/M00-foundations/M00-T01-example.md)"
    )

    assert validate_repository(tmp_path) == []


def test_validator_ignores_external_and_fragment_links(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text(
        "[external](https://example.com) [section](#section)"
    )

    assert validate_repository(tmp_path) == []


def test_validator_reports_and_then_accepts_repaired_dependency_and_link(
    tmp_path: Path,
) -> None:
    task = tmp_path / "docs/curriculum/milestones/M00-foundations/M00-T01-example.md"
    task.parent.mkdir(parents=True)
    task.write_text(TASK.replace("depends_on: []", "depends_on: [M00-T99]"))
    (tmp_path / "README.md").write_text("[missing](docs/missing.md)")

    issues = validate_repository(tmp_path)
    assert all(isinstance(issue, ValidationIssue) for issue in issues)
    assert any("broken dependency" in issue.message for issue in issues)
    assert any("broken link" in issue.message for issue in issues)

    task.write_text(TASK)
    (tmp_path / "README.md").write_text(
        "[task](docs/curriculum/milestones/M00-foundations/M00-T01-example.md)"
    )
    assert validate_repository(tmp_path) == []
