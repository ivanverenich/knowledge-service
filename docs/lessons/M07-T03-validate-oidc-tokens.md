# M07-T03 — Validate OIDC tokens

Task: [M07-T03 — Validate OIDC tokens](../curriculum/milestones/M07-identity-and-authorization/M07-T03-validate-oidc-tokens.md)

## What you will do

This lesson writes the check every request will pass through: given an access token, decide whether Keycloak really signed it for this API and whether it is still valid. In M07-T02 Keycloak learned to issue tokens; nothing in the service can tell a real token from a forged one yet. At the end you have one small verifier that M07-T04 will put in front of the endpoints.

- **Signature:** only `RS256`, and only with a key Keycloak publishes. A token signed with any other key or algorithm is refused.
- **Who and for whom:** the issuer must be exactly our realm, and the audience must name `knowledge-service-api`.
- **Time:** an expired token, or one issued in the future, is refused, with 30 seconds of tolerance for clocks that disagree a little.
- **Keys from the network, cached and bounded:** the verifier fetches Keycloak's public keys and reuses them. A new key is picked up without a restart. Hostile requests cannot make it flood Keycloak, and a Keycloak outage cannot make it accept tokens it cannot check.

| The task asks for | Where it happens |
|---|---|
| Verify issuer, audience, signature, algorithm, expiry, and clock skew | Step 3: `src/knowledge_service/identity/oidc.py` |
| Cached discovery/JWKS with bounded refresh failure behavior | Step 3: the key cache in the same file |
| Tests: valid, expired, wrong audience, wrong issuer, unknown key, key rotation | Step 4: `tests/test_oidc.py` |
| The verifier against the real Keycloak | Step 5: `tests/test_oidc_integration.py` |
| Update only what the change made inaccurate | Step 7: ADR 0013, `docs/architecture.md`, `docs/threat-model.md` |

This lesson includes coding: Steps 2 to 5 write code and tests. Steps 1, 6, and 7 do not, though Step 7 edits three documents.

Terms used in this lesson:

- **JWT:** a token in three base64 parts, `header.payload.signature`. Anyone can read the first two; only the signature proves who made them.
- **Claim:** one field of the payload: `iss` (who issued it), `aud` (who it is for), `sub` (the user's ID), `exp` (when it expires), `iat` (when it was issued), `nbf` (not valid before).
- **RS256:** an RSA signature. Keycloak signs with a private key; anyone checks with the public key.
- **JWKS:** the list of public keys Keycloak publishes, each with an ID, the `kid`. A token's header names the `kid` that signed it.
- **Discovery:** a document at `/.well-known/openid-configuration` that names the issuer and where its JWKS lives.
- **Clock skew (leeway):** a small margin for two machines whose clocks differ by a few seconds.
- **Key rotation:** the provider starts signing with a new key and later withdraws the old one.
- **Fail closed:** when the check cannot be done, refuse, never allow.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **A library for the cryptography, our code for the rules.** PyJWT (`pyjwt[crypto]`) checks the signature, the issuer, and the audience. The verifier checks the times itself against an injected clock, so tests can move time without sleeping. Keycloak's own docs recommend a standard library over hand-written signature code.
- **The algorithm comes from us, not from the token.** The header is read first, and anything but `RS256` is refused before any key is fetched. That stops the two classic forgeries: `alg: none` (no signature at all), and `HS256` signed with the public key as if it were a shared password.
- **A new package, `identity`.** Checking who calls is the seam to an external system, the identity provider, and fits none of the existing packages. ADR 0013 asks for a reason for every new package, so Step 7 records it. M07-T04 will add the Authorization Context beside the verifier.
- **Two kinds of failure.** `TokenRejected` with one `Rejection` reason means "this token is bad", which becomes a 401 in M07-T04. `IdentityProviderUnavailable` means "we cannot check tokens right now", which becomes a 503. Neither message holds any token or claim text.
- **The key cache.**
  - Keys are fetched once, then reused for 10 minutes.
  - A token with an unknown `kid` makes the verifier fetch again, at most once every 30 seconds. That is how a rotation is picked up, and why a flood of random `kid`s costs Keycloak one request per 30 seconds.
  - When fetches fail, the cached keys keep working for at most an hour after the last success. After that, every token is refused as unavailable.
  - The cache is timed with a monotonic clock, one that never jumps back.
- **Bounded fetches.**
  - Each fetch has 5 seconds and 64 KiB.
  - A fetch never follows a redirect and refuses compressed bodies.
  - The JWKS address must be on the issuer's own host.
  - Only RSA signing keys of at least 2048 bits are kept, and a `kid` listed twice is dropped.
- **What the review found.** An independent adversarial review fuzzed thousands of hostile tokens and key documents, tried every forgery above, and attacked the cache with races, cancellations, and clock jumps. Its findings, all fixed; each has a test that fails on the first version:
  - A `jwks_uri` with a stray control character raised an untyped error, and the bad address stayed cached forever. Now it fails closed as unavailable, and the next refresh asks discovery again.
  - A client set to follow redirects would follow one to another host and trust keys from there. Now no fetch follows a redirect.
  - The cache followed the wall clock, so a clock stepping back kept withdrawn keys and refused new ones. Now the cache uses a monotonic clock.
  - Cancelling the request that was fetching keys made every request waiting on it fail for 30 seconds. Now the fetch runs as one shared task that a cancelled caller cannot stop.
  - A policy could be configured to refuse every token while Keycloak was healthy. Now such a policy is refused when it is created.
  - A signed token with a huge or out-of-range time claim crashed with an untyped error. Now it is `MALFORMED`.
  - A compressed key document could expand in memory before the size check. Now compressed documents are refused.
  - A token was accepted at the exact second its tolerance ended. Now that second is already expired.
  - A suppressed library error carrying token text was still chained to the rejection. Now the rejection is raised with nothing chained.
  - The review also showed thirteen one-line breaks of the module that no test noticed: removing the size limit, the timeout, the origin check, and others. Each now has a test that fails when the line is removed.
- **Known limits.** No endpoint calls the verifier yet; M07-T04 does. The verifier sets no maximum token lifetime and does not check a token's type: a Keycloak ID token is refused only because its audience is the client, not the API. The issuer `http://localhost:8180` still cannot be reached from a container (M09-T08, M12-T03).

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before you add anything, write down what must be true when the lesson is done, and what you expect to happen. Step 6 checks the predictions.

Create `docs/evidence/M07-T03-validate-oidc-tokens.md`:

```markdown
# M07-T03 — Validate OIDC tokens evidence

## Invariant

`OidcVerifier.verify` returns claims only for a token signed with RS256 by a
key the configured issuer publishes in its JWKS, whose `iss` is exactly that
issuer, whose `aud` names `knowledge-service-api`, and whose `exp`, `iat`,
and `nbf` hold within 30 seconds of skew. Every other token raises
`TokenRejected` with one reason. When no usable keys are cached and they
cannot be fetched, it raises `IdentityProviderUnavailable`: it never accepts
a token it could not check. Keys are fetched at most once per refresh
interval, and cached keys verify for at most the stale limit after the last
successful fetch. No error or log holds token or claim text.

## Expected failing check

Before the change, `uv run pytest tests/test_oidc.py -q` fails with
`ModuleNotFoundError: No module named 'knowledge_service.identity'`.

## Predictions

- What a token whose header says `"alg": "none"` gets, and whether the verifier fetches any keys first:
- After Keycloak rotates to a new key, how many fetches it takes before a token signed with the new key verifies:
- What happens to a valid token when Keycloak has been down for 10 minutes, and for 2 hours:
- Which test fails if the fetch is allowed to follow redirects:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: the library and the package

**In plain words:** this step adds the library that knows how to check RSA signatures, and an empty home for identity code. Nothing runs yet; the next step fills it.

**Action 1:** add PyJWT with its cryptography extra:

```bash
uv add "pyjwt[crypto]>=2.15.1,<3"
```

**Action 2:** make the package, with a one-line docstring like the other packages.

Create `src/knowledge_service/identity/__init__.py`:

```python
"""Who is calling: verified identity from the OIDC provider."""
```

**Check:** `uv run python -c "import jwt, knowledge_service.identity; print(jwt.__version__)"` prints `2.15.1` or a later 2.x version.

### Step 3 — Coding step: the verifier

**In plain words:** this file is the check itself. Given a token, it reads the header, refuses any algorithm but RS256, finds the public key the header names (fetching Keycloak's keys when needed), checks the signature, the issuer, and the audience, and then checks the times against the clock. It returns the user's ID and claims, or says exactly why the token was refused. When it cannot get Keycloak's keys at all, it says so instead of guessing.

| Part | What it does |
|---|---|
| `Rejection`, `TokenRejected` | One named reason per refusal; the message is only the reason |
| `IdentityProviderUnavailable` | No usable keys: neither cached nor fetchable |
| `VerifiedToken` | `subject`, `key_id`, `expires_at`, and the read-only claims |
| `VerifierPolicy` | Skew, cache ages, limits; refuses settings that would fail closed while Keycloak is up |
| `verify` | Header, key, signature and claims, then times |
| `_key` | A cached key, or one shared refresh when the `kid` is unknown or the cache is old |
| `_refresh`, `_fetch` | Discovery, then JWKS; bounded in time and size, no redirects, no compression |
| `_signing_keys` | Keeps RSA signing keys of 2048 bits or more; drops a `kid` listed twice |

Create `src/knowledge_service/identity/oidc.py`:

```python
"""Verify the access tokens an OIDC identity provider signs.

`OidcVerifier.verify` accepts a token only when it is signed with RS256 by
a key the provider publishes, names the configured issuer and audience,
and is inside its validity window give or take a small clock skew. It
returns the verified claims or raises `TokenRejected` with one reason;
when the provider's keys cannot be had, it raises
`IdentityProviderUnavailable` instead, so a caller can tell "this token is
bad" from "we cannot check tokens right now".

The provider's discovery document and keys (JWKS) are cached. An unknown
key ID refreshes them at most once per `refresh_interval`, so random key
IDs cannot flood the provider; and a failed refresh keeps the cached keys
only up to `stale_limit` after their last successful fetch. Errors and
logs name a reason, never the token or its claims.
"""

import asyncio
import json
import logging
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

logger = logging.getLogger(__name__)

ALGORITHM = "RS256"
MIN_KEY_BITS = 2048
# The last second `datetime` can represent: 9999-12-31T23:59:59Z.
LAST_TIMESTAMP = 253_402_300_799


class Rejection(StrEnum):
    """Why a token was refused."""

    MALFORMED = "malformed"
    ALGORITHM = "algorithm"
    UNKNOWN_KEY = "unknown_key"
    SIGNATURE = "signature"
    ISSUER = "issuer"
    AUDIENCE = "audience"
    MISSING_CLAIM = "missing_claim"
    EXPIRED = "expired"
    NOT_YET_VALID = "not_yet_valid"


class TokenRejected(Exception):
    """The token is not a valid access token for this service."""

    def __init__(self, reason: Rejection) -> None:
        super().__init__(f"token rejected: {reason}")
        self.reason = reason


class IdentityProviderUnavailable(Exception):
    """The provider's keys could not be fetched, and no usable copy is cached."""


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    """The claims of a token whose signature, issuer, audience, and times hold."""

    subject: str
    key_id: str
    expires_at: datetime
    claims: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class VerifierPolicy:
    """How much clock skew is tolerated, and how the key cache behaves.

    `keys_max_age` is how long fetched keys are used before a refresh;
    `refresh_interval` is the least time between two fetches, whatever
    asks for them; `stale_limit` is how long after the last successful
    fetch cached keys still verify while refreshes fail.
    """

    leeway: timedelta = timedelta(seconds=30)
    keys_max_age: timedelta = timedelta(minutes=10)
    refresh_interval: timedelta = timedelta(seconds=30)
    stale_limit: timedelta = timedelta(hours=1)
    fetch_timeout: timedelta = timedelta(seconds=5)
    max_token_bytes: int = 8192
    max_document_bytes: int = 65_536
    max_keys: int = 32

    def __post_init__(self) -> None:
        if not timedelta(0) <= self.leeway <= timedelta(minutes=5):
            raise ValueError("leeway must be between 0 and 5 minutes")
        for name in ("keys_max_age", "refresh_interval", "fetch_timeout"):
            if getattr(self, name) <= timedelta(0):
                raise ValueError(f"{name} must be positive")
        if self.refresh_interval >= self.keys_max_age:
            raise ValueError("refresh_interval must be shorter than keys_max_age")
        # Keys due for a refresh must get at least one try before they go stale.
        if self.keys_max_age + self.refresh_interval > self.stale_limit:
            raise ValueError("stale_limit must leave room for a refresh")
        for name in ("max_token_bytes", "max_document_bytes", "max_keys"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


class OidcVerifier:
    """Verifies access tokens against one issuer and one audience.

    `clock` gives the time that token claims are checked against;
    `monotonic` times the key cache, so a wall clock that jumps cannot
    keep withdrawn keys or hold back new ones.
    """

    def __init__(
        self,
        issuer: str,
        audience: str,
        client: httpx.AsyncClient,
        *,
        policy: VerifierPolicy | None = None,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not issuer or not audience:
            raise ValueError("an issuer and an audience are required")
        self._issuer = issuer
        self._audience = audience
        self._client = client
        self._policy = policy or VerifierPolicy()
        self._clock = clock
        self._monotonic = monotonic
        self._jwks_uri: str | None = None
        self._keys: dict[str, jwt.PyJWK] | None = None
        self._fetched_at = -math.inf
        self._attempted_at = -math.inf
        self._refreshing: asyncio.Task[None] | None = None

    async def verify(self, token: str) -> VerifiedToken:
        """The verified claims of `token`, or `TokenRejected` with the reason."""
        key_id = self._key_id(token)
        key = await self._key(key_id)
        payload = self._decode(token, key)
        now = self._clock()
        leeway = self._policy.leeway.total_seconds()
        expires = _time_claim(payload, "exp")
        if now >= expires + leeway:
            raise TokenRejected(Rejection.EXPIRED)
        if _time_claim(payload, "iat") > now + leeway:
            raise TokenRejected(Rejection.NOT_YET_VALID)
        if "nbf" in payload and _time_claim(payload, "nbf") > now + leeway:
            raise TokenRejected(Rejection.NOT_YET_VALID)
        subject = payload["sub"]
        if not isinstance(subject, str) or not subject:
            raise TokenRejected(Rejection.MISSING_CLAIM)
        return VerifiedToken(
            subject=subject,
            key_id=key_id,
            expires_at=datetime.fromtimestamp(expires, UTC),
            claims=MappingProxyType(payload),
        )

    def _key_id(self, token: str) -> str:
        """The key ID from a well-formed header that asks for RS256."""
        if len(token) > self._policy.max_token_bytes:
            raise TokenRejected(Rejection.MALFORMED)
        try:
            found = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            found = None
        # Raised outside `except`, so no PyJWT error with token text is chained.
        if found is None:
            raise TokenRejected(Rejection.MALFORMED)
        if found.get("alg") != ALGORITHM:
            raise TokenRejected(Rejection.ALGORITHM)
        key_id = found.get("kid")
        if not isinstance(key_id, str) or not key_id:
            raise TokenRejected(Rejection.MALFORMED)
        return key_id

    def _decode(self, token: str, key: jwt.PyJWK) -> dict[str, Any]:
        """The payload, once the signature, issuer, and audience hold."""
        try:
            return jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": ["exp", "iat", "iss", "aud", "sub"],
                    # The times are checked against the injected clock.
                    "verify_exp": False,
                    "verify_nbf": False,
                    "verify_iat": False,
                    "enforce_minimum_key_length": True,
                },
            )
        except jwt.PyJWTError as error:
            reason = _rejection(error)
        raise TokenRejected(reason)

    async def _key(self, key_id: str) -> jwt.PyJWK:
        """The signing key named `key_id`, refreshing the cache if allowed."""
        age = self._monotonic() - self._fetched_at
        fresh = age < self._policy.keys_max_age.total_seconds()
        if self._keys is not None and fresh and key_id in self._keys:
            return self._keys[key_id]
        if self._refreshing is None and self._may_refresh():
            self._refreshing = asyncio.create_task(self._refresh())
        if self._refreshing is not None:
            # Shielded: a caller that is cancelled leaves the refresh running
            # for everyone else waiting on it.
            await asyncio.shield(self._refreshing)
        keys = self._usable_keys()
        if key_id not in keys:
            raise TokenRejected(Rejection.UNKNOWN_KEY)
        return keys[key_id]

    def _may_refresh(self) -> bool:
        waited = self._monotonic() - self._attempted_at
        return waited >= self._policy.refresh_interval.total_seconds()

    def _usable_keys(self) -> dict[str, jwt.PyJWK]:
        """The cached keys, unless they are older than the stale limit."""
        age = self._monotonic() - self._fetched_at
        if self._keys is None or age >= self._policy.stale_limit.total_seconds():
            raise IdentityProviderUnavailable("no usable signing keys")
        return self._keys

    async def _refresh(self) -> None:
        self._attempted_at = self._monotonic()
        try:
            jwks_uri = self._jwks_uri or self._jwks_uri_from(
                await self._fetch(f"{self._issuer}/.well-known/openid-configuration")
            )
            keys = self._signing_keys(await self._fetch(jwks_uri))
        except Exception as error:
            # Discovery is fetched again next time, in case it moved. Only
            # the error's type is logged: its text may quote the provider.
            self._jwks_uri = None
            logger.warning(
                "identity provider keys not refreshed: %s", type(error).__name__
            )
            return
        finally:
            self._refreshing = None
        self._jwks_uri = jwks_uri
        self._keys = keys
        self._fetched_at = self._monotonic()

    async def _fetch(self, url: str) -> dict[str, Any]:
        """A JSON object from `url`, within the time and size limits."""
        limit = self._policy.max_document_bytes
        body = bytearray()
        async with (
            asyncio.timeout(self._policy.fetch_timeout.total_seconds()),
            self._client.stream(
                "GET",
                url,
                headers={"Accept-Encoding": "identity"},
                # A redirect could lead off the issuer's origin.
                follow_redirects=False,
            ) as response,
        ):
            if response.status_code != 200:
                raise ValueError(f"status {response.status_code}")
            # Refused before reading, so nothing is ever decompressed.
            if response.headers.get("content-encoding", "identity") != "identity":
                raise ValueError("compressed document")
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    raise ValueError("document too large")
        document = json.loads(body)
        if not isinstance(document, dict):
            raise ValueError("document is not a JSON object")
        return cast("dict[str, Any]", document)

    def _jwks_uri_from(self, discovery: dict[str, Any]) -> str:
        """The JWKS URI, from a discovery document for this exact issuer."""
        if discovery.get("issuer") != self._issuer:
            raise ValueError("discovery names another issuer")
        uri = discovery.get("jwks_uri")
        if not isinstance(uri, str) or _origin(uri) != _origin(self._issuer):
            raise ValueError("jwks_uri is not on the issuer's origin")
        httpx.URL(uri)  # Raises on a URL httpx cannot request.
        return uri

    def _signing_keys(self, document: dict[str, Any]) -> dict[str, jwt.PyJWK]:
        """The RSA signing keys by key ID; a key ID listed twice is dropped."""
        listed = document.get("keys")
        if not isinstance(listed, list):
            raise ValueError("JWKS has no key list")
        entries = cast("list[object]", listed)
        if len(entries) > self._policy.max_keys:
            raise ValueError("JWKS lists too many keys")
        keys: dict[str, jwt.PyJWK] = {}
        repeated: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            data = cast("dict[str, Any]", entry)
            key_id = data.get("kid")
            if (
                not isinstance(key_id, str)
                or not key_id
                or data.get("kty") != "RSA"
                or data.get("use", "sig") != "sig"
                or data.get("alg", ALGORITHM) != ALGORITHM
            ):
                continue
            try:
                key = jwt.PyJWK(data, algorithm=ALGORITHM)
            except jwt.PyJWTError:
                continue
            public = cast("object", key.key)
            if not isinstance(public, RSAPublicKey) or public.key_size < MIN_KEY_BITS:
                continue
            if key_id in keys:
                repeated.add(key_id)
            keys[key_id] = key
        return {key_id: key for key_id, key in keys.items() if key_id not in repeated}


def _rejection(error: jwt.PyJWTError) -> Rejection:
    """The reason for a PyJWT error; anything unexpected is malformed."""
    match error:
        case jwt.InvalidSignatureError():
            return Rejection.SIGNATURE
        case jwt.InvalidAlgorithmError():
            return Rejection.ALGORITHM
        case jwt.InvalidIssuerError():
            return Rejection.ISSUER
        case jwt.InvalidAudienceError():
            return Rejection.AUDIENCE
        case jwt.MissingRequiredClaimError():
            return Rejection.MISSING_CLAIM
        case _:
            return Rejection.MALFORMED


def _time_claim(payload: Mapping[str, Any], name: str) -> float:
    """A time claim as seconds since the epoch, within what `datetime` holds."""
    value = payload[name]
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TokenRejected(Rejection.MALFORMED)
    # A comparison, not float(): a huge integer must not overflow, and NaN
    # fails both bounds.
    if not 0 <= value <= LAST_TIMESTAMP:
        raise TokenRejected(Rejection.MALFORMED)
    return float(value)


def _origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return parts.scheme, parts.netloc
```

Three details are easy to miss:

- `_decode` saves the reason and raises `TokenRejected` after the `except` block. Raising inside it would chain PyJWT's error, whose text can quote the token, to the rejection.
- `_refresh` runs as one task, and every caller awaits it through `asyncio.shield`. A caller whose request is cancelled stops waiting, but the fetch carries on for the others.
- `_time_claim` compares the value with the bounds before converting it. A huge integer such as `10**309` cannot overflow, and `NaN` fails both comparisons.

**Check:** `uv run pyright src/knowledge_service/identity` reports `0 errors`.

### Step 4 — Coding step: unit tests

**In plain words:** these tests run the verifier against a fake Keycloak that lives inside the test, and a fake clock. Each test makes the fake publish some keys, signs a token with keys made in the test, and checks what the verifier says. That covers every acceptance case without a network or real waiting.

| Group | What it proves |
|---|---|
| Success | A valid token gives its claims; an audience list containing the API is fine; keys are fetched once |
| Time | Expired beyond 30 seconds, issued in the future, and time claims that are not sane numbers are refused |
| Issuer, audience, required claims | Another issuer, another audience, or a missing claim is refused |
| Signature and algorithm | Another key, a changed payload, `none`, the HS256 confusion, and malformed or oversized tokens are refused, mostly before any fetch |
| Unknown keys and rotation | An unknown `kid` is refused; a rotated key verifies without a restart; random `kid`s cost one fetch per interval; concurrent callers share one fetch; only RSA signing keys of 2048 bits or more count |
| Provider failures | Stale keys work up to the limit and then fail closed; a provider that never answered is not hammered; foreign discovery, redirects, errors, oversized, compressed, slow, and moved key documents |
| Cache under stress | A wall clock stepping back, and a caller cancelled mid-fetch |
| No leaks | A planted marker never appears in an error, its chained errors, or the module's logs |

Create `tests/test_oidc.py`:

```python
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
```

**Check:** run the narrow test first:

```bash
uv run pytest tests/test_oidc.py -q
```

Expected: `76 passed`.

### Step 5 — Coding step: the verifier against the real Keycloak

**In plain words:** these tests log alice in through the real Keycloak with the helper from M07-T02 and hand her token to the verifier. They prove that the fake in Step 4 behaves like the real thing: Keycloak's real tokens verify, and its other tokens are refused for the right reason.

| Test | Real token | Expected |
|---|---|---|
| `test_a_token_keycloak_signed_for_the_api_is_verified` | Alice's access token | Verified, her fixed ID and `/engineering` |
| `test_the_id_token_is_not_an_access_token_for_the_api` | Alice's ID token | `AUDIENCE` |
| `test_a_keycloak_token_is_refused_once_it_expires` | Access token, clock 331 seconds ahead | `EXPIRED` |
| `test_a_changed_keycloak_token_is_refused` | One signature character changed | `SIGNATURE` |
| `test_another_realm_on_the_same_server_is_another_issuer` | Checked against the `master` realm | `UNKNOWN_KEY` |
| `test_a_misspelled_issuer_finds_no_keys` | Checked against a wrong issuer | `IdentityProviderUnavailable` |

Create `tests/test_oidc_integration.py`:

```python
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
```

**Check:** with Keycloak running (`make keycloak-up`):

```bash
uv run pytest -m integration tests/test_oidc_integration.py -q
```

Expected: `6 passed`. With Keycloak stopped, `6 skipped`.

### Step 6 — No coding in this step: break it on purpose

A test suite is only worth something if it fails when the code is wrong. This step removes one protective line, watches the right test fail, and puts the line back.

**Action 1:** in `src/knowledge_service/identity/oidc.py`, delete these two lines in `_fetch`:

```python
                # A redirect could lead off the issuer's origin.
                follow_redirects=False,
```

**Action 2:** run `uv run pytest tests/test_oidc.py -q`. Expected: `1 failed, 75 passed`, and the failure is `test_a_redirect_is_never_followed_off_the_issuers_origin`. That test's client follows redirects, so without the line it fetches keys from `evil.test` and accepts a token they signed.

**Action 3:** put the two lines back and run the tests again: `76 passed`.

**Action 4:** compare with your predictions in the evidence file, and note any that were wrong.

### Step 7 — No coding in this step: documents, gates, evidence, and progress

This step updates what the change made inaccurate, proves everything still works, and records the result.

**Action 1:** update three documents.

In `docs/adr/0013-group-code-into-packages-by-module.md`:

**1.** Find:

```markdown
|---|---|
| `knowledge_service.ingestion` | Sources, normalization, chunking, provenance, reconciliation, publishing, synchronization, deletions |
| `knowledge_service.ingestion.parsers` | The format parsers and the structural blocks they produce |
| `knowledge_service.embeddings` | The EmbeddingModel port and its OpenAI adapter, batching, publication, index configurations, model migrations |
| `knowledge_service.retrieval` | Vector search, retrieval filters, the vector benchmark |
| `knowledge_service.persistence` | `schema` (every table, the one `metadata` Alembic compares) and one module per kind of record |

Domain values (`identifiers`, `access`, `documents`, `chunks`, `language`,
`audit`, `jobs`, `conversations`, `evaluation`), configuration, the database
runtime, and the HTTP application stay at the top level, where every module
may import them.

```

Replace with:

```markdown
|---|---|
| `knowledge_service.ingestion` | Sources, normalization, chunking, provenance, reconciliation, publishing, synchronization, deletions |
| `knowledge_service.ingestion.parsers` | The format parsers and the structural blocks they produce |
| `knowledge_service.embeddings` | The EmbeddingModel port and its OpenAI adapter, batching, publication, index configurations, model migrations |
| `knowledge_service.retrieval` | Vector search, retrieval filters, the vector benchmark |
| `knowledge_service.persistence` | `schema` (every table, the one `metadata` Alembic compares) and one module per kind of record |
| `knowledge_service.identity` | Who is calling: the OIDC token verifier (M07-T03), and the Authorization Context built from its result |

Domain values (`identifiers`, `access`, `documents`, `chunks`, `language`,
`audit`, `jobs`, `conversations`, `evaluation`), configuration, the database
runtime, and the HTTP application stay at the top level, where every module
may import them.

```

**2.** Find:

```markdown
  `knowledge_service.embeddings.openai_adapter`.
- Lesson guides and evidence written before this decision name the old flat
  paths. They record the code as it was at their commit and are not
  rewritten.
- A new module goes into the package of the architecture module it serves;
  a new package needs a reason recorded here.
```

Replace with:

```markdown
  `knowledge_service.embeddings.openai_adapter`.
- Lesson guides and evidence written before this decision name the old flat
  paths. They record the code as it was at their commit and are not
  rewritten.
- A new module goes into the package of the architecture module it serves;
  a new package needs a reason recorded here.
- M07-T03 added `knowledge_service.identity`. Checking who calls is the
  seam to the identity provider, an external system, and every transport
  needs it before any module runs; it belongs to none of the modules above.
```

In `docs/architecture.md`:

Find:

```markdown
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

Replace with:

```markdown
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

In `docs/threat-model.md`:

**1.** Find:

```markdown
| Supply-chain attacker | None | Ship a hostile dependency or image |

## Trust zones

The zones are the ones drawn under "Trust zones" in `docs/security.md`. The boundaries that matter here:

- **Public to edge.** Everything from the browser is untrusted until the edge and the API have verified a token (M07-T03).
- **API to data.** PostgreSQL decides eligibility inside every search; a Chunk the reader may not see never leaves it.
- **API to model providers.** Evidence and the Question leave the private zone here; only eligible Evidence under opaque IDs may cross, and replies come back as untrusted data.
- **Workers to Sources.** Source content enters here and stays untrusted data for its whole life.
- **Local model servers.** BGE-M3 and the reranker listen on 127.0.0.1 only and must name the pinned revision.

## Entry points

| Entry point | Who reaches it | Today |
|---|---|---|
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. No token check yet; the reader is fixed when the Evidence source is built (M07-T03, M07-T04) |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors; the Redis queue in M08 |
```

Replace with:

```markdown
| Supply-chain attacker | None | Ship a hostile dependency or image |

## Trust zones

The zones are the ones drawn under "Trust zones" in `docs/security.md`. The boundaries that matter here:

- **Public to edge.** Everything from the browser is untrusted until the edge and the API have verified a token. `OidcVerifier` verifies one; the endpoints call it from M07-T04.
- **API to data.** PostgreSQL decides eligibility inside every search; a Chunk the reader may not see never leaves it.
- **API to model providers.** Evidence and the Question leave the private zone here; only eligible Evidence under opaque IDs may cross, and replies come back as untrusted data.
- **Workers to Sources.** Source content enters here and stays untrusted data for its whole life.
- **Local model servers.** BGE-M3 and the reranker listen on 127.0.0.1 only and must name the pinned revision.

## Entry points

| Entry point | Who reaches it | Today |
|---|---|---|
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. No token check yet; the reader is fixed when the Evidence source is built (M07-T04) |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors; the Redis queue in M08 |
```

**2.** Find:

```markdown
| Retrieved text makes an agent call a tool | None needed yet | Agent prompt-injection tests (M11-T14) |

### Operations

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A client with no identity reads or spends | Nothing yet: neither answer endpoint checks a token | Token validation and the Authorization Context (M07-T03, M07-T04) |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; marker tests prove no planted text reaches them | Redacted logs and traces (M07-T12) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | Durable audit events (M07-T14) |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |

## Invariant trace
```

Replace with:

```markdown
| Retrieved text makes an agent call a tool | None needed yet | Agent prompt-injection tests (M11-T14) |

### Operations

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A client with no identity reads or spends | Nothing yet in the endpoints: `OidcVerifier` exists, but neither answer endpoint calls it | The Authorization Context from verified tokens only (M07-T04) |
| A forged, expired, or other-audience token is accepted | `OidcVerifier`: RS256 only, a key from the issuer's JWKS, the exact issuer, the API audience, and the times within 30 seconds of skew | The endpoints use it (M07-T04) |
| Random key IDs make the API flood the identity provider, or an outage opens the API | An unknown key ID refreshes the keys at most once per 30 seconds; cached keys outlive failed refreshes for at most an hour, then every token is refused as unavailable | None |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; marker tests prove no planted text reaches them | Redacted logs and traces (M07-T12) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | Durable audit events (M07-T14) |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |

## Invariant trace
```

**3.** Find:

```markdown
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- The API checks no token yet; until M07-T03 and M07-T04 the reader is whoever the Evidence source was built for.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
```

Replace with:

```markdown
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- The API checks no token yet: `OidcVerifier` exists, but until M07-T04 the reader is whoever the Evidence source was built for.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
```

**Action 2:** run the gates in order, with Keycloak running:

```bash
uv run pytest tests/test_oidc.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2457 passed for `make test-unit`, 76 more than M07-T02, and 412 passed for `make test-integration`, 6 more.

**Action 3:** complete the evidence file:

- **Design:** the package, the checks in order, the two kinds of failure, and the key cache.
- **Verification:** the commands and counts, the deliberate break, and the known limits from "Before you begin".
- **Review:** the review summary from "Before you begin".
- **Decision:** why the algorithm is fixed by the verifier, why keys come from JWKS by `kid`, and why an outage refuses tokens after the stale limit; plus at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M07-T03 row to `docs/curriculum/PROGRESS.md`, set the next task to M07-T04, and commit with `m07-t03: validate oidc tokens`.

## Reflection questions

1. A caller of `verify` hands over a string and gets a user ID back. List three things the verifier does in between that the caller never has to think about.
2. Later someone wants the API to be faster and suggests caching "token → verified claims" for each token string. Which check from this lesson could that cache quietly skip, and what would go wrong?
3. Imagine someone loosens the verifier "just for local work", for example by accepting any algorithm or skipping the audience. Which test in `tests/test_oidc.py` would fail first, and is that enough protection?
4. You believe one fetch per 30 seconds is gentle enough for Keycloak. What would you measure, or which attack would you run, to find out whether that is true?
5. When Keycloak is down for two hours, every request gets a 503. Why is that better than accepting tokens with the old keys, and who would disagree?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the change.
- [ ] PyJWT is a dependency, and `knowledge_service.identity` exists.
- [ ] `OidcVerifier` accepts only RS256 tokens signed by a published key, for the exact issuer and the API audience, within 30 seconds of skew.
- [ ] Every refusal is a `TokenRejected` with one reason, and a missing provider is an `IdentityProviderUnavailable`; no token text appears in errors or logs.
- [ ] Keys are cached, a rotated key is picked up, unknown `kid`s refresh at most once per interval, and stale keys stop working after the limit.
- [ ] `tests/test_oidc.py` passes (76), including valid, expired, wrong audience, wrong issuer, unknown key, and key rotation.
- [ ] `tests/test_oidc_integration.py` passes against the running Keycloak (6).
- [ ] You ran the deliberate break, saw the redirect test fail, and undid it.
- [ ] ADR 0013, `docs/architecture.md`, and `docs/threat-model.md` describe the verifier.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
