# M04-T04 — Enable pgvector and vector schema

Source task: [M04-T04](../curriculum/milestones/M04-dense-retrieval/M04-T04-enable-pgvector-and-vector-schema.md)

## What you will do

So far vectors exist only in memory. This lesson gives them a home in PostgreSQL: the **pgvector** extension, a table of **embedding spaces**, and a table of Chunk vectors. The database itself, not only the Python code, refuses a vector of the wrong length, and two models' vectors can live side by side while you move from one to the other.

| Requirement | How this lesson meets it |
|---|---|
| A migration for the extension | `CREATE EXTENSION IF NOT EXISTS vector` in a new migration. |
| Versioned Chunk embeddings | `chunk_embeddings` holds one vector per Chunk per embedding space, so a Chunk can have vectors from an old and a new model at once. |
| Dimension and model constraints | Each space is one `(provider, model, dimensions)`. A row's vector length must equal its space's dimensions, enforced by a `CHECK` and a two-column foreign key. |
| Current-state linkage | At most one space is current (a partial unique index), and every vector belongs to a live Chunk: replacing or purging a Chunk removes its vectors. |
| Migration tests reject wrong dimensions and preserve multiple versions during transition | Integration tests against real pgvector. |

This lesson includes coding: Steps 3–8 change code or docs, while Steps 1, 2, and 9 do not. Step 2 replaces your local test database container.

Terms used in this lesson:

- **pgvector:** a PostgreSQL extension that adds a `vector` column type and distance operators. An **extension** is a plugin installed into one database with `CREATE EXTENSION`.
- **Embedding Space:** one embedding model identity, its provider, model, and dimensions. Vectors can only be compared within one space. This lesson adds it to `CONTEXT.md`.
- **Partial unique index:** a unique index over only the rows that match a condition. Here: unique over the rows `WHERE is_current`, so at most one row can be current.
- **Composite foreign key:** a foreign key over two columns at once. Here: `(space_id, dimensions)` must match a space's own `(space_id, dimensions)`.

## Before you begin

- **Your test database has no pgvector.** The `knowledge-service-postgres` container runs the plain `postgres:17` image, and `pg_available_extensions` has no `vector` there. Step 2 replaces it with `pgvector/pgvector:pg17`, the same PostgreSQL 17 with pgvector 0.8.7 added. CI does not use a database, so nothing else changes.
- **Why the vector column has no fixed length.** A column declared `vector(1536)` would hold only one model's vectors. A plain `vector` column accepts any length, so two spaces of different dimensions can share the table during a model switch (M04-T10); the `CHECK` then ties each row to its own space's length. Similarity indexes need a fixed length, so M04-T06 or M04-T11 will add one partial index per space.
- **Why the length check needs two tables.** A `CHECK` can only see its own row. So each vector row repeats its space's `dimensions`, a composite foreign key proves that number is really the space's, and the `CHECK` compares it with `vector_dims(embedding)`.
- **A migration already applied does not run again** (M02-T14). The test fixture also keeps an existing test database. If you edit this migration after running the tests once, drop the test database first, or the tests keep checking the old schema:

  ```bash
  docker exec knowledge-service-postgres psql -U test_user -d postgres -c 'DROP DATABASE IF EXISTS knowledge_service_test WITH (FORCE)'
  ```

- **What the adversarial review found.** An independent review attacked the schema on a real pgvector server. No sequence of inserts, updates, casts (`halfvec`, `sparsevec`), empty vectors, or concurrent transactions produced a wrong-length vector or two current spaces, and the migration round trip was clean. It found two real problems, both fixed below:
  - Switching the current space with one `UPDATE` (`SET is_current = (space_id = :new)`) failed or succeeded depending on row order, because a unique index is checked row by row. Only "clear the old, then set the new" is safe; that is now documented next to the index and used in the test.
  - Alembic's drift check does not compare `CHECK` bodies, index conditions, or a vector's length: removing the length rule from `persistence.py` went unnoticed. Two pinning tests now cover both sides.

  It also led to smaller changes: `downgrade` no longer drops an extension it may not have created, provider and model may not have surrounding spaces, the tests use floats that 32 bits store exactly, and three tests were added (deleting a space, changing its dimensions, the purge cascade).
