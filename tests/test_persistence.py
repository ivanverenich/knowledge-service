"""Tests for the Document row translation."""

from datetime import UTC, datetime

import pytest

from knowledge_service.documents import (
    Document,
    DocumentAvailability,
    DocumentProvenance,
    InvalidDocument,
)
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.persistence import as_document, as_row

OBSERVED_AT = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)


def make_document() -> Document:
    return Document.register(
        document_id=DocumentId.parse("00000000-0000-0000-0000-000000000020"),
        provenance=DocumentProvenance(
            source_id=SourceId.parse("00000000-0000-0000-0000-000000000010"),
            external_id="page-123",
            source_version="17",
        ),
        content_fingerprint="content-a",
        authorization_fingerprint="acl-a",
        observed_at=OBSERVED_AT,
    )


def test_row_translation_round_trips_a_document() -> None:
    document = make_document()

    assert as_document(as_row(document)) == document


def test_a_content_only_change_moves_only_the_content_version() -> None:
    document = make_document().reconcile(
        content_fingerprint="content-b",
        authorization_fingerprint="acl-a",
        source_version="18",
        observed_at=OBSERVED_AT,
    )

    row = as_row(document)

    assert row["content_version"] == 2
    assert row["authorization_version"] == 1


def test_tombstoned_document_round_trips_as_unavailable() -> None:
    document = make_document().tombstone(observed_at=OBSERVED_AT)

    assert as_document(as_row(document)).availability is DocumentAvailability.TOMBSTONED


def test_unknown_stored_availability_raises_typed_failure() -> None:
    row = as_row(make_document())
    row["availability"] = "retired"

    with pytest.raises(InvalidDocument, match="availability"):
        as_document(row)
