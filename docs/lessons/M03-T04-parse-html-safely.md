# M03-T04 — Parse HTML safely

Source task: [M03-T04](../curriculum/milestones/M03-local-ingestion/M03-T04-parse-html-safely.md)

## What you will do

This lesson includes coding: Steps 2–3 write code, while Steps 1 and 4 do not.

In M03-T03 you turned Markdown into `StructuralBlock`s. This lesson does the same for HTML. Real HTML pages carry a lot that must **not** reach the knowledge base, so the parser has a second job: keep only meaningful, safe text.

| Requirement | How this lesson meets it |
|---|---|
| Headings, text, links, tables | The same `StructuralBlock` kinds as Markdown: heading, paragraph, list item, code, table. |
| Remove scripts and styles | `<script>`, `<style>`, `<noscript>`, `<template>`, `<iframe>`, `<object>`, `<embed>`, `<svg>`, `<math>`, and `<canvas>` are dropped with everything inside them. |
| Remove navigation noise | `<head>`, `<nav>`, `<aside>`, `<footer>`, `<form>`, `<button>`, `<select>`, elements with `hidden` or `aria-hidden="true"`, and navigation-type `role`s are dropped too. |
| Remove unsafe markup | The output is plain text, so attributes such as `onclick` vanish. Only `http`, `https`, `mailto`, and relative link targets become `Link`s. |
| Preserved provenance | Every block's `start` and `end` index the source HTML, so `text[start:end]` is the HTML it came from. |
| Deterministic text | Whitespace is collapsed the same way every time, and golden files lock the output. |

Some terms used in this lesson:

- **Executable content:** anything a browser could run, such as a script, an event-handler attribute like `onclick`, or a `javascript:` link. None of it may reach the index, because later answers could quote it to users.
- **Implied end tag:** HTML lets you skip some closing tags. `<p>one<p>two` is two paragraphs, because a new `<p>` ends the previous one.

## Before you begin

- **No new dependency.** Python's built-in `html.parser.HTMLParser` reads HTML as a stream of events: a start tag, some text, an end tag. Its `getpos()` gives the line and column of every event, which is what makes source offsets possible. Common third-party HTML libraries build a tree and lose those positions.
- **What `HTMLParser` already handles:** it decodes entities (`&amp;` → `&`, and `&#x09;` in attributes → a tab). It treats `<script>` and `<style>` content as raw text, so a `"</p>"` inside a script string is not mistaken for a tag. It never raises on broken HTML.
- **`structure.py` already has everything this parser returns:** `StructuralBlock`, `ParsedDocument`, `DocumentParseError`, and `DocumentTooLarge`.
- **Files you will touch:**
  - new `src/knowledge_service/html_parser.py`
  - new `tests/test_html_parser.py`
  - new fixtures in `tests/fixtures/html/`
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone.
- **Not wired into `DirectorySource` yet.** That is M03-T06's job. See the note in M03-T03's lesson.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your prediction

**Invariant:** the same HTML always produces the same blocks, in source order. For every block, `parsed.text[start:end]` is HTML from the source that contains the block's text. No block text and no link target can execute: no script or style content, no event handlers, and no `javascript:`, `vbscript:`, or `data:` targets. Malformed HTML never crashes the parser. The only typed failures are input that is too large or nested too deeply.

**Action:** create `docs/evidence/M03-T04-html-parsing.md`:

```markdown
# M03-T04 — HTML parsing evidence

Completed: pending

## Invariant

Pending.

## Prediction

Before writing code, say what you expect for each part of the hostile fixture:
an `onclick` attribute, an inline `<script>` inside a paragraph, a
`javascript:` link, a `java&#x09;script:` link, a `data:` link, a `#top` link,
an `<iframe>`, an `<svg>` with a script, `hidden` and `aria-hidden` elements,
a `<noscript>`, a comment, an unclosed `<p>` and `<li>`, and an empty table.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and a prediction for each hostile case.

### Step 2 — Coding step: write the HTML parser

Turn `HTMLParser` events into blocks.

