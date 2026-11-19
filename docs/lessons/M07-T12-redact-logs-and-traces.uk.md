# M07-T12 — Чищення логів і трейсів

Англійська версія: [M07-T12-redact-logs-and-traces.md](M07-T12-redact-logs-and-traces.md)

Задача: [M07-T12 — Redact logs and traces](../curriculum/milestones/M07-identity-and-authorization/M07-T12-redact-logs-and-traces.md)

## Що ви зробите

Цей урок вирішує, що сервіс має право писати в логи, і стереже це рішення в одному місці. Логи читають оператори, а ще їх копіюють в інші системи. Тож Питання, уривок Документа чи Відповідь, що потрапили в лог, уже вийшли з-під перевірок доступу. Досі кожен модуль сам обережно вибирав, що логувати, але ніщо не заважало одному необережному рядку все зіпсувати.

- **Один вихід для логів:** новий модуль `telemetry` ставить один обробник, який пише кожен запис логу як рядок JSON, і фільтр, який чистить кожен запис на виході.
- **Дозволені поля:** лишаються тільки поля зі списку і тільки з простими значеннями: лічильники, результати, ID. Речення, українське слово чи список стають `[redacted]`.
- **Діагностика для розробки:** одне налаштування дозволяє розробнику бачити в логах сирі Питання, Докази та Відповідь кожного стріму. Типово воно вимкнене, а продакшен його відмовляє.
- **Доказ:** тести підкидають речення в Питання, Докази, Відповіді, поля, повідомлення і винятки, а потім шукають їх у кожному експортованому рядку.

| Задача вимагає | Де це відбувається |
|---|---|
| Дозволені поля телеметрії | Крок 3: `TELEMETRY_FIELDS` і `TelemetryFilter` у `src/knowledge_service/telemetry.py` |
| Налаштовувана діагностика для розробки | Кроки 2 і 4: `Settings.telemetry_diagnostics` і поле стріму `diagnostic` |
| Сирі Питання, Докази й Відповіді типово не потрапляють у логи | Кроки 3 і 4: нічого сирого не логується, поки діагностика вимкнена |
| Тести шукають підкинуті секрети в структурованих логах і спанах | Крок 5: `tests/test_telemetry.py`; запис спану стріму — один із рядків, які перевіряються |
| Успіх, найризиковіший край, типізована відмова | Крок 5: стрім із відповіддю; текст у будь-якій іншій формі; продакшен відмовляє діагностиці |
| Оновити лише те, що зміна зробила неточним | Крок 7: `docs/architecture.md`, `docs/threat-model.md` |

У цьому уроці є код: кроки 2–5 пишуть код і тести. Кроки 1, 6 і 7 без коду, хоча крок 7 редагує два документи.

Терміни уроку:

- **Телеметрія (telemetry):** те, що працюючий сервіс повідомляє про себе; тут це записи логів, пізніше ще трейси й метрики.
- **Запис логу (log record):** один запис, який створює виклик `logging`: повідомлення, рівень, ім'я логера і структуровані поля, передані через `extra`.
- **Обробник і фільтр (handler, filter):** обробник надсилає записи кудись, наприклад у stderr; фільтр, прикріплений до нього, бачить кожен запис першим і може змінити або відкинути його. Як поштова кімната, що відкриває кожен вихідний конверт і виймає те, що не можна відправляти.
- **Список дозволеного (allowlist):** перелік того, що можна; все інше заборонено. Протилежність — список заборонного (blocklist), який зупиняє лише те, про що хтось подумав.
- **Спан (span):** запис однієї одиниці роботи з початком, кінцем і атрибутами. Стрім відповіді веде такий запис (`_Span`) і логує його в кінці; справжні трейси з'являться з OpenTelemetry в M10-T14.

## Перед початком

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілим, коли ви до нього дійдете.

- **Що вже було.** Кожен модуль логує лічильники, назви й результати під одним ім'ям `extra`, наприклад `extra={"answer_stream": fields}`. Тести з попередніх уроків перевіряють, що підкинутий текст туди не потрапляє. Але це тримається, лише поки кожен майбутній рядок логу пишуть так само обережно. І ніщо не задавало, куди йдуть логи і що в них може бути.
- **Чому список дозволеного, а не пошук секретів.** Фільтр, що шукає «схожий на секрет» текст, пропустить текст, якого ніхто не передбачив. Список дозволеного називає для кожного імені `extra` ключі, які сервіс логує сьогодні. Будь-який інший ключ відкидається й лише рахується в `redacted_fields`.
- **Чому значення теж мають бути простими.** Ключ може бути дозволеним, а значення — не тим, наприклад речення в `outcome`. Тому значення, що лишається, має бути None, bool, int, скінченним float або словом до 128 символів з ASCII-літер, цифр і `_ . : / -`. Сюди вміщається назва моделі, результат, UUID чи тип винятку, але не речення і не українське слово.
- **Тільки власні повідомлення сервісу.** Інша бібліотека може вбудувати текст у повідомлення ще до логування; наприклад, asyncio у «Task exception was never retrieved» вставляє текст винятку. Таке повідомлення не почистиш, тому для будь-якого логера поза `knowledge_service` повідомлення стає `[redacted]`, а логер, рівень і тип винятку лишаються. Власні повідомлення сервісу — це шаблони з аргументами, а правила ruff `G`, увімкнені в кроці 2, забороняють f-рядки й `.format` у викликах логування.
- **Лише тип винятку.** Текст винятку і трасування стеку можуть цитувати Питання чи токен, тому в експорті лишається тільки `exception: "ValueError"`, без трасування і без стеку.
- **Очищена копія.** Фільтр повертає новий запис і не чіпає оригінал, тож інші обробники, як-от захоплення логів у pytest, бачать те, що код залогував. Цю можливість додав Python 3.12: фільтр може повернути запис, який використовується замість оригіналу.
- **Діагностика вмикається біля джерела.** Коли діагностика вимкнена, стрім навіть не кладе сирий текст у свій запис, тому жоден обробник, ні наш, ні чужий, його не побачить. Коли ввімкнена, стрім додає `diagnostic` з Питанням, Доказами, які отримала модель, і текстом Відповіді, а перший експортований рядок попереджає, що логується сирий текст. Продакшен відмовляє налаштуванню двічі: коли будуються `Settings` і ще раз у `configure_telemetry`, бо налаштування можна змінити після перевірки.
- **Що знайшло рев'ю.** Незалежне рев'ю, що шукало слабкі місця, атакувало першу версію ворожими полями, повідомленнями, винятками, дивними типами, іншими логерами й трюками з налаштуваннями, і запустило десять мутацій. Воно знайшло:
  - asyncio у «Task exception was never retrieved» ніс текст винятку прямо в повідомленні; тепер повідомлення інших бібліотек редагуються (`test_another_librarys_message_is_redacted`);
  - один аргумент-словник пропускав свої ключі; тепер для аргументів-відображень лишається тільки шаблон повідомлення;
  - налаштування, змінені після перевірки, могли ввімкнути діагностику в продакшені; тепер `configure_telemetry` кидає `DiagnosticsNotAllowed`;
  - ручне видалення обробника залишало ввімкненим глобальний перемикач діагностики; тепер `diagnostics_enabled()` питає сам встановлений обробник;
  - дев'ять із десяти мутацій пережили перші тести, наприклад пропуск українських слів, збереження стеку чи зміна оригінального запису; тепер кожну ловить тест, а остання еквівалентна (вона все одно редагує);
  - відомі межі, записані в моделі загроз: `configure_telemetry` поки ніхто не викликає, логери без поширення (propagate), як-от у uvicorn, обходять кореневий обробник, а одне просте слово проходить правило значень.
