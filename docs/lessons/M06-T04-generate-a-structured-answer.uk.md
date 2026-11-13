# M06-T04 — Згенеруйте структуровану відповідь

Англійська версія: [M06-T04-generate-a-structured-answer.md](M06-T04-generate-a-structured-answer.md)

Вихідне завдання: [M06-T04](../curriculum/milestones/M06-answers-and-conversations/M06-T04-generate-a-structured-answer.md)

## Що ви зробите

Цей урок робить перший справжній виклик, який перетворює запитання (Question) та його Evidence на відповідь (Answer). M06-T02 зібрав промпт, а M06-T03 вмістив його у вікно моделі. Тепер промпт іде до мовної моделі, а сервер перевіряє її відповідь, перш ніж її хтось побачить. Моделі ніколи не довіряють: її відповідь має відповідати схемі, цитувати лише показані їй Evidence і або відповідати, або пояснювати, чому не може.

- `generate_answer(question, evidence, budget, model, ...)` вміщує контекст, один раз викликає модель і повертає один перевірений результат.
- `AnswerModel` — це порт, вузький інтерфейс, який реалізує будь-який адаптер мовної моделі. `OpenAIAnswerModel` — адаптер для OpenAI.
- Кожна відповідь моделі стає рівно одним результатом: відповідь, утримання, відмова або невалідна відповідь. Збої провайдера піднімають ті самі типізовані помилки, що й порт `ChatModel` з M01.
- Коли не вміщується жоден Evidence, модель узагалі не викликається.

| Завдання вимагає | Де це відбувається |
|---|---|
| Запросити текст відповіді, ID цитат, стан утримання і причину | Крок 3: `generate_answer` надсилає промпт і схему з M06-T02; крок 4: адаптер OpenAI надсилає їх зі Structured Outputs |
| Через порт ChatModel | Кроки 2 і 3: вузький порт `AnswerModel`, що піднімає помилки сімейства `ChatModelError` з M01 |
| Безпечно перекладати помилки валідації | Крок 3: `checked_answer` перетворює кожну зламану відповідь на `INVALID` з названим дефектом і ніколи не цитує модель |
| Тести: підтримана відповідь, відмова, невалідна схема, збій провайдера | Крок 5: по тесту на кожне, плюс 26 зламаних відповідей і фазинг-тест |
| Детерміновані тести: успіх, найризиковіший край, типізована помилка | Крок 5: відповідь; цитата Evidence, відкинутого бюджетом; `ChatModelTimeout` і `InvalidAnswerRequest` |
| Жодного підкинутого тексту в телеметрії | Крок 5: підкинутий маркер ніколи не потрапляє в запис логу, repr чи помилку |

Цей урок містить код: кроки 2–5 пишуть код. Кроки 1, 6 і 7 — без коду. Справжня модель не викликається, хіба що ви запустите необов'язковий живий тест.

Терміни цього уроку:

- **Порт і адаптер (port, adapter):** порт — це інтерфейс, з яким говорить застосунок, як розетка. Адаптер підключає до нього одного провайдера, як вилка. Тести використовують фейковий адаптер, тож ніколи не ходять у мережу.
- **Structured Outputs:** функція OpenAI, яка змушує відповідь моделі відповідати JSON-схемі, яку ви надсилаєте. Суворий режим (strict) означає, що кожне поле обов'язкове і жодне інше поле не може з'явитися.
- **Утримання (abstention):** чесне «я не можу відповісти на це з документів» від моделі, з причиною і без цитат.
- **Відмова (refusal):** модель узагалі відмовляється відповідати, зазвичай з міркувань безпеки. API позначає її окремо від звичайного тексту.
- **Результат (outcome):** той один підсумок, яким закінчується запит. Тут: відповідь, утримання, немає Evidence, відмова або невалідна відповідь.

## Перед початком

Цей розділ пояснює рішення, на яких тримається код, щоб кожен крок був зрозумілим, коли ви до нього дійдете.

- **Що вже є.** `answering/evidence.py` дає Evidence з випадковими ID, `answering/citations.py` перетворює ID від моделі на цитати з даних сервера, `answering/prompt.py` збирає промпт і схему, а `answering/context_budget.py` вміщує їх у бюджет. `model.py` містить порт `ChatModel` з M01 і його помилки; `errors.py` перетворює ці помилки на HTTP-відповіді.
- **Чому новий порт, а не порт з M01.** `ChatModel.answer` з M01 приймає голе запитання і живить HTTP-ендпоінт, де пошуку ще немає. Змінивши його, ми зламали б робочий шлях заради можливості, якою він не може скористатися. Тож, як `RankingModel` і `RewriteModel` у M05, відповіді отримують власний вузький порт. Він піднімає ті самі помилки `ChatModelError`, тож HTTP-шар обробить його збої так само.
- **Адаптер лише передає.** `OpenAIAnswerModel` надсилає промпт таким, як його зібрано, і повертає текст відповіді, ознаку відмови і оплачені токени. Він не оцінює відповідь. Уся оцінка відбувається в `checked_answer`, який тести можуть викликати з будь-яким текстом, без мережі.
- **Що таке валідна відповідь.** Один JSON-об'єкт рівно з чотирма полями. Відповідь має містити видимий символ і цитувати щонайменше один Evidence. Утримання має давати причину з видимим символом і нічого не цитувати. Кожна цитата проходить через `resolve_citations` проти тих Evidence, що справді були надіслані, а не всіх, які знайшов пошук.
- **Чому зламана відповідь — це результат, а не виняток.** Провайдер уже виставив за неї рахунок. Повертаючи `INVALID` разом з usage, ми зберігаємо вартість у записах, як і в переписуванні з M05. Помилки піднімаються лише тоді, коли рахунку не було, наприклад при таймауті чи збої.
- **Немає Evidence — немає виклику.** Якщо нічого не вміщується, модель могла б лише утриматися, а ми б за це заплатили. Тож результат — `NO_EVIDENCE` без usage. M06-T07 вирішить, що сказати користувачеві.
- **Налаштування і бюджет.** `AnswerSettings` читає `ANSWER_*` із середовища. Типовий бюджет — 32 000 токенів, з них 1 000 на відповідь і 4 000 на історію. Це вибір заради вартості й швидкості, значно нижчий за власне вікно моделі. Модель — той самий закріплений знімок, що й у переписувача.
- **Що знайшло рев'ю.** Адверсаріальне рев'ю на понад 6 000 фазинг-відповідей не змогло змусити цитату вказати на Evidence поза промптом. Воно знайшло чотири зламані гарантії, кожну виправлено тестом, що падає без виправлення. Відповіді чи причини лише з невидимих символів приймалися; тепер вони потребують видимого символу, за тим самим правилом, що й запитання. Величезний цілочисельний таймаут падав з `OverflowError`; тепер його відхиляють. `TimeoutError`, який підняла сама модель, маскувався під наш дедлайн і тягнув за собою текст промпта; тепер лише наш власний дедлайн стає `ChatModelTimeout`, і той піднімається без прикріпленої помилки. Зібраний вручну usage чи ідентичність моделі могли занести текст у лог; тепер кількість токенів і назви мусять мати точні типи.
- **Де живе код.** `answering/generation.py` і `answering/openai_answering.py`, невеликі зміни в `model.py`, `errors.py` і `prompt.py`.
- **Відомі обмеження.** Модель, яка ігнорує скасування, може працювати довше за дедлайн; `asyncio.timeout` може лише попросити її зупинитися, і SDK OpenAI зупиняється. Чи приймає суворий режим OpenAI `enum` з ID Evidence у схемі, доводить лише необов'язковий живий тест, якому потрібен API-ключ. `checked_answer` перевіряє форму, а не правдивість: чи підтверджує процитований текст кожне твердження, — це M06-T06.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої передбачення

Поки коду ще немає, запишіть, що має бути правдою, коли урок завершено. Крок 6 перевіряє передбачення.

Створіть `docs/evidence/M06-T04-structured-answer.md`:

```markdown
# M06-T04 — Structured Answer evidence

## Invariant

`generate_answer` fits the Question and Evidence into the budget, calls
the `AnswerModel` once with exactly the prompt it counted, and returns
one outcome. `ANSWERED` holds visible text and at least one Citation,
each built on the server from Evidence that was in the prompt.
`ABSTAINED` holds a visible reason and no Citations. `REFUSED` and
`INVALID` hold no model text; `INVALID` names its defect. Every billed
reply keeps its usage. With no Evidence that fits, the model is not
called. Provider failures raise `ChatModelError` subclasses with no
request text; anything else the model raises propagates. No log record,
repr, or error holds Question, Document, or answer text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.generation"`
fails with `ModuleNotFoundError`.

## Predictions

- What outcome a reply gets that answers correctly but cites an Evidence the budget dropped: ...
- How many tests fail if citations are checked against all retrieved Evidence instead of the Evidence in the prompt: ...
- Which outcome a reply gets whose answer is only zero-width spaces, and why that matters to a user: ...

## Design

## Verification

## Decision

