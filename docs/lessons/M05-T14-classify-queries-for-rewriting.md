# M05-T14 — Classify queries for rewriting

Source task: [M05-T14](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T14-classify-queries-for-rewriting.md)

## What you will do

The next lesson, M05-T15, will let a language model rewrite some Questions before search: turn "And abroad?" into a full question, spell out an acronym, or add a word in the other language. A rewrite can also break a Question. Change one character of `EXP-2041` and search finds nothing. This lesson decides, before any rewriting exists, which Questions a rewrite may touch and which words it must leave exactly as typed. It also measures those rules against Questions a person has labelled, so the rules are judged by cases, not by feeling.

- `classify_question` puts each Question in exactly one of five classes by fixed rules, and returns a plan: may it be rewritten, and which words must survive.
- A labelled file, `rewrite-classes.json`, holds 33 verified Questions: the 16 seed questions and 17 new ones, including follow-ups with their earlier Questions.
- `load_rewrite_cases` checks that file strictly; `score_rewrite_cases` measures how often the rules agree with the labels, class by class.
- `scripts/score_rewrite_classes.py` prints that measurement and fails when any label is missed.

| The task asks for | Where it happens |
|---|---|
| Measurable classes: follow-ups, acronyms, cross-language terms | Step 2: five classes, each defined by one rule you can test |
| Exact identifiers excluded | Step 2: any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, makes the Question `exact_identifier`, never rewritten |
| Verified cases assign class labels and preservation constraints | Steps 4 and 5: every case has a class and the words to keep; the loader checks them |
| Deterministic tests: success, highest-risk edge, typed failure | Steps 3 and 6: each class; no separator can split an identifier; `InvalidQuestion` and `InvalidRewriteCases` |
| No seeded sensitive text in telemetry | Steps 3 and 6: a planted marker never appears in a repr, an error, or the report |
| Authorization before data leaves storage | Not touched: nothing here reads stored Documents or calls a model |

This lesson includes coding: Steps 2, 3, 5, and 6 write code. Steps 1, 4, 7, and 8 do not involve code; Step 4 writes the labelled data. Nothing calls OpenAI or a database except the existing integration suite in Step 8.

Terms used in this lesson:

- **Query rewriting:** changing the search text before retrieval, for example turning the follow-up "And abroad?" into "What is the daily allowance for business trips abroad?". The user's Question itself is not changed.
- **Follow-up:** a Question that only makes sense with an earlier one in the same Conversation, like "How long does it take?" after "How do I renew a certificate?".
- **Exact identifier:** a word that must match letter for letter, such as an error code, a form number, or an address: `EXP-2041`, `ІТ-0457`, `10.0.0.1`.
- **Preservation constraint:** a rule a rewrite must keep. Here: every listed word, exactly as typed, and the language of the Question.
- **Label:** the class and the words to keep that a person decided for a Question. The rules are measured against labels.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **Why classify before rewriting.** Rewriting costs a model call and can hurt. Most seed questions are already clear, and fused retrieval ranks 15 of 16 first. So only Questions with a reason to be rewritten should be: a follow-up, an acronym, or a word in the other language. Everything else is searched as typed.
- **Five classes, one per Question.** The rules are checked in a fixed order, and the first that holds names the class: `exact_identifier`, `follow_up`, `acronym`, `cross_language`, `self_contained`. One label per Question keeps the measurement simple: right or wrong.
- **Identifiers win.** A Question with any word that holds a digit is `exact_identifier` and never rewritten, even when it is also a follow-up. So is a word whose parts are joined by `_`, `@`, `/`, `.`, `:`, or a backslash, such as `ERR_TIMEOUT`, `hr@example.com`, or `/etc/hosts`. A plain hyphen does not count, because it also joins ordinary words such as "Wi-Fi". The rule is broad on purpose: it also stops "And error 2041?" and "10.0.0.1". A missed identifier costs the user the answer; a missed rewrite costs only a little recall.
- **What a word is.** Words are split on spaces only, then punctuation is trimmed from both ends. So `EXP_2041`, `EXP–2041` with an en dash, or `EXP/2041` stay one word. A joiner such as `.` or `/` is not trimmed from the start of a word, and `_` is not trimmed from its end, so `.env`, `/etc/hosts`, and `__init__` stay whole, while the full stop in "leave." is still trimmed. An earlier draft split on dashes and underscores, and an adversarial review turned `EXP_2041` into a rewritable acronym `EXP`.
- **Follow-ups need history.** A Question is a follow-up only when the Conversation has earlier Questions, and it points back ("it", "його", "там") or has at most three words ("А за кордоном?"). Without history, nothing can be resolved, so "And abroad?" alone is `self_contained`.
- **Acronyms and the other alphabet.** An acronym is 2 to 6 capital letters, such as `VPN` or `ПДФО`, in a Question that also has lowercase letters; a Question typed in capitals has no acronyms. A cross-language word has a letter from the other language's alphabet, such as `Outlook` in a Ukrainian Question. With a stated language of `mixed` or `unknown`, no word is foreign.
- **Words to keep.** Every identifier, acronym, and other-alphabet word goes into `preserve`, in order, once, whatever the class. With the stated language, that is the constraint M05-T15 will check: a rewrite that drops one of these whole words, or changes the language, is refused.
- **Why a labelled file.** Rules that feel right can still be wrong. The 33 labelled Questions are the measurement: today the rules agree with every label. When a label and a rule disagree later, the script names the case.
- **Why a separate file.** The seed `cases.json` drives every retrieval report since M05-T07. Adding follow-ups there would change all of them. The labelled file reuses the 16 seed questions by ID, and a test checks that their text and language still match the seed.
- **Where the code lives.** In the `retrieval` package, as `retrieval/rewrite_classes.py` and `retrieval/rewrite_cases.py`, because rewriting serves retrieval (ADR 0013).
- **Quiet.** A plan holds words of the Question, so its repr hides them; errors and the report never quote a Question.
- **Known limits.** 33 cases are few; the follow-up words are a fixed list, so "And for contractors?" with four words is missed; any number blocks rewriting, even "5 days", and so does a dotted abbreviation such as "e.g." or a word that starts with "..."; a word like `VPN-client` is neither an acronym nor foreign in an English Question.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before any code exists, write down what must be true when the lesson is done and what you expect the rules to get right. Step 7 checks these predictions.

