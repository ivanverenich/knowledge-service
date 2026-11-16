# M06-T13 — Безпечно опишіть збої потоку

Англійська версія: [M06-T13-map-streaming-failures-safely.md](M06-T13-map-streaming-failures-safely.md)

Вихідне завдання: [M06-T13](../curriculum/milestones/M06-answers-and-conversations/M06-T13-map-streaming-failures-safely.md)

## Що ви зробите

Цей урок в одному місці записує, що робить потокова відповідь, коли щось ламається, і змушує код цього дотримуватися. Потік має точку повороту: мить, коли відправлено заголовки відповіді зі статусом `200`. До неї збій ще може бути звичайною HTTP-помилкою. Після неї статус уже не змінити, тож потік має завершитися подією `error`, а з уже надісланим текстом треба щось вирішити.

- Новий модуль `stream_failures` містить політику: для кожного виду збою — що бачить клієнт до заголовків і після них.
- База даних, що недоступна чи надто повільна, стає збоєм залежності (`503`), а не помилкою в коді (`500`).
- Запис і читання подій дотримуються політики «після заголовків», а ендпоінт додає заголовок `Retry-After` до збою залежності чи перевищення ліміту.
- Кожен потік завершується одним записом про фазу, аудит-результат і долю частково надісланого тексту.

| Завдання вимагає | Де це відбувається |
|---|---|
| Поведінка до і після заголовків відповіді | Крок 2: `stream_failure(kind, phase)` дає обидві колонки однієї таблиці |
| Термінальні SSE-помилки | Крок 4: одна подія `error` з кодом, фіксованим повідомленням і порадою щодо повтору з політики «після заголовків» |
| Політика частково надісланого тексту | Крок 2: `PartialText`; крок 4: текст лишається лише в завершеній відповіді, утримання чи помилка його замінюють, а клієнт, що пішов, його покидає |
| Поради щодо повтору | Кроки 2 і 4: `Retry-After` до заголовків для `503` і `429`; після них клієнт може надіслати запитання знову після збою залежності чи ліміту |
| Аудит-результат | Кроки 2 і 4: один `AuditOutcome` на потік у його записі |
| Впорскувати збої провайдера, бази, валідації і скасування в кількох точках потоку | Крок 5: пошук, до тексту, посеред тексту, після тексту, у мить відправлення заголовків і після термінальної події |
| Телеметрія без підкладеного чутливого тексту | Крок 5: маркер у кожній помилці ніколи не потрапляє у відповідь, заголовок чи запис логу |

Цей урок містить код: кроки 2–5 пишуть код. Кроки 1, 6 і 7 — без коду. Інтеграційним тестам потрібна тестова база.

Терміни цього уроку:

- **Заголовки відповіді (response headers):** перша частина HTTP-відповіді, зі статус-кодом. Щойно їх відправлено, статус зафіксовано; потокова відповідь відправляє їх до першої події.
- **Фаза (phase):** тут — чи стався збій до заголовків, чи після них.
- **Термінальна подія (terminal event):** остання подія потоку, `completion` або `error`. Рівно одна завершує кожен потік, який не обірвано.
- **Частковий текст (partial text):** текст відповіді, який клієнт уже отримав, коли потік зламався.
- **Аудит-результат (audit outcome):** одне слово із закритого списку, що каже, як завершився потік, як-от `answered`, `throttled` чи `cancelled`; записується для кожного потоку.
- **`Retry-After`:** стандартний HTTP-заголовок, що каже клієнту, скільки секунд почекати перед повтором.

## Перед початком

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілий, коли ви до нього дійдете.

- **Що вже є.** M01-T07 зіставляє кожну помилку з `FailureKind`: HTTP-статус, публічний код, фіксоване повідомлення і власник повтору. M06-T08 визначає події і читача, що відновлює відповідь; `error` уже замінює надісланий текст своїм фіксованим повідомленням. M06-T09 знаходить докази до першого байта, тож його збої — HTTP-помилки, а кожен пізніший збій завершує подією `error`. Бракує однієї записаної політики, що каже, чим різняться дві фази, і кількох місць, де код із нею не узгоджений.
- **Дві фази, одна таблиця.** `stream_failure(kind, phase)` повертає все про один збій: статус і `Retry-After` до заголовків, публічний код, повідомлення і власника повтору, долю часткового тексту та аудит-результат. Ендпоінт, запис подій, їх читання і запис потоку питають ту саму функцію, тож розійтися не можуть.
- **Поради щодо повтору після заголовків.** M01 вважає повтор після збою залежності справою сервісу. Щойно текст пішов, сервіс ніколи не повторює запит, бо другий виклик моделі міг би написати в той самий потік інший текст. Тож після заголовків збій залежності чи ліміт каже, що клієнт може надіслати запитання знову, а все інше — що повторювати не варто нікому. До заголовків збій залежності чи ліміт ще й отримує `Retry-After: 5`.
- **Валідація після заголовків.** Запит уже прийнято, тож пізніший збій валідації, як-от вхід, який відкидає ембединг судді підтримки, — вина сервера. Він повідомляється як `internal_error`, а не як «виправте запит».
- **База даних.** Досі будь-яка помилка бази давала `500` без повтору. База, що недоступна, не має вільних з'єднань, вимкнена чи не вклалася в тайм-аут запиту, — це збій залежності, і пізніший повтор може спрацювати. Інші помилки бази, як-от порушене обмеження чи надто велике значення, — помилки в коді. Правило читає клас помилки PostgreSQL (SQLSTATE): `08`, `40`, `53` і `57` — збій залежності, як і сервер, що взагалі не відповів.
- **Частковий текст.** Клієнт зберігає надісланий текст лише тоді, коли потік завершився відповіддю. Утримання чи `error` замінює його фіксованим повідомленням; клієнт, що пішов, тримає обірваний потік, який читач відкидає. Запис каже, що саме сталося: `kept`, `replaced`, `abandoned` або `none`, коли нічого не надіслали.
- **Коли змінюється фаза.** Заголовки йдуть, коли сервер передає початок відповіді, а це може статися до першої події. Тож ендпоінт повідомляє потоку, коли передає їх, а потік ще й позначає це, коли в нього просять першу подію.
- **Записано до передачі.** Кінець потоку записується до того, як термінальна подія передається серверу, а надісланий текст рахується до передачі. Клієнт, що прочитав останню подію і пішов, усе одно записаний як отримувач відповіді.
- **Що знайшло рев'ю.** Незалежне агресивне рев'ю впорскувало збої і відходи клієнта в кожну точку потоку. Збої пошуку давали правильний статус, з `Retry-After` лише для `503` і `429`; збої посеред потоку завершувалися однією подією `error`; валідацію після заголовків повідомлено як внутрішню; жоден потік не лишився без термінальної події; а маркер, підкладений у SQL, параметри і повідомлення драйвера, ні разу не потрапив у відповідь, заголовок чи запис. Рев'ю знайшло такі проблеми; кожну виправлено з тестом, що падає без виправлення:
  - Кожна `OperationalError` вважалася збоєм залежності, але PostgreSQL використовує її і для помилок, які повтор ніколи не виправить, як-от надто велике для індексу значення. Тепер збій залежності — лише сервер, що не відповів, втрачене з'єднання і класи SQLSTATE `08`, `40`, `53` і `57`.
  - Клієнт, що пішов, поки передавалися заголовки, записувався як `before_headers`. Тепер ендпоінт позначає заголовки, коли передає їх.
  - Текст, надісланий наприкінці, коли JSON моделі не починався з відповіді, рахувався лише після передачі, тож клієнт, що пішов із ним, записувався як такий, що нічого не має. Тепер він рахується до передачі.
  - Клієнт, що прочитав термінальну подію і пішов, записувався як `cancelled`. Тепер кінець записується до того, як термінальну подію передано.
  - `/v1/answer` давав ті самі `503` і `429` без `Retry-After`. Тепер обидва ендпоінти використовують один `failure_response`.
  - Задокументовано, не виправлено: суворіша перевірка повтору в читачі — це зміна протоколу без нової версії, бо жоден клієнт ще не вийшов.