- **Відомі межі.** Поки в сервісу немає точки входу, обробник ніхто не встановлює; одне англійське слово в дозволеному полі пройде; трейси мають дотримуватися тих самих правил, коли з'являться.

## Покроково

### Крок 1 — Без коду в цьому кроці: запишіть інваріант і передбачення

Перш ніж щось змінювати, запишіть, що має бути правдою після уроку, і чого ви очікуєте. Крок 6 перевірить останнє передбачення.

Створіть `docs/evidence/M07-T12-redact-logs-and-traces.md`:

```markdown
# M07-T12 — Redact logs and traces evidence

## Invariant

Every log record the service exports passes one filter: only allowlisted
fields with plain values, only the service's own messages, and only an
exception's type. Raw Questions, Evidence, and Answers appear in exported
telemetry only when development diagnostics are on, and production refuses
them however the settings were made. With diagnostics off, no record the
service creates holds that text at all.

## Expected failing check

Before the change, `uv run pytest tests/test_telemetry.py -q` fails with
`ERROR: file or directory not found: tests/test_telemetry.py`.

## Predictions

- What the exported line for an answered stream holds, and whether the Question's words appear in it:
- What happens to `extra={"answer": {"outcome": "a whole sentence", "question": "..."}}`:
- What happens when another library logs a message with a Question built into it:
- Which test fails if the filter stops redacting other libraries' messages:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після кожної двокрапки в передбаченнях є ваша відповідь.

### Крок 2 — Крок із кодом: перемикач діагностики і лінт для логування

**Простими словами:** цей крок додає одне налаштування, яке каже, чи можуть логи містити сирий текст. Воно вимкнене, поки хтось його не ввімкне, і налаштування відмовляють йому в продакшені. Ще крок вмикає правило лінтера, яке не дає написати повідомлення логу з уже вбудованим текстом.

| Зміна | Що робить |
|---|---|
| `telemetry_diagnostics: bool = False` | Перемикач; читається з `KNOWLEDGE_SERVICE_TELEMETRY_DIAGNOSTICS`, як і решта налаштувань |
| Перевірка продакшену | Відмовляє перемикачу, коли `environment` — production, поруч з наявними перевірками секретів |
| Ruff `G` | Правила flake8-logging-format: жодних f-рядків, `.format` чи `%` у виклику логування, тож аргументи лишаються окремо і їх можна почистити |

У `src/knowledge_service/settings.py`:

**1.** Знайдіть:

```python

    environment: Environment = Environment.LOCAL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    model_api_key: SecretStr | None = None
    database_url: SecretStr | None = None
    database_pool_size: int = Field(default=5, gt=0)
```

Замініть на:

```python

    environment: Environment = Environment.LOCAL
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # Development only: lets logs hold raw Questions, Evidence, and Answers.
    telemetry_diagnostics: bool = False
    model_api_key: SecretStr | None = None
    database_url: SecretStr | None = None
    database_pool_size: int = Field(default=5, gt=0)
```

**2.** Знайдіть:

```python
                raise ValueError("MODEL_API_KEY is required in production")
            if self.database_url is None:
                raise ValueError("DATABASE_URL is required in production")

        return self
```

Замініть на:

```python
                raise ValueError("MODEL_API_KEY is required in production")
            if self.database_url is None:
                raise ValueError("DATABASE_URL is required in production")
            if self.telemetry_diagnostics:
                raise ValueError("TELEMETRY_DIAGNOSTICS is not allowed in production")

        return self
```

У `pyproject.toml`:

Знайдіть:

```toml
    "E",
    "F",
    "I",
    "PTH",
```

Замініть на:

```toml
    "E",
    "F",
    "G",
    "I",
    "PTH",
