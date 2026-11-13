# M06-T04 — Structured Answer evidence

## Invariant

`generate_answer` fits the Question and Evidence into the budget, calls
the `AnswerModel` once with exactly the prompt it counted, and returns
one outcome. `ANSWERED` holds visible text and at least one Citation,
each built on the server from Evidence that was in the prompt.
`ABSTAINED` holds a visible reason and no Citations. `REFUSED` and
`INVALID` hold no model text; `INVALID` names its defect. Every billed
reply keeps its usage. With no Evidence that fits, the model is not
called. Provider failures raise `ChatModelError` subclasses with no
request text; anything else the model raises propagates. No log record,
repr, or error holds Question, Document, or answer text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.generation"`
fails with `ModuleNotFoundError`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What outcome a reply gets that answers correctly but cites an Evidence the budget dropped: `INVALID` with the defect `INVALID_CITATION`, because citations are checked against the Evidence that was in the prompt, and a dropped Evidence was never shown to the model, so a citation to it cannot be backed by what the model read.
- How many tests fail if citations are checked against all retrieved Evidence instead of the Evidence in the prompt: exactly one, `test_evidence_dropped_by_the_budget_cannot_be_cited`; every other test sends Evidence that all fits, so the two sets are the same and the break is invisible to them.
- Which outcome a reply gets whose answer is only zero-width spaces, and why that matters to a user: `INVALID` with the defect `EMPTY_ANSWER`, because the text is not empty to Python but shows nothing on screen; without the check the user would see a blank answer that still carries citations and looks supported.

## Design

- **Changed files.** New `src/knowledge_service/answering/generation.py`, `src/knowledge_service/answering/openai_answering.py`, `tests/test_generation.py`, and `tests/test_openai_answering.py`. Small edits: `ChatModelRequestRefused` in `model.py`, mapped to `FailureKind.INTERNAL` in `errors.py` with a case in `tests/test_errors.py`, and the public `has_visible_text` in `answering/prompt.py`. The `answering` row of `docs/architecture.md` names `generate_answer` and `OpenAIAnswerModel`.
- **A new port.** `AnswerModel` has an `identity` and one `async generate(prompt, *, request_id, timeout_seconds)` that returns a `ModelReply`: the model's text, unchecked, whether it refused, and the billed `Usage`. It raises the M01 `ChatModelError` family, so the M01 `ChatModel` and its endpoint stay unchanged.
- **What the adapter sends.** `OpenAIAnswerModel` sends the counted prompt's instructions and input unchanged to the Responses API, with a strict `json_schema` format named `answer` whose citations allow only this request's Evidence IDs. It pins the model, sets `store=False`, turns off the SDK's retries, passes the deadline and `X-Request-ID`, and maps each SDK failure to one port error raised outside the `except` block.
- **The rules of `checked_answer`.** One JSON object of at most 40,000 characters, with no repeated keys and exactly the fields `answer`, `citations`, `abstained`, and `reason` of the right types; valid Unicode; an answer of at most 16,000 and a reason of at most 1,000 characters. An answer has visible text and at least one citation; an abstention has a visible reason and no citations. Citations go through `resolve_citations` against the Evidence that was in the prompt. A broken rule raises `InvalidAnswer` with one of eight `AnswerDefect` names, never the model's words.
- **Five outcomes.** `ANSWERED`, `ABSTAINED`, `REFUSED`, `INVALID` with its defect, and `NO_EVIDENCE`, which is returned without calling the model. Every billed reply keeps its usage, and every result carries the `ContextReport`.
- **Settings and budget.** `AnswerSettings` reads `ANSWER_*`: the model `gpt-5.4-mini-2026-03-17`, reasoning effort `none`, a 30 s timeout, 1,000 output tokens, a 32,000-token window with 4,000 for History, and prices of 0.75 and 4.50 USD per million tokens. Its `budget` is `ContextBudget(32000, 1000, 4000)`, and a validator refuses reserves that fill the window.

## Verification

| Command | Result |
|---|---|
| `uv run pytest tests/test_generation.py tests/test_openai_answering.py -q` | 80 passed, 1 deselected |
| `uv run pytest tests/test_errors.py tests/test_prompt.py -q` | 60 passed |
| `make format-check` | 237 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors |
| `make test-unit` | 1888 passed |
| `make test-integration` | 329 passed |
| `make docs-check` | passed |

