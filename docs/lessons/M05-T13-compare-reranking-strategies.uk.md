# M05-T13 — Порівняння стратегій reranking

Англійська версія: [M05-T13-compare-reranking-strategies.md](M05-T13-compare-reranking-strategies.md)

Вихідне завдання: [M05-T13](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T13-compare-reranking-strategies.md)

## Що ви зробите

Тепер у вас є два reranker-и: локальний cross-encoder з M05-T11 і мовна модель з M05-T12. Кожного міряли в окремому прогоні, тож їхні числа взято з різних прогонів. У цьому уроці один прогін шукає кожне запитання один раз і дає тих самих кандидатів (Candidate) обом reranker-ам. Звіт ставить поруч три стратегії: без reranker-а, cross-encoder і мовну модель. Для кожної він показує якість, час і гроші. Наприкінці ви вирішуєте, яку стратегію кожне середовище використовує за замовчуванням, або що поки не переранжовує жодне.

- `compare_reranking_strategies` шукає кожен випадок один раз, надсилає тих самих кандидатів cross-encoder-у, а потім, коли всі випадки готові, мовній моделі.
- `render_strategy_comparison` пише один звіт із трьома колонками в кожній таблиці і відмовляє, якщо три звіти не збігаються випадок за випадком.
- Скрипт отримує опцію `--compare-rerankers`, якій потрібні обидва локальні сервери і ключ OpenAI.
- Ви запускаєте її, читаєте компроміси і записуєте рішення для кожного середовища.

| Завдання вимагає | Де це відбувається |
|---|---|
| Без reranker-а, cross-encoder і LLM на тих самих двомовних випадках | Кроки 2 і 6: один пошук на випадок, ті самі кандидати в тому самому порядку обом reranker-ам |
| Компроміси якості, затримки і вартості | Кроки 3 і 7: Recall@1 і MRR за зрізами, зсуви за випадками, час і долари на випадок |
| Детерміновані тести: успіх, найризиковіший край, типізована помилка | Кроки 4 і 6: ті самі кандидати доходять до обох; cross-encoder, що зламався, зупиняє прогін, перш ніж щось оплачено |
| Рішення: значення за замовчуванням для кожного середовища або відмова від reranking | Крок 7 |
| Авторизація до того, як дані покинуть сховище | Крок 6: закритий текст ніколи не доходить до жодного reranker-а |
| Жодного підкинутого чутливого тексту в телеметрії | Крок 6: у звіті й логах немає ні запитання, ні закритого тексту |

Цей урок містить код: кроки 2–6 пишуть код. Кроки 1, 7 і 8 коду не потребують. Крок 7 звертається до OpenAI з вашим API-ключем; один повний прогін коштує близько 2 центів.

Терміни цього уроку:

- **Стратегія:** один спосіб упорядкувати кандидатів запитання. Тут їх три: залишити порядок пошуку, спитати cross-encoder або спитати мовну модель.
- **Recall@1:** частка запитань, у яких релевантний Chunk стоїть першим. Кожна стратегія тут лише переставляє тих самих десять кандидатів, тож Recall@10 змінитися не може; змінитися можуть Recall@1 і MRR.
- **Компроміс (trade-off):** те, чим ви платите за виграш. Стратегія, що додає 0,03 до MRR, але й 1,5 секунди та рахунок на кожне запитання, може того не варти.
- **Значення за замовчуванням для середовища:** налаштування, яке кожне місце використовує, коли ніхто не вибирає: набір тестів, ноутбук розробника і продакшен.
- **Часткове ранжування (partial ranking):** мовна модель упорядковує лише кандидатів, які вміщуються в її бюджет; решта лишаються нижче в порядку пошуку.

## Перш ніж почати

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілий, коли ви до нього дійдете.

