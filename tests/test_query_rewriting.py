"""Query rewriting: behind a policy, checked, and safe when it fails."""
# ruff: noqa: RUF001

import logging
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.query_rewriting import (
    InvalidRewrite,
    InvalidRewriteRequest,
    RewriteAnswer,
    RewriteDefect,
    RewriteOutcome,
    RewritePolicy,
    RewriterError,
    RewriteResult,
    RewriterMalformedResponse,
    RewriterRateLimited,
    RewriterRequestRefused,
    RewriterTimeout,
    RewriterUnavailable,
    checked_rewrite,
    rewrite_question,
)
from knowledge_service.retrieval.rewrite_classes import (
    InvalidQuestion,
    RewriteClass,
    RewritePlan,
    classify_question,
    question_words,
)
from rewriting_fakes import FakeRewriteModel

EN, UK = Language.ENGLISH, Language.UKRAINIAN
REQUEST_ID = UUID("00000000-0000-0000-0000-000000000099")
MARKER = "PLANTED-MARKER-5521"
# A planted word with no digit, so its Question is still rewritable.
WORD = "zanzibarquokka"
ALL = RewritePolicy(
    frozenset(
        {RewriteClass.FOLLOW_UP, RewriteClass.ACRONYM, RewriteClass.CROSS_LANGUAGE}
    )
)
FOLLOW_UP = ("And abroad?", EN, ("What is the daily allowance?",))


class Sneaky(str):
    """A str subclass that runs its own code when read."""

    __slots__ = ()

    def strip(self, chars: str | None = None) -> str:
        raise RuntimeError(self)


class UnknownFailure(RewriterError):
    """A port error the fallback does not name."""


def answering(text: object) -> FakeRewriteModel:
    return FakeRewriteModel(answer=lambda question, plan, history: text)


async def rewrite(
    model: FakeRewriteModel,
    question: str,
    language: Language,
    history: tuple[str, ...] = (),
    *,
    policy: RewritePolicy = ALL,
    timeout_seconds: float = 5.0,
) -> RewriteResult:
    return await rewrite_question(
        model,
        question,
        language=language,
        history=history,
        policy=policy,
        request_id=REQUEST_ID,
        timeout_seconds=timeout_seconds,
    )


async def test_a_follow_up_is_rewritten_when_its_class_is_switched_on() -> None:
    model = FakeRewriteModel()

    result = await rewrite(model, *FOLLOW_UP)

    assert result.outcome is RewriteOutcome.REWRITTEN
    assert result.search_text == "What is the daily allowance? And abroad?"
    assert result.plan.rewrite_class is RewriteClass.FOLLOW_UP
    assert result.fallback is None
    assert result.usage is not None
    assert result.usage.input_tokens == 7
    # The model sees the Question, its history, and the plan; nothing else.
    assert model.calls == [(FOLLOW_UP[0], result.plan, FOLLOW_UP[2])]
    assert "abroad" not in repr(result)


async def test_by_default_nothing_is_sent_and_the_question_is_kept() -> None:
    model = FakeRewriteModel()

    result = await rewrite(model, *FOLLOW_UP, policy=RewritePolicy())

    assert result.outcome is RewriteOutcome.SWITCHED_OFF
    assert result.search_text == FOLLOW_UP[0]
    assert (result.fallback, result.usage) == (None, None)
    assert model.calls == []


async def test_only_the_classes_switched_on_are_sent() -> None:
    model = FakeRewriteModel()
    policy = RewritePolicy(frozenset({RewriteClass.ACRONYM}))

    follow_up = await rewrite(model, *FOLLOW_UP, policy=policy)
    acronym = await rewrite(model, "Як підключитися до VPN з дому?", UK, policy=policy)

    assert follow_up.outcome is RewriteOutcome.SWITCHED_OFF
    assert acronym.outcome is RewriteOutcome.UNCHANGED
    assert [call[0] for call in model.calls] == ["Як підключитися до VPN з дому?"]


