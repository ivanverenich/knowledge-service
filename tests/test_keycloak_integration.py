"""Login and token issuance against the local Keycloak (`make keycloak-up`).

The tests skip themselves when nothing listens on Keycloak's port or its
generated secrets are missing. A Keycloak that answers without the realm
fails them instead.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from keycloak_flow import (
    CLIENT_ID,
    ISSUER,
    REDIRECT_URI,
    SECRETS,
    LoginRefused,
    claims,
    header,
    local_secrets,
    log_in,
)

pytestmark = pytest.mark.integration

PASSWORDS = {
    "alice": "KS_ALICE_PASSWORD",
    "bob": "KS_BOB_PASSWORD",
    "carol": "KS_CAROL_PASSWORD",
}
TOKEN_ENDPOINT = f"{ISSUER}/protocol/openid-connect/token"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            found = await client.get(f"{ISSUER}/.well-known/openid-configuration")
        except httpx.TransportError:
            pytest.skip("no local Keycloak: run `make keycloak-up`")
        if not SECRETS.exists():
            pytest.skip("no local Keycloak secrets: run `make keycloak-up`")
        assert found.status_code == 200, "Keycloak answers, but without the realm"
        yield client


@pytest.fixture
def secrets() -> dict[str, str]:
    return local_secrets()


async def log_in_as(
    client: httpx.AsyncClient,
    secrets: dict[str, str],
    username: str,
    **options: Any,
) -> dict[str, Any]:
    """The claims of `username`'s access token, logged in with `options`."""
    tokens = await log_in(
        client,
        username,
        secrets[PASSWORDS[username]],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
        **options,
    )
    return claims(tokens.access_token)


async def refused(
    client: httpx.AsyncClient, secrets: dict[str, str], **options: Any
) -> LoginRefused:
    """Why alice's login with `options` was refused."""
    with pytest.raises(LoginRefused) as raised:
        await log_in_as(client, secrets, "alice", **options)
    return raised.value


# --- Success --------------------------------------------------------------------------


async def test_discovery_names_the_issuer_and_its_keys(
    client: httpx.AsyncClient,
) -> None:
    found = (await client.get(f"{ISSUER}/.well-known/openid-configuration")).json()

    assert found["issuer"] == ISSUER
    assert found["jwks_uri"] == f"{ISSUER}/protocol/openid-connect/certs"


async def test_a_demo_user_logs_in_and_gets_a_signed_token_for_the_api(
    client: httpx.AsyncClient, secrets: dict[str, str]
) -> None:
    tokens = await log_in(
        client,
        "alice",
        secrets["KS_ALICE_PASSWORD"],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
    )

    keys = (await client.get(f"{ISSUER}/protocol/openid-connect/certs")).json()
    signing = {key["kid"]: key for key in keys["keys"] if key["use"] == "sig"}
    access = header(tokens.access_token)
    assert access["alg"] == "RS256"
    assert signing[access["kid"]]["kty"] == "RSA"
    payload = claims(tokens.access_token)
    assert payload["iss"] == ISSUER
    assert payload["aud"] == "knowledge-service-api"
    assert payload["azp"] == CLIENT_ID
    assert payload["sub"] == "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01"
    assert payload["exp"] - payload["iat"] == 300
    assert claims(tokens.id_token)["aud"] == CLIENT_ID


@pytest.mark.parametrize(
    ("username", "groups", "roles"),
    [
        ("alice", ["/engineering"], {"user"}),
        ("bob", ["/finance"], {"user", "content-manager"}),
        ("carol", [], {"user", "administrator"}),
    ],
)
async def test_each_demo_user_carries_their_groups_and_roles_only(
    client: httpx.AsyncClient,
    secrets: dict[str, str],
    username: str,
    groups: list[str],
    roles: set[str],
) -> None:
    payload = await log_in_as(client, secrets, username)

    assert payload.get("groups", []) == groups
    assert set(payload["realm_access"]["roles"]) == roles


# --- The highest-risk edges: where a code goes, and what a token may claim ------------


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://localhost:3000/oauth/oidc/callback/extra",
        "http://localhost:3000/oauth/oidc/callback?next=http://evil.test",
        "http://localhost:3000.evil.test/oauth/oidc/callback",
        "http://localhost:3000@evil.test/oauth/oidc/callback",
        "http://localhost:3001/oauth/oidc/callback",
        "https://localhost:3000/oauth/oidc/callback",
        "http://evil.test/oauth/oidc/callback",
    ],
    ids=[
        "longer-path",
        "query",
        "suffix-host",
        "userinfo",
        "other-port",
        "other-scheme",
        "other-host",
    ],
)
async def test_a_code_is_never_sent_to_an_unregistered_redirect(
    client: httpx.AsyncClient, secrets: dict[str, str], redirect_uri: str
) -> None:
    refusal = await refused(client, secrets, redirect_uri=redirect_uri)

    assert (refusal.step, refusal.status) == ("authorization request", 400)
    assert refusal.reason == "Invalid parameter: redirect_uri"


