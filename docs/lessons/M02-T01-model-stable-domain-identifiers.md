# M02-T01 — Model stable domain identifiers

Source task: [M02-T01](../curriculum/milestones/M02-domain-and-persistence/M02-T01-model-stable-domain-identifiers.md)

## What you will do

This lesson includes coding. You will add small, immutable identifier value
objects for Sources, Documents, Chunks, Users, Groups, Conversations,
Synchronization Runs, Evaluation Runs, and jobs. You will also define the
boundary rule for provider-owned identifiers and test serialization, identifier
separation, and invalid input.

This starts the domain-and-persistence milestone. The completed walking
skeleton already keeps provider details behind a project-owned interface. This
lesson preserves that idea for identity: application code receives a precise
domain identifier instead of an unlabelled UUID or an OpenAI, Confluence, or
OIDC identifier.

The invariant inherited from M01 is:

> Project-owned contracts remain independent of external providers, and a
> boundary translates external data into safe project-owned values or typed
> failures.

The original curriculum task remains the acceptance source. This guide is the
working sequence for completing it.

## Walkthrough

### Step 1 — Record the invariant and red-test prediction

**Purpose:** Establish the rule this change must preserve and predict the first
observable failure before implementation. This makes the evidence describe
intent rather than merely reporting the finished code.

**Decision:** Keep the task log in
`docs/evidence/M02-T01-stable-domain-identifiers.md`. The identifiers will be
domain values: they must not import FastAPI, Pydantic, SQLAlchemy, an SDK, or a
provider-specific type.

**Action — No coding in this step:** Create the evidence file with the
following structure, but write the invariant in your own words:

```markdown
# M02-T01 — Stable domain identifiers evidence

Completed: pending

## Invariant

Write the provider-neutral boundary rule in your own words.

## Prediction

The first narrow test command will fail because the identifiers module and its
tests do not exist yet. After implementation, each identifier kind should keep
its type, round-trip through its canonical UUID string, and reject malformed
input with InvalidIdentifier.

## Verification

Pending.

## Reflection

Pending.
```

Run the deliberately red command:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_indentifiers.py
```

**Check:** Pytest reports that `tests/test_identifiers.py` does not exist. Add a
short summary of that result to the evidence file; do not paste a long terminal
transcript.

### Step 2 — Create the stable identifier abstraction

**Purpose:** Give every durable entity an immutable identity that cannot change
when its name, content, permissions, or provider metadata changes. A value
object is a small type defined by its value rather than by mutable object
identity.

**Decision:** Add `src/knowledge_service/identifiers.py`. Store a standard
library `UUID` inside a frozen, slotted dataclass. Generate new internal IDs
with UUID version 7, which Python 3.14 supports, and expose one canonical string
serialization method. Parsing failures become the project-owned
`InvalidIdentifier` exception and do not retain or echo the rejected input.

**Action — Coding step:** Create
`src/knowledge_service/identifiers.py` with this base behavior:

```python
"""Stable, provider-neutral identifiers for domain entities.

New internal IDs are created inside the application with ``new()``. Serialized
internal IDs may re-enter through transport and persistence adapters via
``parse()``. Provider-owned IDs may enter only through source or identity
adapters; they remain scoped to their owner and must never be cast into these
internal ID types.
"""

from dataclasses import dataclass
from typing import Self
from uuid import UUID, uuid7


class InvalidIdentifier(ValueError):
    """A serialized value is not a valid stable domain identifier."""

    def __init__(self, identifier_type: str) -> None:
        self.identifier_type = identifier_type
        super().__init__(f"invalid {identifier_type}")


@dataclass(frozen=True, slots=True)
class StableIdentifier:
    """Shared UUID behavior; callers should use a concrete identifier type."""

    value: UUID

    @classmethod
    def new(cls) -> Self:
        """Create a new project-owned identifier."""
        return cls(uuid7())

    @classmethod
    def parse(cls, raw: str) -> Self:
        """Reconstruct a serialized internal identifier at a boundary."""
        try:
            return cls(UUID(raw))
        except ValueError as error:
            raise InvalidIdentifier(cls.__name__) from error

    def serialize(self) -> str:
        """Return the canonical UUID representation for storage or transport."""
        return str(self.value)
