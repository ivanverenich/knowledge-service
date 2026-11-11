"""Verified Questions labelled with a rewrite class and the words to keep.

The cases file holds Questions a person has read and labelled: the class
from `retrieval.rewrite_classes` they belong to, and the words any rewrite
must keep exactly as typed. `score_rewrite_cases` then measures how often
`classify_question` agrees, class by class, so its rules are judged by
cases instead of by intuition.

- **Checked on load.** `load_rewrite_cases` raises `InvalidRewriteCases`
  for a file that is not strict JSON of the expected shape, a case ID used
  twice, a Question `classify_question` would refuse, a word to keep that
  is not a whole word of its Question or is listed twice, an
  `exact_identifier` case that keeps no word, a `follow_up`
  case with no earlier Question, or a class with fewer than two cases. So
  scoring a loaded set never raises.
- **Quiet.** Errors and reports name cases by ID and give classes and
  counts only; they never hold a Question or a word of one.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knowledge_service.language import Language
from knowledge_service.retrieval.rewrite_classes import (
    MAX_HISTORY,
    REWRITABLE,
    InvalidQuestion,
    RewriteClass,
    RewritePlan,
    classify_question,
    question_words,
)

REWRITE_CASES_FILE = Path("datasets/bilingual-seed/rewrite-classes.json")
MAX_FILE_BYTES = 1_000_000
MIN_CASES_PER_CLASS = 2


class InvalidRewriteCases(ValueError):
    """The rewrite cases file breaks a rule; the message quotes no value."""


@dataclass(frozen=True, slots=True)
class RewriteCase:
    """One labelled Question. The texts are hidden from the repr."""

    case_id: str
    question: str = field(repr=False)
    language: Language
    history: tuple[str, ...] = field(repr=False)
    rewrite_class: RewriteClass
    preserve: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class RewriteCaseSet:
    """A versioned set of labelled Questions."""

    name: str
    version: int
    cases: tuple[RewriteCase, ...]


@dataclass(frozen=True, slots=True)
class ClassAgreement:
    """How `classify_question` did on the cases labelled with one class.

    `assigned` counts cases given their labelled class; `kept` counts cases
    whose plan keeps exactly the labelled words, in order, no more and no
    fewer. `missed` names the cases that failed either check.
    """

    rewrite_class: RewriteClass
    cases: int
    assigned: int
    kept: int
    missed: tuple[str, ...]


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _CaseFile(_File):
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    question: str = Field(min_length=1)
    question_language: Literal["en", "uk"]
    history: list[str] = Field(default_factory=list[str], max_length=MAX_HISTORY)
    rewrite_class: Literal[
        "exact_identifier", "follow_up", "acronym", "cross_language", "self_contained"
    ]
    preserve: list[str] = Field(default_factory=list[str])


class _SetFile(_File):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    version: int = Field(ge=1)
    cases: list[_CaseFile] = Field(min_length=1)


_FIELDS = frozenset(
    name for model in (_SetFile, _CaseFile) for name in model.model_fields
)


def load_rewrite_cases(path: Path) -> RewriteCaseSet:
    """Read and check a rewrite cases file; raise `InvalidRewriteCases`."""
    # A pipe or a device would block the read, or never end.
    if not path.is_file():
        raise InvalidRewriteCases("the file is missing or not a regular file")
    try:
        with path.open("rb") as file:
            raw = file.read(MAX_FILE_BYTES + 1)
    except OSError as error:
        raise InvalidRewriteCases(
            f"the file cannot be read: {type(error).__name__}"
        ) from None
    if len(raw) > MAX_FILE_BYTES:
        raise InvalidRewriteCases(f"the file is larger than {MAX_FILE_BYTES} bytes")
    try:
        content = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_constant=_no_constant,
        )
    except ValueError, RecursionError:
        raise InvalidRewriteCases("the file is not valid UTF-8 JSON") from None
    try:
        parsed = _SetFile.model_validate(content)
    except ValidationError as error:
        raise InvalidRewriteCases(_describe(error)) from None
    cases = tuple(_case(index, item) for index, item in enumerate(parsed.cases))
    _check_set(cases)
    return RewriteCaseSet(name=parsed.name, version=parsed.version, cases=cases)


def plan_case(case: RewriteCase) -> RewritePlan:
    """Classify one labelled case's Question as a caller would."""
    return classify_question(
        case.question, language=case.language, history=case.history
    )


