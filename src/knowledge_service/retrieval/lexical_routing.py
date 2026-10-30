"""Choose which stored search words a lexical Question is matched against.

Every Chunk has English words (stemmed, without stop words) and `simple`
words (only lowercased). The right choice depends on the Question, and two
signals describe it: the language the caller states, if any, and the
alphabet of the Question's own words. Neither is trusted alone:

- A caller's language comes from a setting or a form, not from this
  Question, so a Ukrainian reader may still type "parental leave".
- The alphabet is a guess from a few words. It cannot tell Ukrainian from
  Russian, and a short Question gives little to go on.

So each signal adds the configuration it supports, and the Question is
matched against one configuration only when every signal agrees on it.
Otherwise it is matched against both, which costs a second index lookup but
never drops a Chunk that either configuration would find.

A third signal comes from the database: whether the English configuration
keeps any word of the Question. It drops English stop words, so "IT" or
"the who" reads as an empty query that matches nothing; `simple` keeps
them, so it is added.

What the signals give:

| Signal | English | `simple` |
|---|---|---|
| Stated `en` | yes | |
| Stated `uk` | | yes |
| Stated `mixed` | yes | yes |
| Stated `unknown`, or nothing | | |
| Words only in Latin letters | yes | |
| Words only in Cyrillic letters | | yes |
| Both alphabets, or neither | yes | yes |
| English keeps no word | | yes |

Identifiers such as `E-1042`, `api_v2`, or `v1.2.3` are not evidence of a
language: a word with a digit is skipped. Both configurations split and
lowercase `e`, `1042`, `v2`, or `v1.2.3` the same way, so they match
whichever configuration is chosen. A word joined by an underscore without a
digit, such as `leave_policy`, still counts: PostgreSQL splits it into
words that English stemming may change.

The route is descriptive and holds no Question text, so it is safe to log.
It never decides who may read a Chunk.
"""

import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from knowledge_service.language import Language


class LexicalConfiguration(StrEnum):
    """A PostgreSQL text-search configuration with stored Chunk words."""

    ENGLISH = "english"
    SIMPLE = "simple"


class QuestionScript(StrEnum):
    """The alphabets the Question's words are written in, identifiers aside."""

    LATIN = "latin"
    CYRILLIC = "cyrillic"
    MIXED = "mixed"
    NONE = "none"


class InvalidLexicalRoute(ValueError):
    """A lexical route violates an invariant."""


_BOTH = (LexicalConfiguration.ENGLISH, LexicalConfiguration.SIMPLE)

_STATED = {
    Language.ENGLISH: (LexicalConfiguration.ENGLISH,),
    Language.UKRAINIAN: (LexicalConfiguration.SIMPLE,),
    Language.MIXED: _BOTH,
    Language.UNKNOWN: (),
}

_DETECTED = {
    QuestionScript.LATIN: (LexicalConfiguration.ENGLISH,),
    QuestionScript.CYRILLIC: (LexicalConfiguration.SIMPLE,),
    QuestionScript.MIXED: _BOTH,
    QuestionScript.NONE: _BOTH,
}


@dataclass(frozen=True, slots=True)
class LexicalRoute:
    """The configurations a Question is matched against, and why.

    `configurations` is never empty, has no repeats, and keeps the order
    of `LexicalConfiguration`.
    """

    configurations: tuple[LexicalConfiguration, ...]
    stated: Language | None
    script: QuestionScript
    english_words: bool = True

    def __post_init__(self) -> None:
        if not self.configurations:
            raise InvalidLexicalRoute("a route needs at least one configuration")
        if self.configurations != tuple(
            configuration
            for configuration in LexicalConfiguration
            if configuration in self.configurations
        ):
            raise InvalidLexicalRoute("configurations must be unique and in order")


def route_lexical_question(
    text: str, *, stated: Language | None, english_words: bool = True
) -> LexicalRoute:
    """Return the configurations to match `text` against.

    `stated` is the language the caller gives for this Question, or None.
    `english_words` is False when the English configuration keeps no word
    of `text`, because every word is an English stop word.
    """
    if type(text) is not str:
        raise InvalidLexicalRoute("text must be a string")
    if stated is not None and type(stated) is not Language:
        raise InvalidLexicalRoute("stated must be a Language or None")
    if type(english_words) is not bool:
        raise InvalidLexicalRoute("english_words must be a bool")
    script = question_script(text)
    wanted = set(_DETECTED[script])
    if stated is not None:
        wanted.update(_STATED[stated])
    if not english_words:
        wanted.add(LexicalConfiguration.SIMPLE)
    return LexicalRoute(
        configurations=tuple(
            configuration
            for configuration in LexicalConfiguration
            if configuration in wanted
        ),
        stated=stated,
        script=script,
        english_words=english_words,
    )


def question_script(text: str) -> QuestionScript:
    """Return which alphabets the letters of `text` use, identifiers aside."""
    latin = cyrillic = False
    for word in text.split():
        if any(character.isdigit() for character in word):
            continue
        for character in word:
            name = unicodedata.name(character, "")
            latin = latin or name.startswith("LATIN ")
            cyrillic = cyrillic or name.startswith("CYRILLIC ")
    if latin and cyrillic:
        return QuestionScript.MIXED
    if latin:
        return QuestionScript.LATIN
    if cyrillic:
        return QuestionScript.CYRILLIC
    return QuestionScript.NONE
