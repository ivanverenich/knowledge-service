"""Run one complete pass over a Source and publish what it offers.

The pass pages through the Source, normalizes each present record, and
reconciles it. Only after the last page does it tombstone the available
Documents it did not see: a failure part-way through raises before anything
is tombstoned, so a broken run never makes documents disappear.

Each Document's readers come from `normalize_access`: a principal the
Source names but the `PrincipalDirectory` does not map grants nothing, and
the summary reports it so someone can map it. A record that fails to
normalize keeps its last good version, for only the readers its old and new
access both name.

Revocation goes first. Each page is handled in two phases. First, one
query reads the readers every Document of the page has now, and each record
whose new access leaves someone out is narrowed to the readers its old and
new access both name (`narrow_access`); each narrowing commits on its own,
with a new Authorization Version. Only then is any record of the page
parsed, chunked, and reconciled. So a reader the Source removed from a
Document loses it before the slow work of its page, never after it. Readers
the Source adds wait for the record's own reconciliation, with its new
content. A record offered twice in a page counts once, as last offered.

Two kinds of removal still wait: a revocation on a later page waits for the
earlier pages' work, and a Document the Source deletes, or no longer lists,
becomes unavailable only when the pass tombstones it at the end.

Every pass has a Synchronization Run ID. Each grant change it writes names
it, and when the pass ends it records one `synchronization.finished` audit
event against the Source: `succeeded` with counts, or `failed` with the
error's type name, in a transaction of its own. A pass that is cancelled
records nothing; a failed pass whose record cannot be written logs that and
raises its own error.

A caller may pass a `heartbeat`, awaited after every page and so also just
before the tombstones. Raising from it stops the pass there, as a failure:
nothing is tombstoned. `synchronize_source_exclusively` uses it to keep its
lease.

Every pass is fenced. Before it reads anything it claims the Source's fence
with its observation time, which works only when that time is newer than
every earlier pass's, and every transaction that writes a Document (each
narrowing, each reconciliation, the tombstones) first checks, under a share
lock on the Source's row, that it still holds the fence. A newer claim
waits for those writes, and after it the older pass's writes are refused
with `SynchronizationSuperseded`. So an older pass that finishes late can
never undo a newer one. A pass observed more than `MAX_CLOCK_SKEW` in the
future is refused, so a bad clock cannot hold the fence for long.
"""

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
    AuditValue,
)
from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import (
    AuditEventId,
    JobId,
    SourceId,
    SynchronizationRunId,
)
from knowledge_service.ingestion.deletions import (
    Tombstone,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.fencing import WriteGuard
from knowledge_service.ingestion.grant_normalization import (
    NormalizedAccess,
    PrincipalDirectory,
    UnresolvedPrincipal,
    normalize_access,
)
from knowledge_service.ingestion.normalization import Quarantined, normalize
from knowledge_service.ingestion.reconciliation import (
    Change,
    narrow_access,
    reconcile_document,
    removes_readers,
)
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourcePort,
    SourceRecord,
    SourceRecordState,
    SynchronizationSuperseded,
)
from knowledge_service.persistence.access import load_readers
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.sources import (
    claim_synchronization_fence,
    synchronization_fence_holds,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
# How far ahead of this machine's clock an observation may be. A fence set
# far in the future would refuse every real pass until that time came.
MAX_CLOCK_SKEW = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class UnresolvedAccess:
    """A published Document whose Source named readers the directory lacks.

    The Document is readable only by the readers that did resolve.
    """

    external_id: str
    principals: tuple[UnresolvedPrincipal, ...]


@dataclass(frozen=True, slots=True)
class SynchronizationSummary:
    """What one complete pass did."""

    run_id: SynchronizationRunId
    changes: Counter[Change]
    quarantined: tuple[Quarantined, ...]
    tombstones: tuple[Tombstone, ...]
    requeued_purges: tuple[JobId, ...]
    unresolved: tuple[UnresolvedAccess, ...]


async def synchronize_source(
    runtime: DatabaseRuntime,
    source: SourcePort,
    *,
    source_id: SourceId,
    observed_at: datetime,
    principal_directory: PrincipalDirectory | None,
    page_size: int = PAGE_SIZE,
    run_id: SynchronizationRunId | None = None,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
) -> SynchronizationSummary:
    """Publish every record the Source offers, then tombstone the rest.

    Readers come from `principal_directory`, which every caller names: with
    None, only public items are readable. A Source that returns a checkpoint
    it returned before raises `InvalidSource` instead of paging forever.
    The pass runs as `run_id`, or as a new run, and records how it ended.
    Raises `SynchronizationSuperseded`, before reading and recording
    anything, when a pass with the same or a later `observed_at` started.
    """
    run = SynchronizationRunId.new() if run_id is None else run_id
    await _claim_fence(runtime, source_id=source_id, observed_at=observed_at)

    async def guard(connection: AsyncConnection) -> None:
        if not await synchronization_fence_holds(
            connection, source_id=source_id, observed_at=observed_at
        ):
            raise SynchronizationSuperseded("a newer synchronization pass started")

    try:
        summary = await _publish(
            runtime,
            source,
            source_id=source_id,
            observed_at=observed_at,
            principal_directory=principal_directory,
            page_size=page_size,
            run_id=run,
            heartbeat=heartbeat,
            guard=guard,
        )
    except Exception as error:
        # The pass's own error is raised either way; its record holds only
        # its type. A database that cannot take the record is logged.
        try:
            await _record_finished(
                runtime,
                source_id=source_id,
                run_id=run,
                observed_at=observed_at,
                outcome=AuditOutcome.FAILED,
                metadata={"failure": type(error).__name__},
            )
        except Exception as unrecorded:
            # Identifiers and type names only, as the other warnings here.
            logger.warning(
                "failed synchronization run %s of source %s not audited: %s",
                str(run.value),
                str(source_id.value),
                type(unrecorded).__name__,
            )
        raise
    await _record_finished(
        runtime,
        source_id=source_id,
        run_id=run,
        observed_at=observed_at,
        outcome=AuditOutcome.SUCCEEDED,
        metadata={
            "changed": sum(
                count
                for change, count in summary.changes.items()
                if change is not Change.UNCHANGED
            ),
            "unchanged": summary.changes[Change.UNCHANGED],
            "quarantined": len(summary.quarantined),
            "tombstoned": len(summary.tombstones),
            "unresolved": len(summary.unresolved),
        },
    )
    return summary


async def _claim_fence(
    runtime: DatabaseRuntime, *, source_id: SourceId, observed_at: datetime
) -> None:
    if observed_at.utcoffset() != timedelta(0):
        raise InvalidSource("observed_at must be a UTC-aware timestamp")
    if observed_at > datetime.now(UTC) + MAX_CLOCK_SKEW:
        raise InvalidSource("observed_at is too far in the future to fence on")
    async with runtime.transaction() as connection:
        if not await claim_synchronization_fence(
            connection, source_id=source_id, observed_at=observed_at
        ):
            raise SynchronizationSuperseded("a newer synchronization pass started")


async def _record_finished(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    run_id: SynchronizationRunId,
    observed_at: datetime,
    outcome: AuditOutcome,
    metadata: dict[str, AuditValue],
) -> None:
    async with runtime.transaction() as connection:
        await record_audit_event(
            connection,
            AuditEvent(
                event_id=AuditEventId.new(),
                # When the pass ended, not the time it observed the Source.
                occurred_at=max(datetime.now(UTC), observed_at),
                actor_id=None,
                action=AuditAction.SYNCHRONIZATION_FINISHED,
                target_kind=AuditTargetKind.SOURCE,
                target_id=source_id.value,
                decision=AuditDecision.ALLOWED,
                outcome=outcome,
                request_id=None,
                run_id=run_id.value,
                metadata=metadata,
            ),
        )


async def _publish(
    runtime: DatabaseRuntime,
    source: SourcePort,
    *,
    source_id: SourceId,
    observed_at: datetime,
    principal_directory: PrincipalDirectory | None,
    page_size: int,
    run_id: SynchronizationRunId,
    heartbeat: Callable[[], Awaitable[None]] | None,
    guard: WriteGuard | None,
) -> SynchronizationSummary:
    """One pass: every page, then the tombstones and requeued purges."""
    seen: set[str] = set()
    used: set[str] = set()
    changes: Counter[Change] = Counter()
    quarantined: list[Quarantined] = []
    # By external ID, so a record offered twice is reported as last seen.
    unresolved: dict[str, UnresolvedAccess] = {}
    checkpoint: str | None = None
    while True:
        page = await source.fetch_page(checkpoint=checkpoint, page_size=page_size)
        # By external ID, so a record offered twice in a page counts once.
        planned: dict[str, tuple[SourceRecord, NormalizedAccess]] = {}
        for record in page.records:
            # A record the Source reports deleted is not seen, even if an
            # earlier page offered it: the last report wins.
            unresolved.pop(record.external_id, None)
            planned.pop(record.external_id, None)
            if record.state is SourceRecordState.DELETED or record.access is None:
                seen.discard(record.external_id)
                continue
            seen.add(record.external_id)
            access = normalize_access(record.access, principal_directory)
            if access.unresolved:
                unresolved[record.external_id] = UnresolvedAccess(
                    record.external_id, access.unresolved
                )
            planned[record.external_id] = (record, access)
        # Security first: every reader this page removes loses the Document
        # before any record of the page is parsed or chunked.
        narrowed = await _revoke_first(
            runtime,
            list(planned.values()),
            source_id=source_id,
            observed_at=observed_at,
            run_id=run_id,
            guard=guard,
        )
        for record, access in planned.values():
            outcome = normalize(record, source_id=source_id)
            if isinstance(outcome, Quarantined):
                # Kept as seen: a file that fails to parse is not a deleted
                # one; its narrowing above already applied.
                quarantined.append(outcome)
                if record.external_id in narrowed:
                    changes[Change.ACCESS] += 1
                continue
            result = await reconcile_document(
                runtime,
                outcome,
                readers=access.readers,
                observed_at=observed_at,
                run_id=run_id,
                guard=guard,
            )
            changes[
                _counted(result.change, narrowed=record.external_id in narrowed)
            ] += 1
        if heartbeat is not None:
            # After the last page this runs just before the tombstones.
            await heartbeat()
        if page.next_checkpoint is None:
            break
        if page.next_checkpoint in used:
            raise InvalidSource("the source repeated a checkpoint")
        used.add(page.next_checkpoint)
        checkpoint = page.next_checkpoint
    tombstones = await tombstone_missing(
        runtime, source_id=source_id, seen=seen, observed_at=observed_at, guard=guard
    )
    requeued = await requeue_unfinished_purges(
        runtime, source_id=source_id, now=observed_at
    )
    if unresolved:
        # Counts only: a principal may name a person.
        logger.warning(
            "unresolved source principals: %d in %d documents",
            sum(len(item.principals) for item in unresolved.values()),
            len(unresolved),
        )
    return SynchronizationSummary(
        run_id=run_id,
        changes=changes,
        quarantined=tuple(quarantined),
        tombstones=tombstones,
        requeued_purges=requeued,
        unresolved=tuple(unresolved.values()),
    )


async def _revoke_first(
    runtime: DatabaseRuntime,
    planned: list[tuple[SourceRecord, NormalizedAccess]],
    *,
    source_id: SourceId,
    observed_at: datetime,
    run_id: SynchronizationRunId,
    guard: WriteGuard | None,
) -> set[str]:
    """Narrow every record whose new access leaves a reader out.

    One read finds the readers each Document has now, so a record that
    removes nobody costs no locking transaction. Each narrowing is its own
    short transaction, committed before the next, so a revocation is
    visible to readers as soon as it is applied. Returns the narrowed IDs.
    """
    async with runtime.transaction() as connection:
        stored = await load_readers(
            connection,
            source_id=source_id,
            external_ids=[record.external_id for record, _ in planned],
        )
    narrowed: set[str] = set()
    for record, access in planned:
        current = stored.get(record.external_id)
        if current is None or not removes_readers(current, access.readers):
            continue
        if await narrow_access(
            runtime,
            source_id=source_id,
            external_id=record.external_id,
            readers=access.readers,
            observed_at=observed_at,
            run_id=run_id,
            guard=guard,
        ):
            narrowed.add(record.external_id)
    return narrowed


def _counted(change: Change, *, narrowed: bool) -> Change:
    """The change to report for a record whose access was narrowed first.

    The narrowing already wrote the access change, so reconciliation may see
    none; the record still changed who may read it.
    """
    if not narrowed or change in (Change.ACCESS, Change.CONTENT_AND_ACCESS):
        return change
    if change is Change.CONTENT:
        return Change.CONTENT_AND_ACCESS
    return Change.ACCESS
