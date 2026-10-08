# M02-T11 — Define transaction ownership

Source task: [M02-T11](../curriculum/milestones/M02-domain-and-persistence/M02-T11-define-transaction-ownership.md)

## What you will build

This lesson includes coding. It settles who opens a transaction, then uses that boundary to publish a Document's whole state at once.

Three pieces:

- `DatabaseRuntime.transaction()`, the one place a transaction is opened.
- A **Publication**: one Document with the Chunks and Access Grants to store with it, written as a single unit of work.
- A written policy saying where external calls sit relative to transactions and locks.

| Property | What it means |
|---|---|
| Workflows own the boundary | Repository functions take a connection and never commit; the workflow decides when work is final. |
| All or nothing | A Document's row, Chunks, and Access Grants are written in one transaction. |
| Invisible until commit | A reader sees the previous complete version while a publication is in flight. |

## Before you begin

- Every function in `persistence.py` already takes an `AsyncConnection` and never commits. This lesson is what that shape was for.
- `src/knowledge_service/database.py` holds `DatabaseRuntime` and `create_database_runtime`.
- `upsert_document` returns the identity already on record, which may differ from the one proposed. That matters in Step 3.
- `docs/adr/README.md` indexes the decision records; a new ADR needs a line there.
- Integration tests apply migrations in a **synchronous** fixture, because `migrations/env.py` calls `asyncio.run()` and cannot run inside an async test.

## Walkthrough

### Step 1 — No coding in this step: write down the rule you are preserving

**Purpose:** Fix who owns the transaction before writing anything that depends on the answer.

**Decision:** Keep notes in `docs/evidence/M02-T11-transaction-ownership.md`.

**Action:** Read the M02-T10 evidence file under `docs/evidence/`. Create `docs/evidence/M02-T11-transaction-ownership.md`:

```markdown
# M02-T11 — Transaction ownership evidence

Completed: pending

## Invariant

Write down who opens and closes a transaction, what a Document is published as,
and what a reader may see while a publication is in flight.

## Prediction

Write what you expect before adding the boundary.

## Verification

Pending.

## Reflection

Pending.
```

Confirm nothing to be created exists:

```bash
find src tests -name '*publishing*' | grep -v __pycache__
```

**Check:** The evidence file holds your invariant and prediction, and the search shows no publishing module and no publishing test. Add no code and edit no task or progress files in this step.

### Step 2 — Coding step: give the runtime the only transaction boundary

**Purpose:** Provide one place to open a unit of work, so no repository function has to decide when work becomes final.

**Decision:** `DatabaseRuntime.transaction()` wraps `engine.begin()`, which opens a transaction, commits when the block exits cleanly, and rolls back when it raises. The yielded `AsyncConnection` is the only thing a repository function sees. The annotation is `AsyncGenerator`, which is what `@asynccontextmanager` wants from an async generator; `AsyncIterator` still works but is deprecated.

**Action:** In `src/knowledge_service/database.py`, extend the imports and module docstring:

```python
"""Asynchronous PostgreSQL engine, sessions, and transaction boundaries."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
```

Then add the method to `DatabaseRuntime`, before `dispose`:

```python
    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[AsyncConnection]:
        """Run one unit of work in one transaction, committing on a clean exit."""
        async with self.engine.begin() as connection:
            yield connection
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_database.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright src/knowledge_service/database.py
```

### Step 3 — Coding step: publish a Document as one unit of work

**Purpose:** Make "this Document, its Chunks, and its Access Grants" one thing a caller can ask for, and give it a rule about its own parts.

**Decision:** `Publication` checks that each Chunk carries the Document's id, content version, and content fingerprint, and that each Grant belongs to that Document — so a mismatched set fails before any transaction opens. `publish_document` then writes the row, the Chunks, and the Grants on one connection. It re-reads the stored identity from `upsert_document` and rewrites the Chunks against it: a re-published source item keeps the Document id already on record, and the Chunks must point at that id, not at the proposed one. `publish_documents` takes the runtime and owns the boundary, so a whole batch commits or none of it does.

