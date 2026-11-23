"""job open intent

Revision ID: e5f6a7b8c9d0
Revises: c2d3e4f5a6b7
Create Date: 2026-11-22 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "c2d3e4f5a6b7"

OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and attempt < max_attempts)"
)

# Earlier code queued a new Job for every request, so two open Jobs can share
# an intent. Keep one per intent and close the others as failed with no
# attempts left. Their work is the same, so the one kept still does it. A
# running Job is kept first, then a queued one, because a worker will run it;
# a failed one waits for a retry, so it is kept only when nothing else is.
CLOSE_DUPLICATE_OPEN_JOBS = """
with ranked as (
    select
        job_id,
        row_number() over (
            partition by kind, target_id
            order by
                case status when 'running' then 0 when 'queued' then 1 else 2 end,
                queued_at,
                job_id
        ) as rank
    from jobs
    where status in ('queued', 'running')
        or (status = 'failed' and attempt < max_attempts)
)
update jobs
set status = 'failed',
    attempt = max_attempts,
    started_at = coalesce(started_at, queued_at),
    finished_at = coalesce(finished_at, greatest(now(), started_at, queued_at)),
    last_error_code = 'superseded_duplicate'
from ranked
where jobs.job_id = ranked.job_id and ranked.rank > 1
"""


def upgrade() -> None:
    op.execute(CLOSE_DUPLICATE_OPEN_JOBS)
    op.create_index(
        "uq_jobs_open_intent",
        "jobs",
        ["kind", "target_id"],
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    )


def downgrade() -> None:
    # Duplicates closed by the upgrade stay closed: reopening them would
    # queue the same work twice.
    op.drop_index("uq_jobs_open_intent", table_name="jobs")
