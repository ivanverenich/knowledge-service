"""Fingerprint a Document's content and its access separately.

A fingerprint is a SHA-256 hash of a canonical form: one fixed way to write
the value down, so equal values always hash equally. Content and access are
hashed apart, so comparing both fingerprints tells an unchanged item, a
content-only change, and an access-only change apart. Each hash starts from
its own label, so a content fingerprint can never equal an authorization one.
"""

import hashlib
import json
from collections.abc import Sequence

from knowledge_service.access import AccessGrant, granted_subjects
from knowledge_service.identifiers import DocumentId
from knowledge_service.ingestion.parsers.structure import ParsedDocument

_CONTENT_LABEL = "knowledge-service/content/v1"
_AUTHORIZATION_LABEL = "knowledge-service/authorization/v1"


def content_fingerprint(parsed: ParsedDocument) -> str:
    """Return the fingerprint of a document's text and structure.

    Everything the chunker reads counts: the parsed text and every field of
    every block, including offsets, because citations point into the text.
    """
    blocks = [
        {
            "kind": block.kind.value,
            "text": block.text,
            "start": _number(block.start),
            "end": _number(block.end),
            "heading_level": _number(block.heading_level),
            "list_depth": _number(block.list_depth),
            "language": block.language,
            "links": [[link.text, link.target] for link in block.links],
            "page": _number(block.page),
        }
        for block in parsed.blocks
    ]
    return canonical_hash(_CONTENT_LABEL, {"text": parsed.text, "blocks": blocks})


def authorization_fingerprint(
    *, document_id: DocumentId, grants: Sequence[AccessGrant]
) -> str:
    """Return the fingerprint of who may read one document.

    Only the subjects count. The order of the grants, their IDs, and when they
    were granted do not, so the same readers always give the same fingerprint.
    An empty sequence means nobody may read the document; it has its own
    fingerprint.
    """
    subjects = [
        [
            grant.subject.kind.value,
            None
            if grant.subject.identifier is None
            else grant.subject.identifier.serialize(),
        ]
        for grant in granted_subjects(document_id=document_id, grants=tuple(grants))
    ]
    subjects.sort(key=lambda subject: (subject[0], subject[1] or ""))
    return canonical_hash(_AUTHORIZATION_LABEL, subjects)


def canonical_hash(label: str, value: object) -> str:
    """Hash a label and a JSON value written in one canonical way."""
    canonical = json.dumps(
        [label, value], ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    # Parsed text can hold a lone surrogate; "surrogatepass" still encodes it
    # to the same bytes every time instead of raising.
    return hashlib.sha256(canonical.encode("utf-8", "surrogatepass")).hexdigest()


def _number(value: int | None) -> int | None:
    """Write a number as a plain int, so `True` and `1` hash the same."""
    return None if value is None else int(value)