**Action:** Create `src/knowledge_service/publishing.py`:

```python
"""Publishing: store a Document's complete state inside one transaction.

A Document is published when its row, its Chunks, and its Access Grants all
describe the same content version. This module builds that unit of work; the
caller owns the transaction it runs in.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AccessGrant
from knowledge_service.chunks import Chunk
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document
from knowledge_service.identifiers import DocumentId
from knowledge_service.persistence import (
    replace_access_grants,
    replace_chunks,
    upsert_document,
)


class InvalidPublication(ValueError):
    """A Publication holds parts that do not describe one Document version."""


@dataclass(frozen=True, slots=True)
class Publication:
    """One Document together with the Chunks and Access Grants to store with it."""

    document: Document
    chunks: tuple[Chunk, ...] = ()
    grants: tuple[AccessGrant, ...] = ()

    def __post_init__(self) -> None:
        for chunk in self.chunks:
            if chunk.document_id != self.document.document_id:
                raise InvalidPublication("every chunk must belong to the document")
            if chunk.content_version != self.document.content_version:
                raise InvalidPublication(
                    "every chunk must carry the document content version"
                )
            if chunk.content_fingerprint != self.document.content_fingerprint:
                raise InvalidPublication(
                    "every chunk must carry the document content fingerprint"
                )
        for grant in self.grants:
            if grant.document_id != self.document.document_id:
                raise InvalidPublication("every grant must belong to the document")


async def publish_document(
    connection: AsyncConnection, publication: Publication
) -> DocumentId:
    """Store a Publication's row, Chunks, and Access Grants on one connection."""
    document = publication.document
    stored_id = await upsert_document(connection, document)
    stored_document = replace(document, document_id=stored_id)
    chunk_run = [replace(chunk, document_id=stored_id) for chunk in publication.chunks]
    await replace_chunks(connection, document=stored_document, chunk_run=chunk_run)
    await replace_access_grants(
        connection, document_id=stored_id, grant_run=publication.grants
    )
    return stored_id


async def publish_documents(
    runtime: DatabaseRuntime,
    *,
    publications: Sequence[Publication],
) -> tuple[DocumentId, ...]:
    """Publish every Publication in one transaction, or publish none of them."""
    async with runtime.transaction() as connection:
        published: list[DocumentId] = []
        for publication in publications:
            published.append(await publish_document(connection, publication))
        return tuple(published)
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright src/knowledge_service/publishing.py
```

### Step 4 — No coding beyond the record: write the transaction policy

**Purpose:** Record where external calls sit relative to transactions and locks, so the next workflow follows the same rule.

**Decision:** A new decision record rather than a note in a task, because the choice is costly to reverse and surprising without context.

**Action:** Add the new record as entry 12 in the numbered list in `docs/adr/README.md`, titled "Application workflows own transaction boundaries" and pointing at `0012-application-workflows-own-transaction-boundaries.md`. Then create that file:

```markdown
---
status: accepted
---

# Application workflows own transaction boundaries

Repository functions take an `AsyncConnection` and never commit. An application
workflow opens one transaction with `DatabaseRuntime.transaction()`, calls the
repository functions it needs, and commits or rolls back once, at the end.

## Consequences

- A Document is published when its row, its Chunks, and its Access Grants are
  written in one transaction. A reader sees the previous complete version or the
  new one, never a mixture of the two.
- External calls — model providers, source APIs, embedding services — happen
  outside the transaction. No transaction is held open across a network call,
  and no row is locked before one.
- A workflow that fails publishes nothing, including the writes it had already
  made inside its own transaction.
- A redelivered job that arrives out of order is refused by the domain inside
  the transaction, so the older run cannot overwrite a newer published version.
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
```

### Step 5 — Coding step: test the Publication rule without a database

**Purpose:** Pin down what a Publication may hold, which is pure logic.

