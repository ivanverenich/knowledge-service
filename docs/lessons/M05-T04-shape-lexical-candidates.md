# M05-T04 — Shape lexical Candidates

Source task: [M05-T04](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T04-shape-lexical-candidates.md)

## What you will do

Vector search already returns full Candidates: each found Chunk with everything needed to cite it. Lexical search still returns only an ID, a Document ID, and a score. This lesson makes lexical search return the same Candidates, so the next task can combine both lists without caring which search found a Chunk.

- A new module, `candidates.py`, writes down the fields every search branch must return.
- Lexical search returns `LexicalCandidate`s with those fields, its own score, and **highlights**: where the Question's words sit in the Chunk's text.
- Lexical search says when more results exist beyond the limit, as vector search already does.
- One test suite runs the same checks against both searches.

| The task asks for | Where it happens |
|---|---|
| The same Candidate and provenance contract as vector search | Step 2: `Candidate`; Step 3: `LexicalCandidate` |
| Rank and deterministic ties | Step 3: ranks count from 1; ties go by Chunk ID, before the limit cuts |
| Highlights | Step 3: offsets from PostgreSQL's `ts_headline`, only for returned rows |
| ACL predicate slot | Step 3: `lexical_search_statement` still takes the permission check as `eligibility` |
| Bounded results | Step 3: at most `limit`, with `truncated` |
| Vector and lexical adapters pass a shared contract suite | Step 4: `tests/test_candidate_contract_integration.py` |
| Success, the highest-risk edge, one typed failure | Step 4: provenance and order; a hidden better Chunk never returns; a bad limit is refused |

This lesson includes coding: Steps 2–4 write code. Steps 1, 5, and 6 do not.

Terms used in this lesson:

- **Candidate:** one Chunk a search found that the reader may see, before final Evidence is picked. Like a shortlisted job applicant: not hired yet, but with a complete file.
- **Provenance:** where a Chunk came from: its Document, Source, version, position, title, headings, and anchor. It is what makes a Citation possible.
- **Branch:** one way of searching. Here there are two: vector search (meaning) and lexical search (words).
- **Contract:** the promises every branch keeps, whatever it does inside. A shared test suite checks them for each branch.
- **Highlight:** a pair of character positions in the Chunk's text, `start` and `end`, around a word the Question matched.
- **Truncated:** True when more eligible Chunks exist beyond the limit.

## Before you begin

This section explains the decisions behind the code, so the steps make sense when you reach them.

- **What already exists.** `search_similar_chunks` returns a `VectorSearchResult`. It holds `VectorCandidate`s with full provenance, a `limit`, and `truncated`. `search_lexical_chunks` returns a `LexicalSearch` holding the route and `LexicalMatch`es with only `rank`, `chunk_id`, `document_id`, and `score`.
- **What "the same contract" means here.** Both kinds of Candidate get the same provenance fields, read the same way from the same tables. Each branch keeps its own measure of fit: vector search a distance (lower is better), lexical search a score (higher is better). The two scales mean nothing to each other, so only `rank` compares across branches. M05-T05 fuses the two lists by rank for that reason.
- **Why a Protocol, not a shared base class.** `Candidate` is a typing `Protocol`: a list of read-only fields a class must have. `VectorCandidate` already has them, so vector search does not change at all. Pyright checks that both classes fit. A base class would force both searches to change together for no gain.
- **How highlights work.** PostgreSQL's `ts_headline` returns the text with each matched word wrapped in two marker characters. With `HighlightAll=true` it keeps every other character as it was. The code reads the markers and turns them into `start` and `end` positions. It never hands out marked-up text, so a page that shows highlights cannot be tricked by HTML in a Document.
- **Why the markers are control characters.** The markers are `\x02` and `\x03`. A Document's text should never hold them. If a stored text does, the marked text no longer matches it character for character, and the Candidate gets no highlights. Wrong positions would be worse than none.
- **Why highlights only for returned rows.** `ts_headline` reads a whole text, so running it on every match would be slow. The query first ranks and limits the rows in a subquery. Only those rows, at most `limit + 1`, are marked, once for each word list that matched them.
- **Why one extra row.** The query asks for `limit + 1` rows. If the extra one comes back, more eligible Chunks exist, so `truncated` is True. Vector search does the same.
- **What the review found.** An independent review attacked the prototype. It found no break in the search code, but three gaps in the shared test suite, each closed with a test that fails on the mistake it targets:
  - A limit that kept the *worst* rows went unnoticed, because the old limit test had only tied rows. Now the limit test uses three different fits and checks the best ones are kept.
  - A missing tie-break by Chunk ID went unnoticed, because rows were stored in Chunk ID order anyway. Now the tie test stores six tied rows against Chunk ID order and checks every limit. That test still cannot fail every time: without a tie-break, PostgreSQL may keep any tied row, and it sometimes keeps the right ones by chance. So a unit test also checks the generated SQL: the limited subquery must order ties by Chunk ID.
  - The content version was always 1, so a hard-coded version passed. Now the provenance test stores a second version of the Document.

  Some findings are known limits, written in the evidence instead of fixed:
  - A word the Question excludes, such as `leave` in `policy or -leave`, is still highlighted where it occurs.
  - A quoted phrase highlights each of its words anywhere, not only where they stand together.
  - A single token over 2,047 characters, such as a long encoded blob, makes `ts_headline` change the text, so that Chunk gets no highlights.
  - Stored text that is not NFC-normalized can match with no highlights.
  - A word found only in the title or the headings has no highlight, because highlights point into `text` only.
  - Joined pieces vary: "E-1042" becomes one highlight, but "policy-leave" stays two.
  - Lexical search has no metadata filters yet, unlike vector search. The permission check is the only predicate slot.

  The review also confirmed what held: 1,410 fuzzed Questions over 20 hostile stored texts gave 1,532 highlights, all inside the text, ordered, and on matched words. A hidden Document never leaked into any field or into `truncated`. `EXPLAIN ANALYZE` on 25,000 Chunks showed `ts_headline` running on 11 rows for a limit of 10.
