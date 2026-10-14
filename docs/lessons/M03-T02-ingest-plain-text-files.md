# M03-T02 — Ingest plain-text files

Source task: [M03-T02](../curriculum/milestones/M03-local-ingestion/M03-T02-ingest-plain-text-files.md)

## What you will do

This lesson includes coding: Steps 2–4 write code, while Steps 1 and 5 do not.

In M03-T01 you defined the `SourcePort` and proved it with `FakeSource`. Now you write the first real adapter: **`DirectorySource`**, which reads `.txt` files from one local folder and returns them as Source Records through that same port.

The adapter must provide:

| Requirement | How this lesson meets it |
|---|---|
| Deterministic ordering | Records are sorted by relative path, so every run sees the same order. |
| Stable relative-path identity | `external_id` is the path from the root with `/` separators, e.g. `a/z.txt`. |
| Metadata | A new `SourceMetadata` value: title, modified time (UTC), size in bytes. |
| Safe decoding | Strict UTF-8. A byte-order mark is removed. Invalid bytes or a NUL character raise a typed error. |
| Explicit symlink policy | Symbolic links are never followed, whether they point to a file or a folder. |

Terms used below:

- **Symbolic link (symlink):** a file that points to another path. If the adapter followed one, a link inside the root could expose any file on the machine.
- **Path traversal:** reaching outside the root, through a symlink or a path such as `../secret.txt`.

## Before you begin

- `SourceRecord`, `SourcePage`, `SourcePort` and the `SourceError` family live in `src/knowledge_service/sources.py`. `SourceRecord` has no metadata field yet, and Step 2 adds one.
- Python is 3.14, so `Path.walk()` is available. It does not follow symlinks by default. Instead it lists a symlinked folder among the files, which is where the adapter skips it.
- Ruff's `ASYNC` rules flag blocking file I/O inside `async def`. The adapter therefore does its file work in a normal function and runs it with `asyncio.to_thread`, which runs a blocking function on a worker thread so the event loop stays free.
- Files you will touch: `src/knowledge_service/sources.py`, a new `src/knowledge_service/directory_source.py`, a new `tests/test_directory_source.py`, and a new evidence file. Leave the task spec and `PROGRESS.md` alone.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your prediction

Write down what the adapter must keep true before you write it.

**Invariant:** the same folder contents always produce the same records, in the same order, with the same `external_id`s. No record ever holds bytes from outside the root.

**Decision: how a deleted file shows up.** A directory keeps no history, so it cannot say "this file used to exist". `DirectorySource` therefore never emits `SourceRecord.deleted(...)`. A deleted file is simply missing from the next complete read. M03-T13 ("make missing source items unavailable") turns that absence into a tombstone. The explicit `DELETED` state from M03-T01 is for Sources that publish a change feed, such as Confluence later.

This refines one sentence in your M03-T01 invariant. Deletion is either an explicit `DELETED` record or absence from a complete read, depending on what the Source can know.

**Action:** create `docs/evidence/M03-T02-plain-text-files.md`:

```markdown
# M03-T02 — Plain-text directory Source evidence

Completed: pending

## Invariant

Pending.

## Prediction

Before writing code, say what you expect a caller to see for: nested folders,
a modified file, a deleted file, a file that is not valid UTF-8, a symlink that
points outside the root, and a checkpoint of `../secret.txt`.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file exists and contains your invariant and your six predictions.

### Step 2 — Coding step: add metadata and the decoding error to the port

Add the two port values the adapter needs.

| Addition | What it holds |
|---|---|
| `UndecodableSourceContent` | A `SourceError` raised when an item's bytes are not valid UTF-8 text. Carries `external_id`, and never the content. |
| `SourceMetadata` | `title`, `modified_at` (must be UTC), and `size_bytes` (must not be negative). |
| `SourceRecord.metadata` | Optional, so existing code such as `FakeSource` keeps working. A deleted record must not carry it. |

**Decision:** keep metadata as a small typed value, not a `dict`. Pyright can check it, and M03-T06 can canonicalize it field by field.

**Action:** in `src/knowledge_service/sources.py`, add this class directly after `SourceUnauthorized`:

```python
class UndecodableSourceContent(SourceError):
    """A Source item's bytes are not valid UTF-8 text."""

    def __init__(self, external_id: str) -> None:
        super().__init__(f"source item {external_id!r} is not valid UTF-8 text")
        self.external_id = external_id