- **Files you will touch:**
  - `pyproject.toml` and `uv.lock` (the `pgvector` package)
  - `src/knowledge_service/persistence.py`
  - new migration `migrations/versions/e3f4a5b6c7d8_chunk_embeddings.py`
  - `tests/test_indexes.py`, new `tests/test_embedding_schema.py`, new `tests/test_embedding_schema_integration.py`
  - `CONTEXT.md`, `docs/architecture.md`, `docs/operations.md`
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and the expected failing check

**Invariant:** no stored vector can have a length other than its space's dimensions, whatever the application does. A Chunk has at most one vector per space, and vectors of several spaces can coexist. At most one space is current. A vector lives exactly as long as its Chunk and its space.

**Action:** create `docs/evidence/M04-T04-pgvector-schema.md`:

```markdown
# M04-T04 — pgvector schema evidence

Completed: pending

## Invariant

Pending.

## Expected failing check

Before changing anything, say what happens when you run the migrations on
the current `postgres:17` test container, and what fails once the tables are
declared in `persistence.py` but no migration creates them. Then predict
what the database does with: a 4-number vector in a 3-dimension space; a
row that claims 4 dimensions for that space; a vector containing NaN; a
second current space; switching the current space; a new version of a
Chunk's text; deleting a space; and changing a space's dimensions while it
holds vectors.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and predictions.

### Step 2 — No coding in this step: switch the test server to pgvector

Keep the old container, stopped and renamed, until everything passes, so you can go back.

**Action:** run:

```bash
docker stop knowledge-service-postgres
docker rename knowledge-service-postgres knowledge-service-postgres-pg17
PW=$(docker inspect knowledge-service-postgres-pg17 --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^POSTGRES_PASSWORD=//p') && docker run -d --name knowledge-service-postgres -e POSTGRES_USER=test_user -e POSTGRES_PASSWORD="$PW" -e POSTGRES_DB=knowledge_service_test -p 5433:5432 pgvector/pgvector:pg17 && unset PW
```

The third line copies the old password into the new container without printing it, so your `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` keeps working unchanged.

**Check:**

```bash
docker exec knowledge-service-postgres psql -U test_user -d knowledge_service_test -tAc "select default_version from pg_available_extensions where name = 'vector'"
```

prints a version such as `0.8.7`. With `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set, `make test-integration` still passes on the new server. When the whole lesson passes, remove the old container with `docker rm -v knowledge-service-postgres-pg17`; to go back instead, stop the new one and rename the old one back.

### Step 3 — Coding step: add the pgvector package

The `pgvector` Python package gives SQLAlchemy a `Vector` column type, and later the distance operators M04-T06 uses. It ships type hints (`py.typed`), so Pyright strict accepts it.

**Action:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv add "pgvector>=0.5.1,<0.6"
```

**Check:** `pyproject.toml` lists `"pgvector>=0.5.1,<0.6"`, and `uv.lock` holds version 0.5.1.

### Step 4 — Coding step: declare the tables

| Table or rule | Why |
|---|---|
| `embedding_spaces` | One row per `(provider, model, dimensions)` (`uq_embedding_spaces_identity`). Dimensions from 1 to 16000, pgvector's limit. Provider and model must be non-empty and have no surrounding spaces, so `"openai "` and `"openai"` cannot become two spaces. |
| `uq_embedding_spaces_space_dimensions` | Gives the composite foreign key a target: `(space_id, dimensions)` is unique. |
| `uq_embedding_spaces_current` | At most one current space. A unique index is checked row by row and cannot be deferred, so a switch must clear the old space first, then set the new one, in one transaction. |
| `chunk_embeddings` | Primary key `(chunk_id, space_id)`: one vector per Chunk per space. `chunk_id` cascades from `chunks`, so a replaced or purged Chunk takes its vectors along. |
| `fk_chunk_embeddings_space_dimensions` and `ck_chunk_embeddings_dimensions` | Together: the row's `dimensions` is its space's, and the vector has exactly that length. Deleting a space cascades; changing a space's dimensions while it has vectors is refused. |
| `ix_chunk_embeddings_space_id` | Serves deleting a space and, in M04-T06, reading one space's vectors. Without it, the repository's index policy (`test_every_foreign_key_leads_an_index`) fails. |

**Action:** in `src/knowledge_service/persistence.py`, add the import after `import sqlalchemy as sa`:

```python
from pgvector.sqlalchemy import Vector
```

and add these tables right before `access_grants = sa.Table(`:

```python
# One row per embedding model identity. Vectors are comparable only within
# one space, and at most one space is the one retrieval reads.
embedding_spaces = sa.Table(
    "embedding_spaces",
    metadata,
    sa.Column("space_id", sa.Uuid(), primary_key=True),
    sa.Column("provider", sa.Text(), nullable=False),
    sa.Column("model", sa.Text(), nullable=False),
    sa.Column("dimensions", sa.Integer(), nullable=False),
    sa.Column("is_current", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "provider", "model", "dimensions", name="uq_embedding_spaces_identity"
    ),
    # The target of chunk_embeddings' composite foreign key.
    sa.UniqueConstraint(
        "space_id", "dimensions", name="uq_embedding_spaces_space_dimensions"
    ),
    sa.CheckConstraint(
        "dimensions between 1 and 16000", name="ck_embedding_spaces_dimensions"
    ),
    sa.CheckConstraint(
        "provider <> '' and provider = btrim(provider)"
        " and model <> '' and model = btrim(model)",
        name="ck_embedding_spaces_identity",
    ),
    # A unique index is checked row by row and cannot be deferred: to switch
    # the current space, clear the old one first, then set the new one.
    sa.Index(
        "uq_embedding_spaces_current",
        "is_current",
        unique=True,
        postgresql_where=sa.text("is_current"),
    ),
)

# One vector per Chunk per space. The row repeats its space's dimensions, so
# the database itself refuses a vector of another length.
chunk_embeddings = sa.Table(
    "chunk_embeddings",
    metadata,
    sa.Column(
        "chunk_id",
        sa.Uuid(),
        sa.ForeignKey("chunks.chunk_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("space_id", sa.Uuid(), primary_key=True),
    sa.Column("dimensions", sa.Integer(), nullable=False),
    sa.Column("embedding", Vector(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["space_id", "dimensions"],
        ["embedding_spaces.space_id", "embedding_spaces.dimensions"],
        ondelete="CASCADE",
        name="fk_chunk_embeddings_space_dimensions",
    ),
    sa.CheckConstraint(
        "vector_dims(embedding) = dimensions",
        name="ck_chunk_embeddings_dimensions",
    ),
    # Serves deleting a space and reading one space's vectors (M04-T06).
    sa.Index("ix_chunk_embeddings_space_id", "space_id"),
)
```

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 5 — Coding step: the migration

Create it with `uv run alembic revision -m "chunk embeddings" --rev-id e3f4a5b6c7d8`, after `d2e3f4a5b6c7`, and make it read as below. Two choices to notice:

- **`CREATE EXTENSION IF NOT EXISTS`** works whether or not someone installed the extension already. It needs a role allowed to create extensions: pgvector is not a "trusted" extension, so that means a superuser locally. Your `test_user` is the container's superuser.
- **`downgrade` keeps the extension.** It may have existed before this migration, and other objects may depend on it.

**Action:** make `migrations/versions/e3f4a5b6c7d8_chunk_embeddings.py` read:

```python
"""chunk embeddings

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-10-23 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision: str = "e3f4a5b6c7d8"
down_revision: str | Sequence[str] | None = "d2e3f4a5b6c7"


def upgrade() -> None:
    """Enable pgvector, then store versioned Chunk embeddings per space."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "embedding_spaces",
        sa.Column("space_id", sa.Uuid(), primary_key=True),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "provider", "model", "dimensions", name="uq_embedding_spaces_identity"
        ),
        sa.UniqueConstraint(
            "space_id", "dimensions", name="uq_embedding_spaces_space_dimensions"
        ),
        sa.CheckConstraint(
            "dimensions between 1 and 16000", name="ck_embedding_spaces_dimensions"
        ),
        sa.CheckConstraint(
            "provider <> '' and provider = btrim(provider)"
            " and model <> '' and model = btrim(model)",
            name="ck_embedding_spaces_identity",
        ),
    )
    op.create_index(
        "uq_embedding_spaces_current",
        "embedding_spaces",
        ["is_current"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "chunk_embeddings",
        sa.Column(
            "chunk_id",
            sa.Uuid(),
            sa.ForeignKey("chunks.chunk_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("space_id", sa.Uuid(), primary_key=True),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["space_id", "dimensions"],
            ["embedding_spaces.space_id", "embedding_spaces.dimensions"],
            ondelete="CASCADE",
            name="fk_chunk_embeddings_space_dimensions",
        ),
        sa.CheckConstraint(
            "vector_dims(embedding) = dimensions",
            name="ck_chunk_embeddings_dimensions",
        ),
    )
    op.create_index("ix_chunk_embeddings_space_id", "chunk_embeddings", ["space_id"])


def downgrade() -> None:
    """Drop the embedding tables.

    The extension stays: it may have existed before this migration, and other
    objects may use it.
    """
    op.drop_index("ix_chunk_embeddings_space_id", table_name="chunk_embeddings")
    op.drop_table("chunk_embeddings")
    op.drop_index("uq_embedding_spaces_current", table_name="embedding_spaces")
    op.drop_table("embedding_spaces")
```

**Check:** `uv run pytest tests/test_migrations.py -q` passes (one head). With the database URL set, `uv run pytest tests/test_migrations_integration.py -q` passes, including the upgrade, downgrade, and upgrade-again round trip and the drift check.

### Step 6 — Coding step: the index policy

`tests/test_indexes.py` keeps a list of every named index, and checks that every foreign key leads an index. Two changes:

- The two new indexes join the list.
- The foreign-key check looked at single columns, so it flagged `dimensions`, the second column of the composite key. A composite key only needs an index that starts with its **first** column, so the check now walks whole foreign-key constraints. It still reports an unindexed single-column key, and an unindexed composite one.

**Action:** in `tests/test_indexes.py`, add `"ix_chunk_embeddings_space_id",` after `"ix_access_grants_subject_document",` and `"uq_embedding_spaces_current",` before `"uq_synchronization_runs_running_source",` in `ACCESS_PATTERN_INDEXES`, and replace `assert_every_foreign_key_leads_an_index` with:

```python
def assert_every_foreign_key_leads_an_index(table: sa.Table) -> None:
    """Raise when a foreign key has no index to serve it.

    A composite key needs an index that starts with its first column.
    """
    leading = leading_index_columns(table)
    for constraint in table.foreign_key_constraints:
        first = next(iter(constraint.columns)).name
        if first not in leading:
            raise ForeignKeyNotIndexed(f"{table.name}.{first}")
```

**Check:** `uv run pytest tests/test_indexes.py -q` gives 4 passed.

### Step 7 — Coding step: tests

Alembic's drift check compares tables, columns, keys, and indexes, but **not** `CHECK` bodies, index conditions (`WHERE is_current`), or a vector's length. So two tests pin those rules on both sides: `tests/test_embedding_schema.py` pins what `persistence.py` declares (no database), and `test_the_migrated_database_holds_exactly_these_rules` pins what PostgreSQL stores. Changing a rule in only one place fails one of them.

| Integration test | What it proves |
|---|---|
| `test_a_vector_of_its_space_dimensions_is_stored_and_read_back` | The success case. |
| `test_a_vector_of_another_length_is_refused` | Wrong dimensions, both ways: a wrong-length vector, and a row that lies about its space's dimensions. |
| `test_a_vector_that_is_not_finite_is_refused` | pgvector refuses NaN and infinity. |
| `test_two_spaces_keep_their_vectors_while_the_current_one_switches` | Multiple versions during a transition, and the safe switching order. |
| `test_only_one_space_can_be_current`, `test_one_chunk_has_at_most_one_vector_per_space`, `test_the_same_model_identity_is_one_space`, `test_an_impossible_space_is_refused` | The uniqueness and value rules. |
| `test_a_new_chunk_run_removes_the_vectors_of_the_old_chunks`, `test_purging_a_deleted_document_removes_its_vectors`, `test_deleting_a_space_removes_only_its_vectors`, `test_a_space_cannot_change_its_dimensions_while_it_holds_vectors` | The links to current state. |
| `test_the_migrated_database_holds_exactly_these_rules` | The drift guard on the database side. |

The tests use values such as `0.125` and `0.75`: pgvector stores 32-bit floats, and those values survive the round trip exactly, where `0.1` might not.

**Action:** create `tests/test_embedding_schema.py`:

```python
"""Unit tests pinning the declared pgvector schema to the migration's rules.

Alembic's drift check does not compare CHECK bodies, index predicates, or
vector dimensions, so these declarations could drift from the migration
unnoticed. The integration test pins what PostgreSQL stores; this test pins
what persistence.py declares.
"""

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from knowledge_service.persistence import chunk_embeddings, embedding_spaces


def check_text(table: sa.Table, name: str) -> str:
    (constraint,) = [
        c
        for c in table.constraints
        if isinstance(c, sa.CheckConstraint) and c.name == name
    ]
    return str(constraint.sqltext)


def test_the_declared_checks_match_the_migration() -> None:
    assert (
        check_text(chunk_embeddings, "ck_chunk_embeddings_dimensions")
        == "vector_dims(embedding) = dimensions"
    )
    assert (
        check_text(embedding_spaces, "ck_embedding_spaces_dimensions")
        == "dimensions between 1 and 16000"
    )
    assert check_text(embedding_spaces, "ck_embedding_spaces_identity") == (
        "provider <> '' and provider = btrim(provider)"
        " and model <> '' and model = btrim(model)"
    )


def test_the_declared_current_index_matches_the_migration() -> None:
    (index,) = [
        i for i in embedding_spaces.indexes if i.name == "uq_embedding_spaces_current"
    ]

    assert index.unique
    assert [column.name for column in index.columns] == ["is_current"]
    assert str(index.dialect_options["postgresql"]["where"]) == "is_current"


def test_the_declared_vector_column_accepts_any_dimensions() -> None:
    embedding = chunk_embeddings.c.embedding.type

    assert isinstance(embedding, Vector)
    assert embedding.dim is None


def test_the_declared_space_key_carries_the_dimensions() -> None:
    (key,) = [
        k
        for k in chunk_embeddings.foreign_key_constraints
        if k.name == "fk_chunk_embeddings_space_dimensions"
    ]

    assert [column.name for column in key.columns] == ["space_id", "dimensions"]
    assert [element.target_fullname for element in key.elements] == [
        "embedding_spaces.space_id",
        "embedding_spaces.dimensions",
    ]
    assert key.ondelete == "CASCADE"
```

Create `tests/test_embedding_schema_integration.py`:

```python
"""Real PostgreSQL tests for the pgvector schema of Chunk embeddings."""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.deletions import purge_document_chunks, tombstone_missing
from knowledge_service.identifiers import SourceId
from knowledge_service.normalization import NormalizedDocument, normalize
from knowledge_service.persistence import (
    chunk_embeddings,
    chunks,
    embedding_spaces,
)
from knowledge_service.reconciliation import reconcile_document
from knowledge_service.settings import Settings
from knowledge_service.sources import SourceAccess, SourceRecord

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
OBSERVED_AT = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
HANDBOOK = "# Handbook\n\nWelcome to the team.\n"

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
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
    try:
        yield runtime
    finally:
        await runtime.dispose()


def markdown(text: str) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id="handbook.md",
            version="v1",
            media_type="text/markdown",
            content=text.encode(),
            access=SourceAccess(public=True),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def a_chunk(runtime: DatabaseRuntime, text: str = HANDBOOK) -> UUID:
    """Publish the handbook and return the ID of its one Chunk."""
    await reconcile_document(
        runtime, markdown(text), readers=[Subject.public()], observed_at=OBSERVED_AT
    )
    async with runtime.transaction() as connection:
        return (await connection.execute(sa.select(chunks.c.chunk_id))).scalar_one()


async def add_space(
    connection: AsyncConnection,
    *,
    dimensions: int,
    model: str = "test-embedding",
    is_current: bool = False,
    provider: str = "test",
) -> UUID:
    space_id = uuid4()
    await connection.execute(
        sa.insert(embedding_spaces).values(
            space_id=space_id,
            provider=provider,
            model=model,
            dimensions=dimensions,
            is_current=is_current,
            created_at=OBSERVED_AT,
        )
    )
    return space_id


async def add_embedding(
    connection: AsyncConnection,
    *,
    chunk_id: UUID,
    space_id: UUID,
    vector: Sequence[float],
    dimensions: int | None = None,
) -> None:
    await connection.execute(
        sa.insert(chunk_embeddings).values(
            chunk_id=chunk_id,
            space_id=space_id,
            dimensions=len(vector) if dimensions is None else dimensions,
            embedding=list(vector),
            created_at=OBSERVED_AT,
        )
    )


async def stored_vectors(runtime: DatabaseRuntime) -> dict[UUID, list[float]]:
    """Read every stored vector by space.

    pgvector stores 32-bit floats, so tests use values such as 0.125 that 32
    bits hold exactly; 0.1 would not compare equal after a binary round trip.
    """
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(chunk_embeddings.c.space_id, chunk_embeddings.c.embedding)
        )
        return {space_id: [float(x) for x in vector] for space_id, vector in rows}


@pytest.mark.integration
async def test_a_vector_of_its_space_dimensions_is_stored_and_read_back(
    runtime: DatabaseRuntime,
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        space_id = await add_space(connection, dimensions=3, is_current=True)
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=space_id, vector=[0.25, -0.5, 1.0]
        )

    assert await stored_vectors(runtime) == {space_id: [0.25, -0.5, 1.0]}


@pytest.mark.integration
async def test_a_vector_of_another_length_is_refused(runtime: DatabaseRuntime) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        space_id = await add_space(connection, dimensions=3)

    with pytest.raises(IntegrityError, match="ck_chunk_embeddings_dimensions"):
        async with runtime.transaction() as connection:
            await add_embedding(
                connection,
                chunk_id=chunk_id,
                space_id=space_id,
                vector=[0.1, 0.2, 0.3, 0.4],
                dimensions=3,
            )
    # Claiming the vector's own length does not help: the space says 3.
    with pytest.raises(IntegrityError, match="fk_chunk_embeddings_space_dimensions"):
        async with runtime.transaction() as connection:
            await add_embedding(
                connection,
                chunk_id=chunk_id,
                space_id=space_id,
                vector=[0.1, 0.2, 0.3, 0.4],
            )

    assert await stored_vectors(runtime) == {}


@pytest.mark.integration
@pytest.mark.parametrize("value", [float("nan"), float("inf")], ids=["nan", "infinite"])
async def test_a_vector_that_is_not_finite_is_refused(
    runtime: DatabaseRuntime, value: float
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        space_id = await add_space(connection, dimensions=2)

    with pytest.raises(DataError):
        async with runtime.transaction() as connection:
            await add_embedding(
                connection, chunk_id=chunk_id, space_id=space_id, vector=[0.1, value]
            )


@pytest.mark.integration
async def test_two_spaces_keep_their_vectors_while_the_current_one_switches(
    runtime: DatabaseRuntime,
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        old = await add_space(connection, dimensions=3, model="old", is_current=True)
        new = await add_space(connection, dimensions=2, model="new")
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=old, vector=[0.125, 0.25, 0.375]
        )
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=new, vector=[0.5, 0.75]
        )

    # Switching is two updates in one transaction: clear the old, set the new.
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(embedding_spaces)
            .where(embedding_spaces.c.space_id == old)
            .values(is_current=False)
        )
        await connection.execute(
            sa.update(embedding_spaces)
            .where(embedding_spaces.c.space_id == new)
            .values(is_current=True)
        )
        current = (
            await connection.execute(
                sa.select(embedding_spaces.c.space_id).where(
                    embedding_spaces.c.is_current
                )
            )
        ).scalar_one()

    assert current == new
    assert await stored_vectors(runtime) == {
        old: [0.125, 0.25, 0.375],
        new: [0.5, 0.75],
    }


@pytest.mark.integration
async def test_only_one_space_can_be_current(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await add_space(connection, dimensions=3, model="old", is_current=True)

    with pytest.raises(IntegrityError, match="uq_embedding_spaces_current"):
        async with runtime.transaction() as connection:
            await add_space(connection, dimensions=2, model="new", is_current=True)


@pytest.mark.integration
async def test_one_chunk_has_at_most_one_vector_per_space(
    runtime: DatabaseRuntime,
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        space_id = await add_space(connection, dimensions=2)
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=space_id, vector=[0.1, 0.2]
        )

    with pytest.raises(IntegrityError, match="chunk_embeddings_pkey"):
        async with runtime.transaction() as connection:
            await add_embedding(
                connection, chunk_id=chunk_id, space_id=space_id, vector=[0.3, 0.4]
            )


@pytest.mark.integration
async def test_a_new_chunk_run_removes_the_vectors_of_the_old_chunks(
    runtime: DatabaseRuntime,
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        space_id = await add_space(connection, dimensions=2, is_current=True)
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=space_id, vector=[0.1, 0.2]
        )

    await reconcile_document(
        runtime,
        markdown("# Handbook\n\nWelcome aboard, everyone.\n"),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )

    # The changed text replaced the Chunk, and its vector went with it.
    assert await stored_vectors(runtime) == {}


@pytest.mark.integration
@pytest.mark.parametrize(
    ("provider", "model", "dimensions", "constraint"),
    [
        ("test", "m", 0, "ck_embedding_spaces_dimensions"),
        ("test", "m", 16001, "ck_embedding_spaces_dimensions"),
        (" ", "m", 3, "ck_embedding_spaces_identity"),
        (" test", "m", 3, "ck_embedding_spaces_identity"),
        ("test", "", 3, "ck_embedding_spaces_identity"),
    ],
    ids=[
        "no-dimensions",
        "too-many-dimensions",
        "blank-provider",
        "padded-provider",
        "blank-model",
    ],
)
async def test_an_impossible_space_is_refused(
    runtime: DatabaseRuntime,
    provider: str,
    model: str,
    dimensions: int,
    constraint: str,
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        async with runtime.transaction() as connection:
            await add_space(
                connection, dimensions=dimensions, model=model, provider=provider
            )


@pytest.mark.integration
async def test_the_same_model_identity_is_one_space(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await add_space(connection, dimensions=3)

    with pytest.raises(IntegrityError, match="uq_embedding_spaces_identity"):
        async with runtime.transaction() as connection:
            await add_space(connection, dimensions=3)


@pytest.mark.integration
async def test_deleting_a_space_removes_only_its_vectors(
    runtime: DatabaseRuntime,
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        old = await add_space(connection, dimensions=2, model="old")
        new = await add_space(connection, dimensions=2, model="new", is_current=True)
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=old, vector=[0.5, 1.0]
        )
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=new, vector=[1.5, 2.0]
        )

    async with runtime.transaction() as connection:
        await connection.execute(
            sa.delete(embedding_spaces).where(embedding_spaces.c.space_id == old)
        )

    assert await stored_vectors(runtime) == {new: [1.5, 2.0]}


@pytest.mark.integration
async def test_a_space_cannot_change_its_dimensions_while_it_holds_vectors(
    runtime: DatabaseRuntime,
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        space_id = await add_space(connection, dimensions=2)
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=space_id, vector=[0.5, 1.0]
        )

    with pytest.raises(IntegrityError, match="fk_chunk_embeddings_space_dimensions"):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.update(embedding_spaces)
                .where(embedding_spaces.c.space_id == space_id)
                .values(dimensions=3)
            )


@pytest.mark.integration
async def test_purging_a_deleted_document_removes_its_vectors(
    runtime: DatabaseRuntime,
) -> None:
    chunk_id = await a_chunk(runtime)
    async with runtime.transaction() as connection:
        space_id = await add_space(connection, dimensions=2, is_current=True)
        await add_embedding(
            connection, chunk_id=chunk_id, space_id=space_id, vector=[0.5, 1.0]
        )
    (tombstone,) = await tombstone_missing(
        runtime,
        source_id=SOURCE_ID,
        seen=set(),
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )

    await purge_document_chunks(
        runtime, job_id=tombstone.purge_job_id, now=OBSERVED_AT + timedelta(hours=2)
    )

    assert await stored_vectors(runtime) == {}


# PostgreSQL's own rendering of the rules this task adds. The drift check does
# not compare CHECK bodies, index predicates, or vector dimensions, so a change
# to the migration must show up here; tests/test_embedding_schema.py pins the
# declarations in persistence.py the same way.
STORED_RULES = {
    "ck_chunk_embeddings_dimensions": "CHECK ((vector_dims(embedding) = dimensions))",
    "ck_embedding_spaces_dimensions": (
        "CHECK (((dimensions >= 1) AND (dimensions <= 16000)))"
    ),
    "ck_embedding_spaces_identity": (
        "CHECK (((provider <> ''::text) AND (provider = btrim(provider))"
        " AND (model <> ''::text) AND (model = btrim(model))))"
    ),
    "fk_chunk_embeddings_space_dimensions": (
        "FOREIGN KEY (space_id, dimensions) REFERENCES"
        " embedding_spaces(space_id, dimensions) ON DELETE CASCADE"
    ),
    "uq_embedding_spaces_current": (
        "CREATE UNIQUE INDEX uq_embedding_spaces_current ON public.embedding_spaces"
        " USING btree (is_current) WHERE is_current"
    ),
    "embedding": "vector",
}

STORED_RULES_QUERY = sa.text(
    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
    " WHERE conname = ANY(:names)"
    " UNION ALL SELECT indexname, indexdef FROM pg_indexes"
    " WHERE indexname = ANY(:names)"
    " UNION ALL SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute"
    " WHERE attrelid = 'chunk_embeddings'::regclass AND attname = 'embedding'"
)


@pytest.mark.integration
async def test_the_migrated_database_holds_exactly_these_rules(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            STORED_RULES_QUERY, {"names": list(STORED_RULES)}
        )
        stored = {str(name): str(definition) for name, definition in rows}

    assert stored == STORED_RULES
```

**Check:** with the database URL set, `uv run pytest tests/test_embedding_schema.py tests/test_embedding_schema_integration.py -q` gives 22 passed.

Then break the migration on purpose, one change at a time. **Drop the test database before each run** (see "Before you begin"), and again after undoing the change:

| Break | Test that must fail |
|---|---|
| Remove `ck_chunk_embeddings_dimensions` | `test_a_vector_of_another_length_is_refused`, the rules test |
| Use a foreign key on `space_id` alone | `test_a_vector_of_another_length_is_refused` |
| Remove `uq_embedding_spaces_current` | `test_only_one_space_can_be_current` |
| Remove `ondelete="CASCADE"` from `chunk_id` | `test_a_new_chunk_run_removes_the_vectors_of_the_old_chunks` |
| Change `Vector()` to `Vector(3)` in `persistence.py` only | `test_the_declared_vector_column_accepts_any_dimensions` |

### Step 8 — Coding step: update the docs this change makes incomplete

**Action:** in `CONTEXT.md`, after the **Chunk** entry, add:

```markdown
**Embedding Space**:
One embedding model identity, its provider, model, and vector dimensions, within which Chunk vectors can be compared; at most one space is current for retrieval.
_Avoid_: Index, vector store
```

In `docs/architecture.md`, add `space_id` to the list of identifiers under `## Data model`, add these two lines to the `erDiagram` after the `access_grants` line:

```text
    chunks ||--o{ chunk_embeddings : "is embedded as"
    embedding_spaces ||--o{ chunk_embeddings : "holds"
```

and these two rows to the table of rules, before the `access_grants` row:

```markdown
| `embedding_spaces` | One space per `(provider, model, dimensions)`; dimensions from 1 to 16000; at most one space is current. |
| `chunk_embeddings` | One vector per `(chunk_id, space_id)`; the vector's length must equal its space's dimensions; removed with its Chunk or its space. |
```

In `docs/operations.md`, start the paragraph that begins "Every external call has one owner" with:

```text
Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role.
```

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check` passes.

### Step 9 — No coding in this step: run the gates, then record evidence

**Action:** with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set, run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_embedding_schema.py tests/test_embedding_schema_integration.py tests/test_indexes.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make test-integration
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. In Design, record these notes for later lessons:

- **pgvector's insert needs a list.** Its SQLAlchemy type refuses a tuple, and the port's `Vector` is a tuple: M04-T05 must convert with `list(...)`.
- **Similarity indexes need one partial index per space,** cast to that space's length, such as `(embedding::vector(1536)) WHERE space_id = ...`; pgvector's HNSW index also allows at most 2000 dimensions.
- **Nothing stops relabelling a vector** to another space of the same length, or editing a Chunk's text in place; the application never does either.
- **A current space may have no vectors yet,** and deleting the current space leaves none current: M04-T10 must backfill before switching.
- **Tombstoned Documents keep their vectors until the purge,** so search in M04-T06 must filter on availability.

Answer these reflection questions in your own words:

1. Why does the database enforce the vector length, when the embedding port already checks it? What could write a wrong-length vector anyway?
2. Why does each vector row repeat its space's `dimensions`? What would a `CHECK` alone be unable to see?
3. Why must a switch clear the old current space before setting the new one?
4. Why is the `vector` column left without a fixed length, and what does that cost when you add a similarity index?
5. The drift check passed even when the length rule was missing from `persistence.py`. What does that tell you about trusting one automated check?
6. Which test would let a real bug reach production if you deleted it? Name it and describe the bug.

**Check:** every command passes, and the evidence explains the schema in your words.

## Completion checklist

- [ ] The test server runs `pgvector/pgvector:pg17`, and the old container is removed or kept on purpose.
- [ ] `pgvector>=0.5.1,<0.6` is a dependency.
- [ ] The migration creates the extension, `embedding_spaces`, and `chunk_embeddings` with every rule above; `downgrade` keeps the extension.
- [ ] `persistence.py` declares the same tables, and both pinning tests pass.
- [ ] The index policy lists the new indexes and checks whole foreign-key constraints.
- [ ] Wrong dimensions are refused, two spaces coexist, at most one is current, and vectors follow their Chunk and space.
- [ ] `CONTEXT.md`, `docs/architecture.md`, and `docs/operations.md` describe the new term, tables, and privilege.
- [ ] Format, lint, types, the deterministic suite, the integration suite, `make check`, and `make docs-check` pass.
- [ ] The evidence file holds your invariant, predictions, design, verification, and your own reflection.

When you're done, share your work or any error you hit, and I'll review it.
