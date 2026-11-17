"""The threat model traces every security invariant to a control and a test owner."""

from pathlib import Path

import pytest

from knowledge_service.documentation import (
    ValidationIssue,
    validate_repository,
    validate_threat_model,
)

ROOT = Path(__file__).resolve().parents[1]

SECURITY = """# Security model

## Invariants

1. A Chunk is eligible only when a grant matches.
2. Authorization occurs inside the queries.

## Trust zones
"""

TESTS = """
def test_eligibility() -> None: ...


class TestQueries:
    async def test_in_the_query(self) -> None: ...
"""

TASK = """---
id: {task_id}
title: Example
milestone: M07
track: critical
depends_on: []
estimate: 10m
topics: [security]
deliverables: [tests]
status: {status}
---
"""

ROWS = [
    "| 1 | the predicate | `tests/test_access.py::test_eligibility`, `M07-T07` |",
    "| 2 | in the query | `tests/test_access.py::TestQueries::test_in_the_query` |",
]


def repository(
    root: Path,
    rows: list[str] | None = None,
    *,
    security: str = SECURITY,
    statuses: dict[str, str] | None = None,
) -> Path:
    """A small repository with a security model, a trace, a test, and tasks."""
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs/security.md").write_text(security)
    table = ["| Invariant | Control | Test owners |", "|---|---|---|"]
    (root / "docs/threat-model.md").write_text(
        "# Threat model\n\n## Invariant trace\n\n"
        + "\n".join([*table, *(ROWS if rows is None else rows)])
        + "\n\n## Known gaps\n\n| 9 | not | a trace row |\n"
    )
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests/test_access.py").write_text(TESTS)
    milestone = root / "docs/curriculum/milestones/M07-identity"
    milestone.mkdir(parents=True, exist_ok=True)
    for task_id, status in (statuses or {"M07-T07": "planned"}).items():
        (milestone / f"{task_id}-example.md").write_text(
            TASK.format(task_id=task_id, status=status)
        )
    return root


def messages(issues: list[ValidationIssue]) -> list[str]:
    return [issue.message for issue in issues]


def with_invariant(line: str) -> str:
    """The security model with one more line at the end of its invariants."""
    return SECURITY.replace("\n## Trust zones", f"{line}\n\n## Trust zones")


def test_the_repository_threat_model_traces_every_security_invariant() -> None:
    assert validate_threat_model(ROOT) == []


def test_a_complete_trace_has_no_issues(tmp_path: Path) -> None:
    assert validate_threat_model(repository(tmp_path)) == []


def test_an_invariant_added_without_a_trace_row_is_reported(tmp_path: Path) -> None:
    security = with_invariant("3. Restrictive changes apply at once.")

    issues = validate_threat_model(repository(tmp_path, security=security))

    assert messages(issues) == ["invariant 3 is not traced"]
    assert issues[0].source == tmp_path / "docs/threat-model.md"


@pytest.mark.parametrize(
    ("owner", "problem"),
    [
        (
            "`tests/test_access.py::test_renamed`",
            "no such test: tests/test_access.py::test_renamed",
        ),
        (
            "`tests/test_gone.py::test_eligibility`",
            "no readable test file: tests/test_gone.py",
        ),
        ("`M07-T99`", "unknown task: M07-T99"),
        (
            "`tests/test_access.py::test_eligibility[case]`",
            "an owner must be a test or a task: "
            "tests/test_access.py::test_eligibility[case]",
        ),
        (
            "`tests/../src/test_x.py::test_eligibility`",
            "an owner must be a test or a task: "
            "tests/../src/test_x.py::test_eligibility",
        ),
        (
            "`tests/helpers.py::test_eligibility`",
            "an owner must be a test or a task: tests/helpers.py::test_eligibility",
        ),
        ("`the security team`", "an owner must be a test or a task: the security team"),
    ],
    ids=[
        "renamed-test",
        "missing-file",
        "unknown-task",
        "parametrized",
        "outside",
        "not-collected-file",
        "prose",
    ],
)
def test_an_owner_that_names_no_test_or_open_task_is_reported(
    tmp_path: Path, owner: str, problem: str
) -> None:
    rows = [f"| 1 | the predicate | {owner} |", ROWS[1]]

    assert messages(validate_threat_model(repository(tmp_path, rows))) == [
        f"invariant 1: {problem}"
    ]


def test_a_completed_task_cannot_stay_a_test_owner(tmp_path: Path) -> None:
    root = repository(tmp_path, statuses={"M07-T07": "completed"})

    assert messages(validate_threat_model(root)) == [
        "invariant 1: M07-T07 is completed; name the test it added"
    ]


@pytest.mark.parametrize(
    "module",
    [
        "# def test_eligibility() -> None: ...\n",
        "def test_eligibility_later() -> None: ...\n",
        'TEXT = """\ndef test_eligibility():\n"""\n',
        "if False:\n    def test_eligibility() -> None: ...\n",
        "def helper() -> None:\n    def test_eligibility() -> None: ...\n",
        "class Helper:\n    def test_eligibility(self) -> None: ...\n",
    ],
    ids=[
        "comment",
        "longer-name",
        "string",
        "dead-branch",
        "nested",
        "not-a-test-class",
    ],
)
def test_a_test_owner_must_be_a_test_pytest_collects(
    tmp_path: Path, module: str
) -> None:
    root = repository(tmp_path)
    tests = TESTS.replace("def test_eligibility", "def other")
    (root / "tests/test_access.py").write_text(tests + module)

    assert messages(validate_threat_model(root)) == [
        "invariant 1: no such test: tests/test_access.py::test_eligibility"
    ]


