# M05-T15 — Query rewriting evidence

## Invariant

`rewrite_question` sends a Question to the rewrite model only when its
class is rewritable and switched on in the `RewritePolicy`, which is empty
by default. It searches with the rewrite only when `checked_rewrite`
accepts it: every word to keep and every typed identifier survive as
typed, every other letter is in the stated language's alphabet, and no
search operator or hidden character is added. Any failure, late answer,
or rejected rewrite searches the Question as typed and keeps the billed
usage. Only the Question, its earlier Questions, the language, the class,
and the words to keep are sent. `check_rollout` refuses a policy that
switches on a class whose measurement harmed a case, fell back, ran
degraded, had fewer than two rewritten cases, or did not raise MRR.
Errors, logs, reprs, and reports hold no Question or rewrite.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.retrieval.query_rewriting"`
fails with `ModuleNotFoundError`.

## Predictions

These were written before the run in this repository. They are not blind:
Claude wrote them and had run the prototype against OpenAI three times
while preparing the lesson.

- Which rewritable classes will gain, and which might harm retrieval:
  `follow_up` will gain, because "And abroad?" alone does not name the
  daily allowance; `acronym` will harm `en-en-vpn-first-connection`,
  because that Question already ranks first and a longer rewrite moves
  another VPN Chunk above it; `cross_language` cannot be judged with its
  one measured case.
- MRR as typed and rewritten for `follow_up`: 0.611 as typed and 1.000
  rewritten, a gain of +0.389, because all three follow-ups reach rank 1
  once the earlier Question's topic is in the search text.
- How many rewrites the check will refuse in a real run, and why: zero or
  one; the likeliest is `uk-acronym-vpn`, if the model spells out VPN in
  English inside a Ukrainian rewrite, which changes its alphabet.
- The cost of one run in USD: about 0.0018 USD for six requests, about
  1,450 input and 155 output tokens.
- The policy you expect to ship: `QUERY_REWRITE_CLASSES='["follow_up"]'`
  in production once the Answer pipeline calls the rewriter, and an empty
  flag in tests and local development, so they stay offline and free.

## Design

Completed on 2026-11-11.

Changed files:

- `src/knowledge_service/retrieval/query_rewriting.py` (new): the
  `RewriteModel` port, `RewriterError` and its subclasses,
  `InvalidRewriteRequest`, `RewritePolicy`, `RewriteDefect` with
  `InvalidRewrite`, `checked_rewrite`, `rewrite_question`, and
  `RewriteResult`.
- `src/knowledge_service/retrieval/openai_rewriting.py` (new):
  `OpenAIRewriteModel` and `QueryRewriteSettings`, whose `classes` field is
  the policy flag `QUERY_REWRITE_CLASSES`.
- `src/knowledge_service/retrieval/rewrite_evaluation.py` (new):
  `score_rewriting`, `ClassGain`, `Rollout`, `allowed_classes`,
  `check_rollout`, `RolloutRefused`, and `render_rewrite_comparison`.
- `src/knowledge_service/retrieval/evaluation_dataset.py`:
  `load_rewrite_dataset`, `RewriteDataset`, `compare_rewriting`,
  `RewriteCaseOutcome`, `RewriteComparison`, `_rewrite_case`,
  `_rewriter_identity`; the file reading of `load_dataset` moved into
  `_read_json`, unchanged in behaviour.
- `scripts/run_retrieval_dataset.py`: `--compare-rewriting` with
  `_run_rewriting` and `REWRITING_REPORT`; a bad setting prints only its
  field name.
- `datasets/bilingual-seed/rewrite-retrieval.json` (new): the right Chunks
  for 24 labelled cases.
- Tests: `tests/rewriting_fakes.py`, `tests/test_query_rewriting.py` (68),
  `tests/test_openai_rewriting.py` (34 and 1 live),
  `tests/test_rewrite_evaluation.py` (20), and
  `tests/test_rewrite_evaluation_integration.py` (4), all new.
- `docs/architecture.md`: the `retrieval` row and one paragraph on what
  rewriting sends to OpenAI.

Why a policy per class, off by default: each rewritable class can help or
hurt on its own, so each is switched on on its own, and only after a
measurement allows it. `RewritePolicy` refuses `exact_identifier` and
`self_contained`, in code and when the settings are read.

What is sent, and what never is: the Question, its earlier Questions, the
stated language, the class, and the words to keep, as one JSON document,
with `store=False`. No Chunk, Document, or reader is ever part of a
request, so rewriting cannot leak text the reader may not see; the
permission check stays inside search, which the rewritten text goes
through like any Question.

Each rule of `checked_rewrite` and the attack it stops:

| Rule | Stops |
|---|---|
| Plain `str` with a letter or digit, at most 1,000 characters | a `str` subclass running its own code; `???`, which word search refuses |
| No control, invisible, combining, or symbol character | NUL, a lone surrogate, a direction override, a zero-width space, `Ⓐ` |
| No search operator, by PostgreSQL's own rule | `-VPN`, `(-abroad)`, `(or)`, quotes, and long `(-` runs that broke `websearch_to_tsquery` |
| Every word to keep, whole, as typed | dropped, glued, or lowercased `VPN` |
| Every word with a digit typed by the user | `EXP-2041` changed to `EXP-2014`, or a new number |
| Every other letter in the stated alphabet | another language, Greek or Latin look-alikes, fullwidth or styled letters, Russian letters |

