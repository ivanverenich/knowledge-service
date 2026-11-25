# M09-T02 — Реалізувати адміністрування Sources

Англійська версія: [M09-T02-implement-source-administration.md](M09-T02-implement-source-administration.md)

Завдання: [M09-T02 — Implement Source administration](../curriculum/milestones/M09-product-interfaces/M09-T02-implement-source-administration.md)

## Що ви зробите

Цей урок дає операторам API для Sources (джерел, звідки сервіс читає документи). Сьогодні Source з'являється лише тоді, коли розробник вручну вставляє рядок у таблицю `sources`. Після уроку людина з роллю content-manager реєструє, читає, переглядає списком, змінює й вимикає Source через HTTP. Кожне налаштування перевіряється, жоден секрет не проходить через API, а кожна зміна записується в журнал аудиту.

Конкретний приклад. Контент-менеджер реєструє папку з довідником команди:

```text
POST /v1/sources
{"kind": "local_directory", "location": "/srv/handbook", "display_name": "Handbook",
 "default_principals": ["group:/engineering"]}
-> 201, Location: /v1/sources/6f1c...
   {"source_id": "6f1c...", "enabled": true, "credentials_reference": null, ...}

POST /v1/sources   (the same folder again)
-> 409 {"code": "source_exists", ...}

POST /v1/sources   ("location": "/srv/handbook/")
-> 422 {"code": "invalid_source_settings",
        "message": "a local_directory location must be an absolute folder path, ..."}
```

Ці шість маршрутів M09-T01 запланував у таблиці маршрутів. Цей урок їх будує й переводить їхні рядки зі стану «заплановано» у «обслуговується».

| Що просить завдання | Де це зроблено |
|---|---|
| Створювати, змінювати, вмикати, вимикати й показувати списком Sources | Блоки 1 і 4; кроки 5 і 6 |
| Використовувати посилання на секрети, а не самі секрети | Блок 3; крок 5 |
| Перевіряти кожне налаштування | Блоки 2 і 3; крок 5 |
| Перевіряти Application Roles | Блок 1; кроки 6 і 7 |
| Записувати події аудиту | Блок 5; кроки 2 і 5 |
| HTTP-тести на відмову за роллю, дубль ідентичності, неправильну конфігурацію, приховування секретів і пагінацію | Кроки 7 і 8 |
| Справжня інфраструктура там, де перевіряється її поведінка | Крок 8 (PostgreSQL для унікальності, блокувань, аудиту й сторінок) |
| Повтор і скасування описані явно, зовнішні виклики обмежені | Блоки 2 і 6; крок 11 |
| Пояснити, що спростувало б дизайн | Крок 9 і запитання для роздумів |

Терміни, без яких урок не обійдеться:

- **Source:** місце, звідки беруться документи, наприклад локальна папка чи сайт Confluence. Його рядок у `sources` каже, якого він типу, де він і хто може читати його елементи.
- **Ідентичність Source:** тип і розташування разом. Два Sources з однаковим типом і розташуванням читали б ті самі документи двічі, тому база дозволяє лише один.
- **Посилання на секрет** (secret reference): назва секрету, що зберігається деінде, наприклад `confluence/acme-prod`. Сервіс знаходить секрет за цією назвою, коли він потрібен; API бачить тільки назву.
- **Канонічне написання** (canonical spelling): єдиний прийнятий спосіб записати значення. `/srv/handbook` канонічне; `/srv/handbook/` і `/srv/./handbook` означають ту саму папку, але їх відхиляють.
- **Подія аудиту** (audit event): один рядок в `audit_events`, що каже, хто що зробив, із чим і коли, без вмісту.
- **Часткова зміна** (PATCH): запит, що надсилає лише ті налаштування, які змінює; усі інші зберігають своє значення.

## Перш ніж почати

Цей розділ пояснює кожне рішення, на яке спирається код уроку. Кожен блок показує реальну ситуацію в проєкті, що відбувається сьогодні, факт, що лежить під нею, і виправлення. Прочитайте його спочатку: кроки на нього спираються.

### 1. Хто може змінювати Source і в якому порядку перевіряється запит

Сьогодні Source — це рядок, який вставляє розробник. `tests/job_fixtures.py` робить це звичайним SQL, і так само роблять засівачі оцінювання. Ніхто не питає, хто цей розробник, і ніщо не записує, що Source з'явився.

Щойно з'являється HTTP-маршрут, запит може надіслати будь-хто, хто дістається до API. Source вирішує, яку папку читає worker і хто може читати знайдене, тому його зміна — привілейована дія. Модель ролей з M07-T05 уже має для цього операцію: `configure_sources`, яку дозволяє лише роль `content-manager`.

Кожен маршрут цього уроку перевіряє три речі в одному порядку, тому самому, який M09-T01 закріпив для всіх маршрутів:

| Порядок | Перевірка | Відповідь, коли вона не проходить |
|---|---|---|
| 1 | Хто викликає: дійсний bearer-токен | 401 |
| 2 | Чи дозволяє його роль `configure_sources` | 403 |
| 3 | Що він надіслав: ID у шляху, `limit` і `cursor` або тіло | 422, 404 або 409 |

Прихований базовий факт: FastAPI читає й перевіряє оголошене тіло чи параметр запиту ще до того, як запуститься ваша функція маршруту. Якби маршрут оголосив параметр `body: SourceRegistration`, клієнт без ролі отримав би 422 з описом правил вводу ще до перевірки ролі. Тому ці маршрути читають тіло самі, після `require`, з тим самим обмеженням розміру, що й маршрут Question.

Наскільки це було б погано: відхилений клієнт дізнався б форму правильного Source і те, які папки вже зареєстровані (пробуючи реєстрації й читаючи 409). Сьогодні це небагато, але кожен наступний адміністративний маршрут копіює цей шаблон.

```python
# Was (what FastAPI would do with a declared body): input first
async def register_source(request: Request, body: SourceRegistration): ...

# Became: who, then whether they may, then what they send
caller = await authenticate(request)
require(caller.roles, Operation.CONFIGURE_SOURCES)
registration = cast(SourceRegistration, await read_body(request, SourceRegistration))
```

Як на рецепції будівлі: спершу перевіряють перепустку, а вже потім читають бланк, який ви подаєте.

### 2. Source — це його тип і розташування, в одному написанні

Двоє контент-менеджерів реєструють папку довідника одночасно, з двох вкладок браузера.

| Момент | Запит A | Запит B | Таблиця `sources` |
|---|---|---|---|
| 10:00:00.000 | Чи зареєстровано `/srv/handbook`? Ні | | порожня |
| 10:00:00.001 | | Чи зареєстровано `/srv/handbook`? Ні | порожня |
| 10:00:00.002 | Вставити рядок A, commit | | A |
| 10:00:00.003 | | Вставити рядок B, commit | A і B |

Прихований базовий факт: «перевірити, потім вставити» в коді застосунку — це два оператори, і між ними може пройти інша транзакція. Жоден запит не побачив рядка іншого, бо той ще не був закомічений, коли кожен перевіряв. Лише база даних може вирішити «перший виграє», і робить це через обмеження унікальності.

У таблиці воно вже є: `uq_sources_kind_location`. Цей урок змушує вставку спиратися на нього, а не перевіряти наперед:

```sql
-- Was: check, then insert (a race)
SELECT 1 FROM sources WHERE kind = 'local_directory' AND location = '/srv/handbook';
INSERT INTO sources (...) VALUES (...);

-- Became: one statement; the constraint decides
INSERT INTO sources (...) VALUES (...)
ON CONFLICT ON CONSTRAINT uq_sources_kind_location DO NOTHING
RETURNING source_id;
```

Коли оператор не повертає рядка, папка вже зайнята, і API відповідає 409 `source_exists`. Другий запит чекає на рядок першого, доки той не закомітиться, а потім нічого не робить.

Але обмеження унікальності порівнює лише текст. `/srv/handbook`, `/srv/handbook/` і `/srv/./handbook` — три різні рядки для однієї папки, і кожен став би окремим Source. Виправлення: приймати лише одне написання кожного розташування й відхиляти інші. Відхиляти безпечніше, ніж переписувати: оператор бачить точно те, що збережено.

| Тип | Приймається | Відхиляється |
|---|---|---|
| `local_directory` | `/srv/handbook` | `srv/handbook`, `/`, `//srv/handbook`, `/srv/handbook/`, `/srv/./handbook`, `/srv/../handbook`, `/srv//handbook`, `/srv/cafe` + комбінований наголос |
| `confluence` | `https://acme.atlassian.net/wiki` | `http://...`, `https://Acme...`, `https://acme...:443/...`, `https://acme...:/...`, `https://acme.atlassian.net./wiki`, `https://127.0.0.1/wiki`, `/` у кінці, користувач, пароль, запит, фрагмент чи `%` |

Тип і розташування не змінюються після реєстрації, тож ідентичність Source лишається сталою. Інша папка — це реєстрація іншого Source.

Як бібліотечний каталог, що не приймає другу картку на книжку, яка в ньому вже є, і вимагає, щоб кожна картка писала назву однаково.

### 3. Посилання на секрет називає секрет і ніколи його не містить

Source типу Confluence потребуватиме API-токена, коли M08-T11 збудує свій клієнт. Цей токен не можна зберігати в `sources`, показувати у відповіді API, писати в лог чи копіювати в подію аудиту.

Тому Source зберігає лише **посилання на секрет**: назву на кшталт `confluence/acme-prod`. Коли клієнт Confluence запуститься, він знайде токен у сховищі секретів за цією назвою. Правило назви навмисно вузьке:

| Посилання | Результат | Чому |
|---|---|---|
| `confluence/acme-prod` | приймається | слова малими літерами, з'єднані `-`, до чотирьох частин через `/` |
| `ATATT3xFfGF0T4kq2` | відхиляється | великі літери: так виглядає вставлений токен Atlassian |
| `ghp_16C7e42F...` | відхиляється | `_` і великі літери: вставлений токен GitHub |
| `confluence/acme prod` | відхиляється | пробіл |
| будь-що для `local_directory` | відхиляється | папці не потрібні облікові дані |

Другий прихований факт стосується повідомлень про помилки. Власні помилки валідації Pydantic мають поле `input` зі значенням, що не пройшло. Якщо оператор вставить справжній токен у `credentials_reference`, помилка, що цитує ввід, повернула б токен по мережі й у будь-які логи цієї відповіді. Цей застосунок ніколи не надсилає помилки Pydantic (його обробник відповідає загальним `validation_error`), а кожне повідомлення `InvalidSourceSettings` — це незмінне речення про правило. Тому маршрут може безпечно повернути це повідомлення: воно називає порушене правило й ніколи — значення.

```python
# Was (what quoting would look like): the value travels back
raise InvalidSourceSettings(f"{reference!r} is not a secret name")

# Became: a fixed sentence about the rule
raise InvalidSourceSettings(
    "credentials_reference must name a secret, such as"
    " confluence/acme-prod, never hold the secret itself"
)
```

Секрет може нести й адреса Confluence: `https://bot:TOKEN@acme.atlassian.net/wiki` чи `...?token=...`. Правило сайту відхиляє будь-якого користувача, пароль, запит чи фрагмент.

Як гардероб: ви віддаєте номерок, а не вміст пальта.

### 4. Зміна надсилає лише те, що змінює, а деякі налаштування не змінюються ніколи

Контент-менеджер перейменовує Source довідника й не чіпає решти:

```text
PATCH /v1/sources/6f1c...
{"display_name": "Engineering handbook"}
```

Як це працює, вирішують два приховані факти про Pydantic.

Перший: поле моделі зі значенням за замовчуванням не відрізняє «відсутнє» від «надіслане зі значенням за замовчуванням». `SourceChanges.display_name` за замовчуванням `None`, тож `{}` і `{"display_name": null}` після розбору виглядають однаково. Модель пам'ятає, які поля назвало тіло, у `model_fields_set`. Маршрут передає далі лише ці поля, тож пропущене поле зберігає своє значення, а явний `null` доходить до правил і відхиляється там, де він не має сенсу.

Другий: за замовчуванням Pydantic ігнорує невідомі поля й перетворює типи, які може: `"yes"` стає `True`, а поле з помилкою в назві тихо нічого не робить. Обидві моделі запиту мають `extra="forbid"` і `strict=True`, тож `{"enabled": false}`, `{"location": "/srv/other"}`, `{"password": "..."}` чи `{"default_public": "yes"}` відхиляються з 422 ще до звернення до сховища.

| Тіло | Результат |
|---|---|
| `{"display_name": "Engineering handbook"}` | перейменовано; решта лишається |
| `{"default_principals": []}` для Source, що їх не має | 200, нічого не записано, жодної події аудиту |
| `{"credentials_reference": "confluence/x"}` для папки | 422: папці не потрібні облікові дані |
| `{"location": "/srv/other"}` | 422: тип і розташування не змінюються |
| `{}` | 422: зміна називає хоча б одне налаштування |

