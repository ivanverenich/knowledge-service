# M09-T02 — Implement Source administration

Task: [M09-T02 — Implement Source administration](../curriculum/milestones/M09-product-interfaces/M09-T02-implement-source-administration.md)

## What you will do

This lesson gives operators an API for Sources, the places the service reads documents from. Today a Source exists only when a developer inserts a row into the `sources` table by hand. After this lesson a person with the content-manager role registers, reads, lists, changes, and turns off a Source over HTTP. Every setting is checked, no secret ever passes through, and every change is written to the audit log.

A concrete example. A content manager registers the team handbook folder:

```text
POST /v1/sources
{"kind": "local_directory", "location": "/srv/handbook", "display_name": "Handbook",
 "default_principals": ["group:/engineering"]}
-> 201, Location: /v1/sources/6f1c...
   {"source_id": "6f1c...", "enabled": true, "credentials_reference": null, ...}

POST /v1/sources   (the same folder again)
-> 409 {"code": "source_exists", ...}

POST /v1/sources   ("location": "/srv/handbook/")
-> 422 {"code": "invalid_source_settings",
        "message": "a local_directory location must be an absolute folder path, ..."}
```

The six routes are the ones M09-T01 planned in the route table. This lesson builds them and switches their rows from planned to served.

| What the task asks | Where it is done |
|---|---|
| Create, update, enable, disable, and list Sources | Blocks 1 and 4; Steps 5 and 6 |
| Use secret references, never secrets | Block 3; Step 5 |
| Validate every setting | Blocks 2 and 3; Step 5 |
| Check Application Roles | Block 1; Steps 6 and 7 |
| Record audit events | Block 5; Steps 2 and 5 |
| HTTP tests for role denial, duplicate identity, invalid config, secret redaction, and pagination | Steps 7 and 8 |
| Real infrastructure where its behaviour is under test | Step 8 (PostgreSQL for uniqueness, locks, audit, and paging) |
| Replay or cancellation explicit, external calls bounded | Blocks 2 and 6; Step 11 |
| Explain what would falsify the design | Step 9 and the reflection questions |

Terms this lesson needs:

- **Source:** a place documents come from, such as a local folder or a Confluence site. Its row in `sources` says what kind it is, where it is, and who may read its items.
- **Identity of a Source:** its kind and location together. Two Sources with the same kind and location would read the same documents twice, so the database allows only one.
- **Secret reference:** the name of a secret kept somewhere else, such as `confluence/acme-prod`. The service looks the secret up by that name when it needs it; the API only ever sees the name.
- **Canonical spelling:** the one accepted way to write a value. `/srv/handbook` is canonical; `/srv/handbook/` and `/srv/./handbook` name the same folder but are refused.
- **Audit event:** one row in `audit_events` saying who did what to which thing and when, with no content.
- **Partial update (PATCH):** a request that sends only the settings it changes; every setting it leaves out keeps its value.

## Before you begin

This section explains each decision the lesson's code depends on. Each block shows a real situation in this project, what happens today, the fact underneath it, and the fix. Read it first: the steps rely on it.

### 1. Who may change a Source, and in what order the request is checked

Today a Source is a row a developer inserts. `tests/job_fixtures.py` does it with plain SQL, and so do the evaluation seeders. Nothing asks who the developer is, and nothing records that a Source appeared.

Once there is an HTTP route, anyone who reaches the API can send a request. A Source decides which folder the worker reads and who may read what it finds, so changing one is a privileged action. The role model from M07-T05 already has the operation for it: `configure_sources`, which only the `content-manager` role permits.

Every route in this lesson checks three things in one order, the same order M09-T01 fixed for every route:

| Order | Check | Answer when it fails |
|---|---|---|
| 1 | Who is calling: a valid bearer token | 401 |
| 2 | Whether their role permits `configure_sources` | 403 |
| 3 | What they sent: the path ID, `limit` and `cursor`, or the body | 422, 404, or 409 |

The hidden fact: FastAPI reads and validates a declared body or query parameter before your route function runs. If the route declared `body: SourceRegistration` as a parameter, a caller without the role would get a 422 that describes the input rules, before the role check ever ran. So these routes read the body themselves, after `require`, with the same size limit the Question route uses.

How bad it would be: a refused caller learns the shape of a valid Source and which folders exist as Sources (by trying registrations and reading 409s). That is small today, but every later admin route copies this pattern.

```python
# Was (what FastAPI would do with a declared body): input first
async def register_source(request: Request, body: SourceRegistration): ...

# Became: who, then whether they may, then what they send
caller = await authenticate(request)
require(caller.roles, Operation.CONFIGURE_SOURCES)
registration = cast(SourceRegistration, await read_body(request, SourceRegistration))
```

Think of a building's front desk: it checks your badge before it reads the form you hand over.

### 2. A Source is its kind and location, in one spelling

Two content managers register the handbook folder at the same moment, from two browser tabs.

| Moment | Request A | Request B | `sources` table |
|---|---|---|---|
| 10:00:00.000 | Is `/srv/handbook` registered? No | | empty |
| 10:00:00.001 | | Is `/srv/handbook` registered? No | empty |
| 10:00:00.002 | Insert row A, commit | | A |
| 10:00:00.003 | | Insert row B, commit | A and B |

The hidden fact: a "check, then insert" in application code is two statements, and another transaction can run between them. Neither request saw the other's row, because it was not committed yet when each one looked. Only the database can decide "first one wins", and it does that with a unique constraint.

The table already has one, `uq_sources_kind_location`. This lesson makes the insert lean on it instead of checking first:

```sql
-- Was: check, then insert (a race)
SELECT 1 FROM sources WHERE kind = 'local_directory' AND location = '/srv/handbook';
INSERT INTO sources (...) VALUES (...);

-- Became: one statement; the constraint decides
INSERT INTO sources (...) VALUES (...)
ON CONFLICT ON CONSTRAINT uq_sources_kind_location DO NOTHING
RETURNING source_id;
```

When the statement returns no row, the folder was already taken, and the API answers 409 `source_exists`. The second request waits on the first one's row until it commits, then does nothing.

A unique constraint only compares text, though. `/srv/handbook`, `/srv/handbook/`, and `/srv/./handbook` are three different strings for one folder, and each would become its own Source. The fix is to accept only one spelling of each location and refuse the others. Refusing is safer than rewriting: the operator sees exactly what is stored.

| Kind | Accepted | Refused |
|---|---|---|
| `local_directory` | `/srv/handbook` | `srv/handbook`, `/`, `//srv/handbook`, `/srv/handbook/`, `/srv/./handbook`, `/srv/../handbook`, `/srv//handbook`, `/srv/cafe` + a combining accent |
| `confluence` | `https://acme.atlassian.net/wiki` | `http://...`, `https://Acme...`, `https://acme...:443/...`, `https://acme...:/...`, `https://acme.atlassian.net./wiki`, `https://127.0.0.1/wiki`, a trailing `/`, a user, password, query, fragment, or `%` |

Kind and location never change after registration, so a Source's identity stays fixed. Pointing at another folder means registering another Source.

Think of a library catalogue that refuses a second card for a book it already lists, and insists every card spells the title the same way.

### 3. A secret reference names a secret and never holds it

A Confluence Source will need an API token when M08-T11 builds its client. That token must not be stored in `sources`, shown in an API answer, written to a log, or copied into an audit event.

So the Source stores only a **secret reference**: a name such as `confluence/acme-prod`. When the Confluence client runs, it will look the token up in the secret store by that name. The name rule is narrow on purpose:

| Reference | Result | Why |
|---|---|---|
| `confluence/acme-prod` | accepted | lowercase words joined by `-`, in up to four `/` parts |
| `ATATT3xFfGF0T4kq2` | refused | uppercase: this is what a pasted Atlassian token looks like |
| `ghp_16C7e42F...` | refused | `_` and uppercase: a pasted GitHub token |
| `confluence/acme prod` | refused | a space |
| anything for `local_directory` | refused | a folder needs no credentials |

The second hidden fact is about error messages. Pydantic's own validation errors carry an `input` field holding the value that failed. If an operator pastes a real token into `credentials_reference`, an error that quoted the input would send the token back over the wire and into whatever logs the response. This app never sends Pydantic's errors (its handler answers a generic `validation_error`), and every message `InvalidSourceSettings` carries is a fixed sentence about the rule. That is why the route can safely return that message: it names the broken rule and never the value.

```python
# Was (what quoting would look like): the value travels back
raise InvalidSourceSettings(f"{reference!r} is not a secret name")

# Became: a fixed sentence about the rule
raise InvalidSourceSettings(
    "credentials_reference must name a secret, such as"
    " confluence/acme-prod, never hold the secret itself"
)
```

A Confluence address can carry a secret too: `https://bot:TOKEN@acme.atlassian.net/wiki` or `...?token=...`. The site rule refuses any user, password, query, or fragment.

Think of a coat check: you hand over a numbered ticket, never the coat's contents.

### 4. A change sends only what it changes, and some settings never change

A content manager renames the handbook Source and leaves everything else alone:

```text
PATCH /v1/sources/6f1c...
{"display_name": "Engineering handbook"}
```

Two hidden facts about Pydantic decide how this behaves.

First, a model field with a default cannot tell "absent" from "sent as the default". `SourceChanges.display_name` defaults to `None`, so `{}` and `{"display_name": null}` look the same after parsing. The model remembers which fields the body named in `model_fields_set`. The route passes only those fields on, so a left-out field keeps its value, and an explicit `null` reaches the rules and is refused where it makes no sense.

Second, by default Pydantic ignores unknown fields and converts types it can: `"yes"` becomes `True`, and a misspelled field silently does nothing. Both request models set `extra="forbid"` and `strict=True`, so `{"enabled": false}`, `{"location": "/srv/other"}`, `{"password": "..."}`, or `{"default_public": "yes"}` are refused with 422 before the store is asked.

| Body | Result |
|---|---|
| `{"display_name": "Engineering handbook"}` | renamed; everything else stays |
| `{"default_principals": []}` on a Source that has none | 200, nothing written, no audit event |
| `{"credentials_reference": "confluence/x"}` on a folder | 422: a folder needs no credentials |
| `{"location": "/srv/other"}` | 422: kind and location never change |
| `{}` | 422: a change names at least one setting |

The whole result is checked as a new Source would be, so a change that fits only another kind is refused. A stored Source that already breaks a rule (one inserted by hand before these rules existed) cannot be fixed by a change, because its location never changes; the API answers 409 `source_needs_repair` rather than blaming a field the caller never sent.

Think of a form where you correct one box and initial it; the boxes you did not touch stay as they were.

### 5. Every change and its audit event commit together

Each operation locks the Source's row, writes it, and inserts its audit event, all in one transaction. If the audit insert fails, the change rolls back too, so the log never misses a change that happened.

| Operation | Audit action | Metadata |
|---|---|---|
| register | `source.registered` | `{"kind": "local_directory"}` |
| change | `source.updated` | which settings changed, as `true`: `{"display_name": true}` |
| enable | `source.enabled` | none |
| disable | `source.disabled` | none |

The metadata never holds a value: not the location, the display name, the principals, or the reference. A folder path can name a person or a project; the audit log only needs to say that something changed and who changed it. A change or switch that leaves every value as it was writes nothing and records nothing.

The three new actions are new values for the `ck_audit_events_action` check constraint, so the lesson adds a migration. Its downgrade refuses to run while any of the new events exist, because the audit log is append-only: deleting a recorded change to make a downgrade fit would erase history.

### 6. Disabling waits for the scheduler, but does not stop a running pass

The scheduler from M08-T10 queues synchronization Jobs for enabled Sources. It reads each Source under a share lock (`SELECT ... FOR SHARE`) and queues the Job in the same transaction.

