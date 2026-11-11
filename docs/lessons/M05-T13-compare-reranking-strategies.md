# M05-T13 — Compare reranking strategies

Source task: [M05-T13](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T13-compare-reranking-strategies.md)

## What you will do

You now have two rerankers: the local cross-encoder from M05-T11 and the language model from M05-T12. Each was measured in its own run, so their numbers came from different runs. In this lesson one run searches every question once and gives the same Candidates to both rerankers. The report then puts three strategies side by side: no reranker, the cross-encoder, and the language model. It shows quality, time, and money for each. At the end you decide which strategy each environment should use by default, or that none should rerank yet.

- `compare_reranking_strategies` searches each case once, sends the same Candidates to the cross-encoder, and then, once every case is done, to the language model.
- `render_strategy_comparison` writes one report with three columns per table, and refuses three reports that do not line up case by case.
- The script gets a `--compare-rerankers` option that needs both local servers and the OpenAI key.
- You run it, read the trade-offs, and write a decision per environment.

| The task asks for | Where it happens |
|---|---|
| No reranker, cross-encoder, and LLM on the same bilingual cases | Steps 2 and 6: one search per case, the same Candidates in the same order to both rerankers |
| Quality, latency, and cost trade-offs | Steps 3 and 7: Recall@1 and MRR per slice, moves per case, time and USD per case |
| Deterministic tests: success, highest-risk edge, typed failure | Steps 4 and 6: the same Candidates reach both; a failing cross-encoder stops before anything is billed |
| A decision: defaults per environment, or reject reranking | Step 7 |
| Authorization before data leaves storage | Step 6: restricted text never reaches either reranker |
| No seeded sensitive text in telemetry | Step 6: the report and the logs hold no question and no restricted text |

This lesson includes coding: Steps 2 to 6 write code. Steps 1, 7, and 8 do not involve code. Step 7 calls OpenAI with your API key; one full run costs about 2 US cents.

Terms used in this lesson:

- **Strategy:** one way to order a question's Candidates. Here there are three: keep retrieval's order, ask the cross-encoder, or ask the language model.
- **Recall@1:** the share of questions whose relevant Chunk is first. Every strategy here only reorders the same ten Candidates, so Recall@10 cannot change; Recall@1 and MRR are what can.
- **Trade-off:** what you pay for a gain. A strategy that adds 0.03 to MRR but 1.5 seconds and a bill per question may not be worth it.
- **Default per environment:** the setting each place uses when nobody chooses: the test suite, a developer's laptop, and production.
- **Partial ranking:** the language model orders only the Candidates that fit its budget; the rest keep retrieval's order below them.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **Why one run, not two reports.** In M05-T11 and M05-T12 each reranker had its own run, with its own search. The searches gave the same Candidates, but nothing proved it. Here each case is searched once, and that one list goes to both rerankers. So any difference between the strategies comes from the reranker alone.
- **Same Candidates, same order.** Both rerankers get the Candidates in retrieval's order. Order matters for a language model: it may favour what it reads first. If it got the cross-encoder's order instead, it would start with an advantage.
- **Cross-encoder first, for every case.** The run cross-encodes all cases before it sends any case to the language model. The cross-encoder is all or nothing, as in M05-T11, so if its server fails on case 5, the run stops. Done in this order, the stopped run has billed nothing.
- **The language model can see fewer Candidates.** `rerank_with_llm` sends only the head of the list that fits its budget. So the run refuses a `limit` above the budget's `max_candidates`, and it lists every case where the token limit cut the list short as partial. The report never claims both rerankers ordered the same number of Candidates when they did not.
- **Two names, two columns.** The report tells the strategies apart by the reranker's name. Two rerankers with the same identity are refused before any work.
- **A report that cannot mix cases.** The renderer zips three reports row by row. If one report held its cases in another order, case `a` would show case `b`'s ranks. So the renderer checks that the three reports come from one run, hold the same cases in the same order, and are, in order, no reranker and two different rerankers. Otherwise it raises `ValueError`.
- **What the report shows.** Quality by slice as Recall@1 and MRR, each case's ranks under each strategy, the moves against no reranker, where the two rerankers disagree, the language model's fallbacks and partial rankings, and last, "## Latency and cost": median and slowest time, requests, and USD per case for each step. Only that last section changes from run to run, so two runs can be compared with `diff` above it, apart from what the language model chooses.
- **Where the code lives.** In `retrieval/evaluation_dataset.py`, next to the two comparisons it combines. Two small helpers, `_move` and `_times`, are shared with the older reports, which print exactly what they printed before.
- **What leaves the machine.** The same as in M05-T12: only the question and the text of Candidates that passed the permission check, sent to OpenAI with `store=False`. The cross-encoder runs on your machine.
- **Known limits.** 16 cases are few, and fused retrieval already ranks 15 of them first; a language model may order differently on another run; the price is a setting; the device of the cross-encoder changes its time a lot, so the decision names the device.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before any code exists, write down what must be true when the lesson is done and what you expect the three strategies to show. Step 7 checks these predictions against the report.

Create `docs/evidence/M05-T13-reranking-strategies.md` with this skeleton, then replace the prediction prompts with your own sentences:

```markdown
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

- MRR and Recall@1 of the three strategies on all 16 cases: ...
- Cases where the cross-encoder and the language model rank differently: ...
- Median time per case added by the cross-encoder on `cpu`, on `mps`, and by the language model: ...
- Cost in USD of one run, and per case: ...
- The default I expect to choose for tests, for local development, and for production, and why: ...
- The one number that would make me choose reranking in production: ...

## Design

## Verification

## Decision

## Reflection
```

Check: the file exists, and every prediction is a full sentence that a number in Step 7 can prove wrong.

### Step 2 — Coding step: run three strategies on the same Candidates

This step adds the comparison itself: one search per case, then both rerankers on that same list.

**In plain words:** The comparison ingests and embeds the corpus once, as the two earlier comparisons do. For each question it runs fused search once, keeps the list, and asks the cross-encoder to reorder it. When every question is done, it gives each kept list, unchanged, to the language model. It returns three reports with the same cases, plus what each reranker cost.

