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
