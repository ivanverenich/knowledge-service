# M07-T10 — Пріоритет відкликання доступу

Англійська версія: [M07-T10-prioritize-permission-revocation.md](M07-T10-prioritize-permission-revocation.md)

Задача: [M07-T10 — Prioritize permission revocation](../curriculum/milestones/M07-identity-and-authorization/M07-T10-prioritize-permission-revocation.md)

## Що ви зробите

Цей урок робить забирання доступу першою справою Synchronization Run і доводить, що запит, який уже виконується, не може його «обігнати». Досі прохід обробляв файли по черзі: розібрати, порізати на чанки, а потім записати вміст і доступ разом. Файл, у якого поменшало читачів, чекав за кожним повільним файлом перед ним на сторінці, і весь цей час лишався доступним для вилучених читачів.

- **Спершу відкликання:** кожна сторінка тепер має дві фази. Спершу один запит читає, хто зараз може читати кожен Document сторінки, і кожен файл, що втрачає читача, одразу звужується у власній короткій транзакції з новою Authorization Version. Лише потім будь-який файл сторінки розбирається, ріжеться на чанки й узгоджується.
- **Лише там, де треба:** файл, що нікого не вилучає, у першій фазі не чіпається, тож незмінний прохід коштує одне додаткове читання на сторінку, а не блокування на кожен файл.
- **Один запис на файл на сторінку:** файл, запропонований на сторінці двічі, рахується один раз, як запропоновано востаннє, тож вилучений читач навіть на мить не повертається.
- **Відмова для запитів у процесі:** тест ставить питання, поки прохід ще розбирає попередній файл, а інший забирає доступ посеред Conversation; кожне читання після звуження, у кожній точці узгодженості, нічого не дає.

| Задача вимагає | Де це відбувається |
|---|---|
| Publish restrictive Authorization Versions before non-security work | Кроки 2–4: `load_readers`, `removes_readers` і дві фази сторінки в `synchronize_source` |
| Invalidate in-flight access at defined consistency points | Уже є: пошук, перевірка Evidence перед моделлю, перевірка Citations перед рішенням і History; крок 5 доводить їх проти відкликання, що відбувається просто зараз |
| A concurrency test changes grants during a Conversation and proves later reads fail closed | Крок 5: `tests/test_revocation_integration.py` |
| No seeded sensitive text in telemetry | Прохід пише в лог лише кількості; рев'ю перевірив це на рівні DEBUG |
| Update only what the change made inaccurate | Крок 7: `docs/architecture.md`, `docs/threat-model.md` |

У цьому уроці є код: кроки 2–5 пишуть код і тести. Кроки 1, 6 і 7 без коду, хоча крок 7 змінює два документи.

Терміни цього уроку:

- **Authorization Version:** лічильник на кожному Document, що зростає щоразу, коли змінюється, хто може його читати, окремо від версії вмісту.
- **Обмежувальна зміна (restrictive change):** зміна, що вилучає щонайменше одного читача. Додавання читачів не обмежувальне і може зачекати.
- **Звуження (narrowing):** застосування лише обмежувальної частини зміни: лишити читачів, яких називають і старий, і новий доступ. Як викреслити ім'я зі списку гостей ще до того, як передрукувати меню.
- **Точка узгодженості (consistency point):** момент, коли запит знову питає базу, чи може читач щось бачити: сам пошук, перевірка Evidence перед моделлю, перевірка Citations перед рішенням і History.
- **Fail closed:** якщо доступ під сумнівом — нічого не показувати.
- **Тест конкурентності:** тест, у якому дві речі відбуваються одночасно, тут — прохід і запит, і результат має бути правильним, хоч би що закінчилося першим.

## Перш ніж почати

Цей розділ пояснює рішення за кодом, щоб кожен крок був зрозумілим, коли ви до нього дійдете.

- **Чому відкликання особливе.** Запізнілий новий читач — це затримка; запізнілий вилучений читач — це витік. Тож вилучення йдуть поза чергою, а додавання чекають на звичайне узгодження файлу разом із його новим вмістом.
- **Повторно використати `narrow_access`.** M07-T06 написав її для файлів, які не розбираються: лишити читачів, яких називають і старий, і новий доступ, записати це у відбиток доступу, підняти Authorization Version. Та сама функція, викликана для кожного файлу, що втрачає читача, до будь-якого розбору, — це якраз «спершу опублікувати обмежувальну версію».
- **Посторінково, а не на весь прохід.** Прохід читає джерело сторінку за сторінкою; буферизація всіх сторінок наперед подвоїла б пам'ять і виклики до джерела. Тож відкликання на сторінці 2 досі чекає на роботу сторінки 1. Threat model так і пише.
- **Прочитати один раз, блокувати лише за потреби.** Рев'ю виміряв першу версію, яка звужувала кожен файл: незмінний прохід був у 2–8 разів повільнішим, бо кожен файл брав блокування рядка. Тепер `load_readers` одним запитом читає читачів кожного Document сторінки, а `removes_readers` вибирає лише файли, що когось втрачають.
- **Точки узгодженості вже були.** Пошук фільтрує за грантами, M07-T09 перечитує Evidence перед моделлю і Citations перед рішенням, а History перечитує кожен процитований Chunk. Цей урок не додає нової; він доводить, що щойно звуження закомічене, усі вони нічого не дають.
- **Що знайшов рев'ю.** Незалежний атакувальний рев'ю пробував порядок сторінок, явні видалення, винятки в кожній фазі, дублікати записів, публічних і порожніх читачів, нерозпізнаних принципалів, проходи на 300 файлів, маркери в тексті й принципалах на рівні DEBUG і 13 мутацій. Ніщо не розширило доступ і не витекло. Він знайшов такі прогалини, і всі виправлено тестами, що падали до виправлення:
  - Тест «посеред розмови» проходив без змін цього уроку, бо один файл уже відкликався до відповіді моделі. Тепер другий тест питає, поки прохід ще працює над попереднім файлом сторінки; на старому коді він падає. Контрольна перевірка вимагає, щоб до відкликання питання отримало відповідь, тож тест не може пройти з хибної причини.
  - Лічильники змін після звуження ніколи не перевірялися. Тепер є три випадки: вилучено читача, вилучено читача з новим текстом і читачів замінено з новим текстом.
  - Пропуск узгодження для звуженого файлу і пропуск звуження для зламаного файлу лише з невідомими читачами — обидва проходили. Тепер carol мусить отримати `b.md` після проходу, а зламаний файл, що називає лише `group:contractors`, мусить бути звужений до нікого.
  - Звуження кожного файлу коштувало блокування на кожен; тепер блокуються лише файли, що втрачають читача, і тест доводить, що незмінний прохід не блокує жодного.
  - Файл, запропонований на сторінці двічі, міг повернути вилученому читачеві доступ до свого другого узгодження. Тепер кожен файл рахується один раз на сторінку, як запропоновано востаннє, і тест перевіряє, що вилучений читач його ніколи не бачить.
