# M02-T09 — Persist Conversations and feedback

Source task: [M02-T09](../curriculum/milestones/M02-domain-and-persistence/M02-T09-persist-conversations-and-feedback.md)

## What you will build

This lesson includes coding. It stores **Conversations**: one owned exchange between a User and the service, with the Messages in it and the thumbs ratings on those Messages.

A **Conversation** holds:

- the User who owns it;
- when it opened, when it last received a Message, and when it expires;
- the moment it was deleted, if it was.

Each **Message** holds its Conversation, its position in that Conversation, its role, its text, and when it was written. Each **rating** holds a Message, the User who gave it, the thumbs value, and when.

| Property | What it means |
|---|---|
| Owned | A Conversation carries its owner, and only that User may act on it. |
| Ordered | One Message per ordinal per Conversation; reads come back in ordinal order. |
| Bounded and expiring | A Conversation refuses a Message past its bound, and carries the deadline that selects it for deletion. |
| One rating | A Message holds at most one rating per User. Rating again replaces it. |

## Before you begin

- `src/knowledge_service/identifiers.py` has `ConversationId`. It has no identifier for a Message, so you add one.
- `src/knowledge_service/persistence.py` holds the `documents`, `chunks`, and `access_grants` tables plus their functions. This lesson adds to that module.
- `access.py` shows the pattern for a value with a rule inside it: frozen dataclass, validation in `__post_init__`, one typed `ValueError` per module.
- There is no `users` table yet, so `owner_id` is a plain UUID column with no foreign key. Identity arrives in M07.
- Integration tests apply migrations in a **synchronous** fixture, because `migrations/env.py` calls `asyncio.run()` and cannot run inside an async test.

## Walkthrough

### Step 1 — No coding in this step: write down the rule you are preserving

**Purpose:** Fix what a stored Conversation must guarantee before writing the tables.

**Decision:** Keep notes in `docs/evidence/M02-T09-conversations-and-feedback.md`.

**Action:** Read the M02-T08 evidence file under `docs/evidence/`. Create `docs/evidence/M02-T09-conversations-and-feedback.md`:

```markdown
# M02-T09 — Conversations and feedback evidence

Completed: pending

## Invariant

Write down who a Conversation belongs to, how its Messages stay ordered and
bounded, how a query selects the Conversations due for deletion, and what
"one rating policy" means.

## Prediction

Write what you expect before adding the tables.

## Verification

Pending.

## Reflection

Pending.
```

Confirm nothing to be created exists:

```bash
find src tests migrations -name '*conversation*' -o -name '*message*' -o -name '*feedback*' | grep -v __pycache__
```

**Check:** The evidence file holds your invariant and prediction, and the search shows no `conversations.py`, no migration, and no conversation table in `persistence.py`. Add no code and edit no task or progress files in this step.

### Step 2 — Coding step: add the Message identifier

**Purpose:** Give a Message a typed identity, like every other entity in the project.

**Decision:** Add `MessageId` next to `ConversationId`. It uses the shared `StableIdentifier` base, so it gets UUIDv7 creation, parsing, and serialization for free.

**Action:** In `src/knowledge_service/identifiers.py`, add before `ConversationId`:

```python
class MessageId(StableIdentifier):
    """Internal identity of a Conversation Message."""
```

