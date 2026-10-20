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