```

Add this class directly above `class SourceRecordState`:

```python
@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """Descriptive facts a Source reports about one item, before normalization."""

    title: str
    modified_at: datetime
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise InvalidSource("source metadata title must not be empty")
        _require_utc(self.modified_at, "modified_at")
        if self.size_bytes < 0:
            raise InvalidSource("source metadata size must not be negative")
```

In `SourceRecord`, add the new field as the last field:

```python
    content: bytes | None = None
    metadata: SourceMetadata | None = None
```

Then extend the deleted-record check:

```python
            if (
                self.access is not None
                or self.media_type is not None
                or self.content is not None
                or self.metadata is not None
            ):
                raise InvalidSource("a deleted source record carries identity only")
```

Finally, give `present(...)` a `metadata` parameter and pass it through:

```python
        access: SourceAccess,
        metadata: SourceMetadata | None = None,
    ) -> SourceRecord:
        return cls(
            external_id=external_id,
            version=version,
            state=SourceRecordState.PRESENT,
            access=access,
            media_type=media_type,
            content=content,
            metadata=metadata,
        )
```

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_source_port.py tests/test_sources.py -q` still passes. Adding the field changed nothing for existing callers.

### Step 3 — Coding step: write the directory adapter

Create the adapter that satisfies `SourcePort` for one local folder.

| Part | What it does |
|---|---|
| `fetch_page` | Rejects `page_size < 1`, then runs `_read_page` on a worker thread. |
| `_read_page` | Lists every `.txt` path, keeps the ones sorted after the checkpoint, reads the first `page_size`, and sets `next_checkpoint` to the last selected path when more remain. |
| `_resolved_root` | Raises `SourceUnavailable` if the root is not a folder. A folder that cannot be listed also raises `SourceUnavailable`. |
| `_read` | Skips symlinks and anything that resolves outside the root. Returns `None` for a file deleted mid-read. Wraps other OS errors in `SourceUnavailable`. |
| `_list_files` | Walks the tree without following links and returns sorted relative paths. `on_error=_raise` makes an unreadable folder raise instead of being skipped. |
| `_decode` | Strict UTF-8 with the byte-order mark removed. Raises on invalid bytes or a NUL character, which PostgreSQL `text` columns cannot store. |

**Decisions:**

- **Checkpoint:** the last delivered path, not a page number. If someone adds a file between two pages, a path-based checkpoint still resumes at the right place, while a page number would shift and skip or repeat files.
- **The checkpoint is only ever compared, never opened.** That makes `../secret.txt` harmless.
- **Version:** the SHA-256 hash of the decoded content. Editing a file changes the version, but touching it without changing it does not. A hash is also deterministic in tests, which a timestamp is not.
- **An unreadable folder is an error, never an empty folder.** `Path.walk()` silently skips folders it cannot read unless you give it `on_error`. This adapter reports deletion by absence, so a skipped folder would look like every file in it was deleted.
- **Title:** the file name without its extension. A name such as `" .txt"` has a blank stem, so the title falls back to `external_id` rather than failing the whole page.
- **Access:** the caller passes in a `SourceAccess`. A local folder has no access control list (ACL) of its own, so who may read it is configuration.

**Action:** create `src/knowledge_service/directory_source.py`:

```python
"""A Source adapter that reads plain-text files from one local directory.

A file's identity is its path relative to the root, written with forward
slashes. Records come back sorted by that path, and the checkpoint is the last
path delivered. Symbolic links are never followed, because a link inside the
root can point anywhere on the machine.
"""

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

from knowledge_service.sources import (
    InvalidSource,
    SourceAccess,
    SourceMetadata,
    SourcePage,
    SourceRecord,
    SourceUnavailable,
    UndecodableSourceContent,
)

_MEDIA_TYPES = {".txt": "text/plain"}


class DirectorySource:
    """SourcePort over the plain-text files under one local directory."""

    def __init__(self, root: Path, *, access: SourceAccess) -> None:
        self._root = root
        self._access = access

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        if page_size < 1:
            raise InvalidSource("page size must be at least 1")
        return await asyncio.to_thread(self._read_page, checkpoint, page_size)

    def _read_page(self, checkpoint: str | None, page_size: int) -> SourcePage:
        root = self._resolved_root()
        try:
            listed = _list_files(root)
        except OSError as error:
            raise SourceUnavailable("source directory could not be listed") from error
        remaining = [
            external_id
            for external_id in listed
            if checkpoint is None or external_id > checkpoint
        ]
        selected = remaining[:page_size]
        records = tuple(
            record
            for external_id in selected
            if (record := self._read(root, external_id)) is not None
        )
        next_checkpoint = selected[-1] if len(remaining) > page_size else None
        return SourcePage(records=records, next_checkpoint=next_checkpoint)

    def _resolved_root(self) -> Path:
        if not self._root.is_dir():
            raise SourceUnavailable("source directory is not available")
        return self._root.resolve()

    def _read(self, root: Path, external_id: str) -> SourceRecord | None:
        path = root / external_id
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(root):
                return None
            raw = path.read_bytes()
            modified = path.stat().st_mtime
        except FileNotFoundError:
            return None
        except OSError as error:
            raise SourceUnavailable("source file could not be read") from error
        content = _decode(raw, external_id)
        return SourceRecord.present(
            external_id=external_id,
            version=hashlib.sha256(content).hexdigest(),
            media_type=_MEDIA_TYPES[path.suffix.lower()],
            content=content,
            access=self._access,
            metadata=SourceMetadata(
                title=path.stem.strip() or external_id,
                modified_at=datetime.fromtimestamp(modified, tz=UTC),
                size_bytes=len(raw),
            ),
        )


def _list_files(root: Path) -> list[str]:
    """Return every plain-text file under `root` as a sorted relative path.

    A folder that cannot be read raises instead of being skipped: a missing
    file would otherwise look deleted.
    """
    external_ids: list[str] = []
    for directory, _, filenames in root.walk(on_error=_raise):
        for filename in filenames:
            path = directory / filename
            if path.is_symlink() or path.suffix.lower() not in _MEDIA_TYPES:
                continue
            external_ids.append(path.relative_to(root).as_posix())
    return sorted(external_ids)


def _raise(error: OSError) -> None:
    raise error


def _decode(raw: bytes, external_id: str) -> bytes:
    """Return the text as UTF-8 bytes without a byte-order mark."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise UndecodableSourceContent(external_id) from error
    if "\x00" in text:
        raise UndecodableSourceContent(external_id)
    return text.encode("utf-8")
```

Some names in this file come from places that aren't obvious:

- **`root`:** the resolved root, with every link in the root path itself followed. Resolving once lets `is_relative_to(root)` compare real paths. On macOS this matters, because `/var` is itself a link to `/private/var`.
- **`(record := self._read(...))`:** stores each read result in `record` and keeps it only when it is not `None`.
- **`_` in the `for directory, _, filenames` loop:** the list of subfolders, which `walk()` visits by itself.

Every page lists the whole tree again. That is fine for a local folder, and it means a page never relies on a stale listing.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck` reports 0 errors.

### Step 4 — Coding step: test the adapter through the port

Prove each acceptance case with real files in pytest's `tmp_path`, a fresh temporary folder for each test.

| Acceptance case | Test |
|---|---|
| Nested paths and ordering | `test_nested_files_come_back_sorted_by_relative_path` |
| Metadata | `test_a_record_carries_file_metadata` |
| Pagination and resume | `test_pages_resume_after_the_last_delivered_path` |
| Modification | `test_a_modified_file_keeps_its_identity_and_changes_version` |
| Deletion | `test_a_deleted_file_is_absent_from_the_next_read` |
| Safe decoding (success) | `test_utf8_text_is_kept_and_a_byte_order_mark_removed` |
| Encoding failure (typed) | `test_invalid_utf8_raises_a_typed_error_naming_the_file` |
| Traversal through symlinks | `test_symbolic_links_are_never_followed` |
| Traversal through the checkpoint | `test_a_checkpoint_is_never_opened_as_a_path` |
| Unavailable Source (typed) | `test_a_missing_directory_is_unavailable` |
| Unreadable folder is not a deletion | `test_an_unreadable_folder_is_unavailable_not_deleted` |
| Blank file name | `test_a_blank_file_name_falls_back_to_the_path_as_title` |
| Bad page size | `test_page_size_must_be_positive` |

**Decision:** `read_all` takes a `SourcePort`, not a `DirectorySource`. The tests therefore read through the interface, just as ingestion will.

**Action:** create `tests/test_directory_source.py`:

```python
"""Tests for the local directory Source adapter."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from knowledge_service.directory_source import DirectorySource
from knowledge_service.sources import (
    InvalidSource,
    SourceAccess,
    SourcePort,
    SourceRecord,
    SourceUnavailable,
    UndecodableSourceContent,
)

PUBLIC = SourceAccess(public=True)


def write(root: Path, relative: str, content: bytes) -> Path:
    """Create one file under `root`, making parent directories as needed."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


