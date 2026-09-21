# Architecture

## Shape

The system is a modular monolith: one repository and one domain model, deployed as an API process and one or more worker processes. This is a distribution choice, not permission for domain logic to leak into HTTP routes or queue handlers.

```mermaid
flowchart LR
    U[User] --> W[Open WebUI]
    W --> C[OpenAI-compatible adapter]
    A[Native client] --> N[Native HTTP interface]
    C --> Q[Question-answer module]
    N --> Q
    Q --> R[Retrieval module]
    R --> P[(PostgreSQL + pgvector)]
    Q --> M[Chat-model port]
    S[Source adapters] --> I[Ingestion module]
    I --> P
    I --> X[(Redis jobs)]
    X --> K[Worker process]
    K --> I
    ID[OIDC provider] --> N
    ID --> C
```

## Deep modules and seams

A module earns its interface by hiding meaningful behavior and invariants. Tests and callers use the same interface.

| Module | Interface promises | Hidden implementation |
|---|---|---|
| Source synchronization | Start/resume a run and report an outcome | pagination, checkpoints, retries, version comparison, tombstones |
| Document normalization | Convert a Source Record into a Document | format parsing, sanitization, structural metadata, language detection |
| Indexing | Reconcile one Document with searchable state | chunk replacement, embedding batches, lexical vectors, ACL updates |
| Retrieval | Return authorized ranked Candidates | ACL predicates, dense/lexical queries, fusion, reranking, budgets |
| Question answering | Produce an Answer or Abstention | history rewriting, token budgeting, evidence formatting, citation validation |
| Evaluation | Execute an Evaluation Dataset and emit comparable results | concurrency, caching policy, scorers, judge calls, artifacts |

Use a port at a seam for true external systems: model providers, Confluence, identity, and optional external search. Each port must have a production adapter and a deterministic test adapter. PostgreSQL is tested through real PostgreSQL in integration tests rather than hidden behind a repository interface for every query.

## Dependency direction

```mermaid
flowchart TD
    Transport[HTTP / SSE / queue / MCP adapters] --> Application[Application workflows]
    Application --> Domain[Domain values and policies]
    Application --> Ports[External-system ports]
    Adapters[OpenAI / Confluence / OIDC adapters] --> Ports
    Persistence[PostgreSQL implementation] --> Application
```

Domain values do not import FastAPI, Dramatiq, SQLAlchemy, OpenAI SDKs, or LangGraph. Transport adapters translate protocol data into application requests and translate results back. The OpenAI-compatible interface adapts to the same question-answer module as the native interface.

## Runtime flows

### Synchronization

```mermaid
sequenceDiagram
    participant Scheduler
    participant Queue
    participant Worker
    participant Source
    participant DB as PostgreSQL
    participant Embedder
    Scheduler->>Queue: enqueue source + checkpoint
    Queue->>Worker: at-least-once delivery
    Worker->>Source: fetch page
    Source-->>Worker: records + next checkpoint
    Worker->>DB: compare content/auth versions
    Worker->>Embedder: embed changed chunks only
    Worker->>DB: transactionally publish current state
    Worker->>DB: save checkpoint and run outcome
```

### Answering

```mermaid
sequenceDiagram
    participant User
    participant API
    participant Retrieval
    participant DB as PostgreSQL
    participant Ranker
    participant Model
    User->>API: Question + verified identity
    API->>Retrieval: Question + Authorization Context
    Retrieval->>DB: lexical/vector search with ACL predicates
    DB-->>Retrieval: authorized Candidates
    Retrieval->>Ranker: bounded Candidate set
    Ranker-->>Retrieval: ranked Evidence
    API->>Model: instructions + numbered Evidence
    Model-->>API: streamed Answer events
    API->>API: validate citations
    API-->>User: Answer or safe failure
```

## Deliberate exclusions from the default path

- LangGraph and multi-agent orchestration do not replace ordinary question answering.
- MCP is an additional protocol adapter, not the owner of search logic.
- Redis is not the durable record for conversations, jobs, or Documents.
- Generated-answer caching is excluded until permission and staleness behavior is proven.
- Kubernetes does not change application module interfaces.
