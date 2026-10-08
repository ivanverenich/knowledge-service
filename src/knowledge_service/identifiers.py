"""Stable, provider-neutral identifiers for domain entities.

New internal IDs are created inside the application with ``new()``. Serialized
internal IDs may re-enter through transport and persistence adapters via
``parse()``. Provider-owned IDs may enter only through source or identity
adapters; they remain scoped to their owner and must never be cast into these
internal ID types.
"""

from dataclasses import dataclass
from typing import Self
from uuid import UUID, uuid7


class InvalidIdentifier(ValueError):
    """A serialized value is not a valid stable domain identifier."""

    def __init__(self, identifier_type: str) -> None:
        self.identifier_type = identifier_type
        super().__init__(f"Invalid {identifier_type} identifier")


@dataclass(frozen=True, slots=True)
class StableIdentifier:
    """Shared UUID behavior; callers should use a concrete identifier type."""

    value: UUID

    @classmethod
    def new(cls) -> Self:
        """Create a new project-owned identifier."""
        return cls(uuid7())

    @classmethod
    def parse(cls, value: str) -> Self:
        """Reconstruct a serialized internal identifier at a boundary."""
        try:
            return cls(UUID(value))
        except ValueError:
            raise InvalidIdentifier(cls.__name__) from None

    def serialize(self) -> str:
        """Return the canonical UUID representation for storage or transport."""
        return str(self.value)


class SourceId(StableIdentifier):
    """Internal identity of a Source."""


class DocumentId(StableIdentifier):
    """Internal identity of a Document."""


class ChunkId(StableIdentifier):
    """Internal identity of a Chunk."""


class UserId(StableIdentifier):
    """Internal identity of a User."""


class GroupId(StableIdentifier):
    """Internal identity of a Group."""


class AccessGrantId(StableIdentifier):
    """Internal identity of an Access Grant."""


class MessageId(StableIdentifier):
    """Internal identity of a Conversation Message."""


class ConversationId(StableIdentifier):
    """Internal identity of a Conversation."""


class SynchronizationRunId(StableIdentifier):
    """Internal identity of a Synchronization Run"""


class EvaluationDatasetId(StableIdentifier):
    """Internal identity of an Evaluation Dataset."""


class AuditEventId(StableIdentifier):
    """Internal identity of a recorded audit event."""


class EvaluationRunId(StableIdentifier):
    """Internal identity of an Evaluation Run."""


class JobId(StableIdentifier):
    """Internal identity of a durable job."""
