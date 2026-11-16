# M06-T10 — Зберігайте обмежені розмови

Англійська версія: [M06-T10-persist-bounded-conversations.md](M06-T10-persist-bounded-conversations.md)

Вихідне завдання: [M06-T10](../curriculum/milestones/M06-answers-and-conversations/M06-T10-persist-bounded-conversations.md)

## Що ви зробите

Цей урок дає розмовам (Conversation) те сховище, якого потребує чат: кожна належить одному користувачу, тримає повідомлення у фіксованому порядку, перестає бути видимою тієї миті, коли її видалено чи вона прострочилася, і не може рости без меж. M02-T09 уже створив таблиці й прості значення. Бракує частини, через яку проходить запит користувача: функцій, що завжди питають «чия вона?» усередині SQL, правильно нумерують повідомлення, навіть коли два приходять одночасно, і видаляють одразу.

- `ConversationLimits` тримає межі: термін зберігання, кількість повідомлень, довжину одного повідомлення.
- `start_conversation`, `add_message`, `read_conversation` і `delete_conversation` — сховище з прив'язкою до власника.
- `ConversationNotFound` — одна відповідь на «немає, не ваша, видалена чи прострочена».

| Завдання вимагає | Де це відбувається |
|---|---|
| Власник розмови | Крок 3: кожна функція з прив'язкою до власника кладе `UserId` у SQL |
| Упорядковані повідомлення | Крок 3: `add_message` блокує рядок розмови і дає наступний ordinal |
| Термін зберігання | Крок 3: розмова після свого терміну читається як відсутня |
| Стан видалення | Крок 3: `delete_conversation` ставить `deleted_at` і ставить у чергу Job очищення в одній транзакції |
| Ліміти | Крок 2: `ConversationLimits` і `checked_text`; крок 3 рахує повідомлення під блокуванням |
| Незалежно від формату Open WebUI | Крок 2: репліки user і assistant звичайним текстом, власними термінами сервісу |
| Інтеграційні тести забезпечують власника, порядок і негайне логічне видалення | Крок 4: `tests/test_conversation_store_integration.py` |
| Жодного підкладеного тексту в телеметрії | Крок 4: підкладений маркер ніколи не потрапляє в помилку чи repr |

Цей урок містить код: кроки 2–4 пишуть код. Кроки 1, 5 і 6 — без коду. Інтеграційним тестам потрібна тестова база.

Терміни цього уроку:

- **З прив'язкою до власника (owner-scoped):** запит, що несе особу того, хто питає, у своєму `WHERE`, тож рядки інших користувачів ніколи не виходять з бази, — замість того щоб завантажити рядок і потім перевірити власника в Python.
- **Логічне видалення (logical deletion):** позначити рядок як видалений (`deleted_at`) замість того, щоб прибрати його. Кожне читання одразу його ігнорує; пізніший Job очищення прибирає рядки остаточно, як смітник, який виносять уночі.
- **Блокування рядка (row lock):** `SELECT ... FOR UPDATE` змушує інші транзакції, яким потрібен той самий рядок, чекати, доки ця не закінчиться. Тут воно змушує два повідомлення однієї розмови йти по черзі.
- **Рівень ізоляції (isolation level):** скільки одна транзакція бачить з інших, що йдуть одночасно. Типовий для PostgreSQL, READ COMMITTED, дає кожній команді бачити все, що закомічено до її початку.
- **Ordinal:** позиція повідомлення в розмові: 0, 1, 2 і так далі.

## Перед початком

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілий, коли ви до нього дійдете.

- **Що вже є.** `conversations.py` має `Conversation`, `Message`, `MessageRole` і `Conversation.require_owner`. `persistence/conversations.py` зберігає і читає їх, а схема тримає одне повідомлення на ordinal і термін, пізніший за час створення. Ті функції не беруть власника: вони підходять для системної роботи, як-от прибирання за терміном, а не для запиту користувача.
- **Авторизація всередині SQL.** Якщо завантажити розмову, а потім викликати `require_owner`, чужий рядок спершу потрапить у пам'ять. Нові функції кладуть `owner_id` у кожен `WHERE`, тож рядок, що вам не належить, ніколи не виходить з PostgreSQL.
- **Одна відповідь на «не ваша».** Відсутня, чужа, видалена і прострочена розмова — усі читаються як None і відхиляють повідомлення тим самим `ConversationNotFound`. Різні відповіді дали б змогу перевіряти, чи існує чиясь розмова.
- **Повідомлення нумерує база.** Той, хто викликає, більше не обирає ordinal. `add_message` блокує рядок розмови, читає найбільший ordinal і пише наступний. Два повідомлення, що прийшли разом, чекають одне одного, тож немає пропусків, дублів, і ліміт не можна перевищити.
- **Читання не бере блокування.** Видалення лише позначає рядок, тож повідомлення ще на місці для читання. Без блокування прочитати історію, а потім додати запитання в одній транзакції не може призвести до взаємоблокування.
- **Видалено одразу, прибрано пізніше.** `delete_conversation` ставить `deleted_at` і ставить у чергу Job `purge_conversation` в одній транзакції. Від її коміту кожне читання і запис з прив'язкою до власника вважають розмову відсутньою.
- **Час.** Кожен `now` мусить мати часовий пояс і спершу переводиться в UTC, тож 30 днів зберігання — це 30 днів реального часу навіть через перехід на літній час. Час повідомлення ніколи не йде назад, а видалення ніколи не раніше за останнє повідомлення, навіть коли годинники двох серверів розходяться.
- **Ліміти до SQL.** `checked_text` відхиляє порожній текст, текст понад ліміт і текст, який PostgreSQL не може зберегти: символ NUL і половину символу (одиночний сурогат). Ніщо не доходить до бази, щоб упасти там нетипізованою помилкою.
- **Наш власний формат.** Open WebUI, чат-інтерфейс з M09, надсилає власний JSON з ролями на кшталт `system`. Сховище тримає лише те, що визначає цей сервіс: репліку user чи assistant звичайним текстом. Перекладатиме адаптер інтерфейсу.
- **Що знайшло рев'ю.** Змагальне рев'ю атакувало власника, порядок, видалення, термін зберігання і ворожі дані на справжньому PostgreSQL. Воно не знайшло способу прочитати, доповнити чи видалити чужу розмову і жодного пропуску чи дубля в ordinal. Воно знайшло такі проблеми, кожну виправлено з тестом, що падає без виправлення:
  - Читання зі спільним блокуванням, а потім додавання чи видалення в тій самій транзакції давали взаємоблокування, коли два запити робили це одночасно. Тепер читання не бере блокування.
  - Старт біля кінця календаря чи термін `timedelta.max` кидали нетипізовану `OverflowError`. Тепер термін щонайбільше 10 років, а переповнення — це `InvalidConversation`.
  - `now` у поясі на кшталт Лондона проходив перевірку, але зсував термін на годину літнього часу або відхилявся. Тепер кожен `now` спершу переводиться в UTC, а пояс, що падає, дає типізовану відмову.
  - Видалення могло бути раніше за повідомлення, закомічене до нього, коли годинники розходилися. Тепер воно ніколи не раніше за останнє повідомлення.
  - За REPEATABLE READ чи SERIALIZABLE очікування черги кидало помилку серіалізації. Тепер функції кажуть, що очікують READ COMMITTED, типовий рівень проєкту.
