# M05-T11 — Локальний cross-encoder reranker

Англійська версія: [M05-T11-implement-a-local-cross-encoder-reranker.md](M05-T11-implement-a-local-cross-encoder-reranker.md)

Вихідне завдання: [M05-T11](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T11-implement-a-local-cross-encoder-reranker.md)

## Що ви зробите

Пошук (retrieval) швидко знаходить приблизно десять кандидатів (Candidates) на запитання, але їхній порядок грубий: відстань між векторами чи збіг слів рахується для кожного Chunk окремо. Reranker — це друга, повільніша модель, яка читає запитання і текст одного кандидата разом і каже, наскільки добре цей текст відповідає саме на це запитання. У цьому уроці ви запустите багатомовний reranker локально, сховаєте його за невеликим портом і виміряєте, чи частіше правильний Chunk стає першим, і скільки це коштує часу на CPU та на GPU Mac. У продакшені нічого не змінюється: жоден пошук за замовчуванням поки не переранжовує.

- Другий сервер моделі, `reranker_server.py`, запускає `BAAI/bge-reranker-v2-m3` поруч із сервером BGE-M3 і повертає одну оцінку на кожен уривок.
- Порт `Reranker` описує, що обіцяє будь-який reranker: межі, значення оцінки, типізовані помилки і жодного тексту в помилках.
- `rerank_candidates` надсилає кандидатів у reranker пачками, які той приймає, під одним дедлайном, і повертає тих самих кандидатів у новому порядку.
- `LocalReranker` — адаптер: він говорить із сервером через HTTP і відкидає оцінки від будь-якої іншої ревізії моделі.
- `compare_reranking` і опція скрипту `--rerank` проганяють 16 seed-запитань через злитий пошук, переранжовують кожне і показують, як змінилися місця і скільки часу це зайняло.

| Завдання вимагає | Де це відбувається |
|---|---|
| Обмежений порт reranker-а | Крок 4: `Reranker`, `RerankLimits`, перевірки до надсилання, `rerank_candidates` |
| Локальний багатомовний адаптер | Кроки 2 і 5: сервер reranker-а і `LocalReranker` |
| Розмір пачок | Крок 4: `rerank_candidates` ділить кандидатів за кількістю уривків і токенів |
| Значення оцінки | Крок 4: оцінка — це сире число релевантності від моделі; більше — краще; порівнюється лише в межах одного запитання й однієї моделі |
| Дедлайни | Крок 4: один дедлайн на всі запити одного виклику; кожен запит отримує лише час, що лишився |
| Походження моделі | Кроки 2 і 5: закріплена ревізія і відбитки файлів на сервері; адаптер перевіряє їх у кожній відповіді |
| Контрактні й оцінювальні тести: зміна ранжування, затримка на CPU/GPU | Кроки 6 і 9: спільні контрактні тести, розділи «Ranking delta» і «Latency», прогони на `cpu` і `mps` |
| Детерміновані тести: успіх, найризиковіший край, типізована помилка | Кроки 6 і 8: reranking переставляє; закритий текст ніколи не доходить до reranker-а; збій reranker-а дає типізовану помилку |
| Жодного підкинутого чутливого тексту в телеметрії | Кроки 3, 6 і 8: відповіді, помилки, логи і звіти перевіряються на підкинутий текст |

Цей урок містить код: кроки 2–8 пишуть код. Кроки 1, 9 і 10 коду не потребують. Крок 9 запускає обидві моделі локально; це безкоштовно, але reranker важить близько 2,3 ГБ.

Терміни цього уроку:

- **Bi-encoder:** модель, яка перетворює запитання і кожен Chunk на вектори окремо, як BGE-M3 у dense-пошуку. Chunks кодуються один раз, заздалегідь, тож пошук швидкий.
- **Cross-encoder (reranker):** модель, яка читає запитання й один уривок разом, як один вхід, і повертає одне число. Вона бачить, як слова обох текстів пов'язані, тому точніша, але мусить працювати для кожної пари в момент запитання.
- **Логіт (logit):** сире число, яке видає класифікатор, перш ніж його перетворять на ймовірність. Тут більше — значить релевантніше; −11 означає «точно ні», +4 — «точно так».
- **Токени пари:** токени `<s> question </s></s> passage </s>`, єдиного входу, який читає reranker.
- **Пачка (batch):** уривки, надіслані одним запитом. Менше більших пачок — менше накладних витрат; межі сервера обмежують їхній розмір.
- **MPS:** Metal Performance Shaders, GPU-бекенд Mac у PyTorch. `cpu` і `mps` — два пристрої, які ви порівняєте.

## Перш ніж почати

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілий, коли ви до нього дійдете.

- **Чому `BAAI/bge-reranker-v2-m3`.** Він багатомовний, побудований на BGE-M3 і має ліцензію Apache-2.0. У нього той самий токенізатор і словник, що в BGE-M3, тож межа токенів, якій ви вже довіряєте, підходить і тут. Близько 568 мільйонів параметрів, приблизно 2,3 ГБ ваг. Сервер закріплює ревізію `953dc6f6` і перевіряє SHA-256 файлів ваг і токенізатора, перш ніж щось обслуговувати.
- **Де працює модель.** В окремому процесі, `ml/bge_m3_server/reranker_server.py`, на порту 8767. Він використовує uv-проєкт сервера BGE-M3, бо потребує тих самих бібліотек (ADR 0009 тримає середовища моделей поза Python застосунку). Окремий процес означає, що reranker можна зупинити, перезапустити чи перенести на машину з GPU, не чіпаючи ембедінгів. `transformers` тепер названо прямою залежністю, бо сервер його імпортує.
- **Де живе код.** У пакеті `retrieval`, бо reranking — частина модуля Retrieval у `docs/architecture.md`. ADR 0013 вимагає записаної причини, перш ніж створювати новий пакет, а її поки немає. `retrieval/reranking.py` містить порт і `rerank_candidates`; `retrieval/local_reranker.py` — адаптер.
- **Значення оцінки.** Оцінка — це сирий логіт моделі: будь-яке скінченне число, більше — релевантніше. Оцінки порівнюються лише в межах одного запитання й однієї ідентичності моделі. Це не ймовірності, і поріг не застосовується, тож reranker може лише переставляти, але ніколи не вирішує, що кандидат «достатньо нерелевантний», щоб його викинути.
- **Лише новий порядок.** `rerank_candidates` повертає рівно тих кандидатів, яких отримав, кожного один раз. Рівні оцінки зберігають порядок пошуку. Функція ніколи не читає базу, тож кожен кандидат, якого вона повертає, уже пройшов перевірку прав у пошуку, що його знайшов. Закритий Chunk ніколи не доходить до reranker-а, бо пошук його не повертав: авторизація відбувається до того, як текст покине сховище.
- **Розмір пачок.** Сервер приймає щонайбільше 64 уривки, 8192 токени на пару і 16 384 токени на запит. Значення за замовчуванням в адаптері суворіші: 16 уривків на запит. `rerank_candidates` заповнює запит у порядку пошуку, доки наступний уривок не порушить межу, тоді починає наступний, тож десять кандидатів зазвичай ідуть одним запитом.
- **Рахуємо токени без токенізатора.** У застосунку немає токенізатора. `LocalReranker.count_pair_tokens` додає верхні межі обох текстів — ту саму межу, що й `LocalEmbeddingModel.count_tokens`: символи після нормалізації плюс три. Пара додає чотири службові токени і два маркери початку слова, рівно ті шість, що два тексти додають поодинці. Тест сервера перевіряє цю межу на справжньому токенізаторі.
- **Відмовити, а не обрізати.** Як і з ембедінгами, сервер відмовляє в задовгій парі, а не обрізає її. Обрізаний уривок оцінювався б лише за своїм початком, а оцінка виглядала б такою ж надійною, як будь-яка інша.
- **Один дедлайн.** `rerank_candidates` отримує один `timeout_seconds` на весь виклик, включно з підрахунком токенів. Кожен запит отримує лише час, що лишився, і виклик кидає `RerankerTimeout`, щойно дедлайн минув. Reranker, який блокує event loop, не можна перервати, але його запізніла відповідь однаково відкидається. Викликач, якому однаково треба відповісти, може залишити порядок пошуку; це рішення викликача, тому тут воно не сховане.
- **Читаємо один раз.** `rerank_candidates` читає поля кожного кандидата один раз, на початку, і далі використовує лише цю копію. Якщо список викликача змінився, поки reranker працював, результат усе одно містить рівно те, що було перевірено.
- **Все або нічого.** Якщо будь-який запит падає, виклик кидає помилку і нічого не повертає. Наполовину переранжований список змішав би два порядки, нікому про це не сказавши.
- **Вимірювання.** Звіт показує місця кожного випадку до і після reranking-у («Ranking delta»), скільки випадків піднялося чи опустилося, а також медіанний і найповільніший час на випадок для пошуку і для reranking-у, з пристроєм. Ви запустите його раз із `RERANKER_DEVICE=cpu` і раз із `RERANKER_DEVICE=mps`. Перший запит на пристрої повільніший, бо модель прогрівається; медіана це ховає, найповільніший час показує.
- **Відомі обмеження.** Оцінюється лише текст Chunk, без назв і заголовків. Шістнадцять випадків — це мало, а злитий пошук уже ставить 15 із них першими, тож простір для покращення — один випадок. Адаптер повторює код читання HTTP з `LocalEmbeddingModel`, а не ділить його, щоб код M05-T09 лишився недоторканим; винести його можна в пізнішому завданні. Жоден пошук за замовчуванням поки не переранжовує: M05-T12 додає LLM-reranker, а M05-T13 порівнює обидва.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і передбачення

Ще до появи коду запишіть, що має бути правдою наприкінці уроку і чого ви чекаєте від reranker-а. Передбачення, записані зараз, до прогону, — це те, що перевіряє крок 9.

Створіть `docs/evidence/M05-T11-local-cross-encoder-reranker.md` за цим шаблоном, а підказки в передбаченнях замініть власними реченнями:

```markdown
# M05-T11 — Local cross-encoder reranker evidence

## Invariant

`rerank_candidates` returns exactly the Candidates it was given, each once,
in a new order: it never adds, drops, or reads a Chunk, so every Candidate
it returns passed the permission check of the search that found it, and no
restricted text ever reaches the reranker. Scores come only from the pinned
reranker revision, and every score is checked before it is used. One call
finishes within its deadline or raises `RerankerTimeout`. No input text
appears in an error, a log record, a server answer, or the report. No
default search reranks yet.

## Expected failing check

Before the change, `uv run pytest tests/test_reranking.py` fails to import
`knowledge_service.retrieval.reranking`.

## Predictions

- MRR of fused retrieval, before and after reranking: ...
- Cases that move down after reranking: ...
- Median reranking time per question on `cpu`: ...
- Median reranking time per question on `mps`: ...
- Whether reranking is worth its time on this dataset, and the one number that would change my mind: ...

## Design

## Verification

## Decision

## Reflection
```

Перевірка: файл існує, і кожне передбачення — повне речення, яке число з кроку 9 може спростувати.

### Крок 2 — Крок із кодом: запустіть reranker як окремий сервер

Цей крок додає другий сервер моделі, який оцінює пари «запитання — уривок».

**Простими словами:** Новий сервер один раз завантажує закріплений reranker, перевіривши відбитки його файлів. Він приймає одне запитання і список уривків, точно рахує кожну пару власним токенізатором моделі, відмовляє в усьому, що перевищує межі, і повертає одну оцінку на уривок у тому самому порядку, називаючи модель, ревізію і пристрій. Помилки і логи ніколи не повторюють тексти.

| Частина | Що робить |
|---|---|
| `MODEL_ID`, `REVISION`, `FILE_HASHES` | закріплена модель і SHA-256 її файлів ваг і токенізатора |
| `MAX_PASSAGES`, `MAX_PAIR_TOKENS`, `MAX_REQUEST_TOKENS` | 64 уривки, 8192 токени на пару, 16 384 на запит |
| `Scorer` | що серверу потрібно від моделі: порахувати пару, оцінити уривки; у тестах — фейк |
| `GET /v1/model` | походження, пристрій і межі, без тексту |
| `POST /v1/rerank` | перевіряє межі, оцінює по одному запиту за раз, відкидає оцінки, що не є одним скінченним float на уривок |
| `BgeRerankerScorer` | справжня модель через `transformers`, пачками по 8, без обрізання |
| `_check_limits` | відмовляє в порожньому запитанні чи уривку, непідрахованій парі, парі чи запиті понад межі, називаючи лише індекс |

Створіть `ml/bge_m3_server/reranker_server.py`:

```python
"""A local cross-encoder reranker server: the ML runtime side of the seam.

A cross-encoder reads a question and one passage together and returns one
number: how well the passage answers the question. It is slower than
comparing vectors, because every pair goes through the whole model, so it
only reorders the few Candidates retrieval already found.

This server runs `BAAI/bge-reranker-v2-m3`, a multilingual reranker built on
BGE-M3, on Python 3.12, next to the BGE-M3 server and with the same
libraries (ADR 0009). The service reaches it over HTTP through
`knowledge_service.retrieval.local_reranker`.

The server promises:

- **Pinned provenance.** It loads exactly one revision and checks the
  SHA-256 of the weight and tokenizer files before serving. Every answer
  names the model, revision, and device, so the client can refuse scores
  from anything else and a report can say where they were computed.
- **Exact limits, never truncation.** It counts each question and passage
  pair with the model's own tokenizer and refuses a request over 64
  passages, 8192 tokens per pair, or 16,384 tokens in total, naming only
  the index. A cut passage would be scored on its start alone.
- **Raw scores.** One finite number per passage, in order: the model's
  logit. Higher means more relevant; scores compare only within one
  question and one model, and are not probabilities.
- **No text in errors or logs.** Error bodies hold a reason and an index.
- **Loopback only.** It has no authentication, so it listens on 127.0.0.1.

Run it from the repository root with
`uv run --project ml/bge_m3_server python ml/bge_m3_server/reranker_server.py`.
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

logger = logging.getLogger("reranker_server")

MODEL_ID = "BAAI/bge-reranker-v2-m3"
REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
MAX_PASSAGES = 64
MAX_PAIR_TOKENS = 8192
MAX_REQUEST_TOKENS = 16_384
# Counting a very long text is slow, so one longer than this is refused before
# it is counted, whatever its token count; real passages are far shorter.
MAX_TEXT_CHARACTERS = MAX_PAIR_TOKENS * 16
SCORE_BATCH_SIZE = 8
# SHA-256 of the large files at REVISION, from the Hugging Face file listing.
FILE_HASHES = {
    "model.safetensors": "d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286",  # noqa: E501  # gitleaks:allow
    "tokenizer.json": "69564b696052886ed0ac63fa393e928384e0f8caada38c1f4864a9bfbf379c15",  # noqa: E501  # gitleaks:allow
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",  # noqa: E501  # gitleaks:allow
}
MODEL_FILES = [
    *FILE_HASHES,
    "config.json",
    "special_tokens_map.json",
    "tokenizer_config.json",
]


class Scorer(Protocol):
    """The model behind the server; a fake in tests, the reranker in production."""

    def count_pair_tokens(self, query: str, passage: str) -> int:
        """Count the pair exactly in the model's tokens, special tokens included."""
        ...

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """Return one raw relevance score per passage, in order."""
        ...


class ModelFilesChanged(RuntimeError):
    """A downloaded file does not match its pinned hash; nothing is served."""


class RerankRequest(BaseModel):
    """The request body: one question and the passages to score, nothing else."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: Annotated[str, Field(max_length=MAX_TEXT_CHARACTERS)]
    passages: list[Annotated[str, Field(max_length=MAX_TEXT_CHARACTERS)]] = Field(
        min_length=1, max_length=MAX_PASSAGES
    )


def create_app(scorer: Scorer, *, device: str) -> FastAPI:
    """Build the server around one scorer running on `device`."""
    app = FastAPI(title="reranker-server", docs_url=None, redoc_url=None)
    # One request at a time: the model uses every core for one batch already.
    busy = asyncio.Lock()

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, __: RequestValidationError) -> JSONResponse:
        # The default handler repeats the input, which may be document text.
        return _refusal(422, "invalid_request")

    @app.get("/v1/model")
    async def model() -> dict[str, object]:
        return {
            **_provenance(device),
            "max_passages": MAX_PASSAGES,
            "max_pair_tokens": MAX_PAIR_TOKENS,
            "max_request_tokens": MAX_REQUEST_TOKENS,
        }

    @app.post("/v1/rerank")
    async def rerank(body: RerankRequest) -> JSONResponse:
        # Counting takes time on long texts; off the event loop.
        checked = await asyncio.to_thread(
            _check_limits, scorer, body.query, body.passages
        )
        if isinstance(checked, JSONResponse):
            return checked
        failure: str | None = None
        scores: object = None
        async with busy:
            try:
                scores = await asyncio.to_thread(
                    scorer.score, body.query, body.passages
                )
            except Exception as error:
                # Its message or traceback could quote a text; keep the type.
                failure = type(error).__name__
        if failure is None and not _valid(scores, len(body.passages)):
            failure = "InvalidScores"
        if failure is not None:
            logger.warning("reranking failed error=%s", failure)
            return _refusal(500, "reranking_failed")
        logger.info("reranked passages=%d tokens=%d", len(body.passages), checked)
        return JSONResponse(
            {**_provenance(device), "input_tokens": checked, "scores": scores}
        )

    return app


class BgeRerankerScorer:
    """bge-reranker-v2-m3 through transformers: one logit per pair."""

    def __init__(self, folder: Path, device: str) -> None:
        # Imported here, so tests with a fake scorer never load PyTorch.
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch: Any = torch
        self._tokenizer: Any = AutoTokenizer.from_pretrained(folder)
        model: Any = AutoModelForSequenceClassification.from_pretrained(folder)
        self._model: Any = model.to(device).eval()
        self._device = device

    def count_pair_tokens(self, query: str, passage: str) -> int:
        return len(self._tokenizer(query, passage)["input_ids"])

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        scores: list[float] = []
        for start in range(0, len(passages), SCORE_BATCH_SIZE):
            batch = list(passages[start : start + SCORE_BATCH_SIZE])
            # The limits were checked, so nothing here is ever cut.
            features = self._tokenizer(
                [query] * len(batch),
                batch,
                padding=True,
                truncation=False,
                return_tensors="pt",
            )
            features = {
                name: value.to(self._device) for name, value in features.items()
            }
            with self._torch.inference_mode():
                logits = self._model(**features).logits.view(-1).float()
            scores += [float(value) for value in logits.tolist()]
        return scores


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


def _check_limits(
    scorer: Scorer, query: str, passages: list[str]
) -> int | JSONResponse:
    """Return the request's total tokens, or the refusal of the first bad input."""
    if not query.strip():
        return _refusal(422, "empty_query")
    total = 0
    for index, passage in enumerate(passages):
        if not passage.strip():
            return _refusal(422, "empty_passage", index)
        tokens = _count(scorer, query, passage)
        if tokens is None:
            return _refusal(422, "uncountable_text", index)
        if tokens > MAX_PAIR_TOKENS:
            return _refusal(413, "pair_too_long", index)
        total += tokens
        if total > MAX_REQUEST_TOKENS:
            return _refusal(413, "request_too_long", index)
    return total


def _valid(scores: object, expected: int) -> bool:
    # A NaN cannot be written as JSON, and a short list would be refused later.
    return (
        isinstance(scores, list)
        and len(scores) == expected
        and all(type(s) is float and math.isfinite(s) for s in scores)
    )


def _count(scorer: Scorer, query: str, passage: str) -> int | None:
    # A tokenizer error may quote the text, so only its absence is kept.
    try:
        tokens = scorer.count_pair_tokens(query, passage)
    except Exception:
        return None
    return tokens if type(tokens) is int and tokens > 0 else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _provenance(device: str) -> dict[str, object]:
    return {"model": MODEL_ID, "revision": REVISION, "device": device}


def _refusal(status: int, reason: str, index: int | None = None) -> JSONResponse:
    return JSONResponse({"error": reason, "index": index}, status_code=status)


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    device = os.environ.get("RERANKER_DEVICE", "cpu")
    port = int(os.environ.get("RERANKER_PORT", "8767"))
    scorer = BgeRerankerScorer(download_model(), device)
    uvicorn.run(create_app(scorer, device=device), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
```

Потім у `ml/bge_m3_server/pyproject.toml` назвіть `transformers` залежністю і розширте два описи:

Знайдіть у `ml/bge_m3_server/pyproject.toml`:

```toml
description = "Local BGE-M3 embedding server for the knowledge service (ML runtime)"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "sentence-transformers>=5",
    "torch>=2.6",
```

Замініть на:

```toml
description = "Local BGE-M3 embedding and reranker servers for the knowledge service (ML runtime)"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "sentence-transformers>=5",
    "torch>=2.6",
    "transformers>=5",
```

Знайдіть у `ml/bge_m3_server/pyproject.toml`:

```toml
markers = ["model: needs the pinned BGE-M3 files in the Hugging Face cache"]
```

Замініть на:

```toml
markers = ["model: needs the pinned model files in the Hugging Face cache"]
```

Оновіть lock-файл, починаючи з кореня репозиторію:

```bash
cd ml/bge_m3_server && uv lock && cd ../..
```

Перевірка: запустіть сервер із кореня репозиторію. Перший запуск завантажує близько 2,3 ГБ:

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/reranker_server.py
```

У другому терміналі:

```bash
curl -s 127.0.0.1:8767/v1/rerank -H 'Content-Type: application/json' -d '{"query": "Скільки днів відпустки?", "passages": ["Працівники мають 24 дні щорічної відпустки.", "VPN error 4031 means an expired certificate."]}'
```

Очікувано: `"device":"cpu"`, `"input_tokens":44` і дві оцінки, перша вище 0, а друга значно нижче (приблизно 3,7 і −11,0). Зупиніть сервер через Ctrl+C.

### Крок 3 — Крок із кодом: протестуйте сервер reranker-а

Цей крок тестує сервер без завантаження моделі, плюс два тести зі справжньою моделлю.

**Простими словами:** Фейковий оцінювач рахує один токен на символ і оцінює уривок за його довжиною або падає на вимогу. Тести перевіряють, що оцінки повертаються по порядку з назвою моделі, що кожна межа відмовляє, а не обрізає, що поганий запит чи збій моделі ніколи не повторює текст, і що зламані оцінки відкидаються. Два тести завантажують справжню модель: один перевіряє, що межа токенів застосунку ніколи не занизька, інший — що справжні оцінки віддають перевагу правильному уривку англійською й українською.

| Тест | Доводить |
|---|---|
| `test_scores_come_in_passage_order_with_the_pinned_provenance` | успіх: одна оцінка на уривок, по порядку, з моделлю, ревізією, пристроєм і кількістю токенів |
| `test_a_request_over_the_limits_is_refused_and_never_truncated` | п'ять видів поганого входу відкидаються з назвою лише індексу, і нічого не оцінюється |
| `test_an_invalid_request_is_refused_without_repeating_it` | неправильні тіла запиту отримують `invalid_request`, а не свій вміст назад |
| `test_a_scoring_failure_carries_no_text` | модель, що падає, нічого не видає ні у відповідь, ні в логи |
| `test_invalid_scores_are_refused` | короткий список, NaN, нескінченність, int, bool чи не-список відкидаються |
| `test_the_application_pair_bound_is_never_too_low` | справжній токенізатор ніколи не нараховує більше, ніж `LocalReranker` |
| `test_real_scores_prefer_the_answer_in_both_languages_and_ignore_batching` | справжня модель ставить відповідь вище за непов'язаний уривок обома мовами, а уривок отримує ту саму оцінку сам і в пачці |

Створіть `ml/bge_m3_server/test_reranker_server.py`:

```python
"""The reranker server: limits, provenance, raw scores, and quiet failures.

Most tests use a fake scorer, so they need neither PyTorch nor the model.
Tests marked `model` use the real reranker and skip when the pinned files
are not in the Hugging Face cache.
"""

import logging
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import reranker_server as server

PLANTED = "the salary of Olena is 9000"


class FakeScorer:
    """Counts one token per character of the pair; scores by passage length."""

    def __init__(self, *, fail: bool = False, scores: object = None) -> None:
        self.scored: list[tuple[str, list[str]]] = []
        self._fail = fail
        self._scores = scores

    def count_pair_tokens(self, query: str, passage: str) -> int:
        if passage == "uncountable":
            raise ValueError(f"cannot tokenize {passage}")
        return len(query) + len(passage)

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if self._fail:
            raise RuntimeError(f"failed on {query}")
        self.scored.append((query, list(passages)))
        if self._scores is not None:
            return self._scores  # type: ignore[return-value]
        return [float(len(passage)) for passage in passages]


def client(scorer: FakeScorer) -> TestClient:
    return TestClient(server.create_app(scorer, device="cpu"))


def test_scores_come_in_passage_order_with_the_pinned_provenance() -> None:
    scorer = FakeScorer()

    answer = client(scorer).post(
        "/v1/rerank", json={"query": "leave", "passages": ["abc", "a", "abcdef"]}
    )

    assert answer.status_code == 200
    assert answer.json() == {
        "model": "BAAI/bge-reranker-v2-m3",
        "revision": server.REVISION,
        "device": "cpu",
        "input_tokens": 3 * len("leave") + 3 + 1 + 6,
        "scores": [3.0, 1.0, 6.0],
    }
    assert scorer.scored == [("leave", ["abc", "a", "abcdef"])]


def test_the_model_endpoint_needs_no_text_and_states_the_limits() -> None:
    answer = client(FakeScorer()).get("/v1/model")

    assert answer.json() == {
        "model": "BAAI/bge-reranker-v2-m3",
        "revision": server.REVISION,
        "device": "cpu",
        "max_passages": 64,
        "max_pair_tokens": 8192,
        "max_request_tokens": 16_384,
    }


@pytest.mark.parametrize(
    ("body", "status", "reason", "index"),
    [
        ({"query": " ", "passages": ["a"]}, 422, "empty_query", None),
        ({"query": "q", "passages": ["a", " "]}, 422, "empty_passage", 1),
        ({"query": "q", "passages": ["uncountable"]}, 422, "uncountable_text", 0),
        ({"query": "q", "passages": ["a", "x" * 8192]}, 413, "pair_too_long", 1),
        (
            {"query": "q", "passages": ["x" * 8000, "x" * 8000, "x" * 400]},
            413,
            "request_too_long",
            2,
        ),
    ],
    ids=["empty query", "empty passage", "uncountable", "long pair", "long request"],
)
def test_a_request_over_the_limits_is_refused_and_never_truncated(
    body: dict[str, object], status: int, reason: str, index: int | None
) -> None:
    scorer = FakeScorer()

    answer = client(scorer).post("/v1/rerank", json=body)

    assert (answer.status_code, answer.json()) == (
        status,
        {"error": reason, "index": index},
    )
    assert scorer.scored == []


@pytest.mark.parametrize(
    "body",
    [
        {"query": PLANTED, "passages": []},
        {"query": PLANTED, "passages": ["a"] * 65},
        {"query": PLANTED, "passages": [PLANTED], "extra": 1},
        {"query": PLANTED, "passages": [7]},
        {"query": PLANTED, "passages": ["x" * (server.MAX_TEXT_CHARACTERS + 1)]},
    ],
    ids=["no passages", "too many", "unknown field", "not text", "too many chars"],
)
def test_an_invalid_request_is_refused_without_repeating_it(
    body: dict[str, object],
) -> None:
    answer = client(FakeScorer()).post("/v1/rerank", json=body)

    assert answer.status_code == 422
    assert answer.json() == {"error": "invalid_request", "index": None}


def test_a_scoring_failure_carries_no_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    answer = client(FakeScorer(fail=True)).post(
        "/v1/rerank", json={"query": PLANTED, "passages": [PLANTED]}
    )

    assert (answer.status_code, answer.json()) == (
        500,
        {"error": "reranking_failed", "index": None},
    )
    assert PLANTED not in answer.text
    assert PLANTED not in caplog.text


@pytest.mark.parametrize(
    "scores",
    [[1.0], [1.0, float("nan")], [1.0, float("inf")], [1.0, 2], [1.0, True], "x"],
    ids=["too few", "nan", "infinite", "int", "bool", "not a list"],
)
def test_invalid_scores_are_refused(scores: object) -> None:
    answer = client(FakeScorer(scores=scores)).post(
        "/v1/rerank", json={"query": "q", "passages": ["a", "b"]}
    )

    assert (answer.status_code, answer.json()) == (
        500,
        {"error": "reranking_failed", "index": None},
    )


def test_a_changed_or_missing_model_file_is_refused(tmp_path: Path) -> None:
    for name in server.FILE_HASHES:
        (tmp_path / name).write_bytes(b"not the model")

    with pytest.raises(server.ModelFilesChanged):
        server.check_files(tmp_path)


def _cached_model() -> Path | None:
    from huggingface_hub import snapshot_download

    try:
        folder = Path(
            snapshot_download(
                server.MODEL_ID,
                revision=server.REVISION,
                allow_patterns=server.MODEL_FILES,
                local_files_only=True,
            )
        )
    except Exception:
        return None
    return folder if (folder / "model.safetensors").is_file() else None


@pytest.fixture(scope="module")
def scorer() -> server.BgeRerankerScorer:
    folder = _cached_model()
    if folder is None:
        pytest.skip("the pinned reranker files are not cached")
    server.check_files(folder)
    return server.BgeRerankerScorer(folder, "cpu")


@pytest.mark.model
def test_the_application_pair_bound_is_never_too_low(
    scorer: server.BgeRerankerScorer,
) -> None:
    # The adapter bounds a pair by the sum of its two texts' bounds.
    import unicodedata

    def bound(text: str) -> int:
        return 3 + max(
            len(unicodedata.normalize("NFKC", text)),
            len(unicodedata.normalize("NFKD", text)),
        )

    texts = ["a", "Відпустка", "VPN-4031", "\ufb03 \u3131\u314f", "x" * 300]
    for query in texts:
        for passage in texts:
            exact = scorer.count_pair_tokens(query, passage)
            assert exact <= bound(query) + bound(passage), (query, passage)


