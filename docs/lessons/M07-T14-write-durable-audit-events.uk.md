# M07-T14 — Стійкі події аудиту

Англійська версія: [M07-T14-write-durable-audit-events.md](M07-T14-write-durable-audit-events.md)

Задача: [M07-T14 — Write durable audit events](../curriculum/milestones/M07-identity-and-authorization/M07-T14-write-durable-audit-events.md)

## Що ви зробите

Цей урок навчить сервіс вести постійний запис того, хто що зробив. Запис охоплює шість дій: поставити Питання, прочитати Документ через Цитату, змінити, хто може читати Документ, синхронізувати Джерело, оцінити Повідомлення і зміну, яку робить адміністратор. Кожен запис каже, хто діяв, що зробив, з яким об'єктом, чи це було дозволено, чим закінчилося, до якого запиту чи прогону належить і коли сталося. Він ніколи не містить Питання, Відповіді, заголовка чи тексту Документа.

- **Одна подія на дію.** Кожне з шести місць пише один рядок у таблицю `audit_events`.
- **Разом зі зміною.** Якщо дія змінює дані, її запис пишеться в тій самій транзакції бази, тож зберігаються або обидва, або жоден.
- **Перш ніж відповідь піде.** Запис Питання зберігається до того, як надіслано його Цитати й фінальну подію. Якщо зберегти його не вдалося, клієнт отримує помилку.
- **Без вмісту.** Кожне поле — ідентифікатор, час або значення з фіксованого списку. Маленький додатковий набір «міток» містить лише короткі назви, невеликі числа й окремі слова.
- **Доказ:** тести виконують кожну дію на справжньому PostgreSQL, читають журнал назад і шукають у кожному рядку підкинуті слова.

| Задача вимагає | Де це відбувається |
|---|---|
| Виконавець, дія, ID об'єкта, рішення, результат, ID запиту чи прогону, час | Крок 2: модель події; крок 3: таблиця |
| Без сирого чутливого вмісту | Крок 2: правила міток; крок 3: перевірки в базі |
| Запит | Крок 7: стрім відповіді пише `question.asked` |
| Читання Документа | Крок 6: перевірка Цитат пише `document.read` |
| Зміна доступу | Крок 4: кожен запис дозволів на доступ фіксує `document.access_changed` |
| Синхронізація | Крок 4: кожен прохід фіксує `synchronization.finished` |
| Відгук | Крок 5: `rate_message` фіксує `message.rated` |
| Адміністрування | Крок 5: запуск і перемикання міграції ембедингів |
| Успіх, найризиковіший край, типізована відмова чи заборона | Крок 8: записана відповідь; стрім, скасований під час запису; `InvalidAuditEvent` і читання з `denied` |
| Телеметрія не містить підкинутого тексту | Крок 8: тести стріму перевіряють записи логів |
| Оновити лише те, що зміна зробила неточним | Крок 10: `docs/architecture.md`, `docs/threat-model.md` |

У цьому уроці є код: кроки 2–8 пишуть код і тести. Кроки 1, 9 і 10 без коду, хоча крок 10 редагує два документи.

Терміни уроку:

- **Подія аудиту (audit event):** один рядок, який каже, що дія сталася. Як журнал відвідувачів у будівлі: хто зайшов, у яку кімнату, коли і чи пропустила його охорона.
- **Виконавець (actor):** Користувач, який діяв, або ніхто (None), коли сервіс діяв сам, наприклад під час синхронізації.
- **Рішення (decision):** чи дію дозволено: `allowed` або `denied`.
- **Результат (outcome):** чим дія закінчилася, з фіксованого списку: `succeeded`, `failed` або, для Питання, `answered`, `abstained`, `cancelled` і види збоїв, які стрім уже знає.
- **ID запиту і ID прогону (request ID, run ID):** ідентифікатор одного HTTP-запиту і одного Прогону синхронізації (одного проходу по Джерелу). За ними можна знайти всі події одного запиту чи одного проходу.
- **Стійкий (durable):** закомічений у базу, тож переживе збій після коміту.
- **Лише додавання (append-only):** рядки тільки додаються; жоден код їх не змінює і не видаляє.

## Перед початком

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілим, коли ви до нього дійдете.

- **Що вже було.** Таблиця `audit_events` і `AuditEvent` з'явилися в M02. Писало в них лише одне місце: Документ, що стає видаленим (tombstoned) або знову доступним. У таблиці не було рішення, результату, ID запиту чи ID прогону.
- **Одна таблиця, більше стовпців.** Нові поля додаються в ту саму таблицю однією міграцією. Старі рядки всі були дозволені й успішні, тож міграція один раз це заповнює, а потім прибирає значення за замовчуванням. Відтоді кожна подія мусить сама вказати обидва.
- **Мітки, а не текст.** Старе правило пропускало будь-який рядок до 64 символів, тож коротке Питання могло влізти. Тепер ключ метаданих — коротка назва в snake_case, а рядкове значення — одне слово з ASCII-літер, цифр і `_ . : / -`. У реченні є пробіли, тому воно відхиляється. Числа мають бути меншими за 2**53, тож величезне число теж не закодує текст.
- **Та сама транзакція, що й зміна.** Зміна доступу, оцінка і крок міграції пишуть свою подію через те саме з'єднання з базою, що й зміну. Якщо зміна відкочується, її запис теж.
- **Читання фіксуються там, де ухвалюється рішення.** Перевірка Цитат (M07-T09) уже перечитує кожен процитований Документ від імені читача. Тепер вона у своїй транзакції пише одну подію `document.read` на кожен процитований Документ: `allowed`, якщо всі його Цитати пройшли, `denied`, якщо хоч одна не пройшла.
- **Питання фіксується до того, як піде відповідь.** Стрім пише `question.asked`, щойно відоме рішення, до Цитат і фінальної події. Якщо запис не вдався або триває довше двох секунд, стрім закінчується подією `error`. Помилка чи клієнт, що пішов, фіксуються, коли стрім закінчується.
- **Скасування не обриває запис.** Коли клієнт іде, сервер скасовує задачу стріму. Тому запис виконується окремою задачею, і стрім чекає на нього до дедлайну, перш ніж здатися.
- **Синхронізація отримує ID прогону.** Кожен прохід `synchronize_source` отримує ID Прогону синхронізації. Кожна зміна доступу, яку він пише, називає цей прогін, а прохід закінчується однією подією `synchronization.finished`: `succeeded` з лічильниками або `failed` лише з назвою типу помилки. Власна помилка проходу завжди піднімається далі, навіть якщо її запис не вдалося зберегти.
- **Один спільний список результатів.** `AuditOutcome` стріму переїжджає з `stream_failures` в `audit` і отримує `succeeded` і `failed`. `stream_failures` його імпортує, тож наявний код працює як раніше.
- **Що знайшло рев'ю.** Незалежне рев'ю, що шукало слабкі місця, атакувало першу версію. Воно знайшло такі проблеми; усі виправлено, і на кожну є тест, що падає на першій версії:
  - Клієнт, що йшов під час запису рішення, губив запис. Щит `anyio` не зупиняв власного скасування сервера, тож тепер запис іде окремою задачею.
  - Величезне ціле число в мітці могло закодувати речення або зламати вставку нетипізованою помилкою. Тепер цілі мають бути меншими за 2**53.
  - Відображення (mapping), що брехало про свою довжину, могло пронести понад вісім міток. Тепер записи рахуються під час читання.
  - Відкат міграції і повторне оновлення перетворювали рядок `denied` назад на `allowed`. Тепер відкат видаляє рядки, які стара схема не може зберегти правдиво.
  - `publish_document` писав дозволи на доступ без запису. Тепер він їх теж фіксує.
  - Події, прочитані з сесією не в UTC, не завантажувалися. Тепер час переводиться в UTC під час читання.
- **Що знайшло завершальне рев'ю.** Ще два рев'ю, одне щодо стандартів, друге щодо задачі, перевірили готовий код:
  - Перше виправлення чекало на запис, ловлячи кожне скасування й піднімаючи нове. anyio, яким користується сервер, не впізнавав це нове, тож воно вилітало за межі свого cancel scope, а очікування крутилося тисячі разів. Тепер очікування йде під щитом anyio, а скасування від asyncio піднімається знову тим самим об'єктом.
  - `publishing` імпортував `access_event` з `reconciliation`, модуля вищого рівня. Тепер функція живе у власному маленькому модулі `ingestion/audit_events.py`.
  - Тест на відкат був лише для оцінок; тепер він є й для запису дозволів на доступ. HTTP-тест тепер ще й шукає підкинуте слово в записах логу.
  - Подія `synchronization.finished` брала час початку проходу. Тепер вона бере час, коли прохід закінчився.
- **Відомі межі.** Вони лишаються відкритими і записані в моделі загроз:
  - Виклик, відхилений за роллю (403) до відкриття стріму, не лишає події аудиту.
  - Подія `document.read` фіксує рішення перевірки Цитат, а не доставку, тож пізніша відмова все одно лишає читання з `allowed`.
  - Адмініструванням вважаються лише запуск і перемикання міграції ембедингів, і виклики передають ID адміністратора вручну, доки не з'явиться точка входу.
  - Користувач бази, від імені якого працює сервіс, усе ще може змінювати чи видаляти рядки аудиту.
  - Якщо фінальний запис стріму не вдався після помилки чи скасування, про це каже лише запис телеметрії (`audited: false`).
  - Скасований прохід синхронізації нічого не фіксує, а прохід, чий успіх не вдалося записати, піднімає помилку, хоча його робота вже закомічена.
  - Оцінки й кроки міграції не мають ID запиту, доки HTTP-точка входу його не передаватиме.
  - Жодна production-точка входу ще не збирає стрім відповіді з `DatabaseAuditTrail`; той, хто її писатиме, мусить це зробити.

## Покроково

Перед інтеграційними перевірками запустіть PostgreSQL і задайте URL тестової бази у своєму терміналі:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

### Крок 1 — Без коду в цьому кроці: запишіть інваріант і передбачення

Перш ніж щось змінювати, запишіть, що має бути правдою, коли урок завершено, і чого ви очікуєте. Крок 9 перевіряє останнє передбачення.

Створіть `docs/evidence/M07-T14-write-durable-audit-events.md`:

```markdown
# M07-T14 — Write durable audit events evidence

## Invariant

Each of the six audited actions writes exactly one durable audit event per
object it acts on: who acted, the action, the object, `allowed` or
`denied`, how it ended, the request or Synchronization Run, and when. An
event that describes a change commits in the same transaction as the
change; a Question's event commits before its Citations and completion are
sent, or the stream ends with an error. No event holds a Question, an
Answer, a title, or a Document's text.

## Expected failing check

Before the change, `uv run pytest tests/test_audit_events_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_audit_events_integration.py`.

## Predictions

- What a client receives when the audit store is down at the moment the stream decides:
- Which audit events one answered Question writes, and in which order:
- What the event of a failed Synchronization Run holds:
- How many tests in `tests/test_audit_events_integration.py` fail if Access Grant writes stop recording their event:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після кожної двокрапки в передбаченнях є ваша відповідь.

### Крок 2 — Крок із кодом: подія і як вона зберігається

**Простими словами:** цей крок визначає, що містить один запис аудиту, і узгоджує з ним код, який його зберігає й читає. Тепер запис ще каже, чи дію дозволено, чим вона закінчилася і до якого запиту чи прогону належить. Він відхиляє все, що схоже на текст: речення, довге число чи поле не того типу. Нові частини поки ніхто не викликає; це роблять наступні кроки.

| Зміна | Що вона робить |
|---|---|
| `audit.py` | Нові дії й види об'єктів; `AuditDecision`; `AuditOutcome` (переїхав сюди); чотири нові поля; суворі правила міток; точна перевірка типів |
| `stream_failures.py` | Імпортує `AuditOutcome` замість того, щоб оголошувати |
| `deletions.py` | Наявна подія доступності вказує `allowed` і `succeeded` |
| `persistence/audit.py` | Зберігає й читає нові поля; читає час як UTC; додає `load_request_audit_events` |

Замініть увесь `src/knowledge_service/audit.py` на:

```python
"""Audit event values: what happened, without the content it happened to.

An event says who acted (`actor_id`, or None for the service itself), what
they did (`action`), to what (`target_kind` and `target_id`), whether it was
allowed (`decision`), how it ended (`outcome`), which request or run it
belongs to (`request_id`, `run_id`), and when (`occurred_at`).

What an event promises:

- **No content.** Every field is an identifier, a time, or a value from a
  fixed list. The small `metadata` bag holds at most eight labels: a key is
  a short snake_case name, and a value is an integer below 2**53 in size, a
  boolean, or one word of ASCII letters, digits, and `_ . : / -`. A
  Question, an Answer, a title, or a Document's text has spaces or other
  characters, so it cannot be recorded here by accident.
- **Checked types.** Each field must have its exact type, so a plain string
  never stands in for an action or a decision, and a time must be UTC.
- **Append-only.** Events are only ever added (see `persistence.audit`).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import cast
from uuid import UUID

from knowledge_service.identifiers import AuditEventId, UserId

MAX_METADATA_VALUE_LENGTH = 64
MAX_METADATA_ENTRIES = 8
# Integers stay exact in any JSON reader, and too small to spell a sentence.
MAX_METADATA_INTEGER = 2**53

AuditValue = str | int | bool

# A key is a short snake_case name; a string value is one plain word.
_KEY = re.compile(r"[a-z][a-z0-9_]{0,31}")
_WORD = re.compile(r"[A-Za-z0-9_.:/-]+")


class InvalidAuditEvent(ValueError):
    """An audit event violates a domain invariant."""


class AuditAction(StrEnum):
    """What an actor did."""

    SOURCE_REGISTERED = "source.registered"
    DOCUMENT_AVAILABILITY_CHANGED = "document.availability_changed"
    CONVERSATION_DELETED = "conversation.deleted"
    JOB_QUEUED = "job.queued"
    JOB_FAILED = "job.failed"
    EVALUATION_RUN_STARTED = "evaluation_run.started"
    QUESTION_ASKED = "question.asked"
    DOCUMENT_READ = "document.read"
    DOCUMENT_ACCESS_CHANGED = "document.access_changed"
    SYNCHRONIZATION_FINISHED = "synchronization.finished"
    MESSAGE_RATED = "message.rated"
    EMBEDDING_MIGRATION_STARTED = "embedding_migration.started"
    EMBEDDING_MIGRATION_SWITCHED = "embedding_migration.switched"


class AuditTargetKind(StrEnum):
    """What an action was done to."""

    SOURCE = "source"
    DOCUMENT = "document"
    CONVERSATION = "conversation"
    JOB = "job"
    EVALUATION_RUN = "evaluation_run"
    REQUEST = "request"
    MESSAGE = "message"
    EMBEDDING_MIGRATION = "embedding_migration"


class AuditDecision(StrEnum):
    """Whether the action was permitted."""

    ALLOWED = "allowed"
    DENIED = "denied"


class AuditOutcome(StrEnum):
    """How the action ended.

    `succeeded` and `failed` are for actions that either happen or not; the
    rest are how a streamed Answer can end.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ANSWERED = "answered"
    ABSTAINED = "abstained"
    REJECTED = "rejected"
    DENIED = "denied"
    DEPENDENCY_ERROR = "dependency_error"
    THROTTLED = "throttled"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


def _require_utc(value: object, field_name: str) -> None:
    if type(value) is not datetime or value.utcoffset() != timedelta(0):
        raise InvalidAuditEvent(f"{field_name} must be a UTC-aware timestamp")


def _checked_metadata(metadata: object) -> Mapping[str, AuditValue]:
    if not isinstance(metadata, Mapping):
        raise InvalidAuditEvent("metadata must be a mapping")
    entries = cast("Mapping[object, object]", metadata)

    checked: dict[str, AuditValue] = {}
    # Counted while reading, so a mapping cannot hide entries behind its len.
    for key, value in entries.items():
        if len(checked) >= MAX_METADATA_ENTRIES:
            raise InvalidAuditEvent(
                f"metadata holds at most {MAX_METADATA_ENTRIES} entries"
            )
        if type(key) is not str or not key.strip():
            raise InvalidAuditEvent("metadata keys must not be empty")
        if _KEY.fullmatch(key) is None:
            raise InvalidAuditEvent("metadata keys must be short snake_case names")
        if type(value) is str:
            if not value.strip():
                raise InvalidAuditEvent("metadata values must not be empty")
            if len(value) > MAX_METADATA_VALUE_LENGTH:
                raise InvalidAuditEvent(
                    "metadata values must be at most"
                    f" {MAX_METADATA_VALUE_LENGTH} characters"
                )
            if _WORD.fullmatch(value) is None:
                raise InvalidAuditEvent("metadata values must be one plain word")
        elif type(value) is int:
            if not -MAX_METADATA_INTEGER < value < MAX_METADATA_INTEGER:
                raise InvalidAuditEvent("metadata integers must be below 2**53")
        elif type(value) is not bool:
            raise InvalidAuditEvent(
                "metadata values must be short strings, integers, or booleans"
            )
        checked[key] = value
    return MappingProxyType(checked)