## Reflection
```

Перевірка: файл існує, перевірка, що має впасти, падає, а кожне передбачення — повне речення, яке крок 6 може спростувати.

### Крок 2 — Крок із кодом: невеликі зміни в наявних модулях

Цей крок додає три невеликі частини, потрібні новому коду, у файли, що вже існують.

**Простими словами:** Порту потрібна ще одна помилка — на випадок, коли провайдер відхиляє сам запит, наприклад через поганий ключ. HTTP-шар має вважати таку помилку нашою, бо повтор не допоможе. А перевірка на видимий символ, яку промпт уже використовує для запитання, стає публічною функцією, щоб перевірка відповіді могла її використати.

| Зміна | Навіщо |
|---|---|
| `ChatModelRequestRefused` у `model.py` | 4xx від провайдера — це не збій і не таймаут; повторювати не треба |
| `errors.py` відносить її до `INTERNAL` | Те саме правило, що й для `EmbeddingRequestRefused`: 500, а не 503, який запрошує повторити |
| `has_visible_text` у `prompt.py` | Одне правило «містить видимий символ» і для запитання, і для відповіді |

**1.** У `src/knowledge_service/model.py` знайдіть:

```python
class ChatModelRefused(ChatModelError):
    """The model declined to provide an answer."""
```

Замініть на:

```python
class ChatModelRequestRefused(ChatModelError):
    """The provider refused the request, such as for a bad key; do not retry."""


class ChatModelRefused(ChatModelError):
    """The model declined to provide an answer."""
```

**2.** У `src/knowledge_service/errors.py` знайдіть:

```python
from knowledge_service.model import ChatModelError, ChatModelRateLimited
```

Замініть на:

```python
from knowledge_service.model import (
    ChatModelError,
    ChatModelRateLimited,
    ChatModelRequestRefused,
)
```

Потім знайдіть:

```python
    if isinstance(error, EmbeddingRequestRefused):
        return FailureKind.INTERNAL
```

Замініть на:

```python
    if isinstance(error, (ChatModelRequestRefused, EmbeddingRequestRefused)):
        return FailureKind.INTERNAL
```

**3.** У `tests/test_errors.py` знайдіть:

```python
    ChatModelRateLimited,
    ChatModelTimeout,
```

Замініть на:

```python
    ChatModelRateLimited,
    ChatModelRequestRefused,
    ChatModelTimeout,
```

Потім знайдіть:

```python
        (ChatModelRateLimited(), FailureKind.CAPACITY),
```

Замініть на:

```python
        (ChatModelRateLimited(), FailureKind.CAPACITY),
        (ChatModelRequestRefused("bad key"), FailureKind.INTERNAL),
```

**4.** У `src/knowledge_service/answering/prompt.py` знайдіть:

```python
    if not any(_visible(character) for character in question):
        raise InvalidPrompt("the question must be non-empty text")
```

Замініть на:

```python
    if not has_visible_text(question):
        raise InvalidPrompt("the question must be non-empty text")


def has_visible_text(text: str) -> bool:
    """True when `text` holds a character that shows on its own."""
    return any(_visible(character) for character in text)
```

Перевірка:

```bash
uv run pytest tests/test_errors.py tests/test_prompt.py -q
```

Очікувано: 60 passed.

### Крок 3 — Крок із кодом: порт і перевірений робочий процес

Цей крок пише модуль, через який проходитиме кожна відповідь.

**Простими словами:** Спершу код перевіряє таймаут, потім вміщує запитання й Evidence у бюджет. Якщо жоден Evidence не вміщується, він на цьому зупиняється і модель не викликає. Інакше надсилає порахований промпт моделі з одним дедлайном. Збій провайдера передається далі як типізована помилка. Відповідь моделі перевіряється за всіма правилами і стає відповіддю з цитатами, утриманням з причиною, відмовою або невалідною відповіддю з назвою порушеного правила. Один рядок логу фіксує, що сталося, без жодного тексту.

| Частина | Що містить чи робить |
|---|---|
| `AnswerModel`, `ModelReply`, `ModelIdentity` | Порт: один виклик `generate` повертає текст відповіді, ознаку відмови й оплачений usage |
| `checked_answer` | Правила: один JSON-об'єкт, точні поля й типи, видимий текст, коректний Unicode, межі розміру, цитати через `resolve_citations` |
| `InvalidAnswer`, `AnswerDefect` | Типізоване відхилення і вісім названих причин; ніколи не слова моделі |
| `generate_answer` | Перевірка таймауту, бюджет, швидкий вихід без Evidence, один виклик, один результат |
| `AnswerResult`, `AnswerOutcome` | Результат з текстом, причиною, цитатами, дефектом, usage і звітом про контекст |
| `positive_seconds` | Перевірка таймауту, яка ніколи не падає; спільна з адаптером |
| `_ask` | Один дедлайн; лише його власне спрацювання стає `ChatModelTimeout`; відповідь хибної форми — malformed |
| `_log`, `_log_failure` | Один запис на запит: ID, лічильники, токени, результат |

Створіть `src/knowledge_service/answering/generation.py`:

```python
"""The AnswerModel port, and generating a structured Answer from Evidence.

`generate_answer` fits the Question and its Evidence into the context
budget (see `answering.context_budget`), asks a chat model for an answer
that follows the prompt's schema, and checks that answer on the server.
Nothing the model writes is trusted until it passes `checked_answer`.

What every `AnswerModel` adapter promises:

- **One call is one request.** `generate` returns a `ModelReply` holding
  the model's text, unchecked, whether the model refused, and the usage
  the provider billed. When there is nothing to bill, because of a
  timeout, an outage, a rate limit, or a refused request, it raises a
  `ChatModelError` instead, the same errors as the M01 `ChatModel` port.
- **A billed reply keeps its usage.** A refusal, or an answer cut off at
  its token limit, is returned with its usage, so its cost is never lost.
- **Errors and logs carry no text.**

What `generate_answer` promises:

- **Checked first.** A timeout that is not a positive number raises
  `InvalidAnswerRequest`; a bad Question, Evidence, or budget raises the
  errors of `allocate_context`. Both happen before anything is sent.
- **No Evidence, no call.** When no Evidence fits, or there was none, the
  model is not called: the result is `NO_EVIDENCE`, with no usage.
- **One deadline.** The model must answer within `timeout_seconds`, or
  `ChatModelTimeout` is raised, with no chained error. A `TimeoutError`
  the model raises itself is a bug, and propagates.
- **Provider failures propagate.** A `ChatModelError` from the model is
  raised as it is, so the HTTP layer maps it as it maps the M01 port's.
  Anything else the model raises is a bug and propagates too.
- **Every reply becomes one outcome.** `ANSWERED` with Citations built by
  `resolve_citations` from the kept Evidence only; `ABSTAINED` with a
  reason and no Citations; `REFUSED` when the model declined; `INVALID`
  with an `AnswerDefect` when the reply breaks a rule. Each carries the
  billed usage and the context report.
- **A checked reply.** `checked_answer` accepts only one JSON object with
  exactly `answer`, `citations`, `abstained`, and `reason`, each of its
  type and none repeated. An answer must hold a visible character and
  cite at least one Evidence it was given; an abstention must give a
  reason with a visible character and cite nothing. Texts must be valid
  Unicode and within their limits.
- **Quiet.** Results hide the answer and the reason from their repr; the
  one log record per call holds IDs, counts, and names only. A reply's
  token counts must be plain integers, and a model identity that is not
  plain text is logged as "unknown".
"""

import asyncio
import json
import logging
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID

from knowledge_service.answering.citations import (
    Citation,
    InvalidCitation,
    resolve_citations,
)
from knowledge_service.answering.context_budget import (
    ContextBudget,
    ContextReport,
    allocate_context,
)
from knowledge_service.answering.evidence import EvidenceSet
from knowledge_service.answering.prompt import EvidencePrompt, has_visible_text
from knowledge_service.contracts import Usage
from knowledge_service.model import (
    ChatModelError,
    ChatModelMalformedResponse,
    ChatModelTimeout,
)

logger = logging.getLogger(__name__)

MAX_REPLY_CHARACTERS = 40_000
MAX_ANSWER_CHARACTERS = 16_000
MAX_REASON_CHARACTERS = 1_000
_FIELDS = frozenset({"answer", "citations", "abstained", "reason"})


class InvalidAnswerRequest(ValueError):
    """The caller's request is broken; nothing was sent."""


class AnswerDefect(StrEnum):
    """Why a reply was rejected; the model's words are never repeated."""

    UNREADABLE = "unreadable"
    TOO_LONG = "too_long"
    INVALID_TEXT = "invalid_text"
    EMPTY_ANSWER = "empty_answer"
    UNCITED = "uncited"
    INVALID_CITATION = "invalid_citation"
    CITED_ABSTENTION = "cited_abstention"
    NO_REASON = "no_reason"


class InvalidAnswer(ChatModelMalformedResponse):
    """The model answered, but its reply breaks a rule."""

    def __init__(self, defect: AnswerDefect) -> None:
        self.defect = defect
        super().__init__(f"invalid answer: {defect.value}")


class AnswerOutcome(StrEnum):
    """What came of one request."""

    ANSWERED = "answered"
    ABSTAINED = "abstained"
    NO_EVIDENCE = "no_evidence"
    REFUSED = "refused"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    """The provider and the pinned model behind an answer."""

    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class ModelReply:
    """What the model returned, unchecked, and what the provider billed.

    `text` is None when the reply held no readable text; `refused` is True
    when the model declined to answer.
    """

    text: object = field(repr=False)
    refused: bool
    usage: Usage


