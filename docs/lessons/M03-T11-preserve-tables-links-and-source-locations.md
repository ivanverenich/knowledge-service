# M03-T11 — Preserve tables, links, and source locations

Source task: [M03-T11](../curriculum/milestones/M03-local-ingestion/M03-T11-preserve-tables-links-and-source-locations.md)

## What you will do

This lesson includes coding: Steps 2–4 write code, while Steps 1 and 5 do not.

A chunk is what a citation will point at. When the service answers a question later (M06), it shows a short excerpt of each chunk and a link back to where it came from. This lesson builds that "where it came from" for every chunk, and makes everything in it safe to show.

| Requirement | How this lesson meets it |
|---|---|
| Normalized table text | Parsers already write a table as one row per line, cells joined by ` | `. A chunk that starts partway through a table also carries the table's header row, so its rows still make sense. |
| Link targets | Each chunk keeps the links whose visible text it contains, with display-safe targets. |
| Page, heading, offset metadata | First and last PDF page, the heading path, and offsets into the source text. |
| Display-safe source URLs | A strict policy: a shown address never carries a user name, password, query, or fragment, and never leads to another host or runs code. |
| Citation fixture assertions | Tests rebuild an excerpt and a source location from the handbook fixture and from a PDF, and slice the original text with the recorded offsets. |

Terms used in this lesson:

- **Excerpt:** the first words of a chunk, at most 200 characters, cut between words.
- **Anchor:** one string that says where a chunk lives, for example `reports/q3.pdf#page=3` or `guides/handbook.md#offset=120-455`. It is what M03-T12 will store in `Chunk.source_anchor`.
- **Display-safe:** safe to show on a screen: nothing invisible, no secrets, and no link that misleads the reader.

## Before you begin

- **A new module, `provenance.py`, and no change to the chunkers.** `chunk_sources(parsed, chunks, external_id=..., source_url=...)` reads the chunkers' output and returns one `ChunkSource` per chunk.
- **Where a source URL comes from.** Sources do not report a URL yet. `source_url` is optional, so the local directory passes nothing and the location is the file's relative path; a later Confluence adapter can pass the page URL.
- **Why the policy is strict.** An adversarial review broke two looser versions of this module: credentials survived in link text, secrets hid behind `;` in a query, relative links such as `\\evil.com` led browsers to another host, and a guessed list of secret parameter names was both too broad (it dropped `keyword` and `author`) and too narrow (it kept `pw` and `jwt`). Dropping every query and fragment is simple and cannot be fooled. The cost is that a link such as `?id=123` loses its query.
- **A bug fix in the HTML parser.** The review also found that `parse_html('<a href="http://[broken">')` raised a raw `ValueError`, which would break M03-T06's promise that `normalize` never raises. Step 2 fixes it.
- **Files you will touch:**
  - `src/knowledge_service/html_parser.py` and `tests/test_html_parser.py`
  - new `src/knowledge_service/provenance.py` and `tests/test_provenance.py`
  - a new evidence file

  Leave the task spec and `PROGRESS.md` alone.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your prediction

**Invariant:** for every chunk, `chunk_sources` returns an excerpt, a location, and an anchor, and the recorded source offsets slice the original text of the blocks the chunk came from. Everything shown is display-safe: no control or bidirectional characters, no user name, password, query, or fragment in any address, and no link that leads to another host or runs code. A chunk that does not belong to the document is refused with `InvalidChunkSource`.

**Action:** create `docs/evidence/M03-T11-chunk-provenance.md`:

```markdown
# M03-T11 — Chunk provenance evidence

Completed: pending

## Invariant

Pending.

## Prediction

Before writing code, say what excerpt, location, and anchor you expect for:
the Vacation chunk of the handbook fixture, a two-page PDF, a source URL with
a user name, a password, and a query, a link written as
`https://me:secret@docs.example.com/guide?token=t#top`, a relative link
`../policy.md`, an HTML link to `\\evil.com`, text containing
`//admin:hunter2@db.internal`, a window that starts in the middle of a table,
an external ID of `javascript:alert(1)`, and a chunk from another document.

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Check:** the file holds your invariant and your predictions.

