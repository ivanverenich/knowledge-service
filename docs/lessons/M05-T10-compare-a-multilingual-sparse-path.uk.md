# M05-T10 — Порівнюємо багатомовний розріджений пошук

Англійська версія: [M05-T10-compare-a-multilingual-sparse-path.md](M05-T10-compare-a-multilingual-sparse-path.md)

Вихідне завдання: [M05-T10](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T10-compare-a-multilingual-sparse-path.md)

## Що ви зробите

Пошук за словами в PostgreSQL слабкий для української: конфігурація `simple` не знає, що «відпустка» і «відпустки» — те саме слово. BGE-M3, модель, яку ви запускаєте локально з M05-T09, має другий вихід, який може це виправити: для кожного тексту вона дає короткий список шматочків слів, кожен зі своєю вагою. У цьому уроці ви збудуєте прототип пошуку за цими вагами, проженете з ним і з PostgreSQL ті самі 16 запитань і вирішите, чи варто його залишати. У продакшені нічого не змінюється: пошук за замовчуванням лишається «зміст плюс слова PostgreSQL».

- Сервер BGE-M3 отримує другий ендпоінт, `POST /v1/sparse`, який повертає ці ваги для закріпленої моделі.
- Невеликий порт `SparseModel` описує, що така модель обіцяє, а `LocalEmbeddingModel` його виконує.
- `search_sparse_chunks` ранжує Chunks за вагами. Перевірка прав працює в PostgreSQL, у тому самому запиті, що й ранжування, тож Chunk, який читачеві бачити не можна, ніколи не ранжується.
- `compare_sparse` і опція скрипта `--sparse` проганяють кожен випадок обома способами і звітують про якість, вартість і затримку.
- Ви записуєте рішення «залишити» чи «відкинути», підкріплене цими цифрами.

| Що просить завдання | Де це відбувається |
|---|---|
| Прототип розрідженого пошуку BGE-M3 | Кроки 2–7: ендпоінт сервера, порт і адаптер, модуль пошуку, порівняння |
| Проти PostgreSQL, для української | Кроки 7 і 9: ті самі 16 випадків, слова PostgreSQL і розріджені ваги, зі зрізами за мовою |
| Без зміни поведінки продакшену за замовчуванням | Жодної міграції, жодної нової гілки за замовчуванням; індекс живе в пам'яті одного прогону оцінювання |
| Детерміновані тести: успіх, найризикованіший випадок, типізована помилка | Крок 8: прогін набору; закритий Chunk із найкращим балом ніколи не повертається; збій моделі під час індексування дає `CorpusNotEmbedded` |
| Звіт про якість, затримку, експлуатаційну вартість і рішення «залишити/відкинути» | Крок 9: зрізи звіту, розділи «Cost» і «Latency», ваше рішення в доказах |
| Жодного підкинутого чутливого тексту в телеметрії | Кроки 3, 5 і 8: відповіді, помилки, логи і звіти перевіряються на підкинутий текст |

Цей урок містить код: кроки 2–8 пишуть код. Кроки 1, 9 і 10 коду не потребують. Крок 9 запускає справжню модель локально; це нічого не коштує.

Терміни цього уроку:

- **Ідентифікатор токена (token ID):** номер шматочка слова в словнику моделі. BGE-M3 знає 250 002 шматочки; «▁VPN» — один із них.
- **Розріджений вектор (sparse vector):** список пар (ідентифікатор токена, вага) для шматочків, які є в тексті. «Розріджений», бо майже всі з 250 002 можливих значень нульові і їх просто не записують. Щільний вектор, навпаки, має 1024 числа, і всі вони використовуються.
- **Вага (weight):** наскільки шматочок важливий у цьому тексті. Рідкісний, промовистий шматочок на кшталт «▁VPN» отримує високу вагу; кома — низьку або жодної.
- **Бал (score):** наскільки Chunk підходить до запитання: перемножте ваги кожного спільного шматочка і додайте добутки. Немає спільних шматочків — немає балу.
- **Покажчик за токенами (postings, inverted index):** для кожного ідентифікатора токена — Chunks, у яких він є. Як предметний покажчик у кінці книжки, що для кожного слова перелічує сторінки.
- **Медіанна затримка (median latency):** середній час, коли всі часи впорядкувати. Половина запитань швидша, половина повільніша; один повільний викид її не зсуває.

## Перш ніж почати

Цей розділ пояснює рішення за кодом, щоб кожен крок був зрозумілим, коли ви до нього дійдете.

- **Чому розріджений вихід BGE-M3, а не окрема пошукова система.** Завдання дозволяє обидва варіанти. Окрема пошукова система (search engine), наприклад OpenSearch з українським аналізатором, означає новий сервіс, який треба запускати, захищати і синхронізувати. BGE-M3 уже закріплена і працює з M05-T09; її розрідженому виходу потрібен ще один файл на 3,5 КБ, `sparse_linear.pt`, який сервер закріплює за відбитком, як і решту.
- **Як отримують ваги.** Модель читає текст один раз. Для кожного шматочка вона обчислює прихований стан, список із 1024 чисел, а один маленький шар перетворює його на одне число. Від'ємне стає нулем. Шматочок, що трапляється двічі, зберігає більшу вагу, а службові токени, які позначають початок і кінець тексту, відкидаються. Так робить власний код BGE-M3.
- **Де живе індекс.** У пам'яті, на один прогін оцінювання. Щоб його зберігати, потрібні міграція і нова колонка, а це зміна продакшену заради експерименту, який можуть відкинути. Звіт оцінює, скільки коштувало б зберігання: тип pgvector `sparsevec` займає 8 байтів на вагу.
- **Як перевірка прав лишається в базі.** Бали рахуються в Python з покажчика. У PostgreSQL вони потрапляють як два списки, ідентифікатори Chunks і бали, які `unnest` перетворює на маленьку таблицю. Запит з'єднує цю таблицю з `chunks` і `documents` і застосовує `eligible_chunks_predicate` у тому самому WHERE, що й ORDER BY і LIMIT. Тож закритий Chunk може мати найкращий бал і все одно ніколи не ранжується, не рахується, а його текст ніколи не покидає PostgreSQL. Chunk, видалений після індексування, не має рядка і випадає так само.
- **Одна модель на індекс.** Індекс пам'ятає, яка модель зважувала Chunks. Запит, зважений іншою моделлю, дає `SparseIndexMismatch` ще до звернення до PostgreSQL, як пошук за змістом робить із просторами ембедінгів (Embedding Spaces).
- **Моделі не довіряємо ні в чому.** Кожна вага має бути скінченним числом більшим за 0 і не більшим за 1000, кожен ідентифікатор токена — всередині словника, за зростанням. Межа не дає сумам переповнитися до нескінченності. Текст не може мати більше ваг, ніж токенів, тож одна зламана відповідь не роздує індекс.
- **Найризикованіший випадок.** Закритий Chunk, який найкраще підходить до запитання. Без перевірки прав він був би першим. Тест будує саме такий випадок: запитанням є власний текст закритого Chunk.
- **Як міряємо вартість і затримку.** Звіт додає розділ «Cost» (проіндексовані Chunks, збережені ваги, оцінка обсягу, виклики моделі на запитання) і розділ «Latency» (час побудови індексу, медіанний і найдовший час на випадок). Затримка змінюється від прогону до прогону, тому вона в самому кінці: усе вище за неї мусить збігатися у двох прогонах.
- **Що зважує рішення.** Якість за зрізами, особливо `uk->uk` і морфологію. Додатковий виклик моделі на кожне запитання; але з локальним пошуком за змістом запитання вже йде на той самий сервер, тож один прохід міг би повернути обидва виходи. І стійкість: сьогодні, коли сервер моделі лежить, пошук за словами в PostgreSQL усе одно відповідає. Якби його замінив розріджений пошук, падіння моделі поклало б обидві гілки.
- **Відомі обмеження.** Зважується лише текст Chunk, а PostgreSQL зважує ще назву і заголовки. Буквальні `<s>` чи `</s>` у тексті читаються як службові токени і ні з чим не збігаються. Пачка доповнює кожен текст до найдовшого, тож один довгий текст сповільнює всю пачку (як і для щільних ембедінгів). Помилка розрідженої гілки, як-от `SparseIndexMismatch`, пробивається нагору, а не робить гібридний пошук частковим, бо означає неправильне налаштування. Шістнадцять випадків — це мало. Міжмовні запитання за природою мають мало спільних шматочків, тож розріджений пошук їх і не має виправити.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої передбачення

Перш ніж з'явиться код, запишіть, що має бути правдою наприкінці уроку і що, на вашу думку, зроблять розріджені ваги. Передбачення, записані зараз, до прогону, — це те, що перевіряє крок 9.

Створіть `docs/evidence/M05-T10-multilingual-sparse-path.md` з цим каркасом, а потім замініть підказки до передбачень своїми реченнями:

```markdown
# M05-T10 — Multilingual sparse path evidence

## Invariant

`search_sparse_chunks` returns only Chunks the reader may see: the
permission check runs in the same SQL statement as the ranking, so a
restricted Chunk with the best score is never ranked, counted, or returned.
Weights come only from the model that built the index, and every weight the
model sends is checked before it is used. No input text appears in an
error, a log record, a server answer, or the report. The production default
is unchanged: no migration, and no default search uses sparse weights.

## Expected failing check

Before the change, `uv run pytest tests/test_sparse_search.py` fails to
import `knowledge_service.retrieval.sparse_search`.

## Predictions

- `uk->uk` and morphology, sparse against PostgreSQL: ...
- Cross-language (`en->uk`, `uk->en`): ...
- Identifiers such as "VPN-4031": ...
- Median latency per question, sparse against PostgreSQL: ...
- My expected decision, keep or reject, and the one number that would change it: ...

## Design

## Verification

## Decision

## Reflection
```

Перевірка: файл існує, і кожне передбачення — повне речення, яке цифра з кроку 9 може спростувати.

### Крок 2 — Крок з кодом: дайте серверу розріджений ендпоінт

Цей крок вчить сервер BGE-M3 повертати розріджені ваги поруч зі щільними векторами.

**Простими словами:** Сервер уже читає кожен текст моделлю, щоб отримати щільний вектор. Тепер він може ще й пропустити це саме прочитання через маленький розріджений шар моделі і повернути для кожного тексту шматочки слів, які в ньому є, з однією вагою на кожен. Файл цього шару він закріплює за відбитком, як і решту, тримає всі обмеження і відмовляється віддавати ваги, які не є додатними скінченними числами в межах словника.

| Зміна | Що вона робить |
|---|---|
| `VOCABULARY_SIZE`, `sparse_linear.pt` у `FILE_HASHES` | межа словника і закріплений розріджений шар; запис у цьому списку ще й змушує `download_model` його завантажити |
| `Encoder.encode_sparse` | що серверу треба від моделі для розріджених ваг; у тестах — підробка |
| `encoded` | спільна частина обох ендпоінтів: обмеження, одна пачка за раз, збої без тексту |
| `POST /v1/sparse` | перевіряє обмеження, кодує, потім повертає пари `[token ID, weight]` для кожного тексту, за ідентифікатором токена, з моделлю, ревізією і розміром словника |
| `BgeM3Encoder.encode_sparse` | справжнє обчислення: прихований стан на токен, розріджений шар, `relu`, найбільша вага для повторюваного токена, без службових токенів |
| `_sparse_pairs` | відмовляє в результаті з неправильною кількістю, ідентифікатором токена поза словником або вагою, що не є додатним скінченним float |

У `ml/bge_m3_server/bge_m3_server.py` зробіть ці зміни по черзі:

Знайдіть у `ml/bge_m3_server/bge_m3_server.py`:

```python
- **Unit-length dense vectors** of 1024 numbers, one per input, in order.
- **No text in errors or logs.** Error bodies hold a reason and an index;
```

Замініть на:

```python
- **Unit-length dense vectors** of 1024 numbers, one per input, in order.
- **Sparse term weights** from the same pass through the model: for each
  input, the vocabulary pieces it contains, each with one positive weight
  (BGE-M3's lexical matching output). Special tokens are left out.
- **No text in errors or logs.** Error bodies hold a reason and an index;
```

Знайдіть у `ml/bge_m3_server/bge_m3_server.py`:

```python
from collections.abc import Sequence
```

Замініть на:

```python
from collections.abc import Callable, Sequence
```

Знайдіть у `ml/bge_m3_server/bge_m3_server.py`:

```python
# SHA-256 of the large files at REVISION, from the Hugging Face file listing.
FILE_HASHES = {
    "pytorch_model.bin": "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38",  # noqa: E501
    "tokenizer.json": "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08",  # noqa: E501  # gitleaks:allow
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",  # noqa: E501
```

Замініть на:

```python
# Token IDs in sparse weights are below this: the size of the vocabulary.
VOCABULARY_SIZE = 250_002
# SHA-256 of the large files at REVISION, from the Hugging Face file listing.
FILE_HASHES = {
    "pytorch_model.bin": "b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38",  # noqa: E501
    "tokenizer.json": "21106b6d7dab2952c1d496fb21d5dc9db75c28ed361a05f5020bbba27810dd08",  # noqa: E501  # gitleaks:allow
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",  # noqa: E501
    # The layer that turns each token's hidden state into its sparse weight.
    "sparse_linear.pt": "45c93804d2142b8f6d7ec6914ae23a1eee9c6a1d27d83d908a20d2afb3595ad9",  # noqa: E501
```

Знайдіть у `ml/bge_m3_server/bge_m3_server.py`:

```python
        """Return one unit-length vector per text, in order."""
        ...
```

Замініть на:

```python
        """Return one unit-length vector per text, in order."""
        ...

    def encode_sparse(self, texts: Sequence[str]) -> list[dict[int, float]]:
        """Return, per text in order, a weight for each token ID it contains."""
        ...
```

Знайдіть у `ml/bge_m3_server/bge_m3_server.py`:

```python
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
```

Замініть на:

```python
    async def encoded(
        inputs: list[str], work: Callable[[Sequence[str]], Any]
    ) -> tuple[int, Any] | JSONResponse:
        """Check the limits, then run `work` on the inputs, one batch at a time."""
        # Counting takes time on long texts; off the event loop, so a large
        # request cannot hold up every other request.
        checked = await asyncio.to_thread(_check_limits, encoder, inputs)
        if isinstance(checked, JSONResponse):
            return checked
        failure: str | None = None
        result: Any = None
        async with busy:
            try:
                result = await asyncio.to_thread(work, inputs)
            except Exception as error:
                # Its message or traceback could quote an input; keep the type.
                failure = type(error).__name__
        if failure is not None:
            logger.warning("encoding failed error=%s", failure)
            return _refusal(500, "encoding_failed")
        return checked, result

    @app.post("/v1/embeddings")
    async def embeddings(body: EmbedRequest) -> JSONResponse:
        outcome = await encoded(body.inputs, encoder.encode)
        if isinstance(outcome, JSONResponse):
            return outcome
        total, vectors = outcome
        if not _valid(vectors, len(body.inputs)):
            logger.warning("encoding failed error=InvalidVectors")
            return _refusal(500, "encoding_failed")
        logger.info("embedded inputs=%d tokens=%d", len(body.inputs), total)
        return JSONResponse(
            {**_provenance(), "input_tokens": total, "vectors": vectors}
        )

    @app.post("/v1/sparse")
    async def sparse(body: EmbedRequest) -> JSONResponse:
        outcome = await encoded(body.inputs, encoder.encode_sparse)
        if isinstance(outcome, JSONResponse):
            return outcome
        total, weights = outcome
        pairs = _sparse_pairs(weights, len(body.inputs))
        if pairs is None:
            logger.warning("encoding failed error=InvalidWeights")
            return _refusal(500, "encoding_failed")
        logger.info("sparse inputs=%d tokens=%d", len(body.inputs), total)
        return JSONResponse(
            {
                "model": MODEL_ID,
                "revision": REVISION,
                "vocabulary_size": VOCABULARY_SIZE,
                "input_tokens": total,
                "weights": pairs,
            }
        )

    return app


class BgeM3Encoder:
    """BGE-M3 dense embeddings and sparse weights through sentence-transformers."""

    def __init__(self, folder: Path, device: str) -> None:
        # Imported here, so tests with a fake encoder never load PyTorch.
        import torch
        from sentence_transformers import SentenceTransformer

        self._torch: Any = torch
        self._model = SentenceTransformer(str(folder), device=device)
        self._tokenizer: Any = self._model.tokenizer
        # The transformer under the pooling layer: its hidden state per token.
        self._backbone: Any = self._model[0].auto_model
        # BGE-M3's sparse head: one weight per token from its hidden state.
        self._sparse: Any = torch.nn.Linear(DIMENSIONS, 1)
        self._sparse.load_state_dict(
            torch.load(
                folder / "sparse_linear.pt", map_location="cpu", weights_only=True
            )
        )
        self._sparse.to(device).eval()
        self._device = device
        self._special = {
            self._tokenizer.cls_token_id,
            self._tokenizer.eos_token_id,
            self._tokenizer.pad_token_id,
            self._tokenizer.unk_token_id,
        }

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

    def encode_sparse(self, texts: Sequence[str]) -> list[dict[int, float]]:
        weights: list[dict[int, float]] = []
        for start in range(0, len(texts), ENCODE_BATCH_SIZE):
            weights += self._sparse_batch(
                list(texts[start : start + ENCODE_BATCH_SIZE])
            )
        return weights

    def _sparse_batch(self, texts: list[str]) -> list[dict[int, float]]:
        # As BGE-M3's own code does: relu(linear(hidden state)) per token, the
        # largest weight when a token repeats, special tokens left out.
        features = self._tokenizer(texts, padding=True, return_tensors="pt")
        features = {name: value.to(self._device) for name, value in features.items()}
        with self._torch.inference_mode():
            hidden = self._backbone(**features).last_hidden_state
            token_weights = self._torch.relu(self._sparse(hidden)).squeeze(-1)
        result: list[dict[int, float]] = []
        for ids, values, mask in zip(
            features["input_ids"].tolist(),
            token_weights.tolist(),
            features["attention_mask"].tolist(),
            strict=True,
        ):
            weights: dict[int, float] = {}
            for token, value, kept in zip(ids, values, mask, strict=True):
                if kept and token not in self._special and value > 0:
                    weights[token] = max(value, weights.get(token, 0.0))
            result.append(weights)
        return result
```

Знайдіть у `ml/bge_m3_server/bge_m3_server.py`:

```python

def _count(encoder: Encoder, text: str) -> int | None:
```

Замініть на:

```python

def _sparse_pairs(weights: object, expected: int) -> list[list[list[float]]] | None:
    """Return each text's weights as [token ID, weight] pairs by token ID, or None.

    None when the count, a token ID, or a weight is wrong: a weight must be a
    finite number above zero, and a token ID an int inside the vocabulary.
    """
    if not isinstance(weights, list) or len(weights) != expected:
        return None
    result: list[list[list[float]]] = []
    for item in weights:
        if not isinstance(item, dict):
            return None
        pairs: list[list[float]] = []
        for token, value in sorted(item.items()):
            if (
                type(token) is not int
                or not 0 <= token < VOCABULARY_SIZE
                or type(value) is not float
                or not math.isfinite(value)
                or value <= 0
            ):
                return None
            pairs.append([token, value])
        result.append(pairs)
    return result


def _count(encoder: Encoder, text: str) -> int | None:
```

Перевірка: запустіть сервер із кореня репозиторію (під час першого запуску він завантажить розріджений шар на 3,5 КБ):

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py
```

У другому терміналі:

```bash
curl -s 127.0.0.1:8765/v1/sparse -H 'Content-Type: application/json' -d '{"inputs": ["VPN error 4031"]}'
```

Очікувано: JSON-об'єкт із `"vocabulary_size":250002`, `"input_tokens":6` і одним списком із чотирьох пар `[token ID, weight]`, усі ваги між 0 і 1. Якщо порт 8765 зайнятий, запустіть сервер із `BGE_M3_PORT=8766` і використайте цей порт. Зупиніть сервер через Ctrl+C.

### Крок 3 — Крок з кодом: протестуйте розріджений ендпоінт

Цей крок тестує новий ендпоінт без завантаження моделі, плюс один тест зі справжньою моделлю.

**Простими словами:** Підробна модель отримує розріджений метод, який повертає фіксовані ваги або, на замовлення, зламані. Тести перевіряють, що добрі ваги повертаються за порядком токенів із названою моделлю, що діють ті самі обмеження і що від'ємна, нульова, NaN, нескінченна або позасловникова вага відхиляється без повтору тексту. Один тест завантажує справжню модель і перевіряє, що її ваги додатні, не містять службових токенів і однакові, якщо спитати двічі.

| Тест | Що доводить |
|---|---|
| `test_sparse_weights_name_the_pinned_model_and_come_by_token_id` | успіх: пари за ідентифікатором токена, з моделлю, ревізією, розміром словника, кількістю токенів |
| `test_sparse_weights_obey_the_same_limits` | задовгий текст відхиляється і ніколи не кодується |
| `test_invalid_sparse_weights_are_refused_without_text` | дев'ять видів поганих ваг дають `encoding_failed`, без тексту у відповіді чи логах |
| `test_a_sparse_encoding_failure_carries_no_text` | модель, що кидає помилку, нічого не видає |
| `test_real_sparse_weights_are_positive_repeatable_and_skip_special_tokens` | справжня модель: «▁VPN» має вагу, ваги додатні, службових токенів немає, повторні виклики збігаються |

У `ml/bge_m3_server/test_bge_m3_server.py`:

Знайдіть у `ml/bge_m3_server/test_bge_m3_server.py`:

```python

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
```

Замініть на:

```python
from typing import cast

import pytest
from fastapi.testclient import TestClient

import bge_m3_server as server

PLANTED = "the salary of Olena is 9000"


class FakeEncoder:
    """Counts one token per character and records what it encoded."""

    def __init__(
        self,
        *,
        fail: bool = False,
        value: float = 1.0,
        sparse: object = None,
    ) -> None:
        self.encoded: list[list[str]] = []
        self._fail = fail
        self._value = value
        # What `encode_sparse` returns for every text; one pair by default.
        self._sparse = {7: 0.5, 3: 0.25} if sparse is None else sparse

    def count_tokens(self, text: str) -> int:
        if text == "uncountable":
            raise ValueError(f"cannot tokenize {text}")
        return len(text)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        if self._fail:
            raise RuntimeError(f"out of memory while encoding {texts[0]}")
        self.encoded.append(list(texts))
        return [[self._value] + [0.0] * (server.DIMENSIONS - 1) for _ in texts]

    def encode_sparse(self, texts: Sequence[str]) -> list[dict[int, float]]:
        if self._fail:
            raise RuntimeError(f"out of memory while encoding {texts[0]}")
        self.encoded.append(list(texts))
        # Deliberately unchecked: tests pass weights the server must refuse.
        return [cast(dict[int, float], self._sparse) for _ in texts]
```

Знайдіть у `ml/bge_m3_server/test_bge_m3_server.py`:

```python

def _cached_model() -> Path | None:
```

Замініть на:

```python

def test_sparse_weights_name_the_pinned_model_and_come_by_token_id() -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post("/v1/sparse", json={"inputs": ["a", "bc"]})

    assert answer.status_code == 200
    assert answer.json() == {
        "model": "BAAI/bge-m3",
        "revision": server.REVISION,
        "vocabulary_size": server.VOCABULARY_SIZE,
        "input_tokens": 3,
        "weights": [[[3, 0.25], [7, 0.5]], [[3, 0.25], [7, 0.5]]],
    }
    assert encoder.encoded == [["a", "bc"]]


def test_sparse_weights_obey_the_same_limits() -> None:
    encoder = FakeEncoder()

    answer = client(encoder).post(
        "/v1/sparse", json={"inputs": ["x" * (server.MAX_INPUT_TOKENS + 1)]}
    )

    assert answer.status_code == 413
    assert answer.json() == {"error": "input_too_long", "index": 0}
    assert encoder.encoded == []


@pytest.mark.parametrize(
    "weights",
    [
        {7: -0.5},
        {7: 0.0},
        {7: float("nan")},
        {7: float("inf")},
        {server.VOCABULARY_SIZE: 0.5},
        {-1: 0.5},
        {"7": 0.5},
        {7: 1},
        [0.5],
    ],
    ids=[
        "negative",
        "zero",
        "not a number",
        "infinite",
        "outside the vocabulary",
        "negative token",
        "token as text",
        "weight as int",
        "not a mapping",
    ],
)
def test_invalid_sparse_weights_are_refused_without_text(
    weights: object, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    answer = client(FakeEncoder(sparse=weights)).post(
        "/v1/sparse", json={"inputs": [PLANTED]}
    )

    assert answer.status_code == 500
    assert answer.json() == {"error": "encoding_failed", "index": None}
    assert PLANTED not in answer.text
    assert PLANTED not in caplog.text


def test_a_sparse_encoding_failure_carries_no_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    answer = client(FakeEncoder(fail=True)).post(
        "/v1/sparse", json={"inputs": [PLANTED]}
    )

    assert answer.status_code == 500
    assert PLANTED not in answer.text
    assert PLANTED not in caplog.text


def _cached_model() -> Path | None:
```

У кінці `ml/bge_m3_server/test_bge_m3_server.py` додайте:

```python


@pytest.mark.model
def test_real_sparse_weights_are_positive_repeatable_and_skip_special_tokens() -> None:
    folder = _cached_model()
    if folder is None or not (folder / "sparse_linear.pt").is_file():
        pytest.skip("the pinned BGE-M3 files are not in the Hugging Face cache")
    from tokenizers import Tokenizer

    encoder = server.BgeM3Encoder(folder, "cpu")
    texts = ["Помилка VPN-4031 під час підключення", "відпустка"]

    first = encoder.encode_sparse(texts)
    second = encoder.encode_sparse(list(reversed(texts)))

    assert first == list(reversed(second))
    tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
    vpn = tokenizer.token_to_id("\N{LOWER ONE EIGHTH BLOCK}VPN")
    assert vpn in first[0]
    special = {0, 1, 2, 3}  # <s>, <pad>, </s>, <unk>
    for weights in first:
        assert weights
        assert not special & weights.keys()
        assert all(0 < value < 10 for value in weights.values())
```

Перевірка: `make ml-check` показує `0 errors` і `29 passed`. Тестові зі справжньою моделлю потрібен `sparse_linear.pt` у кеші, який завантажив запуск сервера на кроці 2; без нього тест пропускається.

### Крок 4 — Крок з кодом: розріджений порт і адаптер

Цей крок визначає, що обіцяє будь-яка розріджена модель, і змушує `LocalEmbeddingModel` цю обіцянку виконувати.

**Простими словами:** Новий невеликий модуль описує, як виглядають розріджені ваги, і перевіряє відповідь моделі, не довіряючи їй. Локальний адаптер отримує другий метод: він надсилає тексти на новий ендпоінт, перевіряє модель і версію у відповіді, відхиляє текст, що має більше ваг, ніж токенів, і перетворює кожну проблему на ті самі типізовані помилки, що й для щільних ембедінгів. Заодно адаптер тепер відхиляє стиснуті відповіді: невелике стиснуте тіло могло б розпакуватися в набагато більше за обмеження у 8 МБ.

| Частина | Що вона робить |
|---|---|
| `SparseVector` | пари `(token ID, weight)` за зростанням ідентифікатора токена |
| `MAX_SPARSE_WEIGHT` | 1000: набагато більше за справжні ваги BGE-M3 і досить мало, щоб жодна сума не переповнилася |
| `SparseModelIdentity` | постачальник, модель, розмір словника; ваги двох ідентичностей ніколи не змішуються |
| `SparseModel` | `sparse_identity`, `limits`, `count_tokens`, `encode_sparse` |
| `checked_sparse_vectors` | відхиляє неправильну кількість, погану пару, токен чи вагу або пари не за порядком, через `EmbeddingMalformedResponse` |
| `LocalEmbeddingModel.encode_sparse` | перевіряє вхід, надсилає один запит на `/v1/sparse`, перевіряє походження і кожну вагу, логує лише кількості |
| `_send` | просить нестиснуту відповідь і відхиляє стиснуту, не читаючи її |

Створіть `src/knowledge_service/embeddings/sparse.py`:

```python
"""Sparse term weights: which vocabulary pieces a text holds, and how much each counts.

A sparse model such as BGE-M3 gives every text a short list of token IDs
from its vocabulary, each with a positive weight. Two texts match as much as
the weights of the tokens they share: the sum of the products, so a rare,
important piece shared by both counts far more than a shared comma. Unlike
a dense vector, almost every entry is zero, so only the others are kept.

This module is the port a sparse model fills. It is a prototype for the
M05-T10 comparison with PostgreSQL full-text search; nothing in production
uses it.

What a `SparseVector` promises, once `checked_sparse_vectors` returned it:

- **Pairs by token ID.** `(token_id, weight)` pairs in ascending token ID
  order, each ID once, each an int inside the model's vocabulary.
- **Positive, bounded weights.** Above zero, since a zero weight is left
  out instead, and at most `MAX_SPARSE_WEIGHT`, so multiplying and summing
  them can never overflow to infinity and flatten the ranking.
- **Possibly empty.** A text of only punctuation may hold no weighted token;
  it then matches nothing.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, cast
from uuid import UUID

from knowledge_service.embeddings.port import (
    EmbeddingLimits,
    EmbeddingMalformedResponse,
    InvalidEmbedding,
)

type SparseVector = tuple[tuple[int, float], ...]

# BGE-M3's weights stay well below 10. A bound far above that keeps every
# product and every sum of up to 8192 of them a finite number.
MAX_SPARSE_WEIGHT = 1_000.0


@dataclass(frozen=True, slots=True)
class SparseModelIdentity:
    """Which model produced sparse weights; weights of two identities never mix."""

    provider: str
    model: str
    vocabulary_size: int

    def __post_init__(self) -> None:
        if type(self.provider) is not str or type(self.model) is not str:
            raise InvalidEmbedding("provider and model must be strings")
        if not self.provider.strip() or not self.model.strip():
            raise InvalidEmbedding("provider and model must not be empty")
        if type(self.vocabulary_size) is not int or self.vocabulary_size < 1:
            raise InvalidEmbedding("vocabulary_size must be a positive integer")


class SparseModel(Protocol):
    """A model that turns batches of texts into sparse term weights."""

    @property
    def sparse_identity(self) -> SparseModelIdentity:
        """The provider, model, and vocabulary size."""
        ...

    @property
    def limits(self) -> EmbeddingLimits:
        """The most one call to `encode_sparse` may carry."""
        ...

    def count_tokens(self, text: str) -> int:
        """Count `text` in this model's tokens, never fewer than the model does."""
        ...

    async def encode_sparse(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[SparseVector, ...]:
        """Return one sparse vector per text, in order, or raise an `EmbeddingError`."""
        ...


def checked_sparse_vectors(
    items: object, *, expected: int, vocabulary_size: int
) -> tuple[SparseVector, ...]:
    """Return the model's weights as sparse vectors, or raise if any is invalid.

    `items` is whatever the model sent: a list with one list of
    `[token_id, weight]` pairs per text. Nothing about its shape is trusted;
    any fault raises `EmbeddingMalformedResponse` naming the text's index.
    """
    vectors = _list(items)
    if vectors is None or len(vectors) != expected:
        raise EmbeddingMalformedResponse(f"expected {expected} sparse vectors")
    return tuple(
        _checked(item, index, vocabulary_size) for index, item in enumerate(vectors)
    )


def _checked(item: object, index: int, vocabulary_size: int) -> SparseVector:
    pairs = _list(item)
    if pairs is None:
        raise EmbeddingMalformedResponse(f"sparse vector {index} is not a list")
    result: list[tuple[int, float]] = []
    for pair in pairs:
        values = _list(pair)
        if values is None or len(values) != 2:
            raise EmbeddingMalformedResponse(f"sparse vector {index} has a bad pair")
        token, weight = values
        # bool is an int, so `True` would otherwise pass as token 1.
        if type(token) is not int or not 0 <= token < vocabulary_size:
            raise EmbeddingMalformedResponse(f"sparse vector {index} has a bad token")
        if (
            type(weight) is not float
            or not math.isfinite(weight)
            or not 0 < weight <= MAX_SPARSE_WEIGHT
        ):
            raise EmbeddingMalformedResponse(f"sparse vector {index} has a bad weight")
        if result and token <= result[-1][0]:
            raise EmbeddingMalformedResponse(
                f"sparse vector {index} is not ordered by token"
            )
        result.append((token, weight))
    return tuple(result)


def _list(value: object) -> list[object] | None:
    return cast(list[object], value) if isinstance(value, list) else None
```

Потім у `src/knowledge_service/embeddings/local_adapter.py`:

Знайдіть у `src/knowledge_service/embeddings/local_adapter.py`:

```python
  `MAX_RESPONSE_BYTES`, so a broken server cannot fill the memory.
```

Замініть на:

```python
  `MAX_RESPONSE_BYTES`, so a broken server cannot fill the memory. A
  compressed answer is refused unread, because it could unpack to far more.
- **Sparse weights, as a prototype.** `encode_sparse` asks the same server
  for BGE-M3's sparse term weights (see `embeddings.sparse`), with the same
  limits, provenance check, size cap, and errors. Only the M05-T10
  comparison uses them.
```

Знайдіть у `src/knowledge_service/embeddings/local_adapter.py`:

```python

logger = logging.getLogger(__name__)

PROVIDER = "local"
EMBEDDINGS_PATH = "/v1/embeddings"
MODEL_PATH = "/v1/model"
```

Замініть на:

```python
from knowledge_service.embeddings.sparse import (
    SparseModelIdentity,
    SparseVector,
    checked_sparse_vectors,
)

logger = logging.getLogger(__name__)

PROVIDER = "local"
EMBEDDINGS_PATH = "/v1/embeddings"
MODEL_PATH = "/v1/model"
SPARSE_PATH = "/v1/sparse"
```

Знайдіть у `src/knowledge_service/embeddings/local_adapter.py`:

```python
    dimensions: int = Field(default=1024, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
```

Замініть на:

```python
    dimensions: int = Field(default=1024, gt=0)
    vocabulary_size: int = Field(default=250_002, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
```

Знайдіть у `src/knowledge_service/embeddings/local_adapter.py`:

```python
        self._limits = EmbeddingLimits(
            max_inputs=settings.max_inputs,
            max_input_tokens=settings.max_input_tokens,
            max_batch_tokens=settings.max_batch_tokens,
        )

    @property
    def identity(self) -> EmbeddingModelIdentity:
        return self._identity
```

Замініть на:

```python
        self._sparse_identity = SparseModelIdentity(
            provider=PROVIDER,
            model=f"{settings.model}@{settings.revision}",
            vocabulary_size=settings.vocabulary_size,
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
    def sparse_identity(self) -> SparseModelIdentity:
        return self._sparse_identity
```

Знайдіть у `src/knowledge_service/embeddings/local_adapter.py`:

```python

    async def verify_server(self, *, timeout_seconds: float) -> None:
```

Замініть на:

```python

    async def encode_sparse(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[SparseVector, ...]:
        """Return BGE-M3's sparse term weights for each text, in order.

        Checked and refused exactly like `embed`; the answer must also name
        the configured model, revision, and vocabulary size.
        """
        deadline = self._deadline(timeout_seconds)
        try:
            check_inputs(texts, self._limits, self.count_tokens)
        except EmbeddingInputRejected as error:
            _log(request_id, self._identity, len(texts), 0, error, operation="sparse")
            raise
        if not texts:
            return ()
        started_at = time.perf_counter()
        try:
            status, content = await self._send(
                "POST",
                SPARSE_PATH,
                deadline,
                body={"inputs": list(texts)},
                headers={"X-Request-ID": str(request_id)},
            )
            vectors = self._sparse_from(status, content, texts)
        except EmbeddingError as error:
            _log(
                request_id,
                self._identity,
                len(texts),
                _since(started_at),
                error,
                operation="sparse",
            )
            raise
        _log(
            request_id,
            self._identity,
            len(texts),
            _since(started_at),
            None,
            operation="sparse",
        )
        return vectors

    async def verify_server(self, *, timeout_seconds: float) -> None:
```

Знайдіть у `src/knowledge_service/embeddings/local_adapter.py`:

```python
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
```

Замініть на:

```python
        """Send one request; return its status and its bytes, up to the cap.

        The answer must not be compressed: a small compressed body can unpack
        to far more than the cap before the cap is checked.
        """
        status = 0
        content = bytearray()
        encoded = False
        failure: EmbeddingError | None = None
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
            failure = EmbeddingTimeout()
        except httpx.HTTPError:
            failure = EmbeddingUnavailable()
        if len(content) > MAX_RESPONSE_BYTES:
            failure = EmbeddingMalformedResponse("the response is too large")
        if encoded and failure is None:
            failure = EmbeddingMalformedResponse("the response is encoded")
```

Знайдіть у `src/knowledge_service/embeddings/local_adapter.py`:

```python
            expected=expected,
        )

    def _check_provenance(self, body: dict[str, object] | None) -> dict[str, object]:
```

Замініть на:

```python
            expected=expected,
        )

    def _sparse_from(
        self, status: int, content: bytes, texts: Sequence[str]
    ) -> tuple[SparseVector, ...]:
        _check_status(status)
        body = _json_object(content)
        if body is None:
            raise EmbeddingMalformedResponse("the local model response was not JSON")
        size = body.get("vocabulary_size")
        if (
            body.get("model") != self._settings.model
            or body.get("revision") != self._settings.revision
            or type(size) is not int
            or size != self._settings.vocabulary_size
        ):
            # Weights from another vocabulary would match the wrong tokens.
            raise EmbeddingRequestRefused(
                "the local model is not the configured model, revision, or vocabulary"
            )
        vectors = checked_sparse_vectors(
            body.get("weights"),
            expected=len(texts),
            vocabulary_size=self._settings.vocabulary_size,
        )
        for index, (text, vector) in enumerate(zip(texts, vectors, strict=True)):
            # A text has at most one weight per token; more would swell the index.
            if len(vector) > self.count_tokens(text):
                raise EmbeddingMalformedResponse(
                    f"sparse vector {index} has more weights than its text has tokens"
                )
        return vectors

    def _check_provenance(self, body: dict[str, object] | None) -> dict[str, object]:
```

Знайдіть у `src/knowledge_service/embeddings/local_adapter.py`:

```python
    usage: EmbeddingUsage | None = None,
) -> None:
```

Замініть на:

```python
    usage: EmbeddingUsage | None = None,
    *,
    operation: str | None = None,
) -> None:
```

Знайдіть у `src/knowledge_service/embeddings/local_adapter.py`:

```python
        fields["input_tokens"] = usage.input_tokens
    level = logging.INFO if error is None else logging.WARNING
```

Замініть на:

```python
        fields["input_tokens"] = usage.input_tokens
    if operation is not None:
        fields["operation"] = operation
    level = logging.INFO if error is None else logging.WARNING
```

Перевірка: `make typecheck` показує `0 errors`.

### Крок 5 — Крок з кодом: протестуйте розріджений метод адаптера

Цей крок тестує `encode_sparse` проти внутрішньопроцесного замінника сервера.

**Простими словами:** Тестові помічники вчаться відповідати на `/v1/sparse`. Підробна розріджена модель перетворює кожне слово на один токен, тож тексти зі спільними словами мають спільні токени, як і справжні. Тести перевіряють добру відповідь, а потім кожен спосіб, яким відповідь може бути неправильною: інша модель чи словник, погана вага, забагато ваг для тексту, стиснуте тіло і сервер, що лежить, — без тексту в помилках чи логах.

| Частина | Що вона робить |
|---|---|
| `word_weights`, `FakeSparseModel` (`tests/fakes.py`) | детерміновані підробні ваги; `SparseModel`, що записує кожну пачку |
| `StubBgeM3Server` (`tests/local_stubs.py`) | відповідає на `/v1/sparse`; може надіслати інше походження, погані ваги або додаткові заголовки |
| `test_sparse_weights_come_from_the_pinned_model_in_order` | успіх |
| `test_sparse_weights_from_another_model_are_refused` | інша ревізія, модель чи словник дають `EmbeddingRequestRefused` |
| `test_malformed_sparse_weights_are_refused` | дев'ять поганих форм дають `EmbeddingMalformedResponse` |
| `test_a_sparse_failure_carries_no_text_in_its_chain_or_the_logs` | зупинений сервер — це `EmbeddingUnavailable`, без тексту |
| `test_more_sparse_weights_than_the_text_has_tokens_are_refused` | одна відповідь не може роздути індекс |
| `test_a_compressed_answer_is_refused_before_it_is_unpacked` | обмеження у 8 МБ тримається і для стиснутих тіл |

У `tests/fakes.py`:

Знайдіть у `tests/fakes.py`:

```python
import math
from collections.abc import Sequence
```

Замініть на:

```python
import math
import re
from collections.abc import Sequence
```

Знайдіть у `tests/fakes.py`:

```python
    checked_batch,
)
```

Замініть на:

```python
    checked_batch,
)
from knowledge_service.embeddings.sparse import (
    SparseModelIdentity,
    SparseVector,
    checked_sparse_vectors,
)
```

У кінці `tests/fakes.py` додайте:

```python


# The BGE-M3 vocabulary size; fake token IDs stay inside it.
FAKE_VOCABULARY = 250_002


def word_weights(text: str) -> list[list[float]]:
    """Fake sparse weights: one token per lowercased word, weighted by length.

    A word's token ID is a hash of the word, so the same word always gets
    the same ID, and texts sharing words share tokens, as real ones do.
    Pairs come in token order, each token once, as the port requires.
    """
    weights: dict[int, float] = {}
    for word in re.findall(r"\w+", text.lower()):
        token = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4]) % (
            FAKE_VOCABULARY
        )
        weights[token] = max(weights.get(token, 0.0), len(word) / 10)
    return [[token, weights[token]] for token in sorted(weights)]


class FakeSparseModel:
    """Deterministic SparseModel over `word_weights`; records every batch."""

    def __init__(
        self,
        *,
        limits: EmbeddingLimits | None = None,
        error: EmbeddingError | None = None,
    ) -> None:
        self._identity = SparseModelIdentity(
            provider="fake", model="fake-sparse", vocabulary_size=FAKE_VOCABULARY
        )
        self._limits = limits or EmbeddingLimits(
            max_inputs=16, max_input_tokens=1_000, max_batch_tokens=4_000
        )
        self._error = error
        self.batches: list[tuple[str, ...]] = []

    @property
    def sparse_identity(self) -> SparseModelIdentity:
        return self._identity

    @property
    def limits(self) -> EmbeddingLimits:
        return self._limits

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    async def encode_sparse(
        self,
        texts: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[SparseVector, ...]:
        check_inputs(texts, self._limits, self.count_tokens)
        self.batches.append(tuple(texts))
        if self._error is not None:
            raise self._error
        return checked_sparse_vectors(
            [word_weights(text) for text in texts],
            expected=len(texts),
            vocabulary_size=FAKE_VOCABULARY,
        )
```

У `tests/local_stubs.py`:

Знайдіть у `tests/local_stubs.py`:

```python

from knowledge_service.embeddings.local_adapter import (
```

Замініть на:

```python

from fakes import word_weights
from knowledge_service.embeddings.local_adapter import (
```

Знайдіть у `tests/local_stubs.py`:

```python
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
```

Замініть на:

```python
        vocabulary_size: object = 250_002,
        sparse_weights: object = None,
        headers: dict[str, str] | None = None,
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
        self._vocabulary_size = vocabulary_size
        # Sent as the weights of every text instead of `word_weights`.
        self._sparse_weights = sparse_weights
        self._headers = headers or {}
```

Знайдіть у `tests/local_stubs.py`:

```python
            return httpx.Response(self._status, content=self._body or b"{}")
        if request.method == "GET":
            return httpx.Response(200, json=self._provenance)
        texts: list[str] = json.loads(request.content)["inputs"]
```

Замініть на:

```python
            return httpx.Response(
                self._status, content=self._body or b"{}", headers=self._headers
            )
        if request.method == "GET":
            return httpx.Response(200, json=self._provenance)
        texts: list[str] = json.loads(request.content)["inputs"]
        if request.url.path == "/v1/sparse":
            return httpx.Response(
                200,
                json={
                    "model": self._provenance["model"],
                    "revision": self._provenance["revision"],
                    "vocabulary_size": self._vocabulary_size,
                    "input_tokens": 3 * len(texts),
                    "weights": [
                        word_weights(t)
                        if self._sparse_weights is None
                        else self._sparse_weights
                        for t in texts
                    ],
                },
            )
```

У `tests/test_local_embeddings.py`:

Знайдіть у `tests/test_local_embeddings.py`:

```python
import json
import logging
import math
import unicodedata
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

```

Замініть на:

```python
import gzip
import json
import logging
import math
import unicodedata
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from fakes import word_weights
```

Знайдіть у `tests/test_local_embeddings.py`:

```python
)
from local_stubs import URL, StubBgeM3Server, local_embedding_model
```

Замініть на:

```python
)
from knowledge_service.embeddings.sparse import SparseModelIdentity
from local_stubs import URL, StubBgeM3Server, local_embedding_model
```

У кінці `tests/test_local_embeddings.py` додайте:

```python


async def test_sparse_weights_come_from_the_pinned_model_in_order() -> None:
    server = StubBgeM3Server()
    model = local_embedding_model(server)

    vectors = await model.encode_sparse(
        ["VPN error", "відпустка"], request_id=REQUEST_ID, timeout_seconds=2.0
    )

    assert model.sparse_identity == SparseModelIdentity(
        provider="local",
        model=f"BAAI/bge-m3@{BGE_M3_REVISION}",
        vocabulary_size=250_002,
    )
    assert [list(map(list, vector)) for vector in vectors] == [
        word_weights("VPN error"),
        word_weights("відпустка"),
    ]
    assert server.requests[0].url.path == "/v1/sparse"


@pytest.mark.parametrize(
    "server",
    [
        StubBgeM3Server(revision="0" * 40),
        StubBgeM3Server(model="BAAI/bge-m3-unsupervised"),
        StubBgeM3Server(vocabulary_size=250_001),
        StubBgeM3Server(vocabulary_size=250_002.0),
    ],
    ids=["other revision", "other model", "other vocabulary", "vocabulary as float"],
)
async def test_sparse_weights_from_another_model_are_refused(
    server: StubBgeM3Server,
) -> None:
    model = local_embedding_model(server)

    with pytest.raises(EmbeddingRequestRefused):
        await model.encode_sparse(
            ["vacation"], request_id=REQUEST_ID, timeout_seconds=2.0
        )


@pytest.mark.parametrize(
    "weights",
    [
        [[7, -0.5]],
        [[7, 0.0]],
        [[7, 1]],
        [[250_002, 0.5]],
        [[True, 0.5]],
        [[9, 0.5], [3, 0.5]],
        [[3, 0.5], [3, 0.5]],
        [[3, 0.5, 1.0]],
        "weights",
    ],
    ids=[
        "negative",
        "zero",
        "int weight",
        "outside the vocabulary",
        "bool token",
        "out of order",
        "repeated token",
        "three values",
        "not a list",
    ],
)
async def test_malformed_sparse_weights_are_refused(weights: object) -> None:
    model = local_embedding_model(StubBgeM3Server(sparse_weights=weights))

    with pytest.raises(EmbeddingMalformedResponse):
        await model.encode_sparse(
            ["vacation"], request_id=REQUEST_ID, timeout_seconds=2.0
        )


async def test_a_sparse_failure_carries_no_text_in_its_chain_or_the_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = local_embedding_model(
        StubBgeM3Server(error=httpx.ConnectError(f"refused {PLANTED}"))
    )

    with pytest.raises(EmbeddingUnavailable) as raised:
        await model.encode_sparse([PLANTED], request_id=REQUEST_ID, timeout_seconds=2.0)

    assert all(PLANTED not in repr(error) for error in chain(raised.value))
    assert PLANTED not in caplog.text
    (record,) = caplog.records
    assert record.__dict__["embedding"]["operation"] == "sparse"


async def test_more_sparse_weights_than_the_text_has_tokens_are_refused() -> None:
    # "hi" holds at most a few tokens; thousands of pairs would swell the index.
    weights = [[token, 0.5] for token in range(5_000)]
    model = local_embedding_model(StubBgeM3Server(sparse_weights=weights))

    with pytest.raises(EmbeddingMalformedResponse):
        await model.encode_sparse(["hi"], request_id=REQUEST_ID, timeout_seconds=2.0)


async def test_a_compressed_answer_is_refused_before_it_is_unpacked() -> None:
    # 64 MB of zeros packs into about 64 KB; unpacked, it would pass the cap.
    packed = gzip.compress(b"0" * (64 * 1024 * 1024))
    server = StubBgeM3Server(body=packed, headers={"Content-Encoding": "gzip"})

    with pytest.raises(EmbeddingMalformedResponse, match="encoded"):
        await embed(server, ["vacation policy"])
```

Перевірка: `uv run pytest tests/test_local_embeddings.py -q` показує `50 passed`, а `uv run pytest tests/test_embedding_model_contract.py -q` і далі показує `18 passed, 1 deselected`.

### Крок 6 — Крок з кодом: напишіть розріджений пошук

Цей крок пише сам пошук в одному новому модулі і дає йому назву гілки.

**Простими словами:** Щоб побудувати індекс, модуль читає текст кожного доступного Chunk, просить у моделі ваги пачками, які модель приймає, перевіряє кожну вагу і розкладає їх за токенами. Щоб шукати, він перевіряє ліміт, модель і ваги запитання; додає добутки для кожного Chunk, що має спільний шматочок із запитанням; потім передає бали PostgreSQL, яка відкидає кожен Chunk, що читачеві бачити не можна, і повертає найкращі з решти разом із текстом і джерелом.

| Частина | Що вона робить |
|---|---|
| `RetrievalBranch.SPARSE` | назва гілки, яку використовують порівняння і звіти; жоден пошук за замовчуванням її не містить |
| `SparseIndex` | ідентичність моделі, покажчик за токенами і скільки в ньому Chunks і ваг |
| `sparse_index` | перевіряє кожен вектор і будує покажчик у порядку ідентифікаторів Chunks, щоб бали щоразу додавалися в тому самому порядку |
| `build_sparse_index` | читає кожен доступний Chunk, закриті теж, і зважує їх пачками в межах обмежень моделі |
| `search_sparse_chunks` | перевіряє ліміт, модель і запит до PostgreSQL; потім рахує бали і дає PostgreSQL відфільтрувати й упорядкувати |
| `sparse_search_statement` | бали як таблиця `unnest`, з'єднана з Chunks, з перевіркою прав у тому самому WHERE |
| `sparse_branch` | зважує запитання, потім шукає; під'єднується до `retrieve_hybrid`, як і інші гілки |
| `InvalidSparseQuery`, `InvalidSparseWeights`, `SparseIndexMismatch` | типізовані відмови, що не називають тексту |

У `src/knowledge_service/retrieval/fusion.py`:

Знайдіть у `src/knowledge_service/retrieval/fusion.py`:

```python
    LEXICAL = "lexical"

```

Замініть на:

```python
    LEXICAL = "lexical"
    # The M05-T10 prototype: BGE-M3 sparse term weights. No default uses it.
    SPARSE = "sparse"

```

Створіть `src/knowledge_service/retrieval/sparse_search.py`:

```python
"""Prototype: find Chunks by BGE-M3 sparse term weights, for one reader.

This is the M05-T10 experiment, not a production branch: no default uses
it, and its index lives in memory for one evaluation run. It answers one
question: does BGE-M3's sparse output find Ukrainian (and English) Chunks
better than PostgreSQL full-text search, and at what cost?

How it works:

- `build_sparse_index` reads every available Chunk, as dense embedding
  does, asks the sparse model for its weights in batches within the
  model's limits, and keeps them as postings: for each token ID, the
  Chunks that hold it and their weights.
- `search_sparse_chunks` scores a query against the postings, a sum of
  weight products over shared tokens, then lets PostgreSQL drop every
  Chunk the reader may not see and order the rest, in one statement.

What a caller can rely on in a `SparseSearchResult`:

- **Eligible.** The scores go to PostgreSQL as parameters, and the
  permission check sits in the same WHERE as the ordering, as in vector
  search: a Chunk the reader may not see is never ranked, never counted,
  and its text never leaves the database. A Chunk deleted after the index
  was built has no row, so it is never returned either.
- **Ranked.** Highest score first, then by Chunk ID; `rank` counts from 1.
  A Chunk that shares no token with the query has no score and is not a
  Candidate, so a query of only unweighted tokens returns nothing.
- **One model.** The query's weights must come from the model that built
  the index; otherwise `SparseIndexMismatch` is raised before the database
  is used.
- **Bounded.** At most `limit` Candidates, an int from 1 to
  `MAX_CANDIDATES`, and `truncated` says whether more eligible Chunks match.
- **Quiet.** Errors name no text, and the fields holding Document text are
  kept out of repr, as in every Candidate.
"""

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime
from knowledge_service.documents import ContentVersion, DocumentAvailability
from knowledge_service.embeddings.sparse import (
    MAX_SPARSE_WEIGHT,
    SparseModel,
    SparseModelIdentity,
    SparseVector,
)
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.retrieval.hybrid import BranchRequest, BranchResult, BranchSearch
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES

# Scores travel as 64-bit floats, so PostgreSQL orders exactly what was summed.
_SCORE = sa.Double[float]()


class SparseSearchError(Exception):
    """Base error for a sparse search."""


class InvalidSparseQuery(SparseSearchError):
    """The query weights or the limit cannot be searched; nothing was read."""


class InvalidSparseWeights(SparseSearchError):
    """A Chunk's weights break the sparse port's rules; nothing was indexed."""


class SparseIndexMismatch(SparseSearchError):
    """The query's weights come from another model than the index's."""


@dataclass(frozen=True, slots=True)
class SparseIndex:
    """Sparse weights of every indexed Chunk, kept by token for scoring.

    `postings` maps a token ID to the Chunks that hold it, by Chunk ID, with
    their weights. `chunks` counts the indexed Chunks and `weights` the
    stored (token, weight) pairs, which is what storing them would cost.
    """

    identity: SparseModelIdentity
    postings: Mapping[int, tuple[tuple[ChunkId, float], ...]] = field(repr=False)
    chunks: int
    weights: int


@dataclass(frozen=True, slots=True)
class SparseCandidate:
    """One eligible Chunk that shares weighted tokens with the query.

    `score` is the sum of weight products over shared tokens; higher is
    better and only the order means anything. The fields holding Document
    text are kept out of repr.
    """

    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    score: float
    external_id: str = field(repr=False)
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_anchor: str | None = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class SparseSearchResult:
    """The Candidates of one sparse search; see the module docstring."""

    limit: int
    candidates: tuple[SparseCandidate, ...]
    truncated: bool


def sparse_index(
    identity: SparseModelIdentity, vectors: Mapping[ChunkId, SparseVector]
) -> SparseIndex:
    """Turn each Chunk's weights into postings by token, in Chunk ID order.

    Every vector is checked like a query, because a model is outside code:
    a NaN, negative, or huge weight raises `InvalidSparseWeights`.
    """
    postings: dict[int, list[tuple[ChunkId, float]]] = defaultdict(list)
    for chunk_id in sorted(vectors, key=lambda chunk: chunk.value):
        checked = _checked_weights(
            vectors[chunk_id], identity.vocabulary_size, InvalidSparseWeights
        )
        for token, weight in checked:
            postings[token].append((chunk_id, weight))
    return SparseIndex(
        identity=identity,
        postings={token: tuple(pairs) for token, pairs in postings.items()},
        chunks=len(vectors),
        weights=sum(len(vector) for vector in vectors.values()),
    )


async def build_sparse_index(
    runtime: DatabaseRuntime,
    model: SparseModel,
    *,
    request_id: UUID,
    timeout_seconds: float,
) -> SparseIndex:
    """Ask `model` for the weights of every available Chunk and index them.

    Restricted Chunks are indexed too, because search must be able to score
    them for the permission check to matter. Raises the model's
    `EmbeddingError` when a batch fails; nothing is kept then.
    """
    async with runtime.transaction() as connection:
        rows = (
            await connection.execute(
                sa.select(chunks.c.chunk_id, chunks.c.text)
                .select_from(chunks.join(documents))
                .where(documents.c.availability == DocumentAvailability.AVAILABLE.value)
                .order_by(chunks.c.chunk_id)
            )
        ).all()
    ids = [ChunkId(cast(UUID, row.chunk_id)) for row in rows]
    texts = [cast(str, row.text) for row in rows]
    identity = model.sparse_identity
    vectors: dict[ChunkId, SparseVector] = {}
    for start, end in _batches(texts, model):
        weights = await model.encode_sparse(
            texts[start:end],
            request_id=uuid5(request_id, f"sparse {start}"),
            timeout_seconds=timeout_seconds,
        )
        vectors.update(zip(ids[start:end], weights, strict=True))
    return sparse_index(identity, vectors)


async def search_sparse_chunks(
    connection: AsyncConnection,
    *,
    index: SparseIndex,
    query: SparseVector,
    identity: SparseModelIdentity,
    context: AuthorizationContext,
    limit: int,
) -> SparseSearchResult:
    """Return up to `limit` eligible Chunks, highest sparse score first.

    `identity` names the model that weighted the query. Raises
    `InvalidSparseQuery` for a bad limit or query and `SparseIndexMismatch`
    when `identity` is not the index's, both before the database is used.
    """
    # bool is an int, so `True` would otherwise pass as a limit of 1.
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise InvalidSparseQuery(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    if identity != index.identity:
        raise SparseIndexMismatch("the query was weighted by another model")
    checked = _checked_weights(query, identity.vocabulary_size, InvalidSparseQuery)
    scores = _scores(index, checked)
    if not scores:
        return SparseSearchResult(limit=limit, candidates=(), truncated=False)
    statement = sparse_search_statement(
        scores=scores,
        eligibility=eligible_chunks_predicate(context),
        # One row more than asked shows whether more eligible Chunks match.
        limit=limit + 1,
    )
    records = (await connection.execute(statement)).mappings().all()
    return SparseSearchResult(
        limit=limit,
        candidates=tuple(
            _candidate_from(dict(record), rank=position)
            for position, record in enumerate(records[:limit], start=1)
        ),
        truncated=len(records) > limit,
    )


def sparse_search_statement(
    *,
    scores: Mapping[ChunkId, float],
    eligibility: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    """Select the best-scored Chunks that meet `eligibility`.

    The scores arrive as two array parameters and become a table with
    `unnest`; the permission check sits in the same WHERE, so PostgreSQL
    drops ineligible Chunks before ORDER BY and LIMIT. Ties in score are
    broken by Chunk ID.
    """
    ids = sorted(scores, key=lambda chunk: chunk.value)
    scored = (
        sa.func.unnest(
            sa.bindparam("chunk_ids", [c.value for c in ids], type_=ARRAY(sa.Uuid())),
            sa.bindparam("scores", [scores[c] for c in ids], type_=ARRAY(_SCORE)),
        )
        .table_valued(sa.column("chunk_id", sa.Uuid()), sa.column("score", _SCORE))
        .render_derived(name="scored", with_types=False)
    )
    return (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            documents.c.source_id,
            chunks.c.content_version,
            chunks.c.ordinal,
            scored.c.score,
            documents.c.external_id,
            documents.c.title,
            chunks.c.heading_path,
            chunks.c.source_anchor,
            chunks.c.text,
        )
        .select_from(scored)
        .join(chunks, chunks.c.chunk_id == scored.c.chunk_id)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(eligibility)
        .order_by(scored.c.score.desc(), chunks.c.chunk_id)
        .limit(limit)
    )


def sparse_branch(
    runtime: DatabaseRuntime, model: SparseModel, index: SparseIndex
) -> BranchSearch:
    """Weight the question with `model`, then search `index`."""

    async def search(request: BranchRequest) -> BranchResult:
        vectors = await model.encode_sparse(
            [request.question],
            request_id=request.request_id,
            timeout_seconds=request.timeout_seconds,
        )
        async with runtime.transaction() as connection:
            return await search_sparse_chunks(
                connection,
                index=index,
                query=vectors[0],
                identity=model.sparse_identity,
                context=request.context,
                limit=request.limit,
            )

    return search


def _batches(texts: Sequence[str], model: SparseModel) -> list[tuple[int, int]]:
    """Cut `texts` into consecutive (start, end) runs within the model's limits."""
    limits = model.limits
    runs: list[tuple[int, int]] = []
    start = 0
    while start < len(texts):
        end, total = start, 0
        while end < len(texts) and end - start < limits.max_inputs:
            tokens = model.count_tokens(texts[end])
            if end > start and total + tokens > limits.max_batch_tokens:
                break
            total += tokens
            end += 1
        runs.append((start, end))
        start = end
    return runs


def _checked_weights(
    value: object, vocabulary_size: int, error: type[SparseSearchError]
) -> SparseVector:
    """Return `value` if it keeps the sparse port's rules, else raise `error`."""
    if type(value) is not tuple:
        raise error("weights must be a tuple of (token, weight) pairs")
    previous = -1
    for pair in cast(tuple[object, ...], value):
        if type(pair) is not tuple or len(cast(tuple[object, ...], pair)) != 2:
            raise error("weights must be a tuple of (token, weight) pairs")
        token, weight = cast(tuple[object, object], pair)
        if type(token) is not int or not 0 <= token < vocabulary_size:
            raise error("every token must be an int in the vocabulary")
        if (
            type(weight) is not float
            or not math.isfinite(weight)
            or not 0 < weight <= MAX_SPARSE_WEIGHT
        ):
            raise error(f"every weight must be above 0 and at most {MAX_SPARSE_WEIGHT}")
        if token <= previous:
            raise error("tokens must be in ascending order, each once")
        previous = token
    return cast(SparseVector, value)


def _scores(index: SparseIndex, query: SparseVector) -> dict[ChunkId, float]:
    """Sum weight products over shared tokens, in a fixed order every time."""
    scores: dict[ChunkId, float] = defaultdict(float)
    for token, weight in query:
        for chunk_id, chunk_weight in index.postings.get(token, ()):
            scores[chunk_id] += weight * chunk_weight
    return dict(scores)


def _candidate_from(record: Mapping[str, object], *, rank: int) -> SparseCandidate:
    return SparseCandidate(
        rank=rank,
        chunk_id=ChunkId(cast(UUID, record["chunk_id"])),
        document_id=DocumentId(cast(UUID, record["document_id"])),
        source_id=SourceId(cast(UUID, record["source_id"])),
        content_version=ContentVersion(cast(int, record["content_version"])),
        ordinal=cast(int, record["ordinal"]),
        score=cast(float, record["score"]),
        external_id=cast(str, record["external_id"]),
        title=cast(str, record["title"]),
        heading_path=cast("str | None", record["heading_path"]),
        source_anchor=cast("str | None", record["source_anchor"]),
        text=cast(str, record["text"]),
    )
```

Перевірка: `make typecheck` показує `0 errors`, а `make test-unit` і далі проходить: новий елемент переліку не ламає код, який перебирає `RetrievalBranch`.

### Крок 7 — Крок з кодом: порівняйте обидва пошуки і звітуйте про вартість

Цей крок проганяє випадки набору зі словами PostgreSQL і з розрідженими вагами та пише звіт.

**Простими словами:** Порівняння один раз записує корпус набору в порожню базу, будує розріджений індекс і проганяє кожен випадок двічі: раз пошуком за словами PostgreSQL і раз розрідженим пошуком. Воно засікає час кожного випадку. Звіт потім показує обидва прогони поруч за зрізами, скільки коштує індекс і скільки триває запитання. Скрипт отримує опцію `--sparse`, яка робить усе це з локальним сервером.

| Частина | Що вона робить |
|---|---|
| `CaseOutcome.elapsed_seconds` | реальний час одного випадку; не бере участі в порівнянні результатів, бо змінюється |
| `DatasetReport.sparse` | називає розріджену модель у налаштуваннях звіту |
| `SparseComparison` | обидва звіти, розмір індексу і час його побудови |
| `compare_sparse` | записує корпус, будує індекс (збій стає `CorpusNotEmbedded` лише з назвами типів), проганяє слова, проганяє розріджений пошук |
| `render_sparse_comparison` | звіт поруч, потім «## Cost», потім «## Latency» наприкінці |
| `--sparse` | запускає все з локальним сервером і пише `artifacts/evaluations/bilingual-seed-sparse.md` |

У `src/knowledge_service/retrieval/evaluation_dataset.py`:

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
  ones included, is sent to the embedding model's provider.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
```

Замініть на:

```python
  ones included, is sent to the embedding model's provider.
- **Compared with sparse weights.** `compare_sparse` indexes every Chunk
  with a sparse model such as BGE-M3 (see `retrieval.sparse_search`), then
  runs every case twice: PostgreSQL word search, and sparse search alone.
  It also returns what the index costs and how long each case took, which
  `render_sparse_comparison` adds to the report. This is the M05-T10
  prototype; nothing in production uses it.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
import re
from collections import Counter
```

Замініть на:

```python
import re
import statistics
import time
from collections import Counter
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
)
from knowledge_service.identifiers import EmbeddingSpaceId, SourceId, UserId
```

Замініть на:

```python
)
from knowledge_service.embeddings.sparse import SparseModel, SparseModelIdentity
from knowledge_service.identifiers import EmbeddingSpaceId, SourceId, UserId
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
from knowledge_service.retrieval.sparse_search import (
    build_sparse_index,
    sparse_branch,
)
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    degraded: bool

```

Замініть на:

```python
    degraded: bool
    # Wall time of the search, in seconds; not compared, as it varies by run.
    elapsed_seconds: float = field(default=0.0, compare=False)

```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    embedding: EmbeddingModelIdentity | None = None

```

Замініть на:

```python
    embedding: EmbeddingModelIdentity | None = None
    # The model behind the sparse branch; None when the run had none.
    sparse: SparseModelIdentity | None = None


@dataclass(frozen=True, slots=True)
class SparseComparison:
    """Word search and sparse search on the same corpus, and what each costs.

    `chunks` and `weights` count the indexed Chunks and their stored
    (token, weight) pairs; `index_seconds` is the time to build the index.
    """

    lexical: DatasetReport
    sparse: DatasetReport
    chunks: int
    weights: int
    index_seconds: float

```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python

def score_slices(report: DatasetReport) -> tuple[SliceScore, ...]:
```

Замініть на:

```python

async def compare_sparse(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: SparseModel,
    limit: int = DEFAULT_LIMIT,
) -> SparseComparison:
    """Ingest and index the corpus once, then run every case two ways.

    Every Chunk, restricted ones included, is sent to `model` for its
    weights, because search must be able to score a restricted Chunk for
    the permission check to matter. Raises like `run_dataset`, and
    `CorpusNotEmbedded` when the corpus cannot be indexed.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    await verify_corpus(dataset, corpus)
    await _ingest(runtime, dataset, corpus, observed_at, embedding=None)
    started = time.perf_counter()
    failure = "no index"
    index = None
    try:
        index = await build_sparse_index(
            runtime,
            model,
            request_id=uuid5(_REQUEST_IDS, "sparse index"),
            timeout_seconds=EMBEDDING_TIMEOUT_SECONDS,
        )
    except Exception as error:
        # The model is outside code, and its message may quote Chunk text.
        failure = _error_names(error)
    # Raised outside the `except`, so no linked error carries the text.
    if index is None:
        raise CorpusNotEmbedded(f"the corpus could not be indexed: {failure}")
    index_seconds = time.perf_counter() - started
    lexical = await _run_cases(
        dataset,
        {RetrievalBranch.LEXICAL: lexical_branch(runtime)},
        limit,
        embedding=None,
    )
    sparse = await _run_cases(
        dataset,
        {RetrievalBranch.SPARSE: sparse_branch(runtime, model, index)},
        limit,
        embedding=None,
        sparse=index.identity,
    )
    return SparseComparison(
        lexical=lexical,
        sparse=sparse,
        chunks=index.chunks,
        weights=index.weights,
        index_seconds=index_seconds,
    )


def score_slices(report: DatasetReport) -> tuple[SliceScore, ...]:
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    return body + "".join("\n" + render_report(report) for report in reports)

```

Замініть на:

```python
    return body + "".join("\n" + render_report(report) for report in reports)


def render_sparse_comparison(comparison: SparseComparison) -> str:
    """Return the side-by-side report, then what the index costs, then timing.

    Everything above "## Latency" depends only on the data and the model, so
    two runs can be compared with `diff`; the latency section changes from
    run to run.
    """
    reports = (comparison.lexical, comparison.sparse)
    chunks, weights = comparison.chunks, comparison.weights
    per_chunk = weights / chunks if chunks else 0.0
    # pgvector's sparsevec: 4 bytes of index and 4 of value per weight, plus
    # a 16-byte header per vector.
    stored = 8 * weights + 16 * chunks
    lines = [
        "",
        "## Cost",
        "",
        f"- Indexed Chunks: {chunks}, restricted ones included.",
        f"- Stored weights: {weights}, {per_chunk:.1f} per Chunk on average.",
        f"- As pgvector sparsevec: about {stored / 1024:.1f} KiB for this corpus.",
        "- Per question: word search runs one SQL statement; sparse search"
        " also calls the model once, so it needs the BGE-M3 server running.",
        "",
        "## Latency",
        "",
        "Wall time per case, from the question to the ranked Candidates. It"
        " changes from run to run.",
        "",
        f"- Index build: {comparison.index_seconds:.1f} s.",
        "",
        "| Run | Median ms | Slowest ms |",
        "|---|---:|---:|",
    ]
    for report in reports:
        times = [outcome.elapsed_seconds * 1000 for outcome in report.outcomes]
        median = statistics.median(times) if times else 0.0
        slowest = max(times, default=0.0)
        lines.append(f"| {_label(report)} | {median:.1f} | {slowest:.1f} |")
    return render_comparison(reports) + "\n".join(lines) + "\n"

```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
            f" {model.dimensions} dimensions."
        )
```

Замініть на:

```python
            f" {model.dimensions} dimensions."
        )
    sparse = next((r.sparse for r in reports if r.sparse is not None), None)
    if sparse is not None:
        lines.append(
            f"- Sparse model: {sparse.provider} {sparse.model},"
            f" vocabulary of {sparse.vocabulary_size} tokens."
        )
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
) -> DatasetReport:
    outcomes = [await _run_case(case, searches, limit) for case in dataset.cases]
    return DatasetReport(
        name=dataset.name,
        version=dataset.version,
        limit=limit,
        branches=tuple(branch for branch in RetrievalBranch if branch in searches),
        outcomes=tuple(outcomes),
        embedding=embedding if RetrievalBranch.DENSE in searches else None,
    )


async def _run_case(
    case: EvaluationCase,
    searches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
) -> CaseOutcome:
    result: HybridRetrieval | None = None
    unexpected = "no result"
```

Замініть на:

```python
    sparse: SparseModelIdentity | None = None,
) -> DatasetReport:
    outcomes = [await _run_case(case, searches, limit) for case in dataset.cases]
    return DatasetReport(
        name=dataset.name,
        version=dataset.version,
        limit=limit,
        branches=tuple(branch for branch in RetrievalBranch if branch in searches),
        outcomes=tuple(outcomes),
        embedding=embedding if RetrievalBranch.DENSE in searches else None,
        sparse=sparse if RetrievalBranch.SPARSE in searches else None,
    )


async def _run_case(
    case: EvaluationCase,
    searches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
) -> CaseOutcome:
    result: HybridRetrieval | None = None
    unexpected = "no result"
    started = time.perf_counter()
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
        unexpected = _error_names(error)
    # Raised outside the `except`, so no linked error carries the Question.
```

Замініть на:

```python
        unexpected = _error_names(error)
    elapsed = time.perf_counter() - started
    # Raised outside the `except`, so no linked error carries the Question.
```

Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
        degraded=result.degraded,
    )
```

Замініть на:

```python
        degraded=result.degraded,
        elapsed_seconds=elapsed,
    )
```

У `scripts/run_retrieval_dataset.py`:

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
text leaves the machine.
"""
```

Замініть на:

```python
text leaves the machine.

`--sparse` is the M05-T10 prototype. It asks the same local server for
BGE-M3's sparse term weights, runs every case with PostgreSQL word search
and with sparse search, and writes quality, cost, and latency to
`artifacts/evaluations/bilingual-seed-sparse.md`.
"""
```

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
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
```

Замініть на:

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

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
    embeddings.add_argument("--local-embeddings", action="store_true")
    arguments = parser.parse_args()
```

Замініть на:

```python
    embeddings.add_argument("--local-embeddings", action="store_true")
    embeddings.add_argument("--sparse", action="store_true")
    arguments = parser.parse_args()
```

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
        rendered = asyncio.run(_run(runtime, dataset, corpus, arguments.limit, source))
```

Замініть на:

```python
        if arguments.sparse:
            default = SPARSE_REPORT
            rendered = asyncio.run(
                _run_sparse(runtime, dataset, corpus, arguments.limit)
            )
        else:
            rendered = asyncio.run(
                _run(runtime, dataset, corpus, arguments.limit, source)
            )
```

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python

def _openai_model(api_key: SecretStr) -> ModelSource:
```

Замініть на:

```python

async def _run_sparse(
    runtime: DatabaseRuntime, dataset: EvaluationDataset, corpus: Path, limit: int
) -> str:
    try:
        async with _local_model() as model:
            comparison = await compare_sparse(
                runtime,
                dataset,
                corpus,
                observed_at=datetime.now(UTC),
                model=model,
                limit=limit,
            )
        return render_sparse_comparison(comparison)
    finally:
        await runtime.dispose()


def _openai_model(api_key: SecretStr) -> ModelSource:
```

Знайдіть у `scripts/run_retrieval_dataset.py`:

```python
async def _local_model() -> AsyncGenerator[EmbeddingModel]:
```

Замініть на:

```python
async def _local_model() -> AsyncGenerator[LocalEmbeddingModel]:
```

Перевірка: `make typecheck` показує `0 errors`, а `uv run python scripts/run_retrieval_dataset.py --sparse --managed-embeddings` друкує `not allowed with argument`.

### Крок 8 — Крок з кодом: протестуйте пошук і порівняння

Цей крок доводить обіцянки пошуку: частину без бази, решту на справжній PostgreSQL.

**Простими словами:** Перший файл перевіряє все, що має бути відхилено ще до звернення до PostgreSQL: поганий ліміт, ваги від іншої моделі, зламаний запит чи індекс і вагу, завелику, щоб безпечно додавати. Його підробне з'єднання валить тест, якщо його використали. Другий файл проганяє все порівняння з підробною розрідженою моделлю, а потім будує найнебезпечніший випадок: запитання, яке найкраще збігається із закритим Chunk. Без перевірки прав цей Chunk іде першим; з нею він ніколи не з'являється. Ще він перевіряє, що збій моделі під час індексування зупиняє прогін типізованою тихою помилкою.

| Тест | Що доводить |
|---|---|
| `test_the_index_keeps_postings_by_token_in_chunk_order` | покажчик і кількості |
| `test_a_bad_limit_is_refused_before_the_database`, `test_weights_from_another_model_are_refused_before_the_database`, `test_a_malformed_query_is_refused_before_the_database` | типізовані відмови до будь-якого читання |
| `test_the_index_refuses_weights_the_port_does_not_allow`, `test_a_weight_too_large_to_multiply_safely_is_refused` | NaN, від'ємні, величезні, невпорядковані чи позасловникові ваги не потрапляють ні в індекс, ні в суму |
| `test_every_case_runs_with_words_and_with_sparse_weights_quietly` | успіх: обидва прогони, пачки в межах обмежень, жодного тексту запитань чи закритого тексту у звіті й логах |
| `test_the_best_scored_chunk_is_restricted_and_still_never_returned` | найризикованіший випадок |
| `test_a_model_that_fails_while_indexing_stops_the_run_quietly` | типізована помилка: `CorpusNotEmbedded` без пов'язаної помилки |

Створіть `tests/test_sparse_search.py`:

```python
"""Sparse search without a database: the index, and refusals before any read."""

from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from fakes import FAKE_VOCABULARY
from knowledge_service.access import AuthorizationContext
from knowledge_service.embeddings.port import EmbeddingMalformedResponse
from knowledge_service.embeddings.sparse import (
    SparseModelIdentity,
    SparseVector,
    checked_sparse_vectors,
)
from knowledge_service.identifiers import ChunkId, UserId
from knowledge_service.retrieval.sparse_search import (
    InvalidSparseQuery,
    InvalidSparseWeights,
    SparseIndexMismatch,
    search_sparse_chunks,
    sparse_index,
)

IDENTITY = SparseModelIdentity(
    provider="fake", model="fake-sparse", vocabulary_size=FAKE_VOCABULARY
)
READER = AuthorizationContext(user_id=UserId(UUID(int=1)))
FIRST = ChunkId(UUID(int=1))
SECOND = ChunkId(UUID(int=2))


class NoDatabase:
    """Fails the test if a search touches the database."""

    async def execute(self, *_: object) -> None:
        raise AssertionError("the database was used")


NO_DATABASE = cast(AsyncConnection, NoDatabase())


def test_the_index_keeps_postings_by_token_in_chunk_order() -> None:
    index = sparse_index(
        IDENTITY,
        {SECOND: ((5, 0.5), (9, 0.25)), FIRST: ((5, 1.0),)},
    )

    assert index.postings == {5: ((FIRST, 1.0), (SECOND, 0.5)), 9: ((SECOND, 0.25),)}
    assert (index.chunks, index.weights) == (2, 3)


def test_valid_sparse_weights_become_pairs_and_may_be_empty() -> None:
    vectors = checked_sparse_vectors(
        [[[3, 0.5], [7, 1.5]], []], expected=2, vocabulary_size=10
    )

    assert vectors == (((3, 0.5), (7, 1.5)), ())


def test_a_wrong_number_of_sparse_vectors_is_refused() -> None:
    with pytest.raises(EmbeddingMalformedResponse):
        checked_sparse_vectors([[[3, 0.5]]], expected=2, vocabulary_size=10)


@pytest.mark.parametrize(
    "limit", [0, 101, True, 1.0], ids=["zero", "too many", "bool", "float"]
)
async def test_a_bad_limit_is_refused_before_the_database(limit: object) -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})

    with pytest.raises(InvalidSparseQuery):
        await search_sparse_chunks(
            NO_DATABASE,
            index=index,
            query=((5, 1.0),),
            identity=IDENTITY,
            context=READER,
            limit=cast(int, limit),
        )


async def test_weights_from_another_model_are_refused_before_the_database() -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})
    other = SparseModelIdentity(
        provider="local", model="BAAI/bge-m3@other", vocabulary_size=FAKE_VOCABULARY
    )

    with pytest.raises(SparseIndexMismatch):
        await search_sparse_chunks(
            NO_DATABASE,
            index=index,
            query=((5, 1.0),),
            identity=other,
            context=READER,
            limit=10,
        )


@pytest.mark.parametrize(
    "query",
    [
        [(5, 1.0)],
        ((5, -1.0),),
        ((5, float("nan")),),
        ((FAKE_VOCABULARY, 1.0),),
        ((5, 1.0), (5, 2.0)),
        ((5, 1),),
        ((5,),),
    ],
    ids=[
        "a list",
        "negative weight",
        "not a number",
        "outside the vocabulary",
        "repeated token",
        "int weight",
        "no weight",
    ],
)
async def test_a_malformed_query_is_refused_before_the_database(
    query: object,
) -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})

    with pytest.raises(InvalidSparseQuery):
        await search_sparse_chunks(
            NO_DATABASE,
            index=index,
            query=cast(SparseVector, query),
            identity=IDENTITY,
            context=READER,
            limit=10,
        )


async def test_a_query_sharing_no_token_finds_nothing_without_the_database() -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})

    result = await search_sparse_chunks(
        NO_DATABASE,
        index=index,
        query=((6, 1.0),),
        identity=IDENTITY,
        context=READER,
        limit=10,
    )

    assert (result.candidates, result.truncated) == ((), False)


@pytest.mark.parametrize(
    "vector",
    [
        ((5, float("nan")),),
        ((5, -3.0),),
        ((5, 1e300),),
        ((FAKE_VOCABULARY, 1.0),),
        ((9, 1.0), (5, 1.0)),
    ],
    ids=["not a number", "negative", "huge", "outside the vocabulary", "unordered"],
)
def test_the_index_refuses_weights_the_port_does_not_allow(vector: object) -> None:
    with pytest.raises(InvalidSparseWeights):
        sparse_index(IDENTITY, {FIRST: cast(SparseVector, vector)})


async def test_a_weight_too_large_to_multiply_safely_is_refused() -> None:
    index = sparse_index(IDENTITY, {FIRST: ((5, 1.0),)})

    with pytest.raises(InvalidSparseQuery):
        await search_sparse_chunks(
            NO_DATABASE,
            index=index,
            query=((5, 1.7e308),),
            identity=IDENTITY,
            context=READER,
            limit=10,
        )


def test_a_weight_over_the_bound_is_malformed() -> None:
    with pytest.raises(EmbeddingMalformedResponse):
        checked_sparse_vectors([[[3, 1e6]]], expected=1, vocabulary_size=10)
