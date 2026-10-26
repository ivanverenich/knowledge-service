"""Access Grant values: which subjects may read a Document.

Every grant names exactly one subject: everyone, one user, or one group.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from knowledge_service.identifiers import AccessGrantId, DocumentId, GroupId, UserId


class InvalidGrant(ValueError):
    """An Access Grant value or set violates a domain invariant."""


class InvalidAuthorizationContext(ValueError):
    """An Authorization Context does not name one User and only Groups."""


class SubjectKind(StrEnum):
    """The kind of reader a grant names."""

    PUBLIC = "public"
    USER = "user"
    GROUP = "group"


@dataclass(frozen=True, slots=True)
class Subject:
    """Exactly one reader: everyone, one user, or one group."""

    kind: SubjectKind
    identifier: UserId | GroupId | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not SubjectKind:
            raise InvalidGrant("a subject kind must be a SubjectKind")
        if self.kind is SubjectKind.PUBLIC:
            if self.identifier is not None:
                raise InvalidGrant("a public grant must not name a subject")
            return
        if self.identifier is None:
            raise InvalidGrant("a user or group grant must name a subject")
        if self.kind is SubjectKind.USER and not isinstance(self.identifier, UserId):
            raise InvalidGrant("a user grant must name a UserId")
        if self.kind is SubjectKind.GROUP and not isinstance(self.identifier, GroupId):
            raise InvalidGrant("a group grant must name a GroupId")

    @classmethod
    def public(cls) -> Subject:
        """Everyone may read the Document."""
        return cls(kind=SubjectKind.PUBLIC)

    @classmethod
    def user(cls, user_id: UserId) -> Subject:
        """One user may read the Document."""
        return cls(kind=SubjectKind.USER, identifier=user_id)

    @classmethod
    def group(cls, group_id: GroupId) -> Subject:
        """Members of one group may read the Document."""
        return cls(kind=SubjectKind.GROUP, identifier=group_id)


@dataclass(frozen=True, slots=True)
class AuthorizationContext:
    """The verified User and their Groups for one request or workflow.

    It may read a Document granted to everyone, to this User, or to one of
    these Groups.
    """

    user_id: UserId
    group_ids: frozenset[GroupId] = frozenset()

    def __post_init__(self) -> None:
        # A plain UUID, or a UserId passed as a group, would match a grant
        # meant for another subject kind, so the exact types are required.
        if type(self.user_id) is not UserId:
            raise InvalidAuthorizationContext("the user must be a UserId")
        if type(self.group_ids) is not frozenset:
            raise InvalidAuthorizationContext("the groups must be a frozenset")
        if any(type(group_id) is not GroupId for group_id in self.group_ids):
            raise InvalidAuthorizationContext("every group must be a GroupId")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidGrant(f"{field_name} must be a UTC-aware timestamp")


@dataclass(frozen=True, slots=True)
class AccessGrant:
    """One normalized statement that a subject may read a Document."""

    grant_id: AccessGrantId
    document_id: DocumentId
    subject: Subject
    granted_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.granted_at, "granted_at")


def granted_subjects(
    *,
    document_id: DocumentId,
    grants: Sequence[AccessGrant],
) -> tuple[AccessGrant, ...]:
    """Return one grant per subject for a single Document, in a stable order."""
    for grant in grants:
        if grant.document_id != document_id:
            raise InvalidGrant("every grant must belong to the same document")

    ordered = tuple(
        sorted(
            grants,
            key=lambda grant: (
                grant.subject.kind.value,
                str(grant.subject.identifier),
            ),
        )
    )
    seen: set[Subject] = set()
    for grant in ordered:
        if grant.subject in seen:
            raise InvalidGrant("a subject may be granted at most once per document")
        seen.add(grant.subject)
    return ordered
