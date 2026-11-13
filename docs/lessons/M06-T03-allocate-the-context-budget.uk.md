# M06-T03 — Розподіліть бюджет контексту

Англійська версія: [M06-T03-allocate-the-context-budget.md](M06-T03-allocate-the-context-budget.md)

Вихідне завдання: [M06-T03](../curriculum/milestones/M06-answers-and-conversations/M06-T03-allocate-the-context-budget.md)

## Що ви зробите

Мовна модель за один запит може прочитати й написати лише обмежений обсяг тексту. Цей урок вирішує, як ділити це місце, щоб запит ніколи його не переповнив. M06-T02 зібрав промпт, але його розмір нічим не обмежувався: 20 довгих текстів Evidence входили цілком. Тепер одна функція ділить фіксовану кількість токенів між частинами запиту, відкидає Evidence з найнижчим рангом, коли він не вміщується, і звітує, що саме відкинула.

- `allocate_context(question, evidence, budget)` повертає промпт для відправки, Evidence, які в ньому є, і звіт.
- Політику, місце для відповіді (Answer), запитання (Question) і місце для історії (History) ніколи не обрізають. Evidence отримує те, що лишилося, і обрізається першим.
- Evidence залишають або відкидають цілим, знизу порядку пошуку, тож кожна цитата досі вказує на точно той текст, який прочитала модель.
- Звіт перелічує ID кожного залишеного й відкинутого Evidence і токени кожної частини. У ньому немає тексту запитання чи документа, тож його безпечно логувати.

| Завдання вимагає | Де це відбувається |
|---|---|
| Зарезервувати токени для політики, історії, запитання, Evidence і відповіді | Крок 2: `ContextBudget` резервує місце для відповіді та історії; політику й запитання рахують у кожному промпті |
| Обрізати за явним пріоритетом | Крок 2: фіксований порядок п'яти частин; обрізається лише Evidence, починаючи з найнижчого рангу |
| Зберегти походження (provenance) | Крок 2: залишені Evidence — ті самі об'єкти з тими самими ID; відкинутих немає ні в промпті, ні в схемі, ні в наборі |
| Бюджет ніколи не перевищено, рішення про обрізання у звіті | Крок 3: тест точної межі, властивісний тест на випадкових Evidence і перевірки `ContextReport` |
| Детерміновані тести: успіх, найризиковіший край, типізована помилка | Крок 3: усе вміщується; точна межа; `ContextOverBudget` і `InvalidContextBudget` |
| Жодного підкинутого тексту в телеметрії | Крок 3: підкинутий маркер ніколи не з'являється у звіті, repr чи помилці |

Цей урок містить код: кроки 2 і 3 пишуть код. Кроки 1, 4 і 5 — без коду. Ніщо не звертається до моделі чи бази даних.

Терміни цього уроку:

- **Токен (token):** одиниця, якою модель рахує текст, приблизно коротке слово або його частина. Ціна і ліміти моделей задаються в токенах.
- **Контекстне вікно (context window):** найбільша кількість токенів в одному запиті, разом і те, що надсилають, і те, що модель пише у відповідь. Модель із вікном 128 000 токенів не прочитає 130 000.
- **Бюджет контексту (context budget):** як запит ділить своє вікно між частинами, як сімейний бюджет, що спершу платить за житло і їжу, а потім витрачає решту.
- **Резерв (reserve):** токени, відкладені для чогось наперед, до будь-яких інших витрат, наприклад місце для відповіді.
- **Голова списку (head):** його перші елементи, по порядку. «Найдовша голова, що вміщується» означає: беремо елементи 1, 2, 3 і так далі та зупиняємось на першому, що не вміщується.
- **Нижня межа (lower bound):** число, яке ніколи не більше за справжнє значення. Якщо навіть нижня межа завелика, то й справжнє значення теж.

## Перед початком

Цей розділ пояснює рішення, на яких тримається код, щоб кожен крок був зрозумілим, коли ви до нього дійдете.

