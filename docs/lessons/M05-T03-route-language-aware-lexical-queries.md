# M05-T03 — Route language-aware lexical queries

Source task: [M05-T03](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T03-route-language-aware-lexical-queries.md)

## What you will do

Every Chunk now has two stored word lists: English words with endings cut off, and `simple` words that are only lowercased. This lesson makes search choose the right list for each Question by itself, using more than one clue, so a single wrong guess cannot hide results.

- A new module, `lexical_routing.py`, looks at the Question and at what the caller says about it, and picks English, `simple`, or both.
- A new function, `search_lexical_chunks`, uses that choice. When it picks both, one database query searches both lists and returns each Chunk once.
- The result carries the choice and the reason for it, so you can see why a Question was searched the way it was.

| The task asks for | Where it happens |
|---|---|
| Choose the configuration for English, Ukrainian, mixed, and unknown Questions | Step 2: `route_lexical_question` |
| Without trusting a single detector blindly | Step 2: each clue adds a configuration; one is used only when all agree |
| Search with that choice | Step 3: `search_lexical_chunks` and `lexical_search_statement` |
| Tests for explicit language, detected fallback, mixed text, and identifiers | Step 4: the routing unit tests and the new integration tests |
| Success, the highest-risk edge, one typed failure | Step 4: correct matches; a hidden Document never returns; `InvalidLexicalQuery` |

This lesson includes coding: Steps 2–4 write code. Steps 1, 5, and 6 do not.

Terms used in this lesson:

- **Route:** the decision about which word lists a Question is matched against, plus the reasons. Like a sorting desk that sends a letter to one office or to two.
- **Configuration:** PostgreSQL's rules for turning text into search words. `english` cuts endings and drops common words; `simple` only lowercases.
- **Signal:** one clue about the Question. Here there are three: the language the caller states, the alphabet the Question is written in, and whether English keeps any of its words.
- **Stop word:** a very common word a configuration throws away, such as "the", "it", or "who" in English.
- **Identifier:** a code such as `E-1042`, `api_v2`, or `v1.2.3`. It belongs to no language.

## Before you begin

This section explains the decisions behind the code, so the steps make sense when you reach them.

- **What already exists.** `search_english_chunks` reads `search_en`, and `search_ukrainian_chunks` reads `search_simple`. The caller had to choose. M03-T07 added `detect_language`, which labels whole Documents.
- **What the two word lists really differ in.** PostgreSQL's English rules leave Cyrillic words unchanged. So for Ukrainian words the two lists hold the same words today. They differ only for Latin words: English cuts "leaves" to `leav` and drops "the"; `simple` keeps both as typed. Identifiers split the same way in both.
- **Why not reuse `detect_language`.** It is built for Documents: it needs at least 20 letters, and it calls Cyrillic Ukrainian only when it sees letters such as "ї" or "є". A Question like "відпустку" is too short and would come back unknown. For choosing a word list, the alphabet is what matters, not Ukrainian versus Russian, because `simple` is the only list for any Cyrillic text.
- **Why no single clue is trusted.** The stated language comes from a setting or a form, not from this Question; a Ukrainian reader may type "parental leave". The alphabet is a guess from a few words. So each clue adds the list it supports, and only when every clue points to one list is a single list used. Searching both costs a second index lookup but never loses a Chunk.
- **The rules, in one table:**

  | Clue | English | `simple` |
  |---|---|---|
  | Stated `en` | yes | |
  | Stated `uk` | | yes |
  | Stated `mixed` | yes | yes |
  | Stated `unknown`, or nothing | | |
  | Words only in Latin letters | yes | |
  | Words only in Cyrillic letters | | yes |
  | Both alphabets, or no letters at all | yes | yes |
  | English keeps no word of the Question | | yes |

  A word with a digit counts as an identifier and is skipped when reading the alphabet.
- **How two lists are searched at once.** One query asks for Chunks where either list matches, with the permission check in the same `WHERE`. A Chunk's score is the higher rank among the lists that fully match it. The rank of a list that matches only some words is ignored, because PostgreSQL still gives such a partial match a score.
- **What the review found.** An independent review attacked the first version against the real database. Each fix has a test that fails without it:
  - A Latin Question made only of English stop words, such as "IT" or "the who", found nothing, because English reads it as empty. Now the database is asked whether English keeps any word, and if not, `simple` is added.
  - Words joined by an underscore, such as `leave_policies`, were skipped as identifiers, so a mixed Question lost English stemming. Now only a word with a digit counts as an identifier.
  - The router crashed with an untyped error on text that is not a string. Now it raises `InvalidLexicalRoute`.
  - The statement builder accepted repeated or wrongly typed configurations. Now it raises `InvalidLexicalQuery`.

  Some findings are known limits, written in the evidence instead of fixed:
  - Scores depend on the route. `simple` keeps stop words, so the same Chunk can score higher when both lists are searched. Compare positions within one result, never scores across routes; M05-T05 fuses results by position for this reason.
  - Other database errors, such as a timeout, still repeat the Question text, as in M05-T01 and M05-T02.
  - Fullwidth Latin letters, such as "ｌｅａｖｅ", count as no alphabet, so they get both lists.
  - A word glued to digits, such as "відпустка2024", is skipped when reading the alphabet, and only matches in that exact form.

  The review also confirmed what held: 600 random hostile Questions never returned a hidden Document or a repeated Chunk, the order was always the same, and a smaller limit always returned the start of the larger result.
