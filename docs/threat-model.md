# Threat model

This model names what the service must protect, who may attack it, where
they can reach it, and what stops them. It covers ingestion, retrieval,
generation, agents, and operations as the code stands after M06, and names
the task that owns each control still to come. The invariants it protects
are the ones in the security model, `docs/security.md`; the trace at the
end is checked by `make docs-check`.

## Assets

| Asset | Why it matters | Where it lives |
|---|---|---|
| Document text and Chunks | Organizational knowledge; most of it is not for every reader | PostgreSQL `documents`, `chunks`; Evidence in a prompt |
| Access Grants and Authorization Versions | Decide who may read what; a wrong grant is a leak | PostgreSQL `access_grants` |
| Authorization Context | The verified User and Groups of one request | In memory, per request; built only by `Authenticator` from a verified token |
| Questions, Answers, Conversations | Reveal what a User asked and what they were told | PostgreSQL `conversations`, `messages`; the model provider's request |
| Citations and Evidence IDs | Tell the User where a claim came from; a fake one launders a claim | Built on the server per request |
| Evaluation Datasets and Runs | Hold Questions, expected Documents, and Authorization Contexts | `datasets/`, `artifacts/` |
| Credentials | Database URL, provider API keys, the OIDC client secret and demo passwords | Environment and settings, redacted in repr; the local Keycloak's in the Git-ignored `deploy/keycloak/.env`, generated per machine |
| Telemetry | Logs and, later, traces; a copy of everything they hold | Log records today |
| Availability and budget | Every Answer costs model tokens | Model providers, the database pool |

## Actors

| Actor | Trust | What they may try |
|---|---|---|
| User | Authenticated, reads only granted Documents | Read Documents not granted to them; learn that one exists; spend others' budget |
| Content manager, Evaluator, Administrator | Application Roles from the token's realm roles; never imply Document access | Use a role to read content the role does not grant |
| Document author | Writes into a Source; never trusted | Plant instructions, fake citations, invisible characters, or huge files in a Document |
| Model provider and its replies | Approved, but replies are untrusted data | Return invented Citations, unsupported claims, or malformed JSON; keep what it was sent |
| Unauthenticated client | None | Call the API without an identity; flood it |
| Operator | Reads logs and dashboards | See Question, Evidence, or Answer text in telemetry |
| Supply-chain attacker | None | Ship a hostile dependency or image |

## Trust zones

The zones are the ones drawn under "Trust zones" in `docs/security.md`. The boundaries that matter here:

- **Public to edge.** Everything from the browser is untrusted until the edge and the API have verified a token. Both answer endpoints accept only a request whose bearer token `OidcVerifier` verifies, and `Authenticator` builds its Authorization Context from that token's claims alone.
- **API to data.** PostgreSQL decides eligibility inside every search; a Chunk the reader may not see never leaves it.
- **API to model providers.** Evidence and the Question leave the private zone here; only eligible Evidence under opaque IDs may cross, and replies come back as untrusted data.
- **Workers to Sources.** Source content enters here and stays untrusted data for its whole life.
- **Local model servers.** BGE-M3 and the reranker listen on 127.0.0.1 only and must name the pinned revision.

## Entry points

| Entry point | Who reaches it | Today |
|---|---|---|
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. Needs a bearer token and the `user` role, both checked before the body is read; the Evidence source searches as the token's User and Groups |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: needs a bearer token and the `user` role, then sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors; the Redis queue in M08 |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
| OpenAI-compatible adapter, Open WebUI | Users | M09 |
| MCP server and agent tools | Agents | M11 |

## Abuse cases and mitigations

### Ingestion

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A Source reports no readers, and the Document becomes public by mistake | Only an item the Source marks public gets a public grant; any other item is readable by nobody until its principals are mapped | Source group normalization (M07-T06) |
| A permission is removed, but the old Chunks stay searchable until cleanup | Grants and Chunks change in one transaction; a tombstone makes a Document unavailable at once | Priority revocation sync (M07-T10) |
| A file removed or unreadable deletes or keeps the wrong Documents | An unreadable folder is unavailable, not deleted; a failed pass tombstones nothing | Incremental sync (M08-T10) |
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one | Retrieved-instruction hardening (M07-T13) |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | Jobs are stored with typed errors and bounded retries | Job idempotency (M08-T03) |

