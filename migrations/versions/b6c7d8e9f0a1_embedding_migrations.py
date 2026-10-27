"""embedding migrations

Revision ID: b6c7d8e9f0a1
Revises: a5b6c7d8e9f0
Create Date: 2026-10-27 19:08:47.268798

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6c7d8e9f0a1"
down_revision: str | Sequence[str] | None = "a5b6c7d8e9f0"


def upgrade() -> None:
    """Record each move of search from one Embedding Space to another."""
    op.create_table(
        "embedding_migrations",
        sa.Column("migration_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_space_id",
            sa.Uuid(),
            sa.ForeignKey("embedding_spaces.space_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_space_id",
            sa.Uuid(),
            sa.ForeignKey("embedding_spaces.space_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_configuration_id",
            sa.Uuid(),
            sa.ForeignKey("index_configurations.configuration_id"),
            nullable=False,
        ),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state in ('dual_writing', 'switched', 'cleaned')",
            name="ck_embedding_migrations_state",
        ),
        sa.CheckConstraint(
            "updated_at >= started_at", name="ck_embedding_migrations_updated_at"
        ),
    )
    op.create_index(
        "ix_embedding_migrations_source_space_id",
        "embedding_migrations",
        ["source_space_id"],
    )
    op.create_index(
        "ix_embedding_migrations_target_space_id",
        "embedding_migrations",
        ["target_space_id"],
    )
    op.create_index(
        "ix_embedding_migrations_target_configuration_id",
        "embedding_migrations",
        ["target_configuration_id"],
    )


def downgrade() -> None:
    """Drop the recorded embedding migrations."""
    op.drop_table("embedding_migrations")
