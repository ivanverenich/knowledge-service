# M05-T09 — Local BGE-M3 embeddings evidence

Completed: 2026-11-05

## Invariant

`LocalEmbeddingModel` keeps every promise of the `EmbeddingModel` port and
the shared contract suite passes for it. It returns vectors only when the
server names the pinned model, revision, and size, so vectors from other
weights never enter the pinned Embedding Space. No input text appears in an
error, a log record, or a server answer. The server serves one pinned,
fingerprint-checked revision on loopback only and refuses, never truncates,
a text over the model's limit.

## Expected failing check

Before the change, `uv run pytest tests/test_local_embeddings.py` fails to
import `knowledge_service.embeddings.local_adapter`.

## Predictions

Drafted by Claude at the learner's request, before any code exists in this
repository; to be rewritten in the learner's own words. These predictions
are not blind: while preparing the lesson, Claude ran the prototype with
the real model and saw its report. The reasoning below comes from M05-T08
and from what BGE-M3 was trained for, but the learner's own predictions are
the honest test.

- **Cross-language slices (`en->uk`, `uk->en`): better than OpenAI.** With
  OpenAI, dense search found every cross-language case in the top 10, but
  late: MRR 0.625 for `en->uk` and 0.533 for `uk->en`, and
  `uk-en-lost-receipt` only at rank 10. BGE-M3 was trained on many
  languages paired by meaning, so it should place the right section at
  rank 1 or 2 in most of these cases and raise both MRR values.
- **The bare identifier "VPN-4031": rank 1, in both directions.** M05-T08
  showed that a short code is not blurred: each code appears in one
  section only. BGE-M3 cuts "VPN-4031" into pieces as OpenAI's model does,
  so `en-en-error-vpn-4031` and `uk-en-error-vpn-4031` should stay at
  rank 1 in the dense run.
- **Two runs of the local model: the same report.** The same weights on the
  same CPU, with the same batches, do the same arithmetic in the same
  order. So the vectors are identical, and identical vectors give identical
  ranks. Word search is repeatable already.
- **Fused search: never better than dense, and worse wherever word search
  ranks a wrong passage first.** If dense already finds the answer at
  rank 1, fusion can only keep it there or push it down. It will hurt on a
  slice where word search puts a wrong passage first, as it did for
  `uk-uk-sick-first-day` in M05-T08. It will tie on identifiers, where both
  searches agree.

### Local and managed side by side

Dense search, from `artifacts/evaluations/bilingual-seed-managed.md`
(OpenAI `text-embedding-3-small`, 1536 dimensions) and
`artifacts/evaluations/bilingual-seed-local.md` (BGE-M3 at the pinned
revision, 1024 dimensions). Same 16 cases, same corpus, fresh databases.

| Slice | Cases | managed R@10 | managed MRR | local R@10 | local MRR |
|---|---:|---:|---:|---:|---:|
| all | 16 | 1.000 | 0.866 | 1.000 | 1.000 |
| en->en | 6 | 1.000 | 1.000 | 1.000 | 1.000 |
| en->uk | 2 | 1.000 | 0.625 | 1.000 | 1.000 |
| uk->en | 3 | 1.000 | 0.533 | 1.000 | 1.000 |
| uk->uk | 5 | 1.000 | 1.000 | 1.000 | 1.000 |
| plain | 9 | 1.000 | 0.761 | 1.000 | 1.000 |
| identifier | 4 | 1.000 | 1.000 | 1.000 | 1.000 |
| morphology | 3 | 1.000 | 1.000 | 1.000 | 1.000 |

Fused (dense + lexical) MRR: managed 0.834, local 0.969 overall. Both lose
on `uk->uk` (1.000 to 0.900) for the same reason: `uk-uk-sick-first-day`
falls from rank 1 to 2.

### Prediction check

Written by Claude at the learner's request after the real run; to be
rewritten in the learner's own words.

