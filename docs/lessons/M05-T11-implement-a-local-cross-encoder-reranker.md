# M05-T11 — Implement a local cross-encoder reranker

Source task: [M05-T11](../curriculum/milestones/M05-bilingual-hybrid-retrieval/M05-T11-implement-a-local-cross-encoder-reranker.md)

## What you will do

Retrieval finds about ten Candidates per question quickly, but its order is rough: a vector distance or a word match is computed for each Chunk on its own. A reranker is a second, slower model that reads the question and one Candidate's text together and says how well that text answers that question. In this lesson you run a multilingual reranker locally, wrap it behind a small port, and measure whether it puts the right Chunk first more often, and what that costs in time on the CPU and on the Mac's GPU. Nothing in production changes: no default search reranks yet.

- A second model server, `reranker_server.py`, runs `BAAI/bge-reranker-v2-m3` next to the BGE-M3 server and returns one score per passage.
- A port, `Reranker`, says what any reranker promises: limits, score meaning, typed errors, and no text in errors.
- `rerank_candidates` sends Candidates to a reranker in batches it accepts, under one deadline, and returns the same Candidates in a new order.
- `LocalReranker` is the adapter: it talks to the server over HTTP and refuses scores from any other model revision.
- `compare_reranking` and the script's `--rerank` option run the 16 seed questions with fused retrieval, rerank each one, and report the change in ranks and the time it took.

| The task asks for | Where it happens |
|---|---|
| A bounded reranker port | Step 4: `Reranker`, `RerankLimits`, the checks before sending, `rerank_candidates` |
| A local multilingual adapter | Steps 2 and 5: the reranker server and `LocalReranker` |
| Batch sizing | Step 4: `rerank_candidates` splits Candidates by passage count and tokens |
| Score semantics | Step 4: a score is the model's raw relevance number, higher is better, comparable only within one question and one model |
| Deadlines | Step 4: one deadline for all requests of one call; each request gets only the time left |
| Model provenance | Steps 2 and 5: pinned revision and file fingerprints on the server, checked on every answer by the adapter |
| Contract and evaluation tests: ranking delta, CPU/GPU latency | Steps 6 and 9: the shared contract tests, the "Ranking delta" and "Latency" sections, runs on `cpu` and `mps` |
| Deterministic tests: success, highest-risk edge, typed failure | Steps 6 and 8: reranking reorders; restricted text never reaches the reranker; a failing reranker raises a typed error |
| No seeded sensitive text in telemetry | Steps 3, 6, and 8: answers, errors, logs, and reports are checked for a planted text |

This lesson includes coding: Steps 2 to 8 write code. Steps 1, 9, and 10 do not involve code. Step 9 runs both models locally; it costs nothing, but the reranker download is about 2.3 GB.

Terms used in this lesson:

- **Bi-encoder:** a model that turns the question and each Chunk into vectors separately, like BGE-M3 in dense search. Chunks are encoded once, ahead of time, so search is fast.
- **Cross-encoder (reranker):** a model that reads the question and one passage together, as one input, and returns one number. It sees how the words of both relate, so it is sharper, but it must run once per pair at question time.
- **Logit:** the raw number a classifier outputs before it is turned into a probability. Here, higher means more relevant; −11 means "clearly not", +4 means "clearly yes".
- **Pair tokens:** the tokens of `<s> question </s></s> passage </s>`, the single input the reranker reads.
- **Batch:** the passages sent in one request. Fewer, larger batches cost less overhead; the server's limits cap their size.
- **MPS:** Metal Performance Shaders, the Mac's GPU backend in PyTorch. `cpu` and `mps` are the two devices you will compare.

## Before you begin

This section explains the choices behind the code, so each step makes sense when you reach it.

- **Why `BAAI/bge-reranker-v2-m3`.** It is multilingual, built on BGE-M3, and Apache-2.0 licensed. It shares BGE-M3's tokenizer and vocabulary, so the token bound you already trust carries over. It has about 568 million parameters, about 2.3 GB of weights. The server pins revision `953dc6f6` and checks the SHA-256 of the weights and tokenizer files before it serves anything.
- **Where the model runs.** In a second process, `ml/bge_m3_server/reranker_server.py`, on port 8767. It reuses the BGE-M3 server's uv project, because it needs the same libraries (ADR 0009 keeps model runtimes out of the application's Python). A separate process means the reranker can be stopped, restarted, or moved to a GPU machine without touching embeddings. `transformers` is now named as a direct dependency, because the server imports it.
- **Where the code lives.** In the `retrieval` package, because reranking is part of the Retrieval module in `docs/architecture.md`. ADR 0013 asks for a recorded reason before a new package is created, and there is none yet. `retrieval/reranking.py` holds the port and `rerank_candidates`; `retrieval/local_reranker.py` holds the adapter.
- **Score semantics.** A score is the model's raw logit: any finite number, higher is more relevant. Scores compare only within one question and one model identity. They are not probabilities, and no threshold is applied, so a reranker can only reorder, never decide that a Candidate is "irrelevant enough" to drop.
- **Only a new order.** `rerank_candidates` returns exactly the Candidates it was given, each once. Equal scores keep the retrieval order. It never reads the database, so every Candidate it returns already passed the permission check of the search that found it. A restricted Chunk never reaches the reranker, because retrieval never returned it: authorization happens before any text leaves storage.
- **Batch sizing.** The server accepts at most 64 passages, 8192 tokens per pair, and 16,384 tokens per request. The adapter's defaults are stricter: 16 passages per request. `rerank_candidates` fills a request in retrieval order until the next passage would break a limit, then starts another, so ten Candidates usually go in one request.
- **Counting tokens without a tokenizer.** The application has no tokenizer. `LocalReranker.count_pair_tokens` adds the upper bounds of both texts, the same bound as `LocalEmbeddingModel.count_tokens`: characters after normalization plus three. A pair adds four special tokens and two word-start markers, exactly the six the two texts add alone. A server test checks the bound against the real tokenizer.
- **Refuse, never truncate.** As with embeddings, the server refuses a pair that is too long instead of cutting it. A cut passage would be scored on its start only, and the score would look as trustworthy as any other.
- **One deadline.** `rerank_candidates` gets one `timeout_seconds` for the whole call, counting tokens included. Each request gets only the time left, and the call raises `RerankerTimeout` once the deadline has passed. A reranker that blocks the event loop cannot be interrupted, but its late answer is dropped all the same. A caller that must answer anyway can keep the retrieval order; that choice belongs to the caller, so it is not hidden here.
- **Read once.** `rerank_candidates` reads every Candidate's fields once, at the start, and uses only that copy. If the caller's list changed while the reranker was busy, the result still holds exactly what was checked.
- **All or nothing.** If any request fails, the call raises and returns nothing. A half-reranked list would mix two orders without telling anyone.
- **Measuring.** The report shows each case's ranks before and after reranking ("Ranking delta"), how many moved up or down, and the median and slowest time per case for retrieval and for reranking, with the device. You run it once with `RERANKER_DEVICE=cpu` and once with `RERANKER_DEVICE=mps`. The first request on a device is slower, because the model warms up; the median hides that, the slowest time shows it.
- **Known limits.** Only Chunk text is scored, not titles or headings. Sixteen cases are few, and fused retrieval already ranks 15 of them first, so the room for improvement is one case. The adapter repeats the HTTP reading code of `LocalEmbeddingModel` instead of sharing it, so M05-T09 code stays untouched; a later task can extract it. No default search reranks yet: M05-T12 adds an LLM reranker and M05-T13 compares the two.

## Walkthrough

### Step 1 — No coding in this step: write the invariant and your predictions

Before any code exists, write down what must be true when the lesson is done and what you expect the reranker to do. Predictions written now, before the run, are what Step 9 checks.

Create `docs/evidence/M05-T11-local-cross-encoder-reranker.md` with this skeleton, then replace the prediction prompts with your own sentences:

```markdown
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

- MRR of fused retrieval, before and after reranking: ...
- Cases that move down after reranking: ...
- Median reranking time per question on `cpu`: ...
- Median reranking time per question on `mps`: ...
- Whether reranking is worth its time on this dataset, and the one number that would change my mind: ...

## Design

## Verification

## Decision

## Reflection
```

Check: the file exists, and every prediction is a full sentence that a number in Step 9 can prove wrong.

### Step 2 — Coding step: run the reranker as its own server

This step adds a second model server that scores question and passage pairs.

**In plain words:** The new server loads the pinned reranker once, after checking the fingerprints of its files. It takes one question and a list of passages, counts every pair exactly with the model's own tokenizer, refuses anything over the limits, and returns one score per passage in order, naming the model, the revision, and the device it ran on. Errors and logs never repeat the texts.

| Piece | What it does |
|---|---|
| `MODEL_ID`, `REVISION`, `FILE_HASHES` | the pinned model, and the SHA-256 of its weights and tokenizer files |
| `MAX_PASSAGES`, `MAX_PAIR_TOKENS`, `MAX_REQUEST_TOKENS` | 64 passages, 8192 tokens per pair, 16,384 per request |
| `Scorer` | what the server needs from a model: count a pair, score passages; a fake in tests |
| `GET /v1/model` | provenance, device, and limits, with no text |
| `POST /v1/rerank` | checks limits, scores one request at a time, refuses scores that are not one finite float per passage |
| `BgeRerankerScorer` | the real model through `transformers`, in batches of 8, never truncating |
| `_check_limits` | refuses an empty question or passage, an uncountable pair, a pair or request over the limits, naming only the index |

Create `ml/bge_m3_server/reranker_server.py`:

```python
"""A local cross-encoder reranker server: the ML runtime side of the seam.

A cross-encoder reads a question and one passage together and returns one
number: how well the passage answers the question. It is slower than
comparing vectors, because every pair goes through the whole model, so it
only reorders the few Candidates retrieval already found.

This server runs `BAAI/bge-reranker-v2-m3`, a multilingual reranker built on
BGE-M3, on Python 3.12, next to the BGE-M3 server and with the same
libraries (ADR 0009). The service reaches it over HTTP through
`knowledge_service.retrieval.local_reranker`.

The server promises:

- **Pinned provenance.** It loads exactly one revision and checks the
  SHA-256 of the weight and tokenizer files before serving. Every answer
  names the model, revision, and device, so the client can refuse scores
  from anything else and a report can say where they were computed.
- **Exact limits, never truncation.** It counts each question and passage
  pair with the model's own tokenizer and refuses a request over 64
  passages, 8192 tokens per pair, or 16,384 tokens in total, naming only
  the index. A cut passage would be scored on its start alone.
- **Raw scores.** One finite number per passage, in order: the model's
  logit. Higher means more relevant; scores compare only within one
  question and one model, and are not probabilities.
- **No text in errors or logs.** Error bodies hold a reason and an index.
- **Loopback only.** It has no authentication, so it listens on 127.0.0.1.

Run it from the repository root with
`uv run --project ml/bge_m3_server python ml/bge_m3_server/reranker_server.py`.
"""

import asyncio
import hashlib
import logging
import math
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Protocol

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("reranker_server")

MODEL_ID = "BAAI/bge-reranker-v2-m3"
REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
MAX_PASSAGES = 64
MAX_PAIR_TOKENS = 8192
MAX_REQUEST_TOKENS = 16_384
# Counting a very long text is slow, so one longer than this is refused before
# it is counted, whatever its token count; real passages are far shorter.
MAX_TEXT_CHARACTERS = MAX_PAIR_TOKENS * 16
SCORE_BATCH_SIZE = 8
# SHA-256 of the large files at REVISION, from the Hugging Face file listing.
FILE_HASHES = {
    "model.safetensors": "d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286",  # noqa: E501  # gitleaks:allow
    "tokenizer.json": "69564b696052886ed0ac63fa393e928384e0f8caada38c1f4864a9bfbf379c15",  # noqa: E501  # gitleaks:allow
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",  # noqa: E501  # gitleaks:allow
}
MODEL_FILES = [
    *FILE_HASHES,
    "config.json",
    "special_tokens_map.json",
    "tokenizer_config.json",
]


class Scorer(Protocol):
    """The model behind the server; a fake in tests, the reranker in production."""

    def count_pair_tokens(self, query: str, passage: str) -> int:
        """Count the pair exactly in the model's tokens, special tokens included."""
        ...

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        """Return one raw relevance score per passage, in order."""
        ...


class ModelFilesChanged(RuntimeError):
    """A downloaded file does not match its pinned hash; nothing is served."""


class RerankRequest(BaseModel):
    """The request body: one question and the passages to score, nothing else."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: Annotated[str, Field(max_length=MAX_TEXT_CHARACTERS)]
    passages: list[Annotated[str, Field(max_length=MAX_TEXT_CHARACTERS)]] = Field(
        min_length=1, max_length=MAX_PASSAGES
    )


def create_app(scorer: Scorer, *, device: str) -> FastAPI:
    """Build the server around one scorer running on `device`."""
    app = FastAPI(title="reranker-server", docs_url=None, redoc_url=None)
    # One request at a time: the model uses every core for one batch already.
    busy = asyncio.Lock()

    @app.exception_handler(RequestValidationError)
    async def _invalid(_: Request, __: RequestValidationError) -> JSONResponse:
        # The default handler repeats the input, which may be document text.
        return _refusal(422, "invalid_request")

    @app.get("/v1/model")
    async def model() -> dict[str, object]:
        return {
            **_provenance(device),
            "max_passages": MAX_PASSAGES,
            "max_pair_tokens": MAX_PAIR_TOKENS,
            "max_request_tokens": MAX_REQUEST_TOKENS,
        }

    @app.post("/v1/rerank")
    async def rerank(body: RerankRequest) -> JSONResponse:
        # Counting takes time on long texts; off the event loop.
        checked = await asyncio.to_thread(
            _check_limits, scorer, body.query, body.passages
        )
        if isinstance(checked, JSONResponse):
            return checked
        failure: str | None = None
        scores: object = None
        async with busy:
            try:
                scores = await asyncio.to_thread(
                    scorer.score, body.query, body.passages
                )
            except Exception as error:
                # Its message or traceback could quote a text; keep the type.
                failure = type(error).__name__
        if failure is None and not _valid(scores, len(body.passages)):
            failure = "InvalidScores"
        if failure is not None:
            logger.warning("reranking failed error=%s", failure)
            return _refusal(500, "reranking_failed")
        logger.info("reranked passages=%d tokens=%d", len(body.passages), checked)
        return JSONResponse(
            {**_provenance(device), "input_tokens": checked, "scores": scores}
        )

    return app


class BgeRerankerScorer:
    """bge-reranker-v2-m3 through transformers: one logit per pair."""

    def __init__(self, folder: Path, device: str) -> None:
        # Imported here, so tests with a fake scorer never load PyTorch.
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch: Any = torch
        self._tokenizer: Any = AutoTokenizer.from_pretrained(folder)
        model: Any = AutoModelForSequenceClassification.from_pretrained(folder)
        self._model: Any = model.to(device).eval()
        self._device = device

    def count_pair_tokens(self, query: str, passage: str) -> int:
        return len(self._tokenizer(query, passage)["input_ids"])

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        scores: list[float] = []
        for start in range(0, len(passages), SCORE_BATCH_SIZE):
            batch = list(passages[start : start + SCORE_BATCH_SIZE])
            # The limits were checked, so nothing here is ever cut.
            features = self._tokenizer(
                [query] * len(batch),
                batch,
                padding=True,
                truncation=False,
                return_tensors="pt",
            )
            features = {
                name: value.to(self._device) for name, value in features.items()
            }
            with self._torch.inference_mode():
                logits = self._model(**features).logits.view(-1).float()
            scores += [float(value) for value in logits.tolist()]
        return scores


def download_model() -> Path:
    """Fetch the pinned revision (or reuse the cache) and check every hash."""
    from huggingface_hub import snapshot_download

    folder = Path(
        snapshot_download(MODEL_ID, revision=REVISION, allow_patterns=MODEL_FILES)
    )
    check_files(folder)
    return folder


def check_files(folder: Path) -> None:
    """Raise `ModelFilesChanged` unless every pinned file has its pinned hash."""
    for name, expected in FILE_HASHES.items():
        path = folder / name
        if not path.is_file() or _sha256(path) != expected:
            raise ModelFilesChanged(f"{name} does not match the pinned hash")


def _check_limits(
    scorer: Scorer, query: str, passages: list[str]
) -> int | JSONResponse:
    """Return the request's total tokens, or the refusal of the first bad input."""
    if not query.strip():
        return _refusal(422, "empty_query")
    total = 0
    for index, passage in enumerate(passages):
        if not passage.strip():
            return _refusal(422, "empty_passage", index)
        tokens = _count(scorer, query, passage)
        if tokens is None:
            return _refusal(422, "uncountable_text", index)
        if tokens > MAX_PAIR_TOKENS:
            return _refusal(413, "pair_too_long", index)
        total += tokens
        if total > MAX_REQUEST_TOKENS:
            return _refusal(413, "request_too_long", index)
    return total


def _valid(scores: object, expected: int) -> bool:
    # A NaN cannot be written as JSON, and a short list would be refused later.
    return (
        isinstance(scores, list)
        and len(scores) == expected
        and all(type(s) is float and math.isfinite(s) for s in scores)
    )


def _count(scorer: Scorer, query: str, passage: str) -> int | None:
    # A tokenizer error may quote the text, so only its absence is kept.
    try:
        tokens = scorer.count_pair_tokens(query, passage)
    except Exception:
        return None
    return tokens if type(tokens) is int and tokens > 0 else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def _provenance(device: str) -> dict[str, object]:
    return {"model": MODEL_ID, "revision": REVISION, "device": device}


def _refusal(status: int, reason: str, index: int | None = None) -> JSONResponse:
    return JSONResponse({"error": reason, "index": index}, status_code=status)


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    device = os.environ.get("RERANKER_DEVICE", "cpu")
    port = int(os.environ.get("RERANKER_PORT", "8767"))
    scorer = BgeRerankerScorer(download_model(), device)
    uvicorn.run(create_app(scorer, device=device), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
```

