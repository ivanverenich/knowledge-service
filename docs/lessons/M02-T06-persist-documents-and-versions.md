# M02-T06 — Persist Documents and versions

Source task: [M02-T06](../curriculum/milestones/M02-domain-and-persistence/M02-T06-persist-documents-and-versions.md)

## What you will build

This lesson includes coding. You already have the `Document` domain value from
M02-T02 and the `sources` table from M02-T05. Now you will store Documents for
real: one `documents` table, plus a small persistence module that writes and
reads it.

The hard part is not the table — it is the **write path**. The same source item
is observed again and again as it changes, and the row must keep its identity
while its two version numbers move independently:

- `content_version` advances only when the text or structure changes.
- `authorization_version` advances only when the access grants change.

A source item that nobody edited must not look like it changed. That is what
"idempotent upsert" means here: writing the same observation twice leaves one
row, with the same `document_id` and the same versions.

One rule from [architecture.md](../architecture.md) still holds: domain values
must not import SQLAlchemy. So the new module — not `documents.py` — owns the
SQL, and the [ADR 0005](../adr/0005-incremental-at-least-once-synchronization.md)
at-least-once design is why re-writing the same row must be safe.

## Before you begin

- `src/knowledge_service/documents.py` already enforces every rule you need:
  positive versions, UTC timestamps, `reconcile()` advancing only the changed
  dimension, and `tombstone()` for a vanished source item. You are not changing
  it.
- `migrations/versions/b96066f98718_sources_and_synchronization_runs.py` is the
  hand-written revision you will follow, and `migrations/env.py` still has
  `target_metadata = None`, so **you write the revision by hand again**.
- `tests/test_sources_integration.py` shows how the disposable database is
  used. Note one difference in this lesson: the new test is **async**, because
  the whole point is to exercise the async write path your application uses.

For background, the task points to the [task execution guide](../curriculum/TASK-GUIDE.md),
[architecture overview](../architecture.md), [domain language](../../CONTEXT.md),
and [primary-source map](../research/primary-sources.md).

## Walkthrough

### Step 1 — No coding in this step: write down the rule you are preserving

**Purpose:** State what the write path must guarantee before writing any SQL,
so you have something concrete to test against.

**Decision:** Keep this task's notes in `docs/evidence/M02-T06-documents-and-versions.md`.
Fill in the invariant and prediction from what `documents.py` already promises.

**Action:** Read
[`M02-T05 evidence`](../evidence/M02-T05-sources-and-sync-state.md) and
[`M02-T02 evidence`](../evidence/M02-T02-document-version-invariants.md). Then
create `docs/evidence/M02-T06-documents-and-versions.md`:

```markdown
# M02-T06 — Documents and versions evidence

Completed: pending

## Invariant

Write down what a stored Document row must keep stable across repeated
observations, and which version may move when only one dimension changes.

## Prediction

Write what you expect before adding the table.

## Verification

Pending.

## Reflection

Pending.
```

Then confirm nothing to be created already exists:

```bash
rg --files src migrations tests | rg 'persistence|documents'
```

Expected: `src/knowledge_service/documents.py` and the two existing revisions
only. No `persistence.py`, no `documents` migration.

**Check:** The evidence file contains your invariant and prediction, and the
search shows the new module and revision do not exist yet. Do not add code or
edit the task or progress files in this step.

### Step 2 — Coding step: create the documents table in a reviewed revision

**Purpose:** Give PostgreSQL somewhere to store the current state of a source
item, with the domain's rules enforced by the database as well as by Python.

**Decision:** One row per source item, so `(source_id, external_id)` is unique.
`document_id` is the primary key and is never rewritten, which is what keeps a
Document's identity stable across observations. The two version numbers get
`CHECK (>= 1)` constraints and `availability` gets a `CHECK` listing the two
known values, matching the `available`/`tombstoned` strings the domain value
already produces. A foreign key to `sources` keeps provenance honest.

