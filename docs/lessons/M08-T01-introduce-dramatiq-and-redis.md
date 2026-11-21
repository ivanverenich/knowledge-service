# M08-T01 — Introduce Dramatiq and Redis

Task: [M08-T01 — Introduce Dramatiq and Redis](../curriculum/milestones/M08-jobs-and-confluence/M08-T01-introduce-dramatiq-and-redis.md)

## What you will do

This lesson gives the service a second kind of process: a **worker** that runs work in the background, outside any HTTP request. Callers put small messages into Redis, and the worker picks them up and runs them. Today the only job is a harmless `ping`, which proves the path works end to end. Later lessons send synchronization and purge jobs through the same path.

- **A pinned Redis.** A local Redis container, pinned to one exact image, that never throws a message away to save memory.
- **A configured broker.** One function builds the Dramatiq broker with JSON messages, named queues, a time limit, a message age limit, and no automatic retries.
- **One harmless job.** The `ping` job takes a short token and logs it. Both the sender and the worker check the token.
- **A readiness probe.** A command that exits 0 only when Redis answers in time, and 1 otherwise.
- **Proof:** tests run a real worker against a stand-in broker and against real Redis, and attack the setup with hostile messages and broken servers.

| The task asks for | Where it happens |
|---|---|
| Versioned infrastructure | Step 2: a pinned Dramatiq version; Step 3: Redis pinned by digest; Step 4: the `knowledge-service.v1` namespace |
| Explicit queues | Step 4: `QueueName` with `maintenance` and `synchronization` |
| Explicit serializer | Step 4: JSON set on every broker build; pickle is never used |
| Time limits | Step 4: a 60-second default; Step 5: 5 seconds for `ping` |
| Local health checks | Step 3: the container's own check; Step 6: the worker readiness probe |
| A worker consumes a harmless job | Step 7 and Step 8: tests; Step 9: a real worker |
| Readiness fails when the broker is unavailable | Step 6: the probe; Step 7: tests; Step 9: stop Redis and watch it fail |
| Success, boundary, typed failure or denial | Step 7: a consumed ping; a 64-character token; `InvalidPing` and `WorkerConfigurationError` |
| Real infrastructure where its semantics are under test | Step 8: the integration tests use the real Redis container |
| Update only affected documentation | Step 11: `docs/architecture.md`, `docs/threat-model.md` |

This lesson includes coding: Steps 2 to 8 write code, configuration, and tests. Steps 1, 9, 10, and 11 do not, though Step 11 edits two documents.

Terms used in this lesson:

- **Broker:** the post office between the code that asks for work and the code that does it. Here it is Redis, driven by the Dramatiq library.
- **Message:** one request for work, such as "run `ping` with token `abc`". It is stored in Redis as a small JSON document.
- **Queue:** a named line of messages. A worker reads from the queues it is told to read.
- **Actor:** a Python function that Dramatiq can run from a message. `ping` is an actor.
- **Worker:** the process that takes messages off queues and runs their actors.
- **Middleware:** small plug-ins that run around every message, such as "stop it after 60 seconds".
- **Serializer (encoder):** how a message becomes bytes in Redis. JSON is plain data. Pickle can run code while it is read back, so it is never used here.
- **Readiness:** whether a process can do its job right now. A worker whose Redis is down is alive but not ready.
- **At least once:** a message may be delivered more than once, for example when a worker crashes mid-job. Later lessons make jobs safe to run twice.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **What already exists.** `src/knowledge_service/jobs.py` and `persistence/jobs.py` (from M02 and M03) store a Job in PostgreSQL: its kind, status, and attempts. Nothing runs a Job in the background yet. This lesson adds only the delivery path; M08-T02 connects stored Jobs to it.
- **Redis is not the record.** ADR 0011 says Redis is for short-lived coordination only. If Redis restarts and loses every message, the stored Jobs in PostgreSQL still say what has to run. That is why the container keeps no data on disk.
- **Redis must never evict.** Redis can delete keys when memory runs out. For a queue, a deleted key is a lost message, so the container uses `noeviction`: Redis refuses new writes instead.
- **One place for every limit.** `broker.py` sets the time limit, the age limit, and the retry count. No library default decides them. Readers of the code find all the limits in one file.
- **No automatic retries.** Dramatiq retries a failed message up to 20 times by default. Here it is set to 0, because M08-T04 makes the stored Job own retries. Two places retrying the same work would multiply attempts.
- **A message cannot change its own limits.** Dramatiq reads limits from the message first and from its actor second. Anyone who can write to Redis could send a message that turns retries back on or lifts its time limit. The first middleware, `ActorOwnsOptions`, deletes those options before anything reads them.
- **The worker checks its input again.** A message in Redis may have been written by something other than our code. The `ping` actor checks its token on arrival, not only when it is sent.
- **The namespace carries a version.** All keys live under `knowledge-service.v1`. If a message's shape changes in a way old workers cannot read, the version goes up, and old and new workers never read each other's messages.
- **Readiness has one deadline.** The probe asks Redis `PING` and must get `PONG` within the timeout, two seconds by default. The deadline covers the whole check, including a server that sends its reply one byte at a time. Any failure means "not ready", never a crash.
- **Time limits need the boot signal.** Dramatiq's time limit runs a timer thread that starts when a worker process boots. The `dramatiq` command sends that signal. In tests you send it yourself with `broker.emit_after("process_boot")`, or time limits silently do nothing.
- **What the review found.** An independent adversarial review attacked the first version. It found these problems, all now fixed, each with a test that fails on the first version:
  - A message could set `max_retries` for itself and run several times. `ActorOwnsOptions` now drops it.
  - A message could set a huge `time_limit` and run far past the actor's limit. That option is dropped too.
  - A message with `throws: ["x"]` killed a worker thread for good, so later pings were never run. The option is dropped before the retry middleware reads it.
  - A server that sent its reply one byte at a time kept the readiness check running for over a minute. The check now runs under one overall deadline.
  - A server that answered with `+OK`, a URL option such as `?timeout=1`, or a host name too long to encode crashed the check with an untyped error. Every failure now means "not ready".
  - A readiness timeout of `1e-300` passed validation and made a healthy Redis look down. The timeout now has a floor of 0.05 seconds.
  - Invalid settings crashed the probe with a traceback. It now exits 2 with one line.
  - The JSON test passed even without the line that sets JSON. It now sets pickle first, so the test fails if that line is missing.