| Change | What it does |
|---|---|
| module docstring | one bullet for the new comparison |
| `StrategyComparison` | the three reports, the cross-encoder's device and requests, the language model's fallbacks, usage, cost, and partial rankings |
| `compare_reranking_strategies` | refuses a bad `limit`, a `limit` above the budget's `max_candidates`, and two rerankers with one name; then search and cross-encode every case, then rank every case |
| `partial` | `(case, ordered, given)` for each case where the language model did not fall back but ordered fewer Candidates than retrieval returned |

In `src/knowledge_service/retrieval/evaluation_dataset.py`, make these changes in order:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
  billed tokens, and the estimated cost.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
```

Replace with:

```python
  billed tokens, and the estimated cost.
- **Compared across strategies.** `compare_reranking_strategies` searches
  every case once, then gives the same Candidates to a cross-encoder and
  to a RankingModel, so the strategies differ only in their order. Every
  case is cross-encoded before any case is sent to the language model,
  so a failing cross-encoder stops the run before anything is billed.
  `render_strategy_comparison` puts no reranker, the cross-encoder, and
  the language model side by side: quality, moves, fallbacks, latency,
  and cost.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    cost_usd: Decimal | None
```

Replace with:

```python
    cost_usd: Decimal | None


@dataclass(frozen=True, slots=True)
class StrategyComparison:
    """No reranker, a cross-encoder, and a language model on the same Candidates.

    `retrieved`, `cross_encoder`, and `llm` hold the same cases in the same
    order; a reranked outcome's `elapsed_seconds` is that reranker's time
    alone. `device` and `requests` describe the cross-encoder, as in
    `RerankComparison`; `fallbacks`, `usages`, and `cost_usd` describe the
    language model, as in `LLMRerankComparison`. `partial` lists, per case
    the language model ranked without falling back, how many Candidates
    fit its budget out of how many retrieval returned, when not all did.
    """

    retrieved: DatasetReport
    cross_encoder: DatasetReport
    llm: DatasetReport
    device: str
    requests: int
    fallbacks: tuple[tuple[str, str], ...]
    usages: tuple[Usage, ...]
    cost_usd: Decimal | None
    partial: tuple[tuple[str, int, int], ...] = ()
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
        cost_usd=_cost(usages, price),
    )
```

Replace with:

```python
        cost_usd=_cost(usages, price),
    )


