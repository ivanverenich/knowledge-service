# M06-T10 — Persist bounded Conversations

Source task: [M06-T10](../curriculum/milestones/M06-answers-and-conversations/M06-T10-persist-bounded-conversations.md)

## What you will do

This lesson gives Conversations the storage a chat needs: each one belongs to one User, keeps its Messages in a fixed order, stops being visible the moment it is deleted or expires, and cannot grow without bound. M02-T09 already created the tables and the plain values. What is missing is the part a User's request goes through: functions that always ask "whose is it?" inside the SQL, number Messages correctly even when two arrive at once, and delete at once.

- `ConversationLimits` holds the bounds: retention, the number of Messages, the length of one Message.
- `start_conversation`, `add_message`, `read_conversation`, and `delete_conversation` are the owner-scoped store.
- `ConversationNotFound` is the one answer for "missing, not yours, deleted, or expired".

| The task asks for | Where it happens |
|---|---|
| Conversation ownership | Step 3: every owner-scoped function puts the `UserId` in the SQL |
| Ordered messages | Step 3: `add_message` locks the Conversation's row and gives the next ordinal |
| Retention deadline | Step 3: a Conversation past its deadline reads as missing |
| Deletion state | Step 3: `delete_conversation` sets `deleted_at` and queues a purge Job in one transaction |
| Limits | Step 2: `ConversationLimits` and `checked_text`; Step 3 counts Messages under the lock |
| Independent of Open WebUI payload shape | Step 2: user and assistant turns of plain text, the service's own terms |
| Integration tests enforce ownership, ordering, and immediate logical deletion | Step 4: `tests/test_conversation_store_integration.py` |
| No seeded text in telemetry | Step 4: a planted marker never reaches an error or a repr |

This lesson includes coding: Steps 2 to 4 write code. Steps 1, 5, and 6 do not. The integration tests need the test database.

Terms used in this lesson:

- **Owner-scoped:** a query that carries the caller's identity in its `WHERE`, so rows of other Users never leave the database, rather than loading a row and checking its owner in Python afterwards.
- **Logical deletion:** marking a row as deleted (`deleted_at`) instead of removing it. Every read ignores it at once; a later purge Job removes the rows for good, like a bin that is emptied overnight.
- **Row lock:** `SELECT ... FOR UPDATE` makes other transactions that want the same row wait until this one ends. Here it makes two Messages for one Conversation take turns.
- **Isolation level:** how much one transaction sees of others running at the same time. PostgreSQL's default, READ COMMITTED, lets each statement see what was committed before it started.
- **Ordinal:** a Message's position in its Conversation: 0, 1, 2, and so on.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **What already exists.** `conversations.py` has `Conversation`, `Message`, and `MessageRole`, and `Conversation.require_owner`. `persistence/conversations.py` stores and loads them, and the schema keeps one Message per ordinal and the deadline after the creation time. Those functions take no owner: they suit system work such as the retention sweep, not a User's request.
- **Authorization inside the SQL.** Loading a Conversation and then calling `require_owner` would bring another User's row into memory first. The new functions put `owner_id` in every `WHERE`, so a row that is not yours never leaves PostgreSQL.
- **One answer for "not yours".** Missing, another User's, deleted, and expired all read as None and refuse a Message with the same `ConversationNotFound`. Different answers would let a User probe whether someone else's Conversation exists.
- **The database numbers the Messages.** The caller no longer picks an ordinal. `add_message` locks the Conversation's row, reads the highest ordinal, and writes the next one. Two Messages that arrive together wait for each other, so there are no gaps, no duplicates, and the limit cannot be passed.
- **Reads take no lock.** A delete only marks the row, so the Messages are still there to read. Without a lock, reading the history and then adding the question in one transaction cannot deadlock.
- **Deleted at once, removed later.** `delete_conversation` sets `deleted_at` and queues a `purge_conversation` Job in the same transaction. From its commit on, every owner-scoped read and write treats the Conversation as missing.
- **Time.** Every `now` must carry a time zone and is turned into UTC first, so 30 days of retention is 30 days of real time even across a summer-time change. A Message's time never runs backwards, and a deletion is never earlier than the last Message, even when two servers' clocks disagree.
- **Limits before SQL.** `checked_text` refuses empty text, text over the limit, and text PostgreSQL cannot store: the NUL character and half a character (a lone surrogate). Nothing reaches the database to fail there with an untyped error.
- **Our own shape.** Open WebUI, the chat interface of M09, sends its own JSON with roles such as `system`. The store keeps only what this service defines: a user or assistant turn of plain text. The interface adapter will translate.
- **What the review found.** An adversarial review attacked ownership, ordering, deletion, retention, and hostile input on real PostgreSQL. It found no way to read, add to, or delete another User's Conversation, and no gap or duplicate in ordinals. It found these problems, each fixed with a test that fails without the fix:
  - Reading with a shared lock and then adding or deleting in the same transaction deadlocked when two requests did it at once. Now reads take no lock.
  - A start near the end of the calendar, or a retention of `timedelta.max`, raised an untyped `OverflowError`. Now retention is at most 10 years, and an overflow is `InvalidConversation`.
  - A `now` in a zone such as London passed the check but put the deadline off by the summer-time hour, or was refused. Now every `now` is turned into UTC first, and a time zone that fails is a typed refusal.
  - A deletion could be earlier than a Message committed before it, when clocks disagreed. Now it is never earlier than the last Message.
  - Under REPEATABLE READ or SERIALIZABLE, waiting for a turn raised a serialization error. The functions now say they expect READ COMMITTED, the project's default.
