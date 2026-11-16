# M06-T12 — Resolve follow-up Questions evidence

## Invariant

`resolve_question` sends a Question to the rewrite model only when
`classify_question` calls it `follow_up`, the policy switches `follow_up`
on, and its stated language is English or Ukrainian. It sends the Question
and at most the ten newest earlier Questions of the reader's History, never
an Answer or a Chunk. It uses a rewrite only when every new word is in the
stated language and the rewrite keeps the Question's words to keep and the
identifiers and acronyms of the newest earlier Question; otherwise the
search text is the Question as typed. The `ResolvedQuestion` records both
texts and how the search text was chosen. Reprs, log records, errors, and
reports hold no Question or rewrite text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.follow_up"`
fails with `ModuleNotFoundError`.

## Predictions

Drafted by Claude at the learner's request, after the guide's reference run, so they are not blind; the learner should rewrite them in their own words.

- Which rewrite classes reach the model when the policy switches on every rewritable class: only `follow_up`; an acronym or cross-language Question is never sent, and identifiers and self-contained Questions never were.
- What happens when a Ukrainian follow-up comes back rewritten with the English words of an earlier English Question: the rewrite is not used, the fallback is `InvalidRewrite`, and the Question is searched as typed.
- With a rewriter that only joins the newest earlier Question to the follow-up, whether every follow-up case ranks better, and what the rollout decision is: no; "And abroad?" goes from missed to found, but one Ukrainian case drops from rank 1 to 2, so `follow_up` is `harms` and may not be switched on from this run.
- How many unit tests fail if the resolver passes the policy on without narrowing it to `follow_up`: 2 of 34, the acronym and cross-language cases of `test_only_a_follow_up_is_ever_sent`.

## Design

- **What is sent, and when.** `resolve_question` (`answering/follow_up.py`) narrows any `RewritePolicy` to `FOLLOW_UP_ONLY`, and to nothing for a language other than English or Ukrainian, then calls `rewrite_question` from M05-T15. So a Question reaches the Structured Outputs rewriter only when `classify_question` calls it `follow_up`. The model gets the Question and `earlier_questions(history)`: the newest stretch of the History's Questions, at most `MAX_HISTORY` (10), stopping at the first that cannot be searched. It never gets an Answer or a Chunk, and the Questions come from `load_history`, owner-scoped.
- **Two extra checks for a follow-up.** A rewritten follow-up is checked again with `checked_rewrite`, as if only the Question and the carried entities were typed. That refuses new words in another alphabet, which M05-T15 allowed for any word of an earlier Question. The carried entities, the identifiers and acronyms of the newest earlier Question (`_entities`, classified as of no language), are added to the words to keep. A rewrite that fails either check falls back to the Question as typed with `InvalidRewrite`, keeping its usage.
- **Traceable.** `ResolvedQuestion` holds the Question as typed, the search text, the language, the class, the outcome, the fallback, the rewriter, the usage, how many earlier Questions were sent, and the carried entities. Texts are out of its repr. One log record, "follow-up resolved", holds names and counts only.
- **Measuring.** `compare_resolution` (`answering/follow_up_evaluation.py`) refuses a dataset not from its loader, then stores each labelled case's earlier Questions as turns of the evaluation reader with `add_turn`, reads them back with `load_history`, and resolves the case. It searches every case as typed, and every resolved case again under the ID suffix `+resolved`, in one `compare_branches` run, and pairs the fused ranks into a `RewriteComparison`. `score_rewriting` and `render_resolution` then report gain, harm, and the rollout decision per class.

## Verification

- `uv run python -c "import knowledge_service.answering.follow_up"`: failed with `ModuleNotFoundError` before, imports now.
- `uv run pytest tests/test_follow_up.py -q`: 34 passed. `pytest -m integration tests/test_follow_up_integration.py`: 6 passed.
- Gates: `make format-check`, `make lint`, `make typecheck` clean; `make test-unit` 2255 passed (+34); `make test-integration` 376 passed (+6); `make docs-check` passed.
- Deliberate break: `policy.classes` instead of `policy.classes & FOLLOW_UP_ONLY.classes` gave 2 failed (`test_only_a_follow_up_is_ever_sent[acronym]` and `[cross-language]`), 32 passed; restored, 34 passed.
- Measurement, fake joining rewriter and fake embedding, fused retrieval, limit 10: `follow_up` 3 cases, 3 rewritten, MRR 0.444 as typed and 0.367 resolved, -0.078; `en-follow-up-abroad` missed to rank 10, `uk-follow-up-abroad` 1 to 2 (harmed), `uk-follow-up-his` 3 to 2; rollout `harms`. Every other case was not sent and kept its ranks.
- Known limits: no live OpenAI run, so the gain of real resolution is not measured here (M05-T15 measured +0.389 MRR live for the same three requests); an entity search syntax would misread, such as `x_or_y`, can never be kept; nothing in the answer flow calls `resolve_question` yet, and the search text is not stored with the turn.

