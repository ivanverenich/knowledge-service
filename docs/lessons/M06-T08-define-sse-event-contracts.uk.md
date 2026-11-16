# M06-T08 — Визначте контракти SSE-подій

Англійська версія: [M06-T08-define-sse-event-contracts.md](M06-T08-define-sse-event-contracts.md)

Вихідне завдання: [M06-T08](../curriculum/milestones/M06-answers-and-conversations/M06-T08-define-sse-event-contracts.md)

## Що ви зробите

Цей урок фіксує, які саме повідомлення сервер надсилає, поки передає відповідь потоком, і в якому порядку вони мусять іти. У M06-T09 відповідь піде в браузер частинами, у міру того як модель її пише. Перед цим обидві сторони мають домовитися про один формат: що містить кожне повідомлення, яке може йти після якого і яке завершує потік. Ви запишете цей формат як код: писач для сервера і читач, який відновлює відповідь так, як це зробив би клієнт.

- `AnswerEventWriter` перетворює кожен крок відповіді на одну подію і відмовляє події не в тому порядку.
- `AnswerEventReader` читає байти в міру надходження, перевіряє кожне правило і відновлює `StreamedAnswer`.
- Обидва мають спільний набір правил, тож усе, що надсилає писач, читач приймає.

| Завдання вимагає | Де це відбувається |
|---|---|
| Події metadata, text delta, citation, usage, completion і error | Крок 2: одна модель даних на подію в `answering/events.py` |
| Інваріанти порядку | Крок 2: `_AFTER`, таблиця того, яка подія може йти після якої |
| Інваріанти завершальної події | Крок 2: `completion` або `error` завершує потік; після нього нічого не може йти, а потік без нього обірваний |
| Тести парсера відхиляють неправильний порядок | Крок 3: переставлені, загублені, повторені, запізнілі й відсутні події відхиляються |
| Клієнт відновлює повну відповідь | Крок 3: читач відновлює текст, цитати й usage, хоч би як поділили байти |
| Детерміновані тести: успіх, найризиковіший край, типізована відмова | Крок 3: повна відповідь; цитати перед утриманням; `InvalidEventStream` з названим дефектом |
| Жодного підкладеного тексту в телеметрії | Крок 3: підкладений маркер ніколи не з'являється в помилці чи repr |

Цей урок містить код: кроки 2 і 3 пишуть код. Кроки 1, 4 і 5 — без коду. Модель, мережа і база не потрібні.

Терміни цього уроку:

- **SSE (Server-Sent Events):** простий текстовий формат, яким сервер надсилає повідомлення через одну відкриту HTTP-відповідь. Кожне повідомлення — кілька рядків `name: value` і порожній рядок. Браузери читають його через `EventSource`.
- **Подія (event):** одне таке повідомлення. Тут кожна має рядок `id:` (свій номер), рядок `event:` (свій вид) і рядок `data:` з одним JSON-об'єктом.
- **Дельта тексту (text delta):** наступний шматок тексту відповіді. Склеєні по порядку, дельти дають увесь текст, як сторінки факсу.
- **Завершальна подія (terminal event):** подія, що закінчує потік: `completion` (ось рішення) або `error` (не вдалося). Потік, що зупинився без неї, обірвано.
- **Відновлення (reconstruction):** що клієнт робить з подіями: склеює текст, збирає цитати й наприкінці вирішує, що показати.

## Перед початком

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілий, коли ви до нього дійдете.

- **Що вже є.** `answering/generation.py` дає текст відповіді та її usage. `answering/citations.py` перетворює цитату на `CitationPayload`, її JSON-форму для клієнта. `answering/abstention.py` дає остаточне рішення, причину і одне фіксоване повідомлення на кожну причину. `errors.py` зіставляє кожен вид збою з публічним кодом і фіксованим повідомленням.
- **Порядок одного потоку.** `metadata` іде першою, рівно один раз. Далі дельти тексту, потім цитати, потім usage не більше одного разу, потім одна завершальна подія. Як шаблон: `metadata text_delta* citation* usage? (completion | error)`. Фіксований порядок означає, що клієнт може показувати текст у міру надходження і все одно наприкінці знати, що нічого не бракує.
- **Завершальна подія вирішує, що клієнт залишає.** M06-T09 може передавати текст ще до того, як політика вирішила. Тож `completion` з `answer` залишає текст і цитати. `completion` з `abstain` замінює весь переданий текст фіксованим повідомленням причини, а `error` замінює його фіксованим повідомленням коду. Клієнт ніколи не має залишати текст, який пізніша подія забирає назад.
- **Цитати лише після рішення відповідати.** Цитата показує назву Документа, заголовок і посилання. Якщо відповідь потім відкликано, бо процитований Документ став прихованим, ці назви не мають дійти до клієнта. Тож сервер мусить вирішити ще до того, як надішле хоч одну цитату. Контракт забезпечує це там, де може: утримання після цитати відхиляється, тож сервер, що надсилає цитати зарано, не проходить тестів. Він не може зупинити сервер, який надіслав цитату, а потім помилку, бо збій може статися будь-коли.
- **Відповідь має бути цілою.** `completion` з `answer` потребує видимого тексту, щонайменше однієї цитати і usage. Модель завжди виставляє рахунок за відповідь, тож відсутній usage означає загублену подію. Утримання може прийти без usage, бо «нічого не знайдено» модель не викликає.
- **Повідомлення фіксовані, а не довільний текст.** Читач приймає повідомлення утримання, лише якщо це фіксоване повідомлення його причини, англійською чи українською. Помилку він приймає лише з відомим публічним кодом і фіксованим повідомленням цього коду. Тож ні баг сервера, ні проксі не можуть протягнути в повідомлення текст Документа.
- **Нумеровані події.** Ідентифікатори подій рахують від 0 з кроком один. Загублена чи повторена подія збиває лічбу, і читач відхиляє її з `GAP`. Ці ідентифікатори також дозволять M06-T09 пізніше відновити потік, бо SSE надсилає останній побачений ідентифікатор, коли браузер перепідключається.
- **Один набір правил для обох сторін.** Писач і читач пропускають кожну подію через той самий приватний об'єкт `_Rules`. Писач перевіряє подію, перш ніж щось змінити, тож відхилена подія лишає потік як був, і сервер ще може завершити його помилкою.
- **Суворий формат, одне написання.** Писач надсилає лише закінчення рядків `\n`, а JSON екранує кожен перенос рядка всередині значення, тож один рядок `data:` завжди містить один цілий JSON-об'єкт. Читач — еталонний клієнт цього формату, тож приймає подію лише в тих самих байтах, які надіслав би писач. Він відхиляє повернення каретки, мітку порядку байтів, четвертий рядок, зайвий пробіл, екрановану літеру чи повторений JSON-ключ. Він приймає рядки-коментарі з `:` між подіями, які M06-T09 може надсилати, щоб тихе з'єднання не закрилося.
- **Значення такі, як їх робить сервер.** Цитата має називати свій Evidence як `E` і 8 шістнадцяткових цифр, а свій Документ — як звичайний UUID, саме так, як їх пише сервер. Кількість токенів лишається нижче 2**53, найбільшого цілого числа, яке JavaScript-клієнт читає точно.
- **Обмежено.** Текст містить щонайбільше `MAX_ANSWER_CHARACTERS`, той самий ліміт, що й у перевіреної відповіді. Цитат щонайбільше `MAX_EVIDENCE`, а одна ціла подія містить щонайбільше `MAX_EVENT_CHARACTERS`, з обох боків. Читач тримає рядок, що ще надходить, шматками і склеює їх лише в його кінці, тож рядок, надісланий по одному байту, коштує часу пропорційно своїй довжині.
- **Чому формат пишемо самі.** FastAPI має власну підтримку SSE, але цьому урокові вебсервер не потрібен. Простий модуль можна перевірити байт у байт, а M06-T09 надішле його байти через той клас відповіді, який обере.
- **Що знайшло рев'ю.** Змагальне рев'ю атакувало прототип 3 000 випадкових послідовностей викликів писача і 60 000 пошкоджених потоків. Воно знайшло такі проблеми, кожну виправлено з тестом, що падає без виправлення:
  - Цитата чи рішення з полями не того типу або кількість токенів, задовга для JSON, валили писача нетипізованою помилкою. Тепер кожна побудова й кодування захищені, і писач кидає `InvalidEventStream`.
  - Docstring обіцяв, що назви відкликаної відповіді ніколи не доходять до клієнта, але цитата, а за нею помилка, дозволені. Тепер обіцянка каже те, що контракт справді забезпечує, і сервер мусить вирішити першим.
  - Читач обмежував кожен рядок, а не кожну подію, тож подія могла бути більшою, ніж дозволяє писач. Тепер читач рахує всю подію.
  - Читач приймав цитату з вигаданим ідентифікатором Evidence чи Документа. Тепер обидва мають виглядати точно так, як їх пише сервер.
  - Читач приймав той самий JSON, написаний інакше, з пробілами чи екранованими літерами. Тепер він приймає лише точні байти писача.
  - Кількість токенів понад 2**53 приймалася, хоча JavaScript-клієнт не може прочитати її точно. Тепер її відхиляють обидві сторони.
  - Ті самі зламані байти давали різні дефекти залежно від того, як їх поділили. Тепер повні рядки перевіряються раніше за недописаний.