| Part | What it does |
|---|---|
| `parse_html` | Normalizes line endings, refuses input over `max_characters`, runs the parser, and returns a `ParsedDocument`. |
| `_BlockParser._stack` | The open elements, each with the offset of its start tag. A block's `start` is its element's start tag. |
| `_skip_from` | Set when a dropped element opens. Everything is ignored until that element closes, including nested tags. An end tag for an element opened *outside* the dropped one is ignored while skipping, so it cannot end the skip early. |
| `_buffer` | The text and links collected for the block being built. |
| `_flush` | Turns the buffer into a block when a block boundary is reached. `_kind` picks heading, code, list item, or paragraph from the innermost open element. |
| `_close_implied` | Applies HTML's implied end tags: a new block ends an open `<p>`, and a new `<li>` ends the previous item in the same list. |
| `_pop_until` | Closes elements down to a matching end tag. This also handles mis-nested tags such as `<b><i></b></i>`. |
| `_Table` | Collects cells into rows, joined by ` | `. Empty rows and empty tables are dropped. |
| `_safe_target` | Keeps `http`, `https`, `mailto`, and relative targets. It refuses everything else, plus `#fragment` links that point inside the same page. |

**Decisions:**

- **Drop whole subtrees, not just tags.** For `<script>` the content is the danger, and for `<nav>` the content is the noise.
- **`<script/>` is a start tag.** `HTMLParser` reports `<x/>` as a self-closing tag, but browsers ignore the `/` on anything except void elements such as `<br>`. `handle_startendtag` therefore treats `<script/>` and `<div hidden/>` as ordinary start tags, or their content would leak.
- **Attribute values are compared case-insensitively.** `aria-hidden="TRUE"` and `role="NAVIGATION"` are dropped too.
- **Do not split a paragraph on inline noise.** `Inline <script>…</script>script is removed.` stays one paragraph: the script is skipped without ending the block.
- **Check the scheme after decoding and trimming.** `HTMLParser` has already turned `&#x09;` into a tab. `urlsplit` removes tabs and newlines before reading the scheme, and the scheme is lowercased, so `java&#x09;script:` and `JaVaScRiPt:` are both refused.
- **Nesting limit of 256.** Each tag consults the open-element stack. Without a limit, 40,000 nested `<div>`s took 13 seconds, and the time grows with the square of the depth. With the limit, the slowest input measured, 120,000 paragraphs inside 255 nested `<div>`s, takes about 6 seconds for 1,000,000 characters. Real pages nest a few dozen levels deep. Deeper input raises `DocumentParseError`, which M03-T06 can quarantine.
- **A block's offsets** run from its element's start tag to the end of its closing tag. If HTML ends a block implicitly, it runs to where the next element starts. Trailing whitespace is trimmed.

**Action:** create `src/knowledge_service/html_parser.py`:

```python
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
```

Some names in this file come from places that aren't obvious:

- **`getpos()`:** returns `(line, column)`, with lines counted from 1. `_position` turns that into a character offset using `_line_starts`.
- **`_end_of_tag`:** finds the `>` that ends the current end tag, so a block's `end` includes its `</p>`.
- **`# fmt: skip`:** keeps the long tag sets packed onto a few lines instead of one name per line.
- **`field(default_factory=list[str])`:** gives every `_Buffer` its own fresh list. A shared default list would leak text between blocks.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 3 — Coding step: fixtures and tests

Prove the output on a realistic page and on a hostile one.

