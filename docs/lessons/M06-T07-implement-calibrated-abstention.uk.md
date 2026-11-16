# M06-T07 — Реалізуйте каліброване утримання

Англійська версія: [M06-T07-implement-calibrated-abstention.md](M06-T07-implement-calibrated-abstention.md)

Вихідне завдання: [M06-T07](../curriculum/milestones/M06-answers-and-conversations/M06-T07-implement-calibrated-abstention.md)

## Що ви зробите

Цей урок вирішує, коли система відповідає, а коли каже «не можу відповісти надійно». M06-T04–M06-T06 дають багато сигналів: що знайшов пошук, що написала модель, чи цитати досі дозволені і чи підтверджені твердження. Тепер одна політика перетворює їх на одне рішення з причиною і фіксованим повідомленням для користувача. Друга частина міряє це рішення на запитаннях, для яких правильний результат відомий.

- `assess_evidence` оцінює результат пошуку ще до виклику моделі: нічого не знайдено, найкращий збіг слабкий або досить.
- `find_conflicts` помічає, коли два процитовані Документи розходяться в числі, як стара і нова політика.
- `decide` повертає `ANSWER` або `ABSTAIN` з однією причиною і називає версію політики.
- `abstention_message` дає кожній причині одне фіксоване повідомлення англійською та українською.
- `classify`, `score_abstention` і `calibrate_threshold` порівнюють рішення з розміченими запитаннями і обирають поріг оцінки за даними.

| Завдання вимагає | Де це відбувається |
|---|---|
| Сигнали достатності Evidence | Крок 3: `assess_evidence` (порожньо, слабко) і `find_conflicts` (суперечливо) |
| Політика відповіді для порожнього, слабкого, суперечливого чи недозволеного результату | Крок 3: `decide`, по одній причині на кожен; недозволене повідомляється так само, як порожнє |
| Каліброване | Крок 3: поріг слабкості належить одному reranker-у і обирається `calibrate_threshold` за розміченими випадками |
| Випадки оцінювання розрізняють правильне утримання, зайве утримання і непідтверджені відповіді | Крок 4: розмічені випадки, оцінені `classify` і `score_abstention` |
| Детерміновані тести: успіх, найризиковіший край, типізована помилка | Крок 4: підтверджена відповідь; звіт підтвердження іншого тексту; `UncalibratedScore` і `InvalidAbstentionInput` |
| Жодного підкинутого тексту в телеметрії | Крок 4: рішення містять лише причини й версії, а жодне повідомлення не містить підкинутого маркера |

Цей урок містить код: кроки 2–4 пишуть код. Кроки 1, 5 і 6 — без коду. Модель, мережа і база не потрібні.

Терміни цього уроку:

- **Утримання (abstention):** відповідь, яка прямо каже, що наявні Evidence не дають надійної відповіді, наприклад «Я не знайшов документів, які відповідають на це запитання».
- **Достатність (sufficiency):** чи знайшов пошук досить, щоб пробувати: `EMPTY` (нічого), `WEAK` (лише погані збіги) або `SUFFICIENT`.
- **Оцінка reranker-а (reranker score):** число, яке reranker — модель, що перечитує кожен знайдений Chunk разом із запитанням, — дає тому, наскільки він підходить. У неї немає фіксованої шкали: 0.2 може бути добре для одного reranker-а і погано для іншого.
- **Калібрований поріг (calibrated threshold):** межа для цієї оцінки, обрана за запитаннями з відомим правильним результатом, а не вгадана. Як прохідний бал, встановлений за результатами минулорічного іспиту.
- **Зайве утримання (over-abstention):** утримання на запитанні, на яке Документи відповідають. **Непідтверджена відповідь (unsupported answer):** відповідь на запитання, на яке вони не відповідають. Обидва — помилки; політика обмінює одну на іншу.

## Перед початком

Цей розділ пояснює рішення за кодом, щоб кожен крок мав сенс, коли ви до нього дійдете.

- **Що вже є.** `answering/generation.py` дає `AnswerResult` з результатом: відповідь, утримання, відмова, невалідна відповідь, немає Evidence або відкликано. `answering/support.py` дає `SupportReport` з вердиктом на кожне твердження і SHA-256 тексту, який перевіряли. `retrieval/reranking.py` дає оцінки reranker-а і `RerankerIdentity`, якому вони належать.
- **Навіщо спершу перевіряти пошук.** Якщо нічого не знайдено або лише погані збіги, модель може тільки вгадувати чи утримуватися, і обидва варіанти коштують платного виклику. Тож `assess_evidence` працює до генерації, а результат `EMPTY` чи `WEAK` пропускає модель.
- **Чому порожнє і недозволене виглядають однаково.** Пошук повертає лише Chunks, які читач може бачити. Запитання про Документ, якого читач бачити не може, нічого не знаходить, так само як запитання ні про що. Якби повідомлення відрізнялися, користувач дізнався б, що прихований Документ існує. Тож обидва випадки, а також відкликана відповідь з M06-T05, дають ту саму причину і ті самі слова.
- **Чому поріг належить одному reranker-у.** Оцінки reranker-а не мають фіксованої шкали, як каже `reranking.py`. Поріг, обраний для однієї моделі, нічого не означає для іншої. Тож політика називає свій reranker, а оцінка від будь-якого іншого, або відсутня оцінка там, де політика її потребує, піднімає `UncalibratedScore` замість вгадування.
- **Чому суперечність має власну причину.** Два Документи можуть обидва бути дозволеними й доречними і все одно розходитися: «24 дні» в новій політиці, «20 днів» у старій. Відповідь з будь-якого з них може бути хибною, а перевірка підтвердження її пропустить, бо цитата справжня. Тож політика утримується і каже, що Документи суперечать одне одному. Перевірка порівнює слова й числа, а не зміст, тож це дешевий перший сигнал, а не доказ.
- **Чому звіт підтвердження має стосуватися саме цього тексту.** Звіт прив'язаний до тексту через SHA-256, а твердження — через їхні позиції. Звіт іншої відповіді чи звіт без частини тверджень не повинен пропускати цю відповідь. Твердження, які суддя не перевірив, рахуються непідтвердженими.
- **Навіщо калібрувати за розміченими випадками.** «Відповідати менше неправди» і «рідше утримуватися» тягнуть у різні боки. `calibrate_threshold` бере запитання, розмічені як такі, що мають відповідь чи ні, з їхньою найкращою оцінкою, і обирає поріг, що відповідає на найменше запитань без відповіді, утримуючись помилково не частіше за обрану частку запитань з відповіддю. M10 дасть йому справжній набір для оцінювання (Evaluation Dataset).
- **Що знайшло рев'ю.** Адверсарне рев'ю знайшло десять проблем, кожну виправлено тестом, що падає без виправлення:
  - Звіт підтвердження іншого тексту пропускав будь-яку відповідь. Тепер його SHA-256 і твердження мусять збігатися з цим текстом.
  - Твердження після перших 20 ніколи не рахувалися. Тепер неперевірене твердження рахується непідтвердженим.
  - Пошук суперечностей порівнював кожну пару речень і на повтореному тексті займав 26 секунд. Тепер кожне різне речення порівнюється раз, а твердження з одним Документом чи одним числом пропускається.
  - Бюджет 0.29 на 100 випадків дозволяв лише 28 — помилка округлення. Тепер бюджет читається як десятковий дріб, яким його записали.
  - Навіть з повним бюджетом поріг не міг утриматися на всьому. Тепер може.
  - Підроблена ідентичність reranker-а могла позичити поріг. Тепер спершу перевіряється її тип.
  - Випадок, що не є парою, падав з нетипізованою помилкою. Тепер він піднімає `InvalidAbstentionInput`.
  - Рік, який згадує лише один Документ, рахувався суперечністю. Тепер рахуються лише числа самого твердження.
  - Одне зайве слово у твердженні ховало справжню суперечність. Тепер реченню досить 60% слів твердження, а не всіх.
  - Політика могла вимагати нуль підтверджених тверджень. Тепер частка мусить бути більшою за нуль.
