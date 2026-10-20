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
            raise InvalidChunking("chunk offset must satisfy 0 <= start < end")
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
