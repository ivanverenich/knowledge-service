# M07-T10 — Prioritize permission revocation

Task: [M07-T10 — Prioritize permission revocation](../curriculum/milestones/M07-identity-and-authorization/M07-T10-prioritize-permission-revocation.md)

## What you will do

This lesson makes taking access away the first thing a Synchronization Run does, and proves that a request already in progress cannot outrun it. Until now, a run handled each file in order: parse, chunk, then write its content and its access together. A file whose readers shrank waited behind every slow file before it on the page, and stayed readable to the removed readers all that time.

- **Revoke first:** each page now has two phases. First, one query reads who may read each of the page's Documents now, and every file that loses a reader is narrowed at once, in its own short transaction, with a new Authorization Version. Only then does any file on the page get parsed, chunked, and reconciled.
- **Only where needed:** a file that removes nobody is not touched in the first phase, so an unchanged pass costs one extra read per page, not a lock per file.
- **One record per file per page:** a file offered twice on a page counts once, as last offered, so a removed reader never comes back for a moment.
- **Fail closed in flight:** a test asks a question while the run is still parsing an earlier file, and another revokes access in the middle of a Conversation; every read after the narrowing, at every consistency point, gives nothing.

| The task asks for | Where it happens |
|---|---|
| Publish restrictive Authorization Versions before non-security work | Steps 2 to 4: `load_readers`, `removes_readers`, and the two-phase page in `synchronize_source` |
| Invalidate in-flight access at defined consistency points | Already in place: search, the Evidence check before the model, the Citation check before the decision, and History; Step 5 proves them against a running revocation |
| A concurrency test changes grants during a Conversation and proves later reads fail closed | Step 5: `tests/test_revocation_integration.py` |
| No seeded sensitive text in telemetry | The run logs only counts; the review checked it at DEBUG |
| Update only what the change made inaccurate | Step 7: `docs/architecture.md`, `docs/threat-model.md` |

This lesson includes coding: Steps 2 to 5 write code and tests. Steps 1, 6, and 7 do not, though Step 7 edits two documents.

Terms used in this lesson:

- **Authorization Version:** a counter on each Document that goes up whenever who may read it changes, separately from its content version.
- **Restrictive change:** a change that removes at least one reader. Adding readers is not restrictive and can wait.
- **Narrowing:** applying only the restrictive part of a change: keep the readers both the old and the new access name. Like taking a name off a guest list before re-printing the menu.
- **Consistency point:** a moment where a request asks the database again whether the reader may see something: the search itself, the Evidence check before the model, the Citation check before the decision, and History.
- **Fail closed:** when access is in doubt, show nothing.
- **Concurrency test:** a test where two things happen at once, here a run and a request, and the result must be right whichever finishes first.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **Why revocation is special.** A late new reader costs a delay; a late removed reader is a leak. So removals jump the queue, and additions wait for the file's normal reconciliation, together with its new content.
- **Reuse `narrow_access`.** M07-T06 wrote it for files that fail to parse: keep only readers both old and new access name, record that in the authorization fingerprint, raise the Authorization Version. The same function, called for every losing file before any parsing, is exactly "publish the restrictive version first".
- **Per page, not per pass.** A run reads the Source page by page; buffering every page first would double memory and Source calls. So a revocation on page 2 still waits for page 1's work. The threat model says so.
- **Read once, lock only when needed.** The review measured the first version, which narrowed every file, at 2 to 8 times slower on an unchanged pass, because each file took a row lock. Now `load_readers` reads every Document's readers for the page in one query, and `removes_readers` picks only the files that lose someone.
- **The consistency points were already there.** Search filters by grants, M07-T09 rereads Evidence before the model and Citations before the decision, and History rereads every cited Chunk. This lesson does not add a new one; it proves that once a narrowing commits, all of them give nothing.
- **What the review found.** An independent adversarial review tried page orders, explicit deletions, exceptions in either phase, duplicate records, public and empty readers, unresolved principals, 300-file passes, markers in text and principals at DEBUG, and 13 mutations. Nothing widened access or leaked. It found these gaps, all fixed with tests that failed before:
  - The mid-Conversation test passed without this lesson's change, because a single file was already revoked before the model replied. Now a second test asks while the run is still working on an earlier file of the page; it fails on the old code. A control assertion makes sure the question is answered before the revocation, so the test cannot pass for the wrong reason.
  - The change counts after a narrowing were never checked. Now three cases are: a reader removed, a reader removed with new text, and readers swapped with new text.
  - Skipping reconciliation for a narrowed file, and skipping the narrowing for a broken file with only unknown readers, both passed. Now carol must gain `b.md` after the run, and a broken file naming only `group:contractors` must be narrowed to nobody.
  - Narrowing every file cost a lock each; now only files that lose a reader are locked, and a test proves an unchanged pass locks none.
  - A file offered twice on a page could give a removed reader access back until its second reconciliation. Now each file counts once per page, as last offered, and a test checks the removed reader never sees it.
- **Known limits.** A Document the Source deletes, or no longer lists, is tombstoned only at the end of the pass. Revocation is first per page, not per pass. Text streamed between a revocation and the Citation check is replaced, not unsent. Two runs of one Source are not locked against each other; a stale one fails instead of widening. A revocation applies only when a run reaches it; scheduled runs come in M08-T06.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before you change anything, write down what must be true when the lesson is done, and what you expect. Step 6 checks the last prediction.

Create `docs/evidence/M07-T10-prioritize-permission-revocation.md`:

```markdown
# M07-T10 — Prioritize permission revocation evidence

## Invariant

On every page of a Synchronization Run, each Document that loses a reader
is narrowed to the readers its old and new access both name, committed with
a new Authorization Version, before any record of the page is parsed,
chunked, or reconciled; readers the Source adds wait for the record's own
reconciliation. Once a narrowing commits, every consistency point of a
request, search, the Evidence check before the model, the Citation check
before the decision, and History, gives the removed reader nothing. A file
offered twice on a page counts once, as last offered, and a pass that
removes no reader locks no Document.

## Expected failing check

Before the change, `uv run pytest -m integration tests/test_revocation_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_revocation_integration.py`.

## Predictions

- On a page with `notes.md` (new text, engineering) first and `roadmap.md` (engineering removed, carol added) second, what alice's question about the roadmap gets while the run is still working on `notes.md`, before and after this change:
- How many Documents a pass that removes no reader locks, and how many queries it adds per page:
- What a page offering `b.md` for carol and then for engineering does to carol, and what the summary counts:
- Which tests fail if `synchronize_source` stops calling `_revoke_first`:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: who may read the page's Documents now

**In plain words:** this step adds one database question: for a list of files from one Source, who may read each of them right now? It asks it in a single query for a whole page, so the run can tell which files are about to lose a reader without locking any of them.

| Part | What it does |
|---|---|
| `load_readers` | For the available Documents of these files: their current readers, as `Subject`s, in one query; a file with no stored Document is absent |
| `_subject_from` | Turns a stored grant row into a `Subject`, refusing an unknown kind |

In `src/knowledge_service/persistence/access.py`:

**1.** Find:

```python
    Subject,
    SubjectKind,
    checked_reader,
    granted_subjects,
)
from knowledge_service.documents import DocumentAvailability
from knowledge_service.identifiers import AccessGrantId, DocumentId, GroupId, UserId
from knowledge_service.persistence.schema import access_grants, chunks, documents


def subject_grants_statement(subject_kind: str, subject_id: UUID) -> sa.Select[Any]:
    """Select the Documents one verified subject is allowed to reach."""
    return sa.select(access_grants.c.document_id).where(
```

Replace with:

```python
    Subject,
    SubjectKind,
    checked_reader,
    granted_subjects,
)
from knowledge_service.documents import DocumentAvailability
from knowledge_service.identifiers import (
    AccessGrantId,
    DocumentId,
    GroupId,
    SourceId,
    UserId,
)
from knowledge_service.persistence.schema import access_grants, chunks, documents


def subject_grants_statement(subject_kind: str, subject_id: UUID) -> sa.Select[Any]:
    """Select the Documents one verified subject is allowed to reach."""
    return sa.select(access_grants.c.document_id).where(
```

**2.** Find:

```python
        sa.select(access_grants)
        .where(access_grants.c.document_id == document_id.value)
        .order_by(access_grants.c.subject_kind, access_grants.c.subject_id)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_grant_from(dict(record)) for record in records)
```

Replace with:

```python
        sa.select(access_grants)
        .where(access_grants.c.document_id == document_id.value)
        .order_by(access_grants.c.subject_kind, access_grants.c.subject_id)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_grant_from(dict(record)) for record in records)


async def load_readers(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    external_ids: Sequence[str],
) -> dict[str, frozenset[Subject]]:
    """The readers each available Document of these source items has now.

    One statement for all of them. An item with no stored Document, or a
    tombstoned one, is absent; an available one with no grants maps to an
    empty set.
    """
    if not external_ids:
        return {}
    statement = (
        sa.select(
            documents.c.external_id,
            access_grants.c.subject_kind,
            access_grants.c.subject_id,
        )
        .select_from(
            documents.outerjoin(
                access_grants,
                access_grants.c.document_id == documents.c.document_id,
            )
        )
        .where(
            documents.c.source_id == source_id.value,
            documents.c.external_id.in_(sorted(set(external_ids))),
            documents.c.availability == DocumentAvailability.AVAILABLE.value,
        )
    )
    readers: dict[str, set[Subject]] = {}
    for record in (await connection.execute(statement)).mappings():
        found = readers.setdefault(cast(str, record["external_id"]), set())
        if record["subject_kind"] is not None:
            found.add(_subject_from(dict(record)))
    return {name: frozenset(subjects) for name, subjects in readers.items()}


def _subject_from(record: Mapping[str, object]) -> Subject:
    try:
        kind = SubjectKind(cast(str, record["subject_kind"]))
    except ValueError:
        raise InvalidGrant("stored subject kind is not a known value") from None
    stored_id = cast("UUID | None", record["subject_id"])
    if kind is SubjectKind.PUBLIC:
        return Subject.public()
    if kind is SubjectKind.USER:
        return Subject.user(UserId(cast(UUID, stored_id)))
    return Subject.group(GroupId(cast(UUID, stored_id)))
```

**Check:** `uv run pyright src/knowledge_service/persistence/access.py` reports `0 errors`.

### Step 3 — Coding step: does the new access remove anyone?

**In plain words:** this step adds a yes-or-no test: given who may read a file now and who the Source names now, would anyone lose it? It reuses the rule `narrow_access` already uses, where "everyone" counts as any reader.

In `src/knowledge_service/ingestion/reconciliation.py`:

**1.** Find:

```python

When a record cannot be normalized, its last good version stays, but
`narrow_access` keeps it only for readers the Source still names: who may
read it can shrink then, never grow.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncConnection

```

Replace with:

```python

When a record cannot be normalized, its last good version stays, but
`narrow_access` keeps it only for readers the Source still names: who may
read it can shrink then, never grow.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncConnection

```

**2.** Find:

```python
        # observation compares against what is really stored.
        await upsert_document(connection, document)
        await _write_grants(connection, document, kept, observed_at)
        return True


def _both(stored: Sequence[Subject], observed: Sequence[Subject]) -> list[Subject]:
    """The readers both sets let in; everyone stands for any reader."""
    everyone = Subject.public()
    if everyone in stored:
        return list(observed)
    if everyone in observed:
```

Replace with:

```python
        # observation compares against what is really stored.
        await upsert_document(connection, document)
        await _write_grants(connection, document, kept, observed_at)
        return True


def removes_readers(stored: Iterable[Subject], observed: Sequence[Subject]) -> bool:
    """Whether the observed readers leave out anyone the stored ones let in."""
    kept = tuple(stored)
    return set(_both(kept, observed)) != set(kept)


def _both(stored: Sequence[Subject], observed: Sequence[Subject]) -> list[Subject]:
    """The readers both sets let in; everyone stands for any reader."""
    everyone = Subject.public()
    if everyone in stored:
        return list(observed)
    if everyone in observed:
```

**Check:** `uv run pyright src/knowledge_service/ingestion/reconciliation.py` reports `0 errors`.

### Step 4 — Coding step: a page in two phases

**In plain words:** this step reorders the work of a Synchronization Run. For each page, it first collects the files, keeping only the last offer of each. Then it reads who may read them now, and narrows every file that loses a reader, each in its own small commit. Only after that does it parse, chunk, and reconcile the files, as before. The summary still reports what changed for each file, counting a narrowing as an access change.

| Change | What it does |
|---|---|
| Module docstring | Explains revocation first, and what still waits |
| `planned` | A dict by external ID: a file offered twice counts once, as last offered; a later deletion removes it |
| `_revoke_first` | `load_readers` once, then `narrow_access` for each file `removes_readers` picks; returns the narrowed IDs |
| Phase 2 | Parses and reconciles every planned file; a quarantined file counts an access change if it was narrowed |
| `_counted` | Reports an access change for a narrowed file that reconciliation alone would report as unchanged or content-only |

In `src/knowledge_service/ingestion/synchronization.py`:

**1.** Find:

```python

Each Document's readers come from `normalize_access`: a principal the
Source names but the `PrincipalDirectory` does not map grants nothing, and
the summary reports it so someone can map it. A record that fails to
normalize keeps its last good version, for only the readers its old and new
access both name.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
```

Replace with:

```python

Each Document's readers come from `normalize_access`: a principal the
Source names but the `PrincipalDirectory` does not map grants nothing, and
the summary reports it so someone can map it. A record that fails to
normalize keeps its last good version, for only the readers its old and new
access both name.

Revocation goes first. Each page is handled in two phases. First, one
query reads the readers every Document of the page has now, and each record
whose new access leaves someone out is narrowed to the readers its old and
new access both name (`narrow_access`); each narrowing commits on its own,
with a new Authorization Version. Only then is any record of the page
parsed, chunked, and reconciled. So a reader the Source removed from a
Document loses it before the slow work of its page, never after it. Readers
the Source adds wait for the record's own reconciliation, with its new
content. A record offered twice in a page counts once, as last offered.

Two kinds of removal still wait: a revocation on a later page waits for the
earlier pages' work, and a Document the Source deletes, or no longer lists,
becomes unavailable only when the pass tombstones it at the end.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
```

**2.** Find:

```python
from knowledge_service.ingestion.deletions import (
    Tombstone,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.grant_normalization import (
    PrincipalDirectory,
    UnresolvedPrincipal,
    normalize_access,
)
from knowledge_service.ingestion.normalization import Quarantined, normalize
from knowledge_service.ingestion.reconciliation import (
    Change,
    narrow_access,
    reconcile_document,
)
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourcePort,
    SourceRecordState,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


```

Replace with:

```python
from knowledge_service.ingestion.deletions import (
    Tombstone,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.grant_normalization import (
    NormalizedAccess,
    PrincipalDirectory,
    UnresolvedPrincipal,
    normalize_access,
)
from knowledge_service.ingestion.normalization import Quarantined, normalize
from knowledge_service.ingestion.reconciliation import (
    Change,
    narrow_access,
    reconcile_document,
    removes_readers,
)
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourcePort,
    SourceRecord,
    SourceRecordState,
)
from knowledge_service.persistence.access import load_readers

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


```

**3.** Find:

```python
    quarantined: list[Quarantined] = []
    # By external ID, so a record offered twice is reported as last seen.
    unresolved: dict[str, UnresolvedAccess] = {}
    checkpoint: str | None = None
    while True:
        page = await source.fetch_page(checkpoint=checkpoint, page_size=page_size)
        for record in page.records:
            # A record the Source reports deleted is not seen, even if an
            # earlier page offered it: the last report wins.
            unresolved.pop(record.external_id, None)
            if record.state is SourceRecordState.DELETED or record.access is None:
                seen.discard(record.external_id)
                continue
            seen.add(record.external_id)
            access = normalize_access(record.access, principal_directory)
            if access.unresolved:
                unresolved[record.external_id] = UnresolvedAccess(
                    record.external_id, access.unresolved
                )
            outcome = normalize(record, source_id=source_id)
            if isinstance(outcome, Quarantined):
                # Kept as seen: a file that fails to parse is not a deleted
                # one, but whoever the Source no longer names loses it now.
                quarantined.append(outcome)
                if await narrow_access(
                    runtime,
                    source_id=source_id,
                    external_id=record.external_id,
                    readers=access.readers,
                    observed_at=observed_at,
                ):
                    changes[Change.ACCESS] += 1
                continue
            result = await reconcile_document(
                runtime,
                outcome,
                readers=access.readers,
                observed_at=observed_at,
            )
            changes[result.change] += 1
        if page.next_checkpoint is None:
            break
        if page.next_checkpoint in used:
            raise InvalidSource("the source repeated a checkpoint")
        used.add(page.next_checkpoint)
        checkpoint = page.next_checkpoint
```

Replace with:

```python
    quarantined: list[Quarantined] = []
    # By external ID, so a record offered twice is reported as last seen.
    unresolved: dict[str, UnresolvedAccess] = {}
    checkpoint: str | None = None
    while True:
        page = await source.fetch_page(checkpoint=checkpoint, page_size=page_size)
        # By external ID, so a record offered twice in a page counts once.
        planned: dict[str, tuple[SourceRecord, NormalizedAccess]] = {}
        for record in page.records:
            # A record the Source reports deleted is not seen, even if an
            # earlier page offered it: the last report wins.
            unresolved.pop(record.external_id, None)
            planned.pop(record.external_id, None)
            if record.state is SourceRecordState.DELETED or record.access is None:
                seen.discard(record.external_id)
                continue
            seen.add(record.external_id)
            access = normalize_access(record.access, principal_directory)
            if access.unresolved:
                unresolved[record.external_id] = UnresolvedAccess(
                    record.external_id, access.unresolved
                )
            planned[record.external_id] = (record, access)
        # Security first: every reader this page removes loses the Document
        # before any record of the page is parsed or chunked.
        narrowed = await _revoke_first(
            runtime,
            list(planned.values()),
            source_id=source_id,
            observed_at=observed_at,
        )
        for record, access in planned.values():
            outcome = normalize(record, source_id=source_id)
            if isinstance(outcome, Quarantined):
                # Kept as seen: a file that fails to parse is not a deleted
                # one; its narrowing above already applied.
                quarantined.append(outcome)
                if record.external_id in narrowed:
                    changes[Change.ACCESS] += 1
                continue
            result = await reconcile_document(
                runtime,
                outcome,
                readers=access.readers,
                observed_at=observed_at,
            )
            changes[
                _counted(result.change, narrowed=record.external_id in narrowed)
            ] += 1
        if page.next_checkpoint is None:
            break
        if page.next_checkpoint in used:
            raise InvalidSource("the source repeated a checkpoint")
        used.add(page.next_checkpoint)
        checkpoint = page.next_checkpoint
```

**4.** Find:

```python
        changes=changes,
        quarantined=tuple(quarantined),
        tombstones=tombstones,
        requeued_purges=requeued,
        unresolved=tuple(unresolved.values()),
    )