Then in `tests/test_indentifiers.py`, add `MessageId` to the import list and to `ID_TYPES`. The existing parametrized tests then cover the new type: round trip, type mismatch, invalid input, and UUIDv7.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_indentifiers.py -q
```

### Step 3 — Coding step: model the Conversation, its Messages, and a rating

**Purpose:** Put the ownership, lifetime, and rating rules where a caller cannot get them wrong.

**Decision:** Three values and one typed error family. `Conversation.open()` computes the retention deadline from a duration, so no caller does date arithmetic. `require_owner()` refuses a caller who does not own the Conversation. `record_message()` advances the last-message time and refuses a time that moves backwards. `Message` carries its position and text. `MessageFeedback` carries one thumbs value. The named failures — `NotConversationOwner`, `ConversationFull` — are separate classes, so a caller can handle each without matching on a message.

**Action:** Create `src/knowledge_service/conversations.py`:

```python
"""Conversation values: a bounded, owned exchange with a retention deadline.

A Conversation holds the User's questions and the Answers generated for them.
It belongs to one User, it is bounded in length, and it carries the moment it
expires so a later task can delete it without guessing.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from knowledge_service.identifiers import ConversationId, MessageId, UserId


class InvalidConversation(ValueError):
    """A Conversation, Message, or feedback value violates a domain invariant."""


class NotConversationOwner(InvalidConversation):
    """A caller tried to act on a Conversation it does not own."""


class ConversationFull(InvalidConversation):
    """A Conversation already holds as many Messages as its bound allows."""


class MessageRole(StrEnum):
    """Who produced a Message."""

    USER = "user"
    ASSISTANT = "assistant"


class MessageRating(StrEnum):
    """The thumbs signal a User attaches to one Message."""

    UP = "up"
    DOWN = "down"


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidConversation(f"{field_name} must be a UTC-aware timestamp")


@dataclass(frozen=True, slots=True)
class Message:
    """One turn in a Conversation."""

    message_id: MessageId
    conversation_id: ConversationId
    ordinal: int
    role: MessageRole
    text: str
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

    Feedback is a signal to review, never a statement of truth.
    """

    message_id: MessageId
    user_id: UserId
    rating: MessageRating
    rated_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.rated_at, "rated_at")


@dataclass(frozen=True, slots=True)
class Conversation:
    """One owned, bounded exchange with a retention deadline."""

    conversation_id: ConversationId
    owner_id: UserId
    created_at: datetime
    last_message_at: datetime
    retention_deadline: datetime
    deleted_at: datetime | None

    def __post_init__(self) -> None:
        _require_utc(self.created_at, "created_at")
        _require_utc(self.last_message_at, "last_message_at")
        _require_utc(self.retention_deadline, "retention_deadline")
        if self.last_message_at < self.created_at:
            raise InvalidConversation(
                "last_message_at must not be earlier than created_at"
            )
        if self.retention_deadline <= self.created_at:
            raise InvalidConversation(
                "retention_deadline must be later than created_at"
            )
        if self.deleted_at is not None:
            _require_utc(self.deleted_at, "deleted_at")
            if self.deleted_at < self.created_at:
                raise InvalidConversation(
                    "deleted_at must not be earlier than created_at"
                )

    @classmethod
    def open(
        cls,
        *,
        conversation_id: ConversationId,
        owner_id: UserId,
        opened_at: datetime,
        retention: timedelta,
    ) -> Conversation:
        """Start a Conversation that expires `retention` after it opens."""
        if retention <= timedelta(0):
            raise InvalidConversation("retention must be a positive duration")
        return cls(
            conversation_id=conversation_id,
            owner_id=owner_id,
            created_at=opened_at,
            last_message_at=opened_at,
            retention_deadline=opened_at + retention,
            deleted_at=None,
        )

    def require_owner(self, user_id: UserId) -> Conversation:
        """Return this Conversation, or refuse a caller who does not own it."""
        if user_id != self.owner_id:
            raise NotConversationOwner("only the owner may act on this conversation")
        return self

    def record_message(self, *, observed_at: datetime) -> Conversation:
        """Return this Conversation with its last-message time advanced."""
        _require_utc(observed_at, "observed_at")
        if observed_at < self.last_message_at:
            raise InvalidConversation(
                "observed_at must not be earlier than last_message_at"
            )
        return replace(self, last_message_at=observed_at)

    def delete(self, *, deleted_at: datetime) -> Conversation:
        """Return this Conversation marked deleted, keeping its deadline."""
        _require_utc(deleted_at, "deleted_at")
        if self.deleted_at is not None:
            return self
        if deleted_at < self.created_at:
            raise InvalidConversation("deleted_at must not be earlier than created_at")
        return replace(self, deleted_at=deleted_at)

    def is_due_for_deletion(self, *, now: datetime) -> bool:
        """Whether the retention deadline has passed on a live Conversation."""
        _require_utc(now, "now")
        return self.deleted_at is None and now >= self.retention_deadline