- **Де живе код.** `conversations.py` і `persistence/conversations.py`. Схема не змінюється.
- **Відомі обмеження.** Функції очікують READ COMMITTED, типовий рівень проєкту; на суворішому рівні очікування черги стає помилкою серіалізації. Видалені рядки поки ніхто не прибирає: Job очищення ставиться в чергу, а його виконавець з'явиться в M08. Власник, що видаляє власну прострочену розмову, отримує True, тоді як читання і додавання вважають її відсутньою; про інших користувачів це нічого не розкриває. Функції M02 без власника лишаються для системної роботи.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої передбачення

Поки коду ще немає, запишіть, що має бути правдою наприкінці уроку. Крок 5 перевіряє передбачення.

Створіть `docs/evidence/M06-T10-persist-bounded-conversations.md`:

```markdown
# M06-T10 — Persist bounded Conversations evidence

## Invariant

`start_conversation`, `add_message`, `read_conversation`, and
`delete_conversation` put the caller's `UserId` in their SQL, so another
User's Conversation never leaves the database. A Conversation that is
missing, another User's, deleted, or past its retention deadline reads as
None and refuses a Message with the same `ConversationNotFound`.
`add_message` gives each Message the next ordinal under a row lock, so
concurrent Messages get 0, 1, 2, and so on, with no gap or duplicate, and
never pass `max_messages`. `delete_conversation` marks the Conversation
deleted and queues one purge Job in the same transaction; from its commit
on, nothing of it can be read or added to. `ConversationLimits` refuses
text that is empty, too long, or not storable before any SQL runs. Errors
and reprs hold no Message text.

## Expected failing check

Before the change, `uv run python -c "from knowledge_service.persistence.conversations import add_message, delete_conversation"`
fails with `ImportError`: cannot import name `add_message`.

## Predictions

- What a User sees when they ask for another User's Conversation, compared with one that never existed: ...
- What ordinals eight Messages get when they are added to one Conversation at the same moment, and what makes that so: ...
- How many integration tests fail if `add_message` stops locking the Conversation's row, and which ones: ...

## Design

## Verification

## Decision

## Reflection
```

Перевірка: файл існує, перевірка, що має впасти, падає, а кожне передбачення — повне речення, яке крок 5 може спростувати.

### Крок 2 — Крок із кодом: ліміти і відповідь «не знайдено»

Цей крок додає межі розмови й одну помилку, яку отримує той, хто питає «не своє».

**Простими словами:** Тепер у розмови є записані ліміти: скільки вона живе, скільки повідомлень може тримати і якої довжини може бути одне повідомлення. Перш ніж текст наблизиться до бази, його перевіряють за ними і за тим, що база може зберегти. Нова помилка каже «такої розмови немає» тими самими словами, хоч би якою була справжня причина, а повідомлення ніколи не показує свій текст, коли його друкують.

| Частина | Що містить чи робить |
|---|---|
| `ConversationNotFound` | Одна помилка для відсутньої, чужої, видаленої і простроченої |
| `Message.text` | Прихований з repr |
| `Conversation.open` | Відхиляє термін за кінцем календаря типізованою помилкою |
| `ConversationLimits` | Термін зберігання (щонайбільше `MAX_RETENTION`, 10 років), `max_messages`, `max_message_characters` |
| `checked_text` | Звичайний непорожній текст у межах ліміту, без NUL і половини символу |
| `Transcript` | Жива розмова та її повідомлення по порядку |

У `src/knowledge_service/conversations.py`:

**1.** Знайдіть:

```python
expires so a later task can delete it without guessing.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
```

Замініть на:

```python
expires so a later task can delete it without guessing.

`ConversationLimits` holds the bounds: how long a Conversation lives, how
many Messages it may hold, and how long one Message may be. These are this
service's own rules, written in its own terms, user and assistant turns of
plain text, whatever shape a chat interface sends. A Message's text stays
out of every repr and every error.
"""

from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
```

**2.** Знайдіть:

```python
    """A Conversation already holds as many Messages as its bound allows."""

```

Замініть на:

```python
    """A Conversation already holds as many Messages as its bound allows."""


class ConversationNotFound(InvalidConversation):
    """No live Conversation with this ID belongs to the caller.

    A missing, another User's, deleted, or expired Conversation all read the
    same, so a caller learns nothing about Conversations it does not own.
    """

```

**3.** Знайдіть:

```python
    role: MessageRole
    text: str
    created_at: datetime
```

Замініть на:

```python
    role: MessageRole
    text: str = field(repr=False)
    created_at: datetime
```

**4.** Знайдіть:

```python
            raise InvalidConversation("retention must be a positive duration")
        return cls(
```

