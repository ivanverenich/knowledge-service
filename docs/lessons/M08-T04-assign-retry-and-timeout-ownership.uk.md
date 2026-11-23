English version: [M08-T04-assign-retry-and-timeout-ownership.md](M08-T04-assign-retry-and-timeout-ownership.md)

# M08-T04 — Хто повторює і хто стежить за часом

Завдання: [M08-T04 — Assign retry and timeout ownership](../curriculum/milestones/M08-jobs-and-confluence/M08-T04-assign-retry-and-timeout-ownership.md)

## Що ти зробиш

Цей урок вирішує, хто повторює Job, що впав, коли і скільки разів. Зараз цього не робить ніхто: очищення, яке впало, бо база на мить зникла, так і лишається у статусі `failed` назавжди.

Конкретний приклад. О 10:00:00 запускається очищення J1, і база обриває з'єднання воркера: перемикання на резервну або сесія, яку завершив адміністратор. Сама база знову відповідає вже за мить. Після цього уроку:

| Час | Що відбувається з J1 |
|---|---|
| 10:00:00 | спроба 1 падає з «база недоступна» — це збій, який може минути; через нове з'єднання воркер записує в рядок J1 `retry_at` між 10:00:15 і 10:00:30, скажімо 10:00:22 |
| 10:00:22 або пізніше | наступний прохід `make worker-relay` бачить, що час настав, знову ставить J1 у чергу і надсилає повідомлення |
| після 10:00:22 | виконується спроба 2; якщо вона впаде так само, наступне очікування — від 30 до 60 секунд |
| після спроби 3 | бюджет із 3 спроб вичерпано; J1 лишається у статусі `failed` і більше не повторюється |

Якби J1 упав через баг (скажімо, `KeyError`), він зупинився б після спроби 1, без жодного повтору.

| Що просить завдання | Де це зроблено |
|---|---|
| Класифікувати збої джерела, бази, моделі й програміста | Крок 3: `classify_job_failure` дає кожній помилці один із семи кодів |
| Обмежені повтори в одному шарі | Кроки 2, 5, 6: `retry_at` збереженого Job, який ставить лише `fail_attempt`, і `requeue_due_retries` — єдиний шлях назад у чергу |
| Backoff і jitter | Крок 3: `RetryPolicy`, очікування, що подвоюється, з випадковим розкидом |
| Постійні збої не повторюються; тимчасові зупиняються на бюджеті | Крок 8: тести на обидва випадки, плюс перевірка в базі |
| Хто володіє тайм-аутами | Крок 3: тест-контракт, що база здається раніше за жорсткий ліміт часу; «Перед початком», блок 5 |
| Явна поведінка при повторі чи скасуванні | Крок 8: тест скасованої спроби; docstring очищення |
| Оновити лише ту документацію, якої це стосується | Крок 10: архітектура, модель загроз, експлуатація |

У цьому уроці є код: кроки 2–8 пишуть код, міграцію і тести. Кроки 1, 9, 10 і 11 коду не потребують, хоча на кроці 10 ти редагуєш три документи.

Терміни цього уроку:

- **Тимчасовий збій (transient failure):** збій, який може минути сам, як обірване з'єднання. Спроба пізніше може вдатися.
- **Постійний збій (permanent failure):** збій, який повтор лише повторить, як хибний пароль чи баг. Спершу хтось має виправити причину.
- **Бюджет спроб (retry budget):** найбільша кількість спроб, яку може використати Job; тут `max_attempts`, за замовчуванням 3.
- **Backoff:** чекати довше після кожного збою, щоб сервісу, якому зле, вистачило часу оговтатися.
- **Jitter:** випадкова частина очікування, щоб Job, які впали разом, не поверталися всі в ту саму секунду.
- **Власник тайм-ауту (timeout owner):** той один шар, чий дедлайн має зупинити повільний виклик. Дедлайн, поставлений надто далеко, марний; поставлений у двох місцях — сам із собою свариться.

## Перед початком

Цей розділ пояснює кожне рішення, на якому стоїть код, і показує, що відбувається зараз, крок за кроком. Кожен блок закінчується тим, що змінює код цього уроку.

### 1. Зараз ніхто не повторює Job, що впав

**Ситуація.** Очищення J1 запускається о 10:00:00. База обриває з'єднання воркера посеред видалення, але нові з'єднання приймає одразу.

**Що відбувається зараз:**

| Час | Воркер | Relay (`make worker-relay`) | Що каже рядок у `jobs` |
|---|---|---|---|
| 10:00:00 | виконує J1; видалення кидає помилку | | `queued` |
| 10:00:00 | записує збій (точка збереження з M08-T03) | | `failed`, спроба 1 з 3 |
| 10:00:30 | | шукає Job у `queued` без повідомлення: немає | `failed` |
| 11:00:00 | | досі немає | `failed`, назавжди |

**Прихований факт.** Кожен шар навмисно зроблено так, щоб він *не* повторював: у Dramatiq `max_retries=0`, relay надсилає лише Job у `queued`, а адаптери моделей вимикають повтори SDK. Кожен каже «повтори — справа викликача», але жоден викликач цього ще не робить. Єдиний спосіб, у який J1 зараз виконається знову, — новий запит тієї самої роботи, який M08-T03 перетворює на «знову поставити Job, що впав, у чергу».

**Наскільки це погано.** Для очищення фрагменти видаленого документа лишаються в базі, доки той самий документ не видалять знову, а цього може й не статися. Читання при цьому правильні, бо документ позначено видаленим. Для синхронізації, яку будують наступні уроки, 30-секундний збій Confluence зупинив би синк джерела, доки хтось не помітить.

**Що змінюється.** Job, що впав, записує, *коли* він може виконатися знову, у новій колонці `retry_at`. Прохід у `make worker-relay` ставить у чергу кожен Job, що впав і чий `retry_at` настав; далі relay, як завжди, надсилає його повідомлення.

```python
# Before: a failure is just a failure.
failed = job.fail(error_code=PURGE_FAILED, finished_at=now)

# After: the failure is classified, and a retry is planned only if it may help.
failed = fail_attempt(job, error, now=now, policy=retry_policy)
# failed.retry_at is now + a delay, or None when the failure is final.
```

Уяви картку запису до лікаря. На картці невдалого візиту пишуть нову дату, а реєстратор кличе всіх, чия дата настала. Раніше ніхто не заходить.

### 2. Не кожен збій варто повторювати

**Ситуація.** Три очищення падають о 10:00:00 з трьох різних причин.

| Job | Помилка | Що дасть повтор |
|---|---|---|
| J1 | база обірвала з'єднання | найімовірніше спрацює за хвилину |
| J2 | база відмовила в рядку: порушено унікальне обмеження | щоразу впаде так само |
| J3 | `KeyError` у нашому коді | падатиме так само, доки код не виправлять |

**Прихований факт.** Тип помилки показує, який це випадок, а проєкт уже загортає зовнішні помилки у власні типи. Джерела кидають `SourceUnavailable` або `SourceUnauthorized`. Моделі кидають `ChatModelUnavailable`, `ChatModelRequestRefused` та подібні. База кидає помилки SQLAlchemy, а `errors.py` уже знає, які з них означають «база лежить» (втрачене з'єднання, взаємне блокування, тайм-аут запиту, забагато з'єднань). Усе інше, разом із сирим `ConnectionError`, який адаптер забув загорнути, — наш баг.

**Наскільки погано без цього.** Повтор J2 і J3 по три рази марнує по дві спроби і засмічує логи. Повтор хибного пароля до Confluence ще гірший: він може заблокувати акаунт.

**Що змінюється.** `classify_job_failure` зводить кожну помилку до одного з семи кодів, і лише три з них тимчасові:

| Код | Приклад помилки | Повторюється? |
|---|---|---|
| `source_unavailable` | `SourceUnavailable` | так |
| `source_refused` | `SourceUnauthorized` | ні |
| `database_unavailable` | втрачене з'єднання, взаємне блокування (`40P01`), тайм-аут запиту (`57014`) | так |
| `database_rejected` | порушене обмеження (`23505`) | ні |
| `model_unavailable` | `ChatModelUnavailable`, `EmbeddingTimeout`, `EmbeddingRateLimited` | так |
| `model_refused` | `ChatModelRequestRefused`, `EmbeddingInputRejected` | ні |
| `programming_error` | усе інше: `KeyError`, `ValueError`, сирий `ConnectionError` | ні |

Код зберігається як `last_error_code` Job, тож таблиця `jobs` тепер каже, *чому* впав кожен Job, а не просто «очищення впало».

### 3. Скільки чекати: backoff і jitter

**Ситуація.** База перезапускається о 10:00:00, і 200 очищень падають в одну й ту саму секунду.

**Без очікування або з однаковим очікуванням для всіх:**

| Час | Що відбувається |
|---|---|
| 10:00:00 | падають 200 очищень |
| 10:00:30 | усі 200 повторюють разом; база ще розігрівається; усі 200 падають знову |
| 10:01:00 | знову всі 200 |

**Прихований факт.** Однакове очікування повертає кожен Job в одну й ту саму секунду, тож повтори приходять однією хвилею — «тупотіння стада» (thundering herd). Подвоєння очікування щоразу дає сервісу більше простору, а випадкова частина розмазує хвилю.

**Що змінюється.** `RetryPolicy` рахує очікування після `n` збоїв. Стеля — `min(15 хвилин, 30 секунд * 2**(n-1))`. Очікування — половина стелі плюс випадкова частка другої половини, тож воно ніколи не менше половини і завжди нижче стелі («equal jitter»).

| Після збою | Стеля | Очікування |
|---|---|---|
| 1 | 30 с | від 15 до 30 с |
| 2 | 60 с | від 30 до 60 с |
| 3 | 120 с | від 60 до 120 с (лише якщо `max_attempts` більше 3) |
| 6 і далі | 15 хв | від 7,5 до 15 хв |

Тепер 200 очищень повертаються, розподілені на 15 секунд, а не одним залпом. Обчислення йдуть у цілих мікросекундах, тож величезна кількість спроб не переповнить їх, а округлення ніколи не виведе очікування за межі вікна.

Уяви натовп, що виходить зі стадіону через одні ворота: якщо всі рушать знову за свистком, ворота знову застрягнуть; якщо кожен почекає трохи різний час, усі пройдуть.

### 4. Повторами володіє один шар

**Ситуація.** Уяви, що той самий збій повторюють два шари: адаптер моделі повторює запит 3 рази, і Job повторює спробу 3 рази.

| Шар | Спроб |
|---|---|
| адаптер моделі (повтори SDK увімкнено) | 3 на кожну спробу Job |
| Job | 3 спроби |
| усього викликів до моделі, яка лежить | 9, без спільного очікування між ними |

**Прихований факт.** Повтори, складені шарами, множаться, і бюджет та backoff кожного шару не зважають на інші. Саме тому адаптери вимикають повтори SDK, а в Dramatiq `max_retries=0`. M08-T03 випадково додав другий шар: повторний запит ставив Job, що впав, у чергу *одразу*, пропускаючи будь-яке очікування.

**Що змінюється.** Єдиний шар — збережений Job. `fail_attempt` — єдиний код, який ставить `retry_at`. `requeue_due_retries` — єдиний код, який знову ставить Job, що впав, у чергу, і лише коли `retry_at` настав. Сам `Job.retry` відмовляє постійному збою і передчасному повтору, тож жоден майбутній викликач не пропустить очікування випадково. Обхідний шлях M08-T03 прибрано: повторний запит тепер отримує Job, що впав, назад, і той і далі чекає.

```python
# Before (M08-T03): enqueue_job queued a failed open Job again at once.
if existing is not None and existing.status is JobStatus.FAILED:
    existing = await _queue_again(connection, job)

# After: the open Job is returned as it is; it waits for its retry_at.
if existing is not None:
    return Enqueued(job=existing, created=False)
```

### 5. Хто володіє тайм-аутами

**Ситуація.** Під час очищення база зависає на одному запиті.

**Прихований факт.** У кожного шару свій годинник, і той, що спрацює першим, вирішує, що буде:

| Шар | Дедлайн | Що відбувається, коли спрацьовує |
|---|---|---|
| запит до бази | `statement_timeout` 10 с | запит скасовано; SQLAlchemy кидає помилку з SQLSTATE `57014`; класифікація `database_unavailable`, повтор |
| пул з'єднань | 10 с очікування вільного з'єднання | `TimeoutError` від пулу; класифікація `database_unavailable`, повтор |
| відкриття з'єднання | 5 с | відмова або тайм-аут; класифікація `database_unavailable`, повтор |
| адаптери моделей | власний `timeout_seconds` | типізована помилка `...Timeout`; класифікація `model_unavailable`, повтор |
| актор Dramatiq | ліміт часу 60 с | кидає `TimeLimitExceeded` у потік воркера |

`TimeLimitExceeded` — це `BaseException`, а не `Exception`, тож жоден `except Exception` його не ловить. Уся транзакція відкочується, Job лишається `queued`, а його повідомлення вже використане. Ніхто не надішле його знову, доки M08-T08 не додасть прохід, що шукає завислі Job.

**Наскільки це погано.** Тому внутрішні дедлайни мають спрацьовувати першими. Тоді повільна база закінчується звичайним, класифікованим і повтореним збоєм, а 60-секундний ліміт лишається останнім запобіжником.

**Що змінюється.** Нового тайм-ауту немає. Тест-контракт перевіряє, що найгірший окремий запит (очікування пулу + підключення + тайм-аут запиту = 25 с) закінчується раніше за 60-секундний ліміт. Тож якщо згодом змінити одне з чисел, впаде тест, а не запобіжник тихо стане власником.

### 6. Знахідка рев'ю: справжній обрив з'єднання губив Job

**Ситуація.** Адверсаріальне рев'ю обірвало сесію бази посеред очищення через `pg_terminate_backend`, так само, як це робить перезапуск бази чи перемикання на резервну.

| Момент | Воркер (перший прототип) | Що каже рядок у `jobs` |
|---|---|---|
| 1 | захоплює J1, починає видалення в точці збереження | `queued` (захоплення ще не закомічене) |
| 2 | сервер завершує сесію; видалення кидає помилку | `queued` |
| 3 | відкат до точки збереження: не вдається, сесії вже немає | `queued` |
| 4 | пробує записати «failed»: падає з `PendingRollbackError` | `queued` |
| 5 | уся транзакція втрачена | `queued`, повідомлення вже використане, ніхто не надішле його знову |

**Прихований факт.** Точка збереження захищає від невдалого запиту на живому з'єднанні. Коли сервер завершує сесію, зникає все, що в ній було: точка збереження, захоплення і блокування рядка. Записати збій уже нікуди. До того ж до коду дійшла помилка `PendingRollbackError` — наслідок, а не причина, — і перший прототип класифікував її як `programming_error`.

