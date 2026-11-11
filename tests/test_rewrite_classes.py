"""Rewrite classes: one class per Question, and the words a rewrite keeps."""
# ruff: noqa: RUF001

import pytest
from hypothesis import given
from hypothesis import strategies as st

from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.rewrite_classes import (
    MAX_HISTORY,
    InvalidQuestion,
    RewriteClass,
    classify_question,
)

EN, UK = Language.ENGLISH, Language.UKRAINIAN
MARKER = "PLANTED-MARKER-4417"


class Sneaky(str):
    """A str subclass that runs its own code when stripped."""

    __slots__ = ()

    def strip(self, chars: str | None = None) -> str:
        raise RuntimeError(self)


@pytest.mark.parametrize(
    ("question", "language", "history", "expected", "preserve"),
    [
        ("What does error EXP-2041 mean?", EN, (), "exact_identifier", ("EXP-2041",)),
        ("Як заповнити форму ІТ-0457?", UK, (), "exact_identifier", ("ІТ-0457",)),
        ("And abroad?", EN, ("What is the daily allowance?",), "follow_up", ()),
        ("Скільки днів його оплачують?", UK, ("Хто платить?",), "follow_up", ()),
        ("Where do I reset my SSO password?", EN, (), "acronym", ("SSO",)),
        ("Як підключитися до VPN з дому?", UK, (), "acronym", ("VPN",)),
        ("Як налаштувати пошту в Outlook?", UK, (), "cross_language", ("Outlook",)),
        ("Who approves a лікарняний?", EN, (), "cross_language", ("лікарняний",)),
        ("Who pays for sick leave?", EN, (), "self_contained", ()),
        ("Хто оплачує лікарняні?", UK, (), "self_contained", ()),
    ],
)
def test_each_question_gets_one_class_and_the_words_to_keep(
    question: str,
    language: Language,
    history: tuple[str, ...],
    expected: str,
    preserve: tuple[str, ...],
) -> None:
    plan = classify_question(question, language=language, history=history)

    assert plan.rewrite_class is RewriteClass(expected)
    assert plan.preserve == preserve
    assert plan.language is language
    assert plan.may_rewrite is (expected in {"follow_up", "acronym", "cross_language"})


def test_the_first_rule_that_holds_decides() -> None:
    # A follow-up that names an identifier is still never rewritten.
    plan = classify_question(
        "How do I fix it for EXP-2041?",
        language=EN,
        history=("What does error EXP-2041 mean?",),
    )
    assert (plan.rewrite_class, plan.may_rewrite) == ("exact_identifier", False)
    # An acronym in a Ukrainian Question is also Latin; it counts once.
    plan = classify_question("Що таке VPN?", language=UK)
    assert (plan.rewrite_class, plan.preserve) == ("acronym", ("VPN",))


@pytest.mark.parametrize(
    "identifier",
    [
        "EXP_2041",
        "EXP\u20132041",
        "EXP\u22122041",
        "EXP--2041",
        "EXP/2041",
        "EXP:2041",
        "EX\u200dP-2041",
        "2041",
        "10.0.0.1",
        "#2041",
        "EXP-\u216b",
    ],
)
def test_no_separator_cuts_an_identifier_into_rewritable_pieces(
    identifier: str,
) -> None:
    plan = classify_question(
        f"Same for {identifier}?", language=EN, history=("How do I reset it?",)
    )

    assert plan.rewrite_class is RewriteClass.EXACT_IDENTIFIER
    assert not plan.may_rewrite
    # Kept whole, as typed; only punctuation such as `#` or `?` is trimmed.
    assert plan.preserve == (identifier.lstrip("#"),)


@pytest.mark.parametrize(
    ("question", "language", "identifier"),
    [
        ("And ERR_TIMEOUT?", EN, "ERR_TIMEOUT"),
        ("What about hr@example.com?", EN, "hr@example.com"),
        ("Where is /etc/hosts?", EN, "/etc/hosts"),
        ("А що з /etc/hosts?", UK, "/etc/hosts"),
        ("And .env?", EN, ".env"),
        ("А .env?", UK, ".env"),
        ("What about /var/log?", EN, "/var/log"),
        ("And @oncall?", EN, "@oncall"),
        ("And __init__?", EN, "__init__"),
        ("Is C:\\Temp safe?", EN, "C:\\Temp"),
        ("What is app.config for?", EN, "app.config"),
    ],
)
def test_a_code_address_or_path_without_digits_is_an_identifier(
    question: str, language: Language, identifier: str
) -> None:
    plan = classify_question(
        question, language=language, history=("What does error EXP-2041 mean?",)
    )

    assert plan.rewrite_class is RewriteClass.EXACT_IDENTIFIER
    assert not plan.may_rewrite
    assert plan.preserve == (identifier,)


