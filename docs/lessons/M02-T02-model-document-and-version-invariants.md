# M02-T02 — Model Document and version invariants

Source task: [M02-T02](../curriculum/milestones/M02-domain-and-persistence/M02-T02-model-document-and-version-invariants.md)

## What you will do

This lesson includes coding. You will model a Document as an immutable domain
value with a stable typed ID, source provenance, separate content and
authorization versions, availability, and timestamps. You will then test how
the model responds to an unchanged snapshot, an ACL-only change, a content
change, and a tombstone.

This follows M02-T01: the project now has distinct IDs such as `DocumentId` and
`SourceId`. The new Document model will use those IDs and keep its rules in
plain Python. It will not import database or web frameworks. That makes its
version rules testable before PostgreSQL tables exist.

The invariant from M02-T01 is:

> Internal domain identities are project-owned values. External source
> identifiers stay scoped to their Source adapter and enter the domain only as
> provenance, alongside the internal IDs.

The original task file remains the acceptance source; this guide is the
beginner-friendly implementation sequence.

## Walkthrough

### Step 1 — Write down the version rule

**Purpose:** Make clear what counts as a content change and what counts as an
authorization change before defining the model.

**Decision:** Treat fingerprints as opaque, non-empty strings supplied by the
normalization and authorization layers. A changed content fingerprint advances
only `ContentVersion`; a changed access fingerprint advances only
`AuthorizationVersion`. The model will not decide how either fingerprint is
calculated.

**Action — No coding in this step:** Create
`docs/evidence/M02-T02-document-version-invariants.md`. Write the invariant in
your own words and record this prediction before coding:

```markdown
# M02-T02 — Document and version invariants evidence

Completed: pending

## Invariant

Write how this model keeps content and authorization versions independent.

## Prediction

The first focused test command will fail because the Document domain module
and its tests do not exist yet. Equal fingerprints should keep both versions;
changing only the authorization fingerprint should advance only its version.

## Verification

Pending.

## Reflection

Pending.
```

Read the completed M02-T01 evidence and confirm the provider-boundary rule is
still true. Check that no Document domain model exists yet:

```bash
rg --files src/knowledge_service tests | rg 'document|test_document'
```

Then run the deliberately red focused command:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_documents.py
```

**Check:** The search finds no Document model or test module, and pytest reports
that `tests/test_documents.py` does not exist. Record the observed failure
briefly in the evidence file.

### Step 2 — Define versions, availability, and provenance

**Purpose:** Give the Document domain model explicit values for its important
states. A content version and an authorization version are separate because a
permission change must not force text to be re-embedded, and a text change must
not pretend permissions changed.

**Decision:** Add `src/knowledge_service/documents.py`. Use frozen, slotted
dataclasses and a string enum. Versions start at 1 and reject zero or negative
numbers. Provenance stores the internal Source ID and the opaque source-owned
external ID; an optional source version is retained as context. Store no
provider SDK or persistence types in this module.

**Action — Coding step:** Create the module with these imports, failure type,
and value objects:

```python
"""Provider-neutral Document values and version invariants."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from knowledge_service.identifiers import DocumentId, SourceId


class InvalidDocument(ValueError):
    """A Document value or state transition violates a domain invariant."""


@dataclass(frozen=True, slots=True)
class ContentVersion:
    """Positive version number for a Document's text and structure."""

    number: int

    def __post_init__(self) -> None:
        if type(self.number) is not int or self.number < 1:
            raise InvalidDocument("content version must be a positive integer")

    def next(self) -> ContentVersion:
        return ContentVersion(self.number + 1)


@dataclass(frozen=True, slots=True)
class AuthorizationVersion:
    """Positive version number for a Document's access grants."""

    number: int

    def __post_init__(self) -> None:
        if type(self.number) is not int or self.number < 1:
            raise InvalidDocument("authorization version must be a positive integer")

    def next(self) -> AuthorizationVersion:
        return AuthorizationVersion(self.number + 1)


class DocumentAvailability(StrEnum):
    """Whether the Document may currently be retrieved."""

    AVAILABLE = "available"
    TOMBSTONED = "tombstoned"


@dataclass(frozen=True, slots=True)
class DocumentProvenance:
    """Source-owned identity and version used to locate the original item."""

    source_id: SourceId
    external_id: str
    source_version: str | None = None

    def __post_init__(self) -> None:
        if not self.external_id.strip():
            raise InvalidDocument("external source ID must not be empty")
        if self.source_version is not None and not self.source_version.strip():
            raise InvalidDocument("source version must not be empty")
