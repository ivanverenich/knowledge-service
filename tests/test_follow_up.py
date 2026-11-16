"""Follow-up resolution: only follow-ups, language and entities kept, traced."""
# ruff: noqa: RUF001

import logging
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from knowledge_service.answering.follow_up import (
    FOLLOW_UP_ONLY,
    ResolvedQuestion,
    earlier_questions,
    resolve_question,
)
from knowledge_service.answering.history import History, HistoryTurn
from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.query_rewriting import (
    InvalidRewriteRequest,
    RewriteOutcome,
    RewritePolicy,
    RewriterUnavailable,
)
from knowledge_service.retrieval.rewrite_classes import (
    MAX_HISTORY,
    REWRITABLE,
    InvalidQuestion,
    RewriteClass,
    RewritePlan,
    question_words,
)
from rewriting_fakes import FakeRewriteModel

REQUEST = UUID("00000000-0000-0000-0000-0000000000a1")
EN = Language.ENGLISH
UK = Language.UKRAINIAN
MARKER = "PLANTED-MARKER-7719"
EVERYTHING = RewritePolicy(REWRITABLE)


def history(*questions: str, answers: tuple[str | None, ...] = ()) -> History:
    """Every Question as a whole turn, with its Answer if one is given."""
    turns = tuple(
        HistoryTurn(
            question=question,
            answer=answers[n] if n < len(answers) else None,
            references=(),
        )
        for n, question in enumerate(questions)
    )
    return History(earlier_questions=(), turns=turns, tokens=0, left_out=0, withheld=0)


def says(text: object) -> FakeRewriteModel:
    def answer(question: str, plan: RewritePlan, history: tuple[str, ...]) -> object:
        return text

    return FakeRewriteModel(answer=answer)


async def resolve(
    model: FakeRewriteModel,
    question: str,
    earlier: History,
    *,
    language: Language = EN,
    policy: RewritePolicy = FOLLOW_UP_ONLY,
) -> ResolvedQuestion:
    return await resolve_question(
        model,
        question,
        language=language,
        history=earlier,
        policy=policy,
        request_id=REQUEST,
        timeout_seconds=5.0,
    )


ALLOWANCE = "How much is the daily allowance for business trips?"
ABROAD = "What is the daily allowance for business trips abroad?"


# --- What is resolved -----------------------------------------------------------------


async def test_a_follow_up_is_resolved_with_the_earlier_questions() -> None:
    model = says(ABROAD)

    resolved = await resolve(model, "And abroad?", history(ALLOWANCE))

    assert resolved.search_text == ABROAD
    assert resolved.question == "And abroad?"
    assert resolved.resolved
    assert resolved.rewrite_class is RewriteClass.FOLLOW_UP
    assert resolved.outcome is RewriteOutcome.REWRITTEN
    assert resolved.earlier_questions == 1
    assert resolved.usage is not None
    assert [(q, h) for q, _, h in model.calls] == [("And abroad?", (ALLOWANCE,))]


async def test_a_ukrainian_follow_up_is_resolved_in_ukrainian() -> None:
    model = says("Які добові у відрядженні за кордоном?")

    resolved = await resolve(
        model, "А за кордоном?", history("Які добові у відрядженні?"), language=UK
    )

    assert resolved.search_text == "Які добові у відрядженні за кордоном?"
    assert resolved.language is UK


@pytest.mark.parametrize(
    ("question", "rewrite_class"),
    [
        ("What is the VPN used for in the office?", RewriteClass.ACRONYM),
        ("Як налаштувати Outlook на ноутбуці вдома?", RewriteClass.CROSS_LANGUAGE),
        ("What does error EXP-2041 mean for my report?", RewriteClass.EXACT_IDENTIFIER),
        ("Who approves expense reports in the company?", RewriteClass.SELF_CONTAINED),
    ],
    ids=["acronym", "cross-language", "identifier", "self-contained"],
)
async def test_only_a_follow_up_is_ever_sent(
    question: str, rewrite_class: RewriteClass
) -> None:
    model = FakeRewriteModel()
    language = UK if question.startswith("Як") else EN

    resolved = await resolve(
        model, question, history(ALLOWANCE), language=language, policy=EVERYTHING
    )

    assert model.calls == []
    assert resolved.rewrite_class is rewrite_class
    assert resolved.search_text == question
    assert not resolved.resolved


