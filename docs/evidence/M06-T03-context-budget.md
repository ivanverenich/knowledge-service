# M06-T03 — Context budget evidence

## Invariant

`allocate_context` returns a prompt whose counted tokens, plus the Answer
and History reserves, never exceed the context window, and the prompt
returned is the one that was counted. The policy, schema, and Question are
never trimmed. Evidence is kept whole, in retrieval's order, as the longest
head that fits; the rest is dropped and appears in neither the prompt, the
schema, nor the returned set. The report names every kept and dropped
Evidence ID with token counts and holds no Question or Document text. When
even a prompt without Evidence does not fit, `ContextOverBudget` is raised
with counts only.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.context_budget"`
fails with `ModuleNotFoundError`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- How many of 20 Chunks that repeat the word "days" 256 times fit in a 16,000-token window with 1,000 for the Answer and 2,000 for History: 13,000 tokens are left for the prompt; the policy, schema, and Question take about 1,300, and each Chunk costs about 1,440 bytes once its title, heading, and JSON framing are added, so 8 fit and 12 are dropped.
- The same for the Ukrainian word "днів", also four letters, and why the number differs: each Chunk costs about 2,470 bytes, so 4 fit, half as many, because each Cyrillic letter is two bytes in UTF-8 while each Latin letter is one, and the budget counts bytes, not letters or real tokens.
- How many tests the deliberate break in Step 4 fails, and which ones: forgetting the History reserve lets the prompt grow into History's room, so every test that checks the exact budget fails: both cases of `test_the_budget_boundary_is_exact`, the property test `test_the_budget_is_never_exceeded_and_the_kept_head_is_the_longest`, and `test_a_question_too_long_for_the_window_is_denied_with_counts_only`, 4 in all; tests with a generous budget still pass.

## Design

- **Changed files.** New `src/knowledge_service/answering/context_budget.py` and `tests/test_context_budget.py`; the `answering` row of `docs/architecture.md` names `allocate_context`.
- **Five parts, by priority.** The policy and schema, the Answer reserve, and the Question are never trimmed. The History reserve is a fixed room, empty until M06-T11. Evidence gets the rest and is trimmed first.
- **Counting.** `count_prompt_tokens` is the UTF-8 bytes of the instructions, the input, and the schema, plus 64 framing tokens, the M05-T12 allowance. A token is at least one byte, so the count is never too low. The prompt that is counted is the prompt that is returned.
- **Trimming.** Evidence is kept whole, as the longest head of retrieval's order that fits; nothing below a dropped item is kept. Kept Evidence is the same objects in a new `EvidenceSet`, so IDs and provenance are unchanged, and dropped IDs are in neither the input nor the schema.
- **Bounded work.** Lengths are added first, a lower bound that copies nothing, and Evidence beyond what might fit is never read. The whole head that might fit is rendered first; if it is too big, the search halves.
- **Report and failures.** `ContextReport` holds the window, both reserves, the tokens without Evidence, what Evidence added, and the kept and dropped IDs. `ContextOverBudget` holds `needed` and `available` only; `InvalidContextBudget` refuses bad numbers; bad Unicode in Evidence that might fit raises `InvalidPrompt`.

## Verification

| Command | Result |
|---|---|
| `uv run pytest tests/test_context_budget.py -q` | 18 passed |
| `make format-check` | 233 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors |
| `make test-unit` | 1807 passed |
| `make test-integration` | 329 passed |
| `make docs-check` | passed |

- **Deliberate break.** Forgetting the History reserve in `ContextBudget.prompt_tokens` failed 4 tests: both cases of `test_the_budget_boundary_is_exact`, the property test, and the `ContextOverBudget` test. Undone; 18 passed.
- **Size run.** In a 16,000-token window with 1,000 for the Answer and 2,000 for History, 20 Chunks of "days" repeated 256 times: 8 kept, 12 dropped, 12,856 tokens. The same with "днів": 4 kept, 16 dropped, 11,196 tokens. The prompt without Evidence is 1,335 tokens.
- **Timing.** 20 realistic Chunks take 34 to 72 ms to allocate, in English or Ukrainian, at 16,000 or 128,000 tokens.
- **Known limits.** The byte count overestimates, and twice as much for Cyrillic letters. History is reserved but empty until M06-T11. Escaping runs one character at a time, so budgets in the millions take seconds. `count_prompt_tokens` given something that is not a prompt raises Python's own `AttributeError`.

