# M06-T09 — Передавайте відповіді потоком і скасовуйте їх

Англійська версія: [M06-T09-stream-and-cancel-answers.md](M06-T09-stream-and-cancel-answers.md)

Вихідне завдання: [M06-T09](../curriculum/milestones/M06-answers-and-conversations/M06-T09-stream-and-cancel-answers.md)

## Що ви зробите

Цей урок надсилає відповідь клієнту частинами, поки модель її ще пише, і зупиняє всю роботу тієї миті, коли клієнт іде. M06-T08 зафіксував, як виглядають події. Тепер їх надсилає справжній HTTP-ендпоінт: він знаходить Evidence, просить модель про потік, передає текст у міру надходження і завершує рішенням політики з M06-T07. Користувач бачить перші слова за секунду, а не чекає всієї відповіді, а закрита вкладка браузера більше не лишає модель генерувати і виставляти рахунок ні для кого.

- `AnswerStreamer` веде одну потокову відповідь: спершу Evidence, потім потік моделі, потім перевірки й рішення.
- `AnswerFieldReader` витягує текст відповіді з JSON моделі в міру надходження.
- `OpenAIAnswerModel.stream` просить у Responses API потік і закриває його за будь-якого виходу.
- `EventStreamResponse` і `POST /v1/answer/stream` надсилають події через HTTP і одразу помічають, що клієнт пішов.

| Завдання вимагає | Де це відбувається |
|---|---|
| Віддати SSE | Крок 5: `POST /v1/answer/stream` повертає `text/event-stream` |
| Зворотний тиск (backpressure) | Крок 3: кожна подія створюється лише тоді, коли сервер просить наступну; нічого не стоїть у черзі |
| Скасування клієнтом | Крок 5: `EventStreamResponse` слухає відключення і скасовує потік; крок 3 закриває потік моделі |
| Обробка дедлайну | Крок 3: один дедлайн охоплює пошук, потік моделі й перевірки; до першого байта це HTTP-помилка, після — подія `error` |
| Фінальний usage | Крок 3: оплачений usage надсилається перед завершальною подією, також перед `error` |
| Прибирання потоків провайдера | Крок 4: потік OpenAI закривається за будь-якого виходу, захищено й обмежено в часі |
| HTTP-тест відключається посеред потоку і доводить, що робота нагорі та спани завершуються | Крок 6: `test_a_client_that_leaves_midstream_stops_the_model_and_the_stream` |
| Жодного підкладеного тексту в телеметрії | Крок 6: підкладений маркер ніколи не потрапляє в запис логу чи repr |

Цей урок містить код: кроки 2–6 пишуть код. Кроки 1, 7 і 8 — без коду. Ключ моделі, мережа і база не потрібні; один live-тест запускається лише з `OPENAI_API_KEY`.

Терміни цього уроку:

- **Потокова передача (streaming):** надсилання відповіді частинами, поки її ще створюють, як читати лист сторінка за сторінкою, поки його пишуть.
- **Зворотний тиск (backpressure):** повільний читач гальмує того, хто пише, замість того щоб ненадіслані дані накопичувалися. Тут потік створює наступну подію лише тоді, коли сервер готовий її надіслати, тож повільний клієнт сповільнює потік моделі.
- **Скасування (cancellation):** зупинка роботи, яка більше нікому не потрібна. В asyncio задачу скасовують, кидаючи `CancelledError` там, де вона чекає.
- **Спан (span):** один запис про шматок роботи від початку до кінця. Тут це пара записів логу, «answer stream started» і «answer stream ended», доки M10 не принесе OpenTelemetry.
- **До і після заголовків:** щойно надіслано перший байт відповіді, її статус зафіксовано як 200. Збій до цього ще може бути 403 чи 503; збій після — лише подією `error`.

## Перед початком

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілий, коли ви до нього дійдете.

- **Що вже є.** `answering/events.py` пише і читає події. `answering/generation.py` перевіряє відповідь моделі в один результат. `answering/support.py` і `answering/abstention.py` перевіряють твердження і вирішують. `app.py` має `POST /v1/answer`, що відповідає одним шматком.
- **Пошук до першого байта.** `AnswerStreamer.open` знаходить і оцінює Evidence до початку відповіді. Тоді заборонений пошук — це ще 403, а пошук, що з'їв увесь дедлайн, — ще 503. Потоком іде лише частина моделі.
- **Тягнемо, а не штовхаємо.** Потік — це асинхронний генератор: він створює наступну подію лише тоді, коли сервер її просить, а сервер просить лише після того, як записав попередню. Між моделлю і клієнтом немає черги, тож повільний клієнт не може змусити пам'ять рости.
- **Текст зсередини JSON.** Модель відповідає у JSON-формі з M06-T04, і відповідь — її перше поле. `AnswerFieldReader` читає цей рядок у міру надходження і розкодовує екранування, тож `\n` стає переносом рядка, а `\ud83d\ude00` — емодзі, навіть коли шматок обривається посередині. За першої несподіванки він зупиняється, і текст надсилає вже кінець.
- **Переданий текст — це перевірений текст.** Коли відповідь повна, її перевіряють так само, як `generate_answer`. Якщо перевірена відповідь починається з переданого, решту досилають. Якщо ні — клієнт тримає текст, що не є відповіддю, тож потік утримується, і клієнт цей текст відкидає.
- **Рішення до цитат.** Текст може йти потоком до рішення; цитати — ніколи. Їх надсилають лише після того, як `AnswerFinisher` скаже `answer`, як вимагає M06-T08. `PolicyFinisher` перевіряє твердження і питає `decide`. Повторна перевірка цитат у базі (M06-T05) потребує особи читача, яка з'явиться в M07.
- **Один дедлайн.** `timeout_seconds` стартує разом із запитом і охоплює пошук, кожне очікування моделі й перевірки. Кожне очікування отримує час, що лишився, а не новий тайм-аут.
- **Відхід помічають одразу.** За ASGI 2.4 власна потокова відповідь Starlette помічає відключення лише тоді, коли падає її наступний запис. Потік, що чекає мовчазну модель, тоді працював би далі. `EventStreamResponse` завжди слухає відключення, один раз скасовує запис, а потім закриває потік, і це закриває потік моделі. Клієнт, що пішов під час пошуку, скасовує і пошук.
- **Закриття не можна обірвати чи завісити.** Потік моделі закривається під захистом від повторного скасування, тож власне прибирання провайдера, наприклад закриття його з'єднання, завершується. Закриття також обмежене `CLOSE_SECONDS`, тож зависле закриття не може тримати відповідь відкритою.
- **Кожен потік закінчується завершальною подією.** Після першого байта будь-який збій, навіть подія, яку відхилив писач, завершується подією `error`, а оплачений usage, якщо він є, надсилається перед нею.
- **Що знайшло рев'ю.** Змагальне рев'ю атакувало прототип 4 000 випадкових відповідей, відключеннями на кожному етапі за ASGI 2.3 і 2.4, фейковими і справжніми HTTP-потоками. Воно не знайшло способу, щоб клієнт залишив текст, що не є перевіреною відповіддю, і жодної цитати перед утриманням. Воно знайшло такі проблеми, кожну виправлено з тестом, що падає без виправлення:
  - Подія, яку писач відхилив після рішення, наприклад нечитабельний usage чи неправильне рішення, виривалася з потоку, і відповідь закінчувалася без завершальної події. Тепер ці події стоять у власному `try`, і відмова завершується подією `error`.
  - Закриття моделі, чиє прибирання зависло, тримало потік відкритим стільки, скільки тривало прибирання. Тепер кожне закриття обмежене `CLOSE_SECONDS`.
  - Клієнт, що пішов під час пошуку, не зупиняв його, і потік не лишав запису. Тепер пошук скасовується, а запис закінчується `cancelled`, також для потоку, закритого до першої події.
  - Скасований anyio-scope скасовує знову на кожному очікуванні, тож власне прибирання моделі обривалося. Тепер відповідь скасовує запис один раз звичайним asyncio, а кожне закриття захищене.
  - Відповідь, задовга для відповіді, зупиняла потік до того, як прийшов її usage. Тепер читач перестає читати текст, відповідь перевіряється як недійсна, і її usage надсилається.
  - Половина символу в сирому JSON закінчувала потік внутрішньою помилкою. Тепер читач на ній зупиняється, і потік утримується.
  - Repr запису містив переданий текст. Тепер він прихований.
- **Де живе код.** Новий `answering/streaming.py`; потоковий метод в `answering/openai_answering.py`; `reply_result`, винесений з `generate_answer` в `answering/generation.py`; ендпоінт в `app.py`.
- **Відомі обмеження.** `confirm_citations` поки ніхто не викликає, бо йому потрібна особа читача (M07); справжнього `EvidenceSource` теж поки немає, тож ендпоінт відповідає, лише коли застосунок зібрано з ним. Подія usage містить токени моделі, що відповідає, а не судді-моделі. Модель, чиє власне прибирання блокується після скасування, може пережити дедлайн на час цього прибирання; закриття OpenAI-адаптера обмежене. Live-тест запускається лише з `OPENAI_API_KEY`. M06-T13 зіставить кожен збій із точною порадою щодо повтору.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої передбачення

Поки коду ще немає, запишіть, що має бути правдою наприкінці уроку. Крок 7 перевіряє передбачення.

Створіть `docs/evidence/M06-T09-stream-and-cancel-answers.md`:

```markdown
# M06-T09 — Stream and cancel Answers evidence

## Invariant

`POST /v1/answer/stream` finds and judges Evidence before the first byte,
so its failures are plain HTTP errors. After it, the response is SSE in
the M06-T08 order: the answer's text as the model writes it, then, only
after the policy decides `answer`, the Citations; the billed usage before
the terminal event; and always exactly one terminal event, `completion`
or `error`. A client keeps text only when it is exactly the checked
Answer. One deadline covers the search, the model's stream, and the
checks. Each event is made only when the server asks for it. When the
client leaves, at any point, the work is cancelled, the model's stream is
closed within `CLOSE_SECONDS`, and the stream's record ends `cancelled`.
Records hold no Question, Evidence, or answer text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.streaming"`
fails with `ModuleNotFoundError`.

## Predictions

- What a client receives when the model has written half its Answer and the deadline runs out: ...
- What happens to the model's stream when the browser tab is closed while the model is still thinking, and how a test can prove it: ...
- How many tests fail if the endpoint uses Starlette's plain `StreamingResponse` instead of `EventStreamResponse`: ...

## Design

## Verification

## Decision

## Reflection
```

Перевірка: файл існує, перевірка, що має впасти, падає, а кожне передбачення — повне речення, яке крок 7 може спростувати.

### Крок 2 — Крок із кодом: спільна перевірка відповіді

Цей крок виносить з `generate_answer` код, що перетворює відповідь моделі на один результат, щоб потік перевіряв відповідь точно так само.

**Простими словами:** Зараз перевірка готової відповіді живе всередині функції, яка ще й викликає модель. Потік отримує відповідь інакше, частинами, але мусить судити її за тими самими правилами. Тож перевірка переїжджає у власну функцію, якою користуються обидва, і її результат нічим не змінюється.

| Функція | Що робить |
|---|---|
| `reply_result` | Перетворює одну відповідь на `REFUSED`, `INVALID` з дефектом, `ABSTAINED` чи `ANSWERED` з цитатами, зберігаючи usage |
| `generate_answer` | Викликає модель як раніше, потім використовує `reply_result` |

У `src/knowledge_service/answering/generation.py`:

Знайдіть:

```python
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

```

Замініть на:

```python
    reply = await _ask(model, allocated.prompt, request_id, timeout_seconds)
    result = reply_result(reply, allocated.evidence, context)
    _log(request_id, model, result)
    return result


def reply_result(
    reply: ModelReply, evidence: EvidenceSet, context: ContextReport
) -> AnswerResult:
    """The one outcome of a reply to a prompt built from `evidence`."""
    if reply.refused:
        return _result(AnswerOutcome.REFUSED, context, usage=reply.usage)
    try:
        checked = checked_answer(reply.text, evidence)
    except InvalidAnswer as error:
        return _result(
            AnswerOutcome.INVALID, context, usage=reply.usage, defect=error.defect
        )
    return AnswerResult(
        outcome=(
            AnswerOutcome.ABSTAINED if checked.abstained else AnswerOutcome.ANSWERED
        ),
        text=checked.text,
        reason=checked.reason,
        citations=checked.citations,
        defect=None,
        usage=reply.usage,
        context=context,
    )

```

Перевірка:

```bash
uv run pytest tests/test_generation.py -q
```

Очікувано: 51 passed, як і раніше.

### Крок 3 — Крок із кодом: стример

Цей крок пише модуль, що веде одну потокову відповідь від пошуку до останньої події.

**Простими словами:** Перш ніж щось надіслати, він знаходить Evidence і оцінює, чи його досить; будь-який збій тут — ще звичайна HTTP-помилка. Потім він просить у моделі потік і передає слова відповіді в міру надходження, по одній події, лише так швидко, як клієнт їх забирає. Коли модель закінчила, він перевіряє всю відповідь, дає політиці вирішити і лише тоді надсилає цитати й вартість. Якщо час вийшов чи щось зламалося, потік закінчується подією помилки; якщо клієнт пішов, він закриває потік моделі й зупиняється.

| Частина | Що містить чи робить |
|---|---|
| `StreamingAnswerModel` | Порт: шматки тексту відповіді, потім один `ModelReply` з усім текстом і usage; дострокове закриття закриває потік провайдера |
| `EvidenceSource`, `FoundEvidence` | Порт, що знаходить Evidence для запитання, із сигналами пошуку |
| `AnswerFinisher`, `FinishedAnswer`, `PolicyFinisher` | Хто вирішує щодо перевіреного результату; типовий перевіряє твердження і питає `decide` |
| `AnswerStreamer.open` | Робота до першого байта; повертає `AnswerStream` |
| `AnswerStreamer._events` | Події: дельти, потім рішення, цитати, usage і одна завершальна подія |
| `AnswerStream` | Події однієї відповіді; його закриття завершує запис, навіть до першої події |
| `AnswerFieldReader` | Читає рядок відповіді з JSON моделі в міру надходження |
| `_Span`, `_close`, `_before` | Запис одного потоку; захищене, обмежене закриття; один дедлайн на кожне очікування |

Створіть `src/knowledge_service/answering/streaming.py`:

```python
"""Streaming an Answer as SSE events, under one deadline, cancelled cleanly.

`AnswerStreamer.open` does the work that may still fail as a plain HTTP
error: it finds the Evidence and judges it, before any byte is sent. It
returns an `AnswerStream` of event bytes (see `answering.events`) that
asks the model, passes the Answer's text on as it arrives, and ends with
the policy's decision.

What the stream promises:

- **Pulled, never pushed.** Each event is made only when the server asks
  for the next one, so a slow reader slows the model's stream instead of
  filling a queue: that is the backpressure. Nothing is buffered but the
  few characters of an unfinished escape.
- **One deadline.** `timeout_seconds` covers finding Evidence, the model's
  whole stream, and the policy's checks. Before the first byte, running
  out is `AnswerDeadlineExceeded`; after it, the stream ends with an
  `error` event.
- **Text as it comes, the decision at the end.** The model writes JSON
  whose first field is the answer; its text is passed on piece by piece.
  The whole reply is then checked as `generate_answer` checks it, and the
  `AnswerFinisher` decides. The client keeps the text only if the decision
  is `answer`, and the streamed text is then exactly the checked text: a
  missing end is sent, and any other difference abstains.
- **Citations after the decision.** They are sent only once the decision
  is `answer`, so the title of a Document behind an Answer the policy
  rejects never leaves the server.
- **The usage once billed.** When the model's reply arrived, its usage is
  sent before the terminal event, also before an `error`, and also for a
  reply too long to be an Answer.
- **Always a terminal event.** A failure after the first byte, also an
  event the writer refuses, ends the stream with an `error` event.
- **Cancelled cleanly.** When the client goes away, the stream is
  cancelled or closed wherever it waits; it closes the model's stream at
  once, so the provider stops generating, and sends nothing more. The
  close is shielded from a second cancellation, so it is not cut short,
  and bounded by `CLOSE_SECONDS`, so a stuck close cannot hold it.
- **One record per stream.** "answer stream started" and "answer stream
  ended" hold the request ID, counts, tokens, timing, and how the stream
  ended: `completed`, `failed`, or `cancelled`, also when it ends before
  its first event. Never text.
"""

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Protocol, cast
from uuid import UUID

import anyio

from knowledge_service.answering.abstention import (
    AbstentionDecision,
    AbstentionPolicy,
    AbstentionReason,
    Decision,
    EvidenceSignals,
    Sufficiency,
    assess_evidence,
    decide,
)
from knowledge_service.answering.context_budget import (
    AllocatedContext,
    ContextBudget,
    allocate_context,
)
from knowledge_service.answering.events import AnswerEventWriter, InvalidEventStream
from knowledge_service.answering.evidence import EvidenceSet
from knowledge_service.answering.generation import (
    MAX_ANSWER_CHARACTERS,
    MAX_REPLY_CHARACTERS,
    AnswerOutcome,
    AnswerResult,
    InvalidAnswerRequest,
    ModelIdentity,
    ModelReply,
    positive_seconds,
    reply_result,
    well_formed_reply,
)
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.support import SupportJudge, check_support
from knowledge_service.contracts import Question, Usage
from knowledge_service.errors import (
    AnswerDeadlineExceeded,
    FailureKind,
    classify_failure,
)
from knowledge_service.language import Language
from knowledge_service.model import ChatModelMalformedResponse

logger = logging.getLogger(__name__)

# The start of the model's JSON, up to the answer's opening quote.
_OPENING = re.compile(r'\s*\{\s*"answer"\s*:\s*"')
_LONGEST_OPENING = 64
# One piece of a JSON string: plain text, an escape, or its closing quote.
_PIECE = re.compile(r'[^"\\]+|\\u[0-9a-fA-F]{4}|\\["\\/bfnrt]|"')
_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}
# How long closing the model's stream may take, even after the deadline.
CLOSE_SECONDS = 1.0
# The longest escape that may still be arriving: a surrogate pair.
_LONGEST_ESCAPE = 12


