"""The embedding migration state machine, without a database."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from knowledge_service.embeddings.migration import (
    EmbeddingMigration,
    EmbeddingMigrationError,
    InvalidMigrationTransition,
    MigrationState,
    MigrationStep,
    SpaceRole,
    next_state,
    written_spaces,
)
from knowledge_service.identifiers import (
    EmbeddingMigrationId,
    EmbeddingSpaceId,
    IndexConfigurationId,
)

STARTED_AT = datetime(2026, 10, 27, 9, 0, tzinfo=UTC)

# Every state and step: the state after it, or None when it is refused.
TRANSITIONS = {
    (MigrationState.DUAL_WRITING, MigrationStep.SWITCH): MigrationState.SWITCHED,
    (MigrationState.DUAL_WRITING, MigrationStep.CLEAN_UP): None,
    (MigrationState.SWITCHED, MigrationStep.SWITCH): None,
    (MigrationState.SWITCHED, MigrationStep.CLEAN_UP): MigrationState.CLEANED,
    (MigrationState.CLEANED, MigrationStep.SWITCH): None,
    (MigrationState.CLEANED, MigrationStep.CLEAN_UP): None,
}


def test_the_table_covers_every_state_and_step() -> None:
    assert set(TRANSITIONS) == {
        (state, step) for state in MigrationState for step in MigrationStep
    }


@pytest.mark.parametrize(("state", "step"), list(TRANSITIONS))
def test_each_step_is_allowed_only_from_its_state(
    state: MigrationState, step: MigrationStep
) -> None:
    expected = TRANSITIONS[(state, step)]

    if expected is None:
        with pytest.raises(InvalidMigrationTransition):
            next_state(state, step)
    else:
        assert next_state(state, step) is expected


def test_the_only_path_is_dual_write_switch_clean_up() -> None:
    state = MigrationState.DUAL_WRITING
    path = [state]
    for step in (MigrationStep.SWITCH, MigrationStep.CLEAN_UP):
        state = next_state(state, step)
        path.append(state)

    assert path == [
        MigrationState.DUAL_WRITING,
        MigrationState.SWITCHED,
        MigrationState.CLEANED,
    ]


@pytest.mark.parametrize(
    ("state", "written"),
    [
        (MigrationState.DUAL_WRITING, (SpaceRole.SOURCE, SpaceRole.TARGET)),
        (MigrationState.SWITCHED, (SpaceRole.TARGET,)),
        (MigrationState.CLEANED, (SpaceRole.TARGET,)),
    ],
)
def test_publication_writes_to_both_spaces_only_while_dual_writing(
    state: MigrationState, written: tuple[SpaceRole, ...]
) -> None:
    assert written_spaces(state) == written


def test_every_state_writes_to_the_space_search_will_read_next() -> None:
    # The target is written from the start, so it is complete when search
    # moves to it; after the switch nothing writes to the old space.
    for state in MigrationState:
        assert SpaceRole.TARGET in written_spaces(state)


@pytest.mark.parametrize(
    ("started_at", "updated_at"),
    [
        (STARTED_AT, STARTED_AT - timedelta(seconds=1)),
        (datetime(2026, 10, 27, 9, 0), datetime(2026, 10, 27, 9, 0)),
    ],
)
def test_a_migration_refuses_impossible_times(
    started_at: datetime, updated_at: datetime
) -> None:
    with pytest.raises(EmbeddingMigrationError):
        EmbeddingMigration(
            migration_id=EmbeddingMigrationId(UUID(int=1)),
            source_space_id=EmbeddingSpaceId(UUID(int=2)),
            target_space_id=EmbeddingSpaceId(UUID(int=3)),
            target_configuration_id=IndexConfigurationId(UUID(int=4)),
            state=MigrationState.DUAL_WRITING,
            started_at=started_at,
            updated_at=updated_at,
        )
