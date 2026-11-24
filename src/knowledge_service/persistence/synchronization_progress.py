"""Statements over the stored progress of a Source's synchronization pass.

The progress is four facts: when the pass in progress started
(`sources.pass_started_at`), the checkpoint it resumes from
(`sources.checkpoint`), every checkpoint it has stored
(`synchronization_checkpoints`), and the external IDs it has seen so far
(`synchronization_seen`). `advance_checkpoint` and `end_pass` check, in
the same statement, that the pass still holds the Source's fence, and lock
the Source's row; the other writes run in the transaction that checked it.
"""

from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import batched

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.documents import DocumentAvailability
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.persistence.schema import (
    documents,
    sources,
    synchronization_checkpoints,
    synchronization_seen,
)

# PostgreSQL takes at most 65,535 parameters in one statement; a page may
# hold more IDs than that, so they are written this many at a time.
BATCH_SIZE = 1000


@dataclass(frozen=True, slots=True)
class PassProgress:
    """Where a pass starts: from the beginning, or from a stored checkpoint."""

    started_at: datetime
    checkpoint: str | None
    resumed: bool


async def start_or_resume_pass(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    observed_at: datetime,
    max_age: timedelta,
) -> PassProgress:
    """Resume the stored pass if it started at most `max_age` ago, or start anew.

    Call it in the transaction that claimed the fence, so no older pass can
    store progress between the claim and this read. Starting anew forgets
    everything an earlier pass stored.
    """
    row = (
        await connection.execute(
            sa.select(sources.c.checkpoint, sources.c.pass_started_at)
            .where(sources.c.source_id == source_id.value)
            .with_for_update()
        )
    ).one()
    started_at: datetime | None = row.pass_started_at
    if started_at is not None and started_at <= observed_at <= started_at + max_age:
        return PassProgress(
            started_at=started_at, checkpoint=row.checkpoint, resumed=True
        )
    await forget_pass(connection, source_id=source_id)
    await connection.execute(
        sa.update(sources)
        .where(sources.c.source_id == source_id.value)
        .values(pass_started_at=observed_at, checkpoint=None)
    )
    return PassProgress(started_at=observed_at, checkpoint=None, resumed=False)


async def advance_checkpoint(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    observed_at: datetime,
    checkpoint: str | None,
) -> bool:
    """Store `checkpoint` as the resume point, if this pass holds the fence.

    None keeps the stored checkpoint: after the last page the pass resumes
    from that page, not past it. False, and nothing written, when a newer
    pass claimed the fence or no pass is in progress. The Source's row stays
    locked until the transaction ends, so no newer claim lands in between.
    """
    resume_from = (
        sources.c.checkpoint
        if checkpoint is None
        else sa.literal(checkpoint, sa.Text())
    )
    held = await connection.scalar(
        sa.update(sources)
        .where(
            sources.c.source_id == source_id.value,
            sources.c.synchronization_fence == observed_at,
            sources.c.pass_started_at.is_not(None),
        )
        .values(checkpoint=resume_from)
        .returning(sources.c.source_id)
    )
    return held is not None


async def remember_checkpoint(
    connection: AsyncConnection, *, source_id: SourceId, checkpoint: str
) -> bool:
    """Note that the pass stored `checkpoint`; False if it stored it before."""
    added = await connection.scalar(
        insert(synchronization_checkpoints)
        .values(source_id=source_id.value, checkpoint=checkpoint)
        .on_conflict_do_nothing()
        .returning(synchronization_checkpoints.c.checkpoint)
    )
    return added is not None


async def end_pass(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> bool:
    """Clear the stored resume point, if this pass holds the fence.

    The seen IDs stay until `forget_pass`, so the same transaction can still
    tombstone what the pass did not see.
    """
    held = await connection.scalar(
        sa.update(sources)
        .where(
            sources.c.source_id == source_id.value,
            sources.c.synchronization_fence == observed_at,
            sources.c.pass_started_at.is_not(None),
        )
        .values(checkpoint=None, pass_started_at=None)
        .returning(sources.c.source_id)
    )
    return held is not None


async def record_seen(
    connection: AsyncConnection, *, source_id: SourceId, external_ids: Collection[str]
) -> None:
    """Remember that the pass in progress saw these external IDs."""
    for batch in _batches(external_ids):
        await connection.execute(
            insert(synchronization_seen)
            .values(
                [
                    {"source_id": source_id.value, "external_id": external_id}
                    for external_id in batch
                ]
            )
            .on_conflict_do_nothing()
        )


async def forget_seen(
    connection: AsyncConnection, *, source_id: SourceId, external_ids: Collection[str]
) -> None:
    """Forget these external IDs: the Source reported them deleted."""
    for batch in _batches(external_ids):
        await connection.execute(
            sa.delete(synchronization_seen).where(
                synchronization_seen.c.source_id == source_id.value,
                synchronization_seen.c.external_id.in_(batch),
            )
        )


async def forget_pass(connection: AsyncConnection, *, source_id: SourceId) -> None:
    """Forget every external ID and checkpoint the pass stored."""
    for table in (synchronization_seen, synchronization_checkpoints):
        await connection.execute(
            sa.delete(table).where(table.c.source_id == source_id.value)
        )


async def load_unseen_available(
    connection: AsyncConnection, *, source_id: SourceId
) -> tuple[DocumentId, ...]:
    """Available Documents of the Source the pass in progress has not seen."""
    seen = sa.exists().where(
        synchronization_seen.c.source_id == documents.c.source_id,
        synchronization_seen.c.external_id == documents.c.external_id,
    )
    rows = await connection.scalars(
        sa.select(documents.c.document_id)
        .where(
            documents.c.source_id == source_id.value,
            documents.c.availability == DocumentAvailability.AVAILABLE.value,
            ~seen,
        )
        .order_by(documents.c.external_id)
    )
    return tuple(DocumentId(document_id) for document_id in rows)


def _batches(external_ids: Collection[str]) -> Iterator[Sequence[str]]:
    """The IDs in sorted batches, so concurrent writers lock rows in one order."""
    return batched(sorted(external_ids), BATCH_SIZE, strict=False)
