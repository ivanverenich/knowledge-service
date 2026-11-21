English version: [M08-T01-introduce-dramatiq-and-redis.md](M08-T01-introduce-dramatiq-and-redis.md)

# M08-T01 — Підключаємо Dramatiq і Redis

Завдання: [M08-T01 — Introduce Dramatiq and Redis](../curriculum/milestones/M08-jobs-and-confluence/M08-T01-introduce-dramatiq-and-redis.md)

## Що ти зробиш

У цьому уроці в сервісу з'являється процес другого типу: **воркер** (worker). Він виконує роботу у фоні, поза будь-яким HTTP-запитом. Хтось кладе в Redis маленькі повідомлення, а воркер забирає їх і виконує. Поки що є лише одна задача, нешкідливий `ping`: вона доводить, що шлях працює від початку до кінця. Пізніші уроки пустять цим самим шляхом задачі синхронізації та очищення.

- **Закріплений Redis.** Локальний контейнер Redis з одним точним образом. Він ніколи не викидає повідомлення, щоб звільнити пам'ять.
- **Налаштований брокер.** Одна функція будує брокер Dramatiq: повідомлення в JSON, іменовані черги, ліміт часу, ліміт віку повідомлення і жодних автоматичних повторів.
- **Одна нешкідлива задача.** Задача `ping` бере короткий токен і пише його в лог. Токен перевіряють і відправник, і воркер.
- **Перевірка готовності.** Команда, яка повертає код 0, лише коли Redis вчасно відповідає, і 1 в усіх інших випадках.
- **Доказ:** тести запускають справжній воркер на брокері-замінникові та на справжньому Redis. Ще вони атакують налаштування ворожими повідомленнями та зламаними серверами.

| Що просить завдання | Де це зроблено |
|---|---|
| Версійована інфраструктура | Крок 2: закріплена версія Dramatiq; Крок 3: Redis, закріплений за digest; Крок 4: простір імен `knowledge-service.v1` |
| Явні черги | Крок 4: `QueueName` з `maintenance` і `synchronization` |
| Явний серіалізатор | Крок 4: JSON задається при кожному створенні брокера; pickle не використовується ніколи |
| Ліміти часу | Крок 4: 60 секунд за замовчуванням; Крок 5: 5 секунд для `ping` |
| Локальні перевірки здоров'я | Крок 3: власна перевірка контейнера; Крок 6: перевірка готовності воркера |
| Воркер обробляє нешкідливу задачу | Крок 7 і Крок 8: тести; Крок 9: справжній воркер |
| Готовність падає, коли брокер недоступний | Крок 6: перевірка; Крок 7: тести; Крок 9: зупини Redis і подивися, як вона падає |
| Успіх, межа, типізована помилка або відмова | Крок 7: оброблений ping; токен на 64 символи; `InvalidPing` і `WorkerConfigurationError` |
| Справжня інфраструктура там, де перевіряється її поведінка | Крок 8: integration-тести працюють зі справжнім контейнером Redis |
| Оновити лише ту документацію, якої це стосується | Крок 11: `docs/architecture.md`, `docs/threat-model.md` |

У цьому уроці є код: кроки 2–8 пишуть код, конфігурацію і тести. Кроки 1, 9, 10 і 11 коду не потребують, хоча на кроці 11 ти редагуєш два документи.

Терміни цього уроку:

- **Брокер (broker):** пошта між кодом, який просить зробити роботу, і кодом, який її робить. Тут це Redis під керуванням бібліотеки Dramatiq.
- **Повідомлення (message):** один запит на роботу, наприклад «запусти `ping` з токеном `abc`». У Redis воно лежить як невеликий JSON-документ.
- **Черга (queue):** іменований ряд повідомлень. Воркер читає ті черги, які йому вказали.
- **Актор (actor):** Python-функція, яку Dramatiq може запустити з повідомлення. `ping` — це актор.
- **Воркер (worker):** процес, який знімає повідомлення з черг і запускає їхніх акторів.
- **Проміжний обробник (middleware):** маленькі плагіни, що працюють навколо кожного повідомлення, наприклад «зупини його через 60 секунд».
- **Серіалізатор (serializer, encoder):** те, як повідомлення перетворюється на байти в Redis. JSON — це прості дані. Pickle під час читання може виконати код, тому тут його не використовують.
- **Готовність (readiness):** чи може процес робити свою роботу просто зараз. Воркер, у якого впав Redis, живий, але не готовий.
- **Щонайменше один раз (at least once):** повідомлення може прийти більше ніж один раз, наприклад коли воркер упав посеред задачі. Пізніші уроки роблять задачі безпечними для повторного запуску.

## Перед початком

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілим, коли до нього дійдеш.

- **Що вже є.** `src/knowledge_service/jobs.py` і `persistence/jobs.py` (з M02 і M03) зберігають Job у PostgreSQL: його тип, статус і спроби. Поки що ніщо не запускає Job у фоні. Цей урок додає лише шлях доставки, а M08-T02 під'єднує до нього збережені Job.
- **Redis — не джерело правди.** ADR 0011 каже: Redis лише для короткочасної координації. Якщо Redis перезапуститься і втратить усі повідомлення, збережені Job у PostgreSQL однаково скажуть, що треба виконати. Тому контейнер нічого не зберігає на диск.
- **Redis ніколи не витісняє ключі.** Коли закінчується пам'ять, Redis уміє видаляти ключі. Для черги видалений ключ — це втрачене повідомлення. Тому контейнер працює з `noeviction`: замість видалення Redis відмовляє в нових записах.
- **Усі ліміти в одному місці.** `broker.py` задає ліміт часу, ліміт віку і кількість повторів. Жоден з них не береться із замовчувань бібліотеки. Хто читає код, знайде всі ліміти в одному файлі.
- **Жодних автоматичних повторів.** За замовчуванням Dramatiq повторює невдале повідомлення до 20 разів. Тут стоїть 0, бо в M08-T04 повторами володітиме збережений Job. Якщо одну роботу повторюють два різні місця, кількість спроб множиться.
- **Повідомлення не може змінити власні ліміти.** Dramatiq спершу читає ліміти з повідомлення, а вже потім з актора. Будь-хто з доступом до Redis міг би надіслати повідомлення, яке знову вмикає повтори або знімає ліміт часу. Перший проміжний обробник, `ActorOwnsOptions`, видаляє такі опції, перш ніж їх хтось прочитає.
- **Воркер перевіряє вхід ще раз.** Повідомлення в Redis міг записати не лише наш код. Тому актор `ping` перевіряє токен і тоді, коли повідомлення приходить, а не тільки під час надсилання.
- **Простір імен має версію.** Усі ключі живуть під `knowledge-service.v1`. Якщо формат повідомлення зміниться так, що старі воркери не зможуть його прочитати, версія зростає. Тоді старі й нові воркери ніколи не читають повідомлень одне одного.
- **Перевірка готовності має один дедлайн.** Перевірка надсилає Redis `PING` і має отримати `PONG` у межах таймауту, за замовчуванням дві секунди. Дедлайн покриває всю перевірку, навіть сервер, який надсилає відповідь по одному байту. Будь-яка невдача означає «не готовий», а не падіння.
- **Ліміти часу потребують сигналу запуску.** Ліміт часу в Dramatiq працює через окремий потік-таймер, який стартує під час запуску процесу воркера. Команда `dramatiq` надсилає цей сигнал сама. У тестах ти надсилаєш його вручну через `broker.emit_after("process_boot")`, інакше ліміти часу тихо не працюють.
- **Що знайшло рев'ю.** Незалежне адверсаріальне рев'ю атакувало першу версію. Воно знайшло такі проблеми. Усі вони виправлені, і для кожної є тест, який падає на першій версії:
  - Повідомлення могло саме задати собі `max_retries` і виконатися кілька разів. Тепер `ActorOwnsOptions` цю опцію видаляє.
  - Повідомлення могло задати величезний `time_limit` і працювати набагато довше за ліміт актора. Цю опцію тепер теж видаляють.
  - Повідомлення з `throws: ["x"]` назавжди вбивало потік воркера, і наступні ping уже ніхто не виконував. Тепер опція зникає ще до того, як її прочитає проміжний обробник повторів.
  - Сервер, що надсилав відповідь по одному байту, тримав перевірку готовності понад хвилину. Тепер перевірка працює з одним загальним дедлайном.
  - Сервер, що відповідав `+OK`, опція в URL на кшталт `?timeout=1` або надто довге ім'я хоста валили перевірку нетипізованою помилкою. Тепер будь-яка невдача означає «не готовий».
  - Таймаут готовності `1e-300` проходив валідацію, і здоровий Redis виглядав недоступним. Тепер у таймауту є нижня межа 0,05 секунди.
  - Неправильні налаштування валили перевірку з traceback. Тепер вона виходить з кодом 2 і одним рядком пояснення.
  - Тест на JSON проходив навіть без рядка, який вмикає JSON. Тепер тест спершу вмикає pickle, тож без цього рядка він падає.
