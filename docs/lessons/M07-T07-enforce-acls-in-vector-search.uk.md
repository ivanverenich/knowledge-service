# M07-T07 — Контроль доступу у векторному пошуку

Англійська версія: [M07-T07-enforce-acls-in-vector-search.md](M07-T07-enforce-acls-in-vector-search.md)

Задача: [M07-T07 — Enforce ACLs in vector search](../curriculum/milestones/M07-identity-and-authorization/M07-T07-enforce-acls-in-vector-search.md)

## Що ви зробите

Цей урок з'єднує читача (reader) з токена з векторним пошуком у справжній базі і ворожими даними доводить, що нічого недозволеного читачеві не повертається. Перевірка доступу всередині векторного запиту вже існує з M04-T06: вона приєднує кожен Chunk до його Document і його Access Grants у тому самому SQL, що сортує за відстанню. Бракує справжнього джерела Evidence, яке запускає цей запит від імені читача запиту; потік відповіді досі використовував лише тестові джерела.

- **Справжнє джерело Evidence:** `VectorEvidenceSource` перетворює Question на вектор і запускає векторний пошук з Authorization Context запиту. Потік відповіді тепер може брати Evidence з PostgreSQL.
- **Лише справжній читач:** перевірка доступу приймає лише точний, незмінений `AuthorizationContext`. Підклас, схожий об'єкт чи контекст, змінений після створення, відкидаються ще до побудови SQL.
- **Самодостатня перевірка:** перевірка доступу сама прив'язує кожен Chunk до його Document, тож запит, що забув `JOIN`, не з'єднає кожен Chunk з кожним видимим Document.
- **Атакувальний доказ:** тридцять згенерованих корпусів, де недозволені Chunks зазвичай так само близькі або ближчі, ніж дозволені, звіряються з простою Python-моделлю правила.

| Задача вимагає | Де це відбувається |
|---|---|
| Join current Documents and Access Grants inside the vector Candidate query | Уже є у `vector_search_statement`; крок 3 робить перевірку самодостатньою |
| Using the Authorization Context and availability state | Кроки 2 і 3: лише справжній читач стає перевіркою; крок 4: читач потоку доходить до запиту |
| Tests: success, highest-risk edge, typed failure or denial | Кроки 5 і 6 |
| Adversarial data proves unauthorized high-similarity Chunks are never returned | Крок 6: `tests/test_vector_acl_adversarial_integration.py` |
| Authorization preserved before data leaves storage | Крок 6: прихований точний збіг ніколи не доходить до промпту моделі чи клієнта |
| Telemetry holds no seeded sensitive text | Крок 6: тест із маркером на промпті й потоці |
| Update only what the change made inaccurate | Крок 8: `docs/architecture.md`, `docs/threat-model.md` |

У цьому уроці є код: кроки 2–6 пишуть код і тести. Кроки 1, 7 і 8 без коду, хоча крок 8 змінює два документи.

Терміни цього уроку:

- **Векторний пошук:** пошук Chunks, чиї збережені вектори вказують майже в тому самому напрямку, що й вектор Question. Близькість — це косинусна відстань: 0 — той самий напрямок, 2 — протилежний.
- **Придатність (eligibility):** чи може читач бачити Chunk: його Document доступний, і грант називає всіх, самого читача або одну з його Groups.
- **Придатність усередині запиту:** база відкидає непридатні Chunks до того, як сортує і бере перші k. Як бібліотекар, що бере книжки лише з полиць, куди вам можна, а не приносить усе і ховає частину за стійкою.
- **Атакувальні дані (adversarial data):** тестові дані, побудовані так, щоб помилка стала видимою, тут — заборонені Chunks рівно на місці запиту.
- **Еталонна модель (reference model):** кілька рядків звичайного Python, що незалежно формулюють правило, щоб відповідь бази можна було з ними звірити.
- **Декартів добуток (cross join):** запит, що читає дві таблиці, не зв'язуючи їхні рядки, тож кожен рядок однієї стає в пару з кожним рядком іншої.

## Перш ніж почати

Цей розділ пояснює рішення за кодом, щоб кожен крок був зрозумілим, коли ви до нього дійдете.

- **Запит уже фільтрує; прогалина — у виклику.** `search_similar_chunks` з M04 передає `eligible_chunks_predicate(context)` у той самий WHERE, що й сортування за відстанню, а `EXPLAIN` показує перевірку грантів нижче за сортування й ліміт. Відкритим пунктом threat model було «No Evidence source over the database takes the reader yet». Цей урок закриває його для векторного пошуку; M07-T08 — для лексичного.
- **Перевіряти читача там, де він стає SQL.** Кожен пошук будує перевірку доступу через `eligible_chunks_predicate`. Тож саме ця функція тепер викликає `checked_reader`, який приймає лише точний `AuthorizationContext` і ще раз перевіряє його поля. Frozen dataclass усе одно можна змінити через `object.__setattr__`, а підклас може відповідати іншими Groups, ніж ті, з якими його перевіряли.
- **Перевірка сама зв'язує Chunk і Document.** Кожен наявний виклик приєднує `chunks` до `documents`. Якщо майбутній забуде, перевірка все одно називала б `documents`, і база з'єднала б кожен Chunk з кожним Document, який читач може бачити. З `documents.document_id = chunks.document_id` усередині перевірки забутий `JOIN` нічого не розширює.
- **Джерело Evidence перевіряє, перш ніж витрачати.** `VectorEvidenceSource` перевіряє читача до побудови вектора, тож поганий запит не коштує виклику провайдера. Далі він вимагає рівно один вектор від своєї моделі, бо відповідь з іншою моделлю порівнювалася б із векторами, під які цей Question ніколи не будували.
- **Переранжування поки немає.** Джерело повідомляє, скільки Chunks знайшло, але без оцінки. Політика відмови без порогу оцінки працює; з порогом — відмовляє на кожен запит, бо немає з чим порівнювати.
- **Із зерном, а не випадково.** Атакувальні корпуси будуються з `random.Random(seed)` для зерен 0–29, тож падіння називає зерно і відтворюється точно. Окрема перевірка доводить, що корпуси складні: у більшості пар «зерно × читач» заборонений Chunk щонайменше так само близький, як кожен дозволений.
- **Що знайшов рев'ю.** Незалежний атакувальний рев'ю пробував плутанину ID користувачів і груп, дублікати грантів, фільтри метаданих, що називають приховані Documents, видалені Documents, неактуальні простори векторів, 20 000 груп, рівність відстаней на межі ліміту, 60 одночасних читачів на одному пулі з'єднань і повне логування SQL на рівні DEBUG. Нічого прихованого не повернулося, не проявилося в `truncated` чи лічильнику і не потрапило в лог. Він зробив такі зауваження, і всі виправлено тестами, що падали до виправлення:
  - Контекст, змінений після створення, наприклад `group_ids`, замінений звичайним set з іншими Groups, не перевірявся вдруге і розширював пошук. Тепер `checked_reader` перевіряє поля щоразу.
  - Перевірка називала `documents`, не зв'язуючи його з `chunks`, тож запит, що забув `JOIN`, повертав би кожен Chunk, щойно хоч один Document видимий. Тепер зв'язок усередині перевірки.
  - Джерело Evidence довіряло відповіді моделі: чужий identity, жодного вектора чи два вектори. Тепер воно вимагає один вектор від своєї моделі.
  - Дві мутації вижили: читач-підклас, що проходив власну перевірку джерела і коштував виклику побудови вектора, і налаштований тайм-аут, що не доходив до моделі. Тепер на кожну є тест.
