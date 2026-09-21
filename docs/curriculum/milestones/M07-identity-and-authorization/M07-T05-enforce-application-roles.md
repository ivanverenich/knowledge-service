---
id: M07-T05
title: "Enforce Application Roles"
milestone: M07
track: critical
depends_on: ["M07-T04"]
estimate: 75m
topics: ["oidc","authorization","security"]
deliverables: ["role policy"]
status: planned
---

# M07-T05 — Enforce Application Roles

## Outcome

Define User, content manager, evaluator, and administrator permissions independently from Document Access Grants.

## Why

Permission-aware RAG is a security system: identity, source grants, storage predicates, telemetry, and adversarial evidence must agree. This task must leave one narrow, observable capability for the next task instead of speculative structure.

## Read

- [Task execution guide](../../TASK-GUIDE.md)
- [Architecture](../../../architecture.md)
- [Security model](../../../security.md)
- [Domain language](../../../../CONTEXT.md)
- [Primary-source research](../../../research/primary-sources.md)

## Implement

1. Inspect prerequisite evidence and current interfaces; write the invariant and expected failing check before editing.
2. Define User, content manager, evaluator, and administrator permissions independently from Document Access Grants.
3. Add deterministic tests at the module interface for success, the highest-risk edge, and one typed failure or denial.
4. Capture verification evidence and update only diagrams or decisions made inaccurate by the change.

## Verify and accept

- A permission matrix test covers every protected operation and default denial.
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

**Implementation prompt:** Inspect M07-T05 and prerequisite evidence. Describe the existing seam and propose a scoped plan for: “Define User, content manager, evaluator, and administrator permissions independently from Document Access Grants.” Wait for approval before edits; report changed files, tests, and unresolved risks.

**Review prompt:** Review M07-T05 for behavior, security/ACL ordering, async resource ownership, deterministic coverage, and accidental scope. Lead with findings tied to observable failures.

## Git checkpoint

Commit only after acceptance evidence exists, with intent: `m07-t05: enforce application roles`.

