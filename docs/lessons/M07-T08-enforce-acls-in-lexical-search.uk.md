# M07-T08 — Контроль доступу в лексичному пошуку

Англійська версія: [M07-T08-enforce-acls-in-lexical-search.md](M07-T08-enforce-acls-in-lexical-search.md)

Задача: [M07-T08 — Enforce ACLs in lexical search](../curriculum/milestones/M07-identity-and-authorization/M07-T08-enforce-acls-in-lexical-search.md)

## Що ви зробите

Цей урок доводить, що пошук за словами виконує рівно те саме правило доступу, що й пошук за змістом, для англійських, українських і змішаних питань, і ще до будь-якого ранжування чи підсвічування. Лексичний запит використовує ту саму перевірку доступу, що й векторний, ще з M05; бракувало одного спільного тестового контракту, який мусять пройти обидва види пошуку. Наприкінці ті самі тридцять ворожих корпусів проходять через шість пошуків, і кожен мусить повернути рівно те, що читачеві можна бачити.

- **Один контракт, шість пошуків:** векторний; англійський; український за ключовими словами; український із синтаксисом вебпошуку; змішаний; і змішаний із синтаксисом. Маршрут кожного перевіряється, тож контракт не може тихо пропустити жоден.
- **Те саме правило, записане один раз:** `may_read`, просте Python-формулювання того, хто що може бачити, судить кожен пошук.
- **До ранжування й підсвічування:** SQL-тест показує, що перевірка доступу стоїть усередині запиту з ранжуванням, до його сортування й ліміту, а підсвічування працює лише над рядками, що лишилися, для кожної конфігурації і для обох способів читати українську.
- **Без змін у production-коді.** Код пошуку вже так працює; робота уроку — доказ, а навмисний злам показує, що доказ діє.

| Задача вимагає | Де це відбувається |
|---|---|
| Identical permission semantics for English and Ukrainian lexical branches | Крок 3: спільний контракт проганяє кожну гілку проти `may_read` |
| Before ranking or highlighting | Крок 2: тест скомпільованого SQL |
| Tests: success, highest-risk edge, typed failure or denial | Кроки 2 і 3 |
| Shared security contract tests run against dense and lexical search | Крок 3: `tests/test_search_security_contract_integration.py` |
| Authorization preserved before data leaves storage; no seeded text | Крок 3: жодної назви прихованого документа в тексті, заголовку, external ID, heading чи підсвічуванні |
| Update only what the change made inaccurate | Крок 5: `docs/threat-model.md` |

У цьому уроці є код, але лише тести: їх пишуть кроки 2 і 3. Кроки 1, 4 і 5 без коду, хоча крок 5 змінює один документ.

Терміни цього уроку:

- **Лексичний пошук:** пошук за словами через повнотекстовий пошук PostgreSQL. Англійський використовує основи слів («leaves» знаходить «leave»); український — слова як написано, у нижньому регістрі.
- **Ключові слова (topic words):** для українського питання без синтаксису пошуку — питання без службових слів; Chunk має містити щонайменше два з них.
- **Як написано (as typed):** питання із синтаксисом вебпошуку (`"фраза в лапках"`, `or`, `-слово`) читається буквально. Це інша гілка SQL.
- **Підсвічування (highlighting):** `ts_headline` позначає, де в тексті Chunk трапляються слова питання, щоб інтерфейс міг їх показати. Воно читає весь текст, тож має бачити лише дозволені рядки.
- **Контракт безпеки:** один набір правил, який мусить пройти кожен пошук; записаний один раз і прогнаний проти кожного пошуку, як одна перевірка безпеки для всіх дверей будівлі.
- **Маршрут (route):** з якими конфігураціями зіставляється питання: англійська, simple (українська) чи обидві.

## Перш ніж почати

Цей розділ пояснює рішення за тестами, щоб кожен крок був зрозумілим, коли ви до нього дійдете.