- **Де живе код.** `answering/events.py` і `tests/test_answer_events.py`. Жоден наявний файл не змінюється, крім одного рядка `docs/architecture.md`.
- **Відомі обмеження.** Ці події поки ніхто не надсилає; це зробить M06-T09. Читач відновлює відповідь лише з цілого потоку; відновлення після перепідключення лишаємо на потім. Формат має версію `1`; клієнт, що зустріне іншу версію, відхиляє потік. Повідомлення відмови тихе, але її traceback усе ще тримає байти потоку в локальних змінних, тож логер, що записує локальні змінні, не має її бачити. Зайве повернення каретки після завершальної події позначається як `MALFORMED` або `AFTER_TERMINAL` залежно від того, де розрізали байти.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої передбачення

Поки коду ще немає, запишіть, що має бути правдою наприкінці уроку. Крок 4 перевіряє передбачення.

Створіть `docs/evidence/M06-T08-sse-event-contracts.md`:

```markdown
# M06-T08 — SSE event contracts evidence

## Invariant

A streamed Answer is `metadata text_delta* citation* usage?` followed by
exactly one terminal event, `completion` or `error`, with event IDs
counting up from 0 by one. Nothing follows the terminal event, and a
stream without one is cut off. `AnswerEventWriter` refuses any event the
rules forbid and leaves the stream unchanged; `AnswerEventReader` refuses
a stream out of order, with a lost or repeated event, cut off, malformed,
or over its limits, with a typed `InvalidEventStream`. A `completion`
with `answer` needs visible text, a Citation, and the usage; Citations
never precede an abstention; abstention and error messages are the fixed
messages of their reason or code. Whatever the writer sends, the reader
accepts and rebuilds into the same Answer, however the bytes are split.
Errors and reprs hold no text from the stream.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.events"`
fails with `ModuleNotFoundError`.

## Predictions

- What a client shows when the server streamed text and then sent `completion` with `abstain`: ...
- What the reader says when the connection drops after the usage event, before the terminal event: ...
- How many tests fail if the reader stops refusing Citations before an abstention, and why that rule matters: ...

## Design

## Verification

## Decision

## Reflection
```

Перевірка: файл існує, перевірка, що має впасти, падає, а кожне передбачення — повне речення, яке крок 4 може спростувати.

### Крок 2 — Крок із кодом: контракт подій

Цей крок пише модуль, що визначає кожну подію, їхній порядок і обидві сторони потоку.

**Простими словами:** Цей файл — домовлена мова між сервером і клієнтом, поки відповідь передається потоком. Писач перетворює кожен крок відповіді на коротке текстове повідомлення і відмовляється надсилати його не в тому порядку. Читач бере байти в міру надходження, перевіряє ті самі правила і наприкінці каже, що клієнт має показати: відповідь з її цитатами, фіксоване «не можу відповісти» чи фіксоване повідомлення про помилку.

| Частина | Що містить чи робить |
|---|---|
| `EventName` і шість моделей `...Data` | Види подій і JSON кожної; `citation` перевикористовує `CitationPayload` |
| `_AFTER` | Для кожної події: після яких етапів вона може йти і на який етап переводить потік |
| `_Rules` | Спільні перевірки: порядок, нумерація, ліміти, ціла відповідь, фіксовані повідомлення |
| `AnswerEventWriter` | Один метод на подію; кожен повертає байти для надсилання або кидає `InvalidEventStream` |
| `AnswerEventReader` | `feed` читає байти й повертає завершені події; `close` повертає `StreamedAnswer` |
| `StreamDefect` | Чому потік відхилено, наприклад `OUT_OF_ORDER`, `GAP` чи `TRUNCATED` |

Створіть `src/knowledge_service/answering/events.py`:

```python
"""The events of a streamed Answer, and the rules for their order.

The server sends one Answer as Server-Sent Events (SSE): plain text, one
event per block, each block an `id:` line, an `event:` line, a `data:`
line holding one JSON object, and a blank line. `AnswerEventWriter` writes
the events; `AnswerEventReader` reads them back as a client would and
rebuilds the Answer. Both check every event against one set of rules, so
whatever the writer sends, the reader accepts.

The order of one stream:

    metadata  text_delta*  citation*  usage?  (completion | error)

- **`metadata` first.** Exactly once: the protocol version and the
  request ID. A request that fails before it is an HTTP error, not a
  stream.
- **Text, then Citations, then usage.** `text_delta` events hold the
  Answer's text in pieces; joined, they are the text. `citation` events
  follow, each Evidence once; `usage` follows, at most once.
- **One terminal event, last.** `completion` or `error` ends the stream;
  nothing may follow it, and a stream that stops without one is cut off.
- **What the client keeps.** `completion` with `answer` keeps the text and
  the Citations; it needs visible text, a Citation, and the usage.
  `completion` with `abstain` replaces any streamed text with the fixed
  message of its reason, and an `error` replaces it with the fixed message
  of its code. Citations come only after the server has decided to
  answer: an abstention after a Citation is refused, so a server that
  sends Citations before it decides fails its tests.
- **Nothing lost or repeated.** Event IDs count up from 0 by one.
- **One spelling.** The reader accepts an event only in the exact bytes
  the writer would send: no extra space, no escaped letter, no other order
  of keys. A Citation must name its Evidence and Document as the server
  does, and token counts stay below 2**53, the largest whole number a
  JavaScript client reads exactly.
- **Bounded.** The text holds at most `MAX_ANSWER_CHARACTERS`, Citations
  are at most `MAX_EVIDENCE`, and one event at most `MAX_EVENT_CHARACTERS`,
  on both sides.
- **Quiet.** Errors name a defect, never a value from the stream, and keep
  no chained error that could; the reprs hide text and messages.
"""

import codecs
import contextlib
import json
import re
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from knowledge_service.answering.abstention import (
    AbstentionDecision,
    AbstentionReason,
    Decision,
    abstention_message,
)
from knowledge_service.answering.citations import (
    Citation,
    CitationPayload,
    citation_payload,
)
from knowledge_service.answering.evidence import MAX_EVIDENCE, EvidenceId
from knowledge_service.answering.generation import MAX_ANSWER_CHARACTERS
from knowledge_service.answering.prompt import has_visible_text
from knowledge_service.contracts import Usage
from knowledge_service.errors import FailureKind, RetryOwner, policy_for
from knowledge_service.language import Language

EVENT_PROTOCOL_VERSION = "1"
MAX_EVENT_CHARACTERS = 131_072
_ID = re.compile(r"id: (0|[1-9][0-9]{0,8})")
_STRICT = ConfigDict(extra="forbid", strict=True, frozen=True)
_TOKENS = ("input_tokens", "output_tokens", "total_tokens")
MAX_TOKENS = 2**53 - 1


class StreamDefect(StrEnum):
    """Why a stream was refused; the stream's values are never repeated."""

    MALFORMED = "malformed"
    OUT_OF_ORDER = "out_of_order"
    AFTER_TERMINAL = "after_terminal"
    TRUNCATED = "truncated"
    GAP = "gap"
    TOO_LARGE = "too_large"
    DUPLICATE_CITATION = "duplicate_citation"
    INCOMPLETE_ANSWER = "incomplete_answer"
    UNKNOWN_ERROR = "unknown_error"


