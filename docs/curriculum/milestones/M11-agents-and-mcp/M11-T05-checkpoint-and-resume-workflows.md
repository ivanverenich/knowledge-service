---
id: M11-T05
title: "Checkpoint and resume workflows"
milestone: M11
track: optional
depends_on: ["M11-T04"]
estimate: 90m
topics: ["langgraph","mcp","multi-agent"]
deliverables: ["workflow checkpoint adapter"]
status: planned
---

# M11-T05 — Checkpoint and resume workflows

## Outcome

Persist workflow identity and safe state references without duplicating raw content or trusting stale authorization.

## Why

Agentic behavior earns a place only when bounded workflows improve a measured outcome through existing authorized tools. The task crosses existing interfaces and must not move core policy into a framework or deployment adapter.

## Read

- [Task execution guide](../../TASK-GUIDE.md)
- [Architecture](../../../architecture.md)
- [Security model](../../../security.md)
- [Evaluation strategy](../../../evaluation.md)
- [Operations model](../../../operations.md)
- [Primary-source research](../../../research/primary-sources.md)

## Implement

1. Inspect prerequisites and state the interface, resource/security budget, and expected red test or failed drill.
2. Persist workflow identity and safe state references without duplicating raw content or trusting stale authorization.
3. Cover success, boundary, cancellation/replay, and denial/failure behavior at the interface; use real infrastructure only when its semantics matter.
4. Capture reproducible, redacted evidence and update diagrams/runbooks changed by the result.

## Verify and accept

- Restart and permission-change tests prove resumed tools reauthorize current access.
- Run narrow checks before the relevant full suite, security/evaluation gate, or deployment drill.
- Confirm the change preserves Authorization Context, bounded resources, immutable artifacts, and typed terminal outcomes where applicable.
- Record a keep/reject decision when the task is an experiment.

## Failure modes and progressive hints

1. Distinguish framework state or infrastructure signals from durable application truth.
2. Trace identity, budget, cancellation, and correlation IDs across every adapter in the path.
3. Reproduce with a deterministic fake before debugging a remote system; then prove the real seam with a contract or drill.

## Reflection

- What new failure mode did this adapter/framework introduce?
- Which existing interface prevented the new implementation from spreading?
- What measured result would justify deleting or disabling this work?

## Agent workflow

**Implementation prompt:** Inspect M11-T05, prerequisites, and current interfaces. Propose a bounded plan for: “Persist workflow identity and safe state references without duplicating raw content or trusting stale authorization.” Wait for approval; then report changes, tests/drills, spend, and residual risk.

**Review prompt:** Review M11-T05 for interface leakage, identity/budget propagation, failure recovery, reproducibility, supply-chain risk, and misleading success claims. Lead with evidence-backed findings.

## Git checkpoint

Commit only after acceptance evidence exists, with intent: `m11-t05: checkpoint and resume workflows`.

