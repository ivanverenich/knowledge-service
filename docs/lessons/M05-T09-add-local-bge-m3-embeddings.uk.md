# M05-T09 — Додаємо локальні ембедінги BGE-M3

Англійська версія: [M05-T09-add-local-bge-m3-embeddings.md](M05-T09-add-local-bge-m3-embeddings.md)

Вихідне завдання: [M05-T09](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T09-add-local-bge-m3-embeddings.md)

## Що ви зробите

В уроці M05-T08 ви виміряли пошук за змістом з моделлю ембедінгів OpenAI, яка працює на чужих серверах. У цьому уроці ви запустите відкриту модель BGE-M3 на своєму комп'ютері, під'єднаєте її до того самого порту і проженете з нею ті самі 16 запитань. Наприкінці ви зможете порівняти локальну модель із керованою зріз за зрізом, і жоден текст не покине ваш комп'ютер.

- Невеликий **сервер BGE-M3** у `ml/bge_m3_server/` запускає модель на Python 3.12 як окремий проєкт. Один HTTP-запит отримує одну пачку векторів.
- Сервер закріплює одну точну версію ваг моделі і перевіряє їхні відбитки, перш ніж щось віддавати.
- `LocalEmbeddingModel`, частина на боці застосунку, говорить із цим сервером і виконує кожну обіцянку порту `EmbeddingModel`. Вектори від будь-якої іншої версії моделі він відкидає.
- Ті самі контрактні тести, що перевіряють фейковий і OpenAI-адаптери, тепер перевіряють і локальний.
- Скрипт набору даних отримує опцію `--local-embeddings`, і ви порівнюєте її звіт із керованим звітом з M05-T08.

| Завдання вимагає | Де це відбувається |
|---|---|
| Локальний адаптер ембедінгів на Python 3.12 | Кроки 2–4: проєкт сервера, сервер, його тести |
| Походження моделі | Крок 3: закріплена ревізія і відбитки файлів; крок 5: ідентичність називає ревізію, і кожна відповідь має їй відповідати |
| Пакетна обробка | Крок 3: ліміти пачки на сервері і розмір пачки кодування; крок 5: ліміти, з якими наявний планувальник ділить роботу так, що сервер її приймає |
| Транспорт для застосунку, що виконує наявний порт | Крок 5: `LocalEmbeddingModel` через HTTP; крок 6: спільний контрактний набір тестів |
| Детерміновані тести на успіх, найризикованіший край і одну типізовану помилку | Кроки 4 і 6: вектори від інших ваг відкидаються; зупинений сервер дає `EmbeddingUnavailable` |
| Оцінювання порівнює локальні та керовані ембедінги | Крок 7: `--local-embeddings`; крок 8: прогін і таблиця поруч |
| Жодного засіяного чутливого тексту в телеметрії | Кроки 4 і 6: помилки, логи і відповіді сервера перевіряються на підкинутий текст |

Цей урок містить програмування: у кроках 2–7 ви пишете код. Кроки 1, 8 і 9 коду не потребують. Крок 8 запускає справжню модель на вашому комп'ютері. Це нічого не коштує, але перший запуск потребує 2,3 ГБ файлів моделі (на цьому комп'ютері вони вже є в кеші Hugging Face).

Терміни цього уроку:

- **Ваги (weights):** числа, з яких складається натренована модель. Назва моделі, як-от `BAAI/bge-m3`, завтра може вказувати на нові ваги. **Ревізія (revision)** — це незмінний ID одного точного набору, як коміт у Git.
- **Походження (provenance):** звідки щось узялося. Тут: яка модель, яка ревізія і які файли створили вектор.
- **Відбиток файлу (SHA-256 hash):** короткий код, обчислений з байтів файлу. Змініть один байт, і код зміниться, тож збіг коду доводить, що файл саме той.
- **Токен (token):** шматок тексту, який модель читає за раз, часто частина слова. BGE-M3 приймає щонайбільше 8192 токени на текст.
- **Токенізатор (tokenizer):** програма, що ріже текст на токени. Перед цим вона **нормалізує** текст, наприклад перетворює один символ «ﬁ» на «fi».
- **Обрізання (truncation):** відкидання кінця задовгого тексту. Модель, що обрізає мовчки, вбудовує лише початок, і ніхто цього не помічає.
- **Loopback (127.0.0.1):** адреса, до якої може дістатися лише ваш власний комп'ютер.

## Перш ніж почати

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілим, коли ви до нього дійдете.

- **Що вже є.** Порт `EmbeddingModel` у `src/knowledge_service/embeddings/port.py` обіцяє однакове для кожної моделі: один виклик — одна пачка, вхід перевіряється до того, як щось вийде назовні, помилки не несуть тексту. `OpenAIEmbeddingModel` виконує ці обіцянки для OpenAI. `tests/test_embedding_model_contract.py` перевіряє їх для кожного адаптера. `compare_branches` і `scripts/run_retrieval_dataset.py` з M05-T08 проганяють 16 запитань з будь-якою моделлю.
- **Чому окремий процес на Python 3.12.** Застосунок працює на Python 3.14. ML-бібліотеки, як-от PyTorch, підтримують нові версії Python пізніше, тому ADR 0009 тримає їх в окремому середовищі Python 3.12, до якого звертаються через мережу. Застосунок ніколи не імпортує PyTorch, а ML-проєкт ніколи не імпортує застосунок.
- **Чому HTTP і чому лише 127.0.0.1.** HTTP — найпростіший мережевий шов, і `httpx` уже є в проєкті. Сервер не має входу з паролем, тому слухає лише loopback: інші комп'ютери до нього не дістануться.
- **Як працює походження.** Сервер завантажує одну незмінну ревізію `BAAI/bge-m3` і перевіряє відбитки файлів ваг і токенізатора, перш ніж стартувати. Кожна відповідь називає модель, ревізію і розмір вектора. Застосунок вписує ревізію в ідентичність моделі, `BAAI/bge-m3@<revision>`, тож вектори від інших ваг отримують власний Embedding Space. Будь-яку відповідь, що називає щось інше, він відкидає.
- **Найризикованіший край.** Уявіть, що хтось перезапустив сервер з іншими вагами. Його вектори виглядають нормально: по 1024 числа. Якби застосунок зберіг їх у Embedding Space закріпленої моделі, пошук порівнював би вектори двох різних моделей, і ранжування тихо перетворилося б на нісенітницю. Перевірка походження в кожній відповіді цьому запобігає. Тест імітує сервер, що відповідає іншою моделлю, ревізією чи розміром, і чекає `EmbeddingRequestRefused`.
- **Рахуємо токени без токенізатора.** Порт перевіряє токени кожного тексту до відправлення, але токенізатор живе в ML-проєкті. Тож застосунок бере безпечну верхню межу: довжину тексту після Unicode-нормалізації плюс три токени, які токенізатор додає завжди. Кожен токен покриває щонайменше один нормалізований символ, тому справжня кількість ніколи не більша. Міряються дві нормалізації, NFKC і NFKD, і перемагає довша. Токенізатор деякі символи зливає, а деякі лишає окремо, як-от корейську, набрану окремими літерами, тож одна лише NFKC рахувала б замало. Тести сервера перевіряють цю межу на кожному символі Unicode справжнім токенізатором. Межа песимістична, для звичайного тексту приблизно втричі більша за справжню кількість, тож пачки менші, ніж могли б бути. Це коштує лише кілька зайвих запитів.
- **Ніколи не обрізати.** Сервер рахує токени точно і відмовляє тексту понад 8192 токени або запиту понад 16 384. Відмову видно, а мовчки скорочений текст — ні.
- **Від чого захищений сервер.** Він відмовляє текстам, довшим за будь-який текст на 8192 токени, ще до підрахунку. Він рахує поза циклом запитів, тож один великий запит не заморожує інші. Він замінює стандартну помилку валідації FastAPI, яка повторила б текст запиту.
- **Від чого захищений застосунок.** Він читає щонайбільше 8 МБ відповіді. Він перевіряє налаштування URL під час старту. Скрипт створює HTTP-клієнт з `trust_env=False`, тож проксі, заданий у змінних середовища, ніколи не побачить текстів.
- **Відомі обмеження.** Використовуються лише щільні вектори; розріджений вихід BGE-M3 належить до M05-T10. Сервер за замовчуванням обробляє одну пачку за раз на CPU. Він не має входу і не перевіряє заголовок Host, тож вебсторінка могла б змусити його працювати, хоча нічого прочитати не змогла б. Межу токенів перевірено перебором, а не доведено. Сервер ігнорує ID запиту.

## Покроковий план

### Крок 1 — Без програмування: запишіть інваріант і передбачення

Ще до появи коду запишіть, що має бути правдою наприкінці уроку і чого ви чекаєте від локальної моделі. Передбачення, записані зараз, до прогону, і перевіряються в кроці 8.

Створіть `docs/evidence/M05-T09-local-bge-m3-embeddings.md` з цим каркасом, а тоді замініть підказки до передбачень своїми реченнями:

```markdown
# M05-T09 — Local BGE-M3 embeddings evidence

## Invariant

`LocalEmbeddingModel` keeps every promise of the `EmbeddingModel` port and
the shared contract suite passes for it. It returns vectors only when the
server names the pinned model, revision, and size, so vectors from other
weights never enter the pinned Embedding Space. No input text appears in an
error, a log record, or a server answer. The server serves one pinned,
fingerprint-checked revision on loopback only and refuses, never truncates,
a text over the model's limit.

## Expected failing check

Before the change, `uv run pytest tests/test_local_embeddings.py` fails to
import `knowledge_service.embeddings.local_adapter`.

## Predictions

- Cross-language slices (`en->uk`, `uk->en`) compared with OpenAI: ...
- The bare identifier "VPN-4031": ...
- Two runs of the local model: the same report or not, and why: ...
- Where word search plus meaning search (fused) helps or hurts: ...

## Design

## Verification

## Reflection
```

Перевірка: файл існує, і кожне передбачення — повне речення, яке число з кроку 8 може спростувати.

### Крок 2 — Крок із програмуванням: створіть проєкт сервера на Python 3.12

Цей крок створює окремий проєкт на Python 3.12, у якому працюватиме модель.

**Простими словами:** Моделі потрібні PyTorch і бібліотека, що вміє запускати BGE-M3. Вони не мають потрапити в середовище застосунку на Python 3.14. Тож цей крок створює маленький власний проєкт зі своєю версією Python, своїми залежностями і своїми налаштуваннями lint і типів. Коли його встановлено, наступний крок може писати в ньому сервер.

| Частина | Навіщо |
|---|---|
| `requires-python = ">=3.12,<3.13"` | ML-середовище з ADR 0009 |
| `sentence-transformers`, `torch` | завантажують і запускають BGE-M3 |
| `fastapi`, `uvicorn` | HTTP-сервер |
| `[tool.ruff]` з `py312` | кореневий `make lint` перевіряє і цю теку; без цього ruff переписав би код синтаксисом Python 3.14, який 3.12 не запустить |
| `package = false` | проєкт — це тека скриптів, а не пакет для встановлення |

Створіть `ml/bge_m3_server/pyproject.toml`:

```toml
[project]
name = "bge-m3-server"
version = "0.1.0"
description = "Local BGE-M3 embedding server for the knowledge service (ML runtime)"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "sentence-transformers>=5",
    "torch>=2.6",
]

[dependency-groups]
dev = ["httpx>=0.28", "pyright", "pytest>=8"]

[tool.uv]
package = false

[tool.ruff]
# This runtime is Python 3.12 (ADR 0009); the application's 3.14 syntax is not allowed.
target-version = "py312"
line-length = 88

[tool.ruff.lint]
select = ["A", "ASYNC", "B", "C4", "E", "F", "I", "PTH", "PT", "RET", "RUF", "S", "SIM", "T20", "UP", "W"]

[tool.ruff.lint.per-file-ignores]
"test_*.py" = ["S101"]

[tool.pyright]
typeCheckingMode = "standard"
pythonVersion = "3.12"
venvPath = "."
venv = ".venv"

[tool.pytest.ini_options]
addopts = ["--strict-markers"]
markers = ["model: needs the pinned BGE-M3 files in the Hugging Face cache"]
```

Потім встановіть його. `uv` завантажить Python 3.12, якщо на комп'ютері його немає:

```bash
cd ml/bge_m3_server && uv sync && cd ../..
```

Перевірка: `uv run --project ml/bge_m3_server python -c "import sys, torch; print(sys.version_info[:2], torch.__version__)"` друкує `(3, 12)` і версію PyTorch. Закомітьте новий `ml/bge_m3_server/uv.lock` разом з уроком; теку `.venv` усередині Git уже ігнорує.

### Крок 3 — Крок із програмуванням: напишіть сервер BGE-M3

Цей крок пише сам сервер, в одному файлі.

**Простими словами:** Під час старту сервер бере одну незмінну версію BGE-M3, перевіряє, що кожен великий файл саме той, і завантажує модель. Далі він відповідає на два види запитів. Один повертає, яку модель і версію він запускає. Другий приймає список текстів, точно рахує токени кожного, відмовляє задовгим і повертає по вектору на текст, а поруч називає модель і версію. Нічого з того, що він повертає чи пише в лог, ніколи не повторює текст.

| Частина | Що робить |
|---|---|
| `REVISION`, `FILE_HASHES` | закріплені ваги і відбитки трьох великих файлів |
| `Encoder` | що серверу потрібно від моделі: рахувати токени і кодувати тексти; у тестах це фейк |
| `create_app` | будує сервер навколо одного енкодера |
| `GET /v1/model` | називає модель, ревізію і розмір; тексту не потребує |
| `POST /v1/embeddings` | перевіряє ліміти, потім кодує одну пачку; відмовляє лише з причиною та індексом |
| `_check_limits` | точний підрахунок токенів у окремому потоці, щоб довгий текст не блокував інші запити |
| `_valid` | відкидає результат із неправильною кількістю, неправильним розміром або NaN |
| `BgeM3Encoder` | справжня модель через sentence-transformers, вектори одиничної довжини |
| `download_model`, `check_files` | беруть закріплену ревізію, потім перевіряють кожен відбиток |
| `main` | запускає сервер на 127.0.0.1, за замовчуванням на порту 8765 |

Два рішення варто знати ще до читання коду. Запити обробляються по одній пачці за раз, бо одна пачка вже займає всі ядра CPU. І сервер ніколи не обрізає: `sentence-transformers` мовчки скоротив би довгий текст до 8192 токенів, тому сервер спершу рахує і відмовляє.

Створіть `ml/bge_m3_server/bge_m3_server.py`:

```python
"""A local BGE-M3 embedding server: the ML runtime side of the local seam.

The knowledge service runs on Python 3.14; ML libraries such as PyTorch
follow their own release cadence, so the model runs here, on Python 3.12, as
a separate process (ADR 0009). The service reaches it over HTTP through
`knowledge_service.embeddings.local_adapter`.

The server promises:

- **Pinned provenance.** It loads exactly one revision of `BAAI/bge-m3` and
  checks the SHA-256 of every weight and tokenizer file before serving.
  Every response names the model, revision, and dimensions, so the client
  can refuse vectors from anything else.
- **Exact limits, never truncation.** It counts each input with the model's
  own tokenizer and refuses a request over 64 inputs, 8192 tokens per input,
  or 16,384 tokens in total, naming only the index. The model would
  otherwise cut a long text silently and embed only its start.
- **Unit-length dense vectors** of 1024 numbers, one per input, in order.
- **No text in errors or logs.** Error bodies hold a reason and an index;
  FastAPI's default validation error, which repeats the input, is replaced.
- **Loopback only.** It has no authentication, so it listens on 127.0.0.1.

Run it from the repository root with
`uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py`.
"""

import asyncio
import hashlib
import logging
import math
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("bge_m3_server")

MODEL_ID = "BAAI/bge-m3"
REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DIMENSIONS = 1024
MAX_INPUTS = 64
MAX_INPUT_TOKENS = 8192
MAX_BATCH_TOKENS = 16_384
# No vocabulary piece is longer than 16 characters, so a text within the token
# limit is never longer than this; a longer one is refused before counting.
MAX_INPUT_CHARACTERS = MAX_INPUT_TOKENS * 16
ENCODE_BATCH_SIZE = 16
# SHA-256 of the large files at REVISION, from the Hugging Face file listing.
FILE_HASHES = {
    "pytorch_model.bin": "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38",  # noqa: E501
    "tokenizer.json": "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08",  # noqa: E501  # gitleaks:allow
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",  # noqa: E501
}
MODEL_FILES = [
    *FILE_HASHES,
    "config.json",
    "config_sentence_transformers.json",
    "modules.json",
    "sentence_bert_config.json",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "1_Pooling/*",
]


class Encoder(Protocol):
    """The model behind the server; a fake in tests, BGE-M3 in production."""

    def count_tokens(self, text: str) -> int:
        """Count `text` exactly in the model's tokens, special tokens included."""
        ...

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one unit-length vector per text, in order."""
        ...


class ModelFilesChanged(RuntimeError):
    """A downloaded file does not match its pinned hash; nothing is served."""


class EmbedRequest(BaseModel):
    """The request body: the texts to embed, nothing else."""

    model_config = ConfigDict(extra="forbid", strict=True)

    inputs: list[Annotated[str, Field(max_length=MAX_INPUT_CHARACTERS)]] = Field(
        min_length=1, max_length=MAX_INPUTS
    )


def create_app(encoder: Encoder) -> FastAPI:
    """Build the server around one encoder."""
    app = FastAPI(title="bge-m3-server", docs_url=None, redoc_url=None)
    # One batch at a time: the model uses every core for one batch already.
    busy = asyncio.Lock()

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, __: RequestValidationError) -> JSONResponse:
        # The default handler repeats the input, which may be document text.
        return _refusal(422, "invalid_request")

    @app.get("/v1/model")
    async def model() -> dict[str, object]:
        return _provenance()

    @app.post("/v1/embeddings")
    async def embeddings(body: EmbedRequest) -> JSONResponse:
        # Counting takes time on long texts; off the event loop, so a large
        # request cannot hold up every other request.
        checked = await asyncio.to_thread(_check_limits, encoder, body.inputs)
        if isinstance(checked, JSONResponse):
            return checked
        total = checked
        vectors: list[list[float]] = []
        failure: str | None = None
        async with busy:
            try:
                vectors = await asyncio.to_thread(encoder.encode, body.inputs)
            except Exception as error:
                # Its message or traceback could quote an input; keep the type.
                failure = type(error).__name__
        if failure is None and not _valid(vectors, len(body.inputs)):
            failure = "InvalidVectors"
        if failure is not None:
            logger.warning("encoding failed error=%s", failure)
            return _refusal(500, "encoding_failed")
        logger.info("embedded inputs=%d tokens=%d", len(body.inputs), total)
        return JSONResponse(
            {**_provenance(), "input_tokens": total, "vectors": vectors}
        )

    return app


class BgeM3Encoder:
    """BGE-M3 dense embeddings through sentence-transformers."""

    def __init__(self, folder: Path, device: str) -> None:
        # Imported here, so tests with a fake encoder never load PyTorch.
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(str(folder), device=device)
        self._tokenizer: Any = self._model.tokenizer

    def count_tokens(self, text: str) -> int:
        return len(self._tokenizer(text, add_special_tokens=True)["input_ids"])

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts),
            batch_size=ENCODE_BATCH_SIZE,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return [[float(value) for value in vector] for vector in vectors]


def download_model() -> Path:
    """Fetch the pinned revision (or reuse the cache) and check every hash."""
    from huggingface_hub import snapshot_download

    folder = Path(
        snapshot_download(MODEL_ID, revision=REVISION, allow_patterns=MODEL_FILES)
    )
    check_files(folder)
    return folder


def check_files(folder: Path) -> None:
    """Raise `ModelFilesChanged` unless every pinned file has its pinned hash."""
    for name, expected in FILE_HASHES.items():
        path = folder / name
        if not path.is_file() or _sha256(path) != expected:
            raise ModelFilesChanged(f"{name} does not match the pinned hash")


def _check_limits(encoder: Encoder, inputs: list[str]) -> int | JSONResponse:
    """Return the batch's total tokens, or the refusal of the first bad input."""
    total = 0
    for index, text in enumerate(inputs):
        if not text.strip():
            return _refusal(422, "empty_text", index)
        tokens = _count(encoder, text)
        if tokens is None:
            return _refusal(422, "uncountable_text", index)
        if tokens > MAX_INPUT_TOKENS:
            return _refusal(413, "input_too_long", index)
        total += tokens
        if total > MAX_BATCH_TOKENS:
            return _refusal(413, "batch_too_long", index)
    return total


def _valid(vectors: list[list[float]], expected: int) -> bool:
    # A NaN cannot be written as JSON, and a short vector would be refused later.
    return len(vectors) == expected and all(
        len(vector) == DIMENSIONS and all(math.isfinite(value) for value in vector)
        for vector in vectors
    )


def _count(encoder: Encoder, text: str) -> int | None:
    # A tokenizer error may quote the text, so only its absence is kept.
    try:
        tokens = encoder.count_tokens(text)
    except Exception:
        return None
    return tokens if type(tokens) is int and tokens >= 0 else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _provenance() -> dict[str, object]:
    return {"model": MODEL_ID, "revision": REVISION, "dimensions": DIMENSIONS}


def _refusal(status: int, reason: str, index: int | None = None) -> JSONResponse:
    return JSONResponse({"error": reason, "index": index}, status_code=status)


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    device = os.environ.get("BGE_M3_DEVICE", "cpu")
    port = int(os.environ.get("BGE_M3_PORT", "8765"))
    encoder = BgeM3Encoder(download_model(), device)
    uvicorn.run(create_app(encoder), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
```

