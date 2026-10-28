"""Parse Markdown into structural blocks that keep their source offsets.

Parsing follows CommonMark plus pipe tables. Line endings are normalized to
`\n` first, and every offset indexes that normalized text. Raw HTML is dropped:
it is neither indexed as text nor trusted as markup.
"""

from collections.abc import Iterator, Sequence

from markdown_it import MarkdownIt
from markdown_it.token import Token

from knowledge_service.ingestion.parsers.structure import (
    BlockKind,
    DocumentParseError,
    DocumentTooLarge,
    Link,
    ParsedDocument,
    StructuralBlock,
)

MAX_CHARACTERS = 1_000_000

_PARSER = MarkdownIt("commonmark").enable("table")


def parse_markdown(
    text: str, *, max_characters: int = MAX_CHARACTERS
) -> ParsedDocument:
    """Return the blocks of `text` in source order."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(normalized) > max_characters:
        raise DocumentTooLarge(
            f"markdown has {len(normalized)} characters; the limit is {max_characters}"
        )
    tokens = _PARSER.parse(normalized)
    lines = _LineOffsets(normalized)
    return ParsedDocument(text=normalized, blocks=tuple(_blocks(tokens, lines)))


class _LineOffsets:
    """Translate the parser's line numbers into character offsets."""

    def __init__(self, text: str) -> None:
        self._text = text
        self._starts = [0] + [
            index + 1 for index, character in enumerate(text) if character == "\n"
        ]

    def span(self, token: Token) -> tuple[int, int]:
        if token.map is None:
            raise DocumentParseError(f"markdown {token.type} has no source position")
        first, last = token.map
        start = self._offset(first)
        end = self._offset(last)
        while end > start and self._text[end - 1] == "\n":
            end -= 1
        return start, end

    def _offset(self, line: int) -> int:
        return self._starts[line] if line < len(self._starts) else len(self._text)


def _blocks(tokens: Sequence[Token], lines: _LineOffsets) -> Iterator[StructuralBlock]:
    list_depth = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        match token.type:
            case "bullet_list_open" | "ordered_list_open":
                list_depth += 1
            case "bullet_list_close" | "ordered_list_close":
                list_depth -= 1
            case "heading_open":
                text, links = _inline(tokens[index + 1])
                if text:
                    start, end = lines.span(token)
                    yield StructuralBlock(
                        kind=BlockKind.HEADING,
                        text=text,
                        start=start,
                        end=end,
                        heading_level=int(token.tag.removeprefix("h")),
                        links=links,
                    )
            case "paragraph_open":
                text, links = _inline(tokens[index + 1])
                if text:
                    start, end = lines.span(token)
                    yield StructuralBlock(
                        kind=BlockKind.LIST_ITEM if list_depth else BlockKind.PARAGRAPH,
                        text=text,
                        start=start,
                        end=end,
                        list_depth=list_depth or None,
                        links=links,
                    )
            case "fence" | "code_block":
                start, end = lines.span(token)
                yield StructuralBlock(
                    kind=BlockKind.CODE,
                    text=token.content.removesuffix("\n"),
                    start=start,
                    end=end,
                    language=token.info.strip() or None,
                )
            case "table_open":
                close = _closing(tokens, index, "table_close")
                text, links = _table(tokens[index:close])
                if text:
                    start, end = lines.span(token)
                    yield StructuralBlock(
                        kind=BlockKind.TABLE,
                        text=text,
                        start=start,
                        end=end,
                        links=links,
                    )
                index = close
            case _:
                pass
        index += 1


def _inline(token: Token) -> tuple[str, tuple[Link, ...]]:
    """Return the visible text of an inline token and the links inside it."""
    parts: list[str] = []
    links: list[Link] = []
    target: str | None = None
    link_parts: list[str] = []
    for child in token.children or []:
        match child.type:
            case "text" | "code_inline" | "image":
                piece = child.content
            case "softbreak":
                piece = " "
            case "hardbreak":
                piece = "\n"
            case "link_open":
                target = str(child.attrs.get("href", ""))
                link_parts = []
                continue
            case "link_close":
                if target:
                    links.append(Link(text="".join(link_parts), target=target))
                target = None
                continue
            case _:
                continue
        parts.append(piece)
        if target is not None:
            link_parts.append(piece)
    return "".join(parts).strip(), tuple(links)


def _closing(tokens: Sequence[Token], index: int, closing_type: str) -> int:
    for position in range(index, len(tokens)):
        if tokens[position].type == closing_type:
            return position
    raise DocumentParseError(f"markdown {tokens[index].type} is never closed")


def _table(tokens: Sequence[Token]) -> tuple[str, tuple[Link, ...]]:
    """Return a table as one line per row, cells separated by ` | `."""
    rows: list[str] = []
    cells: list[str] = []
    links: list[Link] = []
    for token in tokens:
        if token.type == "inline":
            text, cell_links = _inline(token)
            cells.append(text)
            links.extend(cell_links)
        elif token.type == "tr_close":
            if any(cells):
                rows.append(" | ".join(cells))
            cells = []
    return "\n".join(rows), tuple(links)