@pytest.mark.parametrize(
    ("question", "history"),
    [
        ("How do I fix it for EXP-2041?", ("What does error EXP-2041 mean?",)),
        ("And .env?", ("Where do I keep the database password?",)),
        ("Who pays for sick leave?", ()),
    ],
)
async def test_an_identifier_or_a_clear_question_is_never_sent(
    question: str, history: tuple[str, ...]
) -> None:
    model = FakeRewriteModel()

    result = await rewrite(model, question, EN, history)

    assert result.outcome is RewriteOutcome.NOT_REWRITABLE
    assert result.search_text == question
    assert model.calls == []


async def test_a_rewrite_may_keep_a_foreign_word_it_must_keep() -> None:
    model = answering("Як налаштувати електронну пошту в програмі Outlook?")

    result = await rewrite(model, "Як налаштувати пошту в Outlook?", UK)

    assert result.outcome is RewriteOutcome.REWRITTEN
    assert result.plan.preserve == ("Outlook",)


@pytest.mark.parametrize(
    ("text", "defect"),
    [
        (None, RewriteDefect.NO_TEXT),
        (5, RewriteDefect.NOT_TEXT),
        (Sneaky("What is the daily allowance abroad?"), RewriteDefect.NOT_TEXT),
        ("   ", RewriteDefect.NO_WORDS),
        ("? - ?", RewriteDefect.NO_WORDS),
        ("x" * (MAX_QUERY_CHARACTERS + 1), RewriteDefect.TOO_LONG),
        ("What is the allowance\ud800 abroad?", RewriteDefect.INVALID_CHARACTER),
        ("What is the allowance\x00 abroad?", RewriteDefect.INVALID_CHARACTER),
        ("What is the allowance\x1b abroad?", RewriteDefect.INVALID_CHARACTER),
        ("What is the \u202eallowance abroad?", RewriteDefect.INVALID_CHARACTER),
        ("What is the allow\u200bance abroad?", RewriteDefect.INVALID_CHARACTER),
        ("What is the daily allowance -abroad?", RewriteDefect.SEARCH_OPERATOR),
        ("What is the daily allowance or abroad?", RewriteDefect.SEARCH_OPERATOR),
        ('What is the "daily allowance" abroad?', RewriteDefect.SEARCH_OPERATOR),
        ("What is the daily allowance - - - abroad?", RewriteDefect.SEARCH_OPERATOR),
        ("What is the allowance (-abroad) rule?", RewriteDefect.SEARCH_OPERATOR),
        ("What is the allowance,-abroad rule?", RewriteDefect.SEARCH_OPERATOR),
        ("What is the allowance (or) abroad?", RewriteDefect.SEARCH_OPERATOR),
        ("What is the allowance;OR;abroad?", RewriteDefect.SEARCH_OPERATOR),
        ("What is the allowance " + "(-" * 40, RewriteDefect.SEARCH_OPERATOR),
        ("What is the daily ɑllowance abroad?", RewriteDefect.CHANGED_LANGUAGE),
        ("What is the daily ᴀllowance abroad?", RewriteDefect.CHANGED_LANGUAGE),
        ("What is the daily Ⓐllowance abroad?", RewriteDefect.INVALID_CHARACTER),
        ("What is the daily a\u0363llowance abroad?", RewriteDefect.INVALID_CHARACTER),
        ("What is error 2041 abroad?", RewriteDefect.INVENTED_IDENTIFIER),
        ("What are добові abroad?", RewriteDefect.CHANGED_LANGUAGE),
        ("What is the daily allowance αbroad?", RewriteDefect.CHANGED_LANGUAGE),
        ("What is the daily allowance ａｂｒｏａｄ?", RewriteDefect.CHANGED_LANGUAGE),
        ("What is the daily allowance 𝐚𝐛𝐫𝐨𝐚𝐝?", RewriteDefect.CHANGED_LANGUAGE),
        ("每日津贴 abroad?", RewriteDefect.CHANGED_LANGUAGE),
    ],
)
async def test_a_rewrite_that_breaks_a_rule_falls_back_and_keeps_its_cost(
    text: object, defect: RewriteDefect
) -> None:
    result = await rewrite(answering(text), *FOLLOW_UP)

    assert result.outcome is RewriteOutcome.FALLBACK
    assert result.fallback == "InvalidRewrite"
    assert result.search_text == FOLLOW_UP[0]
    assert result.usage is not None
    plan = classify_question(FOLLOW_UP[0], language=EN, history=FOLLOW_UP[2])
    with pytest.raises(InvalidRewrite) as caught:
        checked_rewrite(text, plan, question=FOLLOW_UP[0], history=FOLLOW_UP[2])
    assert caught.value.defect is defect


