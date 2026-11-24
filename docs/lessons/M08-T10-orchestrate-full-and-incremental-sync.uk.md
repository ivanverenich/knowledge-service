English version: [M08-T10-orchestrate-full-and-incremental-sync.md](M08-T10-orchestrate-full-and-incremental-sync.md)

# M08-T10 — Повна й інкрементальна синхронізація

Завдання: [M08-T10 — Orchestrate full and incremental sync](../curriculum/milestones/M08-jobs-and-confluence/M08-T10-orchestrate-full-and-incremental-sync.md)

## Що ти зробиш

Цей урок змушує воркер справді запускати синхронізації. З M08-T06 relay щогодини ставить у чергу один Job `synchronize_source` на кожне джерело. Жоден воркер їх не виконував, тож вони чекали вічно. Після уроку воркер бере кожен такий Job, читає з PostgreSQL джерело, яке той називає, запускає прохід із M08-T09, що вміє продовжуватися, звітує про прогрес після кожної сторінки й записує, чим усе скінчилось. Прохід також може бути **інкрементальним** (incremental): джерело, яке вміє перелічувати власні зміни, питають лише про те, що змінилось від минулого разу.

Конкретний приклад. Джерело — локальна тека з файлами `handbook.md` і `leave.md`. Хтось видаляє `leave.md`.

| Час | Relay (`make worker-relay`) | Воркер (`make worker`) | Що після цього лежить у PostgreSQL |
|---|---|---|---|
| 10:00 | ставить у чергу Job J1 для джерела й публікує його | | J1 `queued` |
| 10:00 | | бере J1: `running`, спроба 1 | J1 `running`; рядок спроби |
| 10:00 | | читає рядок джерела: тека, читати можуть усі | |
| 10:00 | | запускає прохід; після кожної сторінки звітує "1 оброблено", "2 оброблено" | лічильники спроби ростуть |
| 10:00 | | прохід завершився; J1 `succeeded` | обидва Document доступні |
| 10:05 | `leave.md` видалено з диска | | |
| 11:00 | ставить у чергу Job J2 і публікує його | | J2 `queued` |
| 11:00 | | виконує J2: повний прохід, який уже не бачить `leave.md` | `leave.md` отримує tombstone; Job очищення стоїть у черзі |

| Що просить завдання | Де це зроблено |
|---|---|
| Провести пагінацію, нормалізацію, узгодження, tombstone, прогрес і checkpoint через один глибокий модуль | Блоки 1–3; крок 7: `ingestion/synchronization_jobs.py` |
| Повна й інкрементальна синхронізація | Блоки 4 і 5; кроки 3–6 |
| Набір тестів з фейковим джерелом покриває порожній, багатосторінковий прохід, оновлення, видалення, повтор і скасування | Крок 9: `tests/test_synchronization_jobs_integration.py` |
| Успіх, межові випадки й типізовані відмови на справжній інфраструктурі | Крок 9 (PostgreSQL), крок 11 (справжній воркер, relay і Redis) |
| Поведінка під час повтору й скасування явна | Блоки 2 і 6 |
| Пояснити, що спростувало б цей дизайн | Крок 10 і питання для роздумів |
| Оновити лише зачеплену документацію | Крок 12: architecture, operations, threat model |

Цей урок містить код: кроки 2–9 пишуть міграцію, код і тести. Кроки 1, 10, 11, 12 і 13 коду не потребують, хоча крок 12 редагує три документи.

Терміни цього уроку:

- **Обробник (handler):** функція, яку воркер викликає для одного виду Job. Воркер знаходить її за полем `kind` Job — як у розмові про те, що воркер робить із Job.
- **Захоплення (claim):** переведення Job із `queued` у `running` під блокуванням рядка, щоб друге повідомлення не запустило його теж.
- **Спроба (attempt):** одна спроба Job. Вона має власний рядок прогресу в `job_attempts` (M08-T07).
- **Повний прохід (full pass):** читає кожен елемент джерела й ставить tombstone тому, чого не побачив.
- **Інкрементальний прохід (incremental pass):** читає лише елементи, що змінились від минулого проходу, і ставить tombstone лише тому, про що джерело сказало "видалено".
- **Стрічка змін (change feed):** здатність джерела перелічувати власні зміни. У коді це протокол `ChangeFeed`. Локальна тека її не має; Confluence матиме (M08-T12).
- **Курсор (cursor):** власна позначка джерела "зміни після цього моменту", наприклад час. Він зберігається між проходами, на відміну від checkpoint, що живе лише всередині одного проходу.
- **Довідник принципалів (principal directory):** відповідність імен користувачів і груп джерела користувачам і групам провайдера ідентичності (M07-T06).

## Перш ніж почати

Цей розділ пояснює кожне рішення, на яке спирається код. Прочитай його першим; кроки на нього розраховують.

### 1. Захоплення фіксується одразу

Прохід може тривати хвилини. Захоплення не має тримати блокування рядка Job увесь цей час.

Ось що сталося б, якби захоплення, увесь прохід і завершення жили в одній транзакції, як в обробнику очищення:

| Час | Воркер (одна транзакція) | Звіт про прогрес | Відновлення |
|---|---|---|---|
| 10:00:00 | блокує J1 `FOR UPDATE`, ставить `running` (не зафіксовано) | | |
| 10:00:05 | завершує сторінку 1, викликає `report_progress` | відкриває власну транзакцію й чекає на блокування J1 | |
| 10:00:05 | чекає, поки звіт поверне керування | чекає на транзакцію воркера | |

Воркер чекає на власний звіт, а звіт чекає на воркер. Ніщо не рухається. Прихований факт: `report_progress` бере спільне блокування рядка Job в окремій транзакції (M08-T07). Рядок, заблокований `FOR UPDATE`, не дає взяти це блокування, доки транзакція-власник не закінчиться. Навіть без звітів інші сесії весь прохід бачили б J1 як `queued`, бо незафіксовані зміни їм невидимі. Відновлення й API теж помилялися б.

Наскільки це погано тут? Обробник очищення тримає блокування на одне коротке видалення, тож для нього все гаразд. Синхронізація триває хвилини й звітує про прогрес, тож вона зависла б на першій же сторінці.

Виправлення: захопити в короткій окремій транзакції, потім запустити прохід, потім завершити в іншій транзакції.

Було (форма обробника очищення):

```python
async with runtime.transaction() as connection:
    job = await _claim(connection, job_id=job_id, now=now)
    # ... the whole work, under the lock ...
    await save_job(connection, job=job.succeed(finished_at=now))
```

Стало:

```python
job = await _claim(runtime, job_id=job_id, now=now)  # commits: running
# ... the pass, reporting after every page ...
return await self._finish(runtime, job=job, ...)  # a new transaction
```

Це як записати книжку на себе біля стійки бібліотеки, перш ніж читати, а не стояти біля стійки, поки читаєш.

### 2. Записуй завершення, лише якщо спроба досі твоя

Оскільки захоплення фіксується рано, поки йде прохід, із Job може щось зробити хтось інший. Цей хтось — відновлення (M08-T08).

| Час | Воркер A (спроба 1) | Відновлення | Рядок Job після цього |
|---|---|---|---|
| 10:00 | бере J1, читає сторінку 1 | | `running`, спроба 1 |
| 10:01 | процес призупинено (ноутбук заснув) | | |
| 10:07 | | J1 мовчить шість хвилин: позначає спробу 1 як `worker_lost`, планує повтор | `failed`, повтор о 10:07:30 |
| 10:08 | прокидається, звітує про сторінку 2 | | звіт відхилено: J1 не виконується |
| 10:08 | пробує записати `succeeded` | | теж має бути відхилено |

Прихований факт: поки воркер A спав, його Python-об'єкти не змінились. Його змінна `job` досі каже `running`, спроба 1. Лише база знає, що відновлення пішло далі. Тому завершення перечитує рядок під блокуванням і перевіряє дві речі: чи Job досі `running` і чи це та сама спроба? Якщо ні, рішення відновлення лишається в силі, а доставка повідомляє збережений результат як дублікат.

```python
stored = await load_job(connection, job_id=job.job_id, for_update=True)
if stored.status is not JobStatus.RUNNING or attempt_number(stored) != attempt:
    raise JobAlreadyClaimed(stored, "the attempt was ended before it could finish")
```

M08-T08 залишив саме це як відоме обмеження: "the M08-T10 handler must check its claim before it stores a finish". Без перевірки запізнілий воркер перезаписав би `failed, retry planned` на `succeeded`. Або ще гірше: завершив би спробу, яку вже почав повтор.

Скасована спроба або спроба, обірвана лімітом часу актора, не записує нічого. Це `BaseException`, а не `Exception`, тож `except Exception` в обробнику їх пропускає. Job лишається `running`, доки відновлення не помітить тишу, а повтор продовжує з checkpoint. Крок 9 тестує саме це.

### 3. Кожна спроба спостерігає джерело у свій час початку

Кожен прохід займає огорожу джерела своїм часом спостереження (M08-T05). Новіший час перемагає, а той самий час двічі відхиляється.

Обробник бере за час спостереження початок спроби, `job.started_at`. Захоплення ставить його як `max(now, queued_at)`. Повтор стає в чергу після того, як невдала спроба закінчилась, тож його початок завжди пізніший. Тому повтор займає огорожу й продовжує збережений прохід (M08-T09).

Якби обробник брав початковий `queued_at` Job, спроба 2 спостерігала б у той самий час, що й спроба 1. Огорожа відмовила б їй із `SynchronizationSuperseded`, остаточною відмовою, і прохід ніколи б не продовжився.

### 4. Інкрементальний прохід ніколи не ставить tombstone через відсутність

Повний прохід вирішує про видалення за відсутністю: чого не побачив — того немає. Інкрементальний прохід бачить лише зміни, тож відсутність нічого не означає.

Візьмімо джерело зі стрічкою змін, де є `a.txt`, `b.txt`, `c.txt`. Від минулого проходу `a.txt` відредагували, а `b.txt` видалили. `c.txt` не змінювався.

| | Повний прохід | Інкрементальний прохід |
|---|---|---|
| Що він питає в джерела | "кожен елемент", сторінка за сторінкою | "зміни після курсора `c1`" |
| Що приходить | `a.txt` (відредагований), `c.txt` | `a.txt` (відредагований), "`b.txt` видалено" |
| Чому він ставить tombstone наприкінці | усьому, що не запропоновано: `b.txt` | лише тому, про що сказано "видалено": `b.txt` |
| Якби він помилково взяв правило повного | | ще й `c.txt`, бо його не запропонували |

Прихований факт: стрічка змін відповідає на питання "що змінилось", а не "що існує". Тому список побаченого з M08-T09 тепер зберігає **останнє повідомлення** про кожен елемент у проході — "є" чи "видалено" — у новій колонці `synchronization_seen.deleted`. Повний прохід ставить tombstone доступним Document, про які немає повідомлення "є". Інкрементальний — лише тим, чиє останнє повідомлення було "видалено".

```sql
-- full: never offered as present
... AND NOT EXISTS (SELECT 1 FROM synchronization_seen s
                    WHERE s.source_id = documents.source_id
                      AND s.external_id = documents.external_id
                      AND NOT s.deleted)
-- incremental: last report said deleted
... AND EXISTS (SELECT 1 FROM synchronization_seen s
                WHERE s.source_id = documents.source_id
                  AND s.external_id = documents.external_id
                  AND s.deleted)
```

Стрічка змін може щось пропустити. Confluence, наприклад, може не повідомити про зміну прав як про зміну вмісту. Тому повний прохід повертається щонайменше раз на `full_synchronization_interval_hours`, типово 24 години.

### 5. Звідки береться курсор і коли йому довіряють

Курсор — власна закладка джерела між проходами. Помилка з ним або пропускає зміни, або повторює їх.

Правила, кожне з яких забезпечує код:

- **Курсор несе лише остання сторінка.** `SourcePage` відмовляє курсору поруч із `next_checkpoint`. Курсор із середини переліку заявив би "усе до цього місця" зарано.
- **Джерело бере його з моменту, коли прохід почався.** Зміна, зроблена, поки прохід читав сторінку 7, має все одно опинитися "після" курсора. Це обов'язок джерела; docstring протоколу так і каже.
- **Повний прохід джерела зі стрічкою зберігає курсор.** Якщо на останній сторінці курсора немає, `sync_cursor` стає NULL, і наступний прохід знову повний. Це безпечно.
- **Інкрементальний прохід мусить закінчитися новим курсором.** Якщо ні, прохід падає до tombstone і зберігає старий курсор, тож наступний прохід питає знову з того самого місця.
- **Новий прохід інкрементальний, лише коли виконано все:** джерело — `ChangeFeed`, у нього є збережений курсор, і його останній повний прохід закінчився менше ніж інтервал повного проходу тому.
- **Продовжений прохід зберігає свій режим,** крім випадку, коли він був інкрементальним, а в джерела більше немає стрічки. Тоді починається повний прохід (знахідка рев'ю, блок 7).

Це як передплата газети. Першого разу читаєш усі старі номери. Далі — лише номери, датовані після закладки, а раз на рік перевіряєш усю полицю, чи нічого не пропущено.

### 6. Прогрес — це ще й ознака життя

Відновлення (M08-T08) вважає Job, що виконується, мертвим після п'яти хвилин без жодного слова від нього. Звіт обробника про прогрес після кожної зафіксованої сторінки і є цим словом.

Звіт надсилає накопичені підсумки цієї спроби: оброблено (узгоджено або відправлено в карантин), змінено й пропущено (у карантині). Також він надсилає попередження: невідомі читачі, елементи в карантині. База відмовляє будь-якому звіту, де підсумок, попередження чи heartbeat ідуть назад. Тож обробник тримає дві речі, які лише ростуть, і обидві з'явились після рев'ю (блок 7). Попередження — це множина, до якої лише додають і яку ніколи не очищають. Heartbeat — це `max(годинник, останній heartbeat)`.

Варто знати два крайні випадки. Одна сторінка, що триває понад п'ять хвилин, робить здоровий воркер схожим на мертвий. А оренда (M08-T05) продовжується після кожної сторінки й живе дві хвилини. Тож сторінки мають бути значно коротшими за дві хвилини в будь-якому разі.

### 7. Знахідки рев'ю

Незалежний рецензент атакував першу версію випадковими падіннями, стрибками годинника, ворожими джерелами й міграціями посеред проходу. Шість атак її зламали. Для кожного виправлення є тест у кроці 9, який без нього падає.

**Попередження, яке пізніше повідомлення знімає, валило Job.** Сторінка 1 запропонувала `a.txt` з невідомим читачем `user:bob` — це попередження. Сторінка 2 знову запропонувала `a.txt`, тепер публічним. Лічильник невідомих упав до 0, попередження зникло з наступного звіту, і база відхилила звіт як такий, що "йде назад". Job остаточно опинився у статусі failed. Тепер обробник зберігає кожне попередження, яке зустрів. Тест: `test_a_warning_a_later_report_resolves_does_not_fail_the_job`.

**Годинник, що відступив назад, валив Job.** Годинник машини показував 10:00:10 на сторінці 1 і 10:00:08 на сторінці 2, після синхронізації часу. Heartbeat пішов назад, і звіт відхилили. Тепер heartbeat ніколи не опускається нижче за попередній. Тест: `test_a_clock_stepping_back_between_pages_does_not_fail_the_job`.

**Job, поставлений годинником, що поспішав, падав як баг.** Relay, чий годинник поспішав на 6 хвилин, поставив Job у чергу. Захоплення починає його в `queued_at`, огорожа відмовляє спостереженню, що на понад п'ять хвилин попереду, і помилку класифікували як програмну, тобто остаточну. Тепер ця помилка — `ObservedInTheFuture`, класифікована як нова тимчасова відмова `clock_ahead`, тож Job повторюють. Тест: `test_a_job_queued_by_a_clock_far_ahead_is_retried_not_failed_for_good`.

**Інкрементальний прохід пережив свою стрічку.** Спроба 1 інкрементального проходу впала. Потім адаптер перевстановили без стрічки змін. Повтор продовжив збережений інкрементальний прохід і остаточно опинився у статусі failed, як і кожен прохід протягом години. Тепер збережений інкрементальний прохід продовжує лише джерело, яке досі стежить за змінами; інакше починається повний прохід. Тест: `test_an_incremental_pass_whose_source_lost_its_feed_starts_over_in_full`.

**NULL замість імені читача вважався багом.** PostgreSQL дозволяє масиву тексту містити NULL, і `default_principals = {NULL}` ламав `SourceAccess`. Тепер це `source_misconfigured`. Тест: `test_a_null_default_principal_is_misconfigured_not_a_bug`.

**Міграція вниз падала посеред інкрементального проходу.** Вона очищала час початку проходу, але не його режим, і це порушувало перевірку, що пов'язує їх. Тепер вона очищає обидва. Тест: `test_the_migration_goes_down_and_up_with_passes_in_progress`.

Інші атаки рецензента дизайн витримав: повторні й паралельні повідомлення, відновлення, що змагається із завершенням, порядок огорожі при повторах, курсор не на останній сторінці, елементи, видалені й знову запропоновані між сторінками й крізь падіння, прибирання клієнта оренди, підключення воркера і величезні сторінки.

### 8. Відомі обмеження

**Між повними проходами інкрементальний прохід довіряє стрічці.** Видалення чи зміна прав, про які джерело не повідомило, чекають наступного повного проходу, типово до 24 годин. Модель загроз це записує.

**Сторінка, повільніша за дві хвилини, втрачає оренду, а повільніша за п'ять — виглядає мертвою.** Обірваний так прохід продовжується з checkpoint, але повторює роботу.

**Читачі локальної теки — одне налаштування на всю теку.** Збережені `default_public` і `default_principals` стосуються кожного файлу. Якщо не задано жодного, читати не може ніхто — відмова за замовчуванням. У застосунку поки ніщо не реєструє джерело; рядок джерела вставляєш власноруч (крок 11).

**Job `clock_ahead` витрачає свої три спроби приблизно за хвилину.** Якщо годинники розходяться довше, він чекає на адміністратора (M08-T08).

**Лічильники охоплюють одну спробу.** Лічильники спроби й подія аудиту описують лише сторінки, які прочитала ця спроба, як у M08-T09.

## Покрокові інструкції

Перед початком запусти PostgreSQL і Redis (`make redis-up`). Усі команди виконуй з кореня репозиторію. Для інтеграційних перевірок задай у терміналі URL тестової бази:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

Запускай проти цієї бази лише одну інтеграційну команду за раз.

### Крок 1 — У цьому кроці без коду: запиши інваріант і свої передбачення

Перш ніж щось змінювати, запиши, що має бути правдою наприкінці і що ти очікуєш побачити. Передбачення ти перевіриш у кроках 10, 11 і 13.

Створи `docs/evidence/M08-T10-orchestrate-full-and-incremental-sync.md`:

```markdown
# M08-T10 — Orchestrate full and incremental sync evidence

## Invariant

The worker runs every queued `synchronize_source` Job through
`ingestion.synchronization_jobs`: it claims the Job in its own transaction,
reads the Source the Job names, and runs the resumable pass observed at the
attempt's start, reporting the attempt's totals after every committed page.
It stores the end only while the Job still runs that attempt. A full pass
tombstones every available Document it never saw offered; an incremental
pass, only for a Source with a change feed, a stored cursor, and a full pass
within the full-pass interval, reads only changes after the cursor and
tombstones only what the Source reported deleted. A missing or disabled
Source, or one no adapter can open, fails for good; an unavailable Source,
database, or lease is retried, and the retry resumes.

## Owners

- Running a Job: the worker, through `run_stored_job` and the handler map
  built by `worker.handlers.job_handlers`.
- Claim, progress, and finish: `ingestion.synchronization_jobs`.
- Mode, cursor, and progress of a pass: `ingestion.checkpoints` and
  PostgreSQL (`sources`, `synchronization_seen`).
- Retries and recovery: the stored Job (M08-T04, M08-T08).

## Expected failing check

Before the change,
`uv run pytest tests/test_synchronization_jobs_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.

## Predictions

- A Source has a change feed and a cursor. Its incremental pass gets one edited item and one deleted item. An unchanged item was not offered. Which Documents are tombstoned:
- The worker sleeps for six minutes mid-pass, recovery fails the attempt, then the worker wakes and finishes. What does the Job's row say afterwards:
- How many tests in the new suite fail if the finish skips the "still running this attempt" check:
- In the drill, after deleting one file and waiting for the next scheduled pass, what does `jobs` contain:

## Design

## Verification

## Review

## Decision

## Reflection
```

Шаблон evidence лишається англійською, як і інші записи в `docs/evidence`. Відповіді на передбачення можеш писати будь-якою мовою.

**Перевірка:** запусти очікувано невдалу перевірку й побач `no tests ran`. Кожне передбачення має твою відповідь після двокрапки.

### Крок 2 — Крок із кодом: міграція і форма таблиць

Цей крок додає колонки, що пам'ятають режим проходу, курсор між проходами, хто може читати локальну теку, і останнє повідомлення про кожен елемент.

**Простими словами:** рядок джерела дізнається чотири нові речі. Чи поточний прохід повний чи інкрементальний, курсор змін, який він тримає між проходами, коли закінчився останній повний прохід і хто може читати файли, коли самі файли про це мовчать. Список побаченого дізнається, чи останнє повідомлення про елемент було "є" чи "видалено". Поки цим ніхто не користується.

| Колонка | Зберігає | Навіщо |
|---|---|---|
| `sources.pass_mode` | `full` або `incremental`, поки йде прохід; інакше NULL | продовжений прохід зберігає режим (блок 5) |
| `sources.sync_cursor` | курсор змін джерела, між проходами | блок 5 |
| `sources.last_full_pass_at` | коли закінчився останній повний прохід | коли змусити повний прохід (блок 4) |
| `sources.default_public`, `default_principals` | хто може читати кожен файл локальної теки | воркер мусить знати, хто читає теку; типово — ніхто |
| `synchronization_seen.deleted` | останнє повідомлення про елемент було "видалено" | блок 4 |

**Рішення:** перевірки тримають кожну пару чесною. Режим проходу існує рівно тоді, коли прохід триває, а тека або публічна, або називає читачів, але не одне й інше. Оновлення позначає вже розпочатий прохід як повний, бо до цієї ревізії кожен прохід був повним. Відкат прибирає незавершений інкрементальний прохід, бо до цієї ревізії він не має сенсу, і забуває рядки "видалено" у списку побаченого, які раніше означали "не побачено".

**Дія 1:** створи `migrations/versions/f2a3b4c5d6e7_synchronization_modes.py`:

```python
"""synchronization modes

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-11-25 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "e1f2a3b4c5d6"


def upgrade() -> None:
    # Who may read every item of a Source whose items carry no access of
    # their own, such as a local folder. The default lets nobody read.
    op.add_column(
        "sources",
        sa.Column(
            "default_public",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "sources",
        sa.Column(
            "default_principals",
            ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
    )
    op.create_check_constraint(
        "ck_sources_default_access",
        "sources",
        "not (default_public and cardinality(default_principals) > 0)",
    )
    op.add_column("sources", sa.Column("sync_cursor", sa.Text(), nullable=True))
    op.add_column(
        "sources",
        sa.Column("last_full_pass_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sources", sa.Column("pass_mode", sa.String(length=16), nullable=True)
    )
    # A pass stored before this revision was a full one.
    op.execute(
        "update sources set pass_mode = 'full' where pass_started_at is not null"
    )
    op.create_check_constraint(
        "ck_sources_pass_mode",
        "sources",
        "(pass_started_at is null) = (pass_mode is null)"
        " and (pass_mode is null or pass_mode in ('full', 'incremental'))",
    )
    op.add_column(
        "synchronization_seen",
        sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    # An incremental pass in progress has no meaning before this revision,
    # so its progress goes; the next pass starts over as a full one.
    op.execute(
        "delete from synchronization_seen where source_id in"
        " (select source_id from sources where pass_mode = 'incremental')"
    )
    op.execute(
        "delete from synchronization_checkpoints where source_id in"
        " (select source_id from sources where pass_mode = 'incremental')"
    )
    op.execute(
        "update sources set checkpoint = null, pass_started_at = null,"
        " pass_mode = null where pass_mode = 'incremental'"
    )
    # A seen row that says "deleted" meant "not seen" before this revision.
    op.execute("delete from synchronization_seen where deleted")
    op.drop_column("synchronization_seen", "deleted")
    op.drop_constraint("ck_sources_pass_mode", "sources", type_="check")
    op.drop_column("sources", "pass_mode")
    op.drop_column("sources", "last_full_pass_at")
    op.drop_column("sources", "sync_cursor")
    op.drop_constraint("ck_sources_default_access", "sources", type_="check")
    op.drop_column("sources", "default_principals")
    op.drop_column("sources", "default_public")
```

**Дія 2:** у `src/knowledge_service/persistence/schema.py`, у таблиці `sources`, заміни все від коментаря до `pass_started_at` до закривної `)` таблиці ось цим:

```python
    # When the pass whose progress `checkpoint` records started observing the
    # Source; NULL when no pass is in progress (`ingestion.checkpoints`).
    sa.Column("pass_started_at", sa.DateTime(timezone=True), nullable=True),
    # Whether the pass in progress is `full` or `incremental`; NULL with no
    # pass in progress (`ingestion.checkpoints`).
    sa.Column("pass_mode", sa.String(length=16), nullable=True),
    # The Source's own token for "changes after the last finished pass", kept
    # between passes; NULL when the Source has no change feed.
    sa.Column("sync_cursor", sa.Text(), nullable=True),
    # When the last full pass finished; a full pass is due again after
    # `full_synchronization_interval_hours`.
    sa.Column("last_full_pass_at", sa.DateTime(timezone=True), nullable=True),
    # Who may read every item of a Source whose items carry no access of
    # their own, such as a local folder. The default lets nobody read.
    sa.Column(
        "default_public", sa.Boolean(), nullable=False, server_default=sa.false()
    ),
    sa.Column(
        "default_principals",
        ARRAY(sa.Text()),
        nullable=False,
        server_default=sa.text("'{}'::text[]"),
    ),
    sa.UniqueConstraint("kind", "location", name="uq_sources_kind_location"),
    sa.CheckConstraint(
        "checkpoint is null or pass_started_at is not null",
        name="ck_sources_checkpoint_pass",
    ),
    sa.CheckConstraint(
        "(pass_started_at is null) = (pass_mode is null)"
        " and (pass_mode is null or pass_mode in ('full', 'incremental'))",
        name="ck_sources_pass_mode",
    ),
    sa.CheckConstraint(
        "not (default_public and cardinality(default_principals) > 0)",
        name="ck_sources_default_access",
    ),
)
```

**Дія 3:** у тому самому файлі заміни таблицю `synchronization_seen` разом із коментарем над нею на:

```python
# Each external ID the pass in progress has met, with its last report in the
# pass, so a resumed pass still knows them when it tombstones at the end.
synchronization_seen = sa.Table(
    "synchronization_seen",
    metadata,
    sa.Column(
        "source_id",
        sa.Uuid(),
        sa.ForeignKey("sources.source_id", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("external_id", sa.Text(), nullable=False),
    # True when the item's last report in the pass said it was deleted.
    sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.PrimaryKeyConstraint("source_id", "external_id", name="pk_synchronization_seen"),
)
```

**Перевірка:** `uv run pyright src/knowledge_service/persistence/schema.py` показує `0 errors`. Тести кроку 9 мігрують кожну тестову базу до цієї ревізії, а один із них іде вниз і знову вгору.

### Крок 3 — Крок із кодом: контракт джерела і нові відмови

Цей крок учить контракт джерела про курсори й стрічки змін і дає імена трьом новим способам, якими Job синхронізації може впасти.

**Простими словами:** сторінка тепер може нести курсор джерела, але лише остання. Джерело також може запропонувати другий спосіб читання: "зміни після цього курсора". Три помилки отримують імена: джерела немає, джерело не відкривається, і час надто далеко в майбутньому. Кожна відповідає збереженому коду відмови, і повторювати варто лише ту, що про годинник.

| Ім'я | Що це | Повторюється? |
|---|---|---|
| `SourcePage.cursor` | курсор змін джерела, лише на останній сторінці | |
| `ChangeFeed` | необов'язковий протокол із `fetch_changes(since, checkpoint, page_size)` | |
| `SyncMode` | `full` або `incremental` | |
| `SourceGone` → `source_gone` | рядка джерела немає або його вимкнено | ні |
| `SourceMisconfigured` → `source_misconfigured` | жоден адаптер не відкриває збережену конфігурацію | ні |
| `ObservedInTheFuture` → `clock_ahead` | час спостереження на понад п'ять хвилин попереду | так |

**Рішення:** `ChangeFeed` — окремий протокол, який можна перевірити під час виконання, а не новий метод `SourcePort`. Кожне наявне джерело й тестовий фейк працюють без змін, а `isinstance(source, ChangeFeed)` каже проходу, чи можливий інкрементальний режим. `ObservedInTheFuture` успадковує `InvalidSource`, тож наявний код, що ловить `InvalidSource`, і далі його ловить.

Усі дії нижче — у `src/knowledge_service/ingestion/sources.py`, якщо не сказано інакше.

**Дія 1:** зміни імпорт із `typing` на:

```python
from typing import Protocol, runtime_checkable
```

**Дія 2:** додай три помилки просто перед `SynchronizationSuperseded`:

```python
class ObservedInTheFuture(InvalidSource):
    """A pass's observation time is too far ahead of this machine's clock.

    A clock that ran ahead queued or claimed the Job; trying again later,
    when the clocks agree, can help.
    """


class SourceGone(SourceError):
    """The Source no longer exists, or is disabled, so there is nothing to sync.

    Trying again cannot help until someone registers or enables it.
    """


class SourceMisconfigured(SourceError):
    """The Source's stored configuration cannot be opened, such as a kind no
    adapter reads yet or a folder that does not exist.

    Trying again cannot help until someone fixes the configuration.
    """
```

**Дія 3:** заміни `SourcePage`:

```python
@dataclass(frozen=True, slots=True)
class SourcePage:
    """One page of records plus the checkpoint that resumes after it.

    A Source with a change feed gives a `cursor` on the last page only: its
    token for "changes after everything this pass read". It must not miss a
    change made while the pass was reading, so a Source usually takes it
    from the moment the pass began.
    """

    records: tuple[SourceRecord, ...]
    next_checkpoint: str | None
    cursor: str | None = None

    def __post_init__(self) -> None:
        if self.next_checkpoint is not None and not self.next_checkpoint.strip():
            raise InvalidSource("a next checkpoint must not be empty when provided")
        if self.cursor is not None:
            if not self.cursor.strip():
                raise InvalidSource("a cursor must not be empty when provided")
            if self.next_checkpoint is not None:
                raise InvalidSource("only the last page may carry a cursor")
```

**Дія 4:** додай `ChangeFeed` і `SyncMode` просто перед `RunStatus`:

```python
@runtime_checkable
class ChangeFeed(Protocol):
    """A Source that can list only what changed after a cursor it gave.

    Its pages hold changed items: present ones were created or updated,
    deleted ones were removed. The last page carries the next cursor. A
    Source without this protocol is always read in full.
    """

    async def fetch_changes(
        self,
        *,
        since: str,
        checkpoint: str | None,
        page_size: int,
    ) -> SourcePage:
        """Return the page of changes after `since`, resuming at `checkpoint`."""
        ...


class SyncMode(StrEnum):
    """How a pass reads its Source."""

    # Every item; what the pass did not see is tombstoned at the end.
    FULL = "full"
    # Only what changed after the stored cursor; only items the Source
    # reported deleted are tombstoned.
    INCREMENTAL = "incremental"
```

**Дія 5:** у `src/knowledge_service/job_failures.py` розшир імпорт з `ingestion.sources`:

```python
from knowledge_service.ingestion.sources import (
    ObservedInTheFuture,
    SourceGone,
    SourceMisconfigured,
    SourceUnauthorized,
    SourceUnavailable,
    SynchronizationSuperseded,
)
```

Додай три коди до `JobFailure` одразу після `SOURCE_REFUSED`:

```python
    # A clock that ran ahead stamped the Job; it may pass once clocks agree.
    CLOCK_AHEAD = "clock_ahead"
    # The Source is missing or disabled.
    SOURCE_GONE = "source_gone"
    # The Source's stored configuration cannot be opened.
    SOURCE_MISCONFIGURED = "source_misconfigured"
```

Додай `JobFailure.CLOCK_AHEAD` до `_TRANSIENT` і заміни `classify_job_failure`:

```python
_TRANSIENT: Final = frozenset(
    {
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.CLOCK_AHEAD,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
        JobFailure.WORKER_LOST,
        JobFailure.DELIVERY_LOST,
    }
)
```

```python
def classify_job_failure(error: Exception) -> JobFailure:
    """The failure an attempt's error stands for; unknown errors are bugs."""
    if isinstance(error, SynchronizationSuperseded):
        return JobFailure.SUPERSEDED
    if isinstance(error, ObservedInTheFuture):
        return JobFailure.CLOCK_AHEAD
    if isinstance(error, SourceGone):
        return JobFailure.SOURCE_GONE
    if isinstance(error, SourceMisconfigured):
        return JobFailure.SOURCE_MISCONFIGURED
    if isinstance(error, SourceUnavailable):
        return JobFailure.SOURCE_UNAVAILABLE
    if isinstance(error, SourceUnauthorized):
        return JobFailure.SOURCE_REFUSED
    if database_unavailable(error):
        return JobFailure.DATABASE_UNAVAILABLE
    if isinstance(error, database_errors.DBAPIError):
        # The server answered and said no, such as a broken constraint.
        return JobFailure.DATABASE_REJECTED
    if isinstance(error, _MODEL_REFUSALS):
        return JobFailure.MODEL_REFUSED
    if isinstance(error, (ChatModelError, EmbeddingError)):
        return JobFailure.MODEL_UNAVAILABLE
    return JobFailure.PROGRAMMING_ERROR
```

**Перевірка:** `uv run pyright src/knowledge_service/ingestion/sources.py src/knowledge_service/job_failures.py` показує `0 errors`.

### Крок 4 — Крок із кодом: збережений прогрес із режимами

Цей крок переписує `persistence/synchronization_progress.py`, щоб прохід знав свій режим, і додає завантаження одного рядка джерела.

**Простими словами:** коли прохід починається, цей код вирішує, повний він чи інкрементальний, за правилом із блоку 5. Поки прохід іде, він зберігає останнє повідомлення про кожен елемент. Коли прохід закінчується, він зберігає новий курсор джерела, а для повного проходу — ще й час завершення. Він уміє перелічити або Document, які жодного разу не запропонували (повний прохід), або ті, про які сказано "видалено" (інкрементальний). Друга невелика функція читає один рядок джерела простими значеннями для воркера.

| Функція | Що змінилось від M08-T09 |
|---|---|
| `start_or_resume_pass` | нові аргументи `follows_changes`, `full_interval`; вирішує й зберігає режим; не продовжує інкрементальний прохід без стрічки |
| `PassProgress` | нові поля `incremental` і `since` (курсор, після якого читає інкрементальний прохід) |
| `record_reports` | замінює `record_seen` і `forget_seen`: upsert останнього повідомлення про кожен ID |
| `end_pass` | також зберігає курсор, а для повного проходу — `last_full_pass_at` |
| `load_unseen_available` | "жодного разу не запропонований як наявний" (блок 4) |
| `load_reported_deleted_available` | нова: "останнє повідомлення — видалено" |
| `load_source_row` у `persistence/sources.py` | нова: один рядок джерела як `SourceRow` |

**Рішення:** шар збереження ніколи не імпортує з `ingestion` — цього правила дотримується кожен модуль у `persistence/`. Тож він говорить простим прапорцем `incremental`, а крок 5 перетворює його на `SyncMode`. `SourceRow.default_principals` має тип `tuple[str | None, ...]`, бо масив PostgreSQL може містити NULL; крок 7 такому відмовляє (блок 7).

**Дія 1:** заміни весь `src/knowledge_service/persistence/synchronization_progress.py` на:

```python
"""Statements over the stored progress of a Source's synchronization pass.

The progress is five facts: when the pass in progress started
(`sources.pass_started_at`), whether it is incremental
(`sources.pass_mode`), the checkpoint it resumes from (`sources.checkpoint`),
every checkpoint it has stored (`synchronization_checkpoints`), and each
external ID it has met so far with its last report, present or deleted
(`synchronization_seen`). Between passes a Source keeps its change cursor
(`sources.sync_cursor`) and when its last full pass finished
(`sources.last_full_pass_at`). `advance_checkpoint` and `end_pass` check, in
the same statement, that the pass still holds the Source's fence, and lock
the Source's row; the other writes run in the transaction that checked it.
"""

from collections.abc import Collection, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import batched

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.documents import DocumentAvailability
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.persistence.schema import (
    documents,
    sources,
    synchronization_checkpoints,
    synchronization_seen,
)

# PostgreSQL takes at most 65,535 parameters in one statement; a page may
# hold more IDs than that, so they are written this many at a time.
BATCH_SIZE = 1000

_FULL = "full"
_INCREMENTAL = "incremental"


@dataclass(frozen=True, slots=True)
class PassProgress:
    """Where a pass starts, and how it reads its Source.

    `since` is the change cursor an incremental pass reads after; None for
    a full pass.
    """

    started_at: datetime
    checkpoint: str | None
    resumed: bool
    incremental: bool
    since: str | None


async def start_or_resume_pass(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    observed_at: datetime,
    max_age: timedelta,
    follows_changes: bool,
    full_interval: timedelta,
) -> PassProgress:
    """Resume the stored pass if it started at most `max_age` ago, or start anew.

    A new pass is incremental only when the Source `follows_changes`, has a
    stored cursor, and its last full pass finished less than `full_interval`
    before `observed_at`; otherwise it is full. A resumed pass keeps the mode
    it started with, unless it is incremental and the Source no longer
    follows changes: then a full pass starts. Call it in the transaction that
    claimed the fence, so no older pass can store progress between the claim
    and this read. Starting anew forgets everything an earlier pass stored.
    """
    row = (
        await connection.execute(
            sa.select(
                sources.c.checkpoint,
                sources.c.pass_started_at,
                sources.c.pass_mode,
                sources.c.sync_cursor,
                sources.c.last_full_pass_at,
            )
            .where(sources.c.source_id == source_id.value)
            .with_for_update()
        )
    ).one()
    started_at: datetime | None = row.pass_started_at
    cursor: str | None = row.sync_cursor
    stored_incremental = row.pass_mode == _INCREMENTAL
    if (
        started_at is not None
        and started_at <= observed_at <= started_at + max_age
        # An incremental pass needs a change feed; without one, start over.
        and (follows_changes or not stored_incremental)
    ):
        incremental = stored_incremental
        return PassProgress(
            started_at=started_at,
            checkpoint=row.checkpoint,
            resumed=True,
            incremental=incremental,
            since=cursor if incremental else None,
        )
    last_full: datetime | None = row.last_full_pass_at
    incremental = (
        follows_changes
        and cursor is not None
        and last_full is not None
        and last_full <= observed_at < last_full + full_interval
    )
    await forget_pass(connection, source_id=source_id)
    await connection.execute(
        sa.update(sources)
        .where(sources.c.source_id == source_id.value)
        .values(
            pass_started_at=observed_at,
            pass_mode=_INCREMENTAL if incremental else _FULL,
            checkpoint=None,
        )
    )
    return PassProgress(
        started_at=observed_at,
        checkpoint=None,
        resumed=False,
        incremental=incremental,
        since=cursor if incremental else None,
    )


async def advance_checkpoint(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    observed_at: datetime,
    checkpoint: str | None,
) -> bool:
    """Store `checkpoint` as the resume point, if this pass holds the fence.

    None keeps the stored checkpoint: after the last page the pass resumes
    from that page, not past it. False, and nothing written, when a newer
    pass claimed the fence or no pass is in progress. The Source's row stays
    locked until the transaction ends, so no newer claim lands in between.
    """
    resume_from = (
        sources.c.checkpoint
        if checkpoint is None
        else sa.literal(checkpoint, sa.Text())
    )
    held = await connection.scalar(
        sa.update(sources)
        .where(
            sources.c.source_id == source_id.value,
            sources.c.synchronization_fence == observed_at,
            sources.c.pass_started_at.is_not(None),
        )
        .values(checkpoint=resume_from)
        .returning(sources.c.source_id)
    )
    return held is not None


async def remember_checkpoint(
    connection: AsyncConnection, *, source_id: SourceId, checkpoint: str
) -> bool:
    """Note that the pass stored `checkpoint`; False if it stored it before."""
    added = await connection.scalar(
        insert(synchronization_checkpoints)
        .values(source_id=source_id.value, checkpoint=checkpoint)
        .on_conflict_do_nothing()
        .returning(synchronization_checkpoints.c.checkpoint)
    )
    return added is not None


async def end_pass(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    observed_at: datetime,
    cursor: str | None,
    full: bool,
) -> bool:
    """Clear the pass's resume point and keep `cursor`, if it holds the fence.

    A full pass also records `observed_at` as the last full pass. The seen
    IDs stay until `forget_pass`, so the same transaction can still tombstone
    what the pass found gone.
    """
    values: dict[str, object] = {
        "checkpoint": None,
        "pass_started_at": None,
        "pass_mode": None,
        "sync_cursor": cursor,
    }
    if full:
        values["last_full_pass_at"] = observed_at
    held = await connection.scalar(
        sa.update(sources)
        .where(
            sources.c.source_id == source_id.value,
            sources.c.synchronization_fence == observed_at,
            sources.c.pass_started_at.is_not(None),
        )
        .values(values)
        .returning(sources.c.source_id)
    )
    return held is not None


async def record_reports(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    offered: Collection[str],
    deleted: Collection[str],
) -> None:
    """Store each external ID's last report in the pass: offered or deleted.

    A later report of the same ID replaces an earlier one.
    """
    reports = dict.fromkeys(offered, False)
    reports.update(dict.fromkeys(deleted, True))
    for batch in _batches(reports):
        statement = insert(synchronization_seen).values(
            [
                {
                    "source_id": source_id.value,
                    "external_id": external_id,
                    "deleted": reports[external_id],
                }
                for external_id in batch
            ]
        )
        await connection.execute(
            statement.on_conflict_do_update(
                index_elements=[
                    synchronization_seen.c.source_id,
                    synchronization_seen.c.external_id,
                ],
                set_={"deleted": statement.excluded.deleted},
            )
        )


async def forget_pass(connection: AsyncConnection, *, source_id: SourceId) -> None:
    """Forget every external ID and checkpoint the pass stored."""
    for table in (synchronization_seen, synchronization_checkpoints):
        await connection.execute(
            sa.delete(table).where(table.c.source_id == source_id.value)
        )


async def load_unseen_available(
    connection: AsyncConnection, *, source_id: SourceId
) -> tuple[DocumentId, ...]:
    """Available Documents the pass never saw, or last saw reported deleted."""
    offered = sa.exists().where(
        synchronization_seen.c.source_id == documents.c.source_id,
        synchronization_seen.c.external_id == documents.c.external_id,
        synchronization_seen.c.deleted.is_(False),
    )
    return await _available(connection, source_id=source_id, condition=~offered)


async def load_reported_deleted_available(
    connection: AsyncConnection, *, source_id: SourceId
) -> tuple[DocumentId, ...]:
    """Available Documents whose last report in the pass said deleted."""
    deleted = sa.exists().where(
        synchronization_seen.c.source_id == documents.c.source_id,
        synchronization_seen.c.external_id == documents.c.external_id,
        synchronization_seen.c.deleted.is_(True),
    )
    return await _available(connection, source_id=source_id, condition=deleted)


async def _available(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    condition: sa.ColumnElement[bool],
) -> tuple[DocumentId, ...]:
    rows = await connection.scalars(
        sa.select(documents.c.document_id)
        .where(
            documents.c.source_id == source_id.value,
            documents.c.availability == DocumentAvailability.AVAILABLE.value,
            condition,
        )
        .order_by(documents.c.external_id)
    )
    return tuple(DocumentId(document_id) for document_id in rows)


def _batches(external_ids: Collection[str]) -> Iterator[Sequence[str]]:
    """The IDs in sorted batches, so concurrent writers lock rows in one order."""
    return batched(sorted(external_ids), BATCH_SIZE, strict=False)
```

`on_conflict_do_update(... set_={"deleted": statement.excluded.deleted})` — це PostgreSQL `ON CONFLICT DO UPDATE SET deleted = EXCLUDED.deleted`. Коли ID уже збережено, його повідомлення замінюється новим.

**Дія 2:** у `src/knowledge_service/persistence/sources.py` додай до імпортів `from dataclasses import dataclass` і встав це перед `source_runs_statement`:

```python
@dataclass(frozen=True, slots=True)
class SourceRow:
    """A stored Source as a synchronization Job needs it, as plain values."""

    source_id: SourceId
    kind: str
    location: str
    credentials_reference: str | None
    enabled: bool
    default_public: bool
    # PostgreSQL arrays may hold NULL elements; the caller decides.
    default_principals: tuple[str | None, ...]


async def load_source_row(
    connection: AsyncConnection, *, source_id: SourceId
) -> SourceRow | None:
    """Read one stored Source, if it exists."""
    row = (
        await connection.execute(
            sa.select(
                sources.c.kind,
                sources.c.location,
                sources.c.credentials_reference,
                sources.c.enabled,
                sources.c.default_public,
                sources.c.default_principals,
            ).where(sources.c.source_id == source_id.value)
        )
    ).one_or_none()
    if row is None:
        return None
    return SourceRow(
        source_id=source_id,
        kind=row.kind,
        location=row.location,
        credentials_reference=row.credentials_reference,
        enabled=row.enabled,
        default_public=row.default_public,
        default_principals=tuple(row.default_principals),
    )
```

**Перевірка:** `uv run pyright src/knowledge_service/persistence` показує `0 errors`. Виклики в `ingestion` ще використовують старі імена, тож перевірка типів усього проєкту падає до кроку 5.

### Крок 5 — Крок із кодом: tombstone і транзакція checkpoint за режимом

Цей крок робить завершення проходу залежним від режиму й дає `begin_pass` обирати режим.

**Простими словами:** "почати" тепер передає далі, чи є в джерела стрічка змін і скільки може чекати повний прохід. "Зафіксувати сторінку" зберігає останнє повідомлення про кожен елемент однією командою. "Завершити" ховає жодного разу не запропоновані Document після повного проходу або лише ті, про які сказано "видалено", після інкрементального. Потім воно зберігає новий курсор джерела.

**Рішення:** `finish_pass` приймає `mode` і `cursor` зі значеннями за замовчуванням `SyncMode.FULL` і `None`. Тож кожен виклик і тест із M08-T09 зберігає свій сенс.

**Дія 1:** у `src/knowledge_service/ingestion/deletions.py` заміни пункт 1 docstring модуля на:

```text
1. At the end of a Synchronization Run, a full run tombstones every
   available Document it did not see, or last saw reported deleted
   (`tombstone_unseen` reads the reports the run stored page by page;
   `tombstone_missing` takes the seen IDs as a set in memory, for callers
   that hold a complete pass themselves). An incremental run sees only
   changes, so it tombstones only what the Source reported deleted
   (`tombstone_reported_deleted`). For each such Document, one transaction marks
   it tombstoned, records an audit event, and queues a purge Job. Retrieval
   reads only Chunks of available Documents, so the content disappears from
   reads at that commit, while the Chunks are still stored.
```

Розшир імпорт із `synchronization_progress`:

```python
from knowledge_service.persistence.synchronization_progress import (
    load_reported_deleted_available,
    load_unseen_available,
)
```

Потім заміни `tombstone_unseen` цими двома функціями:

```python
async def tombstone_unseen(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> tuple[Tombstone, ...]:
    """Tombstone the available Documents the stored full pass never saw.

    A Document counts as seen when its last stored report said present. The
    reports are the ones every committed page of the pass stored, so a pass
    resumed after a crash still counts the pages before it. The caller
    owns the transaction and has checked its fence.
    """
    tombstones: list[Tombstone] = []
    for document_id in await load_unseen_available(connection, source_id=source_id):
        tombstone = await _tombstone(
            connection, document_id=document_id, observed_at=observed_at
        )
        if tombstone is not None:
            tombstones.append(tombstone)
    return tuple(tombstones)


async def tombstone_reported_deleted(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> tuple[Tombstone, ...]:
    """Tombstone the available Documents whose last report said deleted.

    An incremental pass uses this: it sees only changes, so absence means
    nothing. The caller owns the transaction and has checked its fence.
    """
    tombstones: list[Tombstone] = []
    for document_id in await load_reported_deleted_available(
        connection, source_id=source_id
    ):
        tombstone = await _tombstone(
            connection, document_id=document_id, observed_at=observed_at
        )
        if tombstone is not None:
            tombstones.append(tombstone)
    return tuple(tombstones)
```

**Дія 2:** заміни весь `src/knowledge_service/ingestion/checkpoints.py` на:

```python
"""Make a synchronization pass resumable, one committed page at a time.

A pass stores its progress in PostgreSQL: when it started, whether it is
full or incremental, the checkpoint it resumes from, every checkpoint it
stored, and the last report of each external ID it met. Each step is one
transaction that first checks the pass still holds the Source's fence:

1. `begin_pass` claims the fence and resumes the stored pass, if it started
   at most `MAX_RESUME_AGE` ago, or starts a new one. A new pass is
   incremental when the Source follows changes, has a stored cursor, and
   had a full pass within `FULL_PASS_INTERVAL`; otherwise it is full.
2. `commit_page` runs after every record of a page is reconciled. It
   stores each external ID's last report on the page, offered or deleted,
   and only then moves the checkpoint past the page. A crash before this
   commit replays the page, which reconciliation tolerates: an unchanged
   record writes nothing.
3. `finish_pass` runs after the last page. A full pass tombstones every
   available Document no attempt saw offered; an incremental pass, which
   sees only changes, tombstones only what the Source reported deleted.
   Either way it keeps the Source's new change cursor and clears the
   progress, so the next pass starts from the beginning.

Nothing is tombstoned before the last page: an item reported deleted on one
page may be offered again on a later one, and the last report wins.

A resumed pass does not read the pages before its checkpoint again, so a
change the Source made there waits for the next pass. `MAX_RESUME_AGE`
bounds that wait. An incremental pass trusts the Source to report every
change; the periodic full pass catches what it did not.
"""

from collections.abc import Collection
from datetime import datetime, timedelta
from typing import Final

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.deletions import (
    Tombstone,
    tombstone_reported_deleted,
    tombstone_unseen,
)
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SynchronizationSuperseded,
    SyncMode,
)
from knowledge_service.persistence.sources import claim_synchronization_fence
from knowledge_service.persistence.synchronization_progress import (
    PassProgress,
    advance_checkpoint,
    end_pass,
    forget_pass,
    record_reports,
    remember_checkpoint,
    start_or_resume_pass,
)

# Older progress is dropped and the pass starts again from the beginning, so
# a page skipped by a resume is read again within this time.
MAX_RESUME_AGE: Final = timedelta(hours=1)
# How long a Source with a change feed may go without a full pass, which
# catches what its incremental passes missed.
FULL_PASS_INTERVAL: Final = timedelta(hours=24)


def mode_of(progress: PassProgress) -> SyncMode:
    """The mode a stored pass reads its Source in."""
    return SyncMode.INCREMENTAL if progress.incremental else SyncMode.FULL


async def begin_pass(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    observed_at: datetime,
    follows_changes: bool = False,
    max_resume_age: timedelta = MAX_RESUME_AGE,
    full_pass_interval: timedelta = FULL_PASS_INTERVAL,
) -> PassProgress:
    """Claim the fence, then resume the stored pass or start a new one.

    Raises `SynchronizationSuperseded` when a pass that observed the Source
    at the same time or later already claimed it.
    """
    async with runtime.transaction() as connection:
        if not await claim_synchronization_fence(
            connection, source_id=source_id, observed_at=observed_at
        ):
            raise SynchronizationSuperseded("a newer synchronization pass started")
        return await start_or_resume_pass(
            connection,
            source_id=source_id,
            observed_at=observed_at,
            max_age=max_resume_age,
            follows_changes=follows_changes,
            full_interval=full_pass_interval,
        )


async def commit_page(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    observed_at: datetime,
    offered: Collection[str],
    deleted: Collection[str],
    next_checkpoint: str | None,
) -> None:
    """Record one reconciled page and move the checkpoint past it, together.

    `offered` and `deleted` are the page's external IDs by their last report.
    With `next_checkpoint` None, the page was the last one and the checkpoint
    stays. Writes nothing and raises `SynchronizationSuperseded` when a newer
    pass holds the fence, or `InvalidSource` when the pass stored
    `next_checkpoint` before.
    """
    async with runtime.transaction() as connection:
        # First, so the Source's row stays locked for the whole commit.
        if not await advance_checkpoint(
            connection,
            source_id=source_id,
            observed_at=observed_at,
            checkpoint=next_checkpoint,
        ):
            raise SynchronizationSuperseded("a newer synchronization pass started")
        if next_checkpoint is not None and not await remember_checkpoint(
            connection, source_id=source_id, checkpoint=next_checkpoint
        ):
            # Raising rolls the whole commit back, so it is never stored.
            raise InvalidSource("the source repeated a checkpoint")
        await record_reports(
            connection, source_id=source_id, offered=offered, deleted=deleted
        )


async def finish_pass(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    observed_at: datetime,
    mode: SyncMode = SyncMode.FULL,
    cursor: str | None = None,
) -> tuple[Tombstone, ...]:
    """Tombstone what the pass found gone, keep `cursor`, clear the progress.

    `cursor` is the Source's token for the next incremental pass, from the
    last page; None when the Source has no change feed. Raises
    `SynchronizationSuperseded`, writing nothing, when a newer pass holds
    the fence.
    """
    full = mode is SyncMode.FULL
    async with runtime.transaction() as connection:
        if not await end_pass(
            connection,
            source_id=source_id,
            observed_at=observed_at,
            cursor=cursor,
            full=full,
        ):
            raise SynchronizationSuperseded("a newer synchronization pass started")
        if full:
            tombstones = await tombstone_unseen(
                connection, source_id=source_id, observed_at=observed_at
            )
        else:
            tombstones = await tombstone_reported_deleted(
                connection, source_id=source_id, observed_at=observed_at
            )
        await forget_pass(connection, source_id=source_id)
    return tombstones
```

**Перевірка:** `uv run pyright src/knowledge_service/ingestion/checkpoints.py src/knowledge_service/ingestion/deletions.py` показує `0 errors`.

### Крок 6 — Крок із кодом: прохід читає за режимом і звітує про кожну сторінку

Цей крок учить `synchronize_source` читати зміни для інкрементального проходу, зберігати курсор останньої сторінки й звітувати про накопичені підсумки.

**Простими словами:** прохід питає "почати" ще про одну річ — чи є в джерела стрічка змін. Потім він читає сторінки або як "кожен елемент", або як "зміни після курсора". Після кожної зафіксованої сторінки він віддає накопичені підсумки тому, хто про них просив. Це обробник Job із кроку 7, який перетворює їх на звіт про прогрес. Наприкінці він перевіряє, що інкрементальний прохід отримав новий курсор, і передає режим і курсор до "завершити".

| Зміна | Де |
|---|---|
| `PassTotals`: оброблено, змінено, у карантині, невідомі читачі | новий dataclass |
| параметри `on_page`, `full_pass_interval` | `synchronize_source`, `synchronize_source_exclusively` |
| `follows_changes=isinstance(source, ChangeFeed)` | `_claim_fence` |
| `ObservedInTheFuture` для часу надто далеко попереду | `_claim_fence` |
| `_fetch`: `fetch_page` або `fetch_changes` | `_publish` |
| `on_page` після кожної фіксації; перевірка курсора; `finish_pass(mode, cursor)` | `_publish` |
| `mode` у підсумку й події аудиту | `SynchronizationSummary`, `synchronize_source` |

**Рішення:** `on_page` викликається перед heartbeat. Звіт, що падає, бо відновлення вже завершило цю спробу, зупиняє прохід до продовження оренди. Підсумки рахують лише цю спробу, як і підсумок проходу.

Усі дії нижче — у `src/knowledge_service/ingestion/synchronization.py`, якщо не сказано інакше.

**Дія 1:** у docstring модуля додай цей абзац перед абзацом, що починається з "The pass is resumable":

```text
A pass is full or incremental (`ingestion.checkpoints` decides). A full
pass reads every page with `fetch_page` and, at the end, tombstones every
available Document it did not see. An incremental pass, for a Source that
follows changes (`ChangeFeed`), reads only what changed after the stored
cursor with `fetch_changes`, and tombstones only what the Source reported
deleted. The last page's cursor is kept for the next incremental pass.
```

Заміни абзац, що починається з "A caller may pass a `heartbeat`", на:

```text
A caller may pass `on_page`, awaited after every committed page with the
running totals of this attempt, to report progress, and a `heartbeat`,
awaited after it and so also just before the tombstones. Raising from either
stops the pass there, as a failure: nothing is tombstoned.
`synchronize_source_exclusively` uses the heartbeat to keep its lease.
```

**Дія 2:** заміни імпорти `checkpoints` і `sources` на:

```python
from knowledge_service.ingestion.checkpoints import (
    FULL_PASS_INTERVAL,
    MAX_RESUME_AGE,
    begin_pass,
    commit_page,
    finish_pass,
    mode_of,
)
```

```python
from knowledge_service.ingestion.sources import (
    ChangeFeed,
    InvalidSource,
    ObservedInTheFuture,
    SourceMisconfigured,
    SourcePage,
    SourcePort,
    SourceRecord,
    SourceRecordState,
    SynchronizationSuperseded,
    SyncMode,
)
```

**Дія 3:** додай `PassTotals` і поле `mode`. Заміни `SynchronizationSummary` обома класами:

```python
@dataclass(frozen=True, slots=True)
class PassTotals:
    """How much this attempt of a pass has done so far; each total only grows."""

    # Records handled: reconciled or quarantined.
    processed: int
    # Records whose content, readers, or metadata changed.
    changed: int
    quarantined: int
    # Documents whose Source named readers the directory lacks.
    unresolved: int


@dataclass(frozen=True, slots=True)
class SynchronizationSummary:
    """What one complete pass did."""

    run_id: SynchronizationRunId
    changes: Counter[Change]
    quarantined: tuple[Quarantined, ...]
    tombstones: tuple[Tombstone, ...]
    requeued_purges: tuple[JobId, ...]
    unresolved: tuple[UnresolvedAccess, ...]
    # Whether this attempt resumed a pass from its stored checkpoint; the
    # counts above cover only the pages this attempt read.
    resumed: bool
    mode: SyncMode
```

**Дія 4:** заміни `synchronize_source` і `_claim_fence`:

```python
async def synchronize_source(
    runtime: DatabaseRuntime,
    source: SourcePort,
    *,
    source_id: SourceId,
    observed_at: datetime,
    principal_directory: PrincipalDirectory | None,
    page_size: int = PAGE_SIZE,
    run_id: SynchronizationRunId | None = None,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
    on_page: Callable[[PassTotals], Awaitable[None]] | None = None,
    max_resume_age: timedelta = MAX_RESUME_AGE,
    full_pass_interval: timedelta = FULL_PASS_INTERVAL,
) -> SynchronizationSummary:
    """Publish what the Source offers or changed, then tombstone what is gone.

    Readers come from `principal_directory`, which every caller names: with
    None, only public items are readable. A Source that returns a checkpoint
    it returned before raises `InvalidSource` instead of paging forever.
    The pass runs as `run_id`, or as a new run, and records how it ended.
    It resumes the stored pass when that started at most `max_resume_age`
    before `observed_at`; a new pass is incremental when the Source is a
    `ChangeFeed` with a stored cursor and a full pass within
    `full_pass_interval`. Raises `SynchronizationSuperseded`, before reading
    and recording anything, when a pass with the same or a later
    `observed_at` started.
    """
    run = SynchronizationRunId.new() if run_id is None else run_id
    progress = await _claim_fence(
        runtime,
        source_id=source_id,
        observed_at=observed_at,
        follows_changes=isinstance(source, ChangeFeed),
        max_resume_age=max_resume_age,
        full_pass_interval=full_pass_interval,
    )

    async def guard(connection: AsyncConnection) -> None:
        if not await synchronization_fence_holds(
            connection, source_id=source_id, observed_at=observed_at
        ):
            raise SynchronizationSuperseded("a newer synchronization pass started")

    try:
        summary = await _publish(
            runtime,
            source,
            source_id=source_id,
            observed_at=observed_at,
            principal_directory=principal_directory,
            page_size=page_size,
            run_id=run,
            heartbeat=heartbeat,
            on_page=on_page,
            guard=guard,
            progress=progress,
        )
    except Exception as error:
        # The pass's own error is raised either way; its record holds only
        # its type. A database that cannot take the record is logged.
        try:
            await _record_finished(
                runtime,
                source_id=source_id,
                run_id=run,
                observed_at=observed_at,
                outcome=AuditOutcome.FAILED,
                metadata={"failure": type(error).__name__},
            )
        except Exception as unrecorded:
            # Identifiers and type names only, as the other warnings here.
            logger.warning(
                "failed synchronization run %s of source %s not audited: %s",
                str(run.value),
                str(source_id.value),
                type(unrecorded).__name__,
            )
        raise
    await _record_finished(
        runtime,
        source_id=source_id,
        run_id=run,
        observed_at=observed_at,
        outcome=AuditOutcome.SUCCEEDED,
        metadata={
            "changed": sum(
                count
                for change, count in summary.changes.items()
                if change is not Change.UNCHANGED
            ),
            "unchanged": summary.changes[Change.UNCHANGED],
            "quarantined": len(summary.quarantined),
            "tombstoned": len(summary.tombstones),
            "unresolved": len(summary.unresolved),
            "resumed": summary.resumed,
            "mode": summary.mode.value,
        },
    )
    return summary
```

```python
async def _claim_fence(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    observed_at: datetime,
    follows_changes: bool,
    max_resume_age: timedelta,
    full_pass_interval: timedelta,
) -> PassProgress:
    if observed_at.utcoffset() != timedelta(0):
        raise InvalidSource("observed_at must be a UTC-aware timestamp")
    if observed_at > datetime.now(UTC) + MAX_CLOCK_SKEW:
        raise ObservedInTheFuture("observed_at is too far in the future to fence on")
    return await begin_pass(
        runtime,
        source_id=source_id,
        observed_at=observed_at,
        follows_changes=follows_changes,
        max_resume_age=max_resume_age,
        full_pass_interval=full_pass_interval,
    )
```

**Дія 5:** заміни `_publish` і додай після неї `_fetch` і `_totals`:

```python
async def _publish(
    runtime: DatabaseRuntime,
    source: SourcePort,
    *,
    source_id: SourceId,
    observed_at: datetime,
    principal_directory: PrincipalDirectory | None,
    page_size: int,
    run_id: SynchronizationRunId,
    heartbeat: Callable[[], Awaitable[None]] | None,
    on_page: Callable[[PassTotals], Awaitable[None]] | None,
    guard: WriteGuard,
    progress: PassProgress,
) -> SynchronizationSummary:
    """Every page from the stored checkpoint, then the tombstones and purges."""
    mode = mode_of(progress)
    checkpoint = progress.checkpoint
    changes: Counter[Change] = Counter()
    quarantined: list[Quarantined] = []
    # By external ID, so a record offered twice is reported as last seen.
    unresolved: dict[str, UnresolvedAccess] = {}
    while True:
        page = await _fetch(
            source, progress, checkpoint=checkpoint, page_size=page_size
        )
        # By external ID, so a record offered twice in a page counts once.
        planned: dict[str, tuple[SourceRecord, NormalizedAccess]] = {}
        # What the page reports deleted, by each item's last report.
        deleted: set[str] = set()
        for record in page.records:
            # The last report of an item in the page wins.
            unresolved.pop(record.external_id, None)
            planned.pop(record.external_id, None)
            if record.state is SourceRecordState.DELETED or record.access is None:
                deleted.add(record.external_id)
                continue
            deleted.discard(record.external_id)
            access = normalize_access(record.access, principal_directory)
            if access.unresolved:
                unresolved[record.external_id] = UnresolvedAccess(
                    record.external_id, access.unresolved
                )
            planned[record.external_id] = (record, access)
        # Security first: every reader this page removes loses the Document
        # before any record of the page is parsed or chunked.
        narrowed = await _revoke_first(
            runtime,
            list(planned.values()),
            source_id=source_id,
            observed_at=observed_at,
            run_id=run_id,
            guard=guard,
        )
        for record, access in planned.values():
            outcome = normalize(record, source_id=source_id)
            if isinstance(outcome, Quarantined):
                # Kept as seen: a file that fails to parse is not a deleted
                # one; its narrowing above already applied.
                quarantined.append(outcome)
                if record.external_id in narrowed:
                    changes[Change.ACCESS] += 1
                continue
            result = await reconcile_document(
                runtime,
                outcome,
                readers=access.readers,
                observed_at=observed_at,
                run_id=run_id,
                guard=guard,
            )
            changes[
                _counted(result.change, narrowed=record.external_id in narrowed)
            ] += 1
        # Every record of the page is reconciled; only now may the
        # checkpoint move past it. Quarantined records count as offered. A
        # checkpoint the pass stored before is refused and never stored.
        await commit_page(
            runtime,
            source_id=source_id,
            observed_at=observed_at,
            offered=planned.keys(),
            deleted=deleted,
            next_checkpoint=page.next_checkpoint,
        )
        if on_page is not None:
            await on_page(_totals(changes, quarantined, unresolved))
        if heartbeat is not None:
            # After the last page this runs just before the tombstones.
            await heartbeat()
        if page.next_checkpoint is None:
            break
        checkpoint = page.next_checkpoint
    if mode is SyncMode.INCREMENTAL and page.cursor is None:
        # Without a new cursor the next pass could not continue from here.
        raise InvalidSource("an incremental pass ended without a cursor")
    tombstones = await finish_pass(
        runtime,
        source_id=source_id,
        observed_at=observed_at,
        mode=mode,
        cursor=page.cursor,
    )
    requeued = await requeue_unfinished_purges(
        runtime, source_id=source_id, now=observed_at
    )
    if unresolved:
        # Counts only: a principal may name a person.
        logger.warning(
            "unresolved source principals: %d in %d documents",
            sum(len(item.principals) for item in unresolved.values()),
            len(unresolved),
        )
    return SynchronizationSummary(
        run_id=run_id,
        changes=changes,
        quarantined=tuple(quarantined),
        tombstones=tombstones,
        requeued_purges=requeued,
        unresolved=tuple(unresolved.values()),
        resumed=progress.resumed,
        mode=mode,
    )


async def _fetch(
    source: SourcePort,
    progress: PassProgress,
    *,
    checkpoint: str | None,
    page_size: int,
) -> SourcePage:
    """The next page: of every item for a full pass, of changes otherwise."""
    if progress.since is None:
        return await source.fetch_page(checkpoint=checkpoint, page_size=page_size)
    if not isinstance(source, ChangeFeed):
        # `begin_pass` never makes a pass incremental for such a Source; this
        # only tells the type checker so.
        raise SourceMisconfigured("an incremental pass needs a change feed")
    return await source.fetch_changes(
        since=progress.since, checkpoint=checkpoint, page_size=page_size
    )


def _totals(
    changes: Counter[Change],
    quarantined: list[Quarantined],
    unresolved: dict[str, UnresolvedAccess],
) -> PassTotals:
    reconciled = sum(changes.values())
    return PassTotals(
        processed=reconciled + len(quarantined),
        changed=reconciled - changes[Change.UNCHANGED],
        quarantined=len(quarantined),
        unresolved=len(unresolved),
    )
```

**Дія 6:** у `src/knowledge_service/ingestion/exclusive_sync.py` заміни імпорти до `logger`:

```python
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Final

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import SourceId, SynchronizationRunId
from knowledge_service.ingestion.checkpoints import (
    FULL_PASS_INTERVAL,
    MAX_RESUME_AGE,
)
from knowledge_service.ingestion.grant_normalization import PrincipalDirectory
from knowledge_service.ingestion.sources import SourcePort, SourceUnavailable
from knowledge_service.ingestion.synchronization import (
    PAGE_SIZE,
    PassTotals,
    SynchronizationSummary,
    synchronize_source,
)
from knowledge_service.leases import LeaseStore, LeaseStoreUnavailable
```

Потім заміни `synchronize_source_exclusively`. Вона приймає три нові параметри й передає їх далі:

```python
async def synchronize_source_exclusively(
    runtime: DatabaseRuntime,
    source: SourcePort,
    *,
    leases: LeaseStore,
    source_id: SourceId,
    observed_at: datetime,
    principal_directory: PrincipalDirectory | None,
    page_size: int = PAGE_SIZE,
    run_id: SynchronizationRunId | None = None,
    ttl: timedelta = SYNC_LEASE_TTL,
    on_page: Callable[[PassTotals], Awaitable[None]] | None = None,
    max_resume_age: timedelta = MAX_RESUME_AGE,
    full_pass_interval: timedelta = FULL_PASS_INTERVAL,
) -> SynchronizationSummary:
    """`synchronize_source`, while holding the Source's lease.

    Raises `SourceBusy` before reading anything when another pass holds the
    lease, `SourceLeaseLost` when the lease is gone at a renewal, and
    `SourceCoordinationUnavailable` when the lease store does not answer.
    `synchronize_source`'s own errors, such as `SynchronizationSuperseded`,
    pass through.
    """
    try:
        lease = await leases.acquire(lease_name(source_id), ttl)
    except LeaseStoreUnavailable as error:
        raise SourceCoordinationUnavailable("cannot take the source lease") from error
    if lease is None:
        raise SourceBusy("another synchronization pass holds this source")

    async def keep_lease() -> None:
        try:
            held = await leases.renew(lease)
        except LeaseStoreUnavailable as error:
            raise SourceCoordinationUnavailable(
                "cannot renew the source lease"
            ) from error
        if not held:
            raise SourceLeaseLost("the source lease expired during the pass")

    try:
        return await synchronize_source(
            runtime,
            source,
            source_id=source_id,
            observed_at=observed_at,
            principal_directory=principal_directory,
            page_size=page_size,
            run_id=run_id,
            heartbeat=keep_lease,
            on_page=on_page,
            max_resume_age=max_resume_age,
            full_pass_interval=full_pass_interval,
        )
    finally:
        try:
            await leases.release(lease)
        except LeaseStoreUnavailable as error:
            # It expires on its own within its time to live.
            logger.warning(
                "source %s lease not released: %s",
                str(source_id.value),
                type(error).__name__,
            )
```

**Перевірка:** `uv run pyright src` показує `0 errors`.

### Крок 7 — Крок із кодом: обробник Job синхронізації

Цей крок пише глибокий модуль, якого просить завдання: одне місце, де Job `synchronize_source` стає проходом.

**Простими словами:** це те, що воркер запускає для Job синхронізації. Він захоплює Job і одразу фіксує, читає джерело, яке Job називає, і відкриває його. Він запускає прохід під орендою джерела, беручи за час спостереження початок спроби, і звітує про прогрес після кожної сторінки. Наостанок, якщо Job досі виконує цю саму спробу, він записує успіх або класифіковану відмову. Якщо щось падає, ті самі правила відмов, що й для кожного Job, вирішують, чи буде повтор.

| Частина | Робить | Блок |
|---|---|---|
| `SourceSettings`, `open_source` | збережене джерело як типізовані значення; відкриває локальну теку, поки відмовляє будь-якому іншому виду | |
| `SynchronizationJobs` | обробник із засобом відкриття, фабрикою оренди, довідником і годинником | |
| `__call__` | захопити, прочитати, відкрити, запустити з `on_page=report`, завершити | 1, 3, 6 |
| `_finish` | записати завершення лише поки Job виконує цю спробу | 2 |
| `_claim` | queued → running, рядок спроби збережено, зафіксовано | 1 |
| `_load_settings`, `_settings` | немає або вимкнено → `SourceGone`; погана конфігурація → `SourceMisconfigured` | 7 |
| `_counters`, `_warnings` | підсумки → лічильники й попередження спроби | 6 |

**Рішення:** обробник отримує залежності ззовні: як відкрити джерело, як зробити сховище оренд, довідник і годинник. Тести передають фейки; воркер — справжні (крок 8). `leases` — це фабрика, а не сховище. Клієнт Redis належить циклу подій, який його створив, а воркер запускає `asyncio.run` раз на повідомлення, тож кожному запуску потрібен свій клієнт.

**Дія:** створи `src/knowledge_service/ingestion/synchronization_jobs.py`:

```python
"""Run one stored synchronization Job: claim it, sync its Source, record how it ended.

This is the one place a `synchronize_source` Job becomes a pass. In order:

1. Claim the Job in a short transaction: it must still be queued; it moves
   to running and its attempt's progress row is stored. The claim commits
   at once, because a pass takes minutes and must not hold the Job's lock.
2. Read the Source the Job names. A Source that is gone or disabled, or
   whose configuration no adapter can open, fails the Job for good.
3. Run the pass under the Source's lease, observed at the attempt's start
   time, so every retry observes later and may claim the fence. After each
   committed page, report the attempt's totals: the report is also the
   attempt's sign of life for recovery (M08-T08).
4. Record the end in a new transaction, but only if the Job is still
   running this attempt. Recovery may have failed it meanwhile, and its
   decision stands: this delivery then reports the stored outcome.

A failure is classified as every Job's is (`job_failures`): a Source or
database that is unavailable, or a lost lease, is retried later from the
pass's checkpoint; anything else is final. A cancellation, or the actor's
time limit, ends the attempt without recording anything: the Job stays
running until recovery finds it silent, and its retry resumes the pass.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.ingestion.checkpoints import FULL_PASS_INTERVAL
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.exclusive_sync import synchronize_source_exclusively
from knowledge_service.ingestion.grant_normalization import PrincipalDirectory
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceConfiguration,
    SourceGone,
    SourceKind,
    SourceMisconfigured,
    SourcePort,
)
from knowledge_service.ingestion.synchronization import PAGE_SIZE, PassTotals
from knowledge_service.job_failures import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
    fail_attempt,
)
from knowledge_service.job_progress import (
    JobCounters,
    JobStage,
    JobWarning,
    attempt_number,
)
from knowledge_service.job_reporting import report_progress
from knowledge_service.jobs import (
    InvalidJob,
    Job,
    JobAlreadyClaimed,
    JobKind,
    JobStatus,
)
from knowledge_service.leases import LeaseStore
from knowledge_service.persistence.job_attempts import begin_attempt, finish_attempt
from knowledge_service.persistence.jobs import load_job, save_job
from knowledge_service.persistence.sources import SourceRow, load_source_row


@dataclass(frozen=True, slots=True)
class SourceSettings:
    """What a stored Source says about how to read it; never a secret."""

    source_id: SourceId
    configuration: SourceConfiguration
    # Who may read every item of a Source whose items carry no access of
    # their own, such as a local folder.
    default_access: SourceAccess


type SourceOpener = Callable[[SourceSettings], SourcePort]
type LeaseStoreFactory = Callable[[], AbstractAsyncContextManager[LeaseStore]]


def open_source(settings: SourceSettings) -> SourcePort:
    """The adapter that reads a stored Source; `SourceMisconfigured` if none can."""
    configuration = settings.configuration
    if configuration.kind is SourceKind.LOCAL_DIRECTORY:
        root = Path(configuration.location)
        if not root.is_absolute():
            raise SourceMisconfigured("a local directory must be an absolute path")
        # A folder that is missing now is unavailable, and retried, not wrong.
        return DirectorySource(root, access=settings.default_access)
    raise SourceMisconfigured(f"no adapter reads {configuration.kind} sources yet")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SynchronizationJobs:
    """The handler of `synchronize_source` Jobs, with what it needs to run one.

    `leases` makes a lease store for one run and closes it after, because a
    Redis client belongs to the event loop that made it.
    """

    open_source: SourceOpener
    leases: LeaseStoreFactory
    principal_directory: PrincipalDirectory | None
    full_pass_interval: timedelta = FULL_PASS_INTERVAL
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY
    page_size: int = PAGE_SIZE
    clock: Callable[[], datetime] = field(default=_now)

    async def __call__(
        self, runtime: DatabaseRuntime, *, job_id: JobId, now: datetime
    ) -> Job:
        """Run one queued synchronization Job and return it as stored after.

        Raises `JobAlreadyClaimed` when another delivery claimed it first,
        or when recovery ended this attempt before it could finish.
        """
        job = await _claim(runtime, job_id=job_id, now=now)
        attempt = attempt_number(job)
        observed_at = cast(datetime, job.started_at)
        totals = PassTotals(processed=0, changed=0, quarantined=0, unresolved=0)
        # A stored warning never goes away, even if a later report of the same
        # item resolves it, and a heartbeat never goes back, even if this
        # machine's clock steps back: either would be refused.
        warnings: set[JobWarning] = set()
        heartbeat = observed_at

        async def report(reached: PassTotals) -> None:
            nonlocal totals, heartbeat
            totals = reached
            warnings.update(_warnings(reached))
            heartbeat = max(self.clock(), heartbeat)
            await report_progress(
                runtime,
                job_id=job_id,
                attempt=attempt,
                stage=JobStage.WRITING,
                counters=_counters(reached),
                warnings=warnings,
                now=heartbeat,
            )

        try:
            source = self.open_source(
                await _load_settings(runtime, SourceId(job.target_id))
            )
            async with self.leases() as leases:
                await synchronize_source_exclusively(
                    runtime,
                    source,
                    leases=leases,
                    source_id=SourceId(job.target_id),
                    observed_at=observed_at,
                    principal_directory=self.principal_directory,
                    page_size=self.page_size,
                    on_page=report,
                    full_pass_interval=self.full_pass_interval,
                )
        except Exception as error:
            return await self._finish(
                runtime, job=job, attempt=attempt, totals=totals, error=error
            )
        return await self._finish(
            runtime, job=job, attempt=attempt, totals=totals, error=None
        )

    async def _finish(
        self,
        runtime: DatabaseRuntime,
        *,
        job: Job,
        attempt: int,
        totals: PassTotals,
        error: Exception | None,
    ) -> Job:
        """Store how the attempt ended, if the Job still runs this attempt."""
        finished_at = max(self.clock(), cast(datetime, job.started_at))
        async with runtime.transaction() as connection:
            stored = await load_job(connection, job_id=job.job_id, for_update=True)
            if stored is None:
                raise InvalidJob("the synchronization job vanished")
            if stored.status is not JobStatus.RUNNING or (
                attempt_number(stored) != attempt
            ):
                # Recovery ended this attempt while it ran; its outcome stands.
                raise JobAlreadyClaimed(
                    stored, "the attempt was ended before it could finish"
                )
            ended = (
                stored.succeed(finished_at=finished_at)
                if error is None
                else fail_attempt(
                    stored, error, now=finished_at, policy=self.retry_policy
                )
            )
            await finish_attempt(connection, job=ended, counters=_counters(totals))
            return await save_job(connection, job=ended)


async def _claim(runtime: DatabaseRuntime, *, job_id: JobId, now: datetime) -> Job:
    """Move a queued synchronization Job to running, and commit at once."""
    async with runtime.transaction() as connection:
        job = await load_job(connection, job_id=job_id, for_update=True)
        if job is None:
            raise InvalidJob("the synchronization job does not exist")
        if job.kind is not JobKind.SYNCHRONIZE_SOURCE:
            raise InvalidJob("the job is not a source synchronization")
        if job.status is not JobStatus.QUEUED:
            raise JobAlreadyClaimed(job)
        # A Job queued by a clock ahead of this one starts no earlier than
        # it was queued.
        started = job.start(started_at=max(now, job.queued_at))
        await save_job(connection, job=started)
        await begin_attempt(connection, job=started)
        return started


async def _load_settings(
    runtime: DatabaseRuntime, source_id: SourceId
) -> SourceSettings:
    async with runtime.transaction() as connection:
        row = await load_source_row(connection, source_id=source_id)
    if row is None or not row.enabled:
        # `jobs.target_id` has no foreign key: the Source may be long gone.
        raise SourceGone("the source is missing or disabled")
    return _settings(row)


def _settings(row: SourceRow) -> SourceSettings:
    principals = tuple(p for p in row.default_principals if p is not None)
    if len(principals) != len(row.default_principals):
        # The column allows a NULL element; no reader is named by one.
        raise SourceMisconfigured("a stored default principal is empty")
    try:
        kind = SourceKind(row.kind)
        return SourceSettings(
            source_id=row.source_id,
            configuration=SourceConfiguration(
                kind=kind,
                location=row.location,
                credentials_reference=row.credentials_reference,
            ),
            default_access=SourceAccess(
                public=row.default_public, principals=principals
            ),
        )
    except ValueError as error:
        raise SourceMisconfigured(
            "the stored source configuration is invalid"
        ) from error


def _counters(totals: PassTotals) -> JobCounters:
    return JobCounters(
        processed=totals.processed,
        changed=totals.changed,
        skipped=totals.quarantined,
    )


def _warnings(totals: PassTotals) -> frozenset[JobWarning]:
    warnings: set[JobWarning] = set()
    if totals.unresolved:
        warnings.add(JobWarning.UNRESOLVED_READERS)
    if totals.quarantined:
        warnings.add(JobWarning.QUARANTINED_ITEMS)
    return frozenset(warnings)
```

`observed_at = job.started_at` — це блок 3. Множина `warnings` і `heartbeat`, які лише ростуть, — блок 6. `except Exception` навколо проходу відправляє кожну звичайну помилку до `_finish`. Скасування проходить крізь нього недоторканим (блок 2).

**Перевірка:** `uv run pyright src/knowledge_service/ingestion/synchronization_jobs.py` показує `0 errors`.

### Крок 8 — Крок із кодом: підключи обробник до воркера

Цей крок змушує `make worker` запускати Job синхронізації, а `make worker-relay` — публікувати їх.

**Простими словами:** налаштування отримують два значення: як часто примусово робити повний прохід і де лежить файл довідника принципалів. Новий модуль будує з цих налаштувань кожен обробник, який запускає воркер. Він також перевіряє під час старту, що Redis налаштовано, а файл довідника читається. Черга синхронізації отримує ліміт у 30 хвилин. Relay публікує кожен вид, який воркер уміє виконувати, і тепер це включає синхронізації.

| Файл | Зміна |
|---|---|
| `settings.py` | `full_synchronization_interval_hours` (24), `principal_directory_file` |
| `worker/jobs.py` | `TIME_LIMIT_MS_FOR_QUEUE`, `RUNNABLE_KINDS`, `JobRunner.handlers` |
| `worker/handlers.py` | новий: `job_handlers`, `load_principal_directory`, `redis_leases` |
| `worker/main.py` | будує обробники й передає їх у `JobRunner` |
| `worker/relay.py` | публікує `RUNNABLE_KINDS` |

**Рішення:** воркер падає на старті, а не на першому Job, коли Redis чи файл довідника неправильні. Воркер, що стартує, а потім провалює кожну синхронізацію, виглядав би здоровим, нічого не роблячи. `RUNNABLE_KINDS` — константа, тож relay знає, які види публікувати, не будуючи обробників і не читаючи довідник.

**Дія 1:** у `src/knowledge_service/settings.py` додай `from pathlib import Path` і ці два поля після `synchronization_interval_minutes`:

```python
    # How long a Source that follows changes may go without a full pass.
    full_synchronization_interval_hours: int = Field(default=24, ge=1, le=30 * 24)
    # A JSON file mapping Source principals to identity-provider users and
    # groups (`ingestion.grant_normalization.PrincipalDirectory`). Without
    # it, only items a Source marks public are readable.
    principal_directory_file: Path | None = None
```

**Дія 2:** у `src/knowledge_service/worker/jobs.py` зміни імпорт на `from dataclasses import dataclass, field`. Потім додай ліміти часу після `JOB_TIME_LIMIT_MS`:

```python
# How long one message may run on each queue. A synchronization pass reads a
# whole Source; one cut off resumes from its checkpoint on its next attempt.
TIME_LIMIT_MS_FOR_QUEUE: Final[Mapping[QueueName, int]] = {
    QueueName.MAINTENANCE: JOB_TIME_LIMIT_MS,
    QueueName.SYNCHRONIZATION: 30 * 60_000,
}
```

Заміни `HANDLERS` і додай після нього `RUNNABLE_KINDS`:

```python
# The handlers that need nothing but the database. Each handler claims a
# queued Job in its own transaction and refuses one that is no longer queued.
HANDLERS: Final[Mapping[JobKind, JobHandler]] = {
    JobKind.PURGE_DOCUMENT_CHUNKS: purge_document_chunks,
}
# Every kind the worker runs: these and `synchronize_source`, whose handler
# `worker.handlers.job_handlers` builds from settings.
RUNNABLE_KINDS: Final = frozenset({*HANDLERS, JobKind.SYNCHRONIZE_SOURCE})
```

У `declare_job_actors` використай ліміт самої черги:

```python
            queue_name=queue,
            time_limit=TIME_LIMIT_MS_FOR_QUEUE[queue],
```

Заміни `JobRunner`:

```python
@dataclass(frozen=True, slots=True)
class JobRunner:
    """Runs one Job per message, with a database connection of its own."""

    settings: Settings
    handlers: Mapping[JobKind, JobHandler] = field(default_factory=lambda: HANDLERS)

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
            await run_stored_job(
                runtime, job_id, now=datetime.now(UTC), handlers=self.handlers
            )
        finally:
            await runtime.dispose()
```

**Дія 3:** створи `src/knowledge_service/worker/handlers.py`:

```python
"""Build every Job handler the worker runs, from settings.

The purge handler needs only the database. The synchronization handler also
needs a way to open each stored Source, a lease store, and the principal
directory, which come from settings here, once, when the worker starts. A
missing Redis URL or an unreadable directory file stops the worker at start,
not at its first Job.
"""

import json
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any, cast

from knowledge_service.ingestion.grant_normalization import (
    InvalidPrincipalDirectory,
    PrincipalDirectory,
)
from knowledge_service.ingestion.synchronization_jobs import (
    LeaseStoreFactory,
    SynchronizationJobs,
    open_source,
)
from knowledge_service.jobs import JobKind
from knowledge_service.leases import LeaseStore, RedisLeaseStore, lease_client
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import WorkerConfigurationError
from knowledge_service.worker.jobs import HANDLERS, RUNNABLE_KINDS, JobHandler


def load_principal_directory(path: Path) -> PrincipalDirectory:
    """Read a directory file: `{"issuer": ..., "users": {...}, "groups": {...}}`."""
    try:
        data: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise WorkerConfigurationError(
            "the principal directory file cannot be read as JSON"
        ) from error
    if not isinstance(data, dict):
        raise WorkerConfigurationError("the principal directory must be an object")
    fields = cast(dict[str, Any], data)
    issuer = fields.get("issuer")
    if not isinstance(issuer, str):
        raise WorkerConfigurationError("the principal directory needs an issuer")
    try:
        return PrincipalDirectory(
            issuer=issuer,
            users=fields.get("users", {}),
            groups=fields.get("groups", {}),
        )
    except InvalidPrincipalDirectory as error:
        raise WorkerConfigurationError(str(error)) from error


def redis_leases(redis_url: str) -> LeaseStoreFactory:
    """Lease stores on `redis_url`, one client per run, closed after it."""

    @asynccontextmanager
    async def leases() -> AsyncGenerator[LeaseStore]:
        client = lease_client(redis_url)
        try:
            yield RedisLeaseStore(client)
        finally:
            await client.aclose()

    return leases


def job_handlers(settings: Settings) -> Mapping[JobKind, JobHandler]:
    """A handler for every kind in `RUNNABLE_KINDS`."""
    if settings.redis_url is None:
        raise WorkerConfigurationError(
            "KNOWLEDGE_SERVICE_REDIS_URL is required to synchronize sources"
        )
    directory_file = settings.principal_directory_file
    handlers: dict[JobKind, JobHandler] = {
        **HANDLERS,
        JobKind.SYNCHRONIZE_SOURCE: SynchronizationJobs(
            open_source=open_source,
            leases=redis_leases(settings.redis_url.get_secret_value()),
            principal_directory=(
                None
                if directory_file is None
                else load_principal_directory(directory_file)
            ),
            full_pass_interval=timedelta(
                hours=settings.full_synchronization_interval_hours
            ),
        ),
    }
    if frozenset(handlers) != RUNNABLE_KINDS:
        raise WorkerConfigurationError("a runnable job kind has no handler")
    return handlers
```

**Дія 4:** заміни весь `src/knowledge_service/worker/main.py` на:

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
from knowledge_service.worker.handlers import job_handlers
from knowledge_service.worker.jobs import JobRunner, declare_job_actors

settings = Settings()
broker = broker_from_settings(settings)
dramatiq.set_broker(broker)
actors = declare_actors(broker, record_ping=log_ping)
handlers = job_handlers(settings)
job_actors = declare_job_actors(
    broker, run_job=JobRunner(settings, handlers), kinds=frozenset(handlers)
)
```

**Дія 5:** у `src/knowledge_service/worker/relay.py` імпортуй з `worker.jobs` `RUNNABLE_KINDS` замість `HANDLERS` і оголошуй акторів із ним:

```python
        actors = declare_job_actors(
            broker, run_job=JobRunner(settings), kinds=RUNNABLE_KINDS
        )
```

**Дія 6:** у `src/knowledge_service/ingestion/scheduling.py` останній абзац docstring модуля досі каже, що ці Job лишаються в черзі. Заміни його на:

```text
A Source with an open synchronization Job is not scheduled again until that
Job ends: a Job stuck running waits for recovery (M08-T08). The worker runs
these Jobs with `ingestion.synchronization_jobs`.
```

**Перевірка:** `uv run pyright` показує `0 errors`, а `uv run ruff check .` проходить.

### Крок 9 — Крок із кодом: набір тестів із фейковим джерелом і змінені тести

Цей крок пише тести, яких просить завдання, плюс unit-тести підключення, і оновлює чотири тести, чиї очікування змінились навмисно.

**Простими словами:** кожен інтеграційний тест зберігає справжній Job і доставляє його так, як це робить воркер — через `run_stored_job`, із фейковим джерелом. Потім він перевіряє Job, рядок його спроби, колонки прогресу джерела й Document. Unit-тести перевіряють підключення воркера без жодної бази: які види виконуються, ліміт часу, файл довідника й відмови на старті.

| Запуск, який називає завдання | Тест |
|---|---|
| порожній | `test_an_empty_source_succeeds_with_nothing_stored` |
| багатосторінковий, із прогресом посеред роботи | `test_a_multi_page_source_is_stored_and_its_progress_reported` |
| оновлення | `test_an_update_changes_only_the_record_that_changed` |
| видалення | `test_a_file_the_source_no_longer_lists_is_tombstoned` |
| повтор | `test_a_failed_attempt_is_retried_and_resumes_from_its_checkpoint` |
| скасування | `test_a_cancelled_attempt_is_recovered_and_its_retry_resumes` |
| інкрементальний | `test_an_incremental_pass_reads_only_changes_after_the_cursor`, `test_a_full_pass_comes_back_after_the_full_pass_interval`, `test_a_failed_incremental_pass_resumes_as_incremental`, `test_an_incremental_pass_without_a_new_cursor_fails_and_keeps_its_cursor`, `test_a_source_without_a_change_feed_is_always_read_in_full` |
| відмови | результат відновлення лишається; друге повідомлення нічого не запускає; джерела, яких немає, вимкнені чи такі, що не відкриваються; шість знахідок рев'ю |

**Рішення:** набір бере справжній час мінус дві години, а не фіксовану дату. `report_progress` відмовляє часу, що далеко попереду годинника бази, а огорожа — часу, що на понад п'ять хвилин попереду годинника цієї машини. Два тести відновлення відсувають `heard_at` спроби на шість хвилин назад. Саме так для відновлення виглядають п'ять хвилин тиші, а його мінімум — одна хвилина. Сховище оренд — фейк у пам'яті, що завжди погоджується. Саму оренду в Redis покривають тести M08-T05 і тренування в кроці 11.

**Дія 1:** створи `tests/test_synchronization_jobs_integration.py`:

```python
"""Real PostgreSQL tests for running `synchronize_source` Jobs end to end.

Each test stores a Job, delivers it the way a worker does, through
`run_stored_job`, and checks the Job, its attempt, the Source's progress,
and the Documents. The Source is a fake, so every run is exact: empty,
multi-page, update, delete, retry, cancellation, and incremental.
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from database_fixtures import migration_config, migration_environment
from fakes import FakeSource
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceConfiguration,
    SourceKind,
    SourcePage,
    SourcePort,
    SourceRecord,
    SourceUnavailable,
)
from knowledge_service.ingestion.synchronization_jobs import (
    SourceSettings,
    SynchronizationJobs,
    open_source,
)
from knowledge_service.job_dispatch import requeue_due_retries
from knowledge_service.job_failures import JobFailure, RetryPolicy
from knowledge_service.job_progress import AttemptOutcome, JobProgress, JobWarning
from knowledge_service.job_recovery import recover_stuck_jobs
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.leases import Lease, LeaseStore
from knowledge_service.persistence.job_attempts import load_latest_attempt
from knowledge_service.persistence.jobs import enqueue_job, load_job
from knowledge_service.persistence.schema import (
    documents,
    job_attempts,
    jobs,
    sources,
    synchronization_seen,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.jobs import JobOutcome, run_stored_job

pytestmark = pytest.mark.integration

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-0000000000a0")
# Fast, fixed retries: the wait before attempt 2 is half a second.
RETRIES = RetryPolicy(
    base_delay=timedelta(seconds=1),
    max_delay=timedelta(seconds=1),
    jitter=lambda: 0.0,
)


class Crash(Exception):
    """Stands in for a bug that stops a pass at one exact point."""


@dataclass
class MemoryLeases:
    """A lease store that always grants and renews; it holds no state."""

    acquired: list[str] = field(default_factory=list[str])

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
        self.acquired.append(name)
        return Lease(name=name, token=name, ttl=ttl)

    async def renew(self, lease: Lease) -> bool:
        return True

    async def release(self, lease: Lease) -> bool:
        return True


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
def start() -> datetime:
    """A moment in the recent past, so no time a test uses is in the future."""
    return datetime.now(UTC) - timedelta(hours=2)


async def register(
    runtime: DatabaseRuntime,
    *,
    kind: str = "local_directory",
    location: str = "/srv/handbook",
    enabled: bool = True,
) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.insert(sources).values(
                source_id=SOURCE_ID.value,
                kind=kind,
                location=location,
                display_name="Handbook",
                enabled=enabled,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                default_public=True,
            )
        )


async def queue(runtime: DatabaseRuntime, at: datetime) -> JobId:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=SOURCE_ID.value,
        queued_at=at,
    )
    async with runtime.transaction() as connection:
        return (await enqueue_job(connection, job)).job.job_id


