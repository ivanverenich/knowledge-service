# M06-T11 — Побудуйте обмежену історію

Англійська версія: [M06-T11-construct-bounded-history.md](M06-T11-construct-bounded-history.md)

Вихідне завдання: [M06-T11](../curriculum/milestones/M06-answers-and-conversations/M06-T11-construct-bounded-history.md)

## Що ви зробите

Цей урок будує історію (History), потрібну уточнювальному запитанню: попередні ходи власної розмови читача, що вміщаються в бюджет токенів. «А для підрядників?» саме по собі нічого не означає; з попереднім ходом — означає. Історія також має бути безпечною: стара відповідь — це текст, написаний з Документів, тож якщо читач відтоді втратив доступ до одного з них, ця відповідь не має знову потрапити до моделі.

- Міграція дає кожному повідомленню асистента запам'ятати, чим воно закінчилося і які Chunks процитувало.
- `add_turn` зберігає запитання і відповідь на нього разом.
- `build_history` вкладає ходи в бюджет: останні цілком, старіші — їхніми запитаннями.
- `load_history` читає розмову читача і залишає лише ті відповіді, чиї Chunks він ще може бачити.

| Завдання вимагає | Де це відбувається |
|---|---|
| Останні та підсумовані ходи в межах бюджету токенів | Крок 5: `build_history` залишає останні ходи цілком, потім старіші запитання, в межах `history_tokens` |
| Виключити невдалий вміст | Крок 5: показуються лише відповіді `answered`; невдалі, утримання і старі приховуються |
| Виключити недозволений вміст | Крок 5: `load_history` перевіряє кожен процитований Chunk через `eligible_chunks_predicate` |
| Зберегти посилання для уточнень | Кроки 2–4: процитовані Chunks відповіді зберігаються; кожен залишений хід їх несе |
| Тести: довгі розмови, зміна моделі, видалення, відкликання доступу | Крок 6: `tests/test_history.py` і `tests/test_history_integration.py` |
| Авторизація до того, як дані залишають сховище | Крок 5: розмова читається з прив'язкою до власника, а видимість Chunks вирішується в SQL |
| Жодного підкладеного тексту в телеметрії | Крок 6: підкладений маркер ніколи не потрапляє в запис логу чи repr |

Цей урок містить код: кроки 2–6 пишуть код, а крок 2 додає міграцію бази. Кроки 1, 7 і 8 — без коду. Інтеграційним тестам потрібна тестова база.

Терміни цього уроку:

- **Історія (History):** попередні ходи розмови, що надсилаються разом із новим запитанням, щоб модель могла стежити за розмовою.
- **Хід (turn):** одне запитання і відповідь на нього.
- **Підсумований хід (summarized turn):** тут — старіший хід, представлений лише своїм запитанням. Це навмисне дешеве підсумовування: уточненню здебільшого треба знати, про що питали раніше, а не кожне слово кожної відповіді.
- **Прихована (withheld):** відповідь, текст якої історія не включає, хоча її запитання лишається.
- **Посилання (reference):** один Chunk, який процитувала відповідь, з його Документом і версією вмісту; зберігається, щоб історія пізніше могла перевірити, чи цей Chunk ще актуальний і видимий.

## Перед початком

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілий, коли ви до нього дійдете.

- **Що вже є.** M06-T10 зберігає розмови й повідомлення з прив'язкою до власника через `read_conversation`. `ContextBudget` з M06-T03 резервує `history_tokens` і каже «M06-T11 заповнює це». `confirm_citations` з M06-T05 уже перевіряє процитовані Chunks за правами читача одним SQL-запитом.
- **Повідомлення має пам'ятати більше.** Щоб пропустити невдалу відповідь, історія має знати, що вона невдала; щоб пропустити відповідь з відкликаного Документа, вона має знати, що ця відповідь цитувала. Тож `messages` отримує колонку `outcome`, а нова таблиця `message_references` зберігає кожен процитований Chunk з його Документом і версією. Зовнішнього ключа на `chunks` навмисно немає: коли Chunk видаляють, посилання має лишитися, щоб історія побачила, що його вже немає.
- **Старіші повідомлення асистента.** Повідомлення, збережені до цієї міграції, не мають результату. Історія не може перевірити, з чого їх написано, тож приховує їх.
- **Чий вміст перевіряється.** Запитання — власні слова користувача і завжди лишаються. Відповідь показується, лише якщо вона `answered` і кожен процитований нею Chunk досі актуальний, у тому самому Документі й версії, і видимий цьому читачу зараз. Перевірка та сама, що в пошуку, і виконується в SQL.
- **Відповідь може повторити попередню.** Кожну відповідь написано з попередніми ходами в її історії, тож вона може повторити сказане попередньою. Якщо попередню відповідь приховано, бо Документ став прихованим, приховується і кожна відповідь після неї. Це обережно: один відкликаний Документ на початку розмови приховує відповіді після нього, але ніколи не показує прихований вміст.
- **Останні цілком, старіші підсумовано.** Історія заповнюється від найновішого ходу назад: цілі ходи, поки вміщаються, потім лише запитання, поки вміщаються, потім нічого. Залишене — це завжди найновіший відрізок, без пропусків.
- **Токени.** Рахуються як у бюджеті контексту: UTF-8 байти плюс невелика обгортка на кожен текст, верхня межа для байтових токенайзерів, як в OpenAI. Модель з меншим бюджетом отримує коротший відрізок тієї самої розмови; історія, побудована для одного бюджету, ніколи його не перевищує, хоч би яка модель писала старі відповіді.
- **Хід зберігається разом.** `add_turn` зберігає запитання і відповідь в одній транзакції, коли відповідь уже відома. Блокування рядка з M06-T10 тримається до коміту, тож хід з іншої вкладки не може вклинитися між ними, і історія може поєднати кожну відповідь із запитанням безпосередньо перед нею. Savepoint забирає запитання назад, якщо відповідь не вдалося зберегти.
- **Що знайшло рев'ю.** Змагальне рев'ю атакувало історію підробленими й застарілими посиланнями, відкликаними групами, старими рядками, міграціями з наявними рядками, бюджетами і ворожими даними на справжньому PostgreSQL. Підроблені посилання, неправильні версії, видалені Chunks і частково приховані відповіді — усе приховувалося, а історія ніколи не перевищувала бюджет. Воно знайшло такі проблеми, кожну виправлено з тестом, що падає без виправлення:
  - Пізніша відповідь, що повторювала відкликану, все одно потрапляла в історію, бо перевірялися лише її власні Chunks. Тепер відповідь приховується, коли прихована будь-яка попередня.
  - Дві вкладки могли зберегти свої запитання і відповіді впереміш, і історія поєднувала відповідь не з тим запитанням. Тепер `add_turn` зберігає хід разом, а відповідь поєднується лише з запитанням безпосередньо перед нею.
  - Версія вмісту понад межу цілого PostgreSQL доходила до бази і падала там нетипізовано. Тепер `MessageReference` її відхиляє.
  - Рядок, записаний в обхід сховища, наприклад відповідь `answered` без посилань, робить свою розмову нечитабельною. Це задокументовано: сховище — єдиний, хто пише.
- **Де живе код.** Нова міграція; `conversations.py`, `persistence/schema.py` і `persistence/conversations.py`; новий `answering/history.py`.
- **Відомі обмеження.** Приховувати пізніші відповіді після відкликаної — обережно, а не точно; точно було б зберігати, які попередні відповіді бачила кожна відповідь. Правила, які перевіряє повідомлення, живуть у Python, тож рядок, записаний в обхід сховища, може зробити свою розмову нечитабельною. `load_history` читає приховані відповіді власника в пам'ять, перш ніж їх пропустити; це власні повідомлення власника. `add_turn` і `load_history` поки ніхто не викликає; це зробить потік уточнень з M06-T12.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої передбачення

Поки коду ще немає, запишіть, що має бути правдою наприкінці уроку. Крок 7 перевіряє передбачення.

Створіть `docs/evidence/M06-T11-construct-bounded-history.md`:

```markdown
# M06-T11 — Construct bounded History evidence

## Invariant

`load_history` returns None for a Conversation that is not the reader's
live Conversation. Otherwise it returns the most recent stretch of turns
that fits `budget_tokens`: whole turns first, then older turns as their
Questions alone, with no gaps and never over the budget. An Answer's text
is included only when it was `answered` and every Chunk it and every
earlier Answer cited is current, in the same Document and version, and
visible to the reader now under `eligible_chunks_predicate`; otherwise it
is withheld and its Question stays. Each kept Answer carries the Chunks
it cited. `add_turn` stores a Question and its reply together, so a reply
always follows its own Question. Records and reprs hold no Question or
Answer text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.history"`
fails with `ModuleNotFoundError`.

## Predictions

- What History holds for a Conversation of 50 long turns with a budget for only a few: ...
- What happens to an earlier Answer, and to the Answers after it, when the reader loses access to a Document the earlier Answer cited: ...
- How many integration tests fail if `load_history` stops checking whether the reader may see each cited Chunk, and why not all of the "changed Document" cases: ...

## Design

## Verification

## Decision

