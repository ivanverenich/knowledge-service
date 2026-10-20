"""Detect whether a document's text is English, Ukrainian, mixed, or unknown.

Detection counts letters by script in the document's blocks; code blocks are
skipped, because code reads as English inside a Ukrainian document. Latin
script counts as English. Cyrillic counts as Ukrainian only with positive
evidence: the letters only Ukrainian uses (`ґ`, `є`, `ї`) must appear, and must
outnumber Cyrillic letters from outside the Ukrainian alphabet (such as Russian
`ы` or Serbian `љ`). Without that evidence, as in Russian, Belarusian, Kazakh,
or a short Ukrainian phrase, the result is `unknown`. Too few letters, or too
many from another script, also give `unknown`.

The result is descriptive metadata for retrieval and evaluation. It is never
an authorization decision: who may read a document comes only from the
Source's access rules, whatever language the text is in.
"""

from dataclasses import dataclass
from enum import StrEnum

from knowledge_service.structure import BlockKind, ParsedDocument

MIN_LETTERS = 20
CONFIDENT_LETTERS = 200
DOMINANT_PERCENT = 80

_UKRAINIAN_ALPHABET = frozenset(
    "абвгґдеєжзиіїйклмнопрстуфхцчшщьюяАБВГҐДЕЄЖЗИІЇЙКЛМНОПРСТУФХЦЧШЩЬЮЯ"
)
# The dotted i (U+0456) is left out: Belarusian and Kazakh use it too.
_UKRAINIAN_ONLY = frozenset("ґєїҐЄЇ")
# The Ukrainian apostrophe counts as a letter in Unicode but is punctuation here.
_MODIFIER_APOSTROPHE = "\u02bc"


class InvalidLanguageProfile(ValueError):
    """A language profile violates an invariant."""


class Language(StrEnum):
    """The language recorded for a document."""

    ENGLISH = "en"
    UKRAINIAN = "uk"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LanguageProfile:
    """The detected language and how strongly the text supports it.

    `confidence` is the share of letters that support the label, scaled down
    when the text has fewer than `CONFIDENT_LETTERS` letters. `unknown` always
    has a confidence of 0.
    """

    language: Language
    confidence: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise InvalidLanguageProfile("confidence must be between 0 and 1")
        if (self.language is Language.UNKNOWN) != (self.confidence == 0):
            raise InvalidLanguageProfile("exactly an unknown has a confidence of 0")


_UNKNOWN = LanguageProfile(Language.UNKNOWN, 0.0)


def detect_language(parsed: ParsedDocument) -> LanguageProfile:
    """Return the language of the document's non-code blocks."""
    latin = cyrillic = other = ukrainian_only = not_ukrainian = 0
    for block in parsed.blocks:
        if block.kind is BlockKind.CODE:
            continue
        for character in block.text:
            if not character.isalpha() or character == _MODIFIER_APOSTROPHE:
                continue
            code = ord(character)
            if code < 0x250:
                latin += 1
            elif 0x400 <= code < 0x530:
                cyrillic += 1
                ukrainian_only += character in _UKRAINIAN_ONLY
                not_ukrainian += character not in _UKRAINIAN_ALPHABET
            else:
                other += 1
    total = latin + cyrillic + other
    # Shares are compared in whole numbers, so a boundary such as exactly 20%
    # is not tipped by floating-point rounding.
    if total < MIN_LETTERS or 100 * other > (100 - DOMINANT_PERCENT) * total:
        return _UNKNOWN
    sample = min(1.0, total / CONFIDENT_LETTERS)
    if 100 * latin >= DOMINANT_PERCENT * total:
        return LanguageProfile(Language.ENGLISH, round(latin / total * sample, 2))
    if 100 * cyrillic >= DOMINANT_PERCENT * total:
        if ukrainian_only == 0 or not_ukrainian >= ukrainian_only:
            return _UNKNOWN
        return LanguageProfile(Language.UKRAINIAN, round(cyrillic / total * sample, 2))
    return LanguageProfile(
        Language.MIXED, round((latin + cyrillic) / total * sample, 2)
    )
