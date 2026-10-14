# M03-T03 — Parse Markdown structure

Source task: [M03-T03](../curriculum/milestones/M03-local-ingestion/M03-T03-parse-markdown-structure.md)

## What you will do

This lesson includes coding: Steps 2–4 write code, while Steps 1 and 5 do not.

In M03-T02, `DirectorySource` returned each file as UTF-8 bytes. This lesson turns Markdown text into **structural blocks**: headings, paragraphs, list items, code blocks, and tables, each with its links and its **source offsets**.

- **Source offsets:** `start` and `end` character positions, so `text[start:end]` is exactly the Markdown that produced the block. M03-T11 uses them to show where a citation came from.
- **Golden test:** a test that compares the parser's output with an expected-output file kept in the repo. Any change in output shows up as a diff you can review.

What later tasks need from these blocks:

| Later task | Needs |
|---|---|
| M03-T04 (HTML) | The same block type, so HTML and Markdown look alike downstream. |
| M03-T06 (normalization) | One function to call, plus a typed failure it can turn into a quarantine result. |
| M03-T10 (structure-aware chunking) | Block kinds, heading levels, and boundaries. |
| M03-T11 (tables, links, locations) | Table text, link targets, and offsets. |

## Before you begin

- **New dependency:** `markdown-it-py`, a Python port of the reference CommonMark parser. **CommonMark** is the precise Markdown specification, so "what counts as a heading" is decided by the spec and not by our own regular expressions. Version 4.2.0 ships type hints, so Pyright strict accepts it.
- **How `markdown-it-py` works:** it returns a flat list of **tokens**. A block such as a paragraph is a `paragraph_open` token, then an `inline` token holding the text, then `paragraph_close`. Block tokens carry `map = [first_line, last_line)`, a line range. This lesson turns those line numbers into character offsets.
- **Files you will touch:**
  - `pyproject.toml` and `uv.lock` (through `uv add`)
  - new `src/knowledge_service/structure.py`
  - new `src/knowledge_service/markdown_parser.py`
  - new `tests/test_markdown_parser.py`
  - new fixtures in `tests/fixtures/markdown/`
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone.
- **Not wired into `DirectorySource`:** this lesson builds only the parser. Choosing a parser per media type is M03-T06's job.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your prediction

Write down what the parser must keep true.

**Invariant:** the same Markdown text always produces the same blocks, in source order. For every block, `parsed.text[block.start:block.end]` is the Markdown that produced it. Parsing never fails on malformed Markdown: CommonMark gives every input a meaning, so bad syntax becomes literal text, not an error. The only typed failure is input that is too large to parse safely.

**Action:** create `docs/evidence/M03-T03-markdown-structure.md`:

```markdown
# M03-T03 — Markdown structure evidence

Completed: pending

## Invariant

Pending.

## Prediction

Before writing code, say what you expect for: `#No space`, an unclosed link,
a raw `<script>` block, a table row with too few or too many cells, a
`javascript:` link, and a code fence that is never closed.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and six predictions.

### Step 2 — Coding step: add the dependency and the block model

Define the format-neutral blocks every parser will return.

| Name | What it holds |
|---|---|
| `BlockKind` | `heading`, `paragraph`, `list_item`, `code`, `table`. |
| `Link` | Visible `text` and a non-empty `target`. |
| `StructuralBlock` | `kind`, `text`, `start`, `end`, and optional `heading_level` (1–6, headings only), `list_depth` (1 or more, list items only), `language` (code only), and `links`. |
| `ParsedDocument` | The normalized `text` the offsets index into, plus the blocks in source order. |
| `InvalidBlock` | A block that breaks one of those rules: a programming error. |
| `DocumentParseError`, `DocumentTooLarge` | Typed failures for content a parser refuses. |

**Decisions:**

- **The model lives in `structure.py`, not in the Markdown module**, so the HTML (M03-T04) and PDF (M03-T05) parsers return the same type.
- **Separate `heading_level` and `list_depth` fields.** One shared `level` field would mean two different things.
- **A list is not a block. Each item is,** with its nesting depth. Chunking (M03-T10) then never has to split one list item across two blocks.

