# M05-T14 — Класифікація запитань для переписування

Англійська версія: [M05-T14-classify-queries-for-rewriting.md](M05-T14-classify-queries-for-rewriting.md)

Вихідне завдання: [M05-T14](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T14-classify-queries-for-rewriting.md)

## Що ви зробите

Наступний урок, M05-T15, дозволить мовній моделі переписувати деякі запитання (Question) перед пошуком. Наприклад, вона перетворить «А за кордоном?» на повне запитання, розгорне абревіатуру або додасть слово іншою мовою. Але переписування може й зламати запитання: змініть один символ у `EXP-2041`, і пошук нічого не знайде. Цей урок ще до появи переписування вирішує, яких запитань воно може торкатися і які слова мусить залишити точно такими, як їх набрано. Ці правила ще й міряються на запитаннях, які розмітила людина, тож судять їх за випадками, а не за відчуттям.

- `classify_question` відносить кожне запитання рівно до одного з п'яти класів за фіксованими правилами і повертає план: чи можна його переписати і які слова мусять уціліти.
- Розмічений файл `rewrite-classes.json` містить 33 перевірені запитання: 16 seed-запитань і 17 нових, серед них уточнювальні разом із попередніми запитаннями.
- `load_rewrite_cases` суворо перевіряє цей файл; `score_rewrite_cases` міряє, як часто правила збігаються з мітками, клас за класом.
- `scripts/score_rewrite_classes.py` друкує цей вимір і завершується помилкою, якщо якусь мітку пропущено.

| Завдання вимагає | Де це відбувається |
|---|---|
| Вимірювані класи: уточнювальні запитання, абревіатури, слова іншою мовою | Крок 2: п'ять класів, кожен задано одним правилом, яке можна перевірити |
| Точні ідентифікатори виключено | Крок 2: будь-яке слово з цифрою або з `_`, `@`, `/`, `.`, `:` чи зворотною скісною рискою всередині робить запитання `exact_identifier`, яке ніколи не переписується |
| Перевірені випадки мають мітки класу й обмеження на збереження | Кроки 4 і 5: кожен випадок має клас і слова, які треба зберегти; завантажувач їх перевіряє |
| Детерміновані тести: успіх, найризиковіший край, типізована помилка | Кроки 3 і 6: кожен клас; жоден роздільник не розрізає ідентифікатор; `InvalidQuestion` і `InvalidRewriteCases` |
| Жодного підкинутого чутливого тексту в телеметрії | Кроки 3 і 6: підкинута мітка ніколи не з'являється в repr, помилці чи звіті |
| Авторизація до того, як дані покинуть сховище | Не зачіпається: тут ніщо не читає збережених документів і не звертається до моделі |

Цей урок містить код: кроки 2, 3, 5 і 6 пишуть код. Кроки 1, 4, 7 і 8 коду не потребують; крок 4 записує розмічені дані. Ніщо не звертається до OpenAI чи бази даних, окрім наявного набору integration-тестів у кроці 8.

Терміни цього уроку:

- **Переписування запиту (query rewriting):** зміна тексту для пошуку перед retrieval. Наприклад, уточнювальне «А за кордоном?» стає «Які добові у відрядженні за кордоном?». Саме запитання користувача не змінюється.
- **Уточнювальне запитання (follow-up):** запитання, яке має сенс лише разом із попереднім у тій самій розмові (Conversation), як «Скільки це триває?» після «Як оновити сертифікат?».
- **Точний ідентифікатор (exact identifier):** слово, що має збігтися літера в літеру: код помилки, номер форми чи адреса — `EXP-2041`, `ІТ-0457`, `10.0.0.1`.
- **Обмеження на збереження (preservation constraint):** правило, яке переписування мусить дотримати. Тут це кожне з перелічених слів, точно як набрано, і мова запитання.
- **Мітка (label):** клас і слова для збереження, які людина визначила для запитання. Правила міряються саме на мітках.

## Перш ніж почати

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілий, коли ви до нього дійдете.

