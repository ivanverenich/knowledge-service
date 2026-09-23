---
id: M14-T13
title: "Compare ECS and Kubernetes"
milestone: M14
track: optional
depends_on: ["M14-T12"]
estimate: 75m
topics: ["kubernetes","helm","eks"]
deliverables: ["ECS versus Kubernetes report"]
status: planned
---

# M14-T13 — Compare ECS and Kubernetes

## Outcome

Evaluate operational surface, portability, scaling, deployment, observability, cost, and team skill assumptions using measured exercises.

## Why

Kubernetes is learned as a second deployment adapter, preserving application interfaces while exposing scheduling and cluster failure modes. Keep the implementation reproducible, bounded, and replaceable through the existing seam.

## Read

- [Task execution guide](../../TASK-GUIDE.md)
- [Architecture](../../../architecture.md)
- [Security model](../../../security.md)
- [Evaluation strategy](../../../evaluation.md)
- [Operations model](../../../operations.md)
- [Primary-source research](../../../research/primary-sources.md)

## Implement

1. Inspect prerequisite evidence and record the interface, cost ceiling, security constraints, and expected failed check or drill.
2. Evaluate operational surface, portability, scaling, deployment, observability, cost, and team skill assumptions using measured exercises.
3. Test success, resource limits, cancellation/retry, and failure/denial behavior at the interface; pin external artifacts and model/infrastructure versions.
4. Capture redacted reproducible evidence, actual spend where relevant, and safe teardown or rollback proof.

## Verify and accept

- An ADR-style decision states when each deployment adapter is justified.
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

**Implementation prompt:** Inspect M14-T13, prerequisites, current interfaces, and cost/security constraints. Propose a bounded plan for: “Evaluate operational surface, portability, scaling, deployment, observability, cost, and team skill assumptions using measured exercises.” Wait for approval; report changes, exact validation, spend, and teardown.

**Review prompt:** Review M14-T13 for state/secret exposure, least privilege, version drift, cost risk, cleanup, reproducibility, evaluation validity, and interface leakage. Lead with evidence-backed findings.

## Git checkpoint

Commit only after acceptance and teardown/rollback evidence exists, using intent: `m14-t13: compare ecs and kubernetes`.
