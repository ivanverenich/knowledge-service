# M06-T12 — Розв'яжіть уточнювальні запитання

Англійська версія: [M06-T12-resolve-follow-up-questions.md](M06-T12-resolve-follow-up-questions.md)

Вихідне завдання: [M06-T12](../curriculum/milestones/M06-answers-and-conversations/M06-T12-resolve-follow-up-questions.md)

## Що ви зробите

Цей урок перетворює уточнювальне запитання на кшталт «А за кордоном?» на пошуковий текст, який називає свою тему: «Які добові у відрядженні за кордоном?». Пошук читає один текст за раз, тож уточнення саме по собі знаходить не ті Chunks або нічого. Переписування має бути безпечним: воно може додати лише те, що вже звучало в розмові, тією самою мовою і не губячи код на кшталт `EXP-2041`.

- `resolve_question` бере історію читача з M06-T11 і просить модель переписування з M05-T15 розв'язати запитання, але лише коли це уточнення.
- Для уточнень вона ще раз перевіряє переписаний текст: мова і сутності попереднього запитання мають зберегтися.
- `compare_resolution` зберігає двомовні тестові розмови, читає їх назад як історію і вимірює пошук із розв'язанням і без нього.

| Завдання вимагає | Де це відбувається |
|---|---|
| Структуроване переписування лише для класифікованих уточнень | Крок 2: до моделі потрапляє лише клас `follow_up`, через переписувач зі Structured Outputs з M05-T15 |
| Зберегти мову оригіналу | Крок 2: кожне нове слово переписаного тексту має бути в абетці заявленої мови |
| Зберегти сутності | Крок 2: слова запитання, які треба зберегти, а також ідентифікатори й абревіатури найновішого попереднього запитання |
| Простежуваний переписаний запит | Крок 2: `ResolvedQuestion` тримає обидва тексти і те, як обрано пошуковий текст; один запис логу з назвами й лічильниками |
| Двомовні розмовні випадки вимірюють пошук із розв'язанням і без | Кроки 3 і 4: `compare_resolution` на англійських і українських уточненнях |
| Тести: успіх, найризикованіший край, типізована відмова | Крок 4: `tests/test_follow_up.py` і `tests/test_follow_up_integration.py` |
| Авторизація до того, як дані залишають сховище | Крок 2: до моделі потрапляють лише власні запитання читача, прочитані з прив'язкою до власника через `load_history`; ніколи відповідь чи Chunk |
| Жодного підкладеного тексту в телеметрії | Крок 4: підкладений маркер ніколи не потрапляє в запис логу, repr, помилку чи звіт |

Цей урок містить код: кроки 2, 3 і 4 пишуть код. Кроки 1, 5 і 6 — без коду. Інтеграційним тестам потрібна тестова база. Жоден крок не звертається до OpenAI: тести використовують фальшивий переписувач з M05-T15.

Терміни цього уроку:

- **Уточнення (follow-up):** запитання, яке має сенс лише разом із попередніми ходами, як-от «А за кордоном?» чи «Скільки це триває?».
- **Розв'язання (resolution):** переписування уточнення в один самодостатній пошуковий текст за допомогою попередніх запитань. Тут це вузький різновид переписування запиту.
- **Клас переписування (rewrite class):** мітка, яку `classify_question` (M05-T14) дає кожному запитанню, як-от `follow_up`, `acronym` чи `exact_identifier`; вона вирішує, чи можна пробувати переписування.
- **Сутність (entity):** тут — слово, що називає одну точну річ і не має змінюватися, як-от код помилки `EXP-2041` чи абревіатура `VPN`.
- **Запасний варіант (fallback):** пошук за запитанням як є, бо переписування не вдалося або порушило правило.

## Перед початком

Цей розділ пояснює рішення, на яких стоїть код, щоб кожен крок був зрозумілий, коли ви до нього дійдете.

- **Що вже є.** M05-T14 класифікує запитання; M05-T15 надсилає переписуване запитання до `RewriteModel` зі Structured Outputs, перевіряє відповідь через `checked_rewrite` і в разі чого шукає за запитанням як є. M06-T11 дає історію читача. Поки ніщо їх не з'єднує.
- **Лише уточнення.** Прапорець розгортання `QUERY_REWRITE_CLASSES` може вмикати кілька класів, але прогін M05-T15 дозволив лише `follow_up`, а переписування абревіатур зашкодило одному випадку. Тому розв'язувач перетинає будь-яку політику з одним лише `follow_up`: запитання з абревіатурою ніколи не надсилається, навіть якщо прапорець це дозволяє.
- **Надсилаються лише запитання.** Історія містить і відповіді, але відповідь — це текст, написаний із Chunks. Переписувачу потрібна тема, а її називають власні запитання користувача, тож відповіді він ніколи не бачить. Запитання беруться з `load_history`, яка читає лише власну живу розмову читача.
- **Лише найновіший відрізок.** Надсилається щонайбільше десять попередніх запитань, від найновішого назад. Якщо одне з них не можна шукати, наприклад воно довше, ніж дозволено пошуковому тексту, відрізок на ньому закінчується. Інакше старіша тема могла б зайняти місце тієї, про яку читач щойно питав.
- **Мова.** M05-T15 дозволяє переписаному тексту повторювати будь-яке слово, яке набрав користувач, у будь-якій абетці. У двомовній розмові це діра: англійське попереднє запитання могло б перетворити українське уточнення на англійський пошук. Тож для уточнення переписаний текст перевіряється ще раз так, ніби набрано лише саме запитання і перенесені сутності. Запитання змішаної чи невідомої мови ніколи не надсилається, бо його абетку не перевірити.
- **Сутності.** Уточнення зазвичай продовжує найновіше попереднє запитання. Його ідентифікатори й абревіатури мають бути в переписаному тексті: «How do I fix it?» після «What does error EXP-2041 mean?» має зберегти `EXP-2041`. Переписаний текст, що загубив хоч одну, не використовується.
- **Простежуваність.** `ResolvedQuestion` тримає запитання як є, пошуковий текст, клас, результат, причину запасного варіанта, переписувач, оплачене використання і кількість надісланих попередніх запитань. Тексти не потрапляють у його repr, а єдиний запис логу має лише назви й лічильники.
- **Вимірювання.** `compare_resolution` програє двомовні випадки переписування з M05-T15 як збережені розмови: кожне попереднє запитання зберігається через `add_turn` з фіксованою невдалою відповіддю, історія читається назад, і запитання розв'язується. Потім кожен випадок шукається як є і, якщо його розв'язано, ще раз за пошуковим текстом, щільним і лексичним пошуком разом. Результат має форму M05-T15, тож `score_rewriting` дає виграш, шкоду і рішення про розгортання для кожного класу.
- **Що знайшло рев'ю.** Незалежне агресивне рев'ю обстріляло розв'язувач випадковими запитаннями, історіями, мовами, політиками й відповідями моделі, з маркером у кожному тексті. До моделі потрапляв лише `follow_up` англійською чи українською, щонайбільше десять попередніх запитань, ніколи відповідь, а маркер ні разу не потрапив у repr, запис логу чи помилку. Рев'ю знайшло такі проблеми; кожну виправлено з тестом, що падає без виправлення:
  - Українське уточнення, переписане англійськими словами попереднього англійського запитання, використовувалося, бо M05-T15 дозволяє будь-якому набраному слову зберегти свою абетку. Тепер переписане уточнення перевіряється ще раз так, ніби набрано лише запитання і перенесені сутності.
  - Історія, що містила щось, крім тексту, як-от `None` чи один рядок замість кортежу, падала з нетипізованою помилкою або розбивалася на літери. Тепер вона відхиляється з `InvalidRewriteRequest`, перш ніж щось надіслано.
  - Найновіше запитання, яке не можна шукати, наприклад задовге, пропускалося, і його місце займала старіша тема. Тепер надісланий відрізок на ньому закінчується.
  - `compare_resolution` зіставляв випадки за позицією, не перевіряючи, що набір даних прийшов з його завантажувача. Тепер невідповідний набір відхиляється з `DatasetRunRefused`.
  - Допустимий ID випадку із закінченням `-resolved` міг збігтися з розв'язаною копією і забрати її ранги. Тепер копії закінчуються на `+resolved`, якого не може містити жоден ID.
  - Одне обмеження лишається: сутність, яку пошуковий синтаксис прочитав би інакше, як-от `x_or_y`, зберегти неможливо, тож її уточнення завжди шукаються як є.
