"""job attempt progress

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-11-24 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"


def upgrade() -> None:
    op.create_table(
        "job_attempts",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("processed", sa.BigInteger(), nullable=False),
        sa.Column("changed", sa.BigInteger(), nullable=False),
        sa.Column("skipped", sa.BigInteger(), nullable=False),
        sa.Column("warnings", ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt >= 1", name="ck_job_attempts_attempt"),
        sa.CheckConstraint(
            "stage in ('starting', 'reading', 'writing', 'cleaning_up')",
            name="ck_job_attempts_stage",
        ),
        sa.CheckConstraint(
            "processed >= 0 and changed >= 0 and skipped >= 0",
            name="ck_job_attempts_counters",
        ),
        sa.CheckConstraint(
            "warnings <@ array['unresolved_readers', 'quarantined_items']::varchar[]",
            name="ck_job_attempts_warnings",
        ),
        sa.CheckConstraint(
            "outcome is null or outcome in ('succeeded', 'succeeded_with_warnings',"
            " 'retry_scheduled', 'superseded', 'failed')",
            name="ck_job_attempts_outcome",
        ),
        sa.CheckConstraint(
            "(outcome is null) = (finished_at is null)",
            name="ck_job_attempts_finished_at",
        ),
        sa.CheckConstraint(
            "heartbeat_at >= started_at and (finished_at is null"
            " or finished_at >= heartbeat_at)",
            name="ck_job_attempts_times",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "attempt"),
    )


def downgrade() -> None:
    op.drop_table("job_attempts")