- **Running the tests.** Read the database URL inline in each command, so the password is never printed or saved:

  ```bash
  KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_language_aware_lexical_search_integration.py -q -m integration
  ```

- **Files you will touch:**
  - new `src/knowledge_service/retrieval/lexical_routing.py`
  - `src/knowledge_service/retrieval/lexical_search.py`
  - new `tests/test_lexical_routing.py`, `tests/test_lexical_search.py`, and new `tests/test_language_aware_lexical_search_integration.py`
  - `docs/architecture.md`, a new evidence file, and at the end `PROGRESS.md`

No migration this time: both word lists already exist.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

This step writes down, before any code, what must always be true and what you expect to see. You will compare your guesses with the real results in Step 5.

**Action:** create `docs/evidence/M05-T03-language-aware-lexical-search.md`:

```markdown
# M05-T03 — Language-aware lexical search evidence

Completed: pending

## Invariant

Every lexical Question is matched against each configuration that one of
its signals supports: the language the caller states, the alphabet of its
words (identifiers aside), and whether English keeps any of its words. It
is matched against one configuration only when all signals agree. On every
route, only Chunks the reader may see come back, each once, best first,
with ties broken by Chunk ID, and the route holds no Question text.

## Expected failing check

Before the change, `tests/test_lexical_routing.py` fails to import
`knowledge_service.retrieval.lexical_routing`.

## Predictions

Before running anything, write what you expect:

- Which word lists does "requesting leaves" get with no stated language?
  And with Ukrainian stated?
- Which lists does "розгортає deploying" get, and does it find a Chunk
  that says "розгортає сервіси через deployments"?
- Which lists does "помилка E-1042" get? And "E-1042" alone?
- What does PostgreSQL's English configuration make of the Question "IT",
  and what should the search do about it?
- A hidden Document matches better than a visible one, and both lists are
  searched. What does the reader get?

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file exists, with your own predictions.

### Step 2 — Coding step: write the router

This step adds the module that decides which word lists a Question is matched against.

**In plain words:** this file reads a Question and what the caller says about its language, and decides whether to search the English words, the plain lowercased words, or both. It looks at which alphabet the words use, skips codes like `E-1042`, and adds a list for every clue it finds. It never reads the database and never touches permissions; it only returns the decision and the reasons.

| Part | What it does |
|---|---|
| `LexicalConfiguration` | The two word lists: `english` and `simple`. |
| `QuestionScript` | The alphabet of the Question: `latin`, `cyrillic`, `mixed`, or `none`. |
| `LexicalRoute` | The decision: the lists to search, the stated language, the alphabet, and whether English keeps any word. It refuses an empty, repeated, or reordered list. |
| `route_lexical_question` | Checks its inputs, then adds the lists each clue supports, in a fixed order. |
| `question_script` | Reads the Unicode name of each letter, skipping words that contain a digit. |

**Action:** create `src/knowledge_service/retrieval/lexical_routing.py`:

```python
"""Choose which stored search words a lexical Question is matched against.

Every Chunk has English words (stemmed, without stop words) and `simple`
words (only lowercased). The right choice depends on the Question, and two
signals describe it: the language the caller states, if any, and the
alphabet of the Question's own words. Neither is trusted alone:

- A caller's language comes from a setting or a form, not from this
  Question, so a Ukrainian reader may still type "parental leave".
- The alphabet is a guess from a few words. It cannot tell Ukrainian from
  Russian, and a short Question gives little to go on.

So each signal adds the configuration it supports, and the Question is
matched against one configuration only when every signal agrees on it.
Otherwise it is matched against both, which costs a second index lookup but
never drops a Chunk that either configuration would find.

A third signal comes from the database: whether the English configuration
keeps any word of the Question. It drops English stop words, so "IT" or
"the who" reads as an empty query that matches nothing; `simple` keeps
them, so it is added.

What the signals give:

| Signal | English | `simple` |
|---|---|---|
| Stated `en` | yes | |
| Stated `uk` | | yes |
| Stated `mixed` | yes | yes |
| Stated `unknown`, or nothing | | |
| Words only in Latin letters | yes | |
| Words only in Cyrillic letters | | yes |
| Both alphabets, or neither | yes | yes |
| English keeps no word | | yes |

Identifiers such as `E-1042`, `api_v2`, or `v1.2.3` are not evidence of a
language: a word with a digit is skipped. Both configurations split and
lowercase `e`, `1042`, `v2`, or `v1.2.3` the same way, so they match
whichever configuration is chosen. A word joined by an underscore without a
digit, such as `leave_policy`, still counts: PostgreSQL splits it into
words that English stemming may change.

The route is descriptive and holds no Question text, so it is safe to log.
It never decides who may read a Chunk.
"""

import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from knowledge_service.language import Language


class LexicalConfiguration(StrEnum):
    """A PostgreSQL text-search configuration with stored Chunk words."""

    ENGLISH = "english"
    SIMPLE = "simple"