```

Replace with:

```python
        changes=changes,
        quarantined=tuple(quarantined),
        tombstones=tombstones,
        requeued_purges=requeued,
        unresolved=tuple(unresolved.values()),
    )


async def _revoke_first(
    runtime: DatabaseRuntime,
    planned: list[tuple[SourceRecord, NormalizedAccess]],
    *,
    source_id: SourceId,
    observed_at: datetime,
) -> set[str]:
    """Narrow every record whose new access leaves a reader out.

    One read finds the readers each Document has now, so a record that
    removes nobody costs no locking transaction. Each narrowing is its own
    short transaction, committed before the next, so a revocation is
    visible to readers as soon as it is applied. Returns the narrowed IDs.
    """
    async with runtime.transaction() as connection:
        stored = await load_readers(
            connection,
            source_id=source_id,
            external_ids=[record.external_id for record, _ in planned],
        )
    narrowed: set[str] = set()
    for record, access in planned:
        current = stored.get(record.external_id)
        if current is None or not removes_readers(current, access.readers):
            continue
        if await narrow_access(
            runtime,
            source_id=source_id,
            external_id=record.external_id,
            readers=access.readers,
            observed_at=observed_at,
        ):
            narrowed.add(record.external_id)
    return narrowed


def _counted(change: Change, *, narrowed: bool) -> Change:
    """The change to report for a record whose access was narrowed first.

    The narrowing already wrote the access change, so reconciliation may see
    none; the record still changed who may read it.
    """
    if not narrowed or change in (Change.ACCESS, Change.CONTENT_AND_ACCESS):
        return change
    if change is Change.CONTENT:
        return Change.CONTENT_AND_ACCESS
    return Change.ACCESS
```

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_synchronization_integration.py tests/test_document_title_integration.py -q
```

Expected: `18 passed`, every existing synchronization test unchanged.

### Step 5 — Coding step: proving the order and the fail-closed reads

**In plain words:** these tests watch a run from the inside. One records the order of events and shows every narrowing happens before the first file is parsed, and that alice has already lost a file when the slow work starts. Others check the counts, broken files, duplicate offers, and that an unchanged pass locks nothing. The last file runs a real question while a run is happening, and checks that every later read gives alice nothing.

| File | Tests |
|---|---|
| `tests/test_synchronization_integration.py` | Revocation before any parse, with carol gaining the file afterwards; three change-count cases; a broken file naming only unknown readers; a file offered twice; a pass that locks nothing; a file offered then deleted on one page |
| `tests/test_revocation_integration.py` | A grant revoked while the model writes, then History, search, and a new question all fail closed; the control case where access is kept; a question asked while the run is still parsing an earlier file |

In `tests/test_synchronization_integration.py`:

**1.** Find:

```python
"""Real PostgreSQL tests for one complete pass over a local Source."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy import text

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import SourceId
from knowledge_service.identity.context import group_id_for, user_id_for
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.grant_normalization import (
    PrincipalDirectory,
    UnresolvedPrincipal,
    UnresolvedReason,
)
```

Replace with:

```python
"""Real PostgreSQL tests for one complete pass over a local Source."""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy import text

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import SourceId
from knowledge_service.identity.context import group_id_for, user_id_for
from knowledge_service.ingestion import synchronization
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.grant_normalization import (
    PrincipalDirectory,
    UnresolvedPrincipal,
    UnresolvedReason,
)
```

**2.** Find:

```python
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )

    assert summary.unresolved == ()
```

Replace with:

```python
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )

    assert summary.unresolved == ()


class OnePage:
    """A Source that offers the given records on one page."""

    def __init__(self, records: Sequence[SourceRecord]) -> None:
        self.records = tuple(records)

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        return SourcePage(records=self.records, next_checkpoint=None)


def a_record(name: str, text: str, *principals: str) -> SourceRecord:
    return SourceRecord.present(
        external_id=name,
        version=text,
        media_type="text/markdown",
        content=text.encode(),
        access=SourceAccess(public=False, principals=principals),
        metadata=SourceMetadata(title=name, modified_at=OBSERVED_AT, size_bytes=1),
    )


async def readable_names(
    runtime: DatabaseRuntime, reader: AuthorizationContext
) -> set[str]:
    """The Documents whose Chunks retrieval's own check lets `reader` see."""
    statement = (
        sa.select(documents.c.external_id)
        .select_from(
            chunks.join(documents, chunks.c.document_id == documents.c.document_id)
        )
        .where(eligible_chunks_predicate(reader))
    )
    async with runtime.transaction() as connection:
        return set((await connection.execute(statement)).scalars())


@pytest.mark.integration
async def test_a_revocation_is_published_before_any_record_of_its_page_is_parsed(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a.md comes first and changes its text; b.md comes second and drops
    # engineering. b.md's revocation must not wait for a.md's parsing.
    await synchronize_source(
        runtime,
        OnePage(
            [
                a_record("a.md", "# A\n\nFirst text.\n", "group:engineering"),
                a_record("b.md", "# B\n\nSalaries.\n", "group:engineering"),
            ]
        ),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )
    events: list[str] = []
    visible_at_first_reconcile: list[set[str]] = []
    narrow, parse, reconcile = (
        synchronization.narrow_access,
        synchronization.normalize,
        synchronization.reconcile_document,
    )

    async def narrowing(*args: Any, **kwargs: Any) -> bool:
        changed = await narrow(*args, **kwargs)
        events.append(f"narrow {kwargs['external_id']} {changed}")
        return changed

    def parsing(record: SourceRecord, *, source_id: SourceId) -> Any:
        events.append(f"parse {record.external_id}")
        return parse(record, source_id=source_id)

    async def reconciling(*args: Any, **kwargs: Any) -> Any:
        if not visible_at_first_reconcile:
            visible_at_first_reconcile.append(await readable_names(runtime, ALICE))
        return await reconcile(*args, **kwargs)

    monkeypatch.setattr(synchronization, "narrow_access", narrowing)
    monkeypatch.setattr(synchronization, "normalize", parsing)
    monkeypatch.setattr(synchronization, "reconcile_document", reconciling)

    summary = await synchronize_source(
        runtime,
        OnePage(
            [
                a_record("a.md", "# A\n\nSecond text.\n", "group:engineering"),
                a_record("b.md", "# B\n\nSalaries.\n", "user:carol@example.test"),
            ]
        ),
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=DIRECTORY,
    )

    # a.md removes nobody, so it is not even locked; b.md is narrowed first.
    assert events == ["narrow b.md True", "parse a.md", "parse b.md"]
    # When the slow work of the page starts, alice already lost b.md.
    assert visible_at_first_reconcile == [{"a.md"}]
    assert summary.changes == {Change.CONTENT: 1, Change.ACCESS: 1}
    # The reader the Source added gets it with the record's reconciliation.
    assert await readable_names(runtime, CAROL_READER) == {"b.md"}


CAROL_READER = AuthorizationContext(user_id=user_id_for(ISSUER, CAROL))
ENGINEERING = "group:engineering"
CAROL_PRINCIPAL = "user:carol@example.test"


async def one_pass(
    runtime: DatabaseRuntime, at: datetime, *records: SourceRecord
) -> SynchronizationSummary:
    return await synchronize_source(
        runtime,
        OnePage(records),
        source_id=SOURCE_ID,
        observed_at=at,
        principal_directory=DIRECTORY,
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("before", "after", "change", "alice", "carol"),
    [
        (
            ("# B\n\nSalaries.\n", ENGINEERING, CAROL_PRINCIPAL),
            ("# B\n\nSalaries.\n", CAROL_PRINCIPAL),
            Change.ACCESS,
            set[str](),
            {"b.md"},
        ),
        (
            ("# B\n\nSalaries.\n", CAROL_PRINCIPAL),
            ("# B\n\nNew salaries.\n", ENGINEERING),
            Change.CONTENT_AND_ACCESS,
            {"b.md"},
            set[str](),
        ),
        (
            ("# B\n\nSalaries.\n", ENGINEERING, CAROL_PRINCIPAL),
            ("# B\n\nNew salaries.\n", CAROL_PRINCIPAL),
            Change.CONTENT_AND_ACCESS,
            set[str](),
            {"b.md"},
        ),
    ],
    ids=["reader-removed", "reader-swapped-with-new-text", "reader-removed-new-text"],
)
async def test_a_narrowed_record_reports_the_access_change_it_made(
    runtime: DatabaseRuntime,
    before: tuple[str, ...],
    after: tuple[str, ...],
    change: Change,
    alice: set[str],
    carol: set[str],
) -> None:
    await one_pass(runtime, OBSERVED_AT, a_record("b.md", *before))

    summary = await one_pass(runtime, LATER, a_record("b.md", *after))

    assert summary.changes == {change: 1}
    assert await readable_names(runtime, ALICE) == alice
    assert await readable_names(runtime, CAROL_READER) == carol


@pytest.mark.integration
async def test_a_broken_file_naming_only_unknown_readers_is_narrowed_to_nobody(
    runtime: DatabaseRuntime,
) -> None:
    await one_pass(runtime, OBSERVED_AT, a_record("b.md", "# B\n\nOk.\n", ENGINEERING))
    broken = SourceRecord.present(
        external_id="b.md",
        version="broken",
        media_type="text/markdown",
        content=b"\xff\xfe broken",
        access=SourceAccess(public=False, principals=("group:contractors",)),
        metadata=SourceMetadata(title="b.md", modified_at=OBSERVED_AT, size_bytes=1),
    )

    summary = await one_pass(runtime, LATER, broken)

    assert summary.changes == {Change.ACCESS: 1}
    assert len(summary.quarantined) == 1
    assert await readable_names(runtime, ALICE) == set()


@pytest.mark.integration
async def test_a_record_offered_twice_in_a_page_counts_once_as_last_offered(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "# B\n\nSalaries.\n"
    await one_pass(
        runtime, OBSERVED_AT, a_record("b.md", text, ENGINEERING, CAROL_PRINCIPAL)
    )
    carol_saw: list[set[str]] = []
    reconcile = synchronization.reconcile_document

    async def reconciling(*args: Any, **kwargs: Any) -> Any:
        result = await reconcile(*args, **kwargs)
        carol_saw.append(await readable_names(runtime, CAROL_READER))
        return result

    monkeypatch.setattr(synchronization, "reconcile_document", reconciling)

    summary = await one_pass(
        runtime,
        LATER,
        a_record("b.md", text, CAROL_PRINCIPAL),
        a_record("b.md", text, ENGINEERING),
    )

    # Only the last offer counts: carol, removed by it, never regains b.md.
    assert carol_saw == [set()]
    assert summary.changes == {Change.ACCESS: 1}
    assert await readable_names(runtime, ALICE) == {"b.md"}


@pytest.mark.integration
async def test_a_pass_that_removes_no_reader_locks_no_document(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = [
        a_record(f"{name}.md", f"# {name}\n\nText.\n", ENGINEERING)
        for name in ("a", "b", "c")
    ]
    await one_pass(runtime, OBSERVED_AT, *records)
    calls: list[str] = []
    narrow = synchronization.narrow_access

    async def narrowing(*args: Any, **kwargs: Any) -> bool:
        calls.append(kwargs["external_id"])
        return await narrow(*args, **kwargs)

    monkeypatch.setattr(synchronization, "narrow_access", narrowing)
    widened = a_record("c.md", "# c\n\nText.\n", ENGINEERING, CAROL_PRINCIPAL)

    summary = await one_pass(runtime, LATER, *records[:2], widened)

    assert calls == []
    assert summary.changes == {Change.UNCHANGED: 2, Change.ACCESS: 1}


@pytest.mark.integration
async def test_a_record_offered_then_deleted_in_one_page_is_not_reconciled(
    runtime: DatabaseRuntime,
) -> None:
    text = "# B\n\nSalaries.\n"
    await one_pass(runtime, OBSERVED_AT, a_record("b.md", text, ENGINEERING))

    summary = await one_pass(
        runtime,
        LATER,
        a_record("b.md", "# B\n\nRewritten.\n", ENGINEERING),
        SourceRecord.deleted(external_id="b.md", version="gone"),
    )

    assert summary.changes == {}
    assert [tombstone.external_id for tombstone in summary.tombstones] == ["b.md"]
    assert await readable_names(runtime, ALICE) == set()
```

