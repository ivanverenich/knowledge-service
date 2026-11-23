English version: [M08-T05-coordinate-with-expiring-locks.md](M08-T05-coordinate-with-expiring-locks.md)

# M08-T05 — Координація через блокування, що спливають

Завдання: [M08-T05 — Coordinate with expiring locks](../curriculum/milestones/M08-jobs-and-confluence/M08-T05-coordinate-with-expiring-locks.md)

## Що ти зробиш

Цей урок гарантує, що два проходи синхронізації того самого джерела ніколи не воюють між собою. Короткоживуче блокування в Redis не дає другому проходу навіть почати, а перевірка в PostgreSQL гарантує, що навіть якщо блокування підведе, старіший прохід ніколи не скасує того, що записав новіший.

Конкретний приклад. О 09:00 джерело «Handbook» має `x.md` і `y.md`, обидва збережені. Далі:

| Час | Що відбувається зараз | Що відбувається після уроку |
|---|---|---|
| 09:10 | прохід A читає джерело; саме в цю мить `x.md` у ньому немає | прохід A бере оренду й огорожу джерела і читає |
| 09:10 + | прохід A зависає перед останнім кроком | прохід A зависає перед останнім кроком; його оренда спливає |
| 10:00 | прохід B читає джерело; `x.md` знову є; B завершується | прохід B бере оренду й новішу огорожу і завершується |
| 10:00 + | прохід A прокидається і позначає `x.md` видаленим | прохід A прокидається; його запис відхилено (`SynchronizationSuperseded`) |
| результат | `x.md` зник, хоча джерело його має | `x.md` лишається доступним |

| Що просить завдання | Де це зроблено |
|---|---|
| Короткоживучі блокування Redis лише там, де дубль небезпечний | «Перед початком», блок 1; Крок 6: оренду бере лише синхронізація |
| Токени власника | Крок 2: кожна оренда має випадковий токен; продовження і зняття порівнюють його всередині Redis |
| Правда в базі після закінчення оренди | Кроки 3–5: огорожа джерела в PostgreSQL, яку перевіряє кожен запис |
| Тести аварії й закінчення оренди без вічного блокування | Кроки 2 і 7: оренда, що спливла, звільняє джерело; скасований прохід повертає оренду |
| Не стверджувати, що блокування гарантують правильність | Крок 7: тести огорожі пускають кожен прохід, як пустила б оренда, що спливла, а дані лишаються правильними |
| Типізований збій або відмова | `SourceBusy`, `SourceLeaseLost`, `SourceCoordinationUnavailable`, `SynchronizationSuperseded`, `InvalidLease` |
| Явна поведінка при повторі чи скасуванні | «Перед початком», блоки 5 і 6 |
| Оновити лише ту документацію, якої це стосується | Крок 9: архітектура, модель загроз, експлуатація |

У цьому уроці є код: кроки 2–7 пишуть код, міграцію і тести. Кроки 1, 8, 9 і 10 коду не потребують, хоча на кроці 9 ти редагуєш три документи.

Терміни цього уроку:

- **Оренда (lease):** блокування, яке закінчується саме через заданий час, свій час життя (TTL), якщо власник його не продовжить. Як бронювання переговорної, що згорає, якщо ніхто його не підтвердив.
- **Токен власника (ownership token):** випадкове значення, яке знає лише власник. Продовжити чи зняти оренду можна тільки з правильним токеном, тож ніхто не завершить чужу оренду випадково.
- **Огорожа (fence):** значення в базі, яке каже, який прохід зараз може писати. Тут це час спостереження найновішого проходу, що почався.
- **Час спостереження (`observed_at`):** момент, коли прохід подивився на джерело. Пізніше спостереження — свіжіше.
- **Спільне блокування (`FOR SHARE`):** блокування рядка, яке водночас можуть тримати багато читачів, але яке змушує чекати того, хто хоче рядок змінити.

## Перед початком

Цей розділ пояснює кожне рішення, на якому стоїть код, крок за кроком. Кожен блок закінчується тим, що змінює цей урок.

### 1. Де дві копії тієї самої роботи — проблема, а де ні

**Ситуація.** Після M08-T04 Job, що впав, повертається сам, а планувальник із M08-T06 запускатиме синхронізації за таймером. Тож та сама робота тепер справді може йти двічі одночасно.

| Робота | Що зробили б дві копії одночасно | Що вже цьому заважає |
|---|---|---|
| очищення J1 | двічі видалили б ті самі фрагменти | блокування рядка Job при захопленні (M08-T03) |
| прохід повторів | двічі поставили б J1 у чергу | `SKIP LOCKED` (M08-T04) |
| relay | двічі надіслали б J1 | `SKIP LOCKED` (M08-T02) |
| запит тієї самої роботи | зберегли б два Job | унікальний індекс відкритих намірів (M08-T03) |
| **синхронізація джерела S** | двічі гортали б джерело, двічі розбирали б кожен файл, і старіший прохід міг би перезаписати новіший | **поки нічого** |

**Прихований факт.** Блокування Redis допомагає лише там, де база ще не вирішує сама. Скрізь вище PostgreSQL уже змушує другу копію нічого не робити, тож блокування там лише додало б ще один спосіб зламатися.

**Що змінюється.** Блокування отримує лише синхронізація.

### 2. Що таке оренда і навіщо їй токен

**Ситуація.** Воркер A починає синхронізувати Handbook о 10:00:00. Воркер B отримує той самий запит о 10:00:05.

| Час | Воркер A | Воркер B | Ключ Redis `lease:source-sync:<id>` |
|---|---|---|---|
| 10:00:00 | `SET key tokenA NX PX 120000`: записати, лише якщо ключа немає, і стерти через 2 хв | | `tokenA`, лишилось 2 хв |
| 10:00:05 | читає сторінку 3 | той самий `SET ... NX`: відмова, ключ існує | `tokenA` |
| 10:00:05 | | кидає `SourceBusy`, нічого не читає | `tokenA` |
| 10:00:10 | після сторінки 3: продовження, `PEXPIRE` лише якщо значення досі `tokenA` | | `tokenA`, знову 2 хв |
| 10:01:00 | кінець; зняття: `DEL` лише якщо значення досі `tokenA` | | немає |

Якби A впав о 10:00:30 і не зняв оренду, ключ зник би сам о 10:02:30, і наступний прохід пішов би. Саме це означає «без вічного блокування».

**Прихований факт.** «Перевірити токен, потім видалити» має відбутися одним кроком усередині Redis. У два кроки між ними є щілина:

| Момент | Воркер A (оренда ось-ось спливе) | Воркер B | Ключ |
|---|---|---|---|
| 1 | `GET key` повертає `tokenA`: «досі моя» | | `tokenA` |
| 2 | (на мить затримується) | | спливає |
| 3 | | `SET key tokenB NX`: успіх | `tokenB` |
| 4 | `DEL key`: видаляє оренду B | | немає; B думає, що тримає її |

Короткий Lua-скрипт виконується всередині Redis одним кроком, тож між перевіркою і видаленням нічого не може статися:

```python
# Two steps: a gap between the check and the delete.
if await client.get(key) == token:
    await client.delete(key)

# One step, inside Redis.
await client.eval(_RELEASE, 1, key, token)
```

Уяви картку бронювання переговорної з твоїм іменем і часом закінчення: будь-хто може зайняти кімнату, коли час минув, і лише ім'я на картці може продовжити чи скасувати бронь.

**Що змінюється.** `leases.py` — невеликий модуль з `acquire`, `renew` і `release`. Імена й TTL перевіряються, виклики Redis обмежені тайм-аутами, а будь-який збій Redis стає `LeaseStoreUnavailable`.

### 3. Знахідка рев'ю: оренда не може тримати дані правильними

**Ситуація.** Перший прототип цього уроку спирався лише на оренду, і документація казала «два проходи — марнотратство, а не помилка». Адверсаріальне рев'ю довело протилежне на справжніх PostgreSQL і Redis. Ось випадок із таблиці на початку, крок за кроком:

| Час | Старіший прохід A (спостерігав о 09:10, бачить лише `y.md`) | Новіший прохід B (спостерігав о 10:00, бачить `x.md` і `y.md`) | `x.md` у базі |
|---|---|---|---|
| 09:10 | бере оренду, читає, продовжує її після останньої сторінки | | доступний, `updated_at` 09:00 |
| 09:10 + | зупиняється: повільна сторінка, пауза збирача сміття, очікування з'єднання з базою | | доступний |
| 09:12 | | оренда спливла: бере її, читає, бачить `x.md` без змін, нічого не пише | доступний, `updated_at` досі 09:00 |
| 10:00 + | прокидається; позначає видаленим кожен документ, якого не бачив | | **позначений видаленим**, його очищення в черзі |

**Приховані факти.** Їх два.

1. Оренда — це перевірка, а не бар'єр. Прохід перевіряє «я досі тримаю», а потім пише, і будь-що може зупинити його посередині. Оренда може спливти саме в цій щілині.
2. Старий захист у `tombstone_missing` зберігає документ, «оновлений після мого спостереження». Але прохід, що бачить файл без змін, нічого не пише, тож `updated_at` лишається 09:00, і 09:10 старішого проходу виглядає новішим.

**Наскільки це погано.** Рев'ю знайшло дві шкоди. Перша: документ, який джерело досі має, зникає з пошуку, а його фрагменти очищуються до наступного синку. Друга стосується безпеки: старіший прохід, чия перша сторінка прийшла пізно, записав права доступу вже після того, як новіший прохід прибрав читача, тож той читач знову бачив документ. Обидві вади існували в `synchronize_source` і до цього уроку; оренда лише робила їх рідшими.

**Що змінюється.** Оренда лишається, щоб економити роботу. Дані захищає огорожа в PostgreSQL (блок 4).

### 4. Огорожа: хто може писати, вирішує база

**Ситуація.** Ті самі два проходи, але тепер з огорожею в рядку джерела.

| Час | Старіший прохід A (09:10) | Новіший прохід B (10:00) | `sources.synchronization_fence` |
|---|---|---|---|
| 09:10 | займає: `UPDATE ... SET fence = 09:10 WHERE fence < 09:10` вдається | | 09:10 |
| 09:10 | кожен запис спершу перевіряє `fence = 09:10` під спільним блокуванням: так, пише | | 09:10 |
| 09:10 + | зупиняється | | 09:10 |
| 09:12 | | займає: `09:10 < 10:00`, вдається; його записи перевіряють `fence = 10:00` | 10:00 |
| 10:00 + | прокидається; запис позначення видаленими перевіряє `fence = 09:10`: **ні** | | 10:00 |
| 10:00 + | кидає `SynchronizationSuperseded`; нічого не записано | | 10:00 |

**Прихований факт.** Чому зайняття огорожі проходом B не може проскочити між перевіркою і записом A? Бо A перевіряє всередині тієї самої транзакції, що пише, і тримає при цьому спільне блокування рядка джерела:

```sql
-- Every write of a pass starts with this, in its own transaction:
SELECT synchronization_fence FROM sources WHERE source_id = :id FOR SHARE;
-- ...then, if the fence is still this pass's observation time, it writes.

-- A newer pass claims the fence like this:
UPDATE sources SET synchronization_fence = :observed_at
WHERE source_id = :id
  AND (synchronization_fence IS NULL OR synchronization_fence < :observed_at)
RETURNING source_id;
```

| Блокування, яке тримає запис A | Зайняття B (`UPDATE`) | Інший запис A |
|---|---|---|
| `FOR SHARE` на рядку джерела | чекає, доки запис A закомітиться | іде далі: спільні блокування одне одному не заважають |

