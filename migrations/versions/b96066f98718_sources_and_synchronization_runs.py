"""sources and synchronization runs

Revision ID: b96066f98718
Revises: 20c8d8dcd117
Create Date: 2026-10-07 10:57:05.705318

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b96066f98718"
down_revision: str | Sequence[str] | None = "20c8d8dcd117"


def upgrade() -> None:
    """Create Source configuration and Synchronization Run records."""
    op.create_table(
        "sources",
        sa.Column("source_id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("credentials_reference", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("checkpoint", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "location", name="uq_sources_kind_location"),
    )
    op.create_table(
        "synchronization_runs",
        sa.Column("run_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Uuid(),
            sa.ForeignKey("sources.source_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status in ('running', 'succeeded', 'failed')",
            name="ck_synchronization_runs_status",
        ),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at is null)",
            name="ck_synchronization_runs_finished_at",
        ),
    )


def downgrade() -> None:
    """Remove Synchronization Run and Source records."""
    op.drop_table("synchronization_runs")
    op.drop_table("sources")
