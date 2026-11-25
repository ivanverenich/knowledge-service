# M09-T01 — Спроєктувати нативні маршрути ресурсів

Англійська версія: [M09-T01-design-native-resource-routes.md](M09-T01-design-native-resource-routes.md)

Завдання: [M09-T01 — Design native resource routes](../curriculum/milestones/M09-product-interfaces/M09-T01-design-native-resource-routes.md)

## Що ви зробите

Цей урок відкриває етап M09, інтерфейси продукту. Ви проєктуєте власний HTTP API сервісу, **нативний API** (native API). Наступні уроки додадуть до нього адміністрування Sources, адміністрування синхронізації, відгуки та OpenAI-сумісний адаптер для Open WebUI, тож дизайн потрібен заздалегідь. Дизайн стає кодом: одна таблиця всіх маршрутів, правила, яким має відповідати кожен маршрут, і тести, що звіряють із цією таблицею застосунок та його документ OpenAPI.

Конкретний приклад того, що зміниться. Сьогодні адміністратор, який просить список Jobs, що чекають на рішення, отримує до 100 штук одним списком `jobs`, і більше нічого. Після уроку той самий запит виглядає так, як виглядатиме кожен список цього API:

```text
GET /v1/job-decisions?limit=2
-> {"items": [job A, job B], "next_cursor": "WzEsImpvYi1kZWNpc2lvbnMi..."}

GET /v1/job-decisions?limit=2&cursor=WzEsImpvYi1kZWNpc2lvbnMi...
-> {"items": [job C], "next_cursor": null}
```

Цей самий урок прибирає `POST /v1/answer`, ранній маршрут, що надсилає Question просто моделі без жодного пошуку. А справжній маршрут відповідей отримує назву свого ресурсу: `POST /v1/questions`.

| Що вимагає завдання | Де це зроблено |
|---|---|
| Описати версіоновані маршрути для Questions, Conversations, Sources, синхронізації, оцінювання, відгуків і діагностики, з вимогами до ролей | Блок 1; крок 2 |
| OpenAPI показує узгоджені ID | Блоки 1 і 4; кроки 6 і 8 |
| OpenAPI показує узгоджену пагінацію | Блок 3; кроки 3–6 і 8 |
| OpenAPI показує узгоджені помилки | Блок 5; кроки 6 і 8 |
| Жодних доменних моделей у формі протоколу провайдера | Блок 6; крок 8 |
| Випадки успіху, межі, типізованої помилки чи відмови; справжня інфраструктура там, де перевіряється її поведінка | Кроки 8 і 9 (PostgreSQL для порядку сторінок) |
| Повтор і скасування описані явно, зовнішні виклики обмежені | Блок 3; крок 11 |
| Пояснити, що спростувало б дизайн | Крок 9 і запитання для роздумів |
| Оновити лише документацію, якої це стосується | Крок 10 |

Цей урок містить програмування: кроки 2–8 змінюють код і тести. Кроки 1, 9, 10 і 11 коду не потребують, хоча крок 10 редагує два документи, а крок 9 робить тимчасову зміну в один рядок, яку ви потім скасовуєте.

Терміни цього уроку:

- **Маршрут** (route): один HTTP-метод на одному шляху, наприклад `GET /v1/jobs/{job_id}`. Застосунок відповідає на нього однією функцією.
- **Ресурс** (resource): те, що називає шлях, у множині: `sources`, `conversations`, `jobs`. Шлях на кшталт `/v1/sources/{source_id}` називає один елемент цього списку.
- **Версіонований маршрут** (versioned route): шлях, що починається з `/v1`. Майбутній несумісний дизайн житиме під `/v2`, тож клієнти `/v1` і далі працюватимуть.
- **Операція** (Operation): дія, яку може дозволити Application Role, наприклад `configure_sources` (M07-T05). Кожному маршруту `/v1` потрібна рівно одна.
- **Документ OpenAPI** (OpenAPI document): машиночитний опис, який FastAPI будує з маршрутів і віддає за адресою `/openapi.json`. Його читають генератори клієнтів і довідник API (M09-T11).
- **Пагінація** (pagination): видача довгого списку сторінками. `limit` каже, скільки елементів на сторінці; **курсор** (cursor) каже, звідки починається наступна сторінка.
- **Пагінація за ключем** (keyset pagination): наступна сторінка починається після ключа сортування останнього елемента, наприклад «після Job, що впав о 09:02 з ID 01a2...». Протилежність — зсув на кшталт «пропусти перші 100».
- **Модель у формі провайдера** (provider-shaped model): запит чи відповідь у формі протоколу провайдера моделей, наприклад OpenAI-вські `choices`, `messages`, `role`. Нативний API тримається власних слів: Question, Answer, Citation.

## Перш ніж почати

Цей розділ пояснює кожне рішення, на яке спирається код уроку. Кожен блок показує реальну ситуацію в цьому проєкті, що відбувається сьогодні, факт, який лежить під нею, і виправлення. Прочитайте його спочатку: кроки на нього спираються.

### 1. Одна таблиця тримає дизайн, а тести тримають застосунок при ньому

Сьогодні кожен маршрут записано у двох місцях, які ніхто не порівнює: один раз у `app.py` і ще раз вручну у словнику тесту, який каже, яка Operation йому потрібна (`tests/test_app_authentication.py`, з M07-T05). Маршрути, яких ще немає, наприклад адміністрування Sources, не записано ніде. Кожен наступний урок додає маршрути. Ось що буває без спільного дизайну.

| Урок | Маршрут, який він міг би додати | Що не так |
|---|---|---|
| M09-T02 | `GET /v1/source/{id}` | однина `source` і голий `{id}` |
| M09-T03 | `GET /v1/sources/{source_id}/runs?page=2` | інший стиль списків: номери сторінок |
| M09-T09 | `POST /v1/rate_answer` | дієслово з `_` замість ресурсу |
| M10 | `GET /v1/evaluation-runs`, що повертає `{"runs": [...]}` | ще одна форма списку |

Кожен маршрут сам по собі працює. Разом вони дають API, який клієнт не може вгадати: у кожного списку своя форма, у кожного ID своя назва.

Прихований базовий факт: FastAPI нічого не перевіряє в назві маршруту, назві його ID чи формі відповіді. Він приймає будь-який рядок шляху. Узгодженість існує, лише якщо щось її перевіряє.

Виправлення — таблиця в коді, `src/knowledge_service/api_routes.py`. Кожен рядок — один маршрут: метод, шлях, Operation, обслуговується він чи запланований, завдання, яке його будує, короткий опис і чи має він сторінки. Функція `route_problems` звіряє кожен рядок із правилами:

| Правило | Добре | Відмова |
|---|---|---|
| Кожен шлях починається з `/v1/` | `/v1/sources` | `/sources`, `/v2/sources` |
| Слово шляху — малими літерами, з'єднане `-` | `evaluation-runs` | `Sources`, `source_list` |
| Слово — список у множині, крім кількох названих слів, що завершують шлях після ID | `sources`, `/{message_id}/feedback`, `/{job_id}/retry` | `source`, `getsourcenow`, `/v1/feedback` |
| ID названо за списком перед ним | `/v1/sources/{source_id}`, `/v1/entries/{entry_id}` | `/v1/sources/{id}`, `/v1/{source_id}` |
| Дія на кшталт `retry` — лише POST | `POST /v1/jobs/{job_id}/retry` | `GET /v1/jobs/{job_id}/retry` |
| ID трапляється один раз | `/v1/sources/{source_id}` | `/v1/sources/{source_id}/sources/{source_id}` |
| Кожен GET списку має сторінки, а більше ніщо | `GET /v1/sources` зі сторінками | `GET /v1/sources` без сторінок |
| Маршрут, що обслуговується, називає завдання, яке його збудувало | `"M08-T07"` | `None` |

Далі два тести прив'язують застосунок до таблиці. Перший порівнює маршрути, які застосунок справді обслуговує під `/v1`, з рядками таблиці, що обслуговуються. Другий — наявний тест ролей з M07-T05, який тепер бере маршрути з таблиці, а не з написаного вручну словника. Новий маршрут у `app.py`, якого немає в таблиці, валить перший тест; рядок із хибною Operation валить другий.

У таблиці 24 рядки: 6 обслуговуються і 18 заплановано. Заплановані рядки — це дизайн, якого дотримуються наступні уроки.

| Область | Обслуговується зараз | Заплановано |
|---|---|---|
| Questions | `POST /v1/questions` | |
| Conversations | `DELETE /v1/conversations/{conversation_id}` | список, читання однієї, список її повідомлень |
| Відгуки | | `PUT` і `DELETE .../messages/{message_id}/feedback` (M09-T09) |
| Діагностика | | `GET .../messages/{message_id}/diagnostics` (завдання поки немає) |
| Sources | | список, реєстрація, читання, зміна, увімкнення, вимкнення (M09-T02) |
| Синхронізація | `GET /v1/jobs/{job_id}` | запуск і список `/v1/sources/{source_id}/synchronizations` (M09-T03) |
| Jobs, що чекають на рішення | `GET /v1/job-decisions`, `POST .../retry`, `POST .../cancel` | |
| Оцінювання | | набори даних (M10-T01), запуски (M10-T11) |

Побутова аналогія: план поверху будинку. Кожен будівельник додає кімнату, але план каже, де двері і як нумерують кімнати. Інспектор порівнює будинок із планом.

### 2. Маршрут без пошуку зникає, а маршрут відповідей отримує назву ресурсу

У сервісі два маршрути, що приймають Question.

| Маршрут | Що він робить |
|---|---|
| `POST /v1/answer` (M01) | надсилає Question просто моделі: без пошуку, без Evidence, без Citations, без обмеження відповіді, і не просить провайдера не зберігати текст |
| `POST /v1/answer/stream` (M06) | заземлений шлях: шукає від імені читача, перевіряє Citations, може утриматися від відповіді і передає Answer потоком |

Модель загроз (M07-T01) називає перший відомою прогалиною, яку «версіонований маршрут Question замінить (M09-T01)». Це той самий урок.

Прихований базовий факт: маршрут доступний кожному, хто має токен із роллю `user`, незалежно від того, чи якийсь клієнт ним користується. Залишити старий маршрут — означає залишити прогалину у витратах і приватності.

Наскільки це погано сьогодні: мало, бо жоден клієнт його не викликає. Стало б гірше: M09-T05 адаптує OpenAI chat completions, і адаптер, під'єднаний не до того workflow, відповідав би без Evidence.

Виправлення:

| Було | Стало |
|---|---|
| `POST /v1/answer` | прибрано; разом із ним зникає аргумент `workflow` у `create_app` |
| `POST /v1/answer/stream` | `POST /v1/questions`, та сама функція, перейменована на `ask_question` |
| код `workflow_unavailable`, коли відповідати нікому | `answering_unavailable` |

Назва йде за правилом блоку 1: ресурс — це Question, тож клієнт надсилає одне питання до списку Questions, а відповідь передає потоком його Answer. `workflow.py`, `openai_model.py` і `contracts.Answer` поки лишаються в коді, і користуються ними лише їхні власні тести; M09-T05 вирішить, чи його адаптер їх використає.

### 3. Сторінка починається після останнього елемента, а не після лічильника

Список рішень упорядковано від найновішої відмови. Припустімо, адміністратор читає його по два Jobs зі зсувом (offset), як найчастіше роблять спершу:

| Момент | Адміністратор | Список у PostgreSQL, найновіші першими | Що показує сторінка |
|---|---|---|---|
| 10:00 | просить сторінку 1: пропустити 0, взяти 2 | C, B, A | C, B |
| 10:01 | | Job D впав: D, C, B, A | |
| 10:02 | просить сторінку 2: пропустити 2, взяти 2 | D, C, B, A | B, A |