def handler(
    source: SourcePort,
    at: datetime,
    *,
    full_pass_interval: timedelta = timedelta(hours=24),
) -> SynchronizationJobs:
    """The real handler, opening `source` for every stored Source."""
    leases = MemoryLeases()

    @asynccontextmanager
    async def memory() -> AsyncGenerator[LeaseStore]:
        yield leases

    return SynchronizationJobs(
        open_source=lambda _settings: source,
        leases=memory,
        principal_directory=None,
        full_pass_interval=full_pass_interval,
        retry_policy=RETRIES,
        page_size=1,
        clock=lambda: at,
    )


async def deliver(
    runtime: DatabaseRuntime, job_id: JobId, run: SynchronizationJobs, at: datetime
) -> JobOutcome:
    return await run_stored_job(
        runtime, job_id, now=at, handlers={JobKind.SYNCHRONIZE_SOURCE: run}
    )


async def sync(
    runtime: DatabaseRuntime,
    source: SourcePort,
    at: datetime,
    *,
    full_pass_interval: timedelta = timedelta(hours=24),
) -> Job:
    """Queue one Job at `at` and run it to its end."""
    job_id = await queue(runtime, at)
    outcome = await deliver(
        runtime,
        job_id,
        handler(source, at, full_pass_interval=full_pass_interval),
        at,
    )
    return outcome.job