async def test_a_short_question_with_no_history_is_not_a_follow_up() -> None:
    model = FakeRewriteModel()

    resolved = await resolve(model, "And abroad?", history())

    assert model.calls == []
    assert resolved.rewrite_class is RewriteClass.SELF_CONTAINED


async def test_a_follow_up_is_not_sent_when_the_policy_leaves_it_off() -> None:
    model = FakeRewriteModel()
    acronyms = RewritePolicy(frozenset({RewriteClass.ACRONYM}))

    resolved = await resolve(model, "And abroad?", history(ALLOWANCE), policy=acronyms)

    assert model.calls == []
    assert resolved.outcome is RewriteOutcome.SWITCHED_OFF


@pytest.mark.parametrize("language", [Language.MIXED, Language.UNKNOWN])
async def test_a_follow_up_in_a_language_that_cannot_be_checked_is_not_sent(
    language: Language,
) -> None:
    model = FakeRewriteModel()

    resolved = await resolve(
        model, "And abroad?", history(ALLOWANCE), language=language
    )

    assert model.calls == []
    assert resolved.outcome is RewriteOutcome.SWITCHED_OFF


# --- What is sent ---------------------------------------------------------------------


async def test_only_questions_are_sent_never_an_answer() -> None:
    model = says(ABROAD)
    earlier = history(ALLOWANCE, answers=(f"It is 30 EUR. {MARKER}",))

    await resolve(model, "And abroad?", earlier)

    assert all(MARKER not in repr(call) for call in model.calls)


async def test_summarized_questions_come_first_and_only_the_newest_are_sent() -> None:
    model = says(ABROAD)
    questions = [f"Question number {n} about leave?" for n in range(14)]
    earlier = History(
        earlier_questions=tuple(questions[:7]),
        turns=history(*questions[7:]).turns,
        tokens=0,
        left_out=0,
        withheld=0,
    )

    await resolve(model, "And abroad?", earlier)

    assert model.calls[0][2] == tuple(questions[-MAX_HISTORY:])


async def test_an_earlier_question_too_long_to_search_is_left_out() -> None:
    long_question = "leave " * (MAX_QUERY_CHARACTERS // 6 + 1)

    assert earlier_questions(history(long_question, ALLOWANCE)) == (ALLOWANCE,)


# --- What a rewrite must keep ---------------------------------------------------------


@pytest.mark.parametrize(
    ("earlier", "question", "rewrite", "carried"),
    [
        (
            "What does error EXP-2041 mean?",
            "How do I fix it?",
            "How do I fix the expense report error?",
            ("EXP-2041",),
        ),
        (
            "How do I set up the VPN on a laptop?",
            "And on phones?",
            "How do I set up a secure connection on phones?",
            ("VPN",),
        ),
    ],
)
async def test_a_rewrite_that_drops_an_entity_of_the_earlier_question_is_not_used(
    earlier: str, question: str, rewrite: str, carried: tuple[str, ...]
) -> None:
    resolved = await resolve(says(rewrite), question, history(earlier))

    assert resolved.search_text == question
    assert resolved.outcome is RewriteOutcome.FALLBACK
    assert resolved.fallback == "InvalidRewrite"
    assert resolved.carried == carried
    assert resolved.usage is not None


async def test_a_rewrite_that_keeps_the_entity_is_used() -> None:
    rewrite = "How do I fix error EXP-2041?"

    resolved = await resolve(
        says(rewrite), "How do I fix it?", history("What does error EXP-2041 mean?")
    )

    assert resolved.search_text == rewrite
    assert resolved.carried == ("EXP-2041",)


async def test_a_ukrainian_follow_up_rewritten_in_english_is_not_used() -> None:
    resolved = await resolve(
        says("Daily allowance abroad"),
        "А за кордоном?",
        history("Які добові у відрядженні?"),
        language=UK,
    )

    assert resolved.search_text == "А за кордоном?"
    assert resolved.fallback == "InvalidRewrite"


async def test_a_failing_rewriter_keeps_the_question() -> None:
    model = FakeRewriteModel(error=RewriterUnavailable())

    resolved = await resolve(model, "And abroad?", history(ALLOWANCE))

    assert resolved.search_text == "And abroad?"
    assert resolved.fallback == "RewriterUnavailable"
    assert resolved.usage is None


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=120))
async def test_any_text_the_model_returns_is_used_only_when_it_keeps_the_entities(
    text: str,
) -> None:
    earlier = history("What does error EXP-2041 mean with the VPN?")

    resolved = await resolve(says(text), "How do I fix it?", earlier)

    if resolved.resolved:
        words = question_words(resolved.search_text)
        assert {"EXP-2041", "VPN"} <= set(words)
    else:
        assert resolved.search_text == "How do I fix it?"


