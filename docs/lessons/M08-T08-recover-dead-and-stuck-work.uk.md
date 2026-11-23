English version: [M08-T08-recover-dead-and-stuck-work.md](M08-T08-recover-dead-and-stuck-work.md)

# M08-T08 — Відновлення мертвої і завислої роботи

Завдання: [M08-T08 — Recover dead and stuck work](../curriculum/milestones/M08-jobs-and-confluence/M08-T08-recover-dead-and-stuck-work.md)

## Що ти зробиш

Цей урок знаходить Job, які перестали рухатись, і зрушує їх з місця. Воркер може загинути посеред Job, а повідомлення в черзі може загубитися ще до того, як його візьме воркер. Сьогодні в обох випадках Job застрягає назавжди: `running` зі старим heartbeat або `queued`, за яким ніхто не прийде. Після цього уроку кожен прохід relay знаходить такі Job, позначає втрачену спробу зрозумілим кодом, а далі працюють правила повторів з M08-T04. Job, у якого скінчились спроби, чекає на адміністратора: той може повторити його або скасувати через API.

Конкретний приклад. Дві Job синхронізації працюють одночасно. J7 на воркері A, і цей воркер убивають. J8 на воркері B, повільному, але живому. Relay запускається щохвилини.

| Час | Воркер A (J7) | Воркер B (J8) | Що робить прохід relay |
|---|---|---|---|
| 10:00 | бере J7, спроба 1 | бере J8, спроба 1 | відновлювати нічого |
| 10:04 | звітує: оброблено 120 | звітує: оброблено 40 | нічого: обидва щойно подали знак |
| 10:05 | процес убито | | нічого: від J7 чули хвилину тому |
| 10:08 | | звітує: оброблено 80 | нічого |
| 10:09 | | | J7 мовчить уже п'ять хвилин: спроба 1 завершується з `worker_lost`, її рядок зберігає «оброблено 120», повтор заплановано приблизно через 15–30 секунд |
| 10:10 | | | J7 знову в черзі й опублікований; воркер бере його як спробу 2 |
| 11:00 | | досі звітує кожні чотири хвилини | J8 не чіпають: повільний не означає мертвий |

| Що просить завдання | Де це зроблено |
|---|---|
| Знаходити завислі Job (running, воркера немає) | Блоки 1 і 2; крок 3: `recover_stuck_jobs` |
| Знаходити осиротілі Job (queued, повідомлення немає) | Блок 3; крок 3 |
| Знаходити вичерпані Job | Блок 4; крок 2: `JobCondition.EXHAUSTED` |
| Показати рішення оператора | Блок 4; крок 4: `GET /v1/job-decisions` |
| Безпечний повтор або скасування | Блок 4; крок 4: `POST /v1/jobs/{job_id}/retry` і `/cancel` |
| Тренування відновлення відрізняє повільний Job від мертвого і зберігає докази попередньої спроби | Крок 5: перші сім тестів |
| Явна поведінка при повторі й скасуванні | Блоки 2 і 4 |
| Оновити лише ту документацію, якої це стосується | Крок 7: архітектура, експлуатація, безпека, модель загроз |

У цьому уроці є код: кроки 2–5 пишуть код, міграцію і тести. Кроки 1, 6, 7 і 8 коду не потребують, хоча на кроці 7 ти редагуєш чотири документи.

Терміни цього уроку:

- **Завислий Job (stale):** Job у статусі `running`, від якого п'ять хвилин нічого не чули. Його воркер вважається мертвим.
- **Осиротілий Job (orphaned):** Job у статусі `queued`, повідомлення для якого надіслали 30 хвилин тому, і жоден воркер його не взяв. Повідомлення вважається загубленим.
- **Вичерпаний Job (exhausted):** Job, що впав через тимчасову проблему, наприклад недоступну базу, і використав усі свої спроби.
- **Остаточна відмова (failed permanently):** Job, що впав через те, чого повтор не виправить, наприклад відхилені облікові дані.
- **Рішення оператора (operator decision):** що адміністратор обирає для Job, який сам не зрушить: повторити ще раз або скасувати.
- **Прохід відновлення (recovery pass):** перший крок кожного запуску `make worker-relay`. Він знаходить завислі й осиротілі Job і завершує їхню втрачену спробу.

## Перш ніж почати

Цей розділ крок за кроком пояснює кожне рішення, на якому стоїть код. Кожен блок закінчується тим, що змінює цей урок.

### 1. Повільний чи мертвий: слухати за годинником бази

**Ситуація.** J7 і J8 обидва `running`. Ззовні вони однакові: рядок, у якому написано `running`. Ніхто не бачить, як процес помирає. Видно лише, що він замовк.

**Прихований факт.** «Замовк» вимагає годинника. Кожен звіт з M08-T07 несе heartbeat, проставлений власним годинником воркера. Годинники на різних машинах розходяться. Адверсальне рев'ю показало, до чого це призводить:

| Момент (реальний час) | Воркер A, годинник відстає на шість хвилин | Збережений `heartbeat_at` | Relay о 10:11 судить за `heartbeat_at` |
|---|---|---|---|
| 10:10 | звітує з міткою 10:04 | 10:04 | |
| 10:11 | | 10:04 | «мовчить сім хвилин»: живого воркера оголошено мертвим, а його наступний звіт відхилено |

**Що змінюється.** Кожен рядок спроби отримує нову колонку `heard_at`. База проставляє її власним годинником на кожному звіті, тож годинник воркера нічого не вирішує. Відновлення міряє тишу як `now() - heard_at`, і обидва значення беруться з бази.

| Було | Стало |
|---|---|
| тиша = `now` relay мінус `heartbeat_at` воркера | тиша = `now()` бази мінус `heard_at`, проставлений базою |
| воркер із неправильним годинником помилково виглядає мертвим або живим | годинник воркера не має значення |

Відхилений звіт теж рахується як знак від воркера. Рев'ю знайшло воркер, чий годинник стрибнув на три хвилини назад: кожен його звіт відхилявся як «рух назад», `heard_at` не рухався, і живого воркера оголосили мертвим. Тепер, коли звіт відхилено через те, що в ньому написано (значення йде назад або час надто далеко попереду), `report_progress` проставляє `heard_at` в окремій, другій транзакції, а потім кидає відмову.

Job без рядка спроби, бо його обробник ніколи не зберіг такого рядка, судять за часом старту. Цей час іде з годинника воркера, і це відоме обмеження.

`heartbeat_at` лишається як був. Це те, що воркер каже про себе і що показує API. `heard_at` — це те, що побачила база, і ним користується лише відновлення.

Аналогія: рятувальник не питає плавців, котра година. Він дивиться на воду й помічає, хто перестав рухатись.

### 2. Відновлювати під блокуванням, і мертвий лишається мертвим

**Ситуація.** Relay читає: «J7 мовчить шість хвилин». Перш ніж він діє, воркер A надсилає звіт: його лише призупинило довге прибирання сміття.

| Момент | Relay | Воркер A | Рядок `jobs` для J7 |
|---|---|---|---|
| 1 | читає список мовчазних Job: J7 у ньому | | `running` |
| 2 | | звітує; `heard_at` стає «зараз»; коміт | `running` |
| 3 | блокує рядок J7 і знову міряє тишу: одна секунда | | `running` |
| 4 | не чіпає J7 | | `running` |

**Прихований факт.** Перше читання — це лише список кандидатів. Кожного кандидата судять знову після того, як його рядок заблоковано, у власній транзакції. Тишу читають уже після блокування, тож звіт, закомічений тим часом, видно. Блокування — це `FOR UPDATE SKIP LOCKED`: якщо воркер саме зараз тримає рядок, посеред звіту чи claim, relay його пропускає, бо той, хто тримає рядок, живий.

**Відновлений воркер уже не повернеться.** Відновлення робить Job `failed` з `worker_lost`. Якщо воркер A все ж був живий і звітує знову, правило з M08-T07 відхиляє звіт з `AttemptNotCurrent`: Job більше не виконує цю спробу.

**Що зберігається.** Рядок спроби завершують, а не видаляють. Він зберігає «оброблено 120» і останній heartbeat та отримує результат `retry_scheduled` або `failed`. Наступна спроба отримує власний рядок.

| Спроба | processed | outcome |
|---|---|---|
| 1 | 120 | `retry_scheduled` |
| 2 | 0 | (працює) |

**Що змінюється.** `recover_stuck_jobs` з `job_recovery` робить це для щонайбільше 100 завислих і 100 осиротілих Job за прохід, кожну у власній транзакції. Job, який не вдалося відновити, записується в лог за типом помилки й рахується, але не зупиняє інших. Relay запускає відновлення першим, ще до повтору Job, тож повтор відновленого Job підхопить один із наступних проходів.

### 3. Осиротілі повідомлення: рахувати втрачену доставку як спробу

**Ситуація.** Job очищення P1 опубліковано о 09:00. Redis перезапускається і губить повідомлення. Або 60-секундний ліміт часу actor-а вбиває воркер посеред claim, claim відкочується, і P1 лишається `queued`. В обох випадках P1 лишається `queued` і опублікованим, і жодне повідомлення вже не прийде.

**Рішення.** Через 30 хвилин відновлення саме бере P1 і завершує його з `delivery_lost`, тож втрачена доставка рахується як одна спроба.

| Варіант | Повідомлення, яке губиться щоразу | Повідомлення, яке просто повільне |
|---|---|---|
| опублікувати знову, нічого не рахувати | крутиться вічно, і ніхто цього не бачить | приходить пізно; нове виявляється дублікатом, що нешкідливо |
| рахувати спробу (обрано) | після трьох спроб: `exhausted`, видно оператору | коштує одну спробу; пізній оригінал знаходить Job у статусі `failed` і нічого не робить |

Обраний варіант жертвує спробою повільної черги, щоб ніколи не крутитися вічно. Якщо черга відстає більше ніж на 30 хвилин, здорові Job втрачають спроби. Це відоме обмеження.

**Прихований факт.** Пізнє повідомлення нешкідливе, бо кожен обробник бере Job під блокуванням рядка і запускає його, лише якщо той досі `queued` (M08-T03).

### 4. Що може вирішити лише оператор

**Ситуація.** Job закінчується як `failed` без запланованого повтору одним із двох способів:

| Стан | Приклад | Чому зупинився |
|---|---|---|
| `exhausted` | тричі `database_unavailable` | проблема може минути, але спроби скінчились |
| `failed_permanently` | `source_refused` | повтор не допоможе, поки хтось не виправить причину |

Ніщо не зрушить такий Job саме. `GET /v1/job-decisions` показує кожен такий Job, якого не замінив новіший Job для тієї самої роботи, найновіші першими. Для кожного вказано дозволені рішення.

| Рішення | Дозволено для | Що робить | Відмова, коли |
|---|---|---|---|
| retry | Job, що впав і не має запланованого повтору | знову ставить його в чергу; вичерпаний Job отримує рівно одну додаткову спробу; relay його публікує | відкритий інший Job для тієї самої роботи (`job_already_open`); повтор уже заплановано |
| cancel | Job у статусі `queued` або `failed` | новий статус `cancelled`: закритий, ніколи не запуститься; Job, що впав, зберігає код помилки | Job працює (`job_running`), бо його воркер може ще писати |

**Прихований факт про повтор.** Одна робота, наприклад «очистити Document D», може мати щонайбільше один відкритий Job: це унікальний індекс з M08-T03. Повтор старого очищення, що впало, коли новіше очищення D уже в черзі, порушив би це правило. Так само й два оператори, які одночасно повторюють два старі очищення D. Повтор пише всередині savepoint. Якщо індекс відмовляє, відкочується лише savepoint, а викликач отримує `JobIntentAlreadyOpen`, на що API відповідає 409.

**Прихований факт про скасування.** Повідомлення для скасованого Job може ще бути в черзі. Коли воно приходить, claim бачить `cancelled`, а не `queued`, і нічого не робить.

Скасування очищення не зберігає видалений вміст. Наступний прохід синхронізації бачить Document з tombstone, у якого ще є Chunks, і ставить у чергу нове очищення.

**Кожне рішення потрапляє в аудит.** Рішення, новий стан Job і подія аудиту з адміністратором як актором комітяться разом: `job.retried` або `job.cancelled`, з видом Job, попереднім статусом, кількістю невдалих спроб і кодом помилки, без жодного вмісту. Рішення потребують нової операції `recover_jobs`, яку має лише роль `administrator`.

| Було | Стало |
|---|---|
| Job, що впав без повтору, лишався таким назавжди, і ніхто його не бачив | `GET /v1/job-decisions` показує його з дозволеними рішеннями |
| не було способу зупинити Job у черзі чи Job, що падає | скасування з аудитом, заборонене, поки Job працює |

### 5. Знову годинники: ніколи не стартувати раніше, ніж став у чергу

Рев'ю знайшло ще дві проблеми з годинниками:

- **Claim раніше за час постановки в чергу.** Повтор від оператора проставляє `queued_at` годинником API. Воркер, чий годинник відстає на десять секунд, брав Job «до того, як його поставили в чергу», і домен це відхиляв. Повідомлення вже було витрачене, тож пізніше відновлення спалювало спробу, яку оператор щойно дав. Тепер claim стартує з `max(now, queued_at)`, а відновлення завершує спробу не раніше за будь-що, що Job уже записав.
- **Кандидати, яких ніколи не відновиш.** Job, поставлений у чергу годинником, що поспішає відносно relay, обирався першим на кожному проході, а потім отримував вердикт «ще не осиротілий». Коли таких набиралося 100, справжній осиротілий Job так і не потрапляв у прохід. Тепер запит пропускає Job, поставлені в чергу пізніше за `now`, за тим самим правилом, що й у `condition_of`.

### 6. Знахідки рев'ю

Адверсальне рев'ю атакувало цей код тричі на справжньому PostgreSQL. Кожну знахідку нижче виправлено, і для кожної є тест, який падає без виправлення:

- воркер, чий годинник відставав від годинника relay, оголошувався мертвим, хоча звітував (блок 1);
- воркер, чий годинник стрибнув назад, мав усі звіти відхиленими й оголошувався мертвим (блок 1);
- повтор від оператора міг згоріти через годинник воркера, що відстає від годинника API (блок 5);
- Job, поставлені в чергу годинником, що поспішає, заповнювали кожен прохід і не пускали справжніх осиротілих (блок 5);
- помилка бази під час запису відхиленого звіту підміняла типізовану відмову нетипізованою помилкою.

Те саме рев'ю влаштовувало гонки відновлення зі звітами, claim, повторами, скасуваннями і другим relay, і нічого не знайшло: 80 завислих Job, яких три relay відновлювали одночасно, відновили рівно по одному разу.

### 7. Відомі обмеження

- Повідомлення, яке чекає в черзі довше за 30 хвилин, коштує спробу, тож велике відставання черги може вичерпати здорові Job (блок 3).
- Воркер, який мовчить п'ять хвилин між звітами, вважається мертвим, і його робота може виконатися двічі. Обробник з M08-T10 має звітувати щонайменше раз на кілька хвилин.
- Job без рядка спроби судять за часом старту від воркера. Наявні спроби вважаються почутими в момент міграції.
- Воркер, який безперервно надсилає відхилені звіти, лишається живим, а його Job — `running`. Job, що працює, не можна скасувати, тож його процес доведеться зупинити.
- Прохід можуть заповнити кандидати, яких він не здатен відновити, наприклад рядки, заблоковані на довгий час.
- Job, узятий годинником, що сильно поспішає, відновлюється з часом завершення й повтору так само далеко в майбутньому.
- Повтор не перевіряє, чи джерело синхронізації досі ввімкнене; обробник з M08-T10 має вважати вимкнене джерело остаточною відмовою.
- Список рішень показує щонайбільше 100 Job, без посторінкового перегляду.
- `save_job` досі не має захисту статусу (з M08-T07).

## Покрокові інструкції

Перш ніж почати, запусти PostgreSQL. Усі команди виконуй з кореня репозиторію. Для інтеграційних перевірок задай URL тестової бази у своєму терміналі:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

Запускай проти цієї бази лише одну integration-команду за раз.

### Крок 1 — Без коду: запиши інваріант і свої прогнози

Перш ніж щось змінювати, запиши, що має бути правдою наприкінці і що ти очікуєш побачити. Прогнози ти перевіриш на кроках 6 і 8.

Створи `docs/evidence/M08-T08-recover-dead-and-stuck-work.md`:

```markdown
# M08-T08 — Recover dead and stuck work evidence

## Invariant

A running Job the database has not heard from for `stale_after` (five
minutes) has its attempt failed with `worker_lost`; a queued Job whose
message was sent `orphan_after` (30 minutes) ago has the attempt it stood
for failed with `delivery_lost`; a Job heard from within `stale_after` is
never touched, whatever its worker's clock says. Each recovered attempt's
row keeps its progress and gets an outcome. A Job that failed with no retry
planned moves on only by an administrator's audited retry or cancel; a
retry never opens a second Job for the same work, and a running Job is
never cancelled.

## Owners

- Liveness: `job_attempts.heard_at`, stamped by PostgreSQL.
- Recovery: `recover_stuck_jobs`, the first step of every relay pass.
- Retries after recovery: the stored Job (M08-T04).
- Decisions and authorization: `recover_jobs`, the `administrator` role.

## Expected failing check

Before the change,
`uv run pytest tests/test_job_recovery_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.

## Predictions

- What a relay pass does to a Job that reported 40 seconds ago but started an hour ago:
- What happens to the attempt row of a Job recovered as stale:
- What an administrator gets when retrying an old purge while a newer purge of the same Document is queued:
- How many tests fail if `condition_of` judges a running Job by its start time only, and whether the slow Job of the drill is still left alone:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після двокрапки в кожному прогнозі стоїть твоя відповідь.

### Крок 2 — Крок із кодом: домен відновлення

