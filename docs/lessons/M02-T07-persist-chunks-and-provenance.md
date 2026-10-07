# M02-T07 — Persist Chunks and provenance

Source task: [M02-T07](../curriculum/milestones/M02-domain-and-persistence/M02-T07-persist-chunks-and-provenance.md)

## What you will build

This lesson includes coding. It stores the **Chunks** of a Document: the smaller text pieces retrieval will search and citations will point at.

A **Chunk** holds:

- the text, its heading path, and its token count;
- the source anchor a citation sends the reader back to;
- the content version the run was built from.

The acceptance rule: replacing a content version atomically removes stale chunks and preserves order. **Atomic** means the whole replacement lands or none of it does.

| Property | What it means |
|---|---|
| Atomic | The delete and the insert share the caller's transaction. |
| Ordered | Ordinals run contiguously from zero, and reads come back in that order. |
| Guarded | A write for a content version older than the stored Chunks is refused. |

## Before you begin

- `src/knowledge_service/persistence.py` holds the `documents` table, `as_row` / `as_document`, `upsert_document`, and `load_document`. This lesson adds to that module.
- `documents.py` supplies `ContentVersion`. `identifiers.py` already defines `ChunkId`.
- `chunks.py` does not exist yet.
- Integration tests apply migrations in a **synchronous** fixture, because `migrations/env.py` calls `asyncio.run()` and cannot run inside an async test.

## Walkthrough

### Step 1 — No coding in this step: write down the rule you are preserving

**Purpose:** Fix what a stored Chunk run must guarantee before writing the table.

**Decision:** Keep notes in `docs/evidence/M02-T07-chunks-and-provenance.md`.

**Action:** Read the M02-T06 and M02-T02 evidence files under `docs/evidence/`. Create `docs/evidence/M02-T07-chunks-and-provenance.md`:

```markdown
# M02-T07 — Chunks and provenance evidence

Completed: pending

## Invariant

Write down what a stored Chunk run must guarantee: how stale Chunks disappear,
what keeps the order, and what happens to a write for an older content version.

## Prediction

Write what you expect before adding the table.

## Verification

Pending.

## Reflection

Pending.
```

Confirm nothing to be created exists:

```bash
find src tests migrations -name '*chunk*' | grep -v __pycache__
```

**Check:** The evidence file holds your invariant and prediction, and the search shows no `chunks.py`, no migration, and no `chunks` table in `persistence.py`. Add no code and edit no task or progress files in this step.

### Step 2 — Coding step: model a Chunk and its run

**Purpose:** Give the ordering and provenance rules a home in the domain.

**Decision:** `Chunk.__post_init__` checks single values — ordinal, text, token count. `ordered_chunks()` checks the whole set: same Document, same content version, contiguous ordinals from zero, and returns it sorted. `StaleChunkWrite` subclasses `InvalidChunk`, so a stale write is a rule violation the caller handles, not a crash. `heading_path` and `source_anchor` stay opaque strings: the directory adapter writes one shape and the Confluence adapter another, and neither belongs in the domain.

**Action:** Create `src/knowledge_service/chunks.py`:

```python
"""Chunk values: ordered, citable sections of a Document.

A Chunk is the unit a citation points at, so it carries both the text that may
be retrieved and the location a reader can be sent back to.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId


class InvalidChunk(ValueError):
    """A Chunk value, chunk run, or chunk write violates a domain invariant."""


class StaleChunkWrite(InvalidChunk):
    """A chunk write targets a content version older than the stored one."""


@dataclass(frozen=True, slots=True)
class Chunk:
    """One ordered, provenance-preserving section of a Document."""

    chunk_id: ChunkId
    document_id: DocumentId
    content_version: ContentVersion
    ordinal: int
    text: str
    heading_path: str | None
    token_count: int
    content_fingerprint: str
    source_anchor: str | None

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise InvalidChunk("chunk ordinal must be a non-negative integer")
        if not self.text.strip():
            raise InvalidChunk("chunk text must not be empty")
        if self.heading_path is not None and not self.heading_path.strip():
            raise InvalidChunk("heading path must not be empty when provided")
        if type(self.token_count) is not int or self.token_count < 1:
            raise InvalidChunk("token count must be a positive integer")
        if not self.content_fingerprint.strip():
            raise InvalidChunk("content fingerprint must not be empty")
        if self.source_anchor is not None and not self.source_anchor.strip():
            raise InvalidChunk("source anchor must not be empty when provided")


def ordered_chunks(
    *,
    document_id: DocumentId,
    content_version: ContentVersion,
    chunks: Sequence[Chunk],
) -> tuple[Chunk, ...]:
    """Return one contiguous, ordered run of chunks for a single content version."""
    for chunk in chunks:
        if chunk.document_id != document_id:
            raise InvalidChunk("every chunk must belong to the same document")
        if chunk.content_version != content_version:
            raise InvalidChunk("every chunk must carry the same content version")

    ordered = tuple(sorted(chunks, key=lambda chunk: chunk.ordinal))
    for expected, chunk in enumerate(ordered):
        if chunk.ordinal != expected:
            raise InvalidChunk("chunk ordinals must be contiguous from zero")
    return ordered
```

