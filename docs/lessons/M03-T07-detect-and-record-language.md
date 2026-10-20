# M03-T07 — Detect and record language

Source task: [M03-T07](../curriculum/milestones/M03-local-ingestion/M03-T07-detect-and-record-language.md)

## What you will do

This lesson includes coding: Steps 2–4 write code, while Steps 1 and 5 do not.

The service answers questions in English and Ukrainian, and the evaluation plan measures each language and each direction separately (English to Ukrainian, Ukrainian to English, and so on). For that, every Document needs a label saying which language its text is in. This lesson adds that label to `NormalizedDocument`.

| Requirement | How this lesson meets it |
|---|---|
| English, Ukrainian, mixed, unknown | A new `Language` enum with exactly those four values. |
| Confidence | A number from 0 to 1 saying how strongly the text supports the label. Short texts get lower confidence. |
| Short and bilingual texts have a documented fallback | Fewer than 20 letters gives `unknown`. A real mix of both scripts gives `mixed`. |
| Not an authorization decision | The label is stored next to the access rules but never read by them. A test proves English and Ukrainian copies keep the same access. |

Terms used in this lesson:

- **Script:** the set of letters a language is written in. English uses the Latin script; Ukrainian uses the Cyrillic script, which Russian, Belarusian, Serbian, and others also use.
- **Heuristic:** a simple, explainable rule of thumb instead of a trained model. It can be wrong on unusual input, so its limits are written down.

**Decision: a letter-counting heuristic, not a detection library.** The corpus has two languages that use two different scripts, so counting Latin and Cyrillic letters already separates them. A heuristic is deterministic, needs no new dependency or model file, runs in linear time, and every result can be explained by counting letters. A statistical library would label any language, but its answers on short or mixed texts are harder to explain and can change between versions.

**Why "not an authorization decision" needs saying.** It would be tempting to use language for access, for example "Ukrainian documents are for the Kyiv office". That would make access depend on a guess. Who may read a Document must come only from the Source's access rules.

## Before you begin

- **Input:** `detect_language` reads `ParsedDocument.blocks`, not `ParsedDocument.text`. For HTML, `text` is the raw HTML source, full of English tag and attribute names; the blocks hold only the visible text.
- **Files you will touch:**
  - new `src/knowledge_service/language.py` and `tests/test_language.py`
  - `src/knowledge_service/normalization.py` and `tests/test_normalization.py`
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your prediction

**Invariant:** the same blocks always give the same `LanguageProfile`. A profile is `unknown` with confidence 0, or a known label with a confidence above 0 and at most 1. Code blocks never affect the label. The label never changes a Document's access.

**Action:** create `docs/evidence/M03-T07-language-metadata.md`:

```markdown
# M03-T07 — Language metadata evidence

Completed: pending

## Invariant

Pending.

## Prediction

Before writing code, say what label and roughly what confidence you expect
for: a long English paragraph, a long Ukrainian paragraph, the two together,
a Ukrainian paragraph with a few English terms such as API and Kubernetes,
"Hello, team!", a 40-letter Ukrainian sentence, a Russian paragraph, a
Kazakh sentence, Chinese text, and a Ukrainian paragraph followed by a large
Python code block.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and your ten predictions.

### Step 2 — Coding step: the language module

The policy, in order:

| Rule | Result |
|---|---|
| Fewer than 20 letters | `unknown` |
| More than 20% of letters from another script (Chinese, Greek, ...) | `unknown` |
| At least 80% Latin | `en` |
| At least 80% Cyrillic, with Ukrainian-only letters that outnumber non-Ukrainian Cyrillic letters | `uk` |
| At least 80% Cyrillic otherwise | `unknown` |
| Anything else (both scripts in real amounts) | `mixed` |

Confidence is the share of letters that support the label, times `min(1, letters / 200)`, rounded to two decimals. A 200-letter English paragraph gets 1.0; a 40-letter Ukrainian sentence gets 0.2.

Decisions:

- **Ukrainian needs positive evidence.** Only `ґ`, `є`, and `ї` belong to Ukrainian alone. The dotted `і` is left out on purpose, because Belarusian and Kazakh use it heavily. Without at least one of those three letters, Cyrillic text is `unknown`, which also covers Russian and Bulgarian sentences that happen to contain no tell-tale letters. The price: a short Ukrainian phrase without them is `unknown` too. That is the documented fallback.
- **Letters outside the Ukrainian alphabet count against Ukrainian.** That covers Russian `ы э ъ ё`, Belarusian `ў`, Serbian `ј љ њ`, and Kazakh `қ ү ө`. A Russian quotation inside a long Ukrainian text does not flip it, because the Ukrainian letters still outnumber it.
- **Code blocks are skipped.** Code is written in English keywords, so a Ukrainian guide with a code sample would otherwise come out `mixed` or even `en`.
- **Shares are compared in whole numbers.** `100 * other > 20 * total` instead of `other > 0.2 * total`. In floating point, `1 - 0.8` is `0.19999999999999996`, so text with exactly 20% other letters was wrongly sent to `unknown`.
- **The Ukrainian apostrophe `ʼ` (U+02BC) is not a letter here.** Python's `isalpha()` says it is, which would count it as another script.
- **`LanguageProfile` checks its own invariant.** `unknown` must have confidence 0, and a known label must have more than 0. That is the module's one typed failure, `InvalidLanguageProfile`.

**Action:** create `src/knowledge_service/language.py`:

```python
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
        if (self.language is Language.UNKNOWN) != (self.confidence == 0.0):
            raise InvalidLanguageProfile(
                "exactly an unknown language has a confidence of 0"
            )


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
```

Some details:

- **`ord(character)`** gives the Unicode code point. Latin letters, including accented ones like `é`, are below `0x250`. The Cyrillic blocks run from `0x400` to `0x52F`.
- **`ukrainian_only += character in _UKRAINIAN_ONLY`** adds a `bool`. In Python `True` counts as 1 and `False` as 0, so this counts matches without an `if`.
- **`latin = cyrillic = other = ... = 0`** sets several counters to 0 in one line.
- **`_UNKNOWN`** is built once and reused, because a frozen dataclass can be shared safely.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 3 — Coding step: record the language in `normalize`

**Decision: one field on `NormalizedDocument`.** Language describes the whole document's text, like the title or media type, so it belongs beside them. Later chunking can still look at single blocks if it needs to.

**Action:** edit `src/knowledge_service/normalization.py`.

1. Update the module docstring's first paragraph:

```python
Normalization picks a parser from the record's media type, decodes text as
UTF-8, parses the content into structural blocks, canonicalizes the
descriptive metadata, and records the text's language. Content it cannot turn
into blocks never raises: the record comes back as `Quarantined` with a
reason, so one bad item cannot stop a Synchronization Run. The same record
always produces the same result.
```

2. Import the new module, next to the other `knowledge_service` imports:

```python
from knowledge_service.language import LanguageProfile, detect_language
```

3. Add a field to `NormalizedDocument`, between `parser_version` and `parsed`:

```python
    language: LanguageProfile
```

4. In `normalize`, pass it when building the result, between `parser_version=` and `parsed=`:

```python
        language=detect_language(parsed),
```

`detect_language` runs only after parsing succeeded and produced blocks, so a quarantined record never gets a language.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_normalization.py -q` still gives 25 passed. The existing tests compare whole outcomes, and the new field is filled the same way every time.

### Step 4 — Coding step: tests

| Test | Proves |
|---|---|
| `test_english_text_is_english_with_full_confidence` | The successful case, English. |
| `test_ukrainian_text_is_ukrainian_with_full_confidence` | The successful case, Ukrainian. |
| `test_a_bilingual_document_is_mixed` | The bilingual fallback. |
| `test_a_few_english_terms_keep_ukrainian_text_ukrainian` | A few English words do not flip the label. |
| `test_short_text_falls_back_to_unknown` (4 cases) | The short-text fallback, including empty text. |
| `test_confidence_shrinks_for_text_that_is_short_but_long_enough` | 40 letters gives 0.2. |
| `test_a_short_ukrainian_phrase_without_distinctive_letters_is_unknown` | No `ґ є ї`, no `uk`. |
| `test_exactly_the_dominant_share_still_counts` | The 20% boundary. |
| `test_other_cyrillic_languages_are_not_mistaken_for_ukrainian` (7 cases) | Russian, Serbian, Belarusian, Kazakh, Macedonian. |
| `test_text_in_another_script_is_unknown` | Chinese. |
| `test_code_blocks_do_not_count` | A code block does not sway the label. |
| `test_an_invalid_profile_is_refused` (4 cases) | The typed failure. |

Seven of these cases came from an adversarial review of the first version: the boundary, the short phrase, and five neighbouring languages. Each one failed against that version.

**Action:** create `tests/test_language.py`:

```python
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
```

