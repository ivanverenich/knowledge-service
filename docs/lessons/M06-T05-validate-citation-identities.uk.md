# M06-T05 — Перевірте ідентичність цитат

Англійська версія: [M06-T05-validate-citation-identities.md](M06-T05-validate-citation-identities.md)

Вихідне завдання: [M06-T05](../curriculum/milestones/M06-answers-and-conversations/M06-T05-validate-citation-identities.md)

## Що ви зробите

Цей урок гарантує, що жодна цитата (Citation) не дійде до користувача, якщо вона вже не правдива в момент надсилання відповіді. M06-T04 уже перевіряє, що модель цитує лише показані їй Evidence. Але модель пише кілька секунд, і за цей час Документ можуть видалити, переписати, перейменувати або забрати в читача. Тому після генерації сервер ще раз питає базу про кожен процитований Chunk.

- Якщо модель назвала той самий ID Evidence двічі, тепер вона отримує одну цитату, а не втрачає всю оплачену відповідь.
- `confirm_citations(connection, context, result, ...)` повторно перевіряє кожну цитату відповіді за збереженими Документами, тією самою перевіркою прав, що й пошук.
- Якщо хоч одна цитата вже не дозволена чи не актуальна, уся відповідь відкликається: новий результат `WITHDRAWN`, без тексту й без цитат.
- Підтверджена цитата перебудовується зі збережених рядків. Тож перейменований Документ показує нову назву, а ніщо, вкладене в цитату ззовні, не дійде до клієнта.

| Завдання вимагає | Де це відбувається |
|---|---|
| Приймати лише ID Evidence, передані генерації | Уже так з M06-T04: `checked_answer` звіряє ID з Evidence у промпті; крок 3 додає другу перевірку, за базою |
| Прибирати повтори цитат | Крок 2: `resolve_citations` цитує повторений ID один раз, на місці першої згадки |
| Зіставляти їх з поточним походженням Документа на сервері | Крок 3: `confirm_citations` перебудовує кожну цитату зі збережених рядків |
| Адверсарні тести: вигадані чи недозволені ID ніколи не доходять до клієнта | Крок 4: підроблені цитати, відкликаний доступ, інший читач, видалені й переписані Документи |
| Детерміновані тести: успіх, найризиковіший край, типізована помилка | Крок 4: незмінна відповідь проходить; Chunk, якого читач не може бачити; `TypeError` для підробленого результату |
| Жодного підкинутого тексту в телеметрії | Крок 4: підкинутий у назву маркер ніколи не потрапляє в запис логу, repr чи помилку |

Цей урок містить код: кроки 2–4 пишуть код. Кроки 1, 5 і 6 — без коду. Нові тести кроку 4 потребують тестової бази PostgreSQL.

Терміни цього уроку:

- **Походження (provenance):** звідки цитата: назва Документа, шлях заголовків усередині нього, посилання на джерело і короткий уривок. Клієнт показує це поруч із відповіддю.
- **Актуальний (current):** процитований Chunk досі існує в тій самій версії свого Документа, і Документ не видалено. Нова версія Документа отримує нові Chunks з новими ID, тож старий ID Chunk просто нічого не знаходить.
- **Дозволений (eligible):** читач може бачити Chunk саме зараз: його Документ доступний, і один з його дозволів доступу (Access Grants) називає всіх, цього користувача або одну з його груп. Пошук використовує те саме правило.
- **Позначений видаленим (tombstoned):** Документ, позначений як видалений. Його Chunks лишаються в базі, доки фонова задача їх не очистить. Тож «Chunk існує» замало: Документ також має бути доступним.
- **Відкликаний (withdrawn):** результат відповіді, яку написали й оплатили, але не можна показати, бо цитата перестала бути дозволеною чи актуальною.

## Перед початком

Цей розділ пояснює рішення за кодом, щоб кожен крок мав сенс, коли ви до нього дійдете.

- **Що вже є.** `answering/citations.py` перетворює ID Evidence від моделі на цитати, побудовані із серверних Evidence. `answering/generation.py` дає `generate_answer`, який повертає `AnswerResult` з одним результатом. `persistence/access.py` має `eligible_chunks_predicate`, SQL-умову, якою кожен пошук показує лише Chunks, дозволені читачеві.
- **Чому повтори ID тепер зливаються.** M06-T01 відхиляв список, що називав той самий ID двічі, і залишив рішення цьому урокові. Повтор не може вигадати чи перейменувати джерело: він знову вказує на те саме Evidence. У M06-T04 відмова стає `INVALID`, тож користувач втратив би відповідь, за яку провайдер уже взяв гроші. Суворий режим OpenAI не вміє заборонити повтори в схемі, тож вони траплятимуться. Тому `resolve_citations` тепер лишає перший і відкидає решту. Читання збережених цитат через `read_citations` і далі відхиляє повтор, бо сервер його ніколи не пише; там повтор означає, що дані змінили.
- **Навіщо перевіряти знову після генерації.** Перевірка прав у пошуку правильна в момент пошуку. Потім модель працює секунди, аж до 30-секундного дедлайну. За цей час прогін синхронізації (Synchronization Run) може відкликати доступ, видалити чи переписати Документ. Без другої перевірки клієнт отримав би цитату, і написану з неї відповідь, яку читач уже не має права бачити.
- **Чому відкликається вся відповідь.** Текст відповіді написано з процитованих Evidence. Якщо прибрати лише погану цитату, текст однаково показав би, що в тому Evidence було, тепер без підтвердження. Тому одна невдала цитата відкликає все. Usage і звіт про контекст лишаються, бо за виклик заплатили.
- **Чому всі невдачі виглядають однаково.** Відкликаний дозвіл, видалений Chunk, нова версія і позначений видаленим Документ дають `WITHDRAWN`. Клієнт не може їх розрізнити, тож не дізнається, що існує Документ, якого йому не можна бачити, чи що той змінився.
- **Навіщо перебудовувати цитату з бази.** Назва може змінитися без нової версії: перейменований Документ зберігає свої Chunks. А `AnswerResult` — звичайний об'єкт; помилка в коді чи підробка можуть нести будь-яку назву чи уривок. Якщо перебудувати з рядків бази кожне поле, крім ID Evidence, клієнт бачить те, що сховище каже зараз.
- **Навіщо окрема коротка транзакція.** Викликач передає з'єднання. Він не повинен тримати транзакцію бази відкритою, поки модель пише до 30 секунд. Тож пошук іде в одній транзакції, генерація — без транзакції, а підтвердження — в новій, короткій. Лишається один проміжок, між підтвердженням і доставкою, і жодна перевірка його не закриє.
- **Що знайшло рев'ю.** Адверсарне рев'ю не знайшло шляху від справжнього пошуку й генерації до цитати, якої читач не може бачити. Воно знайшло п'ять способів, якими зроблений вручну чи підроблений результат проходив перевірку. Кожен виправлено тестом, що падає без виправлення:
  - Результат, заданий простим текстом "answered", пропускав перевірку. Тепер результат має бути справжнім `AnswerOutcome`.
  - Результат, що не є відповіддю, зберігав свої цитати неперевіреними. Тепер цитати може мати лише відповідь.
  - Ідентифікатор із «брехливим» UUID проходив порівняння. Тепер кожен ідентифікатор має містити саме UUID.
  - ID Chunk з текстом усередині падав у базі, і текст потрапляв у повідомлення про помилку. Тепер це `TypeError`, ще до будь-якого SQL.
  - Підроблена назва чи уривок зберігалися. Тепер кожне поле, крім ID Evidence, перебудовується зі сховища.