Create `docs/evidence/M05-T14-rewrite-classes.md` with this skeleton, then replace the prediction prompts with your own sentences:

```markdown
# M05-T14 — Rewrite classes evidence

## Invariant

`classify_question` puts every Question in exactly one rewrite class and
returns the words any rewrite must keep, as typed, with the stated
language. A Question with any word that holds a digit, or whose parts
are joined by `_`, `@`, `/`, `.`, `:`, or a backslash, is
`exact_identifier` and never rewritable, and no separator inside a word
can split that word into rewritable pieces. Every verified case carries a
class label and its words to keep, and the loader refuses a case whose
label could not be checked. Errors, reprs, and the report never hold a
Question or a word of one. Nothing is rewritten yet.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.retrieval.rewrite_classes"`
fails with `ModuleNotFoundError`.

## Predictions

- How many of the 16 seed questions are `self_contained`, and which are not: ...
- Whether the rules agree with every label of the 33 cases on the first run: ...
- One Question a rule will get wrong if the identifier rule needed a letter and a digit: ...
- Which class M05-T15 should rewrite first, and why: ...

## Design

## Verification

## Decision

## Reflection
```

Check: the file exists, and every prediction is a full sentence that Step 7 can prove wrong.

### Step 2 — Coding step: the rewrite classes and the plan

This step writes the rules that decide which Questions a rewrite may touch.

**In plain words:** The code splits a Question into words at spaces and trims punctuation from their ends. It then checks five rules in order: a word with a digit or a joiner such as `_` or `@` inside it, a follow-up, an acronym, a word in the other alphabet, or none of these. The first rule that holds names the class. The plan it returns says whether the Question may be rewritten and lists the words any rewrite must keep. It refuses bad input with one typed error that never quotes the Question.

| Part | What it holds or does |
|---|---|
| `RewriteClass` | the five classes |
| `REWRITABLE` | `follow_up`, `acronym`, `cross_language`; the others are searched as typed |
| `RewritePlan` | the class, the stated language, and `preserve`, hidden from the repr; `may_rewrite` |
| `InvalidQuestion` | a Question or earlier Question that is not plain `str`, is blank, over 1,000 characters, or not valid Unicode; a language that is not a `Language`; history that is not a list or tuple, or longer than 10 |
| `question_words` | split on spaces, trim punctuation from both ends, but keep a joiner at the start and `_` at the end; public, because the loader in Step 5 uses the same words |
| `classify_question` | the checks, then the five rules in order, then the words to keep |
| `_POINTS_BACK` | the words that point back; Ukrainian "та" and "тому" are left out, because they also mean "and" and "so" |

Create `src/knowledge_service/retrieval/rewrite_classes.py`:

```python
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
```

`type(text) is not str` refuses a `str` subclass, because a subclass could run its own code inside `strip` and escape with another error type. `encode("utf-8")` refuses a lone surrogate, a broken character that M05-T15 could not send anywhere. `character.isnumeric()` counts any number character, so full-width digits and Roman numerals such as `Ⅻ` count too.

Check:

```bash
uv run ruff format src/knowledge_service/retrieval/rewrite_classes.py
uv run ruff check src/knowledge_service/retrieval/rewrite_classes.py
uv run pyright src/knowledge_service/retrieval/rewrite_classes.py
```

Expected: `All checks passed!` and `0 errors`.

### Step 3 — Coding step: unit tests for the rules

This step proves each class, the order of the rules, the identifier guarantee, and the typed failure.

**In plain words:** Ten example Questions, an English and a Ukrainian one per class, must get their class and their words to keep. Generated Questions with an identifier must never be rewritable, however the identifier is joined. Any generated text must keep only words it really contains. Bad input must raise the one typed error without quoting the Question.

| Test | What it proves |
|---|---|
| `test_each_question_gets_one_class_and_the_words_to_keep` | each class in English and Ukrainian, with its words to keep and `may_rewrite` |
| `test_the_first_rule_that_holds_decides` | a follow-up that names `EXP-2041` stays `exact_identifier`; `VPN` in Ukrainian is an acronym, kept once |
| `test_no_separator_cuts_an_identifier_into_rewritable_pieces` | underscore, en dash, minus sign, double hyphen, slash, colon, zero-width joiner, digits only, a dotted address, `#2041`, and a Roman numeral all stay one kept identifier |
| `test_a_code_address_or_path_without_digits_is_an_identifier` | `ERR_TIMEOUT`, `hr@example.com`, `/etc/hosts` and `.env` in both languages, `/var/log`, `@oncall`, `__init__`, `C:\Temp`, and `app.config` are identifiers even after an earlier Question, kept with their leading joiner |
| `test_a_hyphen_alone_does_not_make_an_identifier` | "Wi-Fi" stays an ordinary word |
| `test_punctuation_that_ends_a_sentence_is_not_an_identifier` | a full stop, "...", a colon, or "!?" after the last word does not make it an identifier |
| `test_a_follow_up_needs_an_earlier_question` | "And abroad?" alone is `self_contained` |
| `test_a_question_in_capitals_has_no_acronyms` | a Question typed in capitals is `self_contained` |
| `test_without_a_stated_language_no_word_is_foreign` | `mixed` and `unknown` find no foreign word |
| `test_a_question_with_an_identifier_is_never_rewritten` | generated identifiers in English and Ukrainian, with or without history |
| `test_every_word_to_keep_is_in_the_question_as_typed` | any text: each kept word is in the Question, once |
| `test_a_plan_never_shows_the_words_of_the_question` | the planted marker is kept but not in the repr |
| `test_a_question_that_cannot_be_classified_is_refused_without_quoting_it` | each refused input raises `InvalidQuestion` with no marker in the message |

