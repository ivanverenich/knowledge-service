Англійська версія: [M07-T01-write-the-system-threat-model.md](M07-T01-write-the-system-threat-model.md)

# M07-T01 — Напишіть модель загроз системи

Задача: [M07-T01 — Write the system threat model](../curriculum/milestones/M07-identity-and-authorization/M07-T01-write-the-system-threat-model.md)

## Що ви зробите

Цей урок відкриває модуль про ідентичність і авторизацію. Ви запишете, що сервіс має захищати і від кого. Спершу напишете модель загроз для всієї системи, а потім змусите перевірку документації доводити одну річ: кожне правило безпеки має засіб захисту і тест, що за нього відповідає.

- **Модель загроз** (threat model) перелічує цінні речі (активи), тих, хто може на них напасти (актори), межі довіри (зони довіри) і місця, куди входять запити й дані (точки входу). Для інгестії, пошуку, генерації, агентів і експлуатації вона описує, як систему можна зловжити і що цьому заважає.
- **Трасування** (trace) — одна таблиця в кінці: кожен із семи інваріантів у `docs/security.md` отримує рядок із засобом захисту і власниками-тестами.
- **Перевірка** змушує `make docs-check` падати, коли в інваріанта немає рядка, названого тесту більше немає або названу майбутню задачу завершили, не вписавши її тест. Це та вузька спостережувана можливість, яку задача просить лишити наступній задачі.

| Задача просить | Де це відбувається |
|---|---|
| Активи, актори, зони довіри, точки входу, сценарії зловживань, захист для інгестії, пошуку, генерації, агентів, експлуатації | Крок 3: `docs/threat-model.md` |
| Кожен інваріант безпеки простежено до засобу захисту і власника-тесту | Крок 3: таблиця `## Invariant trace`; крок 4: перевірка |
| Тести: успіх, найризикованіший край, типізована відмова | Крок 5: `tests/test_threat_model.py` |
| Оновити лише те, що зміна зробила неточним | Крок 3: одне речення в `docs/security.md`, що вказує на модель |
| Авторизація до того, як дані залишать сховище; жодного підкинутого тексту в телеметрії | Крок 2: ви перевіряєте, де це виконується сьогодні, і записуєте в трасування |

Цей урок містить код: кроки 4 і 5 пишуть код. Кроки 1, 2, 3, 6 і 7 — ні, хоча крок 3 пише саму модель загроз.

Терміни цього уроку:

- **Модель загроз:** письмова відповідь на «що може піти не так і що ми з цим робимо» для однієї системи.
- **Актив** (asset): те, що варто захищати, наприклад текст Документа чи API-ключ.
- **Зона довіри** (trust zone): частина системи, де все має однаковий рівень довіри, як приватна мережа навколо бази даних. Дані, що переходять із зони в зону, треба перевіряти.
- **Точка входу** (entry point): місце, де зовнішні дані потрапляють у систему: HTTP-маршрут, Джерело, відповідь моделі.
- **Сценарій зловживання** (abuse case): конкретний спосіб зловжити системою, записаний як user story для нападника: «Документ ховає інструкції, що керують моделлю».
- **Засіб захисту** (control): механізм, що зупиняє сценарій зловживання, наприклад перевірка дозволів усередині кожного пошукового запиту.
- **Власник-тест** (test owner): тест, що доводить роботу засобу захисту, або майбутня задача, яка його додасть.

## Перш ніж почати

Цей розділ пояснює рішення, на яких стоїть робота, щоб кожен крок мав сенс, коли ви до нього дійдете.

- **Що вже є.** `docs/security.md` перелічує сім інваріантів, діаграму зон довіри і таблицю загроз із потрібними доказами. M02–M06 збудували більшість засобів захисту: перевірку дозволів усередині кожного запиту, tombstone, непрозорі ID доказів, Citations на сервері, History, що приховує відкликані відповіді, і записи логів без тексту. Бракувало одного місця, яке каже, який засіб і який тест стоять за кожним інваріантом.
- **Чому перевірка, а не лише документ.** Модель загроз застаріває наступного дня після написання. Тест перейменовують, заплановану задачу завершують, а таблицю ніхто не оновлює. Перевірка перетворює трасування на те, що забезпечують `make docs-check` і unit-тести.
- **Що вважається власником-тестом.** Або тест, який збирає pytest, записаний `tests/<file>.py::<test>` чи `tests/<file>.py::<Class>::<test>`, або ID ще не завершеної задачі. Коли цю задачу завершать, перевірка падатиме, доки її рядок не назве тест, який задача додала. Так трасування встигає за M07.
- **Рахується лише те, що бачить читач.** Рядок, схований в HTML-коментарі, у fenced-блоці коду чи в другій таблиці нижче, нічого не трасує. Інваріант треба писати як `N. text` з нумерацією від 1, тож нове правило в іншій формі списку буде повідомлене, а не тихо пропущене.
- **Де живе перевірка.** У `src/knowledge_service/documentation.py`, поруч із перевірками посилань і задач, як `validate_threat_model(root)`. `validate_repository` запускає її, коли існує `docs/security.md`, тож старі тести валідатора, які будують маленькі тимчасові репозиторії без нього, не зачеплено.
- **Що знайшло ревʼю.** Незалежне адверсарне ревʼю звірило кожне твердження моделі з кодом і атакувало перевірку ворожим Markdown і тестовими файлами. Кожен названий тест і кожна задача існували, а маршрути, правило «ніхто не може прочитати» і приховані налаштування описано правильно. Знайдено такі проблеми; кожну виправлено, і там, де помилялася перевірка, є тест, що падає на першій версії:
  - Модель подавала `confirm_citations` як чинний засіб захисту, але в коді його ще ніхто не викликає. Тепер це прогалина, яку закриває M07-T09.
  - `POST /v1/answer` описувався як шлях із доказами, але це скелет M01, що надсилає запитання просто моделі. Тепер він має власну точку входу, рядки зловживань і відому прогалину.
  - Модель казала, що кожен парсер прибирає приховані символи, але це робить лише PDF-парсер. Тепер написано, що їх екранує промпт.
  - Інваріант 2 називав бенчмарк-тест, який не перевіряє робочий запит. Тепер він називає `test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`.
  - Інваріант у формі `8)`, `8.` з двома пробілами чи як маркер списку проходив без рядка трасування. Тепер будь-який пункт списку в іншій формі повідомляється.
  - Другий заголовок `## Invariants` і рядки в коментарі, блоці коду чи другій таблиці тихо рахувалися або ігнорувалися. Тепер дубль заголовка повідомляється, а рахується лише перша видима таблиця.
  - «Тест», що був лише текстом, мертвим кодом, вкладеною функцією, методом класу, який pytest не збирає, або у файлі, який pytest не збирає, проходив. Тепер тестовий файл розбирається як Python, і рахуються лише тести верхнього рівня та методи класів `Test*`.
  - Тестовий файл не в UTF-8 чи з невалідним Python ламав перевірку. Тепер це повідомляється.
  - Засіб захисту `TBD` чи `-` проходив. Тепер ні.
  - Задокументовано, не виправлено: тест, позначений як пропущений, усе одно рахується, а CI запускає лише unit-тести, тож інтеграційні власники запускаються локально.
