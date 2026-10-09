"""PostgreSQL persistence for the stored domain records.

Domain values stay free of SQLAlchemy. This module owns the stored table shapes,
the translation between a domain value and a row, and the statements that read
and write them.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import (
    AccessGrant,
    InvalidGrant,
    Subject,
    SubjectKind,
    granted_subjects,
)
from knowledge_service.audit import (
    AuditAction,
    AuditEvent,
    AuditTargetKind,
    AuditValue,
    InvalidAuditEvent,
)
from knowledge_service.chunks import Chunk, StaleChunkWrite, ordered_chunks
from knowledge_service.conversations import (
    Conversation,
    ConversationFull,
    InvalidConversation,
    Message,
    MessageFeedback,
    MessageRating,
    MessageRole,
)
from knowledge_service.documents import (
    AuthorizationVersion,
    ContentVersion,
    Document,
    DocumentAvailability,
    DocumentProvenance,
    InvalidDocument,
)
from knowledge_service.evaluation import (
    EvaluationRun,
    EvaluationStatus,
    InvalidEvaluationRun,
)
from knowledge_service.identifiers import (
    AccessGrantId,
    AuditEventId,
    ChunkId,
    ConversationId,
    DocumentId,
    EvaluationDatasetId,
    EvaluationRunId,
    GroupId,
    JobId,
    MessageId,
    SourceId,
    UserId,
)
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus

metadata = sa.MetaData()

sources = sa.Table(
    "sources",
    metadata,
    sa.Column("source_id", sa.Uuid(), primary_key=True),
    sa.Column("kind", sa.String(length=32), nullable=False),
    sa.Column("location", sa.Text(), nullable=False),
    sa.Column("credentials_reference", sa.Text(), nullable=True),
    sa.Column("display_name", sa.Text(), nullable=False),
    sa.Column("enabled", sa.Boolean(), nullable=False),
    sa.Column("checkpoint", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("kind", "location", name="uq_sources_kind_location"),
)

synchronization_runs = sa.Table(
    "synchronization_runs",
    metadata,
    sa.Column("run_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "source_id",
        sa.Uuid(),
        sa.ForeignKey("sources.source_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("status", sa.String(length=16), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("detail", sa.Text(), nullable=True),
    sa.CheckConstraint(
        "status in ('running', 'succeeded', 'failed')",
        name="ck_synchronization_runs_status",
    ),
    sa.CheckConstraint(
        "(status = 'running') = (finished_at is null)",
        name="ck_synchronization_runs_finished_at",
    ),
    sa.Index(
        "ix_synchronization_runs_source_started_at",
        "source_id",
        "started_at",
    ),
)

documents = sa.Table(
    "documents",
    metadata,
    sa.Column("document_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "source_id",
        sa.Uuid(),
        sa.ForeignKey("sources.source_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("external_id", sa.Text(), nullable=False),
    sa.Column("source_version", sa.Text(), nullable=True),
    sa.Column("content_version", sa.Integer(), nullable=False),
    sa.Column("content_fingerprint", sa.Text(), nullable=False),
    sa.Column("authorization_version", sa.Integer(), nullable=False),
    sa.Column("authorization_fingerprint", sa.Text(), nullable=False),
    sa.Column("availability", sa.String(length=16), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "source_id", "external_id", name="uq_documents_source_external"
    ),
    sa.CheckConstraint("content_version >= 1", name="ck_documents_content_version"),
    sa.CheckConstraint(
        "authorization_version >= 1", name="ck_documents_authorization_version"
    ),
    sa.CheckConstraint(
        "availability in ('available', 'tombstoned')",
        name="ck_documents_availability",
    ),
)

chunks = sa.Table(
    "chunks",
    metadata,
    sa.Column("chunk_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "document_id",
        sa.Uuid(),
        sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("content_version", sa.Integer(), nullable=False),
    sa.Column("ordinal", sa.Integer(), nullable=False),
    sa.Column("text", sa.Text(), nullable=False),
    sa.Column("heading_path", sa.Text(), nullable=True),
    sa.Column("token_count", sa.Integer(), nullable=False),
    sa.Column("content_fingerprint", sa.Text(), nullable=False),
    sa.Column("source_anchor", sa.Text(), nullable=True),
    sa.UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
    sa.CheckConstraint("content_version >= 1", name="ck_chunks_content_version"),
    sa.CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal"),
    sa.CheckConstraint("token_count >= 1", name="ck_chunks_token_count"),
)

access_grants = sa.Table(
    "access_grants",
    metadata,
    sa.Column("grant_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "document_id",
        sa.Uuid(),
        sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("subject_kind", sa.String(length=8), nullable=False),
    sa.Column("subject_id", sa.Uuid(), nullable=True),
    sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "subject_kind in ('public', 'user', 'group')",
        name="ck_access_grants_subject_kind",
    ),
    sa.CheckConstraint(
        "(subject_kind = 'public') = (subject_id is null)",
        name="ck_access_grants_subject_id",
    ),
    sa.UniqueConstraint(
        "document_id",
        "subject_kind",
        "subject_id",
        name="uq_access_grants_document_subject",
        postgresql_nulls_not_distinct=True,
    ),
    sa.Index(
        "ix_access_grants_subject_document",
        "subject_kind",
        "subject_id",
        "document_id",
    ),
)

conversations = sa.Table(
    "conversations",
    metadata,
    sa.Column("conversation_id", sa.Uuid(), primary_key=True),
    sa.Column("owner_id", sa.Uuid(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("retention_deadline", sa.DateTime(timezone=True), nullable=False),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(
        "retention_deadline > created_at", name="ck_conversations_retention"
    ),
    sa.CheckConstraint(
        "last_message_at >= created_at", name="ck_conversations_last_message"
    ),
    sa.CheckConstraint(
        "deleted_at is null or deleted_at >= created_at",
        name="ck_conversations_deleted_at",
    ),
    sa.Index(
        "ix_conversations_retention_deadline",
        "retention_deadline",
        postgresql_where=sa.text("deleted_at is null"),
    ),
)

messages = sa.Table(
    "messages",
    metadata,
    sa.Column("message_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "conversation_id",
        sa.Uuid(),
        sa.ForeignKey("conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("ordinal", sa.Integer(), nullable=False),
    sa.Column("role", sa.String(length=16), nullable=False),
    sa.Column("text", sa.Text(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "conversation_id", "ordinal", name="uq_messages_conversation_ordinal"
    ),
    sa.CheckConstraint("ordinal >= 0", name="ck_messages_ordinal"),
    sa.CheckConstraint("role in ('user', 'assistant')", name="ck_messages_role"),
)

message_feedback = sa.Table(
    "message_feedback",
    metadata,
    sa.Column(
        "message_id",
        sa.Uuid(),
        sa.ForeignKey("messages.message_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("user_id", sa.Uuid(), primary_key=True),
    sa.Column("rating", sa.String(length=8), nullable=False),
    sa.Column("rated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("rating in ('up', 'down')", name="ck_message_feedback_rating"),
)


jobs = sa.Table(
    "jobs",
    metadata,
    sa.Column("job_id", sa.Uuid(), primary_key=True),
    sa.Column("kind", sa.String(length=32), nullable=False),
    sa.Column("target_id", sa.Uuid(), nullable=False),
    sa.Column("status", sa.String(length=16), nullable=False),
    sa.Column("attempt", sa.Integer(), nullable=False),
    sa.Column("max_attempts", sa.Integer(), nullable=False),
    sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_error_code", sa.String(length=64), nullable=True),
    sa.CheckConstraint(
        "kind in ('synchronize_source', 'purge_conversation')", name="ck_jobs_kind"
    ),
    sa.CheckConstraint(
        "status in ('queued', 'running', 'succeeded', 'failed')",
        name="ck_jobs_status",
    ),
    sa.CheckConstraint(
        "max_attempts >= 1 and attempt >= 0 and attempt <= max_attempts",
        name="ck_jobs_attempt",
    ),
    sa.CheckConstraint(
        "(status in ('succeeded', 'failed')) = (finished_at is not null)",
        name="ck_jobs_finished_at",
    ),
    sa.CheckConstraint(
        "(status = 'queued') = (started_at is null)", name="ck_jobs_started_at"
    ),
    sa.CheckConstraint(
        "(status = 'failed') = (last_error_code is not null)",
        name="ck_jobs_last_error_code",
    ),
)

audit_events = sa.Table(
    "audit_events",
    metadata,
    sa.Column("event_id", sa.Uuid(), primary_key=True),
    sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("actor_id", sa.Uuid(), nullable=True),
    sa.Column("action", sa.String(length=48), nullable=False),
    sa.Column("target_kind", sa.String(length=24), nullable=False),
    sa.Column("target_id", sa.Uuid(), nullable=False),
    sa.Column("metadata", JSONB, nullable=False),
    sa.CheckConstraint(
        "action in ('source.registered', 'document.availability_changed',"
        " 'conversation.deleted', 'job.queued', 'job.failed',"
        " 'evaluation_run.started')",
        name="ck_audit_events_action",
    ),
    sa.CheckConstraint(
        "target_kind in ('source', 'document', 'conversation', 'job',"
        " 'evaluation_run')",
        name="ck_audit_events_target_kind",
    ),
    sa.CheckConstraint(
        "pg_column_size(metadata) <= 1024", name="ck_audit_events_metadata_size"
    ),
    sa.Index(
        "ix_audit_events_target_occurred_at",
        "target_id",
        "occurred_at",
        "event_id",
    ),
)

evaluation_runs = sa.Table(
    "evaluation_runs",
    metadata,
    sa.Column("run_id", sa.Uuid(), primary_key=True),
    sa.Column("dataset_id", sa.Uuid(), nullable=False),
    sa.Column("dataset_version", sa.Integer(), nullable=False),
    sa.Column("configuration_fingerprint", sa.Text(), nullable=False),
    sa.Column("status", sa.String(length=16), nullable=False),
    sa.Column("artifact_location", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(
        "status in ('pending', 'running', 'succeeded', 'failed')",
        name="ck_evaluation_runs_status",
    ),
    sa.CheckConstraint("dataset_version >= 1", name="ck_evaluation_runs_dataset"),
    sa.CheckConstraint(
        "(status = 'succeeded') = (artifact_location is not null)",
        name="ck_evaluation_runs_artifact_location",
    ),
    sa.CheckConstraint(
        "(status in ('succeeded', 'failed')) = (finished_at is not null)",
        name="ck_evaluation_runs_finished_at",
    ),
    sa.CheckConstraint(
        "(status = 'pending') = (started_at is null)",
        name="ck_evaluation_runs_started_at",
    ),
)


def due_conversations_statement(now: datetime, limit: int) -> sa.Select[Any]:
    """Select live Conversations whose retention deadline has passed."""
    return (
        sa.select(conversations)
        .where(
            conversations.c.deleted_at.is_(None),
            conversations.c.retention_deadline <= now,
        )
        .order_by(conversations.c.retention_deadline)
        .limit(limit)
    )


def audit_trail_statement(target_id: UUID, limit: int) -> sa.Select[Any]:
    """Select the trail recorded against one target, oldest first."""
    return (
        sa.select(audit_events)
        .where(audit_events.c.target_id == target_id)
        .order_by(audit_events.c.occurred_at, audit_events.c.event_id)
        .limit(limit)
    )


def source_runs_statement(source_id: UUID, limit: int) -> sa.Select[Any]:
    """Select the most recent Synchronization Runs of one Source."""
    return (
        sa.select(synchronization_runs)
        .where(synchronization_runs.c.source_id == source_id)
        .order_by(synchronization_runs.c.started_at.desc())
        .limit(limit)
    )


def subject_grants_statement(subject_kind: str, subject_id: UUID) -> sa.Select[Any]:
    """Select the Documents one verified subject is allowed to reach."""
    return sa.select(access_grants.c.document_id).where(
        access_grants.c.subject_kind == subject_kind,
        access_grants.c.subject_id == subject_id,
    )


def as_row(document: Document) -> dict[str, object]:
    """Translate a domain Document into the columns that store it."""
    return {
        "document_id": document.document_id.value,
        "source_id": document.provenance.source_id.value,
        "external_id": document.provenance.external_id,
        "source_version": document.provenance.source_version,
        "content_version": document.content_version.number,
        "content_fingerprint": document.content_fingerprint,
        "authorization_version": document.authorization_version.number,
        "authorization_fingerprint": document.authorization_fingerprint,
        "availability": document.availability.value,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }


def as_document(record: Mapping[str, object]) -> Document:
    """Rebuild a domain Document from a stored row."""
    try:
        availability = DocumentAvailability(cast(str, record["availability"]))
    except ValueError:
        raise InvalidDocument("stored availability is not a known value") from None

    return Document(
        document_id=DocumentId(cast(UUID, record["document_id"])),
        provenance=DocumentProvenance(
            source_id=SourceId(cast(UUID, record["source_id"])),
            external_id=cast(str, record["external_id"]),
            source_version=cast("str | None", record["source_version"]),
        ),
        content_version=ContentVersion(cast(int, record["content_version"])),
        content_fingerprint=cast(str, record["content_fingerprint"]),
        authorization_version=AuthorizationVersion(
            cast(int, record["authorization_version"])
        ),
        authorization_fingerprint=cast(str, record["authorization_fingerprint"]),
        availability=availability,
        created_at=cast(datetime, record["created_at"]),
        updated_at=cast(datetime, record["updated_at"]),
    )


def _chunk_row(chunk: Chunk) -> dict[str, object]:
    return {
        "chunk_id": chunk.chunk_id.value,
        "document_id": chunk.document_id.value,
        "content_version": chunk.content_version.number,
        "ordinal": chunk.ordinal,
        "text": chunk.text,
        "heading_path": chunk.heading_path,
        "token_count": chunk.token_count,
        "content_fingerprint": chunk.content_fingerprint,
        "source_anchor": chunk.source_anchor,
    }


def _chunk_from(record: Mapping[str, object]) -> Chunk:
    return Chunk(
        chunk_id=ChunkId(cast(UUID, record["chunk_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        content_version=ContentVersion(cast(int, record["content_version"])),
        ordinal=cast(int, record["ordinal"]),
        text=cast(str, record["text"]),
        heading_path=cast("str | None", record["heading_path"]),
        token_count=cast(int, record["token_count"]),
        content_fingerprint=cast(str, record["content_fingerprint"]),
        source_anchor=cast("str | None", record["source_anchor"]),
    )


def _grant_row(grant: AccessGrant) -> dict[str, object]:
    identifier = grant.subject.identifier
    return {
        "grant_id": grant.grant_id.value,
        "document_id": grant.document_id.value,
        "subject_kind": grant.subject.kind.value,
        "subject_id": identifier.value if identifier is not None else None,
        "granted_at": grant.granted_at,
    }


def _grant_from(record: Mapping[str, object]) -> AccessGrant:
    try:
        kind = SubjectKind(cast(str, record["subject_kind"]))
    except ValueError:
        raise InvalidGrant("stored subject kind is not a known value") from None

    stored_id = cast("UUID | None", record["subject_id"])
    if kind is SubjectKind.PUBLIC:
        subject = Subject.public()
    elif kind is SubjectKind.USER:
        subject = Subject.user(UserId(cast(UUID, stored_id)))
    else:
        subject = Subject.group(GroupId(cast(UUID, stored_id)))

    return AccessGrant(
        grant_id=AccessGrantId(cast(UUID, record["grant_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        subject=subject,
        granted_at=cast(datetime, record["granted_at"]),
    )


def _conversation_row(conversation: Conversation) -> dict[str, object]:
    return {
        "conversation_id": conversation.conversation_id.value,
        "owner_id": conversation.owner_id.value,
        "created_at": conversation.created_at,
        "last_message_at": conversation.last_message_at,
        "retention_deadline": conversation.retention_deadline,
        "deleted_at": conversation.deleted_at,
    }


def _conversation_from(record: Mapping[str, object]) -> Conversation:
    return Conversation(
        conversation_id=ConversationId(cast(UUID, record["conversation_id"])),
        owner_id=UserId(cast(UUID, record["owner_id"])),
        created_at=cast(datetime, record["created_at"]),
        last_message_at=cast(datetime, record["last_message_at"]),
        retention_deadline=cast(datetime, record["retention_deadline"]),
        deleted_at=cast("datetime | None", record["deleted_at"]),
    )


def _message_row(message: Message) -> dict[str, object]:
    return {
        "message_id": message.message_id.value,
        "conversation_id": message.conversation_id.value,
        "ordinal": message.ordinal,
        "role": message.role.value,
        "text": message.text,
        "created_at": message.created_at,
    }


def _message_from(record: Mapping[str, object]) -> Message:
    try:
        role = MessageRole(cast(str, record["role"]))
    except ValueError:
        raise InvalidConversation("stored message role is not a known value") from None

    return Message(
        message_id=MessageId(cast(UUID, record["message_id"])),
        conversation_id=ConversationId(cast(UUID, record["conversation_id"])),
        ordinal=cast(int, record["ordinal"]),
        role=role,
        text=cast(str, record["text"]),
        created_at=cast(datetime, record["created_at"]),
    )


def _feedback_row(feedback: MessageFeedback) -> dict[str, object]:
    return {
        "message_id": feedback.message_id.value,
        "user_id": feedback.user_id.value,
        "rating": feedback.rating.value,
        "rated_at": feedback.rated_at,
    }


def _feedback_from(record: Mapping[str, object]) -> MessageFeedback:
    try:
        rating = MessageRating(cast(str, record["rating"]))
    except ValueError:
        raise InvalidConversation("stored rating is not a known value") from None

    return MessageFeedback(
        message_id=MessageId(cast(UUID, record["message_id"])),
        user_id=UserId(cast(UUID, record["user_id"])),
        rating=rating,
        rated_at=cast(datetime, record["rated_at"]),
    )


def _job_row(job: Job) -> dict[str, object]:
    return {
        "job_id": job.job_id.value,
        "kind": job.kind.value,
        "target_id": job.target_id,
        "status": job.status.value,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "queued_at": job.queued_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "last_error_code": job.last_error_code,
    }


def _job_from(record: Mapping[str, object]) -> Job:
    try:
        kind = JobKind(cast(str, record["kind"]))
        status = JobStatus(cast(str, record["status"]))
    except ValueError:
        raise InvalidJob("stored job kind or status is not a known value") from None

    return Job(
        job_id=JobId(cast(UUID, record["job_id"])),
        kind=kind,
        target_id=cast(UUID, record["target_id"]),
        status=status,
        attempt=cast(int, record["attempt"]),
        max_attempts=cast(int, record["max_attempts"]),
        queued_at=cast(datetime, record["queued_at"]),
        started_at=cast("datetime | None", record["started_at"]),
        finished_at=cast("datetime | None", record["finished_at"]),
        last_error_code=cast("str | None", record["last_error_code"]),
    )


def _audit_row(event: AuditEvent) -> dict[str, object]:
    return {
        "event_id": event.event_id.value,
        "occurred_at": event.occurred_at,
        "actor_id": event.actor_id.value if event.actor_id is not None else None,
        "action": event.action.value,
        "target_kind": event.target_kind.value,
        "target_id": event.target_id,
        "metadata": dict(event.metadata),
    }


def _audit_from(record: Mapping[str, object]) -> AuditEvent:
    try:
        action = AuditAction(cast(str, record["action"]))
        target_kind = AuditTargetKind(cast(str, record["target_kind"]))
    except ValueError:
        raise InvalidAuditEvent(
            "stored audit action or target kind is not a known value"
        ) from None

    actor_id = cast("UUID | None", record["actor_id"])
    stored = cast("dict[str, object]", record["metadata"])
    return AuditEvent(
        event_id=AuditEventId(cast(UUID, record["event_id"])),
        occurred_at=cast(datetime, record["occurred_at"]),
        actor_id=UserId(actor_id) if actor_id is not None else None,
        action=action,
        target_kind=target_kind,
        target_id=cast(UUID, record["target_id"]),
        metadata=cast("dict[str, AuditValue]", stored),
    )


def _evaluation_row(run: EvaluationRun) -> dict[str, object]:
    return {
        "run_id": run.run_id.value,
        "dataset_id": run.dataset_id.value,
        "dataset_version": run.dataset_version,
        "configuration_fingerprint": run.configuration_fingerprint,
        "status": run.status.value,
        "artifact_location": run.artifact_location,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _evaluation_from(record: Mapping[str, object]) -> EvaluationRun:
    try:
        status = EvaluationStatus(cast(str, record["status"]))
    except ValueError:
        raise InvalidEvaluationRun("stored run status is not a known value") from None

    return EvaluationRun(
        run_id=EvaluationRunId(cast(UUID, record["run_id"])),
        dataset_id=EvaluationDatasetId(cast(UUID, record["dataset_id"])),
        dataset_version=cast(int, record["dataset_version"]),
        configuration_fingerprint=cast(str, record["configuration_fingerprint"]),
        status=status,
        artifact_location=cast("str | None", record["artifact_location"]),
        created_at=cast(datetime, record["created_at"]),
        started_at=cast("datetime | None", record["started_at"]),
        finished_at=cast("datetime | None", record["finished_at"]),
    )


async def upsert_document(
    connection: AsyncConnection, document: Document
) -> DocumentId:
    """Store a Document, keeping the identity and creation time already stored."""
    statement = insert(documents).values(as_row(document))
    statement = statement.on_conflict_do_update(
        constraint="uq_documents_source_external",
        set_={
            "source_version": statement.excluded.source_version,
            "content_version": statement.excluded.content_version,
            "content_fingerprint": statement.excluded.content_fingerprint,
            "authorization_version": statement.excluded.authorization_version,
            "authorization_fingerprint": statement.excluded.authorization_fingerprint,
            "availability": statement.excluded.availability,
            "updated_at": statement.excluded.updated_at,
        },
    ).returning(documents.c.document_id)
    stored_id = cast(UUID, (await connection.execute(statement)).scalar_one())
    return DocumentId(stored_id)


async def load_document(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    external_id: str,
) -> Document | None:
    """Read the stored Document for one source item, if it exists."""
    statement = sa.select(documents).where(
        documents.c.source_id == source_id.value,
        documents.c.external_id == external_id,
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return as_document(dict(record))


async def replace_chunks(
    connection: AsyncConnection,
    *,
    document: Document,
    chunk_run: Sequence[Chunk],
) -> tuple[Chunk, ...]:
    """Replace every stored Chunk for a Document with one ordered content version."""
    ordered = ordered_chunks(
        document_id=document.document_id,
        content_version=document.content_version,
        chunks=chunk_run,
    )

    stored_version = cast(
        "int | None",
        await connection.scalar(
            sa.select(sa.func.max(chunks.c.content_version)).where(
                chunks.c.document_id == document.document_id.value
            )
        ),
    )
    if stored_version is not None and stored_version > document.content_version.number:
        raise StaleChunkWrite(
            f"stored chunks are at content version {stored_version}; refusing to"
            f" replace them with version {document.content_version.number}"
        )

    await connection.execute(
        sa.delete(chunks).where(chunks.c.document_id == document.document_id.value)
    )
    if ordered:
        await connection.execute(
            sa.insert(chunks), [_chunk_row(chunk) for chunk in ordered]
        )
    return ordered


async def load_chunks(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
) -> tuple[Chunk, ...]:
    """Read the stored Chunks for a Document in ordinal order."""
    statement = (
        sa.select(chunks)
        .where(chunks.c.document_id == document_id.value)
        .order_by(chunks.c.ordinal)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_chunk_from(dict(record)) for record in records)


async def replace_access_grants(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
    grant_run: Sequence[AccessGrant],
) -> tuple[AccessGrant, ...]:
    """Replace every stored Access Grant for a Document with the given set."""
    ordered = granted_subjects(document_id=document_id, grants=grant_run)

    await connection.execute(
        sa.delete(access_grants).where(access_grants.c.document_id == document_id.value)
    )
    if ordered:
        await connection.execute(
            sa.insert(access_grants), [_grant_row(grant) for grant in ordered]
        )
    return ordered


async def load_access_grants(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
) -> tuple[AccessGrant, ...]:
    """Read the stored Access Grants for a Document."""
    statement = (
        sa.select(access_grants)
        .where(access_grants.c.document_id == document_id.value)
        .order_by(access_grants.c.subject_kind, access_grants.c.subject_id)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_grant_from(dict(record)) for record in records)


async def create_conversation(
    connection: AsyncConnection, conversation: Conversation
) -> ConversationId:
    """Store a new Conversation."""
    await connection.execute(
        sa.insert(conversations).values(_conversation_row(conversation))
    )
    return conversation.conversation_id


async def load_conversation(
    connection: AsyncConnection,
    *,
    conversation_id: ConversationId,
) -> Conversation | None:
    """Read a stored Conversation, if it exists."""
    statement = sa.select(conversations).where(
        conversations.c.conversation_id == conversation_id.value
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _conversation_from(dict(record))


async def append_message(
    connection: AsyncConnection,
    *,
    conversation: Conversation,
    message: Message,
    message_limit: int,
) -> Message:
    """Append one Message, refusing a Conversation already at its bound."""
    if message.conversation_id != conversation.conversation_id:
        raise InvalidConversation("the message must belong to the conversation")

    stored = cast(
        int,
        await connection.scalar(
            sa.select(sa.func.count())
            .select_from(messages)
            .where(messages.c.conversation_id == conversation.conversation_id.value)
        ),
    )
    if stored >= message_limit:
        raise ConversationFull(f"the conversation already holds {stored} messages")

    await connection.execute(sa.insert(messages).values(_message_row(message)))
    advanced = conversation.record_message(observed_at=message.created_at)
    await connection.execute(
        sa.update(conversations)
        .where(conversations.c.conversation_id == conversation.conversation_id.value)
        .values(last_message_at=advanced.last_message_at)
    )
    return message


async def load_messages(
    connection: AsyncConnection,
    *,
    conversation_id: ConversationId,
) -> tuple[Message, ...]:
    """Read a Conversation's Messages in ordinal order."""
    statement = (
        sa.select(messages)
        .where(messages.c.conversation_id == conversation_id.value)
        .order_by(messages.c.ordinal)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_message_from(dict(record)) for record in records)