### Step 2 — Coding step: stop the HTML parser raising on a broken link

`urlsplit` raises `ValueError` for some addresses, such as an unclosed IPv6 bracket. The HTML parser called it without a guard, so one such link made the whole page fail, and `normalize` raised instead of quarantining.

**Action:** in `src/knowledge_service/html_parser.py`, replace `_safe_target` with:

```python
def _safe_target(href: str | None) -> str | None:
    """Return the link target when it cannot run code, otherwise None."""
    if href is None:
        return None
    target = href.strip()
    if not target or target.startswith("#"):
        return None
    try:
        scheme = urlsplit(target).scheme.lower()
    except ValueError:  # for example, an unclosed IPv6 address
        return None
    return target if scheme in _SAFE_SCHEMES or not scheme else None
```

Add this test at the end of `tests/test_html_parser.py`:

```python
def test_a_link_target_that_cannot_be_parsed_is_dropped_not_raised() -> None:
    # urlsplit raises ValueError for an unclosed IPv6 address.
    parsed = parse_html('<p>See <a href="http://[broken">this</a>.</p>')

    [block] = parsed.blocks

    assert block.text == "See this."
    assert block.links == ()
```

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_html_parser.py -q` gives 28 passed. The new test fails without the fix.

### Step 3 — Coding step: the provenance module

| Piece | What it does |
|---|---|
| `ChunkSource` | Excerpt, location, anchor, heading path, first and last page, source offsets, links, table header. |
| `chunk_sources` | Checks the external ID, picks the location, computes where each block starts in the reading text, and describes every chunk. |
| `display_url` | The strict address policy: `http`/`https` only; scheme, ASCII host, port, and path; nothing else. |
| `_describe` | Checks the chunk belongs to the document, then builds its `ChunkSource`. |
| `_location` | A safe source URL if given, otherwise the external ID shown safely. |
| `_links`, `_safe_target` | Links whose text appears in the chunk, with targets that are safe web addresses, plain `mailto:` addresses, or plain relative paths. |
| `_excerpt`, `_boundary` | The first words, cut between words or, in text without spaces, between whole characters. |
| `_display_text`, `_clean` | Remove invisible characters, credentials, and unsafe addresses from anything shown. |

Decisions:

- **Drop the whole query, fragment, and path parameters.** Any of them can hold a secret, and a list of secret names can never be complete.
- **Show the host in ASCII.** `host.encode("idna")` turns a look-alike such as `\u0430pple.com` (with a Cyrillic letter) into `xn--pple-43d.com`, so a reader cannot be fooled by a familiar-looking name. Anything but letters, digits, dots, and hyphens is refused, which also refuses a percent-encoded host such as `evil%2Ecom`.
- **A relative link must be a plain path.** Letters, digits, and `. _ ~ % / + -` only, not starting with `//`. That refuses backslashes, colons, and query strings, so a relative link can neither reach another host nor run a script.
- **Remove credentials from any shown text first.** `_CREDENTIALS` removes anything shaped like `name:password@` that starts a word or follows a slash, except `mailto:`; then each web address found is shown through `display_url`, after its query and fragment are cut off.
- **Keep the two joiners.** Zero-width non-joiner and joiner (`\u200c`, `\u200d`) change how Persian, Indic scripts, and emoji display, so they are kept; every other control, format, and bidirectional character is removed.
- **Check that the chunk belongs.** Its offsets must slice the reading text to its text, and its start and end must fall inside the blocks it names; otherwise `InvalidChunkSource`.
- **Source offsets are block-level.** `source_start` and `source_end` cover the whole first and last block in the original text, because only blocks know their place in the source. For HTML they point into the raw page.

**Action:** create `src/knowledge_service/provenance.py`:

```python
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


def _display_text(value: str) -> str:
    """Clean text for display and show any web address in its safe form."""
    without_credentials = _CREDENTIALS.sub("", _clean(value))
    return _WEB_ADDRESS.sub(
        lambda match: _shown_url(match.group()), without_credentials
    )


def _shown_url(url: str) -> str:
    """Show an address found in text; its query and fragment are cut off first."""
    return display_url(_QUERY_OR_FRAGMENT.split(url, 1)[0]) or "[link]"


def _clean(value: str) -> str:
    """Drop invisible characters, except joiners, and collapse whitespace."""
    visible = "".join(
        " " if character.isspace() else character
        for character in value
        if character.isspace() or character in _JOINERS or not _invisible(character)
    )
    return " ".join(visible.split())


def _invisible(character: str) -> bool:
    """Control, format (including bidirectional), and surrogate characters."""
    return unicodedata.category(character) in {"Cc", "Cf", "Cs"}
```

Some details:

- **`_shown_url` and `_QUERY_OR_FRAGMENT.split(url, 1)[0]`** cut an address at the first `?` or `#` before checking it, so a quote inside a query cannot make the whole address unreadable.
- **`(?<![^\s/])`** in `_CREDENTIALS` is a lookbehind: the match must start at the beginning of the text, after whitespace, or after `/`, as in `http://user:pw@`. **`(?!mailto:)`** is a lookahead that skips email links.
- **`links.setdefault(Link(...))`** uses a dictionary as an ordered set: it keeps the first occurrence of each link and drops repeats in one step.
- **Every special character is written as an escape,** such as `"\u2026"` for the ellipsis. Never type invisible characters literally in source code.

**Check:** `UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck lint` passes.

### Step 4 — Coding step: tests

| Test | Proves |
|---|---|
| `test_a_citation_shows_an_excerpt_and_leads_back_to_its_section` | The acceptance criterion: an excerpt and a location rebuilt from the handbook fixture, and the offsets slice the original Markdown. |
| `test_every_window_of_a_long_paragraph_leads_back_to_it` | Fallback windows lead back to their paragraph. |
| `test_a_pdf_citation_names_its_pages` | PDF pages and `#page=` anchors. |
| `test_a_source_url_is_shown_instead_of_the_external_id_and_made_safe` | The source URL wins and loses its secrets. |
| `test_a_long_chunk_gets_a_short_excerpt_cut_between_words` | The excerpt length and cut. |
| `test_an_excerpt_holds_no_invisible_characters` | Zero-width space, right-to-left override, soft hyphen. |
| `test_links_keep_only_display_safe_targets` | Credentials, query, and fragment dropped; `ftp:` refused; relative and `mailto:` kept. |
| `test_html_links_never_lead_to_another_host_or_keep_a_query` | Backslash links and a relative query, from real HTML. |
| `test_credentials_and_queries_never_show_in_text` (4 cases) | Credentials without a scheme, quotes, and a plain `mailto:` that stays. |
| `test_a_web_address_in_shown_text_loses_its_secrets` | An autolink's visible text. |
| `test_an_external_id_is_shown_safely` (6 cases) | A path, credential and script IDs, an embedded URL, a query, a leading `//`. |
| `test_an_excerpt_never_splits_a_character_from_its_accent_or_a_flag` | Text with no spaces. |
| `test_joiners_that_scripts_need_are_kept` | A Persian word with a zero-width non-joiner. |
| `test_a_window_inside_a_table_carries_the_table_header` | The header row on later windows. |
| `test_display_url` (16 cases) | The address policy, case by case. |
| `test_a_blank_external_id_is_refused` | A typed failure. |
| `test_a_chunk_from_another_document_is_refused` (3 cases) | Other text, a wrong block, a block out of range. |
| `test_every_source_is_display_safe_and_points_inside_the_document` | **Property:** for random text, every source is display-safe and points inside the document. |