- **Відомі обмеження.** Ці речі поки лишаються як є:
  - Повідомлення, яке не є коректним JSON, змушує Dramatiq записати помилку в лог і лишити повідомлення непідтвердженим. Його ніколи не розпаковують через pickle. Відновлення застряглих повідомлень — це M08-T08.
  - Локальний Redis не має пароля і слухає лише 127.0.0.1. Будь-хто на цій машині може в нього писати, тому воркер і перевіряє вхід повторно. Керований Redis з автентифікацією з'явиться разом з AWS у M13.

## Покрокова інструкція

Перед початком запусти Docker. Усі команди виконуються з кореня репозиторію.

### Крок 1 — У цьому кроці коду немає: запиши інваріант і свої прогнози

Перш ніж щось змінювати, запиши, що має бути правдою наприкінці уроку і чого ти очікуєш. Останній прогноз перевіриш на кроці 10.

Створи `docs/evidence/M08-T01-introduce-dramatiq-and-redis.md`:

```markdown
# M08-T01 — Introduce Dramatiq and Redis evidence

## Invariant

A worker runs a message only through an actor declared on a broker built
by `build_broker`: JSON messages, the `knowledge-service.v1` namespace,
named queues, the actor's own time limit, and no Dramatiq retries, whatever
options the message carries. The worker rechecks every argument it reads
from Redis. Readiness is true only while Redis answers PING within one
deadline, and every failure reads as "not ready", never as a crash.
PostgreSQL, not Redis, stays the record of every Job.

## Owners

- Retries: the stored Job (M08-T04); Dramatiq retries nothing.
- Timeouts: the actor's time limit; Redis calls make one attempt each.
- Authorization: none yet; `ping` reads and changes no data.

## Expected failing check

Before the change, `uv run pytest tests/test_worker.py -q` fails with
`ERROR: file or directory not found: tests/test_worker.py`.

## Predictions

- What `make worker-ready` prints when the Redis container is stopped:
- How many times a ping that always fails runs before it reaches the dead-letter queue:
- What happens to queued messages when you run `make redis-down`:
- How many tests in `tests/test_worker.py` fail if `ActorOwnsOptions()` is removed from `queue_middleware`:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після двокрапки в кожному прогнозі стоїть твоя відповідь.

### Крок 2 — Крок з кодом: залежність і два налаштування

На цьому кроці ти встановлюєш Dramatiq разом з його клієнтом Redis і вказуєш у налаштуваннях, де живе Redis.

**Простими словами:** сервіс дізнається нову адресу, URL Redis, і те, скільки перевірка здоров'я може чекати на Redis. З URL поводяться як з паролем, бо справжній URL може його містити. До Redis поки ніхто не підключається.

| Що | Значення | Навіщо |
|---|---|---|
| Пакет | `dramatiq[redis]>=2.2,<3` | фіксуються Dramatiq 2.2.1 і redis-py 8.1.0; `<3` не пускає мажорну версію, яка все поламає |
| `redis_url` | `SecretStr \| None`, за замовчуванням `None` | може містити пароль, тому ніколи не друкується |
| `redis_health_timeout_seconds` | 2.0, від 0.05 до 30 | надто мале значення зробило б здоровий Redis «недоступним» |

**Дія 1:** виконай:

```bash
uv add "dramatiq[redis]>=2.2,<3"
```

**Дія 2:** у `src/knowledge_service/settings.py` додай два поля після `database_statement_timeout_ms`:

```python
    # The Redis broker that carries worker messages; it may hold a password.
    redis_url: SecretStr | None = None
    redis_health_timeout_seconds: float = Field(
        default=2.0, ge=0.05, le=30.0, allow_inf_nan=False
    )
```

**Перевірка:** `uv run python -c "import dramatiq; print(dramatiq.__version__)"` друкує `2.2.1`, а `uv run pytest tests/test_settings.py -q` і далі проходить.

### Крок 3 — Крок з кодом: локальний контейнер Redis і цілі Makefile

На цьому кроці з'являється Redis, який можна запустити й зупинити однією командою, а також команди для роботи з воркером.

**Простими словами:** невеликий файл конфігурації каже Docker запустити один точний образ Redis на порту 6380 лише цієї машини. Redis отримує вказівку ніколи не видаляти ключі заради пам'яті й нічого не писати на диск. Нові команди `make` запускають Redis і воркер, питають, чи воркер готовий, і надсилають один ping.

| Налаштування | Значення | Навіщо |
|---|---|---|
| Образ | `redis:8.2.3-alpine`, закріплений за digest | на кожній машині працюють ті самі байти |
| Порт | `127.0.0.1:6380` | ззовні цієї машини до нього не дістатися; 6380 не конфліктує з іншим Redis на 6379 |
| `--maxmemory-policy noeviction` | відмовляти в записі, коли пам'ять заповнена | витіснений ключ — втрачене повідомлення |
| `--save ""`, `--appendonly no` | нічого на диску | правду зберігає PostgreSQL; перезапуск очищає черги |
| Перевірка здоров'я | `redis-cli ping` | `make redis-up` чекає, доки Redis не відповість |

**Дія 1:** створи `deploy/redis/compose.yaml`:

```yaml
# Local, non-production Redis for the worker queue (M08-T01).
#
# Start it with `make redis-up`. Redis only carries messages between the API
# and workers; PostgreSQL holds every durable record (ADR 0011). The port is
# published on 127.0.0.1 only, so nothing outside this machine can reach it.
name: knowledge-service-redis

