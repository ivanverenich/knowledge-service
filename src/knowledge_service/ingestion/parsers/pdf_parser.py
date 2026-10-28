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
is what keeps a single page from running long. That limit is checked one
stream at a time, so a page decodes at most one stream past it before it is
refused, and the PDF library caps a single stream at 75 MB. The fonts a page
uses count toward the same limit, because extracting text decodes their
character maps too.
"""

import re
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from io import BytesIO

from pypdf import PageObject, PasswordType, PdfReader
from pypdf.errors import LimitReachedError
from pypdf.generic import ArrayObject, DictionaryObject, PdfObject, StreamObject

from knowledge_service.ingestion.parsers.structure import (
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
    reader, pages = _open_pages(content, max_pages)
    for number, page in enumerate(pages, start=1):
        if time.monotonic() >= deadline:
            raise DocumentTooLarge(
                f"pdf extraction took longer than {max_seconds} seconds"
            )
        page_text = _page_text(page, max_page_content_bytes)
        _drop_decoded_streams(reader)
        for paragraph in _paragraphs(page_text):
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


def _open_pages(content: bytes, max_pages: int) -> tuple[PdfReader, list[PageObject]]:
    with _pdf_errors():
        reader = PdfReader(BytesIO(content))
        if reader.is_encrypted and reader.decrypt("") == PasswordType.NOT_DECRYPTED:
            raise EncryptedDocument("pdf requires a password to open")
        if len(reader.pages) > max_pages:
            raise DocumentTooLarge(
                f"pdf has {len(reader.pages)} pages; the limit is {max_pages}"
            )
        return reader, list(reader.pages)


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
