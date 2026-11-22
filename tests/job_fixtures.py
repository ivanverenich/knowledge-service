"""Shared setup for the Job dispatch tests against real PostgreSQL."""

from datetime import UTC, datetime

from sqlalchemy import text

from knowledge_service.access import Subject
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document
from knowledge_service.identifiers import DocumentId, JobId, SourceId
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import SourceAccess, SourceRecord
from knowledge_service.jobs import Job
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.jobs import load_job

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000020")
OBSERVED_AT = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)

INSERT_SOURCE = text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/jobs', 'Jobs', true, now(), now())"
)


async def insert_source(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})


async def stored_job(runtime: DatabaseRuntime, job_id: JobId) -> Job:
    async with runtime.transaction() as connection:
        job = await load_job(connection, job_id=job_id)
    assert job is not None
    return job


async def tombstoned_with_purge(runtime: DatabaseRuntime) -> tuple[Document, JobId]:
    """A tombstoned Document that still has Chunks, and its stored purge Job."""
    normalized = normalize(
        SourceRecord.present(
            external_id="retired.md",
            version="v1",
            media_type="text/markdown",
            content=b"# Retired\n\nThis policy was retired.\n",
            access=SourceAccess(public=True),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(normalized, NormalizedDocument)
    result = await reconcile_document(
        runtime, normalized, readers=[Subject.public()], observed_at=OBSERVED_AT
    )
    [tombstone] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=OBSERVED_AT
    )
    return result.document, tombstone.purge_job_id


async def chunk_count(runtime: DatabaseRuntime, document_id: DocumentId) -> int:
    async with runtime.transaction() as connection:
        return len(await load_chunks(connection, document_id=document_id))