Перевірка: запустіть сервер з кореня репозиторію і спитайте, яку модель він запускає:

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py
```

Коли він надрукує `Uvicorn running on http://127.0.0.1:8765`, виконайте в іншому терміналі `curl -s 127.0.0.1:8765/v1/model`. Очікувано: `{"model":"BAAI/bge-m3","revision":"5617a9f61b028005a4858fdac845db406aefb181","dimensions":1024}`. Зупиніть сервер через Ctrl+C. Якщо файлів ще не було в кеші, перший старт завантажить 2,3 ГБ.

### Крок 4 — Крок із програмуванням: протестуйте сервер

Цей крок перевіряє обіцянки сервера, не завантажуючи модель.

**Простими словами:** Більшість тестів дає серверу фейкову модель, яка рахує один токен на символ, тож вони йдуть секунди і не потребують завантаження. Вони перевіряють, що задовгий текст відхиляється і ніколи не доходить до моделі, що зламаний запит відхиляється без повторення тексту, що збій моделі нічого не розкриває і що змінений файл ловиться. Ще один тест бере справжній токенізатор і перевіряє межу токенів застосунку на кожному символі Unicode; він пропускається, коли файлів моделі немає в кеші.

| Тест | Доводить |
|---|---|
| `test_every_response_names_the_pinned_model` | успіх: вектори в порядку, з моделлю, ревізією, розміром і кількістю токенів |
| `test_a_request_over_the_limits_is_refused_and_never_truncated` | задовгий, порожній або непідрахований вхід відхиляється і не кодується |
| `test_an_invalid_request_is_refused_without_repeating_it` | неправильне тіло отримує `invalid_request`, а не текст |
| `test_an_encoding_failure_carries_no_text` | модель, що кидає помилку або повертає NaN, дає `encoding_failed`; тексту немає ні у відповіді, ні в логах |
| `test_a_changed_or_missing_model_file_is_refused` | походження: неправильний або відсутній файл дає `ModelFilesChanged` |
| `test_the_application_token_bound_is_never_too_low` | межа з кроку 5 ніколи не нижча за справжню кількість |

Створіть `ml/bge_m3_server/test_bge_m3_server.py`:

```python
"""The BGE-M3 server: limits, provenance, and quiet failures.

Most tests use a fake encoder, so they need neither PyTorch nor the model.
Tests marked `model` use the real tokenizer and skip when the pinned files
are not in the Hugging Face cache.
"""

import logging
import random
import unicodedata
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import bge_m3_server as server

PLANTED = "the salary of Olena is 9000"


class FakeEncoder:
    """Counts one token per character and records what it encoded."""

    def __init__(self, *, fail: bool = False, value: float = 1.0) -> None:
        self.encoded: list[list[str]] = []
        self._fail = fail
        self._value = value

    def count_tokens(self, text: str) -> int:
        if text == "uncountable":
            raise ValueError(f"cannot tokenize {text}")
        return len(text)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if self._fail:
            raise RuntimeError(f"out of memory while encoding {texts[0]}")
        self.encoded.append(list(texts))
        return [[self._value] + [0.0] * (server.DIMENSIONS - 1) for _ in texts]


def client(encoder: FakeEncoder) -> TestClient:
    return TestClient(server.create_app(encoder), raise_server_exceptions=False)


def test_every_response_names_the_pinned_model() -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post("/v1/embeddings", json={"inputs": ["a", "bc"]})

    body = answer.json()
    assert answer.status_code == 200
    assert (body["model"], body["revision"], body["dimensions"]) == (
        "BAAI/bge-m3",
        server.REVISION,
        1024,
    )
    assert body["input_tokens"] == 3
    assert len(body["vectors"]) == 2
    assert encoder.encoded == [["a", "bc"]]


def test_the_model_endpoint_needs_no_text() -> None:
    answer = client(FakeEncoder()).get("/v1/model")

    assert answer.json() == {
        "model": "BAAI/bge-m3",
        "revision": server.REVISION,
        "dimensions": 1024,
    }


@pytest.mark.parametrize(
    ("inputs", "status", "reason", "index"),
    [
        (["fine", "x" * (server.MAX_INPUT_TOKENS + 1)], 413, "input_too_long", 1),
        (["x" * 8000, "y" * 8000, "z" * 500], 413, "batch_too_long", 2),
        (["fine", "   "], 422, "empty_text", 1),
        (["uncountable"], 422, "uncountable_text", 0),
    ],
    ids=["one input too long", "batch too long", "blank", "uncountable"],
)
def test_a_request_over_the_limits_is_refused_and_never_truncated(
    inputs: list[str], status: int, reason: str, index: int
) -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post("/v1/embeddings", json={"inputs": inputs})

    assert answer.status_code == status
    assert answer.json() == {"error": reason, "index": index}
    assert encoder.encoded == []


@pytest.mark.parametrize(
    "body",
    [
        {"inputs": [PLANTED, 5]},
        {"inputs": []},
        {"inputs": [PLANTED] * (server.MAX_INPUTS + 1)},
        {"inputs": [PLANTED], "extra": PLANTED},
        {"inputs": ["x" * (server.MAX_INPUT_CHARACTERS + 1)]},
        {"texts": [PLANTED]},
    ],
    ids=["not a string", "empty", "too many", "extra field", "too long", "wrong field"],
)
def test_an_invalid_request_is_refused_without_repeating_it(
    body: dict[str, object],
) -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post("/v1/embeddings", json=body)

    assert answer.status_code == 422
    assert answer.json() == {"error": "invalid_request", "index": None}
    assert encoder.encoded == []


@pytest.mark.parametrize(
    "encoder",
    [FakeEncoder(fail=True), FakeEncoder(value=float("nan"))],
    ids=["raises", "not a number"],
)
def test_an_encoding_failure_carries_no_text(
    encoder: FakeEncoder, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    answer = client(encoder).post("/v1/embeddings", json={"inputs": [PLANTED]})

    assert answer.status_code == 500
    assert answer.json() == {"error": "encoding_failed", "index": None}
    assert PLANTED not in answer.text
    assert PLANTED not in caplog.text
    assert "encoding failed" in caplog.text


def test_a_changed_or_missing_model_file_is_refused(tmp_path: Path) -> None:
    for name in server.FILE_HASHES:
        (tmp_path / name).write_bytes(b"not the pinned weights")

    with pytest.raises(server.ModelFilesChanged):
        server.check_files(tmp_path)
    (tmp_path / "pytorch_model.bin").unlink()
    with pytest.raises(server.ModelFilesChanged):
        server.check_files(tmp_path)


def _cached_model() -> Path | None:
    from huggingface_hub import snapshot_download

    try:
        folder = snapshot_download(
            server.MODEL_ID,
            revision=server.REVISION,
            allow_patterns=server.MODEL_FILES,
            local_files_only=True,
        )
    except Exception:
        return None
    return Path(folder)


@pytest.mark.model
def test_the_application_token_bound_is_never_too_low() -> None:
    # Mirrors LocalEmbeddingModel.count_tokens in the application, which must
    # never count fewer tokens than the server, or a batch it lets through
    # would be refused here.
    folder = _cached_model()
    if folder is None:
        pytest.skip("the pinned BGE-M3 files are not in the Hugging Face cache")
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))

    def bound(text: str) -> int:
        return (
            max(
                len(unicodedata.normalize("NFKC", text)),
                len(unicodedata.normalize("NFKD", text)),
            )
            + 3
        )

    every_character = [
        chr(point) for point in range(0x110000) if not 0xD800 <= point <= 0xDFFF
    ]
    rng = random.Random(9)  # noqa: S311 - a repeatable sample, not a secret
    mixed = [
        "".join(
            rng.choice(every_character) if rng.random() < 0.5 else rng.choice(" \tЇя1-")
            for _ in range(rng.randint(1, 40))
        )
        for _ in range(20_000)
    ]
    # Each character alone, decomposed, and between letters, as text has it.
    in_words = [f"a{character}b {character}" for character in every_character]
    decomposed = [unicodedata.normalize("NFD", text) for text in every_character]
    accented = [
        f"{character}\N{COMBINING ACUTE ACCENT}" * 10 for character in every_character
    ]
    texts = every_character + in_words + decomposed + accented + mixed
    for start in range(0, len(texts), 10_000):
        group = texts[start : start + 10_000]
        encoded = tokenizer.encode_batch(group, add_special_tokens=True)
        assert all(
            len(item.ids) <= bound(text)
            for text, item in zip(group, encoded, strict=True)
        )
```

