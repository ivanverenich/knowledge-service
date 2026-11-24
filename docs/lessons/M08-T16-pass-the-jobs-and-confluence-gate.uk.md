English version: [M08-T16-pass-the-jobs-and-confluence-gate.md](M08-T16-pass-the-jobs-and-confluence-gate.md)

# M08-T16 — Підсумкова перевірка розділу про Jobs і Confluence

Завдання: [M08-T16 — Pass the jobs-and-Confluence understanding gate](../curriculum/milestones/M08-jobs-and-confluence/M08-T16-pass-the-jobs-and-confluence-gate.md)

## Що ти зробиш

Цей урок закриває розділ M08. Ти доводиш, що розумієш, як синхронізація переживає збій. Ти правильно її малюєш, навмисно ламаєш і дивишся, як вона відновлюється. Потім сам додаєш одне нове поле до інтерфейсу джерела (Source). Уроки про Confluence, з M08-T11 до M08-T15, відкладено, бо доступу до Confluence поки немає. Усе тут працює на джерелі-папці та на тестовому джерелі.

Конкретний приклад із тренування в кроці 8. У папці 150 файлів, від `doc-001.md` до `doc-150.md`. Тож прохід читає дві сторінки: 100 і 50 файлів. Процес воркера вбивають якраз перед тим, як він збереже `doc-120.md`.

| Час | Воркер | Relay (`make worker-relay`) | PostgreSQL | Redis |
|---|---|---|---|---|
| 17:07:47 | бере Job, читає сторінку 1, фіксує її, звітує `processed` 100 | | Job `running`; checkpoint `doc-100.md` | повідомлення у воркера, не підтверджене |
| 17:07:48 | зберігає `doc-101.md` … `doc-119.md` і гине | | 119 документів; Job досі `running` | повідомлення досі не підтверджене |
| 17:08:27 | стартує новий воркер і чекає | | нічого не змінюється | |
| 17:13:47 | | п'ять хвилин тиші: позначає спробу як `worker_lost` і планує повтор | Job у статусі failed, повтор приблизно за 15 секунд | |
| 17:14:05 | | повтор настав: знову ставить Job у чергу і публікує | Job `queued` | нове повідомлення |
| 17:14:07 | виконує спробу 2 від `doc-100.md`: `processed` 50, `changed` 31 | | Job `succeeded`; 150 документів | нове повідомлення підтверджене; повідомлення мертвого воркера досі в списку |

| Що вимагає завдання | Де це зроблено |
|---|---|
| Пояснити різницю між доставкою і бізнес-станом | Блок 1; крок 2 |
| Оновити схеми синхронізації | Крок 3 |
| Відновитися після навмисного збою посеред сторінки | Блоки 2 і 3; крок 6 (тести), крок 8 (справжній воркер) |
| Самостійно додати одне поле джерела | Блок 4; кроки 4 і 5 |
| Evidence доводить безпечність повтору і незмінну поведінку джерел | Кроки 6, 7 і 10 |
| Успіх, межі, типізовані помилки чи відмови, на справжній інфраструктурі | Кроки 5 і 6 (PostgreSQL), крок 8 (воркер, relay, Redis) |
| Поведінка при повторі й скасуванні описана явно | Блоки 1–3 |
| Пояснити, що спростувало б дизайн | Крок 7 і питання для роздумів |
| Оновити лише потрібну документацію | Кроки 3 і 9 |

Цей урок містить код: кроки 4, 5 і 6 змінюють код і додають тести. Кроки 1, 2, 3, 7, 8, 9 і 10 без коду. Хоча кроки 3 і 9 редагують документи, а крок 8 додає два тимчасові рядки, які ти потім прибираєш.

Терміни цього уроку:

- **Доставка (delivery):** повідомлення в Redis зі змістом «виконай Job J» і позначка, чи воркер його підтвердив. Воно несе лише запитання.
- **Бізнес-стан (business state):** те, що PostgreSQL записує як правду: статус Job і його спроби, checkpoint джерела, документи. Усі рішення ухвалюються за ним.
- **Підтвердження (ack):** воркер каже Redis: «я закінчив із цим повідомленням». Доти Redis тримає повідомлення, щоб віддати його іншому воркеру, якщо цей загине.
- **Повторна доставка (redelivery):** те саме повідомлення, віддане воркеру вдруге. Це нормально для черги, що доставляє **щонайменше один раз**.
- **Відновлення (recovery):** перевірка relay з M08-T08. Вона позначає як failed спробу, чий воркер мовчить п'ять хвилин.
- **Вебадреса (web URL):** адреса, за якою людина відкриває елемент у джерелі, наприклад сторінку вікі. Це нове поле цього уроку.
- **Якір (anchor):** текст, що зберігається з кожним Chunk і каже, звідки він, наприклад `leave.md#offset=0-21`. Цитата показує його як своє посилання.

## Перш ніж почати

Цей розділ пояснює кожне рішення, від якого залежить код уроку. Прочитай його першим: кроки спираються на нього.

### 1. Доставка — це запитання; відповідь тримає PostgreSQL

У тренуванні вище є два записи про ту саму роботу. Redis має повідомлення для Job, PostgreSQL має рядок Job. Після збою вони не збігаються, і вирішувати може лише один із них.

| Момент | Що каже Redis | Що каже PostgreSQL | Хто правий |
|---|---|---|---|
| Воркер гине посеред сторінки | повідомлення досі в того воркера | Job `running`, checkpoint `doc-100.md` | PostgreSQL: сторінку 1 зроблено, сторінку 2 ні |
| Redis віддає повідомлення новому воркеру | «виконай цей Job» | Job `running` | PostgreSQL: claim відмовляє, нічого не запускається |
| Відновлення позначає мовчазну спробу як failed | нічого не змінилося | failed, `worker_lost`, повтор заплановано | PostgreSQL |
| Спроба 2 успішна | повідомлення мертвого воркера досі в списку | `succeeded`, 150 документів | PostgreSQL |

Прихований базовий факт: Redis не знає, що зробила робота. Він знає лише, чи воркер сказав «я закінчив із повідомленням». Воркер може загинути, зберігши пів сторінки й нічого не сказавши. Тож повідомлення ніколи не скаже, чи робота відбулася. Це можуть сказати лише рядки, які робота записала.

Наскільки погано було б вірити повідомленню? Дуже погано. Воркер, що повторно виконав би Job через повідомлення, яке повернулося, прочитав би все джерело двічі. Гірше: старе повідомлення, що прийшло після новішого проходу, скасувало б його роботу. Тому кожен обробник спершу бере Job (claim) у PostgreSQL. Claim переводить `queued` у `running` під блокуванням рядка. Будь-який інший статус означає «його вже має хтось інший, або його завершено».

```python
# run_stored_job, in src/knowledge_service/worker/jobs.py
if job.status is not JobStatus.QUEUED:
    return _duplicate(job)
```

Побутова аналогія: накладна каже «доставити посилку 42». Складська книга каже, чи посилка 42 вже поїхала. Друга копія накладної не відправить другу посилку, бо комірник спершу дивиться в книгу.

### 2. Чому збій посеред сторінки нічого не губить

Прохід зберігає записи по одному, кожен у власній короткій транзакції. Checkpoint рухається лише після того, як збережено всю сторінку (M08-T09). Збій може статися будь-де в цій послідовності.

| Де гине воркер | Що вже зафіксовано | Що робить спроба 2 |
|---|---|---|
| перед `doc-101.md` | сторінка 1 і її checkpoint | читає сторінку 2 від `doc-100.md` |
| перед `doc-120.md` | сторінка 1, її checkpoint і `doc-101.md` … `doc-119.md` | читає сторінку 2 знову; 101–119 збігаються і нічого не змінюють; 120–150 нові |
| перед фіксацією сторінки 2 | усі записи сторінки 2, але не її checkpoint | читає сторінку 2 знову; усі 50 збігаються |
| усередині завершення | усі сторінки, але без tombstone | читає останню сторінку знову, потім ставить tombstone |

