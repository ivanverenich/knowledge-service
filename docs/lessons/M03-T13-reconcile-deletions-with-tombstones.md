# M03-T13 — Reconcile deletions with tombstones

Source task: [M03-T13](../curriculum/milestones/M03-local-ingestion/M03-T13-reconcile-deletions-with-tombstones.md)

## What you will do

This lesson includes coding: Steps 2–7 write code, while Steps 1 and 8 do not.

M03-T12 publishes documents a Source offers. This lesson handles the other direction: a file that disappears from the Source must disappear from answers at once, while the delete itself happens later and can be retried.

| Requirement | How this lesson meets it |
|---|---|
| Missing items become unavailable immediately | `tombstone_missing` marks every available Document a complete pass did not see as tombstoned, in one transaction. Retrieval reads only Chunks of available Documents, so they vanish from reads at that commit. |
| Retain audit identity | The Document row, its ID, and its Access Grants stay. An audit event records each change of availability, both the tombstone and a later restore. |
| Schedule retryable physical cleanup | The same transaction queues a `purge_document_chunks` Job. Running it deletes the Chunks; a failure is stored as a failed Job that can be queued again, and a purge that used every attempt is queued afresh on the next pass. |
| Integration test: deleted content disappears from reads before chunk cleanup | `test_deleted_content_disappears_from_reads_before_chunk_cleanup` checks reads after the tombstone, while the Chunks are still stored, and again after the purge. |

You also get the small sync loop that was missing: `synchronize_source` pages through a Source such as a local folder, normalizes and reconciles each file, and tombstones what is gone. After this lesson you can point it at a folder and watch documents appear and disappear.

Terms used in this lesson:

- **Tombstone:** a durable statement that a formerly available Document must no longer be retrieved (from `CONTEXT.md`). The row stays; only its `availability` changes.
- **Physical cleanup (purge):** actually deleting the stored Chunks. It is separate from the tombstone, so a failed delete never makes deleted text readable again.
- **Complete pass:** one run that has read every page of a Source. Only after a complete pass do you know which items are missing.
- **Row lock (`SELECT ... FOR UPDATE`):** a read that locks the row until the transaction ends; you used it in M03-T12.

## Before you begin

- **Why two steps instead of one delete.** Deleting the Chunks in the same transaction as the tombstone looks simpler, but a deletion of many large documents would hold locks for a long time, and later, when Chunks have embeddings in a vector index, deletion gets slower still. Tombstoning is one small row update; the heavy delete runs as a Job that can fail and retry without ever making the text readable again.
- **What "reads" means today.** No retrieval exists yet (it arrives in M04). This lesson adds `load_searchable_chunks`, the one query retrieval must use: it joins Chunks to their Document and keeps only available ones. The acceptance test reads through it.
- **What an adversarial review found.** An independent review attacked an earlier version of this code with real concurrent runs against PostgreSQL. Reads, locking, and cancellation held. It found three real problems, all fixed in the code below with a test that fails without the fix: the sync loop paged forever when a Source repeated a checkpoint; a purge that used all three attempts was never queued again, so its Chunks stayed stored forever; and an unsorted import failed Ruff. Four smaller design points were fixed too: only the missing rows are locked, a document updated after the pass began is kept, an item offered and then reported deleted in the same pass is tombstoned, and a restore records an audit event.
- **Running the integration tests.** They need the disposable PostgreSQL server and skip without one. Set the URL in your shell first, with your test user's real password:

  ```bash
  export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="postgresql://test_user:<password>@localhost:5433/knowledge_service_test"
  ```

  Never write the real password into a file in the repository.
- **Files you will touch:**
  - new migration `migrations/versions/c1d2e3f4a5b6_purge_document_chunks_job_kind.py`
  - `src/knowledge_service/jobs.py`, `src/knowledge_service/persistence.py`, `src/knowledge_service/reconciliation.py`
  - new `src/knowledge_service/deletions.py` and `src/knowledge_service/synchronization.py`
  - new `tests/test_deletions.py`, `tests/test_deletions_integration.py`, and `tests/test_synchronization_integration.py`
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your prediction

**Invariant:** once `tombstone_missing` commits, no read through `load_searchable_chunks` returns a Chunk of a Document the complete pass did not see, even though its Chunks are still stored. The Document keeps its row, ID, and grants, and an audit event plus a queued purge Job exist for it. The purge deletes Chunks only of a Document that is still tombstoned. A failed purge deletes nothing, and the Chunks are eventually deleted by a retry or a new Job. A pass that fails part-way tombstones nothing.

**Action:** create `docs/evidence/M03-T13-tombstones.md`:

```markdown
# M03-T13 — Tombstone evidence

Completed: pending

## Invariant

Pending.

## Prediction

Before writing code, say what is stored and what reads return for: a file
deleted from the folder, before and after the purge; a document the pass saw;
a document of another Source; tombstoning twice; a document updated after the
pass began; a purge whose delete fails, then a retry; a purge that fails three
times; a purge of a document that came back before the purge ran; a purge Job
run twice; a pass whose second page fails; a file that stops parsing; and a
Source that keeps returning the same checkpoint.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and your predictions.

### Step 2 — Coding step: a new Job kind and its migration

The `jobs` table only accepts the kinds named in its check constraint, `ck_jobs_kind`. A purge Job needs a new kind, so three places change together.

| Where | Change |
|---|---|
| `src/knowledge_service/jobs.py` | Add `PURGE_DOCUMENT_CHUNKS` to `JobKind`. |
| `src/knowledge_service/persistence.py` | Add the kind to the `ck_jobs_kind` constraint in the `jobs` table, so the code's schema matches the database. |
| New migration | Replace the constraint in the database. The downgrade first deletes purge Jobs, because the old constraint would refuse them. |

**Action:** in `src/knowledge_service/jobs.py`, extend `JobKind`:

```python
    SYNCHRONIZE_SOURCE = "synchronize_source"
    PURGE_CONVERSATION = "purge_conversation"
    PURGE_DOCUMENT_CHUNKS = "purge_document_chunks"
