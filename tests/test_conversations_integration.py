"""Real PostgreSQL tests for Conversations and feedback."""

from datetime import UTC, datetime, timedelta

import pytest
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
