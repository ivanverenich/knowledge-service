"""document metadata

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-10-26 22:40:59.795424

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4a5b6c7d8e9"
down_revision: str | Sequence[str] | None = "e3f4a5b6c7d8"


def upgrade() -> None:
    """Store what retrieval may filter each Document by.

    Existing Documents get their row on their next reconcile; until then a
    metadata filter does not match them.
    """
    op.create_table(
        "document_metadata",
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "media_type <> '' and length(media_type) <= 255",
            name="ck_document_metadata_media_type",
        ),
        sa.CheckConstraint(
            "language in ('en', 'uk', 'mixed', 'unknown')",
            name="ck_document_metadata_language",
        ),
    )


def downgrade() -> None:
    """Drop the stored Document metadata."""
    op.drop_table("document_metadata")