Цей крок навчає Job нового способу завершитись і правил, за якими його судять.

**Простими словами:** тепер Job можна скасувати, а Job, що впав остаточно, людина може ще раз поставити в чергу. Два нові коди помилок називають те, що знайшло відновлення: воркер замовк, повідомлення не дійшло. Новий маленький модуль дивиться на Job і каже, в якому стані той насправді: чекає, працює, завислий, осиротілий, чекає повтору, вичерпаний, остаточно впав, виконаний або скасований. А ще він каже, які рішення тут доречні. Нічого тут не торкається бази чи годинника.

| Частина | Що робить |
|---|---|
| `JobStatus.CANCELLED` | закритий Job, який ніхто не запустить; міг стартувати або ні, і зберігає код помилки |
| `Job.cancel` | лише для `queued` або `failed`; Job, що працює, кидає `JobStillRunning` |
| `Job.reopen` | Job, що впав без запланованого повтору, знову в черзі; вичерпаний отримує ще одну спробу |
| `WORKER_LOST`, `DELIVERY_LOST` | тимчасові коди помилок, тож повтор планується, поки лишаються спроби |
| `fail_with` | завершує Job відомим кодом, без винятку, який треба класифікувати |
| `job_conditions` | `JobCondition`, `RecoveryPolicy` (5 і 30 хвилин), `condition_of`, `allowed_decisions` |

`is_open` теж змінюється: раніше «не succeeded» означало відкритий, і скасований Job став би відкритим. Тепер відкриті лише `queued` і `running`, а також Job, що впав із запланованим повтором.


**Дія 1:** у `src/knowledge_service/jobs.py` додай нову помилку після `JobNotRetryable`:

У `src/knowledge_service/jobs.py` знайди:

```python
    """


class JobAlreadyClaimed(InvalidJob):
    """A Job is no longer queued, so this delivery must not run it again.
```

і заміни на:

```python
    """


class JobStillRunning(IllegalJobTransition):
    """A running Job cannot be cancelled: its worker may still be doing it.

    Wait for it to finish, or for recovery to find it dead.
    """


class JobAlreadyClaimed(InvalidJob):
    """A Job is no longer queued, so this delivery must not run it again.
```

додай статус і його переходи:

У `src/knowledge_service/jobs.py` знайди:

```python
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


_FINISHED = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED})

_ALLOWED: Mapping[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED}),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED}),
    JobStatus.SUCCEEDED: frozenset(),
}
```

і заміни на:

```python
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # An operator closed it; nothing will run it again.
    CANCELLED = "cancelled"


_FINISHED = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})
# Statuses that always come from a claim, so they carry a started time.
_STARTED = frozenset({JobStatus.RUNNING, JobStatus.SUCCEEDED, JobStatus.FAILED})

_ALLOWED: Mapping[JobStatus, frozenset[JobStatus]] = {
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED}),
    JobStatus.RUNNING: frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED}),
    JobStatus.FAILED: frozenset({JobStatus.QUEUED, JobStatus.CANCELLED}),
    JobStatus.SUCCEEDED: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}
```

дозволь в інваріантах скасований Job:

У `src/knowledge_service/jobs.py` знайди:

```python
            _require_utc(self.finished_at, "finished_at")
            if self.started_at is not None and self.finished_at < self.started_at:
                raise InvalidJob("finished_at must not be earlier than started_at")
        if (self.status in _FINISHED) != (self.finished_at is not None):
            raise InvalidJob("only a finished job carries a finished time")
        if (self.status is JobStatus.QUEUED) == (self.started_at is not None):
            raise InvalidJob("only a job that is not queued carries a started time")
        if (self.status is JobStatus.FAILED) != (self.last_error_code is not None):
            raise InvalidJob("only a failed job carries an error code")
        if self.last_error_code is not None and not self.last_error_code.strip():
            raise InvalidJob("an error code must not be empty")
        if self.retry_at is not None:
```

і заміни на:

```python
            _require_utc(self.finished_at, "finished_at")
            if self.started_at is not None and self.finished_at < self.started_at:
                raise InvalidJob("finished_at must not be earlier than started_at")
            if self.finished_at < self.queued_at:
                raise InvalidJob("finished_at must not be earlier than queued_at")
        if (self.status in _FINISHED) != (self.finished_at is not None):
            raise InvalidJob("only a finished job carries a finished time")
        if self.status is JobStatus.QUEUED and self.started_at is not None:
            raise InvalidJob("a queued job carries no started time")
        if self.status in _STARTED and self.started_at is None:
            raise InvalidJob("a claimed job carries a started time")
        if (self.status is JobStatus.FAILED) and self.last_error_code is None:
            raise InvalidJob("a failed job carries an error code")
        if self.last_error_code is not None and self.status not in {
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            raise InvalidJob("only a failed or cancelled job carries an error code")
        if self.last_error_code is not None and not self.last_error_code.strip():
            raise InvalidJob("an error code must not be empty")
        if self.retry_at is not None:
```

виправ `is_open`:

У `src/knowledge_service/jobs.py` знайди:

```python
        """
        if self.status is JobStatus.FAILED:
            return self.retry_at is not None
        return self.status is not JobStatus.SUCCEEDED

    @classmethod
    def request(
```

і заміни на:

```python
        """
        if self.status is JobStatus.FAILED:
            return self.retry_at is not None
        return self.status in {JobStatus.QUEUED, JobStatus.RUNNING}

    @classmethod
    def request(
```

і додай `reopen` та `cancel` після `retry`:

У `src/knowledge_service/jobs.py` знайди:

```python
            raise JobNotRetryable("the job failed permanently")
        if queued_at < self.retry_at:
            raise JobNotRetryable("the job may not be retried before its retry time")
        return replace(
            self,
            status=JobStatus.QUEUED,
```

і заміни на:

```python
            raise JobNotRetryable("the job failed permanently")
        if queued_at < self.retry_at:
            raise JobNotRetryable("the job may not be retried before its retry time")
        return self._queued_again(queued_at=queued_at)

    def reopen(self, *, queued_at: datetime) -> Job:
        """Queue a Job that failed for good once more, by an operator's decision.

        Only a Job with no retry planned qualifies. One that used every
        attempt is given exactly one more; its failed attempts stay counted.
        """
        _check_transition(self.status, JobStatus.QUEUED)
        if self.retry_at is not None:
            raise JobNotRetryable("the job already has a retry planned")
        reopened = replace(self, max_attempts=max(self.max_attempts, self.attempt + 1))
        return reopened._queued_again(queued_at=queued_at)

    def cancel(self, *, finished_at: datetime) -> Job:
        """Close a queued or failed Job, by an operator's decision.

        A failed Job keeps its error code, so the reason it failed stays
        visible; a planned retry is dropped. A running Job is refused with
        `JobStillRunning`.
        """
        if self.status is JobStatus.RUNNING:
            raise JobStillRunning("a running job cannot be cancelled")
        _check_transition(self.status, JobStatus.CANCELLED)
        return replace(
            self, status=JobStatus.CANCELLED, finished_at=finished_at, retry_at=None
        )

    def _queued_again(self, *, queued_at: datetime) -> Job:
        return replace(
            self,
            status=JobStatus.QUEUED,
```

**Дія 2:** у `src/knowledge_service/job_failures.py` додай два коди:

У `src/knowledge_service/job_failures.py` знайди:

```python
    PROGRAMMING_ERROR = "programming_error"
    # A newer run of the same work has started; there is nothing to redo.
    SUPERSEDED = "superseded"

    @property
    def transient(self) -> bool:
```

і заміни на:

```python
    PROGRAMMING_ERROR = "programming_error"
    # A newer run of the same work has started; there is nothing to redo.
    SUPERSEDED = "superseded"
    # Recovery found the attempt's worker silent for too long.
    WORKER_LOST = "worker_lost"
    # Recovery found a message that no worker took up in time.
    DELIVERY_LOST = "delivery_lost"

    @property
    def transient(self) -> bool:
```

зроби їх тимчасовими:

У `src/knowledge_service/job_failures.py` знайди:

```python
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
    }
)
```

і заміни на:

```python
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
        JobFailure.WORKER_LOST,
        JobFailure.DELIVERY_LOST,
    }
)
```

нехай `fail_attempt` передає роботу новому `fail_with`:

У `src/knowledge_service/job_failures.py` знайди:

```python
    A transient failure with attempts left gets a retry time; any other
    failure is final. Only the error's type is logged, never its message.
    """
    failure = classify_job_failure(error)
    failed_attempts = job.attempt + 1
    retry_at = None
    if failure.transient and failed_attempts < job.max_attempts:
```

і заміни на:

```python
    A transient failure with attempts left gets a retry time; any other
    failure is final. Only the error's type is logged, never its message.
    """
    return fail_with(
        job,
        classify_job_failure(error),
        now=now,
        policy=policy,
        cause=type(error).__name__,
    )


def fail_with(
    job: Job,
    failure: JobFailure,
    *,
    now: datetime,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    cause: str = "recovery",
) -> Job:
    """`job` failed with a known `failure`, retried later only if worth it.

    `cause` names what reported it, for the log; it is never an error message.
    """
    failed_attempts = job.attempt + 1
    retry_at = None
    if failure.transient and failed_attempts < job.max_attempts:
```

і пиши в лог, хто повідомив про збій, а не тип винятку:

У `src/knowledge_service/job_failures.py` знайди:

```python
        job.job_id.value,
        failed_attempts,
        failure.value,
        type(error).__name__,
        "retry planned" if retry_at is not None else "final",
    )
    return job.fail(error_code=failure.value, finished_at=now, retry_at=retry_at)
```

і заміни на:

```python
        job.job_id.value,
        failed_attempts,
        failure.value,
        cause,
        "retry planned" if retry_at is not None else "final",
    )
    return job.fail(error_code=failure.value, finished_at=now, retry_at=retry_at)
```

**Дія 3:** у `src/knowledge_service/job_progress.py` скасований Job теж не має спроби, що триває:

У `src/knowledge_service/job_progress.py` знайди:

```python
    `Job.attempt` counts failed attempts, so a failed Job's last attempt is
    that count, and a running or succeeded Job's is the next one.
    """
    if job.status is JobStatus.QUEUED:
        raise InvalidJob("a queued job has no attempt under way")
    if job.status is JobStatus.FAILED:
        return job.attempt
    return job.attempt + 1
```

і заміни на:

```python
    `Job.attempt` counts failed attempts, so a failed Job's last attempt is
    that count, and a running or succeeded Job's is the next one.
    """
    if job.status in {JobStatus.QUEUED, JobStatus.CANCELLED}:
        raise InvalidJob(f"a {job.status} job has no attempt under way")
    if job.status is JobStatus.FAILED:
        return job.attempt
    return job.attempt + 1
```

**Дія 4:** у `tests/test_job_failures.py` два нові коди тимчасові:

У `tests/test_job_failures.py` знайди:

```python
    assert classify_job_failure(error) is failure


def test_only_dependencies_that_may_answer_later_are_transient() -> None:
    assert {failure for failure in JobFailure if failure.transient} == {
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
    }
```

і заміни на:

```python
    assert classify_job_failure(error) is failure


def test_only_failures_that_may_pass_later_are_transient() -> None:
    # A dependency that may answer later, or a worker or message lost on the way.
    assert {failure for failure in JobFailure if failure.transient} == {
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
        JobFailure.WORKER_LOST,
        JobFailure.DELIVERY_LOST,
    }
```

**Дія 5:** створи модуль станів.

Створи `src/knowledge_service/job_conditions.py`:

```python
"""What a Job's state and signs of life say about it, and what may be decided.

A running Job is alive while it is heard from within `stale_after`; one
silent longer is stale, its worker presumed dead. A queued Job whose message
was sent `orphan_after` ago and that no worker took up is orphaned. A Job
that failed with no retry planned is exhausted (its failure may pass, but
its attempts ran out) or failed permanently, and only an operator moves it
on, by a retry or a cancel. Nothing here reads the database or a clock.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from knowledge_service.job_failures import JobFailure
from knowledge_service.jobs import Job, JobStatus


class JobCondition(StrEnum):
    """What a Job's stored state and signs of life say about it."""

    WAITING = "waiting"
    ORPHANED = "orphaned"
    RUNNING = "running"
    STALE = "stale"
    RETRY_PLANNED = "retry_planned"
    EXHAUSTED = "exhausted"
    FAILED_PERMANENTLY = "failed_permanently"
    SUCCEEDED = "succeeded"
    CANCELLED = "cancelled"

    @property
    def needs_decision(self) -> bool:
        """Whether only an operator can move the Job on: retry or cancel."""
        return self in {JobCondition.EXHAUSTED, JobCondition.FAILED_PERMANENTLY}


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    """How long a Job may stay silent, or undelivered, before it is recovered.

    `stale_after` must be longer than the longest pause between two reports
    of a healthy worker, or a slow Job is taken for a dead one.
    """

    stale_after: timedelta = timedelta(minutes=5)
    orphan_after: timedelta = timedelta(minutes=30)

    def __post_init__(self) -> None:
        for name, value in (
            ("stale_after", self.stale_after),
            ("orphan_after", self.orphan_after),
        ):
            if type(value) is not timedelta:
                raise ValueError(f"{name} must be a timedelta")
            if not timedelta(minutes=1) <= value <= timedelta(days=7):
                raise ValueError(f"{name} must be from one minute to seven days")


DEFAULT_RECOVERY_POLICY: Final = RecoveryPolicy()


def condition_of(
    job: Job,
    *,
    now: datetime,
    policy: RecoveryPolicy = DEFAULT_RECOVERY_POLICY,
    published_at: datetime | None = None,
    last_heard_at: datetime | None = None,
) -> JobCondition:
    """Judge a Job at `now` by its status and its signs of life.

    `published_at` is when a queued Job's message was sent, if it was;
    `last_heard_at` is a running Job's latest heartbeat, and its start time
    when it never reported.
    """
    if job.status is JobStatus.QUEUED:
        if (
            published_at is not None
            and job.queued_at <= now
            and published_at <= now - policy.orphan_after
        ):
            return JobCondition.ORPHANED
        return JobCondition.WAITING
    if job.status is JobStatus.RUNNING:
        heard = last_heard_at or job.started_at
        if heard is not None and heard <= now - policy.stale_after:
            return JobCondition.STALE
        return JobCondition.RUNNING
    if job.status is JobStatus.FAILED:
        if job.retry_at is not None:
            return JobCondition.RETRY_PLANNED
        if _transient(job.last_error_code):
            # A failure that may pass, so it stopped only because attempts ran out.
            return JobCondition.EXHAUSTED
        return JobCondition.FAILED_PERMANENTLY
    if job.status is JobStatus.SUCCEEDED:
        return JobCondition.SUCCEEDED
    return JobCondition.CANCELLED


def _transient(error_code: str | None) -> bool:
    try:
        return JobFailure(error_code).transient
    except ValueError:
        return False


class JobDecision(StrEnum):
    """What an operator may decide about a Job."""

    RETRY = "retry"
    CANCEL = "cancel"


def allowed_decisions(job: Job) -> tuple[JobDecision, ...]:
    """The decisions that fit `job` in its current status, retry first.

    A retry fits a Job that failed with no retry planned; a cancel fits a
    queued or failed Job. A running or finished Job takes neither.
    """
    allowed: list[JobDecision] = []
    if job.status is JobStatus.FAILED and job.retry_at is None:
        allowed.append(JobDecision.RETRY)
    if job.status in {JobStatus.QUEUED, JobStatus.FAILED}:
        allowed.append(JobDecision.CANCEL)
    return tuple(allowed)
```

**Дія 6:** unit-тести для кожного стану і рішення.

Створи `tests/test_job_conditions.py`:

```python
"""Telling a slow Job from a dead one, and the decisions an operator may take."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from knowledge_service.identifiers import JobId
from knowledge_service.job_conditions import (
    JobCondition,
    JobDecision,
    RecoveryPolicy,
    allowed_decisions,
    condition_of,
)
from knowledge_service.jobs import (
    IllegalJobTransition,
    InvalidJob,
    Job,
    JobKind,
    JobNotRetryable,
    JobStatus,
    JobStillRunning,
)

QUEUED_AT = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
STARTED_AT = QUEUED_AT + timedelta(minutes=1)
FAILED_AT = STARTED_AT + timedelta(minutes=1)
POLICY = RecoveryPolicy()


def queued() -> Job:
    return Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_DOCUMENT_CHUNKS,
        target_id=uuid4(),
        queued_at=QUEUED_AT,
    )


def running() -> Job:
    return queued().start(started_at=STARTED_AT)


def failed(code: str = "database_unavailable", *, retry: bool = False) -> Job:
    retry_at = FAILED_AT + timedelta(seconds=30) if retry else None
    return running().fail(error_code=code, finished_at=FAILED_AT, retry_at=retry_at)


def exhausted() -> Job:
    job = failed()
    for _ in range(2):
        job = job.reopen(queued_at=FAILED_AT)
        job = job.start(started_at=FAILED_AT).fail(
            error_code="database_unavailable", finished_at=FAILED_AT
        )
    return job


# Slow or dead: a running Job is judged by when it was last heard from.


def test_a_running_job_heard_from_recently_is_running_however_long_it_ran() -> None:
    hours_later = STARTED_AT + timedelta(hours=5)

    condition = condition_of(
        running(),
        now=hours_later,
        last_heard_at=hours_later - timedelta(minutes=4, seconds=59),
    )

    assert condition is JobCondition.RUNNING


def test_a_running_job_silent_for_the_stale_time_is_stale() -> None:
    heard = STARTED_AT + timedelta(minutes=30)

    at_limit = condition_of(
        running(), now=heard + POLICY.stale_after, last_heard_at=heard
    )
    just_before = condition_of(
        running(),
        now=heard + POLICY.stale_after - timedelta(microseconds=1),
        last_heard_at=heard,
    )

    assert (at_limit, just_before) == (JobCondition.STALE, JobCondition.RUNNING)


def test_a_running_job_that_never_reported_is_judged_by_its_start() -> None:
    assert (
        condition_of(running(), now=STARTED_AT + timedelta(minutes=5))
        is JobCondition.STALE
    )


def test_a_clock_behind_the_job_never_calls_it_stale() -> None:
    assert condition_of(running(), now=QUEUED_AT) is JobCondition.RUNNING


# Orphaned: a queued Job whose message was sent long ago.


@pytest.mark.parametrize(
    ("published_ago", "condition"),
    [
        (None, JobCondition.WAITING),
        (timedelta(minutes=29), JobCondition.WAITING),
        (timedelta(minutes=30), JobCondition.ORPHANED),
    ],
)
def test_a_queued_job_is_orphaned_once_its_message_is_old(
    published_ago: timedelta | None, condition: JobCondition
) -> None:
    now = QUEUED_AT + timedelta(hours=1)
    published_at = None if published_ago is None else now - published_ago

    assert condition_of(queued(), now=now, published_at=published_at) is condition


def test_a_job_queued_after_the_clock_is_never_orphaned() -> None:
    earlier = QUEUED_AT - timedelta(hours=1)
    published_at = earlier - timedelta(hours=1)

    condition = condition_of(queued(), now=earlier, published_at=published_at)

    assert condition is JobCondition.WAITING


# Failed: planned retry, exhausted, or permanent.


@pytest.mark.parametrize(
    ("job", "condition"),
    [
        (failed(retry=True), JobCondition.RETRY_PLANNED),
        (failed("database_unavailable"), JobCondition.EXHAUSTED),
        (failed("worker_lost"), JobCondition.EXHAUSTED),
        (failed("source_refused"), JobCondition.FAILED_PERMANENTLY),
        (failed("superseded"), JobCondition.FAILED_PERMANENTLY),
        (failed("an_old_code"), JobCondition.FAILED_PERMANENTLY),
    ],
)
def test_a_failed_job_is_judged_by_its_retry_and_its_code(
    job: Job, condition: JobCondition
) -> None:
    assert condition_of(job, now=FAILED_AT) is condition


def test_only_failures_with_no_retry_need_a_decision() -> None:
    assert {condition for condition in JobCondition if condition.needs_decision} == {
        JobCondition.EXHAUSTED,
        JobCondition.FAILED_PERMANENTLY,
    }


@pytest.mark.parametrize("value", [timedelta(seconds=59), timedelta(days=8), 300, None])
def test_a_recovery_policy_refuses_an_unbounded_time(value: object) -> None:
    with pytest.raises(ValueError, match="stale_after"):
        RecoveryPolicy(stale_after=value)  # type: ignore[arg-type]


# An operator's decisions in the domain.


def test_a_retry_gives_a_job_that_used_every_attempt_one_more() -> None:
    job = exhausted()

    reopened = job.reopen(queued_at=FAILED_AT + timedelta(hours=1))

    assert (reopened.status, reopened.attempt, reopened.max_attempts) == (
        JobStatus.QUEUED,
        3,
        4,
    )
    assert reopened.last_error_code is None
    assert reopened.started_at is None


def test_a_retry_of_a_permanent_failure_keeps_its_attempt_limit() -> None:
    reopened = failed("source_refused").reopen(queued_at=FAILED_AT)

    assert (reopened.attempt, reopened.max_attempts) == (1, 3)


def test_a_retry_is_refused_while_one_is_planned() -> None:
    with pytest.raises(JobNotRetryable):
        failed(retry=True).reopen(queued_at=FAILED_AT)


@pytest.mark.parametrize("job", [queued(), running()])
def test_only_a_failed_job_can_be_retried_by_an_operator(job: Job) -> None:
    with pytest.raises(IllegalJobTransition):
        job.reopen(queued_at=FAILED_AT)


def test_cancelling_a_queued_job_closes_it_without_a_start() -> None:
    cancelled = queued().cancel(finished_at=QUEUED_AT)

    assert (cancelled.status, cancelled.started_at, cancelled.is_open) == (
        JobStatus.CANCELLED,
        None,
        False,
    )


def test_cancelling_a_failed_job_drops_its_retry_and_keeps_its_code() -> None:
    cancelled = failed(retry=True).cancel(finished_at=FAILED_AT)

    assert (cancelled.retry_at, cancelled.last_error_code) == (
        None,
        "database_unavailable",
    )


def test_a_running_job_cannot_be_cancelled() -> None:
    with pytest.raises(JobStillRunning):
        running().cancel(finished_at=FAILED_AT)


@pytest.mark.parametrize(
    "job",
    [
        running().succeed(finished_at=FAILED_AT),
        queued().cancel(finished_at=QUEUED_AT),
    ],
)
def test_a_finished_job_cannot_be_cancelled_or_retried(job: Job) -> None:
    with pytest.raises(IllegalJobTransition):
        job.cancel(finished_at=FAILED_AT)
    with pytest.raises(IllegalJobTransition):
        job.reopen(queued_at=FAILED_AT)


def test_a_cancel_never_ends_before_the_job_was_queued() -> None:
    with pytest.raises(InvalidJob, match="queued_at"):
        queued().cancel(finished_at=QUEUED_AT - timedelta(seconds=1))


@pytest.mark.parametrize(
    ("job", "decisions"),
    [
        (queued(), (JobDecision.CANCEL,)),
        (running(), ()),
        (failed(retry=True), (JobDecision.CANCEL,)),
        (failed(), (JobDecision.RETRY, JobDecision.CANCEL)),
        (running().succeed(finished_at=FAILED_AT), ()),
        (queued().cancel(finished_at=QUEUED_AT), ()),
    ],
)
def test_only_the_decisions_that_fit_are_offered(
    job: Job, decisions: tuple[JobDecision, ...]
) -> None:
    assert allowed_decisions(job) == decisions
```

**Перевірка:** `uv run pytest tests/test_jobs.py tests/test_job_failures.py tests/test_job_progress.py tests/test_job_conditions.py -q` показує `119 passed`.

### Крок 3 — Крок із кодом: знайти й відновити завислі Job

Цей крок зберігає власний час бази «востаннє чули» і запускає відновлення в кожному проході relay.

**Простими словами:** кожен рядок спроби отримує час, який база записує сама щоразу, коли чує воркер, навіть якщо відхиляє те, що він сказав. Прохід відновлення питає базу, які Job у статусі `running` мовчать п'ять хвилин і які Job у статусі `queued` опубліковано пів години тому. Потім бере кожен під блокування, перевіряє ще раз і завершує втрачену спробу. Relay запускає цей прохід раніше за все інше. А claim очищення вчиться не спотикатися об час постановки в чергу, проставлений годинником, що поспішає відносно його власного.

| Частина | Що робить |
|---|---|
| міграція `d0e1f2a3b4c5` | `heard_at` у `job_attempts`; `cancelled` у перевірках `jobs`; два індекси; дві дії аудиту |
| `_save` і `record_heard` | проставляють `heard_at` через `clock_timestamp()`, час бази в момент запису |
| `report_progress` | при відхиленому звіті записує, що воркер подав знак, в окремій транзакції, потім кидає відмову |
| `persistence/job_recovery.py` | запити кандидатів, повторне читання під блокуванням і список рішень |
| `job_recovery.py` | `recover_stuck_jobs`: судити знову під блокуванням, завершити спробу, закрити її рядок |
| `relay_once` | спершу відновлення; збій там записується в лог і в звіт, а інші кроки все одно виконуються |


**Дія 1:** створи міграцію.

Створи `migrations/versions/d0e1f2a3b4c5_job_recovery_decisions.py`:

```python
"""job recovery decisions

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-11-24 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d0e1f2a3b4c5"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b4"

OLD_ACTIONS = (
    "'source.registered', 'document.availability_changed',"
    " 'conversation.deleted', 'job.queued', 'job.failed',"
    " 'evaluation_run.started', 'question.asked', 'document.read',"
    " 'document.access_changed', 'synchronization.finished',"
    " 'message.rated', 'embedding_migration.started',"
    " 'embedding_migration.switched'"
)
NEW_ACTIONS = OLD_ACTIONS + ", 'job.retried', 'job.cancelled'"


def _job_checks(*, cancelled: bool) -> None:
    for name in (
        "ck_jobs_status",
        "ck_jobs_finished_at",
        "ck_jobs_started_at",
        "ck_jobs_last_error_code",
    ):
        op.drop_constraint(name, "jobs", type_="check")
    if cancelled:
        statuses = "'queued', 'running', 'succeeded', 'failed', 'cancelled'"
        finished = "'succeeded', 'failed', 'cancelled'"
        exempt = "status = 'cancelled' or "
    else:
        statuses = "'queued', 'running', 'succeeded', 'failed'"
        finished = "'succeeded', 'failed'"
        exempt = ""
    op.create_check_constraint("ck_jobs_status", "jobs", f"status in ({statuses})")
    op.create_check_constraint(
        "ck_jobs_finished_at",
        "jobs",
        f"(status in ({finished})) = (finished_at is not null)",
    )
    op.create_check_constraint(
        "ck_jobs_started_at",
        "jobs",
        f"{exempt}(status = 'queued') = (started_at is null)",
    )
    op.create_check_constraint(
        "ck_jobs_last_error_code",
        "jobs",
        f"{exempt}(status = 'failed') = (last_error_code is not null)",
    )


def _audit_actions(actions: str) -> None:
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_action", "audit_events", f"action in ({actions})"
    )


def upgrade() -> None:
    # An attempt stored before this revision counts as heard from now.
    op.add_column(
        "job_attempts",
        sa.Column(
            "heard_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    _job_checks(cancelled=True)
    _audit_actions(NEW_ACTIONS)
    op.create_index(
        "ix_jobs_in_flight",
        "jobs",
        ["status"],
        postgresql_where=sa.text("status in ('queued', 'running')"),
    )
    op.create_index(
        "ix_jobs_failed_for_good",
        "jobs",
        ["finished_at", "job_id"],
        postgresql_where=sa.text("status = 'failed' and retry_at is null"),
    )


def downgrade() -> None:
    # The audit log is append-only: an operator's decision is never deleted
    # to make a downgrade fit, so the downgrade stops instead.
    op.execute(
        "do $$ begin"
        " if exists (select 1 from audit_events"
        " where action in ('job.retried', 'job.cancelled')) then"
        " raise exception 'operator decisions are recorded; cannot downgrade';"
        " end if; end $$"
    )
    op.drop_index("ix_jobs_failed_for_good", table_name="jobs")
    op.drop_index("ix_jobs_in_flight", table_name="jobs")
    # Before this revision a Job could not be cancelled; the closest closed
    # state is a final failure, which keeps the Job out of every retry.
    op.execute(
        "update jobs set status = 'failed',"
        " started_at = coalesce(started_at, queued_at),"
        " last_error_code = coalesce(last_error_code, 'cancelled')"
        " where status = 'cancelled'"
    )
    _job_checks(cancelled=False)
    _audit_actions(OLD_ACTIONS)
    op.drop_column("job_attempts", "heard_at")
```

**Дія 2:** у `src/knowledge_service/persistence/schema.py` дозволь новий статус:

У `src/knowledge_service/persistence/schema.py` знайди:

```python
        name="ck_jobs_kind",
    ),
    sa.CheckConstraint(
        "status in ('queued', 'running', 'succeeded', 'failed')",
        name="ck_jobs_status",
    ),
    sa.CheckConstraint(
```

і заміни на:

```python
        name="ck_jobs_kind",
    ),
    sa.CheckConstraint(
        "status in ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
        name="ck_jobs_status",
    ),
    sa.CheckConstraint(
```

дозволь часи й код помилки скасованого Job:

У `src/knowledge_service/persistence/schema.py` знайди:

```python
        name="ck_jobs_attempt",
    ),
    sa.CheckConstraint(
        "(status in ('succeeded', 'failed')) = (finished_at is not null)",
        name="ck_jobs_finished_at",
    ),
    sa.CheckConstraint(
        "(status = 'queued') = (started_at is null)", name="ck_jobs_started_at"
    ),
    sa.CheckConstraint(
        "(status = 'failed') = (last_error_code is not null)",
        name="ck_jobs_last_error_code",
    ),
    sa.CheckConstraint(
```

і заміни на:

```python
        name="ck_jobs_attempt",
    ),
    sa.CheckConstraint(
        "(status in ('succeeded', 'failed', 'cancelled')) = (finished_at is not null)",
        name="ck_jobs_finished_at",
    ),
    # A cancelled Job may or may not have been claimed before.
    sa.CheckConstraint(
        "status = 'cancelled' or (status = 'queued') = (started_at is null)",
        name="ck_jobs_started_at",
    ),
    # A cancelled Job keeps the error code of a failure before it, if any.
    sa.CheckConstraint(
        "status = 'cancelled' or (status = 'failed') = (last_error_code is not null)",
        name="ck_jobs_last_error_code",
    ),
    sa.CheckConstraint(
```

додай два індекси:

У `src/knowledge_service/persistence/schema.py` знайди:

```python
        "job_id",
        postgresql_where=sa.text("retry_at is not null"),
    ),
)

# One row per attempt of a Job: how far it got, and how it ended.
```

і заміни на:

```python
        "job_id",
        postgresql_where=sa.text("retry_at is not null"),
    ),
    # Finds queued and running Jobs that recovery may find stuck.
    sa.Index(
        "ix_jobs_in_flight",
        "status",
        postgresql_where=sa.text("status in ('queued', 'running')"),
    ),
    # Finds Jobs that failed for good, for an operator's decision.
    sa.Index(
        "ix_jobs_failed_for_good",
        "finished_at",
        "job_id",
        postgresql_where=sa.text("status = 'failed' and retry_at is null"),
    ),
)

# One row per attempt of a Job: how far it got, and how it ended.
```

додай `heard_at` до `job_attempts`:

У `src/knowledge_service/persistence/schema.py` знайди:

```python
    sa.Column("warnings", ARRAY(sa.String(length=32)), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("outcome", sa.String(length=32), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("attempt >= 1", name="ck_job_attempts_attempt"),
```

і заміни на:

```python
    sa.Column("warnings", ARRAY(sa.String(length=32)), nullable=False),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
    # When the database last heard from the attempt, by its own clock, so
    # recovery never judges a worker by that worker's clock.
    sa.Column(
        "heard_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    ),
    sa.Column("outcome", sa.String(length=32), nullable=True),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("attempt >= 1", name="ck_job_attempts_attempt"),
```

і дві дії аудиту:

У `src/knowledge_service/persistence/schema.py` знайди:

```python
        " 'evaluation_run.started', 'question.asked', 'document.read',"
        " 'document.access_changed', 'synchronization.finished',"
        " 'message.rated', 'embedding_migration.started',"
        " 'embedding_migration.switched')",
        name="ck_audit_events_action",
    ),
    sa.CheckConstraint(
```

і заміни на:

```python
        " 'evaluation_run.started', 'question.asked', 'document.read',"
        " 'document.access_changed', 'synchronization.finished',"
        " 'message.rated', 'embedding_migration.started',"
        " 'embedding_migration.switched', 'job.retried', 'job.cancelled')",
        name="ck_audit_events_action",
    ),
    sa.CheckConstraint(
```

**Дія 3:** у `tests/test_indexes.py` назви два індекси:

У `tests/test_indexes.py` знайди:

```python
    "uq_embedding_spaces_current",
    "uq_jobs_open_intent",
    "ix_jobs_due_retry",
    "ix_jobs_synchronization_target",
    "uq_synchronization_runs_running_source",
}
```

і заміни на:

```python
    "uq_embedding_spaces_current",
    "uq_jobs_open_intent",
    "ix_jobs_due_retry",
    "ix_jobs_in_flight",
    "ix_jobs_failed_for_good",
    "ix_jobs_synchronization_target",
    "uq_synchronization_runs_running_source",
}
```

**Дія 4:** у `src/knowledge_service/persistence/jobs.py` зроби функцію читання рядка публічною, бо відновлення теж читає рядки `jobs`. Перейменуй `_job_from` на `job_from_record` і дай їй docstring:

У `src/knowledge_service/persistence/jobs.py` знайди:

```python
    }


def _job_from(record: Mapping[str, object]) -> Job:
    try:
        kind = JobKind(cast(str, record["kind"]))
        status = JobStatus(cast(str, record["status"]))
```

і заміни на:

```python
    }


def job_from_record(record: Mapping[str, object]) -> Job:
    """The Job a stored `jobs` row holds."""
    try:
        kind = JobKind(cast(str, record["kind"]))
        status = JobStatus(cast(str, record["status"]))
```

потім використай нову назву в `load_open_job`:

У `src/knowledge_service/persistence/jobs.py` знайди:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def enqueue_job(connection: AsyncConnection, job: Job) -> Enqueued:
```

і заміни на:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return job_from_record(dict(record))


async def enqueue_job(connection: AsyncConnection, job: Job) -> Enqueued:
```

у `load_job`:

У `src/knowledge_service/persistence/jobs.py` знайди:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def lock_running_job(connection: AsyncConnection, *, job_id: JobId) -> Job | None:
```

і заміни на:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return job_from_record(dict(record))


async def lock_running_job(connection: AsyncConnection, *, job_id: JobId) -> Job | None:
```

у `lock_running_job`:

У `src/knowledge_service/persistence/jobs.py` знайди:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def save_job(connection: AsyncConnection, *, job: Job) -> Job:
```

і заміни на:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return job_from_record(dict(record))


async def save_job(connection: AsyncConnection, *, job: Job) -> Job:
```

у `requeue_due_jobs`:

У `src/knowledge_service/persistence/jobs.py` знайди:

```python
    records = (await connection.execute(statement)).mappings().all()
    return tuple(
        [
            await save_job(connection, job=_job_from(dict(record)).retry(queued_at=now))
            for record in records
        ]
    )
```

