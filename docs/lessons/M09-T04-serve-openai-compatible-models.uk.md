# M09-T04 — Обслуговувати OpenAI-сумісні моделі

Англійська версія: [M09-T04-serve-openai-compatible-models.md](M09-T04-serve-openai-compatible-models.md)

Завдання: [M09-T04 — Serve OpenAI-compatible models](../curriculum/milestones/M09-product-interfaces/M09-T04-serve-openai-compatible-models.md)

## Що ви зробите

Цей урок відкриває другу половину M09: OpenAI-сумісний адаптер, з яким говоритиме Open WebUI. Open WebUI, як і кожен клієнт OpenAI, спершу питає, якими моделями він може користуватися. Після уроку сервіс відповідає однією віртуальною моделлю, базою знань, і робить це у власному форматі OpenAI, тож її може прочитати навіть офіційний Python-клієнт OpenAI.

Конкретний приклад. З токеном доступу користувача:

```text
GET /openai/v1/models
Authorization: Bearer eyJhbGciOi...
-> 200 {"object": "list",
        "data": [{"id": "knowledge-base", "object": "model", "created": 1795564800,
                  "owned_by": "knowledge-service", "name": "Knowledge base"}]}

GET /openai/v1/models            (no token)
-> 401 {"error": {"message": "A valid access token is required",
                  "type": "authentication_error", "param": null, "code": "unauthenticated"}}
```

Наступні уроки будують на цьому маршруті: M09-T05 відповідає на chat completions цією моделлю, M09-T06 передає їх потоком, M09-T07 зберігає Citations, а M09-T08 підключає Open WebUI.

| Що просить завдання | Де це зроблено |
|---|---|
| `/v1/models` з однією віртуальною моделлю бази знань | Блоки 1 і 2; кроки 2 і 4 |
| Сталі власник і метадані, яких чекає Open WebUI | Блок 2; крок 2 |
| Протокольний тест: форма | Крок 6 (офіційний клієнт OpenAI) |
| Протокольний тест: bearer-автентифікація | Блок 3; крок 6 (також зі справжніми токенами Keycloak) |
| Протокольний тест: видимість налаштованої моделі | Блок 4; кроки 3 і 6 |
| Успіх, межі, типізовані збої чи відмови | Крок 6 |
| Артефакти версіоновані й без секретів, зовнішні виклики обмежені | Блок 3; крок 9 |
| Пояснити, що спростувало б дизайн | Крок 7 і запитання для роздумів |

Терміни, без яких урок не обійдеться:

- **OpenAI-сумісний API:** HTTP API, що говорить тими самими формами запитів і відповідей, що й OpenAI, тож будь-який клієнт OpenAI може ним користуватися, змінивши лише base URL і ключ.
- **Base URL:** адреса, яку клієнт OpenAI ставить перед кожним шляхом. З base URL `http://api/openai/v1` виклик «перелічити моделі» — це `GET http://api/openai/v1/models`.
- **Віртуальна модель** (virtual model): ID моделі, що не називає жодної справжньої мовної моделі. Тут `knowledge-base` означає «відповідай з бази знань», хоч би яка модель провайдера писала текст за лаштунками.
- **Адаптер** (adapter): код, що перекладає один протокол на інший. ADR 0004 тримає нативний API у власних словах і дає тонкому адаптеру говорити мовою OpenAI.

## Перш ніж почати

Цей розділ пояснює кожне рішення, на яке спирається код уроку. Кожен блок показує реальну ситуацію в проєкті, що відбувається сьогодні, факт, що лежить під нею, і виправлення. Прочитайте його спочатку: кроки на нього спираються.

### 1. Адаптер живе на `/openai/v1`, а не всередині нативного `/v1`

Завдання каже `/v1/models`, як шлях у OpenAI. Очевидне місце — власний `/v1/models` цього сервісу. Ось що станеться, якщо покласти його туди:

| Що M09-T01 вимагає від кожного маршруту `/v1` | Чого потребує список моделей OpenAI |
|---|---|
| Жодних назв полів провайдера в жодній схемі: `object`, `id`, `model`, ... | `{"object": "list", "data": [{"id": ..., "object": "model"}]}` |
| Кожен GET списку приймає `limit` і `cursor` і відповідає `{items, next_cursor}` | Одна сторінка, `{"object", "data"}`, без курсора |
| Одна форма помилки: `{"code", "message", "request_id"}` | Форма помилки OpenAI: `{"error": {"message", "type", "param", "code"}}` |

Кожен із цих тестів M09-T01 упав би. І мав би рацію: ADR 0004 каже, що нативний API тримає власні слова, а мовою провайдера говорить лише адаптер.

Прихований факт: клієнт OpenAI ніколи не бачить шлях `/v1/models` як такий. Він з'єднує налаштований **base URL** з `/models`. Тож адаптер може жити будь-де, аби його base URL закінчувався там. Цей урок кладе його на `/openai/v1`:

| Клієнт | Base URL, який йому дають | Список моделей, який він викликає |
|---|---|---|
| Python-клієнт OpenAI | `http://api:8000/openai/v1` | `GET /openai/v1/models` |
| Open WebUI (M09-T08) | `http://api:8000/openai/v1` | `GET /openai/v1/models` |

Маршрут адаптера також не потрапляє до нативного документа OpenAPI (`include_in_schema=False`). Той документ описує нативний API; свій протокол документує сама OpenAI.

Як готель з окремим входом для туристичних груп: та сама будівля й персонал, але біля цих дверей свій напис і своя мова.

### 2. Одна віртуальна модель, чиї дані ніколи не змінюються

Open WebUI запитує список моделей при кожному завантаженні сторінки. Його серверний код (перевірено в `open_webui/routers/openai.py`) робить із відповіддю ось що:

| Що робить Open WebUI | Що це означає для нас |
|---|---|
| читає `response["data"]` | список має бути під `data` |
| ідентифікує кожну модель за `id` і за цим ID зберігає вибір користувача | ID ніколи не має змінюватися, інакше збережені чати вказуватимуть на модель, якої вже немає |
| показує `name`, а якщо його немає — ID | надсилати людську назву `name` |
| замінює `owned_by` на власне значення | годиться будь-який сталий власник |
| пропускає тіло з ключем `error` | помилка має бути у формі OpenAI, інакше її не розпізнають як помилку |

