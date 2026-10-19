# M03-T06 — Normalize Source Records into Documents

Source task: [M03-T06](../curriculum/milestones/M03-local-ingestion/M03-T06-normalize-source-records.md)

## What you will do

This lesson includes coding: Steps 2–5 write code, while Steps 1 and 6 do not.

So far a Source hands out **Source Records** (raw bytes plus a media type), and three parsers turn Markdown, HTML, and PDF into structural blocks. Nothing connects them yet. This lesson builds that connection: a `normalize` function that takes one Source Record and returns either a normalized Document or a typed **quarantine**, a record set aside with the reason it cannot become a Document.

| Requirement | How this lesson meets it |
|---|---|
| Selects a parser | The record's media type picks one of four parsers: plain text, Markdown, HTML, or PDF. |
| Canonicalizes metadata | The media type is lowercased and loses parameters such as `charset`. The title becomes one clean line. |
| Records parser version | Every result names the parser and the library version that produced it, such as `pdf/1 pypdf/6.21.1`. |
| Detects empty content | Whitespace-only content, and content with no blocks after parsing, are quarantined as empty. |
| Typed outcomes | `normalize` returns `NormalizedDocument` or `Quarantined` with a `QuarantineReason`. It never raises for bad content. |
| Deterministic | The same record always gives an equal result. |

Terms used in this lesson:

- **Canonical:** one agreed spelling for a value, so that two spellings of the same thing compare equal. `Text/HTML; charset=utf-8` and `text/html` both become `text/html`.
- **NFC:** a Unicode normal form. The letter `é` can be stored as one character or as `e` plus a combining accent; NFC always picks the single character, so equal-looking titles are equal strings.
- **Quarantine:** keeping a bad record out of the searchable knowledge, with a recorded reason, instead of failing the whole Synchronization Run.

**Why `NormalizedDocument` and not `Document`.** `documents.Document` is the stored entity: it needs a `DocumentId`, content and authorization fingerprints, and versions. Fingerprints come in M03-T08 and storing in M03-T12. This lesson produces everything a Document is built from, the provider-neutral content and metadata, and stops there.

**Decision recorded with you before this lesson:** `DirectorySource` now delivers PDFs too. The PDF parser's file-opening phase is still not bounded (the M03-T05 follow-up). Local folders are chosen by an operator, so that hardening is deferred until PDFs can come from a remote or user-supplied Source, such as Confluence or uploads. Step 6 records this.

## Before you begin

- **Connector versus parser.** Today `DirectorySource` decodes UTF-8 itself and raises `UndecodableSourceContent` for a bad file. One such file makes `fetch_page` fail, so the whole run stops. In this lesson the connector only reads bytes, and normalization decodes them, so a bad file becomes one quarantined record. `UndecodableSourceContent` has no remaining use and is removed.
- **A new plain-text parser.** There is no parser for `text/plain` yet. Running plain text through the Markdown parser would treat `#` and `*` as syntax, so the lesson adds a small `text_parser.py`.
- **Files you will touch:**
  - `src/knowledge_service/directory_source.py` and `src/knowledge_service/sources.py`
  - `tests/test_directory_source.py`
  - new `src/knowledge_service/text_parser.py` and `tests/test_text_parser.py`
  - new `src/knowledge_service/normalization.py` and `tests/test_normalization.py`
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your prediction

**Invariant:** the same Source Record and Source always produce an equal outcome. A present record never makes `normalize` raise: it becomes a `NormalizedDocument` with at least one block, or a `Quarantined` with a reason. The outcome keeps the record's identity (`source_id`, `external_id`, source version). A title is never empty, is one NFC line, and holds no control or invisible characters. A quarantine detail never repeats the record's content.

**Action:** create `docs/evidence/M03-T06-normalize-source-records.md`:

```markdown
# M03-T06 — Normalize Source Records evidence

Completed: pending

## Invariant

Pending.

## Prediction

Before writing code, say what you expect `normalize` to return for: a
Markdown file, the same record normalized twice, `Text/HTML; charset=utf-8`,
a PDF, a `.txt` file that is not UTF-8, a file of only spaces, an HTML page
that is only a navigation bar, a scanned PDF, an `image/png` record, a title
made of zero-width characters, and a deleted record.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and your eleven predictions.

### Step 2 — Coding step: `DirectorySource` delivers raw bytes for four formats

| Change | Why |
|---|---|
| `_MEDIA_TYPES` gains `.md`, `.html`, and `.pdf` | Normalization picks the parser by media type, so the connector must report it. |
| The file's bytes go into the record unchanged | Decoding is normalization's job. A PDF is not text at all. |
| The version hashes the raw bytes | The version must change whenever the file changes, including a byte-order mark. |
| `_decode` and `UndecodableSourceContent` are removed | Nothing raises it any more. |

**Decision: one bad file must not stop the read.** A Source's job is to list items and fetch bytes. Whether those bytes make sense is a question about content, and its answer belongs to the one record, not to the page of records around it.

**Action:** replace `src/knowledge_service/directory_source.py` with:

```python
"""A Source adapter that reads document files from one local directory.

The adapter delivers each file's bytes unchanged, with a media type chosen by
its extension. Decoding and parsing belong to normalization, so a file that
cannot be read as text is quarantined there instead of failing the whole read.

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
)

_MEDIA_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".html": "text/html",
    ".pdf": "application/pdf",
}


class DirectorySource:
    """SourcePort over the document files under one local directory."""

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
        return SourceRecord.present(
            external_id=external_id,
            version=hashlib.sha256(raw).hexdigest(),
            media_type=_MEDIA_TYPES[path.suffix.lower()],
            content=raw,
            access=self._access,
            metadata=SourceMetadata(
                title=path.stem.strip() or external_id,
                modified_at=datetime.fromtimestamp(modified, tz=UTC),
                size_bytes=len(raw),
            ),
        )


def _list_files(root: Path) -> list[str]:
    """Return every supported file under `root` as a sorted relative path.

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
```

What changed: the module docstring's first paragraphs, the class docstring, `_MEDIA_TYPES`, the three lines in `_read` that now use `raw`, the `_list_files` docstring, and `_decode` is gone. The import list no longer has `UndecodableSourceContent`.

In `src/knowledge_service/sources.py`, delete the whole `UndecodableSourceContent` class.

Then update `tests/test_directory_source.py`:

1. Remove `UndecodableSourceContent` from the import list.
2. In `test_nested_files_come_back_sorted_by_relative_path`, `notes.md` is now a supported file. Make the unsupported example an `.rtf` file:

```python
async def test_nested_files_come_back_sorted_by_relative_path(tmp_path: Path) -> None:
    write(tmp_path, "b.txt", b"second")
    write(tmp_path, "a/z.txt", b"nested")
    write(tmp_path, "a.txt", b"first")
    write(tmp_path, "notes.rtf", b"{\\rtf1 unsupported}")
```

3. Replace `test_utf8_text_is_kept_and_a_byte_order_mark_removed` and `test_invalid_utf8_raises_a_typed_error_naming_the_file` with:

```python
async def test_each_supported_extension_gets_its_media_type(tmp_path: Path) -> None:
    for name in ("a.txt", "b.md", "c.html", "d.pdf", "e.MD"):
        write(tmp_path, name, b"content")

    records = await read_all(DirectorySource(tmp_path, access=PUBLIC))

    assert [(record.external_id, record.media_type) for record in records] == [
        ("a.txt", "text/plain"),
        ("b.md", "text/markdown"),
        ("c.html", "text/html"),
        ("d.pdf", "application/pdf"),
        ("e.MD", "text/markdown"),
    ]


async def test_file_bytes_are_delivered_unchanged(tmp_path: Path) -> None:
    # Decoding belongs to normalization, so a byte-order mark and bytes that
    # are not UTF-8 both reach the record as they are on disk.
    write(tmp_path, "bom.txt", b"\xef\xbb\xbf" + "привіт".encode())
    write(tmp_path, "bad.txt", b"\xff\xfe broken")

    records = await read_all(DirectorySource(tmp_path, access=PUBLIC))

    assert [record.content for record in records] == [
        b"\xff\xfe broken",
        b"\xef\xbb\xbf" + "привіт".encode(),
    ]
```

`e.MD` checks that the extension is matched without regard to case, which `_list_files` already did with `path.suffix.lower()`. In the second test, `bad.txt` sorts before `bom.txt`, which is why it comes first.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_directory_source.py -q` gives 13 passed.

### Step 3 — Coding step: a plain-text parser

| Rule | Reason |
|---|---|
| Line endings become `\n` first | Offsets must not depend on whether the file came from Windows. Markdown and HTML do the same. |
| A blank line separates paragraphs | That is what a blank line means in a text file. |
| Each block's text is exactly its slice of the text | No surrounding whitespace, so `text[start:end] == block.text`. |
| Over 1,000,000 characters raises `DocumentTooLarge` | The same limit as the other text parsers. |

**Decision: split on blank lines with a simple pattern, then trim.** The pattern `\n[ \t]*\n` only starts at a newline, so the work stays linear even on long runs of spaces. A single regular expression that matched whole paragraphs was slower in the worst case, because it retried a whitespace lookahead at every character.

**Action:** create `src/knowledge_service/text_parser.py`:

```python
"""Parse plain text into paragraph blocks that keep their source offsets.

Line endings are normalized to `\n` first, and every offset indexes that
normalized text. A blank line separates paragraphs; the line breaks inside a
paragraph are kept. Each block's text is exactly its slice of the text.
"""

import re
from collections.abc import Iterator

from knowledge_service.structure import (
    BlockKind,
    DocumentTooLarge,
    ParsedDocument,
    StructuralBlock,
)

MAX_CHARACTERS = 1_000_000

_BLANK_LINE = re.compile(r"\n[ \t]*\n")


def parse_text(text: str, *, max_characters: int = MAX_CHARACTERS) -> ParsedDocument:
    """Return one block per paragraph of `text`, in source order."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(normalized) > max_characters:
        raise DocumentTooLarge(
            f"text has {len(normalized)} characters; the limit is {max_characters}"
        )
    blocks = tuple(
        StructuralBlock(
            kind=BlockKind.PARAGRAPH,
            text=normalized[start:end],
            start=start,
            end=end,
        )
        for start, end in _paragraph_spans(normalized)
    )
    return ParsedDocument(text=normalized, blocks=blocks)


def _paragraph_spans(text: str) -> Iterator[tuple[int, int]]:
    """Yield the span of each paragraph, without its surrounding whitespace."""
    start = 0
    for match in _BLANK_LINE.finditer(text):
        yield from _trimmed(text, start, match.start())
        start = match.end()
    yield from _trimmed(text, start, len(text))


def _trimmed(text: str, start: int, end: int) -> Iterator[tuple[int, int]]:
    """Yield `text[start:end]`'s span without surrounding whitespace, if any."""
    chunk = text[start:end]
    stripped = chunk.strip()
    if stripped:
        offset = start + len(chunk) - len(chunk.lstrip())
        yield offset, offset + len(stripped)
```

`_paragraph_spans` walks the gaps between blank lines; `_trimmed` removes surrounding whitespace from one chunk and skips it if nothing is left. Both are generators, so the spans are produced one at a time instead of building a list of every match first.

Create `tests/test_text_parser.py`:

```python
"""Tests for plain-text paragraph extraction."""

import pytest

from knowledge_service.structure import BlockKind, DocumentTooLarge
from knowledge_service.text_parser import parse_text


def test_blank_lines_separate_paragraphs_and_line_breaks_stay_inside() -> None:
    parsed = parse_text("First line\nstill first.\n\n  Second.  \n \t\nThird.\n")

    assert [block.text for block in parsed.blocks] == [
        "First line\nstill first.",
        "Second.",
        "Third.",
    ]
    assert all(block.kind is BlockKind.PARAGRAPH for block in parsed.blocks)


def test_offsets_slice_each_block_out_of_the_normalized_text() -> None:
    # Ukrainian on purpose; Ruff mistakes Cyrillic letters for look-alikes.
    parsed = parse_text("Привіт,\r\nсвіте.\r\n\r\nДругий абзац.")  # noqa: RUF001

    assert parsed.text == "Привіт,\nсвіте.\n\nДругий абзац."  # noqa: RUF001
    for block in parsed.blocks:
        assert parsed.text[block.start : block.end] == block.text


def test_whitespace_only_text_has_no_blocks() -> None:
    assert parse_text(" \n\n\t \r\n").blocks == ()


def test_text_over_the_limit_raises_a_typed_error() -> None:
    with pytest.raises(DocumentTooLarge, match="characters"):
        parse_text("x" * 11, max_characters=10)
```

