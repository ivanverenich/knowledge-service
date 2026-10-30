# M05-T05 — Реалізувати Reciprocal Rank Fusion

Англійська версія: [M05-T05-implement-reciprocal-rank-fusion.md](M05-T05-implement-reciprocal-rank-fusion.md)

Вихідне завдання: [M05-T05](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T05-implement-reciprocal-rank-fusion.md)

## Що ви зробите

У проєкті вже є два пошуки, і кожен повертає впорядкований список кандидатів (Candidate): векторний шукає фрагменти (Chunk) за змістом, лексичний — за словами. У цьому уроці ви напишете частину, яка зливає два списки в один, щоб фрагмент, який подобається обом пошукам, піднявся нагору. Метод називається **Reciprocal Rank Fusion** (RRF). Він дивиться лише на позиції у кожному списку і ніколи — на власні оцінки пошуків.

- Новий модуль `fusion.py` містить одну функцію: `fuse_ranked_candidates`.
- Кожен злитий кандидат показує, який пошук його знайшов, на якій позиції і скільки балів це дало. Нічого в остаточному порядку не приховано.
- Константа `k`, яка вирішує, скільки важить верхня позиція, — це налаштування зі значенням 60 за замовчуванням.
- Юніт-тести покривають списки, що перетинаються, списки без спільних фрагментів, відсутній пошук, нічиї та той самий фрагмент, указаний двічі.

| Завдання вимагає | Де це відбувається |
|---|---|
| Злити впорядковані векторний і лексичний списки | Крок 2: `fuse_ranked_candidates` |
| За стабільною ідентичністю фрагмента | Крок 2: кандидатів зіставляють за `chunk_id`, і кожен фрагмент повертається один раз |
| Налаштовуване k | Крок 2: аргумент `k`, за замовчуванням `DEFAULT_RRF_K = 60` |
| Прозорі внески в оцінку | Крок 2: `BranchContribution`, по одному на кожен пошук, що знайшов фрагмент |
| Перетин, списки без спільного, відсутні гілки, нічиї, дубльовані ідентичності | Крок 3: принаймні один тест на кожен випадок |
| Успіх, найризикованіший крайній випадок, одна типізована помилка | Крок 3: тест перетину; тест точної нічиєї; `InvalidFusionInput` |

У цьому уроці є програмування: кроки 2 і 3 пишуть код. Кроки 1, 4 і 5 — ні.

Терміни цього уроку:

- **Гілка (branch):** один спосіб пошуку. Тут їх дві: векторна (dense, за змістом) і лексична (lexical, за словами).
- **Злиття (fusion):** перетворення кількох впорядкованих списків на один. Як поєднати рейтинги двох суддів на конкурсі в один підсумковий.
- **Reciprocal Rank Fusion:** кожна гілка дає фрагменту `1 / (k + rank)` балів, і бали додаються. Перше місце при k=60 дає 1/61, друге — 1/62, і так далі.
- **Внесок (contribution):** бали, які одна гілка дала одному фрагменту.
- **k:** число, яке додають до кожної позиції. Мале k робить перше місце набагато ціннішим за п'яте. Велике k робить місця майже рівноцінними, тож найважливіше — щоб фрагмент знайшли обидві гілки.

## Перш ніж почати

Цей розділ пояснює рішення, на яких стоїть код, щоб кроки були зрозумілі, коли ви до них дійдете.

- **Що вже є.** Векторний пошук повертає `VectorCandidate` з відстанню, а лексичний — `LexicalCandidate` з оцінкою. Обидва виконують контракт `Candidate` з M05-T04, тож обидва мають `rank`, `chunk_id` і однакові поля походження (provenance).
- **Чому позиції, а не оцінки.** Відстань 0.3 і `ts_rank` 0.06 нічого не кажуть одна одній, до того ж одна краща, коли менша, а інша — коли більша. Позиції — єдине, що спільне для обох списків. RRF використовує лише позиції, тож не потребує налаштування, щоб узгодити дві шкали.
- **Чому k = 60 за замовчуванням.** В оригінальній статті про RRF використали 60, і це добре спрацювало в багатьох тестах. Урок залишає його налаштуванням, бо M05-T07 вимірюватиме на власних запитаннях (Question) цього проєкту.
- **Зіставлення за ID фрагмента.** Той самий фрагмент, знайдений обома гілками, має той самий `chunk_id`. Злиття додає його бали і повертає його один раз. Якщо дві гілки описують цей фрагмент по-різному, наприклад з іншим текстом чи іншою версією вмісту, то щось зламано раніше, тож злиття відмовляє, а не вибирає одну з версій.
- **Відсутня гілка.** Якщо гілки немає у вхідних даних або вона нічого не знайшла, вона не додає балів. Інша гілка зливається як звичайно, і її порядок не змінюється. Що робити, коли гілка падає або не встигає, вирішить наступне завдання, M05-T06.
- **Точні нічиї.** Два фрагменти можуть мати точно однакові бали. Тоді першим іде фрагмент з меншим ID — те саме правило, що в кожній гілці. Бали додаються як точні дроби, а не float, бо float може зламати справжню нічию. При k=60 `1/63 + 1/140` точно дорівнює `1/84 + 1/90`, але у float перша сума на один крок менша. З float порядок цих двох фрагментів залежав би від округлення, а не від правила нічиєї. Float-значення `score`, яке бачить викликач, — це точна сума, округлена один раз. Якщо самому додати округлені внески, результат може відрізнятися в останній цифрі.
- **Перевірка вхідних даних.** Злиття довіряє гілкам лише настільки, наскільки мусить. Воно кидає `InvalidFusionInput` у будь-якому з цих випадків:
  - `k`, яке не є додатним int;
  - назва гілки, яка не є `RetrievalBranch`;
  - кандидати гілки, які не є tuple чи list, наприклад `None`;
  - кандидат без `ChunkId`;
  - позиції, які не йдуть 1, 2, 3 по порядку;
  - фрагмент, який одна гілка вказує двічі.

  Якщо порахувати дублікат двічі, він несправедливо підніметься вгору, тож його відхиляють, а не зливають.
