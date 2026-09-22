---
id: M00-T06
title: "Establish the pytest loop"
milestone: M00
track: critical
depends_on: ["M00-T05"]
estimate: 60m
topics: ["python","tooling","testing"]
deliverables: ["pytest configuration","first test"]
status: completed
---

# M00-T06 — Establish the pytest loop

## Outcome

Configure pytest, asyncio behavior, markers, timeouts, coverage reporting, and a first deterministic test.

## Why

A reproducible engineering loop prevents every later experiment from being confused with environment drift. This task must leave one narrow, observable capability for the next task instead of speculative structure.

## Read

- [Task execution guide](../../TASK-GUIDE.md)
- [Architecture](../../../architecture.md)
- [Domain language](../../../../CONTEXT.md)
- [Primary-source research](../../../research/primary-sources.md)

## Implement

1. Inspect the prerequisite task evidence and current interfaces; write down the invariant this change must preserve.
2. Configure pytest, asyncio behavior, markers, timeouts, coverage reporting, and a first deterministic test.
3. Add deterministic tests at the module interface for the successful case, the most important edge case, and one typed failure.
4. Record the verification evidence and update only documentation made inaccurate by the change.

## Verify and accept

- A deliberately failing test goes red, then the corrected suite passes with the expected markers.
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

**Implementation prompt:** Inspect M00-T06 and its prerequisites. Explain the current seam and propose a task-scoped plan for: “Configure pytest, asyncio behavior, markers, timeouts, coverage reporting, and a first deterministic test.” Wait for approval before editing; then report changed files and exact verification results.

**Review prompt:** Review M00-T06 for contract correctness, async/resource safety, security, deterministic tests, and scope. Lead with actionable findings tied to files and behavior; state explicitly if no finding remains.

## Git checkpoint

Commit only after acceptance evidence exists, with intent: `m00-t06: establish the pytest loop`.

