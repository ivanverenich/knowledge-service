"""The `web_url` a Source reports for an item, before anything is stored."""

from datetime import UTC, datetime

import pytest

from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.fingerprints import content_fingerprint
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.provenance import display_text
from knowledge_service.ingestion.sources import (
    MAX_WEB_URL_CHARACTERS,
    InvalidSource,
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-0000000016c3")
WIKI = "https://wiki.example.com/spaces/HR/pages/42"


def metadata(web_url: str | None) -> SourceMetadata:
    return SourceMetadata(
        title="Leave",
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
        size_bytes=0,
        web_url=web_url,
    )


def normalized(web_url: str | None) -> NormalizedDocument:
    record = SourceRecord.present(
        external_id="leave.txt",
        version="v1",
        media_type="text/plain",
        content=b"Twenty days a year.",
        access=SourceAccess(public=True),
        metadata=metadata(web_url),
    )
    outcome = normalize(record, source_id=SOURCE_ID)
    assert isinstance(outcome, NormalizedDocument)
    return outcome


@pytest.mark.parametrize("web_url", ["", "   "])
def test_an_empty_web_url_is_refused(web_url: str) -> None:
    with pytest.raises(InvalidSource, match="must not be empty"):
        metadata(web_url)


def test_a_web_url_longer_than_the_limit_is_refused() -> None:
    too_long = WIKI + "/" + "x" * MAX_WEB_URL_CHARACTERS

    with pytest.raises(InvalidSource, match="at most"):
        metadata(too_long)


def test_metadata_without_a_web_url_still_works() -> None:
    assert (
        SourceMetadata(
            title="Leave", modified_at=datetime(2026, 1, 1, tzinfo=UTC), size_bytes=0
        ).web_url
        is None
    )


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        (WIKI, WIKI),
        (f"{WIKI}?token=SECRET#comments", WIKI),
        ("https://reader:hunter2@wiki.example.com/x", "https://wiki.example.com/x"),
        ("HTTPS://Wiki.Example.com/x", "https://wiki.example.com/x"),
        ("javascript:alert(1)", None),
        ("file:///etc/passwd", None),
        ("https://wiki.example.com/x\u202e", None),
        # A Citation would show this as another page, so it is not kept.
        ("https://wiki.example.com/x:SECRET@y", None),
        (None, None),
    ],
)
def test_normalization_keeps_only_the_safe_form_of_a_web_url(
    given: str | None, kept: str | None
) -> None:
    assert normalized(given).web_url == kept


def test_a_web_url_changes_the_fingerprint() -> None:
    parsed = normalized(None).parsed

    assert content_fingerprint(parsed, web_url=WIKI) != content_fingerprint(parsed)
    assert content_fingerprint(parsed, web_url=WIKI) != content_fingerprint(
        parsed, web_url=WIKI + "/moved"
    )


def test_a_web_url_that_is_not_text_is_refused() -> None:
    with pytest.raises(InvalidSource, match="must be text"):
        metadata(b"https://wiki.example.com/x")  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("fragment", ["#offset=0-21", "#page=3"])
def test_a_citation_keeps_the_place_in_the_page(fragment: str) -> None:
    assert display_text(WIKI + fragment) == WIKI + fragment


@pytest.mark.parametrize(
    "fragment", ["#comments", "#offset=0-21&token=SECRET", "#page=3?x", "#"]
)
def test_any_other_fragment_is_still_cut(fragment: str) -> None:
    assert display_text(WIKI + fragment) == WIKI