- **Де живе код.** `answering/abstention.py`, з однією невеликою зміною в `answering/support.py`.
- **Відомі обмеження.** Перевірка суперечностей лексична: відмінене українське слово чи число, записане двома способами («1,000» і «1000»), може сховати або вигадати суперечність. Політику поки ніхто не викликає; M06-T09 викликатиме її під час стрімінгу. Калібрування тут працює на вручну складених випадках; справжньому потрібен розмічений набір з M10.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої передбачення

Поки коду ще немає, запишіть, що має бути правдою наприкінці уроку. Крок 5 перевіряє передбачення.

Створіть `docs/evidence/M06-T07-calibrated-abstention.md`:

```markdown
# M06-T07 — Calibrated abstention evidence

## Invariant

`assess_evidence` returns `EMPTY` for no eligible Evidence and `WEAK` for
a best score below the policy's threshold, and raises `UncalibratedScore`
for a score of any other reranker or a missing one. `decide` returns one
decision with one reason, in this order: no Evidence, weak Evidence, a
refused, invalid, or withdrawn Answer, the model's abstention, conflicting
Evidence, then claim support. It answers only when the support report is
about this exact text, by SHA-256 and claims, and enough of all claims are
supported, unchecked ones counted as unsupported. Empty, unauthorized,
and withdrawn give the same reason and message. `classify` maps a
decision and an answerability label to a correct answer, a correct
abstention, an over-abstention, or an unsupported answer.
`calibrate_threshold` returns the threshold that answers the fewest
unanswerable cases within the over-abstention budget. Decisions and
messages hold no Document, Question, or answer text.

## Expected failing check

Before the change, `uv run python -c "import knowledge_service.answering.abstention"`
fails with `ModuleNotFoundError`.

## Predictions

- What a user is told when they ask about a Document they may not see, compared with a question about nothing at all: ...
- Which threshold `calibrate_threshold` picks for the scores 0.95, 0.80, 0.60, 0.30 (answerable) and 0.35, 0.20, 0.10 (unanswerable), with a 25% over-abstention budget, and why: ...
- How many tests fail if `decide` skips the support check, and which kind of evaluation error grows: ...

## Design

## Verification

## Decision

## Reflection
```

Перевірка: файл існує, перевірка, що має впасти, падає, а кожне передбачення — повне речення, яке крок 5 може спростувати.

### Крок 2 — Крок із кодом: зробіть лексичні помічники спільними

Цей крок робить публічними три невеликі помічники модуля підтвердження, щоб перевірка суперечностей читала числа й слова точно так само, як лексичний суддя.

**Простими словами:** Лексичний суддя вже вміє витягати з речення цілі числа, перелічувати його довші слова і визначати, чи речення говорить тими самими словами. Перевірці суперечностей потрібні саме ці правила, тож замість копіювання вони стають спільними. Сам суддя працює як раніше.

| Помічник | Що робить |
|---|---|
| `numbers_in` | Кожне ціле число в тексті: «2.5», «1,000» і «-5» — по одному кожне |
| `topic_words` | Слова з чотирьох і більше літер, без огляду на регістр і без чисел |
| `shares_topic` | Чи містить речення щонайменше 60% набору слів |

**1.** У `src/knowledge_service/answering/support.py` знайдіть:

```python
    if not _numbers(text) <= _numbers(quote):
```

Замініть на:

```python
    if not numbers_in(text) <= numbers_in(quote):
```

**2.** Потім знайдіть:

```python
def _numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(text))
```

Замініть на:

```python
def numbers_in(text: str) -> set[str]:
    """Every whole number in `text`: "2.5", "1,000", and "-5" are one each."""
    return set(_NUMBER.findall(text))


def topic_words(text: str) -> set[str]:
    """The words of four or more letters in `text`, ignoring case and numbers."""
    return {
        w
        for w in _WORD.findall(text.casefold())
        if len(w) >= _LONG_WORD and not any(c.isdigit() for c in w)
    }


def shares_topic(words: set[str], sentence: str) -> bool:
    """True when `sentence` holds at least 60% of `words`."""
    present = set(_WORD.findall(sentence.casefold()))
    return len(words & present) >= _OVERLAP * len(words)
```

**3.** Потім у `_lexical_finding` знайдіть:

```python
    numbers = _numbers(claim)
    long_words = {
        w
        for w in _WORD.findall(claim.casefold())
        if len(w) >= _LONG_WORD and not any(c.isdigit() for c in w)
    }
    if not numbers and not long_words:
        return JudgeFinding(claim=index, evidence_id=None, quote=None)
    for evidence_id, sentence in sentences:
        if not numbers <= _numbers(sentence):
            continue
        present = set(_WORD.findall(sentence.casefold()))
        if long_words and len(long_words & present) < _OVERLAP * len(long_words):
            continue
```

Замініть на:

```python
    numbers = numbers_in(claim)
    long_words = topic_words(claim)
    if not numbers and not long_words:
        return JudgeFinding(claim=index, evidence_id=None, quote=None)
    for evidence_id, sentence in sentences:
        if not numbers <= numbers_in(sentence):
            continue
        if long_words and not shares_topic(long_words, sentence):
            continue
```

Перевірка:

```bash
uv run pytest tests/test_support.py -q
```

Очікувано: 74 passed, 1 deselected.

### Крок 3 — Крок із кодом: політика утримання та її оцінювання

Цей крок пише модуль, що вибирає між відповіддю і утриманням.

**Простими словами:** Ще до звернення до моделі він дивиться, що знайшов пошук: нічого, лише слабкі збіги за каліброваним порогом reranker-а, чи досить. Після відповіді моделі він проходить фіксований список причин утриматися, від «нічого не знайдено» до «твердження не підтверджені», і зупиняється на першій, що підходить. Користувач отримує одне фіксоване повідомлення на причину, англійською чи українською, і запитання про прихований Документ звучить точно як запитання ні про що. Друга частина оцінює рішення на запитаннях з відомим правильним результатом і обирає за ними поріг оцінки.

| Частина | Що містить чи робить |
|---|---|
| `AbstentionPolicy` | Калібровані налаштування: reranker і його поріг, частка тверджень, які мусять бути підтверджені, версія |
| `EvidenceSignals`, `assess_evidence`, `Sufficiency` | Кількість і найкраща оцінка пошуку, оцінені як порожньо, слабко чи досить |
| `find_conflicts` | Твердження, процитовані Документи яких кажуть майже те саме речення з різними числами |
| `decide`, `AbstentionDecision`, `AbstentionReason` | Одне рішення з однією причиною у фіксованому порядку |
| `abstention_message` | Одне фіксоване повідомлення на причину двома мовами |
| `classify`, `score_abstention`, `CaseOutcome` | Чотири види результату проти позначки «має відповідь» |
| `calibrate_threshold` | Поріг, що відповідає на найменше запитань без відповіді в межах бюджету зайвих утримань |

Створіть `src/knowledge_service/answering/abstention.py`:

```python
"""When to answer and when to abstain, and how to measure that choice.

An Answer is only as good as its Evidence. This module turns what the
pipeline already knows into one decision: answer, or abstain with a
reason the user is told in plain words.

What the module promises:

- **Evidence first, before any model call.** `assess_evidence` looks at
  retrieval's result: no eligible Evidence is `EMPTY`; a best reranker
  score below the policy's calibrated threshold is `WEAK`. Either means
  the model need not be called at all.
- **Empty and unauthorized look the same.** Retrieval only ever returns
  Chunks the reader may see, so a question about a Document the reader may
  not see finds nothing, exactly like a question about nothing. A
  withdrawn Answer (see `answering.current_citations`) is told the same
  way. The user is never told that something exists but is hidden.
- **Scores are compared only where they mean something.** A reranker
  score has no fixed scale; the threshold belongs to one `RerankerIdentity`.
  A score from another reranker, or no score where the policy needs one,
  raises `UncalibratedScore` instead of guessing.
- **Conflicts are found in the cited Evidence.** `find_conflicts` reports
  a claim when two cited sentences from different Documents say nearly the
  same thing as each other and as the claim, but disagree on the claim's
  numbers, such as an old and a new policy. It compares words, not
  meaning, so it is a cheap first signal, not a proof: an inflected word
  or a number written two ways ("1,000" and "1000") can fool it.
- **One decision, with one reason.** `decide` checks, in order: no
  Evidence, weak Evidence, a refused, invalid, or withdrawn Answer, the
  model's own abstention, conflicting Evidence, then claim support: a
  missing or failed support report, a report of another text (its SHA-256
  or its claims differ), or too small a share of supported claims among
  all claims, unchecked ones counted as unsupported, is `UNSUPPORTED`.
  Only an Answer that passes all of them is `ANSWER`. The decision names
  the policy version that made it.
- **Measured, not assumed.** `classify` compares a decision with an
  Evaluation Case's answerability label: a correct answer, a correct
  abstention, an over-abstention (abstained on an answerable question),
  or an unsupported answer (answered an unanswerable one).
  `score_abstention` counts them, and `calibrate_threshold` picks the
  reranker threshold that answers fewest unanswerable questions while
  over-abstaining on at most a given share of answerable ones; the share
  is read as the decimal it was written as, so 0.29 of 100 cases is 29.
- **Quiet.** Decisions hold reasons, counts, and versions, never text.
"""

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import cast

from knowledge_service.answering.evidence import EvidenceSet
from knowledge_service.answering.generation import AnswerOutcome, AnswerResult
from knowledge_service.answering.support import (
    MAX_CLAIMS,
    SupportOutcome,
    SupportReport,
    SupportVerdict,
    numbers_in,
    split_claims,
    topic_words,
)
from knowledge_service.language import Language
from knowledge_service.retrieval.reranking import RerankerIdentity

POLICY_VERSION = "1"
# A claim needs this many longer words before two sentences can be said to
# talk about the same thing, and a sentence must hold this share of them.
_CONFLICT_WORDS = 2
_CLAIM_SHARE = 0.6
# Two sentences say nearly the same thing when this share of all their
# longer words is common to both.
_SAME_TOPIC = 0.8


class InvalidAbstentionInput(ValueError):
    """A signal, policy, or case is not what the policy can use."""


class UncalibratedScore(InvalidAbstentionInput):
    """A score cannot be compared with the policy's threshold."""


class Sufficiency(StrEnum):
    """What retrieval's result says before any model is asked."""

    SUFFICIENT = "sufficient"
    EMPTY = "empty"
    WEAK = "weak"


class Decision(StrEnum):
    """What the user gets."""

    ANSWER = "answer"
    ABSTAIN = "abstain"


class AbstentionReason(StrEnum):
    """Why the user gets an abstention; each has one fixed message."""

    NO_EVIDENCE = "no_evidence"
    WEAK_EVIDENCE = "weak_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNSUPPORTED = "unsupported"
    ANSWER_FAILED = "answer_failed"


class CaseOutcome(StrEnum):
    """How one decision compares with an Evaluation Case's label."""

    CORRECT_ANSWER = "correct_answer"
    CORRECT_ABSTENTION = "correct_abstention"
    OVER_ABSTENTION = "over_abstention"
    UNSUPPORTED_ANSWER = "unsupported_answer"


@dataclass(frozen=True, slots=True)
class AbstentionPolicy:
    """The calibrated knobs of the decision.

    `min_top_score` is a threshold for scores of `reranker` only, or None
    to skip the weak check. `min_supported_share` is the share of judged
    claims that must be supported, from 0 to 1.
    """

    reranker: RerankerIdentity | None
    min_top_score: float | None
    min_supported_share: float
    version: str = POLICY_VERSION

    def __post_init__(self) -> None:
        if (self.reranker is None) != (self.min_top_score is None):
            raise InvalidAbstentionInput(
                "a score threshold needs its reranker, and a reranker its threshold"
            )
        if self.reranker is not None and type(self.reranker) is not RerankerIdentity:
            raise InvalidAbstentionInput("reranker must be a RerankerIdentity")
        if self.min_top_score is not None and not _finite(self.min_top_score):
            raise InvalidAbstentionInput("min_top_score must be a finite number")
        # Zero would answer when no claim at all is supported.
        if not _finite(self.min_supported_share) or not (
            0 < self.min_supported_share <= 1
        ):
            raise InvalidAbstentionInput(
                "min_supported_share must be above 0 and at most 1"
            )
        if type(self.version) is not str or not self.version:
            raise InvalidAbstentionInput("version must be non-empty text")


@dataclass(frozen=True, slots=True)
class EvidenceSignals:
    """What retrieval knows: how many eligible Chunks, and the best score.

    `top_score` and `reranker` are None when nothing was reranked.
    """

    eligible: int
    top_score: float | None
    reranker: RerankerIdentity | None


@dataclass(frozen=True, slots=True)
class AbstentionDecision:
    """The decision, why, and which policy version made it."""

    decision: Decision
    reason: AbstentionReason | None
    policy_version: str


@dataclass(frozen=True, slots=True)
class AbstentionScore:
    """How a set of decisions compares with their labels."""

    correct_answers: int
    correct_abstentions: int
    over_abstentions: int
    unsupported_answers: int

    @property
    def total(self) -> int:
        return (
            self.correct_answers
            + self.correct_abstentions
            + self.over_abstentions
            + self.unsupported_answers
        )


_MESSAGES: dict[AbstentionReason, tuple[str, str]] = {
    AbstentionReason.NO_EVIDENCE: (
        "I could not find documents that answer this question.",
        "Я не знайшов документів, які відповідають на це запитання.",
    ),
    AbstentionReason.WEAK_EVIDENCE: (
        "The documents I found do not answer this question reliably.",
        "Знайдені документи не дають надійної відповіді на це запитання.",
    ),
    AbstentionReason.CONFLICTING_EVIDENCE: (
        "The documents disagree on this, so I cannot give one reliable answer.",
        "Документи суперечать одне одному, тому я не можу дати надійну відповідь.",
    ),
    AbstentionReason.INSUFFICIENT_EVIDENCE: (
        "The documents do not contain enough to answer this.",
        # A Ukrainian sentence starts with the Cyrillic letter U.
        "У документах недостатньо інформації, щоб відповісти.",  # noqa: RUF001
    ),
    AbstentionReason.UNSUPPORTED: (
        "I could not verify an answer against the documents.",
        "Я не зміг перевірити відповідь за документами.",
    ),
    AbstentionReason.ANSWER_FAILED: (
        "I could not produce an answer right now.",
        "Зараз я не можу сформувати відповідь.",
    ),
}


def abstention_message(reason: AbstentionReason, language: Language) -> str:
    """The fixed message for `reason`, in Ukrainian or else in English."""
    if type(reason) is not AbstentionReason:
        raise InvalidAbstentionInput("reason must be an AbstentionReason")
    english, ukrainian = _MESSAGES[reason]
    return ukrainian if language is Language.UKRAINIAN else english


def assess_evidence(signals: EvidenceSignals, policy: AbstentionPolicy) -> Sufficiency:
    """Judge retrieval's result before any model is asked."""
    _check_policy(policy)
    if type(signals) is not EvidenceSignals:
        raise InvalidAbstentionInput("signals must be EvidenceSignals")
    if type(signals.eligible) is not int or signals.eligible < 0:
        raise InvalidAbstentionInput("eligible must be a whole number of zero or more")
    if signals.eligible == 0:
        return Sufficiency.EMPTY
    if policy.min_top_score is None:
        return Sufficiency.SUFFICIENT
    # The type first: a forged identity could claim to equal anything.
    if (
        type(signals.reranker) is not RerankerIdentity
        or signals.reranker != policy.reranker
        or signals.top_score is None
    ):
        raise UncalibratedScore("the score is not from the policy's reranker")
    if not _finite(signals.top_score):
        raise UncalibratedScore("the score must be a finite number")
    if signals.top_score < policy.min_top_score:
        return Sufficiency.WEAK
    return Sufficiency.SUFFICIENT


def find_conflicts(answer_text: str, cited: EvidenceSet) -> tuple[int, ...]:
    """The indexes of claims whose cited Documents disagree on a number.

    For each of the first `MAX_CLAIMS` claims with a number and at least
    two longer words, the cited sentences that hold at least 60% of those
    words are compared in pairs. Two sentences conflict when they come from
    different Documents, say nearly the same thing (at least 80% of their
    longer words in common), and hold different numbers of the claim, such
    as "24 days of annual leave" and "20 days of annual leave". A number
    only one of them adds, such as a year, is no disagreement, and
    sentences of one Document never conflict.
    """
    if type(answer_text) is not str or type(cited) is not EvidenceSet:
        raise InvalidAbstentionInput("an answer text and its cited EvidenceSet")
    # Each distinct sentence once: repeated text adds no pairs to compare.
    sentences = {
        (
            item.document_id,
            frozenset(topic_words(text)),
            frozenset(numbers_in(text)),
        )
        for item in cited.items
        for c in split_claims(item.text)
        if (text := item.text[c.start : c.end])
    }
    conflicts: list[int] = []
    for claim in split_claims(answer_text)[:MAX_CLAIMS]:
        text = answer_text[claim.start : claim.end]
        words = topic_words(text)
        numbers = numbers_in(text)
        if not numbers or len(words) < _CONFLICT_WORDS:
            continue
        # Each matching sentence by its Document, its words, and which of
        # the claim's numbers it holds.
        about = {
            (document, own, held & numbers)
            for document, own, held in sentences
            if held and len(words & own) >= _CLAIM_SHARE * len(words)
        }
        if len({d for d, _, _ in about}) < 2 or len({h for _, _, h in about}) < 2:
            continue
        if any(
            first[0] != second[0]
            and first[2] != second[2]
            and _similar(first[1], second[1])
            for first in about
            for second in about
        ):
            conflicts.append(claim.index)
    return tuple(conflicts)


def decide(
    sufficiency: Sufficiency,
    result: AnswerResult | None,
    cited: EvidenceSet | None,
    support: SupportReport | None,
    policy: AbstentionPolicy,
) -> AbstentionDecision:
    """Answer, or abstain with one reason.

    `result` is None when retrieval's result was already not sufficient
    and no model was called; `cited` is the Evidence the Answer cites, and
    `support` its support report, or None if it was not checked.
    """
    _check_policy(policy)
    if type(sufficiency) is not Sufficiency:
        raise InvalidAbstentionInput("sufficiency must be a Sufficiency")
    if sufficiency is Sufficiency.EMPTY:
        return _abstain(AbstentionReason.NO_EVIDENCE, policy)
    if sufficiency is Sufficiency.WEAK:
        return _abstain(AbstentionReason.WEAK_EVIDENCE, policy)
    if type(result) is not AnswerResult or type(result.outcome) is not AnswerOutcome:
        raise InvalidAbstentionInput("sufficient Evidence needs an AnswerResult")
    match result.outcome:
        case AnswerOutcome.NO_EVIDENCE | AnswerOutcome.WITHDRAWN:
            return _abstain(AbstentionReason.NO_EVIDENCE, policy)
        case AnswerOutcome.REFUSED | AnswerOutcome.INVALID:
            return _abstain(AbstentionReason.ANSWER_FAILED, policy)
        case AnswerOutcome.ABSTAINED:
            return _abstain(AbstentionReason.INSUFFICIENT_EVIDENCE, policy)
        case AnswerOutcome.ANSWERED:
            pass
    if type(cited) is not EvidenceSet:
        raise InvalidAbstentionInput("an Answer needs its cited EvidenceSet")
    if find_conflicts(result.text, cited):
        return _abstain(AbstentionReason.CONFLICTING_EVIDENCE, policy)
    if not _supported_enough(support, result.text, policy):
        return _abstain(AbstentionReason.UNSUPPORTED, policy)
    return AbstentionDecision(
        decision=Decision.ANSWER, reason=None, policy_version=policy.version
    )


def classify(answerable: bool, decision: AbstentionDecision) -> CaseOutcome:
    """Compare one decision with its Evaluation Case's answerability label."""
    if type(answerable) is not bool or type(decision) is not AbstentionDecision:
        raise InvalidAbstentionInput("a bool label and an AbstentionDecision")
    answered = decision.decision is Decision.ANSWER
    if answerable:
        return CaseOutcome.CORRECT_ANSWER if answered else CaseOutcome.OVER_ABSTENTION
    return (
        CaseOutcome.UNSUPPORTED_ANSWER if answered else CaseOutcome.CORRECT_ABSTENTION
    )


def score_abstention(
    cases: Sequence[tuple[bool, AbstentionDecision]],
) -> AbstentionScore:
    """Count each kind of outcome over labelled decisions."""
    counts = dict.fromkeys(CaseOutcome, 0)
    for item in cases:
        if type(item) is not tuple or len(cast(tuple[object, ...], item)) != 2:
            raise InvalidAbstentionInput("a case is an (answerable, decision) pair")
        answerable, decision = item
        counts[classify(answerable, decision)] += 1
    return AbstentionScore(
        correct_answers=counts[CaseOutcome.CORRECT_ANSWER],
        correct_abstentions=counts[CaseOutcome.CORRECT_ABSTENTION],
        over_abstentions=counts[CaseOutcome.OVER_ABSTENTION],
        unsupported_answers=counts[CaseOutcome.UNSUPPORTED_ANSWER],
    )


def calibrate_threshold(
    cases: Sequence[tuple[float, bool]], *, max_over_abstention: float
) -> float:
    """The reranker threshold for one reranker's labelled top scores.

    Each case is a question's best score and whether it is answerable. A
    threshold abstains below it. Of all thresholds that over-abstain on at
    most `max_over_abstention` of the answerable cases, this returns the
    one that answers fewest unanswerable cases; among equals, the lowest,
    so it abstains least.
    """
    if not _finite(max_over_abstention) or not 0 <= max_over_abstention <= 1:
        raise InvalidAbstentionInput("max_over_abstention must be from 0 to 1")
    labelled = [_case(item) for item in cases]
    answerable = sum(1 for _, label in labelled if label)
    if answerable == 0 or answerable == len(labelled):
        raise InvalidAbstentionInput("calibration needs both kinds of case")
    # 0.29 means 29 of 100, not the float just below it.
    allowed = math.floor(Fraction(repr(max_over_abstention)) * answerable)
    labelled.sort(key=lambda case: case[0])
    unanswerable = len(labelled) - answerable
    best: tuple[int, float] | None = None
    over = below_unanswerable = 0
    index = 0
    # One pass, lowest threshold first: a threshold abstains on every case
    # below it, so the counts below it only grow.
    while index < len(labelled):
        threshold = labelled[index][0]
        unsupported = unanswerable - below_unanswerable
        if over <= allowed and (best is None or unsupported < best[0]):
            best = (unsupported, threshold)
        while index < len(labelled) and labelled[index][0] == threshold:
            if labelled[index][1]:
                over += 1
            else:
                below_unanswerable += 1
            index += 1
    # Above every score: abstain on everything, if the budget allows it.
    if over <= allowed and (best is None or best[0] > 0):
        best = (0, math.nextafter(labelled[-1][0], math.inf))
    # The lowest score is always allowed: it abstains on nothing.
    return cast(tuple[int, float], best)[1]


def _similar(first: frozenset[str], second: frozenset[str]) -> bool:
    """True when two sets of words share at least 80% of all their words."""
    return len(first & second) >= _SAME_TOPIC * len(first | second)


def _supported_enough(
    support: SupportReport | None, text: str, policy: AbstentionPolicy
) -> bool:
    """True when the report is about this text and enough claims passed.

    Every claim counts; one that was not checked counts as unsupported.
    """
    if (
        type(support) is not SupportReport
        or support.outcome is not SupportOutcome.CHECKED
        or support.answer_sha256 != hashlib.sha256(text.encode("utf-8")).hexdigest()
    ):
        return False
    claims = split_claims(text)
    if not claims or tuple(f.claim for f in support.findings) != claims:
        return False
    supported = sum(
        1 for f in support.findings if f.verdict is SupportVerdict.SUPPORTED
    )
    return supported >= policy.min_supported_share * len(claims)


def _abstain(reason: AbstentionReason, policy: AbstentionPolicy) -> AbstentionDecision:
    return AbstentionDecision(
        decision=Decision.ABSTAIN, reason=reason, policy_version=policy.version
    )


def _check_policy(policy: object) -> None:
    if type(policy) is not AbstentionPolicy:
        raise InvalidAbstentionInput("policy must be an AbstentionPolicy")


def _case(item: object) -> tuple[float, bool]:
    if type(item) is not tuple or len(cast(tuple[object, ...], item)) != 2:
        raise InvalidAbstentionInput("a case is a (score, answerable) pair")
    score, label = cast(tuple[object, object], item)
    if not _finite(score) or type(label) is not bool:
        raise InvalidAbstentionInput("a case is a finite score and a bool label")
    return float(cast(float, score)), label


def _finite(value: object) -> bool:
    """A finite int or float, not a bool; never raises."""
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(cast(float, value)))
    except OverflowError:
        return False
```

