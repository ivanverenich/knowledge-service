English version: [M08-T07-report-progress-and-terminal-outcomes.md](M08-T07-report-progress-and-terminal-outcomes.md)

# M08-T07 — Прогрес і фінальний результат Job

Завдання: [M08-T07 — Report progress and terminal outcomes](../curriculum/milestones/M08-jobs-and-confluence/M08-T07-report-progress-and-terminal-outcomes.md)

## Що ти зробиш

Цей урок навчить Job, що працює, повідомляти, як далеко він просунувся. Кожна спроба Job отримує власний рядок у PostgreSQL: що вона робить зараз, скільки елементів обробила, які попередження зустріла, коли востаннє подала знак і, наприкінці, чим закінчилась. Контент-менеджер читає це через `GET /v1/jobs/{job_id}`.

Конкретний приклад. Job синхронізації J7 для Handbook працює на воркері A, який звітує після кожної сторінки:

| Час | Воркер A | Що показує `GET /v1/jobs/J7` |
|---|---|---|
| 10:00 | бере J7, починається спроба 1 | `running`, спроба 1, етап `starting`, оброблено 0 |
| 10:02 | звітує: читає, оброблено 100 | етап `reading`, оброблено 100, heartbeat 10:02 |
| 10:05 | звітує: пише, оброблено 120, 2 відкладено | етап `writing`, оброблено 120, пропущено 2, попередження `quarantined_items`, heartbeat 10:05 |
| 10:06 | процес убито | те саме, що о 10:05: звіт уже закомічено |
| 10:30 | | досі heartbeat 10:05: ознака, що над ним ніхто не працює |

| Що просить завдання | Де це зроблено |
|---|---|
| Зберігати монотонні лічильники, етап, heartbeat, попередження і типізований фінальний результат | Кроки 2 і 3: `JobProgress` і таблиця `job_attempts` |
| Не прив'язувати домен до повідомлень Dramatiq | Блок 1: обробник викликає `report_progress`; жодне повідомлення не задіяне |
| Прогрес видно через API і він переживає перезапуск воркера | Крок 4: `GET /v1/jobs/{job_id}`; крок 5: `test_progress_survives_the_worker_that_reported_it` |
| Відхиляти переходи назад | Блоки 2 і 3 |
| Явна поведінка при повторі чи скасуванні | Блок 2 (повторний звіт нічого не змінює) і блок 7 (скасування) |
| Оновити лише ту документацію, якої це стосується | Крок 7: архітектура, експлуатація, модель загроз |

У цьому уроці є код: кроки 2–5 пишуть код, міграцію і тести. Кроки 1, 6, 7 і 8 коду не потребують, хоча на кроці 7 ти редагуєш три документи.

Терміни цього уроку:

- **Спроба (attempt):** одна спроба виконати Job, нумерація з 1. Job, що впав і був повторений, має дві спроби.
- **Звіт про прогрес (progress report):** одне повідомлення від спроби, що працює, до бази: її етап і підсумки на цей момент.
- **Heartbeat («пульс»):** час останнього звіту. Якщо пульс перестав рухатись, воркер зупинився.
- **Монотонний:** рухається лише в один бік. Лічильник оброблених елементів може рости або стояти, але ніколи не зменшується.
- **Результат (outcome):** чим закінчилась спроба, одне з п'яти фіксованих значень, наприклад `succeeded` або `retry_scheduled`.
- **Застарілий воркер (stale worker):** воркер, який досі виконує спробу, від якої Job уже пішов далі.

## Перед початком

Цей розділ крок за кроком пояснює кожне рішення, на якому стоїть код. Кожен блок закінчується тим, що змінює цей урок.

### 1. Де живе прогрес: один закомічений рядок на спробу

**Ситуація.** Воркер A виконує спробу 1 Job J7. О 10:05 він обробив 120 сторінок. О 10:06 його процес убивають.

| Де воркер A тримає «оброблено 120» | Що бачать інші о 10:10 |
|---|---|
| у власній пам'яті | нічого: це зникло разом із процесом |
| у повідомленні черги | нічого корисного: повідомлення в Redis — не запис (ADR 0011), і забране повідомлення зникає |
| у рядку `jobs` | 120, доки спроба 2 не почнеться і не перезапише його: докази спроби 1 втрачено |
| у власному рядку `job_attempts`, закоміченому при кожному звіті | 120, heartbeat 10:05, і він лишається після початку спроби 2 |

**Прихований факт.** Звіт бачать інші лише тоді, коли його транзакція закомічена. Обробник purge робить усю спробу в одній транзакції, тож ніхто не бачить його «в роботі». Довга синхронізація так працювати не може: кожен звіт — окрема коротка транзакція, тому його видно одразу, і він переживає процес.

**Що змінюється.** Таблиця `job_attempts` тримає один рядок на `(job_id, attempt)`. Обробник зберігає рядок разом зі своїм захопленням Job (`begin_attempt`), надсилає підсумки через `report_progress` і закінчує спробу через `finish_attempt` у тій самій транзакції, що зберігає завершений Job. Жодна з цих функцій не бере повідомлення, актора чи будь-що з Dramatiq.

### 2. Ніщо не йде назад: лічильники, попередження, heartbeat

**Ситуація.** Воркер A швидко надсилає два звіти з двох корутин або повторює один після повільної мережі. «оброблено 60» комітиться першим, «оброблено 50» приходить другим.

| Момент | Звіт «60» | Звіт «50» | Збережено |
|---|---|---|---|
| 1 | блокує рядок спроби, пише 60, комітить | чекає на блокування | 60 |
| 2 | | отримує блокування, читає 60; 50 < 60: відхилено з `ProgressWentBackwards` | 60 |

**Прихований факт.** Без блокування обидва звіти читають старе значення, і перемагає той, хто закомітить останнім, навіть якщо він старший. `SELECT ... FOR UPDATE` на рядку спроби змушує другий звіт чекати, а потім прочитати те, що закомітив перший. Далі його перевіряють два шари:

| Шар | Перевірка |
|---|---|
| домен, `JobProgress.advance` | кожен лічильник не менший за збережений, жодне попередження не зникає, heartbeat не раніший |
| SQL, `_save` | сам `UPDATE` має `WHERE processed <= :new AND changed <= :new AND skipped <= :new AND heartbeat_at <= :new AND warnings <@ :new`; якщо жоден рядок не підходить, він кидає помилку |

**Підсумки, а не прирости.** Звіт каже «на зараз 120», а не «+20». Повторений «+20» порахувався б двічі; повторений «120» нічого не змінює, бо рівні значення дозволені.

| Було (прирости) | Стало (підсумки) |
|---|---|
| `UPDATE ... SET processed = processed + 20` | `UPDATE ... SET processed = 120 WHERE processed <= 120 ...` |
| повтор: 140, неправильно | повтор: 120, те саме |

**Етап може повертатися.** Синхронізація читає сторінку, пише її, читає наступну. Тож `stage` — це лише «що вона робить зараз», а не впорядкований стан.

**Що змінюється.** `JobProgress.advance` і `UPDATE` у `persistence.job_attempts` відхиляють усе, що йде назад. Аналогія: одометр автомобіля. Він може стояти, але ніколи не крутиться назад, а того, хто його відкрутить, спіймають і по циферблату, і по сервісній книжці.

### 3. Застарілий воркер не може писати

**Ситуація.** Спроба 1 Job J7 втрачає з'єднання з базою, її записують як невдалу з повтором, і на воркері B починається спроба 2. Потім воркер A, ще живий, надсилає ще один звіт для спроби 1.

| Момент | Воркер A (спроба 1) | Воркер B (спроба 2) | Рядок `jobs` |
|---|---|---|---|
| 10:05 | звітує 120 | | `running`, спроба 1 |
| 10:06 | з'єднання втрачено; спробу записано як невдалу | | `failed`, повтор о 10:07 |
| 10:07 | | бере J7, починається спроба 2 | `running`, спроба 2 |
| 10:08 | звітує 130 для спроби 1: **відхилено**, `AttemptNotCurrent` | | без змін |

**Прихований факт.** Кожен звіт спершу бере спільне блокування (share lock) рядка Job, і лише поки Job виконує саме цю спробу (`lock_running_job`: `SELECT ... WHERE status = 'running' FOR SHARE`). Спільне блокування пускає інші звіти, але не пускає тих, хто пише, тож Job не може завершитись чи перейти до спроби 2 посеред звіту. Важливо блокувати лише рядки, що працюють: relay ставить у чергу повтори через `FOR UPDATE SKIP LOCKED`, який мовчки пропускає будь-який заблокований рядок, тож застарілий воркер, що заблокував Job у статусі `failed`, затримав би його повтор.

Те саме правило стосується завершення: `finish_attempt` відмовляє спробі, якщо вже є рядок новішої спроби, тож уся транзакція застарілого воркера відкочується, разом зі старим станом Job, який він намагався зберегти.

**Що змінюється.** `advance_attempt` відмовляє спробі, яку Job не виконує; `finish_attempt` відмовляє спробі, від якої Job пішов далі; кожна спроба зберігає власний рядок.

### 4. Типізований результат і завершення, що ніколи не падає

**Ситуація.** Оператор хоче знати, чим закінчилась спроба 1, не розшифровуючи коди помилок.

| Job, коли спроба закінчилась | Результат |
|---|---|
| `succeeded`, без попереджень | `succeeded` |
| `succeeded`, з попередженнями | `succeeded_with_warnings` |
| `failed`, помилка `superseded` | `superseded` |
| `failed`, з `retry_at` | `retry_scheduled` |
| `failed`, без повтору | `failed` |

`outcome_of` виводить це з завершеного Job, тож результат завжди з ним узгоджений.

**Прихований факт.** Завершення ніколи не має падати через те, що зберіг звіт, інакше Job назавжди лишиться `running`. Два випадки, які знайшло рев'ю:

| Випадок | Перша версія | Тепер |
|---|---|---|
| годинник звітувальника на 10 секунд попереду годинника того, хто завершує | «не може закінчитись раніше за свій heartbeat»: усе завершення відкотилось | спроба закінчується в час завершення Job; heartbeat підтягується назад до нього |
| фінальні лічильники менші за звіт, що встиг проскочити | «лічильник зменшився б»: відкотилось | для кожного лічильника лишається більше значення |

Звіт із часом більш ніж на п'ять хвилин попереду годинника бази відхиляється (`ReportFromTheFuture`), тож один поганий годинник не може поставити heartbeat у 9999 рік і відхиляти кожен чесний звіт після цього.

### 5. Хто може це читати

**Ситуація.** `GET /v1/jobs/{job_id}` міг би показувати будь-який Job. Але ціль Job `purge_conversation` — це ID розмови (Conversation).

| Хто питає і який Job | Відповідь |
|---|---|
| без токена або роль без `start_synchronization_runs` | 401 або 403, ще до розбору ID |
| контент-менеджер, Job синхронізації | 200 з Job і його найновішою спробою |
| контент-менеджер, Job purge | 404 `job_not_found` |
| контент-менеджер, ID, якого немає | 404 `job_not_found`, та сама відповідь |

**Прихований факт.** Два читання, спершу Job, потім його спроби, можуть побачити два різні моменти: Job, що завершився між ними, виглядає `running` із закінченою спробою. `DatabaseJobReports.load` виконує свою транзакцію як `REPEATABLE READ`, тож обидва читання бачать один знімок.

### 6. Знахідки рев'ю

Адверсальне рев'ю тричі атакувало цей код на справжньому PostgreSQL. Кожну знахідку нижче виправлено, з тестом, який без виправлення падає:

- годинник звітувальника попереду годинника того, хто завершує, або менші фінальні лічильники не давали Job ніколи завершитись (блок 4);
- звіт із часом далеко в майбутньому відхиляв кожен чесний звіт після нього (блок 4);
- відхилений звіт блокував Job у статусі `failed` і затримував його повтор (блок 3);
- API читав Job і його спробу з двох моментів (блок 5);
- неправильні типи (етап як звичайний рядок, лічильники як dict) падали з нетипізованою помилкою; тепер вони кидають `InvalidJob`;
- повторний початок спроби кидав сиру помилку бази; тепер кидає `AttemptAlreadyBegun`;
- ендпоінт довіряв сховищу фільтрувати за видом; тепер перевіряє ще раз;
- застарілий воркер міг завершити спробу 1 після початку спроби 2, і тоді Job уже ніколи не міг запуститись (блок 3);
- час завершення спроби міг не збігатися з часом її Job (блок 4);
- читання спроби в сесії з часовим поясом не UTC падало; тепер час читається назад в UTC.