async def read_all(source: SourcePort, *, page_size: int = 100) -> list[SourceRecord]:
    """Follow checkpoints until the Source reports it is exhausted."""
    records: list[SourceRecord] = []
    checkpoint: str | None = None
    while True:
        page = await source.fetch_page(checkpoint=checkpoint, page_size=page_size)
        records.extend(page.records)
        if page.next_checkpoint is None:
            return records
        checkpoint = page.next_checkpoint


def ids(records: list[SourceRecord]) -> list[str]:
    return [record.external_id for record in records]


async def test_nested_files_come_back_sorted_by_relative_path(tmp_path: Path) -> None:
    write(tmp_path, "b.txt", b"second")
    write(tmp_path, "a/z.txt", b"nested")
    write(tmp_path, "a.txt", b"first")
    write(tmp_path, "notes.md", b"# not plain text")

    records = await read_all(DirectorySource(tmp_path, access=PUBLIC))

    assert ids(records) == ["a.txt", "a/z.txt", "b.txt"]
    assert records[1].content == b"nested"
    assert records[1].media_type == "text/plain"
    assert records[1].access == PUBLIC


async def test_a_record_carries_file_metadata(tmp_path: Path) -> None:
    path = write(tmp_path, "guide.txt", b"hello")
    os.utime(path, (1_700_000_000, 1_700_000_000))

    [record] = await read_all(DirectorySource(tmp_path, access=PUBLIC))

    assert record.metadata is not None
    assert record.metadata.title == "guide"
    assert record.metadata.size_bytes == 5
    assert record.metadata.modified_at == datetime(2023, 11, 14, 22, 13, 20, tzinfo=UTC)


async def test_pages_resume_after_the_last_delivered_path(tmp_path: Path) -> None:
    for name in ("a.txt", "b.txt", "c.txt"):
        write(tmp_path, name, name.encode())
    source = DirectorySource(tmp_path, access=PUBLIC)

    first = await source.fetch_page(checkpoint=None, page_size=2)
    rest = await source.fetch_page(checkpoint=first.next_checkpoint, page_size=2)

    assert ids(list(first.records)) == ["a.txt", "b.txt"]
    assert ids(list(rest.records)) == ["c.txt"]
    assert rest.next_checkpoint is None


async def test_a_modified_file_keeps_its_identity_and_changes_version(
    tmp_path: Path,
) -> None:
    path = write(tmp_path, "doc.txt", b"before")
    source = DirectorySource(tmp_path, access=PUBLIC)
    [before] = await read_all(source)

    path.write_bytes(b"after")
    [after] = await read_all(source)

    assert after.external_id == before.external_id
    assert after.version != before.version
    assert after.content == b"after"


async def test_a_deleted_file_is_absent_from_the_next_read(tmp_path: Path) -> None:
    write(tmp_path, "keep.txt", b"keep")
    gone = write(tmp_path, "gone.txt", b"gone")
    source = DirectorySource(tmp_path, access=PUBLIC)
    assert ids(await read_all(source)) == ["gone.txt", "keep.txt"]

    gone.unlink()

    assert ids(await read_all(source)) == ["keep.txt"]


async def test_a_blank_file_name_falls_back_to_the_path_as_title(
    tmp_path: Path,
) -> None:
    write(tmp_path, " .txt", b"untitled")

    [record] = await read_all(DirectorySource(tmp_path, access=PUBLIC))

    assert record.metadata is not None
    assert record.metadata.title == " .txt"


async def test_utf8_text_is_kept_and_a_byte_order_mark_removed(tmp_path: Path) -> None:
    write(tmp_path, "uk.txt", b"\xef\xbb\xbf" + "привіт".encode())

    [record] = await read_all(DirectorySource(tmp_path, access=PUBLIC))

    assert record.content == "привіт".encode()


async def test_invalid_utf8_raises_a_typed_error_naming_the_file(
    tmp_path: Path,
) -> None:
    write(tmp_path, "bad.txt", b"\xff\xfe broken")

    with pytest.raises(UndecodableSourceContent) as caught:
        await read_all(DirectorySource(tmp_path, access=PUBLIC))

    assert caught.value.external_id == "bad.txt"


