# M03-T10 — Implement structure-aware chunking

Source task: [M03-T10](../curriculum/milestones/M03-local-ingestion/M03-T10-implement-structure-aware-chunking.md)

## What you will do

This lesson includes coding: Steps 2–3 write code, while Steps 1 and 4 do not.

M03-T09 cut text blindly every 256 tokens, so a chunk could hold the end of one section and the start of the next. This lesson adds a second chunker, `chunk_by_structure`, that follows the document's structure instead, and uses the fixed-window chunker as its fallback.

| Requirement | How this lesson meets it |
|---|---|
| Prefer headings and block boundaries | A chunk is cut only between blocks, and a heading starts a new chunk. |
| Min and max token budgets | A heading starts a new chunk only once the current one holds `min_tokens` (64); no chunk ever holds more than `max_tokens` (256). |
| Hierarchical titles | Each chunk records its `heading_path`, such as `Team handbook > Security > Laptops`. |
| Fallback to fixed windows | A block that does not fit in `max_tokens` is cut with M03-T09's windows. |
| Golden tests | A small handbook fixture and its expected chunks, stored as JSON and compared on every run. |

Terms used in this lesson:

- **Section:** a heading and the blocks after it, up to the next heading of the same or a higher level.
- **Heading path:** the titles of the headings a block sits under, outermost first, joined by ` > `.
- **Golden test:** a test that compares output with a stored, reviewed file. The repository already uses them for the parsers: run with `UPDATE_GOLDEN=1` to rewrite the file, then review the change before committing.

## Before you begin

- **The fallback must work on one block, not a whole document.** `chunk_document` windows the whole document. Step 2 moves its loop into a private `_windows` that windows any range of tokens, keeping offsets and block indexes relative to the whole document. `chunk_document` keeps the same output; M03-T09's tests prove it.
- **Both chunkers live in `chunking.py`.** They share the reading text, the tokens, and the window loop. A separate module would have needed those private helpers made public.
- **`DraftChunk` gains `heading_path`.** It defaults to `None`, so the fixed-window chunker and its tests are unaffected. The stored `Chunk` already has a `heading_path` field, so M03-T12 can pass it straight through.
- **Files you will touch:**
  - `src/knowledge_service/chunking.py`
  - new `tests/test_structured_chunking.py`, `tests/fixtures/chunking/handbook.md`, and `tests/fixtures/chunking/handbook.chunks.json`
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your prediction

**Invariant:** the same document and budgets always give the same chunks, in order, with ordinals from 0. Every chunk holds between 1 and `max_tokens` tokens, slices back exactly from the reading text, and every token is in some chunk. Chunks are cut only between blocks, except inside fixed windows for a block that does not fit. A chunk's heading path is the path of the block it begins in, and every title in it is at most 100 characters. `chunk_document` gives exactly the same output as before.

**Action:** create `docs/evidence/M03-T10-structure-aware-chunking.md`:

```markdown
# M03-T10 — Structure-aware chunking evidence

Completed: pending

## Invariant

Pending.

## Prediction

Before writing code, say what chunks and heading paths you expect for:
`# Guide`, `## Setup`, `### Tools`, `## Usage` each followed by a short
paragraph (minimum 1); two tiny sections `# A` and `# B` with a minimum of
10; three paragraphs of 3 tokens with a maximum of 6 and no headings; a
paragraph of 10 tokens with a maximum of 4 and an overlap of 1; a heading
followed by a paragraph that fits alone but not together with the heading; a
heading of 1,000 words; an empty document; and a minimum above the maximum.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and your predictions.

### Step 2 — Coding step: the structure-aware chunker

The whole module changes in several places, so replace `src/knowledge_service/chunking.py` with the version below, then read the explanation of each change.

| Change | What and why |
|---|---|
| Module docstring | Describes both strategies. |
| `MIN_TOKENS`, `MAX_TOKENS`, `HEADING_SEPARATOR`, `MAX_TITLE_CHARACTERS` | The new budgets, the ` > ` separator, and the 100-character title cap. |
| `DraftChunk.heading_path` | A new optional field, refused if blank, like `Chunk.heading_path`. |
| `chunk_document` | Now one call to `_windows` over all tokens. Its output is unchanged. |
| `chunk_by_structure` | The new chunker. |
| `_heading_paths` | Computes every block's heading path in one pass. |
| `_windows` and `_draft` | The window loop and the chunk builder, shared by both chunkers. |