async def compare_reranking_strategies(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: EmbeddingModel,
    reranker: Reranker,
    device: str,
    ranker: RankingModel,
    budget: RankingBudget,
    price: ModelPrice | None,
    limit: int = DEFAULT_LIMIT,
) -> StrategyComparison:
    """Search every case once, then rerank the same Candidates two ways.

    Each case runs dense and lexical search fused, as `compare_reranking`
    does. Its Candidates, and nothing else, go to `reranker`, and once
    every case is reranked, to `ranker` within `budget`, so the three
    strategies differ only in their order. A cross-encoder failure stops
    the run with `CaseFailed` before any case reaches the language model;
    a failed ranking keeps retrieval's order and is listed in `fallbacks`.
    Raises like `compare_llm_reranking`; `DatasetRunRefused` also when
    both rerankers name the same identity, or when `limit` is above the
    number of Candidates `budget` lets the language model see.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    if limit > budget.max_candidates:
        raise DatasetRunRefused("limit must not exceed the budget's max_candidates")
    identity = _identity(model)
    cross_identity = _reranker_identity(reranker)
    llm_identity = _reranker_identity(ranker)
    # The report tells the strategies apart by these names.
    if cross_identity == llm_identity:
        raise DatasetRunRefused("the two rerankers must have different identities")
    await verify_corpus(dataset, corpus)
    await _ingest(runtime, dataset, corpus, observed_at, embedding=identity)
    await _embed_corpus(runtime, model, observed_at)
    searches: Mapping[RetrievalBranch, BranchSearch] = {
        RetrievalBranch.DENSE: dense_branch(runtime, model),
        RetrievalBranch.LEXICAL: lexical_branch(runtime),
    }
    results: list[HybridRetrieval] = []
    retrieved: list[CaseOutcome] = []
    cross: list[CaseOutcome] = []
    requests = 0
    for case in dataset.cases:
        result, elapsed = await _search_case(case, searches, limit)
        results.append(result)
        retrieved.append(_outcome(case, result.candidates, result.degraded, elapsed))
        started = time.perf_counter()
        scored = await _rerank_case(case, reranker, result, cross_identity)
        took = time.perf_counter() - started
        requests += scored.requests
        cross.append(_outcome(case, scored.candidates, result.degraded, took))
    # Only now, so a cross-encoder failure leaves nothing billed.
    llm: list[CaseOutcome] = []
    fallbacks: list[tuple[str, str]] = []
    usages: list[Usage] = []
    partial: list[tuple[str, int, int]] = []
    for case, result in zip(dataset.cases, results, strict=True):
        started = time.perf_counter()
        ranked = await _llm_rerank_case(case, ranker, result, budget, llm_identity)
        took = time.perf_counter() - started
        given = len(result.candidates)
        if ranked.fallback is not None:
            fallbacks.append((case.case_id, ranked.fallback))
        elif ranked.ranked < given:
            partial.append((case.case_id, ranked.ranked, given))
        if ranked.usage is not None:
            usages.append(ranked.usage)
        llm.append(_outcome(case, ranked.candidates, result.degraded, took))
    branches = tuple(branch for branch in RetrievalBranch if branch in searches)

    def report(
        outcomes: list[CaseOutcome], by: RerankerIdentity | None
    ) -> DatasetReport:
        return DatasetReport(
            name=dataset.name,
            version=dataset.version,
            limit=limit,
            branches=branches,
            outcomes=tuple(outcomes),
            embedding=identity,
            reranker=by,
        )

    return StrategyComparison(
        retrieved=report(retrieved, None),
        cross_encoder=report(cross, cross_identity),
        llm=report(llm, llm_identity),
        device=device,
        requests=requests,
        fallbacks=tuple(fallbacks),
        usages=tuple(usages),
        cost_usd=_cost(usages, price),
        partial=tuple(partial),
    )
```

The function reuses `_search_case`, `_rerank_case`, and `_llm_rerank_case` from the earlier lessons, so every error stays typed and names only the case and the error type. The first loop keeps each `HybridRetrieval` result, so the second loop sends exactly the list the cross-encoder saw.

Check:

```bash
uv run ruff format src/knowledge_service/retrieval/evaluation_dataset.py
uv run ruff check src/knowledge_service/retrieval/evaluation_dataset.py
uv run pyright src/knowledge_service/retrieval/evaluation_dataset.py
```

Expected: `All checks passed!` and `0 errors`.

### Step 3 — Coding step: the three-way report

This step writes the report that puts the three strategies side by side.

**In plain words:** The report first checks that the three result sets describe the same questions in the same order. Then it prints quality per language direction and question type, each question's ranks under each strategy, how many questions moved up or down, and where the two rerankers disagree. It lists the language model's fallbacks and partial rankings. Last comes the only part that changes between runs: time, requests, and money per question for each step.

| Change | What it does |
|---|---|
| `render_strategy_comparison` | the header, then each section in order; Recall@1 and MRR, because Recall@10 cannot change |
| `_check_strategies` | `ValueError` unless the reports come from one run, hold the same cases in order, and are no reranker, then two different rerankers |
| `_strategy_quality` | Recall@1 and MRR per slice, two columns per strategy |
| `_strategy_cases` | ranks per case, moves against no reranker, and the cases where the rerankers differ |
| `_strategy_costs` | "## Latency and cost": the device, then median, slowest, requests, and USD per case per step, then tokens and total cost |
| `_move`, `_times` | one move and one timing rule, now shared with `_delta_lines` and `_latency_lines`; their output is unchanged |

In `src/knowledge_service/retrieval/evaluation_dataset.py`, make these changes in order:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python

def _delta_lines(before: DatasetReport, after: DatasetReport) -> list[str]:
```

Replace with:

```python

def render_strategy_comparison(comparison: StrategyComparison) -> str:
    """Return no reranker, the cross-encoder, and the language model side by side.

    Every strategy only reorders the same Candidates, so Recall at the
    run's limit cannot change; quality is shown as Recall@1 and MRR.
    Everything above "## Latency and cost" depends only on the data and the
    models, although the language model may order differently between
    runs and a timeout fallback depends on the run; that last section
    changes from run to run. Raises `ValueError` when the three reports do
    not describe the same cases in the same order, with no reranker, the
    cross-encoder, and the language model in that order.
    """
    _check_strategies(comparison)
    retrieved = comparison.retrieved
    runs = (
        ("none", retrieved),
        ("cross-encoder", comparison.cross_encoder),
        ("LLM", comparison.llm),
    )
    lines = [
        f"# Reranking strategies: {retrieved.name} v{retrieved.version}",
        "",
        *_configuration((retrieved,)),
        f"- Cross-encoder: {_reranker_name(comparison.cross_encoder)}.",
        f"- Language model: {_reranker_name(comparison.llm)}.",
        "- Both rerankers get the same Candidates of each case; the language"
        " model orders those that fit its budget.",
        "",
        *_strategy_quality(runs),
        "",
        *_strategy_cases(runs),
        "",
        "## Fallbacks",
        "",
        "Cases where the language model's ranking was not used, and why.",
        "",
    ]
    if comparison.fallbacks:
        lines += ["| Case | Reason |", "|---|---|"]
        lines += [f"| {case} | {reason} |" for case, reason in comparison.fallbacks]
    else:
        lines.append("- None.")
    lines += [
        "",
        "Cases where only some Candidates fit the language model's budget:",
        "",
    ]
    lines += [
        f"- {case}: {ranked} of {given} ordered; the rest kept retrieval's order."
        for case, ranked, given in comparison.partial
    ] or ["- None."]
    return "\n".join([*lines, "", *_strategy_costs(comparison)]) + "\n"


def _check_strategies(comparison: StrategyComparison) -> None:
    """Refuse three reports that do not line up case by case."""
    runs = (comparison.retrieved, comparison.cross_encoder, comparison.llm)
    first = runs[0]
    for report in runs:
        same = (report.name, report.version, report.limit, report.branches)
        if same != (first.name, first.version, first.limit, first.branches):
            raise ValueError("the three reports must come from one run")
        cases = [o.case_id for o in report.outcomes]
        if cases != [o.case_id for o in first.outcomes]:
            raise ValueError("the three reports must hold the same cases in order")
    cross, llm = comparison.cross_encoder.reranker, comparison.llm.reranker
    if first.reranker is not None or cross is None or llm is None or cross == llm:
        raise ValueError(
            "expected no reranker, then two different rerankers, in that order"
        )


def _strategy_quality(runs: tuple[tuple[str, DatasetReport], ...]) -> list[str]:
    """Recall@1 and MRR per slice, one pair of columns per strategy."""
    lines = [
        "## Quality by slice",
        "",
        "| Slice | Cases | "
        + " | ".join(f"{name} R@1 | {name} MRR" for name, _ in runs)
        + " |",
        "|---|---:|" + "---:|---:|" * len(runs),
    ]
    scored = [score_slices(report) for _, report in runs]
    for row in zip(*scored, strict=True):
        cells = [f"{row[0].name} | {row[0].cases}"]
        cells += [
            f"{_number(dict(score.recall).get(1))} | {_number(score.mrr)}"
            for score in row
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _strategy_cases(runs: tuple[tuple[str, DatasetReport], ...]) -> list[str]:
    """Each case's ranks per strategy, the moves, and where rerankers differ."""
    lines = [
        "## By case",
        "",
        "Where each case's relevant Chunks ranked under each strategy.",
        "",
        "| Case | Direction | Class | " + " | ".join(n for n, _ in runs) + " |",
        "|---|---|---|" + "---|" * len(runs),
    ]
    rows = list(zip(*(report.outcomes for _, report in runs), strict=True))
    for row in rows:
        first = row[0]
        lines.append(
            f"| {first.case_id} | {first.direction} | {first.query_class} | "
            + " | ".join(_ranks(outcome.ranks) for outcome in row)
            + " |"
        )
    lines.append("")
    for index, (name, _) in enumerate(runs[1:], start=1):
        moves = Counter(_move(row[0], row[index]) for row in rows)
        lines.append(
            f"- {name} against none: up {moves['up']}, down {moves['down']},"
            f" same {moves['same']}."
        )
    differ = [row[0].case_id for row in rows if len({o.ranks for o in row[1:]}) > 1]
    lines.append(
        f"- The rerankers rank differently in {len(differ)} of {len(rows)} cases"
        + (": " + ", ".join(differ) + "." if differ else ".")
    )
    return lines


