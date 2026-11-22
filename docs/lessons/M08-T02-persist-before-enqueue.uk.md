English version: [M08-T02-persist-before-enqueue.md](M08-T02-persist-before-enqueue.md)

# M08-T02 — Спершу зберегти, потім поставити в чергу

Завдання: [M08-T02 — Persist before enqueue](../curriculum/milestones/M08-jobs-and-confluence/M08-T02-persist-before-enqueue.md)

## Що ти зробиш

Цей урок з'єднує збережені Job із чергою з M08-T01 так, щоб жоден Job не загубився дорогою. Спершу Job зберігається в PostgreSQL, і лише потім у Redis іде повідомлення. Якщо надіслати не вдалося або процес зупинився в невдалий момент, окремий прохід знаходить Job, про які нікому не повідомили, і надсилає їх. Воркер потім виконує кожен Job щонайбільше один раз, навіть коли про нього прийшло два повідомлення.

- **Спершу зберегти.** Job комітиться в PostgreSQL до того, як про нього з'явиться хоч одне повідомлення.
- **Записати надсилання.** Нова колонка каже, коли повідомлення про Job передали в Redis.
- **Полагодити пропущене.** Прохід-ретранслятор (relay) надсилає кожен збережений Job, про який ще немає повідомлення.
- **Повідомлення несе лише ID.** Що робити і з яким документом, воркер читає з бази.
- **Один ефект на Job.** Воркер виконує Job, лише поки той чекає в черзі, тож друге повідомлення нічого не змінює.
- **Доказ:** тести змушують чергу відмовити або «падають» між надсиланням і записом, а потім перевіряють, що relay це лагодить і ефект Job стається один раз.

| Що просить завдання | Де це зроблено |
|---|---|
| Надійні ідентичність і намір Job у PostgreSQL до надсилання | Крок 4: `submit_job` комітить, потім надсилає |
| Відновлення збереженої, але не надісланої роботи | Крок 2: колонка `published_at`; Крок 4: `publish_pending_jobs`; Крок 6: `make worker-relay` |
| Збій між комітом і надсиланням виявляється і лагодиться | Крок 8: відмова черги і падіння після надсилання, обидва лагодить relay; Крок 10: Redis зупинено по-справжньому |
| Без повторних ефектів | Крок 5: воркер виконує лише Job у черзі; Кроки 8 і 9: повторні й одночасні повідомлення виконують очищення один раз |
| Успіх, межа, типізована помилка або відмова | Кроки 7 і 8: надісланий Job; ліміт і порядок relay; `PublishFailed`, `UnsupportedJobKind`, `InvalidJobMessage` |
| Справжня інфраструктура там, де перевіряється її поведінка | Крок 8: справжні блокування й коміти PostgreSQL; Крок 9: справжній Redis і справжній воркер Dramatiq |
| Оновити лише ту документацію, якої це стосується | Крок 12: `docs/architecture.md`, `docs/threat-model.md` |

У цьому уроці є код: кроки 2–9 пишуть код, міграцію і тести. Кроки 1, 10, 11 і 12 коду не потребують, хоча на кроці 12 ти редагуєш два документи.

Терміни цього уроку:

- **Спершу зберегти, потім поставити в чергу (persist before enqueue):** спочатку записати роботу в базу, а вже потім оголосити про неї. Як записати замовлення в журнал кафе, перш ніж гукнути його на кухню: якщо кухня не почула, замовлення лишилося в журналі.
- **Надіслати (publish):** передати в Redis одне повідомлення, яке називає Job.
- **Ретранслятор (relay):** прохід, який шукає в базі Job без повідомлення і надсилає їх.
- **Блокування рядка (row lock):** поки одна транзакція тримає блокування рядка, інші, хто просить те саме блокування, чекають. `SKIP LOCKED` каже їм не чекати, а пропустити цей рядок.
- **Повторний ефект (duplicate effect):** та сама робота, зроблена двічі: двічі видалити ті самі фрагменти, двічі надіслати той самий лист.
- **Мертвий лист (dead letter):** повідомлення, яке воркер відхилив або на якому впав. Dramatiq відкладає його вбік і більше не виконує.

## Перед початком

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілим, коли до нього дійдеш.

- **Що вже є.** Таблиця `jobs` і значення `Job` існують з M02 і M03. Job очищення вже зберігається в тій самій транзакції, що позначає документ видаленим, у `tombstone_missing`. Але ніщо не надсилає його в чергу і ніщо його не виконує. Цей урок закриває цю діру.
- **Чому не надсилати спершу.** Якби повідомлення пішло в Redis до коміту, воркер міг би його взяти і не знайти Job у базі. Гірше: якби коміт потім упав, існувало б повідомлення про роботу, якої ніколи не було.
- **Чому не надсилати всередині транзакції.** Повідомлення, надіслане до коміту, має ту саму проблему. Тому код спершу комітить, а надсилає вже в другій, короткій транзакції.
- **Надсилання записується.** Нова колонка `published_at` порожня, доки повідомлення не передали в Redis. Relay дивиться лише на Job у черзі з порожньою `published_at`.
- **Розрив лагодять, а не запобігають йому.** Процес може зупинитися після надсилання, але до запису. Тоді relay надішле друге повідомлення про той самий Job. Це нормально: воркер ігнорує Job, який уже не в черзі. Ми погоджуємося на два повідомлення, щоб ніколи не мати нуля.
- **Один відправник на Job одночасно.** Надсилання блокує рядок Job з `SKIP LOCKED`. Два relay одночасно ніколи не надішлють той самий Job в ту саму мить: другий просто пропускає заблокований рядок.
- **Повідомлення несе лише ID Job.** Тип і ціль воркер читає з PostgreSQL. Тож повідомлення не може принести вказівок, яких немає в базі, а воркер перевіряє написання ID, перш ніж ним користуватися.
- **Лише ті типи, які вміє виконувати воркер.** Зараз обробник є лише в `purge_document_chunks`. Очищення розмов і Job синхронізації лишаються ненадісланими, доки наступний урок не дасть їм обробника. `submit_job` відхиляє їх з `UnsupportedJobKind`.
- **Повторена спроба потребує нового повідомлення.** Коли невдалий Job знову ставлять у чергу, збереження очищає `published_at`, тож relay надішле нову спробу.
- **Окремий цикл подій на кожне повідомлення.** Dramatiq запускає акторів у потоках, а рушій бази належить одному циклу подій (event loop). Тому воркер відкриває новий цикл і рушій на кожне повідомлення і закриває їх після.
- **`submit_job` поки ніхто не викликає.** Маршрути, що надсилають Job, додасть M09-T03. Зараз Job з'являються з інших процесів, наприклад з позначення документів видаленими, і доходять до Redis через relay.
- **Що знайшло рев'ю.** Незалежне адверсаріальне рев'ю атакувало першу версію:
  - Відправник вважав очищення розмов такими, що їх можна виконати, бо вони йдуть тією ж чергою `maintenance`. Їх надсилали, вони ставали мертвими листами і застрягали назавжди. Тепер відправник враховує рівно ті типи, які виконує воркер, а три тести падають на першій версії.
  - Ще воно влаштувало шторм з 80 Job, трьох одночасних relay, штучних падінь і відмов, і кожне повідомлення доставлялося двічі. Кожен Job виконався рівно один раз.
- **Відомі обмеження.** Ці речі поки лишаються як є:
  - Повідомлення, втрачене вже після надсилання, лишається втраченим: перезапуск Redis, мертвий лист до того, як воркер узяв Job, або повідомлення, старше за добу. Job лишається в черзі й позначений надісланим, і relay його пропускає. Пошук таких застряглих Job — це M08-T08.
  - Поки Redis відповідає (до 5 секунд), надсилання тримає блокування рядка Job і одне з'єднання з базою. Якщо Redis завис, а надсилань багато одночасно, Dramatiq ставить їх у чергу одне за одним, і з'єднань з базою може забракнути. Поки ніхто не надсилає Job паралельно; до цього треба повернутися в M09-T03.
  - Повторне повідомлення для спроби 1 може запустити спробу 2 одразу, як збережено повтор, ще до того, як для спроби 2 надіслано власне повідомлення. Це все одно один ефект на спробу; час повторів визначає M08-T04.

## Покрокова інструкція

Перед початком запусти PostgreSQL і Redis (`make redis-up`). Усі команди виконуються з кореня репозиторію. Для integration-перевірок задай тестові URL у своєму терміналі:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
export KNOWLEDGE_SERVICE_TEST_REDIS_URL="redis://127.0.0.1:6380/15"
```

### Крок 1 — У цьому кроці коду немає: запиши інваріант і свої прогнози

Перш ніж щось змінювати, запиши, що має бути правдою наприкінці уроку і чого ти очікуєш. Останній прогноз перевіриш на кроці 11.

Створи `docs/evidence/M08-T02-persist-before-enqueue.md`:

```markdown
# M08-T02 — Persist before enqueue evidence

## Invariant

No message names a Job before the Job is committed to PostgreSQL. Every
queued Job of a kind a worker runs is either named by a message whose send
is recorded in `published_at`, or found and sent by the relay. A message
carries only the Job's ID, and a worker runs a Job only while it is still
queued, so duplicate or concurrent messages have the effect of one.