How `chunk_by_structure` decides, block by block:

1. Find the block's tokens with `bisect_left` over the token start offsets. An empty code block has none and is skipped.
2. **The block does not fit:** it holds more than `max_tokens`, or the open chunk is still below `min_tokens` and the two together would pass `max_tokens`. Cut fixed windows. If the open chunk is below the minimum, such as a lone heading, the windows start at its first token, so the heading stays with its text. Otherwise the open chunk is finished first.
3. **The block would overflow the open chunk**, or it is a heading and the open chunk already holds `min_tokens`: finish the open chunk.
4. Add the block to the open chunk, or start a new one with it.

At the end, every chunk gets its ordinal and the heading path of the block it begins in.

Decisions:

- **One rule for heading paths: the block a chunk begins in.** When a tiny section joins the next one, or when a lone heading joins a window, the chunk still takes the path of its first block. One rule is easier to reason about than special cases.
- **Titles in a path are cut to 100 characters.** An adversarial review found that a 400 KB heading was windowed into 893 chunks, each carrying the whole heading as its path: about 357 MB written to the database. With at most six heading levels, a path now stays under about 620 characters.
- **A lone heading never stands alone before a block that fills a chunk.** The review found that `## Getting started` followed by a 256-token paragraph became a 2-token chunk of just the heading. Now the two are windowed together: the paragraph is cut once, but the heading stays with its text.
- **No overlap between structural chunks.** Section boundaries are natural cuts, so overlap is only used inside fixed windows.
- **The budgets are checked first.** `1 <= min_tokens <= max_tokens` and `0 <= overlap_tokens < max_tokens`, or `InvalidChunking`.

**Action:** replace `src/knowledge_service/chunking.py` with:

```python
"""Split a parsed document into chunks of tokens, two ways.

A document's reading text is its blocks' texts joined by blank lines, and both
strategies cut it into tokens.

`chunk_document` is the simple baseline that other strategies are measured
against: every window holds at most `window_tokens` tokens, and consecutive
windows share exactly `overlap_tokens`, so a sentence cut at a window edge
still appears whole in one of them.

`chunk_by_structure` follows the document's structure instead. It cuts only
between blocks, starts a new chunk at a heading once the current chunk holds
`min_tokens`, never exceeds `max_tokens`, and records the headings each chunk
sits under: the headings above the block it begins in. A block that does not
fit in `max_tokens`, alone or after a chunk still below `min_tokens`, falls
back to fixed windows of that size, and that small chunk, such as a lone
heading, joins the first window instead of standing alone.

Every chunk is reversible: `reading_text(parsed)[chunk.start:chunk.end]` is
exactly the chunk's text, and `first_block` and `last_block` name the blocks
it came from, which lead back to source offsets and PDF pages.

A token is a run of letters, digits, and underscores, or one other visible
character. Accents, joiners, and emoji modifiers stay with the character
before them. No token is longer than `MAX_TOKEN_CHARACTERS`, whatever it is
made of; only pathological text, such as hundreds of stacked accents, gets cut
inside a character cluster. These are word-level tokens, not a model's tokens:
a model usually counts more.
"""

import unicodedata
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, replace

from knowledge_service.structure import BlockKind, ParsedDocument

WINDOW_TOKENS = 256
OVERLAP_TOKENS = 32
MIN_TOKENS = 64
MAX_TOKENS = 256
HEADING_SEPARATOR = " > "
MAX_TITLE_CHARACTERS = 100
MAX_TOKEN_CHARACTERS = 32
BLOCK_SEPARATOR = "\n\n"

_ZERO_WIDTH_JOINER = "\u200d"


class InvalidChunking(ValueError):
    """Chunking settings or a drafted chunk violate an invariant."""


@dataclass(frozen=True, slots=True)
class DraftChunk:
    """One piece of a document's reading text, before it is stored as a Chunk.

    `start` and `end` are character offsets into the reading text, and
    `first_block` and `last_block` are indexes into the parsed blocks.
    `heading_path` names the headings the chunk sits under, outermost first,
    or is None when no heading comes before it.
    """

    ordinal: int
    text: str
    start: int
    end: int
    token_count: int
    first_block: int
    last_block: int
    heading_path: str | None = None

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise InvalidChunking("chunk ordinal must not be negative")
        if not 0 <= self.start < self.end:
            raise InvalidChunking("chunk offset must satisfy 0 <= start < end")
        if len(self.text) != self.end - self.start or not self.text.strip():
            raise InvalidChunking("chunk text must be its non-blank span")
        if self.token_count < 1:
            raise InvalidChunking("a chunk holds at least one token")
        if not 0 <= self.first_block <= self.last_block:
            raise InvalidChunking("a chunk's block range must be ordered")
        if self.heading_path is not None and not self.heading_path.strip():
            raise InvalidChunking("heading path must not be empty when provided")


def reading_text(parsed: ParsedDocument) -> str:
    """Return the blocks' texts joined by blank lines: the text that is chunked."""
    return BLOCK_SEPARATOR.join(block.text for block in parsed.blocks)


def chunk_document(
    parsed: ParsedDocument,
    *,
    window_tokens: int = WINDOW_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> tuple[DraftChunk, ...]:
    """Return the document's windows in order; an empty document has none."""
    if window_tokens < 1:
        raise InvalidChunking("a window must hold at least one token")
    if not 0 <= overlap_tokens < window_tokens:
        raise InvalidChunking("overlap must be at least 0 and below the window size")
    text = reading_text(parsed)
    tokens = token_spans(text)
    return tuple(
        _windows(
            text,
            tokens,
            _block_starts(parsed),
            0,
            len(tokens),
            window_tokens,
            overlap_tokens,
        )
    )


def chunk_by_structure(
    parsed: ParsedDocument,
    *,
    min_tokens: int = MIN_TOKENS,
    max_tokens: int = MAX_TOKENS,
    overlap_tokens: int = OVERLAP_TOKENS,
) -> tuple[DraftChunk, ...]:
    """Return chunks that follow headings and blocks; an empty document has none."""
    if not 1 <= min_tokens <= max_tokens:
        raise InvalidChunking("token budgets must satisfy 1 <= min <= max")
    if not 0 <= overlap_tokens < max_tokens:
        raise InvalidChunking("overlap must be at least 0 and below the maximum")
    text = reading_text(parsed)
    tokens = token_spans(text)
    starts = _block_starts(parsed)
    token_starts = [start for start, _ in tokens]
    chunks: list[DraftChunk] = []
    # The chunk being built: its first and last token, or -1 when none is open.
    open_first = open_last = -1
    for index, block in enumerate(parsed.blocks):
        first = bisect_left(token_starts, starts[index])
        stop = bisect_left(token_starts, starts[index] + len(block.text))
        if first == stop:
            continue  # an empty code block holds no tokens
        open_size = open_last - open_first + 1 if open_first >= 0 else 0
        if stop - first > max_tokens or (
            0 < open_size < min_tokens and stop - open_first > max_tokens
        ):
            # Too big for one chunk: cut fixed windows. A chunk still below
            # the minimum, such as a lone heading, joins the first window.
            if 0 < open_size < min_tokens:
                first, open_first = open_first, -1
            elif open_first >= 0:
                chunks.append(_draft(text, tokens, starts, open_first, open_last))
                open_first = -1
            chunks.extend(
                _windows(text, tokens, starts, first, stop, max_tokens, overlap_tokens)
            )
            continue
        if open_first >= 0 and (
            stop - open_first > max_tokens
            or (block.kind is BlockKind.HEADING and open_size >= min_tokens)
        ):
            chunks.append(_draft(text, tokens, starts, open_first, open_last))
            open_first = -1
        if open_first < 0:
            open_first = first
        open_last = stop - 1
    if open_first >= 0:
        chunks.append(_draft(text, tokens, starts, open_first, open_last))
    paths = _heading_paths(parsed)
    return tuple(
        replace(chunk, ordinal=ordinal, heading_path=paths[chunk.first_block])
        for ordinal, chunk in enumerate(chunks)
    )


def _heading_paths(parsed: ParsedDocument) -> list[str | None]:
    """Return, for each block, the titles of the headings it sits under.

    Each title is cut to `MAX_TITLE_CHARACTERS`, so a path stays short even
    when a heading is huge; there are at most six heading levels.
    """
    paths: list[str | None] = []
    headings: list[tuple[int, str]] = []
    for block in parsed.blocks:
        if block.kind is BlockKind.HEADING:
            level = block.heading_level or 1
            while headings and headings[-1][0] >= level:
                headings.pop()
            title = " ".join(block.text.split())[:MAX_TITLE_CHARACTERS].rstrip()
            headings.append((level, title))
        paths.append(HEADING_SEPARATOR.join(title for _, title in headings) or None)
    return paths


def _windows(
    text: str,
    tokens: list[tuple[int, int]],
    starts: list[int],
    first: int,
    stop: int,
    window_tokens: int,
    overlap_tokens: int,
) -> list[DraftChunk]:
    """Cut `tokens[first:stop]` into overlapping windows.

    Offsets and block indexes stay relative to the whole document, so a caller
    can window a single block of it. Ordinals start at 0; the caller renumbers.
    """
    chunks: list[DraftChunk] = []
    while first < stop:
        last = min(first + window_tokens, stop) - 1
        chunks.append(_draft(text, tokens, starts, first, last, ordinal=len(chunks)))
        if last == stop - 1:
            break
        first = last + 1 - overlap_tokens
    return chunks


def _draft(
    text: str,
    tokens: list[tuple[int, int]],
    starts: list[int],
    first: int,
    last: int,
    ordinal: int = 0,
) -> DraftChunk:
    """Return the chunk holding `tokens[first]` through `tokens[last]`."""
    start, end = tokens[first][0], tokens[last][1]
    return DraftChunk(
        ordinal=ordinal,
        text=text[start:end],
        start=start,
        end=end,
        token_count=last - first + 1,
        first_block=bisect_right(starts, start) - 1,
        last_block=bisect_right(starts, end - 1) - 1,
    )


def token_spans(text: str) -> list[tuple[int, int]]:
    """Return the `(start, end)` offsets of every token in `text`, in order."""
    spans: list[tuple[int, int]] = []
    start = -1
    word = False
    for index, character in enumerate(text):
        if start >= 0 and _continues(text, index, start, word=word):
            continue
        if start >= 0:
            spans.append((start, index))
            start = -1
        if not character.isspace():
            start = index
            word = _is_word(character)
    if start >= 0:
        spans.append((start, len(text)))
    return spans


def _continues(text: str, index: int, start: int, *, word: bool) -> bool:
    """Whether the character at `index` belongs to the token begun at `start`."""
    character = text[index]
    if character.isspace() or index - start >= MAX_TOKEN_CHARACTERS:
        return False
    if _attaches(character) or text[index - 1] == _ZERO_WIDTH_JOINER:
        return True
    return word and _is_word(character)


def _is_word(character: str) -> bool:
    return character.isalnum() or character == "_"


def _attaches(character: str) -> bool:
    """Accents, joiners, and emoji modifiers belong to the previous token."""
    return (
        unicodedata.category(character).startswith("M")
        or character == _ZERO_WIDTH_JOINER
        or "\ufe00" <= character <= "\ufe0f"  # variation selectors
        or "\U0001f3fb" <= character <= "\U0001f3ff"  # skin tones
        or "\U000e0020" <= character <= "\U000e007f"  # tags in flag sequences
    )


def _block_starts(parsed: ParsedDocument) -> list[int]:
    """Return where each block's text starts in the reading text."""
    starts: list[int] = []
    offset = 0
    for block in parsed.blocks:
        starts.append(offset)
        offset += len(block.text) + len(BLOCK_SEPARATOR)
    return starts
```