The tests replace module functions such as `synchronization.normalize` with recording wrappers, which is how they see the order of events. If a refactor ever bypassed those names, the expected events would be missing and the test would fail loudly, not pass silently.

Create `tests/test_revocation_integration.py`:

```python
"""A grant revoked in the middle of a Conversation: every later read fails closed.

alice asked about the roadmap once and got an Answer citing it. She asks
again, and while the model is writing, a Synchronization Run takes the
roadmap away from engineering. From then on, at every consistency point,
she must get nothing from it: the current Answer is withdrawn, her History
withholds the earlier Answer, search finds nothing, and a new Question
abstains without the model.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.abstention import AbstentionReason, EvidenceSignals
from knowledge_service.answering.current_citations import CurrentCitations
from knowledge_service.answering.events import (
    AnswerEventReader,
    StreamedAnswer,
    StreamOutcome,
)
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.history import load_history
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.app import create_app
from knowledge_service.conversations import (
    ConversationLimits,
    MessageOutcome,
    MessageReference,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, ConversationId, DocumentId
from knowledge_service.identity.context import Caller
from knowledge_service.ingestion import synchronization
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.sources import SourceAccess
from knowledge_service.ingestion.synchronization import synchronize_source
from knowledge_service.persistence.conversations import add_turn, start_conversation
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.retrieval.lexical_search import search_lexical_chunks
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings
from test_citation_confirmation_integration import CitingModel
from test_streaming import QUESTION, Model, streamer
from test_synchronization_integration import (
    ALICE,
    DIRECTORY,
    INSERT_SOURCE,
    LATER,
    OBSERVED_AT,
    SOURCE_ID,
    OnePage,
    a_record,
)

ENGINEERING = SourceAccess(public=False, principals=("group:engineering",))
CAROL_ONLY = SourceAccess(public=False, principals=("user:carol@example.test",))
LIMITS = ConversationLimits()
SEARCH_WORDS = "roadmap policy"


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


class AsAlice:
    """An authenticator that makes every request alice's, a `user`."""

    async def authenticate(self, headers: list[str]) -> Caller:
        return Caller(reader=ALICE, roles=frozenset({ApplicationRole.USER}))


class WordSearch:
    """An Evidence source that searches the stored words as the reader."""

    def __init__(self, runtime: DatabaseRuntime) -> None:
        self.runtime = runtime

    async def find(
        self, question: str, *, reader: AuthorizationContext, request_id: UUID
    ) -> FoundEvidence:
        async with self.runtime.transaction() as connection:
            found = await search_lexical_chunks(
                connection, text=SEARCH_WORDS, context=reader, limit=5
            )
        evidence = build_evidence(found.candidates)
        return FoundEvidence(evidence, EvidenceSignals(len(evidence.items), None, None))


async def sync(
    runtime: DatabaseRuntime, folder: Path, access: SourceAccess, at: datetime
) -> None:
    await synchronize_source(
        runtime,
        DirectorySource(folder, access=access),
        source_id=SOURCE_ID,
        observed_at=at,
        principal_directory=DIRECTORY,
    )


async def ask(runtime: DatabaseRuntime, model: Model) -> StreamedAnswer:
    app = create_app(
        streamer=streamer(
            model,
            source=cast(Any, WordSearch(runtime)),
            citations=CurrentCitations(runtime),
        ),
        authenticator=AsAlice(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.post(
            "/v1/answer/stream",
            json={"text": QUESTION.text},
            headers={"Authorization": "Bearer any"},
        )
    reader = AnswerEventReader()
    reader.feed(response.content)
    return reader.close()


async def roadmap_reference(runtime: DatabaseRuntime) -> MessageReference:
    async with runtime.transaction() as connection:
        row = (
            await connection.execute(
                sa.select(
                    chunks.c.chunk_id,
                    documents.c.document_id,
                    documents.c.content_version,
                )
                .join(documents, documents.c.document_id == chunks.c.document_id)
                .where(documents.c.external_id == "roadmap.md")
            )
        ).one()
    return MessageReference(
        chunk_id=ChunkId(row.chunk_id),
        document_id=DocumentId(row.document_id),
        content_version=ContentVersion(row.content_version),
    )


async def earlier_turn(runtime: DatabaseRuntime) -> ConversationId:
    """alice's Conversation, with one earlier Answer citing the roadmap."""
    reference = await roadmap_reference(runtime)
    async with runtime.transaction() as connection:
        started = await start_conversation(
            connection, owner_id=ALICE.user_id, now=OBSERVED_AT, limits=LIMITS
        )
        await add_turn(
            connection,
            owner_id=ALICE.user_id,
            conversation_id=started.conversation_id,
            question="What is on the roadmap?",
            reply="The roadmap.md policy.",
            outcome=MessageOutcome.ANSWERED,
            references=(reference,),
            now=OBSERVED_AT,
            limits=LIMITS,
        )
    return started.conversation_id


async def earlier_answers(
    runtime: DatabaseRuntime, conversation_id: ConversationId
) -> list[str | None]:
    async with runtime.transaction() as connection:
        history = await load_history(
            connection,
            ALICE,
            conversation_id=conversation_id,
            now=LATER,
            budget_tokens=4_000,
        )
    assert history is not None
    return [turn.answer for turn in history.turns]


@pytest.mark.integration
async def test_a_grant_revoked_mid_conversation_fails_every_later_read_closed(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "roadmap.md").write_text("The roadmap.md policy.\n")
    await sync(runtime, tmp_path, ENGINEERING, OBSERVED_AT)
    conversation_id = await earlier_turn(runtime)
    assert await earlier_answers(runtime, conversation_id) == ["The roadmap.md policy."]

    async def revoke() -> None:
        # A Synchronization Run commits while the model is still writing.
        await sync(runtime, tmp_path, CAROL_ONLY, LATER)

    during = await ask(runtime, CitingModel("roadmap.md", before_reply=revoke))
    after = Model()
    later = await ask(runtime, after)
    async with runtime.transaction() as connection:
        searched = await search_lexical_chunks(
            connection, text=SEARCH_WORDS, context=ALICE, limit=5
        )

    # The Answer being written when the grant went is withdrawn.
    assert (during.outcome, during.reason) == (
        StreamOutcome.ABSTAINED,
        AbstentionReason.NO_EVIDENCE,
    )
    assert during.citations == ()
    # Every later read fails closed.
    assert await earlier_answers(runtime, conversation_id) == [None]
    assert searched.candidates == ()
    assert (later.outcome, later.reason) == (
        StreamOutcome.ABSTAINED,
        AbstentionReason.NO_EVIDENCE,
    )
    assert after.calls == 0


@pytest.mark.integration
async def test_a_grant_kept_by_the_same_run_still_answers(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    # The control case: the same run, but engineering keeps the roadmap.
    (tmp_path / "roadmap.md").write_text("The roadmap.md policy.\n")
    await sync(runtime, tmp_path, ENGINEERING, OBSERVED_AT)
    conversation_id = await earlier_turn(runtime)

    async def resync() -> None:
        await sync(runtime, tmp_path, ENGINEERING, LATER)

    during = await ask(runtime, CitingModel("roadmap.md", before_reply=resync))

    assert during.outcome is StreamOutcome.ANSWERED
    assert await earlier_answers(runtime, conversation_id) == ["The roadmap.md policy."]


@pytest.mark.integration
async def test_a_question_asked_while_the_run_still_parses_already_fails_closed(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    # notes.md comes first on the page and changes its text; roadmap.md
    # comes second and drops engineering. alice asks about the roadmap
    # while the run is still working on notes.md.
    # Plain words too: the search reads "roadmap.md" as one file name.
    roadmap = "The roadmap.md policy. Roadmap policy.\n"
    await synchronize_source(
        runtime,
        OnePage(
            [
                a_record("notes.md", "# Notes\n\nOld.\n", "group:engineering"),
                a_record("roadmap.md", roadmap, "group:engineering"),
            ]
        ),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )
    # Without a revocation, alice's question is answered.
    before = await ask(runtime, CitingModel("roadmap.md"))
    assert before.outcome is StreamOutcome.ANSWERED
    answers: list[StreamedAnswer] = []
    reconcile = synchronization.reconcile_document

    async def reconciling(runtime_: Any, normalized: Any, **kwargs: Any) -> Any:
        if normalized.provenance.external_id == "notes.md":
            answers.append(await ask(runtime, CitingModel("roadmap.md")))
        return await reconcile(runtime_, normalized, **kwargs)

    monkeypatch.setattr(synchronization, "reconcile_document", reconciling)

    await synchronize_source(
        runtime,
        OnePage(
            [
                a_record("notes.md", "# Notes\n\nNew.\n", "group:engineering"),
                a_record("roadmap.md", roadmap, "user:carol@example.test"),
            ]
        ),
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=DIRECTORY,
    )

    (during,) = answers
    assert (during.outcome, during.reason) == (
        StreamOutcome.ABSTAINED,
        AbstentionReason.NO_EVIDENCE,
    )
```