- **Чому один прогін, а не два звіти.** У M05-T11 і M05-T12 кожен reranker мав власний прогін із власним пошуком. Пошуки дали тих самих кандидатів, але цього ніщо не доводило. Тут кожен випадок шукається один раз, і цей один список іде обом reranker-ам. Тож будь-яка різниця між стратегіями походить лише від reranker-а.
- **Ті самі кандидати, той самий порядок.** Обидва reranker-и отримують кандидатів у порядку пошуку. Для мовної моделі порядок важливий: вона може віддавати перевагу тому, що прочитала першим. Якби вона отримала порядок cross-encoder-а, то стартувала б із перевагою.
- **Спершу cross-encoder, для кожного випадку.** Прогін переранжовує cross-encoder-ом усі випадки, перш ніж надіслати будь-який випадок мовній моделі. Cross-encoder працює за принципом «все або нічого», як у M05-T11, тож якщо його сервер зламається на п'ятому випадку, прогін зупиниться. За такого порядку зупинений прогін нічого не оплатив.
- **Мовна модель може побачити менше кандидатів.** `rerank_with_llm` надсилає лише верх списку, що вміщується в її бюджет. Тож прогін відмовляє, якщо `limit` більший за `max_candidates` бюджету, і позначає як часткове кожне ранжування, де межа токенів обрізала список. Звіт ніколи не стверджує, що обидва reranker-и впорядкували однакову кількість кандидатів, коли це не так.
- **Дві назви, дві колонки.** Звіт розрізняє стратегії за назвою reranker-а. Два reranker-и з однаковою ідентичністю відхиляються ще до будь-якої роботи.
- **Звіт, який не може переплутати випадки.** Рендерер зшиває три звіти рядок за рядком. Якби в одному звіті випадки йшли в іншому порядку, випадок `a` показав би місця випадку `b`. Тому рендерер перевіряє, що три звіти походять з одного прогону, містять ті самі випадки в тому самому порядку і йдуть так: без reranker-а, а далі два різні reranker-и. Інакше він кидає `ValueError`.
- **Що показує звіт.** Якість за зрізами як Recall@1 і MRR, місця кожного випадку за кожною стратегією, зсуви порівняно з «без reranker-а», де два reranker-и розходяться, запасні варіанти (fallback) і часткові ранжування мовної моделі, і наостанок «## Latency and cost»: медіанний і найдовший час, запити і долари на випадок для кожного кроку. Між прогонами змінюється лише цей останній розділ, тож два прогони можна порівняти через `diff` вище нього, якщо не брати до уваги вибору мовної моделі.
- **Де живе код.** У `retrieval/evaluation_dataset.py`, поруч із двома порівняннями, які він поєднує. Дві маленькі допоміжні функції, `_move` і `_times`, спільні зі старішими звітами, які друкують рівно те саме, що й раніше.
- **Що покидає машину.** Те саме, що в M05-T12: лише запитання і текст кандидатів, які пройшли перевірку прав, надіслані в OpenAI з `store=False`. Cross-encoder працює на вашій машині.
- **Відомі обмеження.** 16 випадків — це мало, а злитий пошук уже ставить 15 із них першими; мовна модель може впорядкувати інакше в іншому прогоні; ціна — це налаштування; пристрій cross-encoder-а сильно змінює його час, тож рішення називає пристрій.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і передбачення

Перш ніж з'явиться код, запишіть, що має бути правдою наприкінці уроку і що, на вашу думку, покажуть три стратегії. Крок 7 перевіряє ці передбачення за звітом.

Створіть `docs/evidence/M05-T13-reranking-strategies.md` за цим шаблоном, а потім замініть підказки до передбачень власними реченнями:

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

Перевірка: файл існує, і кожне передбачення — повне речення, яке число з кроку 7 може спростувати.

### Крок 2 — Крок із кодом: три стратегії на тих самих кандидатах

Цей крок додає саме порівняння: один пошук на випадок, а потім обидва reranker-и на тому самому списку.

**Простими словами:** Порівняння один раз завантажує і векторизує корпус, як і два попередні порівняння. Для кожного запитання воно один раз запускає злитий пошук, зберігає список і просить cross-encoder переставити його. Коли всі запитання готові, воно віддає кожен збережений список, без змін, мовній моделі. Повертає три звіти з тими самими випадками, а також те, скільки коштував кожен reranker.

| Зміна | Що вона робить |
|---|---|
| docstring модуля | один пункт про нове порівняння |
| `StrategyComparison` | три звіти; пристрій і запити cross-encoder-а; запасні варіанти, usage, вартість і часткові ранжування мовної моделі |
| `compare_reranking_strategies` | відхиляє поганий `limit`, `limit` більший за `max_candidates` бюджету і два reranker-и з однією назвою; потім шукає і переранжовує cross-encoder-ом кожен випадок, потім ранжує кожен випадок мовною моделлю |
| `partial` | `(case, ordered, given)` для кожного випадку, де мовна модель не перейшла на запасний варіант, але впорядкувала менше кандидатів, ніж повернув пошук |