- **Known limits.** These stay as they are for now:
  - A message that is not valid JSON makes Dramatiq log an error and leave the message unacknowledged. It is never unpickled. Recovering stuck messages is M08-T08.
  - Local Redis has no password and listens on 127.0.0.1 only. Anyone on this machine can write to it, which is why the worker rechecks its input. A managed Redis with authentication comes with AWS in M13.

## Walkthrough

Start Docker before you begin. Every command runs from the repository root.

### Step 1 — No coding in this step: write the invariant and your predictions

Before you change anything, write down what must be true when the lesson is done, and what you expect. Step 10 checks the last prediction.

Create `docs/evidence/M08-T01-introduce-dramatiq-and-redis.md`:

```markdown
# M08-T01 — Introduce Dramatiq and Redis evidence

## Invariant

A worker runs a message only through an actor declared on a broker built
by `build_broker`: JSON messages, the `knowledge-service.v1` namespace,
named queues, the actor's own time limit, and no Dramatiq retries, whatever
options the message carries. The worker rechecks every argument it reads
from Redis. Readiness is true only while Redis answers PING within one
deadline, and every failure reads as "not ready", never as a crash.
PostgreSQL, not Redis, stays the record of every Job.

## Owners

- Retries: the stored Job (M08-T04); Dramatiq retries nothing.
- Timeouts: the actor's time limit; Redis calls make one attempt each.
- Authorization: none yet; `ping` reads and changes no data.

## Expected failing check

Before the change, `uv run pytest tests/test_worker.py -q` fails with
`ERROR: file or directory not found: tests/test_worker.py`.

## Predictions

- What `make worker-ready` prints when the Redis container is stopped:
- How many times a ping that always fails runs before it reaches the dead-letter queue:
- What happens to queued messages when you run `make redis-down`:
- How many tests in `tests/test_worker.py` fail if `ActorOwnsOptions()` is removed from `queue_middleware`:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Check:** the file exists, and each prediction has your answer after the colon.

### Step 2 — Coding step: the dependency and two settings

This step installs Dramatiq with its Redis client and teaches the settings where Redis lives.

**In plain words:** the service learns a new address, the Redis URL, and how long a health check may wait for Redis. The URL is treated like a password, because a real one can contain one. Nothing connects to Redis yet.

| What | Value | Why |
|---|---|---|
| Package | `dramatiq[redis]>=2.2,<3` | Dramatiq 2.2.1 and redis-py 8.1.0 get locked; `<3` stops a breaking major version |
| `redis_url` | `SecretStr \| None`, default `None` | it may hold a password, so it never prints |
| `redis_health_timeout_seconds` | 2.0, between 0.05 and 30 | a tiny value would make a healthy Redis look down |

**Action 1:** run:

```bash
uv add "dramatiq[redis]>=2.2,<3"
```

**Action 2:** in `src/knowledge_service/settings.py`, add the two fields after `database_statement_timeout_ms`:

```python
    # The Redis broker that carries worker messages; it may hold a password.
    redis_url: SecretStr | None = None
    redis_health_timeout_seconds: float = Field(
        default=2.0, ge=0.05, le=30.0, allow_inf_nan=False
    )
```

**Check:** `uv run python -c "import dramatiq; print(dramatiq.__version__)"` prints `2.2.1`, and `uv run pytest tests/test_settings.py -q` still passes.

### Step 3 — Coding step: the local Redis container and Makefile targets

This step adds a Redis you can start and stop with one command, plus the commands that run the worker.

**In plain words:** a small configuration file tells Docker to run one exact Redis image on port 6380 of this machine only. Redis is told never to delete keys to save memory, and not to save anything to disk. New `make` commands start Redis, run a worker, ask whether the worker is ready, and send one ping.

| Setting | Value | Why |
|---|---|---|
| Image | `redis:8.2.3-alpine` pinned by digest | every machine runs the same bytes |
| Port | `127.0.0.1:6380` | nothing outside this machine can reach it; 6380 avoids clashing with another Redis on 6379 |
| `--maxmemory-policy noeviction` | refuse writes when full | an evicted key is a lost message |
| `--save ""`, `--appendonly no` | nothing on disk | PostgreSQL holds the record; a restart empties the queues |
| Health check | `redis-cli ping` | `make redis-up` waits until Redis answers |

**Action 1:** create `deploy/redis/compose.yaml`:

```yaml
# Local, non-production Redis for the worker queue (M08-T01).
#
# Start it with `make redis-up`. Redis only carries messages between the API
# and workers; PostgreSQL holds every durable record (ADR 0011). The port is
# published on 127.0.0.1 only, so nothing outside this machine can reach it.
name: knowledge-service-redis

services:
  redis:
    # Redis 8.2.3, pinned by digest so every machine runs the same image.
    image: redis:8.2.3-alpine@sha256:08ad0b1d280850169a790dba1393ff7a90aef951fc19632cf4d3ce4f78e679ba
    command:
      - redis-server
      # Never evict a key to free memory: an evicted key is a lost message.
      - --maxmemory-policy
      - noeviction
      # No snapshots or append-only file: a restart empties the queues, and
      # the stored Jobs in PostgreSQL say what still has to run.
      - --save
      - ""
      - --appendonly
      - "no"
    ports:
      - "127.0.0.1:6380:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 2s
      retries: 30
```

**Action 2:** in the `Makefile`, add the new targets to the `.PHONY` line:

```makefile
.PHONY: do_good setup format format-check lint typecheck test-unit test-integration ml-check keycloak-up keycloak-down keycloak-reset redis-up redis-down worker worker-ready worker-ping check hooks-install hooks docs-check
```

**Action 3:** add this section just before the `# Combined checks and git hooks` section. The `@` in front of a command stops `make` from printing it, so a URL with a password never reaches your terminal. `WORKER_ENV` uses your own `KNOWLEDGE_SERVICE_REDIS_URL` when it is set, and the local container otherwise. `make worker` starts Dramatiq from `.venv` rather than through `uv run`: with `uv run`, one Ctrl+C reached Dramatiq twice, and the second signal killed the worker mid-job instead of stopping it gracefully. It runs `uv sync` first, so `.venv` stays current.

