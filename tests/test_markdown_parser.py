"""Tests for the Markdown parser and its structural blocks."""

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from knowledge_service.ingestion.parsers.markdown_parser import parse_markdown
from knowledge_service.ingestion.parsers.structure import (
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