class AnswerModel(Protocol):
    """A chat model that answers an Evidence prompt in its JSON schema."""

    @property
    def identity(self) -> ModelIdentity:
        """The provider and the pinned model."""
        ...

    async def generate(
        self,
        prompt: EvidencePrompt,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> ModelReply:
        """Return the model's reply and usage, or raise a `ChatModelError`."""
        ...


@dataclass(frozen=True, slots=True)
class CheckedAnswer:
    """A reply that passed every rule; Citations come from the server."""

    text: str = field(repr=False)
    abstained: bool
    reason: str | None = field(repr=False)
    citations: tuple[Citation, ...]


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """The outcome of one request, with its cost and its context report.

    `text` and `reason` are empty and None unless the outcome is
    `ANSWERED` or `ABSTAINED`; `usage` is None when the model was not
    called; `defect` is set only for `INVALID`.
    """

    outcome: AnswerOutcome
    text: str = field(repr=False)
    reason: str | None = field(repr=False)
    citations: tuple[Citation, ...]
    defect: AnswerDefect | None
    usage: Usage | None
    context: ContextReport


def checked_answer(text: object, evidence: EvidenceSet) -> CheckedAnswer:
    """Check one reply against the rules; raise `InvalidAnswer` otherwise."""
    if type(text) is not str or len(text) > MAX_REPLY_CHARACTERS:
        raise InvalidAnswer(
            AnswerDefect.TOO_LONG if type(text) is str else AnswerDefect.UNREADABLE
        )
    body = _parsed(text)
    answer, references = body["answer"], body["citations"]
    abstained, reason = body["abstained"], body["reason"]
    if (
        type(answer) is not str
        or type(references) is not list
        or type(abstained) is not bool
        or not (reason is None or type(reason) is str)
    ):
        raise InvalidAnswer(AnswerDefect.UNREADABLE)
    if len(answer) > MAX_ANSWER_CHARACTERS or (
        reason is not None and len(reason) > MAX_REASON_CHARACTERS
    ):
        raise InvalidAnswer(AnswerDefect.TOO_LONG)
    if not _valid(answer) or (reason is not None and not _valid(reason)):
        raise InvalidAnswer(AnswerDefect.INVALID_TEXT)
    if abstained:
        if references:
            raise InvalidAnswer(AnswerDefect.CITED_ABSTENTION)
        if reason is None or not has_visible_text(reason):
            raise InvalidAnswer(AnswerDefect.NO_REASON)
        return CheckedAnswer(text=answer, abstained=True, reason=reason, citations=())
    if not has_visible_text(answer):
        raise InvalidAnswer(AnswerDefect.EMPTY_ANSWER)
    try:
        citations = resolve_citations(evidence, cast(list[object], references))
    except InvalidCitation:
        raise InvalidAnswer(AnswerDefect.INVALID_CITATION) from None
    if not citations:
        raise InvalidAnswer(AnswerDefect.UNCITED)
    return CheckedAnswer(text=answer, abstained=False, reason=None, citations=citations)


async def generate_answer(
    question: str,
    evidence: EvidenceSet,
    budget: ContextBudget,
    model: AnswerModel,
    *,
    request_id: UUID,
    timeout_seconds: float,
) -> AnswerResult:
    """Ask the model to answer from the Evidence that fits, and check its reply."""
    if not positive_seconds(timeout_seconds):
        raise InvalidAnswerRequest("timeout_seconds must be a positive number")
    allocated = allocate_context(question, evidence, budget)
    context = allocated.report
    if not allocated.evidence.items:
        result = _result(AnswerOutcome.NO_EVIDENCE, context, usage=None)
        _log(request_id, model, result)
        return result
    reply = await _ask(model, allocated.prompt, request_id, timeout_seconds)
    if reply.refused:
        result = _result(AnswerOutcome.REFUSED, context, usage=reply.usage)
    else:
        try:
            checked = checked_answer(reply.text, allocated.evidence)
        except InvalidAnswer as error:
            result = _result(
                AnswerOutcome.INVALID, context, usage=reply.usage, defect=error.defect
            )
        else:
            result = AnswerResult(
                outcome=(
                    AnswerOutcome.ABSTAINED
                    if checked.abstained
                    else AnswerOutcome.ANSWERED
                ),
                text=checked.text,
                reason=checked.reason,
                citations=checked.citations,
                defect=None,
                usage=reply.usage,
                context=context,
            )
    _log(request_id, model, result)
    return result


def positive_seconds(value: object) -> bool:
    """True for a finite number of seconds above zero; never raises."""
    # bool is an int, and NaN compares false with everything; neither is.
    if type(value) not in (int, float):
        return False
    try:
        seconds = float(cast(float, value))
    except OverflowError:
        return False
    return math.isfinite(seconds) and seconds > 0


async def _ask(
    model: AnswerModel,
    prompt: EvidencePrompt,
    request_id: UUID,
    timeout_seconds: float,
) -> ModelReply:
    """One call under one deadline; a reply of the wrong shape is malformed."""
    deadline = asyncio.timeout(timeout_seconds)
    reply: object = None
    try:
        async with deadline:
            reply = await model.generate(
                prompt, request_id=request_id, timeout_seconds=timeout_seconds
            )
    except TimeoutError:
        # Only the deadline's own timeout is ours; the model's is a bug.
        if not deadline.expired():
            raise
    except ChatModelError as error:
        _log_failure(request_id, model, type(error).__name__)
        raise
    if deadline.expired():
        # Raised here, not in the except block, so it chains no error.
        _log_failure(request_id, model, ChatModelTimeout.__name__)
        raise ChatModelTimeout()
    if not _well_formed(reply):
        raise ChatModelMalformedResponse("the model did not return a ModelReply")
    return cast(ModelReply, reply)


def _well_formed(reply: object) -> bool:
    """A ModelReply whose flag and token counts are exactly what they claim."""
    if type(reply) is not ModelReply or type(reply.refused) is not bool:
        return False
    usage = cast(object, reply.usage)
    if type(usage) is not Usage:
        return False
    # model_construct skips validation, so the counts are checked again.
    counts = (usage.input_tokens, usage.output_tokens, usage.total_tokens)
    return all(type(count) is int and count >= 0 for count in counts)


def _parsed(text: str) -> dict[str, object]:
    """The reply as one JSON object with exactly the four fields, each once."""

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        keys = [key for key, _ in pairs]
        if len(set(keys)) != len(keys):
            raise ValueError("a key appears twice")
        return dict(pairs)

    try:
        body: object = json.loads(text, object_pairs_hook=unique)
    except ValueError, RecursionError:
        raise InvalidAnswer(AnswerDefect.UNREADABLE) from None
    if type(body) is not dict or frozenset(cast(dict[str, object], body)) != _FIELDS:
        raise InvalidAnswer(AnswerDefect.UNREADABLE)
    return cast(dict[str, object], body)


def _valid(text: str) -> bool:
    """True when `text` is valid Unicode: JSON can hold a lone surrogate."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _result(
    outcome: AnswerOutcome,
    context: ContextReport,
    *,
    usage: Usage | None,
    defect: AnswerDefect | None = None,
) -> AnswerResult:
    return AnswerResult(
        outcome=outcome,
        text="",
        reason=None,
        citations=(),
        defect=defect,
        usage=usage,
        context=context,
    )


def _identity(model: AnswerModel) -> tuple[str, str]:
    """The provider and model as plain text, or "unknown"; never raises."""
    # A broken identity must not hide the outcome being logged.
    try:
        identity = model.identity
        names = (identity.provider, identity.model)
    except Exception:
        return "unknown", "unknown"
    if not all(type(name) is str for name in names):
        return "unknown", "unknown"
    return names


def _log(request_id: UUID, model: AnswerModel, result: AnswerResult) -> None:
    """Record one request: IDs, counts, tokens, outcome. No text."""
    provider, name = _identity(model)
    fields: dict[str, object] = {
        "request_id": str(request_id),
        "provider": provider,
        "model": name,
        "outcome": result.outcome.value,
        "defect": None if result.defect is None else result.defect.value,
        "evidence_kept": len(result.context.kept),
        "evidence_dropped": len(result.context.dropped),
        "prompt_tokens": result.context.prompt_tokens,
        "citations": len(result.citations),
    }
    if result.usage is not None:
        fields["input_tokens"] = result.usage.input_tokens
        fields["output_tokens"] = result.usage.output_tokens
    level = logging.INFO if result.outcome in _EXPECTED else logging.WARNING
    logger.log(level, "answer request finished", extra={"answer": fields})


def _log_failure(request_id: UUID, model: AnswerModel, error: str) -> None:
    provider, name = _identity(model)
    fields = {
        "request_id": str(request_id),
        "provider": provider,
        "model": name,
        "outcome": error,
    }
    logger.warning("answer request failed", extra={"answer": fields})


_EXPECTED = frozenset(
    {AnswerOutcome.ANSWERED, AnswerOutcome.ABSTAINED, AnswerOutcome.NO_EVIDENCE}
)
```

Тут важливі три деталі. `checked_answer` отримує ті Evidence, що були в промпті, `allocated.evidence`, а не ті, що передав викликач; саме це не дає процитувати текст, якого модель не бачила. JSON читається з хуком, який відхиляє повторений ключ, бо звичайний `json.loads` мовчки залишив би останній. А `ChatModelTimeout` піднімається після блоку `except`, тож не несе прикріпленої помилки, у якій міг би бути промпт.

Перевірка:

```bash
uv run python -c "import knowledge_service.answering.generation"
uv run pyright src/knowledge_service/answering/generation.py
```

Очікувано: імпорт нічого не друкує, а pyright показує 0 помилок.

### Крок 4 — Крок із кодом: адаптер OpenAI і налаштування

Цей крок підключає порт до Responses API від OpenAI.

**Простими словами:** Адаптер надсилає фіксовані інструкції промпта, його вхідні дані й схему саме такими, як їх зібрано, просить провайдера нічого не зберігати і вимикає приховані повтори SDK. Він читає назад текст відповіді, ознаку відмови й оплачені токени, не довіряючи формі відповіді. Кожна помилка провайдера стає однією помилкою порту без тексту запиту, і кожен виклик залишає один рядок логу. Налаштування дають назву моделі, таймаут і бюджет токенів.

| Частина | Що містить чи робить |
|---|---|
| `AnswerSettings` | Налаштування `ANSWER_*`: закріплена модель, зусилля міркування, таймаут, межа виводу, токени контексту й історії, ціни; `budget` будує `ContextBudget` і перевіряється при завантаженні налаштувань |
| `OpenAIAnswerModel.generate` | Перевіряє запит, надсилає його зі Structured Outputs у суворому режимі, `store=False`, один дедлайн |
| `_reply` | Текст і ознака відмови; обрізана відповідь чи відповідь дивної форми не дає тексту, але зберігає usage |
| `_translated` | Таймаут, ліміт запитів, збій або відхилений запит — кожне в одну `ChatModelError` |
| `_log` | Один запис на виклик: ID, затримка, токени, результат |

Створіть `src/knowledge_service/answering/openai_answering.py`:

```python
"""OpenAI implementation of the AnswerModel port, and the answer settings.

One call to `generate` is one request to the Responses API. The adapter
adds what the port cannot know:

- **A pinned model.** The model is a dated snapshot, so a provider update
  cannot change the answers silently.
- **The prompt as rendered.** The fixed instructions, the input document,
  and the response schema come from `render_prompt` (see
  `answering.prompt`) unchanged, so what was counted is what is sent.
- **A constrained answer.** Structured Outputs force the reply into the
  prompt's schema, whose citations allow only this request's Evidence
  IDs. That narrows what the model can write; `checked_answer` still
  decides what counts.
- **Nothing kept.** `store=False`, so the provider does not keep the
  response for later retrieval.
- **A deadline without hidden retries.** The SDK's own retries are off, so
  a timeout means what it says.
- **Cost capture.** Every reply returns the billed input and output
  tokens, also a refusal or a reply cut off at its token limit.
- **Safe logging.** One log record per call with counts, tokens, timing,
  and the outcome; never text. Errors are raised outside the SDK's `except`
  block, because its error holds the request, and the request holds the
  Question and the Evidence.

`AnswerSettings`, read from `ANSWER_*`, also gives the `ContextBudget`:
`context_tokens` is a budget chosen for cost and speed, well below the
model's own window, and `max_output_tokens` is the Answer's reserve.
"""

import asyncio
import logging
import time
from decimal import Decimal
from typing import Self, cast
from uuid import UUID

import openai
from openai import AsyncOpenAI, omit
from openai.types.shared_params import Reasoning, ReasoningEffort
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from knowledge_service.answering.context_budget import ContextBudget
from knowledge_service.answering.generation import (
    InvalidAnswerRequest,
    ModelIdentity,
    ModelReply,
    positive_seconds,
)
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.contracts import Usage
from knowledge_service.model import (
    ChatModelError,
    ChatModelMalformedResponse,
    ChatModelRateLimited,
    ChatModelRequestRefused,
    ChatModelTimeout,
    ChatModelUnavailable,
)
from knowledge_service.pricing import ModelPrice

logger = logging.getLogger(__name__)

PROVIDER = "openai"


class AnswerSettings(BaseSettings):
    """Settings for answering, read from `ANSWER_*`.

    The prices are the provider's list prices for the default model on
    2026-11-06, in US dollars per million tokens; set both when the model
    changes.
    """

    model_config = SettingsConfigDict(env_prefix="ANSWER_")

    model: str = Field(default="gpt-5.4-mini-2026-03-17", min_length=1)
    # None sends no reasoning setting, for a model that has none.
    reasoning_effort: ReasoningEffort = "none"
    timeout_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    max_output_tokens: int = Field(default=1_000, gt=0, le=8_192)
    context_tokens: int = Field(default=32_000, gt=0)
    history_tokens: int = Field(default=4_000, ge=0)
    input_usd_per_million_tokens: Decimal = Field(default=Decimal("0.75"), ge=0)
    output_usd_per_million_tokens: Decimal = Field(default=Decimal("4.50"), ge=0)

    @model_validator(mode="after")
    def _fits(self) -> Self:
        # Raises InvalidContextBudget, a ValueError, when the reserves fill it.
        _ = self.budget
        return self

    @property
    def budget(self) -> ContextBudget:
        """The context budget: the window, the Answer's and History's room."""
        return ContextBudget(
            context_tokens=self.context_tokens,
            answer_tokens=self.max_output_tokens,
            history_tokens=self.history_tokens,
        )


class OpenAIAnswerModel:
    """Translate AnswerModel calls to the OpenAI Responses API."""

    def __init__(self, client: AsyncOpenAI, settings: AnswerSettings) -> None:
        # Retries are the caller's job; hidden ones would stretch the deadline.
        self._client = client.with_options(max_retries=0)
        self._settings = settings
        self._identity = ModelIdentity(provider=PROVIDER, model=settings.model)

    @property
    def identity(self) -> ModelIdentity:
        return self._identity

    @property
    def price(self) -> ModelPrice:
        """The configured price, under the model's name as its price ID."""
        return ModelPrice(
            price_id=self._settings.model,
            input_usd_per_million_tokens=self._settings.input_usd_per_million_tokens,
            output_usd_per_million_tokens=self._settings.output_usd_per_million_tokens,
        )

    async def generate(
        self,
        prompt: EvidencePrompt,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> ModelReply:
        if type(prompt) is not EvidencePrompt:
            raise InvalidAnswerRequest("prompt must be an EvidencePrompt")
        deadline = self._deadline(timeout_seconds)
        effort = self._settings.reasoning_effort
        started_at = time.perf_counter()
        response: object = None
        failure: ChatModelError | None = None
        try:
            async with asyncio.timeout(deadline):
                response = await self._client.responses.create(
                    model=self._settings.model,
                    instructions=prompt.instructions,
                    input=prompt.input,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": "answer",
                            "strict": True,
                            "schema": prompt.response_schema(),
                        }
                    },
                    max_output_tokens=self._settings.max_output_tokens,
                    store=False,
                    timeout=deadline,
                    extra_headers={"X-Request-ID": str(request_id)},
                    reasoning=omit if effort is None else Reasoning(effort=effort),
                )
        except (TimeoutError, openai.APIError) as error:
            failure = _translated(error)
        except ValueError:
            # A successful status with a body that is not JSON.
            failure = ChatModelMalformedResponse("the provider response was not JSON")
        except asyncio.CancelledError:
            # The caller's own deadline usually ends a slow call: still record it.
            _log(request_id, self._identity, _since(started_at), "cancelled")
            raise
        latency_ms = _since(started_at)
        try:
            if failure is not None:
                # Raised outside the SDK's except block: its error holds the request.
                raise failure
            usage = self._usage(response, latency_ms)
        except ChatModelError as error:
            _log(request_id, self._identity, latency_ms, type(error).__name__)
            raise
        text, refused = _reply(response)
        outcome = (
            "refused" if refused else "succeeded" if text is not None else "unreadable"
        )
        _log(request_id, self._identity, latency_ms, outcome, usage)
        return ModelReply(text=text, refused=refused, usage=usage)

    def _deadline(self, timeout_seconds: float) -> float:
        if not positive_seconds(timeout_seconds):
            raise InvalidAnswerRequest("timeout_seconds must be a positive number")
        return min(float(timeout_seconds), self._settings.timeout_seconds)

    def _usage(self, response: object, latency_ms: int) -> Usage:
        """Read the billed tokens without trusting the response's shape."""
        usage = getattr(response, "usage", None)
        tokens = [
            getattr(usage, name, None) for name in ("input_tokens", "output_tokens")
        ]
        if any(type(value) is not int or value < 0 for value in tokens):
            raise ChatModelMalformedResponse("the response has no token usage")
        input_tokens, output_tokens = cast(list[int], tokens)
        return Usage(
            provider=PROVIDER,
            model=self._settings.model,
            price_id=self._settings.model,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
        )