The `# noqa: RUF001` lines are needed because Ruff flags Cyrillic letters that look like Latin ones. The Ukrainian text is there on purpose.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_text_parser.py -q` gives 4 passed.

### Step 4 — Coding step: the normalization module

| Piece | What it does |
|---|---|
| `QuarantineReason` | The eight reasons a record can be set aside. Each parser error maps to one. |
| `NormalizedDocument` | Provenance, clean title, canonical media type, modified time, size, access, parser version, and the parsed blocks. |
| `Quarantined` | Provenance, reason, a short safe detail, and the parser version (or `None` when no parser matched). |
| `_PARSERS` | Media type to parser. Text parsers are wrapped by `_decoded`, which turns UTF-8 bytes into `str`. |
| `normalize` | Picks the parser, rejects empty content, parses, maps errors to reasons, and builds the result. |
| `_canonical_media_type` | Drops `; charset=...`, lowercases ASCII, and resolves two aliases. |
| `_clean` / `_canonical_title` | Removes control and invisible characters, applies NFC, and collapses whitespace to single spaces. |

Decisions:

- **Return a value, never raise, for bad content.** A Synchronization Run normalizes many records. If one bad record raised, the caller would need a `try` around every call and could easily lose the reason. A returned `Quarantined` carries the reason as data. A deleted record still raises `InvalidSource`: passing one in is a bug in the caller, because deletions skip normalization (M03-T13).
- **The parser version includes the library version.** The same Markdown can produce different blocks after a `markdown-it-py` upgrade. Recording `markdown/1 markdown-it-py/4.2.0` lets a later task find Documents built by an older parser. Bump the `/1` when you change a parser's output yourself.
- **Decode in one place.** `_decoded` uses `utf-8-sig`, which drops a byte-order mark, and refuses a NUL character, the same rules `DirectorySource` used to apply.
- **Remove invisible characters before NFC.** A zero-width space between `e` and its accent stops NFC from joining them. After the space is removed, the pair must be joined, so NFC runs after the removal.
- **Only lowercase ASCII media types.** Python's `lower()` is Unicode-aware: the Kelvin sign `K` becomes `k`. Media types are ASCII, so a look-alike must stay unsupported rather than pass as `text/markdown`.
- **A title always exists.** The order is the record's title, then the external ID, then `Untitled`. Any of the first two can clean down to nothing if it holds only invisible characters.
- **The quarantine detail never echoes content.** Parser messages contain sizes and limits, not text. The unsupported-type message includes at most 100 characters of the media type.

**Action:** create `src/knowledge_service/normalization.py`:

```python
"""Turn one Source Record into a normalized Document or a typed quarantine.

Normalization picks a parser from the record's media type, decodes text as
UTF-8, parses the content into structural blocks, and canonicalizes the
descriptive metadata. Content it cannot turn into blocks never raises: the
record comes back as `Quarantined` with a reason, so one bad item cannot stop
a Synchronization Run. The same record always produces the same result.
"""

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from importlib.metadata import version

from knowledge_service.documents import DocumentProvenance
from knowledge_service.html_parser import parse_html
from knowledge_service.identifiers import SourceId
from knowledge_service.markdown_parser import parse_markdown
from knowledge_service.pdf_parser import parse_pdf
from knowledge_service.sources import (
    InvalidSource,
    SourceAccess,
    SourceRecord,
    SourceRecordState,
)
from knowledge_service.structure import (
    CorruptDocument,
    DocumentParseError,
    DocumentTooLarge,
    EncryptedDocument,
    NoTextLayer,
    ParsedDocument,
)
from knowledge_service.text_parser import parse_text

MAX_TITLE_CHARACTERS = 200
MAX_DETAIL_CHARACTERS = 200

_UNTITLED = "Untitled"

# Control, format (zero-width, bidirectional, byte-order mark), and surrogate.
_INVISIBLE = frozenset({"Cc", "Cf", "Cs"})
_MEDIA_TYPE_ALIASES = {
    "application/xhtml+xml": "text/html",
    "text/x-markdown": "text/markdown",
}


class QuarantineReason(StrEnum):
    """Why a Source Record could not become a Document."""

    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    UNDECODABLE_TEXT = "undecodable_text"
    EMPTY_CONTENT = "empty_content"
    TOO_LARGE = "too_large"
    ENCRYPTED = "encrypted"
    CORRUPT = "corrupt"
    NO_TEXT_LAYER = "no_text_layer"
    UNPARSABLE = "unparsable"


@dataclass(frozen=True, slots=True)
class NormalizedDocument:
    """A Source Record's content and metadata in provider-neutral form."""

    provenance: DocumentProvenance
    title: str
    media_type: str
    modified_at: datetime | None
    size_bytes: int
    access: SourceAccess
    parser_version: str
    parsed: ParsedDocument


@dataclass(frozen=True, slots=True)
class Quarantined:
    """A Source Record set aside, with the reason it cannot become a Document."""

    provenance: DocumentProvenance
    reason: QuarantineReason
    detail: str
    parser_version: str | None


type NormalizationOutcome = NormalizedDocument | Quarantined


class _Undecodable(Exception):
    """Text content that is not valid UTF-8."""


@dataclass(frozen=True, slots=True)
class _Parser:
    """One parser, the bytes-to-blocks function, and the version it records."""

    version: str
    parse: Callable[[bytes], ParsedDocument]


def _decoded(
    parse: Callable[[str], ParsedDocument],
) -> Callable[[bytes], ParsedDocument]:
    """Wrap a text parser so it takes UTF-8 bytes; a byte-order mark is dropped."""

    def parse_bytes(content: bytes) -> ParsedDocument:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise _Undecodable("content is not valid UTF-8 text") from error
        if "\x00" in text:
            raise _Undecodable("text content contains a NUL character")
        return parse(text)

    return parse_bytes


