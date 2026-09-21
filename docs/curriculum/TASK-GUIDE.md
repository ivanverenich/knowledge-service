# Task execution guide

## Human-first loop

1. Read the task's Outcome, Why, prerequisites, and focused references.
2. Predict the failure you expect before changing code; write it in the work log.
3. Make the smallest change that can produce the requested evidence.
4. Run the task's verification and inspect failures rather than immediately delegating them.
5. Complete the reflection in your own words.
6. Use the review prompt after you can explain the result.
7. Create the stated Git checkpoint only when acceptance criteria are satisfied.

## Agent-assisted implementation

Use the task's implementation prompt to ask an agent to inspect current state and propose a task-scoped plan. Compare its plan with the task before authorizing edits. Keep the agent inside the named deliverables and ask it to report every test it ran.

## Agent review

Use the review prompt after implementation. Require findings first, ordered by severity and tied to observable behavior. A clean review still needs the task's own verification evidence.

## Evidence convention

Store short command outputs, benchmark summaries, screenshots, or written explanations under an implementation-owned `artifacts/` convention established in M00. Never commit secrets, private documents, raw production content, or unredacted provider payloads.

## When blocked

Classify the blocker as missing prerequisite, misunderstood interface, environmental failure, incorrect implementation, or external dependency. Reproduce it with the narrowest command. Use progressive hints in order and record which assumption was wrong. Change scope only through an explicit decision record or roadmap update.

## Completion rule

A task is complete when every acceptance criterion has observable evidence, relevant tests pass, the reflection is answered, and the repository is left in a state from which the next dependent task can begin.