У `src/knowledge_service/retrieval/evaluation_dataset.py` внесіть ці зміни по черзі:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
  billed tokens, and the estimated cost.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
```

Замініть на:

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

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    cost_usd: Decimal | None
```

Замініть на:

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

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
        cost_usd=_cost(usages, price),
    )
```

Замініть на:

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

Функція повторно використовує `_search_case`, `_rerank_case` і `_llm_rerank_case` з попередніх уроків, тож кожна помилка лишається типізованою і називає лише випадок і тип помилки. Перший цикл зберігає кожен результат `HybridRetrieval`, тож другий цикл надсилає рівно той список, який бачив cross-encoder.

Перевірка:

```bash
uv run ruff format src/knowledge_service/retrieval/evaluation_dataset.py
uv run ruff check src/knowledge_service/retrieval/evaluation_dataset.py
uv run pyright src/knowledge_service/retrieval/evaluation_dataset.py
```

Очікувано: `All checks passed!` і `0 errors`.

### Крок 3 — Крок із кодом: звіт про три стратегії

Цей крок пише звіт, що ставить три стратегії поруч.

**Простими словами:** Спершу звіт перевіряє, що три набори результатів описують ті самі запитання в тому самому порядку. Потім він друкує якість за напрямком мови і типом запитання, місця кожного запитання за кожною стратегією, скільки запитань піднялося чи опустилося і де два reranker-и розходяться. Він перелічує запасні варіанти і часткові ранжування мовної моделі. Наостанок іде єдина частина, що змінюється між прогонами: час, запити і гроші на запитання для кожного кроку.

| Зміна | Що вона робить |
|---|---|
| `render_strategy_comparison` | заголовок, далі кожен розділ по черзі; Recall@1 і MRR, бо Recall@10 змінитися не може |
| `_check_strategies` | `ValueError`, якщо звіти не з одного прогону, не містять тих самих випадків у тому самому порядку або йдуть не як «без reranker-а», а потім два різні reranker-и |
| `_strategy_quality` | Recall@1 і MRR за зрізами, по дві колонки на стратегію |
| `_strategy_cases` | місця за випадками, зсуви порівняно з «без reranker-а» і випадки, де reranker-и розходяться |
| `_strategy_costs` | «## Latency and cost»: пристрій, далі медіана, найдовший час, запити і долари на випадок для кожного кроку, далі токени й загальна вартість |
| `_move`, `_times` | одне правило зсуву і одне правило часу, тепер спільні з `_delta_lines` і `_latency_lines`; їхній вивід не змінюється |

У `src/knowledge_service/retrieval/evaluation_dataset.py` внесіть ці зміни по черзі:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python

def _delta_lines(before: DatasetReport, after: DatasetReport) -> list[str]:
```

Замініть на:

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

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    for old, new in zip(before.outcomes, after.outcomes, strict=True):
        change = reciprocal_rank(new.ranks) - reciprocal_rank(old.ranks)
        move = "up" if change > 0 else "down" if change < 0 else "same"
        moves[move] += 1
```

Замініть на:

```python
    for old, new in zip(before.outcomes, after.outcomes, strict=True):
        move = _move(old, new)
        moves[move] += 1
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    for name, report in (("retrieval", before), ("reranking", after)):
        times = [outcome.elapsed_seconds * 1000 for outcome in report.outcomes]
        median = statistics.median(times) if times else 0.0
        slowest = max(times, default=0.0)
        lines.append(f"| {name} | {median:.1f} | {slowest:.1f} |")
```

Замініть на:

```python
    for name, report in (("retrieval", before), ("reranking", after)):
        median, slowest = _times(report)
        lines.append(f"| {name} | {median:.1f} | {slowest:.1f} |")