- **Де живе код.** Новий `answering/stream_failures.py`; невеликі зміни в `errors.py`, `answering/events.py`, `answering/streaming.py` і `app.py`.
- **Відомі обмеження.** Читач подій тепер відкидає подію помилки зі старою порадою щодо повтору, але версія протоколу лишається `1`, бо жоден клієнт ще не вийшов; після релізу така зміна потребує нової версії. `Retry-After` — фіксовані 5 секунд, а не підказка самого провайдера. Аудит-результат записується в лог-запис потоку, а не як збережена подія аудиту (Audit Event), бо до M07 потік не має перевіреного актора. `503` для відсутнього стрімера не має `Retry-After`, бо очікування його не налаштує.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої передбачення

Поки коду ще немає, запишіть, що має бути правдою наприкінці уроку. Крок 6 перевіряє передбачення.

Створіть `docs/evidence/M06-T13-map-streaming-failures-safely.md`:

```markdown
# M06-T13 — Map streaming failures safely evidence

## Invariant

`stream_failure(kind, phase)` is the one policy for a failed streamed
Answer. Before the response headers a failure is an HTTP error with the
M01 status, code, and message, and a `Retry-After` header for an outage or
a limit. After them it is exactly one terminal `error` event whose code,
message, and retry the reader checks against the same policy: an outage or
a limit is the caller's to retry, a validation failure is internal. A
database that is unreachable, overloaded, or too slow is an outage; any
other database error is internal. Every stream is recorded once with its
phase, one audit outcome, and what became of its partial text. No
response, header, or record holds a Question, an Answer, SQL, or a
connection string.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.stream_failures"`
fails with `ModuleNotFoundError`.

## Predictions

- What a client gets when PostgreSQL is unreachable while Evidence is found, before and after this change: ...
- What a client gets when the model fails after half of the Answer's text was streamed, and what it shows the user: ...
- What the stream's record says when the client reads the `completion` event and then leaves at once: ...
- How many integration tests fail if a database outage is classified as a bug again: ...

## Design

## Verification

## Decision

## Reflection
```

Перевірка: файл існує, перевірка, що має впасти, падає, а кожне передбачення — повне речення, яке крок 6 може спростувати.

### Крок 2 — Крок із кодом: політика збоїв

Цей крок пише таблицю, якої дотримуватимуться всі інші частини.

**Простими словами:** маючи вид збою і знаючи, чи вже відправлено заголовки, модуль каже, що бачить клієнт: статус і час очікування до заголовків або код, повідомлення й пораду щодо повтору в останній події після них. Він також каже, що стається з уже надісланим текстом і яке єдине слово отримує аудит-запис.

| Частина | Що містить або робить |
|---|---|
| `StreamPhase` | `before_headers` або `after_headers` |
| `AuditOutcome` | `answered`, `abstained` і шість телеметричних результатів M01, один із яких — `cancelled` |
| `PartialText` | `none`, `kept`, `replaced`, `abandoned` |
| `StreamFailure` | Вид, який бачить клієнт, фаза, статус, код, повідомлення, власник повтору, `Retry-After`, частковий текст, аудит-результат |
| `stream_failure` | Політика для одного виду в одній фазі |
| `finished_outcome` | `answered` або `abstained` для потоку, що завершився рішенням |

Створіть `src/knowledge_service/answering/stream_failures.py`:

```python
"""What a failed streamed Answer looks like, before and after the headers.

A streamed Answer can fail in two phases, and the client can be told
different things in each:

- **Before the headers.** Nothing has been sent, so a failure is still a
  plain HTTP error: a status, the public code and message of the M01
  error policy, and, for an outage or a limit, a `Retry-After` header that
  says when sending the Question again may work. The retry owner stays the
  M01 one: for an outage it is the service, which says when through
  `Retry-After`.
- **After the headers.** The status `200` and maybe some text are already
  out, so the status cannot change. The stream ends with one `error`
  event holding a public code, its fixed message, and who may retry. The
  service never retries a stream once text is out, because the text could
  differ, so an outage or a limit tells the caller to send the Question
  again. A validation failure here is the server's own, because the
  request was accepted, so it is reported as an internal error. The
  event reader accepts only this guidance; no client of the version `1`
  events had shipped before it changed, so the version stays `1`.

What every failure settles:

- **The partial text.** The client keeps streamed text only when the
  stream completes with an Answer. An abstention or an `error` replaces
  it with a fixed message; a client that leaves keeps an incomplete
  stream, which a reader refuses. Nothing streamed means nothing to keep.
- **The audit outcome.** Each stream ends with exactly one outcome from a
  closed list: answered, abstained, one of the M01 telemetry outcomes for
  a failure, or cancelled. It is recorded with the phase and what happened
  to the partial text, never a text.
- **Cancellation is not an error.** A client that left gets no status and
  no event; the outcome is `cancelled`.
"""

from dataclasses import dataclass
from enum import StrEnum

from knowledge_service.answering.abstention import Decision
from knowledge_service.errors import FailureKind, RetryOwner, policy_for

# How long a client should wait before sending the Question again, when
# the failure is an outage or a limit.
RETRY_AFTER_SECONDS = 5


class StreamPhase(StrEnum):
    """Whether the response headers were already sent."""

    BEFORE_HEADERS = "before_headers"
    AFTER_HEADERS = "after_headers"


class AuditOutcome(StrEnum):
    """How one streamed Answer ended, for the audit trail."""

    ANSWERED = "answered"
    ABSTAINED = "abstained"
    REJECTED = "rejected"
    DENIED = "denied"
    DEPENDENCY_ERROR = "dependency_error"
    THROTTLED = "throttled"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


class PartialText(StrEnum):
    """What becomes of the text the client already holds."""

    NONE = "none"
    KEPT = "kept"
    REPLACED = "replaced"
    ABANDONED = "abandoned"


@dataclass(frozen=True, slots=True)
class StreamFailure:
    """What the client is told about one failure, and what is recorded.

    `kind` is the kind the client sees: after the headers, a validation
    failure is reported as internal. `status_code` and `retry_after_seconds`
    exist only before the headers; `public_code` and `message` are None for
    a cancellation, which tells nobody anything. `partial_text` is what
    becomes of streamed text, when there is any.
    """

    kind: FailureKind
    phase: StreamPhase
    status_code: int | None
    public_code: str | None
    message: str | None
    retry: RetryOwner
    retry_after_seconds: int | None
    partial_text: PartialText
    audit_outcome: AuditOutcome


_RETRY_AFTER = frozenset({FailureKind.DEPENDENCY, FailureKind.CAPACITY})


