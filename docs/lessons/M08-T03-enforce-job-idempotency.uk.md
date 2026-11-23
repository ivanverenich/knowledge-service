English version: [M08-T03-enforce-job-idempotency.md](M08-T03-enforce-job-idempotency.md)

# M08-T03 — Ідемпотентність Job

Завдання: [M08-T03 — Enforce job idempotency](../curriculum/milestones/M08-jobs-and-confluence/M08-T03-enforce-job-idempotency.md)

## Що ти зробиш

Цей урок гарантує, що повторний запит тієї самої роботи або повторна доставка того самого повідомлення ніколи не виконують роботу двічі. M08-T02 подбав, щоб жоден Job не загубився. Цей урок дбає, щоб жоден Job не подвоївся і щоб кожен дубль отримав ту саму відповідь, що й оригінал.

- **Один відкритий Job на намір.** Намір (intent) Job — це те, для чого він: його тип і ціль, наприклад «очистити фрагменти документа D». База тримає щонайбільше один відкритий Job на намір.
- **Повторний запит отримує відкритий Job.** Повторний запит, навіть одночасний, повертає наявний Job замість того, щоб зберегти другий.
- **Job, що впав, підхоплюється знову.** Якщо відкритий Job упав, але має спроби, новий запит ставить у чергу цей самий Job.
- **Результат комітиться разом із захопленням.** Обробник очищення бере Job і зберігає його результат, успіх чи збій, в одній транзакції, тож жодна інша доставка не проскочить посередині.
- **Дублі повідомляють збережений результат.** Друге повідомлення про Job повертає Job таким, яким його залишило перше, тож кожна доставка повідомляє той самий результат.
- **Доказ:** тести просять ту саму роботу двічі, одночасно, після збою й під час міграції та перевіряють, що Job один і ефект один.

| Що просить завдання | Де це зроблено |
|---|---|
| Ключі ідемпотентності з бізнес-наміру | Крок 3: намір — це тип плюс ціль, його тримає індекс `uq_jobs_open_intent` |
| Захоплювати роботу в транзакції | Крок 5: очищення захоплює Job, видаляє і зберігає результат в одній транзакції |
| Повертати попередні результати для безпечних дублів | Крок 4: `enqueue_job` повертає відкритий Job; Крок 6: `run_stored_job` повертає збережений Job для повторного повідомлення |
| Одночасні повторні доставки: один ефект, узгоджені кінцеві дані | Крок 7: два і три повідомлення одночасно, також коли спроба падає |
| Успіх, межа, типізована помилка або відмова | Крок 7: `InvalidJob`, `JobIntentContended`, база відмовляє другому відкритому Job |
| Справжня інфраструктура там, де перевіряється її поведінка | Крок 7: справжні блокування, унікальний індекс і міграція PostgreSQL |
| Оновити лише ту документацію, якої це стосується | Крок 9: `docs/architecture.md`, `docs/threat-model.md` |

У цьому уроці є код: кроки 2–7 пишуть код, міграцію і тести. Кроки 1, 8, 9 і 10 коду не потребують, хоча на кроці 9 ти редагуєш два документи.

Терміни цього уроку:

- **Ідемпотентний (idempotent):** зробити двічі — те саме, що зробити один раз, як двічі натиснути кнопку ліфта.
- **Намір (intent):** те, для чого Job, записане як його тип плюс ціль. Два запити з однаковим наміром просять ту саму роботу.
- **Ключ ідемпотентності (idempotency key):** значення, за яким два запити вважаються різними або однаковими. Тут це сам намір, а не випадковий токен, який вигадує той, хто просить.
- **Відкритий Job (open Job):** Job, який ще може зробити свою роботу: у черзі, виконується, або впав і має спроби.
- **Частковий унікальний індекс (partial unique index):** правило бази, що два рядки не можуть мати однакове значення, яке діє лише для рядків, що відповідають умові. Тут: два *відкриті* Job не можуть мати однаковий намір, а завершені можуть.
- **Захоплення (claim):** узяти Job із черги для однієї спроби, під блокуванням його рядка, щоб ніхто інший не взяв його одночасно.
- **Точка збереження (savepoint):** контрольна точка всередині транзакції. Відкат до неї скасовує лише роботу після неї, а все до неї, разом із блокуваннями, лишається.

## Перед початком

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілим, коли до нього дійдеш.

- **Що вже є.** M08-T02 зберігає Job, перш ніж про нього з'явиться повідомлення, а воркер виконує Job, лише поки той у черзі. Але ніщо не заважає двом Job для тієї самої роботи: якщо двічі позначити документ видаленим, з'являться два очищення, а `submit_job` щоразу зберігає новий Job.
- **Чому ключ береться з наміру.** «Очистити документ D» — та сама робота, хоч хто й коли просить. Тип плюс ціль як ключ нічого не вимагає від того, хто просить, і його не обійти, вигадавши новий токен.
- **Чому рахуються лише відкриті Job.** Коли очищення завершилось, той самий документ пізніше може знову потребувати очищення, наприклад, якщо він повернувся і знову зник. Тож правило стосується лише Job, які ще можуть виконатися, а завершені лишаються історією.
- **Чому Job, що впав, але має спроби, вважається відкритим.** Він ще може виконатися, тож другий Job поруч зробив би ту саму роботу двічі. Натомість новий запит ставить цей Job, що впав, у чергу на наступну спробу.
- **Чому правило тримає база.** Якщо спершу перевірити «чи є відкритий Job?», а потім вставити, двоє можуть обидва побачити «ні» й обидва вставити. Унікальний індекс змушує другу вставку чекати на першу і потім знайти її, тож жоден порядок подій не збереже двох.
- **Чому результат комітиться разом із захопленням.** Очищення з M08-T02 записувало збій у другій транзакції. Між ними Job знову виглядав як «у черзі», тож повторне повідомлення, яке чекало, виконувало роботу вдруге. Тепер точка збереження скасовує невдале видалення, а блокування захоплення лишається, і збій записується до того, як блокування зніметься.
- **Що отримує дубль.** У M08-T02 повторне повідомлення повертало `None`. Тепер воно отримує збережений Job, тож кожна доставка одного повідомлення повідомляє той самий статус, спробу і час завершення.
- **Міграція спершу прибирає.** Старий код міг лишити два відкриті Job на один намір, а унікальний індекс над ними не побудувати. Міграція лишає один на намір (спершу той, що виконується, далі той, що в черзі, далі той, що впав; найстаріший серед рівних) і закриває решту як `failed` з кодом `superseded_duplicate`.
- **Що знайшло рев'ю.** Незалежне адверсаріальне рев'ю атакувало першу версію цього коду:
  - Очищення, у якого видалення впало, знімало блокування до того, як записати збій, тож повторне повідомлення, що чекало, виконувало роботу ще раз, а інші отримували сиру помилку. Тепер результат комітиться разом із захопленням, а тест із трьома одночасними повідомленнями падає на старій версії.
  - Документ, який знову позначили видаленим після невдалого очищення, отримував той самий Job, що впав, і ніщо його не запускало. Тепер запит ставить його в чергу знову, і без цього падають два тести.
  - Міграція падала на базах із дубльованими відкритими Job, а перше виправлення лишало Job, що впав, замість того, що в черзі. Тепер вона спершу прибирає і віддає перевагу Job, який виконає воркер.
  - `enqueue_job` приймав уже завершений Job. Тепер він приймає лише новий Job у черзі.
  - Шторми до 12 одночасних запитувачів, відкатів і змін стану жодного разу не лишили двох відкритих Job на один намір.
- **Відомі обмеження.** Ці речі поки лишаються як є:
  - Job, що впав, але має спроби, виконується знову лише тоді, коли хтось знову просить ту саму роботу. Сам його ніщо не перезапускає, доки M08-T04 не визначить, хто володіє повторами.
  - Повідомлення про попередню спробу досі може запустити наступну, коли Job знову в черзі (з M08-T02). У кожної спроби все одно один ефект; час спроб належить M08-T04.
  - Намір — це весь ключ. Два запити, що означають різне для того самого типу й цілі, наприклад повна й інкрементальна синхронізація, рахувались би як один; M08-T10 вирішить, чи синхронізації потрібен багатший намір.
  - Обробники мусять кидати `JobAlreadyClaimed`, коли Job уже не в черзі; будь-яка інша помилка обробника не вважається дублем.