Прихований базовий факт: збереження запису **ідемпотентне**. Тобто зробити його двічі — те саме, що один раз, як двічі натиснути кнопку ліфта. Узгодження (reconciliation) порівнює відбитки (fingerprints) запису з тим, що збережено, і нічого не пише, коли вони збігаються. Тож повтор сторінки безпечний, а checkpoint має бути лише «не пізніше за правду», а не точним.

Саме тому спроба 2 у тренуванні звітує `processed` 50 і `changed` 31. Вона прочитала всі 50 файлів сторінки 2, але 19 із них уже були збережені.

Що це зламало б: checkpoint, зсунутий раніше, ніж збережено сторінку, або список «побачених» елементів лише в пам'яті. У кроці 7 ти навмисно ламаєш друге, і падає 11 тестів.

### 3. Повторно доставлене повідомлення і те, що залишилося

Після збою існують два різні повідомлення. Розгляньмо їх по черзі.

| Повідомлення | Коли приходить | Що тоді каже Job | Що робить воркер |
|---|---|---|---|
| Оригінал мертвого воркера | будь-коли, коли Redis його поверне | `running`, а пізніше `succeeded` | нічого не бере; пише в лог `duplicate message` |
| Нове повідомлення повтору | після того, як relay знову поставить Job у чергу | `queued` | бере його і виконує спробу 2 |

Dramatiq повертає непідтверджені повідомлення мертвого воркера лише під час свого час від часу обслуговування. Тож оригінал може повернутися за кілька хвилин або не повернутися за все тренування. Неважливо коли. Що робити, вирішує те, що воно знайде в PostgreSQL.

Тест у кроці 6 не може вбити справжній процес. Він зупиняє прохід скасуванням (cancellation), яке нічого не записує, так само як вбивство процесу. Потім доставляє те саме повідомлення ще раз і перевіряє, що воно нічого не запускає. Крок 8 робить справжнє вбивство через `make worker`.

### 4. Нове поле: де людина відкриває елемент

Завдання просить «самостійно» додати одне поле до інтерфейсу джерела. Воно має пройти від джерела до місця, де його бачить користувач, і нічого збереженого раніше не зламати. Ми додаємо `web_url`: адресу сторінки елемента в його джерелі. У локальної папки її немає. У Confluence вона буде для кожної сторінки. Сьогодні посилання цитати для файлу з папки — це просто ім'я файлу, наприклад `leave.md#offset=0-21`. З вебадресою воно стає `https://wiki.example.com/spaces/HR/pages/42#offset=0-21`.

Ось шлях поля:

| Етап | Файл | Що відбувається |
|---|---|---|
| Джерело його повідомляє | `ingestion/sources.py` | `SourceMetadata.web_url`; відхиляється, якщо це не текст, якщо воно порожнє або довше за 2048 символів |
| Нормалізація | `ingestion/normalization.py` | зберігається лише в безпечній формі і лише тоді, коли цитата покаже його без змін; інакше `None` |
| Відбиток | `ingestion/fingerprints.py` | враховується лише тоді, коли є |
| Chunks | `ingestion/reconciliation.py` | передається в якорі, тож кожен Chunk веде на сторінку |
| Цитата | `ingestion/provenance.py` | зберігає власну частину якоря `#offset=` або `#page=` |

Чому відбиток має його враховувати? Chunk зберігає свій якір. Chunks переписуються лише тоді, коли змінюється відбиток вмісту. Без адреси у відбитку сторінка, що переїхала, назавжди зберегла б старі посилання.

| Було | Стало |
|---|---|
| `{"text": ..., "blocks": ...}` | `{"text": ..., "blocks": ..., "web_url": ...}`, останній ключ лише тоді, коли адреса є |

Чому лише тоді, коли є? Відбиток кожного вже збереженого документа обчислено без цього ключа. Якби всюди додати `"web_url": None`, змінився б кожен відбиток. Наступний прохід переписав би всі Chunks усіх документів, і всім знадобилися б нові embeddings. Якщо ключа немає, коли немає адреси, кожен старий відбиток лишається таким самим байт у байт. Наявний тест `test_the_canonical_form_does_not_drift` фіксує один такий відбиток і падає, якщо додавати ключ завжди. Ось що тут означає «незмінна поведінка джерел»: джерело, яке не надсилає поле, не бачить жодних змін.

Ціна: коли сторінка переїжджає, її Chunks переписуються один раз і потребують нових embeddings. Версія вмісту стає 2 замість 1. Старі відповіді, що її цитували, відкликає перевірка цитат. Це правильно, бо їхні посилання застаріли.

### 5. Знахідка рецензії: цитата відрізала місце на сторінці

Незалежна рецензія першої версії показала: якір `https://wiki.example.com/p/42#offset=0-10` зберігався правильно. Але цитата показувала лише `https://wiki.example.com/p/42`. Функція, що чистить текст для показу, `display_text`, відрізає все після `?` або `#` у кожній вебадресі. Тож усі Chunks сторінки отримували одне й те саме посилання.

Виправлення залишає рівно один вид фрагмента: власний фрагмент якоря `#offset=START-END` або `#page=N`. Усе інше після `#` і далі відрізається, тож `#comments` чи `#offset=0-21&token=SECRET` ніколи не дійдуть до користувача.

```python
# before
return display_url(_QUERY_OR_FRAGMENT.split(url, 1)[0]) or "[link]"
```

```python
# after (shortened)
fragment = url[url.find("#") :] if "#" in url else ""
return shown + fragment if _ANCHOR_FRAGMENT.fullmatch(fragment) else shown
```

### 6. Знахідка рецензії: збережене має збігатися з показаним

Рецензія знайшла адресу на кшталт `https://wiki.example.com/x:SECRET@y`. Функція безпечної форми її залишала, бо шлях вона зберігає. Але цитата прибирає `x:SECRET@` як пароль, тож користувач побачив би `https://wiki.example.com/y`, тобто іншу сторінку. А збережений якір досі містив би `SECRET`.

Тепер нормалізація залишає адресу лише тоді, коли очищення для показу нічого в ній не змінює. Таку адресу відкидають, і елемент повертається до свого зовнішнього ID, ніби адреси не було.

### 7. Знахідка рецензії: вебадреса, яка не є текстом

Рецензія передала як вебадресу байти, `b"https://..."`. У байтів є `strip()` і довжина, тож перевірки пройшли. Потім нормалізація аварійно зупинилася з `TypeError`, тобто з помилкою, якої ніхто не планував. Тепер `SourceMetadata` відхиляє все, що не є `str`, помилкою `InvalidSource`, власною помилкою контракту джерела.

### 8. Знахідка рецензії: порівняння з чистим проходом не досить

Тест збою запускає два однакові джерела: одне переривають, інше ні. Потім порівнює все збережене. Рецензія запитала: а якщо помилка ламає обидва проходи однаково? Ми спробували: з порожнім списком «побачених» обидва проходи поставили tombstone тим самим неправильним документам, і порівняння пройшло.

Тому тест тепер перевіряє ще й сам очікуваний результат: які документи доступні, їхні версії вмісту і хто може їх читати. Він також порівнює більше, ніж перша версія: текст і якорі Chunks, назви, відбитки, читачів, усі події аудиту кожного документа, checkpoint джерела і події `synchronization.finished`, purge Jobs для кожного джерела окремо і кількість спроб.

### 9. Відомі обмеження

- Секрет, записаний у сам шлях адреси, зберігається, бо шлях зберігають навмисно.
- Два написання однієї сторінки, наприклад із явним портом `:443`, вважаються переїздом і один раз переписують її Chunks.
- У воркері поки ніщо не публікує embeddings, тож нові Chunks сторінки, що переїхала, на це чекають.
- Тест «вбиває» скасуванням, а не справжнім завершенням процесу. Справжнє вбивство показує тренування в кроці 8.
- M08-T11 … M08-T15 (Confluence) відкладено. Поки вебадресу надсилає лише тестове джерело.

## Покрокові інструкції

