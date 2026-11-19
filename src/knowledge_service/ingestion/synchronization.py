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
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.ingestion.deletions import (
    Tombstone,
    requeue_unfinished_purges,
    tombstone_missing,
)
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
)
from knowledge_service.persistence.access import load_readers

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


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
) -> SynchronizationSummary:
    """Publish every record the Source offers, then tombstone the rest.

    Readers come from `principal_directory`, which every caller names: with
    None, only public items are readable. A Source that returns a checkpoint
    it returned before raises `InvalidSource` instead of paging forever.
    """
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
            )
            changes[
                _counted(result.change, narrowed=record.external_id in narrowed)
            ] += 1
        if page.next_checkpoint is None:
            break
        if page.next_checkpoint in used:
            raise InvalidSource("the source repeated a checkpoint")
        used.add(page.next_checkpoint)
        checkpoint = page.next_checkpoint
    tombstones = await tombstone_missing(
        runtime, source_id=source_id, seen=seen, observed_at=observed_at
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
