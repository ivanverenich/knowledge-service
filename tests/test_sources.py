"""Tests for Source configuration and Synchronization Run invariants."""

from datetime import UTC, datetime, timedelta

import pytest

from knowledge_service.identifiers import SourceId, SynchronizationRunId
from knowledge_service.sources import (
    InvalidSource,
    RunStatus,
    Source,
    SourceConfiguration,
    SourceKind,
    SynchronizationRun,
)

REGISTERED_AT = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")


def make_source() -> Source:
    return Source.register(
        source_id=SOURCE_ID,
        configuration=SourceConfiguration(
            kind=SourceKind.LOCAL_DIRECTORY,
            location="/srv/knowledge/handbook",
            credentials_reference="KNOWLEDGE_SERVICE_HANDBOOK_TOKEN",
        ),
        display_name="Engineering handbook",
        registered_at=REGISTERED_AT,
    )


def test_registered_source_starts_enabled_without_checkpoint() -> None:
    source = make_source()

    assert source.enabled is True
    assert source.checkpoint is None
    assert source.created_at == REGISTERED_AT


def test_recording_a_checkpoint_keeps_earlier_checkpoints_reachable() -> None:
    source = make_source()

    advanced = source.record_checkpoint(
        checkpoint="page-42", observed_at=REGISTERED_AT + timedelta(minutes=5)
    )

    assert advanced.checkpoint == "page-42"
    assert source.checkpoint is None
    assert advanced.updated_at == REGISTERED_AT + timedelta(minutes=5)


def test_disabling_an_already_disabled_source_is_a_no_op() -> None:
    disabled = make_source().set_enabled(
        enabled=False, observed_at=REGISTERED_AT + timedelta(minutes=1)
    )

    assert (
        disabled.set_enabled(
            enabled=False, observed_at=REGISTERED_AT + timedelta(minutes=9)
        )
        is disabled
    )


def test_run_can_only_finish_once() -> None:
    run = SynchronizationRun.start(
        run_id=SynchronizationRunId.parse("00000000-0000-0000-0000-000000000011"),
        source_id=SOURCE_ID,
        started_at=REGISTERED_AT,
    )

    succeeded = run.succeed(finished_at=REGISTERED_AT + timedelta(minutes=3))

    assert succeeded.status is RunStatus.SUCCEEDED
    with pytest.raises(InvalidSource, match="cannot change state"):
        succeeded.fail(
            finished_at=REGISTERED_AT + timedelta(minutes=4), detail="late failure"
        )


def test_naive_timestamp_raises_typed_failure() -> None:
    source = make_source()

    with pytest.raises(InvalidSource, match="UTC-aware"):
        source.record_checkpoint(
            checkpoint="page-43", observed_at=datetime(2026, 10, 6, 9, 30)
        )


def test_empty_location_raises_typed_failure() -> None:
    with pytest.raises(InvalidSource, match="location"):
        SourceConfiguration(kind=SourceKind.CONFLUENCE, location="   ")