- **Що вже є.** `answering/evidence.py` дає `EvidenceSet`: щонайбільше 20 Evidence у порядку пошуку, кожен із випадковим ID. `answering/prompt.py` збирає фіксовану політику, запитання, Evidence і схему відповіді. Розмір цього промпта поки нічим не обмежено.
- **Як рахують токени.** У проєкті немає завантаженого токенізатора. Адаптери OpenAI для ембедингів і переранжування вже рахують запит як його байти UTF-8: токен займає щонайменше один байт, тож цей підрахунок ніколи не буває замалим. `count_prompt_tokens` рахує інструкції, вхідні дані й схему, плюс 64 токени на обгортку самого запиту, той самий запас, що й у переранжувальника з M05-T12.
- **Рахуйте те, що надсилаєте.** Функція рахує промпт, який справді зібрала, і повертає саме його. M06-T04 надішле його без змін, тож перевірене число — це й надіслане число.
- **Порядок пріоритетів.** Політика і схема мусять бути, інакше модель не має правил. Відповіді потрібне місце, інакше її обріже. Запитання має бути цілим, інакше модель відповість на інше запитання. Історія — це фіксоване місце, яке M06-T11 заповнить попередніми репліками. Evidence — єдина частина з природним порядком для обрізання: пошук його вже впорядкував.
- **Цілими елементами, знизу.** Якби текст Evidence обрізали навпіл, його ID вказував би на текст, якого модель не читала. Тому Evidence залишають цілим або відкидають цілим. Залишені Evidence — це найдовша голова порядку пошуку, що вміщується. Маленький Evidence під відкинутим великим теж відкидається: порядок і є пріоритетом, а дірку в ньому важче пояснити.
- **Немає місця навіть для запитання.** Коли політика, запитання й обидва резерви вже більші за вікно, запит відхиляється з `ContextOverBudget`, у якому два числа і жодного тексту. Коли запитання вміщується, а жоден Evidence — ні, промпт іде без Evidence; M06-T07 навчить модель тоді утримуватися від відповіді.
- **Обмежена робота.** За цим підрахунком текст коштує щонайменше один токен на символ. Тому функція спершу додає довжини, що нічого не коштує, і ніколи не читає, не копіює і не збирає Evidence, який не вміщується навіть за довжиною. Потім вона збирає всю голову, що могла б уміститися; якщо та завелика, пошук ділить навпіл.
- **Що знайшло рев'ю.** Адверсаріальне рев'ю не змогло вивести промпт за бюджет, залишити не ті Evidence чи пропустити підкинутий маркер. Воно знайшло одну зламану гарантію: перша версія кодувала кожен текст Evidence повністю, щоб його виміряти, тож текст на 10 МБ коштував 10 МБ пам'яті навіть за крихітного бюджету. Тепер додаються лише довжини, а регресійний тест вимірює пік пам'яті.
- **Де живе код.** `answering/context_budget.py`, поруч із промптом, який він збирає.
- **Відомі обмеження.** Підрахунок у байтах завищує: англійський текст — це приблизно чотири байти на справжній токен, а кожна українська літера — два байти, тож той самий бюджет вміщує приблизно вдвічі менше українського тексту, ніж англійського. Історію зарезервовано, але до M06-T11 вона порожня. Екранування обробляє по одному символу, це приблизно 50–70 мс для 20 реалістичних шматків (Chunk) і секунди для бюджетів у мільйони. Виклик `count_prompt_tokens` не з промптом піднімає звичайний для Python `AttributeError`.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої передбачення

Поки коду ще немає, запишіть, що має бути правдою, коли урок завершено. Крок 4 перевіряє передбачення.

Створіть `docs/evidence/M06-T03-context-budget.md`:

```markdown
# M06-T03 — Context budget evidence

## Invariant

`allocate_context` returns a prompt whose counted tokens, plus the Answer
and History reserves, never exceed the context window, and the prompt
returned is the one that was counted. The policy, schema, and Question are
never trimmed. Evidence is kept whole, in retrieval's order, as the longest
head that fits; the rest is dropped and appears in neither the prompt, the
schema, nor the returned set. The report names every kept and dropped
Evidence ID with token counts and holds no Question or Document text. When
even a prompt without Evidence does not fit, `ContextOverBudget` is raised
with counts only.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.context_budget"`
fails with `ModuleNotFoundError`.

## Predictions

- How many of 20 Chunks that repeat the word "days" 256 times fit in a 16,000-token window with 1,000 for the Answer and 2,000 for History: ...
- The same for the Ukrainian word "днів", also four letters, and why the number differs: ...
- How many tests the deliberate break in Step 4 fails, and which ones: ...

## Design

## Verification

## Decision

