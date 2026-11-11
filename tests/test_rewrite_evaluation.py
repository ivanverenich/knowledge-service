"""Gain per rewrite class, the rollout decision, and the rewrite dataset."""

import json
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from knowledge_service.embeddings.port import EmbeddingModelIdentity
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    REWRITE_RETRIEVAL_FILE,
    CaseOutcome,
    InvalidDataset,
    QueryClass,
    RewriteCaseOutcome,
    RewriteComparison,
    load_dataset,
    load_rewrite_dataset,
    verify_corpus,
)
from knowledge_service.retrieval.query_rewriting import (
    RewriteOutcome,
    RewritePolicy,
    RewriterIdentity,
)
from knowledge_service.retrieval.rewrite_cases import (
    REWRITE_CASES_FILE,
    load_rewrite_cases,
)
from knowledge_service.retrieval.rewrite_classes import RewriteClass
from knowledge_service.retrieval.rewrite_evaluation import (
    Rollout,
    RolloutRefused,
    allowed_classes,
    check_rollout,
    render_rewrite_comparison,
    score_rewriting,
)

FOLLOW_UP, ACRONYM = RewriteClass.FOLLOW_UP, RewriteClass.ACRONYM
CROSS, IDENTIFIER = RewriteClass.CROSS_LANGUAGE, RewriteClass.EXACT_IDENTIFIER
MARKER = "PLANTED-MARKER-6613"

type Ranks = tuple[int | None, ...]


def outcome(case_id: str, ranks: Ranks, *, degraded: bool = False) -> CaseOutcome:
    return CaseOutcome(
        case_id=case_id,
        direction="en->en",
        query_class=QueryClass.PLAIN,
        ranks=ranks,
        degraded=degraded,
    )


def case(
    case_id: str,
    rewrite_class: RewriteClass,
    before: Ranks,
    after: Ranks | None = None,
    *,
    fallback: str | None = None,
    degraded: bool = False,
) -> RewriteCaseOutcome:
    if fallback is not None:
        result = RewriteOutcome.FALLBACK
    elif after is None:
        result = RewriteOutcome.NOT_REWRITABLE
    else:
        result = RewriteOutcome.REWRITTEN
    return RewriteCaseOutcome(
        case_id=case_id,
        rewrite_class=rewrite_class,
        outcome=result,
        fallback=fallback,
        before=outcome(case_id, before),
        after=outcome(case_id, before if after is None else after, degraded=degraded),
    )


def comparison(*cases: RewriteCaseOutcome) -> RewriteComparison:
    return RewriteComparison(
        name="rewrite-test",
        version=1,
        limit=10,
        embedding=EmbeddingModelIdentity(
            provider="fake", model="fake-embedding", dimensions=8
        ),
        rewriter=RewriterIdentity(provider="fake", model="fake-rewriter"),
        cases=cases,
        usages=(),
        cost_usd=Decimal("0.000123"),
    )


def gains_of(*cases: RewriteCaseOutcome) -> dict[RewriteClass, Any]:
    return {gain.rewrite_class: gain for gain in score_rewriting(comparison(*cases))}


def test_a_class_that_gains_and_harms_nothing_may_be_rolled_out() -> None:
    gains = gains_of(
        case("follow-1", FOLLOW_UP, (None,), (1,)),
        case("follow-2", FOLLOW_UP, (2,), (2,)),
        case("follow-3", FOLLOW_UP, (3, 4), (3, 2)),
    )

    gain = gains[FOLLOW_UP]
    assert (gain.cases, gain.rewritten, gain.fallbacks, gain.degraded) == (3, 3, 0, 0)
    assert (gain.mrr_before, gain.mrr_after) == (
        Fraction(5, 18),
        Fraction(2, 3),
    )
    assert gain.gain == Fraction(7, 18)
    assert (gain.improved, gain.harmed) == (("follow-1", "follow-3"), ())
    assert gain.rollout is Rollout.ALLOWED