**Decision:** Test through the public interface. The successful case proves a consistent set is accepted. The edge case is a Publication with no grants, which is a Document nobody may read rather than an error. The typed failures cover a Chunk carrying a different content version and a Grant belonging to another Document.

**Action:** Create `tests/test_publishing.py`:

```python
"""Tests for the unit of work that publishes a Document."""

from datetime import UTC, datetime

import pytest

from knowledge_service.access import AccessGrant, Subject
from knowledge_service.chunks import Chunk
from knowledge_service.documents import ContentVersion, Document, DocumentProvenance
from knowledge_service.identifiers import (
    AccessGrantId,
    ChunkId,
    DocumentId,
    SourceId,
    UserId,
)
from knowledge_service.publishing import InvalidPublication, Publication

OBSERVED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000020")
OTHER_DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000021")
USER_ID = UserId.parse("00000000-0000-0000-0000-000000000030")


def registered_document() -> Document:
    return Document.register(
        document_id=DOCUMENT_ID,
        provenance=DocumentProvenance(
            source_id=SOURCE_ID, external_id="page-1", source_version="1"
        ),
        content_fingerprint="content-a",
        authorization_fingerprint="acl-a",
        observed_at=OBSERVED_AT,
    )


def make_chunk(
    document: Document,
    *,
    ordinal: int = 0,
    content_version: ContentVersion | None = None,
) -> Chunk:
    return Chunk(
        chunk_id=ChunkId.new(),
        document_id=document.document_id,
        content_version=content_version or document.content_version,
        ordinal=ordinal,
        text="Install the service.",
        heading_path="Setup",
        token_count=6,
        content_fingerprint=document.content_fingerprint,
        source_anchor="#setup",
    )


def test_a_publication_carries_one_document_version() -> None:
    document = registered_document()
    publication = Publication(document=document, chunks=(make_chunk(document),))

    assert publication.document is document
    assert len(publication.chunks) == 1


def test_a_publication_without_grants_is_accepted() -> None:
    document = registered_document()

    assert Publication(document=document).grants == ()


def test_a_chunk_from_another_content_version_is_rejected() -> None:
    document = registered_document()
    stale = make_chunk(document, content_version=ContentVersion(2))

    with pytest.raises(InvalidPublication, match="content version"):
        Publication(document=document, chunks=(stale,))


def test_a_grant_for_another_document_is_rejected() -> None:
    document = registered_document()
    elsewhere = AccessGrant(
        grant_id=AccessGrantId.new(),
        document_id=OTHER_DOCUMENT_ID,
        subject=Subject.user(USER_ID),
        granted_at=OBSERVED_AT,
    )

    with pytest.raises(InvalidPublication, match="grant"):
        Publication(document=document, grants=(elsewhere,))
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_publishing.py -vv
```

Expected: 4 passed, without a database.

### Step 6 — Coding step: inject a failure and prove nothing half-published is visible

**Purpose:** A pure test cannot show that a failed publication leaves the previous version intact, or that an in-flight one stays invisible.

**Decision:** Two async integration tests. The first publishes version 1, then version 2, then attempts a batch that starts with a redelivered version 1 — which `replace_chunks` refuses with `StaleChunkWrite` *after* the Document row was already rewritten inside the transaction. The second holds a transaction open and reads from a second connection while it is uncommitted.

**Action:** Create `tests/test_publishing_integration.py`:

```python
"""Real PostgreSQL tests for transaction ownership while publishing."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from knowledge_service.access import AccessGrant, Subject
from knowledge_service.chunks import Chunk, StaleChunkWrite
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import Document, DocumentProvenance
from knowledge_service.identifiers import (
    AccessGrantId,
    ChunkId,
    DocumentId,
    SourceId,
    UserId,
)
from knowledge_service.persistence import load_chunks, load_document
from knowledge_service.publishing import (
    Publication,
    publish_document,
    publish_documents,
)
from knowledge_service.settings import Settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
USER_ID = UserId.parse("00000000-0000-0000-0000-000000000030")

INSERT_SOURCE = text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


@pytest.fixture
def migrated_database(monkeypatch: pytest.MonkeyPatch) -> str:
    """Apply migrations synchronously; Alembic's env.py runs its own event loop."""
    database_url = os.environ.get("KNOWLEDGE_SERVICE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set KNOWLEDGE_SERVICE_TEST_DATABASE_URL to a disposable database")

    monkeypatch.setenv("KNOWLEDGE_SERVICE_DATABASE_URL", database_url)
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    return database_url


def registered_document(external_id: str, *, content_fingerprint: str) -> Document:
    return Document.register(
        document_id=DocumentId.new(),
        provenance=DocumentProvenance(
            source_id=SOURCE_ID, external_id=external_id, source_version="1"
        ),
        content_fingerprint=content_fingerprint,
        authorization_fingerprint="acl-a",
        observed_at=OBSERVED_AT,
    )


def publication_for(document: Document, *, text_body: str) -> Publication:
    return Publication(
        document=document,
        chunks=(
            Chunk(
                chunk_id=ChunkId.new(),
                document_id=document.document_id,
                content_version=document.content_version,
                ordinal=0,
                text=text_body,
                heading_path="Setup",
                token_count=4,
                content_fingerprint=document.content_fingerprint,
                source_anchor="#setup",
            ),
        ),
        grants=(
            AccessGrant(
                grant_id=AccessGrantId.new(),
                document_id=document.document_id,
                subject=Subject.user(USER_ID),
                granted_at=OBSERVED_AT,
            ),
        ),
    )


async def start_from_an_empty_database(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(text("DELETE FROM message_feedback"))
        await connection.execute(text("DELETE FROM messages"))
        await connection.execute(text("DELETE FROM conversations"))
        await connection.execute(text("DELETE FROM access_grants"))
        await connection.execute(text("DELETE FROM chunks"))
        await connection.execute(text("DELETE FROM documents"))
        await connection.execute(text("DELETE FROM sources"))
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})


@pytest.mark.integration
async def test_a_failed_publish_leaves_no_half_published_document(
    migrated_database: str,
) -> None:
    runtime = create_database_runtime(Settings())
    try:
        await start_from_an_empty_database(runtime)

        first = registered_document("page-a", content_fingerprint="content-a")
        second = first.reconcile(
            content_fingerprint="content-b",
            authorization_fingerprint="acl-a",
            source_version="2",
            observed_at=OBSERVED_AT + timedelta(minutes=1),
        )
        elsewhere = registered_document("page-b", content_fingerprint="content-b")

        published = await publish_documents(
            runtime,
            publications=[
                publication_for(first, text_body="First version."),
                publication_for(second, text_body="Second version."),
            ],
        )
        assert published == (first.document_id, first.document_id)

        with pytest.raises(StaleChunkWrite):
            await publish_documents(
                runtime,
                publications=[
                    publication_for(first, text_body="Redelivered."),
                    publication_for(elsewhere, text_body="Never published."),
                ],
            )

        async with runtime.transaction() as connection:
            stored = await load_document(
                connection, source_id=SOURCE_ID, external_id="page-a"
            )
            chunks = await load_chunks(connection, document_id=first.document_id)
            absent = await load_document(
                connection, source_id=SOURCE_ID, external_id="page-b"
            )

        assert stored is not None
        assert stored.content_version.number == 2
        assert [chunk.text for chunk in chunks] == ["Second version."]
        assert {chunk.content_version.number for chunk in chunks} == {2}
        assert absent is None
    finally:
        await runtime.dispose()


@pytest.mark.integration
async def test_an_uncommitted_publication_is_invisible_until_it_commits(
    migrated_database: str,
) -> None:
    runtime = create_database_runtime(Settings())
    try:
        await start_from_an_empty_database(runtime)

        first = registered_document("page-a", content_fingerprint="content-a")
        await publish_documents(
            runtime, publications=[publication_for(first, text_body="First version.")]
        )

        upgraded = first.reconcile(
            content_fingerprint="content-b",
            authorization_fingerprint="acl-a",
            source_version="2",
            observed_at=OBSERVED_AT + timedelta(minutes=1),
        )
        async with runtime.transaction() as connection:
            await publish_document(
                connection, publication_for(upgraded, text_body="Second version.")
            )
            async with runtime.transaction() as observer:
                during = await load_document(
                    observer, source_id=SOURCE_ID, external_id="page-a"
                )
                chunks_during = await load_chunks(
                    observer, document_id=first.document_id
                )

            assert during is not None
            assert during.content_version.number == 1
            assert [chunk.text for chunk in chunks_during] == ["First version."]

        async with runtime.transaction() as connection:
            after = await load_document(
                connection, source_id=SOURCE_ID, external_id="page-a"
            )
            chunks_after = await load_chunks(connection, document_id=first.document_id)

        assert after is not None
        assert after.content_version.number == 2
        assert [chunk.text for chunk in chunks_after] == ["Second version."]
    finally:
        await runtime.dispose()
```

