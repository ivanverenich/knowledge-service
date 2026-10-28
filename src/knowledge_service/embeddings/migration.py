"""The states of moving search from one embedding model to another.

Vectors of different models, or of one model at different dimensions, can
never be compared, so search reads exactly one Embedding Space: the current
one. Moving to a new model goes through these states, and search keeps
working in every one of them:

| State | Publication writes to | Search reads |
|---|---|---|
| `DUAL_WRITING` | the source and the target space | the source space |
| `SWITCHED` | the target space | the target space |
| `CLEANED` | the target space | the target space |

1. **Dual-write.** Starting creates the target space, not current, and from
   then on every publication embeds into both spaces.
2. **Backfill.** While dual-writing, Documents published before the start
   are embedded into the target space too, a few at a time.
3. **Switch.** Once the target has a vector for every searchable Chunk the
   source has, one transaction makes the target current. Search moves from
   the old vectors to the new ones at that commit, never to a mixture.
4. **Clean up.** The source space, no longer read or written, is deleted
   with its vectors.

Only these moves exist; any other raises `InvalidMigrationTransition`.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from knowledge_service.embeddings.port import EmbeddingModelIdentity
from knowledge_service.identifiers import (
    EmbeddingMigrationId,
    EmbeddingSpaceId,
    IndexConfigurationId,
)


class EmbeddingMigrationError(Exception):
    """Base error for moving search to another embedding model."""


class InvalidMigrationTransition(EmbeddingMigrationError):
    """The migration is not in a state that allows this step."""


class MigrationState(StrEnum):
    """Where one migration is."""

    DUAL_WRITING = "dual_writing"
    SWITCHED = "switched"
    CLEANED = "cleaned"


class MigrationStep(StrEnum):
    """A step that moves a migration to its next state."""

    SWITCH = "switch"
    CLEAN_UP = "clean_up"


class SpaceRole(StrEnum):
    """Which of a migration's two spaces."""

    SOURCE = "source"
    TARGET = "target"


_NEXT_STATE = {
    (MigrationState.DUAL_WRITING, MigrationStep.SWITCH): MigrationState.SWITCHED,
    (MigrationState.SWITCHED, MigrationStep.CLEAN_UP): MigrationState.CLEANED,
}

_WRITTEN = {
    MigrationState.DUAL_WRITING: (SpaceRole.SOURCE, SpaceRole.TARGET),
    MigrationState.SWITCHED: (SpaceRole.TARGET,),
    MigrationState.CLEANED: (SpaceRole.TARGET,),
}


def next_state(state: MigrationState, step: MigrationStep) -> MigrationState:
    """Return the state after `step`, or raise if `state` does not allow it."""
    after = _NEXT_STATE.get((state, step))
    if after is None:
        raise InvalidMigrationTransition(
            f"a {state.value} migration cannot {step.value}"
        )
    return after


def written_spaces(state: MigrationState) -> tuple[SpaceRole, ...]:
    """The spaces every publication must write to in this state."""
    return _WRITTEN[state]


@dataclass(frozen=True, slots=True)
class EmbeddingSpace:
    """One stored Embedding Space: its identity and whether search reads it."""

    space_id: EmbeddingSpaceId
    identity: EmbeddingModelIdentity
    is_current: bool


@dataclass(frozen=True, slots=True)
class EmbeddingMigration:
    """One move from a source Embedding Space to a target one.

    A space is None once it has been deleted; cleaning up deletes the source.
    `target_configuration_id` names the index configuration the target space
    is built for.
    """

    migration_id: EmbeddingMigrationId
    source_space_id: EmbeddingSpaceId | None
    target_space_id: EmbeddingSpaceId | None
    target_configuration_id: IndexConfigurationId
    state: MigrationState
    started_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for value, name in (
            (self.started_at, "started_at"),
            (self.updated_at, "updated_at"),
        ):
            if value.utcoffset() != timedelta(0):
                raise EmbeddingMigrationError(f"{name} must be a UTC-aware timestamp")
        if self.updated_at < self.started_at:
            raise EmbeddingMigrationError("updated_at must not precede started_at")
