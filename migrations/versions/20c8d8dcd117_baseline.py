"""baseline

Revision ID: 20c8d8dcd117
Revises:
Create Date: 2026-10-05 19:28:46.331368

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "20c8d8dcd117"
down_revision: str | Sequence[str] | None = None


def upgrade() -> None:
    """Record the starting point; later revisions add application tables."""
    pass


def downgrade() -> None:
    """Return to the state before the baseline revision."""
    pass
