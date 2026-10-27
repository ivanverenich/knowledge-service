# M04-T08 — Add retrieval metadata filters

Source task: [M04-T08](../curriculum/milestones/M04-dense-retrieval/M04-T08-add-retrieval-metadata-filters.md)

## What you will do

You will let a search be narrowed by Source, content type, language, and how recently the Source changed a Document. A caller picks conditions from a fixed list, such as "language is any of uk" or "modified on or after 1 October". It never writes SQL. Each condition becomes a fixed SQL condition with bound parameters, added to the same `WHERE` as the permission check, so a filter can only narrow what the reader may already see.

| Requirement | How this lesson meets it |
|---|---|
| Source, content type, language, recency | `FilterField.SOURCE`, `MEDIA_TYPE`, `LANGUAGE`, `MODIFIED_AT`. Media type, language, and the Source's modification time are now stored per Document in a new `document_metadata` table. |
| Allowlisted typed expression | `MetadataCondition(field, operator, value)`: each field allows exactly one operator and one value type. Anything else raises `UnsupportedFilter` before the database is used. |
| No arbitrary SQL fragments | `metadata_predicate` in `persistence.py` maps each field to its own SQL condition; every value is a bound parameter. |
| Each filter combined with vector ranking | A table-driven integration test runs every filter, and combinations, through `search_similar_chunks` with `limit=10` and `limit=1`. |
| Reject unsupported operators | Unit tables, plus integration tests through the search: a second condition on one field, a raw SQL string, and an operator altered after the condition was made. |
| Authorization preserved | A hidden Document that matches every filter and is the nearest match is never returned and never sets `truncated`. |

This lesson includes coding: Steps 2–8 change code or docs, while Steps 1 and 9 do not.

Terms used in this lesson:

- **Metadata:** facts about a Document that are not its text: its media type, language, and when its Source last changed it.
- **Media type:** the kind of file, written as `type/subtype`, such as `text/markdown`.
- **Allowlist:** a fixed list of what is allowed. Anything not on it is refused, instead of trying to list what is forbidden.
- **Migration:** a versioned script that changes the database schema. Alembic applies them in order.
- **Stale observation:** a reading of a Source made earlier but processed later than a newer one.

## Before you begin

- **The values exist but were never stored.** `normalize` in `normalization.py` already works out `media_type`, `language` (a `LanguageProfile`), and `modified_at` on `NormalizedDocument`. Nothing saved them, and the `documents` table has no such columns.
- **Why a separate table.** Adding columns to `documents` would mean new fields on `Document`, which tests build directly in several places, and these values have nothing to do with its versions or fingerprints. A `document_metadata` table with one row per Document keeps `Document` as it is. Its primary key is `document_id`, so the "every foreign key leads an index" test in `tests/test_indexes.py` still passes, and no new index is needed.
- **Who writes it.** `reconcile_document` writes the row on every run, but only when it differs, so an unchanged run still writes nothing. A Document with no row (reconciled before this lesson, or published through `publish_document`) gets one on its next reconcile. Until then it matches no media type, language, or recency condition. A Source condition still matches it, because `source_id` lives on `documents`.
- **Recency means the Source's time.** `MODIFIED_AT` compares the time the Source reports (`SourceMetadata.modified_at`), not when the service saw the Document. A Source that reports no time gives `NULL`, and `NULL >= since` is never true, so such a Document never matches a recency filter.
- **What the adversarial review found.** An independent review attacked the first version against the real database, including a fuzz test comparing every result with a plain-Python model of the rules. It confirmed four breaks, all fixed below with regression tests that fail against the old code:
  - A late, stale observation could roll the metadata back, because a metadata-only change did not move `documents.updated_at`. The row now stores `observed_at`, an older observation raises `InvalidDocument`, and a metadata-only change is reported as the new `Change.METADATA` instead of `UNCHANGED`.
  - A `SourceId` altered after it was made (`object.__setattr__` gets around `frozen=True`) to hold text reached PostgreSQL and failed with a raw `DataError`. Source values must now hold a `UUID`.
  - A NUL character in a media type reached PostgreSQL and failed there. Media types must now match the characters RFC 6838 allows.
  - A time zone object could report UTC while being checked and something else afterwards, which moved the boundary, or could raise an untyped error. The condition now keeps a plain UTC copy, and a time zone that cannot answer raises `UnsupportedFilter`.

  A self-check also found that a condition altered after it was made could skip its checks, so `checked_conditions` rebuilds and re-checks every condition.

  The review also confirmed what held. Thirty kinds of tampered or mistyped input were refused before the database. Across 300 random filter and permission combinations, the fuzz matched the oracle exactly in order, ranks, and `truncated`. Every value stayed a bound parameter. An equal timestamp is included, and one microsecond later is not. Eight concurrent first-time reconciles left one metadata row. The migration round-trips.
- **Known limit, not fixed here.** A late observation is refused only when it is older than the last *change*. If a newer run found nothing to change, nothing records that it happened, so an even later stale run can still roll the metadata back. The same is true of a Document's title today. Fixing it would mean writing on every unchanged reconcile, which is a change to M03's rules. Record it as a risk.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and the expected failing check

Create `docs/evidence/M04-T08-metadata-filters.md` before editing any code. Start it with this text, then adjust the wording to your own:

```markdown
# M04-T08 — Retrieval metadata filters evidence

## Invariant

A search can be narrowed only by conditions from a fixed list: Source is any
of some Sources, media type is any of some types, language is any of some
languages, or the Source changed the Document on or after a UTC time. Each
condition is turned into a fixed SQL condition with bound parameters and
added to the same WHERE as the permission check, so filters only narrow:
they never show a Chunk the reader may not see, and never change ranks,
counts, or `truncated` through hidden Chunks. Anything outside the list
raises `UnsupportedFilter` before the database is used.

## Expected failing check

Before the change, the new test files fail to import
`knowledge_service.retrieval_filters`, and the migration drift test would
fail once `document_metadata` is declared without its migration.

Predictions once it exists:

- Five Documents across two Sources, two media types, two languages, and
  different modification times: each filter returns exactly the matching
  ones in distance order, and `limit=1` returns the first with `truncated`
  True when more match.
- A Document whose Source gives no modification time: never matches a
  recency filter.
- A hidden Document that matches every filter and is the nearest: not
  returned, and `truncated` stays False.
- `LANGUAGE` with `ON_OR_AFTER`, the string `"like"` as an operator, or two
  `LANGUAGE` conditions: `UnsupportedFilter`.
- A newer reconcile changes only the modification time: `Change.METADATA`;
  an older observation delivered after it: `InvalidDocument`.

## Design

## Verification

## Reflection
```

Check: the file exists, and the predictions say what you expect before you run anything.

### Step 2 — Coding step: describe a Document's metadata, and add its table

**In plain words:** This step gives the service a place to remember each Document's file type, language, and when its Source last changed it. Ingestion already works these out; until now they were thrown away. After this step there is a value that holds them for one Document and an empty table ready to store them.

| Change | File | What it does |
|---|---|---|
| `DocumentMetadata` | `src/knowledge_service/documents.py` | Holds `document_id`, `media_type`, `language`, `modified_at` (may be None), and `observed_at`; refuses an empty or over-long media type, a non-`Language` language, and non-UTC times. |
| New migration | `migrations/versions/f4a5b6c7d8e9_document_metadata.py` | Creates `document_metadata`, with `document_id` as primary key and foreign key (`ON DELETE CASCADE`), plus check constraints matching the domain rules. |

`observed_at` is when the observation that last *changed* these values was made. Step 5 uses it to refuse a late, older observation.

In `src/knowledge_service/documents.py`, add the import and the constant at the top:

```python
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.language import Language

MAX_TITLE_CHARACTERS = 200
MAX_MEDIA_TYPE_CHARACTERS = 255
```

and add this class after `DocumentProvenance`:

```python
@dataclass(frozen=True, slots=True)
class DocumentMetadata:
    """What retrieval may filter a Document by, as of its last reconcile.

    `modified_at` is when the Source last changed the item, or None when the
    Source does not say. `observed_at` is when the observation that last
    changed these values was made; an older observation must not replace them.
    """

    document_id: DocumentId
    media_type: str
    language: Language
    modified_at: datetime | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if not self.media_type.strip():
            raise InvalidDocument("media type must not be empty")
        if len(self.media_type) > MAX_MEDIA_TYPE_CHARACTERS:
            raise InvalidDocument(
                f"media type must be at most {MAX_MEDIA_TYPE_CHARACTERS} characters"
            )
        if type(self.language) is not Language:
            raise InvalidDocument("language must be a Language")
        if self.modified_at is not None:
            _require_utc(self.modified_at, "modified_at")
        _require_utc(self.observed_at, "observed_at")
```

Create `migrations/versions/f4a5b6c7d8e9_document_metadata.py`:

```python
"""document metadata

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-10-26 15:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f4a5b6c7d8e9"
down_revision: str | Sequence[str] | None = "e3f4a5b6c7d8"


def upgrade() -> None:
    """Store what retrieval may filter each Document by.

    Existing Documents get their row on their next reconcile; until then a
    metadata filter does not match them.
    """
    op.create_table(
        "document_metadata",
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "media_type <> '' and length(media_type) <= 255",
            name="ck_document_metadata_media_type",
        ),
        sa.CheckConstraint(
            "language in ('en', 'uk', 'mixed', 'unknown')",
            name="ck_document_metadata_language",
        ),
    )


def downgrade() -> None:
    """Drop the stored Document metadata."""
    op.drop_table("document_metadata")
```

