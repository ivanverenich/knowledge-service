"""Durable audit events for the six audited actions, in real PostgreSQL.

Each test does one action through its real seam and reads the trail back:
a Question over HTTP (with the Document reads its Citations make), an
Access Grant change, a Synchronization Run, a rating, and an
administrator's embedding migration. Every event names who acted, what
they did to which object, whether it was allowed, how it ended, the request
or run, and when; no row holds a Question, a title, or a Document's text.
"""

import json
import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from database_fixtures import migration_config, migration_environment
from evidence_fakes import MARKER
from fakes import FakeSource
from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.access import Subject
from knowledge_service.answering.audit_trail import DatabaseAuditTrail
from knowledge_service.answering.current_citations import CurrentCitations
from knowledge_service.answering.events import AnswerEventReader, StreamOutcome
from knowledge_service.app import create_app
from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.conversations import (
    ConversationLimits,
    MessageFeedback,
    MessageRating,
    MessageRole,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import Document, DocumentProvenance
from knowledge_service.embeddings.migration_workflow import (
    MigrationInProgress,
    start_embedding_migration,
    switch_to_target_space,
)
from knowledge_service.embeddings.port import EmbeddingModelIdentity
from knowledge_service.identifiers import DocumentId, SynchronizationRunId, UserId
from knowledge_service.ingestion.publishing import publish_document
from knowledge_service.ingestion.reconciliation import (
    narrow_access,
    reconcile_document,
)
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceRecord,
    SourceUnavailable,
)
from knowledge_service.ingestion.synchronization import synchronize_source
from knowledge_service.persistence.audit import (
    load_audit_events,
    load_request_audit_events,
)
from knowledge_service.persistence.conversations import (
    add_message,
    rate_message,
    start_conversation,
)
from knowledge_service.settings import Settings
from test_citation_confirmation_integration import CitingModel, vector_source
from test_publishing_integration import publication_for
from test_streaming import streamer
from test_vector_search_integration import (
    IDENTITY,
    INSERT_SOURCE,
    OBSERVED_AT,
    SOURCE_ID,
    a_document,
    a_space,
    plain_text,
)

READERS_GROUP = next(iter(READER.group_ids))
LATER = OBSERVED_AT + timedelta(hours=1)
REQUEST_ID = UUID("00000000-0000-0000-0000-0000000000e1")
ADMIN = UserId.parse("00000000-0000-0000-0000-0000000000ad")
TARGET = EmbeddingModelIdentity(provider="fake", model="newer-embedding", dimensions=4)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
    await a_space(runtime, IDENTITY, is_current=True)
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def ask(runtime: DatabaseRuntime, model: CitingModel, question: str) -> bytes:
    """Ask over HTTP with a fixed request ID; return every byte sent."""
    # The real vector search, as the stream's Evidence source.
    source: Any = vector_source(runtime)
    app = create_app(
        streamer=streamer(
            model,
            source=source,
            citations=CurrentCitations(runtime),
            audit=DatabaseAuditTrail(runtime),
        ),
        authenticator=TRUSTED,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.post(
            "/v1/answer/stream",
            json={"text": question},
            headers={**AUTHORIZATION, "X-Request-ID": str(REQUEST_ID)},
        )
    assert response.status_code == 200
    return response.content


async def request_trail(runtime: DatabaseRuntime) -> tuple[AuditEvent, ...]:
    async with runtime.transaction() as connection:
        return await load_request_audit_events(
            connection, request_id=REQUEST_ID, limit=10
        )


async def trail(runtime: DatabaseRuntime, target_id: UUID) -> tuple[AuditEvent, ...]:
    async with runtime.transaction() as connection:
        return await load_audit_events(connection, target_id=target_id, limit=10)


async def every_row(runtime: DatabaseRuntime) -> str:
    """Every audit row, as one JSON text, to search for planted words."""
    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(sa.text("SELECT to_jsonb(a) FROM audit_events a"))
        ).scalars()
        return json.dumps(list(rows))


