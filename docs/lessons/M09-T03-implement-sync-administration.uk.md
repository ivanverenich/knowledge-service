# M09-T03 — Реалізувати адміністрування синхронізації

Англійська версія: [M09-T03-implement-sync-administration.md](M09-T03-implement-sync-administration.md)

Завдання: [M09-T03 — Implement synchronization administration](../curriculum/milestones/M09-product-interfaces/M09-T03-implement-sync-administration.md)

## Що ви зробите

Цей урок дає контент-менеджеру змогу вручну запустити синхронізацію Source і стежити за її історією через HTTP. Досі Jobs синхронізації ставив у чергу лише планувальник, раз на годину. Після уроку натискання «синхронізувати зараз» ставить у чергу один Job, повторне натискання повертає той самий Job, а сторінка Source показує кожну синхронізацію, яка в нього була.

Конкретний приклад для Source довідника з M09-T02:

```text
POST /v1/sources/6f1c.../synchronizations
-> 202, Location: /v1/jobs/01a3...   {"job_id": "01a3...", "status": "queued", ...}

POST /v1/sources/6f1c.../synchronizations      (pressed again)
-> 200, Location: /v1/jobs/01a3...   the same Job, nothing new queued

GET /v1/sources/6f1c.../synchronizations?limit=2
-> {"items": [{"job_id": "01a3...", "status": "running", "latest_attempt": {...}}, ...],
    "next_cursor": "WzEsInN5bmNo..."}
```

Завдання називає п'ять операцій: запуск, статус, скасування, повтор і історію. Статус, повтор і скасування вже є на `/v1/jobs/{job_id}` з M08-T07 і M08-T08. Цей урок будує два маршрути, яких бракує і які запланувала таблиця маршрутів з M09-T01, закриває одну діру в повторі й закріплює кожну відмову всіх п'яти операцій.

| Що просить завдання | Де це зроблено |
|---|---|
| Запустити синхронізацію | Блоки 1–3; кроки 3 і 6 |
| Статус, скасування, повтор над збереженими Jobs, а не над внутрішнім станом брокера | Блоки 3 і 5; крок 4 (повтор) |
| Історія синхронізацій Source | Блок 4; кроки 2, 3 і 6 |
| Ідемпотентний запуск | Блок 1; крок 8 |
| Належність | Блок 6; крок 8 |
| Кінцеві стани | Блок 1; крок 8 |
| Стабільні відповіді з помилками | Блок 5; крок 8 |
| Справжня інфраструктура там, де перевіряється її поведінка | Крок 8 (PostgreSQL для індексу відкритих Jobs, блокувань, публікації й сторінок) |
| Повтор і скасування описані явно, зовнішні виклики обмежені | Блоки 1 і 3; крок 11 |
| Пояснити, що спростувало б дизайн | Крок 9 і запитання для роздумів |

Терміни, без яких урок не обійдеться:

- **Job синхронізації:** один збережений запит прочитати Source один раз, рядок у `jobs` з типом `synchronize_source` і ID Source як ціллю. Кожна спроба його виконати — це спроба (attempt).
- **Відкритий Job** (open Job): Job, який ще щось зробить: у черзі, виконується або у статусі failed із запланованим повтором. Успішний, остаточно невдалий і скасований — це **кінцеві стани** (final states).
- **Ідемпотентний:** зробити двічі — те саме, що один раз, як двічі натиснути кнопку ліфта.
- **Опублікувати** (publish): надіслати в Redis повідомлення з назвою Job, щоб його взяв worker. Повідомлення містить лише ID Job.
- **UUIDv7:** ID, перші 48 біт якого — момент його створення в мілісекундах, тож пізніше створені ID сортуються пізніше. Кожен ID у цьому проєкті такий.

## Перш ніж почати

Цей розділ пояснює кожне рішення, на яке спирається код уроку. Кожен блок показує реальну ситуацію в проєкті, що відбувається сьогодні, факт, що лежить під нею, і виправлення. Прочитайте його спочатку: кроки на нього спираються.

### 1. Запуск просить ту саму роботу, що й планувальник, і лише раз

Контент-менеджер двічі за секунду натискає «синхронізувати зараз», а колега в цей самий час натискає її з іншого браузера.

| Момент | Менеджер, клік 1 | Менеджер, клік 2 | Колега | `jobs` |
|---|---|---|---|---|
| 10:00:00.000 | Вставити Job у черзі для S | | | J1 у черзі |
| 10:00:00.300 | | Вставити Job у черзі для S | | J1 у черзі |
| 10:00:00.301 | | | Вставити Job у черзі для S | J1 у черзі |

Після цього існує лише J1. Прихований факт — індекс з M08-T03: у `jobs` є унікальний індекс на `(kind, target_id)`, що охоплює лише відкриті Jobs (`WHERE status in ('queued', 'running') or (status = 'failed' and retry_at is not null)`). Другу відкритий Job синхронізації для S просто неможливо зберегти. `enqueue_job` вставляє з `ON CONFLICT DO NOTHING` і, коли нічого не вставилося, читає відкритий Job:

```sql
INSERT INTO jobs (...) VALUES (...)
ON CONFLICT (kind, target_id) WHERE <open> DO NOTHING
RETURNING job_id;
-- no row: someone else's open Job is there
SELECT * FROM jobs WHERE kind = 'synchronize_source' AND target_id = :s AND <open>;
```

Тож запуску не потрібна власна перевірка. Він лише повідомляє, що з двох сталося:

| Що зробив `enqueue_job` | Відповідь | Аудит |
|---|---|---|
| зберіг новий Job | 202 Accepted, `Location: /v1/jobs/{job_id}` | одна `job.queued`, оператор як автор |
| знайшов відкритий Job | 200 OK, той самий `Location` і форма тіла | жодної |

Тому клієнт, що повторює запуск після тайм-ауту, у безпеці: у гіршому разі він отримає 200 з Job, який уже просив. Щойно Job доходить до кінцевого стану, індекс його більше не охоплює, і наступний запуск ставить новий Job:

| Стан останнього Job Source | Наступний запуск |
|---|---|
| у черзі, виконується, у статусі failed із запланованим повтором | 200, цей Job |
| успішний, остаточно невдалий, скасований | 202, новий Job |

Як автомат із талонами біля віконця, яке вже вас обслуговує: повторне натискання друкує ваш поточний номер, а не другий.

### 2. Запускається лише ввімкнений Source, і повтор перевіряє те саме

Source вимикають, коли ніщо більше не має його читати. Запуск не повинен ставити для нього Job і не повинен змагатися з вимкненням.

Запуск використовує блокування планувальника з M08-T10: він читає Source через `SELECT ... FOR SHARE` (`lock_enabled_sources`), а вимкнення з M09-T02 бере `SELECT ... FOR UPDATE`, яке чекає на всі спільні блокування рядка. Тож або Job зберігається до вимкнення, або запуск бачить Source вимкненим і нічого не зберігає.

| Момент | Запуск | Вимкнення | Що в базі |
|---|---|---|---|
| 10:00:00 | Блокує S спільно, бачить `enabled` | | S увімкнено |
| 10:00:01 | | `FOR UPDATE` чекає | S увімкнено, спільне блокування тримається |
| 10:00:02 | Зберігає Job J, commit | | J у черзі |
| 10:00:02 | | Блокування отримано; S вимкнено | S вимкнено, J у черзі |

Відсутній Source відповідає 404 `source_not_found`; вимкнений — 409 `source_disabled`.

Змагальне рев'ю знайшло таку саму діру через двері поруч. Job синхронізації, що остаточно впав, можна повторити через `POST /v1/jobs/{job_id}/retry`, а повтор ніколи не дивився на Source:

| Крок | Запит | До виправлення | Після |
|---|---|---|---|
| 1 | Job J джерела S остаточно падає | | |
| 2 | Вимкнути S | | |
| 3 | `POST /v1/sources/S/synchronizations` | 409 `source_disabled` | 409 |
| 4 | `POST /v1/jobs/J/retry` | 200, J знову в черзі | 409 `source_disabled`, J лишається в статусі failed |

Наскільки погано: сьогодні небагато, бо worker прочитав би S, побачив, що він вимкнений, і завершив J з `source_gone`. Але вимкнений Source означає «не читати нічого», а двері повтору суперечили дверям запуску. Тепер `retry_job` спільно блокує Source у Job синхронізації, точно як запуск. Спершу він перевіряє стан самого Job, тож повтор, що ніколи не пасує, наприклад для Job, що завершився успішно, і далі відповідає `decision_not_allowed`, а не радить оператору ввімкнути Source.

### 3. Спершу зберегти, потім опублікувати

Запуск має пережити відмову Redis. Порядок важливий:

```python
# Was (what the wrong order would look like): publish, then store
publisher.publish(job)          # Redis has a message for J
await save(job)                 # crash here: a message names a Job that does not exist

# Became: store and audit in one transaction, then try once to publish
started = await start_synchronization(...)      # commits J and job.queued
await publish_job(runtime, job_id=..., publisher=..., now=now)
```

Прихований факт — ADR 0011: Redis лише доставляє, а запис — це PostgreSQL. Job, збережений, але не опублікований, лишається в черзі з порожнім `published_at`, і relay (`make worker-relay`) пізніше публікує кожен такий Job. Опублікований Job повторний запуск більше не публікує, бо `publish_job` блокує лише ще не опубліковані Jobs. Невдала відправка записується в лог лише типом помилки, бо її повідомлення могло б процитувати URL.

| Що не вдалося | Що отримує клієнт | Що стається з Job |
|---|---|---|
| нічого | 202 | збережена й опублікована |
| Redis відхилив повідомлення | 202 | збережена; її опублікує relay |
| база даних | 503 або 500 | нічого не збережено й не опубліковано |