```

`delete()` returns the same Conversation when it is already deleted, so a repeated delete is harmless. `is_due_for_deletion()` is the domain mirror of the query you write in Step 6.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python -c "
from datetime import UTC, datetime, timedelta
from knowledge_service.conversations import Conversation
from knowledge_service.identifiers import ConversationId, UserId
c = Conversation.open(conversation_id=ConversationId.new(), owner_id=UserId.new(), opened_at=datetime(2026,10,8,9,0,tzinfo=UTC), retention=timedelta(days=30))
print(c.retention_deadline, c.deleted_at)
"
```

### Step 4 — Coding step: create the three tables

**Purpose:** Store the Conversation, its Messages, and its ratings where SQL can select on them.

**Decision:** `messages` carries `UNIQUE (conversation_id, ordinal)`, so two Messages cannot claim one position, and a cascading foreign key to `conversations`. `message_feedback` uses a composite primary key of `(message_id, user_id)`: the row's identity *is* the Message-and-User pair, which is what makes "one rating per Message per User" true rather than merely intended. Each table carries the CHECK constraints its domain value already enforces.

**Action:** Generate an empty revision, then write it by hand:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic revision -m "conversations and feedback"
```

Keep the generated identifier and the `down_revision` Alembic fills in — `9f4e3aa29479`. Remove the generated `typing` and `branch_labels` lines as before, and replace `<the identifier Alembic generated>` with the real identifier.

```python
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "<the identifier Alembic generated>"
down_revision: str | Sequence[str] | None = "9f4e3aa29479"


