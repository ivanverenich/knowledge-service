# M07-T11 — Усунення витоків існування документів

Англійська версія: [M07-T11-eliminate-existence-leaks.md](M07-T11-eliminate-existence-leaks.md)

Задача: [M07-T11 — Eliminate existence leaks](../curriculum/milestones/M07-identity-and-authorization/M07-T11-eliminate-existence-leaks.md)

## Що ви зробите

Цей урок перевіряє, що Document, який читачеві не можна бачити, для цього читача виглядає рівно як Document, якого не існує: не лише його текст, а кожен слід, який він міг би залишити. Система може ховати вміст документа і все одно видавати його існування: через іншу помилку, лічильник, більший на одиницю, відповідь, що триває довше, чи рядок у лозі, що з'являється лише тоді, коли документ є. Кожне з цього — витік існування.

- **Порівняння «чорною скринькою»:** тести двічі спостерігають усе, що видно поза сервером, — один раз без Document іншого User, другий раз із ним як найкращим збігом — і вимагають, щоб обидва спостереження були рівні: байти відповіді, заголовки, кожен запис логу з будь-якого логера і промпт моделі.
- **Чужі ID як відсутні ID:** справжній ID Chunk, Evidence чи Conversation, що належить комусь іншому, дає рівно той самий результат і лог, що й ID, якого ніколи не було.
- **Таймінг:** векторний пошук працював приблизно на 10 мс довше, коли біля питання лежали 5000 прихованих Chunks. Тепер він спершу визначає Chunks читача, а вже потім ранжує, тож приховані нічого не коштують; структурний тест це закріплює.
- **Що лишається, записане:** лексичний пошук має ту саму різницю в часі, але ще не доходить до читача; threat model записує її з цифрами для M09.

| Задача вимагає | Де це відбувається |
|---|---|
| Unauthorized and absent Documents indistinguishable across errors and counts | Крок 4: `tests/test_existence_leaks_integration.py` |
| Across timing-sensitive paths | Кроки 2 і 3: векторний пошук ранжує лише Chunks читача; лексичний записано як обмеження в кроці 6 |
| Across model context | Крок 4: промпт однаковий із прихованим Document і без нього |
| Black-box tests compare responses for missing and forbidden identifiers | Крок 4 |
| No seeded sensitive text in telemetry | Крок 4: порівнюється кожен запис логу з будь-якого логера |
| Update only what the change made inaccurate | Крок 6: `docs/architecture.md`, `docs/threat-model.md` |

У цьому уроці є код: кроки 2–4 пишуть код і тести. Кроки 1, 5 і 6 без коду, хоча крок 6 змінює два документи.

Терміни цього уроку:

- **Витік існування (existence leak):** будь-яка різниця, яку читач може помітити між «існує документ, якого мені не можна бачити» і «такого документа немає».
- **Побічний канал (side channel):** інформація, що витікає через те, як система поводиться, наприклад через час чи розмір, а не через те, що вона повертає.
- **Тест «чорною скринькою» (black-box test):** тест, що дивиться лише на те, що видно ззовні, — тут відповідь, логи й промпт, — і ніколи на внутрішній стан.
- **Маскування:** заміна значень, які й так змінюються з кожним запитом, як-от ID запиту чи непрозорий ID Evidence, щоб два спостереження можна було порівняти точно.
- **Матеріалізований крок (materialized step):** частина запиту, яку PostgreSQL обчислює один раз окремо, перш ніж нею скористається решта. Як скласти список гостей до того, як когось садити, а не перевіряти кожного гостя, лише коли він дійде до місця.

## Перш ніж почати

Цей розділ пояснює рішення за кодом, щоб кожен крок був зрозумілим, коли ви до нього дійдете.