def _strategy_costs(comparison: StrategyComparison) -> list[str]:
    """What each step adds per case in time and money."""
    cases = len(comparison.retrieved.outcomes)
    usages, cost = comparison.usages, comparison.cost_usd
    per_case = "unknown" if cost is None else f"{cost / max(cases, 1):.6f}"
    lines = [
        "## Latency and cost",
        "",
        "Per case. Times and tokens change from run to run.",
        "",
        f"- Cross-encoder device: {comparison.device}.",
        "",
        "| Step | Median ms | Slowest ms | Requests | USD per case |",
        "|---|---:|---:|---:|---:|",
    ]
    steps = (
        ("retrieval", comparison.retrieved, cases, "0"),
        ("cross-encoder", comparison.cross_encoder, comparison.requests, "0"),
        ("LLM", comparison.llm, len(usages), per_case),
    )
    for name, report, requests, usd in steps:
        median, slowest = _times(report)
        lines.append(f"| {name} | {median:.1f} | {slowest:.1f} | {requests} | {usd} |")
    lines += [
        "",
        "- The cross-encoder runs on a local server; it has no bill per request.",
        f"- LLM tokens: {sum(u.input_tokens for u in usages)} in,"
        f" {sum(u.output_tokens for u in usages)} out; estimated cost: "
        + ("unknown" if cost is None else f"{cost:.6f} USD")
        + ", at the configured list price.",
    ]
    return lines


def _reranker_name(report: DatasetReport) -> str:
    reranker = report.reranker
    return "none" if reranker is None else f"{reranker.provider} {reranker.model}"


def _move(old: CaseOutcome, new: CaseOutcome) -> str:
    """Whether a case's best relevant rank got better, worse, or stayed."""
    change = reciprocal_rank(new.ranks) - reciprocal_rank(old.ranks)
    return "up" if change > 0 else "down" if change < 0 else "same"


def _times(report: DatasetReport) -> tuple[float, float]:
    """The median and slowest wall time per case, in milliseconds."""
    times = [outcome.elapsed_seconds * 1000 for outcome in report.outcomes]
    return statistics.median(times) if times else 0.0, max(times, default=0.0)


def _delta_lines(before: DatasetReport, after: DatasetReport) -> list[str]:
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    for old, new in zip(before.outcomes, after.outcomes, strict=True):
        change = reciprocal_rank(new.ranks) - reciprocal_rank(old.ranks)
        move = "up" if change > 0 else "down" if change < 0 else "same"
        moves[move] += 1
```

Replace with:

```python
    for old, new in zip(before.outcomes, after.outcomes, strict=True):
        move = _move(old, new)
        moves[move] += 1
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    for name, report in (("retrieval", before), ("reranking", after)):
        times = [outcome.elapsed_seconds * 1000 for outcome in report.outcomes]
        median = statistics.median(times) if times else 0.0
        slowest = max(times, default=0.0)
        lines.append(f"| {name} | {median:.1f} | {slowest:.1f} |")
```

Replace with:

```python
    for name, report in (("retrieval", before), ("reranking", after)):
        median, slowest = _times(report)
        lines.append(f"| {name} | {median:.1f} | {slowest:.1f} |")
```

The device sits in the last section, as in the M05-T11 report: a run on `mps` and a run on `cpu` then differ only below "## Latency and cost".

Check: the same three commands as in Step 2, then the existing report tests, which prove the older reports did not change:

```bash
uv run pytest tests/test_evaluation_dataset.py tests/test_reranking.py tests/test_llm_reranking.py -q
```

Expected: every test passes.

### Step 4 — Coding step: unit tests for the settings and the report

This step tests what needs no database: the refused settings and the report.

**In plain words:** Three wrong settings must be refused before the comparison touches the database. A small, hand-made set of three questions then checks every line of the report: the quality numbers, the moves, the disagreement, the fallback, the partial ranking, and the time and cost. Four broken result sets check that the report refuses to mix questions up.

| Test | What it proves |
|---|---|
| `test_a_wrong_strategy_setting_is_refused_before_any_work` | `limit` 0, `limit` 21 with a budget of 20, and two rerankers with one name raise `DatasetRunRefused` before any database work |
| `test_the_report_puts_three_strategies_side_by_side` | exact Recall@1 and MRR, ranks per case, moves, the disagreement, a fallback, and a partial ranking |
| `test_the_last_section_shows_what_each_step_adds_per_case` | nothing above "## Latency and cost" holds a time or the device; the times, requests, and USD per case are exact |
| `test_an_unknown_price_is_reported_as_unknown` | no price gives "unknown", not a made-up 0 |
| `test_reports_that_do_not_line_up_are_refused` | another case order, a short report, a reranked report in the place of none, and another limit all raise `ValueError` |

The numbers are small enough to check by hand. Case `a` ranks 2 with no reranker, so its reciprocal rank is 1/2; `b` ranks 1; `c` is missed. MRR is (1/2 + 1 + 0) / 3 = 0.500. The language model puts `b` at 3, so its MRR is (1 + 1/3 + 0) / 3 = 0.444.

Create `tests/test_reranking_strategies.py`:

```python
"""The three-way reranking comparison: refused settings and the report."""

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import SecretStr

from fakes import FakeEmbeddingModel, FakeRankingModel, FakeReranker
from knowledge_service.contracts import Usage
from knowledge_service.database import create_database_runtime
from knowledge_service.embeddings.port import EmbeddingModelIdentity
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    CaseOutcome,
    DatasetReport,
    DatasetRunRefused,
    QueryClass,
    StrategyComparison,
    compare_reranking_strategies,
    load_dataset,
    render_strategy_comparison,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.llm_reranking import RankingBudget, RankingModel
from knowledge_service.retrieval.reranking import RerankerIdentity
from knowledge_service.settings import Settings

CROSS = RerankerIdentity(provider="local", model="cross@1")
LLM = RerankerIdentity(provider="openai", model="ranker-1")


class SameNameRanker(FakeRankingModel):
    """A ranking model that names itself like `FakeReranker`."""

    @property
    def identity(self) -> RerankerIdentity:
        return RerankerIdentity(provider="fake", model="fake-reranker")