- **Права доступу.** Злиття нічого не читає з бази даних. Воно лише переставляє кандидатів, яких гілки вже повернули, а кожна гілка виконала перевірку доступу у своєму запиті. Тож злиття не може розкрити прихований фрагмент і ніколи не потребує читача.
- **Тиша.** Повідомлення про помилки називають гілку й позицію, але ніколи не значення з вхідних даних, тож ні тексту документа (Document), ні ID фрагмента. Злитий кандидат не показує текстові поля у своєму repr, як і інші кандидати.
- **Що знайшов огляд.** Незалежний огляд атакував прототип і знайшов три проблеми. Кожну тепер виправлено, і для кожної є тест, який падає на старому коді:
  - Документація обіцяла, що `score` дорівнює сумі перелічених внесків. Але кожен внесок округлюється окремо, тож вони можуть розходитися в останній цифрі. Тепер обіцянка каже, що `score` — це точна сума, округлена один раз, і тест перевіряє обидва твердження.
  - Гілка, передана як `None`, кандидат, що є `None`, або ID фрагмента неправильного типу падали зі звичайною помилкою Python. `DocumentId` замість ID фрагмента навіть дозволяв одному фрагменту повернутися двічі. Тепер злиття відхиляє все це з `InvalidFusionInput`.
  - Помилка позиції друкувала саму позицію, тож зламана гілка, що передала текст як позицію, розкривала цей текст. Тепер повідомлення називає лише гілку й позицію, а тест на витік перевіряє також примітки (notes) і ланцюжки помилок.

  Деякі знахідки — відомі обмеження, записані в доказах (evidence) замість виправлення:
  - Поле походження, порівняння якого саме кидає помилку, вийшло б назовні нетипізованим. Справжні кандидати містять лише ID, int і рядки, тож з ними такого не буває.
  - З величезним k, наприклад 10**17, різні точні оцінки можуть округлитися до того самого float. Показані оцінки тоді рівні, а порядок усе одно йде за точними значеннями.
  - Злиття відкидає те, що властиве кожній гілці: відстань, лексичну оцінку й підсвічування. Наступне завдання вирішить, що з цього нести далі.

  Огляд також підтвердив, що трималося. Порядок у словнику викликача і hash seed Python ніколи не змінювали результат. Результат завжди містив рівно вхідні фрагменти. 100 000 кандидатів на гілку зливалися приблизно за секунду.

- **Запуск тестів.** Цим тестам база даних не потрібна:

  ```bash
  uv run pytest tests/test_reciprocal_rank_fusion.py -q
  ```

- **Файли, які ви зміните:**
  - новий `src/knowledge_service/retrieval/fusion.py`
  - новий `tests/test_reciprocal_rank_fusion.py`
  - `docs/architecture.md`, новий файл доказів і наприкінці `PROGRESS.md`

Цього разу без міграції й без змін у базі даних.

## Покрокове проходження

### Крок 1 — Без програмування: запишіть інваріант і свої передбачення

На цьому кроці ви ще до коду записуєте, що має бути правдою завжди і що ви очікуєте побачити. На кроці 4 ви порівняєте свої здогадки з реальними результатами.

**Дія:** створіть `docs/evidence/M05-T05-reciprocal-rank-fusion.md`:

```markdown
# M05-T05 — Reciprocal Rank Fusion evidence

Completed: pending

## Invariant

Fusion returns each Chunk that any branch found exactly once, and no other
Chunk. Its score is the sum of `1 / (k + rank)` over the branches that
found it, and it lists each branch's part in branch order. Fused
Candidates are ordered by exact score, highest first, then by Chunk ID.
Bad input is refused with `InvalidFusionInput` before anything is
returned, and no Document text appears in an error message or a repr.

## Expected failing check

Before the change, `tests/test_reciprocal_rank_fusion.py` fails to import
`knowledge_service.retrieval.fusion`.

## Predictions

Before running anything, write what you expect:

- Dense search ranks Chunk 1 first, and lexical search does not find it.
  Chunk 2 is fourth in both lists. Which comes first with k=60? With k=1?
- One Chunk is first in dense search only, another first in lexical search
  only. What are their scores, and which comes first?
- Lexical search found nothing. What does fusion return?
- Lexical search lists the same Chunk twice. What should fusion do?
- `1/63 + 1/140` and `1/84 + 1/90` are equal. Do Python floats agree?

## Design

Pending.

## Verification

Pending.

## Reflection

Pending.
```

**Перевірка:** файл існує, і в ньому ваші власні передбачення.

### Крок 2 — Крок з програмуванням: напишіть функцію злиття

Цей крок будує функцію, яка зливає впорядковані списки обох пошуків в один.

**Простими словами:** Цей файл — саме злиття. Спершу воно перевіряє, що налаштування і кожен список мають сенс. Потім проходить список кожного пошуку в сталому порядку, дає кожному фрагменту бали за його позицію і додає бали, коли обидва пошуки знайшли той самий фрагмент. Наостанок воно сортує фрагменти за балами, розв'язує точні нічиї за ID фрагмента і повертає їх із записом, звідки взявся кожен бал.