@pytest.mark.model
def test_real_scores_prefer_the_answer_in_both_languages_and_ignore_batching(
    scorer: server.BgeRerankerScorer,
) -> None:
    passages = [
        "Employees get 24 days of paid annual leave per year.",
        "The VPN client must be updated before the first connection.",
        "Працівники мають 24 дні оплачуваної щорічної відпустки на рік.",
    ]

    english = scorer.score("How many days of annual leave do I get?", passages)
    ukrainian = scorer.score("Скільки днів відпустки мені належить?", passages)
    alone = scorer.score("How many days of annual leave do I get?", passages[:1])

    assert min(english[0], english[2]) > english[1]
    assert min(ukrainian[0], ukrainian[2]) > ukrainian[1]
    assert alone[0] == pytest.approx(english[0], abs=1e-3)
```

Перевірка: `make ml-check` показує `0 errors` і `51 passed`. Двом тестам зі справжньою моделлю потрібні файли, які сервер завантажив у кроці 2; без них ці тести пропускаються.

### Крок 4 — Крок із кодом: порт Reranker і `rerank_candidates`

Цей крок визначає, що обіцяє будь-який reranker, і пише функцію, яка застосовує його до кандидатів пошуку.

**Простими словами:** Порт перелічує, що мусить робити reranker: назвати себе, повідомити свої межі, порахувати токени пари і повернути одну оцінку на уривок або кинути типізовану помилку. Дві перевірки працюють до і після кожного виклику: вхід у межах і оцінки — рівно одне скінченне число на уривок. Функція переранжування спершу копіює і перевіряє кандидатів, ділить їх на запити, які reranker приймає, дає підрахунку і всім запитам один спільний дедлайн і повертає тих самих кандидатів, відсортованих за оцінкою, пам'ятаючи, де кожного поставив пошук.

| Частина | Що робить |
|---|---|
| `RerankerError` і підкласи | `InvalidRerankRequest` (помилка викликача), `RerankInputRejected` (понад межу), `RerankerUnavailable`, `RerankerTimeout`, `RerankerMalformedResponse`, `RerankerRequestRefused` |
| `RerankerIdentity`, `RerankLimits` | яка модель оцінювала; найбільше, що може нести один виклик |
| `Reranker` | `identity`, `limits`, `count_pair_tokens`, `score` |
| `check_rerank_inputs` | відмовляє в порожньому чи не-UTF-8 тексті, забагатьох уривках, непідрахованій парі, парі чи запиті понад межі; називає лише індекс |
| `checked_scores` | відкидає все, крім `expected` скінченних чисел; bool відкидається, хоча Python вважає його int |
| `RerankedCandidate`, `RerankResult` | кандидат із новим `rank`, своїм `score` і `retrieval_rank`; результат з ідентичністю reranker-а і кількістю запитів |
| `_snapshot`, `_read` | читають кожного кандидата один раз; того, кого не прочитати, відкидають без його тексту |
| `rerank_candidates` | перевіряє, ділить на пачки, тримає один дедлайн від самого початку, відкидає запізнілу відповідь, сортує за оцінкою, потім за місцем у пошуку |
| `_batches` | заповнює кожен запит по порядку, доки наступний уривок не порушить межу |

Створіть `src/knowledge_service/retrieval/reranking.py`:

```python
"""The Reranker port, and reranking retrieval's Candidates with it.

Retrieval finds Candidates cheaply: a vector distance or a word match per
Chunk. A reranker reads the Question and one Candidate's text together and
returns one relevance score, which is slower but sharper, so it only
reorders the few Candidates retrieval already found.

What every `Reranker` adapter promises:

- **One call is one request, all or nothing.** `score` returns exactly one
  score per passage, in passage order, or raises a `RerankerError`.
- **Inputs are checked before anything leaves the process.** An empty
  question or passage, a text that is not valid Unicode, a pair whose
  tokens cannot be counted, too many passages, a pair over the per-pair
  limit, or a request over the total limit raises `RerankInputRejected`
  naming the index, never the text.
- **Score semantics.** A score is the model's raw relevance score: any
  finite number, higher means more relevant. Scores compare only within
  one question and one `RerankerIdentity`; they are not probabilities and
  carry no fixed threshold.
- **Errors carry no text,** not even in a chained exception.

What `rerank_candidates` promises:

- **Checked first.** A bad question, timeout, or list of Candidates raises
  `InvalidRerankRequest`, and a Candidate the model cannot take raises
  `RerankInputRejected`, before the reranker is called. Each Candidate is
  read once, at the start, so changing the list later changes nothing.
- **Batch sizing.** Candidates go to the reranker in retrieval order, in as
  few requests as its limits allow: at most `max_passages` per request, and
  at most `max_request_tokens` per request by the reranker's own count.
- **One deadline.** Counting tokens and every request must finish within
  `timeout_seconds` of the start; each request gets only the time left.
  Past it, `RerankerTimeout` is raised and nothing is returned. A reranker
  that blocks the event loop cannot be interrupted, but its late answer is
  dropped all the same.
- **Only a new order.** The result holds exactly the Candidates it was
  given, each once, ordered by score, highest first, with retrieval rank
  breaking ties. It never adds, drops, or reads a Chunk, so every
  Candidate passed the permission check of the search that found it.
- **Provenance.** The result names the reranker identity behind its scores.
- **Bugs are not hidden.** An error from the reranker that is not a
  `RerankerError` propagates unchanged.
- **Quiet.** The fields holding Document text are kept out of repr.
"""

import asyncio
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES


class RerankerError(Exception):
    """Base error for reranking failures."""


class InvalidRerankRequest(RerankerError):
    """The question, timeout, or Candidates break the contract; nothing was sent."""


class RerankRejection(StrEnum):
    """Why a reranking request was refused before calling the model."""

    EMPTY_QUERY = "empty_query"
    EMPTY_PASSAGE = "empty_passage"
    UNENCODABLE_TEXT = "unencodable_text"
    UNCOUNTABLE_TEXT = "uncountable_text"
    TOO_MANY_PASSAGES = "too_many_passages"
    PAIR_TOO_LONG = "pair_too_long"
    REQUEST_TOO_LONG = "request_too_long"


class RerankInputRejected(RerankerError):
    """The request breaks the reranker's input limits; nothing was sent."""

    def __init__(self, reason: RerankRejection, *, index: int | None) -> None:
        self.reason = reason
        self.index = index
        where = "" if index is None else f" at passage {index}"
        super().__init__(f"rerank input rejected {reason.value}{where}")


class RerankerUnavailable(RerankerError):
    """The reranker cannot currently serve the request."""


class RerankerTimeout(RerankerError):
    """The reranker did not answer within the deadline."""


class RerankerMalformedResponse(RerankerError):
    """The reranker answered, but not with one finite score per passage."""


class RerankerRequestRefused(RerankerError):
    """The reranker refused the request or is not the configured model."""


@dataclass(frozen=True, slots=True)
class RerankerIdentity:
    """Which model produced a score; scores of different identities never mix."""

    provider: str
    model: str

    def __post_init__(self) -> None:
        if type(self.provider) is not str or type(self.model) is not str:
            raise InvalidRerankRequest("provider and model must be strings")
        if not self.provider.strip() or not self.model.strip():
            raise InvalidRerankRequest("provider and model must not be empty")


@dataclass(frozen=True, slots=True)
class RerankLimits:
    """The most one `score` call may carry, in model tokens."""

    max_passages: int
    max_pair_tokens: int
    max_request_tokens: int

    def __post_init__(self) -> None:
        for value in (self.max_passages, self.max_pair_tokens, self.max_request_tokens):
            if type(value) is not int or value < 1:
                raise InvalidRerankRequest("rerank limits must be positive integers")
        if self.max_pair_tokens > self.max_request_tokens:
            raise InvalidRerankRequest("one pair cannot be allowed more than a request")


class Reranker(Protocol):
    """A model that scores how well each passage answers one question."""

    @property
    def identity(self) -> RerankerIdentity:
        """The provider and the model, with its revision."""
        ...

    @property
    def limits(self) -> RerankLimits:
        """The most one call to `score` may carry."""
        ...

    def count_pair_tokens(self, query: str, passage: str) -> int:
        """Count the pair in this model's tokens, never fewer than the model does."""
        ...

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        """Return one raw score per passage, in order, or raise a `RerankerError`."""
        ...


@dataclass(frozen=True, slots=True)
class RerankedCandidate:
    """One eligible Chunk after reranking, and where retrieval had put it.

    `rank` is its 1-based position after reranking, `score` the reranker's
    raw score, and `retrieval_rank` its rank before. The fields holding
    Document text are kept out of repr.
    """

    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    score: float
    retrieval_rank: int
    external_id: str = field(repr=False)
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_anchor: str | None = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Given:
    """One Candidate's fields, read once when reranking starts."""

    rank: object
    chunk_id: object
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    external_id: str = field(repr=False)
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_anchor: str | None = field(repr=False)
    text: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class RerankResult:
    """The Candidates in their new order, and how they were scored."""

    identity: RerankerIdentity
    candidates: tuple[RerankedCandidate, ...]
    requests: int


def check_rerank_inputs(
    query: str,
    passages: Sequence[str],
    limits: RerankLimits,
    count_pair_tokens: Callable[[str, str], int],
) -> int:
    """Refuse a request that breaks `limits`; return its total token count.

    Adapters call this before sending anything, so a rejected request never
    reaches the model. Failures are raised outside any `except` block, so no
    chained exception can carry the text.
    """
    if isinstance(passages, str):
        raise TypeError("passages must be a sequence of strings, not one string")
    if not query.strip():
        raise RerankInputRejected(RerankRejection.EMPTY_QUERY, index=None)
    if not _encodable(query):
        raise RerankInputRejected(RerankRejection.UNENCODABLE_TEXT, index=None)
    if len(passages) > limits.max_passages:
        raise RerankInputRejected(RerankRejection.TOO_MANY_PASSAGES, index=None)
    total = 0
    for index, passage in enumerate(passages):
        tokens = _pair_tokens(query, passage, index, limits, count_pair_tokens)
        total += tokens
        if total > limits.max_request_tokens:
            raise RerankInputRejected(RerankRejection.REQUEST_TOO_LONG, index=index)
    return total


def checked_scores(scores: object, *, expected: int) -> tuple[float, ...]:
    """Return the model's scores, or raise if they are not `expected` finite numbers.

    `scores` is whatever the model sent, so nothing about its shape is
    trusted: bool is refused although it is an int, and so is any number
    that is not finite as a float.
    """
    if not isinstance(scores, list | tuple):
        raise RerankerMalformedResponse("the response is not a list of scores")
    items = cast(Sequence[object], scores)
    if len(items) != expected:
        raise RerankerMalformedResponse(
            f"expected {expected} scores, received {len(items)}"
        )
    checked: list[float] = []
    for index, value in enumerate(items):
        number = _number(value)
        if number is None:
            raise RerankerMalformedResponse(f"score {index} is not a finite number")
        checked.append(number)
    return tuple(checked)


async def rerank_candidates(
    reranker: Reranker,
    question: str,
    candidates: Sequence[Candidate],
    *,
    request_id: UUID,
    timeout_seconds: float,
) -> RerankResult:
    """Score every Candidate against the question and return them in the new order."""
    loop = asyncio.get_running_loop()
    given = _snapshot(question, candidates, timeout_seconds)
    deadline = loop.time() + timeout_seconds
    identity = reranker.identity
    texts = [cast(str, item.text) for item in given]
    batches = _batches(question, texts, reranker.limits, reranker.count_pair_tokens)
    if loop.time() > deadline:
        raise RerankerTimeout()
    scores: list[float] = []
    for start, end in batches:
        remaining = deadline - loop.time()
        batch: object = None
        if remaining > 0:
            try:
                async with asyncio.timeout(remaining):
                    batch = await reranker.score(
                        question,
                        texts[start:end],
                        request_id=request_id,
                        timeout_seconds=remaining,
                    )
            except TimeoutError:
                batch = None
        # Raised outside the `except`: nothing linked can carry the texts.
        # A reranker that blocked the loop answers late; its answer is dropped.
        if batch is None or loop.time() > deadline:
            raise RerankerTimeout()
        scores += checked_scores(batch, expected=end - start)
    # Retrieval ranks are 1, 2, 3, ..., so the position breaks ties.
    order = sorted(range(len(given)), key=lambda index: (-scores[index], index))
    return RerankResult(
        identity=identity,
        candidates=tuple(
            _reranked(rank, given[index], scores[index])
            for rank, index in enumerate(order, start=1)
        ),
        requests=len(batches),
    )