- **Більшість каналів уже були закриті.** Попередні уроки поставили перевірку доступу всередину кожного запиту (M07-T07, M07-T08), зробили так, що відкликана й порожня відповіді закінчуються однаково (M07-T09), і лишили в логах лише кількості. Тести цього уроку підтверджують це ззовні: коли є Document іншого User, що точно збігається з питанням, потік, логи й промпт не змінюються ні на байт.
- **Порівнювати все, а не вибране.** Перша версія порівнювала чотири поля логу з одного логера; рев'ю підкинув витоки в інші місця, і вони пройшли. Тепер порівнюється кожен запис логу з кожного логера з усіма його додатковими полями, а результати порівнюються цілком, разом із reason і usage.
- **Витік через таймінг.** Векторний запит не мав індексу, тож PostgreSQL сортував усі збережені вектори за відстанню, а потім перевіряв доступ рядок за рядком, від найближчого, доки не набере потрібну кількість. Кожен прихований Chunk, ближчий за результати читача, коштував ще однієї перевірки: з 5000 таких пошук стабільно тривав приблизно на 10 мс довше. Це каже читачеві, що біля його питання щось приховано.
- **Виправлення: спершу вирішити, хто що може читати.** Тепер запит обчислює Chunks читача в кроці `MATERIALIZED`, який PostgreSQL обчислює окремо і ніколи не зливає з ранжуванням, і лише потім ранжує їх. З 5000 прихованими точними збігами різниця впала до рівня шуму. Ціна: читач, який бачить 20 000 Documents, тепер чекає приблизно 50 мс замість 24 мс, бо кожен видимий Chunk перевіряється один раз; читач, який бачить мало Documents, чекає приблизно 3 мс, як і раніше. Цей обмін обрано свідомо.
- **Чому структурний тест, а не тест часу.** Час у тестах нестабільний, а з малим корпусом PostgreSQL і так починає з грантів, тож тест часу проходив і на старому коді. Структурний тест читає скомпільований SQL і вимагає, щоб гранти були всередині матеріалізованого кроку, а відстань — поза ним; саме це робить роботу незалежною від прихованих Chunks.
- **Чому лексичний пошук лише записано.** Те саме виправлення не дало б лексичному пошуку користуватися швидким індексом слів і сповільнило б кожен пошук для читачів, які бачать багато Documents. Лексичний пошук ще не доходить до читача, тож threat model записує різницю — приблизно 5–28 мс на 5000 прихованих збігів — щоб виправити чи обмежити її до того, як M09 віддасть його читачам.
- **Що знайшов рев'ю.** Незалежний атакувальний рев'ю порівнював байти потоку, промпти, логи, `truncated`, кількість доступних, діагностику гібрида, шляхи помилок і чужі, видалені та відсутні ID, а також вимірював час із до 20 000 видимих і 5000 прихованих Documents разом із планами запитів. Він знайшов:
  - описаний вище витік через таймінг у векторному пошуку — тепер виправлений і закріплений структурним тестом;
  - витік через таймінг у лексичному пошуку — записаний із цифрами;
  - що дедлайн гілки гібрида перетворив би лексичну різницю на чітку «гілка не встигла» — записано для M09;
  - чотири з шести підкинутих витоків пережили перші тести: лічильник в іншому полі логу, затримка, інший `reason` і заголовок в іншому логері; тепер порівняння логів і результатів ловить ті, що не про час;
  - що `rate_message` і `load_message_feedback` читають за ID Message без перевірки власника — сьогодні недосяжні, записано як прогалину.
- **Відомі обмеження.** Час лексичного і sparse-пошуку досі залежить від прихованих збігів. Векторний пошук повільніший для читачів, які бачать дуже багато Documents. Оцінювання Messages потребує перевірки власника, перш ніж будь-який маршрут його відкриє.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої прогнози

Перш ніж щось змінювати, запишіть, що має бути правдою наприкінці уроку, і що ви очікуєте. Крок 5 перевіряє останній прогноз.

Створіть `docs/evidence/M07-T11-eliminate-existence-leaks.md`:

```markdown
# M07-T11 — Eliminate existence leaks evidence

## Invariant

To a reader, a Document they may not see is indistinguishable from one that
does not exist: the answer stream's bytes and headers, every log record, and
the model's prompt are the same with or without another User's best-matching
Document, and a forbidden Chunk, Evidence, or Conversation ID gives the same
result and log as a missing one. Vector search settles the reader's Chunks
before it ranks any, so its work does not grow with hidden Chunks near the
question. Lexical search's timing gap is recorded, with numbers, as a known
limit until it serves a reader.

## Expected failing check

Before the change, `uv run pytest -m integration tests/test_existence_leaks_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_existence_leaks_integration.py`.

## Predictions

- Whether alice's answer stream, the logs, or the prompt change when bob's exact-match Document is added:
- What `confirm_citations` returns for a Citation of bob's real Chunk and for one of an invented Chunk:
- How long a vector search takes for a reader who sees 20,000 Documents, with and without 5,000 hidden exact matches, before and after the change:
- Which tests fail if `MATERIALIZED` is removed from the vector query, and which keep passing:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після двокрапки в кожному прогнозі є ваша відповідь.

### Крок 2 — Крок із кодом: ранжувати лише Chunks читача

**Простими словами:** цей крок змінює порядок роботи векторного пошуку. Раніше база могла вишикувати всі збережені вектори за відстанню, а потім по одному перевіряти, чи може читач їх бачити, тож приховані Chunks біля питання коштували додаткової роботи. Тепер вона спершу складає повний список Chunks, які читачеві можна, і лише потім сортує їх за відстанню. Результати ті самі; лише робота, а отже й час, більше не залежить від прихованих Chunks.

| Зміна | Що робить |
|---|---|
| Docstring модуля | Каже, що робота не залежить від прихованих Chunks, і чому |
| `visible` | Common table expression `MATERIALIZED`: Chunks, що відповідають `eligibility` і `restriction` |
| Ранжування | Приєднує `visible` і ранжує лише їх; перевірка доступу більше не стоїть поруч з `ORDER BY` |

У `src/knowledge_service/retrieval/vector_search.py`:

**1.** Знайдіть:

```python
- **Filters.** Optional `filters` (see `retrieval.filters`) narrow the
  eligible Chunks further, in the same WHERE, so every guarantee above,
  `truncated` included, counts only Chunks that are eligible and match.