services:
  redis:
    # Redis 8.2.3, pinned by digest so every machine runs the same image.
    image: redis:8.2.3-alpine@sha256:08ad0b1d280850169a790dba1393ff7a90aef951fc19632cf4d3ce4f78e679ba
    command:
      - redis-server
      # Never evict a key to free memory: an evicted key is a lost message.
      - --maxmemory-policy
      - noeviction
      # No snapshots or append-only file: a restart empties the queues, and
      # the stored Jobs in PostgreSQL say what still has to run.
      - --save
      - ""
      - --appendonly
      - "no"
    ports:
      - "127.0.0.1:6380:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 2s
      retries: 30
```

**Дія 2:** у `Makefile` додай нові цілі в рядок `.PHONY`:

```makefile
.PHONY: do_good setup format format-check lint typecheck test-unit test-integration ml-check keycloak-up keycloak-down keycloak-reset redis-up redis-down worker worker-ready worker-ping check hooks-install hooks docs-check
```

**Дія 3:** додай цей розділ одразу перед розділом `# Combined checks and git hooks`. Символ `@` перед командою не дає `make` її надрукувати, тому URL з паролем ніколи не потрапить у твій термінал. `WORKER_ENV` бере твій власний `KNOWLEDGE_SERVICE_REDIS_URL`, якщо він заданий, а інакше — локальний контейнер. `make worker` запускає Dramatiq прямо з `.venv`, а не через `uv run`. Через `uv run` один Ctrl+C доходив до Dramatiq двічі, і другий сигнал убивав воркер посеред задачі замість м'якої зупинки. Перед цим він виконує `uv sync`, тож `.venv` завжди актуальне.

```makefile
# ---------------------------------------------------------------------------
# Local queue: Redis and the worker
# ---------------------------------------------------------------------------

# Redis for local work only (M08-T01). It holds no secret and listens on
# 127.0.0.1 only. The worker reads KNOWLEDGE_SERVICE_REDIS_URL, which
# defaults here to that container.
REDIS_COMPOSE = docker compose -f deploy/redis/compose.yaml
REDIS_PROJECT = knowledge-service-redis
LOCAL_REDIS_URL ?= redis://127.0.0.1:6380/0
WORKER_ENV = KNOWLEDGE_SERVICE_REDIS_URL="$${KNOWLEDGE_SERVICE_REDIS_URL:-$(LOCAL_REDIS_URL)}"

redis-up: ## Start the local Redis for the worker queue
	$(REDIS_COMPOSE) up -d --wait

redis-down: ## Stop the local Redis; its queues are emptied
	docker compose -p $(REDIS_PROJECT) down

# The worker runs from .venv directly, not through `uv run`: uv passes Ctrl+C
# on to Dramatiq, which already got it from the terminal, and a second signal
# makes Dramatiq kill its worker instead of stopping it gracefully. The sync
# first keeps .venv current, as `uv run` would.
worker: ## Run one worker process that reads every queue
	@uv sync --locked --quiet
	@$(WORKER_ENV) .venv/bin/dramatiq knowledge_service.worker.main --processes 1 --threads 2

worker-ready: ## Exit 0 only when the worker's Redis broker answers
	@$(WORKER_ENV) uv run python -m knowledge_service.worker.health

worker-ping: ## Queue one harmless ping for a running worker
	@$(WORKER_ENV) uv run python -m knowledge_service.worker.actors
```

**Перевірка:** `make redis-up` закінчується рядком `Container knowledge-service-redis-redis-1 Healthy`, а `docker exec knowledge-service-redis-redis-1 redis-cli config get maxmemory-policy` друкує `noeviction`.

### Крок 4 — Крок з кодом: брокер

На цьому кроці ти будуєш одну функцію, через яку з Redis говорять усі воркери й відправники.

**Простими словами:** цей файл — збірка правил для черги. Він називає черги, задає, скільки може тривати задача і наскільки старим може бути повідомлення, вимикає автоматичні повтори й робить кожне повідомлення простим JSON. Ще він додає один захист, який викидає будь-який ліміт, що повідомлення намагається задати собі саме. Створення брокера не підключається до Redis; підключення відбувається під час першого надсилання чи читання.

| Частина | Що містить або робить |
|---|---|
| `NAMESPACE` | `knowledge-service.v1`, префікс кожного ключа Redis; версія зростає, коли змінюється формат повідомлення |
| `DEFAULT_TIME_LIMIT_MS`, `MAX_MESSAGE_AGE_MS` | 60 секунд на задачу за замовчуванням; повідомлення, старше за добу, відкидається без запуску |
| `QueueName` | лише дві черги: `maintenance` (ping, очищення) і `synchronization` |
| `WorkerConfigurationError` | типізована помилка, коли URL Redis не задано |
| `ActorOwnsOptions` | видаляє кожну опцію-ліміт з повідомлення, тож діють лише ліміти його актора |
| `queue_middleware` | повний упорядкований список проміжних обробників; `ActorOwnsOptions` має йти першим |
| `redis_client` | клієнт Redis з таймаутами на з'єднання й читання та однією спробою на виклик |
| `build_broker` | вмикає JSON, будує `RedisBroker` і оголошує кожну чергу |
| `broker_from_settings` | брокер із `Settings` або `WorkerConfigurationError` |

Множина `broker.actor_options` містить назву кожної опції, яку розуміють проміжні обробники: `time_limit`, `max_retries`, `throws` тощо. Якщо видалити з повідомлення саме ці назви, власний облік Dramatiq, наприклад лічильник повторів, лишається недоторканим.

**Дія 1:** створи `src/knowledge_service/worker/__init__.py`:

```python
"""Run queued work in a separate process: the Redis broker, actors, and health."""
```

**Дія 2:** створи `src/knowledge_service/worker/broker.py`:

```python
"""The Redis broker that carries Dramatiq messages from callers to workers.

Redis only delivers messages. It is not the record of any Job: PostgreSQL is
(ADR 0011). Every limit a message can hit is set here, in one place, so no
library default decides how long work may run or whether it is retried.
"""

from enum import StrEnum
from typing import Final, override

import dramatiq
import redis
from dramatiq.broker import MessageProxy
from dramatiq.brokers.redis import RedisBroker
from dramatiq.middleware import (
    AgeLimit,
    Middleware,
    Retries,
    ShutdownNotifications,
    TimeLimit,
)
from redis.backoff import NoBackoff
from redis.retry import Retry

from knowledge_service.settings import Settings

# Change the version when a message's shape changes, so old workers never
# read messages they cannot understand.
NAMESPACE: Final = "knowledge-service.v1"
DEFAULT_TIME_LIMIT_MS: Final = 60_000
MAX_MESSAGE_AGE_MS: Final = 24 * 60 * 60 * 1000
REDIS_CONNECT_TIMEOUT_SECONDS: Final = 2.0
REDIS_SOCKET_TIMEOUT_SECONDS: Final = 5.0


class QueueName(StrEnum):
    """Every queue a worker may read. A message names one of these."""

    MAINTENANCE = "maintenance"
    SYNCHRONIZATION = "synchronization"


class WorkerConfigurationError(RuntimeError):
    """The worker cannot start because its configuration is incomplete."""


class ActorOwnsOptions(Middleware):
    """Drop every limit a message sets for itself, so its actor's limits apply.

    Dramatiq reads a message's own options before its actor's. Anyone who can
    write to Redis could otherwise turn retries back on, lift a time limit,
    or name an option that kills the worker thread.
    """

    @override
    def before_process_message(
        self, broker: dramatiq.Broker, message: MessageProxy
    ) -> None:
        for name in broker.actor_options & message.options.keys():
            del message.options[name]


def queue_middleware() -> list[Middleware]:
    """The only middleware a broker runs, each with an explicit limit.

    `ActorOwnsOptions` comes first, so the others never see a message's own
    limits.
    """
    return [
        ActorOwnsOptions(),
        AgeLimit(max_age=MAX_MESSAGE_AGE_MS),
        TimeLimit(time_limit=DEFAULT_TIME_LIMIT_MS),
        ShutdownNotifications(),
        # Dramatiq never retries on its own: the stored Job owns retries.
        Retries(max_retries=0),
    ]


def declare_queues(broker: dramatiq.Broker) -> None:
    """Declare every known queue, so a worker listens to all of them."""
    for queue in QueueName:
        broker.declare_queue(queue)


def redis_client(
    redis_url: str,
    *,
    connect_timeout_seconds: float = REDIS_CONNECT_TIMEOUT_SECONDS,
    socket_timeout_seconds: float = REDIS_SOCKET_TIMEOUT_SECONDS,
) -> redis.Redis:
    """A Redis client whose every call ends within a bounded time.

    It makes one attempt per call: whoever calls Redis owns any retry.
    """
    # redis-py leaves the keyword arguments of from_url untyped.
    return redis.Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        redis_url,
        socket_connect_timeout=connect_timeout_seconds,
        socket_timeout=socket_timeout_seconds,
        retry=Retry(NoBackoff(), 0),
    )


def build_broker(redis_url: str, *, namespace: str = NAMESPACE) -> RedisBroker:
    """A Redis broker with JSON messages, explicit limits, and known queues.

    Building it does not connect to Redis; the first send or consume does.
    """
    # Messages are JSON: a worker must never unpickle data read from Redis.
    dramatiq.set_encoder(dramatiq.JSONEncoder())
    broker = RedisBroker(
        client=redis_client(redis_url),
        namespace=namespace,
        middleware=queue_middleware(),
    )
    declare_queues(broker)
    return broker


def broker_from_settings(settings: Settings) -> RedisBroker:
    """The broker for this process, or a typed error when Redis is not set."""
    if settings.redis_url is None:
        raise WorkerConfigurationError(
            "KNOWLEDGE_SERVICE_REDIS_URL is required to run a worker"
        )
    return build_broker(settings.redis_url.get_secret_value())
```

**Перевірка:** `uv run python -c "from knowledge_service.worker.broker import build_broker; print(build_broker('redis://127.0.0.1:6380/0').get_declared_queues())"` друкує обидві назви черг, і запущений Redis для цього не потрібен.

### Крок 5 — Крок з кодом: актор ping і точка входу воркера

На цьому кроці з'являються перша задача і модуль, який завантажує команда `dramatiq`.

**Простими словами:** задача ping бере один короткий токен і пише його в лог. Відправник перевіряє токен перед тим, як поставити його в чергу, а воркер перевіряє ще раз, коли повідомлення приходить. Причина в тому, що повідомлення міг записати будь-хто з доступом до Redis. Модуль точки входу будує брокер з налаштувань і реєструє задачу ping, тож команда `dramatiq` знає, що запускати.

| Частина | Що робить |
|---|---|
| `check_ping_token` | приймає лише справжній `str` з 1–64 ASCII-літер, цифр, `_` або `-`; на все інше кидає `InvalidPing` |
| `declare_actors` | реєструє `ping` на переданому брокері, у черзі `maintenance`, з лімітом 5 секунд і без повторів; колбек `record_ping` отримує кожен токен |
| `send_ping` | перевіряє токен, потім ставить у чергу один ping |
| `log_ping` | колбек воркера: один рядок у лозі на кожен ping |
| `main` в `actors.py` | надсилає один ping з випадковим токеном; його запускає `make worker-ping` |
| `main.py` | будує брокер, робить його глобальним брокером Dramatiq і оголошує акторів |

`declare_actors` отримує брокер і колбек як аргументи замість глобального брокера. Завдяки цьому тести можуть оголосити того самого актора на брокері-замінникові й збирати токени у список. Аргумент `ping_time_limit_ms` існує, щоб тест міг задати ліміт у 200 мілісекунд; саме повідомлення змінити його вже не може.

**Дія 1:** створи `src/knowledge_service/worker/actors.py`:

```python
"""Actors: the jobs a worker knows how to run.

For now there is one, `ping`, a harmless job that proves a message travels
from a caller through Redis to a worker. A ping carries one short token and
changes nothing.
"""

import logging
import re
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

import dramatiq

from knowledge_service.settings import Settings
from knowledge_service.worker.broker import QueueName, broker_from_settings

logger = logging.getLogger(__name__)

PING_ACTOR: Final = "ping"
PING_TIME_LIMIT_MS: Final = 5_000
_PING_TOKEN = re.compile(r"[A-Za-z0-9_-]{1,64}")

type PingRecorder = Callable[[str], None]


class InvalidPing(ValueError):
    """A ping token is not 1 to 64 ASCII letters, digits, `_`, or `-`."""


@dataclass(frozen=True, slots=True)
class Actors:
    """The actors declared on one broker."""

    ping: dramatiq.Actor[[str], None]


def check_ping_token(token: object) -> str:
    """The token itself, or InvalidPing when it is not a short safe word."""
    if type(token) is not str or _PING_TOKEN.fullmatch(token) is None:
        raise InvalidPing("a ping token is 1 to 64 letters, digits, '_' or '-'")
    return token


def declare_actors(
    broker: dramatiq.Broker,
    *,
    record_ping: PingRecorder,
    ping_time_limit_ms: int = PING_TIME_LIMIT_MS,
) -> Actors:
    """Declare every actor on `broker`; `record_ping` sees each ping's token."""

    def ping(token: str) -> None:
        # The message came from Redis, so its argument is checked again here.
        record_ping(check_ping_token(token))

    declare = dramatiq.actor(
        broker=broker,
        actor_name=PING_ACTOR,
        queue_name=QueueName.MAINTENANCE,
        time_limit=ping_time_limit_ms,
        max_retries=0,
    )
    return Actors(ping=declare(ping))


def send_ping(actors: Actors, token: str) -> dramatiq.Message[None]:
    """Queue one ping, refusing a token the worker would refuse."""
    return actors.ping.send(check_ping_token(token))


def log_ping(token: str) -> None:
    """The worker's recorder: one log line per ping."""
    logger.info("ping received: %s", token)


def main() -> int:
    """Send one ping to the configured Redis and print its token."""
    broker = broker_from_settings(Settings())
    try:
        token = uuid.uuid4().hex
        send_ping(declare_actors(broker, record_ping=log_ping), token)
        sys.stdout.write(f"sent ping {token}\n")
    finally:
        broker.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Дія 2:** створи `src/knowledge_service/worker/main.py`:

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

broker = broker_from_settings(Settings())
dramatiq.set_broker(broker)
actors = declare_actors(broker, record_ping=log_ping)
```

**Перевірка:** `uv run python -c "from knowledge_service.worker.actors import check_ping_token; check_ping_token('two words')"` кидає `InvalidPing`.

### Крок 6 — Крок з кодом: перевірка готовності

