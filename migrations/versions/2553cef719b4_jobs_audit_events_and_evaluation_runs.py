"""jobs, audit events, and evaluation runs

Revision ID: a7d91c0e5f42
Revises: 4c3e640575eb
Create Date: 2026-10-08 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "2553cef719b4"
down_revision: str | Sequence[str] | None = "4c3e640575eb"


def upgrade() -> None:
    """Create queued work, the audit trail, and Evaluation Runs."""
    op.create_table(
        "jobs",
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "kind in ('synchronize_source', 'purge_conversation')",
            name="ck_jobs_kind",
        ),
        sa.CheckConstraint(
            "status in ('queued', 'running', 'succeeded', 'failed')",
            name="ck_jobs_status",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1 and attempt >= 0 and attempt <= max_attempts",
            name="ck_jobs_attempt",
        ),
        sa.CheckConstraint(
            "(status in ('succeeded', 'failed')) = (finished_at is not null)",
            name="ck_jobs_finished_at",
        ),
        sa.CheckConstraint(
            "(status = 'queued') = (started_at is null)", name="ck_jobs_started_at"
        ),
        sa.CheckConstraint(
            "(status = 'failed') = (last_error_code is not null)",
            name="ck_jobs_last_error_code",
        ),
    )

    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=48), nullable=False),
        sa.Column("target_kind", sa.String(length=24), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("metadata", JSONB, nullable=False),
        sa.CheckConstraint(
            "action in ('source.registered', 'document.availability_changed',"
            " 'conversation.deleted', 'job.queued', 'job.failed',"
            " 'evaluation_run.started')",
            name="ck_audit_events_action",
        ),
        sa.CheckConstraint(
            "target_kind in ('source', 'document', 'conversation', 'job',"
            " 'evaluation_run')",
            name="ck_audit_events_target_kind",
        ),
        sa.CheckConstraint(
            "pg_column_size(metadata) <= 1024", name="ck_audit_events_metadata_size"
        ),
    )

    op.create_table(
        "evaluation_runs",
        sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column("dataset_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("configuration_fingerprint", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("artifact_location", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status in ('pending', 'running', 'succeeded', 'failed')",
            name="ck_evaluation_runs_status",
        ),
        sa.CheckConstraint("dataset_version >= 1", name="ck_evaluation_runs_dataset"),
        sa.CheckConstraint(
            "(status = 'succeeded') = (artifact_location is not null)",
            name="ck_evaluation_runs_artifact_location",
        ),
        sa.CheckConstraint(
            "(status in ('succeeded', 'failed')) = (finished_at is not null)",
            name="ck_evaluation_runs_finished_at",
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (started_at is null)",
            name="ck_evaluation_runs_started_at",
        ),
    )


def downgrade() -> None:
    """Remove Evaluation Runs, the audit trail, and queued work."""
    op.drop_table("evaluation_runs")
    op.drop_table("audit_events")
    op.drop_table("jobs")