def _reply(response: object) -> tuple[str | None, bool]:
    """The reply's text and whether the model refused.

    A refusal gives `(None, True)`. An answer cut off at its token limit,
    or an output of the wrong shape, gives `(None, False)`; the caller
    still bills it. The SDK does not check a successful body, so every
    part is checked here before it is read.
    """
    output = getattr(response, "output", None)
    if type(output) is not list:
        return None, False
    parts: list[str] = []
    for item in cast(list[object], output):
        content = getattr(item, "content", None)
        if content is None:
            continue
        if type(content) is not list:
            return None, False
        for part in cast(list[object], content):
            kind = getattr(part, "type", None)
            if kind == "refusal":
                return None, True
            text = getattr(part, "text", None)
            if kind == "output_text" and type(text) is not str:
                return None, False
            if kind == "output_text":
                parts.append(cast(str, text))
    if getattr(response, "status", None) != "completed" or not parts:
        return None, False
    return "".join(parts), False


def _translated(error: TimeoutError | openai.APIError) -> ChatModelError:
    """Map one SDK failure to the port error that says whether to retry."""
    match error:
        case (
            TimeoutError()
            | openai.APITimeoutError()
            | openai.APIStatusError(status_code=408)
        ):
            return ChatModelTimeout()
        case openai.RateLimitError():
            return ChatModelRateLimited()
        case (
            openai.APIConnectionError()
            | openai.InternalServerError()
            | openai.ConflictError()
        ):
            return ChatModelUnavailable()
        case openai.APIStatusError():
            return ChatModelRequestRefused(
                f"the provider refused the request with status {error.status_code}"
            )
        case _:
            return ChatModelMalformedResponse("the provider response was not readable")