- **Empty.** No eligible Chunk is a normal, empty result, not an error.
  A Chunk whose stored vector has no direction (all zeros) has no distance,
  so it is never a Candidate.

Every stored vector and every query has length 1, so distances are right
in pgvector's 32-bit floats. Ties are exact: two vectors in the same
direction can still differ in the last digits of their distances, and are
then ordered by those digits, not by Chunk ID.
"""
```

Замініть на:

```python
- **Filters.** Optional `filters` (see `retrieval.filters`) narrow the
  eligible Chunks further, in the same WHERE, so every guarantee above,
  `truncated` included, counts only Chunks that are eligible and match.
- **Empty.** No eligible Chunk is a normal, empty result, not an error.
  A Chunk whose stored vector has no direction (all zeros) has no distance,
  so it is never a Candidate.

The work does not depend on Chunks the reader may not see. The statement
first settles every Chunk the reader may see, in a materialized step the
database cannot reorder, and only then ranks those by distance. If it
ranked first and checked permissions row by row, as a planner may choose,
every hidden Chunk nearer than the reader's results would cost one more
check, and the time of a search would tell a reader how many hidden
Chunks are close to their question.

Every stored vector and every query has length 1, so distances are right
in pgvector's 32-bit floats. Ties are exact: two vectors in the same
direction can still differ in the last digits of their distances, and are
then ordered by those digits, not by Chunk ID.
"""
```

**2.** Знайдіть:

```python
    eligibility: sa.ColumnElement[bool],
    restriction: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    """Select the nearest Chunks of one space that meet `eligibility`.

    The predicate sits in the same WHERE as the space, so PostgreSQL applies
    it before ORDER BY and LIMIT. The space must still be current when the
    statement runs, so it never reads a space a migration has just switched
    away from. Ties in distance are broken by Chunk ID so
    that equal inputs always return the same order. A stored vector with no
    direction gives a NaN distance, which is not a distance, so its row is
    left out; PostgreSQL treats NaN as equal to NaN.
    """
    distance = chunk_embeddings.c.embedding.cosine_distance(list(query))
    return (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            documents.c.source_id,
```

Замініть на:

```python
    eligibility: sa.ColumnElement[bool],
    restriction: sa.ColumnElement[bool],
    limit: int,
) -> sa.Select[*tuple[Any, ...]]:
    """Select the nearest Chunks of one space that meet `eligibility`.

    The Chunks that meet `eligibility` and `restriction` are chosen first,
    in a `MATERIALIZED` common table expression, which PostgreSQL computes
    once and never merges into the ranking; only those are then ordered by
    distance and cut by the limit. So no Chunk the reader may not see is
    ranked, and the work does not grow with them. The space must still be
    current when the statement runs, so it never reads a space a migration
    has just switched away from. Ties in distance are broken by Chunk ID so
    that equal inputs always return the same order. A stored vector with no
    direction gives a NaN distance, which is not a distance, so its row is
    left out; PostgreSQL treats NaN as equal to NaN.
    """
    visible = (
        sa.select(chunks.c.chunk_id)
        .select_from(chunks)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(eligibility, restriction)
        .cte("visible")
        .prefix_with("MATERIALIZED")
    )
    distance = chunk_embeddings.c.embedding.cosine_distance(list(query))
    return (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            documents.c.source_id,
```

**3.** Знайдіть:

```python
            documents.c.title,
            chunks.c.heading_path,
            chunks.c.source_anchor,
            chunks.c.text,
        )
        .select_from(chunk_embeddings)
        .join(chunks, chunks.c.chunk_id == chunk_embeddings.c.chunk_id)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(
            chunk_embeddings.c.space_id == space_id.value,
            sa.exists().where(
                embedding_spaces.c.space_id == space_id.value,
                embedding_spaces.c.is_current,
            ),
            eligibility,
            restriction,
            distance != float("nan"),
        )
        .order_by(distance, chunks.c.chunk_id)
        .limit(limit)
    )

```

Замініть на:

```python
            documents.c.title,
            chunks.c.heading_path,
            chunks.c.source_anchor,
            chunks.c.text,
        )
        .select_from(chunk_embeddings)
        .join(visible, visible.c.chunk_id == chunk_embeddings.c.chunk_id)
        .join(chunks, chunks.c.chunk_id == chunk_embeddings.c.chunk_id)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(
            chunk_embeddings.c.space_id == space_id.value,
            sa.exists().where(
                embedding_spaces.c.space_id == space_id.value,
                embedding_spaces.c.is_current,
            ),
            distance != float("nan"),
        )
        .order_by(distance, chunks.c.chunk_id)
        .limit(limit)
    )

```

`MATERIALIZED` важливий: без нього PostgreSQL може «згорнути» common table expression назад у головний запит і знову обрати старий порядок.

**Перевірка:** тести векторного пошуку досі проходять проти PostgreSQL:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_vector_search_integration.py -q
```

Очікувано: `43 passed`, без змін.

