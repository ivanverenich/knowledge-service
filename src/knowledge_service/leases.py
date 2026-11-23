"""Short-lived, owned locks in Redis: leases.

A lease lets one holder at a time do work that is wasteful to duplicate. It
expires on its own, so a holder that crashes cannot lock the work out for
good. Its holder gets a random token, and renewing or releasing checks that
token inside Redis, so a holder whose lease already expired can never extend
or delete the next holder's lease.

A lease is a hint, never the truth. It can expire while its holder is still
working, after a long pause or a slow call, and then two holders run at once.
Anything that must stay correct is decided by PostgreSQL (ADR 0011).
"""

import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Protocol, cast

import redis
import redis.asyncio
from redis.backoff import NoBackoff
from redis.retry import Retry

NAMESPACE: Final = "knowledge-service.v1:lease:"
MIN_TTL: Final = timedelta(milliseconds=100)
MAX_TTL: Final = timedelta(minutes=10)
REDIS_CONNECT_TIMEOUT_SECONDS: Final = 2.0
REDIS_SOCKET_TIMEOUT_SECONDS: Final = 2.0
_NAME = re.compile(r"[a-z0-9][a-z0-9:_-]{0,199}")

# Extend or delete the key only while it still holds this holder's token.
# Each runs inside Redis as one step, so nothing can change the key between
# the check and the write.
_RENEW: Final = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE: Final = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class InvalidLease(ValueError):
    """A lease name or time to live is outside what this module allows."""


class LeaseStoreUnavailable(RuntimeError):
    """The lease store did not answer, so no lease was taken or changed."""


@dataclass(frozen=True, slots=True)
class Lease:
    """A held lease: its name, its holder's token, and how long it lives."""

    name: str
    token: str
    ttl: timedelta


class LeaseStore(Protocol):
    """Takes, renews, and releases leases."""

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
        """Take `name` for `ttl`, or None when another holder has it."""
        ...

    async def renew(self, lease: Lease) -> bool:
        """Restart `lease`'s time to live; False when it is no longer held."""
        ...

    async def release(self, lease: Lease) -> bool:
        """Give `lease` up; False when it was no longer held."""
        ...


def check_name(name: str) -> str:
    """The name itself, or InvalidLease when it is not a short, safe key."""
    if type(name) is not str or _NAME.fullmatch(name) is None:
        raise InvalidLease("a lease name is 1 to 200 lowercase letters, digits, ':_-'")
    return name


def check_ttl(ttl: timedelta) -> timedelta:
    """The time to live itself, or InvalidLease when it is out of bounds."""
    if type(ttl) is not timedelta or not MIN_TTL <= ttl <= MAX_TTL:
        raise InvalidLease(f"a lease lives from {MIN_TTL} to {MAX_TTL}")
    return ttl


def _milliseconds(ttl: timedelta) -> int:
    return ttl // timedelta(milliseconds=1)


def lease_client(
    redis_url: str,
    *,
    connect_timeout_seconds: float = REDIS_CONNECT_TIMEOUT_SECONDS,
    socket_timeout_seconds: float = REDIS_SOCKET_TIMEOUT_SECONDS,
) -> redis.asyncio.Redis:
    """An asyncio Redis client whose every call makes one bounded attempt."""
    # redis-py leaves the keyword arguments of from_url untyped.
    return redis.asyncio.Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        redis_url,
        socket_connect_timeout=connect_timeout_seconds,
        socket_timeout=socket_timeout_seconds,
        retry=Retry(NoBackoff(), 0),
    )


@dataclass(frozen=True, slots=True)
class RedisLeaseStore:
    """Leases as Redis keys that expire, holding their holder's token."""

    client: redis.asyncio.Redis

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
        lease = Lease(
            name=check_name(name), token=secrets.token_hex(16), ttl=check_ttl(ttl)
        )
        try:
            taken = await self.client.set(
                NAMESPACE + lease.name,
                lease.token,
                nx=True,
                px=_milliseconds(lease.ttl),
            )
        except (redis.RedisError, OSError) as error:
            raise LeaseStoreUnavailable("the lease store did not answer") from error
        return lease if taken else None

    async def renew(self, lease: Lease) -> bool:
        ttl = _milliseconds(check_ttl(lease.ttl))
        return await self._run(_RENEW, lease, str(ttl)) == 1

    async def release(self, lease: Lease) -> bool:
        return await self._run(_RELEASE, lease) == 1

    async def _run(self, script: str, lease: Lease, *extra: str) -> int:
        try:
            # redis-py types eval loosely; the scripts above return integers.
            result = await self.client.eval(  # pyright: ignore[reportUnknownMemberType]
                script, 1, NAMESPACE + check_name(lease.name), lease.token, *extra
            )
        except (redis.RedisError, OSError) as error:
            raise LeaseStoreUnavailable("the lease store did not answer") from error
        return int(cast(int, result))
