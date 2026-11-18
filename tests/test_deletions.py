"""Unit tests for what retrieval may read once Documents are tombstoned."""

from uuid import UUID

from sqlalchemy.dialects import postgresql

from knowledge_service.persistence.chunks import searchable_chunks_statement


def test_searchable_chunks_belong_only_to_available_documents() -> None:
    statement = searchable_chunks_statement(UUID(int=1))
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "documents.availability = 'available'" in sql