def stream_failure(kind: FailureKind, phase: StreamPhase) -> StreamFailure:
    """The policy for one failure kind in one phase."""
    if type(kind) is not FailureKind or type(phase) is not StreamPhase:
        raise TypeError("kind must be a FailureKind and phase a StreamPhase")
    before = phase is StreamPhase.BEFORE_HEADERS
    reported = kind
    if not before and kind is FailureKind.VALIDATION:
        # The request was accepted; whatever failed later is the server's.
        reported = FailureKind.INTERNAL
    policy = policy_for(reported)
    cancelled = reported is FailureKind.CANCELLATION
    if before:
        retry = policy.retry_owner
        partial = PartialText.NONE
    else:
        retry = RetryOwner.CALLER if reported in _RETRY_AFTER else RetryOwner.NONE
        partial = PartialText.ABANDONED if cancelled else PartialText.REPLACED
    return StreamFailure(
        kind=reported,
        phase=phase,
        status_code=policy.status_code if before else None,
        public_code=policy.public_code,
        message=policy.client_message,
        retry=retry,
        retry_after_seconds=(
            RETRY_AFTER_SECONDS if before and reported in _RETRY_AFTER else None
        ),
        partial_text=partial,
        audit_outcome=AuditOutcome(policy.telemetry_outcome.value),
    )


def finished_outcome(decision: Decision) -> AuditOutcome:
    """The audit outcome of a stream that ended with a decision."""
    if decision is Decision.ANSWER:
        return AuditOutcome.ANSWERED
    return AuditOutcome.ABSTAINED
```

Перевірка:

```bash
uv run python -c "import knowledge_service.answering.stream_failures"
uv run pyright src/knowledge_service/answering/stream_failures.py
```

Очікувано: імпорт нічого не друкує, а pyright показує 0 помилок.

### Крок 3 — Крок із кодом: збій бази — це збій залежності

Цей крок навчає таксономію помилок, як виглядає недоступна база.

**Простими словами:** коли базу не досягти, у неї немає вільних з'єднань, її вимкнено або вона зупинила надто довгий запит, сервіс тепер каже «потрібний сервіс недоступний, спробуйте пізніше», а не «внутрішня помилка». Помилка бази, яку повтор ніколи не виправить, як-от порушене правило в даних, лишається внутрішньою.

| Помилка | Вид |
|---|---|
| У пулі вчасно не знайшлося вільного з'єднання (`TimeoutError`) | `dependency` |
| З'єднання, яке SQLAlchemy позначив як втрачене | `dependency` |
| `OperationalError` без SQLSTATE або з класом `08`, `40`, `53`, `57` | `dependency` |
| Будь-яка інша помилка бази | `internal`, як і раніше |

У `src/knowledge_service/errors.py`:

**1.** Знайдіть:

```python
import asyncio
from dataclasses import dataclass
from enum import StrEnum

from knowledge_service.embeddings.port import (
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
```

Замініть на:

```python
import asyncio
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import exc as database_errors

from knowledge_service.embeddings.port import (
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
```

**2.** Знайдіть:

```python
        RetryOwner.NONE,
        TelemetryOutcome.INTERNAL_ERROR,
    ),
}


def classify_failure(error: BaseException) -> FailureKind:
    if isinstance(error, asyncio.CancelledError):
        return FailureKind.CANCELLATION
    if isinstance(error, AuthorizationDenied):
        return FailureKind.AUTHORIZATION
    if isinstance(error, EmbeddingInputRejected):
```

Замініть на:

```python
        RetryOwner.NONE,
        TelemetryOutcome.INTERNAL_ERROR,
    ),
}


# SQLSTATE classes of a database that is unreachable or overloaded, or
# that gave up on a statement which may work again: a lost connection
# (08), a serialization failure or deadlock (40), too many connections or
# no space (53), and a cancelled statement or a shutdown (57).
_UNAVAILABLE_STATES = frozenset({"08", "40", "53", "57"})


def _database_unavailable(error: BaseException) -> bool:
    """True when sending the request again later may work."""
    # The pool had no free connection in time.
    if isinstance(error, database_errors.TimeoutError):
        return True
    if not isinstance(error, database_errors.DBAPIError):
        return False
    if error.connection_invalidated:
        return True
    if not isinstance(error, database_errors.OperationalError):
        return False
    # No SQLSTATE means the server never answered, such as a refused connection.
    state = getattr(error.orig, "sqlstate", None)
    return state is None or str(state)[:2] in _UNAVAILABLE_STATES


def classify_failure(error: BaseException) -> FailureKind:
    if isinstance(error, asyncio.CancelledError):
        return FailureKind.CANCELLATION
    if isinstance(error, AuthorizationDenied):
        return FailureKind.AUTHORIZATION
    if isinstance(error, EmbeddingInputRejected):
```

**3.** Знайдіть:

```python
    if isinstance(error, (ChatModelRequestRefused, EmbeddingRequestRefused)):
        return FailureKind.INTERNAL
    if isinstance(error, (ChatModelRateLimited, EmbeddingRateLimited)):
        return FailureKind.CAPACITY
    if isinstance(error, (AnswerDeadlineExceeded, ChatModelError, EmbeddingError)):
        return FailureKind.DEPENDENCY
    return FailureKind.INTERNAL


def policy_for(kind: FailureKind) -> FailurePolicy:
    return POLICIES[kind]
```

Замініть на:

```python
    if isinstance(error, (ChatModelRequestRefused, EmbeddingRequestRefused)):
        return FailureKind.INTERNAL
    if isinstance(error, (ChatModelRateLimited, EmbeddingRateLimited)):
        return FailureKind.CAPACITY
    if isinstance(error, (AnswerDeadlineExceeded, ChatModelError, EmbeddingError)):
        return FailureKind.DEPENDENCY
    # The database is down, unreachable, too slow, or out of connections.
    # Any other database error, such as a broken constraint, is a bug.
    if _database_unavailable(error):
        return FailureKind.DEPENDENCY
    return FailureKind.INTERNAL


def policy_for(kind: FailureKind) -> FailurePolicy:
    return POLICIES[kind]
```

Перевірка:

```bash
uv run pytest tests/test_errors.py -q
```

Очікувано: тести M01 досі проходять.

### Крок 4 — Крок із кодом: події, потік і ендпоінт дотримуються політики

Цей крок змушує три місця, що повідомляють про збої, питати політику.

**Простими словами:** подія `error` тепер несе код і пораду щодо повтору з політики «після заголовків», і читач подій приймає лише їх. Останній запис потоку отримує фазу, аудит-результат і долю часткового тексту, і пишеться він до того, як останню подію передано. Ендпоінт додає `Retry-After` до збою залежності чи ліміту і повідомляє потоку, коли йдуть його заголовки.

| Файл | Що змінюється |
|---|---|
| `answering/events.py` | `error` і таблиця відомих помилок читача використовують `stream_failure(kind, AFTER_HEADERS)` |
| `answering/streaming.py` | Запис отримує `phase`, `audit_outcome`, `partial_text`; текст наприкінці рахується до відправлення; термінальну подію записано до передачі; `AnswerStream.headers_sent` |
| `app.py` | `failure_response` додає `Retry-After`; `EventStreamResponse` викликає `headers_sent`, коли передає початок відповіді |

У `src/knowledge_service/answering/events.py`:

**1.** Знайдіть:

```python
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
```

Замініть на:

```python
    CitationPayload,
    citation_payload,
)
from knowledge_service.answering.evidence import MAX_EVIDENCE, EvidenceId
from knowledge_service.answering.generation import MAX_ANSWER_CHARACTERS
from knowledge_service.answering.prompt import has_visible_text
from knowledge_service.answering.stream_failures import StreamPhase, stream_failure
from knowledge_service.contracts import Usage
from knowledge_service.errors import FailureKind, RetryOwner
from knowledge_service.language import Language

