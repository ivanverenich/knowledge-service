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
