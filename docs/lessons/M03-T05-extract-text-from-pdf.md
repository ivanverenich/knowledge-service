# M03-T05 — Extract text from PDF

Source task: [M03-T05](../curriculum/milestones/M03-local-ingestion/M03-T05-extract-text-from-pdf.md)

## What you will do

This lesson includes coding: Steps 2–4 write code, while Steps 1 and 5 do not.

After Markdown and HTML, this is the third format. A PDF is different: it is a binary file of **drawing commands** that place text at positions on a page. It has no paragraphs, headings, or tables, only positioned text. Some pages have no text at all because they are scanned images.

| Requirement | How this lesson meets it |
|---|---|
| Page provenance | Every block records the page it came from, in a new `page` field on `StructuralBlock`. |
| Multi-page text | Each page is split into paragraphs on blank lines, and the paragraphs are returned in page order. |
| Tables | A PDF has no table structure. Layout extraction keeps each table row on one line, such as `Tool Version`. |
| Empty scans, no OCR | An image-only page yields no blocks. A document with no text anywhere raises `NoTextLayer`. **OCR**, reading text out of images, is deliberately not done. |
| Encryption | A PDF that needs a password raises `EncryptedDocument`. One with only an owner password, the usual "protected, but anyone may open it" kind, is read normally. |
| Corrupt files | Anything the PDF library cannot read raises `CorruptDocument`. |
| Bounded | Limits apply to file size, page count, each page's drawing commands, total characters, and total time. |

A few terms used in this lesson:

- **Text layer:** the text a PDF draws with fonts, which can be extracted. A scan has only a picture of text.
- **Form:** a reusable drawing stored once in the file and painted onto pages with `Do`. Stamps and templates use forms.
- **Layout mode:** a `pypdf` extraction mode that keeps the page's spacing, so blank lines separate paragraphs and table columns stay apart.

## Before you begin

- **Dependency:** `pypdf`, pure Python and BSD-3 licensed, with its `crypto` extra. That extra installs `cryptography` (Apache-2.0 or BSD-3), which `pypdf` needs to open AES-encrypted files, even ones with no user password. PyMuPDF was rejected because its AGPL license does not fit this Apache-2.0 project.
- **Input is `bytes`, not `str`.** `parse_pdf(content: bytes)` takes the raw file. `DirectorySource` still decodes every file as UTF-8, so PDFs cannot flow through it yet; M03-T06 fixes that.
- **This lesson's code was attacked before you saw it.** An independent review tried 41 hostile PDFs against an earlier version and broke it four ways:
  - a 10 KB file took 20 seconds and 1 GB of memory
  - the character limit was checked too late
  - control and invisible characters reached the text
  - text drawn through a form was mistaken for a scan

  The code below is the fixed version, and each fix has a test.
- **A new field touches the earlier parsers.** Adding `page` to `StructuralBlock` adds `"page": null` to every block in the Markdown and HTML golden files. Step 2 regenerates them, and you check that nothing else changed.
- **Files you will touch:**
  - `pyproject.toml` and `uv.lock` (through `uv add`)
  - `src/knowledge_service/structure.py`
  - new `src/knowledge_service/pdf_parser.py`
  - new `tests/pdf_builder.py` and `tests/test_pdf_parser.py`
  - the four existing `*.blocks.json` golden files
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your prediction

**Invariant:** the same PDF bytes always produce the same blocks, in page order. Every block's `page` is the 1-based page it came from, and `parsed.text[start:end] == block.text`. Every failure is a typed `DocumentParseError`: `DocumentTooLarge`, `EncryptedDocument`, `CorruptDocument`, or `NoTextLayer`. Block text holds no control or invisible characters and can always be stored as UTF-8. No page is ever sent to OCR.

**Action:** create `docs/evidence/M03-T05-pdf-extraction.md`:

```markdown
# M03-T05 — PDF extraction evidence

Completed: pending

## Invariant

Pending.

## Prediction

Before writing code, say what you expect for: a two-page PDF, a table, a
scanned page between two text pages, a document of only scans, a PDF that
needs a password, a PDF with only an owner password, an empty file, a PNG, a
truncated PDF, and a page that draws a million copies of one letter.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and your ten predictions.

### Step 2 — Coding step: dependency, new errors, and the `page` field

| Addition | What it holds |
|---|---|
| `EncryptedDocument` | The content needs a password. |
| `CorruptDocument` | The bytes are not a readable document of the expected format. |
| `NoTextLayer` | No extractable text, and OCR is not supported. |
| `StructuralBlock.page` | The 1-based page number, or `None` for formats without pages. |

**Decision:** the three errors go in `structure.py` next to `DocumentTooLarge`, so M03-T06 can handle every parser's failures from one module. `page` is optional, so Markdown and HTML blocks stay valid.

**Action:** add the dependency:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv add "pypdf[crypto]>=6,<7"
```

In `src/knowledge_service/structure.py`, add these classes directly after `DocumentTooLarge`:

```python
class EncryptedDocument(DocumentParseError):
    """The content needs a password to read."""


class CorruptDocument(DocumentParseError):
    """The bytes are not a readable document of the expected format."""


class NoTextLayer(DocumentParseError):
    """The document has no extractable text, and OCR is not supported."""
```

In `StructuralBlock`, add `page` as the last field, and check it first in `__post_init__`:

```python
    links: tuple[Link, ...] = ()
    page: int | None = None

    def __post_init__(self) -> None:
        if self.page is not None and self.page < 1:
            raise InvalidBlock("page numbers start at 1")
        if self.start < 0 or self.end < self.start:
```

Then regenerate the four golden files, which now include `"page": null`:

```bash
UPDATE_GOLDEN=1 UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_markdown_parser.py tests/test_html_parser.py -q
```

Then check that `page` is the only change:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python - <<'PY'
import json
import subprocess

changed = subprocess.run(
    ["git", "diff", "--name-only", "tests/fixtures"],
    capture_output=True, text=True, check=True,
).stdout.split()
for path in changed:
    before = json.loads(
        subprocess.run(["git", "show", f"HEAD:{path}"],
                       capture_output=True, text=True, check=True).stdout
    )
    after = json.loads(open(path, encoding="utf-8").read())
    without_page = [{k: v for k, v in b.items() if k != "page"} for b in after]
    print(path, without_page == before and all(b["page"] is None for b in after))
