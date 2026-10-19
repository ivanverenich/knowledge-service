"""Tests for PDF text extraction: pages, tables, scans, encryption, corruption."""

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
from pdf_builder import IMAGE_ONLY, build_pdf, encrypt, text_page

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


def test_a_block_page_number_starts_at_one() -> None:
    with pytest.raises(InvalidBlock, match="start at 1"):
        StructuralBlock(kind=BlockKind.PARAGRAPH, text="x", start=0, end=1, page=0)
