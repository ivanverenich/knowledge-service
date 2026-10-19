"""Tests for turning Source Records into normalized Documents or quarantines."""

import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import pytest

from knowledge_service.directory_source import DirectorySource
from knowledge_service.identifiers import SourceId
from knowledge_service.normalization import (
    MAX_TITLE_CHARACTERS,
    NormalizedDocument,
    Quarantined,
    QuarantineReason,
    normalize,
)
from knowledge_service.sources import (
    InvalidSource,
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.structure import BlockKind
from pdf_builder import IMAGE_ONLY, build_pdf, encrypt, text_page

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
PUBLIC = SourceAccess(public=True)
MODIFIED = datetime(2026, 10, 19, 9, 30, tzinfo=UTC)


def record(
    content: bytes,
    media_type: str = "text/markdown",
    *,
    title: str = "Onboarding",
    external_id: str = "guides/onboarding.md",
) -> SourceRecord:
    return SourceRecord.present(
        external_id=external_id,
        version="v1",
        media_type=media_type,
        content=content,
        access=PUBLIC,
        metadata=SourceMetadata(
            title=title, modified_at=MODIFIED, size_bytes=len(content)
        ),
    )


def document(outcome: NormalizedDocument | Quarantined) -> NormalizedDocument:
    assert isinstance(outcome, NormalizedDocument), outcome
    return outcome


def quarantined(outcome: NormalizedDocument | Quarantined) -> Quarantined:
    assert isinstance(outcome, Quarantined), outcome
    return outcome


def test_a_markdown_record_becomes_a_document_with_provenance() -> None:
    content = b"# Onboarding\n\nRead the security policy.\n"

    normalized = document(normalize(record(content), source_id=SOURCE_ID))

    assert normalized.provenance.source_id == SOURCE_ID
    assert normalized.provenance.external_id == "guides/onboarding.md"
    assert normalized.provenance.source_version == "v1"
    assert normalized.title == "Onboarding"
    assert normalized.media_type == "text/markdown"
    assert normalized.modified_at == MODIFIED
    assert normalized.size_bytes == len(content)
    assert normalized.access == PUBLIC
    assert normalized.parser_version.startswith("markdown/1 markdown-it-py/")
    assert [(block.kind, block.text) for block in normalized.parsed.blocks] == [
        (BlockKind.HEADING, "Onboarding"),
        (BlockKind.PARAGRAPH, "Read the security policy."),
    ]


def test_the_same_record_always_produces_the_same_document() -> None:
    same = record(b"# Title\n\nBody text.\n")

    assert normalize(same, source_id=SOURCE_ID) == normalize(same, source_id=SOURCE_ID)


@pytest.mark.parametrize(
    ("media_type", "content", "expected_type", "parser_prefix"),
    [
        ("text/plain", b"Plain paragraph.", "text/plain", "text/1"),
        ("Text/HTML; charset=utf-8", b"<p>Plain paragraph.</p>", "text/html", "html/1"),
        ("application/xhtml+xml", b"<p>Plain paragraph.</p>", "text/html", "html/1"),
        (
            "application/pdf",
            build_pdf(text_page((72, 720, "Plain paragraph."))),
            "application/pdf",
            "pdf/1 pypdf/",
        ),
    ],
    ids=["text", "html-with-charset", "xhtml-alias", "pdf"],
)
def test_the_media_type_selects_the_parser(
    media_type: str, content: bytes, expected_type: str, parser_prefix: str
) -> None:
    normalized = document(normalize(record(content, media_type), source_id=SOURCE_ID))

    assert normalized.media_type == expected_type
    assert normalized.parser_version.startswith(parser_prefix)
    assert [block.text for block in normalized.parsed.blocks] == ["Plain paragraph."]


def test_a_byte_order_mark_is_not_part_of_the_text() -> None:
    content = "﻿Привіт, світе.".encode()

    normalized = document(normalize(record(content, "text/plain"), source_id=SOURCE_ID))

    assert normalized.parsed.text == "Привіт, світе."


@pytest.mark.parametrize(
    ("media_type", "content", "reason"),
    [
        ("image/png", b"\x89PNG\r\n\x1a\n", QuarantineReason.UNSUPPORTED_MEDIA_TYPE),
        ("text/plain", b"\xff\xfe broken", QuarantineReason.UNDECODABLE_TEXT),
        ("text/plain", b"before\x00after", QuarantineReason.UNDECODABLE_TEXT),
        ("text/markdown", b" \n\t\n", QuarantineReason.EMPTY_CONTENT),
        ("text/html", b"<nav>Home | About</nav>", QuarantineReason.EMPTY_CONTENT),
        ("text/markdown", b"x" * 1_000_001, QuarantineReason.TOO_LARGE),
        ("text/html", b"<div>" * 300 + b"deep", QuarantineReason.UNPARSABLE),
        ("application/pdf", b"not a pdf at all", QuarantineReason.CORRUPT),
        (
            "application/pdf",
            build_pdf(IMAGE_ONLY),
            QuarantineReason.NO_TEXT_LAYER,
        ),
        (
            "application/pdf",
            encrypt(build_pdf(text_page((72, 720, "x"))), require_password=True),
            QuarantineReason.ENCRYPTED,
        ),
    ],
    ids=[
        "unsupported",
        "not-utf8",
        "nul",
        "whitespace",
        "only-navigation",
        "too-large",
        "too-deep",
        "corrupt-pdf",
        "scanned-pdf",
        "encrypted-pdf",
    ],
)
def test_content_that_cannot_become_a_document_is_quarantined(
    media_type: str, content: bytes, reason: QuarantineReason
) -> None:
    outcome = quarantined(normalize(record(content, media_type), source_id=SOURCE_ID))

    assert outcome.reason is reason
    assert outcome.provenance.external_id == "guides/onboarding.md"
    assert outcome.detail.strip()
    assert (outcome.parser_version is None) == (
        reason is QuarantineReason.UNSUPPORTED_MEDIA_TYPE
    )


def test_a_quarantine_detail_never_contains_the_content() -> None:
    outcome = quarantined(
        normalize(record(b"secret-token \xff", "text/plain"), source_id=SOURCE_ID)
    )

    assert "secret-token" not in outcome.detail


def test_the_title_is_one_clean_line() -> None:
    messy = "  Café​  plan\n\tQ4 ‮"

    normalized = document(
        normalize(record(b"Body.", "text/plain", title=messy), source_id=SOURCE_ID)
    )

    assert normalized.title == "Café plan Q4"


def test_a_long_title_is_cut_and_a_blank_one_falls_back_to_the_id() -> None:
    long_title = document(
        normalize(record(b"Body.", "text/plain", title="a " * 300), source_id=SOURCE_ID)
    )
    blank_title = document(
        normalize(record(b"Body.", "text/plain", title="​"), source_id=SOURCE_ID)
    )

    assert len(long_title.title) <= MAX_TITLE_CHARACTERS
    assert not long_title.title.endswith(" ")
    assert blank_title.title == "guides/onboarding.md"


def test_a_title_of_only_invisible_characters_never_comes_out_empty() -> None:
    invisible = record(b"Body.", "text/plain", title="\u200b", external_id="\u200b")

    assert document(normalize(invisible, source_id=SOURCE_ID)).title == "Untitled"


def test_removing_an_invisible_character_still_leaves_an_nfc_title() -> None:
    # The zero-width space keeps "e" and the accent apart until it is removed.
    split = record(b"Body.", "text/plain", title="Cafe\u200b\u0301")

    title = document(normalize(split, source_id=SOURCE_ID)).title

    assert title == "Café"
    assert unicodedata.normalize("NFC", title) == title


def test_a_look_alike_media_type_is_not_lowercased_into_a_real_one() -> None:
    kelvin = record(b"# Title", "text/mar\u212adown")

    outcome = quarantined(normalize(kelvin, source_id=SOURCE_ID))

    assert outcome.reason is QuarantineReason.UNSUPPORTED_MEDIA_TYPE


def test_a_deleted_record_cannot_be_normalized() -> None:
    gone = SourceRecord.deleted(external_id="guides/onboarding.md", version=None)

    with pytest.raises(InvalidSource, match="present"):
        normalize(gone, source_id=SOURCE_ID)


async def test_every_file_in_a_folder_is_normalized_or_quarantined(
    tmp_path: Path,
) -> None:
    files = {
        "a.txt": b"Plain text.",
        "b.md": b"# Heading\n\nMarkdown body.",
        "c.html": b"<h1>Page</h1><p>HTML body.</p>",
        "d.pdf": build_pdf(text_page((72, 720, "PDF body."))),
        "e.txt": b"\xff\xfe not utf-8",
    }
    for name, content in files.items():
        (tmp_path / name).write_bytes(content)
    page = await DirectorySource(tmp_path, access=PUBLIC).fetch_page(
        checkpoint=None, page_size=10
    )

    outcomes = [normalize(item, source_id=SOURCE_ID) for item in page.records]

    assert [
        (outcome.provenance.external_id, type(outcome).__name__) for outcome in outcomes
    ] == [
        ("a.txt", "NormalizedDocument"),
        ("b.md", "NormalizedDocument"),
        ("c.html", "NormalizedDocument"),
        ("d.pdf", "NormalizedDocument"),
        ("e.txt", "Quarantined"),
    ]
    assert quarantined(outcomes[4]).reason is QuarantineReason.UNDECODABLE_TEXT