```makefile
# ---------------------------------------------------------------------------
# Local queue: Redis and the worker
# ---------------------------------------------------------------------------

# Redis for local work only (M08-T01). It holds no secret and listens on
# 127.0.0.1 only. The worker reads KNOWLEDGE_SERVICE_REDIS_URL, which
# defaults here to that container.
REDIS_COMPOSE = docker compose -f deploy/redis/compose.yaml
REDIS_PROJECT = knowledge-service-redis
LOCAL_REDIS_URL ?= redis://127.0.0.1:6380/0
WORKER_ENV = KNOWLEDGE_SERVICE_REDIS_URL="$${KNOWLEDGE_SERVICE_REDIS_URL:-$(LOCAL_REDIS_URL)}"

redis-up: ## Start the local Redis for the worker queue
	$(REDIS_COMPOSE) up -d --wait

redis-down: ## Stop the local Redis; its queues are emptied
	docker compose -p $(REDIS_PROJECT) down

# The worker runs from .venv directly, not through `uv run`: uv passes Ctrl+C
# on to Dramatiq, which already got it from the terminal, and a second signal
# makes Dramatiq kill its worker instead of stopping it gracefully. The sync
# first keeps .venv current, as `uv run` would.
worker: ## Run one worker process that reads every queue
	@uv sync --locked --quiet
	@$(WORKER_ENV) .venv/bin/dramatiq knowledge_service.worker.main --processes 1 --threads 2

worker-ready: ## Exit 0 only when the worker's Redis broker answers
	@$(WORKER_ENV) uv run python -m knowledge_service.worker.health

worker-ping: ## Queue one harmless ping for a running worker
	@$(WORKER_ENV) uv run python -m knowledge_service.worker.actors
```

**Check:** `make redis-up` ends with `Container knowledge-service-redis-redis-1 Healthy`, and `docker exec knowledge-service-redis-redis-1 redis-cli config get maxmemory-policy` prints `noeviction`.

### Step 4 — Coding step: the broker

This step builds the one function every worker and sender uses to talk to Redis.

**In plain words:** this file is the rule book for the queue. It names the queues, sets how long a job may run and how old a message may get, turns off automatic retries, and makes every message plain JSON. It also adds one guard that throws away any limit a message tries to set for itself. Building the broker does not connect to Redis; the first send or receive does.

| Piece | What it holds or does |
|---|---|
| `NAMESPACE` | `knowledge-service.v1`, the prefix of every Redis key; the version goes up when a message's shape changes |
| `DEFAULT_TIME_LIMIT_MS`, `MAX_MESSAGE_AGE_MS` | 60 seconds per job by default; a message older than one day is dropped unrun |
| `QueueName` | the only two queues: `maintenance` (pings, purges) and `synchronization` |
| `WorkerConfigurationError` | the typed error when no Redis URL is configured |
| `ActorOwnsOptions` | deletes every limit option a message carries, so only its actor's limits apply |
| `queue_middleware` | the full, ordered middleware list; `ActorOwnsOptions` must come first |
| `redis_client` | a Redis client with connect and read timeouts and one attempt per call |
| `build_broker` | sets JSON, builds the `RedisBroker`, and declares every queue |
| `broker_from_settings` | the broker from `Settings`, or `WorkerConfigurationError` |

The `broker.actor_options` set holds every option name the middleware understands, such as `time_limit`, `max_retries`, and `throws`. Deleting exactly those from the message leaves Dramatiq's own bookkeeping, such as the retry counter, untouched.

**Action 1:** create `src/knowledge_service/worker/__init__.py`:

```python
"""Run queued work in a separate process: the Redis broker, actors, and health."""
```

**Action 2:** create `src/knowledge_service/worker/broker.py`:

```python
"""The Redis broker that carries Dramatiq messages from callers to workers.

Redis only delivers messages. It is not the record of any Job: PostgreSQL is
(ADR 0011). Every limit a message can hit is set here, in one place, so no
library default decides how long work may run or whether it is retried.
"""

from enum import StrEnum
from typing import Final, override

import dramatiq
import redis
from dramatiq.broker import MessageProxy
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import (
    AgeLimit,
    Middleware,
    Retries,
    ShutdownNotifications,
    TimeLimit,
)
from redis.backoff import NoBackoff
from redis.retry import Retry

from knowledge_service.settings import Settings

# Change the version when a message's shape changes, so old workers never
# read messages they cannot understand.
NAMESPACE: Final = "knowledge-service.v1"
DEFAULT_TIME_LIMIT_MS: Final = 60_000
MAX_MESSAGE_AGE_MS: Final = 24 * 60 * 60 * 1000
REDIS_CONNECT_TIMEOUT_SECONDS: Final = 2.0
REDIS_SOCKET_TIMEOUT_SECONDS: Final = 5.0


class QueueName(StrEnum):
    """Every queue a worker may read. A message names one of these."""

    MAINTENANCE = "maintenance"
    SYNCHRONIZATION = "synchronization"


class WorkerConfigurationError(RuntimeError):
    """The worker cannot start because its configuration is incomplete."""


class ActorOwnsOptions(Middleware):
    """Drop every limit a message sets for itself, so its actor's limits apply.

    Dramatiq reads a message's own options before its actor's. Anyone who can
    write to Redis could otherwise turn retries back on, lift a time limit,
    or name an option that kills the worker thread.
    """

    @override
    def before_process_message(
        self, broker: dramatiq.Broker, message: MessageProxy
    ) -> None:
        for name in broker.actor_options & message.options.keys():
            del message.options[name]


def queue_middleware() -> list[Middleware]:
    """The only middleware a broker runs, each with an explicit limit.

    `ActorOwnsOptions` comes first, so the others never see a message's own
    limits.
    """
    return [
        ActorOwnsOptions(),
        AgeLimit(max_age=MAX_MESSAGE_AGE_MS),
        TimeLimit(time_limit=DEFAULT_TIME_LIMIT_MS),
        ShutdownNotifications(),
        # Dramatiq never retries on its own: the stored Job owns retries.
        Retries(max_retries=0),
    ]


def declare_queues(broker: dramatiq.Broker) -> None:
    """Declare every known queue, so a worker listens to all of them."""
    for queue in QueueName:
        broker.declare_queue(queue)


def redis_client(
    redis_url: str,
    *,
    connect_timeout_seconds: float = REDIS_CONNECT_TIMEOUT_SECONDS,
    socket_timeout_seconds: float = REDIS_SOCKET_TIMEOUT_SECONDS,
) -> redis.Redis:
    """A Redis client whose every call ends within a bounded time.

    It makes one attempt per call: whoever calls Redis owns any retry.
    """
    # redis-py leaves the keyword arguments of from_url untyped.
    return redis.Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        redis_url,
        socket_connect_timeout=connect_timeout_seconds,
        socket_timeout=socket_timeout_seconds,
        retry=Retry(NoBackoff(), 0),
    )


def build_broker(redis_url: str, *, namespace: str = NAMESPACE) -> RedisBroker:
    """A Redis broker with JSON messages, explicit limits, and known queues.

    Building it does not connect to Redis; the first send or consume does.
    """
    # Messages are JSON: a worker must never unpickle data read from Redis.
    dramatiq.set_encoder(dramatiq.JSONEncoder())
    broker = RedisBroker(
        client=redis_client(redis_url),
        namespace=namespace,
        middleware=queue_middleware(),
    )
    declare_queues(broker)
    return broker


def broker_from_settings(settings: Settings) -> RedisBroker:
    """The broker for this process, or a typed error when Redis is not set."""
    if settings.redis_url is None:
        raise WorkerConfigurationError(
            "KNOWLEDGE_SERVICE_REDIS_URL is required to run a worker"
        )
    return build_broker(settings.redis_url.get_secret_value())
```