PY
```

**Check:**

- The script prints four lines, one per golden file, each ending in `True`. That means each file only gained `"page": null` on every block, and nothing else changed.
- `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_markdown_parser.py tests/test_html_parser.py -q` passes.

### Step 3 — Coding step: write the PDF parser

| Part | What it does |
|---|---|
| `parse_pdf` | Checks the file size and starts the time budget. Then it reads page by page: it checks the deadline, extracts the text, splits paragraphs, enforces the character limit, and records each block's page. With no blocks at all, it raises `NoTextLayer`. |
| `_pdf_errors` | A context manager that turns every exception from `pypdf` into a typed error: decompression limits and `MemoryError` become `DocumentTooLarge`, and anything else becomes `CorruptDocument`. |
| `_open_pages` | Opens the file. If it is encrypted, it tries the empty password, which opens owner-only PDFs, and otherwise raises `EncryptedDocument`. It also enforces the page limit. |
| `_page_text` | Measures the page's drawing commands plus every form it can paint, refuses more than 1 MB, and extracts text in layout mode. If that finds nothing, it tries plain mode, which reads text inside forms. |
| `_form_bytes` | Adds up the size of the forms a page can paint, including forms inside forms. `seen` stops a form that paints itself. |
| `_paragraphs` | Removes unsafe characters, splits on blank lines, collapses spaces, and drops empty lines. |
| `_UNSAFE` | A translation table. Control characters, zero-width and bidirectional controls, the byte-order mark, and lone surrogates are removed, while tab and the line-separator characters become a space or a newline. |

**Decisions:**

- **Limits per page, checked before extracting.** `pypdf` cannot be interrupted in the middle of a page, so the guard must come before the expensive call. Measuring the commands is nearly free. 1 MB of commands takes at most about 1.7 seconds to extract, and real text pages are far smaller.
- **A time budget between pages, 10 seconds by default.** Together with the per-page limit, a whole document finishes within about 12 seconds.
- **Forms count toward the page.** Without that, a tiny page could paint a huge form and get around the per-page limit.
- **Remove characters rather than reject the document.** A stray control character from a broken font should not cost the whole document. A lone surrogate, half of a 4-byte UTF-16 character, cannot be encoded as UTF-8 at all, so the PostgreSQL insert would fail later.
- **Catching `Exception` is deliberate here.** On damaged input, `pypdf` raises `PdfReadError`, `KeyError`, `IndexError`, `RecursionError`, and others. To the caller they all mean "this PDF is unreadable".

**Action:** create `src/knowledge_service/pdf_parser.py`:

````python
"""Extract the text layer of a PDF, page by page, without OCR.

A PDF stores positioned text, not paragraphs or tables. Text is extracted in
layout mode, so blank lines separate paragraphs and table rows stay on one
line. Each paragraph becomes a block that records its page number.

A page that is only an image, such as a scan, has no text layer and yields no
blocks. If no page has text, the document is refused with `NoTextLayer`: this
service does not run OCR.

Extraction is bounded: by file size, page count, the size of each page's
drawing commands, total characters, and a time budget checked between pages.
Python cannot interrupt the PDF library inside a page, so the per-page limit
is what keeps a single page from running long.
"""

import re
import time
from collections.abc import Generator
from contextlib import contextmanager
from io import BytesIO

from pypdf import PageObject, PasswordType, PdfReader
from pypdf.errors import LimitReachedError
from pypdf.generic import DictionaryObject, StreamObject

from knowledge_service.structure import (
    BlockKind,
    CorruptDocument,
    DocumentParseError,
    DocumentTooLarge,
    EncryptedDocument,
    NoTextLayer,
    ParsedDocument,
    StructuralBlock,
)

MAX_BYTES = 20_000_000
MAX_PAGES = 500
MAX_CHARACTERS = 1_000_000
MAX_PAGE_CONTENT_BYTES = 1_000_000
MAX_SECONDS = 10.0

_BLANK_LINES = re.compile(r"\n[ \t]*\n")
_SPACES = re.compile(r"[ \t]+")
_SEPARATOR = "\n\n"


def _unsafe_characters() -> dict[int, str | None]:
    """Map characters that must not reach stored text to a replacement."""
    table: dict[int, str | None] = dict.fromkeys(range(32))
    table |= dict.fromkeys(range(127, 160))  # DEL and C1 controls
    table |= dict.fromkeys(range(55296, 57344))  # lone surrogates
    for code in (*range(0x200B, 0x2010), *range(0x202A, 0x202F)):
        table[code] = None  # zero-width and bidirectional controls
    for code in (*range(0x2066, 0x206A), 0xFEFF):
        table[code] = None  # bidirectional isolates and the byte-order mark
    for code in (0x0A, 0x0B, 0x0C, 0x85, 0x2028, 0x2029):
        table[code] = "\n"
    table[0x09] = " "
    return table


_UNSAFE = _unsafe_characters()


def parse_pdf(
    content: bytes,
    *,
    max_bytes: int = MAX_BYTES,
    max_pages: int = MAX_PAGES,
    max_characters: int = MAX_CHARACTERS,
    max_page_content_bytes: int = MAX_PAGE_CONTENT_BYTES,
    max_seconds: float = MAX_SECONDS,
) -> ParsedDocument:
    """Return one block per paragraph, in page order, with page numbers."""
    if len(content) > max_bytes:
        raise DocumentTooLarge(
            f"pdf has {len(content)} bytes; the limit is {max_bytes}"
        )
    deadline = time.monotonic() + max_seconds
    blocks: list[StructuralBlock] = []
    paragraphs: list[str] = []
    offset = 0
    for number, page in enumerate(_open_pages(content, max_pages), start=1):
        if time.monotonic() >= deadline:
            raise DocumentTooLarge(
                f"pdf extraction took longer than {max_seconds} seconds"
            )
        for paragraph in _paragraphs(_page_text(page, max_page_content_bytes)):
            if paragraphs:
                offset += len(_SEPARATOR)
            end = offset + len(paragraph)
            if end > max_characters:
                raise DocumentTooLarge(
                    f"pdf text exceeds the limit of {max_characters} characters"
                )
            blocks.append(
                StructuralBlock(
                    kind=BlockKind.PARAGRAPH,
                    text=paragraph,
                    start=offset,
                    end=end,
                    page=number,
                )
            )
            paragraphs.append(paragraph)
            offset = end
    if not blocks:
        raise NoTextLayer("pdf has no extractable text; OCR is not supported")
    return ParsedDocument(text=_SEPARATOR.join(paragraphs), blocks=tuple(blocks))


@contextmanager
def _pdf_errors() -> Generator[None]:
    """Turn every failure inside the PDF library into a typed error."""
    try:
        yield
    except DocumentParseError:
        raise
    except LimitReachedError as error:
        raise DocumentTooLarge("pdf content exceeds a decompression limit") from error
    except MemoryError as error:
        raise DocumentTooLarge("pdf needs too much memory to read") from error
    except Exception as error:
        # pypdf raises many exception types on damaged or hostile input; to the
        # caller they all mean the same thing.
        raise CorruptDocument("pdf could not be read") from error


def _open_pages(content: bytes, max_pages: int) -> list[PageObject]:
    with _pdf_errors():
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
            raise EncryptedDocument("pdf requires a password to open")
        if len(reader.pages) > max_pages:
            raise DocumentTooLarge(
                f"pdf has {len(reader.pages)} pages; the limit is {max_pages}"
            )
        return list(reader.pages)


def _page_text(page: PageObject, max_content_bytes: int) -> str:
    with _pdf_errors():
        contents = page.get_contents()
        size = 0 if contents is None else len(contents.get_data())
        size += _form_bytes(page, set())
        if size > max_content_bytes:
            raise DocumentTooLarge(
                f"a pdf page has {size} bytes of drawing commands; "
                f"the limit is {max_content_bytes}"
            )
        text = page.extract_text(extraction_mode="layout")
        if not text.strip():
            # Layout mode skips text drawn through form objects; plain mode
            # reads it, so a templated page is not mistaken for a scan.
            text = page.extract_text()
        return text


def _form_bytes(owner: DictionaryObject, seen: set[int]) -> int:
    """Return the size of every form a page or form can paint, recursively.

    Forms are reusable drawings stored outside the page's own commands, so
    they count toward the page's limit too. `seen` stops a form that paints
    itself from being counted forever.
    """
    # Indexing resolves references to other objects in the file; .get() does not.
    resources = owner["/Resources"] if "/Resources" in owner else None  # noqa: SIM401
    if not isinstance(resources, DictionaryObject) or "/XObject" not in resources:
        return 0
    xobjects = resources["/XObject"]
    if not isinstance(xobjects, DictionaryObject):
        return 0
    total = 0
    for name in xobjects:
        form = xobjects[name]
        if (
            not isinstance(form, StreamObject)
            or form.get("/Subtype") != "/Form"
            or id(form) in seen
        ):
            continue
        seen.add(id(form))
        total += len(form.get_data()) + _form_bytes(form, seen)
    return total


def _paragraphs(page_text: str) -> list[str]:
    """Split a page into paragraphs; keep line breaks, collapse spaces."""
    text = page_text.replace("\r\n", "\n").replace("\r", "\n").translate(_UNSAFE)
    paragraphs: list[str] = []
    for chunk in _BLANK_LINES.split(text):
        lines = (_SPACES.sub(" ", line).strip() for line in chunk.split("\n"))
        paragraph = "\n".join(line for line in lines if line)
        if paragraph:
            paragraphs.append(paragraph)
    return paragraphs
````

Some names in this file come from places that aren't obvious:

- **`@contextmanager`:** turns the generator `_pdf_errors` into something usable with `with`. Code inside the `with` block runs at the `yield`, so its exceptions surface there and can be translated.
- **`str.translate(_UNSAFE)`:** replaces each character found in the table with its value; `None` deletes it. That is one pass over the text.
- **`dict.fromkeys(range(0x20))`:** maps every code from 0 to 31 to `None`, and the lines after it override the few that should become a space or newline.
- **`owner["/Resources"]`:** indexing a `pypdf` object follows references inside the file. `.get()` does not, which is why the `# noqa: SIM401` comment overrides Ruff's suggestion on that line.
- **`PasswordType.NOT_DECRYPTED`:** what `decrypt("")` returns when the empty password is wrong.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 4 — Coding step: a PDF builder and the tests