class InvalidEventStream(ValueError):
    """An event breaks the stream's rules; nothing of the stream is quoted."""

    def __init__(self, defect: StreamDefect) -> None:
        self.defect = defect
        super().__init__(f"answer event stream: {defect.value}")


class EventName(StrEnum):
    """The `event:` line of each kind of event."""

    METADATA = "metadata"
    TEXT_DELTA = "text_delta"
    CITATION = "citation"
    USAGE = "usage"
    COMPLETION = "completion"
    ERROR = "error"


_NAMES = frozenset(name.value for name in EventName)


class MetadataData(BaseModel):
    """The first event: which protocol, and which request."""

    model_config = _STRICT

    version: Literal["1"]
    request_id: str


class TextDeltaData(BaseModel):
    """The next piece of the Answer's text."""

    model_config = _STRICT

    text: str = Field(min_length=1, repr=False)


class UsageData(BaseModel):
    """The tokens the provider billed for this Answer."""

    model_config = _STRICT

    input_tokens: int = Field(ge=0, le=MAX_TOKENS)
    output_tokens: int = Field(ge=0, le=MAX_TOKENS)
    total_tokens: int = Field(ge=0, le=MAX_TOKENS)


class CompletionData(BaseModel):
    """The decision: keep the Answer, or show the reason's fixed message."""

    model_config = _STRICT

    decision: Decision
    reason: AbstentionReason | None
    message: str | None = Field(repr=False)
    policy_version: str = Field(min_length=1)


class ErrorData(BaseModel):
    """A failure: its public code, fixed message, and who may retry."""

    model_config = _STRICT

    code: str
    message: str = Field(repr=False)
    retry: RetryOwner


type EventData = (
    MetadataData
    | TextDeltaData
    | CitationPayload
    | UsageData
    | CompletionData
    | ErrorData
)

_DATA: dict[EventName, type[BaseModel]] = {
    EventName.METADATA: MetadataData,
    EventName.TEXT_DELTA: TextDeltaData,
    EventName.CITATION: CitationPayload,
    EventName.USAGE: UsageData,
    EventName.COMPLETION: CompletionData,
    EventName.ERROR: ErrorData,
}


@dataclass(frozen=True, slots=True)
class AnswerEvent:
    """One event: its position, its kind, and its checked data."""

    event_id: int
    name: EventName
    data: EventData


class StreamOutcome(StrEnum):
    """What the client shows at the end."""

    ANSWERED = "answered"
    ABSTAINED = "abstained"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StreamedAnswer:
    """The Answer a client rebuilt from a complete stream.

    `text` and `citations` are empty unless `ANSWERED`; `message` is the
    fixed message to show instead when `ABSTAINED` or `FAILED`.
    """

    request_id: UUID
    outcome: StreamOutcome
    text: str = field(repr=False)
    citations: tuple[CitationPayload, ...]
    usage: UsageData | None
    reason: AbstentionReason | None
    error_code: str | None
    message: str | None = field(repr=False)


class _Phase(StrEnum):
    START = "start"
    METADATA = "metadata"
    TEXT = "text"
    CITATIONS = "citations"
    USAGE = "usage"
    DONE = "done"


# Which phases each event may follow, and the phase it moves the stream to.
_AFTER: dict[EventName, tuple[frozenset[_Phase], _Phase]] = {
    EventName.METADATA: (frozenset({_Phase.START}), _Phase.METADATA),
    EventName.TEXT_DELTA: (
        frozenset({_Phase.METADATA, _Phase.TEXT}),
        _Phase.TEXT,
    ),
    EventName.CITATION: (
        frozenset({_Phase.METADATA, _Phase.TEXT, _Phase.CITATIONS}),
        _Phase.CITATIONS,
    ),
    EventName.USAGE: (
        frozenset({_Phase.METADATA, _Phase.TEXT, _Phase.CITATIONS}),
        _Phase.USAGE,
    ),
    EventName.COMPLETION: (
        frozenset({_Phase.METADATA, _Phase.TEXT, _Phase.CITATIONS, _Phase.USAGE}),
        _Phase.DONE,
    ),
    EventName.ERROR: (
        frozenset({_Phase.METADATA, _Phase.TEXT, _Phase.CITATIONS, _Phase.USAGE}),
        _Phase.DONE,
    ),
}


class _Rules:
    """The order and content rules both the writer and the reader keep."""

    def __init__(self) -> None:
        self.phase = _Phase.START
        self.next_id = 0
        self.request_id: UUID | None = None
        self.text: list[str] = []
        self.characters = 0
        self.citations: list[CitationPayload] = []
        self.usage: UsageData | None = None
        self.result: StreamedAnswer | None = None

    def accept(self, event: AnswerEvent) -> None:
        """Take one event, or raise `InvalidEventStream` and change nothing."""
        if self.phase is _Phase.DONE:
            raise InvalidEventStream(StreamDefect.AFTER_TERMINAL)
        if event.event_id != self.next_id:
            raise InvalidEventStream(StreamDefect.GAP)
        allowed, following = _AFTER[event.name]
        if self.phase not in allowed:
            raise InvalidEventStream(StreamDefect.OUT_OF_ORDER)
        self._take(event.data)
        self.phase = following
        self.next_id += 1

    def _take(self, data: EventData) -> None:
        match data:
            case MetadataData():
                self.request_id = _request_id(data.request_id)
            case TextDeltaData():
                if self.characters + len(data.text) > MAX_ANSWER_CHARACTERS:
                    raise InvalidEventStream(StreamDefect.TOO_LARGE)
                self.text.append(data.text)
                self.characters += len(data.text)
            case CitationPayload():
                if not _server_ids(data):
                    raise InvalidEventStream(StreamDefect.MALFORMED)
                if len(self.citations) >= MAX_EVIDENCE:
                    raise InvalidEventStream(StreamDefect.TOO_LARGE)
                if any(c.evidence_id == data.evidence_id for c in self.citations):
                    raise InvalidEventStream(StreamDefect.DUPLICATE_CITATION)
                self.citations.append(data)
            case UsageData():
                self.usage = data
            case CompletionData():
                self.result = self._completed(data)
            case ErrorData():
                self.result = self._failed(data)

    def _completed(self, data: CompletionData) -> StreamedAnswer:
        if data.decision is Decision.ANSWER:
            text = "".join(self.text)
            if data.reason is not None or data.message is not None:
                raise InvalidEventStream(StreamDefect.MALFORMED)
            if not has_visible_text(text) or not self.citations or self.usage is None:
                raise InvalidEventStream(StreamDefect.INCOMPLETE_ANSWER)
            return self._result(StreamOutcome.ANSWERED, text=text)
        if data.reason is None or data.message not in _messages(data.reason):
            raise InvalidEventStream(StreamDefect.MALFORMED)
        # Citations go only with an Answer the client keeps.
        if self.citations:
            raise InvalidEventStream(StreamDefect.OUT_OF_ORDER)
        return self._result(
            StreamOutcome.ABSTAINED, reason=data.reason, message=data.message
        )

    def _failed(self, data: ErrorData) -> StreamedAnswer:
        policy = _PUBLIC_ERRORS.get(data.code)
        if policy is None:
            raise InvalidEventStream(StreamDefect.UNKNOWN_ERROR)
        if data.message != policy.client_message or data.retry is not (
            policy.retry_owner
        ):
            raise InvalidEventStream(StreamDefect.MALFORMED)
        return self._result(
            StreamOutcome.FAILED, error_code=data.code, message=data.message
        )

    def _result(
        self,
        outcome: StreamOutcome,
        *,
        text: str = "",
        reason: AbstentionReason | None = None,
        error_code: str | None = None,
        message: str | None = None,
    ) -> StreamedAnswer:
        return StreamedAnswer(
            request_id=cast(UUID, self.request_id),
            outcome=outcome,
            text=text,
            citations=tuple(self.citations)
            if outcome is StreamOutcome.ANSWERED
            else (),
            usage=self.usage,
            reason=reason,
            error_code=error_code,
            message=message,
        )


