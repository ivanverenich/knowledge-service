"""Real PostgreSQL tests for tombstoning missing Documents and purging Chunks."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import Subject
from knowledge_service.audit import AuditAction
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import DocumentId, JobId, SourceId
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import (
    PURGE_FAILED,
    purge_document_chunks,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import Change, reconcile_document
from knowledge_service.ingestion.sources import SourceAccess, SourceRecord
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobNotRetryable, JobStatus
from knowledge_service.persistence.access import load_access_grants
from knowledge_service.persistence.audit import load_audit_events
from knowledge_service.persistence.chunks import load_chunks, load_searchable_chunks
from knowledge_service.persistence.documents import load_document
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
from knowledge_service.settings import Settings

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
