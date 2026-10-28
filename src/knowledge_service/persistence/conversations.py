"""Store Conversations, their Messages, and feedback on them."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.conversations import (
    Conversation,
    ConversationFull,
    InvalidConversation,
    Message,
    MessageFeedback,
    MessageRating,
    MessageRole,
)
from knowledge_service.identifiers import ConversationId, MessageId, UserId
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