Then, in `ml/bge_m3_server/pyproject.toml`, name `transformers` as a dependency and widen two descriptions:

Find in `ml/bge_m3_server/pyproject.toml`:

```toml
description = "Local BGE-M3 embedding server for the knowledge service (ML runtime)"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "sentence-transformers>=5",
    "torch>=2.6",
```

Replace with:

```toml
description = "Local BGE-M3 embedding and reranker servers for the knowledge service (ML runtime)"
requires-python = ">=3.12,<3.13"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "sentence-transformers>=5",
    "torch>=2.6",
    "transformers>=5",
```

Find in `ml/bge_m3_server/pyproject.toml`:

```toml
markers = ["model: needs the pinned BGE-M3 files in the Hugging Face cache"]
```

Replace with:

```toml
markers = ["model: needs the pinned model files in the Hugging Face cache"]
```

Update the lock file from the repository root:

```bash
cd ml/bge_m3_server && uv lock && cd ../..
```

Check: start the server from the repository root. The first start downloads about 2.3 GB:

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/reranker_server.py
```

In a second terminal:

```bash
curl -s 127.0.0.1:8767/v1/rerank -H 'Content-Type: application/json' -d '{"query": "Скільки днів відпустки?", "passages": ["Працівники мають 24 дні щорічної відпустки.", "VPN error 4031 means an expired certificate."]}'
```

Expected: `"device":"cpu"`, `"input_tokens":44`, and two scores, the first above 0 and the second far below it (about 3.7 and −11.0). Stop the server with Ctrl+C.

### Step 3 — Coding step: test the reranker server

This step tests the server without loading the model, plus two tests with the real model.

**In plain words:** A fake scorer counts one token per character and scores a passage by its length, or fails on request. The tests check that scores come back in order with the model named, that every limit refuses instead of truncating, that a bad request or a failing model never repeats the text, and that broken scores are refused. Two tests load the real model: one checks that the application's token bound is never too low, the other that the real scores prefer the right passage in English and Ukrainian.

| Test | Proves |
|---|---|
| `test_scores_come_in_passage_order_with_the_pinned_provenance` | success: one score per passage, in order, with model, revision, device, and token count |
| `test_a_request_over_the_limits_is_refused_and_never_truncated` | five kinds of bad input are refused, naming only the index, and nothing is scored |
| `test_an_invalid_request_is_refused_without_repeating_it` | malformed bodies get `invalid_request`, never the input back |
| `test_a_scoring_failure_carries_no_text` | a model that raises leaks nothing to the answer or the logs |
| `test_invalid_scores_are_refused` | a short list, NaN, infinity, an int, a bool, or a non-list is refused |
| `test_the_application_pair_bound_is_never_too_low` | the real tokenizer never counts more than `LocalReranker` would |
| `test_real_scores_prefer_the_answer_in_both_languages_and_ignore_batching` | the real model ranks the answer above the unrelated passage in both languages, and a passage scores the same alone or in a batch |

Create `ml/bge_m3_server/test_reranker_server.py`:

```python
"""The reranker server: limits, provenance, raw scores, and quiet failures.

Most tests use a fake scorer, so they need neither PyTorch nor the model.
Tests marked `model` use the real reranker and skip when the pinned files
are not in the Hugging Face cache.
"""

import logging
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import reranker_server as server

PLANTED = "the salary of Olena is 9000"


class FakeScorer:
    """Counts one token per character of the pair; scores by passage length."""

    def __init__(self, *, fail: bool = False, scores: object = None) -> None:
        self.scored: list[tuple[str, list[str]]] = []
        self._fail = fail
        self._scores = scores

    def count_pair_tokens(self, query: str, passage: str) -> int:
        if passage == "uncountable":
            raise ValueError(f"cannot tokenize {passage}")
        return len(query) + len(passage)

    def score(self, query: str, passages: Sequence[str]) -> list[float]:
        if self._fail:
            raise RuntimeError(f"failed on {query}")
        self.scored.append((query, list(passages)))
        if self._scores is not None:
            return self._scores  # type: ignore[return-value]
        return [float(len(passage)) for passage in passages]


def client(scorer: FakeScorer) -> TestClient:
    return TestClient(server.create_app(scorer, device="cpu"))


def test_scores_come_in_passage_order_with_the_pinned_provenance() -> None:
    scorer = FakeScorer()

    answer = client(scorer).post(
        "/v1/rerank", json={"query": "leave", "passages": ["abc", "a", "abcdef"]}
    )

    assert answer.status_code == 200
    assert answer.json() == {
        "model": "BAAI/bge-reranker-v2-m3",
        "revision": server.REVISION,
        "device": "cpu",
        "input_tokens": 3 * len("leave") + 3 + 1 + 6,
        "scores": [3.0, 1.0, 6.0],
    }
    assert scorer.scored == [("leave", ["abc", "a", "abcdef"])]


def test_the_model_endpoint_needs_no_text_and_states_the_limits() -> None:
    answer = client(FakeScorer()).get("/v1/model")

    assert answer.json() == {
        "model": "BAAI/bge-reranker-v2-m3",
        "revision": server.REVISION,
        "device": "cpu",
        "max_passages": 64,
        "max_pair_tokens": 8192,
        "max_request_tokens": 16_384,
    }


@pytest.mark.parametrize(
    ("body", "status", "reason", "index"),
    [
        ({"query": " ", "passages": ["a"]}, 422, "empty_query", None),
        ({"query": "q", "passages": ["a", " "]}, 422, "empty_passage", 1),
        ({"query": "q", "passages": ["uncountable"]}, 422, "uncountable_text", 0),
        ({"query": "q", "passages": ["a", "x" * 8192]}, 413, "pair_too_long", 1),
        (
            {"query": "q", "passages": ["x" * 8000, "x" * 8000, "x" * 400]},
            413,
            "request_too_long",
            2,
        ),
    ],
    ids=["empty query", "empty passage", "uncountable", "long pair", "long request"],
)
def test_a_request_over_the_limits_is_refused_and_never_truncated(
    body: dict[str, object], status: int, reason: str, index: int | None
) -> None:
    scorer = FakeScorer()

    answer = client(scorer).post("/v1/rerank", json=body)

    assert (answer.status_code, answer.json()) == (
        status,
        {"error": reason, "index": index},
    )
    assert scorer.scored == []


@pytest.mark.parametrize(
    "body",
    [
        {"query": PLANTED, "passages": []},
        {"query": PLANTED, "passages": ["a"] * 65},
        {"query": PLANTED, "passages": [PLANTED], "extra": 1},
        {"query": PLANTED, "passages": [7]},
        {"query": PLANTED, "passages": ["x" * (server.MAX_TEXT_CHARACTERS + 1)]},
    ],
    ids=["no passages", "too many", "unknown field", "not text", "too many chars"],
)
def test_an_invalid_request_is_refused_without_repeating_it(
    body: dict[str, object],
) -> None:
    answer = client(FakeScorer()).post("/v1/rerank", json=body)

    assert answer.status_code == 422
    assert answer.json() == {"error": "invalid_request", "index": None}


def test_a_scoring_failure_carries_no_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    answer = client(FakeScorer(fail=True)).post(
        "/v1/rerank", json={"query": PLANTED, "passages": [PLANTED]}
    )

    assert (answer.status_code, answer.json()) == (
        500,
        {"error": "reranking_failed", "index": None},
    )
    assert PLANTED not in answer.text
    assert PLANTED not in caplog.text


@pytest.mark.parametrize(
    "scores",
    [[1.0], [1.0, float("nan")], [1.0, float("inf")], [1.0, 2], [1.0, True], "x"],
    ids=["too few", "nan", "infinite", "int", "bool", "not a list"],
)
def test_invalid_scores_are_refused(scores: object) -> None:
    answer = client(FakeScorer(scores=scores)).post(
        "/v1/rerank", json={"query": "q", "passages": ["a", "b"]}
    )

    assert (answer.status_code, answer.json()) == (
        500,
        {"error": "reranking_failed", "index": None},
    )


def test_a_changed_or_missing_model_file_is_refused(tmp_path: Path) -> None:
    for name in server.FILE_HASHES:
        (tmp_path / name).write_bytes(b"not the model")

    with pytest.raises(server.ModelFilesChanged):
        server.check_files(tmp_path)


def _cached_model() -> Path | None:
    from huggingface_hub import snapshot_download

    try:
        folder = Path(
            snapshot_download(
                server.MODEL_ID,
                revision=server.REVISION,
                allow_patterns=server.MODEL_FILES,
                local_files_only=True,
            )
        )
    except Exception:
        return None
    return folder if (folder / "model.safetensors").is_file() else None


@pytest.fixture(scope="module")
def scorer() -> server.BgeRerankerScorer:
    folder = _cached_model()
    if folder is None:
        pytest.skip("the pinned reranker files are not cached")
    server.check_files(folder)
    return server.BgeRerankerScorer(folder, "cpu")


@pytest.mark.model
def test_the_application_pair_bound_is_never_too_low(
    scorer: server.BgeRerankerScorer,
) -> None:
    # The adapter bounds a pair by the sum of its two texts' bounds.
    import unicodedata

    def bound(text: str) -> int:
        return 3 + max(
            len(unicodedata.normalize("NFKC", text)),
            len(unicodedata.normalize("NFKD", text)),
        )

    texts = ["a", "Відпустка", "VPN-4031", "\ufb03 \u3131\u314f", "x" * 300]
    for query in texts:
        for passage in texts:
            exact = scorer.count_pair_tokens(query, passage)
            assert exact <= bound(query) + bound(passage), (query, passage)


@pytest.mark.model
def test_real_scores_prefer_the_answer_in_both_languages_and_ignore_batching(
    scorer: server.BgeRerankerScorer,
) -> None:
    passages = [
        "Employees get 24 days of paid annual leave per year.",
        "The VPN client must be updated before the first connection.",
        "Працівники мають 24 дні оплачуваної щорічної відпустки на рік.",
    ]

    english = scorer.score("How many days of annual leave do I get?", passages)
    ukrainian = scorer.score("Скільки днів відпустки мені належить?", passages)
    alone = scorer.score("How many days of annual leave do I get?", passages[:1])

    assert min(english[0], english[2]) > english[1]
    assert min(ukrainian[0], ukrainian[2]) > ukrainian[1]
    assert alone[0] == pytest.approx(english[0], abs=1e-3)
