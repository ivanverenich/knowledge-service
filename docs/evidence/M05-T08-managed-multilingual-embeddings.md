# M05-T08 — Managed multilingual embeddings evidence

Completed: 2026-11-05

## Invariant

`compare_branches` stores and embeds the bilingual seed corpus once, in an
empty database, with one embedding model. It then asks every case as one
ordinary reader three ways: lexical only, dense only, and both fused. No
Chunk from `restricted/` is ever returned, even when it is the nearest
vector. The report gives Recall@k and MRR per language direction and query
class, names the configuration, and holds no Question or Document text.

## Expected failing check

Before the change, `uv run pytest tests/test_ranking_metrics.py` fails to
import `knowledge_service.retrieval.ranking_metrics`.

## Predictions

Drafted by Claude at the learner's request, before any code or real run
existed; to be rewritten in the learner's own words. The lesson prototype
ran only with a fake embedding model, whose vectors carry no meaning, so
nothing below is based on real OpenAI results.

- **Biggest gain: the cross-language slices (`en->uk`, `uk->en`).** Word
  search scored 0 there, because "leave" never matches "відпустки". A
  multilingual embedding model places texts with the same meaning close
  together whatever the language, so most of those five cases should now
  be found.
- **The bare identifier "VPN-4031": found, but probably not at rank 1.**
  A code carries little meaning, so its vector is close to every VPN
  section, not only to "Error VPN-4031". Word search finds it at rank 1;
  dense search should find it in the top 3.
- **Morphology: dense search should fix it.** The model reads pieces of
  words, so "лікарняні" and "лікарняного" mean the same thing to it. "Хто
  оплачує лікарняні?" should find "Оплата лікарняного" near the top.
- **Fused does not beat the better single search on every slice.** Where
  word search finds nothing (cross-language), fusion only repeats the
  dense ranks. Where word search returns a wrong passage at rank 1, fusion
  can push the right dense answer down. Fusion should win on identifiers
  and lose or tie somewhere else.
- **Two runs on fresh databases: the same report.** Word search is fully
  repeatable. OpenAI may return slightly different numbers each time, but
  differences in the last digits rarely change the order of passages, so
  the ranks, and therefore the report, should match.

### Prediction check

Written by Claude at the learner's request after the real run; to be
rewritten in the learner's own words.

- **Cross-language slices gain the most: held.** Recall@10 went from 0 to 1
  in both `en->uk` and `uk->en`. They are still the weakest slices by rank:
  dense MRR 0.625 and 0.533, against 1.000 everywhere else.
- **"VPN-4031" found, but not at rank 1: falsified.** I assumed a bare code
  carries little meaning, so its vector would sit near every VPN section.
  The report shows `en-en-error-vpn-4031` and `uk-en-error-vpn-4031` at
  rank 1 in the dense run, and every identifier case at rank 1 (identifier
  MRR 1.000). So the belief that a managed embedding model blurs short codes
  is wrong for this corpus: each code appears in only one section, and the
  model keeps enough of the code's characters to find it.
- **Dense search fixes morphology: held.** All three morphology cases rank
  1 in the dense run; word search found one of them.
- **Fusion does not beat the better single search everywhere: held, and
  more strongly than predicted.** Fusion never beat dense on any slice. It
  tied on identifiers (both 1.000), so the expected identifier win is also
  falsified. It lost on `uk->uk`: `uk-uk-sick-first-day` fell from rank 1
  to 2 (MRR 1.000 to 0.900).
- **Two fresh-database runs give the same report: held.** `diff` of the two
  reports printed nothing.

## Design

Changed files:

- `src/knowledge_service/retrieval/ranking_metrics.py` (new): `recall_at`,
  `reciprocal_rank`, and `mean`, all on exact `Fraction`s, with
  `InvalidMetricInput` for a bad rank list, cut-off, or score list. The
  module imports nothing from the project.
