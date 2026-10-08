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
