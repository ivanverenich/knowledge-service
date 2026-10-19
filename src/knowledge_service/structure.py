"""Format-neutral structural blocks that parsers extract from a document's text.

Every block records where it came from: `start` and `end` are character
offsets into the parsed text, so `text[start:end]` is the block's source.
"""

from dataclasses import dataclass
from enum import StrEnum


class InvalidBlock(ValueError):
    """A structural block violates an invariant."""


class DocumentParseError(Exception):
    """Base error for content a parser cannot turn into blocks."""


class DocumentTooLarge(DocumentParseError):
    """The content is longer than the parser accepts."""


class EncryptedDocument(DocumentParseError):
    """The content needs a password to read."""


class CorruptDocument(DocumentParseError):
    """The bytes are not a readable document of the expected format."""


class NoTextLayer(DocumentParseError):
    """The document has no extractable text, and OCR is not supported."""


class BlockKind(StrEnum):
    """The structural role of one block."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    CODE = "code"
    TABLE = "table"


@dataclass(frozen=True, slots=True)
class Link:
    """A link's visible text and its target."""

    text: str
    target: str

    def __post_init__(self) -> None:
        if not self.target.strip():
            raise InvalidBlock("link target must not be empty")


@dataclass(frozen=True, slots=True)
class StructuralBlock:
    """One heading, paragraph, list item, code block, or table."""

    kind: BlockKind
    text: str
    start: int
    end: int
    heading_level: int | None = None
    list_depth: int | None = None
    language: str | None = None
    links: tuple[Link, ...] = ()
    page: int | None = None

    def __post_init__(self) -> None:
        if self.page is not None and self.page < 1:
            raise InvalidBlock("page numbers start at 1")
        if self.start < 0 or self.end < self.start:
            raise InvalidBlock("block offsets must satisfy 0 <= start <= end")
        if self.kind is not BlockKind.CODE and not self.text.strip():
            raise InvalidBlock("only a code block may have empty text")
        if (self.kind is BlockKind.HEADING) != (self.heading_level is not None):
            raise InvalidBlock("exactly the heading blocks carry a heading level")
        if self.heading_level is not None and not 1 <= self.heading_level <= 6:
            raise InvalidBlock("heading level must be between 1 and 6")
        if (self.kind is BlockKind.LIST_ITEM) != (self.list_depth is not None):
            raise InvalidBlock("exactly the list items carry a list depth")
        if self.list_depth is not None and self.list_depth < 1:
            raise InvalidBlock("list depth must be at least 1")
        if self.language is not None and self.kind is not BlockKind.CODE:
            raise InvalidBlock("only a code block carries a language")


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """The text that block offsets index into, and the blocks in source order."""

    text: str
    blocks: tuple[StructuralBlock, ...]

    def __post_init__(self) -> None:
        for block in self.blocks:
            if block.end > len(self.text):
                raise InvalidBlock("a block must end inside the parsed text")
