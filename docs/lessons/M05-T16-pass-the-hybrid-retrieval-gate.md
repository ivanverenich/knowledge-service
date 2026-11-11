# M05-T16 — Pass the hybrid-retrieval understanding gate

Source task: [M05-T16](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T16-pass-the-hybrid-retrieval-gate.md)

## What you will do

This is the M05 gate. It checks that you understand bilingual hybrid search well enough to explain it, draw it, find out why it misses, and fix it with measurements rather than one good example. It mixes writing, a diagnosis, and two small code changes:

| The task asks for | How this lesson meets it |
|---|---|
| Explain language-specific failures and fusion | Step 2: you answer questions in the evidence file from the code, with numbers. |
| Update the retrieval diagram | Step 3: you draw hybrid retrieval from memory; Step 9 adds the verified diagram to `docs/architecture.md`. |
| Add an evaluation slice independently | Step 4: you plan a slice on your own, then add the verified one: the language a Question is asked in, `en->*` and `uk->*`. |
| Diagnose a Ukrainian miss | Step 5: you trace `uk-uk-sick-first-day`, the one seed case fused search ranks second, through both branches and PostgreSQL. |
| Evidence connects the repair to sliced metrics | Steps 6 to 8: you plan and add a repair to Ukrainian word search, then compare every slice before and after, and break it on purpose. |
| Deterministic tests: success, highest-risk edge, typed failure | Steps 4 and 7: the slice, the repair's word rules, the seed page, and the exclusion syntax |
| Authorization before data leaves storage; no seeded text in telemetry | The repair stays inside the same SQL statement as the permission check, and the Question reaches PostgreSQL only as bound parameters. |

This lesson includes coding: Steps 4, 6, and 7 write code. Steps 1, 2, 3, 5, 8, and 9 are writing, running commands, and editing Markdown. Nothing calls OpenAI; Steps 5 and 8 need the local BGE-M3 server.

Terms used in this lesson:

- **Slice:** a group of evaluation cases scored on its own, such as every case asked in Ukrainian. One number for all cases can hide a group that got worse.
- **Branch:** one way of searching inside hybrid retrieval. Here, **words** (PostgreSQL full-text search) and **meaning** (vectors from BGE-M3).
- **Stop word:** a word that carries no topic, such as "що", "в", or "the". English search drops English stop words; until this lesson, Ukrainian search kept every word.
- **Topic word:** a word of the Question that is not a stop word.
- **Reciprocal Rank Fusion (RRF):** each branch gives a Chunk `1 / (60 + rank)`, and the fused score is the sum. Rank 1 gives 0.0164, rank 2 gives 0.0161.

## Before you begin

This section explains what the lesson builds on and what it found, so each step makes sense when you reach it.

- **What already exists.** `retrieve_hybrid` (M05-T06) runs word and meaning search at once and fuses them with RRF (M05-T05). Word search routes each Question to PostgreSQL's `english` configuration, its `simple` one, or both (M05-T03). `simple` keeps every word, lowercased, with no stemming: it was a deliberate baseline for Ukrainian in M05-T02. The seed set's 16 cases (M05-T07) are scored by direction (`uk->en`: asked in Ukrainian, answered in English) and by query class.
- **The miss.** With BGE-M3, fused search finds every seed case, but `uk-uk-sick-first-day` ("Що робити в перший день хвороби?") ranks its Chunk second. Earlier evidence only guessed why. Step 5 finds the exact cause.
- **What the diagnosis shows.** Meaning search puts the right section, "Лікарняний > Перший день хвороби", first. Word search finds only the page's opening Chunk, "Лікарняний", because `websearch_to_tsquery` asks for every word, and the section lacks "робити" and "в" (its text says "у"), which the opening has. RRF then gives the opening two parts, rank 2 by meaning and rank 1 by words, 0.0325, more than the section's one part, 0.0164. PostgreSQL itself ranks the section higher (`ts_rank` 0.986 against 0.912); the every-word rule drops it before ranking.
- **The repair, and the one that looked right first.** Asking for any word instead, without Ukrainian stop words, fixed the case but sliced metrics showed the price: one shared word such as "днів" matched Ukrainian Chunks for questions about English pages, and fused MRR on `uk->en` fell from 1.000 to 0.511. The repair kept is in between: a Chunk must hold **two** topic words of the Question, or the only one there is. Every slice then holds or improves.
- **What the reviews found.** Three adversarial reviews attacked the repair; every break is fixed, with a test that fails without the fix:
  - A word made only of punctuation, such as "?", counted as a topic word that could never match, so "відпустка ?" found nothing. A word typed twice counted twice, so one shared word passed the two-word rule. An exclusion after a bracket, `(-лікарняний)`, slipped past the syntax check and did the opposite.
  - The next version split words in Python, differently from PostgreSQL, so "ставка 1.5", a date, an e-mail address, or a file name matched nothing. Now PostgreSQL finds the topic words itself, with `ts_debug` and the same parser as the stored terms.
  - The topic words were found again for every matching Chunk. Now they are computed once per query, which halved the time on 50,000 matching Chunks.