@pytest.mark.parametrize(
    ("limit", "ranker", "message"),
    [
        (0, FakeRankingModel(), "limit"),
        (21, FakeRankingModel(), "max_candidates"),
        (10, SameNameRanker(), "different identities"),
    ],
)
async def test_a_wrong_strategy_setting_is_refused_before_any_work(
    limit: int, ranker: RankingModel, message: str
) -> None:
    # The runtime connects lazily, so nothing is reached at this address.
    runtime = create_database_runtime(
        Settings(database_url=SecretStr("postgresql+psycopg://u:p@localhost:1/x"))
    )
    dataset = load_dataset(DATASET_DIRECTORY / CASES_FILE)

    with pytest.raises(DatasetRunRefused, match=message):
        await compare_reranking_strategies(
            runtime,
            dataset,
            DATASET_DIRECTORY / CORPUS_DIRECTORY,
            observed_at=datetime(2026, 11, 11, tzinfo=UTC),
            model=FakeEmbeddingModel(),
            reranker=FakeReranker(),
            device="cpu",
            ranker=ranker,
            budget=RankingBudget(max_candidates=20, max_input_tokens=8_000),
            price=None,
            limit=limit,
        )
    await runtime.dispose()


def report(
    ranks: list[tuple[int | None, ...]],
    seconds: list[float],
    reranker: RerankerIdentity | None,
) -> DatasetReport:
    cases = [
        ("a", "en->en", QueryClass.PLAIN),
        ("b", "uk->uk", QueryClass.MORPHOLOGY),
        ("c", "uk->en", QueryClass.IDENTIFIER),
    ]
    return DatasetReport(
        name="bilingual-seed",
        version=1,
        limit=10,
        branches=(RetrievalBranch.DENSE, RetrievalBranch.LEXICAL),
        outcomes=tuple(
            CaseOutcome(case, direction, query_class, rank, False, elapsed)
            for (case, direction, query_class), rank, elapsed in zip(
                cases, ranks, seconds, strict=True
            )
        ),
        embedding=EmbeddingModelIdentity(
            provider="local", model="bge-m3@1", dimensions=1024
        ),
        reranker=reranker,
    )


def usage(tokens_in: int, tokens_out: int) -> Usage:
    return Usage(
        provider="openai",
        model="ranker-1",
        price_id="ranker-1",
        latency_ms=1,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        total_tokens=tokens_in + tokens_out,
    )


COMPARISON = StrategyComparison(
    retrieved=report([(2,), (1,), (None,)], [0.1, 0.2, 0.3], None),
    cross_encoder=report([(1,), (1,), (None,)], [0.5, 0.5, 0.5], CROSS),
    llm=report([(1,), (3,), (None,)], [1.0, 2.0, 3.0], LLM),
    device="mps",
    requests=3,
    fallbacks=(("c", "RerankerTimeout"),),
    usages=(usage(100, 10), usage(200, 20)),
    cost_usd=Decimal("0.000300"),
    partial=(("a", 4, 10),),
)


def test_the_report_puts_three_strategies_side_by_side() -> None:
    rendered = render_strategy_comparison(COMPARISON)

    assert rendered.startswith("# Reranking strategies: bilingual-seed v1\n")
    assert "- Cross-encoder: local cross@1." in rendered
    assert "- Language model: openai ranker-1." in rendered
    # Recall@1 and MRR: Recall@10 cannot change when only the order does.
    assert (
        "| Slice | Cases | none R@1 | none MRR | cross-encoder R@1"
        " | cross-encoder MRR | LLM R@1 | LLM MRR |"
    ) in rendered
    assert "| all | 3 | 0.333 | 0.500 | 0.667 | 0.667 | 0.333 | 0.444 |" in rendered
    assert "| b | uk->uk | morphology | 1 | 1 | 3 |" in rendered
    assert "| c | uk->en | identifier | missed | missed | missed |" in rendered
    assert "- cross-encoder against none: up 1, down 0, same 2." in rendered
    assert "- LLM against none: up 1, down 1, same 1." in rendered
    assert "- The rerankers rank differently in 1 of 3 cases: b." in rendered
    assert "| c | RerankerTimeout |" in rendered
    assert "- a: 4 of 10 ordered; the rest kept retrieval's order." in rendered


def test_the_last_section_shows_what_each_step_adds_per_case() -> None:
    rendered = render_strategy_comparison(COMPARISON)
    # Only this section changes from run to run, so it comes last.
    stable, varying = rendered.split("## Latency and cost\n")

    assert "ms" not in stable
    assert "mps" not in stable
    assert "- Cross-encoder device: mps." in varying
    assert "| retrieval | 200.0 | 300.0 | 3 | 0 |" in varying
    assert "| cross-encoder | 500.0 | 500.0 | 3 | 0 |" in varying
    assert "| LLM | 2000.0 | 3000.0 | 2 | 0.000100 |" in varying
    assert "- LLM tokens: 300 in, 30 out; estimated cost: 0.000300 USD" in varying


def test_an_unknown_price_is_reported_as_unknown() -> None:
    unpriced = dataclasses.replace(
        COMPARISON, fallbacks=(), usages=(), cost_usd=None, partial=()
    )

    rendered = render_strategy_comparison(unpriced)

    assert "| LLM | 2000.0 | 3000.0 | 0 | unknown |" in rendered
    assert "estimated cost: unknown" in rendered
    assert "## Fallbacks\n\nCases where the language model's ranking was not" in (
        rendered
    )
    assert "- None." in rendered


def swapped_cases(report: DatasetReport) -> DatasetReport:
    return dataclasses.replace(report, outcomes=report.outcomes[::-1])


@pytest.mark.parametrize(
    "comparison",
    [
        # The cross-encoder's rows in another case order.
        dataclasses.replace(
            COMPARISON, cross_encoder=swapped_cases(COMPARISON.cross_encoder)
        ),
        # One report too short.
        dataclasses.replace(
            COMPARISON,
            llm=dataclasses.replace(
                COMPARISON.llm, outcomes=COMPARISON.llm.outcomes[:2]
            ),
        ),
        # A reranked report in the place of no reranker.
        dataclasses.replace(COMPARISON, retrieved=COMPARISON.cross_encoder),
        # A report from a run with another limit.
        dataclasses.replace(
            COMPARISON, llm=dataclasses.replace(COMPARISON.llm, limit=5)
        ),
    ],
)
def test_reports_that_do_not_line_up_are_refused(
    comparison: StrategyComparison,
) -> None:
    with pytest.raises(ValueError, match=r"three reports|rerankers"):
        render_strategy_comparison(comparison)
