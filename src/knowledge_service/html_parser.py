"""Parse HTML into structural blocks, keeping only safe, meaningful text.

Scripts, styles, embedded content, navigation, and hidden elements are dropped
with everything inside them. What remains becomes headings, paragraphs, list
items, code, and tables, each with the source offsets of the HTML it came from.
Only `http`, `https`, `mailto`, and relative link targets survive.

Elements may nest at most `MAX_DEPTH` deep. Real pages stay far below it, and
the limit keeps parsing time linear in the size of the input.
"""

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlsplit

from knowledge_service.structure import (
    BlockKind,
    DocumentParseError,
    DocumentTooLarge,
    Link,
    ParsedDocument,
    StructuralBlock,
)

MAX_CHARACTERS = 1_000_000
MAX_DEPTH = 256

_DROPPED = frozenset(
    {
        "head", "script", "style", "noscript", "template", "iframe", "object",
        "embed", "svg", "math", "canvas", "nav", "aside", "footer", "form",
        "button", "select",
    }
)  # fmt: skip
_NOISE_ROLES = frozenset(
    {"navigation", "banner", "contentinfo", "complementary", "search"}
)
_VOID = frozenset(
    {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "source", "track", "wbr",
    }
)  # fmt: skip
_BLOCKS = frozenset(
    {
        "address", "article", "blockquote", "body", "dd", "div", "dl", "dt",
        "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "header",
        "hr", "html", "li", "main", "ol", "p", "pre", "section", "table", "ul",
    }
)  # fmt: skip
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_SAFE_SCHEMES = frozenset({"http", "https", "mailto"})
_WHITESPACE = re.compile(r"\s+")


