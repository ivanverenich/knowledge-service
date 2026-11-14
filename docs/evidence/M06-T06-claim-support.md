# M06-T06 — Claim support evidence

## Invariant

`check_support` accepts only an `ANSWERED` result with visible text and
the Evidence its Citations name, and a positive timeout; otherwise it
raises `InvalidSupportRequest` before the judge is called. It splits the
answer into claims by position, sends the judge the claim texts and only
the cited Evidence, and never changes the `AnswerResult`. A claim is
`SUPPORTED` only when the judge names a cited Evidence and a quote that
holds a word, occurs in that Evidence's text on word boundaries, and
holds every number of the claim; the finding keeps the quote's offsets.
Every other claim gets a named reason. A reply that is not one finding
per claim makes the report `JUDGE_FAILED`, keeping valid usage. The
report names the judge and its version and holds the SHA-256 of the
judged text. Provider failures raise `ChatModelError` subclasses; a
missed deadline raises `ChatModelTimeout`. No log record, repr, or error
holds answer, claim, quote, or Evidence text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.support"`
fails with `ModuleNotFoundError`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- Which verdict a claim of "26 days" gets when the cited Evidence says "24 days", with the lexical judge, and with a judge that quotes "24 days of annual leave": with the lexical judge, `NO_SUPPORT`, because no cited sentence holds the number 26, so it proposes nothing; with the quoting judge, `NUMBER_MISMATCH`, because the quote is in the text on word boundaries but does not hold the claim's number 26.
- How many tests fail if the server trusts the judge's quote without looking for it in the Evidence text: 7 of 74; every case whose quote is not in the cited text (a changed number, a changed case, a quote from other Evidence, a quote inside a number, a quote that cuts an accent off), the supported case, whose offsets become wrong, and the model judge test.
- Why the report keeps a fingerprint of the answer text instead of a corrected copy of it: the Answer must stay exactly what the model wrote; a corrected copy would rewrite it silently, and no one could later tell what was changed. The SHA-256 proves which exact text the report judged, and a check by another judge version is a new report beside the old one.
- The learner's own case, a derived number: the Document says "2 kg of pears and 2 kg of apples", the Question asks how many kilograms there were in all, and the model answers "4 kg in all". The answer is right, but no Evidence holds the number 4, so even a judge that quotes "2 kg of pears and 2 kg of apples" gets `NUMBER_MISMATCH`. The claim is marked as not verified, not as false: the Answer stays as it is, and only the report says the server could not find it word for word. This is the price of the strict number rule, which prefers a false alarm on a sum to a false confirmation of a wrong number.

## Design

- **Changed files.** New `src/knowledge_service/answering/support.py` and `tests/test_support.py`. Small edits: `render_document` is public in `answering/prompt.py` and the Evidence prompt uses it; `_well_formed` in `answering/generation.py` became `well_formed_reply` and `well_formed_usage`. The `answering` row of `docs/architecture.md` names `check_support`.
- **Claims by position.** `split_claims` cuts the answer text at sentence ends (".", "!", "?", an ellipsis, followed by a space or the end) and at every kind of line break; a claim is its index and two offsets, never a copy. The run of sentence ends is possessive, so splitting is linear. At most 20 claims are judged; the rest are `NOT_CHECKED`.
- **The port.** `SupportJudge` has a `CheckerIdentity` (name and version) and one `judge(claims, evidence, ...)` that returns a `JudgeReply`: one unchecked `JudgeFinding` per claim and the usage, if any. It sees only the claim texts and the cited Evidence.
- **The server's rules.** `check_support` refuses anything but an `ANSWERED` result with visible text and its own cited Evidence, and a positive timeout, with `InvalidSupportRequest` before the judge. It asks once under one deadline, also checked after the judge returns. A reply that is not one well-typed finding per claim, in order, or with malformed usage, gives `JUDGE_FAILED`. A finding is `SUPPORTED` only for a cited Evidence and a quote that holds a word, is found in that Evidence on word boundaries with no accent cut off, and holds every number of the claim; otherwise `NO_SUPPORT`, `NOT_CITED`, `QUOTE_NOT_FOUND`, or `NUMBER_MISMATCH`.
- **The report.** `SupportReport` holds the checker identity, the SHA-256 of the judged text, the outcome, the findings with offsets, and the usage. The `AnswerResult` is never changed. One log record holds the request ID, the checker, counts, tokens, and the outcome.
- **Two judges.** `LexicalSupportJudge`: the first cited sentence holding every number of the claim and 60% of its words of four or more letters; deterministic, no model. `ModelSupportJudge`: a fixed prompt, version 1, rendered like the Evidence prompt and sent through any `AnswerModel`; its schema allows only the cited IDs or null and exactly one finding per claim; a broken model reply raises `ChatModelMalformedResponse`.

## Verification

| Command | Result |
|---|---|
| `uv run pytest tests/test_support.py tests/test_prompt.py tests/test_generation.py -q` | 165 passed, 1 deselected |
| `make format-check` | 242 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors |
| `make test-unit` | 1983 passed |
| `make test-integration` | 342 passed |
| `make docs-check` | passed |