```

Check:

```bash
uv run pytest tests/test_reranking_strategies.py -q
```

Expected: `10 passed`.

### Step 5 — Coding step: the `--compare-rerankers` option

This step lets you run the comparison from the command line.

**In plain words:** The new option reads the OpenAI key and the ranking settings, opens the local embedding server, the local reranker server, and the OpenAI client, and checks both servers before anything is written. It then runs the comparison, writes the report, and closes everything, whether the run worked or not.

| Change | What it does |
|---|---|
| docstring | what `--compare-rerankers` needs and where it writes |
| `STRATEGIES_REPORT` | `artifacts/evaluations/bilingual-seed-reranking-strategies.md` |
| `--compare-rerankers` | in the same group as the other model options, so only one can be chosen |
| API key check | `--llm-rerank` and `--compare-rerankers` both stop early without a key |
| `_run_strategies` | the settings are read inside `try`, all three clients close through `async with`, the database closes in `finally` |

In `scripts/run_retrieval_dataset.py`, make these changes in order:

Find in `scripts/run_retrieval_dataset.py`:

```python
`artifacts/evaluations/bilingual-seed-llm-rerank.md`.
"""
```

Replace with:

```python
`artifacts/evaluations/bilingual-seed-llm-rerank.md`.

`--compare-rerankers` runs the same fused search once per case and gives
its Candidates to both rerankers above, so it needs the BGE-M3 server, the
reranker server, and `KNOWLEDGE_SERVICE_MODEL_API_KEY`. It writes no
reranker, the cross-encoder, and the language model side by side, with
quality, moves, fallbacks, latency, and cost, to
`artifacts/evaluations/bilingual-seed-reranking-strategies.md`.
"""
```

Find in `scripts/run_retrieval_dataset.py`:

```python
    compare_reranking,
    compare_sparse,
```

Replace with:

```python
    compare_reranking,
    compare_reranking_strategies,
    compare_sparse,
```

Find in `scripts/run_retrieval_dataset.py`:

```python
    render_sparse_comparison,
    run_dataset,
```

Replace with:

```python
    render_sparse_comparison,
    render_strategy_comparison,
    run_dataset,
```

Find in `scripts/run_retrieval_dataset.py`:

```python
LLM_RERANK_REPORT = Path("artifacts/evaluations/bilingual-seed-llm-rerank.md")
SERVER_CHECK_SECONDS = 10.0
```

Replace with:

```python
LLM_RERANK_REPORT = Path("artifacts/evaluations/bilingual-seed-llm-rerank.md")
STRATEGIES_REPORT = Path("artifacts/evaluations/bilingual-seed-reranking-strategies.md")
SERVER_CHECK_SECONDS = 10.0
```

Find in `scripts/run_retrieval_dataset.py`:

```python
    embeddings.add_argument("--llm-rerank", action="store_true")
    arguments = parser.parse_args()
```

Replace with:

```python
    embeddings.add_argument("--llm-rerank", action="store_true")
    embeddings.add_argument("--compare-rerankers", action="store_true")
    arguments = parser.parse_args()
```

Find in `scripts/run_retrieval_dataset.py`:

```python
            return 1
        if arguments.llm_rerank and api_key is None:
            sys.stderr.write("set KNOWLEDGE_SERVICE_MODEL_API_KEY to rank with\n")
```

Replace with:

```python
            return 1
        ranks = arguments.llm_rerank or arguments.compare_rerankers
        if ranks and api_key is None:
            sys.stderr.write("set KNOWLEDGE_SERVICE_MODEL_API_KEY to rank with\n")
```

Find in `scripts/run_retrieval_dataset.py`:

```python
                _run_llm_rerank(runtime, dataset, corpus, arguments.limit, api_key)
            )
```

Replace with:

```python
                _run_llm_rerank(runtime, dataset, corpus, arguments.limit, api_key)
            )
        elif arguments.compare_rerankers and api_key is not None:
            default = STRATEGIES_REPORT
            rendered = asyncio.run(
                _run_strategies(runtime, dataset, corpus, arguments.limit, api_key)
            )
```

Find in `scripts/run_retrieval_dataset.py`:

```python

def _openai_model(api_key: SecretStr) -> ModelSource:
```

Replace with:

```python

async def _run_strategies(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    limit: int,
    api_key: SecretStr,
) -> str:
    try:
        settings = OpenAIRankingSettings()
        budget = RankingBudget(
            max_candidates=settings.max_candidates,
            max_input_tokens=settings.max_input_tokens,
        )
        async with (
            _local_model() as model,
            httpx.AsyncClient(trust_env=False) as client,
            AsyncOpenAI(api_key=api_key.get_secret_value()) as openai,
        ):
            reranker = LocalReranker(client, LocalRerankerSettings())
            # Before anything is written, so a stopped server leaves it empty.
            device = await reranker.verify_server(timeout_seconds=SERVER_CHECK_SECONDS)
            ranker = OpenAIRankingModel(openai, settings)
            comparison = await compare_reranking_strategies(
                runtime,
                dataset,
                corpus,
                observed_at=datetime.now(UTC),
                model=model,
                reranker=reranker,
                device=device,
                ranker=ranker,
                budget=budget,
                price=ranker.price,
                limit=limit,
            )
        return render_strategy_comparison(comparison)
    finally:
        await runtime.dispose()