Перед початком запусти PostgreSQL і Redis (`make redis-up`). Виконуй усі команди з кореня репозиторію. Для інтеграційних перевірок задай у терміналі адресу тестової бази:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

Запускай проти цієї бази лише одну інтеграційну команду за раз.

### Крок 1 — У цьому кроці без коду: запиши інваріант і свої передбачення

Перш ніж щось змінювати, запиши, що має бути правдою і що ти очікуєш побачити. Передбачення ти перевіриш у кроках 7, 8 і 10.

Створи `docs/evidence/M08-T16-jobs-and-confluence-gate.md`:

```markdown
# M08-T16 — Jobs-and-Confluence gate evidence

## Invariant

A synchronization's truth lives only in PostgreSQL: the Job's status and
attempts, the Source's checkpoint and seen IDs, and the Documents. A Redis
message only asks a worker to look. A worker killed at any point of a pass
loses at most its uncommitted work: a redelivered message finds the Job
running and runs nothing, recovery fails the silent attempt, and the retry
resumes from the last committed page and ends with exactly what an
uninterrupted pass stores. A Source item's `web_url` reaches every Chunk's
anchor and Citation only in a safe form that a Citation shows unchanged;
items without one keep their stored fingerprints.

## Owners

- Business state and every decision: PostgreSQL.
- Delivery: Redis and Dramatiq, at least once.
- Retries and recovery of a lost worker: the stored Job (M08-T04) and
  `make worker-relay` (M08-T08).
- Resume point: `ingestion.checkpoints` (M08-T09).
- What a Citation may show: `ingestion.provenance` and normalization.

## Expected failing check

Before the change,
`uv run pytest tests/test_synchronization_gate_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.

## Predictions

- The worker is killed before `doc-120.md` of 150 files, in pages of 100. What do attempt 2's `processed` and `changed` say:
- Right after the kill, what does the Job's row say, and where is its message:
- A page's web URL moves and its text stays. Which of the Document's two versions change:
- How many tests in the gate suite fail if a page commit stores no seen IDs:

## Delivery versus business state

## Sync diagrams

## Injected crash

## New Source field

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** запусти перевірку, що має падати, і побач `no tests ran`. Після двокрапки в кожному передбаченні стоїть твоя відповідь.

### Крок 2 — У цьому кроці без коду: поясни різницю між доставкою і бізнес-станом

Цей крок переказує блок 1 твоїми словами в evidence-файлі. Це головна ідея, яку перевіряє урок.

Під `## Delivery versus business state` напиши:

- одну таблицю з двома стовпцями, Redis і PostgreSQL, і такими рядками: що він зберігає, на яке запитання може відповісти, що з ним стається, коли воркер гине посеред сторінки, і хто його читає перед тим, як щось запустити;
- одне речення про те, чому повторно доставлене повідомлення нічого не запускає;
- одне речення про те, чому повідомлення, що лишилося в Redis, нешкідливе навіть після успіху Job.

Якщо допомагає, використай числа тренування з розділу «Що ти зробиш».

**Перевірка:** читач, який нічого не знає про Dramatiq, з твого тексту зрозуміє, яку систему питати «чи завершився прохід?» і чому.

### Крок 3 — У цьому кроці без коду: онови схеми синхронізації

Перша схема в `docs/architecture.md` під «Synchronization» — це ціль, намальована на початку курсу. Тепер вона неправильна: черга несе лише ID Job, а не «source + checkpoint», і в проході немає embeddings. Цей крок замінює її на те, що побудовано, і додає схему статусів Job.

**Дія 1:** у `docs/architecture.md` заміни все від `### Synchronization` до абзацу, що починається з «The diagram above is the target.», включно з ним, на:

````markdown
### Synchronization

Two systems carry a synchronization, and only one of them holds the truth.
Redis holds **delivery**: a message that says "run Job J" and whether a
worker has acknowledged it. PostgreSQL holds **business state**: the Job's
status and attempts, the Source's checkpoint and seen IDs, and the
Documents. A message can arrive twice, late, or after its worker died; every
decision is made from PostgreSQL, so a message only ever asks a question.

```mermaid
sequenceDiagram
    participant Relay as worker-relay
    participant DB as PostgreSQL (business state)
    participant Redis as Redis (delivery)
    participant Worker
    participant Source
    Relay->>DB: fail attempts silent for five minutes and queued Jobs whose message is 30 minutes old, requeue due retries, store due synchronize_source Jobs
    Relay->>Redis: publish one message per unpublished Job: its ID only
    Redis->>Worker: deliver at least once; again if a worker died before acknowledging
    Worker->>DB: claim: queued to running with an attempt row; any other status runs nothing
    Worker->>Redis: take the Source's lease
    loop each page, from the stored checkpoint
        Worker->>Source: fetch_page, or fetch_changes in an incremental pass
        Worker->>DB: narrow removed readers, then reconcile each record, one transaction each
        Worker->>DB: commit_page: checkpoint and seen IDs, one fenced transaction
        Worker->>DB: report the attempt's progress, its sign of life
    end
    Worker->>DB: finish_pass: tombstones, purge Jobs, cursor
    Worker->>DB: store the end only if the Job still runs this attempt
    Worker->>Redis: acknowledge the message
```

A Job's status moves only in PostgreSQL, and every move is checked under
the Job's row lock:

```mermaid
stateDiagram-v2
    [*] --> queued: stored, then published
    queued --> running: a worker claims it
    running --> succeeded: the pass ended
    running --> failed: a typed failure, or recovery after five silent minutes
    failed --> queued: retry time reached with attempts left (worker-relay), or an operator reopens it
    queued --> cancelled: an operator cancels it
    failed --> cancelled: an operator cancels it
    succeeded --> [*]
    failed --> [*]: permanent, or no attempts left, until an operator reopens it
    cancelled --> [*]
```

A worker killed in the middle of a page loses only that page's uncommitted
work. Its message stays unacknowledged in Redis, and a redelivery finds the
Job running and runs nothing. Recovery fails the silent attempt as
`worker_lost`, the relay requeues it at its retry time, and the next attempt
resumes from the last committed page; records of the killed page that had
already landed compare equal and change nothing.
`tests/test_synchronization_gate_integration.py` stops a worker for good,
by cancelling it, at every step of a pass and compares the result with a
pass nobody interrupted; a cancellation still runs cleanup that a killed
process skips, so the real kill is a drill (`docs/operations.md`).
Embeddings are not part of the pass: `publish_document_embeddings` runs
separately.

#### Synchronization as built in M03

One call, `synchronize_source`, runs a complete pass; since M08-T10 the
worker runs it for every `synchronize_source` Job, as drawn above.
````

**Дія 2:** звір кожен перехід статусу на другій схемі з `_ALLOWED` у `src/knowledge_service/jobs.py`. Кожна стрілка має бути переходом, який дозволяє `_ALLOWED`, і кожен дозволений перехід має мати стрілку.

**Перевірка:** `make docs-check` проходить. Під `## Sync diagrams` в evidence-файлі перелічи, що стара схема показувала неправильно.

### Крок 4 — Крок із кодом: нове поле, від джерела до цитати

**Простими словами:** тепер джерело може сказати, де людина відкриває кожен елемент, наприклад адресу сторінки вікі. Сервіс перевіряє її, залишає лише безпечну для показу версію і робить так, щоб кожен збережений шматок тексту елемента вів на цю сторінку. Якщо адреса зміниться, шматки переписуються один раз. Якщо джерело ніколи не надсилає адресу, у його збережених елементах нічого не змінюється.

| Файл | Зміна |
|---|---|
| `ingestion/sources.py` | `MAX_WEB_URL_CHARACTERS` і `SourceMetadata.web_url` із трьома перевірками |
| `ingestion/normalization.py` | `NormalizedDocument.web_url` і `_shown_url` |
| `ingestion/fingerprints.py` | `content_fingerprint` приймає `web_url` |
| `ingestion/reconciliation.py` | передає її у відбиток і в якорі |
| `ingestion/provenance.py` | цитата зберігає власний фрагмент якоря |