### 7. Відомі обмеження

- Жоден робочий обробник поки не звітує про прогрес: обробник синхронізації з'явиться в M08-T10, і він має викликати `begin_attempt` у транзакції захоплення, `report_progress` після кожної сторінки і `finish_attempt` разом із завершеним Job. Зараз спроби записує лише purge, а ендпоінт ховає Job purge.
- Job, чий воркер помер або чию спробу скасовано, лишається `running` з останнім heartbeat, доки M08-T08 його не знайде.
- Воркер, чий годинник більш ніж на п'ять хвилин попереду бази, не може звітувати.
- `save_job` не перевіряє статус (так було й до цього уроку): застарілий воркер, який ще раз зберігає ту саму невдачу після того, як Job знову поставили в чергу, повертає його в `failed` до наступного проходу relay. Обробник M08-T10 має перевірити своє захоплення, перш ніж зберігати завершення.
- Збережені лічильники можуть змішати два звіти: збережене (оброблено 10, змінено 0) і фінальне (5, 5) дають (10, 5).
- Читання рядків `jobs` у сесії з часовим поясом не UTC досі падає (так було й до цього уроку).

## Покрокові інструкції

Перед початком запусти PostgreSQL. Усі команди запускай з кореня репозиторію. Для integration-перевірок задай URL тестової бази у своєму терміналі:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

Запускай проти цієї бази лише одну integration-команду за раз.

### Крок 1 — Без коду: запиши інваріант і свої прогнози

Перш ніж щось змінювати, запиши, що має бути правдою наприкінці і що ти очікуєш побачити. Прогнози ти перевіриш на кроці 6 і кроці 8.

Створи `docs/evidence/M08-T07-report-progress-and-terminal-outcomes.md`:

```markdown
# M08-T07 — Report progress and terminal outcomes evidence

## Invariant

Each attempt of a Job has one progress row in PostgreSQL. While the Job runs
that attempt, a report may move its stage and only raise its counters, add
warnings, and move its heartbeat forward; anything else is refused, and an
attempt the Job is not running cannot write. Each report commits on its own,
so a new process and the API read it after the reporter is gone. Ending an
attempt records one typed outcome that agrees with the finished Job, and
never fails on what a report stored.

## Owners

- Durable progress and outcome: `job_attempts` in PostgreSQL.
- Who may report: the attempt the Job is running (`lock_running_job`).
- Retries: the stored Job (M08-T04); recovery of dead attempts: M08-T08.
- Authorization: `GET /v1/jobs/{job_id}` needs `start_synchronization_runs`.

## Expected failing check

Before the change,
`uv run pytest tests/test_job_progress_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.

## Predictions

- What happens to a report of 50 processed that arrives after 60 was stored:
- What the API shows after the worker that reported 120 processed is killed:
- What attempt 1's worker gets when it reports after attempt 2 began:
- How many tests fail if the domain stops checking counters, and whether the database still refuses a lower counter:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після двокрапки в кожному прогнозі стоїть твоя відповідь.

### Крок 2 — Крок із кодом: прогрес однієї спроби

Цей крок додає доменне значення для однієї спроби з усіма правилами про те, що може змінюватись.

**Простими словами:** цей файл описує одну спробу як звичайне значення: її етап, три лічильники, попередження, heartbeat і результат. Якщо попросити його прийняти новіший звіт, він дає нове значення або відмовляє, коли щось пішло б назад. Під час завершення він вибирає результат за завершеним Job. Нічого тут не торкається ні бази, ні черги.

| Частина | Що вона робить |
|---|---|
| `JobStage` | `starting`, `reading`, `writing`, `cleaning_up`; можуть повторюватись |
| `JobWarning` | `unresolved_readers`, `quarantined_items`; коди, ніколи не текст |
| `AttemptOutcome` | п'ять результатів із блоку 4 |
| `JobCounters` | `processed`, `changed`, `skipped`, кожен — ціле число від 0 до межі bigint; `merged` бере більше з кожної пари |
| `attempt_number` | номер спроби, на якій Job працює або закінчився, з `Job.attempt` (що рахує невдалі спроби) |
| `outcome_of` | таблиця з блоку 4 |
| `JobProgress.begin` / `advance` / `finish` | правила з блоків 2 і 4 |

**Дія 1:** створи модуль.

Створи `src/knowledge_service/job_progress.py`:

```python
"""How far one attempt of a Job got, and how it ended.

Each attempt of a Job has its own progress: a stage, counters that only
grow, the warnings it met, a heartbeat, and, once it ends, a typed outcome.
It is stored in PostgreSQL, so it outlives the worker that wrote it, and a
later attempt never overwrites an earlier one. Nothing here knows about
queue messages: a handler reports progress, the database keeps it.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from knowledge_service.identifiers import JobId
from knowledge_service.job_failures import JobFailure
from knowledge_service.jobs import InvalidJob, Job, JobStatus

# The largest values a PostgreSQL bigint and integer hold.
MAX_COUNT: Final = 2**63 - 1
MAX_ATTEMPT: Final = 2**31 - 1


class ProgressWentBackwards(InvalidJob):
    """A report would make a counter, a warning, or the heartbeat go back."""


class ReportFromTheFuture(InvalidJob):
    """A report's time is far ahead of the database's clock."""


class AttemptFinished(InvalidJob):
    """The attempt already ended, so it takes no more progress."""


class JobStage(StrEnum):
    """What an attempt is doing now. Stages may repeat, page after page."""

    STARTING = "starting"
    READING = "reading"
    WRITING = "writing"
    CLEANING_UP = "cleaning_up"


class JobWarning(StrEnum):
    """Something an attempt met that did not stop it."""

    # A Source named readers the directory does not know.
    UNRESOLVED_READERS = "unresolved_readers"
    # An item could not be used and was set aside.
    QUARANTINED_ITEMS = "quarantined_items"


class AttemptOutcome(StrEnum):
    """How one attempt ended."""

    SUCCEEDED = "succeeded"
    SUCCEEDED_WITH_WARNINGS = "succeeded_with_warnings"
    # It failed, and the Job will be tried again at its retry time.
    RETRY_SCHEDULED = "retry_scheduled"
    # A newer run of the same work started; there is nothing to redo.
    SUPERSEDED = "superseded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class JobCounters:
    """Running totals of one attempt; each one only grows."""

    processed: int = 0
    changed: int = 0
    skipped: int = 0

    def __post_init__(self) -> None:
        for name in ("processed", "changed", "skipped"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= MAX_COUNT:
                raise InvalidJob(f"{name} must be an integer from 0 to {MAX_COUNT}")

    def merged(self, other: JobCounters) -> JobCounters:
        """The higher of each counter in the two."""
        return JobCounters(
            processed=max(self.processed, other.processed),
            changed=max(self.changed, other.changed),
            skipped=max(self.skipped, other.skipped),
        )

    def covers(self, earlier: JobCounters) -> bool:
        """Whether no counter is below its value in `earlier`."""
        return (
            self.processed >= earlier.processed
            and self.changed >= earlier.changed
            and self.skipped >= earlier.skipped
        )


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidJob(f"{field_name} must be a UTC-aware timestamp")


def _require_report(stage: object, counters: object) -> None:
    if type(stage) is not JobStage:
        raise InvalidJob("stage must be a JobStage")
    if type(counters) is not JobCounters:
        raise InvalidJob("counters must be JobCounters")


def attempt_number(job: Job) -> int:
    """The number, from 1, of the attempt a started or finished Job is on.

    `Job.attempt` counts failed attempts, so a failed Job's last attempt is
    that count, and a running or succeeded Job's is the next one.
    """
    if job.status is JobStatus.QUEUED:
        raise InvalidJob("a queued job has no attempt under way")
    if job.status is JobStatus.FAILED:
        return job.attempt
    return job.attempt + 1


def outcome_of(job: Job, warnings: Iterable[JobWarning] = ()) -> AttemptOutcome:
    """The outcome of the attempt a finished Job just ended."""
    if job.status is JobStatus.SUCCEEDED:
        if any(True for _ in warnings):
            return AttemptOutcome.SUCCEEDED_WITH_WARNINGS
        return AttemptOutcome.SUCCEEDED
    if job.status is not JobStatus.FAILED:
        raise InvalidJob("only a finished job has an outcome")
    if job.last_error_code == JobFailure.SUPERSEDED.value:
        return AttemptOutcome.SUPERSEDED
    if job.retry_at is not None:
        return AttemptOutcome.RETRY_SCHEDULED
    return AttemptOutcome.FAILED


@dataclass(frozen=True, slots=True)
class JobProgress:
    """Where one attempt of a Job is, or how it ended."""

    job_id: JobId
    attempt: int
    stage: JobStage
    counters: JobCounters
    warnings: frozenset[JobWarning]
    started_at: datetime
    heartbeat_at: datetime
    outcome: AttemptOutcome | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        if type(self.attempt) is not int or not 1 <= self.attempt <= MAX_ATTEMPT:
            raise InvalidJob(f"attempt must be an integer from 1 to {MAX_ATTEMPT}")
        _require_report(self.stage, self.counters)
        if type(self.warnings) is not frozenset or any(
            type(warning) is not JobWarning for warning in self.warnings
        ):
            raise InvalidJob("warnings must be a frozenset of JobWarning")
        if self.outcome is not None and type(self.outcome) is not AttemptOutcome:
            raise InvalidJob("outcome must be an AttemptOutcome")
        _require_utc(self.started_at, "started_at")
        _require_utc(self.heartbeat_at, "heartbeat_at")
        if self.heartbeat_at < self.started_at:
            raise InvalidJob("heartbeat_at must not be earlier than started_at")
        if (self.outcome is None) != (self.finished_at is None):
            raise InvalidJob("only a finished attempt carries an outcome")
        if self.finished_at is not None:
            _require_utc(self.finished_at, "finished_at")
            if self.finished_at < self.heartbeat_at:
                raise InvalidJob("finished_at must not be earlier than heartbeat_at")

    @classmethod
    def begin(cls, job: Job) -> JobProgress:
        """The progress of the attempt a Job has just started."""
        if job.status is not JobStatus.RUNNING or job.started_at is None:
            raise InvalidJob("only a running job begins an attempt")
        return cls(
            job_id=job.job_id,
            attempt=attempt_number(job),
            stage=JobStage.STARTING,
            counters=JobCounters(),
            warnings=frozenset(),
            started_at=job.started_at,
            heartbeat_at=job.started_at,
        )

    def advance(
        self,
        *,
        stage: JobStage,
        counters: JobCounters,
        warnings: Iterable[JobWarning] = (),
        at: datetime,
    ) -> JobProgress:
        """Record a newer report: counters, warnings, and time never go back.

        Reporting the same values again changes nothing, so a replayed report
        is harmless.
        """
        if self.outcome is not None:
            raise AttemptFinished("the attempt already ended")
        _require_report(stage, counters)
        _require_utc(at, "at")
        try:
            seen = frozenset(warnings)
        except TypeError:
            raise InvalidJob("warnings must be JobWarning values") from None
        if any(type(warning) is not JobWarning for warning in seen):
            raise InvalidJob("warnings must be JobWarning values")
        if not counters.covers(self.counters):
            raise ProgressWentBackwards("a counter would go down")
        if not seen >= self.warnings:
            raise ProgressWentBackwards("a warning would disappear")
        if at < self.heartbeat_at:
            raise ProgressWentBackwards("the heartbeat would go back in time")
        return replace(
            self, stage=stage, counters=counters, warnings=seen, heartbeat_at=at
        )

    def finish(self, *, outcome: AttemptOutcome, at: datetime) -> JobProgress:
        """End the attempt. Ending it again the same way changes nothing.

        Ending never fails on time: the attempt ends at `at`, its Job's own
        finish time, and a heartbeat later than that, from a reporter whose
        clock runs ahead, is pulled back to it, so the outcome is always
        recorded and agrees with the Job.
        """
        if type(outcome) is not AttemptOutcome:
            raise InvalidJob("outcome must be an AttemptOutcome")
        if self.outcome is not None:
            if self.outcome is outcome:
                return self
            raise AttemptFinished("the attempt already ended another way")
        _require_utc(at, "at")
        if at < self.started_at:
            raise InvalidJob("an attempt cannot end before it started")
        heartbeat = min(self.heartbeat_at, at)
        return replace(self, outcome=outcome, finished_at=at, heartbeat_at=heartbeat)
```

