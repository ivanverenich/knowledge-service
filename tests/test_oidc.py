"""`OidcVerifier` against a fake identity provider and a fake clock.

The provider is an `httpx.MockTransport` that serves a discovery document
and a JWKS the test controls; tokens are signed here with throwaway keys.
"""

import asyncio
import base64
import gzip
import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from knowledge_service.identity.oidc import (
    IdentityProviderUnavailable,
    OidcVerifier,
    Rejection,
    TokenRejected,
    VerifierPolicy,
)

ISSUER = "https://id.example.test/realms/knowledge-service"
AUDIENCE = "knowledge-service-api"
DISCOVERY = f"{ISSUER}/.well-known/openid-configuration"
JWKS = f"{ISSUER}/protocol/openid-connect/certs"
NOW = 1_800_000_000.0
MARKER = "SEEDED-SECRET-7f3a"


@pytest.fixture(scope="module")
def keys() -> dict[str, rsa.RSAPrivateKey]:
    """Three signing keys, named by the key IDs the tests use."""
    return {
        kid: rsa.generate_private_key(public_exponent=65537, key_size=2048)
        for kid in ("key-1", "key-2", "key-3")
    }


def public_jwk(kid: str, key: rsa.RSAPrivateKey, **extra: Any) -> dict[str, Any]:
    jwk = RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    return {**jwk, "kid": kid, "use": "sig", "alg": "RS256", **extra}


@dataclass
class Provider:
    """A fake provider: what it publishes, and what it was asked."""

    published: list[Any]
    discovery: dict[str, Any] = field(
        default_factory=lambda: {"issuer": ISSUER, "jwks_uri": JWKS}
    )
    down: bool = False
    requests: list[str] = field(default_factory=list[str])

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if self.down:
            return httpx.Response(503)
        if url == DISCOVERY:
            return httpx.Response(200, json=self.discovery)
        # Keys are served at any `/certs`, on any host, so a test can see
        # whether the verifier asked a host it should never ask.
        if url.endswith("/certs"):
            return httpx.Response(200, json={"keys": self.published})
        return httpx.Response(404)

    def fetches(self) -> int:
        return sum(url == JWKS for url in self.requests)


@dataclass
class Clock:
    now: float = NOW

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def provider(keys: dict[str, rsa.RSAPrivateKey]) -> Provider:
    return Provider(published=[public_jwk("key-1", keys["key-1"])])


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def verifier(provider: Provider, clock: Clock) -> OidcVerifier:
    client = httpx.AsyncClient(transport=httpx.MockTransport(provider.handle))
    return OidcVerifier(ISSUER, AUDIENCE, client, clock=clock, monotonic=clock)


def verifier_for(
    handler: Any, clock: Clock, policy: VerifierPolicy | None = None, **client: Any
) -> OidcVerifier:
    """A verifier whose provider is `handler`, on the test's clock."""
    transport = httpx.MockTransport(handler)
    return OidcVerifier(
        ISSUER,
        AUDIENCE,
        httpx.AsyncClient(transport=transport, **client),
        policy=policy,
        clock=clock,
        monotonic=clock,
    )


def sign(key: rsa.RSAPrivateKey, kid: str = "key-1", /, **claims: object) -> str:
    """A token signed with `key` under `kid`; a claim set to None is left out."""
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01",
        "iat": int(NOW),
        "exp": int(NOW) + 300,
        **claims,
    }
    payload = {name: value for name, value in payload.items() if value is not None}
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})


def segment(part: dict[str, Any]) -> str:
    """One base64url JWT segment, for tokens PyJWT refuses to make."""
    return base64.urlsafe_b64encode(json.dumps(part).encode()).rstrip(b"=").decode()


async def rejection(verifier: OidcVerifier, token: str) -> Rejection:
    with pytest.raises(TokenRejected) as raised:
        await verifier.verify(token)
    return raised.value.reason