_PARSERS = {
    "text/plain": _Parser("text/1", _decoded(parse_text)),
    "text/markdown": _Parser(
        f"markdown/1 markdown-it-py/{version('markdown-it-py')}",
        _decoded(parse_markdown),
    ),
    "text/html": _Parser("html/1", _decoded(parse_html)),
    "application/pdf": _Parser(f"pdf/1 pypdf/{version('pypdf')}", parse_pdf),
}


def normalize(record: SourceRecord, *, source_id: SourceId) -> NormalizationOutcome:
    """Return the record as a normalized Document, or quarantine it."""
    content, access = record.content, record.access
    if (
        record.state is SourceRecordState.DELETED
        or record.media_type is None
        or content is None
        or access is None
    ):
        raise InvalidSource("only a present source record can be normalized")
    provenance = DocumentProvenance(
        source_id=source_id,
        external_id=record.external_id,
        source_version=record.version,
    )
    media_type = _canonical_media_type(record.media_type)
    parser = _PARSERS.get(media_type)
    if parser is None:
        return Quarantined(
            provenance=provenance,
            reason=QuarantineReason.UNSUPPORTED_MEDIA_TYPE,
            detail=_detail(f"no parser for media type {media_type[:100]!r}"),
            parser_version=None,
        )
    if not content.strip():
        return _quarantine(provenance, parser, QuarantineReason.EMPTY_CONTENT, "")
    try:
        parsed = parser.parse(content)
    except _Undecodable as error:
        return _quarantine(provenance, parser, QuarantineReason.UNDECODABLE_TEXT, error)
    except DocumentParseError as error:
        return _quarantine(provenance, parser, _reason(error), error)
    if not parsed.blocks:
        return _quarantine(provenance, parser, QuarantineReason.EMPTY_CONTENT, "")
    metadata = record.metadata
    return NormalizedDocument(
        provenance=provenance,
        title=_canonical_title(
            metadata.title if metadata is not None else "", record.external_id
        ),
        media_type=media_type,
        modified_at=metadata.modified_at if metadata is not None else None,
        size_bytes=len(content),
        access=access,
        parser_version=parser.version,
        parsed=parsed,
    )


def _canonical_media_type(media_type: str) -> str:
    """Lowercase the type, drop parameters such as charset, and resolve aliases.

    Media types are ASCII. Only an ASCII value is lowercased, because Unicode
    lowercasing would turn look-alikes, such as the Kelvin sign, into letters.
    """
    essence = media_type.split(";", 1)[0].strip()
    if essence.isascii():
        essence = essence.lower()
    return _MEDIA_TYPE_ALIASES.get(essence, essence)


def _reason(error: DocumentParseError) -> QuarantineReason:
    match error:
        case DocumentTooLarge():
            return QuarantineReason.TOO_LARGE
        case EncryptedDocument():
            return QuarantineReason.ENCRYPTED
        case CorruptDocument():
            return QuarantineReason.CORRUPT
        case NoTextLayer():
            return QuarantineReason.NO_TEXT_LAYER
        case _:
            return QuarantineReason.UNPARSABLE


def _quarantine(
    provenance: DocumentProvenance,
    parser: _Parser,
    reason: QuarantineReason,
    error: Exception | str,
) -> Quarantined:
    return Quarantined(
        provenance=provenance,
        reason=reason,
        detail=_detail(str(error) or reason.value.replace("_", " ")),
        parser_version=parser.version,
    )


def _detail(message: str) -> str:
    return _clean(message)[:MAX_DETAIL_CHARACTERS].rstrip()


def _canonical_title(title: str, external_id: str) -> str:
    """Return a one-line NFC title, falling back to the external ID."""
    cleaned = _clean(title) or _clean(external_id) or _UNTITLED
    return cleaned[:MAX_TITLE_CHARACTERS].rstrip()


def _clean(value: str) -> str:
    """Drop control and invisible characters, NFC-normalize, collapse spaces.

    NFC comes after the drop: an invisible character between a letter and its
    accent stops the two from combining until it is gone.
    """
    visible = "".join(
        " " if character.isspace() else character
        for character in value
        if character.isspace() or unicodedata.category(character) not in _INVISIBLE
    )
    return " ".join(unicodedata.normalize("NFC", visible).split())
