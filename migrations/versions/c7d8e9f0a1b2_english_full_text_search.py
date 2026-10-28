"""english full text search

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-10-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "b6c7d8e9f0a1"

# The one place that says how a Chunk becomes English search terms. The
# configuration is named, never taken from the server's default, so the
# same text always gives the same terms.
SEARCH_VECTOR_FUNCTION = """
CREATE FUNCTION english_search_vector(title text, heading_path text, body text)
RETURNS tsvector
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A')
    || setweight(to_tsvector('english'::regconfig, coalesce(heading_path, '')), 'B')
    || setweight(to_tsvector('english'::regconfig, coalesce(body, '')), 'C')
"""

# A Chunk row holds no title, so a trigger reads it from the Document. FOR
# SHARE waits for a title change still in progress, so a Chunk written at the
# same time never keeps the old title. It runs on every insert and update,
# so no write can leave `search_en` different from what it computes.
CHUNK_TRIGGER = """
CREATE FUNCTION chunks_english_search() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_en := english_search_vector(
        (SELECT title FROM documents
         WHERE document_id = NEW.document_id FOR SHARE),
        NEW.heading_path,
        NEW.text
    );
    RETURN NEW;
END
$$;
CREATE TRIGGER chunks_english_search
BEFORE INSERT OR UPDATE ON chunks
FOR EACH ROW EXECUTE FUNCTION chunks_english_search();
"""

# A title-only change rewrites no Chunk, so the Document refreshes them.
DOCUMENT_TRIGGER = """
CREATE FUNCTION documents_english_search() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE chunks
    SET search_en = english_search_vector(NEW.title, heading_path, text)
    WHERE document_id = NEW.document_id;
    RETURN NULL;
END
$$;
CREATE TRIGGER documents_english_search
AFTER UPDATE OF title ON documents
FOR EACH ROW WHEN (OLD.title IS DISTINCT FROM NEW.title)
EXECUTE FUNCTION documents_english_search();
"""


def upgrade() -> None:
    """Give every Chunk a weighted English search vector and a GIN index."""
    op.execute(SEARCH_VECTOR_FUNCTION)
    op.add_column("chunks", sa.Column("search_en", TSVECTOR(), nullable=True))
    op.execute(
        "UPDATE chunks SET search_en ="
        " english_search_vector(documents.title, chunks.heading_path, chunks.text)"
        " FROM documents WHERE documents.document_id = chunks.document_id"
    )
    op.alter_column("chunks", "search_en", nullable=False)
    op.create_index(
        "ix_chunks_search_en",
        "chunks",
        ["search_en"],
        postgresql_using="gin",
        postgresql_with={"fastupdate": "off"},
    )
    op.execute(CHUNK_TRIGGER)
    op.execute(DOCUMENT_TRIGGER)


def downgrade() -> None:
    """Drop the English search vectors, their index, and their triggers."""
    op.execute("DROP TRIGGER documents_english_search ON documents")
    op.execute("DROP FUNCTION documents_english_search()")
    op.execute("DROP TRIGGER chunks_english_search ON chunks")
    op.execute("DROP FUNCTION chunks_english_search()")
    op.drop_index("ix_chunks_search_en", table_name="chunks")
    op.drop_column("chunks", "search_en")
    op.execute("DROP FUNCTION english_search_vector(text, text, text)")
