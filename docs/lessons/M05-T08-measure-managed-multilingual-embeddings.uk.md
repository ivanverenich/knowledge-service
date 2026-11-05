# M05-T08 — Виміряти керовані багатомовні ембединги

Англійська версія: [M05-T08-measure-managed-multilingual-embeddings.md](M05-T08-measure-managed-multilingual-embeddings.md)

Вихідне завдання: [M05-T08](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T08-measure-managed-multilingual-embeddings.md)

## Що ви зробите

У M05-T07 ви зібрали незмінний набір із 16 запитань і прогнали його лише пошуком за словами. У цьому уроці ви додаєте до прогону пошук за змістом і перетворюєте сирі місця на дві стандартні оцінки. Ви ставите запитання трьома способами (лише слова, лише зміст, обидва разом), оцінюєте кожен мовний напрям окремо і записуєте, у чому числа спростували ваші очікування.

- Невеликий модуль `ranking_metrics.py` рахує **Recall@k** і **MRR** за місцями.
- `evaluation_dataset.py` отримує `compare_branches`. Вона один раз зберігає корпус, дає кожному уривку вектор від однієї моделі ембедингів, а потім ставить кожне запитання тричі.
- Звіт отримує оцінки для кожного зрізу і короткий блок конфігурації: які пошуки працювали, яка модель, скільки результатів залишено.
- Скрипт отримує параметр `--managed-embeddings`, який використовує модель ембедингів OpenAI.
- Ви запускаєте його один раз зі справжнім ключем API і записуєте у файл доказів результат, промахи і принаймні одне спростоване припущення.

| Завдання вимагає | Де це відбувається |
|---|---|
| Прогнати базовий набір через шлях керованих ембедингів | Крок 4: `compare_branches` вбудовує корпус моделлю; крок 6: `--managed-embeddings` використовує OpenAI; крок 8: справжній прогін |
| Розбити Recall@k і MRR за мовними напрямами | Крок 2: оцінки; крок 5: окремий рядок для кожного напряму і кожного класу запитань |
| Детерміновані тести для успіху, найризикованішого крайнього випадку, однієї типізованої помилки або відмови | Крок 7: три прогони на фальшивій моделі; найближчий уривок закритий і все одно ніколи не повертається; модель, що ламається, кидає `CorpusNotEmbedded` |
| Версійований звіт із конфігурацією, промахами і спростованим припущенням | Крок 5: блок конфігурації; крок 8: звіт іде у файл доказів |
| Авторизацію збережено, у телеметрії немає чутливого тексту | Крок 7: тест дозволів і перевірки звіту, помилок і логів |

У цьому уроці є програмування: кроки 2–7 пишуть Python. Кроки 1, 8 і 9 не стосуються коду. Для кроку 8 потрібен ваш ключ API OpenAI, і він коштує менше одного цента.

Терміни цього уроку:

- **Ембединг (embedding):** список чисел, що представляє зміст тексту. Тексти зі схожим змістом отримують близькі списки, навіть різними мовами. «Annual leave» і «щорічна відпустка» мають опинитися поруч.
- **Керована модель (managed model):** модель, яку запускає постачальник, тут OpenAI, і яку ви викликаєте через мережу. Ви не керуєте ні її версією, ні її серверами.
- **Простір ембедингів (Embedding Space):** запис проєкту про одну модель (постачальник, назва, кількість вимірів). Вектори з різних просторів ніколи не порівнюють. Пошук читає лише той простір, що позначений поточним.
- **Щільний пошук (dense search):** пошук за змістом. Він порівнює вектор запитання з вектором кожного уривка. **Лексичний пошук (lexical search)** — це пошук за словами з M05-T01–M05-T04.
- **Recall@k:** частка правильних уривків запитання, що потрапили в перші k результатів. Один правильний уривок на 3-му місці дає Recall@1 = 0 і Recall@5 = 1.
- **Обернене місце (reciprocal rank):** 1, поділене на місце першого правильного уривка, або 0, якщо нічого не знайдено. 1-ше місце дає 1, 4-те дає 0,25. **MRR** (mean reciprocal rank, середнє обернене місце) — його середнє за багатьма запитаннями. Воно винагороджує відповідь угорі списку, а не просто десь у ньому.
- **Зріз (slice):** група запитань з однаковою позначкою, наприклад `uk->en` (запитання українською, відповідь англійською). Одна загальна сума може сховати слабку мову, тому кожен зріз отримує власні оцінки.
- **Спростоване припущення (falsified assumption):** те, у що ви вірили до прогону, а числа показали, що це не так. Записати його — це спосіб навчитися з вимірювання, а не просто прочитати його.

## Перш ніж почати

Цей розділ пояснює рішення, що стоять за кодом, щоб кожен крок був зрозумілим, коли ви до нього дійдете.

- **Що вже є.** `run_dataset` з M05-T07 зберігає корпус у порожній базі даних і ставить кожне запитання від імені одного звичайного читача, `READER`. Вона записує місце кожного правильного уривка або `None`, якщо його немає. `publish_document_embeddings` з M04-T05 дає вектори всім уривкам одного документа, усім одразу або жодному. `dense_branch` і `lexical_branch` з M05-T06 — це два пошуки, які `retrieve_hybrid` може запустити й об'єднати.
- **Чому корпус зберігають і вбудовують лише раз.** Прогону потрібна порожня база даних, тому його не можна просто викликати тричі. `compare_branches` зберігає і вбудовує один раз, а потім ставить запитання тричі. Це також означає, що три прогони шукають серед тих самих векторів, тож будь-яка різниця походить лише від способу пошуку.
- **Чому закриті уривки теж отримують вектори.** У справжньому сервісі вбудовують кожен документ, хоч би хто мав право його читати, а перевірка дозволів відбувається під час пошуку. Тест цієї перевірки має сенс, лише якщо закритий уривок можна знайти. Тому закриту сторінку вбудовують так само, як інші. Її текст надсилається в OpenAI; базовий корпус вигаданий, тож нічого приватного не залишає ваш комп'ютер.
- **Найризикованіший крайній випадок.** Пошук за змістом може повернути уривок, що не має з запитанням жодного спільного слова. Закрита сторінка, як і одна з відкритих, — про щорічну відпустку, тож пошук за змістом про дні відпустки ставить її високо. Перевірка дозволів усередині SQL пошуку все одно мусить її відкинути. Тест у кроці 7 навмисно робить закритий уривок єдиним найкращим збігом і перевіряє, що читач його ніколи не отримує.
- **Чому оцінки — точні дроби.** Recall і MRR — це середні невеликих дробів, як 1/3 і 1/4. `Fraction` у Python зберігає їх точно, тож два прогони з однаковими місцями завжди друкують однакові числа. Звіт округлює їх до трьох знаків лише під час друку.
- **Які місця оцінюють.** Кожен прогін залишає перші 10 результатів, тому місця бувають лише до 10. Recall показують на 1, 3, 5 і 10; поріг понад ліміт прогону не мав би сенсу, і його пропускають. В об'єднаному прогоні місце — це позиція уривка в об'єднаному списку.
- **Що може зламатися і що тоді.** Модель, що ламається під час вбудовування корпусу, зупиняє прогін з `CorpusNotEmbedded` ще до першого запитання, тож напіввбудований корпус ніколи не оцінюють. Пошук, що не може відповісти на одне запитання, вже кидає `CaseUnanswered`. Будь-яка інша помилка запитання тепер кидає `CaseFailed`. Кожна з них називає лише ID випадку і тип помилки, ніколи не текст запитання чи документа.
- **Відомі обмеження.** Для об'єднаного прогону запитання вбудовують ще раз, тож якщо постачальник поверне трохи інші числа, щільні місця в об'єднаному прогоні можуть трохи відрізнятися від прогону лише за змістом. Одне невдале запитання в прогоні лише за змістом зупиняє все порівняння, коли корпус уже вбудовано. Підсумкова таблиця вгорі не має стовпця «degraded»; його мають повні звіти нижче. ID запитів фіксовані для кожного випадку, тож кожен прогін надсилає в OpenAI ті самі ID. Прогін, що зламався після збереження корпусу, залишає базу даних використаною, тож наступний починайте на свіжій.

## Покроковий план

### Крок 1 — Без програмування: запишіть інваріант і свої передбачення

Цей крок записує, що зміна мусить гарантувати і що, на вашу думку, покаже справжній прогін, ще до появи коду. Передбачення, написані після прогону, — не передбачення.

Створіть `docs/evidence/M05-T08-managed-multilingual-embeddings.md` за цим шаблоном, а потім своїми словами дайте відповіді на запитання про передбачення:

```markdown
# M05-T08 — Managed multilingual embeddings evidence

## Invariant

`compare_branches` stores and embeds the bilingual seed corpus once, in an
empty database, with one embedding model. It then asks every case as one
ordinary reader three ways: lexical only, dense only, and both fused. No
Chunk from `restricted/` is ever returned, even when it is the nearest
vector. The report gives Recall@k and MRR per language direction and query
class, names the configuration, and holds no Question or Document text.

## Expected failing check

Before the change, `uv run pytest tests/test_ranking_metrics.py` fails to
import `knowledge_service.retrieval.ranking_metrics`.

## Predictions

- Which slice will gain the most from dense search, and why?
- Will dense search find the bare identifier question "VPN-4031" at rank 1?
- Will dense search fix the morphology misses, such as "Хто оплачує
  лікарняні?" against "Оплата лікарняного"?
- Will merging both searches beat the better single search on every slice?
- If you ran the same thing twice on fresh databases, would the report be
  byte-identical?

## Design

## Verification

## Reflection
```

Перевірка: файл існує, і на кожне передбачення є відповідь і пояснення в один рядок.

### Крок 2 — Програмування: порахуйте Recall@k і MRR

Цей крок пише дві оцінки як невеликий модуль чистих функцій, без бази даних і без моделі.

**Простими словами:** маючи місця правильних уривків запитання, ці функції кажуть, яка їх частка потрапила в перші k і наскільки високо опинився перший. Третя функція рахує середнє однієї оцінки за багатьма запитаннями. Безглуздий вхід, як-от місце 0 чи порожній список, вони відхиляють однією зрозумілою помилкою.

| Функція | Вхід | Повертає |
|---|---|---|
| `recall_at(ranks, k)` | одне місце на кожен правильний уривок, `None` для промаху; поріг `k` | частку з місцем `k` або кращим |
| `reciprocal_rank(ranks)` | ті самі місця | 1 / найкраще місце, або 0 |
| `mean(scores)` | одна оцінка на кожне запитання | їхнє точне середнє |
| `InvalidMetricInput` | кидається для порожнього списку, місця менше 1, `bool`, дробового числа, рядка або поганого `k` | |

Створіть `src/knowledge_service/retrieval/ranking_metrics.py`:

```python
"""Recall@k and MRR: how well one ranked list found the relevant Chunks.

Both scores start from the same input: for one case, the rank of each
relevant Chunk in the list a search returned, counted from 1, or None when
the Chunk was not returned at all.

- **Recall@k** is the share of a case's relevant Chunks found in the top
  `k`. A case with two relevant Chunks, ranked 1 and missed, has Recall@5
  of 1/2.
- **Reciprocal rank** is 1 divided by the rank of the first relevant Chunk,
  or 0 when none was found. Ranked 1 gives 1, ranked 4 gives 1/4. The mean
  over many cases is the MRR (mean reciprocal rank).

Scores are exact fractions, so two runs with equal ranks always give equal
scores, and a mean never drifts in the last digit. Bad input raises
`InvalidMetricInput`, whose message never repeats a value.
"""

from collections.abc import Sequence
from fractions import Fraction
from typing import cast


class InvalidMetricInput(ValueError):
    """A rank list or a cut-off breaks the rules of the metric."""


def recall_at(ranks: Sequence[int | None], k: int) -> Fraction:
    """Return the share of relevant Chunks ranked at `k` or better."""
    _check_ranks(ranks)
    # bool is an int, so `True` would otherwise pass as a cut-off of 1.
    if type(k) is not int or k < 1:
        raise InvalidMetricInput("k must be a positive int")
    found = sum(1 for rank in ranks if rank is not None and rank <= k)
    return Fraction(found, len(ranks))


def reciprocal_rank(ranks: Sequence[int | None]) -> Fraction:
    """Return 1 / the best rank of a relevant Chunk, or 0 if none was found."""
    _check_ranks(ranks)
    found = [rank for rank in ranks if rank is not None]
    return Fraction(1, min(found)) if found else Fraction(0)


def mean(scores: Sequence[Fraction]) -> Fraction:
    """Return the exact average of one score over several cases."""
    # Checked at run time too, because a caller may pass any object.
    if isinstance(scores, (str, bytes)) or not isinstance(
        cast(object, scores), Sequence
    ):
        raise InvalidMetricInput("scores must be a sequence of fractions")
    if not scores:
        raise InvalidMetricInput("a mean needs at least one score")
    if any(type(score) is not Fraction for score in scores):
        raise InvalidMetricInput("every score must be a Fraction")
    return sum(scores, Fraction(0)) / len(scores)


def _check_ranks(ranks: Sequence[int | None]) -> None:
    # A string is a sequence too, of characters, not ranks.
    if isinstance(ranks, (str, bytes)) or not isinstance(cast(object, ranks), Sequence):
        raise InvalidMetricInput("ranks must be a sequence")
    if not ranks:
        raise InvalidMetricInput("a case needs at least one relevant Chunk")
    for rank in ranks:
        if rank is not None and (type(rank) is not int or rank < 1):
            raise InvalidMetricInput("a rank must be None or an int from 1")
```

Пояснення до менш очевидних рядків:

- `type(k) is not int` відхиляє `True`, бо в Python `bool` — різновид `int`, і він пройшов би як 1.
- `cast(object, ranks)` каже перевірці типів, що перевірка навмисна. Під час виконання викликач може передати що завгодно, хоча анотація каже `Sequence`.
- Рядок — це послідовність символів, тому його відхиляють першим. Інакше `"12"` виглядав би як два місця.

Тепер створіть `tests/test_ranking_metrics.py`. Фіксовані приклади закріплюють визначення. Два тести властивостей використовують Hypothesis, щоб перебрати сотні випадкових списків місць. Вони перевіряють, що Recall лишається між 0 і 1, не падає з ростом k і погоджується з оберненим місцем щодо того, чи знайшлося щось у перших k.

```python
"""Recall@k and MRR: exact scores from the ranks of relevant Chunks."""

from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from knowledge_service.retrieval.ranking_metrics import (
    InvalidMetricInput,
    mean,
    recall_at,
    reciprocal_rank,
)

ranks_lists = st.lists(
    st.one_of(st.none(), st.integers(min_value=1, max_value=50)),
    min_size=1,
    max_size=6,
)


@pytest.mark.parametrize(
    ("ranks", "k", "expected"),
    [
        ((1,), 1, Fraction(1)),
        ((2,), 1, Fraction(0)),
        ((2,), 5, Fraction(1)),
        ((None,), 10, Fraction(0)),
        ((1, None), 5, Fraction(1, 2)),
        ((3, 7), 5, Fraction(1, 2)),
        ((3, 7), 10, Fraction(1)),
    ],
)
def test_recall_counts_the_relevant_chunks_in_the_top_k(
    ranks: tuple[int | None, ...], k: int, expected: Fraction
) -> None:
    assert recall_at(ranks, k) == expected


@pytest.mark.parametrize(
    ("ranks", "expected"),
    [
        ((1,), Fraction(1)),
        ((4,), Fraction(1, 4)),
        ((None,), Fraction(0)),
        ((None, 3, 2), Fraction(1, 2)),
    ],
)
def test_reciprocal_rank_uses_the_best_relevant_rank(
    ranks: tuple[int | None, ...], expected: Fraction
) -> None:
    assert reciprocal_rank(ranks) == expected


def test_the_mean_is_exact() -> None:
    scores = [Fraction(1), Fraction(1, 3), Fraction(0)]

    assert mean(scores) == Fraction(4, 9)


@pytest.mark.parametrize(
    "ranks",
    [(), (0,), (-1,), (True,), (1.0,), ("1",), "12", None],
)
def test_a_bad_rank_list_is_refused(ranks: object) -> None:
    with pytest.raises(InvalidMetricInput):
        recall_at(ranks, 5)  # pyright: ignore[reportArgumentType]
    with pytest.raises(InvalidMetricInput):
        reciprocal_rank(ranks)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("k", [0, -3, True, 2.0, None])
def test_a_bad_cutoff_is_refused(k: object) -> None:
    with pytest.raises(InvalidMetricInput):
        recall_at((1,), k)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("scores", [[], [0.5], [1], "ab", None])
def test_a_bad_list_of_scores_is_refused(scores: object) -> None:
    with pytest.raises(InvalidMetricInput):
        mean(scores)  # pyright: ignore[reportArgumentType]


@given(ranks=ranks_lists, k=st.integers(min_value=1, max_value=60))
def test_recall_stays_between_zero_and_one_and_grows_with_k(
    ranks: list[int | None], k: int
) -> None:
    recall = recall_at(ranks, k)

    assert Fraction(0) <= recall <= Fraction(1)
    assert recall_at(ranks, k + 1) >= recall


@given(ranks=ranks_lists, k=st.integers(min_value=1, max_value=60))
def test_a_found_chunk_in_the_top_k_shows_in_both_scores(
    ranks: list[int | None], k: int
) -> None:
    # Recall@k is above 0 exactly when the best rank is k or better, which
    # is exactly when the reciprocal rank is at least 1/k.
    assert (recall_at(ranks, k) > 0) == (reciprocal_rank(ranks) >= Fraction(1, k))
```

