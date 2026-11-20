"""audit decisions and requests

Revision ID: b1c2d3e4f5a6
Revises: a9b0c1d2e3f4
Create Date: 2026-11-20 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "a9b0c1d2e3f4"

OLD_ACTIONS = (
    "action in ('source.registered', 'document.availability_changed',"
    " 'conversation.deleted', 'job.queued', 'job.failed',"
    " 'evaluation_run.started')"
)
NEW_ACTIONS = (
    "action in ('source.registered', 'document.availability_changed',"
    " 'conversation.deleted', 'job.queued', 'job.failed',"
    " 'evaluation_run.started', 'question.asked', 'document.read',"
    " 'document.access_changed', 'synchronization.finished',"
    " 'message.rated', 'embedding_migration.started',"
    " 'embedding_migration.switched')"
)
OLD_TARGET_KINDS = (
    "target_kind in ('source', 'document', 'conversation', 'job', 'evaluation_run')"
)
NEW_TARGET_KINDS = (
    "target_kind in ('source', 'document', 'conversation', 'job',"
    " 'evaluation_run', 'request', 'message', 'embedding_migration')"
)


def upgrade() -> None:
    """Record each event's decision, outcome, request, and run.

    Events recorded before this revision were allowed and succeeded; the
    defaults fill them in once and are then dropped, so every new event
    must say both itself.
    """
    op.add_column(
        "audit_events",
        sa.Column(
            "decision", sa.String(length=16), nullable=False, server_default="allowed"
        ),
    )
    op.add_column(
        "audit_events",
        sa.Column(
            "outcome", sa.String(length=24), nullable=False, server_default="succeeded"
        ),
    )
    op.alter_column("audit_events", "decision", server_default=None)
    op.alter_column("audit_events", "outcome", server_default=None)
    op.add_column("audit_events", sa.Column("request_id", sa.Uuid(), nullable=True))
    op.add_column("audit_events", sa.Column("run_id", sa.Uuid(), nullable=True))
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.create_check_constraint("ck_audit_events_action", "audit_events", NEW_ACTIONS)
    op.drop_constraint("ck_audit_events_target_kind", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_target_kind", "audit_events", NEW_TARGET_KINDS
    )
    op.create_check_constraint(
        "ck_audit_events_decision", "audit_events", "decision in ('allowed', 'denied')"
    )
    op.create_check_constraint(
        "ck_audit_events_outcome",
        "audit_events",
        "outcome in ('succeeded', 'failed', 'answered', 'abstained', 'rejected',"
        " 'denied', 'dependency_error', 'throttled', 'cancelled',"
        " 'internal_error')",
    )
    op.create_index(
        "ix_audit_events_request_occurred_at",
        "audit_events",
        ["request_id", "occurred_at", "event_id"],
        postgresql_where=sa.text("request_id is not null"),
    )


def downgrade() -> None:
    """Forget decisions, outcomes, requests, and runs, and the new actions.

    Events the old schema cannot hold truthfully are deleted first: a new
    action or target kind, or a decision or outcome other than the
    `allowed` and `succeeded` an upgrade would give them back.
    """
    op.drop_index("ix_audit_events_request_occurred_at", table_name="audit_events")
    op.drop_constraint("ck_audit_events_outcome", "audit_events", type_="check")
    op.drop_constraint("ck_audit_events_decision", "audit_events", type_="check")
    op.drop_constraint("ck_audit_events_target_kind", "audit_events", type_="check")
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.execute(
        "DELETE FROM audit_events WHERE action IN ('question.asked',"
        " 'document.read', 'document.access_changed', 'synchronization.finished',"
        " 'message.rated', 'embedding_migration.started',"
        " 'embedding_migration.switched')"
        " OR target_kind IN ('request', 'message', 'embedding_migration')"
        " OR decision <> 'allowed' OR outcome <> 'succeeded'"
    )
    op.create_check_constraint("ck_audit_events_action", "audit_events", OLD_ACTIONS)
    op.create_check_constraint(
        "ck_audit_events_target_kind", "audit_events", OLD_TARGET_KINDS
    )
    op.drop_column("audit_events", "run_id")
    op.drop_column("audit_events", "request_id")
    op.drop_column("audit_events", "outcome")
    op.drop_column("audit_events", "decision")
