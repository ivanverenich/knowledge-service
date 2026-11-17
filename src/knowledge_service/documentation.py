"""Validation of repository Markdown links, curriculum metadata, and the
threat model's trace of every security invariant."""

import ast
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
SECURITY_MODEL = Path("docs/security.md")
THREAT_MODEL = Path("docs/threat-model.md")
INVARIANTS_HEADING = "## Invariants"
TRACE_HEADING = "## Invariant trace"
INVARIANT_PATTERN = re.compile(r"(\d+)\. \S")
LIST_ITEM_PATTERN = re.compile(r"\s*(?:\d+[.)]|[-*+])\s")
HIDDEN_PATTERN = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
OWNER_PATTERN = re.compile(r"`([^`]*)`")
TEST_OWNER_PATTERN = re.compile(
    r"(tests/(?:[A-Za-z0-9_]+/)*test_[A-Za-z0-9_]+\.py)"
    r"::((?:Test[A-Za-z0-9_]*::)?test_[A-Za-z0-9_]+)"
)
PLACEHOLDERS = {"-", "?", "tbd", "todo", "n/a", "none"}


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


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None


def _visible_lines(text: str) -> list[str]:
    """The lines a reader sees: no HTML comments and no fenced code blocks."""
    lines: list[str] = []
    fence: str | None = None
    for line in HIDDEN_PATTERN.sub("", text).splitlines():
        marker = line.lstrip()[:3]
        if fence is None and marker in ("```", "~~~"):
            fence = marker
        elif fence is not None:
            if marker == fence:
                fence = None
        else:
            lines.append(line)
    return lines


def _sections(lines: list[str], heading: str) -> list[list[str]]:
    """The lines under each `heading`, up to the next heading of level 1 or 2."""
    sections: list[list[str]] = []
    for start, line in enumerate(lines):
        if line.rstrip() != heading:
            continue
        end = next(
            (
                index
                for index in range(start + 1, len(lines))
                if lines[index].startswith(("# ", "## "))
            ),
            len(lines),
        )
        sections.append(lines[start + 1 : end])
    return sections


def _invariants(
    source: Path, lines: list[str]
) -> tuple[list[str], list[ValidationIssue]]:
    numbers: list[str] = []
    issues: list[ValidationIssue] = []
    for line in lines:
        if (match := INVARIANT_PATTERN.match(line)) is not None:
            numbers.append(match[1])
        elif LIST_ITEM_PATTERN.match(line):
            issues.append(
                ValidationIssue(
                    source, f"an invariant must be written as `N. text`: {line.strip()}"
                )
            )
    if numbers and numbers != [str(number) for number in range(1, len(numbers) + 1)]:
        issues.append(
            ValidationIssue(source, "invariants must be numbered 1, 2, 3, ... in order")
        )
    return numbers, issues


def _first_table(lines: list[str]) -> list[str]:
    """The data rows of the first table, without its header and the line under it."""
    table: list[str] = []
    for line in lines:
        if line.startswith("|"):
            table.append(line.strip())
        elif table:
            break
    return table[2:]


def _defined_tests(text: str) -> set[str] | None:
    """The tests pytest would collect from a module: `test_x` or `TestX::test_x`."""
    try:
        tree = ast.parse(text)
    except SyntaxError, ValueError:
        return None
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, functions):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            names.update(
                f"{node.name}::{item.name}"
                for item in node.body
                if isinstance(item, functions)
            )
    return names


