---
id: M08-T12
title: "Paginate and version Confluence content"
milestone: M08
track: critical
depends_on: ["M08-T11"]
estimate: 90m
topics: ["redis","dramatiq","confluence"]
deliverables: ["Confluence Source adapter"]
status: planned
---

# M08-T12 — Paginate and version Confluence content

## Outcome

Fetch pages/blog posts through explicit pagination, stable content IDs, versions, update times, and continuation tokens.

## Why

Remote synchronization needs durable outcomes, idempotency, and source-specific translation behind the same Source interface. Complete one observable capability and preserve a clean interface for later adapters.

## Read

- [Task execution guide](../../TASK-GUIDE.md)
- [Architecture](../../../architecture.md)
- [Security model](../../../security.md)
- [Evaluation strategy](../../../evaluation.md)
- [Operations model](../../../operations.md)
- [Primary-source research](../../../research/primary-sources.md)

## Implement

1. Inspect prerequisite evidence and state the invariant, expected failure, and owner of retries/authorization before editing.
2. Fetch pages/blog posts through explicit pagination, stable content IDs, versions, update times, and continuation tokens.
3. Test the module interface with success, boundary, and typed failure or denial cases; use real infrastructure when its semantics are under test.
4. Capture reproducible evidence, cost/latency metadata where relevant, and update only affected documentation.

## Verify and accept

- Recorded fixtures detect missed/duplicated pages and API-shape drift.
- Run the narrow check, then formatting, lint, types, deterministic tests, and the relevant integration/contract/evaluation slice.
- Confirm artifacts are versioned and redacted, external calls are bounded, and replay or cancellation behavior is explicit.
- Explain what would falsify the chosen design.

## Failure modes and progressive hints

1. Reproduce with the smallest fixture and one request/job before increasing concurrency.
2. Identify which system owns durable truth; Redis messages and provider responses are not business state.
3. Inspect captured protocol/SQL/trace evidence at the seam, then repair the invariant rather than adding an outer retry.

## Reflection

- Which failure can occur after the caller believes this work succeeded?
- How does the design behave under replay, cancellation, or authorization change?
- Which metric or contract test protects future refactoring?

## Agent workflow

**Implementation prompt:** Inspect M08-T12, its dependencies, and current evidence. Propose a bounded plan for: “Fetch pages/blog posts through explicit pagination, stable content IDs, versions, update times, and continuation tokens.” Wait for approval, then make only task-scoped changes and report exact checks and external spend.

**Review prompt:** Review M08-T12 for contract drift, authorization, idempotency, async cleanup, observability, test determinism, and hidden paid/network dependency. Lead with reproducible findings.

## Git checkpoint

Commit only with complete evidence, using intent: `m08-t12: paginate and version confluence content`.