Увесь результат перевіряється так, як перевірявся б новий Source, тож зміна, що пасує лише іншому типу, відхиляється. Збережений Source, який уже порушує правило (вставлений вручну до появи цих правил), зміною не виправити, бо його розташування не змінюється; API відповідає 409 `source_needs_repair`, а не звинувачує поле, якого клієнт не надсилав.

Як бланк, де ви виправляєте одну клітинку й ставите поруч підпис; клітинки, яких ви не торкалися, лишаються як були.

### 5. Кожна зміна та її подія аудиту комітяться разом

Кожна операція блокує рядок Source, записує його й вставляє свою подію аудиту, все в одній транзакції. Якщо вставка аудиту не вдасться, зміна теж відкотиться, тож журнал ніколи не пропустить зміни, що відбулася.

| Операція | Дія аудиту | Метадані |
|---|---|---|
| реєстрація | `source.registered` | `{"kind": "local_directory"}` |
| зміна | `source.updated` | які налаштування змінилися, як `true`: `{"display_name": true}` |
| увімкнення | `source.enabled` | немає |
| вимкнення | `source.disabled` | немає |

Метадані ніколи не містять значення: ні розташування, ні назви, ні принципалів, ні посилання. Шлях до папки може назвати людину чи проєкт; журналу аудиту досить сказати, що щось змінилося і хто це змінив. Зміна чи перемикання, що лишає всі значення як були, нічого не пише й нічого не записує в аудит.

Три нові дії — це нові значення для обмеження-перевірки `ck_audit_events_action`, тож урок додає міграцію. Її відкат (downgrade) відмовляється працювати, поки існує хоч одна з нових подій, бо журнал аудиту лише доповнюється: видалити записану зміну, щоб вмістився відкат, означало б стерти історію.

### 6. Вимкнення чекає на планувальник, але не зупиняє прохід, що вже йде

Планувальник з M08-T10 ставить у чергу Jobs синхронізації для ввімкнених Sources. Він читає кожен Source під спільним блокуванням (`SELECT ... FOR SHARE`) і ставить Job у чергу в тій самій транзакції.

| Момент | Планувальник | Контент-менеджер | Що в базі |
|---|---|---|---|
| 10:00:00 | Блокує Source S спільно, бачить `enabled` | | S увімкнено |
| 10:00:01 | | Вимкнути S: `SELECT ... FOR UPDATE` чекає | S увімкнено, спільне блокування тримається |
| 10:00:02 | Ставить Job J для S, commit | | J у черзі |
| 10:00:02 | | Блокування отримано; S вимкнено, commit | S вимкнено, J у черзі |

Прихований базовий факт: блокування `FOR UPDATE` не можна отримати, поки інша транзакція тримає `FOR SHARE` на тому самому рядку. Тож Job завжди ставиться в чергу або до зміни, або взагалі не ставиться. Планувальник ніколи не бачить напівухваленого рішення.

Чого це не робить: воно не скасовує J. Коли worker візьме J, він знову прочитає Source, побачить, що той вимкнений, і завершить Job з `source_gone`, а повтор це не змінить. Прохід, що вже йде, завершиться з налаштуваннями, які прочитав на старті. Скасування Jobs — справа адміністрування синхронізації (M09-T03).

### Інші знахідки рев'ю

Незалежне рев'ю атакувало прототип до написання цього посібника. Кожну знахідку нижче виправлено, і для кожної є тест, який падав до виправлення.

- **Один сайт Confluence у кількох написаннях.** `https://acme.net/wiki`, `https://acme.net:/wiki`, `https://acme.net./wiki` і `https://acme.net:08443/wiki` поруч із `:8443` зберігалися як окремі Sources. Тепер хост має бути назвою хоста малими літерами (не IP-адресою і без `.` в кінці), а хост і порт мають читатися точно так, як Python записує їх назад.
- **Одна назва папки у двох формах Unicode.** `/srv/café`, записаний одним символом `é`, і записаний як `e` плюс комбінований наголос — різні рядки, але macOS вважає їх однією папкою. Тепер розташування має бути вже в NFC, складеній формі.
- **Зміна звинувачувала поле, якого клієнт не надсилав.** Source, вставлений вручну з `/datasets/x/public/`, відхиляв кожну зміну правилом папки. Тепер API відповідає 409 `source_needs_repair`.
- **Повна остання сторінка Sources казала, що є наступна.** Тест сторінок використовував 5 Sources, ніколи точне кратне розміру сторінки, тож `>=` замість `>` лишався непоміченим. Тепер тест читає 4 Sources сторінками по 2.
- **Збережений принципал, що не називає нікого, ховався, а потім переписувався.** Стовпець `default_principals` дозволяє елемент NULL, який синхронізація відхиляє. Читання його відкидало, тож Source виглядав здоровим, а наступне перейменування тихо зберігало коротший список без запису в аудит. Тепер NULL показується як `null`, а зміна отримує 409 `source_needs_repair`.
- **Тест-властивість про відмови міг пройти без відмови.** Деякі згенеровані значення були правильними, тож нічого не перевірялося. Тепер кожне значення містить розрив рядка, що порушує кожне правило, і тест вимагає відмови.

### Відомі обмеження

- Символічне посилання, файлова система без розрізнення регістру чи друга адреса того самого сайту все ще дають два Sources для одного місця: API не бачить файлової системи worker-а і не розв'язує адрес.
- Секрет, який випадково пасує правилу назви, наприклад одне слово малими літерами, зберігається як назва.
- API не перевіряє, що папка існує: процес API може не бачити диска worker-а. Відсутня папка проявиться в наступному Job синхронізації цього Source.
- Source типу Confluence можна зареєструвати, але його синхронізація завершується з `source_misconfigured`, доки M08-T11 не збудує клієнт Confluence.
- Нові читачі за замовчуванням дістаються збережених Documents під час наступного проходу Source, а не одразу.
- Вимкнення Source не скасовує Job, що вже в черзі чи виконується (блок 6).
- Клієнт, відхилений за роллю, отримує 403, який не записує жодна подія аудиту, як і на кожному іншому маршруті.

## Крок 1 — Прочитати, передбачити і побачити, як перевірка падає

Без коду в цьому кроці.

Прочитайте файл завдання, потім блоки 1–6 цього уроку. До початку програмування запишіть відповіді на три передбачення:

- Дві реєстрації `/srv/handbook` виконуються одночасно. Скільки рядків буде в `sources` після цього і що отримає другий клієнт?
- Користувач лише з роллю `user` надсилає `POST /v1/sources` з тілом, що порушує всі правила. Який статус повернеться?
- `PATCH` з `{"default_principals": []}` для Source, що не має принципалів. Скільки подій аудиту він додасть?

Потім запустіть файл тестів, який створює урок, щоб побачити, що його ще немає:

```bash
uv run pytest tests/test_source_administration.py -q
```

Очікувано: `ERROR: file or directory not found: tests/test_source_administration.py`, `no tests ran`, код виходу 4.

## Крок 2 — Додати дії аудиту та їхню міграцію

Крок із кодом.

**Простими словами:** журнал аудиту приймає дії лише з фіксованого списку, і в Python, і в базі даних. Цей крок додає до обох списків три нові дії: Source змінено, увімкнено й вимкнено. Міграція вчить наявну базу довшого списку й відмовляється скасовувати себе, коли такі події вже є.

| Файл | Зміна |
|---|---|
| `src/knowledge_service/audit.py` | три нові значення `AuditAction` |
| `src/knowledge_service/persistence/schema.py` | ті самі три в `ck_audit_events_action` |
| `migrations/versions/a3b4c5d6e7f8_source_administration.py` | нова міграція |

У `src/knowledge_service/audit.py` додайте після `JOB_CANCELLED`:

```python
    SOURCE_UPDATED = "source.updated"
    SOURCE_ENABLED = "source.enabled"
    SOURCE_DISABLED = "source.disabled"
```

У `src/knowledge_service/persistence/schema.py` перевірка дій `audit_events` закінчується так:

```python
        " 'embedding_migration.switched', 'job.retried', 'job.cancelled',"
        " 'source.updated', 'source.enabled', 'source.disabled')",
```

Створіть `migrations/versions/a3b4c5d6e7f8_source_administration.py`:

```python
"""source administration

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-11-26 09:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3b4c5d6e7f8"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"

OLD_ACTIONS = (
    "'source.registered', 'document.availability_changed',"
    " 'conversation.deleted', 'job.queued', 'job.failed',"
    " 'evaluation_run.started', 'question.asked', 'document.read',"
    " 'document.access_changed', 'synchronization.finished',"
    " 'message.rated', 'embedding_migration.started',"
    " 'embedding_migration.switched', 'job.retried', 'job.cancelled'"
)
NEW_ACTIONS = OLD_ACTIONS + ", 'source.updated', 'source.enabled', 'source.disabled'"


def _audit_actions(actions: str) -> None:
    op.drop_constraint("ck_audit_events_action", "audit_events", type_="check")
    op.create_check_constraint(
        "ck_audit_events_action", "audit_events", f"action in ({actions})"
    )


def upgrade() -> None:
    _audit_actions(NEW_ACTIONS)


def downgrade() -> None:
    # The audit log is append-only: a recorded change to a Source is never
    # deleted to make a downgrade fit, so the downgrade stops instead.
    op.execute(
        "do $$ begin"
        " if exists (select 1 from audit_events where action in"
        " ('source.updated', 'source.enabled', 'source.disabled')) then"
        " raise exception 'source changes are recorded; cannot downgrade';"
        " end if; end $$"
    )
    _audit_actions(OLD_ACTIONS)
```

Перевірка: `uv run alembic heads` друкує `a3b4c5d6e7f8 (head)`.

## Крок 3 — Додати моделі запиту й відповіді

Крок із кодом.

**Простими словами:** ці класи описують, що може надіслати запит і що показує відповідь. Запит, що називає невідоме поле чи надсилає значення іншого типу, відхиляється, а не вгадується. Відповідь показує кожне налаштування Source і ніколи — секрет, бо Source його ніколи не містить.

| Модель | Для чого |
|---|---|
| `SourceRegistration` | тіло `POST /v1/sources` |
| `SourceChanges` | тіло `PATCH /v1/sources/{source_id}`; кожне поле необов'язкове |
| `SourceView` | один Source у відповіді |
| `SourcePage` | сторінка Sources: `items` і `next_cursor` |

У `src/knowledge_service/contracts.py` змініть імпорти на:

```python
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from knowledge_service.source_administration import (
    MAX_CREDENTIALS_REFERENCE,
    MAX_DISPLAY_NAME,
    MAX_LOCATION,
    MAX_PRINCIPAL,
    MAX_PRINCIPALS,
)
```

і додайте в кінець файлу:

```python
# A request body that names a field the API does not know, or gives a value
# of another JSON type, is refused, never coerced or ignored.
_STRICT_BODY = ConfigDict(extra="forbid", strict=True)
Principal = Annotated[str, Field(max_length=MAX_PRINCIPAL)]


class SourceRegistration(BaseModel):
    """What registers a Source: what it is, where, and who may read its items.

    `credentials_reference` names a secret in the service's secret store; the
    secret itself never passes through the API. `default_public` and
    `default_principals` say who may read the items of a Source that carries
    no access of its own, such as a local folder; by default, nobody.
    """

    model_config = _STRICT_BODY

    kind: str = Field(max_length=32)
    location: str = Field(max_length=MAX_LOCATION)
    display_name: str = Field(max_length=MAX_DISPLAY_NAME)
    credentials_reference: str | None = Field(
        default=None, max_length=MAX_CREDENTIALS_REFERENCE
    )
    default_public: bool = False
    default_principals: list[Principal] = Field(
        default_factory=list, max_length=MAX_PRINCIPALS
    )


class SourceChanges(BaseModel):
    """The settings a change may set; a field left out keeps its value.

    `kind` and `location` are what a Source is, so they never change: a
    different folder is a different Source.
    """

    model_config = _STRICT_BODY

    display_name: str | None = Field(default=None, max_length=MAX_DISPLAY_NAME)
    credentials_reference: str | None = Field(
        default=None, max_length=MAX_CREDENTIALS_REFERENCE
    )
    default_public: bool | None = None
    default_principals: list[Principal] | None = Field(
        default=None, max_length=MAX_PRINCIPALS
    )


class SourceView(BaseModel):
    """A Source as an operator configures it; never a secret."""

    source_id: UUID
    kind: str
    location: str
    display_name: str
    credentials_reference: str | None
    enabled: bool
    default_public: bool
    # A null names nobody: the Source needs repair before it can synchronize.
    default_principals: list[str | None]
    created_at: datetime
    updated_at: datetime


class SourcePage(BaseModel):
    """A page of Sources, newest first; `next_cursor` is None on the last one."""

    items: list[SourceView]
    next_cursor: str | None
```

Налаштування — це пласкі поля, `default_public` і `default_principals`, а не один вкладений об'єкт. Вкладена модель додала б розділ `$defs` у схему тіла запиту в OpenAPI, а написаний вручну опис тіла (крок 6) не може на нього посилатися.

