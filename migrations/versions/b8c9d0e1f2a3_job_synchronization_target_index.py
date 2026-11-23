"""job synchronization target index

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-11-23 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"


def upgrade() -> None:
    op.create_index(
        "ix_jobs_synchronization_target",
        "jobs",
        ["target_id"],
        postgresql_where=sa.text("kind = 'synchronize_source'"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_synchronization_target", table_name="jobs")
