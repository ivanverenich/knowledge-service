"""Only the verifier's output can make a request's caller.

The app gets the real `Authenticator` over a fake verifier. Each test checks
what the Evidence source was asked as, so a reader that came from anywhere
but a verified token would show up here, and that a caller without the role
an endpoint needs is refused before anything runs.
"""

import logging
from typing import Any, cast

import httpx
import pytest

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.generation import InvalidAnswerRequest
from knowledge_service.app import create_app
from knowledge_service.identity.context import (
    Authenticator,
    Caller,
    group_id_for,
    user_id_for,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.roles import ApplicationRole, Operation
from knowledge_service.workflow import AnswerWorkflow
from test_authorization_context import (
    ALICE,
    ISSUER,
    MARKER,
    POLICY,
    Verifier,
    verified,
)
from test_roles import MATRIX, ROLE_SETS
from test_streaming import QUESTION, REQUEST_ID, Model, Source, streamer

ALICE_CONTEXT = AuthorizationContext(
    user_id=user_id_for(ISSUER, ALICE),
    group_ids=frozenset({group_id_for(ISSUER, "/engineering")}),
)
TOKENS = {
    "alice-token": verified(),
    "outsider-token": verified(azp="account-console"),
    "crowded-token": verified(groups=[f"/g{n}" for n in range(65)]),
    "roleless-token": verified(realm_access={"roles": []}),
    "staff-token": verified(
        realm_access={"roles": ["content-manager", "evaluator", "administrator"]}
    ),
    "admin-user-token": verified(realm_access={"roles": ["administrator", "user"]}),
}


class Workflow:
    """Counts the ungrounded answers it was asked for."""

    def __init__(self) -> None:
        self.calls = 0

    async def answer(self, question: Any, *, request_id: Any) -> Any:
        self.calls += 1
        raise AssertionError("an unauthenticated request reached the workflow")


def app_with(
    source: Source, verifier: Verifier | None = None, *, authenticated: bool = True
) -> Any:
    authenticator = Authenticator(verifier or Verifier(TOKENS), POLICY)
    return create_app(
        streamer=streamer(Model(), source=source),
        authenticator=authenticator if authenticated else None,
    )


def client(app: Any) -> httpx.AsyncClient:
    """A client that sends no Authorization header unless a test adds one."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


async def ask(app: Any, headers: Any = None, **body: Any) -> httpx.Response:
    async with client(app) as http:
        return await http.post(
            "/v1/answer/stream",
            json={"text": QUESTION.text, **body},
            headers=headers,
        )


# --- Success: the reader is exactly what the token says -------------------------------


async def test_a_verified_token_is_the_reader_the_search_runs_as() -> None:
    source = Source()

    response = await ask(app_with(source), {"Authorization": "Bearer alice-token"})

    assert response.status_code == 200
    assert source.readers == [ALICE_CONTEXT]


async def test_headers_and_body_fields_cannot_choose_or_widen_the_reader() -> None:
    source = Source()
    spoofed = {
        "Authorization": "Bearer alice-token",
        "X-User-Id": "0f5e7d2a-0000-0000-0000-000000000000",
        "X-Groups": "/finance",
        "X-Forwarded-User": "carol",
        "X-Auth-Request-Groups": "/finance",
    }

    response = await ask(
        app_with(source),
        spoofed,
        user_id="carol",
        groups=["/finance"],
        reader={"user_id": "carol"},
    )

    assert response.status_code == 200
    assert source.readers == [ALICE_CONTEXT]


# --- Denials: no reader, and nothing runs ---------------------------------------------


@pytest.mark.parametrize(
    ("headers", "challenge"),
    [
        (None, "Bearer"),
        ({"Authorization": "Basic YWxpY2U6c2VjcmV0"}, "Bearer"),
        ({"Authorization": "Bearer forged-token"}, 'Bearer error="invalid_token"'),
        ({"Authorization": "Bearer outsider-token"}, 'Bearer error="invalid_token"'),
        ({"Authorization": "Bearer crowded-token"}, 'Bearer error="invalid_token"'),
    ],
    ids=["missing", "basic", "forged", "untrusted-client", "too-many-groups"],
)
async def test_a_request_without_a_usable_token_gets_401_and_no_search(
    headers: Any, challenge: str
) -> None:
    source = Source()

    response = await ask(app_with(source), headers)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == challenge
    assert response.json()["code"] == "unauthenticated"
    assert source.readers == []


@pytest.mark.parametrize(
    "headers",
    [
        [("Authorization", "Bearer alice-token"), ("Authorization", "Bearer x")],
        [("Authorization", "Bearer")],
        [("Authorization", "Bearer alice-token extra")],
    ],
    ids=["repeated", "no-token", "trailing-word"],
)
async def test_a_malformed_authorization_header_is_a_400(
    headers: list[tuple[str, str]],
) -> None:
    source = Source()

    response = await ask(app_with(source), httpx.Headers(headers))

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_request"
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_request"'
    assert source.readers == []


def raw_scope(
    headers: list[tuple[bytes, bytes]], path: str = "/v1/answer/stream"
) -> dict[str, Any]:
    """An ASGI request scope, for what an HTTP client would never send."""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"application/json"), *headers],
        "client": ("127.0.0.1", 1),
        "server": ("t", 80),
    }


async def test_header_names_in_any_case_are_all_seen() -> None:
    # The ASGI spec asks servers for lower-case names; one that does not
    # must not hide a second Authorization header.
    source = Source()
    messages = [{"type": "http.request", "body": b'{"text": "Leave?"}'}]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = [
        (b"authorization", b"Bearer alice-token"),
        (b"Authorization", b"Bearer outsider-token"),
    ]
    await app_with(source)(raw_scope(headers), receive, send)

    assert sent[0]["status"] == 400
    assert source.readers == []


async def test_a_body_is_not_read_before_the_reader_is_known() -> None:
    read: list[int] = []
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        read.append(1)
        return {"type": "http.request", "body": b" " * 65_536, "more_body": True}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app_with(Source())(raw_scope([]), receive, send)

    assert sent[0]["status"] == 401
    assert read == []


@pytest.mark.parametrize(
    ("verifier", "authenticated", "headers", "status"),
    [
        (None, True, {}, 401),
        (None, False, {"Authorization": "Bearer alice-token"}, 503),
        (
            Verifier(error=IdentityProviderUnavailable("down")),
            True,
            {"Authorization": "Bearer alice-token"},
            503,
        ),
    ],
    ids=["no-token", "no-authenticator", "provider-down"],
)
@pytest.mark.parametrize("body", [b"{not json", b"[", b'{"text": ""}'])
async def test_identity_is_decided_before_the_body_is_parsed(
    verifier: Verifier | None,
    authenticated: bool,
    headers: dict[str, str],
    status: int,
    body: bytes,
) -> None:
    app = app_with(Source(), verifier, authenticated=authenticated)

    async with client(app) as http:
        for path in ("/v1/answer", "/v1/answer/stream"):
            response = await http.post(
                path,
                content=body,
                headers={"Content-Type": "application/json", **headers},
            )
            assert response.status_code == status


async def test_an_authenticated_body_over_the_limit_is_a_413() -> None:
    source = Source()

    response = await ask(
        app_with(source),
        {"Authorization": "Bearer alice-token"},
        padding="x" * 70_000,
    )

    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"
    assert source.readers == []


async def test_a_body_without_a_length_is_cut_off_at_the_limit() -> None:
    # No Content-Length: the limit must hold while the body streams in.
    read: list[int] = []
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        read.append(1)
        return {"type": "http.request", "body": b" " * 16_384, "more_body": True}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = [(b"authorization", b"Bearer alice-token")]
    await app_with(Source())(raw_scope(headers), receive, send)

    assert sent[0]["status"] == 413
    assert len(read) == 5


async def test_an_authenticated_invalid_body_is_still_a_422() -> None:
    source = Source()

    async with client(app_with(source)) as http:
        response = await http.post(
            "/v1/answer/stream",
            json={"text": ""},
            headers={"Authorization": "Bearer alice-token"},
        )

    assert response.status_code == 422
    assert source.readers == []


class Broken:
    """An authenticator that fails in a way nobody planned for."""

    async def authenticate(self, headers: list[str]) -> Caller:
        raise KeyError(MARKER)


class Careless:
    """An authenticator that returns something other than exactly a `Caller`."""

    def __init__(self, returned: object) -> None:
        self.returned = returned

    async def authenticate(self, headers: list[str]) -> Caller:
        return cast(Caller, self.returned)


class LooseCaller(Caller):
    """A subclass could change what `roles` or `reader` answer."""

    __slots__ = ()


async def test_an_unexpected_authenticator_error_is_a_503_naming_only_its_type(
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = Source()
    app = create_app(streamer=streamer(Model(), source=source), authenticator=Broken())

    response = await ask(app, {"Authorization": "Bearer alice-token"})

    assert response.status_code == 503
    assert MARKER not in response.text + caplog.text
    assert "authentication failed: KeyError" in caplog.messages
    assert source.readers == []


@pytest.mark.parametrize(
    "returned",
    [ALICE_CONTEXT, LooseCaller(reader=ALICE_CONTEXT, roles=frozenset())],
    ids=["bare-reader", "caller-subclass"],
)
async def test_an_authenticator_that_returns_no_caller_is_a_503(
    returned: object, caplog: pytest.LogCaptureFixture
) -> None:
    source = Source()
    app = create_app(
        streamer=streamer(Model(), source=source), authenticator=Careless(returned)
    )

    response = await ask(app, {"Authorization": "Bearer alice-token"})

    assert response.status_code == 503
    assert "authentication failed: not a Caller" in caplog.messages
    assert source.readers == []


async def test_the_streamer_refuses_a_reader_that_is_not_a_context() -> None:
    with pytest.raises(InvalidAnswerRequest):
        await streamer(Model()).open(
            QUESTION,
            reader=cast(AuthorizationContext, ALICE_CONTEXT.user_id),
            request_id=REQUEST_ID,
        )


async def test_the_ungrounded_endpoint_needs_a_token_too() -> None:
    workflow = Workflow()
    app = create_app(
        workflow=cast(AnswerWorkflow, workflow),
        authenticator=Authenticator(Verifier(TOKENS), POLICY),
    )

    async with client(app) as http:
        response = await http.post("/v1/answer", json={"text": QUESTION.text})

    assert response.status_code == 401
    assert workflow.calls == 0


# --- Application Roles: who may ask, never what they may read -------------------------


@pytest.mark.parametrize("token", ["roleless-token", "staff-token"])
@pytest.mark.parametrize("path", ["/v1/answer", "/v1/answer/stream"])
async def test_a_caller_without_the_user_role_gets_403_and_nothing_runs(
    path: str, token: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    source = Source()
    workflow = Workflow()
    app = create_app(
        workflow=cast(AnswerWorkflow, workflow),
        streamer=streamer(Model(), source=source),
        authenticator=Authenticator(Verifier(TOKENS), POLICY),
    )

    async with client(app) as http:
        response = await http.post(
            path,
            json={"text": QUESTION.text},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"
    assert "www-authenticate" not in response.headers
    assert (source.readers, workflow.calls) == ([], 0)
    assert "request not permitted: ask_questions" in caplog.messages


# Every route the app serves, written out by hand, and the operation it needs;
# None marks a route anyone may call. A new route fails the first test below
# until someone decides who may call it.
ROUTES: dict[tuple[str, str], Operation | None] = {
    ("GET", "/health/live"): None,
    ("GET", "/health/ready"): None,
    ("GET", "/openapi.json"): None,
    ("GET", "/docs"): None,
    ("GET", "/docs/oauth2-redirect"): None,
    ("GET", "/redoc"): None,
    ("POST", "/v1/answer"): Operation.ASK_QUESTIONS,
    ("POST", "/v1/answer/stream"): Operation.ASK_QUESTIONS,
}
PROTECTED = [(route, operation) for route, operation in ROUTES.items() if operation]


class Counting:
    """A workflow that records that it was reached, then stops."""

    def __init__(self) -> None:
        self.calls = 0

    async def answer(self, question: Any, *, request_id: Any) -> Any:
        self.calls += 1
        raise RuntimeError("reached")


def full_app(
    source: Source, workflow: Any = None, tokens: dict[str, Any] | None = None
) -> Any:
    """The app with both answer endpoints wired, so every route is real."""
    return create_app(
        workflow=cast(AnswerWorkflow, workflow or Counting()),
        streamer=streamer(Model(), source=source),
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
    )


def test_every_route_the_app_serves_is_listed_with_its_operation() -> None:
    served: set[tuple[str, str]] = set()
    for route in full_app(Source()).routes:
        # A websocket or mounted route has no methods, and must be listed too.
        methods: set[str] = getattr(route, "methods", None) or {"ANY"}
        served |= {(method, getattr(route, "path", "?")) for method in methods}

    assert served - {("HEAD", path) for _, path in ROUTES} == set(ROUTES)


@pytest.mark.parametrize(
    "roles", ROLE_SETS, ids=lambda roles: "+".join(sorted(roles)) or "none"
)
@pytest.mark.parametrize(
    ("route", "operation"), PROTECTED, ids=[path for (_, path), _ in PROTECTED]
)
async def test_each_protected_route_admits_exactly_the_roles_that_permit_it(
    route: tuple[str, str],
    operation: Operation,
    roles: frozenset[ApplicationRole],
) -> None:
    source, workflow = Source(), Counting()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(source, workflow, {"token": token})
    method, path = route

    async with client(app) as http:
        response = await http.request(
            method,
            path,
            json={"text": QUESTION.text},
            headers={"Authorization": "Bearer token"},
        )

    allowed = bool(roles & MATRIX[operation])
    assert (response.status_code == 403) is not allowed
    assert bool(source.readers or workflow.calls) is allowed


@pytest.mark.parametrize("path", [path for (_, path), _ in PROTECTED])
async def test_the_role_is_checked_before_the_body_is_read(path: str) -> None:
    read: list[int] = []
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        read.append(1)
        return {"type": "http.request", "body": b" " * 65_536, "more_body": True}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = [(b"authorization", b"Bearer roleless-token")]
    await full_app(Source())(raw_scope(headers, path), receive, send)

    assert sent[0]["status"] == 403
    assert read == []


async def test_an_administrator_reads_exactly_what_the_same_user_reads() -> None:
    plain, admin = Source(), Source()

    await ask(app_with(plain), {"Authorization": "Bearer alice-token"})
    response = await ask(app_with(admin), {"Authorization": "Bearer admin-user-token"})

    assert response.status_code == 200
    assert admin.readers == plain.readers == [ALICE_CONTEXT]


# --- When tokens cannot be checked: 503, never a guess --------------------------------


async def test_an_unavailable_identity_provider_is_a_503_with_retry_after() -> None:
    source = Source()
    verifier = Verifier(error=IdentityProviderUnavailable("down"))

    response = await ask(
        app_with(source, verifier), {"Authorization": "Bearer alice-token"}
    )

    assert response.status_code == 503
    assert response.json()["code"] == "dependency_unavailable"
    assert response.headers["retry-after"] == "5"
    assert source.readers == []


@pytest.mark.parametrize("path", ["/v1/answer", "/v1/answer/stream"])
async def test_an_app_without_an_authenticator_answers_nobody(path: str) -> None:
    source = Source()
    app = app_with(source, authenticated=False)

    async with client(app) as http:
        response = await http.post(
            path,
            json={"text": QUESTION.text},
            headers={"Authorization": "Bearer alice-token"},
        )

    assert response.status_code == 503
    assert source.readers == []


async def test_health_checks_need_no_token() -> None:
    async with client(app_with(Source())) as http:
        live = await http.get("/health/live")
        ready = await http.get("/health/ready")

    assert (live.status_code, ready.status_code) == (200, 200)


# --- Nothing sensitive in the response or the log -------------------------------------


async def test_a_refusal_names_no_token_text_to_the_client_or_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    verifier = Verifier({MARKER: verified(sub=f"{MARKER}\n")})

    for header in (f"Bearer {MARKER}", f"Bearer {MARKER}x", f"Basic {MARKER}"):
        response = await ask(app_with(Source(), verifier), {"Authorization": header})
        assert response.status_code == 401
        assert MARKER not in response.text + str(response.headers)

    assert MARKER not in caplog.text
    reasons = [
        r.getMessage() for r in caplog.records if r.name == "knowledge_service.app"
    ]
    assert reasons == [
        "request not authenticated: subject",
        "request not authenticated: signature",
        "request not authenticated: missing",
    ]