- **Де живе код.** Два нові модулі, `answering/follow_up.py` і `answering/follow_up_evaluation.py`, та їхні тести. Наявний код не змінюється; `docs/architecture.md` отримує дві правки.
- **Відомі обмеження.** Живий прогін з OpenAI тут неможливий, тож виміряні числа дає фальшивий переписувач, який лише приєднує найновіше попереднє запитання до уточнення. Для цих трьох уточнень розв'язувач надсилає рівно те саме, що надсилав M05-T15, тож його живий виграш досі чинний. Сутність, яку пошуковий синтаксис прочитав би інакше, як-от `x_or_y`, зберегти неможливо, тож її уточнення завжди йдуть запасним варіантом. У потоці відповіді `resolve_question` поки ніхто не викликає, і пошуковий текст не зберігається разом із ходом.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої передбачення

Поки коду ще немає, запишіть, що має бути правдою наприкінці уроку. Крок 5 перевіряє передбачення.

Створіть `docs/evidence/M06-T12-resolve-follow-up-questions.md`:

```markdown
# M06-T12 — Resolve follow-up Questions evidence

## Invariant

`resolve_question` sends a Question to the rewrite model only when
`classify_question` calls it `follow_up`, the policy switches `follow_up`
on, and its stated language is English or Ukrainian. It sends the Question
and at most the ten newest earlier Questions of the reader's History, never
an Answer or a Chunk. It uses a rewrite only when every new word is in the
stated language and the rewrite keeps the Question's words to keep and the
identifiers and acronyms of the newest earlier Question; otherwise the
search text is the Question as typed. The `ResolvedQuestion` records both
texts and how the search text was chosen. Reprs, log records, errors, and
reports hold no Question or rewrite text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.follow_up"`
fails with `ModuleNotFoundError`.

## Predictions

- Which rewrite classes reach the model when the policy switches on every rewritable class: ...
- What happens when a Ukrainian follow-up comes back rewritten with the English words of an earlier English Question: ...
- With a rewriter that only joins the newest earlier Question to the follow-up, whether every follow-up case ranks better, and what the rollout decision is: ...
- How many unit tests fail if the resolver passes the policy on without narrowing it to `follow_up`: ...

## Design

## Verification

## Decision

## Reflection
```

Перевірка: файл існує, перевірка, що має впасти, падає, а кожне передбачення — повне речення, яке крок 5 може спростувати.

### Крок 2 — Крок із кодом: розв'язувач

Цей крок пише модуль, який вирішує, чи розв'язувати запитання і з яким пошуковим текстом шукати.

**Простими словами:** маючи запитання, його заявлену мову та історію читача, модуль збирає найновіші попередні запитання і дає наявному переписувачу спробувати переписати текст, але лише для уточнення англійською чи українською. Якщо переписаний текст повернувся, модуль перевіряє його ще раз: жодних слів іншої мови, а коди й абревіатури попереднього запитання на місці. Він повертає текст для пошуку і запис про те, як його обрано.

| Частина | Що містить або робить |
|---|---|
| `FOLLOW_UP_ONLY` | Політика, в якій увімкнено лише `follow_up` |
| `ResolvedQuestion` | Обидва тексти, клас, результат, запасний варіант, переписувач, використання, кількість попередніх запитань, перенесені сутності |
| `earlier_questions` | Найновіший відрізок запитань історії, щонайбільше `MAX_HISTORY`, до першого, яке не можна шукати |
| `resolve_question` | Звужує політику, викликає `rewrite_question`, повторно перевіряє переписане уточнення через `checked_rewrite`, пише лог один раз |
| `_entities` | Ідентифікатори й абревіатури запитання, знайдені класифікацією без мови |

Створіть `src/knowledge_service/answering/follow_up.py`:

```python
"""Follow-up resolution: one standalone search text for a follow-up Question.

"And abroad?" names its subject only through the turns before it, and
search reads one text. `resolve_question` takes the reader's History
(`answering.history`) and, only for a Question that `classify_question`
calls a follow-up, asks a `RewriteModel` for a standalone search text such
as "What is the daily allowance abroad?". `rewrite_question` (M05-T15)
sends the request and checks the rewrite; this module decides what may be
sent, adds the follow-up's own check, and records how the text was chosen.

What resolution promises:

- **Only follow-ups.** Whatever classes a `RewritePolicy` switches on, only
  `follow_up` is sent. A Question of another class, one with no earlier
  Question in its History, or one whose policy leaves `follow_up` off is
  searched as typed, and nothing is sent.
- **Only the reader's Questions.** The model sees the Question and the
  earlier Questions of the History, oldest first: never an Answer, a
  Chunk, or a Document. History comes from `load_history`, so they are
  the reader's own. At most `MAX_HISTORY` of the newest are sent, and
  never past one that could not be searched, such as one longer than a
  search text may be: the stretch sent always ends at the newest Question.
- **The language kept.** A Question is sent only in a language whose
  alphabet can be checked, English or Ukrainian. Every word of the
  rewrite that is not a word of the Question or an entity carried over
  must be in that alphabet, so an English earlier Question cannot turn a
  Ukrainian follow-up into an English search. A Question stated as mixed
  or unknown is searched as typed.
- **The entities kept.** A rewrite must keep the Question's words to keep
  (identifiers, acronyms, words in the other alphabet) and the
  identifiers and acronyms of the newest earlier Question, the one a
  follow-up most often continues: "How do I fix it?" after "What does
  error EXP-2041 mean?" must keep `EXP-2041`. A rewrite that drops one is
  not used. An entity search syntax would misread, such as `x_or_y`, can
  never be kept, so its follow-ups are always searched as typed.
- **Traceable.** A `ResolvedQuestion` holds the Question as typed, the
  search text, the class, the outcome, the fallback, the rewriter, the
  usage, and how many earlier Questions were sent. Texts stay out of its
  repr; one log record holds counts and names only.
- **Never half a rewrite.** On any failure the search text is the
  Question as typed, and the usage of a billed answer is kept.
"""

import logging
from dataclasses import dataclass, field, replace
from uuid import UUID

from knowledge_service.answering.history import History, HistoryTurn
from knowledge_service.contracts import Usage
from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.query_rewriting import (
    InvalidRewrite,
    InvalidRewriteRequest,
    RewriteModel,
    RewriteOutcome,
    RewritePolicy,
    RewriterIdentity,
    checked_rewrite,
    rewrite_question,
)
from knowledge_service.retrieval.rewrite_classes import (
    MAX_HISTORY,
    InvalidQuestion,
    RewriteClass,
    classify_question,
)

logger = logging.getLogger(__name__)

# The only class resolution may send.
FOLLOW_UP_ONLY = RewritePolicy(frozenset({RewriteClass.FOLLOW_UP}))
# The languages whose alphabet `checked_rewrite` can check.
_CHECKED_LANGUAGES = frozenset({Language.ENGLISH, Language.UKRAINIAN})


