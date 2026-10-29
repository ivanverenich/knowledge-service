# M05-T02 — Establish the Ukrainian lexical baseline

Source task: [M05-T02](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T02-establish-the-ukrainian-lexical-baseline.md)

## What you will do

In M05-T01 you added word search for English. This lesson adds a first, honest word search for Ukrainian, and a report of where it works and where it fails.

- PostgreSQL has no Ukrainian dictionary built in. So this lesson uses the `simple` configuration, which only splits text into words and lowercases them.
- Every Chunk gets a second stored list of search words, `search_simple`, next to `search_en`.
- A new function, `search_ukrainian_chunks`, searches that list, with the same checks, permissions, and order as the English search.
- A fixture report, a small table kept in a test, shows which Ukrainian questions match and which miss, and why.

| The task asks for | Where it happens |
|---|---|
| Index Ukrainian text with the `simple` configuration | Step 2: column `chunks.search_simple` and index `ix_chunks_search_simple` |
| Search it | Step 4: `search_ukrainian_chunks` in `lexical_search.py` |
| A report of exact matches and known morphological misses | Step 5: `FIXTURE_REPORT`, copied into the evidence in Step 7 |
| Success, the highest-risk edge, one typed failure | Step 5: the report; a hidden Document is never returned; `InvalidLexicalQuery` |

This lesson includes coding: Steps 2–5 write code. Steps 1, 6, and 7 do not.

Terms used in this lesson:

- **Morphology:** how a word changes its form. Ukrainian changes word endings a lot: "відпустка", "відпустку", "відпустки" are one word in three cases.
- **Baseline:** the simplest working version, kept as a yardstick. Later lessons must beat it with measurements, not opinions.
- **`simple` configuration:** PostgreSQL's text rules that only split and lowercase. No stemming, and no stop words, so "на" and "і" are kept.
- **Unicode normalization (NFC):** some letters can be stored two ways. "й" can be one character, or "и" plus a small mark on top. They look the same but are different bytes. NFC picks the one-character form for both.
- **Fixture report:** a table in a test that lists each question, what it finds, and why. The test fails if the database stops behaving as the table says.

## Before you begin

This section explains the decisions behind the code, so the steps make sense when you reach them.

- **What already exists.** `search_en` and `search_english_chunks` from M05-T01, and two triggers that keep `search_en` up to date. This lesson copies that design for a second word list.
- **Why `simple` and not a real Ukrainian dictionary.** PostgreSQL ships stemmers for English and other languages, but not for Ukrainian. A real dictionary (Hunspell) means extra files on the database server. The task asks for a baseline first, so we can measure later improvements against it.
- **What the baseline cannot do.** The fixture report makes each limit visible:
  - "відпустки" does not find "відпустку". There is no stemming, so each case form is a separate word.
  - An ordinary apostrophe splits a word. "сім'я" becomes "сім" and "я". Only the Ukrainian apostrophe "ʼ" keeps it whole, so "сім'ї" typed with a plain apostrophe misses text written with "ʼ".
  - "на" matches almost every Chunk, because nothing is a stop word.
  - Lowercasing Cyrillic depends on the database's language settings. Our test database uses `en_US.utf8`, where it works. Under the plain `C` setting it would not, and the report test would fail.
- **Why every Chunk gets these words, not only Ukrainian ones.** A Document's language can be unknown or mixed. Choosing which word list to search is the question's job, and M05-T03 does that. So for now the list exists for every Chunk.
- **Why only one new trigger.** A Chunk trigger builds `search_simple` on every insert and update, like the English one. A title change needs no second trigger. The English Document trigger already rewrites every Chunk of that Document, and each rewrite fires the new Chunk trigger too.
- **Why one shared search function.** English and Ukrainian search differ only in two things: which word list to read and which configuration to read the question with. So `lexical_search.py` gets one private search, and the two public functions pass those two things in.
- **What the review found.** An independent review attacked the first version against the real database. Each fix below has a test that fails without it:
  - The same Ukrainian letter in two Unicode forms did not match. Now both the stored words and the question are NFC-normalized.
  - The refused-query error still kept the database error in its chain, and that error repeats the question. Now it is raised without that chain.
  - A second Document trigger doubled the work on every title change, and no test could tell it was there. It is gone.

  Some findings are known limits, written in the evidence instead of fixed:
  - A direct `UPDATE` of a Chunk row during a rename of its Document can deadlock, which M05-T01 already had. The application never does that: `reconcile_document` locks the Document first.
  - Other database errors, such as a timeout, still repeat the question text.
  - Stress marks such as "за́мок" stay inside the word, so "замок" misses it.

  The review also confirmed what held: 3,000 random hostile questions never returned a hidden Document, and nothing raised anything but `InvalidLexicalQuery`.
- **Running the tests.** Read the database URL inline in each command, so the password is never printed or saved:

  ```bash
  KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_ukrainian_lexical_search_integration.py -q -m integration
  ```

- **Files you will touch:**
  - new `migrations/versions/d8e9f0a1b2c3_simple_full_text_search.py`
  - `src/knowledge_service/persistence/schema.py` and `tests/test_indexes.py`
  - `src/knowledge_service/retrieval/lexical_search.py`
  - `tests/test_lexical_search.py` and new `tests/test_ukrainian_lexical_search_integration.py`
  - `docs/architecture.md`, a new evidence file, and at the end `PROGRESS.md`

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

This step writes down, before any code, what must always be true and what you expect to see. You will compare your guesses with the real results later.

**Action:** create `docs/evidence/M05-T02-ukrainian-lexical-baseline.md`:

```markdown
# M05-T02 — Ukrainian lexical baseline evidence

Completed: pending

## Invariant

Every Chunk's `search_simple` holds the words of its Document's current
title (weight A), its headings (B), and its text (C), NFC-normalized and
lowercased by the named `simple` configuration, with no stemming and no
stop words, whatever wrote the row. Ukrainian search returns only Chunks
the reader may see, best match first, with ties broken by Chunk ID, and no
typed text raises anything but `InvalidLexicalQuery`.

## Expected failing check

Before the change, `tests/test_ukrainian_lexical_search_integration.py`
fails to import `search_ukrainian_chunks` from
`knowledge_service.retrieval.lexical_search`.

## Predictions

Before running anything, write what you expect:

- A Document says "оплачувану відпустку". Which of these find it:
  "відпустку", "ВІДПУСТКУ", "відпустки", "оплачувана відпустка"?
- What does the question "на" find?
- The text has "сімʼї" with the Ukrainian apostrophe. Does "сім'ї" with a
  plain apostrophe find it?
- Does the Ukrainian search find the English word "leave"? And "leaves"?
- A hidden Document matches better than a visible one. What does the
  reader get?

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file exists, with your own predictions.

### Step 2 — Coding step: add the `simple` search words and their index

This step changes the database, so every Chunk carries a second list of search words.

**In plain words:** this migration teaches the database to keep a plain list of lowercased words for every Chunk, with no word endings cut off. It fills the list for the Chunks that already exist, and from then on the database updates it by itself. It also adds an index, so finding a word does not mean reading every row.

What the migration does, in order:

| Part | What it does |
|---|---|
| `simple_search_vector` | One SQL function: NFC-normalize title, headings, and text, then turn them into weighted words with `simple`. |
| `search_simple` column | Added empty, filled for existing Chunks, then marked NOT NULL. |
| `ix_chunks_search_simple` | The GIN index, with `fastupdate` off, like the English one. |
| Chunk trigger | Before every insert or update of a Chunk, reads the title (waiting for any rename in progress) and rebuilds the words. |
| `downgrade` | Removes all of it in reverse order. |

**Action:** create the file with Alembic, then replace its contents:

```bash
uv run alembic revision --rev-id d8e9f0a1b2c3 -m "simple full text search"
```

`migrations/versions/d8e9f0a1b2c3_simple_full_text_search.py`:

```python
"""simple full text search

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-10-29 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

# revision identifiers, used by Alembic.
revision: str = "d8e9f0a1b2c3"
down_revision: str | Sequence[str] | None = "c7d8e9f0a1b2"

# The Ukrainian baseline: the `simple` configuration only splits text into
# words and lowercases them. It has no stemming and no stop words, so a word
# matches only in the exact form it was written. Every Chunk gets these
# terms, whatever its language; choosing which terms to search is the
# query's job. NFC gives letters such as й and ї one form, matching the
# query, because Chunk text is stored as it arrived.
SEARCH_VECTOR_FUNCTION = """
CREATE FUNCTION simple_search_vector(title text, heading_path text, body text)
RETURNS tsvector
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN setweight(
        to_tsvector('simple'::regconfig, normalize(coalesce(title, ''), NFC)), 'A')
    || setweight(
        to_tsvector('simple'::regconfig, normalize(coalesce(heading_path, ''), NFC)),
        'B')
    || setweight(
        to_tsvector('simple'::regconfig, normalize(coalesce(body, ''), NFC)), 'C')
"""

# The same rule as the English Chunk trigger. No second Document trigger is
# needed: when a title changes, `documents_english_search` rewrites every
# Chunk of that Document, and each rewrite fires this trigger too. Like the
# English trigger, it locks the Document after the Chunk row on an update:
# a writer that updates a Chunk row while another transaction renames its
# Document can deadlock, so writers lock the Document first, as
# `reconcile_document` does.
CHUNK_TRIGGER = """
CREATE FUNCTION chunks_simple_search() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_simple := simple_search_vector(
        (SELECT title FROM documents
         WHERE document_id = NEW.document_id FOR SHARE),
        NEW.heading_path,
        NEW.text
    );
    RETURN NEW;
END
$$;
CREATE TRIGGER chunks_simple_search
BEFORE INSERT OR UPDATE ON chunks
FOR EACH ROW EXECUTE FUNCTION chunks_simple_search();
"""


def upgrade() -> None:
    """Give every Chunk weighted `simple` search terms and a GIN index."""
    op.execute(SEARCH_VECTOR_FUNCTION)
    op.add_column("chunks", sa.Column("search_simple", TSVECTOR(), nullable=True))
    op.execute(
        "UPDATE chunks SET search_simple ="
        " simple_search_vector(documents.title, chunks.heading_path, chunks.text)"
        " FROM documents WHERE documents.document_id = chunks.document_id"
    )
    op.alter_column("chunks", "search_simple", nullable=False)
    op.create_index(
        "ix_chunks_search_simple",
        "chunks",
        ["search_simple"],
        postgresql_using="gin",
        postgresql_with={"fastupdate": "off"},
    )
    op.execute(CHUNK_TRIGGER)


def downgrade() -> None:
    """Drop the `simple` search terms, their index, and their trigger."""
    op.execute("DROP TRIGGER chunks_simple_search ON chunks")
    op.execute("DROP FUNCTION chunks_simple_search()")
    op.drop_index("ix_chunks_search_simple", table_name="chunks")
    op.drop_column("chunks", "search_simple")
    op.execute("DROP FUNCTION simple_search_vector(text, text, text)")
```

`normalize(..., NFC)` is PostgreSQL's own normalization. It sits inside the function, so the backfill and the trigger both use it. The comment above the Chunk trigger records the lock-order rule from the review.

**Check:** run `make test-integration`. Expect one failure in `test_the_migrated_schema_matches_the_declared_tables`, because the code does not declare the new column yet. Step 3 fixes that.

### Step 3 — Coding step: declare the column and the index in the code

This step tells the Python code about the new column and index, so the code and the database describe the same tables.

**In plain words:** the project keeps a copy of every table's shape in Python, and a test compares it with the real database. Here you add the new column and index to that copy, and add the index to the list the project allows.

**Action 1:** in `src/knowledge_service/persistence/schema.py`, add the column right after `search_en`:

```python
    # The same, built with the `simple` configuration: lowercased words with
    # no stemming and no stop words, the Ukrainian baseline. The Chunk
    # trigger from migration d8e9f0a1b2c3 computes it on every insert and
    # update; a title change reaches it through the English Document trigger.
    sa.Column("search_simple", TSVECTOR(), nullable=False),
```

and the index right after `ix_chunks_search_en`, as the last item of the table:

```python
    sa.Index(
        "ix_chunks_search_simple",
        "search_simple",
        postgresql_using="gin",
        postgresql_with={"fastupdate": "off"},
    ),
```

**Action 2:** in `tests/test_indexes.py`, add the name to `ACCESS_PATTERN_INDEXES`, after `"ix_chunks_search_en",`:

```python
    "ix_chunks_search_simple",
```

**Check:**

```bash
uv run pytest tests/test_indexes.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_migrations_integration.py -q -m integration
```

Both pass.

### Step 4 — Coding step: add the Ukrainian search

This step adds the function the rest of the service will call to search Ukrainian text by its exact words.

**In plain words:** the English search and the new Ukrainian search now share one engine. Each public function only says which word list to read and which rules to read the question with. The engine checks the question, makes its letters use one Unicode form, asks the database for matching Chunks this person may see, and returns them best first.

| Part | What changes |
|---|---|
| Module docstring | Describes both word lists, and the Ukrainian limits in one paragraph. |
| `SIMPLE` | The `'simple'` configuration, named in the SQL like `ENGLISH`. |
| `search_ukrainian_chunks`, `ukrainian_search_statement` | New public functions for `search_simple`. |
| `search_english_chunks`, `english_search_statement` | Same behavior, now thin wrappers. |
| `_search`, `_search_statement` | The shared engine, moved out of the English functions. |
| `_search` | Also NFC-normalizes the question, and raises `InvalidLexicalQuery` outside the `except` block. |

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
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES

# Long enough for any question, short enough to bound the work per query.
MAX_QUERY_CHARACTERS = 1_000

# Named, not taken from the server's `default_text_search_config`, and the
# same configurations the stored vectors were built with.
ENGLISH = sa.literal_column("'english'::regconfig", type_=REGCONFIG)
SIMPLE = sa.literal_column("'simple'::regconfig", type_=REGCONFIG)


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
    """Return up to `limit` eligible Chunks matching English `text`, best first.

    Raises `InvalidLexicalQuery` for unusable text or a bad limit, and for
    text PostgreSQL cannot turn into a query.
    """
    return await _search(
        connection,
        chunks.c.search_en,
        ENGLISH,
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
        chunks.c.search_simple,
        SIMPLE,
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
    return _search_statement(
        chunks.c.search_en,
        ENGLISH,
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
    return _search_statement(
        chunks.c.search_simple,
        SIMPLE,
        text=text,
        eligibility=eligibility,
        limit=limit,
    )


async def _search(
    connection: AsyncConnection,
    terms: sa.Column[Any],
    configuration: sa.ColumnElement[Any],
    *,
    text: str,
    context: AuthorizationContext,
    limit: int,
) -> tuple[LexicalMatch, ...]:
    _check_text(text)
    _check_limit(limit)
    statement = _search_statement(
        terms,
        configuration,
        # One form for letters such as й and ї, which can also arrive as a
        # letter plus a combining mark; the stored terms use the same form.
        text=unicodedata.normalize("NFC", text),
        eligibility=eligible_chunks_predicate(context),
        limit=limit,
    )
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
    return tuple(
        LexicalMatch(
            rank=index,
            chunk_id=ChunkId(cast(UUID, record["chunk_id"])),
            document_id=DocumentId(cast(UUID, record["document_id"])),
            score=cast(float, record["score"]),
        )
        for index, record in enumerate(records, start=1)
    )


def _search_statement(
    terms: sa.Column[Any],
    configuration: sa.ColumnElement[Any],
    *,
    text: str,
    eligibility: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    # The text is one bound parameter, read with the configuration the
    # stored terms were built with.
    query = sa.func.websearch_to_tsquery(
        configuration, sa.bindparam("query_text", text)
    )
    score = sa.func.ts_rank(terms, query)
    return (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            score.label("score"),
        )
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(terms.bool_op("@@")(query), eligibility)
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
```

Why each choice:

- The question is normalized after the checks, so the 1,000-character limit still counts what the user typed.
- `refused = True` and a `raise` after the `try` block look odd, but they matter. A `raise` inside `except` keeps the database error attached as `__context__`, even with `from None`. That error repeats the question, and error reporters often print the whole chain.
- `records` starts as an empty tuple only so the type checker knows it always has a value.

**Check:** `uv run pyright` reports 0 errors, and the M05-T01 tests still pass:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_lexical_search_integration.py -q -m integration
```

Expected: 22 passed.

### Step 5 — Coding step: test the search and write the fixture report

This step proves the Ukrainian search keeps its promises, and records exactly what the baseline can and cannot match.

**In plain words:** the unit tests run every input check against both searches. The integration tests store a few small Ukrainian Documents and search them. One test holds the fixture report: each question, what it must find, and why, including the expected misses. Other tests check permissions, weighting, safe syntax, title changes, Unicode forms, the index, and the migration backfill.

| Test | Proves |
|---|---|
| `test_bad_text_or_limit_is_refused_before_the_database` | Typed failure for both searches, with no database call. |
| `test_the_ukrainian_query_is_a_bound_parameter_with_the_simple_configuration` | The text is data, and `'simple'` is named in the SQL. |
| `test_the_fixture_report_shows_exact_matches_and_known_misses` | The report: exact matches, case folding, phrases, apostrophes, and known misses. |
| `test_a_title_match_outranks_a_heading_match_and_a_text_match` | Weighting with Ukrainian words. |
| `test_a_better_match_the_reader_may_not_see_is_never_returned` | Permissions, the highest-risk edge. |
| `test_any_typed_text_is_read_as_words_never_as_syntax` | Safe web-search syntax. |
| `test_text_postgresql_cannot_parse_is_refused_and_the_transaction_survives` | Typed failure from PostgreSQL, with no question text anywhere in the error chain. |
| `test_decomposed_letters_match_their_composed_form` | Review fix: both Unicode forms of й and ї match. |
| `test_a_title_only_change_updates_what_the_chunks_match` | A rename updates `search_simple` through the English Document trigger. |
| `test_a_chunk_written_during_a_title_change_gets_the_new_title` | A Chunk written during a rename waits and gets the new title. |
| `test_a_direct_write_to_the_search_terms_is_replaced` | Nobody can store wrong words directly. |
| `test_the_search_reads_the_gin_index` | PostgreSQL uses `ix_chunks_search_simple`. |
| `test_the_migration_fills_the_terms_of_existing_chunks` | Chunks stored before the migration get their words. |

**Action 1:** replace `tests/test_lexical_search.py` with:

```python
"""Checks on English full-text search that need no database."""

from collections.abc import Awaitable, Callable
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
    search_ukrainian_chunks,
    ukrainian_search_statement,
)
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES

CONTEXT = AuthorizationContext(user_id=UserId(UUID(int=1)))
MARKER = "zebramarker"


@pytest.mark.parametrize("search", [search_english_chunks, search_ukrainian_chunks])
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
```

The bad-input test now runs once for each search, so it covers both public functions.

**Action 2:** create `tests/test_ukrainian_lexical_search_integration.py`:

```python
# Cyrillic letters and Ukrainian apostrophes are what these tests are about.
# ruff: noqa: RUF001, RUF003
"""Real PostgreSQL tests for the Ukrainian lexical baseline over Chunks.

`FIXTURE_REPORT` is the report the evidence copies: each query, the
Documents it finds, and why. The misses are expected; they are what the
`simple` configuration cannot do, and a later task that adds Ukrainian
morphology should turn them into matches on purpose.
"""

import asyncio
import unicodedata
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import make_url

from database_fixtures import migration_config, migration_environment
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, SourceId, UserId
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import Change, reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.retrieval.lexical_search import (
    InvalidLexicalQuery,
    LexicalMatch,
    search_ukrainian_chunks,
    ukrainian_search_statement,
)
from knowledge_service.settings import Settings

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000032")
OBSERVED_AT = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)
READER = UserId.parse("00000000-0000-0000-0000-0000000000b1")
OTHER_USER = UserId.parse("00000000-0000-0000-0000-0000000000b2")
CONTEXT = AuthorizationContext(user_id=READER)
MARKER = "маркерзапиту"

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/dovidnyk', 'Dovidnyk', true, now(), now())"
)
INSERT_CHUNK = sa.text(
    "INSERT INTO chunks (chunk_id, document_id, content_version, ordinal, text,"
    " heading_path, token_count, content_fingerprint) VALUES (gen_random_uuid(),"
    " :document_id, 1, 1, 'Додаткові нотатки.', NULL, 2, repeat('c', 64))"
)
# Chunks whose stored terms differ from what their current title, headings,
# and text give, in either configuration.
STALE_CHUNKS = sa.text(
    "SELECT count(*) FROM chunks JOIN documents USING (document_id)"
    " WHERE search_simple IS DISTINCT FROM"
    " simple_search_vector(documents.title, chunks.heading_path, chunks.text)"
    " OR search_en IS DISTINCT FROM"
    " english_search_vector(documents.title, chunks.heading_path, chunks.text)"
)

# The fixture corpus. "’" is U+2019 and "ʼ" is U+02BC, the apostrophe the
# Ukrainian standard prefers; the default parser keeps only the second inside
# a word.
CORPUS = {
    "vidpustka.txt": (
        "Відпустка",
        "Працівник має право на щорічну оплачувану відпустку."
        " Працівники узгоджують їхню відпустку з керівником.",
    ),
    "likarnyanyi.md": (
        "Лікарняний",
        "# Тимчасова непрацездатність на роботі\n\n"
        "Лікарняний оформлює лікар в електронній системі.",
    ),
    "simya.txt": (
        "Сімейні обставини",
        "Додаткову відпустку надають у зв’язку з народженням дитини в сімʼї.",
    ),
    "leave.txt": ("Time off", "Employees request paid leave every year."),
}

# (query, Documents found best first, why)
FIXTURE_REPORT: list[tuple[str, list[str], str]] = [
    ("відпустку", ["vidpustka.txt", "simya.txt"], "exact form in the text"),
    ("ВІДПУСТКА", ["vidpustka.txt"], "exact form in a title, any letter case"),
    ("ЇХНЮ", ["vidpustka.txt"], "Ukrainian capitals ї, і lowercase"),
    ('"оплачувану відпустку"', ["vidpustka.txt"], "exact phrase"),
    ("непрацездатність", ["likarnyanyi.md"], "exact form in a heading"),
    ("зв'язку", ["simya.txt"], "both apostrophes split the word the same way"),
    ("сімʼї", ["simya.txt"], "the U+02BC apostrophe keeps the word whole"),
    ("відпустки", [], "miss: another case of відпустка, no stemming"),
    ("лікарняного", [], "miss: another case of лікарняний, no stemming"),
    ("оплачувана відпустка", [], "miss: the text has оплачувану"),
    ("сім'ї", [], "miss: an ASCII apostrophe splits the word, the text has ʼ"),
    ("leave", ["leave.txt"], "any language matches in its exact form"),
    ("leaves", [], "miss: no English stemming either"),
    ("every", ["leave.txt"], "no stop words: English every is kept"),
    (
        "на",
        ["likarnyanyi.md", "vidpustka.txt"],
        "noise: no stop words, so на matches every Chunk with it",
    ),
]


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
    """A one-Chunk Document; Markdown when the text starts with a heading."""
    media_type = "text/markdown" if text.startswith("#") else "text/plain"
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version="v1",
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


async def search(
    runtime: DatabaseRuntime,
    text: str,
    *,
    limit: int = 10,
    context: AuthorizationContext = CONTEXT,
) -> tuple[LexicalMatch, ...]:
    async with runtime.transaction() as connection:
        return await search_ukrainian_chunks(
            connection, text=text, context=context, limit=limit
        )


async def names(runtime: DatabaseRuntime, text: str, **kwargs: Any) -> list[str]:
    matches = await search(runtime, text, **kwargs)
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.text("SELECT document_id, external_id FROM documents")
        )
        by_id = {row.document_id: row.external_id for row in rows}
    return [by_id[match.document_id.value] for match in matches]


async def stale_chunks(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        return int(await connection.scalar(STALE_CHUNKS) or 0)


@pytest.mark.integration
async def test_the_fixture_report_shows_exact_matches_and_known_misses(
    runtime: DatabaseRuntime,
) -> None:
    for name, (title, text) in CORPUS.items():
        await a_document(runtime, name, title=title, text=text)

    report = [(query, await names(runtime, query)) for query, _, _ in FIXTURE_REPORT]

    assert report == [(query, found) for query, found, _ in FIXTURE_REPORT]


@pytest.mark.integration
async def test_a_title_match_outranks_a_heading_match_and_a_text_match(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "text.txt", title="Нотатки", text="Загальні нотатки про адаптацію."
    )
    await a_document(
        runtime, "heading.md", title="Посібник", text="# Адаптацію\n\nЗагальні нотатки."
    )
    await a_document(
        runtime, "title.txt", title="Адаптацію", text="Загальні нотатки для команди."
    )

    matches = await search(runtime, "адаптацію")

    assert await names(runtime, "адаптацію") == ["title.txt", "heading.md", "text.txt"]
    assert len({match.score for match in matches}) == 3
    assert [match.rank for match in matches] == [1, 2, 3]


@pytest.mark.integration
async def test_a_better_match_the_reader_may_not_see_is_never_returned(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime,
        "hidden.md",
        title="Зарплата",
        text="# Зарплата\n\nЗарплата для кожного рівня, зарплата щомісяця.",
        readers=[Subject.user(OTHER_USER)],
    )
    await a_document(
        runtime, "visible.txt", title="Виплати", text="Зарплата виплачується щомісяця."
    )

    assert await names(runtime, "зарплата") == ["visible.txt"]
    assert await names(runtime, "зарплата", limit=1) == ["visible.txt"]
    other = AuthorizationContext(user_id=OTHER_USER)
    assert await names(runtime, "зарплата", context=other) == [
        "hidden.md",
        "visible.txt",
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    "text",
    [
        "відпустка & | ! ( ) :* <-> право",
        "'; DROP TABLE chunks; --",
        "відпустку:*",
        '"незакрита лапка',
    ],
)
async def test_any_typed_text_is_read_as_words_never_as_syntax(
    runtime: DatabaseRuntime, text: str
) -> None:
    await a_document(
        runtime, "vidpustka.txt", title="Відпустка", text="Право на відпустку."
    )

    await search(runtime, text)

    assert await names(runtime, "відпустку") == ["vidpustka.txt"]


@pytest.mark.integration
async def test_text_postgresql_cannot_parse_is_refused_and_the_transaction_survives(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "vidpustka.txt", title="Відпустка", text="Право на відпустку."
    )

    async with runtime.transaction() as connection:
        with pytest.raises(InvalidLexicalQuery) as refused:
            await search_ukrainian_chunks(
                connection, text=MARKER + " " + "-" * 40, context=CONTEXT, limit=10
            )
        after = await search_ukrainian_chunks(
            connection, text="відпустку", context=CONTEXT, limit=10
        )

    assert len(after) == 1
    # Error reporters walk the whole chain; the database error repeats the text.
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None
    assert MARKER not in str(refused.value)


@pytest.mark.integration
async def test_decomposed_letters_match_their_composed_form(
    runtime: DatabaseRuntime,
) -> None:
    # й and ї can be stored as one character or as a letter plus a mark.
    composed = "Йод і їхня відпустка."
    decomposed = unicodedata.normalize("NFD", "Йодний розчин і їхній склад.")
    await a_document(runtime, "composed.txt", title="Склад", text=composed)
    await a_document(runtime, "decomposed.txt", title="Розчин", text=decomposed)

    assert await names(runtime, unicodedata.normalize("NFD", "йод")) == ["composed.txt"]
    assert await names(runtime, "їхній") == ["decomposed.txt"]
    assert await names(runtime, "йодний") == ["decomposed.txt"]


@pytest.mark.integration
async def test_a_title_only_change_updates_what_the_chunks_match(
    runtime: DatabaseRuntime,
) -> None:
    text = "Загальні нотатки для команди."
    await a_document(runtime, "notes.txt", title="Адаптація", text=text)

    renamed = await reconcile_document(
        runtime,
        a_record("notes.txt", title="Переїзд", text=text),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )

    assert renamed.change is Change.TITLE
    assert await names(runtime, "адаптація") == []
    assert await names(runtime, "переїзд") == ["notes.txt"]
    assert await stale_chunks(runtime) == 0


@pytest.mark.integration
async def test_a_chunk_written_during_a_title_change_gets_the_new_title(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(
        runtime, "notes.txt", title="Адаптація", text="Загальні нотатки."
    )
    async with (
        runtime.engine.connect() as renaming,
        runtime.engine.connect() as writing,
    ):
        await renaming.begin()
        await renaming.execute(
            sa.text("UPDATE documents SET title = 'Переїзд' WHERE document_id = :id"),
            {"id": document_id.value},
        )

        async def add_chunk() -> None:
            async with writing.begin():
                await writing.execute(INSERT_CHUNK, {"document_id": document_id.value})

        adding = asyncio.create_task(add_chunk())
        finished_early, _ = await asyncio.wait({adding}, timeout=0.5)
        await renaming.commit()
        await adding

    assert not finished_early
    assert await stale_chunks(runtime) == 0
    assert await names(runtime, "переїзд") == ["notes.txt", "notes.txt"]


@pytest.mark.integration
async def test_a_direct_write_to_the_search_terms_is_replaced(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(runtime, "notes.txt", title="Адаптація", text="Загальні нотатки.")

    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text("UPDATE chunks SET search_simple = to_tsvector('simple', 'фальш')")
        )

    assert await names(runtime, "фальш") == []
    assert await stale_chunks(runtime) == 0


@pytest.mark.integration
async def test_the_search_reads_the_gin_index(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO documents (document_id, source_id, external_id, title,"
                " content_version, content_fingerprint, authorization_version,"
                " authorization_fingerprint, availability, created_at, updated_at)"
                " SELECT gen_random_uuid(), :source, 'doc-' || i, 'Документ ' || i,"
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
                " 'слова для заповнення ' || external_id, NULL, 4, repeat('c', 64)"
                " FROM documents"
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
        statement = ukrainian_search_statement(
            text="переїзд",
            eligibility=eligible_chunks_predicate(CONTEXT),
            limit=10,
        )
        compiled = statement.compile(dialect=postgresql.dialect())
        explained = await connection.exec_driver_sql(
            f"EXPLAIN {compiled}", compiled.params
        )
        plan = explained.scalars().all()

    assert any("ix_chunks_search_simple" in line for line in plan), "\n".join(plan)


@pytest.mark.integration
def test_the_migration_fills_the_terms_of_existing_chunks(
    migrated_database: str,
) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    with migration_environment(url):
        command.downgrade(config, "c7d8e9f0a1b2")
    engine = create_engine(migrated_database)
    try:
        with engine.begin() as connection:
            connection.execute(INSERT_SOURCE, {"source_id": SOURCE.value})
            connection.execute(
                sa.text(
                    "INSERT INTO documents (document_id, source_id, external_id,"
                    " title, content_version, content_fingerprint,"
                    " authorization_version, authorization_fingerprint,"
                    " availability, created_at, updated_at) VALUES"
                    " (gen_random_uuid(), :source, 'old.txt', 'Переїзд', 1,"
                    " repeat('a', 64), 1, repeat('b', 64), 'available', now(), now())"
                ),
                {"source": SOURCE.value},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO chunks (chunk_id, document_id, content_version,"
                    " ordinal, text, heading_path, token_count, content_fingerprint)"
                    " SELECT gen_random_uuid(), document_id, 1, 0,"
                    " 'Переїзд до нового офісу.', 'Офіси', 4, repeat('c', 64)"
                    " FROM documents"
                )
            )
        with migration_environment(url):
            command.upgrade(config, "head")
        with engine.connect() as connection:
            stored = connection.scalar(
                sa.text("SELECT search_simple::text FROM chunks")
            )
    finally:
        engine.dispose()

    assert stored == "'до':4C 'нового':5C 'офіси':2B 'офісу':6C 'переїзд':1A,3C"
```

The `# ruff: noqa: RUF001, RUF003` line at the top turns off one lint rule for this file only. That rule warns about letters that look like Latin ones, such as Cyrillic "а", and about unusual apostrophes. Here they are exactly what the tests are about. Other files in the project use the same rule one line at a time.

The report expects "відпустку" to find two Documents: the leave policy, and the family one that says "Додаткову відпустку".

**Check:**

```bash
uv run pytest tests/test_lexical_search.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_ukrainian_lexical_search_integration.py -q -m integration
```

Expected: 29 passed, then 14 passed. To see a test do its job, change `'simple'` to `'english'` in the `SIMPLE` line of `lexical_search.py`. The fixture report fails, because "every" becomes a stop word and "leave" becomes `leav`. Undo it afterwards.

### Step 6 — No coding in this step: see the Ukrainian words for yourself

This step shows, in plain SQL, what PostgreSQL stores for Ukrainian text and what it makes of a question.

**Action:** after Step 5 the test database has the migration applied. Run:

```bash
docker exec knowledge-service-postgres psql -U test_user -d knowledge_service_test \
  -c "SELECT simple_search_vector('Відпустка', 'Правила', 'Працівник має право на щорічну оплачувану відпустку.')" \
  -c "SELECT websearch_to_tsquery('simple', 'ВІДПУСТКИ \"оплачувану відпустку\" -лікарняний or відгул')" \
  -c "SELECT websearch_to_tsquery('simple', 'сім''ї')" \
  -c "SELECT websearch_to_tsquery('simple', 'сімʼї')"
```

The reference run printed:

```text
'відпустка':1A 'відпустку':9C 'має':4C 'на':6C 'оплачувану':8C 'правила':2B 'право':5C 'працівник':3C 'щорічну':7C
'відпустки' & 'оплачувану' <-> 'відпустку' & !'лікарняний' | 'відгул'
'сім' <-> 'ї'
'сімʼї'
```

How to read it:

- Every word is kept whole and lowercased. Compare with English, where "every" became `everi`.
- "на" is stored. In English, a word that common would be dropped.
- "відпустка" (title) and "відпустку" (text) are two different words here. The question "ВІДПУСТКИ" is a third, so it would match neither.
- A plain apostrophe turns "сім'ї" into two words that must stand next to each other. The Ukrainian apostrophe "ʼ" keeps one word, so the two questions can never match the same text.

Write in the evidence which of your Step 1 predictions were right.

**Check:** your output matches the reference, and your evidence names one prediction you got wrong, or says that none were.

### Step 7 — No coding in this step: gates, docs, evidence, and progress

This step proves everything still works, updates the two lines of the architecture the change made out of date, and records the result.

**Action 1:** in `docs/architecture.md`, change the `retrieval` row of the "Code layout" table to:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_english_chunks`, `search_ukrainian_chunks`, `MetadataCondition` |
```

and in the `chunks` row of the data-model table, replace the sentence about `search_en` with:

```markdown
`search_en`, its English search terms (title A, headings B, text C), and `search_simple`, the same words only lowercased (the Ukrainian baseline: no stemming, no stop words), are computed by database triggers on every insert and update of the Chunk and when the Document's title changes; the application never writes them.
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_lexical_search.py -q
make test-integration
make format-check
make lint
make typecheck
make check
make docs-check
```

Expected: all pass. The reference run gave `make check` 1143 passed, 3 deselected, and `make test-integration` 236 passed.

**Action 3:** complete the evidence file:

- **Design:** the column, why every Chunk gets it, why there is only one new trigger, NFC, and the shared search.
- **Verification:** the commands and counts, the fixture report as a table (question, found, why), the Step 6 output, the deliberate break from Step 5, and the known limits from "Before you begin".
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M05-T02 row to `docs/curriculum/PROGRESS.md`, set the next task to M05-T03, and commit with `m05-t02: establish the ukrainian lexical baseline`.

## Reflection questions

1. What does `search_ukrainian_chunks` hide from the code that calls it? List what a caller never has to think about, such as Unicode forms or which word list is read.
2. The Ukrainian words are kept up to date by the English Document trigger. Which later change, made to speed things up, could silently break that, and which test would catch it?
3. The fixture report expects some misses, such as "відпустки". If a later lesson adds a Ukrainian dictionary, those misses become matches and the test fails. Is that failure useful or annoying, and what should the person do when they see it?
4. "на" matches almost every Chunk. What does that do to the results of a long question that contains it, and how would you measure the harm?
5. What question set would prove this baseline is good enough, or not good enough, for Ukrainian users? Think about who writes the questions and how many forms of each word they use.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before running anything.
- [ ] The migration adds `search_simple`, fills existing Chunks, adds the GIN index and the Chunk trigger; downgrade removes them.
- [ ] `schema.py` declares the column and the index, and the drift test passes.
- [ ] `search_ukrainian_chunks` shares the English checks, permissions, and order, and NFC-normalizes the question.
- [ ] The fixture report shows exact matches and known misses, and the other tests cover permissions, syntax, Unicode forms, title changes, the index, and the backfill.
- [ ] You ran the Step 6 queries and compared them with your predictions.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The architecture lines, evidence with the fixture report table, PROGRESS row, and commit are done.

When you have written the code, share it or any error output, and I will review it against this checklist.