**Action:** add the dependency:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv add "markdown-it-py>=4,<5"
```

Then create `src/knowledge_service/structure.py`:

```python
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

    def __post_init__(self) -> None:
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
```

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python -c "import markdown_it; from knowledge_service.structure import StructuralBlock"` succeeds.

### Step 3 — Coding step: write the Markdown parser

Turn the token list into blocks with offsets.

| Part | What it does |
|---|---|
| `parse_markdown` | Normalizes `\r\n` and `\r` to `\n`, refuses input over `max_characters`, parses, and returns a `ParsedDocument`. |
| `_LineOffsets` | Records where each line starts, then turns a token's line range into `(start, end)` character offsets, trimming trailing newlines. |
| `_blocks` | Walks the tokens. Tracks list depth, and emits a block per heading, paragraph (a list item when inside a list), code block, and table. |
| `_inline` | Builds the visible text of one line of content. It collects link text and targets, and drops formatting markers and raw HTML. Link text is gathered in its own list, so the work grows linearly with the number of links. |
| `_table` | Writes one line per row, with cells joined by ` | `. Rows whose cells are all empty are dropped, and a table with no text left produces no block. |

**Decisions:**

- **Preset:** `MarkdownIt("commonmark").enable("table")`. Pipe tables are a GitHub extension, not core CommonMark, so they are switched on explicitly.
- **Offsets index the normalized text.** `markdown-it-py` treats `\r\n` as one line break, so normalizing first keeps its line numbers and our character offsets in step. That is why `ParsedDocument` returns the text the offsets refer to.
- **Raw HTML is dropped.** `html_block` tokens are ignored, so a `<script>` block never becomes text. Inline HTML tags are dropped too, but text between them, such as `alert(1)` in `Hi <script>alert(1)</script>`, stays as plain text. Sanitizing HTML is M03-T04's job.
- **Unsafe links stay text.** `markdown-it-py` refuses `javascript:`, `vbscript:`, `file:` and most `data:` URLs, so they are never turned into links. We keep that default. The fixture uses a reference-style link (`[text][name]` plus a `[name]: target` line) for this case. The repo's docs checker reads every inline link in `docs/` (square brackets followed by parentheses) as a file link, and this guide shows the fixture in full.
- **Size limit:** 1,000,000 characters by default. Huge input could tie up a worker, so it raises `DocumentTooLarge`, which M03-T06 can turn into a quarantine result.
- **Empty blocks are skipped,** for example a bare `#`. Code blocks are the exception, because empty code is still code.

**Action:** create `src/knowledge_service/markdown_parser.py`:

```python
"""Parse Markdown into structural blocks that keep their source offsets.

Parsing follows CommonMark plus pipe tables. Line endings are normalized to
`\n` first, and every offset indexes that normalized text. Raw HTML is dropped:
it is neither indexed as text nor trusted as markup.
"""

from collections.abc import Iterator, Sequence

from markdown_it import MarkdownIt
from markdown_it.token import Token

from knowledge_service.structure import (
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
```

Some names in this file come from places that aren't obvious:

- **`token.map`:** the `[first_line, last_line)` pair from `markdown-it-py`. It is `None` only for tokens that are not blocks, so `span` raises `DocumentParseError` if it is ever missing on a block.
- **`tokens[index + 1]`:** after `heading_open` or `paragraph_open`, the next token is always the `inline` token with the text.
- **`token.tag`:** `"h1"` to `"h6"` for headings, so `removeprefix("h")` gives the level.
- **`token.info`:** the language after a code fence, such as `bash`.
- **`child.content`:** for an image, its alt text.
- **`index = close`:** skips over the table's inner tokens after the table is handled, so its cells are not emitted again as paragraphs.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 4 — Coding step: golden fixtures and tests

Prove the output on realistic and on malformed Markdown.

