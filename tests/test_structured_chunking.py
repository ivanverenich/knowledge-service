"""Tests for structure-aware chunking."""

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from knowledge_service.ingestion.chunking import (
    DraftChunk,
    InvalidChunking,
    chunk_by_structure,
    reading_text,
    token_spans,
)
from knowledge_service.ingestion.parsers.markdown_parser import parse_markdown
from knowledge_service.ingestion.parsers.structure import ParsedDocument
from knowledge_service.ingestion.parsers.text_parser import parse_text

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