def _task_statuses(root: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for path in (root / "docs/curriculum/milestones").rglob("*.md"):
        if TASK_PATTERN.fullmatch(path.name):
            values, _ = _front_matter(path, _text(path) or "")
            if "id" in values:
                statuses[values["id"]] = values.get("status", "")
    return statuses


class _Owners:
    """Checks test owners, reading and parsing each test file once."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._statuses = _task_statuses(root)
        self._tests: dict[str, set[str] | str] = {}

    def problem(self, owner: str) -> str | None:
        if TASK_ID_PATTERN.fullmatch(owner):
            if owner not in self._statuses:
                return f"unknown task: {owner}"
            if self._statuses[owner] == "completed":
                return f"{owner} is completed; name the test it added"
            return None
        test = TEST_OWNER_PATTERN.fullmatch(owner)
        if test is None:
            return f"an owner must be a test or a task: {owner}"
        defined = self._defined(test[1])
        if isinstance(defined, str):
            return defined
        if test[2] not in defined:
            return f"no such test: {owner}"
        return None

    def _defined(self, file: str) -> set[str] | str:
        if file not in self._tests:
            path = self._root / file
            text = _text(path) if path.is_file() else None
            if text is None:
                self._tests[file] = f"no readable test file: {file}"
            else:
                defined = _defined_tests(text)
                self._tests[file] = (
                    f"cannot parse test file: {file}" if defined is None else defined
                )
        return self._tests[file]


def validate_threat_model(root: Path) -> list[ValidationIssue]:
    """Check that the threat model traces every security invariant.

    Each invariant under "## Invariants" in `docs/security.md`, written
    `N. text` and numbered from 1, must have exactly one row in the first
    table under "## Invariant trace" in `docs/threat-model.md`, naming a
    control and at least one test owner: a test pytest would collect,
    written `tests/<file>.py::<test>` or `tests/<file>.py::<Class>::<test>`,
    or a task that is not yet completed and will add one. Only what a reader
    sees counts: HTML comments and fenced code blocks are skipped.
    """
    security, threat = root / SECURITY_MODEL, root / THREAT_MODEL
    security_text = _text(security)
    if security_text is None:
        return [ValidationIssue(security, "cannot read the security model")]
    found = _sections(_visible_lines(security_text), INVARIANTS_HEADING)
    if len(found) > 1:
        return [ValidationIssue(security, f"{INVARIANTS_HEADING} appears twice")]
    numbers, issues = _invariants(security, found[0] if found else [])
    if not numbers:
        return [*issues, ValidationIssue(security, "no numbered invariants to trace")]
    threat_text = _text(threat) if threat.is_file() else None
    if threat_text is None:
        return [*issues, ValidationIssue(threat, "missing threat model")]
    traces = _sections(_visible_lines(threat_text), TRACE_HEADING)
    if len(traces) != 1:
        problem = "appears twice" if traces else "is missing"
        return [*issues, ValidationIssue(threat, f"{TRACE_HEADING} {problem}")]

    owners = _Owners(root)
    traced: set[str] = set()
    for row in _first_table(traces[0]):
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if len(cells) != 3 or not cells[0].isdecimal() or not cells[0].isascii():
            issues.append(ValidationIssue(threat, f"malformed trace row: {row}"))
            continue
        number, control, named = cells[0], cells[1], OWNER_PATTERN.findall(cells[2])
        if number in traced:
            issues.append(
                ValidationIssue(threat, f"invariant {number} is traced twice")
            )
        traced.add(number)
        if number not in numbers:
            issues.append(ValidationIssue(threat, f"no invariant {number} to trace"))
        if control.lower() in PLACEHOLDERS or not any(c.isalpha() for c in control):
            issues.append(ValidationIssue(threat, f"invariant {number} has no control"))
        if not named:
            issues.append(
                ValidationIssue(threat, f"invariant {number} has no test owner")
            )
        for owner in named:
            problem = owners.problem(owner)
            if problem is not None:
                issues.append(ValidationIssue(threat, f"invariant {number}: {problem}"))
    issues.extend(
        ValidationIssue(threat, f"invariant {number} is not traced")
        for number in numbers
        if number not in traced
    )
    return issues


def validate_repository(root: Path) -> list[ValidationIssue]:
    """Return all broken links, malformed task metadata, and dependencies,
    and, when there is a security model, every gap in its trace."""
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
    if (root / SECURITY_MODEL).is_file():
        issues.extend(validate_threat_model(root))
    return issues