def score_rewrite_cases(case_set: RewriteCaseSet) -> tuple[ClassAgreement, ...]:
    """Measure `classify_question` against the labels, one row per class."""
    rows: list[ClassAgreement] = []
    for rewrite_class in RewriteClass:
        labelled = [c for c in case_set.cases if c.rewrite_class is rewrite_class]
        assigned = kept = 0
        missed: list[str] = []
        for case in labelled:
            plan = plan_case(case)
            right_class = plan.rewrite_class is rewrite_class
            all_kept = plan.preserve == case.preserve
            assigned += right_class
            kept += all_kept
            if not (right_class and all_kept):
                missed.append(case.case_id)
        rows.append(
            ClassAgreement(
                rewrite_class=rewrite_class,
                cases=len(labelled),
                assigned=assigned,
                kept=kept,
                missed=tuple(missed),
            )
        )
    return tuple(rows)


def render_rewrite_scores(
    case_set: RewriteCaseSet, rows: Sequence[ClassAgreement]
) -> str:
    """Return the agreement table as Markdown, naming cases by ID only."""
    lines = [
        f"# Rewrite classes: {case_set.name} v{case_set.version}",
        "",
        "| Class | Cases | Class assigned | Words kept | May rewrite | Missed |",
        "|---|---:|---:|---:|---|---|",
    ]
    for row in rows:
        may = "yes" if row.rewrite_class in REWRITABLE else "no"
        missed = ", ".join(row.missed) or "none"
        lines.append(
            f"| {row.rewrite_class} | {row.cases} | {row.assigned} | {row.kept}"
            f" | {may} | {missed} |"
        )
    return "\n".join(lines) + "\n"


def _case(index: int, item: _CaseFile) -> RewriteCase:
    language = Language(item.question_language)
    history = tuple(item.history)
    try:
        # The same checks a caller meets, so scoring cannot fail later.
        classify_question(item.question, language=language, history=history)
    except InvalidQuestion as error:
        raise InvalidRewriteCases(f"cases.{index}: {error}") from None
    words = question_words(item.question)
    for word in item.preserve:
        # Whole words only, as typed: "EXP" does not keep "EXP-2041".
        if word not in words:
            raise InvalidRewriteCases(
                f"cases.{index}.preserve: a word to keep is not a word of the question"
            )
    if len(set(item.preserve)) != len(item.preserve):
        raise InvalidRewriteCases(f"cases.{index}.preserve: a word is listed twice")
    # Which word is checked by scoring, not here: the labels must not
    # depend on the rules they measure.
    if item.rewrite_class == "exact_identifier" and not item.preserve:
        raise InvalidRewriteCases(
            f"cases.{index}: an exact_identifier case must keep its identifier"
        )
    if item.rewrite_class == "follow_up" and not history:
        raise InvalidRewriteCases(
            f"cases.{index}: a follow_up case needs an earlier question"
        )
    return RewriteCase(
        case_id=item.id,
        question=item.question,
        language=language,
        history=history,
        rewrite_class=RewriteClass(item.rewrite_class),
        preserve=tuple(item.preserve),
    )


def _check_set(cases: tuple[RewriteCase, ...]) -> None:
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise InvalidRewriteCases(f"case {case.case_id} appears more than once")
        seen.add(case.case_id)
    for rewrite_class in RewriteClass:
        count = sum(case.rewrite_class is rewrite_class for case in cases)
        if count < MIN_CASES_PER_CLASS:
            raise InvalidRewriteCases(
                f"class {rewrite_class} needs at least {MIN_CASES_PER_CLASS} cases"
            )


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("a key appears twice in one object")
    return dict(pairs)


def _no_constant(name: str) -> None:
    raise ValueError("NaN and Infinity are not JSON")


def _describe(error: ValidationError) -> str:
    # The message names the place and the rule, never a value.
    first = error.errors(include_input=False, include_url=False)[0]
    parts = [
        str(part) if isinstance(part, int) or part in _FIELDS else "<unknown>"
        for part in first["loc"]
    ]
    return f"{'.'.join(parts) or 'file'}: {first['msg']}"