```

**Перевірка:** `uv run ruff check .` проходить; наявний код уже дотримується правил `G`. `uv run pytest tests/test_settings.py -q` і далі проходить.

### Крок 3 — Крок із кодом: модуль телеметрії

**Простими словами:** цей файл — єдиний вихід для логів. Він ставить обробник, що пише кожен запис як один рядок JSON, а перед записом чистить копію: лишає тільки поля зі списку, перетворює все, що не просте слово чи число, на `[redacted]`, ховає повідомлення інших бібліотек і лишає від винятку лише тип. Коли діагностика ввімкнена, він пропускає сирий текст і спершу про це попереджає.

| Ім'я | Що містить або робить |
|---|---|
| `TELEMETRY_FIELDS` | Для кожного імені `extra`, яке використовує сервіс, — ключі, які можна лишити |
| `plain` | Правило значень: None, bool, int, скінченний float або коротке ASCII-слово; інакше `REDACTED` |
| `TelemetryFilter` | Повертає очищену копію; рахує відкинуте в `redacted_fields`; ніколи не кидає виняток у код, що логує |
| `TelemetryFormatter` | Один JSON-об'єкт на запис: час, рівень, логер, повідомлення і поля, що лишилися |
| `TelemetryHandler` | Кореневий обробник, який пам'ятає, чи показує діагностику |
| `configure_telemetry` | Ставить обробник замість попереднього, задає кореневий рівень і відмовляє діагностиці в продакшені через `DiagnosticsNotAllowed` |
| `diagnostics_enabled` | Чи показує встановлений обробник сирий текст; код, що має сирий текст, питає це першим |

Створіть `src/knowledge_service/telemetry.py`:

```python
"""What leaves the process as telemetry, and the switch for raw text.

Every log record reaches the outside world through the handler that
`configure_telemetry` installs on the root logger. Its `TelemetryFilter`
keeps only what is safe by default:

- **Allowlisted fields.** A record's structured fields sit under one name,
  such as `extra={"answer": {...}}`. Only the names in `TELEMETRY_FIELDS`
  are kept, and of each only the listed keys; the rest are dropped and
  counted in `redacted_fields`.
- **Plain values.** A kept value must be None, a bool, an int, a finite
  float, or a word of at most 128 ASCII letters, digits, and `_ . : / -`.
  Anything else, a sentence, a Ukrainian word, a list, becomes
  `[redacted]`. The same rule applies to the arguments of the service's
  own messages, given as a tuple.
- **Only the service's own messages.** A message from another library's
  logger, such as asyncio's, may have text built into it, so it becomes
  `[redacted]`; its logger, level, and exception type stay.
- **No exception text.** An exception's message and traceback may quote a
  Question or a token; only the exception's type is kept.

Development diagnostics are the one way raw Questions, Evidence, and
Answers can be logged: `Settings.telemetry_diagnostics` turns them on, and
both `Settings` and `configure_telemetry` refuse it in production. Code
that has raw text asks `diagnostics_enabled()` first and adds it under
`diagnostic`, so with the switch off, no record ever holds it. The filter
drops `diagnostic` too unless the switch is on.

The filter returns a cleaned copy of each record, so other handlers, such
as pytest's capture, see the original. Records that never reach the root
logger, from a logger with `propagate=False`, are not exported by this
handler; whoever starts the server must route them here. Traces are not
exported yet; when OpenTelemetry arrives (M10-T14), span attributes must
pass this filter's rules too.
"""

import copy
import json
import logging
import math
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, TextIO, cast

from knowledge_service.settings import Environment, Settings

logger = logging.getLogger(__name__)

REDACTED = "[redacted]"
# The structured fields each part of the service logs, by the name its
# `extra` uses. A key missing here is dropped and counted, never shown.
TELEMETRY_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "embedding": frozenset(
            {
                "request_id",
                "provider",
                "model",
                "dimensions",
                "inputs",
                "latency_ms",
                "outcome",
                "input_tokens",
                "operation",
            }
        ),
        "rerank": frozenset(
            {
                "request_id",
                "provider",
                "model",
                "passages",
                "latency_ms",
                "outcome",
                "reason",
                "device",
                "input_tokens",
                "output_tokens",
            }
        ),
        "rewrite": frozenset(
            {
                "request_id",
                "provider",
                "model",
                "rewrite_class",
                "latency_ms",
                "outcome",
                "reason",
                "input_tokens",
                "output_tokens",
            }
        ),
        "answer_model": frozenset(
            {
                "request_id",
                "provider",
                "model",
                "latency_ms",
                "outcome",
                "input_tokens",
                "output_tokens",
            }
        ),
        "follow_up": frozenset(
            {
                "request_id",
                "provider",
                "model",
                "rewrite_class",
                "outcome",
                "fallback",
                "earlier_questions",
                "carried",
                "input_tokens",
                "output_tokens",
            }
        ),
        "answer": frozenset(
            {
                "request_id",
                "provider",
                "model",
                "outcome",
                "defect",
                "evidence_kept",
                "evidence_dropped",
                "prompt_tokens",
                "citations",
                "input_tokens",
                "output_tokens",
            }
        ),
        "answer_stream": frozenset(
            {
                "request_id",
                "outcome",
                "failure",
                "duration_ms",
                "text_deltas",
                "characters",
                "citations",
                "phase",
                "audit_outcome",
                "partial_text",
                "reason",
                "reply",
                "defect",
                "input_tokens",
                "output_tokens",
            }
        ),
        "history": frozenset(
            {
                "user_id",
                "turns",
                "earlier_questions",
                "left_out",
                "withheld",
                "tokens",
                "cited_chunks",
                "current_chunks",
            }
        ),
        "support": frozenset(
            {
                "request_id",
                "checker",
                "version",
                "outcome",
                "claims",
                "unsupported",
                "input_tokens",
                "output_tokens",
            }
        ),
        "evidence_check": frozenset({"request_id", "evidence", "hidden"}),
        "citation_check": frozenset({"request_id", "cited", "withdrawn", "outcome"}),
    }
)
DIAGNOSTIC = "diagnostic"
# The loggers whose messages are written by this service, with arguments.
OWN_LOGGERS = "knowledge_service"
# A model name, an outcome, a UUID, an exception type: never a sentence.
_PLAIN = re.compile(r"[A-Za-z0-9_.:/-]{0,128}")
# The attributes every log record has; anything else came from `extra`.
_STANDARD = frozenset(
    logging.LogRecord("", logging.INFO, "", 0, "", (), None).__dict__
) | {"message", "asctime"}


class DiagnosticsNotAllowed(ValueError):
    """Development diagnostics were asked for in production."""


def diagnostics_enabled() -> bool:
    """Whether the installed telemetry handler logs raw text."""
    return any(
        isinstance(handler, TelemetryHandler) and handler.diagnostics
        for handler in logging.getLogger().handlers
    )


