# M05-T06 — Побудувати модуль гібридного пошуку

Англійська версія: [M05-T06-build-the-hybrid-retrieval-module.md](M05-T06-build-the-hybrid-retrieval-module.md)

Вихідне завдання: [M05-T06](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T06-build-the-hybrid-retrieval-module.md)

## Що ви зробите

У проєкті є два пошуки і спосіб злити їхні списки, але немає нічого, що запускало б їх разом для одного запитання (Question). Цей урок будує саме цю частину. Вона запускає обидва пошуки в ту саму мить, дає їм один спільний ліміт часу, зливає те, що повернулося, і звітує, як пройшов кожен пошук. Вона також вирішує, що робити, коли один пошук повільний або зламаний.

- Новий модуль `hybrid.py` містить `retrieve_hybrid` і два невеликі адаптери, `dense_branch` і `lexical_branch`, які підключають справжні пошуки.
- Обидва пошуки виконуються одночасно, кожен на власному з'єднанні з базою даних, тож відповідь займає стільки, скільки повільніший із них, а не суму обох.
- Один **дедлайн** (deadline) покриває обидва. Пошук, що ще працює, коли час вийшов, зупиняється, а його запит до бази даних скасовується на сервері.
- Якщо один пошук не встиг або впав, результати іншого все одно повертаються з позначкою **неповний** (degraded). Якщо жоден не вдався, про це каже типізована помилка.
- Кожен результат несе **діагностику** (diagnostics): для кожного пошуку — чи він вдався, не встиг чи впав, скільки часу зайняв і скільки кандидатів (Candidate) знайшов.

| Завдання вимагає | Де це відбувається |
|---|---|
| Запускати векторний і лексичний пошук одночасно | Крок 2: `retrieve_hybrid` запускає всі гілки в одній `asyncio.TaskGroup` |
| Під одним дедлайном | Крок 2: один абсолютний дедлайн, застосований до кожної гілки через `asyncio.timeout_at` |
| Злити результати | Крок 2: `fuse_ranked_candidates` з M05-T05, потім обрізання до `limit` |
| Додати діагностику | Крок 2: `BranchDiagnostics` для кожної гілки, `degraded` у результаті |
| Визначити поведінку при частковій відмові | Крок 2: таблиця правил у розділі «Перш ніж почати» |
| Контекст авторизації доходить до обох гілок | Крок 3: `test_every_branch_gets_the_same_question_reader_and_deadline`; крок 4: `test_both_real_branches_search_as_the_reader` |
| Правило таймауту можна спостерігати | Крок 3: `test_a_branch_that_misses_the_deadline_is_cancelled_and_reported`; крок 4: тест повільного запиту |
| Успіх, найризикованіший крайній випадок, одна типізована помилка | Кроки 3–4: обидві гілки злиті; повільний запит скасовано на сервері; `HybridRetrievalUnavailable` і `InvalidHybridQuery` |

У цьому уроці є програмування: кроки 2, 3 і 4 пишуть код. Кроки 1, 5 і 6 — ні.

Терміни цього уроку:

- **Гілка (branch):** один спосіб пошуку. Тут їх дві: векторна (dense, за змістом) і лексична (lexical, за словами).
- **Одночасно (concurrently):** в один і той самий час. Як двоє людей, що кожен приносить по одній книжці з бібліотеки, замість однієї людини, яка приносить обидві по черзі.
- **Дедлайн (deadline):** один момент часу, до якого кожна гілка має відповісти. Його фіксують один раз на початку, тож гілка, яка стартувала пізніше, не отримує додаткового часу.
- **Скасувати (cancel):** зупинити задачу, що виконується. В `asyncio` скасування кидає `CancelledError` усередині задачі на її наступному `await`, і код довкола прибирає за собою.
- **Неповний результат (degraded):** результат, побудований з меншої кількості гілок, ніж просили, тож деяких добрих фрагментів (Chunk) може бракувати.
- **Діагностика (diagnostics):** короткий звіт про кожну гілку, який безпечно логувати: статус, витрачений час, кількість знайденого і тип помилки, якщо вона впала.

## Перш ніж почати

Цей розділ пояснює рішення, на яких стоїть код, щоб кроки були зрозумілі, коли ви до них дійдете.

- **Що вже є.** `search_similar_chunks` потребує вектора запиту, тож векторна гілка спершу має перетворити запитання на вектор через `EmbeddingModel` — це мережевий виклик. `search_lexical_chunks` потребує лише тексту. Обидві приймають `AsyncConnection` і `AuthorizationContext` і обидві повертають кандидатів плюс `truncated`. `fuse_ranked_candidates` зливає впорядковані списки за позиціями.
- **Чому кожна гілка має власне з'єднання.** Одне з'єднання з базою даних виконує один запит за раз. Щоб шукати одночасно, кожна гілка відкриває власне з'єднання через `runtime.transaction()`. У пулі є для цього місце: за замовчуванням він тримає 5 з'єднань і може відкрити ще 5.
- **Чому гілки — звичайні функції.** `retrieve_hybrid` не знає, як гілка шукає. Вона приймає словник від `RetrievalBranch` до **функції пошуку гілки**: асинхронної функції, яка отримує `BranchRequest` і повертає щось із `candidates` і `truncated`. Справжні функції дають `dense_branch` і `lexical_branch`. Тести натомість передають маленькі підробки, тож можуть навмисно зробити гілку повільною, зламаною чи швидкою, без бази даних. Це перша підказка завдання: звузьте інтерфейс, перш ніж братися за моки.
- **Що питають у кожної гілки.** Той самий `BranchRequest`: запитання, його вказана мова, `AuthorizationContext` читача, ліміт, ID запиту і час, що лишився до дедлайну. Векторна гілка передає цей час моделі ембедингів, яка має власний таймаут. Запитання не потрапляє в repr запиту.
- **Один дедлайн, а не по одному на гілку.** Дедлайн обчислюють один раз як «зараз плюс `timeout_seconds`». Кожна гілка виконується всередині `asyncio.timeout_at(deadline)`, який скасовує її, коли ця мить минає. Скасування також скасовує запит до бази даних на сервері, бо psycopg надсилає PostgreSQL запит на скасування, коли його задачу скасовано. Крок 4 доводить це на справжньому повільному запиті. Гілка все ж може відповісти пізно, якщо вона ігнорує скасування або блокує цикл подій повільною синхронною роботою. Таку пізню відповідь відкидають і звітують як таймаут.
- **Правила часткової відмови.** Кожна гілка закінчується в одному з трьох станів:

  | Що сталося | Статус | Що отримує викликач |
  |---|---|---|
  | Гілка відповіла вчасно | `succeeded` | її кандидати йдуть у злиття |
  | Дедлайн минув раніше | `timed_out` | гілку скасовано й не враховано |
  | Вона відповіла або впала лише після дедлайну | `timed_out` | її відповідь відкинуто |
  | Вона вчасно кинула відому помилку залежності чи відмови | `failed` | записано назву типу помилки, гілку не враховано |
  | Вона кинула будь-яку іншу помилку | не перехоплюється | помилка йде далі, а іншу гілку скасовано |

  Відомі помилки — це короткий, свідомо обраний список:
  - будь-яка помилка ембедингів, наприклад коли провайдер недоступний або повільний;
  - `NoCurrentEmbeddingSpace`, коли сховище векторів містить іншу модель, як під час зміни моделі;
  - `InvalidLexicalQuery`, коли пошук за словами відмовляється від цього запитання, наприклад від такого, що складається лише з дефісів;
  - `OperationalError` та `InterfaceError` від SQLAlchemy — втрачене з'єднання або запит, який зупинив сервер, — і `TimeoutError` пулу, коли немає вільного з'єднання;
  - `ConnectionError` і власний `TimeoutError` гілки.

  Коли вдалася хоча б одна гілка, результат зливається з тих, що вдалися, і `degraded` дорівнює True, якщо якась не вдалася. Коли жодна не вдалася, `retrieve_hybrid` кидає `HybridRetrievalUnavailable`, яка несе ту саму діагностику.
