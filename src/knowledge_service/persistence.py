"""PostgreSQL persistence for the stored domain records.

Domain values stay free of SQLAlchemy. This module owns the stored table shapes,
the translation between a domain value and a row, and the statements that read
and write them.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, assert_never, cast
from uuid import UUID

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import (
    AccessGrant,
    AuthorizationContext,
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
    DocumentMetadata,
    DocumentProvenance,
    InvalidDocument,
)
from knowledge_service.embedding_migration import (
    EmbeddingMigration,
    EmbeddingMigrationError,
    EmbeddingSpace,
    MigrationState,
)
from knowledge_service.embeddings import EmbeddingModelIdentity, unit_length
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
    EmbeddingMigrationId,
    EmbeddingSpaceId,
    EvaluationDatasetId,
    EvaluationRunId,
    GroupId,
    IndexConfigurationId,
    JobId,
    MessageId,
    SourceId,
    UserId,
)
from knowledge_service.index_configuration import (
    IndexConfiguration,
    IndexConfigurationConflict,
    settings_fingerprint,
)
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.language import Language
from knowledge_service.retrieval_filters import FilterField, MetadataCondition

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
    sa.Index(
        "uq_synchronization_runs_running_source",
        "source_id",
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
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
    sa.Column("title", sa.Text(), nullable=False),
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

# What retrieval may filter a Document by. Kept apart from `documents`, so
# the Document's own versions and fingerprints do not change with it.
document_metadata = sa.Table(
    "document_metadata",
    metadata,
    sa.Column(
        "document_id",
        sa.Uuid(),
        sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("media_type", sa.Text(), nullable=False),
    sa.Column("language", sa.String(length=8), nullable=False),
    sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "media_type <> '' and length(media_type) <= 255",
        name="ck_document_metadata_media_type",
    ),
    sa.CheckConstraint(
        "language in ('en', 'uk', 'mixed', 'unknown')",
        name="ck_document_metadata_language",
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

# One row per embedding model identity. Vectors are comparable only within
# one space, and at most one space is the one retrieval reads.
embedding_spaces = sa.Table(
    "embedding_spaces",
    metadata,
    sa.Column("space_id", sa.Uuid(), primary_key=True),
    sa.Column("provider", sa.Text(), nullable=False),
    sa.Column("model", sa.Text(), nullable=False),
    sa.Column("dimensions", sa.Integer(), nullable=False),
    sa.Column("is_current", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "provider", "model", "dimensions", name="uq_embedding_spaces_identity"
    ),
    # The target of chunk_embeddings' composite foreign key.
    sa.UniqueConstraint(
        "space_id", "dimensions", name="uq_embedding_spaces_space_dimensions"
    ),
    sa.CheckConstraint(
        "dimensions between 1 and 16000", name="ck_embedding_spaces_dimensions"
    ),
    sa.CheckConstraint(
        "provider <> '' and provider = btrim(provider)"
        " and model <> '' and model = btrim(model)",
        name="ck_embedding_spaces_identity",
    ),
    # A unique index is checked row by row and cannot be deferred: to switch
    # the current space, clear the old one first, then set the new one.
    sa.Index(
        "uq_embedding_spaces_current",
        "is_current",
        unique=True,
        postgresql_where=sa.text("is_current"),
    ),
)

# One vector per Chunk per space. The row repeats its space's dimensions, so
# the database itself refuses a vector of another length.
chunk_embeddings = sa.Table(
    "chunk_embeddings",
    metadata,
    sa.Column(
        "chunk_id",
        sa.Uuid(),
        sa.ForeignKey("chunks.chunk_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("space_id", sa.Uuid(), primary_key=True),
    sa.Column("dimensions", sa.Integer(), nullable=False),
    sa.Column("embedding", Vector(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["space_id", "dimensions"],
        ["embedding_spaces.space_id", "embedding_spaces.dimensions"],
        ondelete="CASCADE",
        name="fk_chunk_embeddings_space_dimensions",
    ),
    sa.CheckConstraint(
        "vector_dims(embedding) = dimensions",
        name="ck_chunk_embeddings_dimensions",
    ),
    # Serves deleting a space and reading one space's vectors (M04-T06).
    sa.Index("ix_chunk_embeddings_space_id", "space_id"),
)

# One row per distinct set of settings that shapes stored vectors. Rows are
# only ever inserted: a changed setting is a new row, never an update.
index_configurations = sa.Table(
    "index_configurations",
    metadata,
    sa.Column("configuration_id", sa.Uuid(), primary_key=True),
    sa.Column("fingerprint", sa.Text(), nullable=False),
    sa.Column("settings", JSONB(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("fingerprint", name="uq_index_configurations_fingerprint"),
    sa.CheckConstraint(
        "fingerprint ~ '^[0-9a-f]{64}$'",
        name="ck_index_configurations_fingerprint",
    ),
)

# One row per move from one Embedding Space to another. The spaces are kept
# by reference only while they exist: cleaning up deletes the old one.
embedding_migrations = sa.Table(
    "embedding_migrations",
    metadata,
    sa.Column("migration_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "source_space_id",
        sa.Uuid(),
        sa.ForeignKey("embedding_spaces.space_id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "target_space_id",
        sa.Uuid(),
        sa.ForeignKey("embedding_spaces.space_id", ondelete="SET NULL"),
        nullable=True,
    ),
    sa.Column(
        "target_configuration_id",
        sa.Uuid(),
        sa.ForeignKey("index_configurations.configuration_id"),
        nullable=False,
    ),
    sa.Column("state", sa.String(length=16), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "state in ('dual_writing', 'switched', 'cleaned')",
        name="ck_embedding_migrations_state",
    ),
    sa.CheckConstraint(
        "updated_at >= started_at", name="ck_embedding_migrations_updated_at"
    ),
    # Serve the foreign keys, so deleting a space or configuration does not
    # scan every migration.
    sa.Index("ix_embedding_migrations_source_space_id", "source_space_id"),
    sa.Index("ix_embedding_migrations_target_space_id", "target_space_id"),
    sa.Index(
        "ix_embedding_migrations_target_configuration_id", "target_configuration_id"
    ),
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
        "kind in ('synchronize_source', 'purge_conversation', 'purge_document_chunks')",
        name="ck_jobs_kind",
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
        "title": document.title,
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
        title=cast(str, record["title"]),
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
            "title": statement.excluded.title,
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


async def insert_document(connection: AsyncConnection, document: Document) -> bool:
    """Store a new Document, or return False if its source item is already stored.

    Unlike `upsert_document`, this never overwrites a row, so a run that lost
    a race to create the Document can decide again against the stored one.
    """
    statement = (
        insert(documents)
        .values(as_row(document))
        .on_conflict_do_nothing(constraint="uq_documents_source_external")
        .returning(documents.c.document_id)
    )
    return (await connection.execute(statement)).scalar_one_or_none() is not None


async def load_document(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    external_id: str,
    for_update: bool = False,
) -> Document | None:
    """Read the stored Document for one source item, if it exists.

    With `for_update`, the row stays locked until the transaction ends, so a
    concurrent writer waits instead of deciding from a stale copy.
    """
    statement = sa.select(documents).where(
        documents.c.source_id == source_id.value,
        documents.c.external_id == external_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return as_document(dict(record))


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


def eligible_chunks_predicate(context: AuthorizationContext) -> sa.ColumnElement[bool]:
    """The condition a Chunk must meet before retrieval may rank it.

    Its Document is available, and at least one of its Access Grants names
    everyone, the context's User, or one of the context's Groups. The
    statement using it must join `chunks` to `documents`. Every value is a
    bound parameter, never text spliced into the SQL.
    """
    group_ids = sorted(group_id.value for group_id in context.group_ids)
    granted = sa.exists().where(
        access_grants.c.document_id == documents.c.document_id,
        sa.or_(
            access_grants.c.subject_kind == SubjectKind.PUBLIC.value,
            sa.and_(
                access_grants.c.subject_kind == SubjectKind.USER.value,
                access_grants.c.subject_id == context.user_id.value,
            ),
            sa.and_(
                access_grants.c.subject_kind == SubjectKind.GROUP.value,
                # One array parameter, however many Groups the User has.
                access_grants.c.subject_id
                == sa.any_(
                    sa.bindparam(
                        "group_ids", group_ids, type_=ARRAY(sa.Uuid()), unique=True
                    )
                ),
            ),
        ),
    )
    return sa.and_(
        documents.c.availability == DocumentAvailability.AVAILABLE.value,
        granted,
    )


def metadata_predicate(
    conditions: Sequence[MetadataCondition],
) -> sa.ColumnElement[bool]:
    """The condition a Chunk's Document must meet for every given filter.

    Each allowed field maps to one fixed SQL condition here, and every value
    is a bound parameter. With no conditions it is simply true. The
    statement using it must join `chunks` to `documents`. A Document with no
    metadata row matches no media type, language, or modified-since
    condition; a Source condition still matches it.
    """
    return sa.and_(sa.true(), *(_condition_clause(c) for c in conditions))


def _condition_clause(condition: MetadataCondition) -> sa.ColumnElement[bool]:
    match condition.field:
        case FilterField.SOURCE:
            source_ids = cast(frozenset[SourceId], condition.value)
            return documents.c.source_id == _any_of(
                "source_ids", sorted(s.value for s in source_ids), sa.Uuid()
            )
        case FilterField.MEDIA_TYPE:
            media_types = cast(frozenset[str], condition.value)
            return _described(
                document_metadata.c.media_type
                == _any_of("media_types", sorted(media_types), sa.Text())
            )
        case FilterField.LANGUAGE:
            languages = cast(frozenset[Language], condition.value)
            return _described(
                document_metadata.c.language
                == _any_of(
                    "languages", sorted(lang.value for lang in languages), sa.Text()
                )
            )
        case FilterField.MODIFIED_AT:
            since = cast(datetime, condition.value)
            # NULL >= since is not true, so an unknown time never matches.
            return _described(document_metadata.c.modified_at >= since)
        case _:
            assert_never(condition.field)


def _any_of(
    name: str, values: Sequence[object], item_type: sa.types.TypeEngine[Any]
) -> sa.ColumnElement[Any]:
    # One array parameter, however many values; unique keeps names apart.
    return sa.any_(
        sa.bindparam(name, list(values), type_=ARRAY(item_type), unique=True)
    )


def _described(clause: sa.ColumnElement[bool]) -> sa.ColumnElement[bool]:
    return sa.exists().where(
        document_metadata.c.document_id == documents.c.document_id, clause
    )


async def upsert_document_metadata(
    connection: AsyncConnection, described: DocumentMetadata
) -> None:
    """Store a Document's metadata, replacing what was stored before."""
    values = {
        "document_id": described.document_id.value,
        "media_type": described.media_type,
        "language": described.language.value,
        "modified_at": described.modified_at,
        "observed_at": described.observed_at,
    }
    statement = insert(document_metadata).values(values)
    await connection.execute(
        statement.on_conflict_do_update(
            index_elements=[document_metadata.c.document_id],
            set_={
                "media_type": statement.excluded.media_type,
                "language": statement.excluded.language,
                "modified_at": statement.excluded.modified_at,
                "observed_at": statement.excluded.observed_at,
            },
        )
    )