| Частина | Що вона робить |
|---|---|
| `DEFAULT_RRF_K` | 60, константа зі статті |
| `RetrievalBranch` | дві назви гілок, `dense` і `lexical`, у порядку, в якому перелічено внески |
| `InvalidFusionInput` | єдина помилка, яку кидає злиття, — це `FusionError` |
| `BranchContribution` | внесок однієї гілки: гілка, її позиція там і `1 / (k + rank)` |
| `FusedCandidate` | поля походження, злита позиція `rank`, `score` і `contributions`; виконує контракт `Candidate` |
| `fuse_ranked_candidates` | перевіряє вхідні дані, додає внески як дроби, сортує й нумерує результат |
| `_provenance` | поля, у яких дві гілки мають погоджуватися щодо того самого фрагмента |

Кілька рядків варто пояснити:

- `type(k) is not int` відхиляє `True`, бо в Python `bool` — це різновид `int`. Проєкт використовує цю перевірку всюди, де число має бути справжнім int.
- `type(branch) is not RetrievalBranch` відхиляє звичайний рядок `"dense"`. Елемент `StrEnum` дорівнює своєму рядку, тож без перевірки звичайний рядок тихо спрацював би як гілка.
- Цикл іде по `RetrievalBranch`, а не по словнику. Тож внески виходять в одному порядку, хоч би в якому порядку викликач заповнив словник.
- `isinstance(candidates, tuple | list)` відхиляє `None` та інші форми ще до циклу. Відсутню гілку записують, просто не додаючи її, або як порожній tuple.
- `getattr(candidate, "chunk_id", None)` читає ID, не падаючи на кандидаті `None`. Потім перевірка `type(...) is not ChunkId` відхиляє `None`, голий UUID або `DocumentId` на його місці.
- `rank != position` перевіряє, що позиції йдуть 1, 2, 3 у порядку списку. RRF довіряє позиції, тож список із пропуском чи повтором дав би неправильні бали.
- `Fraction(1, k + rank)` — точний внесок. Сортування використовує точну суму, а `float()` застосовується лише під час побудови результату.

**Дія:** створіть `src/knowledge_service/retrieval/fusion.py`:

```python
"""Combine the ranked Candidates of several branches into one ranked list.

Reciprocal Rank Fusion gives a Candidate `1 / (k + rank)` from every branch
that found it and adds the parts up. Only positions count: a vector distance
and a lexical score are on scales that mean nothing to each other, so fusion
never reads them. A larger `k` flattens the curve, so being found by several
branches counts for more than being first in one.

What `fuse_ranked_candidates` promises:

- **One per Chunk.** A Chunk found by several branches is returned once, with
  one contribution from each branch that found it.
- **Transparent.** Each fused Candidate lists, in branch order, the branch,
  its rank there, and the part it added. `score` is the exact sum of the
  parts, rounded to a float once, so adding the rounded parts can differ
  from it in the last digit.
- **Deterministic.** Fused Candidates are ordered by their exact score,
  highest first, then by Chunk ID, the tie-break every branch uses. Scores
  are compared as fractions, because two different sums can be equal while
  their floats differ in the last digit.
- **Missing branches.** A branch that is absent or found nothing adds
  nothing; the others are fused as usual.
- **Checked input.** A `k` that is not a positive int, a branch that is not
  a `RetrievalBranch`, a branch's Candidates that are not a tuple or a list,
  a Candidate without a `ChunkId`, ranks that are not 1, 2, 3, ..., a Chunk
  listed twice by one branch, or a Chunk two branches describe differently
  raise `InvalidFusionInput` before anything is returned. Messages name the
  branch and the position, never a value from the input.
- **Nothing new is read.** Fusion only reorders Candidates the branches
  already returned, so every fused Candidate passed the permission check of
  the branch that found it.
- **Quiet.** The fields holding Document text are kept out of repr.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.retrieval.candidates import Candidate

# The constant of the original Reciprocal Rank Fusion paper.
DEFAULT_RRF_K = 60


class RetrievalBranch(StrEnum):
    """One way of finding Candidates, in the order contributions are listed."""

    DENSE = "dense"
    LEXICAL = "lexical"


class FusionError(Exception):
    """Ranked Candidates could not be fused."""


class InvalidFusionInput(FusionError):
    """`k` or a branch's Candidates break the contract; nothing was fused."""


@dataclass(frozen=True, slots=True)
class BranchContribution:
    """What one branch added to a fused Candidate: `1 / (k + rank)`."""

    branch: RetrievalBranch
    rank: int
    contribution: float


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    """One eligible Chunk after fusion, where it came from, and why it ranks here.

    `rank` is its 1-based position in the fused result. `contributions` holds
    one part per branch that found the Chunk, in branch order, and `score` is
    their exact sum rounded once.
    The fields holding Document text are kept out of repr, so logging a
    Candidate never shows them.
    """

    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    score: float
    contributions: tuple[BranchContribution, ...]
    external_id: str = field(repr=False)
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_anchor: str | None = field(repr=False)
    text: str = field(repr=False)


@dataclass(slots=True)
class _Found:
    candidate: Candidate
    score: Fraction
    contributions: list[BranchContribution]


def fuse_ranked_candidates(
    ranked: Mapping[RetrievalBranch, Sequence[Candidate]],
    *,
    k: int = DEFAULT_RRF_K,
) -> tuple[FusedCandidate, ...]:
    """Fuse each branch's ranked Candidates by Chunk ID, best fused score first."""
    # bool is an int, so `True` would otherwise pass as a k of 1.
    if type(k) is not int or k < 1:
        raise InvalidFusionInput("k must be a positive int")
    for branch in ranked:
        # A plain "dense" equals RetrievalBranch.DENSE as a key, so check the type.
        if type(branch) is not RetrievalBranch:
            raise InvalidFusionInput("every branch must be a RetrievalBranch")

    found: dict[ChunkId, _Found] = {}
    for branch in RetrievalBranch:
        candidates = ranked.get(branch, ())
        if not isinstance(candidates, tuple | list):
            raise InvalidFusionInput(f"{branch} Candidates must be a tuple or a list")
        seen: set[ChunkId] = set()
        for position, candidate in enumerate(candidates, start=1):
            # Read with getattr, so a None or a stranger in the list is refused
            # with the typed error instead of an AttributeError.
            if type(getattr(candidate, "chunk_id", None)) is not ChunkId:
                raise InvalidFusionInput(
                    f"{branch} has no ChunkId at position {position}"
                )
            rank = getattr(candidate, "rank", None)
            if type(rank) is not int or rank != position:
                raise InvalidFusionInput(
                    f"{branch} has a wrong rank at position {position}"
                )
            if candidate.chunk_id in seen:
                raise InvalidFusionInput(
                    f"{branch} lists a Chunk twice, again at position {position}"
                )
            seen.add(candidate.chunk_id)

            part = Fraction(1, k + rank)
            contribution = BranchContribution(branch, rank, float(part))
            earlier = found.get(candidate.chunk_id)
            if earlier is None:
                found[candidate.chunk_id] = _Found(candidate, part, [contribution])
                continue
            if _provenance(earlier.candidate) != _provenance(candidate):
                raise InvalidFusionInput(
                    f"{branch} describes the Chunk at position {position} "
                    "differently from an earlier branch"
                )
            earlier.score += part
            earlier.contributions.append(contribution)

    ordered = sorted(
        found.values(),
        key=lambda item: (-item.score, item.candidate.chunk_id.value),
    )
    return tuple(_fused(rank, item) for rank, item in enumerate(ordered, start=1))


def _provenance(candidate: Candidate) -> tuple[object, ...]:
    return (
        candidate.chunk_id,
        candidate.document_id,
        candidate.source_id,
        candidate.content_version,
        candidate.ordinal,
        candidate.external_id,
        candidate.title,
        candidate.heading_path,
        candidate.source_anchor,
        candidate.text,
    )


def _fused(rank: int, item: _Found) -> FusedCandidate:
    candidate = item.candidate
    return FusedCandidate(
        rank=rank,
        chunk_id=candidate.chunk_id,
        document_id=candidate.document_id,
        source_id=candidate.source_id,
        content_version=candidate.content_version,
        ordinal=candidate.ordinal,
        score=float(item.score),
        contributions=tuple(item.contributions),
        external_id=candidate.external_id,
        title=candidate.title,
        heading_path=candidate.heading_path,
        source_anchor=candidate.source_anchor,
        text=candidate.text,
    )
```