Deadline and cost: one request within `timeout_seconds`, no SDK retries.
A late answer is billed and recorded but not used. Every billed answer is
kept in the result, also a refused one.

How the gain is measured: each of the 24 cases is searched as typed, then
again with the rewrite when one is used; a case not rewritten keeps its
ranks. MRR per class compares the two. The rollout rules, in order:
`never_rewritten`, `harms` (a lower best rank or a lost relevant Chunk),
`incomplete` (any fallback or degraded search), `too_few_cases` (fewer
than two rewritten), `no_gain` (a tie or worse), and `allowed`.

## Verification

Commands, in order, on the final code:

| Command | Result |
|---|---|
| `uv run pytest tests/test_query_rewriting.py tests/test_openai_rewriting.py tests/test_rewrite_evaluation.py -q` | 122 passed, 1 deselected |
| `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=... uv run pytest -m integration tests/test_rewrite_evaluation_integration.py -q` | 4 passed |
| `make format-check` | 223 files already formatted |
| `make lint` | All checks passed! |
| `make typecheck` | 0 errors, 0 warnings, 0 informations |
| `make test-unit` | 1660 passed, 322 deselected |
| `KNOWLEDGE_SERVICE_TEST_DATABASE_URL=... make test-integration` | 317 passed, 1665 deselected |
| `make docs-check` | passed |
| `git diff --check` | clean |
| `--compare-rewriting`, empty flag | exit 0; report under Decision; 0.001786 USD |
| `--compare-rewriting`, `["follow_up", "acronym"]` | same tables; `rollout refused: ... acronym (harms)`; exit 1; 0.001786 USD |
| bad flag `["exact_identifier"]` | `a setting is invalid: classes`; exit 1; nothing sent |

Both real runs used BGE-M3 on `127.0.0.1:8766`, because port 8765 was
taken by another program, and a throwaway database
`knowledge_service_dataset`, dropped afterwards.

Predictions against the result:

- Gain and harm per class: right. `follow_up` gained, `acronym` harmed
  `en-en-vpn-first-connection`, and `cross_language` had one case.
- MRR for `follow_up`: right, 0.611 to 1.000.
- Rewrites refused: zero in both runs, inside the predicted "zero or one".
- Cost: right, 0.0018 USD per run.
- Policy: as predicted, `["follow_up"]` for production and empty
  elsewhere.

Known limits: 24 measured cases are few; `follow_up` rests on 3 cases, of
which 2 improved, and `cross_language` on 1. A Russian word made only of
letters Ukrainian shares, and any word the user typed, pass the alphabet
check. A model that likes "or" falls back often, and any fallback holds
its class back. The same Question may be rewritten differently from run
to run. When the deadline cancels the OpenAI call, that call's usage is
not known and only a "cancelled" log is written. An adapter that ignores
cancellation can run past the deadline, though its answer is then
refused. Nothing in the application calls `rewrite_question` yet, so the
rollout gate runs only in the evaluation script.

## Review

An adversarial review of the prototype, before the guide was written,
found four broken guarantees. All are fixed, each with a test that failed
before the fix:

- A rewrite in a third alphabet, such as Greek, Chinese, or fullwidth
  letters, passed the language check. Now every letter must be in the
  stated alphabet.
- A rewrite with many dashes passed the check and then broke word search
  with `tsquery stack too small`. Now such a `-` is a search operator and
  refused.
- `-VPN` or a lone `or` kept the word to keep but excluded it from the
  search or widened it. Now search operators are refused.
- A class whose rewrites fell back or ran degraded could still be
  `allowed`. Now such a class is `incomplete`.

The same review led to refusing invented identifiers and hidden
characters, counting a lost Chunk as harm, a stronger line-up check in
`compare_rewriting`, and a short message for a bad flag. A second review
found that word search also reads an operator right after punctuation, so
`(-abroad)` or `(or)` still passed; now the check follows PostgreSQL's own
rule, and the alphabets are exact letter lists, which also refuse Russian
and look-alike Latin letters. Ten new tests failed on the code before
this fix. A fuzz of 6,000 generated rewrites against PostgreSQL then
found no accepted one that `websearch_to_tsquery` reads as an operator.

Finishing reviews (Standards and Spec, run in parallel on the same
worktree) found nothing blocking. Non-blocking points left as they are:

- `_rewriter_identity` names the class of an identity error in its
  message, as `_reranker_identity` already does; the script builds a
  trusted rewriter.
- The comment above `reasoning_effort` in both OpenAI adapters says that
  None sends no setting, while the default is `"none"`, which is sent.
- No test steers a rewrite toward restricted text; the rewritten search
  goes through the same `_outcome` check that refuses restricted Chunks.
- The rollout gate is only in the evaluation script until the Answer
  pipeline reads `QueryRewriteSettings`.
- `follow_up` is allowed on three cases; one adversarial follow-up could
  change the decision.

