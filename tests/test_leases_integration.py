"""Leases against a real Redis: one holder, expiry, and owner tokens."""

import asyncio
import os
import socket
import time
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from knowledge_service.leases import (
    InvalidLease,
    Lease,
    LeaseStoreUnavailable,
    RedisLeaseStore,
    lease_client,
)

pytestmark = pytest.mark.integration

REDIS_URL_ENV = "KNOWLEDGE_SERVICE_TEST_REDIS_URL"
SHORT = timedelta(milliseconds=300)
LONG = timedelta(seconds=30)


@pytest.fixture
def redis_url() -> str:
    configured = os.environ.get(REDIS_URL_ENV)
    if not configured:
        pytest.skip(f"set {REDIS_URL_ENV} to a disposable Redis server")
    return configured


@pytest.fixture
async def store(redis_url: str) -> AsyncIterator[RedisLeaseStore]:
    client = lease_client(redis_url)
    try:
        yield RedisLeaseStore(client)
    finally:
        await client.aclose()


def unique_name() -> str:
    return f"test:{uuid.uuid4().hex}"


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def test_one_holder_at_a_time(store: RedisLeaseStore) -> None:
    name = unique_name()

    first = await store.acquire(name, LONG)
    second = await store.acquire(name, LONG)
    assert first is not None
    assert second is None

    assert await store.release(first)
    assert await store.acquire(name, LONG) is not None


async def test_a_crashed_holder_does_not_lock_the_work_out(
    store: RedisLeaseStore,
) -> None:
    name = unique_name()
    # The holder takes the lease and never releases it, as if it crashed.
    assert await store.acquire(name, SHORT) is not None
    assert await store.acquire(name, SHORT) is None

    await asyncio.sleep(0.45)

    assert await store.acquire(name, SHORT) is not None


async def test_renewing_keeps_a_lease_past_its_first_ttl(
    store: RedisLeaseStore,
) -> None:
    name = unique_name()
    lease = await store.acquire(name, SHORT)
    assert lease is not None

    for _ in range(4):
        await asyncio.sleep(0.15)
        assert await store.renew(lease)

    # 0.6 s later it is still held, twice its time to live.
    assert await store.acquire(name, SHORT) is None


async def test_an_expired_holder_cannot_renew_or_release_the_next_ones_lease(
    store: RedisLeaseStore,
) -> None:
    name = unique_name()
    stale = await store.acquire(name, SHORT)
    assert stale is not None
    await asyncio.sleep(0.45)
    current = await store.acquire(name, LONG)
    assert current is not None

    assert not await store.renew(stale)
    assert not await store.release(stale)

    assert await store.acquire(name, LONG) is None
    assert await store.renew(current)
    assert await store.release(current)


async def test_every_lease_gets_its_own_random_token(store: RedisLeaseStore) -> None:
    tokens: list[str] = []
    for _ in range(3):
        lease = await store.acquire(unique_name(), LONG)
        assert lease is not None
        tokens.append(lease.token)
        await store.release(lease)

    assert len(set(tokens)) == 3
    assert all(len(token) == 32 for token in tokens)


async def test_an_unreachable_store_is_a_typed_failure_within_its_timeouts() -> None:
    client = lease_client(f"redis://127.0.0.1:{closed_port()}/0")
    store = RedisLeaseStore(client)
    started = time.monotonic()
    try:
        with pytest.raises(LeaseStoreUnavailable):
            await store.acquire(unique_name(), LONG)
    finally:
        await client.aclose()

    assert time.monotonic() - started < 5.0


@pytest.mark.parametrize(
    "ttl", [timedelta(0), -timedelta(seconds=1), timedelta(days=3650)]
)
async def test_a_hand_built_lease_cannot_renew_outside_the_ttl_bounds(
    store: RedisLeaseStore, ttl: timedelta
) -> None:
    held = await store.acquire(unique_name(), LONG)
    assert held is not None
    forged = Lease(name=held.name, token=held.token, ttl=ttl)

    with pytest.raises(InvalidLease):
        await store.renew(forged)

    # The real lease is untouched: still held, with its own time to live.
    assert await store.acquire(held.name, LONG) is None
    assert await store.renew(held)