def _since(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1_000)


def _log(
    request_id: UUID,
    identity: ModelIdentity,
    latency_ms: int,
    outcome: str,
    usage: Usage | None = None,
) -> None:
    """Record one call: identifiers, tokens, timing, outcome. No text.

    `outcome` is "succeeded", "refused", "unreadable", "cancelled", or the
    name of the port error raised; the adapter chooses it, never the
    provider.
    """
    fields: dict[str, object] = {
        "request_id": str(request_id),
        "provider": identity.provider,
        "model": identity.model,
        "latency_ms": latency_ms,
        "outcome": outcome,
    }
    if usage is not None:
        fields["input_tokens"] = usage.input_tokens
        fields["output_tokens"] = usage.output_tokens
    level = logging.INFO if outcome == "succeeded" else logging.WARNING
    logger.log(level, "answer model call finished", extra={"answer_model": fields})
```

Цей файл близько повторює `retrieval/openai_rewriting.py` з M05-T15, тож порівняйте їх, якщо якийсь рядок незрозумілий. Відмінність у тому, що схема тут не константа: вона береться з промпта, бо перелічує власні ID Evidence цього запиту.

Перевірка:

```bash
uv run python -c "from knowledge_service.answering.openai_answering import AnswerSettings; print(AnswerSettings().budget)"
```

Очікувано: `ContextBudget(context_tokens=32000, answer_tokens=1000, history_tokens=4000)`.

### Крок 5 — Крок із кодом: тести робочого процесу й адаптера

Цей крок доводить кожен результат, кожен збій і те, що жоден текст не витікає.

**Простими словами:** Тести робочого процесу використовують фейкову модель, яка повертає будь-яку відповідь, потрібну тесту, тож вони працюють без мережі. Вони покривають відповідь, утримання, відмову, 26 зламаних відповідей, цитату Evidence, відкинутого бюджетом, кожен збій провайдера, модель, що зависла, і випадок без Evidence. Фазинг-тест надсилає 300 випадкових відповідей і очікує від кожної один результат, а не падіння. Тести адаптера підмінюють лише клієнт OpenAI і перевіряють, що надсилається, що читається і як повідомляється кожен збій.

| Тест | Що доводить |
|---|---|
| `test_a_supported_answer_cites_evidence_with_server_provenance` | Підтримана відповідь: цитати з даних сервера, надіслано порахований промпт |
| `test_an_abstention_gives_its_reason_and_no_citations` | Утримання |
| `test_a_refusal_keeps_its_usage_and_has_no_text` | Відмова |
| `test_a_reply_that_breaks_a_rule_is_invalid_and_keeps_its_usage` | Невалідна схема: 26 зламаних відповідей, кожна зі своїм дефектом |
| `test_evidence_dropped_by_the_budget_cannot_be_cited` | Найризиковіший край |
| `test_a_provider_failure_propagates_as_its_port_error` | Збій провайдера |
| `test_a_stalled_model_times_out_at_the_deadline`, `test_a_timeout_raised_by_the_model_itself_is_not_hidden` | Наш дедлайн стає `ChatModelTimeout`; власний таймаут моделі — це баг, і він проходить далі |
| `test_a_reply_of_the_wrong_shape_is_malformed`, `test_a_bug_in_the_model_is_not_hidden` | Відповідь, що не є `ModelReply`, і будь-яка інша помилка не ховаються |
| `test_with_no_evidence_that_fits_the_model_is_not_called`, `test_a_bad_timeout_is_refused_before_sending` | Без Evidence чи з поганим таймаутом нічого не надсилається |
| `test_a_reply_with_unchecked_usage_is_malformed`, `test_a_hostile_model_identity_never_reaches_the_log` | Регресії з рев'ю щодо логу |
| `test_no_question_document_or_answer_text_reaches_logs_or_reprs` | Жодного підкинутого тексту в логах чи repr |
| `test_any_reply_becomes_one_outcome_never_an_untyped_crash` | 300 випадкових відповідей |
| `tests/test_openai_answering.py` | Надісланий запит, відмова, нечитабельні відповіді, кожна помилка провайдера без тексту, дедлайн, поганий вхід, логи, налаштування і адаптер разом із робочим процесом |

Створіть `tests/test_generation.py`:

```python
"""Generating a structured Answer: one checked outcome per reply, or a typed error."""

import asyncio
import json
import logging
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.context_budget import (
    ContextBudget,
    allocate_context,
    count_prompt_tokens,
)
from knowledge_service.answering.evidence import EvidenceSet, build_evidence
from knowledge_service.answering.generation import (
    AnswerDefect,
    AnswerOutcome,
    InvalidAnswerRequest,
    ModelIdentity,
    ModelReply,
    generate_answer,
)
from knowledge_service.answering.prompt import EvidencePrompt, render_prompt
from knowledge_service.contracts import Usage
from knowledge_service.model import (
    ChatModelError,
    ChatModelMalformedResponse,
    ChatModelRateLimited,
    ChatModelRequestRefused,
    ChatModelTimeout,
    ChatModelUnavailable,
)

QUESTION = "How many days of annual leave do I get?"
REQUEST_ID = UUID("00000000-0000-0000-0000-000000000042")
BUDGET = ContextBudget(context_tokens=32_000, answer_tokens=1_000, history_tokens=0)
USAGE = Usage(
    provider="fake",
    model="fake-1",
    latency_ms=5,
    input_tokens=900,
    output_tokens=40,
    total_tokens=940,
)
EVIDENCE = build_evidence(
    [
        candidate(1, text="Every employee receives 24 days of annual leave."),
        candidate(2, text="Unused leave carries over for 5 days."),
    ]
)
FIRST, SECOND = (item.evidence_id.value for item in EVIDENCE.items)


def body(**fields: Any) -> str:
    """A reply in the schema; any field can be replaced."""
    values: dict[str, Any] = {
        "answer": "You get 24 days of annual leave.",
        "citations": [FIRST],
        "abstained": False,
        "reason": None,
    }
    values.update(fields)
    return json.dumps(values)