## Reflection
```

Перевірка: файл існує, перевірка, що має впасти, падає, а кожне передбачення — повне речення, яке крок 4 може спростувати.

### Крок 2 — Крок із кодом: розподіліть бюджет

Цей крок пише одну функцію, яка вирішує, що потрапить у запит, перш ніж його побачить модель.

**Простими словами:** Код бере вікно моделі й віднімає місце, відкладене для відповіді та для попередніх реплік. Він збирає промпт без Evidence і відхиляє запит, якщо не вміщується навіть він. Потім додає довжини Evidence, починаючи з найвищого рангу, щоб побачити, скільки їх узагалі може вміститися, і збирає промпт із такою кількістю. Якщо він завеликий, пробує менше, доки не знайде найбільшу кількість, що вміщується. Повертає цей промпт, Evidence у ньому і звіт про те, що залишено й відкинуто.

| Частина | Що містить чи робить |
|---|---|
| `ContextBudget` | Вікно і два резерви; відхиляє не цілі числа, резерв нижче за мінімум і резерви, що займають усе вікно |
| `ContextReport` | ID залишених і відкинутих Evidence, токени промпта без Evidence і те, що додали Evidence; лише числа й непрозорі ID |
| `AllocatedContext` | Промпт для відправки, його Evidence як новий `EvidenceSet` і звіт |
| `count_prompt_tokens` | Байти UTF-8 інструкцій, вхідних даних і схеми, плюс 64 |
| `allocate_context` | Перевіряє, збирає промпт без Evidence, знаходить за довжиною голову, що могла б уміститися, а потім збиранням — ту, що вміщується |
| `ContextOverBudget` | Піднімається, коли не вміщується навіть промпт без Evidence; містить `needed` і `available` |

Створіть `src/knowledge_service/answering/context_budget.py`:

```python
"""The context budget: what one Answer's prompt may spend, and what it drops.

A chat model reads and writes within one context window, counted in
tokens. `allocate_context` splits a `ContextBudget` between five parts and
renders the prompt (see `answering.prompt`) that fits it.

The parts, by priority:

1. **Policy**: the fixed instructions and the response schema. Never trimmed.
2. **Answer**: `answer_tokens`, reserved for what the model writes. Never trimmed.
3. **Question**: the user's Question, whole. Never trimmed.
4. **History**: `history_tokens`, reserved for earlier turns of the
   conversation. M06-T11 fills it; this module only keeps the room free.
5. **Evidence**: whatever is left. Trimmed first.

What an allocation promises:

- **Never over budget.** The counted tokens of the prompt actually
  returned, plus the Answer and History reserves, never exceed
  `context_tokens`. The returned prompt is the one that was counted.
- **Trimmed by rank, whole items only.** Evidence keeps retrieval's order,
  and the kept Evidence is the longest head of that order that fits. An
  Evidence is kept whole or dropped whole; its text is never cut, so a
  Citation always points at the exact text the model read.
- **Provenance preserved.** Kept Evidence is the same objects, with the
  same opaque IDs and provenance, in a new `EvidenceSet`. Nothing is
  added, and dropped Evidence cannot be cited: it is neither in the
  prompt, nor in the schema, nor in the returned set.
- **Decisions reported.** `ContextReport` names every kept and dropped
  Evidence ID and the tokens of each part. It holds counts and opaque IDs
  only, so it is safe to log.
- **A safe count.** Tokens are counted as UTF-8 bytes plus a fixed
  framing allowance, the project's upper bound for OpenAI tokenizers: a
  token is at least one byte, so a prompt never has more tokens than this
  count says.
- **Bounded work.** By this count a text costs at least one token per
  character, so Evidence whose length alone cannot fit is never read,
  copied, or rendered. The head that fits is found in a few renders, one
  when it all fits, so time and memory grow with the budget, not with the
  Evidence's size.
- **A typed denial.** When the policy, the Question, and the two reserves
  leave no room even for a prompt without Evidence, `ContextOverBudget` is
  raised; it holds token counts only. A bad budget raises
  `InvalidContextBudget`. A bad Question raises the prompt's
  `InvalidPrompt`, and so does Evidence that is not valid Unicode, when it
  is inside the head that might fit; Evidence beyond it is never read.
"""

import re
from dataclasses import dataclass

from knowledge_service.answering.evidence import EvidenceId, EvidenceSet
from knowledge_service.answering.prompt import (
    EvidencePrompt,
    InvalidPrompt,
    render_prompt,
)

# What a chat request adds around the three texts: roles, message
# boundaries, the schema's wrapper. The same allowance as M05-T12's.
FRAMING_TOKENS = 64
# A lone surrogate is not valid Unicode; found without copying the text.
_SURROGATE = re.compile(r"[\ud800-\udfff]")


class InvalidContextBudget(ValueError):
    """A context budget's numbers are not usable."""