def plain(value: object) -> object:
    """`value` if it is safe to export as it is, otherwise `REDACTED`."""
    if value is None or type(value) is bool or type(value) is int:
        return value
    if type(value) is float:
        return value if math.isfinite(value) else REDACTED
    if isinstance(value, str):
        # The characters themselves, whatever a subclass says it prints as.
        text = str.__str__(value)
        return text if _PLAIN.fullmatch(text) else REDACTED
    return REDACTED


def _own(name: str) -> bool:
    return name == OWN_LOGGERS or name.startswith(OWN_LOGGERS + ".")


class TelemetryFilter(logging.Filter):
    """Turn each record into the copy that may leave the process."""

    def __init__(self, *, diagnostics: bool = False) -> None:
        super().__init__()
        self.diagnostics = diagnostics

    def filter(self, record: logging.LogRecord) -> logging.LogRecord:
        try:
            return self._cleaned(record)
        except Exception:
            # A filter that raises would break the caller's logging call.
            return logging.makeLogRecord(
                {
                    "name": record.name,
                    "levelno": record.levelno,
                    "levelname": record.levelname,
                    "msg": "telemetry record dropped",
                }
            )

    def _cleaned(self, record: logging.LogRecord) -> logging.LogRecord:
        clean = copy.copy(record)
        redacted = 0
        for key, value in record.__dict__.items():
            if key in _STANDARD:
                continue
            delattr(clean, key)
            allowed = TELEMETRY_FIELDS.get(key)
            if key == DIAGNOSTIC and self.diagnostics:
                setattr(clean, key, value)
            elif allowed is not None and type(value) is dict:
                fields = cast(dict[object, object], value)
                kept = {
                    name: plain(item)
                    for name, item in fields.items()
                    if type(name) is str and name in allowed
                }
                redacted += len(fields) - len(kept)
                setattr(clean, key, kept)
            else:
                redacted += 1
        clean.msg = self._message(record)
        clean.args = None
        if redacted:
            clean.redacted_fields = redacted
        if not self.diagnostics:
            if record.exc_info:
                error = record.exc_info[1]
                clean.exception = None if error is None else type(error).__name__
            clean.exc_info = None
            clean.exc_text = None
            clean.stack_info = None
        return clean

    def _message(self, record: logging.LogRecord) -> str:
        if self.diagnostics:
            return str(record.getMessage())
        if type(record.msg) is not str or not _own(record.name):
            return REDACTED
        args = record.args
        if not args:
            return record.msg
        if type(args) is not tuple:
            # A mapping's keys, or a string split into letters: keep the form.
            return record.msg
        try:
            return record.msg % tuple(plain(item) for item in args)
        except TypeError, ValueError, KeyError:
            # A redacted value where the format wanted a number.
            return record.msg


class TelemetryFormatter(logging.Formatter):
    """One JSON object per record: time, level, logger, message, fields."""

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = {
            key: value for key, value in record.__dict__.items() if key not in _STANDARD
        }
        if record.exc_info:
            line["traceback"] = self.formatException(record.exc_info)
        if record.stack_info:
            line["stack"] = record.stack_info
        try:
            return json.dumps(line | fields, default=repr, sort_keys=True)
        except TypeError, ValueError:
            # Diagnostics only: a field JSON cannot hold, such as an odd key.
            shown = {key: repr(value) for key, value in fields.items()}
            return json.dumps(line | shown, sort_keys=True)


class TelemetryHandler(logging.StreamHandler[TextIO]):
    """The root handler `configure_telemetry` installs."""

    def __init__(self, stream: TextIO, *, diagnostics: bool) -> None:
        super().__init__(stream)
        self.diagnostics = diagnostics
        self.setFormatter(TelemetryFormatter())
        self.addFilter(TelemetryFilter(diagnostics=diagnostics))


def configure_telemetry(
    settings: Settings, *, stream: TextIO | None = None
) -> TelemetryHandler:
    """Send every log record, cleaned, to `stream` (stderr) as JSON lines.

    Replaces a handler an earlier call installed, and sets the root level
    to `settings.log_level`. With `settings.telemetry_diagnostics`, raw
    text is logged, and a warning says so first; in production that raises
    `DiagnosticsNotAllowed`, however the settings were made.
    """
    diagnostics = settings.telemetry_diagnostics is True
    if diagnostics and settings.environment is Environment.PRODUCTION:
        raise DiagnosticsNotAllowed("development diagnostics are not allowed here")
    root = logging.getLogger()
    for existing in list(root.handlers):
        if isinstance(existing, TelemetryHandler):
            remove_telemetry(existing)
    handler = TelemetryHandler(
        sys.stderr if stream is None else stream, diagnostics=diagnostics
    )
    root.addHandler(handler)
    root.setLevel(settings.log_level)
    if diagnostics:
        logger.warning("development diagnostics enabled: raw text is logged")
    return handler


def remove_telemetry(handler: TelemetryHandler) -> None:
    """Remove `handler`; its diagnostics end with it."""
    logging.getLogger().removeHandler(handler)
    handler.close()
```

Поля в `TELEMETRY_FIELDS` — це ключі, які кожен модуль логує сьогодні; `grep -rn "extra=" src` показує всі місця. Ключ, доданий пізніше без запису сюди, відкидається й рахується, але не показується. Тож пропущений запис коштує телеметрії, а не секретності.

**Перевірка:** `uv run pyright src/knowledge_service/telemetry.py` показує 0 помилок.

### Крок 4 — Крок із кодом: сирий текст у стрімі лише під діагностикою

**Простими словами:** стрім відповіді вже веде запис про кожну відповідь: скільки символів, чим закінчився, скільки тривав. Цей крок дозволяє тому самому запису нести ще Питання, уривки Доказів, які отримала модель, і текст Відповіді, але тільки коли діагностика ввімкнена. Коли вимкнена, текст навіть не додається, тож запис логу його ніколи не містить.

| Зміна | Що робить |
|---|---|
| `question`, `evidence` у `_Span` | Тримаються в пам'яті стріму, сховані від `repr` |
| `open` | Передає Питання, а тексти виділених Доказів — коли контекст зібрано |
| `end` | Додає `extra["diagnostic"]` лише коли `diagnostics_enabled()` |

У `src/knowledge_service/answering/streaming.py`:

**1.** Знайдіть:

```python
)
from knowledge_service.language import Language
from knowledge_service.model import ChatModelMalformedResponse

