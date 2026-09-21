---
id: M02-T07
title: "Persist Chunks and provenance"
milestone: M02
track: critical
depends_on: ["M02-T06"]
estimate: 90m
topics: ["postgresql","domain-model","migrations"]
deliverables: ["chunk tables"]
status: planned
---

# M02-T07 — Persist Chunks and provenance

## Outcome

Store ordered Chunk text, structure, token counts, content fingerprint, and exact source location needed for citations.

## Why

Durable identities, versions, and transactions make later synchronization and authorization behavior explainable. This task must leave one narrow, observable capability for the next task instead of speculative structure.

## Read

- [Task execution guide](../../TASK-GUIDE.md)
- [Architecture](../../../architecture.md)
- [Domain language](../../../../CONTEXT.md)
- [Primary-source research](../../../research/primary-sources.md)

## Implement

1. Inspect the prerequisite task evidence and current interfaces; write down the invariant this change must preserve.
2. Store ordered Chunk text, structure, token counts, content fingerprint, and exact source location needed for citations.
3. Add deterministic tests at the module interface for the successful case, the most important edge case, and one typed failure.
4. Record the verification evidence and update only documentation made inaccurate by the change.

## Verify and accept

- Replacing a content version atomically removes stale searchable chunks and preserves order.
- Run the narrow test first, then format, lint, type checks, and the relevant deterministic suite.
- Confirm the listed deliverables exist, public contracts contain no provider-specific leakage, and no secret or private content entered artifacts.
- Completion requires an explanation in your own words; passing commands alone is insufficient.

## Failure modes and progressive hints

1. If the behavior is difficult to test, reduce the interface before adding mocks.
2. If an integration fails, identify whether the owner is configuration, transport, application workflow, or domain policy.
3. Re-read the named invariant and assert at that seam; avoid inspecting private state when an observable outcome exists.

## Reflection

- What complexity did this task hide from its caller?
- Which alternative would make the next change harder, and why?
- What production failure would escape if your negative test were removed?

## Agent workflow

**Implementation prompt:** Inspect M02-T07 and its prerequisites. Explain the current seam and propose a task-scoped plan for: “Store ordered Chunk text, structure, token counts, content fingerprint, and exact source location needed for citations.” Wait for approval before editing; then report changed files and exact verification results.

**Review prompt:** Review M02-T07 for contract correctness, async/resource safety, security, deterministic tests, and scope. Lead with actionable findings tied to files and behavior; state explicitly if no finding remains.

## Git checkpoint

Commit only after acceptance evidence exists, with intent: `m02-t07: persist chunks and provenance`.