і заміни на:

```python
    records = (await connection.execute(statement)).mappings().all()
    return tuple(
        [
            await save_job(
                connection, job=job_from_record(dict(record)).retry(queued_at=now)
            )
            for record in records
        ]
    )
```

і в `lock_unpublished_job`:

У `src/knowledge_service/persistence/jobs.py` знайди:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def mark_job_published(
```

і заміни на:

```python
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return job_from_record(dict(record))


async def mark_job_published(
```

**Дія 5:** у `src/knowledge_service/persistence/job_attempts.py` додай `record_heard` перед `finish_attempt`:

У `src/knowledge_service/persistence/job_attempts.py` знайди:

```python
    return advanced


async def finish_attempt(
    connection: AsyncConnection,
    *,
```

і заміни на:

```python
    return advanced


async def record_heard(
    connection: AsyncConnection, *, job_id: JobId, attempt: int
) -> bool:
    """Record that the worker of a running attempt was just heard from.

    Only the attempt the Job is running is touched, after the Job's row is
    share-locked, as a report does; returns whether it was. Nothing else in
    the attempt changes.
    """
    job = await lock_running_job(connection, job_id=job_id)
    if job is None or attempt_number(job) != attempt:
        return False
    result = await connection.execute(
        sa.update(job_attempts)
        .where(
            job_attempts.c.job_id == job_id.value,
            job_attempts.c.attempt == attempt,
            job_attempts.c.outcome.is_(None),
        )
        .values(heard_at=sa.func.clock_timestamp())
    )
    return result.rowcount == 1


async def finish_attempt(
    connection: AsyncConnection,
    *,
```

і проставляй `heard_at` у `_save`:

У `src/knowledge_service/persistence/job_attempts.py` знайди:

```python
    ]
    if not ending:
        conditions.append(columns.heartbeat_at <= progress.heartbeat_at)
    result = await connection.execute(
        sa.update(job_attempts).where(*conditions).values(_row(progress))
    )
    if result.rowcount != 1:
        raise ProgressWentBackwards("the stored attempt is further along")
```

і заміни на:

```python
    ]
    if not ending:
        conditions.append(columns.heartbeat_at <= progress.heartbeat_at)
    # Stamped by the database, whatever the reporter's clock says, at the
    # moment of the write rather than when the transaction began.
    values = {**_row(progress), "heard_at": sa.func.clock_timestamp()}
    result = await connection.execute(
        sa.update(job_attempts).where(*conditions).values(values)
    )
    if result.rowcount != 1:
        raise ProgressWentBackwards("the stored attempt is further along")
```

**Дія 6:** у `src/knowledge_service/job_reporting.py` імпортуй `logging`:

У `src/knowledge_service/job_reporting.py` знайди:

```python
its queue messages are never involved.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
```

і заміни на:

```python
its queue messages are never involved.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
```

імпортуй відмови і `record_heard` та додай logger:

У `src/knowledge_service/job_reporting.py` знайди:

```python
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
```

і заміни на:

```python
    JobProgress,
    JobStage,
    JobWarning,
    ProgressWentBackwards,
    ReportFromTheFuture,
)
from knowledge_service.jobs import Job, JobKind
from knowledge_service.persistence.job_attempts import (
    advance_attempt,
    load_latest_attempt,
    record_heard,
)
from knowledge_service.persistence.jobs import load_job

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobReport:
```

і записуй відхилений звіт як знак від воркера:

У `src/knowledge_service/job_reporting.py` знайди:

```python
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
```

і заміни на:

```python
    the same report twice changes nothing. A counter, a warning, or the
    heartbeat that would go back raises `ProgressWentBackwards`; a report
    for an attempt the Job is not running raises `AttemptNotCurrent`.

    A report refused for what it says, such as one from a clock that
    stepped back, still shows the worker is alive: the attempt is recorded
    as heard from in a transaction of its own, then the refusal is raised.
    """
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")
    try:
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
    except ProgressWentBackwards, ReportFromTheFuture:
        try:
            async with runtime.transaction() as connection:
                await record_heard(connection, job_id=job_id, attempt=attempt)
        except Exception as error:
            # The refusal is what the caller must see, not this second failure.
            logger.warning(
                "job %s: recording a refused report failed: %s",
                job_id.value,
                type(error).__name__,
            )
        raise


@dataclass(frozen=True, slots=True)
```

**Дія 7:** створи запити відновлення.

Створи `src/knowledge_service/persistence/job_recovery.py`:

```python
"""Find Jobs that stopped moving, and Jobs waiting for an operator's decision."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.identifiers import JobId
from knowledge_service.jobs import Job, JobStatus
from knowledge_service.persistence.jobs import job_from_record
from knowledge_service.persistence.schema import job_attempts, jobs


@dataclass(frozen=True, slots=True)
class RecoveryCandidate:
    """A locked Job with the signs recovery judges it by.

    `published_at` is when a message for a queued Job was sent. `silent_for`
    is how long, by the database's clock, a running Job has gone unheard:
    since its current attempt's last report, or since its start when it has
    no attempt row. Each is None when it does not apply.
    """

    job: Job
    published_at: datetime | None
    silent_for: timedelta | None


def _current_attempt() -> sa.ColumnElement[bool]:
    # A running Job's current attempt is the one after its failed ones.
    return sa.and_(
        job_attempts.c.job_id == jobs.c.job_id,
        job_attempts.c.attempt == jobs.c.attempt + 1,
    )


def _silence() -> sa.ColumnElement[timedelta]:
    # The start time stands in only for a Job with no attempt row; it comes
    # from the worker's clock.
    heard = sa.func.coalesce(job_attempts.c.heard_at, jobs.c.started_at)
    return sa.func.now() - heard


async def load_silent_job_ids(
    connection: AsyncConnection, *, stale_after: timedelta, limit: int
) -> tuple[JobId, ...]:
    """Running Jobs unheard for at least `stale_after`, longest silent first.

    Silence is measured by the database's clock alone.
    """
    silence = _silence()
    statement = (
        sa.select(jobs.c.job_id)
        .select_from(jobs.outerjoin(job_attempts, _current_attempt()))
        .where(jobs.c.status == JobStatus.RUNNING.value, silence >= stale_after)
        .order_by(silence.desc(), jobs.c.job_id)
        .limit(limit)
    )
    return tuple(JobId(job_id) for job_id in await connection.scalars(statement))


async def load_undelivered_job_ids(
    connection: AsyncConnection, *, now: datetime, orphan_after: timedelta, limit: int
) -> tuple[JobId, ...]:
    """Queued Jobs whose message was sent at least `orphan_after` before `now`.

    A Job queued after `now`, by a clock ahead of this one, is left out, as
    recovery would not judge it orphaned; it must not take another's place.
    """
    statement = (
        sa.select(jobs.c.job_id)
        .where(
            jobs.c.status == JobStatus.QUEUED.value,
            jobs.c.published_at <= now - orphan_after,
            jobs.c.queued_at <= now,
        )
        .order_by(jobs.c.published_at, jobs.c.job_id)
        .limit(limit)
    )
    return tuple(JobId(job_id) for job_id in await connection.scalars(statement))


async def lock_recovery_candidate(
    connection: AsyncConnection, *, job_id: JobId
) -> RecoveryCandidate | None:
    """Lock a Job for recovery and read its signs, or None if it is busy.

    A row someone else holds, such as a worker's claim or a report in
    progress, is skipped: whoever holds it is alive. The silence is read
    after the lock, so a report that committed meanwhile is seen.
    """
    statement = (
        sa.select(jobs)
        .where(jobs.c.job_id == job_id.value)
        .with_for_update(skip_locked=True)
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    job = job_from_record(dict(record))
    published = cast("datetime | None", record["published_at"])
    silent_for = None
    if job.status is JobStatus.RUNNING:
        heard = (
            sa.select(job_attempts.c.heard_at)
            .where(
                job_attempts.c.job_id == job_id.value,
                job_attempts.c.attempt == job.attempt + 1,
            )
            .scalar_subquery()
        )
        silent_for = await connection.scalar(
            sa.select(sa.func.now() - sa.func.coalesce(heard, job.started_at))
        )
    return RecoveryCandidate(
        job=job,
        published_at=None if published is None else published.astimezone(UTC),
        silent_for=cast("timedelta | None", silent_for),
    )


def _newer_of_same_intent() -> sa.Exists:
    newer = jobs.alias("newer")
    return sa.exists().where(
        newer.c.kind == jobs.c.kind,
        newer.c.target_id == jobs.c.target_id,
        sa.tuple_(newer.c.queued_at, newer.c.job_id)
        > sa.tuple_(jobs.c.queued_at, jobs.c.job_id),
    )


async def load_failed_for_good(
    connection: AsyncConnection, *, limit: int
) -> tuple[Job, ...]:
    """Jobs that failed with no retry planned, that no newer Job replaced.

    Newest failure first. A Job for the same work queued after it, such as
    the next scheduled synchronization, replaces it, so it is not listed.
    """
    statement = (
        sa.select(jobs)
        .where(
            jobs.c.status == JobStatus.FAILED.value,
            jobs.c.retry_at.is_(None),
            ~_newer_of_same_intent(),
        )
        .order_by(jobs.c.finished_at.desc(), jobs.c.job_id.desc())
        .limit(limit)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(job_from_record(dict(record)) for record in records)
```

**Дія 8:** створи прохід відновлення.

Створи `src/knowledge_service/job_recovery.py`:

```python
"""Tell a slow Job from a dead one, and recover the Jobs that stopped moving.

A running Job is alive while its worker keeps reporting: a report at least
every `stale_after` keeps it running however long it takes. Silence is
measured by the database's clock, from when it stored the attempt's last
report, so a worker's own clock never makes it look dead or alive. A Job
silent longer is stale: its worker is presumed dead, and its attempt is
failed with `worker_lost`. A queued Job whose message was sent more than
`orphan_after` ago and that no worker took up is orphaned: the message is
presumed lost, and the attempt it stood for is failed with `delivery_lost`.

Both failures are transient, so the stored Job plans a retry while attempts
are left (M08-T04). A Job that failed with none planned waits for an
operator to retry or cancel it (`job_decisions`). An attempt's progress row
is ended, never deleted, so what the dead attempt did stays visible.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId
from knowledge_service.job_conditions import (
    DEFAULT_RECOVERY_POLICY,
    JobCondition,
    RecoveryPolicy,
    condition_of,
)
from knowledge_service.job_failures import (
    DEFAULT_RETRY_POLICY,
    JobFailure,
    RetryPolicy,
    fail_with,
)
from knowledge_service.persistence.job_attempts import finish_attempt
from knowledge_service.persistence.job_recovery import (
    load_silent_job_ids,
    load_undelivered_job_ids,
    lock_recovery_candidate,
)
from knowledge_service.persistence.jobs import save_job

logger = logging.getLogger(__name__)

MAX_RECOVERY_BATCH: Final = 1_000


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """The Jobs one recovery pass failed, by why, and those it could not."""

    stale: tuple[JobId, ...]
    orphaned: tuple[JobId, ...]
    errors: tuple[JobId, ...]


async def recover_stuck_jobs(
    runtime: DatabaseRuntime,
    *,
    now: datetime,
    policy: RecoveryPolicy = DEFAULT_RECOVERY_POLICY,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    limit: int = 100,
) -> RecoveryReport:
    """Fail the attempts of up to `limit` stale and `limit` orphaned Jobs.

    Each Job is judged again under its own lock, in its own transaction, so
    a report or a claim that arrived meanwhile keeps it alive, and one Job
    that cannot be recovered does not hold up the others.
    """
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")
    if type(limit) is not int or not 1 <= limit <= MAX_RECOVERY_BATCH:
        raise ValueError(f"limit must be an integer from 1 to {MAX_RECOVERY_BATCH}")
    async with runtime.transaction() as connection:
        silent = await load_silent_job_ids(
            connection, stale_after=policy.stale_after, limit=limit
        )
        undelivered = await load_undelivered_job_ids(
            connection, now=now, orphan_after=policy.orphan_after, limit=limit
        )
    recovered: dict[JobCondition, list[JobId]] = {
        JobCondition.STALE: [],
        JobCondition.ORPHANED: [],
    }
    errors: list[JobId] = []
    for job_id in (*silent, *undelivered):
        try:
            condition = await _recover(
                runtime,
                job_id=job_id,
                now=now,
                policy=policy,
                retry_policy=retry_policy,
            )
        except Exception as error:
            logger.warning(
                "job %s could not be recovered: %s", job_id.value, type(error).__name__
            )
            errors.append(job_id)
            continue
        if condition is not None:
            recovered[condition].append(job_id)
    return RecoveryReport(
        stale=tuple(recovered[JobCondition.STALE]),
        orphaned=tuple(recovered[JobCondition.ORPHANED]),
        errors=tuple(errors),
    )


async def _recover(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    now: datetime,
    policy: RecoveryPolicy,
    retry_policy: RetryPolicy,
) -> JobCondition | None:
    async with runtime.transaction() as connection:
        candidate = await lock_recovery_candidate(connection, job_id=job_id)
        if candidate is None:
            return None
        job = candidate.job
        # The silence was measured by the database's clock; placed before
        # `now`, it is judged the same whatever clock the worker had.
        silent_for = candidate.silent_for
        condition = condition_of(
            job,
            now=now,
            policy=policy,
            published_at=candidate.published_at,
            last_heard_at=None if silent_for is None else now - silent_for,
        )
        # Never end an attempt before anything the Job already recorded.
        at = max(now, job.queued_at, job.started_at or now)
        if condition is JobCondition.STALE:
            failed = fail_with(job, JobFailure.WORKER_LOST, now=at, policy=retry_policy)
        elif condition is JobCondition.ORPHANED:
            # The lost message stood for an attempt: count it, so a message
            # that keeps getting lost ends in a decision, not a loop.
            failed = fail_with(
                job.start(started_at=at),
                JobFailure.DELIVERY_LOST,
                now=at,
                policy=retry_policy,
            )
        else:
            return None
        await finish_attempt(connection, job=failed)
        await save_job(connection, job=failed)
    logger.info("job %s recovered: %s", job_id.value, condition)
    return condition
```

**Дія 9:** у `src/knowledge_service/ingestion/deletions.py` ніколи не стартуй і не завершуй раніше за час постановки в чергу, в очищенні:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
    try:
        async with runtime.transaction() as connection:
            job = await _claim(connection, job_id=job_id, now=now)
            claimed_attempt = job.attempt
            try:
                # A savepoint: a failed delete is undone, while the claim
```

і заміни на:

```python
    try:
        async with runtime.transaction() as connection:
            job = await _claim(connection, job_id=job_id, now=now)
            # A Job queued by a clock ahead of this one starts, and ends, no
            # earlier than it was queued.
            now = max(now, job.queued_at)
            claimed_attempt = job.attempt
            try:
                # A savepoint: a failed delete is undone, while the claim
```

у `_record_lost_attempt`:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
            raise JobAlreadyClaimed(
                stored, "another delivery already ran the attempt this one lost"
            )
        failed = fail_attempt(
            stored.start(started_at=now), cause, now=now, policy=retry_policy
        )
```

і заміни на:

```python
            raise JobAlreadyClaimed(
                stored, "another delivery already ran the attempt this one lost"
            )
        now = max(now, stored.queued_at)
        failed = fail_attempt(
            stored.start(started_at=now), cause, now=now, policy=retry_policy
        )
```

і в `_claim`:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
        raise InvalidJob("the job is not a document chunk purge")
    if job.status is not JobStatus.QUEUED:
        raise JobAlreadyClaimed(job)
    return job.start(started_at=now)
```

і заміни на:

```python
        raise InvalidJob("the job is not a document chunk purge")
    if job.status is not JobStatus.QUEUED:
        raise JobAlreadyClaimed(job)
    return job.start(started_at=max(now, job.queued_at))
```

**Дія 10:** у `src/knowledge_service/worker/relay.py` опиши новий перший крок:

У `src/knowledge_service/worker/relay.py` знайди:

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
```

і заміни на:

```python
"""Recover stuck Jobs, queue due retries and synchronizations, then publish.

Run `python -m knowledge_service.worker.relay` after a crash or a Redis
outage, and on a schedule, because it is also what fails the attempts of
stale and orphaned Jobs, brings a failed Job back once its retry time has
come, and queues each enabled Source's next synchronization. It exits 0
when every Job it found was published or skipped, and 1 when the queue
refused a message, or recovery or scheduling failed; the rest wait for the
next pass. A failed recovery or scheduling step does not stop the steps
after it.
"""

import asyncio
```

імпортуй його:

У `src/knowledge_service/worker/relay.py` знайди:

```python
    publish_pending_jobs,
    requeue_due_retries,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import broker_from_settings
from knowledge_service.worker.jobs import (
```

і заміни на:

```python
    publish_pending_jobs,
    requeue_due_retries,
)
from knowledge_service.job_recovery import RecoveryReport, recover_stuck_jobs
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import broker_from_settings
from knowledge_service.worker.jobs import (
```

додай його до звіту:

У `src/knowledge_service/worker/relay.py` знайди:

```python
class RelayPass:
    """What one relay pass did, in the order it did it."""

    requeued: tuple[JobId, ...]
    scheduled: ScheduleReport
    schedule_failed: bool
```

і заміни на:

```python
class RelayPass:
    """What one relay pass did, in the order it did it."""

    recovered: RecoveryReport
    recovery_failed: bool
    requeued: tuple[JobId, ...]
    scheduled: ScheduleReport
    schedule_failed: bool
```

запусти його першим:

У `src/knowledge_service/worker/relay.py` знайди:

```python
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
```

і заміни на:

```python
    now: datetime,
    schedule: SynchronizationSchedule,
) -> RelayPass:
    """Recover stuck Jobs, queue due retries and synchronizations, then publish.

    A failure while recovering or scheduling is logged by type and
    reported; the later steps still run, so other Jobs are not held up by it.
    """
    try:
        recovered = await recover_stuck_jobs(runtime, now=now)
        recovery_failed = bool(recovered.errors)
    except Exception as error:
        logger.warning("recovering stuck jobs failed: %s", type(error).__name__)
        recovered = RecoveryReport(stale=(), orphaned=(), errors=())
        recovery_failed = True
    requeued = await requeue_due_retries(runtime, now=now)
    try:
        scheduled = await schedule_due_synchronizations(
```

поверни його:

У `src/knowledge_service/worker/relay.py` знайди:

```python
        schedule_failed = True
    published = await publish_pending_jobs(runtime, publisher=publisher, now=now)
    return RelayPass(
        requeued=requeued,
        scheduled=scheduled,
        schedule_failed=schedule_failed,
```

і заміни на:

```python
        schedule_failed = True
    published = await publish_pending_jobs(runtime, publisher=publisher, now=now)
    return RelayPass(
        recovered=recovered,
        recovery_failed=recovery_failed,
        requeued=requeued,
        scheduled=scheduled,
        schedule_failed=schedule_failed,
```

і виведи його та повертай ненульовий код виходу при збої:

У `src/knowledge_service/worker/relay.py` знайди:

```python
        broker.close()
    report = done.published
    sys.stdout.write(
        f"requeued {len(done.requeued)}, scheduled {len(done.scheduled.queued)}, "
        f"published {report.published}, skipped {report.skipped}, "
        f"failed {report.failed}, untried {report.untried}\n"
    )
    return 1 if report.failed or done.schedule_failed else 0


if __name__ == "__main__":
```

і заміни на:

```python
        broker.close()
    report = done.published
    sys.stdout.write(
        f"stale {len(done.recovered.stale)}, orphaned {len(done.recovered.orphaned)}, "
        f"requeued {len(done.requeued)}, scheduled {len(done.scheduled.queued)}, "
        f"published {report.published}, skipped {report.skipped}, "
        f"failed {report.failed}, untried {report.untried}\n"
    )
    failed = report.failed or done.schedule_failed or done.recovery_failed
    return 1 if failed else 0


if __name__ == "__main__":
```

**Перевірка:** `uv run pyright src` показує 0 помилок; `uv run pytest tests/test_indexes.py -q` показує `4 passed`; `uv run pytest tests/test_migrations_integration.py -q -m integration` показує `3 passed`; а `uv run pytest tests/test_job_progress_integration.py tests/test_deletions_integration.py tests/test_job_retry_integration.py tests/test_scheduling_integration.py -q -m integration` показує `71 passed`.

### Крок 4 — Крок із кодом: рішення і API

Цей крок дає адміністратору побачити Job, які застрягли остаточно, і повторити чи скасувати один із них.

**Простими словами:** новий дозвіл, який мають лише адміністратори, відкриває три endpoint-и. Один показує Job, що остаточно впали і яких ніщо новіше не замінило, разом із доречними рішеннями. Два інші повторюють або скасовують один Job. Кожне рішення блокує Job, змінює його за правилами з кроку 2 і пише запис аудиту з іменем того, хто вирішив, і все це в одній транзакції. API відповідає лише кодами, ніколи текстом помилки.

| Endpoint | Відповіді |
|---|---|
| `GET /v1/job-decisions` | 200 і до 100 Job |
| `POST /v1/jobs/{job_id}/retry` | 200 з Job у черзі; 404 `job_not_found`; 409 `job_already_open` або `decision_not_allowed` |
| `POST /v1/jobs/{job_id}/cancel` | 200 зі скасованим Job; 404; 409 `job_running` або `decision_not_allowed` |

Кожен endpoint перевіряє токен і операцію `recover_jobs` ще до того, як розбирає ID, так само як видалення Conversation.


**Дія 1:** у `src/knowledge_service/audit.py` додай дві дії:

У `src/knowledge_service/audit.py` знайди:

```python
    MESSAGE_RATED = "message.rated"
    EMBEDDING_MIGRATION_STARTED = "embedding_migration.started"
    EMBEDDING_MIGRATION_SWITCHED = "embedding_migration.switched"


class AuditTargetKind(StrEnum):
```

і заміни на:

```python
    MESSAGE_RATED = "message.rated"
    EMBEDDING_MIGRATION_STARTED = "embedding_migration.started"
    EMBEDDING_MIGRATION_SWITCHED = "embedding_migration.switched"
    JOB_RETRIED = "job.retried"
    JOB_CANCELLED = "job.cancelled"


class AuditTargetKind(StrEnum):
```

**Дія 2:** у `src/knowledge_service/roles.py` додай операцію:

У `src/knowledge_service/roles.py` знайди:

```python
    RUN_PAID_EVALUATION = "run_paid_evaluation"
    MANAGE_POLICY = "manage_policy"
    MANAGE_OPERATIONAL_CONFIGURATION = "manage_operational_configuration"


PERMISSIONS: Mapping[ApplicationRole, frozenset[Operation]] = MappingProxyType(
```

і заміни на:

```python
    RUN_PAID_EVALUATION = "run_paid_evaluation"
    MANAGE_POLICY = "manage_policy"
    MANAGE_OPERATIONAL_CONFIGURATION = "manage_operational_configuration"
    RECOVER_JOBS = "recover_jobs"


PERMISSIONS: Mapping[ApplicationRole, frozenset[Operation]] = MappingProxyType(
```

і дай її адміністраторам:

У `src/knowledge_service/roles.py` знайди:

```python
            {Operation.MANAGE_EVALUATION_DATASETS, Operation.RUN_PAID_EVALUATION}
        ),
        ApplicationRole.ADMINISTRATOR: frozenset(
            {Operation.MANAGE_POLICY, Operation.MANAGE_OPERATIONAL_CONFIGURATION}
        ),
    }
)
```

і заміни на:

```python
            {Operation.MANAGE_EVALUATION_DATASETS, Operation.RUN_PAID_EVALUATION}
        ),
        ApplicationRole.ADMINISTRATOR: frozenset(
            {
                Operation.MANAGE_POLICY,
                Operation.MANAGE_OPERATIONAL_CONFIGURATION,
                Operation.RECOVER_JOBS,
            }
        ),
    }
)
```

**Дія 3:** у `tests/test_roles.py` додай її до матриці:

У `tests/test_roles.py` знайди:

```python
    Operation.RUN_PAID_EVALUATION: {EVALUATOR},
    Operation.MANAGE_POLICY: {ADMINISTRATOR},
    Operation.MANAGE_OPERATIONAL_CONFIGURATION: {ADMINISTRATOR},
}
ROLE_SETS = [
    frozenset(chosen)
```

і заміни на:

```python
    Operation.RUN_PAID_EVALUATION: {EVALUATOR},
    Operation.MANAGE_POLICY: {ADMINISTRATOR},
    Operation.MANAGE_OPERATIONAL_CONFIGURATION: {ADMINISTRATOR},
    Operation.RECOVER_JOBS: {ADMINISTRATOR},
}
ROLE_SETS = [
    frozenset(chosen)
```

**Дія 4:** створи workflow рішень.

Створи `src/knowledge_service/job_decisions.py`:

```python
"""An operator's decisions about Jobs recovery cannot move on by itself.

A Job that failed with no retry planned, because its attempts ran out or its
failure was permanent, stays failed until an operator retries or cancels it.
Each decision locks the Job, changes it through the domain, and records who
decided in the audit log, all in one transaction.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import exc as database_errors
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
)
from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import AuditEventId, JobId, UserId
from knowledge_service.jobs import InvalidJob, Job
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.job_recovery import load_failed_for_good
from knowledge_service.persistence.jobs import load_job, save_job

MAX_DECISION_LIST: Final = 500


class JobNotFound(LookupError):
    """No Job has this ID."""


class JobIntentAlreadyOpen(InvalidJob):
    """Another Job for the same work is open, so this one cannot be queued again."""


async def load_jobs_needing_decision(
    runtime: DatabaseRuntime, *, limit: int = 100
) -> tuple[Job, ...]:
    """Jobs that failed for good and that no newer Job replaced, newest first."""
    if type(limit) is not int or not 1 <= limit <= MAX_DECISION_LIST:
        raise ValueError(f"limit must be an integer from 1 to {MAX_DECISION_LIST}")
    async with runtime.transaction() as connection:
        return await load_failed_for_good(connection, limit=limit)


async def retry_job(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> Job:
    """Queue a Job that failed for good once more; the relay publishes it.

    A Job with a retry planned, or one that did not fail, is refused by the
    domain. While another Job for the same work is open, the retry is
    refused with `JobIntentAlreadyOpen`, so one piece of work never has two
    open Jobs.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        job = await _lock(connection, job_id=job_id)
        reopened = job.reopen(queued_at=now)
        try:
            # A savepoint, so the refused update leaves the transaction usable.
            async with connection.begin_nested():
                await save_job(connection, job=reopened)
        except database_errors.IntegrityError as error:
            if _constraint(error) == "uq_jobs_open_intent":
                raise JobIntentAlreadyOpen(
                    "another job for the same work is open"
                ) from None
            raise
        await record_audit_event(
            connection,
            _decision_event(
                job,
                action=AuditAction.JOB_RETRIED,
                operator=operator,
                at=now,
                request_id=request_id,
            ),
        )
    return reopened


async def cancel_job(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> Job:
    """Close a queued or failed Job so nothing runs it again.

    A running Job is refused with `JobStillRunning`: its worker may still be
    writing. A message already sent for a queued Job finds it cancelled and
    does nothing.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        job = await _lock(connection, job_id=job_id)
        cancelled = job.cancel(finished_at=_not_before(job, now))
        await save_job(connection, job=cancelled)
        await record_audit_event(
            connection,
            _decision_event(
                job,
                action=AuditAction.JOB_CANCELLED,
                operator=operator,
                at=now,
                request_id=request_id,
            ),
        )
    return cancelled


async def _lock(connection: AsyncConnection, *, job_id: JobId) -> Job:
    job = await load_job(connection, job_id=job_id, for_update=True)
    if job is None:
        raise JobNotFound("no job has this ID")
    return job


def _not_before(job: Job, now: datetime) -> datetime:
    # A worker's clock may run ahead of this one; a Job never ends before
    # anything it already recorded.
    recorded = [job.queued_at, job.started_at, job.finished_at]
    return max([now, *(moment for moment in recorded if moment is not None)])


def _constraint(error: database_errors.IntegrityError) -> str | None:
    diagnostics = getattr(error.orig, "diag", None)
    name = getattr(diagnostics, "constraint_name", None)
    return name if type(name) is str else None


def _require_utc(now: datetime) -> None:
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")


def _decision_event(
    job: Job,
    *,
    action: AuditAction,
    operator: UserId,
    at: datetime,
    request_id: UUID | None,
) -> AuditEvent:
    """Who decided what about which Job, and the state it was in; no content."""
    metadata: dict[str, str | int | bool] = {
        "kind": job.kind.value,
        "from_status": job.status.value,
        "failed_attempts": job.attempt,
    }
    if job.last_error_code is not None:
        metadata["error_code"] = job.last_error_code
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=at,
        actor_id=operator,
        action=action,
        target_kind=AuditTargetKind.JOB,
        target_id=job.job_id.value,
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=request_id,
        run_id=None,
        metadata=metadata,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DatabaseJobDecisions:
    """The operator's Job decisions, stored in PostgreSQL, at the clock's time."""

    runtime: DatabaseRuntime
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def needing_decision(self, *, limit: int) -> tuple[Job, ...]:
        return await load_jobs_needing_decision(self.runtime, limit=limit)

    async def retry(self, *, job_id: JobId, operator: UserId, request_id: UUID) -> Job:
        return await retry_job(
            self.runtime,
            job_id=job_id,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )

    async def cancel(self, *, job_id: JobId, operator: UserId, request_id: UUID) -> Job:
        return await cancel_job(
            self.runtime,
            job_id=job_id,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )
```

**Дія 5:** у `src/knowledge_service/contracts.py` додай дві моделі відповіді після `SynchronizationJobReport`:

У `src/knowledge_service/contracts.py` знайди:

```python
    error_code: str | None
    retry_at: datetime | None
    latest_attempt: JobAttemptProgress | None
```

і заміни на:

```python
    error_code: str | None
    retry_at: datetime | None
    latest_attempt: JobAttemptProgress | None


class OperatorJob(BaseModel):
    """A Job as an operator decides about it: what it is, and how it ended."""

    job_id: UUID
    kind: str
    target_id: UUID
    status: str
    condition: str
    failed_attempts: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    queued_at: datetime
    finished_at: datetime | None
    error_code: str | None
    # What an operator may decide now: "retry", "cancel", both, or neither.
    decisions: list[str]


class JobsNeedingDecision(BaseModel):
    """Jobs that failed for good and that no newer Job replaced, newest first."""

    jobs: list[OperatorJob]
```

**Дія 6:** у `src/knowledge_service/app.py` імпортуй годинник:

У `src/knowledge_service/app.py` знайди:

```python
import logging
import re
from collections.abc import Awaitable, Callable, Coroutine
from typing import Protocol
from uuid import UUID, uuid4
```

і заміни на:

```python
import logging
import re
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4
```

моделі відповіді:

У `src/knowledge_service/app.py` знайди:

```python
    Answer,
    ErrorResponse,
    JobAttemptProgress,
    Question,
    SynchronizationJobReport,
)
```

і заміни на:

```python
    Answer,
    ErrorResponse,
    JobAttemptProgress,
    JobsNeedingDecision,
    OperatorJob,
    Question,
    SynchronizationJobReport,
)
```

і домен:

У `src/knowledge_service/app.py` знайди:

```python
    Unauthenticated,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import JobKind
from knowledge_service.roles import Operation, PermissionDenied, require
from knowledge_service.workflow import AnswerWorkflow
```

і заміни на:

```python
    Unauthenticated,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.job_conditions import allowed_decisions, condition_of
from knowledge_service.job_decisions import JobIntentAlreadyOpen, JobNotFound
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import (
    IllegalJobTransition,
    Job,
    JobKind,
    JobNotRetryable,
    JobStillRunning,
)
from knowledge_service.roles import Operation, PermissionDenied, require
from knowledge_service.workflow import AnswerWorkflow
```

додай протокол і подання після `JobReports`:

У `src/knowledge_service/app.py` знайди:

```python
    ) -> JobReport | None: ...


