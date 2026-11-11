# M05-T16 — Пройдіть контрольну точку з гібридного пошуку

Англійська версія: [M05-T16-pass-the-hybrid-retrieval-gate.md](M05-T16-pass-the-hybrid-retrieval-gate.md)

Вихідне завдання: [M05-T16](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T16-pass-the-hybrid-retrieval-gate.md)

## Що ви зробите

Це контрольна точка M05. Вона перевіряє, що ви розумієте двомовний гібридний пошук настільки, щоб пояснити його, намалювати, знайти причину промаху і виправити його за вимірами, а не за одним вдалим прикладом. Урок поєднує письмо, діагностику і дві невеликі зміни коду:

| Завдання вимагає | Як урок це виконує |
|---|---|
| Пояснити мовні збої і злиття | Крок 2: ви відповідаєте на запитання у файлі evidence, з коду і з числами. |
| Оновити діаграму пошуку | Крок 3: ви малюєте гібридний пошук з пам'яті; крок 9 додає перевірену діаграму в `docs/architecture.md`. |
| Самостійно додати зріз оцінювання | Крок 4: ви плануєте зріз самі, а потім додаєте перевірений: мову, якою поставлено запитання, `en->*` і `uk->*`. |
| Діагностувати промах на українському запитанні | Крок 5: ви простежуєте `uk-uk-sick-first-day`, єдиний seed-випадок, який злиття ставить на друге місце, через обидві гілки і PostgreSQL. |
| Evidence пов'язує ремонт із метриками по зрізах | Кроки 6–8: ви плануєте і вносите ремонт українського пошуку за словами, порівнюєте кожен зріз до і після і навмисно ламаєте ремонт. |
| Детерміновані тести: успіх, найризиковіший край, типізована помилка | Кроки 4 і 7: зріз, правила слів, справжня сторінка seed-корпусу і синтаксис виключення |
| Авторизація до того, як дані покинуть сховище; жодного підкинутого тексту в телеметрії | Ремонт лишається в тому самому SQL-запиті, що й перевірка доступу, а запитання доходить до PostgreSQL лише як зв'язані параметри. |

Цей урок містить код: кроки 4, 6 і 7 пишуть код. Кроки 1, 2, 3, 5, 8 і 9 — це письмо, запуск команд і редагування Markdown. Ніщо не звертається до OpenAI; крокам 5 і 8 потрібен локальний сервер BGE-M3.

Терміни цього уроку:

- **Зріз (slice):** група випадків оцінювання, яку оцінюють окремо, наприклад усі запитання українською. Одне число на всі випадки може сховати групу, якій стало гірше.
- **Гілка (branch):** один зі способів пошуку всередині гібридного пошуку. Тут це **слова** (повнотекстовий пошук PostgreSQL) і **значення** (вектори BGE-M3).
- **Службове слово (stop word):** слово без теми, як-от «що», «в» чи «the». Англійський пошук відкидає англійські службові слова; до цього уроку український пошук зберігав кожне слово.
- **Слово теми (topic word):** слово запитання, яке не є службовим.
- **Reciprocal Rank Fusion (RRF):** кожна гілка дає шматку (Chunk) `1 / (60 + місце)`, а злита оцінка — це сума. Місце 1 дає 0.0164, місце 2 — 0.0161.

## Перед початком

Цей розділ пояснює, на чому тримається урок і що в ньому знайдено, щоб кожен крок був зрозумілим, коли ви до нього дійдете.

- **Що вже є.** `retrieve_hybrid` (M05-T06) запускає пошук за словами і за значенням одночасно і зливає їх через RRF (M05-T05). Пошук за словами направляє кожне запитання до конфігурації PostgreSQL `english`, `simple` або до обох (M05-T03). `simple` зберігає кожне слово малими літерами, без стемінгу: у M05-T02 це був свідомий базовий варіант для української. 16 seed-випадків (M05-T07) оцінюються за напрямком (`uk->en`: запитання українською, відповідь англійською) і за класом запиту.
- **Промах.** З BGE-M3 злиття знаходить кожен seed-випадок, але для `uk-uk-sick-first-day` («Що робити в перший день хвороби?») ставить потрібний шматок на друге місце. Попередні evidence лише здогадувалися чому. Крок 5 знаходить точну причину.
- **Що показує діагностика.** Пошук за значенням ставить правильний розділ, «Лікарняний > Перший день хвороби», першим. Пошук за словами знаходить лише вступний шматок сторінки, «Лікарняний», бо `websearch_to_tsquery` вимагає кожного слова, а в розділі немає «робити» і «в» (у тексті вжито «у»), які є у вступі. RRF тоді дає вступу два внески, місце 2 за значенням і місце 1 за словами, разом 0.0325, — більше, ніж єдиний внесок розділу, 0.0164. Сам PostgreSQL ранжує розділ вище (`ts_rank` 0.986 проти 0.912); правило «кожне слово» відкидає його ще до ранжування.
- **Ремонт і той, що спершу здавався правильним.** Вимагати будь-яке слово, без українських службових слів, — це лагодило випадок, але метрики по зрізах показали ціну: одне спільне слово на кшталт «днів» збігалося з українськими шматками для запитань про англійські сторінки, і MRR злиття на `uk->en` впав з 1.000 до 0.511. Збережений ремонт — посередині: шматок має містити **два** слова теми запитання або єдине, якщо воно одне. Тоді кожен зріз тримається або покращується.
- **Що знайшли рев'ю.** Три адверсаріальні рев'ю атакували ремонт; кожну діру виправлено, і для кожної є тест, що падає без виправлення:
  - Слово лише з розділових знаків, як «?», рахувалося як слово теми, яке ніколи не могло збігтися, тож «відпустка ?» нічого не знаходила. Слово, набране двічі, рахувалося двічі, тож одне спільне слово проходило правило двох слів. Виключення після дужки, `(-лікарняний)`, проскакувало повз перевірку синтаксису і робило протилежне.
  - Наступна версія ділила слова в Python інакше, ніж PostgreSQL, тож «ставка 1.5», дата, адреса пошти чи ім'я файлу нічого не знаходили. Тепер слова теми знаходить сам PostgreSQL, через `ts_debug` і той самий парсер, що й для збережених слів.
  - Слова теми шукалися заново для кожного шматка, що збігся. Тепер вони рахуються один раз на запит, і це вдвічі скоротило час на 50 000 шматків, що збігаються.
- **Запуск інтеграційних тестів.** Читайте URL прямо в кожній команді, щоб пароль ніколи не друкувався і не зберігався:

  ```bash
  KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_ukrainian_lexical_search_integration.py -q
  ```

- **Файли, які ви зачепите:**
  - новий файл evidence `docs/evidence/M05-T16-hybrid-retrieval-gate.md`
  - `src/knowledge_service/retrieval/evaluation_dataset.py` і `tests/test_evaluation_dataset.py`
  - `src/knowledge_service/retrieval/lexical_search.py`, `tests/test_lexical_search.py`, `tests/test_ukrainian_lexical_search_integration.py` і `tests/test_language_aware_lexical_search_integration.py`
  - `docs/architecture.md`

  Не чіпайте специфікацію задачі й `PROGRESS.md` до кроку 9.

