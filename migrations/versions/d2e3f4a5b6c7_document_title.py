"""document title

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-10-22 12:50:37.284331

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c7"
down_revision: str | Sequence[str] | None = "c1d2e3f4a5b6"


def upgrade() -> None:
    """Store each Document's title, with a placeholder for existing rows."""
    op.add_column("documents", sa.Column("title", sa.Text(), nullable=True))
    # A fixed placeholder is always a valid title; one derived from the external
    # ID can be blank by Python's rules. The next run stores the real title.
    op.execute("UPDATE documents SET title = 'Untitled'")
    op.alter_column("documents", "title", nullable=False)


def downgrade() -> None:
    """Drop the title column."""
    op.drop_column("documents", "title")
