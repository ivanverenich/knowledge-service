"""synchronization modes

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-11-25 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"


def upgrade() -> None:
    # Who may read every item of a Source whose items carry no access of
    # their own, such as a local folder. The default lets nobody read.
    op.add_column(
        "sources",
        sa.Column(
            "default_public",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "default_principals",
            ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.create_check_constraint(
        "ck_sources_default_access",
        "sources",
        "not (default_public and cardinality(default_principals) > 0)",
    )
    op.add_column("sources", sa.Column("sync_cursor", sa.Text(), nullable=True))
    op.add_column(
        "sources",
        sa.Column("last_full_pass_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sources", sa.Column("pass_mode", sa.String(length=16), nullable=True)
    )
    # A pass stored before this revision was a full one.
    op.execute(
        "update sources set pass_mode = 'full' where pass_started_at is not null"
    )
    op.create_check_constraint(
        "ck_sources_pass_mode",
        "sources",
        "(pass_started_at is null) = (pass_mode is null)"
        " and (pass_mode is null or pass_mode in ('full', 'incremental'))",
    )
    op.add_column(
        "synchronization_seen",
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    # An incremental pass in progress has no meaning before this revision,
    # so its progress goes; the next pass starts over as a full one.
    op.execute(
        "delete from synchronization_seen where source_id in"
        " (select source_id from sources where pass_mode = 'incremental')"
    )
    op.execute(
        "delete from synchronization_checkpoints where source_id in"
        " (select source_id from sources where pass_mode = 'incremental')"
    )
    op.execute(
        "update sources set checkpoint = null, pass_started_at = null,"
        " pass_mode = null where pass_mode = 'incremental'"
    )
    # A seen row that says "deleted" meant "not seen" before this revision.
    op.execute("delete from synchronization_seen where deleted")
    op.drop_column("synchronization_seen", "deleted")
    op.drop_constraint("ck_sources_pass_mode", "sources", type_="check")
    op.drop_column("sources", "pass_mode")
    op.drop_column("sources", "last_full_pass_at")
    op.drop_column("sources", "sync_cursor")
    op.drop_constraint("ck_sources_default_access", "sources", type_="check")
    op.drop_column("sources", "default_principals")
    op.drop_column("sources", "default_public")