def upgrade() -> None:
    """Create owned Conversations, their Messages, and one rating per Message."""
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "retention_deadline > created_at", name="ck_conversations_retention"
        ),
        sa.CheckConstraint(
            "last_message_at >= created_at", name="ck_conversations_last_message"
        ),
        sa.CheckConstraint(
            "deleted_at is null or deleted_at >= created_at",
            name="ck_conversations_deleted_at",
        ),
    )

    op.create_table(
        "messages",
        sa.Column("message_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
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

    op.create_table(
        "message_feedback",
        sa.Column(
            "message_id",
            sa.Uuid(),
            sa.ForeignKey("messages.message_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("rating", sa.String(length=8), nullable=False),
        sa.Column("rated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "rating in ('up', 'down')", name="ck_message_feedback_rating"
        ),
    )


def downgrade() -> None:
    """Remove stored feedback, Messages, and Conversations."""
    op.drop_table("message_feedback")
    op.drop_table("messages")
    op.drop_table("conversations")
```

Then add the matching tables to `src/knowledge_service/persistence.py`, after the `access_grants` table:

```python
conversations = sa.Table(
    "conversations",
    metadata,
    sa.Column("conversation_id", sa.Uuid(), primary_key=True),
    sa.Column("owner_id", sa.Uuid(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("retention_deadline", sa.DateTime(timezone=True), nullable=False),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(
        "retention_deadline > created_at", name="ck_conversations_retention"
    ),
    sa.CheckConstraint(
        "last_message_at >= created_at", name="ck_conversations_last_message"
    ),
    sa.CheckConstraint(
        "deleted_at is null or deleted_at >= created_at",
        name="ck_conversations_deleted_at",
    ),
)

messages = sa.Table(
    "messages",
    metadata,
    sa.Column("message_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "conversation_id",
        sa.Uuid(),
        sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
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
        "message_id",
        sa.Uuid(),
        sa.ForeignKey("messages.message_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("user_id", sa.Uuid(), primary_key=True),
    sa.Column("rating", sa.String(length=8), nullable=False),
    sa.Column("rated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("rating in ('up', 'down')", name="ck_message_feedback_rating"),
)
```

**Check:** Six revisions, one head, and both definitions agree:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic history
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python -c "
from knowledge_service.persistence import conversations, messages, message_feedback
for table in (conversations, messages, message_feedback):
    print(table.name, [c.name for c in table.columns], sorted(c.name for c in table.constraints if c.name))
"
```

### Step 5 — Coding step: open, read, and extend a Conversation

**Purpose:** Provide one way to start a Conversation, one way to read it, and one way to add a Message that respects the bound and the ordering.

**Decision:** Four functions on the caller's connection, none of them committing. `append_message` counts the stored Messages and raises `ConversationFull` at the bound, then inserts the Message and advances the Conversation's last-message time through `record_message()`, so the domain decides whether the time may move.

**Action:** In `src/knowledge_service/persistence.py`, extend the imports:

```python
from knowledge_service.conversations import (
    Conversation,
    ConversationFull,
    InvalidConversation,
    Message,
    MessageFeedback,
    MessageRating,
    MessageRole,
)
from knowledge_service.identifiers import (
    AccessGrantId,
    ChunkId,
    ConversationId,
    DocumentId,
    GroupId,
    MessageId,
    SourceId,
    UserId,
)
```

Add the row helpers next to the access-grant ones:

```python
def _conversation_row(conversation: Conversation) -> dict[str, object]:
    return {
        "conversation_id": conversation.conversation_id.value,
        "owner_id": conversation.owner_id.value,
        "created_at": conversation.created_at,
        "last_message_at": conversation.last_message_at,
        "retention_deadline": conversation.retention_deadline,
        "deleted_at": conversation.deleted_at,
    }


def _conversation_from(record: Mapping[str, object]) -> Conversation:
    return Conversation(
        conversation_id=ConversationId(cast(UUID, record["conversation_id"])),
        owner_id=UserId(cast(UUID, record["owner_id"])),
        created_at=cast(datetime, record["created_at"]),
        last_message_at=cast(datetime, record["last_message_at"]),
        retention_deadline=cast(datetime, record["retention_deadline"]),
        deleted_at=cast("datetime | None", record["deleted_at"]),
    )


def _message_row(message: Message) -> dict[str, object]:
    return {
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
```

Then add the four functions:

```python
async def create_conversation(
    connection: AsyncConnection, conversation: Conversation
) -> ConversationId:
    """Store a new Conversation."""
    await connection.execute(
        sa.insert(conversations).values(_conversation_row(conversation))
    )
    return conversation.conversation_id


async def load_conversation(
    connection: AsyncConnection,
    *,
    conversation_id: ConversationId,
) -> Conversation | None:
    """Read a stored Conversation, if it exists."""
    statement = sa.select(conversations).where(
        conversations.c.conversation_id == conversation_id.value
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _conversation_from(dict(record))


async def append_message(
    connection: AsyncConnection,
    *,
    conversation: Conversation,
    message: Message,
    message_limit: int,
) -> Message:
    """Append one Message, refusing a Conversation already at its bound."""
    if message.conversation_id != conversation.conversation_id:
        raise InvalidConversation("the message must belong to the conversation")

    stored = cast(
        int,
        await connection.scalar(
            sa.select(sa.func.count())
            .select_from(messages)
            .where(messages.c.conversation_id == conversation.conversation_id.value)
        ),
    )
    if stored >= message_limit:
        raise ConversationFull(f"the conversation already holds {stored} messages")

    await connection.execute(sa.insert(messages).values(_message_row(message)))
    advanced = conversation.record_message(observed_at=message.created_at)
    await connection.execute(
        sa.update(conversations)
        .where(conversations.c.conversation_id == conversation.conversation_id.value)
        .values(last_message_at=advanced.last_message_at)
    )
    return message


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
```

`order_by` on the read is what makes "ordered" true for callers instead of an accident of insertion order. `message_limit` is a parameter, not a constant, so the bound belongs to whoever opens the Conversation.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright src/knowledge_service/persistence.py
```

### Step 6 — Coding step: apply one rating policy and select by retention

**Purpose:** Settle the rating policy in SQL, and let the database find the Conversations that are due.

**Decision:** `rate_message` inserts with `ON CONFLICT DO UPDATE` on `(message_id, user_id)`. A first rating inserts, a second one overwrites the rating and its time, so "the latest signal wins" needs no read first and cannot race. `load_conversations_due_for_deletion` selects the live Conversations whose deadline has passed, oldest first, and takes a `limit` so a later cleaner can work in batches.

**Action:** Append to `src/knowledge_service/persistence.py`:

```python
def _feedback_row(feedback: MessageFeedback) -> dict[str, object]:
    return {
        "message_id": feedback.message_id.value,
        "user_id": feedback.user_id.value,
        "rating": feedback.rating.value,
        "rated_at": feedback.rated_at,
    }


def _feedback_from(record: Mapping[str, object]) -> MessageFeedback:
    try:
        rating = MessageRating(cast(str, record["rating"]))
    except ValueError:
        raise InvalidConversation("stored rating is not a known value") from None

    return MessageFeedback(
        message_id=MessageId(cast(UUID, record["message_id"])),
        user_id=UserId(cast(UUID, record["user_id"])),
        rating=rating,
        rated_at=cast(datetime, record["rated_at"]),
    )


async def rate_message(
    connection: AsyncConnection, *, feedback: MessageFeedback
) -> MessageFeedback:
    """Store one rating per Message per User, replacing any earlier rating."""
    statement = insert(message_feedback).values(_feedback_row(feedback))
    statement = statement.on_conflict_do_update(
        index_elements=["message_id", "user_id"],
        set_={
            "rating": statement.excluded.rating,
            "rated_at": statement.excluded.rated_at,
        },
    )
    await connection.execute(statement)
    return feedback


async def load_message_feedback(
    connection: AsyncConnection, *, message_id: MessageId
) -> tuple[MessageFeedback, ...]:
    """Read the current ratings attached to one Message."""
    statement = (
        sa.select(message_feedback)
        .where(message_feedback.c.message_id == message_id.value)
        .order_by(message_feedback.c.rated_at, message_feedback.c.user_id)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_feedback_from(dict(record)) for record in records)


async def load_conversations_due_for_deletion(
    connection: AsyncConnection, *, now: datetime, limit: int
) -> tuple[Conversation, ...]:
    """Select live Conversations whose retention deadline has passed."""
    statement = (
        sa.select(conversations)
        .where(
            conversations.c.deleted_at.is_(None),
            conversations.c.retention_deadline <= now,
        )
        .order_by(conversations.c.retention_deadline)
        .limit(limit)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_conversation_from(dict(record)) for record in records)
```

The two `deleted_at is null` conditions matter: a Conversation the User already deleted must not be selected again, because the deadline alone cannot tell a deleted Conversation from a live one.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright src/knowledge_service/persistence.py
```

### Step 7 — Coding step: test the rules without a database

**Purpose:** Pin down the ownership, lifetime, and value rules, which are pure logic.

**Decision:** Test through the public interface. The successful case proves `open()` computes the deadline. The edge case is the exact deadline boundary. The typed failures cover a non-owner, a backwards message time, a non-positive retention, and empty text.

**Action:** Create `tests/test_conversations.py`:

```python
"""Tests for Conversation, Message, and feedback values."""

from datetime import UTC, datetime, timedelta

import pytest

from knowledge_service.conversations import (
    Conversation,
    InvalidConversation,
    Message,
    MessageFeedback,
    MessageRating,
    MessageRole,
    NotConversationOwner,
)
from knowledge_service.identifiers import ConversationId, MessageId, UserId

OBSERVED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
OWNER_ID = UserId.parse("00000000-0000-0000-0000-000000000030")
OTHER_USER_ID = UserId.parse("00000000-0000-0000-0000-000000000031")
CONVERSATION_ID = ConversationId.parse("00000000-0000-0000-0000-000000000050")
RETENTION = timedelta(days=30)


def open_conversation() -> Conversation:
    return Conversation.open(
        conversation_id=CONVERSATION_ID,
        owner_id=OWNER_ID,
        opened_at=OBSERVED_AT,
        retention=RETENTION,
    )


def make_message(*, ordinal: int, text: str = "How do we deploy?") -> Message:
    return Message(
        message_id=MessageId.new(),
        conversation_id=CONVERSATION_ID,
        ordinal=ordinal,
        role=MessageRole.USER,
        text=text,
        created_at=OBSERVED_AT,
    )


def test_opening_a_conversation_sets_its_retention_deadline() -> None:
    conversation = open_conversation()

    assert conversation.retention_deadline == OBSERVED_AT + RETENTION
    assert conversation.last_message_at == OBSERVED_AT
    assert conversation.deleted_at is None


def test_only_the_owner_may_act_on_a_conversation() -> None:
    conversation = open_conversation()

    assert conversation.require_owner(OWNER_ID) is conversation

    with pytest.raises(NotConversationOwner, match="owner"):
        conversation.require_owner(OTHER_USER_ID)


def test_a_conversation_is_due_only_after_its_deadline() -> None:
    conversation = open_conversation()

    assert not conversation.is_due_for_deletion(now=OBSERVED_AT)
    assert not conversation.is_due_for_deletion(
        now=conversation.retention_deadline - timedelta(seconds=1)
    )
    assert conversation.is_due_for_deletion(now=conversation.retention_deadline)
    assert not conversation.delete(
        deleted_at=OBSERVED_AT + timedelta(days=1)
    ).is_due_for_deletion(now=conversation.retention_deadline)


def test_a_conversation_advances_to_the_latest_message_time() -> None:
    conversation = open_conversation()
    later = OBSERVED_AT + timedelta(minutes=5)

    assert conversation.record_message(observed_at=later).last_message_at == later

    with pytest.raises(InvalidConversation, match="earlier"):
        conversation.record_message(observed_at=OBSERVED_AT - timedelta(minutes=1))


def test_a_non_positive_retention_is_rejected() -> None:
    with pytest.raises(InvalidConversation, match="positive"):
        Conversation.open(
            conversation_id=CONVERSATION_ID,
            owner_id=OWNER_ID,
            opened_at=OBSERVED_AT,
            retention=timedelta(0),
        )


def test_an_empty_message_raises_typed_failure() -> None:
    with pytest.raises(InvalidConversation, match="text"):
        make_message(ordinal=0, text="   ")


def test_feedback_carries_a_thumbs_rating() -> None:
    feedback = MessageFeedback(
        message_id=MessageId.new(),
        user_id=OWNER_ID,
        rating=MessageRating.DOWN,
        rated_at=OBSERVED_AT,
    )

    assert feedback.rating is MessageRating.DOWN
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_conversations.py -vv
```

Expected: 7 passed, without a database.

### Step 8 — Coding step: prove ordering, retention selection, and the rating policy

**Purpose:** A pure test cannot show that reads come back in order, that the retention query picks the right rows, or that a second rating replaces the first.

**Decision:** One async integration test over three Conversations and three Messages: one Conversation is live, one expired, one expired but already deleted. It then proves the bound refuses a fourth Message, that a duplicate ordinal is rejected, and that rating a Message twice leaves one row with the latest value while a second User gets their own row.

**Action:** Create `tests/test_conversations_integration.py`:

```python
"""Real PostgreSQL tests for Conversations and feedback."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from knowledge_service.conversations import (
    Conversation,
    ConversationFull,
    Message,
    MessageFeedback,
    MessageRating,
    MessageRole,
)
from knowledge_service.identifiers import ConversationId, MessageId, UserId
from knowledge_service.persistence import (
    append_message,
    create_conversation,
    load_conversation,
    load_conversations_due_for_deletion,
    load_message_feedback,
    load_messages,
    rate_message,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
RETENTION = timedelta(days=30)
OWNER_ID = UserId.parse("00000000-0000-0000-0000-000000000030")
OTHER_USER_ID = UserId.parse("00000000-0000-0000-0000-000000000031")
CONVERSATION_ID = ConversationId.parse("00000000-0000-0000-0000-000000000050")
SECOND_CONVERSATION_ID = ConversationId.parse("00000000-0000-0000-0000-000000000051")
THIRD_CONVERSATION_ID = ConversationId.parse("00000000-0000-0000-0000-000000000052")

INSERT_RAW_MESSAGE = text(
    "INSERT INTO messages (message_id, conversation_id, ordinal, role, text,"
    " created_at) VALUES (:message_id, :conversation_id, :ordinal, :role,"
    " 'raw', now())"
)


@pytest.fixture
def migrated_database(monkeypatch: pytest.MonkeyPatch) -> str:
    """Apply migrations synchronously; Alembic's env.py runs its own event loop."""
    database_url = os.environ.get("KNOWLEDGE_SERVICE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set KNOWLEDGE_SERVICE_TEST_DATABASE_URL to a disposable database")

    monkeypatch.setenv("KNOWLEDGE_SERVICE_DATABASE_URL", database_url)
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    return database_url


def open_conversation(
    conversation_id: ConversationId,
    *,
    opened_at: datetime = OBSERVED_AT,
    retention: timedelta = RETENTION,
) -> Conversation:
    return Conversation.open(
        conversation_id=conversation_id,
        owner_id=OWNER_ID,
        opened_at=opened_at,
        retention=retention,
    )


def make_message(
    *, conversation_id: ConversationId, ordinal: int, role: MessageRole
) -> Message:
    return Message(
        message_id=MessageId.new(),
        conversation_id=conversation_id,
        ordinal=ordinal,
        role=role,
        text=f"turn {ordinal}",
        created_at=OBSERVED_AT + timedelta(seconds=ordinal),
    )


@pytest.mark.integration
async def test_messages_keep_their_order_and_retention_selects_by_deadline(
    migrated_database: str,
) -> None:
    engine = create_async_engine(
        make_url(migrated_database).set(drivername="postgresql+psycopg")
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM message_feedback"))
            await connection.execute(text("DELETE FROM messages"))
            await connection.execute(text("DELETE FROM conversations"))

        live = open_conversation(CONVERSATION_ID)
        expired = open_conversation(
            SECOND_CONVERSATION_ID, opened_at=OBSERVED_AT - timedelta(days=60)
        )
        removed = open_conversation(
            THIRD_CONVERSATION_ID,
            opened_at=OBSERVED_AT - timedelta(days=40),
        ).delete(deleted_at=OBSERVED_AT - timedelta(days=1))

        async with engine.begin() as connection:
            await create_conversation(connection, live)
            await create_conversation(connection, expired)
            await create_conversation(connection, removed)
            await append_message(
                connection,
                conversation=live,
                message=make_message(
                    conversation_id=CONVERSATION_ID, ordinal=0, role=MessageRole.USER
                ),
                message_limit=4,
            )
            await append_message(
                connection,
                conversation=live,
                message=make_message(
                    conversation_id=CONVERSATION_ID,
                    ordinal=1,
                    role=MessageRole.ASSISTANT,
                ),
                message_limit=4,
            )
            await append_message(
                connection,
                conversation=live,
                message=make_message(
                    conversation_id=CONVERSATION_ID, ordinal=2, role=MessageRole.USER
                ),
                message_limit=4,
            )

        async with engine.begin() as connection:
            stored = await load_messages(connection, conversation_id=CONVERSATION_ID)
            reloaded = await load_conversation(
                connection, conversation_id=CONVERSATION_ID
            )

        assert [message.ordinal for message in stored] == [0, 1, 2]
        assert [message.role for message in stored] == [
            MessageRole.USER,
            MessageRole.ASSISTANT,
            MessageRole.USER,
        ]
        assert reloaded is not None
        assert reloaded.last_message_at == OBSERVED_AT + timedelta(seconds=2)

        async with engine.begin() as connection:
            due = await load_conversations_due_for_deletion(
                connection, now=OBSERVED_AT, limit=10
            )

        assert [conversation.conversation_id for conversation in due] == [
            SECOND_CONVERSATION_ID
        ]

        async with engine.begin() as connection:
            with pytest.raises(ConversationFull):
                await append_message(
                    connection,
                    conversation=live,
                    message=make_message(
                        conversation_id=CONVERSATION_ID,
                        ordinal=3,
                        role=MessageRole.ASSISTANT,
                    ),
                    message_limit=3,
                )

        async with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    INSERT_RAW_MESSAGE,
                    {
                        "message_id": MessageId.new().value,
                        "conversation_id": CONVERSATION_ID.value,
                        "ordinal": 2,
                        "role": "assistant",
                    },
                )

        message_id = stored[1].message_id
        async with engine.begin() as connection:
            await rate_message(
                connection,
                feedback=MessageFeedback(
                    message_id=message_id,
                    user_id=OWNER_ID,
                    rating=MessageRating.UP,
                    rated_at=OBSERVED_AT,
                ),
            )
            await rate_message(
                connection,
                feedback=MessageFeedback(
                    message_id=message_id,
                    user_id=OWNER_ID,
                    rating=MessageRating.DOWN,
                    rated_at=OBSERVED_AT + timedelta(minutes=1),
                ),
            )
            await rate_message(
                connection,
                feedback=MessageFeedback(
                    message_id=message_id,
                    user_id=OTHER_USER_ID,
                    rating=MessageRating.UP,
                    rated_at=OBSERVED_AT + timedelta(minutes=2),
                ),
            )

        async with engine.begin() as connection:
            ratings = await load_message_feedback(connection, message_id=message_id)

        assert len(ratings) == 2
        assert {rating.user_id for rating in ratings} == {OWNER_ID, OTHER_USER_ID}
        assert {rating.user_id: rating.rating for rating in ratings}[
            OWNER_ID
        ] is MessageRating.DOWN
    finally:
        await engine.dispose()
```

The two ratings from the owner are the policy proof: after rating `UP` and then `DOWN`, the row count for that User is one and the value is `DOWN`. Two rows overall is the other half — a second User is a second rating, not an overwrite.

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_conversations_integration.py -vv
```

Expected: 1 passed. Run it twice to confirm it repeats. Do not paste the URL into the evidence file or commit it.

### Step 9 — No coding in this step: run the gates and record the evidence

**Purpose:** Confirm the lesson works as a whole before M02-T10 builds on it.

**Decision:** Run the focused tests first, then every repository gate. The normal gate skips the opt-in PostgreSQL tests, so run those separately and record both results.

**Action:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_conversations.py tests/test_indentifiers.py -vv
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration -vv
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
git diff --check
```

In `docs/evidence/M02-T09-conversations-and-feedback.md`, replace the placeholders with the date, your invariant, the files you changed, the exact observed results, the PostgreSQL version, and the answers below. Add no credential, connection URL, or private content.

**Reflection — answer in your own words:**

1. `Conversation.open()` turns a duration into a deadline, and `require_owner()` refuses a caller who does not own the Conversation. Name what those two take off the caller's hands, and say what a caller would have to check for itself if they did not exist.
2. The rating policy could have been "keep every rating and pick the newest on read" instead. Describe what gets harder later if we did that — think about a query that must decide, inside SQL, which rating to trust for one Message.
3. The integration test asserts that a second Message with ordinal `2` is rejected. If we removed that assertion, what production failure might go unnoticed until someone read an Answer out of order?

**Check:** The unit tests and every integration test pass; all repository gates pass; the evidence records the real results and no connection secret.

## Completion checklist

- [ ] `MessageId` exists in `identifiers.py` and is covered by the identifier tests.
- [ ] `src/knowledge_service/conversations.py` models an owned, expiring Conversation, a positioned Message, and a thumbs rating, and imports no SQLAlchemy.
- [ ] One hand-written revision creates `conversations`, `messages`, and `message_feedback`, with the ordering unique constraint, the composite rating key, and cascading foreign keys.
- [ ] `append_message` enforces the bound and advances the last-message time through the domain; `load_messages` returns ordinal order.
- [ ] `rate_message` keeps one rating per Message per User, and `load_conversations_due_for_deletion` skips deleted Conversations.
- [ ] Repository checks pass and the evidence records real results.

Share your implementation or any error you hit and I will review it.
