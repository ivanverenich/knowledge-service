"""job publication

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-11-21 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"


def upgrade() -> None:
    # Every existing row starts unpublished: no message has ever named it.
    op.add_column(
        "jobs",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_jobs_unpublished_queued_at",
        "jobs",
        ["queued_at", "job_id"],
        postgresql_where=sa.text("status = 'queued' and published_at is null"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_unpublished_queued_at", table_name="jobs")
    op.drop_column("jobs", "published_at")