Додайте ціль у Makefile, щоб ML-перевірки мали одну команду. У `Makefile`:

Знайдіть у `Makefile`:

```makefile
.PHONY: do_good setup format format-check lint typecheck test-unit test-integration check hooks-install hooks docs-check
```

Замініть на:

```makefile
.PHONY: do_good setup format format-check lint typecheck test-unit test-integration ml-check check hooks-install hooks docs-check
```

Знайдіть у `Makefile`:

```makefile
	KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$$url" uv run pytest -m "integration and not live"
```

Замініть на:

```makefile
	KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$$url" uv run pytest -m "integration and not live"

ml-check: ## Type-check and test the local BGE-M3 server (Python 3.12)
	cd ml/bge_m3_server && uv run pyright && uv run pytest -q
```

Рядки рецептів у Makefile починаються з табуляції, а не з пробілів.

Перевірка: `make ml-check` показує `0 errors` і `16 passed` (близько 20 секунд; повільний тут тест межі токенів). Щоб побачити, як тест межі ловить справжню помилку, замініть `"NFKD"` на `"NFKC"` у його функції `bound`: він упаде на корейській, набраній окремими літерами. Поверніть зміну назад.

### Крок 5 — Крок із програмуванням: напишіть адаптер застосунку

Цей крок пише `LocalEmbeddingModel`, бік шва з боку застосунку.

**Простими словами:** Застосунок просить у цього адаптера вектори так само, як у OpenAI-адаптера. Адаптер перевіряє тексти на безпечні ліміти, надсилає їх локальному серверу одним запитом і читає відповідь, не довіряючи їй: статус, розмір, форму, а головне — модель і версію, які вона називає. Будь-яка проблема стає однією з типізованих помилок порту, і ніщо з того, що він пише в лог чи кидає, не містить тексту.

Спершу зробіть `httpx` залежністю застосунку; досі ним користувалися лише тести. У `pyproject.toml`:

Знайдіть у `pyproject.toml`:

```toml
    "fastapi>=0.141.1",
    "markdown-it-py>=4,<5",
```

Замініть на:

```toml
    "fastapi>=0.141.1",
    "httpx>=0.28.1",
    "markdown-it-py>=4,<5",
```

Знайдіть у `pyproject.toml`:

```toml
dev = [
    "httpx>=0.28.1",
    "hypothesis>=6.170.5",
```

Замініть на:

```toml
dev = [
    "hypothesis>=6.170.5",
```

Потім виконайте `uv sync`. У `uv.lock` переміщуються лише записи `httpx`, версія лишається 0.28.1.

| Частина | Що робить |
|---|---|
| `LocalEmbeddingSettings` | налаштування `LOCAL_EMBEDDING_*`; URL перевіряється під час старту |
| `identity` | провайдер `local`, модель `BAAI/bge-m3@<revision>`, 1024 виміри |
| `limits` | 32 тексти, по 8192 токени, 16 384 на запит: у межах лімітів сервера |
| `count_tokens` | безпечна верхня межа з розділу «Перш ніж почати» |
| `embed` | перевіряє вхід, надсилає один запит, читає відповідь, пише в лог лише лічильники |
| `verify_server` | питає `/v1/model` і перевіряє походження, не надсилаючи тексту |
| `_send` | один запит під одним дедлайном, читається частинами до 8 МБ; помилки транспорту стають помилками порту поза блоком `except` |
| `_check_provenance` | відкидає будь-яку відповідь, що називає іншу модель, ревізію чи розмір; `1024.0` не проходить як `1024` |
| `_check_status` | 429 — обмеження частоти, 408/504 — тайм-аут, 5xx — недоступний, усе інше — відмова |

Створіть `src/knowledge_service/embeddings/local_adapter.py`:

```python
"""Local BGE-M3 implementation of the provider-neutral EmbeddingModel port.

The model itself runs in a separate Python 3.12 process, the BGE-M3 server
in `ml/bge_m3_server/`, because ML libraries follow a different Python
release cadence than this application (ADR 0009). This adapter is the
application's side of that seam: one call to `embed` is one HTTP request to
the server. It adds what the port cannot know:

- **Pinned provenance.** The identity names the model and the exact weights
  revision, `BAAI/bge-m3@<revision>`, so vectors from other weights never
  share an Embedding Space. Every response must name the same model,
  revision, and dimensions; any other answer raises
  `EmbeddingRequestRefused` and no vector is returned, because storing it
  would mix two models in one space.
- **A safe token bound without a tokenizer.** `count_tokens` returns an
  upper bound, so a batch the server would refuse is refused here first,
  before any text leaves the process. The server counts exactly and refuses
  rather than truncating.
- **Retry classification.** A refused connection or a 5xx becomes
  `EmbeddingUnavailable`, a 429 `EmbeddingRateLimited`, a timeout
  `EmbeddingTimeout`, and any other refusal `EmbeddingRequestRefused`.
- **A bounded answer.** The response is read in pieces and refused past
  `MAX_RESPONSE_BYTES`, so a broken server cannot fill the memory.
- **Safe logging.** One log record per call with counts, timing, and the
  outcome; never text. Port errors are raised outside the `except` block,
  because an HTTP error holds the request, and the request holds every text.

The caller owns the `httpx.AsyncClient`. Create it with `trust_env=False`,
so a proxy set in the environment never receives the texts.
"""

import asyncio
import json
import logging
import math
import time
import unicodedata
from collections.abc import Sequence
from typing import cast
from uuid import UUID

import httpx
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from knowledge_service.embeddings.port import (
    EmbeddingBatch,
    EmbeddingError,
    EmbeddingInputRejected,
    EmbeddingLimits,
    EmbeddingMalformedResponse,
    EmbeddingModelIdentity,
    EmbeddingRateLimited,
    EmbeddingRequestRefused,
    EmbeddingTimeout,
    EmbeddingUnavailable,
    EmbeddingUsage,
    check_inputs,
    checked_batch,
)

logger = logging.getLogger(__name__)

PROVIDER = "local"
EMBEDDINGS_PATH = "/v1/embeddings"
MODEL_PATH = "/v1/model"
# The BGE-M3 weights this application was evaluated with (Hugging Face commit).
BGE_M3_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
# 64 vectors of 1024 numbers as JSON take about 2 MB; far more means a fault.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
# <s>, </s>, and the word-start marker the tokenizer adds before the text.
_ADDED_TOKENS = 3


class LocalEmbeddingSettings(BaseSettings):
    """Settings for the local BGE-M3 adapter, read from `LOCAL_EMBEDDING_*`.

    The limits must not exceed the server's: 8192 tokens per input, as the
    model allows, and at most 64 inputs and 16,384 tokens per request.
    """

    model_config = SettingsConfigDict(env_prefix="LOCAL_EMBEDDING_")

    url: str = Field(default="http://127.0.0.1:8765", min_length=1)
    model: str = Field(default="BAAI/bge-m3", min_length=1)
    revision: str = Field(default=BGE_M3_REVISION, min_length=1)
    dimensions: int = Field(default=1024, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    max_inputs: int = Field(default=32, gt=0)
    max_input_tokens: int = Field(default=8192, gt=0)
    max_batch_tokens: int = Field(default=16_384, gt=0)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        # Checked here, so a typo fails at start-up, not inside a request.
        try:
            url = httpx.URL(value)
        except httpx.InvalidURL:
            url = None
        if url is None or url.scheme not in ("http", "https") or not url.host:
            raise ValueError("url must be an http or https URL")
        return value.rstrip("/")


class LocalEmbeddingModel:
    """Translate EmbeddingModel calls to the local BGE-M3 server."""

    def __init__(
        self, client: httpx.AsyncClient, settings: LocalEmbeddingSettings
    ) -> None:
        self._client = client
        self._settings = settings
        self._identity = EmbeddingModelIdentity(
            provider=PROVIDER,
            model=f"{settings.model}@{settings.revision}",
            dimensions=settings.dimensions,
        )
        self._limits = EmbeddingLimits(
            max_inputs=settings.max_inputs,
            max_input_tokens=settings.max_input_tokens,
            max_batch_tokens=settings.max_batch_tokens,
        )

    @property
    def identity(self) -> EmbeddingModelIdentity:
        return self._identity

    @property
    def limits(self) -> EmbeddingLimits:
        return self._limits

    def count_tokens(self, text: str) -> int:
        """Return an upper bound on the text's BGE-M3 tokens.

        The tokenizer first normalizes the text, and every token then covers
        at least one normalized character or the word-start marker. Its
        normalization composes some characters, as NFKC does, but leaves
        others apart, such as Korean typed as separate letters, so the
        longer of the NFKC and NFKD forms is used. The server's tests check
        this bound against every Unicode character with the real tokenizer.
        """
        longest = max(
            len(unicodedata.normalize("NFKC", text)),
            len(unicodedata.normalize("NFKD", text)),
        )
        return longest + _ADDED_TOKENS

    async def embed(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> EmbeddingBatch:
        deadline = self._deadline(timeout_seconds)
        try:
            check_inputs(texts, self._limits, self.count_tokens)
        except EmbeddingInputRejected as error:
            _log(request_id, self._identity, len(texts), 0, error)
            raise
        if not texts:
            return EmbeddingBatch(
                identity=self._identity,
                vectors=(),
                usage=EmbeddingUsage(input_tokens=0, latency_ms=0),
            )
        started_at = time.perf_counter()
        try:
            status, content = await self._send(
                "POST",
                EMBEDDINGS_PATH,
                deadline,
                body={"inputs": list(texts)},
                headers={"X-Request-ID": str(request_id)},
            )
            latency_ms = _since(started_at)
            batch = self._batch_from(status, content, len(texts), latency_ms)
        except EmbeddingError as error:
            _log(request_id, self._identity, len(texts), _since(started_at), error)
            raise
        _log(request_id, self._identity, len(texts), latency_ms, None, batch.usage)
        return batch

    async def verify_server(self, *, timeout_seconds: float) -> None:
        """Check, sending no text, that the server runs the configured model.

        Raises `EmbeddingUnavailable` or `EmbeddingTimeout` when the server
        cannot be reached, and `EmbeddingRequestRefused` when it serves other
        weights. Call it before a long run, so a stopped server or a wrong
        revision fails at once instead of after the first batch.
        """
        deadline = self._deadline(timeout_seconds)
        status, content = await self._send("GET", MODEL_PATH, deadline)
        _check_status(status)
        self._check_provenance(_json_object(content))

    def _deadline(self, timeout_seconds: float) -> float:
        if math.isnan(timeout_seconds):
            raise ValueError("timeout_seconds must be a number")
        return min(timeout_seconds, self._settings.timeout_seconds)

    async def _send(
        self,
        method: str,
        path: str,
        deadline: float,
        *,
        body: object = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        """Send one request; return its status and its bytes, up to the cap."""
        status = 0
        content = bytearray()
        failure: EmbeddingError | None = None
        try:
            async with (
                asyncio.timeout(deadline),
                self._client.stream(
                    method,
                    self._settings.url + path,
                    json=body,
                    headers=headers,
                    timeout=deadline,
                ) as response,
            ):
                status = response.status_code
                async for piece in response.aiter_bytes():
                    content += piece
                    if len(content) > MAX_RESPONSE_BYTES:
                        break
        except TimeoutError, httpx.TimeoutException:
            failure = EmbeddingTimeout()
        except httpx.HTTPError:
            failure = EmbeddingUnavailable()
        if len(content) > MAX_RESPONSE_BYTES:
            failure = EmbeddingMalformedResponse("the response is too large")
        if failure is not None:
            # Raised outside the except block: the HTTP error holds the request.
            raise failure
        return status, bytes(content)

    def _batch_from(
        self, status: int, content: bytes, expected: int, latency_ms: int
    ) -> EmbeddingBatch:
        """Read the response without trusting its status, shape, or provenance."""
        _check_status(status)
        body = self._check_provenance(_json_object(content))
        tokens = body.get("input_tokens")
        if type(tokens) is not int or tokens < 0:
            raise EmbeddingMalformedResponse("the response has no token usage")
        return checked_batch(
            self._identity,
            body.get("vectors"),
            EmbeddingUsage(input_tokens=tokens, latency_ms=latency_ms),
            expected=expected,
        )

    def _check_provenance(self, body: dict[str, object] | None) -> dict[str, object]:
        if body is None:
            raise EmbeddingMalformedResponse("the local model response was not JSON")
        dimensions = body.get("dimensions")
        if (
            body.get("model") != self._settings.model
            or body.get("revision") != self._settings.revision
            # 1024.0 == 1024 in Python, so the type is checked as well.
            or type(dimensions) is not int
            or dimensions != self._settings.dimensions
        ):
            # Another model's vectors would be stored in this model's space.
            raise EmbeddingRequestRefused(
                "the local model is not the configured model, revision, or size"
            )
        return body


def _check_status(status: int) -> None:
    if status == 429:
        raise EmbeddingRateLimited()
    if status in (408, 504):
        raise EmbeddingTimeout()
    if status >= 500:
        raise EmbeddingUnavailable()
    if status != 200:
        raise EmbeddingRequestRefused(
            f"the local model refused the request with status {status}"
        )


def _json_object(content: bytes) -> dict[str, object] | None:
    try:
        body: object = json.loads(content)
    except ValueError, RecursionError:
        # RecursionError: JSON nested deeper than the parser allows.
        return None
    if not isinstance(body, dict):
        return None
    return cast(dict[str, object], body)


def _since(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1_000)


def _log(
    request_id: UUID,
    identity: EmbeddingModelIdentity,
    inputs: int,
    latency_ms: int,
    error: EmbeddingError | None,
    usage: EmbeddingUsage | None = None,
) -> None:
    """Record one call: identifiers, counts, timing, and the outcome. No text."""
    fields: dict[str, object] = {
        "request_id": str(request_id),
        "provider": identity.provider,
        "model": identity.model,
        "dimensions": identity.dimensions,
        "inputs": inputs,
        "latency_ms": latency_ms,
        "outcome": "succeeded" if error is None else type(error).__name__,
    }
    if usage is not None:
        fields["input_tokens"] = usage.input_tokens
    level = logging.INFO if error is None else logging.WARNING
    logger.log(level, "embedding request finished", extra={"embedding": fields})
```

Перевірка: `make typecheck` показує `0 errors`. `uv run python -c "from knowledge_service.embeddings.local_adapter import LocalEmbeddingSettings as S; print(S().url)"` друкує `http://127.0.0.1:8765`.

### Крок 6 — Крок із програмуванням: протестуйте адаптер

Цей крок доводить, що адаптер виконує обіцянки порту, за допомогою фейкового сервера всередині тестового процесу.

**Простими словами:** Сервер-замінник відповідає через транспорт `httpx` у пам'яті, тож справжній адаптер надсилає справжні HTTP-запити, які не покидають тест. Спільний контрактний набір тепер проганяє кожну свою перевірку і для локального адаптера. Окремі тести покривають те, що є лише в цього адаптера: перевірку походження, кожен вид збою транспорту, зіпсовані відповіді, обмеження розміру, перевірку URL і мовчання про тексти.

| Тест | Доводить |
|---|---|
| контрактний набір, випадок `local` | ті самі шість обіцянок порту, що й для фейкового і OpenAI-адаптерів |
| `test_vectors_from_other_weights_are_refused` | найризикованіший край: інша модель, ревізія чи розмір, навіть `1024.0`, дає `EmbeddingRequestRefused` |
| `test_each_failure_becomes_one_typed_error` | зупинений сервер дає `EmbeddingUnavailable` (типізована помилка); кожен статус і зламане тіло перетворюються на одну помилку порту |
| `test_a_malformed_body_is_refused` | неправильні кількості токенів і списки векторів відхиляються |
| `test_a_failure_carries_no_text_in_its_chain_or_the_logs` | підкинутий текст ніколи не з'являється ні в помилці, ні в пов'язаних помилках, ні в записі логу |
| `test_the_server_check_...` | `verify_server` не надсилає тексту, відкидає інші ваги, повідомляє про зупинений сервер і тримає свій дедлайн |
| `test_an_answer_over_the_size_cap_is_refused_unread` | правильна відповідь, доповнена понад 8 МБ, відхиляється |
| `test_a_url_that_is_not_http_is_refused_at_start_up` | зламаний URL падає в налаштуваннях, а не посеред запиту |
| `test_letters_the_tokenizer_keeps_apart_are_counted_apart` | межа правильно рахує корейську, набрану окремими літерами |

