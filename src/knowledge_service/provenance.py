"""Describe where each chunk came from, safely enough to show to a reader.

A citation needs two things from a chunk: a short excerpt a reader can
recognise, and a location they can follow back to the source. `ChunkSource`
holds both, built from the chunks, the parsed document they came from, and
the Source's identity for that document.

Everything in it is display-safe, and the policy is deliberately strict:

- A web address keeps only its scheme, host, port, and path. The user name,
  password, query, path parameters, and fragment are dropped, because any of
  them can carry a secret and no list of secret names is ever complete. The
  host is shown in its ASCII form, so a look-alike letter from another
  alphabet cannot pass for a familiar name.
- A relative link survives only as a plain path, so it cannot point at
  another host or run code.
- Displayed text holds no control, bidirectional, or other invisible
  characters, except the joiners that some scripts and emoji need. Anything
  shaped like `name:password@` is removed from it, and any web address in it
  is shown in its safe form.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from knowledge_service.chunking import BLOCK_SEPARATOR, DraftChunk, reading_text
from knowledge_service.structure import BlockKind, Link, ParsedDocument, StructuralBlock

EXCERPT_CHARACTERS = 200
UNKNOWN_LOCATION = "unknown source"

_ELLIPSIS = "\u2026"
# Zero-width non-joiner and joiner: Persian, Indic scripts, and emoji need them.
_JOINERS = frozenset("\u200c\u200d")
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_WEB_ADDRESS = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")
# `name:password@`, with or without a scheme in front of it, but not a
# `mailto:` address. It starts a word or follows a slash, as in `http://`.
_CREDENTIALS = re.compile(r"(?i)(?<![^\s/])(?!mailto:)[^\s/@:]+:[^\s/@]*@")
_QUERY_OR_FRAGMENT = re.compile(r"[?#]")
_HOST = re.compile(r"[a-z0-9.-]+")
# Characters a displayed address must not contain: they can end an HTML
# attribute or confuse a reader about where the link leads.
_UNSAFE_IN_URL = frozenset("\\\"'<>`^{|}")
_PATH_SYMBOLS = frozenset("._~%/+-")
_ADDRESS_SYMBOLS = frozenset("._+-@")


class InvalidChunkSource(ValueError):
    """A chunk's source cannot be described from the given document."""


@dataclass(frozen=True, slots=True)
class ChunkSource:
    """What a citation shows for one chunk, and where it leads.

    `anchor` is the value to store as the Chunk's `source_anchor`: the
    location plus `#page=N` for a PDF, or `#offset=START-END` otherwise.
    `source_start` and `source_end` index the parsed text, for the blocks the
    chunk covers. `table_header` is the table's first row when the chunk
    starts partway through a table, so its rows can still be read.
    """

    excerpt: str
    location: str
    anchor: str
    heading_path: str | None
    first_page: int | None
    last_page: int | None
    source_start: int
    source_end: int
    links: tuple[Link, ...]
    table_header: str | None


def chunk_sources(
    parsed: ParsedDocument,
    chunks: Sequence[DraftChunk],
    *,
    external_id: str,
    source_url: str | None = None,
) -> tuple[ChunkSource, ...]:
    """Describe every chunk of one document, in order."""
    if not external_id.strip():
        raise InvalidChunkSource("a source needs a non-empty external ID")
    location = _location(external_id, source_url)
    text = reading_text(parsed)
    starts: list[int] = []
    offset = 0
    for block in parsed.blocks:
        starts.append(offset)
        offset += len(block.text) + len(BLOCK_SEPARATOR)
    return tuple(_describe(parsed, text, starts, chunk, location) for chunk in chunks)


def display_url(url: str) -> str | None:
    """Return `url` safe to show, or None when it is not a web address."""
    if any(
        _invisible(character) or character.isspace() or character in _UNSAFE_IN_URL
        for character in url
    ):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None
    host = parts.hostname
    if ":" in host:
        if not all(character in "0123456789abcdef:." for character in host):
            return None
        host = f"[{host}]"  # an IPv6 address
    else:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return None
        if not _HOST.fullmatch(host):
            return None
    if port is not None:
        host = f"{host}:{port}"
    path = "/".join(segment.split(";", 1)[0] for segment in parts.path.split("/"))
    return urlunsplit((parts.scheme.lower(), host, path, "", ""))