```

Check: `make ml-check` reports `0 errors` and `51 passed`. The two real-model tests need the files the server downloaded in Step 2; without them, they skip.

### Step 4 — Coding step: the Reranker port and `rerank_candidates`

This step defines what any reranker promises, and writes the function that uses one on retrieval's Candidates.

**In plain words:** The port lists what a reranker must do: name itself, state its limits, count a pair's tokens, and return one score per passage or raise a typed error. Two checks run before and after every call: inputs within the limits, and scores that are exactly one finite number per passage. The reranking function first copies and checks its Candidates, cuts them into requests the reranker accepts, gives counting and all requests one shared deadline, and returns the same Candidates sorted by score, remembering where retrieval had put each one.

| Piece | What it does |
|---|---|
| `RerankerError` and its subclasses | `InvalidRerankRequest` (caller's mistake), `RerankInputRejected` (over a limit), `RerankerUnavailable`, `RerankerTimeout`, `RerankerMalformedResponse`, `RerankerRequestRefused` |
| `RerankerIdentity`, `RerankLimits` | which model scored; the most one call may carry |
| `Reranker` | `identity`, `limits`, `count_pair_tokens`, `score` |
| `check_rerank_inputs` | refuses an empty or non-UTF-8 text, too many passages, an uncountable pair, or a pair or request over the limits; names the index only |
| `checked_scores` | refuses anything but `expected` finite numbers; a bool is refused although Python treats it as an int |
| `RerankedCandidate`, `RerankResult` | a Candidate with its new `rank`, its `score`, and its `retrieval_rank`; the result with the reranker identity and the number of requests |
| `_snapshot`, `_read` | read every Candidate once; one that cannot be read is refused without its text |
| `rerank_candidates` | checks, batches, keeps one deadline from the start, drops a late answer, sorts by score then retrieval rank |
| `_batches` | fills each request in order until the next passage would break a limit |

Create `src/knowledge_service/retrieval/reranking.py`:

```python
"""The Reranker port, and reranking retrieval's Candidates with it.

Retrieval finds Candidates cheaply: a vector distance or a word match per
Chunk. A reranker reads the Question and one Candidate's text together and
returns one relevance score, which is slower but sharper, so it only
reorders the few Candidates retrieval already found.

What every `Reranker` adapter promises:

- **One call is one request, all or nothing.** `score` returns exactly one
  score per passage, in passage order, or raises a `RerankerError`.
- **Inputs are checked before anything leaves the process.** An empty
  question or passage, a text that is not valid Unicode, a pair whose
  tokens cannot be counted, too many passages, a pair over the per-pair
  limit, or a request over the total limit raises `RerankInputRejected`
  naming the index, never the text.
- **Score semantics.** A score is the model's raw relevance score: any
  finite number, higher means more relevant. Scores compare only within
  one question and one `RerankerIdentity`; they are not probabilities and
  carry no fixed threshold.
- **Errors carry no text,** not even in a chained exception.

What `rerank_candidates` promises:

- **Checked first.** A bad question, timeout, or list of Candidates raises
  `InvalidRerankRequest`, and a Candidate the model cannot take raises
  `RerankInputRejected`, before the reranker is called. Each Candidate is
  read once, at the start, so changing the list later changes nothing.
- **Batch sizing.** Candidates go to the reranker in retrieval order, in as
  few requests as its limits allow: at most `max_passages` per request, and
  at most `max_request_tokens` per request by the reranker's own count.
- **One deadline.** Counting tokens and every request must finish within
  `timeout_seconds` of the start; each request gets only the time left.
  Past it, `RerankerTimeout` is raised and nothing is returned. A reranker
  that blocks the event loop cannot be interrupted, but its late answer is
  dropped all the same.
- **Only a new order.** The result holds exactly the Candidates it was
  given, each once, ordered by score, highest first, with retrieval rank
  breaking ties. It never adds, drops, or reads a Chunk, so every
  Candidate passed the permission check of the search that found it.
- **Provenance.** The result names the reranker identity behind its scores.
- **Bugs are not hidden.** An error from the reranker that is not a
  `RerankerError` propagates unchanged.
- **Quiet.** The fields holding Document text are kept out of repr.
"""

import asyncio
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, cast
from uuid import UUID

from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.vector_search import MAX_CANDIDATES


class RerankerError(Exception):
    """Base error for reranking failures."""


class InvalidRerankRequest(RerankerError):
    """The question, timeout, or Candidates break the contract; nothing was sent."""


class RerankRejection(StrEnum):
    """Why a reranking request was refused before calling the model."""

    EMPTY_QUERY = "empty_query"
    EMPTY_PASSAGE = "empty_passage"
    UNENCODABLE_TEXT = "unencodable_text"
    UNCOUNTABLE_TEXT = "uncountable_text"
    TOO_MANY_PASSAGES = "too_many_passages"
    PAIR_TOO_LONG = "pair_too_long"
    REQUEST_TOO_LONG = "request_too_long"


class RerankInputRejected(RerankerError):
    """The request breaks the reranker's input limits; nothing was sent."""

    def __init__(self, reason: RerankRejection, *, index: int | None) -> None:
        self.reason = reason
        self.index = index
        where = "" if index is None else f" at passage {index}"
        super().__init__(f"rerank input rejected {reason.value}{where}")


class RerankerUnavailable(RerankerError):
    """The reranker cannot currently serve the request."""


class RerankerTimeout(RerankerError):
    """The reranker did not answer within the deadline."""


class RerankerMalformedResponse(RerankerError):
    """The reranker answered, but not with one finite score per passage."""


class RerankerRequestRefused(RerankerError):
    """The reranker refused the request or is not the configured model."""


@dataclass(frozen=True, slots=True)
class RerankerIdentity:
    """Which model produced a score; scores of different identities never mix."""

    provider: str
    model: str

    def __post_init__(self) -> None:
        if type(self.provider) is not str or type(self.model) is not str:
            raise InvalidRerankRequest("provider and model must be strings")
        if not self.provider.strip() or not self.model.strip():
            raise InvalidRerankRequest("provider and model must not be empty")


@dataclass(frozen=True, slots=True)
class RerankLimits:
    """The most one `score` call may carry, in model tokens."""

    max_passages: int
    max_pair_tokens: int
    max_request_tokens: int

    def __post_init__(self) -> None:
        for value in (self.max_passages, self.max_pair_tokens, self.max_request_tokens):
            if type(value) is not int or value < 1:
                raise InvalidRerankRequest("rerank limits must be positive integers")
        if self.max_pair_tokens > self.max_request_tokens:
            raise InvalidRerankRequest("one pair cannot be allowed more than a request")


class Reranker(Protocol):
    """A model that scores how well each passage answers one question."""

    @property
    def identity(self) -> RerankerIdentity:
        """The provider and the model, with its revision."""
        ...

    @property
    def limits(self) -> RerankLimits:
        """The most one call to `score` may carry."""
        ...

    def count_pair_tokens(self, query: str, passage: str) -> int:
        """Count the pair in this model's tokens, never fewer than the model does."""
        ...

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        """Return one raw score per passage, in order, or raise a `RerankerError`."""
        ...


@dataclass(frozen=True, slots=True)
class RerankedCandidate:
    """One eligible Chunk after reranking, and where retrieval had put it.

    `rank` is its 1-based position after reranking, `score` the reranker's
    raw score, and `retrieval_rank` its rank before. The fields holding
    Document text are kept out of repr.
    """

    rank: int
    chunk_id: ChunkId
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    score: float
    retrieval_rank: int
    external_id: str = field(repr=False)
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_anchor: str | None = field(repr=False)
    text: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _Given:
    """One Candidate's fields, read once when reranking starts."""

    rank: object
    chunk_id: object
    document_id: DocumentId
    source_id: SourceId
    content_version: ContentVersion
    ordinal: int
    external_id: str = field(repr=False)
    title: str = field(repr=False)
    heading_path: str | None = field(repr=False)
    source_anchor: str | None = field(repr=False)
    text: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class RerankResult:
    """The Candidates in their new order, and how they were scored."""

    identity: RerankerIdentity
    candidates: tuple[RerankedCandidate, ...]
    requests: int


def check_rerank_inputs(
    query: str,
    passages: Sequence[str],
    limits: RerankLimits,
    count_pair_tokens: Callable[[str, str], int],
) -> int:
    """Refuse a request that breaks `limits`; return its total token count.

    Adapters call this before sending anything, so a rejected request never
    reaches the model. Failures are raised outside any `except` block, so no
    chained exception can carry the text.
    """
    if isinstance(passages, str):
        raise TypeError("passages must be a sequence of strings, not one string")
    if not query.strip():
        raise RerankInputRejected(RerankRejection.EMPTY_QUERY, index=None)
    if not _encodable(query):
        raise RerankInputRejected(RerankRejection.UNENCODABLE_TEXT, index=None)
    if len(passages) > limits.max_passages:
        raise RerankInputRejected(RerankRejection.TOO_MANY_PASSAGES, index=None)
    total = 0
    for index, passage in enumerate(passages):
        tokens = _pair_tokens(query, passage, index, limits, count_pair_tokens)
        total += tokens
        if total > limits.max_request_tokens:
            raise RerankInputRejected(RerankRejection.REQUEST_TOO_LONG, index=index)
    return total


def checked_scores(scores: object, *, expected: int) -> tuple[float, ...]:
    """Return the model's scores, or raise if they are not `expected` finite numbers.

    `scores` is whatever the model sent, so nothing about its shape is
    trusted: bool is refused although it is an int, and so is any number
    that is not finite as a float.
    """
    if not isinstance(scores, list | tuple):
        raise RerankerMalformedResponse("the response is not a list of scores")
    items = cast(Sequence[object], scores)
    if len(items) != expected:
        raise RerankerMalformedResponse(
            f"expected {expected} scores, received {len(items)}"
        )
    checked: list[float] = []
    for index, value in enumerate(items):
        number = _number(value)
        if number is None:
            raise RerankerMalformedResponse(f"score {index} is not a finite number")
        checked.append(number)
    return tuple(checked)


async def rerank_candidates(
    reranker: Reranker,
    question: str,
    candidates: Sequence[Candidate],
    *,
    request_id: UUID,
    timeout_seconds: float,
) -> RerankResult:
    """Score every Candidate against the question and return them in the new order."""
    loop = asyncio.get_running_loop()
    given = _snapshot(question, candidates, timeout_seconds)
    deadline = loop.time() + timeout_seconds
    identity = reranker.identity
    texts = [cast(str, item.text) for item in given]
    batches = _batches(question, texts, reranker.limits, reranker.count_pair_tokens)
    if loop.time() > deadline:
        raise RerankerTimeout()
    scores: list[float] = []
    for start, end in batches:
        remaining = deadline - loop.time()
        batch: object = None
        if remaining > 0:
            try:
                async with asyncio.timeout(remaining):
                    batch = await reranker.score(
                        question,
                        texts[start:end],
                        request_id=request_id,
                        timeout_seconds=remaining,
                    )
            except TimeoutError:
                batch = None
        # Raised outside the `except`: nothing linked can carry the texts.
        # A reranker that blocked the loop answers late; its answer is dropped.
        if batch is None or loop.time() > deadline:
            raise RerankerTimeout()
        scores += checked_scores(batch, expected=end - start)
    # Retrieval ranks are 1, 2, 3, ..., so the position breaks ties.
    order = sorted(range(len(given)), key=lambda index: (-scores[index], index))
    return RerankResult(
        identity=identity,
        candidates=tuple(
            _reranked(rank, given[index], scores[index])
            for rank, index in enumerate(order, start=1)
        ),
        requests=len(batches),
    )


def _snapshot(
    question: str, candidates: Sequence[Candidate], timeout_seconds: float
) -> tuple[_Given, ...]:
    """Check the request and read every Candidate once; raise if any is wrong."""
    if type(question) is not str or not question.strip():
        raise InvalidRerankRequest("the question must be a non-empty string")
    if (
        type(timeout_seconds) not in (int, float)
        or not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
    ):
        raise InvalidRerankRequest("timeout_seconds must be a positive number")
    if not isinstance(candidates, tuple | list):
        raise InvalidRerankRequest("the Candidates must be a tuple or a list")
    if len(candidates) > MAX_CANDIDATES:
        raise InvalidRerankRequest(f"at most {MAX_CANDIDATES} Candidates")
    given = tuple(_read(candidate) for candidate in tuple(candidates))
    seen: set[ChunkId] = set()
    for position, item in enumerate(given, start=1):
        if item is None:
            raise InvalidRerankRequest(
                f"an unreadable Candidate at position {position}"
            )
        if type(item.chunk_id) is not ChunkId:
            raise InvalidRerankRequest(f"no ChunkId at position {position}")
        if type(item.rank) is not int or item.rank != position:
            raise InvalidRerankRequest(f"a wrong rank at position {position}")
        if type(item.text) is not str:
            raise InvalidRerankRequest(f"no text at position {position}")
        if item.chunk_id in seen:
            raise InvalidRerankRequest(f"a Chunk listed twice, at position {position}")
        seen.add(item.chunk_id)
    return cast(tuple[_Given, ...], given)


def _read(candidate: object) -> _Given | None:
    # A property may raise, and its error may quote the text: only its
    # absence is kept, and the caller raises outside any `except`.
    try:
        item = cast(Candidate, candidate)
        return _Given(
            rank=item.rank,
            chunk_id=item.chunk_id,
            document_id=item.document_id,
            source_id=item.source_id,
            content_version=item.content_version,
            ordinal=item.ordinal,
            external_id=item.external_id,
            title=item.title,
            heading_path=item.heading_path,
            source_anchor=item.source_anchor,
            text=item.text,
        )
    except Exception:
        return None


def _batches(
    question: str,
    texts: Sequence[str],
    limits: RerankLimits,
    count_pair_tokens: Callable[[str, str], int],
) -> list[tuple[int, int]]:
    """Split the texts into requests within the limits, keeping their order."""
    if not texts:
        return []
    if not question.strip():
        raise RerankInputRejected(RerankRejection.EMPTY_QUERY, index=None)
    if not _encodable(question):
        raise RerankInputRejected(RerankRejection.UNENCODABLE_TEXT, index=None)
    batches: list[tuple[int, int]] = []
    start = 0
    total = 0
    for index, text in enumerate(texts):
        tokens = _pair_tokens(question, text, index, limits, count_pair_tokens)
        if index - start == limits.max_passages or (
            total + tokens > limits.max_request_tokens
        ):
            batches.append((start, index))
            start, total = index, 0
        total += tokens
    batches.append((start, len(texts)))
    return batches


def _pair_tokens(
    query: str,
    passage: str,
    index: int,
    limits: RerankLimits,
    count_pair_tokens: Callable[[str, str], int],
) -> int:
    if not passage.strip():
        raise RerankInputRejected(RerankRejection.EMPTY_PASSAGE, index=index)
    if not _encodable(passage):
        raise RerankInputRejected(RerankRejection.UNENCODABLE_TEXT, index=index)
    tokens = _count(query, passage, count_pair_tokens)
    if tokens is None:
        raise RerankInputRejected(RerankRejection.UNCOUNTABLE_TEXT, index=index)
    if tokens > limits.max_pair_tokens:
        raise RerankInputRejected(RerankRejection.PAIR_TOO_LONG, index=index)
    return tokens


def _count(
    query: str, passage: str, count_pair_tokens: Callable[[str, str], int]
) -> int | None:
    # A tokenizer's own error may quote the text, so it is dropped here.
    try:
        tokens = count_pair_tokens(query, passage)
    except Exception:
        return None
    return tokens if type(tokens) is int and tokens > 0 else None