# --- Typed refusals and quiet records -------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"history": (ALLOWANCE,)}, InvalidRewriteRequest),
        ({"policy": frozenset({RewriteClass.FOLLOW_UP})}, InvalidRewriteRequest),
        ({"language": "en"}, InvalidQuestion),
        ({"question": "x" * (MAX_QUERY_CHARACTERS + 1)}, InvalidQuestion),
        ({"question": "   "}, InvalidQuestion),
        ({"timeout_seconds": 0}, InvalidRewriteRequest),
    ],
)
async def test_a_bad_request_is_refused_before_anything_is_sent(
    fields: dict[str, Any], error: type[Exception]
) -> None:
    model = FakeRewriteModel()
    request: dict[str, Any] = {
        "question": "And abroad?",
        "language": EN,
        "history": history(ALLOWANCE),
        "policy": FOLLOW_UP_ONLY,
        "request_id": REQUEST,
        "timeout_seconds": 5.0,
    } | fields

    with pytest.raises(error):
        await resolve_question(model, **request)

    assert model.calls == []


async def test_no_question_or_rewrite_reaches_a_repr_or_a_log_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    earlier = history(f"What does {MARKER} mean?")

    resolved = await resolve(
        says(f"How do I fix {MARKER}?"), "How do I fix it?", earlier
    )

    assert MARKER not in repr(resolved)
    assert all(MARKER not in record.getMessage() for record in caplog.records)
    assert all(MARKER not in repr(record.__dict__) for record in caplog.records)
    assert [r.getMessage() for r in caplog.records].count("follow-up resolved") == 1


def plan_of(model: FakeRewriteModel) -> RewritePlan:
    return model.calls[0][1]


async def test_the_model_is_told_the_follow_up_class_and_the_stated_language() -> None:
    model = says(ABROAD)

    await resolve(model, "And abroad?", history(ALLOWANCE))

    assert plan_of(model).rewrite_class is RewriteClass.FOLLOW_UP
    assert plan_of(model).language is EN


# --- Review regressions ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "language", "earlier", "rewrite"),
    [
        (
            "А за кордоном?",
            UK,
            "How much is the daily allowance for business trips abroad?",
            "How much is the daily allowance for business trips abroad",
        ),
        (
            "And abroad?",
            EN,
            "Які добові у відрядженні за кордоном?",
            "Які добові у відрядженні за кордоном",
        ),
    ],
    ids=["uk-after-english", "en-after-ukrainian"],
)
async def test_a_follow_up_rewritten_in_the_language_of_an_earlier_question_is_not_used(
    question: str, language: Language, earlier: str, rewrite: str
) -> None:
    resolved = await resolve(
        says(rewrite), question, history(earlier), language=language
    )

    assert resolved.search_text == question
    assert resolved.fallback == "InvalidRewrite"


@pytest.mark.parametrize(
    "fields",
    [
        {"earlier_questions": None},
        {"earlier_questions": (None,)},
        {"earlier_questions": "What is VPN"},
        {"turns": ("And abroad?",)},
    ],
    ids=["no-questions", "not-text", "one-string", "not-turns"],
)
async def test_a_history_that_is_not_one_is_refused_before_anything_is_sent(
    fields: dict[str, Any],
) -> None:
    model = FakeRewriteModel()
    broken = History(
        earlier_questions=fields.get("earlier_questions", ()),
        turns=fields.get("turns", ()),
        tokens=0,
        left_out=0,
        withheld=0,
    )

    with pytest.raises(InvalidRewriteRequest):
        await resolve(model, "And abroad?", broken)

    assert model.calls == []


async def test_the_questions_sent_always_end_at_the_newest_one() -> None:
    model = says("How do I fix error EXP-2041?")
    newest = "error EXP-9999 " * (MAX_QUERY_CHARACTERS // 15 + 1)

    resolved = await resolve(
        model, "How do I fix it?", history("What does error EXP-2041 mean?", newest)
    )

    assert model.calls == []
    assert resolved.earlier_questions == 0
    assert resolved.search_text == "How do I fix it?"