def record(name: str, text: str = "") -> SourceRecord:
    return SourceRecord.present(
        external_id=name,
        version="v1",
        media_type="text/plain",
        content=(text or f"The {name} policy.").encode(),
        access=SourceAccess(public=True),
    )


def gone(name: str) -> SourceRecord:
    return SourceRecord.deleted(external_id=name, version="gone")


async def availability(runtime: DatabaseRuntime) -> dict[str, str]:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(documents.c.external_id, documents.c.availability)
        )
        return {row.external_id: row.availability for row in rows}


async def attempt(runtime: DatabaseRuntime, job_id: JobId) -> JobProgress:
    async with runtime.transaction() as connection:
        progress = await load_latest_attempt(connection, job_id=job_id)
    assert progress is not None
    return progress


type Progress = tuple[str | None, str | None, str | None, datetime | None]


async def source_row(runtime: DatabaseRuntime) -> Progress:
    """The Source's checkpoint, pass mode, change cursor, and last full pass."""
    async with runtime.transaction() as connection:
        row = (
            await connection.execute(
                sa.select(
                    sources.c.checkpoint,
                    sources.c.pass_mode,
                    sources.c.sync_cursor,
                    sources.c.last_full_pass_at,
                ).where(sources.c.source_id == SOURCE_ID.value)
            )
        ).one()
    return (row.checkpoint, row.pass_mode, row.sync_cursor, row.last_full_pass_at)


