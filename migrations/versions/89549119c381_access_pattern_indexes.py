"""access pattern indexes

Revision ID: 89549119c381
Revises: 2553cef719b4
Create Date: 2026-10-09 09:55:45.329429

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "89549119c381"
down_revision: str | Sequence[str] | None = "2553cef719b4"


def upgrade() -> None:
    """Create the indexes the named access patterns read through."""
    op.create_index(
        "ix_conversations_retention_deadline",
        "conversations",
        ["retention_deadline"],
        postgresql_where=sa.text("deleted_at is null"),
    )
    op.create_index(
        "ix_audit_events_target_occurred_at",
        "audit_events",
        ["target_id", "occurred_at", "event_id"],
    )
    op.create_index(
        "ix_synchronization_runs_source_started_at",
        "synchronization_runs",
        ["source_id", "started_at"],
    )
    op.create_index(
        "ix_access_grants_subject_document",
        "access_grants",
        ["subject_kind", "subject_id", "document_id"],
    )


def downgrade() -> None:
    """Remove the indexes, returning to sequential scans."""
    op.drop_index("ix_access_grants_subject_document", table_name="access_grants")
    op.drop_index(
        "ix_synchronization_runs_source_started_at",
        table_name="synchronization_runs",
    )
    op.drop_index("ix_audit_events_target_occurred_at", table_name="audit_events")
    op.drop_index(
        "ix_conversations_retention_deadline",
        table_name="conversations",
        postgresql_where=sa.text("deleted_at is null"),
    )