**Дія 1:** у `src/knowledge_service/ingestion/sources.py` заміни клас `SourceMetadata` на:

```python
# Longer links are refused: no real page address comes near it.
MAX_WEB_URL_CHARACTERS = 2048


@dataclass(frozen=True, slots=True)
class SourceMetadata:
    """Descriptive facts a Source reports about one item, before normalization.

    `web_url` is where a person opens the item in its Source, such as a wiki
    page's address, or None when the Source has no such page. It is taken
    as the Source gives it; normalization keeps it only if it is safe to show.
    """

    title: str
    modified_at: datetime
    size_bytes: int
    web_url: str | None = None

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise InvalidSource("source metadata title must not be empty")
        _require_utc(self.modified_at, "modified_at")
        if self.size_bytes < 0:
            raise InvalidSource("source metadata size must not be negative")
        if self.web_url is not None:
            if type(self.web_url) is not str:
                raise InvalidSource("a web URL must be text")
            if not self.web_url.strip():
                raise InvalidSource("a web URL must not be empty when provided")
            if len(self.web_url) > MAX_WEB_URL_CHARACTERS:
                raise InvalidSource(
                    f"a web URL must be at most {MAX_WEB_URL_CHARACTERS} characters"
                )
```

`type(...) is not str` іде першою, бо байти пройшли б дві інші перевірки (блок 7).

**Дія 2:** у `src/knowledge_service/ingestion/normalization.py` додай цей імпорт після імпорту `text_parser`:

```python
from knowledge_service.ingestion.provenance import display_text, display_url
```

Додай поле в кінець `NormalizedDocument`, після `parsed`:

```python
    # The item's page in its Source, already safe to show, or None.
    web_url: str | None = None
```

У поля є значення за замовчуванням, тож усі інші місця, що створюють `NormalizedDocument`, працюють далі. У `normalize` додай останній аргумент виклику `NormalizedDocument(...)`, а одразу після `normalize` додай `_shown_url`:

```python
        parsed=parsed,
        web_url=_shown_url(metadata.web_url if metadata is not None else None),
    )


def _shown_url(web_url: str | None) -> str | None:
    """Keep a web URL only in the safe form a Citation shows unchanged.

    A link a Citation would show differently, such as a path segment shaped
    like `name:password@`, is dropped, so what is stored is what is shown.
    """
    shown = None if web_url is None else display_url(web_url)
    return shown if shown is not None and display_text(shown) == shown else None
```

`display_url` дає безпечну форму або `None` для всього, що не є звичайною сторінкою `http` чи `https`. Друга перевірка, `display_text(shown) == shown`, — це блок 6: збережене має збігатися з тим, що покаже цитата.

**Дія 3:** у `src/knowledge_service/ingestion/fingerprints.py` заміни `content_fingerprint` на:

```python
def content_fingerprint(parsed: ParsedDocument, *, web_url: str | None = None) -> str:
    """Return the fingerprint of a document's text and structure.

    Everything the chunker reads counts: the parsed text and every field of
    every block, including offsets, because citations point into the text.
    So does `web_url`, the page every Chunk's anchor links to; without one
    the fingerprint is the same as before the field existed.
    """
    blocks = [
        {
            "kind": block.kind.value,
            "text": block.text,
            "start": _number(block.start),
            "end": _number(block.end),
            "heading_level": _number(block.heading_level),
            "list_depth": _number(block.list_depth),
            "language": block.language,
            "links": [[link.text, link.target] for link in block.links],
            "page": _number(block.page),
        }
        for block in parsed.blocks
    ]
    value: dict[str, object] = {"text": parsed.text, "blocks": blocks}
    if web_url is not None:
        value["web_url"] = web_url
    return canonical_hash(_CONTENT_LABEL, value)
```

Ключ додається лише тоді, коли є адреса (блок 4).

**Дія 4:** у `src/knowledge_service/ingestion/reconciliation.py` зміни перший рядок тіла `reconcile_document` після `provenance = ...`:

```python
    content = content_fingerprint(normalized.parsed, web_url=normalized.web_url)
```

У `build_chunks` передай адресу в `chunk_sources`, де параметр `source_url` уже на неї чекав:

```python
    sources = chunk_sources(
        normalized.parsed,
        drafts,
        external_id=normalized.provenance.external_id,
        source_url=normalized.web_url,
    )
```

**Дія 5:** у `src/knowledge_service/ingestion/provenance.py` додай цей шаблон після `_QUERY_OR_FRAGMENT`:

```python
# The fragment an anchor adds after its location: where the Chunk starts.
_ANCHOR_FRAGMENT = re.compile(r"#(?:offset=\d+-\d+|page=\d+)")
```

і заміни `_shown_url` на:

```python
def _shown_url(url: str) -> str:
    """Show an address found in text; its query and fragment are cut off first.

    Only an anchor's own fragment, `#offset=START-END` or `#page=N`, is kept,
    so a Citation of a page with a web URL still leads to its place in it.
    """
    shown = display_url(_QUERY_OR_FRAGMENT.split(url, 1)[0])
    if shown is None:
        return "[link]"
    fragment = url[url.find("#") :] if "#" in url else ""
    return shown + fragment if _ANCHOR_FRAGMENT.fullmatch(fragment) else shown
```

`fullmatch` приймає фрагмент лише тоді, коли він рівно у формі якоря. Тож `#offset=0-21&token=SECRET` і далі відрізається (блок 5).

**Перевірка:** `uv run ruff format . && uv run ruff check . && uv run pyright` не показують помилок, а `make test-unit` і далі проходить, разом із `test_the_canonical_form_does_not_drift`.

### Крок 5 — Крок із кодом: unit-тести для поля

**Простими словами:** ці тести перевіряють поле ще до бази. Вони пробують порожні, задовгі й нетекстові адреси; адреси з паролями, токенами, неправильними схемами і невидимими символами. А ще перевіряють, що цитата зберігає місце на сторінці, але нічого іншого після `#`.

Створи `tests/test_source_web_url.py`:

```python
"""The `web_url` a Source reports for an item, before anything is stored."""

from datetime import UTC, datetime

import pytest

from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.fingerprints import content_fingerprint
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.provenance import display_text
from knowledge_service.ingestion.sources import (
    MAX_WEB_URL_CHARACTERS,
    InvalidSource,
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-0000000016c3")
WIKI = "https://wiki.example.com/spaces/HR/pages/42"


def metadata(web_url: str | None) -> SourceMetadata:
    return SourceMetadata(
        title="Leave",
        modified_at=datetime(2026, 1, 1, tzinfo=UTC),
        size_bytes=0,
        web_url=web_url,
    )


def normalized(web_url: str | None) -> NormalizedDocument:
    record = SourceRecord.present(
        external_id="leave.txt",
        version="v1",
        media_type="text/plain",
        content=b"Twenty days a year.",
        access=SourceAccess(public=True),
        metadata=metadata(web_url),
    )
    outcome = normalize(record, source_id=SOURCE_ID)
    assert isinstance(outcome, NormalizedDocument)
    return outcome


@pytest.mark.parametrize("web_url", ["", "   "])
def test_an_empty_web_url_is_refused(web_url: str) -> None:
    with pytest.raises(InvalidSource, match="must not be empty"):
        metadata(web_url)


def test_a_web_url_longer_than_the_limit_is_refused() -> None:
    too_long = WIKI + "/" + "x" * MAX_WEB_URL_CHARACTERS

    with pytest.raises(InvalidSource, match="at most"):
        metadata(too_long)


def test_metadata_without_a_web_url_still_works() -> None:
    assert (
        SourceMetadata(
            title="Leave", modified_at=datetime(2026, 1, 1, tzinfo=UTC), size_bytes=0
        ).web_url
        is None
    )


@pytest.mark.parametrize(
    ("given", "kept"),
    [
        (WIKI, WIKI),
        (f"{WIKI}?token=SECRET#comments", WIKI),
        ("https://reader:hunter2@wiki.example.com/x", "https://wiki.example.com/x"),
        ("HTTPS://Wiki.Example.com/x", "https://wiki.example.com/x"),
        ("javascript:alert(1)", None),
        ("file:///etc/passwd", None),
        ("https://wiki.example.com/x\u202e", None),
        # A Citation would show this as another page, so it is not kept.
        ("https://wiki.example.com/x:SECRET@y", None),
        (None, None),
    ],
)
def test_normalization_keeps_only_the_safe_form_of_a_web_url(
    given: str | None, kept: str | None
) -> None:
    assert normalized(given).web_url == kept


def test_a_web_url_changes_the_fingerprint() -> None:
    parsed = normalized(None).parsed

    assert content_fingerprint(parsed, web_url=WIKI) != content_fingerprint(parsed)
    assert content_fingerprint(parsed, web_url=WIKI) != content_fingerprint(
        parsed, web_url=WIKI + "/moved"
    )


def test_a_web_url_that_is_not_text_is_refused() -> None:
    with pytest.raises(InvalidSource, match="must be text"):
        metadata(b"https://wiki.example.com/x")  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("fragment", ["#offset=0-21", "#page=3"])
def test_a_citation_keeps_the_place_in_the_page(fragment: str) -> None:
    assert display_text(WIKI + fragment) == WIKI + fragment


@pytest.mark.parametrize(
    "fragment", ["#comments", "#offset=0-21&token=SECRET", "#page=3?x", "#"]
)
def test_any_other_fragment_is_still_cut(fragment: str) -> None:
    assert display_text(WIKI + fragment) == WIKI
```

Рядок `"https://wiki.example.com/x\u202e"` має містити шість символів: зворотну скісну риску, `u`, `2`, `0`, `2`, `e`, а не невидимий символ, який вони позначають. Деякі редактори перетворюють escape на сам символ. Перевір так: `grep -n 'u202e' tests/test_source_web_url.py`.

**Перевірка:** `uv run pytest tests/test_source_web_url.py -q` дає `21 passed`.

### Крок 6 — Крок із кодом: набір тестів уроку на справжньому PostgreSQL

**Простими словами:** цей файл запускає справжні Jobs синхронізації проти PostgreSQL. У першій частині воркер назавжди зупиняється в одній точці проходу. Далі тест дає статися всьому, що сталося б у роботі: повідомлення повертається, відновлення відмовляється від воркера, запускається повтор. Потім тест порівнює збережене з проходом, який ніхто не переривав, і з очікуваним результатом. І так для дев'яти різних точок. Друга частина проводить вебадреси через цілий Job і перевіряє, куди веде кожен збережений шматок тексту.

| Тест | Що доводить |
|---|---|
| `test_a_worker_killed_mid_pass_ends_as_if_nothing_happened`, 9 точок збою | безпечність повтору: повторна доставка нічого не запускає, відновлення ставить `worker_lost`, повтор продовжує від збереженого checkpoint, результат дорівнює чистому проходу й очікуваному |
| `test_a_web_url_becomes_where_every_chunk_leads` | поле доходить до якорів |
| `test_a_moved_page_rewrites_its_chunks_and_nothing_else` | переїзд: версія вмісту 2, версія доступу 1 |
| `test_the_same_web_url_again_changes_nothing` | ідемпотентність |
| `test_a_new_token_in_the_same_link_changes_nothing` | важить лише безпечна форма |
| `test_a_secret_in_a_web_url_never_reaches_a_chunk` | жодного пароля чи токена в якорі |
| `test_an_unsafe_web_url_falls_back_to_the_external_id` | `javascript:` відкидається |
| `test_a_source_that_starts_sending_links_rewrites_the_chunks_once` | джерело, яке додає поле пізніше |

Як працює «вбивство»: тест підміняє один крок проходу, наприклад `reconcile_document`, версією, що назавжди зависає на n-му виклику. Потім тест скасовує задачу воркера. Після цієї точки нічого не виконується, тож нічого більше не записується, як у вбитому процесі. `pay.txt` публічний у першому проході, а в другому називає невідомого принципала. Тож другий прохід забирає його в усіх. Так перевіряється звуження доступу, яке йде перед узгодженням.

Створи `tests/test_synchronization_gate_integration.py`:

```python
"""The M08 gate on real PostgreSQL: a worker killed mid-page, and a new field.

The first part kills a synchronization Job's worker before each record of a
pass in turn, redelivers its message, lets recovery and the retry finish
the pass, and compares everything stored with a pass nobody interrupted.
The second part sends a Source item's web address, `web_url`, through the
same Job and checks where every Chunk's anchor leads.
"""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

import knowledge_service.ingestion.synchronization as synchronization
from fakes import FakeSource
from knowledge_service.audit import AuditAction
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import JobId, SourceId
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourcePort,
    SourceRecord,
)
from knowledge_service.ingestion.synchronization_jobs import SynchronizationJobs
from knowledge_service.job_dispatch import requeue_due_retries
from knowledge_service.job_failures import JobFailure, RetryPolicy
from knowledge_service.job_recovery import recover_stuck_jobs
from knowledge_service.jobs import Job, JobKind, JobStatus
from knowledge_service.leases import Lease, LeaseStore
from knowledge_service.persistence.jobs import enqueue_job, load_job
from knowledge_service.persistence.schema import (
    access_grants,
    audit_events,
    chunks,
    documents,
    job_attempts,
    jobs,
    sources,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.jobs import JobOutcome, run_stored_job

pytestmark = pytest.mark.integration

CLEAN = SourceId.parse("00000000-0000-0000-0000-0000000016c1")
CRASHED = SourceId.parse("00000000-0000-0000-0000-0000000016c2")
RETRIES = RetryPolicy(
    base_delay=timedelta(seconds=1),
    max_delay=timedelta(seconds=1),
    jitter=lambda: 0.0,
)


class MemoryLeases:
    """A lease store that always grants and renews; it holds no state."""

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
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


async def register(runtime: DatabaseRuntime, source_id: SourceId) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.insert(sources).values(
                source_id=source_id.value,
                kind="local_directory",
                location=f"/srv/{source_id}",
                display_name="Handbook",
                enabled=True,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
                default_public=True,
            )
        )


async def queue(runtime: DatabaseRuntime, source_id: SourceId, at: datetime) -> JobId:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=source_id.value,
        queued_at=at,
    )
    async with runtime.transaction() as connection:
        return (await enqueue_job(connection, job)).job.job_id


def handler(source: SourcePort, at: datetime) -> SynchronizationJobs:
    """The real handler, opening `source` for every stored Source."""

    @asynccontextmanager
    async def memory() -> AsyncGenerator[LeaseStore]:
        yield MemoryLeases()

    return SynchronizationJobs(
        open_source=lambda _settings: source,
        leases=memory,
        principal_directory=None,
        full_pass_interval=timedelta(hours=24),
        retry_policy=RETRIES,
        clock=lambda: at,
    )


async def deliver(
    runtime: DatabaseRuntime, job_id: JobId, source: SourcePort, at: datetime
) -> JobOutcome:
    """Deliver the Job's message once, the way a worker does."""
    return await run_stored_job(
        runtime,
        job_id,
        now=at,
        handlers={JobKind.SYNCHRONIZE_SOURCE: handler(source, at)},
    )


async def sync(
    runtime: DatabaseRuntime, source_id: SourceId, source: SourcePort, at: datetime
) -> Job:
    """Queue one Job at `at` and run it to its end."""
    job_id = await queue(runtime, source_id, at)
    return (await deliver(runtime, job_id, source, at)).job


def item(
    name: str,
    text: str = "",
    *,
    web_url: str | None = None,
    principals: tuple[str, ...] = (),
) -> SourceRecord:
    """A text item; public unless it names principals."""
    return SourceRecord.present(
        external_id=name,
        version="v1",
        media_type="text/plain",
        content=(text or f"The {name} policy.").encode(),
        access=SourceAccess(public=not principals, principals=principals),
        metadata=SourceMetadata(
            title=name,
            modified_at=datetime(2026, 1, 1, tzinfo=UTC),
            size_bytes=0,
            web_url=web_url,
        ),
    )