## Owners

- Durable truth: the `jobs` row in PostgreSQL; Redis only delivers.
- Publishing: `submit_job` once, then `publish_pending_jobs` (the relay).
- Running once: the handler's claim of a queued Job, in one transaction.
- Retries: the stored Job (M08-T04); Dramatiq retries nothing.
- Authorization: none yet; nothing outside the service submits Jobs.

## Expected failing check

Before the change,
`uv run pytest tests/test_job_dispatch_integration.py -q -m integration`
fails with
`ERROR: file or directory not found: tests/test_job_dispatch_integration.py`.

## Predictions

- What the relay prints when Redis is stopped and one Job waits:
- How many messages the relay sends for a Job whose first send was refused:
- What the worker does with a second message for a Job that already succeeded:
- How many tests fail if saving a queued Job stops clearing `published_at`:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після двокрапки в кожному прогнозі стоїть твоя відповідь.

### Крок 2 — Крок з кодом: записувати, коли Job надіслано

На цьому кроці в таблиці `jobs` з'являються одна колонка й один індекс.

**Простими словами:** у кожного збереженого Job з'являється місце, де записано, коли повідомлення про нього передали в Redis. Поки це місце порожнє, а Job чекає, йому ще потрібне повідомлення. Невеликий індекс дає relay швидко знаходити такі Job, починаючи з найстаріших.

| Частина | Що містить або робить |
|---|---|
| `published_at` | час, коли повідомлення для поточної спроби в черзі передали в Redis; порожньо означає «ще не надіслано» |
| `ix_jobs_unpublished_queued_at` | індекс лише по Job у черзі, які ще не надіслані, упорядкований за часом постановки |
| Міграція `c2d3e4f5a6b7` | додає обидва; кожен наявний Job стартує ненадісланим |

Індекс **частковий** (partial): він тримає лише рядки, що відповідають його умові. Більшість Job завершуються і з нього виходять, тож він лишається маленьким, скільки б Job не накопичилося.

**Дія 1:** створи `migrations/versions/c2d3e4f5a6b7_job_publication.py`:

```python
"""job publication

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-11-21 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"


def upgrade() -> None:
    # Every existing row starts unpublished: no message has ever named it.
    op.add_column(
        "jobs",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_jobs_unpublished_queued_at",
        "jobs",
        ["queued_at", "job_id"],
        postgresql_where=sa.text("status = 'queued' and published_at is null"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_unpublished_queued_at", table_name="jobs")
    op.drop_column("jobs", "published_at")
```

**Дія 2:** у `src/knowledge_service/persistence/schema.py`, у таблиці `jobs`, додай колонку після `last_error_code`:

```python
    # When a message naming this queued attempt was handed to the queue.
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
```

і додай індекс останнім елементом таблиці, після перевірки `ck_jobs_last_error_code`:

```python
    sa.Index(
        "ix_jobs_unpublished_queued_at",
        "queued_at",
        "job_id",
        postgresql_where=sa.text("status = 'queued' and published_at is null"),
    ),
```

**Дія 3:** `tests/test_indexes.py` перелічує всі індекси, які дозволені в проєкті. Додай нову назву в `ACCESS_PATTERN_INDEXES`:

```python
    "ix_jobs_unpublished_queued_at",
```

**Перевірка:** `uv run pytest tests/test_indexes.py -q` проходить, і `uv run pytest tests/test_migrations_integration.py -q -m integration` проходить, а це доводить, що міграція й оголошена таблиця збігаються.

### Крок 3 — Крок з кодом: знайти, заблокувати й позначити ненадіслані Job

На цьому кроці з'являються функції бази, потрібні relay і відправнику.

**Простими словами:** одна функція перелічує Job, що чекають і ще не мають повідомлення, від найстаріших. Друга блокує один такий Job або каже, що його вже немає чи він зайнятий. Третя записує момент надсилання. Збереження Job, який знову поставили в чергу на нову спробу, тепер ще й очищає цей запис, тож нова спроба отримає власне повідомлення.

| Функція | Що робить |
|---|---|
| `load_unpublished_job_ids` | ID Job у черзі, ще не надісланих, заданих типів, від найстаріших, до ліміту |
| `lock_unpublished_job` | блокує один Job у черзі, ще не надісланий, з `SKIP LOCKED`; `None`, коли він надісланий, завершений, відсутній або заблокований кимось іншим |
| `mark_job_published` | записує час надсилання в `published_at` |
| `save_job` | без змін, окрім того, що збереження Job у черзі очищає `published_at` |

`lock_unpublished_job` перевіряє «досі в черзі й не надісланий» у тому самому запиті, що блокує. Тож якщо інший відправник щойно надіслав Job, рядок уже не відповідає умові, і функція повертає `None`.

**Дія:** заміни вміст `src/knowledge_service/persistence/jobs.py` на:

```python
"""Store queued Jobs and whether a message has named them."""

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.identifiers import JobId
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence.schema import jobs


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


async def enqueue_job(connection: AsyncConnection, job: Job) -> JobId:
    """Store a queued Job."""
    await connection.execute(sa.insert(jobs).values(_job_row(job)))
    return job.job_id


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

**Перевірка:** `uv run pyright src/knowledge_service/persistence/jobs.py` показує 0 помилок, а `uv run pytest tests/test_deletions_integration.py -q -m integration` і далі проходить.

### Крок 4 — Крок з кодом: надіслати і ретранслювати

На цьому кроці ти пишеш процес, який зберігає Job, надсилає його і лагодить пропущене.

**Простими словами:** надсилання Job спершу зберігає й комітить його. Потім, у другій короткій транзакції, Job блокується, повідомлення йде в Redis, і надсилання записується. Якщо Redis відмовив, Job просто лишається ненадісланим. Relay згодом проходить по ненадісланих Job від найстаріших і робить те саме для кожного. На першій відмові він зупиняється, щоб не бомбити Redis, який лежить.

| Частина | Що робить |
|---|---|
| `JobPublisher` | порт: які типи він уміє надсилати, і `publish` одного повідомлення або `PublishFailed` |
| `publish_job` | заблокувати, надіслати, записати — в одній транзакції; повертає `published`, `skipped` або `failed` |
| `submit_job` | відхиляє не новий Job або тип, якого не виконує жоден воркер; комітить Job; один раз пробує `publish_job`; повертає `Submission` |
| `publish_pending_jobs` | один прохід relay: до `limit` ненадісланих Job, від найстаріших, із зупинкою на першій відмові; повертає `RelayReport` |

Тут важливі три рішення:

- **Надсилання йде в окремому потоці.** Клієнт Redis синхронний. `asyncio.to_thread` не блокує цикл подій, поки Redis відповідає, а власні таймаути Redis обмежують очікування.
- **Після коміту `submit_job` ніколи не кидає помилку через проблему з надсиланням.** Job уже надійно збережений, і relay його надішле. У лог іде лише тип помилки, бо текст помилки може процитувати URL з паролем.
- **Скасоване надсилання безпечне.** Якщо викликач скасує задачу посеред надсилання, транзакція відкотиться, і Job лишиться ненадісланим. Relay надішле його пізніше, тож у гіршому разі воркер отримає два повідомлення.

**Дія:** створи `src/knowledge_service/job_dispatch.py`:

```python
"""Store a Job before any message names it, then publish it.

Submitting a Job commits it to PostgreSQL first. Only then is a message sent,
under a lock on the Job's row, and the send is recorded in `published_at`.
If the queue refuses the message, or the process stops before the send is
recorded, the Job stays queued and unpublished, and `publish_pending_jobs`
sends it later. One Job can therefore be named by two messages; a worker runs
a Job only while it is still queued, so the second message changes nothing.

Redis is never the record: PostgreSQL says what was asked for (ADR 0011).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final, Protocol

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence.jobs import (
    enqueue_job,
    load_unpublished_job_ids,
    lock_unpublished_job,
    mark_job_published,
)

logger = logging.getLogger(__name__)

MAX_RELAY_BATCH: Final = 1_000


class PublishFailed(RuntimeError):
    """The queue did not accept the message; the Job stays unpublished."""


class UnsupportedJobKind(InvalidJob):
    """No worker runs Jobs of this kind, so none may be submitted."""


class JobPublisher(Protocol):
    """Hands one message naming a Job to the queue."""

    @property
    def kinds(self) -> frozenset[JobKind]:
        """The Job kinds a worker can run."""
        ...

    def publish(self, job: Job) -> None:
        """Send one message naming `job`, or raise `PublishFailed`."""
        ...


class PublishOutcome(StrEnum):
    """What one attempt to publish a Job did."""

    PUBLISHED = "published"
    # Already published, no longer queued, not a kind any worker runs, or
    # being published by someone else right now.
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Submission:
    """A stored Job, and whether its message was sent yet."""

    job_id: JobId
    published: bool


@dataclass(frozen=True, slots=True)
class RelayReport:
    """What one pass over unpublished Jobs did."""

    published: int
    skipped: int
    failed: int
    # Found in this pass but not tried, because the queue had just failed.
    untried: int


def _require_utc(now: datetime) -> None:
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")


async def publish_job(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    publisher: JobPublisher,
    now: datetime,
) -> PublishOutcome:
    """Send one message for a queued, unpublished Job and record the send.

    The row stays locked while the message is sent, so no other publisher
    sends it at the same time. A send that fails leaves the Job unpublished.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        job = await lock_unpublished_job(connection, job_id=job_id)
        if job is None or job.kind not in publisher.kinds:
            return PublishOutcome.SKIPPED
        try:
            # The Redis client is synchronous and bounded by its own timeouts.
            await asyncio.to_thread(publisher.publish, job)
        except PublishFailed:
            return PublishOutcome.FAILED
        await mark_job_published(connection, job_id=job_id, published_at=now)
    return PublishOutcome.PUBLISHED


