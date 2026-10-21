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