class ContextOverBudget(Exception):
    """The policy, Question, and reserves alone exceed the context window.

    Holds token counts only, never the Question.
    """

    def __init__(self, needed: int, available: int) -> None:
        super().__init__(
            f"the prompt needs {needed} tokens without Evidence,"
            f" but only {available} are available"
        )
        self.needed = needed
        self.available = available


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """How many tokens one request may use, and how many are set aside.

    `context_tokens` is the model's whole window. `answer_tokens` is
    reserved for the Answer and `history_tokens` for earlier turns; the
    prompt gets the rest.
    """

    context_tokens: int
    answer_tokens: int
    history_tokens: int

    def __post_init__(self) -> None:
        values = (self.context_tokens, self.answer_tokens, self.history_tokens)
        # bool is an int, so it is refused by name.
        if not all(type(value) is int for value in values):
            raise InvalidContextBudget("a context budget holds whole numbers")
        if self.answer_tokens < 1 or self.history_tokens < 0:
            raise InvalidContextBudget(
                "the Answer needs at least one token and History cannot be negative"
            )
        if self.answer_tokens + self.history_tokens >= self.context_tokens:
            raise InvalidContextBudget("the reserves leave no room for a prompt")

    @property
    def prompt_tokens(self) -> int:
        """What the prompt may spend: the window minus both reserves."""
        return self.context_tokens - self.answer_tokens - self.history_tokens


@dataclass(frozen=True, slots=True)
class ContextReport:
    """How the budget was spent, and which Evidence was kept or dropped.

    `base_tokens` is the prompt without Evidence: policy, schema, and
    Question. `evidence_tokens` is what the kept Evidence added to it.
    """

    context_tokens: int
    answer_tokens: int
    history_tokens: int
    base_tokens: int
    evidence_tokens: int
    kept: tuple[EvidenceId, ...]
    dropped: tuple[EvidenceId, ...]

    @property
    def prompt_tokens(self) -> int:
        """The counted tokens of the prompt that is sent."""
        return self.base_tokens + self.evidence_tokens

    @property
    def unused_tokens(self) -> int:
        """The part of the window nobody uses; never negative."""
        return (
            self.context_tokens
            - self.answer_tokens
            - self.history_tokens
            - self.prompt_tokens
        )


@dataclass(frozen=True, slots=True)
class AllocatedContext:
    """The prompt to send, the Evidence it holds, and the report."""

    prompt: EvidencePrompt
    evidence: EvidenceSet
    report: ContextReport


def count_prompt_tokens(prompt: EvidencePrompt) -> int:
    """An upper bound on a prompt's tokens: its UTF-8 bytes plus framing."""
    parts = (prompt.instructions, prompt.input, prompt.schema)
    return sum(len(part.encode("utf-8")) for part in parts) + FRAMING_TOKENS


def allocate_context(
    question: str, evidence: EvidenceSet, budget: ContextBudget
) -> AllocatedContext:
    """Render the prompt with the longest head of Evidence the budget allows."""
    if type(budget) is not ContextBudget:
        raise InvalidContextBudget("budget must be a ContextBudget")
    if type(evidence) is not EvidenceSet:
        raise InvalidPrompt("evidence must be an EvidenceSet")
    available = budget.prompt_tokens
    base_prompt = render_prompt(question, EvidenceSet(()))
    base = count_prompt_tokens(base_prompt)
    if base > available:
        raise ContextOverBudget(base, available)
    # Rendering never shrinks a text, and every character is at least one
    # byte, so Evidence whose length alone does not fit can never fit.
    limit = 0
    spent = base
    for item in evidence.items:
        texts = (item.title, item.heading_path or "", item.text)
        spent += sum(len(text) for text in texts)
        if spent > available:
            break
        if any(_SURROGATE.search(text) for text in texts):
            raise InvalidPrompt("an Evidence text is not valid Unicode")
        limit += 1
    # Fewer Evidence never costs more, so the longest head that fits is
    # found by halving. The whole head is tried first: it usually fits.
    best, best_tokens, kept = base_prompt, base, 0
    low, high = 1, limit
    middle = limit
    while low <= high:
        prompt = render_prompt(question, EvidenceSet(evidence.items[:middle]))
        tokens = count_prompt_tokens(prompt)
        if tokens <= available:
            best, best_tokens, kept = prompt, tokens, middle
            low = middle + 1
        else:
            high = middle - 1
        middle = (low + high) // 2
    items = evidence.items
    return AllocatedContext(
        prompt=best,
        evidence=EvidenceSet(items[:kept]),
        report=ContextReport(
            context_tokens=budget.context_tokens,
            answer_tokens=budget.answer_tokens,
            history_tokens=budget.history_tokens,
            base_tokens=base,
            evidence_tokens=best_tokens - base,
            kept=tuple(item.evidence_id for item in items[:kept]),
            dropped=tuple(item.evidence_id for item in items[kept:]),
        ),
    )