def _require(value: object, expected: type, field_name: str) -> None:
    if type(value) is not expected:
        raise InvalidAuditEvent(f"{field_name} must be a {expected.__name__}")


def _require_optional(value: object, expected: type, field_name: str) -> None:
    if value is not None:
        _require(value, expected, field_name)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One recorded action, with no raw content."""

    event_id: AuditEventId
    occurred_at: datetime
    actor_id: UserId | None
    action: AuditAction
    target_kind: AuditTargetKind
    target_id: UUID
    decision: AuditDecision
    outcome: AuditOutcome
    request_id: UUID | None
    run_id: UUID | None
    metadata: Mapping[str, AuditValue]

    def __post_init__(self) -> None:
        _require(self.event_id, AuditEventId, "event_id")
        _require_utc(self.occurred_at, "occurred_at")
        _require_optional(self.actor_id, UserId, "actor_id")
        _require(self.action, AuditAction, "action")
        _require(self.target_kind, AuditTargetKind, "target_kind")
        _require(self.target_id, UUID, "target_id")
        _require(self.decision, AuditDecision, "decision")
        _require(self.outcome, AuditOutcome, "outcome")
        _require_optional(self.request_id, UUID, "request_id")
        _require_optional(self.run_id, UUID, "run_id")
        object.__setattr__(self, "metadata", _checked_metadata(self.metadata))
```

Серце правила «без вмісту» — правила міток: `_KEY` і `_WORD` пропускають лише назву й одне слово, а межа для цілих не дає числу нести текст.

У `src/knowledge_service/answering/stream_failures.py`:

**1.** Знайдіть:

```python
  stream, which a reader refuses. Nothing streamed means nothing to keep.
- **The audit outcome.** Each stream ends with exactly one outcome from a
  closed list: answered, abstained, one of the M01 telemetry outcomes for
  a failure, or cancelled. It is recorded with the phase and what happened
  to the partial text, never a text.
- **Cancellation is not an error.** A client that left gets no status and
  no event; the outcome is `cancelled`.
"""
```

Замініть на:

```python
  stream, which a reader refuses. Nothing streamed means nothing to keep.
- **The audit outcome.** Each stream ends with exactly one outcome from a
  closed list: answered, abstained, one of the M01 telemetry outcomes for
  a failure, or cancelled (`AuditOutcome`, shared with every audit event in
  `knowledge_service.audit`). It is recorded with the phase and what
  happened to the partial text, never a text.
- **Cancellation is not an error.** A client that left gets no status and
  no event; the outcome is `cancelled`.
"""
```

**2.** Знайдіть:

```python
from enum import StrEnum

from knowledge_service.answering.abstention import Decision
from knowledge_service.errors import FailureKind, RetryOwner, policy_for

# How long a client should wait before sending the Question again, when
```

Замініть на:

```python
from enum import StrEnum

from knowledge_service.answering.abstention import Decision
from knowledge_service.audit import AuditOutcome
from knowledge_service.errors import FailureKind, RetryOwner, policy_for

# How long a client should wait before sending the Question again, when
```

**3.** Знайдіть:

```python

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
```

Замініть на:

```python

    BEFORE_HEADERS = "before_headers"
    AFTER_HEADERS = "after_headers"


class PartialText(StrEnum):
```

У `src/knowledge_service/ingestion/deletions.py`:

**1.** Знайдіть:

```python

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import AuditAction, AuditEvent, AuditTargetKind
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import AuditEventId, DocumentId, JobId, SourceId
```

Замініть на:

```python

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import AuditEventId, DocumentId, JobId, SourceId
```

**2.** Знайдіть:

```python
        action=AuditAction.DOCUMENT_AVAILABILITY_CHANGED,
        target_kind=AuditTargetKind.DOCUMENT,
        target_id=document.document_id.value,
        metadata={"availability": document.availability.value},
    )

```

Замініть на:

```python
        action=AuditAction.DOCUMENT_AVAILABILITY_CHANGED,
        target_kind=AuditTargetKind.DOCUMENT,
        target_id=document.document_id.value,
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=None,
        run_id=None,
        metadata={"availability": document.availability.value},
    )

```

Замініть увесь `src/knowledge_service/persistence/audit.py` на:

```python
"""Record and read Audit Events.

Events are only ever added: there is no function that updates or deletes
one. A caller records an event on the same connection, and so in the same
transaction, as the change it describes, so the change and its record
commit together or not at all.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
    AuditValue,
    InvalidAuditEvent,
)
from knowledge_service.identifiers import AuditEventId, UserId
from knowledge_service.persistence.schema import audit_events


def audit_trail_statement(target_id: UUID, limit: int) -> sa.Select[Any]:
    """Select the trail recorded against one target, oldest first."""
    return (
        sa.select(audit_events)
        .where(audit_events.c.target_id == target_id)
        .order_by(audit_events.c.occurred_at, audit_events.c.event_id)
        .limit(limit)
    )


def request_trail_statement(request_id: UUID, limit: int) -> sa.Select[Any]:
    """Select the trail recorded for one request, oldest first."""
    return (
        sa.select(audit_events)
        .where(audit_events.c.request_id == request_id)
        .order_by(audit_events.c.occurred_at, audit_events.c.event_id)
        .limit(limit)
    )


def _audit_row(event: AuditEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id.value,
        "occurred_at": event.occurred_at,
        "actor_id": event.actor_id.value if event.actor_id is not None else None,
        "action": event.action.value,
        "target_kind": event.target_kind.value,
        "target_id": event.target_id,
        "decision": event.decision.value,
        "outcome": event.outcome.value,
        "request_id": event.request_id,
        "run_id": event.run_id,
        "metadata": dict(event.metadata),
    }


def _audit_from(record: Mapping[str, object]) -> AuditEvent:
    try:
        action = AuditAction(cast(str, record["action"]))
        target_kind = AuditTargetKind(cast(str, record["target_kind"]))
        decision = AuditDecision(cast(str, record["decision"]))
        outcome = AuditOutcome(cast(str, record["outcome"]))
    except ValueError:
        raise InvalidAuditEvent(
            "stored audit action, target kind, decision, or outcome is not a"
            " known value"
        ) from None

    actor_id = cast("UUID | None", record["actor_id"])
    stored = cast("dict[str, object]", record["metadata"])
    return AuditEvent(
        event_id=AuditEventId(cast(UUID, record["event_id"])),
        # The session's time zone decides the offset psycopg returns.
        occurred_at=cast(datetime, record["occurred_at"]).astimezone(UTC),
        actor_id=UserId(actor_id) if actor_id is not None else None,
        action=action,
        target_kind=target_kind,
        target_id=cast(UUID, record["target_id"]),
        decision=decision,
        outcome=outcome,
        request_id=cast("UUID | None", record["request_id"]),
        run_id=cast("UUID | None", record["run_id"]),
        metadata=cast("dict[str, AuditValue]", stored),
    )


async def record_audit_event(
    connection: AsyncConnection, event: AuditEvent
) -> AuditEventId:
    """Append one audit event. There is no function that updates or deletes one."""
    if type(event) is not AuditEvent:
        raise TypeError("event must be an AuditEvent")
    await connection.execute(sa.insert(audit_events).values(_audit_row(event)))
    return event.event_id


async def load_audit_events(
    connection: AsyncConnection, *, target_id: UUID, limit: int
) -> tuple[AuditEvent, ...]:
    """Read the trail recorded against one target, oldest first."""
    statement = audit_trail_statement(target_id=target_id, limit=limit)
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_audit_from(dict(record)) for record in records)


async def load_request_audit_events(
    connection: AsyncConnection, *, request_id: UUID, limit: int
) -> tuple[AuditEvent, ...]:
    """Read the trail recorded for one request, oldest first."""
    statement = request_trail_statement(request_id=request_id, limit=limit)
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_audit_from(dict(record)) for record in records)
```

**Перевірка:** `uv run pyright src` показує 0 помилок. Тести оновлюються в кроці 3.

### Крок 3 — Крок із кодом: таблиця і тести, що пишуть рядки

**Простими словами:** таблиця в базі отримує чотири нові стовпці, нові дозволені значення й індекс, щоб знаходити всі події одного запиту. Одна міграція так само змінює наявні бази. Тести, які писали рядки аудиту вручну, тепер задають нові стовпці, а тести подій вивчають нові правила.

| Зміна | Що вона робить |
|---|---|
| `schema.py` | Оголошує нові стовпці, перевірки й індекс за запитом |
| Міграція | Додає стовпці, один раз заповнює старі рядки, розширює списки; відкат видаляє рядки, які стара схема не може зберегти правдиво |
| `tests/test_audit.py` | Будує події з новими полями; перевіряє речення, ключі, великі цілі, брехливе відображення і неправильні типи |
| `tests/test_records_integration.py`, `tests/test_indexes.py`, `tests/test_indexes_integration.py` | Задають нові стовпці; знають новий індекс |

У `src/knowledge_service/persistence/schema.py`:

**1.** Знайдіть:

```python
    sa.Column("target_kind", sa.String(length=24), nullable=False),
    sa.Column("target_id", sa.Uuid(), nullable=False),
    sa.Column("metadata", JSONB, nullable=False),
    sa.CheckConstraint(
        "action in ('source.registered', 'document.availability_changed',"
        " 'conversation.deleted', 'job.queued', 'job.failed',"
        " 'evaluation_run.started')",
        name="ck_audit_events_action",
    ),
    sa.CheckConstraint(
        "target_kind in ('source', 'document', 'conversation', 'job',"
        " 'evaluation_run')",
        name="ck_audit_events_target_kind",
    ),
    sa.CheckConstraint(
        "pg_column_size(metadata) <= 1024", name="ck_audit_events_metadata_size"
```

Замініть на:

```python
    sa.Column("target_kind", sa.String(length=24), nullable=False),
    sa.Column("target_id", sa.Uuid(), nullable=False),
    sa.Column("metadata", JSONB, nullable=False),
    sa.Column("decision", sa.String(length=16), nullable=False),
    sa.Column("outcome", sa.String(length=24), nullable=False),
    sa.Column("request_id", sa.Uuid(), nullable=True),
    sa.Column("run_id", sa.Uuid(), nullable=True),
    sa.CheckConstraint(
        "action in ('source.registered', 'document.availability_changed',"
        " 'conversation.deleted', 'job.queued', 'job.failed',"
        " 'evaluation_run.started', 'question.asked', 'document.read',"
        " 'document.access_changed', 'synchronization.finished',"
        " 'message.rated', 'embedding_migration.started',"
        " 'embedding_migration.switched')",
        name="ck_audit_events_action",
    ),
    sa.CheckConstraint(
        "target_kind in ('source', 'document', 'conversation', 'job',"
        " 'evaluation_run', 'request', 'message', 'embedding_migration')",
        name="ck_audit_events_target_kind",
    ),
    sa.CheckConstraint(
        "decision in ('allowed', 'denied')", name="ck_audit_events_decision"
    ),
    sa.CheckConstraint(
        "outcome in ('succeeded', 'failed', 'answered', 'abstained', 'rejected',"
        " 'denied', 'dependency_error', 'throttled', 'cancelled',"
        " 'internal_error')",
        name="ck_audit_events_outcome",
    ),
    sa.CheckConstraint(
        "pg_column_size(metadata) <= 1024", name="ck_audit_events_metadata_size"
```

**2.** Знайдіть:

```python
        "target_id",
        "occurred_at",
        "event_id",
    ),
)

```

Замініть на:

```python
        "target_id",
        "occurred_at",
        "event_id",
    ),
    sa.Index(
        "ix_audit_events_request_occurred_at",
        "request_id",
        "occurred_at",
        "event_id",
        postgresql_where=sa.text("request_id is not null"),
    ),
)