- **Running the tests.** Read the database URL inline in each command, so the password is never printed or saved:

  ```bash
  KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_candidate_contract_integration.py -q -m integration
  ```

- **Files you will touch:**
  - new `src/knowledge_service/retrieval/candidates.py`
  - `src/knowledge_service/retrieval/lexical_search.py`
  - new `tests/test_candidate_contract_integration.py` and `tests/test_lexical_candidates_integration.py`
  - `tests/test_lexical_search.py`, plus small renames in three older lexical test files
  - `docs/architecture.md`, a new evidence file, and at the end `PROGRESS.md`

No migration this time, and vector search does not change.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

This step writes down, before any code, what must always be true and what you expect to see. You will compare your guesses with the real results in Step 5.

**Action:** create `docs/evidence/M05-T04-lexical-candidates.md`:

```markdown
# M05-T04 — Lexical Candidates evidence

Completed: pending

## Invariant

Vector and lexical search return Candidates with the same provenance, read
from the stored Chunk and Document: Chunk, Document, and Source IDs,
content version, ordinal, external ID, title, headings, anchor, and text.
Every Candidate is a Chunk the reader may see. Ranks count from 1, best
first, ties go by Chunk ID, and at most `limit` come back, with
`truncated` True only when more eligible Chunks exist. A lexical
highlight always points at a matched word inside the Candidate's text, or
there is none. No Document or Question text appears in a repr.

## Expected failing check

Before the change, `tests/test_candidate_contract_integration.py` fails to
import `knowledge_service.retrieval.candidates`.

## Predictions

Before running anything, write what you expect:

- Which words of "Employees request parental leave in the portal." does
  the Question "requesting leaves" highlight?
- How many highlights does "E-1042" get in "Error E-1042 means ...", and
  how might PostgreSQL mark it?
- A Chunk matches only through its title. What highlights does it get?
- Three Chunks match and the limit is 2. What is `truncated`? And when
  one of the three belongs to a hidden Document?
- Does `policy or -leave` highlight "leave" in "The policy and the leave."?

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file exists, with your own predictions.

### Step 2 — Coding step: write the Candidate contract

This step adds the module that names the fields every search branch must return.

**In plain words:** this file is a checklist, not code that runs. It lists what any search must hand back for each Chunk it finds: the Chunk's place in the result, which Chunk, Document, and Source it is, which version of the text, where in the Document it sits, and the text itself. Vector search already fits the checklist; lexical search will fit it after Step 3.

| Part | What it does |
|---|---|
| Module docstring | States the promises every branch keeps: eligible, ranked, bounded, quiet. |
| `Candidate` | A `Protocol` with one read-only property per field. Any class with these fields fits; nothing has to inherit from it. |

**Action:** create `src/knowledge_service/retrieval/candidates.py`:

```python
"""The shape every retrieval branch returns its Candidates in.

A Candidate is one Chunk the reader may see, found by one branch, with
everything needed to cite it: which Chunk, which Document and Source, which
version of the text, and the text itself. Vector search returns
`VectorCandidate`, lexical search `LexicalCandidate`; both have these
fields, so code that combines branches, such as fusion, reads them the same
way. Each branch adds its own measure of fit, a distance or a score, which
means nothing to the other branch; only `rank` compares across branches.

What every branch promises about its Candidates:

- **Eligible.** The permission check runs in the same SQL statement as the
  ranking, so a Chunk the reader may not see is never ranked, never counted,
  and never leaves the database.
- **Ranked.** `rank` counts from 1 in result order, best first. Equal fits
  are ordered by Chunk ID, so the same data and query give the same order.
- **Bounded.** At most `limit` Candidates, from 1 to `MAX_CANDIDATES`, and
  `truncated` says whether more eligible Chunks exist.
- **Quiet.** The fields holding Document text are kept out of repr, so
  logging a Candidate or a result never shows them.
"""

from typing import Protocol

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId


class Candidate(Protocol):
    """One eligible Chunk found by one branch, and where it came from."""

    @property
    def rank(self) -> int: ...

    @property
    def chunk_id(self) -> ChunkId: ...

    @property
    def document_id(self) -> DocumentId: ...

    @property
    def source_id(self) -> SourceId: ...

    @property
    def content_version(self) -> ContentVersion: ...

    @property
    def ordinal(self) -> int: ...

    @property
    def external_id(self) -> str: ...

    @property
    def title(self) -> str: ...

    @property
    def heading_path(self) -> str | None: ...

    @property
    def source_anchor(self) -> str | None: ...

    @property
    def text(self) -> str: ...
```

The fields are properties, not plain attributes, because both Candidate classes are frozen dataclasses. A frozen field can be read but not set, and only a read-only property in the `Protocol` matches that.

**Check:** run `uv run pyright`, expecting 0 errors.

### Step 3 — Coding step: return lexical Candidates

This step changes lexical search to return full Candidates with highlights and `truncated`.

**In plain words:** the search still checks the Question, picks the word lists, and asks the database for matching Chunks the reader may see. Now the database also returns where each Chunk came from, and one extra row to tell whether more results exist. For the rows that are kept, PostgreSQL marks the matched words, and the code turns the marks into positions in the text. The result looks like vector search's result, plus the route.

| Part | What changes |
|---|---|
| Module docstring | Points to the shared contract and describes highlights. |
| `_START`, `_STOP`, `_HEADLINE_OPTIONS` | The two marker characters and the `ts_headline` options that keep the whole text. |
| `Highlight` | One `start`/`end` pair: `text[start:end]` is a matched word or a run of them. |
| `LexicalCandidate` | Replaces `LexicalMatch`: the shared provenance fields, `score`, and `highlights`. Text fields stay out of repr. |
| `LexicalSearch` | Now `route`, `limit`, `candidates`, and `truncated`. |
| `search_lexical_chunks` | Returns the new result. Routing is unchanged. |
| `search_english_chunks`, `search_ukrainian_chunks` | Return `LexicalCandidate`s, without `truncated`. |
| `lexical_search_statement` | Ranks, filters by permission, orders by score then Chunk ID, and limits in a subquery. The outer query adds one `ts_headline` column per word list, only where that list matched. |
| `_search` | Asks for `limit + 1` rows and builds Candidates from the first `limit`. |
| `_candidate_from`, `_highlights`, `_spans` | Read one row; turn marked texts into merged, ordered highlights, or none when a marked text does not fit its text. |

**Action 1:** replace `src/knowledge_service/retrieval/lexical_search.py` with:

```python
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
```

Touching highlights join: PostgreSQL marks `E-1042` as `E` and `-1042`, and the code returns one highlight for both. Two word lists may mark different words of the same text, such as "The" only in `simple`; their highlights are merged.

**Action 2:** three older test files still use the old names. Update them:

- In `tests/test_lexical_search_integration.py` and `tests/test_ukrainian_lexical_search_integration.py`, rename `LexicalMatch` to `LexicalCandidate` (in the import and in the `search` return type).
- In `tests/test_language_aware_lexical_search_integration.py`, rename `result.matches` to `result.candidates` (four places).

Then run `uv run ruff format tests` so the imports stay sorted.

**Check:** run `uv run pyright`, expecting 0 errors. The older tests must still pass, because matching, order, and permissions did not change:

```bash
uv run pytest tests/test_lexical_search.py tests/test_lexical_routing.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_lexical_search_integration.py tests/test_ukrainian_lexical_search_integration.py tests/test_language_aware_lexical_search_integration.py -q -m integration
```

Expected: 95 passed, then 56 passed.

### Step 4 — Coding step: test the shared contract and the highlights

This step proves both searches keep the same promises, and that highlights point at the right words.

**In plain words:** the contract suite stores a few Documents that both searches can find: every text holds the word "policy", and every Chunk gets a vector. Each test then runs twice, once through vector search and once through lexical search, and checks the same things: provenance, order, ties, the limit, `truncated`, hidden Documents, empty results, bad limits, and what a repr shows. The highlight tests store small texts and check which words the positions point at.

| Test | Proves |
|---|---|
| `test_each_candidate_carries_the_provenance_of_its_chunk` | Every field matches the stored Chunk and Document, for a second version with two headed Chunks. |
| `test_ranks_count_from_one_best_first_and_ties_go_by_chunk_id` | Ranks 1 to n, best first, equal fits by Chunk ID. |
| `test_the_limit_bounds_the_candidates_and_truncated_says_more_exist` | Review fix: the limit keeps the best Chunks, and `truncated` is right at every limit. |
| `test_the_limit_cuts_ties_by_chunk_id_whatever_order_rows_are_stored_in` | Review fix: ties are cut by Chunk ID even when rows are stored in another order. |
| `test_a_better_chunk_the_reader_may_not_see_is_never_a_candidate` | Highest-risk edge: a hidden, better Chunk never returns and never sets `truncated`. |
| `test_no_eligible_chunk_is_an_empty_result` | No eligible Chunk is an empty result, not an error. |
| `test_a_result_repr_shows_no_document_text` | Logging a result or a Candidate shows no Document text. |
| `test_a_bad_limit_is_refused_before_the_database` | Typed failure: each branch refuses a bad limit with its own error, before any query. |
| `test_both_candidate_types_meet_the_contract` | Pyright checks both classes against `Candidate`. |
| `test_highlights_point_at_the_words_english_stemming_matched` | "requesting leaves" highlights "request" and "leave". |
| `test_an_identifier_is_one_highlight` | `E-1042` is one highlight. |
| `test_highlights_join_the_words_of_every_configuration_that_matched` | With both lists, "The" (from `simple`) and "policy" are both highlighted. |
| `test_a_mixed_question_highlights_both_alphabets` | "розгортає deploying" highlights "розгортає" and "deployments". |
| `test_a_match_only_in_the_title_has_no_highlight` | A title-only match comes back with no highlights. |
| `test_text_holding_a_mark_character_gets_no_highlight` | Text holding a marker character gets no highlights instead of wrong ones. |
| `test_only_the_ranked_and_limited_rows_are_highlighted` | `ts_headline` sits outside the limited subquery. |
| `test_ties_are_broken_by_chunk_id_before_the_limit_cuts` | Review fix: the limited subquery orders ties by Chunk ID. |

**Action 1:** create `tests/test_candidate_contract_integration.py`:

```python
"""The Candidate contract, checked the same way for every retrieval branch.

Each test runs once for vector search and once for lexical search, through
the function a caller uses. Every Document's text holds the word "policy",
and every Chunk gets a vector, so both branches can find the same Chunks.
"""

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import ContentVersion
from knowledge_service.embeddings.port import EmbeddingModelIdentity
from knowledge_service.identifiers import (
    DocumentId,
    EmbeddingSpaceId,
    SourceId,
    UserId,
)
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.documents import load_document_by_id
from knowledge_service.persistence.embeddings import (
    insert_chunk_embeddings,
    load_embedding_space_id,
)
from knowledge_service.persistence.schema import chunks, embedding_spaces
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.lexical_search import (
    InvalidLexicalQuery,
    LexicalCandidate,
    search_lexical_chunks,
)
from knowledge_service.retrieval.vector_search import (
    MAX_CANDIDATES,
    InvalidCandidateLimit,
    VectorCandidate,
    search_similar_chunks,
)
from knowledge_service.settings import Settings

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000044")
OBSERVED_AT = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
IDENTITY = EmbeddingModelIdentity(provider="fake", model="fake-embedding", dimensions=3)
READER = UserId.parse("00000000-0000-0000-0000-0000000000d1")
OTHER_USER = UserId.parse("00000000-0000-0000-0000-0000000000d2")
CONTEXT = AuthorizationContext(user_id=READER)
QUERY = (1.0, 0.0, 0.0)
WORD = "policy"
MARKER = "zebramarker"
# Long enough that each heading starts its own Chunk.
SECTION = " ".join(["Staff read this section of the handbook carefully."] * 12)

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