### 4. Сторінка історії починається після ID Job, бо ID ніколи не зсувається

Історія показує Jobs синхронізації Source сторінками, від найновіших, з дизайном курсора з M09-T01. Перший прототип упорядковував за `(queued_at, job_id)`, як список рішень. Змагальне рев'ю його зламало:

| Момент | Оператор | `jobs` для Source S (queued_at) |
|---|---|---|
| 09:00 | | J1 09:00, J2 09:01, J3 09:02, J4 09:03, усі остаточно впали |
| 10:00:00 | Сторінка 1: J4, J3; курсор «після J3 о 09:02» | |
| 10:00:05 | Адміністратор повторює J1 | `queued_at` у J1 стає 10:00:05 |
| 10:00:10 | Сторінка 2: Jobs перед 09:02 | лише J2; J1 тепер вище курсора |

J1 так і не з'явився в списку. Прихований факт: повтор не створює нового Job; він знову ставить у чергу той самий рядок і переписує його `queued_at` (`Job._queued_again`). Те саме стається з кожним автоматичним повтором. Ключ, що рухається, не може тримати межу сторінки.

Виправлення: сторінки лише за ID Job. Кожен ID тут — UUIDv7, що починається з моменту створення, тож «найновіший ID першим» означає «найновіше створене першим», а ID ніколи не змінюється:

```sql
-- Was: the key moves on every retry
WHERE (queued_at, job_id) < (:queued_at, :job_id) ORDER BY queued_at DESC, job_id DESC

-- Became: the key never changes
WHERE job_id < :job_id ORDER BY job_id DESC
```

Курсор тепер містить лише ID останнього Job. Кожна сторінка читає Jobs і їхні найновіші спроби в одному знімку `REPEATABLE READ`, тож Job, що тим часом завершився, ніколи не показується як «виконується» з уже завершеною спробою. Найновіші спроби приходять одним запитом `DISTINCT ON (job_id)`, а не окремим запитом на кожен Job. Частковий індекс на Jobs синхронізації отримує ID другим стовпцем, `(target_id, job_id)`, тож сторінка читає індекс у порядку, а не сортує всі Jobs, що будь-коли були в Source; Source, який синхронізують щогодини, набирає близько 8 700 на рік.

Як інвентарні номери в бібліотеці: книжка, яку повернули й видали знову, зберігає номер, тож список за номерами ніколи не губить своє місце.

### 5. Кожна відмова має один незмінний статус, код і повідомлення

Клієнт розгалужується за відповіддю: скрипт, що бачить `source_disabled`, зупиняється, а той, що бачить `job_running`, чекає. Якби код чи повідомлення змінилися, такий скрипт тихо зламався б. Тому один тест точно закріплює кожну відмову п'яти операцій:

| Операція | Ситуація | Статус | Код | Повідомлення |
|---|---|---|---|---|
| запуск | Source відсутній | 404 | `source_not_found` | No such Source |
| запуск | Source вимкнений | 409 | `source_disabled` | The Source is missing or disabled |
| історія | Source відсутній | 404 | `source_not_found` | No such Source |
| статус | Job відсутній | 404 | `job_not_found` | No such synchronization job |
| повтор | Job відсутній | 404 | `job_not_found` | No such job |
| повтор | для Source відкрита інший Job | 409 | `job_already_open` | Another job for this work is open |
| повтор | Source відсутній чи вимкнений, коли стан Job пасує | 409 | `source_disabled` | The Source is missing or disabled |
| повтор | Job ще не завершився остаточною невдачею | 409 | `decision_not_allowed` | This decision does not fit the job's status |
| скасування | Job відсутній | 404 | `job_not_found` | No such job |
| скасування | Job виконується | 409 | `job_running` | The job is still running |
| скасування | Job завершений | 409 | `decision_not_allowed` | This decision does not fit the job's status |

Кожна з них також має форму `ErrorResponse`: `code`, `message` і `request_id`, і нічого більше.

### 6. Хто що може робити і чиї Jobs показує сторінка

Таблиця маршрутів з M09-T01 уже вирішила ролі:

| Операція | Маршрут | Операція, яку має дозволяти роль | Роль |
|---|---|---|---|
| запуск | `POST /v1/sources/{source_id}/synchronizations` | `start_synchronization_runs` | content-manager |
| історія | `GET /v1/sources/{source_id}/synchronizations` | `start_synchronization_runs` | content-manager |
| статус | `GET /v1/jobs/{job_id}` | `start_synchronization_runs` | content-manager |
| повтор, скасування | `POST /v1/jobs/{job_id}/retry`, `/cancel` | `recover_jobs` | administrator |

Повтор і скасування лишаються за адміністратором, бо це рішення відновлення для Jobs усіх типів (M08-T08), а не щоденна синхронізація.

«Належність» тут означає, що Job належить своєму Source. Історія Source містить лише Jobs типу `synchronize_source`, націлені на цей Source: не Jobs іншого Source і не Job очищення (purge), який випадково має той самий ID. Статус і так показує лише Jobs синхронізації й відповідає тим самим 404 для Job іншого типу. А подія аудиту кожної поставленої в чергу Job називає оператора, що її попросив.

### Відомі обмеження

- Запуск не може вибрати повний чи інкрементальний прохід; вирішує worker, як і для запланованого Job.
- `POST .../synchronizations` не приймає тіла й ігнорує рядок запиту, тоді як `GET` відхиляє невідомі параметри.
- Курсор історії — лише позиція: переданий в історію іншого Source, він почне той список після того самого ID, а це не покаже нічого, чого клієнт і так не може читати.
- Порядок UUIDv7 — це порядок створення за годинником машини, що створила ID; дві машини з розбіжними годинниками можуть упорядкувати трохи інакше, але сторінка ніколи не повторить і не пропустить Job.
- Без publisher-а або коли Redis відмовляє, Job чекає на наступний прохід `make worker-relay`; ніщо не запускає relay постійно, доки систему не зібрано разом (M12-T03).
- Клієнт, відхилений за роллю, отримує 403, який не записує жодна подія аудиту, як і на кожному маршруті.

## Крок 1 — Прочитати, передбачити і побачити, як перевірка падає

Без коду в цьому кроці.

Прочитайте файл завдання, потім блоки 1–6. До початку програмування запишіть відповіді:

- Чотири запуски для одного Source приходять одночасно. Скільки Jobs збережеться, скільки подій аудиту і які статуси повернуться?
- Job Source остаточно впав, потім Source вимкнули. Що відповість `POST /v1/jobs/{job_id}/retry`?
- Історія показує J4 і J3 на сторінці 1, потім хтось повторює J1. Чи покаже сторінка 2 J1?

Потім запустіть файл тестів, який створює урок, щоб побачити, що його ще немає:

```bash
uv run pytest tests/test_synchronization_administration.py -q
```

Очікувано: `ERROR: file or directory not found: tests/test_synchronization_administration.py`, `no tests ran`, код виходу 4.

## Крок 2 — Читати Jobs Source та їхні найновіші спроби

Крок із кодом.

**Простими словами:** історії потрібні два читання, яких у проєкті ще немає. Одне повертає Jobs синхронізації одного Source сторінками, від найновіше створених. Друге повертає найновішу спробу багатьох Jobs одним запитом, тож сторінка з п'ятдесяти Jobs коштує два запити, а не п'ятдесят один.

| Функція | Файл | Що робить |
|---|---|---|
| `load_target_jobs` | `persistence/jobs.py` | Jobs одного типу й цілі, `ORDER BY job_id DESC`, після ID Job |
| `load_latest_attempts` | `persistence/job_attempts.py` | `SELECT DISTINCT ON (job_id) ... ORDER BY job_id, attempt DESC` |
| `ix_jobs_synchronization_target` | `schema.py` і міграція | `(target_id, job_id)` для Jobs синхронізації |

У `src/knowledge_service/persistence/jobs.py` додайте перед `lock_running_job`:

```python
async def load_target_jobs(
    connection: AsyncConnection,
    *,
    kind: JobKind,
    target_id: UUID,
    limit: int,
    after: JobId | None = None,
) -> tuple[Job, ...]:
    """Up to `limit` Jobs of `kind` for one target, the newest made first.

    A Job's ID is a UUIDv7, which starts with the time it was made and never
    changes; `queued_at` does, on every retry. So the order and `after`, the
    Job the list starts after, use the ID alone, and a retry between two
    pages never moves a Job past the place a page ended.
    """
    statement = sa.select(jobs).where(
        jobs.c.kind == kind.value, jobs.c.target_id == target_id
    )
    if after is not None:
        statement = statement.where(jobs.c.job_id < after.value)
    statement = statement.order_by(jobs.c.job_id.desc()).limit(limit)
    records = (await connection.execute(statement)).mappings().all()
    return tuple(job_from_record(dict(record)) for record in records)
```

У `src/knowledge_service/persistence/job_attempts.py` додайте цей імпорт поруч з іншими імпортами `sqlalchemy`:

```python
from sqlalchemy.dialects import postgresql
```

і додайте перед `begin_attempt`:

```python
async def load_latest_attempts(
    connection: AsyncConnection, *, job_ids: Iterable[JobId]
) -> dict[JobId, JobProgress]:
    """The newest attempt of each of `job_ids` that ever started one, in one query."""
    values = [job_id.value for job_id in job_ids]
    if not values:
        return {}
    statement = (
        sa.select(job_attempts)
        .where(job_attempts.c.job_id.in_(values))
        .ext(postgresql.distinct_on(job_attempts.c.job_id))
        .order_by(job_attempts.c.job_id, job_attempts.c.attempt.desc())
    )
    records = (await connection.execute(statement)).mappings().all()
    return {
        JobId(cast(UUID, record["job_id"])): _progress_from(dict(record))
        for record in records
    }
```

