"""Registering and changing Sources: every setting checked, every change recorded.

A Source is what it reads (`kind`) and where (`location`); those two never
change, and no two Sources share them. An operator may rename a Source,
name another secret for it, change who may read its items, and turn it on
or off. Each change locks the Source, writes it, and records who changed it
in the audit log, all in one transaction.

No secret passes through here. `credentials_reference` is the name of a
secret in the service's secret store, such as `confluence/acme-prod`; the
adapter that reads the Source looks the secret up by that name. An error
names the rule a setting broke, never the value it was given.
"""

import posixpath
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Final, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
    AuditValue,
)
from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import AuditEventId, SourceId, UserId
from knowledge_service.ingestion.sources import SourceKind
from knowledge_service.pagination import InvalidPage, decode_cursor, encode_cursor
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.sources import (
    SourceRecord,
    insert_source_record,
    load_source_page,
    load_source_record,
    save_source_record,
)

MAX_DISPLAY_NAME: Final = 200
MAX_LOCATION: Final = 2048
MAX_CREDENTIALS_REFERENCE: Final = 128
MAX_PRINCIPALS: Final = 100
MAX_PRINCIPAL: Final = 256
MAX_SOURCE_LIST: Final = 500
# What an operator may change; `kind` and `location` are what a Source is.
CHANGEABLE: Final = frozenset(
    {"display_name", "credentials_reference", "default_public", "default_principals"}
)
# A secret's name: lowercase words joined by `-`, in up to four `/` parts.
_SECRET_NAME = re.compile(r"[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*){0,3}")
# A host name: lowercase labels, the last starting with a letter, so an IP
# address, a trailing `.`, or one bare label such as `localhost` is refused.
_HOST = re.compile(r"(?:[a-z0-9]+(?:-+[a-z0-9]+)*\.)+[a-z][a-z0-9]*(?:-+[a-z0-9]+)*")
_SOURCE_LIST = "sources"
_SOURCE_CURSOR_KEYS = frozenset({"created_at", "source_id"})


class InvalidSourceSettings(ValueError):
    """A setting breaks a rule; the message names the rule, never the value."""


class SourceExists(Exception):
    """A Source of the same kind and location is already registered."""


class SourceNotFound(LookupError):
    """No Source has this ID."""


class SourceDisabled(Exception):
    """The Source is turned off, so nothing may synchronize it.

    A retry also raises it for a Job whose Source is gone: a retry names the
    Job, not the Source, so its missing Source is not a missing resource.
    """


class SourceNeedsRepair(Exception):
    """The stored Source breaks a rule it was not checked against when stored.

    No change can fix it, since its kind and location never change: it is
    registered again instead.
    """


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    """Every setting of a Source, each checked against its rule.

    Each value has one spelling: a folder path or site address written
    another way is refused, not rewritten, so two spellings of one folder
    can never be two Sources. The principals are kept sorted.
    """

    kind: SourceKind
    location: str
    display_name: str
    credentials_reference: str | None
    default_public: bool
    default_principals: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.kind) is not SourceKind:
            raise InvalidSourceSettings(_KIND_RULE)
        _check_display_name(self.display_name)
        if self.kind is SourceKind.LOCAL_DIRECTORY:
            _check_folder(self.location)
        else:
            _check_site(self.location)
        _check_credentials(self.kind, self.credentials_reference)
        principals = _checked_access(self.default_public, self.default_principals)
        object.__setattr__(self, "default_principals", principals)

    @classmethod
    def of(
        cls,
        *,
        kind: object,
        location: object,
        display_name: object,
        credentials_reference: object = None,
        default_public: object = False,
        default_principals: object = (),
    ) -> SourceDefinition:
        """A definition from plain values of any type, such as a request's.

        Each value is checked here, its type included, so the casts below
        only name what the checks in `__post_init__` then prove.
        """
        if type(kind) is not str or kind not in set(SourceKind):
            raise InvalidSourceSettings(_KIND_RULE)
        if type(default_principals) not in (tuple, list):
            raise InvalidSourceSettings("default_principals must be a list")
        return cls(
            kind=SourceKind(kind),
            location=cast(str, location),
            display_name=cast(str, display_name),
            credentials_reference=cast("str | None", credentials_reference),
            default_public=cast(bool, default_public),
            default_principals=tuple(cast("list[str]", default_principals)),
        )