**Decision: build test PDFs in code instead of committing binary files.** A PDF is text plus a table of byte offsets, so a short function can write a valid one. The fixtures are then readable, reviewable code. Encrypted copies are made with `pypdf` under random passwords, so no password literal appears in the repo; the secret-scanning hook would flag one. The built-in Helvetica font covers ASCII only, so these fixtures have no Ukrainian text.

| Test | Proves |
|---|---|
| `test_paragraphs_keep_their_page_numbers_in_order` | Multi-page text, paragraphs, and page numbers. |
| `test_offsets_slice_each_block_out_of_the_text` | `text[start:end] == block.text`. |
| `test_table_rows_stay_on_one_line_each` | A two-column table comes out as `Tool Version`, `uv 0.9`, and `Python 3.14`. |
| `test_a_scanned_page_yields_no_blocks_but_later_pages_keep_their_numbers` | An image-only page 2 is skipped, and page 3 keeps the number 3. |
| `test_text_drawn_through_a_form_is_not_mistaken_for_a_scan` | The plain-mode fallback. |
| `test_control_and_invisible_characters_are_removed` | Bell and delete characters are removed, and a vertical tab becomes a newline. |
| `test_a_document_of_only_scans_is_refused_without_ocr` | `NoTextLayer`. |
| `test_a_password_protected_pdf_is_refused` | `EncryptedDocument`. |
| `test_a_pdf_with_only_an_owner_password_is_read` | It gives the same result as the unencrypted file. |
| `test_corrupt_input_raises_a_typed_error` (4 cases) | Empty input, plain text, a PNG, and a truncated PDF. |
| `test_size_limits_raise_typed_errors` | Bytes, pages, characters, drawing commands on a page, commands hidden in a form, and time. |
| `test_a_block_page_number_starts_at_one` | The block model rejects page 0. |