@dataclass(frozen=True, slots=True)
class ResolvedQuestion:
    """The text to search with, and how it was chosen.

    `search_text` is the checked rewrite when `outcome` is `rewritten`, and
    the Question as typed otherwise. `carried` holds the entities of the
    newest earlier Question that the rewrite had to keep.
    """

    question: str = field(repr=False)
    search_text: str = field(repr=False)
    language: Language
    rewrite_class: RewriteClass
    outcome: RewriteOutcome
    fallback: str | None
    rewriter: RewriterIdentity
    usage: Usage | None
    earlier_questions: int
    carried: tuple[str, ...] = field(repr=False)

    @property
    def resolved(self) -> bool:
        """True when the search text is a rewrite, not the Question as typed."""
        return self.outcome is RewriteOutcome.REWRITTEN


def earlier_questions(history: History) -> tuple[str, ...]:
    """The newest stretch of the History's Questions a rewrite may use.

    Summarized Questions come before whole turns, oldest first. Taken from
    the newest back, at most `MAX_HISTORY`, and stopping at the first that
    could not be searched, so no older topic takes the newest one's place.
    Raises `InvalidRewriteRequest` for a History that is not one.
    """
    if (
        type(history) is not History
        or type(history.earlier_questions) is not tuple
        or type(history.turns) is not tuple
        or not all(type(turn) is HistoryTurn for turn in history.turns)
    ):
        raise InvalidRewriteRequest("history must be a History")
    questions = [
        *history.earlier_questions,
        *(turn.question for turn in history.turns),
    ]
    if not all(type(question) is str for question in questions):
        raise InvalidRewriteRequest("history must be a History")
    newest: list[str] = []
    for question in reversed(questions):
        if len(newest) == MAX_HISTORY or not _searchable(question):
            break
        newest.append(question)
    return tuple(reversed(newest))


async def resolve_question(
    model: RewriteModel,
    question: str,
    *,
    language: Language,
    history: History,
    policy: RewritePolicy,
    request_id: UUID,
    timeout_seconds: float,
) -> ResolvedQuestion:
    """Resolve a follow-up into a standalone search text, or keep the Question.

    Raises `InvalidQuestion` for a Question or language `classify_question`
    refuses, and `InvalidRewriteRequest` for a History or policy of the
    wrong type or a bad timeout; both before anything is sent.
    """
    if type(language) is not Language:
        raise InvalidQuestion("language must be a Language")
    if type(policy) is not RewritePolicy:
        raise InvalidRewriteRequest("policy must be a RewritePolicy")
    earlier = earlier_questions(history)
    switched_on = (
        policy.classes & FOLLOW_UP_ONLY.classes
        if language in _CHECKED_LANGUAGES
        else frozenset[RewriteClass]()
    )
    result = await rewrite_question(
        model,
        question,
        language=language,
        history=earlier,
        policy=RewritePolicy(switched_on),
        request_id=request_id,
        timeout_seconds=timeout_seconds,
    )
    carried: tuple[str, ...] = ()
    if result.plan.rewrite_class is RewriteClass.FOLLOW_UP:
        carried = _entities(earlier[-1])
    search_text, outcome, fallback = result.search_text, result.outcome, result.fallback
    if outcome is RewriteOutcome.REWRITTEN:
        try:
            # Checked again as if only the Question and the carried
            # entities were typed: earlier words in another alphabet, or
            # identifiers of older Questions, may not come along.
            checked_rewrite(
                search_text,
                replace(result.plan, preserve=(*result.plan.preserve, *carried)),
                question=question,
                history=carried,
            )
        except InvalidRewrite:
            search_text, outcome = question, RewriteOutcome.FALLBACK
            fallback = InvalidRewrite.__name__
    resolved = ResolvedQuestion(
        question=question,
        search_text=search_text,
        language=language,
        rewrite_class=result.plan.rewrite_class,
        outcome=outcome,
        fallback=fallback,
        rewriter=result.identity,
        usage=result.usage,
        earlier_questions=len(earlier),
        carried=carried,
    )
    _log(request_id, resolved)
    return resolved


def _searchable(question: str) -> bool:
    """True for text `classify_question` accepts as an earlier Question."""
    if not question.strip() or len(question) > MAX_QUERY_CHARACTERS:
        return False
    try:
        question.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _entities(question: str) -> tuple[str, ...]:
    """The identifiers and acronyms of a Question, as typed.

    Classified as of no language, no alphabet counts as foreign, so only
    identifiers and acronyms are words to keep.
    """
    return classify_question(question, language=Language.UNKNOWN).preserve


def _log(request_id: UUID, resolved: ResolvedQuestion) -> None:
    """Record how the search text was chosen: names and counts, no text."""
    fields: dict[str, object] = {
        "request_id": str(request_id),
        "provider": resolved.rewriter.provider,
        "model": resolved.rewriter.model,
        "rewrite_class": str(resolved.rewrite_class),
        "outcome": str(resolved.outcome),
        "fallback": resolved.fallback,
        "earlier_questions": resolved.earlier_questions,
        "carried": len(resolved.carried),
    }
    if resolved.usage is not None:
        fields["input_tokens"] = resolved.usage.input_tokens
        fields["output_tokens"] = resolved.usage.output_tokens
    logger.info("follow-up resolved", extra={"follow_up": fields})
```

Друга перевірка повторно використовує `checked_rewrite` з M05-T15 замість нових правил. Вона передає перенесені сутності як єдиний «попередній» текст, тож лише вони можуть зберегти свою абетку, і додає їх до слів, які переписаний текст мусить зберегти.

Перевірка:

```bash
uv run python -c "import knowledge_service.answering.follow_up"
uv run pyright src/knowledge_service/answering/follow_up.py
```

Очікувано: імпорт нічого не друкує, а pyright показує 0 помилок.

### Крок 3 — Крок із кодом: вимірювання

Цей крок пише порівняння пошуку з розв'язанням і без нього.

**Простими словами:** для кожного тестового випадку модуль створює розмову оцінювального читача, зберігає попередні запитання випадку як ходи, читає їх назад як історію і розв'язує запитання випадку. Потім він один раз завантажує тестові документи і шукає кожен випадок як є, а якщо його розв'язано, то ще раз за розв'язаним текстом. Він повертає ранги обох пошуків у форматі, який M05-T15 уже вміє оцінювати й друкувати.

| Частина | Що робить |
|---|---|
| `compare_resolution` | Відмовляє набору даних не з його завантажувача, розв'язує кожен випадок, запускає один об'єднаний пошук обох версій, зіставляє результати |
| `render_resolution` | Звіт M05-T15 із приміткою, що увімкнено лише `follow_up` |
| `_resolve` | Один випадок: зберегти ходи, `load_history`, `resolve_question`; помилка в коді стає `CaseFailed`, що називає лише тип помилки |
| `RESOLVED_SUFFIX` | `+resolved`, якого не може містити жоден ID випадку, тож розв'язана копія ніколи не займе місце іншого випадку |

Створіть `src/knowledge_service/answering/follow_up_evaluation.py`:

```python
"""Retrieval with and without follow-up resolution, on stored Conversations.

`compare_resolution` replays each case of the bilingual rewrite dataset
(M05-T15) as a Conversation of the evaluation reader: the case's earlier
Questions are stored as turns, `load_history` reads them back, and
`resolve_question` resolves the case's Question with only `follow_up`
switched on. Then the corpus is ingested and embedded once, and every case
is searched as typed and, when it was resolved, again with its search
text, both by dense and lexical search fused.

The result is a `RewriteComparison`, so `score_rewriting`
(`retrieval.rewrite_evaluation`) gives the gain, the harm, and the rollout
decision per class, and `render_resolution` reports them by case ID, with
no Question or rewrite in them.
The earlier turns are stored with a fixed failed reply, because only their
Questions reach the rewriter.
"""

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5