```

Створіть `migrations/versions/b1c2d3e4f5a6_audit_decisions_and_requests.py`:

```python
"""audit decisions and requests

Revision ID: b1c2d3e4f5a6
Revises: a9b0c1d2e3f4
Create Date: 2026-11-20 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "a9b0c1d2e3f4"

OLD_ACTIONS = (
    "action in ('source.registered', 'document.availability_changed',"
    " 'conversation.deleted', 'job.queued', 'job.failed',"
    " 'evaluation_run.started')"
)
NEW_ACTIONS = (
    "action in ('source.registered', 'document.availability_changed',"
    " 'conversation.deleted', 'job.queued', 'job.failed',"
    " 'evaluation_run.started', 'question.asked', 'document.read',"
    " 'document.access_changed', 'synchronization.finished',"
    " 'message.rated', 'embedding_migration.started',"
    " 'embedding_migration.switched')"
)
OLD_TARGET_KINDS = (
    "target_kind in ('source', 'document', 'conversation', 'job', 'evaluation_run')"
)
NEW_TARGET_KINDS = (
    "target_kind in ('source', 'document', 'conversation', 'job',"
    " 'evaluation_run', 'request', 'message', 'embedding_migration')"
)


def upgrade() -> None:
    """Record each event's decision, outcome, request, and run.

    Events recorded before this revision were allowed and succeeded; the
    defaults fill them in once and are then dropped, so every new event
    must say both itself.
    """
    op.add_column(
        "audit_events",
        sa.Column(
            "decision", sa.String(length=16), nullable=False, server_default="allowed"
        ),
    )
    op.add_column(
        "audit_events",
        sa.Column(
            "outcome", sa.String(length=24), nullable=False, server_default="succeeded"
        ),
    )
    op.alter_column("audit_events", "decision", server_default=None)
    op.alter_column("audit_events", "outcome", server_default=None)
    op.add_column("audit_events", sa.Column("request_id", sa.Uuid(), nullable=True))
    op.add_column("audit_events", sa.Column("run_id", sa.Uuid(), nullable=True))
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.create_check_constraint("ck_audit_events_action", "audit_events", NEW_ACTIONS)
    op.drop_constraint("ck_audit_events_target_kind", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_target_kind", "audit_events", NEW_TARGET_KINDS
    )
    op.create_check_constraint(
        "ck_audit_events_decision", "audit_events", "decision in ('allowed', 'denied')"
    )
    op.create_check_constraint(
        "ck_audit_events_outcome",
        "audit_events",
        "outcome in ('succeeded', 'failed', 'answered', 'abstained', 'rejected',"
        " 'denied', 'dependency_error', 'throttled', 'cancelled',"
        " 'internal_error')",
    )
    op.create_index(
        "ix_audit_events_request_occurred_at",
        "audit_events",
        ["request_id", "occurred_at", "event_id"],
        postgresql_where=sa.text("request_id is not null"),
    )


def downgrade() -> None:
    """Forget decisions, outcomes, requests, and runs, and the new actions.

    Events the old schema cannot hold truthfully are deleted first: a new
    action or target kind, or a decision or outcome other than the
    `allowed` and `succeeded` an upgrade would give them back.
    """
    op.drop_index("ix_audit_events_request_occurred_at", table_name="audit_events")
    op.drop_constraint("ck_audit_events_outcome", "audit_events", type_="check")
    op.drop_constraint("ck_audit_events_decision", "audit_events", type_="check")
    op.drop_constraint("ck_audit_events_target_kind", "audit_events", type_="check")
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.execute(
        "DELETE FROM audit_events WHERE action IN ('question.asked',"
        " 'document.read', 'document.access_changed', 'synchronization.finished',"
        " 'message.rated', 'embedding_migration.started',"
        " 'embedding_migration.switched')"
        " OR target_kind IN ('request', 'message', 'embedding_migration')"
        " OR decision <> 'allowed' OR outcome <> 'succeeded'"
    )
    op.create_check_constraint("ck_audit_events_action", "audit_events", OLD_ACTIONS)
    op.create_check_constraint(
        "ck_audit_events_target_kind", "audit_events", OLD_TARGET_KINDS
    )
    op.drop_column("audit_events", "run_id")
    op.drop_column("audit_events", "request_id")
    op.drop_column("audit_events", "outcome")
    op.drop_column("audit_events", "decision")
```

У `tests/test_audit.py`:

**1.** Знайдіть:

```python
"""Tests for content-free audit events."""

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest

from knowledge_service.audit import (
    AuditAction,
    AuditEvent,
    AuditTargetKind,
    AuditValue,
    InvalidAuditEvent,
```

Замініть на:

```python
"""Tests for content-free audit events."""

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
    AuditValue,
    InvalidAuditEvent,
```

**2.** Знайдіть:

```python
        action=AuditAction.SOURCE_REGISTERED,
        target_kind=AuditTargetKind.SOURCE,
        target_id=uuid4(),
        metadata=cast("dict[str, AuditValue]", metadata),
    )

```

Замініть на:

```python
        action=AuditAction.SOURCE_REGISTERED,
        target_kind=AuditTargetKind.SOURCE,
        target_id=uuid4(),
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=None,
        run_id=None,
        metadata=cast("dict[str, AuditValue]", metadata),
    )

```

**3.** Знайдіть:

```python
        action=AuditAction.JOB_FAILED,
        target_kind=AuditTargetKind.JOB,
        target_id=uuid4(),
        metadata={},
    )

```

Замініть на:

```python
        action=AuditAction.JOB_FAILED,
        target_kind=AuditTargetKind.JOB,
        target_id=uuid4(),
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=None,
        run_id=None,
        metadata={},
    )

```

**4.** Знайдіть:

```python
            action=AuditAction.CONVERSATION_DELETED,
            target_kind=AuditTargetKind.CONVERSATION,
            target_id=uuid4(),
            metadata={},
        )

```

Замініть на:

```python
            action=AuditAction.CONVERSATION_DELETED,
            target_kind=AuditTargetKind.CONVERSATION,
            target_id=uuid4(),
            decision=AuditDecision.ALLOWED,
            outcome=AuditOutcome.SUCCEEDED,
            request_id=None,
            run_id=None,
            metadata={},
        )

```

**5.** Знайдіть:

```python
    with pytest.raises(TypeError):
        cast("dict[str, AuditValue]", event.metadata)["kind"] = "other"

```

Замініть на:

```python
    with pytest.raises(TypeError):
        cast("dict[str, AuditValue]", event.metadata)["kind"] = "other"


@pytest.mark.parametrize(
    "value",
    [
        "How do I deploy?",
        "annual leave",
        "line\nbreak",
        "ключ",
        "a" * 65,
    ],
)
def test_a_sentence_is_not_a_label(value: str) -> None:
    with pytest.raises(InvalidAuditEvent):
        make_event(note=value)


@pytest.mark.parametrize("key", ["Title", "the question", "x" * 33, "1st", ""])
def test_a_metadata_key_must_be_a_short_snake_case_name(key: str) -> None:
    with pytest.raises(InvalidAuditEvent):
        make_event(**{key: 1})


def test_a_plain_string_is_not_a_decision() -> None:
    with pytest.raises(InvalidAuditEvent, match="decision"):
        AuditEvent(
            event_id=AuditEventId.new(),
            occurred_at=OCCURRED_AT,
            actor_id=USER_ID,
            action=AuditAction.DOCUMENT_READ,
            target_kind=AuditTargetKind.DOCUMENT,
            target_id=uuid4(),
            decision=cast(AuditDecision, "allowed"),
            outcome=AuditOutcome.SUCCEEDED,
            request_id=None,
            run_id=None,
            metadata={},
        )


def test_a_request_id_must_be_a_uuid() -> None:
    with pytest.raises(InvalidAuditEvent, match="request_id"):
        AuditEvent(
            event_id=AuditEventId.new(),
            occurred_at=OCCURRED_AT,
            actor_id=None,
            action=AuditAction.QUESTION_ASKED,
            target_kind=AuditTargetKind.REQUEST,
            target_id=uuid4(),
            decision=AuditDecision.ALLOWED,
            outcome=AuditOutcome.ANSWERED,
            request_id=cast(UUID, "00000000-0000-0000-0000-000000000001"),
            run_id=None,
            metadata={},
        )


@pytest.mark.parametrize(
    "value",
    [2**53, -(2**53), int.from_bytes(b"What is my salary?", "big"), 10**5000],
    # Named here: pytest cannot print the last one as a test ID.
    ids=["limit", "negative-limit", "spelled-text", "huge"],
)
def test_an_integer_too_large_to_be_a_count_is_rejected(value: int) -> None:
    with pytest.raises(InvalidAuditEvent, match="2\\*\\*53"):
        make_event(count=value)


class LyingMapping(Mapping[str, AuditValue]):
    """Says it holds one entry, but yields many."""

    def __len__(self) -> int:
        return 1

    def __iter__(self) -> Iterator[str]:
        return iter(f"key{index}" for index in range(200))

    def __getitem__(self, key: str) -> AuditValue:
        return 1


def test_a_mapping_cannot_hide_entries_behind_its_length() -> None:
    with pytest.raises(InvalidAuditEvent, match="at most"):
        make_event_with(LyingMapping())


def make_event_with(metadata: Mapping[str, AuditValue]) -> AuditEvent:
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=OCCURRED_AT,
        actor_id=None,
        action=AuditAction.JOB_QUEUED,
        target_kind=AuditTargetKind.JOB,
        target_id=uuid4(),
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=None,
        run_id=None,
        metadata=metadata,
    )

```

У `tests/test_records_integration.py`:

**1.** Знайдіть:

```python
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from knowledge_service.audit import AuditAction, AuditEvent, AuditTargetKind
from knowledge_service.evaluation import EvaluationRun, EvaluationStatus
from knowledge_service.identifiers import (
    AuditEventId,
```

Замініть на:

```python
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.evaluation import EvaluationRun, EvaluationStatus
from knowledge_service.identifiers import (
    AuditEventId,
```

**2.** Знайдіть:

```python

INSERT_RAW_AUDIT = text(
    "INSERT INTO audit_events (event_id, occurred_at, actor_id, action, target_kind,"
    " target_id, metadata) VALUES (:event_id, now(), NULL, 'job.queued', 'job',"
    " :target_id, CAST(:metadata AS jsonb))"
)

INSERT_RAW_RUN = text(
```

Замініть на:

```python

INSERT_RAW_AUDIT = text(
    "INSERT INTO audit_events (event_id, occurred_at, actor_id, action, target_kind,"
    " target_id, metadata, decision, outcome) VALUES (:event_id, now(), NULL,"
    " 'job.queued', 'job', :target_id, CAST(:metadata AS jsonb), 'allowed',"
    " 'succeeded')"
)

INSERT_RAW_RUN = text(
```

**3.** Знайдіть:

```python
                    action=AuditAction.JOB_QUEUED,
                    target_kind=AuditTargetKind.JOB,
                    target_id=target_id,
                    metadata={"kind": "synchronize_source"},
                ),
            )
```

Замініть на:

```python
                    action=AuditAction.JOB_QUEUED,
                    target_kind=AuditTargetKind.JOB,
                    target_id=target_id,
                    decision=AuditDecision.ALLOWED,
                    outcome=AuditOutcome.SUCCEEDED,
                    request_id=None,
                    run_id=None,
                    metadata={"kind": "synchronize_source"},
                ),
            )
```

**4.** Знайдіть:

```python
                    action=AuditAction.JOB_FAILED,
                    target_kind=AuditTargetKind.JOB,
                    target_id=target_id,
                    metadata={"attempt": 1},
                ),
            )
```

Замініть на:

```python
                    action=AuditAction.JOB_FAILED,
                    target_kind=AuditTargetKind.JOB,
                    target_id=target_id,
                    decision=AuditDecision.ALLOWED,
                    outcome=AuditOutcome.SUCCEEDED,
                    request_id=None,
                    run_id=None,
                    metadata={"attempt": 1},
                ),
            )
```

У `tests/test_indexes.py`:

**1.** Знайдіть:

```python
    "ix_embedding_migrations_target_configuration_id",
    "ix_embedding_migrations_target_space_id",
    "ix_audit_events_target_occurred_at",
    "ix_conversations_retention_deadline",
    "ix_synchronization_runs_source_started_at",
    "uq_embedding_spaces_current",
```

Замініть на:

```python
    "ix_embedding_migrations_target_configuration_id",
    "ix_embedding_migrations_target_space_id",
    "ix_audit_events_target_occurred_at",
    "ix_audit_events_request_occurred_at",
    "ix_conversations_retention_deadline",
    "ix_synchronization_runs_source_started_at",
    "uq_embedding_spaces_current",
```

У `tests/test_indexes_integration.py`:

**1.** Знайдіть:

```python

from database_fixtures import SYNC_DRIVER
from knowledge_service.persistence.access import subject_grants_statement
from knowledge_service.persistence.audit import audit_trail_statement
from knowledge_service.persistence.conversations import due_conversations_statement
from knowledge_service.persistence.sources import source_runs_statement

```

Замініть на:

```python

from database_fixtures import SYNC_DRIVER
from knowledge_service.persistence.access import subject_grants_statement
from knowledge_service.persistence.audit import (
    audit_trail_statement,
    request_trail_statement,
)
from knowledge_service.persistence.conversations import due_conversations_statement
from knowledge_service.persistence.sources import source_runs_statement

```

**2.** Знайдіть:

```python
    ),
    (
        "INSERT INTO audit_events (event_id, occurred_at, actor_id, action,"
        " target_kind, target_id, metadata) SELECT gen_random_uuid(),"
        " now() - (i || ' seconds')::interval, NULL, 'job.queued', 'job',"
        " (:prefix || lpad((mod(i, :sources) + 1)::text, 12, '0'))::uuid, '{}'::jsonb"
        " FROM generate_series(1, :rows) AS i",
        {"prefix": TARGET_PREFIX, "sources": SOURCE_COUNT, "rows": TABLE_ROWS},
    ),
```

Замініть на:

```python
    ),
    (
        "INSERT INTO audit_events (event_id, occurred_at, actor_id, action,"
        " target_kind, target_id, metadata, decision, outcome, request_id) SELECT"
        " gen_random_uuid(), now() - (i || ' seconds')::interval, NULL,"
        " 'job.queued', 'job',"
        " (:prefix || lpad((mod(i, :sources) + 1)::text, 12, '0'))::uuid,"
        " '{}'::jsonb, 'allowed', 'succeeded',"
        " (:prefix || lpad((mod(i, :sources) + 1)::text, 12, '0'))::uuid"
        " FROM generate_series(1, :rows) AS i",
        {"prefix": TARGET_PREFIX, "sources": SOURCE_COUNT, "rows": TABLE_ROWS},
    ),
```

**3.** Знайдіть:

```python
            datetime.now(UTC), 10
        ),
        "ix_audit_events_target_occurred_at": audit_trail_statement(TARGET_ID, 10),
        "ix_synchronization_runs_source_started_at": source_runs_statement(
            SOURCE_ID, 5
        ),
```

Замініть на:

```python
            datetime.now(UTC), 10
        ),
        "ix_audit_events_target_occurred_at": audit_trail_statement(TARGET_ID, 10),
        "ix_audit_events_request_occurred_at": request_trail_statement(TARGET_ID, 10),
        "ix_synchronization_runs_source_started_at": source_runs_statement(
            SOURCE_ID, 5
        ),
```

**Перевірка:** `uv run pytest tests/test_audit.py tests/test_indexes.py -q` дає `29 passed`, а `uv run pytest -m integration tests/test_migrations_integration.py tests/test_records_integration.py tests/test_indexes_integration.py -q` проходить.

### Крок 4 — Крок із кодом: зміни доступу і синхронізація

**Простими словами:** щоразу, коли сервіс записує, хто може читати Документ, він тепер у тій самій транзакції пише і запис про це: нову версію доступу й кількість читачів, але ніколи не те, хто вони. Прохід синхронізації отримує свій ID прогону, передає його кожній зміні доступу і наприкінці пише один запис про те, чим прохід закінчився.

| Зміна | Що вона робить |
|---|---|
| `ingestion/audit_events.py` | `access_event`, подія, яку фіксує кожен запис дозволів на доступ |
| `reconciliation.py` | `_write_grants` фіксує `document.access_changed`; `reconcile_document` і `narrow_access` приймають `run_id` |
| `publishing.py` | `publish_document` теж фіксує свій запис дозволів |
| `synchronization.py` | `synchronize_source` бере або створює ID прогону, фіксує `synchronization.finished` і далі піднімає власну помилку проходу; старе тіло стає `_publish` |
| `tests/test_deletions_integration.py` | Очікує нову подію доступу в журналі Документа |

Створіть `src/knowledge_service/ingestion/audit_events.py`:

```python
"""The audit event ingestion records whenever it writes Access Grants.

Both reconciliation and publishing write a Document's Access Grants, so
both import `access_event` from here; neither imports the other for it.
"""

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.documents import Document
from knowledge_service.identifiers import AuditEventId, SynchronizationRunId


def access_event(
    document: Document, *, readers: int, run_id: SynchronizationRunId | None
) -> AuditEvent:
    """Record that a Document's readers were written, without naming them."""
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=document.updated_at,
        actor_id=None,
        action=AuditAction.DOCUMENT_ACCESS_CHANGED,
        target_kind=AuditTargetKind.DOCUMENT,
        target_id=document.document_id.value,
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=None,
        run_id=None if run_id is None else run_id.value,
        metadata={
            "authorization_version": document.authorization_version.number,
            "readers": readers,
        },
    )
```

У `src/knowledge_service/ingestion/reconciliation.py`:

**1.** Знайдіть:

```python
When a record cannot be normalized, its last good version stays, but
`narrow_access` keeps it only for readers the Source still names: who may
read it can shrink then, never grow.
"""

from collections.abc import Iterable, Sequence
```

Замініть на:

```python
When a record cannot be normalized, its last good version stays, but
`narrow_access` keeps it only for readers the Source still names: who may
read it can shrink then, never grow.

Every write of a Document's Access Grants, when it is created, when its
readers change, and when it is narrowed, records a
`document.access_changed` audit event in the same transaction, with the new
Authorization Version, how many readers it has, and the Synchronization
Run it belongs to; never who the readers are.
"""

from collections.abc import Iterable, Sequence
```

**2.** Знайдіть:

```python
    DocumentMetadata,
    InvalidDocument,
)
from knowledge_service.identifiers import AccessGrantId, ChunkId, DocumentId, SourceId
from knowledge_service.ingestion.chunking import chunk_by_structure
from knowledge_service.ingestion.deletions import availability_event
from knowledge_service.ingestion.fingerprints import (
```

Замініть на:

```python
    DocumentMetadata,
    InvalidDocument,
)
from knowledge_service.identifiers import (
    AccessGrantId,
    ChunkId,
    DocumentId,
    SourceId,
    SynchronizationRunId,
)
from knowledge_service.ingestion.audit_events import access_event
from knowledge_service.ingestion.chunking import chunk_by_structure
from knowledge_service.ingestion.deletions import availability_event
from knowledge_service.ingestion.fingerprints import (
```

**3.** Знайдіть:

```python
    *,
    readers: Sequence[Subject],
    observed_at: datetime,
) -> Reconciliation:
    """Publish one observation of a document, writing only what changed.

    `readers` are the normalized subjects who may read it; an empty sequence
    means nobody may. An observation older than the stored version raises
    `InvalidDocument`, and nothing is written.
    """
    provenance = normalized.provenance
    content = content_fingerprint(normalized.parsed)
```

Замініть на:

```python
    *,
    readers: Sequence[Subject],
    observed_at: datetime,
    run_id: SynchronizationRunId | None = None,
) -> Reconciliation:
    """Publish one observation of a document, writing only what changed.

    `readers` are the normalized subjects who may read it; an empty sequence
    means nobody may. `run_id` names the Synchronization Run in the audit
    event of a grant change. An observation older than the stored version
    raises `InvalidDocument`, and nothing is written.
    """
    provenance = normalized.provenance
    content = content_fingerprint(normalized.parsed)
```

**4.** Знайдіть:

```python
            )
            if await insert_document(connection, created):
                await _write_chunks(connection, normalized, created)
                await _write_grants(connection, created, readers, observed_at)
                await _write_metadata(connection, normalized, created, observed_at)
                return Reconciliation(document=created, change=Change.CREATED)
            # Another run created it after the lookup; decide against its row.
```

Замініть на:

```python
            )
            if await insert_document(connection, created):
                await _write_chunks(connection, normalized, created)
                await _write_grants(
                    connection, created, readers, observed_at, run_id=run_id
                )
                await _write_metadata(connection, normalized, created, observed_at)
                return Reconciliation(document=created, change=Change.CREATED)
            # Another run created it after the lookup; decide against its row.