**Що змінюється.** Очищення запам'ятовує першу помилку спроби (`cause`). Якщо транзакція захоплення сама гине, `_record_lost_attempt` відкриває нову, блокує J1 і записує збій за тією першою помилкою, тож це `database_unavailable` і повтор буде. Блокування однаково вже немає, тому спершу перевіряється, що J1 досі `queued` і на тій самій спробі; інакше першою встигла інша доставка, і чинним лишається її результат (`JobAlreadyClaimed`).

| | Живе з'єднання, видалення падає | Сервер завершує сесію |
|---|---|---|
| де записується збій | у тій самій транзакції, під блокуванням захоплення | у новій транзакції, після перевірки, що J1 досі `queued` на тій самій спробі |
| за чим класифікується | за помилкою видалення | за першою помилкою, а не `PendingRollbackError` |
| результат | `failed`, повтор заплановано | `failed`, повтор заплановано |

Рев'ю також знайшло, що друга транзакція без перевірки «та сама спроба» могла записати втрачену спробу 1 на спробу 2, якщо між ними інша доставка провалила J1, а прохід повторів знову поставив його в чергу. Ця перевірка закриває діру.

### 7. Знахідка рев'ю: баг повертався на кожному проході синхронізації

**Ситуація.** Очищення падає з `KeyError`: постійний збій, без повтору. Але кожен прохід синхронізації викликає `requeue_unfinished_purges`, яке просить нове очищення для кожного документа, позначеного видаленим, у якого досі є фрагменти і немає відкритого очищення.

| Прохід синку | Що було в першому прототипі |
|---|---|
| 1 | нове очищення J2; `KeyError`; остаточно |
| 2 | нове очищення J3; `KeyError`; остаточно |
| 5 | 5 Job, що впали, для одного документа, і далі більше |

**Прихований факт.** «Попросити знову» — правильний хід після збою, який минув, і неправильний — після відмови чи бага. Кожен новий Job приносить свіжий бюджет, тож бюджет одного Job нічого не обмежував між проходами.

**Що змінюється.** `load_unpurged_documents` пропускає документ, чиє *останнє* очищення впало назавжди. Людина, яка виправила причину, просить очищення знову сама. Очищення, що вичерпало спроби на тимчасовому збої, наступний прохід і далі просить знову, бо збій міг минути.

«Останнє» визначається за `finished_at`. Рев'ю показало, що `queued_at` тут не годиться: прохід повторів записує туди годинник relay, а синхронізація — час, коли почався її прохід, тож старий Job, повторений пізно, міг виглядати новішим за свіжий постійний збій.

### 8. Відомі обмеження

Ці речі поки лишаються як є:

- `retry_at` живе в базі, тож він рятує, коли база «блимнула», а не коли вона лежить. Якщо й нове з'єднання не вдається, записати збій нікуди: спроба відкочується, Job лишається `queued` з використаним повідомленням, а помилка йде в лог Dramatiq. Такі Job знаходить прохід пошуку завислої роботи в M08-T08.
- Скасована спроба або спроба, обірвана 60-секундним лімітом актора, відкочується повністю: нічого не видалено, а Job лишається `queued` з використаним повідомленням. Тест це фіксує; прохід пошуку завислої роботи в M08-T08 надішле його знову.
- Хибний пароль до бази або неіснуюча база не дають SQLSTATE до відкриття з'єднання, тож класифікуються як `database_unavailable` і повторюються до вичерпання бюджету. Це обмежено, але повтори є.
- Тимчасовий збій, що вичерпав увесь бюджет, отримує нове очищення від наступного проходу синхронізації: свіжий бюджет на кожен прохід, у темпі синхронізації.
- Job, що впали, повертаються лише тоді, коли запускається `make worker-relay`, тож його треба запускати за розкладом; посібник з експлуатації тепер про це каже.

## Покрокова інструкція

Перед початком запусти PostgreSQL. Усі команди виконуються з кореня репозиторію. Для integration-перевірок задай URL тестової бази у своєму терміналі:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
```

Запускай проти цієї бази лише одну integration-команду за раз. Два запуски одночасно мігрують ту саму базу разом і можуть її зламати.

### Крок 1 — У цьому кроці коду немає: запиши інваріант і свої прогнози

Перш ніж щось змінювати, запиши, що має бути правдою наприкінці і чого ти очікуєш. Прогнози перевіриш на кроках 9 і 11.

Створи `docs/evidence/M08-T04-assign-retry-and-timeout-ownership.md`:

```markdown
# M08-T04 — Assign retry and timeout ownership evidence

## Invariant

Only the stored Job retries. A failed attempt is classified by its error;
only a Source, database, or model that is unavailable is retried, after a
doubling, capped, jittered delay recorded in `retry_at`, until the Job's
attempts run out. Refusals and programming errors are final. A failed Job
goes back to the queue only through `requeue_due_retries`, and only once its
`retry_at` has come. Each call's own timeout ends a slow call before the
actor's time limit does.

## Owners

- Retries: the stored Job (`fail_attempt` sets `retry_at`,
  `requeue_due_retries` acts on it); Dramatiq and model SDKs retry nothing.
- Timeouts: each call's own deadline (database statement, pool, connect,
  model adapters); the actor's time limit is a backstop.
- Stuck Jobs after a cancellation or time limit: M08-T08.
- Authorization: none; nothing outside the service submits Jobs.

## Expected failing check

Before the change,
`uv run pytest tests/test_job_failures.py -q`
fails with
`ERROR: file or directory not found: tests/test_job_failures.py`.

## Predictions

- What `retry_at` a purge gets when its delete loses the database connection on attempt 1, with the default policy:
- How many attempts a purge that always loses the connection uses before it stops:
- What happens to a purge that fails with a `KeyError`, and to the next synchronization pass:
- How many tests fail if `fail_attempt` stops checking whether a failure is transient:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після двокрапки в кожному прогнозі стоїть твоя відповідь.

### Крок 2 — Крок з кодом: дати Job, що впав, час повтору

Цей крок учить значення Job зберігати, *коли* він може виконатися знову, і відмовляти в будь-якому повторі, що порушує правила.

**Простими словами:** Job, що впав, отримує ще одне поле: найраніший час, коли його можна знову поставити в чергу, або нічого, якщо цього не буде ніколи. Job вважається «ще відкритим» лише тоді, коли такий час задано. Повторна постановка в чергу тепер спершу перевіряє три речі: спроби ще є, збій не остаточний, і час настав.

| Частина | Що містить або робить |
|---|---|
| `retry_at` | коли Job, що впав, можна знову поставити в чергу; `None` означає «ніколи» |
| перевірки в `__post_init__` | `retry_at` буває лише в Job, що впав і має спроби; він в UTC і не раніше за `finished_at` |
| `is_open` | Job, що впав, відкритий лише тоді, коли `retry_at` задано |
| `fail(..., retry_at=...)` | викликач передає запланований час або не передає його для остаточного збою |
| `retry` | відмовляє, якщо бюджет вичерпано, збій остаточний або `queued_at` раніше за `retry_at`; очищує `retry_at` |


**Дія 1:** у значенні Job спершу розшир, що означає `JobNotRetryable`:

У `src/knowledge_service/jobs.py` знайди:

```python
class IllegalJobTransition(InvalidJob):
    """A caller asked a Job to move to a status it cannot reach from here."""


class JobNotRetryable(InvalidJob):
    """A failed Job has already used every attempt it is allowed."""


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

і заміни на:

```python
class IllegalJobTransition(InvalidJob):
    """A caller asked a Job to move to a status it cannot reach from here."""


class JobNotRetryable(InvalidJob):
    """A failed Job may not be queued again, or not yet.

    It used every attempt, its failure was permanent, or its retry time has
    not come.
    """


class JobAlreadyClaimed(InvalidJob):
    """A Job is no longer queued, so this delivery must not run it again.

    `job` is the Job as stored when the claim was refused: the outcome an
    earlier delivery already produced, or is producing.
    """

    def __init__(self, job: Job, reason: str | None = None) -> None:
        super().__init__(reason or f"only a queued job can run, not a {job.status} one")
        self.job = job


class JobKind(StrEnum):
    """What a Job was queued to do."""
```

Додай поле після `last_error_code`. Воно має значення за замовчуванням, тож `Job.request` і всі наявні викликачі працюють і далі:

У `src/knowledge_service/jobs.py` знайди:

```python
    max_attempts: int
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error_code: str | None

    def __post_init__(self) -> None:
        _require_utc(self.queued_at, "queued_at")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise InvalidJob("max attempts must be a positive integer")
```

і заміни на:

```python
    max_attempts: int
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    last_error_code: str | None
    # When a failed Job may be queued again; None means it never will be.
    retry_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_utc(self.queued_at, "queued_at")
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise InvalidJob("max attempts must be a positive integer")
```

Перевір нове поле в `__post_init__` і зміни `is_open`:

У `src/knowledge_service/jobs.py` знайди:

```python
            raise InvalidJob("only a job that is not queued carries a started time")
        if (self.status is JobStatus.FAILED) != (self.last_error_code is not None):
            raise InvalidJob("only a failed job carries an error code")
        if self.last_error_code is not None and not self.last_error_code.strip():
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
        cls,