На цьому кроці з'являється перевірка, яка каже, чи може воркер робити свою роботу просто зараз.

**Простими словами:** перевірка питає Redis «ти тут?» і недовго чекає відповіді. Якщо відповідь приходить вчасно, воркер готовий. Якщо Redis упав, повільний, плутається або URL неправильний, воркер не готовий, і перевірка при цьому ніколи не падає. Вона запускається як команда з кодом виходу 0 або 1, тож згодом її зможуть викликати Docker, ECS чи Kubernetes.

| Частина | Що робить |
|---|---|
| `_answers_ping` | створює клієнт і надсилає `PING`; будь-який виняток означає `False` |
| `broker_ready` | запускає `_answers_ping` у фоновому потоці й чекає на нього щонайбільше `timeout_seconds` |
| `main` | вихід 0 і `ready`; вихід 1, коли Redis не відповів; вихід 2, коли налаштувань немає або вони неправильні |

Навіщо потік: таймаут клієнта діє на кожне окреме читання, а не на всю перевірку. Сервер, який надсилає один байт кожні 0,4 секунди, ніколи не перевищить таймаут читання в 0,5 секунди, але перевірка триватиме хвилини. Очікування на потік дає один дедлайн на всю перевірку. Потік позначений як daemon, тож він ніколи не тримає процес живим.

`except Exception` тут навмисний. redis-py на зламаний вхід кидає винятки різних типів, зокрема `AttributeError` і `TypeError`. Для перевірки готовності кожен з них означає «не готовий».

**Дія:** створи `src/knowledge_service/worker/health.py`:

```python
"""Worker readiness: a worker is ready only while its broker answers.

Run `python -m knowledge_service.worker.health` as a readiness probe. It exits
0 when Redis answers within the timeout, 1 when it does not, and 2 when the
settings are missing or invalid. It never prints the URL, which may hold a password.
"""

import sys
import threading

from pydantic import ValidationError

from knowledge_service.settings import Settings
from knowledge_service.worker.broker import redis_client


def _answers_ping(redis_url: str, timeout_seconds: float) -> bool:
    try:
        client = redis_client(
            redis_url,
            connect_timeout_seconds=timeout_seconds,
            socket_timeout_seconds=timeout_seconds,
        )
        try:
            # redis-py leaves the keyword arguments of ping untyped.
            return client.ping() is True  # pyright: ignore[reportUnknownMemberType]
        finally:
            client.close()
    # A probe answers "not ready" for any failure: a bad URL, a server that
    # is down, or one that replies with something redis-py cannot read.
    except Exception:
        return False


def broker_ready(redis_url: str, *, timeout_seconds: float) -> bool:
    """True when Redis answers PING within `timeout_seconds`, else False.

    The deadline covers the whole check. A server that trickles its reply
    byte by byte is left to a daemon thread, which may outlive the call but
    never keeps the process alive. Call it once per probe process.
    """
    answers: list[bool] = []
    probe = threading.Thread(
        target=lambda: answers.append(_answers_ping(redis_url, timeout_seconds)),
        name="redis-readiness-probe",
        daemon=True,
    )
    probe.start()
    probe.join(timeout_seconds)
    return answers == [True]


def main() -> int:
    try:
        settings = Settings()
    except ValidationError:
        sys.stderr.write("not ready: the worker settings are invalid\n")
        return 2
    if settings.redis_url is None:
        sys.stderr.write("not ready: KNOWLEDGE_SERVICE_REDIS_URL is not set\n")
        return 2
    if not broker_ready(
        settings.redis_url.get_secret_value(),
        timeout_seconds=settings.redis_health_timeout_seconds,
    ):
        sys.stderr.write("not ready: the Redis broker did not answer\n")
        return 1
    sys.stdout.write("ready\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

**Перевірка:** коли Redis запущений, `make worker-ready` друкує `ready`.

### Крок 7 — Крок з кодом: unit-тести

Цей крок доводить кожну обіцянку без справжнього Redis. Для цього використовується брокер-замінник Dramatiq у пам'яті, `StubBroker`.

**Простими словами:** тести запускають справжній воркер Dramatiq на брокері в пам'яті й перевіряють, що ping доходить. Вони перевіряють ліміти і те, що погані токени відхиляються з обох боків. Вони надсилають повідомлення, які намагаються змінити власні ліміти, і перевіряють, що ліміти тримаються. Потім вони наводять перевірку готовності на закриті порти, мовчазні й повільні сервери та нісенітниці й перевіряють, що вона завжди вчасно каже «не готовий».

| Група тестів | Що доводить |
|---|---|
| `test_worker_consumes_a_ping`, `test_send_ping_accepts_the_longest_token` | успіх і межа в 64 символи |
| `test_ping_runs_on_...`, `test_the_broker_runs_only_...`, `test_build_broker_...` | черги, ліміти, порядок проміжних обробників, JSON, простір імен |
| `test_send_ping_refuses_an_unsafe_token` | типізована відмова `InvalidPing` на боці відправника; у чергу нічого не потрапляє |
| `test_worker_refuses_a_hostile_message_without_retrying` | воркер перевіряє повторно; одна спроба; один лист у dead-letter |
| `test_a_ping_past_its_time_limit_is_stopped` | ліміт часу зупиняє повільну задачу |
| `test_a_message_cannot_...` (три тести) | повідомлення не може зняти свій ліміт часу, увімкнути повтори чи зупинити наступний ping |
| `test_broker_from_settings_requires_a_redis_url` | типізована помилка `WorkerConfigurationError` |
| `test_broker_is_not_ready_...`, `test_readiness_...` | «не готовий» для закритих портів, поганих URL, мовчазних, повільних чи плутаних серверів, у межах дедлайну |
| `test_health_command_...` | коди виходу 1 і 2, і пароль ніколи не друкується |
| `test_worker_entry_point_...` | `main.py` задає глобальний брокер і оголошує `ping` |

Важливі два помічники. `run_worker_until_empty` запускає воркер і чекає, доки черга `maintenance` не спорожніє; з `fail_fast=True` перша помилка актора кидається прямо в тесті. `serve_once` — крихітний фальшивий сервер, який надсилає задану відповідь, за бажанням по одному байту.

**Дія:** створи `tests/test_worker.py`:

```python
"""Tests for the worker package: broker setup, the ping actor, and readiness."""

import importlib
import os
import socket
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import dramatiq
import pytest
from dramatiq.brokers.stub import StubBroker
from dramatiq.middleware import (
    AgeLimit,
    Retries,
    ShutdownNotifications,
    TimeLimit,
    TimeLimitExceeded,
)

from knowledge_service.settings import Settings
from knowledge_service.worker import health
from knowledge_service.worker.actors import (
    PING_TIME_LIMIT_MS,
    Actors,
    InvalidPing,
    declare_actors,
    send_ping,
)
from knowledge_service.worker.broker import (
    NAMESPACE,
    ActorOwnsOptions,
    QueueName,
    WorkerConfigurationError,
    broker_from_settings,
    build_broker,
    declare_queues,
    queue_middleware,
)
from knowledge_service.worker.health import broker_ready


@pytest.fixture
def stub_broker() -> Iterator[StubBroker]:
    broker = StubBroker(middleware=queue_middleware())
    declare_queues(broker)
    # The dramatiq command sends this signal; it starts the time-limit timer.
    broker.emit_after("process_boot")
    yield broker
    broker.close()