class FakeAnswerModel:
    """Returns one reply, raises one error, or never answers; records prompts."""

    def __init__(
        self,
        text: object = None,
        *,
        refused: bool = False,
        reply: object = None,
        error: BaseException | None = None,
        block: bool = False,
    ) -> None:
        self._text = text
        self._refused = refused
        self._reply = reply
        self._error = error
        self._block = block
        self.prompts: list[EvidencePrompt] = []

    @property
    def identity(self) -> ModelIdentity:
        return ModelIdentity(provider="fake", model="fake-1")

    async def generate(
        self,
        prompt: EvidencePrompt,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> ModelReply:
        self.prompts.append(prompt)
        if self._block:
            await asyncio.Event().wait()
        if self._error is not None:
            raise self._error
        if self._reply is not None:
            return self._reply  # type: ignore[return-value]
        return ModelReply(text=self._text, refused=self._refused, usage=USAGE)


async def generate(
    model: FakeAnswerModel,
    evidence: EvidenceSet = EVIDENCE,
    *,
    question: str = QUESTION,
    budget: ContextBudget = BUDGET,
    timeout_seconds: float = 5.0,
) -> Any:
    return await generate_answer(
        question,
        evidence,
        budget,
        model,
        request_id=REQUEST_ID,
        timeout_seconds=timeout_seconds,
    )


async def test_a_supported_answer_cites_evidence_with_server_provenance() -> None:
    model = FakeAnswerModel(body(citations=[SECOND, FIRST]))

    result = await generate(model)

    assert result.outcome is AnswerOutcome.ANSWERED
    assert result.text == "You get 24 days of annual leave."
    assert result.reason is None
    assert [c.evidence_id.value for c in result.citations] == [SECOND, FIRST]
    first = EVIDENCE.items[0]
    assert result.citations[1].chunk_id == first.chunk_id
    assert result.citations[1].title == first.title
    assert result.usage == USAGE
    assert result.defect is None
    # The model got exactly the prompt the budget counted.
    (sent,) = model.prompts
    assert sent == allocate_context(QUESTION, EVIDENCE, BUDGET).prompt
    assert result.context.kept == tuple(item.evidence_id for item in EVIDENCE.items)


async def test_an_abstention_gives_its_reason_and_no_citations() -> None:
    model = FakeAnswerModel(
        body(
            answer="I cannot answer that from the documents.",
            citations=[],
            abstained=True,
            reason="The evidence does not mention parental leave.",
        )
    )

    result = await generate(model)

    assert result.outcome is AnswerOutcome.ABSTAINED
    assert result.reason == "The evidence does not mention parental leave."
    assert result.citations == ()
    assert result.usage == USAGE


async def test_a_refusal_keeps_its_usage_and_has_no_text() -> None:
    result = await generate(FakeAnswerModel(None, refused=True))

    assert result.outcome is AnswerOutcome.REFUSED
    assert (result.text, result.reason, result.citations) == ("", None, ())
    assert result.usage == USAGE


@pytest.mark.parametrize(
    ("text", "defect"),
    [
        (None, AnswerDefect.UNREADABLE),
        (5, AnswerDefect.UNREADABLE),
        ("not json", AnswerDefect.UNREADABLE),
        ("[]", AnswerDefect.UNREADABLE),
        (json.dumps({"answer": "x", "citations": []}), AnswerDefect.UNREADABLE),
        (body(extra=1), AnswerDefect.UNREADABLE),
        (
            '{"answer": "a", "answer": "b", "citations": [], "abstained": true,'
            ' "reason": "r"}',
            AnswerDefect.UNREADABLE,
        ),
        (body(abstained="false"), AnswerDefect.UNREADABLE),
        (body(citations=FIRST), AnswerDefect.UNREADABLE),
        (body(reason=5), AnswerDefect.UNREADABLE),
        ("[" * 100_000 + "]" * 100_000, AnswerDefect.TOO_LONG),
        ("[" * 10_000 + "]" * 10_000, AnswerDefect.UNREADABLE),
        (body(answer="x" * 16_001), AnswerDefect.TOO_LONG),
        (body(answer="Leave is \ud800 days."), AnswerDefect.INVALID_TEXT),
        (body(answer="   "), AnswerDefect.EMPTY_ANSWER),
        (body(answer="\u200b\u200b"), AnswerDefect.EMPTY_ANSWER),
        (body(answer="\u0301\u0301"), AnswerDefect.EMPTY_ANSWER),
        (body(answer="\u202e\x00"), AnswerDefect.EMPTY_ANSWER),
        (body(citations=[]), AnswerDefect.UNCITED),
        (body(citations=["E00000099"]), AnswerDefect.INVALID_CITATION),
        (body(citations=[FIRST, FIRST]), AnswerDefect.INVALID_CITATION),
        (body(citations=["cite E1"]), AnswerDefect.INVALID_CITATION),
        (
            body(abstained=True, citations=[FIRST], reason="Not enough."),
            AnswerDefect.CITED_ABSTENTION,
        ),
        (body(abstained=True, citations=[], reason=None), AnswerDefect.NO_REASON),
        (body(abstained=True, citations=[], reason=" "), AnswerDefect.NO_REASON),
        (
            body(abstained=True, citations=[], reason="\u2800\u3164"),
            AnswerDefect.NO_REASON,
        ),
    ],
    ids=[
        "cut-off",
        "not-text",
        "not-json",
        "not-an-object",
        "missing-fields",
        "extra-field",
        "repeated-field",
        "abstained-not-bool",
        "citations-not-list",
        "reason-not-text",
        "too-long",
        "deep",
        "answer-too-long",
        "lone-surrogate",
        "blank-answer",
        "invisible-answer",
        "combining-only-answer",
        "control-only-answer",
        "uncited",
        "unknown-id",
        "repeated-id",
        "malformed-id",
        "cited-abstention",
        "no-reason",
        "blank-reason",
        "invisible-reason",
    ],
)
async def test_a_reply_that_breaks_a_rule_is_invalid_and_keeps_its_usage(
    text: object, defect: AnswerDefect
) -> None:
    result = await generate(FakeAnswerModel(text))

    assert result.outcome is AnswerOutcome.INVALID
    assert result.defect is defect
    assert (result.text, result.reason, result.citations) == ("", None, ())
    assert result.usage == USAGE


async def test_evidence_dropped_by_the_budget_cannot_be_cited() -> None:
    evidence = build_evidence(
        [candidate(1, text="Leave is 24 days."), candidate(2, text="x" * 5_000)]
    )
    kept_only = count_prompt_tokens(
        render_prompt(QUESTION, EvidenceSet(evidence.items[:1]))
    )
    budget = ContextBudget(kept_only + 1_000, 1_000, 0)
    dropped = evidence.items[1].evidence_id.value
    model = FakeAnswerModel(body(citations=[dropped]))

    result = await generate(model, evidence, budget=budget)

    assert result.context.dropped == (evidence.items[1].evidence_id,)
    assert dropped not in model.prompts[0].input
    assert result.outcome is AnswerOutcome.INVALID
    assert result.defect is AnswerDefect.INVALID_CITATION


@pytest.mark.parametrize(
    "error",
    [
        ChatModelTimeout(),
        ChatModelRateLimited(),
        ChatModelUnavailable(),
        ChatModelRequestRefused("the provider refused the request with status 401"),
        ChatModelMalformedResponse("the provider response was not JSON"),
    ],
)
async def test_a_provider_failure_propagates_as_its_port_error(
    error: ChatModelError,
) -> None:
    with pytest.raises(ChatModelError) as raised:
        await generate(FakeAnswerModel(error=error))

    assert raised.value is error


async def test_a_stalled_model_times_out_at_the_deadline() -> None:
    with pytest.raises(ChatModelTimeout) as raised:
        await generate(FakeAnswerModel(block=True), timeout_seconds=0.05)

    assert raised.value.__context__ is None


async def test_a_timeout_raised_by_the_model_itself_is_not_hidden() -> None:
    model = FakeAnswerModel(error=TimeoutError(MARKER))

    with pytest.raises(TimeoutError) as raised:
        await generate(model, timeout_seconds=5)

    assert type(raised.value) is TimeoutError


async def test_a_reply_with_unchecked_usage_is_malformed() -> None:
    usage = Usage.model_construct(
        provider="fake",
        model="fake-1",
        latency_ms=5,
        input_tokens=MARKER,
        output_tokens=40,
        total_tokens=940,
    )
    reply = ModelReply(text=body(), refused=False, usage=usage)

    with pytest.raises(ChatModelMalformedResponse) as raised:
        await generate(FakeAnswerModel(reply=reply))

    assert MARKER not in str(raised.value)


class _Shown:
    def __str__(self) -> str:
        return MARKER


async def test_a_hostile_model_identity_never_reaches_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = FakeAnswerModel(body())
    hostile = ModelIdentity(provider=_Shown(), model=_Shown())  # type: ignore[arg-type]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(FakeAnswerModel, "identity", property(lambda self: hostile))
        result = await generate(model)

    assert result.outcome is AnswerOutcome.ANSWERED
    (record,) = [r for r in caplog.records if r.name.endswith("generation")]
    assert record.__dict__["answer"]["provider"] == "unknown"
    assert MARKER not in caplog.text


async def test_a_reply_of_the_wrong_shape_is_malformed() -> None:
    with pytest.raises(ChatModelMalformedResponse):
        await generate(FakeAnswerModel(reply={"text": body()}))


async def test_a_bug_in_the_model_is_not_hidden() -> None:
    with pytest.raises(RuntimeError):
        await generate(FakeAnswerModel(error=RuntimeError("bug")))


@pytest.mark.parametrize("room", [0, 10], ids=["no-evidence", "none-fits"])
async def test_with_no_evidence_that_fits_the_model_is_not_called(room: int) -> None:
    evidence = EVIDENCE if room else EvidenceSet(())
    base = count_prompt_tokens(render_prompt(QUESTION, EvidenceSet(())))
    model = FakeAnswerModel(body())

    result = await generate(
        model, evidence, budget=ContextBudget(base + room + 1_000, 1_000, 0)
    )

    assert model.prompts == []
    assert result.outcome is AnswerOutcome.NO_EVIDENCE
    assert result.usage is None
    assert len(result.context.dropped) == len(evidence.items)


@pytest.mark.parametrize(
    "timeout_seconds",
    [0, -1, float("nan"), float("inf"), True, 10**400],
    ids=["zero", "negative", "nan", "inf", "bool", "huge"],
)
async def test_a_bad_timeout_is_refused_before_sending(timeout_seconds: float) -> None:
    model = FakeAnswerModel(body())

    with pytest.raises(InvalidAnswerRequest):
        await generate(model, timeout_seconds=timeout_seconds)

    assert model.prompts == []


async def test_no_question_document_or_answer_text_reaches_logs_or_reprs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    evidence = build_evidence([candidate(1, text=f"{MARKER} leave is 24 days.")])
    first = evidence.items[0].evidence_id.value
    answered = FakeAnswerModel(body(answer=MARKER, citations=[first]))
    invalid = FakeAnswerModel(body(answer=MARKER, citations=["E00000099"]))

    results = [
        await generate(answered, evidence, question=f"{MARKER}?"),
        await generate(invalid, evidence, question=f"{MARKER}?"),
    ]
    with pytest.raises(ChatModelUnavailable):
        await generate(FakeAnswerModel(error=ChatModelUnavailable()), evidence)

    assert [r.outcome for r in results] == [
        AnswerOutcome.ANSWERED,
        AnswerOutcome.INVALID,
    ]
    records = [r for r in caplog.records if r.name.endswith("generation")]
    assert [r.__dict__["answer"]["outcome"] for r in records] == [
        "answered",
        "invalid",
        "ChatModelUnavailable",
    ]
    assert MARKER not in caplog.text
    assert all(MARKER not in repr(r.__dict__) for r in records)
    assert all(MARKER not in repr(result) for result in results)


json_values = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=20),
    lambda children: (
        st.lists(children, max_size=4)
        | st.dictionaries(st.text(max_size=10), children, max_size=4)
    ),
    max_leaves=12,
)


