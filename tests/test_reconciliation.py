"""Tests for turning a normalized document into stored Chunks."""

from datetime import UTC, datetime

from knowledge_service.documents import Document
from knowledge_service.fingerprints import content_fingerprint
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.normalization import NormalizedDocument, normalize
from knowledge_service.reconciliation import build_chunks
from knowledge_service.sources import SourceAccess, SourceRecord

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
OBSERVED_AT = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
HANDBOOK = (
    "# Team handbook\n\nWelcome to the team.\n\n"
    "## Vacation\n\nRequest vacation two weeks ahead.\n"
)


def normalized_handbook() -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id="guides/handbook.md",
            version="v1",
            media_type="text/markdown",
            content=HANDBOOK.encode(),
            access=SourceAccess(public=True),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


def test_chunks_carry_the_document_version_fingerprint_and_anchors() -> None:
    normalized = normalized_handbook()
    document = Document.register(
        title="Handbook",
        document_id=DocumentId.new(),
        provenance=normalized.provenance,
        content_fingerprint=content_fingerprint(normalized.parsed),
        authorization_fingerprint="access",
        observed_at=OBSERVED_AT,
    )

    chunks = build_chunks(normalized, document)

    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    assert chunks[0].heading_path == "Team handbook"
    for chunk in chunks:
        assert chunk.document_id == document.document_id
        assert chunk.content_version == document.content_version
        assert chunk.content_fingerprint == document.content_fingerprint
        assert (chunk.source_anchor or "").startswith("guides/handbook.md#offset=")