The line `# ruff: noqa: RUF001` at the top lets the file hold Cyrillic letters that look like Latin ones, as `tests/test_lexical_routing.py` does.

Create `tests/test_rewrite_classes.py`:

```python
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
```

Check:

```bash
uv run pytest tests/test_rewrite_classes.py -q
```

Expected: `56 passed`.

### Step 4 — No coding in this step: label the verified cases

This step writes the labelled Questions the rules are measured against. Each label is a decision a person makes, so read every case before you accept it.

The file holds the 16 seed questions under their seed IDs, with the same text and language, and 17 new ones. Each case has a class and, where the class needs them, the words to keep. A follow-up also lists its earlier Questions, oldest first.

| New cases | Why they are there |
|---|---|
| `en-follow-up-abroad`, `en-follow-up-it`, `uk-follow-up-abroad`, `uk-follow-up-his` | follow-ups in both languages: short, or pointing back |
| `en-follow-up-keeps-identifier`, `en-follow-up-number` | follow-ups that name `EXP-2041` or `2041`; the identifier wins |
| `en-follow-up-error-code`, `en-follow-up-address`, `uk-path-in-question`, `en-follow-up-dotfile` | identifiers without a digit: `ERR_TIMEOUT`, `hr@example.com`, `/etc/hosts`, and `.env` |
| `en-new-topic-after-history` | a new, complete Question after an earlier one is not a follow-up |
| `en-acronym-sso`, `uk-acronym-pdfo`, `uk-acronym-vpn` | acronyms in both alphabets, and a Latin acronym in a Ukrainian Question |
| `uk-cross-language-outlook`, `en-cross-language-likarnianyi`, `uk-cross-language-per-diem` | words in the other alphabet, both ways |

Create `datasets/bilingual-seed/rewrite-classes.json`:

```json
{
  "name": "bilingual-rewrite-classes",
  "version": 1,
  "cases": [
    {
      "id": "en-en-annual-leave-days",
      "question": "How many days of annual leave does an employee get per year?",
      "question_language": "en",
      "rewrite_class": "self_contained"
    },
    {
      "id": "en-en-expense-deadline",
      "question": "What is the deadline for submitting an expense report?",
      "question_language": "en",
      "rewrite_class": "self_contained"
    },
    {
      "id": "en-en-vpn-first-connection",
      "question": "How do I connect to the VPN for the first time?",
      "question_language": "en",
      "rewrite_class": "acronym",
      "preserve": [
        "VPN"
      ]
    },
    {
      "id": "en-en-error-exp-2041",
      "question": "What does error EXP-2041 mean?",
      "question_language": "en",
      "rewrite_class": "exact_identifier",
      "preserve": [
        "EXP-2041"
      ]
    },
    {
      "id": "en-en-error-vpn-4031",
      "question": "VPN-4031",
      "question_language": "en",
      "rewrite_class": "exact_identifier",
      "preserve": [
        "VPN-4031"
      ]
    },
    {
      "id": "en-en-renewed-certificate",
      "question": "How is an expired device certificate renewed?",
      "question_language": "en",
      "rewrite_class": "self_contained"
    },
    {
      "id": "uk-uk-sick-first-day",
      "question": "Що робити в перший день хвороби?",
      "question_language": "uk",
      "rewrite_class": "self_contained"
    },
    {
      "id": "uk-uk-per-diem-abroad",
      "question": "Які добові за кордоном?",
      "question_language": "uk",
      "rewrite_class": "self_contained"
    },
    {
      "id": "uk-uk-form-it-0457",
      "question": "Як заповнити форму ІТ-0457?",
      "question_language": "uk",
      "rewrite_class": "exact_identifier",
      "preserve": [
        "ІТ-0457"
      ]
    },
    {
      "id": "uk-uk-who-pays-sick-leaves",
      "question": "Хто оплачує лікарняні?",
      "question_language": "uk",
      "rewrite_class": "self_contained"
    },
    {
      "id": "uk-uk-order-laptops",
      "question": "Як замовити ноутбуки?",
      "question_language": "uk",
      "rewrite_class": "self_contained"
    },
    {
      "id": "en-uk-per-diem-abroad",
      "question": "How much is the daily allowance for business trips abroad?",
      "question_language": "en",
      "rewrite_class": "self_contained"
    },
    {
      "id": "en-uk-who-pays-sick-leave",
      "question": "Who pays for sick leave?",
      "question_language": "en",
      "rewrite_class": "self_contained"
    },
    {
      "id": "uk-en-annual-leave-days",
      "question": "Скільки днів щорічної відпустки має працівник?",
      "question_language": "uk",
      "rewrite_class": "self_contained"
    },
    {
      "id": "uk-en-error-vpn-4031",
      "question": "Що означає помилка VPN-4031?",
      "question_language": "uk",
      "rewrite_class": "exact_identifier",
      "preserve": [
        "VPN-4031"
      ]
    },
    {
      "id": "uk-en-lost-receipt",
      "question": "Що робити, якщо загубився чек?",
      "question_language": "uk",
      "rewrite_class": "self_contained"
    },
    {
      "id": "en-follow-up-abroad",
      "question": "And abroad?",
      "question_language": "en",
      "history": [
        "How much is the daily allowance for business trips?"
      ],
      "rewrite_class": "follow_up"
    },
    {
      "id": "en-follow-up-it",
      "question": "How long does it take to arrive?",
      "question_language": "en",
      "history": [
        "How do I renew a device certificate?"
      ],
      "rewrite_class": "follow_up"
    },
    {
      "id": "uk-follow-up-abroad",
      "question": "А за кордоном?",
      "question_language": "uk",
      "history": [
        "Які добові у відрядженні по Україні?"
      ],
      "rewrite_class": "follow_up"
    },
    {
      "id": "uk-follow-up-his",
      "question": "Скільки днів його оплачують?",
      "question_language": "uk",
      "history": [
        "Хто оплачує лікарняний?"
      ],
      "rewrite_class": "follow_up"
    },
    {
      "id": "en-follow-up-keeps-identifier",
      "question": "How do I fix it for EXP-2041?",
      "question_language": "en",
      "history": [
        "What does error EXP-2041 mean?"
      ],
      "rewrite_class": "exact_identifier",
      "preserve": [
        "EXP-2041"
      ]
    },
    {
      "id": "en-follow-up-number",
      "question": "And error 2041?",
      "question_language": "en",
      "history": [
        "What does error EXP-2041 mean?"
      ],
      "rewrite_class": "exact_identifier",
      "preserve": [
        "2041"
      ]
    },
    {
      "id": "en-follow-up-error-code",
      "question": "And ERR_TIMEOUT?",
      "question_language": "en",
      "history": [
        "What does error EXP-2041 mean?"
      ],
      "rewrite_class": "exact_identifier",
      "preserve": [
        "ERR_TIMEOUT"
      ]
    },
    {
      "id": "en-follow-up-address",
      "question": "What about hr@example.com?",
      "question_language": "en",
      "history": [
        "Who answers questions about annual leave?"
      ],
      "rewrite_class": "exact_identifier",
      "preserve": [
        "hr@example.com"
      ]
    },
    {
      "id": "uk-path-in-question",
      "question": "Як змінити файл /etc/hosts?",
      "question_language": "uk",
      "rewrite_class": "exact_identifier",
      "preserve": [
        "/etc/hosts"
      ]
    },
    {
      "id": "en-follow-up-dotfile",
      "question": "And .env?",
      "question_language": "en",
      "history": [
        "Where do I keep the database password?"
      ],
      "rewrite_class": "exact_identifier",
      "preserve": [
        ".env"
      ]
    },
    {
      "id": "en-new-topic-after-history",
      "question": "What is the deadline for submitting an expense report?",
      "question_language": "en",
      "history": [
        "How many days of annual leave do I get?"
      ],
      "rewrite_class": "self_contained"
    },
    {
      "id": "en-acronym-sso",
      "question": "Where do I reset my SSO password?",
      "question_language": "en",
      "rewrite_class": "acronym",
      "preserve": [
        "SSO"
      ]
    },
    {
      "id": "uk-acronym-pdfo",
      "question": "Який ПДФО з добових?",
      "question_language": "uk",
      "rewrite_class": "acronym",
      "preserve": [
        "ПДФО"
      ]
    },
    {
      "id": "uk-acronym-vpn",
      "question": "Як підключитися до VPN з дому?",
      "question_language": "uk",
      "rewrite_class": "acronym",
      "preserve": [
        "VPN"
      ]
    },
    {
      "id": "uk-cross-language-outlook",
      "question": "Як налаштувати пошту в Outlook?",
      "question_language": "uk",
      "rewrite_class": "cross_language",
      "preserve": [
        "Outlook"
      ]
    },
    {
      "id": "en-cross-language-likarnianyi",
      "question": "Who approves a лікарняний?",
      "question_language": "en",
      "rewrite_class": "cross_language",
      "preserve": [
        "лікарняний"
      ]
    },
    {
      "id": "uk-cross-language-per-diem",
      "question": "Скільки становить per diem у відрядженні?",
      "question_language": "uk",
      "rewrite_class": "cross_language",
      "preserve": [
        "per",
        "diem"
      ]
    }
  ]
}
```