from knowledge_service.answering.follow_up import (
    FOLLOW_UP_ONLY,
    ResolvedQuestion,
    resolve_question,
)
from knowledge_service.answering.history import load_history
from knowledge_service.contracts import Usage
from knowledge_service.conversations import ConversationLimits, MessageOutcome
from knowledge_service.database import DatabaseRuntime
from knowledge_service.embeddings.port import EmbeddingModel
from knowledge_service.persistence.conversations import add_turn, start_conversation
from knowledge_service.pricing import ModelPrice, estimate_cost
from knowledge_service.retrieval.evaluation_dataset import (
    DEFAULT_LIMIT,
    READER,
    CaseFailed,
    DatasetRunRefused,
    RewriteCaseOutcome,
    RewriteComparison,
    RewriteDataset,
    compare_branches,
)
from knowledge_service.retrieval.query_rewriting import RewriteModel
from knowledge_service.retrieval.rewrite_cases import RewriteCase
from knowledge_service.retrieval.rewrite_evaluation import (
    render_rewrite_comparison,
    score_rewriting,
)

# The reply stored after each earlier Question; History withholds it.
EARLIER_REPLY = "No answer was kept for this evaluation turn."
HISTORY_TOKENS = 4_000
RESOLVE_TIMEOUT_SECONDS = 30.0
# A dataset case ID is lowercase words and hyphens, so none ends like this.
RESOLVED_SUFFIX = "+resolved"

_SWITCHED_ON = "- Every rewritable class was switched on for this run."
_RESOLVED_ON = (
    "- Only follow_up was switched on; each case was resolved through its"
    " stored History."
)
_REQUEST_IDS = UUID("6f0c2b4e-31d5-4a8e-9c71-5e2d0b7a9f12")


async def compare_resolution(
    runtime: DatabaseRuntime,
    rewrite: RewriteDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: EmbeddingModel,
    rewriter: RewriteModel,
    price: ModelPrice | None,
    limit: int = DEFAULT_LIMIT,
) -> RewriteComparison:
    """Resolve every case through its stored History, then search both ways.

    Raises like `compare_branches`; `DatasetRunRefused` before anything
    is written when `rewrite` did not come from its loader; `CaseFailed`
    when resolving a case raises, naming only error types, or when the
    rewriter changes its identity during the run.
    """
    # Each case is searched with one Question and resolved with the same.
    if type(rewrite) is not RewriteDataset or [
        (case.case_id, case.question, case.question_language)
        for case in rewrite.dataset.cases
    ] != [(case.case_id, case.question, case.language) for case in rewrite.labelled]:
        raise DatasetRunRefused("the rewrite dataset must come from its loader")
    identity = rewriter.identity
    resolved: list[ResolvedQuestion] = []
    for label in rewrite.labelled:
        result = await _resolve(runtime, label, rewriter, observed_at)
        if result.rewriter != identity:
            raise CaseFailed(f"case {label.case_id}: the rewriter changed its identity")
        resolved.append(result)
    typed = rewrite.dataset.cases
    again = tuple(
        replace(
            case,
            case_id=f"{case.case_id}{RESOLVED_SUFFIX}",
            question=result.search_text,
        )
        for case, result in zip(typed, resolved, strict=True)
        if result.resolved
    )
    combined = replace(rewrite.dataset, cases=(*typed, *again))
    reports = await compare_branches(
        runtime, combined, corpus, observed_at=observed_at, model=model, limit=limit
    )
    fused = {outcome.case_id: outcome for outcome in reports[-1].outcomes}
    cases: list[RewriteCaseOutcome] = []
    for case, result in zip(typed, resolved, strict=True):
        before = fused[case.case_id]
        after = before
        if result.resolved:
            after = replace(
                fused[f"{case.case_id}{RESOLVED_SUFFIX}"], case_id=case.case_id
            )
        cases.append(
            RewriteCaseOutcome(
                case_id=case.case_id,
                rewrite_class=result.rewrite_class,
                outcome=result.outcome,
                fallback=result.fallback,
                before=before,
                after=after,
            )
        )
    usages = tuple(result.usage for result in resolved if result.usage is not None)
    return RewriteComparison(
        name=rewrite.dataset.name,
        version=rewrite.dataset.version,
        limit=limit,
        embedding=model.identity,
        rewriter=identity,
        cases=tuple(cases),
        usages=usages,
        cost_usd=_cost(usages, price),
    )


def render_resolution(comparison: RewriteComparison) -> str:
    """The comparison as Markdown: gain per class, every case, cost, rollout."""
    return render_rewrite_comparison(comparison, score_rewriting(comparison)).replace(
        _SWITCHED_ON, _RESOLVED_ON
    )


async def _resolve(
    runtime: DatabaseRuntime,
    label: RewriteCase,
    rewriter: RewriteModel,
    observed_at: datetime,
) -> ResolvedQuestion:
    """Store the case's earlier turns, read its History, resolve its Question."""
    limits = ConversationLimits()
    resolved: ResolvedQuestion | None = None
    failure = "no result"
    try:
        async with runtime.transaction() as connection:
            started = await start_conversation(
                connection, owner_id=READER.user_id, now=observed_at, limits=limits
            )
            for question in label.history:
                await add_turn(
                    connection,
                    owner_id=READER.user_id,
                    conversation_id=started.conversation_id,
                    question=question,
                    reply=EARLIER_REPLY,
                    outcome=MessageOutcome.FAILED,
                    references=(),
                    now=observed_at,
                    limits=limits,
                )
            history = await load_history(
                connection,
                READER,
                conversation_id=started.conversation_id,
                now=observed_at,
                budget_tokens=HISTORY_TOKENS,
            )
        if history is not None:
            resolved = await resolve_question(
                rewriter,
                label.question,
                language=label.language,
                history=history,
                policy=FOLLOW_UP_ONLY,
                request_id=uuid5(_REQUEST_IDS, f"resolve {label.case_id}"),
                timeout_seconds=RESOLVE_TIMEOUT_SECONDS,
            )
    except Exception as error:
        # A bug or a broken request; its message may quote a Question.
        failure = type(error).__name__
    # Raised outside the `except`, so no linked error carries a text.
    if resolved is None:
        raise CaseFailed(f"case {label.case_id}: resolving raised {failure}")
    return resolved


def _cost(usages: tuple[Usage, ...], price: ModelPrice | None) -> Decimal | None:
    """The estimated cost of every billed answer; None if any is unknown."""
    if price is None:
        return None
    total = Decimal(0)
    for usage in usages:
        cost = estimate_cost(usage, {price.price_id: price})
        if cost is None:
            return None
        total += cost
    return total
```

Перевірка:

```bash
uv run pyright src/knowledge_service/answering/follow_up_evaluation.py
```

Очікувано: 0 помилок.

### Крок 4 — Крок із кодом: тести

Цей крок доводить кожну обіцянку: спершу з фальшивим переписувачем у пам'яті, потім на справжньому PostgreSQL.

**Простими словами:** юніт-тести дають розв'язувачу запитання кожного класу і фальшиві переписані тексти, добрі й погані, і перевіряють, що надіслано і за чим шукатимуть. Тест властивостей подає випадкові переписані тексти і перевіряє, що використаний текст завжди зберігає сутності. Інтеграційні тести зберігають двомовні випадки як розмови, запускають порівняння і перевіряють три речі: надіслано лише уточнення, розв'язання знаходить Chunk, якого уточнення саме не знаходить, а перевірка розгортання відмовляє розв'язанню, що шкодить випадку.

| Тест | Що доводить |
|---|---|
| `test_a_follow_up_is_resolved_with_the_earlier_questions` | Успіх, з надісланими попередніми запитаннями |
| `test_a_ukrainian_follow_up_is_resolved_in_ukrainian` | Успіх українською |
| `test_only_a_follow_up_is_ever_sent` | Структуроване переписування лише для класифікованих уточнень |
| `test_a_rewrite_that_drops_an_entity_of_the_earlier_question_is_not_used` | Сутності збережено: найризикованіший край |
| `test_a_follow_up_rewritten_in_the_language_of_an_earlier_question_is_not_used` | Мову збережено у двомовній розмові |
| `test_any_text_the_model_returns_is_used_only_when_it_keeps_the_entities` | Тест властивостей на випадкових переписаних текстах |
| `test_only_questions_are_sent_never_an_answer` | Текст відповіді не йде до переписувача |
| `test_a_bad_request_is_refused_before_anything_is_sent` | Типізована відмова: `InvalidQuestion` або `InvalidRewriteRequest` |
| `test_no_question_or_rewrite_reaches_a_repr_or_a_log_record` | Жодного підкладеного маркера в телеметрії |
| `test_only_follow_ups_are_resolved_through_their_stored_history` | Справжній шлях: збережені ходи, історія, розв'язувач |
| `test_resolution_is_measured_case_by_case_and_guards_the_rollout` | З розв'язанням і без, двомовні випадки |

Створіть `tests/test_follow_up.py`:

```python
"""Follow-up resolution: only follow-ups, language and entities kept, traced."""
# ruff: noqa: RUF001

