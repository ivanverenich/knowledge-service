# Security model

The threat model, `docs/threat-model.md`, names the assets, actors, entry
points, and abuse cases behind these rules, and traces each invariant to a
control and a test owner.

## Invariants

1. A Chunk is eligible for retrieval only when its Document is currently available and at least one normalized Access Grant matches the verified Authorization Context.
2. Authorization occurs inside lexical and vector queries. Post-retrieval filtering is defense in depth, never the primary control.
3. Restrictive permission changes make content unavailable before background cleanup completes.
4. Retrieved content is untrusted data. It cannot grant tools, alter policy, or supply identity claims.
5. Raw questions, Evidence, and Answers are absent from production telemetry unless an explicit, audited diagnostic policy enables them.
6. Agent and MCP tool calls carry the initiating Authorization Context and are re-authorized server-side.
7. ACL leakage, including through citations, caches, traces, evaluation artifacts, or error messages, blocks a release.

## Trust zones

Each arrow that enters a zone crosses a boundary, and the label says what
may cross it. The numbers mark the three checks every request meets, in
order: who the caller is, whether their role permits the operation, and
which records they may touch.

```mermaid
flowchart TB
    subgraph Public["Public internet: untrusted"]
        Browser["Browser"]
    end
    subgraph Edge["Edge"]
        WebUI["Open WebUI + TLS"]
    end
    IdP["OIDC identity provider<br/>users, groups, realm roles"]
    subgraph Private["Private zone"]
        API["API tasks<br/>1 identity: OidcVerifier, Authenticator<br/>2 role: require operation"]
        Worker["Workers<br/>synchronization, purge"]
    end
    subgraph Data["Private data"]
        PG[("PostgreSQL<br/>3 grants: eligible_chunks_predicate<br/>owner in every Conversation query a request reaches<br/>audit_events")]
        Redis[("Redis<br/>coordination only")]
    end
    Providers["Approved AI providers"]
    Sources["Configured Sources<br/>untrusted content"]

    Browser -->|"bearer token"| WebUI
    IdP -->|"signed tokens: sub, groups, roles"| WebUI
    IdP -->|"public signing keys"| API
    WebUI -->|"bearer token"| API
    API -->|"reads and writes as the caller: User and Group IDs"| PG
    API -->|"Question and eligible Evidence under opaque IDs"| Providers
    Providers -->|"replies, untrusted data"| API
    API --> Redis
    Worker -->|"Access Grants and Chunks, one transaction"| PG
    Worker --> Redis
    Sources -->|"content and permissions"| Worker
```

- **Identity** says who is asking: the User and Groups in a verified token,
  turned into an Authorization Context. Nothing else in a request names
  them.
- **Roles** say which operations the caller may run, such as asking or
  deleting their own Conversation. A role never lets anyone read a
  Document.
- **Grants** say which Documents a reader may see. They come from each
  Source's permissions and are checked inside PostgreSQL, in the same
  query that finds the Chunks.

## Threat-driven controls

| Threat | Primary controls | Required evidence |
|---|---|---|
| Cross-user document leakage | verified JWT, group normalization, query-time ACL predicates | adversarial integration tests with indistinguishable documents |
| Stale access after revocation | separate Authorization Version, priority sync, transactional unavailability | revocation race test |
| Prompt injection in documents | evidence delimiting, fixed policy, read-only tools, output validation | injected-document evaluation cases |
| Citation spoofing | opaque Evidence IDs and server-side validation | unknown/mismatched citation tests |
| Sensitive telemetry | configurable redaction and metadata allowlist | log/trace capture assertions |
| Queue replay | idempotency keys and transactional state transitions | duplicate-delivery test |
| Denial of wallet | rate limits, token/cost budgets, workflow limits | load and limit tests |
| Supply-chain compromise | lockfile, provenance, scanning, minimal images, SBOM | CI artifacts and policy results |

## Application roles

- **User** asks questions and manages their retained Conversations.
- **Content manager** configures Sources and starts Synchronization Runs.
- **Evaluator** manages approved Evaluation Datasets and runs paid evaluation.
- **Administrator** manages policy and operational configuration.

Application Roles never imply Document access.