## Покрокова інструкція

Перед початком запусти PostgreSQL. Усі команди виконуються з кореня репозиторію. Для integration-перевірок задай URL тестової бази у своєму терміналі:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

Запускай проти цієї бази лише одну integration-команду за раз. Два запуски одночасно мігрують ту саму базу разом і можуть лишити її в зламаному стані.

### Крок 1 — У цьому кроці коду немає: запиши інваріант і свої прогнози

Перш ніж щось змінювати, запиши, що має бути правдою наприкінці і чого ти очікуєш. Прогнози перевіриш на кроках 8 і 10.

Створи `docs/evidence/M08-T03-enforce-job-idempotency.md`:

```markdown
# M08-T03 — Enforce job idempotency evidence

## Invariant

At most one open Job (queued, running, or failed with attempts left) exists
per intent, its kind and target, under any interleaving of requests. A
repeated request returns the open Job instead of storing a second one, and
queues it again if it failed. A handler claims a queued Job and stores its
outcome in the same transaction, so concurrent deliveries of one Job have
one effect, and every delivery reports the Job as stored.

## Owners

- Durable truth: the `jobs` row and the `uq_jobs_open_intent` index.
- One Job per intent: `enqueue_job`, for every caller.
- Running once: the handler's claim under a row lock, in one transaction.
- Retries: the stored Job (M08-T04); a new request also queues a failed Job again.
- Authorization: none yet; nothing outside the service submits Jobs.

## Expected failing check

Before the change,
`uv run pytest tests/test_job_idempotency_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.

## Predictions

- What a second `tombstone_missing` of the same Document returns as its purge Job:
- What three messages at once for a purge whose delete fails do to the Chunks and to the attempt count:
- How many tests fail if `enqueue_job` stops using `ON CONFLICT DO NOTHING`:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після двокрапки в кожному прогнозі стоїть твоя відповідь.

### Крок 2 — Крок з кодом: сказати, коли Job відкритий, і як виглядає відмова в захопленні

На цьому кроці значення Job отримує дві невеликі частини, якими користується решта уроку.

**Простими словами:** Job учиться відповідати на одне питання: чи може він ще зробити свою роботу? Може, поки чекає, поки виконується, і після збою, коли ще лишились спроби. Друга частина — зрозуміла помилка «цей Job уже взяли»; вона несе Job таким, яким він збережений, щоб той, хто отримав помилку, міг повідомити, що зробила попередня доставка.

| Частина | Що робить |
|---|---|
| `Job.is_open` | true для Job у черзі й тих, що виконуються, а також для Job, що впали, але мають спроби |
| `JobAlreadyClaimed` | `InvalidJob`, який кидає захоплення, коли Job уже не в черзі; `.job` — збережений Job |

Повідомлення лишається «only a queued job can run, not a ... one», тож наявний тест, що перевіряє цей текст, і далі проходить.


**Дія 1:** додай помилку одразу перед `JobKind`.

У `src/knowledge_service/jobs.py` знайди:

```python
class JobKind(StrEnum):
    """What a Job was queued to do."""
```

і заміни на:

```python
class JobAlreadyClaimed(InvalidJob):
    """A Job is no longer queued, so this delivery must not run it again.

    `job` is the Job as stored when the claim was refused: the outcome an
    earlier delivery already produced, or is producing.
    """

    def __init__(self, job: Job) -> None:
        super().__init__(f"only a queued job can run, not a {job.status} one")
        self.job = job


class JobKind(StrEnum):
    """What a Job was queued to do."""
```

**Дія 2:** додай властивість одразу перед `request`, після кінця `__post_init__`.

У `src/knowledge_service/jobs.py` знайди:

```python
            raise InvalidJob("an error code must not be empty")

    @classmethod
    def request(
```

і заміни на:

```python
            raise InvalidJob("an error code must not be empty")

    @property
    def is_open(self) -> bool:
        """Whether this Job can still do its work.

        A Job is open while it is queued or running, and while it has failed
        with attempts left, because it may still be queued again. At most one
        open Job exists per kind and target.
        """
        if self.status is JobStatus.FAILED:
            return self.attempt < self.max_attempts
        return self.status is not JobStatus.SUCCEEDED

    @classmethod
    def request(
```

**Дія 3:** протестуй обидві частини. Спершу додай імпорт.

У `tests/test_jobs.py` знайди:

```python
    IllegalJobTransition,
    InvalidJob,
    Job,
```

і заміни на:

```python
    IllegalJobTransition,
    InvalidJob,
    Job,
    JobAlreadyClaimed,
```

Потім додай ці два тести в кінець `tests/test_jobs.py`:

```python
def test_a_job_is_open_until_it_can_do_no_more_work() -> None:
    job = requested_job(max_attempts=2)
    started = job.start(started_at=QUEUED_AT)
    failed_once = started.fail(error_code="boom", finished_at=QUEUED_AT)
    failed_twice = (
        failed_once.retry(queued_at=QUEUED_AT)
        .start(started_at=QUEUED_AT)
        .fail(error_code="boom", finished_at=QUEUED_AT)
    )

    assert job.is_open
    assert started.is_open
    # A failed Job with attempts left may still be queued again.
    assert failed_once.is_open
    assert not failed_twice.is_open
    assert not started.succeed(finished_at=QUEUED_AT).is_open


def test_a_refused_claim_carries_the_job_as_stored() -> None:
    finished = (
        requested_job().start(started_at=QUEUED_AT).succeed(finished_at=QUEUED_AT)
    )

    refused = JobAlreadyClaimed(finished)

    assert isinstance(refused, InvalidJob)
    assert refused.job is finished
    assert str(refused) == "only a queued job can run, not a succeeded one"
```

**Перевірка:** `uv run pytest tests/test_jobs.py -q` показує `6 passed`.

### Крок 3 — Крок з кодом: дозволити в базі лише один відкритий Job на намір

На цьому кроці правило з'являється в самій базі, тож жоден шлях у коді не зможе його порушити.

**Простими словами:** база дізнається, що два Job, які ще можуть виконатися, не можуть мати однаковий тип і ціль. Завершені Job не рахуються, тож історія лишається. Перш ніж правило ввімкнеться, міграція прибирає дублі, які міг лишити старий код, і лишає той, який воркер найімовірніше виконає.

| Частина | Що містить або робить |
|---|---|
| `OPEN_JOB_PREDICATE` | умова відкритого Job, записана один раз у SQL; її ділять індекс і `enqueue_job` |
| `uq_jobs_open_intent` | частковий унікальний індекс на `kind, target_id` лише над відкритими Job |
| Міграція `e5f6a7b8c9d0` | закриває дубльовані відкриті Job, потім створює індекс |

Для кожного наміру міграція лишає спершу Job, що виконується, далі той, що в черзі, далі той, що впав, а серед рівних — найстаріший. Решту вона закриває як `failed` без спроб і з кодом `superseded_duplicate`. Робота в них та сама, тож Job, який лишився, її і зробить. Міграція повторює умову текстом, а не імпортує її, бо міграція мусить працювати й тоді, коли код застосунку згодом зміниться.


**Дія 1:** створи міграцію.

Створи `migrations/versions/e5f6a7b8c9d0_job_open_intent.py`:

```python
"""job open intent

Revision ID: e5f6a7b8c9d0
Revises: c2d3e4f5a6b7
Create Date: 2026-11-22 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "c2d3e4f5a6b7"

OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and attempt < max_attempts)"
)

# Earlier code queued a new Job for every request, so two open Jobs can share
# an intent. Keep one per intent and close the others as failed with no
# attempts left. Their work is the same, so the one kept still does it. A
# running Job is kept first, then a queued one, because a worker will run it;
# a failed one waits for a retry, so it is kept only when nothing else is.
CLOSE_DUPLICATE_OPEN_JOBS = """
with ranked as (
    select
        job_id,
        row_number() over (
            partition by kind, target_id
            order by
                case status when 'running' then 0 when 'queued' then 1 else 2 end,
                queued_at,
                job_id
        ) as rank
    from jobs
    where status in ('queued', 'running')
        or (status = 'failed' and attempt < max_attempts)
)
update jobs
set status = 'failed',
    attempt = max_attempts,
    started_at = coalesce(started_at, queued_at),
    finished_at = coalesce(finished_at, greatest(now(), started_at, queued_at)),
    last_error_code = 'superseded_duplicate'
from ranked
where jobs.job_id = ranked.job_id and ranked.rank > 1
"""


def upgrade() -> None:
    op.execute(CLOSE_DUPLICATE_OPEN_JOBS)
    op.create_index(
        "uq_jobs_open_intent",
        "jobs",
        ["kind", "target_id"],
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    )


def downgrade() -> None:
    # Duplicates closed by the upgrade stay closed: reopening them would
    # queue the same work twice.
    op.drop_index("uq_jobs_open_intent", table_name="jobs")
```

**Дія 2:** оголоси те саме правило в схемі. Спершу умову, одразу над таблицею `jobs`:

У `src/knowledge_service/persistence/schema.py` знайди:

```python
jobs = sa.Table(
```

і заміни на:

```python
# A Job that can still do its work: queued, running, or failed with attempts
# left. At most one such Job exists per kind and target (its intent).
OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and attempt < max_attempts)"
)

jobs = sa.Table(
```

Потім індекс, останнім елементом таблиці:

У `src/knowledge_service/persistence/schema.py` знайди:

```python
        postgresql_where=sa.text("status = 'queued' and published_at is null"),
    ),
)
```

і заміни на:

```python
        postgresql_where=sa.text("status = 'queued' and published_at is null"),
    ),
    sa.Index(
        "uq_jobs_open_intent",
        "kind",
        "target_id",
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    ),
)
```

**Дія 3:** дозволь новий індекс у списку індексів.

У `tests/test_indexes.py` знайди:

```python
    "uq_embedding_spaces_current",

```

і заміни на:

```python
    "uq_embedding_spaces_current",
    "uq_jobs_open_intent",

```

**Перевірка:** `uv run pytest tests/test_indexes.py -q` показує `4 passed`, а `uv run pytest tests/test_migrations_integration.py -q -m integration` показує `3 passed`, і це доводить, що міграція й оголошена таблиця збігаються.

### Крок 4 — Крок з кодом: зберігати Job лише тоді, коли в його наміру немає відкритого Job

На цьому кроці змінюється те, як зберігається кожен Job, тож повторний запит повертає Job, який уже існує.

**Простими словами:** збереження Job тепер спершу пробує його вставити, а база тихо пропускає вставку, якщо відкритий Job з тим самим наміром уже є. Тоді код читає цей відкритий Job і повертає його. Якщо відкритий Job упав, його знову ставлять у чергу на наступну спробу. Якщо два запити приходять разом, другий чекає, поки перший закомітиться, і знаходить його Job, тож зберігається завжди лише один.

| Частина | Що робить |
|---|---|
| `Enqueued` | відкритий Job для наміру і `created`: чи зберіг його саме цей виклик |
| `enqueue_job` | відхиляє все, крім нового Job у черзі; вставляє з `ON CONFLICT DO NOTHING` за індексом відкритих намірів; інакше повертає відкритий Job, а той, що впав, знову ставить у чергу |
| `open_job` / `load_open_job` | SQL-умова відкритого Job і читання відкритого Job для наміру |
| `_queue_again` | блокує відкритий Job, що впав, перевіряє, що він досі `failed`, і зберігає `retry` |
| `JobIntentContended` | кидається, якщо тричі поспіль відкритий Job завершився між вставкою і читанням |

Тут важливі три рішення:

- **Вирішує база, а не попередня перевірка.** `ON CONFLICT DO NOTHING` змушує другу вставку чекати на першу транзакцію, а потім пропустити. «Спершу select, потім insert» дав би обом побачити порожнечу й обом вставити.
- **Читання може промахнутися, тому воно обмежене.** Відкритий Job може завершитися між пропущеною вставкою і читанням. Тоді код пробує знову, щонайбільше три раунди, і кидає типізовану помилку замість нескінченного циклу.
- **Повторна постановка Job, що впав, бере його блокування.** Два запити можуть обидва побачити Job, що впав; блокування змушує другий побачити його вже в черзі, тож у чергу він стає один раз.

**Дія 1:** файл змінюється в багатьох місцях, тому заміни його цілком.


Заміни весь `src/knowledge_service/persistence/jobs.py` на:

```python
"""Store queued Jobs, at most one open Job per intent, and their messages."""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Final, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.identifiers import JobId
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence.schema import OPEN_JOB_PREDICATE, jobs

# An open Job can finish between our insert and our read of it; each such
# race costs one round, and three in a row mean something keeps closing Jobs.
MAX_ENQUEUE_ROUNDS: Final = 3


class JobIntentContended(RuntimeError):
    """The open Job for an intent kept finishing while a new one was requested."""


@dataclass(frozen=True, slots=True)
class Enqueued:
    """The open Job for an intent, and whether this request created it."""

    job: Job
    created: bool


def _job_row(job: Job) -> dict[str, object]:
    return {
        "job_id": job.job_id.value,
        "kind": job.kind.value,
        "target_id": job.target_id,
        "status": job.status.value,
        "attempt": job.attempt,
        "max_attempts": job.max_attempts,
        "queued_at": job.queued_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "last_error_code": job.last_error_code,
    }


def _job_from(record: Mapping[str, object]) -> Job:
    try:
        kind = JobKind(cast(str, record["kind"]))
        status = JobStatus(cast(str, record["status"]))
    except ValueError:
        raise InvalidJob("stored job kind or status is not a known value") from None

    return Job(
        job_id=JobId(cast(UUID, record["job_id"])),
        kind=kind,
        target_id=cast(UUID, record["target_id"]),
        status=status,
        attempt=cast(int, record["attempt"]),
        max_attempts=cast(int, record["max_attempts"]),
        queued_at=cast(datetime, record["queued_at"]),
        started_at=cast("datetime | None", record["started_at"]),
        finished_at=cast("datetime | None", record["finished_at"]),
        last_error_code=cast("str | None", record["last_error_code"]),
    )


def open_job() -> sa.ColumnElement[bool]:
    """Rows of Jobs that can still do their work; see `Job.is_open`."""
    return sa.or_(
        jobs.c.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
        sa.and_(
            jobs.c.status == JobStatus.FAILED.value,
            jobs.c.attempt < jobs.c.max_attempts,
        ),
    )


async def load_open_job(
    connection: AsyncConnection,
    *,
    kind: JobKind,
    target_id: UUID,
    for_update: bool = False,
) -> Job | None:
    """The open Job of this kind for this target, if there is one."""
    statement = sa.select(jobs).where(
        jobs.c.kind == kind.value, jobs.c.target_id == target_id, open_job()
    )
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def enqueue_job(connection: AsyncConnection, job: Job) -> Enqueued:
    """Store new queued `job`, unless an open Job with the same intent exists.

    The intent is the Job's kind and target. When an open Job for it exists,
    that Job is returned and `job` is not stored, so asking twice for the
    same work, even at the same moment, leaves one Job to do it. An open Job
    that failed with attempts left is queued again for its next attempt,
    because the new request still wants the work done.
    """
    if job.status is not JobStatus.QUEUED or job.attempt != 0:
        raise InvalidJob("only a new queued job can be enqueued")
    for _ in range(MAX_ENQUEUE_ROUNDS):
        inserted = await connection.scalar(
            insert(jobs)
            .values(_job_row(job))
            .on_conflict_do_nothing(
                index_elements=[jobs.c.kind, jobs.c.target_id],
                index_where=sa.text(OPEN_JOB_PREDICATE),
            )
            .returning(jobs.c.job_id)
        )
        if inserted is not None:
            return Enqueued(job=job, created=True)
        existing = await load_open_job(
            connection, kind=job.kind, target_id=job.target_id
        )
        if existing is not None and existing.status is JobStatus.FAILED:
            existing = await _queue_again(connection, job)
        if existing is not None:
            return Enqueued(job=existing, created=False)
    raise JobIntentContended(f"the open {job.kind} job kept changing")


async def load_job(
    connection: AsyncConnection,
    *,
    job_id: JobId,
    for_update: bool = False,
) -> Job | None:
    """Read a stored Job, if it exists, optionally locking its row."""
    statement = sa.select(jobs).where(jobs.c.job_id == job_id.value)
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def save_job(connection: AsyncConnection, *, job: Job) -> Job:
    """Store the status a Job has reached.

    A Job queued again for another attempt needs a new message, so saving a
    queued Job marks it unpublished.
    """
    values = _job_row(job)
    if job.status is JobStatus.QUEUED:
        values["published_at"] = None
    statement = sa.update(jobs).where(jobs.c.job_id == job.job_id.value).values(values)
    await connection.execute(statement)
    return job


async def _queue_again(connection: AsyncConnection, job: Job) -> Job | None:
    """Queue the failed open Job for `job`'s intent again, under its row lock."""
    existing = await load_open_job(
        connection, kind=job.kind, target_id=job.target_id, for_update=True
    )
    if existing is None or existing.status is not JobStatus.FAILED:
        # Another request queued it again first, or it is gone: use what is.
        return existing
    return await save_job(connection, job=existing.retry(queued_at=job.queued_at))


def _unpublished() -> sa.ColumnElement[bool]:
    return sa.and_(
        jobs.c.status == JobStatus.QUEUED.value, jobs.c.published_at.is_(None)
    )


async def load_unpublished_job_ids(
    connection: AsyncConnection,
    *,
    kinds: Collection[JobKind],
    limit: int,
) -> tuple[JobId, ...]:
    """IDs of queued Jobs of these kinds that no message names, oldest first."""
    statement = (
        sa.select(jobs.c.job_id)
        .where(_unpublished(), jobs.c.kind.in_([kind.value for kind in kinds]))
        .order_by(jobs.c.queued_at, jobs.c.job_id)
        .limit(limit)
    )
    rows = await connection.scalars(statement)
    return tuple(JobId(job_id) for job_id in rows)


async def lock_unpublished_job(
    connection: AsyncConnection, *, job_id: JobId
) -> Job | None:
    """Lock a queued, unpublished Job, or None when it is not one.

    A row another transaction has locked counts as not one, so two
    publishers never send the same Job at the same time.
    """
    statement = (
        sa.select(jobs)
        .where(jobs.c.job_id == job_id.value, _unpublished())
        .with_for_update(skip_locked=True)
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def mark_job_published(
    connection: AsyncConnection, *, job_id: JobId, published_at: datetime
) -> None:
    """Record that a message naming this queued Job was handed to the queue."""
    await connection.execute(
        sa.update(jobs)
        .where(jobs.c.job_id == job_id.value)
        .values(published_at=published_at)
    )
```

