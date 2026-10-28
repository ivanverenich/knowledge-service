"""Statements over Sources and their synchronization runs."""

from typing import Any
from uuid import UUID

import sqlalchemy as sa

from knowledge_service.persistence.schema import synchronization_runs


def source_runs_statement(source_id: UUID, limit: int) -> sa.Select[Any]:
    """Select the most recent Synchronization Runs of one Source."""
    return (
        sa.select(synchronization_runs)
        .where(synchronization_runs.c.source_id == source_id)
        .order_by(synchronization_runs.c.started_at.desc())
        .limit(limit)
    )