B з'являється двічі. Якби якийсь Job натомість запустили повторно і він вийшов зі списку, один Job було б пропущено, і його ніхто б не побачив.

Прихований базовий факт: зсув рахує рядки в момент кожного запиту. Кожна сторінка — новий запит до списку, який міг змінитися, тож «третій рядок» щоразу інший Job.

Виправлення — пагінація за ключем. Сторінка запам'ятовує ключ сортування свого останнього Job, `(finished_at, job_id)`, і наступний запит просить Jobs строго перед цим ключем:

```sql
-- page 2, after the last Job of page 1
SELECT ... FROM jobs
WHERE status = 'failed' AND retry_at IS NULL AND ...
  AND (finished_at, job_id) < ('2026-11-24 09:02:00+00', '01a2...')
ORDER BY finished_at DESC, job_id DESC
LIMIT 3;
```

PostgreSQL порівнює дві пари зліва направо, як сортують слова: спершу за `finished_at`, і лише коли час однаковий — за `job_id`. Тож Jobs, що впали в ту саму мікросекунду, усе одно мають один незмінний порядок, а D, який упав пізніше, стає перед сторінкою 1 і ніколи не зсуває сторінку 2.

| Момент | Адміністратор | Список | Що показує сторінка |
|---|---|---|---|
| 10:00 | сторінка 1: перші 2 | C, B, A | C, B; курсор запам'ятовує B |
| 10:01 | | D впав: D, C, B, A | |
| 10:02 | сторінка 2: після B | D, C, B, A | A; курсора немає, список скінчився |

`LIMIT 3` просить на один Job більше, ніж вміщує сторінка. Якщо він повернувся, далі є ще сторінка, і `next_cursor` називає останній Job сторінки; якщо ні, `next_cursor` дорівнює `null`. Без цього зайвого рядка сторінка рівно з 2 Jobs не відрізнила б «є ще» від «це був кінець».

Курсор проходить через клієнта, тож його має бути безпечно віддати і безпечно прийняти назад:

| Рішення | Чому |
|---|---|
| base64url невеликого JSON-списку: `[1, "job-decisions", {"finished_at": ..., "job_id": ...}]` | вміщується в URL; клієнти вважають його непрозорим, тож вміст можна згодом змінити |
| `1` — версія | майбутній формат може чисто відмовити старим курсорам |
| `"job-decisions"` називає список | курсор одного списку ніколи не спрацює на іншому списку з тими самими ключами |
| декодування відкидає все, чого `encode_cursor` не написав би, зокрема інші написання того самого вмісту | на кожну позицію рівно один дійсний курсор, тож нічого не протягнеш через порядок ключів, пробіли чи зайві біти base64 |
| без підпису | він лише каже, звідки починається список, який викликач і так може прочитати повністю; підроблений курсор не покаже нічого нового |
| не більше 512 символів | робота і пам'ять на декодування лишаються малими, хоч би що надіслав клієнт |

Повтор і скасування тут прості. Читання сторінки нічого не змінює, тож повторений чи скасований запит нічого не робить; той самий курсор, надісланий двічі, дає сторінку, що йде після того самого Job.

Побутова аналогія: закладка в книжці, до якої спереду вклеюють нові сторінки. «Сторінка 37» зсувається; закладка після речення — ні.

### 4. Хто, чи можна, потім що: і для запиту, і для шляху

Кожен маршрут від M07 перевіряє в незмінному порядку: хто викликач (401), чи дозволяє його роль операцію (403), і лише потім його вхідні дані (422). Викликач без дозволу нічого не дізнається про правила вхідних даних.

FastAPI порушує цей порядок, якщо дозволити йому перевіряти. Оголосіть `limit: int = Query(ge=1, le=100)`, і FastAPI перевірить його ще до того, як запуститься ваша функція:

| Запит | З FastAPI `Query` | Потрібно |
|---|---|---|
| без токена, `?limit=0` | 422 | 401 |
| `user` без `recover_jobs`, `?limit=0` | 422 | 403 |

Прихований базовий факт: FastAPI перевіряє оголошені параметри до виклику функції маршруту, а отже, до будь-якого коду в ній, зокрема до перевірки особи.

Тому маршрути читають `limit` і `cursor` самі, після `require`, через `page_request`. OpenAPI все одно має їх описати, тож їх вписують у документ вручну через `openapi_extra`. Те саме з ID у шляху. Для FastAPI вони лишаються звичайним текстом, тож він ніколи не відмовить першим; маршрут перевіряє їхнє написання після ролі. Для документа невелика анотація позначає їх як UUID:

```python
# Was: a path ID documented as any string
async def read_synchronization_job(request: Request, job_id: str) -> ...

# Becomes: the same text to FastAPI, a UUID in OpenAPI
Identifier = Annotated[str, Path(json_schema_extra={"format": "uuid"})]
async def read_synchronization_job(request: Request, job_id: Identifier) -> ...
```

`json_schema_extra` лише додає `"format": "uuid"` до документа. FastAPI не вимагає цього для `str`, і прототип це перевірив: `/v1/x/notauuid` усе одно доходить до функції.

`page_request` також відкидає параметр, якого не знає, і параметр, переданий двічі. Інакше адміністратор, який набрав `?Limit=5`, отримав би типові 50 Jobs і не дізнався б, що параметр проігноровано.

### 5. Одна форма помилки для кожної відповіді під /v1

Помилки проєкту мають одну форму від M01: `{"code": ..., "message": ..., "request_id": ...}`. Змагальне рев'ю (adversarial review) прототипу цього уроку знайшло відповіді під `/v1`, що її обходять:

| Запит | Відповідь до виправлення |
|---|---|
| `POST /v1/answer` після видалення | 404 `{"detail": "Not Found"}` |
| `GET /v1/questions` | 405 `{"detail": "Method Not Allowed"}` |
| `GET /v1/job-decisions/` | 307, перенаправлення на шлях без `/`, токен не перевірено |

Прихований базовий факт: ці відповіді дає Starlette, фреймворк під FastAPI, ще до будь-якої функції маршруту. Він кидає власний `HTTPException` Starlette, якого обробники застосунку не покривали.

У документі є ще одна прогалина. FastAPI додає кожному маршруту з параметрами відповідь 422 зі своєю схемою `HTTPValidationError`, але цей проєкт ніколи не надсилає такої форми: його 422 — це `ErrorResponse`. Клієнт, згенерований із документа, чекав би не того тіла.

Виправлення з трьох частин:

| Частина | Що вона робить |
|---|---|
| `http_error_handler` для `HTTPException` Starlette | 404 стає `not_found`, 405 — `method_not_allowed`, в одній формі; заголовок `Allow` відповіді 405 зберігається |
| `FastAPI(redirect_slashes=False)` | `/v1/job-decisions/` — це 404, а не перенаправлення |
| `error_responses(...)` на кожному маршруті | описує 400, 401, 403, 422, 500 і 503, плюс власні статуси маршруту, усі з `ErrorResponse`; маршрут, що описує власну 422, змушує FastAPI не додавати `HTTPValidationError` |

404 для невідомого шляху все одно приходить до перевірки особи. Для HTTP API це звично і розкриває лише перелік шляхів, який документ OpenAPI і так публікує.

### 6. Жодних слів провайдера моделей у нативному API

ADR 0004 вирішує, що нативний API показує ідеї цього сервісу — Questions, Evidence, Citations, відгуки, діагностику, — а OpenAI-сумісний адаптер перекладає для Open WebUI (M09-T04–M09-T07). Якщо нативна схема почне вживати `messages`, `choices` чи `role: assistant`, адаптерові не буде чого перекладати, і кожен нативний клієнт прив'яжеться до протоколу одного провайдера.

Тест перелічує поля провайдера, які ніколи не мають з'являтися в жодній схемі документа, включно з тілом Question: `choices`, `messages`, `finish_reason`, `object`, `delta`, `logprobs`, `role`, `content`, `model`, `id`. Він також відкидає назви схем, що містять `openai`, `chat` чи `completion`. `id` у списку ще з однієї причини: кожен ідентифікатор цього API названо за його ресурсом, наприклад `job_id`, і це UUID.

### Інші знахідки змагального рев'ю

Незалежне рев'ю атакувало прототип до того, як написано цей посібник. Крім блоків 4 і 5, воно знайшло таке; кожне виправлено, і для кожного є тест, що падає без виправлення:

- **Повна остання сторінка обіцяла ще одну.** З `len(jobs) >= limit` 4 Jobs, прочитані по 2, давали третю, порожню сторінку. Тести брали лише 5 Jobs, ніколи не рівне кратне. Тепер перевірка — `>`, і тест читає 4 Jobs сторінками по 2.
- **Проігнорований курсор вішав тест.** Цикл сторінок із `while True` крутився вічно, коли курсор ігнорувався. Тепер кожен такий цикл зупиняється після 10 сторінок і падає.
- **Тести OpenAPI пропускали справжні зміни.** Прибрати 404 маршруту з документа, додати поле `id: int` або поля `role` і `content` — усе проходило. Тепер статуси помилок кожного маршруту порівнюються точно, кожне поле `id` має бути UUID, а список полів провайдера довший.
- **Правила маршрутів приймали погані маршрути.** `GET /v1/source/{source_id}`, повторений `{source_id}`, `/v1/feedback` у корені і дієслово на кшталт `getsourcenow` проходили. `/v1/entries/{entry_id}` відкидався, бо `entries` без `s` — це `entrie`. Тепер кожне слово має бути списком, окремим ресурсом чи дією, а `-ies` стає `-y`.
- **Один курсор мав кілька написань.** Переставлені ключі, пробіли, повторений ключ чи 4 різні останні літери base64 декодувалися в ту саму позицію. Тепер декодування кодує позицію заново і вимагає точно того самого тексту.
- **Ніщо не доводило, що роль перевіряється раніше за параметри сторінки.** Якщо перенести читання `limit` і `cursor` вище перевірки ролі, усі тести лишалися зеленими. Тоді клієнт без ролі дізнавався б правила вводу з відповіді 422. Тепер тест надсилає `?limit=0`, `?cursor=%%` і `?Limit=5` без токена (401) і з роллю `user` (403) і перевіряє, що сховище ніхто не питав. Те саме рев'ю заборонило друге написання `limit`, наприклад `007`.

### Відомі обмеження

- Неправильні форми множини, як-от `analyses`, правила не мають; назвіть такий список інакше або додайте правило з тестом.
- 404 для невідомого шляху відповідає до перевірки особи, як у будь-якому HTTP API.
- Потокові події маршруту Question поки не описано в OpenAPI; M09-T11 опише їх із прикладами.
- `workflow.py`, `openai_model.py` і `contracts.Answer` більше не обслуговуються; M09-T05 використає або прибере їх.
- Сторінки сьогодні має лише `GET /v1/job-decisions`. Кожен запланований список іде за тими самими `page_request`, курсором і формою `{items, next_cursor}`.

## Крок 1 — Прочитати, передбачити і побачити, як перевірка падає

Без коду в цьому кроці.

Цей крок фіксує, чого ви чекаєте, ще до будь-яких змін, щоб у доказах можна було порівняти передбачення і результат.

