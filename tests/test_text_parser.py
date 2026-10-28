"""Tests for plain-text paragraph extraction."""

import pytest

from knowledge_service.ingestion.parsers.structure import BlockKind, DocumentTooLarge
from knowledge_service.ingestion.parsers.text_parser import parse_text


def test_blank_lines_separate_paragraphs_and_line_breaks_stay_inside() -> None:
    parsed = parse_text("First line\nstill first.\n\n  Second.  \n \t\nThird.\n")

    assert [block.text for block in parsed.blocks] == [
        "First line\nstill first.",
        "Second.",
        "Third.",
    ]
    assert all(block.kind is BlockKind.PARAGRAPH for block in parsed.blocks)


def test_offsets_slice_each_block_out_of_the_normalized_text() -> None:
    # Ukrainian on purpose; Ruff mistakes Cyrillic letters for look-alikes.
    parsed = parse_text("Привіт,\r\nсвіте.\r\n\r\nДругий абзац.")  # noqa: RUF001

    assert parsed.text == "Привіт,\nсвіте.\n\nДругий абзац."  # noqa: RUF001
    for block in parsed.blocks:
        assert parsed.text[block.start : block.end] == block.text


def test_whitespace_only_text_has_no_blocks() -> None:
    assert parse_text(" \n\n\t \r\n").blocks == ()


def test_text_over_the_limit_raises_a_typed_error() -> None:
    with pytest.raises(DocumentTooLarge, match="characters"):
        parse_text("x" * 11, max_characters=10)