async def purge_jobs(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        return (
            await connection.scalar(
                sa.select(sa.func.count())
                .select_from(jobs)
                .where(jobs.c.kind == JobKind.PURGE_DOCUMENT_CHUNKS.value)
            )
        ) or 0


async def fall_silent(runtime: DatabaseRuntime, job_id: JobId) -> None:
    """Make the database's last word from the Job's worker six minutes old."""
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(job_attempts)
            .where(job_attempts.c.job_id == job_id.value)
            .values(heard_at=job_attempts.c.heard_at - timedelta(minutes=6))
        )


THREE_PAGES = [[record("a.txt")], [record("b.txt")], [record("c.txt")]]


# --- Full passes: empty, multi-page, update, delete


async def test_an_empty_source_succeeds_with_nothing_stored(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)

    job = await sync(runtime, FakeSource([]), start)

    assert job.status is JobStatus.SUCCEEDED
    progress = await attempt(runtime, job.job_id)
    assert progress.outcome is AttemptOutcome.SUCCEEDED
    assert (progress.counters.processed, progress.counters.changed) == (0, 0)
    assert await availability(runtime) == {}


async def test_a_multi_page_source_is_stored_and_its_progress_reported(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    seen_mid_run: list[int] = []

    class Watched(FakeSource):
        """Reads the stored progress before it hands out page three."""

        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            if checkpoint == "2":
                seen_mid_run.append((await attempt(runtime, job_id)).counters.processed)
            return await super().fetch_page(checkpoint=checkpoint, page_size=page_size)

    job_id = await queue(runtime, start)
    outcome = await deliver(
        runtime, job_id, handler(Watched(THREE_PAGES), start), start
    )

    assert outcome.job.status is JobStatus.SUCCEEDED
    # Two pages were committed, and reported, before the third was read.
    assert seen_mid_run == [2]
    counters = (await attempt(runtime, job_id)).counters
    assert (counters.processed, counters.changed, counters.skipped) == (3, 3, 0)
    assert await availability(runtime) == dict.fromkeys(
        ("a.txt", "b.txt", "c.txt"), "available"
    )
    assert (await source_row(runtime)) == (None, None, None, start)


async def test_an_update_changes_only_the_record_that_changed(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    await sync(runtime, FakeSource(THREE_PAGES), start)
    edited = [[record("a.txt")], [record("b.txt", "Rewritten.")], [record("c.txt")]]

    job = await sync(runtime, FakeSource(edited), start + timedelta(minutes=5))

    counters = (await attempt(runtime, job.job_id)).counters
    assert (counters.processed, counters.changed) == (3, 1)


async def test_a_file_the_source_no_longer_lists_is_tombstoned(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    await sync(runtime, FakeSource(THREE_PAGES), start)

    job = await sync(
        runtime,
        FakeSource([[record("a.txt")], [record("c.txt")]]),
        start + timedelta(minutes=5),
    )

    assert job.status is JobStatus.SUCCEEDED
    assert (await availability(runtime))["b.txt"] == "tombstoned"
    assert await purge_jobs(runtime) == 1


async def test_a_quarantined_file_finishes_with_a_warning(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    broken = SourceRecord.present(
        external_id="broken.txt",
        version="v1",
        media_type="application/x-unknown",
        content=b"???",
        access=SourceAccess(public=True),
    )

    job = await sync(runtime, FakeSource([[record("a.txt")], [broken]]), start)

    progress = await attempt(runtime, job.job_id)
    assert progress.outcome is AttemptOutcome.SUCCEEDED_WITH_WARNINGS
    assert progress.warnings == frozenset({JobWarning.QUARANTINED_ITEMS})
    assert progress.counters.skipped == 1


# --- Retry and cancellation


async def test_a_failed_attempt_is_retried_and_resumes_from_its_checkpoint(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    job_id = await queue(runtime, start)
    # The Source goes away while page three is fetched.
    first = await deliver(
        runtime, job_id, handler(FakeSource(THREE_PAGES, fail_on_call=3), start), start
    )
    assert first.job.status is JobStatus.FAILED
    assert first.job.last_error_code == JobFailure.SOURCE_UNAVAILABLE.value
    assert first.job.retry_at is not None
    assert (await attempt(runtime, job_id)).outcome is AttemptOutcome.RETRY_SCHEDULED

    later = start + timedelta(seconds=1)
    assert await requeue_due_retries(runtime, now=later) == (job_id,)
    retried = FakeSource(THREE_PAGES)
    second = await deliver(runtime, job_id, handler(retried, later), later)

    assert second.job.status is JobStatus.SUCCEEDED
    assert second.job.attempt == 1
    assert retried.checkpoints == ["2"]
    assert (await attempt(runtime, job_id)).attempt == 2
    assert len(await availability(runtime)) == 3


async def test_a_cancelled_attempt_is_recovered_and_its_retry_resumes(
    runtime: DatabaseRuntime,
) -> None:
    # Real time: recovery compares the silence with the database's clock.
    now = datetime.now(UTC)
    await register(runtime)
    waiting = asyncio.Event()

    class HangsOnPageThree(FakeSource):
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            if checkpoint == "2":
                waiting.set()
                await asyncio.Event().wait()
            return await super().fetch_page(checkpoint=checkpoint, page_size=page_size)

    job_id = await queue(runtime, now)
    task = asyncio.create_task(
        deliver(runtime, job_id, handler(HangsOnPageThree(THREE_PAGES), now), now)
    )
    await waiting.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Nothing was recorded: the Job still runs, as far as anyone can tell.
    async with runtime.transaction() as connection:
        stuck = await load_job(connection, job_id=job_id)
    assert stuck is not None
    assert stuck.status is JobStatus.RUNNING

    # Five silent minutes later, recovery takes the worker for dead.
    await fall_silent(runtime, job_id)
    recovered = await recover_stuck_jobs(
        runtime, now=datetime.now(UTC), retry_policy=RETRIES
    )
    assert recovered.stale == (job_id,)
    later = datetime.now(UTC) + timedelta(seconds=1)
    assert await requeue_due_retries(runtime, now=later) == (job_id,)
    retried = FakeSource(THREE_PAGES)
    outcome = await deliver(runtime, job_id, handler(retried, later), later)

    assert outcome.job.status is JobStatus.SUCCEEDED
    assert retried.checkpoints == ["2"]
    assert len(await availability(runtime)) == 3


async def test_an_attempt_ended_by_recovery_keeps_recoverys_outcome(
    runtime: DatabaseRuntime,
) -> None:
    now = datetime.now(UTC)
    await register(runtime)
    job_id = await queue(runtime, now)

    class RecoveredMidRun(FakeSource):
        """Recovery takes the Job for dead while page two is read."""

        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            if checkpoint == "1":
                await fall_silent(runtime, job_id)
                await recover_stuck_jobs(
                    runtime, now=datetime.now(UTC), retry_policy=RETRIES
                )
            return await super().fetch_page(checkpoint=checkpoint, page_size=page_size)

    outcome = await deliver(
        runtime, job_id, handler(RecoveredMidRun(THREE_PAGES), now), now
    )

    # The late worker's report and finish were refused: recovery's stands.
    assert outcome.duplicate is True
    assert outcome.job.status is JobStatus.FAILED
    assert outcome.job.last_error_code == JobFailure.WORKER_LOST.value


async def test_a_second_message_for_a_finished_job_runs_nothing(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    job_id = await queue(runtime, start)
    await deliver(runtime, job_id, handler(FakeSource(THREE_PAGES), start), start)
    again = FakeSource(THREE_PAGES)

    outcome = await deliver(runtime, job_id, handler(again, start), start)

    assert outcome.duplicate is True
    assert again.checkpoints == []


# --- Sources that cannot be synchronized


@pytest.mark.parametrize("enabled", [False, None], ids=["disabled", "missing"])
async def test_a_disabled_or_missing_source_fails_the_job_for_good(
    runtime: DatabaseRuntime, start: datetime, enabled: bool | None
) -> None:
    if enabled is not None:
        await register(runtime, enabled=enabled)
    untouched = FakeSource(THREE_PAGES)

    job = await sync(runtime, untouched, start)

    assert job.status is JobStatus.FAILED
    assert job.last_error_code == JobFailure.SOURCE_GONE.value
    assert job.retry_at is None
    assert untouched.checkpoints == []


@pytest.mark.parametrize(
    ("kind", "location"),
    [("confluence", "https://wiki.example.test"), ("local_directory", "relative")],
    ids=["no-adapter-yet", "relative-folder"],
)
async def test_a_source_no_adapter_can_open_fails_the_job_for_good(
    runtime: DatabaseRuntime, start: datetime, kind: str, location: str
) -> None:
    await register(runtime, kind=kind, location=location)
    job_id = await queue(runtime, start)
    run = handler(FakeSource([]), start)
    real = SynchronizationJobs(
        open_source=open_source,
        leases=run.leases,
        principal_directory=None,
        clock=run.clock,
    )

    outcome = await deliver(runtime, job_id, real, start)

    assert outcome.job.status is JobStatus.FAILED
    assert outcome.job.last_error_code == JobFailure.SOURCE_MISCONFIGURED.value
    assert outcome.job.retry_at is None


# --- Incremental passes


type Changes = Mapping[str, tuple[Sequence[Sequence[SourceRecord]], str]]


class FeedSource(FakeSource):
    """A fake Source with a change feed.

    A full listing ends with cursor "c1". The changes after a cursor are
    `changes[since]`: pages of changed records, and the cursor that follows.
    `fail_on_change_call` makes that call to `fetch_changes` fail as if the
    Source were unreachable.
    """

    def __init__(
        self,
        pages: Sequence[Sequence[SourceRecord]],
        changes: Changes,
        *,
        fail_on_change_call: int | None = None,
    ) -> None:
        super().__init__(pages)
        self.changes = changes
        self.since: list[str] = []
        self._fail_on = fail_on_change_call

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        page = await super().fetch_page(checkpoint=checkpoint, page_size=page_size)
        if page.next_checkpoint is not None:
            return page
        return SourcePage(records=page.records, next_checkpoint=None, cursor="c1")

    async def fetch_changes(
        self, *, since: str, checkpoint: str | None, page_size: int
    ) -> SourcePage:
        self.since.append(since)
        if self._fail_on is not None and len(self.since) == self._fail_on:
            raise SourceUnavailable("the change feed went away")
        pages, following = self.changes[since]
        index = 0 if checkpoint is None else int(checkpoint)
        last = index + 1 >= len(pages)
        return SourcePage(
            records=tuple(pages[index]) if pages else (),
            next_checkpoint=None if last else str(index + 1),
            cursor=following if last else None,
        )


async def test_an_incremental_pass_reads_only_changes_after_the_cursor(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    changes = {"c1": ([[record("a.txt", "Edited.")], [gone("b.txt")]], "c2")}
    full = await sync(runtime, FeedSource(THREE_PAGES, changes), start)
    assert (await source_row(runtime)) == (None, None, "c1", start)

    feed = FeedSource(THREE_PAGES, changes)
    job = await sync(runtime, feed, start + timedelta(minutes=5))

    assert (full.status, job.status) == (JobStatus.SUCCEEDED, JobStatus.SUCCEEDED)
    # No full listing was read: c.txt was never offered, and stays.
    assert feed.checkpoints == []
    assert feed.since == ["c1", "c1"]
    assert await availability(runtime) == {
        "a.txt": "available",
        "b.txt": "tombstoned",
        "c.txt": "available",
    }
    counters = (await attempt(runtime, job.job_id)).counters
    assert (counters.processed, counters.changed) == (1, 1)
    # The cursor moved on; the last full pass is still the first one.
    assert (await source_row(runtime)) == (None, None, "c2", start)


async def test_a_full_pass_comes_back_after_the_full_pass_interval(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    changes: Changes = {"c1": ([[]], "c2")}
    await sync(runtime, FeedSource(THREE_PAGES, changes), start)
    # The Source dropped c.txt without reporting it; only a full pass notices.
    feed = FeedSource([[record("a.txt")], [record("b.txt")]], changes)

    await sync(runtime, feed, start + timedelta(minutes=5))
    assert (await availability(runtime))["c.txt"] == "available"
    await sync(
        runtime,
        feed,
        start + timedelta(minutes=40),
        full_pass_interval=timedelta(minutes=30),
    )

    assert feed.since == ["c1"]
    assert feed.checkpoints == [None, "1"]
    assert (await availability(runtime))["c.txt"] == "tombstoned"
    assert (await source_row(runtime))[2:] == (
        "c1",
        start + timedelta(minutes=40),
    )


async def test_a_failed_incremental_pass_resumes_as_incremental(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    changes = {"c1": ([[record("a.txt", "Edited.")], [gone("b.txt")]], "c2")}
    await sync(runtime, FeedSource(THREE_PAGES, changes), start)
    job_id = await queue(runtime, start + timedelta(minutes=5))
    first = FeedSource(THREE_PAGES, changes, fail_on_change_call=2)

    failed = await deliver(
        runtime,
        job_id,
        handler(first, start + timedelta(minutes=5)),
        start + timedelta(minutes=5),
    )
    assert failed.job.last_error_code == JobFailure.SOURCE_UNAVAILABLE.value
    assert (await source_row(runtime))[:2] == ("1", "incremental")

    # The retry resumes from its checkpoint, still reading changes after "c1".
    later = start + timedelta(minutes=6)
    assert await requeue_due_retries(runtime, now=later) == (job_id,)
    retried = FeedSource(THREE_PAGES, changes)
    done = await deliver(runtime, job_id, handler(retried, later), later)

    assert done.job.status is JobStatus.SUCCEEDED
    assert retried.since == ["c1"]
    assert (await availability(runtime))["b.txt"] == "tombstoned"


async def test_an_incremental_pass_without_a_new_cursor_fails_and_keeps_its_cursor(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)

    class LosesItsCursor(FeedSource):
        async def fetch_changes(
            self, *, since: str, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            page = await super().fetch_changes(
                since=since, checkpoint=checkpoint, page_size=page_size
            )
            return SourcePage(records=page.records, next_checkpoint=None)

    changes = {"c1": ([[gone("a.txt")]], "c2")}
    await sync(runtime, FeedSource(THREE_PAGES, changes), start)

    job = await sync(
        runtime, LosesItsCursor(THREE_PAGES, changes), start + timedelta(minutes=5)
    )

    assert job.status is JobStatus.FAILED
    assert (await availability(runtime))["a.txt"] == "available"
    assert (await source_row(runtime))[2] == "c1"


async def test_a_source_without_a_change_feed_is_always_read_in_full(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    await sync(runtime, FakeSource(THREE_PAGES), start)
    again = FakeSource(THREE_PAGES)

    await sync(runtime, again, start + timedelta(minutes=5))

    assert again.checkpoints == [None, "1", "2"]
    assert (await source_row(runtime))[2] is None


# --- Review findings


async def test_a_warning_a_later_report_resolves_does_not_fail_the_job(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    unknown_reader = SourceRecord.present(
        external_id="a.txt",
        version="v1",
        media_type="text/plain",
        content=b"The a.txt policy.",
        access=SourceAccess(public=False, principals=("user:bob",)),
    )

    job = await sync(runtime, FakeSource([[unknown_reader], [record("a.txt")]]), start)

    assert job.status is JobStatus.SUCCEEDED
    progress = await attempt(runtime, job.job_id)
    assert progress.warnings == frozenset({JobWarning.UNRESOLVED_READERS})


async def test_a_clock_stepping_back_between_pages_does_not_fail_the_job(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    readings = iter([start + timedelta(seconds=10), start + timedelta(seconds=8)])
    run = handler(FakeSource(THREE_PAGES), start)
    stepping = SynchronizationJobs(
        open_source=run.open_source,
        leases=run.leases,
        principal_directory=None,
        page_size=1,
        clock=lambda: next(readings, start + timedelta(seconds=8)),
    )
    job_id = await queue(runtime, start)

    outcome = await deliver(runtime, job_id, stepping, start)

    assert outcome.job.status is JobStatus.SUCCEEDED


async def test_a_job_queued_by_a_clock_far_ahead_is_retried_not_failed_for_good(
    runtime: DatabaseRuntime,
) -> None:
    now = datetime.now(UTC)
    await register(runtime)
    job_id = await queue(runtime, now + timedelta(minutes=6))

    outcome = await deliver(runtime, job_id, handler(FakeSource([]), now), now)

    assert outcome.job.last_error_code == JobFailure.CLOCK_AHEAD.value
    assert outcome.job.retry_at is not None


async def test_an_incremental_pass_whose_source_lost_its_feed_starts_over_in_full(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    changes: Changes = {"c1": ([[record("a.txt", "Edited.")]], "c2")}
    await sync(runtime, FeedSource(THREE_PAGES, changes), start)
    job_id = await queue(runtime, start + timedelta(minutes=5))
    failing = FeedSource(THREE_PAGES, changes, fail_on_change_call=1)
    at = start + timedelta(minutes=5)
    await deliver(runtime, job_id, handler(failing, at), at)
    assert (await source_row(runtime))[1] == "incremental"

    # The adapter is redeployed without its change feed.
    later = start + timedelta(minutes=6)
    assert await requeue_due_retries(runtime, now=later) == (job_id,)
    plain = FakeSource(THREE_PAGES)
    done = await deliver(runtime, job_id, handler(plain, later), later)

    assert done.job.status is JobStatus.SUCCEEDED
    assert plain.checkpoints == [None, "1", "2"]
    assert (await source_row(runtime))[2] is None


async def test_a_null_default_principal_is_misconfigured_not_a_bug(
    runtime: DatabaseRuntime, start: datetime
) -> None:
    await register(runtime)
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.update(sources).values(
                default_public=False,
                default_principals=sa.text("array[null]::text[]"),
            )
        )

    job = await sync(runtime, FakeSource([]), start)

    assert job.last_error_code == JobFailure.SOURCE_MISCONFIGURED.value


# --- The production opener and the migration


def test_a_local_folder_opens_with_its_stored_readers() -> None:
    settings = SourceSettings(
        source_id=SOURCE_ID,
        configuration=SourceConfiguration(
            kind=SourceKind.LOCAL_DIRECTORY, location="/srv/docs"
        ),
        default_access=SourceAccess(public=False, principals=("group:engineering",)),
    )

    opened = open_source(settings)

    assert isinstance(opened, DirectorySource)


def test_the_migration_goes_down_and_up_with_passes_in_progress(
    migrated_database: str,
) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    in_progress = datetime(2026, 11, 24, 9, 0, tzinfo=UTC)
    full_id = SourceId.parse("00000000-0000-0000-0000-0000000000a1")
    try:
        with engine.begin() as connection:
            for source_id, mode in ((SOURCE_ID, "incremental"), (full_id, "full")):
                connection.execute(
                    sa.insert(sources).values(
                        source_id=source_id.value,
                        kind="local_directory",
                        location=f"/srv/{mode}",
                        display_name=mode,
                        enabled=True,
                        created_at=in_progress,
                        updated_at=in_progress,
                        pass_started_at=in_progress,
                        pass_mode=mode,
                        checkpoint="1",
                    )
                )
                connection.execute(
                    sa.insert(synchronization_seen).values(
                        [
                            {
                                "source_id": source_id.value,
                                "external_id": "kept.txt",
                                "deleted": False,
                            },
                            {
                                "source_id": source_id.value,
                                "external_id": "gone.txt",
                                "deleted": True,
                            },
                        ]
                    )
                )
        with migration_environment(url):
            command.downgrade(config, "e1f2a3b4c5d6")
        with engine.connect() as connection:
            columns = {
                column["name"]
                for column in sa.inspect(connection).get_columns("sources")
            }
            passes = connection.execute(
                sa.text("select source_id, checkpoint from sources order by location")
            ).all()
            seen = connection.execute(
                sa.text("select source_id, external_id from synchronization_seen")
            ).all()
        assert not columns & {
            "pass_mode",
            "sync_cursor",
            "last_full_pass_at",
            "default_public",
            "default_principals",
        }
        # The full pass keeps its progress, minus what it saw deleted; the
        # incremental one has no meaning before the revision and is dropped.
        assert [(str(row[0]), row[1]) for row in passes] == [
            (str(full_id.value), "1"),
            (str(SOURCE_ID.value), None),
        ]
        assert [(str(row[0]), row[1]) for row in seen] == [
            (str(full_id.value), "kept.txt")
        ]
    finally:
        with migration_environment(url):
            command.upgrade(config, "head")
        engine.dispose()
```

**Дія 2:** створи `tests/test_synchronization_job_wiring.py`:

```python
"""Unit tests for what wires synchronization Jobs into the worker.

Nothing here touches a database, Redis, or the network.
"""

import json
import os
from pathlib import Path

import dramatiq
import pytest
from dramatiq.brokers.stub import StubBroker
from pydantic import SecretStr

from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourceGone,
    SourceMisconfigured,
    SourcePage,
)
from knowledge_service.ingestion.synchronization_jobs import SynchronizationJobs
from knowledge_service.job_failures import JobFailure, classify_job_failure
from knowledge_service.jobs import JobKind
from knowledge_service.settings import Settings
from knowledge_service.worker.broker import QueueName, WorkerConfigurationError
from knowledge_service.worker.handlers import job_handlers, load_principal_directory
from knowledge_service.worker.jobs import (
    RUNNABLE_KINDS,
    TIME_LIMIT_MS_FOR_QUEUE,
    declare_job_actors,
)


@pytest.fixture(autouse=True)
def isolate_settings_sources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No environment variable or `.env` file may change these settings."""
    for variable in tuple(os.environ):
        if variable.upper().startswith("KNOWLEDGE_SERVICE_"):
            monkeypatch.delenv(variable)
    monkeypatch.chdir(tmp_path)


def test_only_the_last_page_carries_a_cursor() -> None:
    assert SourcePage(records=(), next_checkpoint=None, cursor="c1").cursor == "c1"
    with pytest.raises(InvalidSource, match="only the last page"):
        SourcePage(records=(), next_checkpoint="2", cursor="c1")
    with pytest.raises(InvalidSource, match="must not be empty"):
        SourcePage(records=(), next_checkpoint=None, cursor=" ")


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (SourceGone("gone"), JobFailure.SOURCE_GONE),
        (SourceMisconfigured("bad"), JobFailure.SOURCE_MISCONFIGURED),
    ],
)
def test_a_source_that_cannot_be_synchronized_fails_for_good(
    error: Exception, failure: JobFailure
) -> None:
    assert classify_job_failure(error) is failure
    assert not failure.transient


def test_the_worker_runs_synchronizations_and_purges() -> None:
    handlers = job_handlers(Settings(redis_url=SecretStr("redis://127.0.0.1:1/0")))

    assert frozenset(handlers) == RUNNABLE_KINDS
    assert JobKind.SYNCHRONIZE_SOURCE in RUNNABLE_KINDS
    assert isinstance(handlers[JobKind.SYNCHRONIZE_SOURCE], SynchronizationJobs)


def test_the_worker_refuses_to_start_without_redis() -> None:
    with pytest.raises(WorkerConfigurationError, match="REDIS_URL"):
        job_handlers(Settings())


def test_a_synchronization_message_may_run_for_half_an_hour() -> None:
    broker = StubBroker()
    dramatiq.set_broker(broker)
    actors = declare_job_actors(
        broker, run_job=lambda _job_id: None, kinds=RUNNABLE_KINDS
    )

    synchronization = actors.by_queue[QueueName.SYNCHRONIZATION]
    assert synchronization.options["time_limit"] == 30 * 60_000
    assert TIME_LIMIT_MS_FOR_QUEUE[QueueName.MAINTENANCE] == 60_000


def test_a_principal_directory_file_is_read(tmp_path: Path) -> None:
    path = tmp_path / "directory.json"
    path.write_text(
        json.dumps(
            {
                "issuer": "http://localhost:8180/realms/knowledge-service",
                "users": {"carol@example.test": "0f5e7d2a"},
                "groups": {"engineering": "/engineering"},
            }
        )
    )

    directory = load_principal_directory(path)

    assert directory.groups == {"engineering": "/engineering"}


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", "{}", '{"issuer": "x", "groups": {"g": "no-slash"}}'],
    ids=["not-json", "not-an-object", "no-issuer", "invalid-group-path"],
)
def test_a_bad_principal_directory_file_stops_the_worker(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "directory.json"
    path.write_text(content)

    with pytest.raises(WorkerConfigurationError):
        load_principal_directory(path)


def test_a_missing_principal_directory_file_stops_the_worker(tmp_path: Path) -> None:
    settings = Settings(
        redis_url=SecretStr("redis://127.0.0.1:1/0"),
        principal_directory_file=tmp_path / "missing.json",
    )

    with pytest.raises(WorkerConfigurationError, match="cannot be read"):
        job_handlers(settings)
```

**Дія 3:** онови чотири наявні тести.

У `tests/test_checkpoints_integration.py` `record_seen` тепер називається `record_reports`. Заміни початок `test_a_page_commit_is_all_or_nothing` до рядка `with pytest.raises(Crash):`:

```python
    record_reports = checkpoints.record_reports

    async def crash_on_b(
        connection: AsyncConnection,
        *,
        source_id: SourceId,
        offered: Collection[str],
        deleted: Collection[str],
    ) -> None:
        if "b.txt" in offered:
            raise Crash
        await record_reports(
            connection, source_id=source_id, offered=offered, deleted=deleted
        )

    # Page two's commit dies after it moved the checkpoint, before it ends.
    with monkeypatch.context() as patched:
        patched.setattr(checkpoints, "record_reports", crash_on_b)
```

У `tests/test_audit_events_integration.py` подія аудиту має ключ `mode`:

```python
    assert dict(finished.metadata) == {
        "changed": 2,
        "unchanged": 0,
        "quarantined": 0,
        "tombstoned": 0,
        "unresolved": 0,
        "resumed": False,
        "mode": "full",
    }
```

У `tests/test_worker.py` тест `test_worker_entry_point_declares_ping_on_its_global_broker` тепер очікує актора синхронізації:

```python
        assert entry.broker.get_declared_actors() == {
            "ping",
            "run_maintenance_job",
            "run_synchronization_job",
        }
```

У `tests/test_job_failures.py` тест `test_only_failures_that_may_pass_later_are_transient` тепер включає `CLOCK_AHEAD`:

```python
    # A dependency that may answer later, a clock that may come back in line,
    # or a worker or message lost on the way.
    assert {failure for failure in JobFailure if failure.transient} == {
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.CLOCK_AHEAD,
```

**Перевірка:**

```bash
uv run pytest tests/test_synchronization_jobs_integration.py -q -m integration
uv run pytest tests/test_synchronization_job_wiring.py tests/test_worker.py tests/test_job_failures.py -q
```

Перша команда показує `25 passed`. Друга теж проходить.

### Крок 10 — У цьому кроці без коду: зламай навмисно

Цей крок прибирає перевірку "ця спроба досі виконується" з блоку 2 і показує, який тест її стереже.

**Дія 1:** у `_finish` у `src/knowledge_service/ingestion/synchronization_jobs.py` зміни цей рядок:

```python
            if stored.status is not JobStatus.RUNNING or (
```

на:

```python
            if False and (
```

**Дія 2:** запусти:

```bash
uv run pytest tests/test_synchronization_jobs_integration.py -q -m integration
```

Ти бачиш `1 failed, 24 passed`. Падає `test_an_attempt_ended_by_recovery_keeps_recoverys_outcome`. Запізнілий воркер перезаписав `failed, worker_lost` від відновлення на `succeeded`, тож запланований відновленням повтор зник. Усі інші тести проходять, бо в них ніхто не чіпає Job, поки той виконується. Саме це спростувало б дизайн: Job, чий збережений результат не відповідає спробі, яка справді виконується.

**Дія 3:** скасуй зміну й запусти команду ще раз: `25 passed`.

**Перевірка:** порівняй кількість зі своїм передбаченням із кроку 1 і запиши її у файл evidence.

### Крок 11 — У цьому кроці без коду: тренування зі справжнім воркером

Цей крок запускає справжні relay, Redis і воркер `dramatiq` над текою на твоєму диску. Він використовує окрему базу `knowledge_service_drill`, тож тестову базу не зачеплено.

**Дія 1:** в одному терміналі, з кореня репозиторію, задай середовище тренування й створи теку з двома файлами:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
export KNOWLEDGE_SERVICE_DATABASE_URL="${KNOWLEDGE_SERVICE_TEST_DATABASE_URL%/*}/knowledge_service_drill"
export KNOWLEDGE_SERVICE_REDIS_URL="redis://127.0.0.1:6380/12"
export DRILL_DOCS="$(mktemp -d)"
printf '# Handbook\n\nWelcome aboard.\n' > "$DRILL_DOCS/handbook.md"
printf '# Leave\n\nTwenty days a year.\n' > "$DRILL_DOCS/leave.md"
```

`${...%/*}` відрізає назву бази від тестового URL, тож тренування використовує той самий сервер і пароль з іншою базою.

**Дія 2:** створи й мігруй базу тренування та зареєструй теку як публічне джерело:

```bash
uv run python - "$KNOWLEDGE_SERVICE_DATABASE_URL" "$DRILL_DOCS" <<'EOF'
import sys

import sqlalchemy as sa
from sqlalchemy.engine import make_url

sys.path.insert(0, "tests")
from database_fixtures import create_database, migrate

url = make_url(sys.argv[1])
create_database(url)
migrate(url)
engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
with engine.begin() as connection:
    connection.execute(
        sa.text(
            "insert into sources (source_id, kind, location, display_name,"
            " enabled, created_at, updated_at, default_public)"
            " values ('00000000-0000-0000-0000-00000000d011', 'local_directory',"
            " :location, 'Drill handbook', true, now(), now(), true)"
        ),
        {"location": sys.argv[2]},
    )
engine.dispose()
print("drill database ready")
EOF
```

**Дія 3:** запусти один прохід relay, потім стартуй воркер. Зупини його через Ctrl+C, щойно він запише в лог рядок про Job:

```bash
make worker-relay
make worker
```

Relay друкує `scheduled 1, published 1`. Воркер пише в лог `(synchronize_source) ran: succeeded`.

**Дія 4:** подивись, що він зберіг:

```bash
uv run python - "$KNOWLEDGE_SERVICE_DATABASE_URL" <<'EOF'
import sys

import sqlalchemy as sa
from sqlalchemy.engine import make_url

engine = sa.create_engine(make_url(sys.argv[1]).set(drivername="postgresql+psycopg"))
with engine.connect() as connection:
    for label, query in (
        ("jobs", "select kind, status from jobs order by queued_at"),
        ("attempts", "select attempt, processed, changed, outcome from job_attempts"),
        ("documents", "select external_id, availability from documents order by 1"),
    ):
        print(label, connection.execute(sa.text(query)).all())
engine.dispose()
EOF
```

Ти бачиш один успішний Job `synchronize_source`, одну спробу з `processed` 2 і `changed` 2 і обидва файли доступними.

**Дія 5:** видали один файл і зроби наступний прохід доступним через хвилину замість години:

```bash
rm "$DRILL_DOCS/leave.md"
export KNOWLEDGE_SERVICE_SYNCHRONIZATION_INTERVAL_MINUTES=1
```

Через хвилину знову запускай `make worker-relay`, доки він не надрукує `scheduled 1`. Потім запусти `make worker` і зупини після рядка в лозі. Ще раз виконай команду з дії 4. Ти бачиш дві успішні синхронізації, Job `purge_document_chunks` у черзі й `leave.md` з tombstone. Ще один `make worker-relay` і `make worker` виконали б це очищення.

**Дія 6:** прибери за собою:

```bash
docker exec knowledge-service-postgres psql -U test_user -d postgres -c "DROP DATABASE knowledge_service_drill WITH (FORCE)"
docker exec knowledge-service-redis-redis-1 redis-cli -n 12 flushdb
unset KNOWLEDGE_SERVICE_DATABASE_URL KNOWLEDGE_SERVICE_REDIS_URL KNOWLEDGE_SERVICE_SYNCHRONIZATION_INTERVAL_MINUTES
```

**Перевірка:** обидва проходи поводились як описано. Запиши рядки relay і воркера у файл evidence.

### Крок 12 — У цьому кроці без коду: онови три документи

Цей крок описує працюючу синхронізацію там, де її шукатимуть оператори й рецензенти. Нові тексти лишаються англійською, як і решта цих документів.

**Дія 1:** у `docs/architecture.md`, в абзаці про `schedule_due_synchronizations`, заміни два останні речення, починаючи з "The Job carries the Source's ID only", на:

```markdown
The Job carries the Source's ID only: its handler
(`ingestion.synchronization_jobs`) reads the location, credentials
reference, readers, and progress from PostgreSQL when it runs, so no
configuration or secret travels through Redis.
```

Потім додай цей розділ просто перед "#### Embedding publication as built in M04":

````markdown
#### Synchronization Jobs as built in M08-T10

The worker runs each `synchronize_source` Job through one module,
`ingestion.synchronization_jobs`. It claims the queued Job in a short
transaction, reads the Source the Job names, opens it with a Source opener,
and runs the pass under the Source's lease, observed at the attempt's start
time. After every committed page it reports the attempt's totals, which is
also the attempt's sign of life for recovery. It stores the end only if the
Job still runs that attempt. A Source that is gone, disabled, or that no
adapter opens fails the Job for good; an unavailable Source, database, or
lease is retried, and the retry resumes from the checkpoint.

A pass is full or incremental. A full pass reads every item and tombstones
what it did not see. A Source that is a `ChangeFeed` gives a cursor on the
last page of a full pass; the next pass then reads only the changes after
it, with `fetch_changes`, and tombstones only what the Source reported
deleted. A full pass comes back once `full_synchronization_interval_hours`
(24 by default) has passed since the last one, to catch changes the feed
missed. A local folder has no change feed, so it is always read in full.

```mermaid
sequenceDiagram
    participant Relay as worker-relay
    participant Worker
    participant Jobs as synchronization_jobs
    participant Sync as synchronize_source_exclusively
    participant DB as PostgreSQL
    Relay->>Worker: message with the Job's ID only
    Worker->>Jobs: run_stored_job: the handler for synchronize_source
    Jobs->>DB: claim: queued to running, store the attempt row, commit
    Jobs->>DB: read the Source: kind, location, readers (gone or disabled fails for good)
    Jobs->>Sync: the opened Source, observed at the attempt's start
    loop each committed page
        Sync->>Jobs: on_page with the attempt's totals
        Jobs->>DB: report_progress (also the attempt's sign of life)
    end
    Sync->>DB: finish_pass: full tombstones the unseen, incremental the reported deleted; keep the cursor
    Jobs->>DB: if still running this attempt: succeed, or fail and plan a retry
```
````

**Дія 2:** у `docs/operations.md` заміни речення "A synchronization pass takes its Source's lease in Redis first; while Redis is down no pass starts, and its Job is retried later." на:

```markdown
The worker (`make worker`) runs `synchronize_source` and purge Jobs; it refuses to start without `KNOWLEDGE_SERVICE_REDIS_URL`, and reads Source principals through the JSON file `KNOWLEDGE_SERVICE_PRINCIPAL_DIRECTORY_FILE` (`{"issuer": ..., "users": {...}, "groups": {...}}`); without it only items a Source marks public are readable. A local-folder Source's readers are the Source row's `default_public` and `default_principals`; by default nobody may read. A synchronization message may run for 30 minutes; one cut off resumes from its checkpoint. A Source with a change feed gets a full pass every `KNOWLEDGE_SERVICE_FULL_SYNCHRONIZATION_INTERVAL_HOURS` (24 by default) and incremental passes between. A synchronization pass takes its Source's lease in Redis first; while Redis is down no pass starts, and its Job is retried later.
```

**Дія 3:** у `docs/threat-model.md` заміни рядок, що починається з "A file removed or unreadable", на:

```markdown
| A file removed or unreadable deletes or keeps the wrong Documents | An unreadable folder is unavailable, not deleted; a failed pass tombstones nothing; an incremental pass tombstones only what the Source reported deleted, and a full pass at least every `full_synchronization_interval_hours` tombstones what the feed failed to report | Between full passes, a deletion or a permission change the change feed does not report waits for the next full pass |
```

У рядку, що починається з "A stale or replayed worker rewrites a Job's progress", остання клітинка починається словами "No production handler reports progress until M08-T10;". Заміни їх на "A page that takes longer than recovery's five minutes makes a healthy worker look dead;".

**Перевірка:** `make docs-check` проходить.

### Крок 13 — У цьому кроці без коду: перевірки, evidence і прогрес

Цей крок запускає всі перевірки й завершує записи.

**Дія 1:** запусти всі перевірки, одну за одною:

```bash
make format-check
make lint
make typecheck
make test-unit
make test-integration
make docs-check
```

**Дія 2:** заверши файл evidence. Під "Verification" запиши результати перевірок, кількість із кроку 10 і рядки тренування. Під "Review" — знахідки рев'ю (блок 7) і відомі обмеження (блок 8). Під "Design" і "Decision" — дизайн, а під "Reflection" — свої відповіді на питання нижче. Потім познач завдання виконаним у `docs/curriculum/PROGRESS.md`, зроби наступним незаблокованим завданням M08-T11 і зроби коміт `m08-t10: orchestrate full and incremental sync`.

**Перевірка:** усі перевірки проходять, а `git status` після коміту чистий.

## Питання для роздумів

- Захоплення фіксується до початку проходу. Що сталося б із першим звітом про прогрес, якби захоплення, прохід і завершення ділили одну транзакцію?
- Відновлення позначило спробу 1 як невдалу, поки воркер спав. Коли воркер прокидається, які два факти перевіряє завершення і що повідомляє доставка?
- Стрічка змін повідомляє, що `a.txt` відредаговано, а `b.txt` видалено, і нічого не каже про `c.txt`. Чому інкрементальний прохід не має ставити tombstone `c.txt` і який прохід помітив би, якби `c.txt` справді видалили?
- Чому кожен повтор спостерігає джерело у свій час початку, а не в час, коли Job уперше поставили в чергу?
- Яка помилка ще може статися після того, як воркер повідомив про успіх? Подумай про зміну прав, про яку стрічка змін не повідомила.
- Який тест упав би першим, якби хтось згодом став брати попередження лише з останніх підсумків? А який — якби relay знову публікував лише `HANDLERS`?

## Чекліст завершення

- [ ] Файл evidence містить інваріант, власників і твої передбачення, записані до змін, а очікувано невдала перевірка показала `no tests ran`.
- [ ] Міграція `f2a3b4c5d6e7` і `schema.py` додають режим проходу, курсор, останній повний прохід, читачів теки й прапорець `deleted` у списку побаченого.
- [ ] `SourcePage.cursor`, `ChangeFeed`, `SyncMode`, три нові помилки та їхні коди відмов існують.
- [ ] `synchronization_progress.py`, `checkpoints.py`, `deletions.py`, `synchronization.py` і `exclusive_sync.py` обирають режим, читають за режимом, звітують про кожну сторінку й ставлять tombstone за режимом.
- [ ] `ingestion/synchronization_jobs.py` захоплює, запускає, звітує й завершує лише поки спроба досі його.
- [ ] Воркер будує обробники з налаштувань, запускає синхронізації з лімітом у 30 хвилин, а relay їх публікує.
- [ ] `tests/test_synchronization_jobs_integration.py` (25) і `tests/test_synchronization_job_wiring.py` проходять; чотири оновлені тести проходять.
- [ ] Навмисну поломку запущено, видно `1 failed, 24 passed`, і її скасовано.
- [ ] Тренування зі справжнім воркером зберегло обидва файли, а потім поставило tombstone видаленому.
- [ ] `docs/architecture.md`, `docs/operations.md` і `docs/threat-model.md` описують працюючу синхронізацію.
- [ ] Усі перевірки проходять: формат, lint, типи, unit, integration, docs.
- [ ] Evidence, статус завдання, рядок у PROGRESS і коміт готові.

Коли напишеш файли, поділись ними або виводом помилок, і я перевірю їх за цим чеклістом.