import logging
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from knowledge_service.answering.follow_up import (
    FOLLOW_UP_ONLY,
    ResolvedQuestion,
    earlier_questions,
    resolve_question,
)
from knowledge_service.answering.history import History, HistoryTurn
from knowledge_service.language import Language
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.query_rewriting import (
    InvalidRewriteRequest,
    RewriteOutcome,
    RewritePolicy,
    RewriterUnavailable,
)
from knowledge_service.retrieval.rewrite_classes import (
    MAX_HISTORY,
    REWRITABLE,
    InvalidQuestion,
    RewriteClass,
    RewritePlan,
    question_words,
)
from rewriting_fakes import FakeRewriteModel

REQUEST = UUID("00000000-0000-0000-0000-0000000000a1")
EN = Language.ENGLISH
UK = Language.UKRAINIAN
MARKER = "PLANTED-MARKER-7719"
EVERYTHING = RewritePolicy(REWRITABLE)


def history(*questions: str, answers: tuple[str | None, ...] = ()) -> History:
    """Every Question as a whole turn, with its Answer if one is given."""
    turns = tuple(
        HistoryTurn(
            question=question,
            answer=answers[n] if n < len(answers) else None,
            references=(),
        )
        for n, question in enumerate(questions)
    )
    return History(earlier_questions=(), turns=turns, tokens=0, left_out=0, withheld=0)


def says(text: object) -> FakeRewriteModel:
    def answer(question: str, plan: RewritePlan, history: tuple[str, ...]) -> object:
        return text

    return FakeRewriteModel(answer=answer)


async def resolve(
    model: FakeRewriteModel,
    question: str,
    earlier: History,
    *,
    language: Language = EN,
    policy: RewritePolicy = FOLLOW_UP_ONLY,
) -> ResolvedQuestion:
    return await resolve_question(
        model,
        question,
        language=language,
        history=earlier,
        policy=policy,
        request_id=REQUEST,
        timeout_seconds=5.0,
    )


ALLOWANCE = "How much is the daily allowance for business trips?"
ABROAD = "What is the daily allowance for business trips abroad?"


# --- What is resolved -----------------------------------------------------------------


async def test_a_follow_up_is_resolved_with_the_earlier_questions() -> None:
    model = says(ABROAD)

    resolved = await resolve(model, "And abroad?", history(ALLOWANCE))

    assert resolved.search_text == ABROAD
    assert resolved.question == "And abroad?"
    assert resolved.resolved
    assert resolved.rewrite_class is RewriteClass.FOLLOW_UP
    assert resolved.outcome is RewriteOutcome.REWRITTEN
    assert resolved.earlier_questions == 1
    assert resolved.usage is not None
    assert [(q, h) for q, _, h in model.calls] == [("And abroad?", (ALLOWANCE,))]


async def test_a_ukrainian_follow_up_is_resolved_in_ukrainian() -> None:
    model = says("Які добові у відрядженні за кордоном?")

    resolved = await resolve(
        model, "А за кордоном?", history("Які добові у відрядженні?"), language=UK
    )

    assert resolved.search_text == "Які добові у відрядженні за кордоном?"
    assert resolved.language is UK


@pytest.mark.parametrize(
    ("question", "rewrite_class"),
    [
        ("What is the VPN used for in the office?", RewriteClass.ACRONYM),
        ("Як налаштувати Outlook на ноутбуці вдома?", RewriteClass.CROSS_LANGUAGE),
        ("What does error EXP-2041 mean for my report?", RewriteClass.EXACT_IDENTIFIER),
        ("Who approves expense reports in the company?", RewriteClass.SELF_CONTAINED),
    ],
    ids=["acronym", "cross-language", "identifier", "self-contained"],
)
async def test_only_a_follow_up_is_ever_sent(
    question: str, rewrite_class: RewriteClass
) -> None:
    model = FakeRewriteModel()
    language = UK if question.startswith("Як") else EN

    resolved = await resolve(
        model, question, history(ALLOWANCE), language=language, policy=EVERYTHING
    )

    assert model.calls == []
    assert resolved.rewrite_class is rewrite_class
    assert resolved.search_text == question
    assert not resolved.resolved


async def test_a_short_question_with_no_history_is_not_a_follow_up() -> None:
    model = FakeRewriteModel()

    resolved = await resolve(model, "And abroad?", history())

    assert model.calls == []
    assert resolved.rewrite_class is RewriteClass.SELF_CONTAINED


async def test_a_follow_up_is_not_sent_when_the_policy_leaves_it_off() -> None:
    model = FakeRewriteModel()
    acronyms = RewritePolicy(frozenset({RewriteClass.ACRONYM}))

    resolved = await resolve(model, "And abroad?", history(ALLOWANCE), policy=acronyms)

    assert model.calls == []
    assert resolved.outcome is RewriteOutcome.SWITCHED_OFF


@pytest.mark.parametrize("language", [Language.MIXED, Language.UNKNOWN])
async def test_a_follow_up_in_a_language_that_cannot_be_checked_is_not_sent(
    language: Language,
) -> None:
    model = FakeRewriteModel()

    resolved = await resolve(
        model, "And abroad?", history(ALLOWANCE), language=language
    )

    assert model.calls == []
    assert resolved.outcome is RewriteOutcome.SWITCHED_OFF


# --- What is sent ---------------------------------------------------------------------


async def test_only_questions_are_sent_never_an_answer() -> None:
    model = says(ABROAD)
    earlier = history(ALLOWANCE, answers=(f"It is 30 EUR. {MARKER}",))

    await resolve(model, "And abroad?", earlier)

    assert all(MARKER not in repr(call) for call in model.calls)


async def test_summarized_questions_come_first_and_only_the_newest_are_sent() -> None:
    model = says(ABROAD)
    questions = [f"Question number {n} about leave?" for n in range(14)]
    earlier = History(
        earlier_questions=tuple(questions[:7]),
        turns=history(*questions[7:]).turns,
        tokens=0,
        left_out=0,
        withheld=0,
    )

    await resolve(model, "And abroad?", earlier)

    assert model.calls[0][2] == tuple(questions[-MAX_HISTORY:])


