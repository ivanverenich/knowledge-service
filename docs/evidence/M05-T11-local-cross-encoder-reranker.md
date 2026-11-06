# M05-T11 — Local cross-encoder reranker evidence

## Invariant

`rerank_candidates` returns exactly the Candidates it was given, each once,
in a new order: it never adds, drops, or reads a Chunk, so every Candidate
it returns passed the permission check of the search that found it, and no
restricted text ever reaches the reranker. Scores come only from the pinned
reranker revision, and every score is checked before it is used. One call
finishes within its deadline or raises `RerankerTimeout`. No input text
appears in an error, a log record, a server answer, or the report. No
default search reranks yet.

## Expected failing check

Before the change, `uv run pytest tests/test_reranking.py` fails to import
`knowledge_service.retrieval.reranking`.

## Predictions

These were written before the run in this repository. They are not blind:
the MRR change, the case that moved, and the CPU and MPS latency of a
prototype run were mentioned in chat while the lesson was prepared.

- MRR of fused retrieval, before and after reranking: fused MRR rises from
  below 1.0 to 1.0, because the reranker reads the question and the Chunk
  together and fixes the one case where the expected Chunk sits at rank 2.
- Cases that move down after reranking: none, because fused retrieval
  already ranks almost every expected Chunk first and the reranker agrees
  with it on clear cases.
- Median reranking time per question on `cpu`: between 500 and 1,500 ms,
  because every question needs about ten full model passes, one per
  Candidate, on a 2.3 GB model.
- Median reranking time per question on `mps`: under half the `cpu`
  median, because the GPU runs the ten passes of one batch in parallel.
- Whether reranking is worth its time on this dataset, and the one number
  that would change my mind: not worth enabling by default yet, because it
  fixes one case out of 16 for several times the retrieval time; I would
  change my mind if more than 2 cases moved up with none moving down.

## Design

Completed on 2026-11-06.

Changed files:

- `ml/bge_m3_server/reranker_server.py` (new): the reranker server. It
  pins `BAAI/bge-reranker-v2-m3` at revision
  `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`, checks the SHA-256 of the
  weights and tokenizer files before serving, counts every pair with the
  model's own tokenizer, refuses more than 64 passages, 8192 tokens per
  pair, or 16,384 per request instead of truncating, and returns one raw
  score per passage with model, revision, device, and input tokens.
- `ml/bge_m3_server/test_reranker_server.py` (new): limits, provenance,
  invalid requests and scores, quiet failures, and two real-model tests.
- `ml/bge_m3_server/pyproject.toml` and `uv.lock`: `transformers>=5` as a
  direct dependency; the descriptions name both servers.
- `src/knowledge_service/retrieval/reranking.py` (new): the `Reranker`
  port, `RerankerIdentity`, `RerankLimits`, the typed `RerankerError`
  family, `check_rerank_inputs`, `checked_scores`, `RerankedCandidate`,
  `RerankResult`, and `rerank_candidates`.
- `src/knowledge_service/retrieval/local_reranker.py` (new):
  `LocalRerankerSettings` (`LOCAL_RERANKER_*`) and `LocalReranker`, with
  `score` and `verify_server`.
- `src/knowledge_service/retrieval/evaluation_dataset.py`:
  `compare_reranking`, `RerankComparison`, `render_rerank_comparison`
  ("## Ranking delta" and "## Latency"), and the reranker in the report.
- `scripts/run_retrieval_dataset.py`: `--rerank`, exclusive with the other
  model flags; it checks the server before anything is written.
- Tests: `tests/fakes.py` (`FakeReranker`), `tests/reranker_stubs.py`
  (new), `tests/test_reranker_contract.py` (new),
  `tests/test_local_reranker.py` (new), `tests/test_reranking.py` (new),
  `tests/test_reranking_integration.py` (new).
- `docs/architecture.md`, `Makefile` (`ml-check` help), `.env.example`
  (`LOCAL_RERANKER_URL`).

Why this model and a separate process: bge-reranker-v2-m3 is
multilingual, Apache-2.0, and shares BGE-M3's tokenizer, so the token bound
already trusted for embeddings carries over. It runs in its own process
from the BGE-M3 uv project (ADR 0009), so it can be stopped, restarted, or
moved to a GPU without touching embeddings.

Why the code lives in `retrieval`: reranking is part of the Retrieval
module in `docs/architecture.md`, and ADR 0013 asks for a recorded reason
before a new package; there is none yet.

What a score means: the model's raw logit, any finite number, higher is
more relevant. Scores compare only within one question and one reranker
identity. They are not probabilities and no threshold is applied, so the
reranker only reorders.

