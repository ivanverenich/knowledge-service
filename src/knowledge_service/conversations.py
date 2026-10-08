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
