"""One synchronization pass per Source at a time, best effort.

Two passes over one Source at once waste work: both page the Source (a
remote API with rate limits and, later, a cost) and parse the same files.
A lease in Redis keeps a second pass from even starting. It is only a hint:
it can expire while its holder still works. What keeps the data right when
that happens is the Source's fence, which every pass of `synchronize_source`
claims and holds in PostgreSQL.

A pass takes the Source's lease before it reads anything, renews it after
every page, and gives it up however the pass ends. If renewing finds the
lease gone, the pass stops before it tombstones anything. If the holder
crashes, the lease expires and the next pass runs. The lease errors are
`SourceUnavailable`, so a Job that meets one is retried later (M08-T04).
"""

import logging
from datetime import datetime, timedelta
from typing import Final

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import SourceId, SynchronizationRunId
from knowledge_service.ingestion.grant_normalization import PrincipalDirectory
from knowledge_service.ingestion.sources import SourcePort, SourceUnavailable
from knowledge_service.ingestion.synchronization import (
    PAGE_SIZE,
    SynchronizationSummary,
    synchronize_source,
)
from knowledge_service.leases import LeaseStore, LeaseStoreUnavailable

logger = logging.getLogger(__name__)


# Longer than any one page takes, so a working pass keeps its lease, and
# short enough that a crashed pass blocks the Source only briefly.
SYNC_LEASE_TTL: Final = timedelta(minutes=2)


class SourceBusy(SourceUnavailable):
    """Another pass holds this Source's lease; try again later."""


class SourceLeaseLost(SourceUnavailable):
    """This pass lost its lease part-way, so it stopped before tombstoning."""


class SourceCoordinationUnavailable(SourceUnavailable):
    """The lease store did not answer, so no pass may start or go on."""


def lease_name(source_id: SourceId) -> str:
    """The lease every synchronization pass of this Source shares."""
    return f"source-sync:{source_id.value}"


async def synchronize_source_exclusively(
    runtime: DatabaseRuntime,
    source: SourcePort,
    *,
    leases: LeaseStore,
    source_id: SourceId,
    observed_at: datetime,
    principal_directory: PrincipalDirectory | None,
    page_size: int = PAGE_SIZE,
    run_id: SynchronizationRunId | None = None,
    ttl: timedelta = SYNC_LEASE_TTL,
) -> SynchronizationSummary:
    """`synchronize_source`, while holding the Source's lease.

    Raises `SourceBusy` before reading anything when another pass holds the
    lease, `SourceLeaseLost` when the lease is gone at a renewal, and
    `SourceCoordinationUnavailable` when the lease store does not answer.
    `synchronize_source`'s own errors, such as `SynchronizationSuperseded`,
    pass through.
    """
    try:
        lease = await leases.acquire(lease_name(source_id), ttl)
    except LeaseStoreUnavailable as error:
        raise SourceCoordinationUnavailable("cannot take the source lease") from error
    if lease is None:
        raise SourceBusy("another synchronization pass holds this source")

    async def keep_lease() -> None:
        try:
            held = await leases.renew(lease)
        except LeaseStoreUnavailable as error:
            raise SourceCoordinationUnavailable(
                "cannot renew the source lease"
            ) from error
        if not held:
            raise SourceLeaseLost("the source lease expired during the pass")

    try:
        return await synchronize_source(
            runtime,
            source,
            source_id=source_id,
            observed_at=observed_at,
            principal_directory=principal_directory,
            page_size=page_size,
            run_id=run_id,
            heartbeat=keep_lease,
        )
    finally:
        try:
            await leases.release(lease)
        except LeaseStoreUnavailable as error:
            # It expires on its own within its time to live.
            logger.warning(
                "source %s lease not released: %s",
                str(source_id.value),
                type(error).__name__,
            )