@pytest.mark.integration
async def test_a_question_and_the_documents_it_cites_are_recorded(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    document_id = await a_document(
        runtime,
        f"roadmap-{MARKER}.txt",
        readers=[Subject.group(READERS_GROUP)],
        vector=[1.0, 0.0, 0.0],
    )

    sent = await ask(
        runtime,
        CitingModel(f"roadmap-{MARKER}.txt"),
        f"What is on the roadmap? {MARKER}",
    )

    reader = AnswerEventReader()
    reader.feed(sent)
    assert reader.close().outcome is StreamOutcome.ANSWERED
    read, asked = await request_trail(runtime)
    assert (read.action, read.target_kind, read.target_id) == (
        AuditAction.DOCUMENT_READ,
        AuditTargetKind.DOCUMENT,
        document_id.value,
    )
    assert (read.decision, read.outcome) == (
        AuditDecision.ALLOWED,
        AuditOutcome.SUCCEEDED,
    )
    assert (asked.action, asked.outcome) == (
        AuditAction.QUESTION_ASKED,
        AuditOutcome.ANSWERED,
    )
    for event in (read, asked):
        assert event.actor_id == READER.user_id
        assert event.request_id == REQUEST_ID
        assert event.occurred_at.tzinfo is not None
    # Neither the Question, the title, nor the text was recorded or logged.
    assert MARKER not in await every_row(runtime)
    for record in caplog.records:
        assert MARKER not in record.getMessage()
        assert MARKER not in repr(record.__dict__)


@pytest.mark.integration
async def test_a_citation_revoked_while_the_model_writes_is_a_denied_read(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(
        runtime,
        "roadmap.txt",
        readers=[Subject.group(READERS_GROUP)],
        vector=[1.0, 0.0, 0.0],
    )

    async def revoke() -> None:
        await reconcile_document(
            runtime, plain_text("roadmap.txt"), readers=[], observed_at=LATER
        )

    await ask(runtime, CitingModel("roadmap.txt", before_reply=revoke), "Roadmap?")

    read, asked = await request_trail(runtime)
    assert read.target_id == document_id.value
    assert (read.decision, read.outcome) == (
        AuditDecision.DENIED,
        AuditOutcome.DENIED,
    )
    assert asked.outcome is AuditOutcome.ABSTAINED


@pytest.mark.integration
async def test_every_access_grant_change_is_recorded_with_its_run(
    runtime: DatabaseRuntime,
) -> None:
    run_id = SynchronizationRunId.new()
    created = await reconcile_document(
        runtime,
        plain_text("handbook.txt"),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT,
        run_id=run_id,
    )
    unchanged = await reconcile_document(
        runtime,
        plain_text("handbook.txt"),
        readers=[Subject.public()],
        observed_at=LATER,
        run_id=run_id,
    )
    narrowed = await narrow_access(
        runtime,
        source_id=SOURCE_ID,
        external_id="handbook.txt",
        readers=[],
        observed_at=LATER,
        run_id=run_id,
    )

    assert unchanged.document == created.document
    assert narrowed
    events = await trail(runtime, created.document.document_id.value)
    assert [(e.action, dict(e.metadata)) for e in events] == [
        (
            AuditAction.DOCUMENT_ACCESS_CHANGED,
            {"authorization_version": 1, "readers": 1},
        ),
        (
            AuditAction.DOCUMENT_ACCESS_CHANGED,
            {"authorization_version": 2, "readers": 0},
        ),
    ]
    assert {e.run_id for e in events} == {run_id.value}
    assert {e.actor_id for e in events} == {None}


def public_record(name: str) -> SourceRecord:
    return SourceRecord.present(
        external_id=name,
        version="v1",
        media_type="text/plain",
        content=f"The {name} policy.".encode(),
        access=SourceAccess(public=True),
    )


@pytest.mark.integration
async def test_a_synchronization_run_records_how_it_ended(
    runtime: DatabaseRuntime,
) -> None:
    summary = await synchronize_source(
        runtime,
        FakeSource([[public_record("a.txt"), public_record("b.txt")]]),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )

    *granted, finished = await trail(runtime, SOURCE_ID.value)
    assert granted == []
    assert (finished.action, finished.outcome) == (
        AuditAction.SYNCHRONIZATION_FINISHED,
        AuditOutcome.SUCCEEDED,
    )
    assert finished.run_id == summary.run_id.value
    assert dict(finished.metadata) == {
        "changed": 2,
        "unchanged": 0,
        "quarantined": 0,
        "tombstoned": 0,
        "unresolved": 0,
        "resumed": False,
        "mode": "full",
    }
    async with runtime.transaction() as connection:
        runs = (
            await connection.execute(
                sa.text(
                    "SELECT DISTINCT run_id FROM audit_events"
                    " WHERE action = 'document.access_changed'"
                )
            )
        ).scalars()
        assert list(runs) == [summary.run_id.value]


@pytest.mark.integration
async def test_a_failed_synchronization_run_records_only_the_error_type(
    runtime: DatabaseRuntime,
) -> None:
    run_id = SynchronizationRunId.new()

    with pytest.raises(SourceUnavailable):
        await synchronize_source(
            runtime,
            FakeSource(
                [[public_record("a.txt")]],
                fail_on_call=1,
                failure=SourceUnavailable(f"timeout reading {MARKER}"),
            ),
            source_id=SOURCE_ID,
            observed_at=OBSERVED_AT,
            principal_directory=None,
            run_id=run_id,
        )

    (finished,) = await trail(runtime, SOURCE_ID.value)
    assert (finished.outcome, finished.run_id) == (AuditOutcome.FAILED, run_id.value)
    assert dict(finished.metadata) == {"failure": "SourceUnavailable"}
    assert MARKER not in await every_row(runtime)


@pytest.mark.integration
async def test_a_rating_is_recorded_with_the_user_who_gave_it(
    runtime: DatabaseRuntime,
) -> None:
    limits = ConversationLimits()
    async with runtime.transaction() as connection:
        conversation = await start_conversation(
            connection, owner_id=READER.user_id, now=OBSERVED_AT, limits=limits
        )
        message = await add_message(
            connection,
            owner_id=READER.user_id,
            conversation_id=conversation.conversation_id,
            role=MessageRole.USER,
            text=f"A question {MARKER}",
            now=OBSERVED_AT,
            limits=limits,
        )
        await rate_message(
            connection,
            feedback=MessageFeedback(
                message_id=message.message_id,
                user_id=READER.user_id,
                rating=MessageRating.DOWN,
                rated_at=LATER,
            ),
        )

    (rated,) = await trail(runtime, message.message_id.value)
    assert (rated.action, rated.target_kind) == (
        AuditAction.MESSAGE_RATED,
        AuditTargetKind.MESSAGE,
    )
    assert (rated.actor_id, rated.occurred_at) == (READER.user_id, LATER)
    assert dict(rated.metadata) == {"rating": "down"}
    assert MARKER not in await every_row(runtime)


@pytest.mark.integration
async def test_a_rating_and_its_record_roll_back_together(
    runtime: DatabaseRuntime,
) -> None:
    limits = ConversationLimits()
    async with runtime.transaction() as connection:
        conversation = await start_conversation(
            connection, owner_id=READER.user_id, now=OBSERVED_AT, limits=limits
        )
        message = await add_message(
            connection,
            owner_id=READER.user_id,
            conversation_id=conversation.conversation_id,
            role=MessageRole.USER,
            text="A question",
            now=OBSERVED_AT,
            limits=limits,
        )

    class Abandoned(Exception):
        """The caller fails after rating, before its transaction commits."""

    async def rate_then_fail() -> None:
        async with runtime.transaction() as connection:
            await rate_message(
                connection,
                feedback=MessageFeedback(
                    message_id=message.message_id,
                    user_id=READER.user_id,
                    rating=MessageRating.UP,
                    rated_at=LATER,
                ),
            )
            raise Abandoned

    with pytest.raises(Abandoned):
        await rate_then_fail()

    assert await trail(runtime, message.message_id.value) == ()


@pytest.mark.integration
async def test_administration_is_recorded_with_the_administrator(
    runtime: DatabaseRuntime,
) -> None:
    migration = await start_embedding_migration(
        runtime, TARGET, now=OBSERVED_AT, actor_id=ADMIN
    )
    with pytest.raises(MigrationInProgress):
        await start_embedding_migration(runtime, TARGET, now=LATER, actor_id=ADMIN)
    switched = await switch_to_target_space(
        runtime, migration_id=migration.migration_id, now=LATER, actor_id=ADMIN
    )

    started, switched_event = await trail(runtime, migration.migration_id.value)
    assert [
        (e.action, e.actor_id, dict(e.metadata)) for e in (started, switched_event)
    ] == [
        (
            AuditAction.EMBEDDING_MIGRATION_STARTED,
            ADMIN,
            {"state": migration.state.value},
        ),
        (
            AuditAction.EMBEDDING_MIGRATION_SWITCHED,
            ADMIN,
            {"state": switched.state.value},
        ),
    ]
    # The refused second start changed nothing and recorded nothing.
    async with runtime.transaction() as connection:
        count = (
            await connection.execute(sa.text("SELECT count(*) FROM audit_events"))
        ).scalar()
    assert count == 2


@pytest.mark.integration
async def test_an_audit_row_cannot_hold_an_unknown_decision(
    runtime: DatabaseRuntime,
) -> None:
    # Past the Python checks, the database still refuses an unknown value.
    with pytest.raises(IntegrityError, match="ck_audit_events_decision"):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.text(
                    "INSERT INTO audit_events (event_id, occurred_at, action,"
                    " target_kind, target_id, metadata, decision, outcome)"
                    " VALUES (gen_random_uuid(), now(), 'document.read', 'document',"
                    " gen_random_uuid(), '{}'::jsonb, 'maybe', 'succeeded')"
                )
            )


@pytest.mark.integration
async def test_publishing_a_document_records_its_grants(
    runtime: DatabaseRuntime,
) -> None:
    document = Document.register(
        title="Handbook",
        document_id=DocumentId.new(),
        provenance=DocumentProvenance(
            source_id=SOURCE_ID, external_id="page-a", source_version="1"
        ),
        content_fingerprint="content-a",
        authorization_fingerprint="acl-a",
        observed_at=OBSERVED_AT,
    )
    async with runtime.transaction() as connection:
        stored_id = await publish_document(
            connection, publication_for(document, text_body="Setup.")
        )

    (event,) = await trail(runtime, stored_id.value)
    assert event.action is AuditAction.DOCUMENT_ACCESS_CHANGED
    assert dict(event.metadata) == {"authorization_version": 1, "readers": 1}


@pytest.mark.integration
async def test_an_event_reads_back_in_utc_whatever_the_session_time_zone(
    runtime: DatabaseRuntime,
) -> None:
    run_id = SynchronizationRunId.new()
    created = await reconcile_document(
        runtime,
        plain_text("handbook.txt"),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT,
        run_id=run_id,
    )

    async with runtime.transaction() as connection:
        await connection.execute(sa.text("SET LOCAL TIME ZONE 'Europe/Kyiv'"))
        (event,) = await load_audit_events(
            connection, target_id=created.document.document_id.value, limit=10
        )

    assert event.occurred_at == OBSERVED_AT
    assert event.occurred_at.utcoffset() == timedelta(0)


@pytest.mark.integration
def test_a_downgrade_drops_what_the_old_schema_cannot_hold(
    migrated_database: str,
) -> None:
    # A denied event must not come back as allowed after a round trip.
    # Synchronous: Alembic runs its own event loop.
    url = make_url(migrated_database)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO audit_events (event_id, occurred_at, action,"
                    " target_kind, target_id, metadata, decision, outcome)"
                    " VALUES (gen_random_uuid(), now(), 'job.queued', 'job',"
                    " gen_random_uuid(), '{}'::jsonb, 'denied', 'failed')"
                )
            )
        with migration_environment(url):
            command.downgrade(migration_config(), "a9b0c1d2e3f4")
            command.upgrade(migration_config(), "head")
        with engine.begin() as connection:
            count = connection.execute(
                sa.text("SELECT count(*) FROM audit_events")
            ).scalar()
    finally:
        engine.dispose()

    assert count == 0


@pytest.mark.integration
async def test_a_grant_write_and_its_record_roll_back_together(
    runtime: DatabaseRuntime,
) -> None:
    document = Document.register(
        title="Handbook",
        document_id=DocumentId.new(),
        provenance=DocumentProvenance(
            source_id=SOURCE_ID, external_id="page-b", source_version="1"
        ),
        content_fingerprint="content-b",
        authorization_fingerprint="acl-b",
        observed_at=OBSERVED_AT,
    )

    class Abandoned(Exception):
        """The caller fails after publishing, before its transaction commits."""

    async def publish_then_fail() -> None:
        async with runtime.transaction() as connection:
            await publish_document(
                connection, publication_for(document, text_body="Setup.")
            )
            raise Abandoned

    with pytest.raises(Abandoned):
        await publish_then_fail()

    async with runtime.transaction() as connection:
        count = (
            await connection.execute(sa.text("SELECT count(*) FROM audit_events"))
        ).scalar()
    assert count == 0