async def load_document_metadata(
    connection: AsyncConnection, *, document_id: DocumentId
) -> DocumentMetadata | None:
    """Read a Document's stored metadata, or None if it has none yet."""
    record = (
        (
            await connection.execute(
                sa.select(document_metadata).where(
                    document_metadata.c.document_id == document_id.value
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if record is None:
        return None
    try:
        language = Language(cast(str, record["language"]))
    except ValueError:
        raise InvalidDocument("stored language is not a known value") from None
    return DocumentMetadata(
        document_id=document_id,
        media_type=cast(str, record["media_type"]),
        language=language,
        modified_at=cast("datetime | None", record["modified_at"]),
        observed_at=cast(datetime, record["observed_at"]),
    )


async def delete_chunks(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
) -> int:
    """Delete every stored Chunk of a Document; return how many were deleted."""
    result = await connection.execute(
        sa.delete(chunks).where(chunks.c.document_id == document_id.value)
    )
    return result.rowcount


async def load_embedding_space_id(
    connection: AsyncConnection,
    identity: EmbeddingModelIdentity,
) -> EmbeddingSpaceId | None:
    """Return the Embedding Space that holds this model identity's vectors.

    The space's row stays share-locked until the transaction ends, so it
    cannot be deleted or changed while vectors are written into it.
    """
    space_id = await connection.scalar(
        sa.select(embedding_spaces.c.space_id)
        .where(
            embedding_spaces.c.provider == identity.provider,
            embedding_spaces.c.model == identity.model,
            embedding_spaces.c.dimensions == identity.dimensions,
        )
        .with_for_update(read=True)
    )
    return None if space_id is None else EmbeddingSpaceId(cast(UUID, space_id))


async def load_current_embedding_space_id(
    connection: AsyncConnection,
    identity: EmbeddingModelIdentity,
) -> EmbeddingSpaceId | None:
    """Return the current Embedding Space if it holds this model identity."""
    space_id = await connection.scalar(
        sa.select(embedding_spaces.c.space_id).where(
            embedding_spaces.c.provider == identity.provider,
            embedding_spaces.c.model == identity.model,
            embedding_spaces.c.dimensions == identity.dimensions,
            embedding_spaces.c.is_current,
        )
    )
    return None if space_id is None else EmbeddingSpaceId(cast(UUID, space_id))


async def ensure_index_configuration(
    connection: AsyncConnection,
    configuration: IndexConfiguration,
    *,
    created_at: datetime,
) -> IndexConfigurationId:
    """Return the identity of this configuration, storing it if it is new.

    Equal settings always return the identity stored first; any changed
    setting gives a new fingerprint and so a new row. Two callers storing the
    same new configuration at once get one row: the second insert does
    nothing and reads the first one's row. Raises
    `IndexConfigurationConflict` if a stored row has the fingerprint but not
    the settings, which would mean the row was edited. The configuration is
    checked again first, so one altered after it was made is refused.
    """
    configuration = configuration.checked()
    fingerprint = configuration.fingerprint
    settings = configuration.settings()
    inserted = await connection.scalar(
        insert(index_configurations)
        .values(
            configuration_id=IndexConfigurationId.new().value,
            fingerprint=fingerprint,
            settings=settings,
            created_at=created_at,
        )
        .on_conflict_do_nothing(index_elements=[index_configurations.c.fingerprint])
        .returning(index_configurations.c.configuration_id)
    )
    if inserted is not None:
        return IndexConfigurationId(cast(UUID, inserted))
    stored = (
        await connection.execute(
            sa.select(
                index_configurations.c.configuration_id,
                index_configurations.c.settings,
            ).where(index_configurations.c.fingerprint == fingerprint)
        )
    ).one()
    # Hashing the stored JSON also catches 8.0 for 8 or true for 1, which
    # compare equal in Python but are other settings.
    if (
        stored.settings != settings
        or settings_fingerprint(stored.settings) != fingerprint
    ):
        raise IndexConfigurationConflict(
            "a stored configuration has this fingerprint but other settings"
        )
    return IndexConfigurationId(cast(UUID, stored.configuration_id))


async def insert_embedding_space(
    connection: AsyncConnection,
    *,
    space_id: EmbeddingSpaceId,
    identity: EmbeddingModelIdentity,
    is_current: bool,
    created_at: datetime,
) -> None:
    """Store a new Embedding Space for one model identity."""
    await connection.execute(
        sa.insert(embedding_spaces).values(
            space_id=space_id.value,
            provider=identity.provider,
            model=identity.model,
            dimensions=identity.dimensions,
            is_current=is_current,
            created_at=created_at,
        )
    )


def _space_from(record: Mapping[str, object]) -> EmbeddingSpace:
    return EmbeddingSpace(
        space_id=EmbeddingSpaceId(cast(UUID, record["space_id"])),
        identity=EmbeddingModelIdentity(
            provider=cast(str, record["provider"]),
            model=cast(str, record["model"]),
            dimensions=cast(int, record["dimensions"]),
        ),
        is_current=cast(bool, record["is_current"]),
    )


async def load_embedding_space(
    connection: AsyncConnection,
    space_id: EmbeddingSpaceId,
    *,
    for_update: bool = False,
) -> EmbeddingSpace | None:
    """Read one space; `for_update` locks its row until the commit.

    The lock waits for every publication holding the row `FOR SHARE` and
    keeps new ones out, so no vector is written into the space meanwhile.
    """
    statement = sa.select(embedding_spaces).where(
        embedding_spaces.c.space_id == space_id.value
    )
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    return None if record is None else _space_from(dict(record))


async def load_current_embedding_space(
    connection: AsyncConnection, *, for_update: bool = False
) -> EmbeddingSpace | None:
    """Read the space search reads now, if there is one."""
    statement = sa.select(embedding_spaces).where(embedding_spaces.c.is_current)
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    return None if record is None else _space_from(dict(record))


async def space_accepts_writes(
    connection: AsyncConnection, space_id: EmbeddingSpaceId
) -> bool:
    """Whether publication may still write vectors into this space.

    Only the current space, and the target of a migration still
    dual-writing, are ever read again; vectors written anywhere else would
    be lost.
    """
    current = sa.exists().where(
        embedding_spaces.c.space_id == space_id.value, embedding_spaces.c.is_current
    )
    building = sa.exists().where(
        embedding_migrations.c.target_space_id == space_id.value,
        embedding_migrations.c.state == MigrationState.DUAL_WRITING.value,
    )
    return bool(await connection.scalar(sa.select(sa.or_(current, building))))


async def make_embedding_space_current(
    connection: AsyncConnection,
    *,
    retired: EmbeddingSpaceId,
    current: EmbeddingSpaceId,
) -> None:
    """Move "current" from one space to another in the same transaction.

    The unique index on `is_current` is checked row by row, so the old space
    is cleared first. Readers see the old space until the commit and the new
    one after it, never neither.
    """
    await connection.execute(
        sa.update(embedding_spaces)
        .where(embedding_spaces.c.space_id == retired.value)
        .values(is_current=False)
    )
    await connection.execute(
        sa.update(embedding_spaces)
        .where(embedding_spaces.c.space_id == current.value)
        .values(is_current=True)
    )


async def delete_embedding_space(
    connection: AsyncConnection, space_id: EmbeddingSpaceId
) -> None:
    """Delete a space; its vectors go with it."""
    await connection.execute(
        sa.delete(embedding_spaces).where(embedding_spaces.c.space_id == space_id.value)
    )


def _missing_from_target(
    source: EmbeddingSpaceId, target: EmbeddingSpaceId
) -> sa.ColumnElement[bool]:
    """A Chunk of an available Document with a source vector and no target one."""
    source_vector = sa.exists().where(
        chunk_embeddings.c.chunk_id == chunks.c.chunk_id,
        chunk_embeddings.c.space_id == source.value,
    )
    target_vector = sa.exists().where(
        chunk_embeddings.c.chunk_id == chunks.c.chunk_id,
        chunk_embeddings.c.space_id == target.value,
    )
    return sa.and_(
        documents.c.availability == DocumentAvailability.AVAILABLE.value,
        source_vector,
        ~target_vector,
    )


async def count_chunks_missing_from_target(
    connection: AsyncConnection,
    *,
    source: EmbeddingSpaceId,
    target: EmbeddingSpaceId,
) -> int:
    """Count the searchable Chunks the target space has no vector for yet."""
    count = await connection.scalar(
        sa.select(sa.func.count())
        .select_from(chunks)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(_missing_from_target(source, target))
    )
    return int(count or 0)


async def load_documents_missing_from_target(
    connection: AsyncConnection,
    *,
    source: EmbeddingSpaceId,
    target: EmbeddingSpaceId,
    after: DocumentId | None,
    limit: int,
) -> tuple[DocumentId, ...]:
    """Return up to `limit` Documents with a Chunk the target still lacks.

    Only Documents ordered after `after` count, so a caller can move past a
    Document that keeps failing instead of meeting it first every time.
    """
    after_clause = sa.true() if after is None else chunks.c.document_id > after.value
    document_ids = await connection.scalars(
        sa.select(chunks.c.document_id)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(_missing_from_target(source, target), after_clause)
        .group_by(chunks.c.document_id)
        .order_by(chunks.c.document_id)
        .limit(limit)
    )
    return tuple(DocumentId(cast(UUID, value)) for value in document_ids)


def _migration_row(migration: EmbeddingMigration) -> dict[str, object]:
    return {
        "migration_id": migration.migration_id.value,
        "source_space_id": _optional_id(migration.source_space_id),
        "target_space_id": _optional_id(migration.target_space_id),
        "target_configuration_id": migration.target_configuration_id.value,
        "state": migration.state.value,
        "started_at": migration.started_at,
        "updated_at": migration.updated_at,
    }


def _optional_id(identifier: EmbeddingSpaceId | None) -> UUID | None:
    return None if identifier is None else identifier.value


def _migration_from(record: Mapping[str, object]) -> EmbeddingMigration:
    try:
        state = MigrationState(cast(str, record["state"]))
    except ValueError:
        raise EmbeddingMigrationError("stored state is not a known value") from None
    source = cast("UUID | None", record["source_space_id"])
    target = cast("UUID | None", record["target_space_id"])
    return EmbeddingMigration(
        migration_id=EmbeddingMigrationId(cast(UUID, record["migration_id"])),
        source_space_id=None if source is None else EmbeddingSpaceId(source),
        target_space_id=None if target is None else EmbeddingSpaceId(target),
        target_configuration_id=IndexConfigurationId(
            cast(UUID, record["target_configuration_id"])
        ),
        state=state,
        started_at=cast(datetime, record["started_at"]),
        updated_at=cast(datetime, record["updated_at"]),
    )


async def insert_embedding_migration(
    connection: AsyncConnection, migration: EmbeddingMigration
) -> None:
    """Store a new Embedding Migration."""
    await connection.execute(
        sa.insert(embedding_migrations).values(_migration_row(migration))
    )


async def load_embedding_migration(
    connection: AsyncConnection,
    migration_id: EmbeddingMigrationId,
    *,
    for_update: bool = False,
) -> EmbeddingMigration | None:
    """Read one migration; `for_update` locks its row until the commit."""
    statement = sa.select(embedding_migrations).where(
        embedding_migrations.c.migration_id == migration_id.value
    )
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    return None if record is None else _migration_from(dict(record))


async def load_open_embedding_migration(
    connection: AsyncConnection,
) -> EmbeddingMigration | None:
    """Read the migration that still has a step to take, if there is one."""
    record = (
        (
            await connection.execute(
                sa.select(embedding_migrations)
                .where(embedding_migrations.c.state != MigrationState.CLEANED.value)
                .order_by(embedding_migrations.c.started_at)
                .limit(1)
            )
        )
        .mappings()
        .one_or_none()
    )
    return None if record is None else _migration_from(dict(record))


async def save_embedding_migration(
    connection: AsyncConnection, migration: EmbeddingMigration
) -> None:
    """Store a migration's new state and time."""
    await connection.execute(
        sa.update(embedding_migrations)
        .where(embedding_migrations.c.migration_id == migration.migration_id.value)
        .values(state=migration.state.value, updated_at=migration.updated_at)
    )


async def load_embedded_chunk_ids(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
    space_id: EmbeddingSpaceId,
) -> frozenset[ChunkId]:
    """Return the Chunks of a Document that already have a vector in the space."""
    statement = (
        sa.select(chunk_embeddings.c.chunk_id)
        .join(chunks, chunks.c.chunk_id == chunk_embeddings.c.chunk_id)
        .where(
            chunks.c.document_id == document_id.value,
            chunk_embeddings.c.space_id == space_id.value,
        )
    )
    chunk_ids = await connection.scalars(statement)
    return frozenset(ChunkId(cast(UUID, chunk_id)) for chunk_id in chunk_ids)


async def insert_chunk_embeddings(
    connection: AsyncConnection,
    *,
    space_id: EmbeddingSpaceId,
    dimensions: int,
    vectors: Mapping[ChunkId, Sequence[float]],
    created_at: datetime,
) -> None:
    """Store one vector per Chunk in the space, in one statement.

    Each vector is stored scaled to length 1 (see `unit_length`), so search
    distances stay right in pgvector's 32-bit floats; a vector with no
    direction raises `InvalidEmbedding` and nothing is stored. The database
    refuses a vector whose length is not the space's dimensions, and
    pgvector's type needs a list, so each vector is converted here.
    """
    if not vectors:
        return
    await connection.execute(
        sa.insert(chunk_embeddings),
        [
            {
                "chunk_id": chunk_id.value,
                "space_id": space_id.value,
                "dimensions": dimensions,
                "embedding": list(unit_length(vector)),
                "created_at": created_at,
            }
            for chunk_id, vector in vectors.items()
        ],
    )


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


async def load_job(
    connection: AsyncConnection,
    *,
    job_id: JobId,
    for_update: bool = False,
) -> Job | None:
    """Read a stored Job, if it exists, optionally locking its row."""
    statement = sa.select(jobs).where(jobs.c.job_id == job_id.value)
    if for_update:
        statement = statement.with_for_update()
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
