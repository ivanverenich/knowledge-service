"""Record and read Audit Events."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import (
    AuditAction,
    AuditEvent,
    AuditTargetKind,
    AuditValue,
    InvalidAuditEvent,
)
from knowledge_service.identifiers import AuditEventId, UserId
from knowledge_service.persistence.schema import audit_events


def audit_trail_statement(target_id: UUID, limit: int) -> sa.Select[Any]:
    """Select the trail recorded against one target, oldest first."""
    return (
        sa.select(audit_events)
        .where(audit_events.c.target_id == target_id)
        .order_by(audit_events.c.occurred_at, audit_events.c.event_id)
        .limit(limit)
    )


def _audit_row(event: AuditEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id.value,
        "occurred_at": event.occurred_at,
        "actor_id": event.actor_id.value if event.actor_id is not None else None,
        "action": event.action.value,
        "target_kind": event.target_kind.value,
        "target_id": event.target_id,
        "metadata": dict(event.metadata),
    }


def _audit_from(record: Mapping[str, object]) -> AuditEvent:
    try:
        action = AuditAction(cast(str, record["action"]))
        target_kind = AuditTargetKind(cast(str, record["target_kind"]))
    except ValueError:
        raise InvalidAuditEvent(
            "stored audit action or target kind is not a known value"
        ) from None

    actor_id = cast("UUID | None", record["actor_id"])
    stored = cast("dict[str, object]", record["metadata"])
    return AuditEvent(
        event_id=AuditEventId(cast(UUID, record["event_id"])),
        occurred_at=cast(datetime, record["occurred_at"]),
        actor_id=UserId(actor_id) if actor_id is not None else None,
        action=action,
        target_kind=target_kind,
        target_id=cast(UUID, record["target_id"]),
        metadata=cast("dict[str, AuditValue]", stored),
    )


async def record_audit_event(
    connection: AsyncConnection, event: AuditEvent
) -> AuditEventId:
    """Append one audit event. There is no function that updates or deletes one."""
    await connection.execute(sa.insert(audit_events).values(_audit_row(event)))
    return event.event_id


async def load_audit_events(
    connection: AsyncConnection, *, target_id: UUID, limit: int
) -> tuple[AuditEvent, ...]:
    """Read the trail recorded against one target, oldest first."""
    statement = audit_trail_statement(target_id=target_id, limit=limit)
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_audit_from(dict(record)) for record in records)