Check: read every case and ask whether you agree with its class and its words to keep. If you disagree, change the label, not the rules; Step 7 shows what the rules then miss.

### Step 5 — Coding step: load the labels and measure the rules

This step reads the labelled file strictly and measures the rules against it.

**In plain words:** The loader reads the file with the same care as the seed dataset loader: a size limit, strict JSON, no repeated keys, and a fixed shape. It then runs every case through the same checks a caller meets, so scoring can never fail later. Every word to keep must be a whole word of its Question. The scorer classifies each case and counts, per class, how many got the right class and kept exactly the labelled words, no more and no fewer. The report names cases by ID only.

| Part | What it does |
|---|---|
| `RewriteCase`, `RewriteCaseSet` | one labelled Question, with its texts hidden from the repr; the versioned set |
| `ClassAgreement` | per class: cases, class assigned, words kept, and the IDs of missed cases |
| `load_rewrite_cases` | file, size, UTF-8 JSON, unique keys, shape; then `_case` and `_check_set` |
| `_case` | `classify_question` must accept the case; each word to keep is a whole word, listed once; `exact_identifier` keeps at least one word; `follow_up` has history |
| `_check_set` | unique IDs; at least two cases per class |
| `score_rewrite_cases` | one `ClassAgreement` per class, in the order of `RewriteClass` |
| `render_rewrite_scores` | the Markdown table, with "May rewrite" from `REWRITABLE` |

A word to keep must be a whole word because a substring is not enough. If the label kept `EXP`, a rewrite could keep `EXP` and still drop `2041`.

The loader does not ask the rules which word is the identifier. Labels are the yardstick, so they must not depend on the rules they measure. Scoring then demands the exact list: a rule that keeps a word too many, or one too few, misses the case.

Create `src/knowledge_service/retrieval/rewrite_cases.py`:

```python
"""Verified Questions labelled with a rewrite class and the words to keep.

The cases file holds Questions a person has read and labelled: the class
from `retrieval.rewrite_classes` they belong to, and the words any rewrite
must keep exactly as typed. `score_rewrite_cases` then measures how often
`classify_question` agrees, class by class, so its rules are judged by
cases instead of by intuition.

- **Checked on load.** `load_rewrite_cases` raises `InvalidRewriteCases`
  for a file that is not strict JSON of the expected shape, a case ID used
  twice, a Question `classify_question` would refuse, a word to keep that
  is not a whole word of its Question or is listed twice, an
  `exact_identifier` case that keeps no word, a `follow_up`
  case with no earlier Question, or a class with fewer than two cases. So
  scoring a loaded set never raises.
- **Quiet.** Errors and reports name cases by ID and give classes and
  counts only; they never hold a Question or a word of one.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knowledge_service.language import Language
from knowledge_service.retrieval.rewrite_classes import (
    MAX_HISTORY,
    REWRITABLE,
    InvalidQuestion,
    RewriteClass,
    RewritePlan,
    classify_question,
    question_words,
)

REWRITE_CASES_FILE = Path("datasets/bilingual-seed/rewrite-classes.json")
MAX_FILE_BYTES = 1_000_000
MIN_CASES_PER_CLASS = 2


class InvalidRewriteCases(ValueError):
    """The rewrite cases file breaks a rule; the message quotes no value."""


@dataclass(frozen=True, slots=True)
class RewriteCase:
    """One labelled Question. The texts are hidden from the repr."""

    case_id: str
    question: str = field(repr=False)
    language: Language
    history: tuple[str, ...] = field(repr=False)
    rewrite_class: RewriteClass
    preserve: tuple[str, ...] = field(repr=False)


@dataclass(frozen=True, slots=True)
class RewriteCaseSet:
    """A versioned set of labelled Questions."""

    name: str
    version: int
    cases: tuple[RewriteCase, ...]


@dataclass(frozen=True, slots=True)
class ClassAgreement:
    """How `classify_question` did on the cases labelled with one class.

    `assigned` counts cases given their labelled class; `kept` counts cases
    whose plan keeps exactly the labelled words, in order, no more and no
    fewer. `missed` names the cases that failed either check.
    """

    rewrite_class: RewriteClass
    cases: int
    assigned: int
    kept: int
    missed: tuple[str, ...]


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _CaseFile(_File):
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    question: str = Field(min_length=1)
    question_language: Literal["en", "uk"]
    history: list[str] = Field(default_factory=list[str], max_length=MAX_HISTORY)
    rewrite_class: Literal[
        "exact_identifier", "follow_up", "acronym", "cross_language", "self_contained"
    ]
    preserve: list[str] = Field(default_factory=list[str])


class _SetFile(_File):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    version: int = Field(ge=1)
    cases: list[_CaseFile] = Field(min_length=1)


_FIELDS = frozenset(
    name for model in (_SetFile, _CaseFile) for name in model.model_fields
)


def load_rewrite_cases(path: Path) -> RewriteCaseSet:
    """Read and check a rewrite cases file; raise `InvalidRewriteCases`."""
    # A pipe or a device would block the read, or never end.
    if not path.is_file():
        raise InvalidRewriteCases("the file is missing or not a regular file")
    try:
        with path.open("rb") as file:
            raw = file.read(MAX_FILE_BYTES + 1)
    except OSError as error:
        raise InvalidRewriteCases(
            f"the file cannot be read: {type(error).__name__}"
        ) from None
    if len(raw) > MAX_FILE_BYTES:
        raise InvalidRewriteCases(f"the file is larger than {MAX_FILE_BYTES} bytes")
    try:
        content = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_constant=_no_constant,
        )
    except ValueError, RecursionError:
        raise InvalidRewriteCases("the file is not valid UTF-8 JSON") from None
    try:
        parsed = _SetFile.model_validate(content)
    except ValidationError as error:
        raise InvalidRewriteCases(_describe(error)) from None
    cases = tuple(_case(index, item) for index, item in enumerate(parsed.cases))
    _check_set(cases)
    return RewriteCaseSet(name=parsed.name, version=parsed.version, cases=cases)


def plan_case(case: RewriteCase) -> RewritePlan:
    """Classify one labelled case's Question as a caller would."""
    return classify_question(
        case.question, language=case.language, history=case.history
    )


def score_rewrite_cases(case_set: RewriteCaseSet) -> tuple[ClassAgreement, ...]:
    """Measure `classify_question` against the labels, one row per class."""
    rows: list[ClassAgreement] = []
    for rewrite_class in RewriteClass:
        labelled = [c for c in case_set.cases if c.rewrite_class is rewrite_class]
        assigned = kept = 0
        missed: list[str] = []
        for case in labelled:
            plan = plan_case(case)
            right_class = plan.rewrite_class is rewrite_class
            all_kept = plan.preserve == case.preserve
            assigned += right_class
            kept += all_kept
            if not (right_class and all_kept):
                missed.append(case.case_id)
        rows.append(
            ClassAgreement(
                rewrite_class=rewrite_class,
                cases=len(labelled),
                assigned=assigned,
                kept=kept,
                missed=tuple(missed),
            )
        )
    return tuple(rows)


def render_rewrite_scores(
    case_set: RewriteCaseSet, rows: Sequence[ClassAgreement]
) -> str:
    """Return the agreement table as Markdown, naming cases by ID only."""
    lines = [
        f"# Rewrite classes: {case_set.name} v{case_set.version}",
        "",
        "| Class | Cases | Class assigned | Words kept | May rewrite | Missed |",
        "|---|---:|---:|---:|---|---|",
    ]
    for row in rows:
        may = "yes" if row.rewrite_class in REWRITABLE else "no"
        missed = ", ".join(row.missed) or "none"
        lines.append(
            f"| {row.rewrite_class} | {row.cases} | {row.assigned} | {row.kept}"
            f" | {may} | {missed} |"
        )
    return "\n".join(lines) + "\n"


def _case(index: int, item: _CaseFile) -> RewriteCase:
    language = Language(item.question_language)
    history = tuple(item.history)
    try:
        # The same checks a caller meets, so scoring cannot fail later.
        classify_question(item.question, language=language, history=history)
    except InvalidQuestion as error:
        raise InvalidRewriteCases(f"cases.{index}: {error}") from None
    words = question_words(item.question)
    for word in item.preserve:
        # Whole words only, as typed: "EXP" does not keep "EXP-2041".
        if word not in words:
            raise InvalidRewriteCases(
                f"cases.{index}.preserve: a word to keep is not a word of the question"
            )
    if len(set(item.preserve)) != len(item.preserve):
        raise InvalidRewriteCases(f"cases.{index}.preserve: a word is listed twice")
    # Which word is checked by scoring, not here: the labels must not
    # depend on the rules they measure.
    if item.rewrite_class == "exact_identifier" and not item.preserve:
        raise InvalidRewriteCases(
            f"cases.{index}: an exact_identifier case must keep its identifier"
        )
    if item.rewrite_class == "follow_up" and not history:
        raise InvalidRewriteCases(
            f"cases.{index}: a follow_up case needs an earlier question"
        )
    return RewriteCase(
        case_id=item.id,
        question=item.question,
        language=language,
        history=history,
        rewrite_class=RewriteClass(item.rewrite_class),
        preserve=tuple(item.preserve),
    )


def _check_set(cases: tuple[RewriteCase, ...]) -> None:
    seen: set[str] = set()
    for case in cases:
        if case.case_id in seen:
            raise InvalidRewriteCases(f"case {case.case_id} appears more than once")
        seen.add(case.case_id)
    for rewrite_class in RewriteClass:
        count = sum(case.rewrite_class is rewrite_class for case in cases)
        if count < MIN_CASES_PER_CLASS:
            raise InvalidRewriteCases(
                f"class {rewrite_class} needs at least {MIN_CASES_PER_CLASS} cases"
            )


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError("a key appears twice in one object")
    return dict(pairs)


def _no_constant(name: str) -> None:
    raise ValueError("NaN and Infinity are not JSON")


def _describe(error: ValidationError) -> str:
    # The message names the place and the rule, never a value.
    first = error.errors(include_input=False, include_url=False)[0]
    parts = [
        str(part) if isinstance(part, int) or part in _FIELDS else "<unknown>"
        for part in first["loc"]
    ]
    return f"{'.'.join(parts) or 'file'}: {first['msg']}"
```