Отже, кожен запис A або комітиться до зайняття B, або бачить огорожу B і відхиляється. Третього не дано. Ось що означає «правда в базі після закінчення оренди»: оренда може спливти, огорожа — ні.

```python
# Before: a reconciliation writes whatever it read.
async with runtime.transaction() as connection:
    stored = await load_document(connection, ..., for_update=True)

# After: it first checks it still holds the fence.
async with runtime.transaction() as connection:
    if guard is not None:
        await guard(connection)  # raises SynchronizationSuperseded if not
    stored = await load_document(connection, ..., for_update=True)
```

Огорожа — частина кожного проходу `synchronize_source`, а не лише тих, що мають оренду. Рев'ю показало, що прохід без огорожі міг би перезаписати старіший прохід з огорожею.

Уяви номерки в черзі до прилавка: обслуговують лише номер на табло. Коли висвічується новіший номер, старий квиток відхиляють, хоч скільки його власник чекав.

**Що змінюється.** З'являються `sources.synchronization_fence` і дві невеликі функції бази, `claim_synchronization_fence` і `synchronization_fence_holds`. `reconcile_document`, `narrow_access` і `tombstone_missing` приймають `guard` і виконують його першим у своїй транзакції. `synchronize_source` займає огорожу, перш ніж щось читати, і передає `guard` усім трьом.

### 5. Які збої повторюються

**Ситуація.** Job синхронізації тепер може натрапити на чотири нові помилки.

| Помилка | Коли | Код (M08-T04) | Повторюється? |
|---|---|---|---|
| `SourceBusy` | оренду тримає інший прохід | `source_unavailable` | так, пізніше |
| `SourceLeaseLost` | під час продовження оренди вже не було; прохід зупинився до позначення видаленими | `source_unavailable` | так, пізніше |
| `SourceCoordinationUnavailable` | Redis не відповів | `source_unavailable` | так, пізніше |
| `SynchronizationSuperseded` | почався прохід з тим самим або новішим часом спостереження | `superseded`, новий | **ні** |

**Прихований факт.** Прохід, якого витіснили, ніколи не вдасться при повторі з тим самим `observed_at`: огорожа приймає лише новіший час. І переробляти нічого, бо новіший прохід уже покриває роботу. Рев'ю виявило, що як `SourceUnavailable` така помилка спалила б увесь бюджет спроб, тож тепер вона має власний постійний код. Job, що повторює синхронізацію, мусить знову подивитися на джерело, з новим `observed_at`.

### 6. Аварія, закінчення оренди, скасування: ніщо не блокує джерело назавжди

| Що стається | Оренда | Огорожа |
|---|---|---|
| прохід падає після взяття оренди | спливає протягом 2 хвилин | лишається на часі проходу, що впав; будь-який новіший прохід її займе |
| прохід скасовано | знімається у `finally` | як вище |
| Redis лежить, коли прохід закінчується | не знята; спливе сама | не стосується |
| продовження виявляє, що оренди вже немає | прохід зупиняється до позначення видаленими | його попередні записи лишаються, доки новіший прохід їх не перезапише |
| викликач передає `observed_at` у 2099 році | | відхилено (`InvalidSource`); інакше огорожа відмовляла б кожному справжньому проходу до 2099 року |

Останній рядок — шлях до вічного блокування, який могла б створити сама огорожа, тому `synchronize_source` відхиляє `observed_at`, що більш ніж на п'ять хвилин випереджає годинник машини.

### 7. Відомі обмеження

Ці речі поки лишаються як є:

- Скасування проходу саме під час взяття оренди може лишити ключ ні за ким аж до кінця його TTL, до 2 хвилин.
- Скасування під час фінального зняття оренди витісняє власну помилку проходу, і та лишається лише як контекст винятку. Оренда однаково спливе.
- `SourceBusy` витрачає одну спробу з бюджету Job; планувальнику з M08-T06 варто не ставити в чергу синхронізацію, яка вже йде.
- Сторінка, що займає довше за 2 хвилини оренди, втрачає оренду, і прохід зупиняється на наступному продовженні. Дані лишаються правильними; роботу буде зроблено ще раз.
- Записи, які старіший прохід закомітив до того, як новіший зайняв огорожу, лишаються, доки новіший прохід їх не перезапише, і лишаються, якщо новіший прохід упаде посередині.
- `observed_at`, що до п'яти хвилин випереджає годинник, тримає огорожу до цього часу.

## Покрокова інструкція

Перед початком запусти PostgreSQL і Redis (`make redis-up`). Усі команди виконуються з кореня репозиторію. Для integration-перевірок задай тестові URL у своєму терміналі:

```bash
export KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)"
export KNOWLEDGE_SERVICE_TEST_REDIS_URL="redis://127.0.0.1:6380/15"
```

Запускай проти цієї бази лише одну integration-команду за раз.

### Крок 1 — У цьому кроці коду немає: запиши інваріант і свої прогнози

Перш ніж щось змінювати, запиши, що має бути правдою наприкінці і чого ти очікуєш. Прогнози перевіриш на кроках 8 і 10.

Створи `docs/evidence/M08-T05-coordinate-with-expiring-locks.md`:

```markdown
# M08-T05 — Coordinate with expiring locks evidence

## Invariant

At most one synchronization pass of a Source runs at a time while its lease
holds; a lease expires on its own and only its token's holder can renew or
release it. Whatever the lease does, only the pass with the newest
observation time may write a Source's Documents: every narrowing,
reconciliation, and tombstone checks the Source's fence in PostgreSQL inside
its own transaction, under a share lock, so no write of an older pass lands
after a newer pass claimed the fence.

## Owners

- Avoiding duplicate work: the Source's lease in Redis (`leases`,
  `ingestion.exclusive_sync`).
- Correctness: the Source's fence in PostgreSQL (`synchronize_source`).
- Retries: the stored Job (M08-T04); a superseded pass is never retried.
- Authorization: none; nothing outside the service starts a pass.

## Expected failing check

Before the change,
`uv run pytest tests/test_leases_integration.py -q -m integration`
reports `no tests ran`, because the file does not exist.

## Predictions

- What a second pass gets while another pass of the same Source holds the lease:
- What happens to a lease whose holder crashed, and after how long:
- What an older pass that wakes up after a newer pass finished does when it tries to tombstone:
- How many tests fail if `synchronize_source` stops passing its guard to the writes:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після двокрапки в кожному прогнозі стоїть твоя відповідь.

### Крок 2 — Крок з кодом: модуль оренди

Цей крок додає невеликий модуль, що бере, продовжує і знімає оренди в Redis.

**Простими словами:** взяти оренду означає записати ключ у Redis, лише якщо його ще немає, з випадковим токеном і часом закінчення. Продовження відсуває час закінчення, а зняття видаляє ключ, але обидва лише тоді, коли ключ досі тримає токен цього власника. Якщо Redis не відповідає дві секунди, кожен виклик падає з однією зрозумілою помилкою замість зависати.

| Частина | Що робить |
|---|---|
| `Lease` | ім'я, токен власника і TTL |
| `LeaseStore` | порт: `acquire`, `renew`, `release` |
| `RedisLeaseStore` | `SET NX PX`, щоб узяти; Lua-скрипти, що порівнюють токен, щоб продовжити і зняти |
| `check_name`, `check_ttl` | імена — від 1 до 200 малих літер, цифр, `:_-`; TTL від 100 мс до 10 хвилин |
| `lease_client` | asyncio-клієнт Redis з 2-секундними тайм-аутами і без прихованих повторів |
| `LeaseStoreUnavailable`, `InvalidLease` | типізовані помилки |

`renew` ще й перевіряє TTL оренди, бо вручну зібраний `Lease` з нульовим TTL інакше видалив би ключ, а з десятирічним — тримав би його майже назавжди.


**Дія 1:** створи модуль.

Створи `src/knowledge_service/leases.py`:

```python
"""Short-lived, owned locks in Redis: leases.

A lease lets one holder at a time do work that is wasteful to duplicate. It
expires on its own, so a holder that crashes cannot lock the work out for
good. Its holder gets a random token, and renewing or releasing checks that
token inside Redis, so a holder whose lease already expired can never extend
or delete the next holder's lease.

A lease is a hint, never the truth. It can expire while its holder is still
working, after a long pause or a slow call, and then two holders run at once.
Anything that must stay correct is decided by PostgreSQL (ADR 0011).
"""

import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Protocol, cast

import redis
import redis.asyncio
from redis.backoff import NoBackoff
from redis.retry import Retry

NAMESPACE: Final = "knowledge-service.v1:lease:"
MIN_TTL: Final = timedelta(milliseconds=100)
MAX_TTL: Final = timedelta(minutes=10)
REDIS_CONNECT_TIMEOUT_SECONDS: Final = 2.0
REDIS_SOCKET_TIMEOUT_SECONDS: Final = 2.0
_NAME = re.compile(r"[a-z0-9][a-z0-9:_-]{0,199}")

# Extend or delete the key only while it still holds this holder's token.
# Each runs inside Redis as one step, so nothing can change the key between
# the check and the write.
_RENEW: Final = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('pexpire', KEYS[1], ARGV[2])
end
return 0
"""
_RELEASE: Final = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


class InvalidLease(ValueError):
    """A lease name or time to live is outside what this module allows."""


class LeaseStoreUnavailable(RuntimeError):
    """The lease store did not answer, so no lease was taken or changed."""


@dataclass(frozen=True, slots=True)
class Lease:
    """A held lease: its name, its holder's token, and how long it lives."""

    name: str
    token: str
    ttl: timedelta


class LeaseStore(Protocol):
    """Takes, renews, and releases leases."""

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
        """Take `name` for `ttl`, or None when another holder has it."""
        ...

    async def renew(self, lease: Lease) -> bool:
        """Restart `lease`'s time to live; False when it is no longer held."""
        ...

    async def release(self, lease: Lease) -> bool:
        """Give `lease` up; False when it was no longer held."""
        ...


def check_name(name: str) -> str:
    """The name itself, or InvalidLease when it is not a short, safe key."""
    if type(name) is not str or _NAME.fullmatch(name) is None:
        raise InvalidLease("a lease name is 1 to 200 lowercase letters, digits, ':_-'")
    return name


def check_ttl(ttl: timedelta) -> timedelta:
    """The time to live itself, or InvalidLease when it is out of bounds."""
    if type(ttl) is not timedelta or not MIN_TTL <= ttl <= MAX_TTL:
        raise InvalidLease(f"a lease lives from {MIN_TTL} to {MAX_TTL}")
    return ttl


def _milliseconds(ttl: timedelta) -> int:
    return ttl // timedelta(milliseconds=1)


def lease_client(
    redis_url: str,
    *,
    connect_timeout_seconds: float = REDIS_CONNECT_TIMEOUT_SECONDS,
    socket_timeout_seconds: float = REDIS_SOCKET_TIMEOUT_SECONDS,
) -> redis.asyncio.Redis:
    """An asyncio Redis client whose every call makes one bounded attempt."""
    # redis-py leaves the keyword arguments of from_url untyped.
    return redis.asyncio.Redis.from_url(  # pyright: ignore[reportUnknownMemberType]
        redis_url,
        socket_connect_timeout=connect_timeout_seconds,
        socket_timeout=socket_timeout_seconds,
        retry=Retry(NoBackoff(), 0),
    )


