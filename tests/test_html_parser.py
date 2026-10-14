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
