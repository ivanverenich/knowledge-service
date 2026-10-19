# M03-T05 — PDF extraction evidence

Completed: pending

## Invariant

The same PDF bytes always produce the same blocks, in page order. Every
block's `page` is the 1-based page it came from, and
`parsed.text[block.start:block.end] == block.text`. Every failure is a typed
`DocumentParseError`: `DocumentTooLarge`, `EncryptedDocument`,
`CorruptDocument`, or `NoTextLayer`. Block text holds no control or invisible
characters and can always be stored as UTF-8. No page is ever sent to OCR: the
parser reads only a PDF's text layer.

Extraction is bounded. File size, page count, the drawing commands on each
page (including the forms a page paints), total characters, and total time
all have limits. Each one raises `DocumentTooLarge` before the work can run
away.

## Prediction

Written before the parser and its tests exist. This is what I expect for each
case.

- **A two-page PDF.** Each page is split into paragraphs on blank lines. The
  blocks come back in page order, with `page` 1 for the first page's
  paragraphs and 2 for the second's. A paragraph that wraps over two lines
  keeps its line break inside one block.
- **A table.** A PDF stores no table structure, only positioned text. Layout
  extraction keeps each row on one line, so a two-column table comes out as
  one block with lines such as `Tool Version` and `uv 0.9`, with the column
  gap collapsed to one space.
- **A scanned page between two text pages.** The image-only middle page has no
  text layer, so it yields no blocks. The pages around it are still read, and
  the last page keeps its real number, 3, not 2.
- **A document of only scans.** No page has text, so the parser raises
  `NoTextLayer` with a message saying OCR is not supported, instead of
  returning an empty document.
- **A PDF that needs a password.** The empty password does not open it, so the
  parser raises `EncryptedDocument`.
- **A PDF with only an owner password.** The empty user password opens it, so
  it is read exactly like the unencrypted file. That is the common "protected"
  PDF that anyone may read but not edit.
- **An empty file.** Not a PDF, so it raises `CorruptDocument`.
- **A PNG.** Wrong format, so it also raises `CorruptDocument`.
- **A truncated PDF.** The file ends before its cross-reference table, so it
  cannot be read and raises `CorruptDocument`.
- **A page that draws a million copies of one letter.** The file is tiny
  because the commands compress well, but they expand to megabytes. The page
  is refused with `DocumentTooLarge` before extraction starts, because its
  drawing commands exceed the per-page limit. Otherwise it would take tens of
  seconds and about a gigabyte of memory.

## Design

Changed files:

- `pyproject.toml` and `uv.lock`: `pypdf[crypto]>=6,<7`, added with `uv add`.
  `pypdf` is pure Python and BSD-3 licensed. The `crypto` extra brings
  `cryptography`, which `pypdf` needs to open AES-encrypted files. PyMuPDF was
  rejected because its AGPL license does not fit this Apache-2.0 project.
- `src/knowledge_service/structure.py`: `EncryptedDocument`,
  `CorruptDocument`, and `NoTextLayer`, all subclasses of
  `DocumentParseError`, and an optional `page` field on `StructuralBlock` that
  rejects values below 1.
- The four Markdown and HTML `*.blocks.json` golden files: regenerated, and
  the diff holds only the new `"page": null` line (and the comma before it)
  in each block.
- New `src/knowledge_service/pdf_parser.py`: `parse_pdf` and its limits.
- New `tests/pdf_builder.py` and `tests/test_pdf_parser.py` (15 tests).

How `parse_pdf` meets each requirement:

| Requirement | Decision |
|---|---|
| Paragraphs with pages and offsets | Each page is extracted in layout mode, then `_paragraphs` splits it on blank lines and collapses runs of spaces, so table columns become one space. Paragraphs are joined with `\n\n` into `ParsedDocument.text`, and each block records its 1-based page and its exact slice of that text. |
| Scans and OCR | A page with no text layer yields no blocks, but the page counter still advances, so later pages keep their real numbers. If no page has text, the parser raises `NoTextLayer`. Nothing is ever sent to OCR. |
| Text inside forms | Layout mode skips text drawn through form objects, so when it finds nothing, `_page_text` tries plain mode before deciding the page is a scan. |
| Encryption | `_open_pages` tries the empty password. That opens owner-only PDFs, which anyone may read. Anything else raises `EncryptedDocument`. |
| Corrupt input | `_pdf_errors` is a context manager around every `pypdf` call. Our own typed errors pass through unchanged. `LimitReachedError` and `MemoryError` become `DocumentTooLarge`, and any other exception becomes `CorruptDocument`, because `pypdf` raises many unrelated types on damaged input. |
| Unsafe characters | `_UNSAFE` is a `str.translate` table. It removes C0 and C1 controls, DEL, lone surrogates, zero-width and bidirectional controls, and the byte-order mark. Vertical tab, form feed, and the Unicode line separators become `\n`, and tab becomes a space. |
| Bounded cost | File size (20 MB), page count (500), and total characters (1,000,000) are checked as soon as each number is known. Before each page is extracted, its drawing commands plus every form it can paint (`_form_bytes`, recursively, with a `seen` set against self-painting forms) must fit in 1 MB. A 10-second deadline is checked between pages. |