logger = logging.getLogger(__name__)

```

Замініть на:

```python
)
from knowledge_service.language import Language
from knowledge_service.model import ChatModelMalformedResponse
from knowledge_service.telemetry import DIAGNOSTIC, diagnostics_enabled

logger = logging.getLogger(__name__)

```

**2.** Знайдіть:

```python
        errors of `assess_evidence` and `allocate_context`. Its record ends
        here as `failed` or `cancelled` when it does.
        """
        span = _Span(request_id)
        try:
            if type(reader) is not AuthorizationContext:
                raise InvalidAnswerRequest("reader must be an AuthorizationContext")
```

Замініть на:

```python
        errors of `assess_evidence` and `allocate_context`. Its record ends
        here as `failed` or `cancelled` when it does.
        """
        span = _Span(request_id, question=question.text)
        try:
            if type(reader) is not AuthorizationContext:
                raise InvalidAnswerRequest("reader must be an AuthorizationContext")
```

**3.** Знайдіть:

```python
                if sufficiency is Sufficiency.SUFFICIENT
                else None
            )
        except asyncio.CancelledError:
            span.end()
            raise
```

Замініть на:

```python
                if sufficiency is Sufficiency.SUFFICIENT
                else None
            )
            if context is not None:
                span.evidence = tuple(item.text for item in context.evidence.items)
        except asyncio.CancelledError:
            span.end()
            raise
```

**4.** Знайдіть:

```python
    request_id: UUID
    started_at: float = field(default_factory=time.perf_counter)
    text: list[str] = field(default_factory=list[str], repr=False)
    characters: int = 0
    deltas: int = 0
    citations: int = 0
```

Замініть на:

```python
    request_id: UUID
    started_at: float = field(default_factory=time.perf_counter)
    text: list[str] = field(default_factory=list[str], repr=False)
    # Raw text for development diagnostics only; never in the default record.
    question: str = field(default="", repr=False)
    evidence: tuple[str, ...] = field(default=(), repr=False)
    characters: int = 0
    deltas: int = 0
    citations: int = 0
```

**5.** Знайдіть:

```python
        if self.usage is not None:
            fields["input_tokens"] = self.usage.input_tokens
            fields["output_tokens"] = self.usage.output_tokens
        level = logging.INFO if self.outcome == "completed" else logging.WARNING
        logger.log(level, "answer stream ended", extra={"answer_stream": fields})


async def _close(parts: AsyncGenerator[str | ModelReply]) -> None:
```

Замініть на:

```python
        if self.usage is not None:
            fields["input_tokens"] = self.usage.input_tokens
            fields["output_tokens"] = self.usage.output_tokens
        extra: dict[str, object] = {"answer_stream": fields}
        if diagnostics_enabled():
            extra[DIAGNOSTIC] = {
                "question": self.question,
                "evidence": list(self.evidence),
                "answer": "".join(self.text),
            }
        level = logging.INFO if self.outcome == "completed" else logging.WARNING
        logger.log(level, "answer stream ended", extra=extra)


async def _close(parts: AsyncGenerator[str | ModelReply]) -> None:
```

**Перевірка:** `uv run pytest tests/test_streaming.py -q` проходить без змін, разом із `test_the_stream_record_holds_no_text`.

### Крок 5 — Крок із кодом: перевірте, що експортується

**Простими словами:** ці тести пропускають записи через справжній обробник у текстовий буфер і читають кожен рядок JSON. Вони підкидають слово «zebra» всередині речень: у Питання, уривок Документа, Відповідь, поля, аргументи повідомлень, винятки й повідомлення інших бібліотек. І вимагають, щоб воно ніде не з'являлося, поки діагностика вимкнена.

| Тест | Показує |
|---|---|
| `test_an_answer_exports_its_counts_and_none_of_its_text` | Успіх: справжній стрім з відповіддю експортує свої лічильники, не втрачає жодного власного поля і не містить свого тексту |
| `test_development_diagnostics_export_the_raw_text` | З діагностикою спершу йде попередження, а рядок стріму містить Питання, Докази й Відповідь |
| `test_with_diagnostics_off_no_record_holds_the_raw_text` | Навіть обробник pytest, що бачить записи до будь-якого фільтра, не знаходить сирого тексту |
| `test_text_in_fields_and_arguments_is_dropped_or_redacted` | Найризиковіший край: речення, українська, задовгі слова, NaN, списки, відображення, невідомі ключі, поле `diagnostic` і повідомлення-не-рядок |
| `test_exceptions_and_stacks_export_only_the_type` | `logger.exception`, виняток, переданий напряму, і `stack_info` |
| `test_another_librarys_message_is_redacted` | Повідомлення asyncio та httpx |
| `test_other_handlers_still_see_the_original_record` | Фільтр чистить копію |
| `test_a_record_the_filter_cannot_clean_is_dropped_not_raised` | Зламаний запис стає «telemetry record dropped» |
| `test_configuring_again_replaces_the_handler_and_its_diagnostics` | Один обробник за раз; діагностика закінчується разом із ним |
| `test_production_refuses_development_diagnostics` | Типізована відмова: `ValidationError` від `Settings` і `DiagnosticsNotAllowed` для налаштувань, змінених після перевірки |

Створіть `tests/test_telemetry.py`:

```python
"""Telemetry keeps raw Questions, Evidence, and Answers out unless asked.

Each test seeds sentences that must never be exported, sends records
through the handler `configure_telemetry` installs, and scans every JSON
line it writes.
"""

import io
import json
import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.contracts import Question
from knowledge_service.settings import Environment, Settings
from knowledge_service.telemetry import (
    REDACTED,
    DiagnosticsNotAllowed,
    TelemetryHandler,
    configure_telemetry,
    diagnostics_enabled,
    remove_telemetry,
)
from test_generation import candidate
from test_streaming import SIGNALS, Model, Source, chunks, reply_json, streamer

QUESTION = Question(text="Is the zebra merger with Northwind going ahead in May?")
EVIDENCE_TEXT = "The zebra merger closes on 12 May for 40 million."
ANSWER = "Yes, the zebra merger closes on 12 May."
SEEDED = "zebra"