### Крок 3 — Крок із кодом: закріпити порядок у SQL

**Простими словами:** цей тест будує векторний запит, не запускаючи його, і читає його текст. Він перевіряє, що гранти читаються всередині матеріалізованого кроку, що відстань рахується лише після нього і що ранжування приєднує результат цього кроку. Тести часу нестабільні; цей — точний.

У `tests/test_vector_search.py`:

Знайдіть:

```python
    assert [GROUP.value] in values
    assert [INJECTED_MEDIA_TYPE] in values
    assert ["uk"] in values
    assert [DOCUMENT.value] in values


def test_a_candidate_repr_shows_no_document_text() -> None:
    candidate = VectorCandidate(
        rank=1,
        chunk_id=ChunkId(UUID(int=1)),
        document_id=DocumentId(UUID(int=2)),
        source_id=SourceId(UUID(int=3)),
```

Замініть на:

```python
    assert [GROUP.value] in values
    assert [INJECTED_MEDIA_TYPE] in values
    assert ["uk"] in values
    assert [DOCUMENT.value] in values


def test_the_reader_s_chunks_are_settled_before_any_ranking() -> None:
    # A MATERIALIZED step is computed on its own and never merged into the
    # ranking, so the grants are read for every Chunk once, whatever its
    # distance, and the time of a search cannot count hidden near Chunks.
    statement = vector_search_statement(
        space_id=EmbeddingSpaceId.new(),
        query=(1.0, 0.0, 0.0),
        eligibility=eligible_chunks_predicate(AuthorizationContext(user_id=USER)),
        restriction=metadata_predicate(()),
        limit=5,
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))
    visible, ranked = sql.split(")\n SELECT", 1)

    assert visible.startswith("WITH visible AS MATERIALIZED")
    assert "access_grants" in visible
    assert "access_grants" not in ranked
    assert "<=>" not in visible
    assert "JOIN visible" in ranked
    assert "ORDER BY" in ranked


def test_a_candidate_repr_shows_no_document_text() -> None:
    candidate = VectorCandidate(
        rank=1,
        chunk_id=ChunkId(UUID(int=1)),
        document_id=DocumentId(UUID(int=2)),
        source_id=SourceId(UUID(int=3)),
```

**Перевірка:** `uv run pytest tests/test_vector_search.py -q` дає `35 passed`.

### Крок 4 — Крок із кодом: порівняння «чорною скринькою»

**Простими словами:** ці тести двічі дивляться на систему ззовні. Один раз Document bob немає; другий раз Document bob — ідеальний збіг для питання alice, або запит alice використовує справжні ID bob. Усе, що alice чи лог могли б побачити, записується обидва рази, замасковано лише ID запитів і ID Evidence, і два записи мусять бути однакові.

| Тест | Порівнює |
|---|---|
| `test_a_forbidden_best_match_changes_nothing_the_reader_or_logs_see` | Статус, заголовки, тіло, промпт і кожен запис логу, для читача з одним Document і без жодного |
| `test_citing_a_forbidden_chunk_reads_like_citing_a_missing_one` | Увесь результат `confirm_citations` і його лог |
| `test_forbidden_evidence_reads_like_missing_evidence` | `evidence_still_visible` і його лог |
| `test_another_users_deleted_and_missing_conversations_read_the_same` | `load_history` і `delete_conversation` для Conversation bob, видаленої та відсутньої |

Створіть `tests/test_existence_leaks_integration.py`:

```python
"""Black-box: a Document the reader may not see looks exactly like no Document.

Each test observes what a reader, a log, or the model can see twice: once
where another User's Document does not exist, and once where it exists and
is the best possible match, or its real identifiers are used. The two
observations must be equal, once the values that differ on every request
anyway, UUIDs and opaque Evidence IDs, are masked.
"""

import dataclasses
import logging
import re
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import SecretStr

from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.answering.citations import Citation
from knowledge_service.answering.current_citations import (
    CurrentCitations,
    confirm_citations,
    evidence_still_visible,
)
from knowledge_service.answering.evidence import Evidence, EvidenceSet, build_evidence
from knowledge_service.answering.generation import (
    AnswerOutcome,
    AnswerResult,
    ModelReply,
)
from knowledge_service.answering.history import load_history
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.app import create_app
from knowledge_service.conversations import ConversationLimits
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import ChunkId, ConversationId, DocumentId, UserId
from knowledge_service.persistence.conversations import (
    delete_conversation,
    start_conversation,
)
from knowledge_service.retrieval.vector_search import (
    search_similar_chunks,
)
from knowledge_service.settings import Settings
from test_citation_confirmation_integration import CitingModel, vector_source
from test_streaming import QUESTION, REQUEST_ID, USAGE, streamer
from test_vector_search_integration import (
    IDENTITY,
    INSERT_SOURCE,
    OBSERVED_AT,
    QUERY,
    SOURCE_ID,
    a_document,
    a_space,
)

BOB = AuthorizationContext(user_id=UserId(UUID(int=0xB0B)))
READERS_GROUP = next(iter(READER.group_ids))
LIMITS = ConversationLimits()
# Values that differ on every request whatever the data holds.
VOLATILE = [
    (
        re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"),
        "<id>",
    ),
    (re.compile(r"E[0-9a-f]{8}"), "<evidence>"),
]
# The attributes every log record has; anything else was passed as `extra`.
STANDARD = frozenset(
    logging.LogRecord("", logging.INFO, "", 0, "", (), None).__dict__
) | {"message", "asctime", "taskName"}


def masked(text: str) -> str:
    for pattern, replacement in VOLATILE:
        text = pattern.sub(replacement, text)
    return text


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE_ID.value})
    await a_space(runtime, IDENTITY, is_current=True)
    try:
        yield runtime
    finally:
        await runtime.dispose()


class Watching(CitingModel):
    """The citing model, keeping what each prompt showed it."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.prompts: list[str] = []

    async def stream(  # type: ignore[override]
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> AsyncGenerator[str | ModelReply]:
        self.prompts.append(masked(prompt.input))
        async for part in super().stream(
            prompt, request_id=request_id, timeout_seconds=timeout_seconds
        ):
            yield part


def logged(caplog: pytest.LogCaptureFixture) -> list[tuple[str, str, str, str]]:
    """Every log record, from any logger, with every extra field.

    Only timing and identifiers are masked, so a record that differs in
    anything else, or appears only once, makes two observations differ.
    """
    records: list[tuple[str, str, str, str]] = []
    for record in caplog.records:
        fields = {
            key: value for key, value in record.__dict__.items() if key not in STANDARD
        }
        for value in fields.values():
            if isinstance(value, dict):
                cast(dict[str, Any], value).pop("duration_ms", None)
        records.append(
            (record.name, record.levelname, record.getMessage(), masked(repr(fields)))
        )
    return records


async def observe(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> tuple[Any, ...]:
    """Everything outside the server sees of one question about the handbook."""
    caplog.clear()
    model = Watching("handbook.txt")
    app = create_app(
        streamer=streamer(
            model,
            source=cast(Any, vector_source(runtime)),
            citations=CurrentCitations(runtime),
        ),
        authenticator=TRUSTED,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.post(
            "/v1/answer/stream", json={"text": QUESTION.text}, headers=AUTHORIZATION
        )
    return (
        response.status_code,
        masked(repr(sorted(response.headers.items()))),
        masked(response.text),
        tuple(model.prompts),
        tuple(logged(caplog)),
    )


async def bobs_secret(runtime: DatabaseRuntime) -> DocumentId:
    """Another User's Document, an exact match for the question."""
    return await a_document(
        runtime,
        "SECRET-bob.txt",
        readers=[Subject.user(BOB.user_id)],
        vector=[1.0, 0.0, 0.0],
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "own", [True, False], ids=["reader-has-one", "reader-has-none"]
)
async def test_a_forbidden_best_match_changes_nothing_the_reader_or_logs_see(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture, own: bool
) -> None:
    caplog.set_level(logging.DEBUG)
    if own:
        await a_document(
            runtime,
            "handbook.txt",
            readers=[Subject.group(READERS_GROUP)],
            vector=[0.6, 0.8, 0.0],
        )

    absent = await observe(runtime, caplog)
    await bobs_secret(runtime)
    present = await observe(runtime, caplog)

    assert present == absent
    # The reader with a Document of her own is answered; without, abstains.
    assert ('"decision":"answer"' if own else '"no_evidence"') in absent[2]
    assert "SECRET" not in repr(present)


async def bobs_evidence(runtime: DatabaseRuntime) -> Evidence:
    """Bob's real Chunk as Evidence, with the identifiers retrieval gave it."""
    await bobs_secret(runtime)
    async with runtime.transaction() as connection:
        found = await search_similar_chunks(
            connection, identity=IDENTITY, query=QUERY, context=BOB, limit=1
        )
    (item,) = build_evidence(found.candidates).items
    return item


def invented(item: Evidence) -> Evidence:
    """The same Evidence pointing at a Chunk and Document that never existed."""
    return dataclasses.replace(
        item, chunk_id=ChunkId(uuid4()), document_id=DocumentId(uuid4())
    )


def answer_citing(item: Evidence) -> AnswerResult:
    citation = Citation(
        evidence_id=item.evidence_id,
        chunk_id=item.chunk_id,
        document_id=item.document_id,
        source_id=item.source_id,
        content_version=item.content_version,
        title=item.title,
        heading_path=item.heading_path,
        source_link=item.source_link,
        excerpt=item.excerpt,
    )
    return AnswerResult(
        outcome=AnswerOutcome.ANSWERED,
        text="The SECRET-bob.txt policy.",
        reason=None,
        citations=(citation,),
        defect=None,
        usage=USAGE,
        context=cast(Any, None),
    )


@pytest.mark.integration
async def test_citing_a_forbidden_chunk_reads_like_citing_a_missing_one(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    real = await bobs_evidence(runtime)
    outcomes: list[tuple[Any, ...]] = []

    for item in (real, invented(real)):
        caplog.clear()
        async with runtime.transaction() as connection:
            result = await confirm_citations(
                connection, READER, answer_citing(item), request_id=REQUEST_ID
            )
        outcomes.append((result, logged(caplog)))

    # The whole result, reason, defect, and usage included, and every record.
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0].outcome is AnswerOutcome.WITHDRAWN


@pytest.mark.integration
async def test_forbidden_evidence_reads_like_missing_evidence(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    real = await bobs_evidence(runtime)
    outcomes: list[tuple[Any, ...]] = []

    for item in (real, invented(real)):
        caplog.clear()
        async with runtime.transaction() as connection:
            visible = await evidence_still_visible(
                connection, READER, EvidenceSet((item,)), request_id=REQUEST_ID
            )
        outcomes.append((visible, logged(caplog)))

    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] is False


@pytest.mark.integration
async def test_another_users_deleted_and_missing_conversations_read_the_same(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    async with runtime.transaction() as connection:
        bobs = await start_conversation(
            connection, owner_id=BOB.user_id, now=OBSERVED_AT, limits=LIMITS
        )
        own = await start_conversation(
            connection, owner_id=READER.user_id, now=OBSERVED_AT, limits=LIMITS
        )
        await delete_conversation(
            connection,
            owner_id=READER.user_id,
            conversation_id=own.conversation_id,
            now=OBSERVED_AT,
        )
    targets = {
        "bobs": bobs.conversation_id,
        "deleted": own.conversation_id,
        "missing": ConversationId(uuid4()),
    }
    seen: dict[str, tuple[Any, ...]] = {}

    for name, conversation_id in targets.items():
        caplog.clear()
        async with runtime.transaction() as connection:
            history = await load_history(
                connection,
                READER,
                conversation_id=conversation_id,
                now=OBSERVED_AT,
                budget_tokens=4_000,
            )
            deleted = await delete_conversation(
                connection,
                owner_id=READER.user_id,
                conversation_id=conversation_id,
                now=OBSERVED_AT,
            )
        seen[name] = (history, deleted, logged(caplog))

    assert seen["bobs"] == seen["deleted"] == seen["missing"] == (None, False, [])
```

