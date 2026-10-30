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

Matches come back as `LexicalCandidate`s, with the same provenance and the
same guarantees as vector search; see `retrieval.candidates`. What a caller
can rely on besides:

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
- **Highlights.** Where the query's words occur in the Candidate's `text`,
  as character offsets, never as marked-up text. Words found only in the
  title or the headings have no highlight.
- **Bounded.** At most `limit` Candidates, an int from 1 to `MAX_CANDIDATES`.
  Anything else, or a query that is not usable text, raises
  `InvalidLexicalQuery` before the database is used.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import RowMapping
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
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

# `ts_headline` marks each matched word with these two control characters
# and keeps every other character of the text, so the marks give offsets.
_START = "\x02"
_STOP = "\x03"
_HEADLINE_OPTIONS = f"HighlightAll=true, StartSel={_START}, StopSel={_STOP}"


class LexicalSearchError(Exception):
    """Base error for a full-text search."""


class InvalidLexicalQuery(LexicalSearchError):
    """The query text or the limit cannot be searched; nothing was read."""


@dataclass(frozen=True, slots=True)
class Highlight:
    """Where one matched word, or a run of them, sits: `text[start:end]`."""

    start: int
    end: int


@dataclass(frozen=True, slots=True)
class LexicalCandidate:
    """One eligible Chunk whose words match the query, and where it came from.

    `rank` is its 1-based position in the result; `score` is PostgreSQL's
    `ts_rank`, where higher is better and only the order means anything.
    `highlights` are in text order and never overlap. The fields holding
    Document text are kept out of repr, so logging a Candidate never shows
    them.
    """

    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    score: float
    highlights: tuple[Highlight, ...]
    external_id: str = field(repr=False)
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_anchor: str | None = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class LexicalSearch:
    """The Candidates for one Question, and the route that chose how to match it.

    `truncated` is True only when more eligible Chunks match beyond `limit`.
    """

    route: LexicalRoute
    limit: int
    candidates: tuple[LexicalCandidate, ...]
    truncated: bool


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
    candidates, truncated = await _search(
        connection,
        route.configurations,
        text=text,
        context=context,
        limit=limit,
    )
    return LexicalSearch(
        route=route, limit=limit, candidates=candidates, truncated=truncated
    )


async def search_english_chunks(
    connection: AsyncConnection,
    *,
    text: str,
    context: AuthorizationContext,
    limit: int,
) -> tuple[LexicalCandidate, ...]:
    """Return up to `limit` eligible Chunks matching English `text`, best first.

    Raises `InvalidLexicalQuery` for unusable text or a bad limit, and for
    text PostgreSQL cannot turn into a query.
    """
    candidates, _ = await _search(
        connection,
        (LexicalConfiguration.ENGLISH,),
        text=text,
        context=context,
        limit=limit,
    )
    return candidates


async def search_ukrainian_chunks(
    connection: AsyncConnection,
    *,
    text: str,
    context: AuthorizationContext,
    limit: int,
) -> tuple[LexicalCandidate, ...]:
    """Return up to `limit` eligible Chunks containing the exact words of `text`.

    Words match after lowercasing only, in the form they were typed. Raises
    `InvalidLexicalQuery` like `search_english_chunks`.
    """
    candidates, _ = await _search(
        connection,
        (LexicalConfiguration.SIMPLE,),
        text=text,
        context=context,
        limit=limit,
    )
    return candidates


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

    The ranked, limited rows are chosen first; only those are marked by
    `ts_headline`, once for each configuration that matched them.
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
    queries: list[tuple[sa.ColumnElement[Any], sa.ColumnElement[Any]]] = []
    conditions: list[sa.ColumnElement[bool]] = []
    ranks: list[sa.ColumnElement[Any]] = []
    for configuration in configurations:
        terms, regconfig = _TERMS[configuration]
        query = sa.func.websearch_to_tsquery(regconfig, query_text)
        queries.append((regconfig, query))
        conditions.append(terms.bool_op("@@")(query))
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
    ranked = (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            documents.c.source_id,
            chunks.c.content_version,
            chunks.c.ordinal,
            score.label("score"),
            documents.c.external_id,
            documents.c.title,
            chunks.c.heading_path,
            chunks.c.source_anchor,
            chunks.c.text,
            *(
                condition.label(f"matched_{index}")
                for index, condition in enumerate(conditions)
            ),
        )
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(matched, eligibility)
        .order_by(score.desc(), chunks.c.chunk_id)
        .limit(limit)
        .subquery("ranked")
    )
    options = sa.literal(_HEADLINE_OPTIONS, type_=sa.Text())
    # NULL where this configuration did not match the row.
    marked = (
        sa.case(
            (
                ranked.c[f"matched_{index}"],
                sa.func.ts_headline(regconfig, ranked.c.text, query, options),
            )
        ).label(f"marked_{index}")
        for index, (regconfig, query) in enumerate(queries)
    )
    return sa.select(
        ranked.c.chunk_id,
        ranked.c.document_id,
        ranked.c.source_id,
        ranked.c.content_version,
        ranked.c.ordinal,
        ranked.c.score,
        ranked.c.external_id,
        ranked.c.title,
        ranked.c.heading_path,
        ranked.c.source_anchor,
        ranked.c.text,
        *marked,
    ).order_by(ranked.c.score.desc(), ranked.c.chunk_id)