Тож список містить одну модель, і кожне поле стале:

```python
# Was (what a first try often does): details that move
ModelObject(id=settings.model_name, created=int(time.time()), owned_by="openai")

# Became: a virtual model, all of it fixed or configured
ModelObject(id="knowledge-base", created=MODEL_CREATED, owned_by="knowledge-service", name="Knowledge base")
```

Прихований факт стосується `created`. Це мітка часу Unix. Обчислена через `time.time()`, вона змінюється при кожному перезапуску, і клієнт, що порівнює списки моделей, щоразу бачить «нову» модель. Тут це константа: північ UTC 25.11.2026, день, коли цю модель уперше обслужили.

ID не називає ні провайдера, ні мовної моделі. `knowledge-base` лишається тим самим, коли сервіс переходить з однієї моделі OpenAI на іншу чи на локальну в M15, бо користувач обирає «відповідай з бази знань», а не «поговори з gpt-щось».

### 3. Та сама bearer-перевірка, що й на кожному нативному маршруті, але відмова у формі OpenAI

Списку моделей потрібні bearer-токен і операція `ask_questions` (роль `user`), перевірені точно як на `POST /v1/questions`: хто, потім чи можна. Змінюється лише форма відмови.

Прихований факт: обробники винятків FastAPI належать усьому застосунку. Коли `authenticate` кидає виняток усередині будь-якого маршруту, відповідає обробник застосунку, а він сьогодні завжди пише нативну форму:

| Момент | Запит | Обробник | Тіло |
|---|---|---|---|
| 1 | `GET /openai/v1/models` без токена | обробник `_NotAuthenticated` | `{"code": "unauthenticated", "message": ..., "request_id": ...}` |
| 2 | Open WebUI читає його | | ні ключа `error`, ні ключа `data`: моделей немає, і помилки не видно |

Тому маршрут адаптера сам перехоплює три відмови й відповідає у формі OpenAI. Щоб мати одне джерело правди для кожної відмови, урок спершу переносить те, що кожна з них каже, у невелике значення `Refusal`, спільне для нативних обробників і адаптера:

| Відмова | Статус | Нативний код | `type` OpenAI | Додатковий заголовок |
|---|---|---|---|---|
| немає токена, невідомий токен, інша схема | 401 | `unauthenticated` | `authentication_error` | `WWW-Authenticate: Bearer ...` |
| поламаний заголовок `Authorization` | 400 | `invalid_request` | `invalid_request_error` | `WWW-Authenticate: Bearer error="invalid_request"` |
| роль не дозволяє `ask_questions` | 403 | `forbidden` | `permission_error` | немає |
| токени неможливо перевірити | 503 | `dependency_unavailable` | `server_error` | `Retry-After: 5` |
| невідомий шлях чи метод під `/openai/v1` | 404, 405 | `not_found`, `method_not_allowed` | `invalid_request_error` | `Allow` для 405 |

Тіло помилки OpenAI не має поля для ID запиту, тож адаптер надсилає його заголовком `X-Request-ID`. Повідомлення — незмінні нативні, тож жоден текст токена ніколи не потрапляє в тіло, заголовок чи лог.

Нативні маршрути поводяться точно як раніше: незалежне рев'ю порівняло 580 нативних запитів, з відмовами включно, до і після зміни, байт у байт.

### 4. Модель налаштовується, і приймається лише придатний ID

ID і назва моделі беруться з налаштувань, `KNOWLEDGE_SERVICE_COMPATIBLE_MODEL_ID` і `KNOWLEDGE_SERVICE_COMPATIBLE_MODEL_NAME`, зі значеннями за замовчуванням `knowledge-base` і «Knowledge base». Що налаштовано, те й буде єдиною показаною моделлю.

| Значення | Правило | Приклади відмов |
|---|---|---|
| ID | від 1 до 64 символів з `a-z 0-9 . _ : -`, починається з літери чи цифри | `Knowledge-Base`, `knowledge base`, `kb/1`, `kb?`, `-kb` |
| назва | від 1 до 64 видимих символів, у NFC, без пробілів довкола, хоча б один видимий | `" Knowledge"`, розрив рядка, самотній наголос, `e` + наголос замість `é` |

ID, який клієнт не може вставити в URL чи налаштування без екранування, відхиляється на старті, а не виявляється користувачем. `Settings` перевіряє обидва значення під час завантаження, тож погане розгортання падає ще до того, як щось обслужить.

### Інші знахідки рев'ю

Незалежне рев'ю атакувало прототип до написання цього посібника. Кожну знахідку нижче виправлено, і для кожної є тест, який падав до виправлення.

- **За префіксом шляху невідомі шляхи мали нативну форму.** Коли сервіс обслуговується з `root_path="/api"`, як за проксі, `GET /api/openai/v1/nope` отримував `{"code": "not_found", ...}`, бо перевірка префікса читала повний шлях разом з `/api`. Тепер перевірка спершу знімає `root_path`, як це робить маршрутизація.
- **Назва могла нічого не показувати.** Самотній комбінований наголос проходив правило, а `e` плюс наголос рахувалися як два символи. Тепер назва має бути в NFC і містити хоча б один видимий символ.
- **Відмова не несла ID запиту.** Тепер це заголовок `X-Request-ID`.

### Відомі обмеження

- Обслуговується лише список: `GET /openai/v1/models/{id}` відповідає 404 у формі OpenAI.
- Префікс чутливий до регістру, а `/openai` без `/v1` отримує нативний 404.
- У Open WebUI звичайний користувач бачить модель провайдера лише після того, як адміністратор надасть до неї доступ; це налаштування — частина M09-T08.
- Жодна робоча точка входу ще не будує застосунок, тож `Settings.compatible_model()` поза тестами ніхто не викликає.
- Тести використовують `httpx2`, HTTP-бібліотеку, на якій побудовано клієнт OpenAI; вона приходить разом з `openai`, а не як пряма залежність.

## Крок 1 — Прочитати, передбачити і побачити, як перевірка падає

Без коду в цьому кроці.

Прочитайте файл завдання, потім блоки 1–4. До початку програмування запишіть відповіді:

- Якби список моделей жив на нативному `/v1/models` у формі OpenAI, які тести M09-T01 упали б?
- Запит без токена доходить до списку моделей. Що зробив би Open WebUI з нативним тілом помилки і що він робить з формою OpenAI?
- Сервіс перезапускається. Яке поле моделі змінилося б, якби його будували через `time.time()`?

Потім запустіть файл тестів, який створює урок, щоб побачити, що його ще немає:

```bash
uv run pytest tests/test_compatible_models.py -q
```

Очікувано: `ERROR: file or directory not found: tests/test_compatible_models.py`, `no tests ran`, код виходу 4.

## Крок 2 — Написати пакет сумісності

Крок із кодом.

**Простими словами:** цей новий пакет — єдине місце, що говорить словами OpenAI. Він описує одну віртуальну модель і перевіряє її налаштовані ID й назву, будує список у форматі OpenAI і пише помилку у форматі OpenAI з нативним кодом і повідомленням.

| Частина | Що робить |
|---|---|
| `MODEL_CREATED`, `MODEL_OWNER` | сталі час створення й власник |
| `CompatibleModel` | налаштовані ID і назва, кожне перевірене своїм правилом |
| `ModelObject`, `ModelList` | форми моделі й списку OpenAI, плюс `name` для Open WebUI |
| `model_list` | список, який бачить дозволений клієнт |
| `CompatibleError`, `compatible_error` | тіло помилки OpenAI з `type` для кожного статусу |

Створіть `src/knowledge_service/compatibility/__init__.py`:

```python
"""The OpenAI-compatible adapter: OpenAI's protocol, translated to the native API.

ADR 0004: the native API keeps its own words, and only this package speaks
OpenAI's. Its routes live under `/openai/v1`, outside the native `/v1`
design, and stay out of the native OpenAPI document.
"""
```

Створіть `src/knowledge_service/compatibility/models.py`:

```python
"""The model list of the OpenAI-compatible adapter: one virtual knowledge model.

An OpenAI client, such as Open WebUI, asks `GET {base}/models` which models
it may choose. This service offers exactly one: a virtual model that answers
from the knowledge base through the native Question workflow, whatever
language model runs behind it. Its ID and display name come from
configuration, and its creation time and owner are fixed, so a client's
saved choice keeps working across restarts and upgrades, and nothing names
the provider or the real model behind it.

A refusal has OpenAI's error shape, `{"error": {...}}`, with the native
API's fixed codes and messages, so an OpenAI client reads it as an error
and never as a model list.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Final, Literal

from fastapi.responses import JSONResponse
from pydantic import BaseModel

# Midnight UTC on 2026-11-25, when the virtual model was first served. It
# never changes: a model whose creation time moves looks like a new model.
MODEL_CREATED: Final = 1_795_564_800
MODEL_OWNER: Final = "knowledge-service"
DEFAULT_MODEL_ID: Final = "knowledge-base"
DEFAULT_MODEL_NAME: Final = "Knowledge base"
MAX_MODEL_NAME: Final = 64
# Lowercase letters, digits, and `.`, `_`, `:`, `-`: an ID a client can put
# in a URL or a setting without escaping.
MODEL_ID_PATTERN: Final = r"[a-z0-9][a-z0-9._:-]{0,63}"
_MODEL_ID = re.compile(MODEL_ID_PATTERN)


class InvalidCompatibleModel(ValueError):
    """A configured model ID or name breaks its rule."""


@dataclass(frozen=True, slots=True)
class CompatibleModel:
    """The one virtual model the adapter offers, as configured."""

    model_id: str = DEFAULT_MODEL_ID
    name: str = DEFAULT_MODEL_NAME

    def __post_init__(self) -> None:
        if type(self.model_id) is not str or not _MODEL_ID.fullmatch(self.model_id):
            raise InvalidCompatibleModel(
                "the model ID must be 1 to 64 lowercase letters, digits,"
                " `.`, `_`, `:`, or `-`, starting with a letter or digit"
            )
        if (
            type(self.name) is not str
            or not self.name
            or len(self.name) > MAX_MODEL_NAME
            or self.name != self.name.strip()
            or not self.name.isprintable()
            # One spelling of each letter, so the length counts what shows.
            or unicodedata.normalize("NFC", self.name) != self.name
            or not any(_shows(character) for character in self.name)
        ):
            raise InvalidCompatibleModel(
                f"the model name must be visible text of 1 to {MAX_MODEL_NAME}"
                " characters in NFC, with no space around it"
            )


def _shows(character: str) -> bool:
    """Whether a character is seen on its own: not a mark, space, or control."""
    return unicodedata.category(character)[0] not in "MZC"


class ModelObject(BaseModel):
    """One model, as OpenAI's `GET /models` lists it, with Open WebUI's `name`."""

    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str
    name: str


class ModelList(BaseModel):
    """OpenAI's list of models: all of them, in one page."""

    object: Literal["list"] = "list"
    data: list[ModelObject]


def model_list(model: CompatibleModel) -> ModelList:
    """The list a permitted caller sees: the one configured model."""
    return ModelList(
        data=[
            ModelObject(
                id=model.model_id,
                created=MODEL_CREATED,
                owned_by=MODEL_OWNER,
                name=model.name,
            )
        ]
    )


class CompatibleErrorDetail(BaseModel):
    """What went wrong, in OpenAI's words: a fixed message, a type, a code."""

    message: str
    type: str
    param: str | None = None
    code: str


class CompatibleError(BaseModel):
    """OpenAI's error body: one `error` object, never a list or a model."""

    error: CompatibleErrorDetail


# OpenAI's error type for each status this adapter answers with.
_ERROR_TYPES: Final = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "invalid_request_error",
    405: "invalid_request_error",
    500: "server_error",
    503: "server_error",
}


def compatible_error(
    *, status_code: int, code: str, message: str, headers: dict[str, str] | None = None
) -> JSONResponse:
    """An error response in OpenAI's shape, with the native code and message."""
    body = CompatibleError(
        error=CompatibleErrorDetail(
            message=message,
            type=_ERROR_TYPES.get(status_code, "server_error"),
            code=code,
        )
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers=headers,
    )
```

`_shows` питає Unicode про категорію кожного символу: знак (M), пробіл (Z) чи керівний або форматний символ (C) сам по собі нічого не показує. Назві потрібен хоча б один символ, що не належить до жодної з цих категорій.

Перевірка: `uv run pyright src/knowledge_service/compatibility` показує 0 помилок.

