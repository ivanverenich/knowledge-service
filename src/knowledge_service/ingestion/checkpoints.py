"""Make a synchronization pass resumable, one committed page at a time.

A pass stores its progress in PostgreSQL: when it started, whether it is
full or incremental, the checkpoint it resumes from, every checkpoint it
stored, and the last report of each external ID it met. Each step is one
transaction that first checks the pass still holds the Source's fence:

1. `begin_pass` claims the fence and resumes the stored pass, if it started
   at most `MAX_RESUME_AGE` ago, or starts a new one. A new pass is
   incremental when the Source follows changes, has a stored cursor, and
   had a full pass within `FULL_PASS_INTERVAL`; otherwise it is full.
2. `commit_page` runs after every record of a page is reconciled. It
   stores each external ID's last report on the page, offered or deleted,
   and only then moves the checkpoint past the page. A crash before this
   commit replays the page, which reconciliation tolerates: an unchanged
   record writes nothing.
3. `finish_pass` runs after the last page. A full pass tombstones every
   available Document no attempt saw offered; an incremental pass, which
   sees only changes, tombstones only what the Source reported deleted.
   Either way it keeps the Source's new change cursor and clears the
   progress, so the next pass starts from the beginning.

Nothing is tombstoned before the last page: an item reported deleted on one
page may be offered again on a later one, and the last report wins.

A resumed pass does not read the pages before its checkpoint again, so a
change the Source made there waits for the next pass. `MAX_RESUME_AGE`
bounds that wait. An incremental pass trusts the Source to report every
change; the periodic full pass catches what it did not.
"""

from collections.abc import Collection
from datetime import datetime, timedelta
from typing import Final

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.deletions import (
    Tombstone,
    tombstone_reported_deleted,
    tombstone_unseen,
)
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SynchronizationSuperseded,
    SyncMode,
)
from knowledge_service.persistence.sources import claim_synchronization_fence
from knowledge_service.persistence.synchronization_progress import (
    PassProgress,
    advance_checkpoint,
    end_pass,
    forget_pass,
    record_reports,
    remember_checkpoint,
    start_or_resume_pass,
)

# Older progress is dropped and the pass starts again from the beginning, so
# a page skipped by a resume is read again within this time.
MAX_RESUME_AGE: Final = timedelta(hours=1)
# How long a Source with a change feed may go without a full pass, which
# catches what its incremental passes missed.
FULL_PASS_INTERVAL: Final = timedelta(hours=24)


def mode_of(progress: PassProgress) -> SyncMode:
    """The mode a stored pass reads its Source in."""
    return SyncMode.INCREMENTAL if progress.incremental else SyncMode.FULL


async def begin_pass(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    observed_at: datetime,
    follows_changes: bool = False,
    max_resume_age: timedelta = MAX_RESUME_AGE,
    full_pass_interval: timedelta = FULL_PASS_INTERVAL,
) -> PassProgress:
    """Claim the fence, then resume the stored pass or start a new one.

    Raises `SynchronizationSuperseded` when a pass that observed the Source
    at the same time or later already claimed it.
    """
    async with runtime.transaction() as connection:
        if not await claim_synchronization_fence(
            connection, source_id=source_id, observed_at=observed_at
        ):
            raise SynchronizationSuperseded("a newer synchronization pass started")
        return await start_or_resume_pass(
            connection,
            source_id=source_id,
            observed_at=observed_at,
            max_age=max_resume_age,
            follows_changes=follows_changes,
            full_interval=full_pass_interval,
        )


async def commit_page(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    observed_at: datetime,
    offered: Collection[str],
    deleted: Collection[str],
    next_checkpoint: str | None,
) -> None:
    """Record one reconciled page and move the checkpoint past it, together.

    `offered` and `deleted` are the page's external IDs by their last report.
    With `next_checkpoint` None, the page was the last one and the checkpoint
    stays. Writes nothing and raises `SynchronizationSuperseded` when a newer
    pass holds the fence, or `InvalidSource` when the pass stored
    `next_checkpoint` before.
    """
    async with runtime.transaction() as connection:
        # First, so the Source's row stays locked for the whole commit.
        if not await advance_checkpoint(
            connection,
            source_id=source_id,
            observed_at=observed_at,
            checkpoint=next_checkpoint,
        ):
            raise SynchronizationSuperseded("a newer synchronization pass started")
        if next_checkpoint is not None and not await remember_checkpoint(
            connection, source_id=source_id, checkpoint=next_checkpoint
        ):
            # Raising rolls the whole commit back, so it is never stored.
            raise InvalidSource("the source repeated a checkpoint")
        await record_reports(
            connection, source_id=source_id, offered=offered, deleted=deleted
        )


async def finish_pass(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    observed_at: datetime,
    mode: SyncMode = SyncMode.FULL,
    cursor: str | None = None,
) -> tuple[Tombstone, ...]:
    """Tombstone what the pass found gone, keep `cursor`, clear the progress.

    `cursor` is the Source's token for the next incremental pass, from the
    last page; None when the Source has no change feed. Raises
    `SynchronizationSuperseded`, writing nothing, when a newer pass holds
    the fence.
    """
    full = mode is SyncMode.FULL
    async with runtime.transaction() as connection:
        if not await end_pass(
            connection,
            source_id=source_id,
            observed_at=observed_at,
            cursor=cursor,
            full=full,
        ):
            raise SynchronizationSuperseded("a newer synchronization pass started")
        if full:
            tombstones = await tombstone_unseen(
                connection, source_id=source_id, observed_at=observed_at
            )
        else:
            tombstones = await tombstone_reported_deleted(
                connection, source_id=source_id, observed_at=observed_at
            )
        await forget_pass(connection, source_id=source_id)
    return tombstones