- **Навіщо класифікувати до переписування.** Переписування коштує виклику моделі і може зашкодити. Більшість seed-запитань і так зрозумілі, а злитий пошук ставить 15 із 16 першими. Тож переписувати варто лише запитання, для яких є причина: уточнювальне, з абревіатурою або зі словом іншою мовою. Усе інше шукається так, як набрано.
- **П'ять класів, один на запитання.** Правила перевіряються у фіксованому порядку, і перше, що спрацювало, дає клас: `exact_identifier`, `follow_up`, `acronym`, `cross_language`, `self_contained`. Одна мітка на запитання робить вимір простим: правильно чи ні.
- **Ідентифікатори перемагають.** Запитання з будь-яким словом, що містить цифру, — це `exact_identifier`, і його ніколи не переписують, навіть якщо воно водночас уточнювальне. Так само й зі словом, частини якого з'єднано через `_`, `@`, `/`, `.`, `:` чи зворотну скісну риску: `ERR_TIMEOUT`, `hr@example.com` або `/etc/hosts`. Звичайний дефіс не рахується, бо він з'єднує й звичайні слова, як-от «Wi-Fi». Правило навмисно широке: воно зупиняє й «And error 2041?», і «10.0.0.1». Пропущений ідентифікатор коштує користувачеві відповіді; пропущене переписування коштує лише трохи повноти (recall).
- **Що таке слово.** Слова розділяються лише пробілами, а потім з обох кінців обрізається пунктуація. Тож `EXP_2041`, `EXP–2041` із коротким тире чи `EXP/2041` лишаються одним словом. З'єднувач на кшталт `.` чи `/` не обрізається на початку слова, а `_` — у кінці, тож `.env`, `/etc/hosts` і `__init__` лишаються цілими, а крапка в «leave.» і далі обрізається. Ранній варіант розділяв слова дефісами й підкресленнями, і адверсаріальне рев'ю перетворило `EXP_2041` на абревіатуру `EXP`, яку можна переписати.
- **Уточнювальному запитанню потрібна історія.** Запитання вважається уточнювальним, лише коли в розмові є попередні запитання і воно посилається назад («it», «його», «там») або має щонайбільше три слова («А за кордоном?»). Без історії нічого розв'язати не можна, тож «And abroad?» саме по собі — це `self_contained`.
- **Абревіатури й інша абетка.** Абревіатура — це від 2 до 6 великих літер, як `VPN` чи `ПДФО`, у запитанні, де є й малі літери; у запитанні, набраному великими літерами, абревіатур немає. Слово іншою мовою містить літеру з абетки іншої мови, як `Outlook` в українському запитанні. Якщо заявлена мова `mixed` чи `unknown`, жодне слово не вважається іншомовним.
- **Слова для збереження.** Кожен ідентифікатор, абревіатура і слово з іншої абетки потрапляє в `preserve`, по порядку і по одному разу, хоч би який був клас. Разом із заявленою мовою це те обмеження, яке перевірятиме M05-T15: переписування, що загубило одне з цих слів цілком або змінило мову, відхиляється.
- **Навіщо розмічений файл.** Правила, які здаються правильними, усе одно можуть помилятися. 33 розмічені запитання — це вимір: сьогодні правила збігаються з кожною міткою. Коли згодом мітка й правило розійдуться, скрипт назве випадок.
- **Чому окремий файл.** Seed-файл `cases.json` живить кожен звіт пошуку від M05-T07. Якби уточнювальні запитання додали туди, змінилися б усі ці звіти. Розмічений файл повторно використовує 16 seed-запитань за їхніми ID, а тест перевіряє, що текст і мова досі збігаються з seed.
- **Де живе код.** У пакеті `retrieval`, у файлах `retrieval/rewrite_classes.py` і `retrieval/rewrite_cases.py`, бо переписування служить пошуку (ADR 0013).
- **Тиша.** План містить слова запитання, тож його repr їх приховує; помилки і звіт ніколи не цитують запитання.
- **Відомі обмеження.** 33 випадки — це мало; слова, що посилаються назад, — фіксований список, тож «And for contractors?» із чотирма словами буде пропущено; будь-яке число блокує переписування, навіть «5 днів», і так само скорочення з крапками на кшталт «e.g.» чи слово, що починається з «...»; слово на кшталт `VPN-client` в англійському запитанні не є ні абревіатурою, ні іншомовним.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і передбачення