```

Створіть `tests/test_sparse_search_integration.py`:

```python
"""The sparse prototype on real PostgreSQL: the seed run, eligibility, failure."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from fakes import FakeSparseModel
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import EmbeddingLimits, EmbeddingUnavailable
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    READER,
    SOURCE_IDS,
    CorpusNotEmbedded,
    EvaluationDataset,
    Folder,
    compare_sparse,
    load_dataset,
    render_sparse_comparison,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.sparse_search import (
    build_sparse_index,
    search_sparse_chunks,
    sparse_search_statement,
)
from knowledge_service.settings import Settings

OBSERVED_AT = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
REQUEST_ID = UUID(int=0x5EED_0010)
# Small batches, so indexing the 21 seed Chunks takes several requests.
LIMITS = EmbeddingLimits(max_inputs=4, max_input_tokens=1_000, max_batch_tokens=4_000)


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
async def test_every_case_runs_with_words_and_with_sparse_weights_quietly(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model = FakeSparseModel(limits=LIMITS)

    comparison = await compare_sparse(
        runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model
    )

    assert comparison.lexical.branches == (RetrievalBranch.LEXICAL,)
    assert comparison.sparse.branches == (RetrievalBranch.SPARSE,)
    assert comparison.sparse.sparse == model.sparse_identity
    assert comparison.chunks == 21
    assert comparison.weights > 0
    # Every Chunk was indexed, in batches the model's limits allow.
    assert sum(len(batch) for batch in model.batches) == 21 + len(dataset.cases)
    assert all(len(batch) <= LIMITS.max_inputs for batch in model.batches)
    sparse = {o.case_id: o.ranks for o in comparison.sparse.outcomes}
    # The fake shares a token per shared word, so a code is found by itself.
    assert sparse["en-en-error-vpn-4031"] == (1,)
    assert all(o.elapsed_seconds > 0 for o in comparison.sparse.outcomes)
    rendered = render_sparse_comparison(comparison)
    assert "## Cost" in rendered
    assert "## Latency" in rendered
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in dataset.cases)


@pytest.mark.integration
async def test_the_best_scored_chunk_is_restricted_and_still_never_returned(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    model = FakeSparseModel(limits=LIMITS)
    await compare_sparse(runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model)
    index = await build_sparse_index(
        runtime, model, request_id=REQUEST_ID, timeout_seconds=5.0
    )
    async with runtime.transaction() as connection:
        restricted_text = await connection.scalar(
            sa.select(chunks.c.text)
            .select_from(chunks.join(documents))
            .where(documents.c.source_id == SOURCE_IDS[Folder.RESTRICTED].value)
            .order_by(chunks.c.ordinal)
            .limit(1)
        )
    assert restricted_text is not None
    # The query is the restricted Chunk's own text: every one of its tokens
    # matches, so no other Chunk can score higher.
    (query,) = await model.encode_sparse(
        [restricted_text], request_id=REQUEST_ID, timeout_seconds=5.0
    )
    scores = {
        chunk_id: sum(
            weight * dict(index.postings.get(token, ())).get(chunk_id, 0.0)
            for token, weight in query
        )
        for chunk_id in {c for pairs in index.postings.values() for c, _ in pairs}
    }
    scores = {chunk_id: score for chunk_id, score in scores.items() if score > 0}

    async with runtime.transaction() as connection:
        unfiltered = (
            (
                await connection.execute(
                    sparse_search_statement(
                        scores=scores, eligibility=sa.true(), limit=1
                    )
                )
            )
            .mappings()
            .one()
        )
        found = await search_sparse_chunks(
            connection,
            index=index,
            query=query,
            identity=model.sparse_identity,
            context=READER,
            limit=21,
        )

    # Without the permission check the restricted Chunk ranks first ...
    assert unfiltered["source_id"] == SOURCE_IDS[Folder.RESTRICTED].value
    # ... with it, no restricted Chunk is ranked, counted, or returned.
    assert found.candidates
    assert {c.source_id for c in found.candidates} == {SOURCE_IDS[Folder.PUBLIC]}
    assert [c.rank for c in found.candidates] == list(
        range(1, len(found.candidates) + 1)
    )
    assert found.truncated is False
    assert all(RESTRICTED_MARKER not in c.text for c in found.candidates)


@pytest.mark.integration
async def test_a_model_that_fails_while_indexing_stops_the_run_quietly(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    model = FakeSparseModel(limits=LIMITS, error=EmbeddingUnavailable())

    with pytest.raises(CorpusNotEmbedded) as raised:
        await compare_sparse(
            runtime, dataset, CORPUS, observed_at=OBSERVED_AT, model=model
        )

    assert "EmbeddingUnavailable" in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    # One batch was tried, and no case ran.
    assert len(model.batches) == 1
```

Перевірка, спершу вузькі тести:

```bash
uv run pytest tests/test_sparse_search.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_sparse_search_integration.py -q
```

Очікувано: `23 passed`, потім `3 passed`.

### Крок 9 — Без коду: запустіть, порівняйте і вирішіть

Цей крок запускає справжню модель, перевіряє, що прогін повторюється, навмисно ламає перевірку прав і просить вас ухвалити рішення.

**Дія 1:** запустіть сервер в одному терміналі й лишіть його працювати:

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py
```

Якщо порт 8765 зайнятий, допишіть попереду `BGE_M3_PORT=8766`, а в другому терміналі спершу виконайте `export LOCAL_EMBEDDING_URL=http://127.0.0.1:8766`. Це налаштування читається з оболонки, не з `.env`.

**Дія 2:** у другому терміналі запустіть двічі на свіжих базах і порівняйте все, що вище «## Latency»:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_dataset#')"
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --sparse
cp artifacts/evaluations/bilingual-seed-sparse.md /tmp/first-sparse.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --sparse > /dev/null
diff <(sed '/^## Latency/,$d' /tmp/first-sparse.md) <(sed '/^## Latency/,$d' artifacts/evaluations/bilingual-seed-sparse.md) && echo same
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
unset KNOWLEDGE_SERVICE_DATABASE_URL
```

Коли модель завантажена, кожен прогін триває кілька секунд.

**Дія 3:** прочитайте звіт і запишіть рішення. У файлі доказів, у розділі «Decision», запишіть:

- таблицю за зрізами зі звіту (слова і розріджений пошук, Recall@10 і MRR), бо `artifacts/` Git ігнорує;
- розділи «Cost» і «Latency»;
- **keep** або **reject** одним реченням і три короткі причини: якість (які зрізи, які цифри), затримка й вартість, і стійкість (що буде, коли сервер моделі лежить);
- що означало б «залишити» далі, якщо ви залишаєте: наприклад, зберігати ваги як pgvector `sparsevec` за міграцією або просити в сервера щільний і розріджений вихід за один прохід.

Також запишіть щонайменше одне **спростоване припущення** у формі «Я припускав X. Звіт показує Y (ідентифікатори випадків і цифри). Отже, Z хибне».

**Дія 4:** навмисно зламайте перевірку прав. У `src/knowledge_service/retrieval/sparse_search.py`, у `search_sparse_chunks`, змініть `eligibility=eligible_chunks_predicate(context),` на `eligibility=sa.true(),`. Потім запустіть:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_sparse_search_integration.py -q
```

Очікувано: 2 failed, `test_every_case_runs_with_words_and_with_sparse_weights_quietly` і `test_the_best_scored_chunk_is_restricted_and_still_never_returned`. Обидва зупиняються з `IneligibleChunkRetrieved`: саме порівняння помічає перший закритий Chunk, що повернувся. Скасуйте зміну і запускайте знову, доки все не пройде. Зупиніть сервер через Ctrl+C.

### Крок 10 — Без коду: перевірки, документація, докази і прогрес

Цей крок доводить, що все й далі працює, оновлює текст архітектури, який зміна зробила неповним, і записує результат.

**Дія 1:** у `docs/architecture.md`:

Знайдіть у `docs/architecture.md`:

```markdown
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
own uv project (ADR 0009).
`ml/bge_m3_server/` serves BGE-M3 dense embeddings on Python 3.12, on
127.0.0.1 only. The application reaches it through `LocalEmbeddingModel`,
```

Замініть на:

```markdown
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
own uv project (ADR 0009).
`ml/bge_m3_server/` serves BGE-M3 dense embeddings, and for the M05-T10
prototype its sparse term weights, on Python 3.12, on 127.0.0.1 only. The application reaches it through `LocalEmbeddingModel`,
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_sparse_search.py tests/test_local_embeddings.py tests/test_embedding_model_contract.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make ml-check
make docs-check
```

Очікувано: усе проходить. Еталонний прогін дав 91 passed (1 deselected) для вузьких тестів, `make test-unit` — 1278 passed, `make test-integration` — 303 passed, а `make ml-check` — 29 passed.

**Дія 3:** допишіть файл доказів:

- **Design:** змінені файли; чому розріджений вихід BGE-M3, а не окрема пошукова система; чому індекс живе в пам'яті; як перевірка прав лишається в тому самому запиті; чому кожна вага перевіряється й обмежена.
- **Verification:** команди і кількості, результат повторюваності, навмисне зламування і відомі обмеження з «Перш ніж почати».
- **Review:** ворожий рев'ю прототипу не знайшов жодної порушеної гарантії і знайшов сім менших проблем. Чотири виправлено, кожну з регресійним тестом, що падав до виправлення: ваги до 1.7e308 у сумі давали нескінченність і зрівнювали всі Chunks, тож тепер вага не більша за 1000; індекс довіряв векторам моделі, тож тепер перевіряє їх, як запит; одна відповідь могла принести 250 002 ваги для тексту з одного слова, тож тепер текст не може мати більше ваг, ніж токенів; стиснута відповідь на 1 МБ під час розпакування займала близько 145 МБ пам'яті ще до перевірки обмеження у 8 МБ, тож стиснуті відповіді тепер відхиляються непрочитаними. Три — відомі обмеження: помилка розрідженої гілки пробивається нагору, а не робить гібридний пошук частковим, пачка доповнює кожен текст до найдовшого, а запис логу розрідженого запиту називає щільну ідентичність.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте завдання виконаним, додайте рядок M05-T10 у `docs/curriculum/PROGRESS.md`, поставте наступним завданням M05-T11 і зробіть коміт `m05-t10: compare a multilingual sparse path`.

## Запитання для роздумів

1. Що `search_sparse_chunks` ховає від коду, який її викликає? Перелічіть, про що викликачеві ніколи не треба думати: наприклад, покажчик за токенами, таблиця `unnest` чи порядок, у якому додаються бали.
2. Хтось згодом пришвидшує пошук: ранжує в Python і просить у PostgreSQL лише текст десяти найкращих. Що піде не так із закритим Chunk і з `truncated`?
3. Ваги перевіряються двічі: в адаптері і ще раз під час побудови індексу. Яку помилку чи атаку зупиняє друга перевірка, а перша — ні?
4. Яке з ваших передбачень виявилося хибним і що ви думали про пошук за словами чи про BGE-M3, через що так передбачили?
5. Якщо ви залишаєте розріджений пошук, що мало б стати правдою, перш ніж він замінить пошук за словами PostgreSQL для справжніх користувачів? Назвіть одну метрику й один випадок збою, який треба перевірити.

## Чекліст завершення

- [ ] Файл доказів містить інваріант і ваші передбачення, записані до будь-якого прогону.
- [ ] Сервер закріплює `sparse_linear.pt`, обслуговує `/v1/sparse` з тими самими обмеженнями і відхиляє погані ваги; `make ml-check` проходить.
- [ ] `SparseModel` і `checked_sparse_vectors` існують, а `LocalEmbeddingModel.encode_sparse` відхиляє чуже походження, погані ваги, забагато ваг і стиснуті відповіді.
- [ ] `search_sparse_chunks` тримає перевірку прав у тому самому SQL-запиті, що й ранжування; закритий найкращий збіг ніколи не повертається.
- [ ] Поганий ліміт, інші моделі і погані ваги відхиляються ще до звернення до PostgreSQL.
- [ ] Підкинутий текст не з'являється в помилках, пов'язаних помилках, логах, відповідях сервера чи звітах.
- [ ] Ви запустили `--sparse` двічі на свіжих базах, і все вище «## Latency» збіглося.
- [ ] Файл доказів містить таблицю за зрізами, вартість і затримку, рішення «залишити» чи «відкинути» з причинами і спростоване припущення.
- [ ] Ви виконали навмисне зламування з кроку 9, побачили два впалі тести і скасували його.
- [ ] Усі перевірки проходять: форматування, lint, типи, юніт-, інтеграційні, ML-тести, документація.
- [ ] Текст архітектури, докази, рядок у PROGRESS і коміт зроблено.

Коли напишете файли, поділіться ними або виводом помилок, і я перевірю їх за цим чеклістом.