- **Відомі обмеження.** Гілка гібридного пошуку `dense_branch` так само довіряє відповіді моделі. Векторні й лексичні запити не вимагають, щоб версія вмісту Chunk збігалася з версією його Document; старі Chunks видаляються в тій самій транзакції, що пише нові, тож це питання свіжості, а не доступу. Жоден тест не покриває гонку, коли актуальний Embedding Space перемикається посеред запиту. Продакшн-точка входу ще не збирає застосунок із цим джерелом.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої прогнози

Перш ніж щось змінювати, запишіть, що має бути правдою наприкінці уроку, і що ви очікуєте. Крок 7 перевіряє останній прогноз.

Створіть `docs/evidence/M07-T07-enforce-acls-in-vector-search.md`:

```markdown
# M07-T07 — Enforce ACLs in vector search evidence

## Invariant

A vector search returns only Chunks whose Document is available and granted
to everyone, to the reader's User, or to one of the reader's Groups, decided
in the same SQL statement that orders by distance and applies the limit; a
Chunk the reader may not see is never ranked, counted, or returned, however
near it is. The check accepts only an exact, unchanged `AuthorizationContext`
and ties each Chunk to its own Document. The answer stream's Evidence comes
from that search as the request's reader, and no hidden text reaches the
model's prompt, the client, or a log.

## Expected failing check

Before the change, `uv run pytest tests/test_vector_evidence_integration.py -q`
fails with `ModuleNotFoundError: No module named 'knowledge_service.answering.vector_evidence'`.

## Predictions

- What `VectorEvidenceSource` with limit 1 returns to a reader in `/engineering` when a Chunk granted only to another user is an exact match and the engineering Chunk is farther away:
- What `eligible_chunks_predicate` does with a real context whose `group_ids` was replaced afterwards with a plain `set`:
- What `find` does when the embedding model replies with another model's identity:
- Which tests fail if `eligibility` is removed from the WHERE of `vector_search_statement`:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після двокрапки в кожному прогнозі є ваша відповідь.

### Крок 2 — Крок із кодом: перевіряти читача там, де його використовують

**Простими словами:** читача перевіряють один раз, коли його створюють. Цей крок додає функцію, що перевіряє його знову, щоразу перед тим, як він стане перевіркою доступу: чи це справді тип Authorization Context, і чи його користувач і групи досі мають правильні види ID. Підроблений чи змінений згодом читач отримує відмову.

| Зміна | Що робить |
|---|---|
| `__post_init__` | Викликає `_check_reader_fields`, ті самі перевірки, що й раніше |
| `checked_reader` | Повертає контекст, якщо це точно `AuthorizationContext` зі справними полями, інакше `InvalidAuthorizationContext` |
| `_check_reader_fields` | Користувач — це `UserId`, групи — `frozenset` з `GroupId` |

У `src/knowledge_service/access.py`:

Знайдіть:

```python
    """

    user_id: UserId
    group_ids: frozenset[GroupId] = frozenset()

    def __post_init__(self) -> None:
        # A plain UUID, or a UserId passed as a group, would match a grant
        # meant for another subject kind, so the exact types are required.
        if type(self.user_id) is not UserId:
            raise InvalidAuthorizationContext("the user must be a UserId")
        if type(self.group_ids) is not frozenset:
            raise InvalidAuthorizationContext("the groups must be a frozenset")
        if any(type(group_id) is not GroupId for group_id in self.group_ids):
            raise InvalidAuthorizationContext("every group must be a GroupId")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidGrant(f"{field_name} must be a UTC-aware timestamp")

```

Замініть на:

```python
    """

    user_id: UserId
    group_ids: frozenset[GroupId] = frozenset()

    def __post_init__(self) -> None:
        _check_reader_fields(self)


def checked_reader(context: object) -> AuthorizationContext:
    """`context` itself, if it is exactly an `AuthorizationContext` with sound fields.

    A frozen dataclass can still be changed with `object.__setattr__`, and a
    subclass or look-alike can answer with other Users or Groups, so code
    that turns a reader into a permission check calls this first.
    """
    if type(context) is not AuthorizationContext:
        raise InvalidAuthorizationContext("the reader must be an AuthorizationContext")
    _check_reader_fields(context)
    return context


def _check_reader_fields(context: AuthorizationContext) -> None:
    # A plain UUID, or a UserId passed as a group, would match a grant
    # meant for another subject kind, so the exact types are required.
    if type(context.user_id) is not UserId:
        raise InvalidAuthorizationContext("the user must be a UserId")
    if type(context.group_ids) is not frozenset:
        raise InvalidAuthorizationContext("the groups must be a frozenset")
    if any(type(group_id) is not GroupId for group_id in context.group_ids):
        raise InvalidAuthorizationContext("every group must be a GroupId")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.utcoffset() != timedelta(0):
        raise InvalidGrant(f"{field_name} must be a UTC-aware timestamp")

```

**Перевірка:** `uv run pytest tests/test_authorization_context.py -q` дає `89 passed`, без змін.

### Крок 3 — Крок із кодом: перевірка доступу, що тримається сама

**Простими словами:** кожен пошук, за змістом чи за словами, отримує перевірку доступу від однієї функції. Цей крок змушує її спершу перевіряти читача функцією з кроку 2 і самостійно прив'язувати кожен Chunk до його Document, щоб перевірка лишалася правильною навіть у запиті, що забув зробити цей зв'язок.

| Зміна | Що робить |
|---|---|
| Імпорти | `checked_reader`; `chunks` зі схеми |
| `eligible_chunks_predicate` | Перевіряє читача, потім додає `documents.document_id = chunks.document_id` до умов доступності й грантів |

У `src/knowledge_service/persistence/access.py`:

**1.** Знайдіть:

```python
from knowledge_service.access import (
    AccessGrant,
    AuthorizationContext,
    InvalidGrant,
    Subject,
    SubjectKind,
    granted_subjects,
)
from knowledge_service.documents import DocumentAvailability
from knowledge_service.identifiers import AccessGrantId, DocumentId, GroupId, UserId
from knowledge_service.persistence.schema import access_grants, documents