class StreamingAnswerModel(Protocol):
    """A chat model that streams its answer to an Evidence prompt."""

    @property
    def identity(self) -> ModelIdentity:
        """The provider and the pinned model."""
        ...

    def stream(
        self,
        prompt: EvidencePrompt,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> AsyncGenerator[str | ModelReply]:
        """Yield the reply's text in pieces as it arrives, then one `ModelReply`.

        The `ModelReply` holds the whole text and the billed usage. Failures
        raise a `ChatModelError`. Closing the generator early must close the
        provider's stream, so the provider stops generating.
        """
        ...


@dataclass(frozen=True, slots=True)
class FoundEvidence:
    """What retrieval found for one Question, and its signals."""

    evidence: EvidenceSet
    signals: EvidenceSignals


class EvidenceSource(Protocol):
    """Finds the Evidence the reader may see for one Question."""

    async def find(self, question: str, *, request_id: UUID) -> FoundEvidence: ...


@dataclass(frozen=True, slots=True)
class FinishedAnswer:
    """The checked result and the policy's decision about it."""

    result: AnswerResult
    decision: AbstentionDecision


class AnswerFinisher(Protocol):
    """Decides whether a checked result is shown to the client."""

    async def finish(
        self, result: AnswerResult, evidence: EvidenceSet, *, request_id: UUID
    ) -> FinishedAnswer: ...


class PolicyFinisher:
    """Check an Answer's claims, then let the abstention policy decide.

    Retrieval was already judged sufficient before the model was asked.
    """

    def __init__(
        self, judge: SupportJudge, policy: AbstentionPolicy, *, timeout_seconds: float
    ) -> None:
        if not positive_seconds(timeout_seconds):
            raise InvalidAnswerRequest("timeout_seconds must be a positive number")
        self._judge = judge
        self._policy = policy
        self._timeout_seconds = timeout_seconds

    async def finish(
        self, result: AnswerResult, evidence: EvidenceSet, *, request_id: UUID
    ) -> FinishedAnswer:
        if result.outcome is not AnswerOutcome.ANSWERED:
            decision = decide(Sufficiency.SUFFICIENT, result, None, None, self._policy)
            return FinishedAnswer(result, decision)
        report = await check_support(
            result,
            evidence,
            self._judge,
            request_id=request_id,
            timeout_seconds=self._timeout_seconds,
        )
        cited = EvidenceSet(
            tuple(
                item
                for citation in result.citations
                if (item := evidence.get(citation.evidence_id)) is not None
            )
        )
        decision = decide(Sufficiency.SUFFICIENT, result, cited, report, self._policy)
        return FinishedAnswer(result, decision)


@dataclass(frozen=True, slots=True)
class _Plan:
    """What `open` settled before the first byte."""

    request_id: UUID
    deadline: float
    language: Language
    sufficiency: Sufficiency
    context: AllocatedContext | None


class AnswerStreamer:
    """Answer one Question as a stream of SSE events."""

    def __init__(
        self,
        source: EvidenceSource,
        model: StreamingAnswerModel,
        finisher: AnswerFinisher,
        policy: AbstentionPolicy,
        budget: ContextBudget,
        *,
        timeout_seconds: float,
    ) -> None:
        if not positive_seconds(timeout_seconds):
            raise InvalidAnswerRequest("timeout_seconds must be a positive number")
        self._source = source
        self._model = model
        self._finisher = finisher
        self._policy = policy
        self._budget = budget
        self._timeout_seconds = timeout_seconds

    async def open(self, question: Question, *, request_id: UUID) -> AnswerStream:
        """Find and judge the Evidence, then return the stream of events.

        Raises before any byte is sent: `AnswerDeadlineExceeded` when finding
        Evidence takes the whole deadline, the source's own errors, and the
        errors of `assess_evidence` and `allocate_context`. Its record ends
        here as `failed` or `cancelled` when it does.
        """
        span = _Span(request_id)
        try:
            deadline = asyncio.get_running_loop().time() + self._timeout_seconds
            async with _before(deadline):
                found = await self._source.find(question.text, request_id=request_id)
            if type(found) is not FoundEvidence:
                raise InvalidAnswerRequest("the source must return FoundEvidence")
            sufficiency = assess_evidence(found.signals, self._policy)
            context = (
                allocate_context(question.text, found.evidence, self._budget)
                if sufficiency is Sufficiency.SUFFICIENT
                else None
            )
        except asyncio.CancelledError:
            span.end()
            raise
        except Exception as error:
            span.failure = type(error).__name__
            span.outcome = "failed"
            span.end()
            raise
        plan = _Plan(
            request_id=request_id,
            deadline=deadline,
            language=_language(question.text),
            sufficiency=sufficiency,
            context=context,
        )
        return AnswerStream(self._events(plan, span), span)

    async def _events(self, plan: _Plan, span: _Span) -> AsyncGenerator[bytes]:
        writer = AnswerEventWriter(plan.request_id)
        finished: FinishedAnswer | None = None
        failure: FailureKind | None = None
        try:
            yield writer.metadata()
            context = plan.context
            try:
                if context is not None and context.evidence.items:
                    reply: ModelReply | None = None
                    answer = AnswerFieldReader()
                    parts = self._model.stream(
                        context.prompt,
                        request_id=plan.request_id,
                        timeout_seconds=self._timeout_seconds,
                    )
                    try:
                        while reply is None:
                            async with _before(plan.deadline):
                                part = await anext(parts)
                            if type(part) is not str:
                                reply = _reply(part)
                                continue
                            text = answer.feed(part)
                            if text:
                                span.sent(text)
                                yield writer.text_delta(text)
                    finally:
                        await _close(parts)
                    span.usage = reply.usage
                    finished = await self._finished(plan, context, reply, span)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failure = classify_failure(error)
                span.failure = type(error).__name__
            decision = None if failure is not None else self._decision(plan, finished)
            try:
                if (
                    decision is not None
                    and finished is not None
                    and decision.decision is Decision.ANSWER
                ):
                    rest = finished.result.text[span.characters :]
                    if rest:
                        yield writer.text_delta(rest)
                        span.sent(rest)
                    for citation in finished.result.citations:
                        yield writer.citation(citation)
                        span.citations += 1
                if span.usage is not None:
                    yield writer.usage(span.usage)
                if decision is not None:
                    yield writer.completion(decision, plan.language)
                    span.outcome = "completed"
            except InvalidEventStream as error:
                # A refused event changes nothing, so an error can still end it.
                decision = None
                failure = FailureKind.INTERNAL
                span.failure = f"{type(error).__name__}:{error.defect.value}"
            if decision is None:
                yield writer.error(failure or FailureKind.INTERNAL)
                span.outcome = "failed"
        finally:
            span.end()

    async def _finished(
        self,
        plan: _Plan,
        context: AllocatedContext,
        reply: ModelReply,
        span: _Span,
    ) -> FinishedAnswer:
        result = reply_result(reply, context.evidence, context.report)
        streamed = "".join(span.text)
        if result.outcome is AnswerOutcome.ANSWERED and not result.text.startswith(
            streamed
        ):
            # The client holds text that is not the checked Answer.
            return FinishedAnswer(result, _abstain(self._policy))
        async with _before(plan.deadline):
            finished = await self._finisher.finish(
                result, context.evidence, request_id=plan.request_id
            )
        if (
            type(finished) is not FinishedAnswer
            or finished.result is not result
            or type(finished.decision) is not AbstentionDecision
            or (
                finished.decision.decision is Decision.ANSWER
                and result.outcome is not AnswerOutcome.ANSWERED
            )
        ):
            raise InvalidAnswerRequest("the finisher must decide about this result")
        return finished

    def _decision(
        self, plan: _Plan, finished: FinishedAnswer | None
    ) -> AbstentionDecision:
        if finished is not None:
            return finished.decision
        # No model was asked: nothing found, too weak, or nothing fits.
        sufficiency = plan.sufficiency
        if sufficiency is Sufficiency.SUFFICIENT:
            sufficiency = Sufficiency.EMPTY
        return decide(sufficiency, None, None, None, self._policy)


class AnswerStream:
    """The events of one Answer, and the record that ends with them.

    Iterate it once. Closing it closes the events and the model's stream,
    and ends the record as `cancelled`, also when no event was asked for.
    """

    def __init__(self, events: AsyncGenerator[bytes], span: _Span) -> None:
        self._events = events
        self._span = span

    def __aiter__(self) -> AnswerStream:
        return self

    async def __anext__(self) -> bytes:
        return await anext(self._events)

    async def aclose(self) -> None:
        try:
            await self._events.aclose()
        finally:
            self._span.end()


class AnswerFieldReader:
    """Read the answer's text out of the model's JSON as it arrives.

    The text is passed on only while the reply starts with the answer
    field and its escapes are well formed; at the first surprise, reading
    stops, and the checked text is sent at the end instead.
    """

    def __init__(self) -> None:
        self._pending = ""
        self._opened = False
        self._stopped = False
        self._read = 0
        self._characters = 0

    def feed(self, chunk: str) -> str:
        self._read += len(chunk)
        if self._read > MAX_REPLY_CHARACTERS:
            # Too long to be an Answer; the check at the end says so, billed.
            self._stop()
        if self._stopped:
            return ""
        self._pending += chunk
        if not self._opened:
            opening = _OPENING.match(self._pending)
            if opening is None:
                if len(self._pending) > _LONGEST_OPENING:
                    self._stop()
                return ""
            self._opened = True
            self._pending = self._pending[opening.end() :]
        text = self._decoded()
        if self._characters + len(text) > MAX_ANSWER_CHARACTERS:
            self._stop()
            return ""
        self._characters += len(text)
        return text

    def _decoded(self) -> str:
        out: list[str] = []
        position = 0
        pending = self._pending
        while position < len(pending) and not self._stopped:
            piece = _PIECE.match(pending, position)
            if piece is None:
                # An escape may still be arriving; anything longer is wrong.
                if len(pending) - position > _LONGEST_ESCAPE or not pending[
                    position
                ].startswith("\\"):
                    self._stop()
                break
            value = piece.group()
            if value == '"':
                self._stop()
                break
            if not value.startswith("\\"):
                # JSON text can carry half a character; no Answer holds one.
                if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
                    self._stop()
                    break
                out.append(value)
            elif value.startswith("\\u"):
                code = int(value[2:], 16)
                if 0xDC00 <= code <= 0xDFFF:
                    self._stop()
                    break
                if 0xD800 <= code <= 0xDBFF:
                    low = _PIECE.match(pending, piece.end())
                    if low is None:
                        if len(pending) - piece.end() >= 6:
                            self._stop()
                        break
                    second = low.group()
                    low_code = int(second[2:], 16) if second.startswith("\\u") else -1
                    if not 0xDC00 <= low_code <= 0xDFFF:
                        self._stop()
                        break
                    out.append(
                        chr(0x10000 + (code - 0xD800) * 0x400 + low_code - 0xDC00)
                    )
                    position = low.end()
                    continue
                out.append(chr(code))
            else:
                out.append(_ESCAPES[value[1]])
            position = piece.end()
        self._pending = "" if self._stopped else pending[position:]
        return "".join(out)

    def _stop(self) -> None:
        self._stopped = True
        self._pending = ""


@dataclass(slots=True)
class _Span:
    """The record of one stream: counts, tokens, timing, how it ended."""

    request_id: UUID
    started_at: float = field(default_factory=time.perf_counter)
    text: list[str] = field(default_factory=list[str], repr=False)
    characters: int = 0
    deltas: int = 0
    citations: int = 0
    usage: Usage | None = None
    failure: str | None = None
    outcome: str = "cancelled"
    ended: bool = False

    def __post_init__(self) -> None:
        logger.info(
            "answer stream started",
            extra={"answer_stream": {"request_id": str(self.request_id)}},
        )

    def sent(self, text: str) -> None:
        self.text.append(text)
        self.characters += len(text)
        self.deltas += 1

    def end(self) -> None:
        if self.ended:
            return
        self.ended = True
        fields: dict[str, object] = {
            "request_id": str(self.request_id),
            "outcome": self.outcome,
            "failure": self.failure,
            "duration_ms": round((time.perf_counter() - self.started_at) * 1_000),
            "text_deltas": self.deltas,
            "characters": self.characters,
            "citations": self.citations,
        }
        if self.usage is not None:
            fields["input_tokens"] = self.usage.input_tokens
            fields["output_tokens"] = self.usage.output_tokens
        level = logging.INFO if self.outcome == "completed" else logging.WARNING
        logger.log(level, "answer stream ended", extra={"answer_stream": fields})


async def _close(parts: AsyncGenerator[str | ModelReply]) -> None:
    """Close the model's stream, even while cancelled, within `CLOSE_SECONDS`."""
    # Shielded: a cancelled task would otherwise cut the model's own cleanup.
    with anyio.move_on_after(CLOSE_SECONDS, shield=True) as scope:
        try:
            await parts.aclose()
        except Exception as error:
            logger.warning(
                "model stream closed with an error",
                extra={"answer_stream": {"failure": type(error).__name__}},
            )
    if scope.cancelled_caught:
        logger.warning("model stream did not close in time")


@contextlib.asynccontextmanager
async def _before(deadline: float) -> AsyncGenerator[None]:
    """Run the block before `deadline`, or raise `AnswerDeadlineExceeded`."""
    scope = asyncio.timeout_at(deadline)
    expired = False
    try:
        async with scope:
            yield
    except TimeoutError:
        # Only the deadline's own timeout is ours; any other is a bug.
        if not scope.expired():
            raise
        expired = True
    if expired:
        raise AnswerDeadlineExceeded()


def _reply(part: object) -> ModelReply | None:
    """The final reply, None for a piece of text, or a malformed stream."""
    if type(part) is str:
        return None
    if not well_formed_reply(part):
        raise ChatModelMalformedResponse("the model streamed something else")
    return cast(ModelReply, part)


def _abstain(policy: AbstentionPolicy) -> AbstentionDecision:
    return AbstentionDecision(
        decision=Decision.ABSTAIN,
        reason=AbstentionReason.ANSWER_FAILED,
        policy_version=policy.version,
    )


def _language(text: str) -> Language:
    """Ukrainian when the Question has more Cyrillic letters than Latin ones."""
    cyrillic = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
    latin = sum(1 for c in text if c.isascii() and c.isalpha())
    return Language.UKRAINIAN if cyrillic > latin else Language.ENGLISH
```

Тут важливі три деталі. Кожен `yield` стоїть поза `_before`, обгорткою дедлайну, бо тайм-аут ніколи не має охоплювати час, за який клієнт читає. Потік моделі закривається у `finally`, тож дедлайн, помилка, скасування і закритий генератор — усі його закривають. А події після рішення стоять у власному `try`, тож навіть подія, яку відхилив писач, завершується подією `error`.

Перевірка:

```bash
uv run python -c "import knowledge_service.answering.streaming"
uv run pyright src/knowledge_service/answering/streaming.py
```

Очікувано: імпорт нічого не друкує, а pyright показує 0 помилок.

### Крок 4 — Крок із кодом: потік з OpenAI

Цей крок додає бік провайдера: метод, що просить у Responses API потік.

**Простими словами:** Адаптер уже надсилає один запит і чекає всієї відповіді. Тепер він може також просити відповідь частинами, передавати кожен шматок тексту, а наприкінці повертати всю відповідь з оплаченими токенами, прочитану тими самими помічниками, що й раніше. Хоч би що сталося, він закриває з'єднання з провайдером, тож провайдер перестає генерувати.

| Частина | Що робить |
|---|---|
| `_request` | Аргументи одного запиту, спільні для `generate` і `stream`, тож обидва надсилають те саме |
| `stream` | Видає кожен `response.output_text.delta`, потім один `ModelReply` з фінальної відповіді; зіставляє збої з помилками порту |
| `_CLOSE_SECONDS` | Скільки може тривати закриття потоку провайдера, під захистом від повторного скасування |

У `src/knowledge_service/answering/openai_answering.py`:

**1.** Знайдіть:

```python
import time
from decimal import Decimal
from typing import Self, cast
from uuid import UUID

import openai
from openai import AsyncOpenAI, omit
from openai.types.shared_params import Reasoning, ReasoningEffort
```

Замініть на:

```python
import time
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any, Self, cast
from uuid import UUID

import anyio
import httpx
import openai
from openai import AsyncOpenAI, AsyncStream, omit
from openai.types.responses import Response, ResponseStreamEvent
from openai.types.shared_params import Reasoning, ReasoningEffort
```

**2.** Знайдіть:

```python
PROVIDER = "openai"

```

Замініть на:

```python
PROVIDER = "openai"
# How long closing a stream may take, even for a cancelled caller.
_CLOSE_SECONDS = 1.0
# The events that end a stream with the whole response and its usage.
_FINAL_EVENTS = frozenset(
    {"response.completed", "response.incomplete", "response.failed"}
)

```

**3.** Знайдіть:

```python
        deadline = self._deadline(timeout_seconds)
        effort = self._settings.reasoning_effort
        started_at = time.perf_counter()
```

Замініть на:

```python
        deadline = self._deadline(timeout_seconds)
        started_at = time.perf_counter()
```

**4.** Знайдіть:

```python
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
```

Замініть на:

```python
            async with asyncio.timeout(deadline):
                # Unpacked arguments hide which overload is called: one
                # without streaming, which returns a Response.
                response = cast(
                    Response,
                    await self._client.responses.create(
                        **self._request(prompt, request_id, deadline)
                    ),
                )
```

**5.** Знайдіть:

```python
        return ModelReply(text=text, refused=refused, usage=usage)

```

Замініть на:

```python
        return ModelReply(text=text, refused=refused, usage=usage)

    async def stream(
        self,
        prompt: EvidencePrompt,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> AsyncGenerator[str | ModelReply]:
        """Stream the reply's text, then yield one `ModelReply` with its usage.

        The caller's deadline bounds the whole stream. Closing the generator
        early closes the HTTP response, so the provider stops generating.
        """
        if type(prompt) is not EvidencePrompt:
            raise InvalidAnswerRequest("prompt must be an EvidencePrompt")
        deadline = self._deadline(timeout_seconds)
        started_at = time.perf_counter()
        events: AsyncStream[ResponseStreamEvent] | None = None
        final: object = None
        failure: ChatModelError | None = None
        usage: Usage | None = None
        outcome = "cancelled"
        try:
            try:
                events = await self._client.responses.create(
                    **self._request(prompt, request_id, deadline), stream=True
                )
                async for event in events:
                    if event.type == "response.output_text.delta" and event.delta:
                        yield event.delta
                    elif event.type in _FINAL_EVENTS:
                        final = getattr(event, "response", None)
                        break
                    elif event.type == "error":
                        failure = ChatModelUnavailable()
                        break
            except (TimeoutError, openai.APIError) as error:
                failure = _translated(error)
            except ValueError:
                failure = ChatModelMalformedResponse("the stream was not readable")
            except httpx.HTTPError:
                # The connection broke while the stream was being read.
                failure = ChatModelUnavailable()
            latency_ms = _since(started_at)
            if failure is None and final is None:
                failure = ChatModelMalformedResponse("the stream ended without a reply")
            if failure is not None:
                outcome = type(failure).__name__
                # Raised outside the SDK's except block: its error holds the request.
                raise failure
            usage = self._usage(final, latency_ms)
            text, refused = _reply(final)
            outcome = (
                "refused"
                if refused
                else "succeeded"
                if text is not None
                else "unreadable"
            )
            reply = ModelReply(text=text, refused=refused, usage=usage)
        except ChatModelError as error:
            outcome = type(error).__name__
            raise
        finally:
            if events is not None:
                # Shielded: a cancelled caller must not cut the connection's
                # close short; bounded, so a stuck close cannot hang it.
                with anyio.move_on_after(_CLOSE_SECONDS, shield=True):
                    await events.close()
            _log(request_id, self._identity, _since(started_at), outcome, usage)
        yield reply

    def _request(
        self, prompt: EvidencePrompt, request_id: UUID, deadline: float
    ) -> dict[str, Any]:
        """The arguments of one Responses API request for this prompt."""
        effort = self._settings.reasoning_effort
        return {
            "model": self._settings.model,
            "instructions": prompt.instructions,
            "input": prompt.input,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "answer",
                    "strict": True,
                    "schema": prompt.response_schema(),
                }
            },
            "max_output_tokens": self._settings.max_output_tokens,
            "store": False,
            "timeout": deadline,
            "extra_headers": {"X-Request-ID": str(request_id)},
            "reasoning": omit if effort is None else Reasoning(effort=effort),
        }