- Прочитайте докази M08-T07 і M08-T08, які збудували `GET /v1/jobs/{job_id}` і маршрути рішень, та M07-T05, який збудував тест ролей.
- Запишіть для файлу доказів: інваріант («кожен маршрут `/v1` — це рядок таблиці маршрутів, що обслуговується, потребує Operation цього рядка і описує помилки через `ErrorResponse`; кожен список має сторінки за курсором»), власника авторизації (`require` маршруту, до будь-яких вхідних даних) і власника повторів (клієнт: читання сторінки нічого не змінює).
- Передбачте: скільки запитів зробить адміністратор, читаючи 4 Jobs, що впали, по 2, і який `next_cursor` у другої сторінки? (Два запити; `null`.)

Потім запустіть перевірку, яка спершу має впасти:

```bash
uv run pytest tests/test_api_routes.py -q
```

Очікувано: `ERROR: file or directory not found: tests/test_api_routes.py` і `no tests ran`, код виходу 4.

## Крок 2 — Написати таблицю маршрутів

Крок із кодом.

Простими словами: цей файл — план поверху API. Він перелічує кожен маршрут, на який сервіс відповідає або відповідатиме, хто може його викликати і який урок його будує. Тут же живе перевірка, що каже, яке правило порушує погано названий маршрут. Ні мережі, ні бази даних тут не викликають.

Створіть `src/knowledge_service/api_routes.py`:

| Частина | Що в ній |
|---|---|
| `Method`, `RouteStatus` | HTTP-методи, якими користується API; обслуговується чи заплановано |
| `Route` | один рядок: метод, шлях, Operation, статус, завдання, опис, `paginated` |
| `RouteProblem` | маршрут і порушене правило, текстом, який тест може порівняти |
| `SINGLE_RESOURCES`, `ACTIONS` | єдині дозволені слова, що не є списками в множині; кожне завершує шлях після ID |
| `ROUTES` | 24 рядки, згруповані за областями |
| `served_routes` | рядки, які застосунок мусить обслуговувати |
| `route_problems` | кожне порушене правило кожного рядка; порожньо — дизайн тримається |

```python
"""The native HTTP API's routes: one table, and the rules every row follows.

Every route the service serves or plans is a row here, with the Operation an
Application Role must permit and the task that builds it. `route_problems`
checks the naming, identifier, and pagination rules; the tests check that the
app serves exactly the served rows, each behind its Operation.
"""

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from knowledge_service.roles import Operation

API_PREFIX = "/v1"
# Words that end a path after an ID: one thing of that item, such as its
# feedback, and something done to it, such as a retry. Every other word must
# name a list, in the plural.
SINGLE_RESOURCES = frozenset({"feedback", "diagnostics"})
ACTIONS = frozenset({"enable", "disable", "retry", "cancel"})

_WORD = re.compile(r"[a-z]+(?:-[a-z]+)*")
_PARAMETER = re.compile(r"\{([a-z_]+)\}")
_TASK = re.compile(r"M\d{2}-T\d{2}")


class Method(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class RouteStatus(StrEnum):
    SERVED = "served"
    PLANNED = "planned"


@dataclass(frozen=True, slots=True)
class Route:
    """One route: who may call it, whether it is served, and who builds it.

    `task` is the curriculum task that built or builds it; None marks a
    planned route no task schedules yet. A `paginated` route takes `limit`
    and `cursor` and answers `{"items": [...], "next_cursor": ...}`.
    """

    method: Method
    path: str
    operation: Operation
    status: RouteStatus
    task: str | None
    summary: str
    paginated: bool = False


@dataclass(frozen=True, slots=True)
class RouteProblem:
    """A rule one route breaks."""

    route: Route
    rule: str


_GET, _POST, _PUT, _PATCH, _DELETE = Method
_SERVED, _PLANNED = RouteStatus.SERVED, RouteStatus.PLANNED
_MESSAGE = "/v1/conversations/{conversation_id}/messages/{message_id}"

ROUTES: tuple[Route, ...] = (
    # Questions
    Route(
        _POST,
        "/v1/questions",
        Operation.ASK_QUESTIONS,
        _SERVED,
        "M09-T01",
        "Ask a Question; the response streams its Answer",
    ),
    # Conversations, each only its owner's
    Route(
        _GET,
        "/v1/conversations",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        None,
        "List the caller's Conversations",
        paginated=True,
    ),
    Route(
        _GET,
        "/v1/conversations/{conversation_id}",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        None,
        "Read one of the caller's Conversations",
    ),
    Route(
        _DELETE,
        "/v1/conversations/{conversation_id}",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _SERVED,
        "M07-T16",
        "Delete one of the caller's Conversations",
    ),
    Route(
        _GET,
        "/v1/conversations/{conversation_id}/messages",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        None,
        "List a Conversation's Questions and Answers",
        paginated=True,
    ),
    # Feedback on one of the caller's Answers
    Route(
        _PUT,
        f"{_MESSAGE}/feedback",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        "M09-T09",
        "Rate an Answer up or down; the same rating again changes nothing",
    ),
    Route(
        _DELETE,
        f"{_MESSAGE}/feedback",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        "M09-T09",
        "Remove the caller's rating of an Answer",
    ),
    # Diagnostics of one of the caller's Answers: counts, timings, and usage
    Route(
        _GET,
        f"{_MESSAGE}/diagnostics",
        Operation.MANAGE_OWN_CONVERSATIONS,
        _PLANNED,
        None,
        "How an Answer was made, without any Document text",
    ),
    # Sources
    Route(
        _GET,
        "/v1/sources",
        Operation.CONFIGURE_SOURCES,
        _PLANNED,
        "M09-T02",
        "List Sources",
        paginated=True,
    ),
    Route(
        _POST,
        "/v1/sources",
        Operation.CONFIGURE_SOURCES,
        _PLANNED,
        "M09-T02",
        "Register a Source",
    ),
    Route(
        _GET,
        "/v1/sources/{source_id}",
        Operation.CONFIGURE_SOURCES,
        _PLANNED,
        "M09-T02",
        "Read a Source's configuration",
    ),
    Route(
        _PATCH,
        "/v1/sources/{source_id}",
        Operation.CONFIGURE_SOURCES,
        _PLANNED,
        "M09-T02",
        "Change a Source's configuration",
    ),
    Route(
        _POST,
        "/v1/sources/{source_id}/enable",
        Operation.CONFIGURE_SOURCES,
        _PLANNED,
        "M09-T02",
        "Let a Source synchronize again",
    ),
    Route(
        _POST,
        "/v1/sources/{source_id}/disable",
        Operation.CONFIGURE_SOURCES,
        _PLANNED,
        "M09-T02",
        "Stop a Source from synchronizing",
    ),
    # Synchronization: durable Jobs, never broker messages
    Route(
        _POST,
        "/v1/sources/{source_id}/synchronizations",
        Operation.START_SYNCHRONIZATION_RUNS,
        _PLANNED,
        "M09-T03",
        "Start a synchronization, or return the one already open",
    ),
    Route(
        _GET,
        "/v1/sources/{source_id}/synchronizations",
        Operation.START_SYNCHRONIZATION_RUNS,
        _PLANNED,
        "M09-T03",
        "List a Source's synchronization Jobs",
        paginated=True,
    ),
    Route(
        _GET,
        "/v1/jobs/{job_id}",
        Operation.START_SYNCHRONIZATION_RUNS,
        _SERVED,
        "M08-T07",
        "Read a synchronization Job and its newest attempt",
    ),
    # Jobs that failed for good, for an administrator
    Route(
        _GET,
        "/v1/job-decisions",
        Operation.RECOVER_JOBS,
        _SERVED,
        "M08-T08",
        "List Jobs that failed for good, newest first",
        paginated=True,
    ),
    Route(
        _POST,
        "/v1/jobs/{job_id}/retry",
        Operation.RECOVER_JOBS,
        _SERVED,
        "M08-T08",
        "Queue a Job that failed for good once more",
    ),
    Route(
        _POST,
        "/v1/jobs/{job_id}/cancel",
        Operation.RECOVER_JOBS,
        _SERVED,
        "M08-T08",
        "Cancel a Job that has not finished",
    ),
    # Evaluation
    Route(
        _GET,
        "/v1/evaluation-datasets",
        Operation.MANAGE_EVALUATION_DATASETS,
        _PLANNED,
        "M10-T01",
        "List Evaluation Datasets",
        paginated=True,
    ),
    Route(
        _POST,
        "/v1/evaluation-runs",
        Operation.RUN_PAID_EVALUATION,
        _PLANNED,
        "M10-T11",
        "Start a paid evaluation run",
    ),
    Route(
        _GET,
        "/v1/evaluation-runs",
        Operation.RUN_PAID_EVALUATION,
        _PLANNED,
        "M10-T11",
        "List evaluation runs",
        paginated=True,
    ),
    Route(
        _GET,
        "/v1/evaluation-runs/{evaluation_run_id}",
        Operation.RUN_PAID_EVALUATION,
        _PLANNED,
        "M10-T11",
        "Read an evaluation run and its results",
    ),
)


def served_routes() -> tuple[Route, ...]:
    """The rows the app must serve, in table order."""
    return tuple(route for route in ROUTES if route.status is RouteStatus.SERVED)


def route_problems(routes: Iterable[Route]) -> tuple[RouteProblem, ...]:
    """Every rule each route breaks; an empty tuple means the design holds."""
    routes = tuple(routes)
    seen = Counter((route.method, route.path) for route in routes)
    problems: list[RouteProblem] = []
    for route in routes:
        problems += [RouteProblem(route, rule) for rule in _broken_rules(route)]
        if seen[route.method, route.path] > 1:
            problems.append(RouteProblem(route, "the same method and path twice"))
    return tuple(problems)


def _broken_rules(route: Route) -> list[str]:
    broken: list[str] = []
    if type(route.method) is not Method:
        broken.append("no Method")
    if type(route.operation) is not Operation:
        broken.append("no Operation")
    if type(route.status) is not RouteStatus:
        broken.append("no RouteStatus")
    elif route.status is RouteStatus.SERVED and route.task is None:
        broken.append("served without a task")
    if route.task is not None and not _TASK.fullmatch(route.task):
        broken.append("a task that is not a task ID")
    if not route.path.startswith(f"{API_PREFIX}/"):
        return [*broken, "not under /v1"]
    segments = route.path.removeprefix(f"{API_PREFIX}/").split("/")
    names: set[str] = set()
    previous: str | None = None
    for position, segment in enumerate(segments):
        parameter = _PARAMETER.fullmatch(segment)
        if parameter is not None:
            if previous is None or parameter[1] != _identifier_of(previous):
                broken.append(f"{segment} does not name the list before it")
            if parameter[1] in names:
                broken.append(f"{segment} appears twice")
            names.add(parameter[1])
        elif not _WORD.fullmatch(segment):
            broken.append(f"{segment!r} is not lowercase words joined by '-'")
        elif segment in SINGLE_RESOURCES or segment in ACTIONS:
            after_id = previous is not None and _PARAMETER.fullmatch(previous)
            if position != len(segments) - 1 or not after_id:
                broken.append(f"{segment!r} must end the path, after an ID")
            if segment in ACTIONS and route.method is not Method.POST:
                broken.append(f"{segment!r} is an action, so only POST")
        elif not _is_list(segment):
            broken.append(f"{segment!r} is not a list, a single resource, or an action")
        previous = segment
    lists = route.method is Method.GET and _is_list(segments[-1])
    if route.paginated and not lists:
        broken.append("paginated, but not a GET of a list")
    if lists and not route.paginated:
        broken.append("a GET of a list that is not paginated")
    return broken


def _is_list(segment: str) -> bool:
    """A plural word, such as `sources` or `job-decisions`."""
    return (
        _WORD.fullmatch(segment) is not None
        and segment.endswith("s")
        and segment not in SINGLE_RESOURCES
        and segment not in ACTIONS
    )


def _identifier_of(collection: str) -> str | None:
    """`sources` -> `source_id`, `entries` -> `entry_id`; None if not a list.

    An irregular plural, such as `analyses`, has no rule here: name such a
    list differently, or add the rule with a test.
    """
    if not _is_list(collection):
        return None
    singular = f"{collection[:-3]}y" if collection.endswith("ies") else collection[:-1]
    return f"{singular.replace('-', '_')}_id"
```