```

In `src/knowledge_service/persistence.py`, in the `jobs` table, replace the `ck_jobs_kind` constraint with:

```python
    sa.CheckConstraint(
        "kind in ('synchronize_source', 'purge_conversation', 'purge_document_chunks')",
        name="ck_jobs_kind",
    ),
```

Create `migrations/versions/c1d2e3f4a5b6_purge_document_chunks_job_kind.py`. `down_revision` is the current head, `09aa0992e559`; confirm it with `uv run alembic heads` before you start.

```python
"""purge document chunks job kind

Revision ID: c1d2e3f4a5b6
Revises: 09aa0992e559
Create Date: 2026-10-22 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: str | Sequence[str] | None = "09aa0992e559"


def upgrade() -> None:
    """Allow a Job that deletes a tombstoned Document's Chunks."""
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_kind",
        "jobs",
        "kind in ('synchronize_source', 'purge_conversation', 'purge_document_chunks')",
    )


def downgrade() -> None:
    """Remove purge Jobs, then allow only the original kinds again."""
    op.execute("DELETE FROM jobs WHERE kind = 'purge_document_chunks'")
    op.drop_constraint("ck_jobs_kind", "jobs", type_="check")
    op.create_check_constraint(
        "ck_jobs_kind", "jobs", "kind in ('synchronize_source', 'purge_conversation')"
    )
```

**Check:** `uv run pytest tests/test_migrations.py -q` passes (one head only), and with the database URL set, `uv run pytest tests/test_migrations_integration.py -q` passes.

### Step 3 — Coding step: persistence functions

Each function does one query and never commits; the workflows in Steps 4 and 6 own the transactions, as ADR 0012 requires.

| Function | What it does |
|---|---|
| `load_document_by_id` | Reads a Document by ID, optionally locking it. The purge Job knows only the Document ID. |
| `load_available_documents` | Reads every available Document of a Source, without locking. |
| `load_unpurged_documents` | Reads tombstoned Documents that still hold Chunks and have no purge Job that can still clean them. A Job counts while it is queued, running, or failed with attempts left. |
| `searchable_chunks_statement`, `load_searchable_chunks` | The read retrieval must use: Chunks joined to their Document, only where the Document is available. The statement takes a raw `UUID`, like the other `*_statement` helpers in this file. |
| `delete_chunks` | Deletes every Chunk of a Document and returns how many. |
| `load_job(..., for_update=...)` | Locks the Job row, so two workers cannot run the same Job. `for_update` defaults to `False`, so existing callers are unchanged. |

**Action:** in `src/knowledge_service/persistence.py`, add these three functions right after `load_document`:

```python
async def load_document_by_id(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
    for_update: bool = False,
) -> Document | None:
    """Read a stored Document by its ID, optionally locking its row."""
    statement = sa.select(documents).where(documents.c.document_id == document_id.value)
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return as_document(dict(record))