def _openai_model(api_key: SecretStr) -> ModelSource:
```

Check:

```bash
uv run ruff format scripts/run_retrieval_dataset.py
uv run ruff check scripts/run_retrieval_dataset.py
uv run pyright scripts/run_retrieval_dataset.py
uv run python scripts/run_retrieval_dataset.py --compare-rerankers --llm-rerank
```

Expected: the first three pass. The last one ends with `argument --llm-rerank: not allowed with argument --compare-rerankers`.

### Step 6 — Coding step: test the comparison on PostgreSQL

This step runs the comparison on a real database, with fake models, and checks the guarantees that matter most.

**In plain words:** The seed corpus is stored, embedded with the fake embedding model, and searched as the reader. The tests then look at exactly what each reranker received. Both must get the same texts in the same order, and never a restricted one. A cross-encoder that fails on the second question must stop the run before the language model is asked anything. A failing language model must fall back on every question while the run goes on. A tight token budget must show up as partial rankings.

| Test | What it proves |
|---|---|
| `test_both_rerankers_get_the_same_eligible_candidates_of_every_case` | one request per case to each reranker, the same texts in the same order; no restricted text sent; the same Chunks found by all three strategies; no question or restricted text in the report or the logs |
| `test_a_failing_cross_encoder_stops_before_the_language_model_is_asked` | the cross-encoder scores case 1 and fails on case 2; `CaseFailed` names the case, and the language model got no call |
| `test_a_failing_language_model_falls_back_and_the_run_goes_on` | every case falls back with `RerankerUnavailable`, keeps retrieval's ranks, and bills nothing |
| `test_candidates_beyond_the_token_budget_are_reported_as_partial` | with room for only a few Candidates, each partial case lists how many were ordered, and the report says so |

The second test is the one that protects your money. If the code sent each case to the language model right after the cross-encoder, case 1 would already be billed when case 2 fails.

Create `tests/test_reranking_strategies_integration.py`:

```python
"""Three reranking strategies on real PostgreSQL: same Candidates, typed failure."""

import logging
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import SecretStr

from fakes import FakeEmbeddingModel, FakeRankingModel, FakeReranker
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import EmbeddingLimits
from knowledge_service.pricing import ModelPrice
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    CaseFailed,
    EvaluationDataset,
    compare_reranking_strategies,
    load_dataset,
    render_strategy_comparison,
)
from knowledge_service.retrieval.llm_reranking import RankingBudget
from knowledge_service.retrieval.reranking import RerankerUnavailable
from knowledge_service.settings import Settings

OBSERVED_AT = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
LIMITS = EmbeddingLimits(max_inputs=64, max_input_tokens=1_000, max_batch_tokens=20_000)
BUDGET = RankingBudget(max_candidates=20, max_input_tokens=20_000)
# Room for a few Candidates of the seed corpus, not all ten.
TIGHT_TOKENS = 300
PRICE = ModelPrice(
    price_id="fake-ranker",
    input_usd_per_million_tokens=Decimal("1"),
    output_usd_per_million_tokens=Decimal("2"),
)


class FailsOnSecondCase(FakeReranker):
    """Scores the first case, then fails as if its server stopped."""

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        if self.calls:
            raise RerankerUnavailable()
        return await super().score(
            query, passages, request_id=request_id, timeout_seconds=timeout_seconds
        )


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


@pytest.fixture
def dataset() -> EvaluationDataset:
    return load_dataset(DATASET_DIRECTORY / CASES_FILE)


@pytest.mark.integration
async def test_both_rerankers_get_the_same_eligible_candidates_of_every_case(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    reranker, ranker = FakeReranker(), FakeRankingModel()

    comparison = await compare_reranking_strategies(
        runtime,
        dataset,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        reranker=reranker,
        device="cpu",
        ranker=ranker,
        budget=BUDGET,
        price=PRICE,
    )

    # One request per case to each reranker, carrying the same texts in
    # retrieval's order: the strategies differ only in how they order them.
    assert comparison.requests == len(reranker.calls) == len(dataset.cases)
    assert len(ranker.calls) == len(dataset.cases)
    for scored, (_, passages) in zip(reranker.calls, ranker.calls, strict=True):
        assert list(scored) == [p.text for p in passages]
    sent = [text for texts in reranker.calls for text in texts]
    assert sent
    assert all(RESTRICTED_MARKER not in text for text in sent)
    # Reordering the same Candidates cannot find or lose a relevant Chunk.
    runs = (comparison.retrieved, comparison.cross_encoder, comparison.llm)
    assert (runs[1].reranker, runs[2].reranker) == (
        reranker.identity,
        ranker.identity,
    )
    for row in zip(*(run.outcomes for run in runs), strict=True):
        assert len({tuple(r is None for r in o.ranks) for o in row}) == 1
    assert (comparison.fallbacks, comparison.partial) == ((), ())
    assert len(comparison.usages) == len(dataset.cases)
    rendered = render_strategy_comparison(comparison)
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in dataset.cases)


