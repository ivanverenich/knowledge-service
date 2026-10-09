"""one running synchronization run per source

Revision ID: 09aa0992e559
Revises: 89549119c381
Create Date: 2026-10-09 10:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "09aa0992e559"
down_revision: str | Sequence[str] | None = "89549119c381"


def upgrade() -> None:
    """Refuse a second Synchronization Run that is still running."""
    op.create_index(
        "uq_synchronization_runs_running_source",
        "synchronization_runs",
        ["source_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    """Allow a Source to start a second run while one is still running."""
    op.drop_index(
        "uq_synchronization_runs_running_source",
        table_name="synchronization_runs",
        postgresql_where=sa.text("status = 'running'"),
    )
