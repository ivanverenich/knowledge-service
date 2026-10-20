# M03-T09 — Implement fixed-window chunking

Source task: [M03-T09](../curriculum/milestones/M03-local-ingestion/M03-T09-implement-fixed-window-chunking.md)

## What you will do

This lesson includes coding: Steps 2–3 write code, while Steps 1 and 4 do not.

Retrieval does not search whole documents. It searches **chunks**: short pieces of a document, each small enough to embed and to show as evidence. This lesson builds the simplest chunker, the baseline that smarter chunking (M03-T10) is measured against: cut the text into windows of a fixed number of tokens, with a small overlap between neighbours.

| Requirement | How this lesson meets it |
|---|---|
| Token windows | Every chunk holds at most `window_tokens` tokens (256 by default). |
| Bounded overlap | Neighbouring chunks share exactly `overlap_tokens` tokens (32 by default), so a sentence cut at a window edge appears whole in one of them. |
| Stable ordering | Chunks get ordinals 0, 1, 2, ... in text order, and the same document always gives the same chunks. |
| Token counts | Every chunk records how many tokens it holds. |
| Reversible provenance spans | `reading_text(parsed)[chunk.start:chunk.end]` is exactly the chunk's text, and `first_block`/`last_block` name the blocks it came from, which lead to source offsets and PDF pages. |

Terms used in this lesson:

- **Token:** the unit the window counts. Here it is a word (a run of letters, digits, and underscores) or one punctuation mark. A model's tokenizer usually splits text into more pieces than this.
- **Window:** a run of consecutive tokens. **Overlap:** how many tokens a window repeats from the previous one.
- **Reading text:** the blocks' texts joined by blank lines. It is what gets chunked and later embedded.

## Before you begin

- **Why not chunk `parsed.text`.** For HTML, `parsed.text` is the raw page, so chunks would be full of tags. The blocks hold the clean text. Chunk offsets therefore index the reading text, and the block indexes lead back to `block.start`, `block.end`, and `block.page` in the source.
- **Why `DraftChunk` and not `Chunk`.** `chunks.Chunk` needs a `ChunkId`, a `DocumentId`, a content version, and the content fingerprint; those exist only when a Document is reconciled (M03-T12). A `DraftChunk` holds everything the chunker decides; M03-T12 adds the identity.
- **Why a word tokenizer.** A model tokenizer means a large dependency, model files, or a network download, and the embedding model is not chosen yet. A deterministic word tokenizer is enough for a baseline, works offline, and is easy to explain. Its counts are lower than a model's, so windows must leave room when the embedding model is chosen.
- **Files you will touch:**
  - new `src/knowledge_service/chunking.py` and `tests/test_chunking.py`
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your prediction

**Invariant:** the same parsed document and settings always give the same chunks, in order, with ordinals from 0. Every chunk holds between 1 and `window_tokens` tokens, neighbours share exactly `overlap_tokens`, and every token is in some chunk. `reading_text(parsed)[chunk.start:chunk.end]` is exactly the chunk's text. No token is longer than 32 characters, and no token ever contains whitespace.

**Action:** create `docs/evidence/M03-T09-fixed-window-chunking.md`:

```markdown
# M03-T09 — Fixed-window chunking evidence

Completed: pending

## Invariant

Pending.

## Prediction

Before writing code, say how many chunks, and which tokens in each, you
expect for: a 6-token sentence with a window of 8, exactly 8 tokens with a
window of 8, 9 tokens with a window of 8 and an overlap of 2, 1,000 tokens
with a window of 64 and an overlap of 8, a single 1,000-letter word, Ukrainian
text with an accented letter and an emoji, an empty document, a two-page PDF,
and a window of 8 with an overlap of 8.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and your predictions.

### Step 2 — Coding step: the chunking module

| Piece | What it does |
|---|---|
| `reading_text(parsed)` | Joins the blocks' texts with `"\n\n"`. |
| `token_spans(text)` | Returns the `(start, end)` of every token. |
| `chunk_document(parsed, window_tokens=..., overlap_tokens=...)` | Cuts the token list into windows and returns `DraftChunk`s. |
| `DraftChunk` | Ordinal, text, `start`/`end` in the reading text, token count, first and last block. It checks its own invariants. |
| `InvalidChunking` | The typed failure: impossible settings, or an inconsistent `DraftChunk`. |

How the window loop works, for 9 tokens with a window of 8 and an overlap of 2:

```text
tokens:   w0 w1 w2 w3 w4 w5 w6 w7 w8
chunk 0: [w0 ................... w7]       first=0, last=7
chunk 1:                   [w6 w7 w8]      first = last + 1 - overlap = 6
```

The loop stops as soon as a window reaches the last token, so there is never a tail chunk that only repeats the previous one.

Decisions:

- **Offsets come from tokens.** A chunk runs from its first token's start to its last token's end, so its text never starts or ends with whitespace, and slicing the reading text gives it back exactly.
- **Blocks are found with `bisect_right`.** `_block_starts` lists where each block's text begins in the reading text. `bisect_right(starts, offset) - 1` finds the block containing an offset in logarithmic time, a binary search instead of a scan.
- **Accents, joiners, and emoji modifiers stay with the character before them.** Otherwise a window edge could cut `е` from its accent, or split the emoji `"\U0001f469\u200d\U0001f4bb"` (woman, zero-width joiner, laptop) in two.
- **No token is longer than `MAX_TOKEN_CHARACTERS` (32), whatever it is made of.** An adversarial review found that a run of joiners or stacked accents could make one "token" a million characters long, and so a one-token chunk of a million characters. The length check comes first now, so only pathological text gets cut inside a character cluster.
- **Settings are checked first.** A window below 1, a negative overlap, or an overlap at least as large as the window would loop forever or make no progress, so they raise `InvalidChunking`.

**Action:** create `src/knowledge_service/chunking.py`:

```python
"""Split a parsed document into fixed windows of tokens that overlap a little.

This is the simple baseline that later chunking strategies are measured
against. A document's reading text is its blocks' texts joined by blank
lines. It is cut into tokens, and every window holds at most `window_tokens`
of them; consecutive windows share exactly `overlap_tokens`, so a sentence cut
at a window edge still appears whole in one of them.

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
from bisect import bisect_right
from dataclasses import dataclass

from knowledge_service.structure import ParsedDocument

WINDOW_TOKENS = 256
OVERLAP_TOKENS = 32
MAX_TOKEN_CHARACTERS = 32
BLOCK_SEPARATOR = "\n\n"

_ZERO_WIDTH_JOINER = "\u200d"


class InvalidChunking(ValueError):
    """Chunking settings or a drafted chunk violate an invariant."""


@dataclass(frozen=True, slots=True)
class DraftChunk:
    """One window of a document's reading text, before it is stored as a Chunk.

    `start` and `end` are character offsets into the reading text, and
    `first_block` and `last_block` are indexes into the parsed blocks.
    """

    ordinal: int
    text: str
    start: int
    end: int
    token_count: int
    first_block: int
    last_block: int

    def __post_init__(self) -> None:
        if self.ordinal < 0:
            raise InvalidChunking("chunk ordinal must not be negative")
        if not 0 <= self.start < self.end:
            raise InvalidChunking("chunk offsets must satisfy 0 <= start < end")
        if len(self.text) != self.end - self.start or not self.text.strip():
            raise InvalidChunking("chunk text must be its non-blank span")
        if self.token_count < 1:
            raise InvalidChunking("a chunk holds at least one token")
        if not 0 <= self.first_block <= self.last_block:
            raise InvalidChunking("a chunk's block range must be ordered")


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
    block_starts = _block_starts(parsed)
    chunks: list[DraftChunk] = []
    first = 0
    while first < len(tokens):
        last = min(first + window_tokens, len(tokens)) - 1
        start, end = tokens[first][0], tokens[last][1]
        chunks.append(
            DraftChunk(
                ordinal=len(chunks),
                text=text[start:end],
                start=start,
                end=end,
                token_count=last - first + 1,
                first_block=bisect_right(block_starts, start) - 1,
                last_block=bisect_right(block_starts, end - 1) - 1,
            )
        )
        if last == len(tokens) - 1:
            break
        first = last + 1 - overlap_tokens
    return tuple(chunks)


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

- **`"\u200d"` and similar** are escapes for invisible characters (here the zero-width joiner). Always write invisible characters as escapes in source code: a literal one cannot be seen in review, and some, such as right-to-left overrides, can make code display differently from how it runs.
- **`token_spans` walks the text once.** `start` is where the current token began, or `-1` between tokens; `word` says whether it is a word token, which may grow, or punctuation, which is one character plus anything attached.
- **`_continues` decides whether the next character joins the current token.** The order of its checks is the policy: whitespace and the length cap end a token first; attached characters join; a word character joins a word token.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 3 — Coding step: tests

| Test | Proves |
|---|---|
| `test_a_small_document_is_one_chunk` | The small case, field by field. |
| `test_exactly_one_window_of_tokens_is_one_chunk` | The exact boundary. |
| `test_one_token_past_the_window_starts_a_second_overlapping_chunk` | One past the boundary, with the overlap. |
| `test_a_long_document_becomes_ordered_windows_that_overlap_exactly` | An oversized document: order, full windows, exact overlap. |
| `test_a_huge_word_is_split_into_bounded_tokens` | A single huge word. |
| `test_unicode_characters_are_never_split_from_their_marks` | Ukrainian, an accent, an emoji sequence. |
| `test_emoji_modifiers_stay_with_their_emoji` | A skin-tone modifier. |
| `test_no_token_is_longer_than_the_limit_whatever_it_is_made_of` (3 cases) | The review's break: joiners and stacked accents. |
| `test_an_empty_document_has_no_chunks` | The empty case. |
| `test_chunks_name_the_blocks_and_pdf_pages_they_came_from` | Provenance through to PDF pages. |
| `test_impossible_settings_are_refused` (4 cases) | The typed failure for settings. |
| `test_an_inconsistent_draft_chunk_is_refused` (6 cases) | The typed failure for `DraftChunk`. |
| `test_every_chunking_is_reversible_bounded_and_complete` | **Property:** for random text, window, and overlap, every chunk is reversible, holds 1 to `window` tokens, neighbours share exactly `overlap` tokens, and every token is covered. |

These five cases are the task's acceptance criterion: small, exact-boundary, oversized, Unicode, and empty documents. The property test uses the same deterministic Hypothesis settings as M03-T08.

**Action:** create `tests/test_chunking.py`:

```python
"""Tests for fixed-window chunking."""

from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from knowledge_service.chunking import (
    MAX_TOKEN_CHARACTERS,
    DraftChunk,
    InvalidChunking,
    chunk_document,
    reading_text,
    token_spans,
)
from knowledge_service.pdf_parser import parse_pdf
from knowledge_service.structure import ParsedDocument
from knowledge_service.text_parser import parse_text
from pdf_builder import build_pdf, text_page

# The same examples on every run, and no example database written to disk.
DETERMINISTIC = settings(derandomize=True, database=None, deadline=None)


def words(count: int) -> str:
    return " ".join(f"w{number}" for number in range(count))


def tokens_of(chunk: DraftChunk) -> list[str]:
    return [chunk.text[start:end] for start, end in token_spans(chunk.text)]


def test_a_small_document_is_one_chunk() -> None:
    parsed = parse_text("Read the security policy first.")

    [chunk] = chunk_document(parsed, window_tokens=8, overlap_tokens=2)

    assert chunk == DraftChunk(
        ordinal=0,
        text="Read the security policy first.",
        start=0,
        end=31,
        token_count=6,
        first_block=0,
        last_block=0,
    )


def test_exactly_one_window_of_tokens_is_one_chunk() -> None:
    chunks = chunk_document(parse_text(words(8)), window_tokens=8, overlap_tokens=2)

    assert [chunk.token_count for chunk in chunks] == [8]


def test_one_token_past_the_window_starts_a_second_overlapping_chunk() -> None:
    chunks = chunk_document(parse_text(words(9)), window_tokens=8, overlap_tokens=2)

    assert [tokens_of(chunk) for chunk in chunks] == [
        ["w0", "w1", "w2", "w3", "w4", "w5", "w6", "w7"],
        ["w6", "w7", "w8"],
    ]


def test_a_long_document_becomes_ordered_windows_that_overlap_exactly() -> None:
    chunks = chunk_document(parse_text(words(1000)), window_tokens=64, overlap_tokens=8)

    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.token_count == 64 for chunk in chunks[:-1])
    for before, after in pairwise(chunks):
        assert tokens_of(before)[-8:] == tokens_of(after)[:8]
    assert tokens_of(chunks[-1])[-1] == "w999"


def test_a_huge_word_is_split_into_bounded_tokens() -> None:
    chunks = chunk_document(parse_text("x" * 1000), window_tokens=4, overlap_tokens=0)

    assert all(len(chunk.text) <= 4 * MAX_TOKEN_CHARACTERS for chunk in chunks)
    assert "".join(chunk.text for chunk in chunks) == "x" * 1000


def test_unicode_characters_are_never_split_from_their_marks() -> None:
    text = "Кафе\u0301 👩\u200d💻 Привіт, світе! naïve"

    assert [text[start:end] for start, end in token_spans(text)] == [
        "Кафе\u0301",
        "👩\u200d💻",
        "Привіт",
        ",",
        "світе",
        "!",
        "naïve",
    ]


def test_emoji_modifiers_stay_with_their_emoji() -> None:
    text = "\U0001f44d\U0001f3fd ok"  # thumbs up with a skin tone

    assert [text[start:end] for start, end in token_spans(text)] == [
        "\U0001f44d\U0001f3fd",
        "ok",
    ]


@pytest.mark.parametrize(
    "text",
    ["!\u200d" * 5000, "a" + "\u0301" * 10000, "x\u200d" * 5000],
    ids=["joined-punctuation", "stacked-accents", "joined-letters"],
)
def test_no_token_is_longer_than_the_limit_whatever_it_is_made_of(text: str) -> None:
    spans = token_spans(text)

    assert all(end - start <= MAX_TOKEN_CHARACTERS for start, end in spans)
    assert "".join(text[start:end] for start, end in spans) == text


def test_an_empty_document_has_no_chunks() -> None:
    assert chunk_document(ParsedDocument(text="", blocks=())) == ()


def test_chunks_name_the_blocks_and_pdf_pages_they_came_from() -> None:
    parsed = parse_pdf(
        build_pdf(
            text_page((72, 720, "First page words.")),
            text_page((72, 720, "Second page words.")),
        )
    )

    chunks = chunk_document(parsed, window_tokens=5, overlap_tokens=1)

    assert [
        (parsed.blocks[chunk.first_block].page, parsed.blocks[chunk.last_block].page)
        for chunk in chunks
    ] == [(1, 2), (2, 2)]


@pytest.mark.parametrize(
    ("window", "overlap"),
    [(0, 0), (8, 8), (8, 9), (8, -1)],
    ids=["empty-window", "overlap-equals-window", "overlap-above-window", "negative"],
)
def test_impossible_settings_are_refused(window: int, overlap: int) -> None:
    with pytest.raises(InvalidChunking):
        chunk_document(parse_text("text"), window_tokens=window, overlap_tokens=overlap)


@pytest.mark.parametrize(
    "fields",
    [
        {"ordinal": -1},
        {"start": 5, "end": 5, "text": ""},
        {"text": "different length"},
        {"text": "   ", "start": 0, "end": 3},
        {"token_count": 0},
        {"first_block": 1, "last_block": 0},
    ],
    ids=[
        "negative-ordinal",
        "empty-span",
        "text-not-its-span",
        "blank-text",
        "no-tokens",
        "blocks-backwards",
    ],
)
def test_an_inconsistent_draft_chunk_is_refused(fields: dict[str, object]) -> None:
    valid: dict[str, object] = {
        "ordinal": 0,
        "text": "words",
        "start": 0,
        "end": 5,
        "token_count": 1,
        "first_block": 0,
        "last_block": 0,
    }

    with pytest.raises(InvalidChunking):
        DraftChunk(**(valid | fields))  # pyright: ignore[reportArgumentType]


@DETERMINISTIC
@given(
    paragraphs=st.lists(st.text(min_size=1, max_size=200), min_size=1, max_size=6),
    window=st.integers(min_value=1, max_value=20),
    data=st.data(),
)
def test_every_chunking_is_reversible_bounded_and_complete(
    paragraphs: list[str], window: int, data: st.DataObject
) -> None:
    overlap = data.draw(st.integers(min_value=0, max_value=window - 1))
    parsed = parse_text("\n\n".join(paragraphs))
    text = reading_text(parsed)
    every_token = token_spans(text)

    chunks = chunk_document(parsed, window_tokens=window, overlap_tokens=overlap)

    covered: set[tuple[int, int]] = set()
    for chunk in chunks:
        assert text[chunk.start : chunk.end] == chunk.text
        assert 1 <= chunk.token_count <= window
        inside = [span for span in every_token if chunk.start <= span[0] < chunk.end]
        assert len(inside) == chunk.token_count
        covered.update(inside)
    assert covered == set(every_token)
    for before, after in pairwise(chunks):
        shared = [span for span in every_token if after.start <= span[0] < before.end]
        assert len(shared) == overlap
```

Some details:

- **`pairwise(chunks)`** (from `itertools`) yields `(chunk0, chunk1)`, `(chunk1, chunk2)`, and so on. It is the clearer form of `zip(chunks, chunks[1:])`, and Ruff asks for it.
- **`valid | fields`** merges two dictionaries: the right side wins. Each case changes one field of an otherwise valid `DraftChunk`.
- **`data.draw(st.integers(min_value=0, max_value=window - 1))`** draws the overlap after the window, because a valid overlap depends on it.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_chunking.py -v
```

All 23 tests pass.

### Step 4 — No coding in this step: run the gates, then record evidence

**Action:** run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_chunking.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. In Design, also record these notes from the adversarial review:

- **Word tokens are not model tokens.** A model usually counts more, especially for Ukrainian, so the window size must leave room under the embedding model's limit.
- **Chunk size in characters is bounded by the tokens, not by whitespace.** A chunk has at most `window_tokens` tokens of at most 32 characters, but long runs of spaces between tokens are kept, because the chunk must slice back exactly.
- **Some emoji still count as several tokens.** A flag such as 🇺🇦 is two regional-indicator characters and becomes two tokens, so a window edge could cut it in two.
- **Code blocks are chunked like prose.** Structure-aware chunking (M03-T10) can treat them differently.
- **Offsets index the reading text, not `parsed.text`.** M03-T12 must build a Chunk's `source_anchor` from the blocks (`block.start`, `block.end`, `block.page`), not from the chunk offsets.

Answer these reflection questions in your own words:

1. Why do neighbouring chunks overlap? Describe what retrieval would miss with an overlap of 0.
2. Why does the chunker read the blocks instead of `parsed.text`, and how does a chunk still lead back to the original document?
3. The review made one "token" a million characters long. What would that have done to embedding, and which line fixed it?
4. Which test would let a real bug reach production if you deleted it? Name it and describe the bug.

**Check:** every command passes, and the evidence explains the design in your words.

## Completion checklist

- [ ] `chunking.py` has `reading_text`, `token_spans`, `chunk_document`, `DraftChunk`, and `InvalidChunking`.
- [ ] Chunks hold 1 to `window_tokens` tokens, neighbours share exactly `overlap_tokens`, ordinals run from 0, and every token is covered.
- [ ] Every chunk slices back exactly from the reading text, and its block range leads to the source blocks and PDF pages.
- [ ] No token is longer than 32 characters or contains whitespace, and accents, joiners, and emoji modifiers stay with their character.
- [ ] Impossible settings and inconsistent draft chunks raise `InvalidChunking`.
- [ ] Tests cover small, exact-boundary, oversized, Unicode, and empty documents, plus the property test.
- [ ] All 23 tests, the full deterministic suite, format, lint, types, `make check`, and `make docs-check` pass.
- [ ] The evidence file holds your invariant, predictions, design (including the five recorded notes), verification, and your own reflection.

When you're done, share your code or any error you hit, and I'll review it.
