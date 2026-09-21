# Operations model

## Environments

| Environment | Purpose | Data/model policy |
|---|---|---|
| Local | rapid implementation and failure exercises | synthetic/public data; managed or local models |
| CI | deterministic verification | fixtures and fakes; no production credentials |
| Staging | migrations, deployment, integration, evaluation | sanitized corpus and restricted credentials |
| Production | reference organizational deployment | private subnets, managed secrets, redacted telemetry |

## Service objectives

- Retrieval p95 below 750 ms under the defined ten-user-plus-ingestion load.
- Time to first streamed token p95 below 3 seconds, reporting provider latency separately.
- Typical managed Answer cost below approximately US$0.05 using configurable price data.
- Zero unauthorized Candidate, Evidence, Answer, or Citation exposure.

## Telemetry

Use OpenTelemetry for traces and metrics and structured JSON logs with shared request, Conversation, Synchronization Run, Evaluation Run, and job identifiers. Collect queue depth, run age, retry counts, retrieval-stage latency, Candidate counts, model tokens, estimated cost, streaming cancellation, and provider errors. Production content fields are redacted by default.

## Recovery and failure ownership

Every external call has one owner for timeout, retry, and idempotency. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path

Docker Compose supports local API, worker, PostgreSQL, Redis, Keycloak, Open WebUI, and observability. AWS uses public Open WebUI/edge and private ECS tasks, RDS PostgreSQL, ElastiCache, Secrets Manager, and telemetry exporters. Terraform produces ephemeral staging/portfolio environments with budget alarms and teardown instructions. Kubernetes is a later Helm-based deployment to `kind`, followed by an optional ephemeral EKS exercise.
