# M05-T07 — Create the first bilingual retrieval set

Source task: [M05-T07](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T07-create-the-first-bilingual-retrieval-set.md)

## What you will do

So far every search test has used a few sentences written inside the test itself. This lesson builds the first real measuring stick: a small set of documents in English and Ukrainian, plus a list of questions where each one names the exact passage that answers it. A script then asks every question and reports where the right passage ranked. Later lessons run the same set against new search methods and compare the numbers.

- A **corpus** of seven short Markdown documents: three in English, three in Ukrainian, and one restricted document that no ordinary reader may see.
- A **cases file**, `cases.json`, with 16 questions. Each one names the passage that answers it by document path and heading, and some also name a restricted passage that must never be returned.
- A module, `evaluation_dataset.py`, that loads the cases file and refuses a broken one, checks every named passage against the corpus without a database, and runs the whole set against an empty database.
- A script, `run_retrieval_dataset.py`, that does the check alone or the full run, and writes a report of ranks per case and per slice.

The cases cover what the task names: English to English, Ukrainian to Ukrainian, both cross-language directions, exact identifiers such as `EXP-2041`, and Ukrainian word endings (morphology).

| The task asks for | Where it happens |
|---|---|
| A small corpus in English and Ukrainian | Step 2: seven files under `datasets/bilingual-seed/corpus/` |
| Cases for English, Ukrainian, identifiers, morphology, both cross-language directions | Step 3: `cases.json`; Step 4: the coverage rule refuses a file that misses a slice |
| Each case names relevant Documents/Chunks | Step 3: every case lists `relevant` passages by document path and heading path |
| The set is verified | Step 4: `verify_corpus` chunks the corpus as ingestion does and checks every reference |
| A script executes the set deterministically | Step 5: `scripts/run_retrieval_dataset.py`; Step 8: two runs give the same report |
| Success, the highest-risk edge, one typed failure or denial | Steps 6–7: the seed set loads and runs; a restricted passage is never returned; broken files raise `InvalidDataset`, a leaking search raises `IneligibleChunkRetrieved` |
| Authorization preserved, no sensitive text in telemetry | Step 7: the restricted document is stored but never found, and its marker text is absent from the report and the logs |

This lesson includes coding: Steps 4 to 7 write Python. Steps 2 and 3 write data files (Markdown and JSON) with exact text given. Steps 1, 8, and 9 do not involve code.

Terms used in this lesson:

- **Evaluation Case:** one question, the passages that answer it, and labels that say what kind of question it is. The project glossary uses this name.
- **Evaluation Dataset:** a named, versioned collection of verified cases. Here it is called `bilingual-seed`, version 1.
- **Corpus:** the documents the cases search. A fixed corpus means every run searches the same text.
- **Relevant:** a passage that answers the question. A good search returns it near the top.
- **Ineligible:** a passage the reader may not see. It must never be returned, however well it matches. Relevance and eligibility are separate questions: a perfect match in a private document is still off limits.
- **Slice:** a group of cases that share a label, such as "Ukrainian question, English answer" (`uk->en`). Results are counted per slice, because one total hides a weak language.
- **Direction:** the language of the question and the language of the answer, written `en->uk` for an English question answered by a Ukrainian document.
- **Morphology:** how a word changes its ending, like "лікарняний", "лікарняного", "лікарняні". Search that matches exact words misses a different ending.
- **Deterministic:** the same input always gives the same output, like a calculator. A run that depends on chance or a paid model is not.

## Before you begin

This section explains the choices behind the files, so each step makes sense when you reach it.

- **What already exists.** `DirectorySource` reads a folder of files, `normalize` parses each one, and `chunk_by_structure` cuts a parsed document into Chunks. `synchronize_source` runs that whole path and stores the result. `retrieve_hybrid` from M05-T06 asks the searches and merges their lists. Each Candidate it returns carries `source_id`, `external_id` (the file path inside the folder), and `heading_path`.
- **Why cases name passages by path and heading, not by ID.** A Chunk ID is a new random value on every ingestion, so it cannot be written into a file. The pair "file path plus heading path" is stable and readable, like a street address instead of a parcel number. For example: `public/annual-leave.md` with heading `Annual leave policy > Days per year`. `heading_path` is the chain of headings above a Chunk, joined by ` > `.
- **Why each section is one Chunk.** The structure-aware chunker starts a new Chunk at a heading only when the Chunk before it has at least 64 tokens. A short section would merge into its neighbour, and two answers would share one heading. So every section in the corpus, the intro included, holds at least 64 tokens and fewer than 256. Ukrainian words produce fewer tokens per line than English ones, so the Ukrainian sections are a little longer. The check in Step 4 catches any section that merges.
- **Why the check runs without a database.** `verify_corpus` reads and chunks the corpus with the same functions ingestion uses, then confirms each named passage exists exactly once. It needs no database and no network, so it can run on every commit. A case that names a passage the chunker never produces is caught before anyone measures anything.
- **Who reads what.** The corpus has two folders. Files in `public/` are stored as readable by everyone. Files in `restricted/` are stored with no reader at all, which is what the project does today for any non-public document. Every case is asked as one ordinary employee, `READER`. A case's `ineligible` list names restricted passages that match its question well. If any search ever returns a restricted Chunk, the run stops with `IneligibleChunkRetrieved`.
- **Why the run needs an empty database.** The run ingests the corpus itself, with fixed Source IDs. Other documents in the same database would compete for ranks and change the numbers. So the run refuses a database that already holds any Source, before it writes anything. This mirrors the M04-T11 benchmark.
- **Why only the lexical search runs by default.** The dense search needs a paid embedding model and gives slightly different numbers when the model changes. Word search needs neither, so it is the deterministic baseline. `run_dataset` accepts other branches, but it stores Chunks without vectors: it never publishes embeddings. A dense branch passed in today would find no current Embedding Space, so M05-T08 must first add an embedding step after ingestion.
- **What the report holds.** For every case, the rank of each relevant passage among the top 10, or `missed`. Then a count per slice of the cases where at least one relevant passage was found. Recall@k and MRR, the standard scores, come in M05-T08; this lesson leaves them the raw ranks they need. The report never holds a question or any document text, only case IDs and numbers.
- **Expect many misses.** Word search requires every word of the question, except English stop words such as "the", to appear in the passage, and the `simple` search used for Ukrainian does not reduce words to their stems. So most natural questions miss, and every cross-language question misses. That is the baseline this set exists to show, not a bug. Do not reword a question to make it pass: a set tuned to the current search can no longer measure the next one.
- **Known limits.** Equal search scores are ordered by Chunk ID, which is new on every ingestion, so two passages with exactly equal scores could swap places between runs. A file edited between the check and the ingestion is not checked again: a file that cannot be parsed stops the run, but a changed heading only shows as `missed`. A run that fails after the corpus is written leaves the database used, so start the next run on a fresh one. One reader is used for every case, so per-user permissions are not tested here. Sixteen cases are a seed, not a statistically strong sample; the target in `docs/evaluation.md` is about 200.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

This step writes down, before any file exists, what the dataset must guarantee and what you expect the first run to show. Writing predictions first is what makes the later comparison honest.

Create `docs/evidence/M05-T07-bilingual-retrieval-set.md` with this skeleton, then fill in the predictions in your own words:

```markdown
# M05-T07 — Bilingual retrieval set evidence

## Invariant

Every case in `datasets/bilingual-seed/cases.json` names at least one
relevant Chunk in `public/` by Document path and heading path, and each
reference matches exactly one Chunk when the corpus is chunked as ingestion
does. Every required slice (four language directions, identifiers,
morphology) has at least two cases, and at least one case names ineligible
evidence. A run asks every case as one ordinary reader on an empty
database; a Chunk from `restricted/` is never returned, and the report
holds only case IDs, labels, and ranks.

## Expected failing check

Before the change, `uv run pytest tests/test_evaluation_dataset.py` fails
to import `knowledge_service.retrieval.evaluation_dataset`.

## Predictions

- Which English questions will word search find, and which will it miss?
- Will any cross-language question (`en->uk`, `uk->en`) be found?
- Will "Хто оплачує лікарняні?" find the section "Оплата лікарняного"?
- Will the identifier question in Ukrainian, "Що означає помилка VPN-4031?",
  find the English section about VPN-4031?
- Can the restricted executive leave section ever appear?

## Design

## Verification

## Reflection
```

Check: the file exists, and the five prediction bullets have your answers. Read "Expect many misses" in "Before you begin" again if you are unsure how word search treats a question.

### Step 2 — No coding in this step: write the corpus

This step writes the documents the cases will search: three English pages, three Ukrainian pages, and one restricted page. They are short, made-up company policies, so nothing private is committed.

- **Folders:** `datasets/bilingual-seed/corpus/public/` and `datasets/bilingual-seed/corpus/restricted/`.
- **Shape:** each file has one `#` title, one intro paragraph, and two or three `##` sections. Every block holds 64 to 256 tokens, so each one becomes exactly one Chunk.
- **Planted details:** the identifiers `EXP-2041`, `VPN-4031`, and `ІТ-0457`, which exact-word search should find. The Ukrainian word "лікарняний" appears in several forms, which tests morphology. The restricted page repeats the marker `CONFIDENTIAL-SEED-7731`, which the tests look for in places it must never appear.
- **Overlap on purpose:** the restricted page is about annual leave, like `annual-leave.md`, so it matches the annual leave questions well. That is what makes it a real eligibility test.

Copy each file exactly; a changed word changes the token counts and the ranks.

`datasets/bilingual-seed/corpus/public/annual-leave.md`:

```markdown
# Annual leave policy

This policy explains how much paid time off every employee receives, how
to plan it with the team, and what happens to days that are not used by
the end of the year. It applies to permanent and fixed-term employees in
every office, from the first day of work.
Questions that this page does not answer go to the people team, who
update the policy every January.

## Days per year

Every employee receives twenty-four working days of paid annual leave per
calendar year. Employees who join or leave during the year receive a share
of these days in proportion to the months they worked. Public holidays do
not count against the allowance, and a holiday that falls inside a planned
vacation simply extends it by one day.

## Requesting leave

Request annual leave in the HR portal at least two weeks before the first
day away. Choose the dates, name a colleague who covers urgent work, and
submit the request to your manager. A manager answers within three working
days. A request for more than ten consecutive days needs an extra approval
from the head of the department, because it affects project plans.

## Carrying days over

Unused days can be carried over to the next year, up to a maximum of five
days. Carried-over days must be used before the end of March, otherwise
they expire without payment. Days above the limit are lost on the first of
January, so plan the remaining vacation with your manager in the autumn and
book it early.
```