EVENT_PROTOCOL_VERSION = "1"
MAX_EVENT_CHARACTERS = 131_072
_ID = re.compile(r"id: (0|[1-9][0-9]{0,8})")
_STRICT = ConfigDict(extra="forbid", strict=True, frozen=True)
```

**2.** Знайдіть:

```python

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
```

Замініть на:

```python

        return self._write(EventName.COMPLETION, build)

    def error(self, kind: FailureKind) -> bytes:
        if type(kind) is not FailureKind:
            raise InvalidEventStream(StreamDefect.MALFORMED)
        # What a client is told once the headers are out (`stream_failures`).
        failure = stream_failure(kind, StreamPhase.AFTER_HEADERS)
        # A cancelled request has nobody left to tell.
        if failure.public_code is None or failure.message is None:
            raise InvalidEventStream(StreamDefect.UNKNOWN_ERROR)
        code, message = failure.public_code, failure.message
        return self._write(
            EventName.ERROR,
            lambda: ErrorData(code=code, message=message, retry=failure.retry),
        )

    def _write(self, name: EventName, build: Callable[[], EventData]) -> bytes:
        event, block = _built(self._rules.next_id, name, build)
        # Checked before the rules change, so a refused event leaves no trace.
        if len(block) > MAX_EVENT_CHARACTERS:
```

**3.** Знайдіть:

```python
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

```

Замініть на:

```python
class _PublicError:
    client_message: str
    retry_owner: RetryOwner


_PUBLIC_ERRORS: dict[str, _PublicError] = {
    failure.public_code: _PublicError(failure.message, failure.retry)
    for kind in FailureKind
    if (failure := stream_failure(kind, StreamPhase.AFTER_HEADERS)).public_code
    is not None
    and failure.message is not None
}


def _messages(reason: AbstentionReason) -> frozenset[str]:
    return frozenset(abstention_message(reason, language) for language in Language)

```

У `src/knowledge_service/answering/streaming.py`:

**1.** Знайдіть:

```python
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
```

Замініть на:

```python
  once, so the provider stops generating, and sends nothing more. The
  close is shielded from a second cancellation, so it is not cut short,
  and bounded by `CLOSE_SECONDS`, so a stuck close cannot hold it.
- **One record per stream.** "answer stream started" and "answer stream
  ended" hold the request ID, counts, tokens, timing, and how the stream
  ended: `completed`, `failed`, or `cancelled`, also when it ends before
  its first event. The end also holds the phase it ended in, its audit
  outcome, and what became of the partial text, as `stream_failures`
  defines them. Never text.
"""

import asyncio
import contextlib
import logging
import re
```

**2.** Знайдіть:

```python
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
```

Замініть на:

```python
    ModelReply,
    positive_seconds,
    reply_result,
    well_formed_reply,
)
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.stream_failures import (
    AuditOutcome,
    PartialText,
    StreamPhase,
    finished_outcome,
    stream_failure,
)
from knowledge_service.answering.support import SupportJudge, check_support
from knowledge_service.contracts import Question, Usage
from knowledge_service.errors import (
    AnswerDeadlineExceeded,
    FailureKind,
    classify_failure,
```

**3.** Знайдіть:

```python
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
```

Замініть на:

```python
                else None
            )
        except asyncio.CancelledError:
            span.end()
            raise
        except Exception as error:
            failed = stream_failure(classify_failure(error), StreamPhase.BEFORE_HEADERS)
            span.failure = type(error).__name__
            span.outcome = "failed"
            span.audit_outcome = failed.audit_outcome
            span.end()
            raise
        plan = _Plan(
            request_id=request_id,
            deadline=deadline,
            language=_language(question.text),
```

**4.** Знайдіть:

```python
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
```

Замініть на:

```python
        return AnswerStream(self._events(plan, span), span)

    async def _events(self, plan: _Plan, span: _Span) -> AsyncGenerator[bytes]:
        writer = AnswerEventWriter(plan.request_id)
        finished: FinishedAnswer | None = None
        failure: FailureKind | None = None
        # The response sends its headers before it asks for the first event.
        span.phase = StreamPhase.AFTER_HEADERS
        try:
            yield writer.metadata()
            context = plan.context
            try:
                if context is not None and context.evidence.items:
                    reply: ModelReply | None = None
```

**5.** Знайдіть:

```python
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
```

Замініть на:

```python
                    decision is not None
                    and finished is not None
                    and decision.decision is Decision.ANSWER
                ):
                    rest = finished.result.text[span.characters :]
                    if rest:
                        delta = writer.text_delta(rest)
                        # Counted before it is handed over, as in the loop above.
                        span.sent(rest)
                        yield delta
                    for citation in finished.result.citations:
                        yield writer.citation(citation)
                        span.citations += 1
                if span.usage is not None:
                    yield writer.usage(span.usage)
                if decision is not None:
                    completion = writer.completion(decision, plan.language)
                    # Recorded before it is handed over: a client that leaves
                    # after reading it got the whole stream.
                    span.outcome = "completed"
                    span.audit_outcome = finished_outcome(decision.decision)
                    span.partial_text = (
                        PartialText.KEPT
                        if decision.decision is Decision.ANSWER
                        else PartialText.REPLACED
                    )
                    yield completion
            except InvalidEventStream as error:
                # A refused event changes nothing, so an error can still end it.
                decision = None
                failure = FailureKind.INTERNAL
                span.failure = f"{type(error).__name__}:{error.defect.value}"
            if decision is None:
                failed = stream_failure(
                    failure or FailureKind.INTERNAL, StreamPhase.AFTER_HEADERS
                )
                error_event = writer.error(failed.kind)
                span.outcome = "failed"
                span.audit_outcome = failed.audit_outcome
                span.partial_text = failed.partial_text
                yield error_event
        finally:
            span.end()

    async def _finished(
        self,
        plan: _Plan,
```

**6.** Знайдіть:

```python
    def __init__(self, events: AsyncGenerator[bytes], span: _Span) -> None:
        self._events = events
        self._span = span

    def __aiter__(self) -> AnswerStream:
        return self

    async def __anext__(self) -> bytes:
        return await anext(self._events)

    async def aclose(self) -> None:
        try:
```

Замініть на:

```python
    def __init__(self, events: AsyncGenerator[bytes], span: _Span) -> None:
        self._events = events
        self._span = span

    def __aiter__(self) -> AnswerStream:
        return self

    def headers_sent(self) -> None:
        """Record that the response headers went out, before any event."""
        self._span.phase = StreamPhase.AFTER_HEADERS

    async def __anext__(self) -> bytes:
        return await anext(self._events)

    async def aclose(self) -> None:
        try:
```

**7.** Знайдіть:

```python
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
```

Замініть на:

```python
    characters: int = 0
    deltas: int = 0
    citations: int = 0
    usage: Usage | None = None
    failure: str | None = None
    outcome: str = "cancelled"
    phase: StreamPhase = StreamPhase.BEFORE_HEADERS
    audit_outcome: AuditOutcome = AuditOutcome.CANCELLED
    # What a client that left holds; a decision or an error sets it.
    partial_text: PartialText = PartialText.ABANDONED
    ended: bool = False

    def __post_init__(self) -> None:
        logger.info(
            "answer stream started",
            extra={"answer_stream": {"request_id": str(self.request_id)}},
```

**8.** Знайдіть:

```python
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
```

Замініть на:

```python
            "outcome": self.outcome,
            "failure": self.failure,
            "duration_ms": round((time.perf_counter() - self.started_at) * 1_000),
            "text_deltas": self.deltas,
            "characters": self.characters,
            "citations": self.citations,
            "phase": str(self.phase),
            "audit_outcome": str(self.audit_outcome),
            "partial_text": str(
                self.partial_text if self.characters else PartialText.NONE
            ),
        }
        if self.usage is not None:
            fields["input_tokens"] = self.usage.input_tokens
            fields["output_tokens"] = self.usage.output_tokens
        level = logging.INFO if self.outcome == "completed" else logging.WARNING
        logger.log(level, "answer stream ended", extra={"answer_stream": fields})
```

У `src/knowledge_service/app.py`:

**1.** Знайдіть:

```python
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

Замініть на:

```python
from uuid import UUID, uuid4

import anyio
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.types import Message, Receive, Scope, Send

from knowledge_service.answering.stream_failures import StreamPhase, stream_failure
from knowledge_service.answering.streaming import (
    CLOSE_SECONDS,
    AnswerStream,
    AnswerStreamer,
)
from knowledge_service.contracts import Answer, ErrorResponse, Question
```

**2.** Знайдіть:

```python

def failure_response(
    *,
    kind: FailureKind,
    request_id: UUID,
) -> JSONResponse:
    policy = policy_for(kind)

    status_code = policy.status_code
    public_code = policy.public_code
    client_message = policy.client_message

    if status_code is None or public_code is None or client_message is None:
        raise RuntimeError("Failure policy has no public HTTP response")

    return error_response(
        status_code=status_code,
        code=public_code,
        message=client_message,
        request_id=request_id,
    )


def request_id_from(request: Request) -> UUID:
    raw_request_id = request.headers.get("X-Request-ID")
    if raw_request_id is None:
        return uuid4()
```

Замініть на:

```python

def failure_response(
    *,
    kind: FailureKind,
    request_id: UUID,
) -> JSONResponse:
    """The JSON error for `kind`: a response not yet started has no stream."""
    policy = policy_for(kind)
    # An outage or a limit says when to send the request again.
    retry_after = stream_failure(kind, StreamPhase.BEFORE_HEADERS).retry_after_seconds

    status_code = policy.status_code
    public_code = policy.public_code
    client_message = policy.client_message

    if status_code is None or public_code is None or client_message is None:
        raise RuntimeError("Failure policy has no public HTTP response")

    response = error_response(
        status_code=status_code,
        code=public_code,
        message=client_message,
        request_id=request_id,
    )
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


def request_id_from(request: Request) -> UUID:
    raw_request_id = request.headers.get("X-Request-ID")
    if raw_request_id is None:
        return uuid4()
```

**3.** Знайдіть:

```python
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
```

Замініть на:

```python
                with anyio.move_on_after(2 * CLOSE_SECONDS, shield=True):
                    await close()
        if writing.done() and not writing.cancelled():
            writing.result()

    async def _write(self, send: Send) -> None:
        async def sending(message: Message) -> None:
            # Recorded as the headers are handed over, before any event.
            if message["type"] == "http.response.start":
                headers_sent = getattr(self.body_iterator, "headers_sent", None)
                if callable(headers_sent):
                    headers_sent()
            await send(message)

        # Under ASGI 2.4, a write after the client left raises OSError.
        with contextlib.suppress(OSError):
            await self.stream_response(sending)


async def _unless_client_leaves(
    request: Request, opening: Coroutine[object, object, AnswerStream]
) -> AnswerStream | None:
    """Await `opening`, or cancel it and return None when the client leaves.