Тут важливі три деталі. Порядок усередині `decide` — це і є політика: перемагає перша причина, що підходить, тож дешеві перевірки пошуку йдуть раніше за будь-що про відповідь. `_supported_enough` порівнює SHA-256 і твердження звіту з цим текстом, тож звіт іншої відповіді ніколи не пройде. А `calibrate_threshold` один раз сортує випадки і проходить їх від найменшої оцінки вгору, тож працює пропорційно кількості випадків, а не її квадрату.

Перевірка:

```bash
uv run python -c "import knowledge_service.answering.abstention"
uv run pyright src/knowledge_service/answering/abstention.py
```

Очікувано: імпорт нічого не друкує, а pyright показує 0 помилок.

### Крок 4 — Крок із кодом: тести і випадки оцінювання

Цей крок доводить кожен сигнал, кожну причину і оцінювання.

**Простими словами:** Тести будують справжні Evidence і відповіді, перевіряють їхні твердження лексичним суддею з M06-T06, а потім вирішують. Невеликий набір розмічених запитань показує всі чотири результати: правильну відповідь, правильне утримання, утримання на запитанні з відповіддю і відповідь на запитання без відповіді. Калібрування перевіряється на фіксованому прикладі і проти перебору на випадкових даних.

| Тест | Що доводить |
|---|---|
| `test_a_supported_answer_is_answered` | Успіх: на підтверджену відповідь система відповідає |
| `test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing` | Недозволене і відкликане повідомляються так само, як порожнє |
| `test_documents_that_disagree_on_the_number_abstain` | Суперечливі Evidence ведуть до утримання |
| `test_a_support_report_of_another_text_is_no_support` | Найризиковіший край: звіт іншої відповіді ніколи не пропускає цю |
| `test_a_score_the_threshold_was_not_calibrated_for_is_refused` | Типізована помилка: `UncalibratedScore` для оцінки іншого reranker-а |
| `test_evaluation_cases_distinguish_the_four_outcomes` | По одному розміченому випадку кожного виду рахується правильно |
| `test_the_calibrated_threshold_is_optimal_within_its_budget` | Фазинг: жоден інший поріг у межах бюджету не відповідає на менше запитань без відповіді |