# The Jobs the job endpoint shows: those a Synchronization Run starter owns.
VISIBLE_JOB_KINDS = frozenset({JobKind.SYNCHRONIZE_SOURCE})
```

і заміни на:

```python
    ) -> JobReport | None: ...


class JobDecisions(Protocol):
    """An operator's decisions about Jobs that recovery cannot move on.

    `DatabaseJobDecisions` is the implementation. `retry` and `cancel`
    raise `JobNotFound` for a missing Job, and a domain error for a Job the
    decision does not fit.
    """

    async def needing_decision(self, *, limit: int) -> tuple[Job, ...]: ...

    async def retry(
        self, *, job_id: JobId, operator: UserId, request_id: UUID
    ) -> Job: ...

    async def cancel(
        self, *, job_id: JobId, operator: UserId, request_id: UUID
    ) -> Job: ...


# How many Jobs the decision list shows at most.
DECISION_LIST_LIMIT = 100


def operator_job(job: Job, *, now: datetime) -> OperatorJob:
    """The API view of a Job for an operator, judged at `now`."""
    return OperatorJob(
        job_id=job.job_id.value,
        kind=job.kind.value,
        target_id=job.target_id,
        status=job.status.value,
        condition=condition_of(job, now=now).value,
        failed_attempts=job.attempt,
        max_attempts=job.max_attempts,
        queued_at=job.queued_at,
        finished_at=job.finished_at,
        error_code=job.last_error_code,
        decisions=[decision.value for decision in allowed_decisions(job)],
    )


# The Jobs the job endpoint shows: those a Synchronization Run starter owns.
VISIBLE_JOB_KINDS = frozenset({JobKind.SYNCHRONIZE_SOURCE})
```

прийми сховище в `create_app`:

У `src/knowledge_service/app.py` знайди:

```python
    authenticator: RequestAuthenticator | None = None,
    conversations: ConversationStore | None = None,
    job_reports: JobReports | None = None,
) -> FastAPI:
    """The HTTP application.
```

і заміни на:

```python
    authenticator: RequestAuthenticator | None = None,
    conversations: ConversationStore | None = None,
    job_reports: JobReports | None = None,
    job_decisions: JobDecisions | None = None,
) -> FastAPI:
    """The HTTP application.
```

і додай три endpoint-и в кінці `create_app`, а після неї дві маленькі допоміжні функції:

У `src/knowledge_service/app.py` знайди:

```python
            )
        return synchronization_job_report(report)

    return app


async def read_question(request: Request) -> Question:
    """The body as a Question, read only up to `MAX_BODY_BYTES`."""
    declared = request.headers.get("content-length", "")
