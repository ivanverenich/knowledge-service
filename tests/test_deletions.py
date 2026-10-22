"""Unit tests for what retrieval may read once Documents are tombstoned."""

from uuid import UUID

from sqlalchemy.dialects import postgresql

from knowledge_service.access import Subject
from knowledge_service.persistence import searchable_chunks_statement
from knowledge_service.sources import SourceAccess
from knowledge_service.synchronization import source_readers


def test_searchable_chunks_belong_only_to_available_documents() -> None:
    statement = searchable_chunks_statement(UUID(int=1))
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )

    assert "documents.availability = 'available'" in sql


def test_only_public_items_get_readers_until_identity_mapping_exists() -> None:
    assert source_readers(SourceAccess(public=True)) == [Subject.public()]
    assert source_readers(SourceAccess(public=False, principals=("hr",))) == []
