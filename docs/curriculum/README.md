# Curriculum index

The curriculum is a dependency graph, not a checklist to complete in file-name order. Begin with the first unblocked critical task, keep evidence beside the implementation, and use milestone gates to test understanding.

## How to select the next task

1. Open [ROADMAP.md](ROADMAP.md).
2. Find a `critical` task whose `depends_on` tasks are complete.
3. Read only that task and its linked project/reference material.
4. Follow [TASK-GUIDE.md](TASK-GUIDE.md).
5. Mark status in your own tracking system after the acceptance evidence exists. The planning files remain reusable source material.

Use [PROGRESS.md](PROGRESS.md) as the in-repository tracking system.

Optional tasks are real implementations, not reading suggestions. Start them after their prerequisites when the critical path is healthy. If the twelve-week window tightens, preserve the critical path and move optional tasks to the overflow backlog.

## Milestones

| ID | Milestone | Outcome |
|---|---|---|
| M00 | Foundations | reproducible Python project and engineering loop |
| M01 | Walking skeleton | one tested synchronous question-to-answer path |
| M02 | Domain and persistence | durable model, migrations, deep module interfaces |
| M03 | Local ingestion | normalized Markdown/text/HTML/PDF synchronization |
| M04 | Dense retrieval | embeddings and permission-ready vector search |
| M05 | Bilingual hybrid retrieval | English/Ukrainian lexical+dense fusion and reranking |
| M06 | Answers and conversations | citations, abstention, streaming, bounded history |
| M07 | Identity and authorization | OIDC, roles, normalized Access Grants, adversarial tests |
| M08 | Jobs and Confluence | resilient incremental background synchronization |
| M09 | Product interfaces | native API, OpenAI compatibility, Open WebUI |
| M10 | Evaluation and observability | bilingual release gates, traces, metrics, feedback |
| M11 | Agents and MCP | bounded workflows and measured multi-agent experiments |
| M12 | Containers and CI/CD | hardened images, supply-chain checks, staged releases |
| M13 | AWS and Terraform | complete ephemeral ECS reference deployment |
| M14 | Kubernetes | Helm deployment to `kind` and optional EKS |
| M15 | Local ML and fine-tuning | Ollama/vLLM comparison and bounded QLoRA experiment |

## Understanding gates

Each milestone ends with a gate task. A gate requires four kinds of evidence: explain the design in your own words, update the relevant Mermaid diagram, diagnose an injected failure, and make a small un-scripted change. A green test suite alone does not pass a gate.