- **Чому баги не перехоплюються.** `RuntimeError` у гілці або SQL, який після міграції більше не компілюється (`ProgrammingError`), — це помилка в нашому коді, а не повільний сервіс. Якби її приховували як `failed`, зламана гілка назавжди виглядала б як збій, і сервіс тихо працював би на одній гілці. Тому вона йде далі. Оскільки гілки виконуються в `asyncio.TaskGroup`, іншу гілку спершу скасовують, а викликач отримує `ExceptionGroup`, у якій лежить ця помилка.
- **Перевірка спочатку.** `InvalidHybridQuery` кидається ще до старту будь-якої гілки для: порожнього запитання, запитання довшого за 1 000 символів (стільки максимум приймає пошук за словами) або такого, що містить NUL чи зламаний символ; читача, який не є `AuthorizationContext`; мови, яка не є `Language`; поганого ліміту, таймауту чи `k`; і порожнього чи неправильного набору гілок. Без цього порожнє запитання змусило б обидві гілки впасти, і викликач побачив би «недоступно» через власну помилку.
- **Ліміт і `truncated`.** Кожну гілку просять про `limit` кандидатів. Злитий список може містити вдвічі більше, тож його обрізають до `limit`. `truncated` дорівнює True, коли злитий список обрізали або коли будь-яка вдала гілка сказала, що є ще.
- **Права доступу.** `retrieve_hybrid` передає той самий `AuthorizationContext` кожній гілці. Кожна гілка застосовує його у своєму власному SQL-запиті, до ранжування і `LIMIT`, а злиття лише переставляє те, що повернулося. Тож жоден шлях не може додати фрагмент, який читачеві бачити не можна. Неповний результат так само повністю перевірений щодо прав, просто фрагментів у ньому менше.
- **Тиша.** Діагностика називає помилку лише за типом. Повідомлення помилки бази даних може повторювати запитання, як з'ясували попередні уроки, тож повідомлення ніколи не потрапляють у діагностику. `HybridRetrievalUnavailable` має фіксоване повідомлення і не має ланцюжка причин.
- **Що знайшов огляд.** Незалежний огляд атакував прототип. Він не знайшов способу розкрити прихований фрагмент, не знайшов витоку з'єднань і зависань після дедлайну, коли бере участь база даних. Але він знайшов п'ять проблем, і кожну тепер виправлено, з тестами, що падають на старому коді:
  - Баги приховувалися. Кожна помилка SQLAlchemy і кожна `OSError` вважалися `failed`, тож SQL, зламаний міграцією, або відсутній файл виглядали як збій. Тепер `failed` — лише короткий список помилок залежностей і відмов вище; усе інше йде далі.
  - Пізні відповіді враховувалися. Гілка, яка перехопила скасування і все одно відповіла або заблокувала цикл подій після дедлайну, звітувалася як `succeeded` і зливалася. Тепер усе, що закінчилося після дедлайну, — це `timed_out`, а відповідь відкидається.
  - Помилка, кинута під час скасування гілки, наприклад невдалий rollback, показувалася як `failed`. Тепер це `timed_out`.
  - Запитання, яке виглядало правильним, ніколи не можна було знайти за словами: до 4 000 символів проходило перевірку, але пошук за словами відмовляє понад 1 000. Тепер ліміт — 1 000, і NUL чи зламаний символ теж відхиляються.
  - Деякі погані вхідні дані проскакували першу перевірку: таймаут, завеликий для float, мова, передана рядком, відсутній читач або гілка, яка не є функцією. Тепер усе це кидає `InvalidHybridQuery`.

  Огляд також показав, що деякі тести не могли впасти: стале `elapsed_seconds`, `truncated` гілки, яке завжди було False, і векторна гілка, що тримала з'єднання під час виклику ембедингів, — усе це проходило. Тепер для кожного є тест, який це ловить.

  Деякі знахідки — відомі обмеження, записані в доказах (evidence) замість виправлення:
  - asyncio не може зупинити код, який блокує цикл подій або ігнорує скасування. Така гілка все одно затримує весь виклик, хоча її відповідь відкидається.
  - `TimeoutError` пулу і власний `TimeoutError` гілки мають у діагностиці однакову назву.
  - Гілку, яка повертає більше ніж `limit` кандидатів, не обрізають до злиття. Остаточний список усе одно обрізано до `limit`.
  - `KeyboardInterrupt` і `SystemExit` виходять самі по собі, як і будь-де в asyncio.
  - API приймає запитання до 4 000 символів, а пошук відмовляє понад 1 000. Це має вирішити завдання, яке поєднає пошук з API.

  Огляд також підтвердив, що трималося. 40 випадкових запитань до 12 прихованих документів (Document) жодного разу не повернули жодного з них. З пулом на одне з'єднання і сімома одночасними викликами кожен виклик закінчився до свого дедлайну, а пул потім відновився.

- **Запуск тестів.** Юніт-тестам база даних не потрібна. Інтеграційним — потрібна; читайте адресу бази даних прямо в кожній команді, щоб пароль ніколи не друкувався і не зберігався:

  ```bash
  uv run pytest tests/test_hybrid_retrieval.py -q
  KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_hybrid_retrieval_integration.py -q -m integration
  ```

- **Файли, які ви зміните:**
  - новий `src/knowledge_service/retrieval/hybrid.py`
  - нові `tests/test_hybrid_retrieval.py` і `tests/test_hybrid_retrieval_integration.py`
  - `docs/architecture.md`, новий файл доказів і наприкінці `PROGRESS.md`

Цього разу без міграції й без змін у налаштуваннях.

## Покрокове проходження

### Крок 1 — Без програмування: запишіть інваріант і свої передбачення

На цьому кроці ви ще до коду записуєте, що має бути правдою завжди і що ви очікуєте побачити. На кроці 5 ви порівняєте свої здогадки з реальними результатами.

**Дія:** створіть `docs/evidence/M05-T06-hybrid-retrieval.md`:

```markdown
# M05-T06 — Hybrid retrieval evidence

Completed: pending

## Invariant

`retrieve_hybrid` starts every branch at the same time with the same
Question, reader, and limit, and gives them one deadline. A branch that
answers in time is fused; one that times out is cancelled, its database
query with it; one that fails with a dependency or refusal error is left
out and named by its error type. If no branch succeeds,
`HybridRetrievalUnavailable` is raised. Every returned Chunk passed the
reader's permission check in its branch's own SQL statement. At most
`limit` Candidates come back, and no Question text or error message
appears in diagnostics, errors, or reprs.

## Expected failing check

Before the change, `tests/test_hybrid_retrieval.py` fails to import
`knowledge_service.retrieval.hybrid`.

## Predictions

Before running anything, write what you expect:

- Dense answers with Chunks 1, 2, 3 and lexical with 3, 4; the limit is 3.
  Which Chunks come back, in what order, and is the result truncated?
- Lexical never answers and the deadline is 0.2 seconds. What does the
  caller get, and what does the lexical diagnostic say?
- The embedding service is down, and lexical finds Chunk 4. What comes
  back, and is the result degraded?
- The embedding service is down and lexical never answers. What does the
  caller get?
- A branch is cancelled in the middle of a 30-second `pg_sleep` query.
  What happens to that query on the server?

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Перевірка:** файл існує, і в ньому ваші власні передбачення.

### Крок 2 — Крок з програмуванням: напишіть модуль гібридного пошуку

Цей крок будує функцію, яка запускає обидва пошуки разом і вирішує, що робити, коли один із них повільний або зламаний.

**Простими словами:** Цей файл — диригент двох пошуків. Спершу він перевіряє запитання й налаштування, потім запускає обидва пошуки в ту саму мить і дає їм один спільний ліміт часу. Коли кожен пошук закінчується, він записує, чи той вдався, чи не встиг, чи впав, і скільки часу зайняв. Наостанок він зливає списки вдалих пошуків і повертає їх разом зі звітом або кидає зрозумілу помилку, якщо жоден не вдався.

| Частина | Що вона робить |
|---|---|
| `_BRANCH_FAILURES` | помилки, що означають «ця гілка не може відповісти зараз», записуються як `failed` |
| `InvalidHybridQuery`, `HybridRetrievalUnavailable` | дві типізовані помилки, обидві — `HybridRetrievalError`; друга несе діагностику |
| `BranchStatus` | `succeeded`, `timed_out`, `failed` |
| `BranchRequest` | що питають у кожної гілки; запитання не потрапляє в його repr |
| `BranchResult`, `BranchSearch` | форма, яку повертає гілка, і тип функції гілки |
| `BranchDiagnostics` | звіт про одну гілку |
| `HybridRetrieval` | злиті кандидати, `limit`, `k`, `truncated`, діагностика і `degraded` |
| `retrieve_hybrid` | перевіряє, встановлює дедлайн, запускає гілки, зливає, обрізає до `limit` |
| `dense_branch`, `lexical_branch` | обгортають справжні пошуки як функції гілок, кожну на власному з'єднанні |
| `_run` | виконує одну гілку під дедлайном і перетворює те, як вона закінчилася, на діагностику |
| `_check`, `_encodable` | відхиляють погані вхідні дані, перш ніж щось почнеться |

Кілька рядків варто пояснити:

- `asyncio.TaskGroup` запускає обидві гілки як задачі й чекає на обидві. Якщо одна кидає помилку, яку `_run` не перехоплює, група скасовує іншу і кидає `ExceptionGroup`. Якщо скасовано викликача, група скасовує обидві. Тож жодна гілка ніколи не лишається працювати у фоні.
- `loop.time() + timeout_seconds` фіксує дедлайн один раз. `asyncio.timeout_at(deadline)` у `_run` використовує ту саму мить для кожної гілки.
- Після завершення гілки `budget.expired() or loop.time() > deadline` питає, чи минув дедлайн. Якщо так, що б гілка не зробила, це `timed_out`: саме скасування, помилка під час скасування або пізня відповідь. Якщо ні, відома помилка вважається `failed`, зокрема й власний `TimeoutError` гілки.
- `replace(request, timeout_seconds=remaining)` дає кожній гілці час, що справді лишився, для власного таймауту виклику ембедингів.
- `type(failure).__name__` записує лише назву класу помилки, ніколи не її повідомлення.
- `type(timeout) not in (int, float)` відхиляє `True` і рядки, як і всюди в проєкті. `0 < timeout <= sys.float_info.max` відхиляє NaN, нескінченність і int, завеликий, щоб стати дедлайном типу float.
- `MAX_QUERY_CHARACTERS` береться з лексичного пошуку, тож запитання, від якого пошук за словами завжди відмовився б, відхиляється одразу.

**Дія:** створіть `src/knowledge_service/retrieval/hybrid.py`:

```python
"""Search by meaning and by words at once, under one deadline, and fuse.

`retrieve_hybrid` starts every branch at the same time, each with its own
database connection, and gives them all one deadline. Each branch that
answers in time contributes its ranked Candidates; Reciprocal Rank Fusion
(see `retrieval.fusion`) combines them into one list.

What a caller can rely on in a `HybridRetrieval`:

- **Checked first.** A bad question, reader, language, limit, timeout,
  `k`, or set of branches raises `InvalidHybridQuery` before any branch
  starts. A question must also fit the lexical search's limit of
  `MAX_QUERY_CHARACTERS`, so no valid question is refused by a branch for
  its length.
- **One deadline.** Every branch must finish within `timeout_seconds` of
  the start. A branch still running then is cancelled, its database query
  with it, and is reported as timed out. A branch that answers only after
  the deadline, because it ignored the cancel or blocked the event loop,
  is also reported as timed out, and its answer is dropped.
- **Partial failure.** A branch that times out, or fails because a
  dependency is down or slow (`EmbeddingError`, `NoCurrentEmbeddingSpace`,
  `OperationalError`, `InterfaceError`, a pool `TimeoutError`, a
  `ConnectionError`, or its own `TimeoutError`) or because it refuses the
  question (`InvalidLexicalQuery`), is reported in `branches` and left out
  of the fusion. The others still count, and `degraded` says the result is
  incomplete. When no branch succeeds, `HybridRetrievalUnavailable` is
  raised with the same diagnostics.
- **Bugs are not hidden.** Any other error propagates. Raised inside a
  branch, it cancels the other branches first and arrives inside the
  `ExceptionGroup` of the task group; a malformed branch result raises
  from fusion.
- **Eligible.** Each branch applies the reader's permission check in its
  own SQL statement, and fusion only reorders what the branches returned.
- **Bounded.** Each branch asks for `limit` Candidates; at most `limit`
  fused Candidates come back, and `truncated` says whether more exist.
- **Quiet.** Diagnostics name an error by its type, never by its message,
  which may repeat the question; the question is kept out of every repr.
"""

import asyncio
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID

from sqlalchemy import exc

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime
from knowledge_service.embeddings.port import EmbeddingError, EmbeddingModel
from knowledge_service.language import Language
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.fusion import (
    DEFAULT_RRF_K,
    FusedCandidate,
    RetrievalBranch,
    fuse_ranked_candidates,
)
from knowledge_service.retrieval.lexical_search import (
    MAX_QUERY_CHARACTERS,
    InvalidLexicalQuery,
    search_lexical_chunks,
)
from knowledge_service.retrieval.vector_search import (
    MAX_CANDIDATES,
    NoCurrentEmbeddingSpace,
    search_similar_chunks,
)

# Errors that mean a branch cannot answer this Question now: a dependency
# is down or slow, or the branch refuses this Question. Anything else, such
# as SQL that no longer compiles or a missing file, is a bug and propagates.
_BRANCH_FAILURES = (
    EmbeddingError,
    NoCurrentEmbeddingSpace,
    InvalidLexicalQuery,
    exc.OperationalError,
    exc.InterfaceError,
    exc.TimeoutError,
    ConnectionError,
    TimeoutError,
)


class HybridRetrievalError(Exception):
    """Hybrid retrieval could not return Candidates."""


class InvalidHybridQuery(HybridRetrievalError):
    """The question or a setting is not valid; no branch was started."""


class HybridRetrievalUnavailable(HybridRetrievalError):
    """No branch returned Candidates in time; `branches` says why."""

    def __init__(self, branches: tuple[BranchDiagnostics, ...]) -> None:
        self.branches = branches
        super().__init__("no retrieval branch succeeded")


class BranchStatus(StrEnum):
    """How one branch ended."""

    SUCCEEDED = "succeeded"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class BranchRequest:
    """What every branch is asked: the same question, reader, and deadline.

    `timeout_seconds` is the time left until the shared deadline, for a
    branch that calls a provider with its own timeout.
    """

    question: str = field(repr=False)
    language: Language | None
    context: AuthorizationContext
    limit: int
    request_id: UUID
    timeout_seconds: float


class BranchResult(Protocol):
    """What a branch returns: ranked Candidates and whether more exist."""

    @property
    def candidates(self) -> Sequence[Candidate]: ...

    @property
    def truncated(self) -> bool: ...


type BranchSearch = Callable[[BranchRequest], Awaitable[BranchResult]]


@dataclass(frozen=True, slots=True)
class BranchDiagnostics:
    """How one branch ended, how long it took, and what it found.

    `error` is the type name of the error that failed the branch, or None.
    """

    branch: RetrievalBranch
    status: BranchStatus
    elapsed_seconds: float
    candidates: int
    truncated: bool
    error: str | None


@dataclass(frozen=True, slots=True)
class HybridRetrieval:
    """The fused Candidates of one Question, and how each branch ended."""

    limit: int
    k: int
    candidates: tuple[FusedCandidate, ...]
    truncated: bool
    branches: tuple[BranchDiagnostics, ...]

    @property
    def degraded(self) -> bool:
        """True when a branch timed out or failed, so Candidates may be missing."""
        return any(b.status is not BranchStatus.SUCCEEDED for b in self.branches)


@dataclass(slots=True)
class _Ended:
    diagnostics: BranchDiagnostics
    result: BranchResult | None


async def retrieve_hybrid(
    question: str,
    *,
    context: AuthorizationContext,
    branches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
    timeout_seconds: float,
    request_id: UUID,
    language: Language | None = None,
    k: int = DEFAULT_RRF_K,
) -> HybridRetrieval:
    """Run every branch at once under one deadline and fuse what returns."""
    _check(
        question,
        context=context,
        language=language,
        branches=branches,
        limit=limit,
        timeout=timeout_seconds,
        k=k,
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    request = BranchRequest(
        question=question,
        language=language,
        context=context,
        limit=limit,
        request_id=request_id,
        timeout_seconds=timeout_seconds,
    )
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(_run(branch, branches[branch], request, deadline))
            for branch in RetrievalBranch
            if branch in branches
        ]
    ended = [task.result() for task in tasks]
    diagnostics = tuple(item.diagnostics for item in ended)
    succeeded = {
        item.diagnostics.branch: item.result
        for item in ended
        if item.result is not None
    }
    if not succeeded:
        raise HybridRetrievalUnavailable(diagnostics)
    fused = fuse_ranked_candidates(
        {branch: tuple(result.candidates) for branch, result in succeeded.items()},
        k=k,
    )
    return HybridRetrieval(
        limit=limit,
        k=k,
        candidates=fused[:limit],
        truncated=len(fused) > limit
        or any(result.truncated for result in succeeded.values()),
        branches=diagnostics,
    )