Межі довжин імпортуються з `source_administration`, модуля, який створює крок 5, тож моделі запиту й правила ніколи не розійдуться в межах. Запустіть перевірку цього кроку після кроку 5.

Перевірка (після кроку 5): `uv run pyright src/knowledge_service/contracts.py` показує 0 помилок.

## Крок 4 — Читати й писати рядки Source

Крок із кодом.

**Простими словами:** цей код — єдине місце, що говорить SQL про Sources для адміністрування. Він зберігає новий Source і дає базі відхилити другий для тієї самої папки, читає один Source (і може заблокувати його для зміни), зберігає змінювані налаштування й читає Sources сторінками, від найновіших.

| Функція | Що робить |
|---|---|
| `SourceRecord` | один збережений Source у вигляді простих значень |
| `insert_source_record` | `INSERT ... ON CONFLICT ON CONSTRAINT uq_sources_kind_location DO NOTHING RETURNING`; False, коли зайнято |
| `load_source_record` | один Source; `for_update=True` додає `FOR UPDATE` |
| `save_source_record` | оновлює налаштування, які може змінити оператор |
| `load_source_page` | сторінка за ключем `(created_at, source_id)`, від найновіших |

У `src/knowledge_service/persistence/sources.py` змініть імпорти на:

```python
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection
```

і додайте перед `source_runs_statement`:

```python
@dataclass(frozen=True, slots=True)
class SourceRecord:
    """A stored Source as an operator configures it, as plain values."""

    source_id: SourceId
    kind: str
    location: str
    display_name: str
    credentials_reference: str | None
    enabled: bool
    default_public: bool
    # PostgreSQL arrays may hold NULL elements; such a Source needs repair.
    default_principals: tuple[str | None, ...]
    created_at: datetime
    updated_at: datetime


_RECORD_COLUMNS = (
    sources.c.source_id,
    sources.c.kind,
    sources.c.location,
    sources.c.display_name,
    sources.c.credentials_reference,
    sources.c.enabled,
    sources.c.default_public,
    sources.c.default_principals,
    sources.c.created_at,
    sources.c.updated_at,
)


def _source_record(row: sa.RowMapping) -> SourceRecord:
    return SourceRecord(
        source_id=SourceId(cast(UUID, row["source_id"])),
        kind=row["kind"],
        location=row["location"],
        display_name=row["display_name"],
        credentials_reference=row["credentials_reference"],
        enabled=row["enabled"],
        default_public=row["default_public"],
        default_principals=tuple(row["default_principals"]),
        # The session's time zone decides the offset psycopg returns.
        created_at=cast(datetime, row["created_at"]).astimezone(UTC),
        updated_at=cast(datetime, row["updated_at"]).astimezone(UTC),
    )


async def insert_source_record(
    connection: AsyncConnection, record: SourceRecord
) -> bool:
    """Store a new Source; False, storing nothing, when its kind and location exist.

    The unique constraint decides, so two registrations of the same folder
    at the same moment store one Source, never two.
    """
    inserted = await connection.scalar(
        postgresql.insert(sources)
        .values(
            source_id=record.source_id.value,
            kind=record.kind,
            location=record.location,
            display_name=record.display_name,
            credentials_reference=record.credentials_reference,
            enabled=record.enabled,
            default_public=record.default_public,
            default_principals=list(record.default_principals),
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
        .on_conflict_do_nothing(constraint="uq_sources_kind_location")
        .returning(sources.c.source_id)
    )
    return inserted is not None


async def load_source_record(
    connection: AsyncConnection, *, source_id: SourceId, for_update: bool = False
) -> SourceRecord | None:
    """One stored Source; with `for_update`, locked until the transaction ends."""
    statement = sa.select(*_RECORD_COLUMNS).where(
        sources.c.source_id == source_id.value
    )
    if for_update:
        statement = statement.with_for_update()
    row = (await connection.execute(statement)).mappings().one_or_none()
    return None if row is None else _source_record(row)


async def save_source_record(connection: AsyncConnection, record: SourceRecord) -> None:
    """Store what an operator may change of an existing Source."""
    await connection.execute(
        sa.update(sources)
        .where(sources.c.source_id == record.source_id.value)
        .values(
            display_name=record.display_name,
            credentials_reference=record.credentials_reference,
            enabled=record.enabled,
            default_public=record.default_public,
            default_principals=list(record.default_principals),
            updated_at=record.updated_at,
        )
    )


async def load_source_page(
    connection: AsyncConnection,
    *,
    limit: int,
    after: tuple[datetime, SourceId] | None = None,
) -> tuple[SourceRecord, ...]:
    """Up to `limit` Sources, newest first; with `after`, those after that one."""
    statement = sa.select(*_RECORD_COLUMNS)
    if after is not None:
        created_at, source_id = after
        statement = statement.where(
            sa.tuple_(sources.c.created_at, sources.c.source_id)
            < sa.tuple_(sa.literal(created_at), sa.literal(source_id.value))
        )
    statement = statement.order_by(
        sources.c.created_at.desc(), sources.c.source_id.desc()
    ).limit(limit)
    rows = (await connection.execute(statement)).mappings().all()
    return tuple(_source_record(row) for row in rows)
```

Порядок і фільтр сторінок повторюють дизайн пагінації за ключем з M09-T01 для списку рішень: наступна сторінка просить рядки строго перед `(created_at, source_id)` останнього, тож Source, зареєстрований між двома запитами, нічого не повторює й не пропускає.

Перевірка: `uv run pyright src/knowledge_service/persistence/sources.py` показує 0 помилок.

## Крок 5 — Написати модуль адміністрування Sources

Крок із кодом.

**Простими словами:** цей файл тримає правила й операції. Він перевіряє кожне налаштування, яке надсилає людина, відхиляє значення в другому написанні чи секрет замість його назви й каже, яке правило порушено, не повторюючи значення. Кожна операція блокує Source, зберігає його й пише свою подію аудиту в тій самій транзакції; зміна, що нічого не змінює, нічого не пише.

| Частина | Що робить |
|---|---|
| `SourceDefinition` | усі налаштування, перевірені в `__post_init__`; принципали зберігаються впорядкованими |
| `SourceDefinition.of` | будує визначення з простих значень будь-якого типу, як-от із запиту |
| `_check_folder`, `_check_site` | одне написання шляху до папки чи адреси сайту (блок 2) |
| `_check_credentials` | назва секрету, ніколи не секрет; для папки — жодної (блок 3) |
| `_checked_access` | `user:<id>` або `group:<id>`, щонайбільше 100, без повторів, не разом із `default_public` |
| `register_source` | вставка, `SourceExists`, коли зайнято, `source.registered` |
| `change_source` | блокування, перевірка всього результату, запис лише коли щось змінилося, `source.updated` |
| `set_source_enabled` | блокування, запис лише коли стан змінюється, `source.enabled` або `source.disabled` |
| `source_cursor`, `source_position` | курсор списку Sources, в одному написанні |
| `DatabaseSourceAdministration` | сховище, яким користується застосунок, з годинником |

Створіть `src/knowledge_service/source_administration.py`:

```python
"""Registering and changing Sources: every setting checked, every change recorded.

A Source is what it reads (`kind`) and where (`location`); those two never
change, and no two Sources share them. An operator may rename a Source,
name another secret for it, change who may read its items, and turn it on
or off. Each change locks the Source, writes it, and records who changed it
in the audit log, all in one transaction.

No secret passes through here. `credentials_reference` is the name of a
secret in the service's secret store, such as `confluence/acme-prod`; the
adapter that reads the Source looks the secret up by that name. An error
names the rule a setting broke, never the value it was given.
"""

import posixpath
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Final, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import (
    AuditAction,
    AuditDecision,
    AuditEvent,
    AuditOutcome,
    AuditTargetKind,
    AuditValue,
)
from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import AuditEventId, SourceId, UserId
from knowledge_service.ingestion.sources import SourceKind
from knowledge_service.pagination import InvalidPage, decode_cursor, encode_cursor
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.sources import (
    SourceRecord,
    insert_source_record,
    load_source_page,
    load_source_record,
    save_source_record,
)

MAX_DISPLAY_NAME: Final = 200
MAX_LOCATION: Final = 2048
MAX_CREDENTIALS_REFERENCE: Final = 128
MAX_PRINCIPALS: Final = 100
MAX_PRINCIPAL: Final = 256
MAX_SOURCE_LIST: Final = 500
# What an operator may change; `kind` and `location` are what a Source is.
CHANGEABLE: Final = frozenset(
    {"display_name", "credentials_reference", "default_public", "default_principals"}
)
# A secret's name: lowercase words joined by `-`, in up to four `/` parts.
_SECRET_NAME = re.compile(r"[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*){0,3}")
# A host name: lowercase labels, the last starting with a letter, so an IP
# address, a trailing `.`, or one bare label such as `localhost` is refused.
_HOST = re.compile(r"(?:[a-z0-9]+(?:-+[a-z0-9]+)*\.)+[a-z][a-z0-9]*(?:-+[a-z0-9]+)*")
_SOURCE_LIST = "sources"
_SOURCE_CURSOR_KEYS = frozenset({"created_at", "source_id"})


class InvalidSourceSettings(ValueError):
    """A setting breaks a rule; the message names the rule, never the value."""


class SourceExists(Exception):
    """A Source of the same kind and location is already registered."""


class SourceNotFound(LookupError):
    """No Source has this ID."""


class SourceNeedsRepair(Exception):
    """The stored Source breaks a rule it was not checked against when stored.

    No change can fix it, since its kind and location never change: it is
    registered again instead.
    """


@dataclass(frozen=True, slots=True)
class SourceDefinition:
    """Every setting of a Source, each checked against its rule.

    Each value has one spelling: a folder path or site address written
    another way is refused, not rewritten, so two spellings of one folder
    can never be two Sources. The principals are kept sorted.
    """

    kind: SourceKind
    location: str
    display_name: str
    credentials_reference: str | None
    default_public: bool
    default_principals: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.kind) is not SourceKind:
            raise InvalidSourceSettings(_KIND_RULE)
        _check_display_name(self.display_name)
        if self.kind is SourceKind.LOCAL_DIRECTORY:
            _check_folder(self.location)
        else:
            _check_site(self.location)
        _check_credentials(self.kind, self.credentials_reference)
        principals = _checked_access(self.default_public, self.default_principals)
        object.__setattr__(self, "default_principals", principals)

    @classmethod
    def of(
        cls,
        *,
        kind: object,
        location: object,
        display_name: object,
        credentials_reference: object = None,
        default_public: object = False,
        default_principals: object = (),
    ) -> SourceDefinition:
        """A definition from plain values of any type, such as a request's.

        Each value is checked here, its type included, so the casts below
        only name what the checks in `__post_init__` then prove.
        """
        if type(kind) is not str or kind not in set(SourceKind):
            raise InvalidSourceSettings(_KIND_RULE)
        if type(default_principals) not in (tuple, list):
            raise InvalidSourceSettings("default_principals must be a list")
        return cls(
            kind=SourceKind(kind),
            location=cast(str, location),
            display_name=cast(str, display_name),
            credentials_reference=cast("str | None", credentials_reference),
            default_public=cast(bool, default_public),
            default_principals=tuple(cast("list[str]", default_principals)),
        )


def _check_display_name(name: object) -> None:
    if (
        type(name) is not str
        or not name
        or len(name) > MAX_DISPLAY_NAME
        or name != name.strip()
        or not name.isprintable()
    ):
        raise InvalidSourceSettings(
            f"display_name must be visible text of 1 to {MAX_DISPLAY_NAME}"
            " characters, with no space around it"
        )


def _check_folder(location: object) -> None:
    # `normpath` keeps a leading `//`, which POSIX lets a system read
    # differently from `/`.
    if (
        type(location) is not str
        or len(location) > MAX_LOCATION
        or not location.isprintable()
        # One spelling of each letter: on macOS, `é` and `e` followed by a
        # combining accent name the same folder.
        or unicodedata.normalize("NFC", location) != location
        or not location.startswith("/")
        or location.startswith("//")
        or location == "/"
        or posixpath.normpath(location) != location
    ):
        raise InvalidSourceSettings(
            "a local_directory location must be an absolute folder path, not"
            " the root, with no `.`, `..`, doubled, or trailing `/`"
        )


def _check_site(location: object) -> None:
    problem = InvalidSourceSettings(
        "a confluence location must be an https address with a lowercase host"
        " name, not an IP address, and no user, password, port 443, query,"
        " fragment, `%`, or trailing `/`"
    )
    if (
        type(location) is not str
        or len(location) > MAX_LOCATION
        or not location.isascii()
        or not location.isprintable()
        or any(mark in location for mark in " %?#@\\")
    ):
        raise problem
    try:
        parts = urlsplit(location)
        port = parts.port
    except ValueError:
        raise problem from None
    path = parts.path
    host = parts.hostname or ""
    if (
        parts.scheme != "https"
        or _HOST.fullmatch(host) is None
        # The host and port as written, so `:`, `:08443`, or `:443` is refused.
        or parts.netloc != (host if port is None else f"{host}:{port}")
        or port == 443
        or (path and (path.endswith("/") or posixpath.normpath(path) != path))
        or urlunsplit(parts) != location
    ):
        raise problem