@pytest.mark.parametrize(
    ("cases", "rollout"),
    [
        (
            [case("a-1", ACRONYM, (2,), (1,)), case("a-2", ACRONYM, (1,), (3,))],
            Rollout.HARMS,
        ),
        (
            [case("a-1", ACRONYM, (None,), (1,)), case("a-2", ACRONYM, (1,), (None,))],
            Rollout.HARMS,
        ),
        (
            [case("a-1", ACRONYM, (1, 2), (1, None)), case("a-2", ACRONYM, (2,), (1,))],
            Rollout.HARMS,
        ),
        (
            [
                case("a-1", ACRONYM, (2,), (1,)),
                case("a-2", ACRONYM, (2,), (1,)),
                case("a-3", ACRONYM, (1,), fallback="RewriterTimeout"),
            ],
            Rollout.INCOMPLETE,
        ),
        (
            [
                case("a-1", ACRONYM, (2,), (1,)),
                case("a-2", ACRONYM, (2,), (1,), degraded=True),
            ],
            Rollout.INCOMPLETE,
        ),
        (
            [case("a-1", ACRONYM, (1,), (1,)), case("a-2", ACRONYM, (2,), (2,))],
            Rollout.NO_GAIN,
        ),
        (
            [case("a-1", ACRONYM, (None,), (1,)), case("a-2", ACRONYM, (1,))],
            Rollout.TOO_FEW_CASES,
        ),
        ([], Rollout.TOO_FEW_CASES),
    ],
    ids=[
        "one-falls",
        "one-lost",
        "second-chunk-lost",
        "fallback",
        "degraded",
        "tie",
        "one-rewritten",
        "no-case",
    ],
)
def test_a_class_that_harms_or_was_not_fully_measured_is_held_back(
    cases: list[RewriteCaseOutcome], rollout: Rollout
) -> None:
    assert gains_of(*cases)[ACRONYM].rollout is rollout


def test_a_class_that_is_never_rewritten_is_never_rolled_out() -> None:
    gains = gains_of(case("id-1", IDENTIFIER, (1,)), case("id-2", IDENTIFIER, (2,)))

    assert gains[IDENTIFIER].rollout is Rollout.NEVER_REWRITTEN
    assert gains[IDENTIFIER].gain == 0
    assert gains[RewriteClass.SELF_CONTAINED].mrr_before is None
    assert gains[RewriteClass.SELF_CONTAINED].gain is None


def test_the_rollout_check_refuses_a_policy_the_measurement_did_not_allow() -> None:
    gains = score_rewriting(
        comparison(
            case("f-1", FOLLOW_UP, (None,), (1,)),
            case("f-2", FOLLOW_UP, (3,), (1,)),
            case("a-1", ACRONYM, (1,), (2,)),
            case("a-2", ACRONYM, (1,), (1,)),
        )
    )

    assert allowed_classes(gains) == {FOLLOW_UP}
    check_rollout(RewritePolicy(), gains)
    check_rollout(RewritePolicy(frozenset({FOLLOW_UP})), gains)
    with pytest.raises(RolloutRefused, match=r"acronym \(harms\)"):
        check_rollout(RewritePolicy(frozenset({FOLLOW_UP, ACRONYM})), gains)
    with pytest.raises(RolloutRefused, match=r"cross_language \(too_few_cases\)"):
        check_rollout(RewritePolicy(frozenset({CROSS})), gains)
    with pytest.raises(RolloutRefused, match="not measured"):
        check_rollout(RewritePolicy(frozenset({FOLLOW_UP})), ())
    with pytest.raises(TypeError):
        check_rollout(frozenset({FOLLOW_UP}), gains)  # pyright: ignore[reportArgumentType]