@dataclass(frozen=True)
class Stored:
    """Everything a pass leaves behind for one Document, without its IDs."""

    title: str
    content_fingerprint: str
    content_version: int
    authorization_version: int
    availability: str
    chunks: tuple[tuple[str, str], ...]
    readers: tuple[str, ...]
    events: tuple[str, ...]

    @property
    def anchors(self) -> tuple[str, ...]:
        return tuple(anchor for _, anchor in self.chunks)


async def snapshot(runtime: DatabaseRuntime, source_id: SourceId) -> dict[str, Stored]:
    """What the Source's Documents look like, keyed by their external ID."""
    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(documents).where(documents.c.source_id == source_id.value)
            )
        ).mappings()
        stored: dict[str, Stored] = {}
        for row in rows:
            document_id = row["document_id"]
            texts = await connection.execute(
                sa.select(chunks.c.text, chunks.c.source_anchor)
                .where(chunks.c.document_id == document_id)
                .order_by(chunks.c.ordinal)
            )
            readers = await connection.scalars(
                sa.select(access_grants.c.subject_kind).where(
                    access_grants.c.document_id == document_id
                )
            )
            events = await connection.scalars(
                sa.select(audit_events.c.action).where(
                    audit_events.c.target_id == document_id
                )
            )
            stored[row["external_id"]] = Stored(
                title=row["title"],
                content_fingerprint=row["content_fingerprint"],
                content_version=row["content_version"],
                authorization_version=row["authorization_version"],
                availability=row["availability"],
                chunks=tuple((text, anchor or "") for text, anchor in texts),
                readers=tuple(sorted(readers)),
                events=tuple(sorted(events)),
            )
    return stored


@dataclass(frozen=True)
class SourceState:
    """What a pass leaves on the Source itself."""

    checkpoint: str | None
    pass_mode: str | None
    finished_events: int
    purges: int


async def source_state(runtime: DatabaseRuntime, source_id: SourceId) -> SourceState:
    async with runtime.transaction() as connection:
        row = (
            await connection.execute(
                sa.select(sources.c.checkpoint, sources.c.pass_mode).where(
                    sources.c.source_id == source_id.value
                )
            )
        ).one()
        finished = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(audit_events)
            .where(
                audit_events.c.action == AuditAction.SYNCHRONIZATION_FINISHED.value,
                audit_events.c.target_id == source_id.value,
            )
        )
        purges = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(jobs)
            .where(
                jobs.c.kind == JobKind.PURGE_DOCUMENT_CHUNKS.value,
                jobs.c.target_id.in_(
                    sa.select(documents.c.document_id).where(
                        documents.c.source_id == source_id.value
                    )
                ),
            )
        )
    return SourceState(row.checkpoint, row.pass_mode, finished or 0, purges or 0)


async def attempts(runtime: DatabaseRuntime, job_id: JobId) -> int:
    async with runtime.transaction() as connection:
        return (
            await connection.scalar(
                sa.select(sa.func.count())
                .select_from(job_attempts)
                .where(job_attempts.c.job_id == job_id.value)
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


# --- A worker killed mid-page

FIRST: Sequence[Sequence[SourceRecord]] = [
    [item("a.txt"), item("b.txt")],
    [item("pay.txt"), item("old.txt")],
]
# Pass two edits b.txt, takes pay.txt away from everyone it could resolve,
# adds c and e, and no longer lists old.txt.
SECOND: Sequence[Sequence[SourceRecord]] = [
    [item("a.txt"), item("b.txt", "The b.txt policy, rewritten.")],
    [item("c.txt"), item("pay.txt", principals=("payroll",))],
    [item("e.txt")],
]
# After pass two: old.txt is tombstoned, b.txt has new text, and nobody
# this service knows may read pay.txt any more.
EXPECTED = {
    "a.txt": ("available", 1, ("public",)),
    "b.txt": ("available", 2, ("public",)),
    "c.txt": ("available", 1, ("public",)),
    "e.txt": ("available", 1, ("public",)),
    "old.txt": ("tombstoned", 1, ("public",)),
    "pay.txt": ("available", 1, ()),
}
# Where the worker dies: before the n-th call of one step of the pass.
KILL_POINTS = [
    *(("reconcile_document", n) for n in range(1, 6)),
    *(("commit_page", n) for n in range(1, 4)),
    ("finish_pass", 1),
]


@pytest.mark.parametrize(("step", "call"), KILL_POINTS)
async def test_a_worker_killed_mid_pass_ends_as_if_nothing_happened(
    runtime: DatabaseRuntime,
    monkeypatch: pytest.MonkeyPatch,
    step: str,
    call: int,
) -> None:
    """Kill the worker before call `call` of `step` in the second pass."""
    earlier = datetime.now(UTC) - timedelta(hours=2)
    for source_id in (CLEAN, CRASHED):
        await register(runtime, source_id)
        await sync(runtime, source_id, FakeSource(FIRST), earlier)
    now = datetime.now(UTC)
    clean = await sync(runtime, CLEAN, FakeSource(SECOND), now)
    assert clean.status is JobStatus.SUCCEEDED

    # The injected crash: the worker stops for good at one exact point and
    # is cancelled, so it records nothing more. Unlike a killed process, a
    # cancellation still rolls back and releases; a real kill is a drill.
    real = getattr(synchronization, step)
    reached = asyncio.Event()
    calls = 0

    async def dies_here(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == call:
            reached.set()
            await asyncio.Event().wait()
        return await real(*args, **kwargs)

    monkeypatch.setattr(synchronization, step, dies_here)
    job_id = await queue(runtime, CRASHED, now)
    worker = asyncio.create_task(deliver(runtime, job_id, FakeSource(SECOND), now))
    # A kill point the pass never reaches fails here instead of hanging.
    await asyncio.wait_for(reached.wait(), timeout=30)
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker
    monkeypatch.setattr(synchronization, step, real)

    # Delivery: the broker gives the message to another worker. The Job is
    # still running in PostgreSQL, so that delivery runs nothing.
    redelivered = FakeSource(SECOND)
    duplicate = await deliver(runtime, job_id, redelivered, now)
    assert duplicate.duplicate is True
    assert redelivered.checkpoints == []

    # Business state: only recovery, five silent minutes later, may decide
    # the worker is gone and plan the retry.
    await fall_silent(runtime, job_id)
    recovered = await recover_stuck_jobs(
        runtime, now=datetime.now(UTC), retry_policy=RETRIES
    )
    assert recovered.stale == (job_id,)
    async with runtime.transaction() as connection:
        lost = await load_job(connection, job_id=job_id)
    assert lost is not None
    assert lost.last_error_code == JobFailure.WORKER_LOST.value
    resume_from = (await source_state(runtime, CRASHED)).checkpoint

    later = datetime.now(UTC) + timedelta(seconds=1)
    assert await requeue_due_retries(runtime, now=later) == (job_id,)
    retried = FakeSource(SECOND)
    outcome = await deliver(runtime, job_id, retried, later)

    assert outcome.job.status is JobStatus.SUCCEEDED
    assert await attempts(runtime, job_id) == 2
    # The retry starts where the last committed page left off.
    assert retried.checkpoints[0] == resume_from
    stored = await snapshot(runtime, CRASHED)
    assert stored == await snapshot(runtime, CLEAN)
    assert await source_state(runtime, CRASHED) == await source_state(runtime, CLEAN)
    # Both are right, not merely the same.
    assert {
        name: (document.availability, document.content_version, document.readers)
        for name, document in stored.items()
    } == EXPECTED
    assert (await source_state(runtime, CRASHED)).purges == 1


# --- A new Source field: web_url

WIKI = "https://wiki.example.com/spaces/HR/pages/42"


async def anchors(runtime: DatabaseRuntime, name: str) -> tuple[str, ...]:
    return (await snapshot(runtime, CRASHED))[name].anchors


async def test_a_web_url_becomes_where_every_chunk_leads(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)

    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=WIKI)]]),
        datetime.now(UTC),
    )

    assert await anchors(runtime, "leave.txt") == (f"{WIKI}#offset=0-21",)


