# Skills and technology coverage

This matrix maps the original learning goals to concrete evidence. “Covered” means implemented and verified, not merely named.

| Area | Primary tasks | Evidence |
|---|---|---|
| Python 3.14, typing, packaging | M00-T03–T08 | locked environment, typed settings, checks |
| Architecture and deep modules | M01-T03–T05, M03-T06, M04-T06, M08-T10 | small interfaces, fakes, contract suites, gates |
| Async and concurrency | M01-T08, M04-T03, M06-T09, M10-T11 | cancellation, deadlines, bounded concurrency |
| Testing | every task; M00-T06, M02-T12 | unit, property, contract, integration, failure drills |
| Git and CI/CD | M00-T02, M00-T09–T10, M12-T06–T14 | hooks, CI tiers, immutable promotion, recovery |
| FastAPI and REST | M01, M09 | native/OpenAI-compatible HTTP and SSE contracts |
| PostgreSQL | M02, M04, M05, M07 | migrations, transactions, FTS, pgvector, ACL SQL |
| Redis | M08-T01–T08, M09-T10 | jobs, coordination, rate limits, recovery |
| Distributed systems | M08, M12-T11–T15, M13 | replay, retries, deployment, restore, failure injection |
| LLM APIs and structured output | M01-T03–T10, M06 | ports, structured schemas, usage, citations |
| Context engineering | M06-T02–T03, M06-T11–T12 | evidence/history budgets and measured rewriting |
| Tool calling | M11-T01–T04 | bounded tool loop and graph |
| Embeddings and vector search | M04, M05-T08–T10 | versioned dense retrieval and multilingual comparison |
| Chunking | M03-T09–T11 | fixed baseline, structure-aware approach, provenance |
| Hybrid search | M05-T01–T06 | English/Ukrainian lexical search and RRF |
| Reranking | M05-T11–T13 | local cross-encoder and LLM comparison |
| LangGraph/LangChain | M11-T03–T05 | stateful bounded workflow; core avoids unnecessary framework use |
| MCP | M11-T06–T07 | authorized server and controlled client |
| Multi-agent | M11-T09–T14 | research/evaluation experiments with security and metrics |
| Datasets and retrieval metrics | M10-T01–T06 | 200-case bilingual dataset, Recall/MRR/nDCG |
| LLM-as-judge and human evaluation | M10-T07–T10 | rubric, calibration, two reviewers |
| Regression tests | M07-T15, M10-T13, M12-T07 | security and quality release gates |
| Observability/tracing | M10-T14–T17, M13-T14 | OTel, dashboards, alerts, release evidence |
| Latency and cost | M10-T12, M10-T16, M13-T15 | per-stage telemetry, load reports, budgets |
| Guardrails | M07, M11-T12–T14 | authorization, injection tests, tool/resource constraints |
| Docker | M12-T01–T05 | minimal non-root images and full local stack |
| AWS | M13 | complete ephemeral ECS/RDS/Redis deployment |
| Kubernetes | M14 | Helm on kind and optional EKS |
| Terraform | M13, M14-T12 | full ECS environment and optional EKS extension |
| PyTorch/Transformers | M05-T09–T13, M15 | local embedding/reranker and model runtime work |
| Fine-tuning and LoRA/QLoRA | M15-T08–T15 | preregistered bounded experiment and model card |

## Portfolio narrative

The strongest interview story is not “I used every tool.” It is: the baseline exposed a measured failure; a task introduced the smallest justified mechanism; bilingual, authorization, latency, cost, and operational evidence determined whether it stayed. The milestone gates require enough explanation and failure diagnosis to defend that story.