Замініть на:

```python
            raise InvalidConversation("retention must be a positive duration")
        deadline: datetime | None = None
        # Near the end of the calendar, the deadline does not fit in a date.
        with suppress(OverflowError):
            deadline = opened_at + retention
        if deadline is None:
            raise InvalidConversation("the retention deadline is out of range")
        return cls(
```

**5.** Знайдіть:

```python
            last_message_at=opened_at,
            retention_deadline=opened_at + retention,
            deleted_at=None,
```

Замініть на:

```python
            last_message_at=opened_at,
            retention_deadline=deadline,
            deleted_at=None,
```

**6.** Знайдіть:

```python
        return self.deleted_at is None and now >= self.retention_deadline
```

Замініть на:

```python
        return self.deleted_at is None and now >= self.retention_deadline


MAX_RETENTION = timedelta(days=3_650)


@dataclass(frozen=True, slots=True)
class ConversationLimits:
    """How long a Conversation lives, and how much it may hold."""

    retention: timedelta = timedelta(days=30)
    max_messages: int = 100
    max_message_characters: int = 16_000

    def __post_init__(self) -> None:
        if type(self.retention) is not timedelta or not (
            timedelta(0) < self.retention <= MAX_RETENTION
        ):
            raise InvalidConversation("retention must be positive and at most 10 years")
        for name in ("max_messages", "max_message_characters"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise InvalidConversation(f"{name} must be a positive integer")

    def checked_text(self, text: object) -> str:
        """Return `text` if it may be a Message, or raise `InvalidConversation`.

        It must be plain text with a non-space character, within the length
        limit, and storable: valid Unicode without the NUL character, which
        PostgreSQL text cannot hold.
        """
        if type(text) is not str:
            raise InvalidConversation("message text must be text")
        if len(text) > self.max_message_characters:
            raise InvalidConversation("message text is longer than the limit")
        if not text.strip():
            raise InvalidConversation("message text must not be empty")
        if chr(0) in text or not _encodable(text):
            raise InvalidConversation("message text must be valid, storable Unicode")
        return text


@dataclass(frozen=True, slots=True)
class Transcript:
    """A live Conversation and its Messages, in order."""

    conversation: Conversation
    messages: tuple[Message, ...]


def _encodable(text: str) -> bool:
    """True when `text` is valid Unicode: a str can hold a lone surrogate."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True
```

Перевірка:

```bash
uv run pytest tests/test_conversations.py -q
uv run pyright src/knowledge_service/conversations.py
```

Очікувано: наявні тести проходять, а pyright показує 0 помилок.

### Крок 3 — Крок із кодом: сховище з прив'язкою до власника

Цей крок пише чотири функції, через які проходить запит користувача.

**Простими словами:** Початок розмови зберігає її з терміном. Додавання повідомлення спершу перевіряє текст, потім чекає своєї черги на розмові, знаходить наступну позицію і відмовляє, якщо розмова повна, відсутня, не ваша, видалена чи прострочена. Читання повертає розмову та її повідомлення по порядку лише власнику. Видалення одразу позначає її зниклою і просить фонову задачу прибрати її пізніше.

| Функція | Що робить |
|---|---|
| `start_conversation` | Відкриває розмову для власника з терміном з лімітів |
| `add_message` | Перевіряє текст, блокує живий рядок, рахує й нумерує під блокуванням, зберігає повідомлення, рухає `last_message_at` |
| `read_conversation` | Жива розмова власника та її повідомлення, або None |
| `delete_conversation` | Ставить `deleted_at` (ніколи не раніше за останнє повідомлення) і ставить у чергу один Job очищення; True лише для виклику, що видалив |
| `_live` | Один `WHERE`: цей ID, цей власник, не видалена, не прострочена |
| `_utc`, `_owner`, `_id`, `_limits` | Перевірки типів до будь-якого SQL; `now` переводиться в UTC |

У `src/knowledge_service/persistence/conversations.py`:

**1.** Знайдіть:

```python
"""Store Conversations, their Messages, and feedback on them."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast
```

Замініть на:

```python
"""Store Conversations, their Messages, and feedback on them.

The answering flow uses only the owner-scoped functions: `start_conversation`,
`add_message`, `read_conversation`, and `delete_conversation`. Each takes the
caller's `UserId` and puts it in the SQL itself, so another User's
Conversation never leaves the database. They promise:

- **One answer for "not yours".** A Conversation that is missing, another
  User's, deleted, or past its retention deadline reads as None and refuses
  a Message with `ConversationNotFound`, the same in every case.
- **Order from the database.** `add_message` locks the Conversation's row,
  so concurrent Messages take turns; each gets the next ordinal, 0, 1, 2,
  and so on, and the limit is counted under the same lock. Reads take no
  lock, so reading and then writing in one transaction cannot deadlock.
  Run them at READ COMMITTED, the project's default: a stricter isolation
  level turns waiting for a turn into a serialization failure.
- **Deleted at once.** `delete_conversation` sets the deletion time in the
  same transaction that queues a purge Job; from its commit on, the
  Conversation reads as missing, though its rows stay until the purge. The
  deletion time is never earlier than the last Message.
- **Any clock, one zone.** Every `now` must carry a time zone and is turned
  into UTC first, so a deadline is always the same span of real time.
- **Bounded.** `ConversationLimits` bounds the retention, the number of
  Messages, and each Message's length, and refuses text PostgreSQL cannot
  store, before any SQL runs.

The other functions read and write without an owner, for system work such
as retention and purge Jobs; they are never given a User's request.
"""

from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, cast
```

**2.** Знайдіть:

```python
    ConversationFull,
    InvalidConversation,
```

Замініть на:

```python
    ConversationFull,
    ConversationLimits,
    ConversationNotFound,
    InvalidConversation,
```

**3.** Знайдіть:

```python
    MessageRole,
)
from knowledge_service.identifiers import ConversationId, MessageId, UserId
from knowledge_service.persistence.schema import (
```

Замініть на:

```python
    MessageRole,
    Transcript,
)
from knowledge_service.identifiers import ConversationId, JobId, MessageId, UserId
from knowledge_service.jobs import Job, JobKind
from knowledge_service.persistence.jobs import enqueue_job
from knowledge_service.persistence.schema import (
```

**4.** Знайдіть:

```python
    return tuple(_conversation_from(dict(record)) for record in records)
```

Замініть на:

```python
    return tuple(_conversation_from(dict(record)) for record in records)


async def start_conversation(
    connection: AsyncConnection,
    *,
    owner_id: UserId,
    now: datetime,
    limits: ConversationLimits,
) -> Conversation:
    """Open and store a new Conversation for `owner_id`."""
    now = _utc(now)
    conversation = Conversation.open(
        conversation_id=ConversationId.new(),
        owner_id=_owner(owner_id),
        opened_at=now,
        retention=_limits(limits).retention,
    )
    await create_conversation(connection, conversation)
    return conversation


async def add_message(
    connection: AsyncConnection,
    *,
    owner_id: UserId,
    conversation_id: ConversationId,
    role: MessageRole,
    text: str,
    now: datetime,
    limits: ConversationLimits,
) -> Message:
    """Append a Message to the owner's live Conversation, at the next ordinal.

    Raises `InvalidConversation` for bad text or role before any SQL runs,
    `ConversationNotFound` when no live Conversation of this owner has this
    ID, and `ConversationFull` at the limit.
    """
    checked = _limits(limits).checked_text(text)
    if type(role) is not MessageRole:
        raise InvalidConversation("role must be a MessageRole")
    now = _utc(now)
    # Locked, so concurrent Messages take turns for the next ordinal.
    record = (
        (
            await connection.execute(
                sa.select(conversations)
                .where(_live(owner_id, conversation_id, now))
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if record is None:
        raise ConversationNotFound("no such conversation")
    conversation = _conversation_from(dict(record))
    count, last = (
        await connection.execute(
            sa.select(sa.func.count(), sa.func.max(messages.c.ordinal)).where(
                messages.c.conversation_id == conversation_id.value
            )
        )
    ).one()
    if count >= limits.max_messages:
        raise ConversationFull("the conversation holds as many messages as allowed")
    # Clocks of two servers can disagree; time never runs backwards here.
    created_at = max(now, conversation.last_message_at)
    message = Message(
        message_id=MessageId.new(),
        conversation_id=conversation_id,
        ordinal=0 if last is None else last + 1,
        role=role,
        text=checked,
        created_at=created_at,
    )
    await connection.execute(sa.insert(messages).values(_message_row(message)))
    await connection.execute(
        sa.update(conversations)
        .where(conversations.c.conversation_id == conversation_id.value)
        .values(last_message_at=created_at)
    )
    return message


async def read_conversation(
    connection: AsyncConnection,
    *,
    owner_id: UserId,
    conversation_id: ConversationId,
    now: datetime,
) -> Transcript | None:
    """The owner's live Conversation and its Messages in order, or None."""
    now = _utc(now)
    # No lock: a delete only marks the row, so the Messages read next are
    # still there, and a later write in this transaction cannot deadlock.
    record = (
        (
            await connection.execute(
                sa.select(conversations).where(_live(owner_id, conversation_id, now))
            )
        )
        .mappings()
        .one_or_none()
    )
    if record is None:
        return None
    return Transcript(
        conversation=_conversation_from(dict(record)),
        messages=await load_messages(connection, conversation_id=conversation_id),
    )


async def delete_conversation(
    connection: AsyncConnection,
    *,
    owner_id: UserId,
    conversation_id: ConversationId,
    now: datetime,
) -> bool:
    """Delete the owner's Conversation now, and queue its purge.

    Returns True when this call deleted it, also past its deadline, and
    False when there was nothing of this owner's to delete. A caller must
    answer the same either way, so the result never tells a User whether
    another User's Conversation exists.
    """
    now = _utc(now)
    deleted = await connection.scalar(
        sa.update(conversations)
        .where(
            conversations.c.conversation_id == _id(conversation_id),
            conversations.c.owner_id == _owner(owner_id).value,
            conversations.c.deleted_at.is_(None),
        )
        # Never before its last Message, so a slow clock cannot reorder them.
        .values(deleted_at=sa.func.greatest(now, conversations.c.last_message_at))
        .returning(conversations.c.conversation_id)
    )
    if deleted is None:
        return False
    await enqueue_job(
        connection,
        Job.request(
            job_id=JobId.new(),
            kind=JobKind.PURGE_CONVERSATION,
            target_id=conversation_id.value,
            queued_at=now,
        ),
    )
    return True


def _live(
    owner_id: UserId, conversation_id: ConversationId, now: datetime
) -> sa.ColumnElement[bool]:
    """This ID, this owner, not deleted, and not past its deadline."""
    return sa.and_(
        conversations.c.conversation_id == _id(conversation_id),
        conversations.c.owner_id == _owner(owner_id).value,
        conversations.c.deleted_at.is_(None),
        conversations.c.retention_deadline > now,
    )


def _limits(limits: object) -> ConversationLimits:
    if type(limits) is not ConversationLimits:
        raise InvalidConversation("limits must be ConversationLimits")
    return limits


def _owner(owner_id: object) -> UserId:
    if type(owner_id) is not UserId or type(owner_id.value) is not UUID:
        raise InvalidConversation("owner_id must be a UserId")
    return owner_id


def _id(conversation_id: object) -> UUID:
    if (
        type(conversation_id) is not ConversationId
        or type(conversation_id.value) is not UUID
    ):
        raise InvalidConversation("conversation_id must be a ConversationId")
    return conversation_id.value


def _utc(now: object) -> datetime:
    """`now` in UTC, or `InvalidConversation` for a time without a zone."""
    utc: datetime | None = None
    if type(now) is datetime:
        # A hand-made time zone may raise anything, and a shift may overflow.
        with suppress(Exception):
            if now.utcoffset() is not None:
                utc = now.astimezone(UTC)
    if utc is None:
        raise InvalidConversation("now must be a timestamp with a time zone")
    return utc
```

