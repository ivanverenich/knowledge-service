"""Store Access Grants and decide in SQL which Chunks a reader may see."""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import (
    AccessGrant,
    AuthorizationContext,
    InvalidGrant,
    Subject,
    SubjectKind,
    granted_subjects,
)
from knowledge_service.documents import DocumentAvailability
from knowledge_service.identifiers import AccessGrantId, DocumentId, GroupId, UserId
from knowledge_service.persistence.schema import access_grants, documents


def subject_grants_statement(subject_kind: str, subject_id: UUID) -> sa.Select[Any]:
    """Select the Documents one verified subject is allowed to reach."""
    return sa.select(access_grants.c.document_id).where(
        access_grants.c.subject_kind == subject_kind,
        access_grants.c.subject_id == subject_id,
    )


def _grant_row(grant: AccessGrant) -> dict[str, object]:
    identifier = grant.subject.identifier
    return {
        "grant_id": grant.grant_id.value,
        "document_id": grant.document_id.value,
        "subject_kind": grant.subject.kind.value,
        "subject_id": identifier.value if identifier is not None else None,
        "granted_at": grant.granted_at,
    }


def _grant_from(record: Mapping[str, object]) -> AccessGrant:
    try:
        kind = SubjectKind(cast(str, record["subject_kind"]))
    except ValueError:
        raise InvalidGrant("stored subject kind is not a known value") from None

    stored_id = cast("UUID | None", record["subject_id"])
    if kind is SubjectKind.PUBLIC:
        subject = Subject.public()
    elif kind is SubjectKind.USER:
        subject = Subject.user(UserId(cast(UUID, stored_id)))
    else:
        subject = Subject.group(GroupId(cast(UUID, stored_id)))

    return AccessGrant(
        grant_id=AccessGrantId(cast(UUID, record["grant_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        subject=subject,
        granted_at=cast(datetime, record["granted_at"]),
    )


def eligible_chunks_predicate(context: AuthorizationContext) -> sa.ColumnElement[bool]:
    """The condition a Chunk must meet before retrieval may rank it.

    Its Document is available, and at least one of its Access Grants names
    everyone, the context's User, or one of the context's Groups. The
    statement using it must join `chunks` to `documents`. Every value is a
    bound parameter, never text spliced into the SQL.
    """
    group_ids = sorted(group_id.value for group_id in context.group_ids)
    granted = sa.exists().where(
        access_grants.c.document_id == documents.c.document_id,
        sa.or_(
            access_grants.c.subject_kind == SubjectKind.PUBLIC.value,
            sa.and_(
                access_grants.c.subject_kind == SubjectKind.USER.value,
                access_grants.c.subject_id == context.user_id.value,
            ),
            sa.and_(
                access_grants.c.subject_kind == SubjectKind.GROUP.value,
                # One array parameter, however many Groups the User has.
                access_grants.c.subject_id
                == sa.any_(
                    sa.bindparam(
                        "group_ids", group_ids, type_=ARRAY(sa.Uuid()), unique=True
                    )
                ),
            ),
        ),
    )
    return sa.and_(
        documents.c.availability == DocumentAvailability.AVAILABLE.value,
        granted,
    )


async def replace_access_grants(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
    grant_run: Sequence[AccessGrant],
) -> tuple[AccessGrant, ...]:
    """Replace every stored Access Grant for a Document with the given set."""
    ordered = granted_subjects(document_id=document_id, grants=grant_run)

    await connection.execute(
        sa.delete(access_grants).where(access_grants.c.document_id == document_id.value)
    )
    if ordered:
        await connection.execute(
            sa.insert(access_grants), [_grant_row(grant) for grant in ordered]
        )
    return ordered


async def load_access_grants(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
) -> tuple[AccessGrant, ...]:
    """Read the stored Access Grants for a Document."""
    statement = (
        sa.select(access_grants)
        .where(access_grants.c.document_id == document_id.value)
        .order_by(access_grants.c.subject_kind, access_grants.c.subject_id)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_grant_from(dict(record)) for record in records)
