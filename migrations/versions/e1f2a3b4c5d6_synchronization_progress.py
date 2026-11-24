"""synchronization progress

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-11-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: str | Sequence[str] | None = "d0e1f2a3b4c5"


def upgrade() -> None:
    # No pass wrote a checkpoint before this revision, and one without the
    # pass it belongs to cannot be resumed safely, so any stray one goes.
    op.execute("update sources set checkpoint = null")
    op.add_column(
        "sources",
        sa.Column("pass_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_sources_checkpoint_pass",
        "sources",
        "checkpoint is null or pass_started_at is not null",
    )
    op.create_table(
        "synchronization_seen",
        sa.Column(
            "source_id",
            sa.Uuid(),
            sa.ForeignKey("sources.source_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "source_id", "external_id", name="pk_synchronization_seen"
        ),
    )
    op.create_table(
        "synchronization_checkpoints",
        sa.Column(
            "source_id",
            sa.Uuid(),
            sa.ForeignKey("sources.source_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("checkpoint", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "source_id", "checkpoint", name="pk_synchronization_checkpoints"
        ),
    )


def downgrade() -> None:
    op.drop_table("synchronization_checkpoints")
    op.drop_table("synchronization_seen")
    op.drop_constraint("ck_sources_checkpoint_pass", "sources", type_="check")
    op.drop_column("sources", "pass_started_at")
    # The resume point means nothing without the pass it belongs to.
    op.execute("update sources set checkpoint = null")