**Дія 2:** `load_unpurged_documents` уже записував ту саму умову вручну. Використай спільну, щоб вони ніколи не розійшлися. Спершу імпорти:

У `src/knowledge_service/persistence/documents.py` знайди:

```python
from knowledge_service.jobs import JobKind, JobStatus
from knowledge_service.language import Language

```

і заміни на:

```python
from knowledge_service.jobs import JobKind
from knowledge_service.language import Language
from knowledge_service.persistence.jobs import open_job

```

Потім саму умову:

У `src/knowledge_service/persistence/documents.py` знайди:

```python
        jobs.c.target_id == documents.c.document_id,
        sa.or_(
            jobs.c.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
            sa.and_(
                jobs.c.status == JobStatus.FAILED.value,
                jobs.c.attempt < jobs.c.max_attempts,
            ),
        ),
    )
```

і заміни на:

```python
        jobs.c.target_id == documents.c.document_id,
        open_job(),
    )
```

**Перевірка:** `uv run pyright src/knowledge_service/persistence` показує 0 помилок. Ті, хто викликає ці функції, змінюються в наступних двох кроках.

### Крок 5 — Крок з кодом: захопити очищення і зберегти результат в одній транзакції

Цей крок виправляє очищення так, щоб невдалу спробу ніколи не виконали двічі.

**Простими словами:** очищення бере свій Job, пробує видалити фрагменти і зберігає результат, і все це до того, як відпустить Job. Якщо видалення впало, скасовується лише видалення, а Job лишається взятим, доки збій не записано. Друге повідомлення, яке чекає на той самий Job, тоді бачить збережений результат, а не Job, що виглядає незайманим. Позначення документа видаленим тепер також перевикористовує його відкрите очищення замість зберігати ще одне.

| Частина | Що змінюється |
|---|---|
| `purge_document_chunks` | одна транзакція: захопити, видалити всередині точки збереження, зберегти `succeeded` або `failed` |
| `_claim` | кидає `JobAlreadyClaimed` зі збереженим Job, коли той не в черзі |
| `_queue_purge` | повертає відкрите очищення з `enqueue_job`, нове чи наявне |
| `Tombstone`, `requeue_unfinished_purges` | docstring-и кажуть, що очищення може бути перевикористане |

`connection.begin_nested()` відкриває точку збереження. Проєкт уже використовує її в `persistence/conversations.py`.


**Дія 1:** імпортуй нову помилку.

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus

```

і заміни на:

```python
from knowledge_service.jobs import (
    InvalidJob,
    Job,
    JobAlreadyClaimed,
    JobKind,
    JobStatus,
)

```

**Дія 2:** онови два docstring-и. Спершу `Tombstone`:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
    """One Document made unavailable, and the Job that will purge its Chunks."""
```

і заміни на:

```python
    """One Document made unavailable, and the open Job that will purge its Chunks.

    When a purge of the Document was already open, that Job is reused.
    """
```

Потім `requeue_unfinished_purges`:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
    """Queue a new purge for each tombstoned Document no purge can still clean."""
```

і заміни на:

```python
    """Queue a purge for each tombstoned Document no open purge can clean.

    Two calls at the same moment leave one open purge per Document; both
    return its ID.
    """
```

**Дія 3:** заміни тіло `purge_document_chunks`, від кінця його docstring до кінця функції:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
    `Job.retry` can queue it again.
    """
    try:
        async with runtime.transaction() as connection:
            job = await _claim(connection, job_id=job_id, now=now)
            # Lock the row, so a restore cannot interleave with the delete.
            document = await load_document_by_id(
                connection,
                document_id=DocumentId(job.target_id),
                for_update=True,
            )
            if (
                document is not None
                and document.availability is DocumentAvailability.TOMBSTONED
            ):
                await delete_chunks(connection, document_id=document.document_id)
            return await save_job(connection, job=job.succeed(finished_at=now))
    except InvalidJob:
        raise
    except Exception:
        # The failed transaction rolled back; record the failure separately,
        # unless another worker has finished the Job in the meantime.
        async with runtime.transaction() as connection:
            stored = await load_job(connection, job_id=job_id, for_update=True)
            if stored is None or stored.status is not JobStatus.QUEUED:
                raise
            failed = stored.start(started_at=now).fail(
                error_code=PURGE_FAILED, finished_at=now
            )
            return await save_job(connection, job=failed)
```

і заміни на:

```python
    `Job.retry` can queue it again. Either way the outcome commits with the
    claim, so another delivery waiting on the claim sees it, never `queued`.
    """
    async with runtime.transaction() as connection:
        job = await _claim(connection, job_id=job_id, now=now)
        try:
            # A savepoint: a failed delete is undone, while the claim keeps
            # its row lock until the failure below is stored.
            async with connection.begin_nested():
                # Lock the row, so a restore cannot interleave with the delete.
                document = await load_document_by_id(
                    connection,
                    document_id=DocumentId(job.target_id),
                    for_update=True,
                )
                if (
                    document is not None
                    and document.availability is DocumentAvailability.TOMBSTONED
                ):
                    await delete_chunks(connection, document_id=document.document_id)
        except Exception:
            failed = job.fail(error_code=PURGE_FAILED, finished_at=now)
            return await save_job(connection, job=failed)
        return await save_job(connection, job=job.succeed(finished_at=now))
```