- **Відомі обмеження.** Document, який джерело видаляє чи більше не перелічує, стає недоступним лише наприкінці проходу. Відкликання перше в межах сторінки, а не всього проходу. Текст, надісланий між відкликанням і перевіркою Citations, замінюється, але не «відсилається назад». Два проходи одного джерела не блокують один одного; застарілий падає, а не розширює доступ. Відкликання застосовується лише тоді, коли до нього доходить прохід; заплановані проходи з'являться в M08-T06.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої прогнози

Перш ніж щось змінювати, запишіть, що має бути правдою наприкінці уроку, і що ви очікуєте. Крок 6 перевіряє останній прогноз.

Створіть `docs/evidence/M07-T10-prioritize-permission-revocation.md`:

```markdown
# M07-T10 — Prioritize permission revocation evidence

## Invariant

On every page of a Synchronization Run, each Document that loses a reader
is narrowed to the readers its old and new access both name, committed with
a new Authorization Version, before any record of the page is parsed,
chunked, or reconciled; readers the Source adds wait for the record's own
reconciliation. Once a narrowing commits, every consistency point of a
request, search, the Evidence check before the model, the Citation check
before the decision, and History, gives the removed reader nothing. A file
offered twice on a page counts once, as last offered, and a pass that
removes no reader locks no Document.

## Expected failing check

Before the change, `uv run pytest -m integration tests/test_revocation_integration.py -q`
fails with `ERROR: file or directory not found: tests/test_revocation_integration.py`.

## Predictions

- On a page with `notes.md` (new text, engineering) first and `roadmap.md` (engineering removed, carol added) second, what alice's question about the roadmap gets while the run is still working on `notes.md`, before and after this change:
- How many Documents a pass that removes no reader locks, and how many queries it adds per page:
- What a page offering `b.md` for carol and then for engineering does to carol, and what the summary counts:
- Which tests fail if `synchronize_source` stops calling `_revoke_first`:

## Design

## Verification

## Review

## Decision

## Reflection
```

**Перевірка:** файл існує, і після двокрапки в кожному прогнозі є ваша відповідь.

### Крок 2 — Крок із кодом: хто зараз може читати Documents сторінки

**Простими словами:** цей крок додає одне питання до бази: хто зараз може читати кожен зі списку файлів одного джерела? Воно ставиться одним запитом на всю сторінку, тож прохід може визначити, які файли от-от втратять читача, нічого не блокуючи.

| Частина | Що робить |
|---|---|
| `load_readers` | Для доступних Documents цих файлів: їхні поточні читачі як `Subject`, одним запитом; файлу без збереженого Document у результаті немає |
| `_subject_from` | Перетворює збережений рядок гранту на `Subject`, відкидаючи невідомий вид |

У `src/knowledge_service/persistence/access.py`:

**1.** Знайдіть:

```python
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
```

Замініть на:

```python
    Subject,
    SubjectKind,
    checked_reader,
    granted_subjects,
)
from knowledge_service.documents import DocumentAvailability
from knowledge_service.identifiers import (
    AccessGrantId,
    DocumentId,
    GroupId,
    SourceId,
    UserId,
)
from knowledge_service.persistence.schema import access_grants, chunks, documents


def subject_grants_statement(subject_kind: str, subject_id: UUID) -> sa.Select[Any]:
    """Select the Documents one verified subject is allowed to reach."""
    return sa.select(access_grants.c.document_id).where(
```

**2.** Знайдіть:

```python
        sa.select(access_grants)
        .where(access_grants.c.document_id == document_id.value)
        .order_by(access_grants.c.subject_kind, access_grants.c.subject_id)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_grant_from(dict(record)) for record in records)
```

Замініть на:

```python
        sa.select(access_grants)
        .where(access_grants.c.document_id == document_id.value)
        .order_by(access_grants.c.subject_kind, access_grants.c.subject_id)
    )
    records = (await connection.execute(statement)).mappings().all()
    return tuple(_grant_from(dict(record)) for record in records)


async def load_readers(
    connection: AsyncConnection,
    *,
    source_id: SourceId,
    external_ids: Sequence[str],
) -> dict[str, frozenset[Subject]]:
    """The readers each available Document of these source items has now.

    One statement for all of them. An item with no stored Document, or a
    tombstoned one, is absent; an available one with no grants maps to an
    empty set.
    """
    if not external_ids:
        return {}
    statement = (
        sa.select(
            documents.c.external_id,
            access_grants.c.subject_kind,
            access_grants.c.subject_id,
        )
        .select_from(
            documents.outerjoin(
                access_grants,
                access_grants.c.document_id == documents.c.document_id,
            )
        )
        .where(
            documents.c.source_id == source_id.value,
            documents.c.external_id.in_(sorted(set(external_ids))),
            documents.c.availability == DocumentAvailability.AVAILABLE.value,
        )
    )
    readers: dict[str, set[Subject]] = {}
    for record in (await connection.execute(statement)).mappings():
        found = readers.setdefault(cast(str, record["external_id"]), set())
        if record["subject_kind"] is not None:
            found.add(_subject_from(dict(record)))
    return {name: frozenset(subjects) for name, subjects in readers.items()}


def _subject_from(record: Mapping[str, object]) -> Subject:
    try:
        kind = SubjectKind(cast(str, record["subject_kind"]))
    except ValueError:
        raise InvalidGrant("stored subject kind is not a known value") from None
    stored_id = cast("UUID | None", record["subject_id"])
    if kind is SubjectKind.PUBLIC:
        return Subject.public()
    if kind is SubjectKind.USER:
        return Subject.user(UserId(cast(UUID, stored_id)))
    return Subject.group(GroupId(cast(UUID, stored_id)))
```

**Перевірка:** `uv run pyright src/knowledge_service/persistence/access.py` показує `0 errors`.

### Крок 3 — Крок із кодом: чи вилучає новий доступ когось?

**Простими словами:** цей крок додає перевірку «так чи ні»: знаючи, хто зараз може читати файл і кого зараз називає джерело, чи втратить хтось доступ? Вона використовує те саме правило, що вже використовує `narrow_access`, де «усі» рахуються як будь-який читач.

У `src/knowledge_service/ingestion/reconciliation.py`:

**1.** Знайдіть:

```python

When a record cannot be normalized, its last good version stays, but
`narrow_access` keeps it only for readers the Source still names: who may
read it can shrink then, never grow.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncConnection

```

Замініть на:

```python

When a record cannot be normalized, its last good version stays, but
`narrow_access` keeps it only for readers the Source still names: who may
read it can shrink then, never grow.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncConnection

```

**2.** Знайдіть:

```python
        # observation compares against what is really stored.
        await upsert_document(connection, document)
        await _write_grants(connection, document, kept, observed_at)
        return True


def _both(stored: Sequence[Subject], observed: Sequence[Subject]) -> list[Subject]:
    """The readers both sets let in; everyone stands for any reader."""
    everyone = Subject.public()
    if everyone in stored:
        return list(observed)
    if everyone in observed:
```

Замініть на:

```python
        # observation compares against what is really stored.
        await upsert_document(connection, document)
        await _write_grants(connection, document, kept, observed_at)
        return True


def removes_readers(stored: Iterable[Subject], observed: Sequence[Subject]) -> bool:
    """Whether the observed readers leave out anyone the stored ones let in."""
    kept = tuple(stored)
    return set(_both(kept, observed)) != set(kept)


def _both(stored: Sequence[Subject], observed: Sequence[Subject]) -> list[Subject]:
    """The readers both sets let in; everyone stands for any reader."""
    everyone = Subject.public()
    if everyone in stored:
        return list(observed)
    if everyone in observed:
```

**Перевірка:** `uv run pyright src/knowledge_service/ingestion/reconciliation.py` показує `0 errors`.

### Крок 4 — Крок із кодом: сторінка у дві фази

**Простими словами:** цей крок змінює порядок роботи Synchronization Run. Для кожної сторінки він спершу збирає файли, лишаючи лише останню пропозицію кожного. Потім читає, хто зараз може їх читати, і звужує кожен файл, що втрачає читача, кожен у власному маленькому коміті. Лише після цього він розбирає, ріже на чанки й узгоджує файли, як і раніше. Підсумок досі повідомляє, що змінилося для кожного файлу, рахуючи звуження як зміну доступу.

| Зміна | Що робить |
|---|---|
| Docstring модуля | Пояснює «спершу відкликання» і що досі чекає |
| `planned` | Словник за external ID: файл, запропонований двічі, рахується один раз, як востаннє; пізніше видалення прибирає його |
| `_revoke_first` | Один раз `load_readers`, потім `narrow_access` для кожного файлу, який вибрала `removes_readers`; повертає ID звужених |
| Фаза 2 | Розбирає й узгоджує кожен запланований файл; файл у карантині рахує зміну доступу, якщо його звузили |
| `_counted` | Повідомляє зміну доступу для звуженого файлу, який саме узгодження позначило б як незмінений чи лише змінений за вмістом |

У `src/knowledge_service/ingestion/synchronization.py`:

**1.** Знайдіть:

```python

Each Document's readers come from `normalize_access`: a principal the
Source names but the `PrincipalDirectory` does not map grants nothing, and
the summary reports it so someone can map it. A record that fails to
normalize keeps its last good version, for only the readers its old and new
access both name.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
```

Замініть на:

```python

Each Document's readers come from `normalize_access`: a principal the
Source names but the `PrincipalDirectory` does not map grants nothing, and
the summary reports it so someone can map it. A record that fails to
normalize keeps its last good version, for only the readers its old and new
access both name.

Revocation goes first. Each page is handled in two phases. First, one
query reads the readers every Document of the page has now, and each record
whose new access leaves someone out is narrowed to the readers its old and
new access both name (`narrow_access`); each narrowing commits on its own,
with a new Authorization Version. Only then is any record of the page
parsed, chunked, and reconciled. So a reader the Source removed from a
Document loses it before the slow work of its page, never after it. Readers
the Source adds wait for the record's own reconciliation, with its new
content. A record offered twice in a page counts once, as last offered.

Two kinds of removal still wait: a revocation on a later page waits for the
earlier pages' work, and a Document the Source deletes, or no longer lists,
becomes unavailable only when the pass tombstones it at the end.
"""

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
```

**2.** Знайдіть:

```python
from knowledge_service.ingestion.deletions import (
    Tombstone,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.grant_normalization import (
    PrincipalDirectory,
    UnresolvedPrincipal,
    normalize_access,
)
from knowledge_service.ingestion.normalization import Quarantined, normalize
from knowledge_service.ingestion.reconciliation import (
    Change,
    narrow_access,
    reconcile_document,
)
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourcePort,
    SourceRecordState,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


```

Замініть на:

```python
from knowledge_service.ingestion.deletions import (
    Tombstone,
    requeue_unfinished_purges,
    tombstone_missing,
)
from knowledge_service.ingestion.grant_normalization import (
    NormalizedAccess,
    PrincipalDirectory,
    UnresolvedPrincipal,
    normalize_access,
)
from knowledge_service.ingestion.normalization import Quarantined, normalize
from knowledge_service.ingestion.reconciliation import (
    Change,
    narrow_access,
    reconcile_document,
    removes_readers,
)
from knowledge_service.ingestion.sources import (
    InvalidSource,
    SourcePort,
    SourceRecord,
    SourceRecordState,
)
from knowledge_service.persistence.access import load_readers

logger = logging.getLogger(__name__)

PAGE_SIZE = 100


```

**3.** Знайдіть:

```python
    quarantined: list[Quarantined] = []
    # By external ID, so a record offered twice is reported as last seen.
    unresolved: dict[str, UnresolvedAccess] = {}
    checkpoint: str | None = None
    while True:
        page = await source.fetch_page(checkpoint=checkpoint, page_size=page_size)
        for record in page.records:
            # A record the Source reports deleted is not seen, even if an
            # earlier page offered it: the last report wins.
            unresolved.pop(record.external_id, None)
            if record.state is SourceRecordState.DELETED or record.access is None:
                seen.discard(record.external_id)
                continue
            seen.add(record.external_id)
            access = normalize_access(record.access, principal_directory)
            if access.unresolved:
                unresolved[record.external_id] = UnresolvedAccess(
                    record.external_id, access.unresolved
                )
            outcome = normalize(record, source_id=source_id)
            if isinstance(outcome, Quarantined):
                # Kept as seen: a file that fails to parse is not a deleted
                # one, but whoever the Source no longer names loses it now.
                quarantined.append(outcome)
                if await narrow_access(
                    runtime,
                    source_id=source_id,
                    external_id=record.external_id,
                    readers=access.readers,
                    observed_at=observed_at,
                ):
                    changes[Change.ACCESS] += 1
                continue
            result = await reconcile_document(
                runtime,
                outcome,
                readers=access.readers,
                observed_at=observed_at,
            )
            changes[result.change] += 1
        if page.next_checkpoint is None:
            break
        if page.next_checkpoint in used:
            raise InvalidSource("the source repeated a checkpoint")
        used.add(page.next_checkpoint)
        checkpoint = page.next_checkpoint
```

Замініть на:

```python
    quarantined: list[Quarantined] = []
    # By external ID, so a record offered twice is reported as last seen.
    unresolved: dict[str, UnresolvedAccess] = {}
    checkpoint: str | None = None
    while True:
        page = await source.fetch_page(checkpoint=checkpoint, page_size=page_size)
        # By external ID, so a record offered twice in a page counts once.
        planned: dict[str, tuple[SourceRecord, NormalizedAccess]] = {}
        for record in page.records:
            # A record the Source reports deleted is not seen, even if an
            # earlier page offered it: the last report wins.
            unresolved.pop(record.external_id, None)
            planned.pop(record.external_id, None)
            if record.state is SourceRecordState.DELETED or record.access is None:
                seen.discard(record.external_id)
                continue
            seen.add(record.external_id)
            access = normalize_access(record.access, principal_directory)
            if access.unresolved:
                unresolved[record.external_id] = UnresolvedAccess(
                    record.external_id, access.unresolved
                )
            planned[record.external_id] = (record, access)
        # Security first: every reader this page removes loses the Document
        # before any record of the page is parsed or chunked.
        narrowed = await _revoke_first(
            runtime,
            list(planned.values()),
            source_id=source_id,
            observed_at=observed_at,
        )
        for record, access in planned.values():
            outcome = normalize(record, source_id=source_id)
            if isinstance(outcome, Quarantined):
                # Kept as seen: a file that fails to parse is not a deleted
                # one; its narrowing above already applied.
                quarantined.append(outcome)
                if record.external_id in narrowed:
                    changes[Change.ACCESS] += 1
                continue
            result = await reconcile_document(
                runtime,
                outcome,
                readers=access.readers,
                observed_at=observed_at,
            )
            changes[
                _counted(result.change, narrowed=record.external_id in narrowed)
            ] += 1
        if page.next_checkpoint is None:
            break
        if page.next_checkpoint in used:
            raise InvalidSource("the source repeated a checkpoint")
        used.add(page.next_checkpoint)
        checkpoint = page.next_checkpoint
```