Перш ніж з'явиться код, запишіть, що має бути правдою наприкінці уроку і що, на вашу думку, правила визначать правильно. Крок 7 перевіряє ці передбачення.

Створіть `docs/evidence/M05-T14-rewrite-classes.md` за цим шаблоном, а потім замініть підказки до передбачень власними реченнями:

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

Перевірка: файл існує, і кожне передбачення — повне речення, яке крок 7 може спростувати.

### Крок 2 — Крок із кодом: класи переписування і план

Цей крок пише правила, які вирішують, яких запитань може торкатися переписування.

**Простими словами:** Код розбиває запитання на слова за пробілами й обрізає пунктуацію з їхніх кінців. Потім він по черзі перевіряє п'ять правил: слово з цифрою або з'єднувачем на кшталт `_` чи `@` усередині, уточнювальне запитання, абревіатура, слово з іншої абетки або нічого з цього. Перше правило, що спрацювало, дає клас. План, який він повертає, каже, чи можна переписати запитання, і перелічує слова, які будь-яке переписування мусить зберегти. Погані вхідні дані він відхиляє однією типізованою помилкою, яка ніколи не цитує запитання.

| Частина | Що вона містить чи робить |
|---|---|
| `RewriteClass` | п'ять класів |
| `REWRITABLE` | `follow_up`, `acronym`, `cross_language`; решта шукаються так, як набрано |
| `RewritePlan` | клас, заявлена мова і `preserve`, прихований від repr; `may_rewrite` |
| `InvalidQuestion` | запитання чи попереднє запитання, яке не є звичайним `str`, порожнє, довше за 1000 символів або не є коректним Unicode; мова, яка не є `Language`; історія, що не є list чи tuple або довша за 10 |
| `question_words` | розбиває за пробілами, обрізає пунктуацію з обох кінців, але лишає з'єднувач на початку і `_` у кінці; публічна, бо завантажувач у кроці 5 використовує ті самі слова |
| `classify_question` | перевірки, потім п'ять правил по черзі, потім слова для збереження |
| `_POINTS_BACK` | слова, що посилаються назад; українські «та» і «тому» не включено, бо вони також означають «і» та «тож» |

Створіть `src/knowledge_service/retrieval/rewrite_classes.py`:

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

`type(text) is not str` відхиляє підклас `str`, бо підклас міг би виконати власний код усередині `strip` і вирватися з іншим типом помилки. `encode("utf-8")` відхиляє самотній сурогат — зламаний символ, який M05-T15 нікуди не змогла б надіслати. `character.isnumeric()` рахує будь-який числовий символ, тож повноширинні цифри і римські числа на кшталт `Ⅻ` теж зараховуються.

Перевірка:

```bash
uv run ruff format src/knowledge_service/retrieval/rewrite_classes.py
uv run ruff check src/knowledge_service/retrieval/rewrite_classes.py
uv run pyright src/knowledge_service/retrieval/rewrite_classes.py
```

Очікувано: `All checks passed!` і `0 errors`.

### Крок 3 — Крок із кодом: unit-тести правил

Цей крок доводить кожен клас, порядок правил, гарантію щодо ідентифікаторів і типізовану помилку.

**Простими словами:** Десять прикладів запитань, англійське й українське для кожного класу, мають отримати свій клас і свої слова для збереження. Згенеровані запитання з ідентифікатором ніколи не мають ставати придатними до переписування, хоч би як ідентифікатор було з'єднано. Будь-який згенерований текст має зберігати лише ті слова, які справді містить. Погані вхідні дані мають кидати одну типізовану помилку без цитування запитання.