The first test is the failure injection. The redelivered publication rewrites the Document row to content version 1 before the Chunk write refuses it, so without the shared transaction the Document would be left at version 1 while its Chunks stayed at version 2. The assertions prove the row is still at version 2, its Chunks are still the version 2 run, and the second Document in the failed batch was never published at all.

The second test proves visibility rather than rollback: while the publication is uncommitted, a second connection reads the version 1 row and its version 1 Chunks; after the block exits, the same reads return version 2.

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_publishing_integration.py -vv
```

Expected: 2 passed. Run them twice to confirm they repeat. Do not paste the URL into the evidence file or commit it.

### Step 7 — No coding in this step: run the gates and record the evidence

**Purpose:** Confirm the lesson works as a whole before M02-T12 builds on it.

**Decision:** Run the focused tests first, then every repository gate, including the documentation check that validates the new ADR and its index entry.

**Action:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_publishing.py -vv
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration -vv
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
git diff --check
```

In `docs/evidence/M02-T11-transaction-ownership.md`, replace the placeholders with the date, your invariant, the files you changed, the exact observed results, the PostgreSQL version, and the answers below. Add no credential, connection URL, or private content.

**Reflection — answer in your own words:**

1. `publish_document` takes a connection and `publish_documents` takes the runtime. Name what each is responsible for, and describe what would break if the workflow let every repository function open its own connection.
2. The transaction could have been opened inside each repository function instead. Describe what gets harder later if it were — think about publishing a Document's row, Chunks, and Grants together, and about a redelivered job that arrives after a newer one.
3. The second integration test asserts that a reader sees the old version while a publication is in flight. If we removed that assertion, what production failure might go unnoticed until someone read a Document mid-update?

**Check:** The unit tests and every integration test pass; all repository gates pass, including `make docs-check` over the new decision record; the evidence records the real results and no connection secret.

## Completion checklist

- [ ] `DatabaseRuntime.transaction()` is the only place a transaction is opened, and it commits on a clean exit.
- [ ] `src/knowledge_service/publishing.py` models a `Publication` whose parts must describe one Document version, and imports SQLAlchemy only for the connection type.
- [ ] `publish_document` writes the row, the Chunks, and the Grants on the caller's connection, and rewrites the Chunks against the stored identity.
- [ ] `publish_documents` owns the transaction, so a batch publishes entirely or not at all.
- [ ] `docs/adr/0012-application-workflows-own-transaction-boundaries.md` records the policy and is listed in `docs/adr/README.md`.
- [ ] The failure-injection test proves a refused redelivery leaves the published version untouched, and the visibility test proves an uncommitted publication is invisible.
- [ ] Repository checks pass and the evidence records real results.

Share your implementation or any error you hit and I will review it.