| Test | Proves |
|---|---|
| `test_blocks_match_the_golden_file[representative]` | `<head>`, `<nav>`, and `<footer>` are dropped, while headings, an entity, a link, nested lists, `bash` code, a table, and Ukrainian text are kept. |
| `test_blocks_match_the_golden_file[hostile]` | The exact output for every hostile case. |
| `test_no_executable_content_survives` | No `alert(`, no `<`, and only safe link targets anywhere in the hostile output. |
| `test_every_block_points_back_at_its_source` | Every block's source slice is non-empty and contains its first word. |
| `test_offsets_span_the_whole_element` | `text[start:end]` is exactly `<h1>Title</h1>` and the full `<p>…</p>`. |
| `test_only_safe_link_targets_become_links` (9 cases) | `https`, `mailto`, and relative targets are kept; `javascript` in any case or with a tab, `vbscript`, `data`, and `#top` are refused. |
| `test_windows_line_endings_give_the_same_blocks` | `\r\n` input gives identical blocks. |
| `test_content_over_the_limit_raises_a_typed_error` | `DocumentTooLarge`. |
| `test_nesting_beyond_the_limit_raises_a_typed_error` | `DocumentParseError` beyond 256 levels, and no error at 255. |
| `test_hidden_content_stays_hidden` (4 cases) | `<script/>`, `<style/>`, and `<div hidden/>` still hide their content, and so does `aria-hidden="TRUE"`. |
| `test_an_outer_end_tag_does_not_end_a_dropped_element` (2 cases) | `</a>` or `</b>` inside a dropped `<nav>` or hidden `<div>` cannot end the skip. |
| `test_a_code_language_only_applies_inside_pre` | A `language-…` class on inline `<code>` does not leak into a later code block. |
| `test_void_elements_break_lines_add_alt_text_and_split_blocks` | `<br>`, `<img alt>`, and `<hr>`. |
| `test_unclosed_paragraphs_and_items_close_where_html_says` | Implied end tags. |

**Decision:** the golden test now **skips** after rewriting its files when `UPDATE_GOLDEN=1` is set. An update run can never pass silently. This applies the advice from the M03-T03 review.

**Action:** create `tests/fixtures/html/representative.html`:

````html
<!DOCTYPE html>
<html lang="en">
<head>
  <title>Onboarding guide</title>
  <style>body { color: red; }</style>
  <script>window.analytics = true;</script>
</head>
<body>
  <nav><a href="/">Home</a> | <a href="/about">About</a></nav>
  <main>
    <article>
      <h1>Onboarding guide</h1>
      <p>Read the <a href="https://example.com/security">security policy</a>
         before your first deploy. Tom &amp; Jerry use <code>make check</code>.</p>
      <h2>Steps</h2>
      <ol>
        <li>Clone the repository.</li>
        <li>Install the tools:
          <ul>
            <li>uv</li>
            <li>PostgreSQL</li>
          </ul>
        </li>
      </ol>
      <pre><code class="language-bash">make setup
make check</code></pre>
      <table>
        <tr><th>Tool</th><th>Version</th></tr>
        <tr><td>uv</td><td>0.9</td></tr>
        <tr><td><a href="https://www.python.org">Python</a></td><td>3.14</td></tr>
      </table>
      <h3>Посилання</h3>
      <div>Текст українською.</div>
    </article>
  </main>
  <footer>© Example Corp. <a href="/privacy">Privacy</a></footer>
</body>
</html>
````

Create `tests/fixtures/html/hostile.html`:

````html
<div onclick="steal()">Click handlers are dropped with the attribute.</div>
<p>Inline <script>alert("inline")</script>script is removed.</p>
<p><a href="javascript:alert(1)">script link</a> and
<a href="java&#x09;script:alert(2)">encoded link</a> and
<a href="data:text/html,<b>x</b>">data link</a> keep only their text.</p>
<p><a href="#top">Back to top</a> is not a link target.</p>
<iframe src="https://evil.example"><p>iframe fallback</p></iframe>
<svg><script>alert("svg")</script><text>svg text</text></svg>
<div hidden>Hidden text</div>
<div aria-hidden="true">Decorative text</div>
<div role="navigation">Menu text</div>
<noscript>Enable JavaScript</noscript>
<!-- a comment with <p>markup</p> -->
<p>Unclosed paragraph
<p>Second paragraph with <b>bold <i>nesting</b></i> mistakes
<ul><li>open item<li>another item</ul>
<table><tr><td></td><td></td></tr></table>
````

Create `tests/test_html_parser.py`:

````python
"""Tests for the HTML parser: blocks, provenance, and no executable content."""

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from knowledge_service.html_parser import MAX_DEPTH, parse_html
from knowledge_service.structure import (
    BlockKind,
    DocumentParseError,
    DocumentTooLarge,
    ParsedDocument,
)

FIXTURES = Path(__file__).parent / "fixtures" / "html"