def dense_branch(runtime: DatabaseRuntime, model: EmbeddingModel) -> BranchSearch:
    """Embed the question, then search the current Embedding Space."""

    async def search(request: BranchRequest) -> BranchResult:
        batch = await model.embed(
            [request.question],
            request_id=request.request_id,
            timeout_seconds=request.timeout_seconds,
        )
        async with runtime.transaction() as connection:
            return await search_similar_chunks(
                connection,
                identity=batch.identity,
                query=batch.vectors[0],
                context=request.context,
                limit=request.limit,
            )

    return search


def lexical_branch(runtime: DatabaseRuntime) -> BranchSearch:
    """Search the stored words, routed by the question's language."""

    async def search(request: BranchRequest) -> BranchResult:
        async with runtime.transaction() as connection:
            return await search_lexical_chunks(
                connection,
                text=request.question,
                context=request.context,
                limit=request.limit,
                language=request.language,
            )

    return search


async def _run(
    branch: RetrievalBranch,
    search: BranchSearch,
    request: BranchRequest,
    deadline: float,
) -> _Ended:
    loop = asyncio.get_running_loop()
    started = loop.time()
    remaining = max(deadline - started, 0.0)
    budget = asyncio.timeout_at(deadline)
    status = BranchStatus.FAILED
    error: str | None = None
    result: BranchResult | None = None
    try:
        async with budget:
            result = await search(replace(request, timeout_seconds=remaining))
        status = BranchStatus.SUCCEEDED
    except _BRANCH_FAILURES as failure:
        error = type(failure).__name__
    # Past the deadline, whatever the branch did counts as timed out: the
    # cancel itself, an error raised while it was being cancelled, or a late
    # answer from a branch that ignored the cancel or blocked the event loop.
    if budget.expired() or loop.time() > deadline:
        status, error, result = BranchStatus.TIMED_OUT, None, None
    return _Ended(
        BranchDiagnostics(
            branch=branch,
            status=status,
            elapsed_seconds=loop.time() - started,
            candidates=len(result.candidates) if result is not None else 0,
            truncated=result.truncated if result is not None else False,
            error=error,
        ),
        result,
    )


def _check(
    question: str,
    *,
    context: AuthorizationContext,
    language: Language | None,
    branches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
    timeout: float,
    k: int,
) -> None:
    if type(question) is not str or not question.strip():
        raise InvalidHybridQuery("the question must be text that is not blank")
    if len(question) > MAX_QUERY_CHARACTERS:
        raise InvalidHybridQuery(
            f"the question must be at most {MAX_QUERY_CHARACTERS} characters"
        )
    # PostgreSQL text cannot hold NUL, and UTF-8 cannot hold a lone surrogate.
    if "\x00" in question or not _encodable(question):
        raise InvalidHybridQuery("the question must be valid text")
    if type(context) is not AuthorizationContext:
        raise InvalidHybridQuery("context must be an AuthorizationContext")
    if language is not None and type(language) is not Language:
        raise InvalidHybridQuery("language must be a Language or None")
    # bool is an int, so `True` would otherwise pass as a limit of 1.
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise InvalidHybridQuery(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    # The deadline is a float, so a huge int must not overflow it.
    if type(timeout) not in (int, float) or not 0 < timeout <= sys.float_info.max:
        raise InvalidHybridQuery("timeout_seconds must be finite and positive")
    if type(k) is not int or k < 1:
        raise InvalidHybridQuery("k must be a positive int")
    # Checked at run time too, because a caller may pass any object.
    if not isinstance(cast(object, branches), Mapping) or not branches:
        raise InvalidHybridQuery("at least one branch is required")
    for branch, search in branches.items():
        if type(branch) is not RetrievalBranch or not callable(search):
            raise InvalidHybridQuery(
                "every branch must map a RetrievalBranch to a search"
            )


def _encodable(text: str) -> bool:
    try:
        text.encode()
    except UnicodeEncodeError:
        return False
    return True
```

**Перевірка:**

```bash
uv run ruff check src/knowledge_service/retrieval/hybrid.py
uv run pyright src/knowledge_service/retrieval/hybrid.py
```

Очікувано: жодних зауважень і `0 errors`.

### Крок 3 — Крок з програмуванням: протестуйте правила на підроблених гілках

Цей крок доводить кожне правило, використовуючи підроблені гілки, час яких ви повністю контролюєте.

**Простими словами:** Ці тести замінюють справжні пошуки маленькими підробками. Підробка може відповісти одразу, чекати вічно, кинути помилку або повідомити іншій підробці, що вона стартувала. З ними тести перевіряють, що обидва пошуки йдуть одночасно, що повільний зупиняється на дедлайні, що зламаний потрапляє у звіт і пропускається, що справжній баг не приховується і що погані вхідні дані відхиляються, перш ніж щось почнеться.

`FakeBranch` — це підробка. Вона записує кожен `BranchRequest`, який отримує, тож тест може перевірити, про що її питали. `wait_for` змушує її чекати на подію перед відповіддю; `_never()` дає подію, яку ніхто не встановить, тож гілка чекає, доки її не скасують. `started` встановлюється, щойно гілка стартує, що дозволяє двом підробкам чекати одна на одну. `cancelled` стає True, коли підробку скасовано. `_ranked(1, 2, 3)` робить впорядкований список із фрагментом 1 першим, як у M05-T05. Три маленькі функції, наприклад `_answers_despite_the_cancel`, грають гілки, що поводяться погано на дедлайні.

| Тест | Що він доводить |
|---|---|
| `test_both_branches_are_fused_and_reported` | Успіх: злитий порядок, внески й діагностика для обох гілок |
| `test_every_branch_gets_the_same_question_reader_and_deadline` | Контекст читача, запитання, мова, ліміт і ID запиту доходять до обох гілок |
| `test_the_branches_run_at_the_same_time` | Кожна підробка чекає, доки інша стартує; одна за одною обидві пропустили б дедлайн |
| `test_a_branch_that_misses_the_deadline_is_cancelled_and_reported` | Для будь-якої гілки: скасовано, `timed_out`, результати іншої збережено, `degraded` |
| `test_a_branch_that_fails_is_reported_by_its_error_type` | Вісім видів відмов стають `failed` з назвою типу, а інша гілка все одно враховується |
| `test_whatever_a_branch_does_after_the_deadline_counts_as_timed_out` | Гілка, яка ігнорує скасування, блокує цикл подій або падає під час скасування, — це `timed_out`, а її відповідь відкинуто |
| `test_no_branch_succeeding_raises_unavailable_with_diagnostics` | Типізована помилка, що несе обидві діагностики |
| `test_a_bug_in_a_branch_propagates_and_cancels_the_other` | П'ять видів багів, наприклад `ProgrammingError` і `FileNotFoundError`, не приховуються, а іншу гілку скасовано |
| `test_cancelling_the_caller_cancels_every_branch` | Жодна гілка не лишається працювати, коли викликач здається |
| `test_a_branch_left_out_is_neither_run_nor_reported` | Словник з однією гілкою запускає лише її |
| `test_the_fused_list_is_cut_to_the_limit_and_says_when_more_exist` | Обрізання до ліміту й обидві причини для `truncated` |
| `test_k_reaches_the_fusion` | `k` змінює порядок, як у M05-T05 |
| `test_bad_input_is_refused_before_any_branch_starts` | 25 поганих вхідних даних кидають `InvalidHybridQuery`, і жодну гілку не викликано |
| `test_a_branch_that_is_not_a_retrieval_branch_is_refused` | Звичайний ключ `"lexical"` відхиляється |
| `test_nothing_shows_the_question_or_an_error_message` | Підкинутий маркер ніколи не з'являється в repr, діагностиці чи помилці |

**Дія:** створіть `tests/test_hybrid_retrieval.py`:

```python
"""Checks on running both retrieval branches at once and fusing the result.

The branches here are fakes, so each test decides exactly when a branch
answers, fails, or never answers.
"""

import asyncio
import contextlib
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy import exc

from knowledge_service.access import AuthorizationContext
from knowledge_service.documents import ContentVersion
from knowledge_service.embeddings.port import EmbeddingUnavailable
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId, UserId
from knowledge_service.language import Language
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    BranchRequest,
    BranchResult,
    BranchSearch,
    BranchStatus,
    HybridRetrieval,
    HybridRetrievalUnavailable,
    InvalidHybridQuery,
    retrieve_hybrid,
)
from knowledge_service.retrieval.lexical_search import InvalidLexicalQuery
from knowledge_service.retrieval.vector_search import (
    InvalidVectorQuery,
    NoCurrentEmbeddingSpace,
    VectorCandidate,
)

