"""The stored table shapes, as one SQLAlchemy `metadata` Alembic compares."""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

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