```

**5.** Знайдіть:

```python
        if content_changed or restored:
            await _write_chunks(connection, normalized, document)
        if access_changed:
            await _write_grants(connection, document, readers, observed_at)
    return Reconciliation(
        document=document,
        change=_change(
```

Замініть на:

```python
        if content_changed or restored:
            await _write_chunks(connection, normalized, document)
        if access_changed:
            await _write_grants(
                connection, document, readers, observed_at, run_id=run_id
            )
    return Reconciliation(
        document=document,
        change=_change(
```

**6.** Знайдіть:

```python
    external_id: str,
    readers: Sequence[Subject],
    observed_at: datetime,
) -> bool:
    """Keep a stored Document only for readers both old and new access name.

```

Замініть на:

```python
    external_id: str,
    readers: Sequence[Subject],
    observed_at: datetime,
    run_id: SynchronizationRunId | None = None,
) -> bool:
    """Keep a stored Document only for readers both old and new access name.

```

**7.** Знайдіть:

```python
        # The fingerprint records the narrowed readers, so the next good
        # observation compares against what is really stored.
        await upsert_document(connection, document)
        await _write_grants(connection, document, kept, observed_at)
        return True


```

Замініть на:

```python
        # The fingerprint records the narrowed readers, so the next good
        # observation compares against what is really stored.
        await upsert_document(connection, document)
        await _write_grants(connection, document, kept, observed_at, run_id=run_id)
        return True


```

**8.** Знайдіть:

```python
    document: Document,
    readers: Sequence[Subject],
    observed_at: datetime,
) -> None:
    await replace_access_grants(
        connection,
        document_id=document.document_id,
        grant_run=_grants(document.document_id, readers, observed_at),
    )


```

Замініть на:

```python
    document: Document,
    readers: Sequence[Subject],
    observed_at: datetime,
    *,
    run_id: SynchronizationRunId | None,
) -> None:
    grant_run = _grants(document.document_id, readers, observed_at)
    await replace_access_grants(
        connection, document_id=document.document_id, grant_run=grant_run
    )
    await record_audit_event(
        connection, access_event(document, readers=len(grant_run), run_id=run_id)
    )


```

У `src/knowledge_service/ingestion/publishing.py`:

**1.** Знайдіть:

```python

A Document is published when its row, its Chunks, and its Access Grants all
describe the same content version. This module builds that unit of work; the
caller owns the transaction it runs in.
"""

from collections.abc import Sequence
```

Замініть на:

```python

A Document is published when its row, its Chunks, and its Access Grants all
describe the same content version. This module builds that unit of work; the
caller owns the transaction it runs in. Writing the Access Grants records a
`document.access_changed` audit event in that same transaction.
"""

from collections.abc import Sequence
```

**2.** Знайдіть:

```python
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document
from knowledge_service.identifiers import DocumentId
from knowledge_service.persistence.access import replace_access_grants
from knowledge_service.persistence.chunks import replace_chunks
from knowledge_service.persistence.documents import upsert_document

```

Замініть на:

```python
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document
from knowledge_service.identifiers import DocumentId
from knowledge_service.ingestion.audit_events import access_event
from knowledge_service.persistence.access import replace_access_grants
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.chunks import replace_chunks
from knowledge_service.persistence.documents import upsert_document

```

**3.** Знайдіть:

```python
    await replace_access_grants(
        connection, document_id=stored_id, grant_run=publication.grants
    )
    return stored_id


```

Замініть на:

```python
    await replace_access_grants(
        connection, document_id=stored_id, grant_run=publication.grants
    )
    await record_audit_event(
        connection,
        access_event(stored_document, readers=len(publication.grants), run_id=None),
    )
    return stored_id


```

У `src/knowledge_service/ingestion/synchronization.py`:

**1.** Знайдіть:

```python
Two kinds of removal still wait: a revocation on a later page waits for the
earlier pages' work, and a Document the Source deletes, or no longer lists,
becomes unavailable only when the pass tombstones it at the end.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.ingestion.deletions import (
    Tombstone,
    requeue_unfinished_purges,
```

Замініть на:

```python
Two kinds of removal still wait: a revocation on a later page waits for the
earlier pages' work, and a Document the Source deletes, or no longer lists,
becomes unavailable only when the pass tombstones it at the end.

Every pass has a Synchronization Run ID. Each grant change it writes names
it, and when the pass ends it records one `synchronization.finished` audit
event against the Source: `succeeded` with counts, or `failed` with the
error's type name, in a transaction of its own. A pass that is cancelled
records nothing; a failed pass whose record cannot be written logs that and
raises its own error.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
    AuditValue,
)
from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import (
    AuditEventId,
    JobId,
    SourceId,
    SynchronizationRunId,
)
from knowledge_service.ingestion.deletions import (
    Tombstone,
    requeue_unfinished_purges,
```

**2.** Знайдіть:

```python
    SourceRecordState,
)
from knowledge_service.persistence.access import load_readers

logger = logging.getLogger(__name__)

```

Замініть на:

```python
    SourceRecordState,
)
from knowledge_service.persistence.access import load_readers
from knowledge_service.persistence.audit import record_audit_event

logger = logging.getLogger(__name__)

```

**3.** Знайдіть:

```python
class SynchronizationSummary:
    """What one complete pass did."""

    changes: Counter[Change]
    quarantined: tuple[Quarantined, ...]
    tombstones: tuple[Tombstone, ...]
```

Замініть на:

```python
class SynchronizationSummary:
    """What one complete pass did."""

    run_id: SynchronizationRunId
    changes: Counter[Change]
    quarantined: tuple[Quarantined, ...]
    tombstones: tuple[Tombstone, ...]
```

**4.** Знайдіть:

```python
    observed_at: datetime,
    principal_directory: PrincipalDirectory | None,
    page_size: int = PAGE_SIZE,
) -> SynchronizationSummary:
    """Publish every record the Source offers, then tombstone the rest.

    Readers come from `principal_directory`, which every caller names: with
    None, only public items are readable. A Source that returns a checkpoint
    it returned before raises `InvalidSource` instead of paging forever.
    """
    seen: set[str] = set()
    used: set[str] = set()
    changes: Counter[Change] = Counter()
```

Замініть на:

```python
    observed_at: datetime,
    principal_directory: PrincipalDirectory | None,
    page_size: int = PAGE_SIZE,
    run_id: SynchronizationRunId | None = None,
) -> SynchronizationSummary:
    """Publish every record the Source offers, then tombstone the rest.

    Readers come from `principal_directory`, which every caller names: with
    None, only public items are readable. A Source that returns a checkpoint
    it returned before raises `InvalidSource` instead of paging forever.
    The pass runs as `run_id`, or as a new run, and records how it ended.
    """
    run = SynchronizationRunId.new() if run_id is None else run_id
    try:
        summary = await _publish(
            runtime,
            source,
            source_id=source_id,
            observed_at=observed_at,
            principal_directory=principal_directory,
            page_size=page_size,
            run_id=run,
        )
    except Exception as error:
        # The pass's own error is raised either way; its record holds only
        # its type. A database that cannot take the record is logged.
        try:
            await _record_finished(
                runtime,
                source_id=source_id,
                run_id=run,
                observed_at=observed_at,
                outcome=AuditOutcome.FAILED,
                metadata={"failure": type(error).__name__},
            )
        except Exception as unrecorded:
            # Identifiers and type names only, as the other warnings here.
            logger.warning(
                "failed synchronization run %s of source %s not audited: %s",
                str(run.value),
                str(source_id.value),
                type(unrecorded).__name__,
            )
        raise
    await _record_finished(
        runtime,
        source_id=source_id,
        run_id=run,
        observed_at=observed_at,
        outcome=AuditOutcome.SUCCEEDED,
        metadata={
            "changed": sum(
                count
                for change, count in summary.changes.items()
                if change is not Change.UNCHANGED
            ),
            "unchanged": summary.changes[Change.UNCHANGED],
            "quarantined": len(summary.quarantined),
            "tombstoned": len(summary.tombstones),
            "unresolved": len(summary.unresolved),
        },
    )
    return summary


async def _record_finished(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    run_id: SynchronizationRunId,
    observed_at: datetime,
    outcome: AuditOutcome,
    metadata: dict[str, AuditValue],
) -> None:
    async with runtime.transaction() as connection:
        await record_audit_event(
            connection,
            AuditEvent(
                event_id=AuditEventId.new(),
                # When the pass ended, not the time it observed the Source.
                occurred_at=max(datetime.now(UTC), observed_at),
                actor_id=None,
                action=AuditAction.SYNCHRONIZATION_FINISHED,
                target_kind=AuditTargetKind.SOURCE,
                target_id=source_id.value,
                decision=AuditDecision.ALLOWED,
                outcome=outcome,
                request_id=None,
                run_id=run_id.value,
                metadata=metadata,
            ),
        )


async def _publish(
    runtime: DatabaseRuntime,
    source: SourcePort,
    *,
    source_id: SourceId,
    observed_at: datetime,
    principal_directory: PrincipalDirectory | None,
    page_size: int,
    run_id: SynchronizationRunId,
) -> SynchronizationSummary:
    """One pass: every page, then the tombstones and requeued purges."""
    seen: set[str] = set()
    used: set[str] = set()
    changes: Counter[Change] = Counter()
```

**5.** Знайдіть:

```python
            list(planned.values()),
            source_id=source_id,
            observed_at=observed_at,
        )
        for record, access in planned.values():
            outcome = normalize(record, source_id=source_id)
```

Замініть на:

```python
            list(planned.values()),
            source_id=source_id,
            observed_at=observed_at,
            run_id=run_id,
        )
        for record, access in planned.values():
            outcome = normalize(record, source_id=source_id)
```

**6.** Знайдіть:

```python
                outcome,
                readers=access.readers,
                observed_at=observed_at,
            )
            changes[
                _counted(result.change, narrowed=record.external_id in narrowed)
```

Замініть на:

```python
                outcome,
                readers=access.readers,
                observed_at=observed_at,
                run_id=run_id,
            )
            changes[
                _counted(result.change, narrowed=record.external_id in narrowed)
```

**7.** Знайдіть:

```python
            len(unresolved),
        )
    return SynchronizationSummary(
        changes=changes,
        quarantined=tuple(quarantined),
        tombstones=tombstones,
```

Замініть на:

```python
            len(unresolved),
        )
    return SynchronizationSummary(
        run_id=run_id,
        changes=changes,
        quarantined=tuple(quarantined),
        tombstones=tombstones,
```

**8.** Знайдіть:

```python
    *,
    source_id: SourceId,
    observed_at: datetime,
) -> set[str]:
    """Narrow every record whose new access leaves a reader out.

```

Замініть на:

```python
    *,
    source_id: SourceId,
    observed_at: datetime,
    run_id: SynchronizationRunId,
) -> set[str]:
    """Narrow every record whose new access leaves a reader out.

```

**9.** Знайдіть:

```python
            external_id=record.external_id,
            readers=access.readers,
            observed_at=observed_at,
        ):
            narrowed.add(record.external_id)
    return narrowed
```

Замініть на:

```python
            external_id=record.external_id,
            readers=access.readers,
            observed_at=observed_at,
            run_id=run_id,
        ):
            narrowed.add(record.external_id)
    return narrowed
```

`except` навколо проходу фіксує збій, а потім піднімає власну помилку проходу. Якщо не вдався навіть запис, у лог іде попередження з прогоном, Джерелом і назвою типу помилки, а початкова помилка все одно перемагає, бо викликові треба знати, чому прохід не вдався.

У `tests/test_deletions_integration.py`:

**1.** Знайдіть:

```python
            connection, target_id=retired.document_id.value, limit=10
        )
        job = await load_job(connection, job_id=tombstones[0].purge_job_id)
    assert [(e.action, dict(e.metadata)) for e in events] == [
        (AuditAction.DOCUMENT_AVAILABILITY_CHANGED, {"availability": "tombstoned"})
    ]
    assert job is not None
    assert (job.kind, job.status, job.target_id) == (
```

Замініть на:

```python
            connection, target_id=retired.document_id.value, limit=10
        )
        job = await load_job(connection, job_id=tombstones[0].purge_job_id)
    # Publishing wrote its readers; the deletion pass then tombstoned it.
    assert [(e.action, dict(e.metadata)) for e in events] == [
        (
            AuditAction.DOCUMENT_ACCESS_CHANGED,
            {"authorization_version": 1, "readers": 1},
        ),
        (AuditAction.DOCUMENT_AVAILABILITY_CHANGED, {"availability": "tombstoned"}),
    ]
    assert job is not None
    assert (job.kind, job.status, job.target_id) == (
```

**2.** Знайдіть:

```python
    async with runtime.transaction() as connection:
        jobs = (await connection.execute(text("SELECT count(*) FROM jobs"))).scalar()
        events = (
            await connection.execute(text("SELECT count(*) FROM audit_events"))
        ).scalar()
    assert (jobs, events) == (0, 0)

```

Замініть на:

```python
    async with runtime.transaction() as connection:
        jobs = (await connection.execute(text("SELECT count(*) FROM jobs"))).scalar()
        events = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM audit_events"
                    " WHERE action = 'document.availability_changed'"
                )
            )
        ).scalar()
    assert (jobs, events) == (0, 0)

```

**3.** Знайдіть:

```python
        events = await load_audit_events(
            connection, target_id=retired.document_id.value, limit=10
        )
    assert [e.metadata["availability"] for e in events] == ["tombstoned", "available"]
    assert job.status is JobStatus.SUCCEEDED
    assert any("retired" in chunk for chunk in await searchable_text(runtime))

```

Замініть на:

```python
        events = await load_audit_events(
            connection, target_id=retired.document_id.value, limit=10
        )
    assert [
        e.metadata["availability"]
        for e in events
        if e.action is AuditAction.DOCUMENT_AVAILABILITY_CHANGED
    ] == ["tombstoned", "available"]
    assert job.status is JobStatus.SUCCEEDED
    assert any("retired" in chunk for chunk in await searchable_text(runtime))

```

**Перевірка:** `uv run pytest -m integration tests/test_deletions_integration.py tests/test_reconciliation_integration.py tests/test_synchronization_integration.py tests/test_publishing_integration.py -q` проходить.

### Крок 5 — Крок із кодом: відгуки й адміністрування

**Простими словами:** оцінка Повідомлення тепер у тій самій транзакції записує ще й того, хто оцінив, і чи це «вгору», чи «вниз». Запуск міграції ембедингів і перемикання пошуку на новий простір — адміністративні дії сервісу; кожна тепер фіксує адміністратора, що її попросив, у тій самій транзакції, що й зміна. Відхилений запуск чи перемикання нічого не змінює і нічого не фіксує.

| Зміна | Що вона робить |
|---|---|
| `persistence/conversations.py` | `rate_message` перевіряє свій аргумент і фіксує `message.rated` |
| `embeddings/migration_workflow.py` | `start_embedding_migration` і `switch_to_target_space` приймають `actor_id` і фіксують свій крок |

У `src/knowledge_service/persistence/conversations.py`:

**1.** Знайдіть:

```python
- **Only through the store.** The rules a Message checks, such as "an
  Answer cites Chunks", live in Python, not in the database; a row written
  past these functions can make its Conversation unreadable.

The other functions read and write without an owner, for system work such
as retention and purge Jobs; they are never given a User's request.
```

Замініть на:

```python
- **Only through the store.** The rules a Message checks, such as "an
  Answer cites Chunks", live in Python, not in the database; a row written
  past these functions can make its Conversation unreadable.
- **Feedback is audited.** `rate_message` records who rated which Message,
  up or down, in the same transaction as the rating.

The other functions read and write without an owner, for system work such
as retention and purge Jobs; they are never given a User's request.
```

**2.** Знайдіть:

```python
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.conversations import (
    Conversation,
    ConversationFull,
```

Замініть на:

```python
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.conversations import (
    Conversation,
    ConversationFull,
```

**3.** Знайдіть:

```python
)
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import (
    ChunkId,
    ConversationId,
    DocumentId,
```

Замініть на:

```python
)
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import (
    AuditEventId,
    ChunkId,
    ConversationId,
    DocumentId,
```

**4.** Знайдіть:

```python
    UserId,
)
from knowledge_service.jobs import Job, JobKind
from knowledge_service.persistence.jobs import enqueue_job
from knowledge_service.persistence.schema import (
    conversations,
```

Замініть на:

```python
    UserId,
)
from knowledge_service.jobs import Job, JobKind
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.jobs import enqueue_job
from knowledge_service.persistence.schema import (
    conversations,
```

**5.** Знайдіть:

```python
async def rate_message(
    connection: AsyncConnection, *, feedback: MessageFeedback
) -> MessageFeedback:
    """Store one rating per Message per User, replacing any earlier rating."""
    statement = insert(message_feedback).values(_feedback_row(feedback))
    statement = statement.on_conflict_do_update(
        index_elements=["message_id", "user_id"],
```

Замініть на:

```python
async def rate_message(
    connection: AsyncConnection, *, feedback: MessageFeedback
) -> MessageFeedback:
    """Store one rating per Message per User, replacing any earlier rating.

    Each rating also records a `message.rated` audit event in the same
    transaction: who rated which Message, up or down, and when.
    """
    if type(feedback) is not MessageFeedback:
        raise TypeError("feedback must be a MessageFeedback")
    statement = insert(message_feedback).values(_feedback_row(feedback))
    statement = statement.on_conflict_do_update(
        index_elements=["message_id", "user_id"],
```

**6.** Знайдіть:

```python
        },
    )
    await connection.execute(statement)
    return feedback


```

Замініть на:

```python
        },
    )
    await connection.execute(statement)
    await record_audit_event(
        connection,
        AuditEvent(
            event_id=AuditEventId.new(),
            occurred_at=feedback.rated_at,
            actor_id=feedback.user_id,
            action=AuditAction.MESSAGE_RATED,
            target_kind=AuditTargetKind.MESSAGE,
            target_id=feedback.message_id.value,
            decision=AuditDecision.ALLOWED,
            outcome=AuditOutcome.SUCCEEDED,
            request_id=None,
            run_id=None,
            metadata={"rating": feedback.rating.value},
        ),
    )
    return feedback


```

У `src/knowledge_service/embeddings/migration_workflow.py`:

**1.** Знайдіть:

```python

Search never needs to know a migration is running: it reads the current
space only, and exactly one space is current at every moment.
"""

import math
```

Замініть на:

```python

Search never needs to know a migration is running: it reads the current
space only, and exactly one space is current at every moment.

Starting a migration and switching search to the new space are
administration: each records an audit event (`embedding_migration.started`,
`embedding_migration.switched`) in the same transaction as the change, with
the administrator who asked, or None for the service itself. A start or
switch that is refused changes nothing and records nothing.
"""

