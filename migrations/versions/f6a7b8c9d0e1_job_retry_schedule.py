"""job retry schedule

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-11-23 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"

OLD_OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and attempt < max_attempts)"
)
OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and retry_at is not null)"
)


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    # A failed Job with attempts left was open before; it stays open, due now.
    op.execute(
        "update jobs set retry_at = finished_at"
        " where status = 'failed' and attempt < max_attempts"
    )
    op.create_check_constraint(
        "ck_jobs_retry_at",
        "jobs",
        "retry_at is null or (status = 'failed' and attempt < max_attempts"
        " and retry_at >= finished_at)",
    )
    # The same rows are open under both predicates, so no new duplicates.
    op.drop_index("uq_jobs_open_intent", table_name="jobs")
    op.create_index(
        "uq_jobs_open_intent",
        "jobs",
        ["kind", "target_id"],
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    )
    op.create_index(
        "ix_jobs_due_retry",
        "jobs",
        ["retry_at", "job_id"],
        postgresql_where=sa.text("retry_at is not null"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_due_retry", table_name="jobs")
    op.drop_index("uq_jobs_open_intent", table_name="jobs")
    # Before this revision, a failed Job with attempts left was open. Keep a
    # permanent failure closed by marking its attempts as used up.
    op.execute(
        "update jobs set attempt = max_attempts"
        " where status = 'failed' and retry_at is null and attempt < max_attempts"
    )
    op.create_index(
        "uq_jobs_open_intent",
        "jobs",
        ["kind", "target_id"],
        unique=True,
        postgresql_where=sa.text(OLD_OPEN_JOB_PREDICATE),
    )
    op.drop_constraint("ck_jobs_retry_at", "jobs", type_="check")
    op.drop_column("jobs", "retry_at")