**Action:** Generate an empty revision, then write it by hand. This command only
writes a file; it does not need a database:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic revision -m "documents"
```

Open the new file under `migrations/versions/`. Keep the generated revision
identifier, and keep the `down_revision` Alembic filled in — it should be
`b96066f98718`, the revision you wrote for M02-T05. Remove the generated
`from typing import Sequence, Union` line and the `branch_labels`/`depends_on`
lines exactly as before, then make `upgrade` and `downgrade` read:

```python
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "<the identifier Alembic generated>"
down_revision: str | Sequence[str] | None = "b96066f98718"


def upgrade() -> None:
    """Create the current Document state for each source item."""
    op.create_table(
        "documents",
        sa.Column("document_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "source_id",
            sa.Uuid(),
            sa.ForeignKey("sources.source_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("source_version", sa.Text(), nullable=True),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column("authorization_version", sa.Integer(), nullable=False),
        sa.Column("authorization_fingerprint", sa.Text(), nullable=False),
        sa.Column("availability", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "source_id", "external_id", name="uq_documents_source_external"
        ),
        sa.CheckConstraint("content_version >= 1", name="ck_documents_content_version"),
        sa.CheckConstraint(
            "authorization_version >= 1", name="ck_documents_authorization_version"
        ),
        sa.CheckConstraint(
            "availability in ('available', 'tombstoned')",
            name="ck_documents_availability",
        ),
    )


def downgrade() -> None:
    """Remove every stored Document."""
    op.drop_table("documents")
```

Named constraints matter here: the upsert in Step 4 targets
`uq_documents_source_external` by name, so an unnamed constraint would leave the
statement with nothing to refer to. The
[Alembic operations reference](https://alembic.sqlalchemy.org/en/latest/ops.html)
documents `create_table`, and the
[PostgreSQL constraints chapter](https://www.postgresql.org/docs/18/ddl-constraints.html)
covers `UNIQUE`, `CHECK`, and `FOREIGN KEY`.

**Check:** Alembic shows three revisions in one chain, with the new one on top:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic history
```

Expected: `baseline` → `sources and synchronization runs` → your revision, with
`alembic heads` reporting a single head. Do not upgrade a shared database yet.

### Step 3 — Coding step: translate between a Document and a row

**Purpose:** Keep every column name and type in one place, so the upsert, the
read path, and the tests all agree about what a stored Document looks like.

**Decision:** Create `src/knowledge_service/persistence.py` holding the
SQLAlchemy `Table` for `documents` plus two pure functions: `as_row()` turns a
domain value into column values, and `as_document()` rebuilds the domain value
from a stored row. Splitting translation out from the I/O means the drop from
"domain value" to "loosely typed row" is confined to one file, and it is the
half of this module that can be tested without a database.

**Action:** Create `src/knowledge_service/persistence.py`:

```python
"""PostgreSQL persistence for Documents.

Domain values stay free of SQLAlchemy. This module owns the stored table shape,
the translation between a Document and a row, and the statements that read and
write it.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.documents import (
    AuthorizationVersion,
    ContentVersion,
    Document,
    DocumentAvailability,
    DocumentProvenance,
    InvalidDocument,
)
from knowledge_service.identifiers import DocumentId, SourceId

metadata = sa.MetaData()

documents = sa.Table(
    "documents",
    metadata,
    sa.Column("document_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "source_id",
        sa.Uuid(),
        sa.ForeignKey("sources.source_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("external_id", sa.Text(), nullable=False),
    sa.Column("source_version", sa.Text(), nullable=True),
    sa.Column("content_version", sa.Integer(), nullable=False),
    sa.Column("content_fingerprint", sa.Text(), nullable=False),
    sa.Column("authorization_version", sa.Integer(), nullable=False),
    sa.Column("authorization_fingerprint", sa.Text(), nullable=False),
    sa.Column("availability", sa.String(length=16), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint(
        "source_id", "external_id", name="uq_documents_source_external"
    ),
    sa.CheckConstraint("content_version >= 1", name="ck_documents_content_version"),
    sa.CheckConstraint(
        "authorization_version >= 1", name="ck_documents_authorization_version"
    ),
    sa.CheckConstraint(
        "availability in ('available', 'tombstoned')",
        name="ck_documents_availability",
    ),
)


def as_row(document: Document) -> dict[str, object]:
    """Translate a domain Document into the columns that store it."""
    return {
        "document_id": document.document_id.value,
        "source_id": document.provenance.source_id.value,
        "external_id": document.provenance.external_id,
        "source_version": document.provenance.source_version,
        "content_version": document.content_version.number,
        "content_fingerprint": document.content_fingerprint,
        "authorization_version": document.authorization_version.number,
        "authorization_fingerprint": document.authorization_fingerprint,
        "availability": document.availability.value,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }


def as_document(record: Mapping[str, object]) -> Document:
    """Rebuild a domain Document from a stored row."""
    try:
        availability = DocumentAvailability(cast(str, record["availability"]))
    except ValueError:
        raise InvalidDocument("stored availability is not a known value") from None

    return Document(
        document_id=DocumentId(cast(UUID, record["document_id"])),
        provenance=DocumentProvenance(
            source_id=SourceId(cast(UUID, record["source_id"])),
            external_id=cast(str, record["external_id"]),
            source_version=cast("str | None", record["source_version"]),
        ),
        content_version=ContentVersion(cast(int, record["content_version"])),
        content_fingerprint=cast(str, record["content_fingerprint"]),
        authorization_version=AuthorizationVersion(
            cast(int, record["authorization_version"])
        ),
        authorization_fingerprint=cast(str, record["authorization_fingerprint"]),
        availability=availability,
        created_at=cast(datetime, record["created_at"]),
        updated_at=cast(datetime, record["updated_at"]),
    )
```

The `Table` here and the migration in Step 2 describe the same thing twice.
That is deliberate: the migration is the reviewed change history, and this
object is what the code can query against. If you change one, change the other —
Step 5's round-trip test and Step 6's integration test are what catch drift.

Two details in `as_document` are worth understanding. `cast` tells the type
checker "this column really is a `UUID`/`int`/`datetime`"; the values come from
the driver, so they already are. And `DocumentAvailability(...)` is the one
place a corrupted row becomes a typed `InvalidDocument` instead of a bare
`ValueError` — the same failure shape the rest of the project uses.

**Check:** Ruff and Pyright accept the module:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run ruff check src/knowledge_service/persistence.py
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright src/knowledge_service/persistence.py
```

### Step 4 — Coding step: make the write path idempotent

**Purpose:** Provide the one operation the synchronization work needs: "here is
what this source item looks like now — store it, whoever writes it and however
many times".

**Decision:** Use PostgreSQL's `INSERT ... ON CONFLICT ... DO UPDATE` against
the `(source_id, external_id)` constraint. The update list deliberately excludes
`document_id` and `created_at`, so the first writer's identity and creation time
survive every later observation. `load_document()` is the matching read, so
tests and callers can observe the result through the module's own interface
instead of raw SQL.

**Action:** Append these two functions to `src/knowledge_service/persistence.py`:

```python
async def upsert_document(
    connection: AsyncConnection, document: Document
) -> DocumentId:
    """Store a Document, keeping the identity and creation time already stored."""
    statement = insert(documents).values(as_row(document))
    statement = statement.on_conflict_do_update(
        constraint="uq_documents_source_external",
        set_={
            "source_version": statement.excluded.source_version,
            "content_version": statement.excluded.content_version,
            "content_fingerprint": statement.excluded.content_fingerprint,
            "authorization_version": statement.excluded.authorization_version,
            "authorization_fingerprint": statement.excluded.authorization_fingerprint,
            "availability": statement.excluded.availability,
            "updated_at": statement.excluded.updated_at,
        },
    ).returning(documents.c.document_id)
    stored_id = cast(UUID, (await connection.execute(statement)).scalar_one())
    return DocumentId(stored_id)


async def load_document(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    external_id: str,
) -> Document | None:
    """Read the stored Document for one source item, if it exists."""
    statement = sa.select(documents).where(
        documents.c.source_id == source_id.value,
        documents.c.external_id == external_id,
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return as_document(dict(record))
```

`statement.excluded` means "the value this statement tried to insert", which is
how `DO UPDATE` refers to the incoming row. `returning(documents.c.document_id)`
gives you back whichever id now owns the row — the newly inserted one, or the
one already stored — so the caller always learns the real identity. The
[PostgreSQL INSERT statement](https://www.postgresql.org/docs/18/sql-insert.html)
defines `ON CONFLICT`, and the
[SQLAlchemy PostgreSQL dialect](https://docs.sqlalchemy.org/en/21/dialects/postgresql.html#insert-on-conflict-upsert)
documents `on_conflict_do_update`.

These functions take an `AsyncConnection` rather than creating one. Deciding who
owns the transaction — the caller, a request, or a job — is a separate question
this project answers later, and passing the connection in keeps that decision
out of this module.

**Check:** The module still passes Ruff and Pyright:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright src/knowledge_service/persistence.py
```

### Step 5 — Coding step: test the translation without a database

**Purpose:** Lock down the one part of this module that is pure logic — the
round trip between a domain value and a row — so a column rename or reordering
cannot silently change what is stored.

**Decision:** Test `as_row`/`as_document` through the public functions. The
successful case is a full round trip; the edge case proves the two version
columns stay independent; the typed failure proves a row carrying an unknown
`availability` becomes `InvalidDocument` rather than crashing the caller with a
raw `ValueError`.

**Action:** Create `tests/test_persistence.py`:

```python
"""Tests for the Document row translation."""

from datetime import UTC, datetime

import pytest

from knowledge_service.documents import (
    Document,
    DocumentAvailability,
    DocumentProvenance,
    InvalidDocument,
)
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.persistence import as_document, as_row

OBSERVED_AT = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)


def make_document() -> Document:
    return Document.register(
        document_id=DocumentId.parse("00000000-0000-0000-0000-000000000020"),
        provenance=DocumentProvenance(
            source_id=SourceId.parse("00000000-0000-0000-0000-000000000010"),
            external_id="page-123",
            source_version="17",
        ),
        content_fingerprint="content-a",
        authorization_fingerprint="acl-a",
        observed_at=OBSERVED_AT,
    )


def test_row_translation_round_trips_a_document() -> None:
    document = make_document()

    assert as_document(as_row(document)) == document


def test_a_content_only_change_moves_only_the_content_version() -> None:
    document = make_document().reconcile(
        content_fingerprint="content-b",
        authorization_fingerprint="acl-a",
        source_version="18",
        observed_at=OBSERVED_AT,
    )

    row = as_row(document)

    assert row["content_version"] == 2
    assert row["authorization_version"] == 1


def test_tombstoned_document_round_trips_as_unavailable() -> None:
    document = make_document().tombstone(observed_at=OBSERVED_AT)

    assert as_document(as_row(document)).availability is DocumentAvailability.TOMBSTONED


def test_unknown_stored_availability_raises_typed_failure() -> None:
    row = as_row(make_document())
    row["availability"] = "retired"

    with pytest.raises(InvalidDocument, match="availability"):
        as_document(row)
```

**Check:** Run the narrow test:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_persistence.py -vv
```

Expected: 4 passed, without a database server.

### Step 6 — Coding step: prove idempotency against PostgreSQL

**Purpose:** The pure tests cannot prove the SQL. Only a real database can show
that the upsert keeps one row, keeps the stored identity, and lets the two
version numbers move separately.

**Decision:** One async integration test that applies migrations, registers a
source item, writes it twice, then walks the three interesting transitions:
re-registration with a fresh id, content-only change, authorization-only change,
and finally a tombstone. Migrations run in a **synchronous fixture**, because
`migrations/env.py` calls `asyncio.run()` internally and cannot be invoked from
inside the test's already-running event loop.

**Action:** Create `tests/test_persistence_integration.py`:

```python
"""Real PostgreSQL tests for Document persistence."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from knowledge_service.documents import (
    AuthorizationVersion,
    ContentVersion,
    Document,
    DocumentAvailability,
    DocumentProvenance,
)
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.persistence import documents, load_document, upsert_document

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
EXTERNAL_ID = "page-123"


@pytest.fixture
def migrated_database(monkeypatch: pytest.MonkeyPatch) -> str:
    """Apply migrations synchronously; Alembic's env.py runs its own event loop."""
    database_url = os.environ.get("KNOWLEDGE_SERVICE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set KNOWLEDGE_SERVICE_TEST_DATABASE_URL to a disposable database")

    monkeypatch.setenv("KNOWLEDGE_SERVICE_DATABASE_URL", database_url)
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    return database_url


def make_document(
    *, document_id: DocumentId, observed_at: datetime = OBSERVED_AT
) -> Document:
    return Document.register(
        document_id=document_id,
        provenance=DocumentProvenance(
            source_id=SOURCE_ID,
            external_id=EXTERNAL_ID,
            source_version="17",
        ),
        content_fingerprint="content-a",
        authorization_fingerprint="acl-a",
        observed_at=observed_at,
    )


@pytest.mark.integration
async def test_upsert_is_idempotent_and_versions_move_independently(
    migrated_database: str,
) -> None:
    engine = create_async_engine(
        make_url(migrated_database).set(drivername="postgresql+psycopg")
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM documents"))
            await connection.execute(text("DELETE FROM sources"))
            await connection.execute(
                text(
                    "INSERT INTO sources (source_id, kind, location, display_name,"
                    " enabled, created_at, updated_at) VALUES (:source_id,"
                    " 'local_directory', '/srv/handbook', 'Handbook', true, now(),"
                    " now())"
                ),
                {"source_id": SOURCE_ID.value},
            )

        registered = make_document(document_id=DocumentId.new())

        async with engine.begin() as connection:
            assert (
                await upsert_document(connection, registered) == registered.document_id
            )

        async with engine.begin() as connection:
            assert (
                await upsert_document(connection, registered) == registered.document_id
            )
            stored_rows = await connection.scalar(
                sa.select(sa.func.count()).select_from(documents)
            )
            assert stored_rows == 1
            stored = await load_document(
                connection, source_id=SOURCE_ID, external_id=EXTERNAL_ID
            )
            assert stored == registered

        async with engine.begin() as connection:
            re_registered = make_document(
                document_id=DocumentId.new(),
                observed_at=OBSERVED_AT + timedelta(minutes=1),
            )
            assert (
                await upsert_document(connection, re_registered)
                == registered.document_id
            )
            stored = await load_document(
                connection, source_id=SOURCE_ID, external_id=EXTERNAL_ID
            )
            assert stored is not None
            assert stored.document_id == registered.document_id
            assert stored.created_at == registered.created_at

        content_changed = registered.reconcile(
            content_fingerprint="content-b",
            authorization_fingerprint="acl-a",
            source_version="18",
            observed_at=OBSERVED_AT + timedelta(minutes=2),
        )
        async with engine.begin() as connection:
            await upsert_document(connection, content_changed)
            stored = await load_document(
                connection, source_id=SOURCE_ID, external_id=EXTERNAL_ID
            )
        assert stored is not None
        assert stored.content_version == ContentVersion(2)
        assert stored.authorization_version == AuthorizationVersion(1)

        authorization_changed = content_changed.reconcile(
            content_fingerprint="content-b",
            authorization_fingerprint="acl-b",
            source_version="19",
            observed_at=OBSERVED_AT + timedelta(minutes=3),
        )
        async with engine.begin() as connection:
            await upsert_document(connection, authorization_changed)
            stored = await load_document(
                connection, source_id=SOURCE_ID, external_id=EXTERNAL_ID
            )
        assert stored is not None
        assert stored.content_version == ContentVersion(2)
        assert stored.authorization_version == AuthorizationVersion(2)

        tombstoned = authorization_changed.tombstone(
            observed_at=OBSERVED_AT + timedelta(minutes=4)
        )
        async with engine.begin() as connection:
            await upsert_document(connection, tombstoned)
            stored = await load_document(
                connection, source_id=SOURCE_ID, external_id=EXTERNAL_ID
            )
        assert stored is not None
        assert stored.availability is DocumentAvailability.TOMBSTONED
    finally:
        await engine.dispose()
```

Each write gets its own `engine.begin()` block, so a failing assertion cannot
leave a half-open transaction behind. The `DELETE` at the start makes the test
repeatable against the same disposable database.

**Check:** Run it against your disposable database, as in M02-T05:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_persistence_integration.py -vv
```

Expected: 1 passed. Run it a second time to prove it is repeatable. Do not paste
the URL into the evidence file or commit it.

### Step 7 — No coding in this step: run the gates and record the evidence

**Purpose:** Confirm the lesson works as a whole and leave M02-T07 a persisted
Document it can attach Chunks to.

**Decision:** Run the focused tests first, then the repository's configured
format, lint, type, test, documentation, and hook checks. The opt-in PostgreSQL
tests are skipped by the normal gate, so run them separately and record both
results.

**Action:** From the repository root:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_persistence.py -vv
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_migrations_integration.py tests/test_sources_integration.py tests/test_persistence_integration.py -vv
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
git diff --check
```

In `docs/evidence/M02-T06-documents-and-versions.md`, replace the placeholders
with the date, your invariant, the files you changed, the exact observed
results, the PostgreSQL version, and the answers below. Do not include
credentials, a connection URL, or private content.

**Reflection — answer in your own words:**

1. A caller now hands a `Document` to `upsert_document` and gets a
   `DocumentId` back. Name the things that function decides for the caller —
   including what it refuses to overwrite — and say which of those a caller
   would most likely get wrong if it wrote the SQL itself.
2. We could have stored one `version` column that both changes increment.
   Describe what gets harder later if we did that — think about re-embedding
   only the Documents whose text changed, and about re-checking permissions
   without touching content.
3. The integration test asserts that writing the same Document twice leaves one
   row with unchanged versions. If we deleted that test, what would go wrong in
   production that might not be noticed until the numbers look strange?

**Check:** The unit tests and all three integration tests pass; every repository
gate passes; the evidence records the real results and no connection secret.

## Completion checklist

- [ ] One hand-written revision creates `documents` with a unique
  `(source_id, external_id)`, version and availability `CHECK` constraints, and
  a foreign key to `sources`, and its `downgrade` drops the table.
- [ ] `src/knowledge_service/persistence.py` declares the same table and
  translates between a `Document` and a row without importing domain code into
  the database layer or SQLAlchemy into the domain.
- [ ] `upsert_document` is idempotent: it keeps the stored `document_id` and
  `created_at`, and updates only the columns that describe the current state.
- [ ] `load_document` reconstructs a valid `Document`, and an unknown stored
  `availability` becomes a typed `InvalidDocument`.
- [ ] An integration test proves the idempotent upsert, the independent content
  and authorization versions, and the tombstone round trip.
- [ ] Repository checks pass and the evidence records real results.

When you have finished these steps, share your implementation or any error you
hit and I will review it — I can help you work through a failure without taking
over the implementation.

## Official references

- [PostgreSQL INSERT and ON CONFLICT](https://www.postgresql.org/docs/18/sql-insert.html)
- [SQLAlchemy PostgreSQL upsert support](https://docs.sqlalchemy.org/en/21/dialects/postgresql.html#insert-on-conflict-upsert)
- [Alembic operations reference](https://alembic.sqlalchemy.org/en/latest/ops.html)
- [SQLAlchemy table metadata](https://docs.sqlalchemy.org/en/21/core/metadata.html)
- [PostgreSQL constraints](https://www.postgresql.org/docs/18/ddl-constraints.html)
- [ADR 0005 — incremental at-least-once synchronization](../adr/0005-incremental-at-least-once-synchronization.md)