import math
```

**2.** Знайдіть:

```python

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.database import DatabaseRuntime
from knowledge_service.embeddings.index_configuration import (
    InvalidIndexConfiguration,
```

Замініть на:

```python

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.database import DatabaseRuntime
from knowledge_service.embeddings.index_configuration import (
    InvalidIndexConfiguration,
```

**3.** Знайдіть:

```python
    publish_document_embeddings,
)
from knowledge_service.identifiers import (
    DocumentId,
    EmbeddingMigrationId,
    EmbeddingSpaceId,
)
from knowledge_service.persistence.embedding_migrations import (
    count_chunks_missing_from_target,
    insert_embedding_migration,
```

Замініть на:

```python
    publish_document_embeddings,
)
from knowledge_service.identifiers import (
    AuditEventId,
    DocumentId,
    EmbeddingMigrationId,
    EmbeddingSpaceId,
    UserId,
)
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.embedding_migrations import (
    count_chunks_missing_from_target,
    insert_embedding_migration,
```

**4.** Знайдіть:

```python
    target: EmbeddingModelIdentity,
    *,
    now: datetime,
) -> EmbeddingMigration:
    """Create the target space, not current, and start dual-writing.

    The current space's row stays locked until the commit, so two starts run
    one after the other and the second sees the first.
    """
    try:
        configuration = current_index_configuration(target)
```

Замініть на:

```python
    target: EmbeddingModelIdentity,
    *,
    now: datetime,
    actor_id: UserId | None = None,
) -> EmbeddingMigration:
    """Create the target space, not current, and start dual-writing.

    The current space's row stays locked until the commit, so two starts run
    one after the other and the second sees the first. `actor_id` is the
    administrator recorded in the audit event.
    """
    try:
        configuration = current_index_configuration(target)
```

**5.** Знайдіть:

```python
            updated_at=now,
        )
        await insert_embedding_migration(connection, migration)
    return migration


```

Замініть на:

```python
            updated_at=now,
        )
        await insert_embedding_migration(connection, migration)
        await record_audit_event(
            connection,
            _administration_event(
                migration, AuditAction.EMBEDDING_MIGRATION_STARTED, actor_id
            ),
        )
    return migration


```

**6.** Знайдіть:

```python
    *,
    migration_id: EmbeddingMigrationId,
    now: datetime,
) -> EmbeddingMigration:
    """Make the target space current, if it has every vector the source has.

```

Замініть на:

```python
    *,
    migration_id: EmbeddingMigrationId,
    now: datetime,
    actor_id: UserId | None = None,
) -> EmbeddingMigration:
    """Make the target space current, if it has every vector the source has.

```

**7.** Знайдіть:

```python
    while the check and the switch run; search sees the old space until the
    commit and the new one after it. Raises `IncompleteBackfill`, and
    switches nothing, while any searchable Chunk lacks a target vector.
    """
    async with runtime.transaction() as connection:
        migration = await _load(connection, migration_id, for_update=True)
```

Замініть на:

```python
    while the check and the switch run; search sees the old space until the
    commit and the new one after it. Raises `IncompleteBackfill`, and
    switches nothing, while any searchable Chunk lacks a target vector.
    `actor_id` is the administrator recorded in the audit event.
    """
    async with runtime.transaction() as connection:
        migration = await _load(connection, migration_id, for_update=True)
```

**8.** Знайдіть:

```python
        await make_embedding_space_current(connection, retired=source, current=target)
        switched = replace(migration, state=after, updated_at=now)
        await save_embedding_migration(connection, switched)
    return switched


async def clean_up_source_space(
```

Замініть на:

```python
        await make_embedding_space_current(connection, retired=source, current=target)
        switched = replace(migration, state=after, updated_at=now)
        await save_embedding_migration(connection, switched)
        await record_audit_event(
            connection,
            _administration_event(
                switched, AuditAction.EMBEDDING_MIGRATION_SWITCHED, actor_id
            ),
        )
    return switched


def _administration_event(
    migration: EmbeddingMigration, action: AuditAction, actor_id: UserId | None
) -> AuditEvent:
    """Record one administrative step of a migration, by its state only."""
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=migration.updated_at,
        actor_id=actor_id,
        action=action,
        target_kind=AuditTargetKind.EMBEDDING_MIGRATION,
        target_id=migration.migration_id.value,
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=None,
        run_id=None,
        metadata={"state": migration.state.value},
    )


async def clean_up_source_space(
```

`actor_id` за замовчуванням None, тож наявні виклики працюють як раніше. Точка входу, що перевірятиме роль адміністратора, коли з'явиться, мусить передавати ID виклику.

**Перевірка:** `uv run pytest -m integration tests/test_conversations_integration.py tests/test_embedding_migration_integration.py -q` проходить.

### Крок 6 — Крок із кодом: читання Документів

**Простими словами:** перш ніж Цитати Відповіді дійдуть до читача, сервер перечитує кожен процитований Документ від імені цього читача. Тепер ця перевірка ще й пише один запис на кожен процитований Документ: дозволено, якщо всі його Цитати пройшли, заборонено, якщо одна не пройшла. Запис пишеться в тій самій транзакції, що й перевірка, і зберігається лише на сервері, тож клієнт і далі не може дізнатися, чому Відповідь відкликано.

| Зміна | Що вона робить |
|---|---|
| Докстрінг модуля | Додає обіцянку «Every read audited» |
| `_record_reads` | Одна подія `document.read` на кожен процитований Документ, у порядку Цитат |
| `confirm_citations` | Викликає її після перевірки, перед рішенням |

У `src/knowledge_service/answering/current_citations.py`:

**1.** Знайдіть:

```python
- **Same answer to every failure.** A deleted Chunk, a new version, a
  tombstoned Document, and a revoked grant all give `WITHDRAWN`, so the
  client cannot learn which one happened, or that a Document exists.
- **Quiet.** One log record per check holds the request ID and counts;
  never a title, a link, or text.

```

Замініть на:

```python
- **Same answer to every failure.** A deleted Chunk, a new version, a
  tombstoned Document, and a revoked grant all give `WITHDRAWN`, so the
  client cannot learn which one happened, or that a Document exists.
- **Every read audited.** In the same transaction, each cited Document
  gets one `document.read` audit event for this reader and request:
  `allowed` when all its Citations were confirmed, `denied` when one
  failed. The event names the Document by ID and counts its Citations; the
  client still learns nothing, since only the server keeps the trail.
- **Quiet.** One log record per check holds the request ID and counts;
  never a title, a link, or text.

```

**2.** Знайдіть:

```python

import logging
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

```

Замініть на:

```python

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

```

**3.** Знайдіть:

```python
    EvidenceSet,
)
from knowledge_service.answering.generation import AnswerOutcome, AnswerResult
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.ingestion.provenance import display_excerpt, display_text
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks, documents

logger = logging.getLogger(__name__)
```

Замініть на:

```python
    EvidenceSet,
)
from knowledge_service.answering.generation import AnswerOutcome, AnswerResult
from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import AuditEventId, ChunkId, DocumentId, SourceId
from knowledge_service.ingestion.provenance import display_excerpt, display_text
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.schema import chunks, documents

logger = logging.getLogger(__name__)
```

**4.** Знайдіть:

```python
        _current(citation, rows.get(citation.chunk_id.value)) for citation in cited
    ]
    withdrawn = sum(citation is None for citation in confirmed)
    if withdrawn:
        result = AnswerResult(
            outcome=AnswerOutcome.WITHDRAWN,
```

Замініть на:

```python
        _current(citation, rows.get(citation.chunk_id.value)) for citation in cited
    ]
    withdrawn = sum(citation is None for citation in confirmed)
    await _record_reads(connection, context, cited, confirmed, request_id=request_id)
    if withdrawn:
        result = AnswerResult(
            outcome=AnswerOutcome.WITHDRAWN,
```

**5.** Знайдіть:

```python
            eligibility,
        )
    )


def _checked(citations: object) -> tuple[Citation, ...]:
```

Замініть на:

```python
            eligibility,
        )
    )


async def _record_reads(
    connection: AsyncConnection,
    context: AuthorizationContext,
    cited: Sequence[Citation],
    confirmed: Sequence[Citation | None],
    *,
    request_id: UUID,
) -> None:
    """One `document.read` event per cited Document, in Citation order."""
    occurred_at = datetime.now(UTC)
    counts: dict[DocumentId, int] = {}
    denied: set[DocumentId] = set()
    for citation, current in zip(cited, confirmed, strict=True):
        counts[citation.document_id] = counts.get(citation.document_id, 0) + 1
        if current is None:
            denied.add(citation.document_id)
    for document_id, count in counts.items():
        refused = document_id in denied
        await record_audit_event(
            connection,
            AuditEvent(
                event_id=AuditEventId.new(),
                occurred_at=occurred_at,
                actor_id=context.user_id,
                action=AuditAction.DOCUMENT_READ,
                target_kind=AuditTargetKind.DOCUMENT,
                target_id=document_id.value,
                decision=AuditDecision.DENIED if refused else AuditDecision.ALLOWED,
                outcome=AuditOutcome.DENIED if refused else AuditOutcome.SUCCEEDED,
                request_id=request_id,
                run_id=None,
                metadata={"citations": count},
            ),
        )


def _checked(citations: object) -> tuple[Citation, ...]:
```

**Перевірка:** `uv run pytest tests/test_current_citations.py -q` проходить, і так само `uv run pytest -m integration tests/test_current_citations_integration.py tests/test_citation_confirmation_integration.py -q`.

### Крок 7 — Крок із кодом: запис Питання у стрімі

**Простими словами:** стрім відповіді отримує ще одного помічника: місце, куди писати свій запис, — журнал аудиту (audit trail). Коли стрім знає своє рішення, він пише один запис про Питання: хто питав, чим закінчилося, скільки Цитат буде надіслано. Лише після того, як запис збережено, він надсилає Цитати і фінальну подію. Якщо запис зберегти не вдалося, читач отримує помилку. Стрім, що зламався або від якого пішов клієнт, фіксується, коли закінчується.

| Зміна | Що вона робить |
|---|---|
| `AuditTrail` | Порт: `record(event)`, у власній транзакції |
| `AnswerStreamer(..., audit=...)` | Нова обов'язкова залежність |
| `_Span.record` | Будує одну подію `question.asked` і пише її окремою задачею, не довше за `AUDIT_SECONDS` |
| `_Span.close` | Фіксує стрім таким, яким він закінчився, якщо цього ще ніхто не зробив, потім закриває запис логу |
| Рішення | Фіксується до того, як надіслано решту Відповіді; невдалий запис перетворюється на помилку `dependency` |
| `telemetry.py` | Дозволяє нове поле `audited` у записі «answer stream ended» |
| `answering/audit_trail.py` | `DatabaseAuditTrail`, журнал у PostgreSQL |
| `tests/test_streaming.py` | Фейк `Trail`, що зберігає події, падає або зависає; хелпер `streamer` передає його |

У `src/knowledge_service/answering/streaming.py`:

**1.** Знайдіть:

```python
  replied, how its reply was judged (`answered`, `abstained`, `refused`,
  `invalid`) and the rule an invalid one broke, such as
  `invalid_citation`. Never text.
"""

import asyncio
```

Замініть на:

```python
  replied, how its reply was judged (`answered`, `abstained`, `refused`,
  `invalid`) and the rule an invalid one broke, such as
  `invalid_citation`. Never text.
- **One audit event per stream, before its end.** Each stream records one
  `question.asked` audit event through its `AuditTrail`: the reader, the
  request ID, the audit outcome, the phase, and how many Citations were
  sent, never text. A decision is recorded before its Citations, usage,
  and completion are sent; if the record cannot be written within
  `AUDIT_SECONDS`, none of them are, and the stream ends with an `error`
  event instead. A failure or a client that leaves is recorded as it ends;
  the write runs as a task of its own, so cancelling the stream does not
  cut it short. If that record fails, only the telemetry record says so
  (`audited: false`).
"""

import asyncio
```

**2.** Знайдіть:

```python
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Protocol, cast
from uuid import UUID

```

Замініть на:

```python
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, cast
from uuid import UUID

```

**3.** Знайдіть:

```python
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
)
from knowledge_service.language import Language
from knowledge_service.model import ChatModelMalformedResponse
from knowledge_service.telemetry import DIAGNOSTIC, diagnostics_enabled
```

Замініть на:

```python
)
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.stream_failures import (
    PartialText,
    StreamPhase,
    finished_outcome,
    stream_failure,
)
from knowledge_service.answering.support import SupportJudge, check_support
from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.contracts import Question, Usage
from knowledge_service.errors import (
    AnswerDeadlineExceeded,
    FailureKind,
    classify_failure,
)
from knowledge_service.identifiers import AuditEventId, UserId
from knowledge_service.language import Language
from knowledge_service.model import ChatModelMalformedResponse
from knowledge_service.telemetry import DIAGNOSTIC, diagnostics_enabled
```

**4.** Знайдіть:

```python
}
# How long closing the model's stream may take, even after the deadline.
CLOSE_SECONDS = 1.0
# The longest escape that may still be arriving: a surrogate pair.
_LONGEST_ESCAPE = 12

```

Замініть на:

```python
}
# How long closing the model's stream may take, even after the deadline.
CLOSE_SECONDS = 1.0
# How long writing the stream's audit event may take, even when cancelled.
AUDIT_SECONDS = 2.0
# The longest escape that may still be arriving: a surrogate pair.
_LONGEST_ESCAPE = 12

```

**5.** Знайдіть:

```python
    async def confirm(
        self, result: AnswerResult, *, reader: AuthorizationContext, request_id: UUID
    ) -> AnswerResult: ...


class AnswerFinisher(Protocol):
```

Замініть на:

```python
    async def confirm(
        self, result: AnswerResult, *, reader: AuthorizationContext, request_id: UUID
    ) -> AnswerResult: ...


class AuditTrail(Protocol):
    """Where the stream's audit event is written, in its own transaction."""

    async def record(self, event: AuditEvent) -> None: ...


class AnswerFinisher(Protocol):
```

**6.** Знайдіть:

```python
        budget: ContextBudget,
        *,
        citations: CitationCheck,
        timeout_seconds: float,
    ) -> None:
        if not positive_seconds(timeout_seconds):
```

Замініть на:

```python
        budget: ContextBudget,
        *,
        citations: CitationCheck,
        audit: AuditTrail,
        timeout_seconds: float,
    ) -> None:
        if not positive_seconds(timeout_seconds):
```

**7.** Знайдіть:

```python
        self._source = source
        self._model = model
        self._citations = citations
        self._finisher = finisher
        self._policy = policy
        self._budget = budget
```

Замініть на:

```python
        self._source = source
        self._model = model
        self._citations = citations
        self._audit = audit
        self._finisher = finisher
        self._policy = policy
        self._budget = budget
```

**8.** Знайдіть:

```python
        errors of `assess_evidence` and `allocate_context`. Its record ends
        here as `failed` or `cancelled` when it does.
        """
        span = _Span(request_id, question=question.text)
        try:
            if type(reader) is not AuthorizationContext:
                raise InvalidAnswerRequest("reader must be an AuthorizationContext")
```

Замініть на:

```python
        errors of `assess_evidence` and `allocate_context`. Its record ends
        here as `failed` or `cancelled` when it does.
        """
        span = _Span(
            request_id,
            self._audit,
            actor_id=reader.user_id if type(reader) is AuthorizationContext else None,
            question=question.text,
        )
        try:
            if type(reader) is not AuthorizationContext:
                raise InvalidAnswerRequest("reader must be an AuthorizationContext")
