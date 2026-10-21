# M03-T12 — Reconcile a Document transactionally

Source task: [M03-T12](../curriculum/milestones/M03-local-ingestion/M03-T12-reconcile-a-document-transactionally.md)

## What you will do

This lesson includes coding: Steps 2–4 write code, while Steps 1 and 5 do not.

Everything from M03 now meets the database. On every Synchronization Run, each normalized document must be compared with what is stored, and only what changed must be written: nothing for an unchanged file, new Chunks for changed text, new Access Grants for changed readers. Readers must never see half of an update.

| Requirement | How this lesson meets it |
|---|---|
| Compare fingerprints | The content fingerprint (M03-T08) and the authorization fingerprint go into `Document.register` or `Document.reconcile`, which already decide which version to raise. |
| Replace changed chunks | Changed content is chunked with `chunk_by_structure`, given anchors with `chunk_sources`, and replaces the stored Chunks. |
| Update ACL-only records without re-chunking | An access-only change replaces the Access Grants and leaves the Chunks untouched, with the same Chunk IDs. |
| Publish a single current version | The Document row, its Chunks, and its Access Grants are written in one transaction. |
| Failure injection | A test makes the grant write fail after the new Chunks are written, and proves the old version is still what readers see. |

Terms used in this lesson:

- **Transaction:** a group of database writes that is committed all at once or not at all. A reader never sees part of it.
- **Atomic:** all-or-nothing, as a transaction is.
- **Row lock (`SELECT ... FOR UPDATE`):** a read that also locks the row until the transaction ends, so another transaction that wants to change the same row waits.
- **Race condition:** a bug that appears only when two runs overlap in time, such as two workers reconciling the same file at once.

## Before you begin

- **ADR 0012 sets the rules.** A workflow owns one transaction; repository functions never commit; readers see the previous complete version or the new one; an out-of-order redelivery is refused inside the transaction. Read `docs/adr/0012-application-workflows-own-transaction-boundaries.md`.
- **Readers come in as normalized subjects.** Turning a Source's principals into users and groups is identity work for M07, so `reconcile_document` takes `readers: Sequence[Subject]`; an empty sequence means nobody may read the document.
- **Why the row is locked.** An adversarial review ran two reconciles of the same document at once against real PostgreSQL. Without a lock, each read the stored row, decided from it, and then wrote, so one decision was made from a stale copy. It produced three breaks: a published mix of two versions (a row from one run, Chunks from another), an older observation overwriting a newer one, and a broken row (`updated_at` earlier than `created_at`) that made the document unreadable for good. Step 2 adds the two persistence functions that close them.
- **Running the integration tests.** They need a disposable PostgreSQL server and skip without one. Set the URL for your `knowledge-service-postgres` container in your shell first; use your test user's real password:

  ```bash
  export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="postgresql://test_user:<password>@localhost:5433/knowledge_service_test"
  ```

  Never write the real password into a file in the repository.
- **Files you will touch:**
  - `src/knowledge_service/persistence.py`
  - new `src/knowledge_service/reconciliation.py`, `tests/test_reconciliation.py`, and `tests/test_reconciliation_integration.py`
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your prediction

**Invariant:** after `reconcile_document` returns, the stored Document row, its Chunks, and its Access Grants describe one version: every Chunk carries the row's content version and content fingerprint, and the grants match the row's authorization fingerprint. A reader sees either the previous complete version or the new one. Nothing is written when nothing changed, an access-only change keeps the same Chunks, and an observation older than the stored one is refused without changing anything, even when two runs overlap.

**Action:** create `docs/evidence/M03-T12-transactional-reconciliation.md`:

```markdown
# M03-T12 — Transactional reconciliation evidence

Completed: pending

## Invariant

Pending.

## Prediction

Before writing code, say what `reconcile_document` returns and what is stored
for: a new document; the same document again; changed text with the same
readers; the same text with one more reader; a new source version only; a
failure while writing the grants after the new chunks were written; a reader
looking while a reconcile is in progress; an older observation after a newer
one; an older and a newer observation running at the same time; and two runs
creating the same new document at once.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and your predictions.

### Step 2 — Coding step: two persistence functions

| Function | Why |
|---|---|
| `insert_document` | Stores a new Document with `ON CONFLICT DO NOTHING` and returns whether it did. Unlike `upsert_document`, it never overwrites a row, so a run that lost a race to create the Document can decide again against the stored one. |
| `load_document(..., for_update=True)` | Locks the row with `SELECT ... FOR UPDATE` until the transaction ends. A second reconcile of the same document waits there, then decides from what the first one committed. |

**Action:** in `src/knowledge_service/persistence.py`, replace `load_document` and add `insert_document` just before it, so this whole part reads:

```python
async def insert_document(connection: AsyncConnection, document: Document) -> bool:
    """Store a new Document, or return False if its source item is already stored.

    Unlike `upsert_document`, this never overwrites a row, so a run that lost
    a race to create the Document can decide again against the stored one.
    """
    statement = (
        insert(documents)
        .values(as_row(document))
        .on_conflict_do_nothing(constraint="uq_documents_source_external")
        .returning(documents.c.document_id)
    )
    return (await connection.execute(statement)).scalar_one_or_none() is not None