async def load_available_documents(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
) -> tuple[Document, ...]:
    """Read every available Document of one Source."""
    statement = (
        sa.select(documents)
        .where(
            documents.c.source_id == source_id.value,
            documents.c.availability == DocumentAvailability.AVAILABLE.value,
        )
        .order_by(documents.c.external_id)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(as_document(dict(record)) for record in records)


async def load_unpurged_documents(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
) -> tuple[Document, ...]:
    """Read tombstoned Documents that still hold Chunks and no purge can delete.

    A purge Job counts while it is queued, running, or failed with attempts
    left; a Document whose last purge used every attempt is returned.
    """
    open_purge = sa.exists().where(
        jobs.c.kind == JobKind.PURGE_DOCUMENT_CHUNKS.value,
        jobs.c.target_id == documents.c.document_id,
        sa.or_(
            jobs.c.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            sa.and_(
                jobs.c.status == JobStatus.FAILED.value,
                jobs.c.attempt < jobs.c.max_attempts,
            ),
        ),
    )
    statement = (
        sa.select(documents)
        .where(
            documents.c.source_id == source_id.value,
            documents.c.availability == DocumentAvailability.TOMBSTONED.value,
            sa.exists().where(chunks.c.document_id == documents.c.document_id),
            ~open_purge,
        )
        .order_by(documents.c.external_id)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(as_document(dict(record)) for record in records)
```

Add these right after `load_chunks`:

```python
def searchable_chunks_statement(source_id: UUID) -> sa.Select[Any]:
    """Select the Chunks retrieval may read: only those of available Documents."""
    return (
        sa.select(chunks)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(
            documents.c.source_id == source_id,
            documents.c.availability == DocumentAvailability.AVAILABLE.value,
        )
        .order_by(documents.c.external_id, chunks.c.ordinal)
    )


async def load_searchable_chunks(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
) -> tuple[Chunk, ...]:
    """Read the Chunks of one Source that retrieval may return."""
    records = (
        (await connection.execute(searchable_chunks_statement(source_id.value)))
        .mappings()
        .all()
    )
    return tuple(_chunk_from(dict(record)) for record in records)


async def delete_chunks(connection: AsyncConnection, *, document_id: DocumentId) -> int:
    """Delete every stored Chunk of a Document; return how many were deleted."""
    result = await connection.execute(
        sa.delete(chunks).where(chunks.c.document_id == document_id.value)
    )
    return result.rowcount
```

Replace `load_job` with:

```python
async def load_job(
    connection: AsyncConnection, *, job_id: JobId, for_update: bool = False
) -> Job | None:
    """Read a stored Job, if it exists, optionally locking its row."""
    statement = sa.select(jobs).where(jobs.c.job_id == job_id.value)
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))
```

Everything these use (`UUID`, `Any`, `JobKind`, `JobStatus`, `DocumentAvailability`, `SourceId`, `DocumentId`, `_chunk_from`) is already imported or defined in the file.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 4 — Coding step: the deletion workflows

New module `src/knowledge_service/deletions.py` with three workflows.

| Function | What it must do |
|---|---|
| `tombstone_missing` | In one transaction, for each available Document whose external ID is not in `seen`: lock its row, skip it if it is no longer available or was updated after `observed_at`, otherwise tombstone it, record an audit event, and queue a purge Job. Returns one `Tombstone` per Document. |
| `requeue_unfinished_purges` | Queues a new purge Job for each Document `load_unpurged_documents` returns, so a purge that used every attempt does not leave Chunks stored forever. |
| `purge_document_chunks` | Claims a queued purge Job (lock, check kind and status, start), locks the Document, deletes its Chunks only if it is still tombstoned, and stores the Job as succeeded, all in one transaction. On any other error that transaction rolls back, and a second transaction stores the Job as failed with the error code `chunk_purge_failed`. |

Decisions:

- **Lock only the missing rows.** Reading without a lock, then locking each missing row and checking it again, means a pass where nothing is missing locks nothing, and a concurrent reconcile of the same document waits only when it matters.
- **A newer document is kept, not an error.** If a row was updated after `observed_at`, a later observation saw the item, so this pass's view of it is stale.
- **The error code is a fixed string.** A Job stores no raw error text (see `jobs.py`), so nothing private can leak into the queue.
- **`InvalidJob` passes through unchanged.** A missing Job, a Job of another kind, or one that is not queued is a caller error, not a purge failure, so it is not recorded as a failed attempt.
- **Another worker may have finished the Job** between the two transactions. Then the second one re-raises the original error instead of overwriting that outcome.
- **`availability_event` is public,** because `reconcile_document` uses it in Step 5 to record a restore.

**Action:** create `src/knowledge_service/deletions.py`:

```python
"""Tombstone Documents a Source no longer offers, and purge their Chunks later.

Deletion happens in two steps:

1. `tombstone_missing` runs at the end of a complete Synchronization Run. In
   one transaction it marks every available Document the run did not see as
   tombstoned, records an audit event, and queues a purge Job. Retrieval reads
   only Chunks of available Documents, so the content disappears from reads at
   that commit, while the Chunks are still stored.
2. `purge_document_chunks` runs that Job later and deletes the Chunks. If it
   fails, the Job is recorded as failed with a short error code and can be
   queued again; reads stay correct in the meantime. When a purge has used
   every attempt, `requeue_unfinished_purges` queues a new one.

The Document row, its Access Grants, and the audit trail are kept, so the
Document keeps its identity and comes back with the same ID if the Source
offers it again.
"""

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import AuditAction, AuditEvent, AuditTargetKind
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import AuditEventId, DocumentId, JobId, SourceId
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence import (
    delete_chunks,
    enqueue_job,
    load_available_documents,
    load_document_by_id,
    load_job,
    load_unpurged_documents,
    record_audit_event,
    save_job,
    upsert_document,
)

PURGE_FAILED = "chunk_purge_failed"


@dataclass(frozen=True, slots=True)
class Tombstone:
    """One Document made unavailable, and the Job that will purge its Chunks."""

    document_id: DocumentId
    external_id: str
    purge_job_id: JobId


async def tombstone_missing(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    seen: Collection[str],
    observed_at: datetime,
) -> tuple[Tombstone, ...]:
    """Tombstone every available Document of a Source whose ID is not in `seen`.

    `seen` must hold every external ID of one complete pass over the Source;
    after a partial pass, documents that were simply not reached yet would be
    tombstoned. A Document updated after `observed_at` was seen by a later
    observation, so it is kept.
    """
    seen = frozenset(seen)
    tombstones: list[Tombstone] = []
    async with runtime.transaction() as connection:
        available = await load_available_documents(connection, source_id=source_id)
        for candidate in available:
            if candidate.provenance.external_id in seen:
                continue
            # Lock only the missing rows, so a concurrent reconcile of the same
            # document waits, and decide from the row as it is now.
            document = await load_document_by_id(
                connection, document_id=candidate.document_id, for_update=True
            )
            if (
                document is None
                or document.availability is not DocumentAvailability.AVAILABLE
                or document.updated_at > observed_at
            ):
                continue
            tombstoned = document.tombstone(observed_at=observed_at)
            await upsert_document(connection, tombstoned)
            await record_audit_event(connection, availability_event(tombstoned))
            job = await _queue_purge(
                connection, document_id=tombstoned.document_id, now=observed_at
            )
            tombstones.append(
                Tombstone(
                    document_id=tombstoned.document_id,
                    external_id=tombstoned.provenance.external_id,
                    purge_job_id=job.job_id,
                )
            )
    return tuple(tombstones)