Check: `uv run pyright src/knowledge_service/documents.py` reports 0 errors, and `uv run alembic heads` shows `f4a5b6c7d8e9 (head)`.

### Step 3 — Coding step: the filter allowlist

**In plain words:** This file is the list of filters a caller may ask for, and the gatekeeper that refuses everything else. A caller says which field, how to compare, and the values, and gets a checked condition back or a clear error. Nothing here touches the database; the next step turns these conditions into SQL.

| Part | What it does |
|---|---|
| `FilterField`, `FilterOperator` | The only fields and operators that exist. |
| `_ALLOWED_OPERATOR` | One operator per field: `ANY_OF` for Source, media type, and language; `ON_OR_AFTER` for the modification time. |
| `MetadataCondition` | Checks, on creation, that the field and operator are the exact enum types, are an allowed pair, and that the value has the right type. |
| `_check_values` | A non-empty `frozenset` of at most 100 values of the field's type. A `SourceId` must hold a `UUID`; a media type must match RFC 6838 characters. |
| `_utc_moment` | A UTC `datetime` only. It keeps a plain UTC copy, never the caller's time zone object. |
| `checked_conditions` | Takes a tuple or list, rebuilds every condition so it is checked again, and refuses two conditions on one field. |

Why `type(x) is not ...` instead of `isinstance`: a `StrEnum` member equals its string, and a subclass passes `isinstance`, so only the exact type is safe here. Why rebuild in `checked_conditions`: a frozen dataclass can still be changed with `object.__setattr__`, so a condition checked when it was made is checked again just before use.

Create `src/knowledge_service/retrieval_filters.py`:

```python
"""Typed conditions that narrow retrieval to some Documents.

A caller never writes SQL. It names a field and an operator from a fixed
list, with a value of the type that field takes, and the persistence layer
turns each pair into a fixed SQL condition with bound parameters:

| Field | Operator | Value |
|---|---|---|
| `SOURCE` | `ANY_OF` | a non-empty frozenset of `SourceId` |
| `MEDIA_TYPE` | `ANY_OF` | a non-empty frozenset of media types (`text/markdown`) |
| `LANGUAGE` | `ANY_OF` | a non-empty frozenset of `Language` |
| `MODIFIED_AT` | `ON_OR_AFTER` | a UTC `datetime` |

Every other pair, value type, or a second condition on the same field raises
`UnsupportedFilter`. Conditions only narrow: they are added to the
eligibility check, never in place of it, so a filter cannot show a Document
the reader may not see.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import cast
from uuid import UUID

from knowledge_service.documents import MAX_MEDIA_TYPE_CHARACTERS
from knowledge_service.identifiers import SourceId
from knowledge_service.language import Language

# Bounds the values one condition sends to the database.
MAX_FILTER_VALUES = 100

# A canonical media type: lowercase type/subtype from the characters RFC 6838
# allows in a name, so no space, quote, control character, or parameter.
_MEDIA_TYPE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*")


class UnsupportedFilter(ValueError):
    """A condition is not one of the allowed field, operator, and value types."""


class FilterField(StrEnum):
    """What a condition looks at."""

    SOURCE = "source"
    MEDIA_TYPE = "media_type"
    LANGUAGE = "language"
    MODIFIED_AT = "modified_at"


class FilterOperator(StrEnum):
    """How a condition compares."""

    ANY_OF = "any_of"
    ON_OR_AFTER = "on_or_after"


_ALLOWED_OPERATOR = {
    FilterField.SOURCE: FilterOperator.ANY_OF,
    FilterField.MEDIA_TYPE: FilterOperator.ANY_OF,
    FilterField.LANGUAGE: FilterOperator.ANY_OF,
    FilterField.MODIFIED_AT: FilterOperator.ON_OR_AFTER,
}

type FilterValue = frozenset[SourceId] | frozenset[str] | frozenset[Language] | datetime


@dataclass(frozen=True, slots=True)
class MetadataCondition:
    """One allowed condition, such as "language is any of uk, en"."""

    field: FilterField
    operator: FilterOperator
    value: FilterValue

    def __post_init__(self) -> None:
        # A plain string would compare equal to the enum, so exact types.
        if type(self.field) is not FilterField:
            raise UnsupportedFilter("the field is not a FilterField")
        if type(self.operator) is not FilterOperator:
            raise UnsupportedFilter("the operator is not a FilterOperator")
        if self.operator is not _ALLOWED_OPERATOR[self.field]:
            raise UnsupportedFilter(
                f"{self.field.value} does not support {self.operator.value}"
            )
        if self.field is FilterField.MODIFIED_AT:
            # Keep a plain UTC copy, never the caller's tzinfo object, which
            # could answer differently once it has been checked.
            object.__setattr__(self, "value", _utc_moment(self.value))
        else:
            _check_values(self.field, self.value)


def checked_conditions(
    conditions: Sequence[MetadataCondition],
) -> tuple[MetadataCondition, ...]:
    """Return the conditions checked again, refusing anything but one per field.

    Each condition is rebuilt, so one changed after it was made (a frozen
    dataclass can still be altered through `object.__setattr__`) is checked
    again. Two conditions on one field would have to be combined somehow;
    refusing them keeps the meaning obvious: every condition must hold.
    """
    if type(conditions) is not tuple and type(conditions) is not list:
        raise UnsupportedFilter("conditions must be a tuple or a list")
    given = tuple(cast(Sequence[object], conditions))
    if any(type(condition) is not MetadataCondition for condition in given):
        raise UnsupportedFilter("every condition must be a MetadataCondition")
    checked = tuple(
        MetadataCondition(c.field, c.operator, c.value)
        for c in cast(tuple[MetadataCondition, ...], given)
    )
    fields = [condition.field for condition in checked]
    if len(set(fields)) != len(fields):
        raise UnsupportedFilter("at most one condition per field")
    return checked


_ELEMENT_TYPE: dict[FilterField, type] = {
    FilterField.SOURCE: SourceId,
    FilterField.MEDIA_TYPE: str,
    FilterField.LANGUAGE: Language,
}


def _check_values(field: FilterField, value: object) -> None:
    if type(value) is not frozenset:
        raise UnsupportedFilter(f"{field.value} takes a frozenset of values")
    values = cast(frozenset[object], value)
    if not values:
        raise UnsupportedFilter(f"{field.value} needs at least one value")
    if len(values) > MAX_FILTER_VALUES:
        raise UnsupportedFilter(
            f"{field.value} takes at most {MAX_FILTER_VALUES} values"
        )
    expected = _ELEMENT_TYPE[field]
    if any(type(element) is not expected for element in values):
        raise UnsupportedFilter(
            f"every {field.value} value must be a {expected.__name__}"
        )
    if field is FilterField.SOURCE and any(
        # A SourceId can be altered after it was made, like any frozen value.
        type(cast(SourceId, element).value) is not UUID
        for element in values
    ):
        raise UnsupportedFilter("every source value must hold a UUID")
    if field is FilterField.MEDIA_TYPE and not all(
        _is_media_type(cast(str, element)) for element in values
    ):
        raise UnsupportedFilter("a media type is lowercase type/subtype")


def _is_media_type(value: str) -> bool:
    return (
        len(value) <= MAX_MEDIA_TYPE_CHARACTERS
        and _MEDIA_TYPE.fullmatch(value) is not None
    )


def _utc_moment(value: object) -> datetime:
    if type(value) is not datetime:
        raise UnsupportedFilter("modified_at takes a datetime")
    try:
        offset = value.utcoffset()
    except Exception:  # a tzinfo is caller code and may raise anything
        raise UnsupportedFilter("modified_at has an unusable time zone") from None
    if offset != timedelta(0):
        raise UnsupportedFilter("modified_at must be a UTC-aware datetime")
    return value.replace(tzinfo=UTC)
```

Check: `uv run pyright src/knowledge_service/retrieval_filters.py` reports 0 errors.

### Step 4 — Coding step: store the metadata, and turn conditions into SQL

**In plain words:** This step teaches the database layer two things: how to save and read a Document's metadata, and how to turn each allowed filter into a piece of SQL that only narrows the search. Every value travels separately from the SQL text, so nothing a caller sends can change what the SQL means.

| Helper | What it does |
|---|---|
| `document_metadata` table | The same columns and rules as the migration in Step 2. The drift test compares the two. |
| `metadata_predicate(conditions)` | `true` AND one clause per condition. Source compares `documents.source_id`; the others use `EXISTS` on `document_metadata`, so a Document without a row matches none of them. |
| `_any_of` | One array parameter, compared with `= ANY(...)`, the same pattern as the Groups in M04-T06. |
| `upsert_document_metadata` | Inserts the row, or replaces it on conflict. |
| `load_document_metadata` | Reads the row back, or None. |

`assert_never` in the `match` is a type check: if someone adds a fifth `FilterField` and forgets it here, pyright reports an error.

In `src/knowledge_service/persistence.py`, change two imports and add two:

```python
from typing import Any, assert_never, cast
```

```python
from knowledge_service.documents import (
    AuthorizationVersion,
    ContentVersion,
    Document,
    DocumentAvailability,
    DocumentMetadata,
    DocumentProvenance,
    InvalidDocument,
)
```

```python
from knowledge_service.language import Language
from knowledge_service.retrieval_filters import FilterField, MetadataCondition
```