| Test | Proves |
|---|---|
| `test_blocks_match_the_golden_file[representative]` | Every block kind, nested lists, a link in a paragraph and in a table, a code language, and Ukrainian text. |
| `test_blocks_match_the_golden_file[malformed]` | Bad syntax becomes literal text, raw HTML is dropped, and odd tables and an unclosed fence are handled. |
| `test_offsets_point_back_at_the_source` | `text[start:end]` is the exact source. |
| `test_windows_line_endings_give_the_same_blocks` | `\r\n` input gives identical blocks. |
| `test_list_items_record_their_nesting_depth` | Nested lists give depth 1 and 2. |
| `test_a_table_with_only_empty_cells_produces_no_block` | Empty tables are skipped instead of crashing with `InvalidBlock`. |
| `test_many_links_parse_in_linear_time` | 40,000 links parse within 5 seconds; rebuilding the text for each link would take far longer. |
| `test_content_over_the_limit_raises_a_typed_error` | The typed failure. |
| `test_a_heading_block_requires_a_level` | The block model rejects a heading without a level. |

**Decision: generate the golden files, then review them by hand.** Writing JSON by hand invites typos. Running with `UPDATE_GOLDEN=1` writes what the parser produces now. **You** then check it against the table below, and once committed, it becomes the expected output.

**Action:** create `tests/fixtures/markdown/representative.md`:

````markdown
# Onboarding guide