**Дія 4:** нехай `_queue_purge` повертає відкрите очищення. Спершу його docstring:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
    connection: AsyncConnection, *, document_id: DocumentId, now: datetime
) -> Job:
    job = Job.request(
```

і заміни на:

```python
    connection: AsyncConnection, *, document_id: DocumentId, now: datetime
) -> Job:
    """The open purge of this Document: a new one, or the one already open."""
    job = Job.request(
```

Потім його останні рядки:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
        queued_at=now,
    )
    await enqueue_job(connection, job)
    return job
```

і заміни на:

```python
        queued_at=now,
    )
    return (await enqueue_job(connection, job)).job
```

**Дія 5:** у `_claim` кидай нову помилку:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
        raise InvalidJob(f"only a queued job can run, not a {job.status} one")
```

і заміни на:

```python
        raise JobAlreadyClaimed(job)
```

**Перевірка:** `uv run pytest tests/test_deletions_integration.py -q -m integration` показує `9 passed`.

### Крок 6 — Крок з кодом: повідомити відкритий Job тому, хто подає роботу, а збережений результат — дублям

Цей крок передає нові відповіді двом місцям, які додав M08-T02.

**Простими словами:** подання роботи тепер каже, чи Job уже існував, і все одно надсилає про нього повідомлення, якщо ще не надсилало. На боці воркера друге повідомлення про Job більше не отримує «нічого»; воно отримує Job таким, яким його залишило перше. Тож кожне повідомлення про один Job повідомляє той самий статус, спробу і час завершення.

| Частина | Що змінюється |
|---|---|
| `Submission.duplicate` | true, коли відкритий Job з тим самим наміром уже існував; за замовчуванням false, тож тести M08-T02 і далі збігаються |
| `submit_job` | надсилає ID відкритого Job, хоч який це Job |
| `JobOutcome` | Job таким, яким він збережений після повідомлення, і `duplicate` |
| `run_stored_job` | повертає `JobOutcome`; Job не в черзі або `JobAlreadyClaimed` від обробника — це дубль |


**Дія 1:** у відправнику спершу тип результату:

У `src/knowledge_service/job_dispatch.py` знайди:

```python
class Submission:
    """A stored Job, and whether its message was sent yet."""

    job_id: JobId
    published: bool

```

і заміни на:

```python
class Submission:
    """The open Job for the submitted intent, and what this call did.

    `duplicate` is True when an open Job with the same kind and target
    already existed: that Job is returned and nothing new is stored.
    `published` is True only when this call sent its message.
    """

    job_id: JobId
    published: bool
    duplicate: bool = False

```

Потім docstring `submit_job`:

У `src/knowledge_service/job_dispatch.py` знайди:

```python
    An unpublished Job is sent by a later `publish_pending_jobs`.
    """
```

і заміни на:

```python
    An unpublished Job is sent by a later `publish_pending_jobs`. When an
    open Job with the same kind and target exists, it is returned instead,
    and is published only if no message names it yet.
    """
```

Потім його тіло, яке тепер надсилає той Job, що відкритий:

У `src/knowledge_service/job_dispatch.py` знайди:

```python
    async with runtime.transaction() as connection:
        await enqueue_job(connection, job)
    try:
        outcome = await publish_job(
            runtime, job_id=job.job_id, publisher=publisher, now=now
        )
```

і заміни на:

```python
    async with runtime.transaction() as connection:
        enqueued = await enqueue_job(connection, job)
    job_id = enqueued.job.job_id
    duplicate = not enqueued.created
    try:
        outcome = await publish_job(
            runtime, job_id=job_id, publisher=publisher, now=now
        )
```

І два його return:

У `src/knowledge_service/job_dispatch.py` знайди:

```python
            job.job_id.value,
            type(error).__name__,
        )
        return Submission(job_id=job.job_id, published=False)
    return Submission(job_id=job.job_id, published=outcome is PublishOutcome.PUBLISHED)
```

і заміни на:

```python
            job_id.value,
            type(error).__name__,
        )
        return Submission(job_id=job_id, published=False, duplicate=duplicate)
    return Submission(
        job_id=job_id,
        published=outcome is PublishOutcome.PUBLISHED,
        duplicate=duplicate,
    )
```

**Дія 2:** у воркері спершу імпорт:

У `src/knowledge_service/worker/jobs.py` знайди:

```python
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus

```

і заміни на:

```python
from knowledge_service.jobs import Job, JobAlreadyClaimed, JobKind, JobStatus

```

Потім заміни `run_stored_job` цілком:

У `src/knowledge_service/worker/jobs.py` знайди:

```python
async def run_stored_job(
    runtime: DatabaseRuntime,
    job_id: JobId,
    *,
    now: datetime,
    handlers: Mapping[JobKind, JobHandler] = HANDLERS,
) -> Job | None:
    """Run a stored queued Job once; None when the message was a duplicate.

    A Job that is no longer queued was run, or is running, because of an
    earlier message for it, so this message changes nothing.
    """
    async with runtime.transaction() as connection:
        job = await load_job(connection, job_id=job_id)
    if job is None:
        raise InvalidJobMessage("the message names no stored job")
    if job.status is not JobStatus.QUEUED:
        logger.info("job %s is %s; duplicate message ignored", job_id.value, job.status)
        return None
    handler = handlers.get(job.kind)
    if handler is None:
        raise InvalidJobMessage(f"no handler runs {job.kind} jobs")
    try:
        ran = await handler(runtime, job_id=job_id, now=now)
    except InvalidJob:
        # Another message for the same Job claimed it first.
        async with runtime.transaction() as connection:
            current = await load_job(connection, job_id=job_id)
        if current is not None and current.status is not JobStatus.QUEUED:
            logger.info("job %s was claimed by another message", job_id.value)
            return None
        raise
    logger.info("job %s (%s) ran: %s", job_id.value, ran.kind, ran.status)
    return ran
```

і заміни на:

```python
@dataclass(frozen=True, slots=True)
class JobOutcome:
    """A Job as stored after a message for it, and whether it was a duplicate.

    For a duplicate, `job` is the outcome an earlier message produced (or is
    producing), so every message for one Job reports the same result.
    """

    job: Job
    duplicate: bool


async def run_stored_job(
    runtime: DatabaseRuntime,
    job_id: JobId,
    *,
    now: datetime,
    handlers: Mapping[JobKind, JobHandler] = HANDLERS,
) -> JobOutcome:
    """Run a stored queued Job once, or return what an earlier message did.

    A Job that is no longer queued was run, or is running, because of an
    earlier message for it, so this message changes nothing.
    """
    async with runtime.transaction() as connection:
        job = await load_job(connection, job_id=job_id)
    if job is None:
        raise InvalidJobMessage("the message names no stored job")
    if job.status is not JobStatus.QUEUED:
        return _duplicate(job)
    handler = handlers.get(job.kind)
    if handler is None:
        raise InvalidJobMessage(f"no handler runs {job.kind} jobs")
    try:
        ran = await handler(runtime, job_id=job_id, now=now)
    except JobAlreadyClaimed as claimed:
        # Another message claimed it first; the claim waited for its commit.
        return _duplicate(claimed.job)
    logger.info("job %s (%s) ran: %s", job_id.value, ran.kind, ran.status)
    return JobOutcome(job=ran, duplicate=False)


def _duplicate(job: Job) -> JobOutcome:
    logger.info("job %s is %s; duplicate message", job.job_id.value, job.status)
    return JobOutcome(job=job, duplicate=True)