**4.** Знайдіть:

```python
        changes=changes,
        quarantined=tuple(quarantined),
        tombstones=tombstones,
        requeued_purges=requeued,
        unresolved=tuple(unresolved.values()),
    )
```

Замініть на:

```python
        changes=changes,
        quarantined=tuple(quarantined),
        tombstones=tombstones,
        requeued_purges=requeued,
        unresolved=tuple(unresolved.values()),
    )


async def _revoke_first(
    runtime: DatabaseRuntime,
    planned: list[tuple[SourceRecord, NormalizedAccess]],
    *,
    source_id: SourceId,
    observed_at: datetime,
) -> set[str]:
    """Narrow every record whose new access leaves a reader out.

    One read finds the readers each Document has now, so a record that
    removes nobody costs no locking transaction. Each narrowing is its own
    short transaction, committed before the next, so a revocation is
    visible to readers as soon as it is applied. Returns the narrowed IDs.
    """
    async with runtime.transaction() as connection:
        stored = await load_readers(
            connection,
            source_id=source_id,
            external_ids=[record.external_id for record, _ in planned],
        )
    narrowed: set[str] = set()
    for record, access in planned:
        current = stored.get(record.external_id)
        if current is None or not removes_readers(current, access.readers):
            continue
        if await narrow_access(
            runtime,
            source_id=source_id,
            external_id=record.external_id,
            readers=access.readers,
            observed_at=observed_at,
        ):
            narrowed.add(record.external_id)
    return narrowed


def _counted(change: Change, *, narrowed: bool) -> Change:
    """The change to report for a record whose access was narrowed first.

    The narrowing already wrote the access change, so reconciliation may see
    none; the record still changed who may read it.
    """
    if not narrowed or change in (Change.ACCESS, Change.CONTENT_AND_ACCESS):
        return change
    if change is Change.CONTENT:
        return Change.CONTENT_AND_ACCESS
    return Change.ACCESS
```

**Перевірка:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_synchronization_integration.py tests/test_document_title_integration.py -q
```

Очікувано: `18 passed`, кожен наявний тест синхронізації без змін.

### Крок 5 — Крок із кодом: доказ порядку і відмови для читань у процесі

**Простими словами:** ці тести спостерігають за проходом зсередини. Один записує порядок подій і показує, що кожне звуження відбувається до розбору першого файлу і що alice вже втратила файл, коли починається повільна робота. Інші перевіряють лічильники, зламані файли, дублікати і те, що незмінний прохід нічого не блокує. Останній файл ставить справжнє питання, поки триває прохід, і перевіряє, що кожне подальше читання нічого не дає alice.

| Файл | Тести |
|---|---|
| `tests/test_synchronization_integration.py` | Відкликання до будь-якого розбору, і carol потім отримує файл; три випадки лічильників; зламаний файл лише з невідомими читачами; файл, запропонований двічі; прохід, що нічого не блокує; файл, запропонований, а потім видалений на одній сторінці |
| `tests/test_revocation_integration.py` | Грант відкликано, поки модель пише, і після цього History, пошук і нове питання відмовляють; контрольний випадок, де доступ лишається; питання, поставлене, поки прохід ще розбирає попередній файл |

У `tests/test_synchronization_integration.py`:

**1.** Знайдіть:

```python
"""Real PostgreSQL tests for one complete pass over a local Source."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy import text

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import SourceId
from knowledge_service.identity.context import group_id_for, user_id_for
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.grant_normalization import (
    PrincipalDirectory,
    UnresolvedPrincipal,
    UnresolvedReason,
)
```

Замініть на:

```python
"""Real PostgreSQL tests for one complete pass over a local Source."""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from pydantic import SecretStr
from sqlalchemy import text

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import SourceId
from knowledge_service.identity.context import group_id_for, user_id_for
from knowledge_service.ingestion import synchronization
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.grant_normalization import (
    PrincipalDirectory,
    UnresolvedPrincipal,
    UnresolvedReason,
)
```

**2.** Знайдіть:

```python
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )

    assert summary.unresolved == ()
```

Замініть на:

```python
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )

    assert summary.unresolved == ()


class OnePage:
    """A Source that offers the given records on one page."""

    def __init__(self, records: Sequence[SourceRecord]) -> None:
        self.records = tuple(records)

    async def fetch_page(self, *, checkpoint: str | None, page_size: int) -> SourcePage:
        return SourcePage(records=self.records, next_checkpoint=None)


def a_record(name: str, text: str, *principals: str) -> SourceRecord:
    return SourceRecord.present(
        external_id=name,
        version=text,
        media_type="text/markdown",
        content=text.encode(),
        access=SourceAccess(public=False, principals=principals),
        metadata=SourceMetadata(title=name, modified_at=OBSERVED_AT, size_bytes=1),
    )


async def readable_names(
    runtime: DatabaseRuntime, reader: AuthorizationContext
) -> set[str]:
    """The Documents whose Chunks retrieval's own check lets `reader` see."""
    statement = (
        sa.select(documents.c.external_id)
        .select_from(
            chunks.join(documents, chunks.c.document_id == documents.c.document_id)
        )
        .where(eligible_chunks_predicate(reader))
    )
    async with runtime.transaction() as connection:
        return set((await connection.execute(statement)).scalars())