### Retrieval

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT | Adversarial suite (M07-T15) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query | ACLs in vector and lexical search (M07-T07, M07-T08) |
| A reranker or a ranking model is shown text the reader may not see | Only eligible Candidates reach any reranker | None |
| A Question proves a Document exists by the way it fails | Empty, unauthorized, and withdrawn read the same | Existence leaks (M07-T11) |
| A cache shared across readers serves one reader's Candidates or Answer to another | No cache of Candidates, Evidence, or Answers exists; Redis is for ephemeral coordination only (ADR 0011) | Adversarial suite covers caches (M07-T15) |

### Generation

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| Retrieved text tells the model to ignore the policy or reveal other Documents | The policy is fixed; the Question and Evidence are one escaped JSON document; only eligible Evidence is in the prompt | Retrieved-instruction hardening (M07-T13) |
| The model invents a Citation or cites another request's Evidence | Opaque per-request Evidence IDs; `resolve_citations` refuses any other reference, the reply is `invalid`, and the abstention replaces any text already streamed | None |
| An Answer cites a Chunk revoked after retrieval | None in any served path yet: `confirm_citations` exists and is tested, but nothing calls it | Citation reads with current grants (M07-T09) |
| An old Answer in History repeats revoked content | History keeps an Answer only while everything it cited is current and visible | None |
| A follow-up rewrite or History carries another User's text into a prompt | History is read only from the reader's own Conversation; the rewriter gets only that History's earlier Questions, never an Answer | None |
| The provider keeps Questions and Evidence | The grounded path asks it not to (`store=False`); `POST /v1/answer` does not | Versioned Question route (M09-T01) |
| A Question floods the model to run up cost | On the grounded path, context and token budgets and one deadline per stream; `POST /v1/answer` sets no output limit | Identity-aware rate limits (M09-T10) |

### Agents

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A tool call runs with more access than the User who started it | No agents or tools exist; the default path answers without them (ADR 0007) | Read-only tools carrying the Authorization Context (M11-T01), MCP server re-authorizing every call (M11-T06) |
| Retrieved text makes an agent call a tool | None needed yet | Agent prompt-injection tests (M11-T14) |

### Operations

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A client with no identity reads or spends | Both answer endpoints need a bearer token before the body is read; no other header, cookie, or body field names the reader; bodies over 64 KiB are refused; without an authenticator the endpoints answer 503 | Identity-aware rate limits (M09-T10) |
| A forged, expired, or other-audience token is accepted | `OidcVerifier`: RS256 only, a key from the issuer's JWKS, the exact issuer, the API audience, and the times within 30 seconds of skew; `Authenticator` calls it on every answer request | None |
| A token widens what its User reads: issued to another client, or with crafted group names | `azp` must be a trusted client; `groups` must be a list of at most 64 names, and a path with `~`, a hidden character, or no leading `/` is left out; Keycloak escapes `/` inside group names, so a group named `a/b` never becomes group `b` inside `a` | Source group normalization (M07-T06) |
| A role widens what its holder reads, or a caller without the role asks | `Caller` keeps the reader and the roles apart: the reader comes only from `sub` and `groups`, the roles only from realm roles, and no role permits reading a Document; both answer endpoints need the `user` role before the body is read, roles do not inherit, and an operation no role lists is denied; a test lists every route with its operation, so a new route fails until it has one; the realm maps roles to users only, never to groups or through composite roles | Role checks on the Source, synchronization, evaluation, and policy entry points when they exist |
| Random key IDs make the API flood the identity provider, or an outage opens the API | An unknown key ID refreshes the keys at most once per 30 seconds; cached keys outlive failed refreshes for at most an hour, then every token is refused as unavailable | None |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; marker tests prove no planted text reaches them | Redacted logs and traces (M07-T12) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | Durable audit events (M07-T14) |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |

## Invariant trace

Each row traces one invariant under "Invariants" in `docs/security.md` to its control and to at least one test owner: a test pytest collects, written `tests/<file>.py::<test>`, or a task not yet completed that will add one. `make docs-check` fails when an invariant has no row, a test owner no longer exists, or a named task is already completed. Owners in `*_integration.py` files run under `make test-integration`, which CI does not run yet.

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `M07-T07`, `M07-T08` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `M07-T10` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. No Evidence source over the database takes the reader yet (M07-T07, M07-T08).
- Groups from the identity provider become Group IDs by their path; Source groups get their own mapping in M07-T06. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
