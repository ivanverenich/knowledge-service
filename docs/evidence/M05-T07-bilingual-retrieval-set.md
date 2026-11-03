# M05-T07 — Bilingual retrieval set evidence

Completed: 2026-11-03

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

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words. They were drafted after the lesson prototype had run,
so they are not blind guesses.

- English questions are found only when every word that is not an English
  stop word appears in the section; the English stemmer helps with word
  forms. "What is the deadline for submitting an expense report?" and
  "VPN-4031" should be found. Questions with an extra everyday word, such
  as "get" or "mean", should miss.
- No cross-language question (`en->uk`, `uk->en`) will be found. Word
  search compares words, and "leave" never matches "відпустки".
- "Хто оплачує лікарняні?" will miss "Оплата лікарняного". The `simple`
  search keeps words as they are, so "лікарняні" and "лікарняного" are
  different words.
- "Що означає помилка VPN-4031?" will miss the English VPN-4031 section.
  The identifier matches, but the Ukrainian words must match too, and they
  are not in the English text.
- The restricted executive leave section can never appear. It is stored
  with no reader, the permission check runs inside each search's SQL, and
  the run stops with `IneligibleChunkRetrieved` if any restricted Chunk
  comes back.

### Prediction check

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

All five predictions held against the Step 8 run (word search only, top
10, empty database, 2026-11-03):

| Slice | Cases | Found |
|---|---:|---:|
| en->en | 6 | 4 |
| en->uk | 2 | 0 |
| uk->en | 3 | 0 |
| uk->uk | 5 | 1 |
| plain | 9 | 3 |
| identifier | 4 | 1 |
| morphology | 3 | 1 |

- Every case that was found ranked first. Every miss was a full miss, not
  a low rank: word search requires every word, so a Chunk either matches
  completely or not at all.
- "How many days of annual leave does an employee get per year?" became
  `'mani' & 'day' & 'annual' & 'leav' & 'employe' & 'get' & 'per' &
  'year'`; "many" and "get" are not in the section. "What does error
  EXP-2041 mean?" missed only on `'mean'`.
- "How is an expired device certificate renewed?" became `'expir' &
  'devic' & 'certif' & 'renew'` and was found: the English stemmer joins
  "renewed" and "renew".
- "Хто оплачує лікарняні?" stayed `'хто' & 'оплачує' & 'лікарняні'`,
  while the section stores `'лікарняного'`: no Ukrainian stemming.
- All five cross-language cases missed, including "Що означає помилка
  VPN-4031?", whose Ukrainian words must match too.
- The restricted section never appeared, and the run did not stop.

Repeatability: a second run on the same database was refused (`the
dataset runs only on an empty database`, exit 1) and left the report
untouched. A run on a fresh database gave a byte-identical report. The
database held 6 public Documents with 19 Chunks granted to everyone, and 1
restricted Document with 2 Chunks and no grants.

Deliberate break: storing `restricted/` as public failed
`test_the_restricted_document_is_stored_but_never_found` (1 failed, 5
passed). The script still succeeded, because every seed question has a
word the restricted section lacks. The run's own `IneligibleChunkRetrieved`
guard therefore had nothing to catch with word search alone; it is the
backstop for M05-T08, when dense search can return a passage without an
exact word match. After undoing the change, all 6 tests passed.

## Design

Changed files:

- `datasets/bilingual-seed/corpus/`: seven made-up policy pages. In
  `public/`: `annual-leave.md`, `expense-reports.md`, and `vpn-access.md`
  in English, and `likarnianyi.md`, `obladnannia.md`, and
  `vidriadzhennia.md` in Ukrainian. In `restricted/`: `executive-leave.md`,
  about annual leave like `annual-leave.md`, carrying the leak marker
  `CONFIDENTIAL-SEED-7731`. Every block holds 64 to 256 tokens, so each
  section is exactly one Chunk: 21 in all.
- `datasets/bilingual-seed/cases.json`: `bilingual-seed` version 1, 16
  cases. `en->en` 6, `uk->uk` 5, `en->uk` 2, `uk->en` 3; identifier 4,
  morphology 3; two cases name the restricted section as ineligible.