`datasets/bilingual-seed/corpus/public/expense-reports.md`:

```markdown
# Expense reports

Employees who pay for work costs with their own money are reimbursed
through an expense report. This page describes the deadline, the receipts
the finance team needs, and the codes that the reporting system uses when
something is wrong with a submitted report.
Questions about a specific payment go to the finance team, who answer
them in the order they arrive.

## Submission deadline

Submit every expense report within thirty days of the purchase. Attach a
photo or a scan of each receipt, and add a short note that explains the
business purpose. Reports submitted after the deadline are still reviewed,
but they need written approval from your manager, and the payment moves to
the next monthly payroll run instead of the current one.

## Error code EXP-2041

The reporting system shows error EXP-2041 when a receipt is missing or the
image cannot be read. Open the rejected report, upload a sharper image of
the receipt, and submit it again. If the original receipt is lost, fill in
the missing receipt statement instead, sign it, and ask your manager to
confirm the amount before the report goes back to finance.
```

`datasets/bilingual-seed/corpus/public/vpn-access.md`:

```markdown
# Remote access with the VPN

The company network can be reached from home or from a hotel only through
the virtual private network. This guide explains how to connect for the
first time and what to do when the connection fails with one of the
common error codes that the client shows.
When none of these steps help, the service desk can check the connection
logs together with you.

## Connecting for the first time

Install the VPN client from the software catalog on your laptop, then sign
in with your work account. The first connection asks you to approve a
request in the authenticator app on your phone. Keep the app installed: the
client asks for a new approval every twelve hours and after every restart,
and it disconnects when the approval times out.

## Error VPN-4031

The client shows error VPN-4031 when your device certificate has expired.
Certificates are valid for one year and renew automatically only while the
laptop connects to the office network at least once a month. To fix the
error, connect the laptop to the office network or open a ticket with the
service desk, which can issue a new certificate remotely.
```

`datasets/bilingual-seed/corpus/public/likarnianyi.md`:

```markdown
# Лікарняний

Ця сторінка пояснює, що робити працівнику, який захворів, кого треба
попередити в перший день хвороби, які документи потрібні для оплати
пропущених днів і як повернутися до роботи після одужання без зайвих
затримок і непорозумінь з бухгалтерією.
Якщо у вас залишилися питання, напишіть у відділ кадрів, який відповідає
протягом одного робочого дня.
Відділ кадрів зберігає ці правила і оновлює їх щороку в січні.

## Перший день хвороби

Якщо ви захворіли, повідомте керівника до початку робочого дня телефоном
або повідомленням у робочому чаті. Позначте відсутність у кадровому
порталі, щоб колеги бачили, що вас немає. Перші три дні хвороби можна
оформити без довідки, але лише двічі на рік. Якщо хвороба триває довше,
зверніться до сімейного лікаря і попросіть відкрити електронний
лікарняний.
Поки лікарняний відкритий, не виходьте на роботу і не відповідайте на
робочі листи.

## Оплата лікарняного

Бухгалтерія оплачує лікарняний після того, як лікар закриє електронний
лікарняний лист. Перші п'ять днів оплачує компанія, а решту днів оплачує
фонд соціального страхування. Сума залежить від вашого страхового стажу:
за стажу понад вісім років оплачується повна середня зарплата. Виплата
надходить разом із найближчою зарплатою.
Якщо сума здається неправильною, напишіть у бухгалтерію і додайте номер
лікарняного листа, щоб його швидко знайшли.
```

`datasets/bilingual-seed/corpus/public/obladnannia.md`:

```markdown
# Робоче обладнання

Компанія видає кожному працівнику ноутбук, гарнітуру та інше обладнання,
потрібне для роботи. Ця сторінка пояснює, як замовити нову техніку, як
повідомити про поломку і що робити з обладнанням, коли працівник
звільняється або переходить в інший відділ компанії.
Якщо у вас залишилися питання, напишіть у службу підтримки, яка
відповідає протягом одного робочого дня.
Служба підтримки зберігає ці правила і оновлює їх щороку в січні.

## Замовлення ноутбука

Новий ноутбук замовляють через форму ІТ-0457 у каталозі послуг. У формі
вкажіть посаду, потрібну операційну систему та адресу доставки. Керівник
погоджує замовлення, після чого служба підтримки готує ноутбук протягом
п'яти робочих днів. Ноутбук замінюють новим не частіше ніж раз на три
роки, якщо він справний і не пошкоджений.
Якщо ноутбук потрібен терміново, напишіть про це в коментарі до форми,
і служба підтримки підготує його раніше.

## Поломка обладнання

Якщо ноутбук або інше обладнання зламалося, створіть звернення до служби
підтримки і опишіть, що сталося. Не намагайтеся ремонтувати техніку
самостійно і не віддавайте її в сторонній сервіс, бо це скасовує
гарантію. На час ремонту служба підтримки видає підмінний ноутбук, щоб ви
могли працювати далі.
Про крадіжку або втрату ноутбука повідомте в той самий день, щоб служба
підтримки встигла заблокувати доступ до робочих даних.
```

`datasets/bilingual-seed/corpus/public/vidriadzhennia.md`:

```markdown
# Службові відрядження

Ця сторінка описує, як погодити службове відрядження, скільки компанія
платить на день за кордоном і в Україні та як звітувати про витрати після
повернення. Правила однакові для всіх працівників, які їдуть у справах
компанії на один день або довше.
Якщо у вас залишилися питання, напишіть у бухгалтерію, яка відповідає
протягом одного робочого дня.
Бухгалтерія зберігає ці правила і оновлює їх щороку в січні.

## Добові

За кожен день відрядження в межах України компанія виплачує добові у
розмірі шестисот гривень. За кордоном добові становлять п'ятдесят євро на
день незалежно від країни. Добові покривають харчування та дрібні витрати,
тому чеки за них не потрібні. Проживання і квитки оплачуються окремо за
фактичними чеками, які треба зберегти до кінця звітного місяця.
Якщо відрядження скасували, повідомте бухгалтерію, щоб вона не
нараховувала добові за ці дні.

## Звіт про відрядження

Протягом п'яти робочих днів після повернення заповніть звіт про
відрядження у фінансовій системі. Додайте скани квитків, рахунок за
готель і посадковий талон, якщо ви летіли літаком. Звіт погоджує
керівник, а потім бухгалтерія. Без погодженого звіту наступне відрядження
оформити не можна, тому не відкладайте його до наступного місяця.
Якщо звіт повернули на доопрацювання, виправте його протягом двох
робочих днів і надішліть повторно.
```

`datasets/bilingual-seed/corpus/restricted/executive-leave.md`:

```markdown
# Executive annual leave

This restricted page describes the annual leave terms for members of the
executive board. It must never appear in search results for ordinary
employees, even when their question about annual leave matches it well.
Marker for leak checks: CONFIDENTIAL-SEED-7731.
Only the people team and the board office may read it, and copies must
not be shared in chats or email.

## Executive days per year

Members of the executive board receive thirty-two working days of paid
annual leave per calendar year, and they can carry over up to fifteen
unused days of annual leave to the next year. Requests for annual leave
by board members are approved by the chair of the board instead of a
manager. Marker for leak checks: CONFIDENTIAL-SEED-7731.
```

File names are lowercase Latin letters, digits, and dashes, because the loader in Step 4 accepts only such paths in a case. The Ukrainian names are transliterated for that reason.

Check: this command prints every Chunk with its token count and heading path. Every heading should appear exactly once, and no count should be below 64.

```bash
uv run python - <<'EOF'
import asyncio
from pathlib import Path
from knowledge_service.identifiers import SourceId
from knowledge_service.ingestion.chunking import chunk_by_structure
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.normalization import normalize
from knowledge_service.ingestion.sources import SourceAccess

async def main() -> None:
    for folder in ("public", "restricted"):
        root = Path("datasets/bilingual-seed/corpus") / folder
        page = await DirectorySource(root, access=SourceAccess(public=True)).fetch_page(
            checkpoint=None, page_size=100
        )
        for record in page.records:
            document = normalize(record, source_id=SourceId.new())
            print(folder, record.external_id, document.language.language)
            for chunk in chunk_by_structure(document.parsed):
                print("   ", chunk.token_count, chunk.heading_path)

asyncio.run(main())
EOF
```

Expected: 21 Chunks. For example, `annual-leave.md` gives four, with headings `Annual leave policy`, `Annual leave policy > Days per year`, `Annual leave policy > Requesting leave`, and `Annual leave policy > Carrying days over`. Every English file is detected as `en`, and every Ukrainian file as `uk`.

### Step 3 — No coding in this step: write the cases file

This step writes the 16 questions and, for each one, the passage that answers it. This file is the dataset's real content; the code in later steps only checks and runs it.

Each case has these fields:

| Field | What it holds |
|---|---|
| `id` | A short unique name in lowercase words joined by dashes. Reports show only this. |
| `question` | The text asked, as a person would type it. |
| `question_language`, `evidence_language` | `en` or `uk`. Together they give the direction, such as `uk->en`. |
| `query_class` | `plain`, `identifier` (asks about a code such as `EXP-2041`), or `morphology` (uses a different word form from the text). |
| `relevant` | One or more passages that answer it: `document` (folder and file) and `heading` (the full heading path). |
| `ineligible` | Optional. Restricted passages that match it well and must never be returned. |

The cases per slice:

| Slice | Cases |
|---|---|
| `en->en` | 6: three plain, two identifiers, one English morphology case ("renewed" against "renew") |
| `uk->uk` | 5: two plain, one identifier, two morphology cases ("лікарняні", "ноутбуки") |
| `en->uk` | 2: per diem abroad, sick pay |
| `uk->en` | 3: annual leave, the VPN-4031 error, a lost receipt |
| ineligible evidence | 2: both annual leave questions name the executive leave section |

Create `datasets/bilingual-seed/cases.json`:

```json
{
  "name": "bilingual-seed",
  "version": 1,
  "cases": [
    {
      "id": "en-en-annual-leave-days",
      "question": "How many days of annual leave does an employee get per year?",
      "question_language": "en",
      "evidence_language": "en",
      "query_class": "plain",
      "relevant": [
        {"document": "public/annual-leave.md", "heading": "Annual leave policy > Days per year"}
      ],
      "ineligible": [
        {"document": "restricted/executive-leave.md", "heading": "Executive annual leave > Executive days per year"}
      ]
    },
    {
      "id": "en-en-expense-deadline",
      "question": "What is the deadline for submitting an expense report?",
      "question_language": "en",
      "evidence_language": "en",
      "query_class": "plain",
      "relevant": [
        {"document": "public/expense-reports.md", "heading": "Expense reports > Submission deadline"}
      ]
    },
    {
      "id": "en-en-vpn-first-connection",
      "question": "How do I connect to the VPN for the first time?",
      "question_language": "en",
      "evidence_language": "en",
      "query_class": "plain",
      "relevant": [
        {"document": "public/vpn-access.md", "heading": "Remote access with the VPN > Connecting for the first time"}
      ]
    },
    {
      "id": "en-en-error-exp-2041",
      "question": "What does error EXP-2041 mean?",
      "question_language": "en",
      "evidence_language": "en",
      "query_class": "identifier",
      "relevant": [
        {"document": "public/expense-reports.md", "heading": "Expense reports > Error code EXP-2041"}
      ]
    },
    {
      "id": "en-en-error-vpn-4031",
      "question": "VPN-4031",
      "question_language": "en",
      "evidence_language": "en",
      "query_class": "identifier",
      "relevant": [
        {"document": "public/vpn-access.md", "heading": "Remote access with the VPN > Error VPN-4031"}
      ]
    },
    {
      "id": "en-en-renewed-certificate",
      "question": "How is an expired device certificate renewed?",
      "question_language": "en",
      "evidence_language": "en",
      "query_class": "morphology",
      "relevant": [
        {"document": "public/vpn-access.md", "heading": "Remote access with the VPN > Error VPN-4031"}
      ]
    },
    {
      "id": "uk-uk-sick-first-day",
      "question": "Що робити в перший день хвороби?",
      "question_language": "uk",
      "evidence_language": "uk",
      "query_class": "plain",
      "relevant": [
        {"document": "public/likarnianyi.md", "heading": "Лікарняний > Перший день хвороби"}
      ]
    },
    {
      "id": "uk-uk-per-diem-abroad",
      "question": "Які добові за кордоном?",
      "question_language": "uk",
      "evidence_language": "uk",
      "query_class": "plain",
      "relevant": [
        {"document": "public/vidriadzhennia.md", "heading": "Службові відрядження > Добові"}
      ]
    },
    {
      "id": "uk-uk-form-it-0457",
      "question": "Як заповнити форму ІТ-0457?",
      "question_language": "uk",
      "evidence_language": "uk",
      "query_class": "identifier",
      "relevant": [
        {"document": "public/obladnannia.md", "heading": "Робоче обладнання > Замовлення ноутбука"}
      ]
    },
    {
      "id": "uk-uk-who-pays-sick-leaves",
      "question": "Хто оплачує лікарняні?",
      "question_language": "uk",
      "evidence_language": "uk",
      "query_class": "morphology",
      "relevant": [
        {"document": "public/likarnianyi.md", "heading": "Лікарняний > Оплата лікарняного"}
      ]
    },
    {
      "id": "uk-uk-order-laptops",
      "question": "Як замовити ноутбуки?",
      "question_language": "uk",
      "evidence_language": "uk",
      "query_class": "morphology",
      "relevant": [
        {"document": "public/obladnannia.md", "heading": "Робоче обладнання > Замовлення ноутбука"}
      ]
    },
    {
      "id": "en-uk-per-diem-abroad",
      "question": "How much is the daily allowance for business trips abroad?",
      "question_language": "en",
      "evidence_language": "uk",
      "query_class": "plain",
      "relevant": [
        {"document": "public/vidriadzhennia.md", "heading": "Службові відрядження > Добові"}
      ]
    },
    {
      "id": "en-uk-who-pays-sick-leave",
      "question": "Who pays for sick leave?",
      "question_language": "en",
      "evidence_language": "uk",
      "query_class": "plain",
      "relevant": [
        {"document": "public/likarnianyi.md", "heading": "Лікарняний > Оплата лікарняного"}
      ]
    },
    {
      "id": "uk-en-annual-leave-days",
      "question": "Скільки днів щорічної відпустки має працівник?",
      "question_language": "uk",
      "evidence_language": "en",
      "query_class": "plain",
      "relevant": [
        {"document": "public/annual-leave.md", "heading": "Annual leave policy > Days per year"}
      ],
      "ineligible": [
        {"document": "restricted/executive-leave.md", "heading": "Executive annual leave > Executive days per year"}
      ]
    },
    {
      "id": "uk-en-error-vpn-4031",
      "question": "Що означає помилка VPN-4031?",
      "question_language": "uk",
      "evidence_language": "en",
      "query_class": "identifier",
      "relevant": [
        {"document": "public/vpn-access.md", "heading": "Remote access with the VPN > Error VPN-4031"}
      ]
    },
    {
      "id": "uk-en-lost-receipt",
      "question": "Що робити, якщо загубився чек?",
      "question_language": "uk",
      "evidence_language": "en",
      "query_class": "plain",
      "relevant": [
        {"document": "public/expense-reports.md", "heading": "Expense reports > Error code EXP-2041"}
      ]
    }
  ]
}
```

Two rules guide the questions. First, write each one the way an employee would ask, not by copying words from the passage, because a set that copies the text measures nothing. Second, never change a question after seeing how it ranks. If a result surprises you, write a new case instead.

Check: `python3 -m json.tool datasets/bilingual-seed/cases.json > /dev/null` prints nothing, which means the JSON is well formed. The full check comes in Step 5.

### Step 4 — Coding step: write the dataset module

This step writes the code that reads, checks, and runs the dataset. Steps 5 to 7 all build on it.

**In plain words:** this file is the dataset's gatekeeper and its runner. It reads the cases file and refuses one with a mistake, explaining which case and which rule. It then reads the corpus the same way the real ingestion does and confirms that every named passage exists exactly once. Finally, given an empty database, it stores the corpus, asks every question as one ordinary employee, and notes where each right passage ranked. It stops at once if a private passage ever comes back.

What the module holds:

| Part | What it does |
|---|---|
| `Folder`, `QueryClass`, `DIRECTIONS` | The fixed labels: `public` or `restricted`, the three question kinds, and the four language directions. |
| `ChunkReference`, `EvaluationCase`, `EvaluationDataset` | Plain values for one named passage, one case, and the whole set. A case keeps its question out of its repr. |
| `CaseOutcome`, `DatasetReport` | The result of one case (the rank of each relevant passage, or None) and of a whole run. |
| `DatasetError` and four subclasses | `InvalidDataset` (the file or corpus breaks a rule), `DatasetRunRefused` (wrong setting or a database that is not empty), `IneligibleChunkRetrieved`, and `CaseUnanswered` (no search answered a case). |
| `load_dataset` | Reads the file and checks its shape, then the rules a schema cannot express. |
| `verify_corpus` | Chunks the corpus with the ingestion functions and checks every reference. |
| `run_dataset` | Checks its settings, verifies, ingests, runs every case, and returns the report. |
| `render_report` | Writes the report as Markdown: counts per slice, then ranks per case. |

