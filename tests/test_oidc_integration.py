"""`OidcVerifier` against the local Keycloak (`make keycloak-up`).

The tests skip themselves when nothing listens on Keycloak's port or its
generated secrets are missing.
"""

import time
from collections.abc import AsyncIterator

import httpx
import pytest

from keycloak_flow import ISSUER, SECRETS, Tokens, local_secrets, log_in
from knowledge_service.identity.oidc import (
    IdentityProviderUnavailable,
    OidcVerifier,
    Rejection,
    TokenRejected,
)

pytestmark = pytest.mark.integration

AUDIENCE = "knowledge-service-api"
ALICE = "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.get(f"{ISSUER}/.well-known/openid-configuration")
        except httpx.TransportError:
            pytest.skip("no local Keycloak: run `make keycloak-up`")
        if not SECRETS.exists():
            pytest.skip("no local Keycloak secrets: run `make keycloak-up`")
        yield client


@pytest.fixture
async def alice(client: httpx.AsyncClient) -> Tokens:
    secrets = local_secrets()
    return await log_in(
        client,
        "alice",
        secrets["KS_ALICE_PASSWORD"],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
    )


async def test_a_token_keycloak_signed_for_the_api_is_verified(
    client: httpx.AsyncClient, alice: Tokens
) -> None:
    verified = await OidcVerifier(ISSUER, AUDIENCE, client).verify(alice.access_token)

    assert verified.subject == ALICE
    assert verified.claims["groups"] == ["/engineering"]


async def test_the_id_token_is_not_an_access_token_for_the_api(
    client: httpx.AsyncClient, alice: Tokens
) -> None:
    with pytest.raises(TokenRejected) as raised:
        await OidcVerifier(ISSUER, AUDIENCE, client).verify(alice.id_token)

    assert raised.value.reason is Rejection.AUDIENCE


async def test_a_keycloak_token_is_refused_once_it_expires(
    client: httpx.AsyncClient, alice: Tokens
) -> None:
    later = OidcVerifier(ISSUER, AUDIENCE, client, clock=lambda: time.time() + 331)

    with pytest.raises(TokenRejected) as raised:
        await later.verify(alice.access_token)

    assert raised.value.reason is Rejection.EXPIRED


async def test_a_changed_keycloak_token_is_refused(
    client: httpx.AsyncClient, alice: Tokens
) -> None:
    head, payload, signature = alice.access_token.split(".")
    flipped = signature[:-2] + ("A" if signature[-2] != "A" else "B") + signature[-1]

    with pytest.raises(TokenRejected) as raised:
        await OidcVerifier(ISSUER, AUDIENCE, client).verify(
            f"{head}.{payload}.{flipped}"
        )

    assert raised.value.reason is Rejection.SIGNATURE


async def test_another_realm_on_the_same_server_is_another_issuer(
    client: httpx.AsyncClient, alice: Tokens
) -> None:
    master = ISSUER.replace("knowledge-service", "master")

    with pytest.raises(TokenRejected) as raised:
        await OidcVerifier(master, AUDIENCE, client).verify(alice.access_token)

    # The master realm's keys never signed it; its kid is unknown there.
    assert raised.value.reason is Rejection.UNKNOWN_KEY


async def test_a_misspelled_issuer_finds_no_keys(
    client: httpx.AsyncClient, alice: Tokens
) -> None:
    with pytest.raises(IdentityProviderUnavailable):
        await OidcVerifier(f"{ISSUER}x", AUDIENCE, client).verify(alice.access_token)