async def load_document(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    external_id: str,
    for_update: bool = False,
) -> Document | None:
    """Read the stored Document for one source item, if it exists.

    With `for_update`, the row stays locked until the transaction ends, so a
    concurrent writer waits instead of deciding from a stale copy.
    """
    statement = sa.select(documents).where(
        documents.c.source_id == source_id.value,
        documents.c.external_id == external_id,
    )
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return as_document(dict(record))
```

`for_update` defaults to `False`, so every existing caller keeps its behaviour.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes, and `uv run pytest -m "not integration and not live" -q` still passes.

### Step 3 — Coding step: the reconciliation workflow

How `reconcile_document` works, in order:

1. **Outside the transaction:** compute the content fingerprint, and the authorization fingerprint from the readers. Only the readers count in it, so a provisional Document ID is enough.
2. **Lock the stored row** with `load_document(..., for_update=True)`.
3. **No stored row:** `Document.register` a new one and `insert_document` it. If that succeeds, write its Chunks and grants and return `CREATED`. If another run created it first, lock that row and continue as below.
4. **A stored row:** `stored.reconcile(...)` returns the same object when nothing changed (return `UNCHANGED`, write nothing), raises `InvalidDocument` for an older observation, or returns a new Document with only the changed versions raised.
5. **Write only what changed:** the row always; Chunks when the content version rose or the Document was tombstoned; grants when the authorization version rose.

| `Change` | When |
|---|---|
| `CREATED` | The Document was new. |
| `UNCHANGED` | Nothing changed; nothing was written. |
| `CONTENT` | The text changed; Chunks replaced, grants kept. |
| `ACCESS` | The readers changed; grants replaced, Chunks kept. |
| `CONTENT_AND_ACCESS` | Both changed. |
| `RESTORED` | A tombstoned Document came back unchanged; its Chunks are rebuilt (M03-T13 will tombstone). |
| `SOURCE_VERSION` | Only the source's version label changed; only the row is updated. |

Decisions:

- **One transaction for everything,** opened by the workflow, as ADR 0012 requires. A failure anywhere rolls back every write.
- **Lock before deciding.** The decision (`register` or `reconcile`) is only correct if the stored row cannot change underneath it.
- **Chunking happens inside the transaction.** It is local computation, not a network call, so ADR 0012 allows it. When embeddings arrive, those network calls must happen outside, before the transaction.
- **New grant IDs only when access changed.** An unchanged or content-only update keeps the stored grants exactly, so nothing churns.

**Action:** create `src/knowledge_service/reconciliation.py`:

```python
"""Reconcile one normalized Document with the stored, searchable state.

Each Synchronization Run sees most documents again unchanged. Reconciling
compares the new content and authorization fingerprints with the stored ones
and writes only what changed, all in one transaction:

- nothing changed: nothing is written;
- the content changed: the Chunks are rebuilt and replaced;
- only who may read it changed: the Access Grants are replaced, and the Chunks
  are kept, because re-chunking would change nothing.

A reader sees either the previous complete version or the new one, never a
mixture, because the Document row, its Chunks, and its Access Grants are
written together and committed once.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AccessGrant, Subject
from knowledge_service.chunking import chunk_by_structure
from knowledge_service.chunks import Chunk
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import (
    Document,
    DocumentAvailability,
    InvalidDocument,
)
from knowledge_service.fingerprints import (
    authorization_fingerprint,
    content_fingerprint,
)
from knowledge_service.identifiers import AccessGrantId, ChunkId, DocumentId
from knowledge_service.normalization import NormalizedDocument
from knowledge_service.persistence import (
    insert_document,
    load_document,
    replace_access_grants,
    replace_chunks,
    upsert_document,
)
from knowledge_service.provenance import chunk_sources


class Change(StrEnum):
    """What one reconciliation did to the stored state."""

    CREATED = "created"
    UNCHANGED = "unchanged"
    CONTENT = "content"
    ACCESS = "access"
    CONTENT_AND_ACCESS = "content_and_access"
    RESTORED = "restored"
    SOURCE_VERSION = "source_version"


@dataclass(frozen=True, slots=True)
class Reconciliation:
    """The Document as stored after reconciling, and what changed."""

    document: Document
    change: Change


async def reconcile_document(
    runtime: DatabaseRuntime,
    normalized: NormalizedDocument,
    *,
    readers: Sequence[Subject],
    observed_at: datetime,
) -> Reconciliation:
    """Publish one observation of a document, writing only what changed.

    `readers` are the normalized subjects who may read it; an empty sequence
    means nobody may. An observation older than the stored version raises
    `InvalidDocument`, and nothing is written.
    """
    provenance = normalized.provenance
    content = content_fingerprint(normalized.parsed)
    # Only the readers count in this fingerprint, so a provisional ID will do.
    provisional = DocumentId.new()
    access = authorization_fingerprint(
        document_id=provisional, grants=_grants(provisional, readers, observed_at)
    )
    async with runtime.transaction() as connection:
        # Lock the stored row: a concurrent run of the same document waits
        # here until this one commits, then decides from what it committed.
        stored = await load_document(
            connection,
            source_id=provenance.source_id,
            external_id=provenance.external_id,
            for_update=True,
        )
        if stored is None:
            created = Document.register(
                document_id=DocumentId.new(),
                provenance=provenance,
                content_fingerprint=content,
                authorization_fingerprint=access,
                observed_at=observed_at,
            )
            if await insert_document(connection, created):
                await _write_chunks(connection, normalized, created)
                await _write_grants(connection, created, readers, observed_at)
                return Reconciliation(document=created, change=Change.CREATED)
            # Another run created it after the lookup; decide against its row.
            stored = await load_document(
                connection,
                source_id=provenance.source_id,
                external_id=provenance.external_id,
                for_update=True,
            )
            if stored is None:
                raise InvalidDocument("the document vanished while reconciling")

        document = stored.reconcile(
            content_fingerprint=content,
            authorization_fingerprint=access,
            source_version=provenance.source_version,
            observed_at=observed_at,
        )
        if document is stored:
            return Reconciliation(document=stored, change=Change.UNCHANGED)
        content_changed = document.content_version != stored.content_version
        access_changed = document.authorization_version != stored.authorization_version
        restored = stored.availability is DocumentAvailability.TOMBSTONED
        await upsert_document(connection, document)
        # A restored Document gets its Chunks back even if its text is unchanged.
        if content_changed or restored:
            await _write_chunks(connection, normalized, document)
        if access_changed:
            await _write_grants(connection, document, readers, observed_at)
    return Reconciliation(
        document=document,
        change=_change(content_changed, access_changed, restored),
    )


async def _write_chunks(
    connection: AsyncConnection, normalized: NormalizedDocument, document: Document
) -> None:
    await replace_chunks(
        connection, document=document, chunk_run=build_chunks(normalized, document)
    )


async def _write_grants(
    connection: AsyncConnection,
    document: Document,
    readers: Sequence[Subject],
    observed_at: datetime,
) -> None:
    await replace_access_grants(
        connection,
        document_id=document.document_id,
        grant_run=_grants(document.document_id, readers, observed_at),
    )


def build_chunks(
    normalized: NormalizedDocument, document: Document
) -> tuple[Chunk, ...]:
    """Chunk a document by its structure and give each chunk its source anchor."""
    drafts = chunk_by_structure(normalized.parsed)
    sources = chunk_sources(
        normalized.parsed,
        drafts,
        external_id=normalized.provenance.external_id,
    )
    return tuple(
        Chunk(
            chunk_id=ChunkId.new(),
            document_id=document.document_id,
            content_version=document.content_version,
            ordinal=draft.ordinal,
            text=draft.text,
            heading_path=draft.heading_path,
            token_count=draft.token_count,
            content_fingerprint=document.content_fingerprint,
            source_anchor=source.anchor,
        )
        for draft, source in zip(drafts, sources, strict=True)
    )


def _grants(
    document_id: DocumentId, readers: Sequence[Subject], observed_at: datetime
) -> list[AccessGrant]:
    return [
        AccessGrant(
            grant_id=AccessGrantId.new(),
            document_id=document_id,
            subject=subject,
            granted_at=observed_at,
        )
        for subject in readers
    ]


def _change(content: bool, access: bool, restored: bool) -> Change:
    if content and access:
        return Change.CONTENT_AND_ACCESS
    if content:
        return Change.CONTENT
    if access:
        return Change.ACCESS
    if restored:
        return Change.RESTORED
    return Change.SOURCE_VERSION
```

Some details:

- **`if document is stored`** uses identity, not equality: `Document.reconcile` returns the very same object when nothing changed, which is cheaper and clearer than comparing every field.
- **`_write_chunks` and `_write_grants`** look up `replace_chunks` and `replace_access_grants` through this module's names. That is what lets the tests replace them, to inject a failure or a pause.
- **`build_chunks`** turns each `DraftChunk` and its `ChunkSource` into a stored `Chunk`; `zip(..., strict=True)` raises if the two lists ever differ in length.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 4 — Coding step: tests

`tests/test_reconciliation.py` checks `build_chunks` without a database. `tests/test_reconciliation_integration.py` runs every case against real PostgreSQL:

| Test | Proves |
|---|---|
| `test_a_new_document_is_published_with_chunks_and_grants` | The successful case. |
| `test_an_unchanged_document_writes_nothing` | Same row, Chunk IDs, and grant IDs. |
| `test_a_content_change_replaces_chunks_and_keeps_grants` | Content version 2, authorization version 1. |
| `test_an_access_change_keeps_chunks_without_rechunking` | Same Chunk IDs; grants replaced. |
| `test_a_failure_mid_publish_leaves_the_old_version_searchable` | **The acceptance criterion:** an injected failure after the Chunks were written leaves the old state exactly. |
| `test_readers_see_the_old_version_until_the_new_one_commits` | A second connection reading mid-reconcile sees the old Chunks. |
| `test_an_older_observation_is_refused_and_changes_nothing` | The typed failure, `InvalidDocument`. |
| `test_a_document_id_never_changes_across_versions` | Identity is stable. |
| `test_a_new_source_version_alone_updates_only_the_row` | `SOURCE_VERSION`. |
| `test_a_tombstoned_document_that_returns_gets_its_chunks_back` | `RESTORED`. |
| `test_losing_a_race_to_create_writes_under_the_stored_id` | The insert conflict path. |
| `test_an_older_observation_cannot_overwrite_a_newer_concurrent_one` | Review break 2, with real overlap. |
| `test_concurrent_content_and_access_changes_never_publish_a_mix` | Review break 1. |
| `test_two_runs_creating_one_document_leave_one_valid_row` | Review break 3. |

**How the concurrency tests work.** `pause_first_call` replaces a write function so its first call waits on an `asyncio.Event`. The first run pauses there, inside its transaction, holding its lock. `race` then starts the second run, waits 0.2 seconds so it reaches the lock, and releases the first. Without the lock, the second run would decide from the old row before the first commits; with it, the second run waits and decides from the committed result.

**How the failure injection works.** `monkeypatch.setattr(reconciliation, "replace_access_grants", ...)` swaps the function the workflow calls for one that raises, after the new row and Chunks were written in the same transaction. The transaction rolls back, and the test proves the stored state equals what it was before.

**Action:** create `tests/test_reconciliation.py`:

```python
"""Tests for turning a normalized document into stored Chunks."""

from datetime import UTC, datetime

from knowledge_service.documents import Document
from knowledge_service.fingerprints import content_fingerprint
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.normalization import NormalizedDocument, normalize
from knowledge_service.reconciliation import build_chunks
from knowledge_service.sources import SourceAccess, SourceRecord

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
OBSERVED_AT = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
HANDBOOK = (
    "# Team handbook\n\nWelcome to the team.\n\n"
    "## Vacation\n\nRequest vacation two weeks ahead.\n"
)


def normalized_handbook() -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id="guides/handbook.md",
            version="v1",
            media_type="text/markdown",
            content=HANDBOOK.encode(),
            access=SourceAccess(public=True),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


def test_chunks_carry_the_document_version_fingerprint_and_anchors() -> None:
    normalized = normalized_handbook()
    document = Document.register(
        document_id=DocumentId.new(),
        provenance=normalized.provenance,
        content_fingerprint=content_fingerprint(normalized.parsed),
        authorization_fingerprint="access",
        observed_at=OBSERVED_AT,
    )

    chunks = build_chunks(normalized, document)

    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert chunks[0].heading_path == "Team handbook"
    for chunk in chunks:
        assert chunk.document_id == document.document_id
        assert chunk.content_version == document.content_version
        assert chunk.content_fingerprint == document.content_fingerprint
        assert (chunk.source_anchor or "").startswith("guides/handbook.md#offset=")
```

Create `tests/test_reconciliation_integration.py`:

```python
"""Real PostgreSQL tests for reconciling a Document transactionally."""

import asyncio
from collections.abc import AsyncIterator, Coroutine, Sequence
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import text

from knowledge_service import reconciliation
from knowledge_service.access import AccessGrant, Subject
from knowledge_service.chunks import Chunk
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import (
    Document,
    DocumentAvailability,
    InvalidDocument,
)
from knowledge_service.identifiers import DocumentId, GroupId, SourceId, UserId
from knowledge_service.normalization import NormalizedDocument, normalize
from knowledge_service.persistence import (
    load_access_grants,
    load_chunks,
    load_document,
    replace_chunks,
    upsert_document,
)
from knowledge_service.reconciliation import (
    Change,
    Reconciliation,
    reconcile_document,
)
from knowledge_service.settings import Settings
from knowledge_service.sources import SourceAccess, SourceRecord

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
EXTERNAL_ID = "guides/handbook.md"
OBSERVED_AT = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)
ALICE = Subject.user(UserId.parse("00000000-0000-0000-0000-000000000031"))
FINANCE = Subject.group(GroupId.parse("00000000-0000-0000-0000-000000000041"))
FIRST = "# Team handbook\n\nWelcome to the team.\n\n## Vacation\n\nAsk early.\n"
SECOND = (
    "# Team handbook\n\nWelcome to the team.\n\n## Vacation\n\nAsk a month ahead.\n"
)

INSERT_SOURCE = text(
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


def handbook(markdown: str, version: str) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id=EXTERNAL_ID,
            version=version,
            media_type="text/markdown",
            content=markdown.encode(),
            access=SourceAccess(public=True),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def stored_state(
    runtime: DatabaseRuntime,
) -> tuple[Document, tuple[Chunk, ...], tuple[AccessGrant, ...]]:
    async with runtime.transaction() as connection:
        document = await load_document(
            connection, source_id=SOURCE_ID, external_id=EXTERNAL_ID
        )
        assert document is not None
        chunks = await load_chunks(connection, document_id=document.document_id)
        grants = await load_access_grants(connection, document_id=document.document_id)
    return document, chunks, grants


@pytest.mark.integration
async def test_a_new_document_is_published_with_chunks_and_grants(
    runtime: DatabaseRuntime,
) -> None:
    result = await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )

    document, chunks, grants = await stored_state(runtime)
    assert result.change is Change.CREATED
    assert (document.content_version.number, document.authorization_version.number) == (
        1,
        1,
    )
    assert [chunk.heading_path for chunk in chunks] == ["Team handbook"]
    assert all(
        (chunk.source_anchor or "").startswith(f"{EXTERNAL_ID}#offset=")
        for chunk in chunks
    )
    assert [grant.subject for grant in grants] == [ALICE]


@pytest.mark.integration
async def test_an_unchanged_document_writes_nothing(runtime: DatabaseRuntime) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    before = await stored_state(runtime)

    result = await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=LATER
    )

    assert result.change is Change.UNCHANGED
    assert await stored_state(runtime) == before


@pytest.mark.integration
async def test_a_content_change_replaces_chunks_and_keeps_grants(
    runtime: DatabaseRuntime,
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    _, _, grants_before = await stored_state(runtime)

    result = await reconcile_document(
        runtime, handbook(SECOND, "v2"), readers=[ALICE], observed_at=LATER
    )

    document, chunks, grants = await stored_state(runtime)
    assert result.change is Change.CONTENT
    assert (document.content_version.number, document.authorization_version.number) == (
        2,
        1,
    )
    assert "Ask a month ahead." in chunks[0].text
    assert {chunk.content_version.number for chunk in chunks} == {2}
    assert grants == grants_before


@pytest.mark.integration
async def test_an_access_change_keeps_chunks_without_rechunking(
    runtime: DatabaseRuntime,
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    _, chunks_before, _ = await stored_state(runtime)

    result = await reconcile_document(
        runtime,
        handbook(FIRST, "v1"),
        readers=[ALICE, FINANCE],
        observed_at=LATER,
    )

    document, chunks, grants = await stored_state(runtime)
    assert result.change is Change.ACCESS
    assert (document.content_version.number, document.authorization_version.number) == (
        1,
        2,
    )
    assert chunks == chunks_before  # same chunk IDs: nothing was re-chunked
    assert {grant.subject for grant in grants} == {ALICE, FINANCE}


@pytest.mark.integration
async def test_a_failure_mid_publish_leaves_the_old_version_searchable(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    before = await stored_state(runtime)

    async def fail_after_chunks_were_written(*_: object, **__: object) -> None:
        # By now the new Document row and Chunks are written, uncommitted.
        raise RuntimeError("injected failure while replacing grants")

    monkeypatch.setattr(
        reconciliation, "replace_access_grants", fail_after_chunks_were_written
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        await reconcile_document(
            runtime,
            handbook(SECOND, "v2"),
            readers=[ALICE, FINANCE],
            observed_at=LATER,
        )

    assert await stored_state(runtime) == before


@pytest.mark.integration
async def test_readers_see_the_old_version_until_the_new_one_commits(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    _, old_chunks, _ = await stored_state(runtime)
    seen_mid_publish: list[Sequence[Chunk]] = []
    original = reconciliation.replace_access_grants

    async def look_from_another_connection(
        *args: object, **kwargs: object
    ) -> tuple[AccessGrant, ...]:
        _, chunks, _ = await stored_state(runtime)
        seen_mid_publish.append(chunks)
        return await original(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(
        reconciliation, "replace_access_grants", look_from_another_connection
    )
    await reconcile_document(
        runtime,
        handbook(SECOND, "v2"),
        readers=[ALICE, FINANCE],
        observed_at=LATER,
    )

    _, new_chunks, _ = await stored_state(runtime)
    assert seen_mid_publish == [old_chunks]
    assert new_chunks != old_chunks
    assert {chunk.content_version.number for chunk in new_chunks} == {2}


@pytest.mark.integration
async def test_an_older_observation_is_refused_and_changes_nothing(
    runtime: DatabaseRuntime,
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=LATER
    )
    before = await stored_state(runtime)

    with pytest.raises(InvalidDocument, match="observed_at"):
        await reconcile_document(
            runtime, handbook(SECOND, "v2"), readers=[ALICE], observed_at=OBSERVED_AT
        )

    assert await stored_state(runtime) == before


@pytest.mark.integration
async def test_a_document_id_never_changes_across_versions(
    runtime: DatabaseRuntime,
) -> None:
    first = await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    second = await reconcile_document(
        runtime, handbook(SECOND, "v2"), readers=[FINANCE], observed_at=LATER
    )

    document, _, _ = await stored_state(runtime)
    assert isinstance(document.document_id, DocumentId)
    assert first.document.document_id == second.document.document_id
    assert second.change is Change.CONTENT_AND_ACCESS
    assert document.document_id == first.document.document_id


@pytest.mark.integration
async def test_a_new_source_version_alone_updates_only_the_row(
    runtime: DatabaseRuntime,
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    _, chunks_before, grants_before = await stored_state(runtime)

    result = await reconcile_document(
        runtime, handbook(FIRST, "v2"), readers=[ALICE], observed_at=LATER
    )

    document, chunks, grants = await stored_state(runtime)
    assert result.change is Change.SOURCE_VERSION
    assert document.provenance.source_version == "v2"
    assert (chunks, grants) == (chunks_before, grants_before)


@pytest.mark.integration
async def test_a_tombstoned_document_that_returns_gets_its_chunks_back(
    runtime: DatabaseRuntime,
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    document, _, _ = await stored_state(runtime)
    gone = document.tombstone(observed_at=LATER)
    async with runtime.transaction() as connection:  # what M03-T13 will do
        await upsert_document(connection, gone)
        await replace_chunks(connection, document=gone, chunk_run=[])

    result = await reconcile_document(
        runtime,
        handbook(FIRST, "v1"),
        readers=[ALICE],
        observed_at=LATER + timedelta(hours=1),
    )

    restored, chunks, _ = await stored_state(runtime)
    assert result.change is Change.RESTORED
    assert restored.availability is DocumentAvailability.AVAILABLE
    assert [chunk.heading_path for chunk in chunks] == ["Team handbook"]


@pytest.mark.integration
async def test_losing_a_race_to_create_writes_under_the_stored_id(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )

    original = reconciliation.load_document
    lookups = 0

    async def not_found_the_first_time(*args: object, **kwargs: object) -> object:
        # Another worker created the row after this one first looked it up.
        nonlocal lookups
        lookups += 1
        if lookups == 1:
            return None
        return await original(*args, **kwargs)  # pyright: ignore[reportArgumentType]

    monkeypatch.setattr(reconciliation, "load_document", not_found_the_first_time)
    second = await reconcile_document(
        runtime, handbook(SECOND, "v2"), readers=[FINANCE], observed_at=LATER
    )

    document, chunks, grants = await stored_state(runtime)
    assert second.document.document_id == first.document.document_id
    assert document.document_id == first.document.document_id
    assert "Ask a month ahead." in chunks[0].text
    assert [grant.subject for grant in grants] == [FINANCE]


THIRD = "# Team handbook\n\nWelcome to the team.\n\n## Vacation\n\nAsk your lead.\n"


def pause_first_call(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> tuple[asyncio.Event, asyncio.Event]:
    """Make the first call to `reconciliation.<name>` wait until released.

    The run that makes that call is then paused inside its transaction,
    holding its locks, so a second run can be started against it.
    """
    original = getattr(reconciliation, name)
    paused, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def pausing(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            paused.set()
            await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(reconciliation, name, pausing)
    return paused, release


async def race(
    paused: asyncio.Event,
    release: asyncio.Event,
    first: Coroutine[object, object, Reconciliation],
    second: Coroutine[object, object, Reconciliation],
) -> tuple[Reconciliation | BaseException, Reconciliation | BaseException]:
    """Start `first`, then `second` while `first` is paused; return both results."""
    first_task = asyncio.create_task(first)
    await paused.wait()
    second_task = asyncio.create_task(second)
    await asyncio.sleep(0.2)  # long enough for the second run to reach the lock
    release.set()
    return await asyncio.gather(first_task, second_task, return_exceptions=True)


@pytest.mark.integration
async def test_an_older_observation_cannot_overwrite_a_newer_concurrent_one(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    paused, release = pause_first_call(monkeypatch, "replace_chunks")

    newer, older = await race(
        paused,
        release,
        reconcile_document(
            runtime,
            handbook(SECOND, "v3"),
            readers=[ALICE],
            observed_at=LATER + timedelta(hours=1),
        ),
        reconcile_document(
            runtime, handbook(THIRD, "v2"), readers=[ALICE], observed_at=LATER
        ),
    )

    document, chunks, _ = await stored_state(runtime)
    assert not isinstance(newer, BaseException)
    assert isinstance(older, InvalidDocument)
    assert document.provenance.source_version == "v3"
    assert "Ask a month ahead." in chunks[0].text


@pytest.mark.integration
async def test_concurrent_content_and_access_changes_never_publish_a_mix(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    await reconcile_document(
        runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
    )
    paused, release = pause_first_call(monkeypatch, "replace_chunks")

    content_run, access_run = await race(
        paused,
        release,
        reconcile_document(
            runtime, handbook(SECOND, "v2"), readers=[ALICE], observed_at=LATER
        ),
        reconcile_document(
            runtime,
            handbook(FIRST, "v1"),
            readers=[ALICE, FINANCE],
            observed_at=LATER + timedelta(minutes=1),
        ),
    )

    document, chunks, grants = await stored_state(runtime)
    assert not isinstance(content_run, BaseException)
    assert not isinstance(access_run, BaseException)
    assert {chunk.content_version for chunk in chunks} == {document.content_version}
    assert {chunk.content_fingerprint for chunk in chunks} == {
        document.content_fingerprint
    }
    assert {grant.subject for grant in grants} == {ALICE, FINANCE}


@pytest.mark.integration
async def test_two_runs_creating_one_document_leave_one_valid_row(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    paused, release = pause_first_call(monkeypatch, "replace_chunks")

    newer, older = await race(
        paused,
        release,
        reconcile_document(
            runtime, handbook(SECOND, "v2"), readers=[ALICE], observed_at=LATER
        ),
        reconcile_document(
            runtime, handbook(FIRST, "v1"), readers=[ALICE], observed_at=OBSERVED_AT
        ),
    )

    document, chunks, _ = await stored_state(runtime)  # the row is still readable
    assert not isinstance(newer, BaseException)
    assert isinstance(older, InvalidDocument)
    assert document.created_at <= document.updated_at
    assert "Ask a month ahead." in chunks[0].text
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_reconciliation.py tests/test_reconciliation_integration.py -v
```

With `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set, all 15 tests pass (1 unit test and 14 integration tests). Without it, the 14 integration tests are skipped.

These tests were checked against broken versions: writing the Chunks in a separate transaction fails exactly the two atomicity tests; the version before the review fixes fails all three concurrency tests; removing only the row lock fails the two lock-dependent ones.

### Step 5 — No coding in this step: run the gates, then record evidence

**Action:** with `KNOWLEDGE_SERVICE_TEST_DATABASE_URL` set, run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_reconciliation.py tests/test_reconciliation_integration.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make test-integration
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. In Design, also record these notes:

- **The order key is `observed_at`, not the source version.** Two observations with the same `observed_at` are both accepted, and a replay of an older source version observed later is published. M03-T13 and later jobs should pass a trustworthy observation time.
- **Chunking runs inside the transaction.** It is local and fast today; embedding calls, when they come, must happen before the transaction.
- **The "document vanished" error is defensive.** Documents are never deleted, so it cannot happen today, and it is the one line the tests do not reach.
- **The concurrency tests rely on a 0.2-second pause** to let the second run reach the lock. They passed repeatedly, but a very slow machine could make them pass without testing the overlap.

Answer these reflection questions in your own words:

1. Why must the Document row, its Chunks, and its grants be written in one transaction? Describe what a reader could see if the Chunks were committed first and the grant write then failed.
2. Why does an access-only change keep the same Chunks, and what would re-chunking cost once embeddings exist?
3. Two workers reconcile the same document at the same moment. Without the row lock, how could an older observation overwrite a newer one, and how does `SELECT ... FOR UPDATE` prevent it?
4. Which test would let a real bug reach production if you deleted it? Name it and describe the bug.

**Check:** every command passes, and the evidence explains the design in your words.

## Completion checklist

- [ ] `persistence.py` has `insert_document` and `load_document(..., for_update=...)`, and existing callers are unchanged.
- [ ] `reconciliation.py` has `reconcile_document`, `build_chunks`, `Reconciliation`, and `Change`.
- [ ] Unchanged observations write nothing; content-only changes keep grants; access-only changes keep the same Chunks; a new source version updates only the row.
- [ ] A failure after the new Chunks are written leaves the previous version exactly, and readers see the old version until the new one commits.
- [ ] An older observation raises `InvalidDocument` and changes nothing, also when it overlaps a newer one.
- [ ] Two runs creating one document leave one valid row.
- [ ] All 15 reconciliation tests, the integration suite, the full deterministic suite, format, lint, types, `make check`, and `make docs-check` pass.
- [ ] The evidence file holds your invariant, predictions, design (including the four notes), verification, and your own reflection.

When you're done, share your code or any error you hit, and I'll review it.