| Moment | Scheduler | Content manager | What the database holds |
|---|---|---|---|
| 10:00:00 | Locks Source S for share, sees `enabled` | | S enabled |
| 10:00:01 | | Disable S: `SELECT ... FOR UPDATE` waits | S enabled, share lock held |
| 10:00:02 | Queues Job J for S, commits | | J queued |
| 10:00:02 | | Lock granted; S becomes disabled, commits | S disabled, J queued |

The hidden fact: a `FOR UPDATE` lock cannot be granted while another transaction holds `FOR SHARE` on the same row. So a Job is always queued either before the change, or not at all. The scheduler never sees a half-made decision.

What this does not do: it does not cancel J. When a worker picks J up, it reads the Source again, finds it disabled, and fails the Job with `source_gone`, which no retry repeats. A pass already running finishes with the settings it read at its start. Cancelling Jobs belongs to synchronization administration (M09-T03).

### Other review findings

An independent review attacked the prototype before this guide was written. Each finding below was fixed, with a test that failed before the fix.

- **One Confluence site in several spellings.** `https://acme.net/wiki`, `https://acme.net:/wiki`, `https://acme.net./wiki`, and `https://acme.net:08443/wiki` beside `:8443` were all stored as separate Sources. Now the host must be a lowercase host name (no IP address, no trailing `.`), and the host and port must read exactly as Python writes them back.
- **One folder name in two Unicode forms.** `/srv/café` written with one `é` and written with `e` plus a combining accent are different strings, but macOS treats them as one folder. Now a location must already be in NFC, the composed form.
- **A change blamed a field the caller never sent.** A Source inserted by hand with `/datasets/x/public/` refused every change with the folder rule. Now the API answers 409 `source_needs_repair`.
- **A full last page of Sources claimed another page.** The paging test used 5 Sources, never an exact multiple of the page size, so `>=` instead of `>` went unnoticed. Now a test reads 4 Sources in pages of 2.
- **A stored principal that names nobody was hidden, then rewritten.** The `default_principals` column allows a NULL element, which synchronization refuses. Reading dropped it, so the Source looked healthy, and the next rename silently saved the shorter list without recording it. Now the NULL is shown as `null`, and a change answers 409 `source_needs_repair`.
- **The refusal property test could pass without a refusal.** Some generated values were valid, so nothing was checked. Now every value carries a line break, which breaks every rule, and the test requires the refusal.

### Known limits

- A symlink, a case-insensitive file system, or a second address of one site still make two Sources of one place: the API cannot see the worker's file system or resolve addresses.
- A secret that happens to fit the name rule, such as one lowercase word, is stored as a name.
- The API does not check that a folder exists: the API process may not see the worker's disk. A missing folder shows up in the Source's next synchronization Job.
- A Confluence Source can be registered, but its synchronization fails with `source_misconfigured` until M08-T11 builds a Confluence client.
- New default readers reach stored Documents at the Source's next pass, not at once.
- Disabling a Source does not cancel a Job already queued or running (Block 6).
- A caller refused by role gets a 403 that no audit event records, as on every other route.

## Step 1 — Read, predict, and see the check fail

No coding in this step.

Read the task file, then this lesson's Block 1 to Block 6. Write down your answers to three predictions before you code:

- Two registrations of `/srv/handbook` run at the same moment. How many rows does `sources` hold afterwards, and what does the second caller get?
- A user with only the `user` role sends `POST /v1/sources` with a body that breaks every rule. Which status comes back?
- `PATCH` with `{"default_principals": []}` on a Source that has no principals. How many audit events does it add?

Then run the test file this lesson creates, to see that it does not exist yet:

```bash
uv run pytest tests/test_source_administration.py -q
```

Expected: `ERROR: file or directory not found: tests/test_source_administration.py`, `no tests ran`, exit code 4.

## Step 2 — Add the audit actions and their migration

Coding step.

**In plain words:** the audit log only accepts actions from a fixed list, both in Python and in the database. This step adds three new actions, a Source changed, turned on, and turned off, to both lists. A migration teaches an existing database the longer list, and refuses to undo itself once such events exist.

| File | Change |
|---|---|
| `src/knowledge_service/audit.py` | three new `AuditAction` values |
| `src/knowledge_service/persistence/schema.py` | the same three in `ck_audit_events_action` |
| `migrations/versions/a3b4c5d6e7f8_source_administration.py` | new migration |

In `src/knowledge_service/audit.py`, add after `JOB_CANCELLED`:

```python
    SOURCE_UPDATED = "source.updated"
    SOURCE_ENABLED = "source.enabled"
    SOURCE_DISABLED = "source.disabled"
```

In `src/knowledge_service/persistence/schema.py`, the action check of `audit_events` ends:

```python
        " 'embedding_migration.switched', 'job.retried', 'job.cancelled',"
        " 'source.updated', 'source.enabled', 'source.disabled')",
```

Create `migrations/versions/a3b4c5d6e7f8_source_administration.py`:

```python
"""source administration

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-11-26 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3b4c5d6e7f8"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"

OLD_ACTIONS = (
    "'source.registered', 'document.availability_changed',"
    " 'conversation.deleted', 'job.queued', 'job.failed',"
    " 'evaluation_run.started', 'question.asked', 'document.read',"
    " 'document.access_changed', 'synchronization.finished',"
    " 'message.rated', 'embedding_migration.started',"
    " 'embedding_migration.switched', 'job.retried', 'job.cancelled'"
)
NEW_ACTIONS = OLD_ACTIONS + ", 'source.updated', 'source.enabled', 'source.disabled'"


def _audit_actions(actions: str) -> None:
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_action", "audit_events", f"action in ({actions})"
    )


def upgrade() -> None:
    _audit_actions(NEW_ACTIONS)


def downgrade() -> None:
    # The audit log is append-only: a recorded change to a Source is never
    # deleted to make a downgrade fit, so the downgrade stops instead.
    op.execute(
        "do $$ begin"
        " if exists (select 1 from audit_events where action in"
        " ('source.updated', 'source.enabled', 'source.disabled')) then"
        " raise exception 'source changes are recorded; cannot downgrade';"
        " end if; end $$"
    )
    _audit_actions(OLD_ACTIONS)
```

Check: `uv run alembic heads` prints `a3b4c5d6e7f8 (head)`.

## Step 3 — Add the request and response models

Coding step.

**In plain words:** these classes describe what a request may send and what an answer shows. A request that names an unknown field, or sends a value of another type, is refused instead of being guessed at. The answer shows every setting of a Source and never a secret, because a Source never holds one.

| Model | Used for |
|---|---|
| `SourceRegistration` | the body of `POST /v1/sources` |
| `SourceChanges` | the body of `PATCH /v1/sources/{source_id}`; every field optional |
| `SourceView` | one Source in an answer |
| `SourcePage` | a page of Sources: `items` and `next_cursor` |

In `src/knowledge_service/contracts.py`, change the imports to:

```python
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from knowledge_service.source_administration import (
    MAX_CREDENTIALS_REFERENCE,
    MAX_DISPLAY_NAME,
    MAX_LOCATION,
    MAX_PRINCIPAL,
    MAX_PRINCIPALS,
)
```

and add at the end of the file:

```python
# A request body that names a field the API does not know, or gives a value
# of another JSON type, is refused, never coerced or ignored.
_STRICT_BODY = ConfigDict(extra="forbid", strict=True)
Principal = Annotated[str, Field(max_length=MAX_PRINCIPAL)]


class SourceRegistration(BaseModel):
    """What registers a Source: what it is, where, and who may read its items.

    `credentials_reference` names a secret in the service's secret store; the
    secret itself never passes through the API. `default_public` and
    `default_principals` say who may read the items of a Source that carries
    no access of its own, such as a local folder; by default, nobody.
    """

    model_config = _STRICT_BODY

    kind: str = Field(max_length=32)
    location: str = Field(max_length=MAX_LOCATION)
    display_name: str = Field(max_length=MAX_DISPLAY_NAME)
    credentials_reference: str | None = Field(
        default=None, max_length=MAX_CREDENTIALS_REFERENCE
    )
    default_public: bool = False
    default_principals: list[Principal] = Field(
        default_factory=list, max_length=MAX_PRINCIPALS
    )


class SourceChanges(BaseModel):
    """The settings a change may set; a field left out keeps its value.

    `kind` and `location` are what a Source is, so they never change: a
    different folder is a different Source.
    """

    model_config = _STRICT_BODY

    display_name: str | None = Field(default=None, max_length=MAX_DISPLAY_NAME)
    credentials_reference: str | None = Field(
        default=None, max_length=MAX_CREDENTIALS_REFERENCE
    )
    default_public: bool | None = None
    default_principals: list[Principal] | None = Field(
        default=None, max_length=MAX_PRINCIPALS
    )


class SourceView(BaseModel):
    """A Source as an operator configures it; never a secret."""

    source_id: UUID
    kind: str
    location: str
    display_name: str
    credentials_reference: str | None
    enabled: bool
    default_public: bool
    # A null names nobody: the Source needs repair before it can synchronize.
    default_principals: list[str | None]
    created_at: datetime
    updated_at: datetime


class SourcePage(BaseModel):
    """A page of Sources, newest first; `next_cursor` is None on the last one."""

    items: list[SourceView]
    next_cursor: str | None
```

The settings are flat fields, `default_public` and `default_principals`, rather than one nested object. A nested model would put a `$defs` section into the request body's OpenAPI schema, which the hand-written request body (Step 6) cannot point to.

The length limits are imported from `source_administration`, the module Step 5 creates, so the request models and the rules can never disagree about a limit. Run this step's check after Step 5.

Check (after Step 5): `uv run pyright src/knowledge_service/contracts.py` reports 0 errors.

## Step 4 — Read and write Source rows

Coding step.

**In plain words:** this code is the only place that speaks SQL about Sources for administration. It stores a new Source and lets the database refuse a second one for the same folder, reads one Source (and can lock it for a change), saves the changeable settings, and reads Sources a page at a time, newest first.

| Function | What it does |
|---|---|
| `SourceRecord` | one stored Source as plain values |
| `insert_source_record` | `INSERT ... ON CONFLICT ON CONSTRAINT uq_sources_kind_location DO NOTHING RETURNING`; False when taken |
| `load_source_record` | one Source; `for_update=True` adds `FOR UPDATE` |
| `save_source_record` | updates the settings an operator may change |
| `load_source_page` | keyset page on `(created_at, source_id)`, newest first |

In `src/knowledge_service/persistence/sources.py`, change the imports to:

```python
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection
```

and add, before `source_runs_statement`:

```python
@dataclass(frozen=True, slots=True)
class SourceRecord:
    """A stored Source as an operator configures it, as plain values."""

    source_id: SourceId
    kind: str
    location: str
    display_name: str
    credentials_reference: str | None
    enabled: bool
    default_public: bool
    # PostgreSQL arrays may hold NULL elements; such a Source needs repair.
    default_principals: tuple[str | None, ...]
    created_at: datetime
    updated_at: datetime


_RECORD_COLUMNS = (
    sources.c.source_id,
    sources.c.kind,
    sources.c.location,
    sources.c.display_name,
    sources.c.credentials_reference,
    sources.c.enabled,
    sources.c.default_public,
    sources.c.default_principals,
    sources.c.created_at,
    sources.c.updated_at,
)


def _source_record(row: sa.RowMapping) -> SourceRecord:
    return SourceRecord(
        source_id=SourceId(cast(UUID, row["source_id"])),
        kind=row["kind"],
        location=row["location"],
        display_name=row["display_name"],
        credentials_reference=row["credentials_reference"],
        enabled=row["enabled"],
        default_public=row["default_public"],
        default_principals=tuple(row["default_principals"]),
        # The session's time zone decides the offset psycopg returns.
        created_at=cast(datetime, row["created_at"]).astimezone(UTC),
        updated_at=cast(datetime, row["updated_at"]).astimezone(UTC),
    )


async def insert_source_record(
    connection: AsyncConnection, record: SourceRecord
) -> bool:
    """Store a new Source; False, storing nothing, when its kind and location exist.

    The unique constraint decides, so two registrations of the same folder
    at the same moment store one Source, never two.
    """
    inserted = await connection.scalar(
        postgresql.insert(sources)
        .values(
            source_id=record.source_id.value,
            kind=record.kind,
            location=record.location,
            display_name=record.display_name,
            credentials_reference=record.credentials_reference,
            enabled=record.enabled,
            default_public=record.default_public,
            default_principals=list(record.default_principals),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        .on_conflict_do_nothing(constraint="uq_sources_kind_location")
        .returning(sources.c.source_id)
    )
    return inserted is not None


async def load_source_record(
    connection: AsyncConnection, *, source_id: SourceId, for_update: bool = False
) -> SourceRecord | None:
    """One stored Source; with `for_update`, locked until the transaction ends."""
    statement = sa.select(*_RECORD_COLUMNS).where(
        sources.c.source_id == source_id.value
    )
    if for_update:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).mappings().one_or_none()
    return None if row is None else _source_record(row)


async def save_source_record(connection: AsyncConnection, record: SourceRecord) -> None:
    """Store what an operator may change of an existing Source."""
    await connection.execute(
        sa.update(sources)
        .where(sources.c.source_id == record.source_id.value)
        .values(
            display_name=record.display_name,
            credentials_reference=record.credentials_reference,
            enabled=record.enabled,
            default_public=record.default_public,
            default_principals=list(record.default_principals),
            updated_at=record.updated_at,
        )
    )


async def load_source_page(
    connection: AsyncConnection,
    *,
    limit: int,
    after: tuple[datetime, SourceId] | None = None,
) -> tuple[SourceRecord, ...]:
    """Up to `limit` Sources, newest first; with `after`, those after that one."""
    statement = sa.select(*_RECORD_COLUMNS)
    if after is not None:
        created_at, source_id = after
        statement = statement.where(
            sa.tuple_(sources.c.created_at, sources.c.source_id)
            < sa.tuple_(sa.literal(created_at), sa.literal(source_id.value))
        )
    statement = statement.order_by(
        sources.c.created_at.desc(), sources.c.source_id.desc()
    ).limit(limit)
    rows = (await connection.execute(statement)).mappings().all()
    return tuple(_source_record(row) for row in rows)
```

The paging order and filter repeat M09-T01's keyset design for the decision list: the next page asks for rows strictly before the last one's `(created_at, source_id)`, so a Source registered between two requests neither repeats nor skips one.

Check: `uv run pyright src/knowledge_service/persistence/sources.py` reports 0 errors.

## Step 5 — Write the Source administration module

Coding step.

**In plain words:** this file holds the rules and the operations. It checks every setting a person sends, refuses a value written in a second spelling or a secret in place of its name, and says which rule broke without repeating the value. Each operation locks the Source, saves it, and writes its audit event in the same transaction; a change that changes nothing writes nothing.

| Part | What it does |
|---|---|
| `SourceDefinition` | every setting, checked in `__post_init__`; principals kept sorted |
| `SourceDefinition.of` | builds one from plain values of any type, such as a request's |
| `_check_folder`, `_check_site` | one spelling of a folder path or a site address (Block 2) |
| `_check_credentials` | a secret's name, never a secret; none for a folder (Block 3) |
| `_checked_access` | `user:<id>` or `group:<id>`, at most 100, no repeats, not with `default_public` |
| `register_source` | insert, `SourceExists` when taken, `source.registered` |
| `change_source` | lock, check the whole result, write only when something changed, `source.updated` |
| `set_source_enabled` | lock, write only when the state changes, `source.enabled` or `source.disabled` |
| `source_cursor`, `source_position` | the Source list's cursor, in one spelling |
| `DatabaseSourceAdministration` | the store the app uses, with a clock |

Create `src/knowledge_service/source_administration.py`:

```python
"""Registering and changing Sources: every setting checked, every change recorded.

A Source is what it reads (`kind`) and where (`location`); those two never
change, and no two Sources share them. An operator may rename a Source,
name another secret for it, change who may read its items, and turn it on
or off. Each change locks the Source, writes it, and records who changed it
in the audit log, all in one transaction.

No secret passes through here. `credentials_reference` is the name of a
secret in the service's secret store, such as `confluence/acme-prod`; the
adapter that reads the Source looks the secret up by that name. An error
names the rule a setting broke, never the value it was given.
"""

import posixpath
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Final, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
    AuditValue,
)
from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import AuditEventId, SourceId, UserId
from knowledge_service.ingestion.sources import SourceKind
from knowledge_service.pagination import InvalidPage, decode_cursor, encode_cursor
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.sources import (
    SourceRecord,
    insert_source_record,
    load_source_page,
    load_source_record,
    save_source_record,
)

MAX_DISPLAY_NAME: Final = 200
MAX_LOCATION: Final = 2048
MAX_CREDENTIALS_REFERENCE: Final = 128
MAX_PRINCIPALS: Final = 100
MAX_PRINCIPAL: Final = 256
MAX_SOURCE_LIST: Final = 500
# What an operator may change; `kind` and `location` are what a Source is.
CHANGEABLE: Final = frozenset(
    {"display_name", "credentials_reference", "default_public", "default_principals"}
)
# A secret's name: lowercase words joined by `-`, in up to four `/` parts.
_SECRET_NAME = re.compile(r"[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*){0,3}")
# A host name: lowercase labels, the last starting with a letter, so an IP
# address, a trailing `.`, or one bare label such as `localhost` is refused.
_HOST = re.compile(r"(?:[a-z0-9]+(?:-+[a-z0-9]+)*\.)+[a-z][a-z0-9]*(?:-+[a-z0-9]+)*")
_SOURCE_LIST = "sources"
_SOURCE_CURSOR_KEYS = frozenset({"created_at", "source_id"})


class InvalidSourceSettings(ValueError):
    """A setting breaks a rule; the message names the rule, never the value."""


class SourceExists(Exception):
    """A Source of the same kind and location is already registered."""


class SourceNotFound(LookupError):
    """No Source has this ID."""


class SourceNeedsRepair(Exception):
    """The stored Source breaks a rule it was not checked against when stored.

    No change can fix it, since its kind and location never change: it is
    registered again instead.
    """


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    """Every setting of a Source, each checked against its rule.

    Each value has one spelling: a folder path or site address written
    another way is refused, not rewritten, so two spellings of one folder
    can never be two Sources. The principals are kept sorted.
    """

    kind: SourceKind
    location: str
    display_name: str
    credentials_reference: str | None
    default_public: bool
    default_principals: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.kind) is not SourceKind:
            raise InvalidSourceSettings(_KIND_RULE)
        _check_display_name(self.display_name)
        if self.kind is SourceKind.LOCAL_DIRECTORY:
            _check_folder(self.location)
        else:
            _check_site(self.location)
        _check_credentials(self.kind, self.credentials_reference)
        principals = _checked_access(self.default_public, self.default_principals)
        object.__setattr__(self, "default_principals", principals)

    @classmethod
    def of(
        cls,
        *,
        kind: object,
        location: object,
        display_name: object,
        credentials_reference: object = None,
        default_public: object = False,
        default_principals: object = (),
    ) -> SourceDefinition:
        """A definition from plain values of any type, such as a request's.

        Each value is checked here, its type included, so the casts below
        only name what the checks in `__post_init__` then prove.
        """
        if type(kind) is not str or kind not in set(SourceKind):
            raise InvalidSourceSettings(_KIND_RULE)
        if type(default_principals) not in (tuple, list):
            raise InvalidSourceSettings("default_principals must be a list")
        return cls(
            kind=SourceKind(kind),
            location=cast(str, location),
            display_name=cast(str, display_name),
            credentials_reference=cast("str | None", credentials_reference),
            default_public=cast(bool, default_public),
            default_principals=tuple(cast("list[str]", default_principals)),
        )


def _check_display_name(name: object) -> None:
    if (
        type(name) is not str
        or not name
        or len(name) > MAX_DISPLAY_NAME
        or name != name.strip()
        or not name.isprintable()
    ):
        raise InvalidSourceSettings(
            f"display_name must be visible text of 1 to {MAX_DISPLAY_NAME}"
            " characters, with no space around it"
        )


def _check_folder(location: object) -> None:
    # `normpath` keeps a leading `//`, which POSIX lets a system read
    # differently from `/`.
    if (
        type(location) is not str
        or len(location) > MAX_LOCATION
        or not location.isprintable()
        # One spelling of each letter: on macOS, `é` and `e` followed by a
        # combining accent name the same folder.
        or unicodedata.normalize("NFC", location) != location
        or not location.startswith("/")
        or location.startswith("//")
        or location == "/"
        or posixpath.normpath(location) != location
    ):
        raise InvalidSourceSettings(
            "a local_directory location must be an absolute folder path, not"
            " the root, with no `.`, `..`, doubled, or trailing `/`"
        )


def _check_site(location: object) -> None:
    problem = InvalidSourceSettings(
        "a confluence location must be an https address with a lowercase host"
        " name, not an IP address, and no user, password, port 443, query,"
        " fragment, `%`, or trailing `/`"
    )
    if (
        type(location) is not str
        or len(location) > MAX_LOCATION
        or not location.isascii()
        or not location.isprintable()
        or any(mark in location for mark in " %?#@\\")
    ):
        raise problem
    try:
        parts = urlsplit(location)
        port = parts.port
    except ValueError:
        raise problem from None
    path = parts.path
    host = parts.hostname or ""
    if (
        parts.scheme != "https"
        or _HOST.fullmatch(host) is None
        # The host and port as written, so `:`, `:08443`, or `:443` is refused.
        or parts.netloc != (host if port is None else f"{host}:{port}")
        or port == 443
        or (path and (path.endswith("/") or posixpath.normpath(path) != path))
        or urlunsplit(parts) != location
    ):
        raise problem


def _check_credentials(kind: SourceKind, reference: object) -> None:
    if kind is SourceKind.LOCAL_DIRECTORY:
        if reference is not None:
            raise InvalidSourceSettings("a local_directory needs no credentials")
        return
    if (
        type(reference) is not str
        or len(reference) > MAX_CREDENTIALS_REFERENCE
        or _SECRET_NAME.fullmatch(reference) is None
    ):
        raise InvalidSourceSettings(
            "credentials_reference must name a secret, such as"
            " confluence/acme-prod, never hold the secret itself"
        )


def _checked_access(public: object, principals: tuple[object, ...]) -> tuple[str, ...]:
    if type(public) is not bool:
        raise InvalidSourceSettings("default_public must be true or false")
    if len(principals) > MAX_PRINCIPALS:
        raise InvalidSourceSettings(
            f"default_principals holds at most {MAX_PRINCIPALS} principals"
        )
    checked: list[str] = []
    for principal in principals:
        if type(principal) is not str or len(principal) > MAX_PRINCIPAL:
            raise InvalidSourceSettings(_PRINCIPAL_RULE)
        kind, separator, name = principal.partition(":")
        if (
            not separator
            or kind not in ("user", "group")
            or not name
            or name != name.strip()
            or not name.isprintable()
        ):
            raise InvalidSourceSettings(_PRINCIPAL_RULE)
        checked.append(principal)
    if len(set(checked)) != len(checked):
        raise InvalidSourceSettings("default_principals must not repeat a principal")
    if public and checked:
        raise InvalidSourceSettings(
            "a public Source must not also list default_principals"
        )
    return tuple(sorted(checked))


_PRINCIPAL_RULE = "each default principal must be user:<id> or group:<id>"
_KIND_RULE = "kind must be one of: " + ", ".join(SourceKind)


def definition_of(record: SourceRecord) -> SourceDefinition:
    """A stored Source's settings, checked as a new one would be."""
    return SourceDefinition.of(
        kind=record.kind,
        location=record.location,
        display_name=record.display_name,
        credentials_reference=record.credentials_reference,
        default_public=record.default_public,
        default_principals=record.default_principals,
    )