```

Some details:

- **`type NormalizationOutcome = NormalizedDocument | Quarantined`** is Python 3.12+ syntax for a named type alias. Callers can write `match outcome:` with `case NormalizedDocument():` and `case Quarantined():`.
- **`match error:` with `case DocumentTooLarge():`** checks the error's class, like `isinstance`. The last `case _:` catches any other `DocumentParseError`, such as the HTML parser's nesting limit.
- **`importlib.metadata.version("pypdf")`** reads the installed package version once, when the module is imported.
- **The `if ... raise InvalidSource` at the top of `normalize`** also tells Pyright that `media_type`, `content`, and `access` are not `None` afterwards.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 5 — Coding step: tests at the module interface

| Test | Proves |
|---|---|
| `test_a_markdown_record_becomes_a_document_with_provenance` | The successful case: every field, including provenance and parser version. |
| `test_the_same_record_always_produces_the_same_document` | Determinism. |
| `test_the_media_type_selects_the_parser` (4 cases) | Text, HTML with a `charset`, the XHTML alias, and PDF. |
| `test_a_byte_order_mark_is_not_part_of_the_text` | Decoding drops the BOM. |
| `test_content_that_cannot_become_a_document_is_quarantined` (10 cases) | Every reason, including empty content before and after parsing. |
| `test_a_quarantine_detail_never_contains_the_content` | No content leaks into the detail. |
| `test_the_title_is_one_clean_line` | Combining accent, zero-width space, tab, newline, and a right-to-left override. |
| `test_a_long_title_is_cut_and_a_blank_one_falls_back_to_the_id` | The length limit and the first fallback. |
| `test_a_title_of_only_invisible_characters_never_comes_out_empty` | The last fallback. |
| `test_removing_an_invisible_character_still_leaves_an_nfc_title` | NFC runs after the removal. |
| `test_a_look_alike_media_type_is_not_lowercased_into_a_real_one` | The Kelvin-sign case. |
| `test_a_deleted_record_cannot_be_normalized` | The one typed error `normalize` raises. |
| `test_every_file_in_a_folder_is_normalized_or_quarantined` | The whole path: `DirectorySource` to `normalize`, with one bad file among four good ones. |

The last three title tests and the Kelvin test came from an adversarial review of the first version of this module. Each one failed against that version.

**Action:** create `tests/test_normalization.py`:

```python
"""Tests for turning Source Records into normalized Documents or quarantines."""

import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import pytest

