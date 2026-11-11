# M05-T13 — Reranking strategies evidence

## Invariant

`compare_reranking_strategies` searches every case once and gives the
same eligible Candidates, in retrieval's order, to the cross-encoder and
to the language model, so the three strategies differ only in their
order. Every case is cross-encoded before any is sent to the language
model, so a failing cross-encoder stops the run before anything is
billed. A case the language model ranks only in part, or not at all, is
reported as such. The report refuses three reports that do not line up
case by case, and holds no question and no restricted text. No default
search reranks yet.

## Expected failing check

Before the change, `uv run pytest tests/test_reranking_strategies.py`
fails to import `compare_reranking_strategies` from
`knowledge_service.retrieval.evaluation_dataset`.

## Predictions

These were written before the run in this repository. They are not blind:
Claude wrote them and had seen one prototype run on `cpu` while preparing
the lesson.

- MRR and Recall@1 of the three strategies on all 16 cases: no reranker
  has MRR 0.969 and Recall@1 0.938; the cross-encoder and the language
  model both reach 1.000 and 1.000, because each fixed
  `uk-uk-sick-first-day` in its own lesson and nothing else was wrong.
- Cases where the cross-encoder and the language model rank differently:
  none, because 15 cases are already first and both rerankers agreed on
  the only uncertain one.
- Median time per case added by the cross-encoder on `cpu`, on `mps`, and
  by the language model: about 750 ms on `cpu`, about 300 ms on `mps`, and
  about 1,500 ms for the language model, close to the M05-T11 and M05-T12
  medians, because the models and the Candidates are the same.
- Cost in USD of one run, and per case: about 0.022 USD per run and about
  0.0014 USD per case, all of it from the language model, because the
  token counts should match M05-T12.
- The default I expect to choose for tests, for local development, and
  for production, and why: no reranker in all three. Tests must stay
  deterministic and offline; local development gains one case of 16 for
  a second server; production gains too little on 16 easy cases to pay
  for 2.5 to 13 times retrieval's time and, for the language model, a
  bill per question.
- The one number that would make me choose reranking in production: the
  cross-encoder raising MRR by at least 0.05 over fused retrieval on a
  dataset of at least 200 cases, with a median under 300 ms on the
  production device.

## Design

Completed on 2026-11-11.

Changed files:

- `src/knowledge_service/retrieval/evaluation_dataset.py`:
  `StrategyComparison`, `compare_reranking_strategies`,
  `render_strategy_comparison` with `_check_strategies`,
  `_strategy_quality`, `_strategy_cases`, `_strategy_costs`, and
  `_reranker_name`; `_move` and `_times` shared with `_delta_lines` and
  `_latency_lines`, whose output did not change; one docstring bullet.
- `scripts/run_retrieval_dataset.py`: `--compare-rerankers`, exclusive with
  the other model flags, with `_run_strategies` and `STRATEGIES_REPORT`.
- Tests: `tests/test_reranking_strategies.py` (new) and
  `tests/test_reranking_strategies_integration.py` (new).
- `docs/architecture.md`: the `retrieval` row and one paragraph.

Why one search per case: in M05-T11 and M05-T12 each reranker had its own
run with its own search. Here each case is searched once and that one list
goes to both rerankers, so any difference between the strategies comes
from the reranker alone.

Why the same order: both rerankers get the Candidates in retrieval's
order. A language model may favour what it reads first, so giving it the
cross-encoder's order would hand it an advantage.

Why the cross-encoder runs first for every case: it is all or nothing, as
in M05-T11. The run cross-encodes every case before any case reaches the
language model, so a cross-encoder failing on any case stops the run with
nothing billed.

Why a `limit` above the budget is refused and partial rankings are listed:
`rerank_with_llm` sends only the head that fits its budget. A `limit` above
`max_candidates` would always cut the list, so it is refused before any
work; a case the token limit cuts short is listed as
`(case, ordered, given)`, so the report never claims both rerankers ordered
the same number of Candidates when they did not. Two rerankers with one
identity are refused too, because the report tells the strategies apart by
name.

Why the renderer checks that the reports line up: it zips three reports
row by row. `_check_strategies` raises `ValueError` unless they come from
one run (name, version, limit, branches), hold the same case IDs in the
same order, and are no reranker, then two different rerankers.

What the report shows: Recall@1 and MRR per slice, because every strategy
only reorders the same Candidates and Recall@10 cannot change; ranks per
case; moves against no reranker; the cases where the rerankers disagree;
fallbacks and partial rankings; and last, "## Latency and cost", the only
section that changes from run to run, with the device.

What leaves the machine: as in M05-T12, only the question and the text of
Candidates that passed the permission check, sent to OpenAI with
`store=False`. The cross-encoder runs locally. The seed corpus is made up.

## Verification

