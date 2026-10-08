"""Audit event values: what happened, without the content it happened to.

An event names an actor, an action, and a target, and it may carry a small bag
of short scalar labels. The bag is capped, so Question text, Answer text, or
Document text cannot be recorded here by accident.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from knowledge_service.identifiers import AuditEventId, UserId

MAX_METADATA_VALUE_LENGTH = 64
MAX_METADATA_ENTRIES = 8

AuditValue = str | int | bool


class InvalidAuditEvent(ValueError):
    """An audit event violates a domain invariant."""


class AuditAction(StrEnum):
    """What an actor did."""

    SOURCE_REGISTERED = "source.registered"
    DOCUMENT_AVAILABILITY_CHANGED = "document.availability_changed"
    CONVERSATION_DELETED = "conversation.deleted"
    JOB_QUEUED = "job.queued"
    JOB_FAILED = "job.failed"
    EVALUATION_RUN_STARTED = "evaluation_run.started"


class AuditTargetKind(StrEnum):
    """What an action was done to."""

    SOURCE = "source"
    DOCUMENT = "document"
    CONVERSATION = "conversation"
    JOB = "job"
    EVALUATION_RUN = "evaluation_run"


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidAuditEvent(f"{field_name} must be a UTC-aware timestamp")


def _checked_metadata(
    metadata: Mapping[str, AuditValue],
) -> Mapping[str, AuditValue]:
    if len(metadata) > MAX_METADATA_ENTRIES:
        raise InvalidAuditEvent(
            f"metadata holds at most {MAX_METADATA_ENTRIES} entries"
        )

    checked: dict[str, AuditValue] = {}
    for key, value in metadata.items():
        if not key.strip():
            raise InvalidAuditEvent("metadata keys must not be empty")
        if type(value) is str:
            if not value.strip():
                raise InvalidAuditEvent("metadata values must not be empty")
            if len(value) > MAX_METADATA_VALUE_LENGTH:
                raise InvalidAuditEvent(
                    "metadata values must be at most"
                    f" {MAX_METADATA_VALUE_LENGTH} characters"
                )
        elif type(value) is not int and type(value) is not bool:
            raise InvalidAuditEvent(
                "metadata values must be short strings, integers, or booleans"
            )
        checked[key] = value
    return MappingProxyType(checked)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One recorded action, with no raw content."""

    event_id: AuditEventId
    occurred_at: datetime
    actor_id: UserId | None
    action: AuditAction
    target_kind: AuditTargetKind
    target_id: UUID
    metadata: Mapping[str, AuditValue]

    def __post_init__(self) -> None:
        _require_utc(self.occurred_at, "occurred_at")
        object.__setattr__(self, "metadata", _checked_metadata(self.metadata))