Тут важливі дві деталі. `_live` — єдине місце, що каже, що означає «видима цьому користувачу», і кожне читання та запис ним користуються, тож правило не може розійтися між функціями. А кількість повідомлень читається після того, як взято блокування, новою командою, тож за READ COMMITTED вона бачить кожне повідомлення, закомічене тим, хто тримав блокування раніше.

Перевірка:

```bash
uv run python -c "from knowledge_service.persistence.conversations import add_message, delete_conversation"
uv run pyright src/knowledge_service/persistence/conversations.py
```

Очікувано: імпорт нічого не друкує, а pyright показує 0 помилок.

### Крок 4 — Крок із кодом: тести лімітів, власника, порядку і видалення

Цей крок доводить кожну обіцянку, спершу без бази, а потім на справжньому PostgreSQL.

**Простими словами:** Юніт-тести перевіряють ліміти і те, що погані дані відхиляються до будь-якого SQL, з фейковим з'єднанням, яке падає, якщо його взагалі використають. Інтеграційні тести працюють зі справжньою базою: додають повідомлення з кількох з'єднань одночасно, читають як інший користувач, видаляють і пробують знову, переводять годинник за термін.

| Тест | Що доводить |
|---|---|
| `test_messages_get_the_next_ordinal_and_read_back_in_order` | Успіх: ordinal 0, 1, 2 по порядку |
| `test_concurrent_messages_take_turns_for_the_next_ordinal` | Вісім повідомлень одночасно отримують вісім різних ordinal |
| `test_concurrent_messages_never_pass_the_limit` | Ліміт тримається за конкурентності |
| `test_another_user_can_neither_read_add_nor_delete` | Власник: найризиковіший край |
| `test_missing_foreign_deleted_and_expired_look_the_same` | Одна відповідь, тож нічого не витікає |
| `test_deletion_is_immediate_and_queues_one_purge` | Негайне логічне видалення, один Job очищення |
| `test_a_delete_racing_a_message_leaves_no_message_after_it` | Жодне повідомлення не з'являється після видалення |
| `test_a_conversation_past_its_deadline_reads_as_missing` | Термін зберігання точно в момент дедлайну |
| `test_reading_then_writing_in_one_transaction_cannot_deadlock` | Читання, а потім запис в одній транзакції безпечні |
| `test_text_postgresql_cannot_store_is_refused_quietly` | Типізована відмова, і без тексту в помилці |
| `test_a_bad_message_is_refused_before_any_sql` | Погані дані ніколи не доходять до бази |

Створіть `tests/test_conversation_limits.py`:

```python
"""Conversation limits and the store's checks before any SQL runs."""

from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.conversations import (
    Conversation,
    ConversationLimits,
    InvalidConversation,
    Message,
    MessageRole,
)
from knowledge_service.identifiers import ConversationId, MessageId, UserId
from knowledge_service.persistence.conversations import (
    add_message,
    delete_conversation,
    read_conversation,
    start_conversation,
)

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
OWNER = UserId.parse("00000000-0000-0000-0000-000000000060")
MARKER = "PLANTED-MARKER-7719"


class BrokenZone(tzinfo):
    """A hand-made time zone that fails when asked for its offset."""

    def utcoffset(self, dt: datetime | None) -> timedelta:
        raise RuntimeError("no offset")


class NoDatabase:
    """A connection that fails the test if any SQL is sent."""

    async def execute(self, *args: object, **kwargs: object) -> Any:
        raise AssertionError("no SQL may run")

    async def scalar(self, *args: object, **kwargs: object) -> Any:
        raise AssertionError("no SQL may run")


CONNECTION = cast(AsyncConnection, NoDatabase())


@pytest.mark.parametrize(
    "fields",
    [
        {"retention": timedelta(0)},
        {"retention": timedelta.max},
        {"retention": 30},
        {"max_messages": 0},
        {"max_messages": True},
        {"max_message_characters": 0},
        {"max_message_characters": 1.5},
    ],
)
def test_limits_must_be_positive(fields: dict[str, Any]) -> None:
    with pytest.raises(InvalidConversation):
        ConversationLimits(**fields)


def test_a_message_within_the_limits_is_kept_as_it_is() -> None:
    text = "  Скільки днів відпустки?\n"

    assert ConversationLimits().checked_text(text) is text


@pytest.mark.parametrize(
    "text",
    [
        "",
        " \n\t",
        "x" * 18,
        f"{MARKER}{chr(0)}",
        f"{MARKER}{chr(0xDC00)}",
        7,
        None,
    ],
)
def test_text_that_may_not_be_a_message_is_refused_quietly(text: object) -> None:
    with pytest.raises(InvalidConversation) as caught:
        ConversationLimits(max_message_characters=17).checked_text(text)

    assert MARKER not in str(caught.value)


def test_a_message_repr_hides_its_text() -> None:
    message = Message(
        message_id=MessageId.new(),
        conversation_id=ConversationId.new(),
        ordinal=0,
        role=MessageRole.USER,
        text=MARKER,
        created_at=NOW,
    )

    assert MARKER not in repr(message)


@pytest.mark.parametrize(
    "fields",
    [
        {"text": ""},
        {"text": "x" * 16_001},
        {"role": "user"},
        {"owner_id": "00000000-0000-0000-0000-000000000060"},
        {"conversation_id": "x"},
        {"now": datetime(2026, 11, 16, 9, 0)},  # naive on purpose
        {"now": datetime(2026, 11, 16, 9, 0, tzinfo=BrokenZone())},
        {"limits": None},
    ],
)
async def test_a_bad_message_is_refused_before_any_sql(fields: dict[str, Any]) -> None:
    arguments: dict[str, Any] = {
        "owner_id": OWNER,
        "conversation_id": ConversationId.new(),
        "role": MessageRole.USER,
        "text": "How many days?",
        "now": NOW,
        "limits": ConversationLimits(),
        **fields,
    }

    with pytest.raises(InvalidConversation):
        await add_message(CONNECTION, **arguments)


async def test_other_store_calls_check_their_arguments_before_any_sql() -> None:
    with pytest.raises(InvalidConversation):
        await start_conversation(
            CONNECTION, owner_id=OWNER, now=NOW, limits=cast(Any, None)
        )
    with pytest.raises(InvalidConversation):
        await read_conversation(
            CONNECTION,
            owner_id=OWNER,
            conversation_id=ConversationId.new(),
            now=cast(Any, "now"),
        )
    with pytest.raises(InvalidConversation):
        await delete_conversation(
            CONNECTION,
            owner_id=cast(Any, None),
            conversation_id=ConversationId.new(),
            now=NOW,
        )


def test_a_deadline_past_the_calendar_is_a_typed_refusal() -> None:
    with pytest.raises(InvalidConversation):
        Conversation.open(
            conversation_id=ConversationId.new(),
            owner_id=OWNER,
            opened_at=datetime(9999, 12, 31, tzinfo=UTC),
            retention=timedelta(days=30),
        )
```