Важать дві деталі. `_broken_rules` перевіряє типи через `type(...) is`, тож звичайний рядок `"served"` замість `RouteStatus.SERVED` — це проблема, а не тихий пропуск. І вона збирає всі проблеми, а не зупиняється на першій, тож один тест показує всі помилки рядка одразу.

Перевірка: `uv run python -c "from knowledge_service.api_routes import ROUTES, route_problems; print(len(ROUTES), route_problems(ROUTES))"` друкує `24 ()`.

## Крок 3 — Написати допоміжні функції розміру сторінки і курсора

Крок із кодом.

Простими словами: цей файл перетворює два параметри сторінки на безпечні значення. Він читає `limit` як ціле число від 1 до 100. Він пише курсор, що називає, звідки починається наступна сторінка, а коли курсор повертається, приймає його, лише якщо це точно те, що він сам написав би для цього списку.

Створіть `src/knowledge_service/pagination.py`:

| Частина | Що вона робить |
|---|---|
| `DEFAULT_PAGE_SIZE`, `MAX_PAGE_SIZE`, `MAX_CURSOR_CHARACTERS` | 50, 100, 512 |
| `InvalidPage` | єдина помилка для поганого `limit` чи курсора; маршрут перетворює її на 422 |
| `page_size` | `None` означає 50; приймаються лише 1–3 ASCII-цифри від 1 до 100 |
| `encode_cursor(listing, position)` | `[1, listing, position]` як компактний відсортований JSON, потім base64url без доповнення `=` |
| `decode_cursor(cursor, listing=, keys=)` | перевіряє довжину й абетку, декодує, перевіряє структуру, потім кодує заново і вимагає того самого тексту |

```python
"""Pages of a list: how many items, and where the next page starts.

Every paginated route takes `limit` and `cursor` and answers
`{"items": [...], "next_cursor": ...}`. A cursor names the last item of the
page before, so the next page starts after it even when items are added in
between. Clients must treat it as opaque. It is not signed: it only says
where to start in a list its caller may read whole anyway, and the server
checks every field before using it.
"""

import base64
import json
import re
from collections.abc import Mapping
from typing import cast

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100
MAX_CURSOR_CHARACTERS = 512
_CURSOR_VERSION = 1
_BASE64URL = re.compile(r"[A-Za-z0-9_-]+")
_DIGITS = re.compile(r"[1-9][0-9]{0,2}")


class InvalidPage(ValueError):
    """A page size or cursor the API does not accept."""


def page_size(raw: str | None) -> int:
    """`limit` as a number of items: absent means the default."""
    if raw is None:
        return DEFAULT_PAGE_SIZE
    if not _DIGITS.fullmatch(raw) or not 1 <= int(raw) <= MAX_PAGE_SIZE:
        raise InvalidPage(f"limit must be a whole number from 1 to {MAX_PAGE_SIZE}")
    return int(raw)


def encode_cursor(listing: str, position: Mapping[str, str]) -> str:
    """The cursor that starts the next page of `listing` after `position`."""
    document = json.dumps(
        [_CURSOR_VERSION, listing, dict(position)],
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.urlsafe_b64encode(document.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, *, listing: str, keys: frozenset[str]) -> dict[str, str]:
    """The position a cursor of `listing` names, with exactly `keys`, all text.

    Raises `InvalidPage` for anything `encode_cursor` would not have written
    for this list: a cursor too long, not base64url, not JSON, of another
    version or list, with other fields, or another spelling of the same ones.
    """
    if len(cursor) > MAX_CURSOR_CHARACTERS or not _BASE64URL.fullmatch(cursor):
        raise InvalidPage("the cursor is not one this API gave")
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        document: object = json.loads(raw.decode())
    # Bad base64, bad UTF-8, and bad JSON are all a ValueError.
    except ValueError:
        raise InvalidPage("the cursor is not one this API gave") from None
    position = _position(document, listing, keys)
    # One spelling only: key order, spaces, or spare base64 bits differ.
    if position is None or encode_cursor(listing, position) != cursor:
        raise InvalidPage("the cursor is not one this API gave")
    return position


def _position(
    document: object, listing: str, keys: frozenset[str]
) -> dict[str, str] | None:
    """`[1, listing, {key: text, ...}]` with exactly `keys`, else None."""
    if type(document) is not list:
        return None
    parts = cast(list[object], document)
    # `type(...) is int`, because JSON `true` would equal 1.
    if len(parts) != 3 or type(parts[0]) is not int or parts[0] != _CURSOR_VERSION:
        return None
    if parts[1] != listing or type(parts[2]) is not dict:
        return None
    fields = cast(dict[object, object], parts[2])
    position = {
        key: value
        for key, value in fields.items()
        if type(key) is str and type(value) is str
    }
    if len(position) != len(fields) or frozenset(position) != keys:
        return None
    return position
```

Чому `type(parts[0]) is not int`: у Python `True == 1`, тож JSON `true` при звичайному порівнянні пройшов би як версія 1. Чому кодувати заново наприкінці: це найкоротший спосіб відкинути одразу всі інші написання, хоч що дозволяють JSON і base64.

Перевірка: `uv run python -c "from knowledge_service.pagination import *; c = encode_cursor('x', {'a': 'b'}); print(c, decode_cursor(c, listing='x', keys=frozenset({'a'})))"` друкує курсор і `{'a': 'b'}`.

## Крок 4 — Дати списку рішень починатися після Job

Крок із кодом.

Простими словами: запит до бази даних за Jobs, що чекають на рішення, вчиться починати після заданого Job, а не з верху. Потім модуль рішень вчиться писати курсор для останнього Job сторінки і читати його назад як час і ID, відкидаючи будь-яке інше написання.

Спершу запит, у `src/knowledge_service/persistence/job_recovery.py`. Замініть `load_failed_for_good`:

```python
async def load_failed_for_good(
    connection: AsyncConnection,
    *,
    limit: int,
    after: tuple[datetime, JobId] | None = None,
) -> tuple[Job, ...]:
    """Jobs that failed with no retry planned, that no newer Job replaced.

    Newest failure first. A Job for the same work queued after it, such as
    the next scheduled synchronization, replaces it, so it is not listed.
    With `after`, the list starts after the Job that finished then with
    that ID, so pages never repeat or skip a Job, even one that finished at
    the same moment.
    """
    conditions = [
        jobs.c.status == JobStatus.FAILED.value,
        jobs.c.retry_at.is_(None),
        ~_newer_of_same_intent(),
    ]
    if after is not None:
        finished_at, job_id = after
        conditions.append(
            sa.tuple_(jobs.c.finished_at, jobs.c.job_id)
            < sa.tuple_(sa.literal(finished_at), sa.literal(job_id.value))
        )
    statement = (
        sa.select(jobs)
        .where(*conditions)
        .order_by(jobs.c.finished_at.desc(), jobs.c.job_id.desc())
        .limit(limit)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(job_from_record(dict(record)) for record in records)
```

`sa.tuple_(...) < sa.tuple_(...)` пише SQL-порівняння рядків із блоку 3. `finished_at` ніколи не `NULL` у Job, що впав: це гарантує обмеження `ck_jobs_finished_at`, тож порівняння ніколи не натрапить на невідоме значення.

Далі `src/knowledge_service/job_decisions.py`. Додайте імпорт:

```python
from knowledge_service.pagination import InvalidPage, decode_cursor, encode_cursor
```

Додайте назву і ключі списку під `MAX_DECISION_LIST`:

```python
MAX_DECISION_LIST: Final = 500
_DECISION_LIST = "job-decisions"
_DECISION_CURSOR_KEYS = frozenset({"finished_at", "job_id"})
```

Замініть `load_jobs_needing_decision` і додайте після неї `decision_cursor` і `decision_position`:

```python
async def load_jobs_needing_decision(
    runtime: DatabaseRuntime,
    *,
    limit: int = 100,
    after: tuple[datetime, JobId] | None = None,
) -> tuple[Job, ...]:
    """Jobs that failed for good and that no newer Job replaced, newest first.

    With `after`, from a `decision_position`, the list starts after that Job.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_DECISION_LIST:
        raise ValueError(f"limit must be an integer from 1 to {MAX_DECISION_LIST}")
    async with runtime.transaction() as connection:
        return await load_failed_for_good(connection, limit=limit, after=after)


def decision_cursor(job: Job) -> str:
    """The cursor of the page that starts after `job` in the decision list."""
    if job.finished_at is None:
        raise ValueError("only a finished Job ends a page of decisions")
    return encode_cursor(
        _DECISION_LIST,
        {"finished_at": job.finished_at.isoformat(), "job_id": str(job.job_id.value)},
    )


def decision_position(cursor: str) -> tuple[datetime, JobId]:
    """Where a `decision_cursor` says the next page starts.

    Only the exact text `decision_cursor` writes is accepted, so an offset
    other than UTC or another spelling of the same ID raises `InvalidPage`.
    """
    position = decode_cursor(cursor, listing=_DECISION_LIST, keys=_DECISION_CURSOR_KEYS)
    finished_text, job_text = position["finished_at"], position["job_id"]
    try:
        finished_at = datetime.fromisoformat(finished_text)
        job_id = UUID(job_text)
    except ValueError:
        raise InvalidPage("the cursor is not one this API gave") from None
    if (
        finished_at.utcoffset() != timedelta(0)
        or finished_at.isoformat() != finished_text
        or str(job_id) != job_text
    ):
        raise InvalidPage("the cursor is not one this API gave")
    return finished_at, JobId(job_id)
```

`decision_position` відкидає час з іншим зсувом навіть тоді, коли це той самий момент, як-от `10:30+01:00` замість `09:30+00:00`, бо `decision_cursor` завжди пише UTC. Він також відкидає ID великими літерами чи у фігурних дужках. Один курсор на Job зберігає правило кроку 3 і для полів усередині.

Насамкінець передайте `after` крізь `DatabaseJobDecisions`:

```python
    async def needing_decision(
        self, *, limit: int, after: tuple[datetime, JobId] | None = None
    ) -> tuple[Job, ...]:
        return await load_jobs_needing_decision(self.runtime, limit=limit, after=after)
```

Перевірка: `uv run pyright src/knowledge_service/job_decisions.py src/knowledge_service/persistence/job_recovery.py` повідомляє 0 помилок.

## Крок 5 — Дати списку рішень форму сторінки

Крок із кодом.

Простими словами: модель відповіді списку рішень змінюється з одного списку `jobs` на форму, яку матиме кожен список: елементи сторінки і курсор наступної сторінки або нічого.

У `src/knowledge_service/contracts.py` замініть `JobsNeedingDecision`:

```python
class JobsNeedingDecision(BaseModel):
    """A page of Jobs that failed for good and no newer Job replaced.

    Newest first. `next_cursor` starts the next page; None on the last one.
    """

    items: list[OperatorJob]
    next_cursor: str | None
```

`next_cursor` не має типового значення, тож OpenAPI позначає його обов'язковим: клієнт може покладатися на те, що ключ є, з `null` на останній сторінці.

Це перейменування поля відповіді (`jobs` на `items`). Жоден клієнт ним ще не користується, тому зміна відбувається зараз, а не після того, як M09-T08 під'єднає Open WebUI.

## Крок 6 — Змінити застосунок