- **Expected failing check.** Before Step 3, importing `knowledge_service.answering.support` failed with `ModuleNotFoundError`.
- **Deliberate break.** Trusting the judge's quote (`start = 0`) failed 7 of 74 tests; restored, 74 passed. Details under Decision.
- **Live run.** Not made: no `OPENAI_API_KEY` was available.
- **Known limits.** The lexical judge counts a number anywhere in the matching sentence and cannot see a paraphrase or a negation. A derived number, such as a sum, is never verified word for word. A judge that never waits is caught late, not stopped. Nothing calls `check_support` yet.

## Review

An adversarial review found no way to get a quote accepted that is not in a cited Evidence: random Unicode quotes, hostile finding objects, 100,000 findings, a 10 MB quote, and uncited Evidence with a planted marker all held. It found nine weaker spots, each fixed with a test that failed before the fix: usage with text in its token counts reached the log; a model reply of the wrong shape crashed the model judge with an untyped error; a long run of dots took seconds to split; the lexical judge split "2.5", "1,000", and "-5" into parts; a quote could match inside a word or be a lone dot; a Citation listed twice crashed; a judge that never waits ran past the deadline; only one kind of line break ended a claim; a model judge reply had no size limit and accepted repeated keys, and a judge name that raised crashed. 18 regression tests failed against the unfixed code and pass now.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **The judge proposes; the server decides.** Whether a sentence means what a text says is a judgement a model makes better than code, but a model can be wrong or led by a Document. So a judge only proposes an Evidence and a quote, and the server accepts it only when that quote is in a cited Evidence, on word boundaries, with every number of the claim. A judge can make a true claim look unverified; it can never make a false one look supported by words that are not there.
- **A report, never an edit.** Removing or rewording unsupported sentences would show the user an Answer the model never wrote, and no one could later tell what changed. The Answer stays as it is; the report stands beside it with the judge's name and version and the SHA-256 of the judged text. A better judge later writes a new report, not a change to the old one.
- **Every number must be in the quote.** Numbers are where Answers go wrong most visibly: days, amounts, dates. A quote showing 24 must never support a claim of 26. The price is the learner's own case: "2 kg of pears and 2 kg of apples" makes "4 kg in all" unverified, a false alarm on a correct sum. The rule prefers that to a false confirmation.
- **Two judges behind one port.** `LexicalSupportJudge` is code, deterministic and free, so tests and offline runs can rely on it. `ModelSupportJudge` reuses the M06-T04 `AnswerModel`, so OpenAI works as a judge with no new provider code; its prompt version is part of its identity. Both are checked by the same server rules.
- **Left for later.** What the user sees about unverified claims, a mark, a warning, or an abstention, is M06-T07. Running the check while an Answer streams is M06-T09. Measuring how often each judge agrees with people is M10. Derived numbers, such as sums, would need a judge that gives several quotes and a server that recomputes; not now.
- **Deliberate break.** Replacing the quote search in `_checked` with `start = 0`, so the server trusts the judge's quote, failed 7 of 74 tests: quotes not in the text (a changed number, a changed case, a quote from other Evidence), a quote inside a number, a quote that cuts an accent off, the supported case, whose offsets became wrong, and the model judge test. Restored; 74 passed.
- **Not run live.** No `OPENAI_API_KEY` was available, so it is unproven that OpenAI's strict mode accepts the judge's schema with its `enum` that includes null.
- **Falsified assumption.** I assumed that a correct answer always passes the support check. The learner's pears-and-apples case shows that a correct derived number, 4 from 2 and 2, is never found word for word, so it is `NUMBER_MISMATCH`. So "unsupported" here means "not verified word for word", not "wrong".
- **Second falsified assumption.** I assumed a quote found with `str.find` in the Evidence is real support. The review shows "24 days" is found inside "124 days", and a lone "." is found almost anywhere. So finding the characters is not enough: a quote must hold a word, sit on word boundaries, and hold the claim's numbers.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. **What `check_support` hides from M06-T07.** That code never has to cut the answer into claims, keep uncited Evidence away from the judge, look for quotes in the text, check word boundaries or numbers, survive a judge's broken reply, enforce a deadline, or keep the judge's cost. It gets one report: which claims are supported, by which quote, and why the others are not.
2. **Letting the answer model mark its own claims.** It would grade its own homework: the model that wrote "26 days" would also say it is supported. The server's rule would still hold, because a mark is accepted only with a quote found in cited text that holds the claim's numbers, so a self-graded "26" still fails. But a second, independent judge would no longer catch a misreading the answer model shares with itself.
3. **A wrong lexical verdict.** "Leave is 24 days" against "Leave was 20 days; 24 days is the limit for managers": the sentence holds 24 and the words, so the lexical judge says supported. A model judge would see the claim is about managers only. A test with this pair, the lexical judge, and a scripted model judge that names no Evidence would show the two verdicts side by side.
4. **Two reports that disagree.** `checker.name` and `checker.version` say which judge made each one; equal `answer_sha256` values prove both judged the same text. Neither report replaces the other.
5. **The next attack.** A quote that is in the cited Evidence and holds the claim's number, but from a sentence about something else, such as "24" in "24 hours", to show that the server checks the words and numbers, not the meaning; only the judge can catch that.