Створіть `tests/test_conversation_store_integration.py`:

```python
"""Owner-scoped Conversations in real PostgreSQL: ownership, order, deletion."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from knowledge_service.conversations import (
    ConversationFull,
    ConversationLimits,
    ConversationNotFound,
    InvalidConversation,
    Message,
    MessageRole,
    Transcript,
)
from knowledge_service.identifiers import ConversationId, UserId
from knowledge_service.persistence.conversations import (
    add_message,
    delete_conversation,
    load_conversation,
    load_messages,
    read_conversation,
    start_conversation,
)
from knowledge_service.persistence.schema import jobs

pytestmark = pytest.mark.integration

NOW = datetime(2026, 1, 16, 9, 0, tzinfo=UTC)
OWNER = UserId.parse("00000000-0000-0000-0000-000000000060")
STRANGER = UserId.parse("00000000-0000-0000-0000-000000000061")
LIMITS = ConversationLimits(retention=timedelta(days=30), max_messages=10)
MARKER = "PLANTED-MARKER-7719"


@pytest.fixture
async def engine(migrated_database: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        make_url(migrated_database).set(drivername="postgresql+psycopg")
    )
    try:
        yield engine
    finally:
        await engine.dispose()


async def start(engine: AsyncEngine, owner: UserId = OWNER) -> ConversationId:
    async with engine.begin() as connection:
        conversation = await start_conversation(
            connection, owner_id=owner, now=NOW, limits=LIMITS
        )
    return conversation.conversation_id


async def add(
    engine: AsyncEngine,
    conversation_id: ConversationId,
    text: str = "How many days of leave do I get?",
    *,
    owner: UserId = OWNER,
    role: MessageRole = MessageRole.USER,
    now: datetime = NOW,
    limits: ConversationLimits = LIMITS,
) -> Message:
    async with engine.begin() as connection:
        return await add_message(
            connection,
            owner_id=owner,
            conversation_id=conversation_id,
            role=role,
            text=text,
            now=now,
            limits=limits,
        )


async def read(
    engine: AsyncEngine,
    conversation_id: ConversationId,
    *,
    owner: UserId = OWNER,
    now: datetime = NOW,
) -> Transcript | None:
    async with engine.begin() as connection:
        return await read_conversation(
            connection, owner_id=owner, conversation_id=conversation_id, now=now
        )


async def delete(
    engine: AsyncEngine,
    conversation_id: ConversationId,
    *,
    owner: UserId = OWNER,
    now: datetime = NOW,
) -> bool:
    async with engine.begin() as connection:
        return await delete_conversation(
            connection, owner_id=owner, conversation_id=conversation_id, now=now
        )


async def purge_jobs(engine: AsyncEngine, conversation_id: ConversationId) -> int:
    async with engine.begin() as connection:
        return int(
            await connection.scalar(
                sa.select(sa.func.count())
                .select_from(jobs)
                .where(
                    jobs.c.kind == "purge_conversation",
                    jobs.c.target_id == conversation_id.value,
                )
            )
            or 0
        )


# --- Order ------------------------------------------------------------------------


async def test_messages_get_the_next_ordinal_and_read_back_in_order(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    await add(engine, conversation_id, "How many days of leave do I get?")
    await add(
        engine,
        conversation_id,
        "You get 24 days.",
        role=MessageRole.ASSISTANT,
        now=NOW + timedelta(seconds=5),
    )
    await add(
        engine, conversation_id, "And carry over?", now=NOW + timedelta(minutes=1)
    )

    transcript = await read(engine, conversation_id, now=NOW + timedelta(minutes=2))

    assert transcript is not None
    assert [m.ordinal for m in transcript.messages] == [0, 1, 2]
    assert [m.role for m in transcript.messages] == [
        MessageRole.USER,
        MessageRole.ASSISTANT,
        MessageRole.USER,
    ]
    assert transcript.conversation.last_message_at == NOW + timedelta(minutes=1)


async def test_concurrent_messages_take_turns_for_the_next_ordinal(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)

    added = await asyncio.gather(
        *(add(engine, conversation_id, f"question {n}") for n in range(8))
    )

    assert sorted(m.ordinal for m in added) == list(range(8))


async def test_concurrent_messages_never_pass_the_limit(engine: AsyncEngine) -> None:
    conversation_id = await start(engine)
    small = ConversationLimits(max_messages=3)

    results = await asyncio.gather(
        *(add(engine, conversation_id, f"q {n}", limits=small) for n in range(8)),
        return_exceptions=True,
    )

    assert sum(isinstance(r, Message) for r in results) == 3
    assert sum(isinstance(r, ConversationFull) for r in results) == 5


async def test_a_clock_running_backwards_never_moves_time_back(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    first = await add(engine, conversation_id, now=NOW + timedelta(minutes=5))

    second = await add(engine, conversation_id, now=NOW + timedelta(minutes=1))

    assert second.ordinal == 1
    assert second.created_at == first.created_at


# --- Ownership ----------------------------------------------------------------------


async def test_another_user_can_neither_read_add_nor_delete(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    await add(engine, conversation_id)

    assert await read(engine, conversation_id, owner=STRANGER) is None
    with pytest.raises(ConversationNotFound):
        await add(engine, conversation_id, owner=STRANGER)
    assert await delete(engine, conversation_id, owner=STRANGER) is False

    transcript = await read(engine, conversation_id)
    assert transcript is not None
    assert len(transcript.messages) == 1
    assert await purge_jobs(engine, conversation_id) == 0


async def test_missing_foreign_deleted_and_expired_look_the_same(
    engine: AsyncEngine,
) -> None:
    missing = ConversationId.new()
    foreign = await start(engine, STRANGER)
    deleted = await start(engine)
    await delete(engine, deleted)
    expired = await start(engine)
    # Each one is checked while only its own reason holds.
    cases = [
        (missing, NOW),
        (foreign, NOW),
        (deleted, NOW),
        (expired, NOW + LIMITS.retention),
    ]

    refusals: list[str] = []
    for conversation_id, now in cases:
        assert await read(engine, conversation_id, now=now) is None
        with pytest.raises(ConversationNotFound) as caught:
            await add(engine, conversation_id, now=now)
        refusals.append(str(caught.value))

    assert len(set(refusals)) == 1


# --- Deletion and retention ---------------------------------------------------------


async def test_deletion_is_immediate_and_queues_one_purge(engine: AsyncEngine) -> None:
    conversation_id = await start(engine)
    await add(engine, conversation_id)

    assert await delete(engine, conversation_id, now=NOW + timedelta(seconds=1))
    assert await read(engine, conversation_id) is None
    with pytest.raises(ConversationNotFound):
        await add(engine, conversation_id)
    assert await delete(engine, conversation_id) is False
    assert await purge_jobs(engine, conversation_id) == 1
    # Logical: the rows stay for the purge Job to remove.
    async with engine.begin() as connection:
        assert (
            len(await load_messages(connection, conversation_id=conversation_id)) == 1
        )


async def test_a_delete_racing_a_message_leaves_no_message_after_it(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)

    results = await asyncio.gather(
        *(add(engine, conversation_id, f"q {n}") for n in range(4)),
        delete(engine, conversation_id),
        return_exceptions=True,
    )

    added = [r for r in results if isinstance(r, Message)]
    assert results[-1] is True
    assert all(isinstance(r, (Message, ConversationNotFound)) for r in results[:-1])
    async with engine.begin() as connection:
        stored = await load_messages(connection, conversation_id=conversation_id)
    assert len(stored) == len(added)


async def test_a_conversation_past_its_deadline_reads_as_missing(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    await add(engine, conversation_id)
    deadline = NOW + LIMITS.retention

    assert await read(engine, conversation_id, now=deadline - timedelta(seconds=1))
    assert await read(engine, conversation_id, now=deadline) is None
    with pytest.raises(ConversationNotFound):
        await add(engine, conversation_id, now=deadline)
    # Its owner may still delete it before the retention sweep does.
    assert await delete(engine, conversation_id, now=deadline) is True


async def test_a_full_conversation_refuses_the_next_message(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    two = ConversationLimits(max_messages=2)
    await add(engine, conversation_id, limits=two)
    await add(
        engine, conversation_id, "24 days.", role=MessageRole.ASSISTANT, limits=two
    )

    with pytest.raises(ConversationFull):
        await add(engine, conversation_id, limits=two)


@pytest.mark.parametrize(
    "text",
    [f"{MARKER}{chr(0)}", f"{MARKER}{chr(0xD800)}", MARKER * 2_000, "   "],
)
async def test_text_postgresql_cannot_store_is_refused_quietly(
    engine: AsyncEngine, text: str
) -> None:
    conversation_id = await start(engine)

    with pytest.raises(InvalidConversation) as caught:
        await add(engine, conversation_id, text)

    assert MARKER not in str(caught.value)
    assert caught.value.__context__ is None
    transcript = await read(engine, conversation_id)
    assert transcript is not None
    assert transcript.messages == ()


# --- Review regressions -------------------------------------------------------------


async def test_reading_then_writing_in_one_transaction_cannot_deadlock(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    both_read = asyncio.Barrier(2)

    async def read_then_add(text: str) -> Message:
        async with engine.begin() as connection:
            await read_conversation(
                connection, owner_id=OWNER, conversation_id=conversation_id, now=NOW
            )
            await both_read.wait()
            return await add_message(
                connection,
                owner_id=OWNER,
                conversation_id=conversation_id,
                role=MessageRole.USER,
                text=text,
                now=NOW,
                limits=LIMITS,
            )

    added = await asyncio.gather(read_then_add("first"), read_then_add("second"))

    assert sorted(m.ordinal for m in added) == [0, 1]


async def test_reading_then_deleting_in_one_transaction_cannot_deadlock(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    both_read = asyncio.Barrier(2)

    async def read_then_delete() -> bool:
        async with engine.begin() as connection:
            await read_conversation(
                connection, owner_id=OWNER, conversation_id=conversation_id, now=NOW
            )
            await both_read.wait()
            return await delete_conversation(
                connection, owner_id=OWNER, conversation_id=conversation_id, now=NOW
            )

    results = await asyncio.gather(read_then_delete(), read_then_delete())

    assert sorted(results) == [False, True]
    assert await purge_jobs(engine, conversation_id) == 1


async def test_a_deletion_is_never_earlier_than_the_last_message(
    engine: AsyncEngine,
) -> None:
    conversation_id = await start(engine)
    last = await add(engine, conversation_id, now=NOW + timedelta(seconds=13))

    await delete(engine, conversation_id, now=NOW)

    async with engine.begin() as connection:
        stored = await load_conversation(connection, conversation_id=conversation_id)
    assert stored is not None
    assert stored.deleted_at == last.created_at


@pytest.mark.parametrize(
    "zone",
    [ZoneInfo("Europe/London"), timezone(timedelta(hours=2))],
)
async def test_any_time_zone_gives_the_same_deadline_in_utc(
    engine: AsyncEngine, zone: tzinfo
) -> None:
    # Winter in London, so the 180 days run into summer time.
    local = NOW.astimezone(zone)
    limits = ConversationLimits(retention=timedelta(days=180))
    async with engine.begin() as connection:
        conversation = await start_conversation(
            connection, owner_id=OWNER, now=local, limits=limits
        )

    assert conversation.created_at == NOW
    assert conversation.created_at.tzinfo is UTC
    assert conversation.retention_deadline == NOW + timedelta(days=180)
```