The expected failing check could not be observed as written: before
Step 4 the test file did not exist, so `uv run pytest
tests/test_reranking_strategies.py` collected nothing rather than failing
to import.

Gates, after Step 8, in order:

| Command | Result |
|---|---|
| `uv run pytest tests/test_reranking_strategies.py -q` | 10 passed |
| `make format-check` | 210 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors |
| `make test-unit` | 1457 passed |
| `make test-integration` (test database URL from `scripts/test_database_url.py`) | 313 passed |
| `make docs-check` | passed |
| `git diff --check` and `git diff --cached --check` | clean |

Earlier checks: after Step 3, the existing report tests
(`tests/test_evaluation_dataset.py`, `tests/test_reranking.py`,
`tests/test_llm_reranking.py`) gave 143 passed, so the older reports did
not change; after Step 5, `--compare-rerankers --llm-rerank` ended with
`argument --llm-rerank: not allowed with argument --compare-rerankers`;
`tests/test_reranking_strategies_integration.py` gave 4 passed (Step 6).

The two real runs, their agreement, and the deliberate break are under
"Decision". No default search reranks: `retrieve_hybrid` and the API are
unchanged, and only the evaluation script calls
`compare_reranking_strategies`.

Privacy: the integration test captures logs at DEBUG and checks that the
restricted marker `CONFIDENTIAL-SEED-7731` never reaches either reranker
and that neither the report nor the logs contain any question or that
marker. Errors name the case and the error type only.

Known limits: 16 cases are few, and fused retrieval already ranks 15 of
them first; a language model may order differently on another run; the
price is a setting; the cross-encoder's time depends heavily on the
device, so the decision names it; one run per device, so slowest times are
not comparable.

Review: an adversarial review of the prototype found two broken
guarantees and three smaller problems, all fixed, each with a test that
failed before the fix: the language model sees only the Candidates that
fit its budget while the report claimed both rerankers saw the same ones,
so a `limit` above `max_candidates` is now refused and partial rankings
are listed; three reports in a different case order rendered silently
with one case's ranks under another, so the renderer now checks them; a
cross-encoder failing on a later case left earlier cases billed with no
report, so every case is now cross-encoded first; the device sat above the
line that two runs compare, so it moved into "## Latency and cost"; and
the script read its settings outside `try`, so a bad setting skipped
closing the database.

The finishing Spec review found every spec item met and no gap. The
finishing Standards review found no blocking violation. Non-blocking
points from both:

- The strategy report drops the per-case and per-slice "Degraded" counts
  the older reports show, so a run where a search branch failed would not
  say so.
- `_check_strategies` does not compare the embedding identity or each
  case's direction and class across the three reports.
- A bad API key makes every case fall back and the report is still
  written; only the Fallbacks section shows it.
- A bad setting ends the script with a pydantic traceback, as in
  `--rerank` and `--llm-rerank`; `_run_llm_rerank` still reads its
  settings outside `try`.
- The docstring says "Raises like `compare_llm_reranking`" instead of
  naming `DatasetRunRefused` and `CaseFailed`.
- The highest-risk tests need PostgreSQL; they are deterministic, with
  fake models, but not database-free.

## Decision

Runs on 2026-11-11, on fresh databases, with the local BGE-M3 server on
port 8766 (8765 was taken), the reranker server first with
`RERANKER_DEVICE=cpu` and then with `RERANKER_DEVICE=mps`, and
`gpt-5.4-mini-2026-03-17` with reasoning effort `none`. The two runs were
identical above "## Latency and cost".

| Slice | Cases | none R@1 | none MRR | cross-encoder R@1 | cross-encoder MRR | LLM R@1 | LLM MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| all | 16 | 0.938 | 0.969 | 1.000 | 1.000 | 1.000 | 1.000 |
| en->en | 6 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| en->uk | 2 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| uk->en | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| uk->uk | 5 | 0.800 | 0.900 | 1.000 | 1.000 | 1.000 | 1.000 |
| plain | 9 | 0.889 | 0.944 | 1.000 | 1.000 | 1.000 | 1.000 |
| identifier | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| morphology | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |

Moves, both runs: the cross-encoder and the language model each moved
`uk-uk-sick-first-day` up from rank 2 to rank 1, moved none down, and left
15 cases unchanged. The rerankers ranked differently in 0 of 16 cases.
Fallbacks: none. Partial rankings: none; all ten Candidates of every case
fit the language model's budget.

| Step | `cpu` run median / slowest ms | `mps` run median / slowest ms | Requests per run | USD per case |
|---|---:|---:|---:|---:|
| retrieval | 112.7 / 169.0 | 121.3 / 277.0 | 16 | 0 |
| cross-encoder | 736.8 / 970.4 | 325.7 / 1514.6 | 16 | 0 |
| LLM | 1583.6 / 3319.7 | 1504.0 / 5131.2 | 16 | 0.001384 / 0.001395 |