`logged` зберігає кожен атрибут запису понад стандартні, тож поле, додане будь-яким кодом через будь-який логер, потрапляє в порівняння. Рев'ю підкинув заголовок в інший логер і лічильник в інше поле; перша версія, що читала чотири названі поля, пропустила обидва.

**Перевірка:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_existence_leaks_integration.py -q
```

Очікувано: `5 passed`.

### Крок 5 — Без коду: зламайте навмисно

Цей крок прибирає одне слово, що тримає порядок, і показує, які тести це помічають.

**Дія 1:** у `vector_search_statement` у `src/knowledge_service/retrieval/vector_search.py` видаліть цей рядок:

```python
        .prefix_with("MATERIALIZED")
```

**Дія 2:** запустіть `uv run pytest tests/test_vector_search.py -q`. Очікувано: `1 failed, 34 passed`: `test_the_reader_s_chunks_are_settled_before_any_ranking`.

**Дія 3:** не повертаючи рядок, запустіть контракт безпеки з M07-T08:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_search_security_contract_integration.py tests/test_existence_leaks_integration.py -q
```

Очікувано: `186 passed`. Кожен результат досі правильний; лише робота, а отже й час, знову може залежати від прихованих Chunks. Витік через час ніколи не видно в тесті результатів, тому й потрібен структурний тест.

**Дія 4:** поверніть рядок і знову запустіть `uv run pytest tests/test_vector_search.py -q`: `35 passed`. Порівняйте з останнім прогнозом в evidence-файлі.

### Крок 6 — Без коду: документи, перевірки, evidence і прогрес

Цей крок оновлює те, що зміна зробила неточним, доводить, що все працює, і записує результат. Threat model називав `M07-T11` власником інваріанта 7; trace тепер називає тести цього уроку, а новий рядок записує канал через таймінг.

**Дія 1:** оновіть два документи.

У `docs/architecture.md`:

**1.** Знайдіть:

````markdown
filters (Source, Document, media type, language, modified since) are typed
conditions from a fixed list; they are added to the same WHERE and only
narrow it.

```mermaid
````

Замініть на:

````markdown
filters (Source, Document, media type, language, modified since) are typed
conditions from a fixed list; they are added to the same WHERE and only
narrow it. Since M07-T11 the statement settles the reader's Chunks in a
`MATERIALIZED` step before it ranks any, so its work, and its time, does not
grow with hidden Chunks near the question.

```mermaid
````

**2.** Знайдіть:

```markdown
        Search-->>Caller: NoCurrentEmbeddingSpace
    else current
        Search->>DB: one SELECT: space still current AND available AND a matching Access Grant AND every filter AND a real distance, ORDER BY distance, Chunk ID, LIMIT k + 1
        DB-->>Search: eligible Chunks with provenance and distance
        alt no rows and the space is no longer current
```

