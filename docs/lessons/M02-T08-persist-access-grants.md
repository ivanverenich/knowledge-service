# M02-T08 — Persist Access Grants

Source task: [M02-T08](../curriculum/milestones/M02-domain-and-persistence/M02-T08-persist-access-grants.md)

## What you will build

This lesson includes coding. It stores **Access Grants**: one row per subject allowed to read a Document.

An **Access Grant** holds:

- the Document it applies to;
- one subject — everyone, one user, or one group;
- the time it was granted.

One rule keeps the table honest: a row names exactly one subject. Neither "everyone plus a user" nor "a user with no id" can be stored.

| Property | What it means |
|---|---|
| One subject per row | A CHECK makes `subject_id` null exactly when the kind is `public`. |
| No duplicates | A unique constraint stops the same subject being granted twice for one Document. |
| Queryable | Public, user, group, and no-access are told apart by SQL, with no application filtering. |

## Before you begin

- `src/knowledge_service/identifiers.py` has `UserId` and `GroupId`. It has no identifier for a grant, so you add one.
- `src/knowledge_service/persistence.py` holds the `documents` and `chunks` tables plus their read and write functions. This lesson adds to that module.
- `documents.py` and `chunks.py` show the pattern: frozen dataclass, validation in `__post_init__`, one typed `ValueError` per module.
- Integration tests apply migrations in a **synchronous** fixture, because `migrations/env.py` calls `asyncio.run()` and cannot run inside an async test.

## Walkthrough

### Step 1 — No coding in this step: write down the rule you are preserving

**Purpose:** Fix what a stored grant set must guarantee before writing the table.

**Decision:** Keep notes in `docs/evidence/M02-T08-access-grants.md`.

**Action:** Read the M02-T07 evidence file under `docs/evidence/`. Create `docs/evidence/M02-T08-access-grants.md`:

```markdown
# M02-T08 — Access Grants evidence

Completed: pending

## Invariant

Write down which subjects a grant may name, what a grant set may contain, and
how a query tells public, user, group, and no-access apart.

## Prediction

Write what you expect before adding the table.

## Verification

Pending.

## Reflection

Pending.
```

Confirm nothing to be created exists:

```bash
find src tests migrations -name '*access*' | grep -v __pycache__
```

**Check:** The evidence file holds your invariant and prediction, and the search shows no `access.py`, no migration, and no grant table in `persistence.py`. Add no code and edit no task or progress files in this step.

### Step 2 — Coding step: add the grant identifier

**Purpose:** Give a grant a typed identity, like every other entity in the project.

**Decision:** Add `AccessGrantId` next to the other identifiers. It uses the shared `StableIdentifier` base, so it gets UUIDv7 creation, parsing, and serialization for free.

**Action:** In `src/knowledge_service/identifiers.py`, add after `GroupId`:

```python
class AccessGrantId(StableIdentifier):
    """Internal identity of an Access Grant."""
```