- **Де живе код.** `answering/current_citations.py`, невеликі зміни в `answering/citations.py`, `answering/evidence.py` і `answering/generation.py`.
- **Відомі обмеження.** `confirm_citations` поки ніхто не викликає; стрімінговий ендпоінт у M06-T09 викликатиме його перед надсиланням будь-якої цитати. Між підтвердженням і доставкою Документ ще може змінитися. Перевірка підтверджує ідентичність і права, а не правдивість: чи підтверджує Evidence кожне твердження — це M06-T06.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої передбачення

Поки коду ще немає, запишіть, що має бути правдою наприкінці уроку. Крок 5 перевіряє передбачення.

Створіть `docs/evidence/M06-T05-citation-identities.md`:

```markdown
# M06-T05 — Citation identities evidence

## Invariant

`resolve_citations` accepts only IDs of the request's own Evidence and
cites a repeated ID once, at its first position; `read_citations` still
refuses a repeat. `confirm_citations` returns any outcome other than
`ANSWERED` unchanged and without a query, and refuses with `TypeError`,
before any SQL, a result, outcome, or Citation that is not exactly what
generation builds. For an Answer, one statement selects the cited Chunks
under the retrieval permission check; every Citation must match its
stored Chunk, Document, Source, and content version, in an available
Document the reader may see. Otherwise the whole Answer is `WITHDRAWN`,
with no text, no reason, and no Citations, keeping its usage and context
report. A confirmed Citation is rebuilt from the stored rows, except its
Evidence ID. No log record, repr, or error holds a title, link, or text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.current_citations"`
fails with `ModuleNotFoundError`.

## Predictions

- What a model's reply that cites the same Evidence ID three times became in M06-T04, and what it becomes after this lesson: ...
- What the reader gets when a cited Document was renamed between retrieval and confirmation, and when access to it was revoked: ...
- How many tests fail if the permission check is left out of the confirmation statement, and which kinds of change they cover: ...

## Design

## Verification

## Decision

## Reflection
```

Перевірка: файл існує, перевірка, що має впасти, падає, а кожне передбачення — повне речення, яке крок 5 може спростувати.

### Крок 2 — Крок із кодом: злийте повтори цитат і підготуйте новий результат

Цей крок трохи змінює три наявні файли, щоб новий модуль кроку 3 мав усе потрібне.

**Простими словами:** Коли модель називає те саме джерело двічі, тепер вона отримує одну цитату, а не втрачає всю відповідь. Читання збережених цитат і далі відхиляє повтор, бо сервер його ніколи не зберігає. Відповідь отримує новий можливий результат, «відкликано», для відповіді, яку не можна показати. А довжина тексту, з якого вирізають уривок, стає спільною, щоб новий модуль вирізав уривки так само, як пошук.

| Зміна | Навіщо |
|---|---|
| `resolve_citations` лишає перший із повторених ID | Повтор не може вигадати джерело; відмова викинула б оплачену відповідь |
| `read_citations` і далі відхиляє повтор | Сервер його ніколи не пише, тож повтор у збережених даних означає, що їх змінили |
| `AnswerOutcome.WITHDRAWN` | Результат, який `confirm_citations` ставить відповіді, яку не можна показати |
| `EXCERPT_SOURCE_CHARACTERS` стає публічним | Крок 3 перебудовує уривок з тих самих 4 000 символів, що й Evidence |

**1.** У `src/knowledge_service/answering/citations.py` оновіть докстрінг модуля. Знайдіть:

```python
- **Strict references.** `resolve_citations` takes the model's list of
  IDs as it arrived. It refuses, with `InvalidCitation`, anything that is
  not a list or tuple of plain `str`, an ID that is malformed, an ID not
  in the set, and an ID listed twice. One bad reference rejects the whole
  list; nothing is guessed or dropped.
```

Замініть на:

```python
- **Strict references.** `resolve_citations` takes the model's list of
  IDs as it arrived. It refuses, with `InvalidCitation`, anything that is
  not a list or tuple of plain `str`, an ID that is malformed, and an ID
  not in the set. One bad reference rejects the whole list; nothing is
  guessed or dropped.
- **Each source once.** An ID the model lists twice is cited once, at its
  first position. It names the same Evidence again, so it adds nothing a
  user should see, and it is no reason to throw away a paid Answer.
```

Потім знайдіть:

```python
  same checks, and also refuses a payload whose Document, title, heading,
  link, or excerpt differs from its Evidence, or that has a field too many.
```

Замініть на:

```python
  same checks, and also refuses an ID listed twice, since the server
  never writes one, and a payload whose Document, title, heading, link, or
  excerpt differs from its Evidence, or that has a field too many.
```

**2.** У тому самому файлі змініть `resolve_citations`. Знайдіть:

```python
    """Turn the model's Evidence IDs into Citations, or raise `InvalidCitation`."""
```

Замініть на:

```python
    """Turn the model's Evidence IDs into Citations, or raise `InvalidCitation`.

    A repeated ID is cited once, at its first position.
    """
```

Потім знайдіть:

```python
    for index, reference in enumerate(cast(Sequence[object], references)):
        item = _known(evidence, reference, index, seen)
        citations.append(_citation(item))
```

Замініть на:

```python
    for index, reference in enumerate(cast(Sequence[object], references)):
        item = _known(evidence, reference, index)
        if item.evidence_id in seen:
            continue
        seen.add(item.evidence_id)
        citations.append(_citation(item))
```

**3.** У `read_citations` перевірка повтору виходить з `_known` і лишається тут. Знайдіть:

```python
        item = _known(evidence, payload.evidence_id, index, seen)
        citation = _citation(item)
```

Замініть на:

```python
        item = _known(evidence, payload.evidence_id, index)
        if item.evidence_id in seen:
            raise InvalidCitation(CitationDefect.DUPLICATE, index)
        seen.add(item.evidence_id)
        citation = _citation(item)