The third test first asks the question with nothing revoked and requires an answer. Without that, a search that simply did not find the file would also abstain, and the test would pass whatever the run did. The review found exactly that problem in an earlier draft.

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_synchronization_integration.py tests/test_revocation_integration.py -q
```

Expected: `26 passed`.

### Step 6 — No coding in this step: break it on purpose

This step stops the run from revoking first, and shows which promises depend on it.

**Action 1:** in `synchronize_source` in `src/knowledge_service/ingestion/synchronization.py`, replace

```python
        narrowed = await _revoke_first(
            runtime,
            list(planned.values()),
            source_id=source_id,
            observed_at=observed_at,
        )
```

with

```python
        narrowed: set[str] = set()
```

**Action 2:** run the command from Step 5's check. Expected: `6 failed, 20 passed`:

- `test_a_revocation_is_published_before_any_record_of_its_page_is_parsed`
- `test_a_question_asked_while_the_run_still_parses_already_fails_closed`
- `test_a_broken_file_naming_only_unknown_readers_is_narrowed_to_nobody`
- `test_a_reader_removed_while_the_file_is_broken_loses_it_at_once`, both cases
- `test_access_narrowed_while_broken_is_restored_by_a_good_version`

The second is the leak this lesson closes: alice's question is answered from a file engineering has just lost, because the run had not reached it yet. The last three are M07-T06's promise for broken files, which now rests on the same first phase.

**Action 3:** put the call back and run the command again: `26 passed`.

**Action 4:** compare with your last prediction in the evidence file, and note whether it was right.

### Step 7 — No coding in this step: documents, gates, evidence, and progress

This step updates what the change made inaccurate, proves everything still works, and records the result. The threat model named `M07-T10` as an owner of invariant 3; the trace now names this lesson's tests.

**Action 1:** update two documents.

In `docs/architecture.md`:

Find:

```markdown
            else present
                Sync->>Sync: add its external ID to seen
                Sync->>Norm: SourceRecord
                Norm-->>Sync: NormalizedDocument or Quarantined
                alt NormalizedDocument
                    Sync->>Sync: normalize_access: mapped principals become readers, the rest are kept as unresolved
                    Sync->>Rec: NormalizedDocument + readers
                    Rec->>DB: one transaction: lock row, compare fingerprints, write what changed
                else Quarantined
                    Sync->>Sync: keep it in seen, so its last good version stays
                    Sync->>Rec: narrow_access: keep only readers old and new access both name
                end
            end
        end
```

Replace with:

```markdown
            else present
                Sync->>Sync: add its external ID to seen
                Sync->>Sync: normalize_access: mapped principals become readers, the rest are kept as unresolved
            end
        end
        loop each present record, before any is parsed
            Sync->>Rec: narrow_access: keep only readers old and new access both name
            Rec->>DB: one short transaction each: a removed reader loses the Document now, under a new Authorization Version
        end
        loop each present record
            Sync->>Norm: SourceRecord
            Norm-->>Sync: NormalizedDocument or Quarantined
            alt NormalizedDocument
                Sync->>Rec: NormalizedDocument + readers
                Rec->>DB: one transaction: lock row, compare fingerprints, write what changed
            else Quarantined
                Sync->>Sync: keep it in seen, so its last good version stays, already narrowed
            end
        end