def test_the_report_names_cases_by_id_and_gives_the_largest_safe_policy() -> None:
    measured = comparison(
        case("f-1", FOLLOW_UP, (None,), (1,)),
        case("f-2", FOLLOW_UP, (3,), (1,)),
        case("a-1", ACRONYM, (2,), (1,)),
        case("a-2", ACRONYM, (3,), fallback="InvalidRewrite"),
        case("id-1", IDENTIFIER, (1,)),
    )

    rendered = render_rewrite_comparison(measured, score_rewriting(measured))

    assert rendered.startswith("# Query rewriting: rewrite-test v1\n")
    assert (
        "| follow_up | 2 | 2 | 0 | 0 | 0.167 | 1.000 | +0.833 | none | allowed |"
        in rendered
    )
    assert (
        "| acronym | 2 | 1 | 1 | 0 | 0.417 | 0.667 | +0.250 | none | incomplete |"
        in rendered
    )
    assert "| f-1 | follow_up | rewritten | missed | 1 |" in rendered
    assert "| a-2 | acronym | fallback: InvalidRewrite | 3 | 3 |" in rendered
    assert (
        "| exact_identifier | 1 | 0 | 0 | 0 | 1.000 | 1.000 | +0.000 | none"
        " | never_rewritten |" in rendered
    )
    assert "- Estimated cost: 0.000123 USD" in rendered
    assert "Largest safe policy: `QUERY_REWRITE_CLASSES='[\"follow_up\"]'`." in rendered


async def test_the_rewrite_dataset_joins_labels_with_their_answers() -> None:
    labelled = load_rewrite_cases(REWRITE_CASES_FILE)

    rewrite = load_rewrite_dataset(DATASET_DIRECTORY / REWRITE_RETRIEVAL_FILE, labelled)

    await verify_corpus(rewrite.dataset, DATASET_DIRECTORY / CORPUS_DIRECTORY)
    assert len(rewrite.labelled) == len(rewrite.dataset.cases)
    classes = {label.rewrite_class for label in rewrite.labelled}
    assert classes == set(RewriteClass)
    seed = {
        case.case_id: case
        for case in load_dataset(DATASET_DIRECTORY / CASES_FILE).cases
    }
    joined = {case.case_id: case for case in rewrite.dataset.cases}
    # Every seed case is measured, with the same Question and answers.
    assert seed.keys() <= joined.keys()
    for case_id, case in seed.items():
        assert joined[case_id] == case
    for label, case in zip(rewrite.labelled, rewrite.dataset.cases, strict=True):
        assert (label.question, label.language) == (
            case.question,
            case.question_language,
        )


def write(tmp_path: Path, content: object) -> Path:
    path = tmp_path / "rewrite-retrieval.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def valid() -> dict[str, Any]:
    return {
        "name": "rewrite-test",
        "version": 1,
        "cases": [
            {
                "id": "en-follow-up-abroad",
                "evidence_language": "uk",
                "query_class": "plain",
                "relevant": [
                    {
                        "document": "public/vidriadzhennia.md",
                        "heading": "Службові відрядження > Добові",
                    }
                ],
            }
        ],
    }


def edited(key: str, value: object) -> dict[str, Any]:
    content = valid()
    content["cases"][0][key] = value
    return content


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (edited("id", "no-such-case"), "names no labelled case"),
        (
            {**valid(), "cases": valid()["cases"] * 2},
            "names no labelled case, or one used twice",
        ),
        (
            edited("relevant", [{"document": "restricted/x.md", "heading": "X"}]),
            "relevant evidence must be in public/",
        ),
        (edited(MARKER, 1), "<unknown>"),
        (edited("evidence_language", "mixed"), "evidence_language"),
        ({**valid(), "name": "Not A Name"}, "name must be lowercase"),
    ],
    ids=["unknown-id", "twice", "restricted", "unknown-key", "language", "name"],
)
def test_a_rewrite_dataset_that_breaks_a_rule_is_refused_without_quoting_it(
    tmp_path: Path, content: object, message: str
) -> None:
    labelled = load_rewrite_cases(REWRITE_CASES_FILE)

    with pytest.raises(InvalidDataset, match=message) as caught:
        load_rewrite_dataset(write(tmp_path, content), labelled)

    assert MARKER not in str(caught.value)


def test_the_valid_template_loads(tmp_path: Path) -> None:
    labelled = load_rewrite_cases(REWRITE_CASES_FILE)

    rewrite = load_rewrite_dataset(write(tmp_path, valid()), labelled)

    (label,) = rewrite.labelled
    assert label.history
    assert rewrite.dataset.cases[0].question == label.question