async def _search(
    connection: AsyncConnection,
    configurations: Sequence[LexicalConfiguration],
    *,
    text: str,
    context: AuthorizationContext,
    limit: int,
) -> tuple[tuple[LexicalCandidate, ...], bool]:
    """Return up to `limit` Candidates, and whether more eligible Chunks match."""
    _check_text(text)
    _check_limit(limit)
    statement = lexical_search_statement(
        configurations,
        # One form for letters such as й and ї, which can also arrive as a
        # letter plus a combining mark; the stored terms use the same form.
        text=unicodedata.normalize("NFC", text),
        eligibility=eligible_chunks_predicate(context),
        # One row more than asked shows whether more eligible Chunks exist.
        limit=limit + 1,
    )
    records = await _execute(connection, statement)
    candidates = tuple(
        _candidate_from(record, rank=index, marks=len(configurations))
        for index, record in enumerate(records[:limit], start=1)
    )
    return candidates, len(records) > limit


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


def _candidate_from(record: RowMapping, *, rank: int, marks: int) -> LexicalCandidate:
    text = cast(str, record["text"])
    return LexicalCandidate(
        rank=rank,
        chunk_id=ChunkId(cast(UUID, record["chunk_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        source_id=SourceId(cast(UUID, record["source_id"])),
        content_version=ContentVersion(cast(int, record["content_version"])),
        ordinal=cast(int, record["ordinal"]),
        score=cast(float, record["score"]),
        highlights=_highlights(
            text,
            [cast("str | None", record[f"marked_{index}"]) for index in range(marks)],
        ),
        external_id=cast(str, record["external_id"]),
        title=cast(str, record["title"]),
        heading_path=cast("str | None", record["heading_path"]),
        source_anchor=cast("str | None", record["source_anchor"]),
        text=text,
    )


def _highlights(text: str, marked: Sequence[str | None]) -> tuple[Highlight, ...]:
    """Merge the words each configuration marked into ordered, separate spans.

    Returns no highlights when a marked text is not exactly `text` plus
    marks, for example because `text` itself holds a mark character: an
    offset that points at the wrong words is worse than none.
    """
    spans: list[tuple[int, int]] = []
    for one in marked:
        if one is None:
            continue
        found = _spans(text, one)
        if found is None:
            return ()
        spans.extend(found)
    merged: list[Highlight] = []
    for start, end in sorted(spans):
        # Touching spans join, so "E-1042", marked as "E" and "-1042", is one.
        if merged and start <= merged[-1].end:
            if end > merged[-1].end:
                merged[-1] = Highlight(start=merged[-1].start, end=end)
        else:
            merged.append(Highlight(start=start, end=end))
    return tuple(merged)


def _spans(text: str, marked: str) -> list[tuple[int, int]] | None:
    spans: list[tuple[int, int]] = []
    position = 0
    start: int | None = None
    for character in marked:
        if character == _START:
            if start is not None:
                return None
            start = position
        elif character == _STOP:
            if start is None:
                return None
            if position > start:
                spans.append((start, position))
            start = None
        elif position < len(text) and text[position] == character:
            position += 1
        else:
            return None
    if start is not None or position != len(text):
        return None
    return spans


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