**Why the per-page limit comes before extraction.** Python cannot interrupt
`pypdf` in the middle of `extract_text`, so the only safe place for a guard is
before the call. Measuring the decompressed commands is nearly free. I checked
the attack from the lesson against the finished parser: a page drawing
`(a) Tj` one million times is 7,000,025 bytes of commands that compress to
about 10 KB. It was refused in 0.001 seconds with
`a pdf page has 7000025 bytes of drawing commands; the limit is 1000000`.

**Test fixtures are code, not binaries.** `tests/pdf_builder.py` writes valid
PDFs from text, so every fixture is reviewable. Encrypted copies use random
passwords from `secrets`, so no password literal is in the repo. The built-in
Helvetica font covers ASCII only, so these fixtures have no Ukrainian text.

The parser is not wired into `DirectorySource` yet, which still decodes every
file as UTF-8. That is M03-T06's job.

## Verification

Final run on 2026-10-19 with `UV_CACHE_DIR=/tmp/uv-cache-rag-project`:

| Command | Result |
|---|---|
| `uv run pytest tests/test_pdf_parser.py -q` | 15 passed |
| `make format-check lint typecheck` | 86 files formatted; Ruff: all checks passed; Pyright: 0 errors, 0 warnings |
| `uv run pytest -m "not integration and not live" -q` | 364 passed, 20 deselected |
| `make check` | 365 passed, 17 skipped, 2 deselected; total coverage 83% |
| `make docs-check` | passed |
| `uv lock --check` | passed (62 packages resolved) |
| `git diff --check` | clean |

Each prediction held. Two things came up along the way:

- `test_a_block_page_number_starts_at_one` first failed because my message
  read `page number starts at 1`, not the specified `page numbers start at 1`.
  I fixed the message to match the spec instead of loosening the test.
- The first `make format-check` failed on `pdf_parser.py`: missing blank lines
  between functions and no final newline. `ruff format` fixed it, with no
  change to the code itself.

## Reflection

1. **What code that receives blocks never deals with.** (a) Damaged or
   hostile files raising any of a dozen exception types, such as
   `PdfReadError`, `KeyError`, or `RecursionError`: `_pdf_errors` turns them
   into `CorruptDocument` or `DocumentTooLarge`. (b) Encryption and
   passwords: `_open_pages` tries the empty password and raises
   `EncryptedDocument` otherwise. (c) Raw extracted text with control,
   zero-width, and bidirectional characters, ragged spacing, and no paragraph
   boundaries: `_paragraphs` cleans it with `_UNSAFE` and splits it. A fourth
   is decompression bombs, which `_page_text` with `_form_bytes` refuses
   before extraction.
2. **Why refuse loudly instead of returning an empty document.** An empty
   result looks the same as "this document has no content", and downstream
   that can mean "nothing to index" or even "the document is gone". M03-T13
   treats a missing document as deleted, so a scanned contract that silently
   produced zero blocks could make previously indexed knowledge disappear,
   and nobody would know why. `NoTextLayer` is a typed, specific failure: the
   ingestion job can quarantine the file, report "needs OCR", and leave
   existing data untouched. It turns a silent data loss into a visible,
   actionable error.
3. **Why only the file limit and time budget would not be enough.** A PDF
   stream is compressed, so a page whose commands are `(a) Tj` repeated a
   million times is about 10 KB on disk, far under 20 MB. When extracted, it
   inflates to 7 MB of commands, and `extract_text` takes tens of seconds and
   about 1 GB of memory on that one page, which is what the earlier version
   suffered. The 10-second deadline is only checked between pages, because
   Python cannot interrupt `pypdf` inside `extract_text`. So the deadline
   would notice only after the expensive page finished, and the worker would
   already have been stalled and could have run out of memory. A few such
   pages, or a few such uploads in parallel, could exhaust the workers. The
   per-page limit measures the decompressed commands before the call, which
   is the only point where the parser can still say no cheaply.