Create `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
"""The bilingual seed dataset: a small corpus and the cases that probe it.

The dataset lives in one folder. `cases.json` holds the Evaluation Cases,
and `corpus/` holds the Documents they search: `corpus/public/` is readable
by everyone, `corpus/restricted/` by nobody. Each case names the Chunks that
answer its Question by Document path and heading path, such as
`public/annual-leave.md` and `Annual leave policy > Days per year`, because
Chunk IDs are new on every ingestion.

What a caller can rely on:

- **Checked on load.** `load_dataset` raises `InvalidDataset` for a file
  that is not valid JSON, has an unknown or missing field, a duplicate case,
  relevant evidence outside `public/`, ineligible evidence outside
  `restricted/`, too few cases for a slice, or a key written twice. Its
  messages never repeat the file's values.
- **Checked against the corpus.** `verify_corpus` reads and chunks the
  corpus the way ingestion does, offline, and raises `InvalidDataset` unless
  every named Chunk exists exactly once and every relevant Document is in
  the case's evidence language.
- **Run as one reader.** `run_dataset` checks its settings, then ingests
  the corpus into an empty database, asks every Question as one ordinary
  employee, and records the rank of each relevant Chunk, or None when it is
  missing. A Question no branch can answer raises `CaseUnanswered`.
- **Eligible.** A Candidate from `restricted/` raises
  `IneligibleChunkRetrieved` and stops the run, however relevant it is.
- **Quiet.** Reports and errors name cases by ID and give ranks only; they
  never hold a Question or Document text.
"""

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knowledge_service.access import AuthorizationContext
from knowledge_service.database import DatabaseRuntime
from knowledge_service.identifiers import SourceId, UserId
from knowledge_service.ingestion.chunking import chunk_by_structure
from knowledge_service.ingestion.directory_source import DirectorySource
from knowledge_service.ingestion.normalization import (
    NormalizedDocument,
    Quarantined,
    normalize,
)
from knowledge_service.ingestion.sources import SourceAccess, SourceError
from knowledge_service.ingestion.synchronization import synchronize_source
from knowledge_service.language import Language
from knowledge_service.persistence.schema import sources
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import (
    BranchSearch,
    HybridRetrievalUnavailable,
    lexical_branch,
    retrieve_hybrid,
)
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES

DATASET_DIRECTORY = Path("datasets/bilingual-seed")
CASES_FILE = "cases.json"
CORPUS_DIRECTORY = "corpus"
MAX_FILE_BYTES = 1_000_000
MAX_NAME_CHARACTERS = 64
MIN_CASES_PER_SLICE = 2
DEFAULT_LIMIT = 10
CASE_TIMEOUT_SECONDS = 30.0
PAGE_SIZE = 100

MAX_PATH_CHARACTERS = 200

_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# Lowercase path segments, so a path is safe to print in an error message.
_PATH = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*")


class Folder(StrEnum):
    """Where a corpus Document lives, and so who may read it."""

    PUBLIC = "public"
    RESTRICTED = "restricted"


class QueryClass(StrEnum):
    """What kind of Question a case asks."""

    PLAIN = "plain"
    IDENTIFIER = "identifier"
    MORPHOLOGY = "morphology"


_LANGUAGES = (Language.ENGLISH, Language.UKRAINIAN)
DIRECTIONS = tuple(f"{asked}->{found}" for asked in _LANGUAGES for found in _LANGUAGES)

# Fixed, so two runs of the same dataset store the same Sources and reader.
SOURCE_IDS: Mapping[Folder, SourceId] = {
    Folder.PUBLIC: SourceId(UUID(int=0x5EED_0001)),
    Folder.RESTRICTED: SourceId(UUID(int=0x5EED_0002)),
}
READER = AuthorizationContext(user_id=UserId(UUID(int=0x5EED_0003)))
_REQUEST_IDS = UUID(int=0x5EED_0004)


class DatasetError(Exception):
    """The dataset could not be loaded, verified, or run."""


class InvalidDataset(DatasetError):
    """The cases file or the corpus breaks a rule of the dataset."""


class DatasetRunRefused(DatasetError):
    """The run cannot start: the database is not empty or a setting is wrong."""


class IneligibleChunkRetrieved(DatasetError):
    """Retrieval returned a Chunk the reader may not see."""


class CaseUnanswered(DatasetError):
    """No branch answered one case, so the run cannot score it."""


@dataclass(frozen=True, slots=True)
class ChunkReference:
    """One Chunk, named by its Document's path and its heading path."""

    folder: Folder
    external_id: str
    heading: str

    @property
    def document(self) -> str:
        """The Document's path inside the corpus, such as `public/a.md`."""
        return f"{self.folder}/{self.external_id}"


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """One Question, the Chunks that answer it, and the Chunks it must not see."""

    case_id: str
    question: str = field(repr=False)
    question_language: Language
    evidence_language: Language
    query_class: QueryClass
    relevant: tuple[ChunkReference, ...]
    ineligible: tuple[ChunkReference, ...]

    @property
    def direction(self) -> str:
        """The language asked in and the language answered in, as `uk->en`."""
        return f"{self.question_language}->{self.evidence_language}"


@dataclass(frozen=True, slots=True)
class EvaluationDataset:
    """A named, versioned list of verified cases."""

    name: str
    version: int
    cases: tuple[EvaluationCase, ...]


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """Where each relevant Chunk of one case ranked, or None if it was missed."""

    case_id: str
    direction: str
    query_class: QueryClass
    ranks: tuple[int | None, ...]
    degraded: bool

    @property
    def found(self) -> bool:
        """True when at least one relevant Chunk was returned."""
        return any(rank is not None for rank in self.ranks)


@dataclass(frozen=True, slots=True)
class DatasetReport:
    """The outcome of every case of one run, in the dataset's order."""

    name: str
    version: int
    limit: int
    branches: tuple[RetrievalBranch, ...]
    outcomes: tuple[CaseOutcome, ...]


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _ReferenceFile(_File):
    document: str
    heading: str


class _CaseFile(_File):
    id: str
    question: str
    question_language: Literal["en", "uk"]
    evidence_language: Literal["en", "uk"]
    query_class: Literal["plain", "identifier", "morphology"]
    relevant: list[_ReferenceFile] = Field(min_length=1)
    ineligible: list[_ReferenceFile] = Field(default_factory=list[_ReferenceFile])


class _DatasetFile(_File):
    name: str
    version: int = Field(ge=1)
    cases: list[_CaseFile] = Field(min_length=1)


def load_dataset(path: Path) -> EvaluationDataset:
    """Read and check a cases file; raise `InvalidDataset` if it breaks a rule."""
    # A pipe or a device would block the read, or never end.
    if not path.is_file():
        raise InvalidDataset("the cases file is missing or not a regular file")
    try:
        with path.open("rb") as file:
            raw = file.read(MAX_FILE_BYTES + 1)
    except OSError as error:
        raise InvalidDataset(
            f"the cases file cannot be read: {type(error).__name__}"
        ) from None
    if len(raw) > MAX_FILE_BYTES:
        raise InvalidDataset(f"the cases file is larger than {MAX_FILE_BYTES} bytes")
    try:
        content = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_keys,
            parse_constant=_no_constant,
        )
    except ValueError, RecursionError:
        # UnicodeDecodeError and JSONDecodeError are both ValueErrors.
        raise InvalidDataset("the cases file is not valid UTF-8 JSON") from None
    try:
        parsed = _DatasetFile.model_validate(content)
    except ValidationError as error:
        raise InvalidDataset(_describe(error)) from None
    return _dataset(parsed)


async def verify_corpus(dataset: EvaluationDataset, corpus: Path) -> None:
    """Check every named Chunk against the corpus, chunked as ingestion does.

    Raises `InvalidDataset` when a Document is missing or cannot be ingested,
    when a reference matches no Chunk or more than one, or when a relevant
    Document is not in the case's evidence language.
    """
    documents: dict[tuple[Folder, str], tuple[Language, Counter[str | None]]] = {}
    for folder in Folder:
        for document in await _read_folder(corpus / folder, folder):
            headings = Counter(
                chunk.heading_path for chunk in chunk_by_structure(document.parsed)
            )
            key = (folder, document.provenance.external_id)
            documents[key] = (document.language.language, headings)
    for case in dataset.cases:
        for reference in (*case.relevant, *case.ineligible):
            found = documents.get((reference.folder, reference.external_id))
            if found is None:
                raise InvalidDataset(
                    f"case {case.case_id}: {reference.document} is not in the corpus"
                )
            matches = found[1][reference.heading]
            if matches != 1:
                raise InvalidDataset(
                    f"case {case.case_id}: {matches} Chunks of {reference.document}"
                    " have the named heading path; exactly 1 is required"
                )
        for reference in case.relevant:
            language = documents[(reference.folder, reference.external_id)][0]
            if language is not case.evidence_language:
                raise InvalidDataset(
                    f"case {case.case_id}: {reference.document} is detected as"
                    f" {language}, not {case.evidence_language}"
                )


async def run_dataset(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    limit: int = DEFAULT_LIMIT,
    branches: Mapping[RetrievalBranch, BranchSearch] | None = None,
) -> DatasetReport:
    """Ingest the corpus into an empty database and run every case.

    `branches` defaults to lexical search alone, which needs no provider.
    Raises `DatasetRunRefused` before writing anything when the database
    already holds a Source, `limit` is out of range, or `branches` is not a
    mapping of `RetrievalBranch` to a search; `IneligibleChunkRetrieved`
    when a case returns a restricted Chunk; `CaseUnanswered` when no branch
    answers a case; and `InvalidDataset` like `verify_corpus`. A run that
    fails after ingesting leaves the database used: start the next one on a
    fresh database.
    """
    # bool is an int, so `True` would otherwise pass as a limit of 1.
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    searches = (
        {RetrievalBranch.LEXICAL: lexical_branch(runtime)}
        if branches is None
        else branches
    )
    _check_branches(searches)
    await verify_corpus(dataset, corpus)
    await _ingest(runtime, dataset, corpus, observed_at)
    outcomes = [await _run_case(case, searches, limit) for case in dataset.cases]
    return DatasetReport(
        name=dataset.name,
        version=dataset.version,
        limit=limit,
        branches=tuple(branch for branch in RetrievalBranch if branch in searches),
        outcomes=tuple(outcomes),
    )


def render_report(report: DatasetReport) -> str:
    """Return the report as Markdown: found counts per slice, then every case."""
    branches = ", ".join(report.branches)
    lines = [
        f"# Retrieval dataset report: {report.name} v{report.version}",
        "",
        f"Branches: {branches}. Each case keeps its top {report.limit} Candidates;"
        " a rank is where a relevant Chunk appears among them.",
        "",
        "## By slice",
        "",
        "| Slice | Cases | Found |",
        "|---|---:|---:|",
    ]
    slices: list[tuple[str, list[CaseOutcome]]] = [
        (direction, [o for o in report.outcomes if o.direction == direction])
        for direction in DIRECTIONS
    ]
    slices += [
        (str(query_class), [o for o in report.outcomes if o.query_class is query_class])
        for query_class in QueryClass
    ]
    for name, outcomes in slices:
        found = sum(outcome.found for outcome in outcomes)
        lines.append(f"| {name} | {len(outcomes)} | {found} |")
    lines += [
        "",
        "## By case",
        "",
        "| Case | Direction | Class | Ranks | Degraded |",
        "|---|---|---|---|---|",
    ]
    for outcome in report.outcomes:
        ranks = ", ".join(
            "missed" if rank is None else str(rank) for rank in outcome.ranks
        )
        degraded = "yes" if outcome.degraded else "no"
        lines.append(
            f"| {outcome.case_id} | {outcome.direction} | {outcome.query_class}"
            f" | {ranks} | {degraded} |"
        )
    return "\n".join(lines) + "\n"


def _unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(set(keys)) != len(keys):
        # Otherwise the last copy wins, and a reviewer reads the other one.
        raise ValueError("a key appears twice in one object")
    return dict(pairs)


def _no_constant(name: str) -> None:
    raise ValueError("NaN and Infinity are not JSON")


_FIELDS = frozenset(
    name
    for model in (_DatasetFile, _CaseFile, _ReferenceFile)
    for name in model.model_fields
)


def _describe(error: ValidationError) -> str:
    # The message names the place and the rule, never a value. An unknown
    # key is a value too, so only known field names and indexes are shown.
    first = error.errors(include_input=False, include_url=False)[0]
    parts = [
        str(part) if isinstance(part, int) or part in _FIELDS else "<unknown>"
        for part in first["loc"]
    ]
    return f"{'.'.join(parts) or 'file'}: {first['msg']}"


def _dataset(parsed: _DatasetFile) -> EvaluationDataset:
    if not _is_name(parsed.name):
        raise InvalidDataset("name must be lowercase words joined by hyphens")
    cases: list[EvaluationCase] = []
    seen: set[str] = set()
    for index, item in enumerate(parsed.cases):
        if not _is_name(item.id):
            raise InvalidDataset(
                f"cases.{index}.id must be lowercase words joined by hyphens"
            )
        if item.id in seen:
            raise InvalidDataset(f"case {item.id} appears more than once")
        seen.add(item.id)
        cases.append(_case(item))
    _check_coverage(cases)
    return EvaluationDataset(
        name=parsed.name, version=parsed.version, cases=tuple(cases)
    )


def _case(item: _CaseFile) -> EvaluationCase:
    question = item.question
    # Word search refuses a Question with no letter or digit, such as one
    # made of dashes, and that refusal would fail the whole run.
    if not any(c.isalnum() for c in question) or len(question) > MAX_QUERY_CHARACTERS:
        raise InvalidDataset(
            f"case {item.id}: the question must be 1 to"
            f" {MAX_QUERY_CHARACTERS} characters with a letter or digit"
        )
    # PostgreSQL text cannot hold NUL. The JSON parser already refuses a
    # lone surrogate, which UTF-8 cannot hold.
    if "\x00" in question:
        raise InvalidDataset(f"case {item.id}: the question must be valid text")
    relevant = tuple(_reference(item.id, ref) for ref in item.relevant)
    ineligible = tuple(_reference(item.id, ref) for ref in item.ineligible)
    if any(ref.folder is not Folder.PUBLIC for ref in relevant):
        raise InvalidDataset(f"case {item.id}: relevant evidence must be in public/")
    if any(ref.folder is not Folder.RESTRICTED for ref in ineligible):
        raise InvalidDataset(
            f"case {item.id}: ineligible evidence must be in restricted/"
        )
    if len(set(relevant + ineligible)) != len(relevant) + len(ineligible):
        raise InvalidDataset(f"case {item.id} names the same Chunk twice")
    return EvaluationCase(
        case_id=item.id,
        question=question,
        question_language=Language(item.question_language),
        evidence_language=Language(item.evidence_language),
        query_class=QueryClass(item.query_class),
        relevant=relevant,
        ineligible=ineligible,
    )


def _reference(case_id: str, item: _ReferenceFile) -> ChunkReference:
    folder, _, external_id = item.document.partition("/")
    if (
        folder not in set(Folder)
        or len(item.document) > MAX_PATH_CHARACTERS
        or _PATH.fullmatch(external_id) is None
    ):
        raise InvalidDataset(
            f"case {case_id}: a document must be public/<path> or"
            " restricted/<path>, in lowercase letters, digits, and . _ -"
        )
    if not item.heading.strip():
        raise InvalidDataset(f"case {case_id}: a heading must not be blank")
    return ChunkReference(Folder(folder), external_id, item.heading)


def _check_coverage(cases: list[EvaluationCase]) -> None:
    counts = Counter(case.direction for case in cases)
    counts.update(str(case.query_class) for case in cases)
    required = [*DIRECTIONS, QueryClass.IDENTIFIER, QueryClass.MORPHOLOGY]
    for name in required:
        if counts[str(name)] < MIN_CASES_PER_SLICE:
            raise InvalidDataset(
                f"slice {name} needs at least {MIN_CASES_PER_SLICE} cases"
            )
    if not any(case.ineligible for case in cases):
        raise InvalidDataset("at least one case must name ineligible evidence")


def _check_branches(searches: Mapping[RetrievalBranch, BranchSearch]) -> None:
    # Checked here, not only in `retrieve_hybrid`, so a wrong setting is
    # refused before the corpus is written.
    if not isinstance(cast(object, searches), Mapping) or not searches:
        raise DatasetRunRefused("at least one branch is required")
    for branch, search in searches.items():
        if type(branch) is not RetrievalBranch or not callable(search):
            raise DatasetRunRefused(
                "every branch must map a RetrievalBranch to a search"
            )


def _is_name(value: str) -> bool:
    return len(value) <= MAX_NAME_CHARACTERS and _NAME.fullmatch(value) is not None


async def _read_folder(root: Path, folder: Folder) -> list[NormalizedDocument]:
    source = DirectorySource(root, access=SourceAccess(public=True))
    documents: list[NormalizedDocument] = []
    checkpoint: str | None = None
    while True:
        try:
            page = await source.fetch_page(checkpoint=checkpoint, page_size=PAGE_SIZE)
        except SourceError:
            raise InvalidDataset(
                f"the corpus folder {folder}/ cannot be read"
            ) from None
        for record in page.records:
            outcome = normalize(record, source_id=SOURCE_IDS[folder])
            if isinstance(outcome, Quarantined):
                raise InvalidDataset(
                    f"{folder}/{record.external_id} cannot be ingested:"
                    f" {outcome.reason}"
                )
            documents.append(outcome)
        if page.next_checkpoint is None:
            return documents
        checkpoint = page.next_checkpoint


async def _ingest(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    observed_at: datetime,
) -> None:
    async with runtime.transaction() as connection:
        # Held until commit, so a second run started at the same moment
        # waits here, then finds these Sources and is refused.
        await connection.execute(
            sa.text("LOCK TABLE sources IN SHARE ROW EXCLUSIVE MODE")
        )
        if await connection.scalar(sa.select(sa.exists().select_from(sources))):
            raise DatasetRunRefused("the dataset runs only on an empty database")
        for folder, source_id in SOURCE_IDS.items():
            await connection.execute(
                sa.insert(sources).values(
                    source_id=source_id.value,
                    kind="local_directory",
                    location=f"/datasets/{dataset.name}/{folder}",
                    display_name=f"{dataset.name} {folder}",
                    enabled=True,
                    created_at=observed_at,
                    updated_at=observed_at,
                )
            )
    for folder, source_id in SOURCE_IDS.items():
        # Only public/ is readable; restricted/ is granted to nobody.
        access = SourceAccess(public=folder is Folder.PUBLIC)
        summary = await synchronize_source(
            runtime,
            DirectorySource(corpus / folder, access=access),
            source_id=source_id,
            observed_at=observed_at,
        )
        if summary.quarantined:
            raise InvalidDataset(f"a Document in {folder}/ could not be ingested")


async def _run_case(
    case: EvaluationCase,
    searches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
) -> CaseOutcome:
    try:
        result = await retrieve_hybrid(
            case.question,
            context=READER,
            branches=searches,
            limit=limit,
            timeout_seconds=CASE_TIMEOUT_SECONDS,
            request_id=uuid5(_REQUEST_IDS, case.case_id),
            language=case.question_language,
        )
    except HybridRetrievalUnavailable as error:
        failures = ", ".join(
            f"{b.branch} {b.error or b.status}" for b in error.branches
        )
        raise CaseUnanswered(
            f"case {case.case_id}: no branch answered ({failures})"
        ) from None
    ranks: dict[tuple[str, str | None], int] = {}
    for candidate in result.candidates:
        if candidate.source_id != SOURCE_IDS[Folder.PUBLIC]:
            raise IneligibleChunkRetrieved(
                f"case {case.case_id} returned a Chunk the reader may not see"
            )
        ranks.setdefault(
            (candidate.external_id, candidate.heading_path), candidate.rank
        )
    return CaseOutcome(
        case_id=case.case_id,
        direction=case.direction,
        query_class=case.query_class,
        ranks=tuple(ranks.get((ref.external_id, ref.heading)) for ref in case.relevant),
        degraded=result.degraded,
    )
```