@dataclass(frozen=True, slots=True)
class RedisLeaseStore:
    """Leases as Redis keys that expire, holding their holder's token."""

    client: redis.asyncio.Redis

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
        lease = Lease(
            name=check_name(name), token=secrets.token_hex(16), ttl=check_ttl(ttl)
        )
        try:
            taken = await self.client.set(
                NAMESPACE + lease.name,
                lease.token,
                nx=True,
                px=_milliseconds(lease.ttl),
            )
        except (redis.RedisError, OSError) as error:
            raise LeaseStoreUnavailable("the lease store did not answer") from error
        return lease if taken else None

    async def renew(self, lease: Lease) -> bool:
        ttl = _milliseconds(check_ttl(lease.ttl))
        return await self._run(_RENEW, lease, str(ttl)) == 1

    async def release(self, lease: Lease) -> bool:
        return await self._run(_RELEASE, lease) == 1

    async def _run(self, script: str, lease: Lease, *extra: str) -> int:
        try:
            # redis-py types eval loosely; the scripts above return integers.
            result = await self.client.eval(  # pyright: ignore[reportUnknownMemberType]
                script, 1, NAMESPACE + check_name(lease.name), lease.token, *extra
            )
        except (redis.RedisError, OSError) as error:
            raise LeaseStoreUnavailable("the lease store did not answer") from error
        return int(cast(int, result))
```

**Дія 2:** протестуй його на справжньому Redis: один власник, власник, що впав, продовження, застарілий токен, випадкові токени, недоступне сховище і вручну зібрані оренди.

Створи `tests/test_leases_integration.py`:

```python
"""Leases against a real Redis: one holder, expiry, and owner tokens."""

import asyncio
import os
import socket
import time
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest

from knowledge_service.leases import (
    InvalidLease,
    Lease,
    LeaseStoreUnavailable,
    RedisLeaseStore,
    lease_client,
)

pytestmark = pytest.mark.integration

REDIS_URL_ENV = "KNOWLEDGE_SERVICE_TEST_REDIS_URL"
SHORT = timedelta(milliseconds=300)
LONG = timedelta(seconds=30)


@pytest.fixture
def redis_url() -> str:
    configured = os.environ.get(REDIS_URL_ENV)
    if not configured:
        pytest.skip(f"set {REDIS_URL_ENV} to a disposable Redis server")
    return configured


@pytest.fixture
async def store(redis_url: str) -> AsyncIterator[RedisLeaseStore]:
    client = lease_client(redis_url)
    try:
        yield RedisLeaseStore(client)
    finally:
        await client.aclose()


def unique_name() -> str:
    return f"test:{uuid.uuid4().hex}"


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def test_one_holder_at_a_time(store: RedisLeaseStore) -> None:
    name = unique_name()

    first = await store.acquire(name, LONG)
    second = await store.acquire(name, LONG)
    assert first is not None
    assert second is None

    assert await store.release(first)
    assert await store.acquire(name, LONG) is not None


async def test_a_crashed_holder_does_not_lock_the_work_out(
    store: RedisLeaseStore,
) -> None:
    name = unique_name()
    # The holder takes the lease and never releases it, as if it crashed.
    assert await store.acquire(name, SHORT) is not None
    assert await store.acquire(name, SHORT) is None

    await asyncio.sleep(0.45)

    assert await store.acquire(name, SHORT) is not None


async def test_renewing_keeps_a_lease_past_its_first_ttl(
    store: RedisLeaseStore,
) -> None:
    name = unique_name()
    lease = await store.acquire(name, SHORT)
    assert lease is not None

    for _ in range(4):
        await asyncio.sleep(0.15)
        assert await store.renew(lease)

    # 0.6 s later it is still held, twice its time to live.
    assert await store.acquire(name, SHORT) is None


async def test_an_expired_holder_cannot_renew_or_release_the_next_ones_lease(
    store: RedisLeaseStore,
) -> None:
    name = unique_name()
    stale = await store.acquire(name, SHORT)
    assert stale is not None
    await asyncio.sleep(0.45)
    current = await store.acquire(name, LONG)
    assert current is not None

    assert not await store.renew(stale)
    assert not await store.release(stale)

    assert await store.acquire(name, LONG) is None
    assert await store.renew(current)
    assert await store.release(current)


async def test_every_lease_gets_its_own_random_token(store: RedisLeaseStore) -> None:
    tokens: list[str] = []
    for _ in range(3):
        lease = await store.acquire(unique_name(), LONG)
        assert lease is not None
        tokens.append(lease.token)
        await store.release(lease)

    assert len(set(tokens)) == 3
    assert all(len(token) == 32 for token in tokens)


async def test_an_unreachable_store_is_a_typed_failure_within_its_timeouts() -> None:
    client = lease_client(f"redis://127.0.0.1:{closed_port()}/0")
    store = RedisLeaseStore(client)
    started = time.monotonic()
    try:
        with pytest.raises(LeaseStoreUnavailable):
            await store.acquire(unique_name(), LONG)
    finally:
        await client.aclose()

    assert time.monotonic() - started < 5.0


@pytest.mark.parametrize(
    "ttl", [timedelta(0), -timedelta(seconds=1), timedelta(days=3650)]
)
async def test_a_hand_built_lease_cannot_renew_outside_the_ttl_bounds(
    store: RedisLeaseStore, ttl: timedelta
) -> None:
    held = await store.acquire(unique_name(), LONG)
    assert held is not None
    forged = Lease(name=held.name, token=held.token, ttl=ttl)

    with pytest.raises(InvalidLease):
        await store.renew(forged)

    # The real lease is untouched: still held, with its own time to live.
    assert await store.acquire(held.name, LONG) is None
    assert await store.renew(held)
```

**Перевірка:** `uv run pytest tests/test_leases_integration.py -q -m integration` показує `9 passed`.

### Крок 3 — Крок з кодом: огорожа в базі

Цей крок додає колонку, що каже, який прохід може писати в джерело, і два запити, що нею користуються.

**Простими словами:** кожне джерело отримує ще одне значення: час спостереження найновішого проходу, що почався. Прохід може зайняти його лише з новішим часом. Будь-який запис може спитати «вона досі моя?», і поки питає, тримає блокування, яке змушує новіше зайняття чекати, доки запис не завершиться.

| Частина | Що робить |
|---|---|
| `sources.synchronization_fence` | час спостереження найновішого проходу; порожній до першого проходу |
| Міграція `a7b8c9d0e1f2` | додає її |
| `claim_synchronization_fence` | `UPDATE ... WHERE fence < :observed_at RETURNING` з блоку 4; true, якщо вдалося |
| `synchronization_fence_holds` | `SELECT ... FOR SHARE`; true, якщо огорожа досі має час цього проходу |
| `WriteGuard` | тип перевірки, що виконується першою всередині транзакції запису |


**Дія 1:** створи міграцію.

Створи `migrations/versions/a7b8c9d0e1f2_source_synchronization_fence.py`:

```python
"""source synchronization fence

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-11-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"


def upgrade() -> None:
    # Empty for every Source: the first fenced pass claims it.
    op.add_column(
        "sources",
        sa.Column("synchronization_fence", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sources", "synchronization_fence")
```

**Дія 2:** оголоси колонку в таблиці `sources`:

У `src/knowledge_service/persistence/schema.py` знайди:

```python
    sa.Column("checkpoint", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("kind", "location", name="uq_sources_kind_location"),
)
```

і заміни на:

```python
    sa.Column("checkpoint", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    # The observation time of the newest pass that claimed this Source; only
    # that pass may write its Documents (`ingestion.synchronization`).
    sa.Column("synchronization_fence", sa.DateTime(timezone=True), nullable=True),
    sa.UniqueConstraint("kind", "location", name="uq_sources_kind_location"),
)
```

**Дія 3:** файл короткий; заміни його цілком.

Заміни весь `src/knowledge_service/persistence/sources.py` на:

```python
"""Statements over Sources, their synchronization runs, and their fence."""

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.identifiers import SourceId
from knowledge_service.persistence.schema import sources, synchronization_runs


def source_runs_statement(source_id: UUID, limit: int) -> sa.Select[Any]:
    """Select the most recent Synchronization Runs of one Source."""
    return (
        sa.select(synchronization_runs)
        .where(synchronization_runs.c.source_id == source_id)
        .order_by(synchronization_runs.c.started_at.desc())
        .limit(limit)
    )


async def claim_synchronization_fence(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> bool:
    """Make `observed_at` the Source's fence, if it is newer than the fence.

    False when a pass that observed the Source at the same time or later
    already claimed it. The update waits for every write that holds the
    fence (`synchronization_fence_holds`), so none of them lands after it.
    """
    claimed = await connection.scalar(
        sa.update(sources)
        .where(
            sources.c.source_id == source_id.value,
            sa.or_(
                sources.c.synchronization_fence.is_(None),
                sources.c.synchronization_fence < observed_at,
            ),
        )
        .values(synchronization_fence=observed_at)
        .returning(sources.c.source_id)
    )
    return claimed is not None


async def synchronization_fence_holds(
    connection: AsyncConnection, *, source_id: SourceId, observed_at: datetime
) -> bool:
    """Whether the pass that observed at `observed_at` still holds the fence.

    The Source's row is share-locked until the transaction ends, so a newer
    pass cannot claim the fence while this write is in progress.
    """
    fence = await connection.scalar(
        sa.select(sources.c.synchronization_fence)
        .where(sources.c.source_id == source_id.value)
        .with_for_update(read=True)
    )
    return fence == observed_at
```

**Дія 4:** створи тип `guard`. Він живе в окремому модулі, бо `reconciliation` уже імпортує `deletions`, а він потрібен обом.

Створи `src/knowledge_service/ingestion/fencing.py`:

```python
"""A check that runs first inside each write of a synchronization pass.

A guard is awaited at the start of a write's own transaction. It may take
locks and read the database, and raising from it rolls the write back, so
nothing the pass writes can land after the guard says it should not.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncConnection

type WriteGuard = Callable[[AsyncConnection], Awaitable[None]]
```

**Перевірка:** `uv run pytest tests/test_migrations_integration.py -q -m integration` показує `3 passed`.

### Крок 4 — Крок з кодом: нехай кожен запис спершу перевіряє guard

Цей крок дає трьом функціям, що пишуть документи джерела, необов'язковий `guard`, який виконується першим у їхній транзакції.

**Простими словами:** звіряння документа, звуження того, хто може його читати, і позначення документів видаленими — кожне відкриває транзакцію. Тепер кожне з них спершу питає `guard`, якщо його передали, і якщо `guard` каже «ні», нічого не записується. Викликачі без `guard` працюють точно як раніше.

| Функція | Що змінюється |
|---|---|
| `reconcile_document` | `guard=None`; виконується першим у транзакції |
| `narrow_access` | те саме |
| `tombstone_missing` | те саме |


**Дія 1:** у `reconciliation.py` імпортуй тип:

У `src/knowledge_service/ingestion/reconciliation.py` знайди:

```python
from knowledge_service.ingestion.audit_events import access_event
from knowledge_service.ingestion.chunking import chunk_by_structure
from knowledge_service.ingestion.deletions import availability_event
from knowledge_service.ingestion.fingerprints import (
    authorization_fingerprint,
    content_fingerprint,
```

і заміни на:

```python
from knowledge_service.ingestion.audit_events import access_event
from knowledge_service.ingestion.chunking import chunk_by_structure
from knowledge_service.ingestion.deletions import availability_event
from knowledge_service.ingestion.fencing import WriteGuard
from knowledge_service.ingestion.fingerprints import (
    authorization_fingerprint,
    content_fingerprint,
```

Дай `reconcile_document` параметр:

У `src/knowledge_service/ingestion/reconciliation.py` знайди:

```python
    readers: Sequence[Subject],
    observed_at: datetime,
    run_id: SynchronizationRunId | None = None,
) -> Reconciliation:
    """Publish one observation of a document, writing only what changed.

    `readers` are the normalized subjects who may read it; an empty sequence
    means nobody may. `run_id` names the Synchronization Run in the audit
    event of a grant change. An observation older than the stored version
    raises `InvalidDocument`, and nothing is written.
    """
    provenance = normalized.provenance
    content = content_fingerprint(normalized.parsed)
```

і заміни на:

```python
    readers: Sequence[Subject],
    observed_at: datetime,
    run_id: SynchronizationRunId | None = None,
    guard: WriteGuard | None = None,
) -> Reconciliation:
    """Publish one observation of a document, writing only what changed.

    `readers` are the normalized subjects who may read it; an empty sequence
    means nobody may. `run_id` names the Synchronization Run in the audit
    event of a grant change. An observation older than the stored version
    raises `InvalidDocument`, and nothing is written. `guard` runs first in
    the transaction; raising from it writes nothing.
    """
    provenance = normalized.provenance
    content = content_fingerprint(normalized.parsed)