- **Відомі обмеження.** 16 seed-випадків — це мало, а правило двох слів обрано, дивлячись саме на них. Українська й далі без стемінгу: «лікарняні» не збігається з «лікарняний». PostgreSQL ділить деякі слова надвоє, `E-1042` на «e» і «-1042», а `п'ять` на «п» і «ять», і кожна частина рахується як слово теми. Запитання із синтаксисом вебпошуку, зокрема з тире на кшталт «Відпустка - це», читається як набране, з вимогою кожного слова. Список службових слів короткий і складений вручну. Запитання, слова якого збігаються з десятками тисяч шматків, коштує сотні мілісекунд: 782 мс на 50 000 шматків, що збігаються, проти приблизно 100 мс для старого запиту «кожне слово».

## Покроково

### Крок 1 — Без коду: запишіть інваріант і свої передбачення

Перш ніж щось зміниться, запишіть, що має бути правдою наприкінці і що, на вашу думку, ви знайдете. Крок 8 перевірить передбачення.

Створіть `docs/evidence/M05-T16-hybrid-retrieval-gate.md`:

```markdown
# M05-T16 — Hybrid-retrieval gate evidence

## Invariant

Hybrid retrieval returns only Chunks the reader may see: each branch
applies the permission check in its own SQL statement, and fusion only
reorders what the branches returned. A repair to one language's word
search is accepted only when every slice of the seed evaluation holds or
improves, for fused search as well as for the branch, and the Question
reaches PostgreSQL only as bound parameters.

## Expected failing check

Before the change, `uv run python -c "from knowledge_service.retrieval.lexical_search import simple_query"`
fails with `ImportError`.

## Predictions

- Why fused search ranks `uk-uk-sick-first-day` second: ...
- How many of the 8 Questions asked in Ukrainian word search finds today: ...
- What happens to `uk->en` if Ukrainian word search accepts any one word: ...
- Fused MRR on `uk->*` before and after your repair: ...

## Language-specific failures and fusion

## Diagram

## Slice plan

## Diagnosis

## Repair plan

## Design

## Verification

## Decision

## Reflection
```

Перевірка: файл існує, перевірка, що має падати, падає, і кожне передбачення — повне речення, яке крок 8 може спростувати.

### Крок 2 — Без коду: поясніть мовні збої і злиття

Цей крок просить пояснити з коду, чому кожна мова збоїть там, де збоїть, і як злиття поєднує гілки. Пишіть своїми словами і називайте файл, який прочитали.

У розділі `## Language-specific failures and fusion` дайте відповіді:

1. Що конфігурація `english` робить із «requesting leave» і «the who», а що `simple` — із «відпустку» і «відпустки»? Прочитайте `lexical_search.py` і `lexical_routing.py`.
2. Чому пошук за словами нічого не знаходить для `uk-en-annual-leave-days`, українського запитання про англійську сторінку, і що знаходить її натомість?
3. Чому ідентифікатор на кшталт `EXP-2041` направляється в обидві конфігурації і чому обидві його знаходять?
4. З RRF і k = 60 порахуйте злиту оцінку шматка, який лише пошук за значенням поставив на місце 1, і шматка, який пошук за значенням поставив на місце 2, а пошук за словами — на місце 1. Хто виграє і що це каже про згоду між гілками? Прочитайте `fusion.py`.
5. Що повертає `retrieve_hybrid`, коли одна гілка падає чи не встигає до дедлайну, і як про це дізнається той, хто викликає? Прочитайте `hybrid.py`.

Перевірка: кожна відповідь називає функцію чи налаштування, звідки вона взята. Крок 5 перевірить відповідь 4 на справжньому випадку.

### Крок 3 — Без коду: намалюйте гібридний пошук з пам'яті

У `docs/architecture.md` є діаграма векторного пошуку з M04, але немає картинки гібридного пошуку. Перш ніж побачите перевірену діаграму в кроці 9, намалюйте свою.

У розділі `## Diagram` напишіть з пам'яті Mermaid `sequenceDiagram` (або нумерований список, якщо так зручніше), де є:

- той, хто викликає, і що він передає, включно із заявленою мовою і дедлайном;
- дві гілки, що працюють одночасно, і що кожна надсилає до PostgreSQL чи моделі ембедингів;
- де відбувається перевірка доступу;
- що стається, коли падає одна гілка і коли падають усі;
- формула злиття.

Перевірка: ваш малюнок охоплює всі п'ять пунктів. У кроці 9 порівняйте його з перевіреною діаграмою і запишіть, що пропустили.

### Крок 4 — Крок із кодом: додайте зріз за мовою запитання

Задача вимагає додати один зріз «самостійно». Перш ніж читати код нижче, запишіть власний план у розділі `## Slice plan`: які випадки групує зріз, чому таке групування покаже зміну українського пошуку за словами краще, ніж наявні зрізи, де в `score_slices` він стоїть, і один тест.

**Простими словами:** Звіт уже оцінює кожен напрямок, наприклад українські запитання про англійські сторінки. Зміна українського пошуку за словами зачіпає кожне запитання українською, хоч би якою мовою була сторінка, тож цей крок додає два зрізи: усі випадки англійською (`en->*`) і всі українською (`uk->*`). У кожному вісім seed-випадків — більше, ніж в будь-якому окремому напрямку, тож один випадок менше рухає оцінку.

У `src/knowledge_service/retrieval/evaluation_dataset.py` внесіть ці правки по черзі:

**1.** Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
  Questions are sent to the rewriter. `retrieval.rewrite_evaluation`
  scores the gain per class and decides which classes may be rolled out.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
  MRR for every language direction and query class (see
  `retrieval.ranking_metrics`).
- **Eligible.** A Candidate from `restricted/` raises
  `IneligibleChunkRetrieved` and stops the run, however relevant it is.
- **Typed.** Any error a case raises becomes `CaseFailed`, naming the case
```

Замініть на:

```python
  Questions are sent to the rewriter. `retrieval.rewrite_evaluation`
  scores the gain per class and decides which classes may be rolled out.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
  MRR for every language direction, every question language (`en->*`,
  `uk->*`), and every query class (see `retrieval.ranking_metrics`).
- **Eligible.** A Candidate from `restricted/` raises
  `IneligibleChunkRetrieved` and stops the run, however relevant it is.
- **Typed.** Any error a case raises becomes `CaseFailed`, naming the case
```

**2.** Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python

_LANGUAGES = (Language.ENGLISH, Language.UKRAINIAN)
DIRECTIONS = tuple(f"{asked}->{found}" for asked in _LANGUAGES for found in _LANGUAGES)

# Fixed, so two runs of the same dataset store the same Sources and reader.
SOURCE_IDS: Mapping[Folder, SourceId] = {
```

Замініть на:

```python

_LANGUAGES = (Language.ENGLISH, Language.UKRAINIAN)
DIRECTIONS = tuple(f"{asked}->{found}" for asked in _LANGUAGES for found in _LANGUAGES)
# The language a Question is asked in, whatever the evidence's language.
QUESTION_LANGUAGES = tuple(f"{asked}->*" for asked in _LANGUAGES)

# Fixed, so two runs of the same dataset store the same Sources and reader.
SOURCE_IDS: Mapping[Folder, SourceId] = {
```