Some details:

- **`bisect_left(token_starts, starts[index])`** is the index of the first token starting at or after the block's start, so `tokens[first:stop]` are exactly the block's tokens. Tokens never cross blocks, because blocks are separated by blank lines.
- **`replace(chunk, ordinal=..., heading_path=...)`** from `dataclasses` makes a copy with some fields changed. `_windows` numbers its windows from 0, so the final pass gives every chunk its real ordinal.
- **`block.heading_level or 1`** satisfies Pyright: the field is `int | None`, but a heading block always has a level.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_chunking.py -q` still gives 23 passed, so `chunk_document` is unchanged. `make typecheck lint` passes.

### Step 3 — Coding step: the golden fixture and the tests

**Decision: one readable golden fixture with small budgets.** With `min_tokens=8`, `max_tokens=24`, and `overlap_tokens=4`, a short handbook shows every rule: sections, a tiny section merged with its first subsection, and a paragraph long enough to fall back to windows.

**Action:** create `tests/fixtures/chunking/handbook.md`:

```markdown
# Team handbook

Welcome to the team. This handbook explains how we work.

## Vacation

Request vacation at least two weeks ahead. Your team lead approves it in the
HR system.

## Security

### Passwords

Never share passwords. Store every password in the company password manager.

### Laptops