class QuestionScript(StrEnum):
    """The alphabets the Question's words are written in, identifiers aside."""

    LATIN = "latin"
    CYRILLIC = "cyrillic"
    MIXED = "mixed"
    NONE = "none"


class InvalidLexicalRoute(ValueError):
    """A lexical route violates an invariant."""


_BOTH = (LexicalConfiguration.ENGLISH, LexicalConfiguration.SIMPLE)

_STATED = {
    Language.ENGLISH: (LexicalConfiguration.ENGLISH,),
    Language.UKRAINIAN: (LexicalConfiguration.SIMPLE,),
    Language.MIXED: _BOTH,
    Language.UNKNOWN: (),
}

_DETECTED = {
    QuestionScript.LATIN: (LexicalConfiguration.ENGLISH,),
    QuestionScript.CYRILLIC: (LexicalConfiguration.SIMPLE,),
    QuestionScript.MIXED: _BOTH,
    QuestionScript.NONE: _BOTH,
}


@dataclass(frozen=True, slots=True)
class LexicalRoute:
    """The configurations a Question is matched against, and why.

    `configurations` is never empty, has no repeats, and keeps the order
    of `LexicalConfiguration`.
    """

    configurations: tuple[LexicalConfiguration, ...]
    stated: Language | None
    script: QuestionScript
    english_words: bool = True

    def __post_init__(self) -> None:
        if not self.configurations:
            raise InvalidLexicalRoute("a route needs at least one configuration")
        if self.configurations != tuple(
            configuration
            for configuration in LexicalConfiguration
            if configuration in self.configurations
        ):
            raise InvalidLexicalRoute("configurations must be unique and in order")


def route_lexical_question(
    text: str, *, stated: Language | None, english_words: bool = True
) -> LexicalRoute:
    """Return the configurations to match `text` against.

    `stated` is the language the caller gives for this Question, or None.
    `english_words` is False when the English configuration keeps no word
    of `text`, because every word is an English stop word.
    """
    if type(text) is not str:
        raise InvalidLexicalRoute("text must be a string")
    if stated is not None and type(stated) is not Language:
        raise InvalidLexicalRoute("stated must be a Language or None")
    if type(english_words) is not bool:
        raise InvalidLexicalRoute("english_words must be a bool")
    script = question_script(text)
    wanted = set(_DETECTED[script])
    if stated is not None:
        wanted.update(_STATED[stated])
    if not english_words:
        wanted.add(LexicalConfiguration.SIMPLE)
    return LexicalRoute(
        configurations=tuple(
            configuration
            for configuration in LexicalConfiguration
            if configuration in wanted
        ),
        stated=stated,
        script=script,
        english_words=english_words,
    )


def question_script(text: str) -> QuestionScript:
    """Return which alphabets the letters of `text` use, identifiers aside."""
    latin = cyrillic = False
    for word in text.split():
        if any(character.isdigit() for character in word):
            continue
        for character in word:
            name = unicodedata.name(character, "")
            latin = latin or name.startswith("LATIN ")
            cyrillic = cyrillic or name.startswith("CYRILLIC ")
    if latin and cyrillic:
        return QuestionScript.MIXED
    if latin:
        return QuestionScript.LATIN
    if cyrillic:
        return QuestionScript.CYRILLIC
    return QuestionScript.NONE
```

The alphabet comes from Unicode letter names ("LATIN SMALL LETTER A", "CYRILLIC SMALL LETTER YI"). So the Ukrainian apostrophe "ʼ" and the stress mark in "за́мок" count as neither alphabet, and never turn a Ukrainian Question into a mixed one. `english_words` defaults to `True`; only the search, which can ask the database, sets it to `False`.

**Check:** run `uv run pyright`, expecting 0 errors, then:

```bash
uv run python -c "
from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_routing import route_lexical_question
for text, stated in [('requesting leaves', None), ('відпустку', None), ('розгортає deploying', None), ('E-1042', None), ('requesting leaves', Language.UKRAINIAN)]:
    route = route_lexical_question(text, stated=stated)
    print(text, '|', stated, '|', [c.value for c in route.configurations], route.script.value)
"
```

Expected:

```text
requesting leaves | None | ['english'] latin
відпустку | None | ['simple'] cyrillic
розгортає deploying | None | ['english', 'simple'] mixed
E-1042 | None | ['english', 'simple'] none
requesting leaves | uk | ['english', 'simple'] latin
```

### Step 3 — Coding step: search with the route

This step adds the function the rest of the service will call for any lexical Question.

**In plain words:** the new search checks the Question, chooses the word lists with the router, and, when the router picked English alone, asks the database whether English keeps any word of the Question; if it does not, it adds the plain list. Then it runs one query over the chosen lists, with the same permission check, order, and error handling as before, and returns the matches together with the route.

| Part | What changes |
|---|---|
| Module docstring | Describes the routed search and how two lists are scored. |
| `_TERMS` | Maps each configuration to its stored column and its named PostgreSQL configuration. |
| `LexicalSearch` | The result: the route and the matches. |
| `search_lexical_chunks` | Checks text, limit, and language; NFC-normalizes; routes; asks about English stop words when needed; searches. |
| `lexical_search_statement` | Builds one query for one or more configurations, sharing one bound parameter. Refuses an empty, repeated, or wrongly typed list. |
| `english_search_statement`, `ukrainian_search_statement` | Same behavior, now wrappers over `lexical_search_statement`. |
| `_search`, `_execute` | The shared engine; `_execute` holds the savepoint and the "too many operators" refusal, so the stop-word question and the search refuse the same way. |

**Action:** replace `src/knowledge_service/retrieval/lexical_search.py` with:

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
```