@pytest.fixture
def received() -> list[str]:
    return []


@pytest.fixture
def actors(stub_broker: StubBroker, received: list[str]) -> Actors:
    return declare_actors(stub_broker, record_ping=received.append)


def run_worker_until_empty(broker: StubBroker) -> None:
    """Consume every queued message, raising the first actor failure."""
    worker = dramatiq.Worker(broker, worker_timeout=100)
    worker.start()
    try:
        broker.join(QueueName.MAINTENANCE, timeout=10_000, fail_fast=True)
        worker.join()
    finally:
        worker.stop()


def closed_port() -> int:
    """A local port with nothing listening on it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_worker_consumes_a_ping(
    stub_broker: StubBroker, actors: Actors, received: list[str]
) -> None:
    send_ping(actors, "first-ping_1")

    run_worker_until_empty(stub_broker)

    assert received == ["first-ping_1"]


def test_ping_runs_on_the_maintenance_queue_with_explicit_limits(
    actors: Actors,
) -> None:
    assert actors.ping.queue_name == QueueName.MAINTENANCE
    assert actors.ping.options["time_limit"] == PING_TIME_LIMIT_MS
    assert actors.ping.options["max_retries"] == 0


def test_the_broker_runs_only_the_chosen_middleware() -> None:
    kinds = [type(middleware) for middleware in queue_middleware()]

    assert kinds == [
        ActorOwnsOptions,
        AgeLimit,
        TimeLimit,
        ShutdownNotifications,
        Retries,
    ]


def test_build_broker_declares_queues_and_json_without_connecting() -> None:
    dramatiq.set_encoder(dramatiq.PickleEncoder())
    broker = build_broker(f"redis://127.0.0.1:{closed_port()}/0")
    try:
        assert broker.namespace == NAMESPACE
        assert set(QueueName) <= broker.get_declared_queues()
        assert type(dramatiq.get_encoder()) is dramatiq.JSONEncoder
    finally:
        broker.close()


@pytest.mark.parametrize(
    "token",
    ["", "two words", "x" * 65, "line\nbreak", "ping;DROP", "пінг"],
)
def test_send_ping_refuses_an_unsafe_token(
    stub_broker: StubBroker, actors: Actors, token: str
) -> None:
    with pytest.raises(InvalidPing):
        send_ping(actors, token)

    assert stub_broker.queues[QueueName.MAINTENANCE].qsize() == 0


def test_send_ping_accepts_the_longest_token(
    stub_broker: StubBroker, actors: Actors, received: list[str]
) -> None:
    send_ping(actors, "x" * 64)

    run_worker_until_empty(stub_broker)

    assert received == ["x" * 64]


@pytest.mark.parametrize("argument", ["two words", 123, None, ["list"]])
def test_worker_refuses_a_hostile_message_without_retrying(
    stub_broker: StubBroker, actors: Actors, received: list[str], argument: object
) -> None:
    # Another producer with Redis access skips send_ping's check.
    actors.ping.send_with_options(args=(argument,))  # pyright: ignore[reportUnknownMemberType]

    with pytest.raises(InvalidPing):
        run_worker_until_empty(stub_broker)

    assert received == []
    assert len(stub_broker.dead_letters) == 1


def test_a_ping_past_its_time_limit_is_stopped(stub_broker: StubBroker) -> None:
    finished: list[str] = []

    def slow_recorder(token: str) -> None:
        for _ in range(100):
            time.sleep(0.05)
        finished.append(token)

    slow = declare_actors(
        stub_broker, record_ping=slow_recorder, ping_time_limit_ms=200
    )
    send_ping(slow, "slow")

    with pytest.raises(TimeLimitExceeded):
        run_worker_until_empty(stub_broker)

    assert finished == []


def test_broker_from_settings_requires_a_redis_url() -> None:
    with pytest.raises(WorkerConfigurationError):
        broker_from_settings(Settings(redis_url=None))


def test_broker_is_not_ready_when_nothing_listens() -> None:
    url = f"redis://127.0.0.1:{closed_port()}/0"

    assert broker_ready(url, timeout_seconds=1.0) is False


@pytest.mark.parametrize("url", ["not-a-url", "http://127.0.0.1:1/0", ""])
def test_broker_is_not_ready_for_a_malformed_url(url: str) -> None:
    assert broker_ready(url, timeout_seconds=1.0) is False


def test_readiness_ends_within_its_timeout_when_redis_never_answers() -> None:
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        port = silent.getsockname()[1]
        accepted: list[socket.socket] = []

        def accept_and_stay_silent() -> None:
            connection, _ = silent.accept()
            accepted.append(connection)

        threading.Thread(target=accept_and_stay_silent, daemon=True).start()
        started = time.monotonic()

        ready = broker_ready(f"redis://127.0.0.1:{port}/0", timeout_seconds=0.3)

        elapsed = time.monotonic() - started
        for connection in accepted:
            connection.close()

    assert ready is False
    assert elapsed < 2.0


@pytest.fixture
def clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for variable in tuple(os.environ):
        if variable.upper().startswith("KNOWLEDGE_SERVICE_"):
            monkeypatch.delenv(variable)
    monkeypatch.chdir(tmp_path)


@pytest.mark.usefixtures("clean_environment")
def test_health_command_exits_2_without_a_redis_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert health.main() == 2
    assert "not set" in capsys.readouterr().err


@pytest.mark.usefixtures("clean_environment")
def test_health_command_exits_1_without_printing_the_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    url = f"redis://:s3cret-password@127.0.0.1:{closed_port()}/0"
    monkeypatch.setenv("KNOWLEDGE_SERVICE_REDIS_URL", url)

    assert health.main() == 1

    output = capsys.readouterr()
    assert "s3cret-password" not in output.out + output.err
    assert "did not answer" in output.err


@pytest.mark.usefixtures("clean_environment")
def test_worker_entry_point_declares_ping_on_its_global_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "KNOWLEDGE_SERVICE_REDIS_URL", f"redis://127.0.0.1:{closed_port()}/0"
    )
    monkeypatch.delitem(sys.modules, "knowledge_service.worker.main", raising=False)

    entry = importlib.import_module("knowledge_service.worker.main")
    try:
        assert dramatiq.get_broker() is entry.broker
        assert entry.broker.get_declared_actors() == {"ping"}
    finally:
        entry.broker.close()
        dramatiq.set_broker(StubBroker())


def test_a_message_cannot_lift_its_actors_time_limit(stub_broker: StubBroker) -> None:
    finished: list[str] = []

    def slow_recorder(token: str) -> None:
        for _ in range(100):
            time.sleep(0.05)
        finished.append(token)

    slow = declare_actors(
        stub_broker, record_ping=slow_recorder, ping_time_limit_ms=200
    )
    slow.ping.send_with_options(args=("slow",), time_limit=60_000_000)  # pyright: ignore[reportUnknownMemberType]

    with pytest.raises(TimeLimitExceeded):
        run_worker_until_empty(stub_broker)

    assert finished == []


def test_a_message_cannot_turn_retries_back_on(stub_broker: StubBroker) -> None:
    attempts: list[object] = []

    def count_attempts(token: str) -> None:
        attempts.append(token)
        raise RuntimeError("planted failure")

    failing = declare_actors(stub_broker, record_ping=count_attempts)
    failing.ping.send_with_options(  # pyright: ignore[reportUnknownMemberType]
        args=("again",), max_retries=5, min_backoff=1, max_backoff=1
    )

    with pytest.raises(RuntimeError):
        run_worker_until_empty(stub_broker)

    assert attempts == ["again"]
    assert len(stub_broker.dead_letters) == 1


@pytest.mark.parametrize(
    "options",
    [{"throws": ["x"]}, {"on_retry_exhausted": "ping"}, {"retry_when": "x"}],
)
def test_a_message_option_cannot_stop_the_next_ping(
    stub_broker: StubBroker,
    actors: Actors,
    received: list[str],
    options: dict[str, Any],
) -> None:
    actors.ping.send_with_options(args=("two words",), **options)  # pyright: ignore[reportUnknownMemberType]
    send_ping(actors, "after")
    worker = dramatiq.Worker(stub_broker, worker_timeout=100, worker_threads=1)
    worker.start()
    try:
        stub_broker.join(QueueName.MAINTENANCE, timeout=10_000, fail_fast=False)
        worker.join()
    finally:
        worker.stop()

    assert received == ["after"]
    assert len(stub_broker.dead_letters) == 1


def serve_once(reply: bytes, *, pause_seconds: float = 0.0) -> int:
    """A one-connection server that sends `reply`, one byte per pause."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()

    def answer() -> None:
        with server:
            connection, _ = server.accept()
            with connection:
                connection.recv(1024)
                for byte in reply:
                    try:
                        connection.sendall(bytes([byte]))
                    except OSError:
                        return
                    time.sleep(pause_seconds)
                time.sleep(1.0)

    threading.Thread(target=answer, daemon=True).start()
    return server.getsockname()[1]