Перевірка:

```bash
uv run pytest tests/test_conversation_limits.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_conversation_store_integration.py -m integration -q
```

Очікувано: 26 passed, потім 19 passed.

### Крок 5 — Без коду: зламайте і вирішіть

Цей крок показує, що робить блокування рядка, і фіксує, чого ви навчилися.

**Дія 1:** навмисно зламайте порядок. У `src/knowledge_service/persistence/conversations.py`, в `add_message`, видаліть цей рядок:

```python
                .with_for_update()
```

Запустіть:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_conversation_store_integration.py -m integration -q
```

Очікувано: 4 failed, 15 passed. Без блокування два повідомлення читають той самий найбільший ordinal, і унікальне правило бази відхиляє друге нетипізованою `IntegrityError`: вісім повідомлень одночасно падають, під ліміт уміщаються лише 2 з 3 повідомлень, гонка з видаленням падає, а читання з подальшим записом стикаються. Унікальне правило з M02 і далі береже дані правильними, але той, хто викликає, отримує падіння замість своєї черги. Поверніть рядок і запускайте знову, доки все не пройде.

**Дія 2:** під `## Decision` напишіть:

- чому власника перевіряють усередині SQL, а не після завантаження;
- чому відсутня, чужа, видалена і прострочена дають одну відповідь;
- чому ordinal обирає база під блокуванням;
- чому видалення позначає рядок і ставить у чергу очищення, а не прибирає рядки одразу;
- що лишається на потім: виконавець очищення (M08), побудова історії з Transcript (M06-T11);
- щонайменше одне **спростоване припущення** у формі «Я припускав X. Запуск чи рев'ю показує Y. Отже, Z хибне».