def as_json(parsed: ParsedDocument) -> list[object]:
    """Turn blocks into the plain JSON shape stored in the golden files."""
    return json.loads(json.dumps([asdict(block) for block in parsed.blocks]))


def parse_fixture(name: str) -> ParsedDocument:
    return parse_html((FIXTURES / f"{name}.html").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["representative", "hostile"])
def test_blocks_match_the_golden_file(name: str) -> None:
    golden = FIXTURES / f"{name}.blocks.json"
    actual = as_json(parse_fixture(name))

    if os.environ.get("UPDATE_GOLDEN") == "1":
        golden.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        pytest.skip("golden file rewritten; review the diff, then rerun")
    assert actual == json.loads(golden.read_text(encoding="utf-8"))


def test_no_executable_content_survives() -> None:
    parsed = parse_fixture("hostile")

    for block in parsed.blocks:
        assert "alert(" not in block.text
        assert "<" not in block.text
        for link in block.links:
            assert link.target.startswith(("https:", "http:", "mailto:", "/"))


@pytest.mark.parametrize("name", ["representative", "hostile"])
def test_every_block_points_back_at_its_source(name: str) -> None:
    parsed = parse_fixture(name)

    for block in parsed.blocks:
        source = parsed.text[block.start : block.end]
        assert source
        assert block.text.split()[0] in source


def test_offsets_span_the_whole_element() -> None:
    parsed = parse_html("<h1>Title</h1>\n<p>Some <b>text</b>.</p>\n")

    heading, paragraph = parsed.blocks

    assert parsed.text[heading.start : heading.end] == "<h1>Title</h1>"
    assert parsed.text[paragraph.start : paragraph.end] == "<p>Some <b>text</b>.</p>"


@pytest.mark.parametrize(
    ("href", "kept"),
    [
        ("https://example.com/a", True),
        ("mailto:team@example.com", True),
        ("/docs/start", True),
        ("javascript:alert(1)", False),
        ("  JaVaScRiPt:alert(1)", False),
        ("java\tscript:alert(1)", False),
        ("vbscript:msgbox(1)", False),
        ("data:text/html,<b>x</b>", False),
        ("#top", False),
    ],
)
def test_only_safe_link_targets_become_links(href: str, kept: bool) -> None:
    parsed = parse_html(f'<p><a href="{href}">label</a></p>')

    [paragraph] = parsed.blocks
    assert paragraph.text == "label"
    assert bool(paragraph.links) is kept


def test_windows_line_endings_give_the_same_blocks() -> None:
    unix = parse_html("<h1>Title</h1>\n<p>Body line\nnext line</p>\n")
    windows = parse_html("<h1>Title</h1>\r\n<p>Body line\r\nnext line</p>\r\n")

    assert windows == unix


def test_content_over_the_limit_raises_a_typed_error() -> None:
    with pytest.raises(DocumentTooLarge, match="limit is 10"):
        parse_html("<p>" + "x" * 20 + "</p>", max_characters=10)


def test_nesting_beyond_the_limit_raises_a_typed_error() -> None:
    parse_html("<div>" * (MAX_DEPTH - 1) + "x")

    with pytest.raises(DocumentParseError, match="nests deeper"):
        parse_html("<div>" * (MAX_DEPTH + 1) + "x")


@pytest.mark.parametrize(
    "source",
    [
        "<script/>alert(1)</script><p>ok</p>",
        "<style/>body { color: red }</style><p>ok</p>",
        "<div hidden/>secret</div><p>ok</p>",
        '<div aria-hidden="TRUE">secret</div><p>ok</p>',
    ],
)
def test_hidden_content_stays_hidden(source: str) -> None:
    assert [block.text for block in parse_html(source).blocks] == ["ok"]


@pytest.mark.parametrize(
    "source",
    [
        '<p><a href="https://example.com">one<nav>x</a>two</nav>three</p>',
        "<p><b>one<div hidden>x</b>two</div>three</p>",
    ],
)
def test_an_outer_end_tag_does_not_end_a_dropped_element(source: str) -> None:
    [paragraph] = parse_html(source).blocks

    assert paragraph.text == "onethree"