def test_readiness_deadline_covers_a_reply_that_trickles_in() -> None:
    port = serve_once(b"$100\r\n" + b"x" * 100 + b"\r\n", pause_seconds=0.2)
    started = time.monotonic()

    ready = broker_ready(f"redis://127.0.0.1:{port}/0", timeout_seconds=0.5)

    assert ready is False
    assert time.monotonic() - started < 1.5


@pytest.mark.parametrize("reply", [b"+OK\r\n", b"+PANG\r\n", b"garbage\r\n"])
def test_readiness_is_false_for_a_reply_redis_py_cannot_read(reply: bytes) -> None:
    port = serve_once(reply)

    assert broker_ready(f"redis://127.0.0.1:{port}/0", timeout_seconds=1.0) is False


@pytest.mark.parametrize(
    "query", ["timeout=1", "socket_timeout=-1", "socket_timeout=nan", "driver_info=y"]
)
def test_readiness_is_false_for_hostile_url_options(query: str) -> None:
    url = f"redis://127.0.0.1:{closed_port()}/0?{query}"

    assert broker_ready(url, timeout_seconds=1.0) is False


def test_readiness_is_false_for_a_host_label_too_long_to_encode() -> None:
    url = f"redis://{'a' * 64}.invalid:6379/0"

    assert broker_ready(url, timeout_seconds=1.0) is False


@pytest.mark.usefixtures("clean_environment")
def test_health_command_exits_2_for_invalid_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("KNOWLEDGE_SERVICE_REDIS_HEALTH_TIMEOUT_SECONDS", "0.001")

    assert health.main() == 2
    assert "invalid" in capsys.readouterr().err
```

**Перевірка:** `uv run pytest tests/test_worker.py -q` за кілька секунд показує `40 passed`.

### Крок 8 — Крок з кодом: integration-тести на справжньому Redis

Цей крок доводить той самий шлях на справжньому контейнері Redis, бо брокер-замінник не може довести, що Redis поводиться так само.

**Простими словами:** два тести використовують Redis із кроку 3. Перший перевіряє, що перевірка каже «готовий». Другий запускає воркер, надсилає ping через справжній Redis і до десяти секунд чекає, поки той дійде. Кожен запуск тестів має власний простір імен, тож ніколи не читає чужих повідомлень, а після себе прибирає.

`flush_all` у Dramatiq очищає черги, але лишає ключ, де воркери відмічають, що вони живі (heartbeat). Тому фікстура видаляє цей ключ сама. Без цього кожен запуск лишав би по одному ключу в базі 15.

**Дія 1:** створи `tests/test_worker_integration.py`:

```python
"""The worker against a real Redis: readiness and one consumed ping."""

import os
import threading
import uuid
from collections.abc import Iterator

import dramatiq
import pytest
from dramatiq.brokers.redis import RedisBroker

from knowledge_service.worker.actors import declare_actors, send_ping
from knowledge_service.worker.broker import build_broker, redis_client
from knowledge_service.worker.health import broker_ready

pytestmark = pytest.mark.integration

REDIS_URL_ENV = "KNOWLEDGE_SERVICE_TEST_REDIS_URL"


@pytest.fixture
def redis_url() -> str:
    configured = os.environ.get(REDIS_URL_ENV)
    if not configured:
        pytest.skip(f"set {REDIS_URL_ENV} to a disposable Redis server")
    return configured


@pytest.fixture
def redis_broker(redis_url: str) -> Iterator[RedisBroker]:
    # A namespace of its own, so the test never reads another run's messages.
    namespace = f"knowledge-service.test-{uuid.uuid4()}"
    broker = build_broker(redis_url, namespace=namespace)
    yield broker
    broker.flush_all()
    broker.close()
    # flush_all empties the queues but keeps the workers' heartbeat key.
    cleaner = redis_client(redis_url)
    try:
        cleaner.delete(f"{namespace}:__heartbeats__")
    finally:
        cleaner.close()


def test_broker_is_ready_when_redis_answers(redis_url: str) -> None:
    assert broker_ready(redis_url, timeout_seconds=2.0) is True


def test_worker_consumes_a_ping_through_redis(redis_broker: RedisBroker) -> None:
    received: list[str] = []
    arrived = threading.Event()

    def record(token: str) -> None:
        received.append(token)
        arrived.set()

    actors = declare_actors(redis_broker, record_ping=record)
    worker = dramatiq.Worker(redis_broker, worker_timeout=100)
    worker.start()
    try:
        send_ping(actors, "through-redis")
        assert arrived.wait(timeout=10)
    finally:
        worker.stop()

    assert received == ["through-redis"]
```

**Дія 2:** у `Makefile` зроби так, щоб `make test-integration` передавав URL тестового Redis. База 15 тримає тестові ключі окремо від твоєї локальної роботи в базі 0. Заміни блок від коментаря над `TEST_DB_CONTAINER` до кінця рецепта `test-integration` на:

```makefile
# Integration tests skip themselves without KNOWLEDGE_SERVICE_TEST_DATABASE_URL.
# When it is unset, the URL is built from the local Docker container, so the
# password is read at run time and never stored in a file.
# The Redis tests use the local container's database 15 unless
# KNOWLEDGE_SERVICE_TEST_REDIS_URL says otherwise, so run `make redis-up` first.
TEST_DB_CONTAINER ?= knowledge-service-postgres
TEST_REDIS_URL ?= redis://127.0.0.1:6380/15