DENSE = RetrievalBranch.DENSE
LEXICAL = RetrievalBranch.LEXICAL
CONTEXT = AuthorizationContext(user_id=UserId(UUID(int=7)))
REQUEST_ID = UUID(int=99)
QUESTION = "How do I request parental leave?"
MARKER = "zebramarker"
# Long enough for every fake that answers, short enough to keep tests fast.
SHORT = 0.05


def _candidate(number: int, rank: int) -> VectorCandidate:
    return VectorCandidate(
        rank=rank,
        chunk_id=ChunkId(UUID(int=number)),
        document_id=DocumentId(UUID(int=10_000 + number)),
        source_id=SourceId(UUID(int=1)),
        content_version=ContentVersion(1),
        ordinal=0,
        distance=rank / 100,
        external_id=f"doc-{number}.txt",
        title=f"Title {number}",
        heading_path=None,
        source_anchor=None,
        text=f"Text of Chunk {number}",
    )


def _ranked(*numbers: int) -> tuple[VectorCandidate, ...]:
    return tuple(_candidate(number, rank) for rank, number in enumerate(numbers, 1))


@dataclass(frozen=True, slots=True)
class Found:
    candidates: Sequence[Candidate]
    truncated: bool = False


@dataclass(slots=True)
class FakeBranch:
    """A branch that records what it was asked and answers as told."""

    found: Sequence[Candidate] = ()
    truncated: bool = False
    error: Exception | None = None
    # When set, the branch waits for it before answering.
    wait_for: asyncio.Event | None = None
    # When set, the branch sets it as soon as it starts.
    started: asyncio.Event | None = None
    requests: list[BranchRequest] = field(default_factory=list[BranchRequest])
    cancelled: bool = False

    async def __call__(self, request: BranchRequest) -> BranchResult:
        self.requests.append(request)
        if self.started is not None:
            self.started.set()
        try:
            if self.wait_for is not None:
                await self.wait_for.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        if self.error is not None:
            raise self.error
        return Found(self.found, self.truncated)


def _never() -> asyncio.Event:
    return asyncio.Event()


async def _retrieve(
    branches: Mapping[RetrievalBranch, BranchSearch],
    *,
    question: str = QUESTION,
    limit: int = 10,
    timeout_seconds: float = 5.0,
    k: int = 60,
) -> HybridRetrieval:
    return await retrieve_hybrid(
        question,
        context=CONTEXT,
        branches=branches,
        limit=limit,
        timeout_seconds=timeout_seconds,
        request_id=REQUEST_ID,
        language=Language.ENGLISH,
        k=k,
    )


def _order(result_candidates: Sequence[Candidate]) -> list[int]:
    return [candidate.chunk_id.value.int for candidate in result_candidates]


async def test_both_branches_are_fused_and_reported() -> None:
    dense = FakeBranch(found=_ranked(1, 2, 3))
    lexical = FakeBranch(found=_ranked(3, 4))

    result = await _retrieve({DENSE: dense, LEXICAL: lexical})

    assert _order(result.candidates) == [3, 1, 2, 4]
    assert [part.branch for part in result.candidates[0].contributions] == [
        DENSE,
        LEXICAL,
    ]
    assert [(b.branch, b.status, b.candidates) for b in result.branches] == [
        (DENSE, BranchStatus.SUCCEEDED, 3),
        (LEXICAL, BranchStatus.SUCCEEDED, 2),
    ]
    assert all(b.error is None and b.elapsed_seconds >= 0 for b in result.branches)
    assert not result.degraded
    assert not result.truncated
    assert (result.limit, result.k) == (10, 60)


async def test_every_branch_gets_the_same_question_reader_and_deadline() -> None:
    dense, lexical = FakeBranch(), FakeBranch()

    await _retrieve({DENSE: dense, LEXICAL: lexical}, limit=7, timeout_seconds=5.0)

    for branch in (dense, lexical):
        (request,) = branch.requests
        assert request.question == QUESTION
        assert request.context is CONTEXT
        assert request.language is Language.ENGLISH
        assert request.limit == 7
        assert request.request_id == REQUEST_ID
        assert 0 < request.timeout_seconds <= 5.0


async def test_the_branches_run_at_the_same_time() -> None:
    # Each branch waits until the other has started, so run one after the
    # other they would both miss the deadline.
    dense_started, lexical_started = asyncio.Event(), asyncio.Event()
    dense = FakeBranch(
        found=_ranked(1), started=dense_started, wait_for=lexical_started
    )
    lexical = FakeBranch(
        found=_ranked(2), started=lexical_started, wait_for=dense_started
    )

    result = await _retrieve({DENSE: dense, LEXICAL: lexical}, timeout_seconds=1.0)

    assert [b.status for b in result.branches] == [BranchStatus.SUCCEEDED] * 2


@pytest.mark.parametrize(("slow", "fast"), [(DENSE, LEXICAL), (LEXICAL, DENSE)])
async def test_a_branch_that_misses_the_deadline_is_cancelled_and_reported(
    slow: RetrievalBranch, fast: RetrievalBranch
) -> None:
    late = FakeBranch(found=_ranked(1), wait_for=_never())
    on_time = FakeBranch(found=_ranked(2, 3))
    loop = asyncio.get_running_loop()
    started = loop.time()

    result = await _retrieve({slow: late, fast: on_time}, timeout_seconds=SHORT)

    assert loop.time() - started < 1.0
    assert late.cancelled
    assert _order(result.candidates) == [2, 3]
    report = {b.branch: b for b in result.branches}
    assert report[slow].status is BranchStatus.TIMED_OUT
    assert (report[slow].candidates, report[slow].error) == (0, None)
    assert report[slow].elapsed_seconds >= SHORT * 0.9
    assert report[fast].status is BranchStatus.SUCCEEDED
    assert report[fast].elapsed_seconds < SHORT
    assert result.degraded


@pytest.mark.parametrize(
    "error",
    [
        EmbeddingUnavailable(),
        NoCurrentEmbeddingSpace("the current embedding space is another model"),
        InvalidLexicalQuery("the query has no words"),
        exc.OperationalError("SELECT 1", {}, Exception("server closed")),
        exc.InterfaceError("SELECT 1", {}, Exception("connection already closed")),
        exc.TimeoutError("QueuePool limit reached"),
        ConnectionResetError(),
        TimeoutError(),
    ],
    ids=lambda error: type(error).__name__,
)
async def test_a_branch_that_fails_is_reported_by_its_error_type(
    error: Exception,
) -> None:
    dense = FakeBranch(error=error)
    lexical = FakeBranch(found=_ranked(5))

    result = await _retrieve({DENSE: dense, LEXICAL: lexical})

    assert _order(result.candidates) == [5]
    assert result.branches[0].status is BranchStatus.FAILED
    assert result.branches[0].error == type(error).__name__
    assert result.degraded


async def test_no_branch_succeeding_raises_unavailable_with_diagnostics() -> None:
    dense = FakeBranch(error=EmbeddingUnavailable())
    lexical = FakeBranch(wait_for=_never())

    with pytest.raises(HybridRetrievalUnavailable) as caught:
        await _retrieve({DENSE: dense, LEXICAL: lexical}, timeout_seconds=SHORT)

    assert [(b.status, b.error) for b in caught.value.branches] == [
        (BranchStatus.FAILED, "EmbeddingUnavailable"),
        (BranchStatus.TIMED_OUT, None),
    ]


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("a bug"),
        exc.ProgrammingError("SELECT no_such_column", {}, Exception("no column")),
        exc.CompileError("cannot compile"),
        FileNotFoundError("config.toml"),
        InvalidVectorQuery("the query has the wrong length"),
    ],
    ids=lambda error: type(error).__name__,
)
async def test_a_bug_in_a_branch_propagates_and_cancels_the_other(
    error: Exception,
) -> None:
    dense = FakeBranch(error=error)
    lexical = FakeBranch(wait_for=_never())

    with pytest.raises(ExceptionGroup) as caught:
        await _retrieve({DENSE: dense, LEXICAL: lexical})

    assert caught.group_contains(type(error))
    assert lexical.cancelled


async def _answers_despite_the_cancel(request: BranchRequest) -> BranchResult:
    with contextlib.suppress(asyncio.CancelledError):
        await _never().wait()
    return Found(_ranked(7))


async def _blocks_the_event_loop(request: BranchRequest) -> BranchResult:
    # Blocks on purpose, as CPU-bound or synchronous work would.
    time.sleep(SHORT * 3)  # noqa: ASYNC251
    return Found(_ranked(7))