def source_cursor(record: SourceRecord) -> str:
    """The cursor of the page that starts after `record` in the Source list."""
    return encode_cursor(
        _SOURCE_LIST,
        {
            "created_at": record.created_at.isoformat(),
            "source_id": str(record.source_id.value),
        },
    )


def source_position(cursor: str) -> tuple[datetime, SourceId]:
    """Where a `source_cursor` says the next page starts; else `InvalidPage`."""
    position = decode_cursor(cursor, listing=_SOURCE_LIST, keys=_SOURCE_CURSOR_KEYS)
    created_text, source_text = position["created_at"], position["source_id"]
    try:
        created_at = datetime.fromisoformat(created_text)
        source_id = UUID(source_text)
    except ValueError:
        raise InvalidPage("the cursor is not one this API gave") from None
    if (
        created_at.utcoffset() != timedelta(0)
        or created_at.isoformat() != created_text
        or str(source_id) != source_text
    ):
        raise InvalidPage("the cursor is not one this API gave")
    return created_at, SourceId(source_id)


async def register_source(
    runtime: DatabaseRuntime,
    *,
    definition: SourceDefinition,
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> SourceRecord:
    """Store a new, enabled Source; `SourceExists` if its kind and location exist."""
    _require_utc(now)
    record = SourceRecord(
        source_id=SourceId.new(),
        kind=definition.kind.value,
        location=definition.location,
        display_name=definition.display_name,
        credentials_reference=definition.credentials_reference,
        enabled=True,
        default_public=definition.default_public,
        default_principals=definition.default_principals,
        created_at=now,
        updated_at=now,
    )
    async with runtime.transaction() as connection:
        if not await insert_source_record(connection, record):
            raise SourceExists("a source of this kind and location is registered")
        await record_audit_event(
            connection,
            _source_event(
                record,
                action=AuditAction.SOURCE_REGISTERED,
                operator=operator,
                at=now,
                request_id=request_id,
                metadata={"kind": record.kind},
            ),
        )
    return record


async def change_source(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    changes: Mapping[str, object],
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> SourceRecord:
    """Set some of a Source's changeable settings; the rest stay.

    The whole result is checked, so a change that fits only another kind,
    such as a secret for a local folder, is refused. A change that sets
    every setting to the value it has writes and records nothing.
    """
    _require_utc(now)
    unknown = set(changes) - CHANGEABLE
    if not changes or unknown:
        raise InvalidSourceSettings(
            "a change sets one or more of display_name, credentials_reference,"
            " default_public, and default_principals"
        )
    async with runtime.transaction() as connection:
        record = await _lock(connection, source_id=source_id)
        try:
            current = definition_of(record)
        except InvalidSourceSettings:
            raise SourceNeedsRepair("the stored source breaks a rule") from None
        wanted = SourceDefinition.of(
            kind=current.kind.value,
            location=current.location,
            display_name=changes.get("display_name", current.display_name),
            credentials_reference=changes.get(
                "credentials_reference", current.credentials_reference
            ),
            default_public=changes.get("default_public", current.default_public),
            default_principals=changes.get(
                "default_principals", current.default_principals
            ),
        )
        changed = {
            name: True
            for name in sorted(CHANGEABLE)
            if getattr(wanted, name) != getattr(current, name)
        }
        if not changed:
            return record
        updated = replace(
            record,
            display_name=wanted.display_name,
            credentials_reference=wanted.credentials_reference,
            default_public=wanted.default_public,
            default_principals=wanted.default_principals,
            updated_at=max(now, record.updated_at),
        )
        await save_source_record(connection, updated)
        await record_audit_event(
            connection,
            _source_event(
                updated,
                action=AuditAction.SOURCE_UPDATED,
                operator=operator,
                at=now,
                request_id=request_id,
                metadata=changed,
            ),
        )
    return updated


async def set_source_enabled(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    enabled: bool,
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> SourceRecord:
    """Turn a Source's synchronization on or off; the same state records nothing.

    The lock waits for a scheduler that is queueing a Job for this Source,
    so that Job is queued before the change, never after it.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        record = await _lock(connection, source_id=source_id)
        if record.enabled is enabled:
            return record
        updated = replace(
            record, enabled=enabled, updated_at=max(now, record.updated_at)
        )
        await save_source_record(connection, updated)
        await record_audit_event(
            connection,
            _source_event(
                updated,
                action=AuditAction.SOURCE_ENABLED
                if enabled
                else AuditAction.SOURCE_DISABLED,
                operator=operator,
                at=now,
                request_id=request_id,
                metadata={},
            ),
        )
    return updated


async def load_source(
    runtime: DatabaseRuntime, *, source_id: SourceId
) -> SourceRecord | None:
    """One Source, or None if no Source has this ID."""
    async with runtime.transaction() as connection:
        return await load_source_record(connection, source_id=source_id)


async def load_sources(
    runtime: DatabaseRuntime,
    *,
    limit: int,
    after: tuple[datetime, SourceId] | None = None,
) -> tuple[SourceRecord, ...]:
    """Up to `limit` Sources, newest first; with `after`, from a `source_position`."""
    if type(limit) is not int or not 1 <= limit <= MAX_SOURCE_LIST:
        raise ValueError(f"limit must be an integer from 1 to {MAX_SOURCE_LIST}")
    async with runtime.transaction() as connection:
        return await load_source_page(connection, limit=limit, after=after)


async def _lock(connection: AsyncConnection, *, source_id: SourceId) -> SourceRecord:
    record = await load_source_record(connection, source_id=source_id, for_update=True)
    if record is None:
        raise SourceNotFound("no source has this ID")
    return record


def _require_utc(now: datetime) -> None:
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")


def _source_event(
    record: SourceRecord,
    *,
    action: AuditAction,
    operator: UserId,
    at: datetime,
    request_id: UUID | None,
    metadata: Mapping[str, AuditValue],
) -> AuditEvent:
    """Who changed which Source, and which settings; never their values."""
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=at,
        actor_id=operator,
        action=action,
        target_kind=AuditTargetKind.SOURCE,
        target_id=record.source_id.value,
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=request_id,
        run_id=None,
        metadata=metadata,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DatabaseSourceAdministration:
    """Source administration stored in PostgreSQL, at the clock's time."""

    runtime: DatabaseRuntime
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def page(
        self, *, limit: int, after: tuple[datetime, SourceId] | None = None
    ) -> tuple[SourceRecord, ...]:
        return await load_sources(self.runtime, limit=limit, after=after)

    async def load(self, *, source_id: SourceId) -> SourceRecord | None:
        return await load_source(self.runtime, source_id=source_id)

    async def register(
        self, *, definition: SourceDefinition, operator: UserId, request_id: UUID
    ) -> SourceRecord:
        return await register_source(
            self.runtime,
            definition=definition,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )

    async def change(
        self,
        *,
        source_id: SourceId,
        changes: Mapping[str, object],
        operator: UserId,
        request_id: UUID,
    ) -> SourceRecord:
        return await change_source(
            self.runtime,
            source_id=source_id,
            changes=changes,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )

    async def set_enabled(
        self, *, source_id: SourceId, enabled: bool, operator: UserId, request_id: UUID
    ) -> SourceRecord:
        return await set_source_enabled(
            self.runtime,
            source_id=source_id,
            enabled=enabled,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )
```

A few lines deserve a closer look. `SourceDefinition.of` takes `object` for every value because a request can send any JSON type; the casts only name the types that `__post_init__` then proves. `_HOST` makes the last label start with a letter, which refuses every IP address and a bare `localhost`. `change_source` checks the stored Source first and raises `SourceNeedsRepair` when even that breaks a rule, so the error never blames the caller's field.

Check: `uv run pyright src/knowledge_service/source_administration.py` reports 0 errors.

## Step 6 — Serve the six routes

Coding step.

**In plain words:** the app gets the six Source routes. Each one checks the caller and their role first, then the ID, page, or body, then asks the store, and turns each kind of refusal into its own answer: 404 for a missing Source, 409 for a taken folder or a stored Source that needs repair, 422 for a broken rule. The body is read by the route itself, after the role, with the same size limit as a Question.

| Route | Answer |
|---|---|
| `GET /v1/sources` | 200 page; 422 bad `limit`, `cursor`, or unknown parameter |
| `POST /v1/sources` | 201 with `Location`; 409 `source_exists`; 413; 422 |
| `GET /v1/sources/{source_id}` | 200; 404 `source_not_found`; 422 bad ID |
| `PATCH /v1/sources/{source_id}` | 200; 404; 409 `source_needs_repair`; 413; 422 |
| `POST /v1/sources/{source_id}/enable` and `/disable` | 200; 404; 422 bad ID |

In `src/knowledge_service/app.py`, change these imports:

```python
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from pydantic import BaseModel, ValidationError
```

add `SourceChanges`, `SourcePage`, `SourceRegistration`, and `SourceView` to the `knowledge_service.contracts` import, and `SourceId` to the identifiers import:

```python
from knowledge_service.identifiers import ConversationId, JobId, SourceId, UserId
```

and make the imports after `knowledge_service.pagination` read:

```python
from knowledge_service.persistence.sources import SourceRecord
from knowledge_service.roles import Operation, PermissionDenied, require
from knowledge_service.source_administration import (
    InvalidSourceSettings,
    SourceDefinition,
    SourceExists,
    SourceNeedsRepair,
    SourceNotFound,
    source_cursor,
    source_position,
)
```

Add the store's protocol and the answer's view after `JobDecisions`:

```python
class SourceAdministration(Protocol):
    """Sources as an operator registers and changes them.

    `DatabaseSourceAdministration` is the implementation. `register` raises
    `SourceExists` for a kind and location already registered; `change` and
    `set_enabled` raise `SourceNotFound` for a missing Source, and `change`
    raises `InvalidSourceSettings` for a result that breaks a rule.
    """

    async def page(
        self, *, limit: int, after: tuple[datetime, SourceId] | None = None
    ) -> tuple[SourceRecord, ...]: ...

    async def load(self, *, source_id: SourceId) -> SourceRecord | None: ...

    async def register(
        self, *, definition: SourceDefinition, operator: UserId, request_id: UUID
    ) -> SourceRecord: ...

    async def change(
        self,
        *,
        source_id: SourceId,
        changes: Mapping[str, object],
        operator: UserId,
        request_id: UUID,
    ) -> SourceRecord: ...

    async def set_enabled(
        self, *, source_id: SourceId, enabled: bool, operator: UserId, request_id: UUID
    ) -> SourceRecord: ...


def source_view(record: SourceRecord) -> SourceView:
    """The API view of a stored Source."""
    return SourceView(
        source_id=record.source_id.value,
        kind=record.kind,
        location=record.location,
        display_name=record.display_name,
        credentials_reference=record.credentials_reference,
        enabled=record.enabled,
        default_public=record.default_public,
        default_principals=list(record.default_principals),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
```

Give `create_app` the store, after `job_decisions`:

```python
    sources: SourceAdministration | None = None,
```

In the Question route, replace the hand-written `openapi_extra` with the helper this step adds:

```python
        openapi_extra=body_schema(Question),
```

Add the six routes at the end of `create_app`, just before `return app`:

```python
    @app.get(
        "/v1/sources",
        response_model=SourcePage,
        responses=error_responses(),
        openapi_extra={"parameters": PAGE_PARAMETERS},
    )
    async def list_sources(request: Request) -> SourcePage | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        request_id = request_id_from(request)
        try:
            limit, cursor = page_request(request)
            after = None if cursor is None else source_position(cursor)
        except InvalidPage:
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            # One more than the page, to learn whether another page follows.
            records = await sources.page(limit=limit + 1, after=after)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("source list failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        page = records[:limit]
        return SourcePage(
            items=[source_view(record) for record in page],
            next_cursor=source_cursor(page[-1]) if len(records) > limit else None,
        )

    @app.post(
        "/v1/sources",
        status_code=201,
        response_model=SourceView,
        responses=error_responses(409, 413),
        openapi_extra=body_schema(SourceRegistration),
    )
    async def register_source(request: Request) -> JSONResponse:
        # Who, then whether they may, then what they send.
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        registration = cast(
            SourceRegistration, await read_body(request, SourceRegistration)
        )
        request_id = request_id_from(request)
        try:
            definition = SourceDefinition.of(**registration.model_dump())
        except InvalidSourceSettings as invalid:
            return _invalid_source(invalid, request_id)
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            record = await sources.register(
                definition=definition,
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except SourceExists:
            return _conflict(
                "source_exists",
                "A Source of this kind and location is registered",
                request_id,
            )
        except Exception as error:
            logger.error("source registration failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        return JSONResponse(
            status_code=201,
            content=source_view(record).model_dump(mode="json"),
            headers={"Location": f"/v1/sources/{record.source_id.value}"},
        )

    @app.get(
        "/v1/sources/{source_id}",
        response_model=SourceView,
        responses=error_responses(404),
    )
    async def read_source(
        request: Request, source_id: Identifier
    ) -> SourceView | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            record = await sources.load(source_id=SourceId.parse(source_id))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("source read failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        if record is None:
            return _source_not_found(request_id)
        return source_view(record)

    @app.patch(
        "/v1/sources/{source_id}",
        response_model=SourceView,
        responses=error_responses(404, 409, 413),
        openapi_extra=body_schema(SourceChanges),
    )
    async def change_source(
        request: Request, source_id: Identifier
    ) -> SourceView | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        requested = cast(SourceChanges, await read_body(request, SourceChanges))
        # Only the fields the body names: one left out keeps its value.
        changes = {
            name: getattr(requested, name) for name in requested.model_fields_set
        }
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            record = await sources.change(
                source_id=SourceId.parse(source_id),
                changes=changes,
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except SourceNotFound:
            return _source_not_found(request_id)
        except SourceNeedsRepair:
            return _conflict(
                "source_needs_repair",
                "The stored Source breaks a rule; register it again",
                request_id,
            )
        except InvalidSourceSettings as invalid:
            return _invalid_source(invalid, request_id)
        except Exception as error:
            logger.error("source change failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        return source_view(record)

    @app.post(
        "/v1/sources/{source_id}/enable",
        response_model=SourceView,
        responses=error_responses(404),
    )
    async def enable_source(
        request: Request, source_id: Identifier
    ) -> SourceView | JSONResponse:
        return await switch(request, source_id, enabled=True)

    @app.post(
        "/v1/sources/{source_id}/disable",
        response_model=SourceView,
        responses=error_responses(404),
    )
    async def disable_source(
        request: Request, source_id: Identifier
    ) -> SourceView | JSONResponse:
        return await switch(request, source_id, enabled=False)

    async def switch(
        request: Request, source_id: str, *, enabled: bool
    ) -> SourceView | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            record = await sources.set_enabled(
                source_id=SourceId.parse(source_id),
                enabled=enabled,
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except SourceNotFound:
            return _source_not_found(request_id)
        except Exception as error:
            logger.error("source switch failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        return source_view(record)
```

Add three answer helpers before `_conflict`:

```python
def _sources_unavailable(request_id: UUID) -> JSONResponse:
    return error_response(
        status_code=503,
        code="sources_unavailable",
        message="Sources are unavailable",
        request_id=request_id,
    )


def _source_not_found(request_id: UUID) -> JSONResponse:
    return error_response(
        status_code=404,
        code="source_not_found",
        message="No such Source",
        request_id=request_id,
    )


def _invalid_source(invalid: InvalidSourceSettings, request_id: UUID) -> JSONResponse:
    # The message names a rule and never a value, so it is safe to send.
    return error_response(
        status_code=422,
        code="invalid_source_settings",
        message=str(invalid),
        request_id=request_id,
    )
```

Finally, replace `read_question` with a general body reader and the OpenAPI helper:

```python
def body_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The OpenAPI request body of a route that reads its body itself."""
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        }
    }


async def read_question(request: Request) -> Question:
    """The body as a Question, read only up to `MAX_BODY_BYTES`."""
    return cast(Question, await read_body(request, Question))


async def read_body(request: Request, model: type[BaseModel]) -> BaseModel:
    """The body as a `model`, read only up to `MAX_BODY_BYTES`."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _BodyTooLarge
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise _BodyTooLarge
    try:
        return model.model_validate_json(bytes(body))
    except ValidationError as invalid:
        raise RequestValidationError(invalid.errors()) from None
```

`read_body` is `read_question` with the model as a parameter, so the Question route keeps exactly its behaviour, and both Source bodies get the 64 KiB limit and the 413 for free. It returns a plain `BaseModel`, so each caller names the model it asked for with `cast`; the value really is that model, because `model_validate_json` built it.

Check: `uv run pyright src/knowledge_service/app.py` reports 0 errors.

## Step 7 — Mark the routes served and update the existing tests

Coding step.

**In plain words:** the route table from M09-T01 still lists the six Source routes as planned, and its tests now fail because the app serves them. This step marks them served, tells the OpenAPI test which extra errors each one documents, and teaches the role test to call them with a body they accept, so it can prove each one admits exactly the content-manager role.

In `src/knowledge_service/api_routes.py`, change `_PLANNED` to `_SERVED` in the six rows whose task is `"M09-T02"`. The first one becomes:

```python
    Route(
        _GET,
        "/v1/sources",
        Operation.CONFIGURE_SOURCES,
        _SERVED,
        "M09-T02",
        "List Sources",
        paginated=True,
    ),
```

In `tests/test_api_routes.py`, add the six routes to `ROUTE_ERRORS`:

```python
    ("GET", "/v1/sources"): set[int](),
    ("POST", "/v1/sources"): {409, 413},
    ("GET", "/v1/sources/{source_id}"): {404},
    ("PATCH", "/v1/sources/{source_id}"): {404, 409, 413},
    ("POST", "/v1/sources/{source_id}/enable"): {404},
    ("POST", "/v1/sources/{source_id}/disable"): {404},
```

In `tests/test_app_authentication.py`, five changes.

Add these two imports beside the other `knowledge_service` imports:

```python
from knowledge_service.persistence.sources import SourceRecord
from knowledge_service.source_administration import SourceExists, SourceNotFound
```

Let `raw_scope` take a method, since a PATCH body must be refused before it is read too:

```python
def raw_scope(
    headers: list[tuple[bytes, bytes]],
    path: str = "/v1/questions",
    method: str = "POST",
) -> dict[str, Any]:
    """An ASGI request scope, for what an HTTP client would never send."""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
```

Replace `WITH_BODY` and add the bodies the Source routes accept:

```python
# The routes that may read a body, to check none is read before the role.
WITH_BODY = [route for route, _ in PROTECTED if route[0] in ("POST", "PATCH")]
# A body each route accepts, so a permitted caller reaches the store.
BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/v1/sources"): {
        "kind": "local_directory",
        "location": "/srv/handbook",
        "display_name": "Handbook",
    },
    ("PATCH", "/v1/sources/{source_id}"): {"display_name": "Handbook"},
}
```

Add a fake Source store and wire it into `full_app`:

```python
class Configuring:
    """A Source store that records each request, and finds no Source."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def page(self, **request: Any) -> tuple[SourceRecord, ...]:
        self.calls.append(request)
        return ()

    async def load(self, **request: Any) -> None:
        self.calls.append(request)

    async def register(self, **request: Any) -> SourceRecord:
        self.calls.append(request)
        raise SourceExists("a source of this kind and location is registered")

    async def change(self, **request: Any) -> SourceRecord:
        self.calls.append(request)
        raise SourceNotFound("no source has this ID")

    async def set_enabled(self, **request: Any) -> SourceRecord:
        self.calls.append(request)
        raise SourceNotFound("no source has this ID")


def full_app(
    source: Source,
    tokens: dict[str, Any] | None = None,
    conversations: Deleting | None = None,
    jobs: Reading | None = None,
    decisions: Deciding | None = None,
    sources: Configuring | None = None,
) -> Any:
    """The app with every endpoint wired, so every route is real."""
    return create_app(
        streamer=streamer(Model(), source=source),
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
        conversations=conversations or Deleting(),
        job_reports=jobs or Reading(),
        job_decisions=decisions or Deciding(),
        sources=sources or Configuring(),
    )
```

In `test_each_protected_route_admits_exactly_the_roles_that_permit_it`, replace everything from `source, conversations = ...` to the end:

```python
    source, conversations = Source(), Deleting()
    jobs, decisions, sources = Reading(), Deciding(), Configuring()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(source, {"token": token}, conversations, jobs, decisions, sources)
    method, path = route
    for name in ("{conversation_id}", "{job_id}", "{source_id}"):
        path = path.replace(name, str(uuid4()))

    async with client(app) as http:
        response = await http.request(
            method,
            path,
            json=BODIES.get(route, {"text": QUESTION.text}),
            headers={"Authorization": "Bearer token"},
        )

    allowed = bool(roles & MATRIX[operation])
    assert (response.status_code == 403) is not allowed
    reached = (
        source.readers
        or conversations.calls
        or jobs.calls
        or decisions.calls
        or sources.calls
    )
    assert bool(reached) is allowed
```

and make the body test take the method too:

```python
@pytest.mark.parametrize("route", WITH_BODY, ids=" ".join)
async def test_the_role_is_checked_before_the_body_is_read(
    route: tuple[str, str],
) -> None:
    read: list[int] = []
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        read.append(1)
        return {"type": "http.request", "body": b" " * 65_536, "more_body": True}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = [(b"authorization", b"Bearer roleless-token")]
    method, path = route
    await full_app(Source())(raw_scope(headers, path, method), receive, send)
```

Check:

```bash
uv run pytest tests/test_api_routes.py tests/test_app_authentication.py -q
```

Expected: all pass. The role test now runs every Source route under all sixteen role sets.

## Step 8 — Write the new tests

Coding step.

**In plain words:** two new test files prove the rules, the routes, and the database behaviour. The first runs without services: it tries every rule with good and bad values, fuzzes the rules to show a refusal never repeats what it was given, and calls the routes over a fake store. The second runs on real PostgreSQL, where uniqueness, locks, transactions, and paging actually happen.

| File | What it proves |
|---|---|
| `tests/test_source_administration.py` | each rule; refusals never quote the value; the body's limits and fields match the rules; 201 with `Location`; strict bodies; 409; secrets never sent back or logged; PATCH passes only named fields; 404; ID spelling; pages, a full last page, bad page requests |
| `tests/test_source_administration_integration.py` | registration stored and audited; one Source per kind and location, also for four registrations at once; change audited by setting name only; no-op change records nothing; rule-breaking change stores nothing; disabled Source never due; pages never repeat or skip; 409 for a stored Source needing repair, also for a NULL principal; a rule-breaking PATCH over HTTP names the rule and leaks nothing; enable and disable over HTTP; downgrade refuses to drop recorded events |

Create `tests/test_source_administration.py`:

```python
"""Source administration: each setting's rule, and the routes over a fake store.

A Source's settings come from an operator's request, so every one is
checked, and a refusal names the rule it broke, never the value. No secret
passes through: `credentials_reference` only names one.
"""

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from identity_fakes import READER
from knowledge_service.app import SourceAdministration, create_app
from knowledge_service.contracts import SourceChanges, SourceRegistration
from knowledge_service.identifiers import SourceId
from knowledge_service.identity.context import Caller
from knowledge_service.ingestion.sources import SourceKind
from knowledge_service.pagination import encode_cursor
from knowledge_service.persistence.sources import SourceRecord
from knowledge_service.roles import ApplicationRole
from knowledge_service.source_administration import (
    CHANGEABLE,
    MAX_CREDENTIALS_REFERENCE,
    MAX_DISPLAY_NAME,
    MAX_LOCATION,
    InvalidSourceSettings,
    SourceDefinition,
    SourceExists,
    SourceNotFound,
    source_cursor,
    source_position,
)

MARKER = "SECRET-MARKER-7f3a"
CREATED_AT = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
FOLDER = {
    "kind": "local_directory",
    "location": "/srv/handbook",
    "display_name": "Handbook",
}
SITE = {
    "kind": "confluence",
    "location": "https://acme.atlassian.net/wiki",
    "display_name": "Acme wiki",
    "credentials_reference": "confluence/acme-prod",
}
INVISIBLE = chr(0x202E)  # right-to-left override: shown text differs from stored


def definition(**settings: Any) -> SourceDefinition:
    return SourceDefinition.of(**{**FOLDER, **settings})


# Each rule, one setting at a time.


@pytest.mark.parametrize(
    "settings",
    [
        FOLDER,
        SITE,
        {**FOLDER, "location": "/srv/caf" + chr(0xE9)},
        {**SITE, "location": "https://wiki.acme.example:8443/confluence"},
        {**SITE, "location": "https://wiki.acme.example"},
    ],
)
def test_a_valid_source_is_accepted(settings: dict[str, Any]) -> None:
    assert SourceDefinition.of(**settings).location == settings["location"]


@pytest.mark.parametrize(
    "location",
    [
        "",
        "srv/handbook",
        "/",
        "//srv/handbook",
        "/srv/handbook/",
        "/srv/./handbook",
        "/srv/../handbook",
        "/srv//handbook",
        "/srv/handbook\n",
        "/srv/hand\x00book",
        "/srv/" + INVISIBLE + "handbook",
        "/srv/cafe" + chr(0x301),
        "/" + "a" * 2048,
    ],
)
def test_a_folder_is_an_absolute_path_in_its_one_spelling(location: str) -> None:
    with pytest.raises(InvalidSourceSettings):
        definition(location=location)


@pytest.mark.parametrize(
    "location",
    [
        "http://acme.atlassian.net/wiki",
        "https://user:pass@acme.atlassian.net/wiki",
        "https://acme.atlassian.net@evil.example/wiki",
        "https://Acme.atlassian.net/wiki",
        "https://acme.atlassian.net:443/wiki",
        "https://acme.atlassian.net:99999/wiki",
        "https://acme.atlassian.net/wiki/",
        "https://acme.atlassian.net/",
        "https://acme.atlassian.net/wiki?token=x",
        "https://acme.atlassian.net/wiki#top",
        "https://acme.atlassian.net/wik%69",
        "https://acme.atlassian.net/a/../wiki",
        "https:///wiki",
        "https://acme.atlassian.net/wiki ",
        "https://acmé.atlassian.net/wiki",
        "https://acme.atlassian.net\\wiki",
        "acme.atlassian.net/wiki",
        "https://acme.atlassian.net:/wiki",
        "https://acme.atlassian.net./wiki",
        "https://acme.atlassian.net:08443/wiki",
        "https://[::1]/wiki",
        "https://127.0.0.1/wiki",
        "https://0177.0.0.1/wiki",
        "https://localhost/wiki",
    ],
)
def test_a_site_is_an_https_address_in_its_one_spelling(location: str) -> None:
    with pytest.raises(InvalidSourceSettings):
        SourceDefinition.of(**{**SITE, "location": location})


def test_a_folder_needs_no_credentials() -> None:
    with pytest.raises(InvalidSourceSettings):
        definition(credentials_reference="confluence/acme-prod")


@pytest.mark.parametrize(
    "reference",
    [
        None,
        "",
        "ATATT3xFfGF0T4kq2",
        # Built in parts, so no secret scanner reads it as a real token.
        "ghp_" + "16C7e42F" * 4 + "AbCd",
        "confluence/acme prod",
        "confluence/",
        "/confluence",
        "a/b/c/d/e",
        "confluence/" + "a" * 120,
        "Confluence/acme",
    ],
)
def test_a_site_names_its_secret_and_never_holds_it(reference: str | None) -> None:
    with pytest.raises(InvalidSourceSettings):
        SourceDefinition.of(**{**SITE, "credentials_reference": reference})


@pytest.mark.parametrize(
    "display_name", ["", " Handbook", "Handbook ", "Hand\nbook", "Hand" + INVISIBLE]
)
def test_a_display_name_is_visible_text(display_name: str) -> None:
    with pytest.raises(InvalidSourceSettings):
        definition(display_name=display_name)


@pytest.mark.parametrize(
    ("public", "principals"),
    [
        (False, ["alice"]),
        (False, ["role:admin"]),
        (False, ["user:"]),
        (False, ["user: alice"]),
        (False, ["group:/eng\n"]),
        (False, ["user:alice", "user:alice"]),
        (False, [f"user:u{n}" for n in range(101)]),
        (True, ["group:/eng"]),
        ("yes", []),
        (False, "user:alice"),
    ],
)
def test_default_access_names_users_and_groups_once(
    public: object, principals: object
) -> None:
    with pytest.raises(InvalidSourceSettings):
        definition(default_public=public, default_principals=principals)


def test_default_principals_are_kept_in_one_order() -> None:
    given_order = definition(default_principals=["user:bob", "group:/eng", "user:al"])

    assert given_order.default_principals == ("group:/eng", "user:al", "user:bob")


@pytest.mark.parametrize("kind", ["", "folder", "LOCAL_DIRECTORY", 1, None])
def test_an_unknown_kind_is_refused(kind: object) -> None:
    with pytest.raises(InvalidSourceSettings):
        definition(kind=kind)


def test_the_unknown_kind_refusal_names_every_kind() -> None:
    with pytest.raises(InvalidSourceSettings) as refused:
        definition(kind="folder")

    assert all(kind.value in str(refused.value) for kind in SourceKind)


def test_a_change_may_set_exactly_the_fields_its_body_allows() -> None:
    assert set(SourceChanges.model_fields) == CHANGEABLE


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("display_name", MAX_DISPLAY_NAME),
        ("location", MAX_LOCATION),
        ("credentials_reference", MAX_CREDENTIALS_REFERENCE),
    ],
)
def test_the_body_allows_the_lengths_the_rules_allow(field: str, limit: int) -> None:
    constraints = SourceRegistration.model_fields[field].metadata

    assert any(getattr(c, "max_length", None) == limit for c in constraints)


@given(
    field=st.sampled_from(
        ["location", "display_name", "credentials_reference", "default_principals"]
    ),
    kind=st.sampled_from([FOLDER, SITE]),
    before=st.text(max_size=20),
    after=st.text(max_size=20),
)
def test_a_refusal_never_quotes_the_value(
    field: str, kind: dict[str, Any], before: str, after: str
) -> None:
    # A line break breaks every rule here, so each example is a real refusal.
    value = before + MARKER + "\n" + after
    settings: dict[str, Any] = {
        **kind,
        field: [value] if field == "default_principals" else value,
    }

    with pytest.raises(InvalidSourceSettings) as refused:
        SourceDefinition.of(**settings)

    assert MARKER not in str(refused.value)


# Cursors of the Source list.


def record(**changes: Any) -> SourceRecord:
    base = SourceRecord(
        source_id=SourceId.new(),
        kind="local_directory",
        location="/srv/handbook",
        display_name="Handbook",
        credentials_reference=None,
        enabled=True,
        default_public=False,
        default_principals=(),
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )
    return replace(base, **changes)


def test_a_source_cursor_starts_after_its_source() -> None:
    stored = record()

    assert source_position(source_cursor(stored)) == (CREATED_AT, stored.source_id)


# The routes, over a fake store.


class Sources:
    """Holds Sources newest first, and records what each request asked for."""

    def __init__(self, records: list[SourceRecord] | None = None) -> None:
        self.records = records or []
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def page(
        self, *, limit: int, after: tuple[datetime, SourceId] | None = None
    ) -> tuple[SourceRecord, ...]:
        self.calls.append({"limit": limit, "after": after})
        keys = [(r.created_at, r.source_id.value) for r in self.records]
        start = 0
        if after is not None:
            start = next(
                (i for i, key in enumerate(keys) if key < (after[0], after[1].value)),
                len(keys),
            )
        return tuple(self.records[start : start + limit])

    async def load(self, *, source_id: SourceId) -> SourceRecord | None:
        self.calls.append({"source_id": source_id})
        return next((r for r in self.records if r.source_id == source_id), None)

    async def register(self, **request: Any) -> SourceRecord:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        found: SourceDefinition = request["definition"]
        return record(
            kind=found.kind.value,
            location=found.location,
            display_name=found.display_name,
            credentials_reference=found.credentials_reference,
            default_public=found.default_public,
            default_principals=found.default_principals,
        )

    async def change(self, **request: Any) -> SourceRecord:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return record(display_name="Changed")

    async def set_enabled(self, **request: Any) -> SourceRecord:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return record(enabled=request["enabled"])


class ContentManager:
    """Gives every request a content manager's caller."""

    async def authenticate(self, headers: list[str]) -> Caller:
        return Caller(reader=READER, roles=frozenset({ApplicationRole.CONTENT_MANAGER}))


async def call(
    sources: Sources, method: str, path: str, body: Any = None
) -> httpx.Response:
    app = create_app(
        authenticator=ContentManager(), sources=cast(SourceAdministration, sources)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.request(
            method, path, json=body, headers={"Authorization": "Bearer t"}
        )


async def test_registering_answers_201_with_the_source_and_where_it_lives() -> None:
    sources = Sources()

    response = await call(
        sources,
        "POST",
        "/v1/sources",
        {**FOLDER, "default_principals": ["user:bob", "group:/eng"]},
    )

    body = response.json()
    assert response.status_code == 201
    assert response.headers["location"] == f"/v1/sources/{body['source_id']}"
    assert body["default_principals"] == ["group:/eng", "user:bob"]
    assert (body["enabled"], body["credentials_reference"]) == (True, None)
    assert sources.calls[0]["operator"] == READER.user_id


@pytest.mark.parametrize(
    "body",
    [
        {**FOLDER, "password": "hunter2"},
        {**FOLDER, "enabled": False},
        {**FOLDER, "default_public": "yes"},
        {**FOLDER, "default_principals": "user:bob"},
        {"kind": "local_directory", "location": "/srv/handbook"},
        ["not", "an", "object"],
    ],
)
async def test_a_body_of_another_shape_is_refused_before_the_store(
    body: Any,
) -> None:
    sources = Sources()

    response = await call(sources, "POST", "/v1/sources", body)

    assert (response.status_code, response.json()["code"]) == (422, "validation_error")
    assert sources.calls == []


async def test_a_broken_rule_is_named_and_nothing_is_stored() -> None:
    sources = Sources()

    response = await call(
        sources, "POST", "/v1/sources", {**FOLDER, "location": "srv/handbook"}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_source_settings"
    assert "absolute folder path" in response.json()["message"]
    assert sources.calls == []


async def test_a_second_source_of_the_same_kind_and_location_is_a_409() -> None:
    sources = Sources()
    sources.error = SourceExists("a source of this kind and location is registered")

    response = await call(sources, "POST", "/v1/sources", FOLDER)

    assert (response.status_code, response.json()["code"]) == (409, "source_exists")


@pytest.mark.parametrize(
    "body",
    [
        {**SITE, "credentials_reference": MARKER},
        {**SITE, "location": f"https://bot:{MARKER}@acme.atlassian.net/wiki"},
        {**SITE, "location": f"https://acme.atlassian.net/wiki?token={MARKER}"},
        {**FOLDER, "credentials_reference": MARKER.lower()},
        {**FOLDER, "token": MARKER},
        {**FOLDER, "display_name": MARKER + "\n"},
    ],
)
async def test_a_refused_secret_is_never_sent_back_or_logged(
    body: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    sources = Sources()

    response = await call(sources, "POST", "/v1/sources", body)

    assert response.status_code == 422
    assert MARKER.lower() not in (response.text + caplog.text).lower()
    assert sources.calls == []


async def test_a_store_failure_names_no_value_to_the_client_or_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    sources = Sources()
    sources.error = RuntimeError(f"insert failed for {MARKER}")

    response = await call(sources, "POST", "/v1/sources", FOLDER)

    assert response.status_code == 500
    assert MARKER not in response.text + caplog.text


async def test_a_change_passes_only_the_fields_the_body_names() -> None:
    sources = Sources()
    path = f"/v1/sources/{SourceId.new().value}"

    response = await call(sources, "PATCH", path, {"credentials_reference": None})

    assert response.status_code == 200
    assert sources.calls[0]["changes"] == {"credentials_reference": None}


@pytest.mark.parametrize(
    "body",
    [
        {"location": "/srv/other"},
        {"kind": "confluence"},
        {"enabled": False},
        {"display_name": 5},
    ],
)
async def test_a_change_to_what_a_source_is_is_refused_before_the_store(
    body: dict[str, Any],
) -> None:
    sources = Sources()
    path = f"/v1/sources/{SourceId.new().value}"

    response = await call(sources, "PATCH", path, body)

    assert (response.status_code, response.json()["code"]) == (422, "validation_error")
    assert sources.calls == []


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("GET", "", None),
        ("PATCH", "", {"display_name": "X"}),
        ("POST", "/enable", None),
        ("POST", "/disable", None),
    ],
)
async def test_a_missing_source_is_a_404(
    method: str, suffix: str, body: dict[str, Any] | None
) -> None:
    sources = Sources()
    sources.error = SourceNotFound("no source has this ID")

    response = await call(
        sources, method, f"/v1/sources/{SourceId.new().value}{suffix}", body
    )

    assert (response.status_code, response.json()["code"]) == (404, "source_not_found")


@pytest.mark.parametrize(
    "source_id",
    ["not-an-id", "{00000000-0000-0000-0000-000000000001}", "0" * 32],
)
async def test_an_id_in_another_spelling_is_refused_before_the_store(
    source_id: str,
) -> None:
    sources = Sources()

    response = await call(sources, "GET", f"/v1/sources/{source_id}")

    assert response.status_code == 422
    assert sources.calls == []


async def test_pages_of_sources_follow_each_other_to_the_end() -> None:
    records = sorted(
        (record(created_at=CREATED_AT - timedelta(minutes=n // 2)) for n in range(5)),
        key=lambda r: (r.created_at, r.source_id.value),
        reverse=True,
    )
    sources = Sources(records)
    seen: list[str] = []
    query = "?limit=2"

    for _ in range(10):
        page = (await call(sources, "GET", f"/v1/sources{query}")).json()
        seen += [item["source_id"] for item in page["items"]]
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    assert seen == [str(r.source_id.value) for r in records]


async def test_a_full_last_page_of_sources_says_no_page_follows() -> None:
    records = sorted(
        (record(created_at=CREATED_AT - timedelta(minutes=n)) for n in range(4)),
        key=lambda r: (r.created_at, r.source_id.value),
        reverse=True,
    )
    sources = Sources(records)

    first = (await call(sources, "GET", "/v1/sources?limit=2")).json()
    cursor = first["next_cursor"]
    second = (await call(sources, "GET", f"/v1/sources?limit=2&cursor={cursor}")).json()

    assert len(second["items"]) == 2
    assert second["next_cursor"] is None


@pytest.mark.parametrize(
    "query",
    [
        "?limit=0",
        "?offset=2",
        "?cursor="
        + encode_cursor("job-decisions", {"finished_at": "x", "job_id": "y"}),
    ],
)
async def test_a_bad_page_request_is_refused_before_the_store(query: str) -> None:
    sources = Sources()

    response = await call(sources, "GET", f"/v1/sources{query}")

    assert response.status_code == 422
    assert sources.calls == []
```

Create `tests/test_source_administration_integration.py`:

```python
"""Source administration on real PostgreSQL.

Each change is stored with its audit event in one transaction; a Source's
kind and location are taken once, even by two registrations at the same
moment; pages of Sources neither repeat nor skip one; and no audit event
holds a setting's value.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from authorization_scenarios import Person
from database_fixtures import migration_config, migration_environment
from knowledge_service.app import create_app
from knowledge_service.audit import AuditAction
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import SourceId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.persistence.audit import load_audit_events
from knowledge_service.persistence.schema import audit_events, sources
from knowledge_service.persistence.sources import load_due_source_ids
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings
from knowledge_service.source_administration import (
    DatabaseSourceAdministration,
    InvalidSourceSettings,
    SourceDefinition,
    SourceExists,
    SourceNotFound,
    change_source,
    register_source,
    set_source_enabled,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
MARKER = "SECRET-MARKER-7f3a"
CASEY = Person("casey", ())
OPERATOR = CASEY.context().user_id
CALLERS = {
    "Bearer casey": Caller(
        reader=CASEY.context(), roles=frozenset({ApplicationRole.CONTENT_MANAGER})
    ),
}
FOLDER = SourceDefinition.of(
    kind="local_directory", location="/srv/handbook", display_name="Handbook"
)


class Tokens:
    """Gives each test token the caller its claims would give."""

    async def authenticate(self, headers: list[str]) -> Caller:
        if len(headers) == 1 and headers[0] in CALLERS:
            return CALLERS[headers[0]]
        raise Unauthenticated("test", error=AuthenticationError.INVALID_TOKEN)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def register(
    runtime: DatabaseRuntime, definition: SourceDefinition = FOLDER, **at: Any
) -> SourceId:
    record = await register_source(
        runtime, definition=definition, operator=OPERATOR, now=at.get("now", NOW)
    )
    return record.source_id


async def trail(runtime: DatabaseRuntime, source_id: SourceId) -> list[Any]:
    async with runtime.transaction() as connection:
        events = await load_audit_events(
            connection, target_id=source_id.value, limit=50
        )
    return [(e.action, e.actor_id, dict(e.metadata)) for e in events]


async def count_sources(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        return int(
            await connection.scalar(sa.select(sa.func.count()).select_from(sources))
            or 0
        )


async def over_http(
    runtime: DatabaseRuntime, method: str, path: str, body: Any = None
) -> httpx.Response:
    app = create_app(
        authenticator=Tokens(),
        sources=DatabaseSourceAdministration(runtime, clock=lambda: NOW),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.request(
            method, path, json=body, headers={"Authorization": "Bearer casey"}
        )


async def test_a_registered_source_is_stored_enabled_and_recorded(
    runtime: DatabaseRuntime,
) -> None:
    response = await over_http(
        runtime,
        "POST",
        "/v1/sources",
        {
            "kind": "local_directory",
            "location": "/srv/handbook",
            "display_name": "Handbook",
            "default_principals": ["group:/engineering"],
        },
    )
    source_id = SourceId.parse(response.json()["source_id"])

    read = await over_http(runtime, "GET", f"/v1/sources/{source_id.value}")

    assert response.status_code == 201
    assert read.json() == response.json()
    assert read.json()["enabled"] is True
    assert await trail(runtime, source_id) == [
        (AuditAction.SOURCE_REGISTERED, OPERATOR, {"kind": "local_directory"})
    ]


async def test_the_same_kind_and_location_is_registered_once(
    runtime: DatabaseRuntime,
) -> None:
    first = await register(runtime)

    with pytest.raises(SourceExists):
        await register(
            runtime,
            SourceDefinition.of(
                kind="local_directory", location="/srv/handbook", display_name="Again"
            ),
        )

    assert await count_sources(runtime) == 1
    assert len(await trail(runtime, first)) == 1


async def test_two_registrations_at_once_store_one_source(
    runtime: DatabaseRuntime,
) -> None:
    results = await asyncio.gather(
        *(register(runtime) for _ in range(4)), return_exceptions=True
    )

    stored = [r for r in results if isinstance(r, SourceId)]
    assert len(stored) == 1
    assert all(isinstance(r, SourceExists) for r in results if r not in stored)
    assert await count_sources(runtime) == 1


async def test_a_duplicate_over_http_is_a_409_and_stores_nothing(
    runtime: DatabaseRuntime,
) -> None:
    body = {"kind": "local_directory", "location": "/srv/handbook", "display_name": "A"}
    await over_http(runtime, "POST", "/v1/sources", body)

    again = await over_http(
        runtime, "POST", "/v1/sources", {**body, "display_name": "B"}
    )

    assert (again.status_code, again.json()["code"]) == (409, "source_exists")
    assert await count_sources(runtime) == 1


async def test_a_change_records_which_settings_changed_never_their_values(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await register(runtime)

    changed = await change_source(
        runtime,
        source_id=source_id,
        changes={"display_name": f"Handbook {MARKER}", "default_public": True},
        operator=OPERATOR,
        now=NOW + timedelta(minutes=1),
    )

    assert (changed.display_name, changed.default_public) == (
        f"Handbook {MARKER}",
        True,
    )
    assert changed.updated_at == NOW + timedelta(minutes=1)
    assert (await trail(runtime, source_id))[1] == (
        AuditAction.SOURCE_UPDATED,
        OPERATOR,
        {"default_public": True, "display_name": True},
    )
    async with runtime.transaction() as connection:
        stored = await connection.scalar(
            sa.select(sa.func.string_agg(sa.cast(audit_events.c.metadata, sa.Text), ""))
        )
    assert MARKER not in str(stored)


async def test_a_change_to_the_values_it_has_records_nothing(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await register(runtime)

    unchanged = await change_source(
        runtime,
        source_id=source_id,
        changes={"display_name": "Handbook", "default_principals": []},
        operator=OPERATOR,
        now=NOW + timedelta(minutes=1),
    )

    assert unchanged.updated_at == NOW
    assert len(await trail(runtime, source_id)) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"credentials_reference": "confluence/acme-prod"},
        {"default_public": True, "default_principals": ["user:alice"]},
        {"location": "/srv/other"},
        {},
    ],
)
async def test_a_change_that_breaks_a_rule_stores_nothing(
    runtime: DatabaseRuntime, changes: dict[str, Any]
) -> None:
    source_id = await register(runtime)

    with pytest.raises(InvalidSourceSettings):
        await change_source(
            runtime,
            source_id=source_id,
            changes=changes,
            operator=OPERATOR,
            now=NOW + timedelta(minutes=1),
        )

    assert len(await trail(runtime, source_id)) == 1


async def test_a_change_to_a_missing_source_is_not_found(
    runtime: DatabaseRuntime,
) -> None:
    with pytest.raises(SourceNotFound):
        await change_source(
            runtime,
            source_id=SourceId.new(),
            changes={"display_name": "X"},
            operator=OPERATOR,
            now=NOW,
        )


async def test_a_disabled_source_is_never_due_and_each_switch_is_recorded_once(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await register(runtime)
    later = NOW + timedelta(days=1)

    for _ in range(2):
        await set_source_enabled(
            runtime, source_id=source_id, enabled=False, operator=OPERATOR, now=later
        )
    async with runtime.transaction() as connection:
        due = await load_due_source_ids(
            connection, now=later, interval=timedelta(hours=1), limit=10
        )
    await set_source_enabled(
        runtime, source_id=source_id, enabled=True, operator=OPERATOR, now=later
    )

    assert due == ()
    assert [action for action, _, _ in await trail(runtime, source_id)] == [
        AuditAction.SOURCE_REGISTERED,
        AuditAction.SOURCE_DISABLED,
        AuditAction.SOURCE_ENABLED,
    ]


async def test_pages_of_sources_never_repeat_or_skip_one(
    runtime: DatabaseRuntime,
) -> None:
    # Pairs registered in the same microsecond, so the ID breaks the tie.
    for n in range(6):
        await register(
            runtime,
            SourceDefinition.of(
                kind="local_directory", location=f"/srv/s{n}", display_name=f"S{n}"
            ),
            now=NOW + timedelta(minutes=n // 2),
        )
    seen: list[str] = []
    query = "?limit=2"

    for page_number in range(10):
        page = (await over_http(runtime, "GET", f"/v1/sources{query}")).json()
        seen += [item["source_id"] for item in page["items"]]
        if page_number == 0:
            # A Source registered between pages is newer: it starts no page here.
            await register(
                runtime,
                SourceDefinition.of(
                    kind="local_directory", location="/srv/new", display_name="New"
                ),
                now=NOW + timedelta(hours=1),
            )
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(sources.c.source_id)
            .where(sources.c.location != "/srv/new")
            .order_by(sources.c.created_at.desc(), sources.c.source_id.desc())
        )
        expected = [str(row.source_id) for row in rows]
    assert seen == expected


def test_a_downgrade_stops_rather_than_lose_a_recorded_source_change(
    migrated_database: str,
) -> None:
    # Synchronous: Alembic runs its own event loop.
    url = make_url(migrated_database)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO audit_events (event_id, occurred_at, action,"
                    " target_kind, target_id, metadata, decision, outcome)"
                    " VALUES (gen_random_uuid(), now(), 'source.updated', 'source',"
                    " gen_random_uuid(), '{}'::jsonb, 'allowed', 'succeeded')"
                )
            )
        with migration_environment(url), pytest.raises(Exception, match="recorded"):
            command.downgrade(migration_config(), "f2a3b4c5d6e7")
        with engine.begin() as connection:
            count = connection.execute(
                sa.text("SELECT count(*) FROM audit_events")
            ).scalar()
    finally:
        engine.dispose()

    assert count == 1


async def test_a_stored_source_that_breaks_a_rule_is_named_not_blamed_on_the_body(
    runtime: DatabaseRuntime,
) -> None:
    # Written before the rules, or by hand: a trailing `/` breaks one now.
    source_id = SourceId.new()
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.insert(sources).values(
                source_id=source_id.value,
                kind="local_directory",
                location="/datasets/x/public/",
                display_name="Old",
                enabled=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    response = await over_http(
        runtime, "PATCH", f"/v1/sources/{source_id.value}", {"display_name": "New"}
    )

    assert (response.status_code, response.json()["code"]) == (
        409,
        "source_needs_repair",
    )
    assert len(await trail(runtime, source_id)) == 0


async def test_a_change_that_breaks_a_rule_over_http_names_the_rule_only(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    source_id = await register(runtime)

    response = await over_http(
        runtime,
        "PATCH",
        f"/v1/sources/{source_id.value}",
        {"credentials_reference": MARKER.lower()},
    )

    assert (response.status_code, response.json()["code"]) == (
        422,
        "invalid_source_settings",
    )
    assert MARKER.lower() not in (response.text + caplog.text).lower()
    async with runtime.transaction() as connection:
        stored = await connection.scalar(
            sa.select(sources.c.credentials_reference).where(
                sources.c.source_id == source_id.value
            )
        )
    assert stored is None
    assert len(await trail(runtime, source_id)) == 1


async def test_switching_over_http_answers_the_new_state_and_records_each_change(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await register(runtime)
    path = f"/v1/sources/{source_id.value}"

    states = [
        (await over_http(runtime, "POST", f"{path}/{switch}")).json()["enabled"]
        for switch in ("disable", "disable", "enable")
    ]

    assert states == [False, False, True]
    assert [action for action, _, _ in await trail(runtime, source_id)] == [
        AuditAction.SOURCE_REGISTERED,
        AuditAction.SOURCE_DISABLED,
        AuditAction.SOURCE_ENABLED,
    ]


async def test_a_stored_principal_that_names_nobody_needs_repair(
    runtime: DatabaseRuntime,
) -> None:
    # The column allows a NULL element; synchronization refuses such a Source.
    source_id = SourceId.new()
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO sources (source_id, kind, location, display_name,"
                " enabled, created_at, updated_at, default_principals) VALUES"
                " (:id, 'local_directory', '/srv/old', 'Old', true, now(), now(),"
                " ARRAY['user:a', NULL]::text[])"
            ),
            {"id": source_id.value},
        )
    path = f"/v1/sources/{source_id.value}"

    shown = await over_http(runtime, "GET", path)
    changed = await over_http(runtime, "PATCH", path, {"display_name": "New"})

    assert shown.json()["default_principals"] == ["user:a", None]
    assert (changed.status_code, changed.json()["code"]) == (
        409,
        "source_needs_repair",
    )
    assert len(await trail(runtime, source_id)) == 0
```

`MARKER` stands for a secret: a test plants it in a value and then searches the response, the log, and the audit rows for it. `INVISIBLE` is built with `chr()` rather than typed, so the file stays plain ASCII and no editor hides it.

Check:

```bash
uv run pytest tests/test_source_administration.py -q
uv run pytest tests/test_source_administration_integration.py -q -m integration
```

Expected: `114 passed`, then `18 passed` (the second needs the test database, as in earlier lessons).

## Step 9 — Break uniqueness on purpose

No coding in this step.

This step proves the integration tests guard the "one Source per kind and location" rule. In `src/knowledge_service/persistence/sources.py`, delete this one line from `insert_source_record`:

```python
        .on_conflict_do_nothing(constraint="uq_sources_kind_location")
```

Run the integration tests with bytecode off, so Python does not reuse the old compiled file:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider tests/test_source_administration_integration.py -q -m integration
```

Expected: `3 failed, 15 passed`. The failures are `test_the_same_kind_and_location_is_registered_once`, `test_two_registrations_at_once_store_one_source`, and `test_a_duplicate_over_http_is_a_409_and_stores_nothing`. Without `ON CONFLICT`, the database still refuses the second row, but as an `IntegrityError` the code does not expect, so the API answers 500 instead of 409. Put the line back and run again: `18 passed`.

What would falsify the design: two rows in `sources` for one folder, a 422 or 404 to a caller without the role, a secret or a refused value in any response, log line, or audit row, or a change that is stored without its audit event.

## Step 10 — Update the architecture and threat model

No coding in this step.

In `docs/architecture.md`, before the paragraph that starts with `` `api_routes` is the native API's design ``, add:

```markdown
The Source routes, `GET` and `POST /v1/sources`, `GET` and
`PATCH /v1/sources/{source_id}`, and `POST .../enable` and
`POST .../disable`, require `configure_sources` (the content-manager
role) before the ID, page, or body is read. `source_administration`
checks every setting (`SourceDefinition`): a folder is an absolute path
and a Confluence site an https address with a host name, each in its one
spelling, so the kind and location, unique in `sources`, are the
Source's identity and never change. `credentials_reference` names a
secret in the secret store and never holds one; default readers are
`user:<id>` or `group:<id>`. A refusal names the rule, never the value.
Each change locks the Source, stores it, and records `source.registered`,
`source.updated` (which settings changed, never their values),
`source.enabled`, or `source.disabled` in the same transaction; a change
to the values a Source has records nothing. Disabling waits for a
scheduler that is queueing a Job for the Source, but does not cancel a
Job already queued or running. New default readers reach stored
Documents at the Source's next pass.
```

In `docs/threat-model.md`:

- in the entry points table, before the `GET /health/live` row, add:

```markdown
| `GET` and `POST /v1/sources`, `GET` and `PATCH /v1/sources/{source_id}`, `POST /v1/sources/{source_id}/enable`, `POST /v1/sources/{source_id}/disable` | Any client that reaches the API | Lists, registers, reads, and changes Sources. Needs a bearer token and the `configure_sources` operation (the `content-manager` role), both checked before the ID, `limit`, `cursor`, or body is read; every setting is checked, and a refusal names the rule, never the value; `credentials_reference` only names a secret; each change is an audit event with the operator as actor, naming which settings changed, never their values |
```

- in the Ingestion table, before the row "A Source reports no readers, and the Document becomes public by mistake", add:

```markdown
| An operator's request stores a secret, or gets one shown back | `credentials_reference` must be a secret's name: lowercase words joined by `-`, in up to four `/` parts; a Confluence address with a user, password, query, or fragment is refused; no request value is sent back in an error, logged, or written to an audit event | A secret that happens to fit the name rule, such as one lowercase word, is stored as a name |
| Two Sources read the same folder or site, so its Documents are stored twice | A Source is its kind and location, unique in the database; a location must be in its one spelling (no `.`, `..`, doubled or trailing `/`, letters in one Unicode form, a lowercase host name with no default port), so another spelling is refused, not stored again | A symlink, a case-insensitive file system, or a second address of one site still make two Sources of one place |
```

- in the Operations row "A role widens what its holder reads", the last cell becomes "Role checks on the synchronization, evaluation, and policy entry points when they exist";
- in the known gaps, "Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity;" becomes "Starting Synchronization Runs and running evaluations are scripts a developer runs with no identity;".

Check: `make docs-check` passes.

## Step 11 — Run the gates and write the evidence

No coding in this step.

Run the gates one after another, on one test database:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
make docs-check
make test-unit
make test-integration
git diff --check
uv run pre-commit run --all-files
```

On the prototype these gave: 367 files formatted, all checks passed, 0 errors, docs-check exit 0, `3606 passed` unit, and `928 passed` integration with Keycloak's secrets and Redis available.

Then write `docs/evidence/M09-T02-source-administration.md` in your own words: the invariant and owners, your predictions from Step 1 and what happened, the expected failing check, the six routes and their answers, how a duplicate is refused, how secrets stay out, the audit events, the deliberate break, the gate results, and what would falsify the design. This lesson's code makes no external call and no paid call. A request is not retried by the service: a client that repeats a registration gets 409, and a repeated change or switch records nothing the second time.

## Completion checklist

- [ ] `uv run alembic heads` shows `a3b4c5d6e7f8`, and the three new audit actions are in Python and in the database check.
- [ ] All six Source routes are served and marked served in `ROUTES`; every route check in `tests/test_api_routes.py` passes.
- [ ] A caller without the content-manager role gets 403 on every Source route, before any ID, page, or body is read.
- [ ] A folder or site in a second spelling is refused with 422 `invalid_source_settings`, and the message names the rule, never the value.
- [ ] A second Source of the same kind and location gets 409 `source_exists`, also when four registrations race.
- [ ] `credentials_reference` accepts only a secret's name, and none for a folder; no refused value appears in a response, a log, or an audit row.
- [ ] `PATCH` changes only the fields it names; kind, location, and enabled never change through it.
- [ ] Each real change writes exactly one audit event naming which settings changed; a no-op writes none.
- [ ] `tests/test_source_administration.py` (114) and `tests/test_source_administration_integration.py` (18) pass.
- [ ] The deliberate break gave 3 failed, 15 passed, and the restored code passes.
- [ ] `docs/architecture.md` and `docs/threat-model.md` are updated, and every gate in Step 11 passes.
- [ ] The evidence file is written in your own words.

## Reflection questions

1. Two content managers register the same folder at the same moment. Walk through what each request's SQL does and why exactly one row ends up in `sources`. What would go wrong if the code first ran a `SELECT` to check, then an `INSERT`?
2. Why does the API refuse `/srv/handbook/` instead of quietly storing it as `/srv/handbook`? Think about what the operator sees afterwards and about a value that has several possible "fixed" forms.
3. Pydantic would have read the body for us if the route declared it as a parameter. What would a caller without the content-manager role learn from the answers in that case, and why does that matter for an admin API?
4. A pasted Atlassian token in `credentials_reference` is refused. Which places could it have leaked into if the error message quoted the value, and what in this lesson keeps it out of each one?
5. A content manager disables a Source while the scheduler is queueing a Job for it. Using the share lock and the update lock, explain why the Job is queued either before the change or not at all. What still happens to a Job that was queued just before?
6. The audit event for a change says which settings changed but not their new values. What would a reviewer of the audit log lose because of that, and what would the service risk if it stored the values?