@pytest.mark.parametrize(
    "rewritten",
    [
        "Як підключитися до віртуальної мережі з дому?",
        "Як підключитися до VPNклієнта з дому?",
        "Як підключитися до vpn з дому?",
    ],
    ids=["dropped", "glued", "lowercased"],
)
async def test_a_word_to_keep_must_stay_a_whole_word_as_typed(rewritten: str) -> None:
    result = await rewrite(answering(rewritten), "Як підключитися до VPN з дому?", UK)

    assert result.fallback == "InvalidRewrite"
    plan = classify_question("Як підключитися до VPN з дому?", language=UK)
    with pytest.raises(InvalidRewrite) as caught:
        checked_rewrite(rewritten, plan, question="Як підключитися до VPN з дому?")
    assert caught.value.defect is RewriteDefect.DROPPED_WORD


async def test_a_rewrite_may_carry_typed_words_over_but_not_change_them() -> None:
    question, history = "And abroad?", ("What does error EXP-2041 mean?",)
    kept = "What does error EXP-2041 mean abroad?"
    changed = "What does error EXP-2014 mean abroad?"

    carried = await rewrite(answering(kept), question, EN, history)
    invented = await rewrite(answering(changed), question, EN, history)

    assert (carried.outcome, carried.search_text) == (RewriteOutcome.REWRITTEN, kept)
    assert invented.fallback == "InvalidRewrite"
    plan = classify_question(question, language=EN, history=history)
    with pytest.raises(InvalidRewrite) as caught:
        checked_rewrite(changed, plan, question=question, history=history)
    assert caught.value.defect is RewriteDefect.INVENTED_IDENTIFIER


async def test_ordinary_hyphens_and_words_like_for_pass_the_operator_check() -> None:
    text = "What is the day-to-day allowance for business trips abroad?"

    result = await rewrite(answering(text), *FOLLOW_UP)

    assert result.outcome is RewriteOutcome.REWRITTEN


async def test_a_ukrainian_rewrite_in_russian_letters_is_refused() -> None:
    question, history = "А за кордоном?", ("Які добові у відрядженні по Україні?",)
    plan = classify_question(question, language=UK, history=history)

    with pytest.raises(InvalidRewrite) as caught:
        checked_rewrite(
            "Какие суточные за границей?", plan, question=question, history=history
        )

    assert caught.value.defect is RewriteDefect.CHANGED_LANGUAGE


async def test_a_ukrainian_rewrite_keeps_its_apostrophe_and_typed_latin_words() -> None:
    question, history = "А за кордоном?", ("Скільки добових у Outlook-календарі?",)
    text = "Скільки добових за кордоном за п\u02bcять днів у Outlook-календарі?"

    result = await rewrite(answering(text), question, UK, history)

    assert result.outcome is RewriteOutcome.REWRITTEN


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (RewriterTimeout(), "RewriterTimeout"),
        (RewriterUnavailable(), "RewriterUnavailable"),
        (RewriterRateLimited(), "RewriterRateLimited"),
        (RewriterRequestRefused(MARKER), "RewriterRequestRefused"),
        (RewriterMalformedResponse(MARKER), "RewriterMalformedResponse"),
        (UnknownFailure(MARKER), "RewriterError"),
    ],
)
async def test_a_failing_model_falls_back_to_the_question_as_typed(
    error: RewriterError, reason: str
) -> None:
    result = await rewrite(FakeRewriteModel(error=error), *FOLLOW_UP)

    assert (result.outcome, result.fallback) == (RewriteOutcome.FALLBACK, reason)
    assert result.search_text == FOLLOW_UP[0]
    assert result.usage is None


async def test_an_error_that_is_not_a_port_error_is_a_bug_and_propagates() -> None:
    with pytest.raises(RuntimeError):
        await rewrite(FakeRewriteModel(error=RuntimeError(MARKER)), *FOLLOW_UP)


