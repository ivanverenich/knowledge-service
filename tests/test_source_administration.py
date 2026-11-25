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