```

Перевірка:

```bash
uv run pytest tests/test_openai_answering.py -q
uv run pyright src/knowledge_service/answering/openai_answering.py
```

Очікувано: 29 passed і 1 deselected, як і раніше, і 0 помилок.

### Крок 5 — Крок із кодом: ендпоінт

Цей крок виводить потік на HTTP.

**Простими словами:** Нова адреса приймає запитання і відповідає потоком подій. До першого байта вона чекає пошуку і зупиняє його, якщо клієнт іде. Після цього спеціальна відповідь надсилає події і весь час слухає, чи не пішов клієнт; коли він іде, вона зупиняє потік і стежить, щоб потік моделі закрився.

| Частина | Що робить |
|---|---|
| `EventStreamResponse` | Надсилає події, слухає відключення, один раз скасовує запис, потім закриває потік; кожне очікування обмежене |
| `_unless_client_leaves` | Виконує `open`, але скасовує його, коли клієнт іде раніше; його помилки лишаються звичайними HTTP-помилками |
| `POST /v1/answer/stream` | 503 без стримера, HTTP-помилка для збою до першого байта, інакше події |

У `src/knowledge_service/app.py`:

**1.** Знайдіть:

```python
import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from knowledge_service.contracts import Answer, ErrorResponse, Question
```

Замініть на:

```python
import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Coroutine
from uuid import UUID, uuid4