class AnswerEventWriter:
    """Encode one Answer's events in order; refuse any event out of order.

    Each method returns the bytes of one event, ready to send. A refused
    event raises `InvalidEventStream`, and the stream stays where it was.
    """

    def __init__(self, request_id: UUID) -> None:
        if type(request_id) is not UUID:
            raise InvalidEventStream(StreamDefect.MALFORMED)
        self._request_id = request_id
        self._rules = _Rules()

    def metadata(self) -> bytes:
        return self._write(
            EventName.METADATA,
            lambda: MetadataData(
                version=EVENT_PROTOCOL_VERSION, request_id=str(self._request_id)
            ),
        )

    def text_delta(self, text: str) -> bytes:
        return self._write(EventName.TEXT_DELTA, lambda: TextDeltaData(text=text))

    def citation(self, citation: Citation) -> bytes:
        if type(citation) is not Citation:
            raise InvalidEventStream(StreamDefect.MALFORMED)
        return self._write(EventName.CITATION, lambda: citation_payload(citation))

    def usage(self, usage: Usage) -> bytes:
        if type(usage) is not Usage:
            raise InvalidEventStream(StreamDefect.MALFORMED)
        # A Usage built with model_construct may lack a field or hold any type.
        counts = {name: getattr(usage, name, None) for name in _TOKENS}
        return self._write(EventName.USAGE, lambda: UsageData.model_validate(counts))

    def completion(self, decision: AbstentionDecision, language: Language) -> bytes:
        if type(decision) is not AbstentionDecision or type(language) is not Language:
            raise InvalidEventStream(StreamDefect.MALFORMED)

        def build() -> CompletionData:
            reason = decision.reason
            return CompletionData(
                decision=decision.decision,
                reason=reason,
                message=(
                    None if reason is None else abstention_message(reason, language)
                ),
                policy_version=decision.policy_version,
            )

        return self._write(EventName.COMPLETION, build)

    def error(self, kind: FailureKind) -> bytes:
        if type(kind) is not FailureKind:
            raise InvalidEventStream(StreamDefect.MALFORMED)
        policy = policy_for(kind)
        # A cancelled request has nobody left to tell.
        if policy.public_code is None or policy.client_message is None:
            raise InvalidEventStream(StreamDefect.UNKNOWN_ERROR)
        code, message = policy.public_code, policy.client_message
        return self._write(
            EventName.ERROR,
            lambda: ErrorData(code=code, message=message, retry=policy.retry_owner),
        )

    def _write(self, name: EventName, build: Callable[[], EventData]) -> bytes:
        event, block = _built(self._rules.next_id, name, build)
        # Checked before the rules change, so a refused event leaves no trace.
        if len(block) > MAX_EVENT_CHARACTERS:
            raise InvalidEventStream(StreamDefect.TOO_LARGE)
        self._rules.accept(event)
        return block.encode("utf-8")


class AnswerEventReader:
    """Read a stream's bytes as they arrive, and rebuild the Answer.

    `feed` returns the events completed by these bytes; `close` returns
    the `StreamedAnswer` once the stream has ended. After a refused event
    the reader stays broken and raises the same defect again.
    """

    def __init__(self) -> None:
        self._rules = _Rules()
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        # The start of a line still arriving, kept in pieces: joining them
        # on every chunk would cost the square of a long line.
        self._pending: list[str] = []
        self._pending_characters = 0
        self._block: list[str] = []
        self._block_characters = 0
        self._broken: StreamDefect | None = None

    def feed(self, chunk: bytes) -> tuple[AnswerEvent, ...]:
        if self._broken is None and type(chunk) is not bytes:
            self._broken = StreamDefect.MALFORMED
        with self._guard():
            return self._read(self._decode(chunk, final=False))

    def close(self) -> StreamedAnswer:
        with self._guard():
            self._read(self._decode(b"", final=True))
            result = self._rules.result
            if self._pending or self._block or result is None:
                raise InvalidEventStream(StreamDefect.TRUNCATED)
            return result

    @contextlib.contextmanager
    def _guard(self) -> Generator[None]:
        """Refuse a broken stream, and remember the defect that broke it."""
        if self._broken is not None:
            raise InvalidEventStream(self._broken)
        try:
            yield
        except InvalidEventStream as error:
            self._broken = error.defect
            raise

    def _decode(self, chunk: bytes, *, final: bool) -> str:
        try:
            return self._decoder.decode(chunk, final=final)
        except UnicodeDecodeError:
            pass
        raise InvalidEventStream(StreamDefect.MALFORMED)

    def _read(self, text: str) -> tuple[AnswerEvent, ...]:
        # The writer never sends a carriage return; JSON escapes it.
        if "\r" in text:
            raise InvalidEventStream(StreamDefect.MALFORMED)
        if text and self._rules.phase is _Phase.DONE:
            raise InvalidEventStream(StreamDefect.AFTER_TERMINAL)
        *lines, rest = text.split("\n")
        if lines:
            lines[0] = "".join((*self._pending, lines[0]))
            self._pending, self._pending_characters = [], 0
        events: list[AnswerEvent] = []
        for line in lines:
            event = self._line(line)
            if event is not None:
                events.append(event)
        if rest:
            if self._rules.phase is _Phase.DONE:
                raise InvalidEventStream(StreamDefect.AFTER_TERMINAL)
            self._pending.append(rest)
            self._pending_characters += len(rest)
        if self._block_characters + self._pending_characters > MAX_EVENT_CHARACTERS:
            raise InvalidEventStream(StreamDefect.TOO_LARGE)
        return tuple(events)

    def _line(self, line: str) -> AnswerEvent | None:
        if self._rules.phase is _Phase.DONE:
            raise InvalidEventStream(StreamDefect.AFTER_TERMINAL)
        if line.startswith(":") and not self._block:
            return None  # a comment, such as a keep-alive
        # The block's lines, their line ends, and the blank line ending it.
        self._block_characters += len(line) + 1
        if self._block_characters + 1 > MAX_EVENT_CHARACTERS:
            raise InvalidEventStream(StreamDefect.TOO_LARGE)
        if line:
            if len(self._block) == 3:
                raise InvalidEventStream(StreamDefect.MALFORMED)
            self._block.append(line)
            return None
        if not self._block:
            raise InvalidEventStream(StreamDefect.MALFORMED)
        block, self._block, self._block_characters = self._block, [], 0
        event = _decoded(block)
        self._rules.accept(event)
        return event


@dataclass(frozen=True, slots=True)
class _PublicError:
    client_message: str
    retry_owner: RetryOwner


_PUBLIC_ERRORS: dict[str, _PublicError] = {
    policy.public_code: _PublicError(policy.client_message, policy.retry_owner)
    for policy in map(policy_for, FailureKind)
    if policy.public_code is not None and policy.client_message is not None
}


def _messages(reason: AbstentionReason) -> frozenset[str]:
    return frozenset(abstention_message(reason, language) for language in Language)


def _built(
    event_id: int, name: EventName, build: Callable[[], EventData]
) -> tuple[AnswerEvent, str]:
    """An event built and encoded from the caller's values, or a refusal."""
    built: tuple[AnswerEvent, str] | None = None
    try:
        event = AnswerEvent(event_id, name, build())
        block = _encode(event)
        block.encode("utf-8")
        built = event, block
    except Exception:  # noqa: S110 - a caller's object may raise anything
        # An error may quote the caller's text; nothing of it is kept.
        pass
    if built is None:
        raise InvalidEventStream(StreamDefect.MALFORMED)
    return built


def _encode(event: AnswerEvent) -> str:
    body = _body(event.data)
    return f"id: {event.event_id}\nevent: {event.name.value}\ndata: {body}\n\n"


def _body(data: EventData) -> str:
    """The one spelling of an event's JSON."""
    return json.dumps(
        data.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
    )


def _decoded(block: list[str]) -> AnswerEvent:
    """One block of three lines as a checked event, or `MALFORMED`."""
    if len(block) != 3:
        raise InvalidEventStream(StreamDefect.MALFORMED)
    id_line, name_line, data_line = block
    matched = _ID.fullmatch(id_line)
    name = name_line.removeprefix("event: ")
    if (
        matched is None
        or not name_line.startswith("event: ")
        or name not in _NAMES
        or not data_line.startswith("data: ")
    ):
        raise InvalidEventStream(StreamDefect.MALFORMED)
    kind = EventName(name)
    data = _validated(_DATA[kind], data_line.removeprefix("data: "))
    return AnswerEvent(int(matched.group(1)), kind, data)


def _validated(model: type[BaseModel], text: str) -> EventData:
    """The JSON object of one `data:` line, checked by its model."""
    data: EventData | None = None
    # A validation error quotes its input; nothing of it is kept.
    with contextlib.suppress(ValueError, RecursionError):
        data = cast(EventData, model.model_validate_json(text))
    # Only the writer's own spelling is accepted: this also refuses a key
    # given twice, which the model would read as its last value.
    if data is None or _body(data) != text:
        raise InvalidEventStream(StreamDefect.MALFORMED)
    return data