```

**4.** `_known` більше не стежить за побаченим. Знайдіть:

```python
def _known(
    evidence: EvidenceSet, reference: object, index: int, seen: set[EvidenceId]
) -> Evidence:
    """The Evidence a reference names, if it is well formed, known, and new."""
```

Замініть на:

```python
def _known(evidence: EvidenceSet, reference: object, index: int) -> Evidence:
    """The Evidence a reference names, if it is well formed and known."""
```

Потім знайдіть:

```python
    if item is None:
        raise InvalidCitation(CitationDefect.UNKNOWN, index)
    if evidence_id in seen:
        raise InvalidCitation(CitationDefect.DUPLICATE, index)
    seen.add(evidence_id)
    return item
```

Замініть на:

```python
    if item is None:
        raise InvalidCitation(CitationDefect.UNKNOWN, index)
    return item
```

**5.** У `src/knowledge_service/answering/evidence.py` перейменуйте приватну `_EXCERPT_SOURCE_CHARACTERS` на `EXCERPT_SOURCE_CHARACTERS` в обох місцях, де вона трапляється. Знайдіть:

```python
_EXCERPT_SOURCE_CHARACTERS = 4_000
```

Замініть на:

```python
EXCERPT_SOURCE_CHARACTERS = 4_000
```

Потім знайдіть:

```python
            excerpt=display_excerpt(text[:_EXCERPT_SOURCE_CHARACTERS]),
```

Замініть на:

```python
            excerpt=display_excerpt(text[:EXCERPT_SOURCE_CHARACTERS]),
```

**6.** У `src/knowledge_service/answering/generation.py` знайдіть:

```python
    REFUSED = "refused"
    INVALID = "invalid"
```

Замініть на:

```python
    REFUSED = "refused"
    INVALID = "invalid"
    # Set by `confirm_citations` (see `answering.current_citations`).
    WITHDRAWN = "withdrawn"
```

**7.** Оновіть тести, які очікували відмови на повтор. У `tests/test_citations.py` знайдіть:

```python
def test_an_unknown_or_repeated_reference_rejects_the_whole_list() -> None:
    evidence = evidence_set()
    known = ids(evidence)

    with pytest.raises(InvalidCitation) as unknown:
        resolve_citations(evidence, [known[0], absent(evidence)])
    with pytest.raises(InvalidCitation) as repeated:
        resolve_citations(evidence, [known[1], known[0], known[1]])

    assert (unknown.value.defect, unknown.value.index) == (CitationDefect.UNKNOWN, 1)
    assert (repeated.value.defect, repeated.value.index) == (
        CitationDefect.DUPLICATE,
        2,
    )
```

Замініть на:

```python
def test_an_unknown_reference_rejects_the_whole_list() -> None:
    evidence = evidence_set()

    with pytest.raises(InvalidCitation) as unknown:
        resolve_citations(evidence, [ids(evidence)[0], absent(evidence)])

    assert (unknown.value.defect, unknown.value.index) == (CitationDefect.UNKNOWN, 1)


def test_a_repeated_reference_is_cited_once_at_its_first_position() -> None:
    evidence = evidence_set()
    first, second, _ = ids(evidence)

    citations = resolve_citations(evidence, [second, first, second, second])

    assert [c.evidence_id.value for c in citations] == [second, first]


def test_a_repeated_reference_still_needs_every_other_reference_valid() -> None:
    evidence = evidence_set()
    first = ids(evidence)[0]

    with pytest.raises(InvalidCitation) as caught:
        resolve_citations(evidence, [first, first, "E1"])

    assert (caught.value.defect, caught.value.index) == (CitationDefect.MALFORMED, 2)
```

Потім у кінці property-тесту внизу файлу знайдіть:

```python
    assert all(evidence.get(c.evidence_id) is not None for c in citations)
    assert len({c.evidence_id for c in citations}) == len(citations)
```

Замініть на:

```python
    assert all(evidence.get(c.evidence_id) is not None for c in citations)
    assert len({c.evidence_id for c in citations}) == len(citations)
    # Each listed ID is cited once, in the order it first appears.
    assert [c.evidence_id.value for c in citations] == list(dict.fromkeys(mixed))
```

**8.** У `tests/test_generation.py` повторений ID більше не є зламаною відповіддю. Видаліть цей рядок зі списку зламаних відповідей:

```python
        (body(citations=[FIRST, FIRST]), AnswerDefect.INVALID_CITATION),
```

Видаліть цей рядок з його списку `ids`:

```python
        "repeated-id",
```

Потім знайдіть:

```python
async def test_evidence_dropped_by_the_budget_cannot_be_cited() -> None:
```

Замініть на:

```python
async def test_a_repeated_citation_is_cited_once() -> None:
    model = FakeAnswerModel(body(citations=[FIRST, SECOND, FIRST]))

    result = await generate(model)

    assert result.outcome is AnswerOutcome.ANSWERED
    assert [c.evidence_id.value for c in result.citations] == [FIRST, SECOND]


async def test_evidence_dropped_by_the_budget_cannot_be_cited() -> None:
```

Перевірка:

```bash
uv run pytest tests/test_citations.py tests/test_generation.py tests/test_evidence.py -q
```

Очікувано: 126 passed.

### Крок 3 — Крок із кодом: підтвердіть цитати за сховищем

Цей крок пише модуль, що стоїть між згенерованою відповіддю і клієнтом.

**Простими словами:** Спершу він перевіряє, що отримав саме таку відповідь, яку будує генерація; усе інше відхиляється ще до звернення до бази. Результат, що не є відповіддю, проходить одразу. Для відповіді він одним запитом питає базу, які з процитованих шматків тексту читач досі може бачити в їхній поточній версії. Якщо всі на місці, відповідь іде далі, а кожну цитату перебудовано з того, що збережено зараз. Якщо бракує хоч однієї, вся відповідь відкликається, і лишається тільки її вартість.

| Частина | Що містить чи робить |
|---|---|
| `confirm_citations` | Перевіряє вхід, надсилає один запит і повертає відповідь підтвердженою або відкликаною |
| `current_citations_statement` | Запит: процитовані Chunks, з'єднані з їхніми Документами, лише поточна версія, під перевіркою прав |
| `_checked`, `_well_formed` | Правила входу: від 1 до 20 справжніх цитат, ідентифікатори містять саме UUID, кожне Evidence і Chunk один раз |
| `_current` | Порівнює одну цитату з її рядком і перебудовує її з рядка, або повертає None |
| `_log` | Один запис на перевірку: ID запиту, скільки процитовано й відкликано, результат |

Створіть `src/knowledge_service/answering/current_citations.py`:

```python
"""Confirm an Answer's Citations against the Documents as they are now.

Retrieval checks permissions when it ranks. The model then takes seconds
to write the Answer, and in that time a Document can be deleted, rewritten,
renamed, or taken away from the reader. `confirm_citations` runs after
generation and before anything reaches the client, and asks the database
once more about every cited Chunk.

What `confirm_citations` promises:

- **Checked shape first.** The context, the result, its outcome, and every
  Citation must be of exactly the types generation builds, with UUIDs
  inside the identifiers; otherwise `TypeError`, before any SQL. A result
  that is not `ANSWERED` must hold no Citations.
- **Only Answers are confirmed.** Any other outcome is returned unchanged,
  and the database is not asked.
- **Every Citation is still allowed.** One statement selects the cited
  Chunks under `eligible_chunks_predicate`, the same permission check
  retrieval uses, for the same `AuthorizationContext`. A Citation counts
  only when its Chunk still exists in the same Document, Source, and
  content version, the Document is available, and the reader may see it.
- **All or nothing.** If even one Citation fails, the whole Answer is
  withdrawn: the outcome becomes `WITHDRAWN`, with no text, no reason, and
  no Citations. The text was written from that Evidence, so showing it
  without the Citation would still show what the reader may no longer
  see. The usage and the context report are kept, since the call was paid.
- **Provenance from the stored rows.** A confirmed Citation is rebuilt from
  its row: identifiers, version, title, heading path, source link, and
  excerpt, cleaned for display as Evidence is. Only its Evidence ID comes
  from the result. So a renamed Document is cited under its new title,
  and nothing a caller put into a Citation reaches the client.
- **Same answer to every failure.** A deleted Chunk, a new version, a
  tombstoned Document, and a revoked grant all give `WITHDRAWN`, so the
  client cannot learn which one happened, or that a Document exists.
- **Quiet.** One log record per check holds the request ID and counts;
  never a title, a link, or text.

The caller passes a connection for a short transaction of its own; it
must not hold one open while the model writes.
"""