async def rate_message(
    connection: AsyncConnection, *, feedback: MessageFeedback
) -> MessageFeedback:
    """Store one rating per Message per User, replacing any earlier rating."""
    statement = insert(message_feedback).values(_feedback_row(feedback))
    statement = statement.on_conflict_do_update(
        index_elements=["message_id", "user_id"],
        set_={
            "rating": statement.excluded.rating,
            "rated_at": statement.excluded.rated_at,
        },
    )
    await connection.execute(statement)
    return feedback


async def load_message_feedback(
    connection: AsyncConnection, *, message_id: MessageId
) -> tuple[MessageFeedback, ...]:
    """Read the current ratings attached to one Message."""
    statement = (
        sa.select(message_feedback)
        .where(message_feedback.c.message_id == message_id.value)
        .order_by(message_feedback.c.rated_at, message_feedback.c.user_id)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_feedback_from(dict(record)) for record in records)


async def load_conversations_due_for_deletion(
    connection: AsyncConnection, *, now: datetime, limit: int
) -> tuple[Conversation, ...]:
    """Select live Conversations whose retention deadline has passed."""
    statement = due_conversations_statement(now=now, limit=limit)
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_conversation_from(dict(record)) for record in records)


async def enqueue_job(connection: AsyncConnection, job: Job) -> JobId:
    """Store a queued Job."""
    await connection.execute(sa.insert(jobs).values(_job_row(job)))
    return job.job_id