def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _number(value: object) -> float | None:
    # bool is a subclass of int, and a string may look numeric; neither is.
    if type(value) is not int and type(value) is not float:
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def _reranked(rank: int, given: _Given, score: float) -> RerankedCandidate:
    return RerankedCandidate(
        rank=rank,
        chunk_id=cast(ChunkId, given.chunk_id),
        document_id=given.document_id,
        source_id=given.source_id,
        content_version=given.content_version,
        ordinal=given.ordinal,
        score=score,
        retrieval_rank=cast(int, given.rank),
        external_id=given.external_id,
        title=given.title,
        heading_path=given.heading_path,
        source_anchor=given.source_anchor,
        text=cast(str, given.text),
    )
```

Check: `make typecheck` reports `0 errors`.

### Step 5 — Coding step: the local reranker adapter

This step makes the application talk to the reranker server through the port.

**In plain words:** The adapter checks the request before sending anything, using a token count that is never too low. It sends one request, reads at most 64 KB of answer and refuses a compressed one, and checks that the answer names the pinned model and revision before it trusts a single score. Every failure becomes one of the port's typed errors, raised outside the `except` block so no linked error carries the texts, and one log record notes counts, time, and device only.

| Piece | What it does |
|---|---|
| `LocalRerankerSettings` | `LOCAL_RERANKER_*` settings: URL (default port 8767), model, revision, timeout, limits; a bad URL or a limit above the server's fails at start-up |
| `count_pair_tokens` | the sum of both texts' upper bounds |
| `score` | check, send, check status and provenance, check scores, log |
| `verify_server` | sends no text; returns the device, so a run fails early on a stopped server or wrong revision |
| `_deadline` | refuses a timeout that is not a positive number, with `InvalidRerankRequest` |
| `_send` | one request under the deadline, the answer read up to `MAX_RESPONSE_BYTES`, compressed answers refused unread; a closed client is `RerankerUnavailable` |
| `_check_status` | 408 or 504 is a timeout, 5xx unavailable, any other non-200 refused |
| `_device` | accepts only lowercase letters, digits, `:`, `_`, and `-`, up to 16 characters, because the name is logged and printed in Markdown |

Create `src/knowledge_service/retrieval/local_reranker.py`:

```python
"""Local bge-reranker-v2-m3 implementation of the Reranker port.

The model runs in a separate Python 3.12 process, the reranker server in
`ml/bge_m3_server/reranker_server.py` (ADR 0009). This adapter is the
application's side of that seam: one call to `score` is one HTTP request.
It adds what the port cannot know:

- **Pinned provenance.** The identity names the model and the exact weights
  revision, `BAAI/bge-reranker-v2-m3@<revision>`. Every answer must name the
  same model and revision; any other answer raises `RerankerRequestRefused`
  and no score is returned, because scores of two models do not compare.
- **A safe token bound without a tokenizer.** `count_pair_tokens` returns an
  upper bound, the same bound as `LocalEmbeddingModel.count_tokens` for each
  text: the model shares BGE-M3's tokenizer, and a pair adds no more
  special tokens than the two texts would alone. A request the server would
  refuse is refused here first, before any text leaves the process.
- **Retry classification.** A refused connection or a 5xx becomes
  `RerankerUnavailable`, a timeout or a 408 or 504 `RerankerTimeout`, and
  any other refusal `RerankerRequestRefused`.
- **A bounded answer.** The response is read in pieces and refused past
  `MAX_RESPONSE_BYTES`; a compressed answer is refused unread.
- **Safe logging.** One log record per call with counts, timing, device,
  and the outcome; never text. Errors are raised outside the `except`
  block, because an HTTP error holds the request, which holds every text.

The caller owns the `httpx.AsyncClient`. Create it with `trust_env=False`,
so a proxy set in the environment never receives the texts.
"""

import asyncio
import json
import logging
import math
import re
import time
import unicodedata
from collections.abc import Sequence
from typing import cast
from uuid import UUID

import httpx
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from knowledge_service.retrieval.reranking import (
    InvalidRerankRequest,
    RerankerError,
    RerankerIdentity,
    RerankerMalformedResponse,
    RerankerRequestRefused,
    RerankerTimeout,
    RerankerUnavailable,
    RerankInputRejected,
    RerankLimits,
    check_rerank_inputs,
    checked_scores,
)

logger = logging.getLogger(__name__)

PROVIDER = "local"
RERANK_PATH = "/v1/rerank"
MODEL_PATH = "/v1/model"
# The reranker weights this application was evaluated with (Hugging Face commit).
RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
# 64 scores as JSON take about 2 KB; far more means a fault.
MAX_RESPONSE_BYTES = 64 * 1024
# <s>, </s>, and the word-start marker the tokenizer adds to one text.
_ADDED_TOKENS = 3
# A device name is logged and printed in Markdown reports, so it stays plain.
_DEVICE = re.compile(r"[a-z0-9:_-]{1,16}")


class LocalRerankerSettings(BaseSettings):
    """Settings for the local reranker adapter, read from `LOCAL_RERANKER_*`.

    The limits cannot exceed the server's: 64 passages, 8192 tokens per
    pair, and 16,384 tokens per request.
    """

    model_config = SettingsConfigDict(env_prefix="LOCAL_RERANKER_")

    url: str = Field(default="http://127.0.0.1:8767", min_length=1)
    model: str = Field(default="BAAI/bge-reranker-v2-m3", min_length=1)
    revision: str = Field(default=RERANKER_REVISION, min_length=1)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    max_passages: int = Field(default=16, gt=0, le=64)
    max_pair_tokens: int = Field(default=8192, gt=0, le=8192)
    max_request_tokens: int = Field(default=16_384, gt=0, le=16_384)

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        # Checked here, so a typo fails at start-up, not inside a request.
        try:
            url = httpx.URL(value)
        except httpx.InvalidURL:
            url = None
        if url is None or url.scheme not in ("http", "https") or not url.host:
            raise ValueError("url must be an http or https URL")
        return value.rstrip("/")