def _check_display_name(name: object) -> None:
    if (
        type(name) is not str
        or not name
        or len(name) > MAX_DISPLAY_NAME
        or name != name.strip()
        or not name.isprintable()
    ):
        raise InvalidSourceSettings(
            f"display_name must be visible text of 1 to {MAX_DISPLAY_NAME}"
            " characters, with no space around it"
        )


def _check_folder(location: object) -> None:
    # `normpath` keeps a leading `//`, which POSIX lets a system read
    # differently from `/`.
    if (
        type(location) is not str
        or len(location) > MAX_LOCATION
        or not location.isprintable()
        # One spelling of each letter: on macOS, `é` and `e` followed by a
        # combining accent name the same folder.
        or unicodedata.normalize("NFC", location) != location
        or not location.startswith("/")
        or location.startswith("//")
        or location == "/"
        or posixpath.normpath(location) != location
    ):
        raise InvalidSourceSettings(
            "a local_directory location must be an absolute folder path, not"
            " the root, with no `.`, `..`, doubled, or trailing `/`"
        )


def _check_site(location: object) -> None:
    problem = InvalidSourceSettings(
        "a confluence location must be an https address with a lowercase host"
        " name, not an IP address, and no user, password, port 443, query,"
        " fragment, `%`, or trailing `/`"
    )
    if (
        type(location) is not str
        or len(location) > MAX_LOCATION
        or not location.isascii()
        or not location.isprintable()
        or any(mark in location for mark in " %?#@\\")
    ):
        raise problem
    try:
        parts = urlsplit(location)
        port = parts.port
    except ValueError:
        raise problem from None
    path = parts.path
    host = parts.hostname or ""
    if (
        parts.scheme != "https"
        or _HOST.fullmatch(host) is None
        # The host and port as written, so `:`, `:08443`, or `:443` is refused.
        or parts.netloc != (host if port is None else f"{host}:{port}")
        or port == 443
        or (path and (path.endswith("/") or posixpath.normpath(path) != path))
        or urlunsplit(parts) != location
    ):
        raise problem


def _check_credentials(kind: SourceKind, reference: object) -> None:
    if kind is SourceKind.LOCAL_DIRECTORY:
        if reference is not None:
            raise InvalidSourceSettings("a local_directory needs no credentials")
        return
    if (
        type(reference) is not str
        or len(reference) > MAX_CREDENTIALS_REFERENCE
        or _SECRET_NAME.fullmatch(reference) is None
    ):
        raise InvalidSourceSettings(
            "credentials_reference must name a secret, such as"
            " confluence/acme-prod, never hold the secret itself"
        )


def _checked_access(public: object, principals: tuple[object, ...]) -> tuple[str, ...]:
    if type(public) is not bool:
        raise InvalidSourceSettings("default_public must be true or false")
    if len(principals) > MAX_PRINCIPALS:
        raise InvalidSourceSettings(
            f"default_principals holds at most {MAX_PRINCIPALS} principals"
        )
    checked: list[str] = []
    for principal in principals:
        if type(principal) is not str or len(principal) > MAX_PRINCIPAL:
            raise InvalidSourceSettings(_PRINCIPAL_RULE)
        kind, separator, name = principal.partition(":")
        if (
            not separator
            or kind not in ("user", "group")
            or not name
            or name != name.strip()
            or not name.isprintable()
        ):
            raise InvalidSourceSettings(_PRINCIPAL_RULE)
        checked.append(principal)
    if len(set(checked)) != len(checked):
        raise InvalidSourceSettings("default_principals must not repeat a principal")
    if public and checked:
        raise InvalidSourceSettings(
            "a public Source must not also list default_principals"
        )
    return tuple(sorted(checked))