Add the table just before `chunks = sa.Table(`:

```python
# What retrieval may filter a Document by. Kept apart from `documents`, so
# the Document's own versions and fingerprints do not change with it.
document_metadata = sa.Table(
    "document_metadata",
    metadata,
    sa.Column(
        "document_id",
        sa.Uuid(),
        sa.ForeignKey("documents.document_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("media_type", sa.Text(), nullable=False),
    sa.Column("language", sa.String(length=8), nullable=False),
    sa.Column("modified_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "media_type <> '' and length(media_type) <= 255",
        name="ck_document_metadata_media_type",
    ),
    sa.CheckConstraint(
        "language in ('en', 'uk', 'mixed', 'unknown')",
        name="ck_document_metadata_language",
    ),
)
```

Add these functions just before `async def delete_chunks(`:

```python
def metadata_predicate(
    conditions: Sequence[MetadataCondition],
) -> sa.ColumnElement[bool]:
    """The condition a Chunk's Document must meet for every given filter.

    Each allowed field maps to one fixed SQL condition here, and every value
    is a bound parameter. With no conditions it is simply true. The
    statement using it must join `chunks` to `documents`. A Document with no
    metadata row matches no media type, language, or modified-since
    condition; a Source condition still matches it.
    """
    return sa.and_(sa.true(), *(_condition_clause(c) for c in conditions))


def _condition_clause(condition: MetadataCondition) -> sa.ColumnElement[bool]:
    match condition.field:
        case FilterField.SOURCE:
            source_ids = cast(frozenset[SourceId], condition.value)
            return documents.c.source_id == _any_of(
                "source_ids", sorted(s.value for s in source_ids), sa.Uuid()
            )
        case FilterField.MEDIA_TYPE:
            media_types = cast(frozenset[str], condition.value)
            return _described(
                document_metadata.c.media_type
                == _any_of("media_types", sorted(media_types), sa.Text())
            )
        case FilterField.LANGUAGE:
            languages = cast(frozenset[Language], condition.value)
            return _described(
                document_metadata.c.language
                == _any_of(
                    "languages", sorted(lang.value for lang in languages), sa.Text()
                )
            )
        case FilterField.MODIFIED_AT:
            since = cast(datetime, condition.value)
            # NULL >= since is not true, so an unknown time never matches.
            return _described(document_metadata.c.modified_at >= since)
        case _:
            assert_never(condition.field)


def _any_of(
    name: str, values: Sequence[object], item_type: sa.types.TypeEngine[Any]
) -> sa.ColumnElement[Any]:
    # One array parameter, however many values; unique keeps names apart.
    return sa.any_(
        sa.bindparam(name, list(values), type_=ARRAY(item_type), unique=True)
    )


def _described(clause: sa.ColumnElement[bool]) -> sa.ColumnElement[bool]:
    return sa.exists().where(
        document_metadata.c.document_id == documents.c.document_id, clause
    )


async def upsert_document_metadata(
    connection: AsyncConnection, described: DocumentMetadata
) -> None:
    """Store a Document's metadata, replacing what was stored before."""
    values = {
        "document_id": described.document_id.value,
        "media_type": described.media_type,
        "language": described.language.value,
        "modified_at": described.modified_at,
        "observed_at": described.observed_at,
    }
    statement = insert(document_metadata).values(values)
    await connection.execute(
        statement.on_conflict_do_update(
            index_elements=[document_metadata.c.document_id],
            set_={
                "media_type": statement.excluded.media_type,
                "language": statement.excluded.language,
                "modified_at": statement.excluded.modified_at,
                "observed_at": statement.excluded.observed_at,
            },
        )
    )


async def load_document_metadata(
    connection: AsyncConnection, *, document_id: DocumentId
) -> DocumentMetadata | None:
    """Read a Document's stored metadata, or None if it has none yet."""
    record = (
        (
            await connection.execute(
                sa.select(document_metadata).where(
                    document_metadata.c.document_id == document_id.value
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if record is None:
        return None
    try:
        language = Language(cast(str, record["language"]))
    except ValueError:
        raise InvalidDocument("stored language is not a known value") from None
    return DocumentMetadata(
        document_id=document_id,
        media_type=cast(str, record["media_type"]),
        language=language,
        modified_at=cast("datetime | None", record["modified_at"]),
        observed_at=cast(datetime, record["observed_at"]),
    )
```

Check: `uv run pyright` reports 0 errors, and `uv run pytest tests/test_indexes.py -q` passes.

### Step 5 — Coding step: write the metadata on every reconcile

**In plain words:** Each time a Document is synchronized, its metadata is now saved too, but only if something in it changed. If only the metadata changed, the result says so. If an old reading of the Source arrives after a newer one, it is refused instead of putting old values back.

| Change | What it does |
|---|---|
| `Change.METADATA` | Reported when the Document itself is unchanged but its metadata changed. `synchronize_source` counts it like any other `Change`. |
| `_write_metadata` | Builds `DocumentMetadata` from the `NormalizedDocument`, compares it with the stored row (ignoring `observed_at`), does nothing if equal, raises `InvalidDocument` if the observation is older than the stored one, and otherwise upserts. Returns whether it wrote. |
| Two call sites | After a new Document is inserted, and after `stored.reconcile(...)` for an existing one, before the "nothing changed" return. |

Raising rolls back the whole transaction, the same as an older observation of the Document itself (`Document.reconcile` raises `InvalidDocument` too).

In `src/knowledge_service/reconciliation.py`, add this paragraph to the module docstring, after the bullet list:

```python
In every case the Document's metadata (media type, language, and when the
Source last changed it) is stored if it differs from the stored row, so a
Document reconciled before that row existed gets one on its next run. If
only the metadata changed, the change is `METADATA`. An observation older
than the stored metadata that would change it raises `InvalidDocument`
instead of rolling it back.
```

Change the imports:

```python
from dataclasses import dataclass, replace
```

```python
from knowledge_service.documents import (
    Document,
    DocumentAvailability,
    DocumentMetadata,
    InvalidDocument,
)
```

```python
from knowledge_service.persistence import (
    insert_document,
    load_document,
    load_document_metadata,
    record_audit_event,
    replace_access_grants,
    replace_chunks,
    upsert_document,
    upsert_document_metadata,
)
```

Add the new `Change` member:

```python
    SOURCE_VERSION = "source_version"
    TITLE = "title"
    METADATA = "metadata"
```

In the "created" branch, after `_write_grants(...)`:

```python
                await _write_metadata(connection, normalized, created, observed_at)
```

Replace the "nothing changed" return:

```python
        metadata_changed = await _write_metadata(
            connection, normalized, stored, observed_at
        )
        if document is stored:
            return Reconciliation(
                document=stored,
                change=Change.METADATA if metadata_changed else Change.UNCHANGED,
            )
```

Add the helper after `_write_chunks`:

```python
async def _write_metadata(
    connection: AsyncConnection,
    normalized: NormalizedDocument,
    document: Document,
    observed_at: datetime,
) -> bool:
    """Store the observed metadata if it differs; return whether it did.

    Like the Document's own versions, the metadata only moves forward: an
    observation older than the one that last changed it, with different
    values, raises `InvalidDocument`, so a late run cannot roll it back. An
    older observation with the same values changes nothing and passes.
    """
    observed = DocumentMetadata(
        document_id=document.document_id,
        media_type=normalized.media_type,
        language=normalized.language.language,
        modified_at=normalized.modified_at,
        observed_at=observed_at,
    )
    stored = await load_document_metadata(connection, document_id=document.document_id)
    if stored is not None:
        if replace(stored, observed_at=observed_at) == observed:
            return False
        if observed_at < stored.observed_at:
            raise InvalidDocument(
                "observed_at must not be earlier than the stored metadata"
            )
    await upsert_document_metadata(connection, observed)
    return True
```

Check: `uv run pyright` reports 0 errors, and the existing reconcile and sync tests still pass (Step 7 runs them).

### Step 6 — Coding step: let the search take filters

**In plain words:** The search from M04-T07 gets one new, optional input: a list of filters. They are checked before the database is used, and then added next to the permission check, so the search returns only Chunks the reader may see *and* that match. Every promise from M04-T07 (order, ranks, "more exist") now counts only those Chunks.

| Change | What it does |
|---|---|
| `filters` argument | Defaults to `()`, so every existing call still works. |
| `checked_conditions(filters)` | Runs after the limit and query checks and before the space lookup, so a bad filter never reaches the database. |
| `restriction` | A second predicate in `vector_search_statement`, next to `eligibility`. Keeping them apart makes the difference clear: one is permission, the other is the caller's choice. |

Replace `src/knowledge_service/vector_search.py` with:

```python
"""Find the Chunks nearest to a query vector that one reader may see.

Eligibility is part of the same SQL statement as the ranking: the database
drops every Chunk whose Document is unavailable or not granted to the reader
before it orders the rest by distance. An ineligible Chunk is therefore never
ranked, never counted toward the limit, and never leaves the database.

What a caller can rely on in a `VectorSearchResult`:

- **Limit.** `limit` is top-k: an int from 1 to `MAX_CANDIDATES`. Anything
  else raises `InvalidCandidateLimit` before the database is used.
- **Distance.** Cosine distance, between 0 and 2; lower is nearer. 0 is the
  same direction as the query, 1 is unrelated, 2 is the opposite one.
- **Order.** Nearest first, then by Chunk ID when distances are equal, so
  the same data and query always give the same order. `rank` counts from 1.
- **Fewer than k.** When fewer eligible Chunks exist, all of them are
  returned and `truncated` is False. `truncated` is True only when more
  eligible Chunks exist beyond the limit.
- **Filters.** Optional `filters` (see `retrieval_filters`) narrow the
  eligible Chunks further, in the same WHERE, so every guarantee above,
  `truncated` included, counts only Chunks that are eligible and match.
- **Empty.** No eligible Chunk is a normal, empty result, not an error.
  A Chunk whose stored vector has no direction (all zeros) has no distance,
  so it is never a Candidate.

Every stored vector and every query has length 1, so distances are right
in pgvector's 32-bit floats. Ties are exact: two vectors in the same
direction can still differ in the last digits of their distances, and are
then ordered by those digits, not by Chunk ID.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.documents import ContentVersion
from knowledge_service.embeddings import EmbeddingModelIdentity, Vector, unit_length
from knowledge_service.identifiers import (
    ChunkId,
    DocumentId,
    EmbeddingSpaceId,
    SourceId,
)
from knowledge_service.persistence import (
    chunk_embeddings,
    chunks,
    documents,
    eligible_chunks_predicate,
    load_current_embedding_space_id,
    metadata_predicate,
)
from knowledge_service.retrieval_filters import MetadataCondition, checked_conditions

# The largest top-k one search may ask for, so its rows stay bounded.
MAX_CANDIDATES = 100


class VectorSearchError(Exception):
    """Base error for a vector search."""


class InvalidVectorQuery(VectorSearchError):
    """The query vector or the limit cannot be searched; nothing was read."""


class InvalidCandidateLimit(InvalidVectorQuery):
    """The limit is not an int from 1 to `MAX_CANDIDATES`; nothing was read."""


class NoCurrentEmbeddingSpace(VectorSearchError):
    """The current Embedding Space does not hold the query's model identity."""


class DistanceMetric(StrEnum):
    """How `distance` is measured; lower is always nearer."""

    COSINE = "cosine"


@dataclass(frozen=True, slots=True)
class VectorCandidate:
    """One eligible Chunk, where it came from, and its distance to the query.

    `rank` is its 1-based position in the result. `distance` is the cosine
    distance, from 0 for the same direction to 2 for the opposite one. The
    fields holding Document text are kept out of repr, so logging a
    Candidate never shows them.
    """

    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    distance: float
    external_id: str = field(repr=False)
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_anchor: str | None = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class VectorSearchResult:
    """The Candidates of one search and how to read them.

    See the module docstring for the guarantees on order, distance, and
    fewer-than-k results.
    """

    metric: DistanceMetric
    limit: int
    candidates: tuple[VectorCandidate, ...]
    truncated: bool


async def search_similar_chunks(
    connection: AsyncConnection,
    *,
    identity: EmbeddingModelIdentity,
    query: Vector,
    context: AuthorizationContext,
    limit: int,
    filters: Sequence[MetadataCondition] = (),
) -> VectorSearchResult:
    """Return up to `limit` eligible Chunks, nearest first.

    `identity` names the model that embedded the query; only vectors of the
    current Embedding Space are compared, and it must hold that identity.
    `filters` only narrow the result. Raises `InvalidCandidateLimit` for a
    bad limit, `InvalidVectorQuery` for a bad vector, `UnsupportedFilter`
    for a filter outside the allowlist, and `NoCurrentEmbeddingSpace` when
    the spaces differ.
    """
    _check_limit(limit)
    unit_query = _unit_query(query, dimensions=identity.dimensions)
    conditions = checked_conditions(filters)
    space_id = await load_current_embedding_space_id(connection, identity)
    if space_id is None:
        raise NoCurrentEmbeddingSpace("the current embedding space is another model")
    statement = vector_search_statement(
        space_id=space_id,
        query=unit_query,
        eligibility=eligible_chunks_predicate(context),
        restriction=metadata_predicate(conditions),
        # One row more than asked shows whether more eligible Chunks exist.
        limit=limit + 1,
    )
    records = (await connection.execute(statement)).mappings().all()
    return VectorSearchResult(
        metric=DistanceMetric.COSINE,
        limit=limit,
        candidates=tuple(
            _candidate_from(dict(record), rank=index)
            for index, record in enumerate(records[:limit], start=1)
        ),
        truncated=len(records) > limit,
    )


def vector_search_statement(
    *,
    space_id: EmbeddingSpaceId,
    query: Vector,
    eligibility: sa.ColumnElement[bool],
    restriction: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    """Select the nearest Chunks of one space that meet both predicates.

    `eligibility` decides who may see a Chunk; `restriction` only narrows
    what the caller asked for. Both sit in the same WHERE as the space, so
    PostgreSQL applies them before ORDER BY and LIMIT. Ties in distance are
    broken by Chunk ID so that equal inputs always return the same order. A
    stored vector with no direction gives a NaN distance, which is not a
    distance, so its row is left out; PostgreSQL treats NaN as equal to NaN.
    """
    distance = chunk_embeddings.c.embedding.cosine_distance(list(query))
    return (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            documents.c.source_id,
            chunks.c.content_version,
            chunks.c.ordinal,
            distance.label("distance"),
            documents.c.external_id,
            documents.c.title,
            chunks.c.heading_path,
            chunks.c.source_anchor,
            chunks.c.text,
        )
        .select_from(chunk_embeddings)
        .join(chunks, chunks.c.chunk_id == chunk_embeddings.c.chunk_id)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(
            chunk_embeddings.c.space_id == space_id.value,
            eligibility,
            restriction,
            distance != float("nan"),
        )
        .order_by(distance, chunks.c.chunk_id)
        .limit(limit)
    )


def _check_limit(limit: int) -> None:
    # bool is an int, so `True` would otherwise pass as a limit of 1.
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise InvalidCandidateLimit(
            f"limit must be an int between 1 and {MAX_CANDIDATES}"
        )


def _unit_query(
    query: Vector,
    *,
    dimensions: int,
) -> Vector:
    """Check the query and return it scaled to length 1, like stored vectors."""
    if type(query) is not tuple:
        raise InvalidVectorQuery("query must be a tuple of numbers")
    if len(query) != dimensions:
        raise InvalidVectorQuery(
            f"query has {len(query)} dimensions, expected {dimensions}"
        )
    values = [_finite(value) for value in query]
    # Cosine distance to a zero vector is undefined, so every row would tie.
    if not any(values):
        raise InvalidVectorQuery("query must not be all zeros")
    return unit_length(values)


def _finite(value: object) -> float:
    # bool is an int, and a string may look numeric; neither is a number here.
    if type(value) is not float and type(value) is not int:
        raise InvalidVectorQuery("query must hold numbers")
    try:
        number = float(value)
    except OverflowError:
        raise InvalidVectorQuery("query must hold finite numbers") from None
    if not math.isfinite(number):
        raise InvalidVectorQuery("query must hold finite numbers")
    return number


def _candidate_from(record: Mapping[str, object], *, rank: int) -> VectorCandidate:
    return VectorCandidate(
        rank=rank,
        chunk_id=ChunkId(cast(UUID, record["chunk_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        source_id=SourceId(cast(UUID, record["source_id"])),
        content_version=ContentVersion(cast(int, record["content_version"])),
        ordinal=cast(int, record["ordinal"]),
        distance=cast(float, record["distance"]),
        external_id=cast(str, record["external_id"]),
        title=cast(str, record["title"]),
        heading_path=cast("str | None", record["heading_path"]),
        source_anchor=cast("str | None", record["source_anchor"]),
        text=cast(str, record["text"]),
    )
```

Check: `uv run ruff check src && uv run pyright` reports no errors. `tests/test_vector_search.py` fails until Step 7, because `vector_search_statement` now needs `restriction`.

### Step 7 — Coding step: tests

**In plain words:** These tests show that each filter returns exactly the right Documents in the right order, that filters can never reveal a Document the reader may not see, that the metadata is saved and kept up to date, and that every request outside the list is refused. Some are the review's attacks, kept so the fixes stay fixed.

**`tests/test_documents.py`**: add `timezone` to the `datetime` import, `from typing import cast`, `MAX_MEDIA_TYPE_CHARACTERS` and `DocumentMetadata` to the `knowledge_service.documents` import, and `from knowledge_service.language import Language`. Then append:

```python
def test_metadata_may_have_no_modification_time() -> None:
    described = DocumentMetadata(
        document_id=DocumentId.parse("00000000-0000-0000-0000-000000000001"),
        media_type="text/plain",
        language=Language.UNKNOWN,
        modified_at=None,
        observed_at=OBSERVED_AT,
    )

    assert described.modified_at is None


@pytest.mark.parametrize(
    ("media_type", "language", "modified_at", "observed_at"),
    [
        ("", Language.ENGLISH, None, OBSERVED_AT),
        ("   ", Language.ENGLISH, None, OBSERVED_AT),
        (
            "text/" + "x" * MAX_MEDIA_TYPE_CHARACTERS,
            Language.ENGLISH,
            None,
            OBSERVED_AT,
        ),
        ("text/plain", "en", None, OBSERVED_AT),
        ("text/plain", Language.ENGLISH, datetime(2026, 10, 1), OBSERVED_AT),
        (
            "text/plain",
            Language.ENGLISH,
            datetime(2026, 10, 1, tzinfo=timezone(timedelta(hours=2))),
            OBSERVED_AT,
        ),
        ("text/plain", Language.ENGLISH, None, datetime(2026, 10, 1)),
    ],
)
def test_metadata_refuses_invalid_values(
    media_type: str,
    language: object,
    modified_at: datetime | None,
    observed_at: datetime,
) -> None:
    with pytest.raises(InvalidDocument):
        DocumentMetadata(
            document_id=DocumentId.parse("00000000-0000-0000-0000-000000000001"),
            media_type=media_type,
            language=cast(Language, language),
            modified_at=modified_at,
            observed_at=observed_at,
        )
```

