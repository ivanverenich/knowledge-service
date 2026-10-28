# M05-T01 — Index English full-text search

Source task: [M05-T01](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T01-index-english-full-text-search.md)

## What you will do

So far, search finds Chunks by meaning: vectors. This lesson adds search by words, the way a search engine matches the words you type.

- PostgreSQL will keep a list of English search words for every Chunk, built from the Document's title, the headings, and the text.
- A word in the title counts more than the same word in a heading, and a heading counts more than the text.
- A new function, `search_english_chunks`, finds the Chunks whose words match a question, best match first, with the same permission check as vector search.

| The task asks for | Where it happens |
|---|---|
| Weighted English search words from title, headings, and body | Step 2: a database column `chunks.search_en`, filled by the database itself |
| A GIN index and an explicit configuration | Step 2: index `ix_chunks_search_en`; the `english` configuration is always named |
| Stemming, weighting, exact terms, safe web-search syntax | Steps 4–5: the search function and its tests |
| Typed failure, highest-risk edge, success | Step 5: `InvalidLexicalQuery`; a hidden Document is never returned; matches come back in the right order |

This lesson includes coding: Steps 2–5 write code. Steps 1, 6, and 7 do not.

Terms used in this lesson:

- **Full-text search:** finding text by its words, not by exact characters. "Requesting leave" should find "requests for leave".
- **Stemming:** cutting a word to its root, so "requesting", "requests", and "requested" all become `request`.
- **Stop words:** very common words like "the" and "of". PostgreSQL drops them, because they match almost everything.
- **tsvector:** PostgreSQL's stored list of a text's search words, each with its position and weight. Think of it as the index card for one Chunk.
- **tsquery:** the question turned into the same kind of words, joined by AND, OR, NOT, and "next to".
- **Weight:** a label A, B, C, or D on each word. The ranking function counts A highest.
- **GIN index:** an index made for lists of words. It answers "which rows contain this word?" without reading every row, like the index at the back of a book.
- **Trigger:** a small function the database runs by itself whenever a row is inserted or changed.

## Before you begin

This section explains the few decisions behind the code, so the steps make sense when you reach them.

- **What already exists.** Vector search (`search_similar_chunks`) ranks Chunks by meaning and checks permissions in the same database query. The new word search copies that pattern: one query, permission check included.
- **Why the database builds the search words.** The words must come from the Document's title, but a Chunk row does not hold the title. If Python built them, every place that writes Chunks or changes a title would have to remember to update them. Instead, two triggers do it:
  - When a Chunk is inserted or changed, the database reads its Document's title and builds the Chunk's search words.
  - When a Document's title changes, the database rebuilds the search words of all its Chunks.

  So no write path can forget. The application never writes `search_en` itself.
- **Why the configuration is always named.** PostgreSQL has a default language setting, and it can differ between servers. The code always writes `'english'` explicitly, so the same text always gives the same words. PostgreSQL also requires this for a stored value: without the name, the result could change if the setting changed.
- **Why "web-search" syntax.** People type questions, not query code. `websearch_to_tsquery` reads text the way a search box does: `"quoted words"` must appear together, `or` means either, and `-word` means "without". It ignores any other punctuation, so typed text cannot cause a syntax error. A stricter function, `to_tsquery`, would fail on a stray `&` or `(`.
- **Why the index has `fastupdate` off.** By default, a GIN index first puts new rows in a waiting list and merges them later. While that list is long, PostgreSQL may skip the index and read the whole table. The tests showed exactly that. With `fastupdate` off, a new Chunk goes straight into the index, which costs a little on every write but keeps searches fast and predictable.
- **What the review found.** An independent review attacked the first version against the real database. Three things broke, and each is fixed below with a test that fails without the fix:
  - A pasted line of 33 dashes made PostgreSQL fail with a raw error that repeated the query text. Now that becomes `InvalidLexicalQuery`, and the caller's transaction keeps working.
  - A Chunk written while another transaction was renaming its Document kept the old title. Now the Chunk trigger waits for the rename to finish before it reads the title.
  - A direct `UPDATE` of `search_en` stayed wrong forever. Now the trigger rebuilds the words on every update of a Chunk.

  The review also confirmed what held: 3,000 random queries with hidden exact matches never returned a hidden Chunk, and no other text caused a raw error.
