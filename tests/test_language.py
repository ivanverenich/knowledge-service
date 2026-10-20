"""Tests for the language metadata policy."""

import pytest

from knowledge_service.language import (
    InvalidLanguageProfile,
    Language,
    LanguageProfile,
    detect_language,
)
from knowledge_service.markdown_parser import parse_markdown
from knowledge_service.text_parser import parse_text

ENGLISH = (
    "Every new engineer reads the security policy before the first deploy. "
    "The policy explains how secrets are stored, who approves production "
    "changes, and what to do when an alert fires during the night. Questions "
    "about access go to the platform team on the internal help channel."
)
UKRAINIAN = (
    "Кожен новий інженер читає політику безпеки перед першим розгортанням. "
    "Політика пояснює, де зберігаються секрети, хто погоджує зміни в "
    "продакшені та що робити, коли вночі спрацьовує сповіщення. Питання щодо "
    "доступу надсилайте команді платформи через внутрішній канал підтримки."
)
RUSSIAN = (
    "Каждый новый инженер читает политику безопасности перед первым "
    "развёртыванием. Политика объясняет, где хранятся секреты и кто "
    "согласовывает изменения в продакшене."
)


def profile(text: str) -> LanguageProfile:
    return detect_language(parse_text(text))


def test_english_text_is_english_with_full_confidence() -> None:
    assert profile(ENGLISH) == LanguageProfile(Language.ENGLISH, 1.0)


def test_ukrainian_text_is_ukrainian_with_full_confidence() -> None:
    assert profile(UKRAINIAN) == LanguageProfile(Language.UKRAINIAN, 1.0)


def test_a_bilingual_document_is_mixed() -> None:
    detected = profile(f"{ENGLISH}\n\n{UKRAINIAN}")

    assert detected.language is Language.MIXED
    assert detected.confidence == 1.0


def test_a_few_english_terms_keep_ukrainian_text_ukrainian() -> None:
    detected = profile(f"{UKRAINIAN} Деталі: API, Kubernetes, CI.")

    assert detected.language is Language.UKRAINIAN
    assert 0.9 < detected.confidence < 1.0


@pytest.mark.parametrize("text", ["Hello, team!", "Привіт, командо!", "OK", ""])
def test_short_text_falls_back_to_unknown(text: str) -> None:
    assert profile(text) == LanguageProfile(Language.UNKNOWN, 0.0)


def test_confidence_shrinks_for_text_that_is_short_but_long_enough() -> None:
    detected = profile("Заяву на відпустку погоджує керівник команди.")

    assert detected.language is Language.UKRAINIAN
    assert detected.confidence == 0.2


def test_a_short_ukrainian_phrase_without_distinctive_letters_is_unknown() -> None:
    # No `ґ`, `є`, or `ї`, so nothing tells it apart from other Cyrillic text.
    detected = profile("Відпустку погоджують заздалегідь.")

    assert detected == LanguageProfile(Language.UNKNOWN, 0.0)


def test_exactly_the_dominant_share_still_counts() -> None:
    # 16 Latin and 4 Greek letters: other scripts are exactly 20%, not more.
    detected = profile("abcdefghijklmnop αβγδ")

    assert detected == LanguageProfile(Language.ENGLISH, 0.08)


@pytest.mark.parametrize(
    "text",
    [
        RUSSIAN,
        "Привет, как дела? Хорошо, спасибо, до завтра, пока.",
        "Сваки нови инжењер чита безбедносну политику пре првог покретања.",
        "Кожны новы інжынер чытае палітыку бяспекі перад першым выпускам.",
        "Мінск — сталіца Беларусі. Горад мае багатую гісторыю і культуру.",  # noqa: RUF001
        "Сіз бізге хабарласа аласыз. Біз сізді күтеміз, сізге көмектесу.",
        "Добро утро, како сте? Благодарам, добро, се гледаме утре.",  # noqa: RUF001
    ],
    ids=[
        "russian",
        "russian-without-marker-letters",
        "serbian",
        "belarusian",
        "belarusian-with-many-i",
        "kazakh",
        "macedonian",
    ],
)
def test_other_cyrillic_languages_are_not_mistaken_for_ukrainian(text: str) -> None:
    assert profile(text) == LanguageProfile(Language.UNKNOWN, 0.0)


def test_text_in_another_script_is_unknown() -> None:
    assert profile("安全策略说明了如何存储密钥以及谁批准生产变更。" * 3).language is (
        Language.UNKNOWN
    )


def test_code_blocks_do_not_count() -> None:
    code = "```python\n" + "def deploy(service, version):\n    return None\n" * 20
    parsed = parse_markdown(f"{UKRAINIAN}\n\n{code}```\n")

    assert detect_language(parsed).language is Language.UKRAINIAN


@pytest.mark.parametrize(
    ("language", "confidence"),
    [
        (Language.ENGLISH, 1.5),
        (Language.ENGLISH, -0.1),
        (Language.ENGLISH, 0.0),
        (Language.UNKNOWN, 0.4),
    ],
    ids=["above-one", "negative", "known-with-zero", "unknown-with-confidence"],
)
def test_an_invalid_profile_is_refused(language: Language, confidence: float) -> None:
    with pytest.raises(InvalidLanguageProfile):
        LanguageProfile(language, confidence)