**Check:** `uv run python -c "from knowledge_service.worker.broker import build_broker; print(build_broker('redis://127.0.0.1:6380/0').get_declared_queues())"` prints both queue names, without needing Redis to be running.

### Step 5 — Coding step: the ping actor and the worker entry point

This step adds the first job and the module the `dramatiq` command loads.

**In plain words:** the ping job takes one short token and writes it to the log. The sender checks the token before queuing it, and the worker checks it again when it arrives, because anything with access to Redis could have written the message. The entry module builds the broker from settings and registers the ping job, so the `dramatiq` command knows what to run.

| Piece | What it does |
|---|---|
| `check_ping_token` | accepts only 1 to 64 ASCII letters, digits, `_`, or `-`, as a real `str`; anything else raises `InvalidPing` |
| `declare_actors` | registers `ping` on a given broker, on the `maintenance` queue, with a 5-second limit and no retries; the `record_ping` callback receives each token |
| `send_ping` | checks the token, then queues one ping |
| `log_ping` | the worker's callback: one log line per ping |
| `main` in `actors.py` | sends one ping with a random token; `make worker-ping` runs it |
| `main.py` | builds the broker, makes it Dramatiq's global broker, and declares the actors |

`declare_actors` takes the broker and the callback as arguments instead of using a global broker. Tests can then declare the same actor on a stand-in broker and collect tokens in a list. The `ping_time_limit_ms` argument exists so a test can use a 200-millisecond limit; the message itself can no longer change it.

**Action 1:** create `src/knowledge_service/worker/actors.py`:

```python
"""Actors: the jobs a worker knows how to run.

For now there is one, `ping`, a harmless job that proves a message travels
from a caller through Redis to a worker. A ping carries one short token and
changes nothing.
"""

import logging
import re
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import dramatiq

from knowledge_service.settings import Settings
from knowledge_service.worker.broker import QueueName, broker_from_settings

logger = logging.getLogger(__name__)

PING_ACTOR: Final = "ping"
PING_TIME_LIMIT_MS: Final = 5_000
_PING_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,64}")

type PingRecorder = Callable[[str], None]


class InvalidPing(ValueError):
    """A ping token is not 1 to 64 ASCII letters, digits, `_`, or `-`."""


@dataclass(frozen=True, slots=True)
class Actors:
    """The actors declared on one broker."""

    ping: dramatiq.Actor[[str], None]


def check_ping_token(token: object) -> str:
    """The token itself, or InvalidPing when it is not a short safe word."""
    if type(token) is not str or _PING_TOKEN.fullmatch(token) is None:
        raise InvalidPing("a ping token is 1 to 64 letters, digits, '_' or '-'")
    return token


def declare_actors(
    broker: dramatiq.Broker,
    *,
    record_ping: PingRecorder,
    ping_time_limit_ms: int = PING_TIME_LIMIT_MS,
) -> Actors:
    """Declare every actor on `broker`; `record_ping` sees each ping's token."""

    def ping(token: str) -> None:
        # The message came from Redis, so its argument is checked again here.
        record_ping(check_ping_token(token))

    declare = dramatiq.actor(
        broker=broker,
        actor_name=PING_ACTOR,
        queue_name=QueueName.MAINTENANCE,
        time_limit=ping_time_limit_ms,
        max_retries=0,
    )
    return Actors(ping=declare(ping))


def send_ping(actors: Actors, token: str) -> dramatiq.Message[None]:
    """Queue one ping, refusing a token the worker would refuse."""
    return actors.ping.send(check_ping_token(token))


def log_ping(token: str) -> None:
    """The worker's recorder: one log line per ping."""
    logger.info("ping received: %s", token)


def main() -> int:
    """Send one ping to the configured Redis and print its token."""
    broker = broker_from_settings(Settings())
    try:
        token = uuid.uuid4().hex
        send_ping(declare_actors(broker, record_ping=log_ping), token)
        sys.stdout.write(f"sent ping {token}\n")
    finally:
        broker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Action 2:** create `src/knowledge_service/worker/main.py`:

```python
"""The worker process: `dramatiq knowledge_service.worker.main`.

Importing this module builds the broker from settings, makes it Dramatiq's
global broker, and declares every actor on it. Dramatiq then reads the
declared queues.
"""

import dramatiq

from knowledge_service.settings import Settings
from knowledge_service.worker.actors import declare_actors, log_ping
from knowledge_service.worker.broker import broker_from_settings

broker = broker_from_settings(Settings())
dramatiq.set_broker(broker)
actors = declare_actors(broker, record_ping=log_ping)
```

**Check:** `uv run python -c "from knowledge_service.worker.actors import check_ping_token; check_ping_token('two words')"` raises `InvalidPing`.

### Step 6 — Coding step: the readiness probe

This step adds the check that says whether a worker can do its job right now.

**In plain words:** the probe asks Redis "are you there?" and waits a short time for the answer. If the answer comes in time, the worker is ready. If Redis is down, slow, confused, or the URL is wrong, the worker is not ready, and the probe never crashes. It runs as a command that exits 0 or 1, so Docker, ECS, or Kubernetes can call it later.

| Piece | What it does |
|---|---|
| `_answers_ping` | builds a client and sends `PING`; any exception means `False` |
| `broker_ready` | runs `_answers_ping` in a background thread and waits at most `timeout_seconds` for it |
| `main` | exit 0 and `ready`; exit 1 when Redis did not answer; exit 2 when settings are missing or invalid |

Why a thread: the client's timeout applies to each read, not to the whole check. A server that sends one byte every 0.4 seconds never trips a 0.5-second read timeout, yet the check would take minutes. Waiting on the thread gives one deadline for the whole check. The thread is marked daemon, so it never keeps the process alive.

The `except Exception` is deliberate. redis-py raises several exception types for broken input, including `AttributeError` and `TypeError`. For a readiness probe, every one of them means "not ready".

**Action:** create `src/knowledge_service/worker/health.py`:

```python
"""Worker readiness: a worker is ready only while its broker answers.

Run `python -m knowledge_service.worker.health` as a readiness probe. It exits
0 when Redis answers within the timeout, 1 when it does not, and 2 when the
settings are missing or invalid. It never prints the URL, which may hold a password.
"""