LLM tokens: 22935 in and 1098 out on the `cpu` run, 0.022142 USD; 22985 in
and 1129 out on the `mps` run, 0.022319 USD.

Trade-offs on this dataset: both rerankers buy the same gain, MRR 0.969 to
1.000 through one case of 16. The cross-encoder adds about 6.5 times
retrieval's median on `cpu` (736.8 against 112.7 ms) and about 2.7 times
on `mps` (325.7 against 121.3 ms), for no bill. The language model adds
about 13 times retrieval's median (1504.0 to 1583.6 ms), a slowest case of
3.3 to 5.1 s, and about 0.0014 USD per question.

**Decision: no environment reranks by default.**

| Environment | Default | Reason |
|---|---|---|
| Test suite | no reranker | Tests must stay deterministic and offline; reranking is tested with `FakeReranker` and `FakeRankingModel`, never a real model. |
| Local development | no reranker | Searches stay fast with one server; the cross-encoder on `mps` stays opt-in through `--rerank` and `--compare-rerankers` for evaluation. |
| Production | no reranker | One case of 16 gained is not worth 2.7 to 13 times retrieval's time, and the language model adds a bill and a long tail for the same gain as the free cross-encoder. |

What would change the production default: on a dataset of at least 200
verified cases, the cross-encoder raising MRR over fused retrieval by at
least 0.05 with no slice getting worse, at a median under 300 ms on the
production device. The language model would need a further MRR gain of at
least 0.02 over the cross-encoder, at a median under 1 s and under
0.002 USD per question, to be worth its bill.

Falsified assumption: I assumed retrieval's time does not depend on the
reranker, because retrieval finishes before any reranker starts. The
report shows retrieval's slowest case at 169.0 ms in the `cpu` run and
277.0 ms in the `mps` run, and its median at 112.7 against 121.3 ms, with
the same data and the same search. So "retrieval's timing is the same in
every run" is wrong: one run's slowest time moves by about 100 ms for
reasons outside the code under test, and only a median over many runs
could separate a device effect from noise.

Deliberate break: with each case sent to the language model right after
the cross-encoder, `uv run pytest
tests/test_reranking_strategies_integration.py -m integration -q` reported
`2 failed, 2 passed`: `test_a_failing_cross_encoder_stops_before_the_language_model_is_asked`
and `test_both_rerankers_get_the_same_eligible_candidates_of_every_case`.
After undoing the change, it reported `4 passed`.

## Reflection

Claude implemented Steps 1–8 at the learner's request and drafted the
predictions, the decision, and this reflection; they are not blind,
because Claude had seen one prototype run on `cpu`. The learner should
rewrite them in their own words.

Prediction check:

| Prediction | Measured | Held? |
|---|---|---|
| MRR 0.969 / R@1 0.938 without reranking; 1.000 / 1.000 for both rerankers | exactly that, in both runs | yes |
| The rerankers disagree on no case | 0 of 16 | yes |
| Cross-encoder about 750 ms on `cpu`, 300 ms on `mps`; LLM about 1,500 ms | 736.8, 325.7, 1504.0–1583.6 ms | yes |
| About 0.022 USD per run, 0.0014 per case | 0.022142 and 0.022319; 0.001384 and 0.001395 | yes |
| No reranker in every environment | chosen | yes |

1. What does `compare_reranking_strategies` hide from its callers? That
   each case is searched once and the same list reaches both rerankers;
   that every case is cross-encoded before any is sent to the language
   model; the budget check on `limit`; the identity checks; the fallbacks
   and partial rankings; the cost sum; and the typed errors that never
   carry a question. A caller passes a dataset, the models, a budget, and
   a price, and gets three aligned reports with what each step cost.
2. A later change sends each case to both rerankers at once, to be
   faster. That breaks "a failing cross-encoder stops the run before
   anything is billed" first: the language model would already be paid
   for the cases before the failing one.
   `test_a_failing_cross_encoder_stops_before_the_language_model_is_asked`
   catches it, as the deliberate break showed for the similar change.
3. Both rerankers fixed the same case. To see a real difference, add
   questions whose answer needs two passages compared, such as "which
   rule is newer, the 2024 travel policy or the 2025 update?", or
   questions where two Chunks share the words but only one answers. A
   cross-encoder scores each passage alone and may rank both high; a
   language model reads them together and could put the one that answers
   first.
4. Which prediction was wrong? None of the written predictions failed,
   but the belief that retrieval's time is the same in every run did:
   its slowest case moved from 169.0 to 277.0 ms with the same data and
   search, so one run's slowest time says nothing about a device.
5. Before changing the production default I would check: MRR over fused
   retrieval on at least 200 verified cases, per slice, with no slice
   worse; a median reranking time under 300 ms on the production device
   (1 s and 0.002 USD per question for the language model); and a run
   where the reranker server is down, to prove users still get
   retrieval's order within that time.
