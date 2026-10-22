"""Run one complete pass over a Source and publish what it offers.

The pass pages through the Source, normalizes each present record, and
reconciles it. Only after the last page does it tombstone the available
Documents it did not see: a failure part-way through raises before anything
is tombstoned, so a broken run never makes documents disappear.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from knowledge_service.access import Subject
from knowledge_service.database import DatabaseRuntime
from knowledge_service.deletions import (
    Tombstone,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.normalization import Quarantined, normalize
from knowledge_service.reconciliation import Change, reconcile_document
from knowledge_service.sources import (
    InvalidSource,
    SourceAccess,
    SourcePort,
    SourceRecordState,
)

PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class SynchronizationSummary:
    """What one complete pass did."""

    changes: Counter[Change]
    quarantined: tuple[Quarantined, ...]
    tombstones: tuple[Tombstone, ...]
    requeued_purges: tuple[JobId, ...]


async def synchronize_source(
    runtime: DatabaseRuntime,
    source: SourcePort,
    *,
    source_id: SourceId,
    observed_at: datetime,
    page_size: int = PAGE_SIZE,
) -> SynchronizationSummary:
    """Publish every record the Source offers, then tombstone the rest.

    A Source that returns a checkpoint it returned before raises
    `InvalidSource` instead of paging forever.
    """
    seen: set[str] = set()
    used: set[str] = set()
    changes: Counter[Change] = Counter()
    quarantined: list[Quarantined] = []
    checkpoint: str | None = None
    while True:
        page = await source.fetch_page(checkpoint=checkpoint, page_size=page_size)
        for record in page.records:
            # A record the Source reports deleted is not seen, even if an
            # earlier page offered it: the last report wins.
            if record.state is SourceRecordState.DELETED:
                seen.discard(record.external_id)
                continue
            seen.add(record.external_id)
            outcome = normalize(record, source_id=source_id)
            if isinstance(outcome, Quarantined):
                # Kept as seen: a file that fails to parse is not a deleted one.
                quarantined.append(outcome)
                continue
            result = await reconcile_document(
                runtime,
                outcome,
                readers=source_readers(outcome.access),
                observed_at=observed_at,
            )
            changes[result.change] += 1
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
    return SynchronizationSummary(
        changes=changes,
        quarantined=tuple(quarantined),
        tombstones=tombstones,
        requeued_purges=requeued,
    )


def source_readers(access: SourceAccess) -> Sequence[Subject]:
    """Return who may read an item: everyone if public, otherwise nobody yet.

    Mapping a Source's principals to users and groups is identity work for
    M07; until then a non-public document is readable by nobody.
    """
    if access.public:
        return [Subject.public()]
    return []