import logging
from collections.abc import Sequence
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncConnection

from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.citations import Citation
from knowledge_service.answering.evidence import (
    EXCERPT_SOURCE_CHARACTERS,
    MAX_EVIDENCE,
    EvidenceId,
)
from knowledge_service.answering.generation import AnswerOutcome, AnswerResult
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.ingestion.provenance import display_excerpt, display_text
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.persistence.schema import chunks, documents

logger = logging.getLogger(__name__)


async def confirm_citations(
    connection: AsyncConnection,
    context: AuthorizationContext,
    result: AnswerResult,
    *,
    request_id: UUID,
) -> AnswerResult:
    """Return the Answer with current provenance, or withdraw it.

    Raises `TypeError` when `context` or `result` is not what the request
    and generation produce.
    """
    if type(context) is not AuthorizationContext:
        raise TypeError("context must be an AuthorizationContext")
    if type(result) is not AnswerResult or type(result.outcome) is not AnswerOutcome:
        raise TypeError("result must be an AnswerResult")
    if result.outcome is not AnswerOutcome.ANSWERED:
        if result.citations != ():
            raise TypeError("only an Answer holds Citations")
        return result
    cited = _checked(result.citations)
    statement = current_citations_statement(
        [citation.chunk_id.value for citation in cited],
        eligibility=eligible_chunks_predicate(context),
    )
    records = (await connection.execute(statement)).mappings().all()
    rows = {cast(UUID, record["chunk_id"]): dict(record) for record in records}
    confirmed = [
        _current(citation, rows.get(citation.chunk_id.value)) for citation in cited
    ]
    withdrawn = sum(citation is None for citation in confirmed)
    if withdrawn:
        result = AnswerResult(
            outcome=AnswerOutcome.WITHDRAWN,
            text="",
            reason=None,
            citations=(),
            defect=None,
            usage=result.usage,
            context=result.context,
        )
    else:
        result = AnswerResult(
            outcome=result.outcome,
            text=result.text,
            reason=result.reason,
            citations=tuple(cast(list[Citation], confirmed)),
            defect=result.defect,
            usage=result.usage,
            context=result.context,
        )
    _log(request_id, cited=len(cited), withdrawn=withdrawn, outcome=result.outcome)
    return result


def current_citations_statement(
    chunk_ids: Sequence[UUID], *, eligibility: sa.ColumnElement[bool]
) -> sa.Select[*tuple[Any, ...]]:
    """Select the cited Chunks that are current and meet `eligibility`.

    The IDs are one array parameter, however many there are. A Chunk is
    current when its version is its Document's; replacing a version gives
    its Chunks new IDs, so an old ID finds nothing. Only the start of the
    text is read, as much as an excerpt needs.
    """
    return (
        sa.select(
            chunks.c.chunk_id,
            chunks.c.document_id,
            documents.c.source_id,
            chunks.c.content_version,
            documents.c.title,
            chunks.c.heading_path,
            chunks.c.source_anchor,
            sa.func.left(chunks.c.text, EXCERPT_SOURCE_CHARACTERS).label("opening"),
        )
        .select_from(chunks)
        .join(documents, documents.c.document_id == chunks.c.document_id)
        .where(
            chunks.c.chunk_id
            == sa.any_(
                sa.bindparam(
                    "chunk_ids", list(chunk_ids), type_=ARRAY(sa.Uuid()), unique=True
                )
            ),
            chunks.c.content_version == documents.c.content_version,
            eligibility,
        )
    )


def _checked(citations: object) -> tuple[Citation, ...]:
    """The result's Citations, if they are exactly what generation builds."""
    if type(citations) is not tuple:
        raise TypeError("Citations must be a tuple")
    items = cast(tuple[object, ...], citations)
    if not 1 <= len(items) <= MAX_EVIDENCE:
        raise TypeError(f"an Answer holds 1 to {MAX_EVIDENCE} Citations")
    if not all(_well_formed(item) for item in items):
        raise TypeError("a Citation is not what generation builds")
    cited = cast(tuple[Citation, ...], items)
    if len({c.chunk_id.value for c in cited}) != len(cited) or len(
        {c.evidence_id.value for c in cited}
    ) != len(cited):
        raise TypeError("an Answer cites each Evidence and Chunk once")
    return cited


def _well_formed(item: object) -> bool:
    """A Citation whose identifiers hold exactly a UUID, a str, or an int."""
    if type(item) is not Citation:
        return False
    identifiers = (
        (item.chunk_id, ChunkId),
        (item.document_id, DocumentId),
        (item.source_id, SourceId),
    )
    return (
        type(item.evidence_id) is EvidenceId
        and type(item.evidence_id.value) is str
        and all(
            type(value) is kind and type(value.value) is UUID
            for value, kind in identifiers
        )
        and type(item.content_version) is ContentVersion
        and type(item.content_version.number) is int
    )


