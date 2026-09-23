---
id: M04-T12
title: "Pass the dense-retrieval understanding gate"
milestone: M04
track: critical
depends_on: ["M04-T11"]
estimate: 90m
topics: ["embeddings","pgvector","retrieval"]
deliverables: ["M04 gate evidence"]
status: planned
---

# M04-T12 — Pass the dense-retrieval understanding gate

## Outcome

Explain embedding/index versioning, redraw publication and search, diagnose a dimension mismatch, and add one filter without a recipe.

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
2. Explain embedding/index versioning, redraw publication and search, diagnose a dimension mismatch, and add one filter without a recipe.
3. Add deterministic tests at the module interface for success, the highest-risk edge, and one typed failure or denial.
4. Capture verification evidence and update only diagrams or decisions made inaccurate by the change.

## Verify and accept

- Evidence proves the filter is enforced in storage and preserves ranking semantics.
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

**Implementation prompt:** Inspect M04-T12 and prerequisite evidence. Describe the existing seam and propose a scoped plan for: “Explain embedding/index versioning, redraw publication and search, diagnose a dimension mismatch, and add one filter without a recipe.” Wait for approval before edits; report changed files, tests, and unresolved risks.

**Review prompt:** Review M04-T12 for behavior, security/ACL ordering, async resource ownership, deterministic coverage, and accidental scope. Lead with findings tied to observable failures.

## Git checkpoint

Commit only after acceptance evidence exists, with intent: `m04-t12: pass the dense retrieval gate`.
