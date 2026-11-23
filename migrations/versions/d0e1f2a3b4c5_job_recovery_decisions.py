"""job recovery decisions

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-11-24 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b4"

OLD_ACTIONS = (
    "'source.registered', 'document.availability_changed',"
    " 'conversation.deleted', 'job.queued', 'job.failed',"
    " 'evaluation_run.started', 'question.asked', 'document.read',"
    " 'document.access_changed', 'synchronization.finished',"
    " 'message.rated', 'embedding_migration.started',"
    " 'embedding_migration.switched'"
)
NEW_ACTIONS = OLD_ACTIONS + ", 'job.retried', 'job.cancelled'"


def _job_checks(*, cancelled: bool) -> None:
    for name in (
        "ck_jobs_status",
        "ck_jobs_finished_at",
        "ck_jobs_started_at",
        "ck_jobs_last_error_code",
    ):
        op.drop_constraint(name, "jobs", type_="check")
    if cancelled:
        statuses = "'queued', 'running', 'succeeded', 'failed', 'cancelled'"
        finished = "'succeeded', 'failed', 'cancelled'"
        exempt = "status = 'cancelled' or "
    else:
        statuses = "'queued', 'running', 'succeeded', 'failed'"
        finished = "'succeeded', 'failed'"
        exempt = ""
    op.create_check_constraint("ck_jobs_status", "jobs", f"status in ({statuses})")
    op.create_check_constraint(
        "ck_jobs_finished_at",
        "jobs",
        f"(status in ({finished})) = (finished_at is not null)",
    )
    op.create_check_constraint(
        "ck_jobs_started_at",
        "jobs",
        f"{exempt}(status = 'queued') = (started_at is null)",
    )
    op.create_check_constraint(
        "ck_jobs_last_error_code",
        "jobs",
        f"{exempt}(status = 'failed') = (last_error_code is not null)",
    )


def _audit_actions(actions: str) -> None:
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_action", "audit_events", f"action in ({actions})"
    )


def upgrade() -> None:
    # An attempt stored before this revision counts as heard from now.
    op.add_column(
        "job_attempts",
        sa.Column(
            "heard_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    _job_checks(cancelled=True)
    _audit_actions(NEW_ACTIONS)
    op.create_index(
        "ix_jobs_in_flight",
        "jobs",
        ["status"],
        postgresql_where=sa.text("status in ('queued', 'running')"),
    )
    op.create_index(
        "ix_jobs_failed_for_good",
        "jobs",
        ["finished_at", "job_id"],
        postgresql_where=sa.text("status = 'failed' and retry_at is null"),
    )


def downgrade() -> None:
    # The audit log is append-only: an operator's decision is never deleted
    # to make a downgrade fit, so the downgrade stops instead.
    op.execute(
        "do $$ begin"
        " if exists (select 1 from audit_events"
        " where action in ('job.retried', 'job.cancelled')) then"
        " raise exception 'operator decisions are recorded; cannot downgrade';"
        " end if; end $$"
    )
    op.drop_index("ix_jobs_failed_for_good", table_name="jobs")
    op.drop_index("ix_jobs_in_flight", table_name="jobs")
    # Before this revision a Job could not be cancelled; the closest closed
    # state is a final failure, which keeps the Job out of every retry.
    op.execute(
        "update jobs set status = 'failed',"
        " started_at = coalesce(started_at, queued_at),"
        " last_error_code = coalesce(last_error_code, 'cancelled')"
        " where status = 'cancelled'"
    )
    _job_checks(cancelled=False)
    _audit_actions(OLD_ACTIONS)
    op.drop_column("job_attempts", "heard_at")
