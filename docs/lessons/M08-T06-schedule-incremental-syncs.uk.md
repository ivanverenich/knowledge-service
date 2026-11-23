English version: [M08-T06-schedule-incremental-syncs.md](M08-T06-schedule-incremental-syncs.md)

# M08-T06 — Синхронізації за розкладом

Завдання: [M08-T06 — Schedule incremental Synchronization Runs](../curriculum/milestones/M08-jobs-and-confluence/M08-T06-schedule-incremental-syncs.md)

## Що ти зробиш

Цей урок робить так, що синхронізація запускається сама. Кожен прохід `make worker-relay` ставить у чергу один Job синхронізації для кожного увімкненого джерела, чий останній запуск був щонайменше годину тому, і ніколи не ставить зайвий.

Конкретний приклад: інтервал за замовчуванням — одна година, relay запускається кожні десять хвилин.

| Час | Handbook (увімкнене) | Archive (вимкнене) |
|---|---|---|
| 09:00 | ще жодного разу не синхронізувалось: Job поставлено в чергу | пропущено |
| 09:10 | його Job досі відкритий: нічого | пропущено |
| 09:50 | його запуск завершився о 09:50 | пропущено |
| 10:40 | 50 хвилин від завершення: нічого | пропущено |
| 10:50 | година: Job поставлено в чергу | пропущено |

| Що просить завдання | Де це зроблено |
|---|---|
| Ставити в чергу увімкнені джерела за політикою | Кроки 2 і 3: `load_due_source_ids` і `schedule_due_synchronizations` з налаштовуваним інтервалом |
| Не ставити зайвих розкладів | «Перед початком», блок 2: відкриті Job пропускаються, пропущений розклад ставиться один раз, два проходи зберігають один Job |
| Нести лише стабільні ідентифікатори, а не секретну конфігурацію | Блок 3: Job містить лише ID джерела |
| Тести з керованим годинником: настав час, вимкнене, вже йде, пропущене | Крок 5: кожен тест передає власний `now` |
| Явна поведінка при повторі чи скасуванні | Блоки 2 і 5 |
| Оновити лише ту документацію, якої це стосується | Крок 7: архітектура, експлуатація, модель загроз |

У цьому уроці є код: кроки 2–5 пишуть код, міграцію і тести. Кроки 1, 6, 7 і 8 коду не потребують, хоча на кроці 7 ти редагуєш три документи.

Терміни цього уроку:

- **Настав час (due):** джерело, чию наступну синхронізацію треба поставити в чергу зараз.
- **Інтервал (interval):** через скільки після останнього запуску джерелу знову настає час; за замовчуванням година.
- **Остання активність (last activity):** коли завершився найновіший Job синхронізації джерела, або коли його поставили в чергу, якщо він не завершився.
- **Зайвий розклад (redundant schedule):** Job, що робив би ту саму роботу, що й наявний або щойно виконаний.

## Перед початком

Цей розділ пояснює кожне рішення, на якому стоїть код, крок за кроком. Кожен блок закінчується тим, що змінює цей урок.

### 1. Коли джерелу настає час

**Ситуація.** Останній запуск Handbook поставили в чергу о 09:00, і він завершився о 09:50. Інтервал — одна година.

| Прохід relay о | Остання активність | Настав час? |
|---|---|---|
| 10:40 | 09:50 | ні: 50 хвилин |
| 10:50 | 09:50 | так: година |

**Прихований факт.** Рахувати від *завершення* запуску, а не від постановки в чергу, важливо для довгих запусків. Якби запуск ішов дві з половиною години, то від часу постановки джерелу настав би час одразу, щойно запуск закінчився, і воно синхронізувалось би без перерви. Від часу завершення воно отримує повний інтервал відпочинку.

**Що змінюється.** `load_due_source_ids` повертає кожне увімкнене джерело без відкритого Job синхронізації, чия остання активність була щонайменше інтервал тому або в якого Job не було зовсім.

### 2. Зайві розклади не ставляться

| Випадок | Що зробив би наївний планувальник | Що робить цей |
|---|---|---|
| Job джерела досі в черзі або виконується | поставив би другий | пропускає: є відкритий Job |
| джерело синхронізувалось 20 хвилин тому | поставив би знову | пропускає: час ще не настав |
| relay не працював десять годин | поставив би десять Job, по одному на кожну пропущену годину | ставить **один**: остання активність давня, а щойно Job є, він відкритий |
| два проходи relay в ту саму мить | кожен поставив би свій | один Job: індекс відкритих намірів з M08-T03 |

**Прихований факт.** «Настав час» вирішується за самою таблицею Job, а не за списком запланованих моментів. Немає черги пропущених моментів, яку треба відтворювати, тож пропущений розклад не накопичується.

**Повтор.** Якщо двічі запустити прохід з тим самим `now`, другий раз нічого не поставить: у кожного джерела, яке поставив перший, тепер є відкритий Job.

### 3. Передається лише ID

**Ситуація.** У рядку Handbook лежать його адреса, `https://wiki.example.com/spaces/HB`, і посилання на облікові дані, `vault:confluence/handbook`.

| Поле Job у черзі | Значення |
|---|---|
| `kind` | `synchronize_source` |
| `target_id` | ID джерела Handbook |
| будь-що інше про джерело | нічого |

**Прихований факт.** Повідомлення про Job іде через Redis, який захищений не так, як PostgreSQL, а Job може пролежати в черзі якийсь час. Якби він ніс адресу чи посилання на облікові дані, той, хто читає Redis, міг би їх побачити, а старий Job виконався б зі старою конфігурацією. Коли передається лише ID, обробник читає поточну конфігурацію з PostgreSQL у момент виконання.

**Що змінюється.** Job зберігає ID джерела і нічого більше, а тест перевіряє, що в збереженому рядку немає ні адреси, ні посилання на облікові дані. Обробник, що їх читає, з'явиться в M08-T10; доти ці Job чекають у черзі, бо такий тип ще не виконує жоден воркер.

### 4. Знахідки рев'ю

Адверсаріальне рев'ю атакувало першу версію цього коду на справжньому PostgreSQL. Кожну знахідку нижче виправлено, і є тест, що без виправлення падає.

**Голодування.** Коли 150 джерелам настав час, а за прохід можна поставити 100, перша версія сортувала за ID джерела:

| Прохід | Поставлено | Ніколи не дійшло |
|---|---|---|
| 1 | ID 1–100 | 101–150 |
| 2, після завершення тих запусків | знову ID 1–100 | 101–150 |

Тепер першими йдуть ті, хто чекає найдовше (спершу ті, що ще не синхронізувались, далі з найдавнішою активністю), тож хвіст обробляється наступним проходом.

**Годинник, що забіг уперед.** Воркер, чий годинник поспішав на рік, записав час завершення на рік уперед. Джерело рік виглядало «щойно синхронізованим». Тепер час завершення, пізніший за `now`, ігнорується, і Job лічиться від постановки в чергу; Job, поставлений у майбутньому, не враховується.

**Вимкнено або видалено посеред проходу.**

| Момент | Прохід relay | Інша сесія |
|---|---|---|
| 1 | читає: Handbook настав час | |
| 2 | | вимикає Handbook, комітить |
| 3 | ставить Job для Handbook (перша версія) | |

