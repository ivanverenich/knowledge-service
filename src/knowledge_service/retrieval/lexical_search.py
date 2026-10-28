"""Find the Chunks whose words match an English query that one reader may see.

Every Chunk stores its English search terms in `chunks.search_en`: the
Document's title (weight A), the headings it sits under (B), and its text (C),
each reduced by PostgreSQL's `english` configuration to word stems without
stop words. The query goes through the same configuration, so "requesting
leave" matches "requests for leave".

What a caller can rely on:

- **Syntax.** The query is web-search text, read by `websearch_to_tsquery`:
  words must all match, `"quoted words"` must appear next to each other in
  that order, `or` between words accepts either, and `-word` excludes a word.
  Any other punctuation is ignored, so no text a user types can raise a
  syntax error. A query of only stop words or punctuation matches nothing.
  PostgreSQL still refuses a few queries with too many operators in a row,
  such as a line of 33 dashes; that raises `InvalidLexicalQuery`, and the
  caller's transaction stays usable.
- **Order.** Highest `ts_rank` first, so a match in a title outranks the same
  match in a heading, and a heading outranks the text; then by Chunk ID, so
  equal scores always come back in the same order.
- **Eligibility.** The same permission check as vector search sits in the
  same WHERE, so a Chunk the reader may not see is never ranked and never
  leaves the database.
- **Bounded.** At most `limit` matches, an int from 1 to `MAX_CANDIDATES`.
  Anything else, or a query that is not usable text, raises
  `InvalidLexicalQuery` before the database is used.
"""

from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import ChunkId, DocumentId
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES

# Long enough for any question, short enough to bound the work per query.
MAX_QUERY_CHARACTERS = 1_000

# Named, not taken from the server's `default_text_search_config`, and the
# same configuration the stored vectors were built with.
ENGLISH = sa.literal_column("'english'::regconfig", type_=REGCONFIG)


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


async def search_english_chunks(
    connection: AsyncConnection,
    *,
    text: str,
    context: AuthorizationContext,
    limit: int,
) -> tuple[LexicalMatch, ...]:
    """Return up to `limit` eligible Chunks matching `text`, best first.

    Raises `InvalidLexicalQuery` for unusable text or a bad limit, and for
    text PostgreSQL cannot turn into a query.
    """
    _check_text(text)
    _check_limit(limit)
    statement = english_search_statement(
        text=text,
        eligibility=eligible_chunks_predicate(context),
        limit=limit,
    )
    try:
        # A savepoint, so a refused query leaves the caller's transaction usable.
        async with connection.begin_nested():
            records = (await connection.execute(statement)).mappings().all()
    except DBAPIError as error:
        # `websearch_to_tsquery` overflows on long runs of operators, such as
        # a pasted line of dashes. The error repeats the query text, so it is
        # dropped.
        if "tsquery stack too small" in str(error.orig):
            raise InvalidLexicalQuery("query text has too many operators") from None
        raise
    return tuple(
        LexicalMatch(
            rank=index,
            chunk_id=ChunkId(cast(UUID, record["chunk_id"])),
            document_id=DocumentId(cast(UUID, record["document_id"])),
            score=cast(float, record["score"]),
        )
        for index, record in enumerate(records, start=1)
    )


def english_search_statement(
    *,
    text: str,
    eligibility: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    """Select the eligible Chunks whose English terms match the query text.

    The text is one bound parameter. `@@` on the stored vector is what the
    GIN index `ix_chunks_search_en` answers.
    """
    query = sa.func.websearch_to_tsquery(ENGLISH, sa.bindparam("query_text", text))
    score = sa.func.ts_rank(chunks.c.search_en, query)
    return (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            score.label("score"),
        )
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(chunks.c.search_en.bool_op("@@")(query), eligibility)
        .order_by(score.desc(), chunks.c.chunk_id)
        .limit(limit)
    )


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
