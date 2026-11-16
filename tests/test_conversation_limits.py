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