```

і виконуй його першим у транзакції:

У `src/knowledge_service/ingestion/reconciliation.py` знайди:

```python
        document_id=provisional, grants=_grants(provisional, readers, observed_at)
    )
    async with runtime.transaction() as connection:
        # Lock the stored row: a concurrent run of the same document waits
        # here until this one commits, then decides from what it committed.
        stored = await load_document(
```

і заміни на:

```python
        document_id=provisional, grants=_grants(provisional, readers, observed_at)
    )
    async with runtime.transaction() as connection:
        if guard is not None:
            await guard(connection)
        # Lock the stored row: a concurrent run of the same document waits
        # here until this one commits, then decides from what it committed.
        stored = await load_document(
```

Дай `narrow_access` параметр:

У `src/knowledge_service/ingestion/reconciliation.py` знайди:

```python
    readers: Sequence[Subject],
    observed_at: datetime,
    run_id: SynchronizationRunId | None = None,
) -> bool:
    """Keep a stored Document only for readers both old and new access name.
```

і заміни на:

```python
    readers: Sequence[Subject],
    observed_at: datetime,
    run_id: SynchronizationRunId | None = None,
    guard: WriteGuard | None = None,
) -> bool:
    """Keep a stored Document only for readers both old and new access name.
```

і виконуй його першим у транзакції:

У `src/knowledge_service/ingestion/reconciliation.py` знайди:

```python
    normalizes again. Returns whether the Access Grants changed.
    """
    async with runtime.transaction() as connection:
        stored = await load_document(
            connection, source_id=source_id, external_id=external_id, for_update=True
        )
```

і заміни на:

```python
    normalizes again. Returns whether the Access Grants changed.
    """
    async with runtime.transaction() as connection:
        if guard is not None:
            await guard(connection)
        stored = await load_document(
            connection, source_id=source_id, external_id=external_id, for_update=True
        )
```

**Дія 2:** у `deletions.py` імпортуй тип:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import AuditEventId, DocumentId, JobId, SourceId
from knowledge_service.job_failures import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
```

і заміни на:

```python
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import Document, DocumentAvailability
from knowledge_service.identifiers import AuditEventId, DocumentId, JobId, SourceId
from knowledge_service.ingestion.fencing import WriteGuard
from knowledge_service.job_failures import (
    DEFAULT_RETRY_POLICY,
    RetryPolicy,
```

і дай `tombstone_missing` параметр, що виконується першим у транзакції:

У `src/knowledge_service/ingestion/deletions.py` знайди:

```python
    source_id: SourceId,
    seen: Collection[str],
    observed_at: datetime,
) -> tuple[Tombstone, ...]:
    """Tombstone every available Document of a Source whose ID is not in `seen`.

    `seen` must hold every external ID of one complete pass over the Source;
    after a partial pass, documents that were simply not reached yet would be
    tombstoned. A Document updated after `observed_at` was seen by a later
    observation, so it is kept.
    """
    seen = frozenset(seen)
    tombstones: list[Tombstone] = []
    async with runtime.transaction() as connection:
        available = await load_available_documents(connection, source_id=source_id)
        for candidate in available:
            if candidate.provenance.external_id in seen:
```

і заміни на:

```python
    source_id: SourceId,
    seen: Collection[str],
    observed_at: datetime,
    guard: WriteGuard | None = None,
) -> tuple[Tombstone, ...]:
    """Tombstone every available Document of a Source whose ID is not in `seen`.

    `seen` must hold every external ID of one complete pass over the Source;
    after a partial pass, documents that were simply not reached yet would be
    tombstoned. A Document updated after `observed_at` was seen by a later
    observation, so it is kept. `guard` runs first in the transaction;
    raising from it tombstones nothing.
    """
    seen = frozenset(seen)
    tombstones: list[Tombstone] = []
    async with runtime.transaction() as connection:
        if guard is not None:
            await guard(connection)
        available = await load_available_documents(connection, source_id=source_id)
        for candidate in available:
            if candidate.provenance.external_id in seen:
```

**Перевірка:** `uv run pyright src/knowledge_service/ingestion` показує 0 помилок, а `uv run pytest tests/test_deletions_integration.py -q -m integration` показує `9 passed`.

### Крок 5 — Крок з кодом: кожен прохід синхронізації займає і тримає огорожу

Цей крок робить огорожу частиною кожного проходу `synchronize_source`, додає heartbeat, яким користуватиметься оренда, і дає витісненому проходу власний код збою.

**Простими словами:** перш ніж щось читати, прохід перевіряє, що його час спостереження розумний, і займає огорожу джерела; якщо новіший прохід уже її має, він одразу зупиняється. Кожен запис проходу потім виконує `guard` з блоку 4. Після кожної сторінки прохід викликає необов'язковий heartbeat, через який наступний крок продовжує оренду. Витіснений прохід повідомляється з новим кодом, який ніколи не повторюється.

| Частина | Що змінюється |
|---|---|
| `SynchronizationSuperseded` | новий `SourceError` у `ingestion/sources.py` |
| `MAX_CLOCK_SKEW` | п'ять хвилин; пізніший `observed_at` відхиляється |
| `_claim_fence` | перевіряє UTC і випередження, потім займає огорожу у власній транзакції |
| `synchronize_source` | займає огорожу, будує `guard`, передає його і `heartbeat` далі |
| `_publish`, `_revoke_first` | передають `guard` кожному запису; викликають `heartbeat` після кожної сторінки |
| `JobFailure.SUPERSEDED` | восьмий код, постійний |


**Дія 1:** додай помилку поруч з іншими помилками джерела:

У `src/knowledge_service/ingestion/sources.py` знайди:

```python
    """The Source refused the configured credentials."""


class SourceKind(StrEnum):
    """The kind of origin a Source reads from."""
```

і заміни на:

```python
    """The Source refused the configured credentials."""


class SynchronizationSuperseded(SourceError):
    """A pass that observed the Source at the same time or later has started.

    This pass writes nothing more, and trying it again cannot help: only a
    pass with a newer observation time may write the Source's Documents.
    """


class SourceKind(StrEnum):
    """The kind of origin a Source reads from."""
```

**Дія 2:** у `synchronization.py` спершу docstring модуля та імпорти:

У `src/knowledge_service/ingestion/synchronization.py` знайди:

```python
error's type name, in a transaction of its own. A pass that is cancelled
records nothing; a failed pass whose record cannot be written logs that and
raises its own error.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from knowledge_service.audit import (
    AuditAction,
```

і заміни на:

```python
error's type name, in a transaction of its own. A pass that is cancelled
records nothing; a failed pass whose record cannot be written logs that and
raises its own error.

A caller may pass a `heartbeat`, awaited after every page and so also just
before the tombstones. Raising from it stops the pass there, as a failure:
nothing is tombstoned. `synchronize_source_exclusively` uses it to keep its
lease.

Every pass is fenced. Before it reads anything it claims the Source's fence
with its observation time, which works only when that time is newer than
every earlier pass's, and every transaction that writes a Document (each
narrowing, each reconciliation, the tombstones) first checks, under a share
lock on the Source's row, that it still holds the fence. A newer claim
waits for those writes, and after it the older pass's writes are refused
with `SynchronizationSuperseded`. So an older pass that finishes late can
never undo a newer one. A pass observed more than `MAX_CLOCK_SKEW` in the
future is refused, so a bad clock cannot hold the fence for long.
"""

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.audit import (
    AuditAction,
```

імпортуй тип `guard`:

У `src/knowledge_service/ingestion/synchronization.py` знайди:

```python
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.grant_normalization import (
    NormalizedAccess,
    PrincipalDirectory,
```

і заміни на:

```python
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.fencing import WriteGuard
from knowledge_service.ingestion.grant_normalization import (
    NormalizedAccess,
    PrincipalDirectory,
```

імпортуй помилку й запити огорожі та додай межу випередження:

У `src/knowledge_service/ingestion/synchronization.py` знайди:

```python
    SourcePort,
    SourceRecord,
    SourceRecordState,
)
from knowledge_service.persistence.access import load_readers
from knowledge_service.persistence.audit import record_audit_event

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
```

і заміни на:

```python
    SourcePort,
    SourceRecord,
    SourceRecordState,
    SynchronizationSuperseded,
)
from knowledge_service.persistence.access import load_readers
from knowledge_service.persistence.audit import record_audit_event
from knowledge_service.persistence.sources import (
    claim_synchronization_fence,
    synchronization_fence_holds,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
# How far ahead of this machine's clock an observation may be. A fence set
# far in the future would refuse every real pass until that time came.
MAX_CLOCK_SKEW = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
```

Додай параметр `heartbeat`:

У `src/knowledge_service/ingestion/synchronization.py` знайди:

```python
    principal_directory: PrincipalDirectory | None,
    page_size: int = PAGE_SIZE,
    run_id: SynchronizationRunId | None = None,
) -> SynchronizationSummary:
    """Publish every record the Source offers, then tombstone the rest.
```

і заміни на:

```python
    principal_directory: PrincipalDirectory | None,
    page_size: int = PAGE_SIZE,
    run_id: SynchronizationRunId | None = None,
    heartbeat: Callable[[], Awaitable[None]] | None = None,
) -> SynchronizationSummary:
    """Publish every record the Source offers, then tombstone the rest.
```

У `synchronize_source` займи огорожу і збудуй `guard`:

У `src/knowledge_service/ingestion/synchronization.py` знайди:

```python
    None, only public items are readable. A Source that returns a checkpoint
    it returned before raises `InvalidSource` instead of paging forever.
    The pass runs as `run_id`, or as a new run, and records how it ended.
    """
    run = SynchronizationRunId.new() if run_id is None else run_id
    try:
        summary = await _publish(
            runtime,
```

і заміни на:

```python
    None, only public items are readable. A Source that returns a checkpoint
    it returned before raises `InvalidSource` instead of paging forever.
    The pass runs as `run_id`, or as a new run, and records how it ended.
    Raises `SynchronizationSuperseded`, before reading and recording
    anything, when a pass with the same or a later `observed_at` started.
    """
    run = SynchronizationRunId.new() if run_id is None else run_id
    await _claim_fence(runtime, source_id=source_id, observed_at=observed_at)

    async def guard(connection: AsyncConnection) -> None:
        if not await synchronization_fence_holds(
            connection, source_id=source_id, observed_at=observed_at
        ):
            raise SynchronizationSuperseded("a newer synchronization pass started")

    try:
        summary = await _publish(
            runtime,
```