async def _fails_while_cancelled(request: BranchRequest) -> BranchResult:
    try:
        await _never().wait()
    except asyncio.CancelledError:
        raise exc.OperationalError("ROLLBACK", {}, Exception("lost")) from None
    return Found(_ranked(7))


@pytest.mark.parametrize(
    "late",
    [_answers_despite_the_cancel, _blocks_the_event_loop, _fails_while_cancelled],
    ids=lambda search: search.__name__,
)
async def test_whatever_a_branch_does_after_the_deadline_counts_as_timed_out(
    late: BranchSearch,
) -> None:
    # Dense starts first and answers at once, before the late branch runs.
    on_time = FakeBranch(found=_ranked(2))

    result = await _retrieve({DENSE: on_time, LEXICAL: late}, timeout_seconds=SHORT)

    assert _order(result.candidates) == [2]
    assert [(b.status, b.candidates, b.error) for b in result.branches] == [
        (BranchStatus.SUCCEEDED, 1, None),
        (BranchStatus.TIMED_OUT, 0, None),
    ]


async def test_cancelling_the_caller_cancels_every_branch() -> None:
    dense_started, lexical_started = asyncio.Event(), asyncio.Event()
    dense = FakeBranch(started=dense_started, wait_for=_never())
    lexical = FakeBranch(started=lexical_started, wait_for=_never())
    task = asyncio.create_task(_retrieve({DENSE: dense, LEXICAL: lexical}))
    await dense_started.wait()
    await lexical_started.wait()

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert dense.cancelled
    assert lexical.cancelled


async def test_a_branch_left_out_is_neither_run_nor_reported() -> None:
    lexical = FakeBranch(found=_ranked(4, 2))

    result = await _retrieve({LEXICAL: lexical})

    assert _order(result.candidates) == [4, 2]
    assert [b.branch for b in result.branches] == [LEXICAL]
    assert not result.degraded


@pytest.mark.parametrize(
    ("limit", "dense_truncated", "expected", "truncated"),
    [
        (2, False, [3, 1], True),
        (4, False, [3, 1, 2, 4], False),
        (4, True, [3, 1, 2, 4], True),
        (10, False, [3, 1, 2, 4], False),
    ],
)
async def test_the_fused_list_is_cut_to_the_limit_and_says_when_more_exist(
    limit: int, dense_truncated: bool, expected: list[int], truncated: bool
) -> None:
    dense = FakeBranch(found=_ranked(1, 2, 3), truncated=dense_truncated)
    lexical = FakeBranch(found=_ranked(3, 4))

    result = await _retrieve({DENSE: dense, LEXICAL: lexical}, limit=limit)

    assert _order(result.candidates) == expected
    assert [b.truncated for b in result.branches] == [dense_truncated, False]
    assert [candidate.rank for candidate in result.candidates] == list(
        range(1, len(expected) + 1)
    )
    assert result.truncated is truncated


async def test_k_reaches_the_fusion() -> None:
    dense = FakeBranch(found=_ranked(1, 11, 12, 2))
    lexical = FakeBranch(found=_ranked(21, 22, 23, 2))

    by_default = await _retrieve({DENSE: dense, LEXICAL: lexical})
    by_one = await _retrieve({DENSE: dense, LEXICAL: lexical}, k=1)

    assert _order(by_default.candidates)[0] == 2
    assert _order(by_one.candidates)[0] == 1
    assert by_one.k == 1
    assert by_one.candidates[0].contributions[0].contribution == 1 / 2


@pytest.mark.parametrize(
    "change",
    [
        {"question": ""},
        {"question": "   \n"},
        {"question": None},
        {"question": 5},
        {"question": "x" * 1_001},
        {"question": "parental" + chr(0) + "leave"},
        {"question": "leave" + chr(0xD800)},
        {"context": None},
        {"language": "english"},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"limit": 1.0},
        {"timeout_seconds": 0},
        {"timeout_seconds": -1.0},
        {"timeout_seconds": math.nan},
        {"timeout_seconds": math.inf},
        {"timeout_seconds": True},
        {"timeout_seconds": 10**400},
        {"timeout_seconds": "1"},
        {"k": 0},
        {"k": True},
        {"branches": {}},
        {"branches": "dense"},
        {"branches": {DENSE: None}},
    ],
    ids=repr,
)
async def test_bad_input_is_refused_before_any_branch_starts(
    change: dict[str, object],
) -> None:
    dense, lexical = FakeBranch(), FakeBranch()
    arguments: dict[str, object] = {
        "question": QUESTION,
        "context": CONTEXT,
        "branches": {DENSE: dense, LEXICAL: lexical},
        "limit": 10,
        "timeout_seconds": 5.0,
        "request_id": REQUEST_ID,
        "k": 60,
    }
    arguments.update(change)

    with pytest.raises(InvalidHybridQuery):
        await retrieve_hybrid(**cast(dict[str, Any], arguments))

    assert dense.requests == lexical.requests == []


async def test_a_branch_that_is_not_a_retrieval_branch_is_refused() -> None:
    lexical = FakeBranch()
    branches = cast(Mapping[RetrievalBranch, BranchSearch], {"lexical": lexical})

    with pytest.raises(InvalidHybridQuery):
        await _retrieve(branches)

    assert lexical.requests == []


async def test_nothing_shows_the_question_or_an_error_message() -> None:
    question = f"{MARKER} parental leave"
    dense = FakeBranch(error=EmbeddingUnavailable(MARKER))
    lexical = FakeBranch(found=_ranked(1))

    result = await _retrieve({DENSE: dense, LEXICAL: lexical}, question=question)

    assert MARKER not in repr(result)
    assert MARKER not in repr(dense.requests[0])

    failing = FakeBranch(error=InvalidLexicalQuery(MARKER))
    with pytest.raises(HybridRetrievalUnavailable) as caught:
        await _retrieve({LEXICAL: failing}, question=question)
    error = caught.value
    shown = [str(error), repr(error.args), repr(error.branches)]
    assert all(MARKER not in text for text in shown)
    assert error.__cause__ is None
    assert error.__context__ is None
```

**Перевірка:**

```bash
uv run pytest tests/test_hybrid_retrieval.py -q
```

Очікувано: `56 passed`.

**Навмисна поломка.** Змусьте гілки виконуватися одна за одною. У `hybrid.py` замініть:

```python
    async with asyncio.TaskGroup() as group:
        tasks = [
            group.create_task(_run(branch, branches[branch], request, deadline))
            for branch in RetrievalBranch
            if branch in branches
        ]
    ended = [task.result() for task in tasks]
```

на:

```python
    ended = [
        await _run(branch, branches[branch], request, deadline)
        for branch in RetrievalBranch
        if branch in branches
    ]