```

**9.** Знайдіть:

```python
            if context is not None:
                span.evidence = tuple(item.text for item in context.evidence.items)
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
```

Замініть на:

```python
            if context is not None:
                span.evidence = tuple(item.text for item in context.evidence.items)
        except asyncio.CancelledError:
            await span.close()
            raise
        except Exception as error:
            failed = stream_failure(classify_failure(error), StreamPhase.BEFORE_HEADERS)
            span.failure = type(error).__name__
            span.outcome = "failed"
            span.audit_outcome = failed.audit_outcome
            await span.close()
            raise
        plan = _Plan(
            request_id=request_id,
```

**10.** Знайдіть:

```python
                failure = classify_failure(error)
                span.failure = type(error).__name__
            decision = None if failure is not None else self._decision(plan, finished)
            try:
                if (
                    decision is not None
```

Замініть на:

```python
                failure = classify_failure(error)
                span.failure = type(error).__name__
            decision = None if failure is not None else self._decision(plan, finished)
            if decision is not None:
                cited = len(finished.result.citations) if finished is not None else 0
                sent = cited if decision.decision is Decision.ANSWER else 0
                if not await span.record(
                    finished_outcome(decision.decision), citations=sent
                ):
                    # Nothing of the decision leaves before its audit record.
                    decision, failure = None, FailureKind.DEPENDENCY
                    span.failure = "AuditNotRecorded"
            try:
                if (
                    decision is not None
```

**11.** Знайдіть:

```python
                span.outcome = "failed"
                span.audit_outcome = failed.audit_outcome
                span.partial_text = failed.partial_text
                yield error_event
        finally:
            span.end()

    async def _finished(
        self,
```

Замініть на:

```python
                span.outcome = "failed"
                span.audit_outcome = failed.audit_outcome
                span.partial_text = failed.partial_text
                await span.record(failed.audit_outcome, citations=span.citations)
                yield error_event
        finally:
            await span.close()

    async def _finished(
        self,
```

**12.** Знайдіть:

```python
        try:
            await self._events.aclose()
        finally:
            self._span.end()


class AnswerFieldReader:
```

Замініть на:

```python
        try:
            await self._events.aclose()
        finally:
            await self._span.close()


class AnswerFieldReader:
```

**13.** Знайдіть:

```python
    """The record of one stream: counts, tokens, timing, how it ended."""

    request_id: UUID
    started_at: float = field(default_factory=time.perf_counter)
    text: list[str] = field(default_factory=list[str], repr=False)
    # Raw text for development diagnostics only; never in the default record.
```

Замініть на:

```python
    """The record of one stream: counts, tokens, timing, how it ended."""

    request_id: UUID
    audit: AuditTrail = field(repr=False)
    actor_id: UserId | None = None
    started_at: float = field(default_factory=time.perf_counter)
    text: list[str] = field(default_factory=list[str], repr=False)
    # Raw text for development diagnostics only; never in the default record.
```

**14.** Знайдіть:

```python
    audit_outcome: AuditOutcome = AuditOutcome.CANCELLED
    # What a client that left holds; a decision or an error sets it.
    partial_text: PartialText = PartialText.ABANDONED
    ended: bool = False

    def __post_init__(self) -> None:
```

Замініть на:

```python
    audit_outcome: AuditOutcome = AuditOutcome.CANCELLED
    # What a client that left holds; a decision or an error sets it.
    partial_text: PartialText = PartialText.ABANDONED
    # None until the audit event is tried; then whether it was written.
    audited: bool | None = None
    ended: bool = False

    def __post_init__(self) -> None:
```

**15.** Знайдіть:

```python
        self.text.append(text)
        self.characters += len(text)
        self.deltas += 1

    def end(self) -> None:
        if self.ended:
```

Замініть на:

```python
        self.text.append(text)
        self.characters += len(text)
        self.deltas += 1

    async def record(self, outcome: AuditOutcome, *, citations: int) -> bool:
        """Write this request's one audit event; False if it was not written.

        Tried once: a second call returns whether the first was written.
        The write runs as its own task, so a cancelled caller still waits
        for it, at most `AUDIT_SECONDS`, and is then cancelled again, by
        the same cancellation.
        """
        if self.audited is not None:
            return self.audited
        event = AuditEvent(
            event_id=AuditEventId.new(),
            occurred_at=datetime.now(UTC),
            actor_id=self.actor_id,
            action=AuditAction.QUESTION_ASKED,
            target_kind=AuditTargetKind.REQUEST,
            target_id=self.request_id,
            decision=(
                AuditDecision.DENIED
                if outcome is AuditOutcome.DENIED
                else AuditDecision.ALLOWED
            ),
            outcome=outcome,
            request_id=self.request_id,
            run_id=None,
            metadata={"phase": str(self.phase), "citations": citations},
        )
        self.audited = False
        # A task of its own: cancelling the stream does not cancel the write,
        # which is waited for until the deadline. The shield keeps anyio's
        # cancel scopes from cancelling every wait again; a cancellation by
        # asyncio still arrives, once, and is raised again after the write.
        writing = asyncio.ensure_future(self.audit.record(event))
        writing.add_done_callback(_retrieved)
        caught: asyncio.CancelledError | None = None
        with anyio.CancelScope(shield=True):
            deadline = asyncio.get_running_loop().time() + AUDIT_SECONDS
            while not writing.done():
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    writing.cancel()
                    break
                try:
                    await asyncio.wait({writing}, timeout=remaining)
                except asyncio.CancelledError as cancelled:
                    caught = cancelled
        self.audited = (
            writing.done() and not writing.cancelled() and writing.exception() is None
        )
        if caught is not None:
            # The same exception, so whoever cancelled it still recognises it.
            raise caught
        # A cancel scope that was cancelled meanwhile cancels at the next await.
        return self.audited

    async def close(self) -> None:
        """Record the stream as it ended, unless that was tried, then end it."""
        try:
            if not self.ended and self.audited is None:
                await self.record(self.audit_outcome, citations=self.citations)
        finally:
            self.end()

    def end(self) -> None:
        if self.ended:
```

**16.** Знайдіть:

```python
            "reason": None if self.reason is None else str(self.reason),
            "reply": None if self.reply is None else str(self.reply),
            "defect": None if self.defect is None else str(self.defect),
        }
        if self.usage is not None:
            fields["input_tokens"] = self.usage.input_tokens
```

Замініть на:

```python
            "reason": None if self.reason is None else str(self.reason),
            "reply": None if self.reply is None else str(self.reply),
            "defect": None if self.defect is None else str(self.defect),
            "audited": self.audited,
        }
        if self.usage is not None:
            fields["input_tokens"] = self.usage.input_tokens
```

**17.** Знайдіть:

```python
            }
        level = logging.INFO if self.outcome == "completed" else logging.WARNING
        logger.log(level, "answer stream ended", extra=extra)


async def _close(parts: AsyncGenerator[str | ModelReply]) -> None:
```

Замініть на:

```python
            }
        level = logging.INFO if self.outcome == "completed" else logging.WARNING
        logger.log(level, "answer stream ended", extra=extra)


def _retrieved(task: asyncio.Future[None]) -> None:
    """Read a finished write's error, so asyncio does not log it as lost."""
    if not task.cancelled():
        task.exception()


async def _close(parts: AsyncGenerator[str | ModelReply]) -> None:
```

Цикл у `_Span.record` містить обидва виправлення з рев'ю. Запис іде окремою задачею, тож скасування з боку сервера не може його відкотити. Очікування йде під щитом anyio, тож anyio не скасовує його знову на кожному оберті; скасування від asyncio ловиться один раз і піднімається знову тим самим об'єктом, коли запис закінчиться.

У `src/knowledge_service/telemetry.py`:

**1.** Знайдіть:

```python
                "reason",
                "reply",
                "defect",
                "input_tokens",
                "output_tokens",
            }
```

Замініть на:

```python
                "reason",
                "reply",
                "defect",
                "audited",
                "input_tokens",
                "output_tokens",
            }
```

Створіть `src/knowledge_service/answering/audit_trail.py`:

```python
"""The answer stream's `AuditTrail` over PostgreSQL.

Each event is written in a short transaction of its own, which commits
before `record` returns: when the stream goes on to send its decision, the
event is already durable. Nothing else is written in that transaction, and
a failure raises, so the stream can refuse to send what is not recorded.
"""

from knowledge_service.audit import AuditEvent
from knowledge_service.database import DatabaseRuntime
from knowledge_service.persistence.audit import record_audit_event


class DatabaseAuditTrail:
    """Write each audit event in its own committed transaction."""

    def __init__(self, runtime: DatabaseRuntime) -> None:
        self._runtime = runtime

    async def record(self, event: AuditEvent) -> None:
        """Append `event`; return only once it is committed."""
        async with self._runtime.transaction() as connection:
            await record_audit_event(connection, event)
```

У `tests/test_streaming.py`:

**1.** Знайдіть:

```python
    AnswerFieldReader,
    AnswerFinisher,
    AnswerStreamer,
    CitationCheck,
    FinishedAnswer,
    FoundEvidence,
```

Замініть на:

```python
    AnswerFieldReader,
    AnswerFinisher,
    AnswerStreamer,
    AuditTrail,
    CitationCheck,
    FinishedAnswer,
    FoundEvidence,
```

**2.** Знайдіть:

```python
)
from knowledge_service.answering.support import LexicalSupportJudge
from knowledge_service.app import create_app
from knowledge_service.contracts import Question, Usage
from knowledge_service.errors import AnswerDeadlineExceeded, AuthorizationDenied
from knowledge_service.identifiers import ChunkId, DocumentId
```

Замініть на:

```python
)
from knowledge_service.answering.support import LexicalSupportJudge
from knowledge_service.app import create_app
from knowledge_service.audit import AuditEvent
from knowledge_service.contracts import Question, Usage
from knowledge_service.errors import AnswerDeadlineExceeded, AuthorizationDenied
from knowledge_service.identifiers import ChunkId, DocumentId
```

**3.** Знайдіть:

```python
        return result


class SlowFinisher:
    """Never decides."""

```

Замініть на:

```python
        return result


class Trail:
    """An `AuditTrail` that keeps every event it is given.

    `error` fails every write, and `delay` slows it, as a database that is
    down or stuck would; `started` is set when a write begins.
    """

    def __init__(self, *, error: Exception | None = None, delay: float = 0) -> None:
        self.events: list[AuditEvent] = []
        self.error = error
        self.delay = delay
        self.started = asyncio.Event()

    async def record(self, event: AuditEvent) -> None:
        self.started.set()
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        self.events.append(event)


class SlowFinisher:
    """Never decides."""

```

**4.** Знайдіть:

```python
    policy: AbstentionPolicy = POLICY,
    timeout_seconds: float = 5.0,
    citations: CitationCheck | None = None,
) -> AnswerStreamer:
    return AnswerStreamer(
        source or Source(),
```

Замініть на:

```python
    policy: AbstentionPolicy = POLICY,
    timeout_seconds: float = 5.0,
    citations: CitationCheck | None = None,
    audit: AuditTrail | None = None,
) -> AnswerStreamer:
    return AnswerStreamer(
        source or Source(),
```

**5.** Знайдіть:

```python
        policy,
        AnswerSettings().budget,
        citations=citations or Citations(),
        timeout_seconds=timeout_seconds,
    )

```

Замініть на:

```python
        policy,
        AnswerSettings().budget,
        citations=citations or Citations(),
        audit=audit or Trail(),
        timeout_seconds=timeout_seconds,
    )

```

**6.** Знайдіть:

```python


def test_the_stream_record_hides_the_text_it_counts() -> None:
    span = _Span(REQUEST_ID)  # pyright: ignore[reportPrivateUsage]
    span.sent(MARKER)

    assert MARKER not in repr(span)
```

Замініть на:

```python


def test_the_stream_record_hides_the_text_it_counts() -> None:
    span = _Span(REQUEST_ID, Trail())  # pyright: ignore[reportPrivateUsage]
    span.sent(MARKER)

    assert MARKER not in repr(span)
```

**Перевірка:** `uv run pytest tests/test_streaming.py tests/test_stream_failures.py tests/test_telemetry.py -q` проходить.

### Крок 8 — Крок із кодом: тести для всіх шести дій

**Простими словами:** два нові тестові файли доводять увесь урок. Перший запускає стрім відповіді з фейковим журналом і перевіряє один запис на стрім за будь-якого закінчення, зокрема коли база лежить чи повільна і коли клієнт іде під час запису. Другий виконує кожну з шести дій на справжньому PostgreSQL, читає журнал назад і шукає в кожному рядку слова, підкинуті в Питання, заголовки й помилки.

| Тест | Показує |
|---|---|
| `test_an_answer_is_recorded_once_with_its_reader_and_outcome` | Успіх: одна подія `question.asked` з читачем, запитом і результатом |
| `test_no_citation_or_completion_leaves_before_its_record` | Коли сховище лежить, стрім закінчується `dependency_unavailable` і не надсилає Цитат |
| `test_a_stream_cancelled_while_its_record_is_written_keeps_it` | Найризиковіший край: сервер скасовує стрім посеред запису, а запис усе одно зберігається |
| `test_a_cancel_scope_that_ends_the_stream_mid_record_absorbs_it` | Cancel scope з anyio закінчує стрім посеред запису: один запис, скасування не вилітає, очікування не крутиться |
| `test_the_record_holds_no_question_or_answer_text` | Жодного підкинутого слова в події чи записах логу |
| `test_a_question_and_the_documents_it_cites_are_recorded` | Запит і читання Документа через HTTP, на справжньому PostgreSQL |
| `test_a_citation_revoked_while_the_model_writes_is_a_denied_read` | Заборона: читання з `denied` і Питання з `abstained` |
| `test_every_access_grant_change_is_recorded_with_its_run` | Зміна доступу з ID прогону |
| `test_a_synchronization_run_records_how_it_ended`, `test_a_failed_synchronization_run_records_only_the_error_type` | Синхронізація, успіх і збій |
| `test_a_rating_is_recorded_with_the_user_who_gave_it`, `test_a_rating_and_its_record_roll_back_together` | Відгук і одна транзакція для обох |
| `test_a_grant_write_and_its_record_roll_back_together` | Запис дозволів і його подія ділять одну транзакцію |
| `test_administration_is_recorded_with_the_administrator` | Адміністрування; відхилений запуск нічого не фіксує |
| `test_an_audit_row_cannot_hold_an_unknown_decision` | Власна перевірка бази |
| `test_publishing_a_document_records_its_grants`, `test_an_event_reads_back_in_utc_whatever_the_session_time_zone`, `test_a_downgrade_drops_what_the_old_schema_cannot_hold` | Регресії з рев'ю |

Створіть `tests/test_query_audit.py`:

```python
"""Each answer stream records one `question.asked` audit event, first.

The stream runs with fakes from `test_streaming`; its `AuditTrail` is a
`Trail` that keeps events, or fails or stalls as a database might. The
decision's Citations and completion must never leave before its event is
written, and the event holds who asked and how it ended, never the text.
"""

import asyncio
import json
import logging
from typing import Any

import anyio
import pytest

from evidence_fakes import MARKER
from identity_fakes import READER
from knowledge_service.answering import streaming
from knowledge_service.answering.abstention import EvidenceSignals
from knowledge_service.answering.events import StreamOutcome
from knowledge_service.answering.evidence import EvidenceSet
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.contracts import Question
from knowledge_service.errors import AnswerDeadlineExceeded
from test_streaming import (
    QUESTION,
    REQUEST_ID,
    Model,
    Source,
    Trail,
    chunks,
    rebuilt,
    streamer,
)


def event_names(sent: list[bytes]) -> list[str]:
    return [chunk.decode().split("\n")[1].removeprefix("event: ") for chunk in sent]


async def test_an_answer_is_recorded_once_with_its_reader_and_outcome() -> None:
    trail = Trail()

    answer = rebuilt(await chunks(streamer(Model(), audit=trail)))

    assert answer.outcome is StreamOutcome.ANSWERED
    (event,) = trail.events
    assert event.action is AuditAction.QUESTION_ASKED
    assert (event.target_kind, event.target_id) == (AuditTargetKind.REQUEST, REQUEST_ID)
    assert event.request_id == REQUEST_ID
    assert event.actor_id == READER.user_id
    assert (event.decision, event.outcome) == (
        AuditDecision.ALLOWED,
        AuditOutcome.ANSWERED,
    )
    assert dict(event.metadata) == {"phase": "after_headers", "citations": 1}


async def test_an_abstention_is_recorded_with_no_citations() -> None:
    trail = Trail()

    answer = rebuilt(
        await chunks(streamer(Model(), source=Source(empty()), audit=trail))
    )

    assert answer.outcome is StreamOutcome.ABSTAINED
    (event,) = trail.events
    assert event.outcome is AuditOutcome.ABSTAINED
    assert event.metadata["citations"] == 0


def empty() -> FoundEvidence:
    return FoundEvidence(EvidenceSet(()), EvidenceSignals(0, None, None))


async def test_no_citation_or_completion_leaves_before_its_record() -> None:
    # The audit store is down: the decision is never sent, only an error.
    trail = Trail(error=ConnectionError("audit store down"))

    sent = await chunks(streamer(Model(), audit=trail))

    names = event_names(sent)
    assert "citation" not in names
    assert "completion" not in names
    assert names[-1] == "error"
    error = json.loads(sent[-1].decode().split("\n")[2].removeprefix("data: "))
    assert error["code"] == "dependency_unavailable"
    assert trail.events == []


async def test_a_record_that_takes_too_long_counts_as_not_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(streaming, "AUDIT_SECONDS", 0.05)
    trail = Trail(delay=1.0)

    sent = await chunks(streamer(Model(), audit=trail))

    assert "completion" not in event_names(sent)
    assert event_names(sent)[-1] == "error"


async def test_a_failure_before_the_first_byte_is_recorded() -> None:
    trail = Trail()
    failing = streamer(
        Model(), source=Source(error=AnswerDeadlineExceeded()), audit=trail
    )

    with pytest.raises(AnswerDeadlineExceeded):
        await failing.open(QUESTION, reader=READER, request_id=REQUEST_ID)

    (event,) = trail.events
    assert event.outcome is AuditOutcome.DEPENDENCY_ERROR
    assert event.metadata["phase"] == "before_headers"