- **Відомі обмеження.** Перевірка доводить, що власник існує, а не що він перевіряє інваріант; це досі ваше судження і судження ревʼюера. Модель загроз описує код після M06; задачі M07 змінять багато рядків.

## Покроково

### Крок 1 — Без коду: запишіть інваріант і передбачення

Перш ніж щось змінювати, запишіть, що має бути правдою наприкінці уроку. Крок 6 перевірить передбачення.

Створіть `docs/evidence/M07-T01-system-threat-model.md`:

```markdown
# M07-T01 — System threat model evidence

## Invariant

`docs/threat-model.md` names the system's assets, actors, trust zones,
entry points, and abuse cases with their mitigations for ingestion,
retrieval, generation, agents, and operations. Every numbered invariant in
`docs/security.md` has exactly one row in its trace, with a control and at
least one test owner: a test pytest collects, or a task not yet completed.
`make docs-check` fails when that stops being true.

## Expected failing check

Before the change, `uv run python -c "from knowledge_service.documentation import validate_threat_model"`
fails with `ImportError`.

## Predictions

- Which security invariant has no test in the code today, and why: ...
- Which answer endpoint does not use retrieval at all: ...
- What `make docs-check` prints if a test named in the trace is renamed: ...
- How many tests in `tests/test_threat_model.py` fail if the check stops reporting an invariant with no trace row: ...

## Inventory

## Design

## Verification

## Review

## Decision

## Reflection
```

Перевірка: файл існує, перевірка падає, і кожне передбачення — повне речення, яке крок 6 може спростувати.

### Крок 2 — Без коду: знайдіть засіб захисту і тест для кожного інваріанта

Цей крок — дослідження перед моделлю загроз. Для кожного інваріанта знайдіть код, що його забезпечує, і тест, що це доводить, ще до того, як напишете хоч слово моделі.

**Дія 1:** для кожного з семи інваріантів у `docs/security.md` знайдіть засіб захисту і тест. Добрий початок — ці пошуки:

```bash
grep -rn "eligible_chunks_predicate" src | head
grep -rn "def test_.*may_not_see" tests
grep -rn "def test_.*revoked\|def test_.*tombstoned" tests
grep -rn "def test_.*injection\|def test_.*cannot_choose" tests
grep -rn "def test_.*no_text\|def test_.*holds_no_text" tests
grep -rn "confirm_citations" src
grep -n "input=" src/knowledge_service/openai_model.py
```

**Дія 2:** під `## Inventory` у файлі evidence напишіть по рядку на інваріант: засіб захисту, один тест і чи запускається тест у CI (тести `*_integration.py` потребують бази й запускаються через `make test-integration`). Позначте інваріант, для якого коду ще немає, і назвіть задачу, що його додасть.

Перевірка: для кожного інваріанта ви можете сказати, чи авторизація відбувається до того, як дані залишать сховище, і чи якийсь тест підкидає текст, що не має потрапити в лог. Очікувані сюрпризи: `confirm_citations` ще ніхто не викликає, а `POST /v1/answer` надсилає запитання просто моделі.

### Крок 3 — Без коду: напишіть модель загроз

Цей крок пише сам документ. Спершу складіть чернетку зі свого переліку, потім порівняйте з еталоном нижче і залиште те, що точніше.

**Дія 1:** створіть `docs/threat-model.md`. У ньому, саме в такому порядку:

- **Assets**, **Actors**, **Trust zones** і **Entry points**, кожен — коротка таблиця чи список;
- **Abuse cases and mitigations**: по таблиці для інгестії, пошуку, генерації, агентів і експлуатації, з колонками «Mitigation now» і «Still to come» (задача, що відповідає за решту);
- **Invariant trace**: колонки `Invariant`, `Control`, `Test owners`, по рядку на інваріант, кожен власник у бектиках;
- **Known gaps**.

Еталон, щоб порівняти з чернеткою або взяти як файл:

Створіть `docs/threat-model.md`:

```markdown
# Threat model

This model names what the service must protect, who may attack it, where
they can reach it, and what stops them. It covers ingestion, retrieval,
generation, agents, and operations as the code stands after M06, and names
the task that owns each control still to come. The invariants it protects
are the ones in the security model, `docs/security.md`; the trace at the
end is checked by `make docs-check`.

## Assets

| Asset | Why it matters | Where it lives |
|---|---|---|
| Document text and Chunks | Organizational knowledge; most of it is not for every reader | PostgreSQL `documents`, `chunks`; Evidence in a prompt |
| Access Grants and Authorization Versions | Decide who may read what; a wrong grant is a leak | PostgreSQL `access_grants` |
| Authorization Context | The verified User and Groups of one request | In memory, per request |
| Questions, Answers, Conversations | Reveal what a User asked and what they were told | PostgreSQL `conversations`, `messages`; the model provider's request |
| Citations and Evidence IDs | Tell the User where a claim came from; a fake one launders a claim | Built on the server per request |
| Evaluation Datasets and Runs | Hold Questions, expected Documents, and Authorization Contexts | `datasets/`, `artifacts/` |
| Credentials | Database URL, provider API keys, later OIDC client secrets | Environment and settings, redacted in repr |
| Telemetry | Logs and, later, traces; a copy of everything they hold | Log records today |
| Availability and budget | Every Answer costs model tokens | Model providers, the database pool |

## Actors

| Actor | Trust | What they may try |
|---|---|---|
| User | Authenticated, reads only granted Documents | Read Documents not granted to them; learn that one exists; spend others' budget |
| Content manager, Evaluator, Administrator | Application Roles; never imply Document access | Use a role to read content the role does not grant |
| Document author | Writes into a Source; never trusted | Plant instructions, fake citations, invisible characters, or huge files in a Document |
| Model provider and its replies | Approved, but replies are untrusted data | Return invented Citations, unsupported claims, or malformed JSON; keep what it was sent |
| Unauthenticated client | None | Call the API without an identity; flood it |
| Operator | Reads logs and dashboards | See Question, Evidence, or Answer text in telemetry |
| Supply-chain attacker | None | Ship a hostile dependency or image |

## Trust zones

The zones are the ones drawn under "Trust zones" in `docs/security.md`. The boundaries that matter here:

- **Public to edge.** Everything from the browser is untrusted until the edge and the API have verified a token (M07-T03).
- **API to data.** PostgreSQL decides eligibility inside every search; a Chunk the reader may not see never leaves it.
- **API to model providers.** Evidence and the Question leave the private zone here; only eligible Evidence under opaque IDs may cross, and replies come back as untrusted data.
- **Workers to Sources.** Source content enters here and stays untrusted data for its whole life.
- **Local model servers.** BGE-M3 and the reranker listen on 127.0.0.1 only and must name the pinned revision.

## Entry points

| Entry point | Who reaches it | Today |
|---|---|---|
| `POST /v1/answer/stream` | Any client that reaches the API | The grounded path: Evidence, checked Citations, and the abstention policy. No token check yet; the reader is fixed when the Evidence source is built (M07-T03, M07-T04) |
| `POST /v1/answer` | Any client that reaches the API | The M01 walking skeleton: sends the Question straight to the model, with no retrieval, Evidence, Citations, or output limit, and without asking the provider not to keep it. The versioned Question route replaces it (M09-T01) |
| `GET /health/live`, `GET /health/ready` | Load balancers | Return status only |
| Source adapters | Workers | The local directory Source; Confluence in M08 |
| Model replies | The API | Answer, rewrite, ranking, and support replies, each checked on the server |
| Jobs | Workers | Stored jobs with typed errors; the Redis queue in M08 |
| Scripts | Developers | Retrieval and rewrite evaluations over `datasets/` |
| OpenAI-compatible adapter, Open WebUI | Users | M09 |
| MCP server and agent tools | Agents | M11 |

## Abuse cases and mitigations

### Ingestion

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A Source reports no readers, and the Document becomes public by mistake | Only an item the Source marks public gets a public grant; any other item is readable by nobody until its principals are mapped | Source group normalization (M07-T06) |
| A permission is removed, but the old Chunks stay searchable until cleanup | Grants and Chunks change in one transaction; a tombstone makes a Document unavailable at once | Priority revocation sync (M07-T10) |
| A file removed or unreadable deletes or keeps the wrong Documents | An unreadable folder is unavailable, not deleted; a failed pass tombstones nothing | Incremental sync (M08-T10) |
| A Document hides control or invisible characters to steer the model | The PDF parser removes them; the text, Markdown, and HTML parsers keep them, and the prompt escapes every one | Retrieved-instruction hardening (M07-T13) |
| A huge, deeply nested, or compressed file exhausts a worker | Parsers refuse text over their character limit, HTML nested too deep, and PDF pages past their size limit, with typed errors | None |
| A replayed job writes twice | Jobs are stored with typed errors and bounded retries | Job idempotency (M08-T03) |

### Retrieval

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A highly relevant Chunk the reader may not see is ranked | `eligible_chunks_predicate` sits inside every lexical and vector query, beside ORDER BY and LIMIT | Adversarial suite (M07-T15) |
| Filtering after the index returns too few Chunks, so someone moves the filter out | A benchmark shows the loss; the predicate stays in the query | ACLs in vector and lexical search (M07-T07, M07-T08) |
| A reranker or a ranking model is shown text the reader may not see | Only eligible Candidates reach any reranker | None |
| A Question proves a Document exists by the way it fails | Empty, unauthorized, and withdrawn read the same | Existence leaks (M07-T11) |
| A cache shared across readers serves one reader's Candidates or Answer to another | No cache of Candidates, Evidence, or Answers exists; Redis is for ephemeral coordination only (ADR 0011) | Adversarial suite covers caches (M07-T15) |

### Generation

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| Retrieved text tells the model to ignore the policy or reveal other Documents | The policy is fixed; the Question and Evidence are one escaped JSON document; only eligible Evidence is in the prompt | Retrieved-instruction hardening (M07-T13) |
| The model invents a Citation or cites another request's Evidence | Opaque per-request Evidence IDs; `resolve_citations` refuses any other reference, the reply is `invalid`, and the abstention replaces any text already streamed | None |
| An Answer cites a Chunk revoked after retrieval | None in any served path yet: `confirm_citations` exists and is tested, but nothing calls it | Citation reads with current grants (M07-T09) |
| An old Answer in History repeats revoked content | History keeps an Answer only while everything it cited is current and visible | None |
| A follow-up rewrite or History carries another User's text into a prompt | History is read only from the reader's own Conversation; the rewriter gets only that History's earlier Questions, never an Answer | None |
| The provider keeps Questions and Evidence | The grounded path asks it not to (`store=False`); `POST /v1/answer` does not | Versioned Question route (M09-T01) |
| A Question floods the model to run up cost | On the grounded path, context and token budgets and one deadline per stream; `POST /v1/answer` sets no output limit | Identity-aware rate limits (M09-T10) |

### Agents

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A tool call runs with more access than the User who started it | No agents or tools exist; the default path answers without them (ADR 0007) | Read-only tools carrying the Authorization Context (M11-T01), MCP server re-authorizing every call (M11-T06) |
| Retrieved text makes an agent call a tool | None needed yet | Agent prompt-injection tests (M11-T14) |

### Operations

| Abuse case | Mitigation now | Still to come |
|---|---|---|
| A client with no identity reads or spends | Nothing yet: neither answer endpoint checks a token | Token validation and the Authorization Context (M07-T03, M07-T04) |
| Logs or traces hold Question, Evidence, or Answer text | Log records hold counts, names, and outcomes; marker tests prove no planted text reaches them | Redacted logs and traces (M07-T12) |
| A credential appears in a repr or a log | Settings redact the database URL and API keys | Configuration separate from secrets (M12-T05) |
| An evaluation artifact leaks private content | `artifacts/private` and `artifacts/raw` are ignored; reports name no text | Durable audit events (M07-T14) |
| A hostile dependency or image ships | Locked dependencies | Image scanning and SBOM (M12-T09) |

## Invariant trace

Each row traces one invariant under "Invariants" in `docs/security.md` to its control and to at least one test owner: a test pytest collects, written `tests/<file>.py::<test>`, or a task not yet completed that will add one. `make docs-check` fails when an invariant has no row, a test owner no longer exists, or a named task is already completed. Owners in `*_integration.py` files run under `make test-integration`, which CI does not run yet.

| Invariant | Control | Test owners |
|---|---|---|
| 1 | `eligible_chunks_predicate`: the Document is available and a grant names everyone, the User, or one of their Groups | `tests/test_vector_search_integration.py::test_only_a_matching_access_grant_makes_a_chunk_eligible`, `tests/test_vector_search_integration.py::test_a_reader_without_groups_sees_only_public_and_own_grants`, `M07-T04` |
| 2 | The predicate inside every lexical and vector query, beside ORDER BY and LIMIT | `tests/test_lexical_search_integration.py::test_a_better_match_the_reader_may_not_see_is_never_returned`, `tests/test_vector_search_integration.py::test_a_nearer_chunk_the_reader_may_not_see_is_never_ranked`, `M07-T07`, `M07-T08` |
| 3 | Grants, Chunks, and availability written in one transaction; a tombstone makes a Document unavailable at once | `tests/test_vector_search_integration.py::test_revoked_and_tombstoned_documents_disappear_at_once`, `M07-T10` |
| 4 | Fixed policy; Question and Evidence as escaped JSON; Citations only from this request's Evidence IDs | `tests/test_prompt.py::test_the_input_reads_back_as_exactly_the_question_and_evidence`, `tests/test_prompt.py::test_no_hidden_or_control_character_reaches_the_prompt_unescaped`, `tests/test_citations.py::test_a_passage_cannot_choose_its_own_citation`, `tests/test_llm_reranking.py::test_an_obeyed_injection_can_only_reorder_eligible_candidates`, `M07-T13` |
| 5 | Log records hold counts, names, and outcomes only | `tests/test_streaming.py::test_the_stream_record_holds_no_text`, `tests/test_generation.py::test_no_question_document_or_answer_text_reaches_logs_or_reprs`, `M07-T12` |
| 6 | No agent or tool exists yet; the default path answers without one | `M11-T01`, `M11-T06`, `M11-T14` |
| 7 | Citations only from this request's eligible Evidence; one message for empty, unauthorized, and withdrawn; History withholds what the reader may no longer see | `tests/test_citations.py::test_an_id_from_another_request_is_unknown_here`, `tests/test_abstention.py::test_empty_unauthorized_and_withdrawn_tell_the_user_the_same_thing`, `tests/test_history_integration.py::test_another_users_conversation_has_no_history_for_the_reader`, `M07-T09`, `M07-T11`, `M07-T15` |

## Known gaps

- The API checks no token yet; until M07-T03 and M07-T04 the reader is whoever the Evidence source was built for.
- `POST /v1/answer` is the ungrounded M01 path, kept until M09-T01 replaces it.
- `confirm_citations` is not called in any served path; M07-T09 wires it.
- Telemetry is log records only; there are no traces and no audit events until M07-T12 and M07-T14.
- There are no rate limits until M09-T10.
- CI runs the unit tests only, so the integration test owners and `make docs-check` run locally; the trace itself is also checked by a unit test.
```