def _check_credentials(kind: SourceKind, reference: object) -> None:
    if kind is SourceKind.LOCAL_DIRECTORY:
        if reference is not None:
            raise InvalidSourceSettings("a local_directory needs no credentials")
        return
    if (
        type(reference) is not str
        or len(reference) > MAX_CREDENTIALS_REFERENCE
        or _SECRET_NAME.fullmatch(reference) is None
    ):
        raise InvalidSourceSettings(
            "credentials_reference must name a secret, such as"
            " confluence/acme-prod, never hold the secret itself"
        )


def _checked_access(public: object, principals: tuple[object, ...]) -> tuple[str, ...]:
    if type(public) is not bool:
        raise InvalidSourceSettings("default_public must be true or false")
    if len(principals) > MAX_PRINCIPALS:
        raise InvalidSourceSettings(
            f"default_principals holds at most {MAX_PRINCIPALS} principals"
        )
    checked: list[str] = []
    for principal in principals:
        if type(principal) is not str or len(principal) > MAX_PRINCIPAL:
            raise InvalidSourceSettings(_PRINCIPAL_RULE)
        kind, separator, name = principal.partition(":")
        if (
            not separator
            or kind not in ("user", "group")
            or not name
            or name != name.strip()
            or not name.isprintable()
        ):
            raise InvalidSourceSettings(_PRINCIPAL_RULE)
        checked.append(principal)
    if len(set(checked)) != len(checked):
        raise InvalidSourceSettings("default_principals must not repeat a principal")
    if public and checked:
        raise InvalidSourceSettings(
            "a public Source must not also list default_principals"
        )
    return tuple(sorted(checked))


_PRINCIPAL_RULE = "each default principal must be user:<id> or group:<id>"
_KIND_RULE = "kind must be one of: " + ", ".join(SourceKind)


def definition_of(record: SourceRecord) -> SourceDefinition:
    """A stored Source's settings, checked as a new one would be."""
    return SourceDefinition.of(
        kind=record.kind,
        location=record.location,
        display_name=record.display_name,
        credentials_reference=record.credentials_reference,
        default_public=record.default_public,
        default_principals=record.default_principals,
    )


def source_cursor(record: SourceRecord) -> str:
    """The cursor of the page that starts after `record` in the Source list."""
    return encode_cursor(
        _SOURCE_LIST,
        {
            "created_at": record.created_at.isoformat(),
            "source_id": str(record.source_id.value),
        },
    )


def source_position(cursor: str) -> tuple[datetime, SourceId]:
    """Where a `source_cursor` says the next page starts; else `InvalidPage`."""
    position = decode_cursor(cursor, listing=_SOURCE_LIST, keys=_SOURCE_CURSOR_KEYS)
    created_text, source_text = position["created_at"], position["source_id"]
    try:
        created_at = datetime.fromisoformat(created_text)
        source_id = UUID(source_text)
    except ValueError:
        raise InvalidPage("the cursor is not one this API gave") from None
    if (
        created_at.utcoffset() != timedelta(0)
        or created_at.isoformat() != created_text
        or str(source_id) != source_text
    ):
        raise InvalidPage("the cursor is not one this API gave")
    return created_at, SourceId(source_id)


