"""conversations and feedback

Revision ID: 4c3e640575eb
Revises: 9f4e3aa29479
Create Date: 2026-10-08 11:21:49.315179

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4c3e640575eb"
down_revision: str | Sequence[str] | None = "9f4e3aa29479"


def upgrade() -> None:
    """Create owned Conversations, their Messages, and one rating per Message."""
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "retention_deadline > created_at", name="ck_conversations_retention"
        ),
        sa.CheckConstraint(
            "last_message_at >= created_at", name="ck_conversations_last_message"
        ),
        sa.CheckConstraint(
            "deleted_at is null or deleted_at >= created_at",
            name="ck_conversations_deleted_at",
        ),
    )

    op.create_table(
        "messages",
        sa.Column("message_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "conversation_id", "ordinal", name="uq_messages_conversation_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 0", name="ck_messages_ordinal"),
        sa.CheckConstraint("role in ('user', 'assistant')", name="ck_messages_role"),
    )

    op.create_table(
        "message_feedback",
        sa.Column(
            "message_id",
            sa.Uuid(),
            sa.ForeignKey("messages.message_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("user_id", sa.Uuid(), primary_key=True),
        sa.Column("rating", sa.String(length=8), nullable=False),
        sa.Column("rated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "rating in ('up', 'down')", name="ck_message_feedback_rating"
        ),
    )


def downgrade() -> None:
    """Remove stored feedback, Messages, and Conversations."""
    op.drop_table("message_feedback")
    op.drop_table("messages")
    op.drop_table("conversations")