**Перевірка:**

```bash
uv run ruff check src/knowledge_service/retrieval/fusion.py
uv run pyright src/knowledge_service/retrieval/fusion.py
```

Очікувано: жодних зауважень і `0 errors`.

### Крок 3 — Крок з програмуванням: протестуйте злиття через його інтерфейс

Цей крок доводить, що злиття виконує кожну обіцянку, на маленьких списках, зроблених вручну, без бази даних.

**Простими словами:** Ці тести будують невеликі впорядковані списки з обох пошуків, де кожен фрагмент — просто число, і перевіряють, що повертає злиття. Вони покривають списки зі спільними фрагментами, списки без спільного, відсутній пошук, точні нічиї, змінене `k` і всі види поганих вхідних даних. Останній тест зливає 500 випадкових пар списків і звіряє порядок з точною арифметикою.

| Тест | Що він доводить |
|---|---|
| `test_a_chunk_both_branches_found_is_returned_once_with_both_parts` | Перетин: один результат на фрагмент, обидва внески перелічено, оцінка — їхня точна сума, округлена один раз |
| `test_a_fused_candidate_keeps_the_provenance_of_its_chunk` | Кожне поле походження проходить без змін |
| `test_disjoint_lists_interleave_and_equal_scores_go_by_chunk_id` | Списки без спільного: однакові місця дають нічию, і виграє менший ID фрагмента |
| `test_a_missing_branch_adds_nothing` | Відсутня або порожня гілка не додає балів |
| `test_one_branch_alone_keeps_its_own_order` | З однією гілкою злиття зберігає її порядок |
| `test_no_candidates_is_an_empty_result` | Нічого на вході — нічого на виході |
| `test_the_order_of_branches_in_the_mapping_changes_nothing` | Порядок у словнику викликача не змінює результат |
| `test_equal_exact_scores_tie_by_chunk_id_even_when_their_floats_differ` | Найризикованіший крайній випадок: справжня нічия, яку float зламав би |
| `test_k_decides_whether_one_top_rank_beats_two_middle_ranks` | `k` справді використовується і змінює порядок |
| `test_the_default_k_is_the_papers_60` | За замовчуванням 60 |
| `test_a_bad_k_is_refused` | 0, -1, `True`, 1.5, 60.0, підклас int, `"60"` і `None` кидають `InvalidFusionInput` |
| `test_a_branch_that_is_not_a_retrieval_branch_is_refused` | Звичайний ключ `"dense"` відхиляється |
| `test_candidates_that_are_not_a_tuple_or_a_list_are_refused` | `None`, рядок, ітератор і множина відхиляються |
| `test_a_candidate_without_a_chunk_id_is_refused` | `None`, голий UUID, `DocumentId`, список і відсутній ID відхиляються |
| `test_ranks_that_do_not_count_from_one_in_order_are_refused` | Пропуски, повтори, неправильний порядок, 0, `True`, 1.0 і підклас int відхиляються |
| `test_a_chunk_one_branch_lists_twice_is_refused` | Дубльована ідентичність в одній гілці відхиляється |
| `test_a_chunk_two_branches_describe_differently_is_refused` | Кожне поле походження має збігатися між гілками |
| `test_errors_and_reprs_never_show_document_text` | Підкинутий маркер ніколи не з'являється в repr, повідомленні про помилку, її примітках чи ланцюжку помилок |
| `test_a_fused_candidate_meets_the_candidate_contract` | Pyright приймає злитого кандидата як `Candidate` |
| `test_random_lists_fuse_by_exact_score_then_chunk_id` | 500 випадкових випадків: правильні фрагменти, правильні внески, точний порядок |

