"""Find the Chunks whose words match a query that one reader may see.

Every Chunk stores two sets of search terms, each from the Document's title
(weight A), the headings it sits under (B), and its text (C):

- `chunks.search_en`, built with PostgreSQL's `english` configuration: word
  stems without stop words, so "requesting leave" matches "requests for
  leave". `search_english_chunks` reads it.
- `chunks.search_simple`, built with the `simple` configuration: every word
  lowercased, with no stemming and no stop words. `search_ukrainian_chunks`
  reads it. This is only a baseline for Ukrainian: "відпустка" matches
  "Відпустка" but not "відпустку" or "відпустки", and an apostrophe splits
  "сім'я" into "сім" and "я".

Without stemming, asking for every word of a Ukrainian Question finds
almost nothing: "Що робити в перший день хвороби?" misses the section
headed "Перший день хвороби", which lacks "робити" and "в". So
`simple` reads a Question as its topic words, without the Ukrainian
function words in `UKRAINIAN_STOP_WORDS`, and a Chunk matches when it
holds at least two of them, or the only one there is. One shared word is
not enough: "днів" alone would match a sick-leave page for a question
about annual leave. PostgreSQL finds the topic words with the same
parser as the stored terms, so a date, a decimal, or an address stays one
word, and `ts_rank` then puts the Chunks with the most and best-placed
words first. A Question that uses web-search syntax is read as typed; see
`simple_query`. The stored terms keep every word, so the list can change
without rebuilding them.

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
  with `english`, words must all match; with `simple`, two of its topic
  words must match, as above, unless the text itself uses the syntax
  below, which is then read as typed: `"quoted words"` must appear next to each other in
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

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import RowMapping
from sqlalchemy.dialects.postgresql import ARRAY, REGCONFIG
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

# A `simple` match must hold this many topic words of the Question, or all
# of them when it has fewer. At most `MAX_SIMPLE_WORDS` are read, in the
# order typed, which bounds the work for each matching Chunk.
MIN_SIMPLE_WORDS = 2
MAX_SIMPLE_WORDS = 16
# PostgreSQL's web-search "or": a word with no letter or digit next to it.
_OR = re.compile(r"or", re.IGNORECASE)
# The parts of a hyphenated word, which PostgreSQL also stores whole.
_WORD_PARTS = "hword%part"

# Ukrainian function words: prepositions, conjunctions, particles, pronouns,
# question words, and forms of "to be". Lowercase and in NFC, the form the
# query is matched in. They carry no topic, and as an "or" query word each
# would match nearly every Ukrainian Chunk.
UKRAINIAN_STOP_WORDS = frozenset(
    {
        # Prepositions.
        "в",
        "у",  # noqa: RUF001 - a Ukrainian word
        "на",
        "до",
        "з",
        "із",
        "зі",
        "від",
        "для",
        "по",
        "про",
        "при",
        "за",
        "над",
        "під",
        "між",
        "через",
        "без",
        "біля",
        "після",
        "перед",
        "серед",
        "щодо",
        # Conjunctions and particles.
        "і",  # noqa: RUF001 - a Ukrainian word
        "й",
        "та",
        "а",  # noqa: RUF001 - a Ukrainian word
        "але",
        "або",  # noqa: RUF001 - a Ukrainian word
        "чи",
        "що",
        "щоб",
        "як",
        "якщо",
        "бо",  # noqa: RUF001 - a Ukrainian word
        "не",
        "ні",
        "же",
        "ж",
        "б",  # noqa: RUF001 - a Ukrainian word
        "би",
        "лише",
        "тільки",
        "ще",
        "вже",
        # Pronouns and question words.
        "я",
        "ти",
        "він",
        "вона",
        "воно",
        "ми",
        "ви",
        "вони",
        "мене",
        "мені",
        "мій",
        "моя",
        "моє",
        "мої",
        "наш",
        "ваш",
        "його",
        "її",
        "їх",
        "це",
        "цей",
        "ця",
        "ці",
        "той",
        "те",
        "ті",
        "хто",
        "де",
        "куди",
        "коли",
        "чому",
        "який",
        "яка",
        "яке",
        "які",
        # Forms of "to be".
        "є",
        "бути",
        "був",
        "була",
        "було",
        "були",
        "буде",
    }
)

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
    """Return up to `limit` eligible Chunks matching Ukrainian `text`, best first.

    Words match after lowercasing only, in the form they were typed. The
    Question is read by its topic words: Ukrainian stop words are dropped,
    and a Chunk must hold two of the rest, or the only one. Text that uses
    web-search syntax is read as typed, every word required; see
    `simple_query`. Raises `InvalidLexicalQuery` like `search_english_chunks`.
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
    # The text is a bound parameter, read with the configuration the stored
    # terms were built with; `simple` reads its topic words (see
    # `simple_query`).
    query_text = sa.bindparam("query_text", text, type_=sa.Text())
    queries: list[tuple[sa.ColumnElement[Any], sa.ColumnElement[Any]]] = []
    conditions: list[sa.ColumnElement[bool]] = []
    ranks: list[sa.ColumnElement[Any]] = []
    for configuration in configurations:
        terms, regconfig = _TERMS[configuration]
        if (
            configuration is LexicalConfiguration.SIMPLE
            and not simple_query(text).as_typed
        ):
            query, condition = _topic_match(terms, query_text)
        else:
            query = sa.func.websearch_to_tsquery(regconfig, query_text)
            condition = terms.bool_op("@@")(query)
        queries.append((regconfig, query))
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


