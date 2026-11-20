"""The answer stream's `AuditTrail` over PostgreSQL.

Each event is written in a short transaction of its own, which commits
before `record` returns: when the stream goes on to send its decision, the
event is already durable. Nothing else is written in that transaction, and
a failure raises, so the stream can refuse to send what is not recorded.
"""

from knowledge_service.audit import AuditEvent
from knowledge_service.database import DatabaseRuntime
from knowledge_service.persistence.audit import record_audit_event


class DatabaseAuditTrail:
    """Write each audit event in its own committed transaction."""

    def __init__(self, runtime: DatabaseRuntime) -> None:
        self._runtime = runtime

    async def record(self, event: AuditEvent) -> None:
        """Append `event`; return only once it is committed."""
        async with self._runtime.transaction() as connection:
            await record_audit_event(connection, event)
