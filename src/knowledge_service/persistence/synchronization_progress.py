"""Statements over the stored progress of a Source's synchronization pass.

The progress is five facts: when the pass in progress started
(`sources.pass_started_at`), whether it is incremental
(`sources.pass_mode`), the checkpoint it resumes from (`sources.checkpoint`),
every checkpoint it has stored (`synchronization_checkpoints`), and each
external ID it has met so far with its last report, present or deleted
(`synchronization_seen`). Between passes a Source keeps its change cursor
(`sources.sync_cursor`) and when its last full pass finished
(`sources.last_full_pass_at`). `advance_checkpoint` and `end_pass` check, in
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

_FULL = "full"
_INCREMENTAL = "incremental"


@dataclass(frozen=True, slots=True)
class PassProgress:
    """Where a pass starts, and how it reads its Source.

    `since` is the change cursor an incremental pass reads after; None for
    a full pass.
    """

    started_at: datetime
    checkpoint: str | None
    resumed: bool
    incremental: bool
    since: str | None


async def start_or_resume_pass(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    observed_at: datetime,
    max_age: timedelta,
    follows_changes: bool,
    full_interval: timedelta,
) -> PassProgress:
    """Resume the stored pass if it started at most `max_age` ago, or start anew.

    A new pass is incremental only when the Source `follows_changes`, has a
    stored cursor, and its last full pass finished less than `full_interval`
    before `observed_at`; otherwise it is full. A resumed pass keeps the mode
    it started with, unless it is incremental and the Source no longer
    follows changes: then a full pass starts. Call it in the transaction that
    claimed the fence, so no older pass can store progress between the claim
    and this read. Starting anew forgets everything an earlier pass stored.
    """
    row = (
        await connection.execute(
            sa.select(
                sources.c.checkpoint,
                sources.c.pass_started_at,
                sources.c.pass_mode,
                sources.c.sync_cursor,
                sources.c.last_full_pass_at,
            )
            .where(sources.c.source_id == source_id.value)
            .with_for_update()
        )
    ).one()
    started_at: datetime | None = row.pass_started_at
    cursor: str | None = row.sync_cursor
    stored_incremental = row.pass_mode == _INCREMENTAL
    if (
        started_at is not None
        and started_at <= observed_at <= started_at + max_age
        # An incremental pass needs a change feed; without one, start over.
        and (follows_changes or not stored_incremental)
    ):
        incremental = stored_incremental
        return PassProgress(
            started_at=started_at,
            checkpoint=row.checkpoint,
            resumed=True,
            incremental=incremental,
            since=cursor if incremental else None,
        )
    last_full: datetime | None = row.last_full_pass_at
    incremental = (
        follows_changes
        and cursor is not None
        and last_full is not None
        and last_full <= observed_at < last_full + full_interval
    )
    await forget_pass(connection, source_id=source_id)
    await connection.execute(
        sa.update(sources)
        .where(sources.c.source_id == source_id.value)
        .values(
            pass_started_at=observed_at,
            pass_mode=_INCREMENTAL if incremental else _FULL,
            checkpoint=None,
        )
    )
    return PassProgress(
        started_at=observed_at,
        checkpoint=None,
        resumed=False,
        incremental=incremental,
        since=cursor if incremental else None,
    )


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
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    observed_at: datetime,
    cursor: str | None,
    full: bool,
) -> bool:
    """Clear the pass's resume point and keep `cursor`, if it holds the fence.

    A full pass also records `observed_at` as the last full pass. The seen
    IDs stay until `forget_pass`, so the same transaction can still tombstone
    what the pass found gone.
    """
    values: dict[str, object] = {
        "checkpoint": None,
        "pass_started_at": None,
        "pass_mode": None,
        "sync_cursor": cursor,
    }
    if full:
        values["last_full_pass_at"] = observed_at
    held = await connection.scalar(
        sa.update(sources)
        .where(
            sources.c.source_id == source_id.value,
            sources.c.synchronization_fence == observed_at,
            sources.c.pass_started_at.is_not(None),
        )
        .values(values)
        .returning(sources.c.source_id)
    )
    return held is not None


async def record_reports(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    offered: Collection[str],
    deleted: Collection[str],
) -> None:
    """Store each external ID's last report in the pass: offered or deleted.

    A later report of the same ID replaces an earlier one.
    """
    reports = dict.fromkeys(offered, False)
    reports.update(dict.fromkeys(deleted, True))
    for batch in _batches(reports):
        statement = insert(synchronization_seen).values(
            [
                {
                    "source_id": source_id.value,
                    "external_id": external_id,
                    "deleted": reports[external_id],
                }
                for external_id in batch
            ]
        )
        await connection.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    synchronization_seen.c.source_id,
                    synchronization_seen.c.external_id,
                ],
                set_={"deleted": statement.excluded.deleted},
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
    """Available Documents the pass never saw, or last saw reported deleted."""
    offered = sa.exists().where(
        synchronization_seen.c.source_id == documents.c.source_id,
        synchronization_seen.c.external_id == documents.c.external_id,
        synchronization_seen.c.deleted.is_(False),
    )
    return await _available(connection, source_id=source_id, condition=~offered)


async def load_reported_deleted_available(
    connection: AsyncConnection, *, source_id: SourceId
) -> tuple[DocumentId, ...]:
    """Available Documents whose last report in the pass said deleted."""
    deleted = sa.exists().where(
        synchronization_seen.c.source_id == documents.c.source_id,
        synchronization_seen.c.external_id == documents.c.external_id,
        synchronization_seen.c.deleted.is_(True),
    )
    return await _available(connection, source_id=source_id, condition=deleted)


async def _available(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    condition: sa.ColumnElement[bool],
) -> tuple[DocumentId, ...]:
    rows = await connection.scalars(
        sa.select(documents.c.document_id)
        .where(
            documents.c.source_id == source_id.value,
            documents.c.availability == DocumentAvailability.AVAILABLE.value,
            condition,
        )
        .order_by(documents.c.external_id)
    )
    return tuple(DocumentId(document_id) for document_id in rows)


def _batches(external_ids: Collection[str]) -> Iterator[Sequence[str]]:
    """The IDs in sorted batches, so concurrent writers lock rows in one order."""
    return batched(sorted(external_ids), BATCH_SIZE, strict=False)