def _snapshot(
    question: str, candidates: Sequence[Candidate], timeout_seconds: float
) -> tuple[_Given, ...]:
    """Check the request and read every Candidate once; raise if any is wrong."""
    if type(question) is not str or not question.strip():
        raise InvalidRerankRequest("the question must be a non-empty string")
    if (
        type(timeout_seconds) not in (int, float)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise InvalidRerankRequest("timeout_seconds must be a positive number")
    if not isinstance(candidates, tuple | list):
        raise InvalidRerankRequest("the Candidates must be a tuple or a list")
    if len(candidates) > MAX_CANDIDATES:
        raise InvalidRerankRequest(f"at most {MAX_CANDIDATES} Candidates")
    given = tuple(_read(candidate) for candidate in tuple(candidates))
    seen: set[ChunkId] = set()
    for position, item in enumerate(given, start=1):
        if item is None:
            raise InvalidRerankRequest(
                f"an unreadable Candidate at position {position}"
            )
        if type(item.chunk_id) is not ChunkId:
            raise InvalidRerankRequest(f"no ChunkId at position {position}")
        if type(item.rank) is not int or item.rank != position:
            raise InvalidRerankRequest(f"a wrong rank at position {position}")
        if type(item.text) is not str:
            raise InvalidRerankRequest(f"no text at position {position}")
        if item.chunk_id in seen:
            raise InvalidRerankRequest(f"a Chunk listed twice, at position {position}")
        seen.add(item.chunk_id)
    return cast(tuple[_Given, ...], given)


def _read(candidate: object) -> _Given | None:
    # A property may raise, and its error may quote the text: only its
    # absence is kept, and the caller raises outside any `except`.
    try:
        item = cast(Candidate, candidate)
        return _Given(
            rank=item.rank,
            chunk_id=item.chunk_id,
            document_id=item.document_id,
            source_id=item.source_id,
            content_version=item.content_version,
            ordinal=item.ordinal,
            external_id=item.external_id,
            title=item.title,
            heading_path=item.heading_path,
            source_anchor=item.source_anchor,
            text=item.text,
        )
    except Exception:
        return None


def _batches(
    question: str,
    texts: Sequence[str],
    limits: RerankLimits,
    count_pair_tokens: Callable[[str, str], int],
) -> list[tuple[int, int]]:
    """Split the texts into requests within the limits, keeping their order."""
    if not texts:
        return []
    if not question.strip():
        raise RerankInputRejected(RerankRejection.EMPTY_QUERY, index=None)
    if not _encodable(question):
        raise RerankInputRejected(RerankRejection.UNENCODABLE_TEXT, index=None)
    batches: list[tuple[int, int]] = []
    start = 0
    total = 0
    for index, text in enumerate(texts):
        tokens = _pair_tokens(question, text, index, limits, count_pair_tokens)
        if index - start == limits.max_passages or (
            total + tokens > limits.max_request_tokens
        ):
            batches.append((start, index))
            start, total = index, 0
        total += tokens
    batches.append((start, len(texts)))
    return batches


def _pair_tokens(
    query: str,
    passage: str,
    index: int,
    limits: RerankLimits,
    count_pair_tokens: Callable[[str, str], int],
) -> int:
    if not passage.strip():
        raise RerankInputRejected(RerankRejection.EMPTY_PASSAGE, index=index)
    if not _encodable(passage):
        raise RerankInputRejected(RerankRejection.UNENCODABLE_TEXT, index=index)
    tokens = _count(query, passage, count_pair_tokens)
    if tokens is None:
        raise RerankInputRejected(RerankRejection.UNCOUNTABLE_TEXT, index=index)
    if tokens > limits.max_pair_tokens:
        raise RerankInputRejected(RerankRejection.PAIR_TOO_LONG, index=index)
    return tokens


def _count(
    query: str, passage: str, count_pair_tokens: Callable[[str, str], int]
) -> int | None:
    # A tokenizer's own error may quote the text, so it is dropped here.
    try:
        tokens = count_pair_tokens(query, passage)
    except Exception:
        return None
    return tokens if type(tokens) is int and tokens > 0 else None


def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _number(value: object) -> float | None:
    # bool is a subclass of int, and a string may look numeric; neither is.
    if type(value) is not int and type(value) is not float:
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _reranked(rank: int, given: _Given, score: float) -> RerankedCandidate:
    return RerankedCandidate(
        rank=rank,
        chunk_id=cast(ChunkId, given.chunk_id),
        document_id=given.document_id,
        source_id=given.source_id,
        content_version=given.content_version,
        ordinal=given.ordinal,
        score=score,
        retrieval_rank=cast(int, given.rank),
        external_id=given.external_id,
        title=given.title,
        heading_path=given.heading_path,
        source_anchor=given.source_anchor,
        text=cast(str, given.text),
    )
```

Перевірка: `make typecheck` показує `0 errors`.

### Крок 5 — Крок із кодом: локальний адаптер reranker-а

Цей крок дає застосунку говорити з сервером reranker-а через порт.

**Простими словами:** Адаптер перевіряє запит до надсилання, використовуючи підрахунок токенів, який ніколи не занизький. Він надсилає один запит, читає щонайбільше 64 КБ відповіді й відкидає стиснуту, і перевіряє, що відповідь називає закріплену модель і ревізію, перш ніж повірити хоч одній оцінці. Кожен збій стає однією з типізованих помилок порту, кинутою поза блоком `except`, щоб жодна пов'язана помилка не несла текстів, а один запис у лог містить лише кількості, час і пристрій.

| Частина | Що робить |
|---|---|
| `LocalRerankerSettings` | налаштування `LOCAL_RERANKER_*`: URL (порт 8767 за замовчуванням), модель, ревізія, тайм-аут, межі; поганий URL чи межа понад серверну падає на старті |
| `count_pair_tokens` | сума верхніх меж обох текстів |
| `score` | перевірити, надіслати, перевірити статус і походження, перевірити оцінки, записати в лог |
| `verify_server` | не надсилає тексту; повертає пристрій, тож прогін одразу падає, якщо сервер зупинено або ревізія не та |
| `_deadline` | відкидає тайм-аут, що не є додатним числом, з `InvalidRerankRequest` |
| `_send` | один запит під дедлайном, відповідь читається до `MAX_RESPONSE_BYTES`, стиснуті відповіді відкидаються непрочитаними; закритий клієнт — це `RerankerUnavailable` |
| `_check_status` | 408 чи 504 — тайм-аут, 5xx — недоступність, будь-який інший не-200 — відмова |
| `_device` | приймає лише малі літери, цифри, `:`, `_` і `-`, до 16 символів, бо назва потрапляє в лог і в Markdown-звіт |

Створіть `src/knowledge_service/retrieval/local_reranker.py`:

```python
"""Local bge-reranker-v2-m3 implementation of the Reranker port.

The model runs in a separate Python 3.12 process, the reranker server in
`ml/bge_m3_server/reranker_server.py` (ADR 0009). This adapter is the
application's side of that seam: one call to `score` is one HTTP request.
It adds what the port cannot know:

- **Pinned provenance.** The identity names the model and the exact weights
  revision, `BAAI/bge-reranker-v2-m3@<revision>`. Every answer must name the
  same model and revision; any other answer raises `RerankerRequestRefused`
  and no score is returned, because scores of two models do not compare.
- **A safe token bound without a tokenizer.** `count_pair_tokens` returns an
  upper bound, the same bound as `LocalEmbeddingModel.count_tokens` for each
  text: the model shares BGE-M3's tokenizer, and a pair adds no more
  special tokens than the two texts would alone. A request the server would
  refuse is refused here first, before any text leaves the process.
- **Retry classification.** A refused connection or a 5xx becomes
  `RerankerUnavailable`, a timeout or a 408 or 504 `RerankerTimeout`, and
  any other refusal `RerankerRequestRefused`.
- **A bounded answer.** The response is read in pieces and refused past
  `MAX_RESPONSE_BYTES`; a compressed answer is refused unread.
- **Safe logging.** One log record per call with counts, timing, device,
  and the outcome; never text. Errors are raised outside the `except`
  block, because an HTTP error holds the request, which holds every text.

The caller owns the `httpx.AsyncClient`. Create it with `trust_env=False`,
so a proxy set in the environment never receives the texts.
"""

import asyncio
import json
import logging
import math
import re
import time
import unicodedata
from collections.abc import Sequence
from typing import cast
from uuid import UUID

import httpx
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from knowledge_service.retrieval.reranking import (
    InvalidRerankRequest,
    RerankerError,
    RerankerIdentity,
    RerankerMalformedResponse,
    RerankerRequestRefused,
    RerankerTimeout,
    RerankerUnavailable,
    RerankInputRejected,
    RerankLimits,
    check_rerank_inputs,
    checked_scores,
)

logger = logging.getLogger(__name__)

PROVIDER = "local"
RERANK_PATH = "/v1/rerank"
MODEL_PATH = "/v1/model"
# The reranker weights this application was evaluated with (Hugging Face commit).
RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
# 64 scores as JSON take about 2 KB; far more means a fault.
MAX_RESPONSE_BYTES = 64 * 1024
# <s>, </s>, and the word-start marker the tokenizer adds to one text.
_ADDED_TOKENS = 3
# A device name is logged and printed in Markdown reports, so it stays plain.
_DEVICE = re.compile(r"[a-z0-9:_-]{1,16}")


class LocalRerankerSettings(BaseSettings):
    """Settings for the local reranker adapter, read from `LOCAL_RERANKER_*`.

    The limits cannot exceed the server's: 64 passages, 8192 tokens per
    pair, and 16,384 tokens per request.
    """

    model_config = SettingsConfigDict(env_prefix="LOCAL_RERANKER_")

    url: str = Field(default="http://127.0.0.1:8767", min_length=1)
    model: str = Field(default="BAAI/bge-reranker-v2-m3", min_length=1)
    revision: str = Field(default=RERANKER_REVISION, min_length=1)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    max_passages: int = Field(default=16, gt=0, le=64)
    max_pair_tokens: int = Field(default=8192, gt=0, le=8192)
    max_request_tokens: int = Field(default=16_384, gt=0, le=16_384)

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


class LocalReranker:
    """Translate Reranker calls to the local reranker server."""

    def __init__(
        self, client: httpx.AsyncClient, settings: LocalRerankerSettings
    ) -> None:
        self._client = client
        self._settings = settings
        self._identity = RerankerIdentity(
            provider=PROVIDER, model=f"{settings.model}@{settings.revision}"
        )
        self._limits = RerankLimits(
            max_passages=settings.max_passages,
            max_pair_tokens=settings.max_pair_tokens,
            max_request_tokens=settings.max_request_tokens,
        )

    @property
    def identity(self) -> RerankerIdentity:
        return self._identity

    @property
    def limits(self) -> RerankLimits:
        return self._limits

    def count_pair_tokens(self, query: str, passage: str) -> int:
        """Return an upper bound on the pair's tokens: each text's bound, added.

        A pair is `<s> query </s></s> passage </s>`: four special tokens and
        two word-start markers, the same six the two texts add alone.
        """
        return _upper_bound(query) + _upper_bound(passage)

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        deadline = self._deadline(timeout_seconds)
        try:
            check_rerank_inputs(query, passages, self._limits, self.count_pair_tokens)
        except RerankInputRejected as error:
            _log(request_id, self._identity, len(passages), 0, error)
            raise
        if not passages:
            return ()
        started_at = time.perf_counter()
        device: str | None = None
        try:
            status, content = await self._send(
                "POST",
                RERANK_PATH,
                deadline,
                body={"query": query, "passages": list(passages)},
                headers={"X-Request-ID": str(request_id)},
            )
            _check_status(status)
            body = self._check_provenance(_json_object(content))
            device = _device(body)
            scores = checked_scores(body.get("scores"), expected=len(passages))
        except RerankerError as error:
            _log(request_id, self._identity, len(passages), _since(started_at), error)
            raise
        _log(
            request_id,
            self._identity,
            len(passages),
            _since(started_at),
            None,
            device=device,
        )
        return scores

    async def verify_server(self, *, timeout_seconds: float) -> str:
        """Check, sending no text, that the server runs the configured model.

        Returns the device the server scores on, such as `cpu` or `mps`.
        Raises `RerankerUnavailable` or `RerankerTimeout` when the server
        cannot be reached, and `RerankerRequestRefused` when it serves other
        weights.
        """
        deadline = self._deadline(timeout_seconds)
        status, content = await self._send("GET", MODEL_PATH, deadline)
        _check_status(status)
        device = _device(self._check_provenance(_json_object(content)))
        if device is None:
            raise RerankerMalformedResponse("the reranker did not name its device")
        return device

    def _deadline(self, timeout_seconds: float) -> float:
        # bool is an int, and NaN compares false with everything; neither is.
        if (
            type(timeout_seconds) not in (int, float)
            or math.isnan(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise InvalidRerankRequest("timeout_seconds must be a positive number")
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
        encoded = False
        failure: RerankerError | None = None
        try:
            async with (
                asyncio.timeout(deadline),
                self._client.stream(
                    method,
                    self._settings.url + path,
                    json=body,
                    headers={"Accept-Encoding": "identity", **(headers or {})},
                    timeout=deadline,
                ) as response,
            ):
                status = response.status_code
                coding = response.headers.get("Content-Encoding", "identity")
                encoded = coding.strip().lower() != "identity"
                # An encoded body is never read, so nothing is ever unpacked.
                if not encoded:
                    async for piece in response.aiter_bytes():
                        content += piece
                        if len(content) > MAX_RESPONSE_BYTES:
                            break
        except TimeoutError, httpx.TimeoutException:
            failure = RerankerTimeout()
        except httpx.HTTPError, httpx.StreamError, RuntimeError:
            # RuntimeError: a client the caller already closed.
            failure = RerankerUnavailable()
        if len(content) > MAX_RESPONSE_BYTES:
            failure = RerankerMalformedResponse("the response is too large")
        if encoded and failure is None:
            failure = RerankerMalformedResponse("the response is encoded")
        if failure is not None:
            # Raised outside the except block: the HTTP error holds the request.
            raise failure
        return status, bytes(content)

    def _check_provenance(self, body: dict[str, object] | None) -> dict[str, object]:
        if body is None:
            raise RerankerMalformedResponse("the reranker response was not JSON")
        if (
            body.get("model") != self._settings.model
            or body.get("revision") != self._settings.revision
        ):
            # Scores from another model do not compare with this model's.
            raise RerankerRequestRefused(
                "the reranker is not the configured model or revision"
            )
        return body


def _upper_bound(text: str) -> int:
    # As in `LocalEmbeddingModel.count_tokens`: every token covers at least
    # one normalized character, and the tokenizer may leave either form.
    longest = max(
        len(unicodedata.normalize("NFKC", text)),
        len(unicodedata.normalize("NFKD", text)),
    )
    return longest + _ADDED_TOKENS


def _device(body: dict[str, object]) -> str | None:
    device = body.get("device")
    if type(device) is str and _DEVICE.fullmatch(device):
        return device
    return None


def _check_status(status: int) -> None:
    if status in (408, 504):
        raise RerankerTimeout()
    if status >= 500:
        raise RerankerUnavailable()
    if status != 200:
        raise RerankerRequestRefused(
            f"the reranker refused the request with status {status}"
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
    identity: RerankerIdentity,
    passages: int,
    latency_ms: int,
    error: RerankerError | None,
    *,
    device: str | None = None,
) -> None:
    """Record one call: identifiers, counts, timing, and the outcome. No text."""
    fields: dict[str, object] = {
        "request_id": str(request_id),
        "provider": identity.provider,
        "model": identity.model,
        "passages": passages,
        "latency_ms": latency_ms,
        "outcome": "succeeded" if error is None else type(error).__name__,
    }
    if device is not None:
        fields["device"] = device
    level = logging.INFO if error is None else logging.WARNING
    logger.log(level, "rerank request finished", extra={"rerank": fields})
```

Задокументуйте нове налаштування в `.env.example`:

У кінці `.env.example` додайте:

```bash

# The local reranker server (ml/bge_m3_server/reranker_server.py); the default
# is shown.
# LOCAL_RERANKER_URL=http://127.0.0.1:8767
```

Перевірка: `make typecheck` показує `0 errors`. Як і `LOCAL_EMBEDDING_URL`, це налаштування читається з оточення shell, а не з `.env`.

### Крок 6 — Крок із кодом: тестові двійники і юніт-тести

Цей крок додає фейковий reranker, заглушку сервера і тести для порту, адаптера і `rerank_candidates`.

**Простими словами:** Фейковий reranker оцінює уривок за кількістю слів, спільних із запитанням, тож тести можуть передбачити будь-який порядок. Заглушка сервера відповідає, як справжній сервер, усередині тестового процесу, і може зламатися будь-яким способом, яким ламається справжній. Контрактні тести запускають ті самі перевірки на фейку і на справжньому адаптері. Тести адаптера пробують кожну зламану відповідь і перевіряють, що кожна стає типізованою помилкою без тексту. Тести reranking-у перевіряють новий порядок, пачки, спільний дедлайн і, на багатьох випадкових входах, що результат завжди рівно ті кандидати, яких дали.

| Файл | Що містить |
|---|---|
| `tests/fakes.py` | `shared_words` і `FakeReranker`, який записує кожен виклик і час, що йому дали |
| `tests/reranker_stubs.py` | `StubRerankerServer` поверх `httpx.MockTransport` і `local_reranker` |
| `tests/test_reranker_contract.py` | ті самі п'ять обіцянок для фейка і локального адаптера |
| `tests/test_local_reranker.py` | форма запиту, ідентичність, відмова іншим ревізіям, чотирнадцять типізованих збоїв без тексту, запис у лог, `verify_server` і назви пристроїв, межа, погані тайм-аути, закритий клієнт, налаштування |
| `tests/test_reranking.py` | порядок, рівні оцінки, порожній вхід, property-тест на випадкових входах, пачки за токенами, пара понад межу, спільний дедлайн, погані тайм-аути і кандидати, reranker, що блокує loop, повільний підрахунок, список, змінений під час оцінювання, кандидат, якого не прочитати |

У `tests/fakes.py` змініть імпорти, потім додайте фейк у кінець:

Знайдіть у `tests/fakes.py`:

```python
from knowledge_service.model import ChatModelUnavailable

```

Замініть на:

```python
from knowledge_service.model import ChatModelUnavailable
from knowledge_service.retrieval.reranking import (
    RerankerError,
    RerankerIdentity,
    RerankerTimeout,
    RerankLimits,
    check_rerank_inputs,
    checked_scores,
)

```

У кінці `tests/fakes.py` додайте:

```python


def shared_words(query: str, passage: str) -> float:
    """Fake relevance: how many distinct lowercased words the two texts share."""
    words = set(re.findall(r"\w+", query.lower()))
    return float(len(words & set(re.findall(r"\w+", passage.lower()))))


class FakeReranker:
    """Deterministic Reranker over `shared_words`; records every call.

    `delay_seconds` makes each call that long, within its own timeout.
    `extra_scores` adds that many scores to every answer, to test a
    malformed one.
    """

    def __init__(
        self,
        *,
        limits: RerankLimits | None = None,
        error: RerankerError | None = None,
        delay_seconds: float = 0.0,
        extra_scores: int = 0,
    ) -> None:
        self._identity = RerankerIdentity(provider="fake", model="fake-reranker")
        self._limits = limits or RerankLimits(
            max_passages=16, max_pair_tokens=1_000, max_request_tokens=4_000
        )
        self._error = error
        self._delay_seconds = delay_seconds
        self._extra_scores = extra_scores
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[float] = []

    @property
    def identity(self) -> RerankerIdentity:
        return self._identity

    @property
    def limits(self) -> RerankLimits:
        return self._limits

    def count_pair_tokens(self, query: str, passage: str) -> int:
        return len(query.split()) + len(passage.split()) + 4

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        if math.isnan(timeout_seconds):
            raise ValueError("timeout_seconds must be a number")
        check_rerank_inputs(query, passages, self._limits, self.count_pair_tokens)
        if not passages:
            return ()
        self.calls.append(tuple(passages))
        self.timeouts.append(timeout_seconds)
        if self._delay_seconds:
            try:
                async with asyncio.timeout(timeout_seconds):
                    await asyncio.sleep(self._delay_seconds)
            except TimeoutError:
                raise RerankerTimeout() from None
        if self._error is not None:
            raise self._error
        scores = [shared_words(query, passage) for passage in passages]
        return checked_scores(
            scores + [0.0] * self._extra_scores, expected=len(passages)
        )
```

Створіть `tests/reranker_stubs.py`:

```python
"""A stand-in for the local reranker server, for deterministic tests.

`StubRerankerServer` answers through `httpx.MockTransport`, so the real
`LocalReranker` sends real HTTP requests that never leave the process.
"""

import asyncio
import json

import httpx

from fakes import shared_words
from knowledge_service.retrieval.local_reranker import (
    RERANKER_REVISION,
    LocalReranker,
    LocalRerankerSettings,
)

URL = "http://reranker.test"


class StubRerankerServer:
    """Answer like the reranker server; record every request."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes | None = None,
        model: str = "BAAI/bge-reranker-v2-m3",
        revision: str = RERANKER_REVISION,
        device: object = "cpu",
        scores: object = None,
        error: Exception | None = None,
        block: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._status = status
        self._body = body
        self._provenance = {"model": model, "revision": revision, "device": device}
        # Sent instead of the `shared_words` scores.
        self._scores = scores
        self._error = error
        self._block = block
        self._headers = headers or {}
        self.requests: list[httpx.Request] = []

    @property
    def sent(self) -> int:
        """How many requests carried texts to score."""
        return sum(1 for request in self.requests if request.method == "POST")

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._block:
            await asyncio.Event().wait()
        if self._error is not None:
            raise self._error
        if self._body is not None or self._status != 200:
            return httpx.Response(
                self._status, content=self._body or b"{}", headers=self._headers
            )
        if request.method == "GET":
            return httpx.Response(200, json=self._provenance)
        body = json.loads(request.content)
        query: str = body["query"]
        passages: list[str] = body["passages"]
        scores = (
            [shared_words(query, passage) for passage in passages]
            if self._scores is None
            else self._scores
        )
        return httpx.Response(
            200,
            json={**self._provenance, "input_tokens": 9, "scores": scores},
            headers=self._headers,
        )


def local_reranker(
    server: StubRerankerServer, settings: LocalRerankerSettings | None = None
) -> LocalReranker:
    """The real adapter, talking to `server` through an in-process transport."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(server.handle))
    return LocalReranker(client, settings or LocalRerankerSettings(url=URL))
```

Створіть `tests/test_reranker_contract.py`:

```python
"""Behavioural contract tests shared by every Reranker adapter."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import pytest

from fakes import FakeReranker
from knowledge_service.retrieval.reranking import (
    Reranker,
    RerankerMalformedResponse,
    RerankerTimeout,
    RerankInputRejected,
    RerankRejection,
)
from reranker_stubs import StubRerankerServer, local_reranker

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000033")
PLANTED = "the salary of Olena is 9000"


@dataclass(frozen=True)
class Adapter:
    """One adapter in the state a test needs, and how many calls it sent."""

    reranker: Reranker
    sent: Callable[[], int]


def fake(*, delay_seconds: float = 0.0, extra_scores: int = 0) -> Adapter:
    reranker = FakeReranker(delay_seconds=delay_seconds, extra_scores=extra_scores)
    return Adapter(reranker=reranker, sent=lambda: len(reranker.calls))


def local(server: StubRerankerServer) -> Adapter:
    return Adapter(reranker=local_reranker(server), sent=lambda: server.sent)


WORKING: dict[str, Callable[[], Adapter]] = {
    "fake": fake,
    "local": lambda: local(StubRerankerServer()),
}
SHORT_ANSWER: dict[str, Callable[[], Adapter]] = {
    "fake": lambda: fake(extra_scores=1),
    "local": lambda: local(StubRerankerServer(scores=[1.0])),
}
SLOW: dict[str, Callable[[], Adapter]] = {
    "fake": lambda: fake(delay_seconds=5.0),
    "local": lambda: local(StubRerankerServer(block=True)),
}


@pytest.mark.parametrize("name", WORKING)
async def test_one_score_per_passage_in_order_higher_for_the_answer(
    name: str,
) -> None:
    adapter = WORKING[name]()

    scores = await adapter.reranker.score(
        "annual leave days",
        ["VPN setup", "annual leave is 24 days", "leave a note"],
        request_id=REQUEST_ID,
        timeout_seconds=5.0,
    )

    assert len(scores) == 3
    assert all(type(score) is float for score in scores)
    assert scores[1] > scores[2] > scores[0]
    assert adapter.sent() == 1


@pytest.mark.parametrize("name", WORKING)
async def test_no_passages_score_nothing_and_send_nothing(name: str) -> None:
    adapter = WORKING[name]()

    scores = await adapter.reranker.score(
        "annual leave", [], request_id=REQUEST_ID, timeout_seconds=5.0
    )

    assert (scores, adapter.sent()) == ((), 0)


@pytest.mark.parametrize("name", WORKING)
@pytest.mark.parametrize(
    ("query", "passages", "reason", "index"),
    [
        (" ", ["a"], RerankRejection.EMPTY_QUERY, None),
        (PLANTED, ["a", " "], RerankRejection.EMPTY_PASSAGE, 1),
        (PLANTED, ["\ud800"], RerankRejection.UNENCODABLE_TEXT, 0),
        (PLANTED, ["a"] * 17, RerankRejection.TOO_MANY_PASSAGES, None),
    ],
    ids=["empty query", "empty passage", "unencodable", "too many"],
)
async def test_a_bad_request_is_refused_before_sending_and_names_no_text(
    name: str,
    query: str,
    passages: list[str],
    reason: RerankRejection,
    index: int | None,
) -> None:
    adapter = WORKING[name]()

    with pytest.raises(RerankInputRejected) as raised:
        await adapter.reranker.score(
            query, passages, request_id=REQUEST_ID, timeout_seconds=5.0
        )

    assert (raised.value.reason, raised.value.index) == (reason, index)
    assert PLANTED not in str(raised.value)
    assert adapter.sent() == 0


@pytest.mark.parametrize("name", SHORT_ANSWER)
async def test_a_wrong_number_of_scores_is_malformed(name: str) -> None:
    adapter = SHORT_ANSWER[name]()

    with pytest.raises(RerankerMalformedResponse):
        await adapter.reranker.score(
            "q", ["a", "b"], request_id=REQUEST_ID, timeout_seconds=5.0
        )


@pytest.mark.parametrize("name", SLOW)
async def test_the_timeout_is_honoured(name: str) -> None:
    adapter = SLOW[name]()

    with pytest.raises(RerankerTimeout) as raised:
        await adapter.reranker.score(
            PLANTED, [PLANTED], request_id=REQUEST_ID, timeout_seconds=0.05
        )

    assert PLANTED not in str(raised.value)
    assert raised.value.__context__ is None or PLANTED not in str(
        raised.value.__context__
    )
```

Створіть `tests/test_local_reranker.py`:

```python
"""The local reranker adapter: provenance, transport failures, and quiet logs."""

import gzip
import json
import logging
from typing import cast
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from knowledge_service.retrieval.local_reranker import (
    MAX_RESPONSE_BYTES,
    LocalReranker,
    LocalRerankerSettings,
)
from knowledge_service.retrieval.reranking import (
    InvalidRerankRequest,
    RerankerError,
    RerankerMalformedResponse,
    RerankerRequestRefused,
    RerankerTimeout,
    RerankerUnavailable,
    RerankInputRejected,
    RerankRejection,
)
from reranker_stubs import URL, StubRerankerServer, local_reranker

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000044")
PLANTED = "the salary of Olena is 9000"
PROVENANCE = (
    '"model": "BAAI/bge-reranker-v2-m3",'
    ' "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e", "device": "cpu"'
)
NO_SCORES = ("{" + PROVENANCE + "}").encode()
# Python's JSON parser reads NaN, so the adapter must refuse it itself.
NAN_SCORE = ("{" + PROVENANCE + ', "scores": [1.0, NaN]}').encode()
# 1 MB of zeros packs into about 1 KB; unpacked, it would pass the cap.
PACKED = gzip.compress(b"0" * (1024 * 1024))


async def score(server: StubRerankerServer) -> tuple[float, ...]:
    return await local_reranker(server).score(
        PLANTED, [PLANTED, "VPN setup"], request_id=REQUEST_ID, timeout_seconds=5.0
    )


async def test_the_request_carries_the_question_passages_and_request_id() -> None:
    server = StubRerankerServer()

    scores = await score(server)

    (request,) = server.requests
    assert request.url == httpx.URL(URL + "/v1/rerank")
    assert json.loads(request.content) == {
        "query": PLANTED,
        "passages": [PLANTED, "VPN setup"],
    }
    assert request.headers["X-Request-ID"] == str(REQUEST_ID)
    assert request.headers["Accept-Encoding"] == "identity"
    assert scores == (6.0, 0.0)


def test_the_identity_names_the_pinned_revision() -> None:
    reranker = local_reranker(StubRerankerServer())

    assert reranker.identity.provider == "local"
    assert reranker.identity.model == (
        "BAAI/bge-reranker-v2-m3@953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
    )


@pytest.mark.parametrize(
    "server",
    [
        StubRerankerServer(model="BAAI/bge-reranker-base"),
        StubRerankerServer(revision="main"),
    ],
    ids=["other model", "other revision"],
)
async def test_scores_from_another_model_are_refused(
    server: StubRerankerServer,
) -> None:
    with pytest.raises(RerankerRequestRefused):
        await score(server)


@pytest.mark.parametrize(
    ("server", "error"),
    [
        (StubRerankerServer(status=503), RerankerUnavailable),
        (StubRerankerServer(status=500), RerankerUnavailable),
        (StubRerankerServer(status=504), RerankerTimeout),
        (StubRerankerServer(status=413), RerankerRequestRefused),
        (StubRerankerServer(error=httpx.ConnectError("refused")), RerankerUnavailable),
        (StubRerankerServer(error=httpx.ReadTimeout("slow")), RerankerTimeout),
        (StubRerankerServer(body=b"not json"), RerankerMalformedResponse),
        (StubRerankerServer(body=b"[1.0]"), RerankerMalformedResponse),
        (StubRerankerServer(body=NAN_SCORE), RerankerMalformedResponse),
        (StubRerankerServer(scores=[1.0, True]), RerankerMalformedResponse),
        (StubRerankerServer(scores=[1.0, "2.0"]), RerankerMalformedResponse),
        (StubRerankerServer(body=NO_SCORES), RerankerMalformedResponse),
        (
            StubRerankerServer(body=b" " * (MAX_RESPONSE_BYTES + 1)),
            RerankerMalformedResponse,
        ),
        (
            StubRerankerServer(body=PACKED, headers={"Content-Encoding": "gzip"}),
            RerankerMalformedResponse,
        ),
    ],
    ids=[
        "503",
        "500",
        "504",
        "413",
        "connection refused",
        "read timeout",
        "not json",
        "not an object",
        "nan score",
        "bool score",
        "string score",
        "no scores",
        "too large",
        "compressed",
    ],
)
async def test_every_failure_is_typed_and_carries_no_text(
    server: StubRerankerServer,
    error: type[RerankerError],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    with pytest.raises(error) as raised:
        await score(server)

    assert PLANTED not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert PLANTED not in caplog.text


async def test_one_log_record_per_call_with_counts_and_device_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    await score(StubRerankerServer(device="mps"))

    (record,) = [r for r in caplog.records if r.name.endswith("local_reranker")]
    assert record.rerank == {  # type: ignore[attr-defined]
        "request_id": str(REQUEST_ID),
        "provider": "local",
        "model": "BAAI/bge-reranker-v2-m3@953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
        "passages": 2,
        "latency_ms": record.rerank["latency_ms"],  # type: ignore[attr-defined]
        "outcome": "succeeded",
        "device": "mps",
    }
    assert PLANTED not in caplog.text


async def test_verify_server_returns_the_device_and_sends_no_text() -> None:
    server = StubRerankerServer(device="mps")

    device = await local_reranker(server).verify_server(timeout_seconds=5.0)

    assert device == "mps"
    assert [request.method for request in server.requests] == ["GET"]


@pytest.mark.parametrize(
    "device",
    [None, "", "x" * 17, "gpu\u00e9", "cpu\n## |x|", "CPU", 3],
    ids=["none", "empty", "long", "accent", "markdown", "upper case", "int"],
)
async def test_a_server_that_names_no_plain_device_is_malformed(
    device: object,
) -> None:
    with pytest.raises(RerankerMalformedResponse):
        await local_reranker(StubRerankerServer(device=device)).verify_server(
            timeout_seconds=5.0
        )


async def test_a_pair_over_the_limit_is_refused_before_sending() -> None:
    server = StubRerankerServer()
    reranker = local_reranker(
        server, LocalRerankerSettings(url=URL, max_pair_tokens=20)
    )

    with pytest.raises(RerankInputRejected) as raised:
        await reranker.score(
            "q", ["x" * 20], request_id=REQUEST_ID, timeout_seconds=5.0
        )

    assert (raised.value.reason, raised.value.index) == (
        RerankRejection.PAIR_TOO_LONG,
        0,
    )
    assert server.sent == 0


def test_the_pair_bound_adds_both_texts_bounds() -> None:
    reranker = local_reranker(StubRerankerServer())

    # NFKD splits the ligature into three letters; each text adds three.
    assert reranker.count_pair_tokens("ab", "ﬃ") == (2 + 3) + (3 + 3)


@pytest.mark.parametrize("url", ["", "ftp://x", "not a url", "http://"])
def test_a_bad_url_fails_at_start_up(url: str) -> None:
    with pytest.raises(ValidationError):
        LocalRerankerSettings(url=url)


@pytest.mark.parametrize(
    "timeout_seconds",
    [float("nan"), 0.0, -1.0, "5", True],
    ids=["nan", "zero", "negative", "string", "bool"],
)
async def test_a_bad_timeout_is_a_typed_refusal(timeout_seconds: object) -> None:
    server = StubRerankerServer()

    with pytest.raises(InvalidRerankRequest):
        await local_reranker(server).score(
            "q",
            ["a"],
            request_id=REQUEST_ID,
            timeout_seconds=cast(float, timeout_seconds),
        )

    assert server.sent == 0


async def test_a_closed_client_is_unavailable_and_quiet() -> None:
    server = StubRerankerServer()
    client = httpx.AsyncClient(transport=httpx.MockTransport(server.handle))
    await client.aclose()
    reranker = LocalReranker(client, LocalRerankerSettings(url=URL))

    with pytest.raises(RerankerUnavailable) as raised:
        await reranker.score(
            PLANTED, [PLANTED], request_id=REQUEST_ID, timeout_seconds=5.0
        )

    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    ("name", "value"),
    [("max_passages", 65), ("max_pair_tokens", 8193), ("max_request_tokens", 16_385)],
)
def test_limits_above_the_server_s_fail_at_start_up(name: str, value: int) -> None:
    with pytest.raises(ValidationError):
        LocalRerankerSettings.model_validate({"url": URL, name: value})
```

Створіть `tests/test_reranking.py`:

```python
"""Reranking Candidates: a new order only, batches within limits, one deadline."""

import math
import time
from collections.abc import Sequence
from typing import cast
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fakes import FakeReranker
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.fusion import FusedCandidate
from knowledge_service.retrieval.reranking import (
    InvalidRerankRequest,
    RerankerIdentity,
    RerankerMalformedResponse,
    RerankerTimeout,
    RerankInputRejected,
    RerankLimits,
    RerankRejection,
    RerankResult,
    rerank_candidates,
)

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000055")
PLANTED = "the salary of Olena is 9000"


def candidate(rank: int, text: str, *, chunk: int | None = None) -> FusedCandidate:
    return FusedCandidate(
        rank=rank,
        chunk_id=ChunkId(UUID(int=chunk if chunk is not None else rank)),
        document_id=DocumentId(UUID(int=1000 + rank)),
        source_id=SourceId(UUID(int=7)),
        content_version=ContentVersion(1),
        ordinal=rank,
        score=1 / (60 + rank),
        contributions=(),
        external_id=f"doc-{rank}.md",
        title="Policy",
        heading_path=None,
        source_anchor=None,
        text=text,
    )


def ranked(*texts: str) -> list[FusedCandidate]:
    return [candidate(rank, text) for rank, text in enumerate(texts, start=1)]


async def rerank(
    reranker: FakeReranker,
    candidates: Sequence[Candidate],
    *,
    question: str = "annual leave days",
    timeout_seconds: float = 5.0,
) -> RerankResult:
    return await rerank_candidates(
        reranker,
        question,
        candidates,
        request_id=REQUEST_ID,
        timeout_seconds=timeout_seconds,
    )


async def test_the_best_scored_candidate_comes_first_and_keeps_its_old_rank() -> None:
    result = await rerank(
        FakeReranker(), ranked("VPN setup", "leave a note", "annual leave is 24 days")
    )

    assert [c.text for c in result.candidates] == [
        "annual leave is 24 days",
        "leave a note",
        "VPN setup",
    ]
    assert [c.rank for c in result.candidates] == [1, 2, 3]
    assert [c.retrieval_rank for c in result.candidates] == [3, 2, 1]
    assert [c.score for c in result.candidates] == [3.0, 1.0, 0.0]
    assert result.identity == RerankerIdentity(provider="fake", model="fake-reranker")
    assert result.requests == 1


async def test_equal_scores_keep_the_retrieval_order() -> None:
    result = await rerank(FakeReranker(), ranked("a", "b", "c"))

    assert [c.retrieval_rank for c in result.candidates] == [1, 2, 3]


async def test_no_candidates_send_nothing() -> None:
    reranker = FakeReranker()

    result = await rerank(reranker, [])

    assert (result.candidates, result.requests, reranker.calls) == ((), 0, [])


@settings(max_examples=60, deadline=None)
@given(
    words=st.lists(
        st.lists(
            st.sampled_from(["annual", "leave", "days", "vpn", "note"]), max_size=4
        ),
        max_size=40,
    ),
    max_passages=st.integers(min_value=1, max_value=8),
)
async def test_the_result_is_always_the_same_candidates_in_score_order(
    words: list[list[str]], max_passages: int
) -> None:
    texts = [" ".join(["x", *chosen]) for chosen in words]
    reranker = FakeReranker(
        limits=RerankLimits(
            max_passages=max_passages, max_pair_tokens=50, max_request_tokens=200
        )
    )

    result = await rerank(reranker, ranked(*texts))

    out = result.candidates
    assert sorted(c.retrieval_rank for c in out) == list(range(1, len(texts) + 1))
    assert len({c.chunk_id for c in out}) == len(texts)
    assert [c.rank for c in out] == list(range(1, len(texts) + 1))
    keys = [(-c.score, c.retrieval_rank) for c in out]
    assert keys == sorted(keys)
    # Every batch respects the limits, and together they keep the order.
    assert all(len(call) <= max_passages for call in reranker.calls)
    assert [text for call in reranker.calls for text in call] == texts


async def test_batches_stay_within_the_token_limit() -> None:
    # Each pair counts 3 question words + 2 passage words + 4 = 9 tokens.
    reranker = FakeReranker(
        limits=RerankLimits(max_passages=10, max_pair_tokens=20, max_request_tokens=20)
    )

    result = await rerank(reranker, ranked(*["leave days"] * 5))

    assert [len(call) for call in reranker.calls] == [2, 2, 1]
    assert result.requests == 3


async def test_a_pair_over_the_limit_is_refused_before_any_request() -> None:
    reranker = FakeReranker(
        limits=RerankLimits(max_passages=10, max_pair_tokens=10, max_request_tokens=20)
    )

    with pytest.raises(RerankInputRejected) as raised:
        await rerank(reranker, ranked("leave", " ".join([PLANTED] * 3)))

    assert (raised.value.reason, raised.value.index) == (
        RerankRejection.PAIR_TOO_LONG,
        1,
    )
    assert PLANTED not in str(raised.value)
    assert reranker.calls == []


async def test_one_deadline_covers_every_request() -> None:
    # Four requests of 0.3 s each cannot finish within 0.5 s.
    reranker = FakeReranker(
        limits=RerankLimits(max_passages=1, max_pair_tokens=50, max_request_tokens=50),
        delay_seconds=0.3,
    )

    with pytest.raises(RerankerTimeout):
        await rerank(reranker, ranked("a", "b", "c", "d"), timeout_seconds=0.5)

    # The second request was given only the time left, not a fresh 0.5 s.
    assert len(reranker.calls) == 2
    assert reranker.timeouts[0] == pytest.approx(0.5, abs=0.05)
    assert reranker.timeouts[1] < 0.25


async def test_a_wrong_number_of_scores_is_malformed() -> None:
    with pytest.raises(RerankerMalformedResponse):
        await rerank(FakeReranker(extra_scores=1), ranked("a", "b"))


@pytest.mark.parametrize(
    "timeout_seconds",
    [0, -1.0, math.nan, math.inf, True],
    ids=["zero", "negative", "nan", "infinite", "bool"],
)
async def test_a_bad_timeout_is_refused_before_any_request(
    timeout_seconds: float,
) -> None:
    reranker = FakeReranker()

    with pytest.raises(InvalidRerankRequest):
        await rerank(reranker, ranked("a"), timeout_seconds=timeout_seconds)

    assert reranker.calls == []


@pytest.mark.parametrize(
    "candidates",
    [
        [candidate(2, "a")],
        [candidate(1, "a", chunk=9), candidate(2, "b", chunk=9)],
        [None],
        ranked(*["a"] * 101),
        "not a list",
    ],
    ids=["wrong rank", "chunk twice", "not a candidate", "too many", "a string"],
)
async def test_bad_candidates_are_refused_before_any_request(
    candidates: object,
) -> None:
    reranker = FakeReranker()

    with pytest.raises(InvalidRerankRequest):
        await rerank(reranker, cast(Sequence[Candidate], candidates))

    assert reranker.calls == []


@pytest.mark.parametrize("question", ["", "   "], ids=["empty", "blank"])
async def test_an_empty_question_is_refused(question: str) -> None:
    with pytest.raises(InvalidRerankRequest):
        await rerank(FakeReranker(), ranked("a"), question=question)


async def test_the_text_is_kept_out_of_the_repr() -> None:
    result = await rerank(FakeReranker(), ranked(PLANTED), question=PLANTED)

    assert PLANTED not in repr(result)


class LoopBlockingReranker(FakeReranker):
    """Blocks the whole event loop while scoring, so no timeout can fire."""

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        # On purpose: this reranker misbehaves by blocking the loop.
        time.sleep(0.3)  # noqa: ASYNC251
        return await super().score(
            query, passages, request_id=request_id, timeout_seconds=timeout_seconds
        )


class SlowCountingReranker(FakeReranker):
    """Takes 0.1 s to count each pair's tokens."""

    def count_pair_tokens(self, query: str, passage: str) -> int:
        time.sleep(0.1)
        return super().count_pair_tokens(query, passage)


class ListChangingReranker(FakeReranker):
    """Changes the caller's list of Candidates while it scores them."""

    def __init__(self, given: list[FusedCandidate]) -> None:
        super().__init__()
        self._given = given

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        self._given[0] = candidate(1, "intruder annual leave days", chunk=999)
        self._given.pop()
        return await super().score(
            query, passages, request_id=request_id, timeout_seconds=timeout_seconds
        )


class TextRaises:
    """A Candidate whose text cannot be read; the error repeats a secret."""

    rank = 1
    chunk_id = ChunkId(UUID(int=1))

    @property
    def text(self) -> str:
        raise ValueError(PLANTED)


async def test_a_late_answer_is_dropped_even_when_the_reranker_blocks_the_loop() -> (
    None
):
    with pytest.raises(RerankerTimeout):
        await rerank(LoopBlockingReranker(), ranked("a"), timeout_seconds=0.1)


async def test_slow_token_counting_counts_against_the_deadline() -> None:
    reranker = SlowCountingReranker()

    with pytest.raises(RerankerTimeout):
        await rerank(reranker, ranked("a", "b", "c"), timeout_seconds=0.2)

    assert reranker.calls == []


async def test_the_result_holds_the_candidates_given_even_if_the_list_changes() -> None:
    given = ranked("annual leave", "leave", "VPN setup")
    expected = [c.chunk_id for c in given]

    result = await rerank(ListChangingReranker(given), given)

    assert sorted(c.chunk_id.value for c in result.candidates) == sorted(
        chunk_id.value for chunk_id in expected
    )
    assert all("intruder" not in c.text for c in result.candidates)


async def test_a_candidate_that_cannot_be_read_is_refused_without_its_text() -> None:
    reranker = FakeReranker()

    with pytest.raises(InvalidRerankRequest) as raised:
        await rerank(reranker, cast(Sequence[Candidate], [TextRaises()]))

    assert PLANTED not in str(raised.value)
    assert raised.value.__context__ is None
    assert reranker.calls == []
```

Перевірка:

```bash
uv run pytest tests/test_reranker_contract.py tests/test_local_reranker.py tests/test_reranking.py -q
```

Очікувано: `83 passed`.

### Крок 7 — Крок із кодом: порівняйте пошук до і після reranking-у

Цей крок проганяє кожен seed-випадок злитим пошуком, переранжовує його і звітує про зміни й час.

**Простими словами:** Порівняння, як і раніше, завантажує і вбудовує корпус, а потім для кожного запитання запускає dense-пошук і пошук за словами разом, зберігає цей список і надсилає в reranker рівно цих кандидатів. Воно записує, де кожен релевантний Chunk стояв до і після, і скільки тривав кожен етап. Звіт показує обидва прогони поруч, потім рух кожного випадку, а наостанок час на пристрої reranker-а, бо лише ця частина змінюється від прогону до прогону. Скрипт отримує опцію `--rerank`, яка перевіряє обидва сервери до будь-якого запису.

| Зміна | Що робить |
|---|---|
| `DatasetReport.reranker` | називає reranker у звіті й позначає прогін як «reranked» |
| `RerankComparison` | обидва звіти, пристрій і кількість запитів до reranker-а |
| `compare_reranking` | завантажити, вбудувати, потім для кожного випадку: злитий пошук, потім `rerank_candidates` лише на його кандидатах |
| `_search_case`, `_outcome` | `_run_case`, розділений на дві частини, щоб порівняння могло переранжувати між пошуком і підрахунком; наявні прогони поводяться так само |
| `_rerank_case` | збій reranker-а стає `CaseFailed` з назвою лише випадку і типу помилки; так само reranker, що посеред прогону називає іншу модель |
| `render_rerank_comparison` | таблиця поруч, «Ranking delta», рухи вгору і вниз, потім «Latency» |
| `--rerank` | використовує обидва локальні сервери, спершу перевіряє їх, пише `bilingual-seed-rerank.md` |

У `src/knowledge_service/retrieval/evaluation_dataset.py` зробіть ці зміни по порядку:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
  prototype; nothing in production uses it.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
```

Замініть на:

```python
  prototype; nothing in production uses it.
- **Compared with reranking.** `compare_reranking` embeds every Chunk like
  `compare_branches`, runs every case with both branches fused, then
  reranks each case's fused Candidates with a Reranker (see
  `retrieval.reranking`). Only Candidates that passed the permission check
  reach the reranker. `render_rerank_comparison` reports the ranking delta
  per case and the reranking time on the reranker's device.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
from collections.abc import Mapping
```

Замініть на:

```python
from collections.abc import Mapping, Sequence
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
from knowledge_service.persistence.schema import embedding_spaces, sources
from knowledge_service.retrieval.fusion import DEFAULT_RRF_K, RetrievalBranch
```

Замініть на:

```python
from knowledge_service.persistence.schema import embedding_spaces, sources
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.fusion import DEFAULT_RRF_K, RetrievalBranch
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    reciprocal_rank,
)
```

Замініть на:

```python
    reciprocal_rank,
)
from knowledge_service.retrieval.reranking import (
    Reranker,
    RerankerIdentity,
    RerankResult,
    rerank_candidates,
)
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
EMBEDDING_TIMEOUT_SECONDS = 60.0

```

Замініть на:

```python
EMBEDDING_TIMEOUT_SECONDS = 60.0
RERANK_TIMEOUT_SECONDS = 60.0

```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    sparse: SparseModelIdentity | None = None

```

Замініть на:

```python
    sparse: SparseModelIdentity | None = None
    # The reranker that reordered each case's Candidates; None when none did.
    reranker: RerankerIdentity | None = None

```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    index_seconds: float

```

Замініть на:

```python
    index_seconds: float


@dataclass(frozen=True, slots=True)
class RerankComparison:
    """Fused retrieval before and after reranking, and where the scores ran.

    `retrieved` and `reranked` hold the same cases; a reranked outcome's
    `elapsed_seconds` is the reranking time alone. `device` is where the
    reranker scored, such as `cpu` or `mps`, and `requests` counts the
    requests sent to it over all cases.
    """

    retrieved: DatasetReport
    reranked: DatasetReport
    device: str
    requests: int

```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python

def score_slices(report: DatasetReport) -> tuple[SliceScore, ...]:
```

Замініть на:

```python

async def compare_reranking(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: EmbeddingModel,
    reranker: Reranker,
    device: str,
    limit: int = DEFAULT_LIMIT,
) -> RerankComparison:
    """Ingest and embed the corpus once, run every case fused, then rerank it.

    Each case runs dense and lexical search fused, as `compare_branches`
    does, and sends that case's fused Candidates, and nothing else, to
    `reranker`. Raises like `compare_branches`; `DatasetRunRefused` also
    when `reranker` has no valid identity, and `CaseFailed` when reranking
    a case fails, naming the case and the error type only.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    identity = _identity(model)
    reranker_identity = _reranker_identity(reranker)
    await verify_corpus(dataset, corpus)
    await _ingest(runtime, dataset, corpus, observed_at, embedding=identity)
    await _embed_corpus(runtime, model, observed_at)
    searches: Mapping[RetrievalBranch, BranchSearch] = {
        RetrievalBranch.DENSE: dense_branch(runtime, model),
        RetrievalBranch.LEXICAL: lexical_branch(runtime),
    }
    retrieved: list[CaseOutcome] = []
    reranked: list[CaseOutcome] = []
    requests = 0
    for case in dataset.cases:
        result, elapsed = await _search_case(case, searches, limit)
        retrieved.append(_outcome(case, result.candidates, result.degraded, elapsed))
        started = time.perf_counter()
        ordered = await _rerank_case(case, reranker, result, reranker_identity)
        took = time.perf_counter() - started
        requests += ordered.requests
        reranked.append(_outcome(case, ordered.candidates, result.degraded, took))
    branches = tuple(branch for branch in RetrievalBranch if branch in searches)

    def report(
        outcomes: list[CaseOutcome], by: RerankerIdentity | None
    ) -> DatasetReport:
        return DatasetReport(
            name=dataset.name,
            version=dataset.version,
            limit=limit,
            branches=branches,
            outcomes=tuple(outcomes),
            embedding=identity,
            reranker=by,
        )

    return RerankComparison(
        retrieved=report(retrieved, None),
        reranked=report(reranked, reranker_identity),
        device=device,
        requests=requests,
    )


def score_slices(report: DatasetReport) -> tuple[SliceScore, ...]:
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python

def render_report(report: DatasetReport) -> str:
```

Замініть на:

```python

def render_rerank_comparison(comparison: RerankComparison) -> str:
    """Return the side-by-side report, then each case's move, then timing.

    Everything above "## Latency" depends only on the data and the models,
    so two runs on one device can be compared with `diff`; the latency
    section changes from run to run.
    """
    before, after = comparison.retrieved, comparison.reranked
    moves = Counter[str]()
    lines = [
        "",
        "## Ranking delta",
        "",
        "Where each case's relevant Chunks ranked before and after reranking.",
        "",
        "| Case | Direction | Class | Before | After | Move |",
        "|---|---|---|---|---|---|",
    ]
    for old, new in zip(before.outcomes, after.outcomes, strict=True):
        change = reciprocal_rank(new.ranks) - reciprocal_rank(old.ranks)
        move = "up" if change > 0 else "down" if change < 0 else "same"
        moves[move] += 1
        lines.append(
            f"| {old.case_id} | {old.direction} | {old.query_class}"
            f" | {_ranks(old.ranks)} | {_ranks(new.ranks)} | {move} |"
        )
    lines += [
        "",
        f"- Moved up: {moves['up']}; moved down: {moves['down']};"
        f" unchanged: {moves['same']}.",
        "",
        "## Latency",
        "",
        "Wall time per case. It changes from run to run.",
        "",
        f"- Reranker device: {comparison.device}.",
        f"- Requests to the reranker: {comparison.requests}"
        f" for {len(after.outcomes)} cases.",
        "",
        "| Step | Median ms | Slowest ms |",
        "|---|---:|---:|",
    ]
    for name, report in (("retrieval", before), ("reranking", after)):
        times = [outcome.elapsed_seconds * 1000 for outcome in report.outcomes]
        median = statistics.median(times) if times else 0.0
        slowest = max(times, default=0.0)
        lines.append(f"| {name} | {median:.1f} | {slowest:.1f} |")
    return render_comparison((before, after)) + "\n".join(lines) + "\n"


def render_report(report: DatasetReport) -> str:
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
        ranks = ", ".join(
            "missed" if rank is None else str(rank) for rank in outcome.ranks
        )
        degraded = "yes" if outcome.degraded else "no"
        lines.append(
            f"| {outcome.case_id} | {outcome.direction} | {outcome.query_class}"
            f" | {ranks} | {degraded} |"
```

Замініть на:

```python
        degraded = "yes" if outcome.degraded else "no"
        lines.append(
            f"| {outcome.case_id} | {outcome.direction} | {outcome.query_class}"
            f" | {_ranks(outcome.ranks)} | {degraded} |"
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    return lines


def _label(report: DatasetReport) -> str:
    return " + ".join(report.branches)
```

Замініть на:

```python
    reranker = next((r.reranker for r in reports if r.reranker is not None), None)
    if reranker is not None:
        lines.append(f"- Reranker: {reranker.provider} {reranker.model}.")
    return lines


def _label(report: DatasetReport) -> str:
    label = " + ".join(report.branches)
    return label if report.reranker is None else f"{label}, reranked"


def _ranks(ranks: tuple[int | None, ...]) -> str:
    return ", ".join("missed" if rank is None else str(rank) for rank in ranks)
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
        raise DatasetRunRefused("the model must have an EmbeddingModelIdentity")
    return identity
```

Замініть на:

```python
        raise DatasetRunRefused("the model must have an EmbeddingModelIdentity")
    return identity


def _reranker_identity(reranker: Reranker) -> RerankerIdentity:
    # Read once here, so a reranker that cannot name itself is refused before
    # anything is written.
    try:
        identity = reranker.identity
    except Exception:
        identity = None
    if type(identity) is not RerankerIdentity:
        raise DatasetRunRefused("the reranker must have a RerankerIdentity")
    return identity
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
) -> CaseOutcome:
    result: HybridRetrieval | None = None
```

Замініть на:

```python
) -> CaseOutcome:
    result, elapsed = await _search_case(case, searches, limit)
    return _outcome(case, result.candidates, result.degraded, elapsed)


async def _search_case(
    case: EvaluationCase,
    searches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
) -> tuple[HybridRetrieval, float]:
    """Run one case's search; return the result and its wall time in seconds."""
    result: HybridRetrieval | None = None
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    ranks: dict[tuple[str, str | None], int] = {}
    for candidate in result.candidates:
        if candidate.source_id != SOURCE_IDS[Folder.PUBLIC]:
            raise IneligibleChunkRetrieved(
                f"case {case.case_id} returned a Chunk the reader may not see"
            )
        ranks.setdefault(
            (candidate.external_id, candidate.heading_path), candidate.rank
        )
    return CaseOutcome(
        case_id=case.case_id,
        direction=case.direction,
        query_class=case.query_class,
        ranks=tuple(ranks.get((ref.external_id, ref.heading)) for ref in case.relevant),
        degraded=result.degraded,
```

Замініть на:

```python
    return result, elapsed


async def _rerank_case(
    case: EvaluationCase,
    reranker: Reranker,
    result: HybridRetrieval,
    identity: RerankerIdentity,
) -> RerankResult:
    """Rerank one case's fused Candidates, and only those, with one model."""
    reranked: RerankResult | None = None
    failure = "no result"
    try:
        reranked = await rerank_candidates(
            reranker,
            case.question,
            result.candidates,
            request_id=uuid5(_REQUEST_IDS, f"rerank {case.case_id}"),
            timeout_seconds=RERANK_TIMEOUT_SECONDS,
        )
    except Exception as error:
        # The reranker is outside code, and its message may quote a text.
        failure = _error_names(error)
    # Raised outside the `except`, so no linked error carries a text.
    if reranked is None:
        raise CaseFailed(f"case {case.case_id}: reranking raised {failure}")
    # The report names one reranker; scores of another must not mix in.
    if reranked.identity != identity:
        raise CaseFailed(f"case {case.case_id}: the reranker changed its identity")
    return reranked


def _outcome(
    case: EvaluationCase,
    candidates: Sequence[Candidate],
    degraded: bool,
    elapsed: float,
) -> CaseOutcome:
    """Record where each relevant Chunk ranked; refuse any restricted one."""
    ranks: dict[tuple[str, str | None], int] = {}
    for candidate in candidates:
        if candidate.source_id != SOURCE_IDS[Folder.PUBLIC]:
            raise IneligibleChunkRetrieved(
                f"case {case.case_id} returned a Chunk the reader may not see"
            )
        ranks.setdefault(
            (candidate.external_id, candidate.heading_path), candidate.rank
        )
    return CaseOutcome(
        case_id=case.case_id,
        direction=case.direction,
        query_class=case.query_class,
        ranks=tuple(ranks.get((ref.external_id, ref.heading)) for ref in case.relevant),
        degraded=degraded,
```

Потім у `scripts/run_retrieval_dataset.py`:

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
`artifacts/evaluations/bilingual-seed-sparse.md`.
"""
```

Замініть на:

```python
`artifacts/evaluations/bilingual-seed-sparse.md`.

`--rerank` runs every case with dense and lexical search fused, using the
local BGE-M3 server, then reranks each case's Candidates with the local
reranker server (`LOCAL_RERANKER_URL`, default `http://127.0.0.1:8767`),
which must already run the pinned revision; start it with
`uv run --project ml/bge_m3_server python ml/bge_m3_server/reranker_server.py`.
It writes the ranking delta and the latency on the reranker's device to
`artifacts/evaluations/bilingual-seed-rerank.md`.
"""
```

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
    compare_sparse,
    load_dataset,
    render_comparison,
    render_report,
    render_sparse_comparison,
    run_dataset,
    verify_corpus,
)
from knowledge_service.settings import Settings

REPORT = Path("artifacts/evaluations/bilingual-seed.md")
MANAGED_REPORT = Path("artifacts/evaluations/bilingual-seed-managed.md")
LOCAL_REPORT = Path("artifacts/evaluations/bilingual-seed-local.md")
SPARSE_REPORT = Path("artifacts/evaluations/bilingual-seed-sparse.md")
```

Замініть на:

```python
    compare_reranking,
    compare_sparse,
    load_dataset,
    render_comparison,
    render_report,
    render_rerank_comparison,
    render_sparse_comparison,
    run_dataset,
    verify_corpus,
)
from knowledge_service.retrieval.local_reranker import (
    LocalReranker,
    LocalRerankerSettings,
)
from knowledge_service.retrieval.reranking import RerankerError
from knowledge_service.settings import Settings

REPORT = Path("artifacts/evaluations/bilingual-seed.md")
MANAGED_REPORT = Path("artifacts/evaluations/bilingual-seed-managed.md")
LOCAL_REPORT = Path("artifacts/evaluations/bilingual-seed-local.md")
SPARSE_REPORT = Path("artifacts/evaluations/bilingual-seed-sparse.md")
RERANK_REPORT = Path("artifacts/evaluations/bilingual-seed-rerank.md")
```

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
    embeddings.add_argument("--sparse", action="store_true")
    arguments = parser.parse_args()
```

Замініть на:

```python
    embeddings.add_argument("--sparse", action="store_true")
    embeddings.add_argument("--rerank", action="store_true")
    arguments = parser.parse_args()
```

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
        else:
            rendered = asyncio.run(
                _run(runtime, dataset, corpus, arguments.limit, source)
            )
    except DatasetError as error:
        sys.stderr.write(f"dataset run failed: {error}\n")
        return 1
    except EmbeddingError as error:
        sys.stderr.write(f"the embedding model is not ready: {error!r}\n")
```

Замініть на:

```python
        elif arguments.rerank:
            default = RERANK_REPORT
            rendered = asyncio.run(
                _run_rerank(runtime, dataset, corpus, arguments.limit)
            )
        else:
            rendered = asyncio.run(
                _run(runtime, dataset, corpus, arguments.limit, source)
            )
    except DatasetError as error:
        sys.stderr.write(f"dataset run failed: {error}\n")
        return 1
    except EmbeddingError as error:
        sys.stderr.write(f"the embedding model is not ready: {error!r}\n")
        return 1
    except RerankerError as error:
        sys.stderr.write(f"the reranker is not ready: {error!r}\n")
```

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python

def _openai_model(api_key: SecretStr) -> ModelSource:
```

Замініть на:

```python

async def _run_rerank(
    runtime: DatabaseRuntime, dataset: EvaluationDataset, corpus: Path, limit: int
) -> str:
    try:
        async with (
            _local_model() as model,
            httpx.AsyncClient(trust_env=False) as client,
        ):
            reranker = LocalReranker(client, LocalRerankerSettings())
            # Before anything is written, so a stopped server leaves it empty.
            device = await reranker.verify_server(timeout_seconds=SERVER_CHECK_SECONDS)
            comparison = await compare_reranking(
                runtime,
                dataset,
                corpus,
                observed_at=datetime.now(UTC),
                model=model,
                reranker=reranker,
                device=device,
                limit=limit,
            )
        return render_rerank_comparison(comparison)
    finally:
        await runtime.dispose()


def _openai_model(api_key: SecretStr) -> ModelSource:
```

Перевірка: `make typecheck` показує `0 errors`, а `uv run python scripts/run_retrieval_dataset.py --rerank --sparse` друкує `not allowed with argument`.

### Крок 8 — Крок із кодом: протестуйте порівняння на PostgreSQL

Цей крок запускає порівняння на справжній базі з фейками.

**Простими словами:** Перший тест проганяє всі 16 випадків із фейковою моделлю ембедінгів і фейковим reranker-ом. Він перевіряє, що кожен випадок переранжовано один раз, що жоден текст, який отримав фейковий reranker, не містить закритого маркера, що reranking ніколи не знаходить Chunk, який пропустив пошук, і що ні звіт, ні логи не повторюють запитання чи маркер. Другий тест ламає reranker і перевіряє, що прогін зупиняється на першому випадку з типізованою помилкою без тексту. Третій дає reranker, який на старті називає одну модель, а під час оцінювання — іншу, і перевіряє, що прогін відмовляється змішувати їхні оцінки.

| Тест | Доводить |
|---|---|
| `test_every_case_is_reranked_and_only_eligible_text_reaches_the_reranker` | успіх і найризиковіший край: закритий текст ніколи не покидає базу на шляху до reranker-а |
| `test_a_reranker_that_fails_stops_the_run_quietly` | типізований збій: `CaseFailed` лише з типом помилки, нічого пов'язаного |
| `test_a_reranker_that_changes_its_identity_is_refused` | звіт ніколи не змішує оцінки двох моделей |

Створіть `tests/test_reranking_integration.py`:

```python
"""Reranking on real PostgreSQL: the seed run, what reaches the reranker, failure."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from fakes import FakeEmbeddingModel, FakeReranker
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import EmbeddingLimits
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    CaseFailed,
    EvaluationDataset,
    compare_reranking,
    load_dataset,
    render_rerank_comparison,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.reranking import (
    RerankerIdentity,
    RerankerUnavailable,
)
from knowledge_service.settings import Settings

OBSERVED_AT = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
LIMITS = EmbeddingLimits(max_inputs=64, max_input_tokens=1_000, max_batch_tokens=20_000)


class IdentityChanges(FakeReranker):
    """Names one model when the run starts and another while it scores."""

    def __init__(self) -> None:
        super().__init__()
        self._reads = 0

    @property
    def identity(self) -> RerankerIdentity:
        self._reads += 1
        model = "fake-reranker" if self._reads == 1 else "another-reranker"
        return RerankerIdentity(provider="fake", model=model)


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
def dataset() -> EvaluationDataset:
    return load_dataset(DATASET_DIRECTORY / CASES_FILE)


@pytest.mark.integration
async def test_every_case_is_reranked_and_only_eligible_text_reaches_the_reranker(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    reranker = FakeReranker()

    comparison = await compare_reranking(
        runtime,
        dataset,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        reranker=reranker,
        device="cpu",
    )

    before, after = comparison.retrieved, comparison.reranked
    assert before.branches == (RetrievalBranch.DENSE, RetrievalBranch.LEXICAL)
    assert (before.reranker, after.reranker) == (None, reranker.identity)
    assert [o.case_id for o in after.outcomes] == [c.case_id for c in dataset.cases]
    # One request per case: ten Candidates fit the fake's limits at once.
    assert comparison.requests == len(dataset.cases) == len(reranker.calls)
    # The permission check ran before reranking: no restricted text ever
    # left the database for the reranker, however relevant it might be.
    sent = [text for call in reranker.calls for text in call]
    assert sent
    assert all(RESTRICTED_MARKER not in text for text in sent)
    # Reranking reorders; it never finds what retrieval missed.
    for old, new in zip(before.outcomes, after.outcomes, strict=True):
        assert [r is None for r in new.ranks] == [r is None for r in old.ranks]
    rendered = render_rerank_comparison(comparison)
    assert "## Ranking delta" in rendered
    assert "- Reranker device: cpu." in rendered
    assert rendered.index("## Ranking delta") < rendered.index("## Latency")
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in dataset.cases)


@pytest.mark.integration
async def test_a_reranker_that_fails_stops_the_run_quietly(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    reranker = FakeReranker(error=RerankerUnavailable())

    with pytest.raises(CaseFailed) as raised:
        await compare_reranking(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            reranker=reranker,
            device="cpu",
        )

    first = dataset.cases[0]
    assert str(raised.value) == (
        f"case {first.case_id}: reranking raised RerankerUnavailable"
    )
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert len(reranker.calls) == 1


@pytest.mark.integration
async def test_a_reranker_that_changes_its_identity_is_refused(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    with pytest.raises(CaseFailed, match="changed its identity"):
        await compare_reranking(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            reranker=IdentityChanges(),
            device="cpu",
        )
```

Перевірка:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_reranking_integration.py -q
```

Очікувано: `3 passed`.

### Крок 9 — Без коду: запустіть на CPU і GPU, зламайте і вирішіть

Цей крок запускає обидві справжні моделі, вимірює reranker на двох пристроях, навмисно ламає одну гарантію і просить вас ухвалити рішення.

**Дія 1:** запустіть обидва сервери, кожен у своєму терміналі, і залиште працювати:

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py
```

```bash
RERANKER_DEVICE=cpu uv run --project ml/bge_m3_server python ml/bge_m3_server/reranker_server.py
```

Якщо порт 8765 зайнятий, запустіть сервер BGE-M3 з `BGE_M3_PORT=8766` на початку, а в третьому терміналі спершу виконайте `export LOCAL_EMBEDDING_URL=http://127.0.0.1:8766`.

**Дія 2:** у третьому терміналі запустіть двічі на свіжих базах і порівняйте все вище «## Latency»:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_dataset#')"
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --rerank
cp artifacts/evaluations/bilingual-seed-rerank.md /tmp/rerank-cpu.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --rerank > /dev/null
diff <(sed '/^## Latency/,$d' /tmp/rerank-cpu.md) <(sed '/^## Latency/,$d' artifacts/evaluations/bilingual-seed-rerank.md) && echo same
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
```

**Дія 3:** зупиніть reranker через Ctrl+C, запустіть його на GPU і зробіть ще один прогін:

```bash
RERANKER_DEVICE=mps uv run --project ml/bge_m3_server python ml/bge_m3_server/reranker_server.py
```

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --rerank --output /tmp/rerank-mps.md > /dev/null
diff <(sed '/^## Latency/,$d' /tmp/rerank-cpu.md) <(sed '/^## Latency/,$d' /tmp/rerank-mps.md) && echo same
sed -n '/^## Latency/,$p' /tmp/rerank-mps.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
unset KNOWLEDGE_SERVICE_DATABASE_URL
```

Якщо на вашій машині `mps` недоступний, сервер упаде на старті; запишіть це і залиште цифри CPU. Якщо звіти CPU і GPU відрізняються вище «## Latency», оцінка змінилася в останніх знаках і поміняла місцями двох кандидатів; запишіть, у якому випадку.

**Дія 4:** прочитайте звіти й запишіть рішення. У файлі evidence, у розділі «Decision», запишіть:

- підсумкову таблицю зі звіту (до і після, Recall@10 і MRR), бо `artifacts/` ігнорується Git;
- рухи з «Ranking delta»: які випадки зрушили, вгору чи вниз;
- розділи «Latency» прогонів на CPU і GPU поруч;
- **впроваджувати далі** чи **ще ні**, одним реченням, із трьома короткими причинами: зміна ранжування, час на запитання на кожному пристрої порівняно з часом пошуку, і що стається, коли сервер reranker-а лежить;
- принаймні одне **спростоване припущення** у формі «Я припускав X. Звіт показує Y (ID випадків і цифри). Отже, Z хибне».

**Дія 5:** навмисно зламайте спільний дедлайн. У `src/knowledge_service/retrieval/reranking.py`, у `rerank_candidates`, змініть `timeout_seconds=remaining,` на `timeout_seconds=timeout_seconds,` і `async with asyncio.timeout(remaining):` на `async with asyncio.timeout(timeout_seconds):`. Потім запустіть:

```bash
uv run pytest tests/test_reranking.py -q
```

Очікувано: 1 failed, `test_one_deadline_covers_every_request`: кожен запит отримав свіжий дедлайн, тож другий запит отримав увесь час замість залишку. Скасуйте зміну і запускайте знову, доки все не пройде. Зупиніть обидва сервери через Ctrl+C.

### Крок 10 — Без коду: перевірки, документація, evidence і прогрес

Цей крок доводить, що все й досі працює, оновлює текст, який зміна зробила неповним, і записує результат.

**Дія 1:** у `docs/architecture.md`:

Знайдіть у `docs/architecture.md`:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
own uv project (ADR 0009).
`ml/bge_m3_server/` serves BGE-M3 dense embeddings, and for the M05-T10
prototype its sparse term weights, on Python 3.12, on 127.0.0.1 only. The application reaches it through `LocalEmbeddingModel`,
which refuses any answer that does not name the pinned model revision.
```

Замініть на:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
own uv project (ADR 0009).
`ml/bge_m3_server/` serves BGE-M3 dense embeddings, and for the M05-T10
prototype its sparse term weights, on Python 3.12, on 127.0.0.1 only. The application reaches it through `LocalEmbeddingModel`,
which refuses any answer that does not name the pinned model revision.
`ml/bge_m3_server/reranker_server.py` serves the bge-reranker-v2-m3
cross-encoder from the same uv project, because it needs the same
libraries, as a separate process on 127.0.0.1 only. The application reaches
it through `LocalReranker`, which also refuses other revisions.
```

І в `Makefile`, щоб `make help` називав обидва сервери:

Знайдіть у `Makefile`:

```makefile
ml-check: ## Type-check and test the local BGE-M3 server (Python 3.12)
```

Замініть на:

```makefile
ml-check: ## Type-check and test the local BGE-M3 and reranker servers (Python 3.12)
```

**Дія 2:** запустіть перевірки по порядку:

```bash
uv run pytest tests/test_reranker_contract.py tests/test_local_reranker.py tests/test_reranking.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make ml-check
make docs-check
```

Очікувано: усе проходить. Еталонний прогін дав 83 passed для вузьких тестів, `make test-unit` — 1361 passed, `make test-integration` — 306 passed, `make ml-check` — 51 passed.

**Дія 3:** заповніть файл evidence:

- **Design:** змінені файли; чому ця модель і окремий процес; чому код живе в `retrieval`; що означає оцінка; як працюють пачки і спільний дедлайн; чому reranker бачить лише дозволених кандидатів.
- **Verification:** команди й кількості, результат повторюваності, прогони на CPU і GPU, навмисна поломка і відомі обмеження з розділу «Перш ніж почати».
- **Review:** ворожий рев'ю прототипу знайшов дві зламані гарантії і шість дрібніших проблем. Обидві поломки і п'ять дрібніших проблем виправлено, кожну з регресійним тестом, який падав до виправлення: дедлайн починався після підрахунку токенів, а reranker, що блокував event loop, усе одно повертав запізнілі оцінки, тож тепер дедлайн починається першим, а запізніла відповідь відкидається; результат повторно читав список викликача після відповіді reranker-а, тож змінений тим часом список міг підсунути неперевіреного кандидата, тому тепер кожен кандидат читається один раз, на початку, а того, чиї поля не прочитати, відкидають без його тексту; тайм-аут, що не є додатним числом, виходив нетипізованою помилкою, а закритий клієнт — як `RuntimeError`; сервер міг назвати пристрій на кшталт `cpu\n## x`, і це потрапляло в Markdown-звіт як є, тож тепер приймається лише проста назва; межі понад серверні приймалися і падали на кожному великому запиті, тож тепер вони падають на старті; а reranker, що змінив ідентичність посеред прогону, потрапив би у звіт під однією назвою. Коментар сервера про межу символів був хибний і його виправлено; підрахунок поза блокуванням «один запит за раз» лишається відомим обмеженням, бо сервер слухає лише loopback.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте завдання виконаним, додайте рядок M05-T11 у `docs/curriculum/PROGRESS.md`, встановіть наступним завданням M05-T12 і закомітьте з `m05-t11: implement a local cross encoder reranker`.

## Запитання для рефлексії

1. Що `rerank_candidates` ховає від коду, який її викликає? Перелічіть, про що викликачеві ніколи не треба думати: наприклад, межі сервера, скільки запитів надсилається чи як ділиться дедлайн.
2. Хтось згодом пришвидшує reranking, надсилаючи всі запити одночасно, а не один за одним. Яку обіцянку це може зламати на цьому сервері, який обробляє один запит за раз, і що зробить дедлайн?
3. Колега пропонує викидати кожного кандидата з оцінкою нижче нуля, щоб показувати менше, але кращих результатів. Що означає оцінка нуль для цієї моделі, і чому порт відмовляється давати їй фіксоване значення?
4. Яке з ваших передбачень виявилося хибним, і у що ви вірили щодо reranker-ів чи цього набору даних, що змусило вас так передбачити?
5. Що має бути правдою, перш ніж reranking запрацює для справжніх користувачів? Назвіть одну метрику, одну межу затримки й один випадок збою, який треба протестувати.

## Чекліст завершення

- [ ] Файл evidence містить інваріант і ваші передбачення, записані до будь-якого прогону.
- [ ] Сервер reranker-а закріплює ревізію і файли, відмовляє замість обрізання і повертає одну скінченну оцінку на уривок; `make ml-check` проходить.
- [ ] `Reranker`, `check_rerank_inputs` і `checked_scores` існують; `LocalReranker` відкидає інші ревізії, погані оцінки, завеликі чи стиснуті відповіді і не пише тексту в лог.
- [ ] `rerank_candidates` повертає рівно тих кандидатів, яких отримав, пачками в межах, під одним дедлайном.
- [ ] Закритий текст ніколи не доходить до reranker-а, а reranking ніколи не додає Chunk.
- [ ] Підкинутий текст не з'являється в помилках, пов'язаних помилках, логах, відповідях сервера чи звітах.
- [ ] Ви запустили `--rerank` двічі на CPU на свіжих базах, усе вище «## Latency» збіглося, і один раз на `mps`.
- [ ] Файл evidence містить підсумкову таблицю, рухи, затримку на CPU і GPU, рішення з причинами і спростоване припущення.
- [ ] Ви виконали навмисну поломку з кроку 9, побачили один упалий тест і скасували її.
- [ ] Усі перевірки проходять: форматування, lint, типи, unit, integration, ML, docs.
- [ ] Текст архітектури, evidence, рядок PROGRESS і коміт готові.

Коли напишете файли, поділіться ними чи виводом помилок, і я перевірю їх за цим чеклістом.