async def submit_job(
    runtime: DatabaseRuntime,
    job: Job,
    *,
    publisher: JobPublisher,
    now: datetime,
) -> Submission:
    """Store a new queued Job, then try once to publish it.

    Once this returns, the Job is stored, whether or not it was published.
    An unpublished Job is sent by a later `publish_pending_jobs`.
    """
    _require_utc(now)
    if job.status is not JobStatus.QUEUED or job.attempt != 0:
        raise InvalidJob("only a new queued job can be submitted")
    if job.kind not in publisher.kinds:
        raise UnsupportedJobKind(f"no worker runs {job.kind} jobs")
    async with runtime.transaction() as connection:
        await enqueue_job(connection, job)
    try:
        outcome = await publish_job(
            runtime, job_id=job.job_id, publisher=publisher, now=now
        )
    except Exception as error:
        # The Job is committed; the relay will publish it. Only the type is
        # logged, because a message could quote a URL or a secret.
        logger.warning(
            "job %s stored but not published: %s",
            job.job_id.value,
            type(error).__name__,
        )
        return Submission(job_id=job.job_id, published=False)
    return Submission(job_id=job.job_id, published=outcome is PublishOutcome.PUBLISHED)


async def publish_pending_jobs(
    runtime: DatabaseRuntime,
    *,
    publisher: JobPublisher,
    now: datetime,
    limit: int = 100,
) -> RelayReport:
    """Publish queued Jobs that no message names yet, oldest first.

    This repairs a Job stored by a process that stopped, or whose queue
    refused the message, before its send was recorded. The pass stops at the
    first failed send, so an unavailable queue is not hit once per Job.
    """
    _require_utc(now)
    if type(limit) is not int or not 1 <= limit <= MAX_RELAY_BATCH:
        raise ValueError(f"limit must be an integer from 1 to {MAX_RELAY_BATCH}")
    async with runtime.transaction() as connection:
        job_ids = await load_unpublished_job_ids(
            connection, kinds=publisher.kinds, limit=limit
        )
    counts = dict.fromkeys(PublishOutcome, 0)
    for index, job_id in enumerate(job_ids):
        outcome = await publish_job(
            runtime, job_id=job_id, publisher=publisher, now=now
        )
        counts[outcome] += 1
        if outcome is PublishOutcome.FAILED:
            return RelayReport(
                published=counts[PublishOutcome.PUBLISHED],
                skipped=counts[PublishOutcome.SKIPPED],
                failed=1,
                untried=len(job_ids) - index - 1,
            )
    return RelayReport(
        published=counts[PublishOutcome.PUBLISHED],
        skipped=counts[PublishOutcome.SKIPPED],
        failed=0,
        untried=0,
    )
```

**Перевірка:** `uv run pyright src/knowledge_service/job_dispatch.py` показує 0 помилок.

### Крок 5 — Крок з кодом: бік воркера для повідомлень про Job

На цьому кроці воркер учиться виконувати збережений Job за повідомленням, яке його називає.

**Простими словами:** на кожну чергу, що несе Job, які воркер уміє виконувати, є один актор. Його повідомлення містить лише ID Job, записаний стандартним способом. Воркер перевіряє це написання, читає Job з бази і виконує його, лише якщо той досі чекає. Якщо Job уже виконався через попереднє повідомлення, воркер пише це в лог і більше нічого не робить. Відправник використовує тих самих акторів, тож кожен Job іде в чергу свого типу.

| Частина | Що робить |
|---|---|
| `QUEUE_FOR_KIND` | у якій черзі їде кожен тип Job |
| `HANDLERS` | Job, які воркер уміє виконувати: зараз лише `purge_document_chunks` |
| `parse_job_id` | приймає лише `str` у канонічному вигляді UUID; на все інше кидає `InvalidJobMessage` |
| `JobActors` / `declare_job_actors` | по одному актору `run_<queue>_job` на кожну чергу заданих типів, з лімітом 60 секунд і без повторів; `kinds` — рівно ті типи, для яких їх оголошено |
| `DramatiqJobPublisher` | надсилає ID Job у його чергу; тип, якого не виконує жоден воркер, або помилка брокера дає `PublishFailed` |
| `run_stored_job` | читає Job; ігнорує його, якщо він не в черзі; запускає його обробник; програш захоплення іншому повідомленню вважає дублем |
| `JobRunner` | те, що викликає актор: новий цикл подій і рушій бази на кожне повідомлення |

Перевірка на дубль робиться двічі навмисно. Перша дешева і відсіює більшість дублів. Друга — це захоплення Job обробником, яке блокує рядок. Два повідомлення, що прийшли разом, обидва проходять першу перевірку, але захоплення виграє лише одне.

**Дія 1:** створи `src/knowledge_service/worker/jobs.py`:

```python
"""Messages that name stored Jobs, and the worker side that runs them.

A message carries only a Job's ID. What to do, and to which target, is read
from the Job's row in PostgreSQL, so a message never holds intent that the
database does not.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import dramatiq
import redis
from dramatiq.errors import BrokerError

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.ingestion.deletions import purge_document_chunks
from knowledge_service.job_dispatch import PublishFailed
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence.jobs import load_job
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import QueueName, WorkerConfigurationError

logger = logging.getLogger(__name__)

JOB_TIME_LIMIT_MS: Final = 60_000

# The queue each kind of Job travels on.
QUEUE_FOR_KIND: Final[Mapping[JobKind, QueueName]] = {
    JobKind.SYNCHRONIZE_SOURCE: QueueName.SYNCHRONIZATION,
    JobKind.PURGE_CONVERSATION: QueueName.MAINTENANCE,
    JobKind.PURGE_DOCUMENT_CHUNKS: QueueName.MAINTENANCE,
}

type JobHandler = Callable[..., Awaitable[Job]]

# The Jobs a worker can run today. Each handler claims a queued Job in its
# own transaction and refuses one that is no longer queued.
HANDLERS: Final[Mapping[JobKind, JobHandler]] = {
    JobKind.PURGE_DOCUMENT_CHUNKS: purge_document_chunks,
}


class InvalidJobMessage(ValueError):
    """A message does not name a stored Job a worker can run."""


def parse_job_id(value: object) -> JobId:
    """The Job ID a message carries, written as a canonical UUID string."""
    if type(value) is not str:
        raise InvalidJobMessage("a job message carries one job ID string")
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise InvalidJobMessage("a job message carries a malformed job ID") from None
    if str(parsed) != value:
        raise InvalidJobMessage("a job message carries a malformed job ID")
    return JobId(parsed)


@dataclass(frozen=True, slots=True)
class JobActors:
    """The Job kinds a worker runs, and one actor per queue they travel on.

    `kinds` is exactly what the actors were declared for. Another kind that
    shares a queue is not included: no worker runs it.
    """

    kinds: frozenset[JobKind]
    by_queue: Mapping[QueueName, dramatiq.Actor[[str], None]]


def declare_job_actors(
    broker: dramatiq.Broker,
    *,
    run_job: Callable[[JobId], None],
    kinds: frozenset[JobKind],
) -> JobActors:
    """Declare a `run_<queue>_job` actor for each queue these kinds use."""

    def run(job_id: str) -> None:
        # The message came from Redis, so its argument is checked here.
        run_job(parse_job_id(job_id))

    by_queue: dict[QueueName, dramatiq.Actor[[str], None]] = {}
    for queue in sorted({QUEUE_FOR_KIND[kind] for kind in kinds}):
        declare = dramatiq.actor(
            broker=broker,
            actor_name=f"run_{queue}_job",
            queue_name=queue,
            time_limit=JOB_TIME_LIMIT_MS,
            max_retries=0,
        )
        by_queue[queue] = declare(run)
    return JobActors(kinds=kinds, by_queue=by_queue)


@dataclass(frozen=True, slots=True)
class DramatiqJobPublisher:
    """Publishes a Job as a message on its kind's queue."""

    actors: JobActors

    @property
    def kinds(self) -> frozenset[JobKind]:
        return self.actors.kinds

    def publish(self, job: Job) -> None:
        if job.kind not in self.actors.kinds:
            raise PublishFailed(f"no worker runs {job.kind} jobs")
        actor = self.actors.by_queue[QUEUE_FOR_KIND[job.kind]]
        try:
            actor.send(str(job.job_id.value))
        except (BrokerError, redis.RedisError, OSError) as error:
            raise PublishFailed("the queue did not accept the message") from error


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