def parse_html(text: str, *, max_characters: int = MAX_CHARACTERS) -> ParsedDocument:
    """Return the meaningful blocks of an HTML document in source order."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(normalized) > max_characters:
        raise DocumentTooLarge(
            f"html has {len(normalized)} characters; the limit is {max_characters}"
        )
    parser = _BlockParser(normalized)
    parser.feed(normalized)
    parser.close()
    parser.finish()
    return ParsedDocument(text=normalized, blocks=tuple(parser.blocks))


@dataclass
class _Buffer:
    """Text and links collected for the block being built."""

    start: int
    parts: list[str] = field(default_factory=list[str])
    links: list[Link] = field(default_factory=list[Link])


@dataclass
class _Table:
    start: int
    rows: list[str] = field(default_factory=list[str])
    cells: list[str] = field(default_factory=list[str])
    cell: _Buffer | None = None
    links: list[Link] = field(default_factory=list[Link])


class _BlockParser(HTMLParser):
    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self._text = text
        self._line_starts = [0] + [
            index + 1 for index, character in enumerate(text) if character == "\n"
        ]
        self.blocks: list[StructuralBlock] = []
        self._stack: list[tuple[str, int]] = []
        self._skip_from: int | None = None
        self._buffer: _Buffer | None = None
        self._table: _Table | None = None
        self._link_target: str | None = None
        self._link_parts: list[str] = []
        self._code_language: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID:
            self.handle_startendtag(tag, attrs)
            return
        position = self._position()
        if len(self._stack) >= MAX_DEPTH:
            raise DocumentParseError(f"html nests deeper than {MAX_DEPTH} elements")
        if self._skip_from is not None:
            self._stack.append((tag, position))
            return
        if _is_noise(tag, dict(attrs)):
            self._stack.append((tag, position))
            self._skip_from = len(self._stack)
            return
        self._close_implied(tag, position)
        if self._table is not None:
            self._stack.append((tag, position))
            if tag in {"td", "th"}:
                self._end_cell()
                self._table.cell = _Buffer(start=position)
            elif tag == "tr":
                self._end_row()
            elif tag == "a":
                self._open_link(dict(attrs))
            return
        if tag in _BLOCKS:
            self._flush(position)
        self._stack.append((tag, position))
        if tag == "table":
            self._table = _Table(start=position)
        elif tag == "pre" or (tag == "code" and "pre" in self._open_tags()):
            language = _code_language(dict(attrs).get("class"))
            self._code_language = language or self._code_language
        elif tag == "a":
            self._open_link(dict(attrs))

    def _close_implied(self, tag: str, position: int) -> None:
        """Close the elements HTML ends implicitly when `tag` starts.

        A new block ends an open paragraph, and a new list item ends the
        previous item of the same list.
        """
        open_tags = self._open_tags()
        if tag in _BLOCKS and open_tags and open_tags[-1] == "p":
            self._pop_until("p", position)
        if tag == "li":
            for name in reversed(open_tags):
                if name in {"ul", "ol"}:
                    break
                if name == "li":
                    self._pop_until("li", position)
                    break

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _VOID:
            # Browsers ignore the "/" in <script/> or <div/>: it is a start tag.
            self.handle_starttag(tag, attrs)
            return
        if self._skip_from is not None:
            return
        if tag == "br":
            self._append("\n")
        elif tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self._append(f" {alt} ")
        elif tag == "hr" and self._table is None:
            self._flush(self._position())

    def handle_endtag(self, tag: str) -> None:
        open_tags = self._open_tags()
        if tag not in open_tags:
            return
        innermost = len(open_tags) - 1 - open_tags[::-1].index(tag)
        if self._skip_from is not None and innermost < self._skip_from - 1:
            # The tag was opened outside the dropped element; closing it here
            # would end the skip early and leak the dropped text.
            return
        self._pop_until(tag, self._position(), end=self._end_of_tag())

    def _pop_until(self, tag: str, position: int, *, end: int | None = None) -> None:
        """Close open elements down to and including the innermost `tag`.

        Elements closed on the way end where the closing event starts; `tag`
        itself ends at `end` when its own closing tag is present.
        """
        while self._stack:
            open_tag, start = self._stack.pop()
            closes_here = open_tag == tag
            if self._skip_from is not None:
                if len(self._stack) < self._skip_from:
                    self._skip_from = None
            else:
                block_end = end if closes_here and end is not None else position
                self._close(open_tag, start, block_end)
            if closes_here:
                return

    def handle_data(self, data: str) -> None:
        if self._skip_from is None and data:
            self._append(data)

    def finish(self) -> None:
        """Close whatever the document left open."""
        end = len(self._text)
        while self._stack:
            open_tag, start = self._stack.pop()
            if self._skip_from is not None:
                if len(self._stack) < self._skip_from:
                    self._skip_from = None
                continue
            self._close(open_tag, start, end)
        self._flush(end)

    def _close(self, tag: str, start: int, end: int) -> None:
        if tag == "a":
            self._close_link()
        if self._table is not None:
            if tag in {"td", "th"}:
                self._end_cell()
            elif tag == "tr":
                self._end_row()
            elif tag == "table" and "table" not in self._open_tags():
                self._end_table(end)
            return
        if tag in _BLOCKS:
            self._flush(end, closing=(tag, start))
        if tag == "pre":
            self._code_language = None

    def _append(self, piece: str) -> None:
        if self._table is not None:
            if self._table.cell is None:
                self._table.cell = _Buffer(start=self._position())
            self._table.cell.parts.append(piece)
        else:
            if self._buffer is None:
                self._buffer = _Buffer(start=self._position())
            self._buffer.parts.append(piece)
        if self._link_target is not None:
            self._link_parts.append(piece)

    def _open_link(self, attrs: dict[str, str | None]) -> None:
        self._close_link()
        self._link_target = _safe_target(attrs.get("href"))
        self._link_parts = []

    def _close_link(self) -> None:
        target = self._link_target
        self._link_target = None
        if target is None:
            return
        text = _collapse("".join(self._link_parts))
        links = self._table.links if self._table is not None else None
        if links is None:
            if self._buffer is None:
                return
            links = self._buffer.links
        links.append(Link(text=text, target=target))

    def _flush(self, end: int, *, closing: tuple[str, int] | None = None) -> None:
        """Emit the collected text as one block ending at `end`.

        `closing` is the element being closed, when a closing tag caused the
        flush; the block then starts at that element's opening tag.
        """
        buffer, self._buffer = self._buffer, None
        if buffer is None:
            return
        open_tags = [*self._stack, closing] if closing else self._stack
        kind, heading_level = _kind(open_tags)
        raw = "".join(buffer.parts)
        text = raw.strip("\n") if kind is BlockKind.CODE else _collapse(raw)
        if not text.strip():
            return
        start = min(buffer.start, closing[1]) if closing else buffer.start
        list_depth = sum(1 for tag, _ in open_tags if tag in {"ul", "ol"})
        self.blocks.append(
            StructuralBlock(
                kind=kind,
                text=text,
                start=start,
                end=self._trim(start, end),
                heading_level=heading_level,
                list_depth=list_depth if kind is BlockKind.LIST_ITEM else None,
                language=self._code_language if kind is BlockKind.CODE else None,
                links=tuple(buffer.links),
            )
        )

    def _open_tags(self) -> list[str]:
        return [tag for tag, _ in self._stack]

    def _end_cell(self) -> None:
        table = self._table
        if table is None or table.cell is None:
            return
        table.cells.append(_collapse("".join(table.cell.parts)))
        table.cell = None

    def _end_row(self) -> None:
        table = self._table
        if table is None:
            return
        self._end_cell()
        if any(table.cells):
            table.rows.append(" | ".join(table.cells))
        table.cells = []

    def _end_table(self, end: int) -> None:
        self._end_row()
        table, self._table = self._table, None
        if table is None or not table.rows:
            return
        self.blocks.append(
            StructuralBlock(
                kind=BlockKind.TABLE,
                text="\n".join(table.rows),
                start=table.start,
                end=end,
                links=tuple(table.links),
            )
        )

    def _position(self) -> int:
        line, column = self.getpos()
        return self._line_starts[line - 1] + column

    def _end_of_tag(self) -> int:
        closing = self._text.find(">", self._position())
        return len(self._text) if closing == -1 else closing + 1

    def _trim(self, start: int, end: int) -> int:
        while end > start and self._text[end - 1].isspace():
            end -= 1
        return end


def _kind(open_tags: list[tuple[str, int]]) -> tuple[BlockKind, int | None]:
    """Return the block kind, and heading level, of the innermost open element."""
    for tag, _ in reversed(open_tags):
        if tag in _HEADINGS:
            return BlockKind.HEADING, int(tag[1])
        if tag == "pre":
            return BlockKind.CODE, None
    if any(tag in {"ul", "ol"} for tag, _ in open_tags):
        return BlockKind.LIST_ITEM, None
    return BlockKind.PARAGRAPH, None


def _is_noise(tag: str, attrs: dict[str, str | None]) -> bool:
    return (
        tag in _DROPPED
        or "hidden" in attrs
        or (attrs.get("aria-hidden") or "").strip().lower() == "true"
        or (attrs.get("role") or "").strip().lower() in _NOISE_ROLES
    )


def _safe_target(href: str | None) -> str | None:
    """Return the link target when it cannot run code, otherwise None."""
    if href is None:
        return None
    target = href.strip()
    if not target or target.startswith("#"):
        return None
    scheme = urlsplit(target).scheme.lower()
    return target if scheme in _SAFE_SCHEMES or not scheme else None


def _code_language(css_class: str | None) -> str | None:
    for name in (css_class or "").split():
        if name.startswith("language-"):
            return name.removeprefix("language-") or None
    return None


def _collapse(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()