@dataclass(frozen=True, slots=True)
class Found:
    """What one branch returned, in the terms the contract talks about."""

    result: object
    candidates: Sequence[Candidate]
    truncated: bool
    # Each Candidate's fit, lower is better, so order can be checked alike.
    fits: list[float]


@dataclass(frozen=True, slots=True)
class Branch:
    name: str
    search: Callable[[AsyncConnection, AuthorizationContext, int], Awaitable[Found]]
    invalid_limit: type[Exception]


async def by_vector(
    connection: AsyncConnection, context: AuthorizationContext, limit: int
) -> Found:
    result = await search_similar_chunks(
        connection, identity=IDENTITY, query=QUERY, context=context, limit=limit
    )
    return Found(
        result=result,
        candidates=result.candidates,
        truncated=result.truncated,
        fits=[candidate.distance for candidate in result.candidates],
    )


async def by_words(
    connection: AsyncConnection, context: AuthorizationContext, limit: int
) -> Found:
    result = await search_lexical_chunks(
        connection, text=WORD, context=context, limit=limit
    )
    return Found(
        result=result,
        candidates=result.candidates,
        truncated=result.truncated,
        fits=[-candidate.score for candidate in result.candidates],
    )


BRANCHES = [
    pytest.param(Branch("vector", by_vector, InvalidCandidateLimit), id="vector"),
    pytest.param(Branch("lexical", by_words, InvalidLexicalQuery), id="lexical"),
]


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE.value})
        await connection.execute(
            sa.insert(embedding_spaces).values(
                space_id=EmbeddingSpaceId.new().value,
                provider=IDENTITY.provider,
                model=IDENTITY.model,
                dimensions=IDENTITY.dimensions,
                is_current=True,
                created_at=OBSERVED_AT,
            )
        )
    try:
        yield runtime
    finally:
        await runtime.dispose()