`DISTINCT ON` залишає перший рядок кожного `job_id` у порядку `ORDER BY`, тобто найбільшу спробу. `postgresql.distinct_on` — це поточний запис у SQLAlchemy; передати стовпець у `.distinct()` ще можна, але це застаріло.

Дайте історії індекс у порядку ID. У `src/knowledge_service/persistence/schema.py` індекс цілі синхронізації в `jobs` стає таким:

```python
    # Finds each Source's synchronization Jobs for the schedule, and pages a
    # Source's history in ID order without sorting every Job it ever had.
    sa.Index(
        "ix_jobs_synchronization_target",
        "target_id",
        "job_id",
        postgresql_where=sa.text("kind = 'synchronize_source'"),
    ),
    sa.Index(
        "ix_jobs_due_retry",
        "retry_at",
        "job_id",
        postgresql_where=sa.text("retry_at is not null"),
    ),
    # Finds queued and running Jobs that recovery may find stuck.
```

і створіть `migrations/versions/b4c5d6e7f8a9_synchronization_history_index.py`:

```python
"""synchronization history index

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-11-27 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c5d6e7f8a9"
down_revision: str | Sequence[str] | None = "a3b4c5d6e7f8"

SYNCHRONIZATIONS = sa.text("kind = 'synchronize_source'")


def upgrade() -> None:
    # The ID as a second column lets a history page read the index in order
    # instead of sorting every synchronization Job of the Source.
    op.drop_index("ix_jobs_synchronization_target", table_name="jobs")
    op.create_index(
        "ix_jobs_synchronization_target",
        "jobs",
        ["target_id", "job_id"],
        postgresql_where=SYNCHRONIZATIONS,
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_synchronization_target", table_name="jobs")
    op.create_index(
        "ix_jobs_synchronization_target",
        "jobs",
        ["target_id"],
        postgresql_where=SYNCHRONIZATIONS,
    )
```

Індекс і далі починається з `target_id`, тож пошуки планувальника користуються ним, як і раніше.

Перевірка: `uv run pyright src/knowledge_service/persistence` показує 0 помилок.

## Крок 3 — Написати модуль адміністрування синхронізації

Крок із кодом.

**Простими словами:** цей файл запускає синхронізацію й читає історію. Запуск блокує Source так, як це робить планувальник, відхиляє Source, якого немає чи який вимкнений, і просить один Job, отримуючи відкритий, якщо такий є. Він записує, хто попросив, лише коли Job справді поставили в чергу, зберігає все і лише потім пробує надіслати повідомлення. Історія читає сторінку Jobs Source з їхніми найновішими спробами, все з одного узгодженого знімка.

| Частина | Що робить |
|---|---|
| `SourceDisabled` | у `source_administration.py`: Source відсутній або вимкнений |
| `SynchronizationStart` | звіт про Job і те, чи цей запуск поставив його в чергу |
| `start_synchronization` | спільне блокування, відмови 404 чи 409, `enqueue_job`, `job.queued` лише коли поставлено |
| `load_synchronization_history` | Source має існувати; Jobs і найновіші спроби в одному знімку `REPEATABLE READ` |
| `history_cursor`, `history_position` | курсор містить лише ID останнього Job, в одному написанні |
| `DatabaseSynchronizations` | сховище, яким користується застосунок: запуск, потім одна спроба публікації; історія |

У `src/knowledge_service/source_administration.py` додайте перед `SourceNeedsRepair`:

```python
class SourceDisabled(Exception):
    """The Source is turned off, so nothing may synchronize it.

    A retry also raises it for a Job whose Source is gone: a retry names the
    Job, not the Source, so its missing Source is not a missing resource.
    """
```

Вона живе поруч з іншими помилками Source, бо повтор із кроку 4 теж її кидає.

Створіть `src/knowledge_service/synchronization_administration.py`:

```python
"""Starting a Source's synchronization by hand, and reading its history.

A start asks for the same work the scheduler asks for: one synchronization
Job for one Source. While the Source has an open Job, queued, running, or
failed with a retry planned, a start returns that Job instead of queueing a
second, so pressing the button twice, or two operators at once, queue one
Job. Only an enabled Source can be started; the check locks the Source's
row the way the scheduler does, so disabling it waits for the start to
commit, and a start never queues a Job for a Source disabled after it read.

Everything an operator sees comes from PostgreSQL, the record of what was
asked for and how it ended; Redis only delivers. A start commits its Job
and its audit event first, then tries once to publish the Job; if that
fails, the relay publishes it later (`job_dispatch`).
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

import sqlalchemy as sa

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import AuditEventId, JobId, SourceId, UserId
from knowledge_service.job_dispatch import JobPublisher, publish_job
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import Job, JobKind
from knowledge_service.pagination import InvalidPage, decode_cursor, encode_cursor
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.job_attempts import (
    load_latest_attempt,
    load_latest_attempts,
)
from knowledge_service.persistence.jobs import enqueue_job, load_target_jobs
from knowledge_service.persistence.sources import (
    load_source_record,
    lock_enabled_sources,
)
from knowledge_service.source_administration import SourceDisabled, SourceNotFound

logger = logging.getLogger(__name__)

MAX_HISTORY_PAGE: Final = 500
_HISTORY_LIST = "synchronizations"
_HISTORY_CURSOR_KEYS = frozenset({"job_id"})


@dataclass(frozen=True, slots=True)
class SynchronizationStart:
    """The Job a start answers with, and whether this start queued it."""

    report: JobReport
    queued: bool


async def start_synchronization(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> SynchronizationStart:
    """Queue a synchronization Job for an enabled Source, or return its open one.

    Raises `SourceNotFound` for a missing Source and `SourceDisabled` for one
    turned off. Only a start that queues a Job records `job.queued`.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        enabled = await lock_enabled_sources(connection, source_ids={source_id})
        if source_id not in enabled:
            if await load_source_record(connection, source_id=source_id) is None:
                raise SourceNotFound("no source has this ID")
            raise SourceDisabled("the source is disabled")
        enqueued = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=source_id.value,
                queued_at=now,
            ),
        )
        job = enqueued.job
        if enqueued.created:
            await record_audit_event(
                connection,
                _queued_event(job, operator=operator, at=now, request_id=request_id),
            )
        latest = await load_latest_attempt(connection, job_id=job.job_id)
    return SynchronizationStart(
        report=JobReport(job=job, latest_attempt=latest), queued=enqueued.created
    )


async def load_synchronization_history(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    limit: int,
    after: JobId | None = None,
) -> tuple[JobReport, ...]:
    """A Source's synchronization Jobs, newest made first, each with its newest attempt.

    Raises `SourceNotFound` for a Source that does not exist. Jobs and
    attempts are read from one snapshot, so a Job that finishes meanwhile is
    never shown as running with an ended attempt.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_HISTORY_PAGE:
        raise ValueError(f"limit must be an integer from 1 to {MAX_HISTORY_PAGE}")
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        )
        if await load_source_record(connection, source_id=source_id) is None:
            raise SourceNotFound("no source has this ID")
        found = await load_target_jobs(
            connection,
            kind=JobKind.SYNCHRONIZE_SOURCE,
            target_id=source_id.value,
            limit=limit,
            after=after,
        )
        latest = await load_latest_attempts(
            connection, job_ids=[job.job_id for job in found]
        )
    return tuple(
        JobReport(job=job, latest_attempt=latest.get(job.job_id)) for job in found
    )


def history_cursor(job: Job) -> str:
    """The cursor of the history page that starts after `job`."""
    return encode_cursor(_HISTORY_LIST, {"job_id": str(job.job_id.value)})


def history_position(cursor: str) -> JobId:
    """Which Job a `history_cursor` says the next page starts after."""
    position = decode_cursor(cursor, listing=_HISTORY_LIST, keys=_HISTORY_CURSOR_KEYS)
    job_text = position["job_id"]
    try:
        job_id = UUID(job_text)
    except ValueError:
        raise InvalidPage("the cursor is not one this API gave") from None
    if str(job_id) != job_text:
        raise InvalidPage("the cursor is not one this API gave")
    return JobId(job_id)


def _queued_event(
    job: Job, *, operator: UserId, at: datetime, request_id: UUID | None
) -> AuditEvent:
    """Who asked for which Job; the Source is the Job's target, never its settings."""
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=at,
        actor_id=operator,
        action=AuditAction.JOB_QUEUED,
        target_kind=AuditTargetKind.JOB,
        target_id=job.job_id.value,
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=request_id,
        run_id=None,
        metadata={"kind": job.kind.value},
    )


def _require_utc(now: datetime) -> None:
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DatabaseSynchronizations:
    """Synchronization starts and history in PostgreSQL, at the clock's time.

    With a `publisher`, a start then tries once to send its Job's message;
    without one, or when the send fails, the relay sends it later.
    """

    runtime: DatabaseRuntime
    publisher: JobPublisher | None = None
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def start(
        self, *, source_id: SourceId, operator: UserId, request_id: UUID
    ) -> SynchronizationStart:
        now = self.clock()
        started = await start_synchronization(
            self.runtime,
            source_id=source_id,
            operator=operator,
            now=now,
            request_id=request_id,
        )
        if self.publisher is not None:
            try:
                await publish_job(
                    self.runtime,
                    job_id=started.report.job.job_id,
                    publisher=self.publisher,
                    now=now,
                )
            except Exception as error:
                # The Job is committed; the relay will publish it. Only the
                # type: a message could quote a URL or a secret.
                logger.warning(
                    "synchronization stored but not published: %s",
                    type(error).__name__,
                )
        return started

    async def history(
        self,
        *,
        source_id: SourceId,
        limit: int,
        after: JobId | None = None,
    ) -> tuple[JobReport, ...]:
        return await load_synchronization_history(
            self.runtime, source_id=source_id, limit=limit, after=after
        )
```