**Check:** The module imports:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python -c "import knowledge_service.chunks as c; print(c.Chunk.__dataclass_fields__.keys())"
```

### Step 3 — Coding step: create the chunks table in a reviewed revision

**Purpose:** Give the run somewhere real to live, with the domain's rules enforced by PostgreSQL too.

**Decision:** One row per Chunk, keyed by `chunk_id`, with `UNIQUE (document_id, ordinal)` so two Chunks cannot claim the same position. The `content_version` column is what makes the guard in Step 5 possible. `ON DELETE CASCADE` to `documents` means deleting a Document takes its Chunks with it.

**Action:** Generate an empty revision, then write it by hand:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic revision -m "chunks"
```

Keep the generated identifier and the `down_revision` Alembic fills in — `5e8b9875be50`. Remove the generated `typing` and `branch_labels` lines as before.

```python
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "<the identifier Alembic generated>"
down_revision: str | Sequence[str] | None = "5e8b9875be50"


def upgrade() -> None:
    """Create the ordered, citable Chunks of each Document."""
    op.create_table(
        "chunks",
        sa.Column("chunk_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("content_fingerprint", sa.Text(), nullable=False),
        sa.Column("source_anchor", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "document_id", "ordinal", name="uq_chunks_document_ordinal"
        ),
        sa.CheckConstraint("content_version >= 1", name="ck_chunks_content_version"),
        sa.CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal"),
        sa.CheckConstraint("token_count >= 1", name="ck_chunks_token_count"),
    )


def downgrade() -> None:
    """Remove every stored Chunk."""
    op.drop_table("chunks")
```

Replace `<the identifier Alembic generated>` with the real identifier. Leaving the placeholder in makes Alembic use that sentence as the revision id.

**Check:** Four revisions, one head:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic history
```

### Step 4 — Coding step: declare the same table where the code can use it

**Purpose:** The migration runs once. The code needs a Python object to query through on every call.

**Decision:** Add a `chunks` `Table` to `persistence.py` beside `documents`, column for column. The table is described twice on purpose: the migration is the change history, the `Table` is the query target. Steps 6 and 7 catch drift between them.

**Action:** In `src/knowledge_service/persistence.py`, extend the imports:

```python
from collections.abc import Mapping, Sequence