**`tests/test_retrieval_filters.py`** (new, no database): the allowed pairs; a table of 31 refused inputs, including wrong operators (three of them with a value the field does take, so only the operator check can refuse them), plain strings, wrong value types, bad media types with control characters, and too many values; condition lists; and the review's regressions (an altered operator, an altered `SourceId`, a shifting time zone, a time zone that cannot answer).

```python
"""The allowlist of metadata conditions, checked without a database."""

from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import cast, override
from uuid import UUID

import pytest

from knowledge_service.identifiers import SourceId, UserId
from knowledge_service.language import Language
from knowledge_service.retrieval_filters import (
    MAX_FILTER_VALUES,
    FilterField,
    FilterOperator,
    FilterValue,
    MetadataCondition,
    UnsupportedFilter,
    checked_conditions,
)

SOURCE = SourceId(UUID(int=1))
SINCE = datetime(2026, 10, 1, tzinfo=UTC)

ANY_OF = FilterOperator.ANY_OF
ON_OR_AFTER = FilterOperator.ON_OR_AFTER


@pytest.mark.parametrize(
    ("field", "operator", "value"),
    [
        (FilterField.SOURCE, ANY_OF, frozenset({SOURCE})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/markdown", "text/html"})),
        (FilterField.LANGUAGE, ANY_OF, frozenset({Language.UKRAINIAN})),
        (FilterField.MODIFIED_AT, ON_OR_AFTER, SINCE),
    ],
)
def test_each_field_accepts_its_one_operator(
    field: FilterField, operator: FilterOperator, value: FilterValue
) -> None:
    condition = MetadataCondition(field, operator, value)

    assert condition.value == value


@pytest.mark.parametrize(
    ("field", "operator", "value"),
    [
        # An operator the field does not support, with a value the field
        # takes, so only the operator check can refuse it.
        (FilterField.SOURCE, ON_OR_AFTER, frozenset({SOURCE})),
        (FilterField.LANGUAGE, ON_OR_AFTER, frozenset({Language.ENGLISH})),
        (FilterField.MODIFIED_AT, ANY_OF, SINCE),
        # An operator the field does not support, with the wrong value too.
        (FilterField.LANGUAGE, ON_OR_AFTER, SINCE),
        (FilterField.SOURCE, ON_OR_AFTER, SINCE),
        (FilterField.MODIFIED_AT, ANY_OF, frozenset({SINCE})),
        # An operator or field that is not in the enums at all.
        (FilterField.MEDIA_TYPE, "like", frozenset({"text/%"})),
        (FilterField.LANGUAGE, "any_of", frozenset({Language.ENGLISH})),
        ("language", ANY_OF, frozenset({Language.ENGLISH})),
        ("title", ANY_OF, frozenset({"x"})),
        # A value of the wrong shape or type.
        (FilterField.SOURCE, ANY_OF, frozenset[SourceId]()),
        (FilterField.SOURCE, ANY_OF, [SOURCE]),
        (FilterField.SOURCE, ANY_OF, frozenset({SOURCE.value})),
        (FilterField.SOURCE, ANY_OF, frozenset({UserId(UUID(int=1))})),
        (FilterField.LANGUAGE, ANY_OF, frozenset({"uk"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({1})),
        (FilterField.MODIFIED_AT, ON_OR_AFTER, SINCE.date()),
        (FilterField.MODIFIED_AT, ON_OR_AFTER, "2026-10-01"),
        (FilterField.MODIFIED_AT, ON_OR_AFTER, datetime(2026, 10, 1)),
        (
            FilterField.MODIFIED_AT,
            ON_OR_AFTER,
            datetime(2026, 10, 1, tzinfo=timezone(timedelta(hours=3))),
        ),
        # Media types are stored lowercase, without parameters.
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"Text/Markdown"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/plain; charset=utf-8"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/ plain"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/" + "x" * 300})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"téxt/plain"})),
        # Control characters: the NUL once reached PostgreSQL as a DataError.
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/\x00plain"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/\x01plain"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/plain\x7f"})),
        (FilterField.MEDIA_TYPE, ANY_OF, frozenset({"text/pl'ain"})),
        # Too many values for one condition.
        (
            FilterField.SOURCE,
            ANY_OF,
            frozenset(SourceId(UUID(int=i)) for i in range(MAX_FILTER_VALUES + 1)),
        ),
    ],
)
def test_anything_outside_the_allowlist_is_refused(
    field: object, operator: object, value: object
) -> None:
    with pytest.raises(UnsupportedFilter):
        MetadataCondition(
            cast(FilterField, field),
            cast(FilterOperator, operator),
            cast(FilterValue, value),
        )


def test_conditions_on_different_fields_are_kept_in_order() -> None:
    source = MetadataCondition(FilterField.SOURCE, ANY_OF, frozenset({SOURCE}))
    recent = MetadataCondition(FilterField.MODIFIED_AT, ON_OR_AFTER, SINCE)

    assert checked_conditions([source, recent]) == (source, recent)
    assert checked_conditions(()) == ()


@pytest.mark.parametrize(
    "conditions",
    [
        # Two conditions on one field.
        (
            MetadataCondition(
                FilterField.LANGUAGE, ANY_OF, frozenset({Language.ENGLISH})
            ),
            MetadataCondition(
                FilterField.LANGUAGE, ANY_OF, frozenset({Language.UKRAINIAN})
            ),
        ),
        # Not conditions at all.
        ("language = 'uk'",),
        "language = 'uk'",
        None,
        iter(()),
    ],
)
def test_a_condition_list_must_hold_one_condition_per_field(
    conditions: object,
) -> None:
    with pytest.raises(UnsupportedFilter):
        checked_conditions(cast(tuple[MetadataCondition, ...], conditions))


def test_a_condition_altered_after_it_was_made_is_refused() -> None:
    condition = MetadataCondition(
        FilterField.LANGUAGE, ANY_OF, frozenset({Language.ENGLISH})
    )
    # Frozen dataclasses can still be changed this way.
    object.__setattr__(condition, "operator", "like")

    with pytest.raises(UnsupportedFilter):
        checked_conditions([condition])


def test_a_source_id_altered_to_hold_text_is_refused() -> None:
    source = SourceId(UUID(int=1))
    condition = MetadataCondition(FilterField.SOURCE, ANY_OF, frozenset({source}))
    # Once reached PostgreSQL and failed there with a DataError.
    object.__setattr__(source, "value", "' OR 1=1 --")

    with pytest.raises(UnsupportedFilter):
        checked_conditions([condition])


class ShiftingZone(tzinfo):
    """Says UTC while it is checked, then moves five hours."""

    def __init__(self) -> None:
        self.calls = 0

    @override
    def utcoffset(self, dt: datetime | None) -> timedelta:
        self.calls += 1
        return timedelta(0) if self.calls <= 2 else timedelta(hours=-5)

    @override
    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)


class BrokenZone(tzinfo):
    @override
    def utcoffset(self, dt: datetime | None) -> timedelta:
        raise RuntimeError("no offset")


class WrongTypeZone(tzinfo):
    @override
    def utcoffset(self, dt: datetime | None) -> timedelta:
        return cast(timedelta, 0)


def test_a_moment_keeps_no_caller_time_zone() -> None:
    shifting = datetime(2026, 10, 1, tzinfo=ShiftingZone())

    condition = MetadataCondition(FilterField.MODIFIED_AT, ON_OR_AFTER, shifting)

    # Once kept the zone, so the boundary moved after it was checked.
    assert condition.value == datetime(2026, 10, 1, tzinfo=UTC)
    assert cast(datetime, condition.value).tzinfo is UTC


@pytest.mark.parametrize("zone", [BrokenZone(), WrongTypeZone()])
def test_a_time_zone_that_cannot_answer_is_refused(zone: tzinfo) -> None:
    with pytest.raises(UnsupportedFilter):
        MetadataCondition(
            FilterField.MODIFIED_AT, ON_OR_AFTER, datetime(2026, 10, 1, tzinfo=zone)
        )
```

**`tests/test_retrieval_filters_integration.py`** (new, real database):

| Test | What it proves |
|---|---|
| `test_each_filter_narrows_the_ranked_result` | The table: 13 filter sets over five Documents, each with `limit=10` and `limit=1`. Order, `truncated`, and empty results. |
| `test_a_filter_never_shows_a_document_the_reader_may_not_see` | Highest-risk edge: the hidden nearest Document matches all four filters and still stays hidden. |
| `test_reconciling_stores_and_updates_the_metadata` | The row after creation and after a translation. |
| `test_an_unchanged_reconcile_does_not_rewrite_the_metadata` | The row's `xmin` (PostgreSQL's row version) does not change. |
| `test_a_document_without_metadata_matches_only_source_filters` | A missing row, then the next reconcile fills it. |
| `test_an_unsupported_filter_is_refused_before_the_search` / `test_an_altered_operator_is_refused_before_the_search` | Typed failure through the search itself. |
| `test_a_metadata_only_change_is_reported_and_stored` | Review regression: `Change.METADATA`. |
| `test_a_late_observation_cannot_roll_the_metadata_back` | Review regression: `InvalidDocument`, and the newer value stays. |