**Дія 2:** unit-тести для кожного правила.

Створи `tests/test_job_progress.py`:

```python
"""Tests for the progress of one Job attempt: nothing in it goes back."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from knowledge_service.identifiers import JobId
from knowledge_service.job_failures import JobFailure
from knowledge_service.job_progress import (
    MAX_COUNT,
    AttemptFinished,
    AttemptOutcome,
    JobCounters,
    JobProgress,
    JobStage,
    JobWarning,
    ProgressWentBackwards,
    attempt_number,
    outcome_of,
)
from knowledge_service.jobs import InvalidJob, Job, JobKind

QUEUED_AT = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
STARTED_AT = QUEUED_AT + timedelta(seconds=1)
LATER = STARTED_AT + timedelta(seconds=30)


def running_job(*, max_attempts: int = 3) -> Job:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=uuid4(),
        queued_at=QUEUED_AT,
        max_attempts=max_attempts,
    )
    return job.start(started_at=STARTED_AT)


def begun() -> JobProgress:
    return JobProgress.begin(running_job())


def test_an_attempt_begins_at_its_start_with_nothing_counted() -> None:
    progress = begun()

    assert progress.attempt == 1
    assert progress.stage is JobStage.STARTING
    assert progress.counters == JobCounters()
    assert progress.warnings == frozenset()
    assert progress.heartbeat_at == STARTED_AT
    assert progress.outcome is None


def test_only_a_running_job_begins_an_attempt() -> None:
    queued = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=uuid4(),
        queued_at=QUEUED_AT,
    )

    with pytest.raises(InvalidJob):
        JobProgress.begin(queued)


def test_a_report_moves_the_stage_counters_warnings_and_heartbeat() -> None:
    advanced = begun().advance(
        stage=JobStage.WRITING,
        counters=JobCounters(processed=10, changed=3, skipped=1),
        warnings=[JobWarning.QUARANTINED_ITEMS],
        at=LATER,
    )

    assert advanced.stage is JobStage.WRITING
    assert advanced.counters == JobCounters(processed=10, changed=3, skipped=1)
    assert advanced.warnings == {JobWarning.QUARANTINED_ITEMS}
    assert advanced.heartbeat_at == LATER


def test_the_same_report_twice_changes_nothing() -> None:
    counters = JobCounters(processed=4)
    once = begun().advance(stage=JobStage.READING, counters=counters, at=LATER)

    twice = once.advance(stage=JobStage.READING, counters=counters, at=LATER)

    assert twice == once


def test_the_stage_may_go_back_page_after_page() -> None:
    writing = begun().advance(
        stage=JobStage.WRITING, counters=JobCounters(processed=1), at=LATER
    )

    reading = writing.advance(
        stage=JobStage.READING, counters=JobCounters(processed=1), at=LATER
    )

    assert reading.stage is JobStage.READING


@pytest.mark.parametrize("name", ["processed", "changed", "skipped"])
def test_a_counter_never_goes_down(name: str) -> None:
    ahead = begun().advance(
        stage=JobStage.READING,
        counters=JobCounters(processed=5, changed=5, skipped=5),
        at=LATER,
    )
    lower = {"processed": 5, "changed": 5, "skipped": 5} | {name: 4}

    with pytest.raises(ProgressWentBackwards):
        ahead.advance(stage=JobStage.READING, counters=JobCounters(**lower), at=LATER)


def test_a_warning_never_disappears() -> None:
    warned = begun().advance(
        stage=JobStage.READING,
        counters=JobCounters(),
        warnings=[JobWarning.UNRESOLVED_READERS],
        at=LATER,
    )

    with pytest.raises(ProgressWentBackwards):
        warned.advance(stage=JobStage.READING, counters=JobCounters(), at=LATER)


def test_the_heartbeat_never_goes_back_in_time() -> None:
    progress = begun().advance(stage=JobStage.READING, counters=JobCounters(), at=LATER)

    with pytest.raises(ProgressWentBackwards):
        progress.advance(
            stage=JobStage.READING,
            counters=JobCounters(),
            at=LATER - timedelta(microseconds=1),
        )


def test_an_ended_attempt_takes_no_more_reports() -> None:
    ended = begun().finish(outcome=AttemptOutcome.SUCCEEDED, at=LATER)

    with pytest.raises(AttemptFinished):
        ended.advance(stage=JobStage.READING, counters=JobCounters(), at=LATER)


def test_ending_twice_the_same_way_changes_nothing() -> None:
    ended = begun().finish(outcome=AttemptOutcome.FAILED, at=LATER)

    assert ended.finish(outcome=AttemptOutcome.FAILED, at=LATER) is ended


def test_an_ended_attempt_cannot_end_another_way() -> None:
    ended = begun().finish(outcome=AttemptOutcome.FAILED, at=LATER)

    with pytest.raises(AttemptFinished):
        ended.finish(outcome=AttemptOutcome.SUCCEEDED, at=LATER)


@pytest.mark.parametrize("value", [-1, MAX_COUNT + 1, True, 1.0, "1"])
def test_a_counter_is_a_bounded_whole_number(value: object) -> None:
    with pytest.raises(InvalidJob):
        JobCounters(processed=value)  # type: ignore[arg-type]


def test_a_report_time_must_be_utc() -> None:
    kyiv = timezone(timedelta(hours=2))

    with pytest.raises(InvalidJob):
        begun().advance(
            stage=JobStage.READING,
            counters=JobCounters(),
            at=LATER.astimezone(kyiv),
        )


def test_a_warning_must_be_a_known_one() -> None:
    with pytest.raises(InvalidJob):
        JobProgress(
            job_id=JobId.new(),
            attempt=1,
            stage=JobStage.READING,
            counters=JobCounters(),
            warnings=frozenset({"token=secret"}),  # type: ignore[arg-type]
            started_at=STARTED_AT,
            heartbeat_at=STARTED_AT,
        )


def test_the_attempt_number_follows_the_failed_attempts() -> None:
    first = running_job()
    failed = first.fail(
        error_code=JobFailure.SOURCE_UNAVAILABLE.value,
        finished_at=LATER,
        retry_at=LATER + timedelta(minutes=1),
    )
    second = failed.retry(queued_at=LATER + timedelta(minutes=1)).start(
        started_at=LATER + timedelta(minutes=2)
    )

    assert attempt_number(first) == 1
    assert attempt_number(failed) == 1
    assert attempt_number(second) == 2
    assert attempt_number(second.succeed(finished_at=second.started_at)) == 2  # type: ignore[arg-type]


def test_a_queued_job_has_no_attempt_under_way() -> None:
    with pytest.raises(InvalidJob):
        attempt_number(
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=uuid4(),
                queued_at=QUEUED_AT,
            )
        )


def test_the_outcome_of_a_finished_job_is_typed() -> None:
    job = running_job()
    retry_at = LATER + timedelta(minutes=1)

    def failed(code: JobFailure, retry: datetime | None = None) -> Job:
        return job.fail(error_code=code.value, finished_at=LATER, retry_at=retry)

    assert outcome_of(job.succeed(finished_at=LATER)) is AttemptOutcome.SUCCEEDED
    assert (
        outcome_of(job.succeed(finished_at=LATER), [JobWarning.UNRESOLVED_READERS])
        is AttemptOutcome.SUCCEEDED_WITH_WARNINGS
    )
    assert (
        outcome_of(failed(JobFailure.SOURCE_UNAVAILABLE, retry_at))
        is AttemptOutcome.RETRY_SCHEDULED
    )
    assert outcome_of(failed(JobFailure.SUPERSEDED)) is AttemptOutcome.SUPERSEDED
    assert outcome_of(failed(JobFailure.PROGRAMMING_ERROR)) is AttemptOutcome.FAILED


def test_a_running_job_has_no_outcome_yet() -> None:
    with pytest.raises(InvalidJob):
        outcome_of(running_job())


# --- Found by the adversarial review ---------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"stage": "reading"},
        {"counters": {"processed": 1}},
        {"warnings": ["unresolved_readers"]},
        {"outcome": "succeeded", "finished_at": LATER},
        {"attempt": 2**31},
    ],
)
def test_every_field_of_an_attempt_has_its_own_type(change: dict[str, object]) -> None:
    fields: dict[str, object] = {
        "job_id": JobId.new(),
        "attempt": 1,
        "stage": JobStage.READING,
        "counters": JobCounters(),
        "warnings": frozenset(),
        "started_at": STARTED_AT,
        "heartbeat_at": LATER,
    }

    with pytest.raises(InvalidJob):
        JobProgress(**(fields | change))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "report",
    [
        {"stage": "reading", "counters": JobCounters()},
        {"stage": JobStage.READING, "counters": {"processed": 1}},
        {"stage": JobStage.READING, "counters": JobCounters(), "warnings": None},
    ],
)
def test_a_report_of_the_wrong_type_is_a_typed_error(report: dict[str, object]) -> None:
    with pytest.raises(InvalidJob):
        begun().advance(at=LATER, **report)  # type: ignore[arg-type]


def test_an_attempt_ending_before_its_heartbeat_pulls_the_heartbeat_back() -> None:
    progress = begun().advance(stage=JobStage.READING, counters=JobCounters(), at=LATER)

    ended = progress.finish(outcome=AttemptOutcome.SUCCEEDED, at=STARTED_AT)

    assert ended.finished_at == ended.heartbeat_at == STARTED_AT
```

**Перевірка:** `uv run pytest tests/test_job_progress.py -q` показує `33 passed`.

### Крок 3 — Крок із кодом: зберігати спроби в PostgreSQL

Цей крок додає таблицю, функції, що її читають і пишуть, і вчить purge записувати свої спроби.

**Простими словами:** нова таблиця тримає один рядок на спробу, з перевірками, що повторюють правила домену. Одна функція зберігає рядок, коли спроба починається, друга записує звіт під блокуваннями з блоків 2 і 3, третя записує завершення в транзакції, що зберігає завершений Job. Невеликий модуль-воркфлоу виконує кожен звіт в окремій транзакції і читає Job із найновішою спробою для API. Purge тепер записує кожну свою спробу.

| Частина | Що вона робить |
|---|---|
| Міграція `c9d0e1f2a3b4` і `job_attempts` | таблиця, що видаляється разом зі своїм Job (`ON DELETE CASCADE`) |
| `lock_running_job` | `SELECT ... WHERE status = 'running' FOR SHARE` (блок 3) |
| `begin_attempt` | вставляє рядок; друга вставка кидає `AttemptAlreadyBegun` |
| `advance_attempt` | перевірка спроби, що працює, перевірка годинника на п'ять хвилин, блокування рядка, доменна перевірка, `UPDATE` з умовами |
| `finish_attempt` | блокує Job, відмовляє спробі, від якої Job пішов далі, зберігає більші лічильники, записує результат |
| `report_progress` / `DatabaseJobReports` | один звіт на транзакцію; один знімок на читання API |

**Дія 1:** створи міграцію.

Створи `migrations/versions/c9d0e1f2a3b4_job_attempt_progress.py`:

```python
"""job attempt progress

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-11-24 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"


def upgrade() -> None:
    op.create_table(
        "job_attempts",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("processed", sa.BigInteger(), nullable=False),
        sa.Column("changed", sa.BigInteger(), nullable=False),
        sa.Column("skipped", sa.BigInteger(), nullable=False),
        sa.Column("warnings", ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt >= 1", name="ck_job_attempts_attempt"),
        sa.CheckConstraint(
            "stage in ('starting', 'reading', 'writing', 'cleaning_up')",
            name="ck_job_attempts_stage",
        ),
        sa.CheckConstraint(
            "processed >= 0 and changed >= 0 and skipped >= 0",
            name="ck_job_attempts_counters",
        ),
        sa.CheckConstraint(
            "warnings <@ array['unresolved_readers', 'quarantined_items']::varchar[]",
            name="ck_job_attempts_warnings",
        ),
        sa.CheckConstraint(
            "outcome is null or outcome in ('succeeded', 'succeeded_with_warnings',"
            " 'retry_scheduled', 'superseded', 'failed')",
            name="ck_job_attempts_outcome",
        ),
        sa.CheckConstraint(
            "(outcome is null) = (finished_at is null)",
            name="ck_job_attempts_finished_at",
        ),
        sa.CheckConstraint(
            "heartbeat_at >= started_at and (finished_at is null"
            " or finished_at >= heartbeat_at)",
            name="ck_job_attempts_times",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.job_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "attempt"),
    )


def downgrade() -> None:
    op.drop_table("job_attempts")
```