```

і заміни на:

```python
            raise InvalidJob("only a job that is not queued carries a started time")
        if (self.status is JobStatus.FAILED) != (self.last_error_code is not None):
            raise InvalidJob("only a failed job carries an error code")
        if self.last_error_code is not None and not self.last_error_code.strip():
            raise InvalidJob("an error code must not be empty")
        if self.retry_at is not None:
            _require_utc(self.retry_at, "retry_at")
            if self.status is not JobStatus.FAILED:
                raise InvalidJob("only a failed job carries a retry time")
            if self.attempt >= self.max_attempts:
                raise InvalidJob("a job with no attempts left has no retry time")
            if self.finished_at is not None and self.retry_at < self.finished_at:
                raise InvalidJob("retry_at must not be earlier than finished_at")

    @property
    def is_open(self) -> bool:
        """Whether this Job can still do its work.

        A Job is open while it is queued or running, and while it has failed
        with a retry scheduled, because it will be queued again. At most one
        open Job exists per kind and target.
        """
        if self.status is JobStatus.FAILED:
            return self.retry_at is not None
        return self.status is not JobStatus.SUCCEEDED

    @classmethod
    def request(
        cls,
```

Нехай `fail` приймає запланований час:

У `src/knowledge_service/jobs.py` знайди:

```python
    def succeed(self, *, finished_at: datetime) -> Job:
        """Finish the Job successfully."""
        _check_transition(self.status, JobStatus.SUCCEEDED)
        return replace(self, status=JobStatus.SUCCEEDED, finished_at=finished_at)

    def fail(self, *, error_code: str, finished_at: datetime) -> Job:
        """Finish the Job unsuccessfully, recording a short error code."""
        _check_transition(self.status, JobStatus.FAILED)
        if not error_code.strip():
            raise InvalidJob("an error code must not be empty")
        if len(error_code) > MAX_ERROR_CODE_LENGTH:
            raise InvalidJob(
```

і заміни на:

```python
    def succeed(self, *, finished_at: datetime) -> Job:
        """Finish the Job successfully."""
        _check_transition(self.status, JobStatus.SUCCEEDED)
        return replace(self, status=JobStatus.SUCCEEDED, finished_at=finished_at)

    def fail(
        self,
        *,
        error_code: str,
        finished_at: datetime,
        retry_at: datetime | None = None,
    ) -> Job:
        """Finish the attempt unsuccessfully, recording a short error code.

        `retry_at` schedules the next attempt; None makes the failure final.
        """
        _check_transition(self.status, JobStatus.FAILED)
        if not error_code.strip():
            raise InvalidJob("an error code must not be empty")
        if len(error_code) > MAX_ERROR_CODE_LENGTH:
            raise InvalidJob(
```

Збережи його у `fail`; у `retry` додай дві відмови й очищення часу, коли Job знову стає в чергу:

У `src/knowledge_service/jobs.py` знайди:

```python
            self,
            status=JobStatus.FAILED,
            attempt=self.attempt + 1,
            finished_at=finished_at,
            last_error_code=error_code,
        )

    def retry(self, *, queued_at: datetime) -> Job:
        """Queue the Job again for one more attempt."""
        _check_transition(self.status, JobStatus.QUEUED)
        if self.attempt >= self.max_attempts:
            raise JobNotRetryable(
                f"the job already used all {self.max_attempts} attempts"
            )
        return replace(
            self,
            status=JobStatus.QUEUED,
            queued_at=queued_at,
            started_at=None,
            finished_at=None,
            last_error_code=None,
        )
```

і заміни на:

```python
            self,
            status=JobStatus.FAILED,
            attempt=self.attempt + 1,
            finished_at=finished_at,
            last_error_code=error_code,
            retry_at=retry_at,
        )

    def retry(self, *, queued_at: datetime) -> Job:
        """Queue the Job again for one more attempt, once its retry time came."""
        _check_transition(self.status, JobStatus.QUEUED)
        if self.attempt >= self.max_attempts:
            raise JobNotRetryable(
                f"the job already used all {self.max_attempts} attempts"
            )
        if self.retry_at is None:
            raise JobNotRetryable("the job failed permanently")
        if queued_at < self.retry_at:
            raise JobNotRetryable("the job may not be retried before its retry time")
        return replace(
            self,
            status=JobStatus.QUEUED,
            queued_at=queued_at,
            started_at=None,
            finished_at=None,
            last_error_code=None,
            retry_at=None,
        )
```

**Дія 2:** три наявні тести в `tests/test_jobs.py` створювали збої без часу повтору, а потім їх повторювали, і тепер це заборонено. Дай їм час і додай три тести на нові правила.

Нові тести будують некоректний Job через `replace`:

У `tests/test_jobs.py` знайди:

```python
"""Tests for the Job state machine."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4
```

і заміни на:

```python
"""Tests for the Job state machine."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
```

У `test_a_failed_job_retries_until_its_attempts_run_out` заплануй перший повтор:

У `tests/test_jobs.py` знайди:

```python
def test_a_failed_job_retries_until_its_attempts_run_out() -> None:
    job = requested_job(max_attempts=2)
    first = job.start(started_at=QUEUED_AT + timedelta(seconds=1)).fail(
        error_code="source_unreachable", finished_at=QUEUED_AT + timedelta(seconds=2)
    )
    second = first.retry(queued_at=QUEUED_AT + timedelta(seconds=3))
    second_failure = second.start(started_at=QUEUED_AT + timedelta(seconds=4)).fail(
```

і заміни на:

```python
def test_a_failed_job_retries_until_its_attempts_run_out() -> None:
    job = requested_job(max_attempts=2)
    first = job.start(started_at=QUEUED_AT + timedelta(seconds=1)).fail(
        error_code="source_unreachable",
        finished_at=QUEUED_AT + timedelta(seconds=2),
        retry_at=QUEUED_AT + timedelta(seconds=3),
    )
    second = first.retry(queued_at=QUEUED_AT + timedelta(seconds=3))
    second_failure = second.start(started_at=QUEUED_AT + timedelta(seconds=4)).fail(
```

У `test_a_job_is_open_until_it_can_do_no_more_work` заплануй повтор першого збою:

У `tests/test_jobs.py` знайди:

```python
def test_a_job_is_open_until_it_can_do_no_more_work() -> None:
    job = requested_job(max_attempts=2)
    started = job.start(started_at=QUEUED_AT)
    failed_once = started.fail(error_code="boom", finished_at=QUEUED_AT)
    failed_twice = (
        failed_once.retry(queued_at=QUEUED_AT)
        .start(started_at=QUEUED_AT)
```

і заміни на:

```python
def test_a_job_is_open_until_it_can_do_no_more_work() -> None:
    job = requested_job(max_attempts=2)
    started = job.start(started_at=QUEUED_AT)
    failed_once = started.fail(
        error_code="boom", finished_at=QUEUED_AT, retry_at=QUEUED_AT
    )
    failed_twice = (
        failed_once.retry(queued_at=QUEUED_AT)
        .start(started_at=QUEUED_AT)
```

і покажи, що остаточний збій не відкритий:

У `tests/test_jobs.py` знайди:

```python

    assert job.is_open
    assert started.is_open
    # A failed Job with attempts left may still be queued again.
    assert failed_once.is_open
    assert not failed_twice.is_open
    assert not started.succeed(finished_at=QUEUED_AT).is_open
```

і заміни на:

```python

    assert job.is_open
    assert started.is_open
    # A failed Job with a retry scheduled will be queued again.
    assert failed_once.is_open
    # A failure with no retry time is final, even with attempts left.
    assert not started.fail(error_code="boom", finished_at=QUEUED_AT).is_open
    assert not failed_twice.is_open
    assert not started.succeed(finished_at=QUEUED_AT).is_open
```

Додай нові тести в кінець файлу:

У `tests/test_jobs.py` знайди:

```python
    assert isinstance(refused, InvalidJob)
    assert refused.job is finished
    assert str(refused) == "only a queued job can run, not a succeeded one"
```

і заміни на:

```python
    assert isinstance(refused, InvalidJob)
    assert refused.job is finished
    assert str(refused) == "only a queued job can run, not a succeeded one"


def test_only_a_failed_job_with_attempts_left_carries_a_retry_time() -> None:
    running = requested_job(max_attempts=2).start(started_at=QUEUED_AT)
    later = QUEUED_AT + timedelta(minutes=1)

    with pytest.raises(InvalidJob, match="only a failed job"):
        replace(running, retry_at=later)
    with pytest.raises(InvalidJob, match="earlier than finished_at"):
        running.fail(error_code="boom", finished_at=later, retry_at=QUEUED_AT)
    with pytest.raises(InvalidJob, match="UTC"):
        running.fail(
            error_code="boom",
            finished_at=QUEUED_AT,
            retry_at=later.replace(tzinfo=None),
        )


def test_a_job_with_no_attempts_left_cannot_get_a_retry_time() -> None:
    running = requested_job(max_attempts=1).start(started_at=QUEUED_AT)

    with pytest.raises(InvalidJob, match="no attempts left"):
        running.fail(error_code="boom", finished_at=QUEUED_AT, retry_at=QUEUED_AT)


def test_a_retry_waits_for_its_time_and_never_follows_a_final_failure() -> None:
    running = requested_job(max_attempts=3).start(started_at=QUEUED_AT)
    due = QUEUED_AT + timedelta(minutes=1)
    scheduled = running.fail(error_code="boom", finished_at=QUEUED_AT, retry_at=due)
    final = running.fail(error_code="boom", finished_at=QUEUED_AT)

    with pytest.raises(JobNotRetryable, match="before its retry time"):
        scheduled.retry(queued_at=due - timedelta(seconds=1))
    with pytest.raises(JobNotRetryable, match="permanently"):
        final.retry(queued_at=due)
    retried = scheduled.retry(queued_at=due)
    assert (retried.status, retried.retry_at) == (JobStatus.QUEUED, None)
```

**Перевірка:** `uv run pytest tests/test_jobs.py -q` показує `9 passed`.

### Крок 3 — Крок з кодом: класифікувати збої і запланувати наступну спробу

Цей крок додає єдиний модуль, який вирішує, чи повторювати збій і коли.

**Простими словами:** отримавши помилку, з якою впала спроба, модуль називає збій коротким кодом і каже, чи допоможе повтор. Якщо може допомогти і в Job ще є спроби, він обирає час: очікування, що щоразу подвоюється, ніколи не довше 15 хвилин, із випадковою частиною. Потім записує збій у Job, з цим часом або без нього.

| Частина | Що робить |
|---|---|
| `JobFailure` | сім кодів; `.transient` true для трьох «недоступних» |
| `classify_job_failure` | зводить помилку до коду; усе невідоме — `programming_error` |
| `RetryPolicy` | `base_delay` 30 с, `max_delay` 15 хв і джерело `jitter`, яке тести можуть зафіксувати |
| `RetryPolicy.delay` | очікування після `n` збоїв, у цілих мікросекундах, між половиною і всією стелею |
| `fail_attempt` | класифікує, планує `retry_at` лише для тимчасового збою зі спробами, пише в лог код і тип помилки, але ніколи не її текст, і викликає `Job.fail` |

Правило для бази вже живе в `errors.py`, яким користується HTTP-шар. Зроби його публічним замість копіювання, щоб «база лежить» вирішувалось в одному місці.


**Дія 1:** у `src/knowledge_service/errors.py` перейменуй приватну допоміжну функцію:

У `src/knowledge_service/errors.py` знайди:

```python
_UNAVAILABLE_STATES = frozenset({"08", "40", "53", "57"})


def _database_unavailable(error: BaseException) -> bool:
    """True when sending the request again later may work."""
    # The pool had no free connection in time.
    if isinstance(error, database_errors.TimeoutError):
```

і заміни на:

```python
_UNAVAILABLE_STATES = frozenset({"08", "40", "53", "57"})


def database_unavailable(error: BaseException) -> bool:
    """True when sending the request again later may work."""
    # The pool had no free connection in time.
    if isinstance(error, database_errors.TimeoutError):
```

і єдине місце її виклику:

У `src/knowledge_service/errors.py` знайди:

```python
        return FailureKind.DEPENDENCY
    # The database is down, unreachable, too slow, or out of connections.
    # Any other database error, such as a broken constraint, is a bug.
    if _database_unavailable(error):
        return FailureKind.DEPENDENCY
    return FailureKind.INTERNAL
```

і заміни на:

```python
        return FailureKind.DEPENDENCY
    # The database is down, unreachable, too slow, or out of connections.
    # Any other database error, such as a broken constraint, is a bug.
    if database_unavailable(error):
        return FailureKind.DEPENDENCY
    return FailureKind.INTERNAL
```

**Дія 2:** створи модуль.

Створи `src/knowledge_service/job_failures.py`:

```python
"""Why a Job attempt failed, and whether and when to try it again.

The stored Job is the one layer that retries. Dramatiq retries nothing, model
adapters keep their SDK retries off, and repository code never loops, so a
failure is retried only by scheduling the Job's next attempt here.

Every failure gets one short code. Only a dependency that may answer later
(a Source, the database, or a model that is unavailable) is retried, after a
growing, jittered delay, until the Job's attempts run out. Anything refused or
unknown is permanent: retrying a bad credential or a bug only repeats it.
"""

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from sqlalchemy import exc as database_errors

from knowledge_service.embeddings.port import (
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingRequestRefused,
)
from knowledge_service.errors import database_unavailable
from knowledge_service.ingestion.sources import SourceUnauthorized, SourceUnavailable
from knowledge_service.jobs import Job
from knowledge_service.model import (
    ChatModelError,
    ChatModelRefused,
    ChatModelRequestRefused,
    ChatModelStructuredOutputUnsupported,
)

logger = logging.getLogger(__name__)


class JobFailure(StrEnum):
    """The error code a failed attempt stores: what failed, and how."""

    SOURCE_UNAVAILABLE = "source_unavailable"
    SOURCE_REFUSED = "source_refused"
    DATABASE_UNAVAILABLE = "database_unavailable"
    DATABASE_REJECTED = "database_rejected"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_REFUSED = "model_refused"
    PROGRAMMING_ERROR = "programming_error"

    @property
    def transient(self) -> bool:
        """Whether the same attempt may work later without anyone fixing it."""
        return self in _TRANSIENT


_TRANSIENT: Final = frozenset(
    {
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
    }
)

_MODEL_REFUSALS: Final = (
    ChatModelRequestRefused,
    ChatModelRefused,
    ChatModelStructuredOutputUnsupported,
    EmbeddingRequestRefused,
    EmbeddingInputRejected,
)


def classify_job_failure(error: Exception) -> JobFailure:
    """The failure an attempt's error stands for; unknown errors are bugs."""
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


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """How long to wait before each retry: doubling, capped, and jittered.

    The wait before attempt n+1 is half of min(max_delay, base_delay * 2**(n-1))
    plus a random share of the other half, so it is never under half the
    backoff, and Jobs that failed together do not all come back together.
    """

    base_delay: timedelta = timedelta(seconds=30)
    max_delay: timedelta = timedelta(minutes=15)
    # A number from 0 (inclusive) to 1 (exclusive); tests pass a fixed one.
    jitter: Callable[[], float] = field(default=random.random)

    def __post_init__(self) -> None:
        if self.base_delay <= timedelta(0):
            raise ValueError("base_delay must be positive")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must not be shorter than base_delay")

    def delay(self, failed_attempts: int) -> timedelta:
        """The wait after `failed_attempts` failures, at least one."""
        if type(failed_attempts) is not int or failed_attempts < 1:
            raise ValueError("failed_attempts must be a positive integer")
        share = self.jitter()
        if not 0.0 <= share < 1.0:
            raise ValueError("jitter must return a number from 0 up to 1")
        # Whole microseconds: Python integers cannot overflow, and flooring
        # keeps the result at least half the ceiling and below it.
        base = self.base_delay // timedelta(microseconds=1)
        cap = self.max_delay // timedelta(microseconds=1)
        ceiling = min(cap, base << min(failed_attempts - 1, cap.bit_length()))
        half = ceiling // 2
        return timedelta(microseconds=half + int((ceiling - half) * share))


DEFAULT_RETRY_POLICY: Final = RetryPolicy()


def fail_attempt(
    job: Job,
    error: Exception,
    *,
    now: datetime,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> Job:
    """`job` failed by `error`: classified, and retried later only if worth it.

    A transient failure with attempts left gets a retry time; any other
    failure is final. Only the error's type is logged, never its message.
    """
    failure = classify_job_failure(error)
    failed_attempts = job.attempt + 1
    retry_at = None
    if failure.transient and failed_attempts < job.max_attempts:
        retry_at = now + policy.delay(failed_attempts)
    logger.warning(
        "job %s attempt %d failed: %s (%s); %s",
        job.job_id.value,
        failed_attempts,
        failure.value,
        type(error).__name__,
        "retry planned" if retry_at is not None else "final",
    )
    return job.fail(error_code=failure.value, finished_at=now, retry_at=retry_at)
```

**Дія 3:** протестуй кожен код, вікно очікування, бюджет, логи і порядок тайм-аутів. Останній тест — це контракт тайм-аутів із «Перед початком», блок 5.

Створи `tests/test_job_failures.py`:

```python
"""Tests for classifying Job failures and scheduling their retries."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError

from knowledge_service.embeddings.port import (
    EmbeddingInputRejected,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
    EmbeddingTimeout,
    RejectionReason,
)
from knowledge_service.identifiers import JobId
from knowledge_service.ingestion.sources import SourceUnauthorized, SourceUnavailable
from knowledge_service.job_failures import (
    JobFailure,
    RetryPolicy,
    classify_job_failure,
    fail_attempt,
)
from knowledge_service.jobs import InvalidJob, Job, JobKind
from knowledge_service.model import (
    ChatModelMalformedResponse,
    ChatModelRefused,
    ChatModelRequestRefused,
    ChatModelUnavailable,
)
from knowledge_service.settings import Settings
from knowledge_service.worker.jobs import JOB_TIME_LIMIT_MS

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
# Stands in for driver text that could quote a URL or a credential.
MARKER = "leak-marker-5c1e"


class DriverError(Exception):
    """A database driver error with the SQLSTATE the server sent."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(MARKER)
        self.sqlstate = sqlstate


def database_error(
    kind: type[OperationalError | IntegrityError], sqlstate: str
) -> DBAPIError:
    return kind("DELETE FROM chunks", None, DriverError(sqlstate))


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (SourceUnavailable("the wiki is down"), JobFailure.SOURCE_UNAVAILABLE),
        (SourceUnauthorized("token expired"), JobFailure.SOURCE_REFUSED),
        (
            OperationalError("SELECT 1", None, ConnectionError("lost")),
            JobFailure.DATABASE_UNAVAILABLE,
        ),
        # A deadlock or serialization failure may pass on a second try.
        (database_error(OperationalError, "40P01"), JobFailure.DATABASE_UNAVAILABLE),
        # The statement timeout cancelled the statement.
        (database_error(OperationalError, "57014"), JobFailure.DATABASE_UNAVAILABLE),
        (database_error(IntegrityError, "23505"), JobFailure.DATABASE_REJECTED),
        (ChatModelUnavailable(), JobFailure.MODEL_UNAVAILABLE),
        (ChatModelMalformedResponse("no usage"), JobFailure.MODEL_UNAVAILABLE),
        (EmbeddingTimeout(), JobFailure.MODEL_UNAVAILABLE),
        (EmbeddingRateLimited(), JobFailure.MODEL_UNAVAILABLE),
        (ChatModelRequestRefused(), JobFailure.MODEL_REFUSED),
        (ChatModelRefused(), JobFailure.MODEL_REFUSED),
        (EmbeddingRequestRefused(), JobFailure.MODEL_REFUSED),
        (
            EmbeddingInputRejected(RejectionReason.EMPTY_TEXT, index=0),
            JobFailure.MODEL_REFUSED,
        ),
        (ValueError("bad input"), JobFailure.PROGRAMMING_ERROR),
        (KeyError("missing"), JobFailure.PROGRAMMING_ERROR),
        (InvalidJob("bad job"), JobFailure.PROGRAMMING_ERROR),
        # An adapter must wrap network errors; a raw one is a bug.
        (ConnectionError("reset"), JobFailure.PROGRAMMING_ERROR),
    ],
)
def test_every_failure_is_classified(error: Exception, failure: JobFailure) -> None:
    assert classify_job_failure(error) is failure


def test_only_dependencies_that_may_answer_later_are_transient() -> None:
    assert {failure for failure in JobFailure if failure.transient} == {
        JobFailure.SOURCE_UNAVAILABLE,
        JobFailure.DATABASE_UNAVAILABLE,
        JobFailure.MODEL_UNAVAILABLE,
    }


def test_the_delay_doubles_until_its_cap_and_never_drops_below_half() -> None:
    lowest = RetryPolicy(jitter=lambda: 0.0)
    highest = RetryPolicy(jitter=lambda: 0.999)

    assert [lowest.delay(n) for n in (1, 2, 3)] == [
        timedelta(seconds=15),
        timedelta(seconds=30),
        timedelta(seconds=60),
    ]
    assert highest.delay(1) < timedelta(seconds=30)
    assert lowest.delay(10) == timedelta(minutes=7, seconds=30)
    assert highest.delay(10) < timedelta(minutes=15)
    # A huge attempt count stays at the cap instead of overflowing.
    assert lowest.delay(10_000) == timedelta(minutes=7, seconds=30)


@pytest.mark.parametrize("ceiling_us", [1, 3, 7, 1_000_001])
def test_the_delay_stays_in_its_window_for_any_ceiling(ceiling_us: int) -> None:
    ceiling = timedelta(microseconds=ceiling_us)
    lowest = RetryPolicy(base_delay=ceiling, max_delay=ceiling, jitter=lambda: 0.0)
    highest = RetryPolicy(
        base_delay=ceiling, max_delay=ceiling, jitter=lambda: 0.999999
    )

    assert lowest.delay(1) == timedelta(microseconds=ceiling_us // 2)
    assert highest.delay(1) < ceiling


def test_a_long_base_delay_reaches_the_cap_without_overflowing() -> None:
    policy = RetryPolicy(
        base_delay=timedelta(days=1_000),
        max_delay=timedelta(days=2_000),
        jitter=lambda: 0.0,
    )

    assert policy.delay(21) == timedelta(days=1_000)
    assert policy.delay(10_000) == timedelta(days=1_000)


@pytest.mark.parametrize("failed_attempts", [0, -1, True, 1.0])
def test_the_delay_needs_a_positive_attempt_count(failed_attempts: int) -> None:
    with pytest.raises(ValueError, match="failed_attempts"):
        RetryPolicy().delay(failed_attempts)


@pytest.mark.parametrize("share", [-0.1, 1.0, float("nan")])
def test_a_jitter_outside_zero_to_one_is_refused(share: float) -> None:
    with pytest.raises(ValueError, match="jitter"):
        RetryPolicy(jitter=lambda: share).delay(1)


def test_a_policy_needs_a_positive_delay_and_a_cap_above_it() -> None:
    with pytest.raises(ValueError, match="base_delay"):
        RetryPolicy(base_delay=timedelta(0))
    with pytest.raises(ValueError, match="max_delay"):
        RetryPolicy(base_delay=timedelta(minutes=2), max_delay=timedelta(minutes=1))


def running_job(*, attempt: int = 0, max_attempts: int = 3) -> Job:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_DOCUMENT_CHUNKS,
        target_id=JobId.new().value,
        queued_at=NOW,
        max_attempts=max_attempts,
    )
    for _ in range(attempt):
        job = job.start(started_at=NOW).fail(
            error_code="boom", finished_at=NOW, retry_at=NOW
        )
        job = job.retry(queued_at=NOW)
    return job.start(started_at=NOW)


def test_a_transient_failure_with_attempts_left_is_retried_after_the_delay() -> None:
    policy = RetryPolicy(jitter=lambda: 0.5)

    failed = fail_attempt(
        running_job(), SourceUnavailable("down"), now=NOW, policy=policy
    )

    assert (failed.attempt, failed.last_error_code) == (1, "source_unavailable")
    assert failed.retry_at == NOW + policy.delay(1)
    assert failed.is_open


def test_a_transient_failure_stops_at_the_attempt_budget() -> None:
    last = running_job(attempt=2, max_attempts=3)

    failed = fail_attempt(last, SourceUnavailable("down"), now=NOW)

    assert (failed.attempt, failed.retry_at) == (3, None)
    assert not failed.is_open


@pytest.mark.parametrize(
    "error",
    [SourceUnauthorized("revoked"), ChatModelRequestRefused(), ValueError("bug")],
)
def test_a_permanent_failure_is_never_retried(error: Exception) -> None:
    failed = fail_attempt(running_job(), error, now=NOW)

    assert (failed.attempt, failed.retry_at) == (1, None)
    assert not failed.is_open


def test_a_failure_logs_its_code_and_type_but_not_its_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    fail_attempt(running_job(), database_error(OperationalError, "08006"), now=NOW)

    assert "database_unavailable" in caplog.text
    assert "OperationalError" in caplog.text
    assert MARKER not in caplog.text


def test_database_deadlines_end_before_the_hard_job_time_limit() -> None:
    settings = Settings()
    # The worst single statement: waiting for a pooled connection, opening
    # one, and running until the statement timeout cancels it.
    worst_statement_seconds = (
        settings.database_pool_timeout
        + settings.database_connect_timeout_seconds
        + settings.database_statement_timeout_ms / 1000
    )

    assert worst_statement_seconds < JOB_TIME_LIMIT_MS / 1000
```

**Перевірка:** `uv run pytest tests/test_job_failures.py -q` показує `40 passed`.

### Крок 4 — Крок з кодом: зберігати час повтору і дати базі його стерегти

Цей крок додає колонку, правило, яке перевіряє сама база, і індекс, щоб знаходити Job, яким уже час.

**Простими словами:** таблиця `jobs` отримує колонку для часу повтору. База відмовляє в часі повтору будь-якому Job, який не впав або в якого не лишилось спроб. Правило «відкритого Job» з M08-T03 тепер дивиться на час повтору, а не на лічильник спроб. Невеликий індекс дає проходу повторів швидко знаходити Job, яким уже час.

| Частина | Що містить або робить |
|---|---|
| колонка `retry_at` | час повтору; `null`, коли його немає |
| `ck_jobs_retry_at` | `retry_at` лише в Job, що впав і має спроби, і не раніше за `finished_at` |
| `OPEN_JOB_PREDICATE` | відкритий тепер означає у черзі, виконується або впав із заданим `retry_at` |
| `ix_jobs_due_retry` | частковий індекс над Job, у яких є час повтору, упорядкований за ним |
| Міграція `f6a7b8c9d0e1` | додає все це |

Міграція не повинна змінити, які Job відкриті, інакше унікальний індекс з M08-T03 міг би знайти два відкриті Job на один намір. Тому на шляху вгору вона дає кожному Job, що впав і має спроби, `retry_at = finished_at`: він був відкритий, лишається відкритим і готовий одразу. На шляху вниз вона позначає остаточний збій зі спробами як вичерпаний, щоб старе правило не вважало його відкритим.


**Дія 1:** створи міграцію.

Створи `migrations/versions/f6a7b8c9d0e1_job_retry_schedule.py`:

```python
"""job retry schedule

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-11-23 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"

OLD_OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and attempt < max_attempts)"
)
OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and retry_at is not null)"
)


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    # A failed Job with attempts left was open before; it stays open, due now.
    op.execute(
        "update jobs set retry_at = finished_at"
        " where status = 'failed' and attempt < max_attempts"
    )
    op.create_check_constraint(
        "ck_jobs_retry_at",
        "jobs",
        "retry_at is null or (status = 'failed' and attempt < max_attempts"
        " and retry_at >= finished_at)",
    )
    # The same rows are open under both predicates, so no new duplicates.
    op.drop_index("uq_jobs_open_intent", table_name="jobs")
    op.create_index(
        "uq_jobs_open_intent",
        "jobs",
        ["kind", "target_id"],
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    )
    op.create_index(
        "ix_jobs_due_retry",
        "jobs",
        ["retry_at", "job_id"],
        postgresql_where=sa.text("retry_at is not null"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_due_retry", table_name="jobs")
    op.drop_index("uq_jobs_open_intent", table_name="jobs")
    # Before this revision, a failed Job with attempts left was open. Keep a
    # permanent failure closed by marking its attempts as used up.
    op.execute(
        "update jobs set attempt = max_attempts"
        " where status = 'failed' and retry_at is null and attempt < max_attempts"
    )
    op.create_index(
        "uq_jobs_open_intent",
        "jobs",
        ["kind", "target_id"],
        unique=True,
        postgresql_where=sa.text(OLD_OPEN_JOB_PREDICATE),
    )
    op.drop_constraint("ck_jobs_retry_at", "jobs", type_="check")
    op.drop_column("jobs", "retry_at")
```

**Дія 2:** у схемі зміни правило відкритого Job:

У `src/knowledge_service/persistence/schema.py` знайди:

```python
)


# A Job that can still do its work: queued, running, or failed with attempts
# left. At most one such Job exists per kind and target (its intent).
OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and attempt < max_attempts)"
)

jobs = sa.Table(
```

і заміни на:

```python
)


# A Job that can still do its work: queued, running, or failed with a retry
# scheduled. At most one such Job exists per kind and target (its intent).
OPEN_JOB_PREDICATE = (
    "status in ('queued', 'running') or (status = 'failed' and retry_at is not null)"
)

jobs = sa.Table(
```

додай колонку після `published_at`:

У `src/knowledge_service/persistence/schema.py` знайди:

```python
    sa.Column("last_error_code", sa.String(length=64), nullable=True),
    # When a message naming this queued attempt was handed to the queue.
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(
        "kind in ('synchronize_source', 'purge_conversation', 'purge_document_chunks')",
        name="ck_jobs_kind",
```

і заміни на:

```python
    sa.Column("last_error_code", sa.String(length=64), nullable=True),
    # When a message naming this queued attempt was handed to the queue.
    sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    # When a failed Job may be queued again; null when it never will be.
    sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(
        "kind in ('synchronize_source', 'purge_conversation', 'purge_document_chunks')",
        name="ck_jobs_kind",
```

додай перевірку після `ck_jobs_last_error_code`:

У `src/knowledge_service/persistence/schema.py` знайди:

```python
        "(status = 'failed') = (last_error_code is not null)",
        name="ck_jobs_last_error_code",
    ),
    sa.Index(
        "ix_jobs_unpublished_queued_at",
        "queued_at",
```

і заміни на:

```python
        "(status = 'failed') = (last_error_code is not null)",
        name="ck_jobs_last_error_code",
    ),
    sa.CheckConstraint(
        "retry_at is null or (status = 'failed' and attempt < max_attempts"
        " and retry_at >= finished_at)",
        name="ck_jobs_retry_at",
    ),
    sa.Index(
        "ix_jobs_unpublished_queued_at",
        "queued_at",
```

і додай індекс останнім елементом таблиці:

У `src/knowledge_service/persistence/schema.py` знайди:

```python
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    ),
)

audit_events = sa.Table(
```

і заміни на:

```python
        unique=True,
        postgresql_where=sa.text(OPEN_JOB_PREDICATE),
    ),
    sa.Index(
        "ix_jobs_due_retry",
        "retry_at",
        "job_id",
        postgresql_where=sa.text("retry_at is not null"),
    ),
)

audit_events = sa.Table(
```

**Дія 3:** дозволь новий індекс:

У `tests/test_indexes.py` знайди:

```python
    "ix_synchronization_runs_source_started_at",
    "uq_embedding_spaces_current",
    "uq_jobs_open_intent",
    "uq_synchronization_runs_running_source",
}
```

і заміни на:

```python
    "ix_synchronization_runs_source_started_at",
    "uq_embedding_spaces_current",
    "uq_jobs_open_intent",
    "ix_jobs_due_retry",
    "uq_synchronization_runs_running_source",
}
```

**Перевірка:** `uv run pytest tests/test_indexes.py -q` показує `4 passed`, а `uv run pytest tests/test_migrations_integration.py -q -m integration` показує `3 passed`.

### Крок 5 — Крок з кодом: читати й писати час повтору і ставити в чергу тих, кому час

Цей крок учить шар бази нової колонки, прибирає обхідний шлях M08-T03 і додає запит, що ставить у чергу Job, яким уже час.

**Простими словами:** Job тепер зберігаються і читаються разом із часом повтору. Запит роботи, для якої вже є Job, що впав і чекає, повертає цей Job як є, а не ставить його в чергу одразу. Нова функція знаходить Job, що впали і чий час повтору настав, від найстаріших, і знову ставить кожен у чергу; Job, які цієї миті обробляє інший прохід, пропускаються, тож два проходи ніколи не поставлять один Job двічі.

| Частина | Що змінюється |
|---|---|
| `_job_row`, `_job_from` | несуть `retry_at` |
| `open_job` | для Job, що впав, «відкритий» означає, що `retry_at` задано |
| `load_open_job` | втрачає `for_update`, яким користувався лише прибраний обхідний шлях |
| `enqueue_job` | повертає відкритий Job, що впав, без змін; `_queue_again` прибрано |
| `requeue_due_jobs` | блокує Job, що впали і чий час настав, з `SKIP LOCKED`, від найстарішого `retry_at`, і зберігає `retry` для кожного |

**Дія 1:** файл змінюється в багатьох місцях, тож заміни його цілком.


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
        "retry_at": job.retry_at,
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
        retry_at=cast("datetime | None", record["retry_at"]),
    )


def open_job() -> sa.ColumnElement[bool]:
    """Rows of Jobs that can still do their work; see `Job.is_open`."""
    return sa.or_(
        jobs.c.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
        sa.and_(
            jobs.c.status == JobStatus.FAILED.value,
            jobs.c.retry_at.is_not(None),
        ),
    )


async def load_open_job(
    connection: AsyncConnection, *, kind: JobKind, target_id: UUID
) -> Job | None:
    """The open Job of this kind for this target, if there is one."""
    statement = sa.select(jobs).where(
        jobs.c.kind == kind.value, jobs.c.target_id == target_id, open_job()
    )
    record = (await connection.execute(statement)).mappings().one_or_none()
    if record is None:
        return None
    return _job_from(dict(record))


async def enqueue_job(connection: AsyncConnection, job: Job) -> Enqueued:
    """Store new queued `job`, unless an open Job with the same intent exists.

    The intent is the Job's kind and target. When an open Job for it exists,
    that Job is returned and `job` is not stored, so asking twice for the
    same work, even at the same moment, leaves one Job to do it. An open Job
    that failed waits for its retry time; a new request does not hurry it.
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


async def requeue_due_jobs(
    connection: AsyncConnection, *, now: datetime, limit: int
) -> tuple[Job, ...]:
    """Queue again up to `limit` failed Jobs whose retry time has come.

    Oldest retry time first. Rows another transaction has locked are skipped,
    so two passes at once never queue the same Job twice.
    """
    statement = (
        sa.select(jobs)
        .where(jobs.c.status == JobStatus.FAILED.value, jobs.c.retry_at <= now)
        .order_by(jobs.c.retry_at, jobs.c.job_id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(
        [
            await save_job(connection, job=_job_from(dict(record)).retry(queued_at=now))
            for record in records
        ]
    )


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

**Дія 2:** у `src/knowledge_service/persistence/documents.py` перестань знову просити очищення, чия остання спроба впала назавжди («Перед початком», блок 7). Підзапит обирає останнє очищення за `finished_at`.

Спершу імпорт:

У `src/knowledge_service/persistence/documents.py` знайди:

```python
    InvalidDocument,
)
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.jobs import JobKind
from knowledge_service.language import Language
from knowledge_service.persistence.jobs import open_job
```

і заміни на:

```python
    InvalidDocument,
)
from knowledge_service.identifiers import DocumentId, SourceId
from knowledge_service.job_failures import JobFailure
from knowledge_service.jobs import JobKind
from knowledge_service.language import Language
from knowledge_service.persistence.jobs import open_job
```

Потім список постійних кодів, одразу над `load_unpurged_documents`:

У `src/knowledge_service/persistence/documents.py` знайди:

```python
    return tuple(as_document(dict(record)) for record in records)


async def load_unpurged_documents(
    connection: AsyncConnection,
    *,
```

і заміни на:

```python
    return tuple(as_document(dict(record)) for record in records)


# Codes of a purge failure that a new attempt would only repeat.
_PERMANENT_FAILURES = [failure.value for failure in JobFailure if not failure.transient]


async def load_unpurged_documents(
    connection: AsyncConnection,
    *,
```

Потім початок функції:

У `src/knowledge_service/persistence/documents.py` знайди:

```python
) -> tuple[Document, ...]:
    """Read tombstoned Documents that still hold Chunks and no purge can delete.

    A purge Job counts while it is queued, running, or failed with attempts
    left; a Document whose last purge used every attempt is returned.
    """
    open_purge = sa.exists().where(
        jobs.c.kind == JobKind.PURGE_DOCUMENT_CHUNKS.value,
        jobs.c.target_id == documents.c.document_id,
        open_job(),
    )
    statement = (
        sa.select(documents)
```

і заміни на:

```python
) -> tuple[Document, ...]:
    """Read tombstoned Documents that still hold Chunks and no purge can delete.

    A purge Job counts while it is open: queued, running, or failed with a
    retry scheduled. A Document whose last purge ran out of attempts on a
    passing failure is returned, so a later pass tries again. One whose last
    purge failed permanently is not: asking again would only repeat the
    refusal or the bug, so a person asks once the cause is fixed.
    """
    purge_of_document = sa.and_(
        jobs.c.kind == JobKind.PURGE_DOCUMENT_CHUNKS.value,
        jobs.c.target_id == documents.c.document_id,
    )
    open_purge = sa.exists().where(purge_of_document, open_job())
    last_purge_error = (
        sa.select(jobs.c.last_error_code)
        .where(purge_of_document)
        # Open purges are excluded, so every row here has finished. Finish
        # times come from the attempts themselves; queue times do not.
        .order_by(jobs.c.finished_at.desc(), jobs.c.job_id.desc())
        .limit(1)
        .scalar_subquery()
    )
    statement = (
        sa.select(documents)
```

І додаткову умову в її запиті:

У `src/knowledge_service/persistence/documents.py` знайди:

```python
            documents.c.availability == DocumentAvailability.TOMBSTONED.value,
            sa.exists().where(chunks.c.document_id == documents.c.document_id),
            ~open_purge,
        )
        .order_by(documents.c.external_id)
    )
```

і заміни на:

```python
            documents.c.availability == DocumentAvailability.TOMBSTONED.value,
            sa.exists().where(chunks.c.document_id == documents.c.document_id),
            ~open_purge,
            sa.func.coalesce(last_purge_error, "").not_in(_PERMANENT_FAILURES),
        )
        .order_by(documents.c.external_id)
    )
```

**Перевірка:** `uv run pyright src/knowledge_service/persistence` показує 0 помилок.

### Крок 6 — Крок з кодом: запускати прохід повторів перед кожним проходом relay

Цей крок під'єднує новий запит до команди, яка вже запускається регулярно.

**Простими словами:** нова функція ставить у чергу повтори, яким настав час, з тими самими обмеженнями і перевіркою UTC, що й у relay. Команда relay викликає її першою, а потім надсилає повідомлення, як і раніше, тож Job, повернутий у чергу, отримує повідомлення в тому самому проході. Тепер команда ще й друкує, скільки Job повернула в чергу.

| Частина | Що робить |
|---|---|
| `requeue_due_retries` | перевіряє, що `now` в UTC, а `limit` від 1 до 1 000, потім запускає `requeue_due_jobs` в одній транзакції; повертає ID Job |
| `relay.main` | друкує `requeued N, published ...` |


**Дія 1:** імпортуй запит:

У `src/knowledge_service/job_dispatch.py` знайди:

```python
    load_unpublished_job_ids,
    lock_unpublished_job,
    mark_job_published,
)

logger = logging.getLogger(__name__)
```

і заміни на:

```python
    load_unpublished_job_ids,
    lock_unpublished_job,
    mark_job_published,
    requeue_due_jobs,
)

logger = logging.getLogger(__name__)
```

і додай функцію в кінець файлу:

У `src/knowledge_service/job_dispatch.py` знайди:

```python
        failed=0,
        untried=0,
    )
```

і заміни на:

```python
        failed=0,
        untried=0,
    )


async def requeue_due_retries(
    runtime: DatabaseRuntime, *, now: datetime, limit: int = 100
) -> tuple[JobId, ...]:
    """Queue again failed Jobs whose retry time has come, oldest first.

    This is the only place a failed Job goes back to the queue. Each one is
    then unpublished, so the next `publish_pending_jobs` sends its message.
    """
    _require_utc(now)
    if type(limit) is not int or not 1 <= limit <= MAX_RELAY_BATCH:
        raise ValueError(f"limit must be an integer from 1 to {MAX_RELAY_BATCH}")
    async with runtime.transaction() as connection:
        requeued = await requeue_due_jobs(connection, now=now, limit=limit)
    return tuple(job.job_id for job in requeued)
```

**Дія 2:** команда relay змінюється в кількох місцях; заміни її цілком.

Заміни весь `src/knowledge_service/worker/relay.py` на:

```python
"""Queue due retries, then publish Jobs no message names yet: one pass.

Run `python -m knowledge_service.worker.relay` after a crash or a Redis
outage, and on a schedule, because it is also what brings a failed Job back
once its retry time has come. It exits 0 when every Job it found was
published or skipped, and 1 when the queue refused a message; the rest wait
for the next pass.
"""

import asyncio
import sys
from datetime import UTC, datetime

from knowledge_service.database import create_database_runtime
from knowledge_service.identifiers import JobId
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


async def _relay(
    settings: Settings, publisher: JobPublisher
) -> tuple[tuple[JobId, ...], RelayReport]:
    runtime = create_database_runtime(settings)
    now = datetime.now(UTC)
    try:
        requeued = await requeue_due_retries(runtime, now=now)
        report = await publish_pending_jobs(runtime, publisher=publisher, now=now)
    finally:
        await runtime.dispose()
    return requeued, report


def main() -> int:
    settings = Settings()
    broker = broker_from_settings(settings)
    try:
        actors = declare_job_actors(
            broker, run_job=JobRunner(settings), kinds=frozenset(HANDLERS)
        )
        requeued, report = asyncio.run(_relay(settings, DramatiqJobPublisher(actors)))
    finally:
        broker.close()
    sys.stdout.write(
        f"requeued {len(requeued)}, "
        f"published {report.published}, skipped {report.skipped}, "
        f"failed {report.failed}, untried {report.untried}\n"
    )
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
```

**Перевірка:** `uv run pyright src/knowledge_service/job_dispatch.py src/knowledge_service/worker` показує 0 помилок.

### Крок 7 — Крок з кодом: нехай очищення класифікує свої збої, також коли з'єднання гине

Цей крок змінює єдиний обробник, очищення, щоб воно користувалось `fail_attempt`, і додає другу транзакцію з «Перед початком», блок 6.

**Простими словами:** коли видалення падає, очищення тепер передає помилку новому модулю, а той вирішує код і час повтору. Якщо база завершила сесію і очищення не може зберегти навіть це, воно відкриває свіжу транзакцію і зберігає збій там, судячи за першою помилкою. Перед збереженням воно перевіряє, що Job досі чекає на тій самій спробі; якщо інша доставка вже його виконала, чинним лишається її результат.

| Частина | Що змінюється |
|---|---|
| `PURGE_FAILED` | прибрано; збережений код каже, чому був збій |
| `purge_document_chunks` | приймає `retry_policy`; запам'ятовує `cause` і `claimed_attempt`; коли транзакція гине, викликає `_record_lost_attempt` |
| `_record_lost_attempt` | блокує Job у новій транзакції; `JobAlreadyClaimed`, якщо той уже не `queued` на захопленій спробі; інакше зберігає `fail_attempt` |
| docstring | каже, що роблять скасування і ліміт часу |


**Дія 1:** імпортуй новий модуль:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import AuditEventId, DocumentId, JobId, SourceId
from knowledge_service.jobs import (
    InvalidJob,
    Job,
```

і заміни на:

```python
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import AuditEventId, DocumentId, JobId, SourceId
from knowledge_service.job_failures import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
    fail_attempt,
)
from knowledge_service.jobs import (
    InvalidJob,
    Job,
```

Прибери стару константу коду:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
)
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job

PURGE_FAILED = "chunk_purge_failed"


@dataclass(frozen=True, slots=True)
class Tombstone:
```

і заміни на:

```python
)
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job


@dataclass(frozen=True, slots=True)
class Tombstone:
```

**Дія 2:** заміни `purge_document_chunks` від сигнатури до кінця функції і додай після неї `_record_lost_attempt`:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
    *,
    job_id: JobId,
    now: datetime,
) -> Job:
    """Run one queued purge Job and return it as stored afterwards.

    The Chunks are deleted only if the Document is still tombstoned; a
    Document restored in the meantime keeps its new Chunks. If deleting fails,
    nothing is deleted, the Job is stored as failed with `PURGE_FAILED`, and
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


def availability_event(document: Document) -> AuditEvent:
```

і заміни на:

```python
    *,
    job_id: JobId,
    now: datetime,
    retry_policy: RetryPolicy = DEFAULT_RETRY_POLICY,
) -> Job:
    """Run one queued purge Job and return it as stored afterwards.

    The Chunks are deleted only if the Document is still tombstoned; a
    Document restored in the meantime keeps its new Chunks. If deleting fails,
    nothing is deleted, and the Job is stored as failed with the failure's
    code; `fail_attempt` schedules a retry only for a failure that may pass.
    The outcome commits with the claim, so another delivery waiting on the
    claim sees it, never `queued`.

    If the claim's own transaction dies, as when the database drops the
    connection, its row lock dies with it; the attempt is then recorded in a
    new transaction, by the error that started it. A cancellation or the
    actor's time limit is not an `Exception`: it rolls back the attempt and
    leaves the Job queued, with its message spent, for M08-T08 to find.
    """
    # The first error of the attempt, so a dead connection is not blamed on
    # the errors it causes later, such as a pending rollback.
    cause: Exception | None = None
    # The attempt this delivery claimed, so a lost one is never recorded
    # against a later attempt another delivery started meanwhile.
    claimed_attempt: int | None = None
    try:
        async with runtime.transaction() as connection:
            job = await _claim(connection, job_id=job_id, now=now)
            claimed_attempt = job.attempt
            try:
                # A savepoint: a failed delete is undone, while the claim
                # keeps its row lock until the failure below is stored.
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
        return await _record_lost_attempt(
            runtime,
            job_id=job_id,
            cause=cause or error,
            claimed_attempt=claimed_attempt,
            now=now,
            retry_policy=retry_policy,
        )


async def _record_lost_attempt(
    runtime: DatabaseRuntime,
    *,
    job_id: JobId,
    cause: Exception,
    claimed_attempt: int | None,
    now: datetime,
    retry_policy: RetryPolicy,
) -> Job:
    """Record an attempt whose transaction could not commit its outcome.

    Its lock is gone, so another delivery may have run the Job meanwhile,
    or even a later attempt of it; then that outcome stands. If this
    transaction fails too, the error propagates and the Job stays queued for
    M08-T08.
    """
    async with runtime.transaction() as connection:
        stored = await load_job(connection, job_id=job_id, for_update=True)
        if stored is None:
            raise cause
        if stored.status is not JobStatus.QUEUED:
            raise JobAlreadyClaimed(stored)
        if claimed_attempt is not None and stored.attempt != claimed_attempt:
            raise JobAlreadyClaimed(
                stored, "another delivery already ran the attempt this one lost"
            )
        failed = fail_attempt(
            stored.start(started_at=now), cause, now=now, policy=retry_policy
        )
        return await save_job(connection, job=failed)


def availability_event(document: Document) -> AuditEvent:
```

**Перевірка:** `uv run pyright src` показує 0 помилок.

### Крок 8 — Крок з кодом: оновити тести, що спирались на стару поведінку, і перевірити повтори по-справжньому

Цей крок оновлює п'ять тестових файлів під нові правила і додає тести повторів на справжньому PostgreSQL.

**Простими словами:** старі тести валили очищення звичайним `ConnectionError`, а це тепер справедливо вважається багом і ніколи не повторюється. Тепер вони кидають справжню помилку бази «з'єднання втрачено». Тести, що повторювали вручну, чекають часу повтору. Два тести M08-T03, які очікували, що повторний запит одразу поставить Job, що впав, у чергу, тепер очікують, що він чекатиме. Нові тести проганяють справжні очищення крізь кожен випадок із «Перед початком».

**Дія 1:** спільні фікстури отримують тимчасову помилку бази. `OperationalError` з `ConnectionError` всередині і без SQLSTATE — саме те, що кидає SQLAlchemy, коли сервер перестає відповідати.


Спершу імпорт:

У `tests/job_fixtures.py` знайди:

```python
from datetime import UTC, datetime

from sqlalchemy import text

from knowledge_service.access import Subject
from knowledge_service.database import DatabaseRuntime
```

і заміни на:

```python
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from knowledge_service.access import Subject
from knowledge_service.database import DatabaseRuntime
```

Потім допоміжну функцію, в кінці файлу:

У `tests/job_fixtures.py` знайди:

```python
async def chunk_count(runtime: DatabaseRuntime, document_id: DocumentId) -> int:
    async with runtime.transaction() as connection:
        return len(await load_chunks(connection, document_id=document_id))
```

і заміни на:

```python
async def chunk_count(runtime: DatabaseRuntime, document_id: DocumentId) -> int:
    async with runtime.transaction() as connection:
        return len(await load_chunks(connection, document_id=document_id))


def database_went_away() -> OperationalError:
    """A database error that may pass: the connection was lost mid-statement."""
    return OperationalError(
        "DELETE FROM chunks", None, ConnectionError("the database went away")
    )
```

**Дія 2:** тести видалення користуються нею і чекають часу повтору, коли повторюють вручну.

Імпортуй допоміжну функцію й коди та прибери стару константу:

У `tests/test_deletions_integration.py` знайди:

```python
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import Subject
from knowledge_service.audit import AuditAction
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import DocumentId, JobId, SourceId
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import (
    PURGE_FAILED,
    purge_document_chunks,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import Change, reconcile_document
from knowledge_service.ingestion.sources import SourceAccess, SourceRecord
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobNotRetryable, JobStatus
from knowledge_service.persistence.access import load_access_grants
from knowledge_service.persistence.audit import load_audit_events
from knowledge_service.persistence.chunks import load_chunks, load_searchable_chunks
from knowledge_service.persistence.documents import load_document
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
from knowledge_service.settings import Settings
```

і заміни на:

```python
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from job_fixtures import database_went_away
from knowledge_service.access import Subject
from knowledge_service.audit import AuditAction
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import DocumentId, JobId, SourceId
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import (
    purge_document_chunks,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import Change, reconcile_document
from knowledge_service.ingestion.sources import SourceAccess, SourceRecord
from knowledge_service.job_failures import JobFailure
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobNotRetryable, JobStatus
from knowledge_service.persistence.access import load_access_grants
from knowledge_service.persistence.audit import load_audit_events
from knowledge_service.persistence.chunks import load_chunks, load_searchable_chunks
from knowledge_service.persistence.documents import load_document
from knowledge_service.persistence.jobs import enqueue_job, load_job, save_job
from knowledge_service.settings import Settings
```

У `test_a_failed_purge_keeps_chunks_and_can_be_retried` кидай тимчасову помилку й очікуй її код і час повтору:

У `tests/test_deletions_integration.py` знайди:

```python
    retired = await publish(runtime, "old-policy.md", OLD_POLICY)
    (tombstone,) = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )

    async def broken_delete(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        raise ConnectionError("the database went away")

    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(
            runtime, job_id=tombstone.purge_job_id, now=LATER
        )

    assert (failed.status, failed.attempt, failed.last_error_code) == (
        JobStatus.FAILED,
        1,
        PURGE_FAILED,
    )
    assert await stored_chunk_count(runtime, retired.document_id) > 0
    assert await searchable_text(runtime) == []

    async with runtime.transaction() as connection:
        stored_job = await load_job(connection, job_id=tombstone.purge_job_id)
    assert stored_job == failed
    async with runtime.transaction() as connection:
        await save_job(connection, job=failed.retry(queued_at=LATEST))
```

і заміни на:

```python
    retired = await publish(runtime, "old-policy.md", OLD_POLICY)
    (tombstone,) = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )

    async def broken_delete(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        raise database_went_away()

    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(
            runtime, job_id=tombstone.purge_job_id, now=LATER
        )

    assert (failed.status, failed.attempt, failed.last_error_code) == (
        JobStatus.FAILED,
        1,
        JobFailure.DATABASE_UNAVAILABLE,
    )
    assert failed.retry_at is not None
    assert await stored_chunk_count(runtime, retired.document_id) > 0
    assert await searchable_text(runtime) == []

    async with runtime.transaction() as connection:
        stored_job = await load_job(connection, job_id=tombstone.purge_job_id)
    assert stored_job == failed
    async with runtime.transaction() as connection:
        await save_job(connection, job=failed.retry(queued_at=LATEST))
```

У `test_a_purge_that_used_every_attempt_is_queued_again` кидай тимчасову помилку, щоразу повторюй у запланований час і перевір, що бюджет це зупинив:

У `tests/test_deletions_integration.py` знайди:

```python
    retired = await publish(runtime, "old-policy.md", OLD_POLICY)
    (tombstone,) = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )

    async def broken_delete(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        raise ConnectionError("the database went away")

    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(
            runtime, job_id=tombstone.purge_job_id, now=LATER
        )
        # Nothing is queued again while the job still has attempts left.
        assert (
            await requeue_unfinished_purges(runtime, source_id=SOURCE_ID, now=LATER)
            == ()
        )
        for _ in range(failed.max_attempts - 1):
            async with runtime.transaction() as connection:
                await save_job(connection, job=failed.retry(queued_at=LATER))
            failed = await purge_document_chunks(
                runtime, job_id=tombstone.purge_job_id, now=LATER
            )
    with pytest.raises(JobNotRetryable):
        failed.retry(queued_at=LATEST)

    (requeued,) = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=LATEST
    )
    await purge_document_chunks(runtime, job_id=requeued, now=LATEST)
```

і заміни на:

```python
    retired = await publish(runtime, "old-policy.md", OLD_POLICY)
    (tombstone,) = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=LATER
    )

    async def broken_delete(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        raise database_went_away()

    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(
            runtime, job_id=tombstone.purge_job_id, now=LATER
        )
        # Nothing is queued again while the job still has attempts left.
        assert (
            await requeue_unfinished_purges(runtime, source_id=SOURCE_ID, now=LATER)
            == ()
        )
        for _ in range(failed.max_attempts - 1):
            assert failed.retry_at is not None
            due = failed.retry_at
            async with runtime.transaction() as connection:
                await save_job(connection, job=failed.retry(queued_at=due))
            failed = await purge_document_chunks(
                runtime, job_id=tombstone.purge_job_id, now=due
            )
    # The last attempt used the budget, so no retry is scheduled.
    assert (failed.attempt, failed.retry_at) == (failed.max_attempts, None)
    with pytest.raises(JobNotRetryable):
        failed.retry(queued_at=LATEST)

    (requeued,) = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=LATEST
    )
    await purge_document_chunks(runtime, job_id=requeued, now=LATEST)
```

**Дія 3:** тести ідемпотентності M08-T03. Два з них змінюють зміст: повторний запит тепер чекає часу повтору.

Імпортуй допоміжну функцію:

У `tests/test_job_idempotency_integration.py` знайди:

```python
    OBSERVED_AT,
    SOURCE_ID,
    chunk_count,
    insert_source,
    offer_retired,
    stored_job,
```

і заміни на:

```python
    OBSERVED_AT,
    SOURCE_ID,
    chunk_count,
    database_went_away,
    insert_source,
    offer_retired,
    stored_job,
```

Імпортуй прохід повторів і коди та прибери стару константу:

У `tests/test_job_idempotency_integration.py` знайди:

```python
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
```

і заміни на:

```python
from knowledge_service.identifiers import DocumentId, JobId
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import (
    purge_document_chunks,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.job_dispatch import (
    PublishFailed,
    Submission,
    requeue_due_retries,
    submit_job,
)
from knowledge_service.job_failures import JobFailure
from knowledge_service.jobs import InvalidJob, Job, JobKind, JobStatus
from knowledge_service.persistence import jobs as job_store
from knowledge_service.persistence.jobs import (
```

Зроби `broken_delete` тимчасовою помилкою бази:

У `tests/test_job_idempotency_integration.py` знайди:

```python


async def broken_delete(connection: AsyncConnection, *, document_id: DocumentId) -> int:
    raise ConnectionError("the database went away")


async def stored_job_ids(runtime: DatabaseRuntime) -> list[JobId]:
```

і заміни на:

```python


async def broken_delete(connection: AsyncConnection, *, document_id: DocumentId) -> int:
    raise database_went_away()


async def stored_job_ids(runtime: DatabaseRuntime) -> list[JobId]:
```

Заміни два тести «після збою»:

У `tests/test_job_idempotency_integration.py` знайди:

```python
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
```

і заміни на:

```python
    assert await stored_job_ids(runtime) == []


async def test_asking_again_after_a_failed_attempt_waits_for_its_retry_time(
    runtime: DatabaseRuntime,
) -> None:
    job = (await enqueue(runtime, new_job())).job
    failed = job.start(started_at=NOW).fail(
        error_code="boom", finished_at=NOW, retry_at=LATER
    )
    async with runtime.transaction() as connection:
        await save_job(connection, job=failed)

    again = await enqueue(runtime, new_job())

    # The same Job comes back, still waiting: a request does not hurry it.
    assert not again.created
    assert again.job == failed
    assert await stored_job(runtime, job.job_id) == failed


async def test_a_document_tombstoned_again_after_a_failed_purge_is_purged_when_due(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    with monkeypatch.context() as patch:
        patch.setattr(deletions, "delete_chunks", broken_delete)
        failed = await purge_document_chunks(runtime, job_id=purge_id, now=NOW)
    assert failed.retry_at is not None
    await offer_retired(runtime, observed_at=NOW)

    [again] = await tombstone_missing(
        runtime, source_id=SOURCE_ID, seen=set(), observed_at=NOW
    )
    assert again.purge_job_id == purge_id
    # Nothing runs it before its retry time; the retry pass does after.
    assert await requeue_due_retries(runtime, now=NOW) == ()
    assert await requeue_due_retries(runtime, now=failed.retry_at) == (purge_id,)
    purged = await purge_document_chunks(runtime, job_id=purge_id, now=failed.retry_at)

    assert (purged.status, purged.attempt) == (JobStatus.SUCCEEDED, 1)
    assert await chunk_count(runtime, document.document_id) == 0
```

У `test_two_messages_at_once_for_a_failing_attempt_run_it_once` кидай тимчасову помилку:

У `tests/test_job_idempotency_integration.py` знайди:

```python
        calls.append(document_id)
        # Long enough for the other messages to queue up behind the claim.
        await asyncio.sleep(0.2)
        raise ConnectionError("the database went away")

    monkeypatch.setattr(deletions, "delete_chunks", slow_broken_delete)
    results = await asyncio.gather(
```

і заміни на:

```python
        calls.append(document_id)
        # Long enough for the other messages to queue up behind the claim.
        await asyncio.sleep(0.2)
        raise database_went_away()

    monkeypatch.setattr(deletions, "delete_chunks", slow_broken_delete)
    results = await asyncio.gather(
```

і очікуй її код:

У `tests/test_job_idempotency_integration.py` знайди:

```python
    assert (stored.status, stored.attempt, stored.last_error_code) == (
        JobStatus.FAILED,
        1,
        PURGE_FAILED,
    )
```

і заміни на:

```python
    assert (stored.status, stored.attempt, stored.last_error_code) == (
        JobStatus.FAILED,
        1,
        JobFailure.DATABASE_UNAVAILABLE,
    )
```

**Дія 4:** ще два тести зберігали збій, а потім повторювали його, тож вони планують час повтору.

У `test_a_job_queued_again_needs_a_new_message`:

У `tests/test_job_dispatch_integration.py` знайди:

```python
    job = new_job()
    await submit_job(runtime, job, publisher=FakePublisher(), now=NOW)
    async with runtime.transaction() as connection:
        failed = job.start(started_at=NOW).fail(error_code="boom", finished_at=NOW)
        await save_job(connection, job=failed)
        await save_job(connection, job=failed.retry(queued_at=LATER))
    assert await published_at(runtime, job.job_id) is None
```

і заміни на:

```python
    job = new_job()
    await submit_job(runtime, job, publisher=FakePublisher(), now=NOW)
    async with runtime.transaction() as connection:
        failed = job.start(started_at=NOW).fail(
            error_code="boom", finished_at=NOW, retry_at=LATER
        )
        await save_job(connection, job=failed)
        await save_job(connection, job=failed.retry(queued_at=LATER))
    assert await published_at(runtime, job.job_id) is None
```

У `tests/test_records_integration.py` заплануй повтор:

У `tests/test_records_integration.py` знайди:

```python
            failed = running.fail(
                error_code="source_unreachable",
                finished_at=OBSERVED_AT + timedelta(seconds=2),
            )
            await save_job(connection, job=failed)
```

і заміни на:

```python
            failed = running.fail(
                error_code="source_unreachable",
                finished_at=OBSERVED_AT + timedelta(seconds=2),
                retry_at=OBSERVED_AT + timedelta(seconds=3),
            )
            await save_job(connection, job=failed)
```

і перевір, що час переживає збереження в базі:

У `tests/test_records_integration.py` знайди:

```python
        assert stored_job.status is JobStatus.FAILED
        assert stored_job.attempt == 1
        assert stored_job.last_error_code == "source_unreachable"

        async with engine.begin() as connection:
            retried = failed.retry(queued_at=OBSERVED_AT + timedelta(seconds=3))
```

і заміни на:

```python
        assert stored_job.status is JobStatus.FAILED
        assert stored_job.attempt == 1
        assert stored_job.last_error_code == "source_unreachable"
        assert stored_job.retry_at == OBSERVED_AT + timedelta(seconds=3)

        async with engine.begin() as connection:
            retried = failed.retry(queued_at=OBSERVED_AT + timedelta(seconds=3))
```

**Дія 5:** створи тести повторів. Вони використовують фіксований jitter 0,5, тож кожен час повтору точний.

| Тест | Що доводить |
|---|---|
| `test_a_transient_failure_is_queued_again_only_when_due` | на секунду раніше: нічого; вчасно: у черзі, ненадісланий, надісланий, потім успіх |
| `test_a_transient_failure_stops_at_the_attempt_budget` | рівно 3 спроби, заплановані очікування, потім більше ніколи |
| `test_a_permanent_failure_is_never_queued_again` | `KeyError` зупиняється на спробі 1 |
| `test_a_permanent_purge_failure_is_not_asked_for_again_by_later_passes` | блок 7: три проходи синку нічого не додають |
| `test_a_permanent_failure_stops_the_loop_even_after_a_late_retry` | блок 7: «останнє» за `finished_at`, а не `queued_at` |
| `test_a_lost_attempt_is_not_recorded_against_a_later_one` | блок 6: перевірка «та сама спроба» |
| `test_a_lost_connection_is_recorded_as_a_retried_failure` | блок 6: справжній `pg_terminate_backend` |
| `test_a_cancelled_attempt_leaves_the_job_queued_and_nothing_deleted` | блок 5 і відомі обмеження: скасування і `TimeLimitExceeded` відкочуються повністю |
| `test_two_retry_passes_at_once_queue_each_job_once` | `SKIP LOCKED` розводить два проходи |
| `test_the_retry_pass_takes_the_oldest_retry_first_up_to_its_limit`, `..._limit_is_bounded`, `..._needs_utc` | порядок і межі |
| `test_the_database_refuses_a_retry_time_on_a_job_that_did_not_fail` | `ck_jobs_retry_at` |
| `test_the_migration_schedules_open_failures_and_keeps_final_ones_closed` | міграція вгору і вниз |


Створи `tests/test_job_retry_integration.py`:

```python
"""Retries owned by the stored Job, against real PostgreSQL.

A failed attempt is classified. A transient one gets a retry time, and the
retry pass queues the Job again once that time has come, until its attempts
run out. A permanent one is never queued again.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic import command
from dramatiq.middleware.time_limit import TimeLimitExceeded
from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from database_fixtures import migration_config, migration_environment
from job_fixtures import (
    OBSERVED_AT,
    SOURCE_ID,
    chunk_count,
    database_went_away,
    insert_source,
    stored_job,
    tombstoned_with_purge,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, JobId
from knowledge_service.ingestion import deletions
from knowledge_service.ingestion.deletions import (
    purge_document_chunks,
    requeue_unfinished_purges,
)
from knowledge_service.job_dispatch import publish_pending_jobs, requeue_due_retries
from knowledge_service.job_failures import JobFailure, RetryPolicy
from knowledge_service.jobs import Job, JobAlreadyClaimed, JobKind, JobStatus
from knowledge_service.persistence.jobs import enqueue_job, requeue_due_jobs, save_job
from knowledge_service.persistence.schema import chunks, jobs
from knowledge_service.settings import Settings

pytestmark = pytest.mark.integration

NOW = OBSERVED_AT + timedelta(minutes=5)
FAR_FUTURE = OBSERVED_AT + timedelta(days=30)
# The middle of every delay window, so retry times are exact in assertions.
POLICY = RetryPolicy(jitter=lambda: 0.5)


class RecordingPublisher:
    """Records every Job it is asked to send."""

    kinds = frozenset({JobKind.PURGE_DOCUMENT_CHUNKS})

    def __init__(self) -> None:
        self.sent: list[JobId] = []

    def publish(self, job: Job) -> None:
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


async def job_ids(runtime: DatabaseRuntime) -> list[JobId]:
    async with runtime.transaction() as connection:
        return [
            JobId(job_id)
            for job_id in await connection.scalars(sa.select(jobs.c.job_id))
        ]


async def drop_my_connection(
    connection: AsyncConnection, *, document_id: DocumentId
) -> int:
    # The server ends this session, as a restart or a failover would.
    await connection.execute(sa.text("select pg_terminate_backend(pg_backend_pid())"))
    return 0


async def lost_connection(
    connection: AsyncConnection, *, document_id: DocumentId
) -> int:
    raise database_went_away()


async def bug(connection: AsyncConnection, *, document_id: DocumentId) -> int:
    raise KeyError("a programming error")


async def purge(runtime: DatabaseRuntime, job_id: JobId, now: datetime) -> Job:
    return await purge_document_chunks(
        runtime, job_id=job_id, now=now, retry_policy=POLICY
    )


async def test_a_transient_failure_is_queued_again_only_when_due(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", lost_connection)

    failed = await purge(runtime, purge_id, NOW)

    assert (failed.status, failed.attempt, failed.last_error_code) == (
        JobStatus.FAILED,
        1,
        JobFailure.DATABASE_UNAVAILABLE,
    )
    assert failed.retry_at == NOW + POLICY.delay(1)
    due = failed.retry_at
    assert due is not None
    assert await requeue_due_retries(runtime, now=due - timedelta(seconds=1)) == ()
    assert await requeue_due_retries(runtime, now=due) == (purge_id,)

    requeued = await stored_job(runtime, purge_id)
    assert (requeued.status, requeued.attempt, requeued.retry_at) == (
        JobStatus.QUEUED,
        1,
        None,
    )
    # Queued again means unpublished again: the relay sends a new message.
    publisher = RecordingPublisher()
    await publish_pending_jobs(runtime, publisher=publisher, now=due)
    assert publisher.sent == [purge_id]

    monkeypatch.undo()
    succeeded = await purge(runtime, purge_id, due)
    assert (succeeded.status, succeeded.attempt) == (JobStatus.SUCCEEDED, 1)
    assert await chunk_count(runtime, document.document_id) == 0


async def test_a_transient_failure_stops_at_the_attempt_budget(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", lost_connection)
    now = NOW
    attempts: list[Job] = []

    for _ in range(10):
        attempts.append(await purge(runtime, purge_id, now))
        if attempts[-1].retry_at is None:
            break
        now = attempts[-1].retry_at
        assert await requeue_due_retries(runtime, now=now) == (purge_id,)

    last = attempts[-1]
    assert len(attempts) == last.max_attempts == 3
    assert (last.status, last.attempt, last.retry_at) == (JobStatus.FAILED, 3, None)
    assert [job.retry_at for job in attempts[:-1]] == [
        NOW + POLICY.delay(1),
        NOW + POLICY.delay(1) + POLICY.delay(2),
    ]
    assert await requeue_due_retries(runtime, now=FAR_FUTURE) == ()


async def test_a_permanent_failure_is_never_queued_again(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", bug)

    failed = await purge(runtime, purge_id, NOW)

    assert (failed.attempt, failed.last_error_code, failed.retry_at) == (
        1,
        JobFailure.PROGRAMMING_ERROR,
        None,
    )
    assert await requeue_due_retries(runtime, now=FAR_FUTURE) == ()
    assert (await stored_job(runtime, purge_id)).status is JobStatus.FAILED


async def test_a_permanent_purge_failure_is_not_asked_for_again_by_later_passes(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", bug)
    await purge(runtime, purge_id, NOW)

    # Each synchronization pass asks for unfinished purges; none comes back.
    for _ in range(3):
        assert (
            await requeue_unfinished_purges(
                runtime, source_id=SOURCE_ID, now=FAR_FUTURE
            )
            == ()
        )

    assert await job_ids(runtime) == [purge_id]
    assert await chunk_count(runtime, document.document_id) > 0


async def test_a_permanent_failure_stops_the_loop_even_after_a_late_retry(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    monkeypatch.setattr(deletions, "delete_chunks", lost_connection)
    now = NOW
    while (failed := await purge(runtime, purge_id, now)).retry_at is not None:
        now = failed.retry_at
        await requeue_due_retries(runtime, now=now)
    # A synchronization pass that started before that last retry asks for a
    # new purge, which then fails permanently.
    monkeypatch.setattr(deletions, "delete_chunks", bug)
    [second] = await requeue_unfinished_purges(
        runtime, source_id=SOURCE_ID, now=NOW + timedelta(seconds=1)
    )
    await purge(runtime, second, now + timedelta(hours=1))

    for hours in (2, 3, 4):
        assert (
            await requeue_unfinished_purges(
                runtime, source_id=SOURCE_ID, now=NOW + timedelta(seconds=hours)
            )
            == ()
        )
    assert len(await job_ids(runtime)) == 2
    assert await chunk_count(runtime, document.document_id) > 0


async def test_a_lost_attempt_is_not_recorded_against_a_later_one(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    _document, purge_id = await tombstoned_with_purge(runtime)
    load_job = deletions.load_job
    loads: list[JobId] = []

    async def meanwhile_another_delivery(
        connection: AsyncConnection, *, job_id: JobId, for_update: bool = False
    ) -> Job | None:
        loads.append(job_id)
        # The second load is this delivery's own, after its connection died.
        # Before it, another delivery fails the Job and it is queued again.
        if len(loads) == 2:
            monkeypatch.setattr(deletions, "delete_chunks", lost_connection)
            other = await purge(runtime, purge_id, NOW)
            assert other.retry_at is not None
            await requeue_due_retries(runtime, now=other.retry_at)
        return await load_job(connection, job_id=job_id, for_update=for_update)

    monkeypatch.setattr(deletions, "load_job", meanwhile_another_delivery)
    monkeypatch.setattr(deletions, "delete_chunks", drop_my_connection)

    # This delivery's clock is an hour ahead of the relay's.
    with pytest.raises(JobAlreadyClaimed, match="another delivery") as refused:
        await purge(runtime, purge_id, NOW + timedelta(hours=1))

    stored = await stored_job(runtime, purge_id)
    assert (stored.status, stored.attempt) == (JobStatus.QUEUED, 1)
    assert refused.value.job == stored


async def test_a_lost_connection_is_recorded_as_a_retried_failure(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)

    monkeypatch.setattr(deletions, "delete_chunks", drop_my_connection)
    failed = await purge(runtime, purge_id, NOW)

    assert (failed.status, failed.attempt, failed.last_error_code) == (
        JobStatus.FAILED,
        1,
        JobFailure.DATABASE_UNAVAILABLE,
    )
    assert failed.retry_at == NOW + POLICY.delay(1)
    assert await stored_job(runtime, purge_id) == failed
    assert await chunk_count(runtime, document.document_id) > 0


@pytest.mark.parametrize(
    "interrupt", [asyncio.CancelledError, TimeLimitExceeded], ids=["cancel", "limit"]
)
async def test_a_cancelled_attempt_leaves_the_job_queued_and_nothing_deleted(
    runtime: DatabaseRuntime,
    monkeypatch: pytest.MonkeyPatch,
    interrupt: type[BaseException],
) -> None:
    document, purge_id = await tombstoned_with_purge(runtime)
    chunks_before = await chunk_count(runtime, document.document_id)

    async def interrupted(
        connection: AsyncConnection, *, document_id: DocumentId
    ) -> int:
        await connection.execute(
            sa.delete(chunks).where(chunks.c.document_id == document_id.value)
        )
        raise interrupt()

    monkeypatch.setattr(deletions, "delete_chunks", interrupted)
    with pytest.raises(interrupt):
        await purge(runtime, purge_id, NOW)

    # Rolled back whole: the stuck-work sweep (M08-T08) owns this Job now.
    stored = await stored_job(runtime, purge_id)
    assert (stored.status, stored.attempt) == (JobStatus.QUEUED, 0)
    assert await chunk_count(runtime, document.document_id) == chunks_before
    assert await requeue_due_retries(runtime, now=FAR_FUTURE) == ()


async def test_two_retry_passes_at_once_queue_each_job_once(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        for _ in range(4):
            job = Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=uuid.uuid4(),
                queued_at=OBSERVED_AT,
            )
            await enqueue_job(connection, job)
            failed = job.start(started_at=NOW).fail(
                error_code="boom", finished_at=NOW, retry_at=NOW
            )
            await save_job(connection, job=failed)

    async def pass_and_hold() -> tuple[Job, ...]:
        async with runtime.transaction() as connection:
            requeued = await requeue_due_jobs(connection, now=NOW, limit=10)
            # Keep the rows locked, so the other pass runs meanwhile.
            await asyncio.sleep(0.2)
            return requeued

    first, second = await asyncio.gather(pass_and_hold(), pass_and_hold())

    ids = [job.job_id for job in first + second]
    assert len(ids) == len(set(ids)) == 4


async def test_the_retry_pass_takes_the_oldest_retry_first_up_to_its_limit(
    runtime: DatabaseRuntime,
) -> None:
    stored: list[Job] = []
    async with runtime.transaction() as connection:
        for minutes in (2, 1):
            job = Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=uuid.uuid4(),
                queued_at=OBSERVED_AT,
            )
            await enqueue_job(connection, job)
            retry_at = NOW + timedelta(minutes=minutes)
            failed = job.start(started_at=NOW).fail(
                error_code="boom", finished_at=NOW, retry_at=retry_at
            )
            stored.append(await save_job(connection, job=failed))

    requeued = await requeue_due_retries(runtime, now=FAR_FUTURE, limit=1)

    assert requeued == (stored[1].job_id,)


@pytest.mark.parametrize("limit", [0, 1_001, True, 2.0])
async def test_the_retry_pass_limit_is_bounded(
    runtime: DatabaseRuntime, limit: object
) -> None:
    with pytest.raises(ValueError, match="limit"):
        await requeue_due_retries(runtime, now=NOW, limit=limit)  # pyright: ignore[reportArgumentType]


async def test_the_retry_pass_needs_utc(runtime: DatabaseRuntime) -> None:
    with pytest.raises(ValueError, match="UTC"):
        await requeue_due_retries(runtime, now=datetime(2026, 11, 23, 9, 0))


async def test_the_database_refuses_a_retry_time_on_a_job_that_did_not_fail(
    runtime: DatabaseRuntime,
) -> None:
    job = Job.request(
        job_id=JobId.new(),
        kind=JobKind.PURGE_DOCUMENT_CHUNKS,
        target_id=uuid.uuid4(),
        queued_at=OBSERVED_AT,
    )
    async with runtime.transaction() as connection:
        await enqueue_job(connection, job)

    with pytest.raises(IntegrityError, match="ck_jobs_retry_at"):
        async with runtime.transaction() as connection:
            await connection.execute(
                sa.update(jobs)
                .where(jobs.c.job_id == job.job_id.value)
                .values(retry_at=NOW)
            )


def test_the_migration_schedules_open_failures_and_keeps_final_ones_closed(
    migrated_database: str,
) -> None:
    url = make_url(migrated_database)
    config = migration_config()
    with migration_environment(url):
        command.downgrade(config, "e5f6a7b8c9d0")
    insert_failed = sa.text(
        "insert into jobs (job_id, kind, target_id, status, attempt, max_attempts,"
        " queued_at, started_at, finished_at, last_error_code) values (:job_id,"
        " 'purge_document_chunks', :target_id, 'failed', :attempt, 3, :at, :at,"
        " :at, 'chunk_purge_failed')"
    )
    with_attempts_left, used_up = uuid.uuid4(), uuid.uuid4()
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as connection:
            for job_id, attempt in ((with_attempts_left, 1), (used_up, 3)):
                connection.execute(
                    insert_failed,
                    {
                        "job_id": job_id,
                        "target_id": uuid.uuid4(),
                        "attempt": attempt,
                        "at": NOW,
                    },
                )

        with migration_environment(url):
            command.upgrade(config, "head")
        with engine.connect() as connection:
            retry_at = dict(
                connection.execute(sa.select(jobs.c.job_id, jobs.c.retry_at)).all()
            )

        # Open before, so still open: due at once. Used up stays closed.
        assert retry_at == {with_attempts_left: NOW, used_up: None}

        # A permanent failure with attempts left must stay closed on the way down.
        with engine.begin() as connection:
            connection.execute(
                sa.update(jobs)
                .where(jobs.c.job_id == with_attempts_left)
                .values(retry_at=None)
            )
        with migration_environment(url):
            command.downgrade(config, "e5f6a7b8c9d0")
        with engine.connect() as connection:
            attempt = connection.scalar(
                sa.text("select attempt from jobs where job_id = :job_id"),
                {"job_id": with_attempts_left},
            )
        assert attempt == 3
        with migration_environment(url):
            command.upgrade(config, "head")
    finally:
        engine.dispose()
```

**Перевірка:** `uv run pytest tests/test_job_retry_integration.py tests/test_deletions_integration.py tests/test_job_idempotency_integration.py tests/test_job_dispatch_integration.py tests/test_records_integration.py -q -m integration` показує `65 passed`.

### Крок 9 — У цьому кроці коду немає: зламай навмисно

Цей крок робить кожен збій схожим на тимчасовий і показує, які тести це помічають.

**Дія 1:** у `fail_attempt` у `src/knowledge_service/job_failures.py` зміни:

```python
    if failure.transient and failed_attempts < job.max_attempts:
```

на:

```python
    if failed_attempts < job.max_attempts:
```

**Дія 2:** запусти:

```bash
uv run pytest tests/test_job_failures.py tests/test_job_retry_integration.py -q -m "integration or not integration"
```

Ти побачиш `4 failed, 54 passed`. Баги й відмови тепер отримують час повтору, тож тести «постійні збої ніколи не повторюються» падають.

**Дія 3:** поверни зміну і запусти команду знову: `58 passed`.

**Перевірка:** порівняй кількість зі своїм прогнозом із кроку 1 і запиши її у файл доказів.

### Крок 10 — У цьому кроці коду немає: онови три документи

Цей крок записує, хто володіє повторами й тайм-аутами, там, де їх шукатимуть читачі.


**Дія 1:** в архітектурі, у рядку `worker`, скажи, що relay спершу запускає прохід повторів:

У `docs/architecture.md` знайди:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs and no tools, where a reply holding a tool call is unreadable; `AnswerSettings` gives the budget), `has_active_content` / `unsourced_links` (`answer_content`, what an Answer may hold whatever a Document told the model: no Markdown image, loading HTML tag, or mermaid block, and only links an Evidence it cites contains; `held_from` marks where a stream stops sending text until the check), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`, with `DatabaseAuditTrail` (`audit_trail`) as its `AuditTrail`: one `question.asked` audit event per stream, committed before the decision's Citations and completion are sent; finds the Evidence the request's reader may see, rereads it as the reader with `evidence_still_visible`, and judges it before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply, has its `CitationCheck` (`CurrentCitations`, each call in a short transaction of its own) reread every cited Chunk as the reader with `confirm_citations`, withdrawing the Answer if one fails, and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `VectorEvidenceSource` (`vector_evidence`, the stream's Evidence from PostgreSQL: checks the reader, embeds the Question, requires one vector from its own model, and runs the vector search with the request's Authorization Context, so eligibility is decided in the same SQL that ranks; no reranking, so its signals carry no score), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |
| `worker` | Queue transport: the Redis broker and the worker process | `build_broker` (`broker`, a Dramatiq `RedisBroker` with JSON messages, the `knowledge-service.v1` namespace, the `maintenance` and `synchronization` queues, a 60-second default time limit, a one-day message age limit, and no Dramatiq retries; `ActorOwnsOptions` drops any limit a message sets for itself, so only its actor's limits apply; Redis calls make one attempt with bounded timeouts), `declare_actors` / `send_ping` (`actors`, the harmless `ping` job, whose token is checked on both sides), `broker_ready` (`health`, a readiness probe that is false, within one overall deadline, whenever Redis does not answer PING or any part of the check fails), `declare_job_actors` / `DramatiqJobPublisher` / `run_stored_job` (`jobs`, one `run_<queue>_job` actor per queue of the kinds in `HANDLERS`, today only `purge_document_chunks`; a message carries one canonical Job ID, and the worker reads the Job from PostgreSQL, runs it only while it is queued, and returns the stored Job as the outcome of a duplicate; `JobRunner` gives each message its own event loop and database engine), `relay` (one pass of `publish_pending_jobs`), and `main`, the module the `dramatiq` command loads |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level. Among them, `roles` holds the Application Roles policy:
```

і заміни на:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs and no tools, where a reply holding a tool call is unreadable; `AnswerSettings` gives the budget), `has_active_content` / `unsourced_links` (`answer_content`, what an Answer may hold whatever a Document told the model: no Markdown image, loading HTML tag, or mermaid block, and only links an Evidence it cites contains; `held_from` marks where a stream stops sending text until the check), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`, with `DatabaseAuditTrail` (`audit_trail`) as its `AuditTrail`: one `question.asked` audit event per stream, committed before the decision's Citations and completion are sent; finds the Evidence the request's reader may see, rereads it as the reader with `evidence_still_visible`, and judges it before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply, has its `CitationCheck` (`CurrentCitations`, each call in a short transaction of its own) reread every cited Chunk as the reader with `confirm_citations`, withdrawing the Answer if one fails, and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `VectorEvidenceSource` (`vector_evidence`, the stream's Evidence from PostgreSQL: checks the reader, embeds the Question, requires one vector from its own model, and runs the vector search with the request's Authorization Context, so eligibility is decided in the same SQL that ranks; no reranking, so its signals carry no score), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |
| `worker` | Queue transport: the Redis broker and the worker process | `build_broker` (`broker`, a Dramatiq `RedisBroker` with JSON messages, the `knowledge-service.v1` namespace, the `maintenance` and `synchronization` queues, a 60-second default time limit, a one-day message age limit, and no Dramatiq retries; `ActorOwnsOptions` drops any limit a message sets for itself, so only its actor's limits apply; Redis calls make one attempt with bounded timeouts), `declare_actors` / `send_ping` (`actors`, the harmless `ping` job, whose token is checked on both sides), `broker_ready` (`health`, a readiness probe that is false, within one overall deadline, whenever Redis does not answer PING or any part of the check fails), `declare_job_actors` / `DramatiqJobPublisher` / `run_stored_job` (`jobs`, one `run_<queue>_job` actor per queue of the kinds in `HANDLERS`, today only `purge_document_chunks`; a message carries one canonical Job ID, and the worker reads the Job from PostgreSQL, runs it only while it is queued, and returns the stored Job as the outcome of a duplicate; `JobRunner` gives each message its own event loop and database engine), `relay` (one pass: `requeue_due_retries`, then `publish_pending_jobs`), and `main`, the module the `dramatiq` command loads |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level. Among them, `roles` holds the Application Roles policy:
```

онови абзац про намір і додай після нього абзац про повтори й тайм-аути:

У `docs/architecture.md` знайди:

```markdown

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

`telemetry` decides what leaves the process as logs. `configure_telemetry`
installs one root handler that writes JSON lines, and its `TelemetryFilter`
keeps only the fields `TELEMETRY_FIELDS` lists, with plain values, only this
```

і заміни на:

```markdown

A Job's intent is its kind and target, such as "purge the Chunks of Document
D". `enqueue_job` keeps at most one open Job per intent: queued, running, or
failed with a retry scheduled. The partial unique index `uq_jobs_open_intent`
enforces it, so asking for the same work again, even at the same moment,
returns the open Job (`Enqueued.created` is false, `Submission.duplicate` is
true) instead of storing a second one, and an open Job that failed keeps
waiting for its retry time. A handler claims a queued Job under a row lock and
stores its outcome, success or failure, in the same transaction; a claim that
finds the Job no longer queued raises `JobAlreadyClaimed` with the stored Job,
and `run_stored_job` returns that Job as the outcome of every duplicate
message, so every delivery reports the same result.

The stored Job is the one layer that retries. Dramatiq retries nothing, model
adapters keep their SDK retries off, and repository code never loops.
`fail_attempt` (`job_failures`) classifies a failed attempt by its error as a
Source, database, or model that is unavailable (transient) or refused
(permanent), or a programming error (permanent), and stores that code. Only a
transient failure with attempts left gets a `retry_at`: half of a doubling
backoff (30 seconds, capped at 15 minutes) plus a random share of the other
half. `requeue_due_retries`, run first by every `make worker-relay` pass, is
the only path a failed Job takes back to the queue, and only once its
`retry_at` has come; `Job.retry` refuses a permanent failure and an early one.
Each call owns its own timeout: the database's statement, connect, and pool
timeouts and each model adapter's deadline end a slow call as a classified,
retried failure. When the database drops the connection under an attempt,
the claim's lock goes with it, so the purge records the attempt in a new
transaction by the error that started it. The actor's 60-second time limit
and a cancellation are only backstops: the attempt's transaction rolls back
and the Job waits as queued for M08-T08. A tombstoned Document whose last
purge failed permanently is not given a new purge by later synchronization
passes; one that ran out of attempts on a passing failure is.

`telemetry` decides what leaves the process as logs. `configure_telemetry`
installs one root handler that writes JSON lines, and its `TelemetryFilter`
keeps only the fields `TELEMETRY_FIELDS` lists, with plain values, only this
```

і онови рядок `jobs` у таблиці інваріантів:

У `docs/architecture.md` знайди:

```markdown
| `messages` | One Message per `(conversation_id, ordinal)`; only an assistant Message has an `outcome`: `answered`, `abstained`, or `failed`. |
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code; at most one open Job (queued, running, or failed with attempts left) per kind and target. |
| `audit_events` | An action, target kind, decision (`allowed` or `denied`), and outcome from fixed lists, with optional request and Synchronization Run IDs; no raw content: metadata keys are short names, string values one plain word, and the bag is capped at 1024 bytes. Rows are only ever inserted. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |
```

і заміни на:

```markdown
| `messages` | One Message per `(conversation_id, ordinal)`; only an assistant Message has an `outcome`: `answered`, `abstained`, or `failed`. |
| `message_references` | One row per `(message_id, position)` and per `(message_id, chunk_id)`; `content_version >= 1`; removed with its Message. No foreign key to `chunks`, so a reference outlives the Chunk it names and History can tell that it is gone. |
| `message_feedback` | One rating per `(message_id, user_id)`, replaced when the same User rates again. |
| `jobs` | A status that agrees with the timestamps it carries; a bounded attempt count; a capped error code; a retry time only on a failed Job with attempts left, never before it finished; at most one open Job (queued, running, or failed with a retry scheduled) per kind and target. |
| `audit_events` | An action, target kind, decision (`allowed` or `denied`), and outcome from fixed lists, with optional request and Synchronization Run IDs; no raw content: metadata keys are short names, string values one plain word, and the bag is capped at 1024 bytes. Rows are only ever inserted. |
| `evaluation_runs` | An artifact location only once the run has succeeded. |
```

**Дія 2:** у моделі загроз онови залишковий ризик у рядку `A replayed job writes twice` і додай рядок після нього:

У `docs/threat-model.md` знайди:

```markdown
| A file removed or unreadable deletes or keeps the wrong Documents | An unreadable folder is unavailable, not deleted; a failed pass tombstones nothing | Incremental sync (M08-T10) |
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one; whatever the model then writes passes the Answer's content checks | None |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A failed Job with attempts left runs again only when the same work is requested again, until retries are owned (M08-T04); a message for an earlier attempt can start the next one (M08-T04) |

### Retrieval
```

і заміни на:

```markdown
| A file removed or unreadable deletes or keeps the wrong Documents | An unreadable folder is unavailable, not deleted; a failed pass tombstones nothing | Incremental sync (M08-T10) |
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one; whatever the model then writes passes the Answer's content checks | None |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |

### Retrieval
```

**Дія 3:** у посібнику з експлуатації скажи, що relay має запускатися за розкладом:

У `docs/operations.md` знайди:

```markdown

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

і заміни на:

```markdown

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. A failed Job comes back only through `make worker-relay`, which queues due retries before it publishes, so run it on a schedule. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

**Перевірка:** `make docs-check` завершується з кодом 0.

### Крок 11 — У цьому кроці коду немає: перевірки, докази і прогрес

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

**Дія 2:** заверши файл доказів: результати перевірок і кількість із кроку 9 у «Verification», знахідки рев'ю й відомі обмеження в «Review», дизайн у «Design» і «Decision», а також свої відповіді на питання для рефлексії нижче. Потім познач завдання виконаним у `docs/curriculum/PROGRESS.md` і зроби коміт з `m08-t04: assign retry and timeout ownership`.

**Перевірка:** усі перевірки проходять, а `git status` чистий після коміту.

## Питання для рефлексії

- Очищення падає о 10:00:00, бо база обірвала з'єднання. За таблицею з «Перед початком», блок 3: між якими двома моментами виконається його друга спроба, і що визначає точний момент?
- Уяви, що адаптер моделі лишив повтори SDK увімкненими, 3 на виклик, а Job теж повторює 3 рази. Скільки викликів отримає модель, яка лежить, на один Job, і чому очікування між ними більше не йде за одним планом?
- У токена Confluence минув строк. Який код отримає Job синхронізації, чому його не повторюють, і що має зробити людина, перш ніж робота зможе вдатися?
- База завершує сесію воркера посеред очищення. Пройди кроками, що тепер робить очищення, і поясни, чому перед записом збою воно мусить перевірити, що Job досі на тій самій спробі.
- Що стається з очищенням, яке обірвав 60-секундний ліміт часу, і який наступний урок подбає, щоб воно виконалося знову?
- Що ти побачив би в таблиці `jobs` або в логах, щоб зрозуміти, що класифікація хибна: збій, який мав повторюватись, не повторюється, або навпаки?

## Чекліст завершення

- [ ] Файл доказів містить інваріант, власників і твої прогнози, записані до змін.
- [ ] Job має `retry_at`; `retry` відмовляє остаточному збою, вичерпаному бюджету і передчасному часу; `tests/test_jobs.py` має 9 тестів і проходить.
- [ ] `job_failures.py` класифікує помилки за сімома кодами і планує `retry_at` лише для тимчасового збою зі спробами; `tests/test_job_failures.py` (40) проходить.
- [ ] Міграція `f6a7b8c9d0e1` додає `retry_at`, `ck_jobs_retry_at`, `ix_jobs_due_retry` і нове правило відкритого Job і працює в обидва боки.
- [ ] `enqueue_job` більше не ставить Job, що впав, у чергу одразу; `requeue_due_jobs` і `requeue_due_retries` — єдиний шлях назад у чергу; `make worker-relay` запускає його першим.
- [ ] Очищення користується `fail_attempt`, записує втрачену спробу в новій транзакції лише на тій самій спробі, а очищення, що впало назавжди, синхронізація більше не просить.
- [ ] `tests/test_job_retry_integration.py` (18) і оновлені integration-тести проходять.
- [ ] Ти зламав код навмисно, побачив, як падають тести, і повернув усе назад.
- [ ] `docs/architecture.md`, `docs/threat-model.md` і `docs/operations.md` описують, хто володіє повторами й тайм-аутами.
- [ ] Усі перевірки проходять: формат, lint, типи, unit, integration, документація.
- [ ] Докази, статус завдання, рядок у PROGRESS і коміт зроблено.

Коли напишеш файли, поділись ними або виводом помилок, і я перевірю їх за цим чеклістом.