Then in `tests/test_indentifiers.py`, add `AccessGrantId` to the import list and to `ID_TYPES`. The existing parametrized tests then cover the new type: round trip, type mismatch, invalid input, and UUIDv7.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_indentifiers.py -q
```

### Step 3 — Coding step: model the subject and the grant

**Purpose:** Make "ownerless" and "malformed" grants unrepresentable before the database sees them.

**Decision:** A `Subject` names exactly one reader: `public` with no identifier, or `user` / `group` with an identifier of the matching type. `__post_init__` rejects the other three combinations, so the value cannot be built wrong. `AccessGrant` carries the grant id, the Document, the subject, and a UTC time. `granted_subjects()` checks the whole set: one Document, and no subject granted twice, returned in a stable order.

**Action:** Create `src/knowledge_service/access.py`:

```python
"""Access Grant values: which subjects may read a Document.

Every grant names exactly one subject: everyone, one user, or one group.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from knowledge_service.identifiers import AccessGrantId, DocumentId, GroupId, UserId


class InvalidGrant(ValueError):
    """An Access Grant value or set violates a domain invariant."""


class SubjectKind(StrEnum):
    """The kind of reader a grant names."""

    PUBLIC = "public"
    USER = "user"
    GROUP = "group"


@dataclass(frozen=True, slots=True)
class Subject:
    """Exactly one reader: everyone, one user, or one group."""

    kind: SubjectKind
    identifier: UserId | GroupId | None = None

    def __post_init__(self) -> None:
        if self.kind is SubjectKind.PUBLIC:
            if self.identifier is not None:
                raise InvalidGrant("a public grant must not name a subject")
            return
        if self.identifier is None:
            raise InvalidGrant("a user or group grant must name a subject")
        if self.kind is SubjectKind.USER and not isinstance(self.identifier, UserId):
            raise InvalidGrant("a user grant must name a UserId")
        if self.kind is SubjectKind.GROUP and not isinstance(self.identifier, GroupId):
            raise InvalidGrant("a group grant must name a GroupId")

    @classmethod
    def public(cls) -> Subject:
        """Everyone may read the Document."""
        return cls(kind=SubjectKind.PUBLIC)

    @classmethod
    def user(cls, user_id: UserId) -> Subject:
        """One user may read the Document."""
        return cls(kind=SubjectKind.USER, identifier=user_id)

    @classmethod
    def group(cls, group_id: GroupId) -> Subject:
        """Members of one group may read the Document."""
        return cls(kind=SubjectKind.GROUP, identifier=group_id)


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidGrant(f"{field_name} must be a UTC-aware timestamp")


@dataclass(frozen=True, slots=True)
class AccessGrant:
    """One normalized statement that a subject may read a Document."""

    grant_id: AccessGrantId
    document_id: DocumentId
    subject: Subject
    granted_at: datetime

    def __post_init__(self) -> None:
        _require_utc(self.granted_at, "granted_at")


def granted_subjects(
    *,
    document_id: DocumentId,
    grants: Sequence[AccessGrant],
) -> tuple[AccessGrant, ...]:
    """Return one grant per subject for a single Document, in a stable order."""
    for grant in grants:
        if grant.document_id != document_id:
            raise InvalidGrant("every grant must belong to the same document")

    ordered = tuple(
        sorted(
            grants,
            key=lambda grant: (
                grant.subject.kind.value,
                str(grant.subject.identifier),
            ),
        )
    )
    seen: set[Subject] = set()
    for grant in ordered:
        if grant.subject in seen:
            raise InvalidGrant("a subject may be granted at most once per document")
        seen.add(grant.subject)
    return ordered
```

The `isinstance` checks matter because `UserId` and `GroupId` are separate classes with the same shape. `Subject(kind=USER, identifier=group_id)` is exactly the malformed grant the table must never see.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python -c "import knowledge_service.access as a; print(a.Subject.public(), a.SubjectKind.USER)"
```

### Step 4 — Coding step: create the table, in a revision and in Python

**Purpose:** Store the grants where SQL can filter on them.

**Decision:** One row per grant, keyed by `grant_id`, with `ON DELETE CASCADE` to `documents`. Two CHECK constraints carry the subject rule: one for the three known kinds, and `(subject_kind = 'public') = (subject_id is null)`, which rejects both a public grant that names someone and a user or group grant with no id. The unique constraint is `NULLS NOT DISTINCT`, because PostgreSQL treats NULL as different from NULL — without it, one Document could hold two public grants.

**Action:** Generate an empty revision, then write it by hand:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic revision -m "access grants"
```

Keep the generated identifier and the `down_revision` Alembic fills in — `559156a3fbd6`. Remove the generated `typing` and `branch_labels` lines as before, and replace `<the identifier Alembic generated>` with the real identifier.

```python
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "<the identifier Alembic generated>"
down_revision: str | Sequence[str] | None = "559156a3fbd6"


def upgrade() -> None:
    """Create one row per subject allowed to read a Document."""
    op.create_table(
        "access_grants",
        sa.Column("grant_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subject_kind", sa.String(length=8), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "subject_kind in ('public', 'user', 'group')",
            name="ck_access_grants_subject_kind",
        ),
        sa.CheckConstraint(
            "(subject_kind = 'public') = (subject_id is null)",
            name="ck_access_grants_subject_id",
        ),
        sa.UniqueConstraint(
            "document_id",
            "subject_kind",
            "subject_id",
            name="uq_access_grants_document_subject",
            postgresql_nulls_not_distinct=True,
        ),
    )


def downgrade() -> None:
    """Remove every stored Access Grant."""
    op.drop_table("access_grants")
```

Then add the matching table to `src/knowledge_service/persistence.py`, after the `chunks` table:

```python
access_grants = sa.Table(
    "access_grants",
    metadata,
    sa.Column("grant_id", sa.Uuid(), primary_key=True),
    sa.Column(
        "document_id",
        sa.Uuid(),
        sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("subject_kind", sa.String(length=8), nullable=False),
    sa.Column("subject_id", sa.Uuid(), nullable=True),
    sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "subject_kind in ('public', 'user', 'group')",
        name="ck_access_grants_subject_kind",
    ),
    sa.CheckConstraint(
        "(subject_kind = 'public') = (subject_id is null)",
        name="ck_access_grants_subject_id",
    ),
    sa.UniqueConstraint(
        "document_id",
        "subject_kind",
        "subject_id",
        name="uq_access_grants_document_subject",
        postgresql_nulls_not_distinct=True,
    ),
)
```

**Check:** Five revisions, one head, and both definitions agree:

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run alembic history
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run python -c "
from knowledge_service.persistence import access_grants
print([c.name for c in access_grants.columns])
print(sorted(c.name for c in access_grants.constraints if c.name))
"
```

Expected: `['grant_id', 'document_id', 'subject_kind', 'subject_id', 'granted_at']` and the three named constraints.

### Step 5 — Coding step: read and write grants

**Purpose:** Give callers one way to set a Document's access and one way to read it back.

**Decision:** `replace_access_grants` validates the set, deletes the Document's existing grants, and inserts the new ones on the caller's connection. Like `replace_chunks`, it takes a connection and never commits, so the delete and the insert share the caller's transaction. `load_access_grants` reads them back as domain values.

**Action:** In `src/knowledge_service/persistence.py`, extend the imports:

```python
from knowledge_service.access import (
    AccessGrant,
    InvalidGrant,
    Subject,
    SubjectKind,
    granted_subjects,
)
from knowledge_service.identifiers import (
    AccessGrantId,
    ChunkId,
    DocumentId,
    GroupId,
    SourceId,
    UserId,
)
```

Then add the two row helpers next to the chunk ones, and the two functions at the end of the module:

```python
def _grant_row(grant: AccessGrant) -> dict[str, object]:
    identifier = grant.subject.identifier
    return {
        "grant_id": grant.grant_id.value,
        "document_id": grant.document_id.value,
        "subject_kind": grant.subject.kind.value,
        "subject_id": identifier.value if identifier is not None else None,
        "granted_at": grant.granted_at,
    }


def _grant_from(record: Mapping[str, object]) -> AccessGrant:
    try:
        kind = SubjectKind(cast(str, record["subject_kind"]))
    except ValueError:
        raise InvalidGrant("stored subject kind is not a known value") from None

    stored_id = cast("UUID | None", record["subject_id"])
    if kind is SubjectKind.PUBLIC:
        subject = Subject.public()
    elif kind is SubjectKind.USER:
        subject = Subject.user(UserId(cast(UUID, stored_id)))
    else:
        subject = Subject.group(GroupId(cast(UUID, stored_id)))

    return AccessGrant(
        grant_id=AccessGrantId(cast(UUID, record["grant_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        subject=subject,
        granted_at=cast(datetime, record["granted_at"]),
    )


async def replace_access_grants(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
    grant_run: Sequence[AccessGrant],
) -> tuple[AccessGrant, ...]:
    """Replace every stored Access Grant for a Document with the given set."""
    ordered = granted_subjects(document_id=document_id, grants=grant_run)

    await connection.execute(
        sa.delete(access_grants).where(
            access_grants.c.document_id == document_id.value
        )
    )
    if ordered:
        await connection.execute(
            sa.insert(access_grants), [_grant_row(grant) for grant in ordered]
        )
    return ordered


async def load_access_grants(
    connection: AsyncConnection,
    *,
    document_id: DocumentId,
) -> tuple[AccessGrant, ...]:
    """Read the stored Access Grants for a Document."""
    statement = (
        sa.select(access_grants)
        .where(access_grants.c.document_id == document_id.value)
        .order_by(access_grants.c.subject_kind, access_grants.c.subject_id)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_grant_from(dict(record)) for record in records)
```

`_grant_from` rebuilds the right identifier class from `subject_kind`, which is what makes the round trip return a `Subject` the domain accepts. An unknown stored kind becomes a typed `InvalidGrant` rather than a bare `ValueError`, like `as_document` does for availability.

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pyright src/knowledge_service/persistence.py
```

### Step 6 — Coding step: test the rules without a database

**Purpose:** Pin down the subject and set rules, which are pure logic.

**Decision:** Test through the public interface. The successful case proves a mixed set comes back in a stable order. The edge case is the same subject granted twice. The typed failures cover a public subject that names someone, a user kind holding a `GroupId`, a grant for another Document, and a naive time.

**Action:** Create `tests/test_access.py`:

```python
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
```

**Check:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_access.py -vv
```

Expected: 6 passed, without a database.

### Step 7 — Coding step: prove the four cases in PostgreSQL

**Purpose:** Show that public, user, group, and no-access are told apart by SQL alone.

**Decision:** One async integration test with a synchronous migration fixture. It creates four Documents, gives three of them one grant each, and runs a single `CASE` query that classifies every Document. It then tries four bad rows directly against the table: public with an id, user with no id, a duplicate public grant, and an unknown kind.

**Action:** Create `tests/test_access_integration.py`:

```python
"""Real PostgreSQL tests for Access Grants."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from knowledge_service.access import AccessGrant, Subject
from knowledge_service.documents import Document, DocumentProvenance
from knowledge_service.identifiers import (
    AccessGrantId,
    DocumentId,
    GroupId,
    SourceId,
    UserId,
)
from knowledge_service.persistence import (
    load_access_grants,
    replace_access_grants,
    upsert_document,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OBSERVED_AT = datetime(2026, 10, 7, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")
USER_ID = UserId.parse("00000000-0000-0000-0000-000000000030")
GROUP_ID = GroupId.parse("00000000-0000-0000-0000-000000000040")

INSERT_SOURCE = text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)

INSERT_RAW_GRANT = text(
    "INSERT INTO access_grants (grant_id, document_id, subject_kind, subject_id,"
    " granted_at) VALUES (:grant_id, :document_id, :subject_kind, :subject_id,"
    " now())"
)

ACCESS_CLASSIFICATION = text(
    """
    SELECT d.document_id,
           CASE
               WHEN NOT EXISTS (
                   SELECT 1 FROM access_grants g WHERE g.document_id = d.document_id
               ) THEN 'none'
               WHEN EXISTS (
                   SELECT 1 FROM access_grants g
                   WHERE g.document_id = d.document_id
                     AND g.subject_kind = 'public'
               ) THEN 'public'
               WHEN EXISTS (
                   SELECT 1 FROM access_grants g
                   WHERE g.document_id = d.document_id
                     AND g.subject_kind = 'group'
               ) THEN 'group'
               ELSE 'user'
           END AS access_class
    FROM documents d
    """
)


@pytest.fixture
def migrated_database(monkeypatch: pytest.MonkeyPatch) -> str:
    """Apply migrations synchronously; Alembic's env.py runs its own event loop."""
    database_url = os.environ.get("KNOWLEDGE_SERVICE_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set KNOWLEDGE_SERVICE_TEST_DATABASE_URL to a disposable database")

    monkeypatch.setenv("KNOWLEDGE_SERVICE_DATABASE_URL", database_url)
    command.upgrade(Config(str(PROJECT_ROOT / "alembic.ini")), "head")
    return database_url


def make_document(external_id: str) -> Document:
    return Document.register(
        document_id=DocumentId.new(),
        provenance=DocumentProvenance(
            source_id=SOURCE_ID,
            external_id=external_id,
            source_version="17",
        ),
        content_fingerprint=f"content-{external_id}",
        authorization_fingerprint=f"acl-{external_id}",
        observed_at=OBSERVED_AT,
    )


def make_grant(document: Document, subject: Subject) -> AccessGrant:
    return AccessGrant(
        grant_id=AccessGrantId.new(),
        document_id=document.document_id,
        subject=subject,
        granted_at=OBSERVED_AT,
    )


async def access_classes(connection: AsyncConnection) -> dict[str, str]:
    rows = (await connection.execute(ACCESS_CLASSIFICATION)).mappings().all()
    return {str(row["document_id"]): str(row["access_class"]) for row in rows}


@pytest.mark.integration
async def test_queries_tell_public_user_group_and_no_access_apart(
    migrated_database: str,
) -> None:
    engine = create_async_engine(
        make_url(migrated_database).set(drivername="postgresql+psycopg")
    )
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM access_grants"))
            await connection.execute(text("DELETE FROM chunks"))
            await connection.execute(text("DELETE FROM documents"))
            await connection.execute(text("DELETE FROM sources"))
            await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})

        public = make_document("public-page")
        user_only = make_document("user-page")
        group_only = make_document("group-page")
        secret = make_document("secret-page")

        async with engine.begin() as connection:
            for document in (public, user_only, group_only, secret):
                await upsert_document(connection, document)
            await replace_access_grants(
                connection,
                document_id=public.document_id,
                grant_run=[make_grant(public, Subject.public())],
            )
            await replace_access_grants(
                connection,
                document_id=user_only.document_id,
                grant_run=[make_grant(user_only, Subject.user(USER_ID))],
            )
            await replace_access_grants(
                connection,
                document_id=group_only.document_id,
                grant_run=[make_grant(group_only, Subject.group(GROUP_ID))],
            )

        async with engine.begin() as connection:
            classes = await access_classes(connection)
            stored = await load_access_grants(
                connection, document_id=group_only.document_id
            )

        assert classes[str(public.document_id.value)] == "public"
        assert classes[str(user_only.document_id.value)] == "user"
        assert classes[str(group_only.document_id.value)] == "group"
        assert classes[str(secret.document_id.value)] == "none"
        assert [grant.subject for grant in stored] == [Subject.group(GROUP_ID)]

        async with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    INSERT_RAW_GRANT,
                    {
                        "grant_id": AccessGrantId.new().value,
                        "document_id": user_only.document_id.value,
                        "subject_kind": "public",
                        "subject_id": USER_ID.value,
                    },
                )

        async with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    INSERT_RAW_GRANT,
                    {
                        "grant_id": AccessGrantId.new().value,
                        "document_id": user_only.document_id.value,
                        "subject_kind": "user",
                        "subject_id": None,
                    },
                )

        async with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    INSERT_RAW_GRANT,
                    {
                        "grant_id": AccessGrantId.new().value,
                        "document_id": public.document_id.value,
                        "subject_kind": "public",
                        "subject_id": None,
                    },
                )

        async with engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    INSERT_RAW_GRANT,
                    {
                        "grant_id": AccessGrantId.new().value,
                        "document_id": user_only.document_id.value,
                        "subject_kind": "team",
                        "subject_id": USER_ID.value,
                    },
                )
    finally:
        await engine.dispose()