Крок із кодом.

Простими словами: застосунок прибирає ранній маршрут відповідей, обслуговує маршрут відповідей як `POST /v1/questions`, відповідає на кожен невідомий шлях чи метод в одній формі помилки, видає список рішень сторінками і описує в документі OpenAPI помилки, ID і параметри сторінок кожного маршруту. Порядок перевірок у кожному маршруті лишається: хто, чи можна, потім що.

Усі зміни — у `src/knowledge_service/app.py`, у порядку файлу.

**Імпорти.** `Annotated` і `Any` з `typing`; `Path` з `fastapi`; `HTTPException` зі Starlette:

```python
from typing import Annotated, Any, Protocol
```

```python
from fastapi import FastAPI, Path, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Message, Receive, Scope, Send
```

Приберіть `Answer` з імпорту `knowledge_service.contracts` і рядок `from knowledge_service.workflow import AnswerWorkflow`. Імпортуйте функції курсора і сторінок:

```python
from knowledge_service.job_decisions import (
    JobIntentAlreadyOpen,
    JobNotFound,
    decision_cursor,
    decision_position,
)
```

```python
from knowledge_service.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_CURSOR_CHARACTERS,
    MAX_PAGE_SIZE,
    InvalidPage,
    page_size,
)
```

**Тип ID у шляху**, після `CANONICAL_ID` (блок 4):

```python
# A path ID, documented as a UUID but read as text: the route checks its
# spelling itself, after the caller's role, so it never answers first.
Identifier = Annotated[str, Path(json_schema_extra={"format": "uuid"})]
```

**Протокол сховища.** `JobDecisions.needing_decision` приймає `after`:

```python
    async def needing_decision(
        self, *, limit: int, after: tuple[datetime, JobId] | None = None
    ) -> tuple[Job, ...]: ...
```

Видаліть константу `DECISION_LIST_LIMIT` і її коментар; її замінює `page_size`.

**`create_app`.** Видаліть параметр `workflow: AnswerWorkflow | None = None,`. Створіть застосунок без перенаправлень зі скісною рискою (блок 5):

```python
    # No redirect from `/v1/x/` to `/v1/x`: a redirect answers before identity.
    app = FastAPI(title="knowledge-service", redirect_slashes=False)
```

Додайте обробник невідомих шляхів і методів просто перед обробником `RequestValidationError`:

```python
    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        # An unknown path or method: the same error shape as every other.
        code, message = HTTP_ERRORS.get(
            error.status_code, ("http_error", "The request cannot be served")
        )
        response = error_response(
            status_code=error.status_code,
            code=code,
            message=message,
            request_id=request_id_from(request),
        )
        response.headers.update(error.headers or {})
        return response
```

**Маршрут Question.** Видаліть усю функцію `@app.post("/v1/answer", response_model=Answer)`. Замініть декоратор і назву потокового маршруту:

```python
    @app.post(
        "/v1/questions",
        responses={
            200: {
                "description": "The Answer as Server-Sent Events",
                "content": {"text/event-stream": {}},
            },
            **error_responses(413, 429),
        },
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": Question.model_json_schema()}
                },
            }
        },
    )
    async def ask_question(request: Request) -> Response:
```

`openapi_extra` додає тіло запиту в документ, бо маршрут читає тіло сам, після ролі, точно як параметри сторінки. Відповідь 200 — `text/event-stream`, Server-Sent Events з M06.

Усередині помилка для відсутнього streamer отримує новий код:

```python
        if streamer is None:
            return error_response(
                status_code=503,
                code="answering_unavailable",
                message="Answering is unavailable",
                request_id=request_id,
            )
```

**Інші маршрути, що обслуговуються,** отримують `error_responses(...)` зі своїми додатковими статусами і `Identifier` для ID у шляху:

```python
    @app.delete(
        "/v1/conversations/{conversation_id}",
        status_code=204,
        responses=error_responses(),
    )
    async def delete_own_conversation(
        request: Request, conversation_id: Identifier
    ) -> Response:
```

```python
    @app.get(
        "/v1/jobs/{job_id}",
        response_model=SynchronizationJobReport,
        responses=error_responses(404),
    )
    async def read_synchronization_job(
        request: Request, job_id: Identifier
    ) -> SynchronizationJobReport | JSONResponse:
```

```python
    @app.post(
        "/v1/jobs/{job_id}/retry",
        response_model=OperatorJob,
        responses=error_responses(404, 409),
    )
    async def retry_failed_job(
        request: Request, job_id: Identifier
    ) -> OperatorJob | JSONResponse:
        return await decide(request, job_id, retry=True)

    @app.post(
        "/v1/jobs/{job_id}/cancel",
        response_model=OperatorJob,
        responses=error_responses(404, 409),
    )
    async def cancel_unfinished_job(
        request: Request, job_id: Identifier
    ) -> OperatorJob | JSONResponse:
        return await decide(request, job_id, retry=False)
```

**Список рішень.** Замініть маршрут:

```python
    @app.get(
        "/v1/job-decisions",
        response_model=JobsNeedingDecision,
        responses=error_responses(),
        openapi_extra={"parameters": PAGE_PARAMETERS},
    )
    async def list_jobs_needing_decision(
        request: Request,
    ) -> JobsNeedingDecision | JSONResponse:
        caller = await authenticate(request)
        require(caller.roles, Operation.RECOVER_JOBS)
        request_id = request_id_from(request)
        try:
            limit, cursor = page_request(request)
            after = None if cursor is None else decision_position(cursor)
        except InvalidPage:
            return failure_response(kind=FailureKind.VALIDATION, request_id=request_id)
        if job_decisions is None:
            return _jobs_unavailable(request_id)
        try:
            # One more than the page, to learn whether another page follows.
            jobs = await job_decisions.needing_decision(limit=limit + 1, after=after)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error("job decision list failed: %s", type(error).__name__)
            return failure_response(kind=classify_failure(error), request_id=request_id)
        page = jobs[:limit]
        now = datetime.now(UTC)
        return JobsNeedingDecision(
            items=[operator_job(job, now=now) for job in page],
            next_cursor=decision_cursor(page[-1]) if len(jobs) > limit else None,
        )
```

Читайте його як блок 3 у коді. Параметри сторінки читаються після `require`, тож викликач без ролі отримує 403, хоч би що надіслав. Поганий `limit` чи курсор — це 422 ще до звернення до сховища. Сховище просять про `limit + 1` Jobs, і зайвий лише вирішує, яким буде `next_cursor`.

**Допоміжні функції модуля**, перед `_jobs_unavailable`:

```python
# What each error status means; every `/v1` route can answer the first six.
ERROR_STATUSES: dict[int, str] = {
    400: "The Authorization header is malformed",
    401: "No valid access token",
    403: "No role of the caller permits this operation",
    422: "The request is not valid",
    500: "An internal error",
    503: "A service it needs is unavailable",
    404: "Nothing with this ID",
    409: "The request does not fit the resource's current state",
    413: "The request body is too large",
    429: "Too many requests, or the service is at capacity",
}
COMMON_ERRORS = (400, 401, 403, 422, 500, 503)
# The error code and message for a path or method the API does not have.
HTTP_ERRORS: dict[int, tuple[str, str]] = {
    404: ("not_found", "No such resource"),
    405: ("method_not_allowed", "This resource does not allow this method"),
}

# `limit` and `cursor`, described by hand: the route reads them itself, after
# the caller's role, so a bad one never answers 422 before the 401 or 403.
PAGE_PARAMETERS: list[dict[str, Any]] = [
    {
        "name": "limit",
        "in": "query",
        "required": False,
        "description": "How many items to return",
        "schema": {
            "type": "integer",
            "minimum": 1,
            "maximum": MAX_PAGE_SIZE,
            "default": DEFAULT_PAGE_SIZE,
        },
    },
    {
        "name": "cursor",
        "in": "query",
        "required": False,
        "description": "`next_cursor` of the page before; absent for the first page",
        "schema": {"type": "string", "maxLength": MAX_CURSOR_CHARACTERS},
    },
]


def error_responses(*extra: int) -> dict[int | str, dict[str, Any]]:
    """OpenAPI responses for every error status a route may answer."""
    return {
        status: {"model": ErrorResponse, "description": ERROR_STATUSES[status]}
        for status in (*COMMON_ERRORS, *extra)
    }


def page_request(request: Request) -> tuple[int, str | None]:
    """`limit` and `cursor`, each at most once and nothing else, else `InvalidPage`.

    A misspelled parameter, such as `Limit`, is refused rather than ignored.
    """
    if set(request.query_params) - {"limit", "cursor"}:
        raise InvalidPage("only limit and cursor are accepted")
    for name in ("limit", "cursor"):
        if len(request.query_params.getlist(name)) > 1:
            raise InvalidPage(f"{name} is given more than once")
    return (
        page_size(request.query_params.get("limit")),
        request.query_params.get("cursor"),
    )
```

`error_responses(404, 409)` повертає спільні шість плюс 404 і 409. Оскільки 422 серед шести, FastAPI не додає власну `HTTPValidationError`.

Перевірка: `uv run pyright src` повідомляє 0 помилок, а `uv run python -c "from knowledge_service.app import create_app; print(sorted(create_app().openapi()['paths']))"` показує `/v1/questions` і жодного `/v1/answer`.

## Крок 7 — Оновити наявні тести

Крок із кодом.

Простими словами: тести, що викликали старі маршрути, тепер викликають новий, тести прибраного маршруту зникають, а тест ролей бере маршрути з таблиці. Після цього кроку наявний набір тестів знову проходить.

**Перейменуйте потоковий шлях** у кожному файлі тестів. На macOS:

```bash
grep -rl "/v1/answer/stream" tests | xargs sed -i '' 's#/v1/answer/stream#/v1/questions#g'
```

На Linux пишіть `sed -i` без порожніх `''`. Це змінює 11 файлів; перевірте через `git diff --stat tests`.

**`tests/test_app.py`** залишає лише три тести стану (health). Видаліть усе від `test_answer_endpoint_returns_answer` до кінця і залиште такі імпорти:

```python
"""Tests FastAPI application."""

import httpx
from fastapi import FastAPI

from identity_fakes import AUTHORIZATION, TRUSTED
from knowledge_service.app import create_app


def get_client(app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers=AUTHORIZATION,
    )
```

**`tests/test_stream_failures.py`**: видаліть клас `FailingWorkflow` і тест `test_the_answer_endpoint_gives_the_same_retry_guidance`; ту саму пораду щодо повтору покривають власні тести збоїв потоку. Потім `uv run ruff check --fix tests/test_stream_failures.py` прибере імпорти, що стали непотрібні.

**`tests/test_streaming.py`**: у параметризованому тесті, що чекає `workflow_unavailable`, напишіть `answering_unavailable`.

**`tests/test_app_authentication.py`**, тест ролей з M07-T05:

1. Видаліть класи `Workflow` і `Counting` та імпорт `AnswerWorkflow`. Додайте `from knowledge_service.api_routes import served_routes`.
2. У `test_identity_is_decided_before_the_body_is_parsed` надсилайте лише на `"/v1/questions"` замість циклу по двох шляхах.
3. Замініть `test_the_ungrounded_endpoint_needs_a_token_too` на тест, що старих маршрутів більше немає:

```python
@pytest.mark.parametrize("path", ["/v1/answer", "/v1/answer/stream"])
async def test_the_old_answer_routes_are_gone(path: str) -> None:
    source = Source()

    async with client(app_with(source)) as http:
        response = await http.post(
            path,
            json={"text": QUESTION.text},
            headers={"Authorization": "Bearer alice-token"},
        )

    assert response.status_code == 404
    assert source.readers == []
```