from knowledge_service.chunks import Chunk, StaleChunkWrite, ordered_chunks
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
```

Add the table directly after the `documents` table:

```python
chunks = sa.Table(
    "chunks",
    metadata,
    sa.Column("chunk_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "document_id",
        sa.Uuid(),
        sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("content_version", sa.Integer(), nullable=False),
    sa.Column("ordinal", sa.Integer(), nullable=False),
    sa.Column("text", sa.Text(), nullable=False),
    sa.Column("heading_path", sa.Text(), nullable=True),
    sa.Column("token_count", sa.Integer(), nullable=False),
    sa.Column("content_fingerprint", sa.Text(), nullable=False),
    sa.Column("source_anchor", sa.Text(), nullable=True),
    sa.UniqueConstraint("document_id", "ordinal", name="uq_chunks_document_ordinal"),
    sa.CheckConstraint("content_version >= 1", name="ck_chunks_content_version"),
    sa.CheckConstraint("ordinal >= 0", name="ck_chunks_ordinal"),
    sa.CheckConstraint("token_count >= 1", name="ck_chunks_token_count"),
)
```

Add the two row-translation helpers next to `as_row` / `as_document`:

```python
def _chunk_row(chunk: Chunk) -> dict[str, object]:
    return {
        "chunk_id": chunk.chunk_id.value,
        "document_id": chunk.document_id.value,
        "content_version": chunk.content_version.number,
        "ordinal": chunk.ordinal,
        "text": chunk.text,
        "heading_path": chunk.heading_path,
        "token_count": chunk.token_count,
        "content_fingerprint": chunk.content_fingerprint,
        "source_anchor": chunk.source_anchor,
    }


def _chunk_from(record: Mapping[str, object]) -> Chunk:
    return Chunk(
        chunk_id=ChunkId(cast(UUID, record["chunk_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        content_version=ContentVersion(cast(int, record["content_version"])),
        ordinal=cast(int, record["ordinal"]),
        text=cast(str, record["text"]),
        heading_path=cast("str | None", record["heading_path"]),
        token_count=cast(int, record["token_count"]),
        content_fingerprint=cast(str, record["content_fingerprint"]),
        source_anchor=cast("str | None", record["source_anchor"]),
    )
```

Both helpers are private. The public translation surface stays the Document pair from M02-T06, and Step 5's two functions own Chunk translation.

**Check:** Ruff and Pyright accept the module:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run ruff check src/knowledge_service/persistence.py
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright src/knowledge_service/persistence.py
```

### Step 5 — Coding step: replace a run atomically, with a version guard

**Purpose:** Provide the one write ingestion needs: here is the new ordered run for this Document — store it, or refuse it as stale.

**Decision:** Validate, check the stored version, then delete and insert. All three steps run on the `AsyncConnection` the caller passes in, so they share one transaction. The guard reads `max(content_version)` for the Document and raises `StaleChunkWrite` when the stored run is newer.

**Action:** Append these two functions to `src/knowledge_service/persistence.py`:

```python
async def replace_chunks(
    connection: AsyncConnection,
    *,
    document: Document,
    chunk_run: Sequence[Chunk],
) -> tuple[Chunk, ...]:
    """Replace every stored Chunk for a Document with one ordered content version."""
    ordered = ordered_chunks(
        document_id=document.document_id,
        content_version=document.content_version,
        chunks=chunk_run,
    )

    stored_version = cast(
        "int | None",
        await connection.scalar(
            sa.select(sa.func.max(chunks.c.content_version)).where(
                chunks.c.document_id == document.document_id.value
            )
        ),
    )
    if stored_version is not None and stored_version > document.content_version.number:
        raise StaleChunkWrite(
            f"stored chunks are at content version {stored_version}; refusing to"
            f" replace them with version {document.content_version.number}"
        )

    await connection.execute(
        sa.delete(chunks).where(chunks.c.document_id == document.document_id.value)
    )
    if ordered:
        await connection.execute(
            sa.insert(chunks), [_chunk_row(chunk) for chunk in ordered]
        )
    return ordered


async def load_chunks(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
) -> tuple[Chunk, ...]:
    """Read the stored Chunks for a Document in ordinal order."""
    statement = (
        sa.select(chunks)
        .where(chunks.c.document_id == document_id.value)
        .order_by(chunks.c.ordinal)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_chunk_from(dict(record)) for record in records)
```

Three details carry the weight:

- Taking a connection instead of creating one is what makes the delete and insert atomic, and it leaves transaction ownership to the caller.
- The guard compares against the stored Chunks, not the `documents` row: the question is whether this write would discard Chunks that are already newer. A run for the same version is still allowed, so a redelivered job is idempotent.
- `order_by` on the read makes "preserves order" true for callers instead of an accident of insertion order.

**Check:** The module type-checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright src/knowledge_service/persistence.py
```

### Step 6 — Coding step: test the run rules without a database

**Purpose:** Pin down the ordering and provenance rules, which are pure logic.

**Decision:** Test `ordered_chunks()` through its public interface. The successful case proves an out-of-order input comes back sorted. The edge case is a gap in the ordinals, which is what a buggy chunker produces. The typed failures cover a Chunk from another Document, a Chunk from another content version, and an empty Chunk.

**Action:** Create `tests/test_chunks.py`:

```python
"""Tests for Chunk ordering and value invariants."""

import pytest

from knowledge_service.chunks import Chunk, InvalidChunk, ordered_chunks
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId

DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000020")
OTHER_DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000021")
CONTENT_VERSION = ContentVersion(1)


def make_chunk(
    *,
    ordinal: int,
    document_id: DocumentId = DOCUMENT_ID,
    content_version: ContentVersion = CONTENT_VERSION,
    text: str = "Some handbook text.",
) -> Chunk:
    return Chunk(
        chunk_id=ChunkId.new(),
        document_id=document_id,
        content_version=content_version,
        ordinal=ordinal,
        text=text,
        heading_path="Setup > Install",
        token_count=12,
        content_fingerprint="content-a",
        source_anchor="#install",
    )


def test_ordered_chunks_returns_the_run_in_ordinal_order() -> None:
    run = ordered_chunks(
        document_id=DOCUMENT_ID,
        content_version=CONTENT_VERSION,
        chunks=[make_chunk(ordinal=2), make_chunk(ordinal=0), make_chunk(ordinal=1)],
    )

    assert [chunk.ordinal for chunk in run] == [0, 1, 2]


def test_a_gap_in_ordinals_is_rejected() -> None:
    with pytest.raises(InvalidChunk, match="contiguous"):
        ordered_chunks(
            document_id=DOCUMENT_ID,
            content_version=CONTENT_VERSION,
            chunks=[make_chunk(ordinal=0), make_chunk(ordinal=2)],
        )


def test_a_chunk_from_another_document_is_rejected() -> None:
    with pytest.raises(InvalidChunk, match="same document"):
        ordered_chunks(
            document_id=DOCUMENT_ID,
            content_version=CONTENT_VERSION,
            chunks=[
                make_chunk(ordinal=0),
                make_chunk(ordinal=1, document_id=OTHER_DOCUMENT_ID),
            ],
        )


def test_a_chunk_from_another_content_version_is_rejected() -> None:
    with pytest.raises(InvalidChunk, match="content version"):
        ordered_chunks(
            document_id=DOCUMENT_ID,
            content_version=CONTENT_VERSION,
            chunks=[
                make_chunk(ordinal=0),
                make_chunk(ordinal=1, content_version=ContentVersion(2)),
            ],
        )


def test_empty_chunk_text_raises_typed_failure() -> None:
    with pytest.raises(InvalidChunk, match="text"):
        make_chunk(ordinal=0, text="   ")
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_chunks.py -vv
```

Expected: 5 passed, without a database.

### Step 7 — Coding step: prove atomicity, ordering, and the guard in PostgreSQL

**Purpose:** A pure test cannot show that a rolled-back replacement leaves the old Chunks in place, or that reads come back in ordinal order.

**Decision:** One async integration test over four situations: a normal replacement, a replacement abandoned mid-transaction, a committed second-version replacement, and a stale write that must be refused. `pytest.raises` wants a single simple statement inside it, so the abandoned transaction lives in a small helper.

**Action:** Create `tests/test_chunks_integration.py`:

```python
"""Real PostgreSQL tests for Chunk replacement."""

import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from knowledge_service.chunks import Chunk, StaleChunkWrite
from knowledge_service.documents import Document, DocumentProvenance
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.persistence import load_chunks, replace_chunks, upsert_document

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000020")

INSERT_SOURCE = text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


class Abandoned(Exception):
    """Raised mid-transaction to prove the replacement was never committed."""


async def replace_then_abandon(
    engine: AsyncEngine,
    *,
    document: Document,
    chunk_run: Sequence[Chunk],
) -> None:
    """Replace a run inside one transaction, then fail before it can commit."""
    async with engine.begin() as connection:
        await replace_chunks(connection, document=document, chunk_run=chunk_run)
        raise Abandoned


@pytest.fixture
def migrated_database(monkeypatch: pytest.MonkeyPatch) -> str:
    """Apply migrations synchronously; Alembic's env.py runs its own event loop."""
    database_url = os.environ.get("KNOWLEDGE_SERVICE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set KNOWLEDGE_SERVICE_TEST_DATABASE_URL to a disposable database")

    monkeypatch.setenv("KNOWLEDGE_SERVICE_DATABASE_URL", database_url)
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    return database_url


def make_document(*, observed_at: datetime = OBSERVED_AT) -> Document:
    return Document.register(
        document_id=DOCUMENT_ID,
        provenance=DocumentProvenance(
            source_id=SOURCE_ID,
            external_id="page-123",
            source_version="17",
        ),
        content_fingerprint="content-a",
        authorization_fingerprint="acl-a",
        observed_at=observed_at,
    )


def make_chunk(*, document: Document, ordinal: int, text: str) -> Chunk:
    return Chunk(
        chunk_id=ChunkId.new(),
        document_id=document.document_id,
        content_version=document.content_version,
        ordinal=ordinal,
        text=text,
        heading_path="Setup",
        token_count=8,
        content_fingerprint=document.content_fingerprint,
        source_anchor=f"#section-{ordinal}",
    )


@pytest.mark.integration
async def test_replacing_a_content_version_removes_stale_chunks_in_order(
    migrated_database: str,
) -> None:
    engine = create_async_engine(
        make_url(migrated_database).set(drivername="postgresql+psycopg")
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM chunks"))
            await connection.execute(text("DELETE FROM documents"))
            await connection.execute(text("DELETE FROM sources"))
            await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})

        document_v1 = make_document()
        async with engine.begin() as connection:
            await upsert_document(connection, document_v1)
            await replace_chunks(
                connection,
                document=document_v1,
                chunk_run=[
                    make_chunk(document=document_v1, ordinal=0, text="First"),
                    make_chunk(document=document_v1, ordinal=1, text="Second"),
                    make_chunk(document=document_v1, ordinal=2, text="Third"),
                ],
            )

        document_v2 = document_v1.reconcile(
            content_fingerprint="content-b",
            authorization_fingerprint="acl-a",
            source_version="18",
            observed_at=OBSERVED_AT + timedelta(minutes=1),
        )
        with pytest.raises(Abandoned):
            await replace_then_abandon(
                engine,
                document=document_v2,
                chunk_run=[make_chunk(document=document_v2, ordinal=0, text="Lost")],
            )

        async with engine.begin() as connection:
            stored = await load_chunks(connection, document_id=DOCUMENT_ID)
        assert [chunk.text for chunk in stored] == ["First", "Second", "Third"]

        async with engine.begin() as connection:
            await upsert_document(connection, document_v2)
            await replace_chunks(
                connection,
                document=document_v2,
                chunk_run=[
                    make_chunk(document=document_v2, ordinal=0, text="First"),
                    make_chunk(
                        document=document_v2, ordinal=1, text="Second and third"
                    ),
                ],
            )
            stored = await load_chunks(connection, document_id=DOCUMENT_ID)
        assert [chunk.ordinal for chunk in stored] == [0, 1]
        assert [chunk.text for chunk in stored] == ["First", "Second and third"]
        assert {chunk.content_version.number for chunk in stored} == {2}

        async with engine.begin() as connection:
            with pytest.raises(StaleChunkWrite):
                await replace_chunks(
                    connection,
                    document=document_v1,
                    chunk_run=[
                        make_chunk(document=document_v1, ordinal=0, text="Stale")
                    ],
                )
            stored = await load_chunks(connection, document_id=DOCUMENT_ID)
        assert [chunk.text for chunk in stored] == ["First", "Second and third"]
    finally:
        await engine.dispose()
```

After the abandoned attempt the three original Chunks are still stored; only the committed `v2` replacement shrinks the run to two with `content_version` 2. That pair of assertions is the atomicity proof: it fails if `replace_chunks` ever commits on its own.

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_chunks_integration.py -vv
```

Expected: 1 passed. Run it twice to confirm it repeats. Do not paste the URL into the evidence file or commit it.

### Step 8 — No coding in this step: run the gates and record the evidence

**Purpose:** Confirm the lesson works as a whole before M02-T08 builds on it.

**Decision:** Run the focused tests first, then every repository gate. The normal gate skips the opt-in PostgreSQL tests, so run those separately and record both results.

**Action:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_chunks.py -vv
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration -vv
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
git diff --check
```

In `docs/evidence/M02-T07-chunks-and-provenance.md`, replace the placeholders with the date, your invariant, the files you changed, the exact observed results, the PostgreSQL version, and the answers below. Add no credential, connection URL, or private content.

**Reflection — answer in your own words:**

1. `replace_chunks` is a delete followed by an insert. Name what would go wrong if it opened its own connection and committed by itself, instead of using the connection its caller passed in.
2. We could have stored only the Chunk text and rebuilt the order from a character offset into the Document. Describe what gets harder later if we did that — think about a re-chunking that splits one section in two, and about a citation that has to point at a page in Confluence rather than at a character position in a file.
3. The integration test asserts that a rolled-back replacement leaves the old Chunks in place. If we deleted that test, what production failure might go unnoticed until users were reading the wrong text?

**Check:** The unit tests and every integration test pass; all repository gates pass; the evidence records the real results and no connection secret.

## Completion checklist

- [ ] One hand-written revision creates `chunks` with a unique `(document_id, ordinal)`, version/ordinal/token-count `CHECK` constraints, and a cascading foreign key to `documents`.
- [ ] `src/knowledge_service/chunks.py` models a Chunk and validates a whole run for document, content version, and contiguous ordinals, and imports no SQLAlchemy.
- [ ] `replace_chunks` validates, refuses a stale content version, then deletes and inserts on the caller's connection, so the replacement is atomic.
- [ ] `load_chunks` returns the run in ordinal order.
- [ ] Deterministic tests cover ordering, the ordinal gap, and the typed failures.
- [ ] An integration test proves the rolled-back replacement changes nothing, the second replacement removes the stale run, and a stale write is refused.
- [ ] Repository checks pass and the evidence records real results.

Share your implementation or any error you hit and I will review it.