передай обидва далі:

У `src/knowledge_service/ingestion/synchronization.py` знайди:

```python
            principal_directory=principal_directory,
            page_size=page_size,
            run_id=run,
        )
    except Exception as error:
        # The pass's own error is raised either way; its record holds only
```

і заміни на:

```python
            principal_directory=principal_directory,
            page_size=page_size,
            run_id=run,
            heartbeat=heartbeat,
            guard=guard,
        )
    except Exception as error:
        # The pass's own error is raised either way; its record holds only
```

Додай `_claim_fence` перед `_record_finished`:

У `src/knowledge_service/ingestion/synchronization.py` знайди:

```python
    return summary


async def _record_finished(
    runtime: DatabaseRuntime,
    *,
```

і заміни на:

```python
    return summary


async def _claim_fence(
    runtime: DatabaseRuntime, *, source_id: SourceId, observed_at: datetime
) -> None:
    if observed_at.utcoffset() != timedelta(0):
        raise InvalidSource("observed_at must be a UTC-aware timestamp")
    if observed_at > datetime.now(UTC) + MAX_CLOCK_SKEW:
        raise InvalidSource("observed_at is too far in the future to fence on")
    async with runtime.transaction() as connection:
        if not await claim_synchronization_fence(
            connection, source_id=source_id, observed_at=observed_at
        ):
            raise SynchronizationSuperseded("a newer synchronization pass started")


async def _record_finished(
    runtime: DatabaseRuntime,
    *,
```

`_publish` приймає обидва:

У `src/knowledge_service/ingestion/synchronization.py` знайди:

```python
    principal_directory: PrincipalDirectory | None,
    page_size: int,
    run_id: SynchronizationRunId,
) -> SynchronizationSummary:
    """One pass: every page, then the tombstones and requeued purges."""
    seen: set[str] = set()
```

і заміни на:

```python
    principal_directory: PrincipalDirectory | None,
    page_size: int,
    run_id: SynchronizationRunId,
    heartbeat: Callable[[], Awaitable[None]] | None,
    guard: WriteGuard | None,
) -> SynchronizationSummary:
    """One pass: every page, then the tombstones and requeued purges."""
    seen: set[str] = set()
```

передає `guard` у `_revoke_first`:

У `src/knowledge_service/ingestion/synchronization.py` знайди:

```python
            source_id=source_id,
            observed_at=observed_at,
            run_id=run_id,
        )
        for record, access in planned.values():
            outcome = normalize(record, source_id=source_id)
```

і заміни на:

```python
            source_id=source_id,
            observed_at=observed_at,
            run_id=run_id,
            guard=guard,
        )
        for record, access in planned.values():
            outcome = normalize(record, source_id=source_id)
```

кожному звірянню, а потім викликає heartbeat після сторінки:

У `src/knowledge_service/ingestion/synchronization.py` знайди:

```python
                readers=access.readers,
                observed_at=observed_at,
                run_id=run_id,
            )
            changes[
                _counted(result.change, narrowed=record.external_id in narrowed)
            ] += 1
        if page.next_checkpoint is None:
            break
        if page.next_checkpoint in used:
```

і заміни на:

```python
                readers=access.readers,
                observed_at=observed_at,
                run_id=run_id,
                guard=guard,
            )
            changes[
                _counted(result.change, narrowed=record.external_id in narrowed)
            ] += 1
        if heartbeat is not None:
            # After the last page this runs just before the tombstones.
            await heartbeat()
        if page.next_checkpoint is None:
            break
        if page.next_checkpoint in used:
```

і позначенню видаленими:

У `src/knowledge_service/ingestion/synchronization.py` знайди:

```python
        used.add(page.next_checkpoint)
        checkpoint = page.next_checkpoint
    tombstones = await tombstone_missing(
        runtime, source_id=source_id, seen=seen, observed_at=observed_at
    )
    requeued = await requeue_unfinished_purges(
        runtime, source_id=source_id, now=observed_at
```

і заміни на:

```python
        used.add(page.next_checkpoint)
        checkpoint = page.next_checkpoint
    tombstones = await tombstone_missing(
        runtime, source_id=source_id, seen=seen, observed_at=observed_at, guard=guard
    )
    requeued = await requeue_unfinished_purges(
        runtime, source_id=source_id, now=observed_at
```

`_revoke_first` приймає його:

У `src/knowledge_service/ingestion/synchronization.py` знайди:

```python
    source_id: SourceId,
    observed_at: datetime,
    run_id: SynchronizationRunId,
) -> set[str]:
    """Narrow every record whose new access leaves a reader out.
```

і заміни на:

```python
    source_id: SourceId,
    observed_at: datetime,
    run_id: SynchronizationRunId,
    guard: WriteGuard | None,
) -> set[str]:
    """Narrow every record whose new access leaves a reader out.
```

і передає в `narrow_access`:

У `src/knowledge_service/ingestion/synchronization.py` знайди:

```python
            readers=access.readers,
            observed_at=observed_at,
            run_id=run_id,
        ):
            narrowed.add(record.external_id)
    return narrowed
```

і заміни на:

```python
            readers=access.readers,
            observed_at=observed_at,
            run_id=run_id,
            guard=guard,
        ):
            narrowed.add(record.external_id)
    return narrowed
```

**Дія 3:** у `job_failures.py` імпортуй помилку:

У `src/knowledge_service/job_failures.py` знайди:

```python
    EmbeddingRequestRefused,
)
from knowledge_service.errors import database_unavailable
from knowledge_service.ingestion.sources import SourceUnauthorized, SourceUnavailable
from knowledge_service.jobs import Job
from knowledge_service.model import (
    ChatModelError,
```

і заміни на:

```python
    EmbeddingRequestRefused,
)
from knowledge_service.errors import database_unavailable
from knowledge_service.ingestion.sources import (
    SourceUnauthorized,
    SourceUnavailable,
    SynchronizationSuperseded,
)
from knowledge_service.jobs import Job
from knowledge_service.model import (
    ChatModelError,
```

додай код:

У `src/knowledge_service/job_failures.py` знайди:

```python
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_REFUSED = "model_refused"
    PROGRAMMING_ERROR = "programming_error"

    @property
    def transient(self) -> bool:
```

і заміни на:

```python
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_REFUSED = "model_refused"
    PROGRAMMING_ERROR = "programming_error"
    # A newer run of the same work has started; there is nothing to redo.
    SUPERSEDED = "superseded"

    @property
    def transient(self) -> bool:
```

і перевіряй її першою:

У `src/knowledge_service/job_failures.py` знайди:

```python

def classify_job_failure(error: Exception) -> JobFailure:
    """The failure an attempt's error stands for; unknown errors are bugs."""
    if isinstance(error, SourceUnavailable):
        return JobFailure.SOURCE_UNAVAILABLE
    if isinstance(error, SourceUnauthorized):
```

і заміни на:

```python

def classify_job_failure(error: Exception) -> JobFailure:
    """The failure an attempt's error stands for; unknown errors are bugs."""
    if isinstance(error, SynchronizationSuperseded):
        return JobFailure.SUPERSEDED
    if isinstance(error, SourceUnavailable):
        return JobFailure.SOURCE_UNAVAILABLE
    if isinstance(error, SourceUnauthorized):
```

**Дія 4:** у `tests/test_job_failures.py` імпортуй її:

У `tests/test_job_failures.py` знайди:

```python
    RejectionReason,
)
from knowledge_service.identifiers import JobId
from knowledge_service.ingestion.sources import SourceUnauthorized, SourceUnavailable
from knowledge_service.job_failures import (
    JobFailure,
    RetryPolicy,
```

і заміни на:

```python
    RejectionReason,
)
from knowledge_service.identifiers import JobId
from knowledge_service.ingestion.sources import (
    SourceUnauthorized,
    SourceUnavailable,
    SynchronizationSuperseded,
)
from knowledge_service.job_failures import (
    JobFailure,
    RetryPolicy,
```

і додай її в таблицю класифікації:

У `tests/test_job_failures.py` знайди:

```python
    [
        (SourceUnavailable("the wiki is down"), JobFailure.SOURCE_UNAVAILABLE),
        (SourceUnauthorized("token expired"), JobFailure.SOURCE_REFUSED),
        (
            OperationalError("SELECT 1", None, ConnectionError("lost")),
            JobFailure.DATABASE_UNAVAILABLE,
```

і заміни на:

```python
    [
        (SourceUnavailable("the wiki is down"), JobFailure.SOURCE_UNAVAILABLE),
        (SourceUnauthorized("token expired"), JobFailure.SOURCE_REFUSED),
        (SynchronizationSuperseded("newer pass"), JobFailure.SUPERSEDED),
        (
            OperationalError("SELECT 1", None, ConnectionError("lost")),
            JobFailure.DATABASE_UNAVAILABLE,
```

**Перевірка:** `uv run pytest tests/test_job_failures.py -q` показує `41 passed`, а `uv run pytest tests/test_synchronization_integration.py -q -m integration` показує `23 passed`.

### Крок 6 — Крок з кодом: один прохід на джерело за раз

Цей крок додає обгортку, що тримає оренду джерела довкола проходу.

**Простими словами:** перш ніж прохід почнеться, обгортка бере оренду джерела; якщо її має інший прохід, вона зупиняється, нічого не читаючи. Після кожної сторінки вона продовжує оренду; якщо оренди вже немає, зупиняється до позначення видаленими. Хоч як закінчиться прохід, оренда повертається. Кожна помилка оренди — різновид «джерело недоступне», тож Job повторить пізніше.

| Частина | Що робить |
|---|---|
| `SYNC_LEASE_TTL` | дві хвилини: довше за сторінку і досить коротко після аварії |
| `lease_name` | `source-sync:<source id>`, спільне для всіх проходів джерела |
| `SourceBusy`, `SourceLeaseLost`, `SourceCoordinationUnavailable` | помилки оренди, усі `SourceUnavailable` |
| `synchronize_source_exclusively` | бере оренду, запускає `synchronize_source` з `heartbeat=keep_lease`, знімає у `finally` |


**Дія 1:** створи обгортку.

Створи `src/knowledge_service/ingestion/exclusive_sync.py`:

```python
"""One synchronization pass per Source at a time, best effort.

Two passes over one Source at once waste work: both page the Source (a
remote API with rate limits and, later, a cost) and parse the same files.
A lease in Redis keeps a second pass from even starting. It is only a hint:
it can expire while its holder still works. What keeps the data right when
that happens is the Source's fence, which every pass of `synchronize_source`
claims and holds in PostgreSQL.

A pass takes the Source's lease before it reads anything, renews it after
every page, and gives it up however the pass ends. If renewing finds the
lease gone, the pass stops before it tombstones anything. If the holder
crashes, the lease expires and the next pass runs. The lease errors are
`SourceUnavailable`, so a Job that meets one is retried later (M08-T04).
"""

import logging
from datetime import datetime, timedelta
from typing import Final

from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import SourceId, SynchronizationRunId
from knowledge_service.ingestion.grant_normalization import PrincipalDirectory
from knowledge_service.ingestion.sources import SourcePort, SourceUnavailable
from knowledge_service.ingestion.synchronization import (
    PAGE_SIZE,
    SynchronizationSummary,
    synchronize_source,
)
from knowledge_service.leases import LeaseStore, LeaseStoreUnavailable

logger = logging.getLogger(__name__)


# Longer than any one page takes, so a working pass keeps its lease, and
# short enough that a crashed pass blocks the Source only briefly.
SYNC_LEASE_TTL: Final = timedelta(minutes=2)


class SourceBusy(SourceUnavailable):
    """Another pass holds this Source's lease; try again later."""


class SourceLeaseLost(SourceUnavailable):
    """This pass lost its lease part-way, so it stopped before tombstoning."""


class SourceCoordinationUnavailable(SourceUnavailable):
    """The lease store did not answer, so no pass may start or go on."""


def lease_name(source_id: SourceId) -> str:
    """The lease every synchronization pass of this Source shares."""
    return f"source-sync:{source_id.value}"


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

**Дія 2:** unit-тести імен, TTL і того, як класифікується кожна помилка.

Створи `tests/test_leases.py`:

```python
"""Tests for lease names, times to live, and how lease failures are classified."""