### Крок 6 — Без коду: перевірки, evidence і прогрес

Цей крок доводить, що все досі працює, і фіксує результат. Жодна діаграма чи рішення не стали хибними: таблиці та їхні правила не змінилися, тож `docs/architecture.md` лишається як є.

**Дія 1:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_conversation_limits.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний запуск дав 2196 passed для `make test-unit`, на 26 більше, ніж у M06-T09, і 361 passed для `make test-integration`, на 19 більше. Якщо він каже, що тестової бази немає, запустіть її командою `docker start knowledge-service-postgres`.

**Дія 2:** заповніть файл evidence:

- **Design:** змінені файли; функції з прив'язкою до власника і спільний `WHERE`; як видаються ordinal; як працює видалення; ліміти; час в UTC.
- **Verification:** команди й кількості, навмисний злам і відомі обмеження з «Перед початком».
- **Review:** змагальне рев'ю не знайшло витоку чужих розмов і жодного пропуску чи дубля в ordinal, і знайшло п'ять проблем, кожну виправлено з тестом, що падав до виправлення (8 тестів), крім рівня ізоляції, який тепер задокументовано: читання зі спільним блокуванням і потім запис давали взаємоблокування; термін за кінцем календаря кидав нетипізовану помилку; пояс не UTC зсував термін на годину; видалення могло бути раніше за останнє повідомлення; суворіший рівень ізоляції перетворював очікування на помилки серіалізації.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу виконаною, додайте рядок M06-T10 у `docs/curriculum/PROGRESS.md`, поставте наступною задачу M06-T11 і зробіть коміт з `m06-t10: persist bounded conversations`.

## Запитання для рефлексії

1. Що сховище з прив'язкою до власника приховує від коду, який відповідатиме на уточнювальне запитання? Перелічіть, чого тому коду ніколи не треба перевіряти самому.
2. Пізніша зміна хоче прискорити `read_conversation` кешем за ID розмови. Яку обіцянку кеш може зламати і як тест це покаже?
3. Чому безпечніше, щоб ordinal обирав `add_message`, а не той, хто викликає? Опишіть конкретний баг, який дозволяє старий спосіб.
4. Користувач видаляє розмову, поки його браузер ще надсилає в неї запитання. Що бачить кожен запит і в якому порядку вони можуть закінчитися?
5. Який випадок ви додали б наступним, щоб спробувати зламати власника чи порядок, і який результат очікуєте?

## Чекліст завершення

- [ ] Файл evidence містить інваріант і ваші передбачення, записані до коду.
- [ ] `ConversationLimits` обмежує термін, кількість повідомлень і довжину одного, і відхиляє текст, який не можна зберегти.
- [ ] Кожна функція з прив'язкою до власника кладе власника в SQL.
- [ ] Відсутня, чужа, видалена і прострочена дають ту саму відповідь.
- [ ] Конкурентні повідомлення отримують різні ordinal без пропусків і ніколи не перевищують ліміт.
- [ ] Видалення видно одразу, і воно ставить у чергу рівно один Job очищення.
- [ ] Читання, а потім запис в одній транзакції не можуть заблокувати одне одного.
- [ ] `now` переводиться в UTC; час ніколи не йде назад.
- [ ] Жоден текст повідомлення не потрапляє в помилку чи repr.
- [ ] Ви запустили навмисний злам, побачили, що тести падають, і скасували його.
- [ ] Усі перевірки проходять: форматування, лінтер, типи, юніт, інтеграційні, документація.
- [ ] Evidence, статус задачі, рядок PROGRESS і коміт готові.

Коли напишете файли, поділіться ними чи виводом помилок, і я перевірю їх за цим чеклістом.
