---
id: M01-T05
title: "Build the minimal Answer workflow"
milestone: M01
track: critical
depends_on: ["M01-T04"]
estimate: 75m
topics: ["fastapi","async","llm"]
deliverables: ["question-answer module"]
status: planned
---

# M01-T05 — Build the minimal Answer workflow

## Outcome

Create an application workflow that accepts a Question, invokes ChatModel, and returns an Answer without retrieval or conversation state.

## Why

A narrow end-to-end slice reveals integration costs before retrieval, persistence, and agents multiply them. This task must leave one narrow, observable capability for the next task instead of speculative structure.

## Read

- [Task execution guide](../../TASK-GUIDE.md)
- [Architecture](../../../architecture.md)
- [Domain language](../../../../CONTEXT.md)
- [Primary-source research](../../../research/primary-sources.md)

## Implement

1. Inspect the prerequisite task evidence and current interfaces; write down the invariant this change must preserve.
2. Create an application workflow that accepts a Question, invokes ChatModel, and returns an Answer without retrieval or conversation state.
3. Add deterministic tests at the module interface for the successful case, the most important edge case, and one typed failure.
4. Record the verification evidence and update only documentation made inaccurate by the change.

## Verify and accept

- A workflow test observes only domain contracts and proves provider objects do not leak through.
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

**Implementation prompt:** Inspect M01-T05 and its prerequisites. Explain the current seam and propose a task-scoped plan for: “Create an application workflow that accepts a Question, invokes ChatModel, and returns an Answer without retrieval or conversation state.” Wait for approval before editing; then report changed files and exact verification results.

**Review prompt:** Review M01-T05 for contract correctness, async/resource safety, security, deterministic tests, and scope. Lead with actionable findings tied to files and behavior; state explicitly if no finding remains.

## Git checkpoint

Commit only after acceptance evidence exists, with intent: `m01-t05: build the minimal answer workflow`.