Тепер прохід перед постановкою ще раз перевіряє джерела під спільним блокуванням (`SELECT ... WHERE enabled FOR SHARE`). Зміна, закомічена до цієї перевірки, враховується; спроба після неї чекає, доки прохід не закомітиться.

**Запит, що ріс з історією.** Перша версія сканувала всю таблицю `jobs` для кожного джерела, а планувальник запитів повторював підзапит «остання активність» до трьох разів на джерело. Тепер частковий індекс знаходить Job синхронізації джерела, а кандидати — це матеріалізований CTE, тож остання активність кожного джерела рахується один раз:

```sql
WITH candidates AS MATERIALIZED (
  SELECT source_id, (SELECT max(...) FROM jobs WHERE ...) AS last_activity
  FROM sources WHERE enabled AND NOT EXISTS (an open synchronization Job)
)
SELECT source_id FROM candidates
WHERE last_activity IS NULL OR last_activity <= :now - :interval
ORDER BY last_activity NULLS FIRST, source_id
LIMIT :limit;
```

**Збій планування зупиняв relay.** Планування йде перед надсиланням у тому самому проході relay, тож помилка планування затримувала повідомлення всіх інших Job. Тепер `relay_once` пише її в лог за типом, повідомляє про неї і все одно надсилає; команда завершується з кодом 1.

### 5. Відомі обмеження

- Джерело, чий Job синхронізації завис відкритим, не плануватиметься знову, доки M08-T08 не знайде цей Job. Доки M08-T10 не виконуватиме ці Job, кожне джерело стає в чергу один раз і так лишається.
- Вимкнення джерела не скасовує вже поставленого Job, а `jobs.target_id` не має зовнішнього ключа: обробник у M08-T10 має вважати відсутнє чи вимкнене джерело остаточним результатом.
- Кожен прохід рахує останню активність кожного увімкненого джерела без відкритого Job, по всіх його Job синхронізації; це росте з історією, доки старі Job не почнуть прибирати.
- Збій при поверненні в чергу повторів, яким настав час, досі зупиняє прохід relay до надсилання (з M08-T04).

## Покрокова інструкція

Перед початком запусти PostgreSQL. Усі команди виконуються з кореня репозиторію. Для integration-перевірок задай URL тестової бази у своєму терміналі:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

Запускай проти цієї бази лише одну integration-команду за раз.

### Крок 1 — У цьому кроці коду немає: запиши інваріант і свої прогнози

Перш ніж щось змінювати, запиши, що має бути правдою наприкінці і чого ти очікуєш. Прогнози перевіриш на кроках 6 і 8.

Створи `docs/evidence/M08-T06-schedule-incremental-syncs.md`:

```markdown
# M08-T06 — Schedule incremental Synchronization Runs evidence

## Invariant

Each relay pass queues at most one synchronization Job per enabled Source,
and only for a Source with no open synchronization Job whose last one
finished, or was queued, at least one interval before the pass's clock, or
that never had one. A missed schedule queues one Job, not one per missed
interval; two passes at once store one Job per Source. A queued Job names its
Source by ID and carries no configuration or credentials reference.

## Owners

- When a Source is due: `load_due_source_ids`, from the Jobs table.
- One Job per Source: the open-intent index (M08-T03).
- Running the Job, reading configuration and checkpoint: M08-T10.
- Authorization: none; nothing outside the service schedules.

## Expected failing check

Before the change,
`uv run pytest tests/test_scheduling_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.

## Predictions

- What a pass queues for a Source whose last run finished 59 minutes ago, and at one hour:
- How many Jobs a pass queues for a Source after the relay was down for ten hours:
- What happens to a Source disabled by another session just after the pass read it:
- How many tests fail if the "at least one interval ago" condition is removed:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після двокрапки в кожному прогнозі стоїть твоя відповідь.

### Крок 2 — Крок з кодом: знайти джерела, яким настав час

Цей крок додає запит, що вирішує, яким джерелам настав час, і індекс, що тримає його швидким.

**Простими словами:** запит дивиться на кожне увімкнене джерело, пропускає ті, де синхронізація досі відкрита, і з'ясовує, коли кожне запускалося востаннє. Джерелу, яке ніколи не запускалося або запускалося щонайменше інтервал тому, настав час. Першими йдуть ті, хто чекає найдовше. Другий запит потім блокує ці джерела і лишає тільки ті, що досі увімкнені.

| Частина | Що робить |
|---|---|
| `ix_jobs_synchronization_target` | частковий індекс за `target_id` лише над Job синхронізації |
| Міграція `b8c9d0e1f2a3` | додає його |
| `load_due_source_ids` | запит із блоку 4: майбутній час ігнорується, першими ті, хто чекає найдовше |
| `lock_enabled_sources` | `SELECT ... WHERE enabled FOR SHARE` над ID, яким настав час |


**Дія 1:** створи міграцію.

Створи `migrations/versions/b8c9d0e1f2a3_job_synchronization_target_index.py`:

```python
"""job synchronization target index

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-11-23 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"


def upgrade() -> None:
    op.create_index(
        "ix_jobs_synchronization_target",
        "jobs",
        ["target_id"],
        postgresql_where=sa.text("kind = 'synchronize_source'"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_synchronization_target", table_name="jobs")
```

**Дія 2:** оголоси індекс у таблиці `jobs`, перед `ix_jobs_due_retry`:

У `src/knowledge_service/persistence/schema.py` знайди:

```python
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    ),
    sa.Index(
        "ix_jobs_due_retry",
        "retry_at",
```

і заміни на:

```python
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    ),
    # Finds each Source's synchronization Jobs for the schedule.
    sa.Index(
        "ix_jobs_synchronization_target",
        "target_id",
        postgresql_where=sa.text("kind = 'synchronize_source'"),
    ),
    sa.Index(
        "ix_jobs_due_retry",
        "retry_at",
```

**Дія 3:** дозволь його в списку індексів:

У `tests/test_indexes.py` знайди:

```python
    "uq_embedding_spaces_current",
    "uq_jobs_open_intent",
    "ix_jobs_due_retry",
    "uq_synchronization_runs_running_source",
}
```

і заміни на:

```python
    "uq_embedding_spaces_current",
    "uq_jobs_open_intent",
    "ix_jobs_due_retry",
    "ix_jobs_synchronization_target",
    "uq_synchronization_runs_running_source",
}
```

**Дія 4:** файл отримує дві функції і нові імпорти; заміни його цілком.

Заміни весь `src/knowledge_service/persistence/sources.py` на:

```python
"""Statements over Sources, their synchronization runs, their fence, and schedule."""

from collections.abc import Collection
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.identifiers import SourceId
from knowledge_service.jobs import JobKind
from knowledge_service.persistence.jobs import open_job
from knowledge_service.persistence.schema import jobs, sources, synchronization_runs


def source_runs_statement(source_id: UUID, limit: int) -> sa.Select[Any]:
    """Select the most recent Synchronization Runs of one Source."""
    return (
        sa.select(synchronization_runs)
        .where(synchronization_runs.c.source_id == source_id)
        .order_by(synchronization_runs.c.started_at.desc())
        .limit(limit)
    )


async def claim_synchronization_fence(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> bool:
    """Make `observed_at` the Source's fence, if it is newer than the fence.

    False when a pass that observed the Source at the same time or later
    already claimed it. The update waits for every write that holds the
    fence (`synchronization_fence_holds`), so none of them lands after it.
    """
    claimed = await connection.scalar(
        sa.update(sources)
        .where(
            sources.c.source_id == source_id.value,
            sa.or_(
                sources.c.synchronization_fence.is_(None),
                sources.c.synchronization_fence < observed_at,
            ),
        )
        .values(synchronization_fence=observed_at)
        .returning(sources.c.source_id)
    )
    return claimed is not None


async def synchronization_fence_holds(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> bool:
    """Whether the pass that observed at `observed_at` still holds the fence.

    The Source's row is share-locked until the transaction ends, so a newer
    pass cannot claim the fence while this write is in progress.
    """
    fence = await connection.scalar(
        sa.select(sources.c.synchronization_fence)
        .where(sources.c.source_id == source_id.value)
        .with_for_update(read=True)
    )
    return fence == observed_at


async def load_due_source_ids(
    connection: AsyncConnection, *, now: datetime, interval: timedelta, limit: int
) -> tuple[SourceId, ...]:
    """Enabled Sources with no open synchronization Job whose last one is old.

    A Source is due when its newest synchronization Job, counted from when it
    finished, or was queued if it never finished, is at least `interval`
    before `now`, or when it never had one. However long ago that was, it is
    due once: a missed schedule is not caught up run by run. A time later
    than `now`, from a clock that ran ahead, is ignored: a finish time in the
    future counts from when the Job was queued instead, and a Job queued in
    the future does not count. The longest-waiting Sources come first, so
    when more are due than `limit`, the rest come next pass.
    """
    of_source = sa.and_(
        jobs.c.kind == JobKind.SYNCHRONIZE_SOURCE.value,
        jobs.c.target_id == sources.c.source_id,
    )
    # greatest() skips the NULL a future finish time becomes here.
    activity = sa.func.greatest(
        jobs.c.queued_at,
        sa.case((jobs.c.finished_at <= now, jobs.c.finished_at), else_=None),
    )
    candidates = (
        sa.select(
            sources.c.source_id,
            sa.select(sa.func.max(activity))
            .where(of_source, jobs.c.queued_at <= now)
            .scalar_subquery()
            .label("last_activity"),
        )
        .where(sources.c.enabled.is_(True), ~sa.exists().where(of_source, open_job()))
        # Materialized, so each Source's last activity is computed once; a
        # plain subquery is inlined and the planner repeats it per use.
        .cte("candidates")
        .prefix_with("MATERIALIZED")
    )
    statement = (
        sa.select(candidates.c.source_id)
        .where(
            sa.or_(
                candidates.c.last_activity.is_(None),
                candidates.c.last_activity <= now - interval,
            )
        )
        .order_by(
            candidates.c.last_activity.asc().nulls_first(), candidates.c.source_id
        )
        .limit(limit)
    )
    rows = await connection.scalars(statement)
    return tuple(SourceId(source_id) for source_id in rows)


async def lock_enabled_sources(
    connection: AsyncConnection, *, source_ids: Collection[SourceId]
) -> frozenset[SourceId]:
    """Those of `source_ids` that still exist and are enabled, share-locked.

    Disabling or deleting one of them now waits for this transaction, so a
    Job queued in it is queued before the change, never after.
    """
    if not source_ids:
        return frozenset()
    rows = await connection.scalars(
        sa.select(sources.c.source_id)
        .where(
            sources.c.source_id.in_([source_id.value for source_id in source_ids]),
            sources.c.enabled.is_(True),
        )
        .order_by(sources.c.source_id)
        .with_for_update(read=True)
    )
    return frozenset(SourceId(source_id) for source_id in rows)
```

**Перевірка:** `uv run pytest tests/test_indexes.py -q` показує `4 passed`, а `uv run pytest tests/test_migrations_integration.py -q -m integration` показує `3 passed`.

### Крок 3 — Крок з кодом: розклад

Цей крок додає модуль, що ставить Job у чергу, і налаштування інтервалу.

**Простими словами:** одна функція бере поточний час і розклад, знаходить джерела, яким настав час, перевіряє їх ще раз і зберігає по одному Job у черзі для кожного, вказуючи лише ID джерела. Вона повідомляє, які Job зберегла, а які знайшла вже збереженими проходом, що йшов у ту саму мить.

| Частина | Що робить |
|---|---|
| `SynchronizationSchedule` | інтервал від 1 хвилини до 7 днів, за замовчуванням година |
| `ScheduleReport` | ID Job у `queued` і `already_open` |
| `schedule_due_synchronizations` | перевіряє UTC і `limit` (від 1 до 1 000), потім читає, перевіряє ще раз і ставить у чергу в одній транзакції |
| `synchronization_interval_minutes` | налаштування від 1 до 10 080, за замовчуванням 60 |


**Дія 1:** створи модуль.

Створи `src/knowledge_service/ingestion/scheduling.py`:

```python
"""Queue a synchronization Job for every enabled Source whose time has come.

A pass of the schedule runs with every `make worker-relay`, on the clock it
is given. For each enabled Source with no open synchronization Job whose last
Job ended at least one interval ago, it stores one queued Job. The Job names
the Source by its ID and nothing else: the worker reads the Source's
location, credentials reference, and checkpoint from PostgreSQL when it
runs, so no configuration or secret travels through Redis, and a changed
configuration is seen by the next run.

Redundant schedules are suppressed three ways: a Source with an open Job is
skipped, a Source that ran within the interval is not due, and a missed
schedule, after the scheduler was down, queues one Job, not one per missed
interval. Two passes at once still store one Job per Source, through the
open-intent index (M08-T03). When more Sources are due than a pass may
queue, the longest-waiting go first, so none is starved.

A Source with an open synchronization Job is not scheduled again until that
Job ends: a Job stuck running waits for M08-T08, and until the handler of
M08-T10 runs these Jobs they stay queued, so each Source is queued once.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId
from knowledge_service.jobs import Job, JobKind
from knowledge_service.persistence.jobs import enqueue_job
from knowledge_service.persistence.sources import (
    load_due_source_ids,
    lock_enabled_sources,
)

MIN_INTERVAL: Final = timedelta(minutes=1)
MAX_INTERVAL: Final = timedelta(days=7)
MAX_SCHEDULE_BATCH: Final = 1_000


@dataclass(frozen=True, slots=True)
class SynchronizationSchedule:
    """How often each enabled Source is synchronized."""

    interval: timedelta = timedelta(hours=1)

    def __post_init__(self) -> None:
        if type(self.interval) is not timedelta or not (
            MIN_INTERVAL <= self.interval <= MAX_INTERVAL
        ):
            raise ValueError(f"interval must be from {MIN_INTERVAL} to {MAX_INTERVAL}")


@dataclass(frozen=True, slots=True)
class ScheduleReport:
    """What one pass of the schedule queued, and what it found already open."""

    queued: tuple[JobId, ...]
    already_open: tuple[JobId, ...]


async def schedule_due_synchronizations(
    runtime: DatabaseRuntime,
    *,
    now: datetime,
    schedule: SynchronizationSchedule,
    limit: int = 100,
) -> ScheduleReport:
    """Queue one synchronization Job per due Source, up to `limit`.

    `already_open` lists Sources whose Job another pass stored between this
    pass's read and its write; nothing new is stored for them.
    """
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")
    if type(limit) is not int or not 1 <= limit <= MAX_SCHEDULE_BATCH:
        raise ValueError(f"limit must be an integer from 1 to {MAX_SCHEDULE_BATCH}")
    queued: list[JobId] = []
    already_open: list[JobId] = []
    async with runtime.transaction() as connection:
        due = await load_due_source_ids(
            connection, now=now, interval=schedule.interval, limit=limit
        )
        # A Source disabled or deleted since that read is left out.
        still_enabled = await lock_enabled_sources(connection, source_ids=due)
        for source_id in (s for s in due if s in still_enabled):
            enqueued = await enqueue_job(
                connection,
                Job.request(
                    job_id=JobId.new(),
                    kind=JobKind.SYNCHRONIZE_SOURCE,
                    target_id=source_id.value,
                    queued_at=now,
                ),
            )
            (queued if enqueued.created else already_open).append(enqueued.job.job_id)
    return ScheduleReport(queued=tuple(queued), already_open=tuple(already_open))
```