async def test_a_client_that_leaves_is_recorded_as_cancelled() -> None:
    trail = Trail()
    model = Model(hold=2)
    events = await streamer(model, audit=trail).open(
        QUESTION, reader=READER, request_id=REQUEST_ID
    )
    await anext(events)
    await anext(events)

    await events.aclose()

    (event,) = trail.events
    assert event.outcome is AuditOutcome.CANCELLED


async def test_a_stream_closed_before_its_first_event_is_recorded() -> None:
    trail = Trail()
    events = await streamer(Model(), audit=trail).open(
        QUESTION, reader=READER, request_id=REQUEST_ID
    )

    await events.aclose()

    (event,) = trail.events
    assert event.outcome is AuditOutcome.CANCELLED
    assert event.metadata["phase"] == "before_headers"


async def test_the_record_holds_no_question_or_answer_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    trail = Trail()
    question = Question(text=f"How many days of leave? {MARKER}")

    await chunks(streamer(Model(), audit=trail), question)

    (event,) = trail.events
    assert MARKER not in repr(event)
    assert "leave" not in repr(event)
    for record in caplog.records:
        assert MARKER not in record.getMessage()
        assert MARKER not in repr(record.__dict__)
    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["audited"] is True  # type: ignore[attr-defined]


async def test_a_failed_record_is_marked_in_telemetry(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)

    await chunks(streamer(Model(), audit=Trail(error=ConnectionError("down"))))

    (ended,) = [r for r in caplog.records if r.getMessage() == "answer stream ended"]
    assert ended.answer_stream["audited"] is False  # type: ignore[attr-defined]
    assert ended.answer_stream["outcome"] == "failed"  # type: ignore[attr-defined]


async def test_a_stream_cancelled_while_its_record_is_written_keeps_it() -> None:
    # The server cancels the task when the client leaves; the write goes on.
    trail = Trail(delay=0.2)
    events = await streamer(Model(), audit=trail).open(
        QUESTION, reader=READER, request_id=REQUEST_ID
    )

    async def read_all() -> list[bytes]:
        return [chunk async for chunk in events]

    reading = asyncio.ensure_future(read_all())
    await trail.started.wait()
    reading.cancel()

    with pytest.raises(asyncio.CancelledError):
        await reading
    (event,) = trail.events
    assert event.outcome is AuditOutcome.ANSWERED


async def test_a_cancel_scope_that_ends_the_stream_mid_record_absorbs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Starlette ends a stream through an anyio cancel scope. The scope must
    # absorb its own cancellation, and the wait must not spin meanwhile.
    waits = 0
    wait = asyncio.wait

    async def counted(*args: Any, **kwargs: Any) -> Any:
        nonlocal waits
        waits += 1
        return await wait(*args, **kwargs)

    monkeypatch.setattr(asyncio, "wait", counted)
    trail = Trail(delay=0.2)
    events = await streamer(Model(), audit=trail).open(
        QUESTION, reader=READER, request_id=REQUEST_ID
    )

    with anyio.CancelScope() as scope:

        async def cancel_once_writing() -> None:
            await trail.started.wait()
            scope.cancel()

        async with anyio.create_task_group() as group:
            group.start_soon(cancel_once_writing)
            async for _ in events:
                pass

    # Reaching this line means the cancellation stayed inside its scope.
    (event,) = trail.events
    assert event.outcome is AuditOutcome.ANSWERED
    assert waits < 5
```

Створіть `tests/test_audit_events_integration.py`:

```python
"""Durable audit events for the six audited actions, in real PostgreSQL.

Each test does one action through its real seam and reads the trail back:
a Question over HTTP (with the Document reads its Citations make), an
Access Grant change, a Synchronization Run, a rating, and an
administrator's embedding migration. Every event names who acted, what
they did to which object, whether it was allowed, how it ended, the request
or run, and when; no row holds a Question, a title, or a Document's text.
"""

import json
import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from database_fixtures import migration_config, migration_environment
from evidence_fakes import MARKER
from fakes import FakeSource
from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.access import Subject
from knowledge_service.answering.audit_trail import DatabaseAuditTrail
from knowledge_service.answering.current_citations import CurrentCitations
from knowledge_service.answering.events import AnswerEventReader, StreamOutcome
from knowledge_service.app import create_app
from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.conversations import (
    ConversationLimits,
    MessageFeedback,
    MessageRating,
    MessageRole,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import Document, DocumentProvenance
from knowledge_service.embeddings.migration_workflow import (
    MigrationInProgress,
    start_embedding_migration,
    switch_to_target_space,
)
from knowledge_service.embeddings.port import EmbeddingModelIdentity
from knowledge_service.identifiers import DocumentId, SynchronizationRunId, UserId
from knowledge_service.ingestion.publishing import publish_document
from knowledge_service.ingestion.reconciliation import (
    narrow_access,
    reconcile_document,
)
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceRecord,
    SourceUnavailable,
)
from knowledge_service.ingestion.synchronization import synchronize_source
from knowledge_service.persistence.audit import (
    load_audit_events,
    load_request_audit_events,
)
from knowledge_service.persistence.conversations import (
    add_message,
    rate_message,
    start_conversation,
)
from knowledge_service.settings import Settings
from test_citation_confirmation_integration import CitingModel, vector_source
from test_publishing_integration import publication_for
from test_streaming import streamer
from test_vector_search_integration import (
    IDENTITY,
    INSERT_SOURCE,
    OBSERVED_AT,
    SOURCE_ID,
    a_document,
    a_space,
    plain_text,
)

READERS_GROUP = next(iter(READER.group_ids))
LATER = OBSERVED_AT + timedelta(hours=1)
REQUEST_ID = UUID("00000000-0000-0000-0000-0000000000e1")
ADMIN = UserId.parse("00000000-0000-0000-0000-0000000000ad")
TARGET = EmbeddingModelIdentity(provider="fake", model="newer-embedding", dimensions=4)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
    await a_space(runtime, IDENTITY, is_current=True)
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def ask(runtime: DatabaseRuntime, model: CitingModel, question: str) -> bytes:
    """Ask over HTTP with a fixed request ID; return every byte sent."""
    # The real vector search, as the stream's Evidence source.
    source: Any = vector_source(runtime)
    app = create_app(
        streamer=streamer(
            model,
            source=source,
            citations=CurrentCitations(runtime),
            audit=DatabaseAuditTrail(runtime),
        ),
        authenticator=TRUSTED,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.post(
            "/v1/answer/stream",
            json={"text": question},
            headers={**AUTHORIZATION, "X-Request-ID": str(REQUEST_ID)},
        )
    assert response.status_code == 200
    return response.content


async def request_trail(runtime: DatabaseRuntime) -> tuple[AuditEvent, ...]:
    async with runtime.transaction() as connection:
        return await load_request_audit_events(
            connection, request_id=REQUEST_ID, limit=10
        )


async def trail(runtime: DatabaseRuntime, target_id: UUID) -> tuple[AuditEvent, ...]:
    async with runtime.transaction() as connection:
        return await load_audit_events(connection, target_id=target_id, limit=10)


async def every_row(runtime: DatabaseRuntime) -> str:
    """Every audit row, as one JSON text, to search for planted words."""
    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(sa.text("SELECT to_jsonb(a) FROM audit_events a"))
        ).scalars()
        return json.dumps(list(rows))


@pytest.mark.integration
async def test_a_question_and_the_documents_it_cites_are_recorded(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    document_id = await a_document(
        runtime,
        f"roadmap-{MARKER}.txt",
        readers=[Subject.group(READERS_GROUP)],
        vector=[1.0, 0.0, 0.0],
    )

    sent = await ask(
        runtime,
        CitingModel(f"roadmap-{MARKER}.txt"),
        f"What is on the roadmap? {MARKER}",
    )

    reader = AnswerEventReader()
    reader.feed(sent)
    assert reader.close().outcome is StreamOutcome.ANSWERED
    read, asked = await request_trail(runtime)
    assert (read.action, read.target_kind, read.target_id) == (
        AuditAction.DOCUMENT_READ,
        AuditTargetKind.DOCUMENT,
        document_id.value,
    )
    assert (read.decision, read.outcome) == (
        AuditDecision.ALLOWED,
        AuditOutcome.SUCCEEDED,
    )
    assert (asked.action, asked.outcome) == (
        AuditAction.QUESTION_ASKED,
        AuditOutcome.ANSWERED,
    )
    for event in (read, asked):
        assert event.actor_id == READER.user_id
        assert event.request_id == REQUEST_ID
        assert event.occurred_at.tzinfo is not None
    # Neither the Question, the title, nor the text was recorded or logged.
    assert MARKER not in await every_row(runtime)
    for record in caplog.records:
        assert MARKER not in record.getMessage()
        assert MARKER not in repr(record.__dict__)


@pytest.mark.integration
async def test_a_citation_revoked_while_the_model_writes_is_a_denied_read(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(
        runtime,
        "roadmap.txt",
        readers=[Subject.group(READERS_GROUP)],
        vector=[1.0, 0.0, 0.0],
    )

    async def revoke() -> None:
        await reconcile_document(
            runtime, plain_text("roadmap.txt"), readers=[], observed_at=LATER
        )

    await ask(runtime, CitingModel("roadmap.txt", before_reply=revoke), "Roadmap?")

    read, asked = await request_trail(runtime)
    assert read.target_id == document_id.value
    assert (read.decision, read.outcome) == (
        AuditDecision.DENIED,
        AuditOutcome.DENIED,
    )
    assert asked.outcome is AuditOutcome.ABSTAINED


@pytest.mark.integration
async def test_every_access_grant_change_is_recorded_with_its_run(
    runtime: DatabaseRuntime,
) -> None:
    run_id = SynchronizationRunId.new()
    created = await reconcile_document(
        runtime,
        plain_text("handbook.txt"),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT,
        run_id=run_id,
    )
    unchanged = await reconcile_document(
        runtime,
        plain_text("handbook.txt"),
        readers=[Subject.public()],
        observed_at=LATER,
        run_id=run_id,
    )
    narrowed = await narrow_access(
        runtime,
        source_id=SOURCE_ID,
        external_id="handbook.txt",
        readers=[],
        observed_at=LATER,
        run_id=run_id,
    )

    assert unchanged.document == created.document
    assert narrowed
    events = await trail(runtime, created.document.document_id.value)
    assert [(e.action, dict(e.metadata)) for e in events] == [
        (
            AuditAction.DOCUMENT_ACCESS_CHANGED,
            {"authorization_version": 1, "readers": 1},
        ),
        (
            AuditAction.DOCUMENT_ACCESS_CHANGED,
            {"authorization_version": 2, "readers": 0},
        ),
    ]
    assert {e.run_id for e in events} == {run_id.value}
    assert {e.actor_id for e in events} == {None}


def public_record(name: str) -> SourceRecord:
    return SourceRecord.present(
        external_id=name,
        version="v1",
        media_type="text/plain",
        content=f"The {name} policy.".encode(),
        access=SourceAccess(public=True),
    )


@pytest.mark.integration
async def test_a_synchronization_run_records_how_it_ended(
    runtime: DatabaseRuntime,
) -> None:
    summary = await synchronize_source(
        runtime,
        FakeSource([[public_record("a.txt"), public_record("b.txt")]]),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )

    *granted, finished = await trail(runtime, SOURCE_ID.value)
    assert granted == []
    assert (finished.action, finished.outcome) == (
        AuditAction.SYNCHRONIZATION_FINISHED,
        AuditOutcome.SUCCEEDED,
    )
    assert finished.run_id == summary.run_id.value
    assert dict(finished.metadata) == {
        "changed": 2,
        "unchanged": 0,
        "quarantined": 0,
        "tombstoned": 0,
        "unresolved": 0,
    }
    async with runtime.transaction() as connection:
        runs = (
            await connection.execute(
                sa.text(
                    "SELECT DISTINCT run_id FROM audit_events"
                    " WHERE action = 'document.access_changed'"
                )
            )
        ).scalars()
        assert list(runs) == [summary.run_id.value]


@pytest.mark.integration
async def test_a_failed_synchronization_run_records_only_the_error_type(
    runtime: DatabaseRuntime,
) -> None:
    run_id = SynchronizationRunId.new()

    with pytest.raises(SourceUnavailable):
        await synchronize_source(
            runtime,
            FakeSource(
                [[public_record("a.txt")]],
                fail_on_call=1,
                failure=SourceUnavailable(f"timeout reading {MARKER}"),
            ),
            source_id=SOURCE_ID,
            observed_at=OBSERVED_AT,
            principal_directory=None,
            run_id=run_id,
        )

    (finished,) = await trail(runtime, SOURCE_ID.value)
    assert (finished.outcome, finished.run_id) == (AuditOutcome.FAILED, run_id.value)
    assert dict(finished.metadata) == {"failure": "SourceUnavailable"}
    assert MARKER not in await every_row(runtime)


@pytest.mark.integration
async def test_a_rating_is_recorded_with_the_user_who_gave_it(
    runtime: DatabaseRuntime,
) -> None:
    limits = ConversationLimits()
    async with runtime.transaction() as connection:
        conversation = await start_conversation(
            connection, owner_id=READER.user_id, now=OBSERVED_AT, limits=limits
        )
        message = await add_message(
            connection,
            owner_id=READER.user_id,
            conversation_id=conversation.conversation_id,
            role=MessageRole.USER,
            text=f"A question {MARKER}",
            now=OBSERVED_AT,
            limits=limits,
        )
        await rate_message(
            connection,
            feedback=MessageFeedback(
                message_id=message.message_id,
                user_id=READER.user_id,
                rating=MessageRating.DOWN,
                rated_at=LATER,
            ),
        )

    (rated,) = await trail(runtime, message.message_id.value)
    assert (rated.action, rated.target_kind) == (
        AuditAction.MESSAGE_RATED,
        AuditTargetKind.MESSAGE,
    )
    assert (rated.actor_id, rated.occurred_at) == (READER.user_id, LATER)
    assert dict(rated.metadata) == {"rating": "down"}
    assert MARKER not in await every_row(runtime)


@pytest.mark.integration
async def test_a_rating_and_its_record_roll_back_together(
    runtime: DatabaseRuntime,
) -> None:
    limits = ConversationLimits()
    async with runtime.transaction() as connection:
        conversation = await start_conversation(
            connection, owner_id=READER.user_id, now=OBSERVED_AT, limits=limits
        )
        message = await add_message(
            connection,
            owner_id=READER.user_id,
            conversation_id=conversation.conversation_id,
            role=MessageRole.USER,
            text="A question",
            now=OBSERVED_AT,
            limits=limits,
        )

    class Abandoned(Exception):
        """The caller fails after rating, before its transaction commits."""

    async def rate_then_fail() -> None:
        async with runtime.transaction() as connection:
            await rate_message(
                connection,
                feedback=MessageFeedback(
                    message_id=message.message_id,
                    user_id=READER.user_id,
                    rating=MessageRating.UP,
                    rated_at=LATER,
                ),
            )
            raise Abandoned

    with pytest.raises(Abandoned):
        await rate_then_fail()

    assert await trail(runtime, message.message_id.value) == ()


@pytest.mark.integration
async def test_administration_is_recorded_with_the_administrator(
    runtime: DatabaseRuntime,
) -> None:
    migration = await start_embedding_migration(
        runtime, TARGET, now=OBSERVED_AT, actor_id=ADMIN
    )
    with pytest.raises(MigrationInProgress):
        await start_embedding_migration(runtime, TARGET, now=LATER, actor_id=ADMIN)
    switched = await switch_to_target_space(
        runtime, migration_id=migration.migration_id, now=LATER, actor_id=ADMIN
    )

    started, switched_event = await trail(runtime, migration.migration_id.value)
    assert [
        (e.action, e.actor_id, dict(e.metadata)) for e in (started, switched_event)
    ] == [
        (
            AuditAction.EMBEDDING_MIGRATION_STARTED,
            ADMIN,
            {"state": migration.state.value},
        ),
        (
            AuditAction.EMBEDDING_MIGRATION_SWITCHED,
            ADMIN,
            {"state": switched.state.value},
        ),
    ]
    # The refused second start changed nothing and recorded nothing.
    async with runtime.transaction() as connection:
        count = (
            await connection.execute(sa.text("SELECT count(*) FROM audit_events"))
        ).scalar()
    assert count == 2


@pytest.mark.integration
async def test_an_audit_row_cannot_hold_an_unknown_decision(
    runtime: DatabaseRuntime,
) -> None:
    # Past the Python checks, the database still refuses an unknown value.
    with pytest.raises(IntegrityError, match="ck_audit_events_decision"):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO audit_events (event_id, occurred_at, action,"
                    " target_kind, target_id, metadata, decision, outcome)"
                    " VALUES (gen_random_uuid(), now(), 'document.read', 'document',"
                    " gen_random_uuid(), '{}'::jsonb, 'maybe', 'succeeded')"
                )
            )


@pytest.mark.integration
async def test_publishing_a_document_records_its_grants(
    runtime: DatabaseRuntime,
) -> None:
    document = Document.register(
        title="Handbook",
        document_id=DocumentId.new(),
        provenance=DocumentProvenance(
            source_id=SOURCE_ID, external_id="page-a", source_version="1"
        ),
        content_fingerprint="content-a",
        authorization_fingerprint="acl-a",
        observed_at=OBSERVED_AT,
    )
    async with runtime.transaction() as connection:
        stored_id = await publish_document(
            connection, publication_for(document, text_body="Setup.")
        )

    (event,) = await trail(runtime, stored_id.value)
    assert event.action is AuditAction.DOCUMENT_ACCESS_CHANGED
    assert dict(event.metadata) == {"authorization_version": 1, "readers": 1}


@pytest.mark.integration
async def test_an_event_reads_back_in_utc_whatever_the_session_time_zone(
    runtime: DatabaseRuntime,
) -> None:
    run_id = SynchronizationRunId.new()
    created = await reconcile_document(
        runtime,
        plain_text("handbook.txt"),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT,
        run_id=run_id,
    )

    async with runtime.transaction() as connection:
        await connection.execute(sa.text("SET LOCAL TIME ZONE 'Europe/Kyiv'"))
        (event,) = await load_audit_events(
            connection, target_id=created.document.document_id.value, limit=10
        )

    assert event.occurred_at == OBSERVED_AT
    assert event.occurred_at.utcoffset() == timedelta(0)


@pytest.mark.integration
def test_a_downgrade_drops_what_the_old_schema_cannot_hold(
    migrated_database: str,
) -> None:
    # A denied event must not come back as allowed after a round trip.
    # Synchronous: Alembic runs its own event loop.
    url = make_url(migrated_database)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO audit_events (event_id, occurred_at, action,"
                    " target_kind, target_id, metadata, decision, outcome)"
                    " VALUES (gen_random_uuid(), now(), 'job.queued', 'job',"
                    " gen_random_uuid(), '{}'::jsonb, 'denied', 'failed')"
                )
            )
        with migration_environment(url):
            command.downgrade(migration_config(), "a9b0c1d2e3f4")
            command.upgrade(migration_config(), "head")
        with engine.begin() as connection:
            count = connection.execute(
                sa.text("SELECT count(*) FROM audit_events")
            ).scalar()
    finally:
        engine.dispose()

    assert count == 0