def _server_ids(payload: CitationPayload) -> bool:
    """True when a Citation names its Evidence and Document as the server does."""
    try:
        EvidenceId(payload.evidence_id)
        document = UUID(payload.document_id)
    except ValueError:
        return False
    return str(document) == payload.document_id


def _request_id(value: str) -> UUID:
    try:
        parsed = UUID(value)
    except ValueError:
        parsed = None
    if parsed is None or str(parsed) != value:
        raise InvalidEventStream(StreamDefect.MALFORMED)
    return parsed
```

Тут важливі три деталі. `_Rules.accept` перевіряє все, перш ніж щось змінити, тому відхилений виклик писача лишає потік там, де він був. Читач декодує байти інкрементним UTF-8 декодером, тож українська літера, розрізана між двома шматками, склеюється, а не відхиляється. І кожна відмова кидається поза будь-яким блоком `except`, тож ніколи не несе ланцюжкової помилки, яка могла б процитувати потік.

Перевірка:

```bash
uv run python -c "import knowledge_service.answering.events"
uv run pyright src/knowledge_service/answering/events.py
```

Очікувано: імпорт нічого не друкує, а pyright показує 0 помилок.

### Крок 3 — Крок із кодом: тести порядку, завершальних подій і відновлення

Цей крок доводить, що клієнт може відновити відповідь і що кожен зламаний потік відхиляється.

**Простими словами:** Тести пишуть справжні потоки писачем, зі справжніх Evidence і цитат, і читають їх назад читачем. Вони ділять ті самі байти всіма можливими способами і перевіряють, що відповідь виходить та сама. Потім вони навмисно ламають потоки: переставляють події, гублять одну, обрізають кінець, додають подію після кінця, підробляють повідомлення, і перевіряють, що кожен відхилено з правильною причиною.

| Тест | Що доводить |
|---|---|
| `test_a_client_rebuilds_the_complete_answer_from_its_events` | Успіх: текст, цитати й usage відновлено з повного потоку |
| `test_any_split_of_the_bytes_rebuilds_the_same_answer` | Відновлення не залежить від того, як мережа ріже байти |
| `test_an_abstention_replaces_streamed_text_with_its_fixed_message` | Переданий текст відкидається, коли рішення — утриматися |
| `test_citations_never_precede_an_abstention` | Найризиковіший край: назви відкликаної відповіді не доходять до клієнта |
| `test_events_in_the_wrong_order_are_refused` | Читач відхиляє неправильний порядок |
| `test_only_the_writers_own_spelling_is_accepted` | Та сама подія, написана інакше, відхиляється |
| `test_a_stream_without_a_terminal_event_is_cut_off` | Потік, що зупинився зарано, відхиляється, а не показується як повний |
| `test_the_writer_refuses_bad_values_with_a_typed_error` | Типізована відмова: `InvalidEventStream`, без ланцюжкової помилки |
| `test_errors_and_reprs_never_quote_the_stream` | Підкладений маркер ніколи не з'являється в помилці чи repr |
| `test_whatever_the_writer_accepts_the_reader_accepts` | Фазинг: будь-яка послідовність викликів писача читається назад подія в подію |

Створіть `tests/test_answer_events.py`:

```python
"""The event contract of a streamed Answer: order, terminal events, rebuild."""

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any, cast
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.abstention import (
    POLICY_VERSION,
    AbstentionDecision,
    AbstentionReason,
    Decision,
    abstention_message,
)
from knowledge_service.answering.citations import Citation, resolve_citations
from knowledge_service.answering.events import (
    MAX_EVENT_CHARACTERS,
    MAX_TOKENS,
    AnswerEventReader,
    AnswerEventWriter,
    EventName,
    InvalidEventStream,
    StreamDefect,
    StreamedAnswer,
    StreamOutcome,
)
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import MAX_ANSWER_CHARACTERS
from knowledge_service.contracts import Usage
from knowledge_service.errors import FailureKind
from knowledge_service.language import Language

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000049")
USAGE = Usage(
    provider="fake",
    model="fake-1",
    latency_ms=5,
    input_tokens=300,
    output_tokens=30,
    total_tokens=330,
)
EVIDENCE = build_evidence([candidate(1), candidate(2)])
CITATIONS: tuple[Citation, ...] = resolve_citations(
    EVIDENCE, [item.evidence_id.value for item in EVIDENCE.items]
)
ANSWER = AbstentionDecision(Decision.ANSWER, None, POLICY_VERSION)
UNSUPPORTED = AbstentionDecision(
    Decision.ABSTAIN, AbstentionReason.UNSUPPORTED, POLICY_VERSION
)
NO_EVIDENCE = AbstentionDecision(
    Decision.ABSTAIN, AbstentionReason.NO_EVIDENCE, POLICY_VERSION
)
DELTAS = ("Кожен працівник ", "отримує 24 дні ", "щорічної відпустки.")


def answer_stream(*deltas: str) -> list[bytes]:
    writer = AnswerEventWriter(REQUEST_ID)
    events = [writer.metadata()]
    events += [writer.text_delta(delta) for delta in deltas or DELTAS]
    events += [writer.citation(citation) for citation in CITATIONS]
    events += [writer.usage(USAGE), writer.completion(ANSWER, Language.ENGLISH)]
    return events


def read(*chunks: bytes) -> StreamedAnswer:
    reader = AnswerEventReader()
    for chunk in chunks:
        reader.feed(chunk)
    return reader.close()


def refused(*chunks: bytes) -> StreamDefect:
    with pytest.raises(InvalidEventStream) as caught:
        read(*chunks)
    return caught.value.defect


def event(event_id: int, name: str, data: Mapping[str, object]) -> bytes:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: {name}\ndata: {body}\n\n".encode()


def blocks(stream: list[bytes]) -> list[tuple[str, dict[str, object]]]:
    """Each event of a stream as its name and data, to forge a stream."""
    parts: list[tuple[str, dict[str, object]]] = []
    for raw in stream:
        _, name_line, data_line, *_ = raw.decode().split("\n")
        data: dict[str, object] = json.loads(data_line.removeprefix("data: "))
        parts.append((name_line.removeprefix("event: "), data))
    return parts


def renumbered(parts: list[tuple[str, dict[str, object]]]) -> list[bytes]:
    return [event(number, *part) for number, part in enumerate(parts)]


# --- A complete stream rebuilds the Answer -----------------------------------


def test_a_client_rebuilds_the_complete_answer_from_its_events() -> None:
    result = read(*answer_stream())

    assert result.outcome is StreamOutcome.ANSWERED
    assert result.request_id == REQUEST_ID
    assert result.text == "".join(DELTAS)
    assert [c.evidence_id for c in result.citations] == [
        c.evidence_id.value for c in CITATIONS
    ]
    assert result.citations[0].title == "Policy 1"
    assert result.usage is not None
    assert result.usage.total_tokens == 330
    assert result.reason is None
    assert result.message is None


def test_the_stream_is_the_documented_text_format() -> None:
    first = answer_stream()[0].decode()

    assert first == (
        "id: 0\nevent: metadata\n"
        'data: {"version":"1","request_id":"00000000-0000-0000-0000-000000000049"}'
        "\n\n"
    )


def test_bytes_split_anywhere_rebuild_the_same_answer() -> None:
    whole = b"".join(answer_stream())
    # One byte at a time splits every Ukrainian letter's two bytes too.
    one_by_one = read(*(whole[i : i + 1] for i in range(len(whole))))

    assert one_by_one == read(whole)


@settings(max_examples=200, deadline=None)
@given(st.lists(st.integers(min_value=0, max_value=2_000), max_size=12))
def test_any_split_of_the_bytes_rebuilds_the_same_answer(cuts: list[int]) -> None:
    whole = b"".join(answer_stream())
    points = sorted({min(cut, len(whole)) for cut in cuts})
    chunks = [
        whole[a:b] for a, b in zip([0, *points], [*points, len(whole)], strict=True)
    ]

    assert read(*chunks) == read(whole)


def test_keep_alive_comments_between_events_are_ignored() -> None:
    stream = answer_stream()
    with_comments = [b": keep-alive\n"]
    with_comments += [part + b": ping\n" for part in stream[:-1]] + stream[-1:]

    assert read(*with_comments) == read(*stream)