@contextmanager
def exported(*, diagnostics: bool = False) -> Generator[io.StringIO]:
    """Install telemetry writing to a buffer; restore logging afterwards."""
    root = logging.getLogger()
    level = root.level
    buffer = io.StringIO()
    handler = configure_telemetry(
        Settings(environment=Environment.TEST, telemetry_diagnostics=diagnostics),
        stream=buffer,
    )
    try:
        yield buffer
    finally:
        remove_telemetry(handler)
        root.setLevel(level)


def lines(buffer: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in buffer.getvalue().splitlines()]


async def ask() -> None:
    evidence = build_evidence([candidate(1, text=EVIDENCE_TEXT)])
    cited = evidence.items[0].evidence_id.value
    model = Model(final=reply_json(ANSWER, citations=[cited]))
    source = Source(FoundEvidence(evidence, SIGNALS))
    await chunks(streamer(model, source=source), QUESTION)


async def test_an_answer_exports_its_counts_and_none_of_its_text() -> None:
    with exported() as buffer:
        await ask()

    records = lines(buffer)
    ended = next(r for r in records if r["message"] == "answer stream ended")
    assert ended["answer_stream"]["characters"] == len(ANSWER)
    assert ended["answer_stream"]["outcome"] == "completed"
    # Every field the stream logs is allowlisted, so nothing was dropped.
    assert not [r for r in records if "redacted_fields" in r]
    assert SEEDED not in buffer.getvalue()


async def test_development_diagnostics_export_the_raw_text() -> None:
    with exported(diagnostics=True) as buffer:
        assert diagnostics_enabled()
        await ask()

    records = lines(buffer)
    assert (
        records[0]["message"] == "development diagnostics enabled: raw text is logged"
    )
    ended = next(r for r in records if r["message"] == "answer stream ended")
    assert ended["diagnostic"] == {
        "question": QUESTION.text,
        "evidence": [EVIDENCE_TEXT],
        "answer": ANSWER,
    }
    assert not diagnostics_enabled()


async def test_with_diagnostics_off_no_record_holds_the_raw_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    with exported():
        await ask()

    # pytest's own handler sees records before any filter: still nothing.
    assert caplog.records
    for record in caplog.records:
        assert not hasattr(record, "diagnostic")
        assert SEEDED not in repr(record.__dict__)


OWN = logging.getLogger("knowledge_service.anything")


def test_text_in_fields_and_arguments_is_dropped_or_redacted() -> None:
    with exported() as buffer:
        OWN.info(
            "asked %s after %d tries",
            "the zebra question",
            3,
            extra={
                "answer": {
                    "outcome": "zebra merger",
                    "request_id": "ok-1",
                    "question": "zebra",
                    "citations": ["zebra"],
                },
                "unknown": "zebra",
                "diagnostic": {"question": "zebra"},
            },
        )
        OWN.info("words %s %s %s", "злиття", "z" * 129, "y" * 128)
        OWN.info("rate %s", float("nan"))
        OWN.info("payload %s", {"the zebra merger": 1})
        OWN.info(Question(text="zebra"))  # type: ignore[arg-type]

    asked, words, rate, payload, shown = lines(buffer)
    assert asked["message"] == f"asked {REDACTED} after 3 tries"
    # A list in an allowlisted field is redacted; unknown keys are dropped.
    assert asked["answer"] == {
        "outcome": REDACTED,
        "request_id": "ok-1",
        "citations": REDACTED,
    }
    assert "diagnostic" not in asked
    assert asked["redacted_fields"] == 3
    assert words["message"] == f"words {REDACTED} {REDACTED} {'y' * 128}"
    assert rate["message"] == f"rate {REDACTED}"
    assert payload["message"] == "payload %s"
    assert shown["message"] == REDACTED
    assert SEEDED not in buffer.getvalue()


def test_exceptions_and_stacks_export_only_the_type() -> None:
    with exported() as buffer:
        try:
            raise ValueError("the zebra merger")
        except ValueError:
            OWN.exception("check failed")
        OWN.error("not raised", exc_info=ValueError("the zebra merger"))
        OWN.info("where", stack_info=True)

    raised, given, where = lines(buffer)
    assert raised["exception"] == given["exception"] == "ValueError"
    for line in (raised, given, where):
        assert "traceback" not in line
        assert "stack" not in line
    assert SEEDED not in buffer.getvalue()


def test_another_librarys_message_is_redacted() -> None:
    with exported() as buffer:
        logging.getLogger("asyncio").error(
            "Task exception was never retrieved\nexception=ValueError('zebra')"
        )
        logging.getLogger("httpx").info("[%s] %r", "generated", ("zebra question",))

    for line in lines(buffer):
        assert line["message"] == REDACTED
    assert SEEDED not in buffer.getvalue()


def test_other_handlers_still_see_the_original_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    with exported() as buffer:
        OWN.info("asked %s", "the zebra question")

    assert REDACTED in buffer.getvalue()
    assert caplog.records[-1].getMessage() == "asked the zebra question"


def test_a_record_the_filter_cannot_clean_is_dropped_not_raised() -> None:
    with exported() as buffer:
        (handler,) = [
            h for h in logging.getLogger().handlers if isinstance(h, TelemetryHandler)
        ]
        # Straight to the handler: pytest's own would fail on this record.
        handler.handle(
            logging.makeLogRecord(
                {
                    "name": OWN.name,
                    "levelno": logging.WARNING,
                    "msg": "zebra",
                    "exc_info": 5,
                }
            )
        )

    assert [line["message"] for line in lines(buffer)] == ["telemetry record dropped"]


def test_configuring_again_replaces_the_handler_and_its_diagnostics() -> None:
    with exported(diagnostics=True), exported() as buffer:
        assert not diagnostics_enabled()
        OWN.info("once")

    assert [line["message"] for line in lines(buffer)] == ["once"]
    handlers = logging.getLogger().handlers
    assert not [h for h in handlers if isinstance(h, TelemetryHandler)]