**Дія 2:** оголоси таблицю в схемі. Спершу імпортуй `ARRAY`:

У `src/knowledge_service/persistence/schema.py` знайди:

```python

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

metadata = sa.MetaData()
```

і заміни на:

```python

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR

metadata = sa.MetaData()
```

потім додай таблицю після таблиці `jobs`:

У `src/knowledge_service/persistence/schema.py` знайди:

```python
    ),
)

audit_events = sa.Table(
    "audit_events",
    metadata,
```

і заміни на:

```python
    ),
)

# One row per attempt of a Job: how far it got, and how it ended.
job_attempts = sa.Table(
    "job_attempts",
    metadata,
    sa.Column(
        "job_id",
        sa.Uuid(),
        sa.ForeignKey("jobs.job_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("attempt", sa.Integer(), primary_key=True),
    sa.Column("stage", sa.String(length=16), nullable=False),
    sa.Column("processed", sa.BigInteger(), nullable=False),
    sa.Column("changed", sa.BigInteger(), nullable=False),
    sa.Column("skipped", sa.BigInteger(), nullable=False),
    sa.Column("warnings", ARRAY(sa.String(length=32)), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("outcome", sa.String(length=32), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("attempt >= 1", name="ck_job_attempts_attempt"),
    sa.CheckConstraint(
        "stage in ('starting', 'reading', 'writing', 'cleaning_up')",
        name="ck_job_attempts_stage",
    ),
    sa.CheckConstraint(
        "processed >= 0 and changed >= 0 and skipped >= 0",
        name="ck_job_attempts_counters",
    ),
    sa.CheckConstraint(
        "warnings <@ array['unresolved_readers', 'quarantined_items']::varchar[]",
        name="ck_job_attempts_warnings",
    ),
    sa.CheckConstraint(
        "outcome is null or outcome in ('succeeded', 'succeeded_with_warnings',"
        " 'retry_scheduled', 'superseded', 'failed')",
        name="ck_job_attempts_outcome",
    ),
    sa.CheckConstraint(
        "(outcome is null) = (finished_at is null)",
        name="ck_job_attempts_finished_at",
    ),
    sa.CheckConstraint(
        "heartbeat_at >= started_at and (finished_at is null"
        " or finished_at >= heartbeat_at)",
        name="ck_job_attempts_times",
    ),
)

audit_events = sa.Table(
    "audit_events",
    metadata,
```

**Дія 3:** додай `lock_running_job` перед `save_job`:

У `src/knowledge_service/persistence/jobs.py` знайди:

```python
    return _job_from(dict(record))


async def save_job(connection: AsyncConnection, *, job: Job) -> Job:
    """Store the status a Job has reached.
```

і заміни на:

```python
    return _job_from(dict(record))


async def lock_running_job(connection: AsyncConnection, *, job_id: JobId) -> Job | None:
    """Share-lock a running Job's row, or return None if it is not running.

    Only a running row is locked, so a refused caller never holds back the
    relay, whose `SKIP LOCKED` would pass over a locked failed Job.
    """
    statement = (
        sa.select(jobs)
        .where(jobs.c.job_id == job_id.value, jobs.c.status == JobStatus.RUNNING.value)
        .with_for_update(read=True)
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def save_job(connection: AsyncConnection, *, job: Job) -> Job:
    """Store the status a Job has reached.
```

**Дія 4:** створи сховище спроб.

Створи `src/knowledge_service/persistence/job_attempts.py`:

```python
"""Store the progress and outcome of each attempt of a Job."""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Final, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.identifiers import JobId
from knowledge_service.job_progress import (
    AttemptOutcome,
    JobCounters,
    JobProgress,
    JobStage,
    JobWarning,
    ProgressWentBackwards,
    ReportFromTheFuture,
    attempt_number,
    outcome_of,
)
from knowledge_service.jobs import InvalidJob, Job
from knowledge_service.persistence.jobs import load_job, lock_running_job
from knowledge_service.persistence.schema import job_attempts

# How far ahead of the database's clock a report may be. A heartbeat far in
# the future would refuse every honest report after it.
MAX_REPORT_SKEW: Final = timedelta(minutes=5)


class AttemptNotCurrent(InvalidJob):
    """The Job is not running this attempt, so its report is refused.

    The attempt ended, or the Job moved on to another one, as when a worker
    that was presumed dead wakes up and reports.
    """


class AttemptAlreadyBegun(InvalidJob):
    """The attempt's progress was already stored when it began."""


def _row(progress: JobProgress) -> dict[str, object]:
    return {
        "job_id": progress.job_id.value,
        "attempt": progress.attempt,
        "stage": progress.stage.value,
        "processed": progress.counters.processed,
        "changed": progress.counters.changed,
        "skipped": progress.counters.skipped,
        "warnings": sorted(warning.value for warning in progress.warnings),
        "started_at": progress.started_at,
        "heartbeat_at": progress.heartbeat_at,
        "outcome": None if progress.outcome is None else progress.outcome.value,
        "finished_at": progress.finished_at,
    }


def _progress_from(record: Mapping[str, object]) -> JobProgress:
    try:
        stage = JobStage(cast(str, record["stage"]))
        warnings = frozenset(
            JobWarning(value) for value in cast("list[str]", record["warnings"])
        )
        outcome = record["outcome"]
        finished = cast("datetime | None", record["finished_at"])
        ended = None if outcome is None else AttemptOutcome(cast(str, outcome))
    except ValueError:
        raise InvalidJob("stored attempt holds an unknown value") from None
    return JobProgress(
        job_id=JobId(cast(UUID, record["job_id"])),
        attempt=cast(int, record["attempt"]),
        stage=stage,
        counters=JobCounters(
            processed=cast(int, record["processed"]),
            changed=cast(int, record["changed"]),
            skipped=cast(int, record["skipped"]),
        ),
        warnings=warnings,
        # Read in UTC whatever the session's time zone.
        started_at=cast(datetime, record["started_at"]).astimezone(UTC),
        heartbeat_at=cast(datetime, record["heartbeat_at"]).astimezone(UTC),
        outcome=ended,
        finished_at=None if finished is None else finished.astimezone(UTC),
    )


async def _load(
    connection: AsyncConnection, *, job_id: JobId, attempt: int, for_update: bool
) -> JobProgress | None:
    statement = sa.select(job_attempts).where(
        job_attempts.c.job_id == job_id.value, job_attempts.c.attempt == attempt
    )
    if for_update:
        statement = statement.with_for_update()
    record = (await connection.execute(statement)).mappings().one_or_none()
    return None if record is None else _progress_from(dict(record))


async def load_latest_attempt(
    connection: AsyncConnection, *, job_id: JobId
) -> JobProgress | None:
    """The progress of a Job's newest attempt, if it ever started one."""
    statement = (
        sa.select(job_attempts)
        .where(job_attempts.c.job_id == job_id.value)
        .order_by(job_attempts.c.attempt.desc())
        .limit(1)
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    return None if record is None else _progress_from(dict(record))


async def begin_attempt(connection: AsyncConnection, *, job: Job) -> JobProgress:
    """Store the progress of the attempt a running Job has just started.

    Call it in the transaction that stores the claim, so a report never
    finds a running attempt without its row.
    """
    progress = JobProgress.begin(job)
    inserted = await connection.scalar(
        insert(job_attempts)
        .values(_row(progress))
        .on_conflict_do_nothing()
        .returning(job_attempts.c.attempt)
    )
    if inserted is None:
        raise AttemptAlreadyBegun("the attempt's progress is already stored")
    return progress


async def advance_attempt(
    connection: AsyncConnection,
    *,
    job_id: JobId,
    attempt: int,
    stage: JobStage,
    counters: JobCounters,
    warnings: Iterable[JobWarning] = (),
    now: datetime,
) -> JobProgress:
    """Record a report of a running attempt; nothing in it may go back.

    The Job's row is share-locked first, only while it runs, so the Job
    cannot finish or move to another attempt until this report commits; a
    report from an attempt the Job is no longer running is refused with
    `AttemptNotCurrent`, and one stamped more than `MAX_REPORT_SKEW` ahead of
    the database's clock with `ReportFromTheFuture`.
    """
    job = await lock_running_job(connection, job_id=job_id)
    if job is None or attempt_number(job) != attempt:
        raise AttemptNotCurrent("the job is not running this attempt")
    database_now = await connection.scalar(sa.select(sa.func.now()))
    if database_now is None or now > database_now + MAX_REPORT_SKEW:
        raise ReportFromTheFuture("the report is stamped ahead of the database")
    stored = await _load(connection, job_id=job_id, attempt=attempt, for_update=True)
    if stored is None:
        raise AttemptNotCurrent("the attempt was never started")
    advanced = stored.advance(stage=stage, counters=counters, warnings=warnings, at=now)
    await _save(connection, advanced, earlier=stored)
    return advanced


async def finish_attempt(
    connection: AsyncConnection,
    *,
    job: Job,
    counters: JobCounters | None = None,
) -> JobProgress:
    """Record how the attempt a finished Job just ended, with its outcome.

    Call it in the transaction that stores the finished Job. `counters`, if
    given, are the attempt's final totals; a report that raced in with
    higher ones keeps them. An attempt that never stored its start, such as
    one whose transaction was lost, is recorded here. Nothing a report
    stored can stop the outcome from being recorded; ending an attempt
    the Job has moved past raises `AttemptNotCurrent`, which rolls back the
    stale worker's whole transaction.
    """
    if job.finished_at is None or job.started_at is None:
        raise InvalidJob("only a finished job ends an attempt")
    # The Job's row first, then the attempt's, the order a report takes.
    await load_job(connection, job_id=job.job_id, for_update=True)
    attempt = attempt_number(job)
    newer = await connection.scalar(
        sa.select(sa.func.max(job_attempts.c.attempt)).where(
            job_attempts.c.job_id == job.job_id.value
        )
    )
    if newer is not None and newer > attempt:
        # A worker presumed dead ends an attempt its Job has moved past.
        raise AttemptNotCurrent("the job moved on to a later attempt")
    stored = await _load(
        connection, job_id=job.job_id, attempt=attempt, for_update=True
    )
    if stored is None:
        started = JobProgress(
            job_id=job.job_id,
            attempt=attempt,
            stage=JobStage.STARTING,
            counters=JobCounters(),
            warnings=frozenset(),
            started_at=job.started_at,
            heartbeat_at=job.started_at,
        )
        finished = _ended(started, job=job, counters=counters)
        await connection.execute(sa.insert(job_attempts).values(_row(finished)))
        return finished
    finished = _ended(stored, job=job, counters=counters)
    if finished is not stored:
        await _save(connection, finished, earlier=stored, ending=True)
    return finished


def _ended(
    progress: JobProgress, *, job: Job, counters: JobCounters | None
) -> JobProgress:
    finished_at = cast(datetime, job.finished_at)
    if counters is not None and progress.outcome is None:
        progress = replace(progress, counters=progress.counters.merged(counters))
    return progress.finish(outcome=outcome_of(job, progress.warnings), at=finished_at)


async def _save(
    connection: AsyncConnection,
    progress: JobProgress,
    *,
    earlier: JobProgress,
    ending: bool = False,
) -> None:
    """Update an unfinished attempt, only if nothing in it would go back.

    The conditions repeat the domain's checks in SQL, so even a writer that
    skipped them cannot move a counter or the heartbeat backwards. Only
    `ending` may pull the heartbeat back, to the Job's finish time.
    """
    columns = job_attempts.c
    conditions = [
        columns.job_id == earlier.job_id.value,
        columns.attempt == earlier.attempt,
        columns.outcome.is_(None),
        columns.processed <= progress.counters.processed,
        columns.changed <= progress.counters.changed,
        columns.skipped <= progress.counters.skipped,
        columns.warnings.contained_by(_row(progress)["warnings"]),
    ]
    if not ending:
        conditions.append(columns.heartbeat_at <= progress.heartbeat_at)
    result = await connection.execute(
        sa.update(job_attempts).where(*conditions).values(_row(progress))
    )
    if result.rowcount != 1:
        raise ProgressWentBackwards("the stored attempt is further along")
```

**Дія 5:** створи модуль-воркфлоу.

Створи `src/knowledge_service/job_reporting.py`:

```python
"""Report a running Job's progress, and read a Job with its latest attempt.

Each report commits on its own, so the progress is visible, and survives,
while the Job still runs. A handler calls `report_progress`; the worker and
its queue messages are never involved.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId
from knowledge_service.job_progress import (
    JobCounters,
    JobProgress,
    JobStage,
    JobWarning,
)
from knowledge_service.jobs import Job, JobKind
from knowledge_service.persistence.job_attempts import (
    advance_attempt,
    load_latest_attempt,
)
from knowledge_service.persistence.jobs import load_job


@dataclass(frozen=True, slots=True)
class JobReport:
    """A Job as stored, and the progress of its newest attempt, if any."""

    job: Job
    latest_attempt: JobProgress | None


async def report_progress(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    attempt: int,
    stage: JobStage,
    counters: JobCounters,
    warnings: Iterable[JobWarning] = (),
    now: datetime,
) -> JobProgress:
    """Store one progress report of a running attempt, in its own transaction.

    `counters` and `warnings` are totals so far, not increments, so sending
    the same report twice changes nothing. A counter, a warning, or the
    heartbeat that would go back raises `ProgressWentBackwards`; a report
    for an attempt the Job is not running raises `AttemptNotCurrent`.
    """
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")
    async with runtime.transaction() as connection:
        return await advance_attempt(
            connection,
            job_id=job_id,
            attempt=attempt,
            stage=stage,
            counters=counters,
            warnings=warnings,
            now=now,
        )


@dataclass(frozen=True, slots=True)
class DatabaseJobReports:
    """Reads Jobs and their progress from PostgreSQL for the API."""

    runtime: DatabaseRuntime

    async def load(
        self, *, job_id: JobId, kinds: frozenset[JobKind]
    ) -> JobReport | None:
        """The Job and its newest attempt, or None if it is not of `kinds`.

        Both are read from one snapshot, so a Job that finishes meanwhile is
        never shown as running with an ended attempt.
        """
        async with self.runtime.transaction() as connection:
            await connection.execute(
                sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            )
            job = await load_job(connection, job_id=job_id)
            if job is None or job.kind not in kinds:
                return None
            latest = await load_latest_attempt(connection, job_id=job_id)
        return JobReport(job=job, latest_attempt=latest)
```

**Дія 6:** навчи purge записувати свої спроби. Спершу імпорти:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
    RetryPolicy,
    fail_attempt,
)
from knowledge_service.jobs import (
    InvalidJob,
    Job,
```

і заміни на:

```python
    RetryPolicy,
    fail_attempt,
)
from knowledge_service.job_progress import JobCounters
from knowledge_service.jobs import (
    InvalidJob,
    Job,
```

і:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
    load_unpurged_documents,
    upsert_document,
)
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
```

і заміни на:

```python
    load_unpurged_documents,
    upsert_document,
)
from knowledge_service.persistence.job_attempts import finish_attempt
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
```

потім запам'ятай, чи були видалені Chunks:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
                        document_id=DocumentId(job.target_id),
                        for_update=True,
                    )
                    if (
                        document is not None
                        and document.availability is DocumentAvailability.TOMBSTONED
```

і заміни на:

```python
                        document_id=DocumentId(job.target_id),
                        for_update=True,
                    )
                    purged = False
                    if (
                        document is not None
                        and document.availability is DocumentAvailability.TOMBSTONED
```

запиши кожне завершення, невдале чи успішне, перед збереженням Job:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
                        await delete_chunks(
                            connection, document_id=document.document_id
                        )
            except Exception as error:
                cause = error
                failed = fail_attempt(job, error, now=now, policy=retry_policy)
                return await save_job(connection, job=failed)
            return await save_job(connection, job=job.succeed(finished_at=now))
    except InvalidJob:
        raise
    except Exception as error:
```

і заміни на:

```python
                        await delete_chunks(
                            connection, document_id=document.document_id
                        )
                        purged = True
            except Exception as error:
                cause = error
                failed = fail_attempt(job, error, now=now, policy=retry_policy)
                await finish_attempt(connection, job=failed)
                return await save_job(connection, job=failed)
            succeeded = job.succeed(finished_at=now)
            # One Document looked at: purged, or skipped as restored or gone.
            await finish_attempt(
                connection,
                job=succeeded,
                counters=JobCounters(
                    processed=1, changed=int(purged), skipped=int(not purged)
                ),
            )
            return await save_job(connection, job=succeeded)
    except InvalidJob:
        raise
    except Exception as error:
```

і завершення спроби, чия транзакція втрачена:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
        failed = fail_attempt(
            stored.start(started_at=now), cause, now=now, policy=retry_policy
        )
        return await save_job(connection, job=failed)
```

і заміни на:

```python
        failed = fail_attempt(
            stored.start(started_at=now), cause, now=now, policy=retry_policy
        )
        await finish_attempt(connection, job=failed)
        return await save_job(connection, job=failed)
```

**Перевірка:** `uv run pyright src` показує 0 помилок; `uv run pytest tests/test_indexes.py -q` показує `4 passed`; `uv run pytest tests/test_migrations_integration.py -q -m integration` показує `3 passed`; `uv run pytest tests/test_deletions_integration.py tests/test_job_retry_integration.py -q -m integration` показує `27 passed`.

### Крок 4 — Крок із кодом: показати Job синхронізації через HTTP

Цей крок додає `GET /v1/jobs/{job_id}` і форму його відповіді.

**Простими словами:** ендпоінт перевіряє, хто питає і чи може цей користувач запускати синхронізації, потім перевіряє написання ID, а тоді просить у сховища Job. Він показує лише Job синхронізації з їхньою найновішою спробою; будь-що інше виглядає точно як Job, якого не існує.

| Частина | Що вона робить |
|---|---|
| `JobAttemptProgress`, `SynchronizationJobReport` | відповідь: статус Job, часи, код помилки і найновіша спроба або `null` |
| `JobReports` | протокол, який приймає `create_app`; `DatabaseJobReports` — його реалізація |
| `read_synchronization_job` | таблиця з блоку 5 |
| `test_app_authentication.py` | вносить маршрут зі своєю операцією в список, додає фейкове сховище і перевіряє, що ендпоінт ніколи не показує інший вид |

**Дія 1:** у контрактах імпортуй `datetime`:

У `src/knowledge_service/contracts.py` знайди:

```python
"""Pydantic models for the knowledge service."""

from uuid import UUID

from pydantic import BaseModel, Field
```

і заміни на:

```python
"""Pydantic models for the knowledge service."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field
```

і додай дві моделі відповіді в кінці:

У `src/knowledge_service/contracts.py` знайди:

```python
    code: str
    message: str
    request_id: UUID
```

і заміни на:

```python
    code: str
    message: str
    request_id: UUID


class JobAttemptProgress(BaseModel):
    """How far one attempt of a Job got, and how it ended."""

    attempt: int = Field(ge=1)
    stage: str
    processed: int = Field(ge=0)
    changed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    warnings: list[str]
    started_at: datetime
    heartbeat_at: datetime
    outcome: str | None
    finished_at: datetime | None


class SynchronizationJobReport(BaseModel):
    """A synchronization Job and the progress of its newest attempt."""

    job_id: UUID
    source_id: UUID
    status: str
    failed_attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    retry_at: datetime | None
    latest_attempt: JobAttemptProgress | None
```

**Дія 2:** у застосунку імпортуй те, що потрібно ендпоінту:

У `src/knowledge_service/app.py` знайди:

```python
    AnswerStream,
    AnswerStreamer,
)
from knowledge_service.contracts import Answer, ErrorResponse, Question
from knowledge_service.errors import (
    FailureKind,
    classify_failure,
    policy_for,
)
from knowledge_service.identifiers import ConversationId, UserId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.roles import Operation, PermissionDenied, require
from knowledge_service.workflow import AnswerWorkflow
```

і заміни на:

```python
    AnswerStream,
    AnswerStreamer,
)
from knowledge_service.contracts import (
    Answer,
    ErrorResponse,
    JobAttemptProgress,
    Question,
    SynchronizationJobReport,
)
from knowledge_service.errors import (
    FailureKind,
    classify_failure,
    policy_for,
)
from knowledge_service.identifiers import ConversationId, JobId, UserId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import JobKind
from knowledge_service.roles import Operation, PermissionDenied, require
from knowledge_service.workflow import AnswerWorkflow
```

додай протокол, видимі види і перетворення на відповідь після `ConversationStore`:

У `src/knowledge_service/app.py` знайди:

```python
    ) -> None: ...


class _NotAuthenticated(Exception):
    def __init__(self, error: AuthenticationError) -> None:
        self.error = error
```

і заміни на:

```python
    ) -> None: ...


class JobReports(Protocol):
    """Jobs and the progress of their newest attempt.

    `DatabaseJobReports` is the implementation. `load` returns None for a
    Job that does not exist or is not one of `kinds`.
    """

    async def load(
        self, *, job_id: JobId, kinds: frozenset[JobKind]
    ) -> JobReport | None: ...


# The Jobs the job endpoint shows: those a Synchronization Run starter owns.
VISIBLE_JOB_KINDS = frozenset({JobKind.SYNCHRONIZE_SOURCE})


def synchronization_job_report(report: JobReport) -> SynchronizationJobReport:
    """The API view of a synchronization Job and its newest attempt."""
    job, latest = report.job, report.latest_attempt
    return SynchronizationJobReport(
        job_id=job.job_id.value,
        source_id=job.target_id,
        status=job.status.value,
        failed_attempts=job.attempt,
        max_attempts=job.max_attempts,
        queued_at=job.queued_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error_code=job.last_error_code,
        retry_at=job.retry_at,
        latest_attempt=None
        if latest is None
        else JobAttemptProgress(
            attempt=latest.attempt,
            stage=latest.stage.value,
            processed=latest.counters.processed,
            changed=latest.counters.changed,
            skipped=latest.counters.skipped,
            warnings=sorted(warning.value for warning in latest.warnings),
            started_at=latest.started_at,
            heartbeat_at=latest.heartbeat_at,
            outcome=None if latest.outcome is None else latest.outcome.value,
            finished_at=latest.finished_at,
        ),
    )


class _NotAuthenticated(Exception):
    def __init__(self, error: AuthenticationError) -> None:
        self.error = error
```

прийми сховище в `create_app`:

У `src/knowledge_service/app.py` знайди:

```python
    streamer: AnswerStreamer | None = None,
    authenticator: RequestAuthenticator | None = None,
    conversations: ConversationStore | None = None,
) -> FastAPI:
    """The HTTP application.
```

і заміни на:

```python
    streamer: AnswerStreamer | None = None,
    authenticator: RequestAuthenticator | None = None,
    conversations: ConversationStore | None = None,
    job_reports: JobReports | None = None,
) -> FastAPI:
    """The HTTP application.
```

і додай ендпоінт після видалення розмови:

У `src/knowledge_service/app.py` знайди:

```python
        # The same whether it was deleted, missing, or another User's.
        return Response(status_code=204)

    return app
```

і заміни на:

```python
        # The same whether it was deleted, missing, or another User's.
        return Response(status_code=204)

    @app.get("/v1/jobs/{job_id}", response_model=SynchronizationJobReport)
    async def read_synchronization_job(
        request: Request, job_id: str
    ) -> SynchronizationJobReport | JSONResponse:
        # Who, then whether they may, then which Job.
        caller = await authenticate(request)
        require(caller.roles, Operation.START_SYNCHRONIZATION_RUNS)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(job_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if job_reports is None:
            return error_response(
                status_code=503,
                code="jobs_unavailable",
                message="Jobs are unavailable",
                request_id=request_id,
            )
        try:
            report = await job_reports.load(
                job_id=JobId.parse(job_id), kinds=VISIBLE_JOB_KINDS
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("job read failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        if report is None or report.job.kind not in VISIBLE_JOB_KINDS:
            # The same for a missing Job and for a Job of another kind.
            return error_response(
                status_code=404,
                code="job_not_found",
                message="No such synchronization job",
                request_id=request_id,
            )
        return synchronization_job_report(report)

    return app
```

**Дія 3:** у тестах автентифікації імпортуй `datetime`:

У `tests/test_app_authentication.py` знайди:

```python
"""

