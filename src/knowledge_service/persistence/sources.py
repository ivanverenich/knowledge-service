"""Statements over Sources, their synchronization runs, and their fence."""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.identifiers import SourceId
from knowledge_service.persistence.schema import sources, synchronization_runs


def source_runs_statement(source_id: UUID, limit: int) -> sa.Select[Any]:
    """Select the most recent Synchronization Runs of one Source."""
    return (
        sa.select(synchronization_runs)
        .where(synchronization_runs.c.source_id == source_id)
        .order_by(synchronization_runs.c.started_at.desc())
        .limit(limit)
    )


async def claim_synchronization_fence(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> bool:
    """Make `observed_at` the Source's fence, if it is newer than the fence.

    False when a pass that observed the Source at the same time or later
    already claimed it. The update waits for every write that holds the
    fence (`synchronization_fence_holds`), so none of them lands after it.
    """
    claimed = await connection.scalar(
        sa.update(sources)
        .where(
            sources.c.source_id == source_id.value,
            sa.or_(
                sources.c.synchronization_fence.is_(None),
                sources.c.synchronization_fence < observed_at,
            ),
        )
        .values(synchronization_fence=observed_at)
        .returning(sources.c.source_id)
    )
    return claimed is not None


async def synchronization_fence_holds(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> bool:
    """Whether the pass that observed at `observed_at` still holds the fence.

    The Source's row is share-locked until the transaction ends, so a newer
    pass cannot claim the fence while this write is in progress.
    """
    fence = await connection.scalar(
        sa.select(sources.c.synchronization_fence)
        .where(sources.c.source_id == source_id.value)
        .with_for_update(read=True)
    )
    return fence == observed_at