- **Running the integration tests.** Read the URL inline in each command, so the password is never printed or saved:

  ```bash
  KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_ukrainian_lexical_search_integration.py -q
  ```

- **Files you will touch:**
  - new evidence file `docs/evidence/M05-T16-hybrid-retrieval-gate.md`
  - `src/knowledge_service/retrieval/evaluation_dataset.py` and `tests/test_evaluation_dataset.py`
  - `src/knowledge_service/retrieval/lexical_search.py`, `tests/test_lexical_search.py`, `tests/test_ukrainian_lexical_search_integration.py`, and `tests/test_language_aware_lexical_search_integration.py`
  - `docs/architecture.md`

  Leave the task spec and `PROGRESS.md` alone until Step 9.

- **Known limits.** 16 seed cases are few, and the two-word rule was chosen while looking at them. Ukrainian still has no stemming: "лікарняні" does not match "лікарняний". PostgreSQL splits some words in two, `E-1042` into "e" and "-1042" and `п'ять` into "п" and "ять", and each part counts as a topic word. A Question that uses web-search syntax, including a dash typed as "Відпустка - це", is read as typed, every word required. The stop-word list is short and hand-made. A Question whose words match tens of thousands of Chunks costs hundreds of milliseconds: 782 ms on 50,000 matching Chunks, against about 100 ms for the old every-word query.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before anything changes, write what must be true at the end and what you expect to find. Step 8 checks the predictions.

Create `docs/evidence/M05-T16-hybrid-retrieval-gate.md`:

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

Check: the file exists, the failing check fails, and every prediction is a full sentence Step 8 can prove wrong.

### Step 2 — No coding in this step: explain language-specific failures and fusion

This step asks you to explain, from the code, why each language fails where it does and how fusion combines the branches. Use your own words and cite the file you read.

Under `## Language-specific failures and fusion`, answer:

1. What does the `english` configuration do to "requesting leave" and to "the who", and what does `simple` do to "відпустку" and "відпустки"? Read `lexical_search.py` and `lexical_routing.py`.
2. Why does word search find nothing for `uk-en-annual-leave-days`, a Ukrainian Question about an English page, and what finds it instead?
3. Why does an identifier such as `EXP-2041` route to both configurations, and why do both match it?
4. With RRF and k = 60, compute the fused score of a Chunk ranked 1 by meaning only, and of a Chunk ranked 2 by meaning and 1 by words. Which wins, and what does that say about agreement between branches? Read `fusion.py`.
5. When one branch fails or times out, what does `retrieve_hybrid` return, and how does a caller know? Read `hybrid.py`.

Check: each answer names the function or setting it comes from. Step 5 will test answer 4 on a real case.

### Step 3 — No coding in this step: draw hybrid retrieval from memory

`docs/architecture.md` has the M04 vector-search diagram but no picture of hybrid retrieval. Before you look at the verified diagram in Step 9, draw your own.

Under `## Diagram`, write a Mermaid `sequenceDiagram` (or a numbered list, if you prefer) from memory, with:

- the caller and what it passes, including the stated language and the deadline;
- the two branches running at once, and what each sends to PostgreSQL or the embedding model;
- where the permission check happens;
- what happens when a branch fails, and when every branch fails;
- the fusion formula.