@dataclass(frozen=True, slots=True)
class JobRunner:
    """Runs one Job per message, with a database connection of its own."""

    settings: Settings

    def __post_init__(self) -> None:
        if self.settings.database_url is None:
            raise WorkerConfigurationError(
                "KNOWLEDGE_SERVICE_DATABASE_URL is required to run jobs"
            )

    def __call__(self, job_id: JobId) -> None:
        asyncio.run(self._run(job_id))

    async def _run(self, job_id: JobId) -> None:
        # An engine belongs to one event loop, and each message gets its own.
        runtime = create_database_runtime(self.settings)
        try:
            await run_stored_job(runtime, job_id, now=datetime.now(UTC))
        finally:
            await runtime.dispose()
```

**Дія 2:** заміни вміст `src/knowledge_service/worker/main.py` на:

```python
"""The worker process: `dramatiq knowledge_service.worker.main`.

Importing this module builds the broker from settings, makes it Dramatiq's
global broker, and declares every actor on it. Dramatiq then reads the
declared queues.
"""

import dramatiq

from knowledge_service.settings import Settings
from knowledge_service.worker.actors import declare_actors, log_ping
from knowledge_service.worker.broker import broker_from_settings
from knowledge_service.worker.jobs import HANDLERS, JobRunner, declare_job_actors

settings = Settings()
broker = broker_from_settings(settings)
dramatiq.set_broker(broker)
actors = declare_actors(broker, record_ping=log_ping)
job_actors = declare_job_actors(
    broker, run_job=JobRunner(settings), kinds=frozenset(HANDLERS)
)
```

**Дія 3:** тепер воркеру ще потрібен URL бази. У `tests/test_worker.py` заміни `test_worker_entry_point_declares_ping_on_its_global_broker` на:

```python
def test_worker_entry_point_declares_ping_on_its_global_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "KNOWLEDGE_SERVICE_REDIS_URL", f"redis://127.0.0.1:{closed_port()}/0"
    )
    monkeypatch.setenv(
        "KNOWLEDGE_SERVICE_DATABASE_URL",
        f"postgresql+psycopg://user:pass@127.0.0.1:{closed_port()}/db",
    )
    monkeypatch.delitem(sys.modules, "knowledge_service.worker.main", raising=False)

    entry = importlib.import_module("knowledge_service.worker.main")
    try:
        assert dramatiq.get_broker() is entry.broker
        assert entry.broker.get_declared_actors() == {"ping", "run_maintenance_job"}
    finally:
        entry.broker.close()
        dramatiq.set_broker(StubBroker())
```

**Перевірка:** `uv run pytest tests/test_worker.py -q` показує `40 passed`.

### Крок 6 — Крок з кодом: команда relay і цілі Make

На цьому кроці з'являється команда, що виконує один прохід relay, і воркер отримує доступ до PostgreSQL.

**Простими словами:** команда relay підключається до Redis і бази, надсилає кожен Job, що чекає без повідомлення, друкує, що зробила, і завершується. Якщо Redis відмовив, вона виходить з кодом 1, тож планувальник це помітить. Воркер і relay тепер обидва отримують URL бази; якщо ти його не задав, він будується з локального контейнера PostgreSQL під час запуску команди.

**Дія 1:** створи `src/knowledge_service/worker/relay.py`:

```python
"""Publish stored Jobs that no message names yet: one pass, then exit.

Run `python -m knowledge_service.worker.relay` after a crash or a Redis
outage, or on a schedule. It exits 0 when every Job it found was published
or skipped, and 1 when the queue refused a message; the rest wait for the
next pass.
"""

import asyncio
import sys
from datetime import UTC, datetime

