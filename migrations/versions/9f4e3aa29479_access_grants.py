"""access grants

Revision ID: 9f4e3aa29479
Revises: 559156a3fbd6
Create Date: 2026-10-07 16:43:45.498443

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9f4e3aa29479"
down_revision: str | Sequence[str] | None = "559156a3fbd6"


def upgrade() -> None:
    """Create one row per subject allowed to read a Document."""
    op.create_table(
        "access_grants",
        sa.Column("grant_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_kind", sa.String(length=8), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "subject_kind in ('public', 'user', 'group')",
            name="ck_access_grants_subject_kind",
        ),
        sa.CheckConstraint(
            "(subject_kind = 'public') = (subject_id is null)",
            name="ck_access_grants_subject_id",
        ),
        sa.UniqueConstraint(
            "document_id",
            "subject_kind",
            "subject_id",
            name="uq_access_grants_document_subject",
            postgresql_nulls_not_distinct=True,
        ),
    )


def downgrade() -> None:
    """Remove every stored Access Grant."""
    op.drop_table("access_grants")