import logging
from typing import Any, cast
from uuid import uuid4
```

і заміни на:

```python
"""

import logging
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4
```

потім `JobId`:

У `tests/test_app_authentication.py` знайди:

```python
from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.generation import InvalidAnswerRequest
from knowledge_service.app import create_app
from knowledge_service.identity.context import (
    Authenticator,
    Caller,
```

і заміни на:

```python
from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.generation import InvalidAnswerRequest
from knowledge_service.app import create_app
from knowledge_service.identifiers import JobId
from knowledge_service.identity.context import (
    Authenticator,
    Caller,
```

і типи Job:

У `tests/test_app_authentication.py` знайди:

```python
    user_id_for,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.roles import ApplicationRole, Operation
from knowledge_service.workflow import AnswerWorkflow
from test_authorization_context import (
```

і заміни на:

```python
    user_id_for,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import Job, JobKind
from knowledge_service.roles import ApplicationRole, Operation
from knowledge_service.workflow import AnswerWorkflow
from test_authorization_context import (
```

внеси маршрут зі своєю операцією в список:

У `tests/test_app_authentication.py` знайди:

```python
    ("DELETE", "/v1/conversations/{conversation_id}"): (
        Operation.MANAGE_OWN_CONVERSATIONS
    ),
}
PROTECTED = [(route, operation) for route, operation in ROUTES.items() if operation]
# The routes that read a body, to check none is read before the role.
```

і заміни на:

```python
    ("DELETE", "/v1/conversations/{conversation_id}"): (
        Operation.MANAGE_OWN_CONVERSATIONS
    ),
    ("GET", "/v1/jobs/{job_id}"): Operation.START_SYNCHRONIZATION_RUNS,
}
PROTECTED = [(route, operation) for route, operation in ROUTES.items() if operation]
# The routes that read a body, to check none is read before the role.
```

додай фейкове сховище після `Deleting`:

У `tests/test_app_authentication.py` знайди:

```python
        self.calls.append(request)


def full_app(
    source: Source,
    workflow: Any = None,
    tokens: dict[str, Any] | None = None,
    conversations: Deleting | None = None,
) -> Any:
    """The app with every endpoint wired, so every route is real."""
    return create_app(
```

і заміни на:

```python
        self.calls.append(request)


class Reading:
    """A Job store that records each Job it is asked for, and finds none."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def load(self, **request: Any) -> None:
        self.calls.append(request)


def full_app(
    source: Source,
    workflow: Any = None,
    tokens: dict[str, Any] | None = None,
    conversations: Deleting | None = None,
    jobs: Reading | None = None,
) -> Any:
    """The app with every endpoint wired, so every route is real."""
    return create_app(
```

підключи його у `full_app`:

У `tests/test_app_authentication.py` знайди:

```python
        streamer=streamer(Model(), source=source),
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
        conversations=conversations or Deleting(),
    )
```

і заміни на:

```python
        streamer=streamer(Model(), source=source),
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
        conversations=conversations or Deleting(),
        job_reports=jobs or Reading(),
    )
```

врахуй його в тесті ролей:

У `tests/test_app_authentication.py` знайди:

```python
    roles: frozenset[ApplicationRole],
) -> None:
    source, workflow, conversations = Source(), Counting(), Deleting()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(source, workflow, {"token": token}, conversations)
    method, path = route

    async with client(app) as http:
        response = await http.request(
            method,
            path.replace("{conversation_id}", str(uuid4())),
            json={"text": QUESTION.text},
            headers={"Authorization": "Bearer token"},
        )

    allowed = bool(roles & MATRIX[operation])
    assert (response.status_code == 403) is not allowed
    assert bool(source.readers or workflow.calls or conversations.calls) is allowed


@pytest.mark.parametrize("path", WITH_BODY)
```

і заміни на:

```python
    roles: frozenset[ApplicationRole],
) -> None:
    source, workflow, conversations = Source(), Counting(), Deleting()
    jobs = Reading()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(source, workflow, {"token": token}, conversations, jobs)
    method, path = route

    async with client(app) as http:
        response = await http.request(
            method,
            path.replace("{conversation_id}", str(uuid4())).replace(
                "{job_id}", str(uuid4())
            ),
            json={"text": QUESTION.text},
            headers={"Authorization": "Bearer token"},
        )

    allowed = bool(roles & MATRIX[operation])
    assert (response.status_code == 403) is not allowed
    reached = source.readers or workflow.calls or conversations.calls or jobs.calls
    assert bool(reached) is allowed


@pytest.mark.parametrize("path", WITH_BODY)
```

і додай тест, що недбале сховище не може видати інший вид:

У `tests/test_app_authentication.py` знайди:

```python
        "request not authenticated: signature",
        "request not authenticated: missing",
    ]
```

і заміни на:

```python
        "request not authenticated: signature",
        "request not authenticated: missing",
    ]


async def test_the_job_endpoint_shows_only_synchronization_jobs() -> None:
    purge = Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_CONVERSATION,
        target_id=uuid4(),
        queued_at=datetime(2026, 11, 24, 9, 0, tzinfo=UTC),
    )

    class Careless(Reading):
        """A Job store that forgets to filter by kind."""

        async def load(self, **request: Any) -> JobReport:  # type: ignore[override]
            self.calls.append(request)
            return JobReport(job=purge, latest_attempt=None)

    app = full_app(Source(), tokens={"token": TOKENS["staff-token"]}, jobs=Careless())
    async with client(app) as http:
        response = await http.get(
            f"/v1/jobs/{purge.job_id.value}", headers={"Authorization": "Bearer token"}
        )

    # A purge's target, such as a Conversation, is never shown.
    assert response.status_code == 404
    assert str(purge.target_id) not in response.text