Check: your drawing covers all five points. In Step 9, compare it with the verified diagram and write down what you missed.

### Step 4 — Coding step: add a question-language slice

The task asks for one slice added "independently". Before you read the code below, write your own plan under `## Slice plan`: which cases the slice groups, why that grouping would show a change to Ukrainian word search better than the existing slices, where in `score_slices` it goes, and one test.

**In plain words:** The report already scores each direction, such as Ukrainian questions about English pages. A Ukrainian word-search change affects every Question asked in Ukrainian, whatever the page's language, so this step adds two slices: all cases asked in English (`en->*`) and all asked in Ukrainian (`uk->*`). Each holds eight seed cases, more than any single direction, so one case moves its score less.

In `src/knowledge_service/retrieval/evaluation_dataset.py`, make these edits in order:

**1.** Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

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

Replace with:

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

**2.** Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python

_LANGUAGES = (Language.ENGLISH, Language.UKRAINIAN)
DIRECTIONS = tuple(f"{asked}->{found}" for asked in _LANGUAGES for found in _LANGUAGES)

# Fixed, so two runs of the same dataset store the same Sources and reader.
SOURCE_IDS: Mapping[Folder, SourceId] = {
```

Replace with:

```python

_LANGUAGES = (Language.ENGLISH, Language.UKRAINIAN)
DIRECTIONS = tuple(f"{asked}->{found}" for asked in _LANGUAGES for found in _LANGUAGES)
# The language a Question is asked in, whatever the evidence's language.
QUESTION_LANGUAGES = tuple(f"{asked}->*" for asked in _LANGUAGES)

# Fixed, so two runs of the same dataset store the same Sources and reader.
SOURCE_IDS: Mapping[Folder, SourceId] = {
```

**3.** Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

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

Replace with:

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

In `tests/test_evaluation_dataset.py`, make this edit:

**1.** Find in `tests/test_evaluation_dataset.py`:

```python
    assert scores["uk->uk"].mrr is None


def test_the_report_shows_the_configuration_and_the_scores() -> None:
    rendered = render_report(SCORED)
```

Replace with:

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

The slice names reuse the direction format, so the reports need no other change: a new row appears in every slice table, between the directions and the query classes.

Check:

```bash
uv run pytest tests/test_evaluation_dataset.py -q
uv run ruff check src/knowledge_service/retrieval/evaluation_dataset.py tests/test_evaluation_dataset.py
uv run pyright src/knowledge_service/retrieval/evaluation_dataset.py
```

Expected: all pass, `All checks passed!`, and `0 errors`. Under `## Slice plan`, write what your plan did differently.

### Step 5 — No coding in this step: diagnose the Ukrainian miss

This step finds, with evidence, why fused search ranks `uk-uk-sick-first-day` second.

**Action 1:** start the BGE-M3 server in its own terminal and leave it running for Steps 5 and 8:

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py
```

If port 8765 is taken, start it with `BGE_M3_PORT=8766` in front, and in the other terminal run `export LOCAL_EMBEDDING_URL=http://127.0.0.1:8766` first.

**Action 2:** in a second terminal, run the seed set three ways on a fresh database, keep the report as the "before" measurement, and keep the database for Action 3:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_dataset#')"
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --local-embeddings --output /tmp/m05-t16-before.md
```

Expected, in the first table: fused ("dense + lexical") MRR 0.969 for `all` and 0.900 for `uk->uk`; lexical R@10 0.125 for `uk->*`, so word search finds the right Chunk for only one of eight Ukrainian Questions. In the fused case table, `uk-uk-sick-first-day` has rank 2; in the dense one, rank 1; in the lexical one, `missed`.

**Action 3:** ask PostgreSQL how it reads the Question, and which of the page's Chunks match:

```bash
docker exec knowledge-service-postgres psql -U test_user -d knowledge_service_dataset -c "SELECT websearch_to_tsquery('simple', 'Що робити в перший день хвороби?');"
docker exec knowledge-service-postgres psql -U test_user -d knowledge_service_dataset -c "
SELECT heading_path,
       search_simple @@ websearch_to_tsquery('simple', 'Що робити в перший день хвороби?') AS every_word,
       round(ts_rank(search_simple, websearch_to_tsquery('simple', 'Що робити в перший день хвороби?'))::numeric, 3) AS rank
FROM chunks WHERE heading_path LIKE 'Лікарняний%' ORDER BY ordinal;"
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
```

Expected:

```text
'що' & 'робити' & 'в' & 'перший' & 'день' & 'хвороби'

           heading_path           | every_word | rank
----------------------------------+------------+-------
 Лікарняний                       | t          | 0.912
 Лікарняний > Перший день хвороби | f          | 0.986
 Лікарняний > Оплата лікарняного  | f          | 0.000
```

**Action 4:** under `## Diagnosis`, write the chain from evidence:

- which branch ranks the right section where, from the report;
- what the query requires, and which words the section lacks;
- that PostgreSQL ranks the section higher, but the every-word rule removes it first;
- the RRF arithmetic from Step 2, now with this case's ranks;
- the cause in one sentence, and what kind of repair it points to.

Check: every claim in your diagnosis points to a line of the report or the psql output.

### Step 6 — Coding step: repair Ukrainian word search

Before you read the code, write your plan under `## Repair plan`: what to change about how `simple` reads a Question, what must not change (English, permission check, bound parameters, the web-search syntax), and how you will know it worked. Then compare it with the verified change below.

**In plain words:** Word search for a Ukrainian Question now ignores words that carry no topic, such as "що" or "в", and stops demanding every remaining word. PostgreSQL splits the Question into words itself, the same way it split the stored pages, so a date or a decimal stays one word. A Chunk must contain at least two of the Question's topic words, or its only one, and PostgreSQL's own ranking then puts the section headed "Перший день хвороби" above the page's opening. A Question that uses search syntax, such as quotes or `-word`, is read exactly as typed, as before.

| Part | What it does |
|---|---|
| `UKRAINIAN_STOP_WORDS` | prepositions, conjunctions, particles, pronouns, question words, and forms of "to be" |
| `SimpleQuery`, `simple_query` | whether `simple` reads the Question by its topic words or as typed |
| `_uses_syntax` | PostgreSQL's own rule for quotes, `-` after a non-letter, and a standalone "or" |
| `_topic_match` | in SQL: `ts_debug` splits the Question with the stored terms' parser; word parts and stop words are dropped, each word counts once, the first `MAX_SIMPLE_WORDS` are kept; an "or" query for the index, and a condition that the Chunk's own words hold `MIN_SIMPLE_WORDS` of them |
| `lexical_search_statement` | `simple` uses `_topic_match` unless the text is read as typed; `english` is unchanged |

In `src/knowledge_service/retrieval/lexical_search.py`, make these edits in order:

**1.** Find in `src/knowledge_service/retrieval/lexical_search.py`:

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

Replace with:

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

**2.** Find in `src/knowledge_service/retrieval/lexical_search.py`:

```python
can rely on besides:

- **Syntax.** The query is web-search text, read by `websearch_to_tsquery`:
  words must all match, `"quoted words"` must appear next to each other in
  that order, `or` between words accepts either, and `-word` excludes a word.
  Any other punctuation is ignored, so no text a user types can raise a
  syntax error. A query of only stop words or punctuation matches nothing.
```

Replace with:

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

**3.** Find in `src/knowledge_service/retrieval/lexical_search.py`:

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

Replace with:

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

**4.** Find in `src/knowledge_service/retrieval/lexical_search.py`:

```python

# Long enough for any question, short enough to bound the work per query.
MAX_QUERY_CHARACTERS = 1_000

# Named, not taken from the server's `default_text_search_config`, and the
# same configurations the stored vectors were built with.
```

Replace with:

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

**5.** Find in `src/knowledge_service/retrieval/lexical_search.py`:

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

Replace with:

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

**6.** Find in `src/knowledge_service/retrieval/lexical_search.py`:

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

Replace with:

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

**7.** Find in `src/knowledge_service/retrieval/lexical_search.py`:

```python
        ranked.c.text,
        *marked,
    ).order_by(ranked.c.score.desc(), ranked.c.chunk_id)


async def _search(
```

Replace with:

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

The `# noqa: RUF001` comments are needed because ruff flags one-letter Cyrillic words such as "у" or "а" as look-alikes of Latin letters.

Check:

```bash
uv run ruff format src/knowledge_service/retrieval/lexical_search.py
uv run ruff check src/knowledge_service/retrieval/lexical_search.py
uv run pyright src/knowledge_service/retrieval/lexical_search.py
uv run python -c "from knowledge_service.retrieval.lexical_search import simple_query; print(simple_query('Що робити в перший день хвороби?'))"
```

Expected: `All checks passed!`, `0 errors`, and `SimpleQuery(as_typed=False)`: the Question itself is kept out of the repr.

### Step 7 — Coding step: test the repair

This step proves the word rules without a database and the repair on the real seed page with one.

**In plain words:** The unit tests check which Questions are read by topic words and which as typed, for each kind of search syntax, and that the Question reaches PostgreSQL only as bound parameters, with the stop words as one array. The integration tests load the real seed page and show the section now ranks first; that one shared word is not a match; that a decimal, a date, an address, or a file name stays one word; that a word typed twice counts once; and that an exclusion after a bracket still excludes. Two old expectations change on purpose: "на" alone used to match nearly everything and now matches nothing, and "помилка E-1042" now also finds the English page with that error code.

In `tests/test_lexical_search.py`, make these edits in order:

**1.** Find in `tests/test_lexical_search.py`:

```python
"""Checks on full-text search that need no database."""

from collections.abc import Awaitable, Callable
from typing import cast
```

Replace with:

```python
"""Checks on full-text search that need no database."""
# ruff: noqa: RUF001

from collections.abc import Awaitable, Callable
from typing import cast
```

**2.** Find in `tests/test_lexical_search.py`:

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

Replace with:

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

**3.** Find in `tests/test_lexical_search.py`:

```python
        )


def test_both_configurations_share_one_bound_parameter() -> None:
    statement = lexical_search_statement(
        (LexicalConfiguration.ENGLISH, LexicalConfiguration.SIMPLE),
        text=f"{MARKER}' OR true --",
```

Replace with:

```python
        )


def test_each_configuration_reads_the_text_as_a_bound_parameter() -> None:
    statement = lexical_search_statement(
        (LexicalConfiguration.ENGLISH, LexicalConfiguration.SIMPLE),
        text=f"{MARKER}' OR true --",
```

**4.** Find in `tests/test_lexical_search.py`:

```python
    assert " OR " in sql
    # A rank counts only where its own configuration matches.
    assert "greatest(CASE WHEN" in sql


def test_only_the_ranked_and_limited_rows_are_highlighted() -> None:
```

Replace with:

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

In `tests/test_ukrainian_lexical_search_integration.py`, make these edits in order:

**1.** Find in `tests/test_ukrainian_lexical_search_integration.py`:

```python
    SourceRecord,
)
from knowledge_service.persistence.access import eligible_chunks_predicate
from knowledge_service.retrieval.lexical_search import (
    InvalidLexicalQuery,
    LexicalCandidate,
```

Replace with:

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

**2.** Find in `tests/test_ukrainian_lexical_search_integration.py`:

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

Replace with:

```python
    ("leave", ["leave.txt"], "any language matches in its exact form"),
    ("leaves", [], "miss: no English stemming either"),
    ("every", ["leave.txt"], "no stop words: English every is kept"),
    ("на", [], "stop word: dropped from the query, which is then empty"),
]
```

**3.** Find in `tests/test_ukrainian_lexical_search_integration.py`:

```python
    report = [(query, await names(runtime, query)) for query, _, _ in FIXTURE_REPORT]

    assert report == [(query, found) for query, found, _ in FIXTURE_REPORT]


@pytest.mark.integration
```

Replace with:

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

In `tests/test_language_aware_lexical_search_integration.py`, make this edit:

**1.** Find in `tests/test_language_aware_lexical_search_integration.py`:

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

Replace with:

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

`test_each_configuration_reads_the_text_as_a_bound_parameter` replaces the old one-parameter test with the same proof: no text reaches the SQL.

Check:

```bash
uv run pytest tests/test_lexical_search.py -q
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_ukrainian_lexical_search_integration.py tests/test_language_aware_lexical_search_integration.py -q
```

Expected: `68 passed` and `46 passed`.

### Step 8 — No coding in this step: measure every slice, break it, and decide

This step connects the repair to sliced metrics, shows what the obvious repair would have done, and records the decision.

**Action 1:** run the seed set again on a fresh database:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --local-embeddings --output /tmp/m05-t16-after.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
diff /tmp/m05-t16-before.md /tmp/m05-t16-after.md
```

Expected, in the first table:

| Slice | lexical R@10 before | after | fused MRR before | after |
|---|---:|---:|---:|---:|
| all | 0.312 | 0.500 | 0.969 | 1.000 |
| uk->uk | 0.200 | 0.600 | 0.900 | 1.000 |
| uk->en | 0.000 | 0.333 | 1.000 | 1.000 |
| uk->* | 0.125 | 0.500 | 0.938 | 1.000 |
| en->* | 0.500 | 0.500 | 1.000 | 1.000 |
| identifier | 0.250 | 0.750 | 1.000 | 1.000 |

Every other slice is unchanged. `uk-uk-sick-first-day` ranks first in the fused and the lexical case tables, and word search now also finds `uk-uk-form-it-0457` and `uk-en-error-vpn-4031` by their identifiers.

**Action 2:** break the repair on purpose. In `src/knowledge_service/retrieval/lexical_search.py`, set `MIN_SIMPLE_WORDS = 1`, the "any one word" version. Run the tests:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_ukrainian_lexical_search_integration.py tests/test_language_aware_lexical_search_integration.py -q
```

Expected: 6 failed: the fixture report, `test_one_shared_topic_word_is_not_a_match`, the `typed-twice` and `two-words` cases of `test_each_topic_word_counts_once_and_an_exclusion_still_excludes`, and both "the who" cases of `test_a_question_of_english_stop_words_falls_back_to_simple`. The unit tests still pass, because the rule lives in SQL.

Then measure the broken version, with `MIN_SIMPLE_WORDS = 1` still in place:

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --local-embeddings --output /tmp/m05-t16-broken.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
```

Expected: lexical `uk->*` R@10 rises to 0.625, yet fused MRR falls: 0.511 on `uk->en`, 0.754 on `uk->*`, 0.877 on `all`. Undo the change, run the tests again until all pass, and stop the BGE-M3 server.

**Action 3:** write the decision. Under `## Decision`, write:

- the before, after, and broken numbers for the slices above, because `/tmp` and `artifacts/` are not kept;
- why the repair is accepted: which slices moved, and that none fell;
- what the broken version shows about judging a branch alone, with the case IDs that moved;
- at least one **falsified assumption** in the shape "I assumed X. The run shows Y (case IDs). So Z is wrong."

### Step 9 — No coding in this step: gates, diagram, evidence, and progress

This step brings the architecture text up to date, proves everything still works, and records the result.

**Action 1:** in `docs/architecture.md`, make these edits. The second adds the hybrid-retrieval diagram; compare it with yours from Step 3 and write under `## Diagram` what you missed.

**1.** Find in `docs/architecture.md`:

```markdown
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
```

Replace with:

```markdown
| `ingestion` | Source synchronization, Document normalization, Indexing | `synchronize_source`, `normalize`, `reconcile_document` |
| `ingestion.parsers` | Document normalization | `parse_text`, `parse_markdown`, `parse_html`, `parse_pdf` |
| `embeddings` | Indexing (vectors) | `EmbeddingModel` (`port`), `OpenAIEmbeddingModel` (managed), `LocalEmbeddingModel` (BGE-M3 over HTTP, pinned revision), `SparseModel` (`sparse`, the port for sparse term weights; M05-T10 prototype only), `publish_document_embeddings`, the migration workflow |
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both; `simple` reads a Question's topic words, without Ukrainian stop words, and needs two of them), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet), `RankingModel` (`llm_reranking`, the port for a language model that returns a ranking) with `rerank_with_llm` (orders the top eligible Candidates under opaque IDs within a candidate and token budget, checks the ranking, keeps retrieval's order on any failure, and returns the billed usage) and `OpenAIRankingModel` (`openai_ranking`, a pinned model over the Responses API with Structured Outputs), `compare_llm_reranking` (the same comparison with fallbacks and cost), `compare_reranking_strategies` (no reranker, the cross-encoder, and the language model on the same Candidates of each case, with quality, latency, and cost per case), `classify_question` (`rewrite_classes`, one rewrite class per Question and the words any rewrite must keep; any word with a digit, or with `_`, `@`, `/`, `.`, `:`, or a backslash inside it, is `exact_identifier` and never rewritten) with `load_rewrite_cases` / `score_rewrite_cases` (`rewrite_cases`, the verified labels in `datasets/bilingual-seed/rewrite-classes.json` and how often the rules agree), `rewrite_question` (`query_rewriting`, the `RewriteModel` port: a Question is sent only when its class is switched on in a `RewritePolicy`, and its rewrite is used only when it keeps every word to keep, every typed identifier, and the stated language, and adds no search operator; otherwise the Question is searched as typed) with `OpenAIRewriteModel` (`openai_rewriting`, a pinned model over the Responses API; `QUERY_REWRITE_CLASSES` is the policy flag, empty by default), and `compare_rewriting` with `score_rewriting` / `check_rollout` (`rewrite_evaluation`, the gain per class on `datasets/bilingual-seed/rewrite-retrieval.json`; a class that harms a case, was not fully measured, or does not gain is not rolled out) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
```

**2.** Find in `docs/architecture.md`:

````markdown
refuses to write into any space that is neither current nor the target of a
`dual_writing` migration.

### Answering

```mermaid
````

Replace with:

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

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_lexical_search.py tests/test_evaluation_dataset.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make docs-check
```

Expected: all pass. The reference run gave 139 passed for the narrow tests, `make test-unit` 1676 passed, and `make test-integration` 329 passed.

**Action 3:** complete the evidence file:

- **Design:** the changed files; why a question-language slice; why two topic words, not one and not all; why stop words are dropped only from the query; why syntax is read as typed; what is unchanged (English, the permission check, bound parameters).
- **Verification:** the commands and counts, the three measurements, the deliberate break, and the known limits from "Before you begin".
- **Review:** the three adversarial reviews from "Before you begin": a punctuation-only word, a word typed twice, and `(-лікарняний)` after a bracket; then words split differently from PostgreSQL, so decimals, dates, addresses, and file names matched nothing; then topic words found again for every row. All fixed, each with a test that failed before the fix, and the timing measured before and after.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M05-T16 row to `docs/curriculum/PROGRESS.md`, set the next task to the first M06 task, and commit with `m05-t16: pass the hybrid retrieval gate`.

## Reflection questions

1. What does `search_lexical_chunks` hide from `retrieve_hybrid`? List what the hybrid code never has to know about Ukrainian, English, or search syntax.
2. A later change wants faster Ukrainian search, so it drops the word-count subquery and keeps only the "or" match. Which guarantee from your decision would that break, and which test and which slice would show it?
3. The repair was chosen while looking at 16 cases. What one Ukrainian case would you add to try to prove the two-word rule wrong, and what result would make you change it?
4. Which of your predictions was wrong, and what did you believe about word search or fusion that made you predict it?
5. Fused MRR is now 1.000 on every slice. What does that number not tell you about how good this search is, and what would you measure next?

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before anything ran.
- [ ] Your explanations of language-specific failures and fusion cite the code and include the RRF arithmetic.
- [ ] Your diagram from memory is in the evidence, and what it missed is written down.
- [ ] `score_slices` has `en->*` and `uk->*`, with a test, and your own slice plan is in the evidence.
- [ ] The diagnosis traces `uk-uk-sick-first-day` through the report and PostgreSQL to one cause.
- [ ] `simple` reads topic words found by PostgreSQL, needs two, and reads search syntax as typed; English is unchanged.
- [ ] The new tests pass, and fail with `MIN_SIMPLE_WORDS = 1`.
- [ ] Before, after, and broken measurements are in the evidence, by slice, and no slice fell.
- [ ] `docs/architecture.md` has the hybrid-retrieval diagram.
- [ ] All gates pass: format, lint, types, unit, integration, docs.
- [ ] The evidence, task status, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