How the important parts work:

- **Two layers of checks in `load_dataset`.** The pydantic models (`_DatasetFile`, `_CaseFile`, `_ReferenceFile`) check the shape: every field present, no unknown field, the right types (`strict=True` refuses `true` as a version). Then `_dataset` and `_case` check what a schema cannot: unique IDs, a question with at least one letter or digit, `public/` for relevant passages and `restricted/` for ineligible ones, and enough cases per slice.
- **Why the file is parsed with `json.loads` first.** Standard JSON lets a key appear twice, and the last copy silently wins. A reviewer reading the file would then see one question while another one runs. The `object_pairs_hook` refuses that, and `parse_constant` refuses `NaN`, which is not real JSON.
- **Quiet errors.** An error names the place and the rule, never the value. `_describe` drops pydantic's copy of the input. It also hides an unknown key, because a key is text from the file too. Document paths must be lowercase letters, digits, and `. _ -`, so a path in a message cannot carry a newline or a terminal control code.
- **Why a question needs a letter or digit.** Word search refuses a question made only of symbols, such as forty dashes. With word search as the only branch, nothing would answer, and the run would fail after it had already written the corpus. Refusing such a question at load time stops that early.
- **`verify_corpus` reuses ingestion.** It reads each folder with `DirectorySource`, parses with `normalize`, and cuts with `chunk_by_structure`, the same calls `build_chunks` makes. It counts how many Chunks have each heading path, so "exactly one" catches both a missing section and one that was split or merged.
- **Settings first, writes last.** `run_dataset` checks `limit` and `branches` before touching the database. `_ingest` then locks the `sources` table, checks that it is empty, and inserts the two Sources in one transaction. The lock makes a second run started at the same moment wait, find the Sources, and be refused cleanly.
- **Restricted means nobody.** `_ingest` stores `restricted/` with `SourceAccess(public=False)`, which grants it to no one. The permission check inside each search's SQL then hides it from `READER`. `_run_case` checks again on the way out: any Candidate not from the public Source stops the run.
- **Fixed IDs.** The Source IDs, the reader, and each request ID (`uuid5` of the case ID) are fixed. Two runs of the same dataset therefore store and ask the same things.

Check: `uv run pyright src/knowledge_service/retrieval/evaluation_dataset.py` reports 0 errors, and `uv run ruff check src/knowledge_service/retrieval/evaluation_dataset.py` reports no problems.

### Step 5 — Coding step: write the script

This step adds the command you will actually run. It has two modes: a quick check that needs nothing but the files, and a full run against a database.

**In plain words:** the script loads the cases, then either checks them against the corpus and says how many passed, or runs them against an empty database and writes the report to a file. Any dataset problem ends the script with a one-line reason instead of a long error trace.

- `--verify-only` runs `load_dataset` and `verify_corpus`; no database is needed.
- Without it, the script reads `KNOWLEDGE_SERVICE_DATABASE_URL`, runs `run_dataset`, writes `artifacts/evaluations/bilingual-seed.md` (Git ignores `artifacts/`), and prints the report.
- `--limit` changes how many Candidates each case keeps; `--dataset` points at another dataset folder.
- Exit code 0 on success, 1 for any `DatasetError`.

Create `scripts/run_retrieval_dataset.py`:

```python
"""Verify the bilingual seed dataset and, given a database, run every case.

`--verify-only` checks the cases against the corpus and needs no database.
Otherwise point `KNOWLEDGE_SERVICE_DATABASE_URL` at an empty, migrated
database; the run refuses any other. The report goes to
`artifacts/evaluations/bilingual-seed.md`, which Git ignores, and is printed.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DEFAULT_LIMIT,
    DatasetError,
    DatasetReport,
    EvaluationDataset,
    load_dataset,
    render_report,
    run_dataset,
    verify_corpus,
)
from knowledge_service.settings import Settings

REPORT = Path("artifacts/evaluations/bilingual-seed.md")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DATASET_DIRECTORY)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--output", type=Path, default=REPORT)
    parser.add_argument("--verify-only", action="store_true")
    arguments = parser.parse_args()
    corpus = arguments.dataset / CORPUS_DIRECTORY
    try:
        dataset = load_dataset(arguments.dataset / CASES_FILE)
        if arguments.verify_only:
            asyncio.run(verify_corpus(dataset, corpus))
            sys.stdout.write(f"{len(dataset.cases)} cases verified\n")
            return 0
        runtime = create_database_runtime(Settings())
        report = asyncio.run(_run(runtime, dataset, corpus, arguments.limit))
    except DatasetError as error:
        sys.stderr.write(f"dataset run failed: {error}\n")
        return 1
    rendered = render_report(report)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"{rendered}\nFull report: {arguments.output}\n")
    return 0


async def _run(
    runtime: DatabaseRuntime, dataset: EvaluationDataset, corpus: Path, limit: int
) -> DatasetReport:
    try:
        return await run_dataset(
            runtime, dataset, corpus, observed_at=datetime.now(UTC), limit=limit
        )
    finally:
        await runtime.dispose()


if __name__ == "__main__":
    sys.exit(main())
```