The tests from the two review rounds fail against the earlier versions: 12 against the first, and 9 more against the second.

**Action:** create `tests/test_provenance.py`:

```python
"""Tests for chunk provenance: excerpts and source locations for citations."""

import unicodedata
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from knowledge_service.chunking import (
    DraftChunk,
    chunk_by_structure,
    chunk_document,
)
from knowledge_service.html_parser import parse_html
from knowledge_service.markdown_parser import parse_markdown
from knowledge_service.pdf_parser import parse_pdf
from knowledge_service.provenance import (
    EXCERPT_CHARACTERS,
    UNKNOWN_LOCATION,
    ChunkSource,
    InvalidChunkSource,
    chunk_sources,
    display_url,
)
from knowledge_service.structure import Link
from knowledge_service.text_parser import parse_text
from pdf_builder import build_pdf, text_page

HANDBOOK = Path(__file__).parent / "fixtures" / "chunking" / "handbook.md"
# The same examples on every run, and no example database written to disk.
DETERMINISTIC = settings(derandomize=True, database=None, deadline=None)
ELLIPSIS = "\u2026"
JOINERS = frozenset("\u200c\u200d")  # kept on purpose: scripts and emoji need them


def handbook_sources() -> tuple[str, tuple[ChunkSource, ...]]:
    source = HANDBOOK.read_text(encoding="utf-8")
    parsed = parse_markdown(source)
    chunks = chunk_by_structure(parsed, min_tokens=8, max_tokens=24, overlap_tokens=4)
    return parsed.text, chunk_sources(parsed, chunks, external_id="guides/handbook.md")


def test_a_citation_shows_an_excerpt_and_leads_back_to_its_section() -> None:
    text, sources = handbook_sources()

    vacation = sources[1]

    assert vacation.excerpt == (
        "Vacation Request vacation at least two weeks ahead. Your team lead"
        " approves it in the HR system."
    )
    assert vacation.heading_path == "Team handbook > Vacation"
    assert vacation.anchor == (
        f"guides/handbook.md#offset={vacation.source_start}-{vacation.source_end}"
    )
    assert text[vacation.source_start : vacation.source_end].startswith(
        "## Vacation\n\nRequest vacation"
    )


def test_every_window_of_a_long_paragraph_leads_back_to_it() -> None:
    text, sources = handbook_sources()

    windows = [source for source in sources if "Laptops" in (source.heading_path or "")]

    assert len(windows) == 3
    assert len({(source.source_start, source.source_end) for source in windows}) == 2
    assert all(
        "Report a lost laptop" in text[source.source_start : source.source_end]
        for source in windows
    )


def test_a_pdf_citation_names_its_pages() -> None:
    parsed = parse_pdf(
        build_pdf(
            text_page((72, 720, "First page words.")),
            text_page((72, 720, "Second page words.")),
        )
    )
    chunks = chunk_document(parsed, window_tokens=5, overlap_tokens=1)

    sources = chunk_sources(parsed, chunks, external_id="reports/q3.pdf")

    assert [(source.first_page, source.last_page) for source in sources] == [
        (1, 2),
        (2, 2),
    ]
    assert [source.anchor for source in sources] == [
        "reports/q3.pdf#page=1",
        "reports/q3.pdf#page=2",
    ]


def test_a_source_url_is_shown_instead_of_the_external_id_and_made_safe() -> None:
    parsed = parse_text("Quarterly results.")

    [source] = chunk_sources(
        parsed,
        chunk_document(parsed),
        external_id="space/123",
        source_url="https://bot:hunter2@wiki.example.com/pages/123?api_key=x&v=2",
    )

    assert source.location == "https://wiki.example.com/pages/123"
    assert source.anchor.startswith("https://wiki.example.com/pages/123#offset=")


def test_a_long_chunk_gets_a_short_excerpt_cut_between_words() -> None:
    text = " ".join(f"word{number}" for number in range(200))
    parsed = parse_text(text)

    [source] = chunk_sources(parsed, chunk_document(parsed), external_id="a.txt")

    shown = source.excerpt.removesuffix(ELLIPSIS)
    assert len(source.excerpt) <= EXCERPT_CHARACTERS
    assert source.excerpt.endswith(ELLIPSIS)
    assert text.startswith(shown)
    assert text[len(shown)] == " "  # the cut falls between two words


def test_an_excerpt_holds_no_invisible_characters() -> None:
    parsed = parse_text("Pay\u200b the\u202e invoice\u00ad today.")

    [source] = chunk_sources(parsed, chunk_document(parsed), external_id="a.txt")

    assert source.excerpt == "Pay the invoice today."


def test_links_keep_only_display_safe_targets() -> None:
    # "[text]" "(target)" keeps the docs link checker from reading these
    # Markdown links as links to check; Python joins the strings into one.
    parsed = parse_markdown(
        "Read [the guide](https://me:secret@docs.example.com/guide?token=t&lang=uk#top),"
        " [policy]"
        "(../policy.md), [mail](mailto:help@example.com?subject=hi),"
        " and [ftp]"
        "(ftp://files.example.com/a).\n"
    )

    [source] = chunk_sources(parsed, chunk_document(parsed), external_id="a.md")

    assert source.links == (
        Link(text="the guide", target="https://docs.example.com/guide"),
        Link(text="policy", target="../policy.md"),
        Link(text="mail", target="mailto:help@example.com"),
    )


def test_html_links_never_lead_to_another_host_or_keep_a_query() -> None:
    parsed = parse_html(
        '<p><a href="\\\\evil.com\\x">a</a> <a href="/\\evil.com">b</a>'
        ' <a href="/reset?token=SECRET">c</a> <a href="http://[evil">d</a></p>'
    )

    [source] = chunk_sources(parsed, chunk_document(parsed), external_id="a.html")

    assert source.links == (Link(text="c", target="/reset"),)


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("Ask //admin:hunter2@db.internal now.", "Ask //db.internal now."),
        ("Use http://u:pa'ss@db.internal/x today.", "Use http://db.internal/x today."),
        ('Open https://h.com/x?token="S" now.', "Open https://h.com/x now."),
        (
            "Mail mailto:help@example.com please.",
            "Mail mailto:help@example.com please.",
        ),
    ],
    ids=["no-scheme", "quote-in-password", "quote-in-query", "plain-mailto"],
)
def test_credentials_and_queries_never_show_in_text(text: str, shown: str) -> None:
    parsed = parse_text(text)

    [source] = chunk_sources(parsed, chunk_document(parsed), external_id="a.txt")

    assert source.excerpt == shown


def test_a_web_address_in_shown_text_loses_its_secrets() -> None:
    parsed = parse_markdown("See <https://user:pw@docs.example.com/x?token=S> now.\n")

    [source] = chunk_sources(parsed, chunk_document(parsed), external_id="a.md")

    assert source.excerpt == "See https://docs.example.com/x now."
    assert source.links == (
        Link(text="https://docs.example.com/x", target="https://docs.example.com/x"),
    )


@pytest.mark.parametrize(
    ("external_id", "location"),
    [
        ("guides/team handbook.md", "guides/team handbook.md"),
        ("https://u:pw@wiki.example.com/doc?token=S", "https://wiki.example.com/doc"),
        ("javascript:alert(1)", UNKNOWN_LOCATION),
        ("notes http://u:p@h.com/?t=S", "notes http://h.com/"),
        ("a.md?token=SECRET#x", "a.md"),
        ("//user:pw@evil.com/a", "/evil.com/a"),
    ],
    ids=["path", "credential-url", "script", "embedded-url", "query", "host-like"],
)
def test_an_external_id_is_shown_safely(external_id: str, location: str) -> None:
    parsed = parse_text("Text.")

    [source] = chunk_sources(parsed, chunk_document(parsed), external_id=external_id)

    assert source.location == location


def test_an_excerpt_never_splits_a_character_from_its_accent_or_a_flag() -> None:
    accents = parse_text("e\u0301" * 300)
    flags = parse_text("\U0001f1fa\U0001f1e6" * 150)

    [accented] = chunk_sources(accents, chunk_document(accents), external_id="a.txt")
    flagged, *_ = chunk_sources(flags, chunk_document(flags), external_id="f.txt")

    assert accented.excerpt.removesuffix(ELLIPSIS).endswith("e\u0301")
    assert len(flagged.excerpt.removesuffix(ELLIPSIS)) % 2 == 0


def test_joiners_that_scripts_need_are_kept() -> None:
    word = "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645"  # Persian, with a ZWNJ
    parsed = parse_text(word)

    [source] = chunk_sources(parsed, chunk_document(parsed), external_id="fa.txt")

    assert source.excerpt == word


def test_a_window_inside_a_table_carries_the_table_header() -> None:
    rows = "".join(f"| tool{number} | {number}.0 |\n" for number in range(30))
    parsed = parse_markdown("| Tool | Version |\n| --- | --- |\n" + rows)
    chunks = chunk_document(parsed, window_tokens=40, overlap_tokens=0)

    sources = chunk_sources(parsed, chunks, external_id="tools.md")

    assert sources[0].table_header is None
    assert [source.table_header for source in sources[1:]] == ["Tool | Version"] * (
        len(sources) - 1
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/a?b=1#c", "https://example.com/a"),
        ("HTTP://Example.com/A", "http://example.com/A"),
        ("https://user:pass@example.com/", "https://example.com/"),
        ("https://example.com/?pw=x;token=y", "https://example.com/"),
        ("https://example.com/a;jsessionid=x/b", "https://example.com/a/b"),
        ("https:\\evil.com/a", None),
        ("https://[::1]:8080/a", "https://[::1]:8080/a"),
        ("https://\u0430pple.com/", "https://xn--pple-43d.com/"),
        ("http://evil%2Ecom/", None),
        ('http://evil.com"onmouseover=alert(1)/', None),
        ("http://[fe80::1%25eth0]/", None),
        ("http://" + "a" * 64 + ".com/", None),
        ("javascript:alert(1)", None),
        ("ftp://example.com/a", None),
        ("https:///no-host", None),
        ("https://exa mple.com/", None),
        ("https://example.com:99999/", None),
        ("https://example.com/\u202epath", None),
    ],
    ids=[
        "fragment-dropped",
        "scheme-and-host-lowercased",
        "credentials-dropped",
        "query-dropped",
        "path-parameter-dropped",
        "backslash",
        "ipv6",
        "homograph-shown-as-ascii",
        "encoded-host",
        "quote",
        "ipv6-zone",
        "label-too-long",
        "javascript",
        "ftp",
        "no-host",
        "space",
        "bad-port",
        "bidi-override",
    ],
)
def test_display_url(url: str, expected: str | None) -> None:
    assert display_url(url) == expected


def test_a_blank_external_id_is_refused() -> None:
    parsed = parse_text("Text.")

    with pytest.raises(InvalidChunkSource, match="external ID"):
        chunk_sources(parsed, chunk_document(parsed), external_id="  ")


@pytest.mark.parametrize(
    "chunk",
    [
        DraftChunk(
            ordinal=0,
            text="Another",
            start=0,
            end=7,
            token_count=1,
            first_block=0,
            last_block=0,
        ),
        DraftChunk(
            ordinal=0,
            text="alpha beta",
            start=0,
            end=10,
            token_count=2,
            first_block=1,
            last_block=1,
        ),
        DraftChunk(
            ordinal=0,
            text="alpha beta",
            start=0,
            end=10,
            token_count=2,
            first_block=0,
            last_block=5,
        ),
    ],
    ids=["other-text", "wrong-block", "block-out-of-range"],
)
def test_a_chunk_from_another_document_is_refused(chunk: DraftChunk) -> None:
    parsed = parse_text("alpha beta\n\ngamma delta")

    with pytest.raises(InvalidChunkSource, match="does not come from"):
        chunk_sources(parsed, [chunk], external_id="a.txt")


@DETERMINISTIC
@given(
    paragraphs=st.lists(
        st.text(min_size=1, max_size=150).filter(str.strip), min_size=1, max_size=6
    ),
    window=st.integers(min_value=1, max_value=40),
)
def test_every_source_is_display_safe_and_points_inside_the_document(
    paragraphs: list[str], window: int
) -> None:
    parsed = parse_markdown("\n\n".join(paragraphs))
    chunks = chunk_document(parsed, window_tokens=window, overlap_tokens=0)

    sources = chunk_sources(parsed, chunks, external_id="doc.md")

    assert len(sources) == len(chunks)
    for source in sources:
        # A chunk of only control characters has nothing visible to show.
        assert len(source.excerpt) <= EXCERPT_CHARACTERS
        assert not any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            and character not in JOINERS
            for character in source.excerpt
        )
        assert source.anchor.startswith("doc.md#offset=")
        assert 0 <= source.source_start < source.source_end <= len(parsed.text)
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_provenance.py tests/test_html_parser.py -v
```