```

Тут важливі три деталі. Цикл довжин також перевіряє кожен текст, через який проходить, на самотній сурогат, тобто зламану половину символу, тож поганий Evidence відхиляється однаково, хоч які збирання випадково спробує пошук. Пошук починає з усієї голови, бо в більшості запитів усе вміщується і вистачає одного збирання. Якщо нічого не вміщується, `kept` лишається 0, тож повертається базовий промпт, тобто промпт без Evidence.

Перевірка:

```bash
uv run python -c "import knowledge_service.answering.context_budget"
uv run pyright src/knowledge_service/answering/context_budget.py
```

Очікувано: імпорт нічого не друкує, а pyright показує 0 помилок.

### Крок 3 — Крок із кодом: тести межі, властивостей і відмови

Цей крок доводить, що бюджет ніколи не перевищується і що кожне рішення про обрізання видно у звіті.

**Простими словами:** Тести будують Evidence зі спільних фейкових кандидатів і дають кожному випадку бюджет, виміряний зі справжнього промпта, тож усе вирішує один токен. Вони перевіряють, що все залишається, коли вміщується, що на один токен менше відкидає рівно один Evidence і що великий Evidence посередині відкидає все під собою. Властивісний тест пробує 150 випадкових наборів Evidence і бюджетів. Останні тести перевіряють відмови, те, що жоден підкинутий текст не потрапляє у звіт, і те, що величезний Evidence ніколи не збирається і не копіюється.

| Тест | Що доводить |
|---|---|
| `test_evidence_that_fits_is_all_kept` | Успіх: усе залишено, промпт збігається зі збиранням M06-T02, числа звіту сходяться |
| `test_the_budget_boundary_is_exact` | Найризиковіший край: рівно за ціною двох елементів залишаються два; на токен менше — один |
| `test_trimming_drops_from_the_bottom_and_keeps_provenance` | Залишаються ті самі об'єкти; відкинутих ID немає ні у вхідних даних, ні в схемі |
| `test_when_no_evidence_fits_the_prompt_has_none` | Нічого не вміщується: промпт без Evidence, усе у звіті як відкинуте |
| `test_the_budget_is_never_exceeded_and_the_kept_head_is_the_longest` | Випадкові Evidence і бюджети: ніколи не більше, і ще один елемент не вмістився б |
| `test_a_question_too_long_for_the_window_is_denied_with_counts_only` | Типізована відмова без тексту запитання в повідомленні |
| `test_an_unusable_budget_is_refused` і перевірка типу | Погані бюджети піднімають `InvalidContextBudget` |
| `test_invalid_evidence_that_might_fit_is_refused` | Поганий Evidence, який пошук ніколи не збирає, однаково відхиляється |
| `test_evidence_beyond_what_might_fit_is_never_read` | Evidence, задовгий, щоб уміститися, відкидається без читання |
| `test_the_report_and_reprs_hold_no_question_or_document_text` | Жодного підкинутого маркера в repr чи звіті |
| `test_evidence_too_large_to_fit_is_never_rendered` і `..._never_copied` | Робота росте з бюджетом, а не з Evidence (регресія з рев'ю) |

Створіть `tests/test_context_budget.py`:

```python
"""The context budget: never exceeded, trimmed by rank, decisions reported."""

import tracemalloc
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from evidence_fakes import MARKER, candidate
from knowledge_service.answering import context_budget
from knowledge_service.answering.context_budget import (
    ContextBudget,
    ContextOverBudget,
    InvalidContextBudget,
    allocate_context,
    count_prompt_tokens,
)
from knowledge_service.answering.evidence import EvidenceSet, build_evidence
from knowledge_service.answering.prompt import (
    EvidencePrompt,
    InvalidPrompt,
    render_prompt,
)

QUESTION = "How many days of annual leave do I get?"
ANSWER, HISTORY = 500, 300


def evidence(*texts: str) -> EvidenceSet:
    return build_evidence(
        [candidate(number, text=text) for number, text in enumerate(texts, 1)]
    )


def cost(items: EvidenceSet, count: int) -> int:
    """The counted tokens of the prompt holding the first `count` items."""
    return count_prompt_tokens(
        render_prompt(QUESTION, EvidenceSet(items.items[:count]))
    )


def budget_for(prompt_tokens: int) -> ContextBudget:
    """A budget that leaves exactly `prompt_tokens` for the prompt."""
    return ContextBudget(
        context_tokens=prompt_tokens + ANSWER + HISTORY,
        answer_tokens=ANSWER,
        history_tokens=HISTORY,
    )


def test_evidence_that_fits_is_all_kept() -> None:
    given_evidence = evidence("Leave is 24 days.", "Carry-over is 5 days.")

    allocated = allocate_context(QUESTION, given_evidence, budget_for(100_000))

    assert allocated.evidence == given_evidence
    assert allocated.prompt.input == render_prompt(QUESTION, given_evidence).input
    report = allocated.report
    assert report.kept == tuple(item.evidence_id for item in given_evidence.items)
    assert report.dropped == ()
    assert report.prompt_tokens == count_prompt_tokens(allocated.prompt)
    assert report.base_tokens == cost(given_evidence, 0)
    assert report.unused_tokens == 100_000 - report.prompt_tokens