**3.** Знайдіть у `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python


def score_slices(report: DatasetReport) -> tuple[SliceScore, ...]:
    """Score every case together, then each direction, then each query class."""
    cutoffs = tuple(k for k in RECALL_CUTOFFS if k <= report.limit)
    groups: list[tuple[str, list[CaseOutcome]]] = [("all", list(report.outcomes))]
    groups += [
        (direction, [o for o in report.outcomes if o.direction == direction])
        for direction in DIRECTIONS
    ]
    groups += [
        (str(query_class), [o for o in report.outcomes if o.query_class is query_class])
```

Замініть на:

```python


def score_slices(report: DatasetReport) -> tuple[SliceScore, ...]:
    """Score every case together, then each direction, each question language,
    then each query class.

    A question-language slice such as `uk->*` holds every case asked in that
    language, `uk->uk` and `uk->en` together: what a change to how
    Questions are read, such as Ukrainian word search, moves first.
    """
    cutoffs = tuple(k for k in RECALL_CUTOFFS if k <= report.limit)
    groups: list[tuple[str, list[CaseOutcome]]] = [("all", list(report.outcomes))]
    groups += [
        (direction, [o for o in report.outcomes if o.direction == direction])
        for direction in DIRECTIONS
    ]
    groups += [
        (asked, [o for o in report.outcomes if o.direction.startswith(asked[:-1])])
        for asked in QUESTION_LANGUAGES
    ]
    groups += [
        (str(query_class), [o for o in report.outcomes if o.query_class is query_class])
```

У `tests/test_evaluation_dataset.py` внесіть цю правку:

**1.** Знайдіть у `tests/test_evaluation_dataset.py`:

```python
    assert scores["uk->uk"].mrr is None


def test_the_report_shows_the_configuration_and_the_scores() -> None:
    rendered = render_report(SCORED)
```

Замініть на:

```python
    assert scores["uk->uk"].mrr is None


def test_each_question_language_is_a_slice_of_its_own() -> None:
    scores = score_slices(SCORED)
    by_name = {score.name: score for score in scores}

    # Directions first, then the language asked in, then query classes.
    assert [score.name for score in scores][:7] == [
        "all",
        "en->en",
        "en->uk",
        "uk->en",
        "uk->uk",
        "en->*",
        "uk->*",
    ]
    assert (by_name["en->*"].cases, by_name["en->*"].mrr) == (3, Fraction(5, 12))
    # Asked in Ukrainian: uk->en and uk->uk together; here only "d".
    assert (by_name["uk->*"].cases, by_name["uk->*"].mrr) == (1, Fraction(1, 3))


def test_the_report_shows_the_configuration_and_the_scores() -> None:
    rendered = render_report(SCORED)
```

Назви зрізів повторюють формат напрямків, тож звіти більше нічого не потребують: у кожній таблиці зрізів з'являється новий рядок, між напрямками і класами запитів.

Перевірка:

```bash
uv run pytest tests/test_evaluation_dataset.py -q
uv run ruff check src/knowledge_service/retrieval/evaluation_dataset.py tests/test_evaluation_dataset.py
uv run pyright src/knowledge_service/retrieval/evaluation_dataset.py
```

Очікувано: усе проходить, `All checks passed!` і `0 errors`. У розділі `## Slice plan` запишіть, що ваш план зробив інакше.

### Крок 5 — Без коду: діагностуйте український промах

Цей крок з доказами знаходить, чому злиття ставить `uk-uk-sick-first-day` на друге місце.

**Дія 1:** запустіть сервер BGE-M3 в окремому терміналі й залиште його працювати для кроків 5 і 8:

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py
```

Якщо порт 8765 зайнятий, запустіть його з `BGE_M3_PORT=8766` на початку, а в іншому терміналі спершу виконайте `export LOCAL_EMBEDDING_URL=http://127.0.0.1:8766`.

**Дія 2:** у другому терміналі прогоніть seed-набір трьома способами на свіжій базі, збережіть звіт як вимір «до» і залиште базу для дії 3:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_dataset#')"
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --local-embeddings --output /tmp/m05-t16-before.md
```

Очікувано, у першій таблиці: MRR злиття («dense + lexical») 0.969 для `all` і 0.900 для `uk->uk`; лексичний R@10 0.125 для `uk->*`, тобто пошук за словами знаходить правильний шматок лише для одного з восьми українських запитань. У таблиці випадків злиття `uk-uk-sick-first-day` має місце 2; у dense — місце 1; у lexical — `missed`.

**Дія 3:** спитайте PostgreSQL, як він читає запитання і які шматки сторінки збігаються:

```bash
docker exec knowledge-service-postgres psql -U test_user -d knowledge_service_dataset -c "SELECT websearch_to_tsquery('simple', 'Що робити в перший день хвороби?');"
docker exec knowledge-service-postgres psql -U test_user -d knowledge_service_dataset -c "
SELECT heading_path,
       search_simple @@ websearch_to_tsquery('simple', 'Що робити в перший день хвороби?') AS every_word,
       round(ts_rank(search_simple, websearch_to_tsquery('simple', 'Що робити в перший день хвороби?'))::numeric, 3) AS rank
FROM chunks WHERE heading_path LIKE 'Лікарняний%' ORDER BY ordinal;"
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
```

Очікувано:

```text
'що' & 'робити' & 'в' & 'перший' & 'день' & 'хвороби'

           heading_path           | every_word | rank
----------------------------------+------------+-------
 Лікарняний                       | t          | 0.912
 Лікарняний > Перший день хвороби | f          | 0.986
 Лікарняний > Оплата лікарняного  | f          | 0.000
```

**Дія 4:** у розділі `## Diagnosis` запишіть ланцюжок із доказів:

- яка гілка на яке місце ставить правильний розділ, за звітом;
- чого вимагає запит і яких слів бракує розділу;
- що PostgreSQL ранжує розділ вище, але правило «кожне слово» прибирає його раніше;
- арифметику RRF із кроку 2, тепер із місцями цього випадку;
- причину одним реченням і те, на який ремонт вона вказує.

Перевірка: кожне твердження діагностики вказує на рядок звіту чи виводу psql.

### Крок 6 — Крок із кодом: полагодьте український пошук за словами