import sys
import threading

from pydantic import ValidationError

from knowledge_service.settings import Settings
from knowledge_service.worker.broker import redis_client


def _answers_ping(redis_url: str, timeout_seconds: float) -> bool:
    try:
        client = redis_client(
            redis_url,
            connect_timeout_seconds=timeout_seconds,
            socket_timeout_seconds=timeout_seconds,
        )
        try:
            # redis-py leaves the keyword arguments of ping untyped.
            return client.ping() is True  # pyright: ignore[reportUnknownMemberType]
        finally:
            client.close()
    # A probe answers "not ready" for any failure: a bad URL, a server that
    # is down, or one that replies with something redis-py cannot read.
    except Exception:
        return False


def broker_ready(redis_url: str, *, timeout_seconds: float) -> bool:
    """True when Redis answers PING within `timeout_seconds`, else False.

    The deadline covers the whole check. A server that trickles its reply
    byte by byte is left to a daemon thread, which may outlive the call but
    never keeps the process alive. Call it once per probe process.
    """
    answers: list[bool] = []
    probe = threading.Thread(
        target=lambda: answers.append(_answers_ping(redis_url, timeout_seconds)),
        name="redis-readiness-probe",
        daemon=True,
    )
    probe.start()
    probe.join(timeout_seconds)
    return answers == [True]


