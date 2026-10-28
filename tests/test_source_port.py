"""Tests for the Source port contract and its deterministic fake."""

import pytest

from fakes import FakeSource
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourceAccess,
    SourcePort,
    SourceRecord,
    SourceRecordState,
    SourceUnavailable,
)


def make_record(external_id: str, *, content: bytes = b"hello") -> SourceRecord:
    """Build one present record with public access."""
    return SourceRecord.present(
        external_id=external_id,
        version="1",
        media_type="text/plain",
        content=content,
        access=SourceAccess(public=True),
    )


async def test_fake_source_walks_every_page_in_order() -> None:
    first = make_record("a.txt")
    second = make_record("b.txt")
    source: SourcePort = FakeSource([[first], [second]])

    page = await source.fetch_page(checkpoint=None, page_size=10)

    assert page.records == (first,)
    assert page.next_checkpoint == "1"

    page = await source.fetch_page(checkpoint=page.next_checkpoint, page_size=10)

    assert page.records == (second,)
    assert page.next_checkpoint is None


async def test_resuming_from_a_checkpoint_skips_delivered_pages() -> None:
    source = FakeSource([[make_record("a.txt")], [make_record("b.txt")]])

    page = await source.fetch_page(checkpoint="1", page_size=10)

    assert [record.external_id for record in page.records] == ["b.txt"]
    assert page.next_checkpoint is None


async def test_a_page_can_repeat_a_record_and_signal_a_deletion() -> None:
    record = make_record("a.txt")
    tombstone = SourceRecord.deleted(external_id="gone.txt", version="3")
    source = FakeSource([[record], [record, tombstone]])

    await source.fetch_page(checkpoint=None, page_size=10)
    page = await source.fetch_page(checkpoint="1", page_size=10)

    assert page.records[0] == record
    assert page.records[1].state is SourceRecordState.DELETED
    assert page.records[1].content is None
    assert page.records[1].access is None


async def test_a_source_failure_is_typed() -> None:
    source = FakeSource([[make_record("a.txt")]], fail_on_call=1)

    with pytest.raises(SourceUnavailable):
        await source.fetch_page(checkpoint=None, page_size=10)


def test_a_deleted_record_carries_identity_only() -> None:
    with pytest.raises(InvalidSource, match="identity only"):
        SourceRecord(
            external_id="gone.txt",
            version=None,
            state=SourceRecordState.DELETED,
            content=b"still here",
        )


def test_a_public_access_payload_lists_no_principals() -> None:
    with pytest.raises(InvalidSource, match="must not list principals"):
        SourceAccess(public=True, principals=("alice@example.com",))