```

Запустіть тести знову. Очікувано: 7 failed, 49 passed:

- `test_the_branches_run_at_the_same_time`, бо кожна підробка чекає, доки стартує інша;
- `test_a_branch_that_misses_the_deadline_is_cancelled_and_reported[dense-lexical]`, бо швидка лексична гілка тепер стартує лише після того, як повільна векторна витратила весь дедлайн;
- п'ять випадків `test_a_bug_in_a_branch_propagates_and_cancels_the_other`, бо без групи задач баг приходить сам по собі, а не всередині `ExceptionGroup`, і ніщо не скасовує іншу гілку.

Скасуйте зміну і запустіть тести ще раз: усі проходять.

### Крок 4 — Крок з програмуванням: протестуйте на справжніх пошуках і справжній базі даних

Цей крок доводить, що справжні гілки використовують права читача і що дедлайн справді зупиняє запит на сервері.

**Простими словами:** Ці тести зберігають кілька документів, один з яких бачить лише інший користувач, і запускають справжній гібридний пошук через справжню базу даних. Вони перевіряють, що кожен читач отримує лише те, що йому можна бачити, з обох пошуків. Ще вони підключають гілку, запит якої спить 30 секунд, і перевіряють, що на дедлайні PostgreSQL справді зупиняє цей запит, а з'єднання повертається в пул. Інший тест змушує векторний пошук впасти так, як він падає під час зміни моделі, і перевіряє, що пошук за словами все одно відповідає. Останній перевіряє, що векторний пошук не позичає з'єднання з базою даних, поки чекає на ембединг.

| Тест | Що він доводить |
|---|---|
| `test_both_real_branches_search_as_the_reader` | Документ для іншого користувача ніколи не доходить до цього читача з жодної гілки; той користувач отримує його з обох |
| `test_a_slow_query_is_cancelled_on_the_server_at_the_deadline` | При дедлайні в 1 секунду спляча гілка — `timed_out`, `pg_stat_activity` не показує запущеного `pg_sleep`, і жодне з'єднання не лишилося позиченим |
| `test_a_model_the_current_space_does_not_hold_fails_only_dense` | Модель на 4 виміри проти простору на 3 виміри валить векторну гілку з `NoCurrentEmbeddingSpace`; лексична все одно відповідає, а результат неповний |
| `test_dense_holds_no_connection_while_it_waits_for_the_embedding` | Поки виклик ембедингів чекає, жодне з'єднання з базою даних не позичене, тож повільний провайдер не може вичерпати пул |

Кілька назв варто пояснити. `FakeEmbeddingModel` з `tests/fakes.py` перетворює текст на фіксований вектор, тож `question_vector()` може зберегти «власний вектор запитання» на фрагменті, щоб зробити його найкращим збігом для векторної гілки. `pg_stat_activity` — це список запущених запитів у PostgreSQL. Коментар `/* hybrid-slow-branch */` у `SLOW_QUERY` дозволяє легко знайти там повільний запит. `checkedout()` рахує з'єднання пулу, які зараз позичені.

**Дія:** створіть `tests/test_hybrid_retrieval_integration.py`:

```python
"""Hybrid retrieval against the real database, with both real branches.

The dense branch embeds the question with a fake model and searches the
stored vectors; the lexical branch searches the stored words. Both run on
their own connections from one runtime.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.pool import QueuePool

from fakes import FakeEmbeddingModel
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, EmbeddingSpaceId, SourceId, UserId
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.embeddings import (
    insert_chunk_embeddings,
    load_embedding_space_id,
)
from knowledge_service.persistence.schema import embedding_spaces
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    BranchRequest,
    BranchResult,
    BranchSearch,
    BranchStatus,
    HybridRetrieval,
    dense_branch,
    lexical_branch,
    retrieve_hybrid,
)
from knowledge_service.settings import Settings

DENSE = RetrievalBranch.DENSE
LEXICAL = RetrievalBranch.LEXICAL
SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000046")
OBSERVED_AT = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
READER = UserId.parse("00000000-0000-0000-0000-0000000000f1")
OTHER_USER = UserId.parse("00000000-0000-0000-0000-0000000000f2")
QUESTION = "parental leave"
# Tags the slow query, so the test can look for it on the server.
SLOW_QUERY = "SELECT pg_sleep(30) /* hybrid-slow-branch */"

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    model = FakeEmbeddingModel(dimensions=3)
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE.value})
        await connection.execute(
            sa.insert(embedding_spaces).values(
                space_id=EmbeddingSpaceId.new().value,
                provider=model.identity.provider,
                model=model.identity.model,
                dimensions=model.identity.dimensions,
                is_current=True,
                created_at=OBSERVED_AT,
            )
        )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def a_document(
    runtime: DatabaseRuntime,
    name: str,
    *,
    text: str,
    vector: Sequence[float],
    readers: Sequence[Subject] = (Subject.public(),),
) -> DocumentId:
    """Store a Document and give each of its Chunks the same vector."""
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version="v1",
            media_type="text/plain",
            content=text.encode(),
            access=SourceAccess(public=True),
            metadata=SourceMetadata(
                title="Handbook", modified_at=OBSERVED_AT, size_bytes=1
            ),
        ),
        source_id=SOURCE,
    )
    assert isinstance(outcome, NormalizedDocument)
    reconciled = await reconcile_document(
        runtime, outcome, readers=readers, observed_at=OBSERVED_AT
    )
    document_id = reconciled.document.document_id
    model = FakeEmbeddingModel(dimensions=3)
    async with runtime.transaction() as connection:
        space_id = await load_embedding_space_id(connection, model.identity)
        assert space_id is not None
        stored = await load_chunks(connection, document_id=document_id)
        await insert_chunk_embeddings(
            connection,
            space_id=space_id,
            dimensions=model.identity.dimensions,
            vectors={chunk.chunk_id: vector for chunk in stored},
            created_at=OBSERVED_AT,
        )
    return document_id


async def question_vector() -> tuple[float, ...]:
    batch = await FakeEmbeddingModel(dimensions=3).embed(
        [QUESTION], request_id=UUID(int=1), timeout_seconds=1.0
    )
    return tuple(batch.vectors[0])


async def retrieve(
    branches: dict[RetrievalBranch, BranchSearch],
    *,
    reader: UserId = READER,
    timeout_seconds: float = 5.0,
) -> HybridRetrieval:
    return await retrieve_hybrid(
        QUESTION,
        context=AuthorizationContext(user_id=reader),
        branches=branches,
        limit=10,
        timeout_seconds=timeout_seconds,
        request_id=UUID(int=2),
    )


@pytest.mark.integration
async def test_both_real_branches_search_as_the_reader(
    runtime: DatabaseRuntime,
) -> None:
    near = await question_vector()
    leave = await a_document(
        runtime, "leave.txt", text="Parental leave policy for staff.", vector=near
    )
    secret = await a_document(
        runtime,
        "secret.txt",
        text="Parental leave plans before the announcement.",
        vector=near,
        readers=(Subject.user(OTHER_USER),),
    )
    branches: dict[RetrievalBranch, BranchSearch] = {
        DENSE: dense_branch(runtime, FakeEmbeddingModel(dimensions=3)),
        LEXICAL: lexical_branch(runtime),
    }

    seen = await retrieve(branches)
    other = await retrieve(branches, reader=OTHER_USER)

    assert [b.status for b in seen.branches] == [BranchStatus.SUCCEEDED] * 2
    assert {c.document_id for c in seen.candidates} == {leave}
    assert [p.branch for p in seen.candidates[0].contributions] == [DENSE, LEXICAL]
    assert {c.document_id for c in other.candidates} == {leave, secret}
    found_secret = next(c for c in other.candidates if c.document_id == secret)
    assert [p.branch for p in found_secret.contributions] == [DENSE, LEXICAL]


def sleeping_branch(runtime: DatabaseRuntime) -> BranchSearch:
    """A branch whose query outlasts any deadline in these tests."""

    async def search(request: BranchRequest) -> BranchResult:
        async with runtime.transaction() as connection:
            await connection.execute(sa.text(SLOW_QUERY))
        raise AssertionError("the slow query was expected to be cancelled")

    return search


async def running_slow_queries(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        found = await connection.execute(
            sa.text(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE state = 'active' AND query = :query"
            ),
            {"query": SLOW_QUERY},
        )
        return int(found.scalar_one())


@pytest.mark.integration
async def test_a_slow_query_is_cancelled_on_the_server_at_the_deadline(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "leave.txt", text="Parental leave policy.", vector=(1.0, 0.0, 0.0)
    )
    branches: dict[RetrievalBranch, BranchSearch] = {
        DENSE: dense_branch(runtime, FakeEmbeddingModel(dimensions=3)),
        LEXICAL: sleeping_branch(runtime),
    }
    loop = asyncio.get_running_loop()
    started = loop.time()

    result = await retrieve(branches, timeout_seconds=1.0)

    assert loop.time() - started < 10
    report = {b.branch: b.status for b in result.branches}
    assert report == {DENSE: BranchStatus.SUCCEEDED, LEXICAL: BranchStatus.TIMED_OUT}
    assert len(result.candidates) == 1
    # The server may take a moment to act on the cancel request.
    for _ in range(50):
        if await running_slow_queries(runtime) == 0:
            break
        await asyncio.sleep(0.1)
    assert await running_slow_queries(runtime) == 0
    assert cast(QueuePool, runtime.engine.pool).checkedout() == 0


@pytest.mark.integration
async def test_a_model_the_current_space_does_not_hold_fails_only_dense(
    runtime: DatabaseRuntime,
) -> None:
    leave = await a_document(
        runtime, "leave.txt", text="Parental leave policy.", vector=(1.0, 0.0, 0.0)
    )
    branches: dict[RetrievalBranch, BranchSearch] = {
        DENSE: dense_branch(runtime, FakeEmbeddingModel(dimensions=4)),
        LEXICAL: lexical_branch(runtime),
    }

    result = await retrieve(branches)

    assert [(b.status, b.error) for b in result.branches] == [
        (BranchStatus.FAILED, "NoCurrentEmbeddingSpace"),
        (BranchStatus.SUCCEEDED, None),
    ]
    assert {c.document_id for c in result.candidates} == {leave}
    assert result.degraded


@pytest.mark.integration
async def test_dense_holds_no_connection_while_it_waits_for_the_embedding(
    runtime: DatabaseRuntime,
) -> None:
    leave = await a_document(
        runtime, "leave.txt", text="Parental leave policy.", vector=(1.0, 0.0, 0.0)
    )
    answer = asyncio.Event()
    model = FakeEmbeddingModel(dimensions=3, wait_for=answer)
    pool = cast(QueuePool, runtime.engine.pool)
    task = asyncio.create_task(retrieve({DENSE: dense_branch(runtime, model)}))
    # Wait until the dense branch is inside the embedding call.
    for _ in range(500):
        if model.batches:
            break
        await asyncio.sleep(0.01)
    assert model.batches

    held_while_embedding = pool.checkedout()
    answer.set()
    result = await task

    assert held_while_embedding == 0
    assert {c.document_id for c in result.candidates} == {leave}
    assert pool.checkedout() == 0
```

**Перевірка:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_hybrid_retrieval_integration.py -q -m integration
```

Очікувано: `4 passed`.

### Крок 5 — Без програмування: подивіться, як працюють правила

Цей крок показує чотири результати правил поруч, щоб ви могли порівняти їх зі своїми передбаченнями.

**Дія:** з кореня проєкту запустіть:

```bash
uv run python - <<'PY'
import asyncio
import sys
from uuid import UUID

sys.path.insert(0, "tests")
from test_hybrid_retrieval import CONTEXT, FakeBranch, _ranked

from knowledge_service.embeddings.port import EmbeddingUnavailable
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    HybridRetrievalUnavailable,
    retrieve_hybrid,
)

DENSE, LEXICAL = RetrievalBranch.DENSE, RetrievalBranch.LEXICAL


async def show(title, dense, lexical):
    print(title)
    try:
        result = await retrieve_hybrid(
            "parental leave",
            context=CONTEXT,
            branches={DENSE: dense, LEXICAL: lexical},
            limit=3,
            timeout_seconds=0.2,
            request_id=UUID(int=1),
        )
    except HybridRetrievalUnavailable as error:
        branches, found = error.branches, f"raised {type(error).__name__}"
    else:
        found = [c.chunk_id.value.int for c in result.candidates]
        found = f"Chunks {found}, truncated={result.truncated}, degraded={result.degraded}"
        branches = result.branches
    for b in branches:
        print(f"  {b.branch:<8} {b.status:<10} found={b.candidates} error={b.error}")
    print(f"  -> {found}")


async def main():
    await show("both answer", FakeBranch(found=_ranked(1, 2, 3)), FakeBranch(found=_ranked(3, 4)))
    await show("lexical is too slow", FakeBranch(found=_ranked(1, 2)), FakeBranch(wait_for=asyncio.Event()))
    await show("embedding is down", FakeBranch(error=EmbeddingUnavailable()), FakeBranch(found=_ranked(4)))
    await show("nothing works", FakeBranch(error=EmbeddingUnavailable()), FakeBranch(wait_for=asyncio.Event()))


asyncio.run(main())
PY
```

Еталонний запуск надрукував:

```text
both answer
  dense    succeeded  found=3 error=None
  lexical  succeeded  found=2 error=None
  -> Chunks [3, 1, 2], truncated=True, degraded=False
lexical is too slow
  dense    succeeded  found=2 error=None
  lexical  timed_out  found=0 error=None
  -> Chunks [1, 2], truncated=False, degraded=True
embedding is down
  dense    failed     found=0 error=EmbeddingUnavailable
  lexical  succeeded  found=1 error=None
  -> Chunks [4], truncated=False, degraded=True
nothing works
  dense    failed     found=0 error=EmbeddingUnavailable
  lexical  timed_out  found=0 error=None
  -> raised HybridRetrievalUnavailable
```

Як це читати:

- **Обидві відповідають:** разом п'ять фрагментів, обрізаних до ліміту 3, тож `truncated=True`. Фрагмент 3 перший, бо його знайшли обидві гілки.
- **Лексична надто повільна:** через 0.2 секунди лексична — `timed_out`, а векторні фрагменти все одно повертаються з позначкою `degraded`.
- **Ембединги недоступні:** векторна — `failed` лише з типом помилки, а лексичний фрагмент повертається з позначкою `degraded`.
- **Нічого не працює:** жодна гілка не вдалася, тож викликач отримує `HybridRetrievalUnavailable` з обома звітами.

Запишіть у доказах, які з ваших передбачень із кроку 1 справдилися. Відповідь на останнє передбачення — тест повільного запиту з кроку 4.

**Перевірка:** ваш вивід збігається з еталонним, а в доказах названо одне передбачення, яке не справдилося, або сказано, що таких немає.

### Крок 6 — Без програмування: перевірки, документація, докази й прогрес

Цей крок доводить, що все досі працює, оновлює єдиний рядок архітектури, який зміна зробила застарілим, і записує результат.

**Дія 1:** у `docs/architecture.md` змініть рядок `retrieval` таблиці "Code layout" на:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition` |
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_hybrid_retrieval.py -q
make format-check
make lint
make typecheck
make test-integration
make check
make docs-check
```

Очікувано: усе проходить. Еталонний запуск дав `make test-integration` 288 passed і `make check` 1385 passed, 3 deselected. Запускайте набори з базою даних із заданою адресою бази даних, інакше їхні тести бази даних пропускаються, а не виконуються:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make check
```

**Дія 3:** заповніть файл доказів:

- **Design:** модуль і його типи, чому гілки — звичайні функції, дедлайн, правила часткової відмови і чому баги йдуть далі, ліміт і `truncated`, і що змінив огляд.
- **Verification:** команди й кількості, таблиця тестів із тим, що доводить кожен, вивід кроку 5, навмисна поломка з кроку 3 і відомі обмеження з розділу «Перш ніж почати».
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте завдання виконаним, додайте рядок M05-T06 у `docs/curriculum/PROGRESS.md`, укажіть наступним завданням M05-T07 і зробіть коміт із `m05-t06: build the hybrid retrieval module`.

## Запитання для роздумів

1. Що `retrieve_hybrid` приховує від коду, який її викликає? Перелічіть, про що викликачеві ніколи не треба думати, наприклад про з'єднання, дедлайн чи пошук, що впав.
2. Хтось згодом пришвидшує роботу, даючи кожній гілці власний повний `timeout_seconds` замість одного спільного дедлайну. Чому це може порушити ліміт часу, про який просив викликач, і на яку гілку це вплине найбільше?
3. Хтось інший «спрощує» `_run`, перехоплюючи кожну `Exception` як `failed`. Що стане важче помітити в продакшні і який тест це ловить?
4. Неповний результат має менше фрагментів, ніж повний. Чи має крок відповіді згодом відповідати з нього, попереджати читача чи утримуватися від відповіді? Що б ви виміряли, щоб вирішити?
5. `docs/operations.md` вимагає, щоб пошук займав менше 750 мс для 95% запитань. Що могло б довести, що цей дизайн надто повільний для цієї мети? Подумайте, що б ви засікали на справжніх запитаннях і яка гілка, на вашу думку, буде повільнішою.

## Контрольний список

- [ ] Файл доказів містить інваріант і ваші передбачення, записані до будь-якого запуску.
- [ ] `hybrid.py` визначає `retrieve_hybrid`, `dense_branch`, `lexical_branch`, типи діагностики і дві типізовані помилки.
- [ ] Обидві гілки стартують одночасно, кожна на власному з'єднанні, під одним дедлайном.
- [ ] Гілку, що пропустила дедлайн, скасовано, її запит зупиняється на сервері, і вона звітується як `timed_out`.
- [ ] Гілка, що впала з відомою помилкою, звітується за типом і не враховується; баг іде далі; коли жодна гілка не вдалася, кидається `HybridRetrievalUnavailable`.
- [ ] Погані вхідні дані кидають `InvalidHybridQuery`, перш ніж стартує будь-яка гілка.
- [ ] Контекст читача доходить до обох гілок, і прихований документ ніколи не повертається.
- [ ] Жоден текст запитання чи повідомлення помилки не з'являється в діагностиці, помилках чи repr.
- [ ] Ви виконали навмисну поломку, побачили, що тести падають, і скасували її.
- [ ] Ви запустили команду з кроку 5 і порівняли результат зі своїми передбаченнями.
- [ ] Усі перевірки проходять: форматування, лінтер, типи, юніт-, інтеграційні тести, документація.
- [ ] Рядок архітектури, докази, рядок у PROGRESS і коміт готові.

Коли напишете код, поділіться ним або виводом помилки, і я перевірю його за цим списком.
