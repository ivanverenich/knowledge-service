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