@pytest.mark.integration
async def test_a_grant_write_and_its_record_roll_back_together(
    runtime: DatabaseRuntime,
) -> None:
    document = Document.register(
        title="Handbook",
        document_id=DocumentId.new(),
        provenance=DocumentProvenance(
            source_id=SOURCE_ID, external_id="page-b", source_version="1"
        ),
        content_fingerprint="content-b",
        authorization_fingerprint="acl-b",
        observed_at=OBSERVED_AT,
    )

    class Abandoned(Exception):
        """The caller fails after publishing, before its transaction commits."""

    async def publish_then_fail() -> None:
        async with runtime.transaction() as connection:
            await publish_document(
                connection, publication_for(document, text_body="Setup.")
            )
            raise Abandoned

    with pytest.raises(Abandoned):
        await publish_then_fail()

    async with runtime.transaction() as connection:
        count = (
            await connection.execute(sa.text("SELECT count(*) FROM audit_events"))
        ).scalar()
    assert count == 0
```

**Перевірка:** `uv run pytest tests/test_audit.py tests/test_query_audit.py -q` дає `36 passed`, а `uv run pytest -m integration tests/test_audit_events_integration.py -q` дає `13 passed`.

### Крок 9 — Без коду в цьому кроці: зламайте навмисно

Цей крок прибирає запис зі змін дозволів на доступ і показує, які тести це помічають.

**Дія 1:** у `_write_grants` у `src/knowledge_service/ingestion/reconciliation.py` видаліть ці три рядки:

```python
    await record_audit_event(
        connection, access_event(document, readers=len(grant_run), run_id=run_id)
    )
```

**Дія 2:** запустіть `uv run pytest -m integration tests/test_audit_events_integration.py -q`. Очікувано: `3 failed, 10 passed`: тест зміни доступу, тест синхронізації (він перевіряє ID прогону на подіях доступу) і тест UTC (він читає подію доступу назад).

**Дія 3:** поверніть три рядки і запустіть ту саму команду: `13 passed`. Порівняйте з останнім передбаченням у файлі evidence.

### Крок 10 — Без коду в цьому кроці: документи, перевірки, evidence і прогрес

Цей крок оновлює те, що зміна зробила неточним, доводить, що все працює, і записує результат. Модель загроз називала M07-T14 відповідальним за один сценарій зловживання і казала, що подій аудиту немає; тепер вона описує їх і їхні межі.

**Дія 1:** оновіть два документи.

У `docs/architecture.md`:

**1.** Знайдіть:

```markdown
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs and no tools, where a reply holding a tool call is unreadable; `AnswerSettings` gives the budget), `has_active_content` / `unsourced_links` (`answer_content`, what an Answer may hold whatever a Document told the model: no Markdown image, loading HTML tag, or mermaid block, and only links an Evidence it cites contains; `held_from` marks where a stream stops sending text until the check), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds the Evidence the request's reader may see, rereads it as the reader with `evidence_still_visible`, and judges it before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply, has its `CitationCheck` (`CurrentCitations`, each call in a short transaction of its own) reread every cited Chunk as the reader with `confirm_citations`, withdrawing the Answer if one fails, and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `VectorEvidenceSource` (`vector_evidence`, the stream's Evidence from PostgreSQL: checks the reader, embeds the Question, requires one vector from its own model, and runs the vector search with the request's Authorization Context, so eligibility is decided in the same SQL that ranks; no reranking, so its signals carry no score), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

```

Замініть на:

```markdown
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs and no tools, where a reply holding a tool call is unreadable; `AnswerSettings` gives the budget), `has_active_content` / `unsourced_links` (`answer_content`, what an Answer may hold whatever a Document told the model: no Markdown image, loading HTML tag, or mermaid block, and only links an Evidence it cites contains; `held_from` marks where a stream stops sending text until the check), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`, with `DatabaseAuditTrail` (`audit_trail`) as its `AuditTrail`: one `question.asked` audit event per stream, committed before the decision's Citations and completion are sent; finds the Evidence the request's reader may see, rereads it as the reader with `evidence_still_visible`, and judges it before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply, has its `CitationCheck` (`CurrentCitations`, each call in a short transaction of its own) reread every cited Chunk as the reader with `confirm_citations`, withdrawing the Answer if one fails, and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `VectorEvidenceSource` (`vector_evidence`, the stream's Evidence from PostgreSQL: checks the reader, embeds the Question, requires one vector from its own model, and runs the vector search with the request's Authorization Context, so eligibility is decided in the same SQL that ranks; no reranking, so its signals carry no score), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

```

**2.** Знайдіть:

```markdown
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code. |
| `audit_events` | No raw content, and a metadata bag capped at 1024 bytes. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |

A rule that spans rows of one table, such as one running Synchronization Run per
```

Замініть на:

```markdown
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code. |
| `audit_events` | An action, target kind, decision (`allowed` or `denied`), and outcome from fixed lists, with optional request and Synchronization Run IDs; no raw content: metadata keys are short names, string values one plain word, and the bag is capped at 1024 bytes. Rows are only ever inserted. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |

A rule that spans rows of one table, such as one running Synchronization Run per
```

У `docs/threat-model.md`:

**1.** Знайдіть:

```markdown
| Random key IDs make the API flood the identity provider, or an outage opens the API | An unknown key ID refreshes the keys at most once per 30 seconds; cached keys outlive failed refreshes for at most an hour, then every token is refused as unavailable | None |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; the telemetry handler exports only allowlisted fields (`TELEMETRY_FIELDS`) with plain values, only this service's own messages, and only an exception's type; raw text appears only under development diagnostics, which production refuses; tests scan the exported JSON for seeded sentences | Span attributes pass the same rules when traces arrive (M10-T14) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | Durable audit events (M07-T14) |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |

## Invariant trace
```

Замініть на:

```markdown
| Random key IDs make the API flood the identity provider, or an outage opens the API | An unknown key ID refreshes the keys at most once per 30 seconds; cached keys outlive failed refreshes for at most an hour, then every token is refused as unavailable | None |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; the telemetry handler exports only allowlisted fields (`TELEMETRY_FIELDS`) with plain values, only this service's own messages, and only an exception's type; raw text appears only under development diagnostics, which production refuses; tests scan the exported JSON for seeded sentences | Span attributes pass the same rules when traces arrive (M10-T14) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | An audit event per evaluation run when runs get an entry point |
| An action on content or access leaves no trace, or its trace holds content | Six actions write one `audit_events` row each: a grant change, a rating, and an embedding migration step in the same transaction as the change; a pass of `synchronize_source` when it ends; each cited Document (`document.read`, `allowed` or `denied`) in the Citation check's transaction; and each answer stream (`question.asked`) before its decision is sent, or the stream ends with an error. Rows hold identifiers, fixed labels, and one-word metadata, checked in Python and by the database; tests plant words in Questions, titles, and errors and search every row | Database roles that forbid updating or deleting audit rows; retention and review of the trail |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |

## Invariant trace
```

**2.** Знайдіть:

```markdown
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `tests/test_synchronization_integration.py::test_a_revocation_is_published_before_any_record_of_its_page_is_parsed`, `tests/test_revocation_integration.py::test_a_grant_revoked_mid_conversation_fails_every_later_read_closed` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `tests/test_retrieved_instructions.py::test_an_obeyed_injection_never_reaches_the_client`, `tests/test_retrieved_instructions.py::test_the_model_is_offered_no_tool_and_a_tool_call_is_no_answer` |
| 5 | Log records hold counts, names, and outcomes only; the telemetry handler exports only allowlisted plain fields, and raw text only under development diagnostics, never in production | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `tests/test_telemetry.py::test_an_answer_exports_its_counts_and_none_of_its_text`, `tests/test_telemetry.py::test_text_in_fields_and_arguments_is_dropped_or_redacted`, `tests/test_telemetry.py::test_production_refuses_development_diagnostics`, `M10-T14` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `tests/test_existence_leaks_integration.py::test_a_forbidden_best_match_changes_nothing_the_reader_or_logs_see`, `tests/test_existence_leaks_integration.py::test_citing_a_forbidden_chunk_reads_like_citing_a_missing_one`, `M07-T15` |

```

Замініть на:

```markdown
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `tests/test_synchronization_integration.py::test_a_revocation_is_published_before_any_record_of_its_page_is_parsed`, `tests/test_revocation_integration.py::test_a_grant_revoked_mid_conversation_fails_every_later_read_closed` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `tests/test_retrieved_instructions.py::test_an_obeyed_injection_never_reaches_the_client`, `tests/test_retrieved_instructions.py::test_the_model_is_offered_no_tool_and_a_tool_call_is_no_answer` |
| 5 | Log records hold counts, names, and outcomes only; the telemetry handler exports only allowlisted plain fields, and raw text only under development diagnostics, never in production; audit events hold identifiers, fixed labels, and one-word metadata only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_query_audit.py::test_the_record_holds_no_question_or_answer_text`, `tests/test_audit_events_integration.py::test_a_question_and_the_documents_it_cites_are_recorded`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `tests/test_telemetry.py::test_an_answer_exports_its_counts_and_none_of_its_text`, `tests/test_telemetry.py::test_text_in_fields_and_arguments_is_dropped_or_redacted`, `tests/test_telemetry.py::test_production_refuses_development_diagnostics`, `M10-T14` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `tests/test_existence_leaks_integration.py::test_a_forbidden_best_match_changes_nothing_the_reader_or_logs_see`, `tests/test_existence_leaks_integration.py::test_citing_a_forbidden_chunk_reads_like_citing_a_missing_one`, `M07-T15` |

```

**3.** Знайдіть:

```markdown
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- No endpoint reads a whole Document, an excerpt, or an export by ID yet; each must read through `eligible_chunks_predicate` with the request's reader when it is built, as the agent tools must (M11-T01).
- Telemetry is log records only; there are no traces until M10-T14 and no audit events until M07-T14. No production entry point calls `configure_telemetry` yet: whoever builds it must, and must send loggers that do not propagate, such as uvicorn's, to the root logger. A single plain word, such as a model name or a group path, passes the value rule, so an allowlisted field must never hold Document text.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
```

Замініть на:

```markdown
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- No endpoint reads a whole Document, an excerpt, or an export by ID yet; each must read through `eligible_chunks_predicate` with the request's reader when it is built, as the agent tools must (M11-T01).
- Telemetry is log records only; there are no traces until M10-T14. Audit events are rows in `audit_events` that nothing reads yet but tests; the service's own database user can still update or delete them, and an answer stream whose cancellation or failure cannot be recorded says so only in telemetry (`audited: false`). Administration is recorded only for starting and switching an embedding migration, whose callers pass the administrator's ID by hand until an entry point checks the role. A caller refused by role before the stream opens gets a 403 that no audit event records. A `document.read` event records the Citation check's decision, not delivery: an Answer abstained after it still leaves `allowed` reads. If the event writer refused an event after the decision was recorded, which only a bug can cause, the event keeps the decision while the client gets an error. A cancelled synchronization pass records nothing, and a pass whose success cannot be recorded raises although its work is committed. Ratings and migration steps carry no request ID until an HTTP entry point passes one, and no production entry point builds the answer stream with `DatabaseAuditTrail` yet: whoever writes it must. No production entry point calls `configure_telemetry` yet: whoever builds it must, and must send loggers that do not propagate, such as uvicorn's, to the root logger. A single plain word, such as a model name or a group path, passes the value rule, so an allowlisted field must never hold Document text.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
```

**Дія 2:** запустіть перевірки по черзі, з увімкненими PostgreSQL і Keycloak (`make keycloak-up`):

```bash
uv run pytest -m integration tests/test_audit_events_integration.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний прогін дав 2896 passed для `make test-unit`, на 28 більше, ніж після M07-T13, і 654 passed для `make test-integration`, на 13 більше.

**Дія 3:** заповніть файл evidence:

- **Design:** поля події і правила міток, шість місць і з якою транзакцією ділить кожен запис, запис стріму перед рішенням і ID прогону.
- **Verification:** команди й кількості, навмисна поломка і відомі межі з «Перед початком».
- **Review:** знахідки рев'ю з «Перед початком».
- **Decision:** чому мітки, а не вільний текст; чому та сама транзакція; чому стрім відмовляється надсилати незафіксоване рішення; чому запис іде окремою задачею; плюс принаймні одне **спростоване припущення** у формі «I assumed X. The run or review shows Y. So Z is wrong.»
- **Reflection:** ваші відповіді на питання нижче.

Потім позначте задачу завершеною, додайте рядок M07-T14 у `docs/curriculum/PROGRESS.md`, встановіть наступною задачею M07-T15 і зробіть коміт `m07-t14: write durable audit events`.

## Питання для роздумів

1. Той, хто викликає `rate_message`, передає лише оцінку. Що функція тепер ховає від нього, і чому безпечніше, щоб запис робився всередині функції, а не кожним, хто її викликає?
2. Хтось пізніше пришвидшує стрім: пише запис Питання вже після фінальної події. Що тоді може отримати читач такого, чого журнал аудиту ніколи не покаже?
3. Подія `document.read` каже `denied`, коли Цитата не пройшла, але клієнт бачить ту саму відмову, що й після порожнього пошуку. Чому журналу аудиту можна знати більше, ніж клієнтові?
4. Рядок аудиту може містити одне слово, як-от `SourceUnavailable`. Яке поле все ще могло б витекти щось чутливе, якщо пізніша зміна покладе туди не те слово, і що б це помітило?
5. Відхилена перевірка ролі (403) сьогодні не лишає події аудиту. Що знадобилося б, щоб її фіксувати, і який тест довів би, що це працює?

## Чекліст завершення

- [ ] Файл evidence містить інваріант і ваші передбачення, написані до змін.
- [ ] `AuditEvent` має рішення, результат, ID запиту й ID прогону і відхиляє речення, довгі числа й неправильні типи.
- [ ] Міграція додає стовпці й перевірки; відкат ніколи не переписує історію.
- [ ] Зміни доступу, оцінки й кроки міграції фіксують свою подію в тій самій транзакції.
- [ ] Прохід синхронізації фіксує, чим закінчився, а кожна подія доступу має його ID прогону.
- [ ] Перевірка Цитат фіксує один `document.read` на кожен процитований Документ, `allowed` або `denied`.
- [ ] Стрім фіксує `question.asked` до того, як надіслано рішення, і переживає скасування посеред запису.
- [ ] `tests/test_query_audit.py` (11) і `tests/test_audit_events_integration.py` (13) проходять.
- [ ] Ви зробили навмисну поломку, побачили 3 тести, що впали, і повернули все назад.
- [ ] `docs/architecture.md` і `docs/threat-model.md` описують події аудиту та їхні межі.
- [ ] Усі перевірки проходять: формат, лінтер, типи, unit, integration, документація.
- [ ] Evidence, статус задачі, рядок у PROGRESS і коміт готові.

Коли напишете файли, поділіться ними або виводом помилок, і я перевірю їх за цим чеклістом.