`_Int` — підклас `int`, щоб перевірити, що злиття хоче саме звичайний int. Допоміжні функції роблять кандидатів зі звичайних чисел. `_chunk(5)` — це ID фрагмента, чий UUID дорівнює числу 5, тож правило нічиєї легко читати: фрагмент 5 іде перед фрагментом 7. `_dense(1, 2, 3)` дає векторний список із фрагментом 1 на позиції 1, фрагментом 2 на позиції 2 і фрагментом 3 на позиції 3. `_words` робить те саме для лексичного пошуку.

Тесту нічиєї потрібні списки довжиною 80. Він ставить фрагмент 1 на векторну позицію 3 і лексичну 80, а фрагмент 2 — на векторну 24 і лексичну 30. Ці дві суми точно рівні, а на всіх інших місцях стоять фрагменти-заповнювачі.

**Дія:** створіть `tests/test_reciprocal_rank_fusion.py`:

```python
"""Checks on fusing the ranked Candidates of dense and lexical search."""

import random
from collections.abc import Mapping, Sequence
from dataclasses import replace
from fractions import Fraction
from typing import Any, cast
from uuid import UUID

import pytest

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.fusion import (
    DEFAULT_RRF_K,
    BranchContribution,
    FusedCandidate,
    InvalidFusionInput,
    RetrievalBranch,
    fuse_ranked_candidates,
)
from knowledge_service.retrieval.lexical_search import LexicalCandidate
from knowledge_service.retrieval.vector_search import VectorCandidate

DENSE = RetrievalBranch.DENSE
LEXICAL = RetrievalBranch.LEXICAL
MARKER = "zebramarker"


class _Int(int):
    """An int subclass, which is not a plain int."""


def _chunk(number: int) -> ChunkId:
    return ChunkId(UUID(int=number))


def _vector(number: int, rank: int, text: str = "") -> VectorCandidate:
    return VectorCandidate(
        rank=rank,
        chunk_id=_chunk(number),
        document_id=DocumentId(UUID(int=10_000 + number)),
        source_id=SourceId(UUID(int=1)),
        content_version=ContentVersion(1),
        ordinal=0,
        distance=rank / 100,
        external_id=f"doc-{number}{text}.txt",
        title=f"Title {number}{text}",
        heading_path=f"Heading {number}{text}",
        source_anchor=f"#chunk-{number}{text}",
        text=f"Text of Chunk {number}{text}",
    )


def _lexical(number: int, rank: int, text: str = "") -> LexicalCandidate:
    vector = _vector(number, rank, text)
    return LexicalCandidate(
        rank=rank,
        chunk_id=vector.chunk_id,
        document_id=vector.document_id,
        source_id=vector.source_id,
        content_version=vector.content_version,
        ordinal=vector.ordinal,
        score=1 / rank,
        highlights=(),
        external_id=vector.external_id,
        title=vector.title,
        heading_path=vector.heading_path,
        source_anchor=vector.source_anchor,
        text=vector.text,
    )


def _dense(*numbers: int) -> tuple[VectorCandidate, ...]:
    return tuple(_vector(number, rank) for rank, number in enumerate(numbers, 1))


def _words(*numbers: int) -> tuple[LexicalCandidate, ...]:
    return tuple(_lexical(number, rank) for rank, number in enumerate(numbers, 1))


def _order(fused: Sequence[FusedCandidate]) -> list[int]:
    return [candidate.chunk_id.value.int for candidate in fused]


def _part(rank: int, k: int = DEFAULT_RRF_K) -> float:
    return float(Fraction(1, k + rank))


def test_a_chunk_both_branches_found_is_returned_once_with_both_parts() -> None:
    fused = fuse_ranked_candidates({DENSE: _dense(1, 2, 3), LEXICAL: _words(3, 4)})

    assert _order(fused) == [3, 1, 2, 4]
    assert [candidate.rank for candidate in fused] == [1, 2, 3, 4]
    assert fused[0].contributions == (
        BranchContribution(DENSE, 3, _part(3)),
        BranchContribution(LEXICAL, 1, _part(1)),
    )
    assert fused[0].score == float(Fraction(1, 63) + Fraction(1, 61))
    assert fused[0].score == pytest.approx(
        sum(part.contribution for part in fused[0].contributions)
    )
    assert fused[1].contributions == (BranchContribution(DENSE, 1, _part(1)),)
    assert fused[3].contributions == (BranchContribution(LEXICAL, 2, _part(2)),)


def test_a_fused_candidate_keeps_the_provenance_of_its_chunk() -> None:
    fused = fuse_ranked_candidates({DENSE: _dense(1), LEXICAL: _words(1)})
    source = _vector(1, 1)

    assert fused[0].chunk_id == source.chunk_id
    assert fused[0].document_id == source.document_id
    assert fused[0].source_id == source.source_id
    assert fused[0].content_version == source.content_version
    assert fused[0].ordinal == source.ordinal
    assert fused[0].external_id == source.external_id
    assert fused[0].title == source.title
    assert fused[0].heading_path == source.heading_path
    assert fused[0].source_anchor == source.source_anchor
    assert fused[0].text == source.text


def test_disjoint_lists_interleave_and_equal_scores_go_by_chunk_id() -> None:
    fused = fuse_ranked_candidates({DENSE: _dense(5, 7), LEXICAL: _words(6, 4)})

    assert _order(fused) == [5, 6, 4, 7]
    assert [len(candidate.contributions) for candidate in fused] == [1, 1, 1, 1]
    assert fused[0].score == fused[1].score == _part(1)


@pytest.mark.parametrize(
    "ranked",
    [
        {LEXICAL: _words(2, 1)},
        {DENSE: (), LEXICAL: _words(2, 1)},
    ],
    ids=["absent", "empty"],
)
def test_a_missing_branch_adds_nothing(
    ranked: Mapping[RetrievalBranch, Sequence[Candidate]],
) -> None:
    fused = fuse_ranked_candidates(ranked)

    assert _order(fused) == [2, 1]
    assert fused[0].contributions == (BranchContribution(LEXICAL, 1, _part(1)),)


def test_one_branch_alone_keeps_its_own_order() -> None:
    fused = fuse_ranked_candidates({DENSE: _dense(9, 3, 6)})

    assert _order(fused) == [9, 3, 6]


@pytest.mark.parametrize(
    "ranked",
    [{}, {DENSE: (), LEXICAL: ()}],
    ids=["no-branches", "both-empty"],
)
def test_no_candidates_is_an_empty_result(
    ranked: Mapping[RetrievalBranch, Sequence[Candidate]],
) -> None:
    assert fuse_ranked_candidates(ranked) == ()


def test_the_order_of_branches_in_the_mapping_changes_nothing() -> None:
    forward = fuse_ranked_candidates({DENSE: _dense(1, 2), LEXICAL: _words(2, 3)})
    backward = fuse_ranked_candidates({LEXICAL: _words(2, 3), DENSE: _dense(1, 2)})

    assert forward == backward
    assert [part.branch for part in forward[0].contributions] == [DENSE, LEXICAL]


def test_equal_exact_scores_tie_by_chunk_id_even_when_their_floats_differ() -> None:
    # With k=60, 1/63 + 1/140 equals 1/84 + 1/90 exactly, but as floats the
    # first sum is one step smaller. Chunk 1 has the first pair and the lower
    # Chunk ID, so it must come first.
    dense = [1_000 + rank for rank in range(1, 81)]
    words = [2_000 + rank for rank in range(1, 81)]
    dense[3 - 1], words[80 - 1] = 1, 1
    dense[24 - 1], words[30 - 1] = 2, 2

    fused = fuse_ranked_candidates({DENSE: _dense(*dense), LEXICAL: _words(*words)})

    order = _order(fused)
    assert order.index(1) + 1 == order.index(2)
    assert fused[order.index(1)].score == fused[order.index(2)].score


def test_k_decides_whether_one_top_rank_beats_two_middle_ranks() -> None:
    ranked = {DENSE: _dense(1, 11, 12, 2), LEXICAL: _words(21, 22, 23, 2)}

    assert _order(fuse_ranked_candidates(ranked, k=1))[0] == 1
    assert _order(fuse_ranked_candidates(ranked, k=60))[0] == 2


def test_the_default_k_is_the_papers_60() -> None:
    fused = fuse_ranked_candidates({DENSE: _dense(1)})

    assert DEFAULT_RRF_K == 60
    assert fused[0].score == 1 / 61


@pytest.mark.parametrize("k", [0, -1, True, 1.5, 60.0, _Int(60), "60", None])
def test_a_bad_k_is_refused(k: object) -> None:
    with pytest.raises(InvalidFusionInput):
        fuse_ranked_candidates({DENSE: _dense(1)}, k=cast(int, k))


def test_a_branch_that_is_not_a_retrieval_branch_is_refused() -> None:
    ranked = cast(Mapping[RetrievalBranch, Sequence[Candidate]], {"dense": _dense(1)})

    with pytest.raises(InvalidFusionInput):
        fuse_ranked_candidates(ranked)


@pytest.mark.parametrize(
    "ranks",
    [(2, 3), (1, 1), (1, 3), (2, 1), (0, 1), (True, 2), (1.0, 2), (_Int(1), 2)],
)
def test_ranks_that_do_not_count_from_one_in_order_are_refused(
    ranks: tuple[int, int],
) -> None:
    candidates = (_vector(1, ranks[0]), _vector(2, ranks[1]))

    with pytest.raises(InvalidFusionInput):
        fuse_ranked_candidates({DENSE: candidates})


@pytest.mark.parametrize(
    "candidates",
    [None, "ab", iter(_dense(1)), {_vector(1, 1)}],
    ids=["none", "string", "iterator", "set"],
)
def test_candidates_that_are_not_a_tuple_or_a_list_are_refused(
    candidates: object,
) -> None:
    ranked = cast(Mapping[RetrievalBranch, Sequence[Candidate]], {DENSE: candidates})

    with pytest.raises(InvalidFusionInput):
        fuse_ranked_candidates(ranked)


@pytest.mark.parametrize(
    "candidate",
    [
        None,
        replace(_vector(1, 1), chunk_id=cast(ChunkId, UUID(int=1))),
        replace(_vector(1, 1), chunk_id=cast(ChunkId, DocumentId(UUID(int=1)))),
        replace(_vector(1, 1), chunk_id=cast(ChunkId, [1])),
        replace(_vector(1, 1), chunk_id=cast(ChunkId, None)),
    ],
    ids=["none", "uuid", "document-id", "unhashable", "no-id"],
)
def test_a_candidate_without_a_chunk_id_is_refused(candidate: object) -> None:
    ranked = {DENSE: _dense(1), LEXICAL: (cast(Candidate, candidate),)}

    with pytest.raises(InvalidFusionInput):
        fuse_ranked_candidates(ranked)


def test_a_chunk_one_branch_lists_twice_is_refused() -> None:
    candidates = (_lexical(1, 1), _lexical(2, 2), _lexical(1, 3))

    with pytest.raises(InvalidFusionInput):
        fuse_ranked_candidates({LEXICAL: candidates})


@pytest.mark.parametrize(
    "change",
    [
        {"document_id": DocumentId(UUID(int=99))},
        {"source_id": SourceId(UUID(int=99))},
        {"content_version": ContentVersion(2)},
        {"ordinal": 1},
        {"external_id": "other.txt"},
        {"title": "Other"},
        {"heading_path": None},
        {"source_anchor": None},
        {"text": "Other text"},
    ],
)
def test_a_chunk_two_branches_describe_differently_is_refused(
    change: dict[str, Any],
) -> None:
    lexical = replace(_lexical(1, 1), **change)

    with pytest.raises(InvalidFusionInput):
        fuse_ranked_candidates({DENSE: _dense(1), LEXICAL: (lexical,)})


def test_errors_and_reprs_never_show_document_text() -> None:
    dense = (_vector(1, 1, MARKER), _vector(2, 2, MARKER))
    fused = fuse_ranked_candidates({DENSE: dense})

    assert MARKER not in repr(fused)

    text_as_rank = replace(dense[0], rank=cast(int, dense[0].text))
    failures: list[Mapping[RetrievalBranch, Sequence[Candidate]]] = [
        {DENSE: (*dense, _vector(1, 3, MARKER))},
        {DENSE: dense, LEXICAL: (_lexical(1, 1, MARKER + "x"),)},
        {DENSE: (_vector(1, 2, MARKER),)},
        {DENSE: (text_as_rank,)},
    ]
    for ranked in failures:
        with pytest.raises(InvalidFusionInput) as caught:
            fuse_ranked_candidates(ranked)
        error = caught.value
        shown = [str(error), repr(error.args), *getattr(error, "__notes__", [])]
        assert all(MARKER not in text for text in shown)
        assert all(str(_chunk(1).value) not in text for text in shown)
        assert error.__cause__ is None
        assert error.__context__ is None


def test_a_fused_candidate_meets_the_candidate_contract() -> None:
    fused: Sequence[Candidate] = fuse_ranked_candidates({DENSE: _dense(1)})

    assert fused[0].rank == 1


def test_random_lists_fuse_by_exact_score_then_chunk_id() -> None:
    generator = random.Random(5)  # noqa: S311
    for _ in range(500):
        k = generator.randint(1, 100)
        dense = generator.sample(range(40), generator.randint(0, 25))
        words = generator.sample(range(40), generator.randint(0, 25))
        ranked = {DENSE: _dense(*dense), LEXICAL: _words(*words)}

        fused = fuse_ranked_candidates(ranked, k=k)

        assert sorted(_order(fused)) == sorted(set(dense) | set(words))
        assert [candidate.rank for candidate in fused] == list(range(1, len(fused) + 1))
        keys: list[tuple[Fraction, int]] = []
        for candidate in fused:
            number = candidate.chunk_id.value.int
            expected = [
                BranchContribution(branch, numbers.index(number) + 1, 0.0)
                for branch, numbers in ((DENSE, dense), (LEXICAL, words))
                if number in numbers
            ]
            assert [(p.branch, p.rank) for p in candidate.contributions] == [
                (p.branch, p.rank) for p in expected
            ]
            exact = sum(
                (Fraction(1, k + part.rank) for part in candidate.contributions),
                Fraction(0),
            )
            assert candidate.score == float(exact)
            keys.append((-exact, number))
        assert keys == sorted(keys)
```