async def requeue_unfinished_purges(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    now: datetime,
) -> tuple[JobId, ...]:
    """Queue a new purge for each tombstoned Document no purge can still clean."""
    async with runtime.transaction() as connection:
        unpurged = await load_unpurged_documents(connection, source_id=source_id)
        queued = [
            await _queue_purge(connection, document_id=document.document_id, now=now)
            for document in unpurged
        ]
    return tuple(job.job_id for job in queued)


async def purge_document_chunks(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    now: datetime,
) -> Job:
    """Run one queued purge Job and return it as stored afterwards.

    The Chunks are deleted only if the Document is still tombstoned; a
    Document restored in the meantime keeps its new Chunks. If deleting fails,
    nothing is deleted, the Job is stored as failed with `PURGE_FAILED`, and
    `Job.retry` can queue it again.
    """
    try:
        async with runtime.transaction() as connection:
            job = await _claim(connection, job_id=job_id, now=now)
            # Lock the row, so a restore cannot interleave with the delete.
            document = await load_document_by_id(
                connection,
                document_id=DocumentId(job.target_id),
                for_update=True,
            )
            if (
                document is not None
                and document.availability is DocumentAvailability.TOMBSTONED
            ):
                await delete_chunks(connection, document_id=document.document_id)
            return await save_job(connection, job=job.succeed(finished_at=now))
    except InvalidJob:
        raise
    except Exception:
        # The failed transaction rolled back; record the failure separately,
        # unless another worker has finished the Job in the meantime.
        async with runtime.transaction() as connection:
            stored = await load_job(connection, job_id=job_id, for_update=True)
            if stored is None or stored.status is not JobStatus.QUEUED:
                raise
            failed = stored.start(started_at=now).fail(
                error_code=PURGE_FAILED, finished_at=now
            )
            return await save_job(connection, job=failed)


async def _queue_purge(
    connection: AsyncConnection, *, document_id: DocumentId, now: datetime
) -> Job:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_DOCUMENT_CHUNKS,
        target_id=document_id.value,
        queued_at=now,
    )
    await enqueue_job(connection, job)
    return job


async def _claim(connection: AsyncConnection, *, job_id: JobId, now: datetime) -> Job:
    job = await load_job(connection, job_id=job_id, for_update=True)
    if job is None:
        raise InvalidJob("the purge job does not exist")
    if job.kind is not JobKind.PURGE_DOCUMENT_CHUNKS:
        raise InvalidJob("the job is not a document chunk purge")
    if job.status is not JobStatus.QUEUED:
        raise InvalidJob(f"only a queued job can run, not a {job.status} one")
    return job.start(started_at=now)


def availability_event(document: Document) -> AuditEvent:
    """Record that a Document became available or tombstoned, without content."""
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=document.updated_at,
        actor_id=None,
        action=AuditAction.DOCUMENT_AVAILABILITY_CHANGED,
        target_kind=AuditTargetKind.DOCUMENT,
        target_id=document.document_id.value,
        metadata={"availability": document.availability.value},
    )
```

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 5 — Coding step: audit a restore

A tombstoned Document that comes back is restored by `reconcile_document` (M03-T12's `Change.RESTORED`). Without an event, the audit trail would say "tombstoned" forever.

**Action:** in `src/knowledge_service/reconciliation.py`, add the import `from knowledge_service.deletions import availability_event` after the `knowledge_service.database` import, add `record_audit_event,` to the `knowledge_service.persistence` import list (after `load_document,`), and record the event right after the row is written:

```python
        await upsert_document(connection, document)
        if restored:
            await record_audit_event(connection, availability_event(document))
        # A restored Document gets its Chunks back even if its text is unchanged.
```

`deletions.py` does not import `reconciliation.py`, so this creates no import cycle.

**Check:** `uv run pytest tests/test_reconciliation.py -q` passes, and with the database URL set, `uv run pytest tests/test_reconciliation_integration.py -q` still passes.

### Step 6 — Coding step: one complete pass over a Source

New module `src/knowledge_service/synchronization.py` with `synchronize_source`. It is deliberately small: the real scheduled, resumable sync is M08-T10.

| Rule | Why |
|---|---|
| Page with `fetch_page` until `next_checkpoint` is `None`. | Only then is the pass complete. |
| Tombstone only after the last page. | An exception on any page leaves the function before `tombstone_missing`, so a broken run never hides documents. |
| A quarantined record counts as seen. | A file that fails to parse is not a deleted file; its last good version stays. |
| A record reported `DELETED` is removed from `seen`. | The last report of an item in a pass wins. |
| A repeated checkpoint raises `InvalidSource`. | Otherwise a buggy Source pages forever. |
| Readers: public items get `Subject.public()`, others get nobody. | Mapping a Source's principals to users and groups is M07 identity work; deny by default until then. |
| Afterwards, call `requeue_unfinished_purges`. | Each pass gives an exhausted purge another chance. |

**Action:** create `src/knowledge_service/synchronization.py`:

```python
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
```

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 7 — Coding step: tests

Three files, 18 tests in total. The first file runs without a database; the other two need it.

| File | What it proves |
|---|---|
| `tests/test_deletions.py` (2) | The searchable-chunks query filters on availability; only public items get readers. |
| `tests/test_deletions_integration.py` (9) | The acceptance test; seen documents and other Sources are left alone; tombstoning twice queues nothing new; a newer document is kept; a failed purge keeps the Chunks and can be retried; a restored document keeps its Chunks and gets an audit event; a Job runs only once; only an existing purge Job can run; an exhausted purge is queued again. |
| `tests/test_synchronization_integration.py` (7) | A removed file is tombstoned; a failed pass tombstones nothing; a private document gets no grants; a file that stops parsing keeps its last version; an item reported deleted is tombstoned; the last report in a pass wins; a repeated checkpoint is refused. |

Failures are injected with `monkeypatch.setattr(deletions, "delete_chunks", ...)`. This works because `deletions.py` looks up `delete_chunks` in its own module namespace when it runs.

**Action:** create `tests/test_deletions.py`:

```python
"""Unit tests for what retrieval may read once Documents are tombstoned."""