The Ukrainian sample carries `# noqa: RUF001`, because ruff warns about Cyrillic letters that look like Latin ones; `tests/test_language.py` does the same.

```python
"""Real PostgreSQL tests for metadata filters combined with vector ranking."""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import DocumentMetadata, InvalidDocument
from knowledge_service.embeddings import EmbeddingModelIdentity
from knowledge_service.identifiers import DocumentId, EmbeddingSpaceId, SourceId, UserId
from knowledge_service.language import Language
from knowledge_service.normalization import NormalizedDocument, normalize
from knowledge_service.persistence import (
    document_metadata,
    embedding_spaces,
    insert_chunk_embeddings,
    load_chunks,
    load_document_metadata,
    load_embedding_space_id,
)
from knowledge_service.reconciliation import Change, reconcile_document
from knowledge_service.retrieval_filters import (
    FilterField,
    FilterOperator,
    MetadataCondition,
    UnsupportedFilter,
)
from knowledge_service.settings import Settings
from knowledge_service.sources import SourceAccess, SourceMetadata, SourceRecord
from knowledge_service.vector_search import VectorSearchResult, search_similar_chunks

HANDBOOK = SourceId.parse("00000000-0000-0000-0000-000000000021")
WIKI = SourceId.parse("00000000-0000-0000-0000-000000000022")
OBSERVED_AT = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)
IDENTITY = EmbeddingModelIdentity(provider="fake", model="fake-embedding", dimensions=3)
READER = UserId.parse("00000000-0000-0000-0000-0000000000a1")
OTHER_USER = UserId.parse("00000000-0000-0000-0000-0000000000a2")
CONTEXT = AuthorizationContext(user_id=READER)
QUERY = (1.0, 0.0, 0.0)
SINCE = datetime(2026, 10, 1, tzinfo=UTC)

ENGLISH = "The leave policy explains how employees request paid time off each year."
UKRAINIAN = (
    "Політика відпусток пояснює, як працівники отримують ґрунтовні дозволи"
    " щороку та їдуть у відпустку, якщо є потреба."  # noqa: RUF001
)

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " :location, 'Handbook', true, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        for source_id in (HANDBOOK, WIKI):
            await connection.execute(
                INSERT_SOURCE,
                {"source_id": source_id.value, "location": f"/srv/{source_id}"},
            )
        await connection.execute(
            sa.insert(embedding_spaces).values(
                space_id=EmbeddingSpaceId.new().value,
                provider=IDENTITY.provider,
                model=IDENTITY.model,
                dimensions=IDENTITY.dimensions,
                is_current=True,
                created_at=OBSERVED_AT,
            )
        )
    try:
        yield runtime
    finally:
        await runtime.dispose()


def a_record(
    name: str,
    *,
    source: SourceId,
    media_type: str,
    text: str,
    modified_at: datetime | None,
    version: str = "v1",
) -> NormalizedDocument:
    content = f"# {name}\n\n{text}" if media_type == "text/markdown" else text
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version=version,
            media_type=media_type,
            content=content.encode(),
            access=SourceAccess(public=True),
            metadata=(
                None
                if modified_at is None
                else SourceMetadata(title=name, modified_at=modified_at, size_bytes=1)
            ),
        ),
        source_id=source,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def a_document(
    runtime: DatabaseRuntime,
    record: NormalizedDocument,
    *,
    vector: Sequence[float],
    readers: Sequence[Subject] = (Subject.public(),),
) -> DocumentId:
    """Reconcile a one-Chunk Document, then give its Chunk the given vector."""
    reconciled = await reconcile_document(
        runtime, record, readers=readers, observed_at=OBSERVED_AT
    )
    document_id = reconciled.document.document_id
    async with runtime.transaction() as connection:
        space_id = await load_embedding_space_id(connection, IDENTITY)
        assert space_id is not None
        (chunk,) = await load_chunks(connection, document_id=document_id)
        await insert_chunk_embeddings(
            connection,
            space_id=space_id,
            dimensions=IDENTITY.dimensions,
            vectors={chunk.chunk_id: vector},
            created_at=OBSERVED_AT,
        )
    return document_id


# Five Documents, nearest to QUERY first. Each differs in Source, media type,
# language, or when the Source last changed it; "undated" has no such time.
CORPUS = {
    "en-old.txt": (HANDBOOK, "text/plain", ENGLISH, datetime(2026, 1, 1, tzinfo=UTC)),
    "undated.txt": (HANDBOOK, "text/plain", ENGLISH, None),
    "uk-new.md": (
        HANDBOOK,
        "text/markdown",
        UKRAINIAN,
        datetime(2026, 10, 20, tzinfo=UTC),
    ),
    "en-new.md": (WIKI, "text/markdown", ENGLISH, datetime(2026, 10, 21, tzinfo=UTC)),
    "uk-old.txt": (WIKI, "text/plain", UKRAINIAN, datetime(2026, 2, 1, tzinfo=UTC)),
}
VECTORS = {
    "en-old.txt": [1.0, 0.0, 0.0],
    "undated.txt": [1.0, 0.5, 0.0],
    "uk-new.md": [1.0, 1.0, 0.0],
    "en-new.md": [0.0, 1.0, 0.0],
    "uk-old.txt": [-1.0, 0.0, 0.0],
}


async def a_corpus(runtime: DatabaseRuntime) -> None:
    for name, (source, media_type, text, modified_at) in CORPUS.items():
        record = a_record(
            name,
            source=source,
            media_type=media_type,
            text=text,
            modified_at=modified_at,
        )
        await a_document(runtime, record, vector=VECTORS[name])


async def search(
    runtime: DatabaseRuntime,
    filters: Sequence[MetadataCondition],
    *,
    limit: int = 10,
    context: AuthorizationContext = CONTEXT,
) -> VectorSearchResult:
    async with runtime.transaction() as connection:
        return await search_similar_chunks(
            connection,
            identity=IDENTITY,
            query=QUERY,
            context=context,
            limit=limit,
            filters=filters,
        )


def names(result: VectorSearchResult) -> list[str]:
    return [candidate.external_id for candidate in result.candidates]


def sources(*source_ids: SourceId) -> MetadataCondition:
    return MetadataCondition(
        FilterField.SOURCE, FilterOperator.ANY_OF, frozenset(source_ids)
    )


def media_types(*values: str) -> MetadataCondition:
    return MetadataCondition(
        FilterField.MEDIA_TYPE, FilterOperator.ANY_OF, frozenset(values)
    )


def languages(*values: Language) -> MetadataCondition:
    return MetadataCondition(
        FilterField.LANGUAGE, FilterOperator.ANY_OF, frozenset(values)
    )


def modified_since(moment: datetime) -> MetadataCondition:
    return MetadataCondition(
        FilterField.MODIFIED_AT, FilterOperator.ON_OR_AFTER, moment
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ((), ["en-old.txt", "undated.txt", "uk-new.md", "en-new.md", "uk-old.txt"]),
        ((sources(HANDBOOK),), ["en-old.txt", "undated.txt", "uk-new.md"]),
        ((sources(WIKI),), ["en-new.md", "uk-old.txt"]),
        (
            (sources(HANDBOOK, WIKI),),
            ["en-old.txt", "undated.txt", "uk-new.md", "en-new.md", "uk-old.txt"],
        ),
        ((media_types("text/markdown"),), ["uk-new.md", "en-new.md"]),
        ((media_types("text/html"),), []),
        ((languages(Language.UKRAINIAN),), ["uk-new.md", "uk-old.txt"]),
        ((languages(Language.ENGLISH),), ["en-old.txt", "undated.txt", "en-new.md"]),
        # A Document whose Source gives no time never matches a recency filter.
        ((modified_since(SINCE),), ["uk-new.md", "en-new.md"]),
        ((modified_since(datetime(2026, 10, 21, tzinfo=UTC)),), ["en-new.md"]),
        ((sources(HANDBOOK), languages(Language.UKRAINIAN)), ["uk-new.md"]),
        (
            (media_types("text/markdown"), modified_since(SINCE)),
            ["uk-new.md", "en-new.md"],
        ),
        (
            (
                sources(WIKI),
                languages(Language.UKRAINIAN),
                media_types("text/markdown"),
            ),
            [],
        ),
    ],
)
async def test_each_filter_narrows_the_ranked_result(
    runtime: DatabaseRuntime,
    filters: tuple[MetadataCondition, ...],
    expected: list[str],
) -> None:
    await a_corpus(runtime)

    everything = await search(runtime, filters)
    nearest = await search(runtime, filters, limit=1)

    assert names(everything) == expected
    assert everything.truncated is False
    assert names(nearest) == expected[:1]
    assert nearest.truncated is (len(expected) > 1)


@pytest.mark.integration
async def test_a_filter_never_shows_a_document_the_reader_may_not_see(
    runtime: DatabaseRuntime,
) -> None:
    # The hidden Document matches every filter and is an exact match.
    await a_document(
        runtime,
        a_record(
            "hidden.md",
            source=WIKI,
            media_type="text/markdown",
            text=UKRAINIAN,
            modified_at=datetime(2026, 10, 25, tzinfo=UTC),
        ),
        vector=[1.0, 0.0, 0.0],
        readers=[Subject.user(OTHER_USER)],
    )
    await a_document(
        runtime,
        a_record(
            "visible.md",
            source=WIKI,
            media_type="text/markdown",
            text=UKRAINIAN,
            modified_at=datetime(2026, 10, 25, tzinfo=UTC),
        ),
        vector=[0.0, 1.0, 0.0],
    )
    every_filter = (
        sources(WIKI),
        media_types("text/markdown"),
        languages(Language.UKRAINIAN),
        modified_since(SINCE),
    )

    result = await search(runtime, every_filter, limit=1)

    assert names(result) == ["visible.md"]
    assert result.truncated is False


@pytest.mark.integration
async def test_reconciling_stores_and_updates_the_metadata(
    runtime: DatabaseRuntime,
) -> None:
    first = a_record(
        "policy.md",
        source=HANDBOOK,
        media_type="text/markdown",
        text=ENGLISH,
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    document_id = await a_document(runtime, first, vector=[1.0, 0.0, 0.0])
    translated = a_record(
        "policy.md",
        source=HANDBOOK,
        media_type="text/markdown",
        text=UKRAINIAN,
        modified_at=datetime(2026, 10, 20, tzinfo=UTC),
        version="v2",
    )

    async with runtime.transaction() as connection:
        stored = await load_document_metadata(connection, document_id=document_id)
        unknown = await load_document_metadata(connection, document_id=DocumentId.new())
    await reconcile_document(
        runtime,
        translated,
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )
    async with runtime.transaction() as connection:
        updated = await load_document_metadata(connection, document_id=document_id)

    assert unknown is None
    assert stored == DocumentMetadata(
        document_id=document_id,
        media_type="text/markdown",
        language=Language.ENGLISH,
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
        observed_at=OBSERVED_AT,
    )
    assert updated == DocumentMetadata(
        document_id=document_id,
        media_type="text/markdown",
        language=Language.UKRAINIAN,
        modified_at=datetime(2026, 10, 20, tzinfo=UTC),
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )


@pytest.mark.integration
async def test_an_unchanged_reconcile_does_not_rewrite_the_metadata(
    runtime: DatabaseRuntime,
) -> None:
    record = a_record(
        "policy.txt",
        source=HANDBOOK,
        media_type="text/plain",
        text=ENGLISH,
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    await a_document(runtime, record, vector=[1.0, 0.0, 0.0])
    row_version = sa.text("SELECT xmin::text FROM document_metadata")

    async with runtime.transaction() as connection:
        before = await connection.scalar(row_version)
    await reconcile_document(
        runtime,
        record,
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )
    async with runtime.transaction() as connection:
        after = await connection.scalar(row_version)

    assert after == before


@pytest.mark.integration
async def test_a_document_without_metadata_matches_only_source_filters(
    runtime: DatabaseRuntime,
) -> None:
    record = a_record(
        "legacy.txt",
        source=HANDBOOK,
        media_type="text/plain",
        text=ENGLISH,
        modified_at=datetime(2026, 10, 20, tzinfo=UTC),
    )
    await a_document(runtime, record, vector=[1.0, 0.0, 0.0])
    # As for a Document reconciled before metadata was stored.
    async with runtime.transaction() as connection:
        await connection.execute(sa.delete(document_metadata))

    assert names(await search(runtime, ())) == ["legacy.txt"]
    assert names(await search(runtime, (sources(HANDBOOK),))) == ["legacy.txt"]
    assert names(await search(runtime, (languages(Language.ENGLISH),))) == []

    # The next reconcile, even an unchanged one, stores it.
    await reconcile_document(
        runtime,
        record,
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )

    assert names(await search(runtime, (languages(Language.ENGLISH),))) == [
        "legacy.txt"
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    "filters",
    [
        # Two conditions on one field.
        (languages(Language.ENGLISH), languages(Language.UKRAINIAN)),
        # A raw SQL fragment instead of a condition.
        ("language = 'uk' OR true",),
    ],
)
async def test_an_unsupported_filter_is_refused_before_the_search(
    runtime: DatabaseRuntime, filters: tuple[object, ...]
) -> None:
    await a_corpus(runtime)

    with pytest.raises(UnsupportedFilter):
        await search(runtime, cast(tuple[MetadataCondition, ...], filters))


@pytest.mark.integration
async def test_an_altered_operator_is_refused_before_the_search(
    runtime: DatabaseRuntime,
) -> None:
    await a_corpus(runtime)
    condition = languages(Language.UKRAINIAN)
    object.__setattr__(condition, "operator", "like")

    with pytest.raises(UnsupportedFilter):
        await search(runtime, (condition,))


@pytest.mark.integration
async def test_a_metadata_only_change_is_reported_and_stored(
    runtime: DatabaseRuntime,
) -> None:
    def observed(modified_at: datetime) -> NormalizedDocument:
        return a_record(
            "policy.txt",
            source=HANDBOOK,
            media_type="text/plain",
            text=ENGLISH,
            modified_at=modified_at,
        )

    document_id = await a_document(
        runtime, observed(datetime(2026, 1, 1, tzinfo=UTC)), vector=[1.0, 0.0, 0.0]
    )

    # Same text, readers, and source version: only the Source's time moved.
    result = await reconcile_document(
        runtime,
        observed(datetime(2026, 10, 20, tzinfo=UTC)),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=1),
    )

    assert result.change is Change.METADATA
    assert names(await search(runtime, (modified_since(SINCE),))) == ["policy.txt"]
    async with runtime.transaction() as connection:
        stored = await load_document_metadata(connection, document_id=document_id)
    assert stored is not None
    assert stored.observed_at == OBSERVED_AT + timedelta(hours=1)


@pytest.mark.integration
async def test_a_late_observation_cannot_roll_the_metadata_back(
    runtime: DatabaseRuntime,
) -> None:
    def observed(modified_at: datetime) -> NormalizedDocument:
        return a_record(
            "policy.txt",
            source=HANDBOOK,
            media_type="text/plain",
            text=ENGLISH,
            modified_at=modified_at,
        )

    older, newer = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 10, 20, tzinfo=UTC)
    document_id = await a_document(runtime, observed(older), vector=[1.0, 0.0, 0.0])
    await reconcile_document(
        runtime,
        observed(newer),
        readers=[Subject.public()],
        observed_at=OBSERVED_AT + timedelta(hours=2),
    )

    # Made at +1h but delivered last, it still shows the older time.
    with pytest.raises(InvalidDocument):
        await reconcile_document(
            runtime,
            observed(older),
            readers=[Subject.public()],
            observed_at=OBSERVED_AT + timedelta(hours=1),
        )

    async with runtime.transaction() as connection:
        stored = await load_document_metadata(connection, document_id=document_id)
    assert stored is not None
    assert stored.modified_at == newer
```