Перевірка: `uv run pytest tests/test_ranking_metrics.py -q` дає `32 passed`.

### Крок 3 — Програмування: підготуйте модуль набору

Цей крок додає імена, якими користуються два наступні кроки: імпорти, кілька констант, дві нові помилки і місце у звіті для моделі та оцінок. Поки що нічого не працює інакше.

**Простими словами:** модулю набору треба знати про моделі ембедингів, збережені вектори і нові оцінки. Цей крок їх підключає і додає два способи, якими прогін може чисто зупинитися: корпус не вдалося вбудувати або запитання кинуло неочікувану помилку. Звіт вчиться пам'ятати, яку модель він використав, а новий запис зберігає оцінки одного зрізу.

| Додано | Для чого |
|---|---|
| `RECALL_CUTOFFS` | значення k, на яких показують Recall |
| `EMBEDDING_CONCURRENCY`, `EMBEDDING_TIMEOUT_SECONDS` | скільки запитів вбудовують корпус одночасно і скільки часу може тривати кожен |
| `SPACE_ID` | фіксований ID простору ембедингів прогону, як і фіксовані ID джерел |
| `CaseFailed` | запитання кинуло помилку, якої не повинен кидати жоден пошук |
| `CorpusNotEmbedded` | принаймні один документ не отримав векторів, тож жодне запитання не виконувалося |
| `DatasetReport.embedding` | модель, що стоїть за щільним пошуком, або `None` |
| `SliceScore` | Recall на кожному порозі і MRR для одного зрізу |

Усі правки — у `src/knowledge_service/retrieval/evaluation_dataset.py`. Робіть їх по черзі; кожен текст «Знайдіть» трапляється рівно один раз.

Спочатку docstring модуля, щоб він описував нову поведінку:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
  missing. A Question no branch can answer raises `CaseUnanswered`.
- **Eligible.** A Candidate from `restricted/` raises
  `IneligibleChunkRetrieved` and stops the run, however relevant it is.
- **Quiet.** Reports and errors name cases by ID and give ranks only; they
  never hold a Question or Document text.
"""
```

Замініть на:

```python
  missing. A Question no branch can answer raises `CaseUnanswered`.
- **Compared with embeddings.** `compare_branches` also embeds every
  Chunk with one embedding model, then runs every case three times: words
  only, meaning only, and both fused. A Chunk that cannot be embedded
  raises `CorpusNotEmbedded` before any case runs. Every Chunk, restricted
  ones included, is sent to the embedding model's provider.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
  MRR for every language direction and query class (see
  `retrieval.ranking_metrics`).
- **Eligible.** A Candidate from `restricted/` raises
  `IneligibleChunkRetrieved` and stops the run, however relevant it is.
- **Typed.** Any error a case raises becomes `CaseFailed`, naming the case
  and the error types only.
- **Quiet.** Reports and errors name cases by ID and give ranks only; they
  never hold a Question or Document text, not even in a linked error.
"""
```

Потім імпорти. `Fraction` зберігає точні оцінки:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
from enum import StrEnum
```

Замініть на:

```python
from enum import StrEnum
from fractions import Fraction
```

Модель ембедингів, її публікація і ID простору ембедингів:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
from knowledge_service.identifiers import SourceId, UserId
```

Замініть на:

```python
from knowledge_service.embeddings.port import EmbeddingModel, EmbeddingModelIdentity
from knowledge_service.embeddings.publication import (
    EmbeddingPublicationStatus,
    publish_document_embeddings,
)
from knowledge_service.identifiers import EmbeddingSpaceId, SourceId, UserId
```

Збережені документи і простори, щільний пошук і нові оцінки:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
from knowledge_service.persistence.schema import sources
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    BranchSearch,
    HybridRetrievalUnavailable,
    lexical_branch,
    retrieve_hybrid,
)
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
```

Замініть на:

```python
from knowledge_service.persistence.documents import load_available_documents
from knowledge_service.persistence.embeddings import insert_embedding_space
from knowledge_service.persistence.schema import embedding_spaces, sources
from knowledge_service.retrieval.fusion import DEFAULT_RRF_K, RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    BranchSearch,
    HybridRetrieval,
    HybridRetrievalUnavailable,
    dense_branch,
    lexical_branch,
    retrieve_hybrid,
)
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.ranking_metrics import (
    mean,
    recall_at,
    reciprocal_rank,
)
```

Константи:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
PAGE_SIZE = 100
```

Замініть на:

```python
PAGE_SIZE = 100
# Recall is reported at these cut-offs, as far as the run's limit allows.
RECALL_CUTOFFS = (1, 3, 5, 10)
EMBEDDING_CONCURRENCY = 2
EMBEDDING_TIMEOUT_SECONDS = 60.0
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
_REQUEST_IDS = UUID(int=0x5EED_0004)
```

Замініть на:

```python
_REQUEST_IDS = UUID(int=0x5EED_0004)
SPACE_ID = EmbeddingSpaceId(UUID(int=0x5EED_0005))
```

Дві нові помилки:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
class CaseUnanswered(DatasetError):
    """No branch answered one case, so the run cannot score it."""
```

Замініть на:

```python
class CaseUnanswered(DatasetError):
    """No branch answered one case, so the run cannot score it."""


class CaseFailed(DatasetError):
    """A case raised an error no branch should raise; the run stopped."""


class CorpusNotEmbedded(DatasetError):
    """At least one Document of the corpus has no vectors, so no case ran."""
```

Нове поле звіту і оцінка зрізу. `embedding` має значення за замовчуванням, тож кожен наявний `DatasetReport(...)` у тестах і далі працює:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    branches: tuple[RetrievalBranch, ...]
    outcomes: tuple[CaseOutcome, ...]
```

Замініть на:

```python
    branches: tuple[RetrievalBranch, ...]
    outcomes: tuple[CaseOutcome, ...]
    # The model behind the dense branch; None when the run had no dense branch.
    embedding: EmbeddingModelIdentity | None = None


@dataclass(frozen=True, slots=True)
class SliceScore:
    """Recall@k and MRR over the cases of one slice.

    `recall` pairs each cut-off with the mean Recall at it. A slice with no
    cases has no scores: `recall` is empty and `mrr` is None.
    """

    name: str
    cases: int
    found: int
    degraded: int
    recall: tuple[tuple[int, Fraction], ...]
    mrr: Fraction | None
```

Перевірка: `uv run pytest tests/test_evaluation_dataset.py -q` і далі дає `63 passed`. `make lint` і `make typecheck` скаржаться на невикористані імпорти, доки кроки 4 і 5 їх не використають; поки що так і має бути.

### Крок 4 — Програмування: вбудуйте корпус один раз і проженіть трьома способами

Цей крок пише `compare_branches`, функцію, яку викличе скрипт, і кроки, які їй потрібні.

**Простими словами:** порівняння спершу перевіряє свої налаштування, потім зберігає корпус у порожній базі даних і створює один простір ембедингів для моделі. Далі воно дає вектори уривкам кожного документа, закритим теж. Лише тоді воно ставить кожне запитання тричі: пошуком за словами, пошуком за змістом і обома разом. Будь-яка помилка дорогою зупиняє прогін помилкою, що називає тип, але ніколи не текст.

| Частина | Що робить |
|---|---|
| `compare_branches` | перевіряє ліміт і модель, звіряє корпус, зберігає, вбудовує і повертає три звіти в незмінному порядку |
| `_ingest(..., embedding=...)` | з моделлю ще й відхиляє базу даних, де вже є простір ембедингів, а потім створює простір прогону як поточний |
| `_embed_corpus` | викликає `publish_document_embeddings` для кожного збереженого документа; усе, крім `published`, кидає `CorpusNotEmbedded` |
| `_run_cases` | ставить кожен випадок з одним набором пошуків і будує один звіт |
| `_run_case` | тепер перетворює будь-яку неочікувану помилку на `CaseFailed` |
| `_error_names` | називає помилку її типом, а групу помилок — типами всередині неї |
| `_identity` | читає ідентичність моделі один раз, ще до будь-якого запису |

Замініть кінець `run_dataset` і додайте після неї `compare_branches`. `run_dataset` зберігає свою поведінку; тепер вона ділить `_run_cases` з порівнянням:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    await _ingest(runtime, dataset, corpus, observed_at)
    outcomes = [await _run_case(case, searches, limit) for case in dataset.cases]
    return DatasetReport(
        name=dataset.name,
        version=dataset.version,
        limit=limit,
        branches=tuple(branch for branch in RetrievalBranch if branch in searches),
        outcomes=tuple(outcomes),
    )
```

Замініть на:

```python
    await _ingest(runtime, dataset, corpus, observed_at, embedding=None)
    return await _run_cases(dataset, searches, limit, embedding=None)


async def compare_branches(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: EmbeddingModel,
    limit: int = DEFAULT_LIMIT,
) -> tuple[DatasetReport, ...]:
    """Ingest and embed the corpus once, then run every case three ways.

    Returns three reports in a fixed order: lexical only, dense only, and
    both fused. Every Chunk, restricted ones included, is embedded with
    `model` into one new current Embedding Space, because search must be
    able to rank a restricted Chunk for the permission check to matter.
    Raises like `run_dataset`; `DatasetRunRefused` also when the database
    already holds an Embedding Space or `model` has no valid identity, and
    `CorpusNotEmbedded` when any Document cannot be embedded. Questions are
    embedded again in the fused run, so its dense ranks can differ slightly
    from the dense-only run when the provider is not exactly repeatable.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    identity = _identity(model)
    await verify_corpus(dataset, corpus)
    await _ingest(runtime, dataset, corpus, observed_at, embedding=identity)
    await _embed_corpus(runtime, model, observed_at)
    lexical = lexical_branch(runtime)
    dense = dense_branch(runtime, model)
    setups: tuple[Mapping[RetrievalBranch, BranchSearch], ...] = (
        {RetrievalBranch.LEXICAL: lexical},
        {RetrievalBranch.DENSE: dense},
        {RetrievalBranch.DENSE: dense, RetrievalBranch.LEXICAL: lexical},
    )
    return tuple(
        [
            await _run_cases(dataset, searches, limit, embedding=identity)
            for searches in setups
        ]
    )
```

Передайте `_ingest` ідентичність моделі:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    corpus: Path,
    observed_at: datetime,
) -> None:
```

Замініть на:

```python
    corpus: Path,
    observed_at: datetime,
    *,
    embedding: EmbeddingModelIdentity | None,
) -> None:
```

Усередині `_ingest`, одразу після перевірки наявних джерел, відхиліть наявний простір і створіть власний простір прогону. Це відбувається в тій самій заблокованій транзакції, тож два прогони, запущені разом, не можуть обидва пройти перевірку:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
            raise DatasetRunRefused("the dataset runs only on an empty database")
        for folder, source_id in SOURCE_IDS.items():
```

Замініть на:

```python
            raise DatasetRunRefused("the dataset runs only on an empty database")
        # Another space would hold vectors this run did not make, or be the
        # current one and turn every dense search away.
        if embedding is not None and await connection.scalar(
            sa.select(sa.exists().select_from(embedding_spaces))
        ):
            raise DatasetRunRefused("the dataset runs only on an empty database")
        if embedding is not None:
            await insert_embedding_space(
                connection,
                space_id=SPACE_ID,
                identity=embedding,
                is_current=True,
                created_at=observed_at,
            )
        for folder, source_id in SOURCE_IDS.items():
```

Додайте `_embed_corpus` і `_run_cases` одразу перед `_run_case`. Помилку кидають після завершення блоку `except`, а не всередині нього. Python пов'язує помилку, кинуту всередині `except`, з тією, що обробляється, і traceback друкує обидві. Власна помилка моделі може цитувати уривок:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
async def _run_case(
```

Замініть на:

```python
async def _embed_corpus(
    runtime: DatabaseRuntime, model: EmbeddingModel, observed_at: datetime
) -> None:
    async with runtime.transaction() as connection:
        stored = [
            document
            for source_id in SOURCE_IDS.values()
            for document in await load_available_documents(
                connection, source_id=source_id
            )
        ]
    for document in stored:
        failure: str
        try:
            publication = await publish_document_embeddings(
                runtime,
                model,
                document_id=document.document_id,
                concurrency=EMBEDDING_CONCURRENCY,
                request_id=uuid5(_REQUEST_IDS, f"embed {document.document_id.value}"),
                timeout_seconds=EMBEDDING_TIMEOUT_SECONDS,
                now=observed_at,
            )
            failure = str(publication.status)
        except Exception as error:
            # Any error, not only an EmbeddingError: the model is outside
            # code. Its type only, because its message may quote Chunk text.
            failure = _error_names(error)
        # Raised outside the `except`, so no linked error carries the text.
        if failure != EmbeddingPublicationStatus.PUBLISHED:
            raise CorpusNotEmbedded(f"the corpus could not be embedded: {failure}")


async def _run_cases(
    dataset: EvaluationDataset,
    searches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
    *,
    embedding: EmbeddingModelIdentity | None,
) -> DatasetReport:
    outcomes = [await _run_case(case, searches, limit) for case in dataset.cases]
    return DatasetReport(
        name=dataset.name,
        version=dataset.version,
        limit=limit,
        branches=tuple(branch for branch in RetrievalBranch if branch in searches),
        outcomes=tuple(outcomes),
        embedding=embedding if RetrievalBranch.DENSE in searches else None,
    )


async def _run_case(
```

Тепер `_run_case`. Результат пошуку починається як `None`, тож код після `try` може зрозуміти, чи пошук вдався:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
) -> CaseOutcome:
    try:
```

Замініть на:

```python
) -> CaseOutcome:
    result: HybridRetrieval | None = None
    unexpected = "no result"
    try:
```

Перехопіть будь-яку іншу помилку пошуку. `retrieve_hybrid` запускає пошуки в групі задач, тож баг у пошуку приходить як `ExceptionGroup`, чиє повідомлення може містити запитання:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
        ) from None
    ranks: dict[tuple[str, str | None], int] = {}
```

Замініть на:

```python
        ) from None
    except Exception as error:
        # A bug in a branch arrives in an ExceptionGroup whose message may
        # repeat the Question. Only the error types leave this function.
        unexpected = _error_names(error)
    # Raised outside the `except`, so no linked error carries the Question.
    if result is None:
        raise CaseFailed(f"case {case.case_id}: the search raised {unexpected}")
    ranks: dict[tuple[str, str | None], int] = {}
```

Нарешті, дві допоміжні функції, одразу перед `_unique_keys`:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
```

Замініть на:

```python
def _error_names(error: BaseException) -> str:
    """Name an error by its type; a group by the types inside it."""
    if isinstance(error, BaseExceptionGroup):
        inner = cast(BaseExceptionGroup[BaseException], error).exceptions
        return ", ".join(sorted({_error_names(item) for item in inner}))
    return type(error).__name__


def _identity(model: EmbeddingModel) -> EmbeddingModelIdentity:
    # Read once here, so a model that cannot name itself is refused before
    # anything is written.
    try:
        identity = model.identity
    except Exception:
        identity = None
    if type(identity) is not EmbeddingModelIdentity:
        raise DatasetRunRefused("the model must have an EmbeddingModelIdentity")
    return identity


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
```

Перевірка: `uv run pytest tests/test_evaluation_dataset.py -q` і далі дає `63 passed`. `make typecheck` тепер показує рівно чотири невикористані імпорти, `DEFAULT_RRF_K`, `mean`, `recall_at` і `reciprocal_rank`, які використає крок 5.

### Крок 5 — Програмування: оцініть зрізи і надрукуйте конфігурацію

Цей крок перетворює місця на оцінки для кожного зрізу і додає їх до звіту, з блоком конфігурації вгорі.

**Простими словами:** для кожного зрізу (усі запитання, кожен мовний напрям, кожен вид запитання) звіт тепер показує, скільки там запитань, скільки з них щось знайшли, Recall на кожному порозі, MRR і скільки прогонів були неповними. Сторінка порівняння спершу ставить три прогони поруч, а потім показує кожен повний звіт. Кожна сторінка починається з того, які пошуки працювали, яка модель і скільки результатів залишало кожне запитання.

| Функція | Що робить |
|---|---|
| `score_slices(report)` | один `SliceScore` для `all`, кожного напряму і кожного класу запитань |
| `render_comparison(reports)` | таблиця Recall і MRR пліч-о-пліч, потім кожен повний звіт |
| `render_report(report)` | як раніше, плюс блок конфігурації і стовпці оцінок |
| `_configuration` | пошуки, ліміт, модель і налаштування об'єднання як рядки списку |
| `_score` | оцінки одного зрізу; зріз без запитань отримує `n/a` |

Додайте `score_slices` і `render_comparison` одразу перед `render_report`:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
def render_report(report: DatasetReport) -> str:
```

Замініть на:

```python
def score_slices(report: DatasetReport) -> tuple[SliceScore, ...]:
    """Score every case together, then each direction, then each query class."""
    cutoffs = tuple(k for k in RECALL_CUTOFFS if k <= report.limit)
    groups: list[tuple[str, list[CaseOutcome]]] = [("all", list(report.outcomes))]
    groups += [
        (direction, [o for o in report.outcomes if o.direction == direction])
        for direction in DIRECTIONS
    ]
    groups += [
        (str(query_class), [o for o in report.outcomes if o.query_class is query_class])
        for query_class in QueryClass
    ]
    return tuple(_score(name, outcomes, cutoffs) for name, outcomes in groups)