from uuid import UUID

from sqlalchemy.dialects import postgresql

from knowledge_service.access import Subject
from knowledge_service.persistence import searchable_chunks_statement
from knowledge_service.sources import SourceAccess
from knowledge_service.synchronization import source_readers


def test_searchable_chunks_belong_only_to_available_documents() -> None:
    statement = searchable_chunks_statement(UUID(int=1))
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "documents.availability = 'available'" in sql


def test_only_public_items_get_readers_until_identity_mapping_exists() -> None:
    assert source_readers(SourceAccess(public=True)) == [Subject.public()]
    assert source_readers(SourceAccess(public=False, principals=("hr",))) == []
```

Create `tests/test_deletions_integration.py`:

```python
"""Real PostgreSQL tests for tombstoning missing Documents and purging Chunks."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service import deletions
from knowledge_service.access import Subject
from knowledge_service.audit import AuditAction
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.deletions import (
    PURGE_FAILED,
    purge_document_chunks,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.documents import (
    Document,
    DocumentAvailability,
)
from knowledge_service.identifiers import DocumentId, JobId, SourceId
from knowledge_service.jobs import (
    InvalidJob,
    Job,
    JobKind,
    JobNotRetryable,
    JobStatus,
)
from knowledge_service.normalization import NormalizedDocument, normalize
from knowledge_service.persistence import (
    enqueue_job,
    load_access_grants,
    load_audit_events,
    load_chunks,
    load_document,
    load_job,
    load_searchable_chunks,
    save_job,
)
from knowledge_service.reconciliation import Change, reconcile_document
from knowledge_service.settings import Settings
from knowledge_service.sources import SourceAccess, SourceRecord

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
OTHER_SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000011")
OBSERVED_AT = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)
LATEST = OBSERVED_AT + timedelta(hours=2)
HANDBOOK = "# Team handbook\n\nWelcome to the team.\n"
OLD_POLICY = "# Old policy\n\nThis policy was retired.\n"

INSERT_SOURCE = text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " :location, 'Handbook', true, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        for source_id in (SOURCE_ID, OTHER_SOURCE_ID):
            await connection.execute(
                INSERT_SOURCE,
                {"source_id": source_id.value, "location": f"/srv/{source_id}"},
            )
    try:
        yield runtime
    finally:
        await runtime.dispose()


def markdown(
    external_id: str, body: str, source_id: SourceId = SOURCE_ID
) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id=external_id,
            version="v1",
            media_type="text/markdown",
            content=body.encode(),
            access=SourceAccess(public=True),
        ),
        source_id=source_id,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def publish(
    runtime: DatabaseRuntime,
    external_id: str,
    body: str,
    *,
    source_id: SourceId = SOURCE_ID,
    observed_at: datetime = OBSERVED_AT,
) -> Document:
    result = await reconcile_document(
        runtime,
        markdown(external_id, body, source_id),
        readers=[Subject.public()],
        observed_at=observed_at,
    )
    return result.document


async def searchable_text(
    runtime: DatabaseRuntime, source_id: SourceId = SOURCE_ID
) -> list[str]:
    async with runtime.transaction() as connection:
        chunks = await load_searchable_chunks(connection, source_id=source_id)
    return [chunk.text for chunk in chunks]


async def stored_chunk_count(runtime: DatabaseRuntime, document_id: DocumentId) -> int:
    async with runtime.transaction() as connection:
        return len(await load_chunks(connection, document_id=document_id))


async def stored(runtime: DatabaseRuntime, external_id: str) -> Document:
    async with runtime.transaction() as connection:
        document = await load_document(
            connection, source_id=SOURCE_ID, external_id=external_id
        )
    assert document is not None
    return document


@pytest.mark.integration
async def test_deleted_content_disappears_from_reads_before_chunk_cleanup(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "handbook.md", HANDBOOK)
    retired = await publish(runtime, "old-policy.md", OLD_POLICY)
    assert any("retired" in chunk for chunk in await searchable_text(runtime))

    tombstones = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen={"handbook.md"}, observed_at=LATER
    )

    # Unavailable to reads at once, although its Chunks are still stored.
    assert [t.document_id for t in tombstones] == [retired.document_id]
    assert not any("retired" in chunk for chunk in await searchable_text(runtime))
    assert any("Welcome" in chunk for chunk in await searchable_text(runtime))
    assert await stored_chunk_count(runtime, retired.document_id) > 0
    document = await stored(runtime, "old-policy.md")
    assert document.document_id == retired.document_id
    assert document.availability is DocumentAvailability.TOMBSTONED
    assert document.updated_at == LATER

    async with runtime.transaction() as connection:
        events = await load_audit_events(
            connection, target_id=retired.document_id.value, limit=10
        )
        job = await load_job(connection, job_id=tombstones[0].purge_job_id)
    assert [(e.action, dict(e.metadata)) for e in events] == [
        (AuditAction.DOCUMENT_AVAILABILITY_CHANGED, {"availability": "tombstoned"})
    ]
    assert job is not None
    assert (job.kind, job.status, job.target_id) == (
        JobKind.PURGE_DOCUMENT_CHUNKS,
        JobStatus.QUEUED,
        retired.document_id.value,
    )

    purged = await purge_document_chunks(
        runtime, job_id=tombstones[0].purge_job_id, now=LATEST
    )

    assert purged.status is JobStatus.SUCCEEDED
    assert await stored_chunk_count(runtime, retired.document_id) == 0
    async with runtime.transaction() as connection:
        grants = await load_access_grants(connection, document_id=retired.document_id)
    assert [grant.subject for grant in grants] == [Subject.public()]
    assert (await stored(runtime, "old-policy.md")).document_id == retired.document_id


@pytest.mark.integration
async def test_seen_documents_and_other_sources_are_left_alone(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "handbook.md", HANDBOOK)
    await publish(runtime, "elsewhere.md", OLD_POLICY, source_id=OTHER_SOURCE_ID)

    tombstones = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen={"handbook.md"}, observed_at=LATER
    )

    assert tombstones == ()
    assert (await stored(runtime, "handbook.md")).availability is (
        DocumentAvailability.AVAILABLE
    )
    assert await searchable_text(runtime, OTHER_SOURCE_ID) != []


@pytest.mark.integration
async def test_tombstoning_again_queues_nothing_new(runtime: DatabaseRuntime) -> None:
    await publish(runtime, "old-policy.md", OLD_POLICY)
    first = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )

    second = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATEST
    )

    assert len(first) == 1
    assert second == ()
    async with runtime.transaction() as connection:
        jobs = (await connection.execute(text("SELECT count(*) FROM jobs"))).scalar()
    assert jobs == 1


@pytest.mark.integration
async def test_a_document_updated_after_the_pass_began_is_kept(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "old-policy.md", OLD_POLICY, observed_at=LATER)

    tombstones = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=OBSERVED_AT
    )

    assert tombstones == ()
    assert (await stored(runtime, "old-policy.md")).availability is (
        DocumentAvailability.AVAILABLE
    )
    async with runtime.transaction() as connection:
        jobs = (await connection.execute(text("SELECT count(*) FROM jobs"))).scalar()
        events = (
            await connection.execute(text("SELECT count(*) FROM audit_events"))
        ).scalar()
    assert (jobs, events) == (0, 0)


@pytest.mark.integration
async def test_a_failed_purge_keeps_chunks_and_can_be_retried(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    retired = await publish(runtime, "old-policy.md", OLD_POLICY)
    (tombstone,) = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )

    async def broken_delete(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        raise ConnectionError("the database went away")

    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(
            runtime, job_id=tombstone.purge_job_id, now=LATER
        )

    assert (failed.status, failed.attempt, failed.last_error_code) == (
        JobStatus.FAILED,
        1,
        PURGE_FAILED,
    )
    assert await stored_chunk_count(runtime, retired.document_id) > 0
    assert await searchable_text(runtime) == []

    async with runtime.transaction() as connection:
        stored_job = await load_job(connection, job_id=tombstone.purge_job_id)
    assert stored_job == failed
    async with runtime.transaction() as connection:
        await save_job(connection, job=failed.retry(queued_at=LATEST))
    succeeded = await purge_document_chunks(
        runtime, job_id=tombstone.purge_job_id, now=LATEST
    )

    assert (succeeded.status, succeeded.attempt) == (JobStatus.SUCCEEDED, 1)
    assert await stored_chunk_count(runtime, retired.document_id) == 0


@pytest.mark.integration
async def test_a_restored_document_keeps_its_chunks_when_an_old_purge_runs(
    runtime: DatabaseRuntime,
) -> None:
    retired = await publish(runtime, "old-policy.md", OLD_POLICY)
    (tombstone,) = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )
    restored = await reconcile_document(
        runtime,
        markdown("old-policy.md", OLD_POLICY),
        readers=[Subject.public()],
        observed_at=LATEST,
    )

    job = await purge_document_chunks(
        runtime, job_id=tombstone.purge_job_id, now=LATEST
    )

    assert restored.change is Change.RESTORED
    assert restored.document.document_id == retired.document_id
    async with runtime.transaction() as connection:
        events = await load_audit_events(
            connection, target_id=retired.document_id.value, limit=10
        )
    assert [e.metadata["availability"] for e in events] == ["tombstoned", "available"]
    assert job.status is JobStatus.SUCCEEDED
    assert any("retired" in chunk for chunk in await searchable_text(runtime))


@pytest.mark.integration
async def test_a_purge_job_runs_only_once(runtime: DatabaseRuntime) -> None:
    await publish(runtime, "old-policy.md", OLD_POLICY)
    (tombstone,) = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )
    await purge_document_chunks(runtime, job_id=tombstone.purge_job_id, now=LATER)

    with pytest.raises(InvalidJob, match="only a queued job can run"):
        await purge_document_chunks(runtime, job_id=tombstone.purge_job_id, now=LATEST)


@pytest.mark.integration
async def test_only_an_existing_purge_job_can_run(runtime: DatabaseRuntime) -> None:
    other = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=SOURCE_ID.value,
        queued_at=OBSERVED_AT,
    )
    async with runtime.transaction() as connection:
        await enqueue_job(connection, other)

    with pytest.raises(InvalidJob, match="does not exist"):
        await purge_document_chunks(runtime, job_id=JobId.new(), now=LATER)
    with pytest.raises(InvalidJob, match="not a document chunk purge"):
        await purge_document_chunks(runtime, job_id=other.job_id, now=LATER)


@pytest.mark.integration
async def test_a_purge_that_used_every_attempt_is_queued_again(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    retired = await publish(runtime, "old-policy.md", OLD_POLICY)
    (tombstone,) = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )

    async def broken_delete(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        raise ConnectionError("the database went away")

    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(
            runtime, job_id=tombstone.purge_job_id, now=LATER
        )
        # Nothing is queued again while the job still has attempts left.
        assert (
            await requeue_unfinished_purges(runtime, source_id=SOURCE_ID, now=LATER)
            == ()
        )
        for _ in range(failed.max_attempts - 1):
            async with runtime.transaction() as connection:
                await save_job(connection, job=failed.retry(queued_at=LATER))
            failed = await purge_document_chunks(
                runtime, job_id=tombstone.purge_job_id, now=LATER
            )
    with pytest.raises(JobNotRetryable):
        failed.retry(queued_at=LATEST)

    (requeued,) = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=LATEST
    )
    await purge_document_chunks(runtime, job_id=requeued, now=LATEST)

    assert await stored_chunk_count(runtime, retired.document_id) == 0
    assert (
        await requeue_unfinished_purges(runtime, source_id=SOURCE_ID, now=LATEST) == ()
    )
```

Create `tests/test_synchronization_integration.py`:

```python
"""Real PostgreSQL tests for one complete pass over a local Source."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.directory_source import DirectorySource
from knowledge_service.identifiers import SourceId
from knowledge_service.normalization import QuarantineReason
from knowledge_service.persistence import load_searchable_chunks
from knowledge_service.reconciliation import Change
from knowledge_service.settings import Settings
from knowledge_service.sources import (
    InvalidSource,
    SourceAccess,
    SourcePage,
    SourceRecord,
    SourceUnavailable,
)
from knowledge_service.synchronization import synchronize_source

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
OBSERVED_AT = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)

