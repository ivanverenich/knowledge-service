"""Tests for content-free audit events."""

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
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
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=None,
        run_id=None,
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
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=None,
        run_id=None,
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
            decision=AuditDecision.ALLOWED,
            outcome=AuditOutcome.SUCCEEDED,
            request_id=None,
            run_id=None,
            metadata={},
        )


def test_metadata_is_frozen_after_construction() -> None:
    event = make_event(kind="local_directory")

    with pytest.raises(TypeError):
        cast("dict[str, AuditValue]", event.metadata)["kind"] = "other"


@pytest.mark.parametrize(
    "value",
    [
        "How do I deploy?",
        "annual leave",
        "line\nbreak",
        "ключ",
        "a" * 65,
    ],
)
def test_a_sentence_is_not_a_label(value: str) -> None:
    with pytest.raises(InvalidAuditEvent):
        make_event(note=value)


@pytest.mark.parametrize("key", ["Title", "the question", "x" * 33, "1st", ""])
def test_a_metadata_key_must_be_a_short_snake_case_name(key: str) -> None:
    with pytest.raises(InvalidAuditEvent):
        make_event(**{key: 1})


def test_a_plain_string_is_not_a_decision() -> None:
    with pytest.raises(InvalidAuditEvent, match="decision"):
        AuditEvent(
            event_id=AuditEventId.new(),
            occurred_at=OCCURRED_AT,
            actor_id=USER_ID,
            action=AuditAction.DOCUMENT_READ,
            target_kind=AuditTargetKind.DOCUMENT,
            target_id=uuid4(),
            decision=cast(AuditDecision, "allowed"),
            outcome=AuditOutcome.SUCCEEDED,
            request_id=None,
            run_id=None,
            metadata={},
        )


def test_a_request_id_must_be_a_uuid() -> None:
    with pytest.raises(InvalidAuditEvent, match="request_id"):
        AuditEvent(
            event_id=AuditEventId.new(),
            occurred_at=OCCURRED_AT,
            actor_id=None,
            action=AuditAction.QUESTION_ASKED,
            target_kind=AuditTargetKind.REQUEST,
            target_id=uuid4(),
            decision=AuditDecision.ALLOWED,
            outcome=AuditOutcome.ANSWERED,
            request_id=cast(UUID, "00000000-0000-0000-0000-000000000001"),
            run_id=None,
            metadata={},
        )


@pytest.mark.parametrize(
    "value",
    [2**53, -(2**53), int.from_bytes(b"What is my salary?", "big"), 10**5000],
    # Named here: pytest cannot print the last one as a test ID.
    ids=["limit", "negative-limit", "spelled-text", "huge"],
)
def test_an_integer_too_large_to_be_a_count_is_rejected(value: int) -> None:
    with pytest.raises(InvalidAuditEvent, match="2\\*\\*53"):
        make_event(count=value)


class LyingMapping(Mapping[str, AuditValue]):
    """Says it holds one entry, but yields many."""

    def __len__(self) -> int:
        return 1

    def __iter__(self) -> Iterator[str]:
        return iter(f"key{index}" for index in range(200))

    def __getitem__(self, key: str) -> AuditValue:
        return 1


def test_a_mapping_cannot_hide_entries_behind_its_length() -> None:
    with pytest.raises(InvalidAuditEvent, match="at most"):
        make_event_with(LyingMapping())


def make_event_with(metadata: Mapping[str, AuditValue]) -> AuditEvent:
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=OCCURRED_AT,
        actor_id=None,
        action=AuditAction.JOB_QUEUED,
        target_kind=AuditTargetKind.JOB,
        target_id=uuid4(),
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=None,
        run_id=None,
        metadata=metadata,
    )
