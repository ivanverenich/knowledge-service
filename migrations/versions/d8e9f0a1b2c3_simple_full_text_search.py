"""simple full text search

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-10-29 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c3"
down_revision: str | Sequence[str] | None = "c7d8e9f0a1b2"

# The Ukrainian baseline: the `simple` configuration only splits text into
# words and lowercases them. It has no stemming and no stop words, so a word
# matches only in the exact form it was written. Every Chunk gets these
# terms, whatever its language; choosing which terms to search is the
# query's job. NFC gives letters such as й and ї one form, matching the
# query, because Chunk text is stored as it arrived.
SEARCH_VECTOR_FUNCTION = """
CREATE FUNCTION simple_search_vector(title text, heading_path text, body text)
RETURNS tsvector
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN setweight(
        to_tsvector('simple'::regconfig, normalize(coalesce(title, ''), NFC)), 'A')
    || setweight(
        to_tsvector('simple'::regconfig, normalize(coalesce(heading_path, ''), NFC)),
        'B')
    || setweight(
        to_tsvector('simple'::regconfig, normalize(coalesce(body, ''), NFC)), 'C')
"""

# The same rule as the English Chunk trigger. No second Document trigger is
# needed: when a title changes, `documents_english_search` rewrites every
# Chunk of that Document, and each rewrite fires this trigger too. Like the
# English trigger, it locks the Document after the Chunk row on an update:
# a writer that updates a Chunk row while another transaction renames its
# Document can deadlock, so writers lock the Document first, as
# `reconcile_document` does.
CHUNK_TRIGGER = """
CREATE FUNCTION chunks_simple_search() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_simple := simple_search_vector(
        (SELECT title FROM documents
         WHERE document_id = NEW.document_id FOR SHARE),
        NEW.heading_path,
        NEW.text
    );
    RETURN NEW;
END
$$;
CREATE TRIGGER chunks_simple_search
BEFORE INSERT OR UPDATE ON chunks
FOR EACH ROW EXECUTE FUNCTION chunks_simple_search();
"""


def upgrade() -> None:
    """Give every Chunk weighted `simple` search terms and a GIN index."""
    op.execute(SEARCH_VECTOR_FUNCTION)
    op.add_column("chunks", sa.Column("search_simple", TSVECTOR(), nullable=True))
    op.execute(
        "UPDATE chunks SET search_simple ="
        " simple_search_vector(documents.title, chunks.heading_path, chunks.text)"
        " FROM documents WHERE documents.document_id = chunks.document_id"
    )
    op.alter_column("chunks", "search_simple", nullable=False)
    op.create_index(
        "ix_chunks_search_simple",
        "chunks",
        ["search_simple"],
        postgresql_using="gin",
        postgresql_with={"fastupdate": "off"},
    )
    op.execute(CHUNK_TRIGGER)


def downgrade() -> None:
    """Drop the `simple` search terms, their index, and their trigger."""
    op.execute("DROP TRIGGER chunks_simple_search ON chunks")
    op.execute("DROP FUNCTION chunks_simple_search()")
    op.drop_index("ix_chunks_search_simple", table_name="chunks")
    op.drop_column("chunks", "search_simple")
    op.execute("DROP FUNCTION simple_search_vector(text, text, text)")