**Action:** create `tests/pdf_builder.py`:

````python
"""Build small PDFs in tests, so fixtures are readable code instead of binaries.

Text uses the built-in Helvetica font, which covers ASCII only.
"""

import secrets
from io import BytesIO

from pypdf import PdfWriter

IMAGE_ONLY = b"q 100 0 0 100 72 600 cm BI /W 1 /H 1 /CS /G /BPC 8 ID \xff EI Q"


def text_page(*lines: tuple[int, int, str]) -> bytes:
    """Return page drawing commands that place each `(x, y, text)` line."""
    commands = [b"BT /F1 12 Tf"]
    for x, y, text in lines:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"1 0 0 1 {x} {y} Tm ({escaped}) Tj".encode("ascii"))
    commands.append(b"ET")
    return b"\n".join(commands)


def build_pdf(*pages: bytes, form: bytes | None = None) -> bytes:
    """Return a valid PDF whose pages draw the given commands.

    With `form`, the PDF also holds a form object, a reusable drawing that
    any page can paint with the command `/X1 Do`.
    """
    page_ids = [4 + 2 * index for index in range(len(pages))]
    form_id = 4 + 2 * len(pages)
    resources = "/Font << /F1 3 0 R >>"
    if form is not None:
        resources += f" /XObject << /X1 {form_id} 0 R >>"
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_id, commands in zip(page_ids, pages, strict=True):
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            + f"/Resources << {resources} >> /Contents {page_id + 1} 0 R >>".encode()
        )
        objects.append(_stream(b"", commands))
    if form is not None:
        objects.append(
            _stream(
                b"/Type /XObject /Subtype /Form /BBox [0 0 612 792] "
                b"/Resources << /Font << /F1 3 0 R >> >> ",
                form,
            )
        )
    out = BytesIO()
    out.write(b"%PDF-1.7\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


def _stream(dictionary: bytes, data: bytes) -> bytes:
    return (
        b"<< "
        + dictionary
        + f"/Length {len(data)} >>\nstream\n".encode()
        + data
        + b"\nendstream"
    )


def encrypt(pdf: bytes, *, require_password: bool) -> bytes:
    """Return `pdf` encrypted with AES-256 under random passwords.

    Without `require_password`, the user password is empty, which is how
    "protected" PDFs that anyone may open but not edit are made.
    """
    writer = PdfWriter(clone_from=BytesIO(pdf))
    writer.encrypt(
        user_password=secrets.token_urlsafe(16) if require_password else "",
        owner_password=secrets.token_urlsafe(16),
        algorithm="AES-256",
    )
    out = BytesIO()
    writer.write(out)
    return out.getvalue()
````

Create `tests/test_pdf_parser.py`:

````python
"""Tests for PDF text extraction: pages, tables, scans, encryption, corruption."""

import pytest

from knowledge_service.pdf_parser import parse_pdf
from knowledge_service.structure import (
    BlockKind,
    CorruptDocument,
    DocumentTooLarge,
    EncryptedDocument,
    InvalidBlock,
    NoTextLayer,
    StructuralBlock,
)
from pdf_builder import IMAGE_ONLY, build_pdf, encrypt, text_page

TWO_PAGES = build_pdf(
    text_page(
        (72, 720, "Onboarding guide"),
        (72, 690, "Read the security policy before"),
        (72, 676, "your first deploy."),
    ),
    text_page((72, 720, "Second page paragraph.")),
)


def test_paragraphs_keep_their_page_numbers_in_order() -> None:
    parsed = parse_pdf(TWO_PAGES)

    assert [(block.page, block.text) for block in parsed.blocks] == [
        (1, "Onboarding guide"),
        (1, "Read the security policy before\nyour first deploy."),
        (2, "Second page paragraph."),
    ]
    assert all(block.kind is BlockKind.PARAGRAPH for block in parsed.blocks)


def test_offsets_slice_each_block_out_of_the_text() -> None:
    parsed = parse_pdf(TWO_PAGES)

    for block in parsed.blocks:
        assert parsed.text[block.start : block.end] == block.text


def test_table_rows_stay_on_one_line_each() -> None:
    table = text_page(
        (72, 720, "Tool"),
        (250, 720, "Version"),
        (72, 706, "uv"),
        (250, 706, "0.9"),
        (72, 692, "Python"),
        (250, 692, "3.14"),
    )

    [block] = parse_pdf(build_pdf(table)).blocks

    assert block.text == "Tool Version\nuv 0.9\nPython 3.14"


def test_a_scanned_page_yields_no_blocks_but_later_pages_keep_their_numbers() -> None:
    parsed = parse_pdf(
        build_pdf(
            text_page((72, 720, "Before the scan.")),
            IMAGE_ONLY,
            text_page((72, 720, "After the scan.")),
        )
    )

    assert [(block.page, block.text) for block in parsed.blocks] == [
        (1, "Before the scan."),
        (3, "After the scan."),
    ]


def test_text_drawn_through_a_form_is_not_mistaken_for_a_scan() -> None:
    pdf = build_pdf(b"/X1 Do", form=text_page((72, 720, "Stamped text.")))

    [block] = parse_pdf(pdf).blocks

    assert (block.page, block.text) == (1, "Stamped text.")


def test_control_and_invisible_characters_are_removed() -> None:
    pdf = build_pdf(text_page((72, 720, "a\x07b\x0bc\x7fd")))

    [block] = parse_pdf(pdf).blocks

    assert block.text == "ab\ncd"


def test_a_document_of_only_scans_is_refused_without_ocr() -> None:
    with pytest.raises(NoTextLayer, match="OCR is not supported"):
        parse_pdf(build_pdf(IMAGE_ONLY, IMAGE_ONLY))


def test_a_password_protected_pdf_is_refused() -> None:
    with pytest.raises(EncryptedDocument, match="password"):
        parse_pdf(encrypt(TWO_PAGES, require_password=True))


def test_a_pdf_with_only_an_owner_password_is_read() -> None:
    parsed = parse_pdf(encrypt(TWO_PAGES, require_password=False))

    assert parsed == parse_pdf(TWO_PAGES)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not a pdf at all",
        b"\x89PNG\r\n\x1a\n" + b"0" * 64,
        TWO_PAGES[: len(TWO_PAGES) // 2],
    ],
    ids=["empty", "text", "png", "truncated"],
)
def test_corrupt_input_raises_a_typed_error(content: bytes) -> None:
    with pytest.raises(CorruptDocument):
        parse_pdf(content)


