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

### Step 6 — Coding step: keep memory bounded while measuring pages

The review after Step 5 broke the "extraction is bounded" invariant. The per-page size check runs too late, so a small file can still use a lot of memory.

| Attack | What happens in the Step 3 code | Measured |
|---|---|---|
| A page paints 20 forms that each inflate to 50 MB | `_form_bytes` decodes every form, and only then compares the total with the limit | 976 KB file, 1,076 MB peak |
| A page lists 20 content streams in a `/Contents` array | `get_contents()` decodes and joins them all before the check | 975 KB file, 231 MB peak |
| A page uses 4 fonts whose `/ToUnicode` maps each inflate to 50 MB | Fonts are not counted at all; `extract_text` decodes their maps | 196 KB file, 275 MB peak, and the page is accepted |
| 130 pages, each just under the 1 MB limit | `pypdf` keeps every decoded object in a cache, `reader.resolved_objects`, until parsing ends | 0.55 MB file, 120 MB peak |

A **decompression bomb** is a small compressed stream that inflates to a huge one. Spaces compress about 1000 to 1, so 50 MB of spaces is about 50 KB in the file.

**Decision: decode one stream at a time and stop as soon as the running total passes the limit.** The walk covers every stream that `extract_text` decodes: content streams, forms (recursively), each font's `/ToUnicode` map, and, for a Type1 font without one, its embedded `/FontFile` or `/FontFile3`. Peak memory is then about the limit plus one stream, and `pypdf` already caps one decoded stream at 75 MB.

**Decision: after each page, drop the streams from `reader.resolved_objects`, but keep object streams.** That dictionary is `pypdf`'s cache of every object it has read. Dropping a page's streams frees their decoded bytes, and a later page that needs one reads it from the file again. An **object stream** (`/ObjStm`) is a compressed stream that packs many small dictionaries; most PDF 1.5+ producers store pages and fonts that way. A first version cleared the whole cache. The re-review measured the cost: `pypdf` then decoded the object stream again for every page, and a normal 253 KB, 50-page file went from 0.6 seconds to over 10. Keeping object streams cached avoids that, and dropping only the other streams keeps memory flat.

**Rejected: lowering `pypdf`'s own decompression limits.** `pypdf.Configuration` can lower its 75 MB per-stream cap, but that cap also applies to legitimate large streams such as embedded fonts. It would also do nothing about many streams that are each just under the cap.

**Action:** edit `src/knowledge_service/pdf_parser.py`.

1. Add these sentences to the end of the module docstring's last paragraph, after "is what keeps a single page from running long.":

```python
is what keeps a single page from running long. That limit is checked one
stream at a time, so a page decodes at most one stream past it before it is
refused, and the PDF library caps a single stream at 75 MB. The fonts a page
uses count toward the same limit, because extracting text decodes their
character maps too.
```

2. Change two imports:

```python
from collections.abc import Generator, Iterator
```

```python
from pypdf.generic import ArrayObject, DictionaryObject, PdfObject, StreamObject
```

3. Make `_open_pages` return the reader together with its pages, because `parse_pdf` now needs the reader to drop its cached streams. Change its signature and its last line:

```python
def _open_pages(content: bytes, max_pages: int) -> tuple[PdfReader, list[PageObject]]:
```

```python
        return reader, list(reader.pages)
```

Keep `list(reader.pages)` inside `_open_pages`, within `_pdf_errors`. For an encrypted file, `len(reader.pages)` only reads the page count the file declares. The actual page tree loads while the pages are listed, and a damaged tree must still raise `CorruptDocument`.

4. In `parse_pdf`, unpack both values and drop the cached streams after each page. Replace the `for number, page in enumerate(_open_pages(...))` line and the `for paragraph in _paragraphs(_page_text(...))` line, so the loop starts like this:

```python
    reader, pages = _open_pages(content, max_pages)
    for number, page in enumerate(pages, start=1):
        if time.monotonic() >= deadline:
            raise DocumentTooLarge(
                f"pdf extraction took longer than {max_seconds} seconds"
            )
        page_text = _page_text(page, max_page_content_bytes)
        _drop_decoded_streams(reader)
        for paragraph in _paragraphs(page_text):
```

The rest of the loop body is unchanged. The text is extracted before the streams are dropped, and `page_text` is a plain `str` that does not depend on the cache.

Add the helper just above `_paragraphs`:

```python
def _drop_decoded_streams(reader: PdfReader) -> None:
    """Forget the streams pypdf cached while reading a page.

    pypdf keeps every object it reads, so decoded page streams would otherwise
    pile up for the whole file. Object streams stay cached: each packs many
    small dictionaries, and decoding one again on every page would make long
    files slow. A dropped stream is read from the file again if needed.
    """
    cache = reader.resolved_objects
    for key in [
        key
        for key, item in cache.items()
        if isinstance(item, StreamObject) and item.get("/Type") != "/ObjStm"
    ]:
        del cache[key]
```

The list is built first and the keys deleted afterwards, because Python does not allow deleting from a dictionary while iterating over it.

5. Replace `_page_text` and `_form_bytes` (everything from `def _page_text` up to, but not including, `def _paragraphs`) with:

```python
def _page_text(page: PageObject, max_content_bytes: int) -> str:
    with _pdf_errors():
        size = 0
        for stream in _decoded_streams(page):
            # Each stream is decoded on its own and the walk stops at the
            # first one over the limit, so memory stays near the limit.
            size += len(stream.get_data())
            if size > max_content_bytes:
                raise DocumentTooLarge(
                    f"a pdf page needs more than {max_content_bytes} bytes of "
                    "drawing commands and fonts"
                )
        text = page.extract_text(extraction_mode="layout")
        if not text.strip():
            # Layout mode skips text drawn through form objects; plain mode
            # reads it, so a templated page is not mistaken for a scan.
            text = page.extract_text()
        return text


def _decoded_streams(page: PageObject) -> Iterator[StreamObject]:
    """Yield every stream that extracting the page's text decodes.

    That is the page's content streams, then the fonts and forms it uses.
    """
    contents = _resolved(page, "/Contents")
    parts = contents if isinstance(contents, ArrayObject) else [contents]
    for part in parts:
        stream = part.get_object() if part is not None else None
        if isinstance(stream, StreamObject):
            yield stream
    yield from _resource_streams(page, set())


def _resource_streams(
    owner: DictionaryObject, seen: set[int]
) -> Iterator[StreamObject]:
    """Yield the font maps and forms a page or form uses, recursively.

    Forms are reusable drawings stored outside the page's own commands, and
    fonts carry the maps that turn character codes into text, so both count
    toward the page's limit. `seen` skips a stream already yielded, which
    also stops a form that paints itself from being walked forever.
    """
    resources = _resolved(owner, "/Resources")
    if not isinstance(resources, DictionaryObject):
        return
    for stream in (*_font_streams(resources), *_forms(resources)):
        if id(stream) in seen:
            continue
        seen.add(id(stream))
        yield stream
        if stream.get("/Subtype") == "/Form":
            yield from _resource_streams(stream, seen)


def _font_streams(resources: DictionaryObject) -> Iterator[StreamObject]:
    """Yield each font's /ToUnicode map and embedded Type1 font file."""
    fonts = _resolved(resources, "/Font")
    if not isinstance(fonts, DictionaryObject):
        return
    for name in fonts:
        font = fonts[name]
        if not isinstance(font, DictionaryObject):
            continue
        candidates = [_resolved(font, "/ToUnicode")]
        descriptor = _resolved(font, "/FontDescriptor")
        if isinstance(descriptor, DictionaryObject):
            candidates += [
                _resolved(descriptor, "/FontFile"),
                _resolved(descriptor, "/FontFile3"),
            ]
        for stream in candidates:
            if isinstance(stream, StreamObject):
                yield stream


def _forms(resources: DictionaryObject) -> Iterator[StreamObject]:
    """Yield the forms listed in a resource dictionary."""
    xobjects = _resolved(resources, "/XObject")
    if not isinstance(xobjects, DictionaryObject):
        return
    for name in xobjects:
        form = xobjects[name]
        if isinstance(form, StreamObject) and form.get("/Subtype") == "/Form":
            yield form


def _resolved(owner: DictionaryObject, key: str) -> PdfObject | None:
    """Return `owner[key]`, following a reference to another object, or None."""
    # Indexing resolves references to other objects in the file; .get() does not.
    return owner[key] if key in owner else None  # noqa: SIM401
```

What each new piece does:

| Function | Role |
|---|---|
| `_page_text` | Adds up stream sizes as the walk produces them and raises `DocumentTooLarge` at the first stream that takes the total over the limit. The message still contains "drawing commands", so the existing tests keep matching. |
| `_decoded_streams` | Yields the page's content streams, one at a time, then the font and form streams. `/Contents` may be one stream or an array of them; `part.get_object()` follows an array item's reference to the actual stream. |
| `_resource_streams` | Walks a page's or form's `/Resources`, then recurses into each form. `seen` now holds every stream already yielded, so a font map shared by two forms is counted once, and a form that paints itself is not walked forever. |
| `_font_streams` | Yields the font streams that `pypdf`'s text extraction decodes. |
| `_forms` | Yields the Form XObjects in a resource dictionary and skips images. |
| `_resolved` | The `owner[key] if key in owner else None` lookup from Step 3, in one place. Its `noqa: SIM401` and its comment no longer need repeating at every call. |

Two Python details:

- **Why generators.** A function with `yield` produces values one at a time, only when the `for` loop asks for the next one. When `_page_text` raises, the walk stops right there, and the streams after it are never decoded. That lazy order is what keeps memory bounded. A function that built a list of every stream first would decode them all before the check.
- **`yield from`** passes on every value from another generator, so the recursion into nested forms reads like an ordinary loop.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes, and the 15 tests from Step 4 still pass.

### Step 7 — Coding step: regression tests for the memory bounds

**Decision: measure memory with `tracemalloc` and build the bombs in code.** `tracemalloc` is a standard-library module that records the peak memory Python allocated between `start()` and `stop()`. Decompressed bytes count, so a test can assert on the parser's own peak. The fixture streams hold only spaces, so a 5 MB stream is about 5 KB in the file, and the tests run in under half a second.

The four memory tests fail against the Step 3 code and pass after Step 6. The object-stream test guards the cache fix itself:

| Test | Step 3 code | After Step 6 |
|---|---|---|
| `…far_past_the_limit[contents]` | fails: peak over 50 MB | passes |
| `…far_past_the_limit[forms]` | fails: peak over 50 MB | passes |
| `…far_past_the_limit[fonts]` | fails: page accepted, no `DocumentTooLarge` | passes |
| `test_memory_does_not_grow_with_the_page_count` | fails: peak 5.1 MB | passes: peak 0.36 MB |
| `test_pages_packed_in_an_object_stream_are_read_quickly` | passes; it guards the cache fix | passes in about 0.1 s; clearing the whole cache takes about 2.7 s and fails it |

**Action:** edit `tests/pdf_builder.py`.

1. Add two imports:

```python
import secrets
import struct
import zlib
from io import BytesIO
from typing import Literal
```

2. Move the file-writing part of `build_pdf` into a shared helper. In `build_pdf`, cut everything from `out = BytesIO()` to `return out.getvalue()`, and put `return _assemble(objects)` in its place. Then add the new builder and the helper above `_stream`. The helper's body is exactly the lines you cut:

```python
def compressed_streams_pdf(
    place: Literal["contents", "forms", "fonts"], *, count: int, size: int
) -> bytes:
    """Return a one-page PDF with `count` streams that each inflate to `size` bytes.

    The streams hold only spaces, so each compresses to a few kilobytes.
    `place` makes them the page's content streams, forms the page paints, or
    the /ToUnicode maps of fonts the page uses.
    """
    blob = zlib.compress(b" " * size, 9)
    ids = [5 + index for index in range(count)]
    contents = "/Contents 4 0 R"
    resources = ""
    commands = b""
    prefix = b""
    fonts: list[bytes] = []
    if place == "contents":
        contents = "/Contents [" + " ".join(f"{i} 0 R" for i in ids) + "]"
    elif place == "forms":
        resources = "/XObject << " + " ".join(f"/R{i} {i} 0 R" for i in ids) + " >>"
        commands = b" ".join(b"/R%d Do" % i for i in ids)
        prefix = b"/Type /XObject /Subtype /Form /BBox [0 0 1 1] "
    else:
        font_ids = [i + count for i in ids]
        resources = "/Font << " + " ".join(f"/R{i} {i} 0 R" for i in font_ids) + " >>"
        commands = (
            b"BT " + b" ".join(b"/R%d 12 Tf (a) Tj" % i for i in font_ids) + b" ET"
        )
        font = (
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode {} 0 R >>"
        )
        fonts = [font.format(i).encode() for i in ids]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        + f"/Resources << {resources} >> {contents} >>".encode(),
        _stream(b"", commands),
        *(_stream(prefix + b"/Filter /FlateDecode ", blob) for _ in ids),
        *fonts,
    ]
    return _assemble(objects)


def _assemble(objects: list[bytes]) -> bytes:
    """Number the objects from 1 and write them with a cross-reference table."""
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
```