@pytest.mark.parametrize(("spare", "kept"), [(0, 2), (-1, 1)])
def test_the_budget_boundary_is_exact(spare: int, kept: int) -> None:
    given_evidence = evidence("Leave is 24 days.", "Carry-over is 5 days.", "x" * 50)

    allocated = allocate_context(
        QUESTION, given_evidence, budget_for(cost(given_evidence, 2) + spare)
    )

    assert allocated.evidence.items == given_evidence.items[:kept]
    assert allocated.report.dropped == tuple(
        item.evidence_id for item in given_evidence.items[kept:]
    )


def test_trimming_drops_from_the_bottom_and_keeps_provenance() -> None:
    given_evidence = evidence("Leave is 24 days.", "y" * 5_000, "Carry-over is 5 days.")
    first, large, small = given_evidence.items

    # Room for the first and the small one, but not for the large second.
    allocated = allocate_context(
        QUESTION, given_evidence, budget_for(cost(given_evidence, 1) + 200)
    )

    assert allocated.evidence.items == (first,)
    assert allocated.evidence.items[0] is first
    assert allocated.report.dropped == (large.evidence_id, small.evidence_id)
    for dropped in (large, small):
        assert dropped.evidence_id.value not in allocated.prompt.input
        assert dropped.evidence_id.value not in allocated.prompt.schema


def test_when_no_evidence_fits_the_prompt_has_none() -> None:
    given_evidence = evidence("z" * 2_000)

    allocated = allocate_context(
        QUESTION, given_evidence, budget_for(cost(given_evidence, 0) + 10)
    )

    assert allocated.evidence.items == ()
    assert allocated.prompt == render_prompt(QUESTION, EvidenceSet(()))
    assert allocated.report.kept == ()
    assert allocated.report.dropped == (given_evidence.items[0].evidence_id,)
    assert allocated.report.evidence_tokens == 0


@given(
    texts=st.lists(st.text(min_size=1, max_size=400), max_size=20),
    room=st.integers(min_value=0, max_value=12_000),
)
@settings(derandomize=True, max_examples=150, deadline=None)
def test_the_budget_is_never_exceeded_and_the_kept_head_is_the_longest(
    texts: list[str], room: int
) -> None:
    texts = [f"{text}." for text in texts]  # an Evidence text is never blank
    given_evidence = evidence(*texts)
    budget = budget_for(cost(given_evidence, 0) + room)

    allocated = allocate_context(QUESTION, given_evidence, budget)

    tokens = count_prompt_tokens(allocated.prompt)
    assert (
        tokens + budget.answer_tokens + budget.history_tokens <= budget.context_tokens
    )
    assert tokens == allocated.report.prompt_tokens
    assert allocated.report.unused_tokens >= 0
    kept = len(allocated.evidence.items)
    assert allocated.evidence.items == given_evidence.items[:kept]
    assert allocated.report.kept + allocated.report.dropped == tuple(
        item.evidence_id for item in given_evidence.items
    )
    assert allocated.prompt == render_prompt(QUESTION, allocated.evidence)
    if kept < len(given_evidence.items):
        assert cost(given_evidence, kept + 1) > budget.prompt_tokens


def test_a_question_too_long_for_the_window_is_denied_with_counts_only() -> None:
    question = MARKER + " " + "leave " * 600
    needed = count_prompt_tokens(render_prompt(question, EvidenceSet(())))

    with pytest.raises(ContextOverBudget) as raised:
        allocate_context(
            question, evidence("Leave is 24 days."), budget_for(needed - 1)
        )

    assert raised.value.needed == needed
    assert raised.value.available == needed - 1
    assert MARKER not in str(raised.value)


@pytest.mark.parametrize(
    "numbers",
    [
        (1_000, 0, 0),
        (1_000, 500, -1),
        (1_000, 600, 400),
        (1_000.0, 500, 100),
        (1_000, True, 100),
    ],
)
def test_an_unusable_budget_is_refused(numbers: tuple[Any, Any, Any]) -> None:
    with pytest.raises(InvalidContextBudget):
        ContextBudget(*numbers)


def test_a_budget_that_is_not_a_context_budget_is_refused() -> None:
    with pytest.raises(InvalidContextBudget):
        allocate_context(QUESTION, evidence("Leave."), object())  # type: ignore[arg-type]


