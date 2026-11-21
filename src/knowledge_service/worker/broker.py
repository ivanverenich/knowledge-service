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