def render_comparison(reports: tuple[DatasetReport, ...]) -> str:
    """Return a side-by-side summary of several runs, then each full report."""
    if not reports:
        raise ValueError("at least one report is required")
    first = reports[0]
    scored = [score_slices(report) for report in reports]
    cutoff = max(k for k in RECALL_CUTOFFS if k <= first.limit)
    lines = [
        f"# Retrieval comparison: {first.name} v{first.version}",
        "",
        *_configuration(reports),
        "",
        f"## Recall@{cutoff} and MRR by slice",
        "",
        "| Slice | Cases | "
        + " | ".join(
            f"{_label(report)} R@{cutoff} | {_label(report)} MRR" for report in reports
        )
        + " |",
        "|---|---:|" + "---:|---:|" * len(reports),
    ]
    for index, first_score in enumerate(scored[0]):
        cells = [f"{first_score.name} | {first_score.cases}"]
        for scores in scored:
            score = scores[index]
            recall = dict(score.recall).get(cutoff)
            cells.append(f"{_number(recall)} | {_number(score.mrr)}")
        lines.append("| " + " | ".join(cells) + " |")
    body = "\n".join(lines) + "\n"
    return body + "".join("\n" + render_report(report) for report in reports)


def render_report(report: DatasetReport) -> str:
```

У `render_report` замініть старий заголовок і таблицю зрізів:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    branches = ", ".join(report.branches)
    lines = [
        f"# Retrieval dataset report: {report.name} v{report.version}",
        "",
        f"Branches: {branches}. Each case keeps its top {report.limit} Candidates;"
        " a rank is where a relevant Chunk appears among them.",
        "",
        "## By slice",
        "",
        "| Slice | Cases | Found |",
        "|---|---:|---:|",
    ]
    slices: list[tuple[str, list[CaseOutcome]]] = [
        (direction, [o for o in report.outcomes if o.direction == direction])
        for direction in DIRECTIONS
    ]
    slices += [
        (str(query_class), [o for o in report.outcomes if o.query_class is query_class])
        for query_class in QueryClass
    ]
    for name, outcomes in slices:
        found = sum(outcome.found for outcome in outcomes)
        lines.append(f"| {name} | {len(outcomes)} | {found} |")
```

Замініть на:

```python
    scores = score_slices(report)
    cutoffs = [k for k, _ in scores[0].recall]
    lines = [
        f"# Retrieval dataset report: {report.name} v{report.version}",
        "",
        *_configuration((report,)),
        "",
        "## By slice",
        "",
        "| Slice | Cases | Found | "
        + "".join(f"Recall@{k} | " for k in cutoffs)
        + "MRR | Degraded |",
        "|---|---:|---:|" + "---:|" * len(cutoffs) + "---:|---:|",
    ]
    for score in scores:
        recall = "".join(f"{_number(value)} | " for _, value in score.recall)
        if score.cases == 0:
            recall = "n/a | " * len(cutoffs)
        lines.append(
            f"| {score.name} | {score.cases} | {score.found} | {recall}"
            f"{_number(score.mrr)} | {score.degraded} |"
        )
```

Додайте допоміжні функції форматування одразу перед `_error_names`:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
def _error_names(error: BaseException) -> str:
```

Замініть на:

```python
def _configuration(reports: tuple[DatasetReport, ...]) -> list[str]:
    first = reports[0]
    if len(reports) == 1:
        runs = f"- Branches: {', '.join(first.branches)}."
    else:
        runs = f"- Runs: {'; '.join(_label(report) for report in reports)}."
    lines = [
        runs,
        f"- Each case keeps its top {first.limit} Candidates; a rank is where"
        " a relevant Chunk appears among them.",
    ]
    model = next((r.embedding for r in reports if r.embedding is not None), None)
    if model is not None:
        lines.append(
            f"- Embedding model: {model.provider} {model.model},"
            f" {model.dimensions} dimensions."
        )
    if any(len(report.branches) > 1 for report in reports):
        lines.append(f"- Fusion: Reciprocal Rank Fusion, k = {DEFAULT_RRF_K}.")
    return lines


def _label(report: DatasetReport) -> str:
    return " + ".join(report.branches)


def _number(value: Fraction | None) -> str:
    return "n/a" if value is None else f"{float(value):.3f}"


def _score(
    name: str, outcomes: list[CaseOutcome], cutoffs: tuple[int, ...]
) -> SliceScore:
    found = sum(outcome.found for outcome in outcomes)
    degraded = sum(outcome.degraded for outcome in outcomes)
    if not outcomes:
        return SliceScore(name, 0, 0, 0, recall=(), mrr=None)
    recall = tuple(
        (k, mean([recall_at(outcome.ranks, k) for outcome in outcomes]))
        for k in cutoffs
    )
    mrr = mean([reciprocal_rank(outcome.ranks) for outcome in outcomes])
    return SliceScore(name, len(outcomes), found, degraded, recall=recall, mrr=mrr)