**Перевірка:**

```bash
uv run pytest tests/test_reciprocal_rank_fusion.py -q
```

Очікувано: `51 passed`.

**Навмисна поломка.** Змусьте злиття додавати float замість дробів. У `fusion.py` змініть:

```python
            part = Fraction(1, k + rank)
```

на:

```python
            part = 1 / (k + rank)
```

Запустіть тести знову. Очікувано: 2 failed, `test_equal_exact_scores_tie_by_chunk_id_even_when_their_floats_differ` і `test_random_lists_fuse_by_exact_score_then_chunk_id`. Pyright теж скаржиться, бо float — не `Fraction`. Скасуйте зміну і запустіть тести ще раз: усі проходять.

### Крок 4 — Без програмування: подивіться, як k змінює порядок

Цей крок показує внески наочно і те, як `k` вирішує між «першим в одному списку» і «знайденим обома».

**Дія:** з кореня проєкту запустіть:

```bash
uv run python - <<'PY'
import sys

sys.path.insert(0, "tests")
from test_reciprocal_rank_fusion import DENSE, LEXICAL, _dense, _words

from knowledge_service.retrieval.fusion import fuse_ranked_candidates

ranked = {DENSE: _dense(1, 11, 12, 2), LEXICAL: _words(21, 22, 23, 2)}
for k in (60, 1):
    print(f"k={k}")
    for fused in fuse_ranked_candidates(ranked, k=k)[:3]:
        parts = ", ".join(
            f"{part.branch} #{part.rank} +{part.contribution:.4f}"
            for part in fused.contributions
        )
        print(f"  {fused.rank}. Chunk {fused.chunk_id.value.int}: {fused.score:.4f} = {parts}")
PY
```