Створіть `tests/local_stubs.py`:

```python
"""A stand-in for the local BGE-M3 server, for deterministic tests.

`StubBgeM3Server` answers through `httpx.MockTransport`, so the real
`LocalEmbeddingModel` sends real HTTP requests that never leave the process.
"""

import asyncio
import json

import httpx

from knowledge_service.embeddings.local_adapter import (
    BGE_M3_REVISION,
    LocalEmbeddingModel,
    LocalEmbeddingSettings,
)
from openai_stubs import vector_for

URL = "http://bge-m3.test"


class StubBgeM3Server:
    """Answer like the BGE-M3 server; record every request."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes | None = None,
        model: str = "BAAI/bge-m3",
        revision: str = BGE_M3_REVISION,
        dimensions: float = 1024,
        vector_dimensions: int | None = None,
        error: Exception | None = None,
        block: bool = False,
    ) -> None:
        self._status = status
        self._body = body
        self._provenance = {
            "model": model,
            "revision": revision,
            "dimensions": dimensions,
        }
        self._vector_dimensions = vector_dimensions or int(dimensions)
        self._error = error
        self._block = block
        self.requests: list[httpx.Request] = []

    @property
    def sent(self) -> int:
        """How many requests carried texts to embed."""
        return sum(1 for request in self.requests if request.method == "POST")

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._block:
            await asyncio.Event().wait()
        if self._error is not None:
            raise self._error
        if self._body is not None or self._status != 200:
            return httpx.Response(self._status, content=self._body or b"{}")
        if request.method == "GET":
            return httpx.Response(200, json=self._provenance)
        texts: list[str] = json.loads(request.content)["inputs"]
        return httpx.Response(
            200,
            json={
                **self._provenance,
                "input_tokens": 3 * len(texts),
                "vectors": [vector_for(t, self._vector_dimensions) for t in texts],
            },
        )


def local_embedding_model(
    server: StubBgeM3Server, settings: LocalEmbeddingSettings | None = None
) -> LocalEmbeddingModel:
    """The real adapter, talking to `server` through an in-process transport."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(server.handle))
    return LocalEmbeddingModel(client, settings or LocalEmbeddingSettings(url=URL))
```

Додайте локальний адаптер до контрактного набору. У `tests/test_embedding_model_contract.py`:

Знайдіть у `tests/test_embedding_model_contract.py`:

```python
from openai_stubs import StubEmbeddings, openai_embedding_model, status_error
```

Замініть на:

```python
from local_stubs import StubBgeM3Server, local_embedding_model
from openai_stubs import StubEmbeddings, openai_embedding_model, status_error
```

Знайдіть у `tests/test_embedding_model_contract.py`:

```python
    return Adapter(model=model, sent=lambda: len(embeddings.calls))


CASES = (
```

Замініть на:

```python
    return Adapter(model=model, sent=lambda: len(embeddings.calls))


def local_adapter(server: StubBgeM3Server) -> Adapter:
    return Adapter(model=local_embedding_model(server), sent=lambda: server.sent)


CASES = (
```

Знайдіть у `tests/test_embedding_model_contract.py`:

```python
        blocked=lambda: openai_adapter(StubEmbeddings(block=True)),
    ),
)
```

Замініть на:

```python
        blocked=lambda: openai_adapter(StubEmbeddings(block=True)),
    ),
    AdapterCase(
        name="local",
        working=lambda: local_adapter(StubBgeM3Server()),
        rate_limited=lambda: local_adapter(StubBgeM3Server(status=429)),
        wrong_dimensions=lambda: local_adapter(StubBgeM3Server(vector_dimensions=3)),
        blocked=lambda: local_adapter(StubBgeM3Server(block=True)),
    ),
)
```

Створіть `tests/test_local_embeddings.py`:

```python
"""The local BGE-M3 adapter: provenance, transport errors, and quiet failures."""

import asyncio
import json
import logging
import math
import unicodedata
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from knowledge_service.embeddings.local_adapter import (
    BGE_M3_REVISION,
    MAX_RESPONSE_BYTES,
    LocalEmbeddingSettings,
)
from knowledge_service.embeddings.port import (
    EmbeddingError,
    EmbeddingMalformedResponse,
    EmbeddingModelIdentity,
    EmbeddingRequestRefused,
    EmbeddingTimeout,
    EmbeddingUnavailable,
)
from local_stubs import URL, StubBgeM3Server, local_embedding_model

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000031")
PLANTED = "the salary of Olena is 9000"


async def embed(server: StubBgeM3Server, texts: list[str]) -> None:
    model = local_embedding_model(server)
    await model.embed(texts, request_id=REQUEST_ID, timeout_seconds=2.0)


def chain(error: BaseException) -> list[BaseException]:
    """The error and every error linked to it, as a traceback would print."""
    seen: list[BaseException] = []
    pending: list[BaseException | None] = [error]
    while pending:
        item = pending.pop()
        if item is None or any(item is known for known in seen):
            continue
        seen.append(item)
        pending += [item.__cause__, item.__context__]
    return seen


def test_the_identity_pins_the_weights_revision() -> None:
    model = local_embedding_model(StubBgeM3Server())

    assert model.identity == EmbeddingModelIdentity(
        provider="local",
        model=f"BAAI/bge-m3@{BGE_M3_REVISION}",
        dimensions=1024,
    )


async def test_one_request_carries_the_texts_and_the_request_id() -> None:
    server = StubBgeM3Server()
    model = local_embedding_model(server)

    batch = await model.embed(
        ["vacation policy", "відпустка"], request_id=REQUEST_ID, timeout_seconds=2.0
    )

    [request] = server.requests
    assert request.url == httpx.URL(f"{URL}/v1/embeddings")
    assert request.headers["X-Request-ID"] == str(REQUEST_ID)
    assert json.loads(request.content) == {"inputs": ["vacation policy", "відпустка"]}
    assert batch.usage.input_tokens == 6


@pytest.mark.parametrize(
    "server",
    [
        StubBgeM3Server(model="BAAI/bge-m3-unsupervised"),
        StubBgeM3Server(revision="0" * 40),
        StubBgeM3Server(dimensions=768, vector_dimensions=1024),
        StubBgeM3Server(dimensions=1024.0),
    ],
    ids=["other model", "other revision", "other dimensions", "dimensions as float"],
)
async def test_vectors_from_other_weights_are_refused(server: StubBgeM3Server) -> None:
    # The highest-risk edge: these vectors would be stored in this model's
    # Embedding Space and silently mixed with vectors they cannot be compared to.
    with pytest.raises(EmbeddingRequestRefused):
        await embed(server, ["vacation policy"])


@pytest.mark.parametrize(
    ("server", "error"),
    [
        (StubBgeM3Server(error=httpx.ConnectError("refused")), EmbeddingUnavailable),
        (StubBgeM3Server(error=httpx.ReadTimeout("slow")), EmbeddingTimeout),
        (StubBgeM3Server(status=503), EmbeddingUnavailable),
        (StubBgeM3Server(status=500), EmbeddingUnavailable),
        (StubBgeM3Server(status=504), EmbeddingTimeout),
        (StubBgeM3Server(status=413), EmbeddingRequestRefused),
        (StubBgeM3Server(status=422), EmbeddingRequestRefused),
        (StubBgeM3Server(status=404), EmbeddingRequestRefused),
        (StubBgeM3Server(body=b"not json"), EmbeddingMalformedResponse),
        (StubBgeM3Server(body=b"[" * 100_000), EmbeddingMalformedResponse),
        (StubBgeM3Server(body=b"[]"), EmbeddingMalformedResponse),
    ],
    ids=[
        "no server",
        "read timeout",
        "busy",
        "server error",
        "gateway timeout",
        "too long",
        "invalid",
        "wrong url",
        "not json",
        "nested too deep",
        "not an object",
    ],
)
async def test_each_failure_becomes_one_typed_error(
    server: StubBgeM3Server, error: type[EmbeddingError]
) -> None:
    with pytest.raises(error):
        await embed(server, ["vacation policy"])


@pytest.mark.parametrize(
    "fields",
    [
        {"input_tokens": True, "vectors": [[1.0] * 1024]},
        {"input_tokens": -1, "vectors": [[1.0] * 1024]},
        {"vectors": [[1.0] * 1024]},
        {"input_tokens": 3, "vectors": "1.0"},
        {"input_tokens": 3, "vectors": [[1.0] * 1024, [1.0] * 1024]},
    ],
    ids=["bool tokens", "negative tokens", "no tokens", "string", "extra vector"],
)
async def test_a_malformed_body_is_refused(fields: dict[str, object]) -> None:
    body = {"model": "BAAI/bge-m3", "revision": BGE_M3_REVISION, "dimensions": 1024}
    server = StubBgeM3Server(body=json.dumps({**body, **fields}).encode())

    with pytest.raises(EmbeddingMalformedResponse):
        await embed(server, ["vacation policy"])


async def test_a_failure_carries_no_text_in_its_chain_or_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The transport error holds the request, and the request holds the text.
    server = StubBgeM3Server(error=httpx.ConnectError(PLANTED))
    caplog.set_level(logging.DEBUG)

    with pytest.raises(EmbeddingUnavailable) as raised:
        await embed(server, [PLANTED])

    assert all(PLANTED not in repr(item) for item in chain(raised.value))
    assert raised.value.__context__ is None
    [record] = caplog.records
    assert PLANTED not in caplog.text
    assert PLANTED not in repr(record.__dict__)
    assert record.__dict__["embedding"]["outcome"] == "EmbeddingUnavailable"


async def test_the_server_check_sends_no_text_and_refuses_other_weights() -> None:
    good = StubBgeM3Server()
    other = StubBgeM3Server(revision="0" * 40)

    await local_embedding_model(good).verify_server(timeout_seconds=2.0)
    with pytest.raises(EmbeddingRequestRefused):
        await local_embedding_model(other).verify_server(timeout_seconds=2.0)

    assert [request.method for request in good.requests] == ["GET"]
    assert good.requests[0].url == httpx.URL(f"{URL}/v1/model")


async def test_the_server_check_reports_a_stopped_server() -> None:
    server = StubBgeM3Server(error=httpx.ConnectError("refused"))

    with pytest.raises(EmbeddingUnavailable):
        await local_embedding_model(server).verify_server(timeout_seconds=2.0)


async def test_a_trailing_slash_in_the_url_is_ignored() -> None:
    server = StubBgeM3Server()
    model = local_embedding_model(server, LocalEmbeddingSettings(url=f"{URL}/"))

    await model.embed(["vacation policy"], request_id=REQUEST_ID, timeout_seconds=2.0)

    assert server.requests[0].url == httpx.URL(f"{URL}/v1/embeddings")


@pytest.mark.parametrize(
    "url", ["http://[::1", "ftp://127.0.0.1:8765", "127.0.0.1:8765", "http://"]
)
def test_a_url_that_is_not_http_is_refused_at_start_up(url: str) -> None:
    with pytest.raises(ValidationError):
        LocalEmbeddingSettings(url=url)


async def test_the_server_check_keeps_the_configured_deadline() -> None:
    server = StubBgeM3Server(block=True)
    settings = LocalEmbeddingSettings(url=URL, timeout_seconds=0.05)
    model = local_embedding_model(server, settings)

    with pytest.raises(EmbeddingTimeout):
        await asyncio.wait_for(model.verify_server(timeout_seconds=math.inf), 2.0)
    with pytest.raises(ValueError, match="timeout_seconds"):
        await model.verify_server(timeout_seconds=math.nan)


def test_letters_the_tokenizer_keeps_apart_are_counted_apart() -> None:
    # Korean typed as separate letters (as macOS stores file names) stays
    # three tokens per syllable; NFKC alone would count it as one.
    model = local_embedding_model(StubBgeM3Server())
    text = unicodedata.normalize("NFD", "\uac01" * 10)

    assert model.count_tokens(text) >= 30 + 3


async def test_an_answer_over_the_size_cap_is_refused_unread() -> None:
    # A valid answer padded past the cap: nothing beyond the cap is trusted.
    valid = {
        "model": "BAAI/bge-m3",
        "revision": BGE_M3_REVISION,
        "dimensions": 1024,
        "input_tokens": 3,
        "vectors": [[1.0] * 1024],
    }
    padded = json.dumps(valid).encode() + b" " * MAX_RESPONSE_BYTES

    with pytest.raises(EmbeddingMalformedResponse, match="too large"):
        await embed(StubBgeM3Server(body=padded), ["vacation policy"])
```