4. У `test_a_caller_without_the_user_role_gets_403_and_nothing_runs` і `test_an_app_without_an_authenticator_answers_nobody` приберіть параметризацію `path` і workflow; надсилайте на `"/v1/questions"` і перевіряйте `source.readers == []`.
5. Збудуйте `ROUTES` з таблиці:

```python
# Every route the app serves and the operation it needs: the served rows of
# the route table, and the public routes outside `/v1`, marked None. A new
# route fails the first test below until the table lists it.
ROUTES: dict[tuple[str, str], Operation | None] = {
    ("GET", "/health/live"): None,
    ("GET", "/health/ready"): None,
    ("GET", "/openapi.json"): None,
    ("GET", "/docs"): None,
    ("GET", "/docs/oauth2-redirect"): None,
    ("GET", "/redoc"): None,
    **{(route.method.value, route.path): route.operation for route in served_routes()},
}
```

6. Приберіть workflow з `full_app` і з тесту ролей:

```python
def full_app(
    source: Source,
    tokens: dict[str, Any] | None = None,
    conversations: Deleting | None = None,
    jobs: Reading | None = None,
    decisions: Deciding | None = None,
) -> Any:
    """The app with every endpoint wired, so every route is real."""
    return create_app(
        streamer=streamer(Model(), source=source),
        authenticator=Authenticator(Verifier(tokens or TOKENS), POLICY),
        conversations=conversations or Deleting(),
        job_reports=jobs or Reading(),
        job_decisions=decisions or Deciding(),
    )
```

```python
    source, conversations = Source(), Deleting()
    jobs, decisions = Reading(), Deciding()
    token = verified(realm_access={"roles": sorted(roles)})
    app = full_app(source, {"token": token}, conversations, jobs, decisions)
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
    reached = source.readers or conversations.calls or jobs.calls or decisions.calls
    assert bool(reached) is allowed
```

**`tests/test_job_recovery_integration.py`**: у `test_the_decision_list_shows_only_failures_nothing_replaced` читайте `response.json()["items"]` замість `["jobs"]`.

Перевірка: `uv run pytest tests/test_app.py tests/test_app_authentication.py tests/test_stream_failures.py tests/test_streaming.py -q` дає `244 passed`.

## Крок 8 — Додати тести дизайну

Крок із кодом.

Простими словами: дизайн доводять три набори тестів. Перший перевіряє правила таблиці по одному, те, що застосунок обслуговує точно рядки, які обслуговуються, і що в документі OpenAPI одна форма помилки, ID у вигляді UUID, одна форма сторінки і жодних слів провайдера. Другий атакує курсор ворожими даними і проходить список рішень сторінка за сторінкою. Третій проходить справжні рядки PostgreSQL, зокрема Jobs, що впали в ту саму мікросекунду.

Створіть `tests/test_api_routes.py`:

| Тест | Що він доводить |
|---|---|
| `test_the_route_table_follows_every_rule` | 24 рядки не порушують жодного правила |
| `test_the_table_covers_every_resource_the_task_names` | кожна область завдання має маршрут |
| `test_each_rule_names_the_route_that_breaks_it` | кожен із 20 поганих маршрутів порушує рівно одне очікуване правило |
| `test_a_route_listed_twice_is_a_problem_both_times` | дублікати названо |
| `test_a_single_resource_at_the_end_of_a_get_is_not_a_list`, `test_a_plural_in_ies_or_with_a_dash_names_its_identifier` | добрі маршрути приймаються |
| `test_the_app_serves_exactly_the_served_rows_under_v1` | застосунок і таблиця збігаються |
| `test_every_error_a_route_documents_has_the_one_error_shape` | кожен маршрут описує точно свої статуси, усі як `ErrorResponse` |
| `test_no_route_documents_the_framework_validation_error` | жодної `HTTPValidationError` |
| `test_every_path_identifier_is_a_uuid_named_after_its_resource`, `test_every_id_field_of_every_schema_is_a_uuid` | узгоджені ID |
| `test_every_served_list_takes_limit_and_cursor_and_answers_one_page_shape` | узгоджена пагінація |
| `test_the_question_route_documents_its_body`, `test_no_schema_is_shaped_like_a_model_provider_protocol` | жодних моделей у формі провайдера |
| `test_an_unknown_path_or_method_answers_with_the_one_error_shape` | 404, 405 і скісні риски наприкінці |

```python
"""The native API's route table, and the OpenAPI document of the app it describes.

The table is the design: every route served or planned, who may call it, and
whether it pages. These tests check that the table follows its own rules,
that the app serves exactly its served rows, and that the OpenAPI document
gives every route one error shape, one page shape, and IDs named after their
resource, with nothing shaped like a model provider's protocol.
"""

from dataclasses import replace
from typing import Any

import httpx
import pytest

from knowledge_service.api_routes import (
    ROUTES,
    Method,
    Route,
    RouteStatus,
    route_problems,
    served_routes,
)
from knowledge_service.app import COMMON_ERRORS, create_app
from knowledge_service.roles import Operation

SPEC: dict[str, Any] = create_app().openapi()
SCHEMAS: dict[str, Any] = SPEC["components"]["schemas"]
V1_OPERATIONS = [
    (method.upper(), path, operation)
    for path, item in SPEC["paths"].items()
    if path.startswith("/v1/")
    for method, operation in item.items()
]
# Field names of the OpenAI Chat Completions protocol; the native API has its
# own words, and the compatible adapter (M09-T05) translates.
PROVIDER_FIELDS = frozenset(
    {
        "choices",
        "messages",
        "finish_reason",
        "object",
        "delta",
        "logprobs",
        "role",
        "content",
        "model",
        "id",
    }
)
# Every error status each served route documents, beyond the common six.
ROUTE_ERRORS = {
    ("POST", "/v1/questions"): {413, 429},
    ("DELETE", "/v1/conversations/{conversation_id}"): set[int](),
    ("GET", "/v1/jobs/{job_id}"): {404},
    ("GET", "/v1/job-decisions"): set[int](),
    ("POST", "/v1/jobs/{job_id}/retry"): {404, 409},
    ("POST", "/v1/jobs/{job_id}/cancel"): {404, 409},
}
GOOD = Route(
    Method.GET,
    "/v1/sources",
    Operation.CONFIGURE_SOURCES,
    RouteStatus.PLANNED,
    "M09-T02",
    "List Sources",
    paginated=True,
)


def test_the_route_table_follows_every_rule() -> None:
    assert route_problems(ROUTES) == ()


def test_the_table_covers_every_resource_the_task_names() -> None:
    paths = {route.path for route in ROUTES}

    for resource in (
        "/v1/questions",
        "/v1/conversations",
        "/feedback",
        "/v1/sources",
        "/synchronizations",
        "/v1/evaluation-runs",
        "/diagnostics",
    ):
        assert any(resource in path for path in paths), resource


@pytest.mark.parametrize(
    ("change", "rule"),
    [
        ({"path": "/sources"}, "not under /v1"),
        ({"path": "/v2/sources"}, "not under /v1"),
        (
            {"path": "/v1/Sources", "paginated": False},
            "'Sources' is not lowercase words joined by '-'",
        ),
        (
            {"path": "/v1/source_list", "paginated": False},
            "'source_list' is not lowercase words joined by '-'",
        ),
        (
            {"path": "/v1/sources/", "paginated": False},
            "'' is not lowercase words joined by '-'",
        ),
        (
            {"path": "/v1/sources/{id}", "paginated": False},
            "{id} does not name the list before it",
        ),
        (
            {"path": "/v1/{source_id}", "paginated": False},
            "{source_id} does not name the list before it",
        ),
        (
            {"path": "/v1/sources/{source_id}/{job_id}", "paginated": False},
            "{job_id} does not name the list before it",
        ),
        ({"paginated": False}, "a GET of a list that is not paginated"),
        ({"method": Method.POST}, "paginated, but not a GET of a list"),
        (
            {"path": "/v1/sources/{source_id}", "method": Method.GET},
            "paginated, but not a GET of a list",
        ),
        ({"status": RouteStatus.SERVED, "task": None}, "served without a task"),
        ({"task": "T02"}, "a task that is not a task ID"),
        ({"operation": "configure_sources"}, "no Operation"),
        ({"status": "served", "task": None}, "no RouteStatus"),
        (
            {"path": "/v1/source", "paginated": False},
            "'source' is not a list, a single resource, or an action",
        ),
        (
            {
                "path": "/v1/sources/{source_id}/getsourcenow",
                "method": Method.POST,
                "paginated": False,
            },
            "'getsourcenow' is not a list, a single resource, or an action",
        ),
        (
            {"path": "/v1/sources/{source_id}/sources/{source_id}", "paginated": False},
            "{source_id} appears twice",
        ),
        (
            {"path": "/v1/feedback", "paginated": False},
            "'feedback' must end the path, after an ID",
        ),
        (
            {"path": "/v1/sources/{source_id}/retry", "paginated": False},
            "'retry' is an action, so only POST",
        ),
    ],
)
def test_each_rule_names_the_route_that_breaks_it(
    change: dict[str, Any], rule: str
) -> None:
    bad = replace(GOOD, **change)

    assert [problem.rule for problem in route_problems([bad])] == [rule]


def test_a_route_listed_twice_is_a_problem_both_times() -> None:
    problems = route_problems([GOOD, replace(GOOD, summary="Again")])

    assert [problem.rule for problem in problems] == [
        "the same method and path twice"
    ] * 2


def test_a_single_resource_at_the_end_of_a_get_is_not_a_list() -> None:
    diagnostics = replace(
        GOOD,
        path="/v1/conversations/{conversation_id}/messages/{message_id}/diagnostics",
        paginated=False,
    )

    assert route_problems([diagnostics]) == ()


@pytest.mark.parametrize(
    "path", ["/v1/entries/{entry_id}", "/v1/job-decisions/{job_decision_id}"]
)
def test_a_plural_in_ies_or_with_a_dash_names_its_identifier(path: str) -> None:
    assert route_problems([replace(GOOD, path=path, paginated=False)]) == ()


def test_the_app_serves_exactly_the_served_rows_under_v1() -> None:
    served: set[tuple[str, str]] = set()
    for route in create_app().routes:
        path: str = getattr(route, "path", "")
        methods: set[str] = getattr(route, "methods", None) or set()
        if path.startswith("/v1/"):
            served |= {(method, path) for method in methods}

    assert served == {(route.method.value, route.path) for route in served_routes()}


@pytest.mark.parametrize(
    ("method", "path", "operation"),
    V1_OPERATIONS,
    ids=[f"{method} {path}" for method, path, _ in V1_OPERATIONS],
)
def test_every_error_a_route_documents_has_the_one_error_shape(
    method: str, path: str, operation: dict[str, Any]
) -> None:
    errors = {
        status: response
        for status, response in operation["responses"].items()
        if status.startswith(("4", "5"))
    }

    expected = {*COMMON_ERRORS, *ROUTE_ERRORS[method, path]}
    assert set(errors) == {str(status) for status in expected}
    for response in errors.values():
        assert response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


def test_no_route_documents_the_framework_validation_error() -> None:
    assert "HTTPValidationError" not in SCHEMAS


def test_every_path_identifier_is_a_uuid_named_after_its_resource() -> None:
    for _method, path, operation in V1_OPERATIONS:
        for parameter in operation.get("parameters", []):
            if parameter["in"] == "path":
                assert parameter["name"].endswith("_id"), path
                assert parameter["schema"]["format"] == "uuid", path


def test_every_id_field_of_every_schema_is_a_uuid() -> None:
    for name, schema in SCHEMAS.items():
        for field, value in schema.get("properties", {}).items():
            if field == "id" or field.endswith("_id"):
                formats = [value, *value.get("anyOf", [])]
                assert any(f.get("format") == "uuid" for f in formats), (name, field)


@pytest.mark.parametrize(
    "route",
    [route for route in served_routes() if route.paginated],
    ids=lambda route: route.path,
)
def test_every_served_list_takes_limit_and_cursor_and_answers_one_page_shape(
    route: Route,
) -> None:
    operation = SPEC["paths"][route.path][route.method.value.lower()]
    query = {p["name"]: p for p in operation["parameters"] if p["in"] == "query"}
    reference = operation["responses"]["200"]["content"]["application/json"]["schema"]
    page = SCHEMAS[reference["$ref"].rsplit("/", 1)[1]]

    assert set(query) == {"limit", "cursor"}
    assert (
        query["limit"]["schema"]["minimum"],
        query["limit"]["schema"]["maximum"],
    ) == (
        1,
        100,
    )
    assert set(page["properties"]) == {"items", "next_cursor"}
    assert set(page["required"]) == {"items", "next_cursor"}
    assert page["properties"]["items"]["type"] == "array"


def test_the_question_route_documents_its_body() -> None:
    body = SPEC["paths"]["/v1/questions"]["post"]["requestBody"]

    schema = body["content"]["application/json"]["schema"]
    assert (body["required"], set(schema["properties"])) == (True, {"text"})


def test_no_schema_is_shaped_like_a_model_provider_protocol() -> None:
    question = SPEC["paths"]["/v1/questions"]["post"]["requestBody"]
    schemas = {
        **SCHEMAS,
        "Question": question["content"]["application/json"]["schema"],
    }
    for name, schema in schemas.items():
        assert not PROVIDER_FIELDS & set(schema.get("properties", {})), name
        assert not any(
            word in name.lower() for word in ("openai", "chat", "completion")
        ), name


async def call(method: str, path: str) -> httpx.Response:
    """A request with no token to the app with nothing wired."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://t"
    ) as http:
        return await http.request(method, path)


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("POST", "/v1/answer", 404, "not_found"),
        ("GET", "/v1/nothing-here", 404, "not_found"),
        ("GET", "/v1/questions", 405, "method_not_allowed"),
        ("PUT", "/v1/job-decisions", 405, "method_not_allowed"),
        ("GET", "/v1/job-decisions/", 404, "not_found"),
        ("POST", "/v1/questions/", 404, "not_found"),
    ],
)
async def test_an_unknown_path_or_method_answers_with_the_one_error_shape(
    method: str, path: str, status: int, code: str
) -> None:
    response = await call(method, path)

    assert response.status_code == status
    assert set(response.json()) == {"code", "message", "request_id"}
    assert response.json()["code"] == code
```