The fixtures are long enough to pass 200 letters, so their confidence is exactly 1.0. Two lines carry `# noqa: RUF001`. Ruff flags a one-letter Cyrillic word such as `і` or `се`, because it looks exactly like a Latin letter; here the Cyrillic is intended.

Then add these to `tests/test_normalization.py`. First the import, after the `SourceId` import:

```python
from knowledge_service.language import Language
```

Then these two tests, before `test_a_deleted_record_cannot_be_normalized`:

```python
UKRAINIAN_PAGE = (
    "<html><head><title>Відпустка</title></head><body>"
    '<div class="content"><h1>Як оформити відпустку</h1>'
    "<p>Заяву на відпустку подають керівнику команди щонайменше за два тижні "
    "до її початку; після погодження її реєструє відділ кадрів.</p>"
    "</div></body></html>"
).encode()


def test_the_language_is_recorded_from_the_text_not_the_markup() -> None:
    # The tags and attributes are English words, but only block text counts.
    normalized = document(
        normalize(record(UKRAINIAN_PAGE, "text/html"), source_id=SOURCE_ID)
    )

    assert normalized.language.language is Language.UKRAINIAN


def test_the_language_never_changes_who_may_read_a_document() -> None:
    restricted = SourceAccess(public=False, principals=("group:finance",))
    english = SourceRecord.present(
        external_id="a.txt",
        version="v1",
        media_type="text/plain",
        content=b"The finance team approves every payment above the limit.",
        access=restricted,
    )
    ukrainian = SourceRecord.present(
        external_id="b.txt",
        version="v1",
        media_type="text/plain",
        content="Фінансова команда погоджує кожен платіж понад ліміт.".encode(),
        access=restricted,
    )

    documents = [
        document(normalize(item, source_id=SOURCE_ID)) for item in (english, ukrainian)
    ]

    assert [item.language.language for item in documents] == [
        Language.ENGLISH,
        Language.UKRAINIAN,
    ]
    assert all(item.access == restricted for item in documents)
```

The HTML test proves that tags and attributes do not count. The access test proves the label is never an authorization decision: an English and a Ukrainian record with the same access keep exactly that access.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_language.py tests/test_normalization.py -v
```

All 51 tests pass: 24 in `test_language.py` and 27 in `test_normalization.py`.

### Step 5 — No coding in this step: run the gates, then record evidence

**Action:** run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_language.py tests/test_normalization.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. In Design, record these limits of the policy, found by the adversarial review:

- **Any Latin-script language is labelled `en`.** Polish or German text comes out English. The corpus is English and Ukrainian, so this is accepted.
- **Technical Ukrainian flips to `mixed` quickly.** Inline code, file paths, and email addresses count as Latin letters. One sentence with `src/config/settings.py` and an email address was already `mixed`. Inline code (backticks, HTML `<code>`) is counted; only code blocks are skipped.
- **Short Ukrainian without `ґ`, `є`, or `ї` is `unknown`.** That is the price of not mistaking Belarusian, Kazakh, or Russian for Ukrainian.
- **Old and rare Cyrillic** (Cyrillic Extended-A, B, and C) counts as another script.

Answer these reflection questions in your own words:

1. Why does the detector read the blocks and not `ParsedDocument.text`? What would an HTML page in Ukrainian be labelled if it read `text`?
2. A colleague suggests "documents detected as Ukrainian are only visible to the Ukrainian office". Why is that a bad idea, even if detection were perfect?
3. The first version called neutral Russian sentences Ukrainian. Which rule fixed that, and what does it cost for short Ukrainian phrases?
4. Which test would let a real bug reach production if you deleted it? Name it and describe the bug.

**Check:** every command passes, and the evidence explains the design in your words.

## Completion checklist

- [ ] `language.py` has `Language` (en, uk, mixed, unknown), `LanguageProfile`, `InvalidLanguageProfile`, and `detect_language`.
- [ ] Short text (under 20 letters) is `unknown` with confidence 0, and bilingual text is `mixed`.
- [ ] Russian, Belarusian, Serbian, Kazakh, and Macedonian are never labelled `uk`.
- [ ] Code blocks and HTML markup do not change the label.
- [ ] `NormalizedDocument.language` is set by `normalize`, and the label never changes access.
- [ ] An invalid profile raises `InvalidLanguageProfile`.
- [ ] All 51 tests in the two test files, the full deterministic suite, format, lint, types, `make check`, and `make docs-check` pass.
- [ ] The evidence file holds your invariant, predictions, design (including the four recorded limits), verification, and your own reflection.

When you're done, share your code or any error you hit, and I'll review it.