```

Пристрій стоїть в останньому розділі, як у звіті M05-T11: прогін на `mps` і прогін на `cpu` тоді відрізняються лише нижче «## Latency and cost».

Перевірка: ті самі три команди, що й у кроці 2, а потім наявні тести звітів, які доводять, що старіші звіти не змінилися:

```bash
uv run pytest tests/test_evaluation_dataset.py tests/test_reranking.py tests/test_llm_reranking.py -q
```

Очікувано: усі тести проходять.

### Крок 4 — Крок із кодом: unit-тести налаштувань і звіту

Цей крок тестує те, що не потребує бази даних: відхилені налаштування і звіт.

**Простими словами:** Три неправильні налаштування мають бути відхилені, перш ніж порівняння торкнеться бази даних. Потім невеликий вручну складений набір із трьох запитань перевіряє кожен рядок звіту: числа якості, зсуви, розбіжність, запасний варіант, часткове ранжування, час і вартість. Чотири зламані набори результатів перевіряють, що звіт відмовляється плутати запитання.

| Тест | Що він доводить |
|---|---|
| `test_a_wrong_strategy_setting_is_refused_before_any_work` | `limit` 0, `limit` 21 за бюджету 20 і два reranker-и з однією назвою кидають `DatasetRunRefused` до будь-якої роботи з базою |
| `test_the_report_puts_three_strategies_side_by_side` | точні Recall@1 і MRR, місця за випадками, зсуви, розбіжність, запасний варіант і часткове ранжування |
| `test_the_last_section_shows_what_each_step_adds_per_case` | вище «## Latency and cost» немає ні часу, ні пристрою; час, запити і долари на випадок точні |
| `test_an_unknown_price_is_reported_as_unknown` | без ціни звіт пише «unknown», а не вигаданий 0 |
| `test_reports_that_do_not_line_up_are_refused` | інший порядок випадків, закороткий звіт, переранжований звіт на місці «без reranker-а» та інший limit кидають `ValueError` |

Числа досить малі, щоб перевірити їх вручну. Без reranker-а випадок `a` стоїть на 2-му місці, тож його обернене місце — 1/2; `b` — на 1-му; `c` не знайдено. MRR = (1/2 + 1 + 0) / 3 = 0,500. Мовна модель ставить `b` на 3-тє місце, тож її MRR = (1 + 1/3 + 0) / 3 = 0,444.

Створіть `tests/test_reranking_strategies.py`:

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

Перевірка:

```bash
uv run pytest tests/test_reranking_strategies.py -q
```

Очікувано: `10 passed`.

### Крок 5 — Крок із кодом: опція `--compare-rerankers`

Цей крок дає змогу запустити порівняння з командного рядка.

**Простими словами:** Нова опція читає ключ OpenAI і налаштування ранжування, відкриває локальний сервер векторів, локальний сервер reranker-а і клієнт OpenAI і перевіряє обидва сервери, перш ніж щось записати. Потім вона запускає порівняння, пише звіт і закриває все, хоч би як закінчився прогін.

| Зміна | Що вона робить |
|---|---|
| docstring | що потрібно `--compare-rerankers` і куди вона пише |
| `STRATEGIES_REPORT` | `artifacts/evaluations/bilingual-seed-reranking-strategies.md` |
| `--compare-rerankers` | у тій самій групі, що й інші опції моделей, тож вибрати можна лише одну |
| перевірка API-ключа | і `--llm-rerank`, і `--compare-rerankers` зупиняються одразу, якщо ключа немає |
| `_run_strategies` | налаштування читаються всередині `try`, усі три клієнти закриваються через `async with`, база даних — у `finally` |

У `scripts/run_retrieval_dataset.py` внесіть ці зміни по черзі:

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
`artifacts/evaluations/bilingual-seed-llm-rerank.md`.
"""
```

Замініть на:

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

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
    compare_reranking,
    compare_sparse,
```

Замініть на:

```python
    compare_reranking,
    compare_reranking_strategies,
    compare_sparse,
```

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
    render_sparse_comparison,
    run_dataset,
```

Замініть на:

```python
    render_sparse_comparison,
    render_strategy_comparison,
    run_dataset,
```

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
LLM_RERANK_REPORT = Path("artifacts/evaluations/bilingual-seed-llm-rerank.md")
SERVER_CHECK_SECONDS = 10.0
```

Замініть на:

```python
LLM_RERANK_REPORT = Path("artifacts/evaluations/bilingual-seed-llm-rerank.md")
STRATEGIES_REPORT = Path("artifacts/evaluations/bilingual-seed-reranking-strategies.md")
SERVER_CHECK_SECONDS = 10.0
```

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
    embeddings.add_argument("--llm-rerank", action="store_true")
    arguments = parser.parse_args()
```