async def load_job(connection: AsyncConnection, *, job_id: JobId) -> Job | None:
    """Read a stored Job, if it exists."""
    statement = sa.select(jobs).where(jobs.c.job_id == job_id.value)
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def save_job(connection: AsyncConnection, *, job: Job) -> Job:
    """Store the status a Job has reached."""
    statement = (
        sa.update(jobs).where(jobs.c.job_id == job.job_id.value).values(_job_row(job))
    )
    await connection.execute(statement)
    return job


async def record_audit_event(
    connection: AsyncConnection, event: AuditEvent
) -> AuditEventId:
    """Append one audit event. There is no function that updates or deletes one."""
    await connection.execute(sa.insert(audit_events).values(_audit_row(event)))
    return event.event_id


async def load_audit_events(
    connection: AsyncConnection, *, target_id: UUID, limit: int
) -> tuple[AuditEvent, ...]:
    """Read the trail recorded against one target, oldest first."""
    statement = audit_trail_statement(target_id=target_id, limit=limit)
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_audit_from(dict(record)) for record in records)


async def create_evaluation_run(
    connection: AsyncConnection, run: EvaluationRun
) -> EvaluationRunId:
    """Store a planned Evaluation Run."""
    await connection.execute(sa.insert(evaluation_runs).values(_evaluation_row(run)))
    return run.run_id


async def load_evaluation_run(
    connection: AsyncConnection, *, run_id: EvaluationRunId
) -> EvaluationRun | None:
    """Read a stored Evaluation Run, if it exists."""
    statement = sa.select(evaluation_runs).where(
        evaluation_runs.c.run_id == run_id.value
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _evaluation_from(dict(record))


async def save_evaluation_run(
    connection: AsyncConnection, *, run: EvaluationRun
) -> EvaluationRun:
    """Store the status a run has reached."""
    statement = (
        sa.update(evaluation_runs)
        .where(evaluation_runs.c.run_id == run.run_id.value)
        .values(_evaluation_row(run))
    )
    await connection.execute(statement)
    return run