async def test_a_moved_page_rewrites_its_chunks_and_nothing_else(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    start = datetime.now(UTC) - timedelta(minutes=10)
    await sync(runtime, CRASHED, FakeSource([[item("leave.txt", web_url=WIKI)]]), start)
    moved = "https://wiki.example.com/spaces/HR/pages/43"

    job = await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=moved)]]),
        start + timedelta(minutes=5),
    )

    stored = (await snapshot(runtime, CRASHED))["leave.txt"]
    assert stored.anchors == (f"{moved}#offset=0-21",)
    # The text is the same, but every Chunk stores the link, so they change.
    assert (stored.content_version, stored.authorization_version) == (2, 1)
    assert job.status is JobStatus.SUCCEEDED


async def test_the_same_web_url_again_changes_nothing(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    start = datetime.now(UTC) - timedelta(minutes=10)
    pages = [[item("leave.txt", web_url=WIKI)]]
    await sync(runtime, CRASHED, FakeSource(pages), start)
    before = await snapshot(runtime, CRASHED)

    await sync(runtime, CRASHED, FakeSource(pages), start + timedelta(minutes=5))

    assert await snapshot(runtime, CRASHED) == before


async def test_a_secret_in_a_web_url_never_reaches_a_chunk(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    leaky = "https://reader:hunter2@wiki.example.com/spaces/HR/pages/42?token=SECRET#x"

    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=leaky)]]),
        datetime.now(UTC),
    )

    assert await anchors(runtime, "leave.txt") == (f"{WIKI}#offset=0-21",)


async def test_a_new_token_in_the_same_link_changes_nothing(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    start = datetime.now(UTC) - timedelta(minutes=10)
    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=f"{WIKI}?token=one")]]),
        start,
    )
    before = await snapshot(runtime, CRASHED)

    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=f"{WIKI}?token=two")]]),
        start + timedelta(minutes=5),
    )

    # Only the safe form counts, so a new session token rewrites nothing.
    assert await snapshot(runtime, CRASHED) == before


async def test_an_unsafe_web_url_falls_back_to_the_external_id(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)

    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url="javascript:alert(1)")]]),
        datetime.now(UTC),
    )

    assert await anchors(runtime, "leave.txt") == ("leave.txt#offset=0-21",)


async def test_a_source_that_starts_sending_links_rewrites_the_chunks_once(
    runtime: DatabaseRuntime,
) -> None:
    await register(runtime, CRASHED)
    start = datetime.now(UTC) - timedelta(minutes=10)
    await sync(runtime, CRASHED, FakeSource([[item("leave.txt")]]), start)

    # A Source that later starts sending links rewrites the Chunks once.
    await sync(
        runtime,
        CRASHED,
        FakeSource([[item("leave.txt", web_url=WIKI)]]),
        start + timedelta(minutes=5),
    )

    stored = (await snapshot(runtime, CRASHED))["leave.txt"]
    assert stored.anchors == (f"{WIKI}#offset=0-21",)
    assert stored.content_version == 2
```

**Перевірка:** `uv run pytest tests/test_synchronization_gate_integration.py -q -m integration` дає `16 passed`.

### Крок 7 — У цьому кроці без коду: зламай навмисно

Цей крок показує, який тест захищає найважливіший рядок M08-T09 і що спростувало б дизайн.

**Дія 1:** у `_publish` у `src/knowledge_service/ingestion/synchronization.py`, у виклику `commit_page(...)`, зміни:

```python
            offered=planned.keys(),
```

на:

```python
            offered=(),
```

Тепер фіксація сторінки не зберігає побачених ID, тож завершення забуває, що пропонували попередні сторінки.

**Дія 2:** запусти:

```bash
uv run pytest tests/test_synchronization_gate_integration.py -q -m integration
```

Ти побачиш `11 failed, 5 passed`. Падають усі дев'ять точок збою: завершення повтору ставить tombstone документам, які пропонували попередні сторінки. Падають і два тести вебадрес, бо їхній другий прохід ставить tombstone єдиному елементу. Без абсолютних очікувань із блоку 8 тести збою пройшли б, бо чистий прохід робить ту саму помилку.

**Дія 3:** скасуй зміну і запусти команду знову: `16 passed`.

**Перевірка:** порівняй кількість зі своїм передбаченням із кроку 1. Під `## Verification` запиши кількість failed і passed.

### Крок 8 — У цьому кроці без коду: вбий справжній воркер посеред сторінки

Цей крок робить збій по-справжньому: процес `make worker` гине посеред сторінки 2, а relay і новий воркер його відновлюють. Тут окрема база, `knowledge_service_drill`, тож тестова база не зачіпається. Це займає близько семи хвилин, здебільшого очікування п'яти хвилин тиші для відновлення.

**Дія 1:** в одному терміналі, з кореня репозиторію, задай середовище тренування і створи папку зі 150 файлами:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
export KNOWLEDGE_SERVICE_DATABASE_URL="${KNOWLEDGE_SERVICE_TEST_DATABASE_URL%/*}/knowledge_service_drill"
export KNOWLEDGE_SERVICE_REDIS_URL="redis://127.0.0.1:6380/12"
export DRILL_DOCS="$(mktemp -d)"
for i in $(seq -f "%03g" 1 150); do printf "# Policy $i\n\nRule number $i.\n" > "$DRILL_DOCS/doc-$i.md"; done
```

**Дія 2:** створи й мігруй базу тренування і зареєструй папку як публічне джерело:

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
            " values ('00000000-0000-0000-0000-00000000d016', 'local_directory',"
            " :location, 'Drill policies', true, now(), now(), true)"
        ),
        {"location": sys.argv[2]},
    )
engine.dispose()
print("drill database ready")
EOF
```

Збережи цей скрипт як `drill_show.py` поруч із папкою файлів, а не всередині неї. Так ти зможеш будь-коли подивитися на стан:

```bash
cat > "$DRILL_DOCS/../drill_show.py" <<'EOF'
import sys

import sqlalchemy as sa
from sqlalchemy.engine import make_url

engine = sa.create_engine(make_url(sys.argv[1]).set(drivername="postgresql+psycopg"))
with engine.connect() as connection:
    for label, query in (
        ("jobs", "select kind, status, attempt, last_error_code from jobs order by queued_at"),
        ("attempts", "select attempt, processed, changed, outcome from job_attempts order by attempt"),
        ("source", "select checkpoint, pass_mode from sources"),
        ("documents", "select availability, count(*) from documents group by 1"),
    ):
        print(label, connection.execute(sa.text(query)).all())
engine.dispose()
EOF
```

**Дія 3:** додай збій. У `_publish` у `src/knowledge_service/ingestion/synchronization.py` додай `import os` після `import logging`, а ці два рядки — одразу перед `result = await reconcile_document(`:

```python
            if os.environ.get("DRILL_CRASH_BEFORE") == record.external_id:
                os._exit(1)
```

`os._exit(1)` одразу завершує процес. Він не виконує жодного блоку `finally` і нічого не каже Redis, як і вбивство процесу.

**Дія 4:** постав Job у чергу і запусти воркер зі зведеним збоєм:

```bash
make worker-relay
DRILL_CRASH_BEFORE=doc-120.md make worker
```

Relay друкує `scheduled 1, published 1`. Воркер стартує і сам завершується; `make` друкує `make: *** [worker] Error 1`. Подивися на стан:

```bash
uv run python "$DRILL_DOCS/../drill_show.py" "$KNOWLEDGE_SERVICE_DATABASE_URL"
```

```text
jobs [('synchronize_source', 'running', 0, None)]
attempts [(1, 100, 100, None)]
source [('doc-100.md', 'full')]
documents [('available', 119)]
```

Сторінку 1 зафіксовано, 19 записів сторінки 2 збережено, а Job досі каже `running`. Стовпець `attempt` рахує завершені спроби, тому там досі 0.

