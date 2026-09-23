---
id: M04-T11
title: "Benchmark vector query plans"
milestone: M04
track: critical
depends_on: ["M04-T10"]
estimate: 90m
topics: ["embeddings","pgvector","retrieval"]
deliverables: ["vector benchmark","query-plan report"]
status: planned
---

# M04-T11 — Benchmark vector query plans

## Outcome

Create a reproducible seeded corpus and compare exact versus approximate pgvector plans, recall, latency, and index build cost.

## Why

Dense retrieval becomes trustworthy only when embedding versioning, publication, filtering, and measurement are explicit. This task must leave one narrow, observable capability for the next task instead of speculative structure.

## Read

- [Task execution guide](../../TASK-GUIDE.md)
- [Architecture](../../../architecture.md)
- [Security model](../../../security.md)
- [Domain language](../../../../CONTEXT.md)
- [Primary-source research](../../../research/primary-sources.md)

## Implement

1. Inspect prerequisite evidence and current interfaces; write the invariant and expected failing check before editing.
2. Create a reproducible seeded corpus and compare exact versus approximate pgvector plans, recall, latency, and index build cost.
3. Add deterministic tests at the module interface for success, the highest-risk edge, and one typed failure or denial.
4. Capture verification evidence and update only diagrams or decisions made inaccurate by the change.

## Verify and accept

- The report includes EXPLAIN output, data size, warmup, parameters, and a justified baseline choice.
- Run the narrow test first, then format, lint, types, and the relevant deterministic or integration suite.
- Confirm deliverables exist, authorization is preserved before data leaves storage where applicable, and telemetry contains no seeded sensitive text.
- Explain the result in your own words; green commands alone do not complete the task.

## Failure modes and progressive hints

1. Shrink a difficult-to-test interface before adding mocks or provider flags.
2. Separate ranking relevance from eligibility; a highly relevant unauthorized Chunk is still ineligible.
3. Assert observable results at the named seam and inspect SQL/query plans or captured transport only when that is the contract.

## Reflection

- What complexity does this module hide from callers?
- Which invariant would be easiest to violate during a later optimization?
- What metric or adversarial case could prove your intuition wrong?

## Agent workflow

**Implementation prompt:** Inspect M04-T11 and prerequisite evidence. Describe the existing seam and propose a scoped plan for: “Create a reproducible seeded corpus and compare exact versus approximate pgvector plans, recall, latency, and index build cost.” Wait for approval before edits; report changed files, tests, and unresolved risks.

**Review prompt:** Review M04-T11 for behavior, security/ACL ordering, async resource ownership, deterministic coverage, and accidental scope. Lead with findings tied to observable failures.

## Git checkpoint

Commit only after acceptance evidence exists, with intent: `m04-t11: benchmark vector query plans`.
