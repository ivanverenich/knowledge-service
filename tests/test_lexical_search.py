"""Checks on full-text search that need no database."""

from collections.abc import Awaitable, Callable
from typing import cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import UserId
from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_routing import LexicalConfiguration
from knowledge_service.retrieval.lexical_search import (
    MAX_QUERY_CHARACTERS,
    InvalidLexicalQuery,
    english_search_statement,
    lexical_search_statement,
    search_english_chunks,
    search_lexical_chunks,
    search_ukrainian_chunks,
    ukrainian_search_statement,
)
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES

CONTEXT = AuthorizationContext(user_id=UserId(UUID(int=1)))
MARKER = "zebramarker"


@pytest.mark.parametrize(
    "search", [search_english_chunks, search_ukrainian_chunks, search_lexical_chunks]
)
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
    search: Callable[..., Awaitable[object]], text: object, limit: object
) -> None:
    # Any use of the connection would fail: the check must come first.
    unused = cast(AsyncConnection, object())

    with pytest.raises(InvalidLexicalQuery):
        await search(
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


def test_the_ukrainian_query_is_a_bound_parameter_with_the_simple_configuration() -> (
    None
):
    statement = ukrainian_search_statement(
        text=f"{MARKER}' OR true --", eligibility=sa.true(), limit=5
    )

    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert MARKER not in sql
    assert f"{MARKER}' OR true --" in compiled.params.values()
    assert "websearch_to_tsquery('simple'::regconfig" in sql
    assert "chunks.search_simple @@" in sql


@pytest.mark.parametrize("language", ["en", "fr", 1, Language])
async def test_a_stated_language_that_is_not_a_language_is_refused(
    language: object,
) -> None:
    unused = cast(AsyncConnection, object())

    with pytest.raises(InvalidLexicalQuery):
        await search_lexical_chunks(
            unused,
            text="leave",
            context=CONTEXT,
            limit=5,
            language=cast(Language, language),
        )


def test_both_configurations_share_one_bound_parameter() -> None:
    statement = lexical_search_statement(
        (LexicalConfiguration.ENGLISH, LexicalConfiguration.SIMPLE),
        text=f"{MARKER}' OR true --",
        eligibility=sa.true(),
        limit=5,
    )

    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert MARKER not in sql
    assert list(compiled.params.values()).count(f"{MARKER}' OR true --") == 1
    assert "chunks.search_en @@ websearch_to_tsquery('english'::regconfig" in sql
    assert "chunks.search_simple @@ websearch_to_tsquery('simple'::regconfig" in sql
    assert " OR " in sql
    # A rank counts only where its own configuration matches.
    assert "greatest(CASE WHEN" in sql


@pytest.mark.parametrize(
    "configurations",
    [
        (),
        (LexicalConfiguration.ENGLISH, LexicalConfiguration.ENGLISH),
        ("english",),
        (5,),
    ],
)
def test_a_statement_needs_distinct_configurations(configurations: object) -> None:
    with pytest.raises(InvalidLexicalQuery):
        lexical_search_statement(
            cast(tuple[LexicalConfiguration, ...], configurations),
            text="leave",
            eligibility=sa.true(),
            limit=5,
        )