`start_synchronization` читає останню спробу в тій самій транзакції, бо запуск, що знайшов Job, який виконується, має показати, як далеко той просунувся.

Перевірка: `uv run pyright src/knowledge_service/synchronization_administration.py` показує 0 помилок.

## Крок 4 — Змусити повтор перевіряти Source

Крок із кодом.

**Простими словами:** повтор Job синхронізації адміністратором тепер перевіряє його Source точно так, як запуск. Якщо Source відсутній чи вимкнений, у черзі нічого не з'являється, а відповідь — той самий 409, що й у запуску.

У `src/knowledge_service/job_decisions.py` змініть імпорти:

```python
from knowledge_service.identifiers import AuditEventId, JobId, SourceId, UserId
from knowledge_service.jobs import InvalidJob, Job, JobKind
from knowledge_service.persistence.jobs import load_job, save_job
from knowledge_service.persistence.sources import lock_enabled_sources
from knowledge_service.source_administration import SourceDisabled
```

і в `retry_job` розширте docstring і перевірте Source після стану самого Job:

```python
    refused with `JobIntentAlreadyOpen`, so one piece of work never has two
    open Jobs. A synchronization Job whose Source is missing or disabled is
    refused with `SourceDisabled`, after the Job's own status is checked.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        job = await _lock(connection, job_id=job_id)
        # The Job's status first: a retry that never fits says so, whatever
        # state its Source is in.
```

Спершу Job блокується і перевіряється його стан, тож повтор, що ніколи не пасує, так і каже; лише потім Source блокується спільно. Вимкнення, що тримає `FOR UPDATE` на Source, змушує повтор чекати, тож повтор комітиться або до вимкнення, або взагалі ні.

Перевірка: `uv run pytest tests/test_job_recovery_integration.py -q -m integration` досі проходить: його Jobs належать увімкненому Source.

## Крок 5 — Додати модель сторінки історії

Крок із кодом.

**Простими словами:** сторінка історії має ту саму форму, що й кожен список в API: елементи, а потім курсор наступної сторінки або нічого.

У `src/knowledge_service/contracts.py` додайте перед `OperatorJob`:

```python
class SynchronizationHistory(BaseModel):
    """A page of a Source's synchronization Jobs, newest first.

    `next_cursor` starts the next page; None on the last one.
    """

    items: list[SynchronizationJobReport]
    next_cursor: str | None
```

Кожен елемент — це `SynchronizationJobReport`, яким уже відповідає `GET /v1/jobs/{job_id}`, тож клієнт читає Job однаково з обох маршрутів.

## Крок 6 — Обслуговувати два маршрути

Крок із кодом.

**Простими словами:** застосунок отримує запуск та історію. Обидва спершу перевіряють клієнта і його роль, потім ID Source і сторінку, потім звертаються до сховища. Запуск відповідає 202, коли поставив Job у чергу, і 200, коли знайшов відкритий, в обох випадках з адресою Job. Маршрут повтору вчиться перетворювати нову відмову на той самий 409, що дає запуск.

| Маршрут | Відповідь |
|---|---|
| `POST /v1/sources/{source_id}/synchronizations` | 202 або 200 з `Location`; 404; 409 `source_disabled`; 422 поганий ID |
| `GET /v1/sources/{source_id}/synchronizations` | 200 сторінка; 404; 422 поганий ID, `limit`, `cursor` чи параметр |
| `POST /v1/jobs/{job_id}/retry` | також 409 `source_disabled` |

У `src/knowledge_service/app.py` імпорт з `knowledge_service.contracts` отримує `SynchronizationHistory`:

```python
from knowledge_service.contracts import (
    ErrorResponse,
    JobAttemptProgress,
    JobsNeedingDecision,
    OperatorJob,
    Question,
    SourceChanges,
    SourcePage,
    SourceRegistration,
    SourceView,
    SynchronizationHistory,
    SynchronizationJobReport,
)
```

`SourceDisabled` додається до імпорту з `source_administration`, а за ним іде новий імпорт:

```python
from knowledge_service.source_administration import (
    InvalidSourceSettings,
    SourceDefinition,
    SourceDisabled,
    SourceExists,
    SourceNeedsRepair,
    SourceNotFound,
    source_cursor,
    source_position,
)
from knowledge_service.synchronization_administration import (
    SynchronizationStart,
    history_cursor,
    history_position,
)
```

Додайте протокол сховища перед `source_view`:

```python
class Synchronizations(Protocol):
    """A Source's synchronizations as an operator starts and follows them.

    `DatabaseSynchronizations` is the implementation. Both raise
    `SourceNotFound` for a missing Source; `start` raises `SourceDisabled`
    for one turned off, and returns the open Job when there is one.
    """

    async def start(
        self, *, source_id: SourceId, operator: UserId, request_id: UUID
    ) -> SynchronizationStart: ...

    async def history(
        self, *, source_id: SourceId, limit: int, after: JobId | None = None
    ) -> tuple[JobReport, ...]: ...
```

Дайте `create_app` сховище, після `sources`:

```python
    synchronizations: Synchronizations | None = None,
```

У `decide`, спільному тілі повтору й скасування, додайте одну відмову після `JobStillRunning`:

```python
        except JobStillRunning:
            return _conflict("job_running", "The job is still running", request_id)
        except SourceDisabled:
            return _source_disabled(request_id)
```

Додайте два маршрути в кінець `create_app`, перед `return app`:

```python
    @app.post(
        "/v1/sources/{source_id}/synchronizations",
        status_code=202,
        response_model=SynchronizationJobReport,
        responses={
            200: {
                "model": SynchronizationJobReport,
                "description": "The Source's open Job; nothing new was queued",
            },
            **error_responses(404, 409),
        },
    )
    async def start_synchronization(
        request: Request, source_id: Identifier
    ) -> JSONResponse:
        # Who, then whether they may, then which Source.
        caller = await authenticate(request)
        require(caller.roles, Operation.START_SYNCHRONIZATION_RUNS)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if synchronizations is None:
            return _jobs_unavailable(request_id)
        try:
            started = await synchronizations.start(
                source_id=SourceId.parse(source_id),
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except SourceNotFound:
            return _source_not_found(request_id)
        except SourceDisabled:
            return _source_disabled(request_id)
        except Exception as error:
            logger.error("synchronization start failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        job_id = started.report.job.job_id.value
        # 202: queued now; 200: the open Job, so a repeated start is harmless.
        return JSONResponse(
            status_code=202 if started.queued else 200,
            content=synchronization_job_report(started.report).model_dump(mode="json"),
            headers={"Location": f"/v1/jobs/{job_id}"},
        )

    @app.get(
        "/v1/sources/{source_id}/synchronizations",
        response_model=SynchronizationHistory,
        responses=error_responses(404),
        openapi_extra={"parameters": PAGE_PARAMETERS},
    )
    async def list_synchronizations(
        request: Request, source_id: Identifier
    ) -> SynchronizationHistory | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.START_SYNCHRONIZATION_RUNS)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        try:
            limit, cursor = page_request(request)
            after = None if cursor is None else history_position(cursor)
        except InvalidPage:
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if synchronizations is None:
            return _jobs_unavailable(request_id)
        try:
            # One more than the page, to learn whether another page follows.
            reports = await synchronizations.history(
                source_id=SourceId.parse(source_id), limit=limit + 1, after=after
            )
        except asyncio.CancelledError:
            raise
        except SourceNotFound:
            return _source_not_found(request_id)
        except Exception as error:
            logger.error("synchronization history failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        page = reports[:limit]
        return SynchronizationHistory(
            items=[synchronization_job_report(report) for report in page],
            next_cursor=history_cursor(page[-1].job) if len(reports) > limit else None,
        )
```

і спільну відмову перед `_conflict`:

```python
def _source_disabled(request_id: UUID) -> JSONResponse:
    return _conflict("source_disabled", "The Source is missing or disabled", request_id)
```

Маршрут запуску будує власну `JSONResponse`, бо статус залежить від результату: `status_code=202` у FastAPI лише задає значення за замовчуванням і документує його, а випадок 200 документується через `responses`.

Перевірка: `uv run pyright src/knowledge_service/app.py` показує 0 помилок.

## Крок 7 — Позначити маршрути обслуговуваними й оновити наявні тести

Крок із кодом.

**Простими словами:** таблиця маршрутів досі вважає два маршрути запланованими, і тести OpenAPI та ролей тепер падають, бо застосунок їх обслуговує. Цей крок позначає їх обслуговуваними, каже тесту OpenAPI, які додаткові помилки вони описують, і дає тесту ролей фейкове сховище синхронізацій, щоб він довів: кожен маршрут пускає рівно роль content-manager.

У `src/knowledge_service/api_routes.py` змініть `_PLANNED` на `_SERVED` у двох рядках із завданням `"M09-T03"`:

```python
    Route(
        _POST,
        "/v1/sources/{source_id}/synchronizations",
        Operation.START_SYNCHRONIZATION_RUNS,
        _SERVED,
        "M09-T03",
        "Start a synchronization, or return the one already open",
    ),
    Route(
        _GET,
        "/v1/sources/{source_id}/synchronizations",
        Operation.START_SYNCHRONIZATION_RUNS,
        _SERVED,
        "M09-T03",
        "List a Source's synchronization Jobs",
        paginated=True,
    ),
```

У `tests/test_api_routes.py` додайте до `ROUTE_ERRORS`:

```python
    ("POST", "/v1/sources/{source_id}/synchronizations"): {404, 409},
    ("GET", "/v1/sources/{source_id}/synchronizations"): {404},
```

У `tests/test_app_authentication.py` додайте імпорт поруч з іншими імпортами `knowledge_service`:

```python
from knowledge_service.synchronization_administration import SynchronizationStart
```

додайте фейкове сховище й підключіть його до `full_app`:

```python
class Synchronizing:
    """A synchronization store that records each request, and finds no Source."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def start(self, **request: Any) -> SynchronizationStart:
        self.calls.append(request)
        raise SourceNotFound("no source has this ID")

    async def history(self, **request: Any) -> tuple[JobReport, ...]:
        self.calls.append(request)
        raise SourceNotFound("no source has this ID")


def full_app(
    source: Source,
    tokens: dict[str, Any] | None = None,
    conversations: Deleting | None = None,
    jobs: Reading | None = None,
    decisions: Deciding | None = None,
    sources: Configuring | None = None,
    synchronizations: Synchronizing | None = None,
) -> Any:
    """The app with every endpoint wired, so every route is real."""
    return create_app(
        streamer=streamer(Model(), source=source),
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
        conversations=conversations or Deleting(),
        job_reports=jobs or Reading(),
        job_decisions=decisions or Deciding(),
        sources=sources or Configuring(),
        synchronizations=synchronizations or Synchronizing(),
    )
```

і в `test_each_protected_route_admits_exactly_the_roles_that_permit_it` створюйте застосунок з новим сховищем і рахуйте його виклики:

```python
    jobs, decisions, sources = Reading(), Deciding(), Configuring()
    synchronizations = Synchronizing()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(
        source,
        {"token": token},
        conversations,
        jobs,
        decisions,
        sources,
        synchronizations,
    )
```

```python
    reached = (
        source.readers
        or conversations.calls
        or jobs.calls
        or decisions.calls
        or sources.calls
        or synchronizations.calls
    )
```

Перевірка:

```bash
uv run pytest tests/test_api_routes.py tests/test_app_authentication.py -q
```

Очікувано: усе проходить.

## Крок 8 — Написати нові тести

Крок із кодом.

**Простими словами:** два нові файли тестів доводять контракт. Перший працює без сервісів: викликає маршрути через фейкові сховища, перевіряє 202 проти 200, сторінки й курсори історії, те, що роль перевіряється раніше за ID чи сторінку, і закріплює кожну відмову п'яти операцій. Другий працює на справжньому PostgreSQL, де справді відбуваються індекс відкритих Jobs, блокування, публікація, повтори й сторінки.

| Файл | Що доводить |
|---|---|
| `tests/test_synchronization_administration.py` | 202 з `Location`; 200 з тим самим Job; погані ID і сторінки відхиляються до сховища; роль раніше за ID чи сторінку; жодного витоку, коли запуск чи читання історії ламається; сторінки до кінця, повна остання сторінка; написання курсора; один незмінний статус, код і повідомлення на кожну відмову |
| `tests/test_synchronization_administration_integration.py` | один Job і одна подія `job.queued` з оператором; чотири одночасні запуски через HTTP дають один 202 і три 200 для одного Job; Jobs, що виконуються чи мають запланований повтор, повертаються з 200; після успіху, остаточного падіння чи скасування запускається новий Job з 202; повтор старої Job, поки відкрита нова, дає 409; вимкнений чи відсутній Source нічого не ставить; публікація один раз, а відхилена відправка нічого не втрачає; історія містить лише Jobs синхронізації свого Source; 404 для історії; сторінки не повторюють і не пропускають; показується найновіша спроба; Job, повторений між сторінками, з'являється один раз; повтор відхиляється для вимкненого Source, але спершу `decision_not_allowed`, коли стан Job не пасує; вимкнення чекає на запуск, що тримає Source; індекс історії — `(target_id, job_id)` |

Створіть `tests/test_synchronization_administration.py`:

```python
"""Synchronization administration over HTTP, with fake stores.

A start answers 202 when it queued a Job and 200 when the Source already had
an open one, and both name the Job. The history is a Source's Jobs a page
at a time. Every refusal of the five operations, start, status, history,
retry, and cancel, has one fixed status, code, and message: a client may
branch on them, so a test pins each one.
"""

import logging
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import httpx
import pytest

from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.app import (
    JobDecisions,
    JobReports,
    Synchronizations,
    create_app,
)
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.identity.context import Caller
from knowledge_service.job_decisions import JobIntentAlreadyOpen, JobNotFound
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import (
    IllegalJobTransition,
    Job,
    JobKind,
    JobNotRetryable,
    JobStillRunning,
)
from knowledge_service.pagination import InvalidPage, encode_cursor
from knowledge_service.roles import ApplicationRole
from knowledge_service.source_administration import SourceDisabled, SourceNotFound
from knowledge_service.synchronization_administration import (
    SynchronizationStart,
    history_cursor,
    history_position,
)

MARKER = "SECRET-MARKER-7f3a"
QUEUED_AT = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
SOURCE_ID = SourceId.new()


def queued(queued_at: datetime = QUEUED_AT) -> Job:
    return Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=SOURCE_ID.value,
        queued_at=queued_at,
    )


class Synchronizing:
    """Holds a Source's Jobs newest first; records each request; can refuse."""

    def __init__(self, jobs: list[Job] | None = None) -> None:
        self.jobs = jobs or []
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None
        self.open: Job | None = None

    async def start(self, **request: Any) -> SynchronizationStart:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        if self.open is not None:
            return SynchronizationStart(JobReport(self.open, None), queued=False)
        self.open = queued()
        return SynchronizationStart(JobReport(self.open, None), queued=True)

    async def history(
        self,
        *,
        source_id: SourceId,
        limit: int,
        after: JobId | None = None,
    ) -> tuple[JobReport, ...]:
        self.calls.append({"source_id": source_id, "limit": limit, "after": after})
        if self.error is not None:
            raise self.error
        start = 0
        if after is not None:
            start = next(
                (i for i, j in enumerate(self.jobs) if j.job_id.value < after.value),
                len(self.jobs),
            )
        return tuple(JobReport(job, None) for job in self.jobs[start : start + limit])


class Refusing:
    """A Job store whose every answer is the error a test sets."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def load(self, **_request: Any) -> None:
        if not isinstance(self.error, JobNotFound):
            raise self.error

    async def retry(self, **_request: Any) -> Job:
        raise self.error

    async def cancel(self, **_request: Any) -> Job:
        raise self.error


class Staff:
    """Gives every request a caller with every role but `user`."""

    async def authenticate(self, headers: list[str]) -> Caller:
        return Caller(
            reader=READER,
            roles=frozenset(
                {ApplicationRole.CONTENT_MANAGER, ApplicationRole.ADMINISTRATOR}
            ),
        )


async def call(
    method: str,
    path: str,
    *,
    synchronizations: Synchronizing | None = None,
    jobs: Refusing | None = None,
) -> httpx.Response:
    app = create_app(
        authenticator=Staff(),
        synchronizations=cast(Synchronizations, synchronizations or Synchronizing()),
        job_reports=cast(JobReports, jobs or Refusing(JobNotFound("x"))),
        job_decisions=cast(JobDecisions, jobs or Refusing(JobNotFound("x"))),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.request(method, path, headers={"Authorization": "Bearer t"})


START = f"/v1/sources/{SOURCE_ID.value}/synchronizations"


async def test_a_start_that_queues_a_job_answers_202_with_where_it_lives() -> None:
    synchronizations = Synchronizing()

    response = await call("POST", START, synchronizations=synchronizations)

    body = response.json()
    assert response.status_code == 202
    assert response.headers["location"] == f"/v1/jobs/{body['job_id']}"
    assert (body["status"], body["source_id"]) == ("queued", str(SOURCE_ID.value))
    assert synchronizations.calls[0]["operator"] == READER.user_id


async def test_a_second_start_answers_200_with_the_same_job() -> None:
    synchronizations = Synchronizing()

    first = await call("POST", START, synchronizations=synchronizations)
    second = await call("POST", START, synchronizations=synchronizations)

    assert (first.status_code, second.status_code) == (202, 200)
    assert second.json() == first.json()
    assert second.headers["location"] == first.headers["location"]


@pytest.mark.parametrize(
    "source_id", ["not-an-id", "{00000000-0000-0000-0000-000000000001}", "0" * 32]
)
@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_an_id_in_another_spelling_is_refused_before_the_store(
    method: str, source_id: str
) -> None:
    synchronizations = Synchronizing()

    response = await call(
        method,
        f"/v1/sources/{source_id}/synchronizations",
        synchronizations=synchronizations,
    )

    assert response.status_code == 422
    assert synchronizations.calls == []


@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_a_store_failure_names_nothing_to_the_client_or_the_log(
    method: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    synchronizations = Synchronizing()
    synchronizations.error = RuntimeError(f"query failed for {MARKER}")

    response = await call(method, START, synchronizations=synchronizations)

    assert response.status_code == 500
    assert MARKER not in response.text + caplog.text


@pytest.mark.parametrize(
    ("method", "target"),
    [
        ("POST", "/v1/sources/not-an-id/synchronizations"),
        ("GET", "/v1/sources/not-an-id/synchronizations"),
        ("GET", START + "?limit=0"),
        ("GET", START + "?cursor=%25%25"),
    ],
)
@pytest.mark.parametrize(
    ("headers", "status"),
    [({}, 401), (AUTHORIZATION, 403)],
    ids=["no token", "user role"],
)
async def test_the_role_is_checked_before_the_id_or_page_is_read(
    method: str, target: str, headers: dict[str, str], status: int
) -> None:
    synchronizations = Synchronizing()
    app = create_app(
        authenticator=TRUSTED,
        synchronizations=cast(Synchronizations, synchronizations),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.request(method, target, headers=headers)

    assert response.status_code == status
    assert synchronizations.calls == []


# The history.


def history(n: int) -> list[Job]:
    """`n` Jobs, the newest made first, as the store orders them."""
    return sorted(
        (queued() for _ in range(n)), key=lambda j: j.job_id.value, reverse=True
    )


async def test_pages_of_history_follow_each_other_to_the_end() -> None:
    jobs = history(5)
    synchronizations = Synchronizing(jobs)
    seen: list[str] = []
    query = "?limit=2"

    for _ in range(10):
        page = (
            await call("GET", START + query, synchronizations=synchronizations)
        ).json()
        seen += [item["job_id"] for item in page["items"]]
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    assert seen == [str(job.job_id.value) for job in jobs]
    assert [c["limit"] for c in synchronizations.calls] == [3, 3, 3]


async def test_a_full_last_page_of_history_says_no_page_follows() -> None:
    synchronizations = Synchronizing(history(4))

    first = (
        await call("GET", START + "?limit=2", synchronizations=synchronizations)
    ).json()
    cursor = first["next_cursor"]
    second = (
        await call(
            "GET", f"{START}?limit=2&cursor={cursor}", synchronizations=synchronizations
        )
    ).json()

    assert len(second["items"]) == 2
    assert second["next_cursor"] is None


@pytest.mark.parametrize(
    "query",
    [
        "?limit=0",
        "?offset=2",
        "?cursor=" + encode_cursor("sources", {"created_at": "x", "source_id": "y"}),
    ],
)
async def test_a_bad_page_request_is_refused_before_the_store(query: str) -> None:
    synchronizations = Synchronizing()

    response = await call("GET", START + query, synchronizations=synchronizations)

    assert response.status_code == 422
    assert synchronizations.calls == []


def test_a_history_cursor_starts_after_its_job() -> None:
    job = queued()

    assert history_position(history_cursor(job)) == job.job_id


@pytest.mark.parametrize(
    "position",
    [
        {"job_id": "{00000000-0000-0000-0000-000000000001}"},
        {"job_id": "00000000-0000-0000-0000-00000000000A"},
        {"job_id": "not an id"},
        {"job_id": str(uuid4()), "queued_at": "2026-11-27T09:00:00+00:00"},
    ],
)
def test_a_history_cursor_in_another_spelling_is_refused(
    position: dict[str, str],
) -> None:
    with pytest.raises(InvalidPage):
        history_position(encode_cursor("synchronizations", position))


# Stable error responses: what a client may branch on, pinned.

JOB = f"/v1/jobs/{uuid4()}"
ERRORS = [
    (
        "start, missing Source",
        "POST",
        START,
        "synchronizations",
        SourceNotFound("x"),
        (404, "source_not_found", "No such Source"),
    ),
    (
        "start, disabled Source",
        "POST",
        START,
        "synchronizations",
        SourceDisabled("x"),
        (409, "source_disabled", "The Source is missing or disabled"),
    ),
    (
        "history, missing Source",
        "GET",
        START,
        "synchronizations",
        SourceNotFound("x"),
        (404, "source_not_found", "No such Source"),
    ),
    (
        "status, missing Job",
        "GET",
        JOB,
        "jobs",
        JobNotFound("x"),
        (404, "job_not_found", "No such synchronization job"),
    ),
    (
        "retry, missing Job",
        "POST",
        JOB + "/retry",
        "jobs",
        JobNotFound("x"),
        (404, "job_not_found", "No such job"),
    ),
    (
        "retry, another Job open",
        "POST",
        JOB + "/retry",
        "jobs",
        JobIntentAlreadyOpen("x"),
        (409, "job_already_open", "Another job for this work is open"),
    ),
    (
        "retry, finished Job",
        "POST",
        JOB + "/retry",
        "jobs",
        JobNotRetryable("x"),
        (409, "decision_not_allowed", "This decision does not fit the job's status"),
    ),
    (
        "retry, disabled Source",
        "POST",
        JOB + "/retry",
        "jobs",
        SourceDisabled("x"),
        (409, "source_disabled", "The Source is missing or disabled"),
    ),
    (
        "cancel, missing Job",
        "POST",
        JOB + "/cancel",
        "jobs",
        JobNotFound("x"),
        (404, "job_not_found", "No such job"),
    ),
    (
        "cancel, running Job",
        "POST",
        JOB + "/cancel",
        "jobs",
        JobStillRunning("x"),
        (409, "job_running", "The job is still running"),
    ),
    (
        "cancel, finished Job",
        "POST",
        JOB + "/cancel",
        "jobs",
        IllegalJobTransition("x"),
        (409, "decision_not_allowed", "This decision does not fit the job's status"),
    ),
]


@pytest.mark.parametrize(
    ("method", "path", "store", "error", "expected"),
    [case[1:] for case in ERRORS],
    ids=[case[0] for case in ERRORS],
)
async def test_each_refusal_has_one_fixed_status_code_and_message(
    method: str,
    path: str,
    store: str,
    error: Exception,
    expected: tuple[int, str, str],
) -> None:
    synchronizations = Synchronizing()
    if store == "synchronizations":
        synchronizations.error = error

    response = await call(
        method,
        path,
        synchronizations=synchronizations,
        jobs=Refusing(error) if store == "jobs" else None,
    )

    body = response.json()
    assert (response.status_code, body["code"], body["message"]) == expected
    assert set(body) == {"code", "message", "request_id"}
```