def test_a_test_written_with_a_space_before_its_parameters_counts(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    (root / "tests/test_access.py").write_text(
        TESTS.replace("def test_eligibility()", "def test_eligibility ()")
    )

    assert validate_threat_model(root) == []


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (b"\xff\ndef test_eligibility(): pass\n", "no readable test file"),
        (b"def test_eligibility(:\n", "cannot parse test file"),
        (b"def test_eligibility(): pass\x00\n", "cannot parse test file"),
    ],
    ids=["not-utf-8", "syntax-error", "null-byte"],
)
def test_a_test_file_that_cannot_be_read_is_reported_not_raised(
    tmp_path: Path, content: bytes, problem: str
) -> None:
    root = repository(tmp_path)
    (root / "tests/test_access.py").write_bytes(content)

    assert messages(validate_threat_model(root)) == [
        f"invariant 1: {problem}: tests/test_access.py",
        f"invariant 2: {problem}: tests/test_access.py",
    ]


@pytest.mark.parametrize(
    ("rows", "problems"),
    [
        (["| 1 |  | `M07-T07` |", ROWS[1]], ["invariant 1 has no control"]),
        (["| 1 | TBD | `M07-T07` |", ROWS[1]], ["invariant 1 has no control"]),
        (["| 1 | - | `M07-T07` |", ROWS[1]], ["invariant 1 has no control"]),
        (
            ["| 1 | the predicate | nobody yet |", ROWS[1]],
            ["invariant 1 has no test owner"],
        ),
        ([*ROWS, ROWS[0]], ["invariant 1 is traced twice"]),
        ([*ROWS, "| 7 | something | `M07-T07` |"], ["no invariant 7 to trace"]),
        (
            [ROWS[0], "| two | in the query | `M07-T07` |"],
            [
                "malformed trace row: | two | in the query | `M07-T07` |",
                "invariant 2 is not traced",
            ],
        ),
    ],
    ids=[
        "no-control",
        "placeholder",
        "dash",
        "no-owner",
        "twice",
        "unknown",
        "malformed",
    ],
)
def test_a_row_that_traces_nothing_is_reported(
    tmp_path: Path, rows: list[str], problems: list[str]
) -> None:
    assert messages(validate_threat_model(repository(tmp_path, rows))) == problems


@pytest.mark.parametrize(
    "hidden",
    [
        f"<!--\n{ROWS[1]}\n-->",
        f"```\n{ROWS[1]}\n```",
        f"### Archived\n\n{'| Invariant | Control | Test owners |'}\n|---|---|---|\n"
        f"{ROWS[1]}",
    ],
    ids=["comment", "fence", "second-table"],
)
def test_a_row_outside_the_visible_trace_table_traces_nothing(
    tmp_path: Path, hidden: str
) -> None:
    root = repository(tmp_path, [ROWS[0], "", hidden])

    assert messages(validate_threat_model(root)) == ["invariant 2 is not traced"]


@pytest.mark.parametrize(
    "line",
    [
        "3.  Two spaces.",
        "3.\tA tab.",
        "3) A parenthesis.",
        "   3. Indented.",
        "- A bullet.",
    ],
    ids=["two-spaces", "tab", "parenthesis", "indented", "bullet"],
)
def test_an_invariant_in_another_list_shape_is_reported(
    tmp_path: Path, line: str
) -> None:
    issues = validate_threat_model(repository(tmp_path, security=with_invariant(line)))

    assert messages(issues) == [
        f"an invariant must be written as `N. text`: {line.strip()}"
    ]
    assert issues[0].source == tmp_path / "docs/security.md"


def test_invariants_must_count_up_from_one(tmp_path: Path) -> None:
    security = SECURITY.replace("2. Authorization", "1. Authorization")

    issues = validate_threat_model(repository(tmp_path, security=security))

    assert messages(issues) == [
        "invariants must be numbered 1, 2, 3, ... in order",
        "no invariant 2 to trace",
    ]


def test_an_example_in_a_fenced_block_is_not_an_invariant(tmp_path: Path) -> None:
    security = with_invariant("\n```\n3. An example, not a rule.\n```")

    assert validate_threat_model(repository(tmp_path, security=security)) == []


def test_a_second_invariants_or_trace_heading_is_reported(tmp_path: Path) -> None:
    root = repository(tmp_path, security=f"{SECURITY}\n## Invariants\n\n3. More.\n")

    assert messages(validate_threat_model(root)) == ["## Invariants appears twice"]

    root = repository(tmp_path)
    threat = root / "docs/threat-model.md"
    threat.write_text(f"{threat.read_text()}\n## Invariant trace\n")

    assert messages(validate_threat_model(root)) == ["## Invariant trace appears twice"]


def test_a_missing_threat_model_or_trace_is_reported(tmp_path: Path) -> None:
    root = repository(tmp_path)
    threat = root / "docs/threat-model.md"
    threat.write_text("# Threat model\n\n## Abuse cases\n")

    assert messages(validate_threat_model(root)) == ["## Invariant trace is missing"]

    threat.unlink()

    assert messages(validate_threat_model(root)) == ["missing threat model"]


def test_a_security_model_with_no_invariants_cannot_pass(tmp_path: Path) -> None:
    security = SECURITY.replace("## Invariants", "## Rules")

    issues = validate_threat_model(repository(tmp_path, security=security))

    assert messages(issues) == ["no numbered invariants to trace"]
    assert issues[0].source == tmp_path / "docs/security.md"


def test_the_documentation_check_runs_the_trace(tmp_path: Path) -> None:
    root = repository(tmp_path, [ROWS[0]])

    assert messages(validate_repository(root)) == ["invariant 2 is not traced"]