Замініть на:

```python
    embeddings.add_argument("--llm-rerank", action="store_true")
    embeddings.add_argument("--compare-rerankers", action="store_true")
    arguments = parser.parse_args()
```

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
            return 1
        if arguments.llm_rerank and api_key is None:
            sys.stderr.write("set KNOWLEDGE_SERVICE_MODEL_API_KEY to rank with\n")
```

Замініть на:

```python
            return 1
        ranks = arguments.llm_rerank or arguments.compare_rerankers
        if ranks and api_key is None:
            sys.stderr.write("set KNOWLEDGE_SERVICE_MODEL_API_KEY to rank with\n")
```

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
                _run_llm_rerank(runtime, dataset, corpus, arguments.limit, api_key)
            )
```

Замініть на:

```python
                _run_llm_rerank(runtime, dataset, corpus, arguments.limit, api_key)
            )
        elif arguments.compare_rerankers and api_key is not None:
            default = STRATEGIES_REPORT
            rendered = asyncio.run(
                _run_strategies(runtime, dataset, corpus, arguments.limit, api_key)
            )
```

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python

def _openai_model(api_key: SecretStr) -> ModelSource:
```

Замініть на:

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

Перевірка:

```bash
uv run ruff format scripts/run_retrieval_dataset.py
uv run ruff check scripts/run_retrieval_dataset.py
uv run pyright scripts/run_retrieval_dataset.py
uv run python scripts/run_retrieval_dataset.py --compare-rerankers --llm-rerank
```

Очікувано: перші три проходять. Остання завершується повідомленням `argument --llm-rerank: not allowed with argument --compare-rerankers`.

### Крок 6 — Крок із кодом: тест порівняння на PostgreSQL

Цей крок запускає порівняння на справжній базі даних із фальшивими моделями і перевіряє найважливіші гарантії.

**Простими словами:** Seed-корпус зберігається, векторизується фальшивою моделлю і шукається від імені читача. Потім тести дивляться, що саме отримав кожен reranker. Обидва мають отримати ті самі тексти в тому самому порядку і ніколи — закритий текст. Cross-encoder, що ламається на другому запитанні, має зупинити прогін, перш ніж мовну модель про щось спитають. Мовна модель, що ламається, має перейти на запасний варіант для кожного запитання, а прогін має тривати далі. Тісний бюджет токенів має проявитися як часткові ранжування.

| Тест | Що він доводить |
|---|---|
| `test_both_rerankers_get_the_same_eligible_candidates_of_every_case` | один запит на випадок до кожного reranker-а, ті самі тексти в тому самому порядку; закритий текст не надсилається; усі три стратегії знаходять ті самі Chunk-и; у звіті й логах немає ні запитання, ні закритого тексту |
| `test_a_failing_cross_encoder_stops_before_the_language_model_is_asked` | cross-encoder оцінює випадок 1 і ламається на випадку 2; `CaseFailed` називає випадок, а мовна модель не отримала жодного виклику |
| `test_a_failing_language_model_falls_back_and_the_run_goes_on` | кожен випадок переходить на запасний варіант із `RerankerUnavailable`, зберігає місця пошуку і нічого не оплачує |
| `test_candidates_beyond_the_token_budget_are_reported_as_partial` | коли місця вистачає лише на кількох кандидатів, кожен частковий випадок показує, скільки з них упорядковано, і звіт про це каже |

Другий тест береже ваші гроші. Якби код надсилав кожен випадок мовній моделі одразу після cross-encoder-а, випадок 1 був би вже оплачений, коли випадок 2 зламається.

Створіть `tests/test_reranking_strategies_integration.py`:

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

Перевірка:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_reranking_strategies_integration.py -m integration -q
```

Очікувано: `4 passed`.

### Крок 7 — Без коду: запустіть три стратегії, зламайте і вирішіть

Цей крок запускає справжнє порівняння на `cpu` і на `mps`, навмисно ламає одну гарантію і просить вас вибрати значення за замовчуванням.

