# knowledge-service learning project

This repository currently contains the implementation curriculum for a standalone, permission-aware, bilingual organizational knowledge service. It deliberately contains no application scaffold: creating the repository and the first executable slice are learning tasks.

Start with [the curriculum index](docs/curriculum/README.md). Read [the domain language](CONTEXT.md), [architecture](docs/architecture.md), [security model](docs/security.md), [evaluation strategy](docs/evaluation.md), and [operations model](docs/operations.md) as their tasks become unblocked—not all at once.

## Intended outcome

The completed system will ingest local documents and Confluence, normalize content and access rules, index English and Ukrainian text, and answer questions with verified citations. It will expose a native API and an OpenAI-compatible adapter for Open WebUI. The core remains deterministic; agent, MCP, Kubernetes, vLLM, and QLoRA work arrives as measured extensions.

## Planning constraints

- Dependency-ordered rather than calendar-ordered.
- Tasks target 30–90 minutes and end with observable evidence.
- The critical path is sized for roughly twelve intensive weeks; overflow work is marked optional.
- Python 3.14 is the application runtime. Python 3.12 is the specialized ML/vLLM runtime.
- The application starts with managed OpenAI models, then proves local and alternative adapters against the same contracts.
- English and Ukrainian, including cross-language retrieval, are evaluated separately.

## Navigation

- [Curriculum and next-task procedure](docs/curriculum/README.md)
- [Roadmap and milestone dependencies](docs/curriculum/ROADMAP.md)
- [Task execution guide](docs/curriculum/TASK-GUIDE.md)
- [Architecture](docs/architecture.md)
- [Security](docs/security.md)
- [Evaluation](docs/evaluation.md)
- [Operations](docs/operations.md)
- [Primary-source research](docs/research/primary-sources.md)
- [Architectural decisions](docs/adr/README.md)
- [Reference-project lessons](docs/reference-project-lessons.md)

## Scope guardrail

These files describe work; they are not the finished implementation. Do not skip a task merely because a later document shows the intended design. The purpose is to reproduce the reasoning, tests, failure evidence, and operational understanding one small checkpoint at a time.