## Decision

Report from `uv run python scripts/run_retrieval_dataset.py --compare-rewriting`
(BGE-M3 on `127.0.0.1:8766`, empty `QUERY_REWRITE_CLASSES`, exit 0),
copied here because `artifacts/` is ignored by Git:

| Class | Cases | Rewritten | Fallbacks | Degraded | MRR as typed | MRR rewritten | Gain | Harmed | Rollout |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| exact_identifier | 6 | 0 | 0 | 0 | 1.000 | 1.000 | +0.000 | none | never_rewritten |
| follow_up | 3 | 3 | 0 | 0 | 0.611 | 1.000 | +0.389 | none | allowed |
| acronym | 2 | 2 | 0 | 0 | 0.750 | 0.500 | -0.250 | en-en-vpn-first-connection | harms |
| cross_language | 1 | 1 | 0 | 0 | 1.000 | 1.000 | +0.000 | none | too_few_cases |
| self_contained | 12 | 0 | 0 | 0 | 0.958 | 0.958 | +0.000 | none | never_rewritten |

The rewritten cases moved like this: `en-follow-up-abroad` 3 to 1,
`uk-follow-up-abroad` 2 to 1, `uk-follow-up-his` 1 to 1,
`en-en-vpn-first-connection` 1 to 2, `uk-acronym-vpn` 2 to 2, and
`uk-cross-language-per-diem` 1 to 1. Six requests were billed: 1,464 input
and 153 output tokens, about 0.0018 USD.

The policy chosen, per environment:

| Environment | `QUERY_REWRITE_CLASSES` | Why |
|---|---|---|
| Tests | empty | Tests stay offline, free, and repeatable; the fake model covers the code. |
| Local development | empty | No key or bill is needed to search; `--compare-rewriting` stays the way to measure. |
| Production | `["follow_up"]`, once the Answer pipeline calls the rewriter | It is the only class the run allows: +0.389 MRR, no case harmed, no fallback. |

Classes left off: `acronym` harms `en-en-vpn-first-connection`, which
already ranked first; `cross_language` has one measured case, too few to
judge. `exact_identifier` and `self_contained` are never rewritten.

The rollout gate works. A second run with
`QUERY_REWRITE_CLASSES='["follow_up", "acronym"]'` gave the same tables and
then `rollout refused: the policy switches on classes the measurement did
not allow: acronym (harms)`, with exit 1.

Deliberate break: deleting the `elif fallbacks or degraded:` branch from
`_gain` made 3 tests fail: the `fallback` and `degraded` cases of
`test_a_class_that_harms_or_was_not_fully_measured_is_held_back`, and
`test_the_report_names_cases_by_id_and_gives_the_largest_safe_policy`.
Without the rule, a class whose rewrites timed out would be `allowed`
although its harmful rewrites were never searched. After undoing the
change, 20 passed.

Rewrites the check refused: none in these two runs. In one of the three
prototype runs it refused the rewrite of `uk-acronym-vpn` and searched the
Question as typed; refusing it was right, because the case's rank did not
improve in any run, so nothing was lost.

Falsified assumption: M05-T14 marked `acronym` as a class a rewrite may
help, assuming that spelling out an acronym helps search find the page.
The run shows the opposite on this corpus: `en-en-vpn-first-connection`
fell from rank 1 to 2 and `uk-acronym-vpn` stayed at 2. So "acronyms
gain from a rewrite" is wrong here; the corpus pages already use the
acronym, and a longer search text pulls in other VPN Chunks.

## Reflection

Claude drafted these answers; the learner should rewrite them in their
own words.

1. What `rewrite_question` hides from the Answer pipeline: which classes
   may be rewritten and which are switched on; what the model is sent and
   what it never sees; the deadline and what a late answer means; every
   rule of `checked_rewrite`; which errors are a fallback and which a bug;
   and that the billed usage is kept either way. The caller gets one
   search text, an outcome, and a usage.
2. Skipping `checked_rewrite` for fast answers would break "it searches
   with the rewrite only when `checked_rewrite` accepts it". A fast model
   can still drop `VPN` or add `-VPN`. `test_a_rewrite_that_breaks_a_rule_falls_back_and_keeps_its_cost`
   and `test_the_search_text_is_the_question_or_a_rewrite_that_keeps_its_words`
   would fail, because the fake model answers at once.
3. For `acronym` to be switched on, the corpus would need pages that use
   the full name and not the acronym, or the rewrite would have to keep
   the search text short, for example adding the full name without other
   words. I would add labelled acronym cases whose right page spells the
   name out, and switch the class on only if a run shows a gain and no
   harm on all of them.
4. No prediction failed outright, because they were written after
   prototype runs. The one that rested on a belief rather than a run was
   from M05-T14: that spelling out an acronym helps search. The run
   falsified it.
5. I would add a follow-up whose earlier Question is about a different
   topic than the Chunk that answers it, such as "And for contractors?"
   after a leave question, where the right page is about expenses. If the
   rewrite drags the old topic in and the right Chunk falls or is lost,
   `follow_up` becomes `harms`, and rewriting must be switched off again.
