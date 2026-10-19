"""Tests for PDF text extraction: pages, tables, scans, encryption, corruption."""

import tracemalloc
from typing import Literal

import pytest

from knowledge_service.pdf_parser import parse_pdf
from knowledge_service.structure import (
    BlockKind,
    CorruptDocument,
    DocumentTooLarge,
    EncryptedDocument,
    InvalidBlock,
    NoTextLayer,
    StructuralBlock,
)
from pdf_builder import (
    IMAGE_ONLY,
    build_pdf,
    compressed_streams_pdf,
    encrypt,
    object_stream_pdf,
    text_page,
)

TWO_PAGES = build_pdf(
    text_page(
        (72, 720, "Onboarding guide"),
        (72, 690, "Read the security policy before"),
        (72, 676, "your first deploy."),
    ),
    text_page((72, 720, "Second page paragraph.")),
)


def test_paragraphs_keep_their_page_numbers_in_order() -> None:
    parsed = parse_pdf(TWO_PAGES)

    assert [(block.page, block.text) for block in parsed.blocks] == [
        (1, "Onboarding guide"),
        (1, "Read the security policy before\nyour first deploy."),
        (2, "Second page paragraph."),
    ]
    assert all(block.kind is BlockKind.PARAGRAPH for block in parsed.blocks)


def test_offsets_slice_each_block_out_of_the_text() -> None:
    parsed = parse_pdf(TWO_PAGES)

    for block in parsed.blocks:
        assert parsed.text[block.start : block.end] == block.text


def test_table_rows_stay_on_one_line_each() -> None:
    table = text_page(
        (72, 720, "Tool"),
        (250, 720, "Version"),
        (72, 706, "uv"),
        (250, 706, "0.9"),
        (72, 692, "Python"),
        (250, 692, "3.14"),
    )

    [block] = parse_pdf(build_pdf(table)).blocks

    assert block.text == "Tool Version\nuv 0.9\nPython 3.14"


def test_a_scanned_page_yields_no_blocks_but_later_pages_keep_their_numbers() -> None:
    parsed = parse_pdf(
        build_pdf(
            text_page((72, 720, "Before the scan.")),
            IMAGE_ONLY,
            text_page((72, 720, "After the scan.")),
        )
    )

    assert [(block.page, block.text) for block in parsed.blocks] == [
        (1, "Before the scan."),
        (3, "After the scan."),
    ]


def test_text_drawn_through_a_form_is_not_mistaken_for_a_scan() -> None:
    pdf = build_pdf(b"/X1 Do", form=text_page((72, 720, "Stamped text.")))

    [block] = parse_pdf(pdf).blocks

    assert (block.page, block.text) == (1, "Stamped text.")


def test_control_and_invisible_characters_are_removed() -> None:
    pdf = build_pdf(text_page((72, 720, "a\x07b\x0bc\x7fd")))

    [block] = parse_pdf(pdf).blocks

    assert block.text == "ab\ncd"


def test_a_document_of_only_scans_is_refused_without_ocr() -> None:
    with pytest.raises(NoTextLayer, match="OCR is not supported"):
        parse_pdf(build_pdf(IMAGE_ONLY, IMAGE_ONLY))


def test_a_password_protected_pdf_is_refused() -> None:
    with pytest.raises(EncryptedDocument, match="password"):
        parse_pdf(encrypt(TWO_PAGES, require_password=True))


def test_a_pdf_with_only_an_owner_password_is_read() -> None:
    parsed = parse_pdf(encrypt(TWO_PAGES, require_password=False))

    assert parsed == parse_pdf(TWO_PAGES)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"not a pdf at all",
        b"\x89PNG\r\n\x1a\n" + b"0" * 64,
        TWO_PAGES[: len(TWO_PAGES) // 2],
    ],
    ids=["empty", "text", "png", "truncated"],
)
def test_corrupt_input_raises_a_typed_error(content: bytes) -> None:
    with pytest.raises(CorruptDocument):
        parse_pdf(content)


def test_size_limits_raise_typed_errors() -> None:
    with pytest.raises(DocumentTooLarge, match="bytes"):
        parse_pdf(TWO_PAGES, max_bytes=100)
    with pytest.raises(DocumentTooLarge, match="pages"):
        parse_pdf(TWO_PAGES, max_pages=1)
    with pytest.raises(DocumentTooLarge, match="characters"):
        parse_pdf(TWO_PAGES, max_characters=20)
    with pytest.raises(DocumentTooLarge, match="drawing commands"):
        parse_pdf(TWO_PAGES, max_page_content_bytes=10)
    form_only = build_pdf(b"/X1 Do", form=text_page((72, 720, "x" * 50)))
    with pytest.raises(DocumentTooLarge, match="drawing commands"):
        parse_pdf(form_only, max_page_content_bytes=40)
    with pytest.raises(DocumentTooLarge, match="seconds"):
        parse_pdf(TWO_PAGES, max_seconds=0)


@pytest.mark.parametrize("place", ["contents", "forms", "fonts"])
def test_a_page_is_refused_before_it_decompresses_far_past_the_limit(
    place: Literal["contents", "forms", "fonts"],
) -> None:
    # Ten streams of 5 MB each: decoding them all would need over 50 MB.
    pdf = compressed_streams_pdf(place, count=10, size=5_000_000)

    tracemalloc.start()
    try:
        with pytest.raises(DocumentTooLarge, match="drawing commands"):
            parse_pdf(pdf, max_page_content_bytes=1_000_000)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 20_000_000


def test_memory_does_not_grow_with_the_page_count() -> None:
    # Each page carries 250 KB of padding: under the per-page limit, but
    # holding all 20 pages at once would need over 5 MB.
    pages = [text_page((72, 720, f"Page {n}.")) + b" " * 250_000 for n in range(20)]
    pdf = build_pdf(*pages)

    tracemalloc.start()
    try:
        parsed = parse_pdf(pdf)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(parsed.blocks) == 20
    assert peak < 2_500_000


def test_pages_packed_in_an_object_stream_are_read_quickly() -> None:
    # Dropping the whole pypdf cache after each page made it decode the
    # object stream again for every page: about 2.7 seconds for this file.
    pdf = object_stream_pdf(pages=20, filler=5000)

    parsed = parse_pdf(pdf, max_seconds=1.0)

    assert [block.text for block in parsed.blocks[:2]] == ["Page 1.", "Page 2."]
    assert len(parsed.blocks) == 20


def test_a_block_page_number_starts_at_one() -> None:
    with pytest.raises(InvalidBlock, match="start at 1"):
        StructuralBlock(kind=BlockKind.PARAGRAPH, text="x", start=0, end=1, page=0)