Lock your laptop whenever you leave it. Report a lost laptop to the security
team within one hour, even outside working hours, so they can wipe it remotely
before anyone reads the data on it, and then file an incident report that
names the time, the place, and what was stored on the laptop.

## Contacts

Ask questions in the help channel.
```

Create `tests/test_structured_chunking.py`:

```python
"""Tests for structure-aware chunking."""

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from knowledge_service.chunking import (
    DraftChunk,
    InvalidChunking,
    chunk_by_structure,
    reading_text,
    token_spans,
)
from knowledge_service.markdown_parser import parse_markdown
from knowledge_service.structure import ParsedDocument
from knowledge_service.text_parser import parse_text

FIXTURES = Path(__file__).parent / "fixtures" / "chunking"
# Small budgets keep the golden file readable.
BUDGETS = {"min_tokens": 8, "max_tokens": 24, "overlap_tokens": 4}
# The same examples on every run, and no example database written to disk.
DETERMINISTIC = settings(derandomize=True, database=None, deadline=None)


def summary(chunks: tuple[DraftChunk, ...]) -> list[dict[str, object]]:
    return [asdict(chunk) for chunk in chunks]


def test_chunks_match_the_golden_file() -> None:
    source = (FIXTURES / "handbook.md").read_text(encoding="utf-8")
    golden = FIXTURES / "handbook.chunks.json"

    actual = summary(chunk_by_structure(parse_markdown(source), **BUDGETS))

    if os.environ.get("UPDATE_GOLDEN") == "1":
        golden.write_text(
            json.dumps(actual, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    assert actual == json.loads(golden.read_text(encoding="utf-8"))


def test_each_section_keeps_its_heading_path() -> None:
    parsed = parse_markdown(
        "# Guide\n\nIntro words here.\n\n## Setup\n\nSetup words here.\n\n"
        "### Tools\n\nTool words here.\n\n## Usage\n\nUsage words here.\n"
    )

    chunks = chunk_by_structure(parsed, min_tokens=1, max_tokens=50)

    assert [chunk.heading_path for chunk in chunks] == [
        "Guide",
        "Guide > Setup",
        "Guide > Setup > Tools",
        "Guide > Usage",
    ]
    assert [chunk.text.split("\n\n")[0] for chunk in chunks] == [
        "Guide",
        "Setup",
        "Tools",
        "Usage",
    ]


def test_a_section_below_the_minimum_joins_the_next_one() -> None:
    parsed = parse_markdown("# A\n\nShort.\n\n# B\n\nAlso short.\n")

    [chunk] = chunk_by_structure(parsed, min_tokens=10, max_tokens=50)

    assert chunk.text == "A\n\nShort.\n\nB\n\nAlso short."
    assert chunk.heading_path == "A"


def test_blocks_are_packed_up_to_the_maximum_without_being_cut() -> None:
    parsed = parse_text("one two three\n\nfour five six\n\nseven eight nine")

    chunks = chunk_by_structure(parsed, min_tokens=1, max_tokens=6, overlap_tokens=0)

    assert [chunk.text for chunk in chunks] == [
        "one two three\n\nfour five six",
        "seven eight nine",
    ]
    assert all(chunk.heading_path is None for chunk in chunks)


def test_a_block_over_the_maximum_falls_back_to_fixed_windows() -> None:
    long_paragraph = " ".join(f"w{number}" for number in range(10))
    parsed = parse_markdown(f"# Notes\n\nIntro.\n\n{long_paragraph}\n\nOutro.\n")

    chunks = chunk_by_structure(parsed, min_tokens=1, max_tokens=4, overlap_tokens=1)

    assert [chunk.text for chunk in chunks] == [
        "Notes\n\nIntro.",
        "w0 w1 w2 w3",
        "w3 w4 w5 w6",
        "w6 w7 w8 w9",
        "Outro.",
    ]
    assert all(chunk.heading_path == "Notes" for chunk in chunks)


def test_a_heading_is_never_left_alone_before_a_block_that_fills_a_chunk() -> None:
    # The paragraph fits in a chunk by itself, but not together with the heading.
    paragraph = " ".join(f"w{number}" for number in range(6))
    parsed = parse_markdown(f"## Getting started\n\n{paragraph}\n")

    chunks = chunk_by_structure(parsed, min_tokens=4, max_tokens=6, overlap_tokens=1)

    assert [chunk.text for chunk in chunks] == [
        "Getting started\n\nw0 w1 w2 w3",
        "w3 w4 w5",
    ]


def test_a_chunk_takes_the_heading_path_of_the_block_it_begins_in() -> None:
    # One, Short text, and Two stay below the minimum, so they join the windows
    # cut from the long paragraph; each window takes the path where it begins.
    long_paragraph = " ".join(f"w{number}" for number in range(12))
    parsed = parse_markdown(f"# One\n\nShort text.\n\n## Two\n\n{long_paragraph}\n")

    chunks = chunk_by_structure(parsed, min_tokens=8, max_tokens=8, overlap_tokens=1)

    assert [chunk.heading_path for chunk in chunks] == [
        "One",
        "One > Two",
        "One > Two",
    ]


def test_a_huge_heading_gives_a_short_heading_path() -> None:
    title = " ".join(f"word{number}" for number in range(1000))
    parsed = parse_markdown(f"# {title}\n\nBody.\n")

    chunks = chunk_by_structure(parsed, min_tokens=1, max_tokens=50, overlap_tokens=5)

    assert len(chunks) > 10
    assert all(len(chunk.heading_path or "") <= 100 for chunk in chunks)


def test_an_empty_code_block_adds_nothing() -> None:
    parsed = parse_markdown("# Setup\n\nInstall it.\n\n```\n```\n\nDone.\n")

    [chunk] = chunk_by_structure(parsed, min_tokens=1, max_tokens=50)

    assert chunk.text == "Setup\n\nInstall it.\n\n\n\nDone."


def test_an_empty_document_has_no_chunks() -> None:
    assert chunk_by_structure(ParsedDocument(text="", blocks=())) == ()


@pytest.mark.parametrize(
    "budgets",
    [
        {"min_tokens": 0, "max_tokens": 10},
        {"min_tokens": 11, "max_tokens": 10},
        {"min_tokens": 1, "max_tokens": 10, "overlap_tokens": 10},
        {"min_tokens": 1, "max_tokens": 10, "overlap_tokens": -1},
    ],
    ids=["empty-minimum", "minimum-above-maximum", "overlap-at-maximum", "negative"],
)
def test_impossible_budgets_are_refused(budgets: dict[str, int]) -> None:
    with pytest.raises(InvalidChunking):
        chunk_by_structure(parse_text("text"), **budgets)


def test_a_blank_heading_path_is_refused() -> None:
    with pytest.raises(InvalidChunking, match="heading path"):
        DraftChunk(
            ordinal=0,
            text="words",
            start=0,
            end=5,
            token_count=1,
            first_block=0,
            last_block=0,
            heading_path="  ",
        )


def heading(level: int, title: str) -> str:
    return "#" * level + " " + title


titles = st.text("abc ", min_size=1, max_size=12).filter(str.strip)
blocks_of_text = st.lists(
    st.one_of(
        st.builds(heading, st.integers(min_value=1, max_value=3), titles),
        st.text("abcdef ,.", min_size=1, max_size=120).filter(str.strip),
    ),
    min_size=1,
    max_size=12,
)


@DETERMINISTIC
@given(
    paragraphs=blocks_of_text,
    maximum=st.integers(min_value=1, max_value=30),
    data=st.data(),
)
def test_every_structured_chunking_is_bounded_reversible_and_complete(
    paragraphs: list[str], maximum: int, data: st.DataObject
) -> None:
    minimum = data.draw(st.integers(min_value=1, max_value=maximum))
    overlap = data.draw(st.integers(min_value=0, max_value=maximum - 1))
    parsed = parse_markdown("\n\n".join(paragraphs))
    text = reading_text(parsed)
    every_token = token_spans(text)

    chunks = chunk_by_structure(
        parsed, min_tokens=minimum, max_tokens=maximum, overlap_tokens=overlap
    )

    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    covered: set[tuple[int, int]] = set()
    for chunk in chunks:
        assert text[chunk.start : chunk.end] == chunk.text
        inside = [span for span in every_token if chunk.start <= span[0] < chunk.end]
        assert len(inside) == chunk.token_count <= maximum
        covered.update(inside)
    assert covered == set(every_token)
```

Then generate the golden file and **review it** before trusting it:

```bash
UPDATE_GOLDEN=1 UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_structured_chunking.py -q
```

This writes `tests/fixtures/chunking/handbook.chunks.json`. It must describe these seven chunks:

| Chunk | Tokens | Blocks | Heading path | Starts with |
|---|---|---|---|---|
| 0 | 14 | 0–1 | `Team handbook` | `Team handbook` |
| 1 | 19 | 2–3 | `Team handbook > Vacation` | `Vacation` |
| 2 | 15 | 4–6 | `Team handbook > Security` | `Security`, then `Passwords` (the tiny `Security` section joined its first subsection) |
| 3 | 24 | 7–8 | `Team handbook > Security > Laptops` | `Laptops`, then the long paragraph (the first window) |
| 4 | 24 | 8–8 | `Team handbook > Security > Laptops` | the second window |
| 5 | 22 | 8–8 | `Team handbook > Security > Laptops` | the last window |
| 6 | 8 | 9–10 | `Team handbook > Contacts` | `Contacts` |

The full expected file, to compare with yours:

```json
[
  {
    "ordinal": 0,
    "text": "Team handbook\n\nWelcome to the team. This handbook explains how we work.",
    "start": 0,
    "end": 71,
    "token_count": 14,
    "first_block": 0,
    "last_block": 1,
    "heading_path": "Team handbook"
  },
  {
    "ordinal": 1,
    "text": "Vacation\n\nRequest vacation at least two weeks ahead. Your team lead approves it in the HR system.",
    "start": 73,
    "end": 170,
    "token_count": 19,
    "first_block": 2,
    "last_block": 3,
    "heading_path": "Team handbook > Vacation"
  },
  {
    "ordinal": 2,
    "text": "Security\n\nPasswords\n\nNever share passwords. Store every password in the company password manager.",
    "start": 172,
    "end": 269,
    "token_count": 15,
    "first_block": 4,
    "last_block": 6,
    "heading_path": "Team handbook > Security"
  },
  {
    "ordinal": 3,
    "text": "Laptops\n\nLock your laptop whenever you leave it. Report a lost laptop to the security team within one hour, even outside working",
    "start": 271,
    "end": 399,
    "token_count": 24,
    "first_block": 7,
    "last_block": 8,
    "heading_path": "Team handbook > Security > Laptops"
  },
  {
    "ordinal": 4,
    "text": ", even outside working hours, so they can wipe it remotely before anyone reads the data on it, and then file an",
    "start": 377,
    "end": 488,
    "token_count": 24,
    "first_block": 8,
    "last_block": 8,
    "heading_path": "Team handbook > Security > Laptops"
  },
  {
    "ordinal": 5,
    "text": "and then file an incident report that names the time, the place, and what was stored on the laptop.",
    "start": 472,
    "end": 571,
    "token_count": 22,
    "first_block": 8,
    "last_block": 8,
    "heading_path": "Team handbook > Security > Laptops"
  },
  {
    "ordinal": 6,
    "text": "Contacts\n\nAsk questions in the help channel.",
    "start": 573,
    "end": 617,
    "token_count": 8,
    "first_block": 9,
    "last_block": 10,
    "heading_path": "Team handbook > Contacts"
  }
]
```

| Test | Proves |
|---|---|
| `test_chunks_match_the_golden_file` | The task's acceptance criterion: preserved sections and the fallback, in one reviewed file. |
| `test_each_section_keeps_its_heading_path` | Paths for h1, h2, h3, then back to h2. |
| `test_a_section_below_the_minimum_joins_the_next_one` | The minimum. |
| `test_blocks_are_packed_up_to_the_maximum_without_being_cut` | The maximum, with no headings. |
| `test_a_block_over_the_maximum_falls_back_to_fixed_windows` | The fallback, with overlap. |
| `test_a_heading_is_never_left_alone_before_a_block_that_fills_a_chunk` | The review's orphan-heading case. |
| `test_a_chunk_takes_the_heading_path_of_the_block_it_begins_in` | The one path rule. |
| `test_a_huge_heading_gives_a_short_heading_path` | The review's 357 MB case. |
| `test_an_empty_code_block_adds_nothing` | Blocks without tokens. |
| `test_an_empty_document_has_no_chunks` | The empty case. |
| `test_impossible_budgets_are_refused` (4 cases) | The typed failure. |
| `test_a_blank_heading_path_is_refused` | `DraftChunk`'s new check. |
| `test_every_structured_chunking_is_bounded_reversible_and_complete` | **Property:** for random Markdown and budgets, ordinals are contiguous, every chunk is reversible and holds 1 to `max_tokens` tokens, and every token is covered. |

The three tests from the review fail against the first version of this chunker; the golden test also fails for any change in chunk boundaries or paths.

Some details:

- **`summary(...)` uses `asdict`**, which turns each `DraftChunk` into a plain dictionary, so it can be written as JSON and compared.
- **`st.builds(heading, st.integers(...), titles)`** builds a Markdown heading line from a random level and title. A named function keeps Pyright's types complete; a `lambda` there is reported as partially unknown.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_structured_chunking.py tests/test_chunking.py -v
```

All 39 tests pass: 16 in `test_structured_chunking.py` and 23 in `test_chunking.py`.

### Step 4 — No coding in this step: run the gates, then record evidence

**Action:** run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_structured_chunking.py tests/test_chunking.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. In Design, also record these notes from the adversarial review:

- **A path can mislead when sections merge.** Tiny sections that join because they stay below the minimum share the first block's path, so a chunk covering sections `S0` to `S4` says only `S0`.
- **` > ` inside a heading is ambiguous.** A heading such as `a > b` cannot be told apart from two nested headings when reading the path back.
- **A heading at the very end stands alone.** A last section of only a heading becomes a small chunk; the minimum cannot be met when nothing follows.
- **The fallback can split a paragraph that would fit alone,** when a chunk below the minimum comes right before it. That is the price of keeping a heading with its text.

Answer these reflection questions in your own words:

1. Why is cutting at headings usually better for retrieval than fixed windows? Describe what a question about vacation would retrieve from each.
2. Why does the structure-aware chunker still need fixed windows?
3. What does `test_chunks_match_the_golden_file` protect that the other tests do not, and why must you review the file after `UPDATE_GOLDEN=1` instead of trusting it?
4. A 400 KB heading once produced 357 MB of heading paths. Why did the size grow so fast, and which line stopped it?

**Check:** every command passes, and the evidence explains the design in your words.

## Completion checklist

- [ ] `chunking.py` has `chunk_by_structure`, and `chunk_document` still passes all 23 M03-T09 tests.
- [ ] Chunks are cut only between blocks, except inside fixed windows for a block that does not fit, and never exceed `max_tokens`.
- [ ] A heading starts a new chunk once the current one holds `min_tokens`, and a lone heading never stands alone before a block that fills a chunk.
- [ ] Every chunk has the heading path of the block it begins in, with titles cut to 100 characters.
- [ ] Impossible budgets and a blank heading path raise `InvalidChunking`.
- [ ] The golden file was generated with `UPDATE_GOLDEN=1`, reviewed against the table above, and matches.
- [ ] All 39 chunking tests, the full deterministic suite, format, lint, types, `make check`, and `make docs-check` pass.
- [ ] The evidence file holds your invariant, predictions, design (including the four recorded notes), verification, and your own reflection.

When you're done, share your code or any error you hit, and I'll review it.
