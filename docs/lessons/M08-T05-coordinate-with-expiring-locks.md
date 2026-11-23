# M08-T05 — Coordinate with expiring locks

Task: [M08-T05 — Coordinate with expiring locks](../curriculum/milestones/M08-jobs-and-confluence/M08-T05-coordinate-with-expiring-locks.md)

## What you will do

This lesson makes sure two synchronization passes of the same Source never fight. A short-lived lock in Redis stops a second pass from starting, and a check in PostgreSQL makes sure that, even if the lock fails, an older pass can never undo what a newer pass wrote.

A concrete example. At 09:00 the Source "Handbook" holds `x.md` and `y.md`, and both are stored. Then:

| Time | What happens today | What happens after this lesson |
|---|---|---|
| 09:10 | pass A reads the Source; `x.md` is gone from it at that moment | pass A takes the Source's lock and its fence, and reads |
| 09:10 + | pass A stalls before its last step | pass A stalls before its last step; its lock runs out |
| 10:00 | pass B reads the Source; `x.md` is back; B finishes | pass B takes the lock and a newer fence, and finishes |
| 10:00 + | pass A wakes up and marks `x.md` deleted | pass A wakes up; its write is refused (`SynchronizationSuperseded`) |
| result | `x.md` is gone, though the Source has it | `x.md` stays available |

| What the task asks | Where it is done |
|---|---|
| Short-lived Redis locks only where duplicate concurrent work is unsafe | "Before you begin", block 1; Step 6: only synchronization takes a lease |
| Ownership tokens | Step 2: every lease holds a random token; renew and release compare it inside Redis |
| Database truth after expiry | Steps 3 to 5: the Source's fence in PostgreSQL, checked in every write |
| Crash and expiry tests avoid permanent lockout | Steps 2 and 7: an expired lease frees the Source; a cancelled pass gives its lease back |
| Do not claim locks guarantee correctness | Step 7: the fence tests let every pass in, as an expired lease would, and the data stays right |
| Typed failure or denial | `SourceBusy`, `SourceLeaseLost`, `SourceCoordinationUnavailable`, `SynchronizationSuperseded`, `InvalidLease` |
| Replay or cancellation behaviour explicit | "Before you begin", blocks 5 and 6 |
| Update only affected documentation | Step 9: architecture, threat model, operations |

This lesson includes coding: Steps 2 to 7 write code, a migration, and tests. Steps 1, 8, 9, and 10 need no code, although Step 9 edits three documents.

Terms used in this lesson:

- **Lease:** a lock that ends by itself after a set time, its time to live (TTL), unless its holder renews it. Like a meeting-room booking that expires if nobody confirms it.
- **Ownership token:** a random value only the holder knows. Renewing or releasing a lease works only with the right token, so nobody can end someone else's lease by mistake.
- **Fence:** a value in the database that says which pass may write right now. Here it is the observation time of the newest pass that started.
- **Observation time (`observed_at`):** the moment a pass looked at the Source. A later observation is fresher.
- **Share lock (`FOR SHARE`):** a row lock many readers can hold at once, but which makes anyone who wants to change the row wait.

## Before you begin

This section explains every choice the code rests on, with what happens step by step. Each block ends with what this lesson changes.

### 1. Where two copies of the same work are a problem, and where they are not

**The situation.** Since M08-T04, a failed Job comes back on its own, and the scheduler in M08-T06 will start synchronizations on a timer. So the same work can now really run twice at once.

| Work | What two copies at once would do | What already stops it |
|---|---|---|
| purge J1 | delete the same Chunks twice | the claim's row lock on the Job (M08-T03) |
| retry pass | queue J1 twice | `SKIP LOCKED` (M08-T04) |
| relay | send J1 twice | `SKIP LOCKED` (M08-T02) |
| asking for the same work | store two Jobs | the open-intent unique index (M08-T03) |
| **synchronizing Source S** | page the Source twice, parse every file twice, and let an older pass overwrite a newer one | **nothing yet** |

**The hidden fact.** A Redis lock helps only where the database does not already decide. Everywhere above, PostgreSQL already makes the second copy do nothing, so a lock there would only add a way to fail.

**What changes.** Only synchronization gets a lock.

### 2. What a lease is, and why it needs a token

**The situation.** Worker A starts syncing the Handbook at 10:00:00. Worker B is asked to sync it at 10:00:05.

| Time | Worker A | Worker B | Redis key `lease:source-sync:<id>` |
|---|---|---|---|
| 10:00:00 | `SET key tokenA NX PX 120000`: set only if missing, expire in 2 min | | `tokenA`, 2 min left |
| 10:00:05 | reading page 3 | same `SET ... NX`: refused, key exists | `tokenA` |
| 10:00:05 | | raises `SourceBusy`, reads nothing | `tokenA` |
| 10:00:10 | after page 3: renew, `PEXPIRE` only if the value is still `tokenA` | | `tokenA`, 2 min left again |
| 10:01:00 | done; release: `DEL` only if the value is still `tokenA` | | gone |

If A crashed at 10:00:30 and never released, the key would vanish by itself at 10:02:30, and the next pass would run. That is what "no permanent lockout" means.

**The hidden fact.** "Check the token, then delete" must happen in one step inside Redis. In two steps there is a gap:

| Moment | Worker A (lease about to expire) | Worker B | Key |
|---|---|---|---|
| 1 | `GET key` returns `tokenA`: "still mine" | | `tokenA` |
| 2 | (pauses for a moment) | | expires |
| 3 | | `SET key tokenB NX`: succeeds | `tokenB` |
| 4 | `DEL key`: deletes B's lease | | gone; B thinks it holds it |

A short Lua script runs inside Redis as one step, so nothing can happen between the check and the delete:

```python
# Two steps: a gap between the check and the delete.
if await client.get(key) == token:
    await client.delete(key)

# One step, inside Redis.
await client.eval(_RELEASE, 1, key, token)
```

Think of a meeting-room booking card with your name and an end time: anyone can take the room once the time is up, and only the name on the card can extend or cancel it.

**What changes.** `leases.py` is a small module with `acquire`, `renew`, and `release`. Names and TTLs are checked, Redis calls are bounded by timeouts, and any Redis failure becomes `LeaseStoreUnavailable`.

### 3. Review finding: a lease cannot keep the data right

**The situation.** The first prototype of this lesson used only the lease, and the docs said "two passes are wasteful, not wrong". An adversarial review proved that false with real PostgreSQL and Redis. Here is the case from the table at the top, step by step:

| Time | Older pass A (observed 09:10, sees only `y.md`) | Newer pass B (observed 10:00, sees `x.md` and `y.md`) | `x.md` in the database |
|---|---|---|---|
| 09:10 | takes the lease, reads, renews after its last page | | available, `updated_at` 09:00 |
| 09:10 + | pauses: a slow page, a garbage-collection pause, a wait for a database connection | | available |
| 09:12 | | lease expired: takes it, reads, sees `x.md` unchanged, writes nothing | available, `updated_at` still 09:00 |
| 10:00 + | wakes up; marks every Document it did not see as deleted | | **tombstoned**, its purge queued |

**The hidden facts.** Two of them.

1. A lease is a check, not a barrier. A pass checks "I still hold it" and then writes, and anything can pause it in between. The lease can expire in that gap.
2. The old safety net in `tombstone_missing` keeps a Document "updated after my observation". But a pass that sees a file unchanged writes nothing, so `updated_at` stays 09:00, and the older pass's 09:10 looks newer.

**How bad it is.** The review found two harms. First, a Document the Source still has disappears from search and its Chunks are purged, until the next sync. Second, a security one: an older pass whose first page arrived late wrote access grants after the newer pass had removed a reader, so that reader could read the Document again. Both existed in `synchronize_source` before this lesson; a lease only made them rarer.

**What changes.** The lease stays, to save work. The data is protected by a fence in PostgreSQL (block 4).

### 4. The fence: the database decides who may write

**The situation.** The same two passes, now with a fence on the Source's row.

| Time | Older pass A (09:10) | Newer pass B (10:00) | `sources.synchronization_fence` |
|---|---|---|---|
| 09:10 | claims: `UPDATE ... SET fence = 09:10 WHERE fence < 09:10` succeeds | | 09:10 |
| 09:10 | each write first checks `fence = 09:10` under a share lock: yes, writes | | 09:10 |
| 09:10 + | pauses | | 09:10 |
| 09:12 | | claims: `09:10 < 10:00`, succeeds; its writes check `fence = 10:00` | 10:00 |
| 10:00 + | wakes up; the tombstone write checks `fence = 09:10`: **no** | | 10:00 |
| 10:00 + | raises `SynchronizationSuperseded`; nothing written | | 10:00 |

**The hidden fact.** Why can't B's claim slip in between A's check and A's write? Because A checks inside the same transaction that writes, while holding a share lock on the Source's row:

```sql
-- Every write of a pass starts with this, in its own transaction:
SELECT synchronization_fence FROM sources WHERE source_id = :id FOR SHARE;
-- ...then, if the fence is still this pass's observation time, it writes.

-- A newer pass claims the fence like this:
UPDATE sources SET synchronization_fence = :observed_at
WHERE source_id = :id
  AND (synchronization_fence IS NULL OR synchronization_fence < :observed_at)
RETURNING source_id;
```

| Lock held by A's write | B's claim (`UPDATE`) | Another write of A |
|---|---|---|
| `FOR SHARE` on the Source's row | waits until A's write commits | goes ahead: share locks do not block each other |

So every write of A either commits before B's claim or sees B's fence and is refused. There is no third case. That is what "database truth after expiry" means: the lease may expire, the fence does not.