With two lists, the `WHERE` is `search_en @@ ... OR search_simple @@ ...`, so PostgreSQL can combine both GIN indexes. The score uses `CASE WHEN` so that a list counts only if it matched the whole Question. The stop-word question runs only when English alone was chosen, so most Questions still make one database call.

**Check:** run `uv run pyright`, expecting 0 errors. The old tests must still pass, because the English and Ukrainian functions behave as before:

```bash
uv run pytest tests/test_lexical_search.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_lexical_search_integration.py tests/test_ukrainian_lexical_search_integration.py -q -m integration
```

Expected: 29 passed, then 36 passed.

### Step 4 — Coding step: test the routing and the search

This step proves the route is right for each kind of Question and that the search keeps its promises on every route.

**In plain words:** the routing tests run a table of Questions through the router with no database: English, Ukrainian, Russian, mixed, codes, apostrophes, and empty punctuation, with and without a stated language. The search tests add the new function to every input check. The integration tests store an English, a Ukrainian, and a mixed Document that all mention `E-1042`, and check what each kind of Question finds and why.

| Test | Proves |
|---|---|
| `test_the_question_alphabet_chooses_the_configurations` | Detected fallback, mixed text, and identifiers, without a database. |
| `test_a_stated_language_adds_its_configuration` | Explicit language adds a list and never removes one. |
| `test_any_text_gets_a_route_with_at_least_one_configuration` | Every combination of tricky words and stated languages gets a route. |
| `test_question_text_must_be_a_string`, `test_a_stated_language_must_be_a_language` | Typed failure in the router. |
| `test_bad_text_or_limit_is_refused_before_the_database` | The same input checks for all three searches. |
| `test_both_configurations_share_one_bound_parameter` | The Question is data, sent once, and each list uses its own configuration. |
| `test_a_wrong_stated_language_does_not_lose_english_stemming` | Stated Ukrainian with an English Question still finds "leave" from "leaves". |
| `test_without_a_stated_language_the_alphabet_decides` | Detected fallback against the database. |
| `test_a_mixed_question_keeps_english_stemming_for_its_english_words` | "розгортає deploying" finds "розгортає ... deployments". |
| `test_identifiers_match_on_every_route_and_do_not_decide_it` | `E-1042` is found on every route, and does not change the route. |
| `test_a_question_of_english_stop_words_falls_back_to_simple` | Review fix: "IT" and "the who" find their Documents. |
| `test_a_word_joined_by_an_underscore_keeps_english_stemming` | Review fix: `leave_policies` finds `leave_policy`. |
| `test_a_chunk_found_by_both_configurations_is_returned_once` | No repeated Chunks; ranks and scores in order. |
| `test_a_better_match_the_reader_may_not_see_is_never_returned` | Permissions, the highest-risk edge, on a two-list route. |
| `test_a_refused_question_on_any_route_keeps_no_question_text` | Typed failure from PostgreSQL on both kinds of route, with no Question text in the error. |
| `test_both_routes_read_both_gin_indexes` | The two-list query uses both GIN indexes. |

**Action 1:** create `tests/test_lexical_routing.py`:

```python
# Cyrillic and other scripts are the subject of these tests.
# ruff: noqa: RUF001
"""Checks on choosing the stored search words for a lexical Question."""

import itertools
from typing import cast

import pytest

from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_routing import (
    InvalidLexicalRoute,
    LexicalConfiguration,
    LexicalRoute,
    QuestionScript,
    question_script,
    route_lexical_question,
)

ENGLISH = (LexicalConfiguration.ENGLISH,)
SIMPLE = (LexicalConfiguration.SIMPLE,)
BOTH = (LexicalConfiguration.ENGLISH, LexicalConfiguration.SIMPLE)
MARKER = "zebramarker"


@pytest.mark.parametrize(
    ("text", "script", "configurations"),
    [
        ("requesting parental leaves", QuestionScript.LATIN, ENGLISH),
        ("оплачувана відпустка", QuestionScript.CYRILLIC, SIMPLE),
        ("ОПЛАЧУВАНА ВІДПУСТКА", QuestionScript.CYRILLIC, SIMPLE),
        # The alphabet is the evidence, not the language: Russian is Cyrillic.
        ("оплачиваемый отпуск", QuestionScript.CYRILLIC, SIMPLE),
        ("розгортання deploying", QuestionScript.MIXED, BOTH),
        ("відпустка в Kubernetes", QuestionScript.MIXED, BOTH),
        # Identifiers are not evidence of a language.
        ("E-1042", QuestionScript.NONE, BOTH),
        ("api_v2 v1.2.3 OAuth2", QuestionScript.NONE, BOTH),
        ("помилка E-1042", QuestionScript.CYRILLIC, SIMPLE),
        ("error E-1042 in api_v2", QuestionScript.LATIN, ENGLISH),
        # An underscore alone does not make an identifier: these words can
        # still be stemmed by English.
        ("leave_policies", QuestionScript.LATIN, ENGLISH),
        ("leave_policies оновлено", QuestionScript.MIXED, BOTH),
        # The Ukrainian apostrophe and a stress mark are not other letters.
        ("сімʼї", QuestionScript.CYRILLIC, SIMPLE),
        ("за\u0301мок", QuestionScript.CYRILLIC, SIMPLE),
        ("休暇", QuestionScript.NONE, BOTH),
        ('"" -- ?!', QuestionScript.NONE, BOTH),
    ],
)
def test_the_question_alphabet_chooses_the_configurations(
    text: str,
    script: QuestionScript,
    configurations: tuple[LexicalConfiguration, ...],
) -> None:
    route = route_lexical_question(text, stated=None)

    assert question_script(text) is script
    assert route == LexicalRoute(
        configurations=configurations, stated=None, script=script
    )


@pytest.mark.parametrize(
    ("text", "stated", "configurations"),
    [
        ("requesting parental leaves", Language.ENGLISH, ENGLISH),
        ("оплачувана відпустка", Language.UKRAINIAN, SIMPLE),
        # A stated language that the Question's alphabet contradicts adds
        # its configuration; it never removes the other one.
        ("requesting parental leaves", Language.UKRAINIAN, BOTH),
        ("оплачувана відпустка", Language.ENGLISH, BOTH),
        ("requesting parental leaves", Language.MIXED, BOTH),
        ("E-1042", Language.ENGLISH, BOTH),
        # Unknown states nothing, so the alphabet alone decides.
        ("requesting parental leaves", Language.UNKNOWN, ENGLISH),
        ("оплачувана відпустка", Language.UNKNOWN, SIMPLE),
    ],
)
def test_a_stated_language_adds_its_configuration(
    text: str,
    stated: Language,
    configurations: tuple[LexicalConfiguration, ...],
) -> None:
    route = route_lexical_question(text, stated=stated)

    assert route.configurations == configurations
    assert route.stated is stated


@pytest.mark.parametrize("stated", [None, Language.ENGLISH, Language.UKRAINIAN])
def test_a_question_english_keeps_no_word_of_adds_simple(
    stated: Language | None,
) -> None:
    route = route_lexical_question("the who", stated=stated, english_words=False)

    assert LexicalConfiguration.SIMPLE in route.configurations
    assert route.english_words is False


@pytest.mark.parametrize("stated", ["en", "fr", 1, True])
def test_a_stated_language_must_be_a_language(stated: object) -> None:
    with pytest.raises(InvalidLexicalRoute):
        route_lexical_question("leave", stated=cast(Language, stated))


@pytest.mark.parametrize("text", [None, 5, True, b"leave", ["leave"]])
def test_question_text_must_be_a_string(text: object) -> None:
    with pytest.raises(InvalidLexicalRoute):
        route_lexical_question(cast(str, text), stated=None)


@pytest.mark.parametrize("english_words", [None, 0, "no"])
def test_english_words_must_be_a_bool(english_words: object) -> None:
    with pytest.raises(InvalidLexicalRoute):
        route_lexical_question(
            "leave", stated=None, english_words=cast(bool, english_words)
        )


def test_any_text_gets_a_route_with_at_least_one_configuration() -> None:
    words = ["leave", "ВІДПУСТКА", "E-1042", "сімʼї", "休暇", "--", "\u200b", ""]
    for parts in itertools.product(words, repeat=3):
        for stated in [None, *Language]:
            route = route_lexical_question(" ".join(parts), stated=stated)

            assert route.configurations
            assert set(route.configurations) <= set(LexicalConfiguration)


def test_the_route_holds_no_question_text() -> None:
    route = route_lexical_question(f"{MARKER} відпустка", stated=None)

    assert MARKER not in repr(route)


@pytest.mark.parametrize(
    "configurations",
    [
        (),
        (LexicalConfiguration.SIMPLE, LexicalConfiguration.ENGLISH),
        (LexicalConfiguration.ENGLISH, LexicalConfiguration.ENGLISH),
    ],
)
def test_a_route_needs_unique_ordered_configurations(
    configurations: tuple[LexicalConfiguration, ...],
) -> None:
    with pytest.raises(InvalidLexicalRoute):
        LexicalRoute(
            configurations=configurations, stated=None, script=QuestionScript.NONE
        )
```

**Action 2:** replace `tests/test_lexical_search.py` with:

```python
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
```

**Action 3:** create `tests/test_language_aware_lexical_search_integration.py`:

```python
# Ukrainian test data is written in Cyrillic.
# ruff: noqa: RUF001
"""Language-aware lexical search against a real database.

The corpus has an English, a Ukrainian, and a mixed Document, and all three
mention the identifier `E-1042`. Each test checks the route a Question gets
and the Documents it finds through `search_lexical_chunks`.
"""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.dialects import postgresql

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
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.retrieval.lexical_routing import (
    LexicalConfiguration,
    QuestionScript,
)
from knowledge_service.retrieval.lexical_search import (
    InvalidLexicalQuery,
    LexicalSearch,
    lexical_search_statement,
    search_lexical_chunks,
)
from knowledge_service.settings import Settings

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000033")
OBSERVED_AT = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
READER = UserId.parse("00000000-0000-0000-0000-0000000000c1")
OTHER_USER = UserId.parse("00000000-0000-0000-0000-0000000000c2")
CONTEXT = AuthorizationContext(user_id=READER)
MARKER = "zebramarker"

ENGLISH = (LexicalConfiguration.ENGLISH,)
SIMPLE = (LexicalConfiguration.SIMPLE,)
BOTH = (LexicalConfiguration.ENGLISH, LexicalConfiguration.SIMPLE)

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)

# name: (title, text)
CORPUS = {
    "leave.txt": (
        "Leave requests",
        "Employees request parental leave in the portal."
        " Error E-1042 means the request was rejected.",
    ),
    "vidpustka.txt": (
        "Відпустка",
        "Працівник подає заяву на відпустку в порталі."
        " Помилка E-1042 означає, що заяву відхилено.",
    ),
    "deploy.txt": (
        "Розгортання",
        "Команда розгортає сервіси через deployments у Kubernetes."
        " Помилка E-1042 зупиняє розгортання.",
    ),
}


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


async def the_corpus(runtime: DatabaseRuntime) -> None:
    for name, (title, text) in CORPUS.items():
        await a_document(runtime, name, title=title, text=text)


async def search(
    runtime: DatabaseRuntime,
    text: str,
    *,
    language: Language | None = None,
    limit: int = 10,
    context: AuthorizationContext = CONTEXT,
) -> LexicalSearch:
    async with runtime.transaction() as connection:
        return await search_lexical_chunks(
            connection, text=text, context=context, limit=limit, language=language
        )


async def found(runtime: DatabaseRuntime, text: str, **kwargs: Any) -> list[str]:
    """The names of the Documents found, in result order."""
    result = await search(runtime, text, **kwargs)
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.text("SELECT document_id, external_id FROM documents")
        )
        by_id = {row.document_id: row.external_id for row in rows}
    return [by_id[match.document_id.value] for match in result.matches]


@pytest.mark.integration
async def test_a_stated_language_that_agrees_uses_one_configuration(
    runtime: DatabaseRuntime,
) -> None:
    await the_corpus(runtime)

    english = await search(runtime, "requesting leaves", language=Language.ENGLISH)
    ukrainian = await search(runtime, "відпустку", language=Language.UKRAINIAN)

    assert english.route.configurations == ENGLISH
    assert await found(runtime, "requesting leaves", language=Language.ENGLISH) == [
        "leave.txt"
    ]
    assert ukrainian.route.configurations == SIMPLE
    assert await found(runtime, "відпустку", language=Language.UKRAINIAN) == [
        "vidpustka.txt"
    ]


@pytest.mark.integration
async def test_a_wrong_stated_language_does_not_lose_english_stemming(
    runtime: DatabaseRuntime,
) -> None:
    await the_corpus(runtime)

    # A Ukrainian reader types an English Question. With `simple` alone,
    # "requesting leaves" would miss "request" and "leave".
    result = await search(runtime, "requesting leaves", language=Language.UKRAINIAN)

    assert result.route.configurations == BOTH
    assert await found(runtime, "requesting leaves", language=Language.UKRAINIAN) == [
        "leave.txt"
    ]


@pytest.mark.integration
@pytest.mark.parametrize("language", [None, Language.UNKNOWN])
async def test_without_a_stated_language_the_alphabet_decides(
    runtime: DatabaseRuntime, language: Language | None
) -> None:
    await the_corpus(runtime)

    english = await search(runtime, "requesting leaves", language=language)
    ukrainian = await search(runtime, "відпустку", language=language)

    assert (english.route.script, english.route.configurations) == (
        QuestionScript.LATIN,
        ENGLISH,
    )
    assert await found(runtime, "requesting leaves", language=language) == ["leave.txt"]
    assert (ukrainian.route.script, ukrainian.route.configurations) == (
        QuestionScript.CYRILLIC,
        SIMPLE,
    )
    assert await found(runtime, "відпустку", language=language) == ["vidpustka.txt"]


@pytest.mark.integration
async def test_a_mixed_question_keeps_english_stemming_for_its_english_words(
    runtime: DatabaseRuntime,
) -> None:
    await the_corpus(runtime)

    # "deploying" matches "deployments" only through the English stem
    # `deploy`; "розгортає" is the exact Ukrainian form in the text.
    result = await search(runtime, "розгортає deploying")

    assert (result.route.script, result.route.configurations) == (
        QuestionScript.MIXED,
        BOTH,
    )
    assert await found(runtime, "розгортає deploying") == ["deploy.txt"]
    # A stated Ukrainian cannot narrow the route below what the text needs.
    assert await found(runtime, "розгортає deploying", language=Language.UKRAINIAN) == [
        "deploy.txt"
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("text", "script", "configurations", "names"),
    [
        (
            "E-1042",
            QuestionScript.NONE,
            BOTH,
            {"leave.txt", "vidpustka.txt", "deploy.txt"},
        ),
        ("error E-1042", QuestionScript.LATIN, ENGLISH, {"leave.txt"}),
        (
            "помилка E-1042",
            QuestionScript.CYRILLIC,
            SIMPLE,
            {"vidpustka.txt", "deploy.txt"},
        ),
    ],
)
async def test_identifiers_match_on_every_route_and_do_not_decide_it(
    runtime: DatabaseRuntime,
    text: str,
    script: QuestionScript,
    configurations: tuple[LexicalConfiguration, ...],
    names: set[str],
) -> None:
    await the_corpus(runtime)

    result = await search(runtime, text)

    assert (result.route.script, result.route.configurations) == (
        script,
        configurations,
    )
    assert set(await found(runtime, text)) == names


@pytest.mark.integration
@pytest.mark.parametrize("language", [None, Language.ENGLISH])
@pytest.mark.parametrize(
    ("text", "name"),
    [("IT", "it.txt"), ("it", "it.txt"), ("the who", "who.txt")],
)
async def test_a_question_of_english_stop_words_falls_back_to_simple(
    runtime: DatabaseRuntime, language: Language | None, text: str, name: str
) -> None:
    await a_document(
        runtime, "it.txt", title="IT department", text="The IT team fixes laptops."
    )
    await a_document(runtime, "who.txt", title="The Who", text="A rock band.")

    result = await search(runtime, text, language=language)

    assert result.route.configurations == BOTH
    assert result.route.english_words is False
    assert await found(runtime, text, language=language) == [name]


@pytest.mark.integration
async def test_a_word_joined_by_an_underscore_keeps_english_stemming(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "policy.txt", title="Політика", text="Файл leave_policy оновлено."
    )

    # `simple` alone would need "policies" exactly.
    assert await found(runtime, "leave_policies оновлено") == ["policy.txt"]


@pytest.mark.integration
async def test_a_chunk_found_by_both_configurations_is_returned_once(
    runtime: DatabaseRuntime,
) -> None:
    await the_corpus(runtime)

    result = await search(runtime, "E-1042")

    chunk_ids = [match.chunk_id for match in result.matches]
    assert len(chunk_ids) == len(set(chunk_ids)) == 3
    assert [match.rank for match in result.matches] == [1, 2, 3]
    scores = [match.score for match in result.matches]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.integration
async def test_a_better_match_the_reader_may_not_see_is_never_returned(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime,
        "hidden.txt",
        title="Deploying deployments",
        text="Розгортає deployments, розгортає deployments.",
        readers=(Subject.user(OTHER_USER),),
    )
    await the_corpus(runtime)

    assert await found(runtime, "розгортає deploying") == ["deploy.txt"]
    assert await found(runtime, "розгортає deploying", limit=1) == ["deploy.txt"]
    other = AuthorizationContext(user_id=OTHER_USER)
    assert (await found(runtime, "розгортає deploying", context=other))[0] == (
        "hidden.txt"
    )


@pytest.mark.integration
@pytest.mark.parametrize("words", ["відпустку", "leave"])
async def test_a_refused_question_on_any_route_keeps_no_question_text(
    runtime: DatabaseRuntime, words: str
) -> None:
    await the_corpus(runtime)

    async with runtime.transaction() as connection:
        with pytest.raises(InvalidLexicalQuery) as refused:
            await search_lexical_chunks(
                connection,
                text=f"{MARKER} {'-' * 40} {words}",
                context=CONTEXT,
                limit=5,
            )
        still_usable = await connection.scalar(sa.text("SELECT 1"))

    assert still_usable == 1
    assert MARKER not in str(refused.value)
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None


@pytest.mark.integration
async def test_both_routes_read_both_gin_indexes(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO documents (document_id, source_id, external_id, title,"
                " content_version, content_fingerprint, authorization_version,"
                " authorization_fingerprint, availability, created_at, updated_at)"
                " SELECT gen_random_uuid(), :source, 'doc-' || i, 'Document ' || i,"
                " 1, repeat('a', 64), 1, repeat('b', 64), 'available', now(), now()"
                " FROM generate_series(1, 2000) AS i"
            ),
            {"source": SOURCE.value},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO chunks (chunk_id, document_id, content_version, ordinal,"
                " text, heading_path, token_count, content_fingerprint)"
                " SELECT gen_random_uuid(), document_id, 1, 0,"
                " 'filler words для заповнення ' || external_id, NULL, 4,"
                " repeat('c', 64) FROM documents"
            )
        )
        await connection.execute(
            sa.text(
                "INSERT INTO access_grants (grant_id, document_id, subject_kind,"
                " subject_id, granted_at) SELECT gen_random_uuid(), document_id,"
                " 'public', NULL, now() FROM documents"
            )
        )
        await connection.execute(sa.text("ANALYZE chunks, documents, access_grants"))
        statement = lexical_search_statement(
            BOTH,
            text="розгортає deploying",
            eligibility=eligible_chunks_predicate(CONTEXT),
            limit=10,
        )
        compiled = statement.compile(dialect=postgresql.dialect())
        explained = await connection.exec_driver_sql(
            f"EXPLAIN {compiled}", compiled.params
        )
        plan = "\n".join(explained.scalars().all())

    assert "ix_chunks_search_en" in plan, plan
    assert "ix_chunks_search_simple" in plan, plan
```

