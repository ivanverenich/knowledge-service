"""The OpenAI-compatible model list with real Keycloak tokens.

A demo user logs in through Keycloak's login form, as Open WebUI's users
will, and OpenAI's own client lists the models with that access token. A
token with one character changed is refused in OpenAI's error shape. The
tests skip themselves when Keycloak or its secrets are absent.
"""

from collections.abc import AsyncIterator

import httpx
import httpx2
import openai
import pytest

from keycloak_flow import CLIENT_ID, ISSUER, SECRETS, local_secrets, log_in
from knowledge_service.app import create_app
from knowledge_service.identity.context import Authenticator, ContextPolicy
from knowledge_service.identity.oidc import OidcVerifier

pytestmark = pytest.mark.integration

AUDIENCE = "knowledge-service-api"
PASSWORDS = {name: f"KS_{name.upper()}_PASSWORD" for name in ("alice", "bob", "carol")}


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


async def access_token(keycloak: httpx.AsyncClient, username: str) -> str:
    secrets = local_secrets()
    tokens = await log_in(
        keycloak,
        username,
        secrets[PASSWORDS[username]],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
    )
    return tokens.access_token


def sdk(keycloak: httpx.AsyncClient, token: str) -> openai.AsyncOpenAI:
    app = create_app(
        authenticator=Authenticator(
            OidcVerifier(ISSUER, AUDIENCE, keycloak),
            ContextPolicy(trusted_clients=frozenset({CLIENT_ID})),
        )
    )
    return openai.AsyncOpenAI(
        base_url="http://t/openai/v1",
        api_key=token,
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)),
    )


@pytest.mark.parametrize("username", list(PASSWORDS))
async def test_each_demo_user_sees_the_knowledge_model(
    keycloak: httpx.AsyncClient, username: str
) -> None:
    token = await access_token(keycloak, username)

    page = await sdk(keycloak, token).models.list()

    assert [model.id for model in page.data] == ["knowledge-base"]


async def test_a_token_with_one_character_changed_is_refused(
    keycloak: httpx.AsyncClient,
) -> None:
    token = await access_token(keycloak, "alice")
    # The last character of the signature, so the token still parses.
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(openai.AuthenticationError) as raised:
        await sdk(keycloak, tampered).models.list()

    assert raised.value.code == "unauthenticated"
