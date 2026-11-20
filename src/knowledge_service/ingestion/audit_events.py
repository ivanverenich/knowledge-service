"""The audit event ingestion records whenever it writes Access Grants.

Both reconciliation and publishing write a Document's Access Grants, so
both import `access_event` from here; neither imports the other for it.
"""

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.documents import Document
from knowledge_service.identifiers import AuditEventId, SynchronizationRunId


def access_event(
    document: Document, *, readers: int, run_id: SynchronizationRunId | None
) -> AuditEvent:
    """Record that a Document's readers were written, without naming them."""
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=document.updated_at,
        actor_id=None,
        action=AuditAction.DOCUMENT_ACCESS_CHANGED,
        target_kind=AuditTargetKind.DOCUMENT,
        target_id=document.document_id.value,
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=None,
        run_id=None if run_id is None else run_id.value,
        metadata={
            "authorization_version": document.authorization_version.number,
            "readers": readers,
        },
    )