import anyio
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Receive, Scope, Send

from knowledge_service.answering.streaming import (
    CLOSE_SECONDS,
    AnswerStream,
    AnswerStreamer,
)
from knowledge_service.contracts import Answer, ErrorResponse, Question
```

**2.** Знайдіть:

```python

def create_app(
```

Замініть на:

```python

class EventStreamResponse(StreamingResponse):
    """An SSE response that stops its events the moment the client leaves.

    Under ASGI 2.4, Starlette notices a disconnect only when its next write
    fails, so a stream waiting on a silent model would run on. This
    response always listens for the disconnect. The writing task is then
    cancelled once, so the model's own cleanup is not cut short, and the
    events are closed, so the model's stream is closed even when they were
    waiting to be written. Each wait is bounded by `CLOSE_SECONDS`.
    """

    media_type = "text/event-stream"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        writing = asyncio.ensure_future(self._write(send))
        watching = asyncio.ensure_future(self.listen_for_disconnect(receive))
        try:
            await asyncio.wait({writing, watching}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            watching.cancel()
            writing.cancel()
            await asyncio.wait({writing, watching}, timeout=2 * CLOSE_SECONDS)
            close = getattr(self.body_iterator, "aclose", None)
            # A writing task still running holds the events; leave it be.
            if writing.done() and close is not None:
                # Shielded, so it runs even while cancelled; bounded, so a
                # stuck close cannot hold the response open.
                with anyio.move_on_after(2 * CLOSE_SECONDS, shield=True):
                    await close()
        if writing.done() and not writing.cancelled():
            writing.result()

    async def _write(self, send: Send) -> None:
        # Under ASGI 2.4, a write after the client left raises OSError.
        with contextlib.suppress(OSError):
            await self.stream_response(send)


async def _unless_client_leaves(
    request: Request, opening: Coroutine[object, object, AnswerStream]
) -> AnswerStream | None:
    """Await `opening`, or cancel it and return None when the client leaves.

    Its errors are raised as they are, so they still map to an HTTP error.
    """

    async def left() -> None:
        while (await request.receive())["type"] != "http.disconnect":
            pass

    work = asyncio.ensure_future(opening)
    watch = asyncio.ensure_future(left())
    try:
        await asyncio.wait({work, watch}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (watch, work):
            if not task.done():
                task.cancel()
        # Each task finishes its own cleanup, such as ending its record.
        await asyncio.wait({work, watch})
    if watch.cancelled():
        return work.result()
    if not work.cancelled() and work.exception() is None:
        await work.result().aclose()
    return None


def create_app(
```

**3.** Знайдіть:

```python
    workflow: AnswerWorkflow | None = None,
) -> FastAPI:
```

Замініть на:

```python
    workflow: AnswerWorkflow | None = None,
    streamer: AnswerStreamer | None = None,
) -> FastAPI:
```

**4.** Знайдіть:

```python

    return app
```

Замініть на:

```python

    @app.post("/v1/answer/stream")
    async def answer_stream(question: Question, request: Request) -> Response:
        request_id = request_id_from(request)
        if streamer is None:
            return error_response(
                status_code=503,
                code="workflow_unavailable",
                message="Answer workflow is unavailable",
                request_id=request_id,
            )
        try:
            events = await _unless_client_leaves(
                request, streamer.open(question, request_id=request_id)
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Nothing was sent yet, so a failure is still a plain HTTP error.
            return failure_response(kind=classify_failure(error), request_id=request_id)
        if events is None:
            # Nobody is left to read it; 499 marks the request in access logs.
            return Response(status_code=499)
        return EventStreamResponse(
            events,
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Request-ID": str(request_id),
            },
        )

    return app
```

Відповідь скасовує задачу запису один раз, звичайним asyncio, а не скасованим anyio-scope. Anyio-scope скасовує знову на кожному очікуванні всередині нього, а це обірвало б власне прибирання моделі. Одне скасування дає цьому прибиранню завершитися, а обмежене очікування не дає йому зависнути.

Перевірка:

```bash
uv run pyright src/knowledge_service/app.py
uv run pytest tests/test_app.py -q
```

Очікувано: 0 помилок, і наявні тести застосунку проходять як раніше.

### Крок 6 — Крок із кодом: тести потоку, дедлайнів і відходу клієнта

Цей крок доводить кожну обіцянку, для стримера і через HTTP.

**Простими словами:** Тести використовують фейкову модель, яка передає відповідь дрібними шматками, може замовкнути будь-коли і записує, чи її потік закрили і чи вона взагалі закінчила. Кожен потік вони читають назад читачем з M06-T08. HTTP-тести викликають застосунок так, як це робить сервер, і грають клієнта, що йде посередині, під час пошуку чи поки сервер застряг на записі.

| Тест | Що доводить |
|---|---|
| `test_a_client_rebuilds_the_checked_answer_from_the_stream` | Успіх: текст, цитати, usage і completion по порядку |
| `test_text_reaches_the_client_before_the_model_has_finished` | Текст іде потоком, поки модель ще працює |
| `test_streamed_text_that_is_not_the_checked_answer_abstains` | Найризиковіший край: клієнт ніколи не залишає текст, що не є перевіреною відповіддю |
| `test_an_unsupported_answer_is_withdrawn_and_sends_no_citation` | Цитати лише після рішення `answer` |
| `test_the_deadline_ends_the_stream_with_an_error_and_closes_the_model` | Дедлайн: подія `error`, і потік моделі закрито |
| `test_a_slow_policy_check_ends_in_an_error_after_the_usage` | Оплачений usage іде перед `error` |
| `test_a_failure_before_the_first_byte_is_a_plain_http_error` | Типізована відмова: 403 і 503 до заголовків |
| `test_a_client_that_leaves_midstream_stops_the_model_and_the_stream` | HTTP-тест завдання, за ASGI 2.3 і 2.4: модель не закінчує, її потік закрито, а запис закінчується `cancelled` |
| `test_a_client_that_leaves_during_the_search_cancels_it` | Відхід до першого байта скасовує пошук |
| `test_the_stream_record_holds_no_text` | Підкладений маркер ніколи не потрапляє в запис логу |
| `test_the_answer_field_is_decoded_however_the_json_is_cut` | Фазинг: будь-який текст, розрізаний будь-де, розкодовується в початок відповіді |
| `test_a_cancelled_caller_still_closes_the_provider_stream` | Потік OpenAI закривається навіть усередині скасованого scope |

Створіть `tests/test_streaming.py`:

```python
"""Streaming an Answer: deltas, the decision, the deadline, and cancellation."""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from starlette.types import Message

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.abstention import (
    POLICY_VERSION,
    AbstentionDecision,
    AbstentionPolicy,
    AbstentionReason,
    Decision,
    EvidenceSignals,
)
from knowledge_service.answering.events import (
    AnswerEventReader,
    StreamedAnswer,
    StreamOutcome,
)
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import ModelIdentity, ModelReply
from knowledge_service.answering.openai_answering import AnswerSettings
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.streaming import (
    CLOSE_SECONDS,
    AnswerFieldReader,
    AnswerFinisher,
    AnswerStreamer,
    FinishedAnswer,
    FoundEvidence,
    PolicyFinisher,
    _Span,  # pyright: ignore[reportPrivateUsage]
)
from knowledge_service.answering.support import LexicalSupportJudge
from knowledge_service.app import create_app
from knowledge_service.contracts import Question, Usage
from knowledge_service.errors import AnswerDeadlineExceeded, AuthorizationDenied
from knowledge_service.model import ChatModelError, ChatModelRateLimited
from knowledge_service.retrieval.reranking import RerankerIdentity

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000050")
QUESTION = Question(text="How many days of annual leave do I get?")
EVIDENCE = build_evidence(
    [candidate(1, text="Every employee receives 24 days of leave.")]
)
CITED = EVIDENCE.items[0].evidence_id.value
POLICY = AbstentionPolicy(reranker=None, min_top_score=None, min_supported_share=1.0)
SIGNALS = EvidenceSignals(eligible=1, top_score=None, reranker=None)
USAGE = Usage(
    provider="fake",
    model="fake-1",
    latency_ms=5,
    input_tokens=300,
    output_tokens=30,
    total_tokens=330,
)
ANSWER = "Every employee receives 24 days of leave."


def reply_json(answer: str = ANSWER, **fields: Any) -> str:
    body = {"answer": answer, "citations": [CITED], "abstained": False, "reason": None}
    body.update(fields)
    return json.dumps(body)


def pieces(text: str, size: int = 7) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)]


class Source:
    """Return fixed Evidence, or fail, or wait."""

    def __init__(
        self,
        found: object = None,
        *,
        error: BaseException | None = None,
        wait: bool = False,
    ) -> None:
        self.found = FoundEvidence(EVIDENCE, SIGNALS) if found is None else found
        self.error = error
        self.wait = wait
        self.cancelled = False

    async def find(self, question: str, *, request_id: UUID) -> FoundEvidence:
        if self.wait:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        if self.error is not None:
            raise self.error
        return self.found  # type: ignore[return-value]


class Model:
    """Stream `parts`, then a ModelReply of `final`; record how it ended.

    `hold` waits before that part until `release` is set; `error` is
    raised instead of the reply.
    """

    identity = ModelIdentity(provider="fake", model="fake-1")

    def __init__(
        self,
        parts: list[str] | None = None,
        *,
        final: str | None = None,
        hold: int | None = None,
        error: ChatModelError | None = None,
        close_delay: float = 0,
        usage: Usage = USAGE,
    ) -> None:
        self.final = reply_json() if final is None else final
        self.parts = pieces(self.final) if parts is None else parts
        self.hold = hold
        self.error = error
        self.close_delay = close_delay
        self.usage = usage
        self.release = asyncio.Event()
        self.waiting = asyncio.Event()
        self.calls = 0
        self.closed = False
        self.finished = False

    async def stream(
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> AsyncGenerator[str | ModelReply]:
        self.calls += 1
        try:
            for index, part in enumerate(self.parts):
                if index == self.hold:
                    self.waiting.set()
                    await self.release.wait()
                yield part
            if self.error is not None:
                raise self.error
            yield ModelReply(text=self.final, refused=False, usage=self.usage)
            self.finished = True
        finally:
            # A provider's close awaits too, such as closing its connection.
            if self.close_delay:
                await asyncio.sleep(self.close_delay)
            self.closed = True


class SlowFinisher:
    """Never decides."""

    async def finish(
        self, result: Any, evidence: Any, *, request_id: UUID
    ) -> FinishedAnswer:
        await asyncio.Event().wait()
        raise AssertionError


def streamer(
    model: Model,
    *,
    source: Source | None = None,
    finisher: AnswerFinisher | None = None,
    policy: AbstentionPolicy = POLICY,
    timeout_seconds: float = 5.0,
) -> AnswerStreamer:
    return AnswerStreamer(
        source or Source(),
        model,
        finisher or PolicyFinisher(LexicalSupportJudge(), policy, timeout_seconds=5),
        policy,
        AnswerSettings().budget,
        timeout_seconds=timeout_seconds,
    )


async def chunks(
    answering: AnswerStreamer, question: Question = QUESTION
) -> list[bytes]:
    events = await answering.open(question, request_id=REQUEST_ID)
    return [chunk async for chunk in events]


def rebuilt(sent: list[bytes]) -> StreamedAnswer:
    reader = AnswerEventReader()
    for chunk in sent:
        reader.feed(chunk)
    return reader.close()


def names(sent: list[bytes]) -> list[str]:
    return [chunk.split(b"\n")[1].decode().removeprefix("event: ") for chunk in sent]


# --- A complete stream ---------------------------------------------------------


async def test_a_client_rebuilds_the_checked_answer_from_the_stream() -> None:
    sent = await chunks(streamer(Model()))
    result = rebuilt(sent)

    assert result.outcome is StreamOutcome.ANSWERED
    assert result.text == ANSWER
    assert [c.evidence_id for c in result.citations] == [CITED]
    assert result.usage is not None
    assert result.usage.total_tokens == 330
    assert names(sent)[:2] == ["metadata", "text_delta"]
    assert names(sent)[-3:] == ["citation", "usage", "completion"]


async def test_text_reaches_the_client_before_the_model_has_finished() -> None:
    model = Model(hold=4)
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)

    first = [await anext(events) for _ in range(4)]
    assert names(first) == ["metadata", "text_delta", "text_delta", "text_delta"]
    assert not model.finished
    model.release.set()
    rest = [chunk async for chunk in events]

    assert rebuilt(first + rest).text == ANSWER


async def test_an_unsupported_answer_is_withdrawn_and_sends_no_citation() -> None:
    sent = await chunks(streamer(Model(final=reply_json("You get 99 days and a car."))))
    result = rebuilt(sent)

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.reason is AbstentionReason.UNSUPPORTED
    assert result.text == ""
    assert "citation" not in names(sent)
    assert result.usage is not None


async def test_the_models_own_abstention_abstains() -> None:
    final = reply_json("", citations=[], abstained=True, reason="Not in the documents.")

    result = rebuilt(await chunks(streamer(Model(final=final))))

    assert result.reason is AbstentionReason.INSUFFICIENT_EVIDENCE


async def test_no_evidence_abstains_without_asking_the_model() -> None:
    model = Model()
    empty = FoundEvidence(build_evidence([]), EvidenceSignals(0, None, None))

    sent = await chunks(streamer(model, source=Source(empty)))

    assert model.calls == 0
    assert names(sent) == ["metadata", "completion"]
    assert rebuilt(sent).reason is AbstentionReason.NO_EVIDENCE


async def test_weak_evidence_abstains_without_asking_the_model() -> None:
    reranker = RerankerIdentity(provider="local", model="bge-reranker-v2-m3")
    policy = AbstentionPolicy(reranker, min_top_score=0.5, min_supported_share=1.0)
    weak = FoundEvidence(EVIDENCE, EvidenceSignals(1, 0.1, reranker))
    model = Model()

    sent = await chunks(streamer(model, source=Source(weak), policy=policy))

    assert model.calls == 0
    assert rebuilt(sent).reason is AbstentionReason.WEAK_EVIDENCE


async def test_a_ukrainian_question_gets_a_ukrainian_abstention() -> None:
    empty = FoundEvidence(build_evidence([]), EvidenceSignals(0, None, None))
    question = Question(text="Скільки днів відпустки я маю?")

    sent = await chunks(streamer(Model(), source=Source(empty)), question)

    assert rebuilt(sent).message == (
        "Я не знайшов документів, які відповідають на це запитання."
    )


# --- The streamed text is the checked text -------------------------------------


async def test_a_reply_in_another_key_order_sends_the_text_at_the_end() -> None:
    final = json.dumps(
        {"citations": [CITED], "abstained": False, "reason": None, "answer": ANSWER}
    )

    sent = await chunks(streamer(Model(final=final)))

    assert names(sent) == [
        "metadata",
        "text_delta",
        "citation",
        "usage",
        "completion",
    ]
    assert rebuilt(sent).text == ANSWER


async def test_streamed_text_that_is_not_the_checked_answer_abstains() -> None:
    model = Model(parts=pieces(reply_json("You get 99 days.")), final=reply_json())

    result = rebuilt(await chunks(streamer(model)))

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.reason is AbstentionReason.ANSWER_FAILED


@settings(max_examples=200, deadline=None)
@given(
    st.text(min_size=1, max_size=60),
    st.booleans(),
    st.lists(st.integers(min_value=1, max_value=9), min_size=1, max_size=40),
)
def test_the_answer_field_is_decoded_however_the_json_is_cut(
    answer: str, ascii_only: bool, sizes: list[int]
) -> None:
    raw = json.dumps({"answer": answer, "citations": []}, ensure_ascii=ascii_only)
    field = AnswerFieldReader()
    out: list[str] = []
    position = 0
    for size in sizes * (len(raw) // len(sizes) + 1):
        if position >= len(raw):
            break
        out.append(field.feed(raw[position : position + size]))
        position += size

    decoded = "".join(out)
    assert answer.startswith(decoded)
    # Only a lone surrogate, which no valid Answer holds, stops it early.
    if not any(0xD800 <= ord(c) <= 0xDFFF for c in answer):
        assert decoded == answer


# --- Failures after the first byte ---------------------------------------------


async def test_the_deadline_ends_the_stream_with_an_error_and_closes_the_model() -> (
    None
):
    model = Model(hold=3)

    sent = await chunks(streamer(model, timeout_seconds=0.2))
    result = rebuilt(sent)

    assert result.outcome is StreamOutcome.FAILED
    assert result.error_code == "dependency_unavailable"
    assert result.text == ""
    assert model.closed
    assert not model.finished


async def test_a_slow_policy_check_ends_in_an_error_after_the_usage() -> None:
    sent = await chunks(streamer(Model(), finisher=SlowFinisher(), timeout_seconds=0.2))

    assert names(sent)[-2:] == ["usage", "error"]
    assert rebuilt(sent).usage is not None


async def test_a_provider_failure_mid_stream_is_a_typed_error_event() -> None:
    model = Model(error=ChatModelRateLimited())

    result = rebuilt(await chunks(streamer(model)))

    assert result.outcome is StreamOutcome.FAILED
    assert result.error_code == "capacity_exceeded"


async def test_a_reply_past_the_limit_abstains_and_keeps_its_usage() -> None:
    final = reply_json("x" * 50_000)
    model = Model(parts=pieces(final, 10_000), final=final)

    result = rebuilt(await chunks(streamer(model)))

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.reason is AbstentionReason.ANSWER_FAILED
    assert result.usage is not None


async def test_raw_half_a_character_abstains_instead_of_failing() -> None:
    lone = chr(0xD83D)
    final = '{"answer":"Leave' + lone + '","citations":[],"abstained":false}'

    result = rebuilt(await chunks(streamer(Model(final=final))))

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.reason is AbstentionReason.ANSWER_FAILED


class Deciding:
    """Return a fixed decision about whatever result it gets."""

    def __init__(self, decision: AbstentionDecision) -> None:
        self.decision = decision

    async def finish(
        self, result: Any, evidence: Any, *, request_id: UUID
    ) -> FinishedAnswer:
        return FinishedAnswer(result, self.decision)


@pytest.mark.parametrize(
    "decision",
    [
        AbstentionDecision(Decision.ABSTAIN, None, POLICY_VERSION),
        AbstentionDecision(
            Decision.ANSWER, AbstentionReason.ANSWER_FAILED, POLICY_VERSION
        ),
        AbstentionDecision(Decision.ANSWER, None, ""),
    ],
)
async def test_an_event_the_writer_refuses_still_ends_in_an_error(
    decision: AbstentionDecision,
) -> None:
    sent = await chunks(streamer(Model(), finisher=Deciding(decision)))

    assert names(sent)[-1] == "error"
    assert rebuilt(sent).outcome is StreamOutcome.FAILED


async def test_usage_a_client_cannot_read_ends_in_an_error() -> None:
    huge = USAGE.model_copy(update={"input_tokens": 2**53})

    sent = await chunks(streamer(Model(usage=huge)))

    assert names(sent)[-1] == "error"
    assert "usage" not in names(sent)


# --- Failures before the first byte --------------------------------------------


async def test_a_source_failure_is_raised_before_any_byte() -> None:
    answering = streamer(Model(), source=Source(error=AuthorizationDenied()))

    with pytest.raises(AuthorizationDenied):
        await answering.open(QUESTION, request_id=REQUEST_ID)


async def test_a_slow_source_runs_out_of_the_deadline_before_any_byte() -> None:
    answering = streamer(Model(), source=Source(wait=True), timeout_seconds=0.1)

    with pytest.raises(AnswerDeadlineExceeded):
        await answering.open(QUESTION, request_id=REQUEST_ID)


# --- Cancellation ---------------------------------------------------------------


async def test_closing_the_stream_closes_the_model_and_records_a_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model(hold=2)
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)
    await anext(events)
    await anext(events)

    await events.aclose()

    assert model.closed
    assert not model.finished
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


async def test_a_stream_closed_before_its_first_event_ends_its_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model()
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)

    await events.aclose()

    assert model.calls == 0
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


async def test_a_model_whose_close_never_ends_cannot_hold_the_stream() -> None:
    model = Model(hold=2, close_delay=60)
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)
    await anext(events)
    await anext(events)

    await asyncio.wait_for(events.aclose(), timeout=CLOSE_SECONDS + 1)

    assert not model.closed


async def test_cancelling_the_task_closes_the_model() -> None:
    model = Model(hold=2)

    async def consume() -> None:
        await chunks(streamer(model))

    task = asyncio.create_task(consume())
    await model.waiting.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert model.closed
    assert not model.finished


async def test_the_stream_record_holds_no_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    answer = f"Every employee receives 24 days of leave. {MARKER}"

    await chunks(streamer(Model(final=reply_json(answer))))

    assert caplog.records
    for record in caplog.records:
        assert MARKER not in record.getMessage()
        assert MARKER not in repr(record.__dict__)


def test_the_stream_record_hides_the_text_it_counts() -> None:
    span = _Span(REQUEST_ID)  # pyright: ignore[reportPrivateUsage]
    span.sent(MARKER)

    assert MARKER not in repr(span)


# --- Over HTTP -------------------------------------------------------------------


def client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


async def test_the_endpoint_streams_events_a_client_can_rebuild() -> None:
    app = create_app(streamer=streamer(Model()))
    async with client(app) as http:
        response = await http.post(
            "/v1/answer/stream",
            json={"text": QUESTION.text},
            headers={"X-Request-ID": str(REQUEST_ID)},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert rebuilt([response.content]).text == ANSWER


@pytest.mark.parametrize(
    ("answering", "status", "code"),
    [
        (None, 503, "workflow_unavailable"),
        ("denied", 403, "authorization_denied"),
        ("slow", 503, "dependency_unavailable"),
    ],
)
async def test_a_failure_before_the_first_byte_is_a_plain_http_error(
    answering: str | None, status: int, code: str
) -> None:
    sources = {
        "denied": Source(error=AuthorizationDenied()),
        "slow": Source(wait=True),
    }
    app = create_app(
        streamer=None
        if answering is None
        else streamer(Model(), source=sources[answering], timeout_seconds=0.1)
    )
    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": "Leave?"})

    assert response.status_code == status
    assert response.json()["code"] == code


async def test_an_empty_question_is_refused_before_any_work() -> None:
    model = Model()
    app = create_app(streamer=streamer(model))
    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": ""})

    assert response.status_code == 422
    assert model.calls == 0


class Connection:
    """An ASGI client that leaves after it has read `leave_after` bodies.

    With `stall`, it stops reading after the first body, so the server
    waits to write, then leaves.
    """

    def __init__(self, *, leave_after: int, stall: bool = False) -> None:
        self.leave_after = leave_after
        self.stall = stall
        self.bodies: list[bytes] = []
        self.left = asyncio.Event()
        self._asked = False

    async def receive(self) -> Message:
        if not self._asked:
            self._asked = True
            body = json.dumps({"text": QUESTION.text}).encode()
            return {"type": "http.request", "body": body, "more_body": False}
        await self.left.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: Message) -> None:
        if message["type"] != "http.response.body":
            return
        if self.stall and self.bodies:
            self.left.set()
            await asyncio.Event().wait()
        self.bodies.append(message["body"])
        if len(self.bodies) >= self.leave_after:
            self.left.set()


def scope(spec_version: str) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": spec_version},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/answer/stream",
        "raw_path": b"/v1/answer/stream",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1),
        "server": ("t", 80),
    }


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_a_client_that_leaves_midstream_stops_the_model_and_the_stream(
    spec_version: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    # The model goes quiet after two pieces and would never finish alone.
    model = Model(hold=2)
    connection = Connection(leave_after=2)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert model.closed
    assert not model.finished
    assert all(b"completion" not in body for body in connection.bodies)
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_a_model_that_awaits_while_closing_is_closed_when_the_client_leaves(
    spec_version: str,
) -> None:
    model = Model(hold=2, close_delay=0.05)
    connection = Connection(leave_after=2)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert model.closed
    assert not model.finished


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_a_client_that_leaves_during_the_search_cancels_it(
    spec_version: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    source = Source(wait=True)
    model = Model()
    connection = Connection(leave_after=0)
    connection.left.set()
    app = create_app(streamer=streamer(model, source=source))

    await asyncio.wait_for(
        app(scope(spec_version), connection.receive, connection.send), timeout=2
    )

    assert source.cancelled
    assert model.calls == 0
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["outcome"] == "cancelled"  # type: ignore[attr-defined]


async def test_a_stuck_close_cannot_hold_the_response_open() -> None:
    model = Model(hold=4, close_delay=60)
    connection = Connection(leave_after=100, stall=True)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send),
        timeout=2 * CLOSE_SECONDS + 1,
    )


async def test_a_client_that_stops_reading_and_leaves_closes_the_model() -> None:
    # The server is stuck writing the second event when the client leaves.
    model = Model(hold=4)
    connection = Connection(leave_after=100, stall=True)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send), timeout=2
    )

    assert model.closed
    assert not model.finished
```

Створіть `tests/test_openai_streaming.py`:

```python
"""The OpenAI adapter's stream: pieces, the final reply, failures, cleanup."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import anyio
import httpx
import pytest
from openai import AsyncOpenAI

from evidence_fakes import candidate
from knowledge_service.answering.context_budget import allocate_context
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import ModelReply
from knowledge_service.answering.openai_answering import (
    AnswerSettings,
    OpenAIAnswerModel,
)
from knowledge_service.model import (
    ChatModelError,
    ChatModelMalformedResponse,
    ChatModelRateLimited,
    ChatModelUnavailable,
)
from openai_stubs import status_error
from ranking_stubs import response_for

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000051")
QUESTION = "How many days of annual leave do I get?"
EVIDENCE = build_evidence(
    [candidate(1, text="Every employee receives 24 days of leave.")]
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


def delta(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def completed(text: str | None = REPLY) -> SimpleNamespace:
    return SimpleNamespace(type="response.completed", response=response_for(text))


class StubStream:
    """The SDK's AsyncStream: events in order, then `error`; records close."""

    def __init__(self, events: list[object], error: BaseException | None) -> None:
        self._events = events
        self._error = error
        self.closed = False

    def __aiter__(self) -> AsyncIterator[object]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[object]:
        for event in self._events:
            yield event
        if self._error is not None:
            raise self._error

    async def close(self) -> None:
        self.closed = True


class StubResponses:
    def __init__(
        self,
        events: list[object],
        *,
        error: BaseException | None = None,
        refuse: BaseException | None = None,
    ) -> None:
        self.stream = StubStream(events, error)
        self.refuse = refuse
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> StubStream:
        self.calls.append(kwargs)
        if self.refuse is not None:
            raise self.refuse
        return self.stream


class StubClient:
    def __init__(self, responses: StubResponses) -> None:
        self.responses = responses

    def with_options(self, **options: Any) -> StubClient:
        return self


def model_for(responses: StubResponses) -> OpenAIAnswerModel:
    return OpenAIAnswerModel(
        StubClient(responses),  # pyright: ignore[reportArgumentType]
        AnswerSettings(),
    )


async def drain(model: OpenAIAnswerModel) -> list[str | ModelReply]:
    parts = model.stream(PROMPT, request_id=REQUEST_ID, timeout_seconds=5)
    return [part async for part in parts]


async def test_the_stream_yields_text_pieces_then_the_billed_reply() -> None:
    responses = StubResponses([delta(REPLY[:10]), delta(REPLY[10:]), completed()])

    *texts, reply = await drain(model_for(responses))

    assert texts == [REPLY[:10], REPLY[10:]]
    assert isinstance(reply, ModelReply)
    assert reply.text == REPLY
    assert reply.usage.input_tokens == 120
    assert responses.calls[0]["stream"] is True
    assert responses.calls[0]["store"] is False
    assert responses.stream.closed


async def test_a_refusal_streams_no_text_and_says_refused() -> None:
    responses = StubResponses([completed(None)])

    (reply,) = await drain(model_for(responses))

    assert isinstance(reply, ModelReply)
    assert reply.refused


async def test_closing_the_stream_early_closes_the_provider_stream() -> None:
    responses = StubResponses([delta("{"), delta('"answer"'), completed()])
    parts = model_for(responses).stream(
        PROMPT, request_id=REQUEST_ID, timeout_seconds=5
    )

    await anext(parts)
    await parts.aclose()

    assert responses.stream.closed


@pytest.mark.parametrize(
    ("events", "error", "refuse", "expected"),
    [
        ([delta("{")], None, None, ChatModelMalformedResponse),
        ([delta("{"), SimpleNamespace(type="error")], None, None, ChatModelUnavailable),
        ([delta("{")], httpx.ReadError("gone"), None, ChatModelUnavailable),
        ([], None, status_error(429), ChatModelRateLimited),
        ([delta("{")], status_error(429), None, ChatModelRateLimited),
    ],
)
async def test_failures_become_port_errors_and_close_the_stream(
    events: list[object],
    error: BaseException | None,
    refuse: BaseException | None,
    expected: type[ChatModelError],
) -> None:
    responses = StubResponses(events, error=error, refuse=refuse)

    with pytest.raises(expected) as caught:
        await drain(model_for(responses))

    assert caught.value.__context__ is None
    assert responses.stream.closed or refuse is not None


class SlowClosingStream(StubStream):
    """A stream that stalls after its first event and awaits while closing."""

    async def _iterate(self) -> AsyncIterator[object]:
        yield delta("{")
        await asyncio.Event().wait()

    async def close(self) -> None:
        await asyncio.sleep(0)
        self.closed = True


async def test_a_cancelled_caller_still_closes_the_provider_stream() -> None:
    responses = StubResponses([])
    responses.stream = SlowClosingStream([], None)
    parts = model_for(responses).stream(
        PROMPT, request_id=REQUEST_ID, timeout_seconds=5
    )

    # The scope stays cancelled, so every await inside it is cancelled again.
    with anyio.move_on_after(0.05):
        async for _ in parts:
            pass

    assert responses.stream.closed


@pytest.mark.live
async def test_openai_live_stream_answers_in_pieces() -> None:
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key is None:
        pytest.skip("OPENAI_API_KEY is required for the opt-in live test")

    client = AsyncOpenAI(api_key=api_key)
    try:
        parts = await drain(OpenAIAnswerModel(client, AnswerSettings()))
    finally:
        await client.close()

    *texts, reply = parts
    assert isinstance(reply, ModelReply)
    assert len(texts) > 1
    assert "".join(str(t) for t in texts) == reply.text
    assert "24" in str(reply.text)
```

Перевірка:

```bash
uv run pytest tests/test_streaming.py tests/test_openai_streaming.py -q
```

Очікувано: 49 passed, 1 deselected (live-тест).

### Крок 7 — Без коду: зламайте і вирішіть

Цей крок показує, чому ендпоінту потрібен власний клас відповіді, і фіксує, чого ви навчилися.

**Дія 1:** навмисно зламайте ендпоінт. У `src/knowledge_service/app.py`, в `answer_stream`, замініть:

```python
        return EventStreamResponse(
            events,
```

звичайною відповіддю Starlette:

```python
        return StreamingResponse(
            events,
            media_type="text/event-stream",
```

Запустіть:

```bash
uv run pytest tests/test_streaming.py -q
```

Очікувано: 5 failed, 35 passed, і запуск повільніший, ніж зазвичай (від 13 до 70 секунд в еталонних запусках), бо зависле закриття тепер чекає тайм-ауту свого тесту. За ASGI 2.4 клієнта, що пішов, поки модель мовчить, ніхто не помічає, модель, що чекає під час закриття, обривають, а клієнт, що перестав читати, тримає потік: відповідь Starlette помічає відключення лише на наступному записі і скасовує anyio-scope. Поверніть рядки і запускайте знову, доки все не пройде.

**Дія 2:** під `## Decision` напишіть:

- чому пошук іде до першого байта, а модель — після;
- чому потік тягнуть, і чого коштувала б черга між моделлю і клієнтом;
- чому клієнт залишає текст лише тоді, коли це точно перевірена відповідь;
- чому відповідь сама слухає відключення і чому скасовує лише раз;
- що лишається на потім: повторна перевірка цитат з особою читача (M07), справжній `EvidenceSource` і карта збоїв з M06-T13;
- щонайменше одне **спростоване припущення** у формі «Я припускав X. Запуск чи рев'ю показує Y. Отже, Z хибне».

### Крок 8 — Без коду: архітектура, перевірки, evidence і прогрес

Цей крок оновлює текст, який зміна зробила неповним, доводить, що все досі працює, і фіксує результат.

**Дія 1:** у `docs/architecture.md` додайте стример до рядка `answering`:

Знайдіть:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), and `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages) |
```

Замініть на:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`) |
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_streaming.py tests/test_openai_streaming.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний запуск дав 2170 passed для `make test-unit`, на 49 більше, ніж у M06-T08. Цей урок не чіпає код бази, тож `make test-integration` має дати ті самі 342 passed. Якщо він каже, що тестової бази немає, запустіть її командою `docker start knowledge-service-postgres`.

**Дія 3:** заповніть файл evidence:

- **Design:** змінені файли; що відбувається до і після першого байта; як текст читається з JSON і звіряється з перевіреною відповіддю; порядок подій; дедлайн; як помічають відхід і закривають потік моделі; запис одного потоку.
- **Verification:** команди й кількості, навмисний злам і відомі обмеження з «Перед початком».
- **Review:** змагальне рев'ю з 4 000 випадкових відповідей і відключеннями на кожному етапі за ASGI 2.3 і 2.4 не знайшло способу залишити текст, що не є перевіреною відповіддю, і знайшло сім проблем, кожну виправлено з тестом, що падав до виправлення (15 тестів): відхилена після рішення подія не лишала завершальної події; зависле закриття моделі тримало потік; відхід під час пошуку не зупиняв його і не лишав запису; скасований anyio-scope обривав прибирання моделі; задовга відповідь губила usage; половина символу давала внутрішню помилку; repr запису містив текст.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу виконаною, додайте рядок M06-T09 у `docs/curriculum/PROGRESS.md`, поставте наступною задачу M06-T10 і зробіть коміт з `m06-t09: stream and cancel answers`.

## Запитання для рефлексії

1. Що `AnswerStreamer` приховує від HTTP-ендпоінта? Перелічіть, чого ендпоінту ніколи не треба знати про модель, Evidence чи політику.
2. Хтось хоче спершу прочитати весь потік моделі у список, а потім надіслати, «щоб код був простішим». Що помітив би користувач і що сталося б з пам'яттю та скасуванням?
3. Яку обіцянку цього уроку найлегше випадково зламати, коли M06-T13 додаватиме поради щодо повтору в події помилок, і який тест це впіймає?
4. Потік утримується, коли переданий текст не є початком перевіреної відповіді. Коли таке може статися зі справжньою моделлю і що бачить користувач?
5. Як би ви показали на запущеному сервері зі справжньою моделлю, що закриття вкладки браузера зупиняє генерацію у провайдера? На що б ви дивилися?

## Чекліст завершення

- [ ] Файл evidence містить інваріант і ваші передбачення, записані до коду.
- [ ] `reply_result` перевіряє відповідь, і `generate_answer` його використовує.
- [ ] `AnswerStreamer.open` знаходить і оцінює Evidence до першого байта; його збої — HTTP-помилки.
- [ ] Текст іде потоком, поки модель пише, і клієнт залишає його, лише коли це точно перевірена відповідь.
- [ ] Цитати йдуть лише після рішення `answer`; оплачений usage — перед завершальною подією.
- [ ] Кожен розпочатий потік закінчується рівно одним `completion` або `error`.
- [ ] Один дедлайн охоплює пошук, потік моделі й перевірки.
- [ ] Клієнт, що пішов у будь-який момент, скасовує роботу і закриває потік моделі в межах `CLOSE_SECONDS`.
- [ ] `OpenAIAnswerModel.stream` закриває потік провайдера за будь-якого виходу.
- [ ] Жоден текст запитання, Evidence чи відповіді не потрапляє в запис логу чи repr.
- [ ] Ви запустили навмисний злам, побачили, що тести падають, і скасували його.
- [ ] Усі перевірки проходять: форматування, лінтер, типи, юніт, інтеграційні, документація.
- [ ] Evidence, статус задачі, рядок PROGRESS і коміт готові.

Коли напишете файли, поділіться ними чи виводом помилок, і я перевірю їх за цим чеклістом.
