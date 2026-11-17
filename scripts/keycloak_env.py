"""Create the local Keycloak's secrets once, in a file Git ignores.

`make keycloak-up` runs this before starting Keycloak. It writes
`deploy/keycloak/.env` with a fresh random value for every secret the
Compose file and the realm need, readable by the owner only, and prints the
file's name but never a value. An existing file is kept as it is, because
the realm imported into Keycloak's volume already uses its values; a file
that lacks a secret is refused, and `make keycloak-reset` starts over.
"""

import os
import secrets
import sys
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "deploy/keycloak/.env"
NAMES = (
    "KC_BOOTSTRAP_ADMIN_PASSWORD",
    "KS_OPEN_WEBUI_CLIENT_SECRET",
    "KS_ALICE_PASSWORD",
    "KS_BOB_PASSWORD",
    "KS_CAROL_PASSWORD",
)


def existing_names(path: Path) -> set[str]:
    """The names an existing file sets, without reading any value out."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return {line.split("=", 1)[0] for line in lines if "=" in line}


def create(path: Path) -> None:
    """Write a new file with a random value per name, owner-readable only."""
    lines = [f"{name}={secrets.token_urlsafe(24)}\n" for name in NAMES]
    # O_EXCL: never replace a file another run created in the meantime.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.writelines(
            ["# Local Keycloak secrets; generated, never commit.\n", *lines]
        )


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_PATH
    if not path.exists():
        create(path)
        sys.stdout.write(f"created {path.name} with {len(NAMES)} secrets\n")
        return 0
    missing = [name for name in NAMES if name not in existing_names(path)]
    if missing:
        sys.stderr.write(
            f"{path.name} lacks {', '.join(missing)}; "
            "run `make keycloak-reset` to start over\n"
        )
        return 1
    sys.stdout.write(f"kept the existing {path.name}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
