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
