# M06-T07 — Calibrated abstention evidence

## Invariant

`assess_evidence` returns `EMPTY` for no eligible Evidence and `WEAK` for
a best score below the policy's threshold, and raises `UncalibratedScore`
for a score of any other reranker or a missing one. `decide` returns one
decision with one reason, in this order: no Evidence, weak Evidence, a
refused, invalid, or withdrawn Answer, the model's abstention, conflicting
Evidence, then claim support. It answers only when the support report is
about this exact text, by SHA-256 and claims, and enough of all claims are
supported, unchecked ones counted as unsupported. Empty, unauthorized,
and withdrawn give the same reason and message. `classify` maps a
decision and an answerability label to a correct answer, a correct
abstention, an over-abstention, or an unsupported answer.
`calibrate_threshold` returns the threshold that answers the fewest
unanswerable cases within the over-abstention budget. Decisions and
messages hold no Document, Question, or answer text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.abstention"`
fails with `ModuleNotFoundError`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- What a user is told when they ask about a Document they may not see, compared with a question about nothing at all: exactly the same thing, `NO_EVIDENCE` and "I could not find documents that answer this question.", because retrieval never returns a Chunk the reader may not see, so both searches come back empty, and a different message would reveal that a hidden Document exists.
- Which threshold `calibrate_threshold` picks for the scores 0.95, 0.80, 0.60, 0.30 (answerable) and 0.35, 0.20, 0.10 (unanswerable), with a 25% over-abstention budget, and why: 0.60; 25% of four answerable cases allows one wrong abstention, and abstaining below 0.60 loses only the 0.30 case while answering none of the unanswerable ones, which no lower threshold manages.
- How many tests fail if `decide` skips the support check, and which kind of evaluation error grows: 6 of 57; the unsupported claim, the unchecked report, the report of another text, the unchecked claims after 20, the missing claims, and the evaluation test, where unsupported answers grow from 1 to 2 because "30 days" is now answered.

## Design

- **Changed files.** New `src/knowledge_service/answering/abstention.py` and `tests/test_abstention.py`. Small edit: `numbers_in`, `topic_words`, and `shares_topic` are public in `answering/support.py`, and the lexical judge uses them. The `answering` row of `docs/architecture.md` names `decide`.
- **Sufficiency signals.** `assess_evidence` takes `EvidenceSignals` (the eligible count, the best reranker score, and its `RerankerIdentity`) and an `AbstentionPolicy`: no eligible Evidence is `EMPTY`; a best score below the policy's threshold is `WEAK`; a score of another reranker, a forged identity, or a missing or non-finite score raises `UncalibratedScore`.
- **Conflicts.** `find_conflicts` takes each of the first 20 claims with a number and at least two longer words, keeps the distinct cited sentences holding 60% of those words, and reports the claim when two of them, from different Documents, share 80% of their own words and hold different numbers of the claim.
- **The order of reasons.** `decide` checks: `EMPTY` and a no-Evidence or withdrawn Answer give `NO_EVIDENCE`; `WEAK` gives `WEAK_EVIDENCE`; refused or invalid gives `ANSWER_FAILED`; the model's abstention gives `INSUFFICIENT_EVIDENCE`; a conflict gives `CONFLICTING_EVIDENCE`; then claim support gives `UNSUPPORTED`. Only an Answer past all of them is `ANSWER`. Each reason has one fixed message in English and Ukrainian.
- **Matching a report to its text.** A support report counts only when it is `CHECKED`, its SHA-256 equals the answer text's, and its claims equal `split_claims` of that text. All claims are the denominator; unchecked ones count as unsupported. The policy's share must be above zero.
- **Evaluation and calibration.** `classify` maps a decision and an answerability label to a correct answer, a correct abstention, an over-abstention, or an unsupported answer; `score_abstention` counts them. `calibrate_threshold` sorts labelled (score, answerable) pairs once and returns the threshold with the fewest unsupported answers among those whose over-abstentions fit the budget, read as the decimal it was written as; ties go to the lowest, and above every score is a candidate too.

## Verification

| Command | Result |
|---|---|
| `uv run pytest tests/test_abstention.py tests/test_support.py -q` | 131 passed, 1 deselected |
| `make format-check` | 244 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors |
| `make test-unit` | 2040 passed |
| `make test-integration` | 342 passed |
| `make docs-check` | passed |