**`tests/test_vector_search.py`**: the bound-parameter test now also passes a `restriction` and checks that the media type and language reach the database only as parameters. Replace the file with:

```python
"""Vector search checks that need no database."""

from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext, InvalidAuthorizationContext
from knowledge_service.documents import ContentVersion
from knowledge_service.embeddings import EmbeddingModelIdentity, Vector
from knowledge_service.identifiers import (
    ChunkId,
    DocumentId,
    EmbeddingSpaceId,
    GroupId,
    SourceId,
    UserId,
)
from knowledge_service.language import Language
from knowledge_service.persistence import eligible_chunks_predicate, metadata_predicate
from knowledge_service.retrieval_filters import (
    FilterField,
    FilterOperator,
    MetadataCondition,
)
from knowledge_service.vector_search import (
    MAX_CANDIDATES,
    DistanceMetric,
    InvalidCandidateLimit,
    InvalidVectorQuery,
    VectorCandidate,
    VectorSearchResult,
    search_similar_chunks,
    vector_search_statement,
)

IDENTITY = EmbeddingModelIdentity(provider="fake", model="fake-embedding", dimensions=3)
USER = UserId.parse("00000000-0000-0000-0000-0000000000a1")
GROUP = GroupId.parse("00000000-0000-0000-0000-0000000000b1")
MARKER = "zebramarker"
# A media type that must reach the database only as a bound parameter.
INJECTED_MEDIA_TYPE = "text/x-zebramarker"


def test_a_context_names_one_user_and_only_groups() -> None:
    context = AuthorizationContext(user_id=USER, group_ids=frozenset({GROUP}))

    assert context.group_ids == {GROUP}
    assert AuthorizationContext(user_id=USER).group_ids == frozenset()


@pytest.mark.parametrize(
    ("user_id", "group_ids"),
    [
        (USER.value, frozenset[GroupId]()),
        (GroupId(USER.value), frozenset[GroupId]()),
        (USER, [GROUP]),
        (USER, frozenset({USER})),
        (USER, frozenset({GROUP.value})),
    ],
)
def test_a_context_refuses_other_types(user_id: object, group_ids: object) -> None:
    with pytest.raises(InvalidAuthorizationContext):
        AuthorizationContext(
            user_id=cast(UserId, user_id),
            group_ids=cast(frozenset[GroupId], group_ids),
        )


@pytest.mark.parametrize(
    ("query", "limit"),
    [
        ((1.0, 0.0), 5),
        ((1.0, 0.0, 0.0, 0.0), 5),
        ((float("nan"), 0.0, 0.0), 5),
        ((float("inf"), 0.0, 0.0), 5),
        ((0.0, 0.0, 0.0), 5),
        ((1.0, 0.0, 0.0), 0),
        ((1.0, 0.0, 0.0), MAX_CANDIDATES + 1),
        ((1.0, 0.0, 0.0), True),
        # Each of these once crashed with a TypeError or an OverflowError.
        ((10**400, 0.0, 0.0), 5),
        (("1", 0.0, 0.0), 5),
        ((None, 0.0, 0.0), 5),
        ((True, 0.0, 0.0), 5),
        ([1.0, 0.0, 0.0], 5),
        (None, 5),
        (iter((1.0, 0.0, 0.0)), 5),
    ],
)
async def test_a_bad_query_is_refused_before_the_database(
    query: object, limit: int
) -> None:
    # Any use of the connection would fail: the check must come first.
    unused = cast(AsyncConnection, object())

    with pytest.raises(InvalidVectorQuery):
        await search_similar_chunks(
            unused,
            identity=IDENTITY,
            query=cast(Vector, query),
            context=AuthorizationContext(user_id=USER),
            limit=limit,
        )


def test_the_reader_and_the_query_are_bound_parameters() -> None:
    statement = vector_search_statement(
        space_id=EmbeddingSpaceId.new(),
        query=(0.25, 0.5, 0.75),
        eligibility=eligible_chunks_predicate(
            AuthorizationContext(user_id=USER, group_ids=frozenset({GROUP}))
        ),
        restriction=metadata_predicate(
            (
                MetadataCondition(
                    FilterField.MEDIA_TYPE,
                    FilterOperator.ANY_OF,
                    frozenset({INJECTED_MEDIA_TYPE}),
                ),
                MetadataCondition(
                    FilterField.LANGUAGE,
                    FilterOperator.ANY_OF,
                    frozenset({Language.UKRAINIAN}),
                ),
            )
        ),
        limit=5,
    )

    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    values = list(compiled.params.values())

    assert str(USER.value) not in sql
    assert str(GROUP.value) not in sql
    assert "0.25" not in sql
    assert INJECTED_MEDIA_TYPE not in sql
    assert "'uk'" not in sql
    assert USER.value in values
    assert [GROUP.value] in values
    assert [INJECTED_MEDIA_TYPE] in values
    assert ["uk"] in values


def test_a_candidate_repr_shows_no_document_text() -> None:
    candidate = VectorCandidate(
        rank=1,
        chunk_id=ChunkId(UUID(int=1)),
        document_id=DocumentId(UUID(int=2)),
        source_id=SourceId(UUID(int=3)),
        content_version=ContentVersion(1),
        ordinal=0,
        distance=0.5,
        external_id=f"{MARKER}.txt",
        title=MARKER,
        heading_path=MARKER,
        source_anchor=MARKER,
        text=MARKER,
    )

    result = VectorSearchResult(
        metric=DistanceMetric.COSINE,
        limit=1,
        candidates=(candidate,),
        truncated=False,
    )

    assert MARKER not in repr(candidate)
    assert MARKER not in repr(result)


@pytest.mark.parametrize(
    "limit",
    [0, -1, MAX_CANDIDATES + 1, 10**30, True, False, 1.0, 5.5, None, "5"],
)
async def test_an_invalid_k_is_refused_before_the_database(limit: object) -> None:
    unused = cast(AsyncConnection, object())

    with pytest.raises(InvalidCandidateLimit):
        await search_similar_chunks(
            unused,
            identity=IDENTITY,
            query=(1.0, 0.0, 0.0),
            context=AuthorizationContext(user_id=USER),
            limit=cast(int, limit),
        )


async def test_a_bad_limit_is_reported_before_a_bad_query() -> None:
    unused = cast(AsyncConnection, object())

    # InvalidCandidateLimit is an InvalidVectorQuery, so T06 callers still
    # catch it; a caller that sets k can tell it apart.
    with pytest.raises(InvalidCandidateLimit):
        await search_similar_chunks(
            unused,
            identity=IDENTITY,
            query=(0.0, 0.0, 0.0),
            context=AuthorizationContext(user_id=USER),
            limit=0,
        )
```

