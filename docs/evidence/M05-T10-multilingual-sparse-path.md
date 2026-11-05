# M05-T10 — Multilingual sparse path evidence

## Invariant

`search_sparse_chunks` returns only Chunks the reader may see: the
permission check runs in the same SQL statement as the ranking, so a
restricted Chunk with the best score is never ranked, counted, or returned.
Weights come only from the model that built the index, and every weight the
model sends is checked before it is used. No input text appears in an
error, a log record, a server answer, or the report. The production default
is unchanged: no migration, and no default search uses sparse weights.

## Expected failing check

Before the change, `uv run pytest tests/test_sparse_search.py` fails to
import `knowledge_service.retrieval.sparse_search`.

## Predictions

These were written before the run in this repository. They are not fully
blind: the overall and `uk->uk` MRR of a prototype run were mentioned in
chat while the lesson was prepared.

- `uk->uk` and morphology, sparse against PostgreSQL: sparse MRR is higher
  than PostgreSQL's in both slices by at least 0.3, because the model's
  word pieces let "відпустка" and "відпустки" share a token, while
  PostgreSQL has no Ukrainian dictionary.
- Cross-language (`en->uk`, `uk->en`): both searches stay below 0.5 MRR in
  both slices, because a question and a Chunk in different languages share
  almost no word pieces.
- Identifiers such as "VPN-4031": sparse ranks the expected Chunk first in
  every identifier case, because the code's pieces are rare and get a high
  weight.
- Median latency per question, sparse against PostgreSQL: sparse is at
  least 10 times slower but under 300 ms, because every question needs a
  model call on the CPU, while PostgreSQL only reads its own index.
- My expected decision, keep or reject, and the one number that would
  change it: keep it as a prototype and a candidate second branch for
  Ukrainian, without changing the default; I would reject it if sparse
  overall MRR were less than 0.2 above PostgreSQL's.

## Design

Completed on 2026-11-05.

Changed files:

- `ml/bge_m3_server/bge_m3_server.py`: pins `sparse_linear.pt` by SHA-256
  next to the other files, and serves `POST /v1/sparse` under the same
  input, token, and request limits as `/v1/embeddings`. Each answer names
  model, revision, vocabulary size, input tokens, and per input the
  `[token ID, weight]` pairs in token order, special tokens left out.
- `ml/bge_m3_server/test_bge_m3_server.py`: the endpoint, its limits, its
  refusal of bad weights, a quiet failure, and a real-model check that a
  text's weights do not depend on its batch.
- `src/knowledge_service/embeddings/sparse.py` (new): the `SparseModel`
  port, `SparseModelIdentity`, `SparseVector`, `MAX_SPARSE_WEIGHT`, and
  `checked_sparse_vectors`.
- `src/knowledge_service/embeddings/local_adapter.py`:
  `LocalEmbeddingModel.encode_sparse` and `sparse_identity`.
- `src/knowledge_service/retrieval/fusion.py`: `RetrievalBranch.SPARSE`.
- `src/knowledge_service/retrieval/sparse_search.py` (new):
  `build_sparse_index`, `search_sparse_chunks`, `sparse_search_statement`,
  `sparse_branch`.
- `src/knowledge_service/retrieval/evaluation_dataset.py`: time per case,
  `compare_sparse`, `render_sparse_comparison`.
- `scripts/run_retrieval_dataset.py`: `--sparse`, exclusive with the other
  model flags.
- Tests: `tests/fakes.py` (`word_weights`, `FakeSparseModel`),
  `tests/local_stubs.py` (`/v1/sparse`), `tests/test_local_embeddings.py`,
  `tests/test_sparse_search.py` (new), `tests/test_sparse_search_integration.py`
  (new).
- `docs/architecture.md`: the `embeddings` and `retrieval` rows and the
  `ml/` paragraph.

Why BGE-M3 sparse and not a separate search engine: the task allows
either. A separate engine such as OpenSearch with a Ukrainian analyser is a
new service to run, secure, and keep in sync with PostgreSQL, and it would
need its own copy of the permission rules. BGE-M3 was already pinned and
running from M05-T09; its sparse output needed one more 3.5 KB file.

Why the index lives in memory: the task is a prototype that must not
change the production default. Storing weights would need a migration and
an ingestion step before we know whether the path is worth it. The index
is built once per run from every available Chunk, restricted ones
included, so that the permission check has something to remove; it is
dropped when the run ends.

How the permission check stays in the same statement: Python scores only
the Chunks that share a token with the question, then sends the Chunk IDs
and scores to PostgreSQL as two array parameters. `unnest` turns them into
rows for that one statement, which joins Chunks and documents and puts
`eligible_chunks_predicate` in the same WHERE as `ORDER BY score DESC,
chunk_id` and `LIMIT`. Nothing is written to the database.

