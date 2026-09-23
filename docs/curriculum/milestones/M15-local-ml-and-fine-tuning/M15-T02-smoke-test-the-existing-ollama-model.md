---
id: M15-T02
title: "Smoke-test the existing Ollama model"
milestone: M15
track: optional
depends_on: ["M15-T01"]
estimate: 90m
topics: ["ollama","vllm","qlora"]
deliverables: ["Ollama adapter","smoke report"]
status: planned
---

# M15-T02 — Smoke-test the existing Ollama model

## Outcome

Call the installed Qwen 2.5 14B model through a local adapter, capture version/template/context behavior, and avoid assuming quality from successful generation.

## Why

Local inference and tuning are evidence-driven alternatives whose runtime, data, quality, and cost must be compared with managed models and RAG. Keep the implementation reproducible, bounded, and replaceable through the existing seam.

## Read

- [Task execution guide](../../TASK-GUIDE.md)
- [Architecture](../../../architecture.md)
- [Security model](../../../security.md)
- [Evaluation strategy](../../../evaluation.md)
- [Operations model](../../../operations.md)
- [Primary-source research](../../../research/primary-sources.md)

## Implement

1. Inspect prerequisite evidence and record the interface, cost ceiling, security constraints, and expected failed check or drill.
2. Call the installed Qwen 2.5 14B model through a local adapter, capture version/template/context behavior, and avoid assuming quality from successful generation.
3. Test success, resource limits, cancellation/retry, and failure/denial behavior at the interface; pin external artifacts and model/infrastructure versions.
4. Capture redacted reproducible evidence, actual spend where relevant, and safe teardown or rollback proof.

## Verify and accept

- A bilingual scripted smoke suite records latency, memory, structured-output, and tool-call behavior.
- Run the narrow check first, then relevant static, deterministic, integration, security, evaluation, or infrastructure validation.
- Confirm identity/secrets, immutable versions, budgets, and cleanup survive the full path.
- Experimental tasks require an explicit keep/reject decision; successful execution alone is not acceptance.

## Failure modes and progressive hints

1. Separate application behavior from provider, cluster, or model-runtime compatibility.
2. Inspect plan/rendered configuration and identity/resource limits before debugging runtime symptoms.
3. Reproduce at the smallest scale, preserve failure evidence, then prove cleanup before another paid run.

## Reflection

- What ongoing operational responsibility did this task create?
- Which measurement could reverse the technology choice?
- How would you migrate or remove this adapter without changing callers?

## Agent workflow

**Implementation prompt:** Inspect M15-T02, prerequisites, current interfaces, and cost/security constraints. Propose a bounded plan for: “Call the installed Qwen 2.5 14B model through a local adapter, capture version/template/context behavior, and avoid assuming quality from successful generation.” Wait for approval; report changes, exact validation, spend, and teardown.

**Review prompt:** Review M15-T02 for state/secret exposure, least privilege, version drift, cost risk, cleanup, reproducibility, evaluation validity, and interface leakage. Lead with evidence-backed findings.

## Git checkpoint

Commit only after acceptance and teardown/rollback evidence exists, using intent: `m15-t02: smoke test the existing ollama model`.
