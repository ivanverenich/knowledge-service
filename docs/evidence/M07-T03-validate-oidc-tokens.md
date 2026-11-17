# M07-T03 — Validate OIDC tokens evidence

## Invariant

`OidcVerifier.verify` returns claims only for a token signed with RS256 by a
key the configured issuer publishes in its JWKS, whose `iss` is exactly that
issuer, whose `aud` names `knowledge-service-api`, and whose `exp`, `iat`,
and `nbf` hold within 30 seconds of skew. Every other token raises
`TokenRejected` with one reason. When no usable keys are cached and they
cannot be fetched, it raises `IdentityProviderUnavailable`: it never accepts
a token it could not check. Keys are fetched at most once per refresh
interval, and cached keys verify for at most the stale limit after the last
successful fetch. No error or log holds token or claim text.

## Expected failing check

Before the change, `uv run pytest tests/test_oidc.py -q` fails with
`ModuleNotFoundError: No module named 'knowledge_service.identity'`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What a token whose header says `"alg": "none"` gets, and whether the verifier fetches any keys first: `TokenRejected` with `ALGORITHM`, decided from the header alone; no discovery or JWKS request is made.
- After Keycloak rotates to a new key, how many fetches it takes before a token signed with the new key verifies: one JWKS fetch, because discovery is already cached; if the last attempt was less than 30 seconds ago, the token is refused as `UNKNOWN_KEY` until the interval passes, and then one fetch.
- What happens to a valid token when Keycloak has been down for 10 minutes, and for 2 hours: after 10 minutes it still verifies with the cached keys, because the last successful fetch is within the one-hour stale limit; after 2 hours every token gets `IdentityProviderUnavailable`.
- Which test fails if the fetch is allowed to follow redirects: `test_a_redirect_is_never_followed_off_the_issuers_origin`, and only that one.

## Design

- **Package.** `knowledge_service.identity` is new: checking who calls is the seam to an external system and fits no existing package, so ADR 0013 records the reason. `src/knowledge_service/identity/oidc.py` holds the verifier; PyJWT (`pyjwt[crypto]>=2.15.1,<3`) does the RSA signature, issuer, and audience checks.
- **Checks in order.** `verify` reads the header and refuses anything but `RS256` or a missing `kid` before any fetch; finds the key by `kid`; lets PyJWT check the signature, the exact `iss`, `aud` containing `knowledge-service-api`, and the required `exp`, `iat`, `iss`, `aud`, `sub`; then checks `exp`, `iat`, and `nbf` against the injected clock with 30 seconds of leeway, time values outside 0..9999-12-31 being malformed. It returns `VerifiedToken` (subject, key ID, expiry, read-only claims).
- **Two kinds of failure.** `TokenRejected(reason)` with one `Rejection` (`malformed`, `algorithm`, `unknown_key`, `signature`, `issuer`, `audience`, `missing_claim`, `expired`, `not_yet_valid`), raised with nothing chained; `IdentityProviderUnavailable` when no usable keys are cached or fetchable. Neither holds token or claim text, and the only log line names an error type.
- **Key cache.** Discovery, then JWKS, cached and timed by a monotonic clock: keys reused for 10 minutes; an unknown `kid` or old keys trigger one shared refresh task at most once per 30 seconds, which a cancelled caller cannot stop; after failed refreshes the cached keys verify for at most one hour after the last success. `VerifierPolicy` refuses settings that would fail closed while the provider is healthy.
- **Bounded fetches.** 5 seconds and 64 KiB per document, no redirects, no compressed bodies, a `jwks_uri` on the issuer's own origin, at most 32 keys, only RSA signing keys of at least 2048 bits, and a `kid` listed twice dropped.

## Verification