```python
# Before: a reconciliation writes whatever it read.
async with runtime.transaction() as connection:
    stored = await load_document(connection, ..., for_update=True)

# After: it first checks it still holds the fence.
async with runtime.transaction() as connection:
    if guard is not None:
        await guard(connection)  # raises SynchronizationSuperseded if not
    stored = await load_document(connection, ..., for_update=True)
```

The fence is part of every `synchronize_source` pass, not only leased ones. The review showed that a pass without the fence could be overwritten by an older pass with it.

Think of the ticket numbers at a deli counter: only the number on the display is served. When a newer number comes up, an old ticket is refused, however long its holder waited.

**What changes.** `sources.synchronization_fence` and two small database functions, `claim_synchronization_fence` and `synchronization_fence_holds`. `reconcile_document`, `narrow_access`, and `tombstone_missing` take a `guard` and run it first in their transaction. `synchronize_source` claims the fence before it reads anything and passes the guard to all three.

### 5. Which failures are retried

**The situation.** A synchronization Job can now meet four new errors.

| Error | When | Code (M08-T04) | Retried? |
|---|---|---|---|
| `SourceBusy` | another pass holds the lease | `source_unavailable` | yes, later |
| `SourceLeaseLost` | the lease was gone at a renewal; the pass stopped before tombstoning | `source_unavailable` | yes, later |
| `SourceCoordinationUnavailable` | Redis did not answer | `source_unavailable` | yes, later |
| `SynchronizationSuperseded` | a pass with the same or a newer observation time started | `superseded`, new | **no** |

**The hidden fact.** A superseded pass can never succeed on a retry with the same `observed_at`: the fence only accepts newer times. And there is nothing to redo, because the newer pass covers the work. The review found that, as a `SourceUnavailable`, it would burn the whole retry budget, so it now has its own permanent code. A Job that retries a synchronization must observe the Source again, with a new `observed_at`.

### 6. Crash, expiry, cancellation: nothing locks the Source out for good

| What happens | Lease | Fence |
|---|---|---|
| the pass crashes after taking the lease | expires within 2 minutes | stays at the crashed pass's time; any newer pass claims it |
| the pass is cancelled | given up in `finally` | as above |
| Redis is down when the pass ends | not given up; expires by itself | not involved |
| a renewal finds the lease gone | the pass stops before tombstoning | its earlier writes stay until a newer pass overwrites them |
| a caller passes an `observed_at` in 2099 | | refused (`InvalidSource`); otherwise the fence would refuse every real pass until 2099 |

The last row is a lockout path the fence itself could create, so `synchronize_source` refuses an `observed_at` more than five minutes ahead of the machine's clock.

### 7. Known limits

These stay as they are for now:

- Cancelling a pass while it is taking the lease can leave the key held by nobody until its TTL ends, up to 2 minutes.
- A cancellation during the final release replaces the pass's own error, which then survives only as the exception's context. The lease still expires.
- `SourceBusy` uses one attempt of the Job's retry budget; the scheduler in M08-T06 should avoid queuing a synchronization that is already running.
- A page that takes longer than the lease's 2 minutes loses the lease, and the pass stops at the next renewal. The data stays right; the work is repeated.
- Writes an older pass committed before a newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way.
- An `observed_at` up to five minutes in the future holds the fence until that time.

## Step-by-step instructions

Start PostgreSQL and Redis (`make redis-up`) before you begin. Run every command from the repository root. For integration checks, set the test URLs in your terminal:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
export KNOWLEDGE_SERVICE_TEST_REDIS_URL="redis://127.0.0.1:6380/15"
```

Run only one integration command against this database at a time.

### Step 1 — No coding in this step: write the invariant and your predictions

Before changing anything, write down what must be true at the end and what you expect to see. You check the predictions in Step 8 and Step 10.

Create `docs/evidence/M08-T05-coordinate-with-expiring-locks.md`:

```markdown
# M08-T05 — Coordinate with expiring locks evidence

## Invariant

At most one synchronization pass of a Source runs at a time while its lease
holds; a lease expires on its own and only its token's holder can renew or
release it. Whatever the lease does, only the pass with the newest
observation time may write a Source's Documents: every narrowing,
reconciliation, and tombstone checks the Source's fence in PostgreSQL inside
its own transaction, under a share lock, so no write of an older pass lands
after a newer pass claimed the fence.

## Owners

- Avoiding duplicate work: the Source's lease in Redis (`leases`,
  `ingestion.exclusive_sync`).
- Correctness: the Source's fence in PostgreSQL (`synchronize_source`).
- Retries: the stored Job (M08-T04); a superseded pass is never retried.
- Authorization: none; nothing outside the service starts a pass.

## Expected failing check

Before the change,
`uv run pytest tests/test_leases_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.

## Predictions

- What a second pass gets while another pass of the same Source holds the lease:
- What happens to a lease whose holder crashed, and after how long:
- What an older pass that wakes up after a newer pass finished does when it tries to tombstone:
- How many tests fail if `synchronize_source` stops passing its guard to the writes:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: the lease module

This step adds a small module that takes, renews, and gives up leases in Redis.

**In plain words:** taking a lease writes a key in Redis only if it is not there yet, with a random token and an end time. Renewing pushes the end time out, and giving it up deletes the key, but both only if the key still holds this holder's token. If Redis does not answer within two seconds, every call fails with one clear error instead of hanging.

| Piece | What it does |
|---|---|
| `Lease` | the name, the holder's token, and the TTL |
| `LeaseStore` | the port: `acquire`, `renew`, `release` |
| `RedisLeaseStore` | `SET NX PX` to acquire; Lua scripts that compare the token to renew and release |
| `check_name`, `check_ttl` | names are 1 to 200 lowercase letters, digits, `:_-`; TTLs from 100 ms to 10 minutes |
| `lease_client` | an asyncio Redis client with 2-second timeouts and no hidden retries |
| `LeaseStoreUnavailable`, `InvalidLease` | the typed errors |

`renew` also checks the lease's TTL, because a hand-built `Lease` with a TTL of zero would otherwise delete the key, and one of ten years would hold it nearly forever.


**Action 1:** create the module.

Create `src/knowledge_service/leases.py`:

```python
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
```

**Action 2:** test it against real Redis: one holder, a crashed holder, renewing, a stale token, random tokens, an unreachable store, and hand-built leases.

Create `tests/test_leases_integration.py`:

```python
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
```

**Check:** `uv run pytest tests/test_leases_integration.py -q -m integration` reports `9 passed`.

### Step 3 — Coding step: the fence in the database

This step adds the column that says which pass may write a Source, and the two queries that use it.

**In plain words:** each Source gets one more value: the observation time of the newest pass that started. A pass may claim it only with a newer time. Any write can ask "is it still mine?", and while it asks it holds a lock that makes a newer claim wait until the write is done.

| Piece | What it does |
|---|---|
| `sources.synchronization_fence` | the observation time of the newest pass; empty until the first pass |
| Migration `a7b8c9d0e1f2` | adds it |
| `claim_synchronization_fence` | the `UPDATE ... WHERE fence < :observed_at RETURNING` from block 4; true if it worked |
| `synchronization_fence_holds` | `SELECT ... FOR SHARE`; true if the fence is still this pass's time |
| `WriteGuard` | the type of a check that runs first inside a write's transaction |


**Action 1:** create the migration.

Create `migrations/versions/a7b8c9d0e1f2_source_synchronization_fence.py`:

```python
"""source synchronization fence

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-11-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"


def upgrade() -> None:
    # Empty for every Source: the first fenced pass claims it.
    op.add_column(
        "sources",
        sa.Column("synchronization_fence", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sources", "synchronization_fence")
```

**Action 2:** declare the column in the `sources` table:

In `src/knowledge_service/persistence/schema.py`, find:

```python
    sa.Column("checkpoint", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("kind", "location", name="uq_sources_kind_location"),
)
```

and replace it with:

```python
    sa.Column("checkpoint", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    # The observation time of the newest pass that claimed this Source; only
    # that pass may write its Documents (`ingestion.synchronization`).
    sa.Column("synchronization_fence", sa.DateTime(timezone=True), nullable=True),
    sa.UniqueConstraint("kind", "location", name="uq_sources_kind_location"),
)
```

**Action 3:** the file is short; replace it whole.

Replace the whole of `src/knowledge_service/persistence/sources.py` with:

```python
"""Statements over Sources, their synchronization runs, and their fence."""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.identifiers import SourceId
from knowledge_service.persistence.schema import sources, synchronization_runs


def source_runs_statement(source_id: UUID, limit: int) -> sa.Select[Any]:
    """Select the most recent Synchronization Runs of one Source."""
    return (
        sa.select(synchronization_runs)
        .where(synchronization_runs.c.source_id == source_id)
        .order_by(synchronization_runs.c.started_at.desc())
        .limit(limit)
    )


async def claim_synchronization_fence(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> bool:
    """Make `observed_at` the Source's fence, if it is newer than the fence.

    False when a pass that observed the Source at the same time or later
    already claimed it. The update waits for every write that holds the
    fence (`synchronization_fence_holds`), so none of them lands after it.
    """
    claimed = await connection.scalar(
        sa.update(sources)
        .where(
            sources.c.source_id == source_id.value,
            sa.or_(
                sources.c.synchronization_fence.is_(None),
                sources.c.synchronization_fence < observed_at,
            ),
        )
        .values(synchronization_fence=observed_at)
        .returning(sources.c.source_id)
    )
    return claimed is not None


async def synchronization_fence_holds(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> bool:
    """Whether the pass that observed at `observed_at` still holds the fence.

    The Source's row is share-locked until the transaction ends, so a newer
    pass cannot claim the fence while this write is in progress.
    """
    fence = await connection.scalar(
        sa.select(sources.c.synchronization_fence)
        .where(sources.c.source_id == source_id.value)
        .with_for_update(read=True)
    )
    return fence == observed_at
```

**Action 4:** create the guard type. It lives in its own module, because `reconciliation` already imports `deletions` and both need it.

Create `src/knowledge_service/ingestion/fencing.py`:

```python
"""A check that runs first inside each write of a synchronization pass.