@dataclass(frozen=True, slots=True)
class SimpleQuery:
    """How `simple` reads one Question: by its topic words, or as typed."""

    text: str = field(repr=False)
    as_typed: bool


def simple_query(text: str) -> SimpleQuery:
    """Return how `simple` reads `text`.

    Text that uses web-search syntax the way PostgreSQL reads it, a quote,
    a `-` at the start or after anything but a letter or digit, or an "or"
    with no letter or digit on either side, is read as typed: every word
    must match, so a phrase or an exclusion keeps its meaning. Any other
    text is read by its topic words (see `_topic_match`).
    """
    return SimpleQuery(text=text, as_typed=_uses_syntax(text))


def _uses_syntax(text: str) -> bool:
    """True when web-search syntax would read part of `text` as an operator."""
    if '"' in text:
        return True
    for index, character in enumerate(text):
        # PostgreSQL reads such a `-` as "without", even before a space.
        if character == "-" and (index == 0 or not text[index - 1].isalnum()):
            return True
    for match in _OR.finditer(text):
        before = text[match.start() - 1] if match.start() > 0 else " "
        after = text[match.end()] if match.end() < len(text) else " "
        if not before.isalnum() and not after.isalnum():
            return True
    return False


def _topic_match(
    terms: sa.ColumnElement[Any], query_text: sa.BindParameter[str]
) -> tuple[sa.ColumnElement[Any], sa.ColumnElement[bool]]:
    """Return the "or" query of the topic words, and the two-word condition.

    PostgreSQL splits the Question with the same parser as the stored terms
    (`ts_debug`), so a date, a decimal, an address, or a file name stays one
    word, and "?" or "²" is none. The parts of a hyphenated word are left
    out, since the whole word is kept; stop words are dropped; each word
    counts once, and the first `MAX_SIMPLE_WORDS` in typed order are read.
    A Chunk matches when its stored words hold `MIN_SIMPLE_WORDS` of them,
    or all when there are fewer. With no topic word the query is NULL,
    which matches nothing.
    """
    # Every column `ts_debug` returns, in its order, then the token's place.
    parsed = (
        sa.func.ts_debug(SIMPLE, query_text)
        .table_valued(
            "alias",
            "description",
            "token",
            "dictionaries",
            "dictionary",
            "lexemes",
            with_ordinality="position",
        )
        .render_derived()
    )
    word = sa.type_coerce(parsed.c.lexemes, ARRAY(sa.Text()))[1]
    stop_words = sa.bindparam(
        "simple_stop_words", sorted(UKRAINIAN_STOP_WORDS), type_=ARRAY(sa.Text())
    )
    topic = (
        sa.select(word.label("word"))
        .where(
            sa.func.cardinality(parsed.c.lexemes) > 0,
            sa.not_(parsed.c.alias.like(_WORD_PARTS)),
            word != sa.func.all(stop_words),
        )
        .group_by(word)
        .order_by(sa.func.min(parsed.c.position))
        .limit(MAX_SIMPLE_WORDS)
        .subquery("topic_words")
    )
    # Uncorrelated, so PostgreSQL computes each once per statement, not per row.
    words = sa.select(sa.func.array_agg(topic.c.word)).scalar_subquery()
    query = sa.select(
        sa.func.to_tsquery(
            SIMPLE, sa.func.string_agg(sa.func.quote_literal(topic.c.word), " | ")
        )
    ).scalar_subquery()
    needed = sa.func.least(MIN_SIMPLE_WORDS, sa.func.cardinality(words))
    # The Chunk's own stored words, read from the outer row, never a table scan.
    stored = sa.select(sa.func.unnest(sa.func.tsvector_to_array(terms))).correlate(
        chunks
    )
    held = sa.func.cardinality(
        sa.func.array(
            stored.intersect(sa.select(sa.func.unnest(words))).scalar_subquery()
        )
    )
    return query, sa.and_(terms.bool_op("@@")(query), held >= needed)


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