## Крок 3 — Налаштувати модель

Крок із кодом.

**Простими словами:** ID і назва моделі стають налаштуваннями зі значеннями за замовчуванням з кроку 2. Коли налаштування завантажуються, вони один раз будують модель, тож поганий ID чи назва зупиняє сервіс на старті з назвою порушеного правила.

У `src/knowledge_service/settings.py` додайте імпорт:

```python
from knowledge_service.compatibility.models import (
    DEFAULT_MODEL_ID,
    DEFAULT_MODEL_NAME,
    CompatibleModel,
)
```

і після `redis_health_timeout_seconds` додайте:

```python
    # The one model the OpenAI-compatible adapter offers, as clients see it
    # (`compatibility.models`). Changing the ID makes saved choices stale.
    compatible_model_id: str = DEFAULT_MODEL_ID
    compatible_model_name: str = DEFAULT_MODEL_NAME

    @model_validator(mode="after")
    def check_compatible_model(self) -> Self:
        # Raises `InvalidCompatibleModel`, a ValueError, naming the rule.
        self.compatible_model()
        return self

    def compatible_model(self) -> CompatibleModel:
        """The virtual model the OpenAI-compatible adapter offers."""
        return CompatibleModel(
            model_id=self.compatible_model_id, name=self.compatible_model_name
        )
```

`InvalidCompatibleModel` — це `ValueError`, тож Pydantic перетворює його на звичну `ValidationError` з назвою поля.

Перевірка:

```bash
uv run python -c "from knowledge_service.settings import Settings; print(Settings().compatible_model())"
```

Очікувано: `CompatibleModel(model_id='knowledge-base', name='Knowledge base')`.

## Крок 4 — Обслуговувати список моделей

Крок із кодом.

**Простими словами:** застосунок отримує єдиний маршрут адаптера. Він перевіряє токен і роль точно як нативні маршрути й відповідає списком або відмовою у формі OpenAI. Щоб нативні обробники й адаптер ділили статус, код, повідомлення й заголовки кожної відмови, обробники тепер беруть їх із невеликих спільних значень, а не пишуть на місці. Будь-який невідомий шлях чи метод під префіксом адаптера теж отримує відповідь у формі OpenAI.

| Частина | Що робить |
|---|---|
| `Refusal`, `failure_refusal`, `not_authenticated_refusal`, `FORBIDDEN` | що каже кожна відмова, хоч би яка форма її несла |
| `native_error` | `Refusal` у нативній формі `ErrorResponse` |
| `COMPATIBLE_PREFIX`, `is_compatible` | префікс адаптера, прочитаний без `root_path` |
| `compatible_refusal` | `Refusal` у формі OpenAI, з `X-Request-ID` |
| `list_compatible_models` | `GET /openai/v1/models`, поза нативним документом OpenAPI |

У `src/knowledge_service/app.py` додайте імпорти:

```python
from dataclasses import dataclass, field
```

```python
from knowledge_service.compatibility.models import (
    CompatibleModel,
    ModelList,
    compatible_error,
    model_list,
)
```

Замініть `failure_response` спільними відмовами, допоміжними функціями адаптера й `failure_response`, що ними користується; вони закінчуються просто перед `request_id_from`:

```python
@dataclass(frozen=True, slots=True)
class Refusal:
    """What a refused request is told, whichever error shape carries it."""

    status_code: int
    code: str
    message: str
    headers: dict[str, str] = field(default_factory=dict[str, str])


def failure_refusal(kind: FailureKind) -> Refusal:
    """The refusal for `kind`: a response not yet started has no stream."""
    policy = policy_for(kind)
    status_code = policy.status_code
    public_code = policy.public_code
    client_message = policy.client_message
    if status_code is None or public_code is None or client_message is None:
        raise RuntimeError("Failure policy has no public HTTP response")
    # An outage or a limit says when to send the request again.
    retry_after = stream_failure(kind, StreamPhase.BEFORE_HEADERS).retry_after_seconds
    headers = {} if retry_after is None else {"Retry-After": str(retry_after)}
    return Refusal(status_code, public_code, client_message, headers)


def not_authenticated_refusal(error: AuthenticationError) -> Refusal:
    """A missing, invalid, or malformed bearer token, with its RFC 6750 challenge."""
    malformed = error is AuthenticationError.INVALID_REQUEST
    # RFC 6750 §3: no error code when no Bearer token was sent.
    challenge = f'Bearer error="{error}"' if error else "Bearer"
    if malformed:
        return Refusal(
            400,
            "invalid_request",
            "The Authorization header is malformed",
            {"WWW-Authenticate": challenge},
        )
    return Refusal(
        401,
        "unauthenticated",
        "A valid access token is required",
        {"WWW-Authenticate": challenge},
    )


# Not an RFC 6750 challenge: another token for the same User has the same
# roles, so there is nothing for the client to retry.
FORBIDDEN = Refusal(403, "forbidden", "Your role does not permit this operation")


def native_error(refusal: Refusal, request_id: UUID) -> JSONResponse:
    """`refusal` in the native API's `ErrorResponse` shape."""
    response = error_response(
        status_code=refusal.status_code,
        code=refusal.code,
        message=refusal.message,
        request_id=request_id,
    )
    response.headers.update(refusal.headers)
    return response


def failure_response(
    *,
    kind: FailureKind,
    request_id: UUID,
) -> JSONResponse:
    """The native JSON error for `kind`."""
    return native_error(failure_refusal(kind), request_id)


# Where the OpenAI-compatible adapter lives; a client's base URL ends here.
COMPATIBLE_PREFIX = "/openai/v1"


def is_compatible(request: Request) -> bool:
    """Whether a request is for the OpenAI-compatible adapter.

    The path is read without `root_path`, as routing reads it, so a service
    behind a path prefix such as `/api` still answers in OpenAI's shape.
    """
    path = request.url.path
    root_path: str = request.scope.get("root_path", "")
    if root_path and path.startswith(root_path):
        path = path[len(root_path) :]
    return path == COMPATIBLE_PREFIX or path.startswith(f"{COMPATIBLE_PREFIX}/")


def compatible_refusal(refusal: Refusal, request: Request) -> JSONResponse:
    """`refusal` in OpenAI's error shape.

    OpenAI's body has no field for a request ID, so it travels as the
    `X-Request-ID` header, for matching a client's error with the logs.
    """
    return compatible_error(
        status_code=refusal.status_code,
        code=refusal.code,
        message=refusal.message,
        headers={**refusal.headers, "X-Request-ID": str(request_id_from(request))},
    )
```