- `src/knowledge_service/retrieval/evaluation_dataset.py`:
  - `load_dataset` parses with `json.loads`, refusing duplicate keys and
    `NaN`, then strict pydantic models, then rules a schema cannot express.
    Those rules are unique IDs, a question with a letter or digit, relevant
    evidence in `public/`, ineligible evidence in `restricted/`, safe
    lowercase paths, and at least 2 cases per direction, identifier, and
    morphology slice.
  - `verify_corpus` reads, normalizes, and chunks the corpus with the
    ingestion functions, offline. It requires each reference to match
    exactly one Chunk and each relevant Document's detected language to
    match the case.
  - `run_dataset` checks `limit` and `branches`, verifies, and locks
    `sources`. It refuses a database that holds any Source, then inserts two
    fixed Sources and ingests `public/` (granted to everyone) and
    `restricted/` (granted to nobody) with `synchronize_source`. It then
    asks every case as one fixed `READER` through `retrieve_hybrid`, with
    word search only by default.
  - `render_report` gives per-slice found counts and per-case ranks, with
    case IDs only.
  - Errors: `DatasetError`, with `InvalidDataset`, `DatasetRunRefused`,
    `IneligibleChunkRetrieved`, and `CaseUnanswered`.
- `scripts/run_retrieval_dataset.py`: `--verify-only` needs no database.
  The full run writes `artifacts/evaluations/bilingual-seed.md`, which Git
  ignores. Exit 1 on any `DatasetError`.
- `tests/test_evaluation_dataset.py` (63 tests) and
  `tests/test_evaluation_dataset_integration.py` (6 tests).
- `docs/architecture.md`: the `retrieval` row names the new entry points,
  and a note records why retrieval-only evaluation code lives in
  `retrieval` while the Evaluation module has no package.

Decisions:

- **Cases name Chunks by Document path and heading path.** Chunk IDs are
  new on every ingestion, so they cannot be written into a file.
- **Verification runs offline with the real chunker.** A typo in a
  reference is a data error, caught on every commit without a database,
  instead of showing up as a `missed` rank.
- **Relevance and eligibility stay separate.** Relevant evidence must be
  public; ineligible evidence must be restricted. Eligibility is enforced by
  each search's SQL permission check. `_run_case` adds a second check on
  the way out: any Candidate not from the public Source stops the run,
  however relevant it is.
- **An empty database, fixed IDs, word search by default.** Other
  Documents would change the ranks, and the dense search depends on a paid
  model. The run refuses a database that holds any Source, under a table
  lock, before it writes anything.
- **Raw ranks, not scores.** Recall@k and MRR belong to M05-T08; the report
  keeps the ranks they need.

Review: an adversarial review of the prototype found six problems, all
fixed with regression tests that fail against the unfixed code.

- A question of only symbols passed the checks, then failed the run after
  the corpus was written; it is now refused at load.
- Wrong `branches` were caught only after writing; they are now checked
  first.
- Two runs at once gave a raw `IntegrityError`; the table lock now makes
  the second one a clean `DatasetRunRefused`.
- Errors repeated unknown keys and document paths from the file; they now
  show neither.
- A key written twice was silently accepted; it is now refused.
- A pipe in place of the cases file made the loader wait forever; it is
  now refused.

The finishing Standards and Spec reviews found no blocking problem once the
package placement was recorded in `docs/architecture.md`.

## Verification

Commands, run 2026-11-03:

| Command | Result |
|---|---|
| `uv run pytest tests/test_evaluation_dataset.py -q` | 63 passed |
| `make format-check` | 181 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors, 0 warnings |
| `make test-unit` | 1160 passed |
| `make test-integration` (with the test database URL) | 294 passed |
| `make check` (with the test database URL) | 1454 passed, 3 deselected; `evaluation_dataset.py` 99% covered |
| `make docs-check` | passed |
| `uv run python scripts/run_retrieval_dataset.py --verify-only` | `16 cases verified` |

The one uncovered line raises when a corpus file is quarantined during
ingestion after it passed verification. That can only happen if the file
changes in between.

What the tests prove:

| Test | Proves |
|---|---|
| `test_the_seed_dataset_loads_and_matches_its_corpus` | Success: the real dataset loads, verifies, and covers every slice |
| `test_a_cases_file_that_breaks_a_rule_is_refused` and the file tests | 29 single mistakes and broken, ambiguous, unreadable, or pipe files raise `InvalidDataset` |
| The hypothesis tests and `test_an_error_never_repeats_a_key_or_a_path_from_the_file` | Only `InvalidDataset` is raised, and no planted value appears in its message |
| `test_a_case_that_does_not_match_the_corpus_is_refused`, `test_a_heading_shared_by_two_chunks_is_refused` | Missing documents, wrong headings, wrong languages, and ambiguous headings fail verification |
| `test_a_wrong_setting_is_refused_before_any_work` | Bad `limit` or `branches` raise `DatasetRunRefused` before any connection |
| `test_every_case_runs_and_the_report_names_no_text` | Every case runs; three known matches rank 1; no restricted marker or question in the report or DEBUG logs |
| `test_the_restricted_document_is_stored_but_never_found` | The highest-risk edge: both restricted Chunks are stored, yet a search for their marker as the reader returns nothing |
| `test_a_branch_that_returns_a_restricted_chunk_stops_the_run` | The typed denial: a leaking branch raises `IneligibleChunkRetrieved`, naming the case only |
| `test_a_database_that_is_not_empty_is_refused_before_any_write`, `test_two_runs_at_once_give_one_report_and_one_refusal` | A used database is refused without new rows, even when two runs race |
| `test_a_case_no_branch_answers_raises_a_typed_error` | A refusing branch gives `CaseUnanswered` with the branch's error type |

Security and privacy: the restricted Document is stored with no Access
Grant, and each search applies the reader's permission check in its own
SQL before any Chunk leaves the database. The report holds case IDs,
labels, and ranks. Errors hold case IDs, checked paths, rule names, and
error type names. No question or Document text appears in either, and the
integration test checks the report and the DEBUG logs for the restricted
marker and every question.

The Step 8 report, the repeat-run `diff`, and the deliberate break are
recorded under "Prediction check" above.

Known limits:

- Equal scores are ordered by Chunk ID, which is new on every ingestion.
  The two fresh-database runs matched, but a renamed or added file could
  move a rank without any content change.
- A file edited between verification and ingestion is not checked again.
  A file that cannot be parsed stops the run, but a changed heading only
  shows as `missed`.
- A run that fails after ingesting leaves the database used; the next run
  needs a fresh one.
- `run_dataset` accepts any branch, but it never publishes embeddings.
  M05-T08 must add an embedding step after ingestion before the dense
  branch can answer.
- No seed question matches the restricted section word for word. So the
  run's own leak check is only exercised by the fake-branch test, while the
  SQL check is proven by the marker search.
- One reader is used for every case, so per-user permissions are not
  tested. Sixteen cases are a seed; `docs/evaluation.md` targets about 200.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **What `run_dataset` hides.** A caller never creates Source rows, never
   grants or withholds access, never maps a Candidate back to a case, and
   never thinks about Chunk IDs. They pass a dataset and a corpus folder and
   get ranks back, or a typed reason why not.
2. **Skipping `verify_corpus`.** A typo in a heading, or a section that
   merged with its neighbour after an edit, would silently show as
   `missed`. The report would then blame the search for a mistake in the
   answer key. Nobody would notice unless they reread the case against the
   corpus by hand.
3. **Editing a question after a miss.** The question would then be shaped
   by today's word search, for example by copying its exact words. The
   next method would be measured on questions written for the old one, and
   any improvement it brings on natural wording would disappear from the
   numbers.
4. **Dense search in M05-T08.** The cross-language cases should improve,
   because meaning does not depend on the alphabet. The most surprising
   miss would be `en-uk-per-diem-abroad`, a direct question whose answer is
   a single short section. A miss on the bare identifier `VPN-4031` would
   surprise less, because dense search handles codes worse than word
   search.
5. **A case that could prove me wrong.** In `uk->uk` morphology, a
   question using a rare form, such as "лікарняними", would test whether
   the problem is only common endings. A future Ukrainian stemmer could
   also turn a current miss into a hit for the wrong reason, so it should
   be checked case by case, not by the count alone.
