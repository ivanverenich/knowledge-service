"""Audit event values: what happened, without the content it happened to.

An event says who acted (`actor_id`, or None for the service itself), what
they did (`action`), to what (`target_kind` and `target_id`), whether it was
allowed (`decision`), how it ended (`outcome`), which request or run it
belongs to (`request_id`, `run_id`), and when (`occurred_at`).

What an event promises:

- **No content.** Every field is an identifier, a time, or a value from a
  fixed list. The small `metadata` bag holds at most eight labels: a key is
  a short snake_case name, and a value is an integer below 2**53 in size, a
  boolean, or one word of ASCII letters, digits, and `_ . : / -`. A
  Question, an Answer, a title, or a Document's text has spaces or other
  characters, so it cannot be recorded here by accident.
- **Checked types.** Each field must have its exact type, so a plain string
  never stands in for an action or a decision, and a time must be UTC.
- **Append-only.** Events are only ever added (see `persistence.audit`).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import cast
from uuid import UUID

from knowledge_service.identifiers import AuditEventId, UserId

MAX_METADATA_VALUE_LENGTH = 64
MAX_METADATA_ENTRIES = 8
# Integers stay exact in any JSON reader, and too small to spell a sentence.
MAX_METADATA_INTEGER = 2**53

AuditValue = str | int | bool

# A key is a short snake_case name; a string value is one plain word.
_KEY = re.compile(r"[a-z][a-z0-9_]{0,31}")
_WORD = re.compile(r"[A-Za-z0-9_.:/-]+")


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
    QUESTION_ASKED = "question.asked"
    DOCUMENT_READ = "document.read"
    DOCUMENT_ACCESS_CHANGED = "document.access_changed"
    SYNCHRONIZATION_FINISHED = "synchronization.finished"
    MESSAGE_RATED = "message.rated"
    EMBEDDING_MIGRATION_STARTED = "embedding_migration.started"
    EMBEDDING_MIGRATION_SWITCHED = "embedding_migration.switched"
    JOB_RETRIED = "job.retried"
    JOB_CANCELLED = "job.cancelled"
    SOURCE_UPDATED = "source.updated"
    SOURCE_ENABLED = "source.enabled"
    SOURCE_DISABLED = "source.disabled"


class AuditTargetKind(StrEnum):
    """What an action was done to."""

    SOURCE = "source"
    DOCUMENT = "document"
    CONVERSATION = "conversation"
    JOB = "job"
    EVALUATION_RUN = "evaluation_run"
    REQUEST = "request"
    MESSAGE = "message"
    EMBEDDING_MIGRATION = "embedding_migration"


class AuditDecision(StrEnum):
    """Whether the action was permitted."""

    ALLOWED = "allowed"
    DENIED = "denied"


class AuditOutcome(StrEnum):
    """How the action ended.

    `succeeded` and `failed` are for actions that either happen or not; the
    rest are how a streamed Answer can end.
    """

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ANSWERED = "answered"
    ABSTAINED = "abstained"
    REJECTED = "rejected"
    DENIED = "denied"
    DEPENDENCY_ERROR = "dependency_error"
    THROTTLED = "throttled"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"


def _require_utc(value: object, field_name: str) -> None:
    if type(value) is not datetime or value.utcoffset() != timedelta(0):
        raise InvalidAuditEvent(f"{field_name} must be a UTC-aware timestamp")


def _checked_metadata(metadata: object) -> Mapping[str, AuditValue]:
    if not isinstance(metadata, Mapping):
        raise InvalidAuditEvent("metadata must be a mapping")
    entries = cast("Mapping[object, object]", metadata)

    checked: dict[str, AuditValue] = {}
    # Counted while reading, so a mapping cannot hide entries behind its len.
    for key, value in entries.items():
        if len(checked) >= MAX_METADATA_ENTRIES:
            raise InvalidAuditEvent(
                f"metadata holds at most {MAX_METADATA_ENTRIES} entries"
            )
        if type(key) is not str or not key.strip():
            raise InvalidAuditEvent("metadata keys must not be empty")
        if _KEY.fullmatch(key) is None:
            raise InvalidAuditEvent("metadata keys must be short snake_case names")
        if type(value) is str:
            if not value.strip():
                raise InvalidAuditEvent("metadata values must not be empty")
            if len(value) > MAX_METADATA_VALUE_LENGTH:
                raise InvalidAuditEvent(
                    "metadata values must be at most"
                    f" {MAX_METADATA_VALUE_LENGTH} characters"
                )
            if _WORD.fullmatch(value) is None:
                raise InvalidAuditEvent("metadata values must be one plain word")
        elif type(value) is int:
            if not -MAX_METADATA_INTEGER < value < MAX_METADATA_INTEGER:
                raise InvalidAuditEvent("metadata integers must be below 2**53")
        elif type(value) is not bool:
            raise InvalidAuditEvent(
                "metadata values must be short strings, integers, or booleans"
            )
        checked[key] = value
    return MappingProxyType(checked)


def _require(value: object, expected: type, field_name: str) -> None:
    if type(value) is not expected:
        raise InvalidAuditEvent(f"{field_name} must be a {expected.__name__}")


def _require_optional(value: object, expected: type, field_name: str) -> None:
    if value is not None:
        _require(value, expected, field_name)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One recorded action, with no raw content."""

    event_id: AuditEventId
    occurred_at: datetime
    actor_id: UserId | None
    action: AuditAction
    target_kind: AuditTargetKind
    target_id: UUID
    decision: AuditDecision
    outcome: AuditOutcome
    request_id: UUID | None
    run_id: UUID | None
    metadata: Mapping[str, AuditValue]

    def __post_init__(self) -> None:
        _require(self.event_id, AuditEventId, "event_id")
        _require_utc(self.occurred_at, "occurred_at")
        _require_optional(self.actor_id, UserId, "actor_id")
        _require(self.action, AuditAction, "action")
        _require(self.target_kind, AuditTargetKind, "target_kind")
        _require(self.target_id, UUID, "target_id")
        _require(self.decision, AuditDecision, "decision")
        _require(self.outcome, AuditOutcome, "outcome")
        _require_optional(self.request_id, UUID, "request_id")
        _require_optional(self.run_id, UUID, "run_id")
        object.__setattr__(self, "metadata", _checked_metadata(self.metadata))