- **Cross-language slices better than OpenAI: held.** Dense MRR rose from
  0.625 to 1.000 for `en->uk` and from 0.533 to 1.000 for `uk->en`.
  `uk-en-lost-receipt` moved from rank 10 to rank 1.
- **"VPN-4031" at rank 1 in both directions: held.** `en-en-error-vpn-4031`
  and `uk-en-error-vpn-4031` rank 1 in the dense run; identifier MRR 1.000.
- **Two runs give the same report: held.** `diff` of the two fresh-database
  reports printed nothing.
- **Fused never beats dense, and loses where word search ranks a wrong
  passage first: held.** Fused MRR equals dense on every slice except
  `uk->uk`, where `uk-uk-sick-first-day` falls to rank 2.

**Falsified assumption.** I assumed, from M05-T08, that a managed model
from a large provider would be at least as good as a free model that runs
on a laptop, and that the local model would mainly buy privacy at some cost
in quality. The report shows the opposite on this set: local dense MRR
1.000 on all 16 cases against 0.866 for OpenAI, with every cross-language
case at rank 1 (`en-uk-who-pays-sick-leave` was rank 4 with OpenAI,
`uk-en-lost-receipt` rank 10). So "managed means better" is wrong here;
quality depends on what the model was trained for, and BGE-M3 was trained
on many languages paired by meaning. With 16 cases and a perfect score, the
set can no longer tell which model is better; it needs harder cases.

**Provenance check broken on purpose.** With the revision comparison in
`_check_provenance` commented out, `uv run pytest tests/test_local_embeddings.py -q`
gave 2 failed, 31 passed:
`test_vectors_from_other_weights_are_refused[other revision]` and
`test_the_server_check_sends_no_text_and_refuses_other_weights`. With the
line restored: 33 passed.

## Design

Changed files:

- `ml/bge_m3_server/` (new, its own uv project on Python 3.12):
  `pyproject.toml`, `uv.lock`, `bge_m3_server.py`, `test_bge_m3_server.py`.
  The server pins `BAAI/bge-m3` at revision
  `5617a9f61b028005a4858fdac845db406aefb181`, checks the SHA-256 of
  `pytorch_model.bin`, `tokenizer.json`, and `sentencepiece.bpe.model`
  before it starts, listens on 127.0.0.1 only, and serves `GET /v1/model`
  and `POST /v1/embeddings`. It counts tokens exactly, off the request
  loop, refuses over 64 inputs, 8192 tokens per input, or 16,384 per
  request, encodes one batch at a time, and returns unit-length vectors of
  1024 numbers with the model, revision, and size named in every answer.
- `src/knowledge_service/embeddings/local_adapter.py` (new):
  `LocalEmbeddingSettings` (`LOCAL_EMBEDDING_*`, URL checked at start-up)
  and `LocalEmbeddingModel`, which implements the `EmbeddingModel` port over
  HTTP. Identity `local` / `BAAI/bge-m3@<revision>` / 1024; limits 32
  inputs, 8192 and 16,384 tokens; `verify_server` checks provenance without
  sending text; answers are read up to 8 MB.
- `tests/local_stubs.py` (new), `tests/test_local_embeddings.py` (new, 33
  tests), `tests/test_embedding_model_contract.py` (a `local` case).
- `scripts/run_retrieval_dataset.py`: `--local-embeddings`, exclusive with
  `--managed-embeddings`; checks the server before anything is written;
  writes `artifacts/evaluations/bilingual-seed-local.md`.
- `pyproject.toml`, `uv.lock`: `httpx` moved from dev to runtime
  dependencies (0.28.1 either way). `Makefile`: `ml-check`.
  `.env.example`: `LOCAL_EMBEDDING_URL`. `docs/architecture.md`: the
  `embeddings` row and a paragraph on `ml/`.

Decisions:

- **A separate Python 3.12 process.** ADR 0009 keeps ML libraries such as
  PyTorch out of the 3.14 application. The two sides share only HTTP and
  JSON; neither imports the other.