def production(*, diagnostics: bool) -> Settings:
    return Settings(
        environment=Environment.PRODUCTION,
        model_api_key=SecretStr("test-key"),
        database_url=SecretStr("postgresql+asyncpg://u@h/db"),
        telemetry_diagnostics=diagnostics,
    )


def test_production_refuses_development_diagnostics() -> None:
    settings = production(diagnostics=False)
    assert not settings.telemetry_diagnostics

    with pytest.raises(ValidationError, match="TELEMETRY_DIAGNOSTICS"):
        production(diagnostics=True)
    # Settings changed after validation are refused when telemetry starts.
    changed = settings.model_copy(update={"telemetry_diagnostics": True})
    with pytest.raises(DiagnosticsNotAllowed):
        configure_telemetry(changed, stream=io.StringIO())
    assert not diagnostics_enabled()
```

**Перевірка:** `uv run pytest tests/test_telemetry.py -q` дає `10 passed`.

### Крок 6 — Без коду в цьому кроці: зламайте навмисно

Цей крок прибирає одну перевірку, що ховає повідомлення інших бібліотек, і показує, який тест це помітить.

**Дія 1:** у `TelemetryFilter._message` у `src/knowledge_service/telemetry.py` змініть цей рядок:

```python
        if type(record.msg) is not str or not _own(record.name):
```

на:

```python
        if type(record.msg) is not str:
```

**Дія 2:** запустіть `uv run pytest tests/test_telemetry.py -q`. Очікувано: `1 failed, 9 passed`, падає `test_another_librarys_message_is_redacted`.

**Дія 3:** поверніть рядок і запустіть ту саму команду: `10 passed`. Порівняйте з останнім передбаченням у файлі evidence.

### Крок 7 — Без коду в цьому кроці: документи, перевірки, evidence і прогрес

Цей крок оновлює те, що зміна зробила неточним, доводить, що все працює, і записує результат. Модель загроз називала M07-T12 відповідальним за чищені логи; тепер вона називає тести цього уроку, а залишок роботи переносить на трейси (M10-T14).

**Дія 1:** оновіть два документи.

У `docs/architecture.md`:

Знайдіть:

```markdown
Document. Both answer endpoints `require` the `ask_questions` operation
before they read the body, and refuse with 403 otherwise.

Model runtimes that need their own Python live outside `src/`, each as its
```

Замініть на:

```markdown
Document. Both answer endpoints `require` the `ask_questions` operation
before they read the body, and refuse with 403 otherwise.

`telemetry` decides what leaves the process as logs. `configure_telemetry`
installs one root handler that writes JSON lines, and its `TelemetryFilter`
keeps only the fields `TELEMETRY_FIELDS` lists, with plain values, only this
service's own messages, and only an exception's type. Raw Questions,
Evidence, and Answers appear only when `Settings.telemetry_diagnostics` is
on, which production refuses.

Model runtimes that need their own Python live outside `src/`, each as its
```

У `docs/threat-model.md`:

**1.** Знайдіть:

```markdown
| Citations and Evidence IDs | Tell the User where a claim came from; a fake one launders a claim | Built on the server per request |
| Evaluation Datasets and Runs | Hold Questions, expected Documents, and Authorization Contexts | `datasets/`, `artifacts/` |
| Credentials | Database URL, provider API keys, the OIDC client secret and demo passwords | Environment and settings, redacted in repr; the local Keycloak's in the Git-ignored `deploy/keycloak/.env`, generated per machine |
| Telemetry | Logs and, later, traces; a copy of everything they hold | Log records today |
| Availability and budget | Every Answer costs model tokens | Model providers, the database pool |

## Actors
```

Замініть на:

```markdown
| Citations and Evidence IDs | Tell the User where a claim came from; a fake one launders a claim | Built on the server per request |
| Evaluation Datasets and Runs | Hold Questions, expected Documents, and Authorization Contexts | `datasets/`, `artifacts/` |
| Credentials | Database URL, provider API keys, the OIDC client secret and demo passwords | Environment and settings, redacted in repr; the local Keycloak's in the Git-ignored `deploy/keycloak/.env`, generated per machine |
| Telemetry | Logs and, later, traces; a copy of everything they hold | Log records, written as JSON lines by the one root handler `configure_telemetry` installs |
| Availability and budget | Every Answer costs model tokens | Model providers, the database pool |

## Actors
```

**2.** Знайдіть:

```markdown
| A token widens what its User reads: issued to another client, or with crafted group names | `azp` must be a trusted client; `groups` must be a list of at most 64 names, and a path with `~`, a hidden character, or no leading `/` is left out; Keycloak escapes `/` inside group names, so a group named `a/b` never becomes group `b` inside `a` | None |
| A role widens what its holder reads, or a caller without the role asks | `Caller` keeps the reader and the roles apart: the reader comes only from `sub` and `groups`, the roles only from realm roles, and no role permits reading a Document; both answer endpoints need the `user` role before the body is read, roles do not inherit, and an operation no role lists is denied; a test lists every route with its operation, so a new route fails until it has one; the realm maps roles to users only, never to groups or through composite roles | Role checks on the Source, synchronization, evaluation, and policy entry points when they exist |
| Random key IDs make the API flood the identity provider, or an outage opens the API | An unknown key ID refreshes the keys at most once per 30 seconds; cached keys outlive failed refreshes for at most an hour, then every token is refused as unavailable | None |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; marker tests prove no planted text reaches them | Redacted logs and traces (M07-T12) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | Durable audit events (M07-T14) |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |
```

Замініть на:

```markdown
| A token widens what its User reads: issued to another client, or with crafted group names | `azp` must be a trusted client; `groups` must be a list of at most 64 names, and a path with `~`, a hidden character, or no leading `/` is left out; Keycloak escapes `/` inside group names, so a group named `a/b` never becomes group `b` inside `a` | None |
| A role widens what its holder reads, or a caller without the role asks | `Caller` keeps the reader and the roles apart: the reader comes only from `sub` and `groups`, the roles only from realm roles, and no role permits reading a Document; both answer endpoints need the `user` role before the body is read, roles do not inherit, and an operation no role lists is denied; a test lists every route with its operation, so a new route fails until it has one; the realm maps roles to users only, never to groups or through composite roles | Role checks on the Source, synchronization, evaluation, and policy entry points when they exist |
| Random key IDs make the API flood the identity provider, or an outage opens the API | An unknown key ID refreshes the keys at most once per 30 seconds; cached keys outlive failed refreshes for at most an hour, then every token is refused as unavailable | None |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; the telemetry handler exports only allowlisted fields (`TELEMETRY_FIELDS`) with plain values, only this service's own messages, and only an exception's type; raw text appears only under development diagnostics, which production refuses; tests scan the exported JSON for seeded sentences | Span attributes pass the same rules when traces arrive (M10-T14) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | Durable audit events (M07-T14) |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |
```

**3.** Знайдіть:

```markdown
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `tests/test_synchronization_integration.py::test_a_revocation_is_published_before_any_record_of_its_page_is_parsed`, `tests/test_revocation_integration.py::test_a_grant_revoked_mid_conversation_fails_every_later_read_closed` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `tests/test_existence_leaks_integration.py::test_a_forbidden_best_match_changes_nothing_the_reader_or_logs_see`, `tests/test_existence_leaks_integration.py::test_citing_a_forbidden_chunk_reads_like_citing_a_missing_one`, `M07-T15` |