Створіть `tests/test_pagination.py`. Hypothesis, бібліотека тестування властивостей, яка вже є в проєкті, генерує тисячі рядків і рядків байтів; хоч би що вона надіслала, декодування має дати позицію або `InvalidPage`, і ніколи іншого винятку. HTTP-тести використовують фальшиве сховище, що видає сторінки так само, як SQL.

```python
"""Page sizes and cursors: only what the API wrote comes back in.

A cursor travels through the client, so it is untrusted input. Each test
hands the decoder something `encode_cursor` would never write and expects
`InvalidPage`, never another exception or a position.
"""

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.app import JobDecisions, create_app
from knowledge_service.identifiers import JobId
from knowledge_service.identity.context import Caller
from knowledge_service.job_decisions import decision_cursor, decision_position
from knowledge_service.jobs import Job, JobKind
from knowledge_service.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_CURSOR_CHARACTERS,
    InvalidPage,
    decode_cursor,
    encode_cursor,
    page_size,
)
from knowledge_service.roles import ApplicationRole

KEYS = frozenset({"finished_at", "job_id"})
LIST = "job-decisions"
FINISHED_AT = datetime(2026, 11, 24, 9, 30, 15, 123456, tzinfo=UTC)


def raw_cursor(document: Any) -> str:
    """A cursor holding any JSON, encoded the way `encode_cursor` encodes."""
    text = json.dumps(document)
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def raw_text(text: str) -> str:
    """A cursor holding exactly `text`."""
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def failed_job(finished_at: datetime = FINISHED_AT) -> Job:
    queued = Job.request(
        job_id=JobId.new(),
        kind=JobKind.SYNCHRONIZE_SOURCE,
        target_id=uuid4(),
        queued_at=finished_at - timedelta(minutes=2),
    )
    started = queued.start(started_at=finished_at - timedelta(minutes=1))
    return started.fail(error_code="database_rejected", finished_at=finished_at)


@pytest.mark.parametrize(
    ("raw", "size"), [(None, DEFAULT_PAGE_SIZE), ("1", 1), ("100", 100)]
)
def test_a_page_size_is_a_whole_number_from_1_to_100(
    raw: str | None, size: int
) -> None:
    assert page_size(raw) == size


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "0",
        "00",
        "007",
        "101",
        "-1",
        "+5",
        " 5",
        "5 ",
        "5.0",
        "1e2",
        "0x10",
        "\u0665",
        "1000",
    ],
)
def test_any_other_page_size_is_refused(raw: str) -> None:
    with pytest.raises(InvalidPage):
        page_size(raw)


def test_a_cursor_gives_back_the_position_it_was_made_from() -> None:
    position = {"finished_at": "2026-11-24T09:30:15+00:00", "job_id": str(uuid4())}

    assert (
        decode_cursor(encode_cursor(LIST, position), listing=LIST, keys=KEYS)
        == position
    )


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "a" * (MAX_CURSOR_CHARACTERS + 1),
        raw_cursor([1, LIST, {"finished_at": "x", "job_id": "y"}]) + "=",
        "not+base64/",
        "%%%",
        "A",
        base64.urlsafe_b64encode(b"\xff\xfe").decode(),
        raw_cursor("just text"),
        raw_cursor([2, LIST, {"finished_at": "x", "job_id": "y"}]),
        raw_cursor([True, LIST, {"finished_at": "x", "job_id": "y"}]),
        raw_cursor([1.0, LIST, {"finished_at": "x", "job_id": "y"}]),
        raw_cursor([1, LIST, {"finished_at": "x"}]),
        raw_cursor([1, LIST, {"finished_at": "x", "job_id": "y", "more": "z"}]),
        raw_cursor([1, LIST, {"finished_at": 5, "job_id": "y"}]),
        raw_cursor([1, LIST, ["finished_at", "job_id"]]),
        raw_cursor([1, LIST, {"finished_at": "x", "job_id": "y"}, "extra"]),
        raw_cursor([[[[[[[[[[1]]]]]]]]]]),
        raw_cursor([1, "sources", {"finished_at": "x", "job_id": "y"}]),
        raw_cursor([1, LIST, {"job_id": "y", "finished_at": "x"}]),
        raw_cursor([1, LIST, {"finished_at": "x", "job_id": "y"}]),
        '[1,"job-decisions",{"finished_at":"x","finished_at":"x","job_id":"y"}]',
    ],
)
def test_a_cursor_the_api_did_not_write_is_refused(cursor: str) -> None:
    if cursor.startswith("["):
        cursor = raw_text(cursor)
    with pytest.raises(InvalidPage):
        decode_cursor(cursor, listing=LIST, keys=KEYS)


@given(st.text(max_size=600))
def test_any_text_as_a_cursor_is_a_position_or_invalid_page(cursor: str) -> None:
    try:
        position = decode_cursor(cursor, listing=LIST, keys=KEYS)
    except InvalidPage:
        return
    assert set(position) == KEYS


@given(st.binary(max_size=400))
def test_any_bytes_behind_a_cursor_are_a_position_or_invalid_page(raw: bytes) -> None:
    cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    try:
        decision_position(cursor)
    except InvalidPage:
        return


def test_spare_base64_bits_do_not_make_a_second_spelling() -> None:
    cursor = encode_cursor(LIST, {"finished_at": "x", "job_id": "y"})
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    others = [cursor[:-1] + letter for letter in alphabet if letter != cursor[-1]]

    accepted: list[str] = []
    for other in others:
        try:
            decode_cursor(other, listing=LIST, keys=KEYS)
        except InvalidPage:
            continue
        accepted.append(other)
    assert accepted == []


def test_a_decision_cursor_starts_after_its_job() -> None:
    job = failed_job()

    assert decision_position(decision_cursor(job)) == (FINISHED_AT, job.job_id)


@pytest.mark.parametrize(
    "position",
    [
        {"finished_at": "2026-11-24T10:30:15.123456+01:00"},
        {"finished_at": "2026-11-24T09:30:15.123456"},
        {"finished_at": "2026-11-24T09:30:15.123456Z"},
        {"finished_at": "2026-11-24 09:30:15.123456+00:00"},
        {"finished_at": "yesterday"},
        {"job_id": "{00000000-0000-0000-0000-000000000001}"},
        {"job_id": "00000000-0000-0000-0000-00000000000A"},
        {"job_id": "000000000000000000000000000000001"},
        {"job_id": "not an id"},
    ],
)
def test_a_decision_cursor_in_another_spelling_is_refused(
    position: dict[str, str],
) -> None:
    valid = {
        "finished_at": FINISHED_AT.isoformat(),
        "job_id": "00000000-0000-0000-0000-00000000000a",
    }

    with pytest.raises(InvalidPage):
        decision_position(encode_cursor("job-decisions", {**valid, **position}))


# The decision list over HTTP, with a fake store.


class Decisions:
    """Holds failed Jobs newest first, and records what each list asked for."""

    def __init__(self, jobs: list[Job]) -> None:
        self.jobs = jobs
        self.calls: list[dict[str, Any]] = []

    async def needing_decision(
        self, *, limit: int, after: tuple[datetime, JobId] | None = None
    ) -> tuple[Job, ...]:
        self.calls.append({"limit": limit, "after": after})
        start = 0
        if after is not None:
            keys = [(job.finished_at, job.job_id.value) for job in self.jobs]
            start = next(
                (i for i, key in enumerate(keys) if key < (after[0], after[1].value)),
                len(keys),
            )
        return tuple(self.jobs[start : start + limit])


class Administrator:
    """Gives every request an administrator's caller."""

    async def authenticate(self, headers: list[str]) -> Caller:
        return Caller(reader=READER, roles=frozenset({ApplicationRole.ADMINISTRATOR}))


async def list_decisions(decisions: Decisions, query: str) -> httpx.Response:
    app = create_app(
        authenticator=Administrator(), job_decisions=cast(JobDecisions, decisions)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.get(
            f"/v1/job-decisions{query}", headers={"Authorization": "Bearer t"}
        )


async def test_pages_of_decisions_follow_each_other_to_the_end() -> None:
    jobs = sorted(
        (failed_job(FINISHED_AT - timedelta(minutes=n // 2)) for n in range(5)),
        key=lambda job: (job.finished_at, job.job_id.value),
        reverse=True,
    )
    decisions = Decisions(jobs)
    seen: list[str] = []
    query = "?limit=2"

    for _ in range(10):
        page = (await list_decisions(decisions, query)).json()
        seen += [item["job_id"] for item in page["items"]]
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    assert seen == [str(job.job_id.value) for job in jobs]
    assert [call["limit"] for call in decisions.calls] == [3, 3, 3]


async def test_a_full_last_page_says_no_page_follows() -> None:
    jobs = sorted(
        (failed_job(FINISHED_AT - timedelta(minutes=n)) for n in range(4)),
        key=lambda job: (job.finished_at, job.job_id.value),
        reverse=True,
    )
    decisions = Decisions(jobs)

    first = (await list_decisions(decisions, "?limit=2")).json()
    cursor = first["next_cursor"]
    second = (await list_decisions(decisions, f"?limit=2&cursor={cursor}")).json()

    assert len(second["items"]) == 2
    assert second["next_cursor"] is None


async def test_an_empty_list_has_no_next_page() -> None:
    page = (await list_decisions(Decisions([]), "")).json()

    assert page == {"items": [], "next_cursor": None}


@pytest.mark.parametrize(
    "query",
    [
        "?limit=0",
        "?limit=101",
        "?limit=x",
        "?limit=1&limit=2",
        "?cursor=%%",
        "?cursor=a&cursor=b",
        "?Limit=5",
        "?offset=10",
    ],
)
async def test_a_bad_limit_or_cursor_is_refused_before_the_store_is_asked(
    query: str,
) -> None:
    decisions = Decisions([failed_job()])

    response = await list_decisions(decisions, query)

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert decisions.calls == []


@pytest.mark.parametrize("query", ["?limit=0", "?cursor=%%", "?Limit=5"])
@pytest.mark.parametrize(
    ("headers", "status"),
    [({}, 401), (AUTHORIZATION, 403)],
    ids=["no token", "user role"],
)
async def test_the_role_is_checked_before_limit_or_cursor_is_read(
    query: str, headers: dict[str, str], status: int
) -> None:
    decisions = Decisions([failed_job()])
    app = create_app(authenticator=TRUSTED, job_decisions=cast(JobDecisions, decisions))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.get(f"/v1/job-decisions{query}", headers=headers)

    assert response.status_code == status
    assert decisions.calls == []


async def test_a_cursor_for_one_job_id_is_read_as_that_id() -> None:
    job = failed_job()
    decisions = Decisions([job])

    await list_decisions(decisions, f"?cursor={decision_cursor(job)}")

    assert decisions.calls[0]["after"] == (FINISHED_AT, job.job_id)
    assert isinstance(decisions.calls[0]["after"][1].value, UUID)
```

