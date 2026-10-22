"""purge document chunks job kind

Revision ID: c1d2e3f4a5b6
Revises: 09aa0992e559
Create Date: 2026-10-22 09:34:11.830417

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "09aa0992e559"


def upgrade() -> None:
    """Allow a Job that deletes a tombstoned Document's Chunks."""
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind in ('synchronize_source', 'purge_conversation', 'purge_document_chunks')",
    )


def downgrade() -> None:
    """Remove purge Jobs, then allow only the original kinds again."""
    op.execute("DELETE FROM jobs WHERE kind = 'purge_document_chunks'")
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_kind", "jobs", "kind in ('synchronize_source', 'purge_conversation')"
    )