```

**Перевірка:** `uv run pytest tests/test_app_authentication.py -q` показує `107 passed`.

### Крок 5 — Крок із кодом: довести це на справжньому PostgreSQL

Цей крок перевіряє кожен блок «Перед початком» зі справжніми транзакціями, справжніми блокуваннями і справжнім HTTP.

**Простими словами:** кожен тест запускає Job синхронізації так, як це робитиме обробник M08-T10, надсилає звіти і перевіряє, що збережено і що показує API. Деякі тести вбивають runtime, що звітував, запускають звіти одночасно або дають застарілому воркеру спробувати писати.

| Тест | Що він доводить |
|---|---|
| `test_progress_survives_the_worker_that_reported_it` | блок 1 і API |
| `test_a_lower_counter_is_refused_and_the_stored_one_stays`, `test_an_older_heartbeat_or_a_lost_warning_is_refused` | блок 2 |
| `test_a_replayed_report_changes_nothing` | повтор |
| `test_reports_racing_each_other_never_lower_what_is_stored` | 30 звітів одночасно, не по порядку |
| `test_a_report_from_an_attempt_the_job_left_is_refused`, `test_a_finished_job_takes_no_more_reports`, `test_a_report_for_an_unknown_job_is_refused` | блок 3 |
| `test_the_database_refuses_a_counter_below_zero`, `test_deleting_a_job_deletes_its_attempts` | власні перевірки таблиці |
| `test_a_purge_records_its_attempt_and_outcome`, `test_each_purge_attempt_keeps_its_own_outcome` | purge і рядки на кожну спробу |
| `test_the_api_shows_a_queued_job_with_no_attempt_yet`, `test_the_api_hides_jobs_that_are_not_synchronizations` | блок 5 |
| тести під «Found by the adversarial review» | блок 6, по одному на знахідку |

Створи `tests/test_job_progress_integration.py`:

```python
"""Job progress and outcomes stored in PostgreSQL, read through the API.

A running attempt reports its stage, counters, warnings, and heartbeat; each
report commits on its own, so a new worker process, or the API, reads it
after the reporting one is gone. Nothing in a report may go back, and an
attempt the Job is no longer running cannot report. Each attempt keeps its
own row and typed outcome.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from authorization_scenarios import Person
from job_fixtures import (
    OBSERVED_AT,
    SOURCE_ID,
    database_went_away,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service import job_reporting
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import purge_document_chunks
from knowledge_service.job_failures import RetryPolicy
from knowledge_service.job_progress import (
    AttemptOutcome,
    JobCounters,
    JobProgress,
    JobStage,
    JobWarning,
    ProgressWentBackwards,
    ReportFromTheFuture,
)
from knowledge_service.job_reporting import DatabaseJobReports, report_progress
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.persistence.job_attempts import (
    AttemptAlreadyBegun,
    AttemptNotCurrent,
    advance_attempt,
    begin_attempt,
    finish_attempt,
    load_latest_attempt,
)
from knowledge_service.persistence.jobs import (
    enqueue_job,
    load_job,
    requeue_due_jobs,
    save_job,
)
from knowledge_service.persistence.schema import job_attempts, jobs
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings

pytestmark = pytest.mark.integration

STARTED_AT = OBSERVED_AT + timedelta(minutes=1)
LATER = STARTED_AT + timedelta(seconds=30)
POLICY = RetryPolicy(jitter=lambda: 0.5)

CALLERS = {
    "Bearer morgan": Caller(
        reader=Person("morgan", ("/engineering",)).context(),
        roles=frozenset({ApplicationRole.CONTENT_MANAGER}),
    ),
}


class Tokens:
    """Gives each test token the caller its claims would give."""

    async def authenticate(self, headers: list[str]) -> Caller:
        if len(headers) == 1 and headers[0] in CALLERS:
            return CALLERS[headers[0]]
        raise Unauthenticated("test", error=AuthenticationError.INVALID_TOKEN)


def new_runtime(url: str) -> DatabaseRuntime:
    return create_database_runtime(Settings(database_url=SecretStr(url)))


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = new_runtime(migrated_database)
    await insert_source(runtime)
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def running_synchronization(runtime: DatabaseRuntime) -> Job:
    """A synchronization Job whose claim committed, as its handler will do."""
    async with runtime.transaction() as connection:
        queued = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=SOURCE_ID.value,
                queued_at=OBSERVED_AT,
            ),
        )
        running = queued.job.start(started_at=STARTED_AT)
        await save_job(connection, job=running)
        await begin_attempt(connection, job=running)
    return running


async def latest(runtime: DatabaseRuntime, job_id: JobId) -> JobProgress:
    async with runtime.transaction() as connection:
        progress = await load_latest_attempt(connection, job_id=job_id)
    assert progress is not None
    return progress


async def report(
    runtime: DatabaseRuntime,
    job: Job,
    *,
    processed: int,
    at: timedelta = timedelta(seconds=30),
    attempt: int = 1,
    warnings: tuple[JobWarning, ...] = (),
) -> JobProgress:
    return await report_progress(
        runtime,
        job_id=job.job_id,
        attempt=attempt,
        stage=JobStage.READING,
        counters=JobCounters(processed=processed),
        warnings=warnings,
        now=STARTED_AT + at,
    )


async def read_over_http(runtime: DatabaseRuntime, job_id: JobId) -> httpx.Response:
    app = create_app(authenticator=Tokens(), job_reports=DatabaseJobReports(runtime))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.get(
            f"/v1/jobs/{job_id.value}", headers={"Authorization": "Bearer morgan"}
        )


async def test_progress_survives_the_worker_that_reported_it(
    migrated_database: str, runtime: DatabaseRuntime
) -> None:
    job = await running_synchronization(runtime)
    worker = new_runtime(migrated_database)
    await report_progress(
        worker,
        job_id=job.job_id,
        attempt=1,
        stage=JobStage.WRITING,
        counters=JobCounters(processed=120, changed=7, skipped=2),
        warnings=[JobWarning.QUARANTINED_ITEMS],
        now=LATER,
    )
    # The worker process ends; its connections go with it.
    await worker.dispose()

    response = await read_over_http(runtime, job.job_id)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "running"
    assert body["source_id"] == str(SOURCE_ID.value)
    assert body["latest_attempt"] == {
        "attempt": 1,
        "stage": "writing",
        "processed": 120,
        "changed": 7,
        "skipped": 2,
        "warnings": ["quarantined_items"],
        "started_at": "2026-11-21T09:01:00Z",
        "heartbeat_at": "2026-11-21T09:01:30Z",
        "outcome": None,
        "finished_at": None,
    }


async def test_a_lower_counter_is_refused_and_the_stored_one_stays(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await report(runtime, job, processed=50)

    with pytest.raises(ProgressWentBackwards):
        await report(runtime, job, processed=49, at=timedelta(minutes=1))

    stored = await latest(runtime, job.job_id)
    assert stored.counters.processed == 50
    assert stored.heartbeat_at == STARTED_AT + timedelta(seconds=30)


async def test_an_older_heartbeat_or_a_lost_warning_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await report(runtime, job, processed=1, warnings=(JobWarning.UNRESOLVED_READERS,))

    with pytest.raises(ProgressWentBackwards):
        await report(
            runtime,
            job,
            processed=2,
            at=timedelta(seconds=29),
            warnings=(JobWarning.UNRESOLVED_READERS,),
        )
    with pytest.raises(ProgressWentBackwards):
        await report(runtime, job, processed=2, at=timedelta(minutes=1))

    stored = await latest(runtime, job.job_id)
    assert stored.counters.processed == 1
    assert stored.warnings == {JobWarning.UNRESOLVED_READERS}


async def test_a_replayed_report_changes_nothing(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)
    first = await report(runtime, job, processed=10)

    again = await report(runtime, job, processed=10)

    assert again == first == await latest(runtime, job.job_id)


async def test_reports_racing_each_other_never_lower_what_is_stored(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    # Even totals high to low, then odd ones low to high: out of order.
    totals = [*range(30, 0, -2), *range(1, 31, 2)]

    async def send(total: int) -> int | None:
        try:
            await report(runtime, job, processed=total, at=timedelta(seconds=total))
        except ProgressWentBackwards:
            return None
        return total

    accepted = [t for t in await asyncio.gather(*map(send, totals)) if t]

    # Every report either landed or was refused as going back; the highest won.
    assert 30 in accepted
    assert (await latest(runtime, job.job_id)).counters.processed == 30


async def test_a_report_from_an_attempt_the_job_left_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await report(runtime, job, processed=5)
    retry_at = LATER + timedelta(minutes=1)
    async with runtime.transaction() as connection:
        failed = job.fail(
            error_code="source_unavailable", finished_at=LATER, retry_at=retry_at
        )
        await save_job(connection, job=failed)
        await finish_attempt(connection, job=failed)
        [requeued] = await requeue_due_jobs(connection, now=retry_at, limit=10)
        second = requeued.start(started_at=retry_at)
        await save_job(connection, job=second)
        await begin_attempt(connection, job=second)

    # The first attempt's worker wakes up and reports.
    with pytest.raises(AttemptNotCurrent):
        await report(runtime, job, processed=99, at=timedelta(minutes=5))

    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    job_attempts.c.attempt,
                    job_attempts.c.processed,
                    job_attempts.c.outcome,
                ).order_by(job_attempts.c.attempt)
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        (1, 5, "retry_scheduled"),
        (2, 0, None),
    ]


async def test_a_finished_job_takes_no_more_reports(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)
    async with runtime.transaction() as connection:
        succeeded = job.succeed(finished_at=LATER)
        await save_job(connection, job=succeeded)
        await finish_attempt(connection, job=succeeded)

    with pytest.raises(AttemptNotCurrent):
        await report(runtime, job, processed=1, at=timedelta(minutes=1))

    assert (await latest(runtime, job.job_id)).outcome is AttemptOutcome.SUCCEEDED


async def test_a_report_for_an_unknown_job_is_refused(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)

    with pytest.raises(AttemptNotCurrent):
        await report_progress(
            runtime,
            job_id=JobId.new(),
            attempt=1,
            stage=JobStage.READING,
            counters=JobCounters(),
            now=LATER,
        )
    with pytest.raises(AttemptNotCurrent):
        await report(runtime, job, processed=1, attempt=2)


async def test_the_database_refuses_a_counter_below_zero(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    with pytest.raises(IntegrityError):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.update(job_attempts)
                .where(job_attempts.c.job_id == job.job_id.value)
                .values(processed=-1)
            )


async def test_a_purge_records_its_attempt_and_outcome(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)

    await purge_document_chunks(runtime, job_id=purge_id, now=LATER)

    progress = await latest(runtime, purge_id)
    assert progress.attempt == 1
    assert progress.counters == JobCounters(processed=1, changed=1, skipped=0)
    assert progress.outcome is AttemptOutcome.SUCCEEDED
    assert progress.finished_at == LATER


async def test_each_purge_attempt_keeps_its_own_outcome(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    real_delete = deletions.delete_chunks

    async def lost_connection(*_args: object, **_kwargs: object) -> int:
        raise database_went_away()

    monkeypatch.setattr(deletions, "delete_chunks", lost_connection)
    failed = await purge_document_chunks(
        runtime, job_id=purge_id, now=LATER, retry_policy=POLICY
    )
    assert failed.retry_at is not None
    monkeypatch.setattr(deletions, "delete_chunks", real_delete)
    async with runtime.transaction() as connection:
        await requeue_due_jobs(connection, now=failed.retry_at, limit=10)
    await purge_document_chunks(runtime, job_id=purge_id, now=failed.retry_at)

    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(job_attempts.c.attempt, job_attempts.c.outcome)
                .where(job_attempts.c.job_id == purge_id.value)
                .order_by(job_attempts.c.attempt)
            )
        ).all()
    assert [tuple(row) for row in rows] == [(1, "retry_scheduled"), (2, "succeeded")]
    assert (await stored_job(runtime, purge_id)).status is JobStatus.SUCCEEDED


async def test_the_api_shows_a_queued_job_with_no_attempt_yet(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        queued = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=SOURCE_ID.value,
                queued_at=OBSERVED_AT,
            ),
        )

    response = await read_over_http(runtime, queued.job.job_id)

    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert response.json()["latest_attempt"] is None


async def test_the_api_hides_jobs_that_are_not_synchronizations(
    runtime: DatabaseRuntime,
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)

    hidden = await read_over_http(runtime, purge_id)
    missing = await read_over_http(runtime, JobId.new())

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["code"] == missing.json()["code"] == "job_not_found"


async def test_deleting_a_job_deletes_its_attempts(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)

    async with runtime.transaction() as connection:
        await connection.execute(
            sa.delete(jobs).where(jobs.c.job_id == job.job_id.value)
        )
        left = await connection.scalar(
            sa.select(sa.func.count()).select_from(job_attempts)
        )
        stored = await load_job(connection, job_id=job.job_id)

    assert left == 0
    assert stored is None


# --- Found by the adversarial review ---------------------------------------


async def test_a_heartbeat_later_than_the_finish_does_not_stop_the_finish(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    # The reporting worker's clock runs ten seconds ahead of the finisher's.
    await report(runtime, job, processed=3, at=timedelta(seconds=40))

    async with runtime.transaction() as connection:
        succeeded = job.succeed(finished_at=STARTED_AT + timedelta(seconds=30))
        await save_job(connection, job=succeeded)
        ended = await finish_attempt(connection, job=succeeded)

    assert ended.outcome is AttemptOutcome.SUCCEEDED
    # The attempt ends when its Job did; the skewed heartbeat is pulled back.
    assert ended.finished_at == ended.heartbeat_at == STARTED_AT + timedelta(seconds=30)
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.SUCCEEDED


async def test_final_counters_below_a_late_report_do_not_stop_the_finish(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await report(runtime, job, processed=10)

    async with runtime.transaction() as connection:
        succeeded = job.succeed(finished_at=LATER + timedelta(seconds=5))
        await save_job(connection, job=succeeded)
        ended = await finish_attempt(
            connection, job=succeeded, counters=JobCounters(processed=9, changed=2)
        )

    assert ended.counters == JobCounters(processed=10, changed=2)
    assert ended.outcome is AttemptOutcome.SUCCEEDED


async def test_a_report_from_a_clock_far_ahead_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    with pytest.raises(ReportFromTheFuture):
        await report(runtime, job, processed=1, at=timedelta(days=3650))

    # The attempt is not poisoned: an honest report still lands.
    assert (await report(runtime, job, processed=1)).counters.processed == 1


async def test_a_refused_report_does_not_hold_back_a_due_retry(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    retry_at = LATER + timedelta(minutes=1)
    async with runtime.transaction() as connection:
        failed = job.fail(
            error_code="source_unavailable", finished_at=LATER, retry_at=retry_at
        )
        await save_job(connection, job=failed)
        await finish_attempt(connection, job=failed)

    async with runtime.transaction() as stale:
        with pytest.raises(AttemptNotCurrent):
            await advance_attempt(
                stale,
                job_id=job.job_id,
                attempt=1,
                stage=JobStage.READING,
                counters=JobCounters(processed=1),
                now=retry_at,
            )
        # The stale report's transaction is still open.
        async with runtime.transaction() as relay:
            requeued = await requeue_due_jobs(relay, now=retry_at, limit=10)

    assert [j.job_id for j in requeued] == [job.job_id]


async def test_the_api_shows_the_job_and_its_attempt_from_one_moment(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await running_synchronization(runtime)
    real_load = job_reporting.load_latest_attempt

    async def finish_meanwhile(connection: object, *, job_id: JobId) -> object:
        # Another session finishes the Job between the API's two reads.
        async with runtime.transaction() as other:
            succeeded = job.succeed(finished_at=LATER)
            await save_job(other, job=succeeded)
            await finish_attempt(other, job=succeeded)
        return await real_load(connection, job_id=job_id)  # type: ignore[arg-type]

    monkeypatch.setattr(job_reporting, "load_latest_attempt", finish_meanwhile)

    body = (await read_over_http(runtime, job.job_id)).json()

    assert body["status"] == "running"
    assert body["latest_attempt"]["outcome"] is None


async def test_beginning_an_attempt_twice_is_a_typed_error(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    with pytest.raises(AttemptAlreadyBegun):
        async with runtime.transaction() as connection:
            await begin_attempt(connection, job=job)


async def test_a_stale_worker_cannot_end_an_attempt_the_job_left(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    retry_at = LATER + timedelta(minutes=1)
    stale = job.fail(
        error_code="source_unavailable", finished_at=LATER, retry_at=retry_at
    )
    async with runtime.transaction() as connection:
        await save_job(connection, job=stale)
        await finish_attempt(connection, job=stale)
        [requeued] = await requeue_due_jobs(connection, now=retry_at, limit=10)
        second = requeued.start(started_at=retry_at)
        await save_job(connection, job=second)
        await begin_attempt(connection, job=second)

    async def store_stale_outcome() -> None:
        # The first attempt's worker, presumed dead, stores its outcome again.
        async with runtime.transaction() as connection:
            await save_job(connection, job=stale)
            await finish_attempt(connection, job=stale)

    with pytest.raises(AttemptNotCurrent):
        await store_stale_outcome()

    stored = await stored_job(runtime, job.job_id)
    assert stored.status is JobStatus.RUNNING
    assert (await latest(runtime, job.job_id)).attempt == 2


async def test_an_attempt_reads_back_in_utc_whatever_the_session_time_zone(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    async with runtime.transaction() as connection:
        await connection.execute(sa.text("SET LOCAL TIME ZONE 'Asia/Tokyo'"))
        progress = await load_latest_attempt(connection, job_id=job.job_id)

    assert progress is not None
    assert progress.started_at == STARTED_AT
```

**Перевірка:** `uv run pytest tests/test_job_progress_integration.py -q -m integration` показує `22 passed`.

### Крок 6 — Без коду: зламай навмисно

Цей крок прибирає доменну перевірку лічильників і показує, який шар досі тримає.

**Дія 1:** у `JobProgress.advance` у `src/knowledge_service/job_progress.py` видали ці два рядки:

```python
        if not counters.covers(self.counters):
            raise ProgressWentBackwards("a counter would go down")
```

**Дія 2:** запусти:

```bash
uv run pytest tests/test_job_progress.py tests/test_job_progress_integration.py -q -m "integration or not integration"
```

Ти побачиш `3 failed, 52 passed`. Падають три доменні тести на зменшення лічильника. `test_a_lower_counter_is_refused_and_the_stored_one_stays` досі проходить: власна умова `UPDATE` `WHERE processed <= :new` не знайшла жодного рядка, тож база відмовила. Це другий шар із блоку 2.

**Дія 3:** поверни ці два рядки і запусти команду ще раз: `55 passed`.

**Перевірка:** порівняй кількість зі своїм прогнозом із кроку 1 і запиши її у файл доказів.

### Крок 7 — Без коду: онови три документи

Цей крок записує модель прогресу туди, де її шукатимуть читачі.

**Дія 1:** в архітектурі опиши ендпоінт після видалення розмови:

У `docs/architecture.md` знайди:

```markdown
`DatabaseConversationStore` (`answering.conversation_store`), which passes
the token's User as the owner and returns nothing, so the response is the same whether the
Conversation was deleted, missing, or another User's.

`job_dispatch` puts a Job on the queue without ever losing it. `submit_job`
commits the Job to PostgreSQL first, then sends one message naming only its
```

і заміни на:

```markdown
`DatabaseConversationStore` (`answering.conversation_store`), which passes
the token's User as the owner and returns nothing, so the response is the same whether the
Conversation was deleted, missing, or another User's.
`GET /v1/jobs/{job_id}` requires `start_synchronization_runs` before it
parses the ID, and shows a synchronization Job with its newest attempt
through `DatabaseJobReports` (`job_reporting`), read from one snapshot. A
missing Job and a Job of any other kind give the same 404, so a purge's
target, such as a Conversation, is never shown.

`job_dispatch` puts a Job on the queue without ever losing it. `submit_job`
commits the Job to PostgreSQL first, then sends one message naming only its
```

додай абзац про прогрес після абзацу про повтори:

У `docs/architecture.md` знайди:

```markdown
purge failed permanently is not given a new purge by later synchronization
passes; one that ran out of attempts on a passing failure is.

`leases` holds short-lived, owned locks in Redis. A lease is a key that
expires on its own, holding its holder's random token; renewing and
releasing compare that token inside Redis, so a holder whose lease expired
```

і заміни на:

```markdown
purge failed permanently is not given a new purge by later synchronization
passes; one that ran out of attempts on a passing failure is.

Each attempt of a Job has its own progress row (`job_progress`,
`persistence.job_attempts`): a stage, three counters (processed, changed,
skipped), warning codes, a heartbeat, and, once it ends, a typed outcome
(`succeeded`, `succeeded_with_warnings`, `retry_scheduled`, `superseded`, or
`failed`). A handler stores the row with its claim (`begin_attempt`),
reports totals so far with `report_progress`, each report in its own
transaction, and ends it with `finish_attempt` in the transaction that
stores the finished Job. A report shares the lock of the Job's row only
while the Job runs that attempt, so a worker that lost its attempt cannot
write; counters and warnings never go down and the heartbeat never goes
back, both in the domain and in the `UPDATE`'s own conditions; a report
stamped more than five minutes ahead of the database's clock is refused.
Ending never fails on what a report stored: higher counters stay, and a
heartbeat later than the Job's finish time is pulled back to it. A worker
presumed dead that ends an attempt its Job has moved past gets
`AttemptNotCurrent`, so its whole transaction rolls back. Nothing here knows
about queue messages; a later attempt never touches an earlier one's row.

`leases` holds short-lived, owned locks in Redis. A lease is a key that
expires on its own, holding its holder's random token; renewing and
releasing compare that token inside Redis, so a holder whose lease expired
```

і додай рядок таблиці після `jobs`:

У `docs/architecture.md` знайди:

```markdown
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code; a retry time only on a failed Job with attempts left, never before it finished; at most one open Job (queued, running, or failed with a retry scheduled) per kind and target. |
| `audit_events` | An action, target kind, decision (`allowed` or `denied`), and outcome from fixed lists, with optional request and Synchronization Run IDs; no raw content: metadata keys are short names, string values one plain word, and the bag is capped at 1024 bytes. Rows are only ever inserted. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |
```

і заміни на:

```markdown
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code; a retry time only on a failed Job with attempts left, never before it finished; at most one open Job (queued, running, or failed with a retry scheduled) per kind and target. |
| `job_attempts` | One row per `(job_id, attempt)`, removed with its Job; a known stage, warnings, and outcome; counters never below zero; an outcome exactly when it has a finish time; a heartbeat no earlier than the start and no later than the finish. |
| `audit_events` | An action, target kind, decision (`allowed` or `denied`), and outcome from fixed lists, with optional request and Synchronization Run IDs; no raw content: metadata keys are short names, string values one plain word, and the bag is capped at 1024 bytes. Rows are only ever inserted. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |
```

**Дія 2:** у посібнику з експлуатації скажи, де лежить прогрес і що означає heartbeat, який зупинився:

У `docs/operations.md` знайди:

```markdown

## Telemetry

Use OpenTelemetry for traces and metrics and structured JSON logs with shared request, Conversation, Synchronization Run, Evaluation Run, and job identifiers. Collect queue depth, run age, retry counts, retrieval-stage latency, Candidate counts, model tokens, estimated cost, streaming cancellation, and provider errors. Production content fields are redacted by default.

## Recovery and failure ownership
```

і заміни на:

```markdown

## Telemetry

Use OpenTelemetry for traces and metrics and structured JSON logs with shared request, Conversation, Synchronization Run, Evaluation Run, and job identifiers. Each Job attempt's stage, counters, warnings, heartbeat, and outcome are stored in `job_attempts` and shown by `GET /v1/jobs/{job_id}` for synchronization Jobs; a running attempt whose heartbeat stops moving is the sign of a dead worker. Collect queue depth, run age, retry counts, retrieval-stage latency, Candidate counts, model tokens, estimated cost, streaming cancellation, and provider errors. Production content fields are redacted by default.

## Recovery and failure ownership
```

**Дія 3:** у моделі загроз додай точку входу після видалення розмови:

У `docs/threat-model.md` знайди:

```markdown
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. Needs a bearer token and the `user` role, both checked before the body is read; the Evidence source searches as the token's User and Groups |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: needs a bearer token and the `user` role, then sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `DELETE /v1/conversations/{conversation_id}` | Any client that reaches the API | Deletes the caller's own Conversation. Needs a bearer token and the `manage_own_conversations` operation (the `user` role), both checked before the ID is parsed; the owner is the token's User, and only the canonical spelling of an ID is accepted; the answer is 204 whether the Conversation was deleted, missing, or another User's |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Redis, `127.0.0.1:6380` | Developers and local workers | Bound to 127.0.0.1, with no password: anyone on this machine can read or write its queues, so a worker checks every message argument again. It never evicts a key and keeps nothing across a restart |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
```

і заміни на:

```markdown
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. Needs a bearer token and the `user` role, both checked before the body is read; the Evidence source searches as the token's User and Groups |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: needs a bearer token and the `user` role, then sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `DELETE /v1/conversations/{conversation_id}` | Any client that reaches the API | Deletes the caller's own Conversation. Needs a bearer token and the `manage_own_conversations` operation (the `user` role), both checked before the ID is parsed; the owner is the token's User, and only the canonical spelling of an ID is accepted; the answer is 204 whether the Conversation was deleted, missing, or another User's |
| `GET /v1/jobs/{job_id}` | Any client that reaches the API | Shows one synchronization Job and its newest attempt's progress. Needs a bearer token and the `start_synchronization_runs` operation (the `content-manager` role), both checked before the ID is parsed; a missing Job and a Job of another kind give the same 404; no error text, only codes |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Redis, `127.0.0.1:6380` | Developers and local workers | Bound to 127.0.0.1, with no password: anyone on this machine can read or write its queues, so a worker checks every message argument again. It never evicts a key and keeps nothing across a restart |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
```

і сценарій зловживання перед рядком про запланований Job:

У `docs/threat-model.md` знайди:

```markdown
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |
| Two synchronization passes of one Source run at once: double Source calls, and an older pass finishing late re-grants a removed reader, rolls content back, or tombstones what a newer pass saw | `synchronize_source_exclusively` holds the Source's Redis lease, with an owner token, renewed after every page, so a second pass gets `SourceBusy` before reading; a crashed holder's lease expires in two minutes. The Source's fence in PostgreSQL decides who may write: every pass claims it with its observation time, and every narrowing, reconciliation, and tombstone checks it under a share lock on the Source's row, so after a newer pass claims it nothing of an older pass lands; an observation more than five minutes in the future is refused | Writes an older pass committed before the newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way; a pass observed up to five minutes in the future holds the fence until then |
| A scheduled synchronization Job carries a Source's location or credentials through Redis, or the scheduler floods the queue | A scheduled Job stores only the Source's ID, and its handler reads the configuration and credentials reference from PostgreSQL when it runs; a Source with an open Job is skipped, a missed schedule queues one Job, and a pass queues at most 1,000 | Disabling a Source does not cancel a Job already queued for it; a Source whose synchronization Job is stuck open is not scheduled again until M08-T08 |

### Retrieval
```

і заміни на:

```markdown
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |
| Two synchronization passes of one Source run at once: double Source calls, and an older pass finishing late re-grants a removed reader, rolls content back, or tombstones what a newer pass saw | `synchronize_source_exclusively` holds the Source's Redis lease, with an owner token, renewed after every page, so a second pass gets `SourceBusy` before reading; a crashed holder's lease expires in two minutes. The Source's fence in PostgreSQL decides who may write: every pass claims it with its observation time, and every narrowing, reconciliation, and tombstone checks it under a share lock on the Source's row, so after a newer pass claims it nothing of an older pass lands; an observation more than five minutes in the future is refused | Writes an older pass committed before the newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way; a pass observed up to five minutes in the future holds the fence until then |
| A stale or replayed worker rewrites a Job's progress, or a bad clock blocks it | A report shares the Job's row lock only while the Job runs that attempt, counters and warnings never go down and the heartbeat never goes back (checked again in the `UPDATE`), a report far ahead of the database's clock is refused, each attempt has its own row, and ending an attempt the Job has moved past is refused; ending an attempt never fails on what a report stored | No production handler reports progress until M08-T10; a Job whose worker died stays running with its last heartbeat until M08-T08; a worker more than five minutes ahead of the database cannot report |
| A scheduled synchronization Job carries a Source's location or credentials through Redis, or the scheduler floods the queue | A scheduled Job stores only the Source's ID, and its handler reads the configuration and credentials reference from PostgreSQL when it runs; a Source with an open Job is skipped, a missed schedule queues one Job, and a pass queues at most 1,000 | Disabling a Source does not cancel a Job already queued for it; a Source whose synchronization Job is stuck open is not scheduled again until M08-T08 |

### Retrieval
```

**Перевірка:** `make docs-check` завершується з кодом 0.

### Крок 8 — Без коду: перевірки, докази і прогрес

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

**Дія 2:** заверши файл доказів: результати перевірок і кількість із кроку 6 у «Verification», знахідки рев'ю й відомі обмеження в «Review», дизайн у «Design» і «Decision», а також свої відповіді на питання для рефлексії нижче. Потім познач завдання виконаним у `docs/curriculum/PROGRESS.md` і зроби коміт з `m08-t07: report progress and terminal outcomes`.

**Перевірка:** усі перевірки проходять, і після коміту `git status` чистий.

## Питання для рефлексії

- Воркер A звітував «оброблено 120» о 10:05, і його вбили о 10:06. Що показує `GET /v1/jobs/J7` о 10:30, і яке поле каже операторові, що над Job ніхто не працює?
- Звіт «оброблено 50» приходить після того, як збережено 60. Які дві перевірки його відхиляють, і навіщо друга, якщо є перша?
- Чому звіт несе підсумки («на зараз 120»), а не прирости («+20»)? Що зробив би повторений «+20»?
- Воркер спроби 1 прокидається після того, як почалась спроба 2. Чому він не може писати, і чому `lock_running_job` блокує лише рядки, що працюють?
- Який збій ще може статися після того, як той, хто викликав, вважає роботу успішною? Подумай про воркер, який звітував про останню сторінку і помер до завершення спроби.
- Який тест цього уроку впав би першим, якби хтось пізніше переписав `_save` без його умов `WHERE` і ще й прибрав доменну перевірку?

## Чекліст завершення

- [ ] У файлі доказів є інваріант, власники і твої прогнози, записані до змін.
- [ ] `job_progress.py` існує, і `tests/test_job_progress.py` (33) проходить.
- [ ] Міграція `c9d0e1f2a3b4` додає `job_attempts`; `lock_running_job`, `persistence/job_attempts.py` і `job_reporting.py` існують; purge записує свої спроби.
- [ ] `GET /v1/jobs/{job_id}` вимагає `start_synchronization_runs` і показує лише Job синхронізації; `tests/test_app_authentication.py` (107) проходить.
- [ ] `tests/test_job_progress_integration.py` (22) проходить: перезапуск, рух назад, повтор, гонки, застарілий воркер, результати, API, знахідки рев'ю.
- [ ] Ти зламав код навмисно, побачив, як падають три доменні тести, тоді як тест бази втримався, і повернув усе назад.
- [ ] `docs/architecture.md`, `docs/operations.md` і `docs/threat-model.md` описують модель прогресу.
- [ ] Усі перевірки проходять: формат, lint, типи, unit, integration, документація.
- [ ] Докази, статус завдання, рядок у PROGRESS і коміт зроблено.

Коли напишеш файли, поділись ними або виводом помилок, і я перевірю їх за цим чеклістом.
