"""synchronization history index

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-11-27 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a9"
down_revision: str | Sequence[str] | None = "a3b4c5d6e7f8"

SYNCHRONIZATIONS = sa.text("kind = 'synchronize_source'")


def upgrade() -> None:
    # The ID as a second column lets a history page read the index in order
    # instead of sorting every synchronization Job of the Source.
    op.drop_index("ix_jobs_synchronization_target", table_name="jobs")
    op.create_index(
        "ix_jobs_synchronization_target",
        "jobs",
        ["target_id", "job_id"],
        postgresql_where=SYNCHRONIZATIONS,
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_synchronization_target", table_name="jobs")
    op.create_index(
        "ix_jobs_synchronization_target",
        "jobs",
        ["target_id"],
        postgresql_where=SYNCHRONIZATIONS,
    )