Why every weight is checked and bounded: the adapter refuses an answer
from another model, revision, or vocabulary; weights that are not positive
finite floats, out of token order, or above 1000; more weights than a text
has tokens; and compressed answers. The index checks the vectors again
when it is built, and a search refuses weights from a model other than the
one that built the index. All of these refusals happen before PostgreSQL
is used.

## Verification

The expected failing check held before Step 6: `tests/test_sparse_search.py`
could not import `knowledge_service.retrieval.sparse_search`.

Gates, after Step 10, in order:

| Command | Result |
|---|---|
| `uv run pytest tests/test_sparse_search.py tests/test_local_embeddings.py tests/test_embedding_model_contract.py -q` | 91 passed, 1 deselected |
| `make format-check` | 193 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors |
| `make test-unit` | 1278 passed |
| `make test-integration` (test database URL from `scripts/test_database_url.py`) | 303 passed |
| `make ml-check` | 0 errors, 29 passed |
| `make docs-check` | passed |
| `git diff --check HEAD` | clean |

Earlier narrow checks: `tests/test_local_embeddings.py` 50 passed and the
contract suite 18 passed, 1 deselected (Step 5); `tests/test_sparse_search.py`
23 passed and `tests/test_sparse_search_integration.py` 3 passed (Step 8).

The real run, its repeatability, and the deliberate break are under
"Decision". No migration was added, `retrieve_hybrid` and the API are
unchanged, and only `compare_sparse`, reached through `--sparse`, uses
`RetrievalBranch.SPARSE`.

Privacy: the integration test captures logs at DEBUG and checks that
neither the report nor the logs contain any question or the restricted
marker `CONFIDENTIAL-SEED-7731`; the server logs only counts and error type
names.

Latency is measured per case around the whole retrieval call, so it
includes orchestration as well as the search; both runs are measured the
same way. The sparsevec size is an estimate (8 bytes per weight plus a
16-byte header per Chunk), not a measured table size.

Known limits: weights are taken from the Chunk text only, while PostgreSQL
also weights the title and headings; literal `<s>` or `</s>` in a text are
read as special tokens; a batch pads every text to its longest one; a
sparse branch error propagates instead of degrading a hybrid search; the
sparse log record names the dense identity; 16 cases are few.

Review: an adversarial review of the prototype, before the guide was
written, found no broken guarantee and seven smaller problems. Four were
fixed, each with a regression test that failed before the fix: weights up
to 1.7e308 summed to infinity and tied every Chunk, so weights are now at
most 1000; the index trusted the model's vectors, so it now checks them
like a query; one answer could carry 250,002 weights for a one-word text,
so a text may now have no more weights than tokens; a 1 MB compressed
answer took about 145 MB of memory while it was unpacked, before the 8 MB
cap was checked, so compressed answers are refused unread. The other three
are the known limits above.

The finishing Spec review found every spec item met and no gap. The
finishing Standards review found no blocking violation and left these
non-blocking points:

- `FakeSparseModel.count_tokens` splits on whitespace while `word_weights`
  makes one token per `\w+` match, so "VPN-4031" counts as 1 token but
  gets 2 weights; the fake can undercount, which the port forbids.
- The weight rules exist twice, in `sparse.py` and in `sparse_search.py`,
  so one copy could change without the other.
- `word_weights` and the server's `_sparse_pairs` annotate int token IDs as
  `float`.
- ADR 0013's package table still describes `retrieval` by its M04
  contents.

## Decision

Run on 2026-11-05 with the pinned BGE-M3 server on port 8766 (another
program held 8765), twice on fresh databases. Everything above
"## Latency" was the same in both reports. The summary table, copied
because `artifacts/` is ignored by Git:

| Slice | Cases | lexical R@10 | lexical MRR | sparse R@10 | sparse MRR |
|---|---:|---:|---:|---:|---:|
| all | 16 | 0.312 | 0.312 | 0.750 | 0.688 |
| en->en | 6 | 0.667 | 0.667 | 1.000 | 0.917 |
| en->uk | 2 | 0.000 | 0.000 | 0.000 | 0.000 |
| uk->en | 3 | 0.000 | 0.000 | 0.333 | 0.333 |
| uk->uk | 5 | 0.200 | 0.200 | 1.000 | 0.900 |
| plain | 9 | 0.333 | 0.333 | 0.556 | 0.444 |
| identifier | 4 | 0.250 | 0.250 | 1.000 | 1.000 |
| morphology | 3 | 0.333 | 0.333 | 1.000 | 1.000 |

Cost, from the report:

- Indexed Chunks: 21, restricted ones included.
- Stored weights: 1172, 55.8 per Chunk on average.
- As pgvector sparsevec: about 9.5 KiB for this corpus.
- Per question: word search runs one SQL statement; sparse search also
  calls the model once, so it needs the BGE-M3 server running.

Latency, wall time per case (it changes from run to run):

| Run | Index build | lexical median / slowest ms | sparse median / slowest ms |
|---|---:|---:|---:|
| first | 1.5 s | 5.1 / 10.4 | 106.7 / 234.3 |
| second | — | 4.7 / 38.2 | 106.6 / 130.1 |

For context, M05-T09 measured local BGE-M3 dense search on the same 16
cases at MRR 1.000 in every slice, and dense + lexical at 0.969.

**Keep**, as a prototype and the candidate second branch next to dense,
without changing any default.

- Quality: sparse beats PostgreSQL word search where word search is weak:
  `uk->uk` 0.900 against 0.200, morphology and identifiers 1.000 against
  0.333 and 0.250, and overall 0.688 against 0.312. It does not beat dense,
  which already found every case, and it does not help `en->uk`.
- Latency and cost: about 107 ms per question against about 5 ms, almost
  all of it the model call; the weights are small, about 9.5 KiB for 21
  Chunks.
- Resilience: today word search still answers when the model server is
  down. If sparse replaced it, a model outage would take down both
  branches, so sparse cannot replace word search until the server is as
  reliable as PostgreSQL.

What "keep" means next: ask the server for dense and sparse weights in one
pass, so a question costs one model call; store the weights as pgvector
`sparsevec` behind a migration, written during ingestion; then measure
dense + sparse fusion against dense + lexical on a larger dataset than 16
cases.

**Falsified assumption.** I assumed a question and a Chunk in different
languages share almost no word pieces, so sparse would find nothing across
languages. The report shows `uk-en-error-vpn-4031` at rank 1 for sparse
(word search missed it): the identifier is the same piece in both
languages. So the assumption is wrong for identifiers; it holds for plain
wording, where `en-uk-per-diem-abroad`, `en-uk-who-pays-sick-leave`,
`uk-en-annual-leave-days`, and `uk-en-lost-receipt` were all missed.

The deliberate break: with `eligibility=sa.true()` in
`search_sparse_chunks`, the integration tests gave 2 failed, 1 passed;
`test_every_case_runs_with_words_and_with_sparse_weights_quietly` and
`test_the_best_scored_chunk_is_restricted_and_still_never_returned` both
stopped with `IneligibleChunkRetrieved`. After the change was undone, the
file matched the verified prototype byte for byte and all 3 passed.

## Reflection

Claude implemented Steps 1–10 at the learner's request and drafted the
predictions, the decision, and this reflection; they are not blind,
because Claude had seen the prototype run. The learner should rewrite them
in their own words.

Prediction check:

| Prediction | Measured | Held? |
|---|---|---|
| `uk->uk` and morphology at least 0.3 above PostgreSQL | 0.900 vs 0.200; 1.000 vs 0.333 | yes |
| Cross-language below 0.5 for both | sparse 0.000 and 0.333, PostgreSQL 0.000 and 0.000 | yes, but the reason was wrong for identifiers |
| Every identifier case at rank 1 | identifier MRR 1.000 | yes |
| At least 10 times slower, under 300 ms | median 106.7 vs 5.1 ms; slowest 234.3 ms | yes |
| Keep unless overall MRR is less than 0.2 above PostgreSQL | 0.688 vs 0.312 | keep |

1. What does `search_sparse_chunks` hide from its callers? The postings,
   how scores are summed, the `unnest` parameters, the permission check,
   the tie order by Chunk ID, and the refusals of a bad limit, a foreign
   model, and bad weights. A caller passes an index, a question's weights,
   and a reader, and gets ranked, permitted Candidates.
2. Which invariant is easiest to break in a later optimisation? Ranking in
   the same statement as the permission check. Ranking in Python and
   asking PostgreSQL only for the text of the top 10 would let restricted
   Chunks take places in the top 10 and then disappear, so a reader would
   get fewer results, ranks with gaps, and a `truncated` flag that hides
   how many Chunks were filtered.
3. Why check weights twice? The adapter's check protects the wire. The
   index's check protects against any other `SparseModel`, such as a fake
   or a future adapter, that does not run the adapter's check; without it
   a NaN or infinite weight would reach the scores.
4. Which prediction was wrong? The reasoning behind the cross-language
   prediction: `uk-en-error-vpn-4031` was found at rank 1, because an
   identifier is the same piece in both languages.
5. What would have to be true before sparse replaces PostgreSQL word
   search for real users? Fused dense + sparse MRR at least as good as
   dense + lexical on a dataset much larger than 16 cases, and a test that
   a model server outage still leaves one branch answering.