Допишіть тест на PostgreSQL у `tests/test_job_recovery_integration.py`, перед коментарем `# The relay and the migration.`:

```python
async def failed_purge_at(runtime: DatabaseRuntime, finished_at: datetime) -> Job:
    """A purge of a Document of its own, failed for good at `finished_at`."""
    async with runtime.transaction() as connection:
        queued = await enqueue_job(
            connection,
            Job.request(
                job_id=JobId.new(),
                kind=JobKind.PURGE_DOCUMENT_CHUNKS,
                target_id=uuid.uuid4(),
                queued_at=OBSERVED_AT,
            ),
        )
        failed = queued.job.start(started_at=STARTED_AT).fail(
            error_code="database_rejected", finished_at=finished_at
        )
        await save_job(connection, job=failed)
    return failed


async def test_pages_of_decisions_never_repeat_or_skip_a_job(
    runtime: DatabaseRuntime,
) -> None:
    # Three Jobs fail in the same microsecond, so only their IDs order them.
    for minutes in (1, 2, 2, 2, 3):
        await failed_purge_at(runtime, STARTED_AT + timedelta(minutes=minutes))
    expected = [
        str(job.job_id.value) for job in await load_jobs_needing_decision(runtime)
    ]
    seen: list[str] = []
    query = "?limit=2"

    for _ in range(10):
        response = await over_http(runtime, "GET", f"/v1/job-decisions{query}")
        page = response.json()
        if not seen:
            # A Job that fails while the operator reads goes before the first
            # page, so it moves none of the pages after it.
            await failed_purge_at(runtime, STARTED_AT + timedelta(minutes=10))
        seen += [item["job_id"] for item in page["items"]]
        if page["next_cursor"] is None:
            break
        query = f"?limit=2&cursor={page['next_cursor']}"
    else:
        pytest.fail("the pages never ended")

    assert seen == expected
    assert len(set(seen)) == 5
```

Кожне очищення має власну ціль, тож жоден Job не замінює іншого. Три Jobs падають у ту саму хвилину, тож упорядковує їх лише `job_id`. Після першої сторінки падає новіший Job, і сторінки після нього не мають зсунутися.

Перевірка:

```bash
uv run pytest tests/test_api_routes.py tests/test_pagination.py -q
uv run pytest tests/test_job_recovery_integration.py -q -m integration
```

Очікувано: `45 passed` і `70 passed` для перших двох файлів і `31 passed` для інтеграційного.

## Крок 9 — Зламати навмисно

Без коду в цьому кроці; ви робите одну тимчасову зміну і скасовуєте її.

Тест, який ви жодного разу не бачили червоним, мало що доводить. Цей крок показує, як тест повної останньої сторінки ловить помилку на одиницю, знайдену рев'ю.

У `list_jobs_needing_decision` змініть `if len(jobs) > limit else None` на `if len(jobs) >= limit else None`. Запустіть із вимкненим кешем байткоду, бо правка того самого розміру протягом однієї секунди може взяти старий скомпільований файл:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_pagination.py -q
```

Очікувано: `1 failed, 69 passed`, і впав `test_a_full_last_page_says_no_page_follows`: його друга сторінка з 2 Jobs досі має `next_cursor`. Скасуйте зміну і запустіть знову: `70 passed`.

Що спростувало б дизайн: сторінка, що повторює чи пропускає Job, поки список змінюється; курсор, прийнятий для іншого списку чи в іншому написанні; маршрут під `/v1`, якого немає в таблиці, або який відповідає до перевірки ролі, або в іншій формі помилки; нативна схема з полем провайдера.

## Крок 10 — Оновити документи

Без коду в цьому кроці.

Змінюються лише два документи, що описують маршрути.

У `docs/architecture.md`:

- у рядку `answering` таблиці структури коду `behind POST /v1/answer/stream` стає `behind POST /v1/questions`;
- у розділі «Answering» «The streamed path, `POST /v1/answer/stream`» стає `POST /v1/questions`;
- «Both answer endpoints `require` ...» стає: «`POST /v1/questions`, the one Question route, requires the `ask_questions` operation before it reads the body, and refuses with 403 otherwise.»;
- в абзаці про маршрути рішень «before the ID is parsed. They list the Jobs that failed for good and that no newer Job replaced,» стає «before the ID, `limit`, or `cursor` is read. They list the Jobs that failed for good and that no newer Job replaced, a page at a time,»;
- перед абзацом, що починається з `` `job_dispatch` ``, додайте:

```markdown
`api_routes` is the native API's design. `ROUTES` lists every route the
service serves or plans under `/v1`, each with the Operation an
Application Role must permit, the task that builds it, and whether it
pages; the app serves exactly its served rows. `route_problems` checks the
rules: path words are lowercase and joined by `-`, each `{x_id}` follows
the list it names (`/v1/sources/{source_id}`), and every GET of a list
pages. A paginated route takes `limit` (1 to 100, 50 by default) and
`cursor`, both read after the role check, refuses any other query
parameter, and answers
`{"items": [...], "next_cursor": ...}`; `pagination` writes the cursor,
which names the last item of the page, so a page never repeats or skips
an item added in between, and checks every field of one that comes back.
Every `/v1` route documents its errors in OpenAPI with the one
`ErrorResponse` shape. A provider's protocol is an adapter's business: no
native route or schema uses its field names (ADR 0004).
```

У `docs/threat-model.md`:

- у таблиці точок входу `POST /v1/answer/stream` стає `POST /v1/questions`, а рядок `POST /v1/answer` видаляється;
- рядок «The provider keeps Questions and Evidence» стає: «The one Question route, `POST /v1/questions`, asks it not to (`store=False`); the ungrounded M01 route was removed in M09-T01 | None»;
- у рядку «A Question floods the model to run up cost» лишається тільки «Context and token budgets and one deadline per stream»;
- у рядку точок входу для `GET /v1/job-decisions` «failed for good, with their kind» стає «failed for good, a page at a time, with their kind», а «both checked before the ID is parsed» стає «both checked before the ID, `limit`, or `cursor` is read»;
- серед відомих прогалин замініть «`POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.» на:

```markdown
- A page cursor is not signed. A client can write its own, but it only says where a list starts that the caller may read whole anyway, and the server refuses any cursor it would not have written.
```

Документи проєкту англійською, тож нові речення теж англійською, як вище.

Перевірка: `grep -n "/v1/answer" docs/architecture.md docs/threat-model.md` нічого не друкує, а `make docs-check` проходить.

## Крок 11 — Запустити перевірки і написати докази

Без коду в цьому кроці.

Запустіть перевірки одну за одною, на одній тестовій базі даних:

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

На прототипі вони дали: 363 відформатовані файли, усі перевірки пройдено, 0 помилок, docs-check з кодом 0, `3377 passed` модульних і `910 passed` інтеграційних, коли доступні секрети Keycloak і Redis.

Потім напишіть своїми словами `docs/evidence/M09-T01-native-resource-routes.md`: інваріант і власників із кроку 1, ваше передбачення і що сталося, перевірку, що мала впасти, таблицю маршрутів за областями, дизайн сторінок і чому не зсув, навмисну поломку, результати перевірок і що спростувало б дизайн. Код цього уроку не робить жодного зовнішнього чи платного виклику.

## Підсумковий чекліст

- [ ] `src/knowledge_service/api_routes.py` перелічує 24 маршрути, а `route_problems(ROUTES)` порожній.
- [ ] `POST /v1/answer` більше немає; маршрут відповідей — `POST /v1/questions`.
- [ ] `GET /v1/job-decisions` приймає `limit` і `cursor`, відповідає `{items, next_cursor}`, а повна остання сторінка має `next_cursor: null`.
- [ ] Поганий чи невідомий параметр запиту — це 422 `validation_error`, але лише після перевірки ролі.
- [ ] Невідомий шлях чи метод під `/v1` відповідає у формі `ErrorResponse`, без перенаправлення.
- [ ] Кожен маршрут `/v1` описує помилки через `ErrorResponse`, ID як UUID і жодних полів провайдера.
- [ ] `tests/test_api_routes.py` (45), `tests/test_pagination.py` (70) і новий інтеграційний тест проходять.
- [ ] Ви бачили, як навмисна поломка падає і знову проходить.
- [ ] `docs/architecture.md` і `docs/threat-model.md` більше не згадують `/v1/answer`.
- [ ] Кожна перевірка проходить, файл доказів написано.

## Запитання для роздумів

1. Уявіть, що адміністратор читає сторінку 1 списку рішень, потім запускає повторно один Job з неї, потім читає сторінку 2. Чи буде повторений Job на сторінці 2, і чи може якийсь Job бути пропущено? Поясніть через `(finished_at, job_id)` курсора.
2. Курсор не підписано. Що саме може зробити клієнт, написавши власний курсор, і чому це не покаже йому нічого, чого він і так не міг прочитати?
3. Якби `limit` було оголошено як FastAPI `Query(ge=1, le=100)`, що дізнався б викликач без ролі `recover_jobs`, надіславши `?limit=0`? Чому маршрут читає його вручну?
4. Який тест упаде першим, якщо M09-T02 додасть `GET /v1/sources` до `app.py`, але забуде додати рядок у таблицю? А який — якщо рядок матиме не ту Operation?
5. Чому нативні схеми уникають `messages`, `role` і `choices`, хоча Open WebUI говорить саме цим протоколом? Де ці слова житимуть натомість?
6. Яка помилка ще може статися після цього уроку, коли клієнт уже вважає, що запит удався? Подумайте про сторінку, прочитану, поки Jobs падають, і про старий маршрут `/v1/answer` у клієнті, якого так і не оновили.
