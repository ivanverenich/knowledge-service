---
id: M15-T09
title: "Prepare approved training data"
milestone: M15
track: optional
depends_on: ["M15-T08"]
estimate: 90m
topics: ["ollama","vllm","qlora"]
deliverables: ["fine-tuning dataset","dataset card"]
status: planned
---

# M15-T09 — Prepare approved training data

## Outcome

Create versioned, licensed, sanitized train/validation/test examples with lineage, deduplication, leakage checks, and deletion/retraining policy.

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
2. Create versioned, licensed, sanitized train/validation/test examples with lineage, deduplication, leakage checks, and deletion/retraining policy.
3. Test success, resource limits, cancellation/retry, and failure/denial behavior at the interface; pin external artifacts and model/infrastructure versions.
4. Capture redacted reproducible evidence, actual spend where relevant, and safe teardown or rollback proof.

## Verify and accept

- Automated checks prove no evaluation overlap, secret patterns, or unapproved corpus content.
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

**Implementation prompt:** Inspect M15-T09, prerequisites, current interfaces, and cost/security constraints. Propose a bounded plan for: “Create versioned, licensed, sanitized train/validation/test examples with lineage, deduplication, leakage checks, and deletion/retraining policy.” Wait for approval; report changes, exact validation, spend, and teardown.

**Review prompt:** Review M15-T09 for state/secret exposure, least privilege, version drift, cost risk, cleanup, reproducibility, evaluation validity, and interface leakage. Lead with evidence-backed findings.

## Git checkpoint

Commit only after acceptance and teardown/rollback evidence exists, using intent: `m15-t09: prepare approved training data`.
