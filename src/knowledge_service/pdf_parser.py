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