Add one more builder below `_assemble`, for the object-stream test. It packs the page tree, the pages, a font, and `filler` extra dictionaries into one `/ObjStm`. Objects inside an object stream cannot be found through a plain `xref` table, so the file ends with a **cross-reference stream** (`/Type /XRef`) instead: one 7-byte row per object, packed with `struct.pack(">BIH", ...)`. Type 1 rows give a byte offset in the file, and type 2 rows give the object stream and the position inside it.

```python
def object_stream_pdf(pages: int, filler: int) -> bytes:
    """Return a PDF whose dictionaries are packed into one object stream.

    Most PDF 1.5+ producers write files this way. Each page draws one line of
    text, and `filler` extra dictionaries make the object stream large.
    """
    page_ids = [3 + index for index in range(pages)]
    content_ids = [3 + pages + index for index in range(pages)]
    font_id = 3 + 2 * pages
    packed: dict[int, bytes] = {
        2: f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] "
        f"/Count {pages} >>".encode(),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page_id, content_id in zip(page_ids, content_ids, strict=True):
        packed[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            + f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>".encode()
        )
    for number in range(font_id + 1, font_id + 1 + filler):
        packed[number] = b"<< /Filler [1 2 3 4] >>"
    stream_id = font_id + 1 + filler
    xref_id = stream_id + 1
    header = b""
    body = b""
    for number, dictionary in packed.items():
        header += b"%d %d " % (number, len(body))
        body += dictionary + b"\n"
    out = BytesIO()
    out.write(b"%PDF-1.7\n")
    offsets = {1: out.tell()}
    out.write(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    for index, content_id in enumerate(content_ids):
        offsets[content_id] = out.tell()
        commands = text_page((72, 720, f"Page {index + 1}."))
        out.write(f"{content_id} 0 obj\n".encode() + _stream(b"", commands))
        out.write(b"\nendobj\n")
    offsets[stream_id] = out.tell()
    data = zlib.compress(header + b"\n" + body)
    out.write(
        f"{stream_id} 0 obj\n".encode()
        + _stream(
            f"/Type /ObjStm /N {len(packed)} /First {len(header) + 1} "
            "/Filter /FlateDecode ".encode(),
            data,
        )
        + b"\nendobj\n"
    )
    offsets[xref_id] = out.tell()
    positions = {number: index for index, number in enumerate(packed)}
    rows = struct.pack(">BIH", 0, 0, 65535)
    for number in range(1, xref_id + 1):
        if number in positions:
            rows += struct.pack(">BIH", 2, stream_id, positions[number])
        else:
            rows += struct.pack(">BIH", 1, offsets[number], 0)
    out.write(
        f"{xref_id} 0 obj\n".encode()
        + _stream(
            f"/Type /XRef /Size {xref_id + 1} /W [1 4 2] /Root 1 0 R ".encode(),
            rows,
        )
        + f"\nendobj\nstartxref\n{offsets[xref_id]}\n%%EOF\n".encode()
    )
    return out.getvalue()
```

In `compressed_streams_pdf`, objects 1 to 4 are the catalog, the page tree, the page, and the page's own drawing commands. The `count` bomb streams follow from object 5, and in the `fonts` case the font dictionaries come after them. The font dictionary template is a plain string, not an f-string: its `{}` is filled later by `font.format(i)`.

Then edit `tests/test_pdf_parser.py`.

3. Add the imports:

```python
import tracemalloc
from typing import Literal

import pytest
```

```python
from pdf_builder import (
    IMAGE_ONLY,
    build_pdf,
    compressed_streams_pdf,
    encrypt,
    object_stream_pdf,
    text_page,
)
```

4. Add these tests before `test_a_block_page_number_starts_at_one`:

```python
@pytest.mark.parametrize("place", ["contents", "forms", "fonts"])
def test_a_page_is_refused_before_it_decompresses_far_past_the_limit(
    place: Literal["contents", "forms", "fonts"],
) -> None:
    # Ten streams of 5 MB each: decoding them all would need over 50 MB.
    pdf = compressed_streams_pdf(place, count=10, size=5_000_000)

    tracemalloc.start()
    try:
        with pytest.raises(DocumentTooLarge, match="drawing commands"):
            parse_pdf(pdf, max_page_content_bytes=1_000_000)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 20_000_000


def test_memory_does_not_grow_with_the_page_count() -> None:
    # Each page carries 250 KB of padding: under the per-page limit, but
    # holding all 20 pages at once would need over 5 MB.
    pages = [text_page((72, 720, f"Page {n}.")) + b" " * 250_000 for n in range(20)]
    pdf = build_pdf(*pages)

    tracemalloc.start()
    try:
        parsed = parse_pdf(pdf)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(parsed.blocks) == 20
    assert peak < 2_500_000


def test_pages_packed_in_an_object_stream_are_read_quickly() -> None:
    # Dropping the whole pypdf cache after each page made it decode the
    # object stream again for every page: about 2.7 seconds for this file.
    pdf = object_stream_pdf(pages=20, filler=5000)

    parsed = parse_pdf(pdf, max_seconds=1.0)

    assert [block.text for block in parsed.blocks[:2]] == ["Page 1.", "Page 2."]
    assert len(parsed.blocks) == 20
```

The `try`/`finally` makes sure tracing stops even when an assertion fails, so one failing test cannot slow down every test after it. The limits leave room on both sides: the page test peaks at 0.36 MB against a 2.5 MB limit, and the Step 3 code needs 5.1 MB.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_pdf_parser.py -v
```

All 20 tests pass.

Optional but useful: write Step 7 before Step 6. The four memory tests then fail against the Step 3 parser with the numbers in the table above. Seeing that proves they test the fix and not something else.

### Step 8 — No coding in this step: rerun the gates and update the evidence

**Action:** run every command from Step 5 again. Then update `docs/evidence/M03-T05-pdf-extraction.md`:

- **Design:** add a paragraph on the review finding and the fix: the four attacks, why the check must happen one stream at a time, why fonts count, and why streams other than object streams are dropped after each page.
- **Verification:** replace the numbers with your new run. Expect 20 PDF tests, and five more tests in the deterministic suite and in `make check` than before.
- **Reflection:** rewrite it in your own words; the task requires that. Also answer one more question:

4. Before Step 6, the parser checked the page's size and then extracted its text, which is the right order. Yet a 1 MB file could still use over 1 GB of memory. What did the check actually measure, when did the expensive work happen, and why does checking one stream at a time fix it?

One limit is known and accepted: inside a single page, layout extraction of nearly 1 MB of tiny text commands can take about 9 seconds and about 100 MB of working memory. That fits "10 seconds plus at most one page", so it needs no code, but say so in Design.

A second limit is known and recorded for follow-up, because fixing it means changing how the file is opened. `list(reader.pages)` in `_open_pages` makes `pypdf` load every page dictionary before the page loop starts. If each dictionary sits in its own object stream padded with spaces, each one is decoded and stays cached. A 198 KB file with 10 such pages used 231 MB and 15 seconds, and neither the per-page limit nor the deadline runs during that phase. A follow-up should bound it before PDFs reach `DirectorySource`, for example by lowering `pypdf`'s per-stream decode limit with `pypdf.apply_configuration` and keeping only a few object streams cached. Record it in Design.

**Check:** every command passes, and the evidence has the new design paragraph, fresh numbers, and your own reflection.

## Completion checklist

- [ ] `pypdf[crypto]` is a dependency in `pyproject.toml` and `uv.lock`.
- [ ] `structure.py` has `EncryptedDocument`, `CorruptDocument`, `NoTextLayer`, and the optional `page` field, and the four golden files changed only by `"page": null`.
- [ ] `parse_pdf` returns paragraphs with page numbers and exact offsets.
- [ ] Scans yield no blocks, an all-scan document raises `NoTextLayer`, and nothing is sent to OCR.
- [ ] Password-protected PDFs raise `EncryptedDocument`, owner-only PDFs are read, and corrupt input raises `CorruptDocument`.
- [ ] Limits on bytes, pages, characters, per-page drawing commands (including forms), and time all raise `DocumentTooLarge`.
- [ ] Control and invisible characters never reach block text.
- [ ] A page is refused as soon as its decoded content streams, forms, and font maps pass the per-page limit, so it never decodes far more than that limit. Decoded streams are dropped from the `pypdf` cache after each page, while object streams stay cached, so memory does not grow with the page count and long files stay fast.
- [ ] All 20 PDF tests and the full deterministic suite pass, along with format, lint, types, `make check`, `make docs-check`, and `uv lock --check`.
- [ ] The evidence file holds your invariant, prediction, design (including the memory fix and the open-phase limit recorded for follow-up), fresh verification numbers, and a reflection in your own words that also answers question 4.

When you're done, share your code or any error you hit, and I'll review it.