| Тест | Що він доводить |
|---|---|
| `test_each_question_gets_one_class_and_the_words_to_keep` | кожен клас англійською й українською, зі словами для збереження і `may_rewrite` |
| `test_the_first_rule_that_holds_decides` | уточнювальне запитання з `EXP-2041` лишається `exact_identifier`; `VPN` в українському запитанні — абревіатура, збережена один раз |
| `test_no_separator_cuts_an_identifier_into_rewritable_pieces` | підкреслення, коротке тире, знак мінус, подвійний дефіс, скісна риска, двокрапка, невидимий з'єднувач (zero-width joiner), самі цифри, адреса з крапками, `#2041` і римське число — усе лишається одним збереженим ідентифікатором |
| `test_a_code_address_or_path_without_digits_is_an_identifier` | `ERR_TIMEOUT`, `hr@example.com`, `/etc/hosts` і `.env` обома мовами, `/var/log`, `@oncall`, `__init__`, `C:\Temp` і `app.config` — ідентифікатори навіть після попереднього запитання, збережені разом із з'єднувачем на початку |
| `test_a_hyphen_alone_does_not_make_an_identifier` | «Wi-Fi» лишається звичайним словом |
| `test_punctuation_that_ends_a_sentence_is_not_an_identifier` | крапка, «...», двокрапка чи «!?» після останнього слова не роблять його ідентифікатором |
| `test_a_follow_up_needs_an_earlier_question` | «And abroad?» саме по собі — `self_contained` |
| `test_a_question_in_capitals_has_no_acronyms` | запитання великими літерами — `self_contained` |
| `test_without_a_stated_language_no_word_is_foreign` | для `mixed` і `unknown` іншомовних слів немає |
| `test_a_question_with_an_identifier_is_never_rewritten` | згенеровані ідентифікатори англійською й українською, з історією і без |
| `test_every_word_to_keep_is_in_the_question_as_typed` | будь-який текст: кожне збережене слово є в запитанні, по одному разу |
| `test_a_plan_never_shows_the_words_of_the_question` | підкинута мітка зберігається, але її немає в repr |
| `test_a_question_that_cannot_be_classified_is_refused_without_quoting_it` | кожен відхилений вхід кидає `InvalidQuestion` без мітки в повідомленні |

Рядок `# ruff: noqa: RUF001` на початку дозволяє файлу містити кириличні літери, схожі на латинські, як у `tests/test_lexical_routing.py`.

Створіть `tests/test_rewrite_classes.py`:

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

Перевірка:

```bash
uv run pytest tests/test_rewrite_classes.py -q
```

Очікувано: `56 passed`.

### Крок 4 — Без коду: розмітьте перевірені випадки

Цей крок записує розмічені запитання, на яких міряються правила. Кожна мітка — рішення, яке ухвалює людина, тож прочитайте кожен випадок, перш ніж його прийняти.

Файл містить 16 seed-запитань під їхніми seed-ID, з тим самим текстом і мовою, і 17 нових. Кожен випадок має клас і, де цього вимагає клас, слова для збереження. Уточнювальне запитання ще й перелічує попередні запитання, від найстарішого.

| Нові випадки | Навіщо вони тут |
|---|---|
| `en-follow-up-abroad`, `en-follow-up-it`, `uk-follow-up-abroad`, `uk-follow-up-his` | уточнювальні запитання обома мовами: короткі або з посиланням назад |
| `en-follow-up-keeps-identifier`, `en-follow-up-number` | уточнювальні запитання, що називають `EXP-2041` або `2041`; ідентифікатор перемагає |
| `en-follow-up-error-code`, `en-follow-up-address`, `uk-path-in-question`, `en-follow-up-dotfile` | ідентифікатори без цифр: `ERR_TIMEOUT`, `hr@example.com`, `/etc/hosts` і `.env` |
| `en-new-topic-after-history` | нове повне запитання після попереднього — не уточнювальне |
| `en-acronym-sso`, `uk-acronym-pdfo`, `uk-acronym-vpn` | абревіатури обома абетками і латинська абревіатура в українському запитанні |
| `uk-cross-language-outlook`, `en-cross-language-likarnianyi`, `uk-cross-language-per-diem` | слова з іншої абетки в обидва боки |

Створіть `datasets/bilingual-seed/rewrite-classes.json`:

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