async def register_source(
    runtime: DatabaseRuntime,
    *,
    definition: SourceDefinition,
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> SourceRecord:
    """Store a new, enabled Source; `SourceExists` if its kind and location exist."""
    _require_utc(now)
    record = SourceRecord(
        source_id=SourceId.new(),
        kind=definition.kind.value,
        location=definition.location,
        display_name=definition.display_name,
        credentials_reference=definition.credentials_reference,
        enabled=True,
        default_public=definition.default_public,
        default_principals=definition.default_principals,
        created_at=now,
        updated_at=now,
    )
    async with runtime.transaction() as connection:
        if not await insert_source_record(connection, record):
            raise SourceExists("a source of this kind and location is registered")
        await record_audit_event(
            connection,
            _source_event(
                record,
                action=AuditAction.SOURCE_REGISTERED,
                operator=operator,
                at=now,
                request_id=request_id,
                metadata={"kind": record.kind},
            ),
        )
    return record


async def change_source(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    changes: Mapping[str, object],
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> SourceRecord:
    """Set some of a Source's changeable settings; the rest stay.

    The whole result is checked, so a change that fits only another kind,
    such as a secret for a local folder, is refused. A change that sets
    every setting to the value it has writes and records nothing.
    """
    _require_utc(now)
    unknown = set(changes) - CHANGEABLE
    if not changes or unknown:
        raise InvalidSourceSettings(
            "a change sets one or more of display_name, credentials_reference,"
            " default_public, and default_principals"
        )
    async with runtime.transaction() as connection:
        record = await _lock(connection, source_id=source_id)
        try:
            current = definition_of(record)
        except InvalidSourceSettings:
            raise SourceNeedsRepair("the stored source breaks a rule") from None
        wanted = SourceDefinition.of(
            kind=current.kind.value,
            location=current.location,
            display_name=changes.get("display_name", current.display_name),
            credentials_reference=changes.get(
                "credentials_reference", current.credentials_reference
            ),
            default_public=changes.get("default_public", current.default_public),
            default_principals=changes.get(
                "default_principals", current.default_principals
            ),
        )
        changed = {
            name: True
            for name in sorted(CHANGEABLE)
            if getattr(wanted, name) != getattr(current, name)
        }
        if not changed:
            return record
        updated = replace(
            record,
            display_name=wanted.display_name,
            credentials_reference=wanted.credentials_reference,
            default_public=wanted.default_public,
            default_principals=wanted.default_principals,
            updated_at=max(now, record.updated_at),
        )
        await save_source_record(connection, updated)
        await record_audit_event(
            connection,
            _source_event(
                updated,
                action=AuditAction.SOURCE_UPDATED,
                operator=operator,
                at=now,
                request_id=request_id,
                metadata=changed,
            ),
        )
    return updated


async def set_source_enabled(
    runtime: DatabaseRuntime,
    *,
    source_id: SourceId,
    enabled: bool,
    operator: UserId,
    now: datetime,
    request_id: UUID | None = None,
) -> SourceRecord:
    """Turn a Source's synchronization on or off; the same state records nothing.

    The lock waits for a scheduler that is queueing a Job for this Source,
    so that Job is queued before the change, never after it.
    """
    _require_utc(now)
    async with runtime.transaction() as connection:
        record = await _lock(connection, source_id=source_id)
        if record.enabled is enabled:
            return record
        updated = replace(
            record, enabled=enabled, updated_at=max(now, record.updated_at)
        )
        await save_source_record(connection, updated)
        await record_audit_event(
            connection,
            _source_event(
                updated,
                action=AuditAction.SOURCE_ENABLED
                if enabled
                else AuditAction.SOURCE_DISABLED,
                operator=operator,
                at=now,
                request_id=request_id,
                metadata={},
            ),
        )
    return updated


async def load_source(
    runtime: DatabaseRuntime, *, source_id: SourceId
) -> SourceRecord | None:
    """One Source, or None if no Source has this ID."""
    async with runtime.transaction() as connection:
        return await load_source_record(connection, source_id=source_id)


async def load_sources(
    runtime: DatabaseRuntime,
    *,
    limit: int,
    after: tuple[datetime, SourceId] | None = None,
) -> tuple[SourceRecord, ...]:
    """Up to `limit` Sources, newest first; with `after`, from a `source_position`."""
    if type(limit) is not int or not 1 <= limit <= MAX_SOURCE_LIST:
        raise ValueError(f"limit must be an integer from 1 to {MAX_SOURCE_LIST}")
    async with runtime.transaction() as connection:
        return await load_source_page(connection, limit=limit, after=after)


async def _lock(connection: AsyncConnection, *, source_id: SourceId) -> SourceRecord:
    record = await load_source_record(connection, source_id=source_id, for_update=True)
    if record is None:
        raise SourceNotFound("no source has this ID")
    return record


def _require_utc(now: datetime) -> None:
    if now.utcoffset() != timedelta(0):
        raise ValueError("now must be a UTC-aware timestamp")


def _source_event(
    record: SourceRecord,
    *,
    action: AuditAction,
    operator: UserId,
    at: datetime,
    request_id: UUID | None,
    metadata: Mapping[str, AuditValue],
) -> AuditEvent:
    """Who changed which Source, and which settings; never their values."""
    return AuditEvent(
        event_id=AuditEventId.new(),
        occurred_at=at,
        actor_id=operator,
        action=action,
        target_kind=AuditTargetKind.SOURCE,
        target_id=record.source_id.value,
        decision=AuditDecision.ALLOWED,
        outcome=AuditOutcome.SUCCEEDED,
        request_id=request_id,
        run_id=None,
        metadata=metadata,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class DatabaseSourceAdministration:
    """Source administration stored in PostgreSQL, at the clock's time."""

    runtime: DatabaseRuntime
    clock: Callable[[], datetime] = field(default=_utc_now)

    async def page(
        self, *, limit: int, after: tuple[datetime, SourceId] | None = None
    ) -> tuple[SourceRecord, ...]:
        return await load_sources(self.runtime, limit=limit, after=after)

    async def load(self, *, source_id: SourceId) -> SourceRecord | None:
        return await load_source(self.runtime, source_id=source_id)

    async def register(
        self, *, definition: SourceDefinition, operator: UserId, request_id: UUID
    ) -> SourceRecord:
        return await register_source(
            self.runtime,
            definition=definition,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )

    async def change(
        self,
        *,
        source_id: SourceId,
        changes: Mapping[str, object],
        operator: UserId,
        request_id: UUID,
    ) -> SourceRecord:
        return await change_source(
            self.runtime,
            source_id=source_id,
            changes=changes,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )

    async def set_enabled(
        self, *, source_id: SourceId, enabled: bool, operator: UserId, request_id: UUID
    ) -> SourceRecord:
        return await set_source_enabled(
            self.runtime,
            source_id=source_id,
            enabled=enabled,
            operator=operator,
            now=self.clock(),
            request_id=request_id,
        )
```

Кілька рядків варто розглянути ближче. `SourceDefinition.of` приймає `object` для кожного значення, бо запит може надіслати будь-який тип JSON; `cast` лише називає типи, які потім доводить `__post_init__`. `_HOST` вимагає, щоб остання частина назви хоста починалася з літери, а це відхиляє кожну IP-адресу й голий `localhost`. `change_source` спершу перевіряє збережений Source і кидає `SourceNeedsRepair`, коли навіть він порушує правило, тож помилка ніколи не звинувачує поле клієнта.

Перевірка: `uv run pyright src/knowledge_service/source_administration.py` показує 0 помилок.

## Крок 6 — Обслуговувати шість маршрутів

Крок із кодом.

**Простими словами:** застосунок отримує шість маршрутів Sources. Кожен спершу перевіряє клієнта і його роль, потім ID, сторінку чи тіло, потім звертається до сховища й перетворює кожен вид відмови на свою відповідь: 404 для відсутнього Source, 409 для зайнятої папки чи збереженого Source, що потребує ремонту, 422 для порушеного правила. Тіло маршрут читає сам, після ролі, з тим самим обмеженням розміру, що й для Question.

| Маршрут | Відповідь |
|---|---|
| `GET /v1/sources` | 200 сторінка; 422 поганий `limit`, `cursor` чи невідомий параметр |
| `POST /v1/sources` | 201 з `Location`; 409 `source_exists`; 413; 422 |
| `GET /v1/sources/{source_id}` | 200; 404 `source_not_found`; 422 поганий ID |
| `PATCH /v1/sources/{source_id}` | 200; 404; 409 `source_needs_repair`; 413; 422 |
| `POST /v1/sources/{source_id}/enable` і `/disable` | 200; 404; 422 поганий ID |

У `src/knowledge_service/app.py` змініть ці імпорти:

```python
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from pydantic import BaseModel, ValidationError
```

додайте `SourceChanges`, `SourcePage`, `SourceRegistration` і `SourceView` до імпорту з `knowledge_service.contracts`, а `SourceId` — до імпорту ідентифікаторів:

```python
from knowledge_service.identifiers import ConversationId, JobId, SourceId, UserId
```

і зробіть, щоб імпорти після `knowledge_service.pagination` виглядали так:

```python
from knowledge_service.persistence.sources import SourceRecord
from knowledge_service.roles import Operation, PermissionDenied, require
from knowledge_service.source_administration import (
    InvalidSourceSettings,
    SourceDefinition,
    SourceExists,
    SourceNeedsRepair,
    SourceNotFound,
    source_cursor,
    source_position,
)
```

Додайте протокол сховища й подання відповіді після `JobDecisions`:

```python
class SourceAdministration(Protocol):
    """Sources as an operator registers and changes them.

    `DatabaseSourceAdministration` is the implementation. `register` raises
    `SourceExists` for a kind and location already registered; `change` and
    `set_enabled` raise `SourceNotFound` for a missing Source, and `change`
    raises `InvalidSourceSettings` for a result that breaks a rule.
    """

    async def page(
        self, *, limit: int, after: tuple[datetime, SourceId] | None = None
    ) -> tuple[SourceRecord, ...]: ...

    async def load(self, *, source_id: SourceId) -> SourceRecord | None: ...

    async def register(
        self, *, definition: SourceDefinition, operator: UserId, request_id: UUID
    ) -> SourceRecord: ...

    async def change(
        self,
        *,
        source_id: SourceId,
        changes: Mapping[str, object],
        operator: UserId,
        request_id: UUID,
    ) -> SourceRecord: ...

    async def set_enabled(
        self, *, source_id: SourceId, enabled: bool, operator: UserId, request_id: UUID
    ) -> SourceRecord: ...


def source_view(record: SourceRecord) -> SourceView:
    """The API view of a stored Source."""
    return SourceView(
        source_id=record.source_id.value,
        kind=record.kind,
        location=record.location,
        display_name=record.display_name,
        credentials_reference=record.credentials_reference,
        enabled=record.enabled,
        default_public=record.default_public,
        default_principals=list(record.default_principals),
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
```

Дайте `create_app` сховище, після `job_decisions`:

```python
    sources: SourceAdministration | None = None,
```

У маршруті Question замініть написаний вручну `openapi_extra` на допоміжну функцію, яку додає цей крок:

```python
        openapi_extra=body_schema(Question),
```

Додайте шість маршрутів у кінець `create_app`, просто перед `return app`:

```python
    @app.get(
        "/v1/sources",
        response_model=SourcePage,
        responses=error_responses(),
        openapi_extra={"parameters": PAGE_PARAMETERS},
    )
    async def list_sources(request: Request) -> SourcePage | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        request_id = request_id_from(request)
        try:
            limit, cursor = page_request(request)
            after = None if cursor is None else source_position(cursor)
        except InvalidPage:
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            # One more than the page, to learn whether another page follows.
            records = await sources.page(limit=limit + 1, after=after)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("source list failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        page = records[:limit]
        return SourcePage(
            items=[source_view(record) for record in page],
            next_cursor=source_cursor(page[-1]) if len(records) > limit else None,
        )

    @app.post(
        "/v1/sources",
        status_code=201,
        response_model=SourceView,
        responses=error_responses(409, 413),
        openapi_extra=body_schema(SourceRegistration),
    )
    async def register_source(request: Request) -> JSONResponse:
        # Who, then whether they may, then what they send.
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        registration = cast(
            SourceRegistration, await read_body(request, SourceRegistration)
        )
        request_id = request_id_from(request)
        try:
            definition = SourceDefinition.of(**registration.model_dump())
        except InvalidSourceSettings as invalid:
            return _invalid_source(invalid, request_id)
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            record = await sources.register(
                definition=definition,
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except SourceExists:
            return _conflict(
                "source_exists",
                "A Source of this kind and location is registered",
                request_id,
            )
        except Exception as error:
            logger.error("source registration failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        return JSONResponse(
            status_code=201,
            content=source_view(record).model_dump(mode="json"),
            headers={"Location": f"/v1/sources/{record.source_id.value}"},
        )

    @app.get(
        "/v1/sources/{source_id}",
        response_model=SourceView,
        responses=error_responses(404),
    )
    async def read_source(
        request: Request, source_id: Identifier
    ) -> SourceView | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            record = await sources.load(source_id=SourceId.parse(source_id))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("source read failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        if record is None:
            return _source_not_found(request_id)
        return source_view(record)

    @app.patch(
        "/v1/sources/{source_id}",
        response_model=SourceView,
        responses=error_responses(404, 409, 413),
        openapi_extra=body_schema(SourceChanges),
    )
    async def change_source(
        request: Request, source_id: Identifier
    ) -> SourceView | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        requested = cast(SourceChanges, await read_body(request, SourceChanges))
        # Only the fields the body names: one left out keeps its value.
        changes = {
            name: getattr(requested, name) for name in requested.model_fields_set
        }
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            record = await sources.change(
                source_id=SourceId.parse(source_id),
                changes=changes,
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except SourceNotFound:
            return _source_not_found(request_id)
        except SourceNeedsRepair:
            return _conflict(
                "source_needs_repair",
                "The stored Source breaks a rule; register it again",
                request_id,
            )
        except InvalidSourceSettings as invalid:
            return _invalid_source(invalid, request_id)
        except Exception as error:
            logger.error("source change failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        return source_view(record)

    @app.post(
        "/v1/sources/{source_id}/enable",
        response_model=SourceView,
        responses=error_responses(404),
    )
    async def enable_source(
        request: Request, source_id: Identifier
    ) -> SourceView | JSONResponse:
        return await switch(request, source_id, enabled=True)

    @app.post(
        "/v1/sources/{source_id}/disable",
        response_model=SourceView,
        responses=error_responses(404),
    )
    async def disable_source(
        request: Request, source_id: Identifier
    ) -> SourceView | JSONResponse:
        return await switch(request, source_id, enabled=False)

    async def switch(
        request: Request, source_id: str, *, enabled: bool
    ) -> SourceView | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.CONFIGURE_SOURCES)
        request_id = request_id_from(request)
        if not CANONICAL_ID.fullmatch(source_id):
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if sources is None:
            return _sources_unavailable(request_id)
        try:
            record = await sources.set_enabled(
                source_id=SourceId.parse(source_id),
                enabled=enabled,
                operator=caller.reader.user_id,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            raise
        except SourceNotFound:
            return _source_not_found(request_id)
        except Exception as error:
            logger.error("source switch failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        return source_view(record)
```

Додайте три допоміжні функції відповідей перед `_conflict`:

```python
def _sources_unavailable(request_id: UUID) -> JSONResponse:
    return error_response(
        status_code=503,
        code="sources_unavailable",
        message="Sources are unavailable",
        request_id=request_id,
    )


def _source_not_found(request_id: UUID) -> JSONResponse:
    return error_response(
        status_code=404,
        code="source_not_found",
        message="No such Source",
        request_id=request_id,
    )


def _invalid_source(invalid: InvalidSourceSettings, request_id: UUID) -> JSONResponse:
    # The message names a rule and never a value, so it is safe to send.
    return error_response(
        status_code=422,
        code="invalid_source_settings",
        message=str(invalid),
        request_id=request_id,
    )
```

Нарешті, замініть `read_question` на загальне читання тіла й допоміжну функцію OpenAPI:

```python
def body_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The OpenAPI request body of a route that reads its body itself."""
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema()}},
        }
    }


async def read_question(request: Request) -> Question:
    """The body as a Question, read only up to `MAX_BODY_BYTES`."""
    return cast(Question, await read_body(request, Question))


async def read_body(request: Request, model: type[BaseModel]) -> BaseModel:
    """The body as a `model`, read only up to `MAX_BODY_BYTES`."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _BodyTooLarge
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY_BYTES:
            raise _BodyTooLarge
    try:
        return model.model_validate_json(bytes(body))
    except ValidationError as invalid:
        raise RequestValidationError(invalid.errors()) from None
```

`read_body` — це `read_question` з моделлю як параметром, тож маршрут Question поводиться точно як раніше, а обидва тіла Sources безкоштовно отримують обмеження 64 KiB і відповідь 413. Функція повертає простий `BaseModel`, тож кожен виклик називає модель, яку просив, через `cast`; значення справді є цією моделлю, бо його побудував `model_validate_json`.

Перевірка: `uv run pyright src/knowledge_service/app.py` показує 0 помилок.

## Крок 7 — Позначити маршрути обслуговуваними й оновити наявні тести

Крок із кодом.

**Простими словами:** таблиця маршрутів з M09-T01 досі вважає шість маршрутів Sources запланованими, і її тести тепер падають, бо застосунок їх обслуговує. Цей крок позначає їх обслуговуваними, каже тесту OpenAPI, які додаткові помилки описує кожен, і вчить тест ролей викликати їх із тілом, яке вони приймають, щоб довести, що кожен пускає рівно роль content-manager.

У `src/knowledge_service/api_routes.py` змініть `_PLANNED` на `_SERVED` у шести рядках із завданням `"M09-T02"`. Перший із них стає таким:

```python
    Route(
        _GET,
        "/v1/sources",
        Operation.CONFIGURE_SOURCES,
        _SERVED,
        "M09-T02",
        "List Sources",
        paginated=True,
    ),
```

У `tests/test_api_routes.py` додайте шість маршрутів до `ROUTE_ERRORS`:

```python
    ("GET", "/v1/sources"): set[int](),
    ("POST", "/v1/sources"): {409, 413},
    ("GET", "/v1/sources/{source_id}"): {404},
    ("PATCH", "/v1/sources/{source_id}"): {404, 409, 413},
    ("POST", "/v1/sources/{source_id}/enable"): {404},
    ("POST", "/v1/sources/{source_id}/disable"): {404},
```

У `tests/test_app_authentication.py` — п'ять змін.

Додайте ці два імпорти поруч з іншими імпортами `knowledge_service`:

```python
from knowledge_service.persistence.sources import SourceRecord
from knowledge_service.source_administration import SourceExists, SourceNotFound
```

Дайте `raw_scope` приймати метод, бо тіло PATCH теж має відхилятися до прочитання:

```python
def raw_scope(
    headers: list[tuple[bytes, bytes]],
    path: str = "/v1/questions",
    method: str = "POST",
) -> dict[str, Any]:
    """An ASGI request scope, for what an HTTP client would never send."""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
```

Замініть `WITH_BODY` і додайте тіла, які приймають маршрути Sources:

```python
# The routes that may read a body, to check none is read before the role.
WITH_BODY = [route for route, _ in PROTECTED if route[0] in ("POST", "PATCH")]
# A body each route accepts, so a permitted caller reaches the store.
BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/v1/sources"): {
        "kind": "local_directory",
        "location": "/srv/handbook",
        "display_name": "Handbook",
    },
    ("PATCH", "/v1/sources/{source_id}"): {"display_name": "Handbook"},
}
```

Додайте фейкове сховище Sources і підключіть його до `full_app`:

```python
class Configuring:
    """A Source store that records each request, and finds no Source."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def page(self, **request: Any) -> tuple[SourceRecord, ...]:
        self.calls.append(request)
        return ()

    async def load(self, **request: Any) -> None:
        self.calls.append(request)

    async def register(self, **request: Any) -> SourceRecord:
        self.calls.append(request)
        raise SourceExists("a source of this kind and location is registered")

    async def change(self, **request: Any) -> SourceRecord:
        self.calls.append(request)
        raise SourceNotFound("no source has this ID")

    async def set_enabled(self, **request: Any) -> SourceRecord:
        self.calls.append(request)
        raise SourceNotFound("no source has this ID")


def full_app(
    source: Source,
    tokens: dict[str, Any] | None = None,
    conversations: Deleting | None = None,
    jobs: Reading | None = None,
    decisions: Deciding | None = None,
    sources: Configuring | None = None,
) -> Any:
    """The app with every endpoint wired, so every route is real."""
    return create_app(
        streamer=streamer(Model(), source=source),
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
        conversations=conversations or Deleting(),
        job_reports=jobs or Reading(),
        job_decisions=decisions or Deciding(),
        sources=sources or Configuring(),
    )
```

У `test_each_protected_route_admits_exactly_the_roles_that_permit_it` замініть усе від `source, conversations = ...` до кінця:

```python
    source, conversations = Source(), Deleting()
    jobs, decisions, sources = Reading(), Deciding(), Configuring()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(source, {"token": token}, conversations, jobs, decisions, sources)
    method, path = route
    for name in ("{conversation_id}", "{job_id}", "{source_id}"):
        path = path.replace(name, str(uuid4()))

    async with client(app) as http:
        response = await http.request(
            method,
            path,
            json=BODIES.get(route, {"text": QUESTION.text}),
            headers={"Authorization": "Bearer token"},
        )

    allowed = bool(roles & MATRIX[operation])
    assert (response.status_code == 403) is not allowed
    reached = (
        source.readers
        or conversations.calls
        or jobs.calls
        or decisions.calls
        or sources.calls
    )
    assert bool(reached) is allowed
```

і дайте тесту тіла теж приймати метод:

```python
@pytest.mark.parametrize("route", WITH_BODY, ids=" ".join)
async def test_the_role_is_checked_before_the_body_is_read(
    route: tuple[str, str],
) -> None:
    read: list[int] = []
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        read.append(1)
        return {"type": "http.request", "body": b" " * 65_536, "more_body": True}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    headers = [(b"authorization", b"Bearer roleless-token")]
    method, path = route
    await full_app(Source())(raw_scope(headers, path, method), receive, send)
```

Перевірка:

```bash
uv run pytest tests/test_api_routes.py tests/test_app_authentication.py -q
```

Очікувано: усе проходить. Тест ролей тепер запускає кожен маршрут Sources для всіх шістнадцяти наборів ролей.

## Крок 8 — Написати нові тести

Крок із кодом.

**Простими словами:** два нові файли тестів доводять правила, маршрути й поведінку бази. Перший працює без сервісів: він пробує кожне правило з добрими й поганими значеннями, «фазить» правила, щоб показати, що відмова ніколи не повторює отримане, і викликає маршрути через фейкове сховище. Другий працює на справжньому PostgreSQL, де справді відбуваються унікальність, блокування, транзакції й сторінки.

| Файл | Що доводить |
|---|---|
| `tests/test_source_administration.py` | кожне правило; відмови ніколи не цитують значення; межі й поля тіла збігаються з правилами; 201 з `Location`; суворі тіла; 409; секрети ніколи не повертаються й не потрапляють у лог; PATCH передає лише названі поля; 404; написання ID; сторінки, повна остання сторінка, погані запити сторінок |
| `tests/test_source_administration_integration.py` | реєстрація збережена й записана в аудит; один Source на тип і розташування, також для чотирьох одночасних реєстрацій; зміна записана в аудит лише назвами налаштувань; зміна без змін нічого не записує; зміна, що порушує правило, нічого не зберігає; вимкнений Source ніколи не на черзі; сторінки не повторюють і не пропускають; 409 для збереженого Source, що потребує ремонту, також для принципала NULL; PATCH через HTTP, що порушує правило, називає правило й нічого не видає; увімкнення й вимкнення через HTTP; відкат відмовляється видаляти записані події |

Створіть `tests/test_source_administration.py`:

```python
"""Source administration: each setting's rule, and the routes over a fake store.

A Source's settings come from an operator's request, so every one is
checked, and a refusal names the rule it broke, never the value. No secret
passes through: `credentials_reference` only names one.
"""

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from identity_fakes import READER
from knowledge_service.app import SourceAdministration, create_app
from knowledge_service.contracts import SourceChanges, SourceRegistration
from knowledge_service.identifiers import SourceId
from knowledge_service.identity.context import Caller
from knowledge_service.ingestion.sources import SourceKind
from knowledge_service.pagination import encode_cursor
from knowledge_service.persistence.sources import SourceRecord
from knowledge_service.roles import ApplicationRole
from knowledge_service.source_administration import (
    CHANGEABLE,
    MAX_CREDENTIALS_REFERENCE,
    MAX_DISPLAY_NAME,
    MAX_LOCATION,
    InvalidSourceSettings,
    SourceDefinition,
    SourceExists,
    SourceNotFound,
    source_cursor,
    source_position,
)

MARKER = "SECRET-MARKER-7f3a"
CREATED_AT = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
FOLDER = {
    "kind": "local_directory",
    "location": "/srv/handbook",
    "display_name": "Handbook",
}
SITE = {
    "kind": "confluence",
    "location": "https://acme.atlassian.net/wiki",
    "display_name": "Acme wiki",
    "credentials_reference": "confluence/acme-prod",
}
INVISIBLE = chr(0x202E)  # right-to-left override: shown text differs from stored


def definition(**settings: Any) -> SourceDefinition:
    return SourceDefinition.of(**{**FOLDER, **settings})


# Each rule, one setting at a time.


@pytest.mark.parametrize(
    "settings",
    [
        FOLDER,
        SITE,
        {**FOLDER, "location": "/srv/caf" + chr(0xE9)},
        {**SITE, "location": "https://wiki.acme.example:8443/confluence"},
        {**SITE, "location": "https://wiki.acme.example"},
    ],
)
def test_a_valid_source_is_accepted(settings: dict[str, Any]) -> None:
    assert SourceDefinition.of(**settings).location == settings["location"]


@pytest.mark.parametrize(
    "location",
    [
        "",
        "srv/handbook",
        "/",
        "//srv/handbook",
        "/srv/handbook/",
        "/srv/./handbook",
        "/srv/../handbook",
        "/srv//handbook",
        "/srv/handbook\n",
        "/srv/hand\x00book",
        "/srv/" + INVISIBLE + "handbook",
        "/srv/cafe" + chr(0x301),
        "/" + "a" * 2048,
    ],
)
def test_a_folder_is_an_absolute_path_in_its_one_spelling(location: str) -> None:
    with pytest.raises(InvalidSourceSettings):
        definition(location=location)


@pytest.mark.parametrize(
    "location",
    [
        "http://acme.atlassian.net/wiki",
        "https://user:pass@acme.atlassian.net/wiki",
        "https://acme.atlassian.net@evil.example/wiki",
        "https://Acme.atlassian.net/wiki",
        "https://acme.atlassian.net:443/wiki",
        "https://acme.atlassian.net:99999/wiki",
        "https://acme.atlassian.net/wiki/",
        "https://acme.atlassian.net/",
        "https://acme.atlassian.net/wiki?token=x",
        "https://acme.atlassian.net/wiki#top",
        "https://acme.atlassian.net/wik%69",
        "https://acme.atlassian.net/a/../wiki",
        "https:///wiki",
        "https://acme.atlassian.net/wiki ",
        "https://acmé.atlassian.net/wiki",
        "https://acme.atlassian.net\\wiki",
        "acme.atlassian.net/wiki",
        "https://acme.atlassian.net:/wiki",
        "https://acme.atlassian.net./wiki",
        "https://acme.atlassian.net:08443/wiki",
        "https://[::1]/wiki",
        "https://127.0.0.1/wiki",
        "https://0177.0.0.1/wiki",
        "https://localhost/wiki",
    ],
)
def test_a_site_is_an_https_address_in_its_one_spelling(location: str) -> None:
    with pytest.raises(InvalidSourceSettings):
        SourceDefinition.of(**{**SITE, "location": location})


def test_a_folder_needs_no_credentials() -> None:
    with pytest.raises(InvalidSourceSettings):
        definition(credentials_reference="confluence/acme-prod")


@pytest.mark.parametrize(
    "reference",
    [
        None,
        "",
        "ATATT3xFfGF0T4kq2",
        # Built in parts, so no secret scanner reads it as a real token.
        "ghp_" + "16C7e42F" * 4 + "AbCd",
        "confluence/acme prod",
        "confluence/",
        "/confluence",
        "a/b/c/d/e",
        "confluence/" + "a" * 120,
        "Confluence/acme",
    ],
)
def test_a_site_names_its_secret_and_never_holds_it(reference: str | None) -> None:
    with pytest.raises(InvalidSourceSettings):
        SourceDefinition.of(**{**SITE, "credentials_reference": reference})


@pytest.mark.parametrize(
    "display_name", ["", " Handbook", "Handbook ", "Hand\nbook", "Hand" + INVISIBLE]
)
def test_a_display_name_is_visible_text(display_name: str) -> None:
    with pytest.raises(InvalidSourceSettings):
        definition(display_name=display_name)


@pytest.mark.parametrize(
    ("public", "principals"),
    [
        (False, ["alice"]),
        (False, ["role:admin"]),
        (False, ["user:"]),
        (False, ["user: alice"]),
        (False, ["group:/eng\n"]),
        (False, ["user:alice", "user:alice"]),
        (False, [f"user:u{n}" for n in range(101)]),
        (True, ["group:/eng"]),
        ("yes", []),
        (False, "user:alice"),
    ],
)
def test_default_access_names_users_and_groups_once(
    public: object, principals: object
) -> None:
    with pytest.raises(InvalidSourceSettings):
        definition(default_public=public, default_principals=principals)


def test_default_principals_are_kept_in_one_order() -> None:
    given_order = definition(default_principals=["user:bob", "group:/eng", "user:al"])

    assert given_order.default_principals == ("group:/eng", "user:al", "user:bob")


@pytest.mark.parametrize("kind", ["", "folder", "LOCAL_DIRECTORY", 1, None])
def test_an_unknown_kind_is_refused(kind: object) -> None:
    with pytest.raises(InvalidSourceSettings):
        definition(kind=kind)


def test_the_unknown_kind_refusal_names_every_kind() -> None:
    with pytest.raises(InvalidSourceSettings) as refused:
        definition(kind="folder")

    assert all(kind.value in str(refused.value) for kind in SourceKind)


def test_a_change_may_set_exactly_the_fields_its_body_allows() -> None:
    assert set(SourceChanges.model_fields) == CHANGEABLE


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("display_name", MAX_DISPLAY_NAME),
        ("location", MAX_LOCATION),
        ("credentials_reference", MAX_CREDENTIALS_REFERENCE),
    ],
)
def test_the_body_allows_the_lengths_the_rules_allow(field: str, limit: int) -> None:
    constraints = SourceRegistration.model_fields[field].metadata

    assert any(getattr(c, "max_length", None) == limit for c in constraints)


@given(
    field=st.sampled_from(
        ["location", "display_name", "credentials_reference", "default_principals"]
    ),
    kind=st.sampled_from([FOLDER, SITE]),
    before=st.text(max_size=20),
    after=st.text(max_size=20),
)
def test_a_refusal_never_quotes_the_value(
    field: str, kind: dict[str, Any], before: str, after: str
) -> None:
    # A line break breaks every rule here, so each example is a real refusal.
    value = before + MARKER + "\n" + after
    settings: dict[str, Any] = {
        **kind,
        field: [value] if field == "default_principals" else value,
    }

    with pytest.raises(InvalidSourceSettings) as refused:
        SourceDefinition.of(**settings)

    assert MARKER not in str(refused.value)


# Cursors of the Source list.


def record(**changes: Any) -> SourceRecord:
    base = SourceRecord(
        source_id=SourceId.new(),
        kind="local_directory",
        location="/srv/handbook",
        display_name="Handbook",
        credentials_reference=None,
        enabled=True,
        default_public=False,
        default_principals=(),
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
    )
    return replace(base, **changes)


def test_a_source_cursor_starts_after_its_source() -> None:
    stored = record()

    assert source_position(source_cursor(stored)) == (CREATED_AT, stored.source_id)


# The routes, over a fake store.


class Sources:
    """Holds Sources newest first, and records what each request asked for."""

    def __init__(self, records: list[SourceRecord] | None = None) -> None:
        self.records = records or []
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def page(
        self, *, limit: int, after: tuple[datetime, SourceId] | None = None
    ) -> tuple[SourceRecord, ...]:
        self.calls.append({"limit": limit, "after": after})
        keys = [(r.created_at, r.source_id.value) for r in self.records]
        start = 0
        if after is not None:
            start = next(
                (i for i, key in enumerate(keys) if key < (after[0], after[1].value)),
                len(keys),
            )
        return tuple(self.records[start : start + limit])

    async def load(self, *, source_id: SourceId) -> SourceRecord | None:
        self.calls.append({"source_id": source_id})
        return next((r for r in self.records if r.source_id == source_id), None)

    async def register(self, **request: Any) -> SourceRecord:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        found: SourceDefinition = request["definition"]
        return record(
            kind=found.kind.value,
            location=found.location,
            display_name=found.display_name,
            credentials_reference=found.credentials_reference,
            default_public=found.default_public,
            default_principals=found.default_principals,
        )

    async def change(self, **request: Any) -> SourceRecord:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return record(display_name="Changed")

    async def set_enabled(self, **request: Any) -> SourceRecord:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return record(enabled=request["enabled"])


class ContentManager:
    """Gives every request a content manager's caller."""

    async def authenticate(self, headers: list[str]) -> Caller:
        return Caller(reader=READER, roles=frozenset({ApplicationRole.CONTENT_MANAGER}))


async def call(
    sources: Sources, method: str, path: str, body: Any = None
) -> httpx.Response:
    app = create_app(
        authenticator=ContentManager(), sources=cast(SourceAdministration, sources)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.request(
            method, path, json=body, headers={"Authorization": "Bearer t"}
        )


async def test_registering_answers_201_with_the_source_and_where_it_lives() -> None:
    sources = Sources()

    response = await call(
        sources,
        "POST",
        "/v1/sources",
        {**FOLDER, "default_principals": ["user:bob", "group:/eng"]},
    )

    body = response.json()
    assert response.status_code == 201
    assert response.headers["location"] == f"/v1/sources/{body['source_id']}"
    assert body["default_principals"] == ["group:/eng", "user:bob"]
    assert (body["enabled"], body["credentials_reference"]) == (True, None)
    assert sources.calls[0]["operator"] == READER.user_id


@pytest.mark.parametrize(
    "body",
    [
        {**FOLDER, "password": "hunter2"},
        {**FOLDER, "enabled": False},
        {**FOLDER, "default_public": "yes"},
        {**FOLDER, "default_principals": "user:bob"},
        {"kind": "local_directory", "location": "/srv/handbook"},
        ["not", "an", "object"],
    ],
)
async def test_a_body_of_another_shape_is_refused_before_the_store(
    body: Any,
) -> None:
    sources = Sources()

    response = await call(sources, "POST", "/v1/sources", body)

    assert (response.status_code, response.json()["code"]) == (422, "validation_error")
    assert sources.calls == []


async def test_a_broken_rule_is_named_and_nothing_is_stored() -> None:
    sources = Sources()

    response = await call(
        sources, "POST", "/v1/sources", {**FOLDER, "location": "srv/handbook"}
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_source_settings"
    assert "absolute folder path" in response.json()["message"]
    assert sources.calls == []


async def test_a_second_source_of_the_same_kind_and_location_is_a_409() -> None:
    sources = Sources()
    sources.error = SourceExists("a source of this kind and location is registered")

    response = await call(sources, "POST", "/v1/sources", FOLDER)

    assert (response.status_code, response.json()["code"]) == (409, "source_exists")


@pytest.mark.parametrize(
    "body",
    [
        {**SITE, "credentials_reference": MARKER},
        {**SITE, "location": f"https://bot:{MARKER}@acme.atlassian.net/wiki"},
        {**SITE, "location": f"https://acme.atlassian.net/wiki?token={MARKER}"},
        {**FOLDER, "credentials_reference": MARKER.lower()},
        {**FOLDER, "token": MARKER},
        {**FOLDER, "display_name": MARKER + "\n"},
    ],
)
async def test_a_refused_secret_is_never_sent_back_or_logged(
    body: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    sources = Sources()

    response = await call(sources, "POST", "/v1/sources", body)

    assert response.status_code == 422
    assert MARKER.lower() not in (response.text + caplog.text).lower()
    assert sources.calls == []


async def test_a_store_failure_names_no_value_to_the_client_or_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    sources = Sources()
    sources.error = RuntimeError(f"insert failed for {MARKER}")

    response = await call(sources, "POST", "/v1/sources", FOLDER)

    assert response.status_code == 500
    assert MARKER not in response.text + caplog.text


async def test_a_change_passes_only_the_fields_the_body_names() -> None:
    sources = Sources()
    path = f"/v1/sources/{SourceId.new().value}"

    response = await call(sources, "PATCH", path, {"credentials_reference": None})

    assert response.status_code == 200
    assert sources.calls[0]["changes"] == {"credentials_reference": None}


@pytest.mark.parametrize(
    "body",
    [
        {"location": "/srv/other"},
        {"kind": "confluence"},
        {"enabled": False},
        {"display_name": 5},
    ],
)
async def test_a_change_to_what_a_source_is_is_refused_before_the_store(
    body: dict[str, Any],
) -> None:
    sources = Sources()
    path = f"/v1/sources/{SourceId.new().value}"

    response = await call(sources, "PATCH", path, body)

    assert (response.status_code, response.json()["code"]) == (422, "validation_error")
    assert sources.calls == []


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("GET", "", None),
        ("PATCH", "", {"display_name": "X"}),
        ("POST", "/enable", None),
        ("POST", "/disable", None),
    ],
)
async def test_a_missing_source_is_a_404(
    method: str, suffix: str, body: dict[str, Any] | None
) -> None:
    sources = Sources()
    sources.error = SourceNotFound("no source has this ID")

    response = await call(
        sources, method, f"/v1/sources/{SourceId.new().value}{suffix}", body
    )

    assert (response.status_code, response.json()["code"]) == (404, "source_not_found")


@pytest.mark.parametrize(
    "source_id",
    ["not-an-id", "{00000000-0000-0000-0000-000000000001}", "0" * 32],
)
async def test_an_id_in_another_spelling_is_refused_before_the_store(
    source_id: str,
) -> None:
    sources = Sources()

    response = await call(sources, "GET", f"/v1/sources/{source_id}")

    assert response.status_code == 422
    assert sources.calls == []


async def test_pages_of_sources_follow_each_other_to_the_end() -> None:
    records = sorted(
        (record(created_at=CREATED_AT - timedelta(minutes=n // 2)) for n in range(5)),
        key=lambda r: (r.created_at, r.source_id.value),
        reverse=True,
    )
    sources = Sources(records)
    seen: list[str] = []
    query = "?limit=2"

    for _ in range(10):
        page = (await call(sources, "GET", f"/v1/sources{query}")).json()
        seen += [item["source_id"] for item in page["items"]]
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    assert seen == [str(r.source_id.value) for r in records]


async def test_a_full_last_page_of_sources_says_no_page_follows() -> None:
    records = sorted(
        (record(created_at=CREATED_AT - timedelta(minutes=n)) for n in range(4)),
        key=lambda r: (r.created_at, r.source_id.value),
        reverse=True,
    )
    sources = Sources(records)

    first = (await call(sources, "GET", "/v1/sources?limit=2")).json()
    cursor = first["next_cursor"]
    second = (await call(sources, "GET", f"/v1/sources?limit=2&cursor={cursor}")).json()

    assert len(second["items"]) == 2
    assert second["next_cursor"] is None


@pytest.mark.parametrize(
    "query",
    [
        "?limit=0",
        "?offset=2",
        "?cursor="
        + encode_cursor("job-decisions", {"finished_at": "x", "job_id": "y"}),
    ],
)
async def test_a_bad_page_request_is_refused_before_the_store(query: str) -> None:
    sources = Sources()

    response = await call(sources, "GET", f"/v1/sources{query}")

    assert response.status_code == 422
    assert sources.calls == []
```

Створіть `tests/test_source_administration_integration.py`:

```python
"""Source administration on real PostgreSQL.

Each change is stored with its audit event in one transaction; a Source's
kind and location are taken once, even by two registrations at the same
moment; pages of Sources neither repeat nor skip one; and no audit event
holds a setting's value.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from alembic import command
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from authorization_scenarios import Person
from database_fixtures import migration_config, migration_environment
from knowledge_service.app import create_app
from knowledge_service.audit import AuditAction
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import SourceId
from knowledge_service.identity.context import (
    AuthenticationError,
    Caller,
    Unauthenticated,
)
from knowledge_service.persistence.audit import load_audit_events
from knowledge_service.persistence.schema import audit_events, sources
from knowledge_service.persistence.sources import load_due_source_ids
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings
from knowledge_service.source_administration import (
    DatabaseSourceAdministration,
    InvalidSourceSettings,
    SourceDefinition,
    SourceExists,
    SourceNotFound,
    change_source,
    register_source,
    set_source_enabled,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 26, 9, 0, tzinfo=UTC)
MARKER = "SECRET-MARKER-7f3a"
CASEY = Person("casey", ())
OPERATOR = CASEY.context().user_id
CALLERS = {
    "Bearer casey": Caller(
        reader=CASEY.context(), roles=frozenset({ApplicationRole.CONTENT_MANAGER})
    ),
}
FOLDER = SourceDefinition.of(
    kind="local_directory", location="/srv/handbook", display_name="Handbook"
)


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
    try:
        yield runtime
    finally:
        await runtime.dispose()


async def register(
    runtime: DatabaseRuntime, definition: SourceDefinition = FOLDER, **at: Any
) -> SourceId:
    record = await register_source(
        runtime, definition=definition, operator=OPERATOR, now=at.get("now", NOW)
    )
    return record.source_id


async def trail(runtime: DatabaseRuntime, source_id: SourceId) -> list[Any]:
    async with runtime.transaction() as connection:
        events = await load_audit_events(
            connection, target_id=source_id.value, limit=50
        )
    return [(e.action, e.actor_id, dict(e.metadata)) for e in events]


async def count_sources(runtime: DatabaseRuntime) -> int:
    async with runtime.transaction() as connection:
        return int(
            await connection.scalar(sa.select(sa.func.count()).select_from(sources))
            or 0
        )


async def over_http(
    runtime: DatabaseRuntime, method: str, path: str, body: Any = None
) -> httpx.Response:
    app = create_app(
        authenticator=Tokens(),
        sources=DatabaseSourceAdministration(runtime, clock=lambda: NOW),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.request(
            method, path, json=body, headers={"Authorization": "Bearer casey"}
        )


async def test_a_registered_source_is_stored_enabled_and_recorded(
    runtime: DatabaseRuntime,
) -> None:
    response = await over_http(
        runtime,
        "POST",
        "/v1/sources",
        {
            "kind": "local_directory",
            "location": "/srv/handbook",
            "display_name": "Handbook",
            "default_principals": ["group:/engineering"],
        },
    )
    source_id = SourceId.parse(response.json()["source_id"])

    read = await over_http(runtime, "GET", f"/v1/sources/{source_id.value}")

    assert response.status_code == 201
    assert read.json() == response.json()
    assert read.json()["enabled"] is True
    assert await trail(runtime, source_id) == [
        (AuditAction.SOURCE_REGISTERED, OPERATOR, {"kind": "local_directory"})
    ]


async def test_the_same_kind_and_location_is_registered_once(
    runtime: DatabaseRuntime,
) -> None:
    first = await register(runtime)

    with pytest.raises(SourceExists):
        await register(
            runtime,
            SourceDefinition.of(
                kind="local_directory", location="/srv/handbook", display_name="Again"
            ),
        )

    assert await count_sources(runtime) == 1
    assert len(await trail(runtime, first)) == 1


async def test_two_registrations_at_once_store_one_source(
    runtime: DatabaseRuntime,
) -> None:
    results = await asyncio.gather(
        *(register(runtime) for _ in range(4)), return_exceptions=True
    )

    stored = [r for r in results if isinstance(r, SourceId)]
    assert len(stored) == 1
    assert all(isinstance(r, SourceExists) for r in results if r not in stored)
    assert await count_sources(runtime) == 1


async def test_a_duplicate_over_http_is_a_409_and_stores_nothing(
    runtime: DatabaseRuntime,
) -> None:
    body = {"kind": "local_directory", "location": "/srv/handbook", "display_name": "A"}
    await over_http(runtime, "POST", "/v1/sources", body)

    again = await over_http(
        runtime, "POST", "/v1/sources", {**body, "display_name": "B"}
    )

    assert (again.status_code, again.json()["code"]) == (409, "source_exists")
    assert await count_sources(runtime) == 1


async def test_a_change_records_which_settings_changed_never_their_values(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await register(runtime)

    changed = await change_source(
        runtime,
        source_id=source_id,
        changes={"display_name": f"Handbook {MARKER}", "default_public": True},
        operator=OPERATOR,
        now=NOW + timedelta(minutes=1),
    )

    assert (changed.display_name, changed.default_public) == (
        f"Handbook {MARKER}",
        True,
    )
    assert changed.updated_at == NOW + timedelta(minutes=1)
    assert (await trail(runtime, source_id))[1] == (
        AuditAction.SOURCE_UPDATED,
        OPERATOR,
        {"default_public": True, "display_name": True},
    )
    async with runtime.transaction() as connection:
        stored = await connection.scalar(
            sa.select(sa.func.string_agg(sa.cast(audit_events.c.metadata, sa.Text), ""))
        )
    assert MARKER not in str(stored)


async def test_a_change_to_the_values_it_has_records_nothing(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await register(runtime)

    unchanged = await change_source(
        runtime,
        source_id=source_id,
        changes={"display_name": "Handbook", "default_principals": []},
        operator=OPERATOR,
        now=NOW + timedelta(minutes=1),
    )

    assert unchanged.updated_at == NOW
    assert len(await trail(runtime, source_id)) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"credentials_reference": "confluence/acme-prod"},
        {"default_public": True, "default_principals": ["user:alice"]},
        {"location": "/srv/other"},
        {},
    ],
)
async def test_a_change_that_breaks_a_rule_stores_nothing(
    runtime: DatabaseRuntime, changes: dict[str, Any]
) -> None:
    source_id = await register(runtime)

    with pytest.raises(InvalidSourceSettings):
        await change_source(
            runtime,
            source_id=source_id,
            changes=changes,
            operator=OPERATOR,
            now=NOW + timedelta(minutes=1),
        )

    assert len(await trail(runtime, source_id)) == 1


async def test_a_change_to_a_missing_source_is_not_found(
    runtime: DatabaseRuntime,
) -> None:
    with pytest.raises(SourceNotFound):
        await change_source(
            runtime,
            source_id=SourceId.new(),
            changes={"display_name": "X"},
            operator=OPERATOR,
            now=NOW,
        )


async def test_a_disabled_source_is_never_due_and_each_switch_is_recorded_once(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await register(runtime)
    later = NOW + timedelta(days=1)

    for _ in range(2):
        await set_source_enabled(
            runtime, source_id=source_id, enabled=False, operator=OPERATOR, now=later
        )
    async with runtime.transaction() as connection:
        due = await load_due_source_ids(
            connection, now=later, interval=timedelta(hours=1), limit=10
        )
    await set_source_enabled(
        runtime, source_id=source_id, enabled=True, operator=OPERATOR, now=later
    )

    assert due == ()
    assert [action for action, _, _ in await trail(runtime, source_id)] == [
        AuditAction.SOURCE_REGISTERED,
        AuditAction.SOURCE_DISABLED,
        AuditAction.SOURCE_ENABLED,
    ]


async def test_pages_of_sources_never_repeat_or_skip_one(
    runtime: DatabaseRuntime,
) -> None:
    # Pairs registered in the same microsecond, so the ID breaks the tie.
    for n in range(6):
        await register(
            runtime,
            SourceDefinition.of(
                kind="local_directory", location=f"/srv/s{n}", display_name=f"S{n}"
            ),
            now=NOW + timedelta(minutes=n // 2),
        )
    seen: list[str] = []
    query = "?limit=2"

    for page_number in range(10):
        page = (await over_http(runtime, "GET", f"/v1/sources{query}")).json()
        seen += [item["source_id"] for item in page["items"]]
        if page_number == 0:
            # A Source registered between pages is newer: it starts no page here.
            await register(
                runtime,
                SourceDefinition.of(
                    kind="local_directory", location="/srv/new", display_name="New"
                ),
                now=NOW + timedelta(hours=1),
            )
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(sources.c.source_id)
            .where(sources.c.location != "/srv/new")
            .order_by(sources.c.created_at.desc(), sources.c.source_id.desc())
        )
        expected = [str(row.source_id) for row in rows]
    assert seen == expected


def test_a_downgrade_stops_rather_than_lose_a_recorded_source_change(
    migrated_database: str,
) -> None:
    # Synchronous: Alembic runs its own event loop.
    url = make_url(migrated_database)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO audit_events (event_id, occurred_at, action,"
                    " target_kind, target_id, metadata, decision, outcome)"
                    " VALUES (gen_random_uuid(), now(), 'source.updated', 'source',"
                    " gen_random_uuid(), '{}'::jsonb, 'allowed', 'succeeded')"
                )
            )
        with migration_environment(url), pytest.raises(Exception, match="recorded"):
            command.downgrade(migration_config(), "f2a3b4c5d6e7")
        with engine.begin() as connection:
            count = connection.execute(
                sa.text("SELECT count(*) FROM audit_events")
            ).scalar()
    finally:
        engine.dispose()

    assert count == 1


async def test_a_stored_source_that_breaks_a_rule_is_named_not_blamed_on_the_body(
    runtime: DatabaseRuntime,
) -> None:
    # Written before the rules, or by hand: a trailing `/` breaks one now.
    source_id = SourceId.new()
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.insert(sources).values(
                source_id=source_id.value,
                kind="local_directory",
                location="/datasets/x/public/",
                display_name="Old",
                enabled=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )

    response = await over_http(
        runtime, "PATCH", f"/v1/sources/{source_id.value}", {"display_name": "New"}
    )

    assert (response.status_code, response.json()["code"]) == (
        409,
        "source_needs_repair",
    )
    assert len(await trail(runtime, source_id)) == 0


async def test_a_change_that_breaks_a_rule_over_http_names_the_rule_only(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    source_id = await register(runtime)

    response = await over_http(
        runtime,
        "PATCH",
        f"/v1/sources/{source_id.value}",
        {"credentials_reference": MARKER.lower()},
    )

    assert (response.status_code, response.json()["code"]) == (
        422,
        "invalid_source_settings",
    )
    assert MARKER.lower() not in (response.text + caplog.text).lower()
    async with runtime.transaction() as connection:
        stored = await connection.scalar(
            sa.select(sources.c.credentials_reference).where(
                sources.c.source_id == source_id.value
            )
        )
    assert stored is None
    assert len(await trail(runtime, source_id)) == 1


async def test_switching_over_http_answers_the_new_state_and_records_each_change(
    runtime: DatabaseRuntime,
) -> None:
    source_id = await register(runtime)
    path = f"/v1/sources/{source_id.value}"

    states = [
        (await over_http(runtime, "POST", f"{path}/{switch}")).json()["enabled"]
        for switch in ("disable", "disable", "enable")
    ]

    assert states == [False, False, True]
    assert [action for action, _, _ in await trail(runtime, source_id)] == [
        AuditAction.SOURCE_REGISTERED,
        AuditAction.SOURCE_DISABLED,
        AuditAction.SOURCE_ENABLED,
    ]


async def test_a_stored_principal_that_names_nobody_needs_repair(
    runtime: DatabaseRuntime,
) -> None:
    # The column allows a NULL element; synchronization refuses such a Source.
    source_id = SourceId.new()
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO sources (source_id, kind, location, display_name,"
                " enabled, created_at, updated_at, default_principals) VALUES"
                " (:id, 'local_directory', '/srv/old', 'Old', true, now(), now(),"
                " ARRAY['user:a', NULL]::text[])"
            ),
            {"id": source_id.value},
        )
    path = f"/v1/sources/{source_id.value}"

    shown = await over_http(runtime, "GET", path)
    changed = await over_http(runtime, "PATCH", path, {"display_name": "New"})

    assert shown.json()["default_principals"] == ["user:a", None]
    assert (changed.status_code, changed.json()["code"]) == (
        409,
        "source_needs_repair",
    )
    assert len(await trail(runtime, source_id)) == 0
```

`MARKER` грає роль секрету: тест кладе його в значення, а потім шукає у відповіді, лозі й рядках аудиту. `INVISIBLE` збудовано через `chr()`, а не набрано, тож файл лишається чистим ASCII, і жоден редактор його не сховає.

Перевірка:

```bash
uv run pytest tests/test_source_administration.py -q
uv run pytest tests/test_source_administration_integration.py -q -m integration
```

Очікувано: `114 passed`, потім `18 passed` (другому потрібна тестова база, як у попередніх уроках).

## Крок 9 — Навмисно зламати унікальність

Без коду в цьому кроці.

Цей крок доводить, що інтеграційні тести охороняють правило «один Source на тип і розташування». У `src/knowledge_service/persistence/sources.py` видаліть з `insert_source_record` цей один рядок:

```python
        .on_conflict_do_nothing(constraint="uq_sources_kind_location")
```

Запустіть інтеграційні тести з вимкненим байткодом, щоб Python не взяв старий скомпільований файл:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider tests/test_source_administration_integration.py -q -m integration
```

Очікувано: `3 failed, 15 passed`. Падають `test_the_same_kind_and_location_is_registered_once`, `test_two_registrations_at_once_store_one_source` і `test_a_duplicate_over_http_is_a_409_and_stores_nothing`. Без `ON CONFLICT` база все одно відхиляє другий рядок, але як `IntegrityError`, якого код не чекає, тож API відповідає 500 замість 409. Поверніть рядок і запустіть знову: `18 passed`.

Що спростувало б дизайн: два рядки в `sources` для однієї папки, 422 чи 404 для клієнта без ролі, секрет чи відхилене значення в будь-якій відповіді, рядку логу чи рядку аудиту, або зміна, збережена без своєї події аудиту.

## Крок 10 — Оновити архітектуру й модель загроз

Без коду в цьому кроці.

У `docs/architecture.md` перед абзацом, що починається з `` `api_routes` is the native API's design ``, додайте:

```markdown
The Source routes, `GET` and `POST /v1/sources`, `GET` and
`PATCH /v1/sources/{source_id}`, and `POST .../enable` and
`POST .../disable`, require `configure_sources` (the content-manager
role) before the ID, page, or body is read. `source_administration`
checks every setting (`SourceDefinition`): a folder is an absolute path
and a Confluence site an https address with a host name, each in its one
spelling, so the kind and location, unique in `sources`, are the
Source's identity and never change. `credentials_reference` names a
secret in the secret store and never holds one; default readers are
`user:<id>` or `group:<id>`. A refusal names the rule, never the value.
Each change locks the Source, stores it, and records `source.registered`,
`source.updated` (which settings changed, never their values),
`source.enabled`, or `source.disabled` in the same transaction; a change
to the values a Source has records nothing. Disabling waits for a
scheduler that is queueing a Job for the Source, but does not cancel a
Job already queued or running. New default readers reach stored
Documents at the Source's next pass.
```

У `docs/threat-model.md`:

- у таблиці точок входу перед рядком `GET /health/live` додайте:

```markdown
| `GET` and `POST /v1/sources`, `GET` and `PATCH /v1/sources/{source_id}`, `POST /v1/sources/{source_id}/enable`, `POST /v1/sources/{source_id}/disable` | Any client that reaches the API | Lists, registers, reads, and changes Sources. Needs a bearer token and the `configure_sources` operation (the `content-manager` role), both checked before the ID, `limit`, `cursor`, or body is read; every setting is checked, and a refusal names the rule, never the value; `credentials_reference` only names a secret; each change is an audit event with the operator as actor, naming which settings changed, never their values |
```

- у таблиці Ingestion перед рядком «A Source reports no readers, and the Document becomes public by mistake» додайте:

```markdown
| An operator's request stores a secret, or gets one shown back | `credentials_reference` must be a secret's name: lowercase words joined by `-`, in up to four `/` parts; a Confluence address with a user, password, query, or fragment is refused; no request value is sent back in an error, logged, or written to an audit event | A secret that happens to fit the name rule, such as one lowercase word, is stored as a name |
| Two Sources read the same folder or site, so its Documents are stored twice | A Source is its kind and location, unique in the database; a location must be in its one spelling (no `.`, `..`, doubled or trailing `/`, letters in one Unicode form, a lowercase host name with no default port), so another spelling is refused, not stored again | A symlink, a case-insensitive file system, or a second address of one site still make two Sources of one place |
```

- в рядку Operations «A role widens what its holder reads» остання клітинка стає «Role checks on the synchronization, evaluation, and policy entry points when they exist»;
- у відомих прогалинах «Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity;» стає «Starting Synchronization Runs and running evaluations are scripts a developer runs with no identity;».

Перевірка: `make docs-check` проходить.

## Крок 11 — Запустити перевірки й написати докази

Без коду в цьому кроці.

Запустіть перевірки одну за одною, на одній тестовій базі:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
make docs-check
make test-unit
make test-integration
git diff --check
uv run pre-commit run --all-files
```

На прототипі вони дали: 367 відформатованих файлів, усі перевірки пройшли, 0 помилок, docs-check з кодом 0, `3606 passed` модульних і `928 passed` інтеграційних (з секретами Keycloak і доступним Redis).

Потім своїми словами напишіть `docs/evidence/M09-T02-source-administration.md`: інваріант і власників, ваші передбачення з кроку 1 і що сталося насправді, очікувану поломку перевірки, шість маршрутів і їхні відповіді, як відхиляється дубль, як секрети лишаються поза системою, події аудиту, навмисну поломку, результати перевірок і що спростувало б дизайн. Код цього уроку не робить зовнішніх і платних викликів. Сервіс не повторює запитів: клієнт, що повторює реєстрацію, отримує 409, а повторена зміна чи перемикання вдруге нічого не записують.

## Підсумковий чекліст

- [ ] `uv run alembic heads` показує `a3b4c5d6e7f8`, а три нові дії аудиту є і в Python, і в перевірці бази.
- [ ] Усі шість маршрутів Sources обслуговуються й позначені обслуговуваними в `ROUTES`; кожна перевірка маршрутів у `tests/test_api_routes.py` проходить.
- [ ] Клієнт без ролі content-manager отримує 403 на кожному маршруті Sources, ще до прочитання будь-якого ID, сторінки чи тіла.
- [ ] Папка чи сайт у другому написанні відхиляється з 422 `invalid_source_settings`, а повідомлення називає правило, ніколи — значення.
- [ ] Другий Source того самого типу й розташування отримує 409 `source_exists`, також коли чотири реєстрації змагаються.
- [ ] `credentials_reference` приймає лише назву секрету, а для папки — жодної; жодне відхилене значення не з'являється у відповіді, лозі чи рядку аудиту.
- [ ] `PATCH` змінює лише названі поля; тип, розташування й увімкненість через нього не змінюються.
- [ ] Кожна справжня зміна пише рівно одну подію аудиту з назвами змінених налаштувань; зміна без змін не пише жодної.
- [ ] `tests/test_source_administration.py` (114) і `tests/test_source_administration_integration.py` (18) проходять.
- [ ] Навмисна поломка дала 3 failed, 15 passed, а відновлений код проходить.
- [ ] `docs/architecture.md` і `docs/threat-model.md` оновлено, і кожна перевірка з кроку 11 проходить.
- [ ] Файл доказів написано своїми словами.

## Запитання для роздумів

1. Двоє контент-менеджерів реєструють ту саму папку одночасно. Пройдіть, що робить SQL кожного запиту і чому в `sources` опиняється рівно один рядок. Що пішло б не так, якби код спершу виконував `SELECT` для перевірки, а потім `INSERT`?
2. Чому API відхиляє `/srv/handbook/`, а не тихо зберігає його як `/srv/handbook`? Подумайте, що побачить оператор після цього, і про значення, яке має кілька можливих «виправлених» форм.
3. Pydantic прочитав би тіло за нас, якби маршрут оголосив його параметром. Що в такому разі дізнався б із відповідей клієнт без ролі content-manager і чому це важливо для адміністративного API?
4. Вставлений токен Atlassian у `credentials_reference` відхиляється. Куди він міг би витекти, якби повідомлення про помилку цитувало значення, і що в цьому уроці не пускає його в кожне з цих місць?
5. Контент-менеджер вимикає Source саме тоді, коли планувальник ставить для нього Job у чергу. Пояснюючи спільне блокування й блокування для оновлення, розкажіть, чому Job стає в чергу або до зміни, або взагалі ні. Що все ж станеться з Job, який став у чергу перед самою зміною?
6. Подія аудиту для зміни каже, які налаштування змінилися, але не їхні нові значення. Що через це втрачає той, хто перевіряє журнал аудиту, і чим ризикував би сервіс, якби зберігав значення?