_PRINCIPAL_RULE = "each default principal must be user:<id> or group:<id>"
_KIND_RULE = "kind must be one of: " + ", ".join(SourceKind)


def definition_of(record: SourceRecord) -> SourceDefinition:
    """A stored Source's settings, checked as a new one would be."""
    return SourceDefinition.of(
        kind=record.kind,
        location=record.location,
        display_name=record.display_name,
        credentials_reference=record.credentials_reference,
        default_public=record.default_public,
        default_principals=record.default_principals,
    )


def source_cursor(record: SourceRecord) -> str:
    """The cursor of the page that starts after `record` in the Source list."""
    return encode_cursor(
        _SOURCE_LIST,
        {
            "created_at": record.created_at.isoformat(),
            "source_id": str(record.source_id.value),
        },
    )


def source_position(cursor: str) -> tuple[datetime, SourceId]:
    """Where a `source_cursor` says the next page starts; else `InvalidPage`."""
    position = decode_cursor(cursor, listing=_SOURCE_LIST, keys=_SOURCE_CURSOR_KEYS)
    created_text, source_text = position["created_at"], position["source_id"]
    try:
        created_at = datetime.fromisoformat(created_text)
        source_id = UUID(source_text)
    except ValueError:
        raise InvalidPage("the cursor is not one this API gave") from None
    if (
        created_at.utcoffset() != timedelta(0)
        or created_at.isoformat() != created_text
        or str(source_id) != source_text
    ):
        raise InvalidPage("the cursor is not one this API gave")
    return created_at, SourceId(source_id)