Створіть `tests/test_synchronization_administration_integration.py`:

```python
"""Synchronization administration on real PostgreSQL.

A start is idempotent while the Source has an open Job, even when several
arrive at once; once the Job ends, in any final state, the next start queues
a new one. A Source's history holds its own synchronization Jobs only. A
start commits before it publishes, so a queue that refuses the message
loses nothing.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from authorization_scenarios import Person
from knowledge_service.app import create_app
from knowledge_service.audit import AuditAction
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.job_decisions import DatabaseJobDecisions, retry_job
from knowledge_service.job_dispatch import PublishFailed
from knowledge_service.job_reporting import DatabaseJobReports
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.persistence.audit import load_audit_events
from knowledge_service.persistence.job_attempts import begin_attempt
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
from knowledge_service.persistence.schema import jobs
from knowledge_service.persistence.sources import lock_enabled_sources
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings
from knowledge_service.source_administration import (
    SourceDefinition,
    SourceDisabled,
    SourceNotFound,
    register_source,
    set_source_enabled,
)
from knowledge_service.synchronization_administration import (
    DatabaseSynchronizations,
    start_synchronization,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
MARKER = "SECRET-MARKER-7f3a"
DANA = Person("dana", ())
OPERATOR = DANA.context().user_id
CALLERS = {
    "Bearer dana": Caller(
        reader=DANA.context(),
        roles=frozenset(
            {ApplicationRole.CONTENT_MANAGER, ApplicationRole.ADMINISTRATOR}
        ),
    ),
}


class Tokens:
    """Gives each test token the caller its claims would give."""

    async def authenticate(self, headers: list[str]) -> Caller:
        if len(headers) == 1 and headers[0] in CALLERS:
            return CALLERS[headers[0]]
        raise Unauthenticated("test", error=AuthenticationError.INVALID_TOKEN)


@dataclass
class Publisher:
    """Records each Job it sends; refuses all of them when told to."""

    refuse: bool = False
    kinds: frozenset[JobKind] = frozenset({JobKind.SYNCHRONIZE_SOURCE})
    sent: list[JobId] = field(default_factory=lambda: list[JobId]())

    def publish(self, job: Job) -> None:
        if self.refuse:
            raise PublishFailed(f"the queue is down near {MARKER}")
        self.sent.append(job.job_id)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def folder(runtime: DatabaseRuntime, name: str = "handbook") -> SourceId:
    record = await register_source(
        runtime,
        definition=SourceDefinition.of(
            kind="local_directory", location=f"/srv/{name}", display_name=name
        ),
        operator=OPERATOR,
        now=NOW,
    )
    return record.source_id


async def start(runtime: DatabaseRuntime, source_id: SourceId) -> Any:
    return await start_synchronization(
        runtime, source_id=source_id, operator=OPERATOR, now=NOW
    )


async def store(runtime: DatabaseRuntime, job: Job) -> None:
    async with runtime.transaction() as connection:
        await save_job(connection, job=job)


async def queued_events(runtime: DatabaseRuntime, job_id: JobId) -> list[Any]:
    async with runtime.transaction() as connection:
        events = await load_audit_events(connection, target_id=job_id.value, limit=10)
    return [(e.action, e.actor_id, dict(e.metadata)) for e in events]


def app(runtime: DatabaseRuntime, publisher: Publisher | None = None) -> Any:
    return create_app(
        authenticator=Tokens(),
        synchronizations=DatabaseSynchronizations(
            runtime, publisher=publisher, clock=lambda: NOW
        ),
        job_reports=DatabaseJobReports(runtime),
        job_decisions=DatabaseJobDecisions(runtime, clock=lambda: NOW),
    )


async def over_http(
    runtime: DatabaseRuntime,
    method: str,
    path: str,
    publisher: Publisher | None = None,
) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app(runtime, publisher)),
        base_url="http://t",
    ) as http:
        return await http.request(
            method, path, headers={"Authorization": "Bearer dana"}
        )


def path(source_id: SourceId) -> str:
    return f"/v1/sources/{source_id.value}/synchronizations"


async def test_a_start_queues_one_job_and_records_who_asked(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)

    response = await over_http(runtime, "POST", path(source_id))
    job_id = JobId.parse(response.json()["job_id"])

    assert response.status_code == 202
    async with runtime.transaction() as connection:
        stored = await load_job(connection, job_id=job_id)
    assert stored is not None
    assert (stored.kind, stored.target_id, stored.status) == (
        JobKind.SYNCHRONIZE_SOURCE,
        source_id.value,
        JobStatus.QUEUED,
    )
    assert await queued_events(runtime, job_id) == [
        (AuditAction.JOB_QUEUED, OPERATOR, {"kind": "synchronize_source"})
    ]


async def test_starts_at_once_queue_one_job_and_one_event(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)

    responses = await asyncio.gather(
        *(over_http(runtime, "POST", path(source_id)) for _ in range(4))
    )

    assert sorted(r.status_code for r in responses) == [200, 200, 200, 202]
    assert len({r.json()["job_id"] for r in responses}) == 1
    assert len({r.headers["location"] for r in responses}) == 1
    job_id = JobId.parse(responses[0].json()["job_id"])
    assert len(await queued_events(runtime, job_id)) == 1


async def test_a_repeated_start_over_http_answers_200_with_the_same_job(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)

    first = await over_http(runtime, "POST", path(source_id))
    second = await over_http(runtime, "POST", path(source_id))

    assert (first.status_code, second.status_code) == (202, 200)
    assert second.json()["job_id"] == first.json()["job_id"]


@pytest.mark.parametrize(
    "ending",
    ["running", "retry planned"],
)
async def test_an_open_job_is_returned_whatever_its_state(
    runtime: DatabaseRuntime, ending: str
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    running = job.start(started_at=NOW + timedelta(seconds=1))
    if ending == "running":
        await store(runtime, running)
    else:
        await store(
            runtime,
            running.fail(
                error_code="source_unavailable",
                finished_at=NOW + timedelta(seconds=2),
                retry_at=NOW + timedelta(minutes=1),
            ),
        )

    again = await over_http(runtime, "POST", path(source_id))

    assert (again.status_code, again.json()["job_id"]) == (200, str(job.job_id.value))
    assert again.json()["status"] == ("running" if ending == "running" else "failed")


@pytest.mark.parametrize("ending", ["succeeded", "failed for good", "cancelled"])
async def test_after_a_final_state_a_start_queues_a_new_job(
    runtime: DatabaseRuntime, ending: str
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    later = NOW + timedelta(seconds=2)
    if ending == "cancelled":
        await store(runtime, job.cancel(finished_at=later))
    else:
        running = job.start(started_at=NOW + timedelta(seconds=1))
        await store(
            runtime,
            running.succeed(finished_at=later)
            if ending == "succeeded"
            else running.fail(error_code="source_gone", finished_at=later),
        )

    again = await over_http(runtime, "POST", path(source_id))

    assert again.status_code == 202
    assert again.json()["job_id"] != str(job.job_id.value)


async def test_retrying_a_failed_job_while_a_new_one_is_open_is_a_409(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    await store(
        runtime,
        job.start(started_at=NOW + timedelta(seconds=1)).fail(
            error_code="source_gone", finished_at=NOW + timedelta(seconds=2)
        ),
    )
    await start(runtime, source_id)

    response = await over_http(runtime, "POST", f"/v1/jobs/{job.job_id.value}/retry")

    assert (response.status_code, response.json()["code"]) == (409, "job_already_open")


async def test_a_disabled_or_missing_source_queues_nothing(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    await set_source_enabled(
        runtime, source_id=source_id, enabled=False, operator=OPERATOR, now=NOW
    )

    disabled = await over_http(runtime, "POST", path(source_id))
    missing = await over_http(runtime, "POST", path(SourceId.new()))

    assert (disabled.status_code, disabled.json()["code"]) == (409, "source_disabled")
    assert (missing.status_code, missing.json()["code"]) == (404, "source_not_found")
    with pytest.raises(SourceDisabled):
        await start(runtime, source_id)
    with pytest.raises(SourceNotFound):
        await start(runtime, SourceId.new())

    async with runtime.transaction() as connection:
        count = await connection.scalar(sa.select(sa.func.count()).select_from(jobs))
    assert count == 0


async def test_a_start_publishes_once_and_a_refused_send_loses_nothing(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    published, refused = await folder(runtime, "a"), await folder(runtime, "b")
    publisher, down = Publisher(), Publisher(refuse=True)

    first = await over_http(runtime, "POST", path(published), publisher)
    await over_http(runtime, "POST", path(published), publisher)
    stored = await over_http(runtime, "POST", path(refused), down)

    assert publisher.sent == [JobId.parse(first.json()["job_id"])]
    assert stored.status_code == 202
    async with runtime.transaction() as connection:
        job = await load_job(connection, job_id=JobId.parse(stored.json()["job_id"]))
        unpublished = await connection.scalar(
            sa.select(jobs.c.published_at).where(
                jobs.c.job_id == JobId.parse(stored.json()["job_id"]).value
            )
        )
    assert job is not None
    assert job.status is JobStatus.QUEUED
    assert unpublished is None
    assert MARKER not in caplog.text


async def test_a_history_holds_only_its_sources_synchronization_jobs(
    runtime: DatabaseRuntime,
) -> None:
    mine, other = await folder(runtime, "mine"), await folder(runtime, "other")
    own = (await start(runtime, mine)).report.job
    await start(runtime, other)
    # Another kind of Job aimed at the same ID is not a synchronization.
    async with runtime.transaction() as connection:
        await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=mine.value,
                queued_at=NOW,
            ),
        )

    page = (await over_http(runtime, "GET", path(mine))).json()

    assert [item["job_id"] for item in page["items"]] == [str(own.job_id.value)]
    assert page["next_cursor"] is None


async def test_a_history_of_a_missing_source_is_a_404(
    runtime: DatabaseRuntime,
) -> None:
    response = await over_http(runtime, "GET", path(SourceId.new()))

    assert (response.status_code, response.json()["code"]) == (404, "source_not_found")


async def test_pages_of_history_never_repeat_or_skip_a_job(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    # Older Jobs get newer IDs here, so only an order by ID passes.
    async with runtime.transaction() as connection:
        for n in range(6):
            job = Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=source_id.value,
                queued_at=NOW - timedelta(hours=1 + n // 2),
            )
            await enqueue_job(connection, job)
            started = job.start(started_at=job.queued_at + timedelta(seconds=1))
            await save_job(
                connection,
                job=started.succeed(finished_at=job.queued_at + timedelta(seconds=2)),
            )
    seen: list[str] = []
    query = "?limit=2"

    for page_number in range(10):
        page = (await over_http(runtime, "GET", path(source_id) + query)).json()
        seen += [item["job_id"] for item in page["items"]]
        if page_number == 0:
            # A start between pages is newer: it starts no later page.
            await start(runtime, source_id)
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(jobs.c.job_id)
            .where(jobs.c.target_id == source_id.value, jobs.c.status == "succeeded")
            .order_by(jobs.c.job_id.desc())
        )
        expected = [str(row.job_id) for row in rows]
    assert seen == expected


async def test_a_history_item_shows_its_jobs_newest_attempt(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    first = job.start(started_at=NOW + timedelta(seconds=1))
    async with runtime.transaction() as connection:
        await save_job(connection, job=first)
        await begin_attempt(connection, job=first)
        failed = first.fail(
            error_code="source_unavailable",
            finished_at=NOW + timedelta(seconds=2),
            retry_at=NOW + timedelta(seconds=3),
        )
        await save_job(connection, job=failed)
        second = failed.retry(queued_at=NOW + timedelta(seconds=3)).start(
            started_at=NOW + timedelta(seconds=4)
        )
        await save_job(connection, job=second)
        await begin_attempt(connection, job=second)

    page = (await over_http(runtime, "GET", path(source_id))).json()

    assert page["items"][0]["latest_attempt"]["attempt"] == 2


async def failed_for_good(runtime: DatabaseRuntime, source_id: SourceId) -> Job:
    """A new synchronization Job of `source_id`, claimed and failed for good."""
    job = (await start(runtime, source_id)).report.job
    failed = job.start(started_at=NOW + timedelta(seconds=1)).fail(
        error_code="source_unavailable", finished_at=NOW + timedelta(seconds=2)
    )
    await store(runtime, failed)
    return failed


async def test_a_job_retried_between_pages_is_still_listed_once(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    failed = [await failed_for_good(runtime, source_id) for _ in range(4)]
    seen: list[str] = []
    query = "?limit=2"

    for page_number in range(10):
        page = (await over_http(runtime, "GET", path(source_id) + query)).json()
        seen += [item["job_id"] for item in page["items"]]
        if page_number == 0:
            # A retry queues the oldest Job again, at a later time.
            await retry_job(
                runtime,
                job_id=failed[0].job_id,
                operator=OPERATOR,
                now=NOW + timedelta(hours=1),
            )
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    assert sorted(seen) == sorted(str(job.job_id.value) for job in failed)
    assert len(seen) == len(set(seen))


async def test_a_retry_queues_nothing_for_a_disabled_source(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    failed = await failed_for_good(runtime, source_id)
    await set_source_enabled(
        runtime, source_id=source_id, enabled=False, operator=OPERATOR, now=NOW
    )

    response = await over_http(runtime, "POST", f"/v1/jobs/{failed.job_id.value}/retry")

    assert (response.status_code, response.json()["code"]) == (409, "source_disabled")
    async with runtime.transaction() as connection:
        stored = await load_job(connection, job_id=failed.job_id)
    assert stored is not None
    assert stored.status is JobStatus.FAILED


async def test_a_retry_that_does_not_fit_the_job_says_so_even_for_a_disabled_source(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    job = (await start(runtime, source_id)).report.job
    running = job.start(started_at=NOW + timedelta(seconds=1))
    await store(runtime, running.succeed(finished_at=NOW + timedelta(seconds=2)))
    await set_source_enabled(
        runtime, source_id=source_id, enabled=False, operator=OPERATOR, now=NOW
    )

    response = await over_http(runtime, "POST", f"/v1/jobs/{job.job_id.value}/retry")

    assert (response.status_code, response.json()["code"]) == (
        409,
        "decision_not_allowed",
    )


async def test_a_disable_waits_for_a_start_that_holds_the_source(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await folder(runtime)
    async with runtime.transaction() as connection:
        # What a start holds between its check and its commit.
        await lock_enabled_sources(connection, source_ids={source_id})
        disabling = asyncio.ensure_future(
            set_source_enabled(
                runtime, source_id=source_id, enabled=False, operator=OPERATOR, now=NOW
            )
        )
        await asyncio.sleep(0.3)
        waited = not disabling.done()
    disabled = await asyncio.wait_for(disabling, timeout=5)

    assert waited
    assert disabled.enabled is False


async def test_a_history_page_reads_an_index_in_job_order(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        definition = await connection.scalar(
            sa.text(
                "SELECT indexdef FROM pg_indexes"
                " WHERE indexname = 'ix_jobs_synchronization_target'"
            )
        )

    assert "(target_id, job_id)" in str(definition)
    assert "synchronize_source" in str(definition)
```