Check:

```bash
uv run python scripts/run_retrieval_dataset.py --verify-only
```

Expected: `16 cases verified`. Now break one heading on purpose: change `"Annual leave policy > Days per year"` in the first case to `"Annual leave policy > Days"` and run again. Expected: `dataset run failed: case en-en-annual-leave-days: 0 Chunks of public/annual-leave.md have the named heading path; exactly 1 is required`. Undo the change.

### Step 6 — Coding step: test the file rules and the corpus check

This step proves, without a database, that the real dataset passes and that each kind of mistake is refused with the right message. These tests run on every commit.

**In plain words:** the tests start from the real cases file, change one thing at a time, and check that the loader refuses the result and explains why. They also feed it random junk to make sure it never fails in an unexpected way or repeats what it was given. Then they damage a copy of the corpus and check that the corpus check notices.

| Test | What it proves |
|---|---|
| `test_the_seed_dataset_loads_and_matches_its_corpus` | Success: the real dataset loads, verifies, and covers every slice. |
| `test_a_cases_file_that_breaks_a_rule_is_refused` | 29 single mistakes, each refused with its own message. |
| `test_a_file_that_is_not_a_dataset_is_refused`, `test_json_that_is_ambiguous_or_broken_is_refused` | Empty, broken, duplicate-key, `NaN`, very deep, huge-number, and non-UTF-8 files are refused. |
| `test_a_missing_cases_file_is_refused`, `test_an_unreadable_cases_file_is_refused`, `test_a_pipe_in_place_of_the_cases_file_is_refused_without_waiting` | A missing, unreadable, or pipe file is refused, and a pipe does not make the loader wait forever. |
| The two `..._raises_only_invalid_dataset_without_echoing_it` tests | Hypothesis plants a marker in random values; the only error is `InvalidDataset`, and the marker never appears in it. |
| `test_an_error_never_repeats_a_key_or_a_path_from_the_file` | An unknown key or a path with a newline is not repeated in the message. |
| `test_a_case_that_does_not_match_the_corpus_is_refused`, `test_a_heading_shared_by_two_chunks_is_refused` | A missing document, a wrong heading, a wrong language, or a heading that matches two Chunks fails verification. |
| `test_a_corpus_document_that_cannot_be_ingested_is_refused`, `test_a_missing_corpus_folder_is_refused`, `test_the_corpus_is_read_page_by_page` | Corpus problems are typed, and reading works across pages. |
| `test_a_wrong_setting_is_refused_before_any_work` | A bad `limit` or `branches` raises `DatasetRunRefused` before any connection is made. |
| `test_the_report_counts_slices_and_shows_ranks_only` | Slice counts and the rank column are right. |

Create `tests/test_evaluation_dataset.py`:

```python
"""The bilingual seed dataset: its file rules, its corpus check, its report."""

import json
import os
import shutil
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import SecretStr

from knowledge_service.database import create_database_runtime
from knowledge_service.retrieval import evaluation_dataset
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    DIRECTIONS,
    MAX_FILE_BYTES,
    CaseOutcome,
    DatasetReport,
    DatasetRunRefused,
    InvalidDataset,
    QueryClass,
    load_dataset,
    render_report,
    run_dataset,
    verify_corpus,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import BranchRequest, BranchResult, BranchSearch
from knowledge_service.retrieval.lexical_search import MAX_QUERY_CHARACTERS
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES
from knowledge_service.settings import Settings

MARKER = "PLANTED-MARKER-4417"

type Content = dict[str, Any]
type Edit = Callable[[Content], None]


def seed() -> Content:
    return json.loads((DATASET_DIRECTORY / CASES_FILE).read_text(encoding="utf-8"))


def copy_dataset(tmp_path: Path) -> Path:
    target = tmp_path / "dataset"
    shutil.copytree(DATASET_DIRECTORY, target)
    return target


def write_cases(folder: Path, content: Content) -> Path:
    path = folder / CASES_FILE
    path.write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    return path


def edited(edit: Edit) -> Content:
    content = seed()
    edit(content)
    return content


def first_case(content: Content) -> Content:
    return content["cases"][0]


def top(key: str, value: object) -> Edit:
    def edit(content: Content) -> None:
        content[key] = value

    return edit


def case_field(key: str, value: object) -> Edit:
    def edit(content: Content) -> None:
        first_case(content)[key] = value

    return edit


def without_case_field(key: str) -> Edit:
    def edit(content: Content) -> None:
        del first_case(content)[key]

    return edit


def reference(key: str, value: str) -> Edit:
    def edit(content: Content) -> None:
        first_case(content)["relevant"][0][key] = value

    return edit


def duplicate_first_case(content: Content) -> None:
    content["cases"].append(dict(first_case(content)))


def relevant_from_restricted(content: Content) -> None:
    case = first_case(content)
    case["relevant"].append(case["ineligible"][0])


def ineligible_from_public(content: Content) -> None:
    case = first_case(content)
    case["ineligible"].append(case["relevant"][0])


def relevant_twice(content: Content) -> None:
    case = first_case(content)
    case["relevant"].append(case["relevant"][0])


def drop_class(query_class: str) -> Edit:
    def edit(content: Content) -> None:
        content["cases"] = [
            case for case in content["cases"] if case["query_class"] != query_class
        ]

    return edit


def only_english_evidence(content: Content) -> None:
    content["cases"] = [
        case for case in content["cases"] if case["evidence_language"] == "en"
    ]


def without_ineligible(content: Content) -> None:
    for case in content["cases"]:
        case.pop("ineligible", None)


async def test_the_seed_dataset_loads_and_matches_its_corpus() -> None:
    dataset = load_dataset(DATASET_DIRECTORY / CASES_FILE)

    await verify_corpus(dataset, DATASET_DIRECTORY / CORPUS_DIRECTORY)

    assert (dataset.name, dataset.version) == ("bilingual-seed", 1)
    directions = Counter(case.direction for case in dataset.cases)
    classes = Counter(case.query_class for case in dataset.cases)
    assert all(directions[direction] >= 2 for direction in DIRECTIONS)
    assert classes[QueryClass.IDENTIFIER] >= 2
    assert classes[QueryClass.MORPHOLOGY] >= 2
    assert any(case.ineligible for case in dataset.cases)


def test_a_case_keeps_its_question_out_of_repr() -> None:
    case = load_dataset(DATASET_DIRECTORY / CASES_FILE).cases[0]

    assert case.question not in repr(case)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (case_field("extra", 1), "<unknown>: Extra inputs"),
        (without_case_field("question"), "question"),
        (case_field("question_language", "de"), "question_language"),
        (case_field("query_class", "fuzzy"), "query_class"),
        (case_field("relevant", []), "relevant"),
        (top("version", True), "version"),
        (top("version", 0), "version"),
        (top("cases", []), "cases"),
        (top("name", "Bilingual Seed"), "name"),
        (case_field("id", "Case 1"), "id"),
        (duplicate_first_case, "more than once"),
        (case_field("question", "   "), "letter or digit"),
        (case_field("question", "-" * 40), "letter or digit"),
        (case_field("question", chr(0x200B)), "letter or digit"),
        (case_field("question", "a" * (MAX_QUERY_CHARACTERS + 1)), "characters"),
        (case_field("question", "leave\x00days"), "valid text"),
        (reference("document", "annual-leave.md"), "public/<path>"),
        (reference("document", "private/a.md"), "public/<path>"),
        (reference("document", "public/Annual-Leave.md"), "public/<path>"),
        (reference("document", "public/../restricted/a.md"), "public/<path>"),
        (reference("document", "public/" + "a" * 200 + ".md"), "public/<path>"),
        (reference("heading", " "), "heading"),
        (relevant_from_restricted, "relevant evidence must be in public/"),
        (ineligible_from_public, "ineligible evidence must be in restricted/"),
        (relevant_twice, "same Chunk twice"),
        (drop_class("morphology"), "slice morphology"),
        (drop_class("identifier"), "slice identifier"),
        (only_english_evidence, "slice en->uk"),
        (without_ineligible, "ineligible evidence"),
    ],
)
def test_a_cases_file_that_breaks_a_rule_is_refused(
    tmp_path: Path, edit: Edit, message: str
) -> None:
    path = write_cases(tmp_path, edited(edit))

    with pytest.raises(InvalidDataset, match=message):
        load_dataset(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "file"),
        (b"{not json", "file"),
        (b"[]", "file"),
        (b'{"name": "x", "version": 1, "cases": [', "file"),
        (b" " * (MAX_FILE_BYTES + 1), "larger than"),
    ],
)
def test_a_file_that_is_not_a_dataset_is_refused(
    tmp_path: Path, content: bytes, message: str
) -> None:
    path = tmp_path / CASES_FILE
    path.write_bytes(content)

    with pytest.raises(InvalidDataset, match=message):
        load_dataset(path)


def test_a_missing_cases_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(InvalidDataset, match="not a regular file"):
        load_dataset(tmp_path / CASES_FILE)


def test_an_unreadable_cases_file_is_refused(tmp_path: Path) -> None:
    path = write_cases(tmp_path, seed())
    path.chmod(0)
    try:
        with pytest.raises(InvalidDataset, match="cannot be read: PermissionError"):
            load_dataset(path)
    finally:
        path.chmod(0o600)


def test_a_pipe_in_place_of_the_cases_file_is_refused_without_waiting(
    tmp_path: Path,
) -> None:
    os.mkfifo(tmp_path / CASES_FILE)

    with pytest.raises(InvalidDataset, match="not a regular file"):
        load_dataset(tmp_path / CASES_FILE)


@pytest.mark.parametrize(
    "content",
    [
        b'{"name": "a", "name": "b", "version": 1, "cases": []}',
        b'{"name": "bilingual-seed", "version": NaN, "cases": []}',
        b"[" * 100_000,
        b'{"version": 1' + b"0" * 5000 + b"}",
        b"\xff\xfe",
    ],
)
def test_json_that_is_ambiguous_or_broken_is_refused(
    tmp_path: Path, content: bytes
) -> None:
    path = tmp_path / CASES_FILE
    path.write_bytes(content)

    with pytest.raises(InvalidDataset, match="not valid UTF-8 JSON"):
        load_dataset(path)


def test_a_key_written_twice_is_refused_in_the_real_file(tmp_path: Path) -> None:
    raw = (DATASET_DIRECTORY / CASES_FILE).read_text(encoding="utf-8")
    twice = raw.replace('"question": ', f'"question": "{MARKER}", "question": ', 1)
    path = tmp_path / CASES_FILE
    path.write_text(twice, encoding="utf-8")

    with pytest.raises(InvalidDataset) as raised:
        load_dataset(path)
    assert MARKER not in str(raised.value)


@pytest.mark.parametrize(
    "edit",
    [
        case_field(MARKER + "\nforged: line", 1),
        reference("document", f"public/{MARKER}\x1b[2J\nforged: line"),
    ],
)
def test_an_error_never_repeats_a_key_or_a_path_from_the_file(
    tmp_path: Path, edit: Edit
) -> None:
    path = write_cases(tmp_path, edited(edit))

    with pytest.raises(InvalidDataset) as raised:
        load_dataset(path)
    assert MARKER not in str(raised.value)
    assert "\n" not in str(raised.value)


json_values = st.recursive(
    st.none()
    | st.booleans()
    | st.integers()
    | st.floats(allow_nan=False)
    | st.text(max_size=20).map(lambda text: MARKER + text),
    lambda children: (
        st.lists(children, max_size=3)
        | st.dictionaries(st.text(max_size=8), children, max_size=3)
    ),
    max_leaves=10,
)
fields = st.sampled_from(
    [
        "id",
        "question",
        "question_language",
        "evidence_language",
        "query_class",
        "relevant",
        "ineligible",
    ]
)


@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(name=fields, value=json_values)
def test_any_field_value_raises_only_invalid_dataset_without_echoing_it(
    tmp_path: Path, name: str, value: object
) -> None:
    content = seed()
    first_case(content)[name] = value
    path = write_cases(tmp_path, content)

    # Some values are valid, such as a new question; any error must be typed.
    message = ""
    try:
        load_dataset(path)
    except InvalidDataset as error:
        message = str(error)
    assert MARKER not in message


@settings(
    max_examples=200,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(value=json_values)
def test_any_reference_value_raises_only_invalid_dataset_without_echoing_it(
    tmp_path: Path, value: object
) -> None:
    content = seed()
    first_case(content)["relevant"] = [value]
    path = write_cases(tmp_path, content)

    with pytest.raises(InvalidDataset) as raised:
        load_dataset(path)
    assert MARKER not in str(raised.value)


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        (reference("document", "public/missing.md"), "not in the corpus"),
        (reference("heading", "Annual leave"), "0 Chunks"),
        (case_field("evidence_language", "uk"), "detected as en, not uk"),
    ],
)
async def test_a_case_that_does_not_match_the_corpus_is_refused(
    tmp_path: Path, edit: Edit, message: str
) -> None:
    folder = copy_dataset(tmp_path)
    dataset = load_dataset(write_cases(folder, edited(edit)))

    with pytest.raises(InvalidDataset, match=message):
        await verify_corpus(dataset, folder / CORPUS_DIRECTORY)


async def test_a_heading_shared_by_two_chunks_is_refused(tmp_path: Path) -> None:
    folder = copy_dataset(tmp_path)
    document = folder / CORPUS_DIRECTORY / "public" / "annual-leave.md"
    text = document.read_text(encoding="utf-8")
    section = text[text.index("## Days per year") :].split("\n\n## ")[0]
    document.write_text(f"{text}\n{section}\n", encoding="utf-8")
    dataset = load_dataset(folder / CASES_FILE)

    with pytest.raises(InvalidDataset, match="2 Chunks"):
        await verify_corpus(dataset, folder / CORPUS_DIRECTORY)


async def test_a_corpus_document_that_cannot_be_ingested_is_refused(
    tmp_path: Path,
) -> None:
    folder = copy_dataset(tmp_path)
    (folder / CORPUS_DIRECTORY / "restricted" / "empty.md").write_text("")
    dataset = load_dataset(folder / CASES_FILE)

    with pytest.raises(InvalidDataset, match=r"restricted/empty.md cannot be"):
        await verify_corpus(dataset, folder / CORPUS_DIRECTORY)


async def test_a_missing_corpus_folder_is_refused(tmp_path: Path) -> None:
    folder = copy_dataset(tmp_path)
    shutil.rmtree(folder / CORPUS_DIRECTORY / "restricted")
    dataset = load_dataset(folder / CASES_FILE)

    with pytest.raises(InvalidDataset, match="restricted/ cannot be read"):
        await verify_corpus(dataset, folder / CORPUS_DIRECTORY)


async def test_the_corpus_is_read_page_by_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(evaluation_dataset, "PAGE_SIZE", 1)
    dataset = load_dataset(DATASET_DIRECTORY / CASES_FILE)

    await verify_corpus(dataset, DATASET_DIRECTORY / CORPUS_DIRECTORY)


async def never_called(request: BranchRequest) -> BranchResult:
    raise AssertionError("no branch may run")


@pytest.mark.parametrize(
    ("limit", "branches", "message"),
    [
        (0, None, "limit"),
        (MAX_CANDIDATES + 1, None, "limit"),
        (True, None, "limit"),
        (10, {}, "at least one branch"),
        (10, {"lexical": never_called}, "every branch"),
        (10, {RetrievalBranch.LEXICAL: "search"}, "every branch"),
    ],
)
async def test_a_wrong_setting_is_refused_before_any_work(
    limit: int, branches: Mapping[RetrievalBranch, BranchSearch] | None, message: str
) -> None:
    # The runtime connects lazily, so nothing is reached at this address.
    runtime = create_database_runtime(
        Settings(database_url=SecretStr("postgresql+psycopg://u:p@localhost:1/x"))
    )
    dataset = load_dataset(DATASET_DIRECTORY / CASES_FILE)

    with pytest.raises(DatasetRunRefused, match=message):
        await run_dataset(
            runtime,
            dataset,
            DATASET_DIRECTORY / CORPUS_DIRECTORY,
            observed_at=datetime(2026, 11, 3, tzinfo=UTC),
            limit=limit,
            branches=branches,
        )
    await runtime.dispose()


def test_the_report_counts_slices_and_shows_ranks_only() -> None:
    report = DatasetReport(
        name="bilingual-seed",
        version=1,
        limit=10,
        branches=(RetrievalBranch.LEXICAL,),
        outcomes=(
            CaseOutcome("a", "en->en", QueryClass.PLAIN, (1,), degraded=False),
            CaseOutcome("b", "en->en", QueryClass.IDENTIFIER, (None,), degraded=False),
            CaseOutcome("c", "uk->en", QueryClass.PLAIN, (None, 3), degraded=True),
        ),
    )

    rendered = render_report(report)

    assert "| en->en | 2 | 1 |" in rendered
    assert "| uk->en | 1 | 1 |" in rendered
    assert "| uk->uk | 0 | 0 |" in rendered
    assert "| plain | 2 | 2 |" in rendered
    assert "| c | uk->en | plain | missed, 3 | yes |" in rendered
    assert rendered.startswith("# Retrieval dataset report: bilingual-seed v1\n")
```