- `src/knowledge_service/retrieval/evaluation_dataset.py`:
  - `compare_branches` checks `limit` and the model's identity, verifies
    the corpus, ingests once, embeds once, then runs lexical, dense, and
    fused in that order and returns three reports.
  - `_ingest(..., embedding=)` also refuses a database that holds any
    Embedding Space, and creates the run's space as current, inside the
    same locked transaction.
  - `_embed_corpus` calls `publish_document_embeddings` for every stored
    Document; anything but `published` raises `CorpusNotEmbedded`.
  - `_run_case` turns any unexpected error into `CaseFailed`.
  - `score_slices`, `render_comparison`, and a `render_report` with a
    configuration block and Recall@1/3/5/10 and MRR per slice.
  - `DatasetReport.embedding` names the model; `SliceScore` holds one
    slice's scores.
- `scripts/run_retrieval_dataset.py`: `--managed-embeddings` runs
  `compare_branches` with `OpenAIEmbeddingModel` and writes
  `artifacts/evaluations/bilingual-seed-managed.md` (ignored by Git). The key
  comes from `KNOWLEDGE_SERVICE_MODEL_API_KEY`; `Settings()` is read only
  after `--verify-only`.
- Tests: `tests/test_ranking_metrics.py` (32), `tests/test_evaluation_dataset.py`
  (70, 7 new), `tests/test_branch_comparison_integration.py` (6).
- `docs/architecture.md`: the `retrieval` row names `compare_branches`,
  `recall_at`, and `reciprocal_rank`.

Decisions:

- **Store and embed once, ask three times.** The run needs an empty
  database, so it cannot be called three times. One store also means the
  three runs search the same vectors; a difference comes from the search
  method alone.
- **Restricted Chunks get vectors too.** The service embeds every Document
  and checks permission at search time. The permission test only proves
  something if a restricted Chunk can be the nearest match. The seed corpus
  is made up, so sending the restricted page to OpenAI exposes nothing real.
- **Exact fractions.** Equal ranks always give equal scores, so two runs can
  be compared with `diff`. The report rounds to three digits only when it
  prints.
- **Errors raised outside `except`.** An error raised inside `except` links
  to the one being handled, and a traceback prints both. The model's or a
  branch's error could quote a Question or a Chunk, so only type names leave.
- **The fused run embeds the Questions again.** It reuses `retrieve_hybrid`
  as it is; with an exactly repeatable provider the dense ranks match.

Review: an adversarial review of the prototype found three problems, all
fixed with regression tests that failed before the fix.

- A search bug escaped as an untyped error group carrying the Question;
  it now becomes `CaseFailed` with type names only.
- A model that broke after its first read escaped untyped after rows were
  written; it now becomes `CorpusNotEmbedded`.
- The check-only mode had started to need valid settings; it no longer
  reads them.

The finishing Spec review found every acceptance criterion met. The
Standards review found nothing blocking and three non-blocking points, left
as they are:

- `test_verify_only_needs_no_settings` passes `timeout=60` to
  `subprocess.run`, but pytest's global 30-second timeout ends it first, and
  it uses a path relative to the repository root.
- `render_comparison` raises a bare `ValueError` for an empty tuple instead
  of a `DatasetError`; the script never passes one.
- `mean` shares its name with `statistics.mean` and is missing from the
  architecture row.

## Verification

Commands, run 2026-11-05:

| Command | Result |
|---|---|
| `uv run pytest tests/test_ranking_metrics.py tests/test_evaluation_dataset.py -q` | 102 passed |
| `make format-check` | 184 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors, 0 warnings |
| `make test-unit` | 1199 passed |
| `make test-integration` (with the test database URL) | 300 passed |
| `make check` (with the test database URL) | 1499 passed, 3 deselected; `evaluation_dataset.py` 99% covered |
| `make docs-check` | passed |
| `uv run python scripts/run_retrieval_dataset.py --verify-only` | `16 cases verified` |

The two uncovered lines are the empty-tuple `ValueError` in
`render_comparison` and the quarantine refusal kept from M05-T07.

What the tests prove:

| Test | Proves |
|---|---|
| `tests/test_ranking_metrics.py` | Exact Recall@k and reciprocal rank on known ranks; bad input raises `InvalidMetricInput`; Recall stays in 0..1, grows with k, and agrees with the reciprocal rank (hypothesis) |
| `test_every_case_runs_three_ways_and_the_report_names_no_text` | Success: every case runs lexical, dense, and fused over all 21 vectors; no Question or restricted marker in the report or the logs |
| `test_the_nearest_chunk_is_restricted_and_still_never_returned` | The highest-risk edge: a restricted Chunk at distance 0 ranks first without the permission check and is absent with it |
| `test_a_model_that_fails_stops_the_run_before_any_case`, `test_a_model_that_breaks_during_embedding_raises_a_typed_error` | A failing model raises `CorpusNotEmbedded`, with no linked error |
| `test_a_database_with_an_embedding_space_is_refused_before_any_write` | A used database is refused with `DatasetRunRefused`, nothing written |
| `test_an_unexpected_error_in_a_case_is_typed_and_quiet` | A branch bug becomes `CaseFailed`, naming types only, with an empty error chain |

### Real run

Configuration: dataset `bilingual-seed` v1 on an empty database, 21 Chunks
embedded (restricted ones included), OpenAI `text-embedding-3-small` at
1536 dimensions, top 10 Candidates, Reciprocal Rank Fusion with k = 60,
one ordinary reader.

The first two attempts stopped with `CorpusNotEmbedded` (`IncompleteEmbedding`)
before any case ran; a direct request with the same key succeeded right
after, and the third attempt passed. This looks like a temporary provider
or new-key problem, and it shows the typed failure works on the real
provider. The report from the passing run, copied because `artifacts/` is
ignored by Git:

#### Retrieval comparison: bilingual-seed v1

- Runs: lexical; dense; dense + lexical.
- Each case keeps its top 10 Candidates; a rank is where a relevant Chunk appears among them.
- Embedding model: openai text-embedding-3-small, 1536 dimensions.
- Fusion: Reciprocal Rank Fusion, k = 60.

##### Recall@10 and MRR by slice

| Slice | Cases | lexical R@10 | lexical MRR | dense R@10 | dense MRR | dense + lexical R@10 | dense + lexical MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| all | 16 | 0.312 | 0.312 | 1.000 | 0.866 | 1.000 | 0.834 |
| en->en | 6 | 0.667 | 0.667 | 1.000 | 1.000 | 1.000 | 1.000 |
| en->uk | 2 | 0.000 | 0.000 | 1.000 | 0.625 | 1.000 | 0.625 |
| uk->en | 3 | 0.000 | 0.000 | 1.000 | 0.533 | 1.000 | 0.533 |
| uk->uk | 5 | 0.200 | 0.200 | 1.000 | 1.000 | 1.000 | 0.900 |
| plain | 9 | 0.333 | 0.333 | 1.000 | 0.761 | 1.000 | 0.706 |
| identifier | 4 | 0.250 | 0.250 | 1.000 | 1.000 | 1.000 | 1.000 |
| morphology | 3 | 0.333 | 0.333 | 1.000 | 1.000 | 1.000 | 1.000 |

Dense run by slice:

| Slice | Cases | Found | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | Degraded |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 16 | 16 | 0.812 | 0.875 | 0.938 | 1.000 | 0.866 | 0 |
| en->en | 6 | 6 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 |
| en->uk | 2 | 2 | 0.500 | 0.500 | 1.000 | 1.000 | 0.625 | 0 |
| uk->en | 3 | 3 | 0.333 | 0.667 | 0.667 | 1.000 | 0.533 | 0 |
| uk->uk | 5 | 5 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 |
| plain | 9 | 9 | 0.667 | 0.778 | 0.889 | 1.000 | 0.761 | 0 |
| identifier | 4 | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 |
| morphology | 3 | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 |

Fused run by slice:

| Slice | Cases | Found | Recall@1 | Recall@3 | Recall@5 | Recall@10 | MRR | Degraded |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all | 16 | 16 | 0.750 | 0.875 | 0.938 | 1.000 | 0.834 | 0 |
| en->en | 6 | 6 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 |
| en->uk | 2 | 2 | 0.500 | 0.500 | 1.000 | 1.000 | 0.625 | 0 |
| uk->en | 3 | 3 | 0.333 | 0.667 | 0.667 | 1.000 | 0.533 | 0 |
| uk->uk | 5 | 5 | 0.800 | 1.000 | 1.000 | 1.000 | 0.900 | 0 |
| plain | 9 | 9 | 0.556 | 0.778 | 0.889 | 1.000 | 0.706 | 0 |
| identifier | 4 | 4 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 |
| morphology | 3 | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0 |