Перевірка: прочитайте кожен випадок і спитайте себе, чи згодні ви з його класом і словами для збереження. Якщо ні — змініть мітку, а не правила; крок 7 покаже, що правила тоді пропускають.

### Крок 5 — Крок із кодом: завантажте мітки і виміряйте правила

Цей крок суворо читає розмічений файл і міряє на ньому правила.

**Простими словами:** Завантажувач читає файл так само обережно, як завантажувач seed-датасету: з обмеженням розміру, суворим JSON, без повторених ключів і з фіксованою формою. Потім він проганяє кожен випадок через ті самі перевірки, які зустріне викликач, тож оцінювання вже ніколи не впаде. Кожне слово для збереження має бути цілим словом свого запитання. Оцінювач класифікує кожен випадок і рахує по кожному класу, скільки отримали правильний клас і зберегли рівно розмічені слова, не більше й не менше. Звіт називає випадки лише за ID.

| Частина | Що вона робить |
|---|---|
| `RewriteCase`, `RewriteCaseSet` | одне розмічене запитання з текстами, прихованими від repr; версіонований набір |
| `ClassAgreement` | для кожного класу: випадки, правильно призначений клас, збережені слова й ID пропущених випадків |
| `load_rewrite_cases` | файл, розмір, UTF-8 JSON, унікальні ключі, форма; далі `_case` і `_check_set` |
| `_case` | `classify_question` має прийняти випадок; кожне слово для збереження — ціле слово, указане один раз; `exact_identifier` зберігає хоча б одне слово; `follow_up` має історію |
| `_check_set` | унікальні ID; щонайменше два випадки на клас |
| `score_rewrite_cases` | один `ClassAgreement` на клас, у порядку `RewriteClass` |
| `render_rewrite_scores` | Markdown-таблиця, де «May rewrite» береться з `REWRITABLE` |

Слово для збереження має бути цілим словом, бо підрядка замало. Якби мітка зберігала `EXP`, переписування могло б зберегти `EXP` і все одно загубити `2041`.

Завантажувач не питає в правил, яке слово є ідентифікатором. Мітки — це мірило, тож вони не мають залежати від правил, які вимірюють. Натомість оцінювання вимагає точного списку: правило, яке зберегло на слово більше чи менше, пропускає випадок.

Створіть `src/knowledge_service/retrieval/rewrite_cases.py`:

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

Перевірка: ті самі три команди, що й у кроці 2, для `src/knowledge_service/retrieval/rewrite_cases.py`.

### Крок 6 — Крок із кодом: тести міток і скрипт

Цей крок тестує завантажувач і вимір і додає скрипт, що друкує результат.

**Простими словами:** Справжній розмічений файл має завантажитися, кожен клас має збігтися повністю, а 16 seed-запитань — відповідати seed-датасету. Потім маленький коректний файл ламається по одному правилу за раз, і кожна поломка має бути відхилена без цитування своєї мітки. Скрипт пише таблицю і повертає помилку, якщо якийсь випадок пропущено, тож він може охороняти пізнішу зміну правил.

| Тест | Що він доводить |
|---|---|
| `test_the_rules_agree_with_every_verified_label` | кожен клас: усі випадки отримують свій клас і зберігають свої слова |
| `test_a_label_that_keeps_fewer_words_than_the_rules_is_missed` | мітка, що містить `VPN`, але не `SSO`, пропущена, хоч клас правильний |
| `test_every_seed_question_is_labelled_as_it_is_asked` | усі 16 seed-ID розмічено з тим самим запитанням і мовою |
| `test_the_report_names_cases_by_id_only` | точні рядки, і жодного запитання чи збереженого слова у звіті |
| `test_the_valid_template_loads` | маленький коректний файл завантажується, а його repr приховує мітку |
| `test_a_file_that_breaks_a_rule_is_refused_without_quoting_it` | 15 зламаних файлів, кожен відхилено з `InvalidRewriteCases` і без мітки |
| `test_bytes_that_are_not_a_cases_file_are_refused` | поганий UTF-8, повторений ключ, `NaN` і список |
| `test_a_missing_file_is_refused` | шлях, якого не існує |

