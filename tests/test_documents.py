"""Tests for Document version and availability invariants."""

from datetime import UTC, datetime, timedelta

import pytest

from knowledge_service.documents import (
    AuthorizationVersion,
    ContentVersion,
    Document,
    DocumentAvailability,
    DocumentProvenance,
    InvalidDocument,
)
from knowledge_service.identifiers import DocumentId, SourceId

OBSERVED_AT = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def make_document() -> Document:
    return Document.register(
        document_id=DocumentId.parse("00000000-0000-0000-0000-000000000001"),
        provenance=DocumentProvenance(
            source_id=SourceId.parse("00000000-0000-0000-0000-000000000002"),
            external_id="page-123",
            source_version="17",
        ),
        content_fingerprint="content-a",
        authorization_fingerprint="acl-a",
        observed_at=OBSERVED_AT,
    )


def test_unchangedsnapshot_preverses_versions_and_timestamp() -> None:
    document = make_document()

    result = document.reconcile(
        content_fingerprint="content-a",
        authorization_fingerprint="acl-a",
        source_version="17",
        observed_at=OBSERVED_AT + timedelta(minutes=1),
    )

    assert result is document
    assert result.content_version == ContentVersion(1)
    assert result.authorization_version == AuthorizationVersion(1)
    assert result.created_at == OBSERVED_AT


def test_acl_only_changed_advances_authorization_version() -> None:
    document = make_document()

    result = document.reconcile(
        content_fingerprint="content-a",
        authorization_fingerprint="acl-b",
        source_version="18",
        observed_at=OBSERVED_AT + timedelta(minutes=1),
    )

    assert result.content_version == ContentVersion(1)
    assert result.authorization_version == AuthorizationVersion(2)
    assert result.content_fingerprint == "content-a"
    assert result.authorization_fingerprint == "acl-b"


def test_content_only_changed_advances_content_version() -> None:
    document = make_document()

    result = document.reconcile(
        content_fingerprint="content-b",
        authorization_fingerprint="acl-a",
        source_version="18",
        observed_at=OBSERVED_AT + timedelta(minutes=1),
    )

    assert result.content_version == ContentVersion(2)
    assert result.authorization_version == AuthorizationVersion(1)
    assert result.content_fingerprint == "content-b"


def test_tombstone_marks_unavailable_and_preserves_versions() -> None:
    document = make_document()

    result = document.tombstone(observed_at=OBSERVED_AT + timedelta(minutes=1))

    assert result.document_id == document.document_id
    assert result.availability == DocumentAvailability.TOMBSTONED
    assert result.content_version == document.content_version
    assert result.authorization_version == document.authorization_version
    assert result.tombstone(observed_at=OBSERVED_AT + timedelta(minutes=2)) is result


def test_naive_observation_time_raises_typed_failure() -> None:
    document = make_document()

    with pytest.raises(InvalidDocument, match="UTC-aware"):
        document.tombstone(observed_at=datetime(2026, 10, 3, 13, 0))


@pytest.mark.parametrize("version_type", [ContentVersion, AuthorizationVersion])
def test_versions_must_be_positive_integers(
    version_type: type[ContentVersion] | type[AuthorizationVersion],
) -> None:
    with pytest.raises(InvalidDocument):
        version_type(0)