```

Перевірка:

```bash
uv run pyright src
uv run pytest tests/test_answer_events.py tests/test_streaming.py tests/test_app.py -q
```

Очікувано: 0 помилок, і тести M06-T08, M06-T09 та застосунку досі проходять.

### Крок 5 — Крок із кодом: тести, що впорскують збої всюди

Цей крок доводить, що політика тримається в кожній точці, де потік може зламатися.

**Простими словами:** юніт-тести перевіряють таблицю політики для кожного виду в обох фазах. Потім навмисно ламають фальшивий потік у пошуку, до тексту, посеред тексту і після тексту — помилками провайдера, бази, валідації та невідомими — і перевіряють статус чи останню подію, запис і те, що підкладений маркер нікуди не втік. Інші тести змушують клієнта піти в різні моменти. Інтеграційні тести беруть справжній PostgreSQL, до якого не достукатися або який надто повільний, і чекають `503` з `Retry-After`.

| Тест | Що доводить |
|---|---|
| `test_before_the_headers_a_failure_is_a_plain_http_error` | Колонку політики «до заголовків» |
| `test_after_the_headers_a_failure_is_a_terminal_event_and_never_a_status` | Колонку «після заголовків» |
| `test_a_failure_in_the_search_is_an_http_error_with_retry_guidance` | Збої провайдера, бази, валідації, відмови в доступі та помилки в коді до заголовків |
| `test_a_failure_after_the_headers_ends_in_one_error_event` | Ті самі збої до, під час і після тексту: найризикованіший край |
| `test_a_client_that_leaves_mid_text_abandons_it` | Скасування посеред тексту |
| `test_a_client_that_read_the_terminal_event_and_left_is_not_cancelled` | Запис витримує клієнта, що пішов у самому кінці |
| `test_a_database_error_retrying_cannot_fix_is_internal_without_retry_after` | Типізована відмова: помилка бази в коді лишається `500` |
| `test_an_unreachable_database_is_a_503_with_retry_after` | Справжнє відхилене з'єднання |
| `test_a_database_too_slow_for_its_timeout_is_a_503_with_retry_after` | Справжній тайм-аут запиту |

Створіть `tests/test_stream_failures.py`:

```python
"""Stream failures: one policy before and after the headers, injected everywhere."""

import asyncio
import json
import logging
from typing import Any, cast
from uuid import UUID

import psycopg.errors as pg
import pytest
from sqlalchemy import exc as database_errors
from starlette.types import Message

from evidence_fakes import MARKER
from knowledge_service.answering.events import (
    AnswerEventReader,
    AnswerEventWriter,
    InvalidEventStream,
    StreamOutcome,
)
from knowledge_service.answering.stream_failures import (
    RETRY_AFTER_SECONDS,
    AuditOutcome,
    PartialText,
    StreamPhase,
    stream_failure,
)
from knowledge_service.answering.streaming import FinishedAnswer
from knowledge_service.app import create_app
from knowledge_service.contracts import Question
from knowledge_service.embeddings.port import (
    EmbeddingInputRejected,
    EmbeddingRateLimited,
    RejectionReason,
)
from knowledge_service.errors import (
    AuthorizationDenied,
    FailureKind,
    RetryOwner,
    classify_failure,
)
from knowledge_service.model import ChatModelRateLimited, ChatModelUnavailable
from knowledge_service.workflow import AnswerWorkflow
from test_streaming import (
    ANSWER,
    CITED,
    QUESTION,
    REQUEST_ID,
    Connection,
    Model,
    Source,
    chunks,
    client,
    names,
    rebuilt,
    scope,
    streamer,
)

BEFORE = StreamPhase.BEFORE_HEADERS
AFTER = StreamPhase.AFTER_HEADERS


def database_down() -> database_errors.OperationalError:
    """What the driver raises when PostgreSQL cannot be reached."""
    return database_errors.OperationalError(
        f"SELECT '{MARKER}'", None, ConnectionRefusedError(MARKER)
    )