Перш ніж читати код, запишіть свій план у розділі `## Repair plan`: що змінити в тому, як `simple` читає запитання, що не має змінитися (англійська, перевірка доступу, зв'язані параметри, синтаксис вебпошуку) і як ви дізнаєтеся, що спрацювало. Потім порівняйте його з перевіреною зміною нижче.

**Простими словами:** Пошук за словами для українського запитання тепер ігнорує слова без теми, як «що» чи «в», і перестає вимагати кожного з решти слів. PostgreSQL сам ділить запитання на слова, так само як ділив збережені сторінки, тож дата чи десяткове число лишається одним словом. Шматок має містити щонайменше два слова теми запитання або єдине, якщо воно одне, і тоді власне ранжування PostgreSQL ставить розділ «Перший день хвороби» вище за вступ сторінки. Запитання із синтаксисом пошуку, як лапки чи `-слово`, читається точно як набране, як і раніше.

| Частина | Що робить |
|---|---|
| `UKRAINIAN_STOP_WORDS` | прийменники, сполучники, частки, займенники, питальні слова і форми «бути» |
| `SimpleQuery`, `simple_query` | чи читає `simple` запитання за словами теми, чи як набране |
| `_uses_syntax` | власне правило PostgreSQL для лапок, `-` після небуквеного символу і окремого «or» |
| `_topic_match` | у SQL: `ts_debug` ділить запитання парсером збережених слів; частини складених слів і службові слова відкидаються, кожне слово рахується раз, лишаються перші `MAX_SIMPLE_WORDS`; запит «or» для індексу і умова, що власні слова шматка містять `MIN_SIMPLE_WORDS` із них |
| `lexical_search_statement` | `simple` використовує `_topic_match`, якщо текст не читається як набраний; `english` без змін |

У `src/knowledge_service/retrieval/lexical_search.py` внесіть ці правки по черзі:

**1.** Знайдіть у `src/knowledge_service/retrieval/lexical_search.py`:

```python
- `chunks.search_simple`, built with the `simple` configuration: every word
  lowercased, with no stemming and no stop words. `search_ukrainian_chunks`
  reads it. This is only a baseline for Ukrainian: "відпустка" matches
  "Відпустка" but not "відпустку" or "відпустки", an apostrophe splits
  "сім'я" into "сім" and "я", and a common word such as "на" matches almost
  every Chunk.

The query goes through the same configuration as the terms it searches.
`search_lexical_chunks` chooses the configuration for each Question with
```

Замініть на:

```python
- `chunks.search_simple`, built with the `simple` configuration: every word
  lowercased, with no stemming and no stop words. `search_ukrainian_chunks`
  reads it. This is only a baseline for Ukrainian: "відпустка" matches
  "Відпустка" but not "відпустку" or "відпустки", and an apostrophe splits
  "сім'я" into "сім" and "я".

Without stemming, asking for every word of a Ukrainian Question finds
almost nothing: "Що робити в перший день хвороби?" misses the section
headed "Перший день хвороби", which lacks "робити" and "в". So
`simple` reads a Question as its topic words, without the Ukrainian
function words in `UKRAINIAN_STOP_WORDS`, and a Chunk matches when it
holds at least two of them, or the only one there is. One shared word is
not enough: "днів" alone would match a sick-leave page for a question
about annual leave. PostgreSQL finds the topic words with the same
parser as the stored terms, so a date, a decimal, or an address stays one
word, and `ts_rank` then puts the Chunks with the most and best-placed
words first. A Question that uses web-search syntax is read as typed; see
`simple_query`. The stored terms keep every word, so the list can change
without rebuilding them.

The query goes through the same configuration as the terms it searches.
`search_lexical_chunks` chooses the configuration for each Question with
```

**2.** Знайдіть у `src/knowledge_service/retrieval/lexical_search.py`:

```python
can rely on besides:

- **Syntax.** The query is web-search text, read by `websearch_to_tsquery`:
  words must all match, `"quoted words"` must appear next to each other in
  that order, `or` between words accepts either, and `-word` excludes a word.
  Any other punctuation is ignored, so no text a user types can raise a
  syntax error. A query of only stop words or punctuation matches nothing.
```

Замініть на:

```python
can rely on besides:

- **Syntax.** The query is web-search text, read by `websearch_to_tsquery`:
  with `english`, words must all match; with `simple`, two of its topic
  words must match, as above, unless the text itself uses the syntax
  below, which is then read as typed: `"quoted words"` must appear next to each other in
  that order, `or` between words accepts either, and `-word` excludes a word.
  Any other punctuation is ignored, so no text a user types can raise a
  syntax error. A query of only stop words or punctuation matches nothing.
```

**3.** Знайдіть у `src/knowledge_service/retrieval/lexical_search.py`:

```python
  `InvalidLexicalQuery` before the database is used.
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import RowMapping
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
```

Замініть на:

```python
  `InvalidLexicalQuery` before the database is used.
"""

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import RowMapping
from sqlalchemy.dialects.postgresql import ARRAY, REGCONFIG
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
```

**4.** Знайдіть у `src/knowledge_service/retrieval/lexical_search.py`:

```python

# Long enough for any question, short enough to bound the work per query.
MAX_QUERY_CHARACTERS = 1_000

# Named, not taken from the server's `default_text_search_config`, and the
# same configurations the stored vectors were built with.
```

Замініть на:

```python

# Long enough for any question, short enough to bound the work per query.
MAX_QUERY_CHARACTERS = 1_000

# A `simple` match must hold this many topic words of the Question, or all
# of them when it has fewer. At most `MAX_SIMPLE_WORDS` are read, in the
# order typed, which bounds the work for each matching Chunk.
MIN_SIMPLE_WORDS = 2
MAX_SIMPLE_WORDS = 16
# PostgreSQL's web-search "or": a word with no letter or digit next to it.
_OR = re.compile(r"or", re.IGNORECASE)
# The parts of a hyphenated word, which PostgreSQL also stores whole.
_WORD_PARTS = "hword%part"

# Ukrainian function words: prepositions, conjunctions, particles, pronouns,
# question words, and forms of "to be". Lowercase and in NFC, the form the
# query is matched in. They carry no topic, and as an "or" query word each
# would match nearly every Ukrainian Chunk.
UKRAINIAN_STOP_WORDS = frozenset(
    {
        # Prepositions.
        "в",
        "у",  # noqa: RUF001 - a Ukrainian word
        "на",
        "до",
        "з",
        "із",
        "зі",
        "від",
        "для",
        "по",
        "про",
        "при",
        "за",
        "над",
        "під",
        "між",
        "через",
        "без",
        "біля",
        "після",
        "перед",
        "серед",
        "щодо",
        # Conjunctions and particles.
        "і",  # noqa: RUF001 - a Ukrainian word
        "й",
        "та",
        "а",  # noqa: RUF001 - a Ukrainian word
        "але",
        "або",  # noqa: RUF001 - a Ukrainian word
        "чи",
        "що",
        "щоб",
        "як",
        "якщо",
        "бо",  # noqa: RUF001 - a Ukrainian word
        "не",
        "ні",
        "же",
        "ж",
        "б",  # noqa: RUF001 - a Ukrainian word
        "би",
        "лише",
        "тільки",
        "ще",
        "вже",
        # Pronouns and question words.
        "я",
        "ти",
        "він",
        "вона",
        "воно",
        "ми",
        "ви",
        "вони",
        "мене",
        "мені",
        "мій",
        "моя",
        "моє",
        "мої",
        "наш",
        "ваш",
        "його",
        "її",
        "їх",
        "це",
        "цей",
        "ця",
        "ці",
        "той",
        "те",
        "ті",
        "хто",
        "де",
        "куди",
        "коли",
        "чому",
        "який",
        "яка",
        "яке",
        "які",
        # Forms of "to be".
        "є",
        "бути",
        "був",
        "була",
        "було",
        "були",
        "буде",
    }
)

# Named, not taken from the server's `default_text_search_config`, and the
# same configurations the stored vectors were built with.
```

**5.** Знайдіть у `src/knowledge_service/retrieval/lexical_search.py`:

```python
    context: AuthorizationContext,
    limit: int,
) -> tuple[LexicalCandidate, ...]:
    """Return up to `limit` eligible Chunks containing the exact words of `text`.

    Words match after lowercasing only, in the form they were typed. Raises
    `InvalidLexicalQuery` like `search_english_chunks`.
    """
    candidates, _ = await _search(
        connection,
```

Замініть на:

```python
    context: AuthorizationContext,
    limit: int,
) -> tuple[LexicalCandidate, ...]:
    """Return up to `limit` eligible Chunks matching Ukrainian `text`, best first.

    Words match after lowercasing only, in the form they were typed. The
    Question is read by its topic words: Ukrainian stop words are dropped,
    and a Chunk must hold two of the rest, or the only one. Text that uses
    web-search syntax is read as typed, every word required; see
    `simple_query`. Raises `InvalidLexicalQuery` like `search_english_chunks`.
    """
    candidates, _ = await _search(
        connection,
```

**6.** Знайдіть у `src/knowledge_service/retrieval/lexical_search.py`:

```python
        raise InvalidLexicalQuery(
            "configurations must be distinct LexicalConfiguration values"
        )
    # The text is one bound parameter, read with the configuration the
    # stored terms were built with.
    query_text = sa.bindparam("query_text", text, type_=sa.Text())
    queries: list[tuple[sa.ColumnElement[Any], sa.ColumnElement[Any]]] = []
    conditions: list[sa.ColumnElement[bool]] = []
    ranks: list[sa.ColumnElement[Any]] = []
    for configuration in configurations:
        terms, regconfig = _TERMS[configuration]
        query = sa.func.websearch_to_tsquery(regconfig, query_text)
        queries.append((regconfig, query))
        conditions.append(terms.bool_op("@@")(query))
        ranks.append(sa.func.ts_rank(terms, query))
    if len(configurations) == 1:
        matched, score = conditions[0], ranks[0]
```

Замініть на:

```python
        raise InvalidLexicalQuery(
            "configurations must be distinct LexicalConfiguration values"
        )
    # The text is a bound parameter, read with the configuration the stored
    # terms were built with; `simple` reads its topic words (see
    # `simple_query`).
    query_text = sa.bindparam("query_text", text, type_=sa.Text())
    queries: list[tuple[sa.ColumnElement[Any], sa.ColumnElement[Any]]] = []
    conditions: list[sa.ColumnElement[bool]] = []
    ranks: list[sa.ColumnElement[Any]] = []
    for configuration in configurations:
        terms, regconfig = _TERMS[configuration]
        if (
            configuration is LexicalConfiguration.SIMPLE
            and not simple_query(text).as_typed
        ):
            query, condition = _topic_match(terms, query_text)
        else:
            query = sa.func.websearch_to_tsquery(regconfig, query_text)
            condition = terms.bool_op("@@")(query)
        queries.append((regconfig, query))
        conditions.append(condition)
        ranks.append(sa.func.ts_rank(terms, query))
    if len(configurations) == 1:
        matched, score = conditions[0], ranks[0]
```

**7.** Знайдіть у `src/knowledge_service/retrieval/lexical_search.py`:

```python
        ranked.c.text,
        *marked,
    ).order_by(ranked.c.score.desc(), ranked.c.chunk_id)


async def _search(
```

Замініть на:

```python
        ranked.c.text,
        *marked,
    ).order_by(ranked.c.score.desc(), ranked.c.chunk_id)


@dataclass(frozen=True, slots=True)
class SimpleQuery:
    """How `simple` reads one Question: by its topic words, or as typed."""

    text: str = field(repr=False)
    as_typed: bool


def simple_query(text: str) -> SimpleQuery:
    """Return how `simple` reads `text`.

    Text that uses web-search syntax the way PostgreSQL reads it, a quote,
    a `-` at the start or after anything but a letter or digit, or an "or"
    with no letter or digit on either side, is read as typed: every word
    must match, so a phrase or an exclusion keeps its meaning. Any other
    text is read by its topic words (see `_topic_match`).
    """
    return SimpleQuery(text=text, as_typed=_uses_syntax(text))


def _uses_syntax(text: str) -> bool:
    """True when web-search syntax would read part of `text` as an operator."""
    if '"' in text:
        return True
    for index, character in enumerate(text):
        # PostgreSQL reads such a `-` as "without", even before a space.
        if character == "-" and (index == 0 or not text[index - 1].isalnum()):
            return True
    for match in _OR.finditer(text):
        before = text[match.start() - 1] if match.start() > 0 else " "
        after = text[match.end()] if match.end() < len(text) else " "
        if not before.isalnum() and not after.isalnum():
            return True
    return False


def _topic_match(
    terms: sa.ColumnElement[Any], query_text: sa.BindParameter[str]
) -> tuple[sa.ColumnElement[Any], sa.ColumnElement[bool]]:
    """Return the "or" query of the topic words, and the two-word condition.

    PostgreSQL splits the Question with the same parser as the stored terms
    (`ts_debug`), so a date, a decimal, an address, or a file name stays one
    word, and "?" or "²" is none. The parts of a hyphenated word are left
    out, since the whole word is kept; stop words are dropped; each word
    counts once, and the first `MAX_SIMPLE_WORDS` in typed order are read.
    A Chunk matches when its stored words hold `MIN_SIMPLE_WORDS` of them,
    or all when there are fewer. With no topic word the query is NULL,
    which matches nothing.
    """
    # Every column `ts_debug` returns, in its order, then the token's place.
    parsed = (
        sa.func.ts_debug(SIMPLE, query_text)
        .table_valued(
            "alias",
            "description",
            "token",
            "dictionaries",
            "dictionary",
            "lexemes",
            with_ordinality="position",
        )
        .render_derived()
    )
    word = sa.type_coerce(parsed.c.lexemes, ARRAY(sa.Text()))[1]
    stop_words = sa.bindparam(
        "simple_stop_words", sorted(UKRAINIAN_STOP_WORDS), type_=ARRAY(sa.Text())
    )
    topic = (
        sa.select(word.label("word"))
        .where(
            sa.func.cardinality(parsed.c.lexemes) > 0,
            sa.not_(parsed.c.alias.like(_WORD_PARTS)),
            word != sa.func.all(stop_words),
        )
        .group_by(word)
        .order_by(sa.func.min(parsed.c.position))
        .limit(MAX_SIMPLE_WORDS)
        .subquery("topic_words")
    )
    # Uncorrelated, so PostgreSQL computes each once per statement, not per row.
    words = sa.select(sa.func.array_agg(topic.c.word)).scalar_subquery()
    query = sa.select(
        sa.func.to_tsquery(
            SIMPLE, sa.func.string_agg(sa.func.quote_literal(topic.c.word), " | ")
        )
    ).scalar_subquery()
    needed = sa.func.least(MIN_SIMPLE_WORDS, sa.func.cardinality(words))
    # The Chunk's own stored words, read from the outer row, never a table scan.
    stored = sa.select(sa.func.unnest(sa.func.tsvector_to_array(terms))).correlate(
        chunks
    )
    held = sa.func.cardinality(
        sa.func.array(
            stored.intersect(sa.select(sa.func.unnest(words))).scalar_subquery()
        )
    )
    return query, sa.and_(terms.bool_op("@@")(query), held >= needed)


async def _search(
```

Коментарі `# noqa: RUF001` потрібні, бо ruff позначає однолітерні кириличні слова на кшталт «у» чи «а» як двійників латинських літер.

Перевірка:

```bash
uv run ruff format src/knowledge_service/retrieval/lexical_search.py
uv run ruff check src/knowledge_service/retrieval/lexical_search.py
uv run pyright src/knowledge_service/retrieval/lexical_search.py
uv run python -c "from knowledge_service.retrieval.lexical_search import simple_query; print(simple_query('Що робити в перший день хвороби?'))"
```

Очікувано: `All checks passed!`, `0 errors` і `SimpleQuery(as_typed=False)`: саме запитання сховане з repr.

### Крок 7 — Крок із кодом: протестуйте ремонт

Цей крок доводить правила без бази даних, а ремонт — на справжній сторінці seed-корпусу з базою.

**Простими словами:** Unit-тести перевіряють, які запитання читаються за словами теми, а які як набрані, для кожного виду синтаксису пошуку, і що запитання доходить до PostgreSQL лише як зв'язані параметри, а службові слова — одним масивом. Інтеграційні тести завантажують справжню сторінку seed-корпусу і показують, що розділ тепер перший; що одне спільне слово — не збіг; що десяткове число, дата, адреса чи ім'я файлу лишається одним словом; що слово, набране двічі, рахується раз; і що виключення після дужки й далі виключає. Два старі очікування змінюються свідомо: «на» саме по собі раніше збігалося майже з усім, а тепер ні з чим, і «помилка E-1042» тепер знаходить ще й англійську сторінку з цим кодом помилки.

У `tests/test_lexical_search.py` внесіть ці правки по черзі:

**1.** Знайдіть у `tests/test_lexical_search.py`:

```python
"""Checks on full-text search that need no database."""

from collections.abc import Awaitable, Callable
from typing import cast
```

Замініть на:

```python
"""Checks on full-text search that need no database."""
# ruff: noqa: RUF001

from collections.abc import Awaitable, Callable
from typing import cast
```

**2.** Знайдіть у `tests/test_lexical_search.py`:

```python
from knowledge_service.retrieval.lexical_routing import LexicalConfiguration
from knowledge_service.retrieval.lexical_search import (
    MAX_QUERY_CHARACTERS,
    InvalidLexicalQuery,
    english_search_statement,
    lexical_search_statement,
    search_english_chunks,
    search_lexical_chunks,
    search_ukrainian_chunks,
    ukrainian_search_statement,
)
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES
```

Замініть на:

```python
from knowledge_service.retrieval.lexical_routing import LexicalConfiguration
from knowledge_service.retrieval.lexical_search import (
    MAX_QUERY_CHARACTERS,
    MAX_SIMPLE_WORDS,
    UKRAINIAN_STOP_WORDS,
    InvalidLexicalQuery,
    english_search_statement,
    lexical_search_statement,
    search_english_chunks,
    search_lexical_chunks,
    search_ukrainian_chunks,
    simple_query,
    ukrainian_search_statement,
)
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES
```

**3.** Знайдіть у `tests/test_lexical_search.py`:

```python
        )


def test_both_configurations_share_one_bound_parameter() -> None:
    statement = lexical_search_statement(
        (LexicalConfiguration.ENGLISH, LexicalConfiguration.SIMPLE),
        text=f"{MARKER}' OR true --",
```

Замініть на:

```python
        )


def test_each_configuration_reads_the_text_as_a_bound_parameter() -> None:
    statement = lexical_search_statement(
        (LexicalConfiguration.ENGLISH, LexicalConfiguration.SIMPLE),
        text=f"{MARKER}' OR true --",
```

**4.** Знайдіть у `tests/test_lexical_search.py`:

```python
    assert " OR " in sql
    # A rank counts only where its own configuration matches.
    assert "greatest(CASE WHEN" in sql


def test_only_the_ranked_and_limited_rows_are_highlighted() -> None:
```

Замініть на:

```python
    assert " OR " in sql
    # A rank counts only where its own configuration matches.
    assert "greatest(CASE WHEN" in sql


@pytest.mark.parametrize(
    ("text", "as_typed"),
    [
        ("Що робити в перший день хвороби?", False),
        ("Де форма ІТ-0457?", False),
        ("Скільки п'ять днів?", False),
        ("ставка 1.5 з 01.09.2026", False),
        ("робота-відпочинок", False),
        ("Що це?", False),
        ('"в перший день" хвороби', True),
        ("-лікарняний відпустка", True),
        ("відпустка -в", True),
        ("відпустка (-лікарняний)", True),
        ("Відпустка - це що", True),
        ("VPN or в", True),
        ("VPN (or) в", True),
    ],
    ids=[
        "topic",
        "identifier",
        "apostrophe",
        "decimal-and-date",
        "hyphenated",
        "stop-words",
        "phrase",
        "exclude-first",
        "exclude",
        "exclude-after-bracket",
        "dash-before-a-space",
        "or",
        "or-in-brackets",
    ],
)
def test_simple_reads_the_topic_words_unless_the_text_uses_search_syntax(
    text: str, as_typed: bool
) -> None:
    simple = simple_query(text)

    assert simple.as_typed is as_typed
    # The Question is kept out of the repr.
    assert text not in repr(simple)


def test_the_topic_words_are_found_by_postgresql_from_bound_parameters() -> None:
    statement = ukrainian_search_statement(
        text=f"{MARKER}' першого дня", eligibility=sa.true(), limit=5
    )

    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert MARKER not in sql
    assert compiled.params["query_text"] == f"{MARKER}' першого дня"
    assert compiled.params["simple_stop_words"] == sorted(UKRAINIAN_STOP_WORDS)
    assert MAX_SIMPLE_WORDS in compiled.params.values()
    # The same parser as the stored terms, and the Chunk's own words only.
    assert "ts_debug('simple'::regconfig" in sql
    assert "tsvector_to_array(chunks.search_simple)" in sql


def test_text_with_search_syntax_is_read_as_typed() -> None:
    statement = ukrainian_search_statement(
        text="відпустка (-лікарняний)", eligibility=sa.true(), limit=5
    )

    sql = str(statement.compile(dialect=postgresql.dialect()))

    assert "websearch_to_tsquery('simple'::regconfig" in sql
    assert "ts_debug" not in sql


def test_only_the_ranked_and_limited_rows_are_highlighted() -> None:
```

У `tests/test_ukrainian_lexical_search_integration.py` внесіть ці правки по черзі:

**1.** Знайдіть у `tests/test_ukrainian_lexical_search_integration.py`:

```python
    SourceRecord,
)
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.retrieval.lexical_search import (
    InvalidLexicalQuery,
    LexicalCandidate,
```

Замініть на:

```python
    SourceRecord,
)
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.retrieval.evaluation_dataset import (
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
)
from knowledge_service.retrieval.lexical_search import (
    InvalidLexicalQuery,
    LexicalCandidate,
```

**2.** Знайдіть у `tests/test_ukrainian_lexical_search_integration.py`:

```python
    ("leave", ["leave.txt"], "any language matches in its exact form"),
    ("leaves", [], "miss: no English stemming either"),
    ("every", ["leave.txt"], "no stop words: English every is kept"),
    (
        "на",
        ["likarnyanyi.md", "vidpustka.txt"],
        "noise: no stop words, so на matches every Chunk with it",
    ),
]
```

Замініть на:

```python
    ("leave", ["leave.txt"], "any language matches in its exact form"),
    ("leaves", [], "miss: no English stemming either"),
    ("every", ["leave.txt"], "no stop words: English every is kept"),
    ("на", [], "stop word: dropped from the query, which is then empty"),
]
```

**3.** Знайдіть у `tests/test_ukrainian_lexical_search_integration.py`:

```python
    report = [(query, await names(runtime, query)) for query, _, _ in FIXTURE_REPORT]

    assert report == [(query, found) for query, found, _ in FIXTURE_REPORT]


@pytest.mark.integration
```

Замініть на:

```python
    report = [(query, await names(runtime, query)) for query, _, _ in FIXTURE_REPORT]

    assert report == [(query, found) for query, found, _ in FIXTURE_REPORT]


@pytest.mark.integration
async def test_a_question_finds_the_section_headed_by_its_topic_words(
    runtime: DatabaseRuntime,
) -> None:
    # The seed corpus page this miss was found on, cut to two parts.
    await a_document(
        runtime,
        "likarnianyi.md",
        title="Лікарняний",
        text=(DATASET_DIRECTORY / CORPUS_DIRECTORY / "public" / "likarnianyi.md")
        .read_text(encoding="utf-8")
        .split("## Оплата лікарняного")[0],
    )

    matches = await search(runtime, "Що робити в перший день хвороби?")

    # The section lacks "робити" and "в", and still ranks first:
    # its heading holds three topic words, the opening only its text.
    assert [match.heading_path for match in matches] == [
        "Лікарняний > Перший день хвороби",
        "Лікарняний",
    ]


@pytest.mark.integration
async def test_one_shared_topic_word_is_not_a_match(
    runtime: DatabaseRuntime,
) -> None:
    await a_document(
        runtime, "one.txt", title="Ноутбук", text="Ремонт триває п'ять днів."
    )
    await a_document(
        runtime, "two.txt", title="Відпустка", text="Скільки днів відпустки?"
    )

    found = await names(runtime, "Скільки днів щорічної відпустки?")

    assert found == ["two.txt"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("query", "found"),
    [
        ("відпустка ?", ["holiday.txt", "sick.txt"]),
        ("відпустка відпустка лікарняний", ["sick.txt"]),
        ("відпустка лікарняний", ["sick.txt"]),
        ("відпустка (-лікарняний)", ["holiday.txt"]),
    ],
    ids=["punctuation", "typed-twice", "two-words", "exclusion"],
)
async def test_each_topic_word_counts_once_and_an_exclusion_still_excludes(
    runtime: DatabaseRuntime, query: str, found: list[str]
) -> None:
    await a_document(
        runtime, "holiday.txt", title="Відпустка", text="Щорічна відпустка триває."
    )
    await a_document(
        runtime, "sick.txt", title="Лікарняний", text="Відпустка і лікарняний."
    )

    assert sorted(await names(runtime, query)) == sorted(found)


@pytest.mark.integration
@pytest.mark.parametrize(
    "query",
    [
        "ставка 1.5",
        "умови 01.09.2026",
        "hr@company.ua ставка",
        "умови form.pdf",
        "ставка ²",
        "Умови, УМОВИ?",
    ],
    ids=["decimal", "date", "address", "file", "no-word-symbol", "typed-twice"],
)
async def test_a_number_a_date_or_an_address_stays_one_word(
    runtime: DatabaseRuntime, query: str
) -> None:
    await a_document(
        runtime,
        "terms.txt",
        title="Умови",
        text="Ставка 1.5 діє з 01.09.2026; бланк form.pdf, питання на hr@company.ua.",
    )

    assert await names(runtime, query) == ["terms.txt"]


@pytest.mark.integration
```

У `tests/test_language_aware_lexical_search_integration.py` внесіть цю правку:

**1.** Знайдіть у `tests/test_language_aware_lexical_search_integration.py`:

```python
            {"leave.txt", "vidpustka.txt", "deploy.txt"},
        ),
        ("error E-1042", QuestionScript.LATIN, ENGLISH, {"leave.txt"}),
        (
            "помилка E-1042",
            QuestionScript.CYRILLIC,
            SIMPLE,
            {"vidpustka.txt", "deploy.txt"},
        ),
    ],
)
```

Замініть на:

```python
            {"leave.txt", "vidpustka.txt", "deploy.txt"},
        ),
        ("error E-1042", QuestionScript.LATIN, ENGLISH, {"leave.txt"}),
        # `simple` reads topic words, two of them; PostgreSQL splits
        # E-1042 into "e" and "-1042", so the English page with the code
        # holds two and is found too (M05-T16).
        (
            "помилка E-1042",
            QuestionScript.CYRILLIC,
            SIMPLE,
            {"vidpustka.txt", "deploy.txt", "leave.txt"},
        ),
    ],
)
```

`test_each_configuration_reads_the_text_as_a_bound_parameter` замінює старий тест одного параметра тим самим доказом: жоден текст не потрапляє в SQL.

Перевірка:

```bash
uv run pytest tests/test_lexical_search.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_ukrainian_lexical_search_integration.py tests/test_language_aware_lexical_search_integration.py -q
```

Очікувано: `68 passed` і `46 passed`.

### Крок 8 — Без коду: виміряйте кожен зріз, зламайте і вирішіть

Цей крок пов'язує ремонт із метриками по зрізах, показує, що зробив би очевидний ремонт, і записує рішення.

**Дія 1:** знову прогоніть seed-набір на свіжій базі:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --local-embeddings --output /tmp/m05-t16-after.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
diff /tmp/m05-t16-before.md /tmp/m05-t16-after.md
```

Очікувано, у першій таблиці:

| Зріз | lexical R@10 до | після | MRR злиття до | після |
|---|---:|---:|---:|---:|
| all | 0.312 | 0.500 | 0.969 | 1.000 |
| uk->uk | 0.200 | 0.600 | 0.900 | 1.000 |
| uk->en | 0.000 | 0.333 | 1.000 | 1.000 |
| uk->* | 0.125 | 0.500 | 0.938 | 1.000 |
| en->* | 0.500 | 0.500 | 1.000 | 1.000 |
| identifier | 0.250 | 0.750 | 1.000 | 1.000 |

Усі інші зрізи без змін. `uk-uk-sick-first-day` перший у таблицях випадків злиття і lexical, а пошук за словами тепер знаходить ще й `uk-uk-form-it-0457` і `uk-en-error-vpn-4031` за їхніми ідентифікаторами.

**Дія 2:** навмисно зламайте ремонт. У `src/knowledge_service/retrieval/lexical_search.py` встановіть `MIN_SIMPLE_WORDS = 1` — варіант «будь-яке одне слово». Запустіть тести:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_ukrainian_lexical_search_integration.py tests/test_language_aware_lexical_search_integration.py -q
```

Очікувано: 6 failed — звіт фікстури, `test_one_shared_topic_word_is_not_a_match`, випадки `typed-twice` і `two-words` тесту `test_each_topic_word_counts_once_and_an_exclusion_still_excludes` і обидва випадки «the who» тесту `test_a_question_of_english_stop_words_falls_back_to_simple`. Unit-тести й далі проходять, бо правило живе в SQL.

Потім виміряйте зламану версію, поки `MIN_SIMPLE_WORDS = 1` ще стоїть:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --local-embeddings --output /tmp/m05-t16-broken.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
```

Очікувано: лексичний R@10 для `uk->*` росте до 0.625, але MRR злиття падає: 0.511 на `uk->en`, 0.754 на `uk->*`, 0.877 на `all`. Відкотіть зміну, запускайте тести знову, доки все не пройде, і зупиніть сервер BGE-M3.

**Дія 3:** запишіть рішення. У розділі `## Decision` запишіть:

- числа до, після і для зламаної версії для зрізів вище, бо `/tmp` і `artifacts/` не зберігаються;
- чому ремонт прийнято: які зрізи зрушили і що жоден не впав;
- що зламана версія показує про оцінку гілки окремо, з ID випадків, що зрушили;
- щонайменше одне **спростоване припущення** у формі «Я вважав X. Прогін показує Y (ID випадків). Отже, Z хибне.»

### Крок 9 — Без коду: перевірки, діаграма, evidence і прогрес

Цей крок оновлює текст архітектури, доводить, що все й далі працює, і записує результат.

**Дія 1:** у `docs/architecture.md` внесіть ці правки. Друга додає діаграму гібридного пошуку; порівняйте її зі своєю з кроку 3 і запишіть у розділі `## Diagram`, що ви пропустили.

**1.** Знайдіть у `docs/architecture.md`:

```markdown
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
```

Замініть на:

```markdown
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
```

**2.** Знайдіть у `docs/architecture.md`:

````markdown
refuses to write into any space that is neither current nor the target of a
`dual_writing` migration.

### Answering

```mermaid
````

Замініть на:

````markdown
refuses to write into any space that is neither current nor the target of a
`dual_writing` migration.

#### Hybrid retrieval as built in M05

`retrieve_hybrid` asks two branches at once, under one deadline, and fuses
what they return. Each branch applies the reader's permission check in its
own SQL statement, so fusion only reorders eligible Chunks.

- **Words.** `search_lexical_chunks` routes each Question by its alphabet
  and stated language to `english` (stems, English stop words, every word
  must match), to `simple` (exact lowercased words), or to both. A
  Ukrainian Question read with `simple` keeps only its topic words, without
  Ukrainian stop words, and a Chunk must hold two of them. Without stemming,
  every word of a Ukrainian Question rarely appears in one Chunk.
- **Meaning.** `search_similar_chunks` embeds the Question with the current
  model and reads only the current Embedding Space; it finds the right
  Chunk across languages, where words cannot.
- **Fusion.** Reciprocal Rank Fusion adds `1 / (60 + rank)` for each branch
  that found a Chunk. Ranks near the top differ little, so a Chunk both
  branches found outranks one only a single branch found a place higher.
- **Off by default.** Reranking (M05-T11 to T13) and query rewriting
  (M05-T15) are measured, but no default search uses them.

```mermaid
sequenceDiagram
    participant Caller
    participant Hybrid as retrieve_hybrid
    participant Words as lexical branch
    participant Meaning as dense branch
    participant Model as EmbeddingModel
    participant DB as PostgreSQL
    Caller->>Hybrid: Question, Authorization Context, stated language, limit, timeout
    Hybrid->>Hybrid: refuse a bad Question, reader, language, limit, or timeout
    par one deadline for both branches
        Hybrid->>Words: Question, stated language
        Words->>Words: route: english, simple (topic words, any two), or both
        Words->>DB: one SELECT: words match AND an Access Grant, ORDER BY ts_rank, LIMIT
        DB-->>Words: eligible Chunks by words
    and
        Hybrid->>Meaning: Question
        Meaning->>Model: embed the Question
        Model-->>Meaning: query vector
        Meaning->>DB: one SELECT in the current Embedding Space AND an Access Grant, ORDER BY distance, LIMIT
        DB-->>Meaning: eligible Chunks by meaning
    end
    alt a branch failed or missed the deadline
        Hybrid->>Hybrid: leave it out and mark the result degraded
    end
    alt no branch answered
        Hybrid-->>Caller: HybridRetrievalUnavailable
    else at least one branch answered
        Hybrid->>Hybrid: fuse: sum of 1 / (60 + rank) per branch, ties by Chunk ID
        Hybrid-->>Caller: HybridRetrieval: up to limit fused Candidates and per-branch diagnostics
    end
```

### Answering

```mermaid
````

**Дія 2:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_lexical_search.py tests/test_evaluation_dataset.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний прогін дав 139 passed для вузьких тестів, `make test-unit` — 1676 passed, а `make test-integration` — 329 passed.

**Дія 3:** допишіть файл evidence:

- **Design:** змінені файли; чому зріз за мовою запитання; чому два слова теми, а не одне і не всі; чому службові слова відкидаються лише із запиту; чому синтаксис читається як набраний; що не змінилося (англійська, перевірка доступу, зв'язані параметри).
- **Verification:** команди й лічильники, три виміри, навмисна поломка і відомі обмеження з розділу «Перед початком».
- **Review:** три адверсаріальні рев'ю з розділу «Перед початком»: слово лише з розділових знаків, слово, набране двічі, і `(-лікарняний)` після дужки; потім слова, поділені інакше, ніж у PostgreSQL, через що десяткові числа, дати, адреси й імена файлів нічого не знаходили; потім слова теми, що шукалися заново для кожного рядка. Усе виправлено, для кожного є тест, що падав до виправлення, а час виміряно до і після.
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу виконаною, додайте рядок M05-T16 у `docs/curriculum/PROGRESS.md`, встановіть наступною задачею першу задачу M06 і зробіть коміт із повідомленням `m05-t16: pass the hybrid retrieval gate`.

## Запитання для рефлексії

1. Що `search_lexical_chunks` ховає від `retrieve_hybrid`? Перелічіть, чого гібридному коду ніколи не треба знати про українську, англійську чи синтаксис пошуку.
2. Пізніша зміна хоче швидшого українського пошуку і тому прибирає підзапит, що рахує слова, лишаючи тільки збіг «or». Яку гарантію з вашого рішення це зламало б і який тест та який зріз це показали б?
3. Ремонт обрано, дивлячись на 16 випадків. Який один український випадок ви б додали, щоб спробувати спростувати правило двох слів, і який результат змусив би вас його змінити?
4. Яке з ваших передбачень виявилося хибним і що ви думали про пошук за словами чи злиття, що змусило вас так передбачити?
5. MRR злиття тепер 1.000 на кожному зрізі. Чого це число не каже про те, наскільки добрий цей пошук, і що б ви виміряли далі?

## Чекліст завершення

- [ ] Файл evidence містить інваріант і ваші передбачення, записані до будь-якого запуску.
- [ ] Ваші пояснення мовних збоїв і злиття посилаються на код і містять арифметику RRF.
- [ ] Ваша діаграма з пам'яті є в evidence, і записано, що вона пропустила.
- [ ] `score_slices` має `en->*` і `uk->*` із тестом, а ваш власний план зрізу є в evidence.
- [ ] Діагностика простежує `uk-uk-sick-first-day` через звіт і PostgreSQL до однієї причини.
- [ ] `simple` читає слова теми, знайдені PostgreSQL, вимагає двох і читає синтаксис пошуку як набраний; англійська без змін.
- [ ] Нові тести проходять і падають із `MIN_SIMPLE_WORDS = 1`.
- [ ] Виміри до, після і зламаної версії є в evidence, по зрізах, і жоден зріз не впав.
- [ ] `docs/architecture.md` має діаграму гібридного пошуку.
- [ ] Усі перевірки проходять: форматування, лінт, типи, unit, integration, документація.
- [ ] Evidence, статус задачі, рядок у PROGRESS і коміт готові.

Коли напишете файли, поділіться ними чи виводом помилок, і я перевірю їх за цим чеклістом.