test-integration: ## Run integration tests (needs services)
	@url="$$KNOWLEDGE_SERVICE_TEST_DATABASE_URL"; \
	if [ -z "$$url" ]; then \
		url="$$(uv run python scripts/test_database_url.py "$(TEST_DB_CONTAINER)")"; \
	fi; \
	if [ -z "$$url" ]; then \
		echo "No test database: set KNOWLEDGE_SERVICE_TEST_DATABASE_URL or start the $(TEST_DB_CONTAINER) container."; \
		exit 1; \
	fi; \
	KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$$url" \
	KNOWLEDGE_SERVICE_TEST_REDIS_URL="$${KNOWLEDGE_SERVICE_TEST_REDIS_URL:-$(TEST_REDIS_URL)}" \
	uv run pytest -m "integration and not live"
```

**Перевірка:** коли Redis запущений, `make test-integration` проходить, а `uv run pytest tests/test_worker_integration.py -q -m integration` без змінної показує `2 skipped`.

### Крок 9 — У цьому кроці коду немає: запусти справжній воркер

Цей крок показує критерії приймання на власні очі: справжній воркер обробляє ping, а готовність падає, коли Redis немає.

**Дія 1:** в одному терміналі виконай `make worker`. Дочекайся рядка `Worker process is ready for action.`

**Дія 2:** у другому терміналі виконай `make worker-ping`. Він надрукує `sent ping <token>`. Після цього в першому терміналі з'явиться `ping received: <token>` з тим самим токеном.

**Дія 3:** зупини Redis і запитай про готовність:

```bash
docker compose -p knowledge-service-redis stop
make worker-ready; echo "exit=$?"
make redis-up
```

Ти побачиш `not ready: the Redis broker did not answer` і ненульовий код виходу. Після `make redis-up` команда `make worker-ready` знову друкує `ready`. Зупини воркер через Ctrl+C.

**Перевірка:** встав два рядки з однаковим токеном і вивід невдалої перевірки готовності в розділ «Verification» свого evidence-файлу.

### Крок 10 — У цьому кроці коду немає: зламай навмисно

На цьому кроці ти прибираєш захист від ворожих опцій у повідомленнях і дивишся, які тести це помітять.

**Дія 1:** у `queue_middleware` у `src/knowledge_service/worker/broker.py` видали цей рядок:

```python
        ActorOwnsOptions(),
```

**Дія 2:** виконай `uv run pytest tests/test_worker.py -q`. Падають п'ять тестів:

- `test_the_broker_runs_only_the_chosen_middleware`
- `test_a_message_cannot_lift_its_actors_time_limit`
- `test_a_message_cannot_turn_retries_back_on`
- `test_a_message_option_cannot_stop_the_next_ping[options0]` (`throws`)
- `test_a_message_option_cannot_stop_the_next_ping[options1]` (`on_retry_exhausted`)

**Дія 3:** поверни рядок на місце й запусти тести ще раз: проходять усі 40.

**Перевірка:** порівняй кількість зі своїм прогнозом із кроку 1 і запиши це в evidence-файл.

### Крок 11 — У цьому кроці коду немає: документи, перевірки, evidence і прогрес

На цьому кроці ти оновлюєш два документи і проганяєш усі перевірки.

**Дія 1:** у `docs/architecture.md` додай цей рядок у таблицю структури коду, одразу після рядка `persistence`:

```markdown
| `worker` | Queue transport: the Redis broker and the worker process | `build_broker` (`broker`, a Dramatiq `RedisBroker` with JSON messages, the `knowledge-service.v1` namespace, the `maintenance` and `synchronization` queues, a 60-second default time limit, a one-day message age limit, and no Dramatiq retries; `ActorOwnsOptions` drops any limit a message sets for itself, so only its actor's limits apply; Redis calls make one attempt with bounded timeouts), `declare_actors` / `send_ping` (`actors`, the harmless `ping` job, whose token is checked on both sides), `broker_ready` (`health`, a readiness probe that is false, within one overall deadline, whenever Redis does not answer PING or any part of the check fails), and `main`, the module the `dramatiq` command loads |
```

**Дія 2:** у `docs/threat-model.md` додай цей рядок одразу перед рядком `Local Keycloak`:

```markdown
| Local Redis, `127.0.0.1:6380` | Developers and local workers | Bound to 127.0.0.1, with no password: anyone on this machine can read or write its queues, so a worker checks every message argument again. It never evicts a key and keeps nothing across a restart |
```

і заміни рядок `Jobs` на:

```markdown
| Jobs | Workers | Stored jobs with typed errors. The Redis queue carries only the harmless `ping` job so far, as JSON, never pickle |
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

**Дія 4:** допиши evidence-файл: результати перевірок у «Verification», знахідки рев'ю в «Review», рішення щодо дизайну в «Design» і «Decision», а також свої відповіді на питання для роздумів нижче. Потім познач завдання виконаним у `docs/curriculum/PROGRESS.md` і зроби коміт з повідомленням `m08-t01: introduce dramatiq and redis`.

**Перевірка:** усі перевірки проходять, а після коміту `git status` чистий.

## Питання для роздумів

- Відправник надсилає ping і не отримує помилки. Назви один спосіб, у який цей ping однаково може ніколи не виконатися, і скажи, який пізніший урок робить цей випадок безпечним.
- Уяви, що Redis перезапустився і всі повідомлення в черзі зникли. Яка система каже нам, що ще треба виконати, і чому це правильне місце для такої інформації?
- Dramatiq уміє сам повторювати невдалі повідомлення. Чому ми це вимкнули, і що пішло б не так, якби одну й ту саму роботу повторювали і Dramatiq, і збережений Job?
- Якщо хтось із доступом до Redis надішле повідомлення ping з `max_retries: 100`, що станеться тепер? Який тест захищає цю поведінку, коли хтось згодом змінить проміжні обробники?
- Яке спостереження довело б, що цей дизайн неправильний? Наприклад: що треба було б побачити, щоб вирішити, що Redis усе-таки мусить зберігати дані на диск?

## Чекліст завершення

- [ ] Evidence-файл містить інваріант, власників і твої прогнози, записані до змін.
- [ ] `dramatiq[redis]` є в `pyproject.toml` і `uv.lock`; у `Settings` є `redis_url` і `redis_health_timeout_seconds`.
- [ ] `make redis-up` запускає закріплений Redis на 127.0.0.1:6380 з `noeviction` і без збереження на диск.
- [ ] `broker.py` задає JSON, версійований простір імен, обидві черги, ліміти, нуль повторів і `ActorOwnsOptions` першим.
- [ ] `ping` перевіряє токен під час надсилання й отримання; `main.py` оголошує його для команди `dramatiq`.
- [ ] `make worker-ready` друкує `ready`, коли Redis працює, і виходить з ненульовим кодом у межах дедлайну, коли Redis зупинений.
- [ ] `tests/test_worker.py` (40) і `tests/test_worker_integration.py` (2) проходять.
- [ ] Справжній воркер записав твій ping у лог, а зі зупиненим Redis готовність упала.
- [ ] Навмисну поломку зроблено: впало 5 тестів, і все повернуто назад.
- [ ] `docs/architecture.md` і `docs/threat-model.md` описують воркер і локальний Redis.
- [ ] Усі перевірки проходять: форматування, lint, типи, unit, integration, docs.
- [ ] Evidence, статус завдання, рядок у PROGRESS і коміт готові.

Коли напишеш файли, поділися ними або виводом помилок, і я перевірю їх за цим чеклістом.
