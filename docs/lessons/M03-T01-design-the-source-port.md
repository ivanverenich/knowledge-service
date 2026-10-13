# M03-T01 — Design the Source port

Source task: [M03-T01](../curriculum/milestones/M03-local-ingestion/M03-T01-design-the-source-port.md)

## What you will do

This lesson includes coding. It starts the M03 ingestion milestone.

In M02 you finished persistence: Documents, Chunks, grants, jobs, one transaction boundary. Ingestion is the code that reads content out of some origin and writes it into that store. This lesson defines the seam between them, the **Source port** — a small interface that says what one origin of content can do, without saying how it stores anything.

You will add four things:

- A **Source Record**, the raw item a Source returns before normalization: stable identity, a source version, an access payload, a deletion signal, and content.
- **Pagination** through a checkpoint: the caller asks for one page at a time and resumes from an opaque cursor.
- The **SourcePort** protocol and its typed failures.
- A **fake Source adapter** plus tests that exercise multiple pages, resume, duplicate delivery, deletion, and failure.

The next task, M03-T02, writes a real directory adapter against exactly this interface.

A quick term: a **port** here is a Python `Protocol` — a shape a class must match. The caller depends on the shape, not on a concrete class, so a fake and a real directory adapter are interchangeable.

## Before you begin

- `src/knowledge_service/sources.py` already holds `SourceConfiguration`, `Source`, `SynchronizationRun`, and `InvalidSource`. This lesson adds the port alongside them. That mirrors `model.py`, which keeps the `ChatModel` protocol next to its `ChatModelError` classes.
- `tests/fakes.py` already holds `FakeChatModel` and `SlowChatModel`, the deterministic test adapters for the model port. The fake Source goes there for the same reason.
- `errors.py` already maps failures to public responses. Do not touch it in this lesson; source failures get their own small hierarchy now and are wired into that taxonomy later.
- Tests run with `asyncio_mode = "auto"`, so an `async def test_...` runs without a marker.
- Files you will touch: `src/knowledge_service/sources.py`, `tests/fakes.py`, `tests/test_source_port.py`, and a new evidence file. Do not edit the task spec or `PROGRESS.md`.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your prediction

State what this design must keep true before you design it.

The invariant this change must preserve: ingestion talks to a Source only through the port, so it can page and resume without knowing where records are stored; identity (`external_id`) stays stable across deliveries so a repeated record can be recognised.

**Purpose:** The gate at the end of M03 asks what the seam hid from its caller. Writing it down first makes that answer concrete.

**Decision:** Keep notes in `docs/evidence/M03-T01-source-port.md`, the same convention as M02.

**Action:** Create the evidence file with this skeleton:

```markdown
# M03-T01 — Source port evidence

Completed: pending

## Invariant

Pending.

## Prediction

The fake Source will exercise multiple pages, resume, a repeated record, a
deletion, and one failure. Write what you expect the caller to observe in each
case before you write any code.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** The file exists and holds your invariant and prediction. No code yet.

### Step 2 — Coding step: model one Source Record

Build the value a Source returns for one item.

A Source Record holds:

| Field | Meaning |
|---|---|
| `external_id` | Stable identity inside one Source, for example a relative path. |
| `version` | The Source's own version for the item, or `None` when it has none. |
| `state` | `present` or `deleted`. |
| `access` | The raw ACL payload, or `None` for a deletion. |
| `media_type` | The content type, for parser selection later. |
| `content` | The raw bytes, or `None` for a deletion. |

The access payload, `SourceAccess`, carries a `public` flag and a tuple of `principals` (provider-scoped names such as an email). It is deliberately not the normalized `Subject` from `access.py`: normalization happens in M03-T06.

**Purpose:** One provider-neutral value carries identity, version, access, and content, so no adapter invents its own record shape.

**Decision:** A frozen `dataclass` with two factories, `present(...)` and `deleted(...)`, and a `__post_init__` that refuses two impossible states: a deletion that carries payload, and a present item that carries no content. The factories mirror `Document.register` and `SynchronizationRun.start`.

**Action:** In `src/knowledge_service/sources.py`, add the import:

```python
from typing import Protocol
```

Then append this code to the end of the file:

```python
@dataclass(frozen=True, slots=True)
class SourceAccess:
    """Who a Source reports may read one item, before normalization."""

    public: bool
    principals: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.public and self.principals:
            raise InvalidSource("a public source item must not list principals")
        for principal in self.principals:
            if not principal.strip():
                raise InvalidSource("a source principal must not be empty")
        if len(set(self.principals)) != len(self.principals):
            raise InvalidSource("source principals must be unique")