def a_record(
    name: str, *, title: str, text: str, version: str = "v1"
) -> NormalizedDocument:
    media_type = "text/markdown" if text.startswith("#") else "text/plain"
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version=version,
            media_type=media_type,
            content=text.encode(),
            access=SourceAccess(public=True),
            metadata=SourceMetadata(title=title, modified_at=OBSERVED_AT, size_bytes=1),
        ),
        source_id=SOURCE,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def a_document(
    runtime: DatabaseRuntime,
    name: str,
    *,
    text: str = "The policy.",
    title: str = "Handbook",
    vector: Sequence[float] = (1.0, 1.0, 0.0),
    readers: Sequence[Subject] = (Subject.public(),),
    version: str = "v1",
) -> DocumentId:
    """Store a Document and give each of its Chunks the same vector."""
    reconciled = await reconcile_document(
        runtime,
        a_record(name, title=title, text=text, version=version),
        readers=readers,
        observed_at=OBSERVED_AT,
    )
    document_id = reconciled.document.document_id
    async with runtime.transaction() as connection:
        space_id = await load_embedding_space_id(connection, IDENTITY)
        assert space_id is not None
        stored = await load_chunks(connection, document_id=document_id)
        await insert_chunk_embeddings(
            connection,
            space_id=space_id,
            dimensions=IDENTITY.dimensions,
            vectors={chunk.chunk_id: vector for chunk in stored},
            created_at=OBSERVED_AT,
        )
    return document_id


async def found(
    runtime: DatabaseRuntime,
    branch: Branch,
    *,
    limit: int = 10,
    context: AuthorizationContext = CONTEXT,
) -> Found:
    async with runtime.transaction() as connection:
        return await branch.search(connection, context, limit)


@pytest.mark.integration
@pytest.mark.parametrize("branch", BRANCHES)
async def test_each_candidate_carries_the_provenance_of_its_chunk(
    runtime: DatabaseRuntime, branch: Branch
) -> None:
    await a_document(runtime, "guide.md", title="Staff guide", text="Draft.")
    # A second version, so the content version is not the first one.
    document_id = await a_document(
        runtime,
        "guide.md",
        title="Staff guide",
        text=(
            f"# Leave\n\nThe leave policy. {SECTION}\n\n"
            f"# Travel\n\nThe travel policy. {SECTION}"
        ),
        version="v2",
    )

    result = await found(runtime, branch)

    async with runtime.transaction() as connection:
        document = await load_document_by_id(connection, document_id=document_id)
        stored = await load_chunks(connection, document_id=document_id)
    assert document is not None
    # Two Chunks, each with its own heading and anchor, so nothing matches
    # by chance.
    assert len({(chunk.heading_path, chunk.source_anchor) for chunk in stored}) == 2
    assert all(chunk.heading_path and chunk.source_anchor for chunk in stored)
    assert {chunk.content_version for chunk in stored} == {ContentVersion(2)}
    by_id = {chunk.chunk_id: chunk for chunk in stored}
    assert {candidate.chunk_id for candidate in result.candidates} == set(by_id)
    for candidate in result.candidates:
        chunk = by_id[candidate.chunk_id]
        assert candidate.document_id == document_id
        assert candidate.source_id == SOURCE
        assert candidate.content_version == chunk.content_version
        assert candidate.ordinal == chunk.ordinal
        assert candidate.external_id == "guide.md"
        assert candidate.title == document.title == "Staff guide"
        assert candidate.heading_path == chunk.heading_path
        assert candidate.source_anchor == chunk.source_anchor
        assert candidate.text == chunk.text


@pytest.mark.integration
@pytest.mark.parametrize("branch", BRANCHES)
async def test_ranks_count_from_one_best_first_and_ties_go_by_chunk_id(
    runtime: DatabaseRuntime, branch: Branch
) -> None:
    # Three Documents alike in words and vector tie; one fits better in both.
    for name in ["a.txt", "b.txt", "c.txt"]:
        await a_document(runtime, name)
    await a_document(
        runtime, "best.txt", title="Policy", text="Policy policy.", vector=QUERY
    )

    result = await found(runtime, branch)

    assert [candidate.rank for candidate in result.candidates] == [1, 2, 3, 4]
    assert result.fits == sorted(result.fits)
    assert result.candidates[0].external_id == "best.txt"
    tied = [candidate.chunk_id for candidate in result.candidates[1:]]
    assert len(set(result.fits[1:])) == 1
    assert tied == sorted(tied, key=lambda chunk_id: chunk_id.value)


@pytest.mark.integration
@pytest.mark.parametrize("branch", BRANCHES)
@pytest.mark.parametrize(
    ("limit", "returned", "truncated"),
    [(1, 1, True), (2, 2, True), (3, 3, False), (MAX_CANDIDATES, 3, False)],
)
async def test_the_limit_bounds_the_candidates_and_truncated_says_more_exist(
    runtime: DatabaseRuntime,
    branch: Branch,
    limit: int,
    returned: int,
    truncated: bool,
) -> None:
    # Three fits, best first in both branches, so the limit must keep the
    # best ones, not just any.
    await a_document(
        runtime, "a.txt", title="Policy", text="Policy policy policy.", vector=QUERY
    )
    await a_document(runtime, "b.txt", text="Policy policy.", vector=(1.0, 0.5, 0.0))
    await a_document(runtime, "c.txt", text="The policy.", vector=(1.0, 1.0, 0.0))

    result = await found(runtime, branch, limit=limit)

    assert [c.external_id for c in result.candidates] == [
        "a.txt",
        "b.txt",
        "c.txt",
    ][:returned]
    assert result.truncated is truncated