Еталонний запуск надрукував:

```text
k=60
  1. Chunk 2: 0.0312 = dense #4 +0.0156, lexical #4 +0.0156
  2. Chunk 1: 0.0164 = dense #1 +0.0164
  3. Chunk 21: 0.0164 = lexical #1 +0.0164
k=1
  1. Chunk 1: 0.5000 = dense #1 +0.5000
  2. Chunk 21: 0.5000 = lexical #1 +0.5000
  3. Chunk 2: 0.4000 = dense #4 +0.2000, lexical #4 +0.2000
```

Як це читати:

- При k=60 виграє фрагмент 2. Четверте місце в обох списках дає 2/64, більше, ніж перше місце в одному списку, 1/61.
- При k=1 виграє фрагмент 1. Перше місце важить 1/2, а двічі четверте місце дає лише 2/5.
- Фрагменти 1 і 21 мають нічию в обох запусках, і першим іде менший ID фрагмента, 1.
- Кожен рядок показує, звідки взявся кожен бал. Це і є «прозорі внески в оцінку», яких вимагає завдання.

Запишіть у доказах, які з ваших передбачень із кроку 1 справдилися.

**Перевірка:** ваш вивід збігається з еталонним, а в доказах названо одне передбачення, яке не справдилося, або сказано, що таких немає.