Дайте `create_app` модель, після `synchronizations`:

```python
    compatible_model: CompatibleModel | None = None,
```

і одразу після рядка `FastAPI(...)`:

```python
    offered_model = compatible_model or CompatibleModel()
```

Зробіть, щоб два обробники ідентичності користувалися спільними відмовами:

```python
    @app.exception_handler(_NotAuthenticated)
    async def not_authenticated_handler(
        request: Request, refused: _NotAuthenticated
    ) -> JSONResponse:
        return native_error(
            not_authenticated_refusal(refused.error), request_id_from(request)
        )

    @app.exception_handler(PermissionDenied)
    async def permission_denied_handler(
        request: Request, denied: PermissionDenied
    ) -> JSONResponse:
        logger.info("request not permitted: %s", denied.operation)
        return native_error(FORBIDDEN, request_id_from(request))
```

Дайте обробнику невідомих шляхів і методів обирати форму за префіксом:

```python
    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request, error: StarletteHTTPException
    ) -> JSONResponse:
        # An unknown path or method: the same error shape as every other
        # route under that prefix, OpenAI's for the compatible adapter.
        code, message = HTTP_ERRORS.get(
            error.status_code, ("http_error", "The request cannot be served")
        )
        refusal = Refusal(error.status_code, code, message, dict(error.headers or {}))
        if is_compatible(request):
            return compatible_refusal(refusal, request)
        return native_error(refusal, request_id_from(request))
```

Додайте маршрут перед `/health/live`:

```python
    # The OpenAI-compatible adapter (ADR 0004): OpenAI's shapes, so it stays
    # out of the native OpenAPI document.
    @app.get(
        f"{COMPATIBLE_PREFIX}/models", response_model=ModelList, include_in_schema=False
    )
    async def list_compatible_models(request: Request) -> ModelList | JSONResponse:
        # Who, then whether they may: the same checks as the native routes,
        # answered in OpenAI's error shape.
        try:
            caller = await authenticate(request)
            require(caller.roles, Operation.ASK_QUESTIONS)
        except _NotAuthenticated as refused:
            refusal = not_authenticated_refusal(refused.error)
        except _CannotAuthenticate:
            refusal = failure_refusal(FailureKind.DEPENDENCY)
        except PermissionDenied as denied:
            logger.info("request not permitted: %s", denied.operation)
            refusal = FORBIDDEN
        else:
            return model_list(offered_model)
        return compatible_refusal(refusal, request)
```

Маршрут сам перехоплює три відмови, які кидають `authenticate` і `require`, бо обробники застосунку написали б для них нативну форму. Відмова записується в лог так само, як на нативних маршрутах: за операцією, ніколи за текстом токена.

Перевірка: `uv run pytest tests/test_app.py tests/test_app_authentication.py tests/test_api_routes.py -q` проходить, крім тесту ролей, який виправляє крок 5.

## Крок 5 — Навчити тест ролей нового маршруту

Крок із кодом.

**Простими словами:** тест ролей перелічує кожен маршрут, який обслуговує застосунок, з операцією, що йому потрібна, і падає на будь-якому невідомому маршруті. Новому маршруту потрібна `ask_questions`. Він не звертається до жодного сховища, тож для дозволеного клієнта тест перевіряє саму відповідь, а не виклик сховища.

У `tests/test_app_authentication.py` додайте до `ROUTES`, а після нього — множину:

```python
    # The OpenAI-compatible adapter, outside the native route table.
    ("GET", "/openai/v1/models"): Operation.ASK_QUESTIONS,
}
# Routes that ask no store: a permitted caller is seen by the answer alone.
STORELESS = {("GET", "/openai/v1/models")}
```

і в `test_each_protected_route_admits_exactly_the_roles_that_permit_it` після перевірки 403:

```python
    if route in STORELESS:
        assert (response.status_code == 200) is allowed
        return
```

Маршрут не додається до `api_routes.ROUTES`: та таблиця — це дизайн нативного `/v1`.

Перевірка:

```bash
uv run pytest tests/test_app_authentication.py tests/test_api_routes.py -q
```

Очікувано: усе проходить.

## Крок 6 — Написати протокольні тести

Крок із кодом.

**Простими словами:** головний файл тестів читає список моделей власним Python-клієнтом OpenAI, як це робив би сервер Open WebUI, і перевіряє, що кожна відмова приходить як помилка OpenAI з нативним кодом. Він також перевіряє краї: невідомі шляхи, префікс проксі, нативний документ OpenAPI і правила налаштованої моделі. Другий файл логінить справжніх користувачів через Keycloak і перелічує модель з їхніми справжніми токенами.

| Файл | Що доводить |
|---|---|
| `tests/test_compatible_models.py` | SDK перелічує рівно одну модель; налаштована модель — єдина; тіло байт у байт однакове в різних екземплярах застосунку; точне тіло OpenAI й виклик (challenge) для кожної відмови; SDK кидає власні типи помилок; 503 з `Retry-After`; жодного тексту токена у відповіді чи лозі; невідомі шляхи й методи у формі OpenAI; нативний API зберігає свою форму; нічого в нативному документі OpenAPI; правила ID і назви; налаштування; префікс проксі; `X-Request-ID` |
| `tests/test_compatible_models_integration.py` | справжній токен кожного демо-користувача Keycloak перелічує модель; токен зі зміненим одним символом відхиляється |

Створіть `tests/test_compatible_models.py`:

```python
"""The OpenAI-compatible model list, read by OpenAI's own client.

The protocol test uses the `openai` SDK, as Open WebUI's server would use
the same endpoint: if the SDK reads the list, an OpenAI client can. Every
refusal must reach that client as OpenAI's error shape with the native
code, and the bearer token is checked exactly as on the native routes.
"""

import logging
from typing import Any

import httpx
import httpx2
import openai
import pytest
from pydantic import ValidationError

from knowledge_service.app import create_app
from knowledge_service.compatibility.models import (
    MODEL_CREATED,
    MODEL_OWNER,
    CompatibleModel,
    InvalidCompatibleModel,
)
from knowledge_service.identity.context import Authenticator
from knowledge_service.identity.oidc import IdentityProviderUnavailable
from knowledge_service.settings import Settings
from test_authorization_context import MARKER, POLICY, Verifier, verified

TOKENS = {
    "user-token": verified(),
    "staff-token": verified(
        realm_access={"roles": ["content-manager", "evaluator", "administrator"]}
    ),
    MARKER: verified(azp="account-console"),
}
BASE = "http://t/openai/v1"


def app_with(
    *,
    model: CompatibleModel | None = None,
    verifier: Verifier | None = None,
    authenticated: bool = True,
) -> Any:
    authenticator = Authenticator(verifier or Verifier(TOKENS), POLICY)
    return create_app(
        authenticator=authenticator if authenticated else None,
        compatible_model=model,
    )


def sdk(app: Any, token: str) -> openai.AsyncOpenAI:
    """OpenAI's client, pointed at the adapter, sending `token` as its key."""
    return openai.AsyncOpenAI(
        base_url=BASE,
        api_key=token,
        max_retries=0,
        # The SDK's own HTTP library, httpx2, with the app as its transport.
        http_client=httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)),
    )


async def get(app: Any, path: str, **headers: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        return await http.get(path, headers=headers)


# The list, as OpenAI's client reads it.


async def test_openais_client_lists_exactly_the_one_knowledge_model() -> None:
    page = await sdk(app_with(), "user-token").models.list()

    assert [model.id for model in page.data] == ["knowledge-base"]
    model = page.data[0]
    assert (model.object, model.created, model.owned_by) == (
        "model",
        MODEL_CREATED,
        MODEL_OWNER,
    )
    assert (model.model_extra or {})["name"] == "Knowledge base"


async def test_the_configured_model_is_the_only_one_shown() -> None:
    configured = CompatibleModel(model_id="team-docs:v2", name="Team docs")

    page = await sdk(app_with(model=configured), "user-token").models.list()

    assert [(m.id, (m.model_extra or {})["name"]) for m in page.data] == [
        ("team-docs:v2", "Team docs")
    ]


async def test_the_list_is_the_same_byte_for_byte_after_a_restart() -> None:
    first = await get(
        app_with(), "/openai/v1/models", Authorization="Bearer user-token"
    )
    second = await get(
        app_with(), "/openai/v1/models", Authorization="Bearer user-token"
    )

    assert first.status_code == 200
    assert first.content == second.content
    assert first.json() == {
        "object": "list",
        "data": [
            {
                "id": "knowledge-base",
                "object": "model",
                "created": MODEL_CREATED,
                "owned_by": MODEL_OWNER,
                "name": "Knowledge base",
            }
        ],
    }


# Bearer authentication, and its refusals in OpenAI's shape.


UNAUTHENTICATED = (
    "authentication_error",
    "unauthenticated",
    "A valid access token is required",
)


@pytest.mark.parametrize(
    ("headers", "status", "error", "challenge"),
    [
        ({}, 401, UNAUTHENTICATED, "Bearer"),
        (
            {"Authorization": "Bearer not-a-token"},
            401,
            UNAUTHENTICATED,
            'Bearer error="invalid_token"',
        ),
        ({"Authorization": "Basic dXNlcjpwYXNz"}, 401, UNAUTHENTICATED, "Bearer"),
        (
            {"Authorization": "Bearer two words"},
            400,
            (
                "invalid_request_error",
                "invalid_request",
                "The Authorization header is malformed",
            ),
            'Bearer error="invalid_request"',
        ),
        (
            {"Authorization": "Bearer staff-token"},
            403,
            (
                "permission_error",
                "forbidden",
                "Your role does not permit this operation",
            ),
            None,
        ),
    ],
    ids=[
        "no token",
        "unknown token",
        "another scheme",
        "malformed bearer",
        "no user role",
    ],
)
async def test_each_refusal_is_an_openai_error_with_the_native_code(
    headers: dict[str, str],
    status: int,
    error: tuple[str, str, str],
    challenge: str | None,
) -> None:
    response = await get(app_with(), "/openai/v1/models", **headers)

    error_type, code, message = error
    assert response.status_code == status
    assert response.json() == {
        "error": {"message": message, "type": error_type, "param": None, "code": code}
    }
    assert response.headers.get("www-authenticate") == challenge


@pytest.mark.parametrize(
    ("token", "error"),
    [
        ("not-a-token", openai.AuthenticationError),
        ("staff-token", openai.PermissionDeniedError),
    ],
)
async def test_openais_client_raises_its_own_error_for_each_refusal(
    token: str, error: type[openai.APIStatusError]
) -> None:
    with pytest.raises(error) as raised:
        await sdk(app_with(), token).models.list()

    assert raised.value.code == (
        "forbidden" if error is openai.PermissionDeniedError else "unauthenticated"
    )


@pytest.mark.parametrize(
    "app",
    [
        app_with(authenticated=False),
        app_with(verifier=Verifier(error=IdentityProviderUnavailable("down"))),
    ],
    ids=["no authenticator", "identity provider down"],
)
async def test_when_tokens_cannot_be_checked_the_answer_is_503(app: Any) -> None:
    response = await get(app, "/openai/v1/models", Authorization="Bearer user-token")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "dependency_unavailable"
    assert response.json()["error"]["type"] == "server_error"
    assert response.headers["retry-after"] == "5"


async def test_a_refusal_names_no_token_text_to_the_client_or_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    # MARKER is a valid token from an untrusted client: refused as unknown.
    response = await get(
        app_with(), "/openai/v1/models", Authorization=f"Bearer {MARKER}"
    )

    assert response.status_code == 401
    assert MARKER not in response.text + str(response.headers) + caplog.text


# The adapter's edges.


@pytest.mark.parametrize(
    ("method", "path", "status", "code"),
    [
        ("GET", "/openai/v1/nothing", 404, "not_found"),
        ("GET", "/openai/v1/models/knowledge-base", 404, "not_found"),
        ("POST", "/openai/v1/models", 405, "method_not_allowed"),
        ("GET", "/openai/v1/models/", 404, "not_found"),
    ],
)
async def test_an_unknown_path_or_method_under_the_adapter_is_an_openai_error(
    method: str, path: str, status: int, code: str
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with()), base_url="http://t"
    ) as http:
        response = await http.request(method, path)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert set(response.json()) == {"error"}


async def test_the_native_api_keeps_its_own_error_shape() -> None:
    response = await get(app_with(), "/v1/nothing")

    assert set(response.json()) == {"code", "message", "request_id"}


def test_the_adapter_is_not_in_the_native_openapi_document() -> None:
    spec = app_with().openapi()

    assert not any(path.startswith("/openai") for path in spec["paths"])
    assert "ModelList" not in spec.get("components", {}).get("schemas", {})


# The configured model.


@pytest.mark.parametrize(
    "model_id",
    ["", "Knowledge-Base", "knowledge base", "-knowledge", "a" * 65, "kb/1", "kb?"],
)
def test_a_model_id_a_client_could_not_use_as_is_is_refused(model_id: str) -> None:
    with pytest.raises(InvalidCompatibleModel):
        CompatibleModel(model_id=model_id)


@pytest.mark.parametrize(
    "name",
    [
        "",
        " Knowledge",
        "Knowledge\n",
        "a" * 65,
        chr(0x301),
        "Cafe" + chr(0x301),
        chr(0x200B) + chr(0x301),
    ],
    ids=[
        "empty",
        "space",
        "line break",
        "too long",
        "a lone accent",
        "decomposed",
        "nothing visible",
    ],
)
def test_a_model_name_is_visible_text(name: str) -> None:
    with pytest.raises(InvalidCompatibleModel):
        CompatibleModel(name=name)


def test_settings_configure_the_model_and_refuse_a_bad_one() -> None:
    settings = Settings(compatible_model_id="team-docs", compatible_model_name="Team")

    assert settings.compatible_model() == CompatibleModel("team-docs", "Team")
    with pytest.raises(ValidationError):
        Settings(compatible_model_id="Team Docs")


async def test_behind_a_path_prefix_an_unknown_path_is_still_an_openai_error() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with(), root_path="/api"),
        base_url="http://t",
    ) as http:
        listed = await http.get(
            "/api/openai/v1/models", headers={"Authorization": "Bearer user-token"}
        )
        unknown = await http.get("/api/openai/v1/nothing")

    assert listed.status_code == 200
    assert (unknown.status_code, set(unknown.json())) == (404, {"error"})


@pytest.mark.parametrize(
    "path", ["/openai/v1/models", "/openai/v1/nothing"], ids=["refused", "unknown"]
)
async def test_a_refusal_carries_the_request_id_as_a_header(path: str) -> None:
    request_id = "0b8f1a52-3c1d-4e6f-9a7b-2d4c6e8f0a1b"

    response = await get(app_with(), path, **{"X-Request-ID": request_id})

    assert response.status_code in {401, 404}
    assert response.headers["x-request-id"] == request_id
```