- **Where the code lives.** `conversations.py` and `persistence/conversations.py`. The schema does not change.
- **Known limits.** The functions expect READ COMMITTED, the project's default; at a stricter level, waiting for a turn becomes a serialization error. Nothing removes deleted rows yet: the purge Job is queued, and its worker comes with M08. An owner who deletes their own expired Conversation gets True, while read and add treat it as missing; it reveals nothing about other Users. The M02 functions without an owner stay for system work.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before any code exists, write what must be true when the lesson is done. Step 5 checks the predictions.

Create `docs/evidence/M06-T10-persist-bounded-conversations.md`:

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

Check: the file exists, the failing check fails, and every prediction is a full sentence Step 5 can prove wrong.

### Step 2 — Coding step: limits and the "not found" answer

This step adds the bounds of a Conversation and the one error a caller gets for "not yours".

**In plain words:** A Conversation now has written-down limits: how long it lives, how many Messages it can hold, and how long one Message can be. Before any text goes near the database, it is checked against them and against what the database can store. A new error says "no such Conversation" in the same words whatever the real reason, and a Message never shows its text when it is printed.

| Part | What it holds or does |
|---|---|
| `ConversationNotFound` | One error for missing, another User's, deleted, and expired |
| `Message.text` | Hidden from the repr |
| `Conversation.open` | Refuses a deadline past the end of the calendar with a typed error |
| `ConversationLimits` | Retention (at most `MAX_RETENTION`, 10 years), `max_messages`, `max_message_characters` |
| `checked_text` | Plain, non-empty text within the limit, without NUL or half a character |
| `Transcript` | A live Conversation and its Messages in order |

In `src/knowledge_service/conversations.py`:

**1.** Find:

```python
expires so a later task can delete it without guessing.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
```

Replace with:

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

**2.** Find:

```python
    """A Conversation already holds as many Messages as its bound allows."""

```

Replace with:

```python
    """A Conversation already holds as many Messages as its bound allows."""


class ConversationNotFound(InvalidConversation):
    """No live Conversation with this ID belongs to the caller.

    A missing, another User's, deleted, or expired Conversation all read the
    same, so a caller learns nothing about Conversations it does not own.
    """

```

**3.** Find:

```python
    role: MessageRole
    text: str
    created_at: datetime
```

Replace with:

```python
    role: MessageRole
    text: str = field(repr=False)
    created_at: datetime
```

**4.** Find:

```python
            raise InvalidConversation("retention must be a positive duration")
        return cls(
```

Replace with:

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

**5.** Find:

```python
            last_message_at=opened_at,
            retention_deadline=opened_at + retention,
            deleted_at=None,
```

Replace with:

```python
            last_message_at=opened_at,
            retention_deadline=deadline,
            deleted_at=None,
```

**6.** Find:

```python
        return self.deleted_at is None and now >= self.retention_deadline
```

Replace with:

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

Check:

```bash
uv run pytest tests/test_conversations.py -q
uv run pyright src/knowledge_service/conversations.py
```

Expected: the existing tests pass, and pyright reports 0 errors.

### Step 3 — Coding step: the owner-scoped store

This step writes the four functions a User's request goes through.

**In plain words:** Starting a Conversation stores it with its deadline. Adding a Message first checks the text, then waits its turn on the Conversation, finds the next position, and refuses if the Conversation is full, missing, not the caller's, deleted, or expired. Reading returns the Conversation and its Messages in order only to its owner. Deleting marks it gone at once and asks a background job to remove it later.

| Function | What it does |
|---|---|
| `start_conversation` | Opens a Conversation for the owner, with the deadline from the limits |
| `add_message` | Checks the text, locks the live row, counts and numbers under the lock, stores the Message, moves `last_message_at` |
| `read_conversation` | The owner's live Conversation and its Messages, or None |
| `delete_conversation` | Sets `deleted_at` (never before the last Message) and queues one purge Job; True only for the call that deleted |
| `_live` | The one `WHERE`: this ID, this owner, not deleted, not past the deadline |
| `_utc`, `_owner`, `_id`, `_limits` | Type checks before any SQL; `now` turned into UTC |

In `src/knowledge_service/persistence/conversations.py`:

**1.** Find:

```python
"""Store Conversations, their Messages, and feedback on them."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast
```

Replace with:

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

**2.** Find:

```python
    ConversationFull,
    InvalidConversation,
```

Replace with:

```python
    ConversationFull,
    ConversationLimits,
    ConversationNotFound,
    InvalidConversation,
```

**3.** Find:

```python
    MessageRole,
)
from knowledge_service.identifiers import ConversationId, MessageId, UserId
from knowledge_service.persistence.schema import (
```

Replace with:

```python
    MessageRole,
    Transcript,
)
from knowledge_service.identifiers import ConversationId, JobId, MessageId, UserId
from knowledge_service.jobs import Job, JobKind
from knowledge_service.persistence.jobs import enqueue_job
from knowledge_service.persistence.schema import (
```

**4.** Find:

```python
    return tuple(_conversation_from(dict(record)) for record in records)
```

Replace with:

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

Two details matter here. `_live` is the only place that says what "visible to this caller" means, and every read and write uses it, so the rule cannot drift between functions. And the count of Messages is read after the lock is taken, in a new statement, so under READ COMMITTED it sees every Message committed by whoever held the lock before.

Check:

```bash
uv run python -c "from knowledge_service.persistence.conversations import add_message, delete_conversation"
uv run pyright src/knowledge_service/persistence/conversations.py
```

Expected: the import prints nothing, and pyright reports 0 errors.

### Step 4 — Coding step: tests for limits, ownership, order, and deletion

This step proves each promise, first without a database and then on real PostgreSQL.

**In plain words:** The unit tests check the limits and that bad input is refused before any SQL, using a fake connection that fails if it is ever used. The integration tests use the real database: they add Messages from several connections at once, read as another User, delete and then try again, and move the clock past the deadline.

| Test | What it proves |
|---|---|
| `test_messages_get_the_next_ordinal_and_read_back_in_order` | Success: ordinals 0, 1, 2 in order |
| `test_concurrent_messages_take_turns_for_the_next_ordinal` | Eight Messages at once get eight different ordinals |
| `test_concurrent_messages_never_pass_the_limit` | The limit holds under concurrency |
| `test_another_user_can_neither_read_add_nor_delete` | Ownership: the highest-risk edge |
| `test_missing_foreign_deleted_and_expired_look_the_same` | One answer, so nothing leaks |
| `test_deletion_is_immediate_and_queues_one_purge` | Immediate logical deletion, one purge Job |
| `test_a_delete_racing_a_message_leaves_no_message_after_it` | No Message lands after a delete |
| `test_a_conversation_past_its_deadline_reads_as_missing` | Retention at the exact deadline |
| `test_reading_then_writing_in_one_transaction_cannot_deadlock` | Read then write in one transaction is safe |
| `test_text_postgresql_cannot_store_is_refused_quietly` | Typed refusal, and no text in the error |
| `test_a_bad_message_is_refused_before_any_sql` | Bad input never reaches the database |

