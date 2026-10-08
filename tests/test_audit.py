"""Tests for content-free audit events."""

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest

from knowledge_service.audit import (
    AuditAction,
    AuditEvent,
    AuditTargetKind,
    AuditValue,
    InvalidAuditEvent,
)
from knowledge_service.identifiers import AuditEventId, UserId

OCCURRED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
USER_ID = UserId.parse("00000000-0000-0000-0000-000000000030")


def make_event(**metadata: object) -> AuditEvent:
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=OCCURRED_AT,
        actor_id=USER_ID,
        action=AuditAction.SOURCE_REGISTERED,
        target_kind=AuditTargetKind.SOURCE,
        target_id=uuid4(),
        metadata=cast("dict[str, AuditValue]", metadata),
    )


def test_an_event_records_short_labels() -> None:
    event = make_event(kind="local_directory", enabled=True, documents=12)

    assert event.metadata == {
        "kind": "local_directory",
        "enabled": True,
        "documents": 12,
    }
    assert event.actor_id == USER_ID


def test_an_actor_may_be_the_service_itself() -> None:
    event = AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=OCCURRED_AT,
        actor_id=None,
        action=AuditAction.JOB_FAILED,
        target_kind=AuditTargetKind.JOB,
        target_id=uuid4(),
        metadata={},
    )

    assert event.actor_id is None


def test_a_long_metadata_value_is_rejected() -> None:
    with pytest.raises(InvalidAuditEvent, match="at most"):
        make_event(question="How do I deploy the service?" * 10)


def test_a_structured_metadata_value_is_rejected() -> None:
    with pytest.raises(InvalidAuditEvent, match="short strings"):
        make_event(candidates=["chunk-1", "chunk-2"])


def test_an_empty_metadata_value_is_rejected() -> None:
    with pytest.raises(InvalidAuditEvent, match="must not be empty"):
        make_event(reason="")


def test_a_metadata_bag_has_a_ceiling() -> None:
    too_many = {f"key{index}": index for index in range(9)}

    with pytest.raises(InvalidAuditEvent, match="at most"):
        make_event(**too_many)


def test_a_naive_event_time_raises_typed_failure() -> None:
    with pytest.raises(InvalidAuditEvent, match="UTC-aware"):
        AuditEvent(
            event_id=AuditEventId.new(),
            occurred_at=datetime(2026, 10, 8, 9, 0),
            actor_id=USER_ID,
            action=AuditAction.CONVERSATION_DELETED,
            target_kind=AuditTargetKind.CONVERSATION,
            target_id=uuid4(),
            metadata={},
        )


def test_metadata_is_frozen_after_construction() -> None:
    event = make_event(kind="local_directory")

    with pytest.raises(TypeError):
        cast("dict[str, AuditValue]", event.metadata)["kind"] = "other"