def _current(citation: Citation, row: dict[str, object] | None) -> Citation | None:
    """The Citation rebuilt from its stored row, or None if it is not current."""
    if (
        row is None
        or row["document_id"] != citation.document_id.value
        or row["source_id"] != citation.source_id.value
        or row["content_version"] != citation.content_version.number
    ):
        return None
    return Citation(
        evidence_id=citation.evidence_id,
        chunk_id=ChunkId(cast(UUID, row["chunk_id"])),
        document_id=DocumentId(cast(UUID, row["document_id"])),
        source_id=SourceId(cast(UUID, row["source_id"])),
        content_version=ContentVersion(cast(int, row["content_version"])),
        title=display_text(cast(str, row["title"])),
        heading_path=_shown(row["heading_path"]),
        source_link=_shown(row["source_anchor"]),
        excerpt=display_excerpt(cast(str, row["opening"])),
    )


def _shown(value: object) -> str | None:
    return None if value is None else display_text(cast(str, value))


def _log(
    request_id: UUID, *, cited: int, withdrawn: int, outcome: AnswerOutcome
) -> None:
    """Record one check: the request, counts, and the outcome. No text."""
    fields = {
        "request_id": str(request_id),
        "cited": cited,
        "withdrawn": withdrawn,
        "outcome": outcome.value,
    }
    level = logging.WARNING if withdrawn else logging.INFO
    logger.log(level, "citations confirmed", extra={"citation_check": fields})
```

Тут важливі три деталі. Перевірка прав — це `eligible_chunks_predicate`, та сама функція, якою користується кожен пошук, у тому самому WHERE; другої копії правила, яка могла б розійтися з першою, немає. Запит читає лише перші 4 000 символів кожного Chunk, бо більше уривку не треба. А порівняння в `_current` безпечне, бо `_well_formed` уже переконався, що кожен ідентифікатор містить справжній UUID, а не об'єкт, який удає, що дорівнює будь-чому.

Перевірка:

```bash
uv run python -c "import knowledge_service.answering.current_citations"
uv run pyright src/knowledge_service/answering/current_citations.py
```

Очікувано: імпорт нічого не друкує, а pyright показує 0 помилок.

### Крок 4 — Крок із кодом: юніт- та інтеграційні тести

Цей крок доводить, що проходять лише актуальні й дозволені цитати, і що більше нічого не витікає.

**Простими словами:** Юніт-тестам база не потрібна. Вони перевіряють, що результат, який не є відповіддю, проходить незмінним, і що кожен зламаний чи підроблений вхід відхиляється ще до запиту. Інтеграційні тести працюють зі справжньою базою. Вони публікують Документи, відповідають через справжній пошук з фейковою моделлю, яка цитує все, що отримала, потім змінюють Документи так, як це зробила б синхронізація, і перевіряють, що пропускається.

| Тест | Що доводить |
|---|---|
| `test_a_current_answer_reaches_the_client_unchanged` | Успіх: нічого не змінилося, тож відповідь і цитати незмінні |
| `test_a_renamed_document_is_cited_under_its_current_title` | Поточне походження: той самий Chunk, нова назва |
| `test_an_answer_citing_a_document_that_changed_is_withdrawn` | Відкликаний доступ, нова версія, позначений видаленим, очищений: кожне відкликає всю відповідь |
| `test_another_readers_context_cannot_confirm_the_answer` | Перевірка використовує права самого читача |
| `test_a_citation_of_a_chunk_the_reader_may_not_see_never_reaches_the_client` | Найризиковіший край: підроблена цитата приватного Chunk з підкинутим маркером |
| `test_a_citation_whose_identity_differs_from_storage_is_withdrawn` | Кожна перевірка ідентичності окремо: Документ, Source, версія |
| `test_a_confirmed_citation_is_rebuilt_from_storage` | Підроблені назва, заголовок, посилання чи уривок замінюються збереженими |
| `test_a_broken_request_is_refused_before_the_database` | Типізована помилка: 13 зламаних чи підроблених входів дають `TypeError` до будь-якого SQL |
| `test_each_check_logs_counts_only` | Один запис логу з лічильниками; жодної назви |

Створіть `tests/test_current_citations.py`:

```python
"""Confirming Citations: what is checked before the database is asked."""

from dataclasses import replace
from uuid import UUID

import pytest

from evidence_fakes import MARKER, candidate
from knowledge_service.access import AuthorizationContext
from knowledge_service.answering.citations import resolve_citations
from knowledge_service.answering.context_budget import ContextBudget, allocate_context
from knowledge_service.answering.current_citations import (
    confirm_citations,
    current_citations_statement,
)
from knowledge_service.answering.evidence import build_evidence
from knowledge_service.answering.generation import AnswerOutcome, AnswerResult
from knowledge_service.identifiers import ChunkId, DocumentId, UserId
from knowledge_service.persistence.access import eligible_chunks_predicate

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000046")
CONTEXT = AuthorizationContext(user_id=UserId(UUID(int=7)))
EVIDENCE = build_evidence([candidate(1), candidate(2)])
FIRST, SECOND = (item.evidence_id.value for item in EVIDENCE.items)
REPORT = allocate_context("Leave?", EVIDENCE, ContextBudget(32_000, 1_000, 0)).report