- **Expected failing check.** Before Step 3, importing `knowledge_service.answering.generation` failed with `ModuleNotFoundError`.
- **Deliberate break.** Checking citations against all retrieved Evidence (`checked_answer(reply.text, evidence)`) failed exactly 1 test, `test_evidence_dropped_by_the_budget_cannot_be_cited`; 79 passed. Undone; 80 passed.
- **Live run.** Not made: no `OPENAI_API_KEY` was available.
- **Known limits.** It is unproven that OpenAI's strict mode accepts the schema with its `enum` of Evidence IDs. A model that ignores cancellation can outlive the deadline. Claims are not yet checked against their cited Evidence (M06-T06).

## Review

An adversarial review could not make a Citation point outside the prompt, or crash the check, in over 6,000 fuzzed replies. It found four broken guarantees, each fixed with a test that failed before the fix: invisible answers and reasons were accepted; a huge whole-number timeout (`10**400`) crashed with `OverflowError`; the model's own `TimeoutError` was disguised as ours and kept the prompt text attached; a hand-built usage or identity could put text into the log.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **Its own port.** Answering has a new `AnswerModel` port instead of a changed M01 `ChatModel`. The M01 port takes a bare Question and still serves the HTTP endpoint without retrieval; changing its shape would break that endpoint and its tests for no gain. The new port raises the same `ChatModelError` family, so the HTTP layer maps its failures with the rules it already has.
- **A broken reply is a result; a timeout is an error.** A refusal or a reply that breaks a rule was billed, so it comes back as `REFUSED` or `INVALID` with its usage and a named defect, and the cost is never lost. A timeout, an outage, a rate limit, or a refused request bills nothing and leaves nothing to show, so it raises the port error that tells the caller whether to retry.
- **No Evidence, no call.** When nothing fits the budget, the model could only guess or abstain. Not calling it costs nothing, cannot invent an answer, and gives the same `NO_EVIDENCE` outcome every time.
- **Left for later.** Checking that each claim is supported by its cited Evidence is M06-T06. The abstention policy and the messages a user sees are M06-T07. Streaming is M06-T08 and M06-T09. A model that ignores cancellation can outlive the deadline; that is a known limit, recorded here.
- **Not run live.** No `OPENAI_API_KEY` was available, so the live test did not run, and it is unproven that OpenAI's strict mode accepts the schema with its `enum` of Evidence IDs. If it refuses, the adapter raises `ChatModelRequestRefused`, and the fix is to drop the `enum` and rely on `checked_answer`, which checks citations anyway.
- **Falsified assumption.** I assumed any `TimeoutError` during the call meant our deadline had expired. The review shows that a `TimeoutError` raised by the model itself was reported as `ChatModelTimeout`, hiding a bug and carrying prompt text in its attached context. So catching every `TimeoutError` is wrong: only the expiry of our own deadline becomes `ChatModelTimeout`, raised outside the `except` block, and anything else propagates.
- **Second falsified assumption.** I assumed a non-empty string is a visible answer. The review shows an answer of only zero-width spaces passed the first check. So "non-empty" is wrong: answers and reasons must pass `has_visible_text`, the same rule as the Question.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. **What `generate_answer` hides from M06-T09.** The streaming code never has to count tokens, choose which Evidence fits, decide whether to call the model, parse JSON, check field types, visible text, sizes, or Unicode, check that citations point at Evidence in the prompt, build Citations with server provenance, keep usage for broken replies, or tell our deadline from the model's own timeout. It gets one outcome or one port error.
2. **Checking against all retrieved Evidence.** A user would see a source for text the model never read: the model could guess an Evidence ID that the budget dropped, and the Citation would look real. `test_evidence_dropped_by_the_budget_cannot_be_cited` catches it; the deliberate break showed it is the only test that does.
3. **`INVALID` with cost against a raised timeout.** A broken reply was billed and is a normal, if bad, outcome: the caller shows the user a safe message and the cost report counts it. A timeout bills nothing and leaves nothing to show; the error tells the HTTP layer and a retry policy what happened and whether to try again.
4. **"I cannot answer" with a citation.** Exactly right. The two halves contradict each other, so the server cannot tell which one the model meant; showing either would be a guess. Rejecting it as `CITED_ABSTENTION` keeps the rule simple, and M06-T07 decides what the user sees.
5. **The next attack.** A citation that looks like a dropped ID with a changed case, extra spaces, or a lookalike Unicode letter, or the same valid ID twice, to see whether `resolve_citations` normalizes anything it should not.
