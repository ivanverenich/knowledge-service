---
id: M05-T03
title: "Route language-aware lexical queries"
milestone: M05
track: critical
depends_on: ["M05-T02"]
estimate: 75m
topics: ["hybrid-search","ukrainian","reranking"]
deliverables: ["language-aware lexical search"]
status: planned
---

# M05-T03 — Route language-aware lexical queries

## Outcome

Select the correct lexical configuration for English, Ukrainian, mixed, and unknown Questions without trusting a single detector blindly.

## Why

English and Ukrainian retrieval claims require separate lexical, semantic, fusion, and reranking evidence rather than one aggregate score. This task must leave one narrow, observable capability for the next task instead of speculative structure.

## Read

- [Task execution guide](../../TASK-GUIDE.md)
- [Architecture](../../../architecture.md)
- [Security model](../../../security.md)
- [Domain language](../../../../CONTEXT.md)
- [Primary-source research](../../../research/primary-sources.md)

## Implement

1. Inspect prerequisite evidence and current interfaces; write the invariant and expected failing check before editing.
2. Select the correct lexical configuration for English, Ukrainian, mixed, and unknown Questions without trusting a single detector blindly.
3. Add deterministic tests at the module interface for success, the highest-risk edge, and one typed failure or denial.
4. Capture verification evidence and update only diagrams or decisions made inaccurate by the change.

## Verify and accept

- Tests cover explicit language, detected fallback, mixed text, and identifiers.
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

**Implementation prompt:** Inspect M05-T03 and prerequisite evidence. Describe the existing seam and propose a scoped plan for: “Select the correct lexical configuration for English, Ukrainian, mixed, and unknown Questions without trusting a single detector blindly.” Wait for approval before edits; report changed files, tests, and unresolved risks.

**Review prompt:** Review M05-T03 for behavior, security/ACL ordering, async resource ownership, deterministic coverage, and accidental scope. Lead with findings tied to observable failures.

## Git checkpoint

Commit only after acceptance evidence exists, with intent: `m05-t03: route language aware lexical queries`.
