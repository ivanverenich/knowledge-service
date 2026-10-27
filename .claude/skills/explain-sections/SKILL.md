---
name: explain-sections
description: Explain named sections of a lesson guide in plain, simple English.
disable-model-invocation: true
---

# Explain sections

The learner names one or more sections of a lesson guide in `docs/lessons/` (for example "what you will do", "before you begin", "step 4") and wants the gist of each: what it is about and why, in words a beginner follows on first read.

## Steps

1. **Find the guide.** Use the lesson the learner names. Otherwise use the lesson in progress: the "Next unblocked task" in `docs/curriculum/PROGRESS.md`, whose guide is `docs/lessons/<task-id>-*.md`. If the guide does not exist, say so and stop.
2. **Find each section.** Match the names against the guide's `##` and `### Step N` headings, ignoring case and small wording differences. Headings inside fenced blocks, such as the evidence template in Step 1, are not sections. Read the whole section, and the source task in `docs/curriculum/` when the section leans on it. If a name matches nothing, list the guide's sections and ask which one was meant.
3. **Explain each section, in the guide's order, under its own short heading.** Done when every named section has its explanation.

## How to explain

Give the **gist**: the problem the section is about, what it asks for or decides, and why that matters for this project. Write as if explaining to a smart friend who is new to the topic:

- Short sentences, everyday words. Three to six sentences per section, or a few short bullets when the section lists separate things.
- Keep identifiers, code, SQL, library names, and numbers out unless the point depends on one; then name it once and say what it is in plain words.
- One small concrete example or analogy when it makes the idea click, such as a recipe card for an index configuration.
- For a step, say what the learner will have once it is done.
- For a section that lists review findings or limits, explain the idea behind them in one or two sentences instead of walking every item.

Answer in chat only; the guide stays as it is. End by offering to go deeper into any one section.