from knowledge_service.directory_source import DirectorySource
from knowledge_service.identifiers import SourceId
from knowledge_service.normalization import (
    MAX_TITLE_CHARACTERS,
    NormalizedDocument,
    Quarantined,
    QuarantineReason,
    normalize,
)
from knowledge_service.sources import (
    InvalidSource,
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.structure import BlockKind
from pdf_builder import IMAGE_ONLY, build_pdf, encrypt, text_page

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
PUBLIC = SourceAccess(public=True)
MODIFIED = datetime(2026, 10, 19, 9, 30, tzinfo=UTC)


def record(
    content: bytes,
    media_type: str = "text/markdown",
    *,
    title: str = "Onboarding",
    external_id: str = "guides/onboarding.md",
) -> SourceRecord:
    return SourceRecord.present(
        external_id=external_id,
        version="v1",
        media_type=media_type,
        content=content,
        access=PUBLIC,
        metadata=SourceMetadata(
            title=title, modified_at=MODIFIED, size_bytes=len(content)
        ),
    )


def document(outcome: NormalizedDocument | Quarantined) -> NormalizedDocument:
    assert isinstance(outcome, NormalizedDocument), outcome
    return outcome


def quarantined(outcome: NormalizedDocument | Quarantined) -> Quarantined:
    assert isinstance(outcome, Quarantined), outcome
    return outcome


def test_a_markdown_record_becomes_a_document_with_provenance() -> None:
    content = b"# Onboarding\n\nRead the security policy.\n"

    normalized = document(normalize(record(content), source_id=SOURCE_ID))

    assert normalized.provenance.source_id == SOURCE_ID
    assert normalized.provenance.external_id == "guides/onboarding.md"
    assert normalized.provenance.source_version == "v1"
    assert normalized.title == "Onboarding"
    assert normalized.media_type == "text/markdown"
    assert normalized.modified_at == MODIFIED
    assert normalized.size_bytes == len(content)
    assert normalized.access == PUBLIC
    assert normalized.parser_version.startswith("markdown/1 markdown-it-py/")
    assert [(block.kind, block.text) for block in normalized.parsed.blocks] == [
        (BlockKind.HEADING, "Onboarding"),
        (BlockKind.PARAGRAPH, "Read the security policy."),
    ]


def test_the_same_record_always_produces_the_same_document() -> None:
    same = record(b"# Title\n\nBody text.\n")

    assert normalize(same, source_id=SOURCE_ID) == normalize(same, source_id=SOURCE_ID)


@pytest.mark.parametrize(
    ("media_type", "content", "expected_type", "parser_prefix"),
    [
        ("text/plain", b"Plain paragraph.", "text/plain", "text/1"),
        ("Text/HTML; charset=utf-8", b"<p>Plain paragraph.</p>", "text/html", "html/1"),
        ("application/xhtml+xml", b"<p>Plain paragraph.</p>", "text/html", "html/1"),
        (
            "application/pdf",
            build_pdf(text_page((72, 720, "Plain paragraph."))),
            "application/pdf",
            "pdf/1 pypdf/",
        ),
    ],
    ids=["text", "html-with-charset", "xhtml-alias", "pdf"],
)
def test_the_media_type_selects_the_parser(
    media_type: str, content: bytes, expected_type: str, parser_prefix: str
) -> None:
    normalized = document(normalize(record(content, media_type), source_id=SOURCE_ID))

    assert normalized.media_type == expected_type
    assert normalized.parser_version.startswith(parser_prefix)
    assert [block.text for block in normalized.parsed.blocks] == ["Plain paragraph."]


def test_a_byte_order_mark_is_not_part_of_the_text() -> None:
    content = "﻿Привіт, світе.".encode()

    normalized = document(normalize(record(content, "text/plain"), source_id=SOURCE_ID))

    assert normalized.parsed.text == "Привіт, світе."


@pytest.mark.parametrize(
    ("media_type", "content", "reason"),
    [
        ("image/png", b"\x89PNG\r\n\x1a\n", QuarantineReason.UNSUPPORTED_MEDIA_TYPE),
        ("text/plain", b"\xff\xfe broken", QuarantineReason.UNDECODABLE_TEXT),
        ("text/plain", b"before\x00after", QuarantineReason.UNDECODABLE_TEXT),
        ("text/markdown", b" \n\t\n", QuarantineReason.EMPTY_CONTENT),
        ("text/html", b"<nav>Home | About</nav>", QuarantineReason.EMPTY_CONTENT),
        ("text/markdown", b"x" * 1_000_001, QuarantineReason.TOO_LARGE),
        ("text/html", b"<div>" * 300 + b"deep", QuarantineReason.UNPARSABLE),
        ("application/pdf", b"not a pdf at all", QuarantineReason.CORRUPT),
        (
            "application/pdf",
            build_pdf(IMAGE_ONLY),
            QuarantineReason.NO_TEXT_LAYER,
        ),
        (
            "application/pdf",
            encrypt(build_pdf(text_page((72, 720, "x"))), require_password=True),
            QuarantineReason.ENCRYPTED,
        ),
    ],
    ids=[
        "unsupported",
        "not-utf8",
        "nul",
        "whitespace",
        "only-navigation",
        "too-large",
        "too-deep",
        "corrupt-pdf",
        "scanned-pdf",
        "encrypted-pdf",
    ],
)
def test_content_that_cannot_become_a_document_is_quarantined(
    media_type: str, content: bytes, reason: QuarantineReason
) -> None:
    outcome = quarantined(normalize(record(content, media_type), source_id=SOURCE_ID))

    assert outcome.reason is reason
    assert outcome.provenance.external_id == "guides/onboarding.md"
    assert outcome.detail.strip()
    assert (outcome.parser_version is None) == (
        reason is QuarantineReason.UNSUPPORTED_MEDIA_TYPE
    )


def test_a_quarantine_detail_never_contains_the_content() -> None:
    outcome = quarantined(
        normalize(record(b"secret-token \xff", "text/plain"), source_id=SOURCE_ID)
    )

    assert "secret-token" not in outcome.detail


def test_the_title_is_one_clean_line() -> None:
    messy = "  Café​  plan\n\tQ4 ‮"

    normalized = document(
        normalize(record(b"Body.", "text/plain", title=messy), source_id=SOURCE_ID)
    )

    assert normalized.title == "Café plan Q4"


def test_a_long_title_is_cut_and_a_blank_one_falls_back_to_the_id() -> None:
    long_title = document(
        normalize(record(b"Body.", "text/plain", title="a " * 300), source_id=SOURCE_ID)
    )
    blank_title = document(
        normalize(record(b"Body.", "text/plain", title="​"), source_id=SOURCE_ID)
    )

    assert len(long_title.title) <= MAX_TITLE_CHARACTERS
    assert not long_title.title.endswith(" ")
    assert blank_title.title == "guides/onboarding.md"


def test_a_title_of_only_invisible_characters_never_comes_out_empty() -> None:
    invisible = record(b"Body.", "text/plain", title="\u200b", external_id="\u200b")

    assert document(normalize(invisible, source_id=SOURCE_ID)).title == "Untitled"


def test_removing_an_invisible_character_still_leaves_an_nfc_title() -> None:
    # The zero-width space keeps "e" and the accent apart until it is removed.
    split = record(b"Body.", "text/plain", title="Cafe\u200b\u0301")

    title = document(normalize(split, source_id=SOURCE_ID)).title

    assert title == "Café"
    assert unicodedata.normalize("NFC", title) == title


def test_a_look_alike_media_type_is_not_lowercased_into_a_real_one() -> None:
    kelvin = record(b"# Title", "text/mar\u212adown")

    outcome = quarantined(normalize(kelvin, source_id=SOURCE_ID))

    assert outcome.reason is QuarantineReason.UNSUPPORTED_MEDIA_TYPE


def test_a_deleted_record_cannot_be_normalized() -> None:
    gone = SourceRecord.deleted(external_id="guides/onboarding.md", version=None)

    with pytest.raises(InvalidSource, match="present"):
        normalize(gone, source_id=SOURCE_ID)


async def test_every_file_in_a_folder_is_normalized_or_quarantined(
    tmp_path: Path,
) -> None:
    files = {
        "a.txt": b"Plain text.",
        "b.md": b"# Heading\n\nMarkdown body.",
        "c.html": b"<h1>Page</h1><p>HTML body.</p>",
        "d.pdf": build_pdf(text_page((72, 720, "PDF body."))),
        "e.txt": b"\xff\xfe not utf-8",
    }
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    page = await DirectorySource(tmp_path, access=PUBLIC).fetch_page(
        checkpoint=None, page_size=10
    )

    outcomes = [normalize(item, source_id=SOURCE_ID) for item in page.records]

    assert [
        (outcome.provenance.external_id, type(outcome).__name__) for outcome in outcomes
    ] == [
        ("a.txt", "NormalizedDocument"),
        ("b.md", "NormalizedDocument"),
        ("c.html", "NormalizedDocument"),
        ("d.pdf", "NormalizedDocument"),
        ("e.txt", "Quarantined"),
    ]
    assert quarantined(outcomes[4]).reason is QuarantineReason.UNDECODABLE_TEXT
```

The helpers `document()` and `quarantined()` assert the outcome's type, and then Pyright knows which fields exist. `record()` builds a present record with sensible defaults, so each test shows only what it changes.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_normalization.py tests/test_text_parser.py tests/test_directory_source.py -v
```

All 42 tests pass: 25 functions in `test_normalization.py` (counting parametrized cases separately), 4 in `test_text_parser.py`, and 13 in `test_directory_source.py`.

### Step 6 — No coding in this step: run the gates, then record evidence

**Action:** run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_normalization.py tests/test_text_parser.py tests/test_directory_source.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. In Design, also record these limits, all found by the adversarial review and none caused by this lesson:

- **PDF opening phase (deferred, as decided).** A PDF whose page dictionaries each hide in a padded object stream can still use hundreds of MB while it opens. Fix it before PDFs come from a remote or user-supplied Source.
- **Markdown has no time budget.** Hostile Markdown near the 1,000,000-character limit, such as `![a](` repeated, takes 20–50 seconds inside `markdown-it-py`, and some inputs use 300–500 MB.
- **HTML tables without closing tags.** A 20×15 table that leaves out the optional `</td>` and `</tr>` passes the HTML parser's 256-element nesting limit and is quarantined as `unparsable`. Only `<p>` and `<li>` are closed implicitly today.
- **`charset` is ignored.** Text must be UTF-8; a Latin-1 file is quarantined as `undecodable_text`.

Answer these reflection questions in your own words:

1. Before this lesson, one file that was not UTF-8 made `DirectorySource.fetch_page` raise. What happened to the other files on that page, and to the whole Synchronization Run? What happens to them now?
2. `normalize` returns `Quarantined` instead of raising an exception. What would a caller that processes 1,000 records have to do if it raised instead, and what could go wrong?
3. Why does the parser version include the library version, such as `pypdf/6.21.1`? Describe a situation where two Documents built from the same bytes have different blocks.
4. Which of your tests would let a real bug reach production if you deleted it? Name the test and describe the bug.

**Check:** every command passes, and the evidence explains the design in your words.

## Completion checklist

- [ ] `DirectorySource` returns `.txt`, `.md`, `.html`, and `.pdf` files with the right media types, and delivers their bytes unchanged.
- [ ] `UndecodableSourceContent` is gone, and its old test is replaced.
- [ ] `parse_text` splits plain text into paragraphs whose text is exactly their slice.
- [ ] `normalize` picks the parser by canonical media type and records the parser version.
- [ ] Empty content, undecodable text, unsupported types, and every parser error come back as `Quarantined` with the right reason, never as an exception.
- [ ] The same record gives an equal outcome every time.
- [ ] Titles are one clean NFC line and never empty. Quarantine details never contain content.
- [ ] A deleted record raises `InvalidSource`.
- [ ] All 42 tests in the three test files, the full deterministic suite, format, lint, types, `make check`, and `make docs-check` pass.
- [ ] The evidence file holds your invariant, predictions, design (including the four recorded limits), verification, and your own reflection.

When you're done, share your code or any error you hit, and I'll review it.
