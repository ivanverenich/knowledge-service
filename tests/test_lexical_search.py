"""Checks on English full-text search that need no database."""

from typing import cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import UserId
from knowledge_service.retrieval.lexical_search import (
    MAX_QUERY_CHARACTERS,
    InvalidLexicalQuery,
    english_search_statement,
    search_english_chunks,
)
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES

CONTEXT = AuthorizationContext(user_id=UserId(UUID(int=1)))
MARKER = "zebramarker"


@pytest.mark.parametrize(
    ("text", "limit"),
    [
        ("", 5),
        ("   \n\t", 5),
        (None, 5),
        (b"leave", 5),
        (["leave"], 5),
        ("x" * (MAX_QUERY_CHARACTERS + 1), 5),
        ("paid\x00leave", 5),
        ("paid \ud800 leave", 5),
        ("leave", 0),
        ("leave", MAX_CANDIDATES + 1),
        ("leave", True),
        ("leave", 2.0),
        ("leave", "5"),
    ],
)
async def test_bad_text_or_limit_is_refused_before_the_database(
    text: object, limit: object
) -> None:
    # Any use of the connection would fail: the check must come first.
    unused = cast(AsyncConnection, object())

    with pytest.raises(InvalidLexicalQuery):
        await search_english_chunks(
            unused,
            text=cast(str, text),
            context=CONTEXT,
            limit=cast(int, limit),
        )


def test_the_longest_allowed_text_is_accepted() -> None:
    statement = english_search_statement(
        text="x" * MAX_QUERY_CHARACTERS, eligibility=sa.true(), limit=MAX_CANDIDATES
    )

    assert statement is not None


def test_the_query_text_is_a_bound_parameter_with_a_named_configuration() -> None:
    statement = english_search_statement(
        text=f"{MARKER}' OR true --", eligibility=sa.true(), limit=5
    )

    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert MARKER not in sql
    assert f"{MARKER}' OR true --" in compiled.params.values()
    assert "websearch_to_tsquery('english'::regconfig" in sql
    assert "chunks.search_en @@" in sql
