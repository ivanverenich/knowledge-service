"""Which Questions a rewrite may help, and what any rewrite must keep.

A rewrite turns a Question into a better search text: it resolves "it" in
a follow-up, spells out an acronym, or adds a word in the other language.
It can also break a Question: an identifier such as `EXP-2041` changed by
one character finds nothing. So before anything is rewritten (M05-T15),
`classify_question` puts each Question in exactly one class by fixed
rules, and its `RewritePlan` says whether the Question may be rewritten
and which words any rewrite must keep exactly as typed.

A word is a run of characters between spaces, with punctuation trimmed
from both ends: in "What is EXP_2041?" the words are `What`, `is`, and
`EXP_2041`. Only spaces split a word, so no dash, underscore, slash, or
invisible joiner can cut an identifier into pieces. A joiner (see rule 1)
is not trimmed from the start of a word, and an underscore is not trimmed
from its end, so `.env`, `/tmp`, `@oncall`, and `__init__` stay whole.

The rules, checked in this order; the first that holds names the class:

1. `exact_identifier`: a word holds a digit or another number character,
   such as `EXP-2041`, `2041`, or `10.0.0.1`, or joins its parts with
   `_`, `@`, `/`, `.`, `:`, or a backslash, such as `ERR_TIMEOUT`,
   `hr@example.com`, or `etc/hosts`. Never rewritten.
2. `follow_up`: the Conversation has earlier Questions, and this one
   points back with a word such as "it" or "його", or has at most three
   words. May be rewritten.
3. `acronym`: a word of 2 to 6 capital letters, such as `VPN`, in a
   Question that also has lowercase letters. May be rewritten.
4. `cross_language`: a word with a letter from the other language's
   alphabet, such as `Outlook` in a Ukrainian Question. May be rewritten.
5. `self_contained`: none of the above. Searched as typed.

Words to keep: every identifier, acronym, and other-alphabet word, as
typed, in the order they first appear, whatever the class. The stated
language must be kept too. A rewrite that drops one of them, or changes
the language, must be refused.

The rules are deliberately simple, so a labelled case can show where they
are wrong (see `retrieval.rewrite_cases`). A plan holds words of the
Question, so its repr hides them and it must not be logged; errors never
quote the Question.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS

MAX_HISTORY = 10
MAX_ACRONYM_LETTERS = 6
MAX_FOLLOW_UP_WORDS = 3

# Characters that join the parts of a code, an address, or a path. A plain
# hyphen is left out: it also joins ordinary words, such as "Wi-Fi".
_JOINERS = frozenset("_@/\\.:")

# Words that point back to an earlier Question. Short Ukrainian words that
# also mean "and" or "so", such as "та" or "тому", are left out.
_POINTS_BACK = frozenset(
    {
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "they",
        "them",
        "their",
        "there",
        "same",
        "це",
        "цей",
        "ця",
        "ці",
        "цього",
        "цієї",
        "цих",
        "той",
        "ті",
        "того",
        "тієї",
        "тих",
        "він",
        "вона",
        "воно",
        "вони",
        "його",
        "її",
        "їх",
        "їм",
        "там",
        "такий",
        "така",
        "таке",
        "такі",
    }
)


class RewriteClass(StrEnum):
    """Why a Question might, or must not, be rewritten."""

    EXACT_IDENTIFIER = "exact_identifier"
    FOLLOW_UP = "follow_up"
    ACRONYM = "acronym"
    CROSS_LANGUAGE = "cross_language"
    SELF_CONTAINED = "self_contained"


# The classes a rewrite may help; the others are searched as typed.
REWRITABLE = frozenset(
    {RewriteClass.FOLLOW_UP, RewriteClass.ACRONYM, RewriteClass.CROSS_LANGUAGE}
)


class InvalidQuestion(ValueError):
    """The Question, its language, or its history cannot be classified."""


@dataclass(frozen=True, slots=True)
class RewritePlan:
    """A Question's class, its stated language, and the words to keep.

    `preserve` holds words of the Question, so the repr hides them.
    """

    rewrite_class: RewriteClass
    language: Language
    preserve: tuple[str, ...] = field(repr=False)

    @property
    def may_rewrite(self) -> bool:
        """True for a follow-up, an acronym, or a cross-language Question."""
        return self.rewrite_class in REWRITABLE


def classify_question(
    question: str, *, language: Language, history: Sequence[str] = ()
) -> RewritePlan:
    """Return the Question's class and what any rewrite must keep.

    `language` is the language the caller states; `history` holds the
    Conversation's earlier Questions, oldest first. Raises
    `InvalidQuestion` when the Question or any earlier Question is not
    plain `str`, is empty or too long, or is not valid Unicode; when the
    language is not a `Language`; or when the history is not a list or
    tuple, or holds more than `MAX_HISTORY` Questions. The message never
    quotes a Question.
    """
    _check_text(question, "the question")
    if type(language) is not Language:
        raise InvalidQuestion("language must be a Language")
    if not isinstance(history, list | tuple):
        raise InvalidQuestion("history must be a list or tuple of questions")
    if len(history) > MAX_HISTORY:
        raise InvalidQuestion(f"history must hold at most {MAX_HISTORY} questions")
    for earlier in history:
        _check_text(earlier, "an earlier question")
    words = question_words(question)
    has_lowercase = any(character.islower() for character in question)
    identifiers = [word for word in words if _is_identifier(word)]
    acronyms = [word for word in words if has_lowercase and _is_acronym(word)]
    foreign = [
        word
        for word in words
        if not _is_identifier(word) and _other_alphabet(word, language)
    ]
    if identifiers:
        rewrite_class = RewriteClass.EXACT_IDENTIFIER
    elif history and _points_back(words):
        rewrite_class = RewriteClass.FOLLOW_UP
    elif acronyms:
        rewrite_class = RewriteClass.ACRONYM
    elif foreign:
        rewrite_class = RewriteClass.CROSS_LANGUAGE
    else:
        rewrite_class = RewriteClass.SELF_CONTAINED
    # Kept in the order they first appear, each once.
    kept = {*identifiers, *acronyms, *foreign}
    preserve = tuple(dict.fromkeys(word for word in words if word in kept))
    return RewritePlan(
        rewrite_class=rewrite_class, language=language, preserve=preserve
    )


def question_words(text: str) -> tuple[str, ...]:
    """Split on spaces and trim punctuation from both ends of each word.

    A joiner stays at the start of a word, and an underscore at its end,
    so `.env` and `__init__` are kept whole. Other ending punctuation,
    such as the full stop in "leave.", is trimmed.
    """
    words: list[str] = []
    for chunk in text.split():
        start, end = 0, len(chunk)
        while (
            start < end
            and _is_punctuation(chunk[start])
            and chunk[start] not in _JOINERS
        ):
            start += 1
        while end > start and _is_punctuation(chunk[end - 1]) and chunk[end - 1] != "_":
            end -= 1
        if start < end:
            words.append(chunk[start:end])
    return tuple(words)


def _check_text(text: object, name: str) -> None:
    # Plain str only: a subclass could run its own code on every call.
    if type(text) is not str or not text.strip():
        raise InvalidQuestion(f"{name} must be non-empty text")
    if len(text) > MAX_QUERY_CHARACTERS:
        raise InvalidQuestion(
            f"{name} must be at most {MAX_QUERY_CHARACTERS} characters"
        )
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        # A lone surrogate cannot be sent anywhere later.
        raise InvalidQuestion(f"{name} must be valid Unicode text") from None


def _is_punctuation(character: str) -> bool:
    return unicodedata.category(character).startswith("P")


def _is_identifier(word: str) -> bool:
    return any(character.isnumeric() or character in _JOINERS for character in word)


def _is_acronym(word: str) -> bool:
    return 2 <= len(word) <= MAX_ACRONYM_LETTERS and word.isalpha() and word.isupper()


def _other_alphabet(word: str, language: Language) -> bool:
    other = {Language.ENGLISH: "CYRILLIC ", Language.UKRAINIAN: "LATIN "}.get(language)
    if other is None:
        return False
    return any(unicodedata.name(character, "").startswith(other) for character in word)


def _points_back(words: tuple[str, ...]) -> bool:
    if len(words) <= MAX_FOLLOW_UP_WORDS:
        return True
    return any(word.casefold() in _POINTS_BACK for word in words)