def test_a_hyphen_alone_does_not_make_an_identifier() -> None:
    plan = classify_question("How do I set up Wi-Fi at home?", language=EN)

    assert (plan.rewrite_class, plan.preserve) == ("self_contained", ())


@pytest.mark.parametrize("ending", [".", "...", ":", "!?"])
def test_punctuation_that_ends_a_sentence_is_not_an_identifier(ending: str) -> None:
    plan = classify_question(f"Who pays for sick leave{ending}", language=EN)

    assert (plan.rewrite_class, plan.preserve) == ("self_contained", ())


def test_a_follow_up_needs_an_earlier_question() -> None:
    plan = classify_question("And abroad?", language=EN)

    assert plan.rewrite_class is RewriteClass.SELF_CONTAINED


def test_a_question_in_capitals_has_no_acronyms() -> None:
    plan = classify_question("HOW DO I CONNECT TO THE VPN?", language=EN)

    assert (plan.rewrite_class, plan.preserve) == ("self_contained", ())


@pytest.mark.parametrize("language", [Language.MIXED, Language.UNKNOWN])
def test_without_a_stated_language_no_word_is_foreign(language: Language) -> None:
    plan = classify_question("Як налаштувати пошту в Outlook?", language=language)

    assert plan.rewrite_class is RewriteClass.SELF_CONTAINED


_LETTERS = st.text(
    alphabet=st.sampled_from("ABCXYZabcxyzАБВІТЮабвіїя"), min_size=1, max_size=4
)


def _identifier(letters: str, digits: str, joiner: str) -> str:
    return f"{letters}{joiner}{digits}"


_IDENTIFIERS = st.builds(
    _identifier,
    _LETTERS,
    st.integers(min_value=0, max_value=99_999).map(str),
    st.sampled_from(["-", ".", ""]),
)
_HISTORIES = st.lists(st.just("What does it mean?"), max_size=3)


@given(
    identifier=_IDENTIFIERS,
    before=st.sampled_from(["", "What is ", "Що таке ", "fix it for "]),
    language=st.sampled_from([EN, UK]),
    history=_HISTORIES,
)
def test_a_question_with_an_identifier_is_never_rewritten(
    identifier: str, before: str, language: Language, history: list[str]
) -> None:
    question = f"{before}{identifier}?"

    plan = classify_question(question, language=language, history=history)

    assert plan.rewrite_class is RewriteClass.EXACT_IDENTIFIER
    assert not plan.may_rewrite
    assert identifier in plan.preserve


@given(
    question=st.text(min_size=1, max_size=200).filter(str.strip),
    language=st.sampled_from(list(Language)),
    history=_HISTORIES,
)
def test_every_word_to_keep_is_in_the_question_as_typed(
    question: str, language: Language, history: list[str]
) -> None:
    plan = classify_question(question, language=language, history=history)

    assert all(word and word in question for word in plan.preserve)
    assert len(set(plan.preserve)) == len(plan.preserve)


def test_a_plan_never_shows_the_words_of_the_question() -> None:
    plan = classify_question(f"What is {MARKER}?", language=EN)

    assert MARKER in plan.preserve
    assert MARKER not in repr(plan)


@pytest.mark.parametrize(
    ("question", "language", "history", "message"),
    [
        ("", EN, (), "the question"),
        ("   ", EN, (), "the question"),
        (MARKER + "x" * MAX_QUERY_CHARACTERS, EN, (), "at most"),
        (MARKER, "en", (), "Language"),
        (MARKER, EN, MARKER, "list or tuple"),
        (MARKER, EN, (MARKER,) * (MAX_HISTORY + 1), "at most"),
        (MARKER, EN, ("",), "an earlier question"),
        (MARKER + "\ud800", EN, (), "valid Unicode"),
        (Sneaky(MARKER), EN, (), "the question"),
        (MARKER, EN, (Sneaky(MARKER),), "an earlier question"),
        (MARKER, EN, (MARKER + "x" * MAX_QUERY_CHARACTERS,), "an earlier question"),
    ],
)
def test_a_question_that_cannot_be_classified_is_refused_without_quoting_it(
    question: str, language: Language, history: tuple[str, ...], message: str
) -> None:
    with pytest.raises(InvalidQuestion, match=message) as caught:
        classify_question(question, language=language, history=history)

    assert MARKER not in str(caught.value)