Перевірка:

```bash
uv run pytest tests/test_synchronization_administration.py -q
uv run pytest tests/test_synchronization_administration_integration.py -q -m integration
```

Очікувано: `39 passed`, потім `20 passed`.

## Крок 9 — Навмисно дати запуститися вимкненому Source

Без коду в цьому кроці.

Цей крок доводить, що інтеграційні тести охороняють правило «запускається лише ввімкнений Source». У `src/knowledge_service/synchronization_administration.py`, у `start_synchronization`, змініть

```python
        if source_id not in enabled:
```

на `if False:`, потім запустіть із вимкненим байткодом:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider tests/test_synchronization_administration_integration.py -q -m integration
```

Очікувано: `1 failed, 19 passed`, і падає `test_a_disabled_or_missing_source_queues_nothing`: Job зберігся для вимкненого Source, і відсутній Source теж отримав свій. Скасуйте зміну й запустіть знову: `20 passed`.

Що спростувало б дизайн: дві відкриті Jobs синхронізації для одного Source, Job, збережений для вимкненого Source запуском чи повтором, 202, що нічого не поставив, або 200, що щось поставив, Job, показаний двічі чи жодного разу під час гортання, втрачений Job через невдалу відправку, або відмова, чий статус, код чи повідомлення змінилися.

## Крок 10 — Оновити архітектуру й модель загроз

Без коду в цьому кроці.

У `docs/architecture.md` перед абзацом, що починається з `` `api_routes` is the native API's design ``, додайте:

```markdown
`POST /v1/sources/{source_id}/synchronizations` and
`GET /v1/sources/{source_id}/synchronizations` require
`start_synchronization_runs` (the content-manager role) before the ID or
page is read. `synchronization_administration` starts a synchronization
the way the scheduler does: it share-locks the Source, refuses one that
is missing (404) or disabled (409 `source_disabled`), and asks
`enqueue_job` for one Job; while the Source has an open Job (queued,
running, or failed with a retry planned) that Job comes back with 200,
and only a start that queues one answers 202 and records `job.queued`
with the operator as actor. The start commits before it tries once to
publish, so a refused message is sent later by the relay. The history
lists a Source's synchronization Jobs a page at a time, the newest
made first, each with its newest attempt, from one snapshot. It pages
on the Job's ID, a UUIDv7 that never changes, not on `queued_at`,
which every retry rewrites. Status, retry, and cancel stay on
`/v1/jobs/{job_id}`; a retry of a synchronization Job, once its status
fits, share-locks the Source as a start does and refuses one that is
missing or disabled with 409 `source_disabled`. Every refusal of these
five operations has one fixed status, code, and message. A Source's
history pages on `ix_jobs_synchronization_target (target_id, job_id)`.
```

У `docs/threat-model.md`:

- у таблиці точок входу перед рядком `GET /health/live` додайте:

```markdown
| `POST /v1/sources/{source_id}/synchronizations`, `GET /v1/sources/{source_id}/synchronizations` | Any client that reaches the API | Starts a Source's synchronization, or returns its open Job, and lists its synchronization Jobs a page at a time. Needs a bearer token and the `start_synchronization_runs` operation (the `content-manager` role), both checked before the ID, `limit`, or `cursor` is read; only an enabled Source starts, and a retry of its Job is refused the same way; a start that queues a Job is an audit event with the operator as actor; the message carries only the Job's ID |
```

- в рядку Operations «A role widens what its holder reads» остання клітинка стає «Role checks on the evaluation and policy entry points when they exist»;
- у відомих прогалинах замініть пункт, що починається з «Only `ask_questions` and `manage_own_conversations` have entry points», на:

```markdown
- `ask_questions`, `manage_own_conversations` (only for deleting), `configure_sources`, `start_synchronization_runs`, and `recover_jobs` have entry points that check them; reading a Conversation or its History has no route yet. Running evaluations is a script a developer runs with no identity, and the scheduler queues synchronizations as the service itself; each operation gets its role check with its entry point.
```

У `docs/operations.md`, в абзаці Telemetry, перед «Each Job attempt's stage» додайте:

```markdown
A content manager starts a Source's synchronization with `POST /v1/sources/{source_id}/synchronizations` (202 when it queues a Job, 200 with the Job already open) and follows it with `GET /v1/sources/{source_id}/synchronizations`.
```

Перевірка: `make docs-check` проходить.

## Крок 11 — Запустити перевірки й написати докази

Без коду в цьому кроці.

Додайте нові файли в git перед запуском hooks: pre-commit дивиться лише на файли, які відстежує git, тож зовсім новий файл інакше пройшов би неперевіреним.

```bash
git add -A
uv run ruff format --check .
uv run ruff check .
uv run pyright
make docs-check
make test-unit
make test-integration
git diff --check
uv run pre-commit run
```

На прототипі вони дали: 371 відформатований файл, усі перевірки пройшли, 0 помилок, docs-check з кодом 0, `3681 passed` модульних, `948 passed` інтеграційних (з секретами Keycloak і доступним Redis), і кожен hook пройшов на доданих файлах.

Потім своїми словами напишіть `docs/evidence/M09-T03-synchronization-administration.md`: інваріант і власників, ваші передбачення з кроку 1 і що сталося насправді, очікувану поломку перевірки, п'ять операцій і їхні ролі, правило 202 і 200, порядок збереження й публікації, чому історія гортається за ID, таблицю помилок, навмисну поломку, результати перевірок і що спростувало б дизайн. Запуск робить щонайбільше один виклик Redis, обмежений власними тайм-аутами клієнта, і жодного платного виклику. Повторний запуск безпечний за задумом; скасована Job кінцева, і наступний запуск ставить нову.

## Підсумковий чекліст

- [ ] `POST /v1/sources/{source_id}/synchronizations` відповідає 202 з `Location`, коли ставить Job у чергу, і 200 з тим самим Job, поки він відкритий.
- [ ] Чотири одночасні запуски зберігають один Job і одну подію `job.queued` з оператором.
- [ ] Після успіху, остаточного падіння чи скасування наступний запуск ставить новий Job.
- [ ] Запуск для відсутнього Source дає 404 `source_not_found`; для вимкненого — 409 `source_disabled`, як і повтор, коли стан Job пасує; у черзі нічого не з'являється.
- [ ] Запуск комітиться до публікації; відхилена відправка все одно відповідає 202, а Job чекає на relay.
- [ ] `GET /v1/sources/{source_id}/synchronizations` показує лише Jobs синхронізації цього Source, від найновіше створених, з їхніми найновішими спробами, і гортається за ID Job.
- [ ] Кожна відмова запуску, статусу, історії, повтору й скасування має статус, код і повідомлення з блоку 5.
- [ ] Клієнт без ролі content-manager отримує 401 чи 403 до прочитання будь-якого ID чи сторінки.
- [ ] `tests/test_synchronization_administration.py` (39) і `tests/test_synchronization_administration_integration.py` (20) проходять.
- [ ] Навмисна поломка дала 1 failed, 19 passed, а відновлений код проходить.
- [ ] `docs/architecture.md` і `docs/threat-model.md` оновлено, і кожна перевірка з кроку 11 проходить, hooks теж.
- [ ] Файл доказів написано своїми словами.

## Запитання для роздумів

1. Двоє людей одночасно натискають «синхронізувати зараз» для того самого Source. Який об'єкт бази даних гарантує, що збережеться лише один Job, і чому коду не потрібно перевіряти відкритий Job перед вставкою?
2. Чому запуск відповідає 202 першого разу і 200 другого, а не 409 другого разу? Подумайте про клієнта, що повторює запит після тайм-ауту мережі.
3. Запуск зберігає Job і його подію аудиту до того, як надіслати повідомлення в Redis. Що могло б піти не так у зворотному порядку і хто надішле повідомлення, якщо Redis його відхилить?
4. Перший дизайн історії впорядковував Jobs за `queued_at`. Пройдіть, як повтор між двома запитами сторінок змусив Job зникнути, і чому впорядкування за ID UUIDv7 не може мати такої проблеми.
5. Повтор і запуск — різні маршрути з різними ролями. Чому все одно було помилкою, що повтор не перевіряв, чи Source увімкнений?
6. Клієнтський скрипт розгалужується за `source_disabled` і `job_running`. Чому урок закріплює в тесті точні статус, код і повідомлення кожної відмови, і що тихо зламалося б без цього тесту?
