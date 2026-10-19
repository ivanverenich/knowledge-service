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

    def _parse_bytes(content: bytes) -> ParsedDocument:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise _Undecodable("content is not valid UTF-8 text") from error
        if "\x00" in text:
            raise _Undecodable("text content contains a NUL character")
        return parse(text)

    return _parse_bytes


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


def _detail(message: str) -> str:
    return _clean(message)[:MAX_DETAIL_CHARACTERS].rstrip()


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


def _canonical_title(title: str, external_id: str) -> str:
    """Return a one-line NFC title, falling back to the external ID."""
    cleaned = _clean(title) or _clean(external_id) or _UNTITLED
    return cleaned[:MAX_TITLE_CHARACTERS].rstrip()