INSERT_SOURCE = text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def searchable_text(runtime: DatabaseRuntime) -> str:
    async with runtime.transaction() as connection:
        chunks = await load_searchable_chunks(connection, source_id=SOURCE_ID)
    return "\n".join(chunk.text for chunk in chunks)


@pytest.mark.integration
async def test_a_file_removed_from_the_folder_is_tombstoned(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    (tmp_path / "old-policy.md").write_text("# Old policy\n\nRetired rules.\n")
    (tmp_path / "empty.md").write_text("")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))

    first = await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT, page_size=1
    )
    (tmp_path / "old-policy.md").unlink()
    second = await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=LATER, page_size=1
    )

    assert first.changes == {Change.CREATED: 2}
    assert [q.reason for q in first.quarantined] == [QuarantineReason.EMPTY_CONTENT]
    assert first.tombstones == ()
    assert second.changes == {Change.UNCHANGED: 1}
    assert [t.external_id for t in second.tombstones] == ["old-policy.md"]
    searchable = await searchable_text(runtime)
    assert "Welcome aboard." in searchable
    assert "Retired rules." not in searchable


class FailingSecondPage:
    """A Source whose second page cannot be fetched."""

    def __init__(self, first: DirectorySource) -> None:
        self._first = first

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        if checkpoint is not None:
            raise SourceUnavailable("the source went away")
        return await self._first.fetch_page(checkpoint=None, page_size=page_size)