- **Running the tests.** The integration tests need the test database URL. Read it inline in each command, so the password is never printed or saved:

  ```bash
  KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_lexical_search_integration.py -q -m integration
  ```

- **Files you will touch:**
  - new `migrations/versions/c7d8e9f0a1b2_english_full_text_search.py`
  - `src/knowledge_service/persistence/schema.py` and `tests/test_indexes.py`
  - new `src/knowledge_service/retrieval/lexical_search.py`
  - new `tests/test_lexical_search.py` and `tests/test_lexical_search_integration.py`
  - `docs/architecture.md`, a new evidence file, and at the end `PROGRESS.md`

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

This step writes down, before any code, what must always be true and what you expect to see. You will compare your guesses with the real results later.

**Action:** create `docs/evidence/M05-T01-english-full-text-search.md`:

```markdown
# M05-T01 — English full-text search evidence

Completed: pending

## Invariant

Every Chunk's `search_en` holds the English search words of its Document's
current title (weight A), its headings (B), and its text (C), built with the
named `english` configuration, whatever wrote the row. English search returns
only Chunks the reader may see, best match first, with ties broken by Chunk
ID, and no typed text raises anything but `InvalidLexicalQuery`.

## Expected failing check

Before the change, `tests/test_lexical_search.py` and
`tests/test_lexical_search_integration.py` fail to import
`knowledge_service.retrieval.lexical_search`.

## Predictions

Before running anything, write what you expect:

- What does "requesting leaves" match, and what does "the of and" match?
- The word "onboarding" is in one Document's title, in another's heading,
  and in a third's text. In what order do they come back?
- What does `"paid leave"` (with quotes) match that `paid leave` does not?
- What happens when a user types `'; DROP TABLE chunks; --`?
- A hidden Document matches better than a visible one. What does the reader
  get?

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file exists, with your own predictions.

### Step 2 — Coding step: add the search words, the triggers, and the index

This step changes the database, so every Chunk carries its English search words.

**In plain words:** this migration teaches the database to keep a list of search words for every Chunk. It fills the list for the Chunks that already exist, and from then on the database updates it by itself whenever a Chunk is written or its Document's title changes. It also adds an index, so finding a word does not mean reading every row.

What the migration does, in order:

| Part | What it does |
|---|---|
| `english_search_vector` | One SQL function that turns title, headings, and text into weighted search words. Both triggers use it, so the rule lives in one place. |
| `search_en` column | Added empty, filled for existing Chunks, then marked NOT NULL. |
| `ix_chunks_search_en` | The GIN index, with `fastupdate` off. |
| Chunk trigger | Before every insert or update of a Chunk, reads the title (waiting for any rename in progress) and rebuilds the words. |
| Document trigger | After a title really changes, rebuilds the words of that Document's Chunks. |
| `downgrade` | Removes all of it in reverse order. |

**Action:** create `migrations/versions/c7d8e9f0a1b2_english_full_text_search.py`:

```python
"""english full text search

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-10-28 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

# revision identifiers, used by Alembic.
revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "b6c7d8e9f0a1"

# The one place that says how a Chunk becomes English search terms. The
# configuration is named, never taken from the server's default, so the
# same text always gives the same terms.
SEARCH_VECTOR_FUNCTION = """
CREATE FUNCTION english_search_vector(title text, heading_path text, body text)
RETURNS tsvector
LANGUAGE sql IMMUTABLE PARALLEL SAFE
RETURN setweight(to_tsvector('english'::regconfig, coalesce(title, '')), 'A')
    || setweight(to_tsvector('english'::regconfig, coalesce(heading_path, '')), 'B')
    || setweight(to_tsvector('english'::regconfig, coalesce(body, '')), 'C')
"""

# A Chunk row holds no title, so a trigger reads it from the Document. FOR
# SHARE waits for a title change still in progress, so a Chunk written at the
# same time never keeps the old title. It runs on every insert and update,
# so no write can leave `search_en` different from what it computes.
CHUNK_TRIGGER = """
CREATE FUNCTION chunks_english_search() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_en := english_search_vector(
        (SELECT title FROM documents
         WHERE document_id = NEW.document_id FOR SHARE),
        NEW.heading_path,
        NEW.text
    );
    RETURN NEW;
END
$$;
CREATE TRIGGER chunks_english_search
BEFORE INSERT OR UPDATE ON chunks
FOR EACH ROW EXECUTE FUNCTION chunks_english_search();
"""

# A title-only change rewrites no Chunk, so the Document refreshes them.
DOCUMENT_TRIGGER = """
CREATE FUNCTION documents_english_search() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    UPDATE chunks
    SET search_en = english_search_vector(NEW.title, heading_path, text)
    WHERE document_id = NEW.document_id;
    RETURN NULL;
END
$$;
CREATE TRIGGER documents_english_search
AFTER UPDATE OF title ON documents
FOR EACH ROW WHEN (OLD.title IS DISTINCT FROM NEW.title)
EXECUTE FUNCTION documents_english_search();
"""


def upgrade() -> None:
    """Give every Chunk a weighted English search vector and a GIN index."""
    op.execute(SEARCH_VECTOR_FUNCTION)
    op.add_column("chunks", sa.Column("search_en", TSVECTOR(), nullable=True))
    op.execute(
        "UPDATE chunks SET search_en ="
        " english_search_vector(documents.title, chunks.heading_path, chunks.text)"
        " FROM documents WHERE documents.document_id = chunks.document_id"
    )
    op.alter_column("chunks", "search_en", nullable=False)
    op.create_index(
        "ix_chunks_search_en",
        "chunks",
        ["search_en"],
        postgresql_using="gin",
        postgresql_with={"fastupdate": "off"},
    )
    op.execute(CHUNK_TRIGGER)
    op.execute(DOCUMENT_TRIGGER)


def downgrade() -> None:
    """Drop the English search vectors, their index, and their triggers."""
    op.execute("DROP TRIGGER documents_english_search ON documents")
    op.execute("DROP FUNCTION documents_english_search()")
    op.execute("DROP TRIGGER chunks_english_search ON chunks")
    op.execute("DROP FUNCTION chunks_english_search()")
    op.drop_index("ix_chunks_search_en", table_name="chunks")
    op.drop_column("chunks", "search_en")
    op.execute("DROP FUNCTION english_search_vector(text, text, text)")
```

Two details are worth noticing. `FOR SHARE` in the Chunk trigger makes the insert wait while another transaction is renaming the same Document. `WHEN (OLD.title IS DISTINCT FROM NEW.title)` keeps the Document trigger quiet when a reconcile writes the same title again, so an unchanged sync still rewrites nothing.

**Check:** run `make test-integration`. The tests apply the migration to the test database. Expect one failure in `test_the_migrated_schema_matches_the_declared_tables`, because the code does not declare the new column yet. Step 3 fixes that.

### Step 3 — Coding step: declare the column and the index in the code

This step tells the Python code about the new column and index, so the code and the database describe the same tables.

**In plain words:** the project keeps a copy of every table's shape in Python, and a test compares it with the real database. Here you add the new column and index to that copy, and you add the index to the list of indexes the project allows.

**Action 1:** in `src/knowledge_service/persistence/schema.py`, change the import line to:

```python
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
```

In the `chunks` table, add the column after `source_anchor`:

```python
    # English full-text search: the Document's title (weight A), the
    # headings (B), and the text (C). Never written by the application:
    # triggers from migration c7d8e9f0a1b2 compute it on every insert and
    # update of the Chunk, and again when the Document's title changes.
    sa.Column("search_en", TSVECTOR(), nullable=False),
```

and add the index as the last item of the table, after `ck_chunks_token_count`:

```python
    # Without the pending list (fastupdate), a new Chunk goes straight into
    # the index, so the planner keeps choosing it between vacuums.
    sa.Index(
        "ix_chunks_search_en",
        "search_en",
        postgresql_using="gin",
        postgresql_with={"fastupdate": "off"},
    ),
```

**Action 2:** in `tests/test_indexes.py`, add the index name to `ACCESS_PATTERN_INDEXES`, in alphabetical order:

```python
    "ix_chunks_search_en",
```

**Check:** `uv run pytest tests/test_indexes.py -q` passes, and the drift test from Step 2 passes:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_migrations_integration.py -q -m integration
```

### Step 4 — Coding step: write the English search

This step adds the function the rest of the service will call to search by words.

**In plain words:** this file is the word search itself. It first checks that the question is usable text and the limit makes sense. Then it asks the database for the Chunks whose words match the question and that this person may see, best match first. It returns their positions, IDs, and scores.

| Part | What it does |
|---|---|
| `_check_text`, `_check_limit` | Refuse blank, non-text, too long (over 1,000 characters), NUL, or broken Unicode text, and a bad limit, with `InvalidLexicalQuery`, before the database. |
| `english_search_statement` | The SQL: match with `@@`, score with `ts_rank`, permission check in the same WHERE, order by score then Chunk ID, limit. |
| `search_english_chunks` | Runs the statement inside a savepoint and turns PostgreSQL's "tsquery stack too small" error into `InvalidLexicalQuery`. |
| `LexicalMatch` | One result: position, Chunk ID, Document ID, score. |

**Action:** create `src/knowledge_service/retrieval/lexical_search.py`:

```python
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
```

Why each choice:

- `ENGLISH` is written into the SQL as a constant, not sent as a value, because it is never user input.
- The question text is one bound parameter, so it reaches PostgreSQL as data, never as SQL.
- The savepoint (`begin_nested`) means a refused query undoes only itself. Without it, the caller's whole transaction would be broken after the error.
- `LexicalMatch` is small on purpose. M05-T04 turns these matches into full Candidates with text and provenance.

**Check:** `uv run pyright` reports 0 errors.

### Step 5 — Coding step: test the search

This step proves the search keeps each promise: stemming, weighting, exact phrases, safe syntax, and permissions.

**In plain words:** these tests store a few small Documents and search them. They check that word forms match, that title matches come first, that quoted words must appear together, that strange typed text never breaks anything, and that a Document the reader may not see never shows up. Other tests prove the stored words stay correct when a title changes, when two transactions overlap, and when old Chunks are migrated.

| Test | Proves |
|---|---|
| `test_bad_text_or_limit_is_refused_before_the_database` | Typed failure, with no database call. |
| `test_the_query_text_is_a_bound_parameter_with_a_named_configuration` | The text is data, and `'english'` is named in the SQL. |
| `test_english_stemming_matches_other_forms_of_a_word` | Stemming; stop words alone match nothing. |
| `test_a_title_match_outranks_a_heading_match_and_a_text_match` | Weighting: title, then heading, then text. |
| `test_quoted_words_must_appear_together_and_in_order` | Exact phrases, `-word`, and `or`. |
| `test_any_typed_text_is_read_as_words_never_as_syntax` | Safe web-search syntax. |
| `test_a_better_match_the_reader_may_not_see_is_never_returned` | Permissions, the highest-risk edge. |
| `test_a_title_only_change_updates_what_the_chunks_match` | The Document trigger. |
| `test_equal_scores_are_ordered_by_chunk_id` | Stable order for ties. |
| `test_the_search_reads_the_gin_index` | PostgreSQL really uses the new index. |
| `test_the_migration_fills_the_vectors_of_existing_chunks` | Chunks stored before the migration get their words. |
| the last three tests | The three review findings. |

**Action 1:** create `tests/test_lexical_search.py`:

```python
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
```

**Action 2:** create `tests/test_lexical_search_integration.py`:

```python
"""Real PostgreSQL tests for English full-text search over Chunks."""