@pytest.mark.parametrize(
    "scope",
    ["openid microprofile-jwt", "openid offline_access", "openid address phone"],
    ids=["roles-as-groups", "offline", "other-optional"],
)
async def test_a_client_cannot_add_scopes_that_change_the_token(
    client: httpx.AsyncClient, secrets: dict[str, str], scope: str
) -> None:
    refusal = await refused(client, secrets, scope=scope)

    assert refusal.step == "authorization request"
    assert refusal.reason.startswith("invalid_scope")


# --- Denials --------------------------------------------------------------------------


async def test_a_wrong_password_gets_the_login_form_again_and_no_code(
    client: httpx.AsyncClient, secrets: dict[str, str]
) -> None:
    refusal = await refused(
        client,
        {**secrets, "KS_ALICE_PASSWORD": secrets["KS_BOB_PASSWORD"]},
    )

    assert (refusal.step, refusal.status) == ("login form", 200)
    assert refusal.reason == "Invalid username or password."


@pytest.mark.parametrize(
    ("challenge", "reason"),
    [
        (None, "invalid_request Missing parameter: code_challenge_method"),
        (
            "plain",
            "invalid_request Invalid parameter: "
            "code challenge method is not matching the configured one",
        ),
    ],
    ids=["none", "plain"],
)
async def test_a_login_without_an_s256_challenge_is_refused(
    client: httpx.AsyncClient,
    secrets: dict[str, str],
    challenge: str | None,
    reason: str,
) -> None:
    refusal = await refused(client, secrets, challenge=challenge)

    assert (refusal.step, refusal.reason) == ("authorization request", reason)


async def test_a_code_is_not_exchanged_without_the_client_secret(
    client: httpx.AsyncClient, secrets: dict[str, str]
) -> None:
    wrong = {**secrets, "KS_OPEN_WEBUI_CLIENT_SECRET": secrets["KS_BOB_PASSWORD"]}

    refusal = await refused(client, wrong)

    assert (refusal.step, refusal.status) == ("code exchange", 401)
    assert refusal.reason.startswith("unauthorized_client")


@pytest.mark.parametrize(
    "client_id",
    [
        "open-webui",
        "admin-cli",
        "account",
        "account-console",
        "security-admin-console",
        "broker",
        "realm-management",
    ],
)
async def test_no_client_gives_a_token_for_a_password_alone(
    client: httpx.AsyncClient, secrets: dict[str, str], client_id: str
) -> None:
    data = {
        "grant_type": "password",
        "client_id": client_id,
        "username": "carol",
        "password": secrets["KS_CAROL_PASSWORD"],
    }
    # Only open-webui has a secret; the built-in clients are tried without one.
    secret = secrets["KS_OPEN_WEBUI_CLIENT_SECRET"]
    answer = await (
        client.post(TOKEN_ENDPOINT, data=data, auth=(client_id, secret))
        if client_id == CLIENT_ID
        else client.post(TOKEN_ENDPOINT, data=data)
    )

    assert answer.status_code in {400, 401}
    assert answer.json()["error"] == "unauthorized_client"
    assert "access_token" not in answer.json()


async def test_the_client_has_no_token_of_its_own(
    client: httpx.AsyncClient, secrets: dict[str, str]
) -> None:
    answer = await client.post(
        TOKEN_ENDPOINT,
        data={"grant_type": "client_credentials"},
        auth=(CLIENT_ID, secrets["KS_OPEN_WEBUI_CLIENT_SECRET"]),
    )

    assert answer.json()["error"] == "unauthorized_client"
    assert "access_token" not in answer.json()


async def test_the_implicit_flow_returns_no_token(client: httpx.AsyncClient) -> None:
    answer = await client.get(
        f"{ISSUER}/protocol/openid-connect/auth",
        params={
            "response_type": "token",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": "openid",
            "nonce": "n",
        },
    )

    location = answer.headers.get("location", "")
    assert "error=unauthorized_client" in location
    assert "access_token" not in location
