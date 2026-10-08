"""Tests for stable domain identifiers."""

from itertools import combinations

import pytest

from knowledge_service.identifiers import (
    AccessGrantId,
    ChunkId,
    ConversationId,
    DocumentId,
    EvaluationRunId,
    GroupId,
    InvalidIdentifier,
    JobId,
    MessageId,
    SourceId,
    StableIdentifier,
    SynchronizationRunId,
    UserId,
)

ID_TYPES: tuple[type[StableIdentifier], ...] = (
    SourceId,
    DocumentId,
    ChunkId,
    UserId,
    GroupId,
    AccessGrantId,
    ConversationId,
    MessageId,
    SynchronizationRunId,
    EvaluationRunId,
    JobId,
)

UUID_STRINGS = (
    "00000000-0000-0000-0000-000000000001",
    "ffffffff-ffff-4fff-8fff-ffffffffffff",
)


@pytest.mark.parametrize("id_type", ID_TYPES)
@pytest.mark.parametrize("raw", UUID_STRINGS)
def test_identifier_serialization_round_trip(
    id_type: type[StableIdentifier], raw: str
) -> None:
    identifier = id_type.parse(raw)

    restored = id_type.parse(identifier.serialize())
    assert restored == identifier
    assert restored.serialize() == raw


@pytest.mark.parametrize(("left_type", "right_type"), tuple(combinations(ID_TYPES, 2)))
def test_identifier_type_mismatch(
    left_type: type[StableIdentifier],
    right_type: type[StableIdentifier],
) -> None:
    raw = "00000000-0000-0000-0000-000000000001"

    assert left_type.parse(raw) != right_type.parse(raw)


@pytest.mark.parametrize("id_type", ID_TYPES)
def test_invalid_identifier_raises(id_type: type[StableIdentifier]) -> None:
    with pytest.raises(InvalidIdentifier) as captured:
        id_type.parse("not-a-uuid")

    assert captured.value.identifier_type == id_type.__name__
    assert "not-a-uuid" not in str(captured.value)


@pytest.mark.parametrize("identifier_type", ID_TYPES)
def test_new_identifier_uses_uuid_version_7(
    identifier_type: type[StableIdentifier],
) -> None:
    identifier = identifier_type.new()

    assert type(identifier) is identifier_type
    assert identifier.value.version == 7