class SourceRecordState(StrEnum):
    """Whether a Source still offers an item or reports it gone."""

    PRESENT = "present"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class SourceRecord:
    """One item a Source returns, before normalization."""

    external_id: str
    version: str | None
    state: SourceRecordState
    access: SourceAccess | None = None
    media_type: str | None = None
    content: bytes | None = None

    def __post_init__(self) -> None:
        if not self.external_id.strip():
            raise InvalidSource("source record external_id must not be empty")
        if self.version is not None and not self.version.strip():
            raise InvalidSource("source record version must not be empty when provided")
        if self.state is SourceRecordState.DELETED:
            if (
                self.access is not None
                or self.media_type is not None
                or self.content is not None
            ):
                raise InvalidSource("a deleted source record carries identity only")
            return
        if self.access is None:
            raise InvalidSource("a present source record must carry access")
        if self.media_type is None or not self.media_type.strip():
            raise InvalidSource("a present source record must carry a media type")
        if self.content is None:
            raise InvalidSource("a present source record must carry content")

    @classmethod
    def present(
        cls,
        *,
        external_id: str,
        version: str | None,
        media_type: str,
        content: bytes,
        access: SourceAccess,
    ) -> SourceRecord:
        return cls(
            external_id=external_id,
            version=version,
            state=SourceRecordState.PRESENT,
            access=access,
            media_type=media_type,
            content=content,
        )

    @classmethod
    def deleted(cls, *, external_id: str, version: str | None) -> SourceRecord:
        return cls(
            external_id=external_id,
            version=version,
            state=SourceRecordState.DELETED,
        )
```

**Check:** Import the module and build one record of each kind. A deleted record built with `content=b"x"` must raise `InvalidSource` with the message `a deleted source record carries identity only`.

### Step 3 — Coding step: add pagination, the port, and typed failures

Define how a caller reads a Source one page at a time.

A `SourcePage` holds:

| Field | Meaning |
|---|---|
| `records` | The records in this page, in the Source's order. |
| `next_checkpoint` | The cursor to pass on the next call, or `None` when the Source is exhausted. |

The checkpoint is an opaque `str`. The caller must not parse it; it only stores it and passes it back. This is the same cursor the `Source.checkpoint` field already stores as a string.

**Purpose:** Pagination bounds memory and lets a run resume after a failure instead of re-reading everything.

**Decision:** One async method, `fetch_page(*, checkpoint, page_size)`. `checkpoint=None` starts from the beginning. `next_checkpoint=None` means there is nothing more. The port keeps typed failures in a small hierarchy, like `ChatModelError` in `model.py`.

**Action:** Append to `src/knowledge_service/sources.py`:

```python
class SourceError(Exception):
    """Base error for failures a Source adapter reports."""


class SourceUnavailable(SourceError):
    """The Source cannot currently be reached or read."""


class SourceUnauthorized(SourceError):
    """The Source refused the configured credentials."""


@dataclass(frozen=True, slots=True)
class SourcePage:
    """One page of records plus the checkpoint that resumes after it."""

    records: tuple[SourceRecord, ...]
    next_checkpoint: str | None

    def __post_init__(self) -> None:
        if self.next_checkpoint is not None and not self.next_checkpoint.strip():
            raise InvalidSource("a next checkpoint must not be empty when provided")


class SourcePort(Protocol):
    """A paginated origin of raw Source Records."""

    async def fetch_page(
        self,
        *,
        checkpoint: str | None,
        page_size: int,
    ) -> SourcePage:
        """Return the page after `checkpoint`, or the first page when None."""
        ...
```

Keep the old `InvalidSource` for broken values and the new `SourceError` family for adapter failures. They are different problems: a bad value is a programming error, an unavailable Source is an expected runtime condition.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python -c \
  "from knowledge_service.sources import SourcePage, SourcePort, SourceUnavailable"
```

The import must succeed.

### Step 4 — Coding step: add the fake Source adapter

Write the deterministic adapter the tests and the port share.

**Purpose:** The acceptance criterion is that a fake exercises multiple pages, resume, duplicate delivery, deletion, and failure while the caller sees only the port. The fake is that proof.

**Decision:** A `FakeSource` in `tests/fakes.py` built from an explicit list of pages. A checkpoint is the page index as a string, so resume is trivial and the fake never invents storage. A failure is injected by call number. Duplicate delivery is modelled by putting the same record in two pages.

**Action:** In `tests/fakes.py`, add `from collections.abc import Sequence` to the standard-library imports and `SourceError`, `SourcePage`, `SourceRecord`, and `SourceUnavailable` to the `knowledge_service.sources` import. Then append:

```python
class FakeSource:
    """Deterministic in-memory SourcePort over an explicit list of pages."""

    def __init__(
        self,
        pages: Sequence[Sequence[SourceRecord]],
        *,
        fail_on_call: int | None = None,
        failure: SourceError | None = None,
    ) -> None:
        self._pages = tuple(tuple(page) for page in pages)
        self._fail_on_call = fail_on_call
        self._failure = (
            failure if failure is not None else SourceUnavailable("fake source failed")
        )
        self.checkpoints: list[str | None] = []

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        self.checkpoints.append(checkpoint)
        if (
            self._fail_on_call is not None
            and len(self.checkpoints) == self._fail_on_call
        ):
            raise self._failure
        index = 0 if checkpoint is None else int(checkpoint)
        if index >= len(self._pages):
            return SourcePage(records=(), next_checkpoint=None)
        following = index + 1
        next_checkpoint = None if following >= len(self._pages) else str(following)
        return SourcePage(records=self._pages[index], next_checkpoint=next_checkpoint)
```