## Reflection
```

Перевірка: файл існує, перевірка, що має впасти, падає, а кожне передбачення — повне речення, яке крок 7 може спростувати.

### Крок 2 — Крок із кодом: зберігайте результати й посилання

Цей крок додає в базу дві речі, потрібні історії.

**Простими словами:** Кожне повідомлення асистента отримує поле, яке каже, чи воно відповіло, утрималося чи не вдалося. Нова таблиця перелічує для кожної відповіді процитовані нею Chunks з Документом і версією кожного. Міграція додає обидва, а визначення таблиць у Python кажуть те саме, тож тест може перевірити, що вони збігаються.

| Частина | Що містить |
|---|---|
| `messages.outcome` | `answered`, `abstained`, `failed` або нічого; лише повідомлення асистента може його мати |
| `message_references` | Один рядок на процитований Chunk: позиція, Chunk, Документ і версія; видаляється разом із повідомленням |

Створіть `migrations/versions/a9b0c1d2e3f4_message_outcomes_and_references.py`:

```python
"""message outcomes and references

Revision ID: a9b0c1d2e3f4
Revises: d8e9f0a1b2c3
Create Date: 2026-11-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "d8e9f0a1b2c3"


def upgrade() -> None:
    """Remember how each Answer ended and which Chunks it cited.

    Existing assistant Messages keep no outcome; History treats them as
    unverified and leaves their text out.
    """
    op.add_column("messages", sa.Column("outcome", sa.String(length=16), nullable=True))
    op.create_check_constraint(
        "ck_messages_outcome",
        "messages",
        "outcome is null or (role = 'assistant'"
        " and outcome in ('answered', 'abstained', 'failed'))",
    )
    op.create_table(
        "message_references",
        sa.Column(
            "message_id",
            sa.Uuid(),
            sa.ForeignKey("messages.message_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "message_id", "chunk_id", name="uq_message_references_chunk"
        ),
        sa.CheckConstraint("position >= 0", name="ck_message_references_position"),
        sa.CheckConstraint(
            "content_version >= 1", name="ck_message_references_content_version"
        ),
    )


def downgrade() -> None:
    """Forget Message outcomes and references."""
    op.drop_table("message_references")
    op.drop_constraint("ck_messages_outcome", "messages", type_="check")
    op.drop_column("messages", "outcome")
```

У `src/knowledge_service/persistence/schema.py`:

Знайдіть:

```python
        nullable=False,
    ),
    sa.Column("ordinal", sa.Integer(), nullable=False),
    sa.Column("role", sa.String(length=16), nullable=False),
    sa.Column("text", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "conversation_id", "ordinal", name="uq_messages_conversation_ordinal"
    ),
    sa.CheckConstraint("ordinal >= 0", name="ck_messages_ordinal"),
    sa.CheckConstraint("role in ('user', 'assistant')", name="ck_messages_role"),
)

message_feedback = sa.Table(
    "message_feedback",
    metadata,
    sa.Column(
```

Замініть на:

```python
        nullable=False,
    ),
    sa.Column("ordinal", sa.Integer(), nullable=False),
    sa.Column("role", sa.String(length=16), nullable=False),
    sa.Column("text", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("outcome", sa.String(length=16), nullable=True),
    sa.UniqueConstraint(
        "conversation_id", "ordinal", name="uq_messages_conversation_ordinal"
    ),
    sa.CheckConstraint("ordinal >= 0", name="ck_messages_ordinal"),
    sa.CheckConstraint("role in ('user', 'assistant')", name="ck_messages_role"),
    sa.CheckConstraint(
        "outcome is null or (role = 'assistant'"
        " and outcome in ('answered', 'abstained', 'failed'))",
        name="ck_messages_outcome",
    ),
)

message_references = sa.Table(
    "message_references",
    metadata,
    sa.Column(
        "message_id",
        sa.Uuid(),
        sa.ForeignKey("messages.message_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("position", sa.Integer(), primary_key=True),
    sa.Column("chunk_id", sa.Uuid(), nullable=False),
    sa.Column("document_id", sa.Uuid(), nullable=False),
    sa.Column("content_version", sa.Integer(), nullable=False),
    sa.UniqueConstraint("message_id", "chunk_id", name="uq_message_references_chunk"),
    sa.CheckConstraint("position >= 0", name="ck_message_references_position"),
    sa.CheckConstraint(
        "content_version >= 1", name="ck_message_references_content_version"
    ),
)

message_feedback = sa.Table(
    "message_feedback",
    metadata,
    sa.Column(
```

Перевірка:

```bash
uv run alembic heads
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_migrations_integration.py -m integration -q
```

Очікувано: `a9b0c1d2e3f4 (head)`, і тести міграцій проходять: мігрована схема збігається з оголошеними таблицями, а міграції йдуть вниз і знову вгору.

### Крок 3 — Крок із кодом: результати й посилання повідомлення

Цей крок пояснює домену, що може нести відповідь.

**Простими словами:** Повідомлення тепер може сказати, чим закінчилося і які Chunks процитувало. Правила суворі: лише повідомлення асистента має результат, лише відповідь цитує Chunks, щонайменше один і щонайбільше двадцять, кожен раз, і кожне посилання називає справжній Chunk, його Документ і версію, яку база може зберегти.

| Частина | Що містить чи перевіряє |
|---|---|
| `MessageOutcome` | `ANSWERED`, `ABSTAINED`, `FAILED` |
| `MessageReference` | Chunk, його Документ і версія вмісту, яку він читав; щонайбільше `MAX_STORED_VERSION` |
| `Message.outcome`, `Message.references` | Перевіряються разом: відповідь, і лише відповідь, цитує від 1 до 20 різних Chunks |

У `src/knowledge_service/conversations.py`:

**1.** Знайдіть:

```python
"""

from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

from knowledge_service.identifiers import ConversationId, MessageId, UserId


class InvalidConversation(ValueError):
    """A Conversation, Message, or feedback value violates a domain invariant."""


```

Замініть на:

```python
"""

from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import (
    ChunkId,
    ConversationId,
    DocumentId,
    MessageId,
    UserId,
)


class InvalidConversation(ValueError):
    """A Conversation, Message, or feedback value violates a domain invariant."""


```

**2.** Знайдіть:

```python
    """Who produced a Message."""

    USER = "user"
    ASSISTANT = "assistant"


class MessageRating(StrEnum):
    """The thumbs signal a User attaches to one Message."""

    UP = "up"
    DOWN = "down"

```

Замініть на:

```python
    """Who produced a Message."""

    USER = "user"
    ASSISTANT = "assistant"


class MessageOutcome(StrEnum):
    """How an assistant Message ended; only an Answer may feed History."""

    ANSWERED = "answered"
    ABSTAINED = "abstained"
    FAILED = "failed"


# As many as an Answer can cite (`answering.evidence.MAX_EVIDENCE`).
MAX_MESSAGE_REFERENCES = 20
MAX_STORED_VERSION = 2**31 - 1


@dataclass(frozen=True, slots=True)
class MessageReference:
    """A Chunk an Answer cited, in the version it read."""

    chunk_id: ChunkId
    document_id: DocumentId
    content_version: ContentVersion

    def __post_init__(self) -> None:
        identifiers = ((self.chunk_id, ChunkId), (self.document_id, DocumentId))
        if (
            not all(
                type(value) is kind and type(value.value) is UUID
                for value, kind in identifiers
            )
            or type(self.content_version) is not ContentVersion
            # The column is a PostgreSQL integer.
            or self.content_version.number > MAX_STORED_VERSION
        ):
            raise InvalidConversation(
                "a reference names a Chunk, its Document, and version"
            )


class MessageRating(StrEnum):
    """The thumbs signal a User attaches to one Message."""

    UP = "up"
    DOWN = "down"

```

**3.** Знайдіть:

```python
    message_id: MessageId
    conversation_id: ConversationId
    ordinal: int
    role: MessageRole
    text: str = field(repr=False)
    created_at: datetime

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise InvalidConversation("message ordinal must be a non-negative integer")
        if not self.text.strip():
            raise InvalidConversation("message text must not be empty")
        _require_utc(self.created_at, "message created_at")


@dataclass(frozen=True, slots=True)
class MessageFeedback:
    """One User's current rating of one Message.

```

Замініть на:

```python
    message_id: MessageId
    conversation_id: ConversationId
    ordinal: int
    role: MessageRole
    text: str = field(repr=False)
    created_at: datetime
    # An assistant Message stored before outcomes existed has none.
    outcome: MessageOutcome | None = None
    references: tuple[MessageReference, ...] = ()

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise InvalidConversation("message ordinal must be a non-negative integer")
        if not self.text.strip():
            raise InvalidConversation("message text must not be empty")
        _require_utc(self.created_at, "message created_at")
        if self.outcome is not None and (
            type(self.outcome) is not MessageOutcome
            or self.role is not MessageRole.ASSISTANT
        ):
            raise InvalidConversation("only an assistant Message has an outcome")
        references = self.references
        if type(references) is not tuple or not all(
            type(reference) is MessageReference for reference in references
        ):
            raise InvalidConversation("references must be a tuple of MessageReference")
        answered = self.outcome is MessageOutcome.ANSWERED
        if answered != bool(references):
            raise InvalidConversation("an Answer, and only an Answer, cites Chunks")
        if len(references) > MAX_MESSAGE_REFERENCES:
            raise InvalidConversation("an Answer cites too many Chunks")
        if len({reference.chunk_id for reference in references}) != len(references):
            raise InvalidConversation("an Answer cites a Chunk once")