@pytest.mark.integration
async def test_a_revocation_is_published_before_any_record_of_its_page_is_parsed(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a.md comes first and changes its text; b.md comes second and drops
    # engineering. b.md's revocation must not wait for a.md's parsing.
    await synchronize_source(
        runtime,
        OnePage(
            [
                a_record("a.md", "# A\n\nFirst text.\n", "group:engineering"),
                a_record("b.md", "# B\n\nSalaries.\n", "group:engineering"),
            ]
        ),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )
    events: list[str] = []
    visible_at_first_reconcile: list[set[str]] = []
    narrow, parse, reconcile = (
        synchronization.narrow_access,
        synchronization.normalize,
        synchronization.reconcile_document,
    )

    async def narrowing(*args: Any, **kwargs: Any) -> bool:
        changed = await narrow(*args, **kwargs)
        events.append(f"narrow {kwargs['external_id']} {changed}")
        return changed

    def parsing(record: SourceRecord, *, source_id: SourceId) -> Any:
        events.append(f"parse {record.external_id}")
        return parse(record, source_id=source_id)

    async def reconciling(*args: Any, **kwargs: Any) -> Any:
        if not visible_at_first_reconcile:
            visible_at_first_reconcile.append(await readable_names(runtime, ALICE))
        return await reconcile(*args, **kwargs)

    monkeypatch.setattr(synchronization, "narrow_access", narrowing)
    monkeypatch.setattr(synchronization, "normalize", parsing)
    monkeypatch.setattr(synchronization, "reconcile_document", reconciling)

    summary = await synchronize_source(
        runtime,
        OnePage(
            [
                a_record("a.md", "# A\n\nSecond text.\n", "group:engineering"),
                a_record("b.md", "# B\n\nSalaries.\n", "user:carol@example.test"),
            ]
        ),
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=DIRECTORY,
    )

    # a.md removes nobody, so it is not even locked; b.md is narrowed first.
    assert events == ["narrow b.md True", "parse a.md", "parse b.md"]
    # When the slow work of the page starts, alice already lost b.md.
    assert visible_at_first_reconcile == [{"a.md"}]
    assert summary.changes == {Change.CONTENT: 1, Change.ACCESS: 1}
    # The reader the Source added gets it with the record's reconciliation.
    assert await readable_names(runtime, CAROL_READER) == {"b.md"}


CAROL_READER = AuthorizationContext(user_id=user_id_for(ISSUER, CAROL))
ENGINEERING = "group:engineering"
CAROL_PRINCIPAL = "user:carol@example.test"


async def one_pass(
    runtime: DatabaseRuntime, at: datetime, *records: SourceRecord
) -> SynchronizationSummary:
    return await synchronize_source(
        runtime,
        OnePage(records),
        source_id=SOURCE_ID,
        observed_at=at,
        principal_directory=DIRECTORY,
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("before", "after", "change", "alice", "carol"),
    [
        (
            ("# B\n\nSalaries.\n", ENGINEERING, CAROL_PRINCIPAL),
            ("# B\n\nSalaries.\n", CAROL_PRINCIPAL),
            Change.ACCESS,
            set[str](),
            {"b.md"},
        ),
        (
            ("# B\n\nSalaries.\n", CAROL_PRINCIPAL),
            ("# B\n\nNew salaries.\n", ENGINEERING),
            Change.CONTENT_AND_ACCESS,
            {"b.md"},
            set[str](),
        ),
        (
            ("# B\n\nSalaries.\n", ENGINEERING, CAROL_PRINCIPAL),
            ("# B\n\nNew salaries.\n", CAROL_PRINCIPAL),
            Change.CONTENT_AND_ACCESS,
            set[str](),
            {"b.md"},
        ),
    ],
    ids=["reader-removed", "reader-swapped-with-new-text", "reader-removed-new-text"],
)
async def test_a_narrowed_record_reports_the_access_change_it_made(
    runtime: DatabaseRuntime,
    before: tuple[str, ...],
    after: tuple[str, ...],
    change: Change,
    alice: set[str],
    carol: set[str],
) -> None:
    await one_pass(runtime, OBSERVED_AT, a_record("b.md", *before))

    summary = await one_pass(runtime, LATER, a_record("b.md", *after))

    assert summary.changes == {change: 1}
    assert await readable_names(runtime, ALICE) == alice
    assert await readable_names(runtime, CAROL_READER) == carol


@pytest.mark.integration
async def test_a_broken_file_naming_only_unknown_readers_is_narrowed_to_nobody(
    runtime: DatabaseRuntime,
) -> None:
    await one_pass(runtime, OBSERVED_AT, a_record("b.md", "# B\n\nOk.\n", ENGINEERING))
    broken = SourceRecord.present(
        external_id="b.md",
        version="broken",
        media_type="text/markdown",
        content=b"\xff\xfe broken",
        access=SourceAccess(public=False, principals=("group:contractors",)),
        metadata=SourceMetadata(title="b.md", modified_at=OBSERVED_AT, size_bytes=1),
    )

    summary = await one_pass(runtime, LATER, broken)

    assert summary.changes == {Change.ACCESS: 1}
    assert len(summary.quarantined) == 1
    assert await readable_names(runtime, ALICE) == set()


@pytest.mark.integration
async def test_a_record_offered_twice_in_a_page_counts_once_as_last_offered(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = "# B\n\nSalaries.\n"
    await one_pass(
        runtime, OBSERVED_AT, a_record("b.md", text, ENGINEERING, CAROL_PRINCIPAL)
    )
    carol_saw: list[set[str]] = []
    reconcile = synchronization.reconcile_document

    async def reconciling(*args: Any, **kwargs: Any) -> Any:
        result = await reconcile(*args, **kwargs)
        carol_saw.append(await readable_names(runtime, CAROL_READER))
        return result

    monkeypatch.setattr(synchronization, "reconcile_document", reconciling)

    summary = await one_pass(
        runtime,
        LATER,
        a_record("b.md", text, CAROL_PRINCIPAL),
        a_record("b.md", text, ENGINEERING),
    )

    # Only the last offer counts: carol, removed by it, never regains b.md.
    assert carol_saw == [set()]
    assert summary.changes == {Change.ACCESS: 1}
    assert await readable_names(runtime, ALICE) == {"b.md"}


@pytest.mark.integration
async def test_a_pass_that_removes_no_reader_locks_no_document(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = [
        a_record(f"{name}.md", f"# {name}\n\nText.\n", ENGINEERING)
        for name in ("a", "b", "c")
    ]
    await one_pass(runtime, OBSERVED_AT, *records)
    calls: list[str] = []
    narrow = synchronization.narrow_access

    async def narrowing(*args: Any, **kwargs: Any) -> bool:
        calls.append(kwargs["external_id"])
        return await narrow(*args, **kwargs)

    monkeypatch.setattr(synchronization, "narrow_access", narrowing)
    widened = a_record("c.md", "# c\n\nText.\n", ENGINEERING, CAROL_PRINCIPAL)

    summary = await one_pass(runtime, LATER, *records[:2], widened)

    assert calls == []
    assert summary.changes == {Change.UNCHANGED: 2, Change.ACCESS: 1}


@pytest.mark.integration
async def test_a_record_offered_then_deleted_in_one_page_is_not_reconciled(
    runtime: DatabaseRuntime,
) -> None:
    text = "# B\n\nSalaries.\n"
    await one_pass(runtime, OBSERVED_AT, a_record("b.md", text, ENGINEERING))

    summary = await one_pass(
        runtime,
        LATER,
        a_record("b.md", "# B\n\nRewritten.\n", ENGINEERING),
        SourceRecord.deleted(external_id="b.md", version="gone"),
    )

    assert summary.changes == {}
    assert [tombstone.external_id for tombstone in summary.tombstones] == ["b.md"]
    assert await readable_names(runtime, ALICE) == set()
```

Тести підміняють функції модуля, як-от `synchronization.normalize`, обгортками, що записують події; так вони бачать порядок. Якби рефакторинг колись обійшов ці імена, очікуваних подій не було б, і тест гучно впав би, а не тихо пройшов.

Створіть `tests/test_revocation_integration.py`:

```python
"""A grant revoked in the middle of a Conversation: every later read fails closed.

alice asked about the roadmap once and got an Answer citing it. She asks
again, and while the model is writing, a Synchronization Run takes the
roadmap away from engineering. From then on, at every consistency point,
she must get nothing from it: the current Answer is withdrawn, her History
withholds the earlier Answer, search finds nothing, and a new Question
abstains without the model.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import httpx
import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.abstention import AbstentionReason, EvidenceSignals
from knowledge_service.answering.current_citations import CurrentCitations
from knowledge_service.answering.events import (
    AnswerEventReader,
    StreamedAnswer,
    StreamOutcome,
)
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.history import load_history
from knowledge_service.answering.streaming import FoundEvidence
from knowledge_service.app import create_app
from knowledge_service.conversations import (
    ConversationLimits,
    MessageOutcome,
    MessageReference,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, ConversationId, DocumentId
from knowledge_service.identity.context import Caller
from knowledge_service.ingestion import synchronization
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.sources import SourceAccess
from knowledge_service.ingestion.synchronization import synchronize_source
from knowledge_service.persistence.conversations import add_turn, start_conversation
from knowledge_service.persistence.schema import chunks, documents
from knowledge_service.retrieval.lexical_search import search_lexical_chunks
from knowledge_service.roles import ApplicationRole
from knowledge_service.settings import Settings
from test_citation_confirmation_integration import CitingModel
from test_streaming import QUESTION, Model, streamer
from test_synchronization_integration import (
    ALICE,
    DIRECTORY,
    INSERT_SOURCE,
    LATER,
    OBSERVED_AT,
    SOURCE_ID,
    OnePage,
    a_record,
)

ENGINEERING = SourceAccess(public=False, principals=("group:engineering",))
CAROL_ONLY = SourceAccess(public=False, principals=("user:carol@example.test",))
LIMITS = ConversationLimits()
SEARCH_WORDS = "roadmap policy"


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


class AsAlice:
    """An authenticator that makes every request alice's, a `user`."""

    async def authenticate(self, headers: list[str]) -> Caller:
        return Caller(reader=ALICE, roles=frozenset({ApplicationRole.USER}))


class WordSearch:
    """An Evidence source that searches the stored words as the reader."""

    def __init__(self, runtime: DatabaseRuntime) -> None:
        self.runtime = runtime

    async def find(
        self, question: str, *, reader: AuthorizationContext, request_id: UUID
    ) -> FoundEvidence:
        async with self.runtime.transaction() as connection:
            found = await search_lexical_chunks(
                connection, text=SEARCH_WORDS, context=reader, limit=5
            )
        evidence = build_evidence(found.candidates)
        return FoundEvidence(evidence, EvidenceSignals(len(evidence.items), None, None))


async def sync(
    runtime: DatabaseRuntime, folder: Path, access: SourceAccess, at: datetime
) -> None:
    await synchronize_source(
        runtime,
        DirectorySource(folder, access=access),
        source_id=SOURCE_ID,
        observed_at=at,
        principal_directory=DIRECTORY,
    )


async def ask(runtime: DatabaseRuntime, model: Model) -> StreamedAnswer:
    app = create_app(
        streamer=streamer(
            model,
            source=cast(Any, WordSearch(runtime)),
            citations=CurrentCitations(runtime),
        ),
        authenticator=AsAlice(),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        response = await http.post(
            "/v1/answer/stream",
            json={"text": QUESTION.text},
            headers={"Authorization": "Bearer any"},
        )
    reader = AnswerEventReader()
    reader.feed(response.content)
    return reader.close()


async def roadmap_reference(runtime: DatabaseRuntime) -> MessageReference:
    async with runtime.transaction() as connection:
        row = (
            await connection.execute(
                sa.select(
                    chunks.c.chunk_id,
                    documents.c.document_id,
                    documents.c.content_version,
                )
                .join(documents, documents.c.document_id == chunks.c.document_id)
                .where(documents.c.external_id == "roadmap.md")
            )
        ).one()
    return MessageReference(
        chunk_id=ChunkId(row.chunk_id),
        document_id=DocumentId(row.document_id),
        content_version=ContentVersion(row.content_version),
    )


async def earlier_turn(runtime: DatabaseRuntime) -> ConversationId:
    """alice's Conversation, with one earlier Answer citing the roadmap."""
    reference = await roadmap_reference(runtime)
    async with runtime.transaction() as connection:
        started = await start_conversation(
            connection, owner_id=ALICE.user_id, now=OBSERVED_AT, limits=LIMITS
        )
        await add_turn(
            connection,
            owner_id=ALICE.user_id,
            conversation_id=started.conversation_id,
            question="What is on the roadmap?",
            reply="The roadmap.md policy.",
            outcome=MessageOutcome.ANSWERED,
            references=(reference,),
            now=OBSERVED_AT,
            limits=LIMITS,
        )
    return started.conversation_id


async def earlier_answers(
    runtime: DatabaseRuntime, conversation_id: ConversationId
) -> list[str | None]:
    async with runtime.transaction() as connection:
        history = await load_history(
            connection,
            ALICE,
            conversation_id=conversation_id,
            now=LATER,
            budget_tokens=4_000,
        )
    assert history is not None
    return [turn.answer for turn in history.turns]


@pytest.mark.integration
async def test_a_grant_revoked_mid_conversation_fails_every_later_read_closed(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    (tmp_path / "roadmap.md").write_text("The roadmap.md policy.\n")
    await sync(runtime, tmp_path, ENGINEERING, OBSERVED_AT)
    conversation_id = await earlier_turn(runtime)
    assert await earlier_answers(runtime, conversation_id) == ["The roadmap.md policy."]

    async def revoke() -> None:
        # A Synchronization Run commits while the model is still writing.
        await sync(runtime, tmp_path, CAROL_ONLY, LATER)

    during = await ask(runtime, CitingModel("roadmap.md", before_reply=revoke))
    after = Model()
    later = await ask(runtime, after)
    async with runtime.transaction() as connection:
        searched = await search_lexical_chunks(
            connection, text=SEARCH_WORDS, context=ALICE, limit=5
        )

    # The Answer being written when the grant went is withdrawn.
    assert (during.outcome, during.reason) == (
        StreamOutcome.ABSTAINED,
        AbstentionReason.NO_EVIDENCE,
    )
    assert during.citations == ()
    # Every later read fails closed.
    assert await earlier_answers(runtime, conversation_id) == [None]
    assert searched.candidates == ()
    assert (later.outcome, later.reason) == (
        StreamOutcome.ABSTAINED,
        AbstentionReason.NO_EVIDENCE,
    )
    assert after.calls == 0


@pytest.mark.integration
async def test_a_grant_kept_by_the_same_run_still_answers(
    runtime: DatabaseRuntime, tmp_path: Path
) -> None:
    # The control case: the same run, but engineering keeps the roadmap.
    (tmp_path / "roadmap.md").write_text("The roadmap.md policy.\n")
    await sync(runtime, tmp_path, ENGINEERING, OBSERVED_AT)
    conversation_id = await earlier_turn(runtime)

    async def resync() -> None:
        await sync(runtime, tmp_path, ENGINEERING, LATER)

    during = await ask(runtime, CitingModel("roadmap.md", before_reply=resync))

    assert during.outcome is StreamOutcome.ANSWERED
    assert await earlier_answers(runtime, conversation_id) == ["The roadmap.md policy."]


@pytest.mark.integration
async def test_a_question_asked_while_the_run_still_parses_already_fails_closed(
    runtime: DatabaseRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    # notes.md comes first on the page and changes its text; roadmap.md
    # comes second and drops engineering. alice asks about the roadmap
    # while the run is still working on notes.md.
    # Plain words too: the search reads "roadmap.md" as one file name.
    roadmap = "The roadmap.md policy. Roadmap policy.\n"
    await synchronize_source(
        runtime,
        OnePage(
            [
                a_record("notes.md", "# Notes\n\nOld.\n", "group:engineering"),
                a_record("roadmap.md", roadmap, "group:engineering"),
            ]
        ),
        source_id=SOURCE_ID,
        observed_at=OBSERVED_AT,
        principal_directory=DIRECTORY,
    )
    # Without a revocation, alice's question is answered.
    before = await ask(runtime, CitingModel("roadmap.md"))
    assert before.outcome is StreamOutcome.ANSWERED
    answers: list[StreamedAnswer] = []
    reconcile = synchronization.reconcile_document

    async def reconciling(runtime_: Any, normalized: Any, **kwargs: Any) -> Any:
        if normalized.provenance.external_id == "notes.md":
            answers.append(await ask(runtime, CitingModel("roadmap.md")))
        return await reconcile(runtime_, normalized, **kwargs)

    monkeypatch.setattr(synchronization, "reconcile_document", reconciling)

    await synchronize_source(
        runtime,
        OnePage(
            [
                a_record("notes.md", "# Notes\n\nNew.\n", "group:engineering"),
                a_record("roadmap.md", roadmap, "user:carol@example.test"),
            ]
        ),
        source_id=SOURCE_ID,
        observed_at=LATER,
        principal_directory=DIRECTORY,
    )

    (during,) = answers
    assert (during.outcome, during.reason) == (
        StreamOutcome.ABSTAINED,
        AbstentionReason.NO_EVIDENCE,
    )
```

Третій тест спершу ставить питання без жодного відкликання і вимагає відповіді. Без цього пошук, який просто не знайшов файл, теж дав би відмову, і тест пройшов би, хоч би що робив прохід. Рев'ю знайшов саме таку проблему в ранній чернетці.

**Перевірка:**

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_synchronization_integration.py tests/test_revocation_integration.py -q
```

Очікувано: `26 passed`.

### Крок 6 — Без коду: зламайте навмисно

Цей крок зупиняє «спершу відкликання» і показує, які обіцянки на ньому тримаються.

**Дія 1:** у `synchronize_source` у `src/knowledge_service/ingestion/synchronization.py` замініть

```python
        narrowed = await _revoke_first(
            runtime,
            list(planned.values()),
            source_id=source_id,
            observed_at=observed_at,
        )
```

на

```python
        narrowed: set[str] = set()
```

**Дія 2:** запустіть команду з перевірки кроку 5. Очікувано: `6 failed, 20 passed`:

- `test_a_revocation_is_published_before_any_record_of_its_page_is_parsed`
- `test_a_question_asked_while_the_run_still_parses_already_fails_closed`
- `test_a_broken_file_naming_only_unknown_readers_is_narrowed_to_nobody`
- `test_a_reader_removed_while_the_file_is_broken_loses_it_at_once`, обидва випадки
- `test_access_narrowed_while_broken_is_restored_by_a_good_version`

Другий — це витік, який закриває цей урок: alice отримує відповідь із файлу, який engineering щойно втратила, бо прохід до нього ще не дійшов. Останні три — обіцянка M07-T06 для зламаних файлів, яка тепер тримається на тій самій першій фазі.

**Дія 3:** поверніть виклик і запустіть команду знову: `26 passed`.

**Дія 4:** порівняйте з останнім прогнозом в evidence-файлі й запишіть, чи він справдився.

### Крок 7 — Без коду: документи, перевірки, evidence і прогрес

Цей крок оновлює те, що зміна зробила неточним, доводить, що все працює, і записує результат. Threat model називав `M07-T10` власником інваріанта 3; trace тепер називає тести цього уроку.

**Дія 1:** оновіть два документи.

У `docs/architecture.md`:

Знайдіть:

```markdown
            else present
                Sync->>Sync: add its external ID to seen
                Sync->>Norm: SourceRecord
                Norm-->>Sync: NormalizedDocument or Quarantined
                alt NormalizedDocument
                    Sync->>Sync: normalize_access: mapped principals become readers, the rest are kept as unresolved
                    Sync->>Rec: NormalizedDocument + readers
                    Rec->>DB: one transaction: lock row, compare fingerprints, write what changed
                else Quarantined
                    Sync->>Sync: keep it in seen, so its last good version stays
                    Sync->>Rec: narrow_access: keep only readers old and new access both name
                end
            end
        end
```

Замініть на:

```markdown
            else present
                Sync->>Sync: add its external ID to seen
                Sync->>Sync: normalize_access: mapped principals become readers, the rest are kept as unresolved
            end
        end
        loop each present record, before any is parsed
            Sync->>Rec: narrow_access: keep only readers old and new access both name
            Rec->>DB: one short transaction each: a removed reader loses the Document now, under a new Authorization Version
        end
        loop each present record
            Sync->>Norm: SourceRecord
            Norm-->>Sync: NormalizedDocument or Quarantined
            alt NormalizedDocument
                Sync->>Rec: NormalizedDocument + readers
                Rec->>DB: one transaction: lock row, compare fingerprints, write what changed
            else Quarantined
                Sync->>Sync: keep it in seen, so its last good version stays, already narrowed
            end
        end
```

У `docs/threat-model.md`:

**1.** Знайдіть:

```markdown

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A Source reports no readers, and the Document becomes public by mistake | Only an item the Source marks public gets a public grant; any other item is readable only by the principals `normalize_access` resolves | None |
| A Source principal that is unmapped, malformed, or spelled like another widens who reads a Document | `normalize_access` fails closed: only `user:<id>` and `group:<id>` that the Source's `PrincipalDirectory` maps exactly become grants, as the same UUIDv5 IDs tokens carry; the rest grant nothing and are reported in the synchronization summary, with only their count logged; a directory may map only to subjects and paths a token could carry, and every caller of `synchronize_source` must name its directory | Confluence permission mapping (M08-T14) |
| A reader the Source removed keeps reading the last good version of a file that stopped parsing | `narrow_access` applies the new access to a quarantined record at once, keeping only readers its old and new access both name; nobody gains access until the record normalizes again | None |
| A permission is removed, but the old Chunks stay searchable until cleanup | Grants and Chunks change in one transaction; a tombstone makes a Document unavailable at once | Priority revocation sync (M07-T10) |
| A file removed or unreadable deletes or keeps the wrong Documents | An unreadable folder is unavailable, not deleted; a failed pass tombstones nothing | Incremental sync (M08-T10) |
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one | Retrieved-instruction hardening (M07-T13) |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | Jobs are stored with typed errors and bounded retries | Job idempotency (M08-T03) |

### Retrieval
```

Замініть на:

```markdown

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A Source reports no readers, and the Document becomes public by mistake | Only an item the Source marks public gets a public grant; any other item is readable only by the principals `normalize_access` resolves | None |
| A Source principal that is unmapped, malformed, or spelled like another widens who reads a Document | `normalize_access` fails closed: only `user:<id>` and `group:<id>` that the Source's `PrincipalDirectory` maps exactly become grants, as the same UUIDv5 IDs tokens carry; the rest grant nothing and are reported in the synchronization summary, with only their count logged; a directory may map only to subjects and paths a token could carry, and every caller of `synchronize_source` must name its directory | Confluence permission mapping (M08-T14) |
| A reader the Source removed keeps reading the last good version of a file that stopped parsing | `narrow_access` applies the new access to a quarantined record at once, keeping only readers its old and new access both name; nobody gains access until the record normalizes again | None |
| A permission is removed, but the old Chunks stay searchable until cleanup | Grants and Chunks change in one transaction; a tombstone makes a Document unavailable at once | None |
| A revocation waits behind slow parsing and chunking, or reaches a request already in progress | Each page of a Synchronization Run reads its Documents' readers once and commits every narrowing, with a new Authorization Version, before it parses any of its records; a request rereads access at its consistency points: search, the Evidence check before the model, the Citation check before the decision, and History, so every such read after the commit fails closed | A revocation on a later page waits for the earlier pages; a deleted or unlisted Document is tombstoned only at the end of the pass; text streamed between the commit and the Citation check is replaced, not unsent; a revocation applies only when a run reaches it, so scheduled incremental runs come in M08-T06 |
| A file removed or unreadable deletes or keeps the wrong Documents | An unreadable folder is unavailable, not deleted; a failed pass tombstones nothing | Incremental sync (M08-T10) |
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one | Retrieved-instruction hardening (M07-T13) |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | Jobs are stored with typed errors and bounded retries | Job idempotency (M08-T03) |

### Retrieval
```

**2.** Знайдіть:

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
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `M07-T11`, `M07-T15` |

## Known gaps
```

Замініть на:

```markdown
Each row traces one invariant under "Invariants" in `docs/security.md` to its control and to at least one test owner: a test pytest collects, written `tests/<file>.py::<test>`, or a task not yet completed that will add one. `make docs-check` fails when an invariant has no row, a test owner no longer exists, or a named task is already completed. Owners in `*_integration.py` files run under `make test-integration`, which CI does not run yet.

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `Authenticator` builds the Authorization Context from a verified token only; `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_app_authentication.py::test_a_verified_token_is_the_reader_the_search_runs_as`, `tests/test_app_authentication.py::test_headers_and_body_fields_cannot_choose_or_widen_the_reader`, `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `tests/test_search_security_contract_integration.py::test_every_search_returns_exactly_what_the_reader_may_see`, `tests/test_lexical_search.py::test_the_permission_check_comes_before_ranking_and_highlighting`, `tests/test_vector_evidence_integration.py::test_a_hidden_exact_match_never_reaches_the_model_or_the_client` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `tests/test_synchronization_integration.py::test_a_revocation_is_published_before_any_record_of_its_page_is_parsed`, `tests/test_revocation_integration.py::test_a_grant_revoked_mid_conversation_fails_every_later_read_closed` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `tests/test_citation_confirmation_integration.py::test_another_users_chunk_is_never_cited_to_the_reader`, `tests/test_citation_confirmation_integration.py::test_a_grant_revoked_while_the_model_writes_withdraws_the_answer`, `M07-T11`, `M07-T15` |

## Known gaps
```

**Дія 2:** запустіть перевірки по черзі, із запущеним PostgreSQL:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_revocation_integration.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний прогін дав 2784 passed для `make test-unit`, без змін, і 636 passed для `make test-integration`, на 11 більше, ніж після M07-T09.

**Дія 3:** заповніть evidence-файл:

- **Design:** дві фази, як `load_readers` і `removes_readers` вибирають файли, `_counted` і точки узгодженості.
- **Verification:** команди й кількості, навмисний злам і відомі обмеження з «Перш ніж почати».
- **Review:** підсумок рев'ю з «Перш ніж почати».
- **Decision:** чому спершу вилучення, а додавання потім, чому посторінково і чому читати один раз, а не блокувати кожен файл; плюс щонайменше одне **фальсифіковане припущення** у формі «I assumed X. The run or review shows Y. So Z is wrong.»
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу як completed, додайте рядок M07-T10 у `docs/curriculum/PROGRESS.md`, поставте наступною задачею M07-T11 і закомітьте з `m07-t10: prioritize permission revocation`.

## Запитання для рефлексії

1. `synchronize_source` тепер викликає `_revoke_first` до будь-якого розбору. Що отримує той, хто викликає `synchronize_source`, нічого не знаючи про Authorization Versions?
2. Хтось переносить звуження в `reconcile_document`, щоб «тримати логіку доступу в одному місці». Що побачила б alice під час довгого проходу, і який тест упав би?
3. Чому додані читачі не застосовуються вже в першій фазі? Опишіть файл, для якого це показало б новому читачеві текст, який він ніколи не мав бачити.
4. Джерело має 50 сторінок, а єдине відкликання — на сторінці 50. Як довго вилучений читач ще може читати той файл, і чого коштувала б перша фаза на весь прохід?
5. bob був у `/finance`, і прохід вилучає його з `payroll.md`. Перелічіть кожне місце, де bob міг би ще спробувати його прочитати під час і після проходу, і скажіть, що кожне поверне, щойно звуження закомічене.

## Контрольний список завершення

- [ ] Evidence-файл містить інваріант і ваші прогнози, записані до зміни.
- [ ] `load_readers` читає поточних читачів сторінки одним запитом, а `removes_readers` каже, чи вилучає новий доступ когось.
- [ ] `synchronize_source` звужує кожен файл, що втрачає читача, до розбору будь-якого файлу сторінки, рахує кожен файл один раз на сторінку і повідомляє зміну доступу.
- [ ] Наявні тести синхронізації досі проходять (18).
- [ ] Нові тести порядку, лічильників і відмови проходять проти PostgreSQL (26).
- [ ] Ви запустили навмисний злам, побачили 6 тестів, що впали, і скасували його.
- [ ] `docs/architecture.md` і `docs/threat-model.md` описують «спершу відкликання» та його межі, а trace називає нові тести.
- [ ] Усі перевірки проходять: format, lint, types, unit, integration, docs.
- [ ] Evidence, статус задачі, рядок у PROGRESS і коміт готові.

Коли напишете файли, надішліть їх або вивід помилок, і я перевірю їх за цим списком.