**Дія 2:** додай налаштування після `database_statement_timeout_ms`:

У `src/knowledge_service/settings.py` знайди:

```python
    database_pool_timeout: float = Field(default=10.0, gt=0.0, allow_inf_nan=False)
    database_connect_timeout_seconds: int = Field(default=5, gt=0)
    database_statement_timeout_ms: int = Field(default=10_000, gt=0)
    # The Redis broker that carries worker messages; it may hold a password.
    redis_url: SecretStr | None = None
    redis_health_timeout_seconds: float = Field(
```

і заміни на:

```python
    database_pool_timeout: float = Field(default=10.0, gt=0.0, allow_inf_nan=False)
    database_connect_timeout_seconds: int = Field(default=5, gt=0)
    database_statement_timeout_ms: int = Field(default=10_000, gt=0)
    # How often each enabled Source is synchronized (`ingestion.scheduling`).
    synchronization_interval_minutes: int = Field(default=60, ge=1, le=7 * 24 * 60)
    # The Redis broker that carries worker messages; it may hold a password.
    redis_url: SecretStr | None = None
    redis_health_timeout_seconds: float = Field(
```

**Дія 3:** unit-тести інтервалу і налаштування.

Створи `tests/test_scheduling.py`:

```python
"""Tests for the synchronization schedule's bounds and its setting."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from knowledge_service.ingestion.scheduling import (
    MAX_INTERVAL,
    MIN_INTERVAL,
    SynchronizationSchedule,
)
from knowledge_service.settings import Settings


def test_every_source_is_synchronized_hourly_by_default() -> None:
    assert SynchronizationSchedule().interval == timedelta(hours=1)
    assert Settings.model_fields["synchronization_interval_minutes"].default == 60


@pytest.mark.parametrize("interval", [MIN_INTERVAL, timedelta(hours=6), MAX_INTERVAL])
def test_an_interval_within_bounds_is_accepted(interval: timedelta) -> None:
    assert SynchronizationSchedule(interval=interval).interval == interval


@pytest.mark.parametrize(
    "interval",
    [
        MIN_INTERVAL - timedelta(seconds=1),
        MAX_INTERVAL + timedelta(seconds=1),
        timedelta(0),
        60,
        None,
    ],
)
def test_an_interval_out_of_bounds_is_refused(interval: object) -> None:
    with pytest.raises(ValueError, match="interval"):
        SynchronizationSchedule(interval=interval)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("minutes", [0, -5, 7 * 24 * 60 + 1])
def test_the_interval_setting_is_bounded(minutes: int) -> None:
    with pytest.raises(ValidationError):
        Settings(synchronization_interval_minutes=minutes)
```

**Перевірка:** `uv run pytest tests/test_scheduling.py -q` показує `12 passed`.

### Крок 4 — Крок з кодом: запускати розклад у кожному проході relay

Цей крок змушує `make worker-relay` ставити в чергу синхронізації, яким настав час, між поверненням повторів і надсиланням.

**Простими словами:** прохід relay тепер має три частини: повернути повтори, яким настав час, поставити синхронізації, яким настав час, і надіслати повідомлення. Якщо середня частина падає, relay пише це в лог і все одно надсилає повідомлення всіх інших Job, а потім завершується з кодом 1, щоб планувальник це помітив.

| Частина | Що робить |
|---|---|
| `RelayPass` | що зробив прохід, включно з `schedule_failed` |
| `relay_once` | три частини, збій планування ізольовано; публічна, щоб її можна було протестувати |
| `main` | друкує `requeued N, scheduled M, published ...` |

**Дія 1:** relay змінюється всюди; заміни його цілком.

Заміни весь `src/knowledge_service/worker/relay.py` на:

```python
"""Queue due retries and due synchronizations, then publish: one pass.

Run `python -m knowledge_service.worker.relay` after a crash or a Redis
outage, and on a schedule, because it is also what brings a failed Job back
once its retry time has come, and what queues each enabled Source's next
synchronization. It exits 0 when every Job it found was published or
skipped, and 1 when the queue refused a message or scheduling failed; the
rest wait for the next pass. A failed scheduling step does not stop the
publishing step.
"""

import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.ingestion.scheduling import (
    ScheduleReport,
    SynchronizationSchedule,
    schedule_due_synchronizations,
)
from knowledge_service.job_dispatch import (
    JobPublisher,
    RelayReport,
    publish_pending_jobs,
    requeue_due_retries,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import broker_from_settings
from knowledge_service.worker.jobs import (
    HANDLERS,
    DramatiqJobPublisher,
    JobRunner,
    declare_job_actors,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RelayPass:
    """What one relay pass did, in the order it did it."""

    requeued: tuple[JobId, ...]
    scheduled: ScheduleReport
    schedule_failed: bool
    published: RelayReport


async def relay_once(
    runtime: DatabaseRuntime,
    *,
    publisher: JobPublisher,
    now: datetime,
    schedule: SynchronizationSchedule,
) -> RelayPass:
    """Queue due retries, queue due synchronizations, then publish.

    A failure while scheduling is logged by type and reported; publishing
    still runs, so other Jobs are not held up by it.
    """
    requeued = await requeue_due_retries(runtime, now=now)
    try:
        scheduled = await schedule_due_synchronizations(
            runtime, now=now, schedule=schedule
        )
        schedule_failed = False
    except Exception as error:
        logger.warning("scheduling synchronizations failed: %s", type(error).__name__)
        scheduled = ScheduleReport(queued=(), already_open=())
        schedule_failed = True
    published = await publish_pending_jobs(runtime, publisher=publisher, now=now)
    return RelayPass(
        requeued=requeued,
        scheduled=scheduled,
        schedule_failed=schedule_failed,
        published=published,
    )


async def _relay(settings: Settings, publisher: JobPublisher) -> RelayPass:
    runtime = create_database_runtime(settings)
    schedule = SynchronizationSchedule(
        interval=timedelta(minutes=settings.synchronization_interval_minutes)
    )
    try:
        return await relay_once(
            runtime, publisher=publisher, now=datetime.now(UTC), schedule=schedule
        )
    finally:
        await runtime.dispose()


def main() -> int:
    settings = Settings()
    broker = broker_from_settings(settings)
    try:
        actors = declare_job_actors(
            broker, run_job=JobRunner(settings), kinds=frozenset(HANDLERS)
        )
        done = asyncio.run(_relay(settings, DramatiqJobPublisher(actors)))
    finally:
        broker.close()
    report = done.published
    sys.stdout.write(
        f"requeued {len(done.requeued)}, scheduled {len(done.scheduled.queued)}, "
        f"published {report.published}, skipped {report.skipped}, "
        f"failed {report.failed}, untried {report.untried}\n"
    )
    return 1 if report.failed or done.schedule_failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

**Перевірка:** `uv run pyright src` показує 0 помилок.

### Крок 5 — Крок з кодом: довести розклад на годиннику, яким керують тести

Цей крок перевіряє кожен випадок із «Перед початком» на справжньому PostgreSQL, з явно переданим `now`.

**Простими словами:** кожен тест кладе в базу джерела і минулі Job, запускає прохід у точно заданий момент і перевіряє, які Job існують після нього. Жоден тест не чекає справжнього часу.

| Тест | Що доводить |
|---|---|
| `test_a_source_that_never_synchronized_is_due_at_once` | настав час |
| `test_a_source_is_due_one_interval_after_its_last_run_finished` | 59 хвилин: ні; година: так |
| `test_a_long_run_is_counted_from_when_it_finished` | блок 1 |
| `test_a_disabled_source_is_never_scheduled` | вимкнене |
| `test_a_source_whose_run_is_still_open_is_not_scheduled_again` | вже йде: у черзі, виконується або впало із запланованим повтором |
| `test_a_missed_schedule_queues_one_run_not_one_per_interval` | пропущене |
| `test_two_passes_at_once_store_one_run_per_source` | одночасність |
| `test_a_run_names_its_source_by_id_and_carries_no_configuration` | блок 3 |
| `test_a_pass_takes_sources_in_a_stable_order_up_to_its_limit`, `..._limit_is_bounded`, `..._needs_utc` | межі |
| `test_an_unknown_target_id_is_not_a_source` | Job видаленого джерела не робить нікому час |
| `test_the_longest_waiting_sources_go_first_so_none_is_starved` | блок 4, голодування |
| `test_a_time_from_a_fast_clock_delays_a_source_by_one_interval_at_most` | блок 4, годинник |
| `test_a_source_disabled_or_deleted_during_a_pass_gets_no_job` | блок 4, зміна посеред проходу |
| `test_a_failed_scheduling_step_does_not_stop_publishing` | блок 4, relay |

Створи `tests/test_scheduling_integration.py`:

```python
"""Scheduling synchronizations against real PostgreSQL, on a clock the tests set.

Every test passes `now` itself, so "due", "not due yet", and "missed for ten
hours" are exact moments, not waits.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.ingestion import scheduling
from knowledge_service.ingestion.scheduling import (
    ScheduleReport,
    SynchronizationSchedule,
    schedule_due_synchronizations,
)
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
from knowledge_service.persistence.schema import jobs
from knowledge_service.settings import Settings
from knowledge_service.worker.relay import relay_once

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
HOURLY = SynchronizationSchedule(interval=timedelta(hours=1))
FIRST = SourceId.parse("00000000-0000-0000-0000-000000000061")
SECOND = SourceId.parse("00000000-0000-0000-0000-000000000062")
THIRD = SourceId.parse("00000000-0000-0000-0000-000000000063")
# The name of a credential, which a Job must not carry either.
CREDENTIALS_NAME = "vault:confluence/handbook"

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, credentials_reference,"
    " display_name, enabled, created_at, updated_at) VALUES (:source_id,"
    " 'confluence', :location, :credentials, 'Handbook', :enabled, now(), now())"
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


async def add_source(
    runtime: DatabaseRuntime, source_id: SourceId, *, enabled: bool = True
) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            INSERT_SOURCE,
            {
                "source_id": source_id.value,
                "location": f"https://wiki.example.com/spaces/{source_id.value}",
                "credentials": CREDENTIALS_NAME,
                "enabled": enabled,
            },
        )


async def past_job(
    runtime: DatabaseRuntime,
    source_id: SourceId,
    *,
    queued_at: datetime,
    finished_at: datetime | None = None,
    status: JobStatus = JobStatus.SUCCEEDED,
) -> Job:
    """A synchronization Job of `source_id` that reached `status`."""
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=source_id.value,
        queued_at=queued_at,
    )
    async with runtime.transaction() as connection:
        await enqueue_job(connection, job)
        if status is JobStatus.QUEUED:
            return job
        job = job.start(started_at=queued_at)
        if status is JobStatus.SUCCEEDED:
            assert finished_at is not None
            job = job.succeed(finished_at=finished_at)
        elif status is JobStatus.FAILED:
            assert finished_at is not None
            job = job.fail(
                error_code="source_unavailable",
                finished_at=finished_at,
                retry_at=finished_at + timedelta(minutes=5),
            )
        return await save_job(connection, job=job)


async def schedule(runtime: DatabaseRuntime, now: datetime) -> ScheduleReport:
    return await schedule_due_synchronizations(runtime, now=now, schedule=HOURLY)


async def sync_jobs(runtime: DatabaseRuntime, source_id: SourceId) -> list[Job]:
    async with runtime.transaction() as connection:
        ids = await connection.scalars(
            sa.select(jobs.c.job_id)
            .where(
                jobs.c.kind == JobKind.SYNCHRONIZE_SOURCE.value,
                jobs.c.target_id == source_id.value,
            )
            .order_by(jobs.c.queued_at)
        )
        found = [await load_job(connection, job_id=JobId(job_id)) for job_id in ids]
    return [job for job in found if job is not None]


async def test_a_source_that_never_synchronized_is_due_at_once(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)

    report = await schedule(runtime, NOW)

    [job] = await sync_jobs(runtime, FIRST)
    assert report == ScheduleReport(queued=(job.job_id,), already_open=())
    assert (job.status, job.queued_at) == (JobStatus.QUEUED, NOW)


async def test_a_source_is_due_one_interval_after_its_last_run_finished(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)
    await past_job(
        runtime, FIRST, queued_at=NOW - timedelta(minutes=10), finished_at=NOW
    )

    early = await schedule(runtime, NOW + timedelta(minutes=59))
    due = await schedule(runtime, NOW + timedelta(hours=1))

    assert early.queued == ()
    assert len(due.queued) == 1


async def test_a_long_run_is_counted_from_when_it_finished(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)
    # Queued three hours ago, it took two hours and a half.
    await past_job(
        runtime,
        FIRST,
        queued_at=NOW - timedelta(hours=3),
        finished_at=NOW - timedelta(minutes=30),
    )

    assert (await schedule(runtime, NOW)).queued == ()
    assert len((await schedule(runtime, NOW + timedelta(minutes=30))).queued) == 1


async def test_a_disabled_source_is_never_scheduled(runtime: DatabaseRuntime) -> None:
    await add_source(runtime, FIRST, enabled=False)

    report = await schedule(runtime, NOW + timedelta(days=30))

    assert report.queued == ()
    assert await sync_jobs(runtime, FIRST) == []


@pytest.mark.parametrize(
    "status", [JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.FAILED]
)
async def test_a_source_whose_run_is_still_open_is_not_scheduled_again(
    runtime: DatabaseRuntime, status: JobStatus
) -> None:
    await add_source(runtime, FIRST)
    # Queued, running, or failed with a retry planned: still open.
    open_job = await past_job(
        runtime,
        FIRST,
        queued_at=NOW - timedelta(hours=5),
        finished_at=NOW - timedelta(hours=4),
        status=status,
    )

    report = await schedule(runtime, NOW)

    assert report.queued == ()
    assert [job.job_id for job in await sync_jobs(runtime, FIRST)] == [open_job.job_id]


async def test_a_missed_schedule_queues_one_run_not_one_per_interval(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)
    # The scheduler was down for ten hours: ten intervals were missed.
    await past_job(
        runtime,
        FIRST,
        queued_at=NOW - timedelta(hours=10, minutes=5),
        finished_at=NOW - timedelta(hours=10),
    )

    first = await schedule(runtime, NOW)
    second = await schedule(runtime, NOW)

    assert len(first.queued) == 1
    assert second.queued == ()
    assert len(await sync_jobs(runtime, FIRST)) == 2


async def test_two_passes_at_once_store_one_run_per_source(
    runtime: DatabaseRuntime,
) -> None:
    for source_id in (FIRST, SECOND):
        await add_source(runtime, source_id)

    reports = await asyncio.gather(schedule(runtime, NOW), schedule(runtime, NOW))

    stored = [
        job.job_id for s in (FIRST, SECOND) for job in await sync_jobs(runtime, s)
    ]
    assert len(stored) == 2
    # Each Job was stored by exactly one pass; the other found it or skipped it.
    assert sum(len(r.queued) for r in reports) == 2
    reported = {job_id for r in reports for job_id in r.queued + r.already_open}
    assert reported <= set(stored)


async def test_a_run_names_its_source_by_id_and_carries_no_configuration(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)

    [job_id] = (await schedule(runtime, NOW)).queued

    async with runtime.transaction() as connection:
        row = (
            (
                await connection.execute(
                    sa.select(jobs).where(jobs.c.job_id == job_id.value)
                )
            )
            .mappings()
            .one()
        )
    assert row["kind"] == JobKind.SYNCHRONIZE_SOURCE.value
    assert row["target_id"] == FIRST.value
    stored = " ".join(str(value) for value in row.values())
    assert CREDENTIALS_NAME not in stored
    assert "wiki.example.com" not in stored


async def test_a_pass_takes_sources_in_a_stable_order_up_to_its_limit(
    runtime: DatabaseRuntime,
) -> None:
    for source_id in (THIRD, FIRST, SECOND):
        await add_source(runtime, source_id)

    first = await schedule_due_synchronizations(
        runtime, now=NOW, schedule=HOURLY, limit=2
    )
    second = await schedule_due_synchronizations(
        runtime, now=NOW, schedule=HOURLY, limit=2
    )

    assert [len(first.queued), len(second.queued)] == [2, 1]
    assert [job.target_id for job in await sync_jobs(runtime, THIRD)] == [THIRD.value]
    queued_first = {
        job.target_id
        for source_id in (FIRST, SECOND)
        for job in await sync_jobs(runtime, source_id)
        if job.job_id in first.queued
    }
    assert queued_first == {FIRST.value, SECOND.value}


@pytest.mark.parametrize("limit", [0, 1_001, True, 2.0])
async def test_the_pass_limit_is_bounded(
    runtime: DatabaseRuntime, limit: object
) -> None:
    with pytest.raises(ValueError, match="limit"):
        await schedule_due_synchronizations(
            runtime,
            now=NOW,
            schedule=HOURLY,
            limit=limit,  # pyright: ignore[reportArgumentType]
        )


async def test_the_pass_needs_utc(runtime: DatabaseRuntime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        await schedule(runtime, datetime(2026, 11, 20, 9, 0))


async def test_an_unknown_target_id_is_not_a_source(runtime: DatabaseRuntime) -> None:
    # A synchronization Job whose Source was deleted does not make a Source due.
    await past_job(
        runtime,
        SourceId(uuid.uuid4()),
        queued_at=NOW - timedelta(hours=5),
        finished_at=NOW - timedelta(hours=4),
    )

    assert (await schedule(runtime, NOW)).queued == ()


async def test_the_longest_waiting_sources_go_first_so_none_is_starved(
    runtime: DatabaseRuntime,
) -> None:
    sources = [
        SourceId.parse(f"00000000-0000-0000-0000-0000000000{n}") for n in range(70, 75)
    ]
    for source_id in sources:
        await add_source(runtime, source_id)
    ever_queued: set[SourceId] = set()

    for hours in (0, 2, 4):
        report = await schedule_due_synchronizations(
            runtime, now=NOW + timedelta(hours=hours), schedule=HOURLY, limit=2
        )
        # Each run finishes a minute later, so its Source is due again soon.
        async with runtime.transaction() as connection:
            for job_id in report.queued:
                job = await load_job(connection, job_id=job_id)
                assert job is not None
                ended = job.start(started_at=job.queued_at).succeed(
                    finished_at=job.queued_at + timedelta(minutes=1)
                )
                await save_job(connection, job=ended)
                ever_queued.add(SourceId(job.target_id))

    # Two per pass over three passes reach all five, low IDs or not.
    assert ever_queued == set(sources)


async def test_a_time_from_a_fast_clock_delays_a_source_by_one_interval_at_most(
    runtime: DatabaseRuntime,
) -> None:
    await add_source(runtime, FIRST)
    # A worker whose clock ran a year ahead recorded this finish time.
    await past_job(
        runtime,
        FIRST,
        queued_at=NOW - timedelta(minutes=5),
        finished_at=NOW + timedelta(days=365),
    )

    assert (await schedule(runtime, NOW)).queued == ()
    assert len((await schedule(runtime, NOW + timedelta(hours=1))).queued) == 1


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE sources SET enabled = false WHERE source_id = :source_id",
        "DELETE FROM sources WHERE source_id = :source_id",
    ],
    ids=["disabled", "deleted"],
)
async def test_a_source_disabled_or_deleted_during_a_pass_gets_no_job(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    await add_source(runtime, FIRST)
    load_due = scheduling.load_due_source_ids

    async def then_someone_changes_it(*args: object, **kwargs: object) -> object:
        due = await load_due(*args, **kwargs)  # pyright: ignore[reportArgumentType]
        # Another session commits the change right after this pass read it.
        async with runtime.transaction() as other:
            await other.execute(sa.text(change), {"source_id": FIRST.value})
        return due

    monkeypatch.setattr(scheduling, "load_due_source_ids", then_someone_changes_it)
    report = await schedule(runtime, NOW)

    assert report.queued == ()
    assert await sync_jobs(runtime, FIRST) == []


async def test_a_failed_scheduling_step_does_not_stop_publishing(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    purge = Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_DOCUMENT_CHUNKS,
        target_id=uuid.uuid4(),
        queued_at=NOW,
    )
    async with runtime.transaction() as connection:
        await enqueue_job(connection, purge)
    sent: list[JobId] = []

    class Recording:
        kinds = frozenset({JobKind.PURGE_DOCUMENT_CHUNKS})

        def publish(self, job: Job) -> None:
            sent.append(job.job_id)

    async def broken(*args: object, **kwargs: object) -> ScheduleReport:
        raise RuntimeError("the schedule query timed out")

    monkeypatch.setattr(
        "knowledge_service.worker.relay.schedule_due_synchronizations", broken
    )
    done = await relay_once(runtime, publisher=Recording(), now=NOW, schedule=HOURLY)

    assert done.schedule_failed
    assert sent == [purge.job_id]
    assert done.published.published == 1
```

**Перевірка:** `uv run pytest tests/test_scheduling_integration.py -q -m integration` показує `22 passed`.

### Крок 6 — У цьому кроці коду немає: зламай навмисно

Цей крок робить так, що кожному увімкненому джерелу ніби вже настав час, і показує, які тести це помічають.

**Дія 1:** у `load_due_source_ids` у `src/knowledge_service/persistence/sources.py` зміни:

```python
                candidates.c.last_activity <= now - interval,
```

на:

```python
                sa.true(),
```

**Дія 2:** запусти:

```bash
uv run pytest tests/test_scheduling.py tests/test_scheduling_integration.py -q -m "integration or not integration"
```

Ти побачиш `3 failed, 31 passed`. Падають три тести, що рахують інтервал. Тести пропущеного розкладу і «вже йде» досі проходять, бо відкритий Job сам по собі вже не пускає другого: захист від зайвих розкладів має більше ніж один шар.

**Дія 3:** поверни зміну і запусти команду знову: `34 passed`.

**Перевірка:** порівняй кількість зі своїм прогнозом із кроку 1 і запиши її у файл доказів.

### Крок 7 — У цьому кроці коду немає: онови три документи

Цей крок записує розклад там, де його шукатимуть читачі.


**Дія 1:** в архітектурі онови рядки `ingestion` і `worker` таблиці модулів:

У `docs/architecture.md` знайди:

```markdown

| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source` (claims the Source's fence and checks it in every write; an optional `heartbeat` runs after every page), `synchronize_source_exclusively` (`exclusive_sync`, the same pass while holding the Source's lease), `normalize`, `reconcile_document`, `normalize_access` (`grant_normalization`, a Source's `user:<id>` and `group:<id>` principals to Access Grant subjects through its `PrincipalDirectory`, as the same UUIDv5 IDs the Authorization Context uses, and only to subjects and paths a token could carry; distinct, in a stable order, and failing closed: an unmapped or malformed principal grants nothing and is reported), `narrow_access` (a record that fails to normalize keeps its last good version only for readers its old and new access both name) |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs and no tools, where a reply holding a tool call is unreadable; `AnswerSettings` gives the budget), `has_active_content` / `unsourced_links` (`answer_content`, what an Answer may hold whatever a Document told the model: no Markdown image, loading HTML tag, or mermaid block, and only links an Evidence it cites contains; `held_from` marks where a stream stops sending text until the check), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`, with `DatabaseAuditTrail` (`audit_trail`) as its `AuditTrail`: one `question.asked` audit event per stream, committed before the decision's Citations and completion are sent; finds the Evidence the request's reader may see, rereads it as the reader with `evidence_still_visible`, and judges it before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply, has its `CitationCheck` (`CurrentCitations`, each call in a short transaction of its own) reread every cited Chunk as the reader with `confirm_citations`, withdrawing the Answer if one fails, and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `VectorEvidenceSource` (`vector_evidence`, the stream's Evidence from PostgreSQL: checks the reader, embeds the Question, requires one vector from its own model, and runs the vector search with the request's Authorization Context, so eligibility is decided in the same SQL that ranks; no reranking, so its signals carry no score), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |
| `worker` | Queue transport: the Redis broker and the worker process | `build_broker` (`broker`, a Dramatiq `RedisBroker` with JSON messages, the `knowledge-service.v1` namespace, the `maintenance` and `synchronization` queues, a 60-second default time limit, a one-day message age limit, and no Dramatiq retries; `ActorOwnsOptions` drops any limit a message sets for itself, so only its actor's limits apply; Redis calls make one attempt with bounded timeouts), `declare_actors` / `send_ping` (`actors`, the harmless `ping` job, whose token is checked on both sides), `broker_ready` (`health`, a readiness probe that is false, within one overall deadline, whenever Redis does not answer PING or any part of the check fails), `declare_job_actors` / `DramatiqJobPublisher` / `run_stored_job` (`jobs`, one `run_<queue>_job` actor per queue of the kinds in `HANDLERS`, today only `purge_document_chunks`; a message carries one canonical Job ID, and the worker reads the Job from PostgreSQL, runs it only while it is queued, and returns the stored Job as the outcome of a duplicate; `JobRunner` gives each message its own event loop and database engine), `relay` (one pass: `requeue_due_retries`, then `publish_pending_jobs`), and `main`, the module the `dramatiq` command loads |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level. Among them, `roles` holds the Application Roles policy:
```

і заміни на:

```markdown

| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source` (claims the Source's fence and checks it in every write; an optional `heartbeat` runs after every page), `synchronize_source_exclusively` (`exclusive_sync`, the same pass while holding the Source's lease), `schedule_due_synchronizations` (`scheduling`, a synchronization Job for each enabled Source that is due), `normalize`, `reconcile_document`, `normalize_access` (`grant_normalization`, a Source's `user:<id>` and `group:<id>` principals to Access Grant subjects through its `PrincipalDirectory`, as the same UUIDv5 IDs the Authorization Context uses, and only to subjects and paths a token could carry; distinct, in a stable order, and failing closed: an unmapped or malformed principal grants nothing and is reported), `narrow_access` (a record that fails to normalize keeps its last good version only for readers its old and new access both name) |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs and no tools, where a reply holding a tool call is unreadable; `AnswerSettings` gives the budget), `has_active_content` / `unsourced_links` (`answer_content`, what an Answer may hold whatever a Document told the model: no Markdown image, loading HTML tag, or mermaid block, and only links an Evidence it cites contains; `held_from` marks where a stream stops sending text until the check), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`, with `DatabaseAuditTrail` (`audit_trail`) as its `AuditTrail`: one `question.asked` audit event per stream, committed before the decision's Citations and completion are sent; finds the Evidence the request's reader may see, rereads it as the reader with `evidence_still_visible`, and judges it before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply, has its `CitationCheck` (`CurrentCitations`, each call in a short transaction of its own) reread every cited Chunk as the reader with `confirm_citations`, withdrawing the Answer if one fails, and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `VectorEvidenceSource` (`vector_evidence`, the stream's Evidence from PostgreSQL: checks the reader, embeds the Question, requires one vector from its own model, and runs the vector search with the request's Authorization Context, so eligibility is decided in the same SQL that ranks; no reranking, so its signals carry no score), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |
| `worker` | Queue transport: the Redis broker and the worker process | `build_broker` (`broker`, a Dramatiq `RedisBroker` with JSON messages, the `knowledge-service.v1` namespace, the `maintenance` and `synchronization` queues, a 60-second default time limit, a one-day message age limit, and no Dramatiq retries; `ActorOwnsOptions` drops any limit a message sets for itself, so only its actor's limits apply; Redis calls make one attempt with bounded timeouts), `declare_actors` / `send_ping` (`actors`, the harmless `ping` job, whose token is checked on both sides), `broker_ready` (`health`, a readiness probe that is false, within one overall deadline, whenever Redis does not answer PING or any part of the check fails), `declare_job_actors` / `DramatiqJobPublisher` / `run_stored_job` (`jobs`, one `run_<queue>_job` actor per queue of the kinds in `HANDLERS`, today only `purge_document_chunks`; a message carries one canonical Job ID, and the worker reads the Job from PostgreSQL, runs it only while it is queued, and returns the stored Job as the outcome of a duplicate; `JobRunner` gives each message its own event loop and database engine), `relay` (one pass: `requeue_due_retries`, `schedule_due_synchronizations`, then `publish_pending_jobs`), and `main`, the module the `dramatiq` command loads |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level. Among them, `roles` holds the Application Roles policy:
```

і додай абзац про розклад після абзацу про оренди:

У `docs/architecture.md` знайди:

```markdown
`SourceUnavailable`, so a Job that meets one is retried later, with a new
observation time.

`telemetry` decides what leaves the process as logs. `configure_telemetry`
installs one root handler that writes JSON lines, and its `TelemetryFilter`
keeps only the fields `TELEMETRY_FIELDS` lists, with plain values, only this
```

і заміни на:

```markdown
`SourceUnavailable`, so a Job that meets one is retried later, with a new
observation time.

`schedule_due_synchronizations` (`ingestion.scheduling`), run by every
`make worker-relay` pass after the retries, queues one `synchronize_source`
Job for each enabled Source with no open synchronization Job whose last one
finished, or was queued if it never finished, at least one interval ago
(`synchronization_interval_minutes`, an hour by default). A Source that
never synchronized is due at once; a disabled one never is. A schedule
missed while the relay was not running queues one Job, not one per missed
interval, and two passes at once still store one Job per Source through the
open-intent index. When more Sources are due than a pass may queue, the
longest-waiting go first, so none is starved; a finish time from a clock that
ran ahead is ignored, so it cannot silence a Source; and the pass rechecks,
under a share lock, that each Source still exists and is enabled before it
queues its Job. A Source with an open synchronization Job is not scheduled
again until that Job ends, so a Job stuck running waits for M08-T08. A
failure while scheduling is logged and makes the relay exit 1, but the relay
still publishes. The Job carries the Source's ID only: its handler
(M08-T10) reads the location, credentials reference, and checkpoint from
PostgreSQL when it runs, so no configuration or secret travels through
Redis. Until then the Jobs wait queued and unpublished, as no worker runs
that kind yet.

`telemetry` decides what leaves the process as logs. `configure_telemetry`
installs one root handler that writes JSON lines, and its `TelemetryFilter`
keeps only the fields `TELEMETRY_FIELDS` lists, with plain values, only this
```

**Дія 2:** у посібнику з експлуатації скажи, що relay також планує синхронізації:

У `docs/operations.md` знайди:

```markdown

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. A failed Job comes back only through `make worker-relay`, which queues due retries before it publishes, so run it on a schedule. A synchronization pass takes its Source's lease in Redis first; while Redis is down no pass starts, and its Job is retried later. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

і заміни на:

```markdown

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. A failed Job comes back, and each enabled Source's next synchronization is queued, only through `make worker-relay`, which queues due retries and due synchronizations before it publishes, so run it on a schedule more often than `KNOWLEDGE_SERVICE_SYNCHRONIZATION_INTERVAL_MINUTES` (60 by default). A synchronization pass takes its Source's lease in Redis first; while Redis is down no pass starts, and its Job is retried later. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

**Дія 3:** у моделі загроз додай рядок після `Two synchronization passes of one Source run at once`:

У `docs/threat-model.md` знайди:

```markdown
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |
| Two synchronization passes of one Source run at once: double Source calls, and an older pass finishing late re-grants a removed reader, rolls content back, or tombstones what a newer pass saw | `synchronize_source_exclusively` holds the Source's Redis lease, with an owner token, renewed after every page, so a second pass gets `SourceBusy` before reading; a crashed holder's lease expires in two minutes. The Source's fence in PostgreSQL decides who may write: every pass claims it with its observation time, and every narrowing, reconciliation, and tombstone checks it under a share lock on the Source's row, so after a newer pass claims it nothing of an older pass lands; an observation more than five minutes in the future is refused | Writes an older pass committed before the newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way; a pass observed up to five minutes in the future holds the fence until then |

### Retrieval
```

і заміни на:

```markdown
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |
| Two synchronization passes of one Source run at once: double Source calls, and an older pass finishing late re-grants a removed reader, rolls content back, or tombstones what a newer pass saw | `synchronize_source_exclusively` holds the Source's Redis lease, with an owner token, renewed after every page, so a second pass gets `SourceBusy` before reading; a crashed holder's lease expires in two minutes. The Source's fence in PostgreSQL decides who may write: every pass claims it with its observation time, and every narrowing, reconciliation, and tombstone checks it under a share lock on the Source's row, so after a newer pass claims it nothing of an older pass lands; an observation more than five minutes in the future is refused | Writes an older pass committed before the newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way; a pass observed up to five minutes in the future holds the fence until then |
| A scheduled synchronization Job carries a Source's location or credentials through Redis, or the scheduler floods the queue | A scheduled Job stores only the Source's ID, and its handler reads the configuration and credentials reference from PostgreSQL when it runs; a Source with an open Job is skipped, a missed schedule queues one Job, and a pass queues at most 1,000 | Disabling a Source does not cancel a Job already queued for it; a Source whose synchronization Job is stuck open is not scheduled again until M08-T08 |

### Retrieval
```

**Перевірка:** `make docs-check` завершується з кодом 0.

### Крок 8 — У цьому кроці коду немає: перевірки, докази і прогрес

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

**Дія 2:** заверши файл доказів: результати перевірок і кількість із кроку 6 у «Verification», знахідки рев'ю й відомі обмеження в «Review», дизайн у «Design» і «Decision», а також свої відповіді на питання для рефлексії нижче. Потім познач завдання виконаним у `docs/curriculum/PROGRESS.md` і зроби коміт з `m08-t06: schedule incremental synchronization runs`.

**Перевірка:** усі перевірки проходять, а `git status` чистий після коміту.

## Питання для рефлексії

- Останній запуск Handbook поставили в чергу о 08:00, і він завершився о 10:30. За погодинного інтервалу коли йому знову настане час і чому не о 09:00?
- Relay не працював з 00:00 до 10:00. Скільки Job синхронізації для Handbook поставить перший прохід о 10:00, і що не дає йому поставити десять?
- Чому Job несе лише ID джерела? Назви одну проблему, яку спричинило б посилання на облікові дані в Job, і одну, яку спричинила б адреса.
- 150 джерелам настав час, а прохід може поставити 100. Які 50 чекатимуть і що гарантує, що наступного разу вони будуть першими?
- Що ти побачив би в таблиці `jobs`, щоб зрозуміти, що розклад не відсіює зайвих Job?

## Чекліст завершення

- [ ] Файл доказів містить інваріант, власників і твої прогнози, записані до змін.
- [ ] Міграція `b8c9d0e1f2a3` додає `ix_jobs_synchronization_target`; `load_due_source_ids` і `lock_enabled_sources` існують.
- [ ] `schedule_due_synchronizations` ставить у чергу один Job на кожне увімкнене джерело, якому настав час, лише з ID джерела; `tests/test_scheduling.py` (12) проходить.
- [ ] `make worker-relay` виконує повернення повторів, розклад і надсилання, а збій розкладу не зупиняє надсилання.
- [ ] `tests/test_scheduling_integration.py` (22) проходить: настав час, вимкнене, вже йде, пропущене, голодування, годинник, зміна посеред проходу.
- [ ] Ти зламав код навмисно, побачив, як падають три тести інтервалу, і повернув усе назад.
- [ ] `docs/architecture.md`, `docs/operations.md` і `docs/threat-model.md` описують розклад.
- [ ] Усі перевірки проходять: формат, lint, типи, unit, integration, документація.
- [ ] Докази, статус завдання, рядок у PROGRESS і коміт зроблено.

Коли напишеш файли, поділись ними або виводом помилок, і я перевірю їх за цим чеклістом.