Замініть на:

```markdown
        Search-->>Caller: NoCurrentEmbeddingSpace
    else current
        Search->>DB: one SELECT: first the Chunks that are available, granted, and match every filter (MATERIALIZED), then of those, in the current space with a real distance, ORDER BY distance, Chunk ID, LIMIT k + 1
        DB-->>Search: eligible Chunks with provenance and distance
        alt no rows and the space is no longer current
```

У `docs/threat-model.md`:

**1.** Знайдіть:

```markdown
| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT | Adversarial suite (M07-T15) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query, and one security contract runs the same seeded adversarial corpora through vector search and English, Ukrainian, and mixed lexical search, by topic words and with web-search syntax: each must return exactly the eligible Chunks, its best result must be the reader's best match, and no hidden title, text, or highlight may appear, however well the others match | Authorization adversarial suite (M07-T15) |
| A reader that is not a checked Authorization Context, such as a subclass, a look-alike, or a context changed after it was made, widens a search | `eligible_chunks_predicate` passes every reader through `checked_reader`, which refuses anything but an exact `AuthorizationContext` and checks its fields again; the predicate ties each Chunk to its own Document, so a statement that forgets the join narrows nothing; `VectorEvidenceSource` checks the reader before the Question is embedded and searches only with one vector from its own model | None |
| A reranker or a ranking model is shown text the reader may not see | Only eligible Candidates reach any reranker | None |
| A Question proves a Document exists by the way it fails | Empty, unauthorized, and withdrawn read the same | Existence leaks (M07-T11) |
| A cache shared across readers serves one reader's Candidates or Answer to another | No cache of Candidates, Evidence, or Answers exists; Redis is for ephemeral coordination only (ADR 0011) | Adversarial suite covers caches (M07-T15) |

### Generation

| Abuse case | Mitigation now | Still to come |
|---|---|---|
```

Замініть на:

```markdown
| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT | Adversarial suite (M07-T15) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query, and one security contract runs the same seeded adversarial corpora through vector search and English, Ukrainian, and mixed lexical search, by topic words and with web-search syntax: each must return exactly the eligible Chunks, its best result must be the reader's best match, and no hidden title, text, or highlight may appear, however well the others match | Authorization adversarial suite (M07-T15) |
| A reader that is not a checked Authorization Context, such as a subclass, a look-alike, or a context changed after it was made, widens a search | `eligible_chunks_predicate` passes every reader through `checked_reader`, which refuses anything but an exact `AuthorizationContext` and checks its fields again; the predicate ties each Chunk to its own Document, so a statement that forgets the join narrows nothing; `VectorEvidenceSource` checks the reader before the Question is embedded and searches only with one vector from its own model | None |
| A reranker or a ranking model is shown text the reader may not see | Only eligible Candidates reach any reranker | None |
| A Question proves a Document exists by the way it fails | Empty, unauthorized, and withdrawn read the same; black-box tests show that another User's best-matching Document changes nothing in the response bytes, headers, log records, or the model's prompt, and that a forbidden Chunk, Evidence, or Conversation ID gives the same result and log as a missing one | Authorization adversarial suite (M07-T15) |
| The time a search takes tells a reader how many hidden Chunks match their question | Vector search settles the reader's Chunks in a `MATERIALIZED` step before ranking any, so its work does not grow with hidden near Chunks: with 5,000 hidden exact matches the gap fell from about 10 ms to within noise, at the cost of about twice the time for a reader who can see 20,000 Documents | Lexical search still filters after its index, about 5 to 28 ms slower per 5,000 hidden matches; it reaches no reader yet, and must be fixed or bounded before lexical or hybrid retrieval joins a served path (M09); a hybrid branch deadline would turn that gap into a timed-out branch |
| A cache shared across readers serves one reader's Candidates or Answer to another | No cache of Candidates, Evidence, or Answers exists; Redis is for ephemeral coordination only (ADR 0011) | Adversarial suite covers caches (M07-T15) |

### Generation

| Abuse case | Mitigation now | Still to come |
|---|---|---|
```

**2.** Знайдіть:

```markdown
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `tests/test_synchronization_integration.py::test_a_revocation_is_published_before_any_record_of_its_page_is_parsed`, `tests/test_revocation_integration.py::test_a_grant_revoked_mid_conversation_fails_every_later_read_closed` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. `VectorEvidenceSource` searches the database as the reader, by meaning only: lexical and hybrid retrieval are not wired into the answer stream yet, and nothing reranks its Evidence yet, so the abstention policy sees only whether anything was found.
- Groups from the identity provider become Group IDs by their path; a Source's groups and users become the same IDs only through its `PrincipalDirectory`, which is passed to `synchronize_source` by hand, since no stored Source configuration holds one yet. The count of unresolved principals is logged only when a pass completes. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Sparse search (the BGE-M3 prototype) applies the same predicate but is outside the shared security contract, and `load_searchable_chunks` in `persistence.chunks` reads Chunks with no permission check; nothing in `src` calls it.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- No endpoint reads a whole Document, an excerpt, or an export by ID yet; each must read through `eligible_chunks_predicate` with the request's reader when it is built, as the agent tools must (M11-T01).
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
```