Створіть `tests/test_abstention.py`:

```python
"""Calibrated abstention: evidence signals, one decision, and its evaluation."""

import math
import time
from dataclasses import replace
from fractions import Fraction
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

from evidence_fakes import MARKER, candidate
from knowledge_service.answering.abstention import (
    AbstentionDecision,
    AbstentionPolicy,
    AbstentionReason,
    AbstentionScore,
    CaseOutcome,
    Decision,
    EvidenceSignals,
    InvalidAbstentionInput,
    Sufficiency,
    UncalibratedScore,
    abstention_message,
    assess_evidence,
    calibrate_threshold,
    classify,
    decide,
    find_conflicts,
    score_abstention,
)
from knowledge_service.answering.citations import resolve_citations
from knowledge_service.answering.context_budget import ContextBudget, allocate_context
from knowledge_service.answering.evidence import EvidenceSet, build_evidence
from knowledge_service.answering.generation import AnswerOutcome, AnswerResult
from knowledge_service.answering.support import LexicalSupportJudge, check_support
from knowledge_service.contracts import Usage
from knowledge_service.language import Language
from knowledge_service.retrieval.reranking import RerankerIdentity

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000048")
RERANKER = RerankerIdentity(provider="local", model="bge-reranker-v2-m3")
POLICY = AbstentionPolicy(reranker=RERANKER, min_top_score=0.2, min_supported_share=1.0)
USAGE = Usage(
    provider="fake",
    model="fake-1",
    latency_ms=5,
    input_tokens=300,
    output_tokens=30,
    total_tokens=330,
)
LEAVE = "Every employee receives 24 days of annual leave."
OLD_LEAVE = "Every employee receives 20 days of annual leave."
CARRY = "Unused annual leave carries over for 5 days."


def evidence(*texts: str) -> EvidenceSet:
    return build_evidence(
        [candidate(n, text=text) for n, text in enumerate(texts, start=1)]
    )


def answered(text: str, cited: EvidenceSet) -> AnswerResult:
    return AnswerResult(
        outcome=AnswerOutcome.ANSWERED,
        text=text,
        reason=None,
        citations=resolve_citations(
            cited, [item.evidence_id.value for item in cited.items]
        ),
        defect=None,
        usage=USAGE,
        context=allocate_context(
            "Leave?", cited, ContextBudget(32_000, 1_000, 0)
        ).report,
    )


async def decide_answer(text: str, cited: EvidenceSet) -> AbstentionDecision:
    """Check support with the lexical judge, then decide."""
    result = answered(text, cited)
    support = await check_support(
        result,
        cited,
        LexicalSupportJudge(),
        request_id=REQUEST_ID,
        timeout_seconds=5,
    )
    return decide(Sufficiency.SUFFICIENT, result, cited, support, POLICY)


def signals(eligible: int = 3, top_score: float | None = 0.8) -> EvidenceSignals:
    return EvidenceSignals(
        eligible=eligible,
        top_score=top_score,
        reranker=None if top_score is None else RERANKER,
    )


def test_retrieval_is_judged_before_any_model_call() -> None:
    assert assess_evidence(signals(), POLICY) is Sufficiency.SUFFICIENT
    assert assess_evidence(signals(eligible=0), POLICY) is Sufficiency.EMPTY
    assert assess_evidence(signals(top_score=0.05), POLICY) is Sufficiency.WEAK
    # The threshold itself is enough.
    assert assess_evidence(signals(top_score=0.2), POLICY) is Sufficiency.SUFFICIENT


def test_without_a_threshold_any_eligible_evidence_is_sufficient() -> None:
    policy = AbstentionPolicy(reranker=None, min_top_score=None, min_supported_share=1)

    assert assess_evidence(signals(top_score=None), policy) is Sufficiency.SUFFICIENT


@pytest.mark.parametrize(
    "given_signals",
    [
        EvidenceSignals(eligible=3, top_score=0.9, reranker=None),
        EvidenceSignals(
            eligible=3,
            top_score=0.9,
            reranker=RerankerIdentity(provider="openai", model="gpt-5.4-mini"),
        ),
        EvidenceSignals(eligible=3, top_score=None, reranker=RERANKER),
        EvidenceSignals(eligible=3, top_score=float("nan"), reranker=RERANKER),
    ],
    ids=["no-reranker", "other-reranker", "no-score", "nan-score"],
)
def test_a_score_the_threshold_was_not_calibrated_for_is_refused(
    given_signals: EvidenceSignals,
) -> None:
    with pytest.raises(UncalibratedScore):
        assess_evidence(given_signals, POLICY)


def test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing() -> None:
    # Retrieval returns only what the reader may see: a question about a
    # hidden Document finds nothing, exactly like a question about nothing.
    empty = decide(Sufficiency.EMPTY, None, None, None, POLICY)
    cited = evidence(LEAVE)
    withdrawn = decide(
        Sufficiency.SUFFICIENT,
        replace(answered("x.", cited), outcome=AnswerOutcome.WITHDRAWN, citations=()),
        None,
        None,
        POLICY,
    )

    assert empty == withdrawn
    assert empty.reason is AbstentionReason.NO_EVIDENCE


async def test_a_supported_answer_is_answered() -> None:
    decision = await decide_answer("You get 24 days of annual leave.", evidence(LEAVE))

    assert decision == AbstentionDecision(
        decision=Decision.ANSWER, reason=None, policy_version="1"
    )


async def test_an_answer_with_an_unsupported_claim_abstains() -> None:
    decision = await decide_answer(
        "You get 24 days of annual leave. Unused leave carries over for 9 days.",
        evidence(LEAVE, CARRY),
    )

    assert decision.reason is AbstentionReason.UNSUPPORTED


async def test_documents_that_disagree_on_the_number_abstain() -> None:
    cited = evidence(LEAVE, OLD_LEAVE)

    decision = await decide_answer("You get 24 days of annual leave.", cited)

    assert find_conflicts("You get 24 days of annual leave.", cited) == (0,)
    assert decision.reason is AbstentionReason.CONFLICTING_EVIDENCE


def test_one_document_never_conflicts_with_itself() -> None:
    cited = build_evidence(
        [
            candidate(1, text=LEAVE),
            candidate(2, text=OLD_LEAVE, document_id=candidate(1).document_id),
        ]
    )

    assert find_conflicts("You get 24 days of annual leave.", cited) == ()


@pytest.mark.parametrize(
    "other",
    ["A leave request takes 3 days to approve.", CARRY],
    ids=["request-time", "carry-over"],
)
def test_a_sentence_about_something_else_is_no_conflict(other: str) -> None:
    # Both share the claim's words "days", "annual", and "leave", but say
    # something else, so their different number is no disagreement.
    cited = evidence(LEAVE, other)

    assert find_conflicts("You get 24 days of annual leave.", cited) == ()


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (AnswerOutcome.ABSTAINED, AbstentionReason.INSUFFICIENT_EVIDENCE),
        (AnswerOutcome.REFUSED, AbstentionReason.ANSWER_FAILED),
        (AnswerOutcome.INVALID, AbstentionReason.ANSWER_FAILED),
        (AnswerOutcome.NO_EVIDENCE, AbstentionReason.NO_EVIDENCE),
        (AnswerOutcome.WITHDRAWN, AbstentionReason.NO_EVIDENCE),
    ],
)
def test_every_outcome_that_is_not_an_answer_abstains_with_its_reason(
    outcome: AnswerOutcome, reason: AbstentionReason
) -> None:
    result = replace(answered("x.", evidence(LEAVE)), outcome=outcome, citations=())

    decision = decide(Sufficiency.SUFFICIENT, result, None, None, POLICY)

    assert (decision.decision, decision.reason) == (Decision.ABSTAIN, reason)


def test_weak_evidence_abstains_even_if_the_model_answered() -> None:
    cited = evidence(LEAVE)
    result = answered("You get 24 days of annual leave.", cited)

    decision = decide(Sufficiency.WEAK, result, cited, None, POLICY)

    assert decision.reason is AbstentionReason.WEAK_EVIDENCE


def test_an_answer_whose_support_was_not_checked_abstains() -> None:
    cited = evidence(LEAVE)

    decision = decide(
        Sufficiency.SUFFICIENT,
        answered("You get 24 days of annual leave.", cited),
        cited,
        None,
        POLICY,
    )

    assert decision.reason is AbstentionReason.UNSUPPORTED


@pytest.mark.parametrize("reason", list(AbstentionReason))
def test_each_reason_has_a_fixed_message_in_both_languages(
    reason: AbstentionReason,
) -> None:
    english = abstention_message(reason, Language.ENGLISH)
    ukrainian = abstention_message(reason, Language.UKRAINIAN)

    assert english != ukrainian
    assert abstention_message(reason, Language.UNKNOWN) == english
    assert MARKER not in english + ukrainian


@pytest.mark.parametrize(
    "build",
    [
        lambda: AbstentionPolicy(
            reranker=RERANKER, min_top_score=None, min_supported_share=1
        ),
        lambda: AbstentionPolicy(
            reranker=None, min_top_score=0.2, min_supported_share=1
        ),
        lambda: AbstentionPolicy(
            reranker=RERANKER, min_top_score=float("inf"), min_supported_share=1
        ),
        lambda: AbstentionPolicy(
            reranker=None, min_top_score=None, min_supported_share=1.5
        ),
        lambda: AbstentionPolicy(
            reranker=None, min_top_score=None, min_supported_share=0
        ),
        lambda: AbstentionPolicy(
            reranker=None, min_top_score=None, min_supported_share=True
        ),
        lambda: AbstentionPolicy(
            reranker=None, min_top_score=None, min_supported_share=1, version=""
        ),
    ],
    ids=[
        "threshold-missing",
        "reranker-missing",
        "infinite",
        "share-above-one",
        "share-zero",
        "bool-share",
        "no-version",
    ],
)
def test_a_policy_that_cannot_work_is_refused(build: Any) -> None:
    with pytest.raises(InvalidAbstentionInput):
        build()


def test_decide_refuses_an_answer_without_its_inputs() -> None:
    with pytest.raises(InvalidAbstentionInput):
        decide(Sufficiency.SUFFICIENT, None, None, None, POLICY)
    with pytest.raises(InvalidAbstentionInput):
        decide("sufficient", None, None, None, POLICY)  # type: ignore[arg-type]
    with pytest.raises(InvalidAbstentionInput):
        decide(
            Sufficiency.SUFFICIENT,
            answered("You get 24 days.", evidence(LEAVE)),
            None,
            None,
            POLICY,
        )


ANSWER = AbstentionDecision(decision=Decision.ANSWER, reason=None, policy_version="1")
ABSTAIN = AbstentionDecision(
    decision=Decision.ABSTAIN,
    reason=AbstentionReason.NO_EVIDENCE,
    policy_version="1",
)


def test_each_decision_is_classified_against_its_label() -> None:
    assert classify(True, ANSWER) is CaseOutcome.CORRECT_ANSWER
    assert classify(False, ABSTAIN) is CaseOutcome.CORRECT_ABSTENTION
    assert classify(True, ABSTAIN) is CaseOutcome.OVER_ABSTENTION
    assert classify(False, ANSWER) is CaseOutcome.UNSUPPORTED_ANSWER


async def test_evaluation_cases_distinguish_the_four_outcomes() -> None:
    # Each case: the answerability label, and what the pipeline produced.
    cases = [
        # A plain answerable question, answered and supported.
        (
            True,
            await decide_answer("You get 24 days of annual leave.", evidence(LEAVE)),
        ),
        # Nothing the reader may see: abstaining is right.
        (False, decide(Sufficiency.EMPTY, None, None, None, POLICY)),
        # Answerable, but the reranker scored it low: an over-abstention.
        (True, decide(Sufficiency.WEAK, None, None, None, POLICY)),
        # Unanswerable, yet the model wrote a number its Evidence holds:
        # the support check passes, so this is an unsupported answer.
        (
            False,
            await decide_answer(
                "Contractors get 24 days of annual leave.", evidence(LEAVE)
            ),
        ),
        # The model invented a number: support stops it.
        (
            False,
            await decide_answer("You get 30 days of annual leave.", evidence(LEAVE)),
        ),
    ]

    score = score_abstention(cases)

    assert score == AbstentionScore(
        correct_answers=1,
        correct_abstentions=2,
        over_abstentions=1,
        unsupported_answers=1,
    )
    assert score.total == len(cases)


def test_the_threshold_answers_fewest_unanswerable_within_its_budget() -> None:
    cases = [
        (0.95, True),
        (0.80, True),
        (0.60, True),
        (0.35, False),
        (0.30, True),
        (0.20, False),
        (0.10, False),
    ]

    strict = calibrate_threshold(cases, max_over_abstention=0.25)
    lenient = calibrate_threshold(cases, max_over_abstention=0.0)

    # Abstaining below 0.6 loses one answerable case of four (25%) and
    # answers no unanswerable one.
    assert strict == 0.60
    # With no over-abstention allowed, only the lowest answerable score works.
    assert lenient == 0.30
    policy = AbstentionPolicy(
        reranker=RERANKER, min_top_score=strict, min_supported_share=1
    )
    over = [s for s, label in cases if label and s < policy.min_top_score]  # type: ignore[operator]
    assert len(over) == 1


@pytest.mark.parametrize(
    ("cases", "budget"),
    [
        ([(0.9, True), (0.1, True)], 0.1),
        ([(0.9, False)], 0.1),
        ([(0.9, True), (float("nan"), False)], 0.1),
        ([(0.9, True), (0.1, "no")], 0.1),
        ([(0.9, True), [0.1, False]], 0.1),
        ([(0.9, True), (0.1, False)], 1.5),
    ],
    ids=[
        "only-answerable",
        "only-unanswerable",
        "nan",
        "text-label",
        "a-list",
        "budget",
    ],
)
def test_calibration_refuses_cases_it_cannot_use(cases: Any, budget: float) -> None:
    with pytest.raises(InvalidAbstentionInput):
        calibrate_threshold(cases, max_over_abstention=budget)


@given(
    cases=st.lists(
        st.tuples(st.floats(-10, 10, allow_nan=False), st.booleans()),
        min_size=2,
        max_size=40,
    ),
    budget=st.floats(0, 1),
)
def test_the_calibrated_threshold_is_optimal_within_its_budget(
    cases: list[tuple[float, bool]], budget: float
) -> None:
    if all(label for _, label in cases) or not any(label for _, label in cases):
        return

    threshold = calibrate_threshold(cases, max_over_abstention=budget)

    def counts(t: float) -> tuple[int, int]:
        over = sum(1 for s, label in cases if label and s < t)
        unsupported = sum(1 for s, label in cases if not label and s >= t)
        return over, unsupported

    answerable = sum(1 for _, label in cases if label)
    allowed = math.floor(Fraction(repr(budget)) * answerable)
    over, unsupported = counts(threshold)
    assert over <= allowed
    for other in {s for s, _ in cases} | {math.inf}:
        other_over, other_unsupported = counts(other)
        if other_over <= allowed:
            assert unsupported <= other_unsupported


async def test_a_support_report_of_another_text_is_no_support() -> None:
    cited = evidence(LEAVE)
    checked = answered("You get 24 days of annual leave.", cited)
    support = await check_support(
        checked, cited, LexicalSupportJudge(), request_id=REQUEST_ID, timeout_seconds=5
    )
    other = answered("You get 99 days of annual leave and a free car.", cited)

    decision = decide(Sufficiency.SUFFICIENT, other, cited, support, POLICY)

    assert decision.reason is AbstentionReason.UNSUPPORTED


async def test_a_support_report_with_missing_claims_is_no_support() -> None:
    cited = evidence(LEAVE)
    result = answered("You get 24 days of annual leave.", cited)
    support = await check_support(
        result, cited, LexicalSupportJudge(), request_id=REQUEST_ID, timeout_seconds=5
    )

    decision = decide(
        Sufficiency.SUFFICIENT, result, cited, replace(support, findings=()), POLICY
    )

    assert decision.reason is AbstentionReason.UNSUPPORTED


async def test_claims_the_judge_never_checked_count_as_unsupported() -> None:
    # 20 claims are judged; the five after them never are.
    text = (
        " ".join(["You get 24 days of annual leave."] * 20)
        + " "
        + " ".join(["Every employee is paid 1000000 euros."] * 5)
    )
    policy = AbstentionPolicy(
        reranker=None, min_top_score=None, min_supported_share=0.9
    )
    cited = evidence(LEAVE)
    result = answered(text, cited)
    support = await check_support(
        result, cited, LexicalSupportJudge(), request_id=REQUEST_ID, timeout_seconds=5
    )

    decision = decide(Sufficiency.SUFFICIENT, result, cited, support, policy)

    assert decision.reason is AbstentionReason.UNSUPPORTED


def test_a_number_only_one_document_adds_is_no_conflict() -> None:
    cited = evidence(
        LEAVE, "Every employee receives 24 days of annual leave since 2024."
    )

    assert find_conflicts("You get 24 days of annual leave.", cited) == ()


def test_a_claim_with_one_extra_word_still_finds_the_conflict() -> None:
    cited = evidence(LEAVE, OLD_LEAVE)

    assert find_conflicts("You get 24 paid days of annual leave.", cited) == (0,)


def test_finding_conflicts_in_long_repeated_evidence_is_fast() -> None:
    # Twenty Documents that all agree: no pair stops the search early.
    cited = evidence(*["Every employee receives 24 days of annual leave. " * 200] * 20)
    started = time.perf_counter()

    conflicts = find_conflicts(
        " ".join(["You get 24 days of annual leave."] * 5), cited
    )

    assert conflicts == ()
    assert time.perf_counter() - started < 1.0


def test_the_budget_is_read_as_the_decimal_it_was_written_as() -> None:
    cases = [(0.0, True)] * 29 + [(1.0, True)] * 71 + [(0.0, False)] * 10

    # 29 of 100 answerable cases is exactly 29%, within the budget.
    assert calibrate_threshold(cases, max_over_abstention=0.29) == 1.0


def test_with_the_whole_budget_the_threshold_may_abstain_on_everything() -> None:
    threshold = calibrate_threshold([(0.5, True), (0.9, False)], max_over_abstention=1)

    assert threshold > 0.9


class LyingIdentity:
    """Claims to equal any reranker."""

    def __eq__(self, other: object) -> bool:
        return True

    def __ne__(self, other: object) -> bool:
        return False

    __hash__ = object.__hash__


def test_a_forged_reranker_identity_cannot_borrow_the_threshold() -> None:
    forged = EvidenceSignals(eligible=3, top_score=0.9, reranker=LyingIdentity())  # type: ignore[arg-type]

    with pytest.raises(UncalibratedScore):
        assess_evidence(forged, POLICY)


@pytest.mark.parametrize("item", [(True,), (True, ANSWER, 1), None, [True, ANSWER]])
def test_a_case_that_is_not_a_pair_is_refused(item: object) -> None:
    with pytest.raises(InvalidAbstentionInput):
        score_abstention([item])  # type: ignore[list-item]
```

