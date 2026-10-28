"""Tests for fixed-window chunking."""

from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from knowledge_service.ingestion.chunking import (
    MAX_TOKEN_CHARACTERS,
    DraftChunk,
    InvalidChunking,
    chunk_document,
    reading_text,
    token_spans,
)
from knowledge_service.ingestion.parsers.pdf_parser import parse_pdf
from knowledge_service.ingestion.parsers.structure import ParsedDocument
from knowledge_service.ingestion.parsers.text_parser import parse_text
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