Create `tests/test_conversation_limits.py`:

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

Create `tests/test_conversation_store_integration.py`:

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

Check:

```bash
uv run pytest tests/test_conversation_limits.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_conversation_store_integration.py -m integration -q
```

Expected: 26 passed, then 19 passed.

### Step 5 — No coding in this step: break it and decide

This step shows what the row lock does, and records what you learned.

**Action 1:** break the order on purpose. In `src/knowledge_service/persistence/conversations.py`, in `add_message`, delete this line:

```python
                .with_for_update()
```

Run:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_conversation_store_integration.py -m integration -q
```

Expected: 4 failed, 15 passed. Without the lock, two Messages read the same highest ordinal, and the database's unique rule refuses the second with an untyped `IntegrityError`: eight Messages at once crash, only 2 of 3 Messages fit under the limit, the delete race fails, and reading then writing collides. The unique rule from M02 still keeps the data correct, but the caller gets a crash instead of its turn. Put the line back and run again until all pass.

**Action 2:** under `## Decision`, write:

- why ownership is checked inside the SQL and not after loading;
- why missing, another User's, deleted, and expired give one answer;
- why the database, under a lock, picks the ordinal;
- why deletion marks the row and queues a purge instead of removing rows at once;
- what is left for later: the purge worker (M08), building History from the Transcript (M06-T11);
- at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."

### Step 6 — No coding in this step: gates, evidence, and progress

This step proves everything still works and records the result. No diagram or decision became wrong: the tables and their rules are unchanged, so `docs/architecture.md` stays as it is.

**Action 1:** run the gates in order:

```bash
uv run pytest tests/test_conversation_limits.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2196 passed for `make test-unit`, 26 more than M06-T09, and 361 passed for `make test-integration`, 19 more. If it says there is no test database, start it with `docker start knowledge-service-postgres`.

**Action 2:** complete the evidence file:

- **Design:** the changed files; the owner-scoped functions and the one `WHERE` they share; how ordinals are given; how deletion works; the limits; time in UTC.
- **Verification:** the commands and counts, the deliberate break, and the known limits from "Before you begin".
- **Review:** an adversarial review found no ownership leak and no gap or duplicate in ordinals, and five problems, each fixed with a test that failed before the fix (8 tests), except the isolation level, now documented: reading with a shared lock then writing deadlocked; a deadline past the calendar raised an untyped error; a non-UTC zone moved the deadline by an hour; a deletion could be earlier than the last Message; a stricter isolation level turned waiting into serialization errors.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M06-T10 row to `docs/curriculum/PROGRESS.md`, set the next task to M06-T11, and commit with `m06-t10: persist bounded conversations`.

## Reflection questions

1. What does the owner-scoped store hide from the code that will answer a follow-up question? List what that code never has to check itself.
2. A later change wants to speed up `read_conversation` with a cache keyed by Conversation ID. Which promise could the cache break, and how would a test show it?
3. Why is it safer that `add_message` picks the ordinal than that the caller sends one? Describe a concrete bug the old way allows.
4. A User deletes a Conversation while their browser is still sending a question to it. What does each request see, and in what order can they end?
5. Which case would you add next to try to break ownership or ordering, and what result do you expect?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the code.
- [ ] `ConversationLimits` bounds retention, the number of Messages, and the length of one, and refuses unstorable text.
- [ ] Every owner-scoped function puts the owner in its SQL.
- [ ] Missing, another User's, deleted, and expired give the same answer.
- [ ] Concurrent Messages get distinct ordinals with no gap and never pass the limit.
- [ ] A deletion is visible at once and queues exactly one purge Job.
- [ ] Reading and then writing in one transaction cannot deadlock.
- [ ] `now` is turned into UTC; times never run backwards.
- [ ] No Message text reaches an error or a repr.
- [ ] You ran the deliberate break, saw the tests fail, and undid it.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