```

`frozen=True` prevents reassignment after construction. `slots=True` keeps the
object small and prevents arbitrary attributes. `Self` means `SourceId.parse()`
returns `SourceId`, while `DocumentId.parse()` returns `DocumentId`.

The module docstring is also the external-ID rule required by the task. A
Confluence page ID or OIDC subject may arrive in its owning adapter, but it is
not an internal `DocumentId` or `UserId`. Later persistence work can store that
external reference together with its Source or issuer.

**Check:** Read the imports from top to bottom. They must all come from the
Python standard library. Confirm that `InvalidIdentifier` exposes the expected
identifier kind but neither stores nor prints the rejected raw value.

### Step 3 — Define every concrete identifier kind

**Purpose:** Distinct classes let Pyright catch mistakes such as supplying a
`DocumentId` to a function that requires a `SourceId`. They also make equal UUID
payloads unequal when they represent different domain concepts.

**Decision:** Define nine explicit final classes rather than a generic
`EntityId[T]`. The names match `CONTEXT.md`, and explicit types remain easy to
read in later domain models and SQL mappings. Both kinds of run named by the
domain are included.

**Action — Coding step:** Append these classes to
`src/knowledge_service/identifiers.py`:

```python
class SourceId(StableIdentifier):
    """Internal identity of a Source."""


class DocumentId(StableIdentifier):
    """Internal identity of a Document."""


class ChunkId(StableIdentifier):
    """Internal identity of a Chunk."""


class UserId(StableIdentifier):
    """Internal identity of a User."""


class GroupId(StableIdentifier):
    """Internal identity of a Group."""


class ConversationId(StableIdentifier):
    """Internal identity of a Conversation."""


class SynchronizationRunId(StableIdentifier):
    """Internal identity of a Synchronization Run."""


class EvaluationRunId(StableIdentifier):
    """Internal identity of an Evaluation Run."""


class JobId(StableIdentifier):
    """Internal identity of a durable job."""
```

Do not add IDs for future entities that M02-T01 did not request. Do not replace
the existing HTTP `request_id`: it is request-correlation metadata, not one of
these durable domain entities.

**Check:** There is one type for each item in the task outcome: Source,
Document, Chunk, User, Group, Conversation, both domain run types, and job.
None of the concrete types adds provider fields or mutable state.

### Step 4 — Test the identifier properties

**Purpose:** Prove the same behavioral properties across every identifier type:
canonical serialization round-trips, distinct kinds cannot compare equal, and
malformed boundary input produces a typed failure.

**Decision:** Use deterministic `pytest` parameterization over fixed UUIDs.
This is a property test because the same invariant is exercised for every
identifier kind and every selected value; an additional property-testing
dependency is unnecessary for this bounded value object.

**Action — Coding step:** Create `tests/test_indentifiers.py`:

```python
"""Tests for stable domain identifiers."""

from itertools import combinations

import pytest

from knowledge_service.identifiers import (
    ChunkId,
    ConversationId,
    DocumentId,
    EvaluationRunId,
    GroupId,
    InvalidIdentifier,
    JobId,
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
    ConversationId,
    SynchronizationRunId,
    EvaluationRunId,
    JobId,
)

UUID_STRINGS = (
    "00000000-0000-0000-0000-000000000001",
    "ffffffff-ffff-4fff-8fff-ffffffffffff",
)


@pytest.mark.parametrize("identifier_type", ID_TYPES)
@pytest.mark.parametrize("raw", UUID_STRINGS)
def test_identifier_serialization_round_trip(
    identifier_type: type[StableIdentifier], raw: str
) -> None:
    identifier = identifier_type.parse(raw)

    restored = identifier_type.parse(identifier.serialize())

    assert restored == identifier
    assert restored.serialize() == raw


@pytest.mark.parametrize(
    ("left_type", "right_type"), tuple(combinations(ID_TYPES, 2))
)
def test_identifier_kinds_are_not_interchangeable(
    left_type: type[StableIdentifier], right_type: type[StableIdentifier]
) -> None:
    raw = "00000000-0000-0000-0000-000000000001"

    assert left_type.parse(raw) != right_type.parse(raw)