A guard is awaited at the start of a write's own transaction. It may take
locks and read the database, and raising from it rolls the write back, so
nothing the pass writes can land after the guard says it should not.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

type WriteGuard = Callable[[AsyncConnection], Awaitable[None]]
```

**Check:** `uv run pytest tests/test_migrations_integration.py -q -m integration` reports `3 passed`.

### Step 4 — Coding step: let every write check a guard first

This step gives the three functions that write a Source's Documents an optional guard, run first inside their transaction.

**In plain words:** reconciling a Document, narrowing who may read it, and marking Documents deleted each open a transaction. Now each of them first asks the guard, if one is given, and if the guard says no, nothing is written. Callers that pass no guard behave exactly as before.

| Function | What changes |
|---|---|
| `reconcile_document` | `guard=None`; awaited first in its transaction |
| `narrow_access` | the same |
| `tombstone_missing` | the same |


**Action 1:** in `reconciliation.py`, import the type:

In `src/knowledge_service/ingestion/reconciliation.py`, find:

```python
from knowledge_service.ingestion.audit_events import access_event
from knowledge_service.ingestion.chunking import chunk_by_structure
from knowledge_service.ingestion.deletions import availability_event
from knowledge_service.ingestion.fingerprints import (
    authorization_fingerprint,
    content_fingerprint,
```

and replace it with:

```python
from knowledge_service.ingestion.audit_events import access_event
from knowledge_service.ingestion.chunking import chunk_by_structure
from knowledge_service.ingestion.deletions import availability_event
from knowledge_service.ingestion.fencing import WriteGuard
from knowledge_service.ingestion.fingerprints import (
    authorization_fingerprint,
    content_fingerprint,
```

Give `reconcile_document` the parameter:

In `src/knowledge_service/ingestion/reconciliation.py`, find:

```python
    readers: Sequence[Subject],
    observed_at: datetime,
    run_id: SynchronizationRunId | None = None,
) -> Reconciliation:
    """Publish one observation of a document, writing only what changed.

    `readers` are the normalized subjects who may read it; an empty sequence
    means nobody may. `run_id` names the Synchronization Run in the audit
    event of a grant change. An observation older than the stored version
    raises `InvalidDocument`, and nothing is written.
    """
    provenance = normalized.provenance
    content = content_fingerprint(normalized.parsed)
```

and replace it with:

```python
    readers: Sequence[Subject],
    observed_at: datetime,
    run_id: SynchronizationRunId | None = None,
    guard: WriteGuard | None = None,
) -> Reconciliation:
    """Publish one observation of a document, writing only what changed.

    `readers` are the normalized subjects who may read it; an empty sequence
    means nobody may. `run_id` names the Synchronization Run in the audit
    event of a grant change. An observation older than the stored version
    raises `InvalidDocument`, and nothing is written. `guard` runs first in
    the transaction; raising from it writes nothing.
    """
    provenance = normalized.provenance
    content = content_fingerprint(normalized.parsed)
```

and run it first in its transaction:

In `src/knowledge_service/ingestion/reconciliation.py`, find:

```python
        document_id=provisional, grants=_grants(provisional, readers, observed_at)
    )
    async with runtime.transaction() as connection:
        # Lock the stored row: a concurrent run of the same document waits
        # here until this one commits, then decides from what it committed.
        stored = await load_document(
```

and replace it with:

```python
        document_id=provisional, grants=_grants(provisional, readers, observed_at)
    )
    async with runtime.transaction() as connection:
        if guard is not None:
            await guard(connection)
        # Lock the stored row: a concurrent run of the same document waits
        # here until this one commits, then decides from what it committed.
        stored = await load_document(
```

Give `narrow_access` the parameter:

In `src/knowledge_service/ingestion/reconciliation.py`, find:

```python
    readers: Sequence[Subject],
    observed_at: datetime,
    run_id: SynchronizationRunId | None = None,
) -> bool:
    """Keep a stored Document only for readers both old and new access name.
```

and replace it with:

```python
    readers: Sequence[Subject],
    observed_at: datetime,
    run_id: SynchronizationRunId | None = None,
    guard: WriteGuard | None = None,
) -> bool:
    """Keep a stored Document only for readers both old and new access name.
```

and run it first in its transaction:

In `src/knowledge_service/ingestion/reconciliation.py`, find:

```python
    normalizes again. Returns whether the Access Grants changed.
    """
    async with runtime.transaction() as connection:
        stored = await load_document(
            connection, source_id=source_id, external_id=external_id, for_update=True
        )
```

and replace it with:

```python
    normalizes again. Returns whether the Access Grants changed.
    """
    async with runtime.transaction() as connection:
        if guard is not None:
            await guard(connection)
        stored = await load_document(
            connection, source_id=source_id, external_id=external_id, for_update=True
        )
```

**Action 2:** in `deletions.py`, import the type:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import AuditEventId, DocumentId, JobId, SourceId
from knowledge_service.job_failures import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
```

and replace it with:

```python
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import AuditEventId, DocumentId, JobId, SourceId
from knowledge_service.ingestion.fencing import WriteGuard
from knowledge_service.job_failures import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
```

and give `tombstone_missing` the parameter, run first in its transaction:

In `src/knowledge_service/ingestion/deletions.py`, find:

```python
    source_id: SourceId,
    seen: Collection[str],
    observed_at: datetime,
) -> tuple[Tombstone, ...]:
    """Tombstone every available Document of a Source whose ID is not in `seen`.

    `seen` must hold every external ID of one complete pass over the Source;
    after a partial pass, documents that were simply not reached yet would be
    tombstoned. A Document updated after `observed_at` was seen by a later
    observation, so it is kept.
    """
    seen = frozenset(seen)
    tombstones: list[Tombstone] = []
    async with runtime.transaction() as connection:
        available = await load_available_documents(connection, source_id=source_id)
        for candidate in available:
            if candidate.provenance.external_id in seen:
```

and replace it with:

```python
    source_id: SourceId,
    seen: Collection[str],
    observed_at: datetime,
    guard: WriteGuard | None = None,
) -> tuple[Tombstone, ...]:
    """Tombstone every available Document of a Source whose ID is not in `seen`.

    `seen` must hold every external ID of one complete pass over the Source;
    after a partial pass, documents that were simply not reached yet would be
    tombstoned. A Document updated after `observed_at` was seen by a later
    observation, so it is kept. `guard` runs first in the transaction;
    raising from it tombstones nothing.
    """
    seen = frozenset(seen)
    tombstones: list[Tombstone] = []
    async with runtime.transaction() as connection:
        if guard is not None:
            await guard(connection)
        available = await load_available_documents(connection, source_id=source_id)
        for candidate in available:
            if candidate.provenance.external_id in seen:
```

**Check:** `uv run pyright src/knowledge_service/ingestion` reports 0 errors, and `uv run pytest tests/test_deletions_integration.py -q -m integration` reports `9 passed`.

### Step 5 — Coding step: every synchronization pass claims and holds the fence

This step makes the fence part of every `synchronize_source` pass, adds the heartbeat the lease will use, and gives a superseded pass its own failure code.

**In plain words:** before reading anything, a pass checks its observation time is sensible and claims the Source's fence; if a newer pass already has it, it stops at once. Every write of the pass then runs the guard from block 4. After every page, the pass calls an optional heartbeat, which the next step uses to renew the lease. A pass that is superseded is reported with a new code that is never retried.

| Piece | What changes |
|---|---|
| `SynchronizationSuperseded` | a new `SourceError` in `ingestion/sources.py` |
| `MAX_CLOCK_SKEW` | five minutes; a later `observed_at` is refused |
| `_claim_fence` | checks UTC and the skew, then claims in its own transaction |
| `synchronize_source` | claims, builds the guard, passes it and `heartbeat` down |
| `_publish`, `_revoke_first` | pass the guard to every write; call `heartbeat` after each page |
| `JobFailure.SUPERSEDED` | the eighth code, permanent |


**Action 1:** add the error next to the other Source errors:

In `src/knowledge_service/ingestion/sources.py`, find:

```python
    """The Source refused the configured credentials."""


class SourceKind(StrEnum):
    """The kind of origin a Source reads from."""
```

and replace it with:

```python
    """The Source refused the configured credentials."""


class SynchronizationSuperseded(SourceError):
    """A pass that observed the Source at the same time or later has started.

    This pass writes nothing more, and trying it again cannot help: only a
    pass with a newer observation time may write the Source's Documents.
    """


class SourceKind(StrEnum):
    """The kind of origin a Source reads from."""
```

**Action 2:** in `synchronization.py`, first the module docstring and the imports:

In `src/knowledge_service/ingestion/synchronization.py`, find:

```python
error's type name, in a transaction of its own. A pass that is cancelled
records nothing; a failed pass whose record cannot be written logs that and
raises its own error.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from knowledge_service.audit import (
    AuditAction,
```

and replace it with:

```python
error's type name, in a transaction of its own. A pass that is cancelled
records nothing; a failed pass whose record cannot be written logs that and
raises its own error.

A caller may pass a `heartbeat`, awaited after every page and so also just
before the tombstones. Raising from it stops the pass there, as a failure:
nothing is tombstoned. `synchronize_source_exclusively` uses it to keep its
lease.

Every pass is fenced. Before it reads anything it claims the Source's fence
with its observation time, which works only when that time is newer than
every earlier pass's, and every transaction that writes a Document (each
narrowing, each reconciliation, the tombstones) first checks, under a share
lock on the Source's row, that it still holds the fence. A newer claim
waits for those writes, and after it the older pass's writes are refused
with `SynchronizationSuperseded`. So an older pass that finishes late can
never undo a newer one. A pass observed more than `MAX_CLOCK_SKEW` in the
future is refused, so a bad clock cannot hold the fence for long.
"""

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import (
    AuditAction,
```

import the guard type:

In `src/knowledge_service/ingestion/synchronization.py`, find:

```python
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.grant_normalization import (
    NormalizedAccess,
    PrincipalDirectory,
```

and replace it with:

```python
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.fencing import WriteGuard
from knowledge_service.ingestion.grant_normalization import (
    NormalizedAccess,
    PrincipalDirectory,
```

import the error and the fence queries, and add the skew limit:

In `src/knowledge_service/ingestion/synchronization.py`, find:

```python
    SourcePort,
    SourceRecord,
    SourceRecordState,
)
from knowledge_service.persistence.access import load_readers
from knowledge_service.persistence.audit import record_audit_event

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
```

and replace it with:

```python
    SourcePort,
    SourceRecord,
    SourceRecordState,
    SynchronizationSuperseded,
)
from knowledge_service.persistence.access import load_readers
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.sources import (
    claim_synchronization_fence,
    synchronization_fence_holds,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
# How far ahead of this machine's clock an observation may be. A fence set
# far in the future would refuse every real pass until that time came.
MAX_CLOCK_SKEW = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
```

Add the `heartbeat` parameter:

In `src/knowledge_service/ingestion/synchronization.py`, find:

```python
    principal_directory: PrincipalDirectory | None,
    page_size: int = PAGE_SIZE,
    run_id: SynchronizationRunId | None = None,
) -> SynchronizationSummary:
    """Publish every record the Source offers, then tombstone the rest.
```

and replace it with:

```python
    principal_directory: PrincipalDirectory | None,
    page_size: int = PAGE_SIZE,
    run_id: SynchronizationRunId | None = None,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
) -> SynchronizationSummary:
    """Publish every record the Source offers, then tombstone the rest.
```

In `synchronize_source`, claim the fence and build the guard:

In `src/knowledge_service/ingestion/synchronization.py`, find:

```python
    None, only public items are readable. A Source that returns a checkpoint
    it returned before raises `InvalidSource` instead of paging forever.
    The pass runs as `run_id`, or as a new run, and records how it ended.
    """
    run = SynchronizationRunId.new() if run_id is None else run_id
    try:
        summary = await _publish(
            runtime,
```

and replace it with:

```python
    None, only public items are readable. A Source that returns a checkpoint
    it returned before raises `InvalidSource` instead of paging forever.
    The pass runs as `run_id`, or as a new run, and records how it ended.
    Raises `SynchronizationSuperseded`, before reading and recording
    anything, when a pass with the same or a later `observed_at` started.
    """
    run = SynchronizationRunId.new() if run_id is None else run_id
    await _claim_fence(runtime, source_id=source_id, observed_at=observed_at)

    async def guard(connection: AsyncConnection) -> None:
        if not await synchronization_fence_holds(
            connection, source_id=source_id, observed_at=observed_at
        ):
            raise SynchronizationSuperseded("a newer synchronization pass started")

    try:
        summary = await _publish(
            runtime,
```

pass both down:

In `src/knowledge_service/ingestion/synchronization.py`, find:

```python
            principal_directory=principal_directory,
            page_size=page_size,
            run_id=run,
        )
    except Exception as error:
        # The pass's own error is raised either way; its record holds only
```

and replace it with:

```python
            principal_directory=principal_directory,
            page_size=page_size,
            run_id=run,
            heartbeat=heartbeat,
            guard=guard,
        )
    except Exception as error:
        # The pass's own error is raised either way; its record holds only
```

Add `_claim_fence` before `_record_finished`:

In `src/knowledge_service/ingestion/synchronization.py`, find:

```python
    return summary


async def _record_finished(
    runtime: DatabaseRuntime,
    *,
```

and replace it with:

```python
    return summary


async def _claim_fence(
    runtime: DatabaseRuntime, *, source_id: SourceId, observed_at: datetime
) -> None:
    if observed_at.utcoffset() != timedelta(0):
        raise InvalidSource("observed_at must be a UTC-aware timestamp")
    if observed_at > datetime.now(UTC) + MAX_CLOCK_SKEW:
        raise InvalidSource("observed_at is too far in the future to fence on")
    async with runtime.transaction() as connection:
        if not await claim_synchronization_fence(
            connection, source_id=source_id, observed_at=observed_at
        ):
            raise SynchronizationSuperseded("a newer synchronization pass started")


async def _record_finished(
    runtime: DatabaseRuntime,
    *,
```

`_publish` takes both:

In `src/knowledge_service/ingestion/synchronization.py`, find:

```python
    principal_directory: PrincipalDirectory | None,
    page_size: int,
    run_id: SynchronizationRunId,
) -> SynchronizationSummary:
    """One pass: every page, then the tombstones and requeued purges."""
    seen: set[str] = set()
```

and replace it with:

```python
    principal_directory: PrincipalDirectory | None,
    page_size: int,
    run_id: SynchronizationRunId,
    heartbeat: Callable[[], Awaitable[None]] | None,
    guard: WriteGuard | None,
) -> SynchronizationSummary:
    """One pass: every page, then the tombstones and requeued purges."""
    seen: set[str] = set()
```

passes the guard to `_revoke_first`:

In `src/knowledge_service/ingestion/synchronization.py`, find:

```python
            source_id=source_id,
            observed_at=observed_at,
            run_id=run_id,
        )
        for record, access in planned.values():
            outcome = normalize(record, source_id=source_id)
```

and replace it with:

```python
            source_id=source_id,
            observed_at=observed_at,
            run_id=run_id,
            guard=guard,
        )
        for record, access in planned.values():
            outcome = normalize(record, source_id=source_id)
```

to each reconciliation, then calls the heartbeat after the page:

In `src/knowledge_service/ingestion/synchronization.py`, find:

```python
                readers=access.readers,
                observed_at=observed_at,
                run_id=run_id,
            )
            changes[
                _counted(result.change, narrowed=record.external_id in narrowed)
            ] += 1
        if page.next_checkpoint is None:
            break
        if page.next_checkpoint in used:
```

and replace it with:

```python
                readers=access.readers,
                observed_at=observed_at,
                run_id=run_id,
                guard=guard,
            )
            changes[
                _counted(result.change, narrowed=record.external_id in narrowed)
            ] += 1
        if heartbeat is not None:
            # After the last page this runs just before the tombstones.
            await heartbeat()
        if page.next_checkpoint is None:
            break
        if page.next_checkpoint in used:
```

and to the tombstones:

In `src/knowledge_service/ingestion/synchronization.py`, find:

```python
        used.add(page.next_checkpoint)
        checkpoint = page.next_checkpoint
    tombstones = await tombstone_missing(
        runtime, source_id=source_id, seen=seen, observed_at=observed_at
    )
    requeued = await requeue_unfinished_purges(
        runtime, source_id=source_id, now=observed_at
```

and replace it with:

```python
        used.add(page.next_checkpoint)
        checkpoint = page.next_checkpoint
    tombstones = await tombstone_missing(
        runtime, source_id=source_id, seen=seen, observed_at=observed_at, guard=guard
    )
    requeued = await requeue_unfinished_purges(
        runtime, source_id=source_id, now=observed_at
```

`_revoke_first` takes it:

In `src/knowledge_service/ingestion/synchronization.py`, find:

```python
    source_id: SourceId,
    observed_at: datetime,
    run_id: SynchronizationRunId,
) -> set[str]:
    """Narrow every record whose new access leaves a reader out.
```

and replace it with:

```python
    source_id: SourceId,
    observed_at: datetime,
    run_id: SynchronizationRunId,
    guard: WriteGuard | None,
) -> set[str]:
    """Narrow every record whose new access leaves a reader out.
```

and passes it to `narrow_access`:

In `src/knowledge_service/ingestion/synchronization.py`, find:

```python
            readers=access.readers,
            observed_at=observed_at,
            run_id=run_id,
        ):
            narrowed.add(record.external_id)
    return narrowed
```

and replace it with:

```python
            readers=access.readers,
            observed_at=observed_at,
            run_id=run_id,
            guard=guard,
        ):
            narrowed.add(record.external_id)
    return narrowed
```

**Action 3:** in `job_failures.py`, import the error:

In `src/knowledge_service/job_failures.py`, find:

```python
    EmbeddingRequestRefused,
)
from knowledge_service.errors import database_unavailable
from knowledge_service.ingestion.sources import SourceUnauthorized, SourceUnavailable
from knowledge_service.jobs import Job
from knowledge_service.model import (
    ChatModelError,
```

and replace it with:

```python
    EmbeddingRequestRefused,
)
from knowledge_service.errors import database_unavailable
from knowledge_service.ingestion.sources import (
    SourceUnauthorized,
    SourceUnavailable,
    SynchronizationSuperseded,
)
from knowledge_service.jobs import Job
from knowledge_service.model import (
    ChatModelError,
```

add the code:

In `src/knowledge_service/job_failures.py`, find:

```python
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_REFUSED = "model_refused"
    PROGRAMMING_ERROR = "programming_error"

    @property
    def transient(self) -> bool:
```

and replace it with:

```python
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_REFUSED = "model_refused"
    PROGRAMMING_ERROR = "programming_error"
    # A newer run of the same work has started; there is nothing to redo.
    SUPERSEDED = "superseded"

    @property
    def transient(self) -> bool:
```

and check for it first:

In `src/knowledge_service/job_failures.py`, find:

```python

def classify_job_failure(error: Exception) -> JobFailure:
    """The failure an attempt's error stands for; unknown errors are bugs."""
    if isinstance(error, SourceUnavailable):
        return JobFailure.SOURCE_UNAVAILABLE
    if isinstance(error, SourceUnauthorized):
```

and replace it with:

```python

def classify_job_failure(error: Exception) -> JobFailure:
    """The failure an attempt's error stands for; unknown errors are bugs."""
    if isinstance(error, SynchronizationSuperseded):
        return JobFailure.SUPERSEDED
    if isinstance(error, SourceUnavailable):
        return JobFailure.SOURCE_UNAVAILABLE
    if isinstance(error, SourceUnauthorized):
```

**Action 4:** in `tests/test_job_failures.py`, import it:

In `tests/test_job_failures.py`, find:

```python
    RejectionReason,
)
from knowledge_service.identifiers import JobId
from knowledge_service.ingestion.sources import SourceUnauthorized, SourceUnavailable
from knowledge_service.job_failures import (
    JobFailure,
    RetryPolicy,
```

and replace it with:

```python
    RejectionReason,
)
from knowledge_service.identifiers import JobId
from knowledge_service.ingestion.sources import (
    SourceUnauthorized,
    SourceUnavailable,
    SynchronizationSuperseded,
)
from knowledge_service.job_failures import (
    JobFailure,
    RetryPolicy,
```

and add it to the classification table:

In `tests/test_job_failures.py`, find:

```python
    [
        (SourceUnavailable("the wiki is down"), JobFailure.SOURCE_UNAVAILABLE),
        (SourceUnauthorized("token expired"), JobFailure.SOURCE_REFUSED),
        (
            OperationalError("SELECT 1", None, ConnectionError("lost")),
            JobFailure.DATABASE_UNAVAILABLE,
```

and replace it with:

```python
    [
        (SourceUnavailable("the wiki is down"), JobFailure.SOURCE_UNAVAILABLE),
        (SourceUnauthorized("token expired"), JobFailure.SOURCE_REFUSED),
        (SynchronizationSuperseded("newer pass"), JobFailure.SUPERSEDED),
        (
            OperationalError("SELECT 1", None, ConnectionError("lost")),
            JobFailure.DATABASE_UNAVAILABLE,
```

**Check:** `uv run pytest tests/test_job_failures.py -q` reports `41 passed`, and `uv run pytest tests/test_synchronization_integration.py -q -m integration` reports `23 passed`.

### Step 6 — Coding step: one pass per Source at a time

This step adds the wrapper that holds the Source's lease around a pass.

**In plain words:** before a pass starts, the wrapper takes the Source's lease; if another pass has it, it stops without reading anything. After every page it renews the lease; if the lease is gone, it stops before marking anything deleted. However the pass ends, it gives the lease back. Every lease error is a kind of "the Source is unavailable", so a Job retries later.

| Piece | What it does |
|---|---|
| `SYNC_LEASE_TTL` | two minutes: longer than a page, short enough after a crash |
| `lease_name` | `source-sync:<source id>`, shared by every pass of the Source |
| `SourceBusy`, `SourceLeaseLost`, `SourceCoordinationUnavailable` | the lease errors, all `SourceUnavailable` |
| `synchronize_source_exclusively` | acquire, run `synchronize_source` with `heartbeat=keep_lease`, release in `finally` |


**Action 1:** create the wrapper.

Create `src/knowledge_service/ingestion/exclusive_sync.py`:

```python
"""One synchronization pass per Source at a time, best effort.

Two passes over one Source at once waste work: both page the Source (a
remote API with rate limits and, later, a cost) and parse the same files.
A lease in Redis keeps a second pass from even starting. It is only a hint:
it can expire while its holder still works. What keeps the data right when
that happens is the Source's fence, which every pass of `synchronize_source`
claims and holds in PostgreSQL.

A pass takes the Source's lease before it reads anything, renews it after
every page, and gives it up however the pass ends. If renewing finds the
lease gone, the pass stops before it tombstones anything. If the holder
crashes, the lease expires and the next pass runs. The lease errors are
`SourceUnavailable`, so a Job that meets one is retried later (M08-T04).
"""

import logging
from datetime import datetime, timedelta
from typing import Final

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import SourceId, SynchronizationRunId
from knowledge_service.ingestion.grant_normalization import PrincipalDirectory
from knowledge_service.ingestion.sources import SourcePort, SourceUnavailable
from knowledge_service.ingestion.synchronization import (
    PAGE_SIZE,
    SynchronizationSummary,
    synchronize_source,
)
from knowledge_service.leases import LeaseStore, LeaseStoreUnavailable

logger = logging.getLogger(__name__)


# Longer than any one page takes, so a working pass keeps its lease, and
# short enough that a crashed pass blocks the Source only briefly.
SYNC_LEASE_TTL: Final = timedelta(minutes=2)


class SourceBusy(SourceUnavailable):
    """Another pass holds this Source's lease; try again later."""


class SourceLeaseLost(SourceUnavailable):
    """This pass lost its lease part-way, so it stopped before tombstoning."""


class SourceCoordinationUnavailable(SourceUnavailable):
    """The lease store did not answer, so no pass may start or go on."""


def lease_name(source_id: SourceId) -> str:
    """The lease every synchronization pass of this Source shares."""
    return f"source-sync:{source_id.value}"


async def synchronize_source_exclusively(
    runtime: DatabaseRuntime,
    source: SourcePort,
    *,
    leases: LeaseStore,
    source_id: SourceId,
    observed_at: datetime,
    principal_directory: PrincipalDirectory | None,
    page_size: int = PAGE_SIZE,
    run_id: SynchronizationRunId | None = None,
    ttl: timedelta = SYNC_LEASE_TTL,
) -> SynchronizationSummary:
    """`synchronize_source`, while holding the Source's lease.

    Raises `SourceBusy` before reading anything when another pass holds the
    lease, `SourceLeaseLost` when the lease is gone at a renewal, and
    `SourceCoordinationUnavailable` when the lease store does not answer.
    `synchronize_source`'s own errors, such as `SynchronizationSuperseded`,
    pass through.
    """
    try:
        lease = await leases.acquire(lease_name(source_id), ttl)
    except LeaseStoreUnavailable as error:
        raise SourceCoordinationUnavailable("cannot take the source lease") from error
    if lease is None:
        raise SourceBusy("another synchronization pass holds this source")

    async def keep_lease() -> None:
        try:
            held = await leases.renew(lease)
        except LeaseStoreUnavailable as error:
            raise SourceCoordinationUnavailable(
                "cannot renew the source lease"
            ) from error
        if not held:
            raise SourceLeaseLost("the source lease expired during the pass")

    try:
        return await synchronize_source(
            runtime,
            source,
            source_id=source_id,
            observed_at=observed_at,
            principal_directory=principal_directory,
            page_size=page_size,
            run_id=run_id,
            heartbeat=keep_lease,
        )
    finally:
        try:
            await leases.release(lease)
        except LeaseStoreUnavailable as error:
            # It expires on its own within its time to live.
            logger.warning(
                "source %s lease not released: %s",
                str(source_id.value),
                type(error).__name__,
            )
```

**Action 2:** unit tests for names, TTLs, and how every error is classified.

Create `tests/test_leases.py`:

```python
"""Tests for lease names, times to live, and how lease failures are classified."""

from datetime import timedelta

import pytest

from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.exclusive_sync import (
    SourceBusy,
    SourceCoordinationUnavailable,
    SourceLeaseLost,
    lease_name,
)
from knowledge_service.ingestion.sources import SynchronizationSuperseded
from knowledge_service.job_failures import JobFailure, classify_job_failure
from knowledge_service.leases import (
    MAX_TTL,
    MIN_TTL,
    InvalidLease,
    check_name,
    check_ttl,
)

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")


@pytest.mark.parametrize("name", ["source-sync:abc", "a", "x" * 200, "a:b_c-1"])
def test_a_short_safe_name_is_accepted(name: str) -> None:
    assert check_name(name) == name


@pytest.mark.parametrize(
    "name", ["", "Upper", "a b", "x" * 201, ":leading", "new\nline", None, 7]
)
def test_any_other_name_is_refused(name: object) -> None:
    with pytest.raises(InvalidLease, match="lease name"):
        check_name(name)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("ttl", [MIN_TTL, timedelta(minutes=2), MAX_TTL])
def test_a_ttl_within_bounds_is_accepted(ttl: timedelta) -> None:
    assert check_ttl(ttl) == ttl


@pytest.mark.parametrize(
    "ttl",
    [
        MIN_TTL - timedelta(milliseconds=1),
        MAX_TTL + timedelta(seconds=1),
        timedelta(0),
        -timedelta(seconds=1),
        5,
        None,
    ],
)
def test_a_ttl_out_of_bounds_is_refused(ttl: object) -> None:
    with pytest.raises(InvalidLease, match="lease lives"):
        check_ttl(ttl)  # pyright: ignore[reportArgumentType]


def test_every_pass_of_one_source_shares_one_valid_lease_name() -> None:
    name = lease_name(SOURCE_ID)

    assert name == f"source-sync:{SOURCE_ID.value}"
    assert check_name(name) == name


@pytest.mark.parametrize(
    "error",
    [
        SourceBusy("busy"),
        SourceLeaseLost("lost"),
        SourceCoordinationUnavailable("down"),
    ],
)
def test_a_lease_failure_is_retried_later_like_an_unavailable_source(
    error: Exception,
) -> None:
    failure = classify_job_failure(error)

    assert failure is JobFailure.SOURCE_UNAVAILABLE
    assert failure.transient


def test_a_superseded_pass_is_never_retried() -> None:
    # A newer pass already covers the work; a retry could only be refused.
    failure = classify_job_failure(SynchronizationSuperseded("newer"))

    assert failure is JobFailure.SUPERSEDED
    assert not failure.transient
```

