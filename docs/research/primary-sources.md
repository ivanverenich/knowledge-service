# Primary sources for the `knowledge-service` curriculum

> Reviewed on **2026-09-21**. This is a curriculum source map, not a dependency lockfile. The implementation tasks should pin concrete versions and re-check mutable product documentation before use. Every link below is an official document, specification, first-party repository/model card, or original paper.

## Python and tooling

- [Python 3.14 documentation](https://docs.python.org/3.14/) is the language/runtime source of truth; in particular, [`asyncio`](https://docs.python.org/3.14/library/asyncio.html) is for concurrent I/O using `async`/`await`, not a substitute for CPU parallelism.
- [uv project documentation](https://docs.astral.sh/uv/concepts/projects/) defines `pyproject.toml`, environments, lockfiles, and reproducible sync. Its [Python version documentation](https://docs.astral.sh/uv/concepts/python-versions/) supports managed, per-project runtimes, which permits Python 3.14 for the application and Python 3.12 for ML-serving/training environments.
- [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/) validate untrusted boundary data and emit JSON Schema. Use them at HTTP/provider/message boundaries; do not confuse transport validation with domain invariants.

## FastAPI and APIs

- [FastAPI async guidance](https://fastapi.tiangolo.com/async/) says to use `async def` when called libraries are awaitable; ordinary `def` path operations and dependencies run in a thread pool. Teach cancellation, blocking-call detection, and bounded concurrency separately.
- [FastAPI's SSE guide](https://fastapi.tiangolo.com/tutorial/server-sent-events/) documents `text/event-stream`, event IDs/retry fields, `Last-Event-ID`, and typed serialization. Native SSE was added in FastAPI 0.135.0, so tasks must pin and verify the version. For lower-level streams, [`StreamingResponse`](https://fastapi.tiangolo.com/advanced/stream-data/) accepts async iterables but leaves framing and encoding to the application.
- [OpenAPI metadata and documentation](https://fastapi.tiangolo.com/tutorial/metadata/) show FastAPI's generated OpenAPI schema and Swagger UI/ReDoc endpoints. Treat the generated schema, error forms, and tags as a tested public contract.
- The [HTTP semantics specification](https://www.rfc-editor.org/rfc/rfc9110) and [SSE living standard](https://html.spec.whatwg.org/multipage/server-sent-events.html) are the protocol authorities for status codes, caching, disconnection, reconnection, and event framing.

## PostgreSQL pgvector and retrieval

- PostgreSQL full-text search turns documents into `tsvector`, queries into `tsquery`, and matches them with `@@`; it also supplies ranking, highlighting, and indexes ([overview](https://www.postgresql.org/docs/18/textsearch.html), [controls](https://www.postgresql.org/docs/18/textsearch-controls.html)).
- [Text-search configurations](https://www.postgresql.org/docs/18/textsearch-configuration.html) bind parsers to dictionaries. The [`simple` dictionary](https://www.postgresql.org/docs/18/textsearch-dictionaries.html) lowercases but does not stem; custom Ispell/Hunspell dictionaries can add morphology. Therefore, `simple` is only a Ukrainian token baseline, not evidence of strong Ukrainian lexical retrieval.
- The first-party [pgvector README](https://github.com/pgvector/pgvector/blob/master/README.md) documents exact search, HNSW/IVFFlat trade-offs, cosine/L2/inner-product operators, iterative scans, filtered ANN behavior, production index creation, and `EXPLAIN (ANALYZE, BUFFERS)`. ACL-filtered ANN recall must be evaluated rather than assumed.
- The original [Reciprocal Rank Fusion paper](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf) proposes a simple rank-based fusion method and reports gains over individual systems and other fusion methods. Rank fusion avoids pretending lexical and vector scores share a calibrated scale.

## Redis and jobs

- The [Dramatiq guide](https://dramatiq.io/guide.html) documents Redis-backed actors, JSON-encodable arguments, exponential-backoff retries, dead letters, retry/age limits, and `StubBroker` testing. It explicitly assumes actors are idempotent, aligning with at-least-once ingestion design.
- The [RedisBroker API](https://dramatiq.io/reference.html#dramatiq.brokers.redis.RedisBroker) is the configuration reference; isolate broker setup from ingestion business logic.
- [Redis Streams](https://redis.io/docs/latest/develop/data-types/streams/) provide IDs, consumer groups, acknowledgement, and claiming, but persistence/replication settings still determine loss behavior. [Redis Pub/Sub](https://redis.io/docs/latest/develop/interact/pubsub/) is at-most-once and must not be presented as durable job delivery.
- [Redis distributed-lock guidance](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/) explains safety, liveness, and timing assumptions. Locks should prevent duplicate coordination work, while database constraints/idempotency preserve correctness.

## OpenAI and Open WebUI

- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) constrain supported responses to JSON Schema, but refusals and incomplete outputs remain explicit paths the application must handle.
- [Function calling](https://developers.openai.com/api/docs/guides/function-calling) is a multi-step exchange: the model proposes calls, the application validates/authorizes and executes them, then returns results. Strict schemas improve shape conformance; they do not grant authority or make tool content trustworthy.
- [Embeddings](https://developers.openai.com/api/docs/guides/embeddings) cover vector use for search and similarity. Batch/input limits, dimensions, normalization, and pricing belong in adapter contract tests/configuration rather than domain code.
- [Open WebUI's OpenAI-compatible provider guide](https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/) requires `POST /v1/chat/completions`, recommends `GET /v1/models`, and expects streaming/common chat parameters. This supports a thin compatibility adapter over the richer native RAG API.

## OIDC Keycloak and security

- [OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html) defines the identity layer over OAuth 2.0 and ID-token semantics; [OIDC Discovery](https://openid.net/specs/openid-connect-discovery-1_0.html) exposes issuer, endpoints, and JWKS metadata. Validate signature, issuer, audience, and time claims—never merely decode JWTs.
- Keycloak recommends standards-based OIDC libraries in its [application-securing overview](https://www.keycloak.org/securing-apps/overview). Its [server administration guide](https://www.keycloak.org/docs/latest/server_admin/) defines realms, users, groups, role mappings, and protocol mappers. Keep application roles separate from source document ACLs.
- [OWASP authorization guidance](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html) calls for least privilege, deny-by-default, validation on every request, and authorization tests. Enforce ACLs inside every lexical/vector query before chunks reach reranking, prompts, traces, or caches.
- [OWASP's LLM Prompt Injection guidance](https://genai.owasp.org/llmrisk/llm01-prompt-injection/) treats external content as untrusted and recommends constrained privileges plus human approval for high-impact actions. The curriculum's agents remain read-only and inherit the initiating user's authorization.

## Confluence

- The [Confluence REST v1 introduction](https://developer.atlassian.com/cloud/confluence/rest/v1/intro/) covers auth, expansions, CQL, and `start`/`limit` pagination. Expanded bodies can change effective page sizes, so connectors must follow returned pagination rather than assume the requested limit.
- [REST v2 Pages](https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-page/) returns stable page IDs, hierarchy/space data, status, version metadata, body representations, and cursor pagination via `Link`; results are limited to pages viewable by the authenticated principal.
- The [content-permission API](https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-content-permissions/) evaluates effective permission across site permission, space permission, and content restrictions. Page restrictions alone are not a complete ACL model, and checking another subject requires elevated Confluence authority.
- [Confluence OAuth scopes](https://developer.atlassian.com/cloud/confluence/scopes-for-oauth-2-3LO-and-forge-apps/) do not override product permissions. Request least privilege and explicitly model permission/group scopes required for ACL synchronization.

## Multilingual retrieval and reranking

- The original [BGE-M3 paper](https://arxiv.org/abs/2402.03216) and first-party [BGE-M3 model card](https://huggingface.co/BAAI/bge-m3) describe dense, sparse, and multi-vector retrieval, 100+ languages, and inputs up to 8,192 tokens. These broad claims do not establish Ukrainian or EN↔UK quality; measure those slices separately.
- The first-party [BGE reranker v2 M3 card](https://huggingface.co/BAAI/bge-reranker-v2-m3) describes a multilingual cross-encoder scoring query–passage pairs. Compare quality, batching, throughput, and latency against no reranker and LLM reranking.
- [BEIR](https://arxiv.org/abs/2104.08663) found BM25 a strong baseline and reranking/late interaction effective but comparatively expensive across heterogeneous tasks. This supports baseline-first, evidence-driven retrieval progression rather than adopting complexity by default.
- The PostgreSQL dictionary limitations above make Ukrainian morphology a measured risk. Maintain distinct test strata for Ukrainian→Ukrainian, English→English, Ukrainian→English, and English→Ukrainian; never hide failures in one aggregate score.

## Evaluation and observability

- [RAGAS](https://aclanthology.org/2024.eacl-demo.16/) separates retrieval relevance/focus, answer faithfulness, and answer quality. Automated judge metrics are estimates: pin prompts/models, preserve raw structured results, and calibrate against bilingual human review.
- [BEIR's paper and benchmark](https://github.com/beir-cellar/beir) provide reproducible IR evaluation across heterogeneous datasets. Curriculum datasets should retain query, relevant document/chunk, answerability, language direction, difficulty, and authorization context so Recall@k, MRR, and nDCG are interpretable.
- [OpenTelemetry Python instrumentation](https://opentelemetry.io/docs/languages/python/instrumentation/) covers traces, metrics, and logs; [instrumentation libraries](https://opentelemetry.io/docs/languages/python/libraries/) cover frameworks and clients. Use automatic edge spans plus manual domain spans for ingestion, retrieval, reranking, and generation.
- [OpenTelemetry GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) standardize model/agent telemetry but warn that prompts, outputs, and tool arguments may contain sensitive data. Production defaults should record safe metadata and make content capture an explicit redacted opt-in.

## Agents LangGraph and MCP

- LangGraph's [overview](https://docs.langchain.com/oss/python/langgraph/overview) positions it as low-level orchestration for long-running, stateful agents; [durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution), [persistence](https://docs.langchain.com/oss/python/langgraph/persistence), and [interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts) justify it only when workflows need resumability, branching, retries, or human review.
- The current [MCP 2026-07-28 release description](https://blog.modelcontextprotocol.io/posts/2026-07-28/) documents a stateless protocol core, per-request metadata, discovery, cacheable list/read results, hardened authorization, JSON-Schema tool contracts, and Tasks as an extension. Target a named protocol revision and test conformance; do not code against a generic idea of “MCP.”
- The [MCP specification](https://modelcontextprotocol.io/specification/2026-07-28) is authoritative for clients, servers, tools, resources, transport, and authorization. Tool schemas validate representation, while the server must independently authenticate, authorize, constrain budgets, and validate results.

## Containers CI and supply chain

- Docker's [build best practices](https://docs.docker.com/build/building/best-practices/) recommend small trusted bases, multi-stage builds, ephemeral containers, version awareness, and non-root `USER`; [multi-stage builds](https://docs.docker.com/build/building/multi-stage/) reduce final image contents and attack surface.
- GitHub's [dependency review](https://docs.github.com/en/code-security/concepts/supply-chain-security/dependency-review) can fail pull requests that introduce vulnerable dependencies; [code scanning](https://docs.github.com/en/code-security/concepts/code-scanning/code-scanning) finds vulnerabilities/errors; [secret scanning](https://docs.github.com/en/code-security/concepts/secret-security/secret-scanning) searches history and collaboration surfaces for credentials.
- [GitHub artifact attestations](https://docs.github.com/en/actions/concepts/security/artifact-attestations) create signed build-provenance claims and can associate an SBOM. Immutable image digests plus provenance/SBOM are release evidence, not replacements for dependency and container vulnerability scanning.
- GitHub's [OIDC guidance for AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws) supports short-lived cloud credentials instead of stored AWS keys; restrict the IAM trust policy to expected repositories, refs, and environments.

## AWS and Terraform

- Amazon ECS recommends distinct [task execution and task roles](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/security-iam-roles.html): the former lets ECS pull images/write logs/read referenced secrets, while application calls use the task role. Grant least privilege to each API and worker separately.
- [RDS for PostgreSQL](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_PostgreSQL.html) supports snapshots, automated backups, point-in-time restore, Multi-AZ, replicas, VPC placement, and TLS. The curriculum must execute a [backup/restore drill](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/CHAP_CommonTasks.BackupRestore.html), not merely enable backups.
- [ElastiCache security](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/security.html) covers network isolation, IAM/service roles, encryption, and authentication. Treat Redis as ephemeral queue/coordination infrastructure and retain durable business state in PostgreSQL.
- [Secrets Manager best practices](https://docs.aws.amazon.com/secretsmanager/latest/userguide/best-practices.html) cover encryption, rotation, least privilege, monitoring, caching, and private-network access. Do not bake or log secrets.
- HashiCorp's first-party [Terraform AWS curriculum](https://developer.hashicorp.com/terraform/tutorials/aws-get-started) covers init/validate/plan/apply, variables/outputs, modules, state, and destroy. Remote state, locking, reviewable saved plans, provider/version constraints, and explicit teardown/cost controls are required for shared/ephemeral environments.

## Kubernetes and Helm

- Kubernetes' official docs define [Deployments](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/), [Services](https://kubernetes.io/docs/concepts/services-networking/service/), [startup/readiness/liveness probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/), and [container resource requests/limits](https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/). A health endpoint, a traffic-readiness signal, and a restart signal solve different problems.
- [Horizontal Pod Autoscaling](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/) reacts to metrics; [disruption budgets](https://kubernetes.io/docs/concepts/workloads/pods/disruptions/) limit voluntary disruption but do not guarantee availability. Test API and worker scaling independently.
- Helm templates generate Kubernetes manifests from charts and values ([template guide](https://helm.sh/docs/chart_template_guide/)); the [chart best-practices guide](https://helm.sh/docs/chart_best_practices/) covers structure, values, labels, pods, dependencies, and RBAC. Render, lint, schema-check, and smoke-test the chart rather than only verifying installation syntax.

## Local inference and fine-tuning

- [PyTorch installation guidance](https://pytorch.org/get-started/locally/) selects builds by OS/accelerator, while [MPS backend notes](https://docs.pytorch.org/docs/stable/notes/mps.html) cover Apple GPU execution. An M1 Pro smoke test is useful, but CUDA-oriented throughput/training conclusions require a Linux GPU environment.
- [vLLM GPU installation](https://docs.vllm.ai/en/latest/getting_started/installation/gpu/) documents accelerator-specific wheels/builds and Python constraints; [OpenAI-compatible serving](https://docs.vllm.ai/en/latest/serving/online_serving/) exposes model-serving APIs. Keep this Python 3.12 runtime isolated from the Python 3.14 application and benchmark latency, throughput, batching, memory, and cancellation.
- The original [LoRA paper](https://arxiv.org/abs/2106.09685) freezes base weights and trains low-rank update matrices; [QLoRA](https://arxiv.org/abs/2305.14314) backpropagates through a frozen quantized model into LoRA adapters. Their efficiency claims do not imply a project-specific quality gain.
- Hugging Face's first-party [PEFT LoRA guide](https://huggingface.co/docs/peft/en/package_reference/lora) documents adapter configuration and QLoRA-style `all-linear` targeting; its [quantization guide](https://huggingface.co/docs/peft/developer_guides/quantization) covers quantized-base preparation. Compare the approved, lineage-tracked adapter against prompting/RAG on a held-out bilingual set before accepting it.

## Curriculum-level conclusions

- Start with deterministic baselines, then admit chunking, hybrid retrieval, reranking, rewriting, agents, and fine-tuning only when a versioned evaluation slice demonstrates a benefit worth its latency, cost, and failure modes.
- Provider compatibility means project-owned behavioral contracts and contract tests—not an assumption that SDKs, scores, token limits, streaming events, or error semantics are interchangeable.
- Authorization is an end-to-end invariant: source sync, storage queries, reranking, prompting, citations, agents, MCP calls, audit, telemetry, and deletion must all preserve the initiating user's effective access.
- “Multilingual” is a hypothesis for Ukrainian and cross-language retrieval. Keep language-direction metrics and bilingual human calibration visible throughout the curriculum.
