"""The whole path against the local Keycloak (`make keycloak-up`).

A demo user logs in through the login form; their access token goes to the
app, and the Evidence source sees exactly their User and Groups, whatever
Application Roles they also hold. The tests skip themselves when Keycloak or
its generated secrets are missing.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from keycloak_flow import CLIENT_ID, ISSUER, SECRETS, local_secrets, log_in
from knowledge_service.access import AuthorizationContext
from knowledge_service.app import create_app
from knowledge_service.identity.context import (
    Authenticator,
    ContextPolicy,
    group_id_for,
    user_id_for,
)
from knowledge_service.identity.oidc import OidcVerifier
from knowledge_service.roles import ApplicationRole
from test_streaming import QUESTION, Model, Source, streamer

pytestmark = pytest.mark.integration

AUDIENCE = "knowledge-service-api"
USERS: dict[str, tuple[str, list[str]]] = {
    "alice": ("0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01", ["/engineering"]),
    "bob": ("0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a02", ["/finance"]),
    "carol": ("0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a03", []),
}
PASSWORDS = {name: f"KS_{name.upper()}_PASSWORD" for name in USERS}
ROLES = {
    "alice": {ApplicationRole.USER},
    "bob": {ApplicationRole.USER, ApplicationRole.CONTENT_MANAGER},
    "carol": {ApplicationRole.USER, ApplicationRole.ADMINISTRATOR},
}


@pytest.fixture
async def keycloak() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.get(f"{ISSUER}/.well-known/openid-configuration")
        except httpx.TransportError:
            pytest.skip("no local Keycloak: run `make keycloak-up`")
        if not SECRETS.exists():
            pytest.skip("no local Keycloak secrets: run `make keycloak-up`")
        yield client


def authenticator_for(keycloak: httpx.AsyncClient) -> Authenticator:
    return Authenticator(
        OidcVerifier(ISSUER, AUDIENCE, keycloak),
        ContextPolicy(trusted_clients=frozenset({CLIENT_ID})),
    )


def app_for(keycloak: httpx.AsyncClient, source: Source) -> Any:
    return create_app(
        streamer=streamer(Model(), source=source),
        authenticator=authenticator_for(keycloak),
    )


async def ask(app: Any, token: str | None) -> httpx.Response:
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as http:
        return await http.post(
            "/v1/questions", json={"text": QUESTION.text}, headers=headers
        )


@pytest.mark.parametrize("username", list(USERS))
async def test_each_demo_user_is_the_reader_their_token_names(
    keycloak: httpx.AsyncClient, username: str
) -> None:
    secrets = local_secrets()
    tokens = await log_in(
        keycloak,
        username,
        secrets[PASSWORDS[username]],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
    )
    source = Source()
    subject, paths = USERS[username]

    response = await ask(app_for(keycloak, source), tokens.access_token)

    assert response.status_code == 200
    assert source.readers == [
        AuthorizationContext(
            user_id=user_id_for(ISSUER, subject),
            group_ids=frozenset(group_id_for(ISSUER, path) for path in paths),
        )
    ]


async def test_an_id_token_or_no_token_reads_nothing(
    keycloak: httpx.AsyncClient,
) -> None:
    secrets = local_secrets()
    tokens = await log_in(
        keycloak,
        "alice",
        secrets["KS_ALICE_PASSWORD"],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
    )
    source = Source()
    app = app_for(keycloak, source)

    assert (await ask(app, tokens.id_token)).status_code == 401
    assert (await ask(app, None)).status_code == 401
    assert source.readers == []


@pytest.mark.parametrize("username", list(USERS))
async def test_each_demo_user_has_exactly_the_roles_the_realm_gives_them(
    keycloak: httpx.AsyncClient, username: str
) -> None:
    secrets = local_secrets()
    tokens = await log_in(
        keycloak,
        username,
        secrets[PASSWORDS[username]],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
    )

    caller = await authenticator_for(keycloak).authenticate(
        [f"Bearer {tokens.access_token}"]
    )

    assert caller.roles == ROLES[username]
