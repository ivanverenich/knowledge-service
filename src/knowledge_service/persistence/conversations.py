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
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.conversations import (
    Conversation,
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
    return (
        sa.select(conversations)
        .where(
            conversations.c.deleted_at.is_(None),
            conversations.c.retention_deadline <= now,
        )
        .order_by(conversations.c.retention_deadline)
        .limit(limit)
    )


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
    statement = due_conversations_statement(now=now, limit=limit)
    records = (await connection.execute(statement)).mappings().all()
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