Створіть `tests/test_rewrite_cases.py`:

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

Створіть `scripts/score_rewrite_classes.py`:

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

Перевірка:

```bash
uv run pytest tests/test_rewrite_cases.py -q
uv run ruff check scripts/score_rewrite_classes.py
uv run pyright scripts/score_rewrite_classes.py
```

Очікувано: `25 passed`, `All checks passed!` і `0 errors`.

### Крок 7 — Без коду: виміряйте, зламайте і вирішіть

Цей крок запускає вимір, навмисно ламає правило ідентифікаторів і просить вас вирішити, що може переписувати M05-T15.

**Дія 1:** запустіть скрипт:

```bash
uv run python scripts/score_rewrite_classes.py; echo "exit $?"
```

Очікувано: у кожному рядку «Class assigned» і «Words kept» дорівнюють «Cases», «Missed» — `none`, а код виходу 0. Звіт іде в `artifacts/evaluations/rewrite-classes.md`, який Git ігнорує.

**Дія 2:** навмисно зламайте правило ідентифікаторів. У `src/knowledge_service/retrieval/rewrite_classes.py` замініть тіло `_is_identifier` правилом із раннього варіанта — літера і цифра:

```python
    return any(c.isdigit() for c in word) and any(c.isalpha() for c in word)
```

Потім запустіть:

```bash
uv run pytest tests/test_rewrite_classes.py tests/test_rewrite_cases.py -q
uv run python scripts/score_rewrite_classes.py; echo "exit $?"
```

Очікувано: 17 failed. Падають чотири варіанти `test_no_separator_cuts_an_identifier_into_rewritable_pieces` (`2041`, `10.0.0.1`, `#2041` і римське число), усі одинадцять варіантів `test_a_code_address_or_path_without_digits_is_an_identifier`, а також `test_the_rules_agree_with_every_verified_label` і `test_the_report_names_cases_by_id_only`. Скрипт називає пропущеними `en-follow-up-number`, `en-follow-up-error-code`, `en-follow-up-address`, `uk-path-in-question` і `en-follow-up-dotfile` і виходить із кодом 1: «And error 2041?» стало уточнювальним запитанням, яке можна переписати. Відкотіть зміну і запускайте знову, доки все не пройде.

**Дія 3:** запишіть рішення. У файлі evidence, у розділі «Decision», напишіть:

- таблицю зі звіту, бо `artifacts/` Git ігнорує;
- які класи M05-T15 може переписувати, а які ні, з однією причиною для кожного;
- обмеження, які M05-T15 має перевіряти для кожного переписування: кожне слово для збереження присутнє як ціле слово, і мова не змінилася;
- що показала навмисна поломка, з ID випадку;
- щонайменше одне **спростоване припущення** у формі «Я припускав X. Прогін показує Y (ID випадків). Отже, Z хибне».

### Крок 8 — Без коду: перевірки, документація, evidence і прогрес

Цей крок доводить, що все досі працює, оновлює текст, який зміна зробила неповним, і записує результат.

**Дія 1:** у `docs/architecture.md`:

Знайдіть у `docs/architecture.md`:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case) |
```

Замініть на:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree; nothing is rewritten yet) |
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_rewrite_classes.py tests/test_rewrite_cases.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. В еталонному прогоні вузькі тести дали 81 passed, `make test-unit` — 1538 passed, а `make test-integration` — 313 passed.

**Дія 3:** заповніть файл evidence:

- **Design:** змінені файли; п'ять класів і їхній порядок; чому ідентифікатори перемагають і чому рахується будь-яка цифра чи з'єднувач усередині слова; що таке слово; чому слова для збереження мають бути цілими словами; навіщо окремий розмічений файл; що не зачіпається (жодних збережених документів, жодних викликів моделі).
- **Verification:** команди й кількості, результат скрипту, навмисна поломка і відомі обмеження з «Перш ніж почати».
- **Review:** адверсаріальне рев'ю прототипу знайшло дві зламані гарантії і дві менші проблеми. Усі виправлено, і для кожної є тест, що падав до виправлення:
  - слова розділялися дефісами й підкресленнями, тож `EXP_2041` чи `EXP–2041` ставали абревіатурою `EXP`, яку можна переписати; тепер слова розділяють лише пробіли;
  - самі цифри, як `2041` чи `10.0.0.1`, не вважалися ідентифікаторами, тож уточнювальне запитання з ними можна було переписати; тепер рахується будь-який числовий символ;
  - розмічене запитання довше за 1000 символів завантажувалося, а потім валило скрипт; тепер завантажувач проганяє кожен випадок через `classify_question`;
  - підклас `str` чи самотній сурогат вилітали з іншим типом помилки; тепер приймається лише звичайний коректний `str`.

  Підсумкове Spec-рев'ю потім знайшло, що ідентифікатор без цифр, як-от `ERR_TIMEOUT` чи `hr@example.com`, можна було переписати, і жодне слово не зберігалося. Тепер `_`, `@`, `/`, `.`, `:` чи зворотна скісна риска всередині слова теж роблять його ідентифікатором, а оцінювання вимагає точного списку слів для збереження, а не підмножини. Друге Spec-рев'ю знайшло, що з'єднувач на початку слова, як у `.env` чи `@oncall`, обрізався ще до перевірки; тепер з'єднувач на початку і `_` у кінці зберігаються.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте завдання виконаним, додайте рядок M05-T14 у `docs/curriculum/PROGRESS.md`, встановіть наступним завданням M05-T15 і зробіть commit із повідомленням `m05-t14: classify queries for rewriting`.

## Запитання для роздумів

1. Що `classify_question` ховає від коду, який викликатиме її в M05-T15? Перелічіть, про що викликачеві ніколи не треба думати: наприклад, що таке слово чи яке правило йде першим.
2. Пізніша зміна хоче, щоб переписування допомагало більшій кількості запитань, і прибирає правило ідентифікаторів для уточнювальних запитань. Яку гарантію з вашого інваріанту це зламає, і який тест та який розмічений випадок це впіймають?
3. «And for contractors?» має чотири слова і жодного слова, що посилається назад. Що зроблять із ним правила після «How many days of leave do I get?», і що б ви змінили: правило, список слів чи мітку?
4. Яке з ваших передбачень виявилося хибним, і у що ви вірили щодо seed-запитань чи правил, що змусило вас так передбачити?
5. Перш ніж M05-T15 почне переписувати щось для справжніх користувачів, що б ви виміряли, щоб показати, що переписування класу допомагає? Назвіть одну метрику, один клас і один випадок, який може довести, що ви помиляєтеся.

## Чекліст завершення

- [ ] У файлі evidence є інваріант і ваші передбачення, записані до будь-якого запуску.
- [ ] `classify_question` повертає один із п'яти класів, `may_rewrite`, заявлену мову і слова для збереження.
- [ ] Будь-яке слово з числовим символом робить запитання `exact_identifier`, і жоден роздільник усередині слова його не розрізає.
- [ ] Погані вхідні дані кидають лише `InvalidQuestion`, і жодна помилка чи repr не цитує запитання.
- [ ] `rewrite-classes.json` розмічає всі 16 seed-запитань і 17 нових, щонайменше два на клас, кожне зі словами для збереження.
- [ ] `load_rewrite_cases` відхиляє випадок, який не змогла перевірити, не цитуючи його.
- [ ] `scripts/score_rewrite_classes.py` повідомляє, що всі мітки збіглися, і виходить із кодом 0.
- [ ] Ви виконали навмисну поломку з кроку 7, побачили 17 тестів, що впали, і п'ять пропущених випадків, серед них `en-follow-up-number`, і відкотили її.
- [ ] У файлі evidence є таблиця, які класи можна переписувати, обмеження для M05-T15 і спростоване припущення.
- [ ] Усі перевірки проходять: format, lint, types, unit, integration, docs.
- [ ] Текст архітектури, evidence, рядок у PROGRESS і commit готові.

Коли напишете файли, поділіться ними або виводом помилок, і я перевірю їх за цим чеклістом.