def subject_grants_statement(subject_kind: str, subject_id: UUID) -> sa.Select[Any]:
    """Select the Documents one verified subject is allowed to reach."""
    return sa.select(access_grants.c.document_id).where(
        access_grants.c.subject_kind == subject_kind,
```

Замініть на:

```python
from knowledge_service.access import (
    AccessGrant,
    AuthorizationContext,
    InvalidGrant,
    Subject,
    SubjectKind,
    checked_reader,
    granted_subjects,
)
from knowledge_service.documents import DocumentAvailability
from knowledge_service.identifiers import AccessGrantId, DocumentId, GroupId, UserId
from knowledge_service.persistence.schema import access_grants, chunks, documents


def subject_grants_statement(subject_kind: str, subject_id: UUID) -> sa.Select[Any]:
    """Select the Documents one verified subject is allowed to reach."""
    return sa.select(access_grants.c.document_id).where(
        access_grants.c.subject_kind == subject_kind,
```

**2.** Знайдіть:

```python

def eligible_chunks_predicate(context: AuthorizationContext) -> sa.ColumnElement[bool]:
    """The condition a Chunk must meet before retrieval may rank it.

    Its Document is available, and at least one of its Access Grants names
    everyone, the context's User, or one of the context's Groups. The
    statement using it must join `chunks` to `documents`. Every value is a
    bound parameter, never text spliced into the SQL.
    """
    group_ids = sorted(group_id.value for group_id in context.group_ids)
    granted = sa.exists().where(
        access_grants.c.document_id == documents.c.document_id,
        sa.or_(
            access_grants.c.subject_kind == SubjectKind.PUBLIC.value,
            sa.and_(
```

Замініть на:

```python

def eligible_chunks_predicate(context: AuthorizationContext) -> sa.ColumnElement[bool]:
    """The condition a Chunk must meet before retrieval may rank it.

    Its Document is available, and at least one of its Access Grants names
    everyone, the context's User, or one of the context's Groups. The
    statement using it must read `chunks` and `documents`; the predicate
    ties each Chunk to its own Document itself, so a statement that forgets
    the join narrows nothing instead of pairing every Chunk with every
    visible Document. Every value is a bound parameter, never text spliced
    into the SQL.

    Only an exact `AuthorizationContext` with sound fields is accepted (see
    `checked_reader`): a subclass, a look-alike, or a context changed after
    it was made could answer with other Users or Groups.
    """
    context = checked_reader(context)
    group_ids = sorted(group_id.value for group_id in context.group_ids)
    granted = sa.exists().where(
        access_grants.c.document_id == documents.c.document_id,
        sa.or_(
            access_grants.c.subject_kind == SubjectKind.PUBLIC.value,
            sa.and_(
```

**3.** Знайдіть:

```python
                    )
                ),
            ),
        ),
    )
    return sa.and_(
        documents.c.availability == DocumentAvailability.AVAILABLE.value,
        granted,
    )


async def replace_access_grants(
```

Замініть на:

```python
                    )
                ),
            ),
        ),
    )
    return sa.and_(
        documents.c.document_id == chunks.c.document_id,
        documents.c.availability == DocumentAvailability.AVAILABLE.value,
        granted,
    )