- **Provenance pinned twice and compared on every answer.** The server
  checks file fingerprints, so a running server holds the pinned files.
  The application keeps its own copy of the revision, puts it in the
  identity, and refuses any answer naming another model, revision, or size
  (an `int`, so `1024.0` does not pass). Vectors from other weights get
  their own Embedding Space or none.
- **An upper bound instead of a tokenizer in the application.** Every token
  covers at least one normalized character, so the longer of the NFKC and
  NFKD lengths plus three added tokens is never below the real count.
  `test_the_application_token_bound_is_never_too_low` checks it against
  every Unicode code point, decomposed and accented forms, and 20,000
  random mixes with the real tokenizer. The cost is smaller batches.
- **Refuse, never truncate.** `sentence-transformers` would cut a long text
  silently and embed only its start. A refusal is visible and typed.

Review: an adversarial review of the prototype found four problems and
three smaller ones, all fixed with regression tests that failed before the
fix.

- NFKC alone undercounted Korean typed as separate letters by up to three
  times; the bound now takes the longer of NFKC and NFKD.
- A proxy set in the environment received the texts; the script's client
  now ignores the environment (`trust_env=False`).
- A server answer was read whole, so a broken server could fill the
  memory; it is now capped at 8 MB.
- One huge text stalled the server; texts are now capped by length and
  counted off the request loop.
- Smaller: `1024.0` passed as `1024`; a broken URL crashed untyped inside a
  request; `verify_server` ignored the configured deadline.

The finishing Spec review found every acceptance criterion met. The
Standards review found nothing blocking. Non-blocking points, left as they
are:

- `LocalEmbeddingSettings` reads only the process environment, not `.env`,
  although `.env.example` lists `LOCAL_EMBEDDING_URL`. A value written in
  `.env` is ignored; export it in the shell instead.
- The client accepts any http(s) host, so loopback-only holds on the server,
  not in the client; a wrong `LOCAL_EMBEDDING_URL` would send texts there.
- Client limits can be set above the server's (64 inputs, 16,384 tokens);
  such batches fail loudly with `EmbeddingRequestRefused`.
- `make ml-check` is not part of `make check`, CI, or pre-commit, so the
  server tests and the revision copy in each project are checked by hand.
- The ML project uses pyright `standard`, not `strict`, and imports
  `huggingface_hub`, `pydantic`, and `tokenizers` without declaring them;
  `uv.lock` pins them today.
- The token-bound test skips without the cached model files; in this run
  it ran and passed (`pytest -m model -rs`: 1 passed).

## Verification

Commands, run 2026-11-05 with no model server running:

| Command | Result |
|---|---|
| `uv run pytest tests/test_local_embeddings.py tests/test_embedding_model_contract.py -q` | 51 passed, 1 deselected |
| `make format-check` | 189 files already formatted |
| `make lint` | All checks passed |
| `make typecheck` | 0 errors, 0 warnings |
| `make test-unit` | 1238 passed |
| `make test-integration` (with the test database URL) | 300 passed |
| `make check` (with the test database URL) | 1538 passed, 3 deselected; `local_adapter.py` 99% covered |
| `make ml-check` | 0 errors; 16 passed |
| `cd ml/bge_m3_server && uv run pytest -m model -rs` | 1 passed (token bound, not skipped) |
| `make docs-check` | passed |
| `git diff --check` | clean |

The uncovered line in `local_adapter.py` is the early return for an empty
batch, which the contract suite reaches only through the port's check.

What the tests prove:

| Test | Proves |
|---|---|
| `test_one_request_carries_the_texts_and_the_request_id`, contract `[local]` | Success: one batch, one request, one vector per input in order |
| `test_vectors_from_other_weights_are_refused` | The highest-risk edge: another model, revision, size, or `1024.0` raises `EmbeddingRequestRefused`, no vector returned |
| `test_each_failure_becomes_one_typed_error` | A refused connection is `EmbeddingUnavailable`; 429, timeouts, 5xx, other statuses each map to one port error |
| `test_a_failure_carries_no_text_in_its_chain_or_the_logs` | A planted text appears in no error, linked error, or log record |
| `test_a_request_over_the_limits_is_refused_and_never_truncated` | The server refuses, and never encodes, a text or batch over the limit |
| `test_a_changed_or_missing_model_file_is_refused` | A changed or missing weight file stops the server |
| `test_the_application_token_bound_is_never_too_low` | The application's bound is never below the real tokenizer's count |