## Review

An adversarial review ran a 3,000-case fuzz with hand-built titles and headings, hidden and escape-heavy characters, and a planted marker, plus targeted attacks. It could not exceed a budget, keep the wrong Evidence, leak a dropped ID, or leak the marker, and every hostile type raised a typed error. It found one broken guarantee: every Evidence text was encoded in full to measure it, so time and memory grew with the Evidence, not the budget (1.56 s and 150 MB for 20 texts of 50 million characters with a budget that held none). Lengths are now added instead; the same case takes 0 s and 0 MB. Three regression tests fail without the fix, and the Unicode check on Evidence that is never rendered has its own test that fails without it.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **The order: policy, Answer, Question, History, then Evidence.** Without the policy and schema the model has no rules; without room for the Answer it is cut off mid-sentence; a cut Question is a different question. History is a fixed room, so it cannot grow into Evidence's place. If History were trimmed after Evidence, a long conversation would leave the model remembering the chat but holding no Documents to answer from. Evidence is the only part with a natural order to trim by: retrieval already ranked it.
- **Evidence is dropped whole, never cut.** A cut text would keep its Evidence ID while the model read only part of it, so a Citation would point at text the model never saw. Keeping only the head of retrieval's order, with no holes, makes the rule easy to explain: the most relevant N that fit.
- **What the byte count costs Ukrainian users.** The run above kept 8 English Chunks and 4 Ukrainian ones of the same four-letter word: each Cyrillic letter is two bytes, so Ukrainian Questions get half the Evidence under the same budget. The count is never too low, which is what the budget needs, but it wastes room. A real tokenizer for the chosen model would count closer to what is billed; it belongs in the ChatModel port of M06-T04, behind the same `count_prompt_tokens` seam.
- **Left for later lessons.** Sending the prompt and choosing the model's window and reserves from settings: M06-T04. Not calling the model at all when no Evidence fits, and telling "nothing found" from "found, but nothing fit": M06-T04 and M06-T07. Filling the History reserve: M06-T11.
- **Falsified assumption.** I assumed that measuring an Evidence by its encoded UTF-8 size was cheap enough to do for every item. The adversarial review showed that encoding copies the whole text, so 20 texts of 50 million characters cost 1.56 s and 150 MB with a budget that could hold none of them. So "cost grows with the budget" was wrong for that version; adding up lengths, a lower bound that copies nothing, made it true.

## Reflection

Drafted by Claude at the learner's request, from the lesson's discussion; the learner should rewrite it in their own words. Question 4 follows the learner's own point that a request with no Evidence left has no sense being sent.

1. **What `allocate_context` hides from M06-T04.** How tokens are counted and how much the policy, schema, and Question cost; which Evidence fits and how it is found without rendering too much; that dropped Evidence must leave the schema too; and that the prompt to send must be exactly the one counted. M06-T04 gets a ready prompt, the Evidence a Citation may use, and a report to log.
2. **The invariant easiest to break in an optimization.** "The prompt returned is the one counted." Counting each Evidence once and adding the numbers would skip the JSON commas, indentation, numbers, and schema entries, so the sum could be lower than the real prompt and overflow the window. `test_the_budget_boundary_is_exact` and the property test would catch it, because both compare against a real rendered prompt.
3. **Who pays for the byte count, and how to check it.** Ukrainian users: under the same budget they get half the Evidence of English users, 4 Chunks against 8. To measure the gap, count the same prompts with the model's own tokenizer, such as `tiktoken`, or read the input tokens the provider bills, and compare per language.
4. **No Evidence fits.** Sending the prompt only to have the model abstain wastes a paid call. The caller should not call the model when `kept` is empty and return an Abstention directly. "Nothing found" is a normal Answer; "found, but nothing fit" means the budget or Chunks are wrong and should be logged as a warning. Refusing like an oversized Question would be wrong: the user's Question was fine.
5. **What could still exceed the budget.** A model whose real tokenizer counts more tokens than UTF-8 bytes for some text would break the bound; no OpenAI tokenizer does, but another provider's port must prove it. And M06-T04's request framing may grow beyond 64 tokens once the schema and roles are sent; the billed input tokens of a real call should be compared with the count.