- **Чому без змін у коді.** `lexical_search_statement` ставить `eligible_chunks_predicate` у WHERE свого підзапиту з ранжуванням, поруч з умовою збігу, до `ORDER BY` і `LIMIT`; зовнішній запит лише викликає `ts_headline` для рядків, які цей підзапит лишив. M07-T07 змусив цей предикат перевіряти читача і робити `JOIN` самостійно. Рев'ю нижче дуже старався зламати лексичний пошук і не зміг.
- **Чому контракт, а не більше прикладів.** Векторні й лексичні тести писалися окремо, кожен зі своїми випадками. Контракт формулює правила один раз і проганяє їх проти кожного пошуку, тож новий пошук чи нова гілка старого тримаються рівно того самого стандарту.
- **Кожна гілка, а не лише кожна функція.** Український пошук будує одну SQL-умову для ключових слів і іншу — для тексту із синтаксисом. Перевірку, прибрану лише з однієї гілки, пропустив би будь-який тест, що цю гілку не проходить, тож у контракті є український пошук «як написано» і змішаний «як написано», а SQL-тест компілює кожну комбінацію.
- **Корпуси складні й для слів.** Приховані Documents часто містять слова питання в заголовку і повторені в тексті, тож вони виграли б ранжування, якби перевірки не було. Окремий тест доводить це за вектором і за словами в більшості пар «зерно × читач».
- **Найкращий результат звіряється з правилом, а не сам із собою.** Для векторного пошуку перший результат має бути так само близьким, як найближчий Document, який читачеві можна. Для лексичного він має містити слова питання в заголовку, якщо хоч один дозволений Document їх там має, бо збіг у заголовку завжди вищий за збіги в тексті.
- **Він замінює корпусний тест минулого уроку.** `tests/test_vector_acl_adversarial_integration.py` з M07-T07 покривав лише векторний пошук. Контракт покриває його і більше, тож цей урок видаляє той файл і переносить два його додаткові тести в контракт.
- **Що знайшов рев'ю.** Незалежний атакувальний рев'ю прогнав 72 ворожі питання з п'ятьма налаштуваннями мови і двома лімітами: лапки, `or`, виключення, лише службові слова, 33 дефіси, розкладені літери, варіанти апострофа, синтаксис tsquery, невидимі символи й символи справа наліво, питання на 1000 символів. Він порівняв результати, оцінки, підсвічування й `truncated` до і після додавання прихованих Documents, і 6,7 МБ логів рівня DEBUG. Нічого прихованого не витекло, і ніщо не залежало від прихованих рядків. Він знайшов такі прогалини в тестах, і всі виправлено:
  - Контракт ніколи не проходив українську гілку «як написано». Мутація, що прибирала перевірку лише там, пережила всі тести. Тепер у контракті є `ukrainian_typed` і `both_typed`, а SQL-тест компілює текст у лапках.
  - Контракт звіряв найкращий результат лише з власним довшим списком, що пройде й неправильний порядок сортування. Тепер він звіряє з еталонною моделлю.
- **Відомі обмеження.** Sparse-пошук, прототип BGE-M3, використовує той самий предикат, але поза контрактом. `load_searchable_chunks` у `persistence.chunks` читає Chunks без перевірки доступу; у `src` її ніхто не викликає. Кожен Document корпусу має один Chunk і без заголовків розділів. Лексичний і гібридний пошук ще не підключені до потоку відповіді.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої прогнози

Перш ніж щось змінювати, запишіть, що має бути правдою наприкінці уроку, і що ви очікуєте. Крок 4 перевіряє останній прогноз.

Створіть `docs/evidence/M07-T08-enforce-acls-in-lexical-search.md`:

```markdown
# M07-T08 — Enforce ACLs in lexical search evidence

## Invariant

Every search by words, English, Ukrainian by topic words, Ukrainian as typed,
or both at once, applies the same eligibility rule as vector search: a Chunk
is matched, ranked, counted, and highlighted only if its Document is
available and granted to everyone, to the reader, or to one of the reader's
Groups, decided in the ranked query's WHERE before its ORDER BY and LIMIT.
One security contract runs the same adversarial corpora through vector and
lexical search, and each must return exactly what the reference rule allows,
with no hidden title, text, or highlight.

## Expected failing check

Before the change, `uv run pytest tests/test_search_security_contract_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_search_security_contract_integration.py`.

## Predictions

- What English lexical search with limit 1 returns to a reader when a hidden Document has the question's words in its title and the reader's only Document has them in its text:
- Whether `ts_headline` ever reads the text of that hidden Document:
- Whether `"щорічна відпустка"` in quotes and `щорічна відпустка` without them run the same SQL condition, and why that matters for testing:
- Which tests fail if `eligibility` is removed from the ranked query's WHERE in `lexical_search_statement`:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після двокрапки в кожному прогнозі є ваша відповідь.

### Крок 2 — Крок із кодом: перевірка доступу йде до ранжування й підсвічування

**Простими словами:** цей тест будує SQL для кожної форми лексичного пошуку, не запускаючи його, і читає текст запиту. Він перевіряє, що гранти читаються всередині запиту, який сортує й обрізає результати, ще до сортування, а підсвічування відбувається лише в зовнішньому запиті, над тим, що лишилося. Він покриває англійську, українську й обидві разом, кожну зі звичайними словами і з синтаксисом пошуку.

У `tests/test_lexical_search.py`:

**1.** Знайдіть:

```python
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import UserId
from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_routing import LexicalConfiguration
from knowledge_service.retrieval.lexical_search import (
    MAX_QUERY_CHARACTERS,
    MAX_SIMPLE_WORDS,
    UKRAINIAN_STOP_WORDS,
    InvalidLexicalQuery,
```

Замініть на:

```python
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.identifiers import UserId
from knowledge_service.language import Language
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.retrieval.lexical_routing import LexicalConfiguration
from knowledge_service.retrieval.lexical_search import (
    MAX_QUERY_CHARACTERS,
    MAX_SIMPLE_WORDS,
    UKRAINIAN_STOP_WORDS,
    InvalidLexicalQuery,
```

**2.** Знайдіть:

```python
    # the rows LIMIT kept, never on every match.
    assert outer.count("ts_headline(") == 2
    assert "ts_headline" not in ranked
    assert "LIMIT" in ranked


def test_ties_are_broken_by_chunk_id_before_the_limit_cuts() -> None:
    statement = english_search_statement(text="leave", eligibility=sa.true(), limit=5)

    sql = str(statement.compile(dialect=postgresql.dialect()))
    ranked = sql.split("FROM (SELECT", 1)[1]
    order = ranked[ranked.index("ORDER BY") : ranked.index("LIMIT")]
```

Замініть на:

```python
    # the rows LIMIT kept, never on every match.
    assert outer.count("ts_headline(") == 2
    assert "ts_headline" not in ranked
    assert "LIMIT" in ranked


@pytest.mark.parametrize(
    "text",
    ["leave", '"leave" -sick', "щорічна відпустка", '"щорічна відпустка"'],
    ids=["words", "syntax", "topic-words", "typed"],
)
@pytest.mark.parametrize(
    "configurations",
    [
        (LexicalConfiguration.ENGLISH,),
        (LexicalConfiguration.SIMPLE,),
        (LexicalConfiguration.ENGLISH, LexicalConfiguration.SIMPLE),
    ],
    ids=["english", "ukrainian", "both"],
)
def test_the_permission_check_comes_before_ranking_and_highlighting(
    configurations: tuple[LexicalConfiguration, ...], text: str
) -> None:
    # Each configuration, and `simple` both by topic words and as typed,
    # builds its own match; the check must sit beside every one of them.
    statement = lexical_search_statement(
        configurations,
        text=text,
        eligibility=eligible_chunks_predicate(CONTEXT),
        limit=5,
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))
    outer, rest = sql.split("FROM (SELECT", 1)
    ranked = rest.split(") AS ranked", 1)[0]

    # The grants are read in the ranked query's WHERE, before its own last
    # ORDER BY and LIMIT; the outer query only highlights the rows kept.
    assert "access_grants" not in outer
    assert ranked.index("access_grants") < ranked.rindex("ORDER BY")
    assert ranked.rindex("ORDER BY") < ranked.rindex("LIMIT")
    assert outer.count("ts_headline(") == len(configurations)


def test_ties_are_broken_by_chunk_id_before_the_limit_cuts() -> None:
    statement = english_search_statement(text="leave", eligibility=sa.true(), limit=5)

    sql = str(statement.compile(dialect=postgresql.dialect()))
    ranked = sql.split("FROM (SELECT", 1)[1]
    order = ranked[ranked.index("ORDER BY") : ranked.index("LIMIT")]
```

Тест проходить одразу, бо код уже правильний; він існує, щоб упасти того дня, коли хтось перенесе перевірку. Крок 4 показує, як він падає.

**Перевірка:** `uv run pytest tests/test_lexical_search.py -q` дає `80 passed`.

### Крок 3 — Крок із кодом: один контракт безпеки для кожного пошуку

**Простими словами:** цей файл будує тридцять малих ворожих корпусів, де Documents, яких читачеві не можна, зазвичай найкраще збігаються і за змістом, і за словами, і проганяє кожен через шість пошуків. Для чотирьох різних читачів кожен пошук має повернути рівно ті Documents, які дозволяє просте Python-правило, поставити першим правильний і ніколи не показати назву прихованого Document у жодному тексті, заголовку, heading чи підсвічуванні. Він замінює векторну версію минулого уроку.

| Частина | Що робить |
|---|---|
| `Planted`, `corpus` | Один корпус із зерном: хто може читати кожен Document, його вектор, чи є слова в заголовку, скільки разів текст їх повторює, і чи його відкликано або видалено |
| `may_read` | Еталонне правило в кількох рядках Python |
| `SEARCHES` | Шість пошуків; кожен лексичний перевіряє свій маршрут |
| `store` | Публікує, будує вектори, відкликає й видаляє документи корпусу |
| `shown` | Усе в Candidate, що може прочитати людина, разом із підсвічуваннями |
| Три тести | Контракт; доказ, що корпуси складні; запит, що забув `JOIN` |

Видаліть векторний файл минулого уроку, який замінює цей контракт:

```bash
git rm tests/test_vector_acl_adversarial_integration.py
```

Створіть `tests/test_search_security_contract_integration.py`:

```python
"""One security contract, run against every search: by meaning and by words.

Each seed builds a small corpus where Chunks the reader may not see are
usually the best matches: exact vector matches, and the query's words in
their titles and repeated in their text. A plain Python statement of the
eligibility rule, `may_read`, says which Documents each reader may see. Every
search under contract must then return exactly those, rank only those, and
never show text, a title, or a highlight from any other:

- `dense`: `search_similar_chunks`, by vector distance;
- `english`: `search_lexical_chunks` routed to the `english` configuration;
- `ukrainian`: routed to the `simple` configuration, read by topic words;
- `ukrainian_typed`: the same with web-search syntax, read as typed;
- `both`: a mixed-language question, routed to both at once;
- `both_typed`: the same with a quoted phrase and an excluded word.

Each search's best result must also be the reader's best match by the
reference model: the nearest vector, or a title match when there is one.
"""

import math
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext, Subject, SubjectKind
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import DocumentId, GroupId, UserId
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.chunks import load_chunks
from knowledge_service.persistence.embeddings import (
    insert_chunk_embeddings,
    load_embedding_space_id,
)
from knowledge_service.persistence.schema import chunks
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.lexical_routing import LexicalConfiguration
from knowledge_service.retrieval.lexical_search import (
    LexicalCandidate,
    search_lexical_chunks,
)
from knowledge_service.retrieval.vector_search import search_similar_chunks
from knowledge_service.settings import Settings
from test_vector_search_integration import (
    IDENTITY,
    INSERT_SOURCE,
    OBSERVED_AT,
    QUERY,
    SOURCE_ID,
    a_space,
)

SEEDS = range(30)
WORDS = "annual leave щорічна відпустка"
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
    """One Document: who may read it, how well it matches, and its fate."""

    name: str
    readers: tuple[Subject, ...]
    vector: tuple[float, float, float]
    in_title: bool
    repeats: int
    revoked: bool
    tombstoned: bool

    @property
    def title(self) -> str:
        return f"{self.name} {WORDS}" if self.in_title else self.name

    @property
    def text(self) -> str:
        return f"{self.name}. " + " ".join([WORDS] * self.repeats)


type Search = Callable[
    [AsyncConnection, AuthorizationContext, int],
    Awaitable[tuple[Sequence[Candidate], bool]],
]


async def dense(
    connection: AsyncConnection, reader: AuthorizationContext, limit: int
) -> tuple[Sequence[Candidate], bool]:
    found = await search_similar_chunks(
        connection, identity=IDENTITY, query=QUERY, context=reader, limit=limit
    )
    return found.candidates, found.truncated


def lexical(text: str, configurations: tuple[LexicalConfiguration, ...]) -> Search:
    async def search(
        connection: AsyncConnection, reader: AuthorizationContext, limit: int
    ) -> tuple[Sequence[Candidate], bool]:
        found = await search_lexical_chunks(
            connection, text=text, context=reader, limit=limit
        )
        # The contract is only worth its name if each route really runs.
        assert found.route.configurations == configurations
        return found.candidates, found.truncated

    return search


ENGLISH = LexicalConfiguration.ENGLISH
SIMPLE = LexicalConfiguration.SIMPLE
SEARCHES: dict[str, Search] = {
    "dense": dense,
    "english": lexical("annual leave", (ENGLISH,)),
    "ukrainian": lexical("щорічна відпустка", (SIMPLE,)),
    "ukrainian_typed": lexical('"щорічна відпустка"', (SIMPLE,)),
    "both": lexical(WORDS, (ENGLISH, SIMPLE)),
    "both_typed": lexical('"annual leave" щорічна відпустка -ніщо', (ENGLISH, SIMPLE)),
}


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
        # Half the corpus sits exactly on the query vector.
        if rng.random() < 0.5:
            vector = (1.0, 0.0, 0.0)
        else:
            vector = (rng.uniform(-1, 1), rng.uniform(0.1, 1), rng.uniform(-1, 1))
        planted.append(
            Planted(
                name=f"doc{index}x{seed}",
                readers=tuple(rng.sample(SUBJECTS, rng.randint(0, 3))),
                vector=vector,
                in_title=rng.random() < 0.5,
                repeats=rng.randint(1, 4),
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


def distance(item: Planted) -> float:
    norm = math.sqrt(sum(value * value for value in item.vector))
    return 1 - item.vector[0] / norm


def strength(item: Planted) -> tuple[bool, int]:
    """How strongly an item's words match: a title match, then repeats."""
    return item.in_title, item.repeats


def normalized(item: Planted) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id=item.name,
            version="v1",
            media_type="text/plain",
            content=item.text.encode(),
            access=SourceAccess(public=True),
            metadata=SourceMetadata(
                title=item.title, modified_at=OBSERVED_AT, size_bytes=1
            ),
        ),
        source_id=SOURCE_ID,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def store(
    runtime: DatabaseRuntime, planted: list[Planted]
) -> dict[str, DocumentId]:
    """Publish the corpus, embed each Chunk, then revoke and tombstone."""
    ids: dict[str, DocumentId] = {}
    for item in planted:
        reconciled = await reconcile_document(
            runtime, normalized(item), readers=item.readers, observed_at=OBSERVED_AT
        )
        document_id = reconciled.document.document_id
        async with runtime.transaction() as connection:
            space_id = await load_embedding_space_id(connection, IDENTITY)
            assert space_id is not None
            (chunk,) = await load_chunks(connection, document_id=document_id)
            await insert_chunk_embeddings(
                connection,
                space_id=space_id,
                dimensions=IDENTITY.dimensions,
                vectors={chunk.chunk_id: item.vector},
                created_at=OBSERVED_AT,
            )
        ids[item.name] = document_id
    later = OBSERVED_AT + timedelta(hours=1)
    for item in planted:
        if item.revoked:
            await reconcile_document(
                runtime, normalized(item), readers=[], observed_at=later
            )
    await tombstone_missing(
        runtime,
        source_id=SOURCE_ID,
        seen={item.name for item in planted if not item.tombstoned},
        observed_at=later,
    )
    return ids


def shown(candidate: Candidate) -> str:
    """Everything a Candidate carries that a person could read."""
    parts = [candidate.text, candidate.title, candidate.external_id]
    parts.append(candidate.heading_path or "")
    if isinstance(candidate, LexicalCandidate):
        parts += [candidate.text[h.start : h.end] for h in candidate.highlights]
    return " ".join(parts)


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("name", list(SEARCHES))
async def test_every_search_returns_exactly_what_the_reader_may_see(
    runtime: DatabaseRuntime, name: str, seed: int
) -> None:
    search = SEARCHES[name]
    planted = corpus(seed)
    ids = await store(runtime, planted)

    by_id = {ids[item.name]: item for item in planted}
    for reader in READERS:
        allowed = [item for item in planted if may_read(reader, item)]
        eligible = {item.name for item in allowed}
        hidden = {item.name for item in planted} - eligible
        async with runtime.transaction() as connection:
            everything, more = await search(connection, reader, 100)
            first, truncated = await search(connection, reader, 1)

        assert {c.document_id for c in everything} == {ids[n] for n in eligible}, seed
        assert not more, seed
        text = " ".join(shown(candidate) for candidate in everything)
        assert not any(hidden_name in text for hidden_name in hidden), seed
        assert truncated is (len(eligible) > 1), seed
        if not allowed:
            assert first == (), seed
            continue
        # The best match among what the reader may see, by the reference model.
        (best,) = first
        top = by_id[best.document_id]
        if name == "dense":
            nearest = min(map(distance, allowed))
            assert distance(top) == pytest.approx(nearest, abs=1e-6), seed
        else:
            # A title match outranks any number of matches in the text; past
            # that, `ts_rank` saturates, so repeats may tie.
            assert top.in_title or not any(item.in_title for item in allowed), seed


def test_the_corpora_are_adversarial() -> None:
    # In most seed and reader pairs, a Document the reader may not see must
    # match at least as well as every one it may see, by vector and by
    # words, or the contract above would prove little.
    by_vector = by_words = 0
    for seed in SEEDS:
        planted = corpus(seed)
        for reader in READERS:
            eligible = [item for item in planted if may_read(reader, item)]
            hidden = [item for item in planted if not may_read(reader, item)]
            if not eligible or not hidden:
                continue
            if min(map(distance, hidden)) <= min(map(distance, eligible)):
                by_vector += 1
            if max(map(strength, hidden)) >= max(map(strength, eligible)):
                by_words += 1
    assert by_vector >= len(SEEDS)
    assert by_words >= len(SEEDS)


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

    eligible = {ids[item.name].value for item in planted if may_read(reader, item)}
    assert sorted(rows) == sorted(eligible)
```

**Перевірка:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m "integration or not integration" tests/test_search_security_contract_integration.py -q
```

Очікувано: `182 passed`: шість пошуків на тридцять зерен, перевірка корпусів і тест забутого `JOIN`. Це триває близько хвилини.

### Крок 4 — Без коду: зламайте навмисно

Цей крок прибирає перевірку доступу з лексичного запиту, показує, що падають обидва види тестів, і повертає все назад.

**Дія 1:** у `lexical_search_statement` у `src/knowledge_service/retrieval/lexical_search.py` замініть

```python
        .where(matched, eligibility)
```

на

```python
        .where(matched)
```

**Дія 2:** запустіть

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m "integration or not integration" tests/test_search_security_contract_integration.py tests/test_lexical_search.py -q
```

Очікувано: `162 failed, 100 passed`:

- `test_the_permission_check_comes_before_ranking_and_highlighting`, усі 12 випадків
- `test_every_search_returns_exactly_what_the_reader_may_see`, усі 30 зерен кожного з п'яти лексичних пошуків; 30 векторних досі проходять

**Дія 3:** поверніть перевірку і запустіть команду знову: `262 passed`.

**Дія 4:** порівняйте з останнім прогнозом в evidence-файлі й запишіть, чи він справдився.

### Крок 5 — Без коду: документи, перевірки, evidence і прогрес

Цей крок оновлює те, що зміна зробила неточним, доводить, що все працює, і записує результат. Threat model називав видалений тестовий файл і `M07-T08` власниками інваріанта 2 і казав, що лексичний пошук приєднається до потоку відповіді в цьому уроці; змінюються всі три речі.

**Дія 1:** оновіть threat model.

У `docs/threat-model.md`:

**1.** Знайдіть:

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

Замініть на:

```markdown

### Retrieval

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT | Adversarial suite (M07-T15) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query, and one security contract runs the same seeded adversarial corpora through vector search and English, Ukrainian, and mixed lexical search, by topic words and with web-search syntax: each must return exactly the eligible Chunks, its best result must be the reader's best match, and no hidden title, text, or highlight may appear, however well the others match | Authorization adversarial suite (M07-T15) |
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
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
```

Замініть на:

```markdown

Each row traces one invariant under "Invariants" in `docs/security.md` to its control and to at least one test owner: a test pytest collects, written `tests/<file>.py::<test>`, or a task not yet completed that will add one. `make docs-check` fails when an invariant has no row, a test owner no longer exists, or a named task is already completed. Owners in `*_integration.py` files run under `make test-integration`, which CI does not run yet.

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `M07-T10` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- No production entry point assembles the app yet: whoever does must pass an `Authenticator`, or both answer endpoints answer 503. `VectorEvidenceSource` searches the database as the reader, by meaning only: lexical and hybrid retrieval are not wired into the answer stream yet, and nothing reranks its Evidence yet, so the abstention policy sees only whether anything was found.
- Groups from the identity provider become Group IDs by their path; a Source's groups and users become the same IDs only through its `PrincipalDirectory`, which is passed to `synchronize_source` by hand, since no stored Source configuration holds one yet. The count of unresolved principals is logged only when a pass completes. A Keycloak group whose path holds `~` or a hidden character gives its members nothing.
- `/docs`, `/redoc`, and `/openapi.json` answer anyone and list the API's routes; whoever builds the production entry point decides whether to serve them.
- A malformed or oversized realm roles claim refuses the whole token, as a malformed `groups` claim does, so its User cannot read either.
- Sparse search (the BGE-M3 prototype) applies the same predicate but is outside the shared security contract, and `load_searchable_chunks` in `persistence.chunks` reads Chunks with no permission check; nothing in `src` calls it.
- Only `ask_questions` has an entry point that checks it. Configuring Sources, starting Synchronization Runs, and running evaluations are scripts a developer runs with no identity; each operation gets its role check with its entry point.
- `OidcVerifier` sets no maximum token lifetime and does not check a token's type: Keycloak's ID tokens are refused only because their audience is the client, not the API.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
```

**Дія 2:** запустіть перевірки по черзі, із запущеним PostgreSQL:

```bash
uv run pytest tests/test_lexical_search.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний прогін дав 2762 passed для `make test-unit`, на 12 більше, ніж після M07-T07, і 621 passed для `make test-integration`, на 150 більше.

**Дія 3:** заповніть evidence-файл:

- **Design:** де перевірка стоїть у лексичному запиті, чому код не змінився, і що контракт проганяє й перевіряє.
- **Verification:** команди й кількості, навмисний злам і відомі обмеження з «Перш ніж почати».
- **Review:** підсумок рев'ю з «Перш ніж почати».
- **Decision:** чому спільний контракт, чому кожна гілка потребує свого пошуку в ньому, і чому найкращий результат звіряється з еталонною моделлю; плюс щонайменше одне **фальсифіковане припущення** у формі «I assumed X. The run or review shows Y. So Z is wrong.»
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу як completed, додайте рядок M07-T08 у `docs/curriculum/PROGRESS.md`, поставте наступною задачею M07-T09 і закомітьте з `m07-t08: enforce acls in lexical search`.

## Запитання для рефлексії

1. Наступного року додають новий пошук, скажімо за підписами до зображень. Що контракт дає його автору задарма, і яку одну річ треба додати, щоб пошук був покритий?
2. Хтось пришвидшує підсвічування: рахує `ts_headline` для кожного рядка зі збігом, а вже потім сортує. Що може піти не так для доступу і для вартості, і який тест це впіймає?
3. Український пошук має дві гілки SQL. Чому набір тестів із десятками українських випадків усе одно пропустив перевірку, прибрану лише з однієї з них?
4. Контракт перевіряє, що перший лексичний результат має збіг у заголовку, якщо його має хоч один дозволений Document. Чому не перевіряти точний порядок `ts_rank`?
5. Прихований Document має заголовок «Щорічна відпустка 2027», і alice не може його читати. Запишіть, що кожен із шести пошуків поверне їй, якщо її єдиний Document згадує відпустку один раз у тексті.

## Контрольний список завершення

- [ ] Evidence-файл містить інваріант і ваші прогнози, записані до зміни.
- [ ] SQL-тест показує перевірку до ранжування й підсвічування для кожної конфігурації і обох способів читати українську (80 у файлі).
- [ ] Векторний корпусний файл із M07-T07 видалено, а спільний контракт проходить проти PostgreSQL (182).
- [ ] Ви запустили навмисний злам, побачили 162 тести, що впали, і скасували його.
- [ ] `docs/threat-model.md` називає нові тести, контракт і його межі.
- [ ] Усі перевірки проходять: format, lint, types, unit, integration, docs.
- [ ] Evidence, статус задачі, рядок у PROGRESS і коміт готові.

Коли напишете файли, надішліть їх або вивід помилок, і я перевірю їх за цим списком.