**Дія 2:** вкажіть на модель із моделі безпеки.

У `docs/security.md`:

Знайдіть:

```markdown
# Security model

## Invariants

1. A Chunk is eligible for retrieval only when its Document is currently available and at least one normalized Access Grant matches the verified Authorization Context.
2. Authorization occurs inside lexical and vector queries. Post-retrieval filtering is defense in depth, never the primary control.
3. Restrictive permission changes make content unavailable before background cleanup completes.
```

Замініть на:

```markdown
# Security model

The threat model, `docs/threat-model.md`, names the assets, actors, entry
points, and abuse cases behind these rules, and traces each invariant to a
control and a test owner.

## Invariants

1. A Chunk is eligible for retrieval only when its Document is currently available and at least one normalized Access Grant matches the verified Authorization Context.
2. Authorization occurs inside lexical and vector queries. Post-retrieval filtering is defense in depth, never the primary control.
3. Restrictive permission changes make content unavailable before background cleanup completes.
```

Перевірка: кожен ID задачі в моделі існує, кожен рядок зловживання каже, що зупиняє його сьогодні, або «nothing yet», і жоден рядок не приписує коду засіб захисту, якого там немає.

### Крок 4 — Крок із кодом: перевірка документації трасує кожен інваріант

Цей крок додає перевірку, яка тримає трасування чесним.

**Простими словами:** Перевірка документації тепер читає пронумеровані правила моделі безпеки і таблицю трасування в моделі загроз та порівнює їх. Кожне правило потребує рівно одного рядка зі справжнім засобом захисту і хоча б одним власником. Власник — це або тест, який справді існує (перевірка знаходить його, читаючи тестовий файл як Python), або майбутня задача, ще не завершена. Те, чого читач не побачить, наприклад закоментований рядок, не рахується, а зламаний файл повідомляється замість того, щоб обвалити перевірку.

| Частина | Що робить |
|---|---|
| `_visible_lines` | Відкидає HTML-коментарі та fenced-блоки коду, тож перевіряється лише те, що бачить читач |
| `_sections` | Рядки під заголовком до наступного заголовка рівня 1 чи 2; знаходить кожну копію заголовка, тож дубль повідомляється |
| `_invariants` | Номери пунктів `N. text`; будь-який інший пункт списку — проблема, і номери мають іти від 1 по порядку |
| `_first_table` | Рядки даних першої таблиці в розділі трасування |
| `_defined_tests` | Тести, які pytest зібрав би з модуля, за його синтаксичним деревом: функції верхнього рівня і методи класів `Test*` |
| `_Owners` | Перевіряє кожного власника: задача має існувати й не бути завершеною; тест має бути в читабельному, розбірному файлі `tests/.../test_*.py` |
| `validate_threat_model` | Збирає все разом і повертає `ValidationIssue`, ніколи не кидає виняток через поганий Markdown чи тестовий файл |

У `src/knowledge_service/documentation.py`:

**1.** Знайдіть:

```python
"""Validation of repository Markdown links and curriculum metadata."""

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

TASK_FIELDS = {
```

Замініть на:

```python
"""Validation of repository Markdown links, curriculum metadata, and the
threat model's trace of every security invariant."""

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

TASK_FIELDS = {
```

**2.** Знайдіть:

```python
    "status",
}
TASK_PATTERN = re.compile(r"M\d{2}-T\d{2}-[^/]+\.md$")
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
TASK_ID_PATTERN = re.compile(r"M\d{2}-T\d{2}")
VALID_STATUSES = {"planned", "in_progress", "completed", "blocked"}


@dataclass(frozen=True)
class ValidationIssue:
    """One documentation validation failure."""

```

Замініть на:

```python
    "status",
}
TASK_PATTERN = re.compile(r"M\d{2}-T\d{2}-[^/]+\.md$")
LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
TASK_ID_PATTERN = re.compile(r"M\d{2}-T\d{2}")
VALID_STATUSES = {"planned", "in_progress", "completed", "blocked"}
SECURITY_MODEL = Path("docs/security.md")
THREAT_MODEL = Path("docs/threat-model.md")
INVARIANTS_HEADING = "## Invariants"
TRACE_HEADING = "## Invariant trace"
INVARIANT_PATTERN = re.compile(r"(\d+)\. \S")
LIST_ITEM_PATTERN = re.compile(r"\s*(?:\d+[.)]|[-*+])\s")
HIDDEN_PATTERN = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
OWNER_PATTERN = re.compile(r"`([^`]*)`")
TEST_OWNER_PATTERN = re.compile(
    r"(tests/(?:[A-Za-z0-9_]+/)*test_[A-Za-z0-9_]+\.py)"
    r"::((?:Test[A-Za-z0-9_]*::)?test_[A-Za-z0-9_]+)"
)
PLACEHOLDERS = {"-", "?", "tbd", "todo", "n/a", "none"}


@dataclass(frozen=True)
class ValidationIssue:
    """One documentation validation failure."""

```

**3.** Знайдіть:

```python
        return None
    if not (source.parent / path).resolve().exists():
        return ValidationIssue(source, f"broken link: {target}")
    return None


def validate_repository(root: Path) -> list[ValidationIssue]:
    """Return all broken links, malformed task metadata, and dependencies."""
    issues: list[ValidationIssue] = []
    task_files = sorted(
        path
        for path in (root / "docs/curriculum/milestones").rglob("*.md")
        if TASK_PATTERN.fullmatch(path.name)
    )
```

Замініть на:

````python
        return None
    if not (source.parent / path).resolve().exists():
        return ValidationIssue(source, f"broken link: {target}")
    return None


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError, UnicodeDecodeError:
        return None


def _visible_lines(text: str) -> list[str]:
    """The lines a reader sees: no HTML comments and no fenced code blocks."""
    lines: list[str] = []
    fence: str | None = None
    for line in HIDDEN_PATTERN.sub("", text).splitlines():
        marker = line.lstrip()[:3]
        if fence is None and marker in ("```", "~~~"):
            fence = marker
        elif fence is not None:
            if marker == fence:
                fence = None
        else:
            lines.append(line)
    return lines


def _sections(lines: list[str], heading: str) -> list[list[str]]:
    """The lines under each `heading`, up to the next heading of level 1 or 2."""
    sections: list[list[str]] = []
    for start, line in enumerate(lines):
        if line.rstrip() != heading:
            continue
        end = next(
            (
                index
                for index in range(start + 1, len(lines))
                if lines[index].startswith(("# ", "## "))
            ),
            len(lines),
        )
        sections.append(lines[start + 1 : end])
    return sections


def _invariants(
    source: Path, lines: list[str]
) -> tuple[list[str], list[ValidationIssue]]:
    numbers: list[str] = []
    issues: list[ValidationIssue] = []
    for line in lines:
        if (match := INVARIANT_PATTERN.match(line)) is not None:
            numbers.append(match[1])
        elif LIST_ITEM_PATTERN.match(line):
            issues.append(
                ValidationIssue(
                    source, f"an invariant must be written as `N. text`: {line.strip()}"
                )
            )
    if numbers and numbers != [str(number) for number in range(1, len(numbers) + 1)]:
        issues.append(
            ValidationIssue(source, "invariants must be numbered 1, 2, 3, ... in order")
        )
    return numbers, issues


def _first_table(lines: list[str]) -> list[str]:
    """The data rows of the first table, without its header and the line under it."""
    table: list[str] = []
    for line in lines:
        if line.startswith("|"):
            table.append(line.strip())
        elif table:
            break
    return table[2:]


def _defined_tests(text: str) -> set[str] | None:
    """The tests pytest would collect from a module: `test_x` or `TestX::test_x`."""
    try:
        tree = ast.parse(text)
    except SyntaxError, ValueError:
        return None
    functions = (ast.FunctionDef, ast.AsyncFunctionDef)
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, functions):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            names.update(
                f"{node.name}::{item.name}"
                for item in node.body
                if isinstance(item, functions)
            )
    return names


def _task_statuses(root: Path) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for path in (root / "docs/curriculum/milestones").rglob("*.md"):
        if TASK_PATTERN.fullmatch(path.name):
            values, _ = _front_matter(path, _text(path) or "")
            if "id" in values:
                statuses[values["id"]] = values.get("status", "")
    return statuses


class _Owners:
    """Checks test owners, reading and parsing each test file once."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._statuses = _task_statuses(root)
        self._tests: dict[str, set[str] | str] = {}

    def problem(self, owner: str) -> str | None:
        if TASK_ID_PATTERN.fullmatch(owner):
            if owner not in self._statuses:
                return f"unknown task: {owner}"
            if self._statuses[owner] == "completed":
                return f"{owner} is completed; name the test it added"
            return None
        test = TEST_OWNER_PATTERN.fullmatch(owner)
        if test is None:
            return f"an owner must be a test or a task: {owner}"
        defined = self._defined(test[1])
        if isinstance(defined, str):
            return defined
        if test[2] not in defined:
            return f"no such test: {owner}"
        return None

    def _defined(self, file: str) -> set[str] | str:
        if file not in self._tests:
            path = self._root / file
            text = _text(path) if path.is_file() else None
            if text is None:
                self._tests[file] = f"no readable test file: {file}"
            else:
                defined = _defined_tests(text)
                self._tests[file] = (
                    f"cannot parse test file: {file}" if defined is None else defined
                )
        return self._tests[file]


def validate_threat_model(root: Path) -> list[ValidationIssue]:
    """Check that the threat model traces every security invariant.

    Each invariant under "## Invariants" in `docs/security.md`, written
    `N. text` and numbered from 1, must have exactly one row in the first
    table under "## Invariant trace" in `docs/threat-model.md`, naming a
    control and at least one test owner: a test pytest would collect,
    written `tests/<file>.py::<test>` or `tests/<file>.py::<Class>::<test>`,
    or a task that is not yet completed and will add one. Only what a reader
    sees counts: HTML comments and fenced code blocks are skipped.
    """
    security, threat = root / SECURITY_MODEL, root / THREAT_MODEL
    security_text = _text(security)
    if security_text is None:
        return [ValidationIssue(security, "cannot read the security model")]
    found = _sections(_visible_lines(security_text), INVARIANTS_HEADING)
    if len(found) > 1:
        return [ValidationIssue(security, f"{INVARIANTS_HEADING} appears twice")]
    numbers, issues = _invariants(security, found[0] if found else [])
    if not numbers:
        return [*issues, ValidationIssue(security, "no numbered invariants to trace")]
    threat_text = _text(threat) if threat.is_file() else None
    if threat_text is None:
        return [*issues, ValidationIssue(threat, "missing threat model")]
    traces = _sections(_visible_lines(threat_text), TRACE_HEADING)
    if len(traces) != 1:
        problem = "appears twice" if traces else "is missing"
        return [*issues, ValidationIssue(threat, f"{TRACE_HEADING} {problem}")]

    owners = _Owners(root)
    traced: set[str] = set()
    for row in _first_table(traces[0]):
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        if len(cells) != 3 or not cells[0].isdecimal() or not cells[0].isascii():
            issues.append(ValidationIssue(threat, f"malformed trace row: {row}"))
            continue
        number, control, named = cells[0], cells[1], OWNER_PATTERN.findall(cells[2])
        if number in traced:
            issues.append(
                ValidationIssue(threat, f"invariant {number} is traced twice")
            )
        traced.add(number)
        if number not in numbers:
            issues.append(ValidationIssue(threat, f"no invariant {number} to trace"))
        if control.lower() in PLACEHOLDERS or not any(c.isalpha() for c in control):
            issues.append(ValidationIssue(threat, f"invariant {number} has no control"))
        if not named:
            issues.append(
                ValidationIssue(threat, f"invariant {number} has no test owner")
            )
        for owner in named:
            problem = owners.problem(owner)
            if problem is not None:
                issues.append(ValidationIssue(threat, f"invariant {number}: {problem}"))
    issues.extend(
        ValidationIssue(threat, f"invariant {number} is not traced")
        for number in numbers
        if number not in traced
    )
    return issues


def validate_repository(root: Path) -> list[ValidationIssue]:
    """Return all broken links, malformed task metadata, and dependencies,
    and, when there is a security model, every gap in its trace."""
    issues: list[ValidationIssue] = []
    task_files = sorted(
        path
        for path in (root / "docs/curriculum/milestones").rglob("*.md")
        if TASK_PATTERN.fullmatch(path.name)
    )
````

**4.** Знайдіть:

```python
        values, _ = _front_matter(source, source.read_text())
        for dependency in TASK_ID_PATTERN.findall(values.get("depends_on", "")):
            if dependency not in task_ids:
                issues.append(
                    ValidationIssue(source, f"broken dependency: {dependency}")
                )
    return issues
```

Замініть на:

```python
        values, _ = _front_matter(source, source.read_text())
        for dependency in TASK_ID_PATTERN.findall(values.get("depends_on", "")):
            if dependency not in task_ids:
                issues.append(
                    ValidationIssue(source, f"broken dependency: {dependency}")
                )
    if (root / SECURITY_MODEL).is_file():
        issues.extend(validate_threat_model(root))
    return issues
```

Чому синтаксичне дерево: пошук тексту `def test_x(` знаходить і тест у рядку, у мертвій гілці `if False:` чи у вкладеному помічнику, а жоден із них pytest не запускає. `ast.parse` бачить те, що бачить Python.

Перевірка: `uv run python scripts/validate_docs.py` нічого не друкує і завершується з кодом 0.

### Крок 5 — Крок із кодом: тести для перевірки

Цей крок доводить роботу перевірки на маленьких тимчасових репозиторіях і на справжньому.

**Простими словами:** Один тест запускає перевірку на цьому репозиторії й очікує нуль проблем. Інші будують крихітний репозиторій із двома інваріантами, трасуванням, тестовим файлом і задачею, змінюють одну річ і очікують рівно ту проблему, яку ця зміна має спричинити: відсутній рядок, перейменований тест, завершену задачу, схований рядок, правило не в тій формі списку, нечитабельний тестовий файл.

| Тест | Що доводить |
|---|---|
| `test_the_repository_threat_model_traces_every_security_invariant` | Успіх: справжнє трасування повне; це також запускається в unit-job CI |
| `test_an_invariant_added_without_a_trace_row_is_reported` | Найризикованіший край: нове правило, яке ніхто не простежив |
| `test_an_owner_that_names_no_test_or_open_task_is_reported`, `test_a_completed_task_cannot_stay_a_test_owner` | Типізована відмова: кожен фальшивий власник дає одну названу проблему |
| `test_a_test_owner_must_be_a_test_pytest_collects`, `test_a_test_file_that_cannot_be_read_is_reported_not_raised` | Регресії з ревʼю: текст, що лише схожий на тест, і зламані файли |
| `test_a_row_outside_the_visible_trace_table_traces_nothing`, `test_an_invariant_in_another_list_shape_is_reported`, `test_a_second_invariants_or_trace_heading_is_reported` | Регресії з ревʼю: сховані рядки і правила в інших формах |

Створіть `tests/test_threat_model.py`:

````python
"""The threat model traces every security invariant to a control and a test owner."""

from pathlib import Path

import pytest

from knowledge_service.documentation import (
    ValidationIssue,
    validate_repository,
    validate_threat_model,
)

ROOT = Path(__file__).resolve().parents[1]

SECURITY = """# Security model

## Invariants

1. A Chunk is eligible only when a grant matches.
2. Authorization occurs inside the queries.

## Trust zones
"""

TESTS = """
def test_eligibility() -> None: ...


class TestQueries:
    async def test_in_the_query(self) -> None: ...
"""

TASK = """---
id: {task_id}
title: Example
milestone: M07
track: critical
depends_on: []
estimate: 10m
topics: [security]
deliverables: [tests]
status: {status}
---
"""

ROWS = [
    "| 1 | the predicate | `tests/test_access.py::test_eligibility`, `M07-T07` |",
    "| 2 | in the query | `tests/test_access.py::TestQueries::test_in_the_query` |",
]


def repository(
    root: Path,
    rows: list[str] | None = None,
    *,
    security: str = SECURITY,
    statuses: dict[str, str] | None = None,
) -> Path:
    """A small repository with a security model, a trace, a test, and tasks."""
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs/security.md").write_text(security)
    table = ["| Invariant | Control | Test owners |", "|---|---|---|"]
    (root / "docs/threat-model.md").write_text(
        "# Threat model\n\n## Invariant trace\n\n"
        + "\n".join([*table, *(ROWS if rows is None else rows)])
        + "\n\n## Known gaps\n\n| 9 | not | a trace row |\n"
    )
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests/test_access.py").write_text(TESTS)
    milestone = root / "docs/curriculum/milestones/M07-identity"
    milestone.mkdir(parents=True, exist_ok=True)
    for task_id, status in (statuses or {"M07-T07": "planned"}).items():
        (milestone / f"{task_id}-example.md").write_text(
            TASK.format(task_id=task_id, status=status)
        )
    return root


def messages(issues: list[ValidationIssue]) -> list[str]:
    return [issue.message for issue in issues]


def with_invariant(line: str) -> str:
    """The security model with one more line at the end of its invariants."""
    return SECURITY.replace("\n## Trust zones", f"{line}\n\n## Trust zones")


def test_the_repository_threat_model_traces_every_security_invariant() -> None:
    assert validate_threat_model(ROOT) == []


def test_a_complete_trace_has_no_issues(tmp_path: Path) -> None:
    assert validate_threat_model(repository(tmp_path)) == []


def test_an_invariant_added_without_a_trace_row_is_reported(tmp_path: Path) -> None:
    security = with_invariant("3. Restrictive changes apply at once.")

    issues = validate_threat_model(repository(tmp_path, security=security))

    assert messages(issues) == ["invariant 3 is not traced"]
    assert issues[0].source == tmp_path / "docs/threat-model.md"


@pytest.mark.parametrize(
    ("owner", "problem"),
    [
        (
            "`tests/test_access.py::test_renamed`",
            "no such test: tests/test_access.py::test_renamed",
        ),
        (
            "`tests/test_gone.py::test_eligibility`",
            "no readable test file: tests/test_gone.py",
        ),
        ("`M07-T99`", "unknown task: M07-T99"),
        (
            "`tests/test_access.py::test_eligibility[case]`",
            "an owner must be a test or a task: "
            "tests/test_access.py::test_eligibility[case]",
        ),
        (
            "`tests/../src/test_x.py::test_eligibility`",
            "an owner must be a test or a task: "
            "tests/../src/test_x.py::test_eligibility",
        ),
        (
            "`tests/helpers.py::test_eligibility`",
            "an owner must be a test or a task: tests/helpers.py::test_eligibility",
        ),
        ("`the security team`", "an owner must be a test or a task: the security team"),
    ],
    ids=[
        "renamed-test",
        "missing-file",
        "unknown-task",
        "parametrized",
        "outside",
        "not-collected-file",
        "prose",
    ],
)
def test_an_owner_that_names_no_test_or_open_task_is_reported(
    tmp_path: Path, owner: str, problem: str
) -> None:
    rows = [f"| 1 | the predicate | {owner} |", ROWS[1]]

    assert messages(validate_threat_model(repository(tmp_path, rows))) == [
        f"invariant 1: {problem}"
    ]


def test_a_completed_task_cannot_stay_a_test_owner(tmp_path: Path) -> None:
    root = repository(tmp_path, statuses={"M07-T07": "completed"})

    assert messages(validate_threat_model(root)) == [
        "invariant 1: M07-T07 is completed; name the test it added"
    ]


@pytest.mark.parametrize(
    "module",
    [
        "# def test_eligibility() -> None: ...\n",
        "def test_eligibility_later() -> None: ...\n",
        'TEXT = """\ndef test_eligibility():\n"""\n',
        "if False:\n    def test_eligibility() -> None: ...\n",
        "def helper() -> None:\n    def test_eligibility() -> None: ...\n",
        "class Helper:\n    def test_eligibility(self) -> None: ...\n",
    ],
    ids=[
        "comment",
        "longer-name",
        "string",
        "dead-branch",
        "nested",
        "not-a-test-class",
    ],
)
def test_a_test_owner_must_be_a_test_pytest_collects(
    tmp_path: Path, module: str
) -> None:
    root = repository(tmp_path)
    tests = TESTS.replace("def test_eligibility", "def other")
    (root / "tests/test_access.py").write_text(tests + module)

    assert messages(validate_threat_model(root)) == [
        "invariant 1: no such test: tests/test_access.py::test_eligibility"
    ]


def test_a_test_written_with_a_space_before_its_parameters_counts(
    tmp_path: Path,
) -> None:
    root = repository(tmp_path)
    (root / "tests/test_access.py").write_text(
        TESTS.replace("def test_eligibility()", "def test_eligibility ()")
    )

    assert validate_threat_model(root) == []


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        (b"\xff\ndef test_eligibility(): pass\n", "no readable test file"),
        (b"def test_eligibility(:\n", "cannot parse test file"),
        (b"def test_eligibility(): pass\x00\n", "cannot parse test file"),
    ],
    ids=["not-utf-8", "syntax-error", "null-byte"],
)
def test_a_test_file_that_cannot_be_read_is_reported_not_raised(
    tmp_path: Path, content: bytes, problem: str
) -> None:
    root = repository(tmp_path)
    (root / "tests/test_access.py").write_bytes(content)

    assert messages(validate_threat_model(root)) == [
        f"invariant 1: {problem}: tests/test_access.py",
        f"invariant 2: {problem}: tests/test_access.py",
    ]


@pytest.mark.parametrize(
    ("rows", "problems"),
    [
        (["| 1 |  | `M07-T07` |", ROWS[1]], ["invariant 1 has no control"]),
        (["| 1 | TBD | `M07-T07` |", ROWS[1]], ["invariant 1 has no control"]),
        (["| 1 | - | `M07-T07` |", ROWS[1]], ["invariant 1 has no control"]),
        (
            ["| 1 | the predicate | nobody yet |", ROWS[1]],
            ["invariant 1 has no test owner"],
        ),
        ([*ROWS, ROWS[0]], ["invariant 1 is traced twice"]),
        ([*ROWS, "| 7 | something | `M07-T07` |"], ["no invariant 7 to trace"]),
        (
            [ROWS[0], "| two | in the query | `M07-T07` |"],
            [
                "malformed trace row: | two | in the query | `M07-T07` |",
                "invariant 2 is not traced",
            ],
        ),
    ],
    ids=[
        "no-control",
        "placeholder",
        "dash",
        "no-owner",
        "twice",
        "unknown",
        "malformed",
    ],
)
def test_a_row_that_traces_nothing_is_reported(
    tmp_path: Path, rows: list[str], problems: list[str]
) -> None:
    assert messages(validate_threat_model(repository(tmp_path, rows))) == problems


@pytest.mark.parametrize(
    "hidden",
    [
        f"<!--\n{ROWS[1]}\n-->",
        f"```\n{ROWS[1]}\n```",
        f"### Archived\n\n{'| Invariant | Control | Test owners |'}\n|---|---|---|\n"
        f"{ROWS[1]}",
    ],
    ids=["comment", "fence", "second-table"],
)
def test_a_row_outside_the_visible_trace_table_traces_nothing(
    tmp_path: Path, hidden: str
) -> None:
    root = repository(tmp_path, [ROWS[0], "", hidden])

    assert messages(validate_threat_model(root)) == ["invariant 2 is not traced"]


@pytest.mark.parametrize(
    "line",
    [
        "3.  Two spaces.",
        "3.\tA tab.",
        "3) A parenthesis.",
        "   3. Indented.",
        "- A bullet.",
    ],
    ids=["two-spaces", "tab", "parenthesis", "indented", "bullet"],
)
def test_an_invariant_in_another_list_shape_is_reported(
    tmp_path: Path, line: str
) -> None:
    issues = validate_threat_model(repository(tmp_path, security=with_invariant(line)))

    assert messages(issues) == [
        f"an invariant must be written as `N. text`: {line.strip()}"
    ]
    assert issues[0].source == tmp_path / "docs/security.md"


def test_invariants_must_count_up_from_one(tmp_path: Path) -> None:
    security = SECURITY.replace("2. Authorization", "1. Authorization")

    issues = validate_threat_model(repository(tmp_path, security=security))

    assert messages(issues) == [
        "invariants must be numbered 1, 2, 3, ... in order",
        "no invariant 2 to trace",
    ]


def test_an_example_in_a_fenced_block_is_not_an_invariant(tmp_path: Path) -> None:
    security = with_invariant("\n```\n3. An example, not a rule.\n```")

    assert validate_threat_model(repository(tmp_path, security=security)) == []


def test_a_second_invariants_or_trace_heading_is_reported(tmp_path: Path) -> None:
    root = repository(tmp_path, security=f"{SECURITY}\n## Invariants\n\n3. More.\n")

    assert messages(validate_threat_model(root)) == ["## Invariants appears twice"]

    root = repository(tmp_path)
    threat = root / "docs/threat-model.md"
    threat.write_text(f"{threat.read_text()}\n## Invariant trace\n")

    assert messages(validate_threat_model(root)) == ["## Invariant trace appears twice"]


def test_a_missing_threat_model_or_trace_is_reported(tmp_path: Path) -> None:
    root = repository(tmp_path)
    threat = root / "docs/threat-model.md"
    threat.write_text("# Threat model\n\n## Abuse cases\n")

    assert messages(validate_threat_model(root)) == ["## Invariant trace is missing"]

    threat.unlink()

    assert messages(validate_threat_model(root)) == ["missing threat model"]


def test_a_security_model_with_no_invariants_cannot_pass(tmp_path: Path) -> None:
    security = SECURITY.replace("## Invariants", "## Rules")

    issues = validate_threat_model(repository(tmp_path, security=security))

    assert messages(issues) == ["no numbered invariants to trace"]
    assert issues[0].source == tmp_path / "docs/security.md"


def test_the_documentation_check_runs_the_trace(tmp_path: Path) -> None:
    root = repository(tmp_path, [ROWS[0]])

    assert messages(validate_repository(root)) == ["invariant 2 is not traced"]
````

Запустіть:

```bash
uv run pytest tests/test_threat_model.py tests/test_documentation_validator.py -q
```

Очікувано: 45 passed; сам `tests/test_threat_model.py` дає 42 passed.

### Крок 6 — Без коду: зламайте і вирішіть

Цей крок показує, що саме захищає перевірка, і записує, чого ви навчилися.

**Дія 1:** навмисно зламайте перевірку. У `src/knowledge_service/documentation.py`, ближче до кінця `validate_threat_model`, видаліть блок, що повідомляє про непростежені інваріанти:

```python
    issues.extend(
        ValidationIssue(threat, f"invariant {number} is not traced")
        for number in numbers
        if number not in traced
    )
```

Запустіть:

```bash
uv run pytest tests/test_threat_model.py -q
```

Очікувано: 6 failed, 36 passed, серед них `test_an_invariant_added_without_a_trace_row_is_reported` і три випадки `test_a_row_outside_the_visible_trace_table_traces_nothing`. Без цього блоку новий інваріант без рядка проходить мовчки, а саме від такого дрейфу перевірка й існує. Поверніть блок і запускайте, доки все не пройде.

**Дія 2:** подивіться, як перевірка падає по-справжньому. У `docs/threat-model.md` замініть `test_the_stream_record_holds_no_text` на `test_the_stream_record_holds_no_texts` і запустіть `make docs-check`. Очікувано: команда завершується з помилкою і друкує:

```text
docs/threat-model.md: invariant 5: no such test: tests/test_streaming.py::test_the_stream_record_holds_no_texts
```

Надрукований шлях починається з абсолютного шляху вашого репозиторію. Відкотіть зміну.

**Дія 3:** під `## Decision` напишіть:

- чому трасування перевіряє код, а не лише ревʼю;
- чому завершена задача не може лишатися власником-тестом;
- чому рахується лише те, що бачить читач;
- які інваріанти сьогодні не мають тесту в коді і яка задача відповідає за кожен;
- хоча б одне **спростоване припущення** у формі «Я припускав X. Ревʼю показує Y. Отже, Z хибне».

### Крок 7 — Без коду: перевірки, evidence і прогрес

Цей крок доводить, що все досі працює, і записує результат.

**Дія 1:** запустіть перевірки по черзі:

```bash
uv run pytest tests/test_threat_model.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Очікувано: усе проходить. Еталонний запуск дав 2362 passed для `make test-unit`, на 42 більше, ніж після M06-T14, і 378 passed для `make test-integration`, як і після M06-T14. Якщо пише, що тестової бази немає, запустіть Docker, а потім `docker start knowledge-service-postgres`.

**Дія 2:** заповніть файл evidence:

- **Design:** що містить модель загроз, формат трасування, що вважається власником-тестом і де запускається перевірка.
- **Verification:** команди й числа, навмисна поломка, справжнє падіння `make docs-check` і відомі обмеження з «Перш ніж почати».
- **Review:** підсумок ревʼю з «Перш ніж почати».
- **Reflection:** ваші відповіді на запитання нижче.

Потім позначте задачу завершеною, додайте рядок M07-T01 у `docs/curriculum/PROGRESS.md`, поставте наступною задачею M07-T02 і зробіть коміт `m07-t01: write the system threat model`.

## Запитання для рефлексії

1. Перевірка доводить, що тест, названий у трасуванні, існує. Чого вона не доводить про цей тест, і хто має це оцінити?
2. Модель загроз каже, що `confirm_citations` ще ніхто не викликає. Який сценарій зловживання це сьогодні лишає відкритим, і що побачив би Користувач, якби він стався?
3. Який інваріант найлегше зламати майбутнім прискоренням, наприклад кешуванням результатів пошуку, і який тест у трасуванні це впіймає?
4. Коли M07-T04 завершать, перевірка падатиме, доки інваріант 1 не назве новий тест. Це корисно чи дратує? Що ви втратили б, якби завершені задачі могли лишатися власниками?
5. Яка точка входу в моделі турбує вас найбільше сьогодні, і яка найменша зміна її закрила б?

## Чек-лист завершення

- [ ] Файл evidence містить інваріант і ваші передбачення, написані до змін.
- [ ] `## Inventory` називає засіб захисту і тест або відповідальну задачу для кожного з семи інваріантів.
- [ ] `docs/threat-model.md` охоплює активи, акторів, зони довіри, точки входу і сценарії зловживань із захистом для інгестії, пошуку, генерації, агентів і експлуатації.
- [ ] Його трасування має по рядку на інваріант, кожен із засобом захисту і хоча б одним власником-тестом, і жоден рядок не приписує коду засіб, якого там немає.
- [ ] `docs/security.md` вказує на модель загроз.
- [ ] `make docs-check` падає, коли бракує рядка трасування або він називає тест, якого немає.
- [ ] Ви виконали навмисну поломку, побачили падіння тестів і відкотили її.
- [ ] Усі перевірки проходять: format, lint, types, unit, integration, docs.
- [ ] Evidence, статус задачі, рядок у PROGRESS і коміт зроблено.

Коли напишете файли, поділіться ними або виводом помилок, і я перевірю їх за цим чек-листом.