async def test_an_earlier_question_too_long_to_search_is_left_out() -> None:
    long_question = "leave " * (MAX_QUERY_CHARACTERS // 6 + 1)

    assert earlier_questions(history(long_question, ALLOWANCE)) == (ALLOWANCE,)


# --- What a rewrite must keep ---------------------------------------------------------


@pytest.mark.parametrize(
    ("earlier", "question", "rewrite", "carried"),
    [
        (
            "What does error EXP-2041 mean?",
            "How do I fix it?",
            "How do I fix the expense report error?",
            ("EXP-2041",),
        ),
        (
            "How do I set up the VPN on a laptop?",
            "And on phones?",
            "How do I set up a secure connection on phones?",
            ("VPN",),
        ),
    ],
)
async def test_a_rewrite_that_drops_an_entity_of_the_earlier_question_is_not_used(
    earlier: str, question: str, rewrite: str, carried: tuple[str, ...]
) -> None:
    resolved = await resolve(says(rewrite), question, history(earlier))

    assert resolved.search_text == question
    assert resolved.outcome is RewriteOutcome.FALLBACK
    assert resolved.fallback == "InvalidRewrite"
    assert resolved.carried == carried
    assert resolved.usage is not None


async def test_a_rewrite_that_keeps_the_entity_is_used() -> None:
    rewrite = "How do I fix error EXP-2041?"

    resolved = await resolve(
        says(rewrite), "How do I fix it?", history("What does error EXP-2041 mean?")
    )

    assert resolved.search_text == rewrite
    assert resolved.carried == ("EXP-2041",)


async def test_a_ukrainian_follow_up_rewritten_in_english_is_not_used() -> None:
    resolved = await resolve(
        says("Daily allowance abroad"),
        "А за кордоном?",
        history("Які добові у відрядженні?"),
        language=UK,
    )

    assert resolved.search_text == "А за кордоном?"
    assert resolved.fallback == "InvalidRewrite"


async def test_a_failing_rewriter_keeps_the_question() -> None:
    model = FakeRewriteModel(error=RewriterUnavailable())

    resolved = await resolve(model, "And abroad?", history(ALLOWANCE))

    assert resolved.search_text == "And abroad?"
    assert resolved.fallback == "RewriterUnavailable"
    assert resolved.usage is None


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=120))
async def test_any_text_the_model_returns_is_used_only_when_it_keeps_the_entities(
    text: str,
) -> None:
    earlier = history("What does error EXP-2041 mean with the VPN?")

    resolved = await resolve(says(text), "How do I fix it?", earlier)

    if resolved.resolved:
        words = question_words(resolved.search_text)
        assert {"EXP-2041", "VPN"} <= set(words)
    else:
        assert resolved.search_text == "How do I fix it?"


# --- Typed refusals and quiet records -------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"history": (ALLOWANCE,)}, InvalidRewriteRequest),
        ({"policy": frozenset({RewriteClass.FOLLOW_UP})}, InvalidRewriteRequest),
        ({"language": "en"}, InvalidQuestion),
        ({"question": "x" * (MAX_QUERY_CHARACTERS + 1)}, InvalidQuestion),
        ({"question": "   "}, InvalidQuestion),
        ({"timeout_seconds": 0}, InvalidRewriteRequest),
    ],
)
async def test_a_bad_request_is_refused_before_anything_is_sent(
    fields: dict[str, Any], error: type[Exception]
) -> None:
    model = FakeRewriteModel()
    request: dict[str, Any] = {
        "question": "And abroad?",
        "language": EN,
        "history": history(ALLOWANCE),
        "policy": FOLLOW_UP_ONLY,
        "request_id": REQUEST,
        "timeout_seconds": 5.0,
    } | fields

    with pytest.raises(error):
        await resolve_question(model, **request)

    assert model.calls == []


async def test_no_question_or_rewrite_reaches_a_repr_or_a_log_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    earlier = history(f"What does {MARKER} mean?")

    resolved = await resolve(
        says(f"How do I fix {MARKER}?"), "How do I fix it?", earlier
    )

    assert MARKER not in repr(resolved)
    assert all(MARKER not in record.getMessage() for record in caplog.records)
    assert all(MARKER not in repr(record.__dict__) for record in caplog.records)
    assert [r.getMessage() for r in caplog.records].count("follow-up resolved") == 1


def plan_of(model: FakeRewriteModel) -> RewritePlan:
    return model.calls[0][1]


async def test_the_model_is_told_the_follow_up_class_and_the_stated_language() -> None:
    model = says(ABROAD)

    await resolve(model, "And abroad?", history(ALLOWANCE))

    assert plan_of(model).rewrite_class is RewriteClass.FOLLOW_UP
    assert plan_of(model).language is EN


# --- Review regressions ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "language", "earlier", "rewrite"),
    [
        (
            "А за кордоном?",
            UK,
            "How much is the daily allowance for business trips abroad?",
            "How much is the daily allowance for business trips abroad",
        ),
        (
            "And abroad?",
            EN,
            "Які добові у відрядженні за кордоном?",
            "Які добові у відрядженні за кордоном",
        ),
    ],
    ids=["uk-after-english", "en-after-ukrainian"],
)
async def test_a_follow_up_rewritten_in_the_language_of_an_earlier_question_is_not_used(
    question: str, language: Language, earlier: str, rewrite: str
) -> None:
    resolved = await resolve(
        says(rewrite), question, history(earlier), language=language
    )

    assert resolved.search_text == question
    assert resolved.fallback == "InvalidRewrite"


@pytest.mark.parametrize(
    "fields",
    [
        {"earlier_questions": None},
        {"earlier_questions": (None,)},
        {"earlier_questions": "What is VPN"},
        {"turns": ("And abroad?",)},
    ],
    ids=["no-questions", "not-text", "one-string", "not-turns"],
)
async def test_a_history_that_is_not_one_is_refused_before_anything_is_sent(
    fields: dict[str, Any],
) -> None:
    model = FakeRewriteModel()
    broken = History(
        earlier_questions=fields.get("earlier_questions", ()),
        turns=fields.get("turns", ()),
        tokens=0,
        left_out=0,
        withheld=0,
    )

    with pytest.raises(InvalidRewriteRequest):
        await resolve(model, "And abroad?", broken)

    assert model.calls == []