class NoConnection:
    """Fails the test if anything asks the database."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the database was used: {name}")


def result(outcome: AnswerOutcome, citations: tuple[object, ...]) -> AnswerResult:
    return AnswerResult(
        outcome=outcome,
        text="You get 24 days.",
        reason=None,
        citations=citations,  # type: ignore[arg-type]
        defect=None,
        usage=None,
        context=REPORT,
    )


@pytest.mark.parametrize(
    "outcome",
    [o for o in AnswerOutcome if o is not AnswerOutcome.ANSWERED],
)
async def test_an_outcome_that_is_not_an_answer_passes_without_a_query(
    outcome: AnswerOutcome,
) -> None:
    given = result(outcome, ())

    confirmed = await confirm_citations(
        NoConnection(),  # type: ignore[arg-type]
        CONTEXT,
        given,
        request_id=REQUEST_ID,
    )

    assert confirmed is given


CITED = resolve_citations(EVIDENCE, [FIRST, SECOND])


class LyingUUID(UUID):
    """A UUID that claims to equal anything."""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    __hash__ = UUID.__hash__


def answered(*citations: object) -> AnswerResult:
    return result(AnswerOutcome.ANSWERED, citations)


@pytest.mark.parametrize(
    ("context", "given"),
    [
        (None, result(AnswerOutcome.ANSWERED, CITED)),
        (CONTEXT, object()),
        (CONTEXT, result(AnswerOutcome.ANSWERED, ())),
        (CONTEXT, result(AnswerOutcome.ANSWERED, (CITED[0], CITED[0]))),
        (CONTEXT, replace(answered(*CITED), outcome="answered")),
        (CONTEXT, result(AnswerOutcome.ABSTAINED, CITED)),
        (
            CONTEXT,
            answered(replace(CITED[0], document_id=DocumentId(LyingUUID(int=9)))),
        ),
        (CONTEXT, answered(replace(CITED[0], chunk_id=ChunkId(MARKER)))),  # type: ignore[arg-type]
        (CONTEXT, answered(replace(CITED[0], content_version=3))),
        (
            CONTEXT,
            answered(CITED[0], replace(CITED[1], evidence_id=CITED[0].evidence_id)),
        ),
        (CONTEXT, result(AnswerOutcome.ANSWERED, (CITED[0], "E00000001"))),
        (CONTEXT, result(AnswerOutcome.ANSWERED, CITED[:1] * 21)),
        (
            CONTEXT,
            result(
                AnswerOutcome.ANSWERED,
                (CITED[0], replace(CITED[1], chunk_id=CITED[0].chunk_id)),
            ),
        ),
    ],
    ids=[
        "no-context",
        "not-a-result",
        "answer-without-citations",
        "same-citation-twice",
        "outcome-as-text",
        "abstention-with-citations",
        "lying-uuid",
        "text-as-chunk-id",
        "plain-int-version",
        "same-evidence-id-twice",
        "not-a-citation",
        "too-many",
        "same-chunk-twice",
    ],
)
async def test_a_broken_request_is_refused_before_the_database(
    context: object, given: object
) -> None:
    with pytest.raises(TypeError) as caught:
        await confirm_citations(
            NoConnection(),  # type: ignore[arg-type]
            context,  # type: ignore[arg-type]
            given,  # type: ignore[arg-type]
            request_id=REQUEST_ID,
        )

    assert MARKER not in str(caught.value)


def test_the_statement_checks_permission_version_and_ids_in_one_query() -> None:
    statement = current_citations_statement(
        [CITED[0].chunk_id.value], eligibility=eligible_chunks_predicate(CONTEXT)
    )

    sql = str(statement)
    assert sql.count("SELECT") == 2  # the query and the grant check inside it
    assert "chunks.content_version = documents.content_version" in sql
    assert "documents.availability" in sql
    assert "ANY (" in sql
```

Створіть `tests/test_current_citations_integration.py`:

```python
"""Confirming Citations against the Documents as they are now, in PostgreSQL.