@given(
    answer=json_values | st.text(max_size=40),
    citations=json_values | st.lists(st.sampled_from([FIRST, SECOND, "E1"])),
    abstained=json_values,
    reason=json_values,
)
@settings(derandomize=True, max_examples=300, deadline=None)
def test_any_reply_becomes_one_outcome_never_an_untyped_crash(
    answer: object, citations: object, abstained: object, reason: object
) -> None:
    text = json.dumps(
        {
            "answer": answer,
            "citations": citations,
            "abstained": abstained,
            "reason": reason,
        }
    )

    result = asyncio.run(generate(FakeAnswerModel(text)))

    assert result.outcome in {
        AnswerOutcome.ANSWERED,
        AnswerOutcome.ABSTAINED,
        AnswerOutcome.INVALID,
    }
    known = {FIRST, SECOND}
    assert all(c.evidence_id.value in known for c in result.citations)
    if result.outcome is AnswerOutcome.ANSWERED:
        assert result.citations
```

Створіть `tests/test_openai_answering.py`:

```python
"""The OpenAI answer adapter: what it sends, what it reads, how it fails."""

import asyncio
import json
import logging
import os
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import httpx2
import openai
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.context_budget import ContextBudget, allocate_context
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import (
    AnswerOutcome,
    InvalidAnswerRequest,
    generate_answer,
)
from knowledge_service.answering.openai_answering import (
    AnswerSettings,
    OpenAIAnswerModel,
)
from knowledge_service.model import (
    ChatModelError,
    ChatModelMalformedResponse,
    ChatModelRateLimited,
    ChatModelRequestRefused,
    ChatModelTimeout,
    ChatModelUnavailable,
)
from openai_stubs import status_error
from ranking_stubs import response_for

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000043")
QUESTION = "How many days of annual leave do I get?"
EVIDENCE = build_evidence(
    [candidate(1, text=f"Every employee receives 24 days of leave. {MARKER}")]
)
PROMPT = allocate_context(QUESTION, EVIDENCE, AnswerSettings().budget).prompt
CITED = EVIDENCE.items[0].evidence_id.value
REPLY = json.dumps(
    {
        "answer": "You get 24 days.",
        "citations": [CITED],
        "abstained": False,
        "reason": None,
    }
)


class StubResponses:
    """Replace only `AsyncOpenAI.responses`; record every request.

    By default it answers `REPLY`. `response` answers with that object
    instead, `error` raises, and `block` never answers.
    """

    def __init__(
        self,
        *,
        response: object = None,
        error: BaseException | None = None,
        block: bool = False,
    ) -> None:
        self._response = response
        self._error = error
        self._block = block
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._block:
            await asyncio.Event().wait()
        if self._error is not None:
            raise self._error
        if self._response is not None:
            return self._response
        return response_for(REPLY)


class StubClient:
    """Enough of AsyncOpenAI for the adapter; only the configured copy answers."""

    def __init__(self, responses: StubResponses) -> None:
        self._configured = responses
        self.responses: StubResponses | None = None
        self.option_calls: list[dict[str, Any]] = []

    def with_options(self, **options: Any) -> StubClient:
        self.option_calls.append(options)
        copy = StubClient(self._configured)
        copy.responses = self._configured
        return copy


def answerer(
    responses: StubResponses, **settings: Any
) -> tuple[OpenAIAnswerModel, StubClient]:
    client = StubClient(responses)
    model = OpenAIAnswerModel(
        client,  # pyright: ignore[reportArgumentType]
        AnswerSettings(**settings),
    )
    return model, client


async def test_the_request_sends_the_counted_prompt_and_its_schema() -> None:
    responses = StubResponses()
    model, client = answerer(responses, timeout_seconds=7.0)

    reply = await model.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=30)

    (call,) = responses.calls
    assert client.option_calls == [{"max_retries": 0}]
    assert call["model"] == "gpt-5.4-mini-2026-03-17"
    assert call["instructions"] == PROMPT.instructions
    assert call["input"] == PROMPT.input
    assert call["text"] == {
        "format": {
            "type": "json_schema",
            "name": "answer",
            "strict": True,
            "schema": PROMPT.response_schema(),
        }
    }
    assert call["max_output_tokens"] == 1_000
    assert call["store"] is False
    assert call["timeout"] == 7.0
    assert call["extra_headers"] == {"X-Request-ID": str(REQUEST_ID)}
    assert (reply.text, reply.refused) == (REPLY, False)
    assert (reply.usage.input_tokens, reply.usage.output_tokens) == (120, 12)
    assert reply.usage.price_id == "gpt-5.4-mini-2026-03-17"


async def test_a_refusal_is_reported_with_its_usage() -> None:
    model, _ = answerer(StubResponses(response=response_for(None)))

    reply = await model.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)

    assert (reply.text, reply.refused) == (None, True)
    assert reply.usage.input_tokens == 120


@pytest.mark.parametrize(
    "response",
    [
        response_for(REPLY, status="incomplete"),
        SimpleNamespace(
            status="completed",
            output="not a list",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        ),
        SimpleNamespace(
            status="completed",
            output=[SimpleNamespace(content=[SimpleNamespace(type="output_text")])],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        ),
    ],
    ids=["cut-off", "output-not-a-list", "text-missing"],
)
async def test_an_unreadable_reply_has_no_text_and_keeps_its_usage(
    response: object,
) -> None:
    model, _ = answerer(StubResponses(response=response))

    reply = await model.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)

    assert (reply.text, reply.refused) == (None, False)
    assert reply.usage.input_tokens >= 1


@pytest.mark.parametrize(
    "usage",
    [None, SimpleNamespace(input_tokens=-1, output_tokens=3)],
    ids=["missing", "negative"],
)
async def test_a_reply_without_usage_is_malformed(usage: object) -> None:
    response = SimpleNamespace(status="completed", output=[], usage=usage)
    model, _ = answerer(StubResponses(response=response))

    with pytest.raises(ChatModelMalformedResponse, match="no token usage"):
        await model.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)


def _connection_error() -> openai.APIConnectionError:
    request = httpx2.Request("POST", "https://api.openai.test", json={"t": MARKER})
    return openai.APIConnectionError(request=request)


def _timeout_error() -> openai.APITimeoutError:
    request = httpx2.Request("POST", "https://api.openai.test", json={"t": MARKER})
    return openai.APITimeoutError(request=request)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (status_error(400, body_text=MARKER), ChatModelRequestRefused),
        (status_error(401, body_text=MARKER), ChatModelRequestRefused),
        (status_error(408, body_text=MARKER), ChatModelTimeout),
        (status_error(409, body_text=MARKER), ChatModelUnavailable),
        (status_error(429, body_text=MARKER), ChatModelRateLimited),
        (status_error(500, body_text=MARKER), ChatModelUnavailable),
        (_connection_error(), ChatModelUnavailable),
        (_timeout_error(), ChatModelTimeout),
    ],
)
async def test_each_provider_failure_becomes_one_port_error_without_text(
    error: BaseException, expected: type[ChatModelError]
) -> None:
    model, _ = answerer(StubResponses(error=error))

    with pytest.raises(ChatModelError) as raised:
        await model.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)

    assert type(raised.value) is expected
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert MARKER not in str(raised.value)


async def test_a_stalled_provider_times_out_at_the_deadline() -> None:
    model, _ = answerer(StubResponses(block=True))

    with pytest.raises(ChatModelTimeout):
        await model.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=0.05)


@pytest.mark.parametrize(
    ("prompt", "timeout_seconds"),
    [
        (PROMPT, 0),
        (PROMPT, float("nan")),
        (PROMPT, True),
        (PROMPT, 10**400),
        ({"input": "x"}, 5),
    ],
    ids=["zero", "nan", "bool", "huge", "not-a-prompt"],
)
async def test_a_bad_request_is_refused_before_sending(
    prompt: object, timeout_seconds: float
) -> None:
    responses = StubResponses()
    model, _ = answerer(responses)

    with pytest.raises(InvalidAnswerRequest):
        await model.generate(
            prompt,  # type: ignore[arg-type]
            request_id=REQUEST_ID,
            timeout_seconds=timeout_seconds,
        )

    assert responses.calls == []