@pytest.mark.integration
async def test_a_failed_pass_tombstones_nothing(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "a.md").write_text("# A\n\nFirst file.\n")
    (tmp_path / "b.md").write_text("# B\n\nSecond file.\n")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )

    with pytest.raises(SourceUnavailable):
        await synchronize_source(
            runtime,
            FailingSecondPage(source),
            source_id=SOURCE_ID,
            observed_at=LATER,
            page_size=1,
        )

    searchable = await searchable_text(runtime)
    assert "First file." in searchable
    assert "Second file." in searchable


@pytest.mark.integration
async def test_a_private_document_is_stored_but_readable_by_nobody(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "salaries.md").write_text("# Salaries\n\nConfidential.\n")
    source = DirectorySource(
        tmp_path, access=SourceAccess(public=False, principals=("hr",))
    )

    summary = await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )

    assert summary.changes == {Change.CREATED: 1}
    async with runtime.transaction() as connection:
        grants = (
            await connection.execute(text("SELECT count(*) FROM access_grants"))
        ).scalar()
    assert grants == 0


@pytest.mark.integration
async def test_a_file_that_stops_parsing_keeps_its_last_good_version(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )
    (tmp_path / "handbook.md").write_bytes(b"\xff\xfe broken")

    summary = await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=LATER
    )

    assert [q.reason for q in summary.quarantined] == [
        QuarantineReason.UNDECODABLE_TEXT
    ]
    assert summary.tombstones == ()
    assert "Welcome aboard." in await searchable_text(runtime)


class ReportsDeletion:
    """A Source that reports one item deleted instead of leaving it out."""

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        return SourcePage(
            records=(SourceRecord.deleted(external_id="handbook.md", version="v2"),),
            next_checkpoint=None,
        )


@pytest.mark.integration
async def test_an_item_reported_deleted_is_tombstoned(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    source = DirectorySource(tmp_path, access=SourceAccess(public=True))
    await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )

    summary = await synchronize_source(
        runtime, ReportsDeletion(), source_id=SOURCE_ID, observed_at=LATER
    )

    assert [t.external_id for t in summary.tombstones] == ["handbook.md"]
    assert await searchable_text(runtime) == ""