Each test answers from real retrieval, changes the stored Documents the
way a Synchronization Run would, and checks what `confirm_citations`
lets through to the client.
"""

import json
import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from evidence_fakes import MARKER
from knowledge_service.access import AuthorizationContext, Subject
from knowledge_service.answering.citations import Citation
from knowledge_service.answering.context_budget import ContextBudget
from knowledge_service.answering.current_citations import confirm_citations
from knowledge_service.answering.evidence import EvidenceId, build_evidence
from knowledge_service.answering.generation import (
    AnswerOutcome,
    AnswerResult,
    ModelIdentity,
    ModelReply,
    generate_answer,
)
from knowledge_service.answering.prompt import EvidencePrompt
from knowledge_service.contracts import Usage
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.identifiers import ChunkId, DocumentId, GroupId, SourceId, UserId
from knowledge_service.ingestion.deletions import tombstone_missing
from knowledge_service.ingestion.normalization import NormalizedDocument, normalize
from knowledge_service.ingestion.reconciliation import reconcile_document
from knowledge_service.ingestion.sources import (
    SourceAccess,
    SourceMetadata,
    SourceRecord,
)
from knowledge_service.persistence.schema import chunks
from knowledge_service.retrieval.lexical_search import search_lexical_chunks
from knowledge_service.settings import Settings

SOURCE = SourceId.parse("00000000-0000-0000-0000-000000000056")
OBSERVED_AT = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
LATER = OBSERVED_AT + timedelta(hours=1)
READER = UserId.parse("00000000-0000-0000-0000-0000000000e2")
OTHER = UserId.parse("00000000-0000-0000-0000-0000000000e3")
TEAM = GroupId.parse("00000000-0000-0000-0000-0000000000f1")
CONTEXT = AuthorizationContext(user_id=READER, group_ids=frozenset({TEAM}))
REQUEST_ID = UUID("00000000-0000-0000-0000-000000000045")
BUDGET = ContextBudget(context_tokens=32_000, answer_tokens=1_000, history_tokens=0)
USAGE = Usage(
    provider="fake",
    model="fake-1",
    latency_ms=5,
    input_tokens=900,
    output_tokens=40,
    total_tokens=940,
)
LEAVE = "Every employee receives 24 days of annual leave."
CARRY = "Unused annual leave carries over for 5 days."

INSERT_SOURCE = sa.text(
    "INSERT INTO sources (source_id, kind, location, display_name, enabled,"
    " created_at, updated_at) VALUES (:source_id, 'local_directory',"
    " '/srv/handbook', 'Handbook', true, now(), now())"
)


@pytest.fixture
async def runtime(migrated_database: str) -> AsyncIterator[DatabaseRuntime]:
    runtime = create_database_runtime(
        Settings(database_url=SecretStr(migrated_database))
    )
    async with runtime.transaction() as connection:
        await connection.execute(INSERT_SOURCE, {"source_id": SOURCE.value})
    try:
        yield runtime
    finally:
        await runtime.dispose()


def a_record(name: str, *, title: str, text: str, version: str) -> NormalizedDocument:
    outcome = normalize(
        SourceRecord.present(
            external_id=name,
            version=version,
            media_type="text/plain",
            content=text.encode(),
            access=SourceAccess(public=False, principals=("someone",)),
            metadata=SourceMetadata(title=title, modified_at=OBSERVED_AT, size_bytes=1),
        ),
        source_id=SOURCE,
    )
    assert isinstance(outcome, NormalizedDocument)
    return outcome


async def publish(
    runtime: DatabaseRuntime,
    name: str,
    *,
    title: str = "Leave policy",
    text: str = LEAVE,
    readers: Sequence[Subject] = (Subject.group(TEAM),),
    version: str = "v1",
    observed_at: datetime = OBSERVED_AT,
) -> DocumentId:
    reconciled = await reconcile_document(
        runtime,
        a_record(name, title=title, text=text, version=version),
        readers=readers,
        observed_at=observed_at,
    )
    return reconciled.document.document_id


class CitingModel:
    """Answers by citing every Evidence it was given, in prompt order."""

    def __init__(self, answer: str = "You get 24 days of annual leave.") -> None:
        self._answer = answer

    @property
    def identity(self) -> ModelIdentity:
        return ModelIdentity(provider="fake", model="fake-1")

    async def generate(
        self,
        prompt: EvidencePrompt,
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> ModelReply:
        schema = prompt.response_schema()
        ids = schema["properties"]["citations"]["items"]["enum"]
        text = json.dumps(
            {
                "answer": self._answer,
                "citations": ids,
                "abstained": False,
                "reason": None,
            }
        )
        return ModelReply(text=text, refused=False, usage=USAGE)


async def answer(
    runtime: DatabaseRuntime, question: str = "annual leave"
) -> AnswerResult:
    """Retrieve, then answer citing everything retrieved."""
    async with runtime.transaction() as connection:
        found = await search_lexical_chunks(
            connection, text=question, context=CONTEXT, limit=10
        )
    result = await generate_answer(
        question,
        build_evidence(found.candidates),
        BUDGET,
        CitingModel(),
        request_id=REQUEST_ID,
        timeout_seconds=5,
    )
    assert result.outcome is AnswerOutcome.ANSWERED
    return result


async def confirm(
    runtime: DatabaseRuntime,
    result: AnswerResult,
    context: AuthorizationContext = CONTEXT,
) -> AnswerResult:
    async with runtime.transaction() as connection:
        return await confirm_citations(
            connection, context, result, request_id=REQUEST_ID
        )


def assert_withdrawn(confirmed: AnswerResult, original: AnswerResult) -> None:
    assert confirmed.outcome is AnswerOutcome.WITHDRAWN
    assert (confirmed.text, confirmed.reason, confirmed.citations) == ("", None, ())
    assert confirmed.defect is None
    # The call was paid for, and the context report still explains it.
    assert confirmed.usage == original.usage
    assert confirmed.context == original.context


@pytest.mark.integration
async def test_a_current_answer_reaches_the_client_unchanged(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "leave.txt")
    await publish(runtime, "carry.txt", title="Carry-over", text=CARRY)
    result = await answer(runtime)

    confirmed = await confirm(runtime, result)

    assert len(result.citations) == 2
    assert confirmed == result


@pytest.mark.integration
async def test_a_renamed_document_is_cited_under_its_current_title(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "leave.txt", title="Leave policy")
    result = await answer(runtime)
    await publish(
        runtime, "leave.txt", title="Annual leave", version="v2", observed_at=LATER
    )

    confirmed = await confirm(runtime, result)

    assert confirmed.outcome is AnswerOutcome.ANSWERED
    (before,) = result.citations
    (after,) = confirmed.citations
    assert (after.chunk_id, after.excerpt) == (before.chunk_id, before.excerpt)
    assert (before.title, after.title) == ("Leave policy", "Annual leave")
    assert confirmed.text == result.text


async def revoke(runtime: DatabaseRuntime) -> None:
    await publish(
        runtime, "leave.txt", readers=(Subject.user(OTHER),), observed_at=LATER
    )


async def rewrite(runtime: DatabaseRuntime) -> None:
    await publish(
        runtime,
        "leave.txt",
        text="Every employee receives 26 days of annual leave.",
        version="v2",
        observed_at=LATER,
    )


async def tombstone(runtime: DatabaseRuntime) -> None:
    await tombstone_missing(
        runtime, source_id=SOURCE, seen={"carry.txt"}, observed_at=LATER
    )


async def purge(runtime: DatabaseRuntime) -> None:
    async with runtime.transaction() as connection:
        await connection.execute(
            sa.delete(chunks).where(chunks.c.text.contains("24 days"))
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [revoke, rewrite, tombstone, purge],
    ids=["access-revoked", "new-version", "tombstoned", "chunks-purged"],
)
async def test_an_answer_citing_a_document_that_changed_is_withdrawn(
    runtime: DatabaseRuntime, change: object
) -> None:
    await publish(runtime, "leave.txt")
    await publish(runtime, "carry.txt", title="Carry-over", text=CARRY)
    result = await answer(runtime)
    assert len(result.citations) == 2

    await change(runtime)  # type: ignore[operator]
    confirmed = await confirm(runtime, result)

    # One Citation failed, so the whole Answer is withheld, not just it.
    assert_withdrawn(confirmed, result)


@pytest.mark.integration
async def test_another_readers_context_cannot_confirm_the_answer(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "leave.txt")
    result = await answer(runtime)

    confirmed = await confirm(runtime, result, AuthorizationContext(user_id=OTHER))

    assert_withdrawn(confirmed, result)


async def forged(
    runtime: DatabaseRuntime, result: AnswerResult, *, private: DocumentId
) -> tuple[Citation, Citation]:
    """Two hand-made Citations of the private Document's Chunk."""
    async with runtime.transaction() as connection:
        record = (
            await connection.execute(
                sa.select(chunks.c.chunk_id).where(
                    chunks.c.document_id == private.value
                )
            )
        ).one()
    (real,) = result.citations
    secret = ChunkId(record.chunk_id)
    # A fresh Evidence ID each: otherwise the check refuses the repeat first.
    whole = replace(
        real, evidence_id=EvidenceId.new(), chunk_id=secret, document_id=private
    )
    relabelled = replace(real, evidence_id=EvidenceId.new(), chunk_id=secret)
    return whole, relabelled