`page_size` is accepted and ignored because the fake serves whole pages; that matches the signature so the fake still satisfies `SourcePort`.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck` reports no error. That proves `FakeSource` structurally satisfies `SourcePort`.

### Step 5 — Coding step: test the port at its interface

Cover the successful case, the important edge case, and one typed failure.

**Purpose:** The tests are the observable contract M03-T02 must match.

**Decision:** A new `tests/test_source_port.py`. Type the fake as `SourcePort` in the first test, so the test reads through the interface and not through the concrete class. Use `pytest.raises` for the typed failure and for the value invariants.

**Action:** Create `tests/test_source_port.py`:

```python
"""Tests for the Source port contract and its deterministic fake."""

import pytest

from fakes import FakeSource
from knowledge_service.sources import (
    InvalidSource,
    SourceAccess,
    SourcePort,
    SourceRecord,
    SourceRecordState,
    SourceUnavailable,
)


def make_record(external_id: str, *, content: bytes = b"hello") -> SourceRecord:
    """Build one present record with public access."""
    return SourceRecord.present(
        external_id=external_id,
        version="1",
        media_type="text/plain",
        content=content,
        access=SourceAccess(public=True),
    )


async def test_fake_source_walks_every_page_in_order() -> None:
    first = make_record("a.txt")
    second = make_record("b.txt")
    source: SourcePort = FakeSource([[first], [second]])

    page = await source.fetch_page(checkpoint=None, page_size=10)

    assert page.records == (first,)
    assert page.next_checkpoint == "1"

    page = await source.fetch_page(checkpoint=page.next_checkpoint, page_size=10)

    assert page.records == (second,)
    assert page.next_checkpoint is None


async def test_resuming_from_a_checkpoint_skips_delivered_pages() -> None:
    source = FakeSource([[make_record("a.txt")], [make_record("b.txt")]])

    page = await source.fetch_page(checkpoint="1", page_size=10)

    assert [record.external_id for record in page.records] == ["b.txt"]
    assert page.next_checkpoint is None


async def test_a_page_can_repeat_a_record_and_signal_a_deletion() -> None:
    record = make_record("a.txt")
    tombstone = SourceRecord.deleted(external_id="gone.txt", version="3")
    source = FakeSource([[record], [record, tombstone]])

    await source.fetch_page(checkpoint=None, page_size=10)
    page = await source.fetch_page(checkpoint="1", page_size=10)

    assert page.records[0] == record
    assert page.records[1].state is SourceRecordState.DELETED
    assert page.records[1].content is None
    assert page.records[1].access is None


async def test_a_source_failure_is_typed() -> None:
    source = FakeSource([[make_record("a.txt")]], fail_on_call=1)

    with pytest.raises(SourceUnavailable):
        await source.fetch_page(checkpoint=None, page_size=10)


def test_a_deleted_record_carries_identity_only() -> None:
    with pytest.raises(InvalidSource, match="identity only"):
        SourceRecord(
            external_id="gone.txt",
            version=None,
            state=SourceRecordState.DELETED,
            content=b"still here",
        )


def test_a_public_access_payload_lists_no_principals() -> None:
    with pytest.raises(InvalidSource, match="must not list principals"):
        SourceAccess(public=True, principals=("alice@example.com",))
```

**Check:** The narrow test passes:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_source_port.py -v
```

All six tests pass.

### Step 6 — No coding in this step: run the gates, then record evidence

Prove the change is clean and explain it in your own words.

**Purpose:** The task is accepted on evidence plus an explanation, not on a passing command alone.

**Decision:** Run the narrow test first, then the repository gates, and record the exact commands and results in the evidence file.

**Action:** Run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_source_port.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Then fill in `## Design`, `## Verification`, and `## Reflection` in `docs/evidence/M03-T01-source-port.md`. Name the files you changed and answer the reflection questions in your own words:

1. The port returns one page and a checkpoint at a time. What does that force the caller to handle that returning every record in one list would hide?
2. Suppose the port returned all records at once, with no checkpoint, and a record had no stable `external_id`. Which later M03 task would get harder, and why?
3. The test `test_a_source_failure_is_typed` checks for `SourceUnavailable`. If you deleted that test, what would go wrong in production the next time a Source was unreachable?

**Check:** The narrow test and the full deterministic suite pass, format/lint/type/documentation checks pass, `git diff --check` is silent, and the evidence file explains the design and the prediction.

## Completion checklist

- [ ] `SourceAccess`, `SourceRecord`, `SourceRecordState`, `SourcePage`, `SourcePort`, and the `SourceError` family exist in `src/knowledge_service/sources.py`.
- [ ] `FakeSource` exists in `tests/fakes.py` and satisfies `SourcePort`.
- [ ] `tests/test_source_port.py` covers multiple pages, resume, a repeated record, a deletion, and a typed failure.
- [ ] The narrow test passes, then format, lint, type checks, the deterministic suite, and `make check` pass.
- [ ] The design has no provider-specific names or storage details in the public contract.
- [ ] The evidence file records your invariant, prediction, design, verification, and reflection.

Share your implementation or any error you hit and I will review it.