```

Each bad row goes in its own transaction: PostgreSQL aborts a transaction after a constraint violation, so a shared one would fail the next statement for the wrong reason.

**Check:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration tests/test_access_integration.py -vv
```

Expected: 1 passed. Run it twice to confirm it repeats. Do not paste the URL into the evidence file or commit it.

### Step 8 — No coding in this step: run the gates and record the evidence

**Purpose:** Confirm the lesson works as a whole before M02-T09 builds on it.

**Decision:** Run the focused tests first, then every repository gate. The normal gate skips the opt-in PostgreSQL tests, so run those separately and record both results.

**Action:**

```bash
UV_CACHE_DIR=/tmp/uv-cache-rag-project uv run pytest tests/test_access.py tests/test_indentifiers.py -vv
KNOWLEDGE_SERVICE_TEST_DATABASE_URL='<disposable PostgreSQL URL>' \
  UV_CACHE_DIR=/tmp/uv-cache-rag-project \
  uv run pytest -m integration -vv
UV_CACHE_DIR=/tmp/uv-cache-rag-project make check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make docs-check
UV_CACHE_DIR=/tmp/uv-cache-rag-project make hooks
git diff --check
```

In `docs/evidence/M02-T08-access-grants.md`, replace the placeholders with the date, your invariant, the files you changed, the exact observed results, the PostgreSQL version, and the answers below. Add no credential, connection URL, or private content.