@pytest.mark.integration
@pytest.mark.parametrize("branch", BRANCHES)
async def test_the_limit_cuts_ties_by_chunk_id_whatever_order_rows_are_stored_in(
    runtime: DatabaseRuntime, branch: Branch
) -> None:
    tied = [await a_document(runtime, f"{index}.txt") for index in range(6)]
    # Rewriting a row moves it to free space, often at the end of the table.
    # Rewriting the last Document first tends to store the rows against
    # Chunk ID order, so the order cannot come right by accident.
    async with runtime.transaction() as connection:
        for document_id in reversed(tied):
            await connection.execute(
                sa.update(chunks)
                .where(chunks.c.document_id == document_id.value)
                .values(ordinal=chunks.c.ordinal)
            )
    everything = await found(runtime, branch)
    assert len(set(everything.fits)) == 1
    by_chunk_id = sorted(
        (candidate.chunk_id for candidate in everything.candidates),
        key=lambda chunk_id: chunk_id.value,
    )

    for limit in range(1, len(tied) + 1):
        result = await found(runtime, branch, limit=limit)

        assert [c.chunk_id for c in result.candidates] == by_chunk_id[:limit]


@pytest.mark.integration
@pytest.mark.parametrize("branch", BRANCHES)
async def test_a_better_chunk_the_reader_may_not_see_is_never_a_candidate(
    runtime: DatabaseRuntime, branch: Branch
) -> None:
    await a_document(
        runtime,
        "hidden.txt",
        title="Policy",
        text="Policy policy policy.",
        vector=QUERY,
        readers=(Subject.user(OTHER_USER),),
    )
    await a_document(runtime, "visible.txt")

    for limit in [1, 10]:
        result = await found(runtime, branch, limit=limit)

        assert [c.external_id for c in result.candidates] == ["visible.txt"]
        # Not counted either: nothing more exists for this reader.
        assert result.truncated is False
    other = await found(
        runtime, branch, context=AuthorizationContext(user_id=OTHER_USER)
    )
    assert other.candidates[0].external_id == "hidden.txt"


@pytest.mark.integration
@pytest.mark.parametrize("branch", BRANCHES)
async def test_no_eligible_chunk_is_an_empty_result(
    runtime: DatabaseRuntime, branch: Branch
) -> None:
    await a_document(runtime, "hidden.txt", readers=(Subject.user(OTHER_USER),))

    result = await found(runtime, branch)

    assert list(result.candidates) == []
    assert result.truncated is False


@pytest.mark.integration
@pytest.mark.parametrize("branch", BRANCHES)
async def test_a_result_repr_shows_no_document_text(
    runtime: DatabaseRuntime, branch: Branch
) -> None:
    await a_document(
        runtime,
        f"{MARKER}.md",
        title=MARKER,
        text=f"# {MARKER}\n\nThe {MARKER} policy.",
    )

    result = await found(runtime, branch)

    assert len(result.candidates) == 1
    assert MARKER in result.candidates[0].text
    assert MARKER not in repr(result.result)
    assert MARKER not in repr(result.candidates[0])


@pytest.mark.parametrize("branch", BRANCHES)
@pytest.mark.parametrize("limit", [0, -1, MAX_CANDIDATES + 1, True, 2.0, "5", None])
async def test_a_bad_limit_is_refused_before_the_database(
    branch: Branch, limit: object
) -> None:
    # Any use of the connection would fail: the check must come first.
    unused = cast(AsyncConnection, object())

    with pytest.raises(branch.invalid_limit):
        await branch.search(unused, CONTEXT, cast(int, limit))


def test_both_candidate_types_meet_the_contract() -> None:
    # Pyright checks this assignment against the Protocol's fields.
    kinds: list[type[Candidate]] = [VectorCandidate, LexicalCandidate]

    assert len(kinds) == 2
```

**Action 2:** create `tests/test_lexical_candidates_integration.py`:

```python
# Ukrainian test data is written in Cyrillic.
# ruff: noqa: RUF001
"""Highlights on lexical Candidates, against a real database.

A highlight is a pair of offsets into the Candidate's `text`. Each test
checks the words they point at, through `search_lexical_chunks`.
"""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, SourceId, UserId
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.language import Language
from knowledge_service.persistence.schema import chunks
from knowledge_service.retrieval.lexical_search import (
    LexicalCandidate,
    search_lexical_chunks,
)
from knowledge_service.settings import Settings

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000055")
OBSERVED_AT = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
READER = UserId.parse("00000000-0000-0000-0000-0000000000e1")
CONTEXT = AuthorizationContext(user_id=READER)

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE.value})
    try:
        yield runtime
    finally:
        await runtime.dispose()