@dataclass(frozen=True, slots=True)
class MessageFeedback:
    """One User's current rating of one Message.

```

Перевірка:

```bash
uv run pytest tests/test_conversations.py tests/test_conversation_limits.py -q
```

Очікувано: наявні тести проходять, бо обидва нові поля типово «порожні».

### Крок 4 — Крок із кодом: зберігайте і читайте їх, і зберігайте хід разом

Цей крок пише результати й посилання разом із повідомленням, читає їх назад і додає `add_turn`.

**Простими словами:** Додавання відповіді тепер також зберігає, чим вона закінчилася, і процитовані нею Chunks, у тій самій транзакції. Читання розмови повертає їх із кожним повідомленням. Нова функція зберігає запитання і відповідь разом, коли відповідь уже відома, тож жодне інше повідомлення не може опинитися між ними; якщо відповідь не вдалося зберегти, запитання теж забирається назад.

| Функція | Що змінюється |
|---|---|
| `add_message` | Приймає `outcome` і `references`, перевіряє їх до будь-якого SQL і зберігає посилання з повідомленням |
| `load_messages` | Читає результат і посилання кожного повідомлення |
| `add_turn` | Зберігає запитання і відповідь в одному savepoint, під блокуванням розмови |

У `src/knowledge_service/persistence/conversations.py`:

**1.** Знайдіть:

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
```

Замініть на:

```python
"""Store Conversations, their Messages, and feedback on them.

The answering flow uses only the owner-scoped functions: `start_conversation`,
`add_message`, `add_turn`, `read_conversation`, and `delete_conversation`.
Each takes the caller's `UserId` and puts it in the SQL itself, so another
User's Conversation never leaves the database. They promise:

- **One answer for "not yours".** A Conversation that is missing, another
  User's, deleted, or past its retention deadline reads as None and refuses
  a Message with `ConversationNotFound`, the same in every case.
- **Order from the database.** `add_message` locks the Conversation's row,
  so concurrent Messages take turns; each gets the next ordinal, 0, 1, 2,
```

**2.** Знайдіть:

```python
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
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
```

Замініть на:

```python
  deletion time is never earlier than the last Message.
- **Any clock, one zone.** Every `now` must carry a time zone and is turned
  into UTC first, so a deadline is always the same span of real time.
- **Bounded.** `ConversationLimits` bounds the retention, the number of
  Messages, and each Message's length, and refuses text PostgreSQL cannot
  store, before any SQL runs.
- **A turn stays together.** `add_turn` stores a Question and its reply in
  one transaction, with how the reply ended and the Chunks an Answer
  cited, so History can trust that a reply follows its own Question.
- **Only through the store.** The rules a Message checks, such as "an
  Answer cites Chunks", live in Python, not in the database; a row written
  past these functions can make its Conversation unreadable.

The other functions read and write without an owner, for system work such
as retention and purge Jobs; they are never given a User's request.
"""

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
```

**3.** Знайдіть:

```python
    ConversationFull,
    ConversationLimits,
    ConversationNotFound,
    InvalidConversation,
    Message,
    MessageFeedback,
    MessageRating,
    MessageRole,
    Transcript,
)
from knowledge_service.identifiers import ConversationId, JobId, MessageId, UserId
from knowledge_service.jobs import Job, JobKind
from knowledge_service.persistence.jobs import enqueue_job
from knowledge_service.persistence.schema import (
    conversations,
    message_feedback,
    messages,
)


def due_conversations_statement(now: datetime, limit: int) -> sa.Select[Any]:
    """Select live Conversations whose retention deadline has passed."""
```

Замініть на:

```python
    ConversationFull,
    ConversationLimits,
    ConversationNotFound,
    InvalidConversation,
    Message,
    MessageFeedback,
    MessageOutcome,
    MessageRating,
    MessageReference,
    MessageRole,
    Transcript,
)
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import (
    ChunkId,
    ConversationId,
    DocumentId,
    JobId,
    MessageId,
    UserId,
)
from knowledge_service.jobs import Job, JobKind
from knowledge_service.persistence.jobs import enqueue_job
from knowledge_service.persistence.schema import (
    conversations,
    message_feedback,
    message_references,
    messages,
)


def due_conversations_statement(now: datetime, limit: int) -> sa.Select[Any]:
    """Select live Conversations whose retention deadline has passed."""
```

**4.** Знайдіть:

```python
        "message_id": message.message_id.value,
        "conversation_id": message.conversation_id.value,
        "ordinal": message.ordinal,
        "role": message.role.value,
        "text": message.text,
        "created_at": message.created_at,
    }


def _message_from(record: Mapping[str, object]) -> Message:
    try:
        role = MessageRole(cast(str, record["role"]))
    except ValueError:
        raise InvalidConversation("stored message role is not a known value") from None

    return Message(
        message_id=MessageId(cast(UUID, record["message_id"])),
        conversation_id=ConversationId(cast(UUID, record["conversation_id"])),
        ordinal=cast(int, record["ordinal"]),
        role=role,
        text=cast(str, record["text"]),
        created_at=cast(datetime, record["created_at"]),
    )


def _feedback_row(feedback: MessageFeedback) -> dict[str, object]:
    return {
        "message_id": feedback.message_id.value,
```

Замініть на:

```python
        "message_id": message.message_id.value,
        "conversation_id": message.conversation_id.value,
        "ordinal": message.ordinal,
        "role": message.role.value,
        "text": message.text,
        "created_at": message.created_at,
        "outcome": None if message.outcome is None else message.outcome.value,
    }


def _reference_rows(message: Message) -> list[dict[str, object]]:
    return [
        {
            "message_id": message.message_id.value,
            "position": position,
            "chunk_id": reference.chunk_id.value,
            "document_id": reference.document_id.value,
            "content_version": reference.content_version.number,
        }
        for position, reference in enumerate(message.references)
    ]


def _message_from(
    record: Mapping[str, object],
    references: tuple[MessageReference, ...] = (),
) -> Message:
    try:
        role = MessageRole(cast(str, record["role"]))
        stored = record.get("outcome")
        outcome = None if stored is None else MessageOutcome(cast(str, stored))
    except ValueError:
        raise InvalidConversation("a stored role or outcome is not known") from None

    return Message(
        message_id=MessageId(cast(UUID, record["message_id"])),
        conversation_id=ConversationId(cast(UUID, record["conversation_id"])),
        ordinal=cast(int, record["ordinal"]),
        role=role,
        text=cast(str, record["text"]),
        created_at=cast(datetime, record["created_at"]),
        outcome=outcome,
        references=references,
    )


def _reference_from(record: Mapping[str, object]) -> MessageReference:
    return MessageReference(
        chunk_id=ChunkId(cast(UUID, record["chunk_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        content_version=ContentVersion(cast(int, record["content_version"])),
    )


def _feedback_row(feedback: MessageFeedback) -> dict[str, object]:
    return {
        "message_id": feedback.message_id.value,
```

**5.** Знайдіть:

```python

async def load_messages(
    connection: AsyncConnection,
    *,
    conversation_id: ConversationId,
) -> tuple[Message, ...]:
    """Read a Conversation's Messages in ordinal order."""
    statement = (
        sa.select(messages)
        .where(messages.c.conversation_id == conversation_id.value)
        .order_by(messages.c.ordinal)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_message_from(dict(record)) for record in records)


async def rate_message(
    connection: AsyncConnection, *, feedback: MessageFeedback
) -> MessageFeedback:
    """Store one rating per Message per User, replacing any earlier rating."""
```

Замініть на:

```python

async def load_messages(
    connection: AsyncConnection,
    *,
    conversation_id: ConversationId,
) -> tuple[Message, ...]:
    """Read a Conversation's Messages in ordinal order, with their references."""
    statement = (
        sa.select(messages)
        .where(messages.c.conversation_id == conversation_id.value)
        .order_by(messages.c.ordinal)
    )
    records = (await connection.execute(statement)).mappings().all()
    cited = (
        (
            await connection.execute(
                sa.select(message_references)
                .join(
                    messages, messages.c.message_id == message_references.c.message_id
                )
                .where(messages.c.conversation_id == conversation_id.value)
                .order_by(
                    message_references.c.message_id, message_references.c.position
                )
            )
        )
        .mappings()
        .all()
    )
    references: dict[UUID, list[MessageReference]] = {}
    for record in cited:
        references.setdefault(cast(UUID, record["message_id"]), []).append(
            _reference_from(dict(record))
        )
    return tuple(
        _message_from(
            dict(record),
            tuple(references.get(cast(UUID, record["message_id"]), ())),
        )
        for record in records
    )


async def rate_message(
    connection: AsyncConnection, *, feedback: MessageFeedback
) -> MessageFeedback:
    """Store one rating per Message per User, replacing any earlier rating."""
