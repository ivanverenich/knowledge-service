"""documents

Revision ID: 5e8b9875be50
Revises: b96066f98718
Create Date: 2026-10-07 13:17:21.368755

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5e8b9875be50"
down_revision: str | Sequence[str] | None = "b96066f98718"


def upgrade() -> None:
    """Create the current Document state for each source item."""
    op.create_table(
        "documents",
        sa.Column("document_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Uuid(),
            sa.ForeignKey("sources.source_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("source_version", sa.Text(), nullable=True),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column("authorization_version", sa.Integer(), nullable=False),
        sa.Column("authorization_fingerprint", sa.Text(), nullable=False),
        sa.Column("availability", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_id", "external_id", name="uq_documents_source_external"
        ),
        sa.CheckConstraint("content_version >= 1", name="ck_documents_content_version"),
        sa.CheckConstraint(
            "authorization_version >= 1", name="ck_documents_authorization_version"
        ),
        sa.CheckConstraint(
            "availability in ('available', 'tombstoned')",
            name="ck_documents_availability",
        ),
    )


def downgrade() -> None:
    """Remove every stored Document."""
    op.drop_table("documents")
