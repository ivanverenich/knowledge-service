"""Tests for content and authorization fingerprints."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from knowledge_service.access import AccessGrant, InvalidGrant, Subject
from knowledge_service.documents import Document, DocumentProvenance
from knowledge_service.fingerprints import (
    authorization_fingerprint,
    content_fingerprint,
)
from knowledge_service.identifiers import (
    AccessGrantId,
    DocumentId,
    GroupId,
    SourceId,
    UserId,
)
from knowledge_service.markdown_parser import parse_markdown
from knowledge_service.structure import BlockKind, ParsedDocument, StructuralBlock

# The same examples on every run, and no example database written to disk.
DETERMINISTIC = settings(derandomize=True, database=None, deadline=None)

DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000020")
OTHER_DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000021")
ALICE = Subject.user(UserId.parse("00000000-0000-0000-0000-000000000031"))
FINANCE = Subject.group(GroupId.parse("00000000-0000-0000-0000-000000000041"))
GRANTED_AT = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
GUIDE = parse_markdown("# Onboarding\n\nRead the security policy first.\n")

subjects = st.one_of(
    st.just(Subject.public()),
    st.uuids().map(lambda value: Subject.user(UserId(value))),
    st.uuids().map(lambda value: Subject.group(GroupId(value))),
)
timestamps = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 1, 1),
    timezones=st.just(UTC),
)


def grant(subject: Subject, *, document_id: DocumentId = DOCUMENT_ID) -> AccessGrant:
    return AccessGrant(
        grant_id=AccessGrantId.new(),
        document_id=document_id,
        subject=subject,
        granted_at=GRANTED_AT,
    )


@DETERMINISTIC
@given(data=st.data(), readers=st.lists(subjects, unique=True, max_size=8))
def test_the_order_of_grants_never_changes_the_fingerprint(
    data: st.DataObject, readers: list[Subject]
) -> None:
    grants = [grant(subject) for subject in readers]
    shuffled = data.draw(st.permutations(grants))

    assert authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=shuffled
    ) == authorization_fingerprint(document_id=DOCUMENT_ID, grants=grants)


@DETERMINISTIC
@given(
    readers=st.lists(subjects, unique=True, max_size=8),
    ids=st.lists(st.uuids(), min_size=8, max_size=8, unique=True),
    moments=st.lists(timestamps, min_size=8, max_size=8),
)
def test_grant_ids_and_times_never_change_the_fingerprint(
    readers: list[Subject], ids: list[UUID], moments: list[datetime]
) -> None:
    original = [grant(subject) for subject in readers]
    reissued = [
        replace(item, grant_id=AccessGrantId(new_id), granted_at=moment)
        for item, new_id, moment in zip(original, ids, moments, strict=False)
    ]

    assert authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=reissued
    ) == authorization_fingerprint(document_id=DOCUMENT_ID, grants=original)


@DETERMINISTIC
@given(
    readers=st.lists(subjects, unique=True, max_size=8),
    extra=subjects,
)
def test_adding_a_reader_always_changes_the_fingerprint(
    readers: list[Subject], extra: Subject
) -> None:
    if extra in readers:
        return
    before = [grant(subject) for subject in readers]

    assert authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=[*before, grant(extra)]
    ) != authorization_fingerprint(document_id=DOCUMENT_ID, grants=before)


def test_no_readers_has_its_own_fingerprint() -> None:
    nobody = authorization_fingerprint(document_id=DOCUMENT_ID, grants=[])
    everyone = authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=[grant(Subject.public())]
    )

    assert nobody != everyone


def test_a_user_and_a_group_with_the_same_uuid_are_different_readers() -> None:
    same = UUID("00000000-0000-0000-0000-000000000050")

    as_user = authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=[grant(Subject.user(UserId(same)))]
    )
    as_group = authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=[grant(Subject.group(GroupId(same)))]
    )

    assert as_user != as_group


def test_the_same_reader_granted_twice_is_refused() -> None:
    with pytest.raises(InvalidGrant, match="at most once"):
        authorization_fingerprint(
            document_id=DOCUMENT_ID, grants=[grant(ALICE), grant(ALICE)]
        )


def test_a_grant_for_another_document_is_refused() -> None:
    foreign = grant(FINANCE, document_id=OTHER_DOCUMENT_ID)

    with pytest.raises(InvalidGrant, match="same document"):
        authorization_fingerprint(document_id=DOCUMENT_ID, grants=[foreign])


def test_the_same_content_always_has_the_same_fingerprint() -> None:
    again = parse_markdown("# Onboarding\n\nRead the security policy first.\n")

    assert content_fingerprint(again) == content_fingerprint(GUIDE)


@pytest.mark.parametrize(
    "changed",
    [
        "# Onboarding\n\nRead the security policy second.\n",
        "## Onboarding\n\nRead the security policy first.\n",
        "# Onboarding\n\n\nRead the security policy first.\n",
        "# Onboarding\n\n- Read the security policy first.\n",
    ],
    ids=["text", "heading-level", "offsets", "kind"],
)
def test_any_change_the_chunker_sees_changes_the_content_fingerprint(
    changed: str,
) -> None:
    assert content_fingerprint(parse_markdown(changed)) != content_fingerprint(GUIDE)


def test_equal_documents_have_equal_fingerprints_even_with_bool_offsets() -> None:
    # `True == 1` in Python, so these blocks are equal and must hash equally.
    as_ints = ParsedDocument(
        text="x", blocks=(StructuralBlock(BlockKind.PARAGRAPH, "x", 0, 1),)
    )
    as_bools = ParsedDocument(
        text="x", blocks=(StructuralBlock(BlockKind.PARAGRAPH, "x", False, True),)
    )

    assert as_bools == as_ints
    assert content_fingerprint(as_bools) == content_fingerprint(as_ints)


def test_a_lone_surrogate_in_text_still_has_a_fingerprint() -> None:
    surrogate = parse_markdown("Broken \ud800 character.")

    assert content_fingerprint(surrogate) == content_fingerprint(surrogate)


def test_the_canonical_form_does_not_drift() -> None:
    # A changed canonical form would silently re-index every Document. If
    # this fails on purpose, bump the label's version and update the values.
    assert content_fingerprint(GUIDE) == (
        "9f9a307cdb75ab83f1c9f708bb2786f2fbe3568881ec395d2cc6900442d25964"
    )
    assert authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=[grant(FINANCE), grant(ALICE)]
    ) == ("4d7d0315d598182e4926bae2f7f9308707bc279e2102817b2b5af70f9dd670e7")


def test_fingerprints_tell_content_and_access_changes_apart() -> None:
    provenance = DocumentProvenance(
        source_id=SourceId.parse("00000000-0000-0000-0000-000000000010"),
        external_id="guides/onboarding.md",
    )
    alice_only = authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=[grant(ALICE)]
    )
    document = Document.register(
        document_id=DOCUMENT_ID,
        provenance=provenance,
        content_fingerprint=content_fingerprint(GUIDE),
        authorization_fingerprint=alice_only,
        observed_at=GRANTED_AT,
    )
    edited = content_fingerprint(
        parse_markdown("# Onboarding\n\nRead the new security policy.\n")
    )
    with_finance = authorization_fingerprint(
        document_id=DOCUMENT_ID, grants=[grant(FINANCE), grant(ALICE)]
    )

    def observe(content: str, access: str) -> Document:
        return document.reconcile(
            content_fingerprint=content,
            authorization_fingerprint=access,
            source_version=None,
            observed_at=GRANTED_AT,
        )

    unchanged = observe(content_fingerprint(GUIDE), alice_only)
    content_only = observe(edited, alice_only)
    access_only = observe(content_fingerprint(GUIDE), with_finance)

    assert unchanged is document
    assert (
        content_only.content_version.number,
        content_only.authorization_version.number,
    ) == (2, 1)
    assert (
        access_only.content_version.number,
        access_only.authorization_version.number,
    ) == (1, 2)