**Check:**

```bash
uv run pytest tests/test_lexical_routing.py tests/test_lexical_search.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_language_aware_lexical_search_integration.py -q -m integration
```

Expected: 95 passed (44 routing and 51 search), then 20 passed.

**Deliberate break:** make the router trust a stated language blindly. In `route_lexical_question`, replace

```python
    if stated is not None:
        wanted.update(_STATED[stated])
```

with

```python
    if stated is not None and _STATED[stated]:
        wanted = set(_STATED[stated])
```

Run both test commands again. Expected: 5 failures, among them `test_a_wrong_stated_language_does_not_lose_english_stemming` and `test_a_mixed_question_keeps_english_stemming_for_its_english_words`. Undo the change and confirm everything passes again.

### Step 5 — No coding in this step: see the routes for yourself

This step shows, in Python and in plain SQL, why each Question gets its route.

**Action:** run the Step 2 command again, then:

```bash
docker exec knowledge-service-postgres psql -U test_user -d knowledge_service_test -At \
  -c "SELECT numnode(websearch_to_tsquery('english', 'IT')), websearch_to_tsquery('simple', 'IT')" \
  -c "SELECT websearch_to_tsquery('english', 'розгортає deploying'), websearch_to_tsquery('simple', 'розгортає deploying')" \
  -c "SELECT websearch_to_tsquery('english', 'leave_policies оновлено'), websearch_to_tsquery('simple', 'leave_policies оновлено')"
```

The reference run printed:

```text
NOTICE:  text-search query contains only stop words or doesn't contain lexemes, ignored
0|'it'
'розгортає' & 'deploy'|'розгортає' & 'deploying'
'leav' <-> 'polici' & 'оновлено'|'leave' <-> 'policies' & 'оновлено'
```

How to read it:

- English reads "IT" as nothing: `numnode` counts the words in the query, and it is 0. `simple` keeps `'it'`. This is why the search adds `simple` for such a Question.
- In the mixed Question, the Ukrainian word is the same in both lists. Only the English word differs: `deploy` matches "deployments", `deploying` does not.
- PostgreSQL splits `leave_policies` into two words, and English stems both. This is why an underscore alone does not make an identifier.

Write in the evidence which of your Step 1 predictions were right.

**Check:** your output matches the reference, and your evidence names one prediction you got wrong, or says that none were.

### Step 6 — No coding in this step: gates, docs, evidence, and progress

This step proves everything still works, updates the one architecture line the change made out of date, and records the result.

**Action 1:** in `docs/architecture.md`, change the `retrieval` row of the "Code layout" table to:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `MetadataCondition` |
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_lexical_routing.py tests/test_lexical_search.py -q
make test-integration
make format-check
make lint
make typecheck
make check
make docs-check
```

Expected: all pass. The reference run gave `make check` 1229 passed, 3 deselected, and `make test-integration` 256 passed.

**Action 3:** complete the evidence file:

- **Design:** the three signals and the table, why no signal is trusted alone, why not `detect_language`, how two lists are searched and scored, and the stop-word question.
- **Verification:** the commands and counts, a table of Questions with their routes and what they found, the Step 5 output, the deliberate break from Step 4, and the known limits from "Before you begin".
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M05-T03 row to `docs/curriculum/PROGRESS.md`, set the next task to M05-T04, and commit with `m05-t03: route language aware lexical queries`.

## Reflection questions

1. What does `search_lexical_chunks` hide from the code that calls it? List what a caller never has to decide, such as which word list to read or what to do with stop words.
2. Someone wants to save the second index lookup by using only the stated language when it is given. Which Questions would then lose results, and which test would catch it?
3. A Ukrainian dictionary is added later, so `search_simple` starts to differ from `search_en` for Cyrillic words. Which rules in the routing table would you check again, and why?
4. The same Chunk can score higher when both lists are searched. Why does that happen, and why is it a problem only if scores from different Questions or routes are compared?
5. What set of real Questions would show whether the router chooses well? Think about who types them, in which languages, and how you would count a wrong choice.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before running anything.
- [ ] `lexical_routing.py` chooses English, `simple`, or both from the stated language, the alphabet, and English stop words, and refuses bad input with `InvalidLexicalRoute`.
- [ ] `search_lexical_chunks` returns the route and the matches, searches two lists in one query, and keeps the checks, permissions, order, and refusal of the earlier searches.
- [ ] Tests cover explicit language, detected fallback, mixed text, identifiers, stop words, permissions, refusal, and both indexes.
- [ ] You ran the deliberate break and saw it fail, then undid it.
- [ ] You ran the Step 5 commands and compared them with your predictions.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The architecture line, evidence with the routes table, PROGRESS row, and commit are done.

When you have written the code, share it or any error output, and I will review it against this checklist.