**Дія 1:** запустіть сервер BGE-M3 і сервер reranker-а, кожен у власному терміналі, і залиште їх працювати. Спершу запустіть reranker на `cpu`:

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py
```

```bash
RERANKER_DEVICE=cpu uv run --project ml/bge_m3_server python ml/bge_m3_server/reranker_server.py
```

Якщо порт 8765 зайнятий, запустіть BGE-M3 з `BGE_M3_PORT=8766` на початку, а в третьому терміналі спершу виконайте `export LOCAL_EMBEDDING_URL=http://127.0.0.1:8766`.

**Дія 2:** у третьому терміналі запустіть один раз на свіжій базі даних. Скрипт сам читає ключ OpenAI з `.env`:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_dataset#')"
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --compare-rerankers
cp artifacts/evaluations/bilingual-seed-reranking-strategies.md /tmp/strategies-cpu.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
```

**Дія 3:** зупиніть сервер reranker-а через Ctrl+C, запустіть його знову з `RERANKER_DEVICE=mps` і запустіть порівняння ще раз:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --compare-rerankers > /dev/null
diff <(sed '/^## Latency and cost/,$d' /tmp/strategies-cpu.md) <(sed '/^## Latency and cost/,$d' artifacts/evaluations/bilingual-seed-reranking-strategies.md) && echo same
sed -n '/^## Latency and cost/,$p' artifacts/evaluations/bilingual-seed-reranking-strategies.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
unset KNOWLEDGE_SERVICE_DATABASE_URL
```

Якщо два прогони відрізняються вище «## Latency and cost», це мовна модель вибрала інший порядок; запишіть, які випадки змінилися. Зупиніть обидва сервери через Ctrl+C.

**Дія 4:** запишіть рішення. У файлі evidence, у розділі «Decision», напишіть:

- таблицю «Quality by slice», бо `artifacts/` Git ігнорує;
- зсуви кожного reranker-а, випадки, де вони розходяться, запасні варіанти і часткові ранжування;
- таблицю «Latency and cost» обох прогонів;
- рішення для кожного середовища у вигляді таблиці: набір тестів, локальна розробка і продакшен, для кожного **no reranker**, **cross-encoder** або **LLM**, і по одній причині;
- одне речення про те, що змінило б значення за замовчуванням у продакшені, у числах: виграш MRR, розмір датасету, а також час і вартість на запитання, які ви прийняли б;
- щонайменше одне **спростоване припущення** у формі «Я припускав X. Звіт показує Y (ID випадків і числа). Отже, Z хибне».

**Дія 5:** навмисно зламайте порядок роботи. У `src/knowledge_service/retrieval/evaluation_dataset.py`, у `compare_reranking_strategies`, додайте один рядок у кінці першого циклу, одразу після `cross.append(...)`:

```python
        await _llm_rerank_case(case, ranker, result, budget, llm_identity)
```

Так кожен випадок іде до мовної моделі одразу після cross-encoder-а. Потім запустіть:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_reranking_strategies_integration.py -m integration -q
```

Очікувано: 2 failed. Падає `test_a_failing_cross_encoder_stops_before_the_language_model_is_asked`, бо мовну модель спитали про випадок 1 раніше, ніж cross-encoder зламався на випадку 2. Падає і `test_both_rerankers_get_the_same_eligible_candidates_of_every_case`, бо тепер кожен випадок дійшов до мовної моделі двічі. Видаліть рядок і запускайте знову, доки все не пройде.

### Крок 8 — Без коду: перевірки, документація, evidence і прогрес

Цей крок доводить, що все досі працює, оновлює текст, який зміна зробила неповним, і записує результат.

**Дія 1:** у `docs/architecture.md`:

Знайдіть у `docs/architecture.md`:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost) |
```

Замініть на:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case) |
```

Знайдіть у `docs/architecture.md`:

```markdown
evaluation script uses it; no default search reranks.
```

Замініть на:

```markdown
evaluation script uses it; no default search reranks.

`--compare-rerankers` runs both rerankers on the same Candidates of each
case. The default chosen for each environment, and the numbers behind it,
are recorded in `docs/evidence/M05-T13-reranking-strategies.md`.
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_reranking_strategies.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. В еталонному прогоні вузькі тести дали 10 passed, `make test-unit` — 1457 passed, а `make test-integration` — 313 passed.

**Дія 3:** заповніть файл evidence:

- **Design:** змінені файли; чому один пошук на випадок; чому той самий порядок; чому cross-encoder іде першим для кожного випадку; чому `limit` понад бюджет відхиляється, а часткові ранжування перелічуються; чому рендерер перевіряє, що звіти збігаються; що покидає машину.
- **Verification:** команди й кількості, обидва прогони і чи збіглися вони вище «## Latency and cost», навмисна поломка і відомі обмеження з «Перш ніж почати».
- **Review:** адверсаріальне рев'ю прототипу знайшло дві зламані гарантії і три менші проблеми. Усі виправлено, і для кожної є тест, що падав до виправлення:
  - мовна модель бачить лише кандидатів, що вміщуються в її бюджет, а звіт стверджував, що обидва reranker-и бачили тих самих, тож `limit` понад `max_candidates` тепер відхиляється, а часткові ранжування перелічуються;
  - три звіти з іншим порядком випадків рендерилися мовчки, з місцями одного випадку під іншим, тож тепер рендерер їх перевіряє;
  - cross-encoder, що ламався на пізнішому випадку, залишав ранні випадки оплаченими без звіту, тож тепер спершу всі випадки проходять через cross-encoder;
  - пристрій стояв вище межі, за якою порівнюють два прогони, тож його перенесено в «## Latency and cost»;
  - скрипт читав налаштування поза `try`, тож погане налаштування пропускало закриття бази даних.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте завдання виконаним, додайте рядок M05-T13 у `docs/curriculum/PROGRESS.md`, встановіть наступним завданням M05-T14 і зробіть commit із повідомленням `m05-t13: compare reranking strategies`.

## Запитання для роздумів

1. Що `compare_reranking_strategies` ховає від коду, який її викликає? Перелічіть, про що викликачеві ніколи не треба думати: наприклад, порядок роботи чи що отримав кожен reranker.
2. Пізніша зміна хоче пришвидшити прогін і надсилає кожен випадок cross-encoder-у і мовній моделі одночасно. Яку гарантію з вашого інваріанту це зламає першою, і який тест це впіймає?
3. Cross-encoder і мовна модель виправили той самий випадок. Яке запитання ви додали б до датасету, щоб побачити справжню різницю між ними, і що, на вашу думку, кожен із них із ним зробив би?
4. Яке з ваших передбачень виявилося хибним, і у що ви вірили щодо reranker-ів, пристрою чи цього датасету, що змусило вас так передбачити?
5. Ви вибрали значення за замовчуванням для продакшену. Назвіть одну метрику, одну межу затримки чи вартості й один випадок збою, які ви перевірили б, перш ніж його змінити.

## Чекліст завершення

- [ ] У файлі evidence є інваріант і ваші передбачення, записані до будь-якого запуску.
- [ ] `compare_reranking_strategies` шукає кожен випадок один раз і дає тих самих кандидатів, у порядку пошуку, обом reranker-ам.
- [ ] Кожен випадок проходить через cross-encoder, перш ніж будь-який випадок дійде до мовної моделі; cross-encoder, що зламався, нічого не оплачує.
- [ ] `limit` понад бюджет і два reranker-и з однією назвою відхиляються до будь-якої роботи; часткові ранжування перелічуються.
- [ ] `render_strategy_comparison` відхиляє звіти, що не збігаються, і між прогонами змінюється лише «## Latency and cost».
- [ ] Закритий текст ніколи не доходить до жодного reranker-а, і у звіті й логах немає ні запитання, ні закритого тексту.
- [ ] `--compare-rerankers` перевіряє обидва сервери, перш ніж щось записати, і закриває кожен клієнт.
- [ ] Ви запустили порівняння на `cpu` і на `mps` і записали, чи збіглися прогони.
- [ ] У файлі evidence є таблиця якості, зсуви, розбіжності, запасні варіанти, затримка і вартість, рішення для кожного середовища і спростоване припущення.
- [ ] Ви виконали навмисну поломку з кроку 7, побачили 2 тести, що впали, і відкотили її.
- [ ] Усі перевірки проходять: format, lint, types, unit, integration, docs.
- [ ] Текст архітектури, evidence, рядок у PROGRESS і commit готові.

Коли напишете файли, поділіться ними або виводом помилок, і я перевірю їх за цим чеклістом.
