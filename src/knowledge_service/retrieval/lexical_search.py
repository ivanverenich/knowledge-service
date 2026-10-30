"""Find the Chunks whose words match a query that one reader may see.

Every Chunk stores two sets of search terms, each from the Document's title
(weight A), the headings it sits under (B), and its text (C):

- `chunks.search_en`, built with PostgreSQL's `english` configuration: word
  stems without stop words, so "requesting leave" matches "requests for
  leave". `search_english_chunks` reads it.
- `chunks.search_simple`, built with the `simple` configuration: every word
  lowercased, with no stemming and no stop words. `search_ukrainian_chunks`
  reads it. This is only a baseline for Ukrainian: "відпустка" matches
  "Відпустка" but not "відпустку" or "відпустки", an apostrophe splits
  "сім'я" into "сім" and "я", and a common word such as "на" matches almost
  every Chunk.

The query goes through the same configuration as the terms it searches.
`search_lexical_chunks` chooses the configuration for each Question with
`route_lexical_question`, from the language the caller states, the
alphabet of the Question, and whether English keeps any of its words; when
these do not agree on one configuration, it matches both in one statement
and keeps a Chunk that either finds.

What a caller can rely on:

- **Syntax.** The query is web-search text, read by `websearch_to_tsquery`:
  words must all match, `"quoted words"` must appear next to each other in
  that order, `or` between words accepts either, and `-word` excludes a word.
  Any other punctuation is ignored, so no text a user types can raise a
  syntax error. A query of only stop words or punctuation matches nothing.
  PostgreSQL still refuses a few queries with too many operators in a row,
  such as a line of 33 dashes; that raises `InvalidLexicalQuery`, and the
  caller's transaction stays usable.
- **Order.** Highest `ts_rank` first, so a match in a title outranks the
  same match in a heading, and a heading outranks the text; then by Chunk
  ID, so equal scores always come back in the same order. With two
  configurations, a Chunk's score is the higher rank among those that match.
- **Eligibility.** The same permission check as vector search sits in the
  same WHERE, so a Chunk the reader may not see is never ranked and never
  leaves the database.
- **Bounded.** At most `limit` matches, an int from 1 to `MAX_CANDIDATES`.
  Anything else, or a query that is not usable text, raises
  `InvalidLexicalQuery` before the database is used.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import RowMapping
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import ChunkId, DocumentId
from knowledge_service.language import Language
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.retrieval.lexical_routing import (
    LexicalConfiguration,
    LexicalRoute,
    route_lexical_question,
)
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES

# Long enough for any question, short enough to bound the work per query.
MAX_QUERY_CHARACTERS = 1_000

# Named, not taken from the server's `default_text_search_config`, and the
# same configurations the stored vectors were built with.
ENGLISH = sa.literal_column("'english'::regconfig", type_=REGCONFIG)
SIMPLE = sa.literal_column("'simple'::regconfig", type_=REGCONFIG)

# The stored words of each configuration, and the configuration to read a
# query with.
_TERMS: dict[LexicalConfiguration, tuple[sa.Column[Any], sa.ColumnElement[Any]]] = {
    LexicalConfiguration.ENGLISH: (chunks.c.search_en, ENGLISH),
    LexicalConfiguration.SIMPLE: (chunks.c.search_simple, SIMPLE),
}


class LexicalSearchError(Exception):
    """Base error for a full-text search."""


class InvalidLexicalQuery(LexicalSearchError):
    """The query text or the limit cannot be searched; nothing was read."""


@dataclass(frozen=True, slots=True)
class LexicalMatch:
    """One eligible Chunk whose words match the query, and how well.

    `rank` is its 1-based position in the result; `score` is PostgreSQL's
    `ts_rank`, where higher is better and only the order means anything.
    """

    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    score: float


@dataclass(frozen=True, slots=True)
class LexicalSearch:
    """The matches for one Question, and the route that chose how to match it."""

    route: LexicalRoute
    matches: tuple[LexicalMatch, ...]


async def search_lexical_chunks(
    connection: AsyncConnection,
    *,
    text: str,
    context: AuthorizationContext,
    limit: int,
    language: Language | None = None,
) -> LexicalSearch:
    """Return up to `limit` eligible Chunks matching `text`, best first.

    `language` is what the caller states about this Question, or None. It
    is one signal for the route, next to the Question's own alphabet; see
    `route_lexical_question`. Raises `InvalidLexicalQuery` like
    `search_english_chunks`, and for a `language` that is not a `Language`.
    """
    # Checked here as well as in `_search`, because the route reads the text.
    _check_text(text)
    _check_limit(limit)
    if language is not None and type(language) is not Language:
        raise InvalidLexicalQuery("language must be a Language or None")
    text = unicodedata.normalize("NFC", text)
    route = route_lexical_question(text, stated=language)
    if route.configurations == (LexicalConfiguration.ENGLISH,):
        # Only the database knows its English stop words.
        query = sa.func.websearch_to_tsquery(
            ENGLISH, sa.bindparam("query_text", text, type_=sa.Text())
        )
        records = await _execute(
            connection, sa.select(sa.func.numnode(query).label("words"))
        )
        if records[0]["words"] == 0:
            route = route_lexical_question(text, stated=language, english_words=False)
    matches = await _search(
        connection,
        route.configurations,
        text=text,
        context=context,
        limit=limit,
    )
    return LexicalSearch(route=route, matches=matches)


async def search_english_chunks(
    connection: AsyncConnection,
    *,
    text: str,
    context: AuthorizationContext,
    limit: int,
) -> tuple[LexicalMatch, ...]:
    """Return up to `limit` eligible Chunks matching English `text`, best first.

    Raises `InvalidLexicalQuery` for unusable text or a bad limit, and for
    text PostgreSQL cannot turn into a query.
    """
    return await _search(
        connection,
        (LexicalConfiguration.ENGLISH,),
        text=text,
        context=context,
        limit=limit,
    )


async def search_ukrainian_chunks(
    connection: AsyncConnection,
    *,
    text: str,
    context: AuthorizationContext,
    limit: int,
) -> tuple[LexicalMatch, ...]:
    """Return up to `limit` eligible Chunks containing the exact words of `text`.

    Words match after lowercasing only, in the form they were typed. Raises
    `InvalidLexicalQuery` like `search_english_chunks`.
    """
    return await _search(
        connection,
        (LexicalConfiguration.SIMPLE,),
        text=text,
        context=context,
        limit=limit,
    )


def english_search_statement(
    *,
    text: str,
    eligibility: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    """Select the eligible Chunks whose English terms match the query text.

    `@@` on the stored vector is what the GIN index `ix_chunks_search_en`
    answers.
    """
    return lexical_search_statement(
        (LexicalConfiguration.ENGLISH,),
        text=text,
        eligibility=eligibility,
        limit=limit,
    )


def ukrainian_search_statement(
    *,
    text: str,
    eligibility: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    """Select the eligible Chunks whose `simple` terms match the query text.

    `@@` on the stored vector is what the GIN index `ix_chunks_search_simple`
    answers.
    """
    return lexical_search_statement(
        (LexicalConfiguration.SIMPLE,),
        text=text,
        eligibility=eligibility,
        limit=limit,
    )


def lexical_search_statement(
    configurations: Sequence[LexicalConfiguration],
    *,
    text: str,
    eligibility: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    """Select the eligible Chunks whose words match the query text.

    With one configuration, the stored words of that configuration must
    match. With several, the words of any one may match, each read with its
    own configuration, and a Chunk scores the highest rank among those that
    match. Each `@@` is answered by that configuration's GIN index.
    """
    if (
        not configurations
        or any(type(item) is not LexicalConfiguration for item in configurations)
        or len(set(configurations)) != len(configurations)
    ):
        raise InvalidLexicalQuery(
            "configurations must be distinct LexicalConfiguration values"
        )
    # The text is one bound parameter, read with the configuration the
    # stored terms were built with.
    query_text = sa.bindparam("query_text", text, type_=sa.Text())
    conditions: list[sa.ColumnElement[bool]] = []
    ranks: list[sa.ColumnElement[Any]] = []
    for configuration in configurations:
        terms, regconfig = _TERMS[configuration]
        query = sa.func.websearch_to_tsquery(regconfig, query_text)
        condition = terms.bool_op("@@")(query)
        conditions.append(condition)
        ranks.append(sa.func.ts_rank(terms, query))
    if len(configurations) == 1:
        matched, score = conditions[0], ranks[0]
    else:
        matched = sa.or_(*conditions)
        # A rank counts only where its own query matches: `ts_rank` also
        # scores a Chunk that has only some of the words.
        score = sa.func.greatest(
            *(
                sa.case((condition, rank))
                for condition, rank in zip(conditions, ranks, strict=True)
            )
        )
    return (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            score.label("score"),
        )
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(matched, eligibility)
        .order_by(score.desc(), chunks.c.chunk_id)
        .limit(limit)
    )


async def _search(
    connection: AsyncConnection,
    configurations: Sequence[LexicalConfiguration],
    *,
    text: str,
    context: AuthorizationContext,
    limit: int,
) -> tuple[LexicalMatch, ...]:
    _check_text(text)
    _check_limit(limit)
    statement = lexical_search_statement(
        configurations,
        # One form for letters such as й and ї, which can also arrive as a
        # letter plus a combining mark; the stored terms use the same form.
        text=unicodedata.normalize("NFC", text),
        eligibility=eligible_chunks_predicate(context),
        limit=limit,
    )
    records = await _execute(connection, statement)
    return tuple(
        LexicalMatch(
            rank=index,
            chunk_id=ChunkId(cast(UUID, record["chunk_id"])),
            document_id=DocumentId(cast(UUID, record["document_id"])),
            score=cast(float, record["score"]),
        )
        for index, record in enumerate(records, start=1)
    )


async def _execute(
    connection: AsyncConnection, statement: sa.Select[*tuple[Any, ...]]
) -> Sequence[RowMapping]:
    records: Sequence[RowMapping] = ()
    refused = False
    try:
        # A savepoint, so a refused query leaves the caller's transaction usable.
        async with connection.begin_nested():
            records = (await connection.execute(statement)).mappings().all()
    except DBAPIError as error:
        # `websearch_to_tsquery` overflows on long runs of operators, such as
        # a pasted line of dashes.
        if "tsquery stack too small" not in str(error.orig):
            raise
        refused = True
    if refused:
        # Raised outside the handler, so no exception chain keeps the
        # database error, which repeats the query text.
        raise InvalidLexicalQuery("query text has too many operators")
    return records


def _check_text(text: object) -> None:
    if type(text) is not str:
        raise InvalidLexicalQuery("query text must be a string")
    if not text.strip():
        raise InvalidLexicalQuery("query text must not be blank")
    if len(text) > MAX_QUERY_CHARACTERS:
        raise InvalidLexicalQuery(
            f"query text must be at most {MAX_QUERY_CHARACTERS} characters"
        )
    # PostgreSQL text cannot hold NUL, and the driver cannot send a lone
    # surrogate; both would otherwise fail inside the database call.
    if "\x00" in text or not _encodable(text):
        raise InvalidLexicalQuery("query text must be valid text")


def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _check_limit(limit: object) -> None:
    # bool is an int, so `True` would otherwise pass as a limit of 1.
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise InvalidLexicalQuery(
            f"limit must be an int between 1 and {MAX_CANDIDATES}"
        )