- **Expected failing check.** Before Step 3, importing `knowledge_service.answering.abstention` failed with `ModuleNotFoundError`.
- **Deliberate break.** Deleting the support check from `decide` failed 6 of 57 tests; restored, 57 passed. Details under Decision.
- **Known limits.** The conflict check is lexical: an inflected Ukrainian word or one number written two ways ("1,000" and "1000") can hide or invent a conflict. Nothing calls the policy yet. Calibration runs on hand-made cases until M10's labelled dataset.

## Review

An adversarial review found ten problems, each fixed with a test that failed before the fix: a support report of another text let any Answer through; claims after the first 20 were never counted; finding conflicts compared every pair and took 26 seconds on repeated text; a 0.29 budget over 100 cases allowed only 28 through float rounding; the threshold could never abstain on everything; a forged reranker identity borrowed the threshold; a case that was not a pair crashed with an untyped error; a year that only one Document adds counted as a conflict; one extra word in a claim hid a real conflict; a policy could require zero supported claims. It confirmed that every non-answer outcome abstains, that empty and withdrawn give the same message, that hostile signals raise typed errors, and that calibration is optimal against brute force over 500 random cases per budget and handles 300,000 cases in under 3 seconds.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **Retrieval first.** When nothing eligible was found, or only matches below the calibrated threshold, the model can only guess or abstain, and both cost a paid call. `assess_evidence` runs before generation, so `EMPTY` and `WEAK` skip the model entirely.
- **A hidden Document reads like nothing.** Retrieval never returns a Chunk the reader may not see, so a question about a hidden Document finds nothing. If its message differed from "nothing found", a user could learn that the Document exists. Empty, unauthorized, and withdrawn therefore share one reason and one message.
- **A threshold belongs to one reranker and comes from data.** Reranker scores have no fixed scale, so a number tuned for one model means nothing for another; a score from any other reranker raises `UncalibratedScore`. The threshold trades over-abstention against unsupported answers, and `calibrate_threshold` makes that trade explicit: the fewest unsupported answers within a chosen over-abstention budget.
- **One reason, in a fixed order.** Cheap checks on retrieval come first, then the Answer's own outcome, then conflicts, then claim support. The first reason that applies wins, so every abstention has exactly one explanation.
- **Left for later.** Calling the policy while an Answer streams is M06-T09. Calibrating on a real labelled Evaluation Dataset is M10. Telling a conflict apart from an inflected word or a number written two ways needs more than words and numbers; not now.
- **Deliberate break.** Deleting the support check from `decide` failed 6 of 57 tests: the unsupported claim, the unchecked report, the report of another text, the report with missing claims, the claims after 20, and the evaluation test, where unsupported answers grew from 1 to 2 because "30 days" was answered. Restored; 57 passed.
- **Falsified assumption.** I assumed a `SupportReport` in hand belongs to the Answer next to it. The review shows a report of "24 days" let "99 days and a free car" through. So a report must be matched to its text by SHA-256 and claims before it counts.
- **Second falsified assumption.** I assumed a cited sentence holding all of a claim's words talks about the same thing. The first prototype flagged "Unused annual leave carries over for 5 days" as a conflict with "24 days of annual leave", because both hold "days", "annual", and "leave". So two sentences must also be nearly the same as each other, and differ in the claim's own numbers, before they conflict.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. **What `decide` hides from M06-T09.** The streaming code never has to look at retrieval counts or reranker scores, know which reranker a threshold belongs to, read the Answer's outcome, compare cited Documents, match a support report to its text, count supported claims, or choose words for an abstention. It gets one decision, one reason, and a fixed message to show.
2. **Skipping the conflict check.** The support check proves that a quote is really in a cited Document, not that the Documents agree. With an old policy saying 20 days and a new one saying 24, an Answer of "24 days" passes support but may still be wrong for the reader. `test_documents_that_disagree_on_the_number_abstain` would fail.
3. **Who chooses the budget.** The people who own the knowledge base and answer for its mistakes, such as HR or the support team, not the developer. I would show them the evaluation table for a few budgets: how many questions would go unanswered against how many would get an unsupported answer, with real examples of each.
4. **Why the messages must match.** A different message for "hidden" than for "nothing" tells a user that a Document they may not see exists, and by trying topics, roughly what it is about. `test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing` compares the two decisions, and so the two messages.
5. **The next case.** "How many days do contractors get?" against a handbook that only covers employees. I expect the lexical judge to pass "Contractors get 24 days" and the policy to answer, an unsupported answer, which a model judge or a stricter support share would have to catch.