async def register_source(
    runtime: DatabaseRuntime,
    *,
    definition: SourceDefinition,
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> SourceRecord:
    """Store a new, enabled Source; `SourceExists` if its kind and location exist."""
    _require_utc(now)
    record = SourceRecord(
        source_id=SourceId.new(),
        kind=definition.kind.value,
        location=definition.location,
        display_name=definition.display_name,
        credentials_reference=definition.credentials_reference,
        enabled=True,
        default_public=definition.default_public,
        default_principals=definition.default_principals,
        created_at=now,
        updated_at=now,
    )
    async with runtime.transaction() as connection:
        if not await insert_source_record(connection, record):
            raise SourceExists("a source of this kind and location is registered")
        await record_audit_event(
            connection,
            _source_event(
                record,
                action=AuditAction.SOURCE_REGISTERED,
                operator=operator,
                at=now,
                request_id=request_id,
                metadata={"kind": record.kind},
            ),
        )
    return record


async def change_source(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    changes: Mapping[str, object],
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> SourceRecord:
    """Set some of a Source's changeable settings; the rest stay.

    The whole result is checked, so a change that fits only another kind,
    such as a secret for a local folder, is refused. A change that sets
    every setting to the value it has writes and records nothing.
    """
    _require_utc(now)
    unknown = set(changes) - CHANGEABLE
    if not changes or unknown:
        raise InvalidSourceSettings(
            "a change sets one or more of display_name, credentials_reference,"
            " default_public, and default_principals"
        )
    async with runtime.transaction() as connection:
        record = await _lock(connection, source_id=source_id)
        try:
            current = definition_of(record)
        except InvalidSourceSettings:
            raise SourceNeedsRepair("the stored source breaks a rule") from None
        wanted = SourceDefinition.of(
            kind=current.kind.value,
            location=current.location,
            display_name=changes.get("display_name", current.display_name),
            credentials_reference=changes.get(
                "credentials_reference", current.credentials_reference
            ),
            default_public=changes.get("default_public", current.default_public),
            default_principals=changes.get(
                "default_principals", current.default_principals
            ),
        )
        changed = {
            name: True
            for name in sorted(CHANGEABLE)
            if getattr(wanted, name) != getattr(current, name)
        }
        if not changed:
            return record
        updated = replace(
            record,
            display_name=wanted.display_name,
            credentials_reference=wanted.credentials_reference,
            default_public=wanted.default_public,
            default_principals=wanted.default_principals,
            updated_at=max(now, record.updated_at),
        )
        await save_source_record(connection, updated)
        await record_audit_event(
            connection,
            _source_event(
                updated,
                action=AuditAction.SOURCE_UPDATED,
                operator=operator,
                at=now,
                request_id=request_id,
                metadata=changed,
            ),
        )
    return updated


async def set_source_enabled(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    enabled: bool,
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> SourceRecord:
    """Turn a Source's synchronization on or off; the same state records nothing.

    The lock waits for a scheduler that is queueing a Job for this Source,
    so that Job is queued before the change, never after it.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        record = await _lock(connection, source_id=source_id)
        if record.enabled is enabled:
            return record
        updated = replace(
            record, enabled=enabled, updated_at=max(now, record.updated_at)
        )
        await save_source_record(connection, updated)
        await record_audit_event(
            connection,
            _source_event(
                updated,
                action=AuditAction.SOURCE_ENABLED
                if enabled
                else AuditAction.SOURCE_DISABLED,
                operator=operator,
                at=now,
                request_id=request_id,
                metadata={},
            ),
        )
    return updated


async def load_source(
    runtime: DatabaseRuntime, *, source_id: SourceId
) -> SourceRecord | None:
    """One Source, or None if no Source has this ID."""
    async with runtime.transaction() as connection:
        return await load_source_record(connection, source_id=source_id)


async def load_sources(
    runtime: DatabaseRuntime,
    *,
    limit: int,
    after: tuple[datetime, SourceId] | None = None,
) -> tuple[SourceRecord, ...]:
    """Up to `limit` Sources, newest first; with `after`, from a `source_position`."""
    if type(limit) is not int or not 1 <= limit <= MAX_SOURCE_LIST:
        raise ValueError(f"limit must be an integer from 1 to {MAX_SOURCE_LIST}")
    async with runtime.transaction() as connection:
        return await load_source_page(connection, limit=limit, after=after)


async def _lock(connection: AsyncConnection, *, source_id: SourceId) -> SourceRecord:
    record = await load_source_record(connection, source_id=source_id, for_update=True)
    if record is None:
        raise SourceNotFound("no source has this ID")
    return record


def _require_utc(now: datetime) -> None:
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")


def _source_event(
    record: SourceRecord,
    *,
    action: AuditAction,
    operator: UserId,
    at: datetime,
    request_id: UUID | None,
    metadata: Mapping[str, AuditValue],
) -> AuditEvent:
    """Who changed which Source, and which settings; never their values."""
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=at,
        actor_id=operator,
        action=action,
        target_kind=AuditTargetKind.SOURCE,
        target_id=record.source_id.value,
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=request_id,
        run_id=None,
        metadata=metadata,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DatabaseSourceAdministration:
    """Source administration stored in PostgreSQL, at the clock's time."""

    runtime: DatabaseRuntime
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def page(
        self, *, limit: int, after: tuple[datetime, SourceId] | None = None
    ) -> tuple[SourceRecord, ...]:
        return await load_sources(self.runtime, limit=limit, after=after)

    async def load(self, *, source_id: SourceId) -> SourceRecord | None:
        return await load_source(self.runtime, source_id=source_id)

    async def register(
        self, *, definition: SourceDefinition, operator: UserId, request_id: UUID
    ) -> SourceRecord:
        return await register_source(
            self.runtime,
            definition=definition,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )

    async def change(
        self,
        *,
        source_id: SourceId,
        changes: Mapping[str, object],
        operator: UserId,
        request_id: UUID,
    ) -> SourceRecord:
        return await change_source(
            self.runtime,
            source_id=source_id,
            changes=changes,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )

    async def set_enabled(
        self, *, source_id: SourceId, enabled: bool, operator: UserId, request_id: UUID
    ) -> SourceRecord:
        return await set_source_enabled(
            self.runtime,
            source_id=source_id,
            enabled=enabled,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )
