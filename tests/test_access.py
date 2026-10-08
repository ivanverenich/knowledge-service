"""Tests for Access Grant subjects and grant sets."""

from datetime import UTC, datetime

import pytest

from knowledge_service.access import (
    AccessGrant,
    InvalidGrant,
    Subject,
    SubjectKind,
    granted_subjects,
)
from knowledge_service.identifiers import AccessGrantId, DocumentId, GroupId, UserId

OBSERVED_AT = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000020")
OTHER_DOCUMENT_ID = DocumentId.parse("00000000-0000-0000-0000-000000000021")
USER_ID = UserId.parse("00000000-0000-0000-0000-000000000030")
GROUP_ID = GroupId.parse("00000000-0000-0000-0000-000000000040")


def make_grant(
    subject: Subject,
    *,
    document_id: DocumentId = DOCUMENT_ID,
    granted_at: datetime = OBSERVED_AT,
) -> AccessGrant:
    return AccessGrant(
        grant_id=AccessGrantId.new(),
        document_id=document_id,
        subject=subject,
        granted_at=granted_at,
    )


def test_granted_subjects_returns_one_grant_per_subject_in_a_stable_order() -> None:
    run = granted_subjects(
        document_id=DOCUMENT_ID,
        grants=[
            make_grant(Subject.user(USER_ID)),
            make_grant(Subject.public()),
            make_grant(Subject.group(GROUP_ID)),
        ],
    )

    assert [grant.subject.kind for grant in run] == [
        SubjectKind.GROUP,
        SubjectKind.PUBLIC,
        SubjectKind.USER,
    ]


def test_a_subject_granted_twice_is_rejected() -> None:
    with pytest.raises(InvalidGrant, match="at most once"):
        granted_subjects(
            document_id=DOCUMENT_ID,
            grants=[
                make_grant(Subject.user(USER_ID)),
                make_grant(Subject.user(USER_ID)),
            ],
        )


def test_a_grant_for_another_document_is_rejected() -> None:
    with pytest.raises(InvalidGrant, match="same document"):
        granted_subjects(
            document_id=DOCUMENT_ID,
            grants=[make_grant(Subject.public(), document_id=OTHER_DOCUMENT_ID)],
        )


def test_a_public_subject_must_not_name_anyone() -> None:
    with pytest.raises(InvalidGrant, match="public"):
        Subject(kind=SubjectKind.PUBLIC, identifier=USER_ID)


def test_a_user_grant_must_name_a_user_id() -> None:
    with pytest.raises(InvalidGrant, match="UserId"):
        Subject(kind=SubjectKind.USER, identifier=GROUP_ID)


def test_a_naive_grant_time_raises_typed_failure() -> None:
    with pytest.raises(InvalidGrant, match="UTC-aware"):
        make_grant(Subject.public(), granted_at=datetime(2026, 10, 7, 9, 0))