Тест оцінювання містить один випадок, на якому політика навмисно помиляється: «Contractors get 24 days» проходить лексичну перевірку підтвердження, бо речення містить число і більшість слів. Цей випадок рахується непідтвердженою відповіддю, і саме для цього існує оцінювання: воно показує, де політика хибить, щоб M10 міг виміряти, як часто.

Перевірка:

```bash
uv run pytest tests/test_abstention.py -q
```

Очікувано: 57 passed.

### Крок 5 — Без коду: зламайте і вирішіть

Цей крок показує, що перевірка підтвердження додає до рішення, і записує, чого ви навчилися.

**Дія 1:** навмисно зламайте політику. У `src/knowledge_service/answering/abstention.py`, у `decide`, видаліть ці два рядки:

```python
    if not _supported_enough(support, result.text, policy):
        return _abstain(AbstentionReason.UNSUPPORTED, policy)
```

Запустіть:

```bash
uv run pytest tests/test_abstention.py -q
```

Очікувано: 6 failed, 51 passed. Серед упалих — відповідь з непідтвердженим твердженням, відповідь, підтвердження якої не перевіряли, звіт іншого тексту, неперевірені твердження і тест оцінювання, де кількість непідтверджених відповідей зростає з 1 до 2. Без цих двох рядків відповідь з вигаданими «30 днями» дійде до користувача. Поверніть рядки і запускайте знову, доки все не пройде.