def test_the_reader_returns_each_event_as_it_completes() -> None:
    reader = AnswerEventReader()
    stream = answer_stream()

    assert reader.feed(stream[0][:10]) == ()
    (metadata,) = reader.feed(stream[0][10:])

    assert metadata.event_id == 0
    assert metadata.name is EventName.METADATA


# --- Abstention and failure replace the streamed text ------------------------


def test_an_abstention_replaces_streamed_text_with_its_fixed_message() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    stream = [
        writer.metadata(),
        writer.text_delta(MARKER),
        writer.usage(USAGE),
        writer.completion(UNSUPPORTED, Language.UKRAINIAN),
    ]

    result = read(*stream)

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.text == ""
    assert result.citations == ()
    assert result.reason is AbstentionReason.UNSUPPORTED
    assert result.message == abstention_message(
        AbstentionReason.UNSUPPORTED, Language.UKRAINIAN
    )
    assert result.usage is not None


def test_no_evidence_abstains_without_text_or_usage() -> None:
    writer = AnswerEventWriter(REQUEST_ID)

    result = read(writer.metadata(), writer.completion(NO_EVIDENCE, Language.MIXED))

    assert result.outcome is StreamOutcome.ABSTAINED
    assert result.usage is None
    assert result.message == abstention_message(
        AbstentionReason.NO_EVIDENCE, Language.ENGLISH
    )


def test_an_error_ends_the_stream_with_its_public_code_and_keeps_the_usage() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    stream = [
        writer.metadata(),
        writer.text_delta(MARKER),
        writer.citation(CITATIONS[0]),
        writer.usage(USAGE),
        writer.error(FailureKind.DEPENDENCY),
    ]

    result = read(*stream)

    assert result.outcome is StreamOutcome.FAILED
    assert result.error_code == "dependency_unavailable"
    assert result.message == "A required service is unavailable"
    assert result.text == ""
    assert result.citations == ()
    assert result.usage is not None


def test_a_cancelled_request_has_no_error_event() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    writer.metadata()

    with pytest.raises(InvalidEventStream) as caught:
        writer.error(FailureKind.CANCELLATION)

    assert caught.value.defect is StreamDefect.UNKNOWN_ERROR


# --- The writer refuses what the reader would refuse -------------------------


@pytest.mark.parametrize(
    ("before", "attempt", "defect"),
    [
        ((), "text", StreamDefect.OUT_OF_ORDER),
        (("metadata",), "metadata", StreamDefect.OUT_OF_ORDER),
        (("metadata", "citation"), "text", StreamDefect.OUT_OF_ORDER),
        (("metadata", "usage"), "citation", StreamDefect.OUT_OF_ORDER),
        (("metadata", "usage"), "usage", StreamDefect.OUT_OF_ORDER),
        (("metadata", "error"), "text", StreamDefect.AFTER_TERMINAL),
        (("metadata", "error"), "error", StreamDefect.AFTER_TERMINAL),
    ],
)
def test_the_writer_refuses_events_out_of_order(
    before: tuple[str, ...], attempt: str, defect: StreamDefect
) -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    calls = {
        "metadata": writer.metadata,
        "text": lambda: writer.text_delta("24 days"),
        "citation": lambda: writer.citation(CITATIONS[0]),
        "usage": lambda: writer.usage(USAGE),
        "error": lambda: writer.error(FailureKind.INTERNAL),
    }
    for name in before:
        calls[name]()

    with pytest.raises(InvalidEventStream) as caught:
        calls[attempt]()

    assert caught.value.defect is defect


@pytest.mark.parametrize("missing", ["text", "citation", "usage"])
def test_an_answer_needs_text_a_citation_and_its_usage(missing: str) -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    stream = [writer.metadata()]
    if missing != "text":
        stream.append(writer.text_delta("24 days"))
    if missing != "citation":
        stream.append(writer.citation(CITATIONS[0]))
    if missing != "usage":
        stream.append(writer.usage(USAGE))

    with pytest.raises(InvalidEventStream) as caught:
        writer.completion(ANSWER, Language.ENGLISH)
    # The refused event left no trace: the stream can still end in an error.
    stream.append(writer.error(FailureKind.INTERNAL))

    assert caught.value.defect is StreamDefect.INCOMPLETE_ANSWER
    assert read(*stream).outcome is StreamOutcome.FAILED


def test_whitespace_alone_is_not_an_answer() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    writer.metadata()
    writer.text_delta(" \n\t")
    writer.citation(CITATIONS[0])
    writer.usage(USAGE)

    with pytest.raises(InvalidEventStream) as caught:
        writer.completion(ANSWER, Language.ENGLISH)

    assert caught.value.defect is StreamDefect.INCOMPLETE_ANSWER


def test_citations_never_precede_an_abstention() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    writer.metadata()
    writer.text_delta("24 days")
    writer.citation(CITATIONS[0])

    with pytest.raises(InvalidEventStream) as caught:
        writer.completion(UNSUPPORTED, Language.ENGLISH)

    assert caught.value.defect is StreamDefect.OUT_OF_ORDER


def test_the_same_citation_twice_is_refused() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    writer.metadata()
    writer.citation(CITATIONS[0])

    with pytest.raises(InvalidEventStream) as caught:
        writer.citation(CITATIONS[0])

    assert caught.value.defect is StreamDefect.DUPLICATE_CITATION


def test_the_text_is_bounded_by_the_answer_limit() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    writer.metadata()
    writer.text_delta("a" * MAX_ANSWER_CHARACTERS)

    with pytest.raises(InvalidEventStream) as caught:
        writer.text_delta("a")

    assert caught.value.defect is StreamDefect.TOO_LARGE


# Each takes the writer as Any, so it can pass what the types forbid.
BAD_VALUES: dict[str, Callable[[Any], object]] = {
    "empty text": lambda w: w.text_delta(""),
    "number as text": lambda w: w.text_delta(7),
    "lone surrogate": lambda w: w.text_delta("\ud800"),
    "ID as citation": lambda w: w.citation("E00000001"),
    "negative tokens": lambda w: w.usage(Usage.model_construct(input_tokens=-1)),
    "bool tokens": lambda w: w.usage(Usage.model_construct(input_tokens=True)),
    "language as text": lambda w: w.completion(ANSWER, "en"),
    "abstain without reason": lambda w: w.completion(
        AbstentionDecision(Decision.ABSTAIN, None, POLICY_VERSION), Language.ENGLISH
    ),
    "reason as text": lambda w: w.completion(
        AbstentionDecision(Decision.ABSTAIN, cast(Any, "unsupported"), POLICY_VERSION),
        Language.ENGLISH,
    ),
    "kind as text": lambda w: w.error("internal"),
    "Document ID as text": lambda w: w.citation(
        replace(CITATIONS[0], document_id=cast(Any, "doc"))
    ),
    "empty Citation": lambda w: w.citation(object.__new__(Citation)),
    "empty decision": lambda w: w.completion(
        object.__new__(AbstentionDecision), Language.ENGLISH
    ),
    "tokens past 2**53": lambda w: w.usage(
        USAGE.model_copy(update={"input_tokens": MAX_TOKENS + 1})
    ),
    "tokens past JSON's digit limit": lambda w: w.usage(
        USAGE.model_copy(update={"input_tokens": 10**5000})
    ),
}


@pytest.mark.parametrize("name", BAD_VALUES)
def test_the_writer_refuses_bad_values_with_a_typed_error(name: str) -> None:
    call = BAD_VALUES[name]
    writer = AnswerEventWriter(REQUEST_ID)
    writer.metadata()

    with pytest.raises(InvalidEventStream) as caught:
        call(writer)

    assert caught.value.defect is StreamDefect.MALFORMED
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


# --- The reader refuses broken streams ----------------------------------------


def test_events_in_the_wrong_order_are_refused() -> None:
    parts = blocks(answer_stream())
    # Usage moved before the Citations.
    parts.insert(len(DELTAS) + 1, parts.pop(-2))

    assert refused(*renumbered(parts)) is StreamDefect.OUT_OF_ORDER


def test_text_before_metadata_is_refused() -> None:
    parts = blocks(answer_stream())
    parts[0], parts[1] = parts[1], parts[0]

    assert refused(*renumbered(parts)) is StreamDefect.OUT_OF_ORDER


def test_a_lost_or_repeated_event_is_refused() -> None:
    stream = answer_stream()

    assert refused(*(stream[:2] + stream[3:])) is StreamDefect.GAP
    assert refused(*(stream[:2] + stream[1:])) is StreamDefect.GAP


def test_a_stream_without_a_terminal_event_is_cut_off() -> None:
    stream = answer_stream()

    assert refused(*stream[:-1]) is StreamDefect.TRUNCATED
    assert refused(*stream[:-1], stream[-1][:-1]) is StreamDefect.TRUNCATED
    assert refused(b"".join(stream)[:-3]) is StreamDefect.TRUNCATED
    assert refused() is StreamDefect.TRUNCATED


def test_nothing_may_follow_the_terminal_event() -> None:
    stream = answer_stream()

    assert refused(*stream, b": late\n") is StreamDefect.AFTER_TERMINAL
    assert refused(*stream, b"\n") is StreamDefect.AFTER_TERMINAL
    assert refused(*stream, b"id") is StreamDefect.AFTER_TERMINAL


def test_a_forged_abstention_message_is_refused() -> None:
    forged = event(
        1,
        "completion",
        {
            "decision": "abstain",
            "reason": "no_evidence",
            "message": f"The document {MARKER} exists but is hidden.",
            "policy_version": POLICY_VERSION,
        },
    )

    assert refused(answer_stream()[0], forged) is StreamDefect.MALFORMED


def test_an_unknown_or_forged_error_is_refused() -> None:
    first = answer_stream()[0]
    unknown = {"code": "teapot", "message": "x", "retry": "none"}
    forged = {"code": "internal_error", "message": MARKER, "retry": "none"}

    assert refused(first, event(1, "error", unknown)) is StreamDefect.UNKNOWN_ERROR
    assert refused(first, event(1, "error", forged)) is StreamDefect.MALFORMED


@pytest.mark.parametrize(
    "raw",
    [
        b"id: 0\nevent: metadata\n\n",
        b"id: 0\nevent: shout\ndata: {}\n\n",
        b"id: 00\nevent: metadata\ndata: {}\n\n",
        b"event: metadata\nid: 0\ndata: {}\n\n",
        b"id: 0\nevent: metadata\ndata: []\n\n",
        b"id: 0\nevent: metadata\ndata: {}\nretry: 5\n\n",
        b'id: 0\nevent: metadata\ndata: {"version":"2",'
        b'"request_id":"00000000-0000-0000-0000-000000000049"}\n\n',
        b'id: 0\nevent: metadata\ndata: {"version":"1","request_id":"x"}\n\n',
        b'id: 0\nevent: metadata\ndata: {"version":"1",'
        b'"request_id":"{00000000-0000-0000-0000-000000000049}"}\n\n',
        b'id: 0\nevent: metadata\ndata: {"version":"1","version":"1",'
        b'"request_id":"00000000-0000-0000-0000-000000000049"}\n\n',
        b'id: 0\nevent: metadata\ndata: {"version":"1","extra":1,'
        b'"request_id":"00000000-0000-0000-0000-000000000049"}\n\n',
        b"\n",
        b"id: 0\r\nevent: metadata\r\n",
        b"id: 0\nevent: metadata\ndata: {\xff}\n\n",
        b"\xef\xbb\xbf" + b"".join(answer_stream()),
    ],
)
def test_a_malformed_event_is_refused(raw: bytes) -> None:
    assert refused(raw) is StreamDefect.MALFORMED


@pytest.mark.parametrize(
    "data",
    [
        b'data: {"text": "ok"}',
        b'data:  {"text":"ok"}',
        b'data: {"text":"ok"} ',
        b'data: {"\\u0074ext":"ok"}',
        b'data: {"text":"\\u006fk"}',
        b'data: {"text":"no","text":"ok"}',
    ],
)
def test_only_the_writers_own_spelling_is_accepted(data: bytes) -> None:
    first = answer_stream()[0]

    assert refused(first, b"id: 1\nevent: text_delta\n" + data + b"\n\n") is (
        StreamDefect.MALFORMED
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("evidence_id", MARKER),
        ("document_id", MARKER),
        ("document_id", "{00000000-0000-0000-0000-000000002001}"),
    ],
)
def test_a_citation_must_name_its_evidence_and_document_as_the_server_does(
    field: str, value: str
) -> None:
    stream = answer_stream()
    parts = blocks(stream)
    name, citation = parts[len(DELTAS) + 1]
    parts[len(DELTAS) + 1] = (name, {**citation, field: value})

    assert refused(*renumbered(parts)) is StreamDefect.MALFORMED


def test_token_counts_past_2_to_the_53_are_refused() -> None:
    first = answer_stream()[0]
    usage = {"input_tokens": MAX_TOKENS + 1, "output_tokens": 0, "total_tokens": 0}

    assert refused(first, event(1, "usage", usage)) is StreamDefect.MALFORMED


def test_wrongly_typed_usage_is_refused() -> None:
    first = answer_stream()[0]
    usage = {"input_tokens": "300", "output_tokens": 30, "total_tokens": 330}

    assert refused(first, event(1, "usage", usage)) is StreamDefect.MALFORMED


def test_a_stream_over_the_limits_is_refused() -> None:
    first = answer_stream()[0]
    endless = b"data: " + b"a" * MAX_EVENT_CHARACTERS
    too_long = [event(n, "text_delta", {"text": "a" * 4_000}) for n in range(1, 6)]

    assert refused(first, endless) is StreamDefect.TOO_LARGE
    assert refused(first, *too_long) is StreamDefect.TOO_LARGE


def test_an_event_over_the_limit_is_refused_even_when_each_line_fits() -> None:
    stream = answer_stream()
    name, citation = blocks(stream)[len(DELTAS) + 1]
    data_line = event(0, name, citation).split(b"\n")[2]
    # A title that makes the data line 10 characters short of the limit.
    title = "T" * (MAX_EVENT_CHARACTERS - 10 - len(data_line) + len("Policy 1"))
    block = event(len(DELTAS) + 1, name, {**citation, "title": title})

    assert all(len(line) < MAX_EVENT_CHARACTERS for line in block.split(b"\n"))
    assert len(block) > MAX_EVENT_CHARACTERS
    assert refused(*stream[: len(DELTAS) + 1], block) is StreamDefect.TOO_LARGE


def test_the_defect_does_not_depend_on_how_the_bytes_are_split() -> None:
    stream = answer_stream()
    # The usage repeats an ID, and then a line runs past the limit.
    broken = b"".join(stream[:-1]) + stream[-2] + b"x" * (MAX_EVENT_CHARACTERS + 1)

    assert refused(broken) is refused(broken[:-10], broken[-10:]) is StreamDefect.GAP


def test_a_long_line_sent_byte_by_byte_is_read_in_linear_time() -> None:
    reader = AnswerEventReader()
    reader.feed(answer_stream()[0])
    started = time.perf_counter()

    for _ in range(MAX_EVENT_CHARACTERS):
        reader.feed(b"a")
    with pytest.raises(InvalidEventStream) as caught:
        reader.feed(b"a")

    assert caught.value.defect is StreamDefect.TOO_LARGE
    assert time.perf_counter() - started < 2


def test_a_broken_reader_stays_broken() -> None:
    reader = AnswerEventReader()
    with pytest.raises(InvalidEventStream):
        reader.feed(b"\n")

    with pytest.raises(InvalidEventStream) as again:
        reader.feed(answer_stream()[0])
    with pytest.raises(InvalidEventStream) as closing:
        reader.close()

    assert again.value.defect is StreamDefect.MALFORMED
    assert closing.value.defect is StreamDefect.MALFORMED


def test_the_reader_refuses_a_chunk_that_is_not_bytes() -> None:
    reader = AnswerEventReader()

    with pytest.raises(InvalidEventStream) as caught:
        reader.feed("id: 0\n")  # type: ignore[arg-type]

    assert caught.value.defect is StreamDefect.MALFORMED


# --- Nothing of the stream leaks -----------------------------------------------


def test_errors_and_reprs_never_quote_the_stream() -> None:
    first = answer_stream()[0]
    bad = event(1, "text_delta", {"text": MARKER, "extra": MARKER})
    with pytest.raises(InvalidEventStream) as caught:
        read(first, bad)
    writer = AnswerEventWriter(REQUEST_ID)
    stream = [writer.metadata(), writer.text_delta(MARKER)]
    stream += [writer.citation(CITATIONS[0]), writer.usage(USAGE)]
    stream.append(writer.completion(ANSWER, Language.ENGLISH))
    reader = AnswerEventReader()
    events = [e for chunk in stream for e in reader.feed(chunk)]
    result = reader.close()

    assert MARKER not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert MARKER not in repr(events)
    assert MARKER not in repr(result)


# --- Whatever the writer sends, the reader accepts ----------------------------

_STEPS = st.sampled_from(
    [
        *("metadata", "text", "space", "citation", "citation2"),
        *("usage", "answer", "abstain", "error"),
    ]
)


@settings(max_examples=300, deadline=None)
@given(st.lists(_STEPS, max_size=10))
def test_whatever_the_writer_accepts_the_reader_accepts(steps: list[str]) -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    calls = {
        "metadata": writer.metadata,
        "text": lambda: writer.text_delta(MARKER),
        "space": lambda: writer.text_delta(" "),
        "citation": lambda: writer.citation(CITATIONS[0]),
        "citation2": lambda: writer.citation(CITATIONS[1]),
        "usage": lambda: writer.usage(USAGE),
        "answer": lambda: writer.completion(ANSWER, Language.ENGLISH),
        "abstain": lambda: writer.completion(UNSUPPORTED, Language.ENGLISH),
        "error": lambda: writer.error(FailureKind.CAPACITY),
    }
    sent: list[bytes] = []
    for step in steps:
        try:
            sent.append(calls[step]())
        except InvalidEventStream:
            continue
    reader = AnswerEventReader()
    read_back = [e for chunk in sent for e in reader.feed(chunk)]

    assert [e.event_id for e in read_back] == list(range(len(sent)))
    if sent and read_back[-1].name in (EventName.COMPLETION, EventName.ERROR):
        result = reader.close()
        assert (result.text == "") is (result.outcome is not StreamOutcome.ANSWERED)
    else:
        with pytest.raises(InvalidEventStream):
            reader.close()


@settings(max_examples=300, deadline=None)
@given(
    st.integers(min_value=0, max_value=10_000),
    st.integers(min_value=0, max_value=255),
)
def test_a_corrupted_byte_gives_a_typed_refusal_or_a_valid_answer(
    position: int, value: int
) -> None:
    whole = bytearray(b"".join(answer_stream()))
    whole[position % len(whole)] = value

    reader = AnswerEventReader()
    try:
        reader.feed(bytes(whole))
        result = reader.close()
    except InvalidEventStream:
        result = None

    # Any refusal is typed; anything accepted is still a whole Answer.
    assert result is None or result.outcome is StreamOutcome.ANSWERED
    assert result is None or len(result.citations) == len(CITATIONS)
```

Перевірка:

```bash
uv run pytest tests/test_answer_events.py -q
```

Очікувано: 81 passed.

### Крок 4 — Без коду: зламайте і вирішіть

Цей крок показує, що захищає правило «цитати лише перед відповіддю», і фіксує, чого ви навчилися.

**Дія 1:** навмисно зламайте правила. У `src/knowledge_service/answering/events.py`, у `_Rules._completed`, видаліть ці рядки:

```python
        # Citations go only with an Answer the client keeps.
        if self.citations:
            raise InvalidEventStream(StreamDefect.OUT_OF_ORDER)
```

Запустіть:

```bash
uv run pytest tests/test_answer_events.py -q
```

Очікувано: 1 failed, 80 passed: `test_citations_never_precede_an_abstention`. Фазинг-тест далі проходить, бо писач і читач мають спільні правила і йдуть в ногу, навіть коли одного правила бракує. Без цих рядків сервер міг би надіслати назви процитованих Документів, а потім утриматися, і клієнт уже показав би, звідки взялася відкликана відповідь. Поверніть рядки і запускайте знову, доки все не пройде.

**Дія 2:** під `## Decision` напишіть:

- чому порядок фіксований і чому завершальна подія вирішує, що клієнт залишає;
- чому цитати йдуть лише після того, як сервер вирішив відповідати;
- чому повідомлення утримання і помилки мають бути фіксованими текстами;
- що лишається для M06-T09: надсилання цих подій через HTTP, скасування і коментарі keep-alive;
- щонайменше одне **спростоване припущення** у формі «Я припускав X. Запуск чи рев'ю показує Y. Отже, Z хибне».

### Крок 5 — Без коду: архітектура, перевірки, evidence і прогрес

Цей крок оновлює текст, який зміна зробила неповним, доводить, що все досі працює, і фіксує результат.

**Дія 1:** у `docs/architecture.md` додайте контракт подій до рядка `answering`. Знайдіть:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), and `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases) |
```

Замініть на:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), and `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages) |
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_answer_events.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний запуск дав 2121 passed для `make test-unit`, на 81 більше, ніж у M06-T07. Цей урок не чіпає код бази, тож `make test-integration` має дати ті самі 342 passed. Якщо він каже, що тестової бази немає, запустіть її командою `docker start knowledge-service-postgres`.

**Дія 3:** заповніть файл evidence:

- **Design:** нові файли; шість подій і що містить кожна; порядок і завершальні події; що клієнт залишає за кожного завершення; чому цитати йдуть лише перед відповіддю; фіксовані повідомлення; ліміти.
- **Verification:** команди й кількості, навмисний злам і відомі обмеження з «Перед початком».
- **Review:** змагальне рев'ю з 3 000 випадкових послідовностей писача і 60 000 пошкоджених потоків знайшло сім проблем, кожну виправлено з тестом, що падав до виправлення: нетипізовані падіння писача на полях не того типу чи величезній кількості токенів; обіцянка docstring про цитати, яку контракт не може виконати; подія понад ліміт, коли кожен рядок вкладається; вигадані ідентифікатори Evidence і Документа в цитаті; той самий JSON, написаний інакше; кількість токенів понад 2**53; дефект, що залежав від поділу байтів.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу виконаною, додайте рядок M06-T08 у `docs/curriculum/PROGRESS.md`, поставте наступною задачу M06-T09 і зробіть коміт з `m06-t08: define sse event contracts`.

## Запитання для рефлексії

1. Що `AnswerEventWriter` приховує від ендпоінта стрімінгу в M06-T09? Перелічіть, що тому коду ніколи не доведеться перевіряти, перш ніж надіслати подію.
2. Пізніша зміна хоче надсилати кожну цитату, щойно модель її назве, щоб сторінка здавалася швидшою. Яке правило вона порушить і що може побачити користувач, чого не мав би?
3. Проксі між сервером і браузером губить одну подію посеред потоку. Як читач це помічає і що показав би клієнт, якби ідентифікатори подій не перевірялися?
4. Чому повідомлення утримання має бути одним із фіксованих текстів, а не будь-чим, що надішле сервер? Що могло б просочитися в повідомлення з довільним текстом?
5. Який зламаний потік ви додали б наступним тестом і який дефект очікуєте від читача?

## Чекліст завершення

- [ ] Файл evidence містить інваріант і ваші передбачення, записані до коду.
- [ ] Є шість подій: metadata, text delta, citation, usage, completion і error, кожна зі строгим JSON.
- [ ] Порядок — `metadata text_delta* citation* usage?`, а потім одна завершальна подія.
- [ ] Після завершальної події нічого не може йти, а потік без неї відхиляється як обірваний.
- [ ] Ідентифікатори подій рахують від 0 з кроком один; загублена чи повторена подія відхиляється.
- [ ] Відповідь потребує видимого тексту, цитати й usage; цитати ніколи не йдуть перед утриманням.
- [ ] Повідомлення утримання і помилки — фіксовані тексти їхньої причини чи коду.
- [ ] Писач відхиляє те, що відхилив би читач, і відхилена подія нічого не змінює.
- [ ] Читач відновлює ту саму відповідь, хоч би як поділили байти.
- [ ] Жоден текст потоку не потрапляє в помилку чи repr.
- [ ] Ви запустили навмисний злам, побачили, що тести падають, і скасували його.
- [ ] Усі перевірки проходять: форматування, лінтер, типи, юніт, інтеграційні, документація.
- [ ] Evidence, статус задачі, рядок PROGRESS і коміт готові.

Коли напишете файли, поділіться ними чи виводом помилок, і я перевірю їх за цим чеклістом.