from knowledge_service.database import create_database_runtime
from knowledge_service.job_dispatch import (
    JobPublisher,
    RelayReport,
    publish_pending_jobs,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import broker_from_settings
from knowledge_service.worker.jobs import (
    HANDLERS,
    DramatiqJobPublisher,
    JobRunner,
    declare_job_actors,
)


async def _relay(settings: Settings, publisher: JobPublisher) -> RelayReport:
    runtime = create_database_runtime(settings)
    try:
        return await publish_pending_jobs(
            runtime, publisher=publisher, now=datetime.now(UTC)
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
        report = asyncio.run(_relay(settings, DramatiqJobPublisher(actors)))
    finally:
        broker.close()
    sys.stdout.write(
        f"published {report.published}, skipped {report.skipped}, "
        f"failed {report.failed}, untried {report.untried}\n"
    )
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

**Дія 2:** у `Makefile` додай `worker-relay` у рядок `.PHONY`:

```makefile
.PHONY: do_good setup format format-check lint typecheck test-unit test-integration ml-check keycloak-up keycloak-down keycloak-reset redis-up redis-down worker worker-ready worker-ping worker-relay check hooks-install hooks docs-check
```

**Дія 3:** одразу після рядка `WORKER_ENV = ...` додай:

```makefile
# Running Jobs and relaying them reads PostgreSQL too. When
# KNOWLEDGE_SERVICE_DATABASE_URL is unset, the URL is built from the local
# container at run time, so its password never lands in a file.
WORKER_DB_ENV = KNOWLEDGE_SERVICE_DATABASE_URL="$${KNOWLEDGE_SERVICE_DATABASE_URL:-$$(uv run python scripts/test_database_url.py $(TEST_DB_CONTAINER))}"
```

**Дія 4:** у рецепті `worker` передай Dramatiq і URL бази. Другий рядок рецепта стає таким:

```makefile
	@$(WORKER_ENV) $(WORKER_DB_ENV) .venv/bin/dramatiq knowledge_service.worker.main --processes 1 --threads 2
```

**Дія 5:** після цілі `worker-ping` додай:

```makefile
worker-relay: ## Publish stored Jobs that no message names yet (one pass)
	@$(WORKER_ENV) $(WORKER_DB_ENV) uv run python -m knowledge_service.worker.relay
```

**Перевірка:** `make` показує `worker-relay` у своєму меню, а `uv run pyright src/knowledge_service/worker` показує 0 помилок. По-справжньому relay запуститься на кроці 10.

### Крок 7 — Крок з кодом: unit-тести повідомлень про Job

Цей крок перевіряє формат повідомлення і відправника без PostgreSQL, на брокері Dramatiq у пам'яті.

**Простими словами:** тести надсилають Job через відправника і дивляться на сире повідомлення: у ньому лише ID Job і більше нічого. Вони перевіряють, що актори є лише для черг тих типів, які можна виконати, а типи, яких не виконує жоден воркер, відхиляються. Вони наводять відправника на закритий порт і перевіряють, що він швидко падає з типізованою помилкою. Потім вони дають воркеру повідомлення з усіма неправильними формами ID і перевіряють, що жодне не дійшло до виконавця.

| Тест | Що доводить |
|---|---|
| `test_a_published_job_reaches_the_worker_as_its_id_only` | повідомлення несе один канонічний ID; воркер передає його виконавцю |
| `test_actors_exist_only_for_the_queues_of_runnable_kinds` | один актор, ліміт 60 секунд, без повторів; очищення розмов не враховуються |
| `test_a_kind_no_worker_runs_is_refused_as_a_failed_publish` | синхронізація й очищення розмов дають `PublishFailed`; у черзі нічого |
| `test_an_unreachable_queue_is_a_failed_publish_within_its_timeouts` | закритий порт дає `PublishFailed` швидше ніж за п'ять секунд |
| `test_the_worker_refuses_a_message_that_names_no_canonical_job_id` | вісім ворожих форм: `InvalidJobMessage`, один мертвий лист, виконавець не викликається |
| `test_parse_job_id_accepts_the_canonical_spelling`, `test_a_job_runner_requires_a_database_url` | прийнятна форма і `WorkerConfigurationError` |

**Дія:** створи `tests/test_job_messages.py`:

```python
"""Tests for job messages: what they carry, how they are sent and refused."""

import json
import socket
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import dramatiq
import pytest
from dramatiq.brokers.stub import StubBroker

from knowledge_service.identifiers import JobId
from knowledge_service.job_dispatch import PublishFailed
from knowledge_service.jobs import Job, JobKind
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import (
    QueueName,
    WorkerConfigurationError,
    build_broker,
    declare_queues,
    queue_middleware,
)
from knowledge_service.worker.jobs import (
    DramatiqJobPublisher,
    InvalidJobMessage,
    JobActors,
    JobRunner,
    declare_job_actors,
    parse_job_id,
)

QUEUED_AT = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)
JOB_ID = JobId(uuid.UUID("0b8c2d5e-4f6a-4b1c-9d3e-2a7f8e6c5b40"))


def purge_job(kind: JobKind = JobKind.PURGE_DOCUMENT_CHUNKS) -> Job:
    return Job.request(
        job_id=JOB_ID, kind=kind, target_id=uuid.uuid4(), queued_at=QUEUED_AT
    )


@pytest.fixture
def stub_broker() -> Iterator[StubBroker]:
    broker = StubBroker(middleware=queue_middleware())
    declare_queues(broker)
    broker.emit_after("process_boot")
    yield broker
    broker.close()


@pytest.fixture
def ran() -> list[JobId]:
    return []


@pytest.fixture
def job_actors(stub_broker: StubBroker, ran: list[JobId]) -> JobActors:
    return declare_job_actors(
        stub_broker,
        run_job=ran.append,
        kinds=frozenset({JobKind.PURGE_DOCUMENT_CHUNKS}),
    )


def run_worker_until_empty(broker: StubBroker) -> None:
    worker = dramatiq.Worker(broker, worker_timeout=100)
    worker.start()
    try:
        broker.join(QueueName.MAINTENANCE, timeout=10_000, fail_fast=True)
        worker.join()
    finally:
        worker.stop()


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_published_job_reaches_the_worker_as_its_id_only(
    stub_broker: StubBroker, job_actors: JobActors, ran: list[JobId]
) -> None:
    DramatiqJobPublisher(job_actors).publish(purge_job())

    [raw] = list(stub_broker.queues[QueueName.MAINTENANCE].queue)
    message = json.loads(raw)
    assert message["actor_name"] == "run_maintenance_job"
    assert message["args"] == [str(JOB_ID.value)]
    assert message["kwargs"] == {}

    run_worker_until_empty(stub_broker)

    assert ran == [JOB_ID]


def test_actors_exist_only_for_the_queues_of_runnable_kinds(
    stub_broker: StubBroker, job_actors: JobActors
) -> None:
    assert set(job_actors.by_queue) == {QueueName.MAINTENANCE}
    # Conversation purges share the queue, but no worker runs them.
    assert job_actors.kinds == {JobKind.PURGE_DOCUMENT_CHUNKS}
    assert stub_broker.get_declared_actors() == {"run_maintenance_job"}
    actor = job_actors.by_queue[QueueName.MAINTENANCE]
    assert actor.options == {"time_limit": 60_000, "max_retries": 0}


@pytest.mark.parametrize(
    "kind", [JobKind.SYNCHRONIZE_SOURCE, JobKind.PURGE_CONVERSATION]
)
def test_a_kind_no_worker_runs_is_refused_as_a_failed_publish(
    stub_broker: StubBroker, job_actors: JobActors, kind: JobKind
) -> None:
    publisher = DramatiqJobPublisher(job_actors)

    assert kind not in publisher.kinds
    with pytest.raises(PublishFailed):
        publisher.publish(purge_job(kind))
    assert stub_broker.queues[QueueName.MAINTENANCE].qsize() == 0


def test_an_unreachable_queue_is_a_failed_publish_within_its_timeouts() -> None:
    broker = build_broker(f"redis://127.0.0.1:{closed_port()}/0")
    try:
        actors = declare_job_actors(
            broker,
            run_job=lambda _job_id: None,
            kinds=frozenset({JobKind.PURGE_DOCUMENT_CHUNKS}),
        )
        started = time.monotonic()
        with pytest.raises(PublishFailed):
            DramatiqJobPublisher(actors).publish(purge_job())
        assert time.monotonic() - started < 5.0
    finally:
        broker.close()


@pytest.mark.parametrize(
    "argument",
    [
        123,
        None,
        "not-a-uuid",
        str(JOB_ID.value).upper(),
        "{" + str(JOB_ID.value) + "}",
        str(JOB_ID.value).replace("-", ""),
        f" {JOB_ID.value}",
        [str(JOB_ID.value)],
    ],
)
def test_the_worker_refuses_a_message_that_names_no_canonical_job_id(
    stub_broker: StubBroker,
    job_actors: JobActors,
    ran: list[JobId],
    argument: object,
) -> None:
    actor = job_actors.by_queue[QueueName.MAINTENANCE]
    actor.send_with_options(args=(argument,))  # pyright: ignore[reportUnknownMemberType]

    with pytest.raises(InvalidJobMessage):
        run_worker_until_empty(stub_broker)

    assert ran == []
    assert len(stub_broker.dead_letters) == 1


def test_parse_job_id_accepts_the_canonical_spelling() -> None:
    assert parse_job_id(str(JOB_ID.value)) == JOB_ID


def test_a_job_runner_requires_a_database_url() -> None:
    with pytest.raises(WorkerConfigurationError):
        JobRunner(Settings(database_url=None))
```

**Перевірка:** `uv run pytest tests/test_job_messages.py -q` показує `15 passed`.

### Крок 8 — Крок з кодом: integration-тести на справжньому PostgreSQL

Цей крок доводить порядок коміту, лагодження і єдиний ефект там, де вони справді відбуваються: у PostgreSQL.

**Простими словами:** замість Redis тут фальшивий відправник, тож кожен тест може змусити його відмовити або «впасти» одразу після надсилання. Один тест у момент надсилання відкриває друге з'єднання з базою і перевіряє, що Job уже закомічений. Інші змушують надсилання впасти, потім запускають relay і перевіряють, що він лагодить Job рівно один раз. Два relay працюють одночасно і мусять надіслати кожен Job один раз. Наостанок справжній Job очищення запускають двічі, а також одночасно з двох повідомлень, і він мусить видалити фрагменти один раз.

| Група тестів | Що доводить |
|---|---|
| `test_a_job_is_committed_before_its_message_is_sent` | інше з'єднання бачить рядок у черзі, ще не надісланий, у момент надсилання |
| `test_a_refused_message_...`, `test_a_crash_after_sending_...` | штучний збій з обох боків надсилання; relay лагодить його один раз |
| `test_the_relay_finds_jobs_stored_inside_another_transaction` | Job очищення, збережений `tombstone_missing`, надсилається через relay |
| `test_two_relays_at_once_send_each_job_once` | `SKIP LOCKED` розводить одночасні relay |
| `test_the_relay_stops_...`, `test_the_relay_publishes_oldest_first_...`, `test_the_relay_limit_is_bounded` | зупинка на першій відмові; порядок; ліміт від 1 до 1 000 |
| `test_jobs_no_worker_runs_...`, `test_the_real_publisher_leaves_kinds_without_a_handler_waiting` | `UnsupportedJobKind`; очищення розмов лишаються ненадісланими |
| `test_a_job_queued_again_needs_a_new_message`, `test_a_finished_or_running_job_is_never_published` | повтори надсилаються знову; Job, що виконуються, — ні |
| `test_only_a_new_queued_job_can_be_submitted`, `test_times_must_be_utc` | типізовані відмови; нічого не збережено |
| `test_a_duplicate_message_runs_the_job_once`, `test_two_messages_at_once_run_the_job_once` | справжнє очищення виконується раз; Job лишається зі спробою 0 і першим часом завершення |
| `test_a_message_for_no_stored_job_...`, `test_a_message_for_a_kind_without_a_handler_...` | `InvalidJobMessage`; Job лишається в черзі |

Спільна підготовка живе в окремому модулі, тож крок 9 може її використати. `tombstoned_with_purge` зберігає один документ із фрагментами, а потім позначає його видаленим, і це зберігає Job очищення так, як це зробив би справжній прохід синхронізації.

**Дія 1:** створи `tests/job_fixtures.py`:

```python
"""Shared setup for the Job dispatch tests against real PostgreSQL."""

from datetime import UTC, datetime

from sqlalchemy import text

from knowledge_service.access import Subject
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document
from knowledge_service.identifiers import DocumentId, JobId, SourceId
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import SourceAccess, SourceRecord
from knowledge_service.jobs import Job
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.jobs import load_job

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000020")
OBSERVED_AT = datetime(2026, 11, 21, 9, 0, tzinfo=UTC)

INSERT_SOURCE = text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/jobs', 'Jobs', true, now(), now())"
)


async def insert_source(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})


async def stored_job(runtime: DatabaseRuntime, job_id: JobId) -> Job:
    async with runtime.transaction() as connection:
        job = await load_job(connection, job_id=job_id)
    assert job is not None
    return job


async def tombstoned_with_purge(runtime: DatabaseRuntime) -> tuple[Document, JobId]:
    """A tombstoned Document that still has Chunks, and its stored purge Job."""
    normalized = normalize(
        SourceRecord.present(
            external_id="retired.md",
            version="v1",
            media_type="text/markdown",
            content=b"# Retired\n\nThis policy was retired.\n",
            access=SourceAccess(public=True),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(normalized, NormalizedDocument)
    result = await reconcile_document(
        runtime, normalized, readers=[Subject.public()], observed_at=OBSERVED_AT
    )
    [tombstone] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=OBSERVED_AT
    )
    return result.document, tombstone.purge_job_id


async def chunk_count(runtime: DatabaseRuntime, document_id: DocumentId) -> int:
    async with runtime.transaction() as connection:
        return len(await load_chunks(connection, document_id=document_id))
```

**Дія 2:** створи `tests/test_job_dispatch_integration.py`:

```python
"""Persist before enqueue, against real PostgreSQL.

A fake publisher stands in for Redis here, so each test can make the queue
refuse a message or the process "crash" at an exact point.
"""

import asyncio
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from dramatiq.brokers.stub import StubBroker
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from job_fixtures import (
    OBSERVED_AT,
    chunk_count,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.job_dispatch import (
    PublishFailed,
    PublishOutcome,
    RelayReport,
    Submission,
    UnsupportedJobKind,
    publish_job,
    publish_pending_jobs,
    submit_job,
)
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence.jobs import enqueue_job, save_job
from knowledge_service.persistence.schema import jobs
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import QueueName, declare_queues, queue_middleware
from knowledge_service.worker.jobs import (
    HANDLERS,
    DramatiqJobPublisher,
    InvalidJobMessage,
    declare_job_actors,
    run_stored_job,
)

pytestmark = pytest.mark.integration

NOW = OBSERVED_AT + timedelta(minutes=5)
LATER = OBSERVED_AT + timedelta(minutes=10)
PURGE = JobKind.PURGE_DOCUMENT_CHUNKS
RUNNABLE = frozenset({PURGE})


@dataclass
class FakePublisher:
    """Records every message it is asked to send, and can fail on purpose."""

    kinds: frozenset[JobKind] = RUNNABLE
    sent: list[JobId] = field(default_factory=lambda: list[JobId]())
    # Called with each Job before it counts as sent; may raise.
    before_send: Callable[[Job], None] | None = None
    # Called after the message counts as sent; may raise.
    after_send: Callable[[Job], None] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def publish(self, job: Job) -> None:
        if self.before_send is not None:
            self.before_send(job)
        with self.lock:
            self.sent.append(job.job_id)
        if self.after_send is not None:
            self.after_send(job)


def refuse(_job: Job) -> None:
    raise PublishFailed("the queue is down")


def crash(_job: Job) -> None:
    raise RuntimeError("the process stopped before recording the send")


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


def new_job(kind: JobKind = PURGE) -> Job:
    return Job.request(
        job_id=JobId.new(), kind=kind, target_id=uuid.uuid4(), queued_at=OBSERVED_AT
    )


async def published_at(runtime: DatabaseRuntime, job_id: JobId) -> datetime | None:
    async with runtime.transaction() as connection:
        return await connection.scalar(
            sa.select(jobs.c.published_at).where(jobs.c.job_id == job_id.value)
        )


async def job_count(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        result = await connection.execute(sa.select(sa.func.count()).select_from(jobs))
        return result.scalar_one()


async def test_a_job_is_committed_before_its_message_is_sent(
    runtime: DatabaseRuntime, migrated_database: str
) -> None:
    seen_at_send: list[tuple[str, bool]] = []
    other = sa.create_engine(
        make_url(migrated_database).set(drivername="postgresql+psycopg")
    )

    def check_committed(job: Job) -> None:
        # Another connection sees the row only once it is committed.
        with other.connect() as connection:
            row = connection.execute(
                sa.select(jobs.c.status, jobs.c.published_at).where(
                    jobs.c.job_id == job.job_id.value
                )
            ).one()
        seen_at_send.append((row.status, row.published_at is None))

    job = new_job()
    publisher = FakePublisher(before_send=check_committed)

    try:
        submission = await submit_job(runtime, job, publisher=publisher, now=NOW)
    finally:
        other.dispose()

    assert submission == Submission(job_id=job.job_id, published=True)
    assert seen_at_send == [("queued", True)]
    assert publisher.sent == [job.job_id]
    assert await published_at(runtime, job.job_id) == NOW


async def test_a_refused_message_leaves_the_job_stored_for_the_relay(
    runtime: DatabaseRuntime,
) -> None:
    job = new_job()

    submission = await submit_job(
        runtime, job, publisher=FakePublisher(before_send=refuse), now=NOW
    )

    assert submission == Submission(job_id=job.job_id, published=False)
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.QUEUED
    assert await published_at(runtime, job.job_id) is None

    relay = FakePublisher()
    first = await publish_pending_jobs(runtime, publisher=relay, now=LATER)
    second = await publish_pending_jobs(runtime, publisher=relay, now=LATER)

    assert first == RelayReport(published=1, skipped=0, failed=0, untried=0)
    assert second == RelayReport(published=0, skipped=0, failed=0, untried=0)
    assert relay.sent == [job.job_id]
    assert await published_at(runtime, job.job_id) == LATER


async def test_a_crash_after_sending_is_repaired_by_sending_again(
    runtime: DatabaseRuntime,
) -> None:
    job = new_job()
    publisher = FakePublisher(after_send=crash)

    submission = await submit_job(runtime, job, publisher=publisher, now=NOW)

    # The message left, but the send was never recorded.
    assert submission == Submission(job_id=job.job_id, published=False)
    assert publisher.sent == [job.job_id]
    assert await published_at(runtime, job.job_id) is None

    relay = FakePublisher()
    report = await publish_pending_jobs(runtime, publisher=relay, now=LATER)

    assert report.published == 1
    assert relay.sent == [job.job_id]
    assert await published_at(runtime, job.job_id) == LATER


async def test_the_relay_finds_jobs_stored_inside_another_transaction(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    assert await published_at(runtime, purge_id) is None

    relay = FakePublisher()
    report = await publish_pending_jobs(runtime, publisher=relay, now=NOW)

    assert report.published == 1
    assert relay.sent == [purge_id]


async def test_two_relays_at_once_send_each_job_once(
    runtime: DatabaseRuntime,
) -> None:
    stored = [new_job() for _ in range(6)]
    async with runtime.transaction() as connection:
        for job in stored:
            await enqueue_job(connection, job)

    def slow(_job: Job) -> None:
        time.sleep(0.05)

    publisher = FakePublisher(before_send=slow)
    reports = await asyncio.gather(
        publish_pending_jobs(runtime, publisher=publisher, now=NOW),
        publish_pending_jobs(runtime, publisher=publisher, now=NOW),
    )

    assert sorted(publisher.sent, key=str) == sorted(
        (job.job_id for job in stored), key=str
    )
    assert sum(report.published for report in reports) == 6


async def test_the_relay_stops_at_the_first_refused_message(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        for _ in range(3):
            await enqueue_job(connection, new_job())
    attempts: list[Job] = []

    def count_and_refuse(job: Job) -> None:
        attempts.append(job)
        refuse(job)

    report = await publish_pending_jobs(
        runtime, publisher=FakePublisher(before_send=count_and_refuse), now=NOW
    )

    assert report == RelayReport(published=0, skipped=0, failed=1, untried=2)
    assert len(attempts) == 1


async def test_the_relay_publishes_oldest_first_up_to_its_limit(
    runtime: DatabaseRuntime,
) -> None:
    older = Job.request(
        job_id=JobId.new(), kind=PURGE, target_id=uuid.uuid4(), queued_at=OBSERVED_AT
    )
    newer = Job.request(
        job_id=JobId.new(), kind=PURGE, target_id=uuid.uuid4(), queued_at=NOW
    )
    async with runtime.transaction() as connection:
        await enqueue_job(connection, newer)
        await enqueue_job(connection, older)
    relay = FakePublisher()

    report = await publish_pending_jobs(runtime, publisher=relay, now=LATER, limit=1)

    assert report.published == 1
    assert relay.sent == [older.job_id]


async def test_jobs_no_worker_runs_are_neither_submitted_nor_relayed(
    runtime: DatabaseRuntime,
) -> None:
    with pytest.raises(UnsupportedJobKind):
        await submit_job(
            runtime,
            new_job(JobKind.SYNCHRONIZE_SOURCE),
            publisher=FakePublisher(),
            now=NOW,
        )
    assert await job_count(runtime) == 0

    waiting = new_job(JobKind.PURGE_CONVERSATION)
    async with runtime.transaction() as connection:
        await enqueue_job(connection, waiting)
    relay = FakePublisher()

    report = await publish_pending_jobs(runtime, publisher=relay, now=NOW)

    assert report.published == 0
    assert relay.sent == []
    assert await published_at(runtime, waiting.job_id) is None


async def test_a_job_queued_again_needs_a_new_message(
    runtime: DatabaseRuntime,
) -> None:
    job = new_job()
    await submit_job(runtime, job, publisher=FakePublisher(), now=NOW)
    async with runtime.transaction() as connection:
        failed = job.start(started_at=NOW).fail(error_code="boom", finished_at=NOW)
        await save_job(connection, job=failed)
        await save_job(connection, job=failed.retry(queued_at=LATER))
    assert await published_at(runtime, job.job_id) is None

    relay = FakePublisher()
    await publish_pending_jobs(runtime, publisher=relay, now=LATER)

    assert relay.sent == [job.job_id]


async def test_a_finished_or_running_job_is_never_published(
    runtime: DatabaseRuntime,
) -> None:
    job = new_job()
    async with runtime.transaction() as connection:
        await enqueue_job(connection, job)
        await save_job(connection, job=job.start(started_at=NOW))
    publisher = FakePublisher()

    outcome = await publish_job(
        runtime, job_id=job.job_id, publisher=publisher, now=NOW
    )

    assert outcome is PublishOutcome.SKIPPED
    assert publisher.sent == []


async def test_only_a_new_queued_job_can_be_submitted(runtime: DatabaseRuntime) -> None:
    running = new_job().start(started_at=NOW)

    with pytest.raises(InvalidJob):
        await submit_job(runtime, running, publisher=FakePublisher(), now=NOW)

    assert await job_count(runtime) == 0


@pytest.mark.parametrize("limit", [0, 1_001, True, 2.0])
async def test_the_relay_limit_is_bounded(runtime: DatabaseRuntime, limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        await publish_pending_jobs(
            runtime, publisher=FakePublisher(), now=NOW, limit=limit
        )


async def test_times_must_be_utc(runtime: DatabaseRuntime) -> None:
    local = datetime(2026, 11, 21, 9, 0)
    with pytest.raises(ValueError, match="UTC"):
        await submit_job(runtime, new_job(), publisher=FakePublisher(), now=local)
    assert await job_count(runtime) == 0


async def test_a_duplicate_message_runs_the_job_once(
    runtime: DatabaseRuntime,
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    assert await chunk_count(runtime, document.document_id) > 0

    first = await run_stored_job(runtime, purge_id, now=NOW)
    second = await run_stored_job(runtime, purge_id, now=LATER)

    assert first is not None
    assert first.status is JobStatus.SUCCEEDED
    assert second is None
    assert await chunk_count(runtime, document.document_id) == 0
    stored = await stored_job(runtime, purge_id)
    assert (stored.status, stored.attempt, stored.finished_at) == (
        JobStatus.SUCCEEDED,
        0,
        NOW,
    )


async def test_two_messages_at_once_run_the_job_once(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)

    results = await asyncio.gather(
        run_stored_job(runtime, purge_id, now=NOW),
        run_stored_job(runtime, purge_id, now=NOW),
    )

    assert sum(result is not None for result in results) == 1
    assert (await stored_job(runtime, purge_id)).status is JobStatus.SUCCEEDED


async def test_a_message_for_no_stored_job_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    with pytest.raises(InvalidJobMessage):
        await run_stored_job(runtime, JobId.new(), now=NOW)


async def test_a_message_for_a_kind_without_a_handler_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    waiting = new_job(JobKind.PURGE_CONVERSATION)
    async with runtime.transaction() as connection:
        await enqueue_job(connection, waiting)

    with pytest.raises(InvalidJobMessage):
        await run_stored_job(runtime, waiting.job_id, now=NOW)

    assert (await stored_job(runtime, waiting.job_id)).status is JobStatus.QUEUED


async def test_the_real_publisher_leaves_kinds_without_a_handler_waiting(
    runtime: DatabaseRuntime,
) -> None:
    broker = StubBroker(middleware=queue_middleware())
    declare_queues(broker)
    try:
        publisher = DramatiqJobPublisher(
            declare_job_actors(
                broker, run_job=lambda _job_id: None, kinds=frozenset(HANDLERS)
            )
        )
        conversation_purge = new_job(JobKind.PURGE_CONVERSATION)
        async with runtime.transaction() as connection:
            await enqueue_job(connection, conversation_purge)

        with pytest.raises(UnsupportedJobKind):
            await submit_job(
                runtime,
                new_job(JobKind.PURGE_CONVERSATION),
                publisher=publisher,
                now=NOW,
            )
        report = await publish_pending_jobs(runtime, publisher=publisher, now=NOW)

        assert report == RelayReport(published=0, skipped=0, failed=0, untried=0)
        assert await published_at(runtime, conversation_purge.job_id) is None
        assert broker.queues[QueueName.MAINTENANCE].qsize() == 0
    finally:
        broker.close()
```

**Перевірка:** `uv run pytest tests/test_job_dispatch_integration.py -q -m integration` показує `21 passed`.

### Крок 9 — Крок з кодом: один Job від початку до кінця через Redis

Цей крок проганяє весь шлях зі справжнім Redis і справжнім воркером Dramatiq.

**Простими словами:** Job очищення зберігається, коли документ позначають видаленим. Спершу relay пробує Redis, якого немає, падає і лишає Job чекати. Потім relay працює зі справжнім Redis, воркер виконує Job, і фрагментів документа більше немає. Наостанок про той самий Job надсилають друге повідомлення, і збережений Job не змінюється, а мертвих листів немає.

**Дія:** створи `tests/test_job_queue_integration.py`:

```python
"""A stored Job through real Redis and a real Dramatiq worker, end to end."""

import os
import socket
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta

import dramatiq
import pytest
from dramatiq.brokers.redis import RedisBroker
from pydantic import SecretStr

from job_fixtures import (
    OBSERVED_AT,
    chunk_count,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.job_dispatch import RelayReport, publish_pending_jobs
from knowledge_service.jobs import JobStatus
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import QueueName, build_broker, redis_client
from knowledge_service.worker.jobs import (
    HANDLERS,
    DramatiqJobPublisher,
    JobActors,
    JobRunner,
    declare_job_actors,
)

pytestmark = pytest.mark.integration

REDIS_URL_ENV = "KNOWLEDGE_SERVICE_TEST_REDIS_URL"
NOW = OBSERVED_AT + timedelta(minutes=5)


@pytest.fixture
def redis_url() -> str:
    configured = os.environ.get(REDIS_URL_ENV)
    if not configured:
        pytest.skip(f"set {REDIS_URL_ENV} to a disposable Redis server")
    return configured


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


@pytest.fixture
def redis_broker(redis_url: str) -> Iterator[RedisBroker]:
    namespace = f"knowledge-service.test-{uuid.uuid4()}"
    broker = build_broker(redis_url, namespace=namespace)
    broker.emit_after("process_boot")
    yield broker
    broker.flush_all()
    broker.close()
    cleaner = redis_client(redis_url)
    try:
        cleaner.delete(f"{namespace}:__heartbeats__")
    finally:
        cleaner.close()


@pytest.fixture
def job_actors(redis_broker: RedisBroker, migrated_database: str) -> JobActors:
    runner = JobRunner(Settings(database_url=SecretStr(migrated_database)))
    return declare_job_actors(redis_broker, run_job=runner, kinds=frozenset(HANDLERS))


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def test_a_job_stored_while_redis_was_down_runs_once_after_the_relay(
    runtime: DatabaseRuntime,
    redis_broker: RedisBroker,
    job_actors: JobActors,
    redis_url: str,
) -> None:
    # A synchronization pass stored the purge Job; no message names it yet.
    document, purge_id = await tombstoned_with_purge(runtime)

    # The queue is unreachable: the relay fails and the Job keeps waiting.
    down = build_broker(f"redis://127.0.0.1:{closed_port()}/0")
    try:
        down_actors = declare_job_actors(
            down, run_job=lambda _job_id: None, kinds=frozenset(HANDLERS)
        )
        refused = await publish_pending_jobs(
            runtime, publisher=DramatiqJobPublisher(down_actors), now=NOW
        )
    finally:
        down.close()
    assert refused == RelayReport(published=0, skipped=0, failed=1, untried=0)
    assert (await stored_job(runtime, purge_id)).status is JobStatus.QUEUED

    # Redis is back: the relay publishes it, and a worker runs it.
    publisher = DramatiqJobPublisher(job_actors)
    relayed = await publish_pending_jobs(runtime, publisher=publisher, now=NOW)
    assert relayed == RelayReport(published=1, skipped=0, failed=0, untried=0)

    worker = dramatiq.Worker(redis_broker, worker_timeout=100, worker_threads=2)
    worker.start()
    try:
        redis_broker.join(QueueName.MAINTENANCE, timeout=20_000)
        finished = await stored_job(runtime, purge_id)
        assert finished.status is JobStatus.SUCCEEDED
        assert await chunk_count(runtime, document.document_id) == 0

        # A second message for the same Job, as after a crash before its send
        # was recorded, changes nothing.
        publisher.publish(finished)
        redis_broker.join(QueueName.MAINTENANCE, timeout=20_000)
        assert await stored_job(runtime, purge_id) == finished
    finally:
        worker.stop()

    # Neither message failed: nothing reached the dead-letter queue.
    inspector = redis_client(redis_url)
    try:
        dead = inspector.zcard(f"{redis_broker.namespace}:maintenance.XQ")
    finally:
        inspector.close()
    assert dead == 0
```

**Перевірка:** `uv run pytest tests/test_job_queue_integration.py -q -m integration` показує `1 passed`.

### Крок 10 — У цьому кроці коду немає: влаштуй збій по-справжньому

Цей крок показує критерій приймання на справжньому процесі воркера: Redis лежить, коли запускається relay, а коли Redis повертається, Job лагодиться.

Тут використовується окрема демо-база, тож твої локальні дані ніколи не зачіпаються. Виконуй усі команди в **одному терміналі** і не передавай `source` або блоки нижче через `|` в іншу команду, бо інакше URL бази загубиться.

**Дія 1:** створи демо-базу, застосуй міграції і збережи один Job очищення:

```bash
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/[^/]*$#/m08_t02_demo#')"
uv run python - <<'EOF'
import os

import sqlalchemy as sa
from sqlalchemy.engine import make_url

url = make_url(os.environ["KNOWLEDGE_SERVICE_DATABASE_URL"])
admin = sa.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
with admin.connect() as connection:
    connection.execute(sa.text("DROP DATABASE IF EXISTS m08_t02_demo"))
    connection.execute(sa.text("CREATE DATABASE m08_t02_demo"))
EOF
uv run alembic upgrade head
PYTHONPATH=tests uv run python - <<'EOF'
import asyncio

from job_fixtures import chunk_count, insert_source, tombstoned_with_purge
from knowledge_service.database import create_database_runtime
from knowledge_service.settings import Settings


async def main() -> None:
    runtime = create_database_runtime(Settings())
    try:
        await insert_source(runtime)
        document, job_id = await tombstoned_with_purge(runtime)
        chunks = await chunk_count(runtime, document.document_id)
        print(f"purge job {job_id.value}, chunks {chunks}")
    finally:
        await runtime.dispose()


asyncio.run(main())
EOF
```

Ти побачиш `purge job <id>, chunks 1`.

**Дія 2:** тримай під рукою цей помічник; він друкує стан Job і кількість фрагментів:

```bash
uv run python - <<'EOF'
import os

import sqlalchemy as sa
from sqlalchemy.engine import make_url

url = make_url(os.environ["KNOWLEDGE_SERVICE_DATABASE_URL"])
engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
with engine.connect() as connection:
    jobs = connection.execute(
        sa.text("select status, published_at is not null as published from jobs")
    ).all()
    chunks = connection.execute(sa.text("select count(*) from chunks")).scalar_one()
print("jobs:", [tuple(row) for row in jobs], "chunks:", chunks)
EOF
```

Він друкує `jobs: [('queued', False)] chunks: 1`.

**Дія 3:** зупини Redis і запусти relay:

```bash
docker compose -p knowledge-service-redis stop
make worker-relay
```

Він друкує `published 0, skipped 0, failed 1, untried 0` і завершується з помилкою. Помічник і далі друкує `('queued', False)`.

**Дія 4:** запусти Redis, у **другому терміналі** з тим самим експортованим `KNOWLEDGE_SERVICE_DATABASE_URL` запусти воркер, а в першому знову запусти relay:

```bash
make redis-up
make worker-relay
```

Relay друкує `published 1`. Воркер пише в лог `job <id> (purge_document_chunks) ran: succeeded`, а помічник друкує `jobs: [('succeeded', True)] chunks: 0`. Третій `make worker-relay` друкує `published 0`.

**Дія 5:** зупини воркер через Ctrl+C, потім видали демо-базу:

```bash
uv run python - <<'EOF'
import os

import sqlalchemy as sa
from sqlalchemy.engine import make_url

url = make_url(os.environ["KNOWLEDGE_SERVICE_DATABASE_URL"])
admin = sa.create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
with admin.connect() as connection:
    connection.execute(sa.text("DROP DATABASE IF EXISTS m08_t02_demo"))
EOF
unset KNOWLEDGE_SERVICE_DATABASE_URL
```

**Перевірка:** встав виводи relay, рядок з логу воркера і три виводи помічника в розділ «Verification» свого evidence-файлу.

### Крок 11 — У цьому кроці коду немає: зламай навмисно

На цьому кроці ти прибираєш рядок, через який повторена спроба Job потребує нового повідомлення, і дивишся, який тест це помітить.

**Дія 1:** у `save_job` у `src/knowledge_service/persistence/jobs.py` видали ці два рядки:

```python
    if job.status is JobStatus.QUEUED:
        values["published_at"] = None
```

**Дія 2:** запусти три тестові файли:

```bash
uv run pytest tests/test_job_messages.py tests/test_job_dispatch_integration.py tests/test_job_queue_integration.py -q -m "integration or not integration"
```

Падає один тест: `test_a_job_queued_again_needs_a_new_message`. Без цього рядка повторений Job зберігає старий час надсилання, тож relay вважає його вже надісланим, і нова спроба ніколи не доходить до воркера.

**Дія 3:** поверни рядки на місце й запусти команду ще раз: проходять усі 37.

**Перевірка:** порівняй кількість зі своїм прогнозом із кроку 1 і запиши це в evidence-файл.

### Крок 12 — У цьому кроці коду немає: документи, перевірки, evidence і прогрес

На цьому кроці ти оновлюєш два документи і проганяєш усі перевірки.

**Дія 1:** у `docs/architecture.md` додай цей абзац одразу перед абзацом, що починається з `` `telemetry` decides what leaves the process as logs``:

```markdown
`job_dispatch` puts a Job on the queue without ever losing it. `submit_job`
commits the Job to PostgreSQL first, then sends one message naming only its
ID, under a lock on its row, and records the send in `published_at`. When
the queue refuses the message, or the process stops before the send is
recorded, the Job stays queued and unpublished; `publish_pending_jobs`
(`make worker-relay`) sends such Jobs later, oldest first, and stops at the
first refused message. Jobs that other workflows store in their own
transactions, such as the purge `tombstone_missing` queues, reach the queue
the same way. A Job queued again for another attempt is unpublished again.
Only kinds a worker runs are submitted or sent. A Job can be named by two
messages; the worker runs it only while it is still queued.
```

і заміни рядок `worker` у таблиці структури коду на:

```markdown
| `worker` | Queue transport: the Redis broker and the worker process | `build_broker` (`broker`, a Dramatiq `RedisBroker` with JSON messages, the `knowledge-service.v1` namespace, the `maintenance` and `synchronization` queues, a 60-second default time limit, a one-day message age limit, and no Dramatiq retries; `ActorOwnsOptions` drops any limit a message sets for itself, so only its actor's limits apply; Redis calls make one attempt with bounded timeouts), `declare_actors` / `send_ping` (`actors`, the harmless `ping` job, whose token is checked on both sides), `broker_ready` (`health`, a readiness probe that is false, within one overall deadline, whenever Redis does not answer PING or any part of the check fails), `declare_job_actors` / `DramatiqJobPublisher` / `run_stored_job` (`jobs`, one `run_<queue>_job` actor per queue of the kinds in `HANDLERS`, today only `purge_document_chunks`; a message carries one canonical Job ID, and the worker reads the Job from PostgreSQL, runs it only while it is queued, and ignores a duplicate; `JobRunner` gives each message its own event loop and database engine), `relay` (one pass of `publish_pending_jobs`), and `main`, the module the `dramatiq` command loads |
```

**Дія 2:** у `docs/threat-model.md` заміни рядок `Jobs` на:

```markdown
| Jobs | Workers | Stored jobs with typed errors. A queue message, JSON and never pickle, carries only the harmless `ping` token or one canonical Job ID; the worker reads what to do from PostgreSQL. A stored Job is committed before any message names it, and a Job that is no longer queued is not run again. A Job whose message was sent but then lost (a Redis restart, a message dead-lettered before the Job was claimed, or one dropped by the one-day age limit) stays queued and published until M08-T08 |
```

**Дія 3:** прожени всі перевірки:

```bash
make format-check
make lint
make typecheck
make test-unit
make test-integration
make docs-check
```

**Дія 4:** допиши evidence-файл: результати перевірок у «Verification», знахідки рев'ю і відомі обмеження в «Review», дизайн у «Design» і «Decision», а також свої відповіді на питання для роздумів нижче. Потім познач завдання виконаним у `docs/curriculum/PROGRESS.md` і зроби коміт з повідомленням `m08-t02: persist before enqueue`.

**Перевірка:** усі перевірки проходять, а після коміту `git status` чистий.

## Питання для роздумів

- `submit_job` повертає `published=False`. Робота втрачена? Що змусить її виконатися, і що все ще може цьому завадити?
- Уяви, що ми надсилаємо повідомлення спершу, а зберігаємо Job потім. Опиши одну послідовність подій, де воркер отримує повідомлення про Job, якого не існує, і одну, де існує Job, про який жодне повідомлення ніколи не скаже.
- Relay може надіслати друге повідомлення про Job, для якого вже є одне. Чому ми погодилися на це, а не намагалися надсилати рівно один раз, і що робить друге повідомлення нешкідливим?
- Два повідомлення про той самий Job очищення одночасно доходять до двох потоків воркера. Пройди, що робить кожен потік, і скажи, який крок у базі вирішує, що фрагменти видалить лише один з них.
- Яке спостереження довело б, що цей дизайн неправильний? Наприклад: що треба було б побачити в продакшені, щоб вирішити, що одного relay недостатньо?

## Чекліст завершення

- [ ] Evidence-файл містить інваріант, власників і твої прогнози, записані до змін.
- [ ] Міграція `c2d3e4f5a6b7` додає `jobs.published_at` і частковий індекс; тести індексів і міграцій проходять.
- [ ] `persistence/jobs.py` перелічує, блокує з `SKIP LOCKED` і позначає ненадіслані Job; збереження Job у черзі очищає `published_at`.
- [ ] `job_dispatch.py` комітить до надсилання, після коміту ніколи не кидає помилку через проблему з надсиланням, а relay зупиняється на першій відмові.
- [ ] Повідомлення несуть лише канонічний ID Job; воркер виконує Job, лише поки той у черзі; надсилається лише `purge_document_chunks`.
- [ ] `make worker-relay` існує, і воркер має доступ до PostgreSQL.
- [ ] `tests/test_job_messages.py` (15), `tests/test_job_dispatch_integration.py` (21) і `tests/test_job_queue_integration.py` (1) проходять, а в `tests/test_worker.py` і далі 40.
- [ ] Relay падав при вимкненому Redis, потім надіслав Job один раз, і воркер виконав його один раз.
- [ ] Навмисну поломку зроблено: впав 1 тест, і все повернуто назад.
- [ ] `docs/architecture.md` і `docs/threat-model.md` описують надсилання, relay і відомі обмеження.
- [ ] Усі перевірки проходять: форматування, lint, типи, unit, integration, docs.
- [ ] Evidence, статус завдання, рядок у PROGRESS і коміт готові.

Коли напишеш файли, поділися ними або виводом помилок, і я перевірю їх за цим чеклістом.