def a_record(name: str, *, title: str, text: str) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version="v1",
            media_type="text/plain",
            content=text.encode(),
            access=SourceAccess(public=True),
            metadata=SourceMetadata(title=title, modified_at=OBSERVED_AT, size_bytes=1),
        ),
        source_id=SOURCE,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def a_document(
    runtime: DatabaseRuntime,
    name: str,
    *,
    title: str,
    text: str,
    readers: Sequence[Subject] = (Subject.public(),),
) -> DocumentId:
    reconciled = await reconcile_document(
        runtime,
        a_record(name, title=title, text=text),
        readers=readers,
        observed_at=OBSERVED_AT,
    )
    return reconciled.document.document_id


async def only_candidate(
    runtime: DatabaseRuntime, text: str, *, language: Language | None = None
) -> LexicalCandidate:
    async with runtime.transaction() as connection:
        result = await search_lexical_chunks(
            connection, text=text, context=CONTEXT, limit=10, language=language
        )
    (candidate,) = result.candidates
    return candidate


def highlighted(candidate: LexicalCandidate) -> list[str]:
    return [candidate.text[h.start : h.end] for h in candidate.highlights]


@pytest.mark.integration
async def test_highlights_point_at_the_words_english_stemming_matched(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime,
        "leave.txt",
        title="Handbook",
        text="Employees request parental leave in the portal.",
    )

    candidate = await only_candidate(runtime, "requesting leaves")

    assert highlighted(candidate) == ["request", "leave"]


@pytest.mark.integration
async def test_an_identifier_is_one_highlight(runtime: DatabaseRuntime) -> None:
    await a_document(
        runtime,
        "error.txt",
        title="Handbook",
        text="Error E-1042 means the request was rejected.",
    )

    candidate = await only_candidate(runtime, "E-1042")

    # PostgreSQL marks "E" and "-1042" apart; touching marks join.
    assert highlighted(candidate) == ["E-1042"]


@pytest.mark.integration
async def test_highlights_join_the_words_of_every_configuration_that_matched(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(runtime, "policy.txt", title="Handbook", text="The policy.")

    # English drops "the"; `simple` keeps and marks it.
    candidate = await only_candidate(runtime, "the policy", language=Language.MIXED)

    assert highlighted(candidate) == ["The", "policy"]


@pytest.mark.integration
async def test_a_mixed_question_highlights_both_alphabets(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime,
        "deploy.txt",
        title="Розгортання",
        text="Команда розгортає сервіси через deployments у Kubernetes.",
    )

    candidate = await only_candidate(runtime, "розгортає deploying")

    assert highlighted(candidate) == ["розгортає", "deployments"]


@pytest.mark.integration
async def test_a_match_only_in_the_title_has_no_highlight(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(runtime, "notes.txt", title="Leave", text="General notes.")

    candidate = await only_candidate(runtime, "leave")

    assert candidate.highlights == ()


@pytest.mark.integration
@pytest.mark.parametrize(
    "stored",
    [
        "Leave \x02policy\x03 here, policy.",
        "Leave policy\x03 here.",
        "Leave policy here.\x02",
    ],
)
async def test_text_holding_a_mark_character_gets_no_highlight(
    runtime: DatabaseRuntime, stored: str
) -> None:
    document_id = await a_document(
        runtime, "odd.txt", title="Handbook", text="Leave policy here."
    )
    # A row can hold the characters `ts_headline` marks words with.
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(chunks)
            .where(chunks.c.document_id == document_id.value)
            .values(text=stored)
        )

    candidate = await only_candidate(runtime, "policy")

    # Offsets that might point at the wrong words are worse than none.
    assert candidate.text == stored
    assert candidate.highlights == ()
```

**Action 3:** in `tests/test_lexical_search.py`, add these two tests just before `test_a_statement_needs_distinct_configurations` (above its `@pytest.mark.parametrize`):

```python
def test_only_the_ranked_and_limited_rows_are_highlighted() -> None:
    statement = lexical_search_statement(
        (LexicalConfiguration.ENGLISH, LexicalConfiguration.SIMPLE),
        text="leave",
        eligibility=sa.true(),
        limit=5,
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))
    outer, ranked = sql.split("FROM (SELECT", 1)

    # `ts_headline` reads a whole text, so it runs once per configuration on
    # the rows LIMIT kept, never on every match.
    assert outer.count("ts_headline(") == 2
    assert "ts_headline" not in ranked
    assert "LIMIT" in ranked


def test_ties_are_broken_by_chunk_id_before_the_limit_cuts() -> None:
    statement = english_search_statement(text="leave", eligibility=sa.true(), limit=5)

    sql = str(statement.compile(dialect=postgresql.dialect()))
    ranked = sql.split("FROM (SELECT", 1)[1]
    order = ranked[ranked.index("ORDER BY") : ranked.index("LIMIT")]

    # Without it, PostgreSQL may keep any of the tied rows when LIMIT cuts,
    # and the outer ORDER BY cannot bring back the ones it dropped.
    assert order.rstrip().endswith("DESC, chunks.chunk_id")
```

**Check:**

```bash
uv run pytest tests/test_lexical_search.py tests/test_lexical_routing.py tests/test_candidate_contract_integration.py -q -m "not integration"
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_candidate_contract_integration.py tests/test_lexical_candidates_integration.py -q -m integration
```

Expected: 112 passed, 20 deselected; then 28 passed, 15 deselected.

**Deliberate break:** stop asking for the extra row. In `_search`, change

```python
        limit=limit + 1,