async def test_a_timeout_the_model_raises_itself_propagates() -> None:
    with pytest.raises(TimeoutError):
        await rewrite(FakeRewriteModel(error=TimeoutError()), *FOLLOW_UP)


async def test_a_slow_model_is_cut_at_the_deadline() -> None:
    model = FakeRewriteModel(delay_seconds=5.0)

    result = await rewrite(model, *FOLLOW_UP, timeout_seconds=0.05)

    assert result.fallback == "RewriterTimeout"
    assert result.usage is None


async def test_a_model_that_blocks_past_the_deadline_is_billed_but_not_used() -> None:
    model = FakeRewriteModel(block_seconds=0.1)

    result = await rewrite(model, *FOLLOW_UP, timeout_seconds=0.05)

    assert result.fallback == "RewriterTimeout"
    assert result.search_text == FOLLOW_UP[0]
    assert result.usage is not None


async def test_an_answer_that_is_not_a_rewrite_answer_is_malformed() -> None:
    class Wrong(FakeRewriteModel):
        async def rewrite(self, *args: object, **kwargs: object) -> RewriteAnswer:
            return "What is it?"  # pyright: ignore[reportReturnType]

    result = await rewrite(Wrong(), *FOLLOW_UP)

    assert result.fallback == "RewriterMalformedResponse"


@pytest.mark.parametrize("timeout_seconds", [0, -1, float("nan"), float("inf"), True])
async def test_a_bad_timeout_is_refused_before_sending(timeout_seconds: float) -> None:
    model = FakeRewriteModel()

    with pytest.raises(InvalidRewriteRequest):
        await rewrite(model, *FOLLOW_UP, timeout_seconds=timeout_seconds)

    assert model.calls == []


async def test_a_bad_policy_or_question_is_refused_before_sending() -> None:
    model = FakeRewriteModel()

    with pytest.raises(InvalidRewriteRequest):
        await rewrite(model, *FOLLOW_UP, policy="follow_up")  # pyright: ignore[reportArgumentType]
    with pytest.raises(InvalidQuestion):
        await rewrite(model, "   ", EN)

    assert model.calls == []


@pytest.mark.parametrize(
    "classes",
    [
        frozenset({RewriteClass.EXACT_IDENTIFIER}),
        frozenset({RewriteClass.SELF_CONTAINED}),
        {RewriteClass.FOLLOW_UP},
        frozenset({"follow_up"}),
    ],
    ids=["identifier", "self-contained", "a-set", "a-string"],
)
def test_a_policy_switches_on_only_rewritable_classes(classes: object) -> None:
    with pytest.raises(InvalidRewriteRequest):
        RewritePolicy(classes)  # pyright: ignore[reportArgumentType]


async def test_a_fallback_is_logged_with_names_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = answering(f"What is {WORD} д?")

    await rewrite(model, f"And {WORD}?", EN, ("What is it?",))

    (record,) = [r for r in caplog.records if r.message == "query rewrite fell back"]
    fields = record.__dict__["rewrite"]
    assert (fields["rewrite_class"], fields["reason"]) == (
        "follow_up",
        "InvalidRewrite",
    )
    assert WORD not in caplog.text
    assert WORD not in repr(record.__dict__)


@given(text=st.text(max_size=300))
async def test_the_search_text_is_the_question_or_a_rewrite_that_keeps_its_words(
    text: str,
) -> None:
    question = "Як підключитися до VPN з дому?"
    result = await rewrite(answering(text), question, UK)

    if result.outcome is RewriteOutcome.FALLBACK:
        assert result.search_text == question
    else:
        assert "VPN" in question_words(result.search_text)
        assert len(result.search_text) <= MAX_QUERY_CHARACTERS


def test_checked_rewrite_reads_only_a_plan() -> None:
    plan = RewritePlan(
        rewrite_class=RewriteClass.FOLLOW_UP, language=Language.MIXED, preserve=()
    )

    # Without a stated en or uk, any alphabet is the Question's own.
    assert checked_rewrite("Що таке VPN?", plan, question="VPN?") == "Що таке VPN?"