from datetime import timedelta

import pytest

from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.exclusive_sync import (
    SourceBusy,
    SourceCoordinationUnavailable,
    SourceLeaseLost,
    lease_name,
)
from knowledge_service.ingestion.sources import SynchronizationSuperseded
from knowledge_service.job_failures import JobFailure, classify_job_failure
from knowledge_service.leases import (
    MAX_TTL,
    MIN_TTL,
    InvalidLease,
    check_name,
    check_ttl,
)

SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000010")


@pytest.mark.parametrize("name", ["source-sync:abc", "a", "x" * 200, "a:b_c-1"])
def test_a_short_safe_name_is_accepted(name: str) -> None:
    assert check_name(name) == name


@pytest.mark.parametrize(
    "name", ["", "Upper", "a b", "x" * 201, ":leading", "new\nline", None, 7]
)
def test_any_other_name_is_refused(name: object) -> None:
    with pytest.raises(InvalidLease, match="lease name"):
        check_name(name)  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("ttl", [MIN_TTL, timedelta(minutes=2), MAX_TTL])
def test_a_ttl_within_bounds_is_accepted(ttl: timedelta) -> None:
    assert check_ttl(ttl) == ttl


@pytest.mark.parametrize(
    "ttl",
    [
        MIN_TTL - timedelta(milliseconds=1),
        MAX_TTL + timedelta(seconds=1),
        timedelta(0),
        -timedelta(seconds=1),
        5,
        None,
    ],
)
def test_a_ttl_out_of_bounds_is_refused(ttl: object) -> None:
    with pytest.raises(InvalidLease, match="lease lives"):
        check_ttl(ttl)  # pyright: ignore[reportArgumentType]


def test_every_pass_of_one_source_shares_one_valid_lease_name() -> None:
    name = lease_name(SOURCE_ID)

    assert name == f"source-sync:{SOURCE_ID.value}"
    assert check_name(name) == name


@pytest.mark.parametrize(
    "error",
    [
        SourceBusy("busy"),
        SourceLeaseLost("lost"),
        SourceCoordinationUnavailable("down"),
    ],
)
def test_a_lease_failure_is_retried_later_like_an_unavailable_source(
    error: Exception,
) -> None:
    failure = classify_job_failure(error)

    assert failure is JobFailure.SOURCE_UNAVAILABLE
    assert failure.transient


def test_a_superseded_pass_is_never_retried() -> None:
    # A newer pass already covers the work; a retry could only be refused.
    failure = classify_job_failure(SynchronizationSuperseded("newer"))

    assert failure is JobFailure.SUPERSEDED
    assert not failure.transient
```

**Перевірка:** `uv run pytest tests/test_leases.py -q` показує `26 passed`.

### Крок 7 — Крок з кодом: довести на справжніх PostgreSQL і Redis

Цей крок проганяє цілі проходи проти обох, з кожним випадком із «Перед початком».

**Простими словами:** перші тести користуються справжньою орендою: продовження на кожній сторінці, зайняте джерело, власник, що впав, оренда, втрачена посеред проходу, скасований прохід і недоступний Redis. Тести огорожі далі користуються сховищем оренд, яке пускає кожен прохід, як пустила б оренда, що спливла, і показують, що старіший прохід однаково не може позначити видаленим, не може записати сторінку, яку прочитав зарано, і не проскочить повз прохід, що взагалі не брав оренди.

| Тест | Що доводить |
|---|---|
| `test_a_pass_renews_its_lease_after_every_page_and_gives_it_up` | три сторінки, три продовження, одне зняття |
| `test_a_second_pass_does_nothing_while_another_holds_the_source` | `SourceBusy`; нічого не записано, жодного запису аудиту |
| `test_a_crashed_pass_blocks_its_source_only_until_the_lease_expires` | блок 6, перший рядок |
| `test_a_pass_that_loses_its_lease_stops_before_tombstoning` | нічого не позначено видаленим; оренда нового власника вціліла |
| `test_a_cancelled_pass_gives_up_its_lease` | блок 6, другий рядок |
| `test_an_unreachable_lease_store_stops_the_pass_before_it_reads` | нічого не прочитано, нічого не записано |
| `test_an_older_pass_cannot_tombstone_what_a_newer_pass_saw` | блок 3, перша шкода |
| `test_an_older_pass_cannot_write_a_page_it_read_before_a_newer_pass` | блок 3, друга шкода |
| `test_a_pass_that_did_not_observe_after_the_fence_reads_nothing` | той самий і ранній час відхиляються ще до читання |
| `test_a_newer_claim_waits_for_a_write_that_holds_the_fence` | таблиця блокувань з блоку 4 |
| `test_an_observation_from_the_far_future_cannot_take_the_fence` | блок 6, останній рядок |
| `test_an_unleased_newer_pass_also_fences_out_an_older_leased_one` | огорожа — частина кожного проходу |
| `test_a_retry_must_observe_the_source_again` | блок 5 |

Створи `tests/test_exclusive_sync_integration.py`:

```python
"""One synchronization pass per Source at a time, against real PostgreSQL and Redis.

The lease saves work; the fence in PostgreSQL keeps the data right. The fence
tests use a lease store that lets every pass in, as an expired lease would,
and show that an older pass still cannot write after a newer one started.
"""

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.audit import AuditAction, AuditOutcome
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import DocumentAvailability
from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.exclusive_sync import (
    SourceBusy,
    SourceCoordinationUnavailable,
    SourceLeaseLost,
    lease_name,
    synchronize_source_exclusively,
)
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourceAccess,
    SourcePage,
    SourcePort,
    SynchronizationSuperseded,
)
from knowledge_service.ingestion.synchronization import synchronize_source
from knowledge_service.leases import (
    NAMESPACE,
    Lease,
    LeaseStore,
    RedisLeaseStore,
    lease_client,
)
from knowledge_service.persistence.audit import load_audit_events
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.persistence.sources import (
    claim_synchronization_fence,
    synchronization_fence_holds,
)
from knowledge_service.settings import Settings

pytestmark = pytest.mark.integration

REDIS_URL_ENV = "KNOWLEDGE_SERVICE_TEST_REDIS_URL"
SOURCE_ID = SourceId.parse("00000000-0000-0000-0000-000000000030")
OBSERVED_AT = datetime(2026, 11, 20, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)
PUBLIC = SourceAccess(public=True)
SHORT = timedelta(milliseconds=300)

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/exclusive', 'Exclusive', true, now(), now())"
)


@dataclass
class WatchedStore:
    """A real lease store that counts renewals and can act on each one."""

    inner: RedisLeaseStore
    renewals: int = 0
    on_renew: Callable[[Lease], object] | None = None
    released: list[Lease] = field(default_factory=lambda: list[Lease]())

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
        return await self.inner.acquire(name, ttl)

    async def renew(self, lease: Lease) -> bool:
        self.renewals += 1
        if self.on_renew is not None:
            outcome = self.on_renew(lease)
            if asyncio.iscoroutine(outcome):
                await outcome
        return await self.inner.renew(lease)

    async def release(self, lease: Lease) -> bool:
        self.released.append(lease)
        return await self.inner.release(lease)


@pytest.fixture
def redis_url() -> str:
    configured = os.environ.get(REDIS_URL_ENV)
    if not configured:
        pytest.skip(f"set {REDIS_URL_ENV} to a disposable Redis server")
    return configured


@pytest.fixture
async def store(redis_url: str) -> AsyncIterator[RedisLeaseStore]:
    client = lease_client(redis_url)
    leases = RedisLeaseStore(client)
    try:
        yield leases
    finally:
        await client.delete(NAMESPACE + lease_name(SOURCE_ID))
        await client.aclose()


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
    try:
        yield runtime
    finally:
        await runtime.dispose()


def folder(path: Path, *names: str) -> DirectorySource:
    path.mkdir(parents=True, exist_ok=True)
    for name in names:
        (path / name).write_text(f"# {name}\n\nThe text of {name}.\n")
    return DirectorySource(path, access=PUBLIC)


async def availability(runtime: DatabaseRuntime) -> dict[str, str]:
    async with runtime.transaction() as connection:
        rows = await connection.execute(
            sa.select(documents.c.external_id, documents.c.availability).where(
                documents.c.source_id == SOURCE_ID.value
            )
        )
        return {row.external_id: row.availability for row in rows}


