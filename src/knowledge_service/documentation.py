"""Validation of repository Markdown links and curriculum metadata."""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

TASK_FIELDS = {
    "id",
    "title",
    "milestone",
    "track",
    "depends_on",
    "estimate",
    "topics",
    "deliverables",
    "status",
}
TASK_PATTERN = re.compile(r"M\d{2}-T\d{2}-[^/]+\.md$")
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
TASK_ID_PATTERN = re.compile(r"M\d{2}-T\d{2}")
VALID_STATUSES = {"planned", "in_progress", "completed", "blocked"}


@dataclass(frozen=True)
class ValidationIssue:
    """One documentation validation failure."""

    source: Path
    message: str


def _front_matter(
    source: Path, text: str
) -> tuple[dict[str, str], list[ValidationIssue]]:
    if not text.startswith("---\n"):
        return {}, []
    closing = text.find("\n---", 4)
    if closing == -1:
        return {}, [ValidationIssue(source, "front matter is not closed")]

    values: dict[str, str] = {}
    issues: list[ValidationIssue] = []
    for line in text[4:closing].splitlines():
        if not line.strip():
            continue
        if ":" not in line:
            issues.append(ValidationIssue(source, f"invalid front matter line: {line}"))
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip()
    return values, issues


def _validate_task_front_matter(
    source: Path, values: dict[str, str], issues: list[ValidationIssue]
) -> None:
    missing = sorted(TASK_FIELDS - values.keys())
    if missing:
        issues.append(
            ValidationIssue(source, f"missing front matter: {', '.join(missing)}")
        )
    status = values.get("status")
    if status is not None and status not in VALID_STATUSES:
        issues.append(ValidationIssue(source, f"invalid status: {status}"))
    task_id = values.get("id")
    if task_id is not None and not TASK_ID_PATTERN.fullmatch(task_id):
        issues.append(ValidationIssue(source, f"invalid task id: {task_id}"))


def _validate_link(source: Path, target: str) -> ValidationIssue | None:
    target = target.strip().split(maxsplit=1)[0].strip("<>")
    if not target or target.startswith(("http://", "https://", "mailto:", "#")):
        return None
    path = unquote(target.split("#", 1)[0])
    if not path:
        return None
    if not (source.parent / path).resolve().exists():
        return ValidationIssue(source, f"broken link: {target}")
    return None


def validate_repository(root: Path) -> list[ValidationIssue]:
    """Return all broken links, malformed task metadata, and dependencies."""
    issues: list[ValidationIssue] = []
    task_files = sorted(
        path
        for path in (root / "docs/curriculum/milestones").rglob("*.md")
        if TASK_PATTERN.fullmatch(path.name)
    )
    task_ids: dict[str, Path] = {}
    sources = [root / "README.md", root / "CONTEXT.md", *root.glob("docs/**/*.md")]

    for source in sources:
        if not source.exists():
            continue
        text = source.read_text()
        values, front_matter_issues = _front_matter(source, text)
        issues.extend(front_matter_issues)
        if source in task_files:
            _validate_task_front_matter(source, values, issues)
            task_id = values.get("id")
            if task_id is not None:
                if task_id in task_ids:
                    issues.append(
                        ValidationIssue(source, f"duplicate task id: {task_id}")
                    )
                task_ids[task_id] = source
        for target in LINK_PATTERN.findall(text):
            issue = _validate_link(source, target)
            if issue is not None:
                issues.append(issue)

    for source in task_files:
        values, _ = _front_matter(source, source.read_text())
        for dependency in TASK_ID_PATTERN.findall(values.get("depends_on", "")):
            if dependency not in task_ids:
                issues.append(
                    ValidationIssue(source, f"broken dependency: {dependency}")
                )
    return issues
