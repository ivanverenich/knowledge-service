---
id: M13-T10
title: "Deploy the private API service"
milestone: M13
track: critical
depends_on: ["M13-T09"]
estimate: 90m
topics: ["aws","terraform","ecs"]
deliverables: ["API ECS service"]
status: planned
---

# M13-T10 — Deploy the private API service

## Outcome

Define task/service, private networking, load-balancer target, health checks, autoscaling inputs, secrets injection, and graceful deployment settings.

## Why

A complete ephemeral AWS path teaches infrastructure ownership, security, cost, deployment, and recovery more honestly than disconnected cloud examples. Keep the implementation reproducible, bounded, and replaceable through the existing seam.

## Read

- [Task execution guide](../../TASK-GUIDE.md)
- [Architecture](../../../architecture.md)
- [Security model](../../../security.md)
- [Evaluation strategy](../../../evaluation.md)
- [Operations model](../../../operations.md)
- [Primary-source research](../../../research/primary-sources.md)

## Implement

1. Inspect prerequisite evidence and record the interface, cost ceiling, security constraints, and expected failed check or drill.
2. Define task/service, private networking, load-balancer target, health checks, autoscaling inputs, secrets injection, and graceful deployment settings.
3. Test success, resource limits, cancellation/retry, and failure/denial behavior at the interface; pin external artifacts and model/infrastructure versions.
4. Capture redacted reproducible evidence, actual spend where relevant, and safe teardown or rollback proof.

## Verify and accept

- Deployment reaches readiness, scales in a controlled test, and survives one task replacement.
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

**Implementation prompt:** Inspect M13-T10, prerequisites, current interfaces, and cost/security constraints. Propose a bounded plan for: “Define task/service, private networking, load-balancer target, health checks, autoscaling inputs, secrets injection, and graceful deployment settings.” Wait for approval; report changes, exact validation, spend, and teardown.

**Review prompt:** Review M13-T10 for state/secret exposure, least privilege, version drift, cost risk, cleanup, reproducibility, evaluation validity, and interface leakage. Lead with evidence-backed findings.

## Git checkpoint

Commit only after acceptance and teardown/rollback evidence exists, using intent: `m13-t10: deploy the private api service`.