```

In `docs/threat-model.md`:

**1.** Find:

```markdown

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A Source reports no readers, and the Document becomes public by mistake | Only an item the Source marks public gets a public grant; any other item is readable only by the principals `normalize_access` resolves | None |
| A Source principal that is unmapped, malformed, or spelled like another widens who reads a Document | `normalize_access` fails closed: only `user:<id>` and `group:<id>` that the Source's `PrincipalDirectory` maps exactly become grants, as the same UUIDv5 IDs tokens carry; the rest grant nothing and are reported in the synchronization summary, with only their count logged; a directory may map only to subjects and paths a token could carry, and every caller of `synchronize_source` must name its directory | Confluence permission mapping (M08-T14) |
| A reader the Source removed keeps reading the last good version of a file that stopped parsing | `narrow_access` applies the new access to a quarantined record at once, keeping only readers its old and new access both name; nobody gains access until the record normalizes again | None |
| A permission is removed, but the old Chunks stay searchable until cleanup | Grants and Chunks change in one transaction; a tombstone makes a Document unavailable at once | Priority revocation sync (M07-T10) |
| A file removed or unreadable deletes or keeps the wrong Documents | An unreadable folder is unavailable, not deleted; a failed pass tombstones nothing | Incremental sync (M08-T10) |
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one | Retrieved-instruction hardening (M07-T13) |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | Jobs are stored with typed errors and bounded retries | Job idempotency (M08-T03) |

### Retrieval
```

Replace with:

```markdown

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A Source reports no readers, and the Document becomes public by mistake | Only an item the Source marks public gets a public grant; any other item is readable only by the principals `normalize_access` resolves | None |
| A Source principal that is unmapped, malformed, or spelled like another widens who reads a Document | `normalize_access` fails closed: only `user:<id>` and `group:<id>` that the Source's `PrincipalDirectory` maps exactly become grants, as the same UUIDv5 IDs tokens carry; the rest grant nothing and are reported in the synchronization summary, with only their count logged; a directory may map only to subjects and paths a token could carry, and every caller of `synchronize_source` must name its directory | Confluence permission mapping (M08-T14) |
| A reader the Source removed keeps reading the last good version of a file that stopped parsing | `narrow_access` applies the new access to a quarantined record at once, keeping only readers its old and new access both name; nobody gains access until the record normalizes again | None |
| A permission is removed, but the old Chunks stay searchable until cleanup | Grants and Chunks change in one transaction; a tombstone makes a Document unavailable at once | None |
| A revocation waits behind slow parsing and chunking, or reaches a request already in progress | Each page of a Synchronization Run reads its Documents' readers once and commits every narrowing, with a new Authorization Version, before it parses any of its records; a request rereads access at its consistency points: search, the Evidence check before the model, the Citation check before the decision, and History, so every such read after the commit fails closed | A revocation on a later page waits for the earlier pages; a deleted or unlisted Document is tombstoned only at the end of the pass; text streamed between the commit and the Citation check is replaced, not unsent; a revocation applies only when a run reaches it, so scheduled incremental runs come in M08-T06 |
| A file removed or unreadable deletes or keeps the wrong Documents | An unreadable folder is unavailable, not deleted; a failed pass tombstones nothing | Incremental sync (M08-T10) |
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one | Retrieved-instruction hardening (M07-T13) |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | Jobs are stored with typed errors and bounded retries | Job idempotency (M08-T03) |

### Retrieval
```

**2.** Find:

```markdown
Each row traces one invariant under "Invariants" in `docs/security.md` to its control and to at least one test owner: a test pytest collects, written `tests/<file>.py::<test>`, or a task not yet completed that will add one. `make docs-check` fails when an invariant has no row, a test owner no longer exists, or a named task is already completed. Owners in `*_integration.py` files run under `make test-integration`, which CI does not run yet.

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `M07-T10` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `M07-T11`, `M07-T15` |

## Known gaps
```

Replace with:

```markdown
Each row traces one invariant under "Invariants" in `docs/security.md` to its control and to at least one test owner: a test pytest collects, written `tests/<file>.py::<test>`, or a task not yet completed that will add one. `make docs-check` fails when an invariant has no row, a test owner no longer exists, or a named task is already completed. Owners in `*_integration.py` files run under `make test-integration`, which CI does not run yet.

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `tests/test_synchronization_integration.py::test_a_revocation_is_published_before_any_record_of_its_page_is_parsed`, `tests/test_revocation_integration.py::test_a_grant_revoked_mid_conversation_fails_every_later_read_closed` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `M07-T11`, `M07-T15` |

## Known gaps
```

**Action 2:** run the gates in order, with PostgreSQL running:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_revocation_integration.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2784 passed for `make test-unit`, unchanged, and 636 passed for `make test-integration`, 11 more than M07-T09.

**Action 3:** complete the evidence file:

- **Design:** the two phases, how `load_readers` and `removes_readers` pick files, `_counted`, and the consistency points.
- **Verification:** the commands and counts, the deliberate break, and the known limits from "Before you begin".
- **Review:** the review summary from "Before you begin".
- **Decision:** why removals first and additions later, why per page, and why read once instead of locking every file; plus at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M07-T10 row to `docs/curriculum/PROGRESS.md`, set the next task to M07-T11, and commit with `m07-t10: prioritize permission revocation`.

## Reflection questions

1. `synchronize_source` now calls `_revoke_first` before any parsing. What does a caller of `synchronize_source` gain from that without knowing anything about Authorization Versions?
2. Someone moves the narrowing into `reconcile_document` to "keep access logic in one place". What would alice see during a long run, and which test would fail?
3. Why are added readers not applied in the first phase too? Describe a file where doing so would show a new reader text they were never meant to see.
4. A Source has 50 pages, and the only revocation is on page 50. How long can the removed reader still read that file, and what would a per-pass first phase cost?
5. bob was in `/finance` and a run removes him from `payroll.md`. List every place bob could still try to read it during and after the run, and say what each one returns once the narrowing has committed.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the change.
- [ ] `load_readers` reads a page's current readers in one query, and `removes_readers` says whether new access removes anyone.
- [ ] `synchronize_source` narrows every file that loses a reader before parsing any file of the page, counts each file once per page, and reports the access change.
- [ ] The existing synchronization tests still pass (18).
- [ ] The new order, count, and fail-closed tests pass against PostgreSQL (26).
- [ ] You ran the deliberate break, saw the 6 tests fail, and undid it.
- [ ] `docs/architecture.md` and `docs/threat-model.md` describe revocation first and its limits, and the trace names the new tests.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
