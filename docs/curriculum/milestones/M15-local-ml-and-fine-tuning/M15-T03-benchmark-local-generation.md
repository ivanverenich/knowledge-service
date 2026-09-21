---
id: M15-T03
title: "Benchmark local generation"
milestone: M15
track: optional
depends_on: ["M15-T02"]
estimate: 90m
topics: ["ollama","vllm","qlora"]
deliverables: ["local generation benchmark"]
status: planned
---

# M15-T03 — Benchmark local generation

## Outcome

Measure warm/cold latency, tokens per second, memory pressure, concurrency, cancellation, and Answer quality on the M1 Pro.

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
2. Measure warm/cold latency, tokens per second, memory pressure, concurrency, cancellation, and Answer quality on the M1 Pro.
3. Test success, resource limits, cancellation/retry, and failure/denial behavior at the interface; pin external artifacts and model/infrastructure versions.
4. Capture redacted reproducible evidence, actual spend where relevant, and safe teardown or rollback proof.

## Verify and accept

- The report states hardware, quantization, context, sampling, and repeatability limits.
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

**Implementation prompt:** Inspect M15-T03, prerequisites, current interfaces, and cost/security constraints. Propose a bounded plan for: “Measure warm/cold latency, tokens per second, memory pressure, concurrency, cancellation, and Answer quality on the M1 Pro.” Wait for approval; report changes, exact validation, spend, and teardown.

**Review prompt:** Review M15-T03 for state/secret exposure, least privilege, version drift, cost risk, cleanup, reproducibility, evaluation validity, and interface leakage. Lead with evidence-backed findings.

## Git checkpoint

Commit only after acceptance and teardown/rollback evidence exists, using intent: `m15-t03: benchmark local generation`.

