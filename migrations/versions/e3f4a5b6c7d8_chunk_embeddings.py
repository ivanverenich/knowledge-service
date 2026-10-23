"""chunk embeddings

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-10-23 11:15:18.795757

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "d2e3f4a5b6c7"


def upgrade() -> None:
    """Enable pgvector, then store versioned Chunk embeddings per space."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "embedding_spaces",
        sa.Column("space_id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider", "model", "dimensions", name="uq_embedding_spaces_identity"
        ),
        sa.UniqueConstraint(
            "space_id", "dimensions", name="uq_embedding_spaces_space_dimensions"
        ),
        sa.CheckConstraint(
            "dimensions between 1 and 16000", name="ck_embedding_spaces_dimensions"
        ),
        sa.CheckConstraint(
            "provider <> '' and provider = btrim(provider)"
            " and model <> '' and model = btrim(model)",
            name="ck_embedding_spaces_identity",
        ),
    )
    op.create_index(
        "uq_embedding_spaces_current",
        "embedding_spaces",
        ["is_current"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "chunk_embeddings",
        sa.Column(
            "chunk_id",
            sa.Uuid(),
            sa.ForeignKey("chunks.chunk_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("space_id", sa.Uuid(), primary_key=True),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["space_id", "dimensions"],
            ["embedding_spaces.space_id", "embedding_spaces.dimensions"],
            ondelete="CASCADE",
            name="fk_chunk_embeddings_space_dimensions",
        ),
        sa.CheckConstraint(
            "vector_dims(embedding) = dimensions",
            name="ck_chunk_embeddings_dimensions",
        ),
    )
    op.create_index("ix_chunk_embeddings_space_id", "chunk_embeddings", ["space_id"])


def downgrade() -> None:
    """Drop the embedding tables.

    The extension stays: it may have existed before this migration, and other
    objects may use it.
    """
    op.drop_index("ix_chunk_embeddings_space_id", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
    op.drop_index("uq_embedding_spaces_current", table_name="embedding_spaces")
    op.drop_table("embedding_spaces")
