"""Write the integration-test database URL for the local Docker container.

The URL carries the password, so it is meant only for capture by a caller
such as `make test-integration`, and the script refuses to write it to a
terminal. The credentials are read from the running container at call time,
so the password never lands in a file. Exits non-zero, with a reason that
holds no secret, when the container is missing or does not publish
PostgreSQL's port.
"""

import json
import subprocess
import sys
from urllib.parse import quote

DEFAULT_CONTAINER = "knowledge-service-postgres"
POSTGRES_PORT = "5432/tcp"


def database_url(container: str) -> str:
    """Build a psycopg URL from the container's environment and port mapping."""
    # The executable and arguments are fixed; only the container name varies.
    inspected = subprocess.run(  # noqa: S603
        ["docker", "inspect", container],  # noqa: S607
        capture_output=True,
        check=True,
        text=True,
    )
    details = json.loads(inspected.stdout)[0]
    env = dict(item.split("=", 1) for item in details["Config"]["Env"])
    port = details["NetworkSettings"]["Ports"][POSTGRES_PORT][0]["HostPort"]
    user = quote(env["POSTGRES_USER"], safe="")
    password = quote(env["POSTGRES_PASSWORD"], safe="")
    return (
        f"postgresql+psycopg://{user}:{password}@localhost:{port}/{env['POSTGRES_DB']}"
    )


def main() -> int:
    if sys.stdout.isatty():
        sys.stderr.write("refusing to write credentials to a terminal\n")
        return 2
    # The Makefile passes its TEST_DB_CONTAINER; the default must match it.
    container = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CONTAINER
    try:
        url = database_url(container)
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ) as error:
        # Only the class name: a KeyError or ValueError could quote a secret.
        sys.stderr.write(f"cannot read {container!r}: {type(error).__name__}\n")
        return 1
    sys.stdout.write(f"{url}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