async def finished_outcomes(runtime: DatabaseRuntime) -> list[AuditOutcome]:
    async with runtime.transaction() as connection:
        events = await load_audit_events(
            connection, target_id=SOURCE_ID.value, limit=20
        )
    return [
        event.outcome
        for event in events
        if event.action is AuditAction.SYNCHRONIZATION_FINISHED
    ]


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def test_a_pass_renews_its_lease_after_every_page_and_gives_it_up(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    watched = WatchedStore(store)

    summary = await synchronize_source_exclusively(
        runtime,
        folder(tmp_path, "a.md", "b.md", "c.md"),
        leases=watched,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
        page_size=1,
    )

    assert sum(summary.changes.values()) == 3
    assert watched.renewals == 3
    assert len(watched.released) == 1
    # Given up: the next pass can take it at once.
    assert await store.acquire(lease_name(SOURCE_ID), SHORT) is not None


async def test_a_second_pass_does_nothing_while_another_holds_the_source(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    other = await store.acquire(lease_name(SOURCE_ID), timedelta(seconds=30))
    assert other is not None

    with pytest.raises(SourceBusy):
        await synchronize_source_exclusively(
            runtime,
            folder(tmp_path, "a.md"),
            leases=store,
            source_id=SOURCE_ID,
            observed_at=OBSERVED_AT,
            principal_directory=None,
        )

    assert await availability(runtime) == {}
    assert await finished_outcomes(runtime) == []
    # The other holder still has it.
    assert await store.renew(other)


async def test_a_crashed_pass_blocks_its_source_only_until_the_lease_expires(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    # A pass took the lease and died without giving it up.
    assert await store.acquire(lease_name(SOURCE_ID), SHORT) is not None
    source = folder(tmp_path, "a.md")
    with pytest.raises(SourceBusy):
        await synchronize_source_exclusively(
            runtime,
            source,
            leases=store,
            source_id=SOURCE_ID,
            observed_at=OBSERVED_AT,
            principal_directory=None,
        )

    await asyncio.sleep(0.45)
    summary = await synchronize_source_exclusively(
        runtime,
        source,
        leases=store,
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )

    assert sum(summary.changes.values()) == 1


async def test_a_pass_that_loses_its_lease_stops_before_tombstoning(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    await synchronize_source(
        runtime,
        folder(tmp_path / "before", "a.md", "b.md", "gone.md"),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    taken_over: list[Lease] = []

    async def expire_and_take_over(lease: Lease) -> None:
        # The lease runs out mid-pass, and another pass takes it.
        if not taken_over:
            await store.release(lease)
            other = await store.acquire(lease.name, timedelta(seconds=30))
            assert other is not None
            taken_over.append(other)

    watched = WatchedStore(store, on_renew=expire_and_take_over)
    with pytest.raises(SourceLeaseLost):
        await synchronize_source_exclusively(
            runtime,
            folder(tmp_path / "after", "a.md", "b.md"),
            leases=watched,
            source_id=SOURCE_ID,
            observed_at=LATER,
            principal_directory=None,
            page_size=1,
        )

    # Stopped after its first page: nothing was tombstoned.
    assert (await availability(runtime))["gone.md"] == DocumentAvailability.AVAILABLE
    assert await finished_outcomes(runtime) == [
        AuditOutcome.SUCCEEDED,
        AuditOutcome.FAILED,
    ]
    # Releasing its own lease did not take the new holder's.
    assert await store.renew(taken_over[0])


async def test_a_cancelled_pass_gives_up_its_lease(
    runtime: DatabaseRuntime, store: RedisLeaseStore, tmp_path: Path
) -> None:
    first_page_done = asyncio.Event()
    never = asyncio.Event()
    inner = folder(tmp_path, "a.md", "b.md")

    class StallsOnSecondPage:
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            if checkpoint is not None:
                first_page_done.set()
                await never.wait()
            return await inner.fetch_page(checkpoint=checkpoint, page_size=page_size)

    source: SourcePort = StallsOnSecondPage()
    task = asyncio.create_task(
        synchronize_source_exclusively(
            runtime,
            source,
            leases=store,
            source_id=SOURCE_ID,
            observed_at=OBSERVED_AT,
            principal_directory=None,
            page_size=1,
        )
    )
    await first_page_done.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await store.acquire(lease_name(SOURCE_ID), SHORT) is not None


async def test_an_unreachable_lease_store_stops_the_pass_before_it_reads(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    client = lease_client(f"redis://127.0.0.1:{closed_port()}/0")
    reads: list[str | None] = []
    inner = folder(tmp_path, "a.md")

    class Recording:
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            reads.append(checkpoint)
            return await inner.fetch_page(checkpoint=checkpoint, page_size=page_size)

    try:
        with pytest.raises(SourceCoordinationUnavailable):
            await synchronize_source_exclusively(
                runtime,
                Recording(),
                leases=RedisLeaseStore(client),
                source_id=SOURCE_ID,
                observed_at=OBSERVED_AT,
                principal_directory=None,
            )
    finally:
        await client.aclose()

    assert reads == []
    assert await availability(runtime) == {}


@dataclass
class EveryoneGetsIn:
    """A lease store whose lease never stops anyone, as if it kept expiring.

    `pause` makes the first renewal of one pass wait, so a test can run a
    newer pass in between.
    """

    pause: asyncio.Event | None = None
    paused: asyncio.Event = field(default_factory=asyncio.Event)

    async def acquire(self, name: str, ttl: timedelta) -> Lease | None:
        return Lease(name=name, token=os.urandom(16).hex(), ttl=ttl)

    async def renew(self, lease: Lease) -> bool:
        if self.pause is not None and not self.paused.is_set():
            self.paused.set()
            await self.pause.wait()
        return True

    async def release(self, lease: Lease) -> bool:
        return True


async def text_of(runtime: DatabaseRuntime) -> str:
    async with runtime.transaction() as connection:
        rows = await connection.scalars(sa.select(chunks.c.text))
        return " ".join(rows)


async def exclusive(
    runtime: DatabaseRuntime,
    source: SourcePort,
    leases: LeaseStore,
    observed_at: datetime,
) -> object:
    return await synchronize_source_exclusively(
        runtime,
        source,
        leases=leases,
        source_id=SOURCE_ID,
        observed_at=observed_at,
        principal_directory=None,
    )


async def test_an_older_pass_cannot_tombstone_what_a_newer_pass_saw(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    await synchronize_source(
        runtime,
        folder(tmp_path / "start", "x.md", "y.md"),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    # The older pass no longer sees x.md; it stops at its last renewal.
    resume = asyncio.Event()
    older_store = EveryoneGetsIn(pause=resume)
    older = asyncio.create_task(
        exclusive(
            runtime,
            folder(tmp_path / "older", "y.md"),
            older_store,
            OBSERVED_AT + timedelta(minutes=10),
        )
    )
    await older_store.paused.wait()
    # A newer pass sees both files, unchanged, so it writes nothing.
    await exclusive(
        runtime, folder(tmp_path / "newer", "x.md", "y.md"), EveryoneGetsIn(), LATER
    )
    resume.set()

    with pytest.raises(SynchronizationSuperseded):
        await older
    assert (await availability(runtime))["x.md"] == DocumentAvailability.AVAILABLE


async def test_an_older_pass_cannot_write_a_page_it_read_before_a_newer_pass(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    await synchronize_source(
        runtime,
        folder(tmp_path / "start", "x.md"),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    edited = tmp_path / "older"
    edited.mkdir()
    (edited / "x.md").write_text("# x.md\n\nAn edit the Source later undid.\n")
    page_read = asyncio.Event()
    newer_done = asyncio.Event()
    inner = DirectorySource(edited, access=PUBLIC)

    class SlowFirstPage:
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            page = await inner.fetch_page(checkpoint=checkpoint, page_size=page_size)
            page_read.set()
            # The page arrives only after a newer pass has finished.
            await newer_done.wait()
            return page

    older = asyncio.create_task(
        exclusive(
            runtime,
            SlowFirstPage(),
            EveryoneGetsIn(),
            OBSERVED_AT + timedelta(minutes=10),
        )
    )
    await page_read.wait()
    await exclusive(
        runtime, folder(tmp_path / "newer", "x.md"), EveryoneGetsIn(), LATER
    )
    newer_done.set()

    with pytest.raises(SynchronizationSuperseded):
        await older
    text = await text_of(runtime)
    assert "The text of x.md." in text
    assert "An edit the Source later undid." not in text


async def test_a_pass_that_did_not_observe_after_the_fence_reads_nothing(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    await exclusive(
        runtime, folder(tmp_path / "first", "a.md"), EveryoneGetsIn(), LATER
    )
    reads: list[str | None] = []
    inner = folder(tmp_path / "second", "a.md", "b.md")

    class Recording:
        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            reads.append(checkpoint)
            return await inner.fetch_page(checkpoint=checkpoint, page_size=page_size)

    # Same time, then earlier: neither is newer than the fence.
    for observed_at in (LATER, OBSERVED_AT):
        with pytest.raises(SynchronizationSuperseded):
            await exclusive(runtime, Recording(), EveryoneGetsIn(), observed_at)

    assert reads == []
    assert set(await availability(runtime)) == {"a.md"}


async def test_a_newer_claim_waits_for_a_write_that_holds_the_fence(
    runtime: DatabaseRuntime,
) -> None:
    async with runtime.transaction() as connection:
        assert await claim_synchronization_fence(
            connection, source_id=SOURCE_ID, observed_at=OBSERVED_AT
        )
    write_holds = asyncio.Event()
    finish_write = asyncio.Event()

    async def a_write_of_the_older_pass() -> None:
        async with runtime.transaction() as connection:
            assert await synchronization_fence_holds(
                connection, source_id=SOURCE_ID, observed_at=OBSERVED_AT
            )
            write_holds.set()
            await finish_write.wait()

    async def the_newer_claim() -> bool:
        async with runtime.transaction() as connection:
            return await claim_synchronization_fence(
                connection, source_id=SOURCE_ID, observed_at=LATER
            )

    write = asyncio.create_task(a_write_of_the_older_pass())
    await write_holds.wait()
    claim = asyncio.create_task(the_newer_claim())
    await asyncio.sleep(0.3)
    # Still waiting for the write's share lock on the Source's row.
    assert not claim.done()

    finish_write.set()
    await write
    assert await claim
    async with runtime.transaction() as connection:
        assert not await synchronization_fence_holds(
            connection, source_id=SOURCE_ID, observed_at=OBSERVED_AT
        )


async def test_an_observation_from_the_far_future_cannot_take_the_fence(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    far_future = datetime.now(UTC) + timedelta(days=365)

    with pytest.raises(InvalidSource, match="future"):
        await exclusive(
            runtime, folder(tmp_path / "bad", "a.md"), EveryoneGetsIn(), far_future
        )

    # A real pass can still claim the Source.
    await exclusive(runtime, folder(tmp_path / "good", "a.md"), EveryoneGetsIn(), LATER)
    assert set(await availability(runtime)) == {"a.md"}


async def test_an_unleased_newer_pass_also_fences_out_an_older_leased_one(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    await synchronize_source(
        runtime,
        folder(tmp_path / "start", "x.md", "y.md"),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=None,
    )
    resume = asyncio.Event()
    older_store = EveryoneGetsIn(pause=resume)
    older = asyncio.create_task(
        exclusive(
            runtime,
            folder(tmp_path / "older", "y.md"),
            older_store,
            OBSERVED_AT + timedelta(minutes=10),
        )
    )
    await older_store.paused.wait()
    # Every pass claims the fence, with or without a lease.
    await synchronize_source(
        runtime,
        folder(tmp_path / "newer", "x.md", "y.md"),
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=None,
    )
    resume.set()

    with pytest.raises(SynchronizationSuperseded):
        await older
    assert (await availability(runtime))["x.md"] == DocumentAvailability.AVAILABLE


async def test_a_retry_must_observe_the_source_again(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    class FailsOnce:
        def __init__(self, inner: DirectorySource) -> None:
            self.inner = inner
            self.failed = False

        async def fetch_page(
            self, *, checkpoint: str | None, page_size: int
        ) -> SourcePage:
            if not self.failed:
                self.failed = True
                raise SourceCoordinationUnavailable("a blip")
            return await self.inner.fetch_page(
                checkpoint=checkpoint, page_size=page_size
            )

    source = FailsOnce(folder(tmp_path, "a.md"))
    with pytest.raises(SourceCoordinationUnavailable):
        await exclusive(runtime, source, EveryoneGetsIn(), OBSERVED_AT)

    # The same observation time cannot claim the fence again...
    with pytest.raises(SynchronizationSuperseded):
        await exclusive(runtime, source, EveryoneGetsIn(), OBSERVED_AT)
    # ...a new one can.
    await exclusive(runtime, source, EveryoneGetsIn(), LATER)
    assert set(await availability(runtime)) == {"a.md"}
```

**Перевірка:** `uv run pytest tests/test_exclusive_sync_integration.py -q -m integration` показує `13 passed`.

### Крок 8 — У цьому кроці коду немає: зламай навмисно

Цей крок забирає в записів перевірку огорожі і показує, які тести це помічають.

**Дія 1:** у `synchronize_source` у `src/knowledge_service/ingestion/synchronization.py` зміни:

```python
            heartbeat=heartbeat,
            guard=guard,
```

на:

```python
            heartbeat=heartbeat,
            guard=None,
```

**Дія 2:** запусти:

```bash
uv run pytest tests/test_exclusive_sync_integration.py tests/test_synchronization_integration.py -q -m integration
```

Ти побачиш `3 failed, 33 passed`. Падають три тести, де старіший прохід прокидається після новішого: без `guard` старіший прохід знову позначає видаленим або перезаписує. Усе інше проходить, і це показує, що дані ніколи не тримала сама лише оренда.

**Дія 3:** поверни зміну і запусти команду знову: `36 passed`.

**Перевірка:** порівняй кількість зі своїм прогнозом із кроку 1 і запиши її у файл доказів.

### Крок 9 — У цьому кроці коду немає: онови три документи

Цей крок записує оренду й огорожу там, де їх шукатимуть читачі.


**Дія 1:** в архітектурі онови рядок `ingestion`:

У `docs/architecture.md` знайди:

```markdown

| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document`, `normalize_access` (`grant_normalization`, a Source's `user:<id>` and `group:<id>` principals to Access Grant subjects through its `PrincipalDirectory`, as the same UUIDv5 IDs the Authorization Context uses, and only to subjects and paths a token could carry; distinct, in a stable order, and failing closed: an unmapped or malformed principal grants nothing and is reported), `narrow_access` (a record that fails to normalize keeps its last good version only for readers its old and new access both name) |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
```

і заміни на:

```markdown

| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source` (claims the Source's fence and checks it in every write; an optional `heartbeat` runs after every page), `synchronize_source_exclusively` (`exclusive_sync`, the same pass while holding the Source's lease), `normalize`, `reconcile_document`, `normalize_access` (`grant_normalization`, a Source's `user:<id>` and `group:<id>` principals to Access Grant subjects through its `PrincipalDirectory`, as the same UUIDv5 IDs the Authorization Context uses, and only to subjects and paths a token could carry; distinct, in a stable order, and failing closed: an unmapped or malformed principal grants nothing and is reported), `narrow_access` (a record that fails to normalize keeps its last good version only for readers its old and new access both name) |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
```

додай новий код в абзац про повтори:

У `docs/architecture.md` знайди:

```markdown
adapters keep their SDK retries off, and repository code never loops.
`fail_attempt` (`job_failures`) classifies a failed attempt by its error as a
Source, database, or model that is unavailable (transient) or refused
(permanent), or a programming error (permanent), and stores that code. Only a
transient failure with attempts left gets a `retry_at`: half of a doubling
backoff (30 seconds, capped at 15 minutes) plus a random share of the other
half. `requeue_due_retries`, run first by every `make worker-relay` pass, is
```

і заміни на:

```markdown
adapters keep their SDK retries off, and repository code never loops.
`fail_attempt` (`job_failures`) classifies a failed attempt by its error as a
Source, database, or model that is unavailable (transient) or refused
(permanent), a programming error (permanent), or a pass superseded by a newer synchronization pass (permanent), and stores that code. Only a
transient failure with attempts left gets a `retry_at`: half of a doubling
backoff (30 seconds, capped at 15 minutes) plus a random share of the other
half. `requeue_due_retries`, run first by every `make worker-relay` pass, is
```

додай після нього абзац про оренди й огорожу:

У `docs/architecture.md` знайди:

```markdown
purge failed permanently is not given a new purge by later synchronization
passes; one that ran out of attempts on a passing failure is.

`telemetry` decides what leaves the process as logs. `configure_telemetry`
installs one root handler that writes JSON lines, and its `TelemetryFilter`
keeps only the fields `TELEMETRY_FIELDS` lists, with plain values, only this
```

і заміни на:

```markdown
purge failed permanently is not given a new purge by later synchronization
passes; one that ran out of attempts on a passing failure is.

`leases` holds short-lived, owned locks in Redis. A lease is a key that
expires on its own, holding its holder's random token; renewing and
releasing compare that token inside Redis, so a holder whose lease expired
cannot touch the next holder's. Only synchronization takes one, through
`synchronize_source_exclusively` (`ingestion.exclusive_sync`): it stops a
second pass of one Source from starting, so the Source is not paged and
parsed twice. The lease is renewed after every page through
`synchronize_source`'s `heartbeat` and given up however the pass ends.
Correctness never depends on it: an older pass that finishes late could
re-grant a reader a newer pass removed, roll content back, or tombstone a
Document the newer pass saw, so every `synchronize_source` pass, leased or
not, claims the Source's fence (`sources.synchronization_fence`) with its
observation time, which works only when that time is newer than the fence,
and every write of the pass (each narrowing, each reconciliation, the
tombstones) first checks under a share lock on the Source's row that it
still holds the fence. A newer claim waits for those writes, and every later
write of the older pass is refused with `SynchronizationSuperseded`, which
is never retried. An observation more than five minutes in the future is
refused, so a bad clock cannot hold the fence. Purges, retries, the relay,
and `enqueue_job` take no lease, because row locks, `SKIP LOCKED`, and the
open-intent index already give them one effect. The lease errors are
`SourceUnavailable`, so a Job that meets one is retried later, with a new
observation time.

`telemetry` decides what leaves the process as logs. `configure_telemetry`
installs one root handler that writes JSON lines, and its `TelemetryFilter`
keeps only the fields `TELEMETRY_FIELDS` lists, with plain values, only this
```

і уточни правило про Redis наприкінці:

У `docs/architecture.md` знайди:

```markdown

- LangGraph and multi-agent orchestration do not replace ordinary question answering.
- MCP is an additional protocol adapter, not the owner of search logic.
- Redis is not the durable record for conversations, jobs, or Documents.
- Generated-answer caching is excluded until permission and staleness behavior is proven.
- Kubernetes does not change application module interfaces.
```

і заміни на:

```markdown

- LangGraph and multi-agent orchestration do not replace ordinary question answering.
- MCP is an additional protocol adapter, not the owner of search logic.
- Redis is not the durable record for conversations, jobs, or Documents, and a lease is a hint: correctness comes from PostgreSQL, such as a Source's synchronization fence, never from holding a lease.
- Generated-answer caching is excluded until permission and staleness behavior is proven.
- Kubernetes does not change application module interfaces.
```

**Дія 2:** у моделі загроз додай рядок після `Retries hammer a failing dependency`:

У `docs/threat-model.md` знайди:

```markdown
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |

### Retrieval
```

і заміни на:

```markdown
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | At most one open Job per kind and target, enforced by a partial unique index; a repeated request returns the open Job; a handler claims a queued Job under a row lock and stores its outcome in the same transaction, so a duplicate or concurrent message runs nothing and reports the stored outcome | A message for an earlier attempt can start the next one, but only after its retry time, so it never shortens the wait |
| Retries hammer a failing dependency, or repeat a bug forever | Only the stored Job retries; a failure is classified, and only a Source, database, or model that is unavailable is retried, after a doubling, capped, jittered delay, until its attempts run out; refusals and programming errors are final; Dramatiq and model SDKs retry nothing | A purge that ran out of attempts on a passing failure is asked for again by the next synchronization pass, a new budget per pass; one that failed permanently waits for a person; an attempt cut off by a cancellation or the actor time limit stays queued for M08-T08 |
| Two synchronization passes of one Source run at once: double Source calls, and an older pass finishing late re-grants a removed reader, rolls content back, or tombstones what a newer pass saw | `synchronize_source_exclusively` holds the Source's Redis lease, with an owner token, renewed after every page, so a second pass gets `SourceBusy` before reading; a crashed holder's lease expires in two minutes. The Source's fence in PostgreSQL decides who may write: every pass claims it with its observation time, and every narrowing, reconciliation, and tombstone checks it under a share lock on the Source's row, so after a newer pass claims it nothing of an older pass lands; an observation more than five minutes in the future is refused | Writes an older pass committed before the newer pass claimed the fence stay until the newer pass overwrites them, and stay if the newer pass fails part-way; a pass observed up to five minutes in the future holds the fence until then |

### Retrieval
```

**Дія 3:** у посібнику з експлуатації скажи, що синхронізації потрібен Redis:

У `docs/operations.md` знайди:

```markdown

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. A failed Job comes back only through `make worker-relay`, which queues due retries before it publishes, so run it on a schedule. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

і заміни на:

```markdown

## Recovery and failure ownership

Migrations need a database role that may create the `vector` extension: pgvector is not a trusted extension, so locally that is the superuser and on a managed service its administrative role. Every external call has one owner for timeout, retry, and idempotency. A failed Job comes back only through `make worker-relay`, which queues due retries before it publishes, so run it on a schedule. A synchronization pass takes its Source's lease in Redis first; while Redis is down no pass starts, and its Job is retried later. Queue delivery is at least once. Database transitions make availability and checkpoint changes durable. Backups are incomplete until a restore drill proves recovery. Schema changes use expand/migrate/contract sequencing.

## Deployment path
```

**Перевірка:** `make docs-check` завершується з кодом 0.

### Крок 10 — У цьому кроці коду немає: перевірки, докази і прогрес

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

**Дія 2:** заверши файл доказів: результати перевірок і кількість із кроку 8 у «Verification», знахідки рев'ю й відомі обмеження в «Review», дизайн у «Design» і «Decision», а також свої відповіді на питання для рефлексії нижче. Потім познач завдання виконаним у `docs/curriculum/PROGRESS.md` і зроби коміт з `m08-t05: coordinate with expiring locks`.

**Перевірка:** усі перевірки проходять, а `git status` чистий після коміту.

## Питання для рефлексії

- Воркер A тримає оренду Handbook і падає. Що бачить воркер B одразу і що він побачить через три хвилини? Який рядок `leases.py` робить цю різницю?
- Чому «перевірити токен, потім видалити ключ» мусить бути одним Lua-скриптом? Пройди кроками, що зламається, якщо це дві команди Redis.
- У блоці 3 старіший прохід успішно продовжив оренду і все одно позначив `x.md` видаленим після того, як новіший прохід завершився. Поясни, чому успішного продовження недостатньо, щоб запис був безпечним.
- У блоці 4: чи могло зайняття огорожі новішим проходом потрапити між перевіркою огорожі старішим проходом і його записом? Поясни, як цьому заважає спільне блокування.
- Чому `SynchronizationSuperseded` ніколи не повторюється, а `SourceBusy` повторюється? Що має зробити Job, щоб спробувати синхронізацію знову?
- Що ти побачив би в базі або в журналі аудиту, щоб зрозуміти, що огорожа не тримає?

## Чекліст завершення

- [ ] Файл доказів містить інваріант, власників і твої прогнози, записані до змін.
- [ ] `leases.py` бере, продовжує і знімає оренди з токеном власника, який перевіряється всередині Redis; `tests/test_leases_integration.py` (9) проходить.
- [ ] Міграція `a7b8c9d0e1f2` додає `sources.synchronization_fence`; `claim_synchronization_fence` і `synchronization_fence_holds` існують.
- [ ] `reconcile_document`, `narrow_access` і `tombstone_missing` виконують `guard` першим у своїй транзакції.
- [ ] Кожен прохід `synchronize_source` займає огорожу, відхиляє `observed_at` з далекого майбутнього, захищає кожен запис і викликає heartbeat після кожної сторінки; `SynchronizationSuperseded` має постійний код `superseded`.
- [ ] `synchronize_source_exclusively` тримає оренду джерела; `tests/test_leases.py` (26) і `tests/test_exclusive_sync_integration.py` (13) проходять.
- [ ] Ти зламав код навмисно, побачив, як падають три тести огорожі, і повернув усе назад.
- [ ] `docs/architecture.md`, `docs/threat-model.md` і `docs/operations.md` описують оренду й огорожу.
- [ ] Усі перевірки проходять: формат, lint, типи, unit, integration, документація.
- [ ] Докази, статус завдання, рядок у PROGRESS і коміт зроблено.

Коли напишеш файли, поділись ними або виводом помилок, і я перевірю їх за цим чеклістом.
