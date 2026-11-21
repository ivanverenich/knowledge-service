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