```

**Перевірка:** `uv run pyright src` показує 0 помилок.

### Крок 7 — Крок з кодом: довести один Job на намір і один ефект на Job

Цей крок перевіряє нову поведінку на справжньому PostgreSQL, де блокування, унікальний індекс і міграція працюють по-справжньому.

**Простими словами:** тести просять ту саму роботу двічі й одночасно і перевіряють, що збережено один Job. Вони перевіряють, що інший тип чи ціль — це інша робота, що завершені Job не заважають новій роботі і що Job, який упав, знову стає в чергу. Вони двічі позначають документ видаленим, запускають три одночасні повідомлення для очищення, у якого падає видалення, і проганяють міграцію над дублями, які міг лишити старий код.

| Тест | Що доводить |
|---|---|
| `test_asking_twice_...`, `test_another_kind_or_target_is_other_work` | той самий намір дає той самий Job; усе інше — нова робота |
| `test_a_job_that_can_do_no_more_work_...`, `test_asking_again_after_a_failed_attempt_...` | завершений Job дозволяє нову роботу; той, що впав, знову стає в чергу зі своїм лічильником спроб |
| `test_two_requests_at_once_store_one_job` | дві одночасні транзакції зберігають один Job |
| `test_the_database_refuses_...`, `test_an_open_job_that_keeps_finishing_...`, `test_only_a_new_queued_job_...` | індекс зупиняє звичайну вставку; `JobIntentContended`; `InvalidJob` |
| `test_a_document_tombstoned_again_...` (два тести) | відкрите очищення перевикористовується, а після збою воно виконується й очищає |
| `test_two_requeues_at_once_...` | одночасні повторні постановки лишають одне відкрите очищення |
| `test_submitting_...`, `test_a_duplicate_submission_...` | `Submission.duplicate`; одне повідомлення; ненадісланий відкритий Job надсилається |
| `test_two_messages_at_once_for_a_failing_attempt_run_it_once` | один виклик видалення, один збережений збій, три однакові звіти |
| `test_the_migration_keeps_one_open_job_per_intent` | той, що виконується, важливіший за той, що в черзі, а той — за той, що впав; решта стає `superseded_duplicate` |

`test_two_requests_at_once_store_one_job` тримає кожну транзакцію відкритою якусь мить, тож вони справді перетинаються. `test_two_requeues_at_once_leave_one_open_purge` проходить незалежно від того, чи перетнулися два виклики, бо перевіряє об'єднання того, що вони повернули.


**Дія 1:** спільним фікстурам потрібен спосіб знову запропонувати тестовий документ. Розділи `tombstoned_with_purge` на дві частини. Спершу її початок:

У `tests/job_fixtures.py` знайди:

```python
async def tombstoned_with_purge(runtime: DatabaseRuntime) -> tuple[Document, JobId]:
    """A tombstoned Document that still has Chunks, and its stored purge Job."""
    normalized = normalize(
```

і заміни на:

```python
async def offer_retired(runtime: DatabaseRuntime, *, observed_at: datetime) -> Document:
    """Reconcile the one Document these tests use, as its Source offers it."""
    normalized = normalize(
```

Потім її кінець:

У `tests/job_fixtures.py` знайди:

```python
    result = await reconcile_document(
        runtime, normalized, readers=[Subject.public()], observed_at=OBSERVED_AT
    )
    [tombstone] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=OBSERVED_AT
    )
    return result.document, tombstone.purge_job_id
```

і заміни на:

```python
    result = await reconcile_document(
        runtime, normalized, readers=[Subject.public()], observed_at=observed_at
    )
    return result.document


async def tombstoned_with_purge(runtime: DatabaseRuntime) -> tuple[Document, JobId]:
    """A tombstoned Document that still has Chunks, and its stored purge Job."""
    document = await offer_retired(runtime, observed_at=OBSERVED_AT)
    [tombstone] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=OBSERVED_AT
    )
    return document, tombstone.purge_job_id
```

**Дія 2:** два тести M08-T02 очікували `None` для дубля. У `test_a_duplicate_message_runs_the_job_once`:

У `tests/test_job_dispatch_integration.py` знайди:

```python
    assert first is not None
    assert first.status is JobStatus.SUCCEEDED
    assert second is None
```

і заміни на:

```python
    assert not first.duplicate
    assert first.job.status is JobStatus.SUCCEEDED
    # The duplicate reports the outcome the first message produced.
    assert second.duplicate
    assert second.job == first.job
```

І в `test_two_messages_at_once_run_the_job_once`, який тепер ще й перевіряє, що обидва звіти — той самий збережений Job:

У `tests/test_job_dispatch_integration.py` знайди:

```python
    assert sum(result is not None for result in results) == 1
    assert (await stored_job(runtime, purge_id)).status is JobStatus.SUCCEEDED
```

і заміни на:

```python
    assert sorted(result.duplicate for result in results) == [False, True]
    stored = await stored_job(runtime, purge_id)
    assert stored.status is JobStatus.SUCCEEDED
    assert [result.job for result in results] == [stored, stored]