**Check:** `uv run pytest tests/test_leases.py -q` reports `26 passed`.

### Step 7 — Coding step: prove it with real PostgreSQL and Redis

This step runs whole passes against both, including every case in "Before you begin".

**In plain words:** the first tests use the real lease: renewals per page, a busy Source, a crashed holder, a lease lost mid-pass, a cancelled pass, and Redis down. The fence tests then use a lease store that lets every pass in, as an expired lease would, and show that an older pass still cannot tombstone, cannot write a page it read too early, and cannot slip past a pass that took no lease at all.

| Test | What it proves |
|---|---|
| `test_a_pass_renews_its_lease_after_every_page_and_gives_it_up` | three pages, three renewals, one release |
| `test_a_second_pass_does_nothing_while_another_holds_the_source` | `SourceBusy`; nothing written, no audit record |
| `test_a_crashed_pass_blocks_its_source_only_until_the_lease_expires` | block 6, first row |
| `test_a_pass_that_loses_its_lease_stops_before_tombstoning` | nothing tombstoned; the new holder's lease survives |
| `test_a_cancelled_pass_gives_up_its_lease` | block 6, second row |
| `test_an_unreachable_lease_store_stops_the_pass_before_it_reads` | nothing read, nothing written |
| `test_an_older_pass_cannot_tombstone_what_a_newer_pass_saw` | block 3, first harm |
| `test_an_older_pass_cannot_write_a_page_it_read_before_a_newer_pass` | block 3, second harm |
| `test_a_pass_that_did_not_observe_after_the_fence_reads_nothing` | same and earlier times are refused before reading |
| `test_a_newer_claim_waits_for_a_write_that_holds_the_fence` | the lock table in block 4 |
| `test_an_observation_from_the_far_future_cannot_take_the_fence` | block 6, last row |
| `test_an_unleased_newer_pass_also_fences_out_an_older_leased_one` | the fence is part of every pass |
| `test_a_retry_must_observe_the_source_again` | block 5 |


Create `tests/test_exclusive_sync_integration.py`:

```python
"""One synchronization pass per Source at a time, against real PostgreSQL and Redis.

The lease saves work; the fence in PostgreSQL keeps the data right. The fence
tests use a lease store that lets every pass in, as an expired lease would,
and show that an older pass still cannot write after a newer one started.
"""

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.audit import AuditAction, AuditOutcome
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import DocumentAvailability
from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.exclusive_sync import (
    SourceBusy,
    SourceCoordinationUnavailable,
    SourceLeaseLost,
    lease_name,
    synchronize_source_exclusively,
)
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourceAccess,
    SourcePage,
    SourcePort,
    SynchronizationSuperseded,
)
from knowledge_service.ingestion.synchronization import synchronize_source
from knowledge_service.leases import (
    NAMESPACE,
    Lease,
    LeaseStore,
    RedisLeaseStore,
    lease_client,
)
from knowledge_service.persistence.audit import load_audit_events
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.persistence.sources import (
    claim_synchronization_fence,
    synchronization_fence_holds,
)
from knowledge_service.settings import Settings

pytestmark = pytest.mark.integration

REDIS_URL_ENV = "KNOWLEDGE_SERVICE_TEST_REDIS_URL"
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000030")
OBSERVED_AT = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)
PUBLIC = SourceAccess(public=True)
SHORT = timedelta(milliseconds=300)

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/exclusive', 'Exclusive', true, now(), now())"
)


@dataclass
class WatchedStore:
    """A real lease store that counts renewals and can act on each one."""

    inner: RedisLeaseStore
    renewals: int = 0
    on_renew: Callable[[Lease], object] | None = None
    released: list[Lease] = field(default_factory=lambda: list[Lease]())

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
        return await self.inner.acquire(name, ttl)

    async def renew(self, lease: Lease) -> bool:
        self.renewals += 1
        if self.on_renew is not None:
            outcome = self.on_renew(lease)
            if asyncio.iscoroutine(outcome):
                await outcome
        return await self.inner.renew(lease)

    async def release(self, lease: Lease) -> bool:
        self.released.append(lease)
        return await self.inner.release(lease)


@pytest.fixture
def redis_url() -> str:
    configured = os.environ.get(REDIS_URL_ENV)
    if not configured:
        pytest.skip(f"set {REDIS_URL_ENV} to a disposable Redis server")
    return configured


@pytest.fixture
async def store(redis_url: str) -> AsyncIterator[RedisLeaseStore]:
    client = lease_client(redis_url)
    leases = RedisLeaseStore(client)
    try:
        yield leases
    finally:
        await client.delete(NAMESPACE + lease_name(SOURCE_ID))
        await client.aclose()


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
    try:
        yield runtime
    finally:
        await runtime.dispose()


def folder(path: Path, *names: str) -> DirectorySource:
    path.mkdir(parents=True, exist_ok=True)
    for name in names:
        (path / name).write_text(f"# {name}\n\nThe text of {name}.\n")
    return DirectorySource(path, access=PUBLIC)


async def availability(runtime: DatabaseRuntime) -> dict[str, str]:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(documents.c.external_id, documents.c.availability).where(
                documents.c.source_id == SOURCE_ID.value
            )
        )
        return {row.external_id: row.availability for row in rows}


async def finished_outcomes(runtime: DatabaseRuntime) -> list[AuditOutcome]:
    async with runtime.transaction() as connection:
        events = await load_audit_events(
            connection, target_id=SOURCE_ID.value, limit=20
        )
    return [
        event.outcome
        for event in events
        if event.action is AuditAction.SYNCHRONIZATION_FINISHED
    ]


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def test_a_pass_renews_its_lease_after_every_page_and_gives_it_up(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    watched = WatchedStore(store)

    summary = await synchronize_source_exclusively(
        runtime,
        folder(tmp_path, "a.md", "b.md", "c.md"),
        leases=watched,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
        page_size=1,
    )

    assert sum(summary.changes.values()) == 3
    assert watched.renewals == 3
    assert len(watched.released) == 1
    # Given up: the next pass can take it at once.
    assert await store.acquire(lease_name(SOURCE_ID), SHORT) is not None


async def test_a_second_pass_does_nothing_while_another_holds_the_source(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    other = await store.acquire(lease_name(SOURCE_ID), timedelta(seconds=30))
    assert other is not None

    with pytest.raises(SourceBusy):
        await synchronize_source_exclusively(
            runtime,
            folder(tmp_path, "a.md"),
            leases=store,
            source_id=SOURCE_ID,
            observed_at=OBSERVED_AT,
            principal_directory=None,
        )

    assert await availability(runtime) == {}
    assert await finished_outcomes(runtime) == []
    # The other holder still has it.
    assert await store.renew(other)


async def test_a_crashed_pass_blocks_its_source_only_until_the_lease_expires(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    # A pass took the lease and died without giving it up.
    assert await store.acquire(lease_name(SOURCE_ID), SHORT) is not None
    source = folder(tmp_path, "a.md")
    with pytest.raises(SourceBusy):
        await synchronize_source_exclusively(
            runtime,
            source,
            leases=store,
            source_id=SOURCE_ID,
            observed_at=OBSERVED_AT,
            principal_directory=None,
        )

    await asyncio.sleep(0.45)
    summary = await synchronize_source_exclusively(
        runtime,
        source,
        leases=store,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )

    assert sum(summary.changes.values()) == 1


async def test_a_pass_that_loses_its_lease_stops_before_tombstoning(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    await synchronize_source(
        runtime,
        folder(tmp_path / "before", "a.md", "b.md", "gone.md"),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    taken_over: list[Lease] = []

    async def expire_and_take_over(lease: Lease) -> None:
        # The lease runs out mid-pass, and another pass takes it.
        if not taken_over:
            await store.release(lease)
            other = await store.acquire(lease.name, timedelta(seconds=30))
            assert other is not None
            taken_over.append(other)

    watched = WatchedStore(store, on_renew=expire_and_take_over)
    with pytest.raises(SourceLeaseLost):
        await synchronize_source_exclusively(
            runtime,
            folder(tmp_path / "after", "a.md", "b.md"),
            leases=watched,
            source_id=SOURCE_ID,
            observed_at=LATER,
            principal_directory=None,
            page_size=1,
        )

    # Stopped after its first page: nothing was tombstoned.
    assert (await availability(runtime))["gone.md"] == DocumentAvailability.AVAILABLE
    assert await finished_outcomes(runtime) == [
        AuditOutcome.SUCCEEDED,
        AuditOutcome.FAILED,
    ]
    # Releasing its own lease did not take the new holder's.
    assert await store.renew(taken_over[0])


async def test_a_cancelled_pass_gives_up_its_lease(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    first_page_done = asyncio.Event()
    never = asyncio.Event()
    inner = folder(tmp_path, "a.md", "b.md")

    class StallsOnSecondPage:
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            if checkpoint is not None:
                first_page_done.set()
                await never.wait()
            return await inner.fetch_page(checkpoint=checkpoint, page_size=page_size)

    source: SourcePort = StallsOnSecondPage()
    task = asyncio.create_task(
        synchronize_source_exclusively(
            runtime,
            source,
            leases=store,
            source_id=SOURCE_ID,
            observed_at=OBSERVED_AT,
            principal_directory=None,
            page_size=1,
        )
    )
    await first_page_done.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await store.acquire(lease_name(SOURCE_ID), SHORT) is not None


async def test_an_unreachable_lease_store_stops_the_pass_before_it_reads(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    client = lease_client(f"redis://127.0.0.1:{closed_port()}/0")
    reads: list[str | None] = []
    inner = folder(tmp_path, "a.md")

    class Recording:
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            reads.append(checkpoint)
            return await inner.fetch_page(checkpoint=checkpoint, page_size=page_size)

    try:
        with pytest.raises(SourceCoordinationUnavailable):
            await synchronize_source_exclusively(
                runtime,
                Recording(),
                leases=RedisLeaseStore(client),
                source_id=SOURCE_ID,
                observed_at=OBSERVED_AT,
                principal_directory=None,
            )
    finally:
        await client.aclose()

    assert reads == []
    assert await availability(runtime) == {}


@dataclass
class EveryoneGetsIn:
    """A lease store whose lease never stops anyone, as if it kept expiring.

    `pause` makes the first renewal of one pass wait, so a test can run a
    newer pass in between.
    """

    pause: asyncio.Event | None = None
    paused: asyncio.Event = field(default_factory=asyncio.Event)

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
        return Lease(name=name, token=os.urandom(16).hex(), ttl=ttl)

    async def renew(self, lease: Lease) -> bool:
        if self.pause is not None and not self.paused.is_set():
            self.paused.set()
            await self.pause.wait()
        return True

    async def release(self, lease: Lease) -> bool:
        return True


async def text_of(runtime: DatabaseRuntime) -> str:
    async with runtime.transaction() as connection:
        rows = await connection.scalars(sa.select(chunks.c.text))
        return " ".join(rows)


async def exclusive(
    runtime: DatabaseRuntime,
    source: SourcePort,
    leases: LeaseStore,
    observed_at: datetime,
) -> object:
    return await synchronize_source_exclusively(
        runtime,
        source,
        leases=leases,
        source_id=SOURCE_ID,
        observed_at=observed_at,
        principal_directory=None,
    )


async def test_an_older_pass_cannot_tombstone_what_a_newer_pass_saw(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    await synchronize_source(
        runtime,
        folder(tmp_path / "start", "x.md", "y.md"),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    # The older pass no longer sees x.md; it stops at its last renewal.
    resume = asyncio.Event()
    older_store = EveryoneGetsIn(pause=resume)
    older = asyncio.create_task(
        exclusive(
            runtime,
            folder(tmp_path / "older", "y.md"),
            older_store,
            OBSERVED_AT + timedelta(minutes=10),
        )
    )
    await older_store.paused.wait()
    # A newer pass sees both files, unchanged, so it writes nothing.
    await exclusive(
        runtime, folder(tmp_path / "newer", "x.md", "y.md"), EveryoneGetsIn(), LATER
    )
    resume.set()

    with pytest.raises(SynchronizationSuperseded):
        await older
    assert (await availability(runtime))["x.md"] == DocumentAvailability.AVAILABLE


async def test_an_older_pass_cannot_write_a_page_it_read_before_a_newer_pass(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    await synchronize_source(
        runtime,
        folder(tmp_path / "start", "x.md"),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    edited = tmp_path / "older"
    edited.mkdir()
    (edited / "x.md").write_text("# x.md\n\nAn edit the Source later undid.\n")
    page_read = asyncio.Event()
    newer_done = asyncio.Event()
    inner = DirectorySource(edited, access=PUBLIC)

    class SlowFirstPage:
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            page = await inner.fetch_page(checkpoint=checkpoint, page_size=page_size)
            page_read.set()
            # The page arrives only after a newer pass has finished.
            await newer_done.wait()
            return page

    older = asyncio.create_task(
        exclusive(
            runtime,
            SlowFirstPage(),
            EveryoneGetsIn(),
            OBSERVED_AT + timedelta(minutes=10),
        )
    )
    await page_read.wait()
    await exclusive(
        runtime, folder(tmp_path / "newer", "x.md"), EveryoneGetsIn(), LATER
    )
    newer_done.set()

    with pytest.raises(SynchronizationSuperseded):
        await older
    text = await text_of(runtime)
    assert "The text of x.md." in text
    assert "An edit the Source later undid." not in text


async def test_a_pass_that_did_not_observe_after_the_fence_reads_nothing(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    await exclusive(
        runtime, folder(tmp_path / "first", "a.md"), EveryoneGetsIn(), LATER
    )
    reads: list[str | None] = []
    inner = folder(tmp_path / "second", "a.md", "b.md")

    class Recording:
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            reads.append(checkpoint)
            return await inner.fetch_page(checkpoint=checkpoint, page_size=page_size)

    # Same time, then earlier: neither is newer than the fence.
    for observed_at in (LATER, OBSERVED_AT):
        with pytest.raises(SynchronizationSuperseded):
            await exclusive(runtime, Recording(), EveryoneGetsIn(), observed_at)

    assert reads == []
    assert set(await availability(runtime)) == {"a.md"}


async def test_a_newer_claim_waits_for_a_write_that_holds_the_fence(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        assert await claim_synchronization_fence(
            connection, source_id=SOURCE_ID, observed_at=OBSERVED_AT
        )
    write_holds = asyncio.Event()
    finish_write = asyncio.Event()

    async def a_write_of_the_older_pass() -> None:
        async with runtime.transaction() as connection:
            assert await synchronization_fence_holds(
                connection, source_id=SOURCE_ID, observed_at=OBSERVED_AT
            )
            write_holds.set()
            await finish_write.wait()

    async def the_newer_claim() -> bool:
        async with runtime.transaction() as connection:
            return await claim_synchronization_fence(
                connection, source_id=SOURCE_ID, observed_at=LATER
            )

    write = asyncio.create_task(a_write_of_the_older_pass())
    await write_holds.wait()
    claim = asyncio.create_task(the_newer_claim())
    await asyncio.sleep(0.3)
    # Still waiting for the write's share lock on the Source's row.
    assert not claim.done()

    finish_write.set()
    await write
    assert await claim
    async with runtime.transaction() as connection:
        assert not await synchronization_fence_holds(
            connection, source_id=SOURCE_ID, observed_at=OBSERVED_AT
        )


async def test_an_observation_from_the_far_future_cannot_take_the_fence(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    far_future = datetime.now(UTC) + timedelta(days=365)

    with pytest.raises(InvalidSource, match="future"):
        await exclusive(
            runtime, folder(tmp_path / "bad", "a.md"), EveryoneGetsIn(), far_future
        )

    # A real pass can still claim the Source.
    await exclusive(runtime, folder(tmp_path / "good", "a.md"), EveryoneGetsIn(), LATER)
    assert set(await availability(runtime)) == {"a.md"}


async def test_an_unleased_newer_pass_also_fences_out_an_older_leased_one(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    await synchronize_source(
        runtime,
        folder(tmp_path / "start", "x.md", "y.md"),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    resume = asyncio.Event()
    older_store = EveryoneGetsIn(pause=resume)
    older = asyncio.create_task(
        exclusive(
            runtime,
            folder(tmp_path / "older", "y.md"),
            older_store,
            OBSERVED_AT + timedelta(minutes=10),
        )
    )
    await older_store.paused.wait()
    # Every pass claims the fence, with or without a lease.
    await synchronize_source(
        runtime,
        folder(tmp_path / "newer", "x.md", "y.md"),
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=None,
    )
    resume.set()

    with pytest.raises(SynchronizationSuperseded):
        await older
    assert (await availability(runtime))["x.md"] == DocumentAvailability.AVAILABLE


async def test_a_retry_must_observe_the_source_again(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    class FailsOnce:
        def __init__(self, inner: DirectorySource) -> None:
            self.inner = inner
            self.failed = False

        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            if not self.failed:
                self.failed = True
                raise SourceCoordinationUnavailable("a blip")
            return await self.inner.fetch_page(
                checkpoint=checkpoint, page_size=page_size
            )

    source = FailsOnce(folder(tmp_path, "a.md"))
    with pytest.raises(SourceCoordinationUnavailable):
        await exclusive(runtime, source, EveryoneGetsIn(), OBSERVED_AT)

    # The same observation time cannot claim the fence again...
    with pytest.raises(SynchronizationSuperseded):
        await exclusive(runtime, source, EveryoneGetsIn(), OBSERVED_AT)
    # ...a new one can.
    await exclusive(runtime, source, EveryoneGetsIn(), LATER)
    assert set(await availability(runtime)) == {"a.md"}
```

**Check:** `uv run pytest tests/test_exclusive_sync_integration.py -q -m integration` reports `13 passed`.

### Step 8 — No coding in this step: break it on purpose

This step stops the writes from checking the fence, and shows which tests notice.

**Action 1:** in `synchronize_source` in `src/knowledge_service/ingestion/synchronization.py`, change:

```python
            heartbeat=heartbeat,
            guard=guard,
```

to:

```python
            heartbeat=heartbeat,
            guard=None,
```

**Action 2:** run:

```bash
uv run pytest tests/test_exclusive_sync_integration.py tests/test_synchronization_integration.py -q -m integration
```