Перевірка, спершу найвужчий тест:

```bash
uv run pytest tests/test_local_embeddings.py -q
uv run pytest tests/test_embedding_model_contract.py -q
```

Очікувано: `33 passed`, потім `18 passed, 1 deselected` (по шість перевірок для кожного з трьох адаптерів; пропущений — це необов'язковий живий тест OpenAI).

### Крок 7 — Крок із програмуванням: додайте локальну опцію до скрипта

Цей крок дає змогу прогнати 16 запитань з локальною моделлю з командного рядка.

**Простими словами:** Скрипт отримує другу опцію моделі поруч з OpenAI. З `--local-embeddings` він спершу питає сервер, яку модель той запускає, тож зупинений сервер чи неправильна версія падають одразу, а база лишається порожньою. Потім він проганяє те саме порівняння трьома способами, що й у M05-T08, і пише звіт у власний файл.

| Зміна | Навіщо |
|---|---|
| `--local-embeddings`, несумісна з `--managed-embeddings` | одна модель на прогін, один поточний Embedding Space |
| `ModelSource` і дві функції `_..._model` | кожна відкриває і закриває власний клієнт навколо прогону |
| `verify_server` перед `compare_branches` | про зупинений сервер повідомляється до того, як щось записано |
| `httpx.AsyncClient(trust_env=False)` | проксі зі змінних середовища ніколи не побачить текстів |
| `artifacts/evaluations/bilingual-seed-local.md` | окремий звіт, який Git ігнорує |
| `except EmbeddingError` | друкує `the embedding model is not ready: ...` і завершується з кодом 1 |

Повністю замініть `scripts/run_retrieval_dataset.py` на:

```python
"""Verify the bilingual seed dataset and, given a database, run every case.

`--verify-only` checks the cases against the corpus and needs no database.
Otherwise point `KNOWLEDGE_SERVICE_DATABASE_URL` at an empty, migrated
database; the run refuses any other. By default only word search runs and
the report goes to `artifacts/evaluations/bilingual-seed.md`.

`--managed-embeddings` also embeds the corpus with the OpenAI model set by
`EMBEDDING_MODEL` and `EMBEDDING_DIMENSIONS` (default
`text-embedding-3-small`, 1536), using `KNOWLEDGE_SERVICE_MODEL_API_KEY`.
It runs every case three ways, words only, meaning only, and both fused, and
writes `artifacts/evaluations/bilingual-seed-managed.md`. Git ignores both
reports. Every Chunk, the restricted ones included, is sent to OpenAI; the
seed corpus is made up, so nothing private leaves the machine.

`--local-embeddings` does the same with the local BGE-M3 server instead
(`LOCAL_EMBEDDING_URL`, default `http://127.0.0.1:8765`), which must already
run the pinned revision; start it with
`uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py`.
The report goes to `artifacts/evaluations/bilingual-seed-local.md`, and no
text leaves the machine.
"""

import argparse
import asyncio
import sys
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from pydantic import SecretStr

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.local_adapter import (
    LocalEmbeddingModel,
    LocalEmbeddingSettings,
)
from knowledge_service.embeddings.openai_adapter import (
    OpenAIEmbeddingModel,
    OpenAIEmbeddingSettings,
)
from knowledge_service.embeddings.port import EmbeddingError, EmbeddingModel
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DEFAULT_LIMIT,
    DatasetError,
    EvaluationDataset,
    compare_branches,
    load_dataset,
    render_comparison,
    render_report,
    run_dataset,
    verify_corpus,
)
from knowledge_service.settings import Settings

REPORT = Path("artifacts/evaluations/bilingual-seed.md")
MANAGED_REPORT = Path("artifacts/evaluations/bilingual-seed-managed.md")
LOCAL_REPORT = Path("artifacts/evaluations/bilingual-seed-local.md")
SERVER_CHECK_SECONDS = 10.0

type ModelSource = Callable[[], AbstractAsyncContextManager[EmbeddingModel]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_DIRECTORY)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--verify-only", action="store_true")
    embeddings = parser.add_mutually_exclusive_group()
    embeddings.add_argument("--managed-embeddings", action="store_true")
    embeddings.add_argument("--local-embeddings", action="store_true")
    arguments = parser.parse_args()
    corpus = arguments.dataset / CORPUS_DIRECTORY
    try:
        dataset = load_dataset(arguments.dataset / CASES_FILE)
        if arguments.verify_only:
            asyncio.run(verify_corpus(dataset, corpus))
            sys.stdout.write(f"{len(dataset.cases)} cases verified\n")
            return 0
        # Read only for a run, so a bad setting cannot stop the check above.
        settings = Settings()
        api_key = settings.model_api_key
        if arguments.managed_embeddings and api_key is None:
            sys.stderr.write(
                "set KNOWLEDGE_SERVICE_MODEL_API_KEY to embed the corpus\n"
            )
            return 1
        source: ModelSource | None = None
        default = REPORT
        if arguments.managed_embeddings and api_key is not None:
            source, default = _openai_model(api_key), MANAGED_REPORT
        if arguments.local_embeddings:
            source, default = _local_model, LOCAL_REPORT
        runtime = create_database_runtime(settings)
        rendered = asyncio.run(_run(runtime, dataset, corpus, arguments.limit, source))
    except DatasetError as error:
        sys.stderr.write(f"dataset run failed: {error}\n")
        return 1
    except EmbeddingError as error:
        sys.stderr.write(f"the embedding model is not ready: {error!r}\n")
        return 1
    output: Path = arguments.output or default
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"{rendered}\nFull report: {output}\n")
    return 0


async def _run(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    limit: int,
    source: ModelSource | None,
) -> str:
    observed_at = datetime.now(UTC)
    try:
        if source is None:
            report = await run_dataset(
                runtime, dataset, corpus, observed_at=observed_at, limit=limit
            )
            return render_report(report)
        async with source() as model:
            reports = await compare_branches(
                runtime,
                dataset,
                corpus,
                observed_at=observed_at,
                model=model,
                limit=limit,
            )
        return render_comparison(reports)
    finally:
        await runtime.dispose()


def _openai_model(api_key: SecretStr) -> ModelSource:
    @asynccontextmanager
    async def source() -> AsyncGenerator[EmbeddingModel]:
        client = AsyncOpenAI(api_key=api_key.get_secret_value())
        try:
            yield OpenAIEmbeddingModel(client, OpenAIEmbeddingSettings())
        finally:
            await client.close()

    return source


@asynccontextmanager
async def _local_model() -> AsyncGenerator[EmbeddingModel]:
    # trust_env=False: a proxy set in the environment must never see the texts.
    async with httpx.AsyncClient(trust_env=False) as client:
        model = LocalEmbeddingModel(client, LocalEmbeddingSettings())
        # Before anything is written, so a stopped server leaves the database empty.
        await model.verify_server(timeout_seconds=SERVER_CHECK_SECONDS)
        yield model


if __name__ == "__main__":
    sys.exit(main())
```

Також задокументуйте налаштування. У кінець `.env.example` додайте:

```text

# The local BGE-M3 server (ml/bge_m3_server); the default is shown.
# LOCAL_EMBEDDING_URL=http://127.0.0.1:8765
```

Перевірка: із зупиненим сервером і мігрованою порожньою базою в `KNOWLEDGE_SERVICE_DATABASE_URL` команда `uv run python scripts/run_retrieval_dataset.py --local-embeddings` друкує `the embedding model is not ready: EmbeddingUnavailable()` і завершується з кодом 1. Якщо передати обидві опції моделі, скрипт надрукує `not allowed with argument`. `--verify-only` і далі друкує `16 cases verified`.

### Крок 8 — Без програмування: прогін локально і порівняння з OpenAI

Цей крок запускає справжню модель і порівнює її з керованою. Він також навмисно ламає перевірку походження, щоб побачити, як тести це ловлять.

**Дія 1:** запустіть сервер в одному терміналі і залиште його працювати:

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py
```

**Дія 2:** у другому терміналі зробіть два прогони на свіжій базі й порівняйте:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_dataset#')"
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --local-embeddings
cp artifacts/evaluations/bilingual-seed-local.md /tmp/first-local.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --local-embeddings > /dev/null
diff /tmp/first-local.md artifacts/evaluations/bilingual-seed-local.md && echo same
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
unset KNOWLEDGE_SERVICE_DATABASE_URL
```

Кожен прогін триває кілька секунд, коли модель уже завантажена. Жоден текст не покидає ваш комп'ютер.

**Дія 3:** порівняйте з OpenAI. Файл доказів M05-T08 містить керований звіт. У своєму файлі доказів складіть одну таблицю: рядок на зріз і чотири стовпці — керований щільний Recall@10 і MRR, локальний щільний Recall@10 і MRR. Потім подивіться на три речі:

- Міжмовні зрізи `en->uk` і `uk->en`, де керована модель була найслабшою за MRR.
- Зріз ідентифікаторів: чи локальна модель теж ставить кожен код першим.
- Об'єднаний прогін проти щільного для кожної моделі: чи допомагає комусь із них пошук за словами?

Запишіть щонайменше одне **спростоване припущення** у формі «Я припускав X. Звіт показує Y (ID запитань і числа). Отже, Z хибне».

**Дія 4:** навмисно зламайте перевірку походження. У `src/knowledge_service/embeddings/local_adapter.py`, у `_check_provenance`, поставте `#` перед рядком `or body.get("revision") != self._settings.revision`. Потім запустіть:

```bash
uv run pytest tests/test_local_embeddings.py -q
```

Очікувано: 2 failed, `test_vectors_from_other_weights_are_refused[other revision]` і `test_the_server_check_sends_no_text_and_refuses_other_weights`. Сервер з іншими вагами тепер вважався б надійним. Приберіть `#` і запускайте тести, доки вони не пройдуть. Коли закінчите, зупиніть сервер через Ctrl+C.

### Крок 9 — Без програмування: перевірки, документація, докази і прогрес

Цей крок доводить, що все досі працює, оновлює текст архітектури, який зміна зробила неповним, і записує результат.

**Дія 1:** у `docs/architecture.md` замініть рядок `embeddings` у таблиці «Code layout» на:

```markdown
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `publish_document_embeddings`, the migration workflow |
```

Потім одразу після абзацу, що починається з «Domain values, settings, the database runtime», додайте:

```markdown
Model runtimes that need their own Python live outside `src/`, each as its
own uv project (ADR 0009).
`ml/bge_m3_server/` serves BGE-M3 dense embeddings on Python 3.12, on
127.0.0.1 only. The application reaches it through `LocalEmbeddingModel`,
which refuses any answer that does not name the pinned model revision.
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_local_embeddings.py tests/test_embedding_model_contract.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make ml-check
make docs-check
```

Очікувано: усе проходить. Еталонний прогін дав `make test-unit` 1238 passed, `make test-integration` 300 passed і `make ml-check` 16 passed. Один старіший hypothesis-тест з M05-T07 упав один раз із чотирьох повних прогонів, поки у фоні працював сервер моделі; окремо він проходить. Якщо побачите це, зупиніть сервер і запустіть ще раз.

**Дія 3:** заповніть файл доказів:

- **Design:** змінені файли; чому окремий процес на Python 3.12; як закріплюється і перевіряється походження; чому кількість токенів — верхня межа і як її перевірено; чому сервер ніколи не обрізає.
- **Verification:** команди і кількості, таблиця поруч із кроку 8, результат повторюваності, спростоване припущення, навмисна поломка і відомі обмеження з розділу «Перш ніж почати». Вставте в файл доказів зведену таблицю локального звіту: `artifacts/` Git ігнорує.
- **Review:** ворожий рев'ю прототипу знайшов чотири проблеми і три дрібніші. Усі виправлено, кожну з регресійним тестом, що падав до виправлення. Одна лише NFKC недораховувала корейську, набрану окремими літерами, аж утричі; тепер межа бере довшу з NFKC і NFKD. Проксі зі змінних середовища отримував тексти; тепер клієнт скрипта ігнорує середовище. Відповідь сервера читалася цілком, тож зламаний сервер міг заповнити пам'ять; тепер вона обмежена 8 МБ. Один величезний текст зупиняв сервер; тепер тексти обмежені за довжиною і рахуються поза циклом запитів. Дрібніші: `1024.0` проходило як `1024`, зламаний URL падав без типу посеред запиту, а `verify_server` ігнорував налаштований дедлайн.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте завдання виконаним, додайте рядок M05-T09 у `docs/curriculum/PROGRESS.md`, зробіть наступним завданням M05-T10 і закомітьте з повідомленням `m05-t09: add local bge m3 embeddings`.

## Запитання для рефлексії

1. Що `LocalEmbeddingModel` ховає від коду, який його викликає? Перелічіть, про що викликачеві ніколи не треба думати, наприклад про URL, HTTP-коди статусу чи ревізію, яку запускає сервер.
2. Хтось пізніше «оптимізує» адаптер і перевіряє походження лише раз, у `verify_server`, а не в кожній відповіді. Що тоді може піти не так під час довгого прогону ембедінгів, і як хтось би це помітив?
3. Кількість токенів — верхня межа, для звичайного тексту приблизно втричі завелика. Чого це коштує сьогодні, і що зламалося б, якби хтось замінив її оцінкою, яка іноді буває замалою?
4. Яке з ваших передбачень виявилося хибним, і що ви думали про локальні чи відкриті моделі, через що так передбачили?
5. Шістнадцять вигаданих запитань не віддають переваги жодній моделі. Яке справжнє запитання ви б додали, щоб з'ясувати, чи локальна модель справді така ж добра, як керована?

## Чек-лист завершення

- [ ] Файл доказів містить інваріант і ваші передбачення, записані до будь-якого запуску.
- [ ] `ml/bge_m3_server/` має власні `pyproject.toml`, `uv.lock`, сервер і тести; `make ml-check` проходить.
- [ ] Сервер закріплює ревізію, перевіряє відбитки файлів, слухає 127.0.0.1 і відмовляє задовгому тексту, а не обрізає його.
- [ ] `LocalEmbeddingModel` проходить спільний контрактний набір тестів.
- [ ] Вектори від іншої моделі, ревізії чи розміру дають `EmbeddingRequestRefused`; зупинений сервер дає `EmbeddingUnavailable`.
- [ ] Жоден підкинутий текст не з'являється в помилках, пов'язаних помилках, логах чи відповідях сервера.
- [ ] Ви двічі запустили `--local-embeddings` і порівняли результат із керованим звітом в одній таблиці.
- [ ] Файл доказів називає щонайменше одне спростоване припущення.
- [ ] Ви зробили навмисну поломку з кроку 8, побачили два тести, що впали, і скасували її.
- [ ] Усі перевірки проходять: формат, lint, типи, юніт, інтеграційні, ML, документація.
- [ ] Текст архітектури, докази, рядок у PROGRESS і коміт готові.

Коли напишете файли, покажіть їх або вивід помилок, і я перевірю їх за цим чек-листом.
