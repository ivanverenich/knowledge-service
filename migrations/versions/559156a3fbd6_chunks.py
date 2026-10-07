"""chunks

Revision ID: 559156a3fbd6
Revises: 5e8b9875be50
Create Date: 2026-10-07 16:06:05.213673

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "559156a3fbd6"
down_revision: str | Sequence[str] | None = "5e8b9875be50"


def upgrade() -> None:
    """Create the ordered, citable Chunks of each Document."""
    op.create_table(
        "chunks",
        sa.Column("chunk_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column("source_anchor", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "document_id", "ordinal", name="uq_chunks_document_ordinal"
        ),
        sa.CheckConstraint("content_version >= 1", name="ck_chunks_content_version"),
        sa.CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal"),
        sa.CheckConstraint("token_count >= 1", name="ck_chunks_token_count"),
    )


def downgrade() -> None:
    """Remove every stored Chunk."""
    op.drop_table("chunks")