```

Замініть на:

```markdown
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `tests/test_synchronization_integration.py::test_a_revocation_is_published_before_any_record_of_its_page_is_parsed`, `tests/test_revocation_integration.py::test_a_grant_revoked_mid_conversation_fails_every_later_read_closed` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only; the telemetry handler exports only allowlisted plain fields, and raw text only under development diagnostics, never in production | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `tests/test_telemetry.py::test_an_answer_exports_its_counts_and_none_of_its_text`, `tests/test_telemetry.py::test_text_in_fields_and_arguments_is_dropped_or_redacted`, `tests/test_telemetry.py::test_production_refuses_development_diagnostics`, `M10-T14` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `tests/test_existence_leaks_integration.py::test_a_forbidden_best_match_changes_nothing_the_reader_or_logs_see`, `tests/test_existence_leaks_integration.py::test_citing_a_forbidden_chunk_reads_like_citing_a_missing_one`, `M07-T15` |

```

**4.** Знайдіть:

```markdown
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- No endpoint reads a whole Document, an excerpt, or an export by ID yet; each must read through `eligible_chunks_predicate` with the request's reader when it is built, as the agent tools must (M11-T01).
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
```

Замініть на:

```markdown
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- No endpoint reads a whole Document, an excerpt, or an export by ID yet; each must read through `eligible_chunks_predicate` with the request's reader when it is built, as the agent tools must (M11-T01).
- Telemetry is log records only; there are no traces until M10-T14 and no audit events until M07-T14. No production entry point calls `configure_telemetry` yet: whoever builds it must, and must send loggers that do not propagate, such as uvicorn's, to the root logger. A single plain word, such as a model name or a group path, passes the value rule, so an allowlisted field must never hold Document text.
- There are no rate limits until M09-T10.
- The local Keycloak names itself `http://localhost:8180`, which a container cannot reach; Open WebUI and the API in containers need a back-channel address (M09-T08, M12-T03).
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
```

**Дія 2:** запустіть перевірки по черзі, з увімкненим PostgreSQL:

```bash
uv run pytest tests/test_telemetry.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний прогін дав 2795 passed для `make test-unit`, на 10 більше, ніж після M07-T11, а інтеграційних так само 641.

**Дія 3:** заповніть файл evidence:

- **Design:** один обробник, список дозволеного і правило значень, повідомлення інших бібліотек, винятки, очищена копія і діагностика біля джерела.
- **Verification:** команди й числа, навмисна поломка і відомі межі з «Перед початком».
- **Review:** підсумок рев'ю з «Перед початком».
- **Decision:** чому список дозволеного, чому повідомлення інших бібліотек редагуються повністю, чому продакшен відмовляє діагностиці двічі; плюс принаймні одне **спростоване припущення** у формі «I assumed X. The run or review shows Y. So Z is wrong.»
- **Reflection:** ваші відповіді на питання нижче.

Потім позначте задачу виконаною, додайте рядок M07-T12 у `docs/curriculum/PROGRESS.md`, поставте наступною задачею M07-T13 і зробіть коміт `m07-t12: redact logs and traces`.

## Питання для роздумів

1. Колега додає `extra={"answer": {"question": question.text}}` у виклик логування. Що покаже експортований рядок, і як колега це помітить?
2. Чому фільтр редагує все повідомлення від asyncio, а не шукає в ньому Питання?
3. Правило значень пропускає «Northwind», але не «the Northwind merger». Назвіть одне поле, через яке одне слово з Документа все ще могло б проскочити, і що б це зупинило.
4. Продакшен відмовляє діагностиці, коли будуються `Settings`, і ще раз у `configure_telemetry`. Яку помилку ловить друга перевірка, якої не може зловити перша?
5. Коли в M10-T14 прийде OpenTelemetry, спани матимуть атрибути. Що з цього уроку мають повторно використати ці атрибути, і який тест ви б написали першим?

## Чекліст завершення

- [ ] Файл evidence містить інваріант і ваші передбачення, записані до змін.
- [ ] `Settings.telemetry_diagnostics` існує, типово вимкнений і заборонений у продакшені; правила ruff `G` увімкнені.
- [ ] `src/knowledge_service/telemetry.py` експортує лише дозволені прості поля, лише власні повідомлення сервісу і лише типи винятків.
- [ ] Стрім відповіді додає сирий текст тільки з увімкненою діагностикою.
- [ ] `tests/test_telemetry.py` проходить (10).
- [ ] Ви зробили навмисну поломку, побачили падіння одного тесту і повернули все назад.
- [ ] `docs/architecture.md` і `docs/threat-model.md` описують обробник, фільтр, діагностику і межі, що лишилися.
- [ ] Усі перевірки проходять: format, lint, types, unit, integration, docs.
- [ ] Evidence, статус задачі, рядок PROGRESS і коміт готові.

Коли напишете файли, поділіться ними або виводом помилок, і я перевірю їх за цим чеклістом.
