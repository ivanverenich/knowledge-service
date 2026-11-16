"""message outcomes and references

Revision ID: a9b0c1d2e3f4
Revises: d8e9f0a1b2c3
Create Date: 2026-11-16 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b0c1d2e3f4"
down_revision: str | Sequence[str] | None = "d8e9f0a1b2c3"


def upgrade() -> None:
    """Remember how each Answer ended and which Chunks it cited.

    Existing assistant Messages keep no outcome; History treats them as
    unverified and leaves their text out.
    """
    op.add_column("messages", sa.Column("outcome", sa.String(length=16), nullable=True))
    op.create_check_constraint(
        "ck_messages_outcome",
        "messages",
        "outcome is null or (role = 'assistant'"
        " and outcome in ('answered', 'abstained', 'failed'))",
    )
    op.create_table(
        "message_references",
        sa.Column(
            "message_id",
            sa.Uuid(),
            sa.ForeignKey("messages.message_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "message_id", "chunk_id", name="uq_message_references_chunk"
        ),
        sa.CheckConstraint("position >= 0", name="ck_message_references_position"),
        sa.CheckConstraint(
            "content_version >= 1", name="ck_message_references_content_version"
        ),
    )


def downgrade() -> None:
    """Forget Message outcomes and references."""
    op.drop_table("message_references")
    op.drop_constraint("ck_messages_outcome", "messages", type_="check")
    op.drop_column("messages", "outcome")
