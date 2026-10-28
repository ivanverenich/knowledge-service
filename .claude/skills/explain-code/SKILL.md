---
name: explain-code
description: Explain what a piece of this project's code does and why, in plain, simple English.
disable-model-invocation: true
---

# Explain code

The learner describes some code in their own words, for example "the benchmark's run function", "how search hides private Chunks", or "what happens when a source is synchronized". They want its **sense and purpose**: what problem it solves and how it goes about solving it, in words a beginner follows on first read.

## Steps

1. **Find the code.** Match the description against `src/`, `scripts/`, `tests/`, and `migrations/`. Read the code itself, plus its callers and the lesson guide or task in `docs/` that introduced it when they explain why it exists. If the description fits several places, list them in one line each and ask which one was meant.
2. **Explain it.** Done when the learner could retell, without the code in front of them, what problem it solves and what it does, in an answer of about 150 words.

## How to explain

Keep it brief: the learner asks for more when they want it. Use this shape, with no extra sections:

- **Purpose**: one or two sentences on the problem the code solves and who relies on it.
- **How**: three to five short bullets of what happens when it runs, in order.
- **Why it matters**: one sentence, only when it makes the purpose clearer.

Write as if explaining to a smart friend who is new to programming:

- Short sentences, everyday words. Skip tables, and pick the one or two points that matter most instead of walking every field or line.
- Describe behavior ("it checks the database is empty before starting"), not mechanics. Types, recursion, design patterns, decorators, and language features stay out.
- Name an identifier, file, or other part of the project only when the learner needs it to find their way; then say in plain words what it is, for example "`_vacuum`, the clean-up that runs before and after".
- A small technical explanation is fine when the point depends on it. Give it in one or two sentences after the plain meaning, never instead of it.
- One small concrete example or analogy when it makes the idea click.

Answer in chat only; the code stays as it is. If the learner's code differs from the lesson guide, add one line naming the difference. End with a one-line offer to go deeper into one part.