class LocalReranker:
    """Translate Reranker calls to the local reranker server."""

    def __init__(
        self, client: httpx.AsyncClient, settings: LocalRerankerSettings
    ) -> None:
        self._client = client
        self._settings = settings
        self._identity = RerankerIdentity(
            provider=PROVIDER, model=f"{settings.model}@{settings.revision}"
        )
        self._limits = RerankLimits(
            max_passages=settings.max_passages,
            max_pair_tokens=settings.max_pair_tokens,
            max_request_tokens=settings.max_request_tokens,
        )

    @property
    def identity(self) -> RerankerIdentity:
        return self._identity

    @property
    def limits(self) -> RerankLimits:
        return self._limits

    def count_pair_tokens(self, query: str, passage: str) -> int:
        """Return an upper bound on the pair's tokens: each text's bound, added.

        A pair is `<s> query </s></s> passage </s>`: four special tokens and
        two word-start markers, the same six the two texts add alone.
        """
        return _upper_bound(query) + _upper_bound(passage)

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        deadline = self._deadline(timeout_seconds)
        try:
            check_rerank_inputs(query, passages, self._limits, self.count_pair_tokens)
        except RerankInputRejected as error:
            _log(request_id, self._identity, len(passages), 0, error)
            raise
        if not passages:
            return ()
        started_at = time.perf_counter()
        device: str | None = None
        try:
            status, content = await self._send(
                "POST",
                RERANK_PATH,
                deadline,
                body={"query": query, "passages": list(passages)},
                headers={"X-Request-ID": str(request_id)},
            )
            _check_status(status)
            body = self._check_provenance(_json_object(content))
            device = _device(body)
            scores = checked_scores(body.get("scores"), expected=len(passages))
        except RerankerError as error:
            _log(request_id, self._identity, len(passages), _since(started_at), error)
            raise
        _log(
            request_id,
            self._identity,
            len(passages),
            _since(started_at),
            None,
            device=device,
        )
        return scores

    async def verify_server(self, *, timeout_seconds: float) -> str:
        """Check, sending no text, that the server runs the configured model.

        Returns the device the server scores on, such as `cpu` or `mps`.
        Raises `RerankerUnavailable` or `RerankerTimeout` when the server
        cannot be reached, and `RerankerRequestRefused` when it serves other
        weights.
        """
        deadline = self._deadline(timeout_seconds)
        status, content = await self._send("GET", MODEL_PATH, deadline)
        _check_status(status)
        device = _device(self._check_provenance(_json_object(content)))
        if device is None:
            raise RerankerMalformedResponse("the reranker did not name its device")
        return device

    def _deadline(self, timeout_seconds: float) -> float:
        # bool is an int, and NaN compares false with everything; neither is.
        if (
            type(timeout_seconds) not in (int, float)
            or math.isnan(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise InvalidRerankRequest("timeout_seconds must be a positive number")
        return min(timeout_seconds, self._settings.timeout_seconds)

    async def _send(
        self,
        method: str,
        path: str,
        deadline: float,
        *,
        body: object = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        """Send one request; return its status and its bytes, up to the cap."""
        status = 0
        content = bytearray()
        encoded = False
        failure: RerankerError | None = None
        try:
            async with (
                asyncio.timeout(deadline),
                self._client.stream(
                    method,
                    self._settings.url + path,
                    json=body,
                    headers={"Accept-Encoding": "identity", **(headers or {})},
                    timeout=deadline,
                ) as response,
            ):
                status = response.status_code
                coding = response.headers.get("Content-Encoding", "identity")
                encoded = coding.strip().lower() != "identity"
                # An encoded body is never read, so nothing is ever unpacked.
                if not encoded:
                    async for piece in response.aiter_bytes():
                        content += piece
                        if len(content) > MAX_RESPONSE_BYTES:
                            break
        except TimeoutError, httpx.TimeoutException:
            failure = RerankerTimeout()
        except httpx.HTTPError, httpx.StreamError, RuntimeError:
            # RuntimeError: a client the caller already closed.
            failure = RerankerUnavailable()
        if len(content) > MAX_RESPONSE_BYTES:
            failure = RerankerMalformedResponse("the response is too large")
        if encoded and failure is None:
            failure = RerankerMalformedResponse("the response is encoded")
        if failure is not None:
            # Raised outside the except block: the HTTP error holds the request.
            raise failure
        return status, bytes(content)

    def _check_provenance(self, body: dict[str, object] | None) -> dict[str, object]:
        if body is None:
            raise RerankerMalformedResponse("the reranker response was not JSON")
        if (
            body.get("model") != self._settings.model
            or body.get("revision") != self._settings.revision
        ):
            # Scores from another model do not compare with this model's.
            raise RerankerRequestRefused(
                "the reranker is not the configured model or revision"
            )
        return body


def _upper_bound(text: str) -> int:
    # As in `LocalEmbeddingModel.count_tokens`: every token covers at least
    # one normalized character, and the tokenizer may leave either form.
    longest = max(
        len(unicodedata.normalize("NFKC", text)),
        len(unicodedata.normalize("NFKD", text)),
    )
    return longest + _ADDED_TOKENS


def _device(body: dict[str, object]) -> str | None:
    device = body.get("device")
    if type(device) is str and _DEVICE.fullmatch(device):
        return device
    return None


def _check_status(status: int) -> None:
    if status in (408, 504):
        raise RerankerTimeout()
    if status >= 500:
        raise RerankerUnavailable()
    if status != 200:
        raise RerankerRequestRefused(
            f"the reranker refused the request with status {status}"
        )


def _json_object(content: bytes) -> dict[str, object] | None:
    try:
        body: object = json.loads(content)
    except ValueError, RecursionError:
        # RecursionError: JSON nested deeper than the parser allows.
        return None
    if not isinstance(body, dict):
        return None
    return cast(dict[str, object], body)


def _since(started_at: float) -> int:
    return round((time.perf_counter() - started_at) * 1_000)


def _log(
    request_id: UUID,
    identity: RerankerIdentity,
    passages: int,
    latency_ms: int,
    error: RerankerError | None,
    *,
    device: str | None = None,
) -> None:
    """Record one call: identifiers, counts, timing, and the outcome. No text."""
    fields: dict[str, object] = {
        "request_id": str(request_id),
        "provider": identity.provider,
        "model": identity.model,
        "passages": passages,
        "latency_ms": latency_ms,
        "outcome": "succeeded" if error is None else type(error).__name__,
    }
    if device is not None:
        fields["device"] = device
    level = logging.INFO if error is None else logging.WARNING
    logger.log(level, "rerank request finished", extra={"rerank": fields})
```

Document the new setting in `.env.example`:

At the end of `.env.example`, add:

```bash

# The local reranker server (ml/bge_m3_server/reranker_server.py); the default
# is shown.
# LOCAL_RERANKER_URL=http://127.0.0.1:8767
```

Check: `make typecheck` reports `0 errors`. As with `LOCAL_EMBEDDING_URL`, the setting is read from the shell environment, not from `.env`.

### Step 6 — Coding step: test doubles and unit tests

This step adds a fake reranker, a stand-in server, and the tests for the port, the adapter, and `rerank_candidates`.

**In plain words:** The fake reranker scores a passage by how many words it shares with the question, so tests can predict every order. The stand-in server answers like the real one, inside the test process, and can be told to fail in each way a real server can. The contract tests run the same checks on the fake and on the real adapter. The adapter tests try every broken answer and check that each one becomes a typed error with no text. The reranking tests check the new order, the batches, the shared deadline, and, over many random inputs, that the result is always exactly the Candidates given.

| File | What it holds |
|---|---|
| `tests/fakes.py` | `shared_words` and `FakeReranker`, which records every call and the time each was given |
| `tests/reranker_stubs.py` | `StubRerankerServer` over `httpx.MockTransport`, and `local_reranker` |
| `tests/test_reranker_contract.py` | the same five promises for the fake and the local adapter |
| `tests/test_local_reranker.py` | request shape, identity, other revisions refused, fourteen typed failures without text, the log record, `verify_server` and device names, the bound, bad timeouts, a closed client, settings |
| `tests/test_reranking.py` | order, ties, empty input, a property test over random inputs, token batches, a pair over the limit, the shared deadline, bad timeouts and Candidates, a reranker that blocks the loop, slow counting, a list changed during scoring, an unreadable Candidate |

In `tests/fakes.py`, change the imports, then add the fake at the end:

Find in `tests/fakes.py`:

```python
from knowledge_service.model import ChatModelUnavailable

```

Replace with:

```python
from knowledge_service.model import ChatModelUnavailable
from knowledge_service.retrieval.reranking import (
    RerankerError,
    RerankerIdentity,
    RerankerTimeout,
    RerankLimits,
    check_rerank_inputs,
    checked_scores,
)

```

At the end of `tests/fakes.py`, add:

```python


def shared_words(query: str, passage: str) -> float:
    """Fake relevance: how many distinct lowercased words the two texts share."""
    words = set(re.findall(r"\w+", query.lower()))
    return float(len(words & set(re.findall(r"\w+", passage.lower()))))


class FakeReranker:
    """Deterministic Reranker over `shared_words`; records every call.

    `delay_seconds` makes each call that long, within its own timeout.
    `extra_scores` adds that many scores to every answer, to test a
    malformed one.
    """

    def __init__(
        self,
        *,
        limits: RerankLimits | None = None,
        error: RerankerError | None = None,
        delay_seconds: float = 0.0,
        extra_scores: int = 0,
    ) -> None:
        self._identity = RerankerIdentity(provider="fake", model="fake-reranker")
        self._limits = limits or RerankLimits(
            max_passages=16, max_pair_tokens=1_000, max_request_tokens=4_000
        )
        self._error = error
        self._delay_seconds = delay_seconds
        self._extra_scores = extra_scores
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[float] = []

    @property
    def identity(self) -> RerankerIdentity:
        return self._identity

    @property
    def limits(self) -> RerankLimits:
        return self._limits

    def count_pair_tokens(self, query: str, passage: str) -> int:
        return len(query.split()) + len(passage.split()) + 4

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        if math.isnan(timeout_seconds):
            raise ValueError("timeout_seconds must be a number")
        check_rerank_inputs(query, passages, self._limits, self.count_pair_tokens)
        if not passages:
            return ()
        self.calls.append(tuple(passages))
        self.timeouts.append(timeout_seconds)
        if self._delay_seconds:
            try:
                async with asyncio.timeout(timeout_seconds):
                    await asyncio.sleep(self._delay_seconds)
            except TimeoutError:
                raise RerankerTimeout() from None
        if self._error is not None:
            raise self._error
        scores = [shared_words(query, passage) for passage in passages]
        return checked_scores(
            scores + [0.0] * self._extra_scores, expected=len(passages)
        )
```

Create `tests/reranker_stubs.py`:

```python
"""A stand-in for the local reranker server, for deterministic tests.

`StubRerankerServer` answers through `httpx.MockTransport`, so the real
`LocalReranker` sends real HTTP requests that never leave the process.
"""

import asyncio
import json

import httpx

from fakes import shared_words
from knowledge_service.retrieval.local_reranker import (
    RERANKER_REVISION,
    LocalReranker,
    LocalRerankerSettings,
)

URL = "http://reranker.test"


class StubRerankerServer:
    """Answer like the reranker server; record every request."""

    def __init__(
        self,
        *,
        status: int = 200,
        body: bytes | None = None,
        model: str = "BAAI/bge-reranker-v2-m3",
        revision: str = RERANKER_REVISION,
        device: object = "cpu",
        scores: object = None,
        error: Exception | None = None,
        block: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._status = status
        self._body = body
        self._provenance = {"model": model, "revision": revision, "device": device}
        # Sent instead of the `shared_words` scores.
        self._scores = scores
        self._error = error
        self._block = block
        self._headers = headers or {}
        self.requests: list[httpx.Request] = []

    @property
    def sent(self) -> int:
        """How many requests carried texts to score."""
        return sum(1 for request in self.requests if request.method == "POST")

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._block:
            await asyncio.Event().wait()
        if self._error is not None:
            raise self._error
        if self._body is not None or self._status != 200:
            return httpx.Response(
                self._status, content=self._body or b"{}", headers=self._headers
            )
        if request.method == "GET":
            return httpx.Response(200, json=self._provenance)
        body = json.loads(request.content)
        query: str = body["query"]
        passages: list[str] = body["passages"]
        scores = (
            [shared_words(query, passage) for passage in passages]
            if self._scores is None
            else self._scores
        )
        return httpx.Response(
            200,
            json={**self._provenance, "input_tokens": 9, "scores": scores},
            headers=self._headers,
        )


def local_reranker(
    server: StubRerankerServer, settings: LocalRerankerSettings | None = None
) -> LocalReranker:
    """The real adapter, talking to `server` through an in-process transport."""
    client = httpx.AsyncClient(transport=httpx.MockTransport(server.handle))
    return LocalReranker(client, settings or LocalRerankerSettings(url=URL))
```

Create `tests/test_reranker_contract.py`:

```python
"""Behavioural contract tests shared by every Reranker adapter."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

import pytest

from fakes import FakeReranker
from knowledge_service.retrieval.reranking import (
    Reranker,
    RerankerMalformedResponse,
    RerankerTimeout,
    RerankInputRejected,
    RerankRejection,
)
from reranker_stubs import StubRerankerServer, local_reranker

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000033")
PLANTED = "the salary of Olena is 9000"


@dataclass(frozen=True)
class Adapter:
    """One adapter in the state a test needs, and how many calls it sent."""

    reranker: Reranker
    sent: Callable[[], int]


def fake(*, delay_seconds: float = 0.0, extra_scores: int = 0) -> Adapter:
    reranker = FakeReranker(delay_seconds=delay_seconds, extra_scores=extra_scores)
    return Adapter(reranker=reranker, sent=lambda: len(reranker.calls))


def local(server: StubRerankerServer) -> Adapter:
    return Adapter(reranker=local_reranker(server), sent=lambda: server.sent)


WORKING: dict[str, Callable[[], Adapter]] = {
    "fake": fake,
    "local": lambda: local(StubRerankerServer()),
}
SHORT_ANSWER: dict[str, Callable[[], Adapter]] = {
    "fake": lambda: fake(extra_scores=1),
    "local": lambda: local(StubRerankerServer(scores=[1.0])),
}
SLOW: dict[str, Callable[[], Adapter]] = {
    "fake": lambda: fake(delay_seconds=5.0),
    "local": lambda: local(StubRerankerServer(block=True)),
}


@pytest.mark.parametrize("name", WORKING)
async def test_one_score_per_passage_in_order_higher_for_the_answer(
    name: str,
) -> None:
    adapter = WORKING[name]()

    scores = await adapter.reranker.score(
        "annual leave days",
        ["VPN setup", "annual leave is 24 days", "leave a note"],
        request_id=REQUEST_ID,
        timeout_seconds=5.0,
    )

    assert len(scores) == 3
    assert all(type(score) is float for score in scores)
    assert scores[1] > scores[2] > scores[0]
    assert adapter.sent() == 1


@pytest.mark.parametrize("name", WORKING)
async def test_no_passages_score_nothing_and_send_nothing(name: str) -> None:
    adapter = WORKING[name]()

    scores = await adapter.reranker.score(
        "annual leave", [], request_id=REQUEST_ID, timeout_seconds=5.0
    )

    assert (scores, adapter.sent()) == ((), 0)


@pytest.mark.parametrize("name", WORKING)
@pytest.mark.parametrize(
    ("query", "passages", "reason", "index"),
    [
        (" ", ["a"], RerankRejection.EMPTY_QUERY, None),
        (PLANTED, ["a", " "], RerankRejection.EMPTY_PASSAGE, 1),
        (PLANTED, ["\ud800"], RerankRejection.UNENCODABLE_TEXT, 0),
        (PLANTED, ["a"] * 17, RerankRejection.TOO_MANY_PASSAGES, None),
    ],
    ids=["empty query", "empty passage", "unencodable", "too many"],
)
async def test_a_bad_request_is_refused_before_sending_and_names_no_text(
    name: str,
    query: str,
    passages: list[str],
    reason: RerankRejection,
    index: int | None,
) -> None:
    adapter = WORKING[name]()

    with pytest.raises(RerankInputRejected) as raised:
        await adapter.reranker.score(
            query, passages, request_id=REQUEST_ID, timeout_seconds=5.0
        )

    assert (raised.value.reason, raised.value.index) == (reason, index)
    assert PLANTED not in str(raised.value)
    assert adapter.sent() == 0


@pytest.mark.parametrize("name", SHORT_ANSWER)
async def test_a_wrong_number_of_scores_is_malformed(name: str) -> None:
    adapter = SHORT_ANSWER[name]()

    with pytest.raises(RerankerMalformedResponse):
        await adapter.reranker.score(
            "q", ["a", "b"], request_id=REQUEST_ID, timeout_seconds=5.0
        )


@pytest.mark.parametrize("name", SLOW)
async def test_the_timeout_is_honoured(name: str) -> None:
    adapter = SLOW[name]()

    with pytest.raises(RerankerTimeout) as raised:
        await adapter.reranker.score(
            PLANTED, [PLANTED], request_id=REQUEST_ID, timeout_seconds=0.05
        )

    assert PLANTED not in str(raised.value)
    assert raised.value.__context__ is None or PLANTED not in str(
        raised.value.__context__
    )
```

Create `tests/test_local_reranker.py`:

```python
"""The local reranker adapter: provenance, transport failures, and quiet logs."""

import gzip
import json
import logging
from typing import cast
from uuid import UUID

import httpx
import pytest
from pydantic import ValidationError

from knowledge_service.retrieval.local_reranker import (
    MAX_RESPONSE_BYTES,
    LocalReranker,
    LocalRerankerSettings,
)
from knowledge_service.retrieval.reranking import (
    InvalidRerankRequest,
    RerankerError,
    RerankerMalformedResponse,
    RerankerRequestRefused,
    RerankerTimeout,
    RerankerUnavailable,
    RerankInputRejected,
    RerankRejection,
)
from reranker_stubs import URL, StubRerankerServer, local_reranker

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000044")
PLANTED = "the salary of Olena is 9000"
PROVENANCE = (
    '"model": "BAAI/bge-reranker-v2-m3",'
    ' "revision": "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e", "device": "cpu"'
)
NO_SCORES = ("{" + PROVENANCE + "}").encode()
# Python's JSON parser reads NaN, so the adapter must refuse it itself.
NAN_SCORE = ("{" + PROVENANCE + ', "scores": [1.0, NaN]}').encode()
# 1 MB of zeros packs into about 1 KB; unpacked, it would pass the cap.
PACKED = gzip.compress(b"0" * (1024 * 1024))


async def score(server: StubRerankerServer) -> tuple[float, ...]:
    return await local_reranker(server).score(
        PLANTED, [PLANTED, "VPN setup"], request_id=REQUEST_ID, timeout_seconds=5.0
    )


async def test_the_request_carries_the_question_passages_and_request_id() -> None:
    server = StubRerankerServer()

    scores = await score(server)

    (request,) = server.requests
    assert request.url == httpx.URL(URL + "/v1/rerank")
    assert json.loads(request.content) == {
        "query": PLANTED,
        "passages": [PLANTED, "VPN setup"],
    }
    assert request.headers["X-Request-ID"] == str(REQUEST_ID)
    assert request.headers["Accept-Encoding"] == "identity"
    assert scores == (6.0, 0.0)


def test_the_identity_names_the_pinned_revision() -> None:
    reranker = local_reranker(StubRerankerServer())

    assert reranker.identity.provider == "local"
    assert reranker.identity.model == (
        "BAAI/bge-reranker-v2-m3@953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
    )


@pytest.mark.parametrize(
    "server",
    [
        StubRerankerServer(model="BAAI/bge-reranker-base"),
        StubRerankerServer(revision="main"),
    ],
    ids=["other model", "other revision"],
)
async def test_scores_from_another_model_are_refused(
    server: StubRerankerServer,
) -> None:
    with pytest.raises(RerankerRequestRefused):
        await score(server)


@pytest.mark.parametrize(
    ("server", "error"),
    [
        (StubRerankerServer(status=503), RerankerUnavailable),
        (StubRerankerServer(status=500), RerankerUnavailable),
        (StubRerankerServer(status=504), RerankerTimeout),
        (StubRerankerServer(status=413), RerankerRequestRefused),
        (StubRerankerServer(error=httpx.ConnectError("refused")), RerankerUnavailable),
        (StubRerankerServer(error=httpx.ReadTimeout("slow")), RerankerTimeout),
        (StubRerankerServer(body=b"not json"), RerankerMalformedResponse),
        (StubRerankerServer(body=b"[1.0]"), RerankerMalformedResponse),
        (StubRerankerServer(body=NAN_SCORE), RerankerMalformedResponse),
        (StubRerankerServer(scores=[1.0, True]), RerankerMalformedResponse),
        (StubRerankerServer(scores=[1.0, "2.0"]), RerankerMalformedResponse),
        (StubRerankerServer(body=NO_SCORES), RerankerMalformedResponse),
        (
            StubRerankerServer(body=b" " * (MAX_RESPONSE_BYTES + 1)),
            RerankerMalformedResponse,
        ),
        (
            StubRerankerServer(body=PACKED, headers={"Content-Encoding": "gzip"}),
            RerankerMalformedResponse,
        ),
    ],
    ids=[
        "503",
        "500",
        "504",
        "413",
        "connection refused",
        "read timeout",
        "not json",
        "not an object",
        "nan score",
        "bool score",
        "string score",
        "no scores",
        "too large",
        "compressed",
    ],
)
async def test_every_failure_is_typed_and_carries_no_text(
    server: StubRerankerServer,
    error: type[RerankerError],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    with pytest.raises(error) as raised:
        await score(server)

    assert PLANTED not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert PLANTED not in caplog.text


async def test_one_log_record_per_call_with_counts_and_device_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    await score(StubRerankerServer(device="mps"))

    (record,) = [r for r in caplog.records if r.name.endswith("local_reranker")]
    assert record.rerank == {  # type: ignore[attr-defined]
        "request_id": str(REQUEST_ID),
        "provider": "local",
        "model": "BAAI/bge-reranker-v2-m3@953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e",
        "passages": 2,
        "latency_ms": record.rerank["latency_ms"],  # type: ignore[attr-defined]
        "outcome": "succeeded",
        "device": "mps",
    }
    assert PLANTED not in caplog.text


async def test_verify_server_returns_the_device_and_sends_no_text() -> None:
    server = StubRerankerServer(device="mps")

    device = await local_reranker(server).verify_server(timeout_seconds=5.0)

    assert device == "mps"
    assert [request.method for request in server.requests] == ["GET"]


@pytest.mark.parametrize(
    "device",
    [None, "", "x" * 17, "gpu\u00e9", "cpu\n## |x|", "CPU", 3],
    ids=["none", "empty", "long", "accent", "markdown", "upper case", "int"],
)
async def test_a_server_that_names_no_plain_device_is_malformed(
    device: object,
) -> None:
    with pytest.raises(RerankerMalformedResponse):
        await local_reranker(StubRerankerServer(device=device)).verify_server(
            timeout_seconds=5.0
        )


async def test_a_pair_over_the_limit_is_refused_before_sending() -> None:
    server = StubRerankerServer()
    reranker = local_reranker(
        server, LocalRerankerSettings(url=URL, max_pair_tokens=20)
    )

    with pytest.raises(RerankInputRejected) as raised:
        await reranker.score(
            "q", ["x" * 20], request_id=REQUEST_ID, timeout_seconds=5.0
        )

    assert (raised.value.reason, raised.value.index) == (
        RerankRejection.PAIR_TOO_LONG,
        0,
    )
    assert server.sent == 0


def test_the_pair_bound_adds_both_texts_bounds() -> None:
    reranker = local_reranker(StubRerankerServer())

    # NFKD splits the ligature into three letters; each text adds three.
    assert reranker.count_pair_tokens("ab", "ﬃ") == (2 + 3) + (3 + 3)


@pytest.mark.parametrize("url", ["", "ftp://x", "not a url", "http://"])
def test_a_bad_url_fails_at_start_up(url: str) -> None:
    with pytest.raises(ValidationError):
        LocalRerankerSettings(url=url)


@pytest.mark.parametrize(
    "timeout_seconds",
    [float("nan"), 0.0, -1.0, "5", True],
    ids=["nan", "zero", "negative", "string", "bool"],
)
async def test_a_bad_timeout_is_a_typed_refusal(timeout_seconds: object) -> None:
    server = StubRerankerServer()

    with pytest.raises(InvalidRerankRequest):
        await local_reranker(server).score(
            "q",
            ["a"],
            request_id=REQUEST_ID,
            timeout_seconds=cast(float, timeout_seconds),
        )

    assert server.sent == 0


async def test_a_closed_client_is_unavailable_and_quiet() -> None:
    server = StubRerankerServer()
    client = httpx.AsyncClient(transport=httpx.MockTransport(server.handle))
    await client.aclose()
    reranker = LocalReranker(client, LocalRerankerSettings(url=URL))

    with pytest.raises(RerankerUnavailable) as raised:
        await reranker.score(
            PLANTED, [PLANTED], request_id=REQUEST_ID, timeout_seconds=5.0
        )

    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    ("name", "value"),
    [("max_passages", 65), ("max_pair_tokens", 8193), ("max_request_tokens", 16_385)],
)
def test_limits_above_the_server_s_fail_at_start_up(name: str, value: int) -> None:
    with pytest.raises(ValidationError):
        LocalRerankerSettings.model_validate({"url": URL, name: value})
```

Create `tests/test_reranking.py`:

```python
"""Reranking Candidates: a new order only, batches within limits, one deadline."""

import math
import time
from collections.abc import Sequence
from typing import cast
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fakes import FakeReranker
from knowledge_service.documents import ContentVersion
from knowledge_service.identifiers import ChunkId, DocumentId, SourceId
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.fusion import FusedCandidate
from knowledge_service.retrieval.reranking import (
    InvalidRerankRequest,
    RerankerIdentity,
    RerankerMalformedResponse,
    RerankerTimeout,
    RerankInputRejected,
    RerankLimits,
    RerankRejection,
    RerankResult,
    rerank_candidates,
)

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000055")
PLANTED = "the salary of Olena is 9000"


def candidate(rank: int, text: str, *, chunk: int | None = None) -> FusedCandidate:
    return FusedCandidate(
        rank=rank,
        chunk_id=ChunkId(UUID(int=chunk if chunk is not None else rank)),
        document_id=DocumentId(UUID(int=1000 + rank)),
        source_id=SourceId(UUID(int=7)),
        content_version=ContentVersion(1),
        ordinal=rank,
        score=1 / (60 + rank),
        contributions=(),
        external_id=f"doc-{rank}.md",
        title="Policy",
        heading_path=None,
        source_anchor=None,
        text=text,
    )


def ranked(*texts: str) -> list[FusedCandidate]:
    return [candidate(rank, text) for rank, text in enumerate(texts, start=1)]


async def rerank(
    reranker: FakeReranker,
    candidates: Sequence[Candidate],
    *,
    question: str = "annual leave days",
    timeout_seconds: float = 5.0,
) -> RerankResult:
    return await rerank_candidates(
        reranker,
        question,
        candidates,
        request_id=REQUEST_ID,
        timeout_seconds=timeout_seconds,
    )


async def test_the_best_scored_candidate_comes_first_and_keeps_its_old_rank() -> None:
    result = await rerank(
        FakeReranker(), ranked("VPN setup", "leave a note", "annual leave is 24 days")
    )

    assert [c.text for c in result.candidates] == [
        "annual leave is 24 days",
        "leave a note",
        "VPN setup",
    ]
    assert [c.rank for c in result.candidates] == [1, 2, 3]
    assert [c.retrieval_rank for c in result.candidates] == [3, 2, 1]
    assert [c.score for c in result.candidates] == [3.0, 1.0, 0.0]
    assert result.identity == RerankerIdentity(provider="fake", model="fake-reranker")
    assert result.requests == 1


async def test_equal_scores_keep_the_retrieval_order() -> None:
    result = await rerank(FakeReranker(), ranked("a", "b", "c"))

    assert [c.retrieval_rank for c in result.candidates] == [1, 2, 3]


async def test_no_candidates_send_nothing() -> None:
    reranker = FakeReranker()

    result = await rerank(reranker, [])

    assert (result.candidates, result.requests, reranker.calls) == ((), 0, [])


@settings(max_examples=60, deadline=None)
@given(
    words=st.lists(
        st.lists(
            st.sampled_from(["annual", "leave", "days", "vpn", "note"]), max_size=4
        ),
        max_size=40,
    ),
    max_passages=st.integers(min_value=1, max_value=8),
)
async def test_the_result_is_always_the_same_candidates_in_score_order(
    words: list[list[str]], max_passages: int
) -> None:
    texts = [" ".join(["x", *chosen]) for chosen in words]
    reranker = FakeReranker(
        limits=RerankLimits(
            max_passages=max_passages, max_pair_tokens=50, max_request_tokens=200
        )
    )

    result = await rerank(reranker, ranked(*texts))

    out = result.candidates
    assert sorted(c.retrieval_rank for c in out) == list(range(1, len(texts) + 1))
    assert len({c.chunk_id for c in out}) == len(texts)
    assert [c.rank for c in out] == list(range(1, len(texts) + 1))
    keys = [(-c.score, c.retrieval_rank) for c in out]
    assert keys == sorted(keys)
    # Every batch respects the limits, and together they keep the order.
    assert all(len(call) <= max_passages for call in reranker.calls)
    assert [text for call in reranker.calls for text in call] == texts


async def test_batches_stay_within_the_token_limit() -> None:
    # Each pair counts 3 question words + 2 passage words + 4 = 9 tokens.
    reranker = FakeReranker(
        limits=RerankLimits(max_passages=10, max_pair_tokens=20, max_request_tokens=20)
    )

    result = await rerank(reranker, ranked(*["leave days"] * 5))

    assert [len(call) for call in reranker.calls] == [2, 2, 1]
    assert result.requests == 3


async def test_a_pair_over_the_limit_is_refused_before_any_request() -> None:
    reranker = FakeReranker(
        limits=RerankLimits(max_passages=10, max_pair_tokens=10, max_request_tokens=20)
    )

    with pytest.raises(RerankInputRejected) as raised:
        await rerank(reranker, ranked("leave", " ".join([PLANTED] * 3)))

    assert (raised.value.reason, raised.value.index) == (
        RerankRejection.PAIR_TOO_LONG,
        1,
    )
    assert PLANTED not in str(raised.value)
    assert reranker.calls == []


async def test_one_deadline_covers_every_request() -> None:
    # Four requests of 0.3 s each cannot finish within 0.5 s.
    reranker = FakeReranker(
        limits=RerankLimits(max_passages=1, max_pair_tokens=50, max_request_tokens=50),
        delay_seconds=0.3,
    )

    with pytest.raises(RerankerTimeout):
        await rerank(reranker, ranked("a", "b", "c", "d"), timeout_seconds=0.5)

    # The second request was given only the time left, not a fresh 0.5 s.
    assert len(reranker.calls) == 2
    assert reranker.timeouts[0] == pytest.approx(0.5, abs=0.05)
    assert reranker.timeouts[1] < 0.25


async def test_a_wrong_number_of_scores_is_malformed() -> None:
    with pytest.raises(RerankerMalformedResponse):
        await rerank(FakeReranker(extra_scores=1), ranked("a", "b"))


@pytest.mark.parametrize(
    "timeout_seconds",
    [0, -1.0, math.nan, math.inf, True],
    ids=["zero", "negative", "nan", "infinite", "bool"],
)
async def test_a_bad_timeout_is_refused_before_any_request(
    timeout_seconds: float,
) -> None:
    reranker = FakeReranker()

    with pytest.raises(InvalidRerankRequest):
        await rerank(reranker, ranked("a"), timeout_seconds=timeout_seconds)

    assert reranker.calls == []


@pytest.mark.parametrize(
    "candidates",
    [
        [candidate(2, "a")],
        [candidate(1, "a", chunk=9), candidate(2, "b", chunk=9)],
        [None],
        ranked(*["a"] * 101),
        "not a list",
    ],
    ids=["wrong rank", "chunk twice", "not a candidate", "too many", "a string"],
)
async def test_bad_candidates_are_refused_before_any_request(
    candidates: object,
) -> None:
    reranker = FakeReranker()

    with pytest.raises(InvalidRerankRequest):
        await rerank(reranker, cast(Sequence[Candidate], candidates))

    assert reranker.calls == []


@pytest.mark.parametrize("question", ["", "   "], ids=["empty", "blank"])
async def test_an_empty_question_is_refused(question: str) -> None:
    with pytest.raises(InvalidRerankRequest):
        await rerank(FakeReranker(), ranked("a"), question=question)


async def test_the_text_is_kept_out_of_the_repr() -> None:
    result = await rerank(FakeReranker(), ranked(PLANTED), question=PLANTED)

    assert PLANTED not in repr(result)


class LoopBlockingReranker(FakeReranker):
    """Blocks the whole event loop while scoring, so no timeout can fire."""

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        # On purpose: this reranker misbehaves by blocking the loop.
        time.sleep(0.3)  # noqa: ASYNC251
        return await super().score(
            query, passages, request_id=request_id, timeout_seconds=timeout_seconds
        )


class SlowCountingReranker(FakeReranker):
    """Takes 0.1 s to count each pair's tokens."""

    def count_pair_tokens(self, query: str, passage: str) -> int:
        time.sleep(0.1)
        return super().count_pair_tokens(query, passage)


class ListChangingReranker(FakeReranker):
    """Changes the caller's list of Candidates while it scores them."""

    def __init__(self, given: list[FusedCandidate]) -> None:
        super().__init__()
        self._given = given

    async def score(
        self,
        query: str,
        passages: Sequence[str],
        *,
        request_id: UUID,
        timeout_seconds: float,
    ) -> tuple[float, ...]:
        self._given[0] = candidate(1, "intruder annual leave days", chunk=999)
        self._given.pop()
        return await super().score(
            query, passages, request_id=request_id, timeout_seconds=timeout_seconds
        )


class TextRaises:
    """A Candidate whose text cannot be read; the error repeats a secret."""

    rank = 1
    chunk_id = ChunkId(UUID(int=1))

    @property
    def text(self) -> str:
        raise ValueError(PLANTED)


async def test_a_late_answer_is_dropped_even_when_the_reranker_blocks_the_loop() -> (
    None
):
    with pytest.raises(RerankerTimeout):
        await rerank(LoopBlockingReranker(), ranked("a"), timeout_seconds=0.1)


async def test_slow_token_counting_counts_against_the_deadline() -> None:
    reranker = SlowCountingReranker()

    with pytest.raises(RerankerTimeout):
        await rerank(reranker, ranked("a", "b", "c"), timeout_seconds=0.2)

    assert reranker.calls == []


async def test_the_result_holds_the_candidates_given_even_if_the_list_changes() -> None:
    given = ranked("annual leave", "leave", "VPN setup")
    expected = [c.chunk_id for c in given]

    result = await rerank(ListChangingReranker(given), given)

    assert sorted(c.chunk_id.value for c in result.candidates) == sorted(
        chunk_id.value for chunk_id in expected
    )
    assert all("intruder" not in c.text for c in result.candidates)


async def test_a_candidate_that_cannot_be_read_is_refused_without_its_text() -> None:
    reranker = FakeReranker()

    with pytest.raises(InvalidRerankRequest) as raised:
        await rerank(reranker, cast(Sequence[Candidate], [TextRaises()]))

    assert PLANTED not in str(raised.value)
    assert raised.value.__context__ is None
    assert reranker.calls == []
```

Check:

```bash
uv run pytest tests/test_reranker_contract.py tests/test_local_reranker.py tests/test_reranking.py -q
```

Expected: `83 passed`.

### Step 7 — Coding step: compare retrieval before and after reranking

This step runs every seed case with fused retrieval, reranks it, and reports the change and the time.

**In plain words:** The comparison ingests and embeds the corpus as before, then for each question runs dense and word search fused, keeps that list, and sends exactly those Candidates to the reranker. It records where each relevant Chunk ranked before and after, and how long each step took. The report shows both runs side by side, then every case's move, then the time on the reranker's device last, because only that part changes from run to run. The script gets a `--rerank` option that checks both servers before anything is written.

| Change | What it does |
|---|---|
| `DatasetReport.reranker` | names the reranker in the report, and labels the run "reranked" |
| `RerankComparison` | both reports, the device, and the number of reranker requests |
| `compare_reranking` | ingest, embed, then per case: fused search, then `rerank_candidates` on its Candidates only |
| `_search_case`, `_outcome` | `_run_case` split in two, so the comparison can rerank between search and scoring; existing runs behave the same |
| `_rerank_case` | a reranker failure becomes `CaseFailed` naming the case and the error type only; so does a reranker that names another model mid-run |
| `render_rerank_comparison` | the side-by-side table, "Ranking delta", moves up and down, then "Latency" |
| `--rerank` | uses both local servers, checks them first, writes `bilingual-seed-rerank.md` |

In `src/knowledge_service/retrieval/evaluation_dataset.py`, make these changes in order:

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
  prototype; nothing in production uses it.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
```

Replace with:

```python
  prototype; nothing in production uses it.
- **Compared with reranking.** `compare_reranking` embeds every Chunk like
  `compare_branches`, runs every case with both branches fused, then
  reranks each case's fused Candidates with a Reranker (see
  `retrieval.reranking`). Only Candidates that passed the permission check
  reach the reranker. `render_rerank_comparison` reports the ranking delta
  per case and the reranking time on the reranker's device.
- **Scored per slice.** `score_slices` turns the ranks into Recall@k and
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
from collections.abc import Mapping
```

Replace with:

```python
from collections.abc import Mapping, Sequence
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
from knowledge_service.persistence.schema import embedding_spaces, sources
from knowledge_service.retrieval.fusion import DEFAULT_RRF_K, RetrievalBranch
```

Replace with:

```python
from knowledge_service.persistence.schema import embedding_spaces, sources
from knowledge_service.retrieval.candidates import Candidate
from knowledge_service.retrieval.fusion import DEFAULT_RRF_K, RetrievalBranch
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    reciprocal_rank,
)
```

Replace with:

```python
    reciprocal_rank,
)
from knowledge_service.retrieval.reranking import (
    Reranker,
    RerankerIdentity,
    RerankResult,
    rerank_candidates,
)
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
EMBEDDING_TIMEOUT_SECONDS = 60.0

```

Replace with:

```python
EMBEDDING_TIMEOUT_SECONDS = 60.0
RERANK_TIMEOUT_SECONDS = 60.0

```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    sparse: SparseModelIdentity | None = None

```

Replace with:

```python
    sparse: SparseModelIdentity | None = None
    # The reranker that reordered each case's Candidates; None when none did.
    reranker: RerankerIdentity | None = None

```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    index_seconds: float

```

Replace with:

```python
    index_seconds: float


@dataclass(frozen=True, slots=True)
class RerankComparison:
    """Fused retrieval before and after reranking, and where the scores ran.

    `retrieved` and `reranked` hold the same cases; a reranked outcome's
    `elapsed_seconds` is the reranking time alone. `device` is where the
    reranker scored, such as `cpu` or `mps`, and `requests` counts the
    requests sent to it over all cases.
    """

    retrieved: DatasetReport
    reranked: DatasetReport
    device: str
    requests: int

```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python

def score_slices(report: DatasetReport) -> tuple[SliceScore, ...]:
```

Replace with:

```python

async def compare_reranking(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    corpus: Path,
    *,
    observed_at: datetime,
    model: EmbeddingModel,
    reranker: Reranker,
    device: str,
    limit: int = DEFAULT_LIMIT,
) -> RerankComparison:
    """Ingest and embed the corpus once, run every case fused, then rerank it.

    Each case runs dense and lexical search fused, as `compare_branches`
    does, and sends that case's fused Candidates, and nothing else, to
    `reranker`. Raises like `compare_branches`; `DatasetRunRefused` also
    when `reranker` has no valid identity, and `CaseFailed` when reranking
    a case fails, naming the case and the error type only.
    """
    if type(limit) is not int or not 1 <= limit <= MAX_CANDIDATES:
        raise DatasetRunRefused(f"limit must be an int from 1 to {MAX_CANDIDATES}")
    identity = _identity(model)
    reranker_identity = _reranker_identity(reranker)
    await verify_corpus(dataset, corpus)
    await _ingest(runtime, dataset, corpus, observed_at, embedding=identity)
    await _embed_corpus(runtime, model, observed_at)
    searches: Mapping[RetrievalBranch, BranchSearch] = {
        RetrievalBranch.DENSE: dense_branch(runtime, model),
        RetrievalBranch.LEXICAL: lexical_branch(runtime),
    }
    retrieved: list[CaseOutcome] = []
    reranked: list[CaseOutcome] = []
    requests = 0
    for case in dataset.cases:
        result, elapsed = await _search_case(case, searches, limit)
        retrieved.append(_outcome(case, result.candidates, result.degraded, elapsed))
        started = time.perf_counter()
        ordered = await _rerank_case(case, reranker, result, reranker_identity)
        took = time.perf_counter() - started
        requests += ordered.requests
        reranked.append(_outcome(case, ordered.candidates, result.degraded, took))
    branches = tuple(branch for branch in RetrievalBranch if branch in searches)

    def report(
        outcomes: list[CaseOutcome], by: RerankerIdentity | None
    ) -> DatasetReport:
        return DatasetReport(
            name=dataset.name,
            version=dataset.version,
            limit=limit,
            branches=branches,
            outcomes=tuple(outcomes),
            embedding=identity,
            reranker=by,
        )

    return RerankComparison(
        retrieved=report(retrieved, None),
        reranked=report(reranked, reranker_identity),
        device=device,
        requests=requests,
    )


def score_slices(report: DatasetReport) -> tuple[SliceScore, ...]:
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python

def render_report(report: DatasetReport) -> str:
```

Replace with:

```python

def render_rerank_comparison(comparison: RerankComparison) -> str:
    """Return the side-by-side report, then each case's move, then timing.

    Everything above "## Latency" depends only on the data and the models,
    so two runs on one device can be compared with `diff`; the latency
    section changes from run to run.
    """
    before, after = comparison.retrieved, comparison.reranked
    moves = Counter[str]()
    lines = [
        "",
        "## Ranking delta",
        "",
        "Where each case's relevant Chunks ranked before and after reranking.",
        "",
        "| Case | Direction | Class | Before | After | Move |",
        "|---|---|---|---|---|---|",
    ]
    for old, new in zip(before.outcomes, after.outcomes, strict=True):
        change = reciprocal_rank(new.ranks) - reciprocal_rank(old.ranks)
        move = "up" if change > 0 else "down" if change < 0 else "same"
        moves[move] += 1
        lines.append(
            f"| {old.case_id} | {old.direction} | {old.query_class}"
            f" | {_ranks(old.ranks)} | {_ranks(new.ranks)} | {move} |"
        )
    lines += [
        "",
        f"- Moved up: {moves['up']}; moved down: {moves['down']};"
        f" unchanged: {moves['same']}.",
        "",
        "## Latency",
        "",
        "Wall time per case. It changes from run to run.",
        "",
        f"- Reranker device: {comparison.device}.",
        f"- Requests to the reranker: {comparison.requests}"
        f" for {len(after.outcomes)} cases.",
        "",
        "| Step | Median ms | Slowest ms |",
        "|---|---:|---:|",
    ]
    for name, report in (("retrieval", before), ("reranking", after)):
        times = [outcome.elapsed_seconds * 1000 for outcome in report.outcomes]
        median = statistics.median(times) if times else 0.0
        slowest = max(times, default=0.0)
        lines.append(f"| {name} | {median:.1f} | {slowest:.1f} |")
    return render_comparison((before, after)) + "\n".join(lines) + "\n"


def render_report(report: DatasetReport) -> str:
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
        ranks = ", ".join(
            "missed" if rank is None else str(rank) for rank in outcome.ranks
        )
        degraded = "yes" if outcome.degraded else "no"
        lines.append(
            f"| {outcome.case_id} | {outcome.direction} | {outcome.query_class}"
            f" | {ranks} | {degraded} |"
```

Replace with:

```python
        degraded = "yes" if outcome.degraded else "no"
        lines.append(
            f"| {outcome.case_id} | {outcome.direction} | {outcome.query_class}"
            f" | {_ranks(outcome.ranks)} | {degraded} |"
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
    return lines


def _label(report: DatasetReport) -> str:
    return " + ".join(report.branches)
```

Replace with:

```python
    reranker = next((r.reranker for r in reports if r.reranker is not None), None)
    if reranker is not None:
        lines.append(f"- Reranker: {reranker.provider} {reranker.model}.")
    return lines


def _label(report: DatasetReport) -> str:
    label = " + ".join(report.branches)
    return label if report.reranker is None else f"{label}, reranked"


def _ranks(ranks: tuple[int | None, ...]) -> str:
    return ", ".join("missed" if rank is None else str(rank) for rank in ranks)
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
        raise DatasetRunRefused("the model must have an EmbeddingModelIdentity")
    return identity
```

Replace with:

```python
        raise DatasetRunRefused("the model must have an EmbeddingModelIdentity")
    return identity


def _reranker_identity(reranker: Reranker) -> RerankerIdentity:
    # Read once here, so a reranker that cannot name itself is refused before
    # anything is written.
    try:
        identity = reranker.identity
    except Exception:
        identity = None
    if type(identity) is not RerankerIdentity:
        raise DatasetRunRefused("the reranker must have a RerankerIdentity")
    return identity
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
) -> CaseOutcome:
    result: HybridRetrieval | None = None
```

Replace with:

```python
) -> CaseOutcome:
    result, elapsed = await _search_case(case, searches, limit)
    return _outcome(case, result.candidates, result.degraded, elapsed)


async def _search_case(
    case: EvaluationCase,
    searches: Mapping[RetrievalBranch, BranchSearch],
    limit: int,
) -> tuple[HybridRetrieval, float]:
    """Run one case's search; return the result and its wall time in seconds."""
    result: HybridRetrieval | None = None
```

Find in `src/knowledge_service/retrieval/evaluation_dataset.py`:

```python
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
```

Replace with:

```python
    return result, elapsed


async def _rerank_case(
    case: EvaluationCase,
    reranker: Reranker,
    result: HybridRetrieval,
    identity: RerankerIdentity,
) -> RerankResult:
    """Rerank one case's fused Candidates, and only those, with one model."""
    reranked: RerankResult | None = None
    failure = "no result"
    try:
        reranked = await rerank_candidates(
            reranker,
            case.question,
            result.candidates,
            request_id=uuid5(_REQUEST_IDS, f"rerank {case.case_id}"),
            timeout_seconds=RERANK_TIMEOUT_SECONDS,
        )
    except Exception as error:
        # The reranker is outside code, and its message may quote a text.
        failure = _error_names(error)
    # Raised outside the `except`, so no linked error carries a text.
    if reranked is None:
        raise CaseFailed(f"case {case.case_id}: reranking raised {failure}")
    # The report names one reranker; scores of another must not mix in.
    if reranked.identity != identity:
        raise CaseFailed(f"case {case.case_id}: the reranker changed its identity")
    return reranked


def _outcome(
    case: EvaluationCase,
    candidates: Sequence[Candidate],
    degraded: bool,
    elapsed: float,
) -> CaseOutcome:
    """Record where each relevant Chunk ranked; refuse any restricted one."""
    ranks: dict[tuple[str, str | None], int] = {}
    for candidate in candidates:
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
        degraded=degraded,
```

Then, in `scripts/run_retrieval_dataset.py`:

Find in `scripts/run_retrieval_dataset.py`:

```python
`artifacts/evaluations/bilingual-seed-sparse.md`.
"""
```

Replace with:

```python
`artifacts/evaluations/bilingual-seed-sparse.md`.

`--rerank` runs every case with dense and lexical search fused, using the
local BGE-M3 server, then reranks each case's Candidates with the local
reranker server (`LOCAL_RERANKER_URL`, default `http://127.0.0.1:8767`),
which must already run the pinned revision; start it with
`uv run --project ml/bge_m3_server python ml/bge_m3_server/reranker_server.py`.
It writes the ranking delta and the latency on the reranker's device to
`artifacts/evaluations/bilingual-seed-rerank.md`.
"""
```

Find in `scripts/run_retrieval_dataset.py`:

```python
    compare_sparse,
    load_dataset,
    render_comparison,
    render_report,
    render_sparse_comparison,
    run_dataset,
    verify_corpus,
)
from knowledge_service.settings import Settings

REPORT = Path("artifacts/evaluations/bilingual-seed.md")
MANAGED_REPORT = Path("artifacts/evaluations/bilingual-seed-managed.md")
LOCAL_REPORT = Path("artifacts/evaluations/bilingual-seed-local.md")
SPARSE_REPORT = Path("artifacts/evaluations/bilingual-seed-sparse.md")
```

Replace with:

```python
    compare_reranking,
    compare_sparse,
    load_dataset,
    render_comparison,
    render_report,
    render_rerank_comparison,
    render_sparse_comparison,
    run_dataset,
    verify_corpus,
)
from knowledge_service.retrieval.local_reranker import (
    LocalReranker,
    LocalRerankerSettings,
)
from knowledge_service.retrieval.reranking import RerankerError
from knowledge_service.settings import Settings

REPORT = Path("artifacts/evaluations/bilingual-seed.md")
MANAGED_REPORT = Path("artifacts/evaluations/bilingual-seed-managed.md")
LOCAL_REPORT = Path("artifacts/evaluations/bilingual-seed-local.md")
SPARSE_REPORT = Path("artifacts/evaluations/bilingual-seed-sparse.md")
RERANK_REPORT = Path("artifacts/evaluations/bilingual-seed-rerank.md")
```

Find in `scripts/run_retrieval_dataset.py`:

```python
    embeddings.add_argument("--sparse", action="store_true")
    arguments = parser.parse_args()
```

Replace with:

```python
    embeddings.add_argument("--sparse", action="store_true")
    embeddings.add_argument("--rerank", action="store_true")
    arguments = parser.parse_args()
```

Find in `scripts/run_retrieval_dataset.py`:

```python
        else:
            rendered = asyncio.run(
                _run(runtime, dataset, corpus, arguments.limit, source)
            )
    except DatasetError as error:
        sys.stderr.write(f"dataset run failed: {error}\n")
        return 1
    except EmbeddingError as error:
        sys.stderr.write(f"the embedding model is not ready: {error!r}\n")
```

Replace with:

```python
        elif arguments.rerank:
            default = RERANK_REPORT
            rendered = asyncio.run(
                _run_rerank(runtime, dataset, corpus, arguments.limit)
            )
        else:
            rendered = asyncio.run(
                _run(runtime, dataset, corpus, arguments.limit, source)
            )
    except DatasetError as error:
        sys.stderr.write(f"dataset run failed: {error}\n")
        return 1
    except EmbeddingError as error:
        sys.stderr.write(f"the embedding model is not ready: {error!r}\n")
        return 1
    except RerankerError as error:
        sys.stderr.write(f"the reranker is not ready: {error!r}\n")
```

Find in `scripts/run_retrieval_dataset.py`:

```python

def _openai_model(api_key: SecretStr) -> ModelSource:
```

Replace with:

```python

async def _run_rerank(
    runtime: DatabaseRuntime, dataset: EvaluationDataset, corpus: Path, limit: int
) -> str:
    try:
        async with (
            _local_model() as model,
            httpx.AsyncClient(trust_env=False) as client,
        ):
            reranker = LocalReranker(client, LocalRerankerSettings())
            # Before anything is written, so a stopped server leaves it empty.
            device = await reranker.verify_server(timeout_seconds=SERVER_CHECK_SECONDS)
            comparison = await compare_reranking(
                runtime,
                dataset,
                corpus,
                observed_at=datetime.now(UTC),
                model=model,
                reranker=reranker,
                device=device,
                limit=limit,
            )
        return render_rerank_comparison(comparison)
    finally:
        await runtime.dispose()


def _openai_model(api_key: SecretStr) -> ModelSource:
```

Check: `make typecheck` reports `0 errors`, and `uv run python scripts/run_retrieval_dataset.py --rerank --sparse` prints `not allowed with argument`.

### Step 8 — Coding step: test the comparison on PostgreSQL

This step runs the comparison against a real database with the fakes.

**In plain words:** The first test runs all 16 cases with a fake embedding model and the fake reranker. It checks that each case was reranked once, that no text the fake reranker received holds the restricted marker, that reranking never finds a Chunk retrieval missed, and that neither the report nor the logs repeat a question or the marker. The second test makes the reranker fail and checks that the run stops at the first case with a typed error that names no text. The third gives a reranker that names one model at the start and another while scoring, and checks that the run refuses to mix their scores.

| Test | Proves |
|---|---|
| `test_every_case_is_reranked_and_only_eligible_text_reaches_the_reranker` | success and the highest-risk edge: restricted text never leaves the database for the reranker |
| `test_a_reranker_that_fails_stops_the_run_quietly` | typed failure: `CaseFailed` with the error type only, nothing linked |
| `test_a_reranker_that_changes_its_identity_is_refused` | the report never mixes scores of two models |

Create `tests/test_reranking_integration.py`:

```python
"""Reranking on real PostgreSQL: the seed run, what reaches the reranker, failure."""

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from fakes import FakeEmbeddingModel, FakeReranker
from knowledge_service.database import DatabaseRuntime, create_database_runtime
from knowledge_service.embeddings.port import EmbeddingLimits
from knowledge_service.retrieval.evaluation_dataset import (
    CASES_FILE,
    CORPUS_DIRECTORY,
    DATASET_DIRECTORY,
    CaseFailed,
    EvaluationDataset,
    compare_reranking,
    load_dataset,
    render_rerank_comparison,
)
from knowledge_service.retrieval.fusion import RetrievalBranch
from knowledge_service.retrieval.reranking import (
    RerankerIdentity,
    RerankerUnavailable,
)
from knowledge_service.settings import Settings

OBSERVED_AT = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
CORPUS = DATASET_DIRECTORY / CORPUS_DIRECTORY
RESTRICTED_MARKER = "CONFIDENTIAL-SEED-7731"
LIMITS = EmbeddingLimits(max_inputs=64, max_input_tokens=1_000, max_batch_tokens=20_000)


class IdentityChanges(FakeReranker):
    """Names one model when the run starts and another while it scores."""

    def __init__(self) -> None:
        super().__init__()
        self._reads = 0

    @property
    def identity(self) -> RerankerIdentity:
        self._reads += 1
        model = "fake-reranker" if self._reads == 1 else "another-reranker"
        return RerankerIdentity(provider="fake", model=model)


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


@pytest.mark.integration
async def test_every_case_is_reranked_and_only_eligible_text_reaches_the_reranker(
    runtime: DatabaseRuntime,
    dataset: EvaluationDataset,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    reranker = FakeReranker()

    comparison = await compare_reranking(
        runtime,
        dataset,
        CORPUS,
        observed_at=OBSERVED_AT,
        model=FakeEmbeddingModel(limits=LIMITS),
        reranker=reranker,
        device="cpu",
    )

    before, after = comparison.retrieved, comparison.reranked
    assert before.branches == (RetrievalBranch.DENSE, RetrievalBranch.LEXICAL)
    assert (before.reranker, after.reranker) == (None, reranker.identity)
    assert [o.case_id for o in after.outcomes] == [c.case_id for c in dataset.cases]
    # One request per case: ten Candidates fit the fake's limits at once.
    assert comparison.requests == len(dataset.cases) == len(reranker.calls)
    # The permission check ran before reranking: no restricted text ever
    # left the database for the reranker, however relevant it might be.
    sent = [text for call in reranker.calls for text in call]
    assert sent
    assert all(RESTRICTED_MARKER not in text for text in sent)
    # Reranking reorders; it never finds what retrieval missed.
    for old, new in zip(before.outcomes, after.outcomes, strict=True):
        assert [r is None for r in new.ranks] == [r is None for r in old.ranks]
    rendered = render_rerank_comparison(comparison)
    assert "## Ranking delta" in rendered
    assert "- Reranker device: cpu." in rendered
    assert rendered.index("## Ranking delta") < rendered.index("## Latency")
    for text in (rendered, caplog.text):
        assert RESTRICTED_MARKER not in text
        assert not any(case.question in text for case in dataset.cases)


@pytest.mark.integration
async def test_a_reranker_that_fails_stops_the_run_quietly(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    reranker = FakeReranker(error=RerankerUnavailable())

    with pytest.raises(CaseFailed) as raised:
        await compare_reranking(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            reranker=reranker,
            device="cpu",
        )

    first = dataset.cases[0]
    assert str(raised.value) == (
        f"case {first.case_id}: reranking raised RerankerUnavailable"
    )
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert len(reranker.calls) == 1


@pytest.mark.integration
async def test_a_reranker_that_changes_its_identity_is_refused(
    runtime: DatabaseRuntime, dataset: EvaluationDataset
) -> None:
    with pytest.raises(CaseFailed, match="changed its identity"):
        await compare_reranking(
            runtime,
            dataset,
            CORPUS,
            observed_at=OBSERVED_AT,
            model=FakeEmbeddingModel(limits=LIMITS),
            reranker=IdentityChanges(),
            device="cpu",
        )
```

Check:

```bash
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" uv run pytest -m integration tests/test_reranking_integration.py -q
```

Expected: `3 passed`.

### Step 9 — No coding in this step: run on CPU and GPU, break it, and decide

This step runs both real models, measures the reranker on two devices, breaks one guarantee on purpose, and asks you to decide.

**Action 1:** start both servers, each in its own terminal, and leave them running:

```bash
uv run --project ml/bge_m3_server python ml/bge_m3_server/bge_m3_server.py
```

```bash
RERANKER_DEVICE=cpu uv run --project ml/bge_m3_server python ml/bge_m3_server/reranker_server.py
```

If port 8765 is taken, start the BGE-M3 server with `BGE_M3_PORT=8766` in front, and in the third terminal run `export LOCAL_EMBEDDING_URL=http://127.0.0.1:8766` first.

**Action 2:** in a third terminal, run twice on fresh databases and compare everything above "## Latency":

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
export KNOWLEDGE_SERVICE_DATABASE_URL="$(uv run python scripts/test_database_url.py | sed 's#/knowledge_service_test$#/knowledge_service_dataset#')"
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --rerank
cp artifacts/evaluations/bilingual-seed-rerank.md /tmp/rerank-cpu.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --rerank > /dev/null
diff <(sed '/^## Latency/,$d' /tmp/rerank-cpu.md) <(sed '/^## Latency/,$d' artifacts/evaluations/bilingual-seed-rerank.md) && echo same
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
```

**Action 3:** stop the reranker with Ctrl+C, start it on the GPU, and run once more:

```bash
RERANKER_DEVICE=mps uv run --project ml/bge_m3_server python ml/bge_m3_server/reranker_server.py
```

```bash
docker exec knowledge-service-postgres createdb -U test_user knowledge_service_dataset
uv run alembic upgrade head
uv run python scripts/run_retrieval_dataset.py --rerank --output /tmp/rerank-mps.md > /dev/null
diff <(sed '/^## Latency/,$d' /tmp/rerank-cpu.md) <(sed '/^## Latency/,$d' /tmp/rerank-mps.md) && echo same
sed -n '/^## Latency/,$p' /tmp/rerank-mps.md
docker exec knowledge-service-postgres dropdb -U test_user knowledge_service_dataset
unset KNOWLEDGE_SERVICE_DATABASE_URL
```

If `mps` is not available on your machine, the server fails at start; record that and keep the CPU numbers. If the CPU and GPU reports differ above "## Latency", a score changed in the last digits and swapped two Candidates; record which case.

**Action 4:** read the reports and write the decision. In the evidence file, under "Decision", write:

- the summary table from the report (before and after, Recall@10 and MRR), because `artifacts/` is ignored by Git;
- the "Ranking delta" moves: which cases moved, up or down;
- the "Latency" sections of the CPU and GPU runs side by side;
- **adopt next** or **not yet**, in one sentence, with three short reasons: the ranking delta, the time per question on each device against retrieval's time, and what happens when the reranker server is down;
- at least one **falsified assumption** in the shape "I assumed X. The report shows Y (case IDs and numbers). So Z is wrong."

**Action 5:** break the shared deadline on purpose. In `src/knowledge_service/retrieval/reranking.py`, in `rerank_candidates`, change `timeout_seconds=remaining,` to `timeout_seconds=timeout_seconds,` and `async with asyncio.timeout(remaining):` to `async with asyncio.timeout(timeout_seconds):`. Then run:

```bash
uv run pytest tests/test_reranking.py -q
```

Expected: 1 failed, `test_one_deadline_covers_every_request`: every request got a fresh deadline, so the second request was given the whole time instead of what was left. Undo the change and run again until all pass. Stop both servers with Ctrl+C.

### Step 10 — No coding in this step: gates, docs, evidence, and progress

This step proves everything still works, updates the text the change made incomplete, and records the result.

**Action 1:** in `docs/architecture.md`:

Find in `docs/architecture.md`:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
own uv project (ADR 0009).
`ml/bge_m3_server/` serves BGE-M3 dense embeddings, and for the M05-T10
prototype its sparse term weights, on Python 3.12, on 127.0.0.1 only. The application reaches it through `LocalEmbeddingModel`,
which refuses any answer that does not name the pinned model revision.
```

Replace with:

```markdown
| `retrieval` | Retrieval | `search_similar_chunks`, `search_lexical_chunks` (routes each Question to English, `simple`, or both), `search_english_chunks`, `search_ukrainian_chunks`, `Candidate` (the fields every branch returns), `fuse_ranked_candidates` (Reciprocal Rank Fusion of branch Candidates), `retrieve_hybrid` (both branches at once under one deadline, fused, with per-branch diagnostics), `MetadataCondition`, `load_dataset` / `verify_corpus` / `run_dataset` / `compare_branches` (the bilingual seed Evaluation Dataset in `datasets/bilingual-seed/`, run lexical, dense, and fused), `recall_at` / `reciprocal_rank` (Recall@k and MRR), `compare_sparse` with `search_sparse_chunks` (M05-T10 prototype: BGE-M3 sparse weights against PostgreSQL word search; no default uses it), `Reranker` (`reranking`, the port for cross-encoder scores) with `rerank_candidates` (reorders eligible Candidates within the reranker's limits under one deadline) and `LocalReranker` (`local_reranker`, bge-reranker-v2-m3 over HTTP, pinned revision), `compare_reranking` (fused retrieval before and after reranking; no default reranks yet) |
| `persistence` | PostgreSQL implementation | `schema` and one module per kind of record |

Domain values, settings, the database runtime, and the HTTP application stay
at the top level.

Model runtimes that need their own Python live outside `src/`, each as its
own uv project (ADR 0009).
`ml/bge_m3_server/` serves BGE-M3 dense embeddings, and for the M05-T10
prototype its sparse term weights, on Python 3.12, on 127.0.0.1 only. The application reaches it through `LocalEmbeddingModel`,
which refuses any answer that does not name the pinned model revision.
`ml/bge_m3_server/reranker_server.py` serves the bge-reranker-v2-m3
cross-encoder from the same uv project, because it needs the same
libraries, as a separate process on 127.0.0.1 only. The application reaches
it through `LocalReranker`, which also refuses other revisions.
```

And in `Makefile`, so `make help` names both servers:

Find in `Makefile`:

```makefile
ml-check: ## Type-check and test the local BGE-M3 server (Python 3.12)
```

Replace with:

```makefile
ml-check: ## Type-check and test the local BGE-M3 and reranker servers (Python 3.12)
```

**Action 2:** run the gates in order:

```bash
uv run pytest tests/test_reranker_contract.py tests/test_local_reranker.py tests/test_reranking.py -q
make format-check
make lint
make typecheck
make test-unit
KNOWLEDGE_SERVICE_TEST_DATABASE_URL="$(uv run python scripts/test_database_url.py)" make test-integration
make ml-check
make docs-check
```

Expected: all pass. The reference run gave 83 passed for the narrow tests, `make test-unit` 1361 passed, `make test-integration` 306 passed, and `make ml-check` 51 passed.

**Action 3:** complete the evidence file:

- **Design:** the changed files; why this model and a separate process; why the code lives in `retrieval`; what a score means; how batching and the shared deadline work; why the reranker only ever sees eligible Candidates.
- **Verification:** the commands and counts, the repeatability result, the CPU and GPU runs, the deliberate break, and the known limits from "Before you begin".
- **Review:** an adversarial review of the prototype found two broken guarantees and six smaller problems. Both breaks and five of the smaller problems were fixed, each with a regression test that failed before the fix: the deadline started after token counting and a reranker that blocked the event loop still returned late scores, so the deadline now starts first and a late answer is dropped; the result re-read the caller's list after the reranker answered, so a list changed meanwhile could slip in a Candidate nobody checked, so every Candidate is now read once, at the start, and one whose fields cannot be read is refused without its text; a timeout that was not a positive number escaped as an untyped error, and a closed client as a `RuntimeError`; a server could name a device such as `cpu\n## x` that landed raw in the Markdown report, so only a plain name is accepted; limits above the server's were accepted and failed on every large request, so they now fail at start-up; and a reranker that changed its identity mid-run would have been reported under one name. The server's comment about its character limit was wrong and was corrected; counting outside the one-at-a-time lock stays a known limit, because the server listens on loopback only.
- **Reflection:** your answers to the questions below.

Then mark the task completed, add the M05-T11 row to `docs/curriculum/PROGRESS.md`, set the next task to M05-T12, and commit with `m05-t11: implement a local cross encoder reranker`.

## Reflection questions

1. What does `rerank_candidates` hide from the code that calls it? List what a caller never has to think about, such as the server's limits, how many requests are sent, or how the deadline is shared.
2. Someone later speeds reranking up by sending all requests at once instead of one after another. Which promise could that break on this server, which runs one request at a time, and what would the deadline do?
3. A teammate proposes dropping every Candidate with a score below zero, to show fewer, better results. What does a score of zero mean for this model, and why does the port refuse to give it a fixed meaning?
4. Which of your predictions was wrong, and what did you believe about rerankers or about this dataset that made you predict it?
5. Before reranking runs for real users, what would have to be true? Name one metric, one latency limit, and one failure case to test.

## Completion checklist

- [ ] The evidence file has the invariant and your predictions, written before running anything.
- [ ] The reranker server pins its revision and files, refuses instead of truncating, and returns one finite score per passage; `make ml-check` passes.
- [ ] `Reranker`, `check_rerank_inputs`, and `checked_scores` exist; `LocalReranker` refuses other revisions, bad scores, oversized or compressed answers, and logs no text.
- [ ] `rerank_candidates` returns exactly the Candidates it was given, in batches within the limits, under one deadline.
- [ ] Restricted text never reaches the reranker, and reranking never adds a Chunk.
- [ ] No planted text appears in errors, linked errors, logs, server answers, or reports.
- [ ] You ran `--rerank` twice on CPU on fresh databases, everything above "## Latency" was the same, and you ran it once on `mps`.
- [ ] The evidence file holds the summary table, the moves, CPU and GPU latency, a decision with reasons, and a falsified assumption.
- [ ] You ran the deliberate break from Step 9, saw one test fail, and undid it.
- [ ] All gates pass: format, lint, types, unit, integration, ML, docs.
- [ ] The architecture text, evidence, PROGRESS row, and commit are done.

When you have written the files, share them or any error output, and I will review them against this checklist.