class Raising:
    """A finisher that fails the way `error` says, after the model replied."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def finish(
        self, result: Any, evidence: Any, *, request_id: UUID
    ) -> FinishedAnswer:
        raise self.error


def ended(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    (record,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    return record.answer_stream  # type: ignore[attr-defined, no-any-return]


def error_data(sent: list[bytes]) -> dict[str, Any]:
    data = sent[-1].split(b"\n")[2].removeprefix(b"data: ")
    return json.loads(data)


def leaks(caplog: pytest.LogCaptureFixture, *texts: bytes) -> bool:
    records = any(
        MARKER in r.getMessage() or MARKER in repr(r.__dict__) for r in caplog.records
    )
    return records or any(MARKER.encode() in text for text in texts)


# --- The policy -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "status", "code", "retry", "retry_after", "outcome"),
    [
        (
            FailureKind.VALIDATION,
            422,
            "validation_error",
            RetryOwner.CALLER,
            None,
            AuditOutcome.REJECTED,
        ),
        (
            FailureKind.AUTHORIZATION,
            403,
            "authorization_denied",
            RetryOwner.NONE,
            None,
            AuditOutcome.DENIED,
        ),
        (
            FailureKind.DEPENDENCY,
            503,
            "dependency_unavailable",
            RetryOwner.SERVICE,
            RETRY_AFTER_SECONDS,
            AuditOutcome.DEPENDENCY_ERROR,
        ),
        (
            FailureKind.CAPACITY,
            429,
            "capacity_exceeded",
            RetryOwner.CALLER,
            RETRY_AFTER_SECONDS,
            AuditOutcome.THROTTLED,
        ),
        (
            FailureKind.INTERNAL,
            500,
            "internal_error",
            RetryOwner.NONE,
            None,
            AuditOutcome.INTERNAL_ERROR,
        ),
        (
            FailureKind.CANCELLATION,
            None,
            None,
            RetryOwner.NONE,
            None,
            AuditOutcome.CANCELLED,
        ),
    ],
)
def test_before_the_headers_a_failure_is_a_plain_http_error(
    kind: FailureKind,
    status: int | None,
    code: str | None,
    retry: RetryOwner,
    retry_after: int | None,
    outcome: AuditOutcome,
) -> None:
    failure = stream_failure(kind, BEFORE)

    assert failure.status_code == status
    assert failure.public_code == code
    assert failure.retry is retry
    assert failure.retry_after_seconds == retry_after
    assert failure.partial_text is PartialText.NONE
    assert failure.audit_outcome is outcome


@pytest.mark.parametrize(
    ("kind", "code", "retry", "partial", "outcome"),
    [
        (
            FailureKind.VALIDATION,
            "internal_error",
            RetryOwner.NONE,
            PartialText.REPLACED,
            AuditOutcome.INTERNAL_ERROR,
        ),
        (
            FailureKind.AUTHORIZATION,
            "authorization_denied",
            RetryOwner.NONE,
            PartialText.REPLACED,
            AuditOutcome.DENIED,
        ),
        (
            FailureKind.DEPENDENCY,
            "dependency_unavailable",
            RetryOwner.CALLER,
            PartialText.REPLACED,
            AuditOutcome.DEPENDENCY_ERROR,
        ),
        (
            FailureKind.CAPACITY,
            "capacity_exceeded",
            RetryOwner.CALLER,
            PartialText.REPLACED,
            AuditOutcome.THROTTLED,
        ),
        (
            FailureKind.INTERNAL,
            "internal_error",
            RetryOwner.NONE,
            PartialText.REPLACED,
            AuditOutcome.INTERNAL_ERROR,
        ),
        (
            FailureKind.CANCELLATION,
            None,
            RetryOwner.NONE,
            PartialText.ABANDONED,
            AuditOutcome.CANCELLED,
        ),
    ],
)
def test_after_the_headers_a_failure_is_a_terminal_event_and_never_a_status(
    kind: FailureKind,
    code: str | None,
    retry: RetryOwner,
    partial: PartialText,
    outcome: AuditOutcome,
) -> None:
    failure = stream_failure(kind, AFTER)

    assert failure.status_code is None
    assert failure.retry_after_seconds is None
    assert failure.public_code == code
    assert failure.retry is retry
    assert failure.partial_text is partial
    assert failure.audit_outcome is outcome


def database_state(error: type[Exception]) -> database_errors.OperationalError:
    """An OperationalError whose driver error carries one SQLSTATE."""
    return database_errors.OperationalError(f"SELECT '{MARKER}'", None, error(MARKER))


@pytest.mark.parametrize(
    "error",
    [
        database_down(),
        database_state(pg.QueryCanceled),
        database_state(pg.AdminShutdown),
        database_state(pg.TooManyConnections),
        database_state(pg.SerializationFailure),
        database_errors.TimeoutError(MARKER),
    ],
    ids=[
        "unreachable",
        "statement-timeout",
        "shutdown",
        "too-many-connections",
        "serialization",
        "pool-exhausted",
    ],
)
def test_a_database_that_is_unavailable_is_a_dependency_failure(
    error: Exception,
) -> None:
    assert classify_failure(error) is FailureKind.DEPENDENCY


def test_a_database_error_that_is_a_bug_stays_internal() -> None:
    broken = database_errors.IntegrityError("INSERT", None, Exception(MARKER))

    assert classify_failure(broken) is FailureKind.INTERNAL


# --- Failures injected before the headers ---------------------------------------------


@pytest.mark.parametrize(
    ("error", "status", "code", "retry_after", "outcome"),
    [
        (database_down(), 503, "dependency_unavailable", "5", "dependency_error"),
        (EmbeddingRateLimited(MARKER), 429, "capacity_exceeded", "5", "throttled"),
        (
            EmbeddingInputRejected(RejectionReason.EMPTY_TEXT, index=0),
            422,
            "validation_error",
            None,
            "rejected",
        ),
        (AuthorizationDenied(MARKER), 403, "authorization_denied", None, "denied"),
        (RuntimeError(MARKER), 500, "internal_error", None, "internal_error"),
    ],
    ids=["database", "provider-limit", "validation", "denied", "bug"],
)
async def test_a_failure_in_the_search_is_an_http_error_with_retry_guidance(
    error: Exception,
    status: int,
    code: str,
    retry_after: str | None,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = Model()
    app = create_app(streamer=streamer(model, source=Source(error=error)))

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.headers.get("retry-after") == retry_after
    assert model.calls == 0
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("before_headers", outcome)
    assert record["partial_text"] == "none"
    assert not leaks(caplog, response.content, str(response.headers).encode())


# --- Failures injected after the headers ----------------------------------------------


def at(point: str, error: Exception) -> dict[str, Any]:
    """Where the failure happens once the stream has started."""
    if point == "before-text":
        return {"model": Model(parts=[], error=error)}  # type: ignore[arg-type]
    if point == "mid-text":
        return {"model": Model(error=error)}  # type: ignore[arg-type]
    return {"model": Model(), "finisher": Raising(error)}


@pytest.mark.parametrize(
    ("point", "error", "code", "retry", "outcome"),
    [
        (
            "before-text",
            ChatModelUnavailable(MARKER),
            "dependency_unavailable",
            "caller",
            "dependency_error",
        ),
        (
            "mid-text",
            ChatModelUnavailable(MARKER),
            "dependency_unavailable",
            "caller",
            "dependency_error",
        ),
        (
            "mid-text",
            ChatModelRateLimited(MARKER),
            "capacity_exceeded",
            "caller",
            "throttled",
        ),
        (
            "after-text",
            database_down(),
            "dependency_unavailable",
            "caller",
            "dependency_error",
        ),
        (
            "after-text",
            EmbeddingInputRejected(RejectionReason.EMPTY_TEXT, index=0),
            "internal_error",
            "none",
            "internal_error",
        ),
        (
            "after-text",
            RuntimeError(MARKER),
            "internal_error",
            "none",
            "internal_error",
        ),
    ],
    ids=[
        "provider-before-text",
        "provider-mid-text",
        "provider-limit-mid-text",
        "database-after-text",
        "validation-after-text",
        "bug-after-text",
    ],
)
async def test_a_failure_after_the_headers_ends_in_one_error_event(
    point: str,
    error: Exception,
    code: str,
    retry: str,
    outcome: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    sent = await chunks(streamer(**at(point, error)))

    assert names(sent)[0] == "metadata"
    assert names(sent)[-1] == "error"
    assert names(sent).count("error") == 1
    assert "citation" not in names(sent)
    data = error_data(sent)
    assert (data["code"], data["retry"]) == (code, retry)
    result = rebuilt(sent)
    assert result.outcome is StreamOutcome.FAILED
    # The text already streamed is replaced by the fixed message.
    assert result.text == ""
    assert result.message == data["message"]
    streamed = "text_delta" in names(sent)
    assert streamed is (point != "before-text")
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("after_headers", outcome)
    assert record["partial_text"] == ("replaced" if streamed else "none")
    assert not leaks(caplog, *sent)


async def test_a_finished_stream_records_what_the_client_keeps(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    sent = await chunks(streamer(Model()))

    assert rebuilt(sent).outcome is StreamOutcome.ANSWERED
    record = ended(caplog)
    assert (record["audit_outcome"], record["partial_text"]) == ("answered", "kept")


# --- Cancellation at several points ---------------------------------------------------


async def test_a_client_that_leaves_during_the_search_is_recorded_before_the_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    source = Source(wait=True)
    opening = asyncio.create_task(
        streamer(Model(), source=source).open(QUESTION, request_id=REQUEST_ID)
    )
    await asyncio.sleep(0)

    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening

    assert source.cancelled
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("before_headers", "cancelled")
    assert record["partial_text"] == "none"


async def test_a_client_that_leaves_mid_text_abandons_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model(hold=2)
    events = await streamer(model).open(QUESTION, request_id=REQUEST_ID)
    sent = [await anext(events), await anext(events)]

    await events.aclose()

    assert names(sent) == ["metadata", "text_delta"]
    assert model.closed
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("after_headers", "cancelled")
    assert record["partial_text"] == "abandoned"
    # What the client holds is cut off, and a reader refuses it.
    reader = AnswerEventReader()
    for chunk in sent:
        reader.feed(chunk)
    with pytest.raises(InvalidEventStream, match="truncated"):
        reader.close()


# --- The event contract follows the policy --------------------------------------------


def test_an_error_event_with_the_retry_owner_of_the_http_policy_is_refused() -> None:
    writer = AnswerEventWriter(REQUEST_ID)
    reader = AnswerEventReader()
    reader.feed(writer.metadata())
    sent = writer.error(FailureKind.DEPENDENCY)
    # Before the headers an outage is the service's to retry; not after.
    stale = sent.replace(b'"retry":"caller"', b'"retry":"service"')

    assert stale != sent
    with pytest.raises(InvalidEventStream, match="malformed"):
        reader.feed(stale)


# --- Review regressions ---------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        database_state(pg.ProgramLimitExceeded),
        database_state(pg.ObjectNotInPrerequisiteState),
        database_errors.InterfaceError("SELECT 1", None, Exception(MARKER)),
    ],
    ids=["program-limit", "wrong-state", "driver-misuse"],
)
async def test_a_database_error_retrying_cannot_fix_is_internal_without_retry_after(
    error: Exception,
) -> None:
    app = create_app(streamer=streamer(Model(), source=Source(error=error)))

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert classify_failure(error) is FailureKind.INTERNAL
    assert response.status_code == 500
    assert "retry-after" not in response.headers


class LeavesOnHeaders(Connection):
    """Leaves while the response headers are still being handed over."""

    async def send(self, message: Message) -> None:
        if message["type"] == "http.response.start":
            self.left.set()
            await asyncio.Event().wait()
        await super().send(message)


async def test_a_client_that_leaves_as_the_headers_go_out_is_after_the_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    model = Model()
    connection = LeavesOnHeaders(leave_after=1)
    app = create_app(streamer=streamer(model))

    await asyncio.wait_for(
        app(scope("2.4"), connection.receive, connection.send), timeout=5
    )

    assert connection.bodies == []
    record = ended(caplog)
    assert (record["phase"], record["audit_outcome"]) == ("after_headers", "cancelled")
    assert record["partial_text"] == "none"


async def test_text_sent_at_the_end_is_counted_when_the_client_leaves(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    # In this key order nothing streams live; the whole text goes at the end.
    final = json.dumps(
        {"citations": [CITED], "abstained": False, "reason": None, "answer": ANSWER}
    )
    events = await streamer(Model(final=final)).open(QUESTION, request_id=REQUEST_ID)
    sent = [await anext(events), await anext(events)]

    await events.aclose()

    assert names(sent) == ["metadata", "text_delta"]
    record = ended(caplog)
    assert record["characters"] == len(ANSWER)
    assert (record["audit_outcome"], record["partial_text"]) == (
        "cancelled",
        "abandoned",
    )


@pytest.mark.parametrize(
    ("finisher", "outcome", "partial"),
    [(None, "answered", "kept"), ("failing", "dependency_error", "replaced")],
    ids=["completion", "error"],
)
async def test_a_client_that_read_the_terminal_event_and_left_is_not_cancelled(
    finisher: str | None,
    outcome: str,
    partial: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    failing = Raising(database_down()) if finisher else None
    events = await streamer(Model(), finisher=failing).open(
        QUESTION, request_id=REQUEST_ID
    )
    sent: list[bytes] = []
    while not sent or names(sent)[-1] not in ("completion", "error"):
        sent.append(await anext(events))

    # It leaves before asking for more, while the stream waits at the end.
    await events.aclose()

    record = ended(caplog)
    assert (record["audit_outcome"], record["partial_text"]) == (outcome, partial)


class FailingWorkflow:
    async def answer(self, question: Question, *, request_id: UUID) -> Any:
        raise ChatModelUnavailable(MARKER)


async def test_the_answer_endpoint_gives_the_same_retry_guidance() -> None:
    app = create_app(workflow=cast(AnswerWorkflow, FailingWorkflow()))

    async with client(app) as http:
        response = await http.post("/v1/answer", json={"text": QUESTION.text})

    assert response.status_code == 503
    assert response.headers["retry-after"] == str(RETRY_AFTER_SECONDS)
```

Створіть `tests/test_stream_failures_integration.py`:

```python
"""Real database failures before the headers: an outage is a 503 with Retry-After."""

import logging
from collections.abc import AsyncIterator
from typing import cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from evidence_fakes import MARKER
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.settings import Settings
from test_streaming import (
    EVIDENCE,
    QUESTION,
    SIGNALS,
    Model,
    Source,
    client,
    streamer,
)

pytestmark = pytest.mark.integration

# Nothing listens on port 1, so the connection is refused at once.
UNREACHABLE = "postgresql://reader:secret@127.0.0.1:1/knowledge"


class DatabaseSource:
    """Find Evidence after one query, as a real source would."""

    def __init__(self, runtime: DatabaseRuntime, query: str) -> None:
        self.runtime = runtime
        self.query = query

    async def find(self, question: str, *, request_id: UUID) -> FoundEvidence:
        async with self.runtime.transaction() as connection:
            await connection.execute(sa.text(self.query), {"marker": MARKER})
        return FoundEvidence(EVIDENCE, SIGNALS)


@pytest.fixture
async def unreachable() -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(
            database_url=SecretStr(UNREACHABLE), database_connect_timeout_seconds=2
        )
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


@pytest.fixture
async def impatient(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(
            database_url=SecretStr(migrated_database),
            database_statement_timeout_ms=50,
        )
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def search_fails(
    runtime: DatabaseRuntime, query: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Search with one failing query, then check what the client and log got."""
    caplog.set_level(logging.DEBUG)
    model = Model()
    source = cast(Source, DatabaseSource(runtime, query))
    app = create_app(streamer=streamer(model, source=source))

    async with client(app) as http:
        response = await http.post("/v1/answer/stream", json={"text": QUESTION.text})

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert response.headers["retry-after"] == "5"
    assert model.calls == 0
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    stream = ended.answer_stream  # type: ignore[attr-defined]
    assert stream["audit_outcome"] == "dependency_error"
    assert stream["failure"] == "OperationalError"
    exposed = [response.text, str(response.headers), caplog.text]
    assert not any(MARKER in text or "secret" in text for text in exposed)


