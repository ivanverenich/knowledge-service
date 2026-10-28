"""Unit tests pinning the declared pgvector schema to the migration's rules.

Alembic's drift check does not compare CHECK bodies, index predicates, or
vector dimensions, so these declarations could drift from the migration
unnoticed. The integration test pins what PostgreSQL stores; this test pins
what persistence.schema declares.
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from knowledge_service.persistence.schema import chunk_embeddings, embedding_spaces


def check_text(table: sa.Table, name: str) -> str:
    (constraint,) = [
        c
        for c in table.constraints
        if isinstance(c, sa.CheckConstraint) and c.name == name
    ]
    return str(constraint.sqltext)


def test_the_declared_checks_match_the_migration() -> None:
    assert (
        check_text(chunk_embeddings, "ck_chunk_embeddings_dimensions")
        == "vector_dims(embedding) = dimensions"
    )
    assert (
        check_text(embedding_spaces, "ck_embedding_spaces_dimensions")
        == "dimensions between 1 and 16000"
    )
    assert check_text(embedding_spaces, "ck_embedding_spaces_identity") == (
        "provider <> '' and provider = btrim(provider)"
        " and model <> '' and model = btrim(model)"
    )


def test_the_declared_current_index_matches_the_migration() -> None:
    (index,) = [
        i for i in embedding_spaces.indexes if i.name == "uq_embedding_spaces_current"
    ]

    assert index.unique
    assert [column.name for column in index.columns] == ["is_current"]
    assert str(index.dialect_options["postgresql"]["where"]) == "is_current"


def test_the_declared_vector_column_accepts_any_dimensions() -> None:
    embedding = chunk_embeddings.c.embedding.type

    assert isinstance(embedding, Vector)
    assert embedding.dim is None


def test_the_declared_space_key_carries_the_dimensions() -> None:
    (key,) = [
        k
        for k in chunk_embeddings.foreign_key_constraints
        if k.name == "fk_chunk_embeddings_space_dimensions"
    ]

    assert [column.name for column in key.columns] == ["space_id", "dimensions"]
    assert [element.target_fullname for element in key.elements] == [
        "embedding_spaces.space_id",
        "embedding_spaces.dimensions",
    ]
    assert key.ondelete == "CASCADE"