Batching and the shared deadline: `rerank_candidates` fills a request in
retrieval order until the next passage would break a limit, then starts
another; ten Candidates go in one request. One deadline starts before
token counting and covers every request; each request gets only the time
left, and a late answer is dropped even when the reranker blocked the
event loop. Any failed request fails the whole call.

Why the reranker only sees eligible Candidates: `rerank_candidates` never
reads the database. It reads every Candidate's fields once, at the start,
from what retrieval returned after its permission check, and returns
exactly those Candidates in a new order.

## Verification

The expected failing check held before Step 4:
`tests/test_reranking.py` could not import
`knowledge_service.retrieval.reranking`.

Gates, after Step 10, in order:

| Command | Result |
|---|---|
| `uv run pytest tests/test_reranker_contract.py tests/test_local_reranker.py tests/test_reranking.py -q` | 83 passed |
| `make format-check` | 202 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors |
| `make test-unit` | 1361 passed |
| `make test-integration` (test database URL from `scripts/test_database_url.py`) | 306 passed |
| `make ml-check` | 0 errors, 51 passed |
| `make docs-check` | passed |
| `git diff --check` and `git diff --cached --check` | clean |

Earlier checks: the server answered the Step 2 request on `cpu` with 44
input tokens and scores 3.658 and -11.008; `make ml-check` 51 passed
(Step 3); `tests/test_reranking_integration.py` 3 passed (Step 8).

The real runs, their repeatability on `cpu`, the `mps` run, and the
deliberate break are under "Decision". No default search reranks:
`retrieve_hybrid` and the API are unchanged, and only `compare_reranking`,
reached through `--rerank`, calls `rerank_candidates`.

Privacy: the integration test captures logs at DEBUG and checks that the
restricted marker `CONFIDENTIAL-SEED-7731` never reaches the reranker and
that neither the report nor the logs contain any question or that marker.
The adapter logs counts, timing, device, and outcome only; the server's
errors name a reason and an index, never a text.

Known limits: only Chunk text is scored, not titles or headings; 16 cases
are few, and fused retrieval already ranked 15 first; the adapter repeats
the HTTP reading code of `LocalEmbeddingModel` instead of sharing it; no
fallback to the fused order exists yet; the server counts tokens outside
its one-at-a-time lock, which is accepted because it listens on loopback
only.

Review: an adversarial review of the prototype found two broken
guarantees and six smaller problems. Both breaks and five of the smaller
problems were fixed, each with a regression test that failed before the
fix: the deadline started after token counting and a reranker that
blocked the event loop still returned late scores, so the deadline now
starts first and a late answer is dropped; the result re-read the caller's
list after the reranker answered, so a list changed meanwhile could slip
in a Candidate nobody checked, so every Candidate is now read once, at the
start, and one whose fields cannot be read is refused without its text; a
timeout that was not a positive number escaped as an untyped error, and a
closed client as a `RuntimeError`; a server could name a device such as
`cpu\n## x` that landed raw in the Markdown report, so only a plain name
is accepted; limits above the server's were accepted and failed on every
large request, so they now fail at start-up; and a reranker that changed
its identity mid-run would have been reported under one name. The server's
comment about its character limit was wrong and was corrected; counting
outside the one-at-a-time lock stays a known limit.

The finishing Spec review found every spec item met and no gap. The
finishing Standards review found no blocking violation and left these
non-blocking points:

- The server counts tokens with the shared fast tokenizer outside its
  lock while scoring uses it inside; overlapping requests can make
  counting fail and refuse a valid pair as `uncountable_text`. The BGE-M3
  server has the same pattern.
- `rerank_candidates` turns any `TimeoutError` raised inside
  `reranker.score` into `RerankerTimeout`, so an adapter bug that raises
  `TimeoutError` would look like a retryable timeout, although the
  docstring says non-`RerankerError` errors propagate unchanged.
- `LocalReranker` refuses only NaN as a timeout, while `_snapshot` refuses
  every non-finite value; infinity is clamped to the 60 s setting, so
  nothing breaks today.
- A bad `LOCAL_RERANKER_URL` ends the script with a pydantic traceback
  instead of a one-line message, as `LOCAL_EMBEDDING_URL` already does.

The Spec review also noted that the report's device comes from one
`verify_server` call before the run, so a server restarted on another
device mid-run would be labelled wrongly.

## Decision

Runs on 2026-11-06, on fresh databases, with the local BGE-M3 server on
port 8766 (8765 was taken) and the reranker on `cpu`, then on `mps`. Two
`cpu` runs were identical above "## Latency", and the `mps` report was
identical to the `cpu` report above "## Latency".