def main() -> int:
    try:
        settings = Settings()
    except ValidationError:
        sys.stderr.write("not ready: the worker settings are invalid\n")
        return 2
    if settings.redis_url is None:
        sys.stderr.write("not ready: KNOWLEDGE_SERVICE_REDIS_URL is not set\n")
        return 2
    if not broker_ready(
        settings.redis_url.get_secret_value(),
        timeout_seconds=settings.redis_health_timeout_seconds,
    ):
        sys.stderr.write("not ready: the Redis broker did not answer\n")
        return 1
    sys.stdout.write("ready\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Check:** with Redis running, `make worker-ready` prints `ready`.

### Step 7 — Coding step: the unit tests

This step proves each promise without real Redis, using Dramatiq's in-memory stand-in broker, `StubBroker`.

**In plain words:** the tests start a real Dramatiq worker against an in-memory broker and check that a ping arrives. They check the limits, and that bad tokens are refused on both sides. They send messages that try to change their own limits and check that the limits hold. Then they aim the readiness probe at closed ports, silent servers, slow servers, and nonsense, and check it always says "not ready" in time.

| Test group | What it proves |
|---|---|
| `test_worker_consumes_a_ping`, `test_send_ping_accepts_the_longest_token` | success, and the 64-character boundary |
| `test_ping_runs_on_...`, `test_the_broker_runs_only_...`, `test_build_broker_...` | queues, limits, middleware order, JSON, namespace |
| `test_send_ping_refuses_an_unsafe_token` | typed denial `InvalidPing` on the sender side; nothing is queued |
| `test_worker_refuses_a_hostile_message_without_retrying` | the worker rechecks; one attempt; one dead letter |
| `test_a_ping_past_its_time_limit_is_stopped` | the time limit stops a slow job |
| `test_a_message_cannot_...` (three tests) | a message cannot lift its time limit, turn retries on, or stop the next ping |
| `test_broker_from_settings_requires_a_redis_url` | typed failure `WorkerConfigurationError` |
| `test_broker_is_not_ready_...`, `test_readiness_...` | not ready for closed ports, bad URLs, silent, trickling, or confused servers, within the deadline |
| `test_health_command_...` | exit codes 1 and 2, and the password never printed |
| `test_worker_entry_point_...` | `main.py` sets the global broker and declares `ping` |

Two helpers matter. `run_worker_until_empty` starts a worker and waits until the `maintenance` queue is empty; with `fail_fast=True`, the first actor error is raised in the test. `serve_once` is a tiny fake server that sends a chosen reply, optionally one byte at a time.

**Action:** create `tests/test_worker.py`:

```python
"""Tests for the worker package: broker setup, the ping actor, and readiness."""

import importlib
import os
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import dramatiq
import pytest
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import (
    AgeLimit,
    Retries,
    ShutdownNotifications,
    TimeLimit,
    TimeLimitExceeded,
)

from knowledge_service.settings import Settings
from knowledge_service.worker import health
from knowledge_service.worker.actors import (
    PING_TIME_LIMIT_MS,
    Actors,
    InvalidPing,
    declare_actors,
    send_ping,
)
from knowledge_service.worker.broker import (
    NAMESPACE,
    ActorOwnsOptions,
    QueueName,
    WorkerConfigurationError,
    broker_from_settings,
    build_broker,
    declare_queues,
    queue_middleware,
)
from knowledge_service.worker.health import broker_ready


@pytest.fixture
def stub_broker() -> Iterator[StubBroker]:
    broker = StubBroker(middleware=queue_middleware())
    declare_queues(broker)
    # The dramatiq command sends this signal; it starts the time-limit timer.
    broker.emit_after("process_boot")
    yield broker
    broker.close()


@pytest.fixture
def received() -> list[str]:
    return []


@pytest.fixture
def actors(stub_broker: StubBroker, received: list[str]) -> Actors:
    return declare_actors(stub_broker, record_ping=received.append)


def run_worker_until_empty(broker: StubBroker) -> None:
    """Consume every queued message, raising the first actor failure."""
    worker = dramatiq.Worker(broker, worker_timeout=100)
    worker.start()
    try:
        broker.join(QueueName.MAINTENANCE, timeout=10_000, fail_fast=True)
        worker.join()
    finally:
        worker.stop()


def closed_port() -> int:
    """A local port with nothing listening on it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_worker_consumes_a_ping(
    stub_broker: StubBroker, actors: Actors, received: list[str]
) -> None:
    send_ping(actors, "first-ping_1")

    run_worker_until_empty(stub_broker)

    assert received == ["first-ping_1"]


def test_ping_runs_on_the_maintenance_queue_with_explicit_limits(
    actors: Actors,
) -> None:
    assert actors.ping.queue_name == QueueName.MAINTENANCE
    assert actors.ping.options["time_limit"] == PING_TIME_LIMIT_MS
    assert actors.ping.options["max_retries"] == 0


def test_the_broker_runs_only_the_chosen_middleware() -> None:
    kinds = [type(middleware) for middleware in queue_middleware()]

    assert kinds == [
        ActorOwnsOptions,
        AgeLimit,
        TimeLimit,
        ShutdownNotifications,
        Retries,
    ]


def test_build_broker_declares_queues_and_json_without_connecting() -> None:
    dramatiq.set_encoder(dramatiq.PickleEncoder())
    broker = build_broker(f"redis://127.0.0.1:{closed_port()}/0")
    try:
        assert broker.namespace == NAMESPACE
        assert set(QueueName) <= broker.get_declared_queues()
        assert type(dramatiq.get_encoder()) is dramatiq.JSONEncoder
    finally:
        broker.close()


@pytest.mark.parametrize(
    "token",
    ["", "two words", "x" * 65, "line\nbreak", "ping;DROP", "пінг"],
)
def test_send_ping_refuses_an_unsafe_token(
    stub_broker: StubBroker, actors: Actors, token: str
) -> None:
    with pytest.raises(InvalidPing):
        send_ping(actors, token)

    assert stub_broker.queues[QueueName.MAINTENANCE].qsize() == 0


def test_send_ping_accepts_the_longest_token(
    stub_broker: StubBroker, actors: Actors, received: list[str]
) -> None:
    send_ping(actors, "x" * 64)

    run_worker_until_empty(stub_broker)

    assert received == ["x" * 64]


@pytest.mark.parametrize("argument", ["two words", 123, None, ["list"]])
def test_worker_refuses_a_hostile_message_without_retrying(
    stub_broker: StubBroker, actors: Actors, received: list[str], argument: object
) -> None:
    # Another producer with Redis access skips send_ping's check.
    actors.ping.send_with_options(args=(argument,))  # pyright: ignore[reportUnknownMemberType]

    with pytest.raises(InvalidPing):
        run_worker_until_empty(stub_broker)

    assert received == []
    assert len(stub_broker.dead_letters) == 1


def test_a_ping_past_its_time_limit_is_stopped(stub_broker: StubBroker) -> None:
    finished: list[str] = []

    def slow_recorder(token: str) -> None:
        for _ in range(100):
            time.sleep(0.05)
        finished.append(token)

    slow = declare_actors(
        stub_broker, record_ping=slow_recorder, ping_time_limit_ms=200
    )
    send_ping(slow, "slow")

    with pytest.raises(TimeLimitExceeded):
        run_worker_until_empty(stub_broker)

    assert finished == []


def test_broker_from_settings_requires_a_redis_url() -> None:
    with pytest.raises(WorkerConfigurationError):
        broker_from_settings(Settings(redis_url=None))


def test_broker_is_not_ready_when_nothing_listens() -> None:
    url = f"redis://127.0.0.1:{closed_port()}/0"

    assert broker_ready(url, timeout_seconds=1.0) is False


@pytest.mark.parametrize("url", ["not-a-url", "http://127.0.0.1:1/0", ""])
def test_broker_is_not_ready_for_a_malformed_url(url: str) -> None:
    assert broker_ready(url, timeout_seconds=1.0) is False


def test_readiness_ends_within_its_timeout_when_redis_never_answers() -> None:
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        port = silent.getsockname()[1]
        accepted: list[socket.socket] = []

        def accept_and_stay_silent() -> None:
            connection, _ = silent.accept()
            accepted.append(connection)

        threading.Thread(target=accept_and_stay_silent, daemon=True).start()
        started = time.monotonic()

        ready = broker_ready(f"redis://127.0.0.1:{port}/0", timeout_seconds=0.3)

        elapsed = time.monotonic() - started
        for connection in accepted:
            connection.close()

    assert ready is False
    assert elapsed < 2.0


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for variable in tuple(os.environ):
        if variable.upper().startswith("KNOWLEDGE_SERVICE_"):
            monkeypatch.delenv(variable)
    monkeypatch.chdir(tmp_path)


@pytest.mark.usefixtures("clean_environment")
def test_health_command_exits_2_without_a_redis_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert health.main() == 2
    assert "not set" in capsys.readouterr().err


@pytest.mark.usefixtures("clean_environment")
def test_health_command_exits_1_without_printing_the_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"redis://:s3cret-password@127.0.0.1:{closed_port()}/0"
    monkeypatch.setenv("KNOWLEDGE_SERVICE_REDIS_URL", url)

    assert health.main() == 1

    output = capsys.readouterr()
    assert "s3cret-password" not in output.out + output.err
    assert "did not answer" in output.err


@pytest.mark.usefixtures("clean_environment")
def test_worker_entry_point_declares_ping_on_its_global_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "KNOWLEDGE_SERVICE_REDIS_URL", f"redis://127.0.0.1:{closed_port()}/0"
    )
    monkeypatch.delitem(sys.modules, "knowledge_service.worker.main", raising=False)

    entry = importlib.import_module("knowledge_service.worker.main")
    try:
        assert dramatiq.get_broker() is entry.broker
        assert entry.broker.get_declared_actors() == {"ping"}
    finally:
        entry.broker.close()
        dramatiq.set_broker(StubBroker())


def test_a_message_cannot_lift_its_actors_time_limit(stub_broker: StubBroker) -> None:
    finished: list[str] = []

    def slow_recorder(token: str) -> None:
        for _ in range(100):
            time.sleep(0.05)
        finished.append(token)

    slow = declare_actors(
        stub_broker, record_ping=slow_recorder, ping_time_limit_ms=200
    )
    slow.ping.send_with_options(args=("slow",), time_limit=60_000_000)  # pyright: ignore[reportUnknownMemberType]

    with pytest.raises(TimeLimitExceeded):
        run_worker_until_empty(stub_broker)

    assert finished == []


def test_a_message_cannot_turn_retries_back_on(stub_broker: StubBroker) -> None:
    attempts: list[object] = []

    def count_attempts(token: str) -> None:
        attempts.append(token)
        raise RuntimeError("planted failure")

    failing = declare_actors(stub_broker, record_ping=count_attempts)
    failing.ping.send_with_options(  # pyright: ignore[reportUnknownMemberType]
        args=("again",), max_retries=5, min_backoff=1, max_backoff=1
    )

    with pytest.raises(RuntimeError):
        run_worker_until_empty(stub_broker)

    assert attempts == ["again"]
    assert len(stub_broker.dead_letters) == 1


@pytest.mark.parametrize(
    "options",
    [{"throws": ["x"]}, {"on_retry_exhausted": "ping"}, {"retry_when": "x"}],
)
def test_a_message_option_cannot_stop_the_next_ping(
    stub_broker: StubBroker,
    actors: Actors,
    received: list[str],
    options: dict[str, Any],
) -> None:
    actors.ping.send_with_options(args=("two words",), **options)  # pyright: ignore[reportUnknownMemberType]
    send_ping(actors, "after")
    worker = dramatiq.Worker(stub_broker, worker_timeout=100, worker_threads=1)
    worker.start()
    try:
        stub_broker.join(QueueName.MAINTENANCE, timeout=10_000, fail_fast=False)
        worker.join()
    finally:
        worker.stop()

    assert received == ["after"]
    assert len(stub_broker.dead_letters) == 1


def serve_once(reply: bytes, *, pause_seconds: float = 0.0) -> int:
    """A one-connection server that sends `reply`, one byte per pause."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()

    def answer() -> None:
        with server:
            connection, _ = server.accept()
            with connection:
                connection.recv(1024)
                for byte in reply:
                    try:
                        connection.sendall(bytes([byte]))
                    except OSError:
                        return
                    time.sleep(pause_seconds)
                time.sleep(1.0)

    threading.Thread(target=answer, daemon=True).start()
    return server.getsockname()[1]


def test_readiness_deadline_covers_a_reply_that_trickles_in() -> None:
    port = serve_once(b"$100\r\n" + b"x" * 100 + b"\r\n", pause_seconds=0.2)
    started = time.monotonic()

    ready = broker_ready(f"redis://127.0.0.1:{port}/0", timeout_seconds=0.5)

    assert ready is False
    assert time.monotonic() - started < 1.5


@pytest.mark.parametrize("reply", [b"+OK\r\n", b"+PANG\r\n", b"garbage\r\n"])
def test_readiness_is_false_for_a_reply_redis_py_cannot_read(reply: bytes) -> None:
    port = serve_once(reply)

    assert broker_ready(f"redis://127.0.0.1:{port}/0", timeout_seconds=1.0) is False


@pytest.mark.parametrize(
    "query", ["timeout=1", "socket_timeout=-1", "socket_timeout=nan", "driver_info=y"]
)
def test_readiness_is_false_for_hostile_url_options(query: str) -> None:
    url = f"redis://127.0.0.1:{closed_port()}/0?{query}"

    assert broker_ready(url, timeout_seconds=1.0) is False


def test_readiness_is_false_for_a_host_label_too_long_to_encode() -> None:
    url = f"redis://{'a' * 64}.invalid:6379/0"

    assert broker_ready(url, timeout_seconds=1.0) is False


@pytest.mark.usefixtures("clean_environment")
def test_health_command_exits_2_for_invalid_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KNOWLEDGE_SERVICE_REDIS_HEALTH_TIMEOUT_SECONDS", "0.001")

    assert health.main() == 2
    assert "invalid" in capsys.readouterr().err
```

**Check:** `uv run pytest tests/test_worker.py -q` reports `40 passed` in a few seconds.

### Step 8 — Coding step: the integration tests against real Redis

This step proves the same path against the real Redis container, because a stand-in broker cannot prove Redis behaves the same way.

**In plain words:** two tests use the Redis from Step 3. One checks that the probe says "ready". The other starts a worker, sends a ping through real Redis, and waits up to ten seconds for it to arrive. Each test run uses a namespace of its own, so it never reads another run's messages, and it cleans up afterwards.

Dramatiq's `flush_all` empties the queues but keeps the key where workers record their heartbeat, so the fixture deletes that key itself. Without it, every run would leave one key behind in database 15.

**Action 1:** create `tests/test_worker_integration.py`:

```python
"""The worker against a real Redis: readiness and one consumed ping."""

import os
import threading
import uuid
from collections.abc import Iterator

import dramatiq
import pytest
from dramatiq.brokers.redis import RedisBroker

from knowledge_service.worker.actors import declare_actors, send_ping
from knowledge_service.worker.broker import build_broker, redis_client
from knowledge_service.worker.health import broker_ready

pytestmark = pytest.mark.integration

REDIS_URL_ENV = "KNOWLEDGE_SERVICE_TEST_REDIS_URL"


@pytest.fixture
def redis_url() -> str:
    configured = os.environ.get(REDIS_URL_ENV)
    if not configured:
        pytest.skip(f"set {REDIS_URL_ENV} to a disposable Redis server")
    return configured


@pytest.fixture
def redis_broker(redis_url: str) -> Iterator[RedisBroker]:
    # A namespace of its own, so the test never reads another run's messages.
    namespace = f"knowledge-service.test-{uuid.uuid4()}"
    broker = build_broker(redis_url, namespace=namespace)
    yield broker
    broker.flush_all()
    broker.close()
    # flush_all empties the queues but keeps the workers' heartbeat key.
    cleaner = redis_client(redis_url)
    try:
        cleaner.delete(f"{namespace}:__heartbeats__")
    finally:
        cleaner.close()


def test_broker_is_ready_when_redis_answers(redis_url: str) -> None:
    assert broker_ready(redis_url, timeout_seconds=2.0) is True


def test_worker_consumes_a_ping_through_redis(redis_broker: RedisBroker) -> None:
    received: list[str] = []
    arrived = threading.Event()

    def record(token: str) -> None:
        received.append(token)
        arrived.set()

    actors = declare_actors(redis_broker, record_ping=record)
    worker = dramatiq.Worker(redis_broker, worker_timeout=100)
    worker.start()
    try:
        send_ping(actors, "through-redis")
        assert arrived.wait(timeout=10)
    finally:
        worker.stop()

    assert received == ["through-redis"]
```

**Action 2:** in the `Makefile`, let `make test-integration` pass the test Redis URL. Database 15 keeps test keys apart from your local work in database 0. Replace the block from the comment above `TEST_DB_CONTAINER` to the end of the `test-integration` recipe with:

```makefile
# Integration tests skip themselves without KNOWLEDGE_SERVICE_TEST_DATABASE_URL.
# When it is unset, the URL is built from the local Docker container, so the
# password is read at run time and never stored in a file.
# The Redis tests use the local container's database 15 unless
# KNOWLEDGE_SERVICE_TEST_REDIS_URL says otherwise, so run `make redis-up` first.
TEST_DB_CONTAINER ?= knowledge-service-postgres
TEST_REDIS_URL ?= redis://127.0.0.1:6380/15

test-integration: ## Run integration tests (needs services)
	@url="$$KNOWLEDGE_SERVICE_TEST_DATABASE_URL"; \
	if [ -z "$$url" ]; then \
		url="$$(uv run python scripts/test_database_url.py "$(TEST_DB_CONTAINER)")"; \
	fi; \
	if [ -z "$$url" ]; then \
		echo "No test database: set KNOWLEDGE_SERVICE_TEST_DATABASE_URL or start the $(TEST_DB_CONTAINER) container."; \
		exit 1; \
	fi; \
	KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$$url" \
	KNOWLEDGE_SERVICE_TEST_REDIS_URL="$${KNOWLEDGE_SERVICE_TEST_REDIS_URL:-$(TEST_REDIS_URL)}" \
	uv run pytest -m "integration and not live"
```

**Check:** with Redis running, `make test-integration` passes, and `uv run pytest tests/test_worker_integration.py -q -m integration` without the variable reports `2 skipped`.

### Step 9 — No coding in this step: run a real worker

This step shows the acceptance criteria with your own eyes: a real worker consumes a ping, and readiness fails when Redis is gone.

**Action 1:** in one terminal, run `make worker`. Wait for `Worker process is ready for action.`

**Action 2:** in a second terminal, run `make worker-ping`. It prints `sent ping <token>`. The first terminal then logs `ping received: <token>` with the same token.

**Action 3:** stop Redis and ask for readiness:

```bash
docker compose -p knowledge-service-redis stop
make worker-ready; echo "exit=$?"
make redis-up
```

You see `not ready: the Redis broker did not answer` and a non-zero exit. After `make redis-up`, `make worker-ready` prints `ready` again. Stop the worker with Ctrl+C.

**Check:** paste the two matching ping lines and the failed readiness output into the "Verification" section of your evidence file.

### Step 10 — No coding in this step: break it on purpose

This step removes the guard against hostile message options, and shows which tests notice.

**Action 1:** in `queue_middleware` in `src/knowledge_service/worker/broker.py`, delete this line:

```python
        ActorOwnsOptions(),
```

**Action 2:** run `uv run pytest tests/test_worker.py -q`. Five tests fail:

- `test_the_broker_runs_only_the_chosen_middleware`
- `test_a_message_cannot_lift_its_actors_time_limit`
- `test_a_message_cannot_turn_retries_back_on`
- `test_a_message_option_cannot_stop_the_next_ping[options0]` (`throws`)
- `test_a_message_option_cannot_stop_the_next_ping[options1]` (`on_retry_exhausted`)

**Action 3:** put the line back and run the tests again: all 40 pass.

**Check:** compare the count with your Step 1 prediction, and note it in the evidence file.

### Step 11 — No coding in this step: documents, gates, evidence, and progress

This step brings the two documents up to date and runs every gate.

**Action 1:** in `docs/architecture.md`, add this row to the code layout table, right after the `persistence` row:

```markdown
| `worker` | Queue transport: the Redis broker and the worker process | `build_broker` (`broker`, a Dramatiq `RedisBroker` with JSON messages, the `knowledge-service.v1` namespace, the `maintenance` and `synchronization` queues, a 60-second default time limit, a one-day message age limit, and no Dramatiq retries; `ActorOwnsOptions` drops any limit a message sets for itself, so only its actor's limits apply; Redis calls make one attempt with bounded timeouts), `declare_actors` / `send_ping` (`actors`, the harmless `ping` job, whose token is checked on both sides), `broker_ready` (`health`, a readiness probe that is false, within one overall deadline, whenever Redis does not answer PING or any part of the check fails), and `main`, the module the `dramatiq` command loads |
```

**Action 2:** in `docs/threat-model.md`, add this row just before the `Local Keycloak` row:

```markdown
| Local Redis, `127.0.0.1:6380` | Developers and local workers | Bound to 127.0.0.1, with no password: anyone on this machine can read or write its queues, so a worker checks every message argument again. It never evicts a key and keeps nothing across a restart |
```

and replace the `Jobs` row with:

```markdown
| Jobs | Workers | Stored jobs with typed errors. The Redis queue carries only the harmless `ping` job so far, as JSON, never pickle |
```

**Action 3:** run every gate:

```bash
make format-check
make lint
make typecheck
make test-unit
make test-integration
make docs-check
```

**Action 4:** finish the evidence file: the gate results under "Verification", the review findings under "Review", the design choices under "Design" and "Decision", and your answers to the reflection questions below. Then mark the task completed in `docs/curriculum/PROGRESS.md` and commit with `m08-t01: introduce dramatiq and redis`.

**Check:** every gate passes, and `git status` is clean after the commit.

## Reflection questions

- A caller sends a ping and gets no error back. Name one way the ping can still never run, and say which later lesson makes that case safe.
- Suppose Redis restarts and every queued message is lost. Which system tells us what still needs to run, and why is that the right place for it?
- Dramatiq can retry failed messages on its own. Why did we switch that off, and what would go wrong if both Dramatiq and the stored Job retried the same work?
- If someone with access to Redis sends a ping message with `max_retries: 100`, what happens now, and which test protects that behavior when someone changes the middleware later?
- What observation would prove this design wrong? For example: what would you have to see to decide that Redis must keep its data on disk after all?

## Completion checklist

- [ ] The evidence file has the invariant, the owners, and your predictions, written before the change.
- [ ] `dramatiq[redis]` is in `pyproject.toml` and `uv.lock`; `Settings` has `redis_url` and `redis_health_timeout_seconds`.
- [ ] `make redis-up` starts a pinned Redis on 127.0.0.1:6380 with `noeviction` and no persistence.
- [ ] `broker.py` sets JSON, the versioned namespace, both queues, the limits, zero retries, and `ActorOwnsOptions` first.
- [ ] `ping` checks its token when sent and when received; `main.py` declares it for the `dramatiq` command.
- [ ] `make worker-ready` prints `ready` with Redis up, and exits non-zero within its deadline with Redis down.
- [ ] `tests/test_worker.py` (40) and `tests/test_worker_integration.py` (2) pass.
- [ ] You saw a real worker log your ping, and you saw readiness fail with Redis stopped.
- [ ] You ran the deliberate break, saw 5 tests fail, and undid it.
- [ ] `docs/architecture.md` and `docs/threat-model.md` describe the worker and local Redis.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
