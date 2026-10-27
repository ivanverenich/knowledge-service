"""index configurations

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-10-27 14:25:32.026280

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "a5b6c7d8e9f0"
down_revision: str | Sequence[str] | None = "f4a5b6c7d8e9"


def upgrade() -> None:
    """Store each distinct set of settings that shapes stored vectors."""
    op.create_table(
        "index_configurations",
        sa.Column("configuration_id", sa.Uuid(), primary_key=True),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("settings", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("fingerprint", name="uq_index_configurations_fingerprint"),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_index_configurations_fingerprint",
        ),
    )


def downgrade() -> None:
    """Drop the stored index configurations."""
    op.drop_table("index_configurations")