The settings test builds a database runtime with an address where nothing listens. That is safe, because the runtime connects only on first use, and the test expects the refusal before that. `never_called` raises if a branch ever runs.

Check:

```bash
uv run pytest tests/test_evaluation_dataset.py -q
```

Expected: 63 passed.

### Step 7 — Coding step: test the run on a real database

This step proves the guarantees that only a real database can show: the corpus is stored, the restricted page stays hidden, and nothing private leaks into the report or the logs.

**In plain words:** the tests store the corpus in a fresh test database and run every case. They then check four things. Known good matches come first. The private page is really stored, yet a search for its marker finds nothing. A fake search that returns the private page stops the run. And a second run on the same database is refused without writing anything.

| Test | What it proves |
|---|---|
| `test_every_case_runs_and_the_report_names_no_text` | Success: every case runs in order, three known matches rank first, and neither the restricted marker nor any question appears in the report or the logs. |
| `test_the_restricted_document_is_stored_but_never_found` | The highest-risk edge: both restricted Chunks exist in the database, but searching for their exact marker as `READER` returns nothing. |
| `test_a_branch_that_returns_a_restricted_chunk_stops_the_run` | The typed denial: a fake search that leaks raises `IneligibleChunkRetrieved`, and the message names the case, not the text. |
| `test_a_database_that_is_not_empty_is_refused_before_any_write` | A second run raises `DatasetRunRefused` and adds no rows. |
| `test_a_case_no_branch_answers_raises_a_typed_error` | A search that refuses every case gives `CaseUnanswered` with the branch's error type. |
| `test_two_runs_at_once_give_one_report_and_one_refusal` | Two runs started together: one succeeds, the other is refused cleanly. |

The stored-but-never-found test matters most. Without the `stored == 2` line, it would also pass if the restricted page had never been ingested at all, which would prove nothing about permissions.

Create `tests/test_evaluation_dataset_integration.py`:

```python
"""The bilingual seed dataset on real PostgreSQL: ingested, run, and quiet."""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from pydantic import SecretStr

from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.persistence.schema import chunks, documents, sources
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    READER,
    SOURCE_IDS,
    CaseUnanswered,
    DatasetReport,
    DatasetRunRefused,
    EvaluationDataset,
    Folder,
    IneligibleChunkRetrieved,
    load_dataset,
    render_report,
    run_dataset,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.hybrid import BranchRequest, BranchResult
from knowledge_service.retrieval.lexical_search import (
    InvalidLexicalQuery,
    search_lexical_chunks,
)
from knowledge_service.settings import Settings

OBSERVED_AT = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
# Written into every section of the restricted Document.
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"


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
def dataset() -> EvaluationDataset:
    return load_dataset(DATASET_DIRECTORY / CASES_FILE)


@dataclass(frozen=True, slots=True)
class FakeCandidate:
    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    external_id: str
    title: str
    heading_path: str | None
    source_anchor: str | None
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class FakeResult:
    candidates: tuple[FakeCandidate, ...]
    truncated: bool = False


@pytest.mark.integration
async def test_every_case_runs_and_the_report_names_no_text(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    report = await run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)

    outcomes = {outcome.case_id: outcome for outcome in report.outcomes}
    assert [o.case_id for o in report.outcomes] == [c.case_id for c in dataset.cases]
    assert report.branches == (RetrievalBranch.LEXICAL,)
    assert not any(outcome.degraded for outcome in report.outcomes)
    # Every word of these Questions is in the relevant section.
    assert outcomes["en-en-expense-deadline"].ranks == (1,)
    assert outcomes["en-en-error-vpn-4031"].ranks == (1,)
    assert outcomes["uk-uk-per-diem-abroad"].ranks == (1,)
    rendered = render_report(report)
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in dataset.cases)


@pytest.mark.integration
async def test_the_restricted_document_is_stored_but_never_found(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    await run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)

    async with runtime.transaction() as connection:
        stored = await connection.scalar(
            sa.select(sa.func.count())
            .select_from(chunks.join(documents))
            .where(documents.c.source_id == SOURCE_IDS[Folder.RESTRICTED].value)
        )
        found = await search_lexical_chunks(
            connection, text=RESTRICTED_MARKER, context=READER, limit=10
        )
    assert stored == 2
    assert found.candidates == ()


@pytest.mark.integration
async def test_a_branch_that_returns_a_restricted_chunk_stops_the_run(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    async def leaking(request: BranchRequest) -> BranchResult:
        return FakeResult(
            candidates=(
                FakeCandidate(
                    rank=1,
                    chunk_id=ChunkId.new(),
                    document_id=DocumentId.new(),
                    source_id=SOURCE_IDS[Folder.RESTRICTED],
                    content_version=ContentVersion(1),
                    ordinal=1,
                    external_id="executive-leave.md",
                    title="Executive annual leave",
                    heading_path="Executive annual leave > Executive days per year",
                    source_anchor=None,
                    text=RESTRICTED_MARKER,
                ),
            )
        )

    with pytest.raises(IneligibleChunkRetrieved) as raised:
        await run_dataset(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            branches={RetrievalBranch.LEXICAL: leaking},
        )
    assert dataset.cases[0].case_id in str(raised.value)
    assert RESTRICTED_MARKER not in str(raised.value)


@pytest.mark.integration
async def test_a_database_that_is_not_empty_is_refused_before_any_write(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    await run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)
    async with runtime.transaction() as connection:
        before = await connection.scalar(sa.select(sa.func.count()).select_from(chunks))

    with pytest.raises(DatasetRunRefused, match="empty database"):
        await run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)

    async with runtime.transaction() as connection:
        after = await connection.scalar(sa.select(sa.func.count()).select_from(chunks))
        source_rows = await connection.scalar(
            sa.select(sa.func.count()).select_from(sources)
        )
    assert after == before
    assert source_rows == 2


@pytest.mark.integration
async def test_a_case_no_branch_answers_raises_a_typed_error(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    async def refusing(request: BranchRequest) -> BranchResult:
        raise InvalidLexicalQuery("refused")

    with pytest.raises(CaseUnanswered, match="InvalidLexicalQuery") as raised:
        await run_dataset(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            branches={RetrievalBranch.LEXICAL: refusing},
        )
    assert dataset.cases[0].case_id in str(raised.value)


@pytest.mark.integration
async def test_two_runs_at_once_give_one_report_and_one_refusal(
    migrated_database: str, dataset: EvaluationDataset
) -> None:
    runtimes = [
        create_database_runtime(Settings(database_url=SecretStr(migrated_database)))
        for _ in range(2)
    ]
    try:
        results = await asyncio.gather(
            *(
                run_dataset(runtime, dataset, CORPUS, observed_at=OBSERVED_AT)
                for runtime in runtimes
            ),
            return_exceptions=True,
        )
    finally:
        for runtime in runtimes:
            await runtime.dispose()

    kinds = sorted(type(result).__name__ for result in results)
    assert kinds == sorted([DatasetReport.__name__, DatasetRunRefused.__name__])
```

`FakeCandidate` has every field of the `Candidate` protocol from M05-T04, so fusion accepts it like a real one.

Check:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest tests/test_evaluation_dataset_integration.py -q
```

Expected: 6 passed. Without the URL, all six are skipped, not run.

### Step 8 — No coding in this step: run the set and read the report

This step runs the dataset for real, twice, and compares the result with your predictions. It also breaks the permission rule on purpose, to see the tests catch it.

**Action 1:** create an empty database, migrate it, and run. The URL holds the password, so it lives only in a shell variable.

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_dataset#')"
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py
```

The reference run printed:

```text
# Retrieval dataset report: bilingual-seed v1

Branches: lexical. Each case keeps its top 10 Candidates; a rank is where a relevant Chunk appears among them.

## By slice

| Slice | Cases | Found |
|---|---:|---:|
| en->en | 6 | 4 |
| en->uk | 2 | 0 |
| uk->en | 3 | 0 |
| uk->uk | 5 | 1 |
| plain | 9 | 3 |
| identifier | 4 | 1 |
| morphology | 3 | 1 |

## By case

| Case | Direction | Class | Ranks | Degraded |
|---|---|---|---|---|
| en-en-annual-leave-days | en->en | plain | missed | no |
| en-en-expense-deadline | en->en | plain | 1 | no |
| en-en-vpn-first-connection | en->en | plain | 1 | no |
| en-en-error-exp-2041 | en->en | identifier | missed | no |
| en-en-error-vpn-4031 | en->en | identifier | 1 | no |
| en-en-renewed-certificate | en->en | morphology | 1 | no |
| uk-uk-sick-first-day | uk->uk | plain | missed | no |
| uk-uk-per-diem-abroad | uk->uk | plain | 1 | no |
| uk-uk-form-it-0457 | uk->uk | identifier | missed | no |
| uk-uk-who-pays-sick-leaves | uk->uk | morphology | missed | no |
| uk-uk-order-laptops | uk->uk | morphology | missed | no |
| en-uk-per-diem-abroad | en->uk | plain | missed | no |
| en-uk-who-pays-sick-leave | en->uk | plain | missed | no |
| uk-en-annual-leave-days | uk->en | plain | missed | no |
| uk-en-error-vpn-4031 | uk->en | identifier | missed | no |
| uk-en-lost-receipt | uk->en | plain | missed | no |

Full report: artifacts/evaluations/bilingual-seed.md
```

**Action 2:** prove the run is repeatable. Run the script again on the same database: it must refuse with `dataset run failed: the dataset runs only on an empty database`. Then start from a fresh database and compare:

```bash
cp artifacts/evaluations/bilingual-seed.md /tmp/first-run.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py > /dev/null
diff /tmp/first-run.md artifacts/evaluations/bilingual-seed.md && echo same
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
unset KNOWLEDGE_SERVICE_DATABASE_URL
```

Expected: `same`.

**Action 3:** read the report against your predictions. Each line below explains one pattern:

- **English hits need every word.** "What is the deadline for submitting an expense report?" is found, because every word that is not an English stop word is in the section. "How many days of annual leave does an employee get per year?" misses because "many" and "get" are not in the text.
- **Ukrainian has no stop words or stems.** "Які добові за кордоном?" is found, because all three words are in the section exactly. "Що робити в перший день хвороби?" misses: "робити" and "в" are required too, and the section does not contain them.
- **Morphology misses in Ukrainian, not in English.** "лікарняні" is not "лікарняного", and `simple` keeps words as they are. The English "renewed" becomes "renew", so that case is found.
- **Every cross-language case misses.** Word search cannot match "відпустки" with "leave". Even the identifier case "Що означає помилка VPN-4031?" misses, because its Ukrainian words must match too.
- **The restricted page never appears,** and the run did not stop, so no search returned it.

Write in the evidence file which predictions held and which did not, and why. These numbers are the baseline M05-T08 will compare against.

**Action 4:** break the permission rule on purpose. In `_ingest`, change `access = SourceAccess(public=folder is Folder.PUBLIC)` to `access = SourceAccess(public=True)` and run the integration tests from Step 7. Expected: `test_the_restricted_document_is_stored_but_never_found` fails, because a search for the marker now finds the restricted page. Run the script on a fresh database too: it still succeeds. Every seed question has at least one word the restricted section lacks, so word search never returns it, and the run's own guard has nothing to stop. That guard starts to matter in M05-T08, when search by meaning can return a passage without an exact word match. Undo the change and run the tests again until they pass.

### Step 9 — No coding in this step: gates, docs, evidence, and progress

This step proves everything still works, updates the one architecture line the change made out of date, and records the result.

**Action 1:** in `docs/architecture.md`, change the `retrieval` row of the "Code layout" table to:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`) |
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_evaluation_dataset.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make check
make docs-check
```

Expected: all pass. The reference run gave `make test-unit` 1160 passed, `make test-integration` 294 passed, and `make check` 1454 passed, 3 deselected.

**Action 3:** complete the evidence file:

- **Design:** the folder layout, why cases name passages by path and heading, the two layers of checks, why the check runs offline, the two Sources and the one reader, why the run needs an empty database, and why the default is word search only.
- **Verification:** the commands and counts, the report from Step 8, the repeat-run `diff`, the deliberate break from Step 8, and the known limits from "Before you begin".
- **Review:** an adversarial review of the prototype found six problems, all fixed with regression tests. A question of only symbols passed the checks, then failed the run after the corpus was written; it is now refused at load. Wrong `branches` settings were caught only after writing; they are now checked first. Two runs at once gave a raw database error; a table lock now makes the second one a clean refusal. Errors repeated unknown keys and document paths from the file; they now show neither. A key written twice was silently accepted; it is now refused. A pipe in place of the cases file made the loader wait forever; it is now refused.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M05-T07 row to `docs/curriculum/PROGRESS.md`, set the next task to M05-T08, and commit with `m05-t07: create the first bilingual retrieval set`.

## Reflection questions

1. What does `run_dataset` hide from the code that calls it? List what a caller never has to think about, such as Source rows, permissions, or how a case names its passage.
2. Later someone speeds up the run by skipping `verify_corpus`, because "the corpus has not changed". What wrong number could the report then show, and how would anyone notice?
3. Someone else edits a question after seeing that it misses, so that it now matches. Why does that make the dataset worse at measuring the next search method, even though the report looks better?
4. Every cross-language case misses with word search. What result would you expect from the dense search in M05-T08, and which single case would most surprise you if it still missed?
5. Sixteen cases are few. Pick one slice and say what kind of new case could prove your belief about it wrong, for example a Ukrainian question whose answer uses a rare word form.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before running anything.
- [ ] The seven corpus files exist, and the Step 2 command shows 21 Chunks with no heading repeated.
- [ ] `cases.json` has 16 cases covering all four directions, identifiers, morphology, and ineligible evidence.
- [ ] `uv run python scripts/run_retrieval_dataset.py --verify-only` prints `16 cases verified`.
- [ ] A broken cases file raises `InvalidDataset` with a message that names the case and rule, not the value.
- [ ] The restricted page is stored but never returned, and its marker is absent from the report and the logs.
- [ ] A search that leaks raises `IneligibleChunkRetrieved`; a non-empty database raises `DatasetRunRefused` before any write.
- [ ] Two runs on fresh databases gave the same report.
- [ ] You ran the deliberate break from Step 8, saw a test fail, and undid it.
- [ ] You compared the report with your predictions in the evidence file.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The architecture line, evidence, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
