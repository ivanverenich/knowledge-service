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
                connection,
                document_id=candidate.document_id,
                for_update=True,
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
