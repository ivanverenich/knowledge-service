"""Make a synchronization pass resumable, one committed page at a time.

A pass stores its progress in PostgreSQL: when it started, the checkpoint it
resumes from, every checkpoint it stored, and the external IDs it has seen.
Each step is one transaction that first checks the pass still holds the
Source's fence:

1. `begin_pass` claims the fence and resumes the stored pass, if it started
   at most `MAX_RESUME_AGE` ago, or starts a new one.
2. `commit_page` runs after every record of a page is reconciled. It
   stores which external IDs the page offered and forgets the ones it
   reported deleted, and only then moves the checkpoint past the page. A
   crash before this commit replays the page, which reconciliation
   tolerates: an unchanged record writes nothing.
3. `finish_pass` runs after the last page. It tombstones every available
   Document the pass never saw, or last saw reported deleted, counting the
   pages of earlier attempts, and clears the progress, so the next pass
   starts from the beginning.

Nothing is tombstoned before the last page: an item reported deleted on one
page may be offered again on a later one, and the last report wins.

A resumed pass does not read the pages before its checkpoint again, so a
change the Source made there waits for the next pass. `MAX_RESUME_AGE`
bounds that wait.
"""

from collections.abc import Collection
from datetime import datetime, timedelta
from typing import Final

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.deletions import Tombstone, tombstone_unseen
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SynchronizationSuperseded,
)
from knowledge_service.persistence.sources import claim_synchronization_fence
from knowledge_service.persistence.synchronization_progress import (
    PassProgress,
    advance_checkpoint,
    end_pass,
    forget_pass,
    forget_seen,
    record_seen,
    remember_checkpoint,
    start_or_resume_pass,
)

# Older progress is dropped and the pass starts again from the beginning, so
# a page skipped by a resume is read again within this time.
MAX_RESUME_AGE: Final = timedelta(hours=1)


async def begin_pass(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    observed_at: datetime,
    max_resume_age: timedelta = MAX_RESUME_AGE,
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
        await forget_seen(connection, source_id=source_id, external_ids=deleted)
        await record_seen(connection, source_id=source_id, external_ids=offered)


async def finish_pass(
    runtime: DatabaseRuntime, *, source_id: SourceId, observed_at: datetime
) -> tuple[Tombstone, ...]:
    """Tombstone what the whole pass never saw and clear its progress.

    Raises `SynchronizationSuperseded`, writing nothing, when a newer pass
    holds the fence.
    """
    async with runtime.transaction() as connection:
        if not await end_pass(connection, source_id=source_id, observed_at=observed_at):
            raise SynchronizationSuperseded("a newer synchronization pass started")
        tombstones = await tombstone_unseen(
            connection, source_id=source_id, observed_at=observed_at
        )
        await forget_pass(connection, source_id=source_id)
    return tombstones