async def test_symbolic_links_are_never_followed(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    write(outside, "secret.txt", b"secret")
    write(root, "inside.txt", b"inside")
    (root / "link.txt").symlink_to(outside / "secret.txt")
    (root / "linked").symlink_to(outside, target_is_directory=True)

    records = await read_all(DirectorySource(root, access=PUBLIC))

    assert ids(records) == ["inside.txt"]


async def test_a_checkpoint_is_never_opened_as_a_path(tmp_path: Path) -> None:
    root = tmp_path / "root"
    write(tmp_path, "secret.txt", b"secret")
    write(root, "inside.txt", b"inside")
    source = DirectorySource(root, access=PUBLIC)

    page = await source.fetch_page(checkpoint="../secret.txt", page_size=10)

    assert ids(list(page.records)) == ["inside.txt"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")
async def test_an_unreadable_folder_is_unavailable_not_deleted(
    tmp_path: Path,
) -> None:
    write(tmp_path, "b.txt", b"visible")
    locked = write(tmp_path, "sub/a.txt", b"hidden").parent
    locked.chmod(0)
    try:
        with pytest.raises(SourceUnavailable):
            await read_all(DirectorySource(tmp_path, access=PUBLIC))
    finally:
        locked.chmod(0o755)


async def test_a_missing_directory_is_unavailable(tmp_path: Path) -> None:
    source = DirectorySource(tmp_path / "missing", access=PUBLIC)

    with pytest.raises(SourceUnavailable):
        await source.fetch_page(checkpoint=None, page_size=10)


async def test_page_size_must_be_positive(tmp_path: Path) -> None:
    source = DirectorySource(tmp_path, access=PUBLIC)

    with pytest.raises(InvalidSource, match="at least 1"):
        await source.fetch_page(checkpoint=None, page_size=0)
```

Some of these tests rely on details worth knowing:

- **The unreadable-folder test** sets the folder's permissions to `0`, meaning nobody may read it, and restores them in `finally` so pytest can delete `tmp_path` afterwards. It is skipped when running as root, because root ignores permissions.
- **`a.txt` sorts before `a/z.txt`:** `.` comes before `/` in character order.
- **`1_700_000_000` seconds after 1970 is 2023-11-14 22:13:20 UTC.** Pinning the time with `os.utime` keeps the metadata test deterministic.
- **The checkpoint test relies on sorting:** `../secret.txt` sorts before `inside.txt`, so the adapter returns `inside.txt` and never opens the parent folder.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_directory_source.py -v
```

All 13 tests pass.

### Step 5 — No coding in this step: run the gates, then record evidence

Prove the change is clean, then explain it in your own words.

**Action:** run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_directory_source.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Then fill in `## Design`, `## Verification`, and `## Reflection` in the evidence file. Name the changed files, record the exact results, and answer these questions:

1. Code that reads from `DirectorySource` sees only pages of records. List three things the adapter handles that this code never has to think about.
2. Suppose the checkpoint were a page number ("page 2") instead of the last file path. A colleague adds a new file between your first and second page. What would happen to the files on the second page, and which later task would that break?
3. Suppose you deleted `test_symbolic_links_are_never_followed` and someone later changed `root.walk()` to `root.walk(follow_symlinks=True)`. What could leak into the knowledge base, and who could then read it?

**Check:** every command passes, `git diff --check` prints nothing, and the evidence file explains the design in your words.

## Completion checklist

- [ ] `SourceMetadata` and `UndecodableSourceContent` exist in `sources.py`, and `SourceRecord` accepts optional `metadata` (never on a deleted record).
- [ ] `src/knowledge_service/directory_source.py` defines `DirectorySource`, and Pyright accepts it wherever a `SourcePort` is expected.
- [ ] Records are sorted by relative path, `external_id` uses `/`, and the checkpoint is the last delivered path.
- [ ] Invalid UTF-8 or a NUL character raises `UndecodableSourceContent` naming the file. A byte-order mark is removed.
- [ ] Symlinks are never followed, and a `../` checkpoint never reads outside the root.
- [ ] An unreadable folder raises `SourceUnavailable`, and a blank file name falls back to the path as the title.
- [ ] `tests/test_directory_source.py` covers nested paths, encoding failure, modification, deletion, and traversal attempts.
- [ ] The narrow test, format, lint, type checks, the deterministic suite, `make check`, and `make docs-check` all pass.
- [ ] The evidence file holds your invariant, prediction, design, verification results, and reflection.

When you're done, share your code or any error you hit, and I'll review it.