class OffersThenDeletes:
    """A Source that offers an item on page one and reports it deleted on page two."""

    def __init__(self, first: DirectorySource) -> None:
        self._first = first

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        if checkpoint is None:
            page = await self._first.fetch_page(checkpoint=None, page_size=page_size)
            return SourcePage(records=page.records, next_checkpoint="page-2")
        return SourcePage(
            records=(SourceRecord.deleted(external_id="handbook.md", version="v2"),),
            next_checkpoint=None,
        )


@pytest.mark.integration
async def test_the_last_report_of_an_item_in_a_pass_wins(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "handbook.md").write_text("# Handbook\n\nWelcome aboard.\n")
    source = OffersThenDeletes(
        DirectorySource(tmp_path, access=SourceAccess(public=True))
    )

    summary = await synchronize_source(
        runtime, source, source_id=SOURCE_ID, observed_at=OBSERVED_AT
    )

    assert [t.external_id for t in summary.tombstones] == ["handbook.md"]
    assert await searchable_text(runtime) == ""


class StuckCheckpoint:
    """A Source that always says there is one more page after "0"."""

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        return SourcePage(records=(), next_checkpoint="0")


@pytest.mark.integration
async def test_a_repeated_checkpoint_is_refused(runtime: DatabaseRuntime) -> None:
    with pytest.raises(InvalidSource, match="repeated a checkpoint"):
        await synchronize_source(
            runtime, StuckCheckpoint(), source_id=SOURCE_ID, observed_at=OBSERVED_AT
        )
```

**Check:** with the database URL set, `uv run pytest tests/test_deletions.py tests/test_deletions_integration.py tests/test_synchronization_integration.py -q` gives 18 passed.

Then break the code on purpose, one change at a time, run the tests, and undo the change:

| Break | Test that must fail |
|---|---|
| Remove the availability filter from `searchable_chunks_statement` | the acceptance test and `test_searchable_chunks_belong_only_to_available_documents` |
| Delete Chunks regardless of availability in the purge | `test_a_restored_document_keeps_its_chunks_when_an_old_purge_runs` |
| Call `tombstone_missing` after every page | `test_a_failed_pass_tombstones_nothing` |
| Stop counting quarantined records as seen | `test_a_file_that_stops_parsing_keeps_its_last_good_version` |

### Step 8 — No coding in this step: run the gates, then record evidence

**Action:** with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set, run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_deletions.py tests/test_deletions_integration.py tests/test_synchronization_integration.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make test-integration
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. In Design, also record these known limits:

- **An empty listing tombstones everything.** If the folder exists but is empty, for example an unmounted network share, the pass is complete with nothing seen, and every document of the Source is tombstoned. They come back with the same IDs on the next good pass, but a guard (refuse when nothing was seen but documents exist) is worth adding before a real remote Source.
- **No single-run guard.** `synchronize_source` does not create a Synchronization Run, so two overlapping passes over different views of a folder could flip documents between tombstoned and available. M08 adds runs and their "one running run per source" rule.
- **Duplicate purge Jobs are possible** if two passes call `requeue_unfinished_purges` at the same moment. That is harmless: the second purge deletes nothing.
- **External IDs that differ only in Unicode form** (for example a name typed as `é` or as `e` plus an accent) are two different documents. macOS file names can do this.
- **The cause of a purge failure is not logged;** only the fixed code `chunk_purge_failed` is stored.

Answer these reflection questions in your own words:

1. Why does deleting a file only mark the Document as tombstoned at first, instead of deleting its Chunks straight away? Think about what a reader could see if the delete failed half-way, and about how long the delete holds locks.
2. Why must `tombstone_missing` run only after the last page of a Source has been read? Describe what would happen to documents on page 2 if it ran after page 1.
3. Why is a file that fails to parse counted as seen, so its old version stays searchable? What could go wrong if a parser bug instead made every PDF look deleted?
4. A purge Job is queued, but before it runs, the file comes back and the Document is restored. Walk through what `purge_document_chunks` does, and why the row lock matters here.
5. What does `synchronize_source` hide from its caller? List the things the caller never has to do.
6. Which alternative design would make later work harder? For example, what changes when M04 adds embeddings if the tombstone deleted Chunks in the same transaction?
7. Which test would let a real bug reach production if you deleted it? Name it and describe the bug.

**Check:** every command passes, and the evidence explains the design in your words.

## Completion checklist

- [ ] The migration adds `purge_document_chunks` to `ck_jobs_kind`, `JobKind` has it, and the ORM constraint matches.
- [ ] `persistence.py` has `load_document_by_id`, `load_available_documents`, `load_unpurged_documents`, `searchable_chunks_statement`, `load_searchable_chunks`, `delete_chunks`, and `load_job(..., for_update=...)`.
- [ ] `deletions.py` has `tombstone_missing`, `requeue_unfinished_purges`, `purge_document_chunks`, `availability_event`, `Tombstone`, and `PURGE_FAILED`.
- [ ] `reconcile_document` records an audit event when it restores a Document.
- [ ] `synchronization.py` has `synchronize_source`, `source_readers`, and `SynchronizationSummary`.
- [ ] The acceptance test shows deleted content gone from reads while its Chunks are still stored, and the Chunks gone after the purge, with the Document row and grants kept.
- [ ] All 18 new tests, the integration suite, the full deterministic suite, format, lint, types, `make check`, and `make docs-check` pass.
- [ ] The evidence file holds your invariant, predictions, design (including the known limits), verification, and your own reflection.

When you're done, share your code or any error you hit, and I'll review it.