## Review

An independent adversarial review fuzzed the resolver with random Questions, Histories, languages, policies, and model answers, with a marker in every text. Only `follow_up` in English or Ukrainian was ever sent, at most ten earlier Questions, never an Answer, and the marker never reached a repr, a log record, or an error. It found five problems, each fixed with a regression test that failed on the first version:

- A Ukrainian follow-up rewritten with the English words of an earlier English Question was used. Now a follow-up's rewrite is checked again with only the Question and the carried entities as typed (`test_a_follow_up_rewritten_in_the_language_of_an_earlier_question_is_not_used`).
- A History holding something other than text crashed untyped or was split into letters. Now it is refused with `InvalidRewriteRequest` before anything is sent (`test_a_history_that_is_not_one_is_refused_before_anything_is_sent`).
- An unsearchable newest Question was skipped, so an older topic took its place. Now the stretch sent stops there (`test_the_questions_sent_always_end_at_the_newest_one`).
- `compare_resolution` paired cases by position without checking the dataset. Now a mismatched one is refused with `DatasetRunRefused` (`test_a_dataset_not_from_its_loader_is_refused`).
- A legal case ID ending in `-resolved` could take a resolved copy's ranks. Now copies end in `+resolved` (`test_a_case_named_like_a_resolved_one_keeps_its_own_outcome`).

Documented, not fixed: an entity that search syntax would misread can never be kept, so its follow-ups are searched as typed; that fails safe.

## Decision

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

- **Only follow-ups.** M05-T15 allowed only `follow_up` and measured acronym rewriting as harmful. A deployment flag can still list more classes, so the answering path narrows the policy itself: one wrong setting cannot start rewriting acronyms or identifiers in every Answer.
- **Questions, never Answers.** The subject of a follow-up is named in the User's own Questions. An Answer is text written from Chunks; sending it to the rewriter would let Document content leave storage through a second provider call, and an Answer withheld by History could still be quoted. Questions are the reader's own words, read owner-scoped.
- **A second check for follow-ups.** The M05-T15 rules let any typed word keep its alphabet, which is right for one Question but wrong across a bilingual Conversation. The resolver reuses `checked_rewrite` with only the Question and the carried entities as typed, so language and entities are kept by the same tested rules instead of new ones.
- **What the measurement shows.** With a fake rewriter that only joins texts, the path works end to end through stored Conversations and History, resolution finds a Chunk the follow-up alone misses, and the rollout guard refuses a class that harms a case. It cannot show the gain of real resolution: the fake does not resolve, and the dense ranks come from a fake embedding. Before `QUERY_REWRITE_CLASSES='["follow_up"]'` is switched on, a live `compare_resolution` run must show a gain with no case harmed.
- **Left for later.** Calling `resolve_question` in the answer flow before retrieval, and storing the search text with the turn so an Answer can be traced to what was searched after the request ends.

**Falsified assumption.** I assumed the M05-T15 checks already kept the stated language of a rewrite. The review shows a Ukrainian follow-up rewritten in English words of an earlier English Question passed, because every typed word may keep its own alphabet. So "the language rule is enough for follow-ups" was wrong; follow-ups are now checked again with only the Question and the carried entities as typed.

**Break run.** With `policy.classes` passed on unnarrowed: 2 failed (acronym, cross-language), 32 passed. Identifier and self-contained Questions still passed, because `rewrite_question` never sends those classes. Restored: 34 passed.

## Reflection

Drafted by Claude at the learner's request; the learner should rewrite it in their own words.

1. The answering code never decides which classes may be rewritten, which earlier Questions to send and how many, whether a rewrite kept its language and entities, what to do when the model fails or answers late, or how to log it without text. It gets one search text and a record of how it was chosen.
2. Answer text written from Chunks would leave storage in a second provider request, including Answers History withholds if the resolver read the raw Transcript. The first test would plant a marker in an earlier Answer and assert it never reaches the rewriter, as `test_only_questions_are_sent_never_an_answer` does now.
3. Joined text holds the words of both Questions, so lexical search ranks Chunks matching the earlier topic, and the dense vector moves toward the earlier Question too; a neighbouring Chunk about the same topic can overtake the right one. I would compare the Candidates of both searches for that case, branch by branch.
4. Nothing is sent: the log is longer than a search text may be, so the stretch of earlier Questions stops before it and the Question has no earlier Question; it is searched as typed. That is right: an older topic must not take the log's place, and a log is a poor search text anyway.
5. A follow-up whose earlier Question is in the other language, such as "А за кордоном?" after an English question about the daily allowance, and one that refers to a Question two turns back. I expect the resolver to keep Ukrainian or fall back, and the rollout to be `harms` or `incomplete` if resolution moves either case down.
