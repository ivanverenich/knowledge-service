# Architectural decision records

These records capture decisions that are costly to reverse, surprising without context, and based on real tradeoffs. Easy-to-change library choices stay in tasks rather than becoming ADR sediment.

1. [Modular monolith with API and worker processes](0001-modular-monolith-with-api-and-worker-processes.md)
2. [PostgreSQL and pgvector as the primary knowledge store](0002-postgresql-pgvector-as-primary-knowledge-store.md)
3. [Normalize and enforce access before retrieval](0003-normalize-and-enforce-access-before-retrieval.md)
4. [Native interface with an OpenAI-compatible adapter](0004-native-interface-with-openai-compatible-adapter.md)
5. [Incremental at-least-once synchronization](0005-incremental-at-least-once-synchronization.md)
6. [Bilingual and cross-language quality as a core requirement](0006-bilingual-and-cross-language-quality-is-a-core-requirement.md)
7. [Agents outside default question answering](0007-keep-agents-outside-default-question-answering.md)
8. [ECS as primary runtime and Kubernetes as an extension](0008-use-ecs-as-primary-and-kubernetes-as-an-extension.md)
9. [Separate Python runtimes for application and ML workloads](0009-split-python-runtime-for-application-and-ml-workloads.md)
10. [Production telemetry redacted by default](0010-redact-production-telemetry-by-default.md)
11. [Redis for ephemeral coordination only](0011-use-redis-for-ephemeral-coordination-only.md)