def test_a_code_language_only_applies_inside_pre() -> None:
    parsed = parse_html('<p><code class="language-py">x</code></p><pre>y</pre>')

    assert [(block.kind, block.language) for block in parsed.blocks] == [
        (BlockKind.PARAGRAPH, None),
        (BlockKind.CODE, None),
    ]


def test_void_elements_break_lines_add_alt_text_and_split_blocks() -> None:
    parsed = parse_html('<div>a<br>b <img alt="diagram" src="x.png"><hr>c</div>')

    assert [block.text for block in parsed.blocks] == ["a b diagram", "c"]


def test_unclosed_paragraphs_and_items_close_where_html_says() -> None:
    parsed = parse_html("<p>one<p>two<ul><li>a<li>b</ul>")

    assert [(block.kind, block.text) for block in parsed.blocks] == [
        (BlockKind.PARAGRAPH, "one"),
        (BlockKind.PARAGRAPH, "two"),
        (BlockKind.LIST_ITEM, "a"),
        (BlockKind.LIST_ITEM, "b"),
    ]
````

Generate the golden files once. You should see `25 passed, 2 skipped`:

```bash
UPDATE_GOLDEN=1 UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_html_parser.py -q
```

Then open both `*.blocks.json` files and check them against this table:

| Fixture | Expected blocks |
|---|---|
| `representative` | 11 blocks: h1 "Onboarding guide"; a paragraph containing "Tom & Jerry" and "make check" with one link to `https://example.com/security`; h2 "Steps"; list items "Clone the repository." and "Install the tools:" at depth 1, then "uv" and "PostgreSQL" at depth 2; `bash` code "make setup\nmake check"; a table "Tool \| Version / uv \| 0.9 / Python \| 3.14" with one link; h3 "Посилання"; and the paragraph "Текст українською.". There is no "Home", "About", "Privacy", "©", `<title>`, CSS, or `analytics` anywhere. |
| `hostile` | 8 blocks: "Click handlers are dropped with the attribute."; "Inline script is removed." as one paragraph; "script link and encoded link and data link keep only their text." with no links; "Back to top is not a link target." with no links; "Unclosed paragraph"; "Second paragraph with bold nesting mistakes"; and list items "open item" and "another item". There is no iframe, SVG, hidden, decorative, menu, noscript, or comment text, and no table. |

**Check:** run it without `UPDATE_GOLDEN`:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_html_parser.py -v
```

All 27 tests pass.

### Step 4 — No coding in this step: run the gates, then record evidence

**Action:** run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_html_parser.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. Answer these questions:

1. Code that indexes these blocks never sees HTML. Name three kinds of content in a real web page that it no longer has to worry about, and say where in `html_parser.py` each is removed.
2. Suppose the parser removed only `<script>` and `<style>` *tags* but kept the text inside them. Using the hostile fixture, what would end up in the index, and why is that worse than losing a little real text?
3. Suppose `test_only_safe_link_targets_become_links` were deleted, and someone simplified `_safe_target` to check `href.startswith("javascript:")`. Which hostile link would then become a real `Link`, and what could happen when M03-T11 shows that link to a user in a citation?

**Check:** every command passes, and the evidence explains the design in your words.

## Completion checklist

- [ ] `src/knowledge_service/html_parser.py` defines `parse_html`, `MAX_CHARACTERS`, and `MAX_DEPTH`, and returns `ParsedDocument`s.
- [ ] Scripts, styles, embedded content, navigation, footers, forms, and hidden elements are dropped with their content.
- [ ] Only `http`, `https`, `mailto`, and relative links become `Link`s.
- [ ] Every block's `text[start:end]` points back at its source HTML.
- [ ] Too-large input raises `DocumentTooLarge`, and nesting beyond 256 levels raises `DocumentParseError`.
- [ ] Both fixtures have reviewed `*.blocks.json` golden files, and all 27 tests pass without `UPDATE_GOLDEN`.
- [ ] Format, lint, type checks, the deterministic suite, `make check`, and `make docs-check` all pass.
- [ ] The evidence file holds your invariant, prediction, design, verification, and reflection.

When you're done, share your code or any error you hit, and I'll review it.