@pytest.mark.integration
async def test_a_citation_of_a_chunk_the_reader_may_not_see_never_reaches_the_client(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await publish(runtime, "leave.txt")
    private = await publish(
        runtime,
        "secret.txt",
        title=MARKER,
        text=f"{MARKER} salary bands.",
        readers=(Subject.user(OTHER),),
    )
    result = await answer(runtime)

    for citation in await forged(runtime, result, private=private):
        sneaked = replace(result, citations=(*result.citations, citation))
        confirmed = await confirm(runtime, sneaked)
        assert_withdrawn(confirmed, sneaked)
        assert MARKER not in repr(confirmed)
    assert MARKER not in caplog.text
    assert all(MARKER not in repr(r.__dict__) for r in caplog.records)


@pytest.mark.integration
async def test_each_check_logs_counts_only(
    runtime: DatabaseRuntime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await publish(runtime, "leave.txt", title=MARKER)
    result = await answer(runtime)
    await revoke(runtime)

    await confirm(runtime, result)

    (record,) = [r for r in caplog.records if r.message == "citations confirmed"]
    assert record.__dict__["citation_check"] == {
        "request_id": str(REQUEST_ID),
        "cited": 1,
        "withdrawn": 1,
        "outcome": "withdrawn",
    }
    assert MARKER not in caplog.text


@pytest.mark.integration
@pytest.mark.parametrize(
    "field", ["document_id", "source_id", "content_version"], ids=str
)
async def test_a_citation_whose_identity_differs_from_storage_is_withdrawn(
    runtime: DatabaseRuntime, field: str
) -> None:
    await publish(runtime, "leave.txt")
    other = await publish(runtime, "carry.txt", title="Carry-over", text=CARRY)
    result = await answer(runtime, "24 days")
    (real,) = result.citations
    wrong = {
        "document_id": other,
        "source_id": SourceId(UUID(int=99)),
        "content_version": real.content_version.next(),
    }[field]
    relabelled = replace(result, citations=(replace(real, **{field: wrong}),))

    confirmed = await confirm(runtime, relabelled)

    assert_withdrawn(confirmed, relabelled)


@pytest.mark.integration
async def test_a_confirmed_citation_is_rebuilt_from_storage(
    runtime: DatabaseRuntime,
) -> None:
    await publish(runtime, "leave.txt")
    result = await answer(runtime)
    (real,) = result.citations
    forged = replace(
        real, title=MARKER, heading_path=MARKER, source_link=MARKER, excerpt=MARKER
    )

    confirmed = await confirm(runtime, replace(result, citations=(forged,)))

    assert confirmed.outcome is AnswerOutcome.ANSWERED
    assert confirmed.citations == (real,)
```

`publish` використовує `reconcile_document`, ту саму функцію, що й прогін синхронізації, тож кожна зміна в тестах справжня: нові читачі, новий текст чи нова назва. `tombstone_missing` позначає Документ видаленим так, як це робить прогін, коли файл зникає з джерела (Source). `CitingModel` бере дозволені ID з власної схеми промпта, тож цитує саме те, що повернув пошук.

Перевірка:

```bash
uv run pytest tests/test_current_citations.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_current_citations_integration.py -m integration -q
```

Очікувано: 19 passed, потім 13 passed. Якщо друга команда каже, що тестової бази немає, запустіть її командою `docker start knowledge-service-postgres`.

### Крок 5 — Без коду: зламайте і вирішіть

Цей крок показує, що стоїть між відкликаним Документом і клієнтом, і записує, чого ви навчилися.

**Дія 1:** навмисно зламайте перевірку. У `src/knowledge_service/answering/current_citations.py`, у `current_citations_statement`, видаліть цей рядок з виклику `.where(...)`:

```python
            eligibility,
```

Запустіть:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_current_citations.py tests/test_current_citations_integration.py -m "integration or not integration" -q
```

Очікувано: 6 failed, 26 passed. Падають тест запиту, відкликаний доступ, позначений видаленим Документ, інший читач, підроблена приватна цитата і тест логу. Нова версія й очищені Chunks і далі проходять, бо їхні старі ID Chunk однаково нічого не знаходять. Без цього одного рядка читач отримував би відповіді, побудовані з Документів, до яких утратив доступ, і з видалених. Поверніть рядок і запускайте знову, доки не пройдуть усі 32.

**Дія 2:** під `## Decision` запишіть:

- чому `resolve_citations` зливає повторений ID, а `read_citations` його відхиляє;
- чому одна невдала цитата відкликає всю відповідь, а не лише себе;
- чому відкликаний доступ, видалення і переписування виглядають для клієнта однаково;
- що лишається на наступні уроки: виклик `confirm_citations` зі стрімінгового ендпоінту (M06-T09), підтвердження тверджень (M06-T06), і що казати користувачеві про відкликану відповідь (M06-T07, M06-T13);
- щонайменше одне **спростоване припущення** у формі «Я припускав X. Запуск чи рев'ю показує Y. Отже, Z хибне».

### Крок 6 — Без коду: архітектура, перевірки, evidence і прогрес

Цей крок оновлює текст, який зміна зробила неповним, доводить, що все досі працює, і записує результат.

**Дія 1:** у `docs/architecture.md` оновіть рядок `answering`: повторені ID тепер цитуються один раз, а до списку додається `confirm_citations`. Знайдіть:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, repeated, malformed, or mismatched references are refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), and `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget) |
```

Замініть на:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), and `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows) |
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_current_citations.py tests/test_citations.py tests/test_generation.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний запуск дав 1909 passed для `make test-unit` і 342 passed для `make test-integration`: на 13 більше, ніж у M06-T04, і всі з цього уроку.

**Дія 3:** доповніть файл evidence:

- **Design:** змінені файли; чому повтори зливаються; що `confirm_citations` перевіряє і в якому порядку; запит; чому відкликається вся відповідь; що перебудовується зі сховища.
- **Verification:** команди й кількості, навмисний злам і відомі обмеження з «Перед початком».
- **Review:** адверсарне рев'ю не знайшло шляху від справжнього пошуку й генерації до цитати, якої читач не може бачити; воно знайшло п'ять способів, якими підроблений результат проходив, кожен виправлено тестом, що падав до виправлення: результат, заданий простим текстом, пропускав перевірку; результат, що не є відповіддю, зберігав цитати; «брехливий» UUID проходив порівняння; ID Chunk з текстом падав у базі з текстом у помилці; підроблені назва чи уривок зберігалися.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу виконаною, додайте рядок M06-T05 до `docs/curriculum/PROGRESS.md`, поставте наступною задачею M06-T06 і закомітьте з `m06-t05: validate citation identities`.

## Запитання для рефлексії

1. Що `confirm_citations` приховує від стрімінгового ендпоінту в M06-T09? Перелічіть, що тому коду ніколи не доведеться перевіряти перед надсиланням цитати.
2. Пізніша зміна хоче пропускати другий запит, коли відповідь зайняла менше секунди, бо «так швидко нічого не зміниться». Що все одно може піти не так, і який тест це впіймає?
3. Коли одна з п'яти цитат не проходить, відкликається вся відповідь. Це надто суворо для користувача, який міг би прочитати інші чотири, чи якраз правильно? Чому?
4. Клієнт отримує `WITHDRAWN`. Чому він не повинен дізнатися, чи Документ видалили, чи лише сховали від цього читача?
5. Який підроблений `AnswerResult` ви побудували б наступним, щоб спробувати провести цитату повз цю перевірку?

## Чекліст завершення

- [ ] Файл evidence містить інваріант і ваші передбачення, записані до коду.
- [ ] `resolve_citations` цитує повторений ID один раз; `read_citations` і далі відхиляє повтор.
- [ ] `AnswerOutcome.WITHDRAWN` існує, а `EXCERPT_SOURCE_CHARACTERS` — публічна.
- [ ] `confirm_citations` відхиляє підроблений чи зламаний результат з `TypeError` до будь-якого SQL.
- [ ] Результати, що не є відповіддю, проходять без запиту.
- [ ] Один запит перевіряє кожен процитований Chunk перевіркою прав пошуку і його поточною версією.
- [ ] Одна невдала цитата відкликає всю відповідь, зберігаючи її usage і звіт про контекст.
- [ ] Підтверджена цитата перебудовується зі сховища, тож перейменований Документ показує нову назву.
- [ ] Жодна назва, посилання чи текст не потрапляє в запис логу, repr чи помилку.
- [ ] Ви запустили навмисний злам, побачили 6 упалих тестів і скасували його.
- [ ] Усі перевірки проходять: форматування, лінтер, типи, юніт, інтеграційні, документація.
- [ ] Evidence, статус задачі, рядок PROGRESS і коміт готові.

Коли напишете файли, поділіться ними чи виводом помилок, і я перевірю їх за цим чеклістом.