def test_size_limits_raise_typed_errors() -> None:
    with pytest.raises(DocumentTooLarge, match="bytes"):
        parse_pdf(TWO_PAGES, max_bytes=100)
    with pytest.raises(DocumentTooLarge, match="pages"):
        parse_pdf(TWO_PAGES, max_pages=1)
    with pytest.raises(DocumentTooLarge, match="characters"):
        parse_pdf(TWO_PAGES, max_characters=20)
    with pytest.raises(DocumentTooLarge, match="drawing commands"):
        parse_pdf(TWO_PAGES, max_page_content_bytes=10)
    form_only = build_pdf(b"/X1 Do", form=text_page((72, 720, "x" * 50)))
    with pytest.raises(DocumentTooLarge, match="drawing commands"):
        parse_pdf(form_only, max_page_content_bytes=40)
    with pytest.raises(DocumentTooLarge, match="seconds"):
        parse_pdf(TWO_PAGES, max_seconds=0)


def test_a_block_page_number_starts_at_one() -> None:
    with pytest.raises(InvalidBlock, match="start at 1"):
        StructuralBlock(kind=BlockKind.PARAGRAPH, text="x", start=0, end=1, page=0)
````

Some details these tests rely on:

- **`text_page((x, y, text), ...)`:** places each line at a point; PDF coordinates start at the bottom-left of the page. The table puts its second column at `x=250`.
- **`IMAGE_ONLY`:** draws a 1×1 grey inline image (`BI … EI`), which is a page with no text, like a scan.
- **`max_seconds=0`:** makes the deadline pass immediately, so the time limit is tested without a slow test.
- **`test_a_scanned_page…`:** the `-> (None)` with parentheses is Ruff's formatting for a long function name, not a typo.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_pdf_parser.py -v
```

All 15 tests pass.

### Step 5 — No coding in this step: run the gates, then record evidence

**Action:** run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_pdf_parser.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv lock --check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. Answer these questions:

1. Code that receives these blocks never touches `pypdf`. Name three problems it does not have to deal with, and say which function in `pdf_parser.py` handles each.
2. The parser refuses an all-scan PDF with `NoTextLayer` instead of returning an empty document. Why is a loud, typed refusal better here than an empty result, given that M03-T13 treats a missing document as deleted?
3. Suppose the per-page limit in `_page_text` were removed and only the 20 MB file limit remained. Using the attack from "Before you begin", explain how a 10 KB upload could stall an ingestion worker, and why the 10-second time budget alone would not stop it.

**Check:** every command passes, and the evidence explains the design in your words.

## Completion checklist

- [ ] `pypdf[crypto]` is a dependency in `pyproject.toml` and `uv.lock`.
- [ ] `structure.py` has `EncryptedDocument`, `CorruptDocument`, `NoTextLayer`, and the optional `page` field, and the four golden files changed only by `"page": null`.
- [ ] `parse_pdf` returns paragraphs with page numbers and exact offsets.
- [ ] Scans yield no blocks, an all-scan document raises `NoTextLayer`, and nothing is sent to OCR.
- [ ] Password-protected PDFs raise `EncryptedDocument`, owner-only PDFs are read, and corrupt input raises `CorruptDocument`.
- [ ] Limits on bytes, pages, characters, per-page drawing commands (including forms), and time all raise `DocumentTooLarge`.
- [ ] Control and invisible characters never reach block text.
- [ ] All 15 PDF tests and the full deterministic suite pass, along with format, lint, types, `make check`, `make docs-check`, and `uv lock --check`.
- [ ] The evidence file holds your invariant, prediction, design, verification, and reflection.

When you're done, share your code or any error you hit, and I'll review it.