```

**6.** Знайдіть:

```python
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
```

Замініть на:

```python
    owner_id: UserId,
    conversation_id: ConversationId,
    role: MessageRole,
    text: str,
    now: datetime,
    limits: ConversationLimits,
    outcome: MessageOutcome | None = None,
    references: tuple[MessageReference, ...] = (),
) -> Message:
    """Append a Message to the owner's live Conversation, at the next ordinal.

    An assistant Message carries how it ended; an Answer also carries the
    Chunks it cited, stored with it in the same transaction.

    Raises `InvalidConversation` for bad text or role before any SQL runs,
    `ConversationNotFound` when no live Conversation of this owner has this
    ID, and `ConversationFull` at the limit.
    """
    checked = _limits(limits).checked_text(text)
    if type(role) is not MessageRole:
        raise InvalidConversation("role must be a MessageRole")
    now = _utc(now)
    # Built once before any SQL, so a bad outcome or reference never waits
    # for the lock; its ordinal and time are set under the lock.
    draft = Message(
        message_id=MessageId.new(),
        conversation_id=ConversationId(_id(conversation_id)),
        ordinal=0,
        role=role,
        text=checked,
        created_at=now,
        outcome=outcome,
        references=references,
    )
    # Locked, so concurrent Messages take turns for the next ordinal.
    record = (
        (
            await connection.execute(
                sa.select(conversations)
                .where(_live(owner_id, conversation_id, now))
```

**7.** Знайдіть:

```python
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
```

Замініть на:

```python
        )
    ).one()
    if count >= limits.max_messages:
        raise ConversationFull("the conversation holds as many messages as allowed")
    # Clocks of two servers can disagree; time never runs backwards here.
    created_at = max(now, conversation.last_message_at)
    message = replace(
        draft, ordinal=0 if last is None else last + 1, created_at=created_at
    )
    await connection.execute(sa.insert(messages).values(_message_row(message)))
    if message.references:
        await connection.execute(
            sa.insert(message_references), _reference_rows(message)
        )
    await connection.execute(
        sa.update(conversations)
        .where(conversations.c.conversation_id == conversation_id.value)
        .values(last_message_at=created_at)
    )
    return message


async def add_turn(
    connection: AsyncConnection,
    *,
    owner_id: UserId,
    conversation_id: ConversationId,
    question: str,
    reply: str,
    outcome: MessageOutcome,
    references: tuple[MessageReference, ...] = (),
    now: datetime,
    limits: ConversationLimits,
) -> tuple[Message, Message]:
    """Store a Question and its reply together, once the reply is known.

    The first Message's row lock is held to the end of the transaction, so
    no other Message comes between the two, and History pairs a reply with
    the Question right before it. Both are checked before any SQL; if the
    second cannot be stored, a savepoint takes back the first as well.
    """
    if type(outcome) is not MessageOutcome:
        raise InvalidConversation("a reply must say how it ended")
    _limits(limits).checked_text(question)
    Message(
        message_id=MessageId.new(),
        conversation_id=ConversationId(_id(conversation_id)),
        ordinal=0,
        role=MessageRole.ASSISTANT,
        text=_limits(limits).checked_text(reply),
        created_at=_utc(now),
        outcome=outcome,
        references=references,
    )
    async with connection.begin_nested():
        asked = await add_message(
            connection,
            owner_id=owner_id,
            conversation_id=conversation_id,
            role=MessageRole.USER,
            text=question,
            now=now,
            limits=limits,
        )
        replied = await add_message(
            connection,
            owner_id=owner_id,
            conversation_id=conversation_id,
            role=MessageRole.ASSISTANT,
            text=reply,
            now=now,
            limits=limits,
            outcome=outcome,
            references=references,
        )
    return asked, replied


async def read_conversation(
    connection: AsyncConnection,
    *,
    owner_id: UserId,
```

Перевірка:

```bash
uv run pyright src/knowledge_service/persistence/conversations.py
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_conversation_store_integration.py -m integration -q
```

Очікувано: 0 помилок, і тести сховища з M06-T10 досі проходять.

### Крок 5 — Крок із кодом: історія

Цей крок пише модуль, що будує історію.

**Простими словами:** Він читає власну розмову читача, питає базу, які з процитованих Chunks цей читач ще може бачити в їхньому теперішньому вигляді, і поєднує кожне запитання з відповіддю. Потім іде назад від найновішого ходу, додаючи цілі ходи, поки вміщаються в бюджет, а потім лише старіші запитання. Текст відповіді потрапляє туди, лише якщо вона вдалася і ніщо, процитоване нею чи будь-якою попередньою відповіддю, не стало прихованим; інакше потрапляє лише її запитання.

| Частина | Що містить чи робить |
|---|---|
| `HistoryTurn` | Запитання, його відповідь або None, і посилання відповіді |
| `History` | `earlier_questions`, цілі `turns`, `tokens`, `left_out`, `withheld` |
| `build_history` | Чисте вкладання: без бази, тож кожне правило легко перевірити |
| `load_history` | Читання з прив'язкою до власника, одна SQL-перевірка процитованих Chunks, потім `build_history`; None, коли розмова не читача |
| `current_references_statement` | Процитовані Chunks, що актуальні й видимі, під `eligible_chunks_predicate` |
| `_turns`, `_shown` | Поєднання відповіді з запитанням безпосередньо перед нею; які відповіді можна показати |

Створіть `src/knowledge_service/answering/history.py`:

```python
"""History: the earlier turns of a Conversation that fit the History budget.

A follow-up such as "and for contractors?" means nothing without the turns
before it. `load_history` reads the reader's own Conversation, keeps the
Answers whose cited Chunks the reader may still see, and `build_history`
fits the rest into `history_tokens` (see `answering.context_budget`).

What History promises:

- **Owner first.** `load_history` reads through `read_conversation`, so a
  Conversation that is missing, another User's, deleted, or expired gives
  None, the same in every case.
- **Only Answers the reader may still see.** An Answer counts only when it
  was `ANSWERED` and every Chunk it cited is still current, in the same
  Document and version, and passes `eligible_chunks_predicate` for this
  reader now. Otherwise its text is withheld: a failed or abstained turn
  holds no knowledge, and a revoked, deleted, or rewritten Document must
  not reach the model again through an old Answer. The Question stays,
  because the User wrote it.
- **Nor what it may have repeated.** An Answer was written with the
  earlier Answers in its History, so it may repeat them. It is shown only
  when the Chunks of every earlier Answer are current too; one revoked
  Document early in a Conversation withholds every later Answer.
- **Recent turns whole, older ones as their Questions.** Turns are taken
  from the newest back, each with its Question and its Answer, while they
  fit. From the first that does not fit, older turns are summarized by
  their Questions alone, which is what a follow-up most needs to name its
  subject, while those fit. Everything older is left out. So what is kept
  is always the most recent stretch of the Conversation.
- **Never over budget.** Tokens are counted as in the context budget, UTF-8
  bytes plus a framing allowance per text, an upper bound for byte-level
  tokenizers such as OpenAI's, so a History that fits one model's budget
  fits it whatever model wrote the earlier Answers. A smaller budget, for a model with a
  smaller window, keeps a shorter stretch.
- **References kept.** Each whole turn keeps the Chunks its Answer cited,
  so a follow-up can be traced to the Documents it continues from.
- **Quiet.** Questions and Answers stay out of every repr; one log record
  holds counts only.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.conversations import (
    InvalidConversation,
    Message,
    MessageOutcome,
    MessageReference,
    MessageRole,
    Transcript,
)
from knowledge_service.identifiers import ConversationId
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.conversations import read_conversation
from knowledge_service.persistence.schema import chunks, documents

logger = logging.getLogger(__name__)

# Role markers and separators around each text in the prompt.
TEXT_FRAMING_TOKENS = 8


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    """One earlier Question and, if it may be shown, its Answer."""

    question: str = field(repr=False)
    answer: str | None = field(repr=False)
    references: tuple[MessageReference, ...]


@dataclass(frozen=True, slots=True)
class History:
    """The earlier turns that fit, oldest first.

    `earlier_questions` summarizes the turns before `turns` by their
    Questions alone. `left_out` counts turns not even summarized;
    `withheld` counts Answers whose text may not be shown.
    """

    earlier_questions: tuple[str, ...] = field(repr=False)
    turns: tuple[HistoryTurn, ...]
    tokens: int
    left_out: int
    withheld: int


@dataclass(frozen=True, slots=True)
class _Turn:
    question: Message
    answer: Message | None


def build_history(
    transcript: Transcript,
    current: frozenset[MessageReference],
    *,
    budget_tokens: int,
) -> History:
    """Fit the Conversation's turns into `budget_tokens`, newest first.

    `current` holds the cited Chunks the reader may still see, as they are
    now; an Answer is shown only when every Chunk it cited is in it.
    """
    if type(transcript) is not Transcript:
        raise InvalidConversation("transcript must be a Transcript")
    if type(current) is not frozenset:
        raise InvalidConversation("current must be a frozenset of references")
    if type(budget_tokens) is not int or budget_tokens < 0:
        raise InvalidConversation("budget_tokens must be a whole number, 0 or more")
    turns = _turns(transcript.messages)
    shown = _shown(turns, current)
    withheld = sum(
        turn.answer is not None and answer is None
        for turn, answer in zip(turns, shown, strict=True)
    )
    used = 0
    index = len(turns) - 1
    whole: list[HistoryTurn] = []
    while index >= 0:
        answer = shown[index]
        cost = _tokens(turns[index].question.text) + (
            0 if answer is None else _tokens(answer.text)
        )
        if used + cost > budget_tokens:
            break
        used += cost
        whole.append(
            HistoryTurn(
                question=turns[index].question.text,
                answer=None if answer is None else answer.text,
                references=() if answer is None else answer.references,
            )
        )
        index -= 1
    summarized: list[str] = []
    while index >= 0:
        cost = _tokens(turns[index].question.text)
        if used + cost > budget_tokens:
            break
        used += cost
        summarized.append(turns[index].question.text)
        index -= 1
    return History(
        earlier_questions=tuple(reversed(summarized)),
        turns=tuple(reversed(whole)),
        tokens=used,
        left_out=index + 1,
        withheld=withheld,
    )


async def load_history(
    connection: AsyncConnection,
    context: AuthorizationContext,
    *,
    conversation_id: ConversationId,
    now: datetime,
    budget_tokens: int,
) -> History | None:
    """The reader's History of this Conversation, or None if it is not theirs.

    Build it before the new Question is stored, so it holds only earlier
    turns.
    """
    if type(context) is not AuthorizationContext:
        raise InvalidConversation("context must be an AuthorizationContext")
    transcript = await read_conversation(
        connection,
        owner_id=context.user_id,
        conversation_id=conversation_id,
        now=now,
    )
    if transcript is None:
        return None
    cited = {
        reference for message in transcript.messages for reference in message.references
    }
    current: frozenset[MessageReference] = frozenset()
    if cited:
        statement = current_references_statement(
            [reference.chunk_id.value for reference in cited],
            eligibility=eligible_chunks_predicate(context),
        )
        records = (await connection.execute(statement)).mappings().all()
        found = {
            (record["chunk_id"], record["document_id"], record["content_version"])
            for record in records
        }
        current = frozenset(
            reference
            for reference in cited
            if (
                reference.chunk_id.value,
                reference.document_id.value,
                reference.content_version.number,
            )
            in found
        )
    history = build_history(transcript, current, budget_tokens=budget_tokens)
    _log(context, history, cited=len(cited), current=len(current))
    return history


def current_references_statement(
    chunk_ids: Sequence[UUID], *, eligibility: sa.ColumnElement[bool]
) -> sa.Select[*tuple[Any, ...]]:
    """Select the cited Chunks that are current and meet `eligibility`.

    A Chunk is current when its version is its Document's; replacing a
    version gives its Chunks new IDs, so an old ID finds nothing.
    """
    return (
        sa.select(chunks.c.chunk_id, chunks.c.document_id, chunks.c.content_version)
        .select_from(chunks)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(
            chunks.c.chunk_id
            == sa.any_(
                sa.bindparam(
                    "chunk_ids", list(chunk_ids), type_=ARRAY(sa.Uuid()), unique=True
                )
            ),
            chunks.c.content_version == documents.c.content_version,
            eligibility,
        )
    )


def _turns(messages: tuple[Message, ...]) -> list[_Turn]:
    """Pair each Question with the assistant Message right after it.

    `add_turn` stores a Question and its reply together, so a reply always
    follows its own Question. A Question with no reply after it is a turn
    with no Answer; a reply after anything but a Question is no turn.
    """
    turns: list[_Turn] = []
    previous: Message | None = None
    for message in messages:
        if message.role is MessageRole.USER:
            turns.append(_Turn(question=message, answer=None))
        elif previous is not None and previous.role is MessageRole.USER:
            turns[-1] = _Turn(question=turns[-1].question, answer=message)
        previous = message
    return turns


def _shown(
    turns: list[_Turn], current: frozenset[MessageReference]
) -> list[Message | None]:
    """Each Answer, if it and every Answer before it cite only current Chunks."""
    shown: list[Message | None] = []
    earlier_current = True
    for turn in turns:
        answer = turn.answer
        if answer is None or answer.outcome is not MessageOutcome.ANSWERED:
            shown.append(None)
            continue
        own_current = all(r in current for r in answer.references)
        shown.append(answer if own_current and earlier_current else None)
        earlier_current = earlier_current and own_current
    return shown


def _tokens(text: str) -> int:
    """An upper bound: at least one byte per token, plus the framing."""
    return len(text.encode("utf-8")) + TEXT_FRAMING_TOKENS


def _log(
    context: AuthorizationContext, history: History, *, cited: int, current: int
) -> None:
    fields = {
        "user_id": str(context.user_id.value),
        "turns": len(history.turns),
        "earlier_questions": len(history.earlier_questions),
        "left_out": history.left_out,
        "withheld": history.withheld,
        "tokens": history.tokens,
        "cited_chunks": cited,
        "current_chunks": current,
    }
    logger.info("history built", extra={"history": fields})
```

Тут важливі дві деталі. `build_history` приймає множину актуальних посилань як прості дані, тож його правила перевіряються без бази, а `load_history` — лише та частина, що питає PostgreSQL. А посилання вважається актуальним, лише коли збігаються Chunk, його Документ і версія: переписаний Документ дає своїм Chunks нові ID, тож старе посилання нічого не знаходить.

Перевірка:

```bash
uv run python -c "import knowledge_service.answering.history"
uv run pyright src/knowledge_service/answering/history.py
```

Очікувано: імпорт нічого не друкує, а pyright показує 0 помилок.

### Крок 6 — Крок із кодом: тести бюджету, приховування і відкликання

Цей крок доводить кожну обіцянку, спершу як чисті правила, а потім на справжньому PostgreSQL.

**Простими словами:** Юніт-тести будують розмови в пам'яті: довгі, які треба обрізати, з невдалими відповідями, такі, де читач втратив Chunk, і випадкові — для тесту властивості, що історія ніколи не перевищує бюджет і завжди є найновішим відрізком. Інтеграційні тести публікують справжні Документи, зберігають ходи з цитатами, потім відкликають доступ, публікують нову версію чи видаляють Документ і перевіряють, що історія ще показує.

| Тест | Що доводить |
|---|---|
| `test_a_short_conversation_is_kept_whole_and_in_order` | Успіх, з посиланнями |
| `test_a_long_conversation_keeps_recent_turns_and_summarizes_older_ones` | Довгі розмови: останні цілком, старіші як запитання |
| `test_a_smaller_budget_for_another_model_keeps_a_shorter_recent_stretch` | Зміна моделі: менший бюджет залишає коротший найновіший відрізок |
| `test_an_answer_whose_document_changed_is_withheld` | Відкликання доступу, нова версія, tombstone: найризиковіший край |
| `test_a_later_answer_that_may_repeat_a_withheld_one_is_withheld_too` | Прихований вміст не повертається через пізнішу відповідь |
| `test_a_deleted_conversation_has_no_history` | Видалення |
| `test_another_users_conversation_has_no_history_for_the_reader` | Власник |
| `test_turns_stored_at_once_never_interleave` | Одночасні ходи тримають кожну відповідь зі своїм запитанням |
| `test_history_is_never_over_budget_and_always_the_most_recent_stretch` | Тест властивості на випадкових розмовах і бюджетах |
| `test_bad_input_is_a_typed_refusal` | Типізована відмова: `InvalidConversation` |
| `test_answers_the_reader_may_still_see_are_kept_with_their_references` | Жодного підкладеного маркера в записах логу |

Створіть `tests/test_history.py`:

```python
"""Bounded History: recent turns whole, older ones as Questions, never over."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from knowledge_service.answering.history import (
    TEXT_FRAMING_TOKENS,
    History,
    build_history,
)
from knowledge_service.conversations import (
    Conversation,
    InvalidConversation,
    Message,
    MessageOutcome,
    MessageReference,
    MessageRole,
    Transcript,
)
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import (
    ChunkId,
    ConversationId,
    DocumentId,
    MessageId,
    UserId,
)

NOW = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
OWNER = UserId.parse("00000000-0000-0000-0000-000000000070")
CONVERSATION = Conversation.open(
    conversation_id=ConversationId.new(),
    owner_id=OWNER,
    opened_at=NOW,
    retention=timedelta(days=30),
)
MARKER = "PLANTED-MARKER-7719"
ANSWERED = MessageOutcome.ANSWERED
NOTHING_CURRENT: frozenset[MessageReference] = frozenset()


def reference(number: int) -> MessageReference:
    return MessageReference(
        chunk_id=ChunkId(UUID(int=1000 + number)),
        document_id=DocumentId(UUID(int=2000 + number)),
        content_version=ContentVersion(1),
    )


Turn = tuple[str, str | None, MessageOutcome | None, tuple[MessageReference, ...]]


def transcript(*turns: Turn) -> Transcript:
    messages: list[Message] = []
    for question, answer, outcome, references in turns:
        messages.append(message(len(messages), MessageRole.USER, question))
        if answer is not None:
            messages.append(
                message(
                    len(messages),
                    MessageRole.ASSISTANT,
                    answer,
                    outcome=outcome,
                    references=references,
                )
            )
    return Transcript(conversation=CONVERSATION, messages=tuple(messages))


def message(ordinal: int, role: MessageRole, text: str, **fields: Any) -> Message:
    return Message(
        message_id=MessageId.new(),
        conversation_id=CONVERSATION.conversation_id,
        ordinal=ordinal,
        role=role,
        text=text,
        created_at=NOW + timedelta(seconds=ordinal),
        **fields,
    )


def answered(question: str, answer: str, *cited: int) -> Turn:
    return (question, answer, ANSWERED, tuple(reference(n) for n in cited))


def everything(conversation: Transcript) -> frozenset[MessageReference]:
    return frozenset(r for m in conversation.messages for r in m.references)


def cost(*texts: str) -> int:
    return sum(len(text.encode()) + TEXT_FRAMING_TOKENS for text in texts)


# --- What is kept ------------------------------------------------------------------


def test_a_short_conversation_is_kept_whole_and_in_order() -> None:
    conversation = transcript(
        answered("How many days of leave?", "24 days.", 1),
        answered("Do they carry over?", "Up to 5 days.", 2),
    )

    history = build_history(conversation, everything(conversation), budget_tokens=1_000)

    assert [t.question for t in history.turns] == [
        "How many days of leave?",
        "Do they carry over?",
    ]
    assert [t.answer for t in history.turns] == ["24 days.", "Up to 5 days."]
    assert history.turns[1].references == (reference(2),)
    assert history.earlier_questions == ()
    assert history.left_out == 0
    assert history.tokens == cost(
        "How many days of leave?", "24 days.", "Do they carry over?", "Up to 5 days."
    )


def test_a_long_conversation_keeps_recent_turns_and_summarizes_older_ones() -> None:
    turns = [answered(f"Question {n}?", "x" * 400, n) for n in range(50)]
    conversation = transcript(*turns)
    budget = 3 * cost("Question 49?", "x" * 400) + 5 * cost("Question 40?")

    history = build_history(
        conversation, everything(conversation), budget_tokens=budget
    )

    assert [t.question for t in history.turns] == [
        "Question 47?",
        "Question 48?",
        "Question 49?",
    ]
    assert history.earlier_questions == tuple(f"Question {n}?" for n in range(42, 47))
    assert history.left_out == 42
    assert history.tokens <= budget


def test_a_smaller_budget_for_another_model_keeps_a_shorter_recent_stretch() -> None:
    conversation = transcript(
        *(answered(f"Question {n}?", "y" * 200, n) for n in range(10))
    )
    current = everything(conversation)

    large = build_history(conversation, current, budget_tokens=4_000)
    small = build_history(conversation, current, budget_tokens=600)

    assert len(small.turns) < len(large.turns)
    assert small.turns == large.turns[len(large.turns) - len(small.turns) :]


def test_no_budget_means_no_history() -> None:
    conversation = transcript(answered("How many days?", "24 days.", 1))

    history = build_history(conversation, everything(conversation), budget_tokens=0)

    assert history.turns == ()
    assert history.earlier_questions == ()
    assert history.left_out == 1
    assert history.tokens == 0


# --- What is withheld ----------------------------------------------------------------


@pytest.mark.parametrize(
    "outcome", [MessageOutcome.ABSTAINED, MessageOutcome.FAILED, None]
)
def test_an_answer_that_did_not_succeed_is_withheld_but_its_question_stays(
    outcome: MessageOutcome | None,
) -> None:
    conversation = transcript(("How many days?", "I could not answer.", outcome, ()))

    history = build_history(conversation, frozenset(), budget_tokens=1_000)

    assert history.turns[0].question == "How many days?"
    assert history.turns[0].answer is None
    assert history.withheld == 1


def test_an_answer_citing_a_chunk_the_reader_may_no_longer_see_is_withheld() -> None:
    conversation = transcript(
        answered("And leave?", "24 days.", 3),
        answered("Secret project budget?", f"It is 2M. {MARKER}", 1, 2),
    )
    # The reader lost one of the two Chunks of the second Answer.
    current = frozenset({reference(2), reference(3)})

    history = build_history(conversation, current, budget_tokens=1_000)

    assert history.turns[0].answer == "24 days."
    assert history.turns[1].answer is None
    assert history.turns[1].references == ()
    assert history.withheld == 1
    assert all(MARKER not in (t.answer or "") for t in history.turns)


def test_a_reference_of_another_version_is_not_current() -> None:
    conversation = transcript(answered("How many days?", "24 days.", 1))
    newer = MessageReference(
        chunk_id=reference(1).chunk_id,
        document_id=reference(1).document_id,
        content_version=ContentVersion(2),
    )

    history = build_history(conversation, frozenset({newer}), budget_tokens=1_000)

    assert history.turns[0].answer is None


def test_questions_without_replies_and_replies_without_questions() -> None:
    messages = (
        message(0, MessageRole.ASSISTANT, "Welcome.", outcome=MessageOutcome.FAILED),
        message(1, MessageRole.USER, "First?"),
        message(2, MessageRole.USER, "Second?"),
        message(
            3,
            MessageRole.ASSISTANT,
            "24.",
            outcome=ANSWERED,
            references=(reference(1),),
        ),
    )
    conversation = Transcript(conversation=CONVERSATION, messages=messages)

    history = build_history(conversation, everything(conversation), budget_tokens=1_000)

    assert [(t.question, t.answer) for t in history.turns] == [
        ("First?", None),
        ("Second?", "24."),
    ]


# --- Properties --------------------------------------------------------------------

_TURN = st.tuples(
    st.text(min_size=1, max_size=60).filter(str.strip),
    st.one_of(st.none(), st.text(min_size=1, max_size=200).filter(str.strip)),
    st.booleans(),
)


@settings(max_examples=300, deadline=None)
@given(st.lists(_TURN, max_size=30), st.integers(min_value=0, max_value=3_000))
def test_history_is_never_over_budget_and_always_the_most_recent_stretch(
    raw: list[tuple[str, str | None, bool]], budget: int
) -> None:
    turns: list[Turn] = [
        (
            question,
            answer,
            None if answer is None else (ANSWERED if ok else MessageOutcome.FAILED),
            (reference(n),) if answer is not None and ok else (),
        )
        for n, (question, answer, ok) in enumerate(raw)
    ]
    conversation = transcript(*turns)

    history = build_history(
        conversation, everything(conversation), budget_tokens=budget
    )

    kept = len(history.turns) + len(history.earlier_questions)
    assert history.tokens <= budget
    assert history.left_out + kept == len(turns)
    expected = [question for question, *_ in turns][history.left_out :]
    assert [
        *history.earlier_questions,
        *(t.question for t in history.turns),
    ] == expected


# --- Typed failures and quiet reprs ---------------------------------------------------


@pytest.mark.parametrize(
    ("conversation", "current", "budget"),
    [
        ("not a transcript", NOTHING_CURRENT, 10),
        (transcript(), {reference(1)}, 10),
        (transcript(), NOTHING_CURRENT, -1),
        (transcript(), NOTHING_CURRENT, True),
    ],
)
def test_bad_input_is_a_typed_refusal(
    conversation: Any, current: Any, budget: Any
) -> None:
    with pytest.raises(InvalidConversation):
        build_history(conversation, current, budget_tokens=budget)


def test_history_holds_no_text_in_its_repr() -> None:
    conversation = transcript(answered(f"Q {MARKER}?", f"A {MARKER}.", 1))
    history = build_history(conversation, everything(conversation), budget_tokens=1)
    summary = build_history(conversation, frozenset(), budget_tokens=1_000)

    assert isinstance(history, History)
    assert MARKER not in repr(history)
    assert MARKER not in repr(summary)


@pytest.mark.parametrize(
    "fields",
    [
        {"role": MessageRole.USER, "outcome": ANSWERED, "references": (reference(1),)},
        {"role": MessageRole.ASSISTANT, "outcome": ANSWERED, "references": ()},
        {
            "role": MessageRole.ASSISTANT,
            "outcome": MessageOutcome.FAILED,
            "references": (reference(1),),
        },
        {
            "role": MessageRole.ASSISTANT,
            "outcome": ANSWERED,
            "references": (reference(1), reference(1)),
        },
        {
            "role": MessageRole.ASSISTANT,
            "outcome": ANSWERED,
            "references": tuple(reference(n) for n in range(21)),
        },
        {"role": MessageRole.ASSISTANT, "outcome": "answered", "references": ()},
    ],
)
def test_a_message_and_its_references_must_agree(fields: dict[str, Any]) -> None:
    role = fields.pop("role")

    with pytest.raises(InvalidConversation):
        message(0, role, "24 days.", **fields)


# --- Review regressions -------------------------------------------------------------


def test_a_later_answer_that_may_repeat_a_withheld_one_is_withheld_too() -> None:
    conversation = transcript(
        answered("Secret project budget?", f"It is 2M {MARKER}.", 1),
        answered("And leave?", f"Budget {MARKER} as said; leave 24 days.", 2),
    )

    history = build_history(
        conversation, frozenset({reference(2)}), budget_tokens=1_000
    )

    assert [t.answer for t in history.turns] == [None, None]
    assert history.withheld == 2


def test_a_failed_answer_does_not_withhold_the_answers_after_it() -> None:
    conversation = transcript(
        ("Secret project budget?", "I could not answer.", MessageOutcome.FAILED, ()),
        answered("And leave?", "24 days.", 2),
    )

    history = build_history(conversation, everything(conversation), budget_tokens=1_000)

    assert [t.answer for t in history.turns] == [None, "24 days."]


def test_a_version_too_large_to_store_is_refused() -> None:
    with pytest.raises(InvalidConversation):
        MessageReference(
            chunk_id=ChunkId(UUID(int=1)),
            document_id=DocumentId(UUID(int=2)),
            content_version=ContentVersion(2**31),
        )
```

Створіть `tests/test_history_integration.py`:

```python
"""History in PostgreSQL: stored references, revocation, deletion, ownership."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.answering.history import History, load_history
from knowledge_service.conversations import (
    ConversationFull,
    ConversationLimits,
    MessageOutcome,
    MessageReference,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import (
    ChunkId,
    ConversationId,
    DocumentId,
    GroupId,
    SourceId,
    UserId,
)
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.conversations import (
    add_turn,
    delete_conversation,
    read_conversation,
    start_conversation,
)
from knowledge_service.persistence.schema import chunks
from knowledge_service.settings import Settings

pytestmark = pytest.mark.integration

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000057")
OBSERVED_AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)
READER = UserId.parse("00000000-0000-0000-0000-0000000000e4")
OTHER = UserId.parse("00000000-0000-0000-0000-0000000000e5")
TEAM = GroupId.parse("00000000-0000-0000-0000-0000000000f2")
CONTEXT = AuthorizationContext(user_id=READER, group_ids=frozenset({TEAM}))
LIMITS = ConversationLimits()
MARKER = "PLANTED-MARKER-7719"

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE.value})
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def publish(
    runtime: DatabaseRuntime,
    name: str,
    text: str,
    *,
    readers: tuple[Subject, ...] = (Subject.group(TEAM),),
    version: str = "v1",
    observed_at: datetime = OBSERVED_AT,
) -> MessageReference:
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version=version,
            media_type="text/plain",
            content=text.encode(),
            access=SourceAccess(public=False, principals=("someone",)),
            metadata=SourceMetadata(title=name, modified_at=observed_at, size_bytes=1),
        ),
        source_id=SOURCE,
    )
    assert isinstance(outcome, NormalizedDocument)
    reconciled = await reconcile_document(
        runtime, outcome, readers=readers, observed_at=observed_at
    )
    document = reconciled.document
    async with runtime.transaction() as connection:
        chunk_id = await connection.scalar(
            sa.select(chunks.c.chunk_id).where(
                chunks.c.document_id == document.document_id.value
            )
        )
    return MessageReference(
        chunk_id=ChunkId(cast(UUID, chunk_id)),
        document_id=DocumentId(document.document_id.value),
        content_version=ContentVersion(document.content_version.number),
    )


async def conversation(
    runtime: DatabaseRuntime,
    *turns: tuple[str, str, MessageOutcome, tuple[MessageReference, ...]],
    owner: UserId = READER,
) -> ConversationId:
    async with runtime.transaction() as connection:
        started = await start_conversation(
            connection, owner_id=owner, now=OBSERVED_AT, limits=LIMITS
        )
        for question, answer, outcome, references in turns:
            await add_turn(
                connection,
                owner_id=owner,
                conversation_id=started.conversation_id,
                question=question,
                reply=answer,
                outcome=outcome,
                references=references,
                now=OBSERVED_AT,
                limits=LIMITS,
            )
    return started.conversation_id


async def history(
    runtime: DatabaseRuntime,
    conversation_id: ConversationId,
    context: AuthorizationContext = CONTEXT,
) -> History | None:
    async with runtime.transaction() as connection:
        return await load_history(
            connection,
            context,
            conversation_id=conversation_id,
            now=LATER,
            budget_tokens=4_000,
        )


async def leave_and_carry(
    runtime: DatabaseRuntime,
) -> tuple[ConversationId, MessageReference, MessageReference]:
    leave = await publish(runtime, "leave.txt", "Every employee receives 24 days.")
    carry = await publish(runtime, "carry.txt", "Unused leave carries over 5 days.")
    conversation_id = await conversation(
        runtime,
        ("How many days?", f"24 days. {MARKER}", MessageOutcome.ANSWERED, (leave,)),
        ("Do they carry over?", "Up to 5 days.", MessageOutcome.ANSWERED, (carry,)),
        ("And next year?", "I could not answer.", MessageOutcome.FAILED, ()),
    )
    return conversation_id, leave, carry


async def test_answers_the_reader_may_still_see_are_kept_with_their_references(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    conversation_id, leave, carry = await leave_and_carry(runtime)

    built = await history(runtime, conversation_id)

    assert built is not None
    assert [t.answer for t in built.turns] == [
        f"24 days. {MARKER}",
        "Up to 5 days.",
        None,
    ]
    assert [t.references for t in built.turns] == [(leave,), (carry,), ()]
    assert built.withheld == 1
    assert all(MARKER not in record.getMessage() for record in caplog.records)
    assert all(MARKER not in repr(record.__dict__) for record in caplog.records)


async def test_stored_outcomes_and_references_read_back_as_written(
    runtime: DatabaseRuntime,
) -> None:
    conversation_id, leave, _ = await leave_and_carry(runtime)

    async with runtime.transaction() as connection:
        transcript = await read_conversation(
            connection, owner_id=READER, conversation_id=conversation_id, now=LATER
        )

    assert transcript is not None
    assert [m.outcome for m in transcript.messages] == [
        None,
        MessageOutcome.ANSWERED,
        None,
        MessageOutcome.ANSWERED,
        None,
        MessageOutcome.FAILED,
    ]
    assert transcript.messages[1].references == (leave,)


async def revoke(runtime: DatabaseRuntime) -> None:
    await publish(
        runtime,
        "leave.txt",
        "Every employee receives 24 days.",
        readers=(Subject.user(OTHER),),
        observed_at=LATER,
    )


async def rewrite(runtime: DatabaseRuntime) -> None:
    await publish(
        runtime,
        "leave.txt",
        "Every employee receives 26 days.",
        version="v2",
        observed_at=LATER,
    )


async def tombstone(runtime: DatabaseRuntime) -> None:
    await tombstone_missing(
        runtime, source_id=SOURCE, seen={"carry.txt"}, observed_at=LATER
    )


@pytest.mark.parametrize(
    "change",
    [revoke, rewrite, tombstone],
    ids=["access-revoked", "new-version", "tombstoned"],
)
async def test_an_answer_whose_document_changed_is_withheld(
    runtime: DatabaseRuntime,
    change: Callable[[DatabaseRuntime], Awaitable[None]],
) -> None:
    conversation_id, _, _ = await leave_and_carry(runtime)

    await change(runtime)
    built = await history(runtime, conversation_id)

    assert built is not None
    assert [t.question for t in built.turns] == [
        "How many days?",
        "Do they carry over?",
        "And next year?",
    ]
    # The second Answer was written with the first in its History.
    assert [t.answer for t in built.turns] == [None, None, None]
    assert [t.references for t in built.turns] == [(), (), ()]
    assert built.withheld == 3


async def test_a_deleted_conversation_has_no_history(runtime: DatabaseRuntime) -> None:
    conversation_id, _, _ = await leave_and_carry(runtime)
    async with runtime.transaction() as connection:
        await delete_conversation(
            connection, owner_id=READER, conversation_id=conversation_id, now=LATER
        )

    assert await history(runtime, conversation_id) is None


async def test_another_users_conversation_has_no_history_for_the_reader(
    runtime: DatabaseRuntime,
) -> None:
    leave = await publish(runtime, "leave.txt", "Every employee receives 24 days.")
    foreign = await conversation(
        runtime,
        ("How many days?", "24 days.", MessageOutcome.ANSWERED, (leave,)),
        owner=OTHER,
    )

    assert await history(runtime, foreign) is None
    other = AuthorizationContext(user_id=OTHER, group_ids=frozenset({TEAM}))
    assert await history(runtime, foreign, other) is not None


# --- Review regressions -------------------------------------------------------------


async def test_turns_stored_at_once_never_interleave(runtime: DatabaseRuntime) -> None:
    leave = await publish(runtime, "leave.txt", "Every employee receives 24 days.")
    conversation_id = await conversation(runtime)

    async def ask(question: str, reply: str) -> None:
        async with runtime.transaction() as connection:
            await add_turn(
                connection,
                owner_id=READER,
                conversation_id=conversation_id,
                question=question,
                reply=reply,
                outcome=MessageOutcome.ANSWERED,
                references=(leave,),
                now=OBSERVED_AT,
                limits=LIMITS,
            )

    await asyncio.gather(*(ask(f"Q{n}?", f"A{n}.") for n in range(6)))
    built = await history(runtime, conversation_id)

    assert built is not None
    assert len(built.turns) == 6
    assert all(t.answer == f"A{t.question[1:-1]}." for t in built.turns)


async def test_a_turn_that_does_not_fit_leaves_no_half_behind(
    runtime: DatabaseRuntime,
) -> None:
    leave = await publish(runtime, "leave.txt", "Every employee receives 24 days.")
    conversation_id = await conversation(
        runtime, ("How many days?", "24 days.", MessageOutcome.ANSWERED, (leave,))
    )
    three = ConversationLimits(max_messages=3)

    async with runtime.transaction() as connection:
        with pytest.raises(ConversationFull):
            await add_turn(
                connection,
                owner_id=READER,
                conversation_id=conversation_id,
                question="And next year?",
                reply="26 days.",
                outcome=MessageOutcome.ANSWERED,
                references=(leave,),
                now=OBSERVED_AT,
                limits=three,
            )
    async with runtime.transaction() as connection:
        transcript = await read_conversation(
            connection, owner_id=READER, conversation_id=conversation_id, now=LATER
        )

    assert transcript is not None
    assert len(transcript.messages) == 2
```

Перевірка:

```bash
uv run pytest tests/test_history.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_history_integration.py -m integration -q
```

Очікувано: 25 passed, потім 9 passed.

### Крок 7 — Без коду: зламайте і вирішіть

Цей крок показує, що захищає перевірка прав в історії, і фіксує, чого ви навчилися.

**Дія 1:** навмисно зламайте історію. У `src/knowledge_service/answering/history.py`, в `load_history`, замініть:

```python
            eligibility=eligible_chunks_predicate(context),
```

на:

```python
            eligibility=sa.true(),
```

Запустіть:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_history_integration.py -m integration -q
```

Очікувано: 2 failed, 7 passed: випадки з відкликаним доступом і tombstone знову показують стару відповідь. Випадок з новою версією досі проходить, бо переписаний Документ дає своїм Chunks нові ID і відповідь приховує вже сама перевірка версії; лише перевірка прав зупиняє відповідь з Документа, який читач більше не може бачити. Поверніть рядок і запускайте знову, доки все не пройде.

**Дія 2:** під `## Decision` напишіть:

- чому відповідь перевіряють за правами читача зараз, а не тоді, коли її написали;
- чому пізнішу відповідь приховують, коли приховано попередню;
- чому старіші ходи підсумовуються їхніми запитаннями;
- чому посилання не має зовнішнього ключа на `chunks`;
- що лишається на потім: потік уточнень (M06-T12) і точне відстеження того, які попередні відповіді бачила кожна відповідь;
- щонайменше одне **спростоване припущення** у формі «Я припускав X. Запуск чи рев'ю показує Y. Отже, Z хибне».

### Крок 8 — Без коду: архітектура, перевірки, evidence і прогрес

Цей крок оновлює текст, який зміна зробила неповним, доводить, що все досі працює, і фіксує результат.

**Дія 1:** у `docs/architecture.md` додайте історію до рядка `answering`, нову таблицю до діаграми та її правила до таблиці правил:

**1.** Знайдіть:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`) |
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
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

**2.** Знайдіть:

````markdown
    chunks ||--o{ chunk_embeddings : "is embedded as"
    embedding_spaces ||--o{ chunk_embeddings : "holds"
    embedding_spaces ||--o{ embedding_migrations : "is moved from or to"
    index_configurations ||--o{ embedding_migrations : "describes the target of"
    conversations ||--o{ messages : "holds"
    messages ||--o{ message_feedback : "is rated by"
```

The rules each table enforces:

| Table | Rule |
|---|---|
````

Замініть на:

````markdown
    chunks ||--o{ chunk_embeddings : "is embedded as"
    embedding_spaces ||--o{ chunk_embeddings : "holds"
    embedding_spaces ||--o{ embedding_migrations : "is moved from or to"
    index_configurations ||--o{ embedding_migrations : "describes the target of"
    conversations ||--o{ messages : "holds"
    messages ||--o{ message_feedback : "is rated by"
    messages ||--o{ message_references : "cites"
```

The rules each table enforces:

| Table | Rule |
|---|---|
````

**3.** Знайдіть:

```markdown
| `index_configurations` | One row per settings `fingerprint` (64 lowercase hex digits); rows are only inserted, so a changed setting is a new row. |
| `embedding_spaces` | One space per `(provider, model, dimensions)`; dimensions from 1 to 16000; at most one space is current. |
| `embedding_migrations` | A state of `dual_writing`, `switched`, or `cleaned`; `updated_at >= started_at`; a deleted space leaves the row with that space set to null. |
| `chunk_embeddings` | One vector per `(chunk_id, space_id)`; the vector's length must equal its space's dimensions; removed with its Chunk or its space. |
| `access_grants` | One Grant per `(document_id, subject_kind, subject_id)`; a `public` Grant carries no subject and every other kind carries one. |
| `conversations` | `retention_deadline > created_at`; `last_message_at >= created_at`. |
| `messages` | One Message per `(conversation_id, ordinal)`. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code. |
| `audit_events` | No raw content, and a metadata bag capped at 1024 bytes. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |

A rule that spans rows of one table, such as one running Synchronization Run per
```

Замініть на:

```markdown
| `index_configurations` | One row per settings `fingerprint` (64 lowercase hex digits); rows are only inserted, so a changed setting is a new row. |
| `embedding_spaces` | One space per `(provider, model, dimensions)`; dimensions from 1 to 16000; at most one space is current. |
| `embedding_migrations` | A state of `dual_writing`, `switched`, or `cleaned`; `updated_at >= started_at`; a deleted space leaves the row with that space set to null. |
| `chunk_embeddings` | One vector per `(chunk_id, space_id)`; the vector's length must equal its space's dimensions; removed with its Chunk or its space. |
| `access_grants` | One Grant per `(document_id, subject_kind, subject_id)`; a `public` Grant carries no subject and every other kind carries one. |
| `conversations` | `retention_deadline > created_at`; `last_message_at >= created_at`. |
| `messages` | One Message per `(conversation_id, ordinal)`; only an assistant Message has an `outcome`: `answered`, `abstained`, or `failed`. |
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code. |
| `audit_events` | No raw content, and a metadata bag capped at 1024 bytes. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |

A rule that spans rows of one table, such as one running Synchronization Run per
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_history.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний запуск дав 2221 passed для `make test-unit`, на 25 більше, ніж у M06-T10, і 370 passed для `make test-integration`, на 9 більше. Якщо він каже, що тестової бази немає, запустіть її командою `docker start knowledge-service-postgres`.

**Дія 3:** заповніть файл evidence:

- **Design:** міграція; результати й посилання повідомлення; `add_turn`; як історія поєднує, перевіряє і вкладає ходи; підрахунок токенів.
- **Verification:** команди й кількості, навмисний злам і відомі обмеження з «Перед початком».
- **Review:** змагальне рев'ю показало, що підроблені посилання, неправильні версії, видалені Chunks, відкликані групи і старі рядки приховуються, а історія ніколи не перевищує бюджет; воно знайшло чотири проблеми, три виправлено з тестами, що падали до виправлення, а одну задокументовано: пізніша відповідь, що повторювала відкликану, потрапляла в історію; впереміш збережені ходи поєднували відповідь не з тим запитанням; версія понад межу цілого падала нетипізовано; рядок, записаний в обхід сховища, робить розмову нечитабельною.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу виконаною, додайте рядок M06-T11 у `docs/curriculum/PROGRESS.md`, поставте наступною задачу M06-T12 і зробіть коміт з `m06-t11: construct bounded history`.

## Запитання для рефлексії

1. Що `load_history` приховує від коду, який відповідатиме на уточнення? Перелічіть, чого тому коду ніколи не треба перевіряти щодо старих відповідей.
2. Хтось пропонує кешувати історію кожної розмови, щоб зекономити запит до бази на кожне запитання. Що могла б показати закешована історія, що свіжа приховала б, і який тест це впіймає?
3. Приховувати кожну відповідь після відкликаної — обережно. Що треба було б зберігати з кожною відповіддю, щоб приховувати лише ті відповіді, які справді бачили відкликаний текст?
4. Користувач питає «А для підрядників?» після п'яти ходів про відпустку. З малим бюджетом, які частини розмови залишить історія, і чи досить цього, щоб уточнення мало сенс?
5. Яку розмову ви побудували б наступною, щоб спробувати протягнути прихований вміст в історію, і що, на вашу думку, історія з нею зробить?

## Чекліст завершення

- [ ] Файл evidence містить інваріант і ваші передбачення, записані до коду.
- [ ] Міграція додає `messages.outcome` і `message_references`, і схема з нею збігається.
- [ ] Результат і посилання повідомлення перевіряються разом: лише відповідь цитує Chunks.
- [ ] `add_message` зберігає посилання з повідомленням; `load_messages` читає їх назад.
- [ ] `add_turn` зберігає запитання і відповідь разом, або нічого.
- [ ] Історія залишає останні ходи цілком, потім старіші запитання, ніколи не перевищує бюджет, завжди найновіший відрізок.
- [ ] Невдалі, утримані й старі відповіді приховано; їхні запитання лишаються.
- [ ] Відповідь, чиї Chunks чи Chunks попередньої відповіді вже не актуальні й не видимі, приховано.
- [ ] Розмова, що не належить читачу, не має історії.
- [ ] Жоден текст запитання чи відповіді не потрапляє в запис логу чи repr.
- [ ] Ви запустили навмисний злам, побачили, що тести падають, і скасували його.
- [ ] Усі перевірки проходять: форматування, лінтер, типи, юніт, інтеграційні, документація.
- [ ] Evidence, статус задачі, рядок PROGRESS і коміт готові.

Коли напишете файли, поділіться ними чи виводом помилок, і я перевірю їх за цим чеклістом.