### Крок 5 — Без програмування: перевірки, документація, докази й прогрес

Цей крок доводить, що все досі працює, оновлює єдиний рядок архітектури, який зміна зробила застарілим, і записує результат.

**Дія 1:** у `docs/architecture.md` змініть рядок `retrieval` таблиці "Code layout" на:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `MetadataCondition` |
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_reciprocal_rank_fusion.py -q
make format-check
make lint
make typecheck
make test-integration
make check
make docs-check
```

Очікувано: усе проходить. Еталонний запуск дав `make check` 1325 passed, 3 deselected, а `make test-integration` — 284 passed. Запускайте `make check` із заданою адресою бази даних, інакше його тести бази даних пропускаються, а не виконуються:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make check
```

**Дія 3:** заповніть файл доказів:

- **Design:** функція та її типи, чому злиття використовує позиції, а не оцінки, чому `k` за замовчуванням 60, точні дроби для нічиїх і що змінив огляд.
- **Verification:** команди й кількості, таблиця тестів із тим, що доводить кожен, вивід кроку 4, навмисна поломка з кроку 3 і відомі обмеження з розділу «Перш ніж почати».
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте завдання виконаним, додайте рядок M05-T05 у `docs/curriculum/PROGRESS.md`, укажіть наступним завданням M05-T06 і зробіть коміт із `m05-t05: implement reciprocal rank fusion`.

## Запитання для роздумів

1. Що `fuse_ranked_candidates` приховує від коду, який її викликає? Перелічіть, про що викликачеві ніколи не треба думати, наприклад як розв'язуються нічиї або що буває, коли один пошук нічого не повернув.
2. Хтось згодом пришвидшує злиття, додаючи float замість дробів. Яка обіцянка ламається, як часто читач це помітив би і який тест це ловить?
3. Хтось інший «спрощує» злиття: фрагмент, який один пошук указав двічі, зливають, а не відхиляють. Чому це було б несправедливо щодо інших фрагментів?
4. Як дізнатися, чи k = 60 підходить цьому проєкту? Подумайте, що б ви вимірювали на справжніх запитаннях і який результат змусив би вас вибрати інше k.
5. Злиття довіряє лише позиціям. Придумайте випадок, коли один пошук явно кращий для запитання, наприклад для коду помилки на кшталт `E-1042`. Як RRF може дати гірший порядок, ніж цей пошук сам по собі?

## Контрольний список

- [ ] Файл доказів містить інваріант і ваші передбачення, записані до будь-якого запуску.
- [ ] `fusion.py` визначає `fuse_ranked_candidates`, `FusedCandidate`, `BranchContribution`, `RetrievalBranch` і `InvalidFusionInput`.
- [ ] Кожен фрагмент повертається один раз, з одним внеском від кожного пошуку, що його знайшов, і з оцінкою, що дорівнює їхній точній сумі.
- [ ] `k` — налаштування зі значенням 60 за замовчуванням, а погане `k` відхиляється.
- [ ] Погані вхідні дані, наприклад `None` чи неправильний тип ID, кидають `InvalidFusionInput`, а не звичайну помилку Python.
- [ ] Точні нічиї розв'язуються за ID фрагмента, навіть коли float не погоджується.
- [ ] Тести покривають перетин, списки без спільного, відсутню гілку, нічиї та дубльовані ідентичності, і всі проходять.
- [ ] Ви виконали навмисну поломку, побачили, що тести падають, і скасували її.
- [ ] Ви запустили команду з кроку 4 і порівняли результат зі своїми передбаченнями.
- [ ] Усі перевірки проходять: форматування, лінтер, типи, юніт-, інтеграційні тести, документація.
- [ ] Рядок архітектури, докази, рядок у PROGRESS і коміт готові.

Коли напишете код, поділіться ним або виводом помилки, і я перевірю його за цим списком.