Створіть `tests/test_compatible_models_integration.py`:

```python
"""The OpenAI-compatible model list with real Keycloak tokens.

A demo user logs in through Keycloak's login form, as Open WebUI's users
will, and OpenAI's own client lists the models with that access token. A
token with one character changed is refused in OpenAI's error shape. The
tests skip themselves when Keycloak or its secrets are absent.
"""

from collections.abc import AsyncIterator

import httpx
import httpx2
import openai
import pytest

from keycloak_flow import CLIENT_ID, ISSUER, SECRETS, local_secrets, log_in
from knowledge_service.app import create_app
from knowledge_service.identity.context import Authenticator, ContextPolicy
from knowledge_service.identity.oidc import OidcVerifier

pytestmark = pytest.mark.integration

AUDIENCE = "knowledge-service-api"
PASSWORDS = {name: f"KS_{name.upper()}_PASSWORD" for name in ("alice", "bob", "carol")}


@pytest.fixture
async def keycloak() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.get(f"{ISSUER}/.well-known/openid-configuration")
        except httpx.TransportError:
            pytest.skip("no local Keycloak: run `make keycloak-up`")
        if not SECRETS.exists():
            pytest.skip("no local Keycloak secrets: run `make keycloak-up`")
        yield client


async def access_token(keycloak: httpx.AsyncClient, username: str) -> str:
    secrets = local_secrets()
    tokens = await log_in(
        keycloak,
        username,
        secrets[PASSWORDS[username]],
        client_secret=secrets["KS_OPEN_WEBUI_CLIENT_SECRET"],
    )
    return tokens.access_token


def sdk(keycloak: httpx.AsyncClient, token: str) -> openai.AsyncOpenAI:
    app = create_app(
        authenticator=Authenticator(
            OidcVerifier(ISSUER, AUDIENCE, keycloak),
            ContextPolicy(trusted_clients=frozenset({CLIENT_ID})),
        )
    )
    return openai.AsyncOpenAI(
        base_url="http://t/openai/v1",
        api_key=token,
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)),
    )


@pytest.mark.parametrize("username", list(PASSWORDS))
async def test_each_demo_user_sees_the_knowledge_model(
    keycloak: httpx.AsyncClient, username: str
) -> None:
    token = await access_token(keycloak, username)

    page = await sdk(keycloak, token).models.list()

    assert [model.id for model in page.data] == ["knowledge-base"]


async def test_a_token_with_one_character_changed_is_refused(
    keycloak: httpx.AsyncClient,
) -> None:
    token = await access_token(keycloak, "alice")
    # The last character of the signature, so the token still parses.
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(openai.AuthenticationError) as raised:
        await sdk(keycloak, tampered).models.list()

    assert raised.value.code == "unauthenticated"
```

Клієнт OpenAI побудовано на `httpx2`, а не на `httpx`, тож він отримує `httpx2.AsyncClient` із застосунком як транспортом: без мережі, але зі справжнім кодом клієнта.

Перевірка:

```bash
uv run pytest tests/test_compatible_models.py -q
uv run pytest tests/test_compatible_models_integration.py -q -m integration
```

Очікувано: `37 passed`, потім `4 passed` (другому потрібен `make keycloak-up`; без Keycloak він пропускається).

## Крок 7 — Навмисно прибрати перевірку ролі

Без коду в цьому кроці.

Цей крок доводить, що тести охороняють правило «модель бачить лише клієнт із роллю user». У `src/knowledge_service/app.py`, у `list_compatible_models`, видаліть рядок