**Дія 2:** під `## Decision` запишіть:

- чому пошук оцінюється до виклику моделі;
- чому запитання про прихований Документ має звучати точно як запитання ні про що;
- чому поріг належить одному reranker-у і обирається за розміченими випадками, а не вгадується;
- що лишається на наступні уроки: виклик політики під час стрімінгу (M06-T09) і калібрування на справжньому наборі для оцінювання (M10);
- щонайменше одне **спростоване припущення** у формі «Я припускав X. Запуск чи рев'ю показує Y. Отже, Z хибне».

### Крок 6 — Без коду: архітектура, перевірки, evidence і прогрес

Цей крок оновлює текст, який зміна зробила неповним, доводить, що все досі працює, і записує результат.

**Дія 1:** у `docs/architecture.md` додайте політику до рядка `answering`. Знайдіть:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), and `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer) |
```

Замініть на:

```markdown
| `answering` | Question answering | `build_evidence` / `EvidenceSet` (`evidence`, the Chunks an Answer may use, each under a fresh opaque `EvidenceId`, with provenance, excerpt, and source link copied from retrieval) and `resolve_citations` / `read_citations` (`citations`, a model's Evidence IDs checked against the request's own set: unknown, malformed, or mismatched references are refused, a repeated one is cited once, and a repeat in stored Citations read back is refused, and a Citation's provenance always comes from the server), `render_prompt` (`prompt`, the fixed policy, the Question and numbered Evidence as one escaped JSON document, and a response schema that allows only this request's Evidence IDs), `allocate_context` (`context_budget`, splits a `ContextBudget` between the fixed policy, the Answer and History reserves, the Question, and Evidence; keeps the longest head of retrieval's order whose counted prompt fits, drops the rest whole, and reports kept and dropped Evidence IDs with token counts), `generate_answer` (`generation`, the `AnswerModel` port: fits the context, sends the counted prompt once, and checks the reply on the server into one outcome, answered with Citations of Evidence in the prompt, abstained with a reason, refused, invalid with a named defect, or no Evidence without a call; provider failures raise the M01 `ChatModelError` family) with `OpenAIAnswerModel` (`openai_answering`, a pinned model over the Responses API with Structured Outputs; `AnswerSettings` gives the budget), `confirm_citations` (`current_citations`, run after generation and before the client: one query rechecks every cited Chunk under the retrieval permission check and its current version; one Chunk that is gone, rewritten, tombstoned, or no longer visible withdraws the whole Answer, keeping its usage, and confirmed Citations are rebuilt from the stored rows), `check_support` (`support`, splits an Answer into claims by position and asks a versioned `SupportJudge`, the deterministic `LexicalSupportJudge` or a `ModelSupportJudge` over any `AnswerModel`, which cited Evidence supports each claim; the server keeps a finding only for a quote found in cited Evidence on word boundaries that holds the claim's numbers, and returns a separate `SupportReport` bound to the judge's version and the answer text's SHA-256, never editing the Answer), and `decide` (`abstention`, the answer policy: `assess_evidence` judges retrieval before any model call as empty or weak by a threshold calibrated for one `RerankerIdentity`, `find_conflicts` finds cited Documents that disagree on a claim's numbers, and `decide` answers, or abstains with one reason and a fixed bilingual message, answering only with a support report of this exact text; empty, unauthorized, and withdrawn read the same; `classify`, `score_abstention`, and `calibrate_threshold` measure correct answers, correct abstentions, over-abstentions, and unsupported answers against labelled cases) |
```

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_abstention.py tests/test_support.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний запуск дав 2040 passed для `make test-unit`, на 57 більше, ніж у M06-T06. Цей урок не чіпає коду бази, тож `make test-integration` має дати ті самі 342 passed. Якщо він каже, що тестової бази немає, запустіть її командою `docker start knowledge-service-postgres`.