async def test_the_questions_sent_always_end_at_the_newest_one() -> None:
    model = says("How do I fix error EXP-2041?")
    newest = "error EXP-9999 " * (MAX_QUERY_CHARACTERS // 15 + 1)

    resolved = await resolve(
        model, "How do I fix it?", history("What does error EXP-2041 mean?", newest)
    )

    assert model.calls == []
    assert resolved.earlier_questions == 0
    assert resolved.search_text == "How do I fix it?"
```

Створіть `tests/test_follow_up_integration.py`:

```python
"""Follow-up resolution on real PostgreSQL: stored History, retrieval both ways."""

import logging
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from fakes import FakeEmbeddingModel
from knowledge_service.answering.follow_up_evaluation import (
    compare_resolution,
    render_resolution,
)
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import EmbeddingLimits
from knowledge_service.retrieval.evaluation_dataset import (
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    REWRITE_RETRIEVAL_FILE,
    CaseFailed,
    DatasetRunRefused,
    RewriteDataset,
    load_rewrite_dataset,
)
from knowledge_service.retrieval.query_rewriting import (
    RewriteOutcome,
    RewriterUnavailable,
)
from knowledge_service.retrieval.rewrite_cases import (
    REWRITE_CASES_FILE,
    load_rewrite_cases,
)
from knowledge_service.retrieval.rewrite_classes import RewriteClass
from knowledge_service.retrieval.rewrite_evaluation import Rollout, score_rewriting
from knowledge_service.settings import Settings
from rewriting_fakes import FakeRewriteModel

pytestmark = pytest.mark.integration

OBSERVED_AT = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
LIMITS = EmbeddingLimits(max_inputs=64, max_input_tokens=1_000, max_batch_tokens=20_000)


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
def rewrite() -> RewriteDataset:
    return load_rewrite_dataset(
        DATASET_DIRECTORY / REWRITE_RETRIEVAL_FILE,
        load_rewrite_cases(REWRITE_CASES_FILE),
    )


async def test_only_follow_ups_are_resolved_through_their_stored_history(
    runtime: DatabaseRuntime,
    rewrite: RewriteDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    rewriter = FakeRewriteModel()

    comparison = await compare_resolution(
        runtime,
        rewrite,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        rewriter=rewriter,
        price=None,
    )

    follow_ups = [
        label
        for label in rewrite.labelled
        if label.rewrite_class is RewriteClass.FOLLOW_UP
    ]
    assert follow_ups
    # Each call holds a follow-up and its earlier Questions, read back from
    # the database through History; never another class, never a Chunk.
    assert [(q, h) for q, _, h in rewriter.calls] == [
        (label.question, label.history) for label in follow_ups
    ]
    assert all(RESTRICTED_MARKER not in repr(call) for call in rewriter.calls)
    for case in comparison.cases:
        if case.rewrite_class is not RewriteClass.FOLLOW_UP:
            assert case.outcome is not RewriteOutcome.REWRITTEN
            assert case.after == case.before
    resolved = [c for c in comparison.cases if c.outcome is RewriteOutcome.REWRITTEN]
    assert {c.case_id for c in resolved} == {label.case_id for label in follow_ups}
    rendered = render_resolution(comparison)
    assert "Only follow_up was switched on" in rendered
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(label.question in text for label in rewrite.labelled)


async def test_resolution_is_measured_case_by_case_and_guards_the_rollout(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    # The fake rewriter only joins the last earlier Question to the follow-up.
    comparison = await compare_resolution(
        runtime,
        rewrite,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        rewriter=FakeRewriteModel(),
        price=None,
    )

    cases = {case.case_id: case for case in comparison.cases}
    # "And abroad?" alone finds nothing; with its earlier Question it does.
    assert not cases["en-follow-up-abroad"].before.found
    assert cases["en-follow-up-abroad"].after.found
    gains = {g.rewrite_class: g for g in score_rewriting(comparison)}
    follow_up = gains[RewriteClass.FOLLOW_UP]
    assert (follow_up.cases, follow_up.rewritten) == (3, 3)
    # Joining texts moves one Ukrainian case down, so rollout is refused.
    assert follow_up.harmed == ("uk-follow-up-abroad",)
    assert follow_up.rollout is Rollout.HARMS


async def test_a_failing_rewriter_leaves_every_case_as_typed(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    comparison = await compare_resolution(
        runtime,
        rewrite,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        rewriter=FakeRewriteModel(error=RewriterUnavailable()),
        price=None,
    )

    assert all(case.after == case.before for case in comparison.cases)
    assert {
        case.fallback
        for case in comparison.cases
        if case.rewrite_class is RewriteClass.FOLLOW_UP
    } == {"RewriterUnavailable"}
    assert comparison.usages == ()


async def test_a_rewriter_bug_stops_the_run_without_quoting_the_question(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    question = next(
        label.question
        for label in rewrite.labelled
        if label.rewrite_class is RewriteClass.FOLLOW_UP
    )

    with pytest.raises(CaseFailed, match="resolving raised RuntimeError") as caught:
        await compare_resolution(
            runtime,
            rewrite,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            rewriter=FakeRewriteModel(error=RuntimeError(question)),
            price=None,
        )

    assert question not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


# --- Review regressions ---------------------------------------------------------------


async def test_a_dataset_not_from_its_loader_is_refused(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    shuffled = replace(
        rewrite,
        dataset=replace(rewrite.dataset, cases=rewrite.dataset.cases[::-1]),
    )

    with pytest.raises(DatasetRunRefused):
        await compare_resolution(
            runtime,
            shuffled,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            rewriter=FakeRewriteModel(),
            price=None,
        )


async def test_a_case_named_like_a_resolved_one_keeps_its_own_outcome(
    runtime: DatabaseRuntime, rewrite: RewriteDataset
) -> None:
    # A legal case ID that ends the way a resolved copy's ID could.
    taken = "en-follow-up-abroad-resolved"
    victim = "en-en-annual-leave-days"
    renamed = RewriteDataset(
        dataset=replace(
            rewrite.dataset,
            cases=tuple(
                replace(c, case_id=taken) if c.case_id == victim else c
                for c in rewrite.dataset.cases
            ),
        ),
        labelled=tuple(
            replace(c, case_id=taken) if c.case_id == victim else c
            for c in rewrite.labelled
        ),
    )

    comparison = await compare_resolution(
        runtime,
        renamed,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        rewriter=FakeRewriteModel(),
        price=None,
    )

    cases = {case.case_id: case for case in comparison.cases}
    # Searched as typed, the annual leave Question finds nothing in this run.
    assert not cases[taken].before.found
    assert cases[taken].after == cases[taken].before
```

Перевірка:

```bash
uv run pytest tests/test_follow_up.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_follow_up_integration.py -m integration -q
```

Очікувано: 34 passed, потім 6 passed.

### Крок 5 — Без коду: зламайте, виміряйте і вирішіть

Цей крок показує, що захищає звужена політика, пояснює вимірювання і фіксує, чого ви навчилися.

**Дія 1:** навмисно зламайте розв'язувач. У `src/knowledge_service/answering/follow_up.py`, в `resolve_question`, замініть:

```python
        policy.classes & FOLLOW_UP_ONLY.classes
```

на:

```python
        policy.classes
```

Запустіть:

```bash
uv run pytest tests/test_follow_up.py -q
```

Очікувано: 2 failed, 32 passed: `test_only_a_follow_up_is_ever_sent` для запитання з абревіатурою і для запитання з іншомовним словом, які тепер потрапляють до моделі, бо прапорець вмикає їхні класи. Випадки з ідентифікатором і самодостатнього запитання досі проходять, бо `rewrite_question` ніколи не надсилає ці класи. Поверніть рядок і запускайте знову, доки все не пройде.

**Дія 2:** прочитайте вимірювання. Еталонний прогін із фальшивим переписувачем, як його друкує `render_resolution`, мав такі рядки:

| Class | Cases | Rewritten | MRR as typed | MRR rewritten | Gain | Harmed | Rollout |
|---|---:|---:|---:|---:|---:|---|---|
| follow_up | 3 | 3 | 0.444 | 0.367 | -0.078 | uk-follow-up-abroad | harms |

| Case | Ranks as typed | Ranks rewritten |
|---|---|---|
| en-follow-up-abroad | missed | 10 |
| uk-follow-up-abroad | 1 | 2 |
| uk-follow-up-his | 3 | 2 |

Решту випадків не надсилали, і їхні ранги не змінилися. «And abroad?» саме по собі нічого не знаходить; склеєне з попереднім запитанням — знаходить потрібний Chunk. Але склеювання посунуло один український випадок з рангу 1 на 2, тож `score_rewriting` каже `harms`, і за цим прогоном клас вмикати не можна. Фальшивий переписувач нічого не розв'язує: він лише склеює два тексти, а щільні ранги дає фальшивий ембединг. Тож цей прогін доводить шлях і перевірку розгортання, а не виграш. Виграш має показати живий прогін: M05-T15 виміряв +0.389 MRR для тих самих трьох випадків зі справжньою моделлю, а розв'язувач надсилає їй рівно те саме, що надсилав M05-T15.

**Дія 3:** під `## Decision` напишіть:

- чому розв'язуються лише уточнення, хоч би що вмикав прапорець;
- чому переписувач бачить попередні запитання і ніколи — відповіді;
- чому переписане уточнення перевіряється ще раз на мову і перенесені сутності;
- що показує фальшиве вимірювання, чого воно показати не може і що має показати живий прогін, перш ніж вмикати `follow_up`;
- що лишається на потім: виклик `resolve_question` у потоці відповіді і збереження пошукового тексту разом із ходом;
- щонайменше одне **спростоване припущення** у формі «Я припускав X. Прогін чи рев'ю показує Y. Отже, Z хибне».

### Крок 6 — Без коду: архітектура, перевірки, evidence і прогрес

Цей крок оновлює текст, який зміна зробила неповним, доводить, що все досі працює, і фіксує результат.

**Дія 1:** у `docs/architecture.md` додайте розв'язувач до рядка `answering` і до абзацу про переписування запитів:

**1.** Знайдіть:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

Замініть на:

```markdown
| Package | Module | Main entry points |
|---|---|---|
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases), `AnswerEventWriter` / `AnswerEventReader` (`events`, the SSE event contract of a streamed Answer: `metadata`, then `text_delta`, `citation`, and at most one `usage`, then one terminal `completion` or `error`, with IDs counting up from 0; the writer refuses an event out of order without changing the stream, and the reader refuses invalid order, lost or repeated events, a cut-off stream, any spelling but the writer's, or a stream over its limits, and rebuilds the Answer however the bytes are split; an answer needs visible text, a Citation, and its usage, Citations never precede an abstention, and abstentions and errors carry only their fixed messages), and `AnswerStreamer` (`streaming`, behind `POST /v1/answer/stream`: finds and judges Evidence before any byte, so its failures stay HTTP errors; then pulls the model's stream one event at a time, passes the answer's text on as it arrives, checks the whole reply and lets an `AnswerFinisher` decide, and sends Citations only after an `answer` decision and the usage before the terminal event; one deadline covers it all, and a client that leaves cancels the work and closes the model's stream through `EventStreamResponse`), and `load_history` (`history`, the earlier turns of the reader's own Conversation within `history_tokens`: recent turns whole, older ones as their Questions; an Answer's text only when it was answered and every Chunk it and every earlier Answer cited is current and visible to the reader now, so a revoked, deleted, or rewritten Document never returns through an old Answer; Questions and their replies are stored together by `add_turn`, with the reply's outcome and cited Chunks), and `resolve_question` (`follow_up`, a follow-up Question made standalone for search: only the `follow_up` class is ever sent to the `RewriteModel`, with the earlier Questions of the History and never an Answer; the rewrite must stay in the stated language and keep the Question's words to keep and the identifiers and acronyms of the newest earlier Question, or the Question is searched as typed; a `ResolvedQuestion` records both texts and how the search text was chosen) with `compare_resolution` (`follow_up_evaluation`, the bilingual rewrite cases replayed as stored Conversations and searched with and without resolution) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
```

**2.** Знайдіть:

```markdown
Query rewriting calls OpenAI too. `OpenAIRewriteModel` sends only the
Question, the earlier Questions of its Conversation, the stated language,
the rewrite class, and the words to keep, with `store=False`; never a
Chunk. Nothing rewrites by default: `QUERY_REWRITE_CLASSES` is empty, and
`--compare-rewriting` exits 1 when it switches on a class the measurement
did not allow. The decision and its numbers are recorded in
`docs/evidence/M05-T15-query-rewriting.md`.

`--compare-rerankers` runs both rerankers on the same Candidates of each
case. The default chosen for each environment, and the numbers behind it,
are recorded in `docs/evidence/M05-T13-reranking-strategies.md`.

The Evaluation module has no package yet. Code that measures retrieval
```

Замініть на:

```markdown
Query rewriting calls OpenAI too. `OpenAIRewriteModel` sends only the
Question, the earlier Questions of its Conversation, the stated language,
the rewrite class, and the words to keep, with `store=False`; never a
Chunk. Nothing rewrites by default: `QUERY_REWRITE_CLASSES` is empty, and
`--compare-rewriting` exits 1 when it switches on a class the measurement
did not allow. The decision and its numbers are recorded in
`docs/evidence/M05-T15-query-rewriting.md`. In answering,
`resolve_question` sends only follow-ups, whatever else the flag switches
on, and reads their earlier Questions from the reader's own History
(`docs/evidence/M06-T12-resolve-follow-up-questions.md`).

`--compare-rerankers` runs both rerankers on the same Candidates of each
case. The default chosen for each environment, and the numbers behind it,
are recorded in `docs/evidence/M05-T13-reranking-strategies.md`.

The Evaluation module has no package yet. Code that measures retrieval
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_follow_up.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний прогін дав 2255 passed для `make test-unit`, на 34 більше, ніж після M06-T11, і 376 passed для `make test-integration`, на 6 більше. Якщо пише, що тестової бази немає, запустіть її командою `docker start knowledge-service-postgres`.

**Дія 3:** заповніть evidence-файл:

- **Design:** що надсилається і коли, дві додаткові перевірки, `ResolvedQuestion` і як вимірює `compare_resolution`.
- **Verification:** команди й лічильники, навмисна поломка, вимірювання і відомі обмеження з «Перед початком».
- **Review:** незалежне агресивне рев'ю обстріляло розв'язувач із підкладеним маркером: надсилалися лише уточнення, ніколи відповідь, і жоден текст не витік. Воно знайшло п'ять проблем, усі виправлено з тестами, що падали до виправлення: використовувалося уточнення, переписане мовою попереднього запитання; зіпсована історія падала без типу; найновіше запитання, яке не можна шукати, дозволяло старішій темі зайняти його місце; вимірювався невідповідний набір даних; ID випадку міг збігтися з розв'язаною копією. Одне обмеження задокументовано: сутність, яку пошуковий синтаксис прочитав би інакше, зберегти неможливо.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу виконаною, додайте рядок M06-T12 у `docs/curriculum/PROGRESS.md`, встановіть наступною задачею M06-T13 і закомітьте з `m06-t12: resolve follow up questions`.

## Запитання для рефлексії

1. Що `resolve_question` приховує від коду, який відповідатиме на уточнення? Перелічіть, чого цьому коду ніколи не треба вирішувати про переписування.
2. Хтось пропонує надсилати переписувачу ще й попередні відповіді, бо вони краще називають тему. Що тоді могло б залишити сховище, чого не залишає зараз, і який тест ви б додали першим?
3. Переписувач, що просто склеює тексти, погіршив ранг одного українського випадку. Чому додавання слів попереднього запитання може відсунути потрібний Chunk нижче, і на що б ви подивилися, щоб це з'ясувати?
4. Користувач питає «How do I fix it?» одразу після дуже довгого вставленого логу помилки, і цей лог був попереднім запитанням. Що надішле розв'язувач, і чи це правильний вибір?
5. Яку розмову ви б додали до набору даних наступною, щоб спробувати змусити розв'язання зашкодити випадку, і яким, на вашу думку, буде рішення про розгортання?

## Чек-лист завершення

- [ ] Evidence-файл містить інваріант і ваші передбачення, записані до коду.
- [ ] Надсилається лише запитання класу `follow_up` англійською чи українською, хоч би що вмикала політика.
- [ ] Надсилаються лише попередні запитання читача, щонайбільше десять, аж до найновішого; ніколи відповідь чи Chunk.
- [ ] Переписаний текст зі словами іншої абетки або без ідентифікаторів і абревіатур попереднього запитання не використовується.
- [ ] Будь-яка невдача повертає запитання як є, а оплачена відповідь зберігає своє використання.
- [ ] `ResolvedQuestion` фіксує обидва тексти і те, як обрано пошуковий текст; жоден текст не потрапляє в repr, запис логу, помилку чи звіт.
- [ ] `compare_resolution` вимірює двомовні випадки з розв'язанням і без, а перевірка розгортання читає це вимірювання.
- [ ] Ви виконали навмисну поломку, побачили, що тести падають, і скасували її.
- [ ] Усі перевірки проходять: формат, lint, типи, unit, integration, docs.
- [ ] Evidence, статус задачі, рядок у PROGRESS і коміт готові.

Коли напишете файли, поділіться ними або виводом помилок, і я перевірю їх за цим чек-листом.