@pytest.mark.integration
async def test_a_failing_cross_encoder_stops_before_the_language_model_is_asked(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    ranker = FakeRankingModel()

    with pytest.raises(CaseFailed, match=r"case \S+: reranking raised Reranker"):
        await compare_reranking_strategies(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            reranker=FailsOnSecondCase(),
            device="cpu",
            ranker=ranker,
            budget=BUDGET,
            price=PRICE,
        )

    # The first case was cross-encoded, but nothing was sent to the paid
    # model for a run that cannot finish.
    assert ranker.calls == []


@pytest.mark.integration
async def test_a_failing_language_model_falls_back_and_the_run_goes_on(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    reranker = FakeReranker()

    comparison = await compare_reranking_strategies(
        runtime,
        dataset,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        reranker=reranker,
        device="cpu",
        ranker=FakeRankingModel(error=RerankerUnavailable()),
        budget=BUDGET,
        price=PRICE,
    )

    assert len(reranker.calls) == len(dataset.cases)
    assert comparison.fallbacks == tuple(
        (case.case_id, "RerankerUnavailable") for case in dataset.cases
    )
    before, llm = comparison.retrieved, comparison.llm
    assert [o.ranks for o in llm.outcomes] == [o.ranks for o in before.outcomes]
    assert (comparison.usages, comparison.cost_usd) == ((), Decimal(0))


@pytest.mark.integration
async def test_candidates_beyond_the_token_budget_are_reported_as_partial(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    ranker = FakeRankingModel()

    comparison = await compare_reranking_strategies(
        runtime,
        dataset,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        reranker=FakeReranker(),
        device="cpu",
        ranker=ranker,
        budget=RankingBudget(max_candidates=10, max_input_tokens=TIGHT_TOKENS),
        price=PRICE,
    )

    assert comparison.partial
    for case_id, ranked, given in comparison.partial:
        assert 2 <= ranked < given
        assert f"- {case_id}: {ranked} of {given} ordered;" in (
            render_strategy_comparison(comparison)
        )
```

Check:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_reranking_strategies_integration.py -m integration -q
```

Expected: `4 passed`.

### Step 7 — No coding in this step: run three strategies, break it, and decide

This step runs the real comparison, on `cpu` and on `mps`, breaks one guarantee on purpose, and asks you to choose the defaults.

**Action 1:** start the BGE-M3 server and the reranker server, each in its own terminal, and leave them running. Start the reranker on `cpu` first:

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py
```

```bash
RERANKER_DEVICE=cpu uv run --project ml/bge_m3_server python ml/bge_m3_server/reranker_server.py
```

If port 8765 is taken, start BGE-M3 with `BGE_M3_PORT=8766` in front, and in the third terminal run `export LOCAL_EMBEDDING_URL=http://127.0.0.1:8766` first.

**Action 2:** in a third terminal, run once on a fresh database. The script reads the OpenAI key from `.env` itself:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_dataset#')"
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --compare-rerankers
cp artifacts/evaluations/bilingual-seed-reranking-strategies.md /tmp/strategies-cpu.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
```

**Action 3:** stop the reranker server with Ctrl+C, start it again with `RERANKER_DEVICE=mps`, and run once more:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --compare-rerankers > /dev/null
diff <(sed '/^## Latency and cost/,$d' /tmp/strategies-cpu.md) <(sed '/^## Latency and cost/,$d' artifacts/evaluations/bilingual-seed-reranking-strategies.md) && echo same
sed -n '/^## Latency and cost/,$p' artifacts/evaluations/bilingual-seed-reranking-strategies.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
unset KNOWLEDGE_SERVICE_DATABASE_URL
```

If the two runs differ above "## Latency and cost", the language model chose a different order; record which cases changed. Stop both servers with Ctrl+C.

**Action 4:** write the decision. In the evidence file, under "Decision", write:

- the "Quality by slice" table, because `artifacts/` is ignored by Git;
- the moves of each reranker, the cases where they disagree, the fallbacks, and the partial rankings;
- the "Latency and cost" table of both runs;
- a decision per environment, in a table: the test suite, local development, and production, each with **no reranker**, **cross-encoder**, or **LLM**, and one reason each;
- one sentence on what would change the production default, as a number: the MRR gain, the dataset size, and the time and cost per question you would accept;
- at least one **falsified assumption** in the shape "I assumed X. The report shows Y (case IDs and numbers). So Z is wrong."

**Action 5:** break the order of the work on purpose. In `src/knowledge_service/retrieval/evaluation_dataset.py`, in `compare_reranking_strategies`, add one line at the end of the first loop, right after `cross.append(...)`:

```python
        await _llm_rerank_case(case, ranker, result, budget, llm_identity)
```

This sends each case to the language model right after the cross-encoder. Then run:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_reranking_strategies_integration.py -m integration -q
```

Expected: 2 failed: `test_a_failing_cross_encoder_stops_before_the_language_model_is_asked`, because the language model was asked about case 1 before the cross-encoder failed on case 2, and `test_both_rerankers_get_the_same_eligible_candidates_of_every_case`, because each case now reached the language model twice. Remove the line and run again until all pass.

### Step 8 — No coding in this step: gates, docs, evidence, and progress

This step proves everything still works, updates the text the change made incomplete, and records the result.

**Action 1:** in `docs/architecture.md`:

Find in `docs/architecture.md`:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost) |
```

Replace with:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case) |
```

Find in `docs/architecture.md`:

```markdown
evaluation script uses it; no default search reranks.
```

Replace with:

```markdown
evaluation script uses it; no default search reranks.

`--compare-rerankers` runs both rerankers on the same Candidates of each
case. The default chosen for each environment, and the numbers behind it,
are recorded in `docs/evidence/M05-T13-reranking-strategies.md`.
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_reranking_strategies.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 10 passed for the narrow tests, `make test-unit` 1457 passed, and `make test-integration` 313 passed.

**Action 3:** complete the evidence file:

- **Design:** the changed files; why one search per case; why the same order; why the cross-encoder runs first for every case; why a `limit` above the budget is refused and partial rankings are listed; why the renderer checks that the reports line up; what leaves the machine.
- **Verification:** the commands and counts, both runs and whether they agreed above "## Latency and cost", the deliberate break, and the known limits from "Before you begin".
- **Review:** an adversarial review of the prototype found two broken guarantees and three smaller problems, all fixed, each with a test that failed before the fix: the language model sees only the Candidates that fit its budget while the report claimed both rerankers saw the same ones, so a `limit` above `max_candidates` is now refused and partial rankings are listed; three reports in a different case order rendered silently with one case's ranks under another, so the renderer now checks them; a cross-encoder failing on a later case left earlier cases billed with no report, so every case is now cross-encoded first; the device sat above the line that two runs compare, so it moved into "## Latency and cost"; and the script read its settings outside `try`, so a bad setting skipped closing the database.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M05-T13 row to `docs/curriculum/PROGRESS.md`, set the next task to M05-T14, and commit with `m05-t13: compare reranking strategies`.

## Reflection questions

1. What does `compare_reranking_strategies` hide from the code that calls it? List what a caller never has to think about, such as the order of the work or what each reranker was given.
2. A later change wants the run to be faster, so it sends each case to the cross-encoder and the language model at the same time. Which guarantee from your invariant would that break first, and which test would catch it?
3. The cross-encoder and the language model fixed the same case. What kind of question would you add to the dataset to see a real difference between them, and what would you expect each to do with it?
4. Which of your predictions was wrong, and what did you believe about the rerankers, the device, or this dataset that made you predict it?
5. You chose a default for production. Name the one metric, the one latency or cost limit, and the one failure case you would check before changing it.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before running anything.
- [ ] `compare_reranking_strategies` searches each case once and gives the same Candidates, in retrieval's order, to both rerankers.
- [ ] Every case is cross-encoded before any case reaches the language model; a failing cross-encoder bills nothing.
- [ ] A `limit` above the budget, and two rerankers with one name, are refused before any work; partial rankings are listed.
- [ ] `render_strategy_comparison` refuses reports that do not line up, and only "## Latency and cost" changes from run to run.
- [ ] Restricted text never reaches either reranker, and no question or restricted text appears in the report or the logs.
- [ ] `--compare-rerankers` checks both servers before writing and closes every client.
- [ ] You ran it on `cpu` and on `mps` and recorded whether the runs agreed.
- [ ] The evidence file holds the quality table, the moves, the disagreements, the fallbacks, latency and cost, a decision per environment, and a falsified assumption.
- [ ] You ran the deliberate break from Step 7, saw 2 tests fail, and undid it.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The architecture text, evidence, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