Замініть на:

```markdown
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `tests/test_synchronization_integration.py::test_a_revocation_is_published_before_any_record_of_its_page_is_parsed`, `tests/test_revocation_integration.py::test_a_grant_revoked_mid_conversation_fails_every_later_read_closed` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `tests/test_existence_leaks_integration.py::test_a_forbidden_best_match_changes_nothing_the_reader_or_logs_see`, `tests/test_existence_leaks_integration.py::test_citing_a_forbidden_chunk_reads_like_citing_a_missing_one`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. `VectorEvidenceSource` searches the database as the reader, by meaning only: lexical and hybrid retrieval are not wired into the answer stream yet, and nothing reranks its Evidence yet, so the abstention policy sees only whether anything was found.
- Groups from the identity provider become Group IDs by their path; a Source's groups and users become the same IDs only through its `PrincipalDirectory`, which is passed to `synchronize_source` by hand, since no stored Source configuration holds one yet. The count of unresolved principals is logged only when a pass completes. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Sparse search (the BGE-M3 prototype) applies the same predicate but is outside the shared security contract, and `load_searchable_chunks` in `persistence.chunks` reads Chunks with no permission check; nothing in `src` calls it.
- `rate_message` and `load_message_feedback` take a Message ID with no owner check, and rating a missing Message fails differently from rating another User's; no route exposes them yet, and an owner-scoped wrapper must come first. Sparse search (the prototype) scores hidden Chunks in Python, so its time depends on them.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- No endpoint reads a whole Document, an excerpt, or an export by ID yet; each must read through `eligible_chunks_predicate` with the request's reader when it is built, as the agent tools must (M11-T01).
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
```

**Дія 2:** запустіть перевірки по черзі, із запущеним PostgreSQL:

```bash
uv run pytest tests/test_vector_search.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний прогін дав 2785 passed для `make test-unit`, на 1 більше, ніж після M07-T10, і 641 passed для `make test-integration`, на 5 більше.

**Дія 3:** заповніть evidence-файл:

- **Design:** порівняння «чорною скринькою», що маскується і чому, матеріалізований крок і що досі витікає з лексичного пошуку.
- **Verification:** команди й кількості, цифри часу до і після, навмисний злам і відомі обмеження з «Перш ніж почати».
- **Review:** підсумок рев'ю з «Перш ніж почати».
- **Decision:** чому порівнювати все, чому вирішувати, хто що читає, до ранжування, чому прийняти ціну для великих читачів і чому лексичний пошук чекає; плюс щонайменше одне **фальсифіковане припущення** у формі «I assumed X. The run or review shows Y. So Z is wrong.»
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу як completed, додайте рядок M07-T11 у `docs/curriculum/PROGRESS.md`, поставте наступною задачею M07-T12 і закомітьте з `m07-t11: eliminate existence leaks`.

## Запитання для рефлексії

1. Потік відповіді повертає ті самі байти, чи існує Document bob, чи ні. Назвіть дві інші речі, крім байтів, за якими цікавий читач міг би спостерігати, і скажіть, які з них перевіряє цей урок.
2. Прибирання `MATERIALIZED` не зламало жодного тесту результатів. Чому витік через час невидимий для таких тестів, і який тест його ловить?
3. Виправлення подвоїло час для читача, який бачить 20 000 Documents. Хто платить цю ціну, хто виграє, і що б ви змінили, коли корпус виросте до мільйонів Chunks?
4. Майбутній ендпоінт відповідає «403 Forbidden» на Conversation іншого User і «404 Not Found» на відсутню. Що дізнається читач, і що він має відповідати натомість?
5. bob має 5000 Documents про заплановану реорганізацію, яких alice бачити не можна. Опишіть, як alice могла б спробувати дізнатися про їхнє існування через лексичний пошук, щойно він почне відповідати читачам, і чим має стати там виправлення з цього уроку.

## Контрольний список завершення

- [ ] Evidence-файл містить інваріант і ваші прогнози, записані до зміни.
- [ ] Векторний пошук визначає Chunks читача в кроці `MATERIALIZED` до ранжування, а його тести досі проходять (43).
- [ ] Структурний тест закріплює цей порядок (35 у файлі).
- [ ] Тести «чорною скринькою» проходять проти PostgreSQL (5): потік, логи, промпт і чужі проти відсутніх ID.
- [ ] Ви запустили навмисний злам, побачили, що структурний тест впав, а контракт пройшов, і скасували його.
- [ ] `docs/architecture.md` і `docs/threat-model.md` описують виправлення, його ціну й обмеження лексичного пошуку, а trace називає нові тести.
- [ ] Усі перевірки проходять: format, lint, types, unit, integration, docs.
- [ ] Evidence, статус задачі, рядок у PROGRESS і коміт готові.

Коли напишете файли, надішліть їх або вивід помилок, і я перевірю їх за цим списком.