- Expected failing check: before the change, `knowledge_service.identity` did not exist (`uv run pytest tests/test_oidc.py` found no tests; with the test file first, it fails with `ModuleNotFoundError`).
- `uv run pytest tests/test_oidc.py -q`: 76 passed, covering valid, expired, wrong-audience, wrong-issuer, unknown-key, and key-rotation tokens. `uv run pytest -m integration tests/test_oidc_integration.py -q`: 6 passed against the running Keycloak (alice's access token verified with her ID and `/engineering`; her ID token `AUDIENCE`; expired `EXPIRED`; a changed signature `SIGNATURE`; the `master` realm `UNKNOWN_KEY`; a wrong issuer `IdentityProviderUnavailable`).
- Gates, with Keycloak running: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2457 passed (+76); `make test-integration` 412 passed (+6); `make docs-check` passed; `tests/test_threat_model.py` 42 passed.
- Deliberate break: without `follow_redirects=False` in `_fetch`, 1 failed, 75 passed: `test_a_redirect_is_never_followed_off_the_issuers_origin` (`DID NOT RAISE IdentityProviderUnavailable`, so a token signed by keys behind a redirect to `evil.test` was accepted). Restored: 76 passed. The predictions held: `alg: none` is refused before any fetch, a rotated key needs one JWKS fetch, stale keys work at 10 minutes and fail closed at 2 hours.
- Known limits: no endpoint calls the verifier yet (M07-T04); no maximum token lifetime and no token-type check, so a Keycloak ID token is refused only by its audience; a withdrawn key can verify for up to 10 minutes; the issuer is unreachable from containers (M09-T08, M12-T03).

## Review

An independent adversarial review fuzzed thousands of hostile tokens and key documents, tried `none`, HS256 with the public key as secret, other algorithms, `jku`/`jwk`/`x5u`/`crit` headers, odd `kid`, `iss`, and `aud` types, and attacked the cache with races, cancellations, and clock jumps; none of these got a forged token accepted. It found these problems, all fixed, each with a test that fails on the first version:

- A `jwks_uri` with a control character raised an untyped `httpx.InvalidURL`, and the bad address stayed cached forever. Now the URI is validated, every refresh failure is `IdentityProviderUnavailable`, and the address is kept only after a successful fetch (`test_a_jwks_uri_httpx_cannot_request_fails_closed_and_recovers`).
- A client that follows redirects fetched keys from another host and accepted a token they signed. Now no fetch follows a redirect (`test_a_redirect_is_never_followed_off_the_issuers_origin`).
- The cache used the wall clock, so a step back kept withdrawn keys and refused new ones. Now it uses a monotonic clock (`test_a_wall_clock_stepping_back_neither_keeps_nor_hides_keys`).
- Cancelling the request that was refreshing made every waiting request fail for 30 seconds. Now the refresh is one shielded task (`test_a_caller_cancelled_mid_refresh_does_not_stall_the_others`).
- A policy could fail closed while the provider was healthy. Now `VerifierPolicy` refuses it (`test_a_policy_that_could_fail_closed_while_the_provider_is_up_is_refused`).
- Huge or out-of-range time claims raised `OverflowError`, `OSError`, or `ValueError`. Now they are `MALFORMED` (`test_an_expiry_that_is_not_a_number_is_refused`).
- A gzip key document expanded in memory before the size check. Now compressed documents are refused (`test_a_compressed_key_document_is_refused`).
- A token was accepted at the exact second its leeway ended. Now that second is expired.
- A suppressed PyJWT error with token text stayed chained to the rejection. Now nothing is chained (`test_no_token_or_claim_text_reaches_errors_or_logs`).
- Thirteen one-line mutations of the module (size limit, timeout, status check, origin check, key count, discovery reset, key length, lock, token size, log text, and others) passed every test. Each now has a test that fails; the only surviving mutant, the non-object check, is redundant with the refresh's catch-all.

Documented, not fixed: no maximum token lifetime and no token-type check.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **The verifier fixes the algorithm.** The header is written by whoever made the token. If it chose the algorithm, `none` would need no signature and `HS256` would let anyone holding the public key sign. So `RS256` is the only answer, checked before any key is fetched, and the key itself is bound to `RS256`.
- **Keys come from JWKS by `kid`, never from configuration.** M07-T02 showed that a reset gives a new key; a pinned key would refuse every login after it and never follow a rotation. Fetching by `kid` follows rotation on its own, and the refresh interval keeps random `kid`s from turning the API into a flood against Keycloak.
- **An outage refuses tokens after the stale limit.** Cached keys keep the service up through a short outage, but a key Keycloak withdrew because it leaked must stop working at some point. One hour bounds that; after it, a 503 is safer than trusting keys nobody can confirm.
- **Two failure types.** A bad token and an unavailable provider need different answers (401 and 503) and different alerts, so they are different exceptions, and neither message carries token text.
- **Left for later.** M07-T04 calls the verifier from the endpoints and maps `sub` and `groups` to the Authorization Context; it may also require the access token's type.

**Falsified assumption.** I assumed the fetch was safe because it checks that `jwks_uri` is on the issuer's host. The review shows that check only covers the first URL: a client that follows redirects would take keys from wherever the redirect points, and accept a forged token. So "checking the address" was not enough; the fetch itself must refuse to move.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. It fetches and caches Keycloak's keys and picks one by `kid`; it follows key rotation and survives short outages without a restart; and it turns every way a token can be wrong (algorithm, signature, issuer, audience, times, malformed input) into one named reason without leaking the token.
2. A cache keyed by the token string would skip the expiry check: a token verified at 10:00 would still be "verified" at 10:20. It would also keep tokens signed by a withdrawn key. Any such cache must store the expiry and recheck the time on every hit.
3. Accepting any algorithm fails `test_an_unsigned_token_is_refused_before_any_key_is_fetched` and `test_the_public_key_used_as_an_hmac_secret_is_refused`; skipping the audience fails `test_a_token_for_another_audience_is_refused` and the ID-token integration test. Tests catch the change only if someone runs them, so the endpoints in M07-T04 should take the verifier as it is, with no switch to loosen it.
4. Count Keycloak's JWKS requests per minute while sending thousands of tokens with random `kid`s from many clients at once, and measure the API's latency for valid tokens during it. If requests stay at two per minute per API process and latency does not move, the interval is gentle enough; with many API processes, the total is per process, which might not be.
5. Old keys might have been withdrawn because they leaked; accepting them would let whoever holds the leaked key sign in as anyone. Users and on-call engineers, who see a total outage instead of a working service, would disagree, which is why the limit is an hour and not zero.