Word search matches the M05-T07 baseline: 5 of 16 found, all at rank 1.
Dense search found all 16. Weak ranks, with one line each:

- `uk-en-lost-receipt`, rank 10: the Ukrainian question about a lost
  receipt is probably close in meaning to several English expense sections, and the
  right one wins only narrowly.
- `en-uk-who-pays-sick-leave`, rank 4: the English question probably sits near every
  Ukrainian sick-leave section; the payment one is not the closest.
- `uk-en-annual-leave-days`, rank 2: another English section ranks
  first.
- `en-uk-per-diem-abroad` ranks 1, so cross-language ranks are not uniformly
  poor.
- Fused: `uk-uk-sick-first-day` fell from 1 to 2, probably because word search ranked
  another sick-leave section first.

No restricted Chunk was returned in any run, so `IneligibleChunkRetrieved`
never fired.

Repeatability: two runs, each on a freshly created and migrated database,
gave byte-identical reports (`diff` printed nothing).

Deliberate break: with `eligibility=sa.true()` in `search_similar_chunks`,
`tests/test_branch_comparison_integration.py` gave 2 failed, 4 passed:
`test_the_nearest_chunk_is_restricted_and_still_never_returned` returned
the restricted Chunk, and `test_every_case_runs_three_ways_and_the_report_names_no_text`
stopped with `IneligibleChunkRetrieved` on `en-en-annual-leave-days`. After
the undo, 6 passed. The paid script was not run with the break in place.

Security and privacy: every Chunk, restricted ones included, is sent to
OpenAI for embedding; the seed corpus is made up. Eligibility is enforced in
each search's SQL, and `_run_case` checks every Candidate again on the way
out. The real-run report and its error output contain none of the 16
Questions and not the restricted marker `CONFIDENTIAL-SEED-7731` (checked by
string search). The API key stays a `SecretStr` until the client is built,
lives only in the Git-ignored `.env`, and is never printed.

Known limits:

- Sixteen cases are a seed. Each cross-language slice has two or three
  cases, so one more case can move its MRR by a lot.
- The fused run embeds the Questions again; with a less repeatable provider
  its dense ranks could differ from the dense-only run.
- One failed case stops the whole comparison after the corpus was embedded,
  and a failed run leaves the database used.
- The summary table has no degraded column; the full reports do.
- Request IDs are fixed per case, so every run sends the same IDs.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **What `compare_branches` hides.** The script never creates the Embedding
   Space, never refuses a used database, never embeds a Document or checks
   that every Document got vectors, never builds the two searches, and
   never decides the order of the three runs. It passes a model and gets
   three reports, or one typed reason why not.
2. **Embedding only public Documents.** The restricted Chunk would have no
   vector, so dense search could never find it, with or without the
   permission check. The test would pass even with the check removed, and
   the deliberate break would no longer fail anything.
3. **Recall@10 up, MRR down.** Returning more varied Candidates, for example
   adding sections from the other language, can pull a missed answer into
   the top 10 while pushing answers that were first down to second or third.
   A person asking reads the first one or two results, so MRR matters more
   to them.
4. **The wrong prediction.** I predicted "VPN-4031" would not rank 1 in
   dense search. I believed an embedding is about meaning only and loses the
   exact characters of a code. The run showed the model keeps enough of a
   rare code to put its one section first.
5. **Where one more case matters most.** `uk->en`, with three cases and an
   MRR of 0.533. A case like `uk-en-lost-receipt`, a Ukrainian question whose
   English answer shares its topic with several sections, could pull the MRR
   lower still; a direct one could raise it. The conclusion "fusion adds
   nothing" would also change with one identifier case whose code appears in
   several sections, where word search might rank the exact one first.