async def replace_access_grants(
```

Додана рівність нічого не змінює для пошуків, які вже роблять `JOIN`, бо вони з'єднують ті самі стовпці; вона лише захищає майбутній запит, який цього не зробить.

**Перевірка:** кожен пошук, що використовує перевірку, досі проходить проти PostgreSQL:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_vector_search_integration.py tests/test_lexical_search_integration.py tests/test_current_citations_integration.py -q
```

Очікувано: `78 passed`.

### Крок 4 — Крок із кодом: векторне джерело Evidence

**Простими словами:** цей файл — те, що потік відповіді викликатиме, щоб знайти Evidence у справжній базі. Він перевіряє читача, перетворює Question на вектор, переконується, що модель повернула рівно один свій вектор, і запускає векторний пошук від імені цього читача. Chunks, які він отримує, стають Evidence; ніщо інше не може.

| Частина | Що робить |
|---|---|
| `VectorEvidenceSource(runtime, model, *, limit, embed_timeout_seconds)` | Ліміт від 1 до 20 і додатний скінченний тайм-аут, інакше `ValueError` |
| `find` | `checked_reader` (відмова стає `InvalidAnswerRequest`); побудова вектора; один вектор з identity самої моделі, інакше `InvalidEmbedding`; `search_similar_chunks` з читачем; `build_evidence` |
| Сигнали | `eligible` — кількість знайдених Chunks; без оцінки й без reranker |

Створіть `src/knowledge_service/answering/vector_evidence.py`:

```python
"""Find a Question's Evidence in PostgreSQL by meaning, as one reader.

`VectorEvidenceSource` is the `EvidenceSource` the answer stream uses over
the real database. It embeds the Question, then runs the vector search with
the request's Authorization Context, so the permission check happens inside
the same SQL statement that ranks Chunks: a Chunk the reader may not see is
never ranked, never counted, and never leaves the database, however close
its vector is.

It does not rerank, so its signals carry no score: the abstention policy can
only tell whether anything eligible was found, and a policy with a score
threshold refuses every request it judges.
"""

import math
from uuid import UUID

from knowledge_service.access import (
    AuthorizationContext,
    InvalidAuthorizationContext,
    checked_reader,
)
from knowledge_service.answering.abstention import EvidenceSignals
from knowledge_service.answering.evidence import MAX_EVIDENCE, build_evidence
from knowledge_service.answering.generation import InvalidAnswerRequest
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.database import DatabaseRuntime
from knowledge_service.embeddings.port import EmbeddingModel, InvalidEmbedding
from knowledge_service.retrieval.vector_search import search_similar_chunks

DEFAULT_LIMIT = 8


class VectorEvidenceSource:
    """Evidence for one Question: the nearest Chunks the reader may see."""

    def __init__(
        self,
        runtime: DatabaseRuntime,
        model: EmbeddingModel,
        *,
        limit: int = DEFAULT_LIMIT,
        embed_timeout_seconds: float,
    ) -> None:
        # bool is an int, so `True` would otherwise pass as a limit of 1.
        if type(limit) is not int or not 1 <= limit <= MAX_EVIDENCE:
            raise ValueError(f"limit must be an int between 1 and {MAX_EVIDENCE}")
        if (
            type(embed_timeout_seconds) not in (int, float)
            or not math.isfinite(embed_timeout_seconds)
            or embed_timeout_seconds <= 0
        ):
            raise ValueError("embed_timeout_seconds must be a positive number")
        self._runtime = runtime
        self._model = model
        self._limit = limit
        self._timeout = float(embed_timeout_seconds)

    async def find(
        self, question: str, *, reader: AuthorizationContext, request_id: UUID
    ) -> FoundEvidence:
        """The nearest eligible Chunks as Evidence, and how many there were.

        The reader is checked, fields included, before the Question is
        embedded, so a request without a sound Authorization Context costs
        no provider call.
        """
        try:
            reader = checked_reader(reader)
        except InvalidAuthorizationContext:
            raise InvalidAnswerRequest(
                "reader must be an AuthorizationContext"
            ) from None
        batch = await self._model.embed(
            [question], request_id=request_id, timeout_seconds=self._timeout
        )
        # Search only with the model's own space and exactly one vector: a
        # reply naming another model would compare against vectors this
        # Question was never embedded for.
        if batch.identity != self._model.identity or len(batch.vectors) != 1:
            raise InvalidEmbedding("the model must return one vector of its own")
        async with self._runtime.transaction() as connection:
            result = await search_similar_chunks(
                connection,
                identity=batch.identity,
                query=batch.vectors[0],
                context=reader,
                limit=self._limit,
            )
        return FoundEvidence(
            evidence=build_evidence(result.candidates),
            signals=EvidenceSignals(
                eligible=len(result.candidates), top_score=None, reranker=None
            ),
        )
```

**Перевірка:** `uv run pyright src` показує `0 errors`.

### Крок 5 — Крок із кодом: unit-тести перевірки читача

**Простими словами:** ці тести, яким не потрібна база, намагаються підсунути перевірці доступу ширшого читача: підклас, що відповідає більшою кількістю Groups, об'єкт, що лише схожий на читача, і справжніх читачів, змінених після створення. Усі мають отримати відмову. Ще два тести перевіряють, що читач потрапляє в SQL лише як прив'язані параметри і що перевірка сама зв'язує Chunk із Document.

Створіть `tests/test_eligibility_predicate.py`:

```python
"""The eligibility predicate takes only a sound Authorization Context.

Every search builds its permission check from the reader. A subclass could
answer with other Groups than the ones it was checked with, a look-alike was
never checked at all, and a context changed after it was made skipped its
checks, so all three are refused before any SQL is built.
"""

from typing import Any, cast
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from knowledge_service.access import AuthorizationContext, InvalidAuthorizationContext
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks

ALICE = UserId(UUID(int=0xA1))
ENGINEERING = GroupId(UUID(int=0xE1))
EVERY_GROUP = frozenset(GroupId(UUID(int=n)) for n in range(1, 4))


def wider_than_checked() -> Any:
    """A subclass that stores the Group it is checked with, then reads others."""
    stored: Any = AuthorizationContext.__dict__["group_ids"]

    def read(self: object) -> frozenset[GroupId]:
        return EVERY_GROUP

    def write(self: object, value: frozenset[GroupId]) -> None:
        stored.__set__(self, value)

    wider = type(
        "Wider",
        (AuthorizationContext,),
        {"__slots__": (), "group_ids": property(read, write)},
    )
    return wider(user_id=ALICE, group_ids=frozenset({ENGINEERING}))


def changed_after_checking(field: str, value: object) -> AuthorizationContext:
    """A real context whose field was replaced, skipping its own checks."""
    context = AuthorizationContext(user_id=ALICE, group_ids=frozenset({ENGINEERING}))
    object.__setattr__(context, field, value)
    return context


class LookAlike:
    user_id = ALICE
    group_ids = EVERY_GROUP


@pytest.mark.parametrize(
    "reader",
    [
        wider_than_checked(),
        LookAlike(),
        None,
        changed_after_checking("group_ids", set(EVERY_GROUP)),
        changed_after_checking("group_ids", frozenset({ALICE})),
        changed_after_checking("user_id", ENGINEERING),
    ],
    ids=[
        "subclass",
        "look-alike",
        "none",
        "groups-set",
        "user-as-group",
        "group-as-user",
    ],
)
def test_anything_but_an_authorization_context_is_refused(reader: Any) -> None:
    with pytest.raises(InvalidAuthorizationContext):
        eligible_chunks_predicate(cast(AuthorizationContext, reader))


def test_the_reader_reaches_the_sql_only_as_bound_parameters() -> None:
    reader = AuthorizationContext(user_id=ALICE, group_ids=frozenset({ENGINEERING}))

    compiled = eligible_chunks_predicate(reader).compile(dialect=postgresql.dialect())

    assert str(ALICE.value) not in str(compiled)
    assert ALICE.value in compiled.params.values()
    assert [ENGINEERING.value] in compiled.params.values()


def test_the_predicate_ties_each_chunk_to_its_own_document() -> None:
    # A statement that reads chunks but forgets to join documents must not
    # pair every Chunk with every Document the reader may see.
    reader = AuthorizationContext(user_id=ALICE)
    statement = sa.select(chunks.c.chunk_id).where(eligible_chunks_predicate(reader))

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "documents.document_id = chunks.document_id" in sql
```

`wider_than_checked` будує підклас, що зберігає Group, з якою його перевіряли, але відповідає трьома іншими. Він показує, чому перевірка порівнює точний тип, а не `isinstance`.

**Перевірка:** `uv run pytest tests/test_eligibility_predicate.py -q` дає `8 passed`.

### Крок 6 — Крок із кодом: джерело Evidence і атакувальні корпуси проти PostgreSQL

**Простими словами:** ці тести використовують справжню базу. Перший файл кладе прихований Chunk, що точно збігається з Question, і видимий далі, і перевіряє, що читач отримує лише видимий, що поганий читач чи зламана відповідь моделі відкидаються і що через HTTP прихований текст ніколи не доходить до промпту моделі чи відповіді. Другий файл будує тридцять малих ворожих корпусів і перевіряє, що база повертає рівно те, що каже проста Python-версія правила, і найближчий Chunk із них.

| Файл | Тести |
|---|---|
| `tests/test_vector_evidence_integration.py` | Лише видиме Evidence; без гранту нічого не знаходиться; ліміт обмежує; погані читачі відкидаються до побудови вектора; тайм-аут доходить до моделі; відповідь, що не є одним вектором самої моделі, відкидається; погані налаштування відкидаються; прихований точний збіг ніколи не доходить до моделі чи клієнта |
| `tests/test_vector_acl_adversarial_integration.py` | Тридцять корпусів із зерном звіряються з `may_read` для чотирьох читачів; перевірка, що корпуси складні; запит без `JOIN` досі бачить лише придатні Chunks |

Створіть `tests/test_vector_evidence_integration.py`:

```python
"""The answer stream's Evidence from real PostgreSQL, searched as the reader.

`VectorEvidenceSource` embeds the Question and runs the vector search with
the request's Authorization Context. These tests plant a hidden Chunk that is
an exact match for the Question and check it never becomes Evidence, never
reaches the model's prompt, and never reaches the client.
"""

from collections.abc import AsyncIterator, Sequence
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr

from identity_fakes import AUTHORIZATION, READER, TRUSTED
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.answering.generation import InvalidAnswerRequest
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.answering.vector_evidence import VectorEvidenceSource
from knowledge_service.app import create_app
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import (
    EmbeddingBatch,
    EmbeddingLimits,
    EmbeddingModelIdentity,
    EmbeddingUsage,
    InvalidEmbedding,
)
from knowledge_service.identifiers import GroupId, UserId
from knowledge_service.settings import Settings
from test_eligibility_predicate import changed_after_checking, wider_than_checked
from test_streaming import QUESTION, REQUEST_ID, Model, streamer
from test_vector_search_integration import (
    IDENTITY,
    INSERT_SOURCE,
    QUERY,
    SOURCE_ID,
    a_document,
    a_space,
)

MARKER = "SEEDED-SECRET-77c0"
OTHER_USER = UserId(UUID(int=0xB0B))
READERS_GROUP = next(iter(READER.group_ids))
OTHER_GROUP = GroupId(UUID(int=0xF))


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    """A migrated database with one Source and a current Embedding Space."""
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


class FixedModel:
    """An EmbeddingModel that embeds every Question as `QUERY`.

    `replies_as` and `vectors` make it answer like a broken provider.
    """

    identity: EmbeddingModelIdentity = IDENTITY
    limits = EmbeddingLimits(max_inputs=4, max_input_tokens=64, max_batch_tokens=256)

    def __init__(
        self,
        *,
        replies_as: EmbeddingModelIdentity = IDENTITY,
        vectors: int | None = None,
    ) -> None:
        self.calls = 0
        self.timeouts: list[float] = []
        self.replies_as = replies_as
        self.vectors = vectors

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    async def embed(
        self, texts: Sequence[str], *, request_id: UUID, timeout_seconds: float
    ) -> EmbeddingBatch:
        self.calls += 1
        self.timeouts.append(timeout_seconds)
        count = len(texts) if self.vectors is None else self.vectors
        return EmbeddingBatch(
            identity=self.replies_as,
            vectors=tuple(QUERY for _ in range(count)),
            usage=EmbeddingUsage(input_tokens=len(texts), latency_ms=0),
        )


class RecordingModel(Model):
    """The streaming test model, keeping every prompt it was sent."""

    def __init__(self) -> None:
        super().__init__()
        self.prompts: list[EvidencePrompt] = []

    def stream(
        self, prompt: EvidencePrompt, *, request_id: UUID, timeout_seconds: float
    ) -> Any:
        self.prompts.append(prompt)
        return super().stream(
            prompt, request_id=request_id, timeout_seconds=timeout_seconds
        )


def source(runtime: DatabaseRuntime, model: Any = None, **settings: Any) -> Any:
    return VectorEvidenceSource(
        runtime, model or FixedModel(), embed_timeout_seconds=5, **settings
    )


async def plant(runtime: DatabaseRuntime) -> tuple[Any, Any]:
    """A hidden exact match with the marker, and a farther granted Chunk."""
    hidden = await a_document(
        runtime,
        f"{MARKER}.txt",
        readers=[Subject.user(OTHER_USER), Subject.group(OTHER_GROUP)],
        vector=[1.0, 0.0, 0.0],
    )
    granted = await a_document(
        runtime,
        "granted.txt",
        readers=[Subject.group(READERS_GROUP)],
        vector=[0.6, 0.8, 0.0],
    )
    return hidden, granted


@pytest.mark.integration
async def test_the_reader_gets_only_evidence_it_may_see(
    runtime: DatabaseRuntime,
) -> None:
    _, granted = await plant(runtime)

    found = await source(runtime, limit=1).find(
        QUESTION.text, reader=READER, request_id=REQUEST_ID
    )

    assert [item.document_id for item in found.evidence.items] == [granted]
    assert found.signals.eligible == 1
    assert MARKER not in " ".join(item.text for item in found.evidence.items)


@pytest.mark.integration
async def test_a_reader_with_no_grant_finds_nothing(runtime: DatabaseRuntime) -> None:
    await plant(runtime)
    stranger = AuthorizationContext(user_id=UserId(UUID(int=0x5)))

    found = await source(runtime).find(
        QUESTION.text, reader=stranger, request_id=REQUEST_ID
    )

    assert (found.evidence.items, found.signals.eligible) == ((), 0)


@pytest.mark.integration
async def test_the_limit_bounds_the_evidence(runtime: DatabaseRuntime) -> None:
    for index in range(5):
        await a_document(
            runtime,
            f"public-{index}.txt",
            readers=[Subject.public()],
            vector=[1.0, float(index), 0.0],
        )

    found = await source(runtime, limit=3).find(
        QUESTION.text, reader=READER, request_id=REQUEST_ID
    )

    assert len(found.evidence.items) == found.signals.eligible == 3


@pytest.mark.integration
@pytest.mark.parametrize(
    "reader",
    [
        None,
        {"user_id": READER.user_id},
        READER.user_id,
        wider_than_checked(),
        changed_after_checking("group_ids", set[GroupId]()),
    ],
    ids=["none", "mapping", "user-id", "subclass", "changed"],
)
async def test_a_reader_that_is_not_a_context_is_refused_before_embedding(
    runtime: DatabaseRuntime, reader: object
) -> None:
    model = FixedModel()

    with pytest.raises(InvalidAnswerRequest):
        await source(runtime, model).find(
            QUESTION.text,
            reader=cast(AuthorizationContext, reader),
            request_id=REQUEST_ID,
        )

    assert model.calls == 0


@pytest.mark.integration
async def test_the_question_is_embedded_with_the_configured_timeout(
    runtime: DatabaseRuntime,
) -> None:
    model = FixedModel()

    await VectorEvidenceSource(runtime, model, embed_timeout_seconds=2.5).find(
        QUESTION.text, reader=READER, request_id=REQUEST_ID
    )

    assert model.timeouts == [2.5]


OTHER_MODEL = EmbeddingModelIdentity(provider="fake", model="older", dimensions=3)


@pytest.mark.integration
@pytest.mark.parametrize(
    "model",
    [FixedModel(replies_as=OTHER_MODEL), FixedModel(vectors=0), FixedModel(vectors=2)],
    ids=["another-model", "no-vector", "two-vectors"],
)
async def test_a_model_reply_that_is_not_one_vector_of_its_own_is_refused(
    runtime: DatabaseRuntime, model: FixedModel
) -> None:
    await plant(runtime)

    with pytest.raises(InvalidEmbedding):
        await source(runtime, model).find(
            QUESTION.text, reader=READER, request_id=REQUEST_ID
        )


@pytest.mark.parametrize(
    "settings",
    [
        {"limit": 0},
        {"limit": 21},
        {"limit": True},
        {"limit": 2.0},
        {"embed_timeout_seconds": 0},
        {"embed_timeout_seconds": float("nan")},
        {"embed_timeout_seconds": float("inf")},
        {"embed_timeout_seconds": True},
    ],
    ids=[
        "zero",
        "over-max",
        "bool-limit",
        "float-limit",
        "zero-timeout",
        "nan",
        "inf",
        "bool-timeout",
    ],
)
def test_a_source_with_a_bad_limit_or_timeout_is_refused(
    settings: dict[str, Any],
) -> None:
    arguments: dict[str, Any] = {"embed_timeout_seconds": 5, **settings}
    with pytest.raises(ValueError, match="must be"):
        VectorEvidenceSource(cast(Any, None), FixedModel(), **arguments)


@pytest.mark.integration
async def test_a_hidden_exact_match_never_reaches_the_model_or_the_client(
    runtime: DatabaseRuntime,
) -> None:
    await plant(runtime)
    model = RecordingModel()
    app = create_app(
        streamer=streamer(model, source=source(runtime)), authenticator=TRUSTED
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.post(
            "/v1/answer/stream", json={"text": QUESTION.text}, headers=AUTHORIZATION
        )

    assert response.status_code == 200
    assert len(model.prompts) == 1
    assert "granted" in model.prompts[0].input
    assert MARKER not in model.prompts[0].input
    assert MARKER not in response.text
```

Створіть `tests/test_vector_acl_adversarial_integration.py`:

```python
"""Adversarial corpora: unauthorized near-matches never leave the vector query.

Each seed builds a small corpus where Chunks the reader may not see are
usually nearer to the query than the ones it may see: exact matches, grants
to other users and groups, a group granted under the reader's own user ID,
revoked and tombstoned Documents. A plain Python model of the eligibility
rule says which Chunks the reader may see; the database must return exactly
those, and its nearest one, and never text from any other.
"""

import math
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.access import AuthorizationContext, Subject, SubjectKind
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, GroupId, UserId
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks
from knowledge_service.retrieval.vector_search import search_similar_chunks
from knowledge_service.settings import Settings
from test_vector_search_integration import (
    IDENTITY,
    INSERT_SOURCE,
    OBSERVED_AT,
    QUERY,
    SOURCE_ID,
    a_document,
    a_space,
    plain_text,
)

SEEDS = range(30)
MARKER = "HIDDEN"
ALICE = UserId(UUID(int=0xA1))
BOB = UserId(UUID(int=0xB0))
ENGINEERING = GroupId(UUID(int=0xE1))
FINANCE = GroupId(UUID(int=0xF1))
SUBJECTS = [
    Subject.public(),
    Subject.user(ALICE),
    Subject.user(BOB),
    Subject.group(ENGINEERING),
    Subject.group(FINANCE),
    # The same UUIDs under the other kind are other subjects.
    Subject.group(GroupId(ALICE.value)),
    Subject.user(UserId(ENGINEERING.value)),
]
READERS = [
    AuthorizationContext(user_id=ALICE, group_ids=frozenset({ENGINEERING})),
    AuthorizationContext(user_id=ALICE),
    AuthorizationContext(user_id=BOB, group_ids=frozenset({FINANCE})),
    AuthorizationContext(
        user_id=UserId(UUID(int=0xC0)), group_ids=frozenset({ENGINEERING, FINANCE})
    ),
]


@dataclass(frozen=True, slots=True)
class Planted:
    external_id: str
    readers: tuple[Subject, ...]
    vector: tuple[float, float, float]
    revoked: bool
    tombstoned: bool


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


def corpus(seed: int) -> list[Planted]:
    rng = random.Random(seed)  # noqa: S311 - a seeded, reproducible corpus
    planted: list[Planted] = []
    for index in range(rng.randint(6, 12)):
        readers = tuple(rng.sample(SUBJECTS, rng.randint(0, 3)))
        # Half the corpus sits exactly on the query; the rest is farther out.
        if rng.random() < 0.5:
            vector = (1.0, 0.0, 0.0)
        else:
            vector = (rng.uniform(-1, 1), rng.uniform(0.1, 1), rng.uniform(-1, 1))
        planted.append(
            Planted(
                external_id=f"doc-{index}-{MARKER if index % 2 else 'open'}",
                readers=readers,
                vector=vector,
                revoked=rng.random() < 0.15,
                tombstoned=rng.random() < 0.15,
            )
        )
    return planted


def may_read(reader: AuthorizationContext, item: Planted) -> bool:
    """The eligibility rule, written out in Python."""
    if item.revoked or item.tombstoned:
        return False
    for subject in item.readers:
        if subject.kind is SubjectKind.PUBLIC:
            return True
        if subject.kind is SubjectKind.USER and subject.identifier == reader.user_id:
            return True
        if subject.kind is SubjectKind.GROUP and subject.identifier in reader.group_ids:
            return True
    return False


def distance(vector: tuple[float, float, float]) -> float:
    norm = math.sqrt(sum(value * value for value in vector))
    return 1 - vector[0] / norm


async def store(
    runtime: DatabaseRuntime, planted: list[Planted]
) -> dict[str, DocumentId]:
    ids: dict[str, DocumentId] = {}
    for item in planted:
        ids[item.external_id] = await a_document(
            runtime, item.external_id, readers=item.readers, vector=item.vector
        )
    later = OBSERVED_AT + timedelta(hours=1)
    for item in planted:
        if item.revoked:
            await reconcile_document(
                runtime, plain_text(item.external_id), readers=[], observed_at=later
            )
    await tombstone_missing(
        runtime,
        source_id=SOURCE_ID,
        seen={item.external_id for item in planted if not item.tombstoned},
        observed_at=later,
    )
    return ids


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
async def test_only_eligible_chunks_come_back_however_near_the_others_are(
    runtime: DatabaseRuntime, seed: int
) -> None:
    planted = corpus(seed)
    ids = await store(runtime, planted)

    for reader in READERS:
        eligible = [item for item in planted if may_read(reader, item)]
        async with runtime.transaction() as connection:
            everything = await search_similar_chunks(
                connection, identity=IDENTITY, query=QUERY, context=reader, limit=100
            )
            nearest = await search_similar_chunks(
                connection, identity=IDENTITY, query=QUERY, context=reader, limit=1
            )

        returned = {candidate.document_id for candidate in everything.candidates}
        assert returned == {ids[item.external_id] for item in eligible}, seed
        assert not everything.truncated
        hidden = {item.external_id for item in planted} - {
            item.external_id for item in eligible
        }
        texts = " ".join(candidate.text for candidate in everything.candidates)
        assert not any(external_id in texts for external_id in hidden), seed
        if not eligible:
            assert nearest.candidates == ()
            continue
        (first,) = nearest.candidates
        best = min(distance(item.vector) for item in eligible)
        assert first.distance == pytest.approx(best, abs=1e-6), seed
        assert nearest.truncated is (len(eligible) > 1)


def test_the_corpora_are_adversarial() -> None:
    # Most seeds must put a Chunk the reader may not see nearest of all,
    # or the test above would prove little.
    hard = 0
    for seed in SEEDS:
        planted = corpus(seed)
        for reader in READERS:
            eligible = [item for item in planted if may_read(reader, item)]
            hidden = [item for item in planted if not may_read(reader, item)]
            if (
                eligible
                and hidden
                and min(map(distance, (i.vector for i in hidden)))
                <= min(map(distance, (i.vector for i in eligible)))
            ):
                hard += 1
    assert hard >= len(SEEDS)


@pytest.mark.integration
async def test_a_query_that_forgets_to_join_documents_still_sees_only_eligible_chunks(
    runtime: DatabaseRuntime,
) -> None:
    planted = corpus(0)
    ids = await store(runtime, planted)
    reader = READERS[0]
    statement = sa.select(chunks.c.document_id).where(eligible_chunks_predicate(reader))

    async with runtime.transaction() as connection:
        rows = (await connection.execute(statement)).scalars().all()

    eligible = {
        ids[item.external_id].value for item in planted if may_read(reader, item)
    }
    assert sorted(rows) == sorted(eligible)
```

`may_read` — еталонна модель: правило в кількох рядках Python, написане без погляду на SQL. Якщо база і ця функція колись розійдуться для будь-якого зерна і читача, одна з них помиляється, а зерно в повідомленні про падіння це відтворить.

**Перевірка:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m "integration or not integration" tests/test_vector_evidence_integration.py tests/test_vector_acl_adversarial_integration.py -q
```

Очікувано: `53 passed`. Маркер `-m "integration or not integration"` запускає і тести з базою, і кілька тестів у цих файлах, яким база не потрібна.

### Крок 7 — Без коду: зламайте навмисно

Цей крок виносить перевірку доступу з векторного запиту, показує, що падають саме потрібні тести, і повертає все назад.

**Дія 1:** у `vector_search_statement` у `src/knowledge_service/retrieval/vector_search.py` видаліть цей рядок із `.where(...)`:

```python
            eligibility,
```

**Дія 2:** запустіть

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_vector_acl_adversarial_integration.py tests/test_vector_evidence_integration.py -q
```

Очікувано: `33 failed, 11 passed`:

- `test_only_eligible_chunks_come_back_however_near_the_others_are`, усі 30 зерен
- `test_the_reader_gets_only_evidence_it_may_see`
- `test_a_reader_with_no_grant_finds_nothing`
- `test_a_hidden_exact_match_never_reaches_the_model_or_the_client`

Останній показує шкоду: прихований точний збіг стає Evidence, потрапляє в промпт моделі, а його текст може дійти до клієнта.

**Дія 3:** поверніть рядок і запустіть команду знову: `44 passed`.

**Дія 4:** порівняйте з останнім прогнозом в evidence-файлі й запишіть, чи він справдився.

### Крок 8 — Без коду: документи, перевірки, evidence і прогрес

Цей крок оновлює те, що зміна зробила неточним, доводить, що все працює, і записує результат. Invariant trace у threat model називав `M07-T07` майбутнім власником інваріанта 2, а `make docs-check` не приймає завершену задачу як власника, тож trace тепер називає тести цього уроку.

**Дія 1:** оновіть два документи.

У `docs/architecture.md`:

Знайдіть:

```markdown
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges the Evidence the request's reader may see before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |
```

Замініть на:

```markdown
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, at most one `checking` once the model is done, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges the Evidence the request's reader may see before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, sends `checking`, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), `VectorEvidenceSource` (`vector_evidence`, the stream's Evidence from PostgreSQL: checks the reader, embeds the Question, requires one vector from its own model, and runs the vector search with the request's Authorization Context, so eligibility is decided in the same SQL that ranks; no reranking, so its signals carry no score), `stream_failure` (`stream_failures`, one policy per failure kind and phase: before the headers an HTTP error with the M01 status and, for an outage or a limit, a `Retry-After` header; after them one terminal `error` event, the caller's to retry for an outage or a limit, a validation failure reported as internal; a database that is unreachable, overloaded, or too slow is an outage, any other database error a bug; every stream is recorded with its phase, one audit outcome, what became of its partial text, and for an abstention its reason and any defect of the reply, such as `invalid_citation`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `identity` | Identity, the seam to the OIDC provider | `OidcVerifier` (`oidc`, accepts an access token only when it is RS256-signed by a key the issuer publishes and names the exact issuer, the API audience, and a current time window within the allowed clock skew; returns `VerifiedToken` or raises `TokenRejected` with one `Rejection`, or `IdentityProviderUnavailable` when no usable keys are cached; discovery and keys are cached, an unknown key ID refreshes them at most once per interval, and cached keys outlive failed refreshes only up to a stale limit)), and `Authenticator` (`context`, the only way a request gets an `AuthorizationContext`: one `Authorization: Bearer` header, verified by `OidcVerifier`, from a trusted client (`azp`); the User and each plain full group path become UUIDv5 IDs scoped to the issuer, a `groups` claim over 64 names is refused, and a path with `~` or a hidden character is left out; refusals are `Unauthenticated` with an RFC 6750 error; it returns a `Caller`, the reader and, apart from it, the Application Roles from the token's realm roles) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |
```

У `docs/threat-model.md`:

**1.** Знайдіть:

```markdown

### Retrieval

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT | Adversarial suite (M07-T15) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query | ACLs in vector and lexical search (M07-T07, M07-T08) |
| A reranker or a ranking model is shown text the reader may not see | Only eligible Candidates reach any reranker | None |
| A Question proves a Document exists by the way it fails | Empty, unauthorized, and withdrawn read the same | Existence leaks (M07-T11) |
| A cache shared across readers serves one reader's Candidates or Answer to another | No cache of Candidates, Evidence, or Answers exists; Redis is for ephemeral coordination only (ADR 0011) | Adversarial suite covers caches (M07-T15) |

### Generation

```

Замініть на:

```markdown

### Retrieval

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT | Adversarial suite (M07-T15) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query, and seeded adversarial corpora check that the vector query returns exactly the eligible Chunks however near the others are | ACLs in lexical search (M07-T08) |
| A reader that is not a checked Authorization Context, such as a subclass, a look-alike, or a context changed after it was made, widens a search | `eligible_chunks_predicate` passes every reader through `checked_reader`, which refuses anything but an exact `AuthorizationContext` and checks its fields again; the predicate ties each Chunk to its own Document, so a statement that forgets the join narrows nothing; `VectorEvidenceSource` checks the reader before the Question is embedded and searches only with one vector from its own model | None |
| A reranker or a ranking model is shown text the reader may not see | Only eligible Candidates reach any reranker | None |
| A Question proves a Document exists by the way it fails | Empty, unauthorized, and withdrawn read the same | Existence leaks (M07-T11) |
| A cache shared across readers serves one reader's Candidates or Answer to another | No cache of Candidates, Evidence, or Answers exists; Redis is for ephemeral coordination only (ADR 0011) | Adversarial suite covers caches (M07-T15) |

### Generation

```

**2.** Знайдіть:

```markdown

Each row traces one invariant under "Invariants" in `docs/security.md` to its control and to at least one test owner: a test pytest collects, written `tests/<file>.py::<test>`, or a task not yet completed that will add one. `make docs-check` fails when an invariant has no row, a test owner no longer exists, or a named task is already completed. Owners in `*_integration.py` files run under `make test-integration`, which CI does not run yet.

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `M07-T07`, `M07-T08` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `M07-T10` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. No Evidence source over the database takes the reader yet (M07-T07, M07-T08).
- Groups from the identity provider become Group IDs by their path; a Source's groups and users become the same IDs only through its `PrincipalDirectory`, which is passed to `synchronize_source` by hand, since no stored Source configuration holds one yet. The count of unresolved principals is logged only when a pass completes. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
```

Замініть на:

```markdown

Each row traces one invariant under "Invariants" in `docs/security.md` to its control and to at least one test owner: a test pytest collects, written `tests/<file>.py::<test>`, or a task not yet completed that will add one. `make docs-check` fails when an invariant has no row, a test owner no longer exists, or a named task is already completed. Owners in `*_integration.py` files run under `make test-integration`, which CI does not run yet.

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_vector_acl_adversarial_integration.py::test_only_eligible_chunks_come_back_however_near_the_others_are`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client`, `M07-T08` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `M07-T10` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. `VectorEvidenceSource` searches the database as the reader, by meaning only: lexical search joins the answer stream in M07-T08, and nothing reranks its Evidence yet, so the abstention policy sees only whether anything was found.
- Groups from the identity provider become Group IDs by their path; a Source's groups and users become the same IDs only through its `PrincipalDirectory`, which is passed to `synchronize_source` by hand, since no stored Source configuration holds one yet. The count of unresolved principals is logged only when a pass completes. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
```

**Дія 2:** запустіть перевірки по черзі, із запущеним PostgreSQL:

```bash
uv run pytest tests/test_eligibility_predicate.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний прогін дав 2750 passed для `make test-unit`, на 17 більше, ніж після M07-T06, і 471 passed для `make test-integration`, на 44 більше.

**Дія 3:** заповніть evidence-файл:

- **Design:** де перевірка стоїть у запиті, `checked_reader`, самодостатній `JOIN` і що `VectorEvidenceSource` перевіряє й повертає.
- **Verification:** команди й кількості, навмисний злам і відомі обмеження з «Перш ніж почати».
- **Review:** підсумок рев'ю з «Перш ніж почати».
- **Decision:** чому читач перевіряється в предикаті, чому предикат робить `JOIN` сам і навіщо корпуси із зерном та еталонна модель; плюс щонайменше одне **фальсифіковане припущення** у формі «I assumed X. The run or review shows Y. So Z is wrong.»
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу як completed, додайте рядок M07-T07 у `docs/curriculum/PROGRESS.md`, поставте наступною задачею M07-T08 і закомітьте з `m07-t07: enforce acls in vector search`.

## Запитання для рефлексії

1. Потік відповіді викликає `VectorEvidenceSource.find(question, reader=...)`. Що йому більше не треба знати про вектори, Embedding Spaces чи те, як перевіряється доступ?
2. Хтось додає векторний індекс для швидкості, і база починає брати з індексу 40 найближчих векторів, а вже потім перевіряти доступ. Що побачить читач, якому дозволено мало Documents, і який із тестів цього уроку це помітить?
3. Колега каже: «читача перевірили, коли перевіряли токен; перевіряти його знову в кожному запиті — марна робота». Що ловить `checked_reader` такого, чого перша перевірка не може?
4. Тридцять зерен пройшли. Яка помилка все ще могла б сховатися від цих корпусів, і як би ви змінили генератор, щоб її шукати?
5. Alice у `/engineering`. Document, відкритий для `/engineering`, далеко від її Question, а відкритий лише для bob — точний збіг. Запишіть, що `find` з лімітом 1 поверне їй, що — bob, і що — carol, яка не має груп.

## Контрольний список завершення

- [ ] Evidence-файл містить інваріант і ваші прогнози, записані до зміни.
- [ ] `checked_reader` відкидає все, крім точного `AuthorizationContext` зі справними полями, а тести контексту досі проходять (89).
- [ ] `eligible_chunks_predicate` перевіряє читача і прив'язує кожен Chunk до його Document, а наявні тести пошуку досі проходять (78).
- [ ] `VectorEvidenceSource` перевіряє читача до побудови вектора, вимагає один вектор від своєї моделі і шукає від імені читача.
- [ ] Unit-тести перевірки читача проходять (8).
- [ ] Тести джерела Evidence і атакувальні тести проходять проти PostgreSQL (53).
- [ ] Ви запустили навмисний злам, побачили 33 тести, що впали, і скасували його.
- [ ] `docs/architecture.md` і `docs/threat-model.md` описують джерело та його межі, а trace називає нові тести.
- [ ] Усі перевірки проходять: format, lint, types, unit, integration, docs.
- [ ] Evidence, статус задачі, рядок у PROGRESS і коміт готові.

Коли напишете файли, надішліть їх або вивід помилок, і я перевірю їх за цим списком.
