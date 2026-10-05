from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from knowledge_service.identifiers import DocumentId, SourceId


class InvalidDocument(ValueError):
    """A Document value or state transition violates a domain invariant."""


@dataclass(frozen=True, slots=True)
class ContentVersion:
    """Positive version number for a Document's text and structure."""

    number: int

    def __post_init__(self) -> None:
        if type(self.number) is not int or self.number < 1:
            raise InvalidDocument("content version must be a positive integer")

    def next(self) -> ContentVersion:
        return ContentVersion(self.number + 1)


@dataclass(frozen=True, slots=True)
class AuthorizationVersion:
    """Positive version number for a Document's access grants."""

    number: int

    def __post_init__(self) -> None:
        if type(self.number) is not int or self.number < 1:
            raise InvalidDocument("authorization version must be a positive integer")

    def next(self) -> AuthorizationVersion:
        return AuthorizationVersion(self.number + 1)


class DocumentAvailability(StrEnum):
    """Whether the Document may currently be retrieved."""

    AVAILABLE = "available"
    TOMBSTONED = "tombstoned"


@dataclass(frozen=True, slots=True)
class DocumentProvenance:
    """Source-owned identity and version used to locate the original item."""

    source_id: SourceId
    external_id: str
    source_version: str | None = None

    def __post_init__(self) -> None:
        if not self.external_id.strip():
            raise InvalidDocument("external source ID must not be empty")
        if self.source_version is not None and not self.source_version.strip():
            raise InvalidDocument("source version must not be empty")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidDocument(f"{field_name} must be a UTC-aware timestamp")


@dataclass(frozen=True, slots=True)
class Document:
    """Current provider-neutral state for one stable source item."""

    document_id: DocumentId
    provenance: DocumentProvenance
    content_version: ContentVersion
    content_fingerprint: str
    authorization_version: AuthorizationVersion
    authorization_fingerprint: str
    availability: DocumentAvailability
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.content_fingerprint.strip():
            raise InvalidDocument("content fingerprint must not be empty")
        if not self.authorization_fingerprint.strip():
            raise InvalidDocument("authorization fingerprint must not be empty")
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise InvalidDocument("updated_at must not be earlier than created_at")

    @classmethod
    def register(
        cls,
        *,
        document_id: DocumentId,
        provenance: DocumentProvenance,
        content_fingerprint: str,
        authorization_fingerprint: str,
        observed_at: datetime,
    ) -> Document:
        """Create the first available snapshot for a source item."""
        return cls(
            document_id=document_id,
            provenance=provenance,
            content_version=ContentVersion(1),
            content_fingerprint=content_fingerprint,
            authorization_version=AuthorizationVersion(1),
            authorization_fingerprint=authorization_fingerprint,
            availability=DocumentAvailability.AVAILABLE,
            created_at=observed_at,
            updated_at=observed_at,
        )

    def reconcile(
        self,
        *,
        content_fingerprint: str,
        authorization_fingerprint: str,
        source_version: str | None,
        observed_at: datetime,
    ) -> Document:
        """Apply one observed snapshot and advance only changed dimensions."""
        provenance = replace(self.provenance, source_version=source_version)
        content_changed = content_fingerprint != self.content_fingerprint
        authorization_changed = (
            authorization_fingerprint != self.authorization_fingerprint
        )
        availability_changed = self.availability is DocumentAvailability.TOMBSTONED
        provenance_changed = provenance != self.provenance

        if not any(
            (
                content_changed,
                authorization_changed,
                availability_changed,
                provenance_changed,
            )
        ):
            return self

        _require_utc(observed_at, "observed_at")
        if observed_at < self.updated_at:
            raise InvalidDocument("observed_at must not be earlier than updated_at")

        return replace(
            self,
            provenance=provenance,
            content_version=(
                self.content_version.next() if content_changed else self.content_version
            ),
            content_fingerprint=content_fingerprint,
            authorization_version=(
                self.authorization_version.next()
                if authorization_changed
                else self.authorization_version
            ),
            authorization_fingerprint=authorization_fingerprint,
            availability=DocumentAvailability.AVAILABLE,
            updated_at=observed_at,
        )

    def tombstone(self, *, observed_at: datetime) -> Document:
        """Mark a missing source item unavailable without deleting its history."""
        if self.availability is DocumentAvailability.TOMBSTONED:
            return self

        _require_utc(observed_at, "observed_at")
        if observed_at < self.updated_at:
            raise InvalidDocument("observed_at must not be earlier than updated_at")

        return replace(
            self,
            availability=DocumentAvailability.TOMBSTONED,
            updated_at=observed_at,
        )