import asyncio
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
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.retrieval.lexical_search import (
    InvalidLexicalQuery,
    LexicalMatch,
    english_search_statement,
    search_english_chunks,
)
from knowledge_service.settings import Settings

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000031")
OBSERVED_AT = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
READER = UserId.parse("00000000-0000-0000-0000-0000000000a1")
OTHER_USER = UserId.parse("00000000-0000-0000-0000-0000000000a2")
CONTEXT = AuthorizationContext(user_id=READER)

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)

# A second Chunk for a Document that already has one at ordinal 0.
INSERT_CHUNK = sa.text(
    "INSERT INTO chunks (chunk_id, document_id, content_version, ordinal, text,"
    " heading_path, token_count, content_fingerprint) VALUES (gen_random_uuid(),"
    " :document_id, 1, 1, 'More notes.', NULL, 2, repeat('c', 64))"
)
# Chunks whose stored terms differ from what their current title, headings,
# and text give.
STALE_CHUNKS = sa.text(
    "SELECT count(*) FROM chunks JOIN documents USING (document_id)"
    " WHERE search_en IS DISTINCT FROM"
    " english_search_vector(documents.title, chunks.heading_path, chunks.text)"
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


def a_record(
    name: str, *, title: str, text: str, version: str = "v1"
) -> NormalizedDocument:
    """A one-Chunk Document; Markdown when the text starts with a heading."""
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
        return await search_english_chunks(
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


@pytest.mark.integration
async def test_english_stemming_matches_other_forms_of_a_word(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime,
        "leave.txt",
        title="Time off",
        text="Employees request paid leave every year.",
    )

    assert await names(runtime, "requesting leaves") == ["leave.txt"]
    assert await names(runtime, "employee") == ["leave.txt"]
    # Only stop words: no search terms, so nothing matches.
    assert await names(runtime, "the of and") == []


@pytest.mark.integration
async def test_a_title_match_outranks_a_heading_match_and_a_text_match(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "text.txt", title="Notes", text="General notes about onboarding."
    )
    await a_document(
        runtime, "heading.md", title="Guide", text="# Onboarding\n\nGeneral notes."
    )
    await a_document(
        runtime, "title.txt", title="Onboarding", text="General notes for staff."
    )

    matches = await search(runtime, "onboarding")

    assert await names(runtime, "onboarding") == [
        "title.txt",
        "heading.md",
        "text.txt",
    ]
    scores = [match.score for match in matches]
    assert scores == sorted(scores, reverse=True)
    assert len(set(scores)) == 3
    assert [match.rank for match in matches] == [1, 2, 3]


@pytest.mark.integration
async def test_quoted_words_must_appear_together_and_in_order(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "phrase.txt", title="Policy", text="Read the paid leave policy."
    )
    await a_document(
        runtime, "apart.txt", title="Policy", text="Leave is always paid here."
    )

    assert sorted(await names(runtime, "paid leave")) == ["apart.txt", "phrase.txt"]
    assert await names(runtime, '"paid leave"') == ["phrase.txt"]
    assert await names(runtime, "leave -always") == ["phrase.txt"]
    assert sorted(await names(runtime, "always or read")) == [
        "apart.txt",
        "phrase.txt",
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    "text",
    [
        "paid & | ! ( ) :* <-> leave",
        '"unclosed quote leave',
        "'; DROP TABLE chunks; --",
        "leave:*",
        "-",
        "or",
        "\\",
        "((((leave",
        "!!!",
    ],
)
async def test_any_typed_text_is_read_as_words_never_as_syntax(
    runtime: DatabaseRuntime, text: str
) -> None:
    await a_document(
        runtime, "leave.txt", title="Time off", text="Employees request paid leave."
    )

    await search(runtime, text)

    # The table is still there and still searchable.
    assert await names(runtime, "leave") == ["leave.txt"]


@pytest.mark.integration
async def test_a_better_match_the_reader_may_not_see_is_never_returned(
    runtime: DatabaseRuntime,
) -> None:
    # The hidden Document matches in its title, its heading, and its text.
    await a_document(
        runtime,
        "hidden.md",
        title="Salary bands",
        text="# Salary bands\n\nSalary bands for every salary grade.",
        readers=[Subject.user(OTHER_USER)],
    )
    await a_document(
        runtime, "visible.txt", title="Payroll", text="Salary is paid monthly."
    )

    assert await names(runtime, "salary") == ["visible.txt"]
    assert await names(runtime, "salary", limit=1) == ["visible.txt"]
    other = AuthorizationContext(user_id=OTHER_USER)
    assert await names(runtime, "salary", context=other) == [
        "hidden.md",
        "visible.txt",
    ]


@pytest.mark.integration
async def test_a_title_only_change_updates_what_the_chunks_match(
    runtime: DatabaseRuntime,
) -> None:
    text = "General notes for staff."
    document_id = await a_document(runtime, "notes.txt", title="Onboarding", text=text)
    async with runtime.transaction() as connection:
        (before,) = await load_chunks(connection, document_id=document_id)

    renamed = await reconcile_document(
        runtime,
        a_record("notes.txt", title="Relocation", text=text),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )
    async with runtime.transaction() as connection:
        (after,) = await load_chunks(connection, document_id=document_id)

    assert renamed.change is Change.TITLE
    assert after.chunk_id == before.chunk_id
    assert await names(runtime, "onboarding") == []
    assert await names(runtime, "relocation") == ["notes.txt"]


@pytest.mark.integration
async def test_equal_scores_are_ordered_by_chunk_id(
    runtime: DatabaseRuntime,
) -> None:
    for name in ("twin-a.txt", "twin-b.txt", "twin-c.txt"):
        await a_document(runtime, name, title="Twin", text="Exactly the same words.")

    matches = await search(runtime, "same words")

    assert len({match.score for match in matches}) == 1
    chunk_ids = [match.chunk_id for match in matches]
    assert chunk_ids == sorted(chunk_ids, key=lambda chunk_id: chunk_id.value)


@pytest.mark.integration
async def test_the_search_reads_the_gin_index(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO documents (document_id, source_id, external_id, title,"
                " content_version, content_fingerprint, authorization_version,"
                " authorization_fingerprint, availability, created_at, updated_at)"
                " SELECT gen_random_uuid(), :source, 'doc-' || i, 'Doc ' || i, 1,"
                " repeat('a', 64), 1, repeat('b', 64), 'available', now(), now()"
                " FROM generate_series(1, 2000) AS i"
            ),
            {"source": SOURCE.value},
        )
        await connection.execute(
            sa.text(
                "INSERT INTO chunks (chunk_id, document_id, content_version, ordinal,"
                " text, heading_path, token_count, content_fingerprint)"
                " SELECT gen_random_uuid(), document_id, 1, 0,"
                " 'filler words number ' || external_id, NULL, 4, repeat('c', 64)"
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
        statement = english_search_statement(
            text="relocation",
            eligibility=eligible_chunks_predicate(CONTEXT),
            limit=10,
        )
        compiled = statement.compile(dialect=postgresql.dialect())
        explained = await connection.exec_driver_sql(
            f"EXPLAIN {compiled}", compiled.params
        )
        plan = explained.scalars().all()

    assert any("ix_chunks_search_en" in line for line in plan), "\n".join(plan)


@pytest.mark.integration
def test_the_migration_fills_the_vectors_of_existing_chunks(
    migrated_database: str,
) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    with migration_environment(url):
        command.downgrade(config, "b6c7d8e9f0a1")
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
                    " (gen_random_uuid(), :source, 'old.txt', 'Relocation', 1,"
                    " repeat('a', 64), 1, repeat('b', 64), 'available', now(), now())"
                ),
                {"source": SOURCE.value},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO chunks (chunk_id, document_id, content_version,"
                    " ordinal, text, heading_path, token_count, content_fingerprint)"
                    " SELECT gen_random_uuid(), document_id, 1, 0,"
                    " 'Moving to a new office.', 'Offices', 4, repeat('c', 64)"
                    " FROM documents"
                )
            )
        with migration_environment(url):
            command.upgrade(config, "head")
        with engine.connect() as connection:
            stored = connection.scalar(sa.text("SELECT search_en::text FROM chunks"))
    finally:
        engine.dispose()

    assert stored == "'move':3C 'new':6C 'offic':2B,7C 'reloc':1A"


async def stale_chunks(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        return int(await connection.scalar(STALE_CHUNKS) or 0)


@pytest.mark.integration
@pytest.mark.parametrize("text", ["-" * 33, "- " * 40, "leave " + "-" * 40])
async def test_text_postgresql_cannot_parse_is_refused_and_the_transaction_survives(
    runtime: DatabaseRuntime, text: str
) -> None:
    await a_document(
        runtime, "leave.txt", title="Time off", text="Employees request paid leave."
    )

    async with runtime.transaction() as connection:
        # Once a raw InternalError whose message repeated the query text.
        with pytest.raises(InvalidLexicalQuery):
            await search_english_chunks(
                connection, text=text, context=CONTEXT, limit=10
            )
        after = await search_english_chunks(
            connection, text="leave", context=CONTEXT, limit=10
        )

    assert len(after) == 1


@pytest.mark.integration
async def test_a_chunk_written_during_a_title_change_gets_the_new_title(
    runtime: DatabaseRuntime,
) -> None:
    document_id = await a_document(
        runtime, "notes.txt", title="Onboarding", text="General notes for staff."
    )
    async with (
        runtime.engine.connect() as renaming,
        runtime.engine.connect() as writing,
    ):
        await renaming.begin()
        await renaming.execute(
            sa.text(
                "UPDATE documents SET title = 'Relocation' WHERE document_id = :id"
            ),
            {"id": document_id.value},
        )

        async def add_chunk() -> None:
            async with writing.begin():
                await writing.execute(INSERT_CHUNK, {"document_id": document_id.value})

        adding = asyncio.create_task(add_chunk())
        finished_early, _ = await asyncio.wait({adding}, timeout=0.5)
        await renaming.commit()
        await adding

    # The insert waited for the rename instead of keeping the old title.
    assert not finished_early
    assert await stale_chunks(runtime) == 0
    assert await names(runtime, "relocation") == ["notes.txt", "notes.txt"]


@pytest.mark.integration
async def test_a_direct_write_to_the_search_terms_is_replaced(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "notes.txt", title="Onboarding", text="General notes for staff."
    )

    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text("UPDATE chunks SET search_en = to_tsvector('english', 'bogus')")
        )

    assert await names(runtime, "bogus") == []
    assert await stale_chunks(runtime) == 0
```

The migration test goes back one revision, stores a Chunk the old way, and migrates forward again. The exact words it expects, such as `'offic':2B,7C`, show stemming ("Offices" became `offic`), weights, and positions in one line.

**Check:**

```bash
uv run pytest tests/test_lexical_search.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_lexical_search_integration.py -q -m integration
```

Expected: 15 passed, then 22 passed. To see a test do its job, remove `eligibility` from the `.where(...)` in `english_search_statement`: the hidden-Document test fails. Undo it afterwards.

### Step 6 — No coding in this step: see the search words for yourself

This step shows, in plain SQL, what PostgreSQL stores and what it makes of a question. It turns the tests' claims into something you can see.

**Action:** after Step 5 the test database has the migration applied. Run:

```bash
docker exec knowledge-service-postgres psql -U test_user -d knowledge_service_test \
  -c "SELECT english_search_vector('Staff guide', 'Onboarding', 'Employees request paid leave every year.')" \
  -c "SELECT websearch_to_tsquery('english', 'requesting \"paid leave\" -sick or holiday')" \
  -c "SELECT websearch_to_tsquery('english', '''; DROP TABLE chunks; --')"
```

The reference run printed:

```text
'employe':4C 'everi':8C 'guid':2A 'leav':7C 'onboard':3B 'paid':6C 'request':5C 'staff':1A 'year':9C
'request' & 'paid' <-> 'leav' & !'sick' | 'holiday'
'drop' & 'tabl' & 'chunk'
```

How to read it:

- Each word is a stem, with its position and weight: `'staff':1A` is word 1, from the title.
- "every" became `everi` and "employees" became `employe`: stems are not dictionary words, and they do not need to be.
- `<->` means "next to", from the quotes. `!` means "not", from `-sick`.
- The hostile text became three harmless words. Nothing in it was run as SQL.

Write in the evidence which of your Step 1 predictions were right.

**Check:** your output matches the reference, and your evidence names one prediction you got wrong, or says that none were.

### Step 7 — No coding in this step: gates, docs, evidence, and progress

This step proves everything still works, updates the two lines of the architecture the change made out of date, and records the result.

**Action 1:** in `docs/architecture.md`, change the `retrieval` row of the "Code layout" table to:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_english_chunks`, `MetadataCondition` |
```

and the `chunks` row of the data-model table to:

```markdown
| `chunks` | One Chunk per `(document_id, ordinal)`; `token_count >= 1`; a Chunk's `content_version` is at least 1. `search_en`, its English search terms (title A, headings B, text C), is computed by database triggers on every insert and update of the Chunk and when the Document's title changes; the application never writes it. |
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

Expected: all pass. The reference run gave `make check` 1115 passed, 3 deselected, and `make test-integration` 222 passed.

**Action 3:** complete the evidence file:

- **Design:** the column, the two triggers and why they exist, the index and why `fastupdate` is off, and the search function.
- **Verification:** the commands and counts, the Step 6 output, and the deliberate break from Step 5.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M05-T01 row to `docs/curriculum/PROGRESS.md`, set the next task to M05-T02, and commit with `m05-t01: index english full text search`.

## Reflection questions

1. What does `search_english_chunks` hide from the code that calls it? List the things a caller never has to think about, such as stemming, the triggers, or the savepoint.
2. The search words live in the database and are built by triggers, not by Python. What could go wrong if a later change moved that work into Python, for example into `replace_chunks`?
3. The database could have skipped the GIN index and still returned the right answers. Why does it matter that the index is used, and what measurement would tell you it stopped being used?
4. English stemming turns "offices" into `offic`. What do you expect to happen with a Ukrainian Document, which M05-T02 handles, if it goes through the English configuration?
5. What test or measurement could prove the ranking wrong? Think about a very long Chunk that mentions a word many times against a short title that mentions it once.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before running anything.
- [ ] The migration adds `search_en`, fills existing Chunks, adds the GIN index, and installs both triggers; downgrade removes them.
- [ ] `schema.py` declares the column and the index, and the drift test passes.
- [ ] `search_english_chunks` refuses bad input with `InvalidLexicalQuery` and checks permissions in the same query.
- [ ] The tests prove stemming, weighting, exact phrases, safe syntax, permissions, title changes, ties, index use, and the migration backfill.
- [ ] You ran the Step 6 queries and compared them with your predictions.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The architecture lines, evidence, PROGRESS row, and commit are done.

When you have written the code, share it or any error output, and I will review it against this checklist.