@pytest.mark.parametrize("identifier_type", ID_TYPES)
def test_invalid_serialized_identifier_raises_typed_failure(
    identifier_type: type[StableIdentifier],
) -> None:
    with pytest.raises(InvalidIdentifier) as captured:
        identifier_type.parse("not-a-uuid")

    assert captured.value.identifier_type == identifier_type.__name__
    assert "not-a-uuid" not in str(captured.value)


@pytest.mark.parametrize("identifier_type", ID_TYPES)
def test_new_identifier_uses_uuid_version_7(
    identifier_type: type[StableIdentifier],
) -> None:
    identifier = identifier_type.new()

    assert type(identifier) is identifier_type
    assert identifier.value.version == 7
```

The pairwise test uses one identical UUID payload for every pair of classes. It
therefore checks the important edge case: identity includes both the UUID and
its domain kind. The invalid-input test checks the public exception rather than
the internal `uuid.UUID` error.

**Check:** Run the narrow suite:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_indentifiers.py -vv
```

All cases should pass without a database, credentials, network access, or a
provider SDK.

### Step 5 — Prove the static type boundary

**Purpose:** Runtime tests show value behavior, but the main protection against
passing one valid identifier kind where another is required comes from strict
static type checking.

**Decision:** Plant one temporary misuse, observe Pyright reject it, and then
remove the misuse. Do not keep intentionally invalid code in the repository.

**Action — Coding step:** Temporarily append this probe to
`tests/test_identifiers.py`:

```python
def _requires_source_id(source_id: SourceId) -> None:
    pass


_requires_source_id(
    DocumentId.parse("00000000-0000-0000-0000-000000000001")
)
```

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project make typecheck
```

Record the short Pyright error summary in the evidence file. Then delete the
entire temporary probe and run the same command again.

**Check:** The first run reports that `DocumentId` is incompatible with
`SourceId`; the second run reports zero type errors. Confirm with
`git diff --check` that removing the probe left no accidental whitespace.

### Step 6 — Verify and record acceptance evidence

**Purpose:** Confirm the narrow capability works within the complete project
and leave concise evidence that the next domain-model task can trust.

**Decision:** Run the focused test before the repository gates. Do not make a
live provider call. Existing architecture and domain-language documents remain
accurate, so this task needs no speculative documentation changes beyond the
identifier boundary rule and evidence record.

**Action — No coding in this step:** Run these commands in order:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_indentifiers.py -vv
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
git diff --check
```

Update `docs/evidence/M02-T01-stable-domain-identifiers.md` with brief, exact
results. State that the deliverable is
`src/knowledge_service/identifiers.py`, that the module contains no
provider-specific imports, and that no secrets or private content entered the
tests or evidence.

Answer the task reflection in your own words:

1. What complexity do the identifier types hide from a caller?
2. Which alternative would make M02-T02 harder, and why?
3. What production failure could escape if the negative test were removed?

Only after every acceptance item is satisfied, create the requested checkpoint
with the intent `m02-t01: model stable domain identifiers`.

**Check:** The focused suite and all repository gates pass. The evidence has
the invariant, initial prediction and observed red result, static-type failure
probe, final command results, security/provider-neutrality confirmation, and
your own reflection.

## Completion checklist

- [ ] Nine concrete domain identifier types exist.
- [ ] Internal creation uses UUIDv7 and identifiers are immutable.
- [ ] Canonical string serialization round-trips for every type.
- [ ] Equal UUID payloads in different identifier kinds remain unequal.
- [ ] Invalid serialized input raises `InvalidIdentifier` without echoing it.
- [ ] Pyright was observed rejecting `DocumentId` where `SourceId` is required.
- [ ] The module states where internal serialized IDs and external IDs may enter.
- [ ] Focused tests, `make check`, documentation validation, and diff checks pass.
- [ ] Evidence and the three reflection answers are complete.

When you have completed Step 1, share the evidence draft or any unexpected
test output and we can review it before you continue.
