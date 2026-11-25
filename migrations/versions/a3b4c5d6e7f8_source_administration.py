"""source administration

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-11-26 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3b4c5d6e7f8"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"

OLD_ACTIONS = (
    "'source.registered', 'document.availability_changed',"
    " 'conversation.deleted', 'job.queued', 'job.failed',"
    " 'evaluation_run.started', 'question.asked', 'document.read',"
    " 'document.access_changed', 'synchronization.finished',"
    " 'message.rated', 'embedding_migration.started',"
    " 'embedding_migration.switched', 'job.retried', 'job.cancelled'"
)
NEW_ACTIONS = OLD_ACTIONS + ", 'source.updated', 'source.enabled', 'source.disabled'"


def _audit_actions(actions: str) -> None:
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_action", "audit_events", f"action in ({actions})"
    )


def upgrade() -> None:
    _audit_actions(NEW_ACTIONS)


def downgrade() -> None:
    # The audit log is append-only: a recorded change to a Source is never
    # deleted to make a downgrade fit, so the downgrade stops instead.
    op.execute(
        "do $$ begin"
        " if exists (select 1 from audit_events where action in"
        " ('source.updated', 'source.enabled', 'source.disabled')) then"
        " raise exception 'source changes are recorded; cannot downgrade';"
        " end if; end $$"
    )
    _audit_actions(OLD_ACTIONS)