async def test_an_unreachable_database_is_a_503_with_retry_after(
    unreachable: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    await search_fails(unreachable, "SELECT :marker", caplog)


async def test_a_database_too_slow_for_its_timeout_is_a_503_with_retry_after(
    impatient: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    await search_fails(impatient, "SELECT pg_sleep(1), :marker", caplog)
```

Перевірка:

```bash
uv run pytest tests/test_stream_failures.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_stream_failures_integration.py -m integration -q
```

Очікувано: 42 passed, потім 2 passed.

### Крок 6 — Без коду: зламайте і вирішіть

Цей крок показує, що захищає правило для бази, і фіксує, чого ви навчилися.

**Дія 1:** навмисно зламайте класифікацію. У `src/knowledge_service/errors.py`, у `classify_failure`, видаліть ці два рядки:

```python
    if _database_unavailable(error):
        return FailureKind.DEPENDENCY
```

Запустіть:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_stream_failures_integration.py -m integration -q
```

Очікувано: 2 failed: обидва справжні збої бази тепер дають `500 internal_error` без `Retry-After`. Юніт-тести, що впорскують помилку бази, теж падають (9 із 42), бо проблема не в таблиці політики, а в класифікації. Поверніть рядки і запускайте знову, доки все не пройде.

**Дія 2:** під `## Decision` напишіть:

- чому збій після заголовків не може змінити статус і що клієнт має зробити з уже отриманим текстом;
- чому до заголовків повтор після збою залежності — справа сервісу, а після них — клієнта;
- чому збій валідації після заголовків повідомляється як внутрішній;
- чому лише деякі помилки бази — збої залежності;
- чому кінець потоку записується до того, як останню подію передано;
- що лишається на потім: збережена подія аудиту, коли з'явиться перевірений актор (M07), і нова версія протоколу для наступної зміни, яку помітив би випущений клієнт;
- щонайменше одне **спростоване припущення** у формі «Я припускав X. Прогін чи рев'ю показує Y. Отже, Z хибне».

### Крок 7 — Без коду: архітектура, перевірки, evidence і прогрес

Цей крок оновлює текст, який зміна зробила неповним, доводить, що все досі працює, і фіксує результат.

**Дія 1:** додайте політику до рядка `answering` і дві фази — до діаграми відповіді.

У `docs/architecture.md`:

**1.** Знайдіть:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

Замініть на:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, and what became of its partial text), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

**2.** Знайдіть:

````markdown
    DB-->>Retrieval: authorized Candidates
    Retrieval->>Ranker: bounded Candidate set
    Ranker-->>Retrieval: ranked Evidence
    API->>Model: instructions + numbered Evidence
    Model-->>API: streamed Answer events
    API->>API: validate citations
    API-->>User: Answer or safe failure
```

## Deliberate exclusions from the default path

- LangGraph and multi-agent orchestration do not replace ordinary question answering.
- MCP is an additional protocol adapter, not the owner of search logic.
````

Замініть на:

````markdown
    DB-->>Retrieval: authorized Candidates
    Retrieval->>Ranker: bounded Candidate set
    Ranker-->>Retrieval: ranked Evidence
    API->>Model: instructions + numbered Evidence
    Model-->>API: streamed Answer events
    API->>API: validate citations
    alt a failure before the headers
        API-->>User: HTTP error, with Retry-After for 503 or 429
    else after the headers
        API-->>User: text, Citations, usage, then completion or one error event
    end
```

## Deliberate exclusions from the default path

- LangGraph and multi-agent orchestration do not replace ordinary question answering.
- MCP is an additional protocol adapter, not the owner of search logic.
````

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_stream_failures.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний прогін дав 2297 passed для `make test-unit`, на 42 більше, ніж після M06-T12, і 378 passed для `make test-integration`, на 2 більше. Якщо пише, що тестової бази немає, запустіть її командою `docker start knowledge-service-postgres`.

**Дія 3:** заповніть evidence-файл:

- **Design:** дві фази і таблиця, правило для бази, поради щодо повтору, частковий текст, запис і коли змінюється фаза.
- **Verification:** команди й лічильники, навмисна поломка і відомі обмеження з «Перед початком».
- **Review:** незалежне агресивне рев'ю впорскувало збої і відходи клієнта в кожну точку потоку: кожен потік завершився рівно однією термінальною подією, і жоден маркер не витік. Воно знайшло п'ять проблем, усі виправлено з тестами, що падали до виправлення: невиправна помилка бази давала `503`; клієнта, що пішов у мить відправлення заголовків, записано як до заголовків; текст наприкінці не рахувався для клієнта, що пішов із ним; клієнта, що прочитав термінальну подію і пішов, записано як скасування; `/v1/answer` не мав `Retry-After`. Одне обмеження задокументовано: суворіша перевірка в читачі залишила версію протоколу `1`.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу виконаною, додайте рядок M06-T13 у `docs/curriculum/PROGRESS.md`, встановіть наступною задачею M06-T14 і закомітьте з `m06-t13: map streaming failures safely`.

## Запитання для рефлексії

1. Що `stream_failure` приховує від ендпоінта, запису подій і читача? Перелічіть, чого кожному з них більше не треба вирішувати.
2. Хтось хоче, щоб сервіс сам повторював виклик моделі, коли вона ламається посеред відповіді. Що побачив би клієнт і що мало б змінитися в політиці та подіях?
3. Повільна база змушує кожен запит чекати свого тайм-ауту, і кожен отримує `503` з `Retry-After: 5`. Що станеться, якщо тисяча клієнтів повторить запит рівно через п'ять секунд, і що б ви змінили?
4. Яка зміна під час пізнішої оптимізації найлегше зламала б правило «записано до передачі», і який тест це помітить?
5. Який збій ви б упорснули наступним, щоб пошукати потік без термінальної події чи з неправильним аудит-результатом, і що, на вашу думку, побачите?

## Чек-лист завершення

- [ ] Evidence-файл містить інваріант і ваші передбачення, записані до коду.
- [ ] `stream_failure` дає поведінку до і після заголовків для кожного виду збою.
- [ ] До заголовків збій залежності чи ліміт — це HTTP-помилка з `Retry-After`; після них — одна подія `error`, яку клієнт може повторити.
- [ ] Після заголовків збій валідації повідомляється як внутрішній.
- [ ] База, що недоступна, перевантажена чи надто повільна, дає `503`; будь-яка інша помилка бази лишається `500`.
- [ ] Запис і читання подій погоджуються щодо порад «після заголовків».
- [ ] Кожен потік записано один раз із фазою, аудит-результатом і долею часткового тексту, також коли клієнт іде в самому кінці.
- [ ] Жодна відповідь, заголовок чи запис не містить запитання, відповіді, SQL чи рядка підключення.
- [ ] Ви виконали навмисну поломку, побачили, що тести падають, і скасували її.
- [ ] Усі перевірки проходять: формат, lint, типи, unit, integration, docs.
- [ ] Evidence, статус задачі, рядок у PROGRESS і коміт готові.

Коли напишете файли, поділіться ними або виводом помилок, і я перевірю їх за цим чек-листом.