def test_invalid_evidence_that_might_fit_is_refused() -> None:
    # The second is short enough to be read but too long once each NUL is
    # escaped, so the third is never rendered; it is still checked.
    given_evidence = evidence("Leave is 24 days.", chr(0) * 40, "ok" + chr(0xD800))

    with pytest.raises(InvalidPrompt):
        allocate_context(
            QUESTION, given_evidence, budget_for(cost(given_evidence, 1) + 120)
        )


def test_evidence_beyond_what_might_fit_is_never_read() -> None:
    given_evidence = evidence("Leave is 24 days.", "broken " + chr(0xD800) * 3_000)

    allocated = allocate_context(
        QUESTION, given_evidence, budget_for(cost(given_evidence, 1) + 10)
    )

    assert allocated.report.dropped == (given_evidence.items[1].evidence_id,)


def test_the_report_and_reprs_hold_no_question_or_document_text() -> None:
    given_evidence = evidence(f"{MARKER} leave", f"{MARKER} " + "w" * 3_000)

    allocated = allocate_context(
        f"{MARKER}?", given_evidence, budget_for(cost(given_evidence, 0) + 400)
    )

    assert allocated.report.dropped  # the large one was trimmed
    for shown in (repr(allocated), repr(allocated.report), str(allocated.report)):
        assert MARKER not in shown


def test_evidence_too_large_to_fit_is_never_rendered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    huge = "\u200b" * 2_000_000  # escaping makes every character six bytes
    given_evidence = evidence(*["Leave is 24 days."] * 19, huge)
    rendered: list[int] = []

    def counting(question: str, items: EvidenceSet) -> EvidencePrompt:
        rendered.append(sum(len(item.text) for item in items.items))
        return render_prompt(question, items)

    monkeypatch.setattr(context_budget, "render_prompt", counting)

    allocated = allocate_context(QUESTION, given_evidence, budget_for(20_000))

    assert len(allocated.evidence.items) == 19
    assert len(rendered) == 2  # no Evidence, then the head that might fit
    assert max(rendered) < 1_000


def test_evidence_too_large_to_fit_is_never_copied() -> None:
    huge = "Я" * 5_000_000  # 10 MB as UTF-8
    given_evidence = evidence("Leave is 24 days.", huge)

    tracemalloc.start()
    try:
        allocated = allocate_context(QUESTION, given_evidence, budget_for(20_000))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(allocated.evidence.items) == 1
    assert peak < 1_000_000
```

`cost` збирає справжній промпт із кількома першими елементами і рахує його, тож `budget_for(cost(given_evidence, 2))` — це бюджет без жодного зайвого токена для двох елементів. `MARKER` і `candidate` беруться з `tests/evidence_fakes.py`, який додав M06-T01.

Перевірка:

```bash
uv run pytest tests/test_context_budget.py -q
```

Очікувано: 18 passed.

### Крок 4 — Без коду: зламайте і вирішіть

Цей крок показує, які тести охороняють резерви, і фіксує, чого ви навчилися.

**Дія 1:** навмисно зламайте бюджет: забудьте резерв для історії. У `src/knowledge_service/answering/context_budget.py`, у `ContextBudget.prompt_tokens`, замініть:

```python
        return self.context_tokens - self.answer_tokens - self.history_tokens
```

на:

```python
        return self.context_tokens - self.answer_tokens
```

Запустіть:

```bash
uv run pytest tests/test_context_budget.py -q
```

Очікувано: 4 failed: обидва випадки `test_the_budget_boundary_is_exact`, `test_the_budget_is_never_exceeded_and_the_kept_head_is_the_longest` і `test_a_question_too_long_for_the_window_is_denied_with_counts_only`. Коли місце для історії забуте, промпт розростається в нього, тож вікно переповниться, щойно M06-T11 заповнить історію. Скасуйте зміну і запускайте знову, доки все не пройде.

**Дія 2:** перевірте свої два передбачення про розмір на справжніх числах. Запустіть:

```bash
uv run python - <<'EOF'
import sys
sys.path.insert(0, "tests")
from evidence_fakes import candidate
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.context_budget import ContextBudget, allocate_context

budget = ContextBudget(16_000, 1_000, 2_000)
for word in ("days", "днів"):
    evidence = build_evidence(
        [candidate(number, text=" ".join([word] * 256)) for number in range(1, 21)]
    )
    report = allocate_context("How many days?", evidence, budget).report
    print(word, len(report.kept), "kept,", len(report.dropped), "dropped,", report.prompt_tokens, "tokens")
EOF
```

Очікувано: `days 8 kept, 12 dropped, 12856 tokens` і `днів 4 kept, 16 dropped, 11196 tokens`. Обидва слова мають чотири літери, але кожна українська літера — два байти, тож вміщується вдвічі менше шматків.

**Дія 3:** під `## Decision` напишіть:

- чому порядок саме такий: політика, відповідь, запитання, історія, потім Evidence, і що пішло б не так, якби історію обрізали раніше за Evidence;
- чому Evidence відкидають цілим і ніколи не обрізають;
- чого підрахунок у байтах коштує українським користувачам і що змінив би справжній токенізатор;
- що лишається на наступні уроки: надсилання промпта (M06-T04), утримання від відповіді, коли не вміщується жоден Evidence (M06-T07), заповнення історії (M06-T11);
- щонайменше одне **спростоване припущення** у формі «Я припускав X. Запуск чи рев'ю показує Y. Отже, Z хибне».

### Крок 5 — Без коду: архітектура, перевірки, evidence і прогрес

Цей крок оновлює текст, який зміна зробила неповним, доводить, що все досі працює, і фіксує результат.

**Дія 1:** у `docs/architecture.md` додайте нову функцію до рядка `answering`.

Знайдіть у `docs/architecture.md`:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, repeated, malformed, or mismatched references are refused, and a Citation's provenance always comes from the server), and `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs) |
```

Замініть на:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, repeated, malformed, or mismatched references are refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), and `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts) |
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_context_budget.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний запуск дав 18 passed для вузьких тестів і 1807 passed для `make test-unit`. Цей урок не чіпає коду бази даних, тож `make test-integration` має дати ті самі 329 passed, що й у M06-T02.

**Дія 3:** заповніть файл evidence:

- **Design:** змінені файли; п'ять частин і їхній порядок; як рахують токени і чому; обрізання цілими елементами; поля звіту.
- **Verification:** команди і числа, навмисна поломка, запуск розмірів із кроку 4 і відомі обмеження з розділу «Перед початком».
- **Review:** адверсаріальне рев'ю не змогло перевищити бюджет, залишити не ті Evidence чи пропустити маркер на 3000 випадках фазингу. Воно знайшло одну зламану гарантію, виправлену тестами, що падали до виправлення: кожен текст Evidence кодувався повністю, щоб його виміряти, тож час і пам'ять росли з Evidence, а не з бюджетом (1,56 с і 150 МБ для 20 текстів по 50 мільйонів символів, тепер 0 с і 0 МБ).
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте завдання як виконане, додайте рядок M06-T03 у `docs/curriculum/PROGRESS.md`, укажіть наступним завданням M06-T04 і зробіть коміт `m06-t03: allocate the context budget`.

## Запитання для рефлексії

1. Що `allocate_context` приховує від коду, який надсилатиме промпт у M06-T04? Перелічіть, про що тому коду ніколи не треба думати.
2. Пізніша зміна хоче пришвидшити роботу: порахувати кожен Evidence один раз і додати числа замість збирання промпта. Яку обіцянку це могло б зламати і який тест це спіймає?
3. Підрахунок у байтах каже, що український промпт приблизно вдвічі більший за англійський із тією самою кількістю літер. Хто за це платить і як перевірити, наскільки це далеко від справжньої кількості токенів?
4. Коли не вміщується жоден Evidence, промпт іде без них, і модель має утриматися від відповіді. Чи це правильний результат для користувача, чи запит слід відхиляти, як запитання, що не вміщується? Чому?
5. Який вхід ви спробували б наступним, щоб довести, що бюджет усе ж можна перевищити?

## Чекліст завершення

- [ ] Файл evidence містить інваріант і ваші передбачення, записані до коду.
- [ ] `ContextBudget` відхиляє не цілі числа, резерв нижче за мінімум і резерви, що займають усе вікно.
- [ ] Пораховані токени поверненого промпта плюс обидва резерви ніколи не перевищують вікна, а повернений промпт — саме той, що порахований.
- [ ] Evidence залишаються цілими, у порядку пошуку, як найдовша голова, що вміщується; відкинутих немає ні в промпті, ні в схемі, ні в наборі.
- [ ] Звіт перелічує ID залишених і відкинутих та кількість токенів і не містить тексту запитання чи документа.
- [ ] Запитання, що не вміщується, піднімає `ContextOverBudget` лише з числами.
- [ ] Evidence, задовгий, щоб уміститися, ніколи не читається, не копіюється і не збирається.
- [ ] Ви запустили навмисну поломку, побачили 4 failed і скасували її.
- [ ] Усі перевірки проходять: формат, лінтер, типи, unit, інтеграційні, документація.
- [ ] Evidence, статус завдання, рядок PROGRESS і коміт готові.

Коли напишете файли, поділіться ними або виводом помилок, і я перевірю їх за цим чеклістом.