async def test_one_log_record_per_call_with_counts_and_tokens_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model, _ = answerer(StubResponses())
    failing, _ = answerer(StubResponses(error=status_error(500, body_text=MARKER)))

    await model.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)
    with pytest.raises(ChatModelUnavailable):
        await failing.generate(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)

    records = [r for r in caplog.records if r.message == "answer model call finished"]
    assert [r.__dict__["answer_model"]["outcome"] for r in records] == [
        "succeeded",
        "ChatModelUnavailable",
    ]
    assert records[0].__dict__["answer_model"]["input_tokens"] == 120
    assert MARKER not in caplog.text
    assert all(MARKER not in repr(r.__dict__) for r in records)


async def test_the_adapter_and_the_workflow_answer_together() -> None:
    model, _ = answerer(StubResponses())

    result = await generate_answer(
        QUESTION,
        EVIDENCE,
        AnswerSettings().budget,
        model,
        request_id=REQUEST_ID,
        timeout_seconds=5,
    )

    assert result.outcome is AnswerOutcome.ANSWERED
    assert [c.evidence_id.value for c in result.citations] == [CITED]


def test_the_settings_give_the_budget_and_the_price() -> None:
    settings = AnswerSettings()
    model, _ = answerer(StubResponses(), input_usd_per_million_tokens="1.5")

    assert settings.budget == ContextBudget(32_000, 1_000, 4_000)
    assert model.price.price_id == "gpt-5.4-mini-2026-03-17"
    assert str(model.price.input_usd_per_million_tokens) == "1.5"


@pytest.mark.parametrize(
    "setting",
    [
        {"timeout_seconds": 0},
        {"max_output_tokens": 0},
        {"context_tokens": 4_000, "history_tokens": 3_000},
        {"model": ""},
    ],
)
def test_settings_refuse_values_that_cannot_work(setting: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AnswerSettings(**setting)  # pyright: ignore[reportArgumentType]


def test_the_settings_are_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANSWER_CONTEXT_TOKENS", "16000")

    assert AnswerSettings().budget.context_tokens == 16_000


@pytest.mark.live
async def test_openai_live_answer_cites_the_evidence() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key is None:
        pytest.skip("OPENAI_API_KEY is required for the opt-in live test")

    client = AsyncOpenAI(api_key=api_key)
    settings = AnswerSettings()
    try:
        result = await generate_answer(
            QUESTION,
            EVIDENCE,
            settings.budget,
            OpenAIAnswerModel(client, settings),
            request_id=REQUEST_ID,
            timeout_seconds=30.0,
        )
    finally:
        await client.close()

    assert result.outcome is AnswerOutcome.ANSWERED
    assert [c.evidence_id.value for c in result.citations] == [CITED]
    assert "24" in result.text
    assert result.usage is not None
    assert 0 < result.usage.input_tokens < 2_000
```

`response_for` береться з `tests/ranking_stubs.py`, а `status_error` — з `tests/openai_stubs.py`; обидва додано в M05. `PROMPT` будується з бюджету типових налаштувань, тож тести адаптера надсилають справжній зібраний промпт.

Перевірка:

```bash
uv run pytest tests/test_generation.py tests/test_openai_answering.py -q
```

Очікувано: 80 passed, 1 deselected. Пропущений — це живий тест.

### Крок 6 — Без коду: зламайте і вирішіть

Цей крок показує, що стоїть між правильною перевіркою і цитатою тексту, якого модель не читала, і фіксує, чого ви навчилися.

**Дія 1:** навмисно зламайте перевірку. У `src/knowledge_service/answering/generation.py`, у `generate_answer`, замініть:

```python
            checked = checked_answer(reply.text, allocated.evidence)
```

на:

```python
            checked = checked_answer(reply.text, evidence)
```

Запустіть:

```bash
uv run pytest tests/test_generation.py tests/test_openai_answering.py -q
```

Очікувано: 1 failed: `test_evidence_dropped_by_the_budget_cannot_be_cited`. Усі інші тести проходять, бо в них усі Evidence вміщуються. З цією зміною модель могла б процитувати документ, який бюджет відкинув, і користувач побачив би джерело для тексту, якого модель не читала. Скасуйте зміну і запускайте знову, доки все не пройде.

**Дія 2 (необов'язково, потрібен API-ключ OpenAI):** запустіть живий тест, щоб перевірити, що суворий режим OpenAI приймає схему зі списком ID Evidence.

```bash
OPENAI_API_KEY=... uv run pytest tests/test_openai_answering.py -m live -q
```

Очікувано: 1 passed, 29 deselected. Якщо він падає з `ChatModelRequestRefused`, провайдер відхилив схему; запишіть це під `## Decision`.

**Дія 3:** під `## Decision` напишіть:

- чому відповіді мають власний порт замість зміни `ChatModel` з M01;
- чому зламана відповідь — це результат з usage, а таймаут — помилка;
- чому модель не викликається, коли не вміщується жоден Evidence;
- що лишається на наступні уроки: підтвердження тверджень (M06-T06), політика утримання і повідомлення користувачу (M06-T07), стримінг (M06-T08, M06-T09);
- щонайменше одне **спростоване припущення** у формі «Я припускав X. Запуск чи рев'ю показує Y. Отже, Z хибне».

### Крок 7 — Без коду: архітектура, перевірки, evidence і прогрес

Цей крок оновлює текст, який зміна зробила неповним, доводить, що все досі працює, і фіксує результат.

**Дія 1:** у `docs/architecture.md` додайте нові функції до рядка `answering`.

Знайдіть у `docs/architecture.md`:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, repeated, malformed, or mismatched references are refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), and `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts) |
```

Замініть на:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, repeated, malformed, or mismatched references are refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), and `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget) |
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_generation.py tests/test_openai_answering.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний запуск дав 80 passed для вузьких тестів і 1888 passed для `make test-unit`. Цей урок не чіпає коду бази даних, тож `make test-integration` має дати ті самі 329 passed, що й у M06-T03. Якщо він каже, що тестової бази немає, запустіть її командою `docker start knowledge-service-postgres`.

**Дія 3:** заповніть файл evidence:

- **Design:** змінені файли; новий порт і чому; що надсилає адаптер; правила `checked_answer`; п'ять результатів; налаштування і бюджет.
- **Verification:** команди і числа, навмисна поломка, живий запуск, якщо ви його робили, і відомі обмеження з розділу «Перед початком».
- **Review:** адверсаріальне рев'ю на понад 6 000 фазинг-відповідей не змогло змусити цитату вказати поза промпт чи зламати перевірку. Воно знайшло чотири зламані гарантії, кожну виправлено тестом, що падав до виправлення: невидимі відповіді й причини приймалися; величезний цілочисельний таймаут падав; власний `TimeoutError` моделі маскувався під наш і тягнув текст промпта; зібраний вручну usage чи ідентичність могли занести текст у лог.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте завдання як виконане, додайте рядок M06-T04 у `docs/curriculum/PROGRESS.md`, укажіть наступним завданням M06-T05 і зробіть коміт `m06-t04: generate a structured answer`.

## Запитання для рефлексії

1. Що `generate_answer` приховує від коду, який стримитиме відповідь у M06-T09? Перелічіть, чого тому коду ніколи не треба перевіряти.
2. Пізніша зміна хоче зекономити час і перевіряти цитати проти всіх знайдених Evidence, бо вони вже під рукою. Що пішло б не так для користувача і який один тест це спіймає?
3. Зламана відповідь повертає `INVALID` з вартістю, а таймаут піднімає помилку. Чому така різниця і хто використовує кожне з них?
4. Модель сказала «я не можу відповісти», але водночас процитувала документ. Наша перевірка відхиляє всю відповідь. Це надто суворо для користувача чи саме те, що треба? Чому?
5. Яку відповідь ви надіслали б наступною, щоб спробувати отримати цитату тексту, якого модель не бачила?

## Чекліст завершення

- [ ] Файл evidence містить інваріант і ваші передбачення, записані до коду.
- [ ] `ChatModelRequestRefused` існує, і `errors.py` відносить її до внутрішніх помилок.
- [ ] `has_visible_text` публічна в `prompt.py`, і перевірка запитання її використовує.
- [ ] `generate_answer` надсилає саме порахований промпт і повертає один із п'яти результатів.
- [ ] Цитати перевіряються проти Evidence у промпті, і відповідь потребує щонайменше однієї.
- [ ] Утримання потребує видимої причини і жодних цитат; відмова і невалідна відповідь не містять тексту моделі.
- [ ] Кожна оплачена відповідь зберігає usage; без Evidence модель не викликається.
- [ ] Збої провайдера піднімають підкласи `ChatModelError` без тексту запиту.
- [ ] Ви запустили навмисну поломку, побачили 1 failed і скасували її.
- [ ] Усі перевірки проходять: формат, лінтер, типи, unit, інтеграційні, документація.
- [ ] Evidence, статус завдання, рядок PROGRESS і коміт готові.

Коли напишете файли, поділіться ними або виводом помилок, і я перевірю їх за цим чеклістом.