**Reflection — answer in your own words:**

1. A caller hands `replace_access_grants` a Document id and a set of grants. Name what that function and the `Subject` value check on its behalf, and say which check a caller writing its own SQL would most likely forget.
2. We could have let one grant row name both a user and a group, with the "any of these may read" rule resolved in application code. Describe what gets harder later if we did that — think about a query that must decide, inside SQL, whether one user may read one Document.
3. The integration test asserts that a `user` grant with no `subject_id` is rejected. If we removed that assertion, what production failure might go unnoticed until someone saw a Document they should not?

**Check:** The unit tests and every integration test pass; all repository gates pass; the evidence records the real results and no connection secret.

## Completion checklist

- [ ] `AccessGrantId` exists in `identifiers.py` and is covered by the identifier tests.
- [ ] `src/knowledge_service/access.py` models a one-subject `Subject` and a `granted_subjects()` set check, and imports no SQLAlchemy.
- [ ] One hand-written revision creates `access_grants` with the kind CHECK, the `subject_id` XOR CHECK, the `NULLS NOT DISTINCT` unique constraint, and a cascading foreign key to `documents`.
- [ ] `replace_access_grants` validates, then deletes and inserts on the caller's connection; `load_access_grants` reads them back.
- [ ] One SQL query tells public, user, group, and no-access apart, and four malformed rows are rejected.
- [ ] Repository checks pass and the evidence records real results.

Share your implementation or any error you hit and I will review it.