Script checks, on a migrated empty database: with no server,
`--local-embeddings` printed `the embedding model is not ready:
EmbeddingUnavailable()`, exited 1, and left 0 Documents. Against another
program on the same port it printed `EmbeddingRequestRefused('the local
model refused the request with status 404')`. Both model options together
printed `not allowed with argument`. `--verify-only` printed `16 cases
verified`.

### Real run

The server ran on port 8766 (`BGE_M3_PORT=8766`, with
`LOCAL_EMBEDDING_URL=http://127.0.0.1:8766` exported in the shell), because
another program held 8765. Two runs on fresh databases, about 6.5 seconds
each; `diff` of the two reports printed nothing. The summary table from the
local report, copied because `artifacts/` is ignored by Git:

| Slice | Cases | lexical R@10 | lexical MRR | dense R@10 | dense MRR | dense + lexical R@10 | dense + lexical MRR |
|---|---:|---:|---:|---:|---:|---:|---:|
| all | 16 | 0.312 | 0.312 | 1.000 | 1.000 | 1.000 | 0.969 |
| en->en | 6 | 0.667 | 0.667 | 1.000 | 1.000 | 1.000 | 1.000 |
| en->uk | 2 | 0.000 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| uk->en | 3 | 0.000 | 0.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| uk->uk | 5 | 0.200 | 0.200 | 1.000 | 1.000 | 1.000 | 0.900 |
| plain | 9 | 0.333 | 0.333 | 1.000 | 1.000 | 1.000 | 0.944 |
| identifier | 4 | 0.250 | 0.250 | 1.000 | 1.000 | 1.000 | 1.000 |
| morphology | 3 | 0.333 | 0.333 | 1.000 | 1.000 | 1.000 | 1.000 |

The comparison with the managed model, the falsified assumption, and the
deliberate break are under Predictions above. Lexical results match
M05-T08 exactly, as they should: word search does not use the model.

Security and privacy: the local path sends no text off the machine. The
server binds 127.0.0.1, the script's client ignores proxy settings, and no
error, log record, or server answer carries an input text.

Known limits: only dense vectors are used (sparse output is M05-T10); one
batch at a time on the CPU; no login and no Host check on the server, so a
web page could make it work but not read the answer; the token bound is
checked by brute force, not proven; the server ignores the request ID; the
16-case set is now saturated (dense MRR 1.000) and cannot rank better
models.

## Reflection

Drafted by Claude at the learner's request; to be rewritten in the
learner's own words.

1. **What `LocalEmbeddingModel` hides.** The URL, HTTP, status codes,
   timeouts, the size cap on answers, the JSON shape, which revision the
   server runs, and the tokenizer. A caller sees the same port as for
   OpenAI: identity, limits, a token count, and `embed`.
2. **Checking provenance only once.** If the server is restarted with other
   weights during a long run, every later batch would be stored in the
   pinned model's space. Nothing would fail; search quality would quietly
   drop. Only a test like `test_vectors_from_other_weights_are_refused`
   would notice, which is why the check runs on every answer.
3. **The pessimistic token bound.** Today it costs smaller batches and a
   few more requests. A bound that is sometimes too low would let through
   a batch the server refuses, so a run would stop with
   `EmbeddingRequestRefused` on text such as Korean typed letter by letter.
4. **A wrong prediction.** None of the four predictions failed, but they
   were not blind. The assumption the report falsifies is "managed means
   better": the free local model beat OpenAI on cross-language questions.
5. **A question to add.** Real employee questions with typos, mixed
   Ukrainian and English in one sentence, and near-duplicate sections
   (two versions of the same policy), where the right answer is one of
   several close passages, so a perfect score is no longer easy.