```

**Дія 3:** створи тести ідемпотентності.

Створи `tests/test_job_idempotency_integration.py`:

```python
"""One open Job per intent, against real PostgreSQL.

A Job's intent is its kind and target: "purge the Chunks of Document D".
Asking for the same intent again, even at the same moment, returns the Job
that is already open instead of storing a second one.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta

import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from database_fixtures import migration_config, migration_environment
from job_fixtures import (
    OBSERVED_AT,
    SOURCE_ID,
    chunk_count,
    insert_source,
    offer_retired,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, JobId
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import (
    PURGE_FAILED,
    purge_document_chunks,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.job_dispatch import PublishFailed, Submission, submit_job
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence import jobs as job_store
from knowledge_service.persistence.jobs import (
    Enqueued,
    JobIntentContended,
    enqueue_job,
    save_job,
)
from knowledge_service.persistence.schema import jobs
from knowledge_service.settings import Settings
from knowledge_service.worker.jobs import run_stored_job

pytestmark = pytest.mark.integration

NOW = OBSERVED_AT + timedelta(minutes=5)
LATER = OBSERVED_AT + timedelta(minutes=10)
PURGE = JobKind.PURGE_DOCUMENT_CHUNKS
TARGET = uuid.UUID("5f1d2c3b-4a59-4e6f-8a7b-9c0d1e2f3a4b")


@dataclass
class RecordingPublisher:
    """Records every message it is asked to send; can refuse them all."""

    refuse: bool = False
    kinds: frozenset[JobKind] = frozenset({PURGE})
    sent: list[JobId] = field(default_factory=lambda: list[JobId]())

    def publish(self, job: Job) -> None:
        if self.refuse:
            raise PublishFailed("the queue is down")
        self.sent.append(job.job_id)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    await insert_source(runtime)
    try:
        yield runtime
    finally:
        await runtime.dispose()


def new_job(kind: JobKind = PURGE, target_id: uuid.UUID = TARGET) -> Job:
    return Job.request(
        job_id=JobId.new(), kind=kind, target_id=target_id, queued_at=OBSERVED_AT
    )


async def enqueue(runtime: DatabaseRuntime, job: Job) -> Enqueued:
    async with runtime.transaction() as connection:
        return await enqueue_job(connection, job)


async def broken_delete(connection: AsyncConnection, *, document_id: DocumentId) -> int:
    raise ConnectionError("the database went away")


async def stored_job_ids(runtime: DatabaseRuntime) -> list[JobId]:
    async with runtime.transaction() as connection:
        rows = await connection.scalars(sa.select(jobs.c.job_id))
        return [JobId(job_id) for job_id in rows]


async def test_asking_twice_for_the_same_work_leaves_one_job(
    runtime: DatabaseRuntime,
) -> None:
    first = await enqueue(runtime, new_job())
    second = await enqueue(runtime, new_job())

    assert first.created
    assert not second.created
    assert second.job == first.job
    assert await stored_job_ids(runtime) == [first.job.job_id]


@pytest.mark.parametrize(
    ("kind", "target_id"),
    [
        (JobKind.PURGE_CONVERSATION, TARGET),
        (PURGE, uuid.UUID("0c9b8a7d-6e5f-4a3b-8c1d-2e3f4a5b6c7d")),
    ],
)
async def test_another_kind_or_target_is_other_work(
    runtime: DatabaseRuntime, kind: JobKind, target_id: uuid.UUID
) -> None:
    await enqueue(runtime, new_job())

    other = await enqueue(runtime, new_job(kind, target_id))

    assert other.created
    assert len(await stored_job_ids(runtime)) == 2


async def test_a_job_that_can_do_no_more_work_does_not_block_new_work(
    runtime: DatabaseRuntime,
) -> None:
    done = (await enqueue(runtime, new_job())).job
    async with runtime.transaction() as connection:
        await save_job(
            connection, job=done.start(started_at=NOW).succeed(finished_at=NOW)
        )

    again = await enqueue(runtime, new_job())

    assert again.created
    assert again.job.job_id != done.job_id


async def test_two_requests_at_once_store_one_job(runtime: DatabaseRuntime) -> None:
    async def request_and_hold() -> Enqueued:
        async with runtime.transaction() as connection:
            enqueued = await enqueue_job(connection, new_job())
            # Keep the transaction open, so the other request runs meanwhile.
            await asyncio.sleep(0.2)
            return enqueued

    results = await asyncio.gather(request_and_hold(), request_and_hold())

    assert sorted(result.created for result in results) == [False, True]
    assert results[0].job.job_id == results[1].job.job_id
    assert len(await stored_job_ids(runtime)) == 1


async def test_the_database_refuses_a_second_open_job_for_one_intent(
    runtime: DatabaseRuntime,
) -> None:
    await enqueue(runtime, new_job())
    second = new_job()

    with pytest.raises(IntegrityError, match="uq_jobs_open_intent"):
        async with runtime.transaction() as connection:
            # A plain insert, as code that skipped `enqueue_job` would do.
            await connection.execute(
                sa.insert(jobs).values(
                    job_id=second.job_id.value,
                    kind=second.kind.value,
                    target_id=second.target_id,
                    status=second.status.value,
                    attempt=0,
                    max_attempts=second.max_attempts,
                    queued_at=second.queued_at,
                )
            )


async def test_an_open_job_that_keeps_finishing_is_a_typed_failure(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await enqueue(runtime, new_job())

    async def always_finished(*_args: object, **_kwargs: object) -> None:
        return None

    # Each round finds the conflict but never the open Job behind it.
    monkeypatch.setattr(job_store, "load_open_job", always_finished)
    with pytest.raises(JobIntentContended):
        await enqueue(runtime, new_job())

    assert await stored_job_ids(runtime) == [first.job.job_id]


async def test_a_document_tombstoned_again_reuses_its_open_purge(
    runtime: DatabaseRuntime,
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    # The Source offers the Document again, then stops offering it.
    await offer_retired(runtime, observed_at=NOW)
    [again] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )

    assert again.purge_job_id == purge_id
    assert await stored_job_ids(runtime) == [purge_id]
    assert await chunk_count(runtime, document.document_id) > 0


async def test_two_requeues_at_once_leave_one_open_purge(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    # The purge ended without deleting anything, so the Chunks still need one.
    finished = (await stored_job(runtime, purge_id)).start(started_at=NOW)
    async with runtime.transaction() as connection:
        await save_job(connection, job=finished.succeed(finished_at=NOW))

    results = await asyncio.gather(
        requeue_unfinished_purges(runtime, source_id=SOURCE_ID, now=LATER),
        requeue_unfinished_purges(runtime, source_id=SOURCE_ID, now=LATER),
    )

    requeued = {job_id for result in results for job_id in result}
    assert len(requeued) == 1
    [requeued_id] = requeued
    assert (await stored_job(runtime, requeued_id)).status is JobStatus.QUEUED
    assert len(await stored_job_ids(runtime)) == 2


async def test_submitting_the_same_work_twice_returns_the_open_job(
    runtime: DatabaseRuntime,
) -> None:
    publisher = RecordingPublisher()
    first = await submit_job(runtime, new_job(), publisher=publisher, now=NOW)

    second = await submit_job(runtime, new_job(), publisher=publisher, now=LATER)

    assert first == Submission(job_id=first.job_id, published=True)
    assert second == Submission(job_id=first.job_id, published=False, duplicate=True)
    assert publisher.sent == [first.job_id]


async def test_a_duplicate_submission_sends_an_open_job_no_message_names(
    runtime: DatabaseRuntime,
) -> None:
    first = await submit_job(
        runtime, new_job(), publisher=RecordingPublisher(refuse=True), now=NOW
    )
    publisher = RecordingPublisher()

    second = await submit_job(runtime, new_job(), publisher=publisher, now=LATER)

    assert not first.published
    assert second == Submission(job_id=first.job_id, published=True, duplicate=True)
    assert publisher.sent == [first.job_id]


async def test_only_a_new_queued_job_can_be_enqueued(runtime: DatabaseRuntime) -> None:
    finished = new_job().start(started_at=NOW).succeed(finished_at=NOW)

    with pytest.raises(InvalidJob, match="only a new queued job"):
        await enqueue(runtime, finished)

    assert await stored_job_ids(runtime) == []


async def test_asking_again_after_a_failed_attempt_queues_the_job_again(
    runtime: DatabaseRuntime,
) -> None:
    job = (await enqueue(runtime, new_job())).job
    async with runtime.transaction() as connection:
        await save_job(
            connection,
            job=job.start(started_at=NOW).fail(error_code="boom", finished_at=NOW),
        )

    again = await enqueue(runtime, new_job())

    assert not again.created
    assert (again.job.job_id, again.job.status, again.job.attempt) == (
        job.job_id,
        JobStatus.QUEUED,
        1,
    )
    assert await stored_job(runtime, job.job_id) == again.job


async def test_a_document_tombstoned_again_after_a_failed_purge_is_purged(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(runtime, job_id=purge_id, now=NOW)
    assert failed.status is JobStatus.FAILED
    await offer_retired(runtime, observed_at=NOW)

    [again] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )
    purged = await purge_document_chunks(runtime, job_id=again.purge_job_id, now=LATER)

    assert again.purge_job_id == purge_id
    assert (purged.status, purged.attempt) == (JobStatus.SUCCEEDED, 1)
    assert await chunk_count(runtime, document.document_id) == 0


async def test_two_messages_at_once_for_a_failing_attempt_run_it_once(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    calls: list[DocumentId] = []

    async def slow_broken_delete(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        calls.append(document_id)
        # Long enough for the other messages to queue up behind the claim.
        await asyncio.sleep(0.2)
        raise ConnectionError("the database went away")

    monkeypatch.setattr(deletions, "delete_chunks", slow_broken_delete)
    results = await asyncio.gather(
        *(run_stored_job(runtime, purge_id, now=NOW) for _ in range(3))
    )

    stored = await stored_job(runtime, purge_id)
    assert len(calls) == 1
    assert sorted(result.duplicate for result in results) == [False, True, True]
    assert [result.job for result in results] == [stored] * 3
    assert (stored.status, stored.attempt, stored.last_error_code) == (
        JobStatus.FAILED,
        1,
        PURGE_FAILED,
    )


def test_the_migration_keeps_one_open_job_per_intent(migrated_database: str) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    with migration_environment(url):
        command.downgrade(config, "c2d3e4f5a6b7")
    insert_job = sa.text(
        "insert into jobs (job_id, kind, target_id, status, attempt, max_attempts,"
        " queued_at, started_at, finished_at, last_error_code) values (:job_id,"
        " 'purge_document_chunks', :target_id, :status, :attempt, 3, :queued_at,"
        " :started_at, :finished_at, :error)"
    )
    other_target = uuid.uuid4()
    failed_target = uuid.uuid4()
    rows = {
        "newer_queued": ("queued", LATER, TARGET),
        "running": ("running", LATER, TARGET),
        "older_queued": ("queued", NOW, TARGET),
        "alone": ("queued", NOW, other_target),
        "older_failed": ("failed", NOW, failed_target),
        "newer_queued_after_failure": ("queued", LATER, failed_target),
    }
    ids = {name: uuid.uuid4() for name in rows}
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            for name, (status, queued_at, target_id) in rows.items():
                failed = status == "failed"
                connection.execute(
                    insert_job,
                    {
                        "job_id": ids[name],
                        "target_id": target_id,
                        "status": status,
                        "attempt": 1 if failed else 0,
                        "queued_at": queued_at,
                        "started_at": None if status == "queued" else queued_at,
                        "finished_at": queued_at if failed else None,
                        "error": "chunk_purge_failed" if failed else None,
                    },
                )

        with migration_environment(url):
            command.upgrade(config, "head")

        with engine.connect() as connection:
            stored = {
                row.job_id: (row.status, row.attempt, row.last_error_code)
                for row in connection.execute(
                    sa.select(
                        jobs.c.job_id,
                        jobs.c.status,
                        jobs.c.attempt,
                        jobs.c.last_error_code,
                    )
                )
            }
    finally:
        engine.dispose()

    # A running Job is kept first; the queued duplicates are closed.
    assert stored[ids["running"]] == ("running", 0, None)
    assert stored[ids["older_queued"]] == ("failed", 3, "superseded_duplicate")
    assert stored[ids["newer_queued"]] == ("failed", 3, "superseded_duplicate")
    assert stored[ids["alone"]] == ("queued", 0, None)
    # A queued Job will run, so it is kept over an older failed one.
    assert stored[ids["newer_queued_after_failure"]] == ("queued", 0, None)
    assert stored[ids["older_failed"]] == ("failed", 3, "superseded_duplicate")
```

**Перевірка:** `uv run pytest tests/test_job_idempotency_integration.py tests/test_job_dispatch_integration.py -q -m integration` показує `37 passed`.

### Крок 8 — У цьому кроці коду немає: зламай навмисно

Цей крок прибирає рядки, які дають базі розв'язувати дублі, і показує, які тести це помічають.

**Дія 1:** в `enqueue_job` у `src/knowledge_service/persistence/jobs.py` видали ці рядки:

```python
            .on_conflict_do_nothing(
                index_elements=[jobs.c.kind, jobs.c.target_id],
                index_where=sa.text(OPEN_JOB_PREDICATE),
            )
```

**Дія 2:** запусти:

```bash
uv run pytest tests/test_jobs.py tests/test_job_idempotency_integration.py tests/test_job_dispatch_integration.py tests/test_deletions_integration.py -q -m "integration or not integration"
```

Ти побачиш `8 failed, 44 passed`. Усі падіння в `tests/test_job_idempotency_integration.py`: без цієї частини другий запит для відкритого наміру натрапляє на унікальний індекс і кидає `IntegrityError`, замість отримати відкритий Job.

**Дія 3:** поверни рядки і запусти команду знову: `52 passed`.

**Перевірка:** порівняй кількість зі своїм прогнозом із кроку 1 і запиши її у файл доказів.

### Крок 9 — У цьому кроці коду немає: онови два документи

Цей крок записує нове правило там, де його шукатимуть читачі: в архітектурі й моделі загроз.


**Дія 1:** в архітектурі додай абзац після абзацу про `job_dispatch`.

У `docs/architecture.md` знайди:

```markdown
Only kinds a worker runs are submitted or sent. A Job can be named by two
messages; the worker runs it only while it is still queued.
```

і заміни на:

```markdown
Only kinds a worker runs are submitted or sent. A Job can be named by two
messages; the worker runs it only while it is still queued.

A Job's intent is its kind and target, such as "purge the Chunks of Document
D". `enqueue_job` keeps at most one open Job per intent: queued, running, or
failed with attempts left. The partial unique index `uq_jobs_open_intent`
enforces it, so asking for the same work again, even at the same moment,
returns the open Job (`Enqueued.created` is false, `Submission.duplicate` is
true) instead of storing a second one, and an open Job that failed is queued
again for its next attempt. A handler claims a queued Job under a row lock and
stores its outcome, success or failure, in the same transaction; a claim that
finds the Job no longer queued raises `JobAlreadyClaimed` with the stored Job,
and `run_stored_job` returns that Job as the outcome of every duplicate
message, so every delivery reports the same result.
```

**Дія 2:** розшир рядок `jobs` у таблиці інваріантів.

У `docs/architecture.md` знайди:

```markdown
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code. |
```

і заміни на:

```markdown
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code; at most one open Job (queued, running, or failed with attempts left) per kind and target. |
```

**Дія 3:** у діаграмі послідовності синхронізації зміни рядок про позначення видаленим.

У `docs/architecture.md` знайди:

```markdown
    Del->>DB: one transaction: tombstone, audit event, queue purge Job
```

і заміни на:

```markdown
    Del->>DB: one transaction: tombstone, audit event, queue purge Job or reuse the open one
```

**Дія 4:** у моделі загроз заміни рядок `A replayed job writes twice`.

У `docs/threat-model.md` знайди:

```markdown
| A replayed job writes twice | Jobs are stored with typed errors and bounded retries | Job idempotency (M08-T03) |
```

і заміни на:

```markdown
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A failed Job with attempts left runs again only when the same work is requested again, until retries are owned (M08-T04); a message for an earlier attempt can start the next one (M08-T04) |
```

**Перевірка:** `make docs-check` завершується з кодом 0.

### Крок 10 — У цьому кроці коду немає: перевірки, докази і прогрес

Цей крок запускає всі перевірки і завершує записи.

**Дія 1:** запусти всі перевірки, одну за одною:

```bash
make format-check
make lint
make typecheck
make test-unit
make test-integration
make docs-check
```

**Дія 2:** заверши файл доказів: результати перевірок і кількість із кроку 8 у «Verification», знахідки рев'ю й відомі обмеження в «Review», дизайн у «Design» і «Decision», а також свої відповіді на питання для рефлексії нижче. Потім познач завдання виконаним у `docs/curriculum/PROGRESS.md` і зроби коміт з `m08-t03: enforce job idempotency`.

**Перевірка:** усі перевірки проходять, а `git status` чистий після коміту.

## Питання для рефлексії

- Документ позначили видаленим, його очищення один раз упало, джерело знову запропонувало документ, а потім перестало. Який Job очищення отримає друге позначення видаленим, і чому це краще, ніж зберегти нове очищення?
- Уяви, що `enqueue_job` спершу шукав би відкритий Job і вставляв новий, лише якщо нічого не знайшов. Опиши одну послідовність двох запитів, яка збереже два Job, і скажи, що саме в базі тепер цьому заважає.
- У M08-T02 очищення записувало збій у другій транзакції. Пройди кроками, як три повідомлення для того самого очищення, що падає, могли тоді видалити двічі, і скажи, який рядок нового коду закриває цю діру.
- Чому повторне повідомлення тепер отримує збережений Job, а не `None`? Кому корисно, що кожна доставка повідомляє той самий результат?
- Намір — це лише тип плюс ціль. Придумай майбутній Job, де два запити з однаковим типом і ціллю мали б бути різною роботою. Що ти побачив би в тестах чи в роботі системи, що скаже тобі: ключ занадто грубий?
- Яке спостереження довело б, що цей дизайн хибний? Наприклад, що ти мав би побачити в таблиці `jobs`, щоб засумніватися, що індекс тримає правило?

## Чекліст завершення

- [ ] Файл доказів містить інваріант, власників і твої прогнози, записані до змін.
- [ ] `Job.is_open` і `JobAlreadyClaimed` існують; `tests/test_jobs.py` має 6 тестів і проходить.
- [ ] Міграція `e5f6a7b8c9d0` закриває дубльовані відкриті Job і додає `uq_jobs_open_intent`; тести індексів і міграцій проходять.
- [ ] `enqueue_job` повертає `Enqueued`, зберігає щонайбільше один відкритий Job на намір, знову ставить у чергу відкритий Job, що впав, і відхиляє все, крім нового Job у черзі.
- [ ] `purge_document_chunks` захоплює Job, видаляє в точці збереження і зберігає результат в одній транзакції; `_claim` кидає `JobAlreadyClaimed`.
- [ ] `submit_job` повідомляє `duplicate`; `run_stored_job` повертає `JobOutcome` зі збереженим Job для дублів.
- [ ] `tests/test_job_idempotency_integration.py` (16), `tests/test_job_dispatch_integration.py` (21) і `tests/test_deletions_integration.py` (9) проходять.
- [ ] Ти зламав код навмисно, побачив 8 падінь і повернув усе назад.
- [ ] `docs/architecture.md` і `docs/threat-model.md` описують один відкритий Job на намір і його обмеження.
- [ ] Усі перевірки проходять: формат, lint, типи, unit, integration, документація.
- [ ] Докази, статус завдання, рядок у PROGRESS і коміт зроблено.

Коли напишеш файли, поділись ними або виводом помилок, і я перевірю їх за цим чеклістом.