# --- Success --------------------------------------------------------------------------


async def test_a_valid_token_returns_its_verified_claims(
    verifier: OidcVerifier, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    verified = await verifier.verify(sign(keys["key-1"], groups=["/engineering"]))

    assert verified.subject == "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01"
    assert verified.key_id == "key-1"
    assert verified.expires_at.timestamp() == NOW + 300
    assert verified.claims["groups"] == ["/engineering"]


async def test_an_audience_list_that_includes_the_api_is_accepted(
    verifier: OidcVerifier, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = sign(keys["key-1"], aud=["account", AUDIENCE])

    assert (await verifier.verify(token)).subject


async def test_keys_are_fetched_once_and_reused(
    verifier: OidcVerifier, provider: Provider, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    for _ in range(5):
        await verifier.verify(sign(keys["key-1"]))

    assert provider.requests == [DISCOVERY, JWKS]


# --- Time: expiry and clock skew ------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds_after_expiry", "accepted"),
    [(-1, True), (29, True), (30, False), (3600, False)],
)
async def test_an_expired_token_is_refused_beyond_the_allowed_skew(
    verifier: OidcVerifier,
    clock: Clock,
    keys: dict[str, rsa.RSAPrivateKey],
    seconds_after_expiry: int,
    accepted: bool,
) -> None:
    token = sign(keys["key-1"])
    clock.now = NOW + 300 + seconds_after_expiry

    if accepted:
        assert (await verifier.verify(token)).subject
    else:
        assert await rejection(verifier, token) is Rejection.EXPIRED


@pytest.mark.parametrize(("claim", "ahead"), [("iat", 31), ("nbf", 31)])
async def test_a_token_from_the_future_is_refused_beyond_the_allowed_skew(
    verifier: OidcVerifier, keys: dict[str, rsa.RSAPrivateKey], claim: str, ahead: int
) -> None:
    within = sign(keys["key-1"], **{claim: int(NOW) + 30})
    beyond = sign(keys["key-1"], **{claim: int(NOW) + ahead})

    assert (await verifier.verify(within)).subject
    assert await rejection(verifier, beyond) is Rejection.NOT_YET_VALID


@pytest.mark.parametrize(
    "value",
    ["1800000300", True, None, 1e309, 10**309, 1e18, 253_402_300_800, -1],
    ids=[
        "text",
        "bool",
        "missing",
        "infinity",
        "huge",
        "1e18",
        "year-10000",
        "negative",
    ],
)
async def test_an_expiry_that_is_not_a_number_is_refused(
    verifier: OidcVerifier, keys: dict[str, rsa.RSAPrivateKey], value: object
) -> None:
    token = sign(keys["key-1"], exp=value)

    assert await rejection(verifier, token) in {
        Rejection.MALFORMED,
        Rejection.MISSING_CLAIM,
    }


# --- Issuer, audience, and the claims every token needs -------------------------------


@pytest.mark.parametrize(
    "issuer",
    [
        "https://id.example.test/realms/master",
        f"{ISSUER}/",
        "https://evil.test/realms/knowledge-service",
    ],
)
async def test_a_token_from_another_issuer_is_refused(
    verifier: OidcVerifier, keys: dict[str, rsa.RSAPrivateKey], issuer: str
) -> None:
    assert await rejection(verifier, sign(keys["key-1"], iss=issuer)) is (
        Rejection.ISSUER
    )


@pytest.mark.parametrize(
    "audience", ["open-webui", "account", ["account", "open-webui"], [], ""]
)
async def test_a_token_for_another_audience_is_refused(
    verifier: OidcVerifier, keys: dict[str, rsa.RSAPrivateKey], audience: object
) -> None:
    assert await rejection(verifier, sign(keys["key-1"], aud=audience)) in {
        Rejection.AUDIENCE,
        Rejection.MISSING_CLAIM,
    }


@pytest.mark.parametrize("claim", ["exp", "iat", "iss", "aud", "sub"])
async def test_a_token_without_a_required_claim_is_refused(
    verifier: OidcVerifier, keys: dict[str, rsa.RSAPrivateKey], claim: str
) -> None:
    token = sign(keys["key-1"], **{claim: None})

    assert await rejection(verifier, token) is Rejection.MISSING_CLAIM


@pytest.mark.parametrize("subject", ["", 42])
async def test_a_subject_that_is_not_a_name_is_refused(
    verifier: OidcVerifier, keys: dict[str, rsa.RSAPrivateKey], subject: object
) -> None:
    assert await rejection(verifier, sign(keys["key-1"], sub=subject)) in {
        Rejection.MISSING_CLAIM,
        Rejection.MALFORMED,
    }


# --- Signature and algorithm: the highest-risk edge -----------------------------------


async def test_a_token_signed_by_a_key_the_provider_did_not_publish_is_refused(
    verifier: OidcVerifier, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    forged = sign(keys["key-2"], "key-1")

    assert await rejection(verifier, forged) is Rejection.SIGNATURE


async def test_a_changed_payload_breaks_the_signature(
    verifier: OidcVerifier, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    head, _, signature = sign(keys["key-1"]).split(".")
    other = sign(keys["key-1"], sub="carol").split(".")[1]

    assert await rejection(verifier, f"{head}.{other}.{signature}") is (
        Rejection.SIGNATURE
    )


async def test_an_unsigned_token_is_refused_before_any_key_is_fetched(
    verifier: OidcVerifier, provider: Provider
) -> None:
    header = segment({"alg": "none", "typ": "JWT", "kid": "key-1"})
    body = segment(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "iat": NOW, "exp": NOW + 300}
    )
    unsigned = f"{header}.{body}."

    assert await rejection(verifier, unsigned) is Rejection.ALGORITHM
    assert provider.requests == []


async def test_the_public_key_used_as_an_hmac_secret_is_refused(
    verifier: OidcVerifier, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    # The classic confusion: sign with HS256, using the published public key
    # as the shared secret, and hope the verifier lets the header choose.
    secret = (
        keys["key-1"]
        .public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    header = segment({"alg": "HS256", "typ": "JWT", "kid": "key-1"})
    body = segment(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "iat": NOW, "exp": NOW + 300}
    )
    mac = hmac.new(secret, f"{header}.{body}".encode(), hashlib.sha256).digest()
    forged = f"{header}.{body}.{base64.urlsafe_b64encode(mac).rstrip(b'=').decode()}"

    assert await rejection(verifier, forged) is Rejection.ALGORITHM


async def test_a_signed_token_over_the_size_limit_is_refused_unread(
    verifier: OidcVerifier, provider: Provider, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    token = sign(keys["key-1"], padding="x" * 8000)

    assert len(token) > 8192
    assert await rejection(verifier, token) is Rejection.MALFORMED
    assert provider.requests == []


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-token",
        "a.b.c",
        "a" * 8193,
        "eyJhbGciOiJSUzI1NiJ9.e30.c2ln",
    ],
    ids=["empty", "one-part", "garbage", "too-long", "no-kid"],
)
async def test_a_malformed_token_is_refused_without_a_fetch(
    verifier: OidcVerifier, provider: Provider, token: str
) -> None:
    assert await rejection(verifier, token) is Rejection.MALFORMED
    assert provider.requests == []


# --- Unknown keys and key rotation ----------------------------------------------------


async def test_a_token_with_an_unknown_key_id_is_refused(
    verifier: OidcVerifier, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    assert await rejection(verifier, sign(keys["key-2"], "key-2")) is (
        Rejection.UNKNOWN_KEY
    )


async def test_a_rotated_key_is_picked_up_without_a_restart(
    verifier: OidcVerifier,
    provider: Provider,
    clock: Clock,
    keys: dict[str, rsa.RSAPrivateKey],
) -> None:
    await verifier.verify(sign(keys["key-1"]))
    # The provider rotates: key-2 signs from now on, key-1 is withdrawn.
    provider.published = [public_jwk("key-2", keys["key-2"])]
    clock.now += 31

    assert (await verifier.verify(sign(keys["key-2"], "key-2"))).key_id == "key-2"
    assert await rejection(verifier, sign(keys["key-1"])) is Rejection.UNKNOWN_KEY


async def test_a_withdrawn_key_stops_verifying_once_the_cache_expires(
    verifier: OidcVerifier,
    provider: Provider,
    clock: Clock,
    keys: dict[str, rsa.RSAPrivateKey],
) -> None:
    await verifier.verify(sign(keys["key-1"]))
    provider.published = [public_jwk("key-2", keys["key-2"])]
    clock.now += 600
    token = sign(keys["key-1"], iat=int(clock.now), exp=int(clock.now) + 300)

    assert await rejection(verifier, token) is Rejection.UNKNOWN_KEY


async def test_unknown_key_ids_refresh_the_keys_at_most_once_per_interval(
    verifier: OidcVerifier,
    provider: Provider,
    clock: Clock,
    keys: dict[str, rsa.RSAPrivateKey],
) -> None:
    await verifier.verify(sign(keys["key-1"]))

    for number in range(50):
        await rejection(verifier, sign(keys["key-2"], f"random-{number}"))
    clock.now += 31
    await rejection(verifier, sign(keys["key-2"], "random-again"))

    assert provider.fetches() == 2


async def test_concurrent_requests_share_one_fetch(
    provider: Provider, clock: Clock, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.01)
        return provider.handle(request)

    verifier = verifier_for(slow, clock)
    token = sign(keys["key-1"])

    verified = await asyncio.gather(*(verifier.verify(token) for _ in range(20)))

    assert {token.key_id for token in verified} == {"key-1"}

    assert provider.requests == [DISCOVERY, JWKS]


async def test_only_rsa_signing_keys_are_used(
    verifier: OidcVerifier, provider: Provider, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    provider.published = [
        public_jwk("enc", keys["key-1"], use="enc", alg="RSA-OAEP"),
        public_jwk("ps", keys["key-1"], alg="PS256"),
        {"kid": "oct", "kty": "oct", "k": "c2VjcmV0", "alg": "HS256"},
        {"kid": "broken", "kty": "RSA", "n": "AQAB"},
        "not-a-key",
    ]

    for kid in ("enc", "ps", "oct", "broken"):
        assert await rejection(verifier, sign(keys["key-1"], kid)) is (
            Rejection.UNKNOWN_KEY
        )


async def test_a_key_id_published_twice_verifies_nothing(
    verifier: OidcVerifier, provider: Provider, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    provider.published = [
        public_jwk("key-1", keys["key-1"]),
        public_jwk("key-1", keys["key-2"]),
    ]

    assert await rejection(verifier, sign(keys["key-1"])) is Rejection.UNKNOWN_KEY


# --- When the provider fails: bounded, then closed ------------------------------------


async def test_cached_keys_outlive_a_failed_refresh_up_to_the_stale_limit(
    verifier: OidcVerifier,
    provider: Provider,
    clock: Clock,
    keys: dict[str, rsa.RSAPrivateKey],
) -> None:
    await verifier.verify(sign(keys["key-1"]))
    provider.down = True

    clock.now = NOW + 3599
    fresh = sign(keys["key-1"], iat=int(clock.now), exp=int(clock.now) + 300)
    assert (await verifier.verify(fresh)).key_id == "key-1"

    clock.now = NOW + 3600
    later = sign(keys["key-1"], iat=int(clock.now), exp=int(clock.now) + 300)
    with pytest.raises(IdentityProviderUnavailable):
        await verifier.verify(later)


async def test_a_provider_that_never_answered_fails_closed_and_is_not_hammered(
    verifier: OidcVerifier,
    provider: Provider,
    clock: Clock,
    keys: dict[str, rsa.RSAPrivateKey],
) -> None:
    provider.down = True

    for _ in range(10):
        with pytest.raises(IdentityProviderUnavailable):
            await verifier.verify(sign(keys["key-1"]))
    assert len(provider.requests) == 1

    provider.down = False
    clock.now += 30
    assert (await verifier.verify(sign(keys["key-1"]))).key_id == "key-1"


@pytest.mark.parametrize(
    "discovery",
    [
        {"issuer": "https://evil.test/realms/knowledge-service", "jwks_uri": JWKS},
        {"issuer": ISSUER, "jwks_uri": "https://evil.test/certs"},
        {"issuer": ISSUER},
    ],
    ids=["other-issuer", "keys-elsewhere", "no-jwks-uri"],
)
async def test_a_discovery_document_for_another_issuer_gives_no_keys(
    verifier: OidcVerifier,
    provider: Provider,
    keys: dict[str, rsa.RSAPrivateKey],
    discovery: dict[str, Any],
) -> None:
    provider.discovery = discovery

    with pytest.raises(IdentityProviderUnavailable):
        await verifier.verify(sign(keys["key-1"]))
    assert not any("evil.test" in url for url in provider.requests)


def serving(documents: dict[str, httpx.Response]) -> Any:
    """A provider handler that answers each URL with a fixed response."""

    def handle(request: httpx.Request) -> httpx.Response:
        return documents.get(str(request.url), httpx.Response(404))

    return handle


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(503, json={"keys": []}),
        httpx.Response(200, json=[]),
        httpx.Response(200, content=b"not json"),
        httpx.Response(302, headers={"Location": "https://evil.test/certs"}),
    ],
    ids=["error-status", "not-an-object", "not-json", "redirect"],
)
async def test_a_key_document_that_is_not_a_plain_json_object_gives_no_keys(
    clock: Clock, keys: dict[str, rsa.RSAPrivateKey], answer: httpx.Response
) -> None:
    verifier = verifier_for(
        serving(
            {
                DISCOVERY: httpx.Response(
                    200, json={"issuer": ISSUER, "jwks_uri": JWKS}
                ),
                JWKS: answer,
            }
        ),
        clock,
    )

    with pytest.raises(IdentityProviderUnavailable):
        await verifier.verify(sign(keys["key-1"]))


async def test_a_redirect_is_never_followed_off_the_issuers_origin(
    provider: Provider, clock: Clock, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    def moved(request: httpx.Request) -> httpx.Response:
        if str(request.url) == JWKS:
            return httpx.Response(302, headers={"Location": "https://evil.test/certs"})
        return provider.handle(request)

    # Even a client set to follow redirects must not follow one here.
    verifier = verifier_for(moved, clock, follow_redirects=True)

    with pytest.raises(IdentityProviderUnavailable):
        await verifier.verify(sign(keys["key-1"]))
    assert not any("evil.test" in url for url in provider.requests)


@pytest.mark.parametrize("pad", [70_000], ids=["over-64-KiB"])
async def test_an_oversized_key_document_is_abandoned(
    provider: Provider, clock: Clock, keys: dict[str, rsa.RSAPrivateKey], pad: int
) -> None:
    def padded(request: httpx.Request) -> httpx.Response:
        if str(request.url) == JWKS:
            return httpx.Response(
                200, json={"keys": provider.published, "padding": "x" * pad}
            )
        return provider.handle(request)

    with pytest.raises(IdentityProviderUnavailable):
        await verifier_for(padded, clock).verify(sign(keys["key-1"]))


async def test_a_compressed_key_document_is_refused(
    provider: Provider, clock: Clock, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    def compressed(request: httpx.Request) -> httpx.Response:
        if str(request.url) == JWKS:
            body = json.dumps({"keys": provider.published}).encode()
            return httpx.Response(
                200, content=gzip.compress(body), headers={"Content-Encoding": "gzip"}
            )
        return provider.handle(request)

    with pytest.raises(IdentityProviderUnavailable):
        await verifier_for(compressed, clock).verify(sign(keys["key-1"]))


async def test_a_slow_provider_is_abandoned_at_the_fetch_timeout(
    provider: Provider, clock: Clock, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return provider.handle(request)

    policy = VerifierPolicy(fetch_timeout=timedelta(seconds=0.1))

    with pytest.raises(IdentityProviderUnavailable):
        await verifier_for(slow, clock, policy).verify(sign(keys["key-1"]))


async def test_a_key_document_with_too_many_keys_gives_no_keys(
    verifier: OidcVerifier, provider: Provider, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    provider.published = [public_jwk("key-1", keys["key-1"])] + [
        public_jwk(f"extra-{n}", keys["key-2"]) for n in range(32)
    ]

    with pytest.raises(IdentityProviderUnavailable):
        await verifier.verify(sign(keys["key-1"]))


@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")
async def test_a_key_shorter_than_2048_bits_is_never_used(
    verifier: OidcVerifier, provider: Provider
) -> None:
    # Deliberately weak: the provider publishes it, and it must not verify.
    weak = rsa.generate_private_key(public_exponent=65537, key_size=1024)  # noqa: S505
    provider.published = [public_jwk("weak", weak)]

    assert await rejection(verifier, sign(weak, "weak")) is Rejection.UNKNOWN_KEY


async def test_a_jwks_uri_httpx_cannot_request_fails_closed_and_recovers(
    verifier: OidcVerifier,
    provider: Provider,
    clock: Clock,
    keys: dict[str, rsa.RSAPrivateKey],
) -> None:
    provider.discovery = {"issuer": ISSUER, "jwks_uri": f"{JWKS}\n"}

    with pytest.raises(IdentityProviderUnavailable):
        await verifier.verify(sign(keys["key-1"]))
    provider.discovery = {"issuer": ISSUER, "jwks_uri": JWKS}
    clock.now += 30

    assert (await verifier.verify(sign(keys["key-1"]))).key_id == "key-1"


async def test_a_jwks_uri_that_fails_is_discovered_again(
    verifier: OidcVerifier,
    provider: Provider,
    clock: Clock,
    keys: dict[str, rsa.RSAPrivateKey],
) -> None:
    provider.discovery = {"issuer": ISSUER, "jwks_uri": f"{ISSUER}/moved"}

    with pytest.raises(IdentityProviderUnavailable):
        await verifier.verify(sign(keys["key-1"]))
    provider.discovery = {"issuer": ISSUER, "jwks_uri": JWKS}
    clock.now += 30

    assert (await verifier.verify(sign(keys["key-1"]))).key_id == "key-1"


async def test_keys_that_move_are_found_through_discovery_again(
    clock: Clock, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    published = {JWKS: [public_jwk("key-1", keys["key-1"])]}
    jwks_uri = {"jwks_uri": JWKS}

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == DISCOVERY:
            return httpx.Response(200, json={"issuer": ISSUER, **jwks_uri})
        if url in published:
            return httpx.Response(200, json={"keys": published[url]})
        return httpx.Response(404)

    verifier = verifier_for(handle, clock)
    await verifier.verify(sign(keys["key-1"]))
    # The provider moves its keys to a new address and rotates to key-2.
    moved = f"{ISSUER}/keys-v2/certs"
    published = {moved: [public_jwk("key-2", keys["key-2"])]}
    jwks_uri["jwks_uri"] = moved
    clock.now += 600
    token = sign(keys["key-2"], "key-2", iat=int(clock.now), exp=int(clock.now) + 300)

    # The old address fails once; the next refresh asks discovery again.
    assert await rejection(verifier, token) is Rejection.UNKNOWN_KEY
    clock.now += 30
    assert (await verifier.verify(token)).key_id == "key-2"


# --- The key cache under clocks that jump and callers that leave ----------------------


async def test_a_wall_clock_stepping_back_neither_keeps_nor_hides_keys(
    provider: Provider, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    wall, steady = Clock(), Clock(now=0.0)
    client = httpx.AsyncClient(transport=httpx.MockTransport(provider.handle))
    verifier = OidcVerifier(ISSUER, AUDIENCE, client, clock=wall, monotonic=steady)
    await verifier.verify(sign(keys["key-1"]))
    provider.published = [public_jwk("key-2", keys["key-2"])]

    wall.now -= 7200
    steady.now += 600
    issued = {"iat": int(wall.now), "exp": int(wall.now) + 300}

    assert (await verifier.verify(sign(keys["key-2"], "key-2", **issued))).key_id
    assert await rejection(verifier, sign(keys["key-1"], **issued)) is (
        Rejection.UNKNOWN_KEY
    )


async def test_a_caller_cancelled_mid_refresh_does_not_stall_the_others(
    provider: Provider, clock: Clock, keys: dict[str, rsa.RSAPrivateKey]
) -> None:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return provider.handle(request)

    verifier = verifier_for(slow, clock)
    token = sign(keys["key-1"])
    first = asyncio.create_task(verifier.verify(token))
    await asyncio.sleep(0.05)
    waiting = [asyncio.create_task(verifier.verify(token)) for _ in range(3)]
    first.cancel()

    verified = await asyncio.gather(*waiting)

    assert {token.key_id for token in verified} == {"key-1"}
    assert provider.requests == [DISCOVERY, JWKS]


@pytest.mark.parametrize(
    "settings",
    [
        {"leeway": timedelta(minutes=6)},
        {"leeway": timedelta(seconds=-1)},
        {"refresh_interval": timedelta(0)},
        {"refresh_interval": timedelta(hours=2), "stale_limit": timedelta(hours=3)},
        {"keys_max_age": timedelta(minutes=10), "stale_limit": timedelta(minutes=10)},
        {"max_token_bytes": 0},
    ],
)
def test_a_policy_that_could_fail_closed_while_the_provider_is_up_is_refused(
    settings: dict[str, Any],
) -> None:
    with pytest.raises(ValueError, match="must"):
        VerifierPolicy(**settings)


# --- Nothing sensitive leaves the verifier --------------------------------------------


async def test_no_token_or_claim_text_reaches_errors_or_logs(
    verifier: OidcVerifier,
    provider: Provider,
    keys: dict[str, rsa.RSAPrivateKey],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="knowledge_service")
    tokens = [
        sign(keys["key-1"], sub=MARKER, aud=MARKER),
        sign(keys["key-1"], iss=MARKER),
        sign(keys["key-2"], MARKER),
        sign(keys["key-2"], groups=[MARKER]),
        f"{MARKER}.{MARKER}.{MARKER}",
    ]
    crit = segment({"alg": "RS256", "kid": "key-1", "crit": [MARKER]})
    tokens.append(f"{crit}.{segment({'sub': MARKER})}.c2ln")
    errors: list[BaseException] = []
    for token in tokens:
        with pytest.raises(TokenRejected) as raised:
            await verifier.verify(token)
        errors.append(raised.value)

    provider.discovery = {"issuer": ISSUER, "jwks_uri": f"{ISSUER}/{MARKER}"}
    with pytest.raises(IdentityProviderUnavailable):
        await verifier_for(provider.handle, Clock()).verify(sign(keys["key-1"]))

    seen = " ".join(
        f"{error!s} {error!r} {error.__context__!r} {error.__cause__!r}"
        for error in errors
    )
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("knowledge")]
    assert MARKER not in seen + " ".join(ours)
    assert ours == ["identity provider keys not refreshed: ValueError"]
