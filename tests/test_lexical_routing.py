# Cyrillic and other scripts are the subject of these tests.
# ruff: noqa: RUF001
"""Checks on choosing the stored search words for a lexical Question."""

import itertools
from typing import cast

import pytest

from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_routing import (
    InvalidLexicalRoute,
    LexicalConfiguration,
    LexicalRoute,
    QuestionScript,
    question_script,
    route_lexical_question,
)

ENGLISH = (LexicalConfiguration.ENGLISH,)
SIMPLE = (LexicalConfiguration.SIMPLE,)
BOTH = (LexicalConfiguration.ENGLISH, LexicalConfiguration.SIMPLE)
MARKER = "zebramarker"


@pytest.mark.parametrize(
    ("text", "script", "configurations"),
    [
        ("requesting parental leaves", QuestionScript.LATIN, ENGLISH),
        ("оплачувана відпустка", QuestionScript.CYRILLIC, SIMPLE),
        ("ОПЛАЧУВАНА ВІДПУСТКА", QuestionScript.CYRILLIC, SIMPLE),
        # The alphabet is the evidence, not the language: Russian is Cyrillic.
        ("оплачиваемый отпуск", QuestionScript.CYRILLIC, SIMPLE),
        ("розгортання deploying", QuestionScript.MIXED, BOTH),
        ("відпустка в Kubernetes", QuestionScript.MIXED, BOTH),
        # Identifiers are not evidence of a language.
        ("E-1042", QuestionScript.NONE, BOTH),
        ("api_v2 v1.2.3 OAuth2", QuestionScript.NONE, BOTH),
        ("помилка E-1042", QuestionScript.CYRILLIC, SIMPLE),
        ("error E-1042 in api_v2", QuestionScript.LATIN, ENGLISH),
        # An underscore alone does not make an identifier: these words can
        # still be stemmed by English.
        ("leave_policies", QuestionScript.LATIN, ENGLISH),
        ("leave_policies оновлено", QuestionScript.MIXED, BOTH),
        # The Ukrainian apostrophe and a stress mark are not other letters.
        ("сімʼї", QuestionScript.CYRILLIC, SIMPLE),
        ("за\u0301мок", QuestionScript.CYRILLIC, SIMPLE),
        ("休暇", QuestionScript.NONE, BOTH),
        ('"" -- ?!', QuestionScript.NONE, BOTH),
    ],
)
def test_the_question_alphabet_chooses_the_configurations(
    text: str,
    script: QuestionScript,
    configurations: tuple[LexicalConfiguration, ...],
) -> None:
    route = route_lexical_question(text, stated=None)

    assert question_script(text) is script
    assert route == LexicalRoute(
        configurations=configurations, stated=None, script=script
    )


@pytest.mark.parametrize(
    ("text", "stated", "configurations"),
    [
        ("requesting parental leaves", Language.ENGLISH, ENGLISH),
        ("оплачувана відпустка", Language.UKRAINIAN, SIMPLE),
        # A stated language that the Question's alphabet contradicts adds
        # its configuration; it never removes the other one.
        ("requesting parental leaves", Language.UKRAINIAN, BOTH),
        ("оплачувана відпустка", Language.ENGLISH, BOTH),
        ("requesting parental leaves", Language.MIXED, BOTH),
        ("E-1042", Language.ENGLISH, BOTH),
        # Unknown states nothing, so the alphabet alone decides.
        ("requesting parental leaves", Language.UNKNOWN, ENGLISH),
        ("оплачувана відпустка", Language.UNKNOWN, SIMPLE),
    ],
)
def test_a_stated_language_adds_its_configuration(
    text: str,
    stated: Language,
    configurations: tuple[LexicalConfiguration, ...],
) -> None:
    route = route_lexical_question(text, stated=stated)

    assert route.configurations == configurations
    assert route.stated is stated


@pytest.mark.parametrize("stated", [None, Language.ENGLISH, Language.UKRAINIAN])
def test_a_question_english_keeps_no_word_of_adds_simple(
    stated: Language | None,
) -> None:
    route = route_lexical_question("the who", stated=stated, english_words=False)

    assert LexicalConfiguration.SIMPLE in route.configurations
    assert route.english_words is False


@pytest.mark.parametrize("stated", ["en", "fr", 1, True])
def test_a_stated_language_must_be_a_language(stated: object) -> None:
    with pytest.raises(InvalidLexicalRoute):
        route_lexical_question("leave", stated=cast(Language, stated))


@pytest.mark.parametrize("text", [None, 5, True, b"leave", ["leave"]])
def test_question_text_must_be_a_string(text: object) -> None:
    with pytest.raises(InvalidLexicalRoute):
        route_lexical_question(cast(str, text), stated=None)


@pytest.mark.parametrize("english_words", [None, 0, "no"])
def test_english_words_must_be_a_bool(english_words: object) -> None:
    with pytest.raises(InvalidLexicalRoute):
        route_lexical_question(
            "leave", stated=None, english_words=cast(bool, english_words)
        )


def test_any_text_gets_a_route_with_at_least_one_configuration() -> None:
    words = ["leave", "ВІДПУСТКА", "E-1042", "сімʼї", "休暇", "--", "\u200b", ""]
    for parts in itertools.product(words, repeat=3):
        for stated in [None, *Language]:
            route = route_lexical_question(" ".join(parts), stated=stated)

            assert route.configurations
            assert set(route.configurations) <= set(LexicalConfiguration)


def test_the_route_holds_no_question_text() -> None:
    route = route_lexical_question(f"{MARKER} відпустка", stated=None)

    assert MARKER not in repr(route)


@pytest.mark.parametrize(
    "configurations",
    [
        (),
        (LexicalConfiguration.SIMPLE, LexicalConfiguration.ENGLISH),
        (LexicalConfiguration.ENGLISH, LexicalConfiguration.ENGLISH),
    ],
)
def test_a_route_needs_unique_ordered_configurations(
    configurations: tuple[LexicalConfiguration, ...],
) -> None:
    with pytest.raises(InvalidLexicalRoute):
        LexicalRoute(
            configurations=configurations, stated=None, script=QuestionScript.NONE
        )