def _error_names(error: BaseException) -> str:
```

Перевірка: `uv run pytest tests/test_evaluation_dataset.py -q` дає `63 passed`. Старий тест звіту і далі проходить, бо кожен старий рядок, як-от `| en->en | 2 | 1 |`, і далі є початком нового, довшого рядка. Запустіть `make lint` і `make typecheck`: обидва знову чисті.

### Крок 6 — Програмування: додайте до скрипту параметр керованих ембедингів

Цей крок дає змогу запускати порівняння з командного рядка з OpenAI.

**Простими словами:** без нового параметра скрипт поводиться як раніше: лише пошук за словами, один звіт. З `--managed-embeddings` він будує модель ембедингів OpenAI з ваших налаштувань, запускає порівняння і пише сторінку пліч-о-пліч в окремий файл. Без ключа API він відмовляється запускатися. Режиму лише перевірки, як і раніше, налаштування не потрібні взагалі.

| Зміна | Чому |
|---|---|
| `--managed-embeddings` | запускає `compare_branches` з `OpenAIEmbeddingModel` |
| `Settings()` читається після `--verify-only` | зламане налаштування не може зупинити офлайн-перевірку |
| ключ береться з `KNOWLEDGE_SERVICE_MODEL_API_KEY` | те саме налаштування, що й для чат-моделі; його ніколи не друкують |
| `artifacts/evaluations/bilingual-seed-managed.md` | окремий файл звіту, який Git ігнорує, як і перший |

Модель і кількість її вимірів беруться з `OpenAIEmbeddingSettings`, яка читає `EMBEDDING_MODEL` і `EMBEDDING_DIMENSIONS` із середовища. За замовчуванням це `text-embedding-3-small` і 1536. Замініть увесь `scripts/run_retrieval_dataset.py` на:

```python
"""Verify the bilingual seed dataset and, given a database, run every case.

`--verify-only` checks the cases against the corpus and needs no database.
Otherwise point `KNOWLEDGE_SERVICE_DATABASE_URL` at an empty, migrated
database; the run refuses any other. By default only word search runs and
the report goes to `artifacts/evaluations/bilingual-seed.md`.

`--managed-embeddings` also embeds the corpus with the OpenAI model set by
`EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` (default
`text-embedding-3-small`, 1536), using `KNOWLEDGE_SERVICE_MODEL_API_KEY`.
It runs every case three ways, words only, meaning only, and both fused, and
writes `artifacts/evaluations/bilingual-seed-managed.md`. Git ignores both
reports. Every Chunk, the restricted ones included, is sent to OpenAI; the
seed corpus is made up, so nothing private leaves the machine.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from openai import AsyncOpenAI
from pydantic import SecretStr

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.openai_adapter import (
    OpenAIEmbeddingModel,
    OpenAIEmbeddingSettings,
)
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DEFAULT_LIMIT,
    DatasetError,
    EvaluationDataset,
    compare_branches,
    load_dataset,
    render_comparison,
    render_report,
    run_dataset,
    verify_corpus,
)
from knowledge_service.settings import Settings

REPORT = Path("artifacts/evaluations/bilingual-seed.md")
MANAGED_REPORT = Path("artifacts/evaluations/bilingual-seed-managed.md")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_DIRECTORY)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--managed-embeddings", action="store_true")
    arguments = parser.parse_args()
    corpus = arguments.dataset / CORPUS_DIRECTORY
    try:
        dataset = load_dataset(arguments.dataset / CASES_FILE)
        if arguments.verify_only:
            asyncio.run(verify_corpus(dataset, corpus))
            sys.stdout.write(f"{len(dataset.cases)} cases verified\n")
            return 0
        # Read only for a run, so a bad setting cannot stop the check above.
        settings = Settings()
        api_key = settings.model_api_key
        if arguments.managed_embeddings and api_key is None:
            sys.stderr.write(
                "set KNOWLEDGE_SERVICE_MODEL_API_KEY to embed the corpus\n"
            )
            return 1
        runtime = create_database_runtime(settings)
        rendered = asyncio.run(
            _run(
                runtime,
                dataset,
                corpus,
                arguments.limit,
                api_key if arguments.managed_embeddings else None,
            )
        )
    except DatasetError as error:
        sys.stderr.write(f"dataset run failed: {error}\n")
        return 1
    default = MANAGED_REPORT if arguments.managed_embeddings else REPORT
    output: Path = arguments.output or default
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"{rendered}\nFull report: {output}\n")
    return 0


async def _run(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    limit: int,
    api_key: SecretStr | None,
) -> str:
    observed_at = datetime.now(UTC)
    try:
        if api_key is None:
            report = await run_dataset(
                runtime, dataset, corpus, observed_at=observed_at, limit=limit
            )
            return render_report(report)
        client = AsyncOpenAI(api_key=api_key.get_secret_value())
        try:
            model = OpenAIEmbeddingModel(client, OpenAIEmbeddingSettings())
            reports = await compare_branches(
                runtime,
                dataset,
                corpus,
                observed_at=observed_at,
                model=model,
                limit=limit,
            )
        finally:
            await client.close()
        return render_comparison(reports)
    finally:
        await runtime.dispose()


if __name__ == "__main__":
    sys.exit(main())
```

Перевірка: `uv run python scripts/run_retrieval_dataset.py --verify-only` друкує `16 cases verified`. Без ключа API `uv run python scripts/run_retrieval_dataset.py --managed-embeddings` друкує `set KNOWLEDGE_SERVICE_MODEL_API_KEY to embed the corpus` і завершується з кодом 1.

### Крок 7 — Програмування: протестуйте оцінки і порівняння

Цей крок додає тести, яких вимагає завдання: успіх, найризикованіший крайній випадок і типізовані помилки. Вони використовують `FakeEmbeddingModel` з `tests/fakes.py`, тож їм не потрібна мережа, і вони щоразу дають ті самі вектори.

**Простими словами:** модульні тести перевіряють оцінки і звіт на невеликому прогоні, складеному вручну, і перевіряють, що погані налаштування відхиляються ще до звернення до бази даних. Тести з базою даних проганяють усе порівняння на справжньому базовому корпусі з фальшивою моделлю. Вони перевіряють, що всі три прогони завершуються і що закритий уривок ніколи не повертається, навіть коли він найближчий збіг. Ще вони перевіряють, що модель, що ламається, або зламаний пошук зупиняють прогін чистою помилкою і що текст запитань чи уривків ніде не просочується.

Фальшива модель хешує кожен текст у вектор. Її вектори не мають змісту, тож щільні місця в цих тестах випадкові. Важливо те, що однакові тексти отримують однакові вектори. Тест найризикованішого випадку цим користується: він вбудовує власний текст закритого уривка як запитання, тож цей уривок стоїть на відстані 0, найкращому можливому збігу.

| Тест | Доводить |
|---|---|
| `test_slices_get_exact_recall_and_mrr` | точні Recall і MRR для кожного зрізу; пороги понад ліміт пропущено; порожні зрізи не мають оцінок |
| `test_the_report_shows_the_configuration_and_the_scores`, `test_the_comparison_puts_every_run_side_by_side` | надруковані числа і конфігурація збігаються з оцінками |
| `test_a_wrong_comparison_setting_is_refused_before_any_work` | поганий ліміт або модель без ідентичності кидають `DatasetRunRefused` без жодного з'єднання |
| `test_verify_only_needs_no_settings` | режим лише перевірки витримує зламане налаштування |
| `test_every_case_runs_three_ways_and_the_report_names_no_text` | успіх: три звіти по черзі, кожен уривок вбудовано, місця пошуку за словами не змінилися, у звіті і логах DEBUG немає тексту |
| `test_the_nearest_chunk_is_restricted_and_still_never_returned` | найризикованіший крайній випадок: без перевірки дозволів закритий уривок на першому місці; з нею читач отримує лише 19 відкритих уривків |
| `test_a_model_that_fails_stops_the_run_before_any_case` | типізована помилка: `CorpusNotEmbedded`, векторів не збережено, жодного запитання не надіслано, до помилки нічого не прив'язано |
| `test_a_model_that_breaks_during_embedding_raises_a_typed_error` | модель, чия ідентичність ламається після першого читання, усе одно дає `CorpusNotEmbedded` |
| `test_a_database_with_an_embedding_space_is_refused_before_any_write` | наявний простір відхиляють ще до запису будь-якого джерела |
| `test_an_unexpected_error_in_a_case_is_typed_and_quiet` | баг пошуку стає `CaseFailed`, що називає випадок і `RuntimeError`, а запитання немає ніде в ланцюжку помилок |

У `tests/test_evaluation_dataset.py` розширте імпорти:

Знайдіть у `tests/test_evaluation_dataset.py`:

```python
import shutil
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
```

Замініть на:

```python
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any
```

Знайдіть у `tests/test_evaluation_dataset.py`:

```python
from knowledge_service.database import create_database_runtime
from knowledge_service.retrieval import evaluation_dataset
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DIRECTIONS,
    MAX_FILE_BYTES,
    CaseOutcome,
    DatasetReport,
    DatasetRunRefused,
    InvalidDataset,
    QueryClass,
    load_dataset,
    render_report,
    run_dataset,
    verify_corpus,
)
```

Замініть на:

```python
from fakes import FakeEmbeddingModel
from knowledge_service.database import create_database_runtime
from knowledge_service.embeddings.port import EmbeddingModel, EmbeddingModelIdentity
from knowledge_service.retrieval import evaluation_dataset
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DIRECTIONS,
    MAX_FILE_BYTES,
    CaseOutcome,
    DatasetReport,
    DatasetRunRefused,
    InvalidDataset,
    QueryClass,
    compare_branches,
    load_dataset,
    render_comparison,
    render_report,
    run_dataset,
    score_slices,
    verify_corpus,
)
```

Потім додайте нові тести в кінець файлу. Перший рядок нижче — останній рядок наявного тесту звіту:

Знайдіть у `tests/test_evaluation_dataset.py`:

```python
    assert rendered.startswith("# Retrieval dataset report: bilingual-seed v1\n")
```

Замініть на:

```python
    assert rendered.startswith("# Retrieval dataset report: bilingual-seed v1\n")


SCORED = DatasetReport(
    name="bilingual-seed",
    version=1,
    limit=5,
    branches=(RetrievalBranch.DENSE, RetrievalBranch.LEXICAL),
    outcomes=(
        CaseOutcome("a", "en->en", QueryClass.PLAIN, (1,), degraded=False),
        CaseOutcome("b", "en->en", QueryClass.IDENTIFIER, (4,), degraded=False),
        CaseOutcome("c", "en->en", QueryClass.PLAIN, (None,), degraded=True),
        CaseOutcome("d", "uk->en", QueryClass.PLAIN, (None, 3), degraded=False),
    ),
    embedding=EmbeddingModelIdentity(
        provider="openai", model="text-embedding-3-small", dimensions=1536
    ),
)


def test_slices_get_exact_recall_and_mrr() -> None:
    scores = {score.name: score for score in score_slices(SCORED)}

    en_en = scores["en->en"]
    assert (en_en.cases, en_en.found, en_en.degraded) == (3, 2, 1)
    # Cut-offs above the run's limit of 5 are left out.
    assert en_en.recall == (
        (1, Fraction(1, 3)),
        (3, Fraction(1, 3)),
        (5, Fraction(2, 3)),
    )
    assert en_en.mrr == Fraction(5, 12)
    # Two relevant Chunks, one found at rank 3: half of them, best rank 3.
    assert scores["uk->en"].recall[-1] == (5, Fraction(1, 2))
    assert scores["uk->en"].mrr == Fraction(1, 3)
    assert scores["all"].cases == 4
    assert scores["uk->uk"].recall == ()
    assert scores["uk->uk"].mrr is None


def test_the_report_shows_the_configuration_and_the_scores() -> None:
    rendered = render_report(SCORED)

    assert "- Branches: dense, lexical." in rendered
    assert "- Embedding model: openai text-embedding-3-small, 1536 dimensions." in (
        rendered
    )
    assert "- Fusion: Reciprocal Rank Fusion, k = 60." in rendered
    assert "| Slice | Cases | Found | Recall@1 | Recall@3 | Recall@5 | MRR |" in (
        rendered
    )
    assert "| en->en | 3 | 2 | 0.333 | 0.333 | 0.667 | 0.417 | 1 |" in rendered
    assert "| uk->uk | 0 | 0 | n/a | n/a | n/a | n/a | 0 |" in rendered


def test_the_comparison_puts_every_run_side_by_side() -> None:
    lexical = DatasetReport(
        name="bilingual-seed",
        version=1,
        limit=5,
        branches=(RetrievalBranch.LEXICAL,),
        outcomes=tuple(
            CaseOutcome(
                o.case_id,
                o.direction,
                o.query_class,
                (None,) * len(o.ranks),
                degraded=False,
            )
            for o in SCORED.outcomes
        ),
    )

    rendered = render_comparison((lexical, SCORED))

    assert rendered.startswith("# Retrieval comparison: bilingual-seed v1\n")
    assert "- Runs: lexical; dense + lexical." in rendered
    assert "- Embedding model: openai text-embedding-3-small" in rendered
    assert (
        "| Slice | Cases | lexical R@5 | lexical MRR"
        " | dense + lexical R@5 | dense + lexical MRR |"
    ) in rendered
    assert "| en->en | 3 | 0.000 | 0.000 | 0.667 | 0.417 |" in rendered
    assert rendered.count("# Retrieval dataset report:") == 2


class NamelessModel(FakeEmbeddingModel):
    @property
    def identity(self) -> EmbeddingModelIdentity:
        raise RuntimeError("no identity")


@pytest.mark.parametrize(
    ("limit", "model", "message"),
    [
        (0, FakeEmbeddingModel(), "limit"),
        (True, FakeEmbeddingModel(), "limit"),
        (10, NamelessModel(), "EmbeddingModelIdentity"),
    ],
)
async def test_a_wrong_comparison_setting_is_refused_before_any_work(
    limit: int, model: EmbeddingModel, message: str
) -> None:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr("postgresql+psycopg://u:p@localhost:1/x"))
    )
    dataset = load_dataset(DATASET_DIRECTORY / CASES_FILE)

    with pytest.raises(DatasetRunRefused, match=message):
        await compare_branches(
            runtime,
            dataset,
            DATASET_DIRECTORY / CORPUS_DIRECTORY,
            observed_at=datetime(2026, 11, 4, tzinfo=UTC),
            model=model,
            limit=limit,
        )
    await runtime.dispose()


def test_verify_only_needs_no_settings() -> None:
    # Only a run reads the settings, so a bad one cannot stop the check.
    finished = subprocess.run(
        [sys.executable, "scripts/run_retrieval_dataset.py", "--verify-only"],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "KNOWLEDGE_SERVICE_LOG_LEVEL": "not-a-level"},
        timeout=60,
    )

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout == "16 cases verified\n"
```

Створіть `tests/test_branch_comparison_integration.py`. Допоміжна функція `chain` обходить кожну помилку, пов'язану з кинутою, так само, як їх друкує traceback, тож тести можуть довести, що жодної пов'язаної помилки не лишилося:

```python
"""The seed dataset embedded and run three ways on real PostgreSQL."""

import logging
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from fakes import FakeEmbeddingModel
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import (
    EmbeddingLimits,
    EmbeddingModelIdentity,
    EmbeddingUnavailable,
)
from knowledge_service.identifiers import EmbeddingSpaceId
from knowledge_service.persistence.embeddings import insert_embedding_space
from knowledge_service.persistence.schema import (
    chunk_embeddings,
    chunks,
    documents,
    sources,
)
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    READER,
    SOURCE_IDS,
    SPACE_ID,
    CaseFailed,
    CorpusNotEmbedded,
    DatasetRunRefused,
    EvaluationDataset,
    Folder,
    compare_branches,
    load_dataset,
    render_comparison,
    run_dataset,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import BranchRequest, BranchResult
from knowledge_service.retrieval.vector_search import (
    search_similar_chunks,
    vector_search_statement,
)
from knowledge_service.settings import Settings

OBSERVED_AT = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
# The fake counts words as tokens; every seed Chunk fits these limits.
LIMITS = EmbeddingLimits(max_inputs=64, max_input_tokens=1_000, max_batch_tokens=20_000)


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


def chain(error: BaseException) -> Iterator[BaseException]:
    """Yield the error and every error linked to it, as a traceback shows them."""
    seen: list[BaseException] = []
    pending = [error]
    while pending:
        current = pending.pop()
        if any(current is item for item in seen):
            continue
        seen.append(current)
        yield current
        pending += [e for e in (current.__cause__, current.__context__) if e]
        if isinstance(current, BaseExceptionGroup):
            group = cast(BaseExceptionGroup[BaseException], current)
            pending += list(group.exceptions)


class IdentityFailsLater(FakeEmbeddingModel):
    """Names itself once, then fails: a model that breaks after the run began."""

    reads = 0

    @property
    def identity(self) -> EmbeddingModelIdentity:
        self.reads += 1
        if self.reads > 1:
            raise RuntimeError(f"identity lost {RESTRICTED_MARKER}")
        return super().identity


async def count(runtime: DatabaseRuntime, table: sa.Table) -> int:
    async with runtime.transaction() as connection:
        total = await connection.scalar(sa.select(sa.func.count()).select_from(table))
    return int(total or 0)


@pytest.mark.integration
async def test_every_case_runs_three_ways_and_the_report_names_no_text(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = FakeEmbeddingModel(limits=LIMITS)

    reports = await compare_branches(
        runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model
    )

    assert [report.branches for report in reports] == [
        (RetrievalBranch.LEXICAL,),
        (RetrievalBranch.DENSE,),
        (RetrievalBranch.DENSE, RetrievalBranch.LEXICAL),
    ]
    assert [report.embedding for report in reports] == [
        None,
        model.identity,
        model.identity,
    ]
    for report in reports:
        assert [o.case_id for o in report.outcomes] == [
            c.case_id for c in dataset.cases
        ]
        assert not any(outcome.degraded for outcome in report.outcomes)
    # Words only gives the same ranks as the M05-T07 baseline.
    lexical = {o.case_id: o.ranks for o in reports[0].outcomes}
    assert lexical["en-en-expense-deadline"] == (1,)
    assert lexical["uk-uk-per-diem-abroad"] == (1,)
    # Every Chunk, the two restricted ones included, has one vector.
    assert await count(runtime, chunk_embeddings) == await count(runtime, chunks) == 21
    rendered = render_comparison(reports)
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in dataset.cases)


@pytest.mark.integration
async def test_the_nearest_chunk_is_restricted_and_still_never_returned(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    model = FakeEmbeddingModel(limits=LIMITS)
    await compare_branches(
        runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model
    )
    async with runtime.transaction() as connection:
        restricted_text = await connection.scalar(
            sa.select(chunks.c.text)
            .select_from(chunks.join(documents))
            .where(documents.c.source_id == SOURCE_IDS[Folder.RESTRICTED].value)
            .order_by(chunks.c.ordinal)
            .limit(1)
        )
    assert restricted_text is not None
    # The fake gives equal text equal vectors, so this query sits exactly on
    # a restricted Chunk: distance 0, the best match a search could find.
    batch = await model.embed(
        [restricted_text], request_id=READER.user_id.value, timeout_seconds=5.0
    )
    query = batch.vectors[0]

    async with runtime.transaction() as connection:
        unfiltered = (
            (
                await connection.execute(
                    vector_search_statement(
                        space_id=SPACE_ID,
                        query=query,
                        eligibility=sa.true(),
                        restriction=sa.true(),
                        limit=1,
                    )
                )
            )
            .mappings()
            .one()
        )
        found = await search_similar_chunks(
            connection,
            identity=model.identity,
            query=query,
            context=READER,
            limit=21,
        )

    # Without the permission check the restricted Chunk ranks first ...
    assert unfiltered["source_id"] == SOURCE_IDS[Folder.RESTRICTED].value
    # ... with it, the reader gets every public Chunk and no restricted one.
    assert len(found.candidates) == 19
    assert {c.source_id for c in found.candidates} == {SOURCE_IDS[Folder.PUBLIC]}


@pytest.mark.integration
async def test_a_model_that_fails_stops_the_run_before_any_case(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    model = FakeEmbeddingModel(limits=LIMITS, error=EmbeddingUnavailable())

    with pytest.raises(CorpusNotEmbedded) as raised:
        await compare_branches(
            runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model
        )

    assert str(raised.value) == (
        "the corpus could not be embedded: IncompleteEmbedding"
    )
    assert [type(error) for error in chain(raised.value)] == [CorpusNotEmbedded]
    assert await count(runtime, chunk_embeddings) == 0
    # Only the corpus was sent: no Question reached the model.
    questions = {case.question for case in dataset.cases}
    assert not any(text in questions for batch in model.batches for text in batch)


@pytest.mark.integration
async def test_a_database_with_an_embedding_space_is_refused_before_any_write(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    async with runtime.transaction() as connection:
        await insert_embedding_space(
            connection,
            space_id=EmbeddingSpaceId.new(),
            identity=EmbeddingModelIdentity(
                provider="other", model="older-model", dimensions=8
            ),
            is_current=True,
            created_at=OBSERVED_AT,
        )
    model = FakeEmbeddingModel(limits=LIMITS)

    with pytest.raises(DatasetRunRefused, match="empty database"):
        await compare_branches(
            runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model
        )

    assert await count(runtime, sources) == 0
    assert model.batches == []


@pytest.mark.integration
async def test_a_model_that_breaks_during_embedding_raises_a_typed_error(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    with pytest.raises(CorpusNotEmbedded, match="RuntimeError") as raised:
        await compare_branches(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=IdentityFailsLater(limits=LIMITS),
        )

    assert [type(error) for error in chain(raised.value)] == [CorpusNotEmbedded]
    assert await count(runtime, chunk_embeddings) == 0


@pytest.mark.integration
async def test_an_unexpected_error_in_a_case_is_typed_and_quiet(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    async def broken(request: BranchRequest) -> BranchResult:
        raise RuntimeError(f"cannot answer {request.question}")

    with pytest.raises(CaseFailed) as raised:
        await run_dataset(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            branches={RetrievalBranch.LEXICAL: broken},
        )

    first = dataset.cases[0]
    assert str(raised.value) == f"case {first.case_id}: the search raised RuntimeError"
    # Nothing linked to the error, which a traceback would print, holds the
    # Question.
    assert [type(error) for error in chain(raised.value)] == [CaseFailed]
```

Перевірка, саме в такому порядку:

```bash
uv run pytest tests/test_ranking_metrics.py tests/test_evaluation_dataset.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_branch_comparison_integration.py tests/test_evaluation_dataset_integration.py -q
```

Очікувано: `102 passed`, потім `12 passed`.

### Крок 8 — Без програмування: проженіть з OpenAI і прочитайте звіт

Цей крок запускає порівняння зі справжньою керованою моделлю і звіряє результат із вашими передбаченнями. Ще він навмисно ламає перевірку дозволів, щоб побачити, як тести це ловлять.

**Дія 1:** створіть порожню базу даних, виконайте міграції і запустіть прогін. URL бази даних містить пароль, тож він живе лише в змінній оболонки. Введіть ключ API у змінну так, щоб він не відображався:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_dataset#')"
uv run alembic upgrade head
read -rs KNOWLEDGE_SERVICE_MODEL_API_KEY && export KNOWLEDGE_SERVICE_MODEL_API_KEY
uv run python scripts/run_retrieval_dataset.py --managed-embeddings
```

Прогін надсилає в OpenAI 21 уривок і 32 запитання: кілька тисяч токенів. Сторінка починається з конфігурації і таблиці пліч-о-пліч, далі йдуть три повні звіти. Під час підготовки уроку я не міг виконати цей крок, бо ключа API не було. Ваші числа будуть першими справжніми.

**Дія 2:** перевірте одне припущення про повторюваність. Збережіть звіт, проженіть ще раз на свіжій базі даних і порівняйте:

```bash
cp artifacts/evaluations/bilingual-seed-managed.md /tmp/first-managed.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --managed-embeddings > /dev/null
diff /tmp/first-managed.md artifacts/evaluations/bilingual-seed-managed.md && echo same
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
unset KNOWLEDGE_SERVICE_DATABASE_URL KNOWLEDGE_SERVICE_MODEL_API_KEY
```

Частина пошуку за словами мусить бути однаковою. Щільні частини можуть збігтися, а можуть і ні, бо числа постачальника можуть відрізнятися в останніх знаках. Будь-яка відповідь — це доказ; запишіть, яку ви отримали.

**Дія 3:** прочитайте звіт, звіряючи з передбаченнями, зріз за зрізом.

- Порівняйте стовпці `lexical` з базовим рівнем M05-T07. Вони мусять збігатися: знайдено 5 з 16, усі на 1-му місці.
- Подивіться на `en->uk` і `uk->en` у стовпцях `dense`. Заради цього результату і існує урок: пошук за словами отримав там 0.
- Подивіться на зріз ідентифікаторів у `dense` і в `dense + lexical`. Коди на кшталт `VPN-4031` майже не несуть змісту, тож саме тут пошук за словами ще може вигравати.
- Прочитайте таблицю «By case» щільного прогону для кожного промаху. Запишіть ID кожного пропущеного випадку і одним рядком — чому, на вашу думку, його пропущено.

Потім запишіть у файл доказів принаймні одне **спростоване припущення**. Використайте таку форму: «Я припускав X. Звіт показує Y (ID випадків і числа). Отже, Z хибне». Якщо всі передбачення справдилися, звичне місце, де його шукати, — перевірка повторюваності або об'єднаний прогін проти кращого з одиночних.

**Дія 4:** навмисно зламайте перевірку дозволів. У `src/knowledge_service/retrieval/vector_search.py`, у `search_similar_chunks`, змініть `eligibility=eligible_chunks_predicate(context),` на `eligibility=sa.true(),`. Потім запустіть:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_branch_comparison_integration.py -q
```

Очікувано: `test_the_nearest_chunk_is_restricted_and_still_never_returned` падає, бо читач тепер отримує закритий уривок. `test_every_case_runs_three_ways_and_the_report_names_no_text` теж падає: власний запобіжник прогону зупиняє його з `IneligibleChunkRetrieved` на `en-en-annual-leave-days`. Скасуйте зміну і знову запустіть тести, доки вони не пройдуть. Не запускайте платний скрипт, поки злам на місці.

### Крок 9 — Без програмування: перевірки, документація, докази і прогрес

Цей крок доводить, що все й далі працює, оновлює один рядок архітектури, який зміна зробила застарілим, і записує результат.

**Дія 1:** у `docs/architecture.md` змініть рядок `retrieval` у таблиці «Code layout» на:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR) |
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_ranking_metrics.py tests/test_evaluation_dataset.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний прогін дав `make test-unit` 1199 passed і `make test-integration` 300 passed.

**Дія 3:** заповніть файл доказів:

- **Design:** змінені файли; чому корпус зберігають і вбудовують один раз; чому закриті уривки отримують вектори; чому оцінки — точні дроби; чому помилки кидають поза блоками `except`.
- **Verification:** команди і кількості, таблиця пліч-о-пліч з кроку 8, промахи з поясненням в один рядок для кожного, спростоване припущення, результат повторюваності, навмисний злам і відомі обмеження з розділу «Перш ніж почати». Вставте звіт у файл доказів: Git ігнорує `artifacts/`, а завдання вимагає версійованого звіту.
- **Review:** змагальний огляд прототипу знайшов три проблеми, усі виправлено з регресійними тестами, які падали до виправлення. Баг пошуку виходив назовні нетипізованою групою помилок із текстом запитання; тепер він стає `CaseFailed` лише з назвами типів. Модель, що ламалася після першого читання, виходила нетипізованою вже після запису рядків; тепер вона стає `CorpusNotEmbedded`. Режиму лише перевірки стали потрібні правильні налаштування; тепер він їх не читає.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте завдання виконаним, додайте рядок M05-T08 у `docs/curriculum/PROGRESS.md`, встановіть наступним завданням M05-T09 і зробіть коміт із повідомленням `m05-t08: measure managed multilingual embeddings`.

## Запитання для роздумів

1. Що `compare_branches` ховає від скрипту, який її викликає? Перелічіть, про що скрипту ніколи не доводиться думати, наприклад про створення простору ембедингів чи порядок трьох прогонів.
2. Згодом хтось пришвидшує порівняння, вбудовуючи лише відкриті документи, бо «читач однаково не бачить закритих». Що тоді перестане доводити тест дозволів і чому?
3. MRR і Recall@10 можуть рухатися в різні боки. Опишіть зміну пошуку, яка підняла б Recall@10, але знизила MRR, і скажіть, яке з двох чисел важливіше для людини, що ставить запитання.
4. Яке з ваших передбачень виявилося хибним, і у що ви вірили щодо ембедингів, коли його робили?
5. Шістнадцять випадків — це мало. У якому зрізі ще один випадок найімовірніше змінив би ваш висновок, і яким був би цей випадок?

## Контрольний список

- [ ] У файлі доказів є інваріант і ваші передбачення, записані до будь-якого прогону.
- [ ] `ranking_metrics.py` існує, а `tests/test_ranking_metrics.py` дає 32 passed.
- [ ] Звіт показує блок конфігурації і Recall@1, @3, @5, @10 та MRR для кожного зрізу.
- [ ] `compare_branches` зберігає і вбудовує один раз і повертає звіти лише за словами, лише за змістом і об'єднаний.
- [ ] Закритий уривок вбудовано, у тесті він найближчий збіг, і читач його ніколи не отримує.
- [ ] Модель, що ламається, кидає `CorpusNotEmbedded`, а баг пошуку кидає `CaseFailed`; жодна не несе тексту.
- [ ] Ви запустили `--managed-embeddings` з OpenAI і вставили звіт у файл доказів.
- [ ] Файл доказів називає кожен промах і принаймні одне спростоване припущення.
- [ ] Ви виконали навмисний злам із кроку 8, побачили, як падає тест, і скасували його.
- [ ] Усі перевірки проходять: форматування, lint, типи, модульні, інтеграційні, документація.
- [ ] Рядок архітектури, докази, рядок у PROGRESS і коміт готові.

Коли напишете файли, поділіться ними або виводом помилок, і я перевірю їх за цим списком.