**Дія 5:** прибери два додані рядки й `import os`, щоб `git diff src/knowledge_service/ingestion/synchronization.py` нічого не показував. У другому терміналі виконай ті самі три рядки `export`, запусти звичайний воркер і залиш його працювати:

```bash
make worker
```

**Дія 6:** зачекай, доки від збою мине п'ять хвилин. Потім у першому терміналі:

```bash
make worker-relay
uv run python "$DRILL_DOCS/../drill_show.py" "$KNOWLEDGE_SERVICE_DATABASE_URL"
```

Relay друкує `stale 1`. Job у статусі failed із `worker_lost`, а результат спроби 1 — `retry_scheduled`. Запускай `make worker-relay` кожні кілька секунд, доки він не надрукує `requeued 1, ... published 1`; перший повтор чекає приблизно 15 секунд. Воркер у другому терміналі пише в лог `(synchronize_source) ran: succeeded`. Подивися ще раз:

```text
jobs [('synchronize_source', 'succeeded', 1, None)]
attempts [(1, 100, 100, 'retry_scheduled'), (2, 50, 31, 'succeeded')]
source [(None, None)]
documents [('available', 150)]
```

**Дія 7:** подивися на доставку, що лишилася, а потім зупини воркер через Ctrl+C:

```bash
docker exec knowledge-service-redis-redis-1 redis-cli -n 12 keys '*__acks__*'
```

Можливо, ти побачиш один ключ `__acks__` мертвого воркера: його повідомлення, так і не підтверджене. Якщо Dramatiq поверне його, поки воркер працює, воркер напише в лог `is succeeded; duplicate message` і нічого не зробить.

**Дія 8:** прибери за собою:

```bash
docker exec knowledge-service-postgres psql -U test_user -d postgres -c "DROP DATABASE knowledge_service_drill WITH (FORCE)"
docker exec knowledge-service-redis-redis-1 redis-cli -n 12 flushdb
unset KNOWLEDGE_SERVICE_DATABASE_URL KNOWLEDGE_SERVICE_REDIS_URL
```

**Перевірка:** спроба 2 повідомила `processed` 50 і `changed` 31, як пояснює блок 2. Під `## Injected crash` в evidence-файлі запиши хронологію зі своїми часами, рядки relay і воркера та два знімки стану.

### Крок 9 — У цьому кроці без коду: онови документи для нового поля

Цей крок записує поле там, де його шукатимуть читачі архітектури, моделі загроз і експлуатаційних документів.

**Дія 1:** у `docs/architecture.md` додай цей абзац одразу перед `#### Synchronization Jobs as built in M08-T10`:

```markdown
A Source may report an item's `web_url` (since M08-T16): where a person
opens the item in the Source, such as a wiki page. Normalization keeps only
its safe form, without credentials, query, or fragment, and only when a
Citation would show it unchanged; otherwise the item has none. Every Chunk's
anchor then leads to it, as `<web_url>#offset=START-END` or `#page=N`, and
a Citation keeps that fragment. The link counts in the content fingerprint
only when present, so items without one keep their fingerprints; a moved
page rewrites its Chunks once.
```

**Дія 2:** у `docs/threat-model.md` додай цей рядок одразу перед рядком, що починається з «A reader the Source removed keeps reading»:

```markdown
| A Source's `web_url` carries a secret or a misleading link into a Citation | Normalization keeps only the safe `http`/`https` form: no credentials, query, fragment, or invisible character, and only when the Citation would show it unchanged; anything else falls back to the external ID; a Citation keeps only an anchor's own `#offset=` or `#page=` fragment | A secret written into the URL's path itself is kept; spellings of one page that differ, such as an explicit default port, count as a move and rewrite its Chunks |
```

**Дія 3:** у `docs/operations.md` після речення, що закінчується на «and incremental passes between.», додай:

```markdown
A worker killed mid-pass is recovered without help (drilled in M08-T16 by ending `make worker` with `os._exit` before one record): after five silent minutes `make worker-relay` fails the attempt as `worker_lost`, requeues it at its retry time, and the retry resumes from the last committed page; Redis may still hold the dead worker's message, and its redelivery runs nothing. An item whose `web_url` changes gets new Chunks, which need their embeddings published again.
```

**Перевірка:** `make docs-check` проходить.

### Крок 10 — У цьому кроці без коду: перевірки, evidence і прогрес

Цей крок доводить, що весь репозиторій і далі працює, а потім записує результат уроку.

**Дія 1:** запусти всі перевірки одну за одною:

```bash
make format-check
make lint
make typecheck
make test-unit
make test-integration
make docs-check
git diff --check
```

**Дія 2:** заповни решту evidence-файлу:

- `## New Source field`: шлях `web_url` (таблиця з блоку 4), чому вона у відбитку лише тоді, коли є, і ціна переїзду.
- `## Design`: змінені файли.
- `## Verification`: точні команди й кількості з кроків 5–8 і дії 1.
- `## Review`: блоки 5–8, кожен у формі «що зламалося, що ми робимо тепер», і відомі обмеження з блоку 9.
- `## Decision`: чому вирішує PostgreSQL, а Redis лише доставляє, і що спростувало б дизайн: точка збою, після якої результат відрізняється від чистого проходу; повторно доставлене повідомлення, що запускає прохід; або елемент без вебадреси, чий відбиток змінився.
- `## Reflection`: твої відповіді на питання для роздумів нижче.

**Дія 3:** у `docs/curriculum/PROGRESS.md` додай рядки для M08-T11 … M08-T15 зі статусом `deferred` і приміткою «no Confluence access yet». Потім додай рядок `completed` для M08-T16 із посиланням на evidence. Наступним незаблокованим завданням вкажи M09-T01. У файлі завдання M08-T16 встанови `status: completed`.

**Перевірка:** усі перевірки проходять, і `make docs-check` проходить після змін у записах.

## Питання для роздумів

1. Воркер загинув, зберігши 19 записів сторінки 2. Яка система могла б тобі це сказати, Redis чи PostgreSQL, і на що саме ти подивився б?
2. Уяви, що claim не перевіряв би статус Job і просто ставив би `running`. Що зробило б повідомлення мертвого воркера, яке лишилося, коли Dramatiq поверне його вже після успіху спроби 2?
3. Фіксація сторінки рухає checkpoint лише після того, як збережено всі записи сторінки. Що пішло б не так у тренуванні, якби вона рухала checkpoint першою, ще до збереження записів?
4. Чому відбиток враховує `web_url` лише тоді, коли вона є? Що сталося б з усіма вже збереженими документами, якби він враховував її завжди?
5. Сторінку в Confluence перейменували, і її адреса змінилася. Що наступний прохід зробить із її Chunks і що станеться зі старою відповіддю, яка її цитувала?
6. Який тест цього уроку ти запустив би першим після зміни того, як фіксуються сторінки, і чому саме його?

## Чекліст завершення

- [ ] Evidence-файл має інваріант, власників, перевірку, що має падати, і твої передбачення, записані до будь-яких змін.
- [ ] `## Delivery versus business state` пояснює, яку систему питати і чому.
- [ ] Схеми архітектури показують relay, Redis, воркер і PostgreSQL такими, як їх побудовано, а статуси Job збігаються з `_ALLOWED`.
- [ ] `web_url` проходить від `SourceMetadata` до якорів і цитат; `tests/test_source_web_url.py` дає `21 passed`.
- [ ] `tests/test_synchronization_gate_integration.py` дає `16 passed`.
- [ ] Навмисна поломка дала `11 failed, 5 passed`, а відновлений код — `16 passed`.
- [ ] Тренування: справжній воркер убито посеред сторінки, відновлено як `worker_lost`, спроба 2 з `processed` 50 і `changed` 31, 150 документів; дані тренування прибрано.
- [ ] Документи архітектури, моделі загроз і експлуатації описують нове поле.
- [ ] Усі перевірки проходять.
- [ ] M08-T11 … M08-T15 записано як відкладені, M08-T16 як виконаний, а наступне завдання — M09-T01.

Коли зробиш крок, поділися кодом, виводом або помилками, і я їх перегляну.