Read the [security policy](https://example.com/security) before your first
deploy. Use `make check` locally.

## Steps

1. Clone the repository.
2. Install the tools:
   - uv
   - PostgreSQL

```bash
make setup
```

| Tool | Version |
|------|---------|
| uv | 0.9 |
| [Python](https://www.python.org) | 3.14 |

### Посилання

Текст українською.
````

Create `tests/fixtures/markdown/malformed.md`:

````markdown
#No space means this is not a heading

An [unclosed link](https://example.com and *unclosed emphasis.

<script>alert("raw html is dropped")</script>

| a | b |
|---|---|
| only one cell |
| one | two | three |

A [script link][unsafe] is kept as text.

[unsafe]: javascript:alert(1)

```python
print("the fence is never closed")
````

Create `tests/test_markdown_parser.py`:

````python
"""Tests for the Markdown parser and its structural blocks."""

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from knowledge_service.markdown_parser import parse_markdown
from knowledge_service.structure import (
    BlockKind,
    DocumentTooLarge,
    InvalidBlock,
    ParsedDocument,
    StructuralBlock,
)

FIXTURES = Path(__file__).parent / "fixtures" / "markdown"


def as_json(parsed: ParsedDocument) -> list[object]:
    """Turn blocks into the plain JSON shape stored in the golden files."""
    return json.loads(json.dumps([asdict(block) for block in parsed.blocks]))


@pytest.mark.parametrize("name", ["representative", "malformed"])
def test_blocks_match_the_golden_file(name: str) -> None:
    source = (FIXTURES / f"{name}.md").read_text(encoding="utf-8")
    golden = FIXTURES / f"{name}.blocks.json"

    actual = as_json(parse_markdown(source))

    if os.environ.get("UPDATE_GOLDEN") == "1":
        golden.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    assert actual == json.loads(golden.read_text(encoding="utf-8"))


def test_offsets_point_back_at_the_source() -> None:
    parsed = parse_markdown("# Title\n\nSome *text*.\n\n```\ncode\n```\n")

    heading, paragraph, code = parsed.blocks

    assert parsed.text[heading.start : heading.end] == "# Title"
    assert parsed.text[paragraph.start : paragraph.end] == "Some *text*."
    assert parsed.text[code.start : code.end] == "```\ncode\n```"


def test_windows_line_endings_give_the_same_blocks() -> None:
    unix = parse_markdown("# Title\n\nBody line\nnext line\n")
    windows = parse_markdown("# Title\r\n\r\nBody line\r\nnext line\r\n")

    assert windows == unix


def test_list_items_record_their_nesting_depth() -> None:
    parsed = parse_markdown("- outer\n  - inner\n")

    assert [(block.kind, block.list_depth) for block in parsed.blocks] == [
        (BlockKind.LIST_ITEM, 1),
        (BlockKind.LIST_ITEM, 2),
    ]


@pytest.mark.parametrize(
    "source",
    [
        "| |\n|-|\n",
        "| ![](https://example.com/x.png) |\n|---|\n",
        "| | |\n|-|-|\n| | |\n",
    ],
)
def test_a_table_with_only_empty_cells_produces_no_block(source: str) -> None:
    assert parse_markdown(source).blocks == ()


@pytest.mark.timeout(5)
def test_many_links_parse_in_linear_time() -> None:
    parsed = parse_markdown("[a](https://x.y) " * 40_000)

    [paragraph] = parsed.blocks
    assert len(paragraph.links) == 40_000


def test_content_over_the_limit_raises_a_typed_error() -> None:
    with pytest.raises(DocumentTooLarge, match="limit is 10"):
        parse_markdown("x" * 11, max_characters=10)


def test_a_heading_block_requires_a_level() -> None:
    with pytest.raises(InvalidBlock, match="heading level"):
        StructuralBlock(kind=BlockKind.HEADING, text="Title", start=0, end=7)
````

Generate the golden files once:

```bash
UPDATE_GOLDEN=1 UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_markdown_parser.py -q
```

Then open both `*.blocks.json` files and check them against this table:

| Fixture | Expected blocks |
|---|---|
| `representative` | heading 1 "Onboarding guide"; a paragraph whose text has no `[]()` or backticks, with one link to `https://example.com/security`; heading 2 "Steps"; two list items at depth 1 and two at depth 2; code "make setup" in `bash`; a table "Tool \| Version / uv \| 0.9 / Python \| 3.14" with one link; heading 3 "Посилання"; and a Ukrainian paragraph. 11 blocks. |
| `malformed` | 6 blocks: a paragraph "#No space…" that is not a heading; the unclosed-link line kept as literal text with no links; a table where the short row gets an empty cell and the extra cell "three" is dropped; `A [script link][unsafe] is kept as text.` with no links; the `[unsafe]: javascript:…` definition itself kept as a plain paragraph, because the parser refuses it as a link definition; and a code block in `python` that runs to the end. No `<script>` anywhere. |

Some of these results depend on details worth knowing:

- **`asdict`** turns a block, including its `Link`s, into a dictionary. The `json.dumps` and `json.loads` round trip turns tuples into lists, so the result compares equal to what was loaded from the file.
- **`ensure_ascii=False`** keeps "Посилання" readable in the JSON instead of `\u` escapes.
- **The pre-commit hooks** strip trailing spaces and check JSON, which is why the fixtures avoid trailing spaces.

**Check:** run it again *without* `UPDATE_GOLDEN`:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_markdown_parser.py -v
```

All 11 tests pass.

### Step 5 — No coding in this step: run the gates, then record evidence

**Action:** run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_markdown_parser.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. Answer these questions:

1. A later task asks for "the paragraph under the second heading". List three things it does not have to know about Markdown, because the parser already handled them.
2. Suppose you had written the parser with regular expressions instead of `markdown-it-py`. Pick one line from `malformed.md` and explain how a simple regular expression could get it wrong. Which later task would get the wrong input?
3. Suppose `test_offsets_point_back_at_the_source` were deleted and someone removed the `\r\n` normalization. What would go wrong for a Windows-edited file when M03-T11 tries to show a user where a citation came from?

**Check:** every command passes, and the evidence explains the design in your words.

## Completion checklist

- [ ] `markdown-it-py` is a project dependency, recorded in `pyproject.toml` and `uv.lock`.
- [ ] `structure.py` defines `BlockKind`, `Link`, `StructuralBlock`, `ParsedDocument`, `InvalidBlock`, `DocumentParseError`, and `DocumentTooLarge`.
- [ ] `parse_markdown` returns headings, paragraphs, list items, code, and tables with links and offsets, and `text[start:end]` is each block's source.
- [ ] Raw HTML is dropped, unsafe links stay text, and too-large input raises `DocumentTooLarge`.
- [ ] Both golden fixtures exist with reviewed `*.blocks.json` files, and the test passes without `UPDATE_GOLDEN`.
- [ ] Format, lint, type checks, the deterministic suite, `make check`, and `make docs-check` all pass.
- [ ] The evidence file holds your invariant, prediction, design, verification, and reflection.

When you're done, share your code or any error you hit, and I'll review it.