Check: the same three commands as in Step 2, on `src/knowledge_service/retrieval/rewrite_cases.py`.

### Step 6 — Coding step: test the labels and add the script

This step tests the loader and the measurement, and adds a script that prints the result.

**In plain words:** The real labelled file must load, every class must be fully agreed, and the 16 seed questions must match the seed dataset. A small valid file is then broken one rule at a time, and each break must be refused without quoting its marker. The script writes the table and returns failure when any case is missed, so it can guard a later change to the rules.

| Test | What it proves |
|---|---|
| `test_the_rules_agree_with_every_verified_label` | every class: all cases get their class and keep their words |
| `test_a_label_that_keeps_fewer_words_than_the_rules_is_missed` | a label that lists `VPN` but not `SSO` is missed, though its class is right |
| `test_every_seed_question_is_labelled_as_it_is_asked` | all 16 seed IDs are labelled with the same question and language |
| `test_the_report_names_cases_by_id_only` | exact rows, and no question or kept word in the report |
| `test_the_valid_template_loads` | the small valid file loads, and its repr hides the marker |
| `test_a_file_that_breaks_a_rule_is_refused_without_quoting_it` | 15 broken files, each refused with `InvalidRewriteCases` and no marker |
| `test_bytes_that_are_not_a_cases_file_are_refused` | bad UTF-8, a repeated key, `NaN`, and a list |
| `test_a_missing_file_is_refused` | a path that does not exist |

Create `tests/test_rewrite_cases.py`:

```python
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
```

Create `scripts/score_rewrite_classes.py`:

```python
"""Measure the rewrite-class rules against the verified labels.

Reads `datasets/bilingual-seed/rewrite-classes.json`, classifies every
labelled Question with `classify_question`, and writes how often the class
and the words to keep agree, class by class, to
`artifacts/evaluations/rewrite-classes.md`. Git ignores the report. It
names cases by ID only. The exit code is 1 when any case is missed, so the
script can guard a change to the rules.
"""

import argparse
import sys
from pathlib import Path

from knowledge_service.retrieval.rewrite_cases import (
    REWRITE_CASES_FILE,
    InvalidRewriteCases,
    load_rewrite_cases,
    render_rewrite_scores,
    score_rewrite_cases,
)

REPORT = Path("artifacts/evaluations/rewrite-classes.md")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=REWRITE_CASES_FILE)
    parser.add_argument("--output", type=Path, default=REPORT)
    arguments = parser.parse_args()
    try:
        case_set = load_rewrite_cases(arguments.cases)
    except InvalidRewriteCases as error:
        sys.stderr.write(f"rewrite cases are invalid: {error}\n")
        return 1
    rows = score_rewrite_cases(case_set)
    rendered = render_rewrite_scores(case_set, rows)
    output: Path = arguments.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"{rendered}\nFull report: {output}\n")
    return 1 if any(row.missed for row in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
```

Check:

```bash
uv run pytest tests/test_rewrite_cases.py -q
uv run ruff check scripts/score_rewrite_classes.py
uv run pyright scripts/score_rewrite_classes.py
```

Expected: `25 passed`, `All checks passed!`, and `0 errors`.

### Step 7 — No coding in this step: measure, break it, and decide

This step runs the measurement, breaks the identifier rule on purpose, and asks you to decide what M05-T15 may rewrite.

**Action 1:** run the script:

```bash
uv run python scripts/score_rewrite_classes.py; echo "exit $?"
```

Expected: every row has as many "Class assigned" and "Words kept" as "Cases", "Missed" is `none`, and the exit code is 0. The report goes to `artifacts/evaluations/rewrite-classes.md`, which Git ignores.

**Action 2:** break the identifier rule on purpose. In `src/knowledge_service/retrieval/rewrite_classes.py`, replace the body of `_is_identifier` with the rule an earlier draft used, a letter and a digit:

```python
    return any(c.isdigit() for c in word) and any(c.isalpha() for c in word)
```

Then run:

```bash
uv run pytest tests/test_rewrite_classes.py tests/test_rewrite_cases.py -q
uv run python scripts/score_rewrite_classes.py; echo "exit $?"
```

Expected: 17 failed: four cases of `test_no_separator_cuts_an_identifier_into_rewritable_pieces` (`2041`, `10.0.0.1`, `#2041`, and the Roman numeral), all eleven cases of `test_a_code_address_or_path_without_digits_is_an_identifier`, `test_the_rules_agree_with_every_verified_label`, and `test_the_report_names_cases_by_id_only`. The script names `en-follow-up-number`, `en-follow-up-error-code`, `en-follow-up-address`, `uk-path-in-question`, and `en-follow-up-dotfile` as missed and exits 1: "And error 2041?" became a rewritable follow-up. Undo the change and run again until all pass.

**Action 3:** write the decision. In the evidence file, under "Decision", write:

- the table from the report, because `artifacts/` is ignored by Git;
- which classes M05-T15 may rewrite, and which it must not, with one reason each;
- the constraints M05-T15 must check on every rewrite: each word to keep present as a whole word, and the language unchanged;
- what the deliberate break showed, with the case ID;
- at least one **falsified assumption** in the shape "I assumed X. The run shows Y (case IDs). So Z is wrong."

### Step 8 — No coding in this step: gates, docs, evidence, and progress

This step proves everything still works, updates the text the change made incomplete, and records the result.

**Action 1:** in `docs/architecture.md`:

Find in `docs/architecture.md`:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case) |
```

Replace with:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree; nothing is rewritten yet) |
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_rewrite_classes.py tests/test_rewrite_cases.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 81 passed for the narrow tests, `make test-unit` 1538 passed, and `make test-integration` 313 passed.

**Action 3:** complete the evidence file:

- **Design:** the changed files; the five classes and their order; why identifiers win and why any digit or inner joiner counts; what a word is; why words to keep must be whole words; why a separate labelled file; what is not touched (no stored Documents, no model calls).
- **Verification:** the commands and counts, the script's result, the deliberate break, and the known limits from "Before you begin".
- **Review:** an adversarial review of the prototype found two broken guarantees and two smaller problems, all fixed, each with a test that failed before the fix: words were split at dashes and underscores, so `EXP_2041` or `EXP–2041` became a rewritable acronym `EXP`, and now only spaces split words; digits alone, such as `2041` or `10.0.0.1`, were not identifiers, so a follow-up naming them was rewritable, and now any number character counts; a labelled Question over 1,000 characters loaded and then crashed the script, so the loader now runs every case through `classify_question`; and a `str` subclass or a lone surrogate escaped as another error type, so only plain, valid `str` is accepted. The finishing Spec review then found that an identifier without a digit, such as `ERR_TIMEOUT` or `hr@example.com`, was rewritable with nothing kept; now `_`, `@`, `/`, `.`, `:`, or a backslash inside a word makes it an identifier too, and scoring demands the exact words to keep instead of a subset. A second Spec review then found that a joiner at the start of a word, as in `.env` or `@oncall`, was trimmed before the check; now a joiner at the start and `_` at the end are kept.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M05-T14 row to `docs/curriculum/PROGRESS.md`, set the next task to M05-T15, and commit with `m05-t14: classify queries for rewriting`.

## Reflection questions

1. What does `classify_question` hide from the code that will call it in M05-T15? List what a caller never has to think about, such as what a word is or which rule comes first.
2. A later change wants rewriting to help more Questions, so it drops the identifier rule for follow-ups. Which guarantee from your invariant would that break, and which test and which labelled case would catch it?
3. "And for contractors?" has four words and no word that points back. What would the rules do with it after "How many days of leave do I get?", and what would you change: the rule, the word list, or the label?
4. Which of your predictions was wrong, and what did you believe about the seed questions or the rules that made you predict it?
5. Before M05-T15 rewrites anything for real users, what would you measure to show that rewriting a class helps? Name one metric, one class, and one case that could prove you wrong.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before running anything.
- [ ] `classify_question` returns one of five classes, `may_rewrite`, the stated language, and the words to keep.
- [ ] Any word with a number character makes the Question `exact_identifier`, and no separator inside a word splits it.
- [ ] Bad input raises `InvalidQuestion` only, and no error or repr quotes the Question.
- [ ] `rewrite-classes.json` labels all 16 seed questions and 17 new ones, at least two per class, each with its words to keep.
- [ ] `load_rewrite_cases` refuses a case it could not check, without quoting it.
- [ ] `scripts/score_rewrite_classes.py` reports every label agreed and exits 0.
- [ ] You ran the deliberate break from Step 7, saw 17 tests fail and five cases missed, `en-follow-up-number` among them, and undid it.
- [ ] The evidence file holds the table, which classes may be rewritten, the constraints for M05-T15, and a falsified assumption.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The architecture text, evidence, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
