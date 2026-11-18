"""Turn who a Source says may read an item into Access Grant subjects.

A Source names readers in its own words, as `user:<id>` or `group:<id>`,
with IDs only that Source understands. A `PrincipalDirectory` maps each ID
it knows to the identity provider's subject or full group path, and the
same deterministic IDs the Authorization Context uses make the Subject. So a
Source group mapped to `/engineering` is exactly the Group a token listing
`/engineering` carries.

Normalization fails closed. A principal that is malformed, or that the
directory does not map, grants nothing and is reported as unresolved, so a
gap in the mapping can only hide a Document, never show it. The result does
not depend on the order or the repetition of the principals.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from knowledge_service.access import Subject
from knowledge_service.identity.context import (
    group_id_for,
    plain_group_path,
    user_id_for,
    visible_name,
)
from knowledge_service.ingestion.sources import SourceAccess


class InvalidPrincipalDirectory(ValueError):
    """A principal directory names an identity no token could ever carry."""


class UnresolvedReason(StrEnum):
    """Why a Source principal grants nothing."""

    MALFORMED = "malformed"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class UnresolvedPrincipal:
    """A principal that grants nothing until the directory maps it.

    The principal is kept for whoever fixes the mapping, but left out of the
    repr: it may name a person, and reprs end up in logs.
    """

    principal: str = field(repr=False)
    reason: UnresolvedReason


@dataclass(frozen=True, slots=True)
class NormalizedAccess:
    """The distinct readers of one item, and the principals that grant nothing."""

    readers: tuple[Subject, ...]
    unresolved: tuple[UnresolvedPrincipal, ...]


@dataclass(frozen=True, slots=True)
class PrincipalDirectory:
    """The stable mapping from one Source's principals to provider identities.

    `users` maps a Source user ID to the provider's `sub`; `groups` maps a
    Source group ID to a full group path such as `/engineering`. IDs are
    matched exactly, as the Source wrote them. Each `sub` and path must be
    one a token could carry, by the Authorization Context's own rules, so a
    mapping can never name an identity nobody can ever be. The mappings are
    left out of the repr: they name people.
    """

    issuer: str
    users: Mapping[str, str] = field(default_factory=dict[str, str], repr=False)
    groups: Mapping[str, str] = field(default_factory=dict[str, str], repr=False)

    def __post_init__(self) -> None:
        if not _exact(self.issuer) or not visible_name(self.issuer):
            raise InvalidPrincipalDirectory("the issuer must be a visible name")
        for name in ("users", "groups"):
            mapping: object = getattr(self, name)
            if not isinstance(mapping, Mapping):
                raise InvalidPrincipalDirectory(f"{name} must be a mapping")
            # A private, read-only copy: changing the caller's dictionary
            # later must not change who may read what.
            copied = dict(cast("Mapping[object, object]", mapping))
            if not all(_exact(key) and _exact(value) for key, value in copied.items()):
                raise InvalidPrincipalDirectory(f"{name} must map names to names")
            object.__setattr__(self, name, MappingProxyType(copied))
        if not all(visible_name(subject) for subject in self.users.values()):
            raise InvalidPrincipalDirectory("a user must map to a visible subject")
        if not all(plain_group_path(path) for path in self.groups.values()):
            raise InvalidPrincipalDirectory("a group must map to a plain full path")

    def subject_for(self, kind: str, external_id: str) -> Subject | None:
        """The Subject a Source principal is, or None if it is not mapped."""
        if kind == "user" and (subject := self.users.get(external_id)) is not None:
            return Subject.user(user_id_for(self.issuer, subject))
        if kind == "group" and (path := self.groups.get(external_id)) is not None:
            return Subject.group(group_id_for(self.issuer, path))
        return None


def normalize_access(
    access: SourceAccess, directory: PrincipalDirectory | None
) -> NormalizedAccess:
    """The readers a Source's access names, failing closed.

    A public item is readable by everyone. Otherwise each principal must be
    `user:<id>` or `group:<id>` and mapped by `directory`; any other becomes
    an `UnresolvedPrincipal`. With no directory, nothing resolves. Readers and
    unresolved principals come back distinct and sorted.
    """
    if access.public:
        return NormalizedAccess(readers=(Subject.public(),), unresolved=())
    readers: set[Subject] = set()
    unresolved: set[UnresolvedPrincipal] = set()
    for principal in access.principals:
        kind, separator, external_id = principal.partition(":")
        if not separator or kind not in ("user", "group") or not _exact(external_id):
            unresolved.add(UnresolvedPrincipal(principal, UnresolvedReason.MALFORMED))
            continue
        subject = (
            None if directory is None else directory.subject_for(kind, external_id)
        )
        if subject is None:
            unresolved.add(UnresolvedPrincipal(principal, UnresolvedReason.UNKNOWN))
        else:
            readers.add(subject)
    return NormalizedAccess(
        readers=tuple(sorted(readers, key=_subject_order)),
        unresolved=tuple(sorted(unresolved, key=lambda u: (u.principal, u.reason))),
    )


def _subject_order(subject: Subject) -> tuple[str, str]:
    return (subject.kind.value, str(subject.identifier))


def _exact(name: object) -> bool:
    """A plain, non-empty string with no surrounding whitespace.

    Exactly `str`: a subclass could compare or hash as another name.
    """
    return type(name) is str and bool(name) and name == name.strip()