def _describe(
    parsed: ParsedDocument,
    text: str,
    starts: list[int],
    chunk: DraftChunk,
    location: str,
) -> ChunkSource:
    blocks = parsed.blocks
    if chunk.last_block >= len(blocks) or not (
        starts[chunk.first_block]
        <= chunk.start
        < starts[chunk.first_block] + len(blocks[chunk.first_block].text)
        and starts[chunk.last_block]
        < chunk.end
        <= starts[chunk.last_block] + len(blocks[chunk.last_block].text)
        and text[chunk.start : chunk.end] == chunk.text
    ):
        raise InvalidChunkSource("the chunk does not come from this document")
    first = blocks[chunk.first_block]
    last = blocks[chunk.last_block]
    if first.page is not None:
        anchor = f"{location}#page={first.page}"
    else:
        anchor = f"{location}#offset={first.start}-{last.end}"
    header = None
    first_row = first.text.split("\n", 1)[0]
    if first.kind is BlockKind.TABLE and chunk.start >= starts[chunk.first_block] + len(
        first_row
    ):
        header = _display_text(first_row)
    return ChunkSource(
        excerpt=_excerpt(chunk.text),
        location=location,
        anchor=anchor,
        heading_path=chunk.heading_path,
        first_page=first.page,
        last_page=last.page,
        source_start=first.start,
        source_end=last.end,
        links=_links(blocks[chunk.first_block : chunk.last_block + 1], chunk.text),
        table_header=header,
    )


def _location(external_id: str, source_url: str | None) -> str:
    """Prefer a safe source URL, then the external ID shown safely."""
    if source_url is not None and (url := display_url(source_url)):
        return url
    shown = _display_text(external_id)
    if _SCHEME.match(shown):
        return display_url(shown) or UNKNOWN_LOCATION
    # A query or fragment can carry a secret, and a leading `//` reads as a host.
    shown = shown.split("?", 1)[0].split("#", 1)[0]
    while shown.startswith("//"):
        shown = shown[1:]
    return shown or UNKNOWN_LOCATION


def _invisible(character: str) -> bool:
    """Control, format (including bidirectional), and surrogate characters."""
    return unicodedata.category(character) in {"Cc", "Cf", "Cs"}


def _display_text(value: str) -> str:
    """Clean text for display and show any web address in its safe form."""
    without_credentails = _CREDENTIALS.sub("", _clean(value))
    return _WEB_ADDRESS.sub(
        lambda match: _shown_url(match.group()), without_credentails
    )


def _clean(value: str) -> str:
    """Drop invisible characters, except joiners, and collapse whitespace."""
    visible = "".join(
        " " if character.isspace() else character
        for character in value
        if character.isspace() or character in _JOINERS or not _invisible(character)
    )
    return " ".join(visible.split())


def _shown_url(url: str) -> str:
    """Show an address found in text; its query and fragment are cut off first."""
    return display_url(_QUERY_OR_FRAGMENT.split(url, 1)[0]) or "[link]"


def _links(blocks: Sequence[StructuralBlock], chunk_text: str) -> tuple[Link, ...]:
    """Return the safe links whose visible text appears in the chunk."""
    links: dict[Link, None] = {}
    for block in blocks:
        for link in block.links:
            text = _display_text(link.text)
            target = _safe_target(link.target)
            if text and target is not None and link.text in chunk_text:
                links.setdefault(Link(text=text, target=target))
    return tuple(links)


def _safe_target(target: str) -> str | None:
    """Keep web links made safe, plain `mailto:` addresses, and plain paths."""
    if _SCHEME.match(target):
        scheme, rest = target.split(":", 1)
        if scheme.lower() in {"http", "https"}:
            return display_url(target)
        address = rest.split("?", 1)[0]
        if scheme.lower() == "mailto" and _plain(address, _ADDRESS_SYMBOLS):
            return f"mailto:{address}"
        return None
    path = target.split("?", 1)[0].split("#", 1)[0]
    if path.startswith("//") or not _plain(path, _PATH_SYMBOLS):
        return None
    return path


def _plain(value: str, symbols: frozenset[str]) -> bool:
    """Whether `value` is non-empty letters, digits, and the given symbols."""
    return bool(value) and all(
        character.isalnum() or character in symbols for character in value
    )


def _excerpt(text: str) -> str:
    """Return the chunk's first words, at most `EXCERPT_CHARACTERS` long."""
    shown = _display_text(text)
    if len(shown) <= EXCERPT_CHARACTERS:
        return shown
    limit = EXCERPT_CHARACTERS - 1
    cut = shown[:limit]
    # Cut between words; a text without spaces is cut between characters.
    cut = cut[: cut.rindex(" ")] if " " in cut else shown[: _boundary(shown, limit)]
    return cut.rstrip() + _ELLIPSIS


def _boundary(text: str, index: int) -> int:
    """Move `index` back until it does not split a character cluster."""
    while index > 0 and (_attached(text[index]) or text[index - 1] in _JOINERS):
        index -= 1
    run = 0
    while index - run > 0 and _regional(text[index - 1 - run]):
        run += 1
    if run % 2 and _regional(text[index]):
        index -= 1  # never keep half of a flag
    return index


def _attached(character: str) -> bool:
    """Accents, joiners, and emoji modifiers belong to the character before."""
    return (
        unicodedata.category(character).startswith("M")
        or character in _JOINERS
        or "\ufe00" <= character <= "\ufe0f"
        or "\U0001f3fb" <= character <= "\U0001f3ff"
        or "\U000e0020" <= character <= "\U000e007f"
    )


def _regional(character: str) -> bool:
    """A regional indicator: two of them make one flag."""
    return "\U0001f1e6" <= character <= "\U0001f1ff"