| Slice | Cases | Fused R@10 | Fused MRR | Reranked R@10 | Reranked MRR |
|---|---:|---:|---:|---:|---:|
| all | 16 | 1.000 | 0.969 | 1.000 | 1.000 |
| en->en | 6 | 1.000 | 1.000 | 1.000 | 1.000 |
| en->uk | 2 | 1.000 | 1.000 | 1.000 | 1.000 |
| uk->en | 3 | 1.000 | 1.000 | 1.000 | 1.000 |
| uk->uk | 5 | 1.000 | 0.900 | 1.000 | 1.000 |
| plain | 9 | 1.000 | 0.944 | 1.000 | 1.000 |
| identifier | 4 | 1.000 | 1.000 | 1.000 | 1.000 |
| morphology | 3 | 1.000 | 1.000 | 1.000 | 1.000 |

Ranking delta: `uk-uk-sick-first-day` moved up from rank 2 to rank 1; no
case moved down; 15 cases were unchanged. Moved up: 1; moved down: 0.

Latency per case, 16 requests for 16 cases on every run:

| Step | `cpu` run 1 median / slowest ms | `cpu` run 2 median / slowest ms | `mps` median / slowest ms |
|---|---:|---:|---:|
| retrieval | 120.0 / 152.8 | 113.9 / 155.5 | 118.3 / 147.8 |
| reranking | 857.0 / 1273.9 | 752.0 / 855.1 | 322.3 / 1213.0 |

**Not yet.** Reranking stays an evaluation tool and no default search uses
it:

- Ranking delta: it fixes one case out of 16 and breaks none, so fused MRR
  rises from 0.969 to 1.000; the set is too small and too easy to show a
  gain worth a second model.
- Time: reranking takes about 6 to 7 times retrieval's median on `cpu`
  (752–857 ms against 114–120 ms) and about 2.7 times on `mps` (322 ms
  against 118 ms).
- Failure: `rerank_candidates` is all or nothing, so a stopped reranker
  server fails the whole request; a default search would first need a
  fallback to the fused order, which this task does not build.

Falsified assumption: I assumed the GPU makes every question faster. The
report shows the slowest `mps` case took 1213.0 ms, slower than the
slowest case of the second `cpu` run (855.1 ms), while the `mps` median
was 322.3 ms. So "the GPU is always faster" is wrong: the first requests
on `mps` pay a warm-up cost, and only the median shows the GPU's gain.

Deliberate break: with each request given the full `timeout_seconds`
instead of what was left, `uv run pytest tests/test_reranking.py -q`
reported `1 failed, 24 passed`, and the failure was
`test_one_deadline_covers_every_request`. After undoing the change, it
reported `25 passed`.

## Reflection

Claude implemented Steps 1–10 at the learner's request and drafted the
predictions, the decision, and this reflection; they are not blind,
because Claude had seen the prototype run. The learner should rewrite them
in their own words.

Prediction check:

| Prediction | Measured | Held? |
|---|---|---|
| Fused MRR rises from below 1.0 to 1.0 | 0.969 to 1.000 | yes |
| No case moves down | moved down: 0 | yes |
| `cpu` median 500 to 1,500 ms | 857.0 and 752.0 ms | yes |
| `mps` median under half of `cpu` | 322.3 ms | yes |
| Not worth enabling; change my mind if more than 2 cases moved up with none down | 1 up, 0 down | not yet |

1. What does `rerank_candidates` hide from its callers? The server's
   limits and how texts are split into requests, how many requests are
   sent, the token counting, how one deadline is shared and late answers
   dropped, the checks on every score, the tie order by retrieval rank,
   and the snapshot that protects against a list changed meanwhile. A
   caller passes a question, Candidates, and a timeout, and gets the same
   Candidates in a new order with the reranker's identity.
2. What would sending all requests at once break? This server scores one
   request at a time, so parallel requests would queue behind each other
   on the server while every one of them counts against the same
   deadline; the later ones would time out, and because the call is all
   or nothing, the whole rerank would fail with `RerankerTimeout` where
   sequential requests would have finished.
3. What does a score of zero mean? Nothing fixed: a score is a raw logit
   whose scale depends on the model and the question. Zero can rank first
   for one question and last for another, so a threshold would drop
   different amounts per question and would silently change meaning when
   the model changes; the port only promises an order.
4. Which prediction was wrong? None of the written predictions failed,
   but the belief behind the `mps` prediction did: the slowest `mps` case
   (1213.0 ms) was slower than the slowest case of the second `cpu` run
   (855.1 ms), because the first requests on the GPU pay a warm-up cost.
5. What would have to be true before reranking runs for real users? A
   fused-and-reranked MRR clearly above fused MRR on a dataset much larger
   than 16 cases, a reranking time per question within an agreed budget
   such as 300 ms at the median on the production device, and a tested
   fallback to the fused order when the reranker server is down or slow.
