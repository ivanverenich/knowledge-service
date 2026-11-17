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

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What Keycloak does with a realm file that changed while its volume already holds the realm: nothing; it skips the import because the realm exists, so the change takes effect only after `make keycloak-reset` empties the volume.
- What a login gets when the redirect URI differs from the registered one by one character: no login form and no code; Keycloak answers 400 with `Invalid parameter: redirect_uri` before the user can type a password.
- Where the signing key's private half lives, and what happens to it on `make keycloak-reset`: only inside Keycloak's volume, generated on the first start; a reset deletes it, and the next start makes a new key with a new `kid`.
- How many integration tests fail if the client allows the optional `microprofile-jwt` scope again, before and after a reset: none before, because the stored realm is unchanged; one after (`test_a_client_cannot_add_scopes_that_change_the_token[roles-as-groups]`).

## Design

- **Compose.** `deploy/keycloak/compose.yaml` runs `quay.io/keycloak/keycloak:26.8.1` pinned by digest, as `start-dev --import-realm`, published on `127.0.0.1:8180` only, with `KC_HOSTNAME=http://localhost:8180` so every token names one issuer. Every secret is passed as `${NAME:?run make keycloak-up}`, the realm file is mounted read-only into the import directory, data and generated keys live in the `keycloak-data` volume at `/opt/keycloak/data`, and a health check on `/health/ready` lets `up --wait` return only when logins work.
- **The realm.** `deploy/keycloak/knowledge-service-realm.json`: no self-registration or password reset, brute-force protection, `RS256`, 5-minute access tokens; roles `user`, `content-manager`, `evaluator`, `administrator`; groups `engineering`, `finance`; alice, bob, carol with fixed IDs and `${...}` passwords. One application client, `open-webui`: confidential, code flow only, S256 PKCE, one redirect URI and web origin, fixed default scopes, no optional scopes, `fullScopeAllowed: false` with a scope mapping to the four application roles, and mappers for `groups` by full path and the `knowledge-service-api` audience. The built-in `admin-cli` has every flow off. Key providers `rsa-generated`, `hmac-generated`, `aes-generated` hold no key material.
- **Secrets.** `scripts/keycloak_env.py` writes five random values to `deploy/keycloak/.env` (Git-ignored, mode `0600`, `O_EXCL`), prints no value, keeps a complete file, and refuses an incomplete one. `make keycloak-up` refuses new secrets while the old volume exists; `make keycloak-reset` removes the container, the volume, and `.env` by project name.
- **The login helper.** `tests/keycloak_flow.py` logs in through the real form with the code flow and S256 PKCE, passes Keycloak's `Secure` cookies itself, and raises `LoginRefused` with the step, the status, and Keycloak's reason. M07-T03 can reuse it.

## Verification

- `make keycloak-up`: `No rule to make target` before; now `created .env with 5 secrets` and the container `Healthy`.
- Step 5: issuer `http://localhost:8180/realms/knowledge-service`, one JWKS key `RS256`/`sig`, `docker port` only `8080/tcp -> 127.0.0.1:8180`, `.env` is `-rw-------` and absent from `git status`.
- `uv run pytest tests/test_keycloak_config.py -q`: 19 passed. `uv run pytest -m integration tests/test_keycloak_integration.py -q`: 28 passed (28 skipped with Keycloak stopped).
- Gates, with Keycloak running: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2381 passed (+19); `make test-integration` 406 passed (+28); `make docs-check` passed.
- The deliberate break, before and after the reset, and the clean-volume run: see `## Decision`.
- Known limits: local only, with no TLS, an embedded database, and demo users; Keycloak briefly locks a user after quick failed logins; the image digest was checked by hand (`docker buildx imagetools`), not by a test; an edited `.env` goes unnoticed until a login fails; the issuer cannot be reached from inside a container (M09-T08, M12-T03); the API checks no token yet (M07-T03).

## Review

An independent adversarial review tried more than twenty redirect URI variants (case, trailing slash, encoded characters, userinfo, fragments, traversal, `127.0.0.1`), other response types and modes, plain and missing PKCE, a wrong verifier, reused codes, token exchange, device and backchannel grants, anonymous client registration, and the admin API, and got no code or token through `open-webui` without the password, the secret, and the PKCE verifier. It found these problems, all fixed; each problem in the configuration has a test that fails on the first version:

- Asking for the `microprofile-jwt` scope put realm role names into `groups`. Now the client has no optional scopes, fixed default scopes, and only the application roles (`test_a_client_cannot_add_scopes_that_change_the_token`, `test_a_client_cannot_ask_for_more_scopes_or_roles_than_these`).
- The built-in `admin-cli` gave a token for a password alone. Now it is off (`test_no_client_gives_a_token_for_a_password_alone[admin-cli]`, `test_the_built_in_admin_cli_gives_no_tokens_in_this_realm`).
- `make keycloak-reset` failed without `.env`, and `make keycloak-up` then made secrets the stored realm did not use. Now reset works by project name, and up refuses that case.
- The login helper reported a wrong password, a locked user, and stale secrets alike. Now it reports Keycloak's reason, and the tests assert it.
- Tests that could pass on a broken configuration: an unquoted port line, the `plain` PKCE method, a Keycloak without the realm shown as skipped. Now each is checked.
- Demo users got new IDs on every reset, and short group names could collide. Now IDs are fixed (`test_each_demo_user_keeps_one_id_across_resets`) and groups use full paths.
- The threat model and README were inaccurate or silent about the realm's other clients, the admin console, and recovery. Now both describe them.

Documented, not fixed: the brute-force defaults, the hand-checked digest, an edited `.env`, and the container-unreachable issuer.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **The redirect URI matches exactly.** The code Keycloak returns is worth a token to whoever holds it with the client secret, and the redirect URI decides who receives it. One wildcard or prefix match would let a link send alice's code to `localhost:3000.evil.test`. PKCE limits what a stolen code is worth, but exact matching stops the theft.
- **No optional scopes, and `groups` holds only groups.** M07-T04 will turn `groups` into the Groups that decide which Documents a User may read. With `microprofile-jwt` allowed, any client request could put role names into that claim: in the break, carol, who is in no group, got `"groups": ["administrator", "user"]`. A role is permission to operate the service, never to read content, so the claim must not be shaped by what the client asks for.
- **Passwords in an ignored file, placeholders in the realm.** The realm file is committed and shared; anything in it is public to everyone with the repository. The generated `.env` exists on one machine, is readable by its owner only, and is never printed. Compose's `:?` guard means Keycloak never starts with a missing secret, so no literal `${...}` becomes a password.
- **Signing keys generated, never committed.** A committed private key would let anyone with the repository sign a token for any User. Keycloak makes the key in its volume, and a reset makes a new one (the `kid` changed in the clean-volume run). So M07-T03 must fetch the public keys from JWKS by `kid`, never pin a key in configuration.
- **Left for later.** The API checks no token yet (M07-T03); groups become Group IDs in M07-T04; Open WebUI and containers need a back-channel address (M09-T08, M12-T03).

**Falsified assumption.** I assumed changing the realm file changes what Keycloak does. The break shows Keycloak imports the realm only into an empty volume: with `microprofile-jwt` allowed in the file, all 28 integration tests still passed until `make keycloak-reset`. So "the file is the configuration" was wrong; the volume is, and the file only seeds it.

**Break run.** With `"optionalClientScopes": ["microprofile-jwt"]`: the unit tests gave 1 failed, 18 passed (`test_a_client_cannot_ask_for_more_scopes_or_roles_than_these`); the integration tests gave 28 passed before the reset and 1 failed, 27 passed after it (`test_a_client_cannot_add_scopes_that_change_the_token[roles-as-groups]`). Restored, reset, and started again from an empty volume, with new secrets and a new signing key: 28 passed and 19 passed.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. The signature against a JWKS key chosen by `kid`, the exact issuer, the `knowledge-service-api` audience, the expiry and issue times, and the algorithm (RS256 only, never `none` or HS256). Keycloak signs tokens but cannot stop a forged, expired, or other-audience token from reaching the API: an `account-console` token from the same realm has no API audience and must be refused by the API itself.
2. M07-T04 builds the Authorization Context, and M07-T06 normalizes source grants. A group path the service has never seen should map to nothing: the User simply has no Group from it, so it grants no Document, and it is counted in telemetry by number, not by name.
3. Turning on `directAccessGrantsEnabled` for `open-webui` so a script can get tokens with a password. `test_the_client_uses_only_the_code_flow_with_pkce_and_exact_redirects` fails on the file, and `test_no_client_gives_a_token_for_a_password_alone[open-webui]` after a reset.
4. Every token signed after the reset would carry a new `kid` the pinned key cannot verify, so every login would be refused until someone edited the configuration; and a pinned key would never pick up a rotation. Fetching JWKS by `kid`, with a cache, avoids both.
5. An abstention, the same "no evidence" answer as for a Document that does not exist: her `administrator` role lets her operate the service, but she has no Group that an engineering Access Grant names, so retrieval never sees the Document.
