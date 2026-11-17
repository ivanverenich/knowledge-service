# M07-T02 — Run Keycloak locally

Task: [M07-T02 — Run Keycloak locally](../curriculum/milestones/M07-identity-and-authorization/M07-T02-run-keycloak-locally.md)

## What you will do

This lesson gives the project its own identity provider: a local Keycloak that knows a few demo users, lets them log in, and hands out signed tokens that say who they are and which groups they belong to. Today no endpoint checks who is calling; from M07-T03 on, the API will check exactly these tokens.

- **Pinned:** one exact Keycloak version, fixed by the image's digest, so every machine runs the same server.
- **A realm:** a separate space in Keycloak for this project, with one client (Open WebUI), one exact address to return to after login, signing keys Keycloak generates itself, two groups, four application roles, and three demo users.
- **No committed credentials:** every password and secret is generated on your machine into a file Git ignores. The realm file holds only placeholders.
- **Reproducible:** `make keycloak-reset` and `make keycloak-up` give a fresh Keycloak from an empty volume, and the same tests log in again.

| The task asks for | Where it happens |
|---|---|
| Pinned local Keycloak configuration | Step 3: `deploy/keycloak/compose.yaml` |
| Realm, clients, redirect URIs, signing keys, demo users and groups | Step 4: `deploy/keycloak/knowledge-service-realm.json` |
| No committed credentials | Step 2: `scripts/keycloak_env.py` and the ignored `deploy/keycloak/.env` |
| Tests: success, highest-risk edge, typed failure or denial | Step 6: `tests/test_keycloak_config.py`, `tests/test_keycloak_integration.py` |
| A clean volume reproduces login and token issuance | Step 7: reset, start, test again |
| Update only what the change made inaccurate | Step 8: `README.md` and `docs/threat-model.md` |

This lesson includes coding: Steps 2, 3, 4, and 6 write code or configuration. Steps 1, 5, 7, and 8 do not, though Step 5 starts Keycloak and Step 8 edits two documents.

Terms used in this lesson:

- **OIDC (OpenID Connect):** the standard way for an application to ask an identity provider "who is this person?" and get a signed answer.
- **Realm:** one separate world inside Keycloak, with its own users, groups, clients, and keys, like one tenant.
- **Client:** an application allowed to send users to Keycloak to log in, here Open WebUI.
- **Redirect URI:** the exact address Keycloak sends the user back to after login, carrying a one-time code. If an attacker could choose it, the code would go to them.
- **Authorization code flow:** the browser login: the user types their password on Keycloak's page, never in the application, and the application trades the returned code for tokens.
- **PKCE:** a one-time secret the application makes before login and proves at the code exchange, so a stolen code is useless on its own. `S256` sends only its hash.
- **Access token / ID token:** signed JSON (a JWT). The access token is for the API; the ID token is for the client.
- **Claim:** one field in a token, such as `sub` (the user's ID), `groups`, `aud` (who the token is for), or `iss` (who issued it).
- **Scope:** a named bundle of claims a client may ask for at login.
- **JWKS:** the public keys Keycloak publishes, each with an ID (`kid`), so anyone can check a token's signature.

## Before you begin

This section explains the choices behind the configuration, so each step makes sense when you reach it.

- **Compose in its own folder.** `deploy/keycloak/` holds the Compose file and the realm file. M12-T03 composes the whole system later; a separate file now keeps Keycloak startable alone. The Makefile gets `keycloak-up`, `keycloak-down`, and `keycloak-reset`.
- **`start-dev` with a volume.** Development mode keeps its data in an embedded database inside the volume, with no TLS and no external database. That is right for a laptop and wrong for production, which is why the realm and the README say "non-production".
- **Secrets from placeholders.** Keycloak replaces `${NAME}` in a realm file with the environment variable `NAME` when it imports the realm. Compose passes each variable with `${NAME:?...}`, which refuses to start when it is missing, so Keycloak can never import a literal `${...}` as a password.
- **Imported once.** Keycloak imports a realm only when it does not exist yet, so a change to the realm file needs `make keycloak-reset`. The same rule means new secrets beside an old volume would not match the stored passwords, so `make keycloak-up` refuses to create them while the volume exists.
- **One client, the browser flow only.** `open-webui` is a confidential client: the code flow with S256 PKCE and one exact redirect URI, `http://localhost:3000/oauth/oidc/callback`. The implicit flow, password logins, and service accounts are off. Its tokens hold the `knowledge-service-api` audience, the user's groups by full path (`/engineering`), and only the four application roles.
- **Signing keys Keycloak makes.** The realm names the key providers (RSA for `RS256` signatures, plus HMAC and AES that Keycloak uses internally) but no key. Keycloak generates them on the first start and keeps them in the volume; a reset gives new ones. That is why M07-T03 must read the keys from JWKS, never from a file.
- **Demo users with fixed IDs.** `alice` (`/engineering`, `user`), `bob` (`/finance`, `user`, `content-manager`), `carol` (no group, `user`, `administrator`). Their `sub` stays the same across resets, so later tests can rely on it. Carol shows that an Application Role never implies Document access.
- **Tests at two levels.** Unit tests read the files: pins, exact redirect URIs, placeholders, and the secrets script. Integration tests log in through the real login form with `tests/keycloak_flow.py`, which M07-T03 will reuse. They skip themselves when no Keycloak is running.
- **What the review found.** An independent adversarial review tried more than twenty redirect URI variants, other response types and modes, plain and missing PKCE, reused codes, token exchange, device and backchannel grants, client registration, and the admin API, and got no code or token through `open-webui` without the password, the secret, and the PKCE verifier. It found these problems, all fixed; each problem in the configuration has a test that fails on the first version:
  - Asking for the `microprofile-jwt` scope put realm role names into the `groups` claim, so a user could make `administrator` look like a group. Now the client has no optional scopes, fixed default scopes, and only the application roles in its tokens.
  - The built-in `admin-cli` client in the realm gave a token for a password alone. Now it is off, and a test tries every built-in client.
  - `make keycloak-reset` failed when `.env` was missing, and `make keycloak-up` then created secrets the stored realm did not use. Now reset works by project name, and up refuses new secrets beside an old volume.
  - The login helper reported a wrong password, a locked user, and stale secrets the same way. Now it reports Keycloak's own reason, and tests check it.
  - Some tests could pass on a broken configuration: a port line without quotes, the `plain` PKCE method, a Keycloak without the realm shown as skipped. Now each is checked.
  - Every reset gave the demo users new IDs, and short group names could collide. Now the IDs are fixed and groups use full paths.
  - Documented, not fixed: Keycloak briefly locks a user after quick failed logins; the image digest is checked by hand, not by a test; an edited `.env` goes unnoticed until a login fails; the issuer `http://localhost:8180` cannot be reached from inside a container (M09-T08, M12-T03).
- **Known limits.** This is for local work only: no TLS, an embedded database, demo users. Nothing in the API checks a token yet; that is M07-T03.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before you add anything, write what must be true when the lesson is done. Step 7 checks the predictions.

Create `docs/evidence/M07-T02-run-keycloak-locally.md`:

```markdown
# M07-T02 — Run Keycloak locally evidence

## Invariant

`make keycloak-up` starts a pinned Keycloak on 127.0.0.1 with the
`knowledge-service` realm: one client, `open-webui`, using the code flow
with S256 PKCE and one exact redirect URI; signing keys Keycloak generates;
two groups, four application roles, and three non-production demo users.
Every password and secret is generated into a Git-ignored file; the
repository holds none. From an empty volume, a demo user logs in through
the login form and gets an RS256 access token for `knowledge-service-api`
naming their groups by full path and only their application roles.

## Expected failing check

Before the change, `make keycloak-up` fails with `No rule to make target`
for `keycloak-up`.

## Predictions

- What Keycloak does with a realm file that changed while its volume already holds the realm: ...
- What a login gets when the redirect URI differs from the registered one by one character: ...
- Where the signing key's private half lives, and what happens to it on `make keycloak-reset`: ...
- How many integration tests fail if the client allows the optional `microprofile-jwt` scope again, before and after a reset: ...

## Design

## Verification

## Review

## Decision

## Reflection
```

Check: the file exists, the failing check fails, and every prediction is a full sentence Step 7 can prove wrong.

### Step 2 — Coding step: generate the secrets, and the make targets

This step creates the only place secrets ever live, before any file needs them.

**In plain words:** A small script writes five random secrets into a file next to the Compose file: the Keycloak admin's password, Open WebUI's client secret, and the three demo users' passwords. Only you can read the file, Git ignores it, and the script never prints a value. Run again, it keeps the file, because the realm already stored inside Keycloak uses those values. The make targets run the script, start or stop Keycloak, and reset everything.

| Part | What it does |
|---|---|
| `NAMES` | The five secrets; Step 6 checks they match the Compose file and the realm exactly |
| `create` | Opens the file with `O_EXCL` and mode `0600`: never replaces a file, readable by the owner only |
| `main` | Creates a missing file, keeps a complete one, refuses one that lacks a name (exit 1) |
| `keycloak-up` | Refuses when `.env` is gone but the volume is not; then the script, then `docker compose up --wait` |
| `keycloak-reset` | Removes the container, the volume, and `.env`, by project name so it works without `.env` |

Create `scripts/keycloak_env.py`:

```python
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
```

In `Makefile`:

**1.** Find:

```make
# targets keep working exactly as before (CI and pre-commit call them directly),
# and a bare `make` opens the same menu.

# Bare `make` shows the command menu.
.DEFAULT_GOAL := do_good

.PHONY: do_good setup format format-check lint typecheck test-unit test-integration ml-check check hooks-install hooks docs-check

# ---------------------------------------------------------------------------
# Command menu
# ---------------------------------------------------------------------------

# Interactive launcher. It lists every target that carries a `## description`,
```

Replace with:

```make
# targets keep working exactly as before (CI and pre-commit call them directly),
# and a bare `make` opens the same menu.

# Bare `make` shows the command menu.
.DEFAULT_GOAL := do_good

.PHONY: do_good setup format format-check lint typecheck test-unit test-integration ml-check keycloak-up keycloak-down keycloak-reset check hooks-install hooks docs-check

# ---------------------------------------------------------------------------
# Command menu
# ---------------------------------------------------------------------------

# Interactive launcher. It lists every target that carries a `## description`,
```

**2.** Find:

```make
	KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$$url" uv run pytest -m "integration and not live"

ml-check: ## Type-check and test the local BGE-M3 and reranker servers (Python 3.12)
	cd ml/bge_m3_server && uv run pyright && uv run pytest -q

# ---------------------------------------------------------------------------
# Combined checks and git hooks
# ---------------------------------------------------------------------------

check: ## Run the full local gate (format, lint, types, tests + coverage)
	uv run ruff format --check .
	uv run ruff check .
```

Replace with:

```make
	KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$$url" uv run pytest -m "integration and not live"

ml-check: ## Type-check and test the local BGE-M3 and reranker servers (Python 3.12)
	cd ml/bge_m3_server && uv run pyright && uv run pytest -q

# ---------------------------------------------------------------------------
# Local identity provider
# ---------------------------------------------------------------------------

# Keycloak for local work only (M07-T02). Its secrets live in
# deploy/keycloak/.env, created on the first `keycloak-up` and ignored by Git.
# The realm is imported only into an empty volume, so new secrets beside an
# old volume would not match the stored passwords: `keycloak-up` refuses that.
KEYCLOAK_COMPOSE = docker compose -f deploy/keycloak/compose.yaml
KEYCLOAK_PROJECT = knowledge-service-keycloak
KEYCLOAK_VOLUME = $(KEYCLOAK_PROJECT)_keycloak-data

keycloak-up: ## Start the local Keycloak, creating its secrets on first use
	@if [ ! -f deploy/keycloak/.env ] && docker volume inspect $(KEYCLOAK_VOLUME) >/dev/null 2>&1; then \
		echo "deploy/keycloak/.env is gone but Keycloak's volume still holds a realm made with it; run make keycloak-reset"; \
		exit 1; \
	fi
	uv run python scripts/keycloak_env.py
	$(KEYCLOAK_COMPOSE) up -d --wait

keycloak-down: ## Stop the local Keycloak and keep its users and keys
	docker compose -p $(KEYCLOAK_PROJECT) down

# By project name, not by file, so it works even when .env is missing.
keycloak-reset: ## Remove the local Keycloak, its volume, and its secrets
	docker compose -p $(KEYCLOAK_PROJECT) down -v
	rm -f deploy/keycloak/.env

# ---------------------------------------------------------------------------
# Combined checks and git hooks
# ---------------------------------------------------------------------------

check: ## Run the full local gate (format, lint, types, tests + coverage)
	uv run ruff format --check .
	uv run ruff check .
```

Check: `git check-ignore -v deploy/keycloak/.env` names the `.env` rule in `.gitignore`, so the file can never be committed.

### Step 3 — Coding step: the pinned Compose file

This step says exactly which Keycloak runs and how.

**In plain words:** The Compose file runs one Keycloak container from an image fixed by its digest, reachable only from your own machine on port 8180. It passes in the secrets from the file Step 2 made and refuses to start without any of them, mounts the realm file read-only for import, keeps Keycloak's data in a named volume, and reports healthy only when Keycloak is ready.

| Setting | Why |
|---|---|
| `image: ...:26.8.1@sha256:...` | The version for people, the digest for machines; a re-pushed tag cannot change what runs |
| `start-dev --import-realm` | Development mode, importing the realm file on the first start |
| `KC_HOSTNAME: http://localhost:8180` | Every token names the same issuer, whatever address the caller used |
| `${NAME:?run make keycloak-up}` | Compose stops before Keycloak could import a missing secret |
| `127.0.0.1:8180:8080` | Not reachable from other machines; the management port 9000 is not published |
| `keycloak-data:/opt/keycloak/data` | Users, sessions, and generated keys; the image owns this directory, so a new volume gets its owner, not root's |
| `healthcheck` | Asks `/health/ready` on port 9000 inside the container, so `up --wait` returns only when logins work |

Create `deploy/keycloak/compose.yaml`:

```yaml
# Local, non-production Keycloak for the knowledge service (M07-T02).
#
# Start it with `make keycloak-up`, which first creates `.env` beside this
# file with random secrets. Nothing here holds a credential: every secret is
# read from that file, and Compose refuses to start when one is missing.
name: knowledge-service-keycloak

services:
  keycloak:
    # Keycloak 26.8.1, pinned by digest so every machine runs the same image.
    image: quay.io/keycloak/keycloak:26.8.1@sha256:3f3c5f8e03c614774b4bd9173b88391fefa4153bfc010d8012d59ad825377332
    command: ["start-dev", "--import-realm"]
    environment:
      KC_BOOTSTRAP_ADMIN_USERNAME: admin
      KC_BOOTSTRAP_ADMIN_PASSWORD: ${KC_BOOTSTRAP_ADMIN_PASSWORD:?run make keycloak-up}
      # Tokens name this issuer whichever address the client used.
      KC_HOSTNAME: http://localhost:8180
      KC_HEALTH_ENABLED: "true"
      # Read by the realm file's placeholders when the realm is imported.
      KS_OPEN_WEBUI_CLIENT_SECRET: ${KS_OPEN_WEBUI_CLIENT_SECRET:?run make keycloak-up}
      KS_ALICE_PASSWORD: ${KS_ALICE_PASSWORD:?run make keycloak-up}
      KS_BOB_PASSWORD: ${KS_BOB_PASSWORD:?run make keycloak-up}
      KS_CAROL_PASSWORD: ${KS_CAROL_PASSWORD:?run make keycloak-up}
    ports:
      - "127.0.0.1:8180:8080"
    volumes:
      # The realm is imported once, on the first start with an empty volume.
      - ./knowledge-service-realm.json:/opt/keycloak/data/import/knowledge-service-realm.json:ro
      # Users, sessions, and the generated signing keys live here. The image
      # owns this directory, so a new volume gets its owner, not root's.
      - keycloak-data:/opt/keycloak/data
    healthcheck:
      test:
        - CMD-SHELL
        - >-
          exec 3<>/dev/tcp/127.0.0.1/9000 &&
          printf 'GET /health/ready HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n' >&3 &&
          grep -q '"status": "UP"' <&3
      interval: 5s
      timeout: 5s
      retries: 60

volumes:
  keycloak-data:
```

Check: `docker compose -f deploy/keycloak/compose.yaml config` before Step 2's script has run fails with `required variable ... is missing a value: run make keycloak-up`, naming one of the secrets.

### Step 4 — Coding step: the realm

This step describes the realm Keycloak imports: who exists, who may log in from where, and what their tokens say.

**In plain words:** The realm file creates the `knowledge-service` realm with two groups, four application roles, and three demo users whose passwords come from the secrets file. It registers Open WebUI as the only application that may log users in, only through the browser login with PKCE and only back to one exact address, and it switches off the built-in `admin-cli` login. It tells Keycloak to put the user's groups and the API audience into tokens, to add nothing else a client asks for, and to generate its own signing keys.

| Part | What it holds |
|---|---|
| Realm settings | No self-registration or password reset, brute-force protection, `RS256`, 5-minute access tokens |
| `roles`, `groups`, `users` | The four application roles, `engineering` and `finance`, and alice, bob, carol with fixed `id`s and `${...}` passwords |
| `open-webui` | Confidential, code flow only, `pkce.code.challenge.method: S256`, one redirect URI and web origin, fixed default scopes, no optional scopes, `fullScopeAllowed: false` |
| Protocol mappers | `groups` by full path in every token; `knowledge-service-api` in the access token's audience |
| `admin-cli` | Keycloak's built-in client, with every flow and grant off |
| `scopeMappings` | Only the four application roles may appear in an `open-webui` token |
| `components` | Key providers: `rsa-generated` (RS256), `hmac-generated`, `aes-generated`; no key material |

Create `deploy/keycloak/knowledge-service-realm.json`:

```json
{
  "realm": "knowledge-service",
  "displayName": "Knowledge service (local, non-production)",
  "enabled": true,
  "sslRequired": "external",
  "registrationAllowed": false,
  "resetPasswordAllowed": false,
  "rememberMe": false,
  "loginWithEmailAllowed": false,
  "duplicateEmailsAllowed": false,
  "bruteForceProtected": true,
  "defaultSignatureAlgorithm": "RS256",
  "accessTokenLifespan": 300,
  "ssoSessionIdleTimeout": 1800,
  "ssoSessionMaxLifespan": 36000,
  "roles": {
    "realm": [
      {
        "name": "user",
        "description": "Asks questions and manages their own Conversations."
      },
      {
        "name": "content-manager",
        "description": "Configures Sources and starts Synchronization Runs."
      },
      {
        "name": "evaluator",
        "description": "Manages Evaluation Datasets and runs paid evaluation."
      },
      {
        "name": "administrator",
        "description": "Manages policy and operational configuration."
      }
    ]
  },
  "groups": [
    {
      "name": "engineering"
    },
    {
      "name": "finance"
    }
  ],
  "users": [
    {
      "id": "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01",
      "username": "alice",
      "enabled": true,
      "email": "alice@example.test",
      "emailVerified": true,
      "firstName": "Alice",
      "lastName": "Demo",
      "credentials": [
        {
          "type": "password",
          "value": "${KS_ALICE_PASSWORD}",
          "temporary": false
        }
      ],
      "groups": [
        "/engineering"
      ],
      "realmRoles": [
        "user"
      ]
    },
    {
      "id": "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a02",
      "username": "bob",
      "enabled": true,
      "email": "bob@example.test",
      "emailVerified": true,
      "firstName": "Bob",
      "lastName": "Demo",
      "credentials": [
        {
          "type": "password",
          "value": "${KS_BOB_PASSWORD}",
          "temporary": false
        }
      ],
      "groups": [
        "/finance"
      ],
      "realmRoles": [
        "user",
        "content-manager"
      ]
    },
    {
      "id": "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a03",
      "username": "carol",
      "enabled": true,
      "email": "carol@example.test",
      "emailVerified": true,
      "firstName": "Carol",
      "lastName": "Demo",
      "credentials": [
        {
          "type": "password",
          "value": "${KS_CAROL_PASSWORD}",
          "temporary": false
        }
      ],
      "groups": [],
      "realmRoles": [
        "user",
        "administrator"
      ]
    }
  ],
  "clients": [
    {
      "clientId": "open-webui",
      "name": "Open WebUI (local)",
      "enabled": true,
      "protocol": "openid-connect",
      "publicClient": false,
      "clientAuthenticatorType": "client-secret",
      "secret": "${KS_OPEN_WEBUI_CLIENT_SECRET}",
      "standardFlowEnabled": true,
      "implicitFlowEnabled": false,
      "directAccessGrantsEnabled": false,
      "serviceAccountsEnabled": false,
      "fullScopeAllowed": false,
      "frontchannelLogout": true,
      "redirectUris": [
        "http://localhost:3000/oauth/oidc/callback"
      ],
      "webOrigins": [
        "http://localhost:3000"
      ],
      "attributes": {
        "pkce.code.challenge.method": "S256",
        "post.logout.redirect.uris": "http://localhost:3000/"
      },
      "protocolMappers": [
        {
          "name": "groups",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-group-membership-mapper",
          "config": {
            "claim.name": "groups",
            "full.path": "true",
            "id.token.claim": "true",
            "access.token.claim": "true",
            "userinfo.token.claim": "true"
          }
        },
        {
          "name": "knowledge-service-api audience",
          "protocol": "openid-connect",
          "protocolMapper": "oidc-audience-mapper",
          "config": {
            "included.custom.audience": "knowledge-service-api",
            "id.token.claim": "false",
            "access.token.claim": "true"
          }
        }
      ],
      "defaultClientScopes": [
        "basic",
        "acr",
        "web-origins",
        "profile",
        "email",
        "roles"
      ],
      "optionalClientScopes": []
    },
    {
      "clientId": "admin-cli",
      "enabled": true,
      "publicClient": true,
      "standardFlowEnabled": false,
      "implicitFlowEnabled": false,
      "directAccessGrantsEnabled": false,
      "serviceAccountsEnabled": false
    }
  ],
  "scopeMappings": [
    {
      "client": "open-webui",
      "roles": [
        "user",
        "content-manager",
        "evaluator",
        "administrator"
      ]
    }
  ],
  "components": {
    "org.keycloak.keys.KeyProvider": [
      {
        "name": "rsa-generated",
        "providerId": "rsa-generated",
        "config": {
          "priority": [
            "100"
          ],
          "keySize": [
            "2048"
          ],
          "algorithm": [
            "RS256"
          ]
        }
      },
      {
        "name": "hmac-generated-hs512",
        "providerId": "hmac-generated",
        "config": {
          "priority": [
            "100"
          ],
          "algorithm": [
            "HS512"
          ]
        }
      },
      {
        "name": "aes-generated",
        "providerId": "aes-generated",
        "config": {
          "priority": [
            "100"
          ]
        }
      }
    ]
  }
}
```

Check: `python3 -m json.tool deploy/keycloak/knowledge-service-realm.json > /dev/null` succeeds.

### Step 5 — No coding in this step: start Keycloak and look at it

This step starts Keycloak and checks, by hand, what it publishes.

**Action 1:** start it. Docker must be running.

```bash
make keycloak-up
```

Expected: `created .env with 5 secrets`, then the container reported `Healthy`. The first start takes up to a minute.

**Action 2:** read what the realm publishes:

```bash
curl -s http://localhost:8180/realms/knowledge-service/.well-known/openid-configuration | python3 -m json.tool | grep -E '"(issuer|jwks_uri)"'
curl -s http://localhost:8180/realms/knowledge-service/protocol/openid-connect/certs | python3 -m json.tool | grep -E '"(kid|alg|use)"'
docker port knowledge-service-keycloak-keycloak-1
```

Expected: the issuer `http://localhost:8180/realms/knowledge-service`, one key with `"alg": "RS256"` and `"use": "sig"`, and only `8080/tcp -> 127.0.0.1:8180`.

Check: `ls -l deploy/keycloak/.env` shows `-rw-------`, and `git status` does not list it.

### Step 6 — Coding step: tests

This step proves the configuration in files and in a running Keycloak.

**In plain words:** A helper logs a user in the way a browser does: it opens the login page, posts the name and password, catches the returned code, and trades it for tokens with the PKCE verifier and the client secret. If Keycloak refuses any step, the helper says which step and gives Keycloak's own reason. The unit tests read the realm, the Compose file, and the secrets script. The integration tests log the demo users in and try everything that must fail.

| Test | What it proves |
|---|---|
| `test_a_demo_user_logs_in_and_gets_a_signed_token_for_the_api`, `test_each_demo_user_carries_their_groups_and_roles_only` | Success: an RS256 token with a JWKS key, the exact issuer and audience, the fixed `sub`, and each user's groups and roles |
| `test_a_code_is_never_sent_to_an_unregistered_redirect` | The highest-risk edge: seven near-miss redirect URIs, each refused with `Invalid parameter: redirect_uri` |
| `test_a_client_cannot_add_scopes_that_change_the_token` | The other edge: no scope can put roles into `groups` or add an offline token |
| `test_a_wrong_password_gets_the_login_form_again_and_no_code`, `test_a_login_without_an_s256_challenge_is_refused`, `test_a_code_is_not_exchanged_without_the_client_secret`, `test_no_client_gives_a_token_for_a_password_alone` | Typed denials, each with Keycloak's reason |
| `tests/test_keycloak_config.py` | No credential committed, every placeholder required by Compose and made by the script, the pin, loopback only, the script's file mode and refusals |

Why the helper sends cookies itself: Keycloak marks its login cookies `Secure`. A browser still sends them to `http://localhost`, which it treats as secure; httpx does not.

Create `tests/keycloak_flow.py`:

```python
"""Log in to the local Keycloak as a browser would, for integration tests.

The flow is the one Open WebUI uses: authorization code with PKCE. The
helper asks for the login page, posts the user's name and password to the
form Keycloak returns, takes the code from the redirect without following
it, and exchanges the code for tokens. Each step that Keycloak refuses
raises `LoginRefused` with the step, the status, and Keycloak's own reason,
never a secret.
"""

import base64
import hashlib
import html
import json
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

ISSUER = "http://localhost:8180/realms/knowledge-service"
CLIENT_ID = "open-webui"
REDIRECT_URI = "http://localhost:3000/oauth/oidc/callback"
SECRETS = Path(__file__).resolve().parents[1] / "deploy/keycloak/.env"
FORM_ACTION = re.compile(r'<form[^>]*id="kc-form-login"[^>]*action="([^"]+)"')
# The message on Keycloak's error page, and the one above its login form.
PAGE_ERROR = re.compile(r'id="kc-error-message">\s*<p class="instruction">([^<]*)<')
FORM_ERROR = re.compile(r'kc-feedback-text">\s*([^<]*?)\s*<')


class LoginRefused(Exception):
    """Keycloak refused one step of the login.

    `step` names the step, `status` is Keycloak's HTTP status, and `reason`
    is the message Keycloak gave: the text of its error page or login form,
    or the `error` and `error_description` of a redirect.
    """

    def __init__(self, step: str, status: int, reason: str) -> None:
        super().__init__(f"{step} refused with status {status}: {reason}")
        self.step = step
        self.status = status
        self.reason = reason


@dataclass(frozen=True)
class Tokens:
    access_token: str
    id_token: str


def local_secrets(path: Path = SECRETS) -> dict[str, str]:
    """The generated secrets, read from the ignored `.env` file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    pairs = [line.split("=", 1) for line in lines if "=" in line]
    return {name: value for name, value in pairs if not name.startswith("#")}


def claims(token: str) -> dict[str, Any]:
    """The payload of a JWT, decoded but not verified; M07-T03 verifies."""
    return _segment(token.split(".")[1])


def header(token: str) -> dict[str, Any]:
    """The header of a JWT, decoded but not verified."""
    return _segment(token.split(".")[0])


async def log_in(
    client: httpx.AsyncClient,
    username: str,
    password: str,
    *,
    client_secret: str,
    redirect_uri: str = REDIRECT_URI,
    scope: str = "openid",
    challenge: str | None = "S256",
) -> Tokens:
    """Log `username` in through the login form and return their tokens.

    `challenge` is the PKCE method to ask for; `None` sends no challenge.
    """
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(16)
    query = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
    }
    if challenge == "S256":
        digest = hashlib.sha256(verifier.encode()).digest()
        query["code_challenge"] = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    elif challenge is not None:
        query["code_challenge"] = verifier
    if challenge is not None:
        query["code_challenge_method"] = challenge
    page = await client.get(f"{ISSUER}/protocol/openid-connect/auth", params=query)
    action = FORM_ACTION.search(page.text) if page.status_code == 200 else None
    if action is None:
        raise LoginRefused("authorization request", page.status_code, _reason(page))

    answer = await client.post(
        html.unescape(action[1]),
        data={"username": username, "password": password, "credentialId": ""},
        headers={"Cookie": _session_cookies(page)},
    )
    location: str = answer.headers.get("location", "")
    returned: dict[str, list[str]] = parse_qs(urlsplit(location).query)
    if answer.status_code != 302 or not location.startswith(redirect_uri):
        raise LoginRefused("login form", answer.status_code, _reason(answer))
    if returned.get("state") != [state] or "code" not in returned:
        raise LoginRefused("redirect", answer.status_code, _reason(answer))

    exchanged = await client.post(
        f"{ISSUER}/protocol/openid-connect/token",
        data={
            "grant_type": "authorization_code",
            "code": returned["code"][0],
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        auth=(CLIENT_ID, client_secret),
    )
    if exchanged.status_code != 200:
        raise LoginRefused("code exchange", exchanged.status_code, _reason(exchanged))
    body = exchanged.json()
    return Tokens(access_token=body["access_token"], id_token=body["id_token"])


def _reason(response: httpx.Response) -> str:
    """What Keycloak said, from a redirect, a JSON error, or a page."""
    location: str = response.headers.get("location", "")
    if location:
        parts = urlsplit(location)
        said: dict[str, list[str]] = parse_qs(parts.query or parts.fragment)
        return " ".join(said.get("error", []) + said.get("error_description", []))
    if response.headers.get("content-type", "").startswith("application/json"):
        body: dict[str, str] = response.json()
        return f"{body.get('error', '')} {body.get('error_description', '')}".strip()
    found = PAGE_ERROR.search(response.text) or FORM_ERROR.search(response.text)
    return html.unescape(found[1]).strip() if found else ""


def _session_cookies(response: httpx.Response) -> str:
    """The login session's cookies, as a `Cookie` header.

    Keycloak marks them `Secure`. A browser still sends them to
    `http://localhost`, which it treats as secure; httpx does not, so the
    helper passes them on itself.
    """
    cookies = response.headers.get_list("set-cookie")
    return "; ".join(cookie.split(";", 1)[0] for cookie in cookies)


def _segment(part: str) -> dict[str, Any]:
    decoded = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    return json.loads(decoded)  # type: ignore[no-any-return]
```

Create `tests/test_keycloak_config.py`:

```python
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
```

Create `tests/test_keycloak_integration.py`:

```python
"""Login and token issuance against the local Keycloak (`make keycloak-up`).

The tests skip themselves when nothing listens on Keycloak's port or its
generated secrets are missing. A Keycloak that answers without the realm
fails them instead.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from keycloak_flow import (
    CLIENT_ID,
    ISSUER,
    REDIRECT_URI,
    SECRETS,
    LoginRefused,
    claims,
    header,
    local_secrets,
    log_in,
)

pytestmark = pytest.mark.integration

PASSWORDS = {
    "alice": "KS_ALICE_PASSWORD",
    "bob": "KS_BOB_PASSWORD",
    "carol": "KS_CAROL_PASSWORD",
}
TOKEN_ENDPOINT = f"{ISSUER}/protocol/openid-connect/token"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            found = await client.get(f"{ISSUER}/.well-known/openid-configuration")
        except httpx.TransportError:
            pytest.skip("no local Keycloak: run `make keycloak-up`")
        if not SECRETS.exists():
            pytest.skip("no local Keycloak secrets: run `make keycloak-up`")
        assert found.status_code == 200, "Keycloak answers, but without the realm"
        yield client


@pytest.fixture
def secrets() -> dict[str, str]:
    return local_secrets()


async def log_in_as(
    client: httpx.AsyncClient,
    secrets: dict[str, str],
    username: str,
    **options: Any,
) -> dict[str, Any]:
    """The claims of `username`'s access token, logged in with `options`."""
    tokens = await log_in(
        client,
        username,
        secrets[PASSWORDS[username]],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
        **options,
    )
    return claims(tokens.access_token)


async def refused(
    client: httpx.AsyncClient, secrets: dict[str, str], **options: Any
) -> LoginRefused:
    """Why alice's login with `options` was refused."""
    with pytest.raises(LoginRefused) as raised:
        await log_in_as(client, secrets, "alice", **options)
    return raised.value


# --- Success --------------------------------------------------------------------------


async def test_discovery_names_the_issuer_and_its_keys(
    client: httpx.AsyncClient,
) -> None:
    found = (await client.get(f"{ISSUER}/.well-known/openid-configuration")).json()

    assert found["issuer"] == ISSUER
    assert found["jwks_uri"] == f"{ISSUER}/protocol/openid-connect/certs"


async def test_a_demo_user_logs_in_and_gets_a_signed_token_for_the_api(
    client: httpx.AsyncClient, secrets: dict[str, str]
) -> None:
    tokens = await log_in(
        client,
        "alice",
        secrets["KS_ALICE_PASSWORD"],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
    )

    keys = (await client.get(f"{ISSUER}/protocol/openid-connect/certs")).json()
    signing = {key["kid"]: key for key in keys["keys"] if key["use"] == "sig"}
    access = header(tokens.access_token)
    assert access["alg"] == "RS256"
    assert signing[access["kid"]]["kty"] == "RSA"
    payload = claims(tokens.access_token)
    assert payload["iss"] == ISSUER
    assert payload["aud"] == "knowledge-service-api"
    assert payload["azp"] == CLIENT_ID
    assert payload["sub"] == "0f5e7d2a-7c51-4f0e-9d2b-1a6c3e8b4a01"
    assert payload["exp"] - payload["iat"] == 300
    assert claims(tokens.id_token)["aud"] == CLIENT_ID


@pytest.mark.parametrize(
    ("username", "groups", "roles"),
    [
        ("alice", ["/engineering"], {"user"}),
        ("bob", ["/finance"], {"user", "content-manager"}),
        ("carol", [], {"user", "administrator"}),
    ],
)
async def test_each_demo_user_carries_their_groups_and_roles_only(
    client: httpx.AsyncClient,
    secrets: dict[str, str],
    username: str,
    groups: list[str],
    roles: set[str],
) -> None:
    payload = await log_in_as(client, secrets, username)

    assert payload.get("groups", []) == groups
    assert set(payload["realm_access"]["roles"]) == roles


# --- The highest-risk edges: where a code goes, and what a token may claim ------------


@pytest.mark.parametrize(
    "redirect_uri",
    [
        "http://localhost:3000/oauth/oidc/callback/extra",
        "http://localhost:3000/oauth/oidc/callback?next=http://evil.test",
        "http://localhost:3000.evil.test/oauth/oidc/callback",
        "http://localhost:3000@evil.test/oauth/oidc/callback",
        "http://localhost:3001/oauth/oidc/callback",
        "https://localhost:3000/oauth/oidc/callback",
        "http://evil.test/oauth/oidc/callback",
    ],
    ids=[
        "longer-path",
        "query",
        "suffix-host",
        "userinfo",
        "other-port",
        "other-scheme",
        "other-host",
    ],
)
async def test_a_code_is_never_sent_to_an_unregistered_redirect(
    client: httpx.AsyncClient, secrets: dict[str, str], redirect_uri: str
) -> None:
    refusal = await refused(client, secrets, redirect_uri=redirect_uri)

    assert (refusal.step, refusal.status) == ("authorization request", 400)
    assert refusal.reason == "Invalid parameter: redirect_uri"


@pytest.mark.parametrize(
    "scope",
    ["openid microprofile-jwt", "openid offline_access", "openid address phone"],
    ids=["roles-as-groups", "offline", "other-optional"],
)
async def test_a_client_cannot_add_scopes_that_change_the_token(
    client: httpx.AsyncClient, secrets: dict[str, str], scope: str
) -> None:
    refusal = await refused(client, secrets, scope=scope)

    assert refusal.step == "authorization request"
    assert refusal.reason.startswith("invalid_scope")


# --- Denials --------------------------------------------------------------------------


async def test_a_wrong_password_gets_the_login_form_again_and_no_code(
    client: httpx.AsyncClient, secrets: dict[str, str]
) -> None:
    refusal = await refused(
        client,
        {**secrets, "KS_ALICE_PASSWORD": secrets["KS_BOB_PASSWORD"]},
    )

    assert (refusal.step, refusal.status) == ("login form", 200)
    assert refusal.reason == "Invalid username or password."


@pytest.mark.parametrize(
    ("challenge", "reason"),
    [
        (None, "invalid_request Missing parameter: code_challenge_method"),
        (
            "plain",
            "invalid_request Invalid parameter: "
            "code challenge method is not matching the configured one",
        ),
    ],
    ids=["none", "plain"],
)
async def test_a_login_without_an_s256_challenge_is_refused(
    client: httpx.AsyncClient,
    secrets: dict[str, str],
    challenge: str | None,
    reason: str,
) -> None:
    refusal = await refused(client, secrets, challenge=challenge)

    assert (refusal.step, refusal.reason) == ("authorization request", reason)


async def test_a_code_is_not_exchanged_without_the_client_secret(
    client: httpx.AsyncClient, secrets: dict[str, str]
) -> None:
    wrong = {**secrets, "KS_OPEN_WEBUI_CLIENT_SECRET": secrets["KS_BOB_PASSWORD"]}

    refusal = await refused(client, wrong)

    assert (refusal.step, refusal.status) == ("code exchange", 401)
    assert refusal.reason.startswith("unauthorized_client")


@pytest.mark.parametrize(
    "client_id",
    [
        "open-webui",
        "admin-cli",
        "account",
        "account-console",
        "security-admin-console",
        "broker",
        "realm-management",
    ],
)
async def test_no_client_gives_a_token_for_a_password_alone(
    client: httpx.AsyncClient, secrets: dict[str, str], client_id: str
) -> None:
    data = {
        "grant_type": "password",
        "client_id": client_id,
        "username": "carol",
        "password": secrets["KS_CAROL_PASSWORD"],
    }
    # Only open-webui has a secret; the built-in clients are tried without one.
    secret = secrets["KS_OPEN_WEBUI_CLIENT_SECRET"]
    answer = await (
        client.post(TOKEN_ENDPOINT, data=data, auth=(client_id, secret))
        if client_id == CLIENT_ID
        else client.post(TOKEN_ENDPOINT, data=data)
    )

    assert answer.status_code in {400, 401}
    assert answer.json()["error"] == "unauthorized_client"
    assert "access_token" not in answer.json()


async def test_the_client_has_no_token_of_its_own(
    client: httpx.AsyncClient, secrets: dict[str, str]
) -> None:
    answer = await client.post(
        TOKEN_ENDPOINT,
        data={"grant_type": "client_credentials"},
        auth=(CLIENT_ID, secrets["KS_OPEN_WEBUI_CLIENT_SECRET"]),
    )

    assert answer.json()["error"] == "unauthorized_client"
    assert "access_token" not in answer.json()


async def test_the_implicit_flow_returns_no_token(client: httpx.AsyncClient) -> None:
    answer = await client.get(
        f"{ISSUER}/protocol/openid-connect/auth",
        params={
            "response_type": "token",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": "openid",
            "nonce": "n",
        },
    )

    location = answer.headers.get("location", "")
    assert "error=unauthorized_client" in location
    assert "access_token" not in location
```

Run:

```bash
uv run pytest tests/test_keycloak_config.py -q
uv run pytest -m integration tests/test_keycloak_integration.py -q
```

Expected: 19 passed, then 28 passed. With Keycloak stopped, the second command gives 28 skipped.

### Step 7 — No coding in this step: break it, reset it, and decide

This step shows what the scope setting protects, why a realm change needs a reset, and that a clean volume reproduces everything.

**Action 1:** break the client on purpose. In `deploy/keycloak/knowledge-service-realm.json`, change `"optionalClientScopes": []` to `"optionalClientScopes": ["microprofile-jwt"]`. Run both test files:

```bash
uv run pytest tests/test_keycloak_config.py -q
uv run pytest -m integration tests/test_keycloak_integration.py -q
```

Expected: the unit tests give 1 failed, 18 passed (`test_a_client_cannot_ask_for_more_scopes_or_roles_than_these`). The integration tests still give 28 passed: Keycloak kept the realm it imported, and the changed file was never read.

**Action 2:** reset and start again, then rerun the integration tests:

```bash
make keycloak-reset
make keycloak-up
uv run pytest -m integration tests/test_keycloak_integration.py -q
```

Expected: 1 failed, 27 passed: `test_a_client_cannot_add_scopes_that_change_the_token[roles-as-groups]`. With that scope, carol's token says `"groups": ["administrator", "user"]`, and code that trusted `groups` for Document access would treat a role as a group.

**Action 3:** undo the change, then reset, start, and test once more. This is also the acceptance check: a clean volume with new secrets and new signing keys, and the same logins work.

```bash
make keycloak-reset
make keycloak-up
uv run pytest -m integration tests/test_keycloak_integration.py -q
```

Expected: 28 passed.

**Action 4:** under `## Decision`, write:

- why the redirect URI must match exactly, and what a code sent elsewhere would allow;
- why the client may not ask for optional scopes, and why `groups` must hold only groups;
- why passwords live in an ignored file and the realm holds placeholders;
- why signing keys are generated and never committed, and what that means for M07-T03;
- at least one **falsified assumption** in the shape "I assumed X. The run or review shows Y. So Z is wrong."

### Step 8 — No coding in this step: documents, gates, evidence, and progress

This step updates what the change made inaccurate, proves everything still works, and records the result.

**Action 1:** update two documents.

In `README.md`:

Find:

````markdown
make lint              Run Ruff linting
make typecheck         Run Pyright
make test-unit         Run deterministic unit tests
make test-integration  Run non-live integration tests
make check             Run every non-live quality gate with coverage
make docs-check        Validate the documentation under docs/
```

Example of a direct command: UV_CACHE_DIR=/tmp/uv-cache-rag-project make test-unit

The menu is built from the `## description` comment on each target, so a new
command shows up in it automatically once you document it:

````

Replace with:

````markdown
make lint              Run Ruff linting
make typecheck         Run Pyright
make test-unit         Run deterministic unit tests
make test-integration  Run non-live integration tests
make check             Run every non-live quality gate with coverage
make docs-check        Validate the documentation under docs/
make keycloak-up       Start the local Keycloak (creates its secrets once)
make keycloak-down     Stop the local Keycloak, keeping its users and keys
make keycloak-reset    Remove the local Keycloak, its volume, and its secrets
```

The local Keycloak (`deploy/keycloak/`) serves the `knowledge-service` realm at
`http://localhost:8180` for development only. `make keycloak-up` writes random
secrets to the Git-ignored `deploy/keycloak/.env` on first use; the demo users'
passwords are read from there. The realm is imported only into an empty volume,
so if that file is lost or edited, run `make keycloak-reset` and then
`make keycloak-up`. Keycloak briefly locks a user after quick failed logins;
wait a minute or reset.

Example of a direct command: UV_CACHE_DIR=/tmp/uv-cache-rag-project make test-unit

The menu is built from the `## description` comment on each target, so a new
command shows up in it automatically once you document it:

````

In `docs/threat-model.md`:

**1.** Find:

```markdown
| Document text and Chunks | Organizational knowledge; most of it is not for every reader | PostgreSQL `documents`, `chunks`; Evidence in a prompt |
| Access Grants and Authorization Versions | Decide who may read what; a wrong grant is a leak | PostgreSQL `access_grants` |
| Authorization Context | The verified User and Groups of one request | In memory, per request |
| Questions, Answers, Conversations | Reveal what a User asked and what they were told | PostgreSQL `conversations`, `messages`; the model provider's request |
| Citations and Evidence IDs | Tell the User where a claim came from; a fake one launders a claim | Built on the server per request |
| Evaluation Datasets and Runs | Hold Questions, expected Documents, and Authorization Contexts | `datasets/`, `artifacts/` |
| Credentials | Database URL, provider API keys, later OIDC client secrets | Environment and settings, redacted in repr |
| Telemetry | Logs and, later, traces; a copy of everything they hold | Log records today |
| Availability and budget | Every Answer costs model tokens | Model providers, the database pool |

## Actors

| Actor | Trust | What they may try |
```

Replace with:

```markdown
| Document text and Chunks | Organizational knowledge; most of it is not for every reader | PostgreSQL `documents`, `chunks`; Evidence in a prompt |
| Access Grants and Authorization Versions | Decide who may read what; a wrong grant is a leak | PostgreSQL `access_grants` |
| Authorization Context | The verified User and Groups of one request | In memory, per request |
| Questions, Answers, Conversations | Reveal what a User asked and what they were told | PostgreSQL `conversations`, `messages`; the model provider's request |
| Citations and Evidence IDs | Tell the User where a claim came from; a fake one launders a claim | Built on the server per request |
| Evaluation Datasets and Runs | Hold Questions, expected Documents, and Authorization Contexts | `datasets/`, `artifacts/` |
| Credentials | Database URL, provider API keys, the OIDC client secret and demo passwords | Environment and settings, redacted in repr; the local Keycloak's in the Git-ignored `deploy/keycloak/.env`, generated per machine |
| Telemetry | Logs and, later, traces; a copy of everything they hold | Log records today |
| Availability and budget | Every Answer costs model tokens | Model providers, the database pool |

## Actors

| Actor | Trust | What they may try |
```

**2.** Find:

```markdown

| Entry point | Who reaches it | Today |
|---|---|---|
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. No token check yet; the reader is fixed when the Evidence source is built (M07-T03, M07-T04) |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors; the Redis queue in M08 |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
| OpenAI-compatible adapter, Open WebUI | Users | M09 |
| MCP server and agent tools | Agents | M11 |
```

Replace with:

```markdown

| Entry point | Who reaches it | Today |
|---|---|---|
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. No token check yet; the reader is fixed when the Evidence source is built (M07-T03, M07-T04) |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors; the Redis queue in M08 |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
| OpenAI-compatible adapter, Open WebUI | Users | M09 |
| MCP server and agent tools | Agents | M11 |
```

**3.** Find:

```markdown

- The API checks no token yet; until M07-T03 and M07-T04 the reader is whoever the Evidence source was built for.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
```

Replace with:

```markdown

- The API checks no token yet; until M07-T03 and M07-T04 the reader is whoever the Evidence source was built for.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
```

**Action 2:** run the gates in order, with Keycloak running:

```bash
uv run pytest tests/test_keycloak_config.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 2381 passed for `make test-unit`, 19 more than M07-T01, and 406 passed for `make test-integration`, 28 more. Without Keycloak, those 28 are skipped.

**Action 3:** complete the evidence file:

- **Design:** the Compose file, the realm, where secrets live, and the login helper.
- **Verification:** the commands and counts, the Step 5 output, the deliberate break before and after the reset, the clean-volume run, and the known limits from "Before you begin".
- **Review:** the review summary from "Before you begin".
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M07-T02 row to `docs/curriculum/PROGRESS.md`, set the next task to M07-T03, and commit with `m07-t02: run keycloak locally`.

## Reflection questions

1. Keycloak hides a lot from the API: passwords, login pages, sessions, key rotation. What is left that the API itself must still check in every token, and why can it not trust Keycloak to have done it?
2. The access token says `"groups": ["/engineering"]`. Which later task turns that into the Group IDs that Access Grants use, and what should happen to a group name the service has never seen?
3. Which setting in the realm would be the easiest to loosen "just for testing" later, and which test would catch it?
4. A reset gives new signing keys. What would break if M07-T03 copied today's public key into a configuration file?
5. Carol is an administrator with no group. What should she see when she asks a question about an engineering Document, and why?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before the change.
- [ ] `make keycloak-up` creates the secrets once, starts the pinned Keycloak, and reports it healthy.
- [ ] The realm has one application client with the code flow, S256 PKCE, one exact redirect URI, and no optional scopes; `admin-cli` gives no tokens.
- [ ] Signing keys are generated by Keycloak, and the repository holds no password, secret, or key.
- [ ] Demo users log in through the login form and get RS256 tokens with the API audience, their groups by full path, and only their application roles.
- [ ] Unregistered redirect URIs, extra scopes, wrong passwords, missing PKCE, a wrong client secret, and password-only logins are all refused.
- [ ] You ran the deliberate break, saw it change nothing until the reset, saw the test fail after it, and undid it.
- [ ] A reset and a fresh start reproduce login and token issuance.
- [ ] `README.md` and `docs/threat-model.md` describe the local Keycloak.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