All 73 tests pass: 45 in `test_provenance.py` and 28 in `test_html_parser.py`.

### Step 5 — No coding in this step: run the gates, then record evidence

**Action:** run, in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_provenance.py tests/test_html_parser.py -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make format-check lint typecheck
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest -m "not integration and not live" -q
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Fill in `## Design`, `## Verification`, and `## Reflection`. In Design, also record these known limits:

- **Queries are always dropped,** so a link such as `?id=123` loses its query.
- **Source offsets are block-level.** Several windows of one long paragraph or table share the same anchor.
- **A PDF anchor names only the first page,** even when `last_page` is later.
- **Decomposed Hangul and Indic conjuncts can still be cut** in an excerpt of text without spaces; splitting them correctly needs Unicode's full text-segmentation rules, which the standard library does not provide.
- **Only what is shown is redacted.** The chunk text itself is document content and is stored and embedded unchanged.
- **A caller must still escape text for HTML** when it renders a citation; display-safe is not the same as HTML-escaped.
- **A Windows-style external ID** such as `C:\Users\a.md` is shown as `unknown source`.

Answer these reflection questions in your own words:

1. Why does the module drop every query parameter instead of only the ones that look secret? What did the review find with the list of names?
2. How could a relative link such as `\\evil.com\\x` send a reader to another site, and which rule now stops it?
3. A citation shows an excerpt and an anchor. How would you use `source_start` and `source_end` to show the reader the original text, and why do several windows share the same range?
4. Which test would let a real leak reach a user if you deleted it? Name it and describe the leak.

**Check:** every command passes, and the evidence explains the design in your words.

## Completion checklist

- [ ] `parse_html` drops a link whose target cannot be parsed instead of raising.
- [ ] `provenance.py` has `ChunkSource`, `chunk_sources`, `display_url`, and `InvalidChunkSource`.
- [ ] Every chunk gets an excerpt, a location, an anchor, its heading path, pages, source offsets, safe links, and a table header when it starts inside a table.
- [ ] No shown text or address contains invisible characters, credentials, a query, or a fragment, and no link leads to another host or runs code.
- [ ] A blank external ID and a chunk from another document raise `InvalidChunkSource`.
- [ ] Citation fixture tests rebuild an excerpt and a source location from the handbook and from a PDF.
- [ ] All 73 tests, the full deterministic suite, format, lint, types, `make check`, and `make docs-check` pass.
- [ ] The evidence file holds your invariant, predictions, design (including the seven known limits), verification, and your own reflection.

When you're done, share your code or any error you hit, and I'll review it.
