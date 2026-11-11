"""The labelled rewrite cases: checked on load, and the rules measured on them."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    DATASET_DIRECTORY,
    load_dataset,
)
from knowledge_service.retrieval.rewrite_cases import (
    REWRITE_CASES_FILE,
    InvalidRewriteCases,
    load_rewrite_cases,
    render_rewrite_scores,
    score_rewrite_cases,
)
from knowledge_service.retrieval.rewrite_classes import RewriteClass

MARKER = "PLANTED-MARKER-4417"

type Content = dict[str, Any]
type Edit = Callable[[Content], None]


def test_the_rules_agree_with_every_verified_label() -> None:
    case_set = load_rewrite_cases(REWRITE_CASES_FILE)

    rows = score_rewrite_cases(case_set)

    assert [row.rewrite_class for row in rows] == list(RewriteClass)
    for row in rows:
        assert row.cases >= 2
        assert (row.assigned, row.kept, row.missed) == (row.cases, row.cases, ())


def test_every_seed_question_is_labelled_as_it_is_asked() -> None:
    seed = load_dataset(DATASET_DIRECTORY / CASES_FILE)
    labelled = {
        case.case_id: case for case in load_rewrite_cases(REWRITE_CASES_FILE).cases
    }

    for case in seed.cases:
        assert case.case_id in labelled
        assert labelled[case.case_id].question == case.question
        assert labelled[case.case_id].language is case.question_language


def test_the_report_names_cases_by_id_only() -> None:
    case_set = load_rewrite_cases(REWRITE_CASES_FILE)

    rendered = render_rewrite_scores(case_set, score_rewrite_cases(case_set))

    assert rendered.startswith("# Rewrite classes: bilingual-rewrite-classes v1\n")
    assert "| exact_identifier | 10 | 10 | 10 | no | none |" in rendered
    assert "| follow_up | 4 | 4 | 4 | yes | none |" in rendered
    for case in case_set.cases:
        assert case.question not in rendered
        assert all(word not in rendered for word in case.preserve)


def test_a_label_that_keeps_fewer_words_than_the_rules_is_missed(
    tmp_path: Path,
) -> None:
    content = valid()
    acronym = next(c for c in content["cases"] if c["id"] == "acronym-1")
    acronym.update(question="Where are the VPN and SSO pages?", preserve=["VPN"])
    case_set = load_rewrite_cases(write(tmp_path, content))

    rows = score_rewrite_cases(case_set)

    row = next(r for r in rows if r.rewrite_class is RewriteClass.ACRONYM)
    # The class is right, but the rules keep "SSO" too: the label is wrong.
    assert row.assigned == 1
    assert "acronym-1" in row.missed


def labelled(case_id: str, rewrite_class: str, **fields: object) -> dict[str, object]:
    return {
        "id": case_id,
        "question": f"What is {MARKER} for {case_id}?",
        "question_language": "en",
        "rewrite_class": rewrite_class,
        **fields,
    }


def valid() -> Content:
    cases: list[dict[str, object]] = []
    for name in RewriteClass:
        for number in (1, 2):
            extra: dict[str, object] = {}
            if name is RewriteClass.EXACT_IDENTIFIER:
                extra["preserve"] = [MARKER]
            if name is RewriteClass.FOLLOW_UP:
                extra["history"] = [f"Earlier {MARKER}?"]
            cases.append(labelled(f"{name.replace('_', '-')}-{number}", name, **extra))
    return {"name": "rewrite-test", "version": 1, "cases": cases}


def write(tmp_path: Path, content: Content) -> Path:
    path = tmp_path / "rewrite-classes.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def first(key: str, value: object) -> Edit:
    def edit(content: Content) -> None:
        content["cases"][0][key] = value

    return edit


def without_first_key(key: str) -> Edit:
    def edit(content: Content) -> None:
        del content["cases"][0][key]

    return edit


def duplicate_first(content: Content) -> None:
    content["cases"].append(dict(content["cases"][0]))


def one_acronym_only(content: Content) -> None:
    content["cases"] = [c for c in content["cases"] if c["id"] != "acronym-2"]


def unknown_key(content: Content) -> None:
    content["cases"][0][MARKER] = 1


def test_the_valid_template_loads(tmp_path: Path) -> None:
    case_set = load_rewrite_cases(write(tmp_path, valid()))

    assert len(case_set.cases) == 2 * len(RewriteClass)
    assert MARKER not in repr(case_set)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (first("preserve", [MARKER + "-not-there"]), "not a word of the question"),
        (first("preserve", ["PLANTED"]), "not a word of the question"),
        (first("preserve", [MARKER, MARKER]), "listed twice"),
        (without_first_key("preserve"), "must keep its identifier"),
        (first("rewrite_class", "follow_up"), "needs an earlier question"),
        (first("question", " "), "the question must be non-empty"),
        (first("question", MARKER + "x" * 1_000), "at most"),
        (first("history", [" "]), "an earlier question"),
        (first("history", [MARKER + "x" * 1_000]), "an earlier question"),
        (first("rewrite_class", MARKER), "rewrite_class"),
        (first("id", MARKER), "id"),
        (first("question_language", "mixed"), "question_language"),
        (duplicate_first, "more than once"),
        (one_acronym_only, "at least 2 cases"),
        (unknown_key, "<unknown>"),
    ],
)
def test_a_file_that_breaks_a_rule_is_refused_without_quoting_it(
    tmp_path: Path, edit: Edit, message: str
) -> None:
    content = valid()
    edit(content)

    with pytest.raises(InvalidRewriteCases, match=message) as caught:
        load_rewrite_cases(write(tmp_path, content))

    assert MARKER not in str(caught.value)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"\xff", "not valid UTF-8 JSON"),
        (b'{"name": "a", "name": "b"}', "not valid UTF-8 JSON"),
        (b'{"version": NaN}', "not valid UTF-8 JSON"),
        (b"[]", "file"),
    ],
)
def test_bytes_that_are_not_a_cases_file_are_refused(
    tmp_path: Path, raw: bytes, message: str
) -> None:
    path = tmp_path / "rewrite-classes.json"
    path.write_bytes(raw)

    with pytest.raises(InvalidRewriteCases, match=message):
        load_rewrite_cases(path)


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidRewriteCases, match="missing"):
        load_rewrite_cases(tmp_path / "absent.json")
