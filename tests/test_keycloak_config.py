"""The local Keycloak configuration: pinned, exact, and free of credentials."""

import json
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEYCLOAK = ROOT / "deploy/keycloak"
SCRIPT = ROOT / "scripts/keycloak_env.py"
PLACEHOLDER = re.compile(r"^\$\{([A-Z0-9_]+)\}$")
REQUIRED = re.compile(r"^\s+([A-Z0-9_]+): \$\{\1:\?[^}]+\}$", re.MULTILINE)


def realm() -> dict[str, Any]:
    return json.loads((KEYCLOAK / "knowledge-service-realm.json").read_text())


def realm_client(client_id: str = "open-webui") -> dict[str, Any]:
    (found,) = [c for c in realm()["clients"] if c["clientId"] == client_id]
    return found


def compose() -> str:
    return (KEYCLOAK / "compose.yaml").read_text()


def generate(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def secrets_in(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines()
    pairs = [line.split("=", 1) for line in lines if not line.startswith("#")]
    return dict(pairs)


def strings(value: object) -> list[str]:
    """Every string anywhere in a JSON value."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in strings(item)]  # type: ignore[misc]
    if isinstance(value, list):
        return [s for item in value for s in strings(item)]  # type: ignore[misc]
    return []


# --- The realm ------------------------------------------------------------------------


def test_the_realm_has_the_client_groups_roles_and_demo_users() -> None:
    config = realm()

    assert config["realm"] == "knowledge-service"
    assert [client["clientId"] for client in config["clients"]] == [
        "open-webui",
        "admin-cli",
    ]
    assert [group["name"] for group in config["groups"]] == ["engineering", "finance"]
    assert {role["name"] for role in config["roles"]["realm"]} == {
        "user",
        "content-manager",
        "evaluator",
        "administrator",
    }
    assert {
        user["username"]: (user["groups"], user["realmRoles"])
        for user in config["users"]
    } == {
        "alice": (["/engineering"], ["user"]),
        "bob": (["/finance"], ["user", "content-manager"]),
        "carol": ([], ["user", "administrator"]),
    }


def test_each_demo_user_keeps_one_id_across_resets() -> None:
    ids = [UUID(user["id"]) for user in realm()["users"]]

    assert len(set(ids)) == len(ids) == 3


def test_the_client_uses_only_the_code_flow_with_pkce_and_exact_redirects() -> None:
    client = realm_client()

    assert client["publicClient"] is False
    assert client["standardFlowEnabled"] is True
    assert client["implicitFlowEnabled"] is False
    assert client["directAccessGrantsEnabled"] is False
    assert client["serviceAccountsEnabled"] is False
    assert client["attributes"]["pkce.code.challenge.method"] == "S256"
    assert client["redirectUris"] == ["http://localhost:3000/oauth/oidc/callback"]
    assert client["webOrigins"] == ["http://localhost:3000"]
    assert not any("*" in uri for uri in client["redirectUris"] + client["webOrigins"])


def test_a_client_cannot_ask_for_more_scopes_or_roles_than_these() -> None:
    client = realm_client()

    assert client["defaultClientScopes"] == [
        "basic",
        "acr",
        "web-origins",
        "profile",
        "email",
        "roles",
    ]
    assert client["optionalClientScopes"] == []
    assert client["fullScopeAllowed"] is False
    assert realm()["scopeMappings"] == [
        {
            "client": "open-webui",
            "roles": ["user", "content-manager", "evaluator", "administrator"],
        }
    ]


def test_the_built_in_admin_cli_gives_no_tokens_in_this_realm() -> None:
    admin = realm_client("admin-cli")

    assert admin["directAccessGrantsEnabled"] is False
    assert admin["standardFlowEnabled"] is False
    assert admin["implicitFlowEnabled"] is False
    assert admin["serviceAccountsEnabled"] is False


def test_tokens_carry_groups_by_full_path_and_the_api_audience() -> None:
    client = realm_client()
    mappers = {
        mapper["protocolMapper"]: mapper["config"]
        for mapper in client["protocolMappers"]
    }

    groups = mappers["oidc-group-membership-mapper"]
    assert (groups["claim.name"], groups["full.path"]) == ("groups", "true")
    assert groups["access.token.claim"] == "true"
    audience = mappers["oidc-audience-mapper"]
    assert audience["included.custom.audience"] == "knowledge-service-api"
    assert audience["access.token.claim"] == "true"


def test_signing_keys_are_generated_by_keycloak_never_committed() -> None:
    config = realm()
    providers = config["components"]["org.keycloak.keys.KeyProvider"]

    assert config["defaultSignatureAlgorithm"] == "RS256"
    assert {provider["providerId"] for provider in providers} == {
        "rsa-generated",
        "hmac-generated",
        "aes-generated",
    }
    held = {key for provider in providers for key in provider["config"]}
    assert not held & {"privateKey", "certificate", "secret", "kid"}


def test_every_credential_in_the_realm_is_a_placeholder() -> None:
    config = realm()
    values = [realm_client()["secret"]] + [
        credential["value"]
        for user in config["users"]
        for credential in user["credentials"]
    ]

    assert all(PLACEHOLDER.fullmatch(value) for value in values)
    assert all(
        credential["temporary"] is False
        for user in config["users"]
        for credential in user["credentials"]
    )


def test_every_placeholder_is_required_by_compose_and_made_by_the_script(
    tmp_path: Path,
) -> None:
    placeholders = {
        match[1]
        for value in strings(realm())
        if (match := PLACEHOLDER.fullmatch(value)) is not None
    }
    generate(tmp_path / ".env")

    required = set(REQUIRED.findall(compose()))
    assert placeholders <= required
    assert required == set(secrets_in(tmp_path / ".env"))


def test_no_placeholder_hides_inside_a_longer_value() -> None:
    partial = [
        value
        for value in strings(realm())
        if "${" in value and PLACEHOLDER.fullmatch(value) is None
    ]

    assert partial == []


# --- Compose --------------------------------------------------------------------------


def test_the_image_is_pinned_by_version_and_digest() -> None:
    (image,) = re.findall(r"^\s+image: (\S+)$", compose(), re.MULTILINE)

    assert re.fullmatch(
        r"quay\.io/keycloak/keycloak:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", image
    )


def test_keycloak_listens_on_the_loopback_address_only() -> None:
    text = compose()
    (block,) = re.findall(r"^    ports:\n((?:      .*\n)+)", text, re.MULTILINE)

    assert block.splitlines() == ['      - "127.0.0.1:8180:8080"']
    assert "network_mode" not in text
    assert "expose:" not in text


def test_the_realm_is_mounted_read_only_and_imported_on_start() -> None:
    text = compose()

    assert (
        "./knowledge-service-realm.json:"
        "/opt/keycloak/data/import/knowledge-service-realm.json:ro"
    ) in text
    assert 'command: ["start-dev", "--import-realm"]' in text


# --- The secrets script ---------------------------------------------------------------


def test_the_script_creates_random_owner_only_secrets_and_prints_none(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"

    done = generate(path)

    assert done.returncode == 0
    values = secrets_in(path)
    assert len(set(values.values())) == len(values) == 5
    assert all(len(value) >= 32 for value in values.values())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not any(value in done.stdout + done.stderr for value in values.values())
    generate(tmp_path / "other.env")
    other = secrets_in(tmp_path / "other.env")
    assert not set(values.values()) & set(other.values())


def test_an_existing_file_is_kept_because_the_realm_already_uses_it(
    tmp_path: Path,
) -> None:
    path = tmp_path / ".env"
    generate(path)
    before = path.read_text()

    done = generate(path)

    assert done.returncode == 0
    assert "kept" in done.stdout
    assert path.read_text() == before


@pytest.mark.parametrize(
    "content",
    ["", "KS_ALICE_PASSWORD=x\n", "# KS_BOB_PASSWORD=x\n"],
    ids=["empty", "partial", "commented"],
)
def test_a_file_without_every_secret_is_refused_without_changing_it(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / ".env"
    path.write_text(content)

    done = generate(path)

    assert done.returncode == 1
    assert "keycloak-reset" in done.stderr
    assert path.read_text() == content


def test_the_generated_file_is_ignored_by_git() -> None:
    ignored = subprocess.run(
        ["git", "check-ignore", "--quiet", "deploy/keycloak/.env"],  # noqa: S607
        cwd=ROOT,
        check=False,
    )

    assert ignored.returncode == 0