Check: `uv run pytest tests/test_retrieval_filters.py tests/test_documents.py tests/test_vector_search.py -q` passes, then:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py knowledge-service-postgres)" uv run pytest tests/test_retrieval_filters_integration.py tests/test_vector_search_integration.py tests/test_reconciliation_integration.py tests/test_synchronization_integration.py tests/test_migrations_integration.py -q
```

### Step 8 — Coding step: update the architecture doc

**In plain words:** The architecture page lists every table and shows how the search works. This step adds the new table and its rules, and shows filters going into the search.

In `docs/architecture.md`:

1. In the data model diagram, after the `access_grants` line, add:

```text
    documents ||--o| document_metadata : "is described by"
```

2. In the table of rules, add this row before the `chunks` row:

```markdown
| `document_metadata` | At most one row per Document, removed with it; a non-empty media type of at most 255 characters; language is `en`, `uk`, `mixed`, or `unknown`; `modified_at` may be unknown. |
```

3. Replace the whole "Vector search as built in M04" section, up to "### Answering", with:

````markdown
#### Vector search as built in M04

`search_similar_chunks` returns the Chunks nearest to a query vector that
one reader may see. Eligibility is part of the same SQL statement as the
ranking, so a Chunk the reader may not see is never ranked and never leaves
the database. Stored vectors and the query both have length 1, and cosine
distance runs from 0 (same direction) to 2 (opposite). Optional metadata
filters (Source, media type, language, modified since) are typed conditions
from a fixed list; they are added to the same WHERE and only narrow it.

```mermaid
sequenceDiagram
    participant Caller
    participant Search as search_similar_chunks
    participant DB as PostgreSQL
    Caller->>Search: query vector, model identity, Authorization Context, limit, filters
    Search->>Search: refuse a bad query, limit, or filter; scale the query to length 1
    Search->>DB: find the current Embedding Space for the model identity
    alt not current
        Search-->>Caller: NoCurrentEmbeddingSpace
    else current
        Search->>DB: one SELECT: space AND available AND a matching Access Grant AND every filter AND a real distance, ORDER BY distance, Chunk ID, LIMIT k + 1
        DB-->>Search: eligible Chunks with provenance and distance
        Search-->>Caller: VectorSearchResult: up to k ranked Candidates, nearest first, and whether more exist
    end
```
````

Check: `make docs-check` passes.

### Step 9 — No coding in this step: run the gates, then record evidence

Run these in order and keep the outputs:

```bash
uv run pytest tests/test_retrieval_filters.py -q
make test-integration
make format-check
make lint
make typecheck
make check
make docs-check
```

Expected: everything passes. On the reference run, `make check` reported 903 passed and 3 deselected, with `retrieval_filters.py` and `vector_search.py` fully covered.

Then complete the evidence file from Step 1:

- **Design:** the changed files (`documents.py`, the migration, `retrieval_filters.py`, `persistence.py`, `reconciliation.py`, `vector_search.py`, four test files, `architecture.md`) and the allowlist table.
- **Verification:** the commands and their pass counts. Optionally, break one thing at a time (remove `restriction` from the `WHERE`; remove the `_ALLOWED_OPERATOR` check; remove the staleness check in `_write_metadata`) and record which test fails.
- **Risks:** a Document without a metadata row matches only Source filters until its next reconcile; a stale observation older than an unchanged newer one can still roll metadata back, as it can a title; the filters have no index yet (M04-T11 measures plans).
- **Reflection:** your answers to the questions below.

Finally, add the M04-T08 row to `docs/curriculum/PROGRESS.md`, set the next task to M04-T09, and commit with `m04-t08: add retrieval metadata filters`.

## Reflection questions

1. A caller could have been allowed to pass a short piece of SQL, like `language = 'uk'`, instead of a `MetadataCondition`. What could go wrong with that, even if the caller is our own code, and what does the fixed list of fields and operators prevent?
2. The permission check and the filters both sit in the same SQL `WHERE`. If someone later moved the filters into Python, after the database returned the 10 nearest Chunks, what would a user notice when filtering by a rare language?
3. Media type, language, and modification time live in a separate `document_metadata` table, not in `documents`. What did that save us in this lesson, and what does it cost every time we search with a language filter?
4. A Document whose Source gives no modification time never matches "modified on or after 1 October". Is that the right choice for a user who asks for recent documents? What would the opposite choice risk?
5. The review showed that a late, old reading of a Source could put old metadata back. In your own words, why does storing `observed_at` fix the case it fixes, and why does it not fix the case where the newer reading changed nothing?

## Completion checklist

- [ ] The evidence file has the invariant and predictions, written before the code.
- [ ] `DocumentMetadata` and the `document_metadata` migration exist; the migration drift test passes.
- [ ] `retrieval_filters.py` allows exactly the four field and operator pairs and refuses everything else with `UnsupportedFilter`.
- [ ] `reconcile_document` stores metadata only when it changes, reports `Change.METADATA`, and refuses an older observation.
- [ ] `search_similar_chunks(..., filters=...)` adds the filters to the same `WHERE` as the permission check.
- [ ] The filter table test, the hidden-Document test, and the unsupported-filter tests pass.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] `docs/architecture.md` shows the new table and the filters in the search.
- [ ] The evidence file has the design, verification, risks and your reflection.
- [ ] The PROGRESS row is added and the commit is made.

When you have written the code, share it or any error output, and I will review it against this checklist.
