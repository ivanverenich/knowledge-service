---
id: M15-T11
title: "Configure a bounded QLoRA run"
milestone: M15
track: optional
depends_on: ["M15-T10"]
estimate: 90m
topics: ["ollama","vllm","qlora"]
deliverables: ["QLoRA training configuration"]
status: planned
---

# M15-T11 — Configure a bounded QLoRA run

## Outcome

Set model revision, quantization, adapters, target modules, sequence length, optimizer, schedule, checkpoints, seeds, logging, and hard cost/time limits.

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
2. Set model revision, quantization, adapters, target modules, sequence length, optimizer, schedule, checkpoints, seeds, logging, and hard cost/time limits.
3. Test success, resource limits, cancellation/retry, and failure/denial behavior at the interface; pin external artifacts and model/infrastructure versions.
4. Capture redacted reproducible evidence, actual spend where relevant, and safe teardown or rollback proof.

## Verify and accept

- A tiny overfit/sanity run validates the pipeline before paid training.
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

**Implementation prompt:** Inspect M15-T11, prerequisites, current interfaces, and cost/security constraints. Propose a bounded plan for: “Set model revision, quantization, adapters, target modules, sequence length, optimizer, schedule, checkpoints, seeds, logging, and hard cost/time limits.” Wait for approval; report changes, exact validation, spend, and teardown.

**Review prompt:** Review M15-T11 for state/secret exposure, least privilege, version drift, cost risk, cleanup, reproducibility, evaluation validity, and interface leakage. Lead with evidence-backed findings.

## Git checkpoint

Commit only after acceptance and teardown/rollback evidence exists, using intent: `m15-t11: configure a bounded qlora run`.