```

`ContentVersion` and `AuthorizationVersion` are distinct even though both hold
an integer. `DocumentProvenance.external_id` is intentionally a string: the
source owns its format, and the value is not interchangeable with
`DocumentId`. It is paired with `SourceId` so two sources can have the same
external ID without referring to the same Document.

**Check:** Imports come only from Python's standard library and the project's
identifier module. Constructing either version with `0` or `True` raises
`InvalidDocument`; both start at a positive integer.

### Step 3 — Add the immutable Document snapshot

**Purpose:** Keep the data that describes one current Document together, and
validate its invariants at construction. A snapshot makes it possible to
compare incoming source state with known state without depending on a database
row.

**Decision:** Require timezone-aware UTC timestamps and non-empty fingerprints.
The caller supplies time so tests remain deterministic. `register()` creates
version 1 for both dimensions. Keep `created_at` stable; update `updated_at`
only when a fingerprint, provenance value, or availability state changes.

**Action — Coding step:** Append this helper and `Document` class to
`src/knowledge_service/documents.py`:

```python
def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidDocument(f"{field_name} must be a UTC-aware timestamp")


@dataclass(frozen=True, slots=True)
class Document:
    """Current provider-neutral state for one stable source item."""

    document_id: DocumentId
    provenance: DocumentProvenance
    content_version: ContentVersion
    content_fingerprint: str
    authorization_version: AuthorizationVersion
    authorization_fingerprint: str
    availability: DocumentAvailability
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.content_fingerprint.strip():
            raise InvalidDocument("content fingerprint must not be empty")
        if not self.authorization_fingerprint.strip():
            raise InvalidDocument("authorization fingerprint must not be empty")
        _require_utc(self.created_at, "created_at")
        _require_utc(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise InvalidDocument("updated_at must not precede created_at")

    @classmethod
    def register(
        cls,
        *,
        document_id: DocumentId,
        provenance: DocumentProvenance,
        content_fingerprint: str,
        authorization_fingerprint: str,
        observed_at: datetime,
    ) -> Document:
        """Create the first available snapshot for a source item."""
        return cls(
            document_id=document_id,
            provenance=provenance,
            content_version=ContentVersion(1),
            content_fingerprint=content_fingerprint,
            authorization_version=AuthorizationVersion(1),
            authorization_fingerprint=authorization_fingerprint,
            availability=DocumentAvailability.AVAILABLE,
            created_at=observed_at,
            updated_at=observed_at,
        )
```

UTC-aware means the timestamp has a defined zero UTC offset. Naive timestamps
and local-offset timestamps are rejected rather than silently guessed or
converted. The application or adapter chooses the observation time and passes
it in.

**Check:** A registered Document has content and authorization version `1`, is
available, and has equal creation and update timestamps. Invalid fingerprints
and invalid timestamp ordering raise `InvalidDocument`.

### Step 4 — Encode snapshot reconciliation and tombstoning

**Purpose:** Put version increments at one domain seam. Callers should not have
to remember which counter to update when source state changes.

**Decision:** Add `reconcile()` to compare fingerprints independently. If
nothing changes, return the same immutable object and preserve `updated_at`.
If only access changes, advance only the authorization version; if only content
changes, advance only the content version. Any incoming observation restores a
tombstoned item to available. `tombstone()` is idempotent and preserves both
version counters.

**Action — Coding step:** Add these methods inside `Document`, after
`register()`:

```python
    def reconcile(
        self,
        *,
        content_fingerprint: str,
        authorization_fingerprint: str,
        source_version: str | None,
        observed_at: datetime,
    ) -> Document:
        """Apply one observed snapshot and advance only changed dimensions."""
        provenance = replace(self.provenance, source_version=source_version)
        content_changed = content_fingerprint != self.content_fingerprint
        authorization_changed = (
            authorization_fingerprint != self.authorization_fingerprint
        )
        availability_changed = self.availability is DocumentAvailability.TOMBSTONED
        provenance_changed = provenance != self.provenance

        if not any(
            (content_changed, authorization_changed, availability_changed, provenance_changed)
        ):
            return self

        _require_utc(observed_at, "observed_at")
        if observed_at < self.updated_at:
            raise InvalidDocument("observed_at must not precede updated_at")

        return replace(
            self,
            provenance=provenance,
            content_version=(
                self.content_version.next()
                if content_changed
                else self.content_version
            ),
            content_fingerprint=content_fingerprint,
            authorization_version=(
                self.authorization_version.next()
                if authorization_changed
                else self.authorization_version
            ),
            authorization_fingerprint=authorization_fingerprint,
            availability=DocumentAvailability.AVAILABLE,
            updated_at=observed_at,
        )

    def tombstone(self, *, observed_at: datetime) -> Document:
        """Mark a missing source item unavailable without deleting its history."""
        if self.availability is DocumentAvailability.TOMBSTONED:
            return self

        _require_utc(observed_at, "observed_at")
        if observed_at < self.updated_at:
            raise InvalidDocument("observed_at must not precede updated_at")

        return replace(
            self,
            availability=DocumentAvailability.TOMBSTONED,
            updated_at=observed_at,
        )
```

`replace()` returns a new dataclass value and runs its validation again. A
tombstone keeps the row's logical identity and version history while making
availability explicit; later persistence can represent it without treating a
missing source response as permission to erase history.

**Check:** Review the four booleans in `reconcile()`: content, authorization,
availability, and provenance. Only the corresponding fingerprint changes its
own version. An unchanged snapshot returns `self`; tombstoning twice also
returns the existing state.

### Step 5 — Test the observable state transitions

**Purpose:** Prove the invariants through the public Document methods. These
tests catch accidental coupling between content and ACL updates, and ensure a
tombstone does not erase a Document or its version history.

**Decision:** Use fixed UUIDs and UTC timestamps. Test the model without
database fixtures because M02-T02 defines domain behavior, not persistence.
Include a typed failure for a naive timestamp and an invalid fingerprint.

**Action — Coding step:** Create `tests/test_documents.py` with this fixture
helper and imports:

```python
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
```

The fingerprints in the fixture are synthetic labels. In production the
normalization and authorization layers will provide stable fingerprints; this
domain module only compares them.

Add the successful and version-isolation cases:

```python
def test_unchanged_snapshot_preserves_versions_and_timestamp() -> None:
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
    assert result.updated_at == OBSERVED_AT


def test_acl_only_change_advances_authorization_version() -> None:
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


def test_content_change_advances_content_version_only() -> None:
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
```

The unchanged test also checks that the timestamp is not refreshed just because
the source was polled again. That avoids turning every synchronization into a
write when the source state is identical.

Add the tombstone and typed-failure cases:

```python
def test_tombstone_marks_unavailable_and_preserves_versions() -> None:
    document = make_document()

    result = document.tombstone(observed_at=OBSERVED_AT + timedelta(minutes=1))

    assert result.document_id == document.document_id
    assert result.availability is DocumentAvailability.TOMBSTONED
    assert result.content_version == document.content_version
    assert result.authorization_version == document.authorization_version
    assert result.tombstone(observed_at=OBSERVED_AT + timedelta(minutes=2)) is result


def test_naive_observation_time_raises_typed_failure() -> None:
    document = make_document()

    with pytest.raises(InvalidDocument, match="UTC-aware"):
        document.tombstone(observed_at=datetime(2026, 10, 3, 13, 0))


@pytest.mark.parametrize("version_type", (ContentVersion, AuthorizationVersion))
def test_versions_must_be_positive_integers(
    version_type: type[ContentVersion] | type[AuthorizationVersion],
) -> None:
    with pytest.raises(InvalidDocument):
        version_type(0)
```

The union annotation lists the only two version classes accepted by this
parameterized test. Both constructors take an integer. Keep this test focused
on the domain failure, not on database behavior.

**Check:** Run the focused test file:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_documents.py -vv
```

All tests pass without PostgreSQL. The unchanged, ACL-only, content-only, and
tombstone cases each demonstrate the expected versions and availability.

### Step 6 — Run the project gates and finish the evidence

**Purpose:** Check that the new domain types follow project standards and leave
the next persistence task with a verified, provider-neutral Document contract.

**Decision:** Run the narrow suite first, then the repository check and
documentation/hook checks. No migration or database fixture belongs in this
task because its outcome is a domain model with no persistence imports.

**Action — No coding in this step:** Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_documents.py -vv
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
git diff --check
```

Record exact results in `docs/evidence/M02-T02-document-version-invariants.md`.
List the changed files and state that `documents.py` imports only standard
library types and project-owned identifiers. Confirm that you used synthetic
IDs/fingerprints and no credentials or private source content.

Answer the source task's reflection in your own words:

1. What complexity do separate content and authorization versions hide from a
   persistence caller?
2. Which alternative would make later indexing or ACL updates harder, and why?
3. What production failure could escape if the ACL-only or tombstone test were
   removed?

**Check:** The focused tests, `make check`, documentation validation, hooks,
and diff checks pass. The evidence includes the initial prediction and observed
result, exact verification outcomes, and your reflection.

## Completion checklist

- [ ] The domain module models Document, Content Version, Authorization
  Version, availability, provenance, and timestamps.
- [ ] Domain imports contain no FastAPI, SQLAlchemy, SDK, or provider types.
- [ ] Unchanged snapshots preserve versions and `updated_at`.
- [ ] ACL-only changes advance only Authorization Version.
- [ ] Content changes advance only Content Version.
- [ ] Tombstones preserve the Document identity and version history.
- [ ] Naive timestamps and invalid versions produce `InvalidDocument`.
- [ ] Focused tests and all repository gates pass.
- [ ] Evidence and the three reflection answers are complete.

When you finish Step 1, share the first test result or the code you wrote and
we can review the behavior together.