**Дія 3:** доповніть файл evidence:

- **Design:** змінені файли; сигнали достатності; порядок причин у `decide`; чому порожнє і недозволене — одна причина; як звіт підтвердження зіставляється з текстом; чотири результати оцінювання; як калібрується поріг.
- **Verification:** команди й кількості, навмисний злам і відомі обмеження з «Перед початком».
- **Review:** адверсарне рев'ю знайшло десять проблем, кожну виправлено тестом, що падав до виправлення: звіт підтвердження іншого тексту пропускав будь-яку відповідь; твердження після перших 20 не рахувалися; пошук суперечностей займав 26 секунд на повтореному тексті; бюджет 0.29 втрачав випадок через округлення; поріг не міг утриматися на всьому; підроблена ідентичність reranker-а позичала поріг; випадок, що не є парою, падав; рік в одному Документі рахувався суперечністю; одне зайве слово ховало суперечність; політика могла вимагати нуль підтверджених тверджень.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу виконаною, додайте рядок M06-T07 до `docs/curriculum/PROGRESS.md`, поставте наступною задачею M06-T08 і закомітьте з `m06-t07: implement calibrated abstention`.

## Запитання для рефлексії

1. Що `decide` приховує від коду стрімінгу в M06-T09? Перелічіть, що тому коду ніколи не доведеться перевіряти, перш ніж показати відповідь чи утримання.
2. Пізніша зміна хоче пропускати перевірку суперечностей заради швидкості, бо «перевірка підтвердження вже звірила кожну цитату». Що піде не так для користувача, і який тест це впіймає?
3. Бюджет калібрування каже, як часто система може утримуватися на запитанні, що має відповідь. Хто має обирати це число для корпоративної бази знань, і що б ви показали цій людині, щоб вона обрала?
4. Запитання про Документ, якого користувач не може бачити, отримує те саме повідомлення, що й запитання ні про що. Що міг би дізнатися користувач, якби повідомлення відрізнялися, і як би ви перевірили тестом, що вони ніколи не відрізняються?
5. Який розмічений випадок ви додали б наступним, щоб знайти слабке місце цієї політики, і який результат очікуєте?

## Чекліст завершення

- [ ] Файл evidence містить інваріант і ваші передбачення, записані до коду.
- [ ] `numbers_in`, `topic_words` і `shares_topic` публічні в `support.py`, і лексичний суддя їх використовує.
- [ ] `assess_evidence` повертає порожньо, слабко чи досить і відхиляє оцінку іншого reranker-а.
- [ ] `find_conflicts` повідомляє про Документи, що розходяться в числах твердження, і ігнорує речення про інше.
- [ ] `decide` дає одне рішення з однією причиною у фіксованому порядку і відповідає лише з відповідним, достатнім звітом підтвердження.
- [ ] Порожнє, недозволене і відкликане дають ту саму причину і повідомлення.
- [ ] `classify` і `score_abstention` рахують правильні відповіді, правильні утримання, зайві утримання і непідтверджені відповіді.
- [ ] `calibrate_threshold` відповідає на найменше запитань без відповіді в межах бюджету.
- [ ] Жоден текст Документа, запитання чи відповіді не потрапляє в рішення чи повідомлення.
- [ ] Ви запустили навмисний злам, побачили 6 упалих тестів і скасували його.
- [ ] Усі перевірки проходять: форматування, лінтер, типи, юніт, інтеграційні, документація.
- [ ] Evidence, статус задачі, рядок PROGRESS і коміт готові.

Коли напишете файли, поділіться ними чи виводом помилок, і я перевірю їх за цим чеклістом.