You see `3 failed, 33 passed`. The three tests where an older pass wakes up after a newer one fail: without the guard, the older pass tombstones or overwrites again. Everything else passes, which shows the lease alone was never what kept the data right.

**Action 3:** undo the change and run the command again: `36 passed`.

**Check:** compare the count with your Step 1 prediction, and note it in the evidence file.

### Step 9 — No coding in this step: update three documents

This step records the lease and the fence where readers look for them.


**Action 1:** in the architecture, update the `ingestion` row:

In `docs/architecture.md`, find:

```markdown

| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document`, `normalize_access` (`grant_normalization`, a Source's `user:<id>` and `group:<id>` principals to Access Grant subjects through its `PrincipalDirectory`, as the same UUIDv5 IDs the Authorization Context uses, and only to subjects and paths a token could carry; distinct, in a stable order, and failing closed: an unmapped or malformed principal grants nothing and is reported), `narrow_access` (a record that fails to normalize keeps its last good version only for readers its old and new access both name) |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
```

and replace it with:

```markdown

| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source` (claims the Source's fence and checks it in every write; an optional `heartbeat` runs after every page), `synchronize_source_exclusively` (`exclusive_sync`, the same pass while holding the Source's lease), `normalize`, `reconcile_document`, `normalize_access` (`grant_normalization`, a Source's `user:<id>` and `group:<id>` principals to Access Grant subjects through its `PrincipalDirectory`, as the same UUIDv5 IDs the Authorization Context uses, and only to subjects and paths a token could carry; distinct, in a stable order, and failing closed: an unmapped or malformed principal grants nothing and is reported), `narrow_access` (a record that fails to normalize keeps its last good version only for readers its old and new access both name) |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
```

add the new code to the retries paragraph:

In `docs/architecture.md`, find:

```markdown
adapters keep their SDK retries off, and repository code never loops.
`fail_attempt` (`job_failures`) classifies a failed attempt by its error as a
Source, database, or model that is unavailable (transient) or refused
(permanent), or a programming error (permanent), and stores that code. Only a
transient failure with attempts left gets a `retry_at`: half of a doubling
backoff (30 seconds, capped at 15 minutes) plus a random share of the other
half. `requeue_due_retries`, run first by every `make worker-relay` pass, is
```

and replace it with:

```markdown
adapters keep their SDK retries off, and repository code never loops.
`fail_attempt` (`job_failures`) classifies a failed attempt by its error as a
Source, database, or model that is unavailable (transient) or refused
(permanent), a programming error (permanent), or a pass superseded by a newer synchronization pass (permanent), and stores that code. Only a
transient failure with attempts left gets a `retry_at`: half of a doubling
backoff (30 seconds, capped at 15 minutes) plus a random share of the other
half. `requeue_due_retries`, run first by every `make worker-relay` pass, is
```

add the leases and fence paragraph after it:

In `docs/architecture.md`, find:

```markdown
purge failed permanently is not given a new purge by later synchronization
passes; one that ran out of attempts on a passing failure is.

`telemetry` decides what leaves the process as logs. `configure_telemetry`
installs one root handler that writes JSON lines, and its `TelemetryFilter`
keeps only the fields `TELEMETRY_FIELDS` lists, with plain values, only this
```

and replace it with:

```markdown
purge failed permanently is not given a new purge by later synchronization
passes; one that ran out of attempts on a passing failure is.

`leases` holds short-lived, owned locks in Redis. A lease is a key that
expires on its own, holding its holder's random token; renewing and
releasing compare that token inside Redis, so a holder whose lease expired
cannot touch the next holder's. Only synchronization takes one, through
`synchronize_source_exclusively` (`ingestion.exclusive_sync`): it stops a
second pass of one Source from starting, so the Source is not paged and
parsed twice. The lease is renewed after every page through
`synchronize_source`'s `heartbeat` and given up however the pass ends.
Correctness never depends on it: an older pass that finishes late could
re-grant a reader a newer pass removed, roll content back, or tombstone a
Document the newer pass saw, so every `synchronize_source` pass, leased or
not, claims the Source's fence (`sources.synchronization_fence`) with its
observation time, which works only when that time is newer than the fence,
and every write of the pass (each narrowing, each reconciliation, the
tombstones) first checks under a share lock on the Source's row that it
still holds the fence. A newer claim waits for those writes, and every later
write of the older pass is refused with `SynchronizationSuperseded`, which
is never retried. An observation more than five minutes in the future is
refused, so a bad clock cannot hold the fence. Purges, retries, the relay,
and `enqueue_job` take no lease, because row locks, `SKIP LOCKED`, and the
open-intent index already give them one effect. The lease errors are
`SourceUnavailable`, so a Job that meets one is retried later, with a new
observation time.

`telemetry` decides what leaves the process as logs. `configure_telemetry`
installs one root handler that writes JSON lines, and its `TelemetryFilter`
keeps only the fields `TELEMETRY_FIELDS` lists, with plain values, only this
```

and sharpen the Redis rule at the end:

In `docs/architecture.md`, find:

```markdown

- LangGraph and multi-agent orchestration do not replace ordinary question answering.
- MCP is an additional protocol adapter, not the owner of search logic.
- Redis is not the durable record for conversations, jobs, or Documents.
- Generated-answer caching is excluded until permission and staleness behavior is proven.
- Kubernetes does not change application module interfaces.
```

and replace it with:

```markdown

- LangGraph and multi-agent orchestration do not replace ordinary question answering.
- MCP is an additional protocol adapter, not the owner of search logic.
- Redis is not the durable record for conversations, jobs, or Documents, and a lease is a hint: correctness comes from PostgreSQL, such as a Source's synchronization fence, never from holding a lease.
- Generated-answer caching is excluded until permission and staleness behavior is proven.
- Kubernetes does not change application module interfaces.
```

**Action 2:** in the threat model, add a row after `Retries hammer a failing dependency`:

In `docs/threat-model.md`, find:

```markdown
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |

### Retrieval
```

and replace it with:

```markdown
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |
| Two synchronization passes of one Source run at once: double Source calls, and an older pass finishing late re-grants a removed reader, rolls content back, or tombstones what a newer pass saw | `synchronize_source_exclusively` holds the Source's Redis lease, with an owner token, renewed after every page, so a second pass gets `SourceBusy` before reading; a crashed holder's lease expires in two minutes. The Source's fence in PostgreSQL decides who may write: every pass claims it with its observation time, and every narrowing, reconciliation, and tombstone checks it under a share lock on the Source's row, so after a newer pass claims it nothing of an older pass lands; an observation more than five minutes in the future is refused | Writes an older pass committed before the newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way; a pass observed up to five minutes in the future holds the fence until then |

### Retrieval
```

**Action 3:** in the operations guide, say that synchronization needs Redis:

In `docs/operations.md`, find:

```markdown

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. A failed Job comes back only through `make worker-relay`, which queues due retries before it publishes, so run it on a schedule. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

and replace it with:

```markdown

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. A failed Job comes back only through `make worker-relay`, which queues due retries before it publishes, so run it on a schedule. A synchronization pass takes its Source's lease in Redis first; while Redis is down no pass starts, and its Job is retried later. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

**Check:** `make docs-check` exits 0.

### Step 10 — No coding in this step: gates, evidence, and progress

This step runs every gate and finishes the records.

**Action 1:** run every gate, one after another:

```bash
make format-check
make lint
make typecheck
make test-unit
make test-integration
make docs-check
```

**Action 2:** finish the evidence file: the gate results and the Step 8 count under "Verification", the review findings and known limits under "Review", the design under "Design" and "Decision", and your answers to the reflection questions below. Then mark the task completed in `docs/curriculum/PROGRESS.md` and commit with `m08-t05: coordinate with expiring locks`.

**Check:** every gate passes, and `git status` is clean after the commit.

## Reflection questions

- Worker A holds the Handbook's lease and crashes. What does worker B see at once, and what does it see three minutes later? Which line of `leases.py` makes the difference?
- Why must "check the token, then delete the key" run as one Lua script? Walk through what goes wrong if it is two Redis commands.
- In block 3, the older pass renewed its lease successfully and still tombstoned `x.md` after the newer pass finished. Explain why a successful renewal is not enough to make the write safe.
- In block 4, could the newer pass's claim land between the older pass's fence check and its write? Explain what the share lock does to prevent it.
- Why is `SynchronizationSuperseded` never retried, while `SourceBusy` is? What must a Job do to try a synchronization again?
- What would you see in the database, or in the audit log, that tells you the fence is not holding?

## Completion checklist

- [ ] The evidence file has the invariant, the owners, and your predictions, written before the change.
- [ ] `leases.py` takes, renews, and releases leases with an owner token checked inside Redis; `tests/test_leases_integration.py` (9) passes.
- [ ] Migration `a7b8c9d0e1f2` adds `sources.synchronization_fence`; `claim_synchronization_fence` and `synchronization_fence_holds` exist.
- [ ] `reconcile_document`, `narrow_access`, and `tombstone_missing` run a guard first in their transaction.
- [ ] Every `synchronize_source` pass claims the fence, refuses a far-future `observed_at`, guards every write, and calls the heartbeat after each page; `SynchronizationSuperseded` has the permanent code `superseded`.
- [ ] `synchronize_source_exclusively` holds the Source's lease; `tests/test_leases.py` (26) and `tests/test_exclusive_sync_integration.py` (13) pass.
- [ ] You ran the deliberate break, saw the three fence tests fail, and undid it.
- [ ] `docs/architecture.md`, `docs/threat-model.md`, and `docs/operations.md` describe the lease and the fence.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