```python
            require(caller.roles, Operation.ASK_QUESTIONS)
```

і запустіть із вимкненим байткодом:

```bash
PYTHONDONTWRITEBYTECODE=1 uv run pytest -p no:cacheprovider tests/test_compatible_models.py -q
```

Очікувано: `2 failed, 35 passed`: `test_each_refusal_is_an_openai_error_with_the_native_code[no user role]` і `test_openais_client_raises_its_own_error_for_each_refusal[staff-token-PermissionDeniedError]`. Співробітник без ролі user тепер бачить модель. `tests/test_app_authentication.py` теж падає — для кожного набору ролей без `user`. Поверніть рядок і запустіть знову: `37 passed`.

Що спростувало б дизайн: клієнт OpenAI, що не може прочитати список, модель, чиї ID чи час створення змінюються між перезапусками, клієнт без ролі user, що бачить модель, відмова під `/openai/v1` у нативній формі чи з текстом токена, або схема у формі OpenAI в нативному документі OpenAPI.

## Крок 8 — Оновити архітектуру й модель загроз

Без коду в цьому кроці.

У `docs/architecture.md` після абзацу, що закінчується на «native route or schema uses its field names (ADR 0004).», додайте:

```markdown
`compatibility` is the OpenAI-compatible adapter. It lives under
`/openai/v1`, so an OpenAI client's base URL ends there, outside the
native `/v1` design and out of the native OpenAPI document. Its
`GET /openai/v1/models` needs a bearer token and `ask_questions` (the
user role), checked exactly as on the native routes, and lists one
virtual model: the configured `compatible_model_id` and
`compatible_model_name` (`knowledge-base`, "Knowledge base"), with a
fixed creation time and owner, so a client's saved choice survives
restarts and nothing names the provider or the language model behind
it. Every refusal under `/openai/v1`, unknown paths and methods
included, has OpenAI's error shape, `{"error": {...}}`, with the
native code and message, and the request ID as the `X-Request-ID`
header; the prefix is read without the server's `root_path`.
```

У `docs/threat-model.md`, у таблиці точок входу, замініть рядок `| OpenAI-compatible adapter, Open WebUI | Users | M09 |` на:

```markdown
| `GET /openai/v1/models` | Any client that reaches the API, Open WebUI among them | Lists the one virtual knowledge model. Needs a bearer token and the `ask_questions` operation (the `user` role), checked as on the native routes; refusals have OpenAI's error shape with the native codes and never quote the token; the list names no provider or language model |
| Chat completions through the adapter, Open WebUI | Users | M09-T05 to M09-T08 |
```

Перевірка: `make docs-check` проходить.

## Крок 9 — Запустити перевірки й написати докази

Без коду в цьому кроці.

Додайте нові файли в git перед запуском hooks: pre-commit дивиться лише на файли, які відстежує git.

```bash
git add -A
uv run ruff format --check .
uv run ruff check .
uv run pyright
make docs-check
make test-unit
make test-integration
git diff --cached --check
uv run pre-commit run
```

На прототипі вони дали: 375 відформатованих файлів, усі перевірки пройшли, 0 помилок, docs-check з кодом 0, `3734 passed` модульних, `952 passed` інтеграційних (з секретами Keycloak і доступним Redis), і кожен hook пройшов на доданих файлах.

Потім своїми словами напишіть `docs/evidence/M09-T04-openai-compatible-models.md`: інваріант і власників, ваші передбачення з кроку 1 і що сталося насправді, очікувану поломку перевірки, чому адаптер живе на `/openai/v1`, сталі дані моделі, таблицю відмов, навмисну поломку, результати перевірок і що спростувало б дизайн. Маршрут не робить власних зовнішніх викликів: лише завантаження ключів провайдера ідентичності, вже обмежене з M07. Нічого не зберігається, тож і повторювати нічого.

## Підсумковий чекліст

- [ ] `GET /openai/v1/models` перелічує рівно одну модель, за замовчуванням налаштовану `knowledge-base`, зі сталими `created` і `owned_by`.
- [ ] Python-клієнт OpenAI читає список і кидає власні типи помилок для 401 і 403.
- [ ] Кожна відмова під `/openai/v1`, з невідомими шляхами й методами включно, має форму помилки OpenAI з нативним кодом і повідомленням, правильний challenge чи `Retry-After`, і `X-Request-ID`.
- [ ] Клієнт без ролі user отримує 403; жоден текст токена не з'являється у відповіді чи лозі.
- [ ] Нативний API і його документ OpenAPI не змінилися, і жоден шлях `/openai` чи схема OpenAI в тому документі не з'являється.
- [ ] Поганий налаштований ID чи назва не дає `Settings` завантажитися.
- [ ] `tests/test_compatible_models.py` (37) і `tests/test_compatible_models_integration.py` (4, з Keycloak) проходять.
- [ ] Навмисна поломка дала 2 failed, 35 passed, а відновлений код проходить.
- [ ] `docs/architecture.md` і `docs/threat-model.md` оновлено, і кожна перевірка з кроку 9 проходить, hooks теж.
- [ ] Файл доказів написано своїми словами.

## Запитання для роздумів

1. Завдання каже `/v1/models`, а маршрут — `/openai/v1/models`. Поясніть, як клієнт OpenAI знаходить маршрут і чому розміщення його у власному `/v1` цього сервісу зламало б дизайн M09-T01.
2. `created` моделі — стале число, а не поточний час. Що бачив би клієнт після кожного перезапуску, якби це був поточний час, і чому це важливо для збереженого чату в Open WebUI?
3. ID моделі — `knowledge-base`, а не назва моделі OpenAI, що пише відповіді. Що змінюється для користувачів, коли сервіс пізніше змінить мовну модель, за кожного з цих варіантів?
4. Запит без токена доходить до списку моделей. Пройдіть, що Open WebUI зробив би з нативним тілом помилки і що він робить з формою помилки OpenAI.
5. Нативні обробники відмов переписали так, щоб вони ділили значення `Refusal` з адаптером. Як урок показує, що жодна нативна відповідь не змінилася, і чому це важливіше за власні тести нового маршруту?
6. За проксі, що додає `/api` перед кожним шляхом, невідомі шляхи адаптера отримували нативну форму помилки. Чому маршрутизація все одно працювала, а перевірка форми — ні?