```

to

```python
        limit=limit,
```

Run the integration command again. Expected: 2 failures, `test_the_limit_bounds_the_candidates_and_truncated_says_more_exist[1-1-True-lexical]` and `[2-2-True-lexical]`. The Candidates are still right, but `truncated` never becomes True, so a caller cannot tell that more results exist. Undo the change and confirm everything passes again.

### Step 5 — No coding in this step: see the highlights for yourself

This step shows what PostgreSQL's marks look like before the code turns them into positions.

**Action:** run these queries. They replace the two marker characters with `[` and `]` so you can see them:

```bash
docker exec knowledge-service-postgres psql -U test_user -d knowledge_service_test -At \
  -c "SELECT replace(replace(ts_headline('english', 'Employees request parental leave in the portal.', websearch_to_tsquery('english', 'requesting leaves'), E'HighlightAll=true, StartSel=\x02, StopSel=\x03'), E'\x02', '['), E'\x03', ']')" \
  -c "SELECT replace(replace(ts_headline('english', 'Error E-1042 means the request was rejected.', websearch_to_tsquery('english', 'E-1042'), E'HighlightAll=true, StartSel=\x02, StopSel=\x03'), E'\x02', '['), E'\x03', ']')" \
  -c "SELECT replace(replace(ts_headline('english', 'The policy and the leave.', websearch_to_tsquery('english', 'policy or -leave'), E'HighlightAll=true, StartSel=\x02, StopSel=\x03'), E'\x02', '['), E'\x03', ']')" \
  -c "SELECT replace(replace(ts_headline('english', 'Read <b>the</b> leave   policy.', websearch_to_tsquery('english', 'leave'), E'HighlightAll=true, StartSel=\x02, StopSel=\x03'), E'\x02', '['), E'\x03', ']')"
```

The reference run printed:

```text
Employees [request] parental [leave] in the portal.
Error [E][-1042] means the request was rejected.
The [policy] and the [leave].
Read <b>the</b> [leave]   policy.
```

How to read it:

- English stemming marks "request" and "leave", although the Question said "requesting leaves".
- `E-1042` is marked in two touching pieces. The code joins them into one highlight.
- "leave" is marked although the Question excludes it. This is a known limit.
- The tag and the three spaces come back unchanged. That is why the marks can become exact positions.

Write in the evidence which of your Step 1 predictions were right.

**Check:** your output matches the reference, and your evidence names one prediction you got wrong, or says that none were.

### Step 6 — No coding in this step: gates, docs, evidence, and progress

This step proves everything still works, updates the one architecture line the change made out of date, and records the result.

**Action 1:** in `docs/architecture.md`, change the `retrieval` row of the "Code layout" table to:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `MetadataCondition` |
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_lexical_search.py tests/test_lexical_routing.py -q
make test-integration
make format-check
make lint
make typecheck
make check
make docs-check
```

Expected: all pass. The reference run gave `make check` 1274 passed, 3 deselected, and `make test-integration` 284 passed. Run `make check` with the database URL set, or its database tests are skipped instead of run.

**Action 3:** complete the evidence file:

- **Design:** the `Candidate` contract and why it is a `Protocol`, the new `LexicalCandidate` and `LexicalSearch`, how highlights are made and why only for returned rows, the extra row for `truncated`, and what the review changed.
- **Verification:** the commands and counts, a table of the contract tests with what each proves for both branches, the Step 5 output, the deliberate break from Step 4, and the known limits from "Before you begin".
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M05-T04 row to `docs/curriculum/PROGRESS.md`, set the next task to M05-T05, and commit with `m05-t04: shape lexical candidates`.

## Reflection questions

1. What does `search_lexical_chunks` now hide from the code that calls it? List what a caller never has to know, such as how highlights are found or how `truncated` is decided.
2. Someone speeds up search by computing highlights in the same query as the ranking, without the subquery. What would get slower, and which test would notice?
3. Vector search measures fit by distance and lexical search by score. Why does the shared contract leave both out, and what should M05-T05 use to combine the two lists instead?
4. A web page will show highlighted text to readers. Why are positions safer to hand to it than text with markers or HTML tags in it?
5. Which adversarial case could show that the contract suite is still too weak? Think of a wrong search that would pass every test in it today.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before running anything.
- [ ] `candidates.py` defines `Candidate`, and pyright accepts both `VectorCandidate` and `LexicalCandidate` as one.
- [ ] Lexical search returns `LexicalCandidate`s with full provenance, `score`, and `highlights`, and `LexicalSearch` has `limit` and `truncated`.
- [ ] Highlights are made only for the returned rows, and are positions inside `text` or nothing.
- [ ] The shared contract suite passes for both vector and lexical search, and the highlight tests pass.
- [ ] You ran the deliberate break and saw it fail, then undid it.
- [ ] You ran the Step 5 commands and compared them with your predictions.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The architecture line, evidence with the contract table, PROGRESS row, and commit are done.

When you have written the code, share it or any error output, and I will review it against this checklist.