```

і заміни на:

```python
            )
        return synchronization_job_report(report)

    @app.get("/v1/job-decisions", response_model=JobsNeedingDecision)
    async def list_jobs_needing_decision(
        request: Request,
    ) -> JobsNeedingDecision | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.RECOVER_JOBS)
        request_id = request_id_from(request)
        if job_decisions is None:
            return _jobs_unavailable(request_id)
        try:
            jobs = await job_decisions.needing_decision(limit=DECISION_LIST_LIMIT)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("job decision list failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        now = datetime.now(UTC)
        return JobsNeedingDecision(jobs=[operator_job(job, now=now) for job in jobs])

    @app.post("/v1/jobs/{job_id}/retry", response_model=OperatorJob)
    async def retry_failed_job(
        request: Request, job_id: str
    ) -> OperatorJob | JSONResponse:
        return await decide(request, job_id, retry=True)

    @app.post("/v1/jobs/{job_id}/cancel", response_model=OperatorJob)
    async def cancel_unfinished_job(
        request: Request, job_id: str
    ) -> OperatorJob | JSONResponse:
        return await decide(request, job_id, retry=False)

    async def decide(
        request: Request, job_id: str, *, retry: bool
    ) -> OperatorJob | JSONResponse:
        # Who, then whether they may, then which Job.
        caller = await authenticate(request)
        require(caller.roles, Operation.RECOVER_JOBS)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(job_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if job_decisions is None:
            return _jobs_unavailable(request_id)
        decision = job_decisions.retry if retry else job_decisions.cancel
        try:
            decided = await decision(
                job_id=JobId.parse(job_id),
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except JobNotFound:
            return error_response(
                status_code=404,
                code="job_not_found",
                message="No such job",
                request_id=request_id,
            )
        except JobIntentAlreadyOpen:
            return _conflict(
                "job_already_open", "Another job for this work is open", request_id
            )
        except JobStillRunning:
            return _conflict("job_running", "The job is still running", request_id)
        except JobNotRetryable, IllegalJobTransition:
            return _conflict(
                "decision_not_allowed",
                "This decision does not fit the job's status",
                request_id,
            )
        except Exception as error:
            logger.error("job decision failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        return operator_job(decided, now=datetime.now(UTC))

    return app


def _jobs_unavailable(request_id: UUID) -> JSONResponse:
    return error_response(
        status_code=503,
        code="jobs_unavailable",
        message="Jobs are unavailable",
        request_id=request_id,
    )


def _conflict(code: str, message: str, request_id: UUID) -> JSONResponse:
    return error_response(
        status_code=409, code=code, message=message, request_id=request_id
    )


async def read_question(request: Request) -> Question:
    """The body as a Question, read only up to `MAX_BODY_BYTES`."""
    declared = request.headers.get("content-length", "")
```

**Дія 7:** у `tests/test_app_authentication.py` імпортуй `JobNotFound`:

У `tests/test_app_authentication.py` знайди:

```python
    user_id_for,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import Job, JobKind
from knowledge_service.roles import ApplicationRole, Operation
```

і заміни на:

```python
    user_id_for,
)
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.job_decisions import JobNotFound
from knowledge_service.job_reporting import JobReport
from knowledge_service.jobs import Job, JobKind
from knowledge_service.roles import ApplicationRole, Operation
```

перелічи три маршрути:

У `tests/test_app_authentication.py` знайди:

```python
        Operation.MANAGE_OWN_CONVERSATIONS
    ),
    ("GET", "/v1/jobs/{job_id}"): Operation.START_SYNCHRONIZATION_RUNS,
}
PROTECTED = [(route, operation) for route, operation in ROUTES.items() if operation]
# The routes that read a body, to check none is read before the role.
```

і заміни на:

```python
        Operation.MANAGE_OWN_CONVERSATIONS
    ),
    ("GET", "/v1/jobs/{job_id}"): Operation.START_SYNCHRONIZATION_RUNS,
    ("GET", "/v1/job-decisions"): Operation.RECOVER_JOBS,
    ("POST", "/v1/jobs/{job_id}/retry"): Operation.RECOVER_JOBS,
    ("POST", "/v1/jobs/{job_id}/cancel"): Operation.RECOVER_JOBS,
}
PROTECTED = [(route, operation) for route, operation in ROUTES.items() if operation]
# The routes that read a body, to check none is read before the role.
```

додай фальшиве сховище рішень після `Reading` і прийми його в `full_app`:

У `tests/test_app_authentication.py` знайди:

```python
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

і заміни на:

```python
        self.calls.append(request)


class Deciding:
    """An operator's Job store that records each decision, and finds no Job."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def needing_decision(self, **request: Any) -> tuple[Job, ...]:
        self.calls.append(request)
        return ()

    async def retry(self, **request: Any) -> Job:
        self.calls.append(request)
        raise JobNotFound("no job has this ID")

    async def cancel(self, **request: Any) -> Job:
        self.calls.append(request)
        raise JobNotFound("no job has this ID")


def full_app(
    source: Source,
    workflow: Any = None,
    tokens: dict[str, Any] | None = None,
    conversations: Deleting | None = None,
    jobs: Reading | None = None,
    decisions: Deciding | None = None,
) -> Any:
    """The app with every endpoint wired, so every route is real."""
    return create_app(
```

передай його в `create_app`:

У `tests/test_app_authentication.py` знайди:

```python
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
        conversations=conversations or Deleting(),
        job_reports=jobs or Reading(),
    )
```

і заміни на:

```python
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
        conversations=conversations or Deleting(),
        job_reports=jobs or Reading(),
        job_decisions=decisions or Deciding(),
    )
```

передай його в тесті ролей:

У `tests/test_app_authentication.py` знайди:

```python
    roles: frozenset[ApplicationRole],
) -> None:
    source, workflow, conversations = Source(), Counting(), Deleting()
    jobs = Reading()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(source, workflow, {"token": token}, conversations, jobs)
    method, path = route

    async with client(app) as http:
```

і заміни на:

```python
    roles: frozenset[ApplicationRole],
) -> None:
    source, workflow, conversations = Source(), Counting(), Deleting()
    jobs, decisions = Reading(), Deciding()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(source, workflow, {"token": token}, conversations, jobs, decisions)
    method, path = route

    async with client(app) as http:
```

і рахуй його виклики:

У `tests/test_app_authentication.py` знайди:

```python

    allowed = bool(roles & MATRIX[operation])
    assert (response.status_code == 403) is not allowed
    reached = source.readers or workflow.calls or conversations.calls or jobs.calls
    assert bool(reached) is allowed
```

і заміни на:

```python

    allowed = bool(roles & MATRIX[operation])
    assert (response.status_code == 403) is not allowed
    reached = (
        source.readers
        or workflow.calls
        or conversations.calls
        or jobs.calls
        or decisions.calls
    )
    assert bool(reached) is allowed
```

**Перевірка:** `uv run pytest tests/test_roles.py tests/test_app_authentication.py -q` показує `227 passed`.

### Крок 5 — Крок із кодом: тренування відновлення на справжньому PostgreSQL

Цей крок перевіряє весь урок на справжній базі.

**Простими словами:** ці тести змушують Job замовкати, губити повідомлення й остаточно падати, потім запускають відновлення та API і перевіряють, що сталося. Вони не чекають п'ять справжніх хвилин. Натомість `go_silent` відсуває `heard_at` спроби назад власним годинником бази, а це саме те, що лишили б по собі п'ять тихих хвилин.

| Тести | Що доводять |
|---|---|
| `test_a_slow_job_that_keeps_reporting_is_left_running`, `test_a_silent_job_is_failed_and_its_attempt_keeps_its_progress`, `test_the_next_attempt_gets_its_own_row_and_the_dead_one_stays` | тренування: повільний не мертвий; докази попередньої спроби лишаються |
| `test_a_worker_presumed_dead_cannot_report_after_recovery`, `test_a_job_that_never_reported_is_judged_by_its_start` | блок 2 і запасний варіант із часом старту |
| `test_a_report_that_lands_before_the_lock_keeps_the_job_alive`, `test_a_job_whose_row_a_worker_holds_is_skipped` | повторна перевірка під блокуванням і `SKIP LOCKED` |
| від `test_a_recently_published_job_is_left_waiting` до `test_a_message_lost_every_time_ends_in_a_decision` | блок 3 |
| від `test_an_operator_retry_gives_an_exhausted_job_one_more_attempt` до `test_the_decision_list_shows_only_failures_nothing_replaced` | блок 4, через HTTP, з аудитом |
| `test_the_relay_recovers_before_it_requeues_and_publishes`, два тести міграції, `test_the_database_refuses_a_cancelled_job_with_a_planned_retry` | relay і схема |
| тести під «Found by the adversarial review» | блоки 1 і 5, по одному на знахідку |

Створи `tests/test_job_recovery_integration.py`:

```python
"""Recovering stuck Jobs, and an operator's decisions, on real PostgreSQL.

The drill: a Job that keeps reporting is left running however long it
takes, while one that went silent is failed with `worker_lost`, and its
attempt keeps what it reported. A message no worker took up counts as a lost
attempt, so one that keeps getting lost ends in an operator's decision, not
a loop. An operator retries or cancels through the API, and each decision is
in the audit log.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import cast

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from authorization_scenarios import Person
from database_fixtures import migration_config, migration_environment
from job_fixtures import (
    OBSERVED_AT,
    SOURCE_ID,
    database_went_away,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service import job_recovery, job_reporting
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.ingestion.deletions import (
    purge_document_chunks,
    requeue_unfinished_purges,
)
from knowledge_service.job_conditions import JobCondition, condition_of
from knowledge_service.job_decisions import (
    DatabaseJobDecisions,
    cancel_job,
    load_jobs_needing_decision,
    retry_job,
)
from knowledge_service.job_failures import RetryPolicy
from knowledge_service.job_progress import (
    AttemptOutcome,
    JobCounters,
    JobProgress,
    JobStage,
    ProgressWentBackwards,
)
from knowledge_service.job_recovery import RecoveryReport, recover_stuck_jobs
from knowledge_service.job_reporting import report_progress
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.persistence.job_attempts import (
    AttemptNotCurrent,
    begin_attempt,
    load_latest_attempt,
)
from knowledge_service.persistence.jobs import (
    enqueue_job,
    mark_job_published,
    requeue_due_jobs,
    save_job,
)
from knowledge_service.persistence.schema import audit_events, job_attempts, jobs
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings
from knowledge_service.worker.jobs import run_stored_job

pytestmark = pytest.mark.integration

STARTED_AT = OBSERVED_AT + timedelta(minutes=1)
POLICY = RetryPolicy(jitter=lambda: 0.5)
# The first retry waits half of 30 s plus half of the other half.
FIRST_DELAY = timedelta(seconds=22.5)

AVERY = Person("avery", ())
CALLERS = {
    "Bearer avery": Caller(
        reader=AVERY.context(), roles=frozenset({ApplicationRole.ADMINISTRATOR})
    ),
}


class Tokens:
    """Gives each test token the caller its claims would give."""

    async def authenticate(self, headers: list[str]) -> Caller:
        if len(headers) == 1 and headers[0] in CALLERS:
            return CALLERS[headers[0]]
        raise Unauthenticated("test", error=AuthenticationError.INVALID_TOKEN)


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


async def running_synchronization(runtime: DatabaseRuntime) -> Job:
    """A synchronization Job whose claim and attempt row committed at 09:01."""
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


async def heartbeat(
    runtime: DatabaseRuntime, job: Job, *, minutes: float, processed: int
) -> None:
    await report_progress(
        runtime,
        job_id=job.job_id,
        attempt=job.attempt + 1,
        stage=JobStage.READING,
        counters=JobCounters(processed=processed),
        now=STARTED_AT + timedelta(minutes=minutes),
    )


async def go_silent(runtime: DatabaseRuntime, job: Job, *, minutes: float) -> None:
    """Make the database have last heard from `job`'s attempt `minutes` ago."""
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(job_attempts)
            .where(
                job_attempts.c.job_id == job.job_id.value,
                job_attempts.c.attempt == job.attempt + 1,
            )
            .values(heard_at=sa.func.now() - timedelta(minutes=minutes))
        )


async def database_now(runtime: DatabaseRuntime) -> datetime:
    async with runtime.transaction() as connection:
        now = await connection.scalar(sa.select(sa.func.now()))
    assert now is not None
    return now.astimezone(UTC)


async def recover(runtime: DatabaseRuntime, *, minutes: float) -> RecoveryReport:
    return await recover_stuck_jobs(
        runtime, now=STARTED_AT + timedelta(minutes=minutes), retry_policy=POLICY
    )


# One row per attempt: its number, how many it processed, and its outcome.
type AttemptRow = tuple[int, int, str | None]
# One row per decision: the action, who took it, and its metadata.
type DecisionRow = tuple[str, uuid.UUID, dict[str, object]]


async def attempts_of(runtime: DatabaseRuntime, job_id: JobId) -> list[AttemptRow]:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(
                job_attempts.c.attempt,
                job_attempts.c.processed,
                job_attempts.c.outcome,
            )
            .where(job_attempts.c.job_id == job_id.value)
            .order_by(job_attempts.c.attempt)
        )
        return [cast(AttemptRow, tuple(row)) for row in rows]


async def latest(runtime: DatabaseRuntime, job_id: JobId) -> JobProgress:
    async with runtime.transaction() as connection:
        progress = await load_latest_attempt(connection, job_id=job_id)
    assert progress is not None
    return progress


async def published_purge(runtime: DatabaseRuntime, *, at: timedelta) -> Job:
    """A queued purge whose message was sent at OBSERVED_AT + `at`."""
    _document, job_id = await tombstoned_with_purge(runtime)
    async with runtime.transaction() as connection:
        await mark_job_published(
            connection, job_id=job_id, published_at=OBSERVED_AT + at
        )
    return await stored_job(runtime, job_id)


async def failed_for_good(
    runtime: DatabaseRuntime, job: Job, *, error_code: str = "database_rejected"
) -> Job:
    """`job`, claimed at 09:01 and failed at 09:02 with no retry planned."""
    failed = job.start(started_at=STARTED_AT).fail(
        error_code=error_code, finished_at=STARTED_AT + timedelta(minutes=1)
    )
    async with runtime.transaction() as connection:
        await save_job(connection, job=failed)
    return failed


async def decisions_of(runtime: DatabaseRuntime, job_id: JobId) -> list[DecisionRow]:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(
                audit_events.c.action,
                audit_events.c.actor_id,
                audit_events.c.metadata,
            ).where(audit_events.c.target_id == job_id.value)
        )
        return [cast(DecisionRow, tuple(row)) for row in rows]


async def over_http(runtime: DatabaseRuntime, method: str, path: str) -> httpx.Response:
    app = create_app(
        authenticator=Tokens(),
        job_decisions=DatabaseJobDecisions(
            runtime, clock=lambda: STARTED_AT + timedelta(hours=2)
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.request(
            method, path, headers={"Authorization": "Bearer avery"}
        )


# The recovery drill: slow is not dead.


async def test_a_slow_job_that_keeps_reporting_is_left_running(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    # An hour of work, a report every four minutes.
    for minute in range(4, 61, 4):
        await heartbeat(runtime, job, minutes=minute, processed=minute * 10)
    # The last report reached the database four minutes ago.
    await go_silent(runtime, job, minutes=4)

    report = await recover(runtime, minutes=63)

    assert report == RecoveryReport(stale=(), orphaned=(), errors=())
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.RUNNING


async def test_a_silent_job_is_failed_and_its_attempt_keeps_its_progress(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=4, processed=120)
    # Nothing reached the database for six minutes since.
    await go_silent(runtime, job, minutes=6)

    report = await recover(runtime, minutes=10)

    assert report.stale == (job.job_id,)
    stored = await stored_job(runtime, job.job_id)
    recovered_at = STARTED_AT + timedelta(minutes=10)
    assert (stored.status, stored.last_error_code) == (JobStatus.FAILED, "worker_lost")
    assert stored.retry_at == recovered_at + FIRST_DELAY
    attempt = await latest(runtime, job.job_id)
    # What the dead attempt did stays: 120 processed, last heard at 09:05.
    assert attempt.counters.processed == 120
    assert attempt.heartbeat_at == STARTED_AT + timedelta(minutes=4)
    assert (attempt.outcome, attempt.finished_at) == (
        AttemptOutcome.RETRY_SCHEDULED,
        recovered_at,
    )


async def test_the_next_attempt_gets_its_own_row_and_the_dead_one_stays(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=4, processed=120)
    await go_silent(runtime, job, minutes=6)
    await recover(runtime, minutes=10)
    retry_at = STARTED_AT + timedelta(minutes=10) + FIRST_DELAY
    async with runtime.transaction() as connection:
        [queued] = await requeue_due_jobs(connection, now=retry_at, limit=10)
        second = queued.start(started_at=retry_at)
        await save_job(connection, job=second)
        await begin_attempt(connection, job=second)

    assert await attempts_of(runtime, job.job_id) == [
        (1, 120, "retry_scheduled"),
        (2, 0, None),
    ]


async def test_a_worker_presumed_dead_cannot_report_after_recovery(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=1, processed=10)
    await go_silent(runtime, job, minutes=9)
    await recover(runtime, minutes=10)

    # The worker was only paused; it wakes up and reports.
    with pytest.raises(AttemptNotCurrent):
        await heartbeat(runtime, job, minutes=11, processed=500)
    assert (await latest(runtime, job.job_id)).counters.processed == 10


async def test_a_job_that_never_reported_is_judged_by_its_start(
    runtime: DatabaseRuntime,
) -> None:
    now = await database_now(runtime)
    started: dict[int, Job] = {}
    async with runtime.transaction() as connection:
        for minutes in (4, 5):
            queued = await enqueue_job(
                connection,
                Job.request(
                    job_id=JobId.new(),
                    kind=JobKind.PURGE_CONVERSATION,
                    target_id=uuid.uuid4(),
                    queued_at=now - timedelta(minutes=10),
                ),
            )
            # Claimed, but its handler stored no attempt row.
            started[minutes] = queued.job.start(
                started_at=now - timedelta(minutes=minutes)
            )
            await save_job(connection, job=started[minutes])

    report = await recover_stuck_jobs(runtime, now=now, retry_policy=POLICY)

    assert report.stale == (started[5].job_id,)


async def test_a_report_that_lands_before_the_lock_keeps_the_job_alive(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await running_synchronization(runtime)
    await go_silent(runtime, job, minutes=6)
    real = job_recovery.load_silent_job_ids

    async def then_a_report(*args: object, **kwargs: object) -> tuple[JobId, ...]:
        found = await real(*args, **kwargs)  # type: ignore[arg-type]
        # Read as silent, then the worker reports before recovery locks it.
        await heartbeat(runtime, job, minutes=9, processed=30)
        return found

    monkeypatch.setattr(job_recovery, "load_silent_job_ids", then_a_report)

    report = await recover(runtime, minutes=10)

    assert report.stale == ()
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.RUNNING


async def test_a_job_whose_row_a_worker_holds_is_skipped(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await go_silent(runtime, job, minutes=6)
    async with runtime.transaction() as connection:
        # A report in progress holds a share lock on the Job's row.
        await connection.execute(
            sa.select(jobs.c.job_id)
            .where(jobs.c.job_id == job.job_id.value)
            .with_for_update(read=True)
        )
        report = await recover(runtime, minutes=10)

    assert report.stale == ()


# Orphaned: a message no worker took up.


async def test_a_recently_published_job_is_left_waiting(
    runtime: DatabaseRuntime,
) -> None:
    await published_purge(runtime, at=timedelta(0))

    # Published at 09:00; 29 minutes later it may still be in the queue.
    report = await recover(runtime, minutes=28)

    assert report == RecoveryReport(stale=(), orphaned=(), errors=())


async def test_an_undelivered_message_counts_as_a_lost_attempt(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))

    report = await recover(runtime, minutes=29)

    assert report.orphaned == (purge.job_id,)
    stored = await stored_job(runtime, purge.job_id)
    assert (stored.status, stored.attempt, stored.last_error_code) == (
        JobStatus.FAILED,
        1,
        "delivery_lost",
    )
    assert await attempts_of(runtime, purge.job_id) == [(1, 0, "retry_scheduled")]


async def test_the_lost_message_arriving_late_does_nothing(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await recover(runtime, minutes=29)

    late = await run_stored_job(
        runtime, purge.job_id, now=STARTED_AT + timedelta(minutes=30)
    )

    assert late.duplicate
    assert (await stored_job(runtime, purge.job_id)).last_error_code == "delivery_lost"


async def test_a_message_lost_every_time_ends_in_a_decision(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    minutes = 29.0
    for _ in range(3):
        await recover(runtime, minutes=minutes)
        stored = await stored_job(runtime, purge.job_id)
        if stored.retry_at is None:
            break
        async with runtime.transaction() as connection:
            await requeue_due_jobs(connection, now=stored.retry_at, limit=10)
            await mark_job_published(
                connection, job_id=purge.job_id, published_at=stored.retry_at
            )
        minutes += 31

    stored = await stored_job(runtime, purge.job_id)
    assert (stored.attempt, stored.retry_at) == (3, None)
    assert condition_of(stored, now=stored.queued_at) is JobCondition.EXHAUSTED
    assert [job.job_id for job in await load_jobs_needing_decision(runtime)] == [
        purge.job_id
    ]
    assert [row[2] for row in await attempts_of(runtime, purge.job_id)] == [
        "retry_scheduled",
        "retry_scheduled",
        "failed",
    ]


# An operator's decisions.


async def test_an_operator_retry_gives_an_exhausted_job_one_more_attempt(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    exhausted = await failed_for_good(runtime, purge, error_code="database_unavailable")
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(jobs).where(jobs.c.job_id == purge.job_id.value).values(attempt=3)
        )

    response = await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/retry")

    assert response.status_code == 200
    body = response.json()
    assert (body["status"], body["failed_attempts"], body["max_attempts"]) == (
        "queued",
        3,
        4,
    )
    assert body["decisions"] == ["cancel"]
    async with runtime.transaction() as connection:
        published_at = await connection.scalar(
            sa.select(jobs.c.published_at).where(jobs.c.job_id == purge.job_id.value)
        )
    # Unpublished, so the next relay pass sends a new message.
    assert published_at is None
    [(action, actor, metadata)] = await decisions_of(runtime, exhausted.job_id)
    assert (action, actor) == (
        "job.retried",
        CALLERS["Bearer avery"].reader.user_id.value,
    )
    assert metadata == {
        "kind": "purge_document_chunks",
        "from_status": "failed",
        "failed_attempts": 3,
        "error_code": "database_unavailable",
    }


async def test_a_retry_is_refused_while_newer_work_is_open(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await failed_for_good(runtime, purge, error_code="database_unavailable")
    # The next synchronization pass asks for a new purge of the same Document.
    [newer] = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=STARTED_AT + timedelta(hours=1)
    )

    response = await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/retry")

    assert response.status_code == 409
    assert response.json()["code"] == "job_already_open"
    assert (await stored_job(runtime, purge.job_id)).status is JobStatus.FAILED
    assert (await stored_job(runtime, newer)).status is JobStatus.QUEUED
    assert await decisions_of(runtime, purge.job_id) == []


async def test_two_retries_of_the_same_work_at_once_leave_one_open(
    runtime: DatabaseRuntime,
) -> None:
    first = await published_purge(runtime, at=timedelta(0))
    await failed_for_good(runtime, first)
    async with runtime.transaction() as connection:
        second = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=first.target_id,
                queued_at=STARTED_AT,
            ),
        )
    await failed_for_good(runtime, second.job)
    operator = CALLERS["Bearer avery"].reader.user_id
    now = STARTED_AT + timedelta(hours=1)

    results = await asyncio.gather(
        *(
            retry_job(runtime, job_id=job.job_id, operator=operator, now=now)
            for job in (first, second.job)
        ),
        return_exceptions=True,
    )

    assert sorted(type(result).__name__ for result in results) == [
        "Job",
        "JobIntentAlreadyOpen",
    ]


async def test_cancel_closes_a_queued_job_and_its_message_does_nothing(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))

    response = await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/cancel")

    assert response.status_code == 200
    assert (response.json()["status"], response.json()["decisions"]) == (
        "cancelled",
        [],
    )
    late = await run_stored_job(
        runtime, purge.job_id, now=STARTED_AT + timedelta(hours=3)
    )
    assert late.duplicate
    assert late.job.status is JobStatus.CANCELLED
    [(action, _actor, metadata)] = await decisions_of(runtime, purge.job_id)
    assert (action, metadata["from_status"]) == ("job.cancelled", "queued")


async def test_a_cancelled_purge_is_asked_for_again_while_its_chunks_remain(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/cancel")

    # Cancelling does not keep a deleted Document's Chunks: a pass queues
    # a new purge for them.
    requeued = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=STARTED_AT + timedelta(hours=3)
    )

    assert len(requeued) == 1
    assert requeued[0] != purge.job_id


async def test_cancelling_a_failed_job_keeps_why_it_failed(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await failed_for_good(runtime, purge, error_code="source_refused")

    response = await over_http(runtime, "POST", f"/v1/jobs/{purge.job_id.value}/cancel")

    assert response.status_code == 200
    stored = await stored_job(runtime, purge.job_id)
    assert (stored.status, stored.last_error_code) == (
        JobStatus.CANCELLED,
        "source_refused",
    )


async def test_a_running_job_cannot_be_cancelled(runtime: DatabaseRuntime) -> None:
    job = await running_synchronization(runtime)

    response = await over_http(runtime, "POST", f"/v1/jobs/{job.job_id.value}/cancel")

    assert response.status_code == 409
    assert response.json()["code"] == "job_running"
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.RUNNING


async def test_a_decision_that_does_not_fit_is_refused(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)

    retried = await over_http(runtime, "POST", f"/v1/jobs/{job.job_id.value}/retry")
    missing = await over_http(runtime, "POST", f"/v1/jobs/{uuid.uuid4()}/retry")

    assert (retried.status_code, retried.json()["code"]) == (
        409,
        "decision_not_allowed",
    )
    assert (missing.status_code, missing.json()["code"]) == (404, "job_not_found")


async def test_the_decision_list_shows_only_failures_nothing_replaced(
    runtime: DatabaseRuntime,
) -> None:
    replaced = await published_purge(runtime, at=timedelta(0))
    await failed_for_good(runtime, replaced, error_code="database_unavailable")
    [newer] = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=STARTED_AT + timedelta(hours=1)
    )
    async with runtime.transaction() as connection:
        newer_job = await stored_job(runtime, newer)
        newest = newer_job.start(started_at=STARTED_AT + timedelta(hours=1)).fail(
            error_code="database_unavailable",
            finished_at=STARTED_AT + timedelta(hours=1, minutes=1),
        )
        await save_job(connection, job=newest)

    response = await over_http(runtime, "GET", "/v1/job-decisions")

    assert response.status_code == 200
    [listed] = response.json()["jobs"]
    assert listed["job_id"] == str(newer.value)
    assert (listed["condition"], listed["decisions"]) == (
        "exhausted",
        ["retry", "cancel"],
    )


# The relay and the migration.


async def test_the_relay_recovers_before_it_requeues_and_publishes(
    runtime: DatabaseRuntime,
) -> None:
    from knowledge_service.ingestion.scheduling import SynchronizationSchedule
    from knowledge_service.worker.relay import relay_once

    job = await running_synchronization(runtime)
    await go_silent(runtime, job, minutes=6)
    sent: list[JobId] = []

    class Recording:
        kinds = frozenset({JobKind.PURGE_DOCUMENT_CHUNKS})

        def publish(self, job: Job) -> None:
            sent.append(job.job_id)

    done = await relay_once(
        runtime,
        publisher=Recording(),
        now=STARTED_AT + timedelta(minutes=10),
        schedule=SynchronizationSchedule(interval=timedelta(hours=1)),
    )

    assert done.recovered.stale == (job.job_id,)
    assert not done.recovery_failed


def test_the_migration_turns_cancelled_jobs_into_final_failures_on_the_way_down(
    migrated_database: str,
) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    job_id = uuid.uuid4()
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "insert into jobs (job_id, kind, target_id, status, attempt,"
                    " max_attempts, queued_at, finished_at) values (:job_id,"
                    " 'purge_document_chunks', :target_id, 'cancelled', 0, 3,"
                    " :at, :at)"
                ),
                {"job_id": job_id, "target_id": uuid.uuid4(), "at": OBSERVED_AT},
            )
        with migration_environment(url):
            command.downgrade(config, "c9d0e1f2a3b4")
        with engine.connect() as connection:
            row = connection.execute(
                sa.text(
                    "select status, last_error_code, started_at from jobs"
                    " where job_id = :job_id"
                ),
                {"job_id": job_id},
            ).one()
        assert tuple(row) == ("failed", "cancelled", OBSERVED_AT)
    finally:
        with migration_environment(url):
            command.upgrade(config, "head")
        engine.dispose()


async def test_the_migration_will_not_go_down_past_recorded_decisions(
    migrated_database: str, runtime: DatabaseRuntime
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))
    await cancel_job(
        runtime,
        job_id=purge.job_id,
        operator=CALLERS["Bearer avery"].reader.user_id,
        now=STARTED_AT,
    )
    url = make_url(migrated_database)

    def downgrade() -> None:
        with migration_environment(url):
            command.downgrade(migration_config(), "c9d0e1f2a3b4")

    with pytest.raises(DBAPIError, match="operator decisions are recorded"):
        await asyncio.to_thread(downgrade)
    # Nothing changed: the Job is still cancelled.
    assert (await stored_job(runtime, purge.job_id)).status is JobStatus.CANCELLED


async def test_the_database_refuses_a_cancelled_job_with_a_planned_retry(
    runtime: DatabaseRuntime,
) -> None:
    purge = await published_purge(runtime, at=timedelta(0))

    with pytest.raises(DBAPIError, match="ck_jobs_retry_at"):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.update(jobs)
                .where(jobs.c.job_id == purge.job_id.value)
                .values(
                    status="cancelled",
                    finished_at=STARTED_AT,
                    retry_at=STARTED_AT,
                )
            )


# Found by the adversarial review.


async def test_a_worker_whose_clock_runs_behind_is_not_taken_for_dead(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    # The worker reports just now, but its clock runs six minutes behind
    # the relay's: by the worker's stamps it looks silent.
    await heartbeat(runtime, job, minutes=4, processed=40)

    report = await recover(runtime, minutes=10)

    assert report.stale == ()
    assert (await stored_job(runtime, job.job_id)).status is JobStatus.RUNNING


async def test_a_purge_queued_by_a_clock_ahead_of_the_worker_still_runs(
    runtime: DatabaseRuntime,
) -> None:
    _document, job_id = await tombstoned_with_purge(runtime)

    # Queued at 09:00 by a clock ten seconds ahead of this worker's.
    ran = await purge_document_chunks(
        runtime, job_id=job_id, now=OBSERVED_AT - timedelta(seconds=10)
    )

    assert ran.status is JobStatus.SUCCEEDED
    assert ran.started_at == OBSERVED_AT


async def test_a_job_queued_by_a_clock_ahead_does_not_crowd_out_a_real_orphan(
    runtime: DatabaseRuntime,
) -> None:
    orphan = await published_purge(runtime, at=timedelta(0))
    async with runtime.transaction() as connection:
        ahead = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.SYNCHRONIZE_SOURCE,
                target_id=SOURCE_ID.value,
                queued_at=OBSERVED_AT + timedelta(days=1),
            ),
        )
        # Published earlier than the orphan, so it would be tried first.
        await mark_job_published(
            connection,
            job_id=ahead.job.job_id,
            published_at=OBSERVED_AT - timedelta(hours=1),
        )

    report = await recover_stuck_jobs(
        runtime, now=STARTED_AT + timedelta(minutes=29), retry_policy=POLICY, limit=1
    )

    assert report.orphaned == (orphan.job_id,)


async def test_a_refused_report_still_shows_the_worker_is_alive(
    runtime: DatabaseRuntime,
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=4, processed=40)
    await go_silent(runtime, job, minutes=6)
    # The worker's clock stepped back three minutes: its report is refused,
    # yet it was sent just now.
    with pytest.raises(ProgressWentBackwards):
        await heartbeat(runtime, job, minutes=1, processed=50)

    report = await recover(runtime, minutes=10)

    assert report.stale == ()
    assert (await latest(runtime, job.job_id)).counters.processed == 40


async def test_a_refusal_stays_typed_when_recording_the_worker_fails(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = await running_synchronization(runtime)
    await heartbeat(runtime, job, minutes=4, processed=40)

    async def unreachable(*args: object, **kwargs: object) -> bool:
        raise database_went_away()

    monkeypatch.setattr(job_reporting, "record_heard", unreachable)

    with pytest.raises(ProgressWentBackwards):
        await heartbeat(runtime, job, minutes=1, processed=50)
```

**Перевірка:** `uv run pytest tests/test_job_recovery_integration.py -q -m integration` показує `29 passed`.

### Крок 6 — Без коду: зламай навмисно

Цей крок змушує відновлення судити Job, що працює, лише за часом старту, ніби воно ніколи не слухало звітів.

**Дія 1:** у `condition_of` у `src/knowledge_service/job_conditions.py` зміни цей рядок:

```python
        heard = last_heard_at or job.started_at
```

на:

```python
        heard = job.started_at
```

**Дія 2:** запусти:

```bash
uv run pytest tests/test_job_conditions.py tests/test_job_recovery_integration.py -q -m "integration or not integration"
```

Ти побачиш `3 failed, 62 passed`. Падають два unit-тести, які судять Job, що працює, за останнім звітом, а також `test_a_report_that_lands_before_the_lock_keeps_the_job_alive`: Job, якого прочитали як мовчазного, але який звітував до того, як відновлення його заблокувало, тепер завершено, хоча він живий.

Повільний Job із тренування досі проходить, як і тести з годинниками. Запит, що обирає кандидатів, міряє тишу через `heard_at` у SQL, тож ніколи не пропонує Job, який нещодавно звітував. `condition_of` — це другий шар: він важить тоді, коли перше читання вже застаріло, як у гонці. Це «судити знову під блокуванням» з блоку 2, побачене зсередини.

**Дія 3:** скасуй зміну і запусти команду знову: `65 passed`.

**Перевірка:** порівняй кількість зі своїм прогнозом із кроку 1 і запиши її у файл доказів.

### Крок 7 — Без коду: онови чотири документи

Цей крок записує відновлення і рішення туди, де їх шукають читачі.


**Дія 1:** в архітектурі опиши три endpoint-и після endpoint-а Job:

У `docs/architecture.md` знайди:

```markdown
through `DatabaseJobReports` (`job_reporting`), read from one snapshot. A
missing Job and a Job of any other kind give the same 404, so a purge's
target, such as a Conversation, is never shown.

`job_dispatch` puts a Job on the queue without ever losing it. `submit_job`
commits the Job to PostgreSQL first, then sends one message naming only its
```

і заміни на:

```markdown
through `DatabaseJobReports` (`job_reporting`), read from one snapshot. A
missing Job and a Job of any other kind give the same 404, so a purge's
target, such as a Conversation, is never shown.
`GET /v1/job-decisions`, `POST /v1/jobs/{job_id}/retry`, and
`POST /v1/jobs/{job_id}/cancel` require `recover_jobs` (the administrator
role) before the ID is parsed. They list the Jobs that failed for good and
that no newer Job replaced, and retry or cancel one through
`DatabaseJobDecisions` (`job_decisions`), which records each decision in the
audit log with the operator as its actor.

`job_dispatch` puts a Job on the queue without ever losing it. `submit_job`
commits the Job to PostgreSQL first, then sends one message naming only its
```

додай абзац про відновлення після абзацу про прогрес:

У `docs/architecture.md` знайди:

```markdown
write; counters and warnings never go down and the heartbeat never goes
back, both in the domain and in the `UPDATE`'s own conditions; a report
stamped more than five minutes ahead of the database's clock is refused.
Ending never fails on what a report stored: higher counters stay, and a
heartbeat later than the Job's finish time is pulled back to it. A worker
presumed dead that ends an attempt its Job has moved past gets
```

і заміни на:

```markdown
write; counters and warnings never go down and the heartbeat never goes
back, both in the domain and in the `UPDATE`'s own conditions; a report
stamped more than five minutes ahead of the database's clock is refused.

`job_conditions` tells a slow Job from a dead one, and `job_recovery`, run
first in every relay pass, acts on it. A running Job is alive
while the database heard from it within `stale_after` (five minutes): each
report stamps the attempt's `heard_at` with the database's own clock, even
one refused for going back, so no worker's clock decides it, and a Job with
no attempt row falls back to its start time. One silent longer is stale, and its attempt is failed with
`worker_lost`. A queued Job whose message was sent
`orphan_after` (30 minutes) ago and that no worker took up is orphaned, and
the attempt the lost message stood for is failed with `delivery_lost`. Both
codes are transient, so the stored Job plans a retry while attempts are
left, and a message that keeps getting lost ends as `exhausted`, not in a
loop. Each Job is judged again under its row lock, skipping a row a worker
holds, and its attempt row is ended, never deleted. A Job that failed with
no retry planned waits for an operator: a retry gives an exhausted Job one
more attempt and is refused while another Job for the same work is open; a
cancel closes a queued or failed Job as `cancelled`, and is refused for a
running one, whose worker may still write.
Ending never fails on what a report stored: higher counters stay, and a
heartbeat later than the Job's finish time is pulled back to it. A worker
presumed dead that ends an attempt its Job has moved past gets
```

і онови рядки `jobs` та `job_attempts`:

У `docs/architecture.md` знайди:

```markdown
| `messages` | One Message per `(conversation_id, ordinal)`; only an assistant Message has an `outcome`: `answered`, `abstained`, or `failed`. |
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code; a retry time only on a failed Job with attempts left, never before it finished; at most one open Job (queued, running, or failed with a retry scheduled) per kind and target. |
| `job_attempts` | One row per `(job_id, attempt)`, removed with its Job; a known stage, warnings, and outcome; counters never below zero; an outcome exactly when it has a finish time; a heartbeat no earlier than the start and no later than the finish. |
| `audit_events` | An action, target kind, decision (`allowed` or `denied`), and outcome from fixed lists, with optional request and Synchronization Run IDs; no raw content: metadata keys are short names, string values one plain word, and the bag is capped at 1024 bytes. Rows are only ever inserted. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |
```

і заміни на:

```markdown
| `messages` | One Message per `(conversation_id, ordinal)`; only an assistant Message has an `outcome`: `answered`, `abstained`, or `failed`. |
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries (a `cancelled` Job has a finish time, and keeps the start and error code it had, if any); a bounded attempt count; a capped error code; a retry time only on a failed Job with attempts left, never before it finished; at most one open Job (queued, running, or failed with a retry scheduled) per kind and target. |
| `job_attempts` | One row per `(job_id, attempt)`, removed with its Job; a `heard_at` the database stamps on every report; a known stage, warnings, and outcome; counters never below zero; an outcome exactly when it has a finish time; a heartbeat no earlier than the start and no later than the finish. |
| `audit_events` | An action, target kind, decision (`allowed` or `denied`), and outcome from fixed lists, with optional request and Synchronization Run IDs; no raw content: metadata keys are short names, string values one plain word, and the bag is capped at 1024 bytes. Rows are only ever inserted. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |
```

**Дія 2:** у посібнику з експлуатації напиши, що відновлює relay і де чекають рішення:

У `docs/operations.md` знайди:

```markdown

## Telemetry

Use OpenTelemetry for traces and metrics and structured JSON logs with shared request, Conversation, Synchronization Run, Evaluation Run, and job identifiers. Each Job attempt's stage, counters, warnings, heartbeat, and outcome are stored in `job_attempts` and shown by `GET /v1/jobs/{job_id}` for synchronization Jobs; a running attempt whose heartbeat stops moving is the sign of a dead worker. Collect queue depth, run age, retry counts, retrieval-stage latency, Candidate counts, model tokens, estimated cost, streaming cancellation, and provider errors. Production content fields are redacted by default.

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. A failed Job comes back, and each enabled Source's next synchronization is queued, only through `make worker-relay`, which queues due retries and due synchronizations before it publishes, so run it on a schedule more often than `KNOWLEDGE_SERVICE_SYNCHRONIZATION_INTERVAL_MINUTES` (60 by default). A synchronization pass takes its Source's lease in Redis first; while Redis is down no pass starts, and its Job is retried later. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

і заміни на:

```markdown

## Telemetry

Use OpenTelemetry for traces and metrics and structured JSON logs with shared request, Conversation, Synchronization Run, Evaluation Run, and job identifiers. Each Job attempt's stage, counters, warnings, heartbeat, and outcome are stored in `job_attempts` and shown by `GET /v1/jobs/{job_id}` for synchronization Jobs; a running attempt whose heartbeat stops moving is the sign of a dead worker. Each `make worker-relay` pass first fails the attempts of stale Jobs (silent for five minutes) with `worker_lost` and of orphaned ones (published 30 minutes ago and never taken up) with `delivery_lost`; a Job that failed with no retry planned is listed by `GET /v1/job-decisions` until an administrator retries or cancels it. Collect queue depth, run age, retry counts, retrieval-stage latency, Candidate counts, model tokens, estimated cost, streaming cancellation, and provider errors. Production content fields are redacted by default.

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. A failed Job comes back, and each enabled Source's next synchronization is queued, only through `make worker-relay`, which recovers stale and orphaned Jobs, then queues due retries and due synchronizations, before it publishes, so run it on a schedule more often than `KNOWLEDGE_SERVICE_SYNCHRONIZATION_INTERVAL_MINUTES` (60 by default). A synchronization pass takes its Source's lease in Redis first; while Redis is down no pass starts, and its Job is retried later. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

**Дія 3:** у моделі безпеки дай адміністраторам новий обов'язок:

У `docs/security.md` знайди:

```markdown
- **User** asks questions and manages their retained Conversations.
- **Content manager** configures Sources and starts Synchronization Runs.
- **Evaluator** manages approved Evaluation Datasets and runs paid evaluation.
- **Administrator** manages policy and operational configuration.

Application Roles never imply Document access.
```

і заміни на:

```markdown
- **User** asks questions and manages their retained Conversations.
- **Content manager** configures Sources and starts Synchronization Runs.
- **Evaluator** manages approved Evaluation Datasets and runs paid evaluation.
- **Administrator** manages policy and operational configuration, and retries or cancels Jobs that failed for good.

Application Roles never imply Document access.
```

**Дія 4:** у моделі загроз додай точку входу перед health checks:

У `docs/threat-model.md` знайди:

```markdown
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: needs a bearer token and the `user` role, then sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `DELETE /v1/conversations/{conversation_id}` | Any client that reaches the API | Deletes the caller's own Conversation. Needs a bearer token and the `manage_own_conversations` operation (the `user` role), both checked before the ID is parsed; the owner is the token's User, and only the canonical spelling of an ID is accepted; the answer is 204 whether the Conversation was deleted, missing, or another User's |
| `GET /v1/jobs/{job_id}` | Any client that reaches the API | Shows one synchronization Job and its newest attempt's progress. Needs a bearer token and the `start_synchronization_runs` operation (the `content-manager` role), both checked before the ID is parsed; a missing Job and a Job of another kind give the same 404; no error text, only codes |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Redis, `127.0.0.1:6380` | Developers and local workers | Bound to 127.0.0.1, with no password: anyone on this machine can read or write its queues, so a worker checks every message argument again. It never evicts a key and keeps nothing across a restart |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors. A queue message, JSON and never pickle, carries only the harmless `ping` token or one canonical Job ID; the worker reads what to do from PostgreSQL. A stored Job is committed before any message names it, and a Job that is no longer queued is not run again. A Job whose message was sent but then lost (a Redis restart, a message dead-lettered before the Job was claimed, or one dropped by the one-day age limit) stays queued and published until M08-T08 |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
| OpenAI-compatible adapter, Open WebUI | Users | M09 |
| MCP server and agent tools | Agents | M11 |
```

і заміни на:

```markdown
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: needs a bearer token and the `user` role, then sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `DELETE /v1/conversations/{conversation_id}` | Any client that reaches the API | Deletes the caller's own Conversation. Needs a bearer token and the `manage_own_conversations` operation (the `user` role), both checked before the ID is parsed; the owner is the token's User, and only the canonical spelling of an ID is accepted; the answer is 204 whether the Conversation was deleted, missing, or another User's |
| `GET /v1/jobs/{job_id}` | Any client that reaches the API | Shows one synchronization Job and its newest attempt's progress. Needs a bearer token and the `start_synchronization_runs` operation (the `content-manager` role), both checked before the ID is parsed; a missing Job and a Job of another kind give the same 404; no error text, only codes |
| `GET /v1/job-decisions`, `POST /v1/jobs/{job_id}/retry`, `POST /v1/jobs/{job_id}/cancel` | Any client that reaches the API | Lists Jobs of every kind that failed for good, with their kind, target ID, and error code, and retries or cancels one. Needs a bearer token and the `recover_jobs` operation (the `administrator` role), both checked before the ID is parsed; each decision is an audit event with the operator as actor; no error text, only codes |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Local Redis, `127.0.0.1:6380` | Developers and local workers | Bound to 127.0.0.1, with no password: anyone on this machine can read or write its queues, so a worker checks every message argument again. It never evicts a key and keeps nothing across a restart |
| Local Keycloak, `http://localhost:8180` | Developers and, later, Open WebUI | Bound to 127.0.0.1. One application client, `open-webui`: the code flow with S256 PKCE, one exact redirect URI, no optional scopes, and only the four application roles in its tokens. No client in the realm gives a token for a password alone. The master realm's admin console shares the port, behind the generated bootstrap admin. Non-production demo users with fixed IDs |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors. A queue message, JSON and never pickle, carries only the harmless `ping` token or one canonical Job ID; the worker reads what to do from PostgreSQL. A stored Job is committed before any message names it, and a Job that is no longer queued is not run again. A Job whose message was sent but then lost (a Redis restart, a message dead-lettered before the Job was claimed, or one dropped by the one-day age limit) stays queued and published until recovery, 30 minutes after it was published, counts it as a lost attempt |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
| OpenAI-compatible adapter, Open WebUI | Users | M09 |
| MCP server and agent tools | Agents | M11 |
```

і онови рядки, які чекали на цей урок, додавши в кінці новий сценарій зловживання:

У `docs/threat-model.md` знайди:

```markdown
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one; whatever the model then writes passes the Answer's content checks | None |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |
| Two synchronization passes of one Source run at once: double Source calls, and an older pass finishing late re-grants a removed reader, rolls content back, or tombstones what a newer pass saw | `synchronize_source_exclusively` holds the Source's Redis lease, with an owner token, renewed after every page, so a second pass gets `SourceBusy` before reading; a crashed holder's lease expires in two minutes. The Source's fence in PostgreSQL decides who may write: every pass claims it with its observation time, and every narrowing, reconciliation, and tombstone checks it under a share lock on the Source's row, so after a newer pass claims it nothing of an older pass lands; an observation more than five minutes in the future is refused | Writes an older pass committed before the newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way; a pass observed up to five minutes in the future holds the fence until then |
| A stale or replayed worker rewrites a Job's progress, or a bad clock blocks it | A report shares the Job's row lock only while the Job runs that attempt, counters and warnings never go down and the heartbeat never goes back (checked again in the `UPDATE`), a report far ahead of the database's clock is refused, each attempt has its own row, and ending an attempt the Job has moved past is refused; ending an attempt never fails on what a report stored | No production handler reports progress until M08-T10; a Job whose worker died stays running with its last heartbeat until M08-T08; a worker more than five minutes ahead of the database cannot report |
| A scheduled synchronization Job carries a Source's location or credentials through Redis, or the scheduler floods the queue | A scheduled Job stores only the Source's ID, and its handler reads the configuration and credentials reference from PostgreSQL when it runs; a Source with an open Job is skipped, a missed schedule queues one Job, and a pass queues at most 1,000 | Disabling a Source does not cancel a Job already queued for it; a Source whose synchronization Job is stuck open is not scheduled again until M08-T08 |

### Retrieval
```

і заміни на:

```markdown
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one; whatever the model then writes passes the Answer's content checks | None |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued until recovery counts it as a lost delivery |
| Two synchronization passes of one Source run at once: double Source calls, and an older pass finishing late re-grants a removed reader, rolls content back, or tombstones what a newer pass saw | `synchronize_source_exclusively` holds the Source's Redis lease, with an owner token, renewed after every page, so a second pass gets `SourceBusy` before reading; a crashed holder's lease expires in two minutes. The Source's fence in PostgreSQL decides who may write: every pass claims it with its observation time, and every narrowing, reconciliation, and tombstone checks it under a share lock on the Source's row, so after a newer pass claims it nothing of an older pass lands; an observation more than five minutes in the future is refused | Writes an older pass committed before the newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way; a pass observed up to five minutes in the future holds the fence until then |
| A stale or replayed worker rewrites a Job's progress, or a bad clock blocks it | A report shares the Job's row lock only while the Job runs that attempt, counters and warnings never go down and the heartbeat never goes back (checked again in the `UPDATE`), a report far ahead of the database's clock is refused, each attempt has its own row, and ending an attempt the Job has moved past is refused; ending an attempt never fails on what a report stored | No production handler reports progress until M08-T10; a Job whose worker died stays running with its last heartbeat until recovery finds it silent for five minutes; a worker more than five minutes ahead of the database cannot report |
| A scheduled synchronization Job carries a Source's location or credentials through Redis, or the scheduler floods the queue | A scheduled Job stores only the Source's ID, and its handler reads the configuration and credentials reference from PostgreSQL when it runs; a Source with an open Job is skipped, a missed schedule queues one Job, and a pass queues at most 1,000 | Disabling a Source does not cancel a Job already queued for it; a Source whose synchronization Job is stuck open is not scheduled again until recovery fails its attempt |
| A live worker is taken for a dead one, or an operator's decision races a worker or another operator | Recovery judges a Job again under its row lock and skips a row a worker holds, so a report or claim in progress keeps it alive; a worker that wakes after recovery cannot report or end the attempt; a cancel refuses a running Job; a retry is refused while another Job for the same work is open, enforced by the open-intent index; each decision is audited | A worker silent for five minutes between reports is taken for dead and its work may run twice; a Job claimed without an attempt row is judged by its worker's start time; a message that waits in the queue longer than 30 minutes costs an attempt, so a long backlog can exhaust healthy Jobs; a cancelled purge is queued again by the next pass while its Document's Chunks remain; a worker that keeps sending refused reports stays alive and its Job stays running, and a running Job cannot be cancelled, so its process must be stopped; a pass can be filled by candidates it cannot recover, such as rows held locked |

### Retrieval
```

**Перевірка:** `make docs-check` завершується з кодом 0.

### Крок 8 — Без коду: перевірки, докази і прогрес

Цей крок запускає всі перевірки й завершує записи.

**Дія 1:** запусти всі перевірки одну за одною:

```bash
make format-check
make lint
make typecheck
make test-unit
make test-integration
make docs-check
```

**Дія 2:** заверши файл доказів: результати перевірок і кількість із кроку 6 у «Verification», знахідки рев'ю й відомі обмеження в «Review», дизайн у «Design» і «Decision», а також свої відповіді на питання для рефлексії нижче. Потім познач завдання виконаним у `docs/curriculum/PROGRESS.md` і зроби коміт з `m08-t08: recover dead and stuck work`.

**Перевірка:** усі перевірки проходять, а після коміту `git status` чистий.

## Питання для рефлексії

- J8 стартував годину тому, а востаннє звітував 40 секунд тому. Яку колонку читає відновлення, щоб вирішити, що він живий, і чому не `heartbeat_at`?
- Воркер A був лише призупинений на шість хвилин, і відновлення завершило його спробу. Що станеться, коли він прокинеться і звітуватиме, і що буде з роботою, яку він уже зробив?
- Повідомлення очищення 35 хвилин чекало за чергою інших. Що зробило відновлення і що робить пізнє повідомлення, коли нарешті приходить? Чи ти радше публікував би знову, не рахуючи спроби? Чого б це коштувало?
- Який збій може статися вже після того, як викликач вважає, що робота вдалася? Подумай про воркер, який завершив свої записи, а потім мовчав п'ять хвилин, перш ніж зберегти результат.
- У адміністратора забирають роль, поки в нього відкритий список рішень. Що станеться з його наступним повтором і де записане його попереднє рішення?
- Який тест упаде першим, якщо хтось пізніше змусить відновлення чекати на заблокований рядок замість того, щоб його пропускати? А який, якщо прибрати savepoint навколо збереження при повторі?

## Чекліст завершення

- [ ] Файл доказів містить інваріант, власників і твої прогнози, записані до зміни.
- [ ] `jobs.py` має `cancelled`, `reopen` і `cancel`; `job_conditions.py` існує; unit-тести кроку 2 проходять.
- [ ] Міграція `d0e1f2a3b4c5`, `heard_at`, `record_heard`, `persistence/job_recovery.py` і `job_recovery.py` існують; relay спершу відновлює; claim очищення ніколи не стартує раніше за час постановки в чергу.
- [ ] `job_decisions.py` і три endpoint-и існують, за операцією `recover_jobs`; тести ролей і маршрутів проходять.
- [ ] `tests/test_job_recovery_integration.py` проходить: тренування, осиротілі, рішення, relay, міграція і знахідки рев'ю.
- [ ] Ти виконав навмисну поломку, побачив, що повторна перевірка під блокуванням падає, а запит усе одно лишає повільний Job живим, і скасував її.
- [ ] `docs/architecture.md`, `docs/operations.md`, `docs/security.md` і `docs/threat-model.md` описують відновлення і рішення.
- [ ] Усі перевірки проходять: формат, lint, типи, unit, integration, документація.
- [ ] Докази, статус завдання, рядок у PROGRESS і коміт готові.

Коли напишеш файли, поділись ними або виводом помилок, і я перевірю їх за цим чеклістом.
